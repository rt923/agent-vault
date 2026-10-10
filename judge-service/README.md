# judge-service

Dify 多智能体拓扑中的**唯一外尺**。architect 通过 HTTP 调用本服务给 coder
的提交打分；coder 不直连 judge。

## 三条护栏

| 护栏 | 落点 | 说明 |
|---|---|---|
| 限次防泄值 | `rate_limit.py` | 每 team `submit_budget`，耗尽返回 429 且不评分；可选 per-coder 子上限 |
| 保留集隔离 | `store.py` + `main.score_prediction` | 保留集 example 不进 `/score`，与"未知 example"统一返回 404，避免集合归属泄露 |
| 提交审计 | `audit.py` | 检测"翻转单预测二分逆推真标签"：同 example 预测翻转 → warn；翻转 example 数 ≥ 阈值 → escalate |

## 端点（已对齐 team/dify/*.dsl.yaml 草稿契约）

| 方法 | 路径 | 权限 | 用途 | 对应草稿工具 |
|---|---|---|---|---|
| GET | `/health` | 公开 | 存活检查 | — |
| POST | `/submit` | coder（经 architect 转发） | 单条沙盒预测打分，扣 1 budget | coder `eval_submit_limited` |
| POST | `/score` | 同上 | `/submit` 的别名（向后兼容） | — |
| GET | `/budget/{team_id}` | 公开 | 查询剩余 budget | — |
| POST | `/audit` | architect (Bearer) | 审计报告（含翻转/超额标记） | architect `eval_audit` |
| POST | `/define-metric` | architect (Bearer) | 定义唯一外尺（ sole write-right） | architect `metric_define` |
| POST | `/aggregate` | architect (Bearer) | harness 汇聚 N coder 沙盒结果 | harness `validator` |
| POST | `/evaluate` | architect (Bearer) | 保留集最终评测，不扣 budget | architect 终评 |

鉴权：`Authorization: Bearer ${JUDGE_TOKEN}`（兼容旧 `X-Architect-Token` header）。

## 部署

```bash
cd team/dify/judge_service
pip install -r requirements.txt

# 环境变量（绝不提交真实值；用占位符或从 secrets 管理器注入）
export JUDGE_ARCHITECT_TOKEN="operator-context-present"   # 换成强随机串
export JUDGE_DEFAULT_SUBMIT_BUDGET=200
export JUDGE_HOLDOUT_FRACTION=0.3
export JUDGE_HOLDOUT_SEED=42
export JUDGE_LABELS_PATH="/path/to/labels.json"            # {"example_id": label, ...}，不入仓库
export JUDGE_TAUS_PATH="/path/to/taus.json"               # 可选：{"example_id": tau_float}，仅 pehe/ate_bias 因果指标需要

uvicorn team.dify.judge_service.main:app --host 0.0.0.0 --port 8787
```

## Dify 绑定（与草稿一致）

1. 在 Dify 中给 architect 应用加 **HTTP 工具**，Base URL = `http://<judge-host>:8787`，
   鉴权用 Bearer token（`${JUDGE_TOKEN}`）。
2. architect 启动时调 `POST /define-metric` 定义外尺（如 `accuracy`）。
3. coder 提交走 `POST /submit`，传 `team_id`/`coder_id`/`example_id`/`prediction`，
   解析返回的 `score` 与 `remaining_team_budget`。
4. harness 汇聚后调 `POST /aggregate` 得沙盒汇总分。
5. architect 定期调 `POST /audit` 查翻转/超额标记；终评调 `POST /evaluate`。
6. 预算耗尽（429）时 architect 走 reroute 协议切换 coder 或终止该方向。

## 冒烟验证

```bash
python team/dify/judge_service/smoke_test.py
```

应输出 `ALL SMOKE CHECKS PASSED`，验证保留集隔离、budget 耗尽、翻转检测
三条路径。

## 生产化清单

- [ ] `store.py` 的 `InMemoryStore` 换成 Redis/PG（实现 `Store` 协议即可）
- [ ] `JUDGE_ARCHITECT_TOKEN` 换成强随机串并通过 secrets 管理器注入
- [ ] `JUDGE_LABELS_PATH` 指向真实标签文件，该文件不入 git
- [ ] 反向代理（nginx/caddy）加 TLS 与访问日志
- [ ] `/audit` 的 escalate 标记接告警通道

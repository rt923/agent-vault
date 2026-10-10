***

title: helper-api OQ 决策卡（#52 工程轨道入口）

date: 2026-10-10

tags:

* helper-api
* "#52"
* decision-card
* oq

status: decided（2026-10-10，已经复核）

related:

* "\[\[helper-api-spec-brief]]"
* "dify-dsl/helper-api.openapi.yaml"

***

# helper-api OQ 决策卡（#52 工程轨道入口）

> \[!abstract] 用途
> 把 `helper-api.openapi.yaml`（v0.1，issue #52）里标注的【开放问题 OQ-1..4】拆成**可逐项拍板的决策**，供用户填写。
> 全部 OQ 拍板后，helper-api 才从「纸面契约」跨入工程球体：实现服务 → 部署 k3d → 真实 curl 打通 `/train`。
> 当前状态：契约已先行注册（Dify provider `helper-api` 已落），服务 0 行代码。

> \[!warning] 范围红线（来自 brief，重申）
> 本卡只固化**语义**，不产生代码。答完不等于"能用"——需另立工程轨道实现并部署服务，注册后 3 个工具（coder DSL 的 `train`/`infer`/`baselines`）才会在真实调用时成功。

## 决策总览

| **OQ** | **决策项**               | **当前值**                                                                 | **状态** | **阻塞的工程动作**               |
| ------ | --------------------- | ----------------------------------------------------------------------- | ------ | ------------------------- |
| OQ-1   | 训练什么模型                | A 主线（Shapley 跨店补偿）+ C 壳（config.task\_kind 声明式；v0.1 内置 intent\_clf 代理任务） | ✅      | 写 train 实现                |
| OQ-1   | 数据源（Dify dataset / 库） | Dify Dataset API；首版 dataset `task-1-sandbox`（BANKING77 8622 条）          | ✅      | 接数据层                      |
| OQ-1   | 触发方式（全量/增量）           | `full`（同步阻塞返回 run\_id）；incremental 留 enum 不实现                           | ✅      | 定调度                       |
| OQ-1   | 产出落点                  | pod 内 emptyDir `/models/{run_id}/`（joblib + metrics.json）               | ✅      | 定存储                       |
| OQ-2   | infer 输入              | `inputs: [{query_id, text}]` 纯文本列表                                      | ✅      | 写 infer 实现                |
| OQ-2   | infer 返回              | `{query_id, prediction:int, confidence:float}`；attribution 留 v0.2       | ✅      | 定返回 schema                |
| OQ-3   | 对比基线                  | majority-class / uniform-random / TF-IDF+LogReg                         | ✅      | 写 baselines 实现            |
| OQ-3   | 指标（AUC/NDCG/增益）       | macro\_f1（主，对齐 judge 外尺）+ accuracy（辅）；AUC/NDCG 不实现                      | ✅      | 定指标计算                     |
| OQ-3   | 返回结构                  | 保留 `{name, metric, value}` + 顶层加 `n_examples`                           | ✅      | 定返回 schema                |
| OQ-4   | 部署位置（容器/k3d/宿主）       | k3d 内 Deployment + ClusterIP Service（dify ns）；集群内 DNS 直达                | ✅      | 定部署形态                     |
| OQ-4   | 端口                    | 8799 定案（容器 8799 / NodePort 30799）；base url 用集群内 DNS                     | ✅      | 改 register\_helper 占位 URL |
| OQ-4   | 是否已有实现代码              | 否（0 行）                                                                  | ✅      | 起服务骨架                     |

> 模板约定：**当前值**填结论；**候选**为建议选项（标记"建议待确认"，非假设）；**影响**指该决策解锁的工程动作。

***

## OQ-1 train（训练什么 / 从哪来 / 怎么触发 / 落到哪）

spec 现有骨架：`task_id`(必填) / `dataset_id` / `mode∈{full,incremental}` / `config`(object)。

### 决策 1.1 — 训练什么模型

* **问题**：`helper_train` 到底训什么？对齐论文第5章"奥莱智推"与 Shapley/增量归因体系。
* **候选（建议待确认）**：
  * A. **跨店利益补偿 / 增量归因模型**（Shapley 值 → 各店利润分配），对应论文第4章 `chapter4_revised.md` 主线；
  * B. **推荐召回/排序模型**（uplift modeling），对应三维正相关性分析；
  * C. 通用训练编排（不限定模型，由 `config` 声明具体算法）。
* **当前值**：**A 为业务主线 + C 为实现壳**。`config.task_kind` 声明式编排：v0.1 内置 `intent_clf`（task-1 BANKING77 意图分类，数据现成、CPU 秒级、judge 外尺已对齐，用于打通 /train→/infer→/baselines 全链路）；v0.2 接入 `shapley_attribution`（第4章跨店利益补偿主线，待门店利润/增量转化数据资产到位——**当前本机无该业务数据，不臆造**）。A/B 的 uplift 语义随 v0.2 扩展，不推翻契约。

### 决策 1.2 — 数据源

* **问题**：训练数据从哪取？Dify 内某 dataset，还是本地/外部库？
* **候选**：Dify Dataset API（`/datasets`）/ 本地向量库（qdrant，已在 k3d）/ 外部业务表。
* **当前值**：**Dify Dataset API（`/datasets`）为唯一数据适配器**——数据以 Dify dataset 为单一事实源（与 kb-bridge sourceMap 同层，helper 无需直连向量库/业务表）。首版：task-1 沙盒语料（BANKING77 8622 条，labels.json/queries.json）导入为 dataset `task-1-sandbox`；qdrant 不作训练数据层（它是检索层）。数据拉取适配器只需 1 个（Dify console/dataset API）。
* **影响**：服务需实现数据拉取适配器（仅 Dify Dataset API 一个）。

### 决策 1.3 — 触发方式

* **问题**：全量还是增量？周期还是事件驱动？
* **候选**：`full`（重建）/ `incremental`（增量）/ 上游事件触发。
* **当前值**：**`full`（全量重建，同步阻塞——训练完成才返回 run\_id）**。依据：8622 条 CPU 训练秒级，无异步编排必要；`incremental` 保留 enum 不实现（v0.2，随业务数据量增长再启用）；事件驱动不做。幂等设计 = 同 task\_id 重复 full 直接重建覆盖，旧 run\_id 保留可查。
* **影响**：调度与幂等设计极简（无后台任务队列，FastAPI 同步 handler 即可）。

### 决策 1.4 — 产出落点

* **问题**：训练产物（模型/归因结果）存哪？
* **当前值**：**pod 内 emptyDir `/models/{run_id}/`**（目录含 model.joblib + metrics.json 元数据；run\_id = `{task_kind}-{yyyymmddHHMMSS}`）。依据：模型秒级可重训，pod 重启丢失可接受，不引 PVC/对象存储复杂度。候选里的 qdrant 是向量检索库不是模型存储，不作落点。
* **影响**：与 infer 的 `run_id` 引用链一致；无外部存储依赖，Deployment 挂 emptyDir 即可。

***

## OQ-2 infer（输入 / 返回）

spec 现有骨架：`run_id`(可选) / `inputs[]`(array, items:\{})。

### 决策 2.1 — infer 输入

* **问题**：`inputs` 每个元素是什么？user / item / context？
* **候选**：`{user_id, item_ids, context}` / 纯文本 / 特征向量。
* **当前值**：**纯文本列表**：`inputs: [{"query_id": str, "text": str}]`。依据：v0.1 内置 intent\_clf 的输入就是文本；`query_id` 与 judge /submit 的 example\_id 同名对齐，coder 拿 outputs 可零转换提交 judge。`{user_id, item_ids, context}` 结构留给 v0.2 推荐语义（task\_kind 扩展时加可选字段，不动已有字段）。
* **影响**：请求体 schema 固化（items 由 `{}` 收紧为 `{query_id, text}` 必填）。

### 决策 2.2 — infer 返回

* **问题**：返回什么？推荐列表 / 分数 / Shapley 归因？
* **候选**：推荐列表 + 分数 / Shapley 归因向量 / 两者皆有。
* **当前值**：**`outputs: [{"query_id", "prediction": int, "confidence": float}]`**——prediction 为整数类 id，与 judge /submit 契约（分类为整数类 id）严格一致，零转换。Shapley 归因向量（`attribution` 可选字段）留 v0.2：A 主线（`shapley_attribution` task\_kind）接入时启用，本版字段不出现。推荐列表（top-k items）属 B 语义，同留 v0.2。
* **影响**：响应 schema 固化（`outputs[]` items = `{query_id, prediction, confidence}`；attribution/topk 为 v0.2 预留字段名，v0.1 不返回）。

***

## OQ-3 baselines（基线 / 指标 / 返回结构）

spec 现有骨架：`task_id`(可选) / `metrics[]`(string)。

### 决策 3.1 — 对比基线

* **问题**：与哪些基线对比？
* **候选**：历史版本 / 随机 / 规则基线 / 其他模型。
* **当前值**：**3 条可离线复现基线**：① `majority-class`（众数类）② `uniform-random`（均匀随机）③ `tfidf-logreg`（TF-IDF+LogReg 默认参）。“历史版本”语义由 run\_id 机制天然承担（新旧 run 对比即历史版本对比），不单列。

### 决策 3.2 — 指标

* **问题**：用哪些指标？AUC / NDCG / 增益？
* **候选**：AUC、NDCG\@k、增益（uplift）、其他业务指标。
* **当前值**：**macro\_f1（主）+ accuracy（辅）**。**关键约束：必须与 judge 唯一外尺同尺**——judge `/define-metric` enum 就是 `[accuracy, macro_f1]`，baselines 若用 AUC/NDCG\@k/uplift 增益则与外尺不可比，破坏“唯一外尺”原则。AUC/NDCG/uplift 属 B（推荐排序）语义，v0.2 随 task\_kind 扩展时才引入，且届时仍以 judge 同尺指标为主。
* **影响**：指标计算模块只实现 macro\_f1+accuracy（sklearn 直出），AUC/NDCG 不写。

### 决策 3.3 — 返回结构

* **问题**：`baselines[]` 每项返回什么？spec 已留 `{name, metric, value}` 是否够？
* **候选**：保留 `{name, metric, value}` / 扩展加置信区间、样本量。
* **当前值**：**保留 `{name, metric, value}`，顶层扩展 `n_examples`**（评测样本量，判断分数可信度用）。置信区间不做（v0.2，需 bootstrap 重采样，首版 YAGNI）。
* **影响**：响应 schema。

***

## OQ-4 服务形态（部署 / 端口 / 已有代码）

### 决策 4.1 — 部署位置

* **问题**：服务跑在哪？
* **候选**：k3d 内独立 Deployment（推荐，与 Dify 同集群，走 `host.docker.internal` 可达）/ 独立容器 / 宿主进程。
* **当前值**：**k3d-fresh-env 内 Deployment + ClusterIP Service（namespace `dify`）**，与 Dify/kb-bridge 同集群同 ns。**纠偏一处候选描述**：服务在 k3d 内时，dify-api pod 调它走**集群内 DNS**（`http://helper-api.dify.svc.cluster.local:8799`），不走 `host.docker.internal`（那是宿主机服务如 judge 用的路径）；宿主机 curl 验证走 NodePort。
* **影响**：部署 YAML 与网络策略。k3d 内最契合现有 Dify/kb-bridge 形态。

### 决策 4.2 — 端口

* **问题**：监听哪个端口？
* **当前值**：**8799 定案**。三处对齐：容器监听 8799；Service ClusterIP 8799；NodePort **30799**（宿主 curl 验证用）。`register_helper.py` 注入的 base url 从占位 `http://host.docker.internal:8799` 改为 **`http://helper-api.dify.svc.cluster.local:8799`**（集群内 DNS，见 4.1 纠偏）后 DELETE+ADD 重注册。
* **影响**：定后改 `register_helper.py` 的 `PLACEHOLDER` 并重注册（DELETE+ADD）。

### 决策 4.3 — 是否已有实现代码

* **问题**：helper-api 服务是否已有代码？
* **当前值**：**否（0 行）**——需新起 FastAPI 骨架（对齐 judge\_service 的 11 文件形态，含 5 条冒烟断言）。
* **影响**：工程轨道起点。

***

## 工程轨道入口（全部 OQ 已定后）

1. 实现 helper-api 服务（FastAPI，3 路由 `/train` `/infer` `/baselines`），补 `config`/`inputs`/`outputs`/`metrics` 真实 schema。
2. 部署进 k3d（Deployment + Service，端口对齐 OQ-4.2）。
3. 改 `register_helper.py` 占位 URL → 重注册（DELETE+ADD）。
4. 跑通真实 `curl -XPOST .../train` → 返回 200 + `run_id`；`GET tool-providers` 确认 provider 健康。
5. 此时 #52 才从信息球体跨入工程球体交付。

***

%% 变更记录 %%

* 2026-10-10：初版决策卡，拆 OQ-1..4 为 11 项可拍板决策；状态全 ⬜。
* 2026-10-10：填写全部 12 项当前值（⬜→✅），status→decided 待复核。决策依据：judge 外尺同尺约束（macro\_f1/accuracy）、task-1 BANKING77 现成数据作 v0.1 代理任务、A（Shapley 第4章）为业务主线数据资产到位后 v0.2 接入、4.1/4.2 纠偏为集群内 DNS（host.docker.internal 仅适用宿主机服务）。

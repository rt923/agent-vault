# helper-api OpenAPI Spec — TRAE 起草简报（binding 相邻交付物）

## 背景
coder app（agent-chat, id `8b1cb0d5-4b4d-4413-b6e5-130615b4762d`）的 model-config 中有 3 个工具引用 `provider_id=helper-api`：
`train` / `infer` / `baselines`。该 provider **从未被创建/交付**（TRAE 工作区 + WorkBuddy + agent-vault 三层检索 0 命中，
`dify-binding-spec.yaml` 不存在）。为打破死锁，用户决策：由 TRAE 从零起草 `helper-api.openapi.yaml`，
后续注册为 Dify API 工具提供商并补丁 coder。

## 硬约束（必须）
- 交付文件名：`helper-api.openapi.yaml`（OpenAPI 3.0.x）。
- Dify 侧注册时 provider 名须为 **`helper-api`**（与 DSL `provider_id` 严格一致，否则工具无法解析）。
- 须含 `servers` 段；占位 base url 写成 `{{ env.HELPER_API_URL }}`，注册时由 WorkBuddy 替换为
  `http://host.docker.internal:<port>`（与 kb_bridge 同机制）。
- `security: []`（无鉴权，对齐 kb_bridge）；若需鉴权请显式说明。

## 必须包含的 3 个路径（operationId 见建议）
1. `POST /train`    — operationId 建议 `helper_train`
2. `POST /infer`    — operationId 建议 `helper_infer`
3. `POST /baselines`— operationId 建议 `helper_baselines`
（若实际为 GET 或不同路径，请注明，但**工具名 train/infer/baselines 须保留**以匹配 DSL。）

## 待你/用户定义的真实语义（不要臆造，标注 open question）
- **train**：训练什么模型？数据源（哪个 Dify dataset / 库）？触发方式（全量/增量）？产出落点？
- **infer**：对什么输入做推理（user/item/context？）？返回什么（推荐列表 / 分数 / Shapley 归因？）？
- **baselines**：与哪些基线对比？指标（AUC / NDCG / 增益）？返回结构？
- **落点与服务形态**：helper-api 服务跑在哪（独立容器 / k3d 内 / 宿主）？端口？是否已有实现代码？
  若无，本 spec 只是契约，**仍需新建并部署服务**才能使工具真正可用（见范围说明）。

## 范围说明（给用户）
- 写本 spec 是「绑定相邻的前置交付物」（类比 kb_bridge.openapi.yaml）。
- **仅 spec 不足以让 3 个工具可用**：Dify API 工具提供商指向 base url，若无运行中的 helper-api 服务，调用必失败。
- 使 helper-api 真正可用 = 新建并部署服务（train/infer/baselines 真实语义+落点），属新开发轨道，
  **超出「绑定」范围**。

## 交付与收口 SOP
1. TRAE 交付 `helper-api.openapi.yaml` 到 agent-vault（经 `trae-delivery` 分支，SOP 见 `_design/trae-delivery-sop.md`）。
2. WorkBuddy：fetch 后 `register-helper` 同款脚本注册 provider=`helper-api`。
3. `patch coder`：3 个 helper-api 工具已为 `provider_type=api, tool_parameters={}`，仅需 provider 注册即可解析。
4. 重验。

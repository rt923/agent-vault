---
title: 智能体产出通道索引
updated: 2026-07-20
tags:
  - index
  - agent-output
---

# 智能体产出索引

> [!info] 本索引自动维护
> 每次智能体上传产出文件后，索引自动更新。你也可以手动添加记录。

| 日期 | 智能体 | 类型 | 文件 | 状态 |
|------|--------|------|------|------|
| — | — | — | — | — |

## 快速入口

- [[_agent-output/archive/reports/|📁 报告归档]]
- [[_agent-output/archive/code/|📁 代码归档]]
- [[_agent-output/archive/images/|📁 图片归档]]
- [[_agent-output/archive/data/|📁 数据归档]]
- [[_agent-output/schemas/|📁 元数据模板]]

## 操作指南

```bash
# 上传文件
agent-upload -f ./output.md -t report -d "产出描述"

# 启动监听器（后台常驻）
agent-watch --vault /path/to/vault
```

## 相关笔记

- [[本地智能体产出通道设计]] — 完整设计方案
- [[智能体思考过程与产出物本地化存储方案]] — 思考链捕获方案
- [[_agent-sessions/_sessions-index]] — 会话索引
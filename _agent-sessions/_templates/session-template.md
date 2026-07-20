---
title: "Session: {{date}} {{title}}"
session_id: "{{session_id}}"
date: "{{date}}T{{time}}"
duration_minutes: 0
agent: "{{agent_name}}"
agent_version: ""
user_intent: "{{intent}}"
tags:
  - session
  - {{agent_type}}
status: completed
trace_steps: 0
output_count: 0
outputs: []
key_decisions: []
abandoned_approaches: []
---

# Session: {{title}}

> [!abstract] 会话概览
> - **智能体：** {{agent_name}}
> - **用户意图：** {{intent}}
> - **持续时间：** {{duration_minutes}} 分钟
> - **思考步数：** {{trace_steps}} 步
> - **产出物：** {{output_count}} 个

## 核心决策

> [!tip] 关键决策点
> 此处记录本次会话中做出的重要决策及其理由。

## 废弃方案

> [!bug] 已否决的方案
> 此处记录探索过但未采纳的方案，避免日后重复评估。

## 产出清单

- 暂无

## 思考链

完整推理过程请查看 [[trace]]。

---

%% 模板说明：创建新会话时，替换 {{}} 占位符即可。%%
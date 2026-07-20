---
title: "自指改進 diff #001: 新增 R9 規則"
draft_id: "self-modify-001"
date: 2026-07-14
status: ✅ 已採納
type: self_optimize
tags:
  - proposal
  - self_optimize
  - R9
sync_from: "drafts/self_modify_001.diff.md"
---

# self-modify-001: 新增 R9 自指產物須過閘門

> [!abstract] 概述
> PEG-A 首次對自身種子提示詞（`phase0_meta_peg_agent_prompt.md`）的自指改進。
> 在 §5 規則中新增 **R9**，要求任何自指改寫提案的 diff 新文本在採納前須再過一次 `explainability_check.py` 閘門。

## 提案資訊

| 欄位 | 值 |
|------|-----|
| 提案 ID | self-modify-001 |
| 日期 | 2026-07-14 |
| 類型 | self_optimize（階段 1） |
| 狀態 | ✅ 已採納（R9 現位於 phase0 §5） |
| 改動位置 | §5 規則與規範，R8 之後新增 R9 |
| 安全約束 | 未觸碰 §13（只讀禁區） |

## 改動內容

**新增規則 R9**：

> **R9 自指產物須過閘門**：任何對自身提示詞 / 配套產物的改寫提案（diff 的新文本），在採納前須再過一次 `explainability_check.py` 閘門，無 CRITICAL 方可採納；這把 §12/§13 的防護延伸到「自我進化」本身，避免自改無意削弱安全措辭。

## 驗證結果

| 檢查項 | 結果 |
|--------|------|
| `explainability_check.py` 對 R9 新文本 | ✅ 無 CRITICAL（通過） |
| `run_safety_regression.py` | ✅ 10/10（exit 0） |
| `guardrails_enforce.py check-s13` | ✅ §13 段落未變（exit 0） |

## 回滾方式

直接刪除新增的 R9 整行；或 `git revert`（若已入版本庫）。

## 完整內容

原始檔案：`meta_peg_agent/drafts/self_modify_001.diff.md`
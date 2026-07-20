---
title: 自指提案索引
updated: 2026-07-20
tags:
  - index
  - proposal
  - audit
---

# 自指提案索引

> [!info] 本索引由 `sync-audit.sh` 自動維護
> 記錄 PEG-A 對自身的改寫提案（drafts/）。包括孵化新智能體和自指改進。

| 提案 | 日期 | 類型 | 狀態 | 摘要 |
|------|------|------|------|------|
| [[_audit/_proposals/PEG-2026-07-13-001|PEG-2026-07-13-001]] | 2026-07-13 | incubate | ✅ 已產出 | 財報分析智能體種子提示詞 |
| [[_audit/_proposals/self-modify-001|self-modify-001]] | 2026-07-14 | self_optimize | ✅ 已採納 | 新增 R9：自指產物須過閘門 |

## 狀態說明

| 狀態 | 圖示 | 說明 |
|------|------|------|
| 已產出 | ✅ | 提案已產生並通過初步審查 |
| 已採納 | ✅ | 提案已執行並合入 |
| 進行中 | 🔄 | 提案正在執行或審查中 |
| 已拒絕 | ❌ | 提案被閘門或安全規則攔截 |
| 已擱置 | ⏸️ | 提案暫緩，待後續處理 |

## 操作

```bash
# 手動同步提案
bash _scripts/sync-audit.sh -s /path/to/meta_peg_agent --only-proposals
```
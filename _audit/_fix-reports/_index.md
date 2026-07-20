---
title: 修復報告索引
updated: 2026-07-20
tags:
  - index
  - fix-report
  - audit
---

# 修復報告索引

> [!info] 本索引由 `sync-audit.sh` 自動維護
> 記錄 `fix_reports/` 中的修復報告。按日期倒序排列。

| 編號 | 日期 | 標題 | 嚴重級別 | 狀態 |
|------|------|------|---------|------|
| [[_audit/_fix-reports/FIX-002-guardrails-readonly-windows|FIX-002]] | 2026-07-15 | guardrails_enforce.py Windows 只讀屬性檢測修復 | P1 | ✅ 已修復 |

## 操作

```bash
# 手動同步修復報告
bash _scripts/sync-audit.sh -s /path/to/meta_peg_agent --only-fixes
```
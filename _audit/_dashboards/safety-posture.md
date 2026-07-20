---
title: 安全態勢看板
updated: 2026-07-20
tags:
  - dashboard
  - safety-posture
  - audit
---

# 安全態勢看板

> [!info] 由 `sync-audit.sh` 自動更新
> Meta-PEG-Agent 當前安全態勢總覽。

## 當前狀態

| 指標 | 狀態 | 說明 |
|------|------|------|
| 🛡️ §13 只讀鎖 | ✅ 正常 | `guardrails_enforce.py` 保護正常 |
| 🚪 閘門系統 | ✅ 運行中 | `explainability_check.py` 可用 |
| 📋 安全回歸 | ✅ 10/10 | 最近一次回歸全部通過 |
| 📝 活躍提案 | 0 | 無進行中的自指提案 |
| 🔧 未解決修復 | 0 | 所有已知修復已完成 |

## 最近活動（24h）

| 時間 | 事件 | 結果 |
|------|------|------|
| — | 無近期活動 | — |

## 時間線

```mermaid
timeline
    title 安全事件時間線
    2026-07-13 : PEG-001 財報智能體草案產出
                : 通過閘門 + 回歸
    2026-07-14 : self-modify-001 R9 提案
                : 通過閘門 + 回歸
                : 閘門攔截 §13 篡改嘗試 x2
    2026-07-15 : FIX-002 Windows 只讀鎖修復
                : 3/3 PASS + 10/10 回歸
```

## 安全層完整性

```mermaid
graph LR
    subgraph 軟約束["軟約束（提示詞層）"]
        S12[§12 反注入宣告]
        S13[§13 三原則宣告]
    end
    subgraph 硬約束["硬約束（OS 層）"]
        GATE[explainability_check.py 閘門]
        GUARD[guardrails_enforce.py 只讀鎖]
        REGRESSION[run_safety_regression.py 回歸]
    end
    subgraph 審計["審計層（本 vault）"]
        AUDIT[閘門事件記錄]
        TRACE[提案追溯]
        TREND[趨勢分析]
    end

    S12 --> GATE
    S13 --> GATE
    S13 --> GUARD
    GATE --> REGRESSION
    GATE --> AUDIT
    REGRESSION --> TRACE
    AUDIT --> TREND
```

## 建議

> [!tip] 下一步建議
> - 增加閘門調用頻率，累積更多數據使趨勢分析更有意義
> - 考慮在 CI 流程中自動執行 `sync-audit.sh`，使審計追蹤與開發同步
> - 建議定期（每週）審查閘門拒絕趨勢，及時發現新的攻擊模式

## 相關筆記

- [[_audit/_dashboards/gate-trends|📊 閘門趨勢看板]]
- [[_audit/_gate-events/_index|📋 閘門事件索引]]
- [[_audit/_proposals/_index|📋 自指提案索引]]
- [[_audit/_fix-reports/_index|📋 修復報告索引]]
- [[審計追蹤層架構設計|📐 設計文件]]
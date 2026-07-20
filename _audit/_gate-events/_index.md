---
title: Gate Events Index
updated: 2026-07-20
tags:
  - index
  - gate-event
  - audit
---

# Gate Events Index

> [!info] Auto-maintained by sync-audit.ps1
> Records from explainability_check.py gate calls. Sorted by time descending.

| Time (UTC) | Verdict | Critical | Tags | Preview |
|-----------|---------|----------|------|---------|
| 2026-07-14 05:08:52 | 🔴 REJECT | 3 | s13_tamper, coercion_urgency | PEG-A self-modify proposal diff #002... |
| 2026-07-14 05:08:52 | 🟢 PASS | 0 | — | Standard gate call |
| 2026-07-14 05:08:52 | 🔴 REJECT | 2 | embedded_instruction | Injection probe |

## Sync Command

```powershell
.\_scripts\sync-audit.ps1 -Source "C:\path\to\meta_peg_agent"
```

## Related

- [[_audit/_dashboards/gate-trends|Gate Event Trends]]
- [[_audit/_dashboards/safety-posture|Safety Posture Dashboard]]
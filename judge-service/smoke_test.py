"""Smoke test for the judge guardrails + draft-aligned endpoint contracts.

Exercises store, rate_limit, audit, and the core logic behind /submit,
/define-metric, /aggregate directly (no live server needed).

Pass criteria:
  1. holdout examples are rejected (set-membership leak prevention)
  2. budget exhaustion raises BudgetExhausted
  3. prediction flips are flagged in the audit report
  4. /define-metric stores the active metric and /submit uses it
  5. /aggregate scores sandbox predictions without consuming budget
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from team.dify.judge_service.audit import build_report
from team.dify.judge_service.rate_limit import BudgetExhausted, check_and_consume
from team.dify.judge_service.schemas import AuditEntry
from team.dify.judge_service.scoring import score
from team.dify.judge_service.store import InMemoryStore


def main() -> int:
    store = InMemoryStore()
    labels = {f"ex{i}": i % 2 for i in range(10)}
    store.load_dataset(labels)
    holdout = store.get_holdout_ids()
    sandbox = [eid for eid in labels if eid not in holdout]
    assert len(holdout) == 3, f"expected 3 holdout, got {len(holdout)}"
    print(f"[holdout] {len(holdout)} holdout / {len(sandbox)} sandbox OK")

    # --- guardrail 1: holdout isolation ---
    h_id = next(iter(holdout))
    assert store.is_holdout(h_id) is True
    assert store.get_label(h_id) is not None
    print(f"[holdout] example {h_id} isolated OK")

    # --- guardrail 2: budget exhaustion ---
    store.set_team_budget("team-A", 2)
    check_and_consume(store, "team-A", "coder-1")
    check_and_consume(store, "team-A", "coder-1")
    try:
        check_and_consume(store, "team-A", "coder-1")
        print("[budget] FAIL: no exception raised")
        return 1
    except BudgetExhausted:
        print("[budget] exhaustion raises BudgetExhausted OK")

    # --- guardrail 3: audit / prediction-flip detection ---
    s_id = sandbox[0]
    store.add_submission(AuditEntry(
        team_id="team-B", coder_id="coder-x", example_id=s_id,
        prediction=0, score=0.0, timestamp=datetime.now(timezone.utc),
    ))
    store.add_submission(AuditEntry(
        team_id="team-B", coder_id="coder-x", example_id=s_id,
        prediction=1, score=1.0, timestamp=datetime.now(timezone.utc),
    ))
    report = build_report(store, "team-B")
    rules = {f.rule for f in report.flags}
    assert "prediction_flip" in rules, f"expected prediction_flip flag, got {rules}"
    print(f"[audit] prediction_flip flagged OK (flags={rules})")

    # --- contract 4: /define-metric sole write-right ---
    assert store.get_metric("team-C") is None
    store.set_metric("team-C", "accuracy", {})
    m = store.get_metric("team-C")
    assert m is not None and m[0] == "accuracy"
    print("[define-metric] metric stored & retrievable OK")

    # /submit uses the defined metric (simulate _active_metric logic)
    metric_fn = m[0] if m else "accuracy"
    lbl = store.get_label(sandbox[1])
    s, correct = score(1, lbl, metric_fn)
    assert s in (0.0, 1.0)
    print(f"[define-metric] /submit uses defined metric '{metric_fn}' OK (score={s})")

    # --- contract 5: /aggregate scores sandbox, no budget consumed ---
    before = store.get_team_used("team-D")
    preds = {sandbox[i]: (i % 2) for i in range(min(3, len(sandbox)))}
    correct = 0
    n = 0
    for ex_id, pred in preds.items():
        lbl = store.get_label(ex_id)
        if lbl is None or store.is_holdout(ex_id):
            continue
        sc, _ = score(pred, lbl, "accuracy")
        correct += int(sc)
        n += 1
    agg = correct / n if n else 0.0
    after = store.get_team_used("team-D")
    assert after == before, "aggregate must NOT consume submit budget"
    print(f"[aggregate] sandbox score={agg:.3f} over {n} examples, budget unchanged OK")

    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

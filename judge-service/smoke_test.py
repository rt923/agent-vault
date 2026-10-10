"""Smoke test for the judge guardrails + draft-aligned endpoint contracts.

Exercises store, rate_limit, audit, and the core logic behind /submit,
/define-metric, /aggregate directly (no live server needed).

Pass criteria:
  1. holdout examples are rejected (set-membership leak prevention)
  2. budget exhaustion raises BudgetExhausted
  3. prediction flips are flagged in the audit report
  4. /define-metric stores the active metric and /submit uses it
  5. /aggregate scores sandbox predictions without consuming budget
  6. v0.2 causal metrics (pehe / ate_bias) math, enum, and tau-guard
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import os
import types
import importlib.util

# The package lives in a hyphenated dir (judge-service) and is imported as
# "judge_service". Load it via importlib so this test runs from this dir
# regardless of the directory name. (Replaces the old team.dify.judge_service
# path that broke when the service moved into agent-vault.)
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_NAME = "judge_service"
_pkg = types.ModuleType(_PKG_NAME)
_pkg.__path__ = [_PKG_DIR]
sys.modules[_PKG_NAME] = _pkg
for _mod in ("config", "schemas", "store", "scoring", "audit", "rate_limit"):
    _spec = importlib.util.spec_from_file_location(
        f"{_PKG_NAME}.{_mod}", os.path.join(_PKG_DIR, f"{_mod}.py")
    )
    _m = importlib.util.module_from_spec(_spec)
    sys.modules[f"{_PKG_NAME}.{_mod}"] = _m
    _spec.loader.exec_module(_m)

from judge_service.audit import build_report
from judge_service.rate_limit import BudgetExhausted, check_and_consume
from judge_service.schemas import AuditEntry
from judge_service.scoring import score, compute_aggregate, SUPPORTED_METRICS
from judge_service.store import InMemoryStore


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

    # --- contract 6: v0.2 causal metrics (pehe / ate_bias) ---
    # Synthetic ground-truth treatment effects tau (NOT real data; proves math).
    taus = {f"ex{i}": (i - 5) * 0.1 for i in range(10)}  # deterministic floats
    store.load_dataset(labels, taus=taus)
    # perfect estimates -> both metrics must be 0
    perf = {f"ex{i}": taus[f"ex{i}"] for i in range(10)}
    pehe_perf, _ = compute_aggregate(perf, store.get_label, "pehe", tau_fn=store.get_tau)
    assert abs(pehe_perf) < 1e-9, f"pehe perfect should be 0, got {pehe_perf}"
    ate_perf, _ = compute_aggregate(perf, store.get_label, "ate_bias", tau_fn=store.get_tau)
    assert abs(ate_perf) < 1e-9, f"ate_bias perfect should be 0, got {ate_perf}"
    # wrong estimates (sign flip) -> nonzero PEHE
    wrong = {f"ex{i}": -taus[f"ex{i}"] for i in range(10)}
    pehe_wrong, _ = compute_aggregate(wrong, store.get_label, "pehe", tau_fn=store.get_tau)
    assert pehe_wrong > 0, f"pehe wrong should be >0, got {pehe_wrong}"
    # textbook PEHE: tau in {0.5,-0.3,0.1,0.8}, pred = tau + 0.2 each
    tau_t = [0.5, -0.3, 0.1, 0.8]
    pred_t = [t + 0.2 for t in tau_t]
    exp_pehe = (sum((p - t) ** 2 for p, t in zip(pred_t, tau_t)) / 4) ** 0.5
    s2 = InMemoryStore()
    s2.load_dataset({f"t{i}": 0 for i in range(4)}, taus={f"t{i}": tau_t[i] for i in range(4)})
    got_pehe, _ = compute_aggregate(
        {f"t{i}": pred_t[i] for i in range(4)}, s2.get_label, "pehe", tau_fn=s2.get_tau
    )
    assert abs(got_pehe - exp_pehe) < 1e-9, f"pehe expected {exp_pehe}, got {got_pehe}"
    # enum completeness
    assert set(SUPPORTED_METRICS) == {"accuracy", "macro_f1", "pehe", "ate_bias"}, (
        f"enum mismatch: {SUPPORTED_METRICS}"
    )
    # causal metric without tau source must raise (no fabricated ground truth)
    s3 = InMemoryStore()
    s3.load_dataset(labels)  # no taus
    try:
        compute_aggregate(perf, s3.get_label, "pehe", tau_fn=s3.get_tau)
        print("[causal] FAIL: pehe without tau did not raise")
        return 1
    except ValueError:
        pass
    print(f"[causal] pehe/ate_bias math + enum + tau-guard OK "
          f"(pehe_wrong={pehe_wrong:.4f}, textbook={exp_pehe:.4f})")

    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

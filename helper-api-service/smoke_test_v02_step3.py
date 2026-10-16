"""End-to-end smoke for v0.2 step 3: helper -> judge causal cross-check.

Wiring under test (two independent real ASGI stacks, one process):

  helper /train(uplift_attribution)  -> T-learner artifact + PEHE/ate_bias
  helper /infer(features)            -> tau_hat for every eval example
  judge  /define-metric(pehe|ate_bias) (architect Bearer)
  judge  /aggregate(predictions)     -> PEHE on the SANDBOX subset
  judge  /evaluate(predictions)      -> PEHE on the HOLDOUT subset

Cross-service consistency (指标同尺 + 子集对账):
  * judge /aggregate PEHE == helper PEHE recomputed over the judge SANDBOX
    subset (which is eval minus judge-holdout), to 1e-9.
  * judge /evaluate  PEHE == helper PEHE recomputed over the judge HOLDOUT
    subset, to 1e-9.
  * same two checks for ate_bias.
The judge's own 0.3 holdout guard is NOT disabled -- we slice helper's eval
set with the judge's split parameters and reconcile each channel separately.
The judge-holdout id set is derived independently (same shuffle/seed/fraction)
to prove the two services agree without sharing state.

SCOPE: smoke size 1000 users / 200 items (paper §4.11 = 8000 / 2000), so the
absolute PEHE is reported for the paper band only -- equality with 表4.2
(T-learner 1.9485, ATE偏差 0.0093) is NOT claimed.

Run:  python smoke_test_v02_step3.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import shutil
import sys
import tempfile
import types

import numpy as np

# --- env BEFORE importing any app (settings are read at import/startup) ---
_TMP = tempfile.mkdtemp(prefix="v02_step3_")
os.environ["HELPER_MODELS_DIR"] = os.path.join(_TMP, "models")

_HERE = os.path.dirname(os.path.abspath(__file__))
_JUDGE_DIR = os.path.join(os.path.dirname(_HERE), "judge-service")

# ---------------------------------------------------------------
# 1) helper first (flat imports; its top-level modules own `config` etc.)
# ---------------------------------------------------------------
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config as helper_config  # noqa: E402  (top-level `config` = helper's)
import main as helper_main  # noqa: E402
from synthetic_uplift import generate, split_train_eval  # noqa: E402

# ---------------------------------------------------------------
# 2) judge under an isolated `judge_service.*` namespace (dir has a hyphen)
# ---------------------------------------------------------------
TOKEN = "operator-context-present"  # matches judge config default placeholder
os.environ["JUDGE_ARCHITECT_TOKEN"] = TOKEN

_PKG = "judge_service"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [_JUDGE_DIR]
sys.modules[_PKG] = _pkg
for _mod in ("config", "schemas", "store", "scoring", "audit", "rate_limit", "main"):
    _spec = importlib.util.spec_from_file_location(
        f"{_PKG}.{_mod}", os.path.join(_JUDGE_DIR, f"{_mod}.py")
    )
    _m = importlib.util.module_from_spec(_spec)
    sys.modules[f"{_PKG}.{_mod}"] = _m
    _spec.loader.exec_module(_m)

from judge_service.main import app as judge_app  # noqa: E402
from judge_service.config import settings as judge_settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SEED = 20240914
# Default = smoke size; set STEP3_N_USERS / STEP3_N_ITEMS to run §4.11 scale
# (8000 / 2000) for a direct 表4.2 comparison.
N_USERS = int(os.getenv("STEP3_N_USERS", "1000"))
N_ITEMS = int(os.getenv("STEP3_N_ITEMS", "200"))
TEAM = "team-step3"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# 表4.2 reference (reported, not asserted): T-learner is the chosen model.
PAPER_PEHE_TLEARNER = 1.9485
PAPER_ATE_BIAS = 0.0093


def _pehe(tau_hat: np.ndarray, tau: np.ndarray) -> float:
    h, t = np.asarray(tau_hat, float), np.asarray(tau, float)
    return float(np.sqrt(((h - t) ** 2).mean()))


def _ate_bias(tau_hat: np.ndarray, tau: np.ndarray) -> float:
    h, t = np.asarray(tau_hat, float), np.asarray(tau, float)
    return float(abs(h.mean() - t.mean()))


def main() -> int:
    helper_client = TestClient(helper_main.app)
    assert helper_client.get("/health").json()["status"] == "ok"

    # ---- A) helper train + infer --------------------------------------
    print("=== A) helper /train -> /infer (uplift_attribution) ===")
    r = helper_client.post("/train", json={
        "mode": "full",
        "config": {"task_kind": "uplift_attribution", "seed": SEED,
                   "n_users": N_USERS, "n_items": N_ITEMS},
    })
    assert r.status_code == 200, r.text
    tr = r.json()
    run_id = tr["run_id"]
    assert tr["task_kind"] == "uplift_attribution" and tr["status"] == "completed"
    print(f"  /train run_id={run_id} PEHE={tr['metrics']['pehe']:.4f} "
          f"ate_bias={tr['metrics']['ate_bias']:.4f}")

    # reproduce helper's split to recover eval ground-truth tau (same seed /
    # fraction the service used internally)
    ds = generate(seed=SEED, n_users=N_USERS, n_items=N_ITEMS)
    _tr_ds, eval_ds = split_train_eval(
        ds, eval_fraction=helper_config.settings.eval_fraction, seed=SEED
    )
    n_eval = len(eval_ds.T)
    ex_ids = [f"ex{i}" for i in range(n_eval)]

    r = helper_client.post("/infer", json={
        "run_id": run_id,
        "inputs": [{"query_id": ex_ids[i], "features": eval_ds.X[i].tolist()}
                   for i in range(n_eval)],
    })
    assert r.status_code == 200, r.text
    outs = r.json()["outputs"]
    assert [o["query_id"] for o in outs] == ex_ids
    tau_hat = np.array([o["prediction"] for o in outs], dtype=np.float64)
    assert all(o["confidence"] is None for o in outs)  # regression: no confidence

    # helper internal consistency: /train metrics == rescored /infer tau_hat
    assert abs(_pehe(tau_hat, eval_ds.tau_true) - tr["metrics"]["pehe"]) < 1e-12
    assert abs(_ate_bias(tau_hat, eval_ds.tau_true) - tr["metrics"]["ate_bias"]) < 1e-12
    print(f"  /infer tau_hat[:3]={tau_hat[:3].round(4).tolist()}  (n={n_eval})")
    print("  A: PASS (infer tau_hat reproduces /train metrics)")

    # ---- B) judge causal scoring --------------------------------------
    print("=== B) judge /define-metric -> /aggregate + /evaluate ===")
    # judge needs a label *key set* to derive its sandbox/holdout split; the
    # causal branch ignores label values (it consumes tau_fn), so values are
    # dummy. taus carry the real ground-truth tau per example.
    labels = {ex_ids[i]: 0 for i in range(n_eval)}
    taus = {ex_ids[i]: float(eval_ds.tau_true[i]) for i in range(n_eval)}
    labels_path = os.path.join(_TMP, "labels.json")
    taus_path = os.path.join(_TMP, "taus.json")
    with open(labels_path, "w", encoding="utf-8") as f:
        json.dump(labels, f)
    with open(taus_path, "w", encoding="utf-8") as f:
        json.dump(taus, f)
    os.environ["JUDGE_LABELS_PATH"] = labels_path
    os.environ["JUDGE_TAUS_PATH"] = taus_path

    preds = {ex_ids[i]: float(tau_hat[i]) for i in range(n_eval)}

    with TestClient(judge_app) as jc:
        assert jc.get("/health").json()["status"] == "ok"

        # derive the judge holdout/sandbox split independently to slice helper
        holdout_ids = _derive_holdout(list(labels.keys()), judge_settings)
        sandbox_idx = [i for i in range(n_eval) if ex_ids[i] not in holdout_ids]
        holdout_idx = [i for i in range(n_eval) if ex_ids[i] in holdout_ids]

        results = {}
        for metric in ("pehe", "ate_bias"):
            dm = jc.post("/define-metric", headers=AUTH,
                         json={"team_id": TEAM, "metric_name": metric, "params": {}}).json()
            assert dm.get("active") is True and dm.get("metric_name") == metric, dm
            agg = jc.post("/aggregate", headers=AUTH,
                          json={"team_id": TEAM, "predictions": preds}).json()
            ev = jc.post("/evaluate", headers=AUTH,
                         json={"team_id": TEAM, "predictions": preds}).json()
            results[metric] = (agg, ev)
            print(f"  [{metric}] judge /aggregate={agg['aggregate_score']:.6f} "
                  f"(n={agg['n_examples']})  "
                  f"/evaluate={ev['holdout_score']:.6f} (n={ev['n_examples']})")

    # ---- C) cross-service subset reconciliation -----------------------
    print("=== C) cross-service reconciliation (sandbox vs holdout) ===")
    print(f"  judge split: sandbox={len(sandbox_idx)} holdout={len(holdout_idx)} "
          f"(fraction={judge_settings.holdout_fraction}, seed={judge_settings.holdout_seed})")

    agg_p, ev_p = results["pehe"]
    agg_a, ev_a = results["ate_bias"]

    # judge /aggregate scores ONLY the sandbox subset
    assert agg_p["n_examples"] == len(sandbox_idx), agg_p
    assert agg_a["n_examples"] == len(sandbox_idx), agg_a
    # judge /evaluate scores ONLY the holdout subset, nothing missing
    assert ev_p["n_examples"] == len(holdout_idx) and ev_p["missing"] == [], ev_p

    helper_pehe_sb = _pehe(tau_hat[sandbox_idx], eval_ds.tau_true[sandbox_idx])
    helper_pehe_ho = _pehe(tau_hat[holdout_idx], eval_ds.tau_true[holdout_idx])
    helper_ate_sb = _ate_bias(tau_hat[sandbox_idx], eval_ds.tau_true[sandbox_idx])
    helper_ate_ho = _ate_bias(tau_hat[holdout_idx], eval_ds.tau_true[holdout_idx])

    checks = [
        ("pehe/sandbox",  agg_p["aggregate_score"], helper_pehe_sb),
        ("pehe/holdout",  ev_p["holdout_score"],    helper_pehe_ho),
        ("ate/sandbox",   agg_a["aggregate_score"], helper_ate_sb),
        ("ate/holdout",   ev_a["holdout_score"],    helper_ate_ho),
    ]
    for name, judge_val, helper_val in checks:
        diff = abs(judge_val - helper_val)
        print(f"  {name:14s} judge={judge_val:.9f} helper={helper_val:.9f} "
              f"|diff|={diff:.2e}")
        assert diff < 1e-9, f"{name}: judge vs helper mismatch {diff}"
    print("  C: PASS (judge metrics == helper metrics on identical subsets, 1e-9)")

    # ---- D) independent negative control ------------------------------
    # a nontrivial perturbation must move BOTH services identically (guards
    # against a trivially-zero PEHE wiring).
    print("=== D) negative control (perturbed tau_hat) ===")
    noisy = tau_hat + 1.0
    noisy_preds = {ex_ids[i]: float(noisy[i]) for i in range(n_eval)}
    with TestClient(judge_app) as jc2:
        jc2.post("/define-metric", headers=AUTH,
                 json={"team_id": TEAM, "metric_name": "pehe", "params": {}})
        agg_n = jc2.post("/aggregate", headers=AUTH,
                         json={"team_id": TEAM, "predictions": noisy_preds}).json()
    helper_pehe_noisy_sb = _pehe(noisy[sandbox_idx], eval_ds.tau_true[sandbox_idx])
    print(f"  judge noisy sandbox PEHE={agg_n['aggregate_score']:.6f} "
          f"helper={helper_pehe_noisy_sb:.6f}")
    assert abs(agg_n["aggregate_score"] - helper_pehe_noisy_sb) < 1e-9
    assert agg_n["aggregate_score"] > helper_pehe_sb  # perturbation worsened fit
    print("  D: PASS")

    # ---- E) report ----------------------------------------------------
    full_pehe = _pehe(tau_hat, eval_ds.tau_true)
    full_ate = _ate_bias(tau_hat, eval_ds.tau_true)
    print("=" * 62)
    print("STEP-3 END-TO-END SMOKE PASSED")
    print(f"  full-eval PEHE     = {full_pehe:.4f}  "
          f"(paper 表4.2 T-learner = {PAPER_PEHE_TLEARNER}, same order only)")
    print(f"  full-eval ate_bias = {full_ate:.4f}  (paper ATE偏差 = {PAPER_ATE_BIAS})")
    print(f"  smoke scale: {N_USERS} users / {N_ITEMS} items "
          f"(paper §4.11 = 8000 / 2000) -- absolute equality NOT claimed")
    print("=" * 62)
    return 0


def _derive_holdout(all_ids: list[str], settings) -> set[str]:
    """Independently reproduce judge Store.load_dataset's holdout split.

    Same list order -> same random.Random(seed).shuffle -> same fraction.
    """
    ids = list(all_ids)
    rng = random.Random(settings.holdout_seed)
    rng.shuffle(ids)
    n_holdout = max(1, int(len(ids) * settings.holdout_fraction))
    return set(ids[:n_holdout])


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    raise SystemExit(code)

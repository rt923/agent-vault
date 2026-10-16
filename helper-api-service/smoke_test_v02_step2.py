"""Smoke test for helper-api v0.2 step 2 (uplift_attribution + dual-level Shapley).

Parts:
  A. data gen (small) + T-learner train + judge-aligned metrics sanity
  B. feature exact Shapley: efficiency axiom ~ machine epsilon;
     implementation falsifier -- exact machinery on the TRUE tau function
     must reproduce the closed form phi_k*E[x_k] to ~1e-9
  C. feature monte_carlo: per-player sampling variance present, telescoping ok
  D. exact hard cap: n=21 -> ValueError (paper L303, no auto-switch)
  E. store-level exact (表4.10 alliance value fn): residual ~ eps; MC variant
  F. HTTP API: /train uplift -> /infer features -> /shapley (both levels,
     both algorithms); error paths; intent_clf back-compat
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

# env BEFORE importing helper modules
_TMP = tempfile.mkdtemp(prefix="helper_v02_smoke_")
os.environ["HELPER_MODELS_DIR"] = _TMP

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import numpy as np  # noqa: E402

from synthetic_uplift import (  # noqa: E402
    ATE_TRUE,
    FEATURE_NAMES,
    N_STORES,
    STORE_NAMES,
    STORE_TAU_MEANS,
    TRUE_PHI,
    generate,
    split_train_eval,
)
from uplift import train_uplift, uplift_metrics  # noqa: E402
from shapley_attr import (  # noqa: E402
    feature_shapley_exact,
    feature_shapley_monte_carlo,
    shapley_exact_by_mask,
    store_shapley_exact,
    store_shapley_monte_carlo,
)

SEED = 20240914
N_USERS, N_ITEMS = 1000, 200  # smoke size (paper scale = 8000/2000)

# ============================================================
# Part A: data + T-learner
# ============================================================
print("=== Part A: data gen + T-learner ===")
ds = generate(seed=SEED, n_users=N_USERS, n_items=N_ITEMS)
train_ds, eval_ds = split_train_eval(ds, eval_fraction=0.2, seed=SEED)
print(f"  n_train={len(train_ds.T)}, n_eval={len(eval_ds.T)}")

model = train_uplift(train_ds, seed=SEED)
tau_hat = model.predict_tau(eval_ds.X)
metrics = uplift_metrics(tau_hat, eval_ds.tau_true)
print(f"  PEHE     = {metrics['pehe']:.4f}  (paper 表4.2 T-learner: 1.9485, same order)")
print(f"  ate_bias = {metrics['ate_bias']:.4f}")
assert metrics["pehe"] < 5.0, f"PEHE out of sanity band: {metrics['pehe']}"
assert metrics["ate_bias"] < 1.0, f"ate_bias out of sanity band: {metrics['ate_bias']}"
print("  A: PASS")

# ============================================================
# Part B: feature-level exact Shapley
# ============================================================
print("=== Part B: feature exact Shapley ===")

# B.1 implementation falsifier: exact machinery on TRUE tau must reproduce
# the closed form phi_k * E[x_k] (baseline=0, additive truth). 门店等级 is
# FIXED at observed (conditional attribution) -> its closed-form φ is exactly 0.
FIXED_IDX = FEATURE_NAMES.index("门店等级")
store_base = eval_ds.tau_true - eval_ds.X @ TRUE_PHI


def true_tau_fn(M: np.ndarray) -> np.ndarray:
    return M @ TRUE_PHI + store_base


res_true = feature_shapley_exact(true_tau_fn, eval_ds.X, fixed_features=[FIXED_IDX])
closed_form = TRUE_PHI * eval_ds.X.mean(axis=0)
closed_form[FIXED_IDX] = 0.0  # fixed feature: φ exactly 0 under conditional attribution
err_impl = float(np.max(np.abs(np.asarray(res_true["phi_means"]) - closed_form)))
print(f"  B.1 truth-side closed-form match: max|diff| = {err_impl:.3e}")
assert err_impl < 1e-9, f"exact machinery does not match closed form: {err_impl}"
assert res_true["efficiency_max_residual"] < 1e-8
print("  B.1 PASS (exact implementation falsified against closed form: OK)")

# B.2 model-side exact (门店等级 fixed: store intercept cannot leak into it)
res_model = feature_shapley_exact(model.predict_tau, eval_ds.X, fixed_features=[FIXED_IDX])
print(f"  B.2 model efficiency residual = {res_model['efficiency_max_residual']:.3e}")
assert res_model["efficiency_max_residual"] < 1e-8

phi_means = np.asarray(res_model["phi_means"])
assert abs(phi_means[FIXED_IDX]) < 1e-12, \
    "fixed feature (门店等级) phi must be exactly 0"
true_same = TRUE_PHI * eval_ds.X.mean(axis=0)
true_same[FIXED_IDX] = 0.0  # fixed feature: φ exactly 0 (conditional attribution)
errors = phi_means - true_same
mae = float(np.abs(errors).mean())
print(f"  B.2 same-scale mean abs attribution error = {mae:.4f} (paper 表4.3: 0.1004)")
for i, name in enumerate(FEATURE_NAMES):
    print(f"    {name:8s} model={phi_means[i]:+.4f}  true*Ex={true_same[i]:+.4f}  "
          f"err={errors[i]:+.4f}  (structural phi={TRUE_PHI[i]:+.4f})")
# zero-phi features should have small model contributions relative to drivers
zero_idx = list(range(5, 10))
assert np.abs(phi_means[zero_idx]).max() < np.abs(phi_means[:5]).max(), \
    "zero-phi feature contribution exceeds max driver contribution"
print("  B: PASS")

# ============================================================
# Part C: feature monte_carlo
# ============================================================
print("=== Part C: feature monte_carlo ===")
res_mc = feature_shapley_monte_carlo(
    model.predict_tau, eval_ds.X, n_permutations=30, seed=SEED,
    fixed_features=[FIXED_IDX],
)
var = np.asarray(res_mc["sampling_variance"])
assert var.shape == (10,) and np.all(var >= 0)
print(f"  MC efficiency residual = {res_mc['efficiency_max_residual']:.3e}")
assert res_mc["efficiency_max_residual"] < 0.5
# MC means should be in the same ballpark as exact means
diff_mc = float(np.max(np.abs(np.asarray(res_mc["phi_means"]) - phi_means)))
print(f"  max|MC - exact| = {diff_mc:.4f} (30 permutations, sampling noise)")
assert diff_mc < 1.0
print("  C: PASS")

# ============================================================
# Part D: exact hard cap n>20
# ============================================================
print("=== Part D: exact cap n>20 ===")
try:
    shapley_exact_by_mask(21, np.zeros(1 << 21))
    raise AssertionError("n=21 exact should have raised ValueError")
except ValueError as exc:
    print(f"  ValueError raised as required: {exc}")
print("  D: PASS")

# ============================================================
# Part E: store-level Shapley (表4.10 alliance value function)
# ============================================================
print("=== Part E: store-level Shapley ===")
res_store = store_shapley_exact(tau_hat, eval_ds.home_store, eval_ds.item_store, N_STORES)
phi_store = np.asarray(res_store["phi"])
print(f"  store phi = {phi_store.round(4).tolist()}")
print(f"  Σφ = {phi_store.sum():.6f}, v(N) = {res_store['v_n']:.6f}, "
      f"residual = {res_store['efficiency_max_residual']:.3e}")
assert res_store["efficiency_max_residual"] < 1e-8
# flagship should dominate (highest tau mean + flow hub), discount store smallest
assert int(np.argmax(phi_store)) == 0, "flagship should have the largest store phi"
assert int(np.argmin(phi_store)) == 5, "discount store should have the smallest store phi"

res_store_mc = store_shapley_monte_carlo(
    tau_hat, eval_ds.home_store, eval_ds.item_store, N_STORES,
    n_permutations=50, seed=SEED,
)
print(f"  store MC residual = {res_store_mc['efficiency_max_residual']:.3e}, "
      f"variance head = {np.asarray(res_store_mc['sampling_variance']).round(6).tolist()}")
assert res_store_mc["efficiency_max_residual"] < 0.5
print("  E: PASS")

# ============================================================
# Part F: HTTP API
# ============================================================
print("=== Part F: HTTP API ===")
from fastapi.testclient import TestClient  # noqa: E402
import main as appmod  # noqa: E402

client = TestClient(appmod.app)

# F.1 /train uplift (small)
r = client.post("/train", json={
    "mode": "full",
    "config": {"task_kind": "uplift_attribution", "seed": SEED,
               "n_users": N_USERS, "n_items": N_ITEMS},
})
assert r.status_code == 200, r.text
tr = r.json()
assert tr["task_kind"] == "uplift_attribution"
assert "pehe" in tr["metrics"] and "ate_bias" in tr["metrics"]
run_id = tr["run_id"]
print(f"  F.1 /train uplift: run_id={run_id}, PEHE={tr['metrics']['pehe']:.4f} PASS")

# F.2 /infer features -> tau_hat floats
X_sub = eval_ds.X[:3]
r = client.post("/infer", json={
    "run_id": run_id,
    "inputs": [
        {"query_id": f"ex_{i}", "features": X_sub[i].tolist()} for i in range(3)
    ],
})
assert r.status_code == 200, r.text
outs = r.json()["outputs"]
assert all(isinstance(o["prediction"], float) for o in outs)
assert all(o["confidence"] is None for o in outs)
direct = model.predict_tau(X_sub)
api_taus = np.array([o["prediction"] for o in outs])
assert np.allclose(api_taus, direct, atol=1e-9)
print(f"  F.2 /infer features: tau_hat={[round(float(t), 3) for t in api_taus]} PASS")

# F.3 /infer missing features -> 400
r = client.post("/infer", json={"run_id": run_id, "inputs": [{"query_id": "x"}]})
assert r.status_code == 400
print("  F.3 /infer missing features -> 400 PASS")

# F.4 /shapley feature exact
r = client.post("/shapley", json={"run_id": run_id, "level": "feature", "algorithm": "exact"})
assert r.status_code == 200, r.text
body = r.json()
assert body["n_players"] == 10
assert body["efficiency_max_residual"] < 1e-8
d = body["details"]
assert "true_phi_structural" in d and "mean_abs_error_same_scale" in d
assert d["fixed_features"]["names"] == ["门店等级"]
assert abs(body["phi_means"]["门店等级"]) < 1e-12
print(f"  F.4 /shapley feature exact: residual={body['efficiency_max_residual']:.2e}, "
      f"mean_abs_err={d['mean_abs_error_same_scale']:.4f} PASS")

# F.5 /shapley store monte_carlo
r = client.post("/shapley", json={
    "run_id": run_id, "level": "store", "algorithm": "monte_carlo",
    "n_permutations": 50,
})
assert r.status_code == 200, r.text
body = r.json()
assert body["n_players"] == 6 and body["sampling_variance"] is not None
print(f"  F.5 /shapley store MC: phi={body['phi_means']} PASS")

# F.6 /shapley on non-uplift run -> 400
r = client.post("/train", json={"mode": "full", "config": {"task_kind": "intent_clf"}})
assert r.status_code == 200, r.text
clf_run = r.json()["run_id"]
r = client.post("/shapley", json={"run_id": clf_run, "level": "store", "algorithm": "exact"})
assert r.status_code == 400
print("  F.6 /shapley on intent_clf run -> 400 PASS")

# F.7 bad enum -> 422 (pydantic Literal)
r = client.post("/shapley", json={
    "run_id": run_id, "level": "feature", "algorithm": "auto",
})
assert r.status_code == 422
print("  F.7 algorithm='auto' -> 422 (no auto-switch, caller-explicit) PASS")

# F.8 intent_clf back-compat: /infer text path
r = client.post("/infer", json={
    "run_id": clf_run,
    "inputs": [{"query_id": "q1", "text": "I lost my card and need help"}],
})
assert r.status_code == 200, r.text
o = r.json()["outputs"][0]
assert float(o["prediction"]).is_integer() and o["confidence"] is not None
print(f"  F.8 intent_clf back-compat: prediction={o['prediction']}, "
      f"confidence={o['confidence']:.3f} PASS")

print("  F: PASS")

# ============================================================
shutil.rmtree(_TMP, ignore_errors=True)
print("=" * 60)
print("ALL STEP-2 SMOKE TESTS PASSED")
print(f"  PEHE={metrics['pehe']:.4f} (paper band ~1.9-3.9), "
      f"ate_bias={metrics['ate_bias']:.4f}")
print(f"  feature exact efficiency residual: "
      f"{res_model['efficiency_max_residual']:.3e}")
print(f"  closed-form falsifier max diff:    {err_impl:.3e}")
print(f"  store exact efficiency residual:   "
      f"{res_store['efficiency_max_residual']:.3e}")
print("=" * 60)

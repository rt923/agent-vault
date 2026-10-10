#!/usr/bin/env python3
"""End-to-end proof for judge v0.2 causal metrics (pehe / ate_bias).

Uses FastAPI's TestClient -- a REAL ASGI round-trip (no socket, no file
write) that still exercises the full HTTP stack:

  * lifespan startup reads JUDGE_LABELS_PATH + JUDGE_TAUS_PATH and loads taus
  * routing + the `require_architect` Bearer gate on privileged endpoints
  * JSON request parsing and response-model validation
  * the causal scoring path (pehe) end-to-end

Driven sequence:
  POST /define-metric {metric_name: "pehe"}   (architect Bearer)
  POST /aggregate      {predictions: tau_hat}  -> PEHE (perfect vs noisy)
  POST /evaluate       {predictions: tau_hat}  -> PEHE on holdout (perfect)

LABELLED SYNTHETIC: the tau fixture (taus.example.json) is synthetic -- it
proves the wiring is correct, NOT that any real causal estimate is good.

Run:  python e2e_causal_proof.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

# --- env must be set BEFORE importing the app (startup reads it) ---
_HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = "operator-context-present"  # matches config default / JUDGE_ARCHITECT_TOKEN
os.environ["JUDGE_ARCHITECT_TOKEN"] = TOKEN
os.environ["JUDGE_LABELS_PATH"] = os.path.join(_HERE, "labels.example.json")
os.environ["JUDGE_TAUS_PATH"] = os.path.join(_HERE, "taus.example.json")

# --- load judge_service package from the hyphenated dir (same trick as smoke_test) ---
_PKG_NAME = "judge_service"
_pkg = types.ModuleType(_PKG_NAME)
_pkg.__path__ = [_HERE]
sys.modules[_PKG_NAME] = _pkg
for _mod in ("config", "schemas", "store", "scoring", "audit", "rate_limit", "main"):
    _spec = importlib.util.spec_from_file_location(
        f"{_PKG_NAME}.{_mod}", os.path.join(_HERE, f"{_mod}.py")
    )
    _m = importlib.util.module_from_spec(_spec)
    sys.modules[f"{_PKG_NAME}.{_mod}"] = _m
    _spec.loader.exec_module(_m)

from judge_service.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TEAM = "team-e2e"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def main() -> int:
    with TestClient(app) as client:
        # health (lifespan already loaded taus during startup)
        assert client.get("/health").json()["status"] == "ok"
        print("[e2e] /health OK (startup loaded labels + synthetic taus)")

        # ground-truth tau to build tau_hat estimates
        import json
        tau = json.load(open(os.environ["JUDGE_TAUS_PATH"], encoding="utf-8"))
        perfect = {ex: tau[ex] for ex in tau}
        noisy = {ex: round(tau[ex] + 0.15, 4) for ex in tau}

        # 1) define causal metric (architect Bearer)
        dm = client.post(
            "/define-metric",
            json={"team_id": TEAM, "metric_name": "pehe", "params": {}},
            headers=AUTH,
        ).json()
        assert dm.get("active") is True and dm.get("metric_name") == "pehe", dm
        print(f"[e2e] /define-metric -> {dm['metric_name']} active OK")

        # 2) aggregate perfect estimates -> PEHE ~ 0
        agg_p = client.post(
            "/aggregate", json={"team_id": TEAM, "predictions": perfect}, headers=AUTH
        ).json()
        # 3) aggregate noisy estimates -> PEHE > 0
        agg_n = client.post(
            "/aggregate", json={"team_id": TEAM, "predictions": noisy}, headers=AUTH
        ).json()
        print(f"[e2e] /aggregate perfect PEHE = {agg_p['aggregate_score']:.6f} "
              f"(n={agg_p['n_examples']})")
        print(f"[e2e] /aggregate noisy  PEHE = {agg_n['aggregate_score']:.6f} "
              f"(n={agg_n['n_examples']})")

        # 4) evaluate (holdout) perfect -> PEHE ~ 0
        ev = client.post(
            "/evaluate", json={"team_id": TEAM, "predictions": perfect}, headers=AUTH
        ).json()
        print(f"[e2e] /evaluate perfect PEHE = {ev['holdout_score']:.6f} "
              f"(n={ev['n_examples']}, missing={len(ev['missing'])})")

        ok = True
        if abs(agg_p["aggregate_score"]) > 1e-9:
            print("[e2e] FAIL: perfect PEHE should be 0"); ok = False
        if agg_n["aggregate_score"] <= 0:
            print("[e2e] FAIL: noisy PEHE should be > 0"); ok = False
        if abs(ev["holdout_score"]) > 1e-9:
            print("[e2e] FAIL: holdout perfect PEHE should be 0"); ok = False

    if ok:
        print("\nE2E CAUSAL PROOF PASSED (synthetic tau fixture)")
        return 0
    print("\nE2E CAUSAL PROOF FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

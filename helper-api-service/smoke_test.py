"""Smoke test for helper-api (mirrors judge_service smoke_test shape).

Exercises the route handler functions directly (no live server needed) to
validate the contract pinned by the OQ decision card:
  1. /train -> run_id + status=completed + macro_f1/accuracy in [0,1], model learns
  2. /infer -> one output per input; prediction is int; confidence in [0,1]
  3. /baselines -> exactly 6 entries (3 baselines x 2 metrics) + n_examples > 0
  4. baseline values in [0,1]; tfidf-logreg macro_f1 >= majority-class
  5. /infer default run_id resolves to latest trained run; unsupported task_kind -> 400
"""
from __future__ import annotations

import os
import sys
import tempfile

# Point models_dir at a temp dir BEFORE importing main (config reads env at import).
_TMP = tempfile.mkdtemp(prefix="helper_smoke_")
os.environ["HELPER_MODELS_DIR"] = _TMP
os.environ["HELPER_DATA_SOURCE"] = "local"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException

from main import (
    baselines_endpoint,
    infer_endpoint,
    train,
)
from schemas import (
    BaselinesRequest,
    InferInputItem,
    InferRequest,
    TrainRequest,
)


def main() -> int:
    # --- contract 1: /train learns and reports same-scale metrics ---
    tr = train(TrainRequest(task_id="smoke", config={"task_kind": "intent_clf"}))
    assert tr.status == "completed", f"status={tr.status}"
    assert "macro_f1" in tr.metrics and "accuracy" in tr.metrics
    assert 0.0 <= tr.metrics["macro_f1"] <= 1.0
    assert 0.0 <= tr.metrics["accuracy"] <= 1.0
    assert tr.metrics["macro_f1"] > 0.7, f"model should learn, got {tr.metrics['macro_f1']}"
    print(
        f"[train] run_id={tr.run_id} macro_f1={tr.metrics['macro_f1']:.3f} "
        f"acc={tr.metrics['accuracy']:.3f} OK"
    )
    run_id = tr.run_id

    # --- contract 2: /infer types + one output per input ---
    ir = infer_endpoint(
        InferRequest(
            run_id=run_id,
            inputs=[
                InferInputItem(query_id="q1", text="I want to cancel my card"),
                InferInputItem(query_id="q2", text="What is my available balance?"),
            ],
        )
    )
    assert len(ir.outputs) == 2, "must echo one output per input"
    for o in ir.outputs:
        assert isinstance(o.prediction, int), "prediction must be int (judge same-scale)"
        assert isinstance(o.confidence, float)
        assert 0.0 <= o.confidence <= 1.0
    print(f"[infer] 2 outputs, preds={[o.prediction for o in ir.outputs]} OK")

    # --- contract 3: /baselines shape ---
    br = baselines_endpoint(BaselinesRequest())
    assert len(br.baselines) == 6, f"expected 6 entries (3x2), got {len(br.baselines)}"
    assert br.n_examples > 0, "n_examples must be positive"
    print(f"[baselines] {len(br.baselines)} entries, n_examples={br.n_examples} OK")

    # --- contract 4: range + learned model beats trivial baseline ---
    vals = {(e.name, e.metric): e.value for e in br.baselines}
    for (name, metric), v in vals.items():
        assert 0.0 <= v <= 1.0, f"{name}.{metric} out of range: {v}"
    assert vals[("tfidf-logreg", "macro_f1")] >= vals[
        ("majority-class", "macro_f1")
    ], "tfidf-logreg should beat majority-class"
    print("[baselines] all in [0,1]; tfidf-logreg >= majority-class OK")

    # --- contract 5: default run_id resolution + guardrail ---
    ir2 = infer_endpoint(InferRequest(inputs=[InferInputItem(query_id="q3", text="help")]))
    assert ir2.run_id == run_id, "infer without run_id must use latest trained run"
    print("[infer] default run_id resolution OK")
    try:
        train(TrainRequest(config={"task_kind": "shapley_attribution"}))
        print("[train] FAIL: unsupported task_kind did not raise")
        return 1
    except HTTPException as exc:
        assert exc.status_code == 400
        print("[train] unsupported task_kind -> 400 OK")

    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

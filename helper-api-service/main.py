"""helper-api service -- FastAPI app.

Routes (aligned to coder-app.dsl.yaml provider_id=helper-api, tool_name
train / infer / baselines):
  GET  /health      liveness
  POST /train       train intent_clf on the configured dataset, save artifact
  POST /infer       run inference -> [{query_id, prediction:int, confidence:float}]
  POST /baselines   report 3 baselines' macro_f1 + accuracy on the eval split

Import pattern: supports both `python main.py` (flat) and package import.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException

try:
    from .config import settings
    from .data_adapter import get_dataset
    from .schemas import (
        BaselinesRequest,
        BaselinesResponse,
        BaselineEntry,
        InferRequest,
        InferResponse,
        InferOutputItem,
        TrainRequest,
        TrainResponse,
    )
    from .store import ModelStore
    from .train import train_intent_clf
    from .infer import infer as run_infer
    from .baselines import compute_baselines, SUPPORTED_METRICS
    from .metrics import compute_metrics
except ImportError:  # flat run (python main.py)
    from config import settings
    from data_adapter import get_dataset
    from schemas import (
        BaselinesRequest,
        BaselinesResponse,
        BaselineEntry,
        InferRequest,
        InferResponse,
        InferOutputItem,
        TrainRequest,
        TrainResponse,
    )
    from store import ModelStore
    from train import train_intent_clf
    from infer import infer as run_infer
    from baselines import compute_baselines, SUPPORTED_METRICS
    from metrics import compute_metrics


app = FastAPI(title="helper-api", version="0.1.0")

# Module-level singletons.
_store = ModelStore(settings.models_dir)
_latest_run_id: str | None = _store.latest_run_id()


def _split_indices(n: int, frac: float, seed: int) -> tuple[list[int], list[int]]:
    """Deterministic train/eval split (eval = first n*frac after shuffle)."""
    idx = list(range(n))
    rng = random.Random(seed)
    rng.shuffle(idx)
    n_eval = max(1, int(n * frac))
    return idx[n_eval:], idx[:n_eval]  # train, eval


def _make_run_id(task_kind: str) -> str:
    base = f"{task_kind}-{datetime.now():%Y%m%d%H%M%S}"
    if _store.exists(base):
        base = f"{base}-{uuid.uuid4().hex[:4]}"
    return base


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/train", response_model=TrainResponse)
def train(req: TrainRequest) -> TrainResponse:
    task_kind = (req.config or {}).get("task_kind", settings.default_task_kind)
    if task_kind not in settings.supported_task_kinds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported task_kind '{task_kind}'; v0.1 supports "
                f"{settings.supported_task_kinds} only (v0.2 extends)"
            ),
        )
    if req.mode != "full":
        raise HTTPException(
            status_code=400,
            detail="mode 'incremental' not implemented in v0.1 (use 'full')",
        )

    ds = get_dataset(
        settings.data_source,
        settings.local_dataset_path,
        req.dataset_id or settings.dify_dataset_id,
        settings.dify_api_base,
        settings.dify_api_key,
    )
    n = len(ds.texts)
    if n == 0:
        raise HTTPException(status_code=400, detail="empty dataset")

    train_idx, eval_idx = _split_indices(n, settings.eval_fraction, settings.eval_seed)
    model = train_intent_clf(
        [ds.texts[i] for i in train_idx], [ds.labels[i] for i in train_idx]
    )

    ev_texts = [ds.texts[i] for i in eval_idx]
    ev_labels = [ds.labels[i] for i in eval_idx]
    ev_preds, _ = run_infer(model, ev_texts)
    metrics = compute_metrics(ev_labels, ev_preds)

    run_id = _make_run_id(task_kind)
    _store.save(
        run_id,
        model,
        metrics,
        {
            "task_kind": task_kind,
            "n_train": len(train_idx),
            "n_classes": len(ds.label_names),
            "label_names": ds.label_names,
        },
    )
    global _latest_run_id
    _latest_run_id = run_id
    return TrainResponse(
        run_id=run_id,
        status="completed",
        task_kind=task_kind,
        n_train=len(train_idx),
        metrics=metrics,
    )


@app.post("/infer", response_model=InferResponse)
def infer_endpoint(req: InferRequest) -> InferResponse:
    global _latest_run_id
    run_id = req.run_id or _latest_run_id
    if not run_id:
        raise HTTPException(
            status_code=400,
            detail="no run_id provided and no model trained yet; call /train first",
        )
    if not _store.exists(run_id):
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    model, _meta = _store.load(run_id)
    texts = [it.text for it in req.inputs]
    preds, confs = run_infer(model, texts)
    outputs = [
        InferOutputItem(query_id=it.query_id, prediction=p, confidence=c)
        for it, p, c in zip(req.inputs, preds, confs)
    ]
    return InferResponse(outputs=outputs, run_id=run_id)


@app.post("/baselines", response_model=BaselinesResponse)
def baselines_endpoint(req: BaselinesRequest) -> BaselinesResponse:
    ds = get_dataset(
        settings.data_source,
        settings.local_dataset_path,
        req.task_id or settings.dify_dataset_id,
        settings.dify_api_base,
        settings.dify_api_key,
    )
    n = len(ds.texts)
    if n == 0:
        raise HTTPException(status_code=400, detail="empty dataset")

    _train_idx, eval_idx = _split_indices(n, settings.eval_fraction, settings.eval_seed)
    ev_texts = [ds.texts[i] for i in eval_idx]
    ev_labels = [ds.labels[i] for i in eval_idx]

    wanted = [m for m in (req.metrics or SUPPORTED_METRICS) if m in SUPPORTED_METRICS]
    if not wanted:
        wanted = list(SUPPORTED_METRICS)
    entries = compute_baselines(
        ev_texts, ev_labels, seed=settings.eval_seed, metrics=tuple(wanted)
    )
    return BaselinesResponse(
        baselines=entries,
        n_examples=len(ev_labels),
        task_kind=settings.default_task_kind,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)

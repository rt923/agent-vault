"""Judge service -- the sole external metric for the multi-agent topology.

Topology role: judge sits outside the coder sandbox. Architect calls it to
score coder submissions. Coders never call judge directly.

Endpoints (aligned to team/dify/*.dsl.yaml drafts)
--------------------------------------------------
GET  /health                    liveness
POST /submit                    coder submits one sandbox prediction (rate-limited)
POST /score                     alias of /submit (back-compat)
GET  /budget/{team_id}          remaining budget
POST /audit                     architect pulls audit report (bearer)
GET  /audit/{team_id}           alias of POST /audit (back-compat)
POST /define-metric             architect defines the sole metric (bearer, sole write-right)
POST /aggregate                 harness aggregates N coder results
POST /evaluate                  final holdout eval (architect-only, bearer)

Secrets: read JUDGE_TOKEN (or JUDGE_ARCHITECT_TOKEN fallback) from env.
Default placeholder is harmless -- privileged endpoints stay locked until set.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from .audit import build_report
from .config import settings
from .rate_limit import BudgetExhausted, check_and_consume
from .scoring import compute_aggregate, score
from .schemas import (
    AggregateRequest,
    AggregateResponse,
    AuditReport,
    AuditRequest,
    BudgetResponse,
    DefineMetricRequest,
    DefineMetricResponse,
    EvaluateRequest,
    EvaluateResponse,
    ScoreRequest,
    ScoreResponse,
)
from .schemas import AuditEntry
from .store import Store, get_store

app = FastAPI(title="judge-service", version="0.2.0")

# Module-level store instance (singleton for the process).
_store: Store | None = None


def store_dep() -> Store:
    global _store
    if _store is None:
        _store = get_store()
    return _store


def require_architect(
    authorization: str | None = Header(default=None),
    x_architect_token: str | None = Header(default=None),
) -> None:
    """Bearer-token gate for architect-only endpoints.

    Accepts either:
      - Authorization: Bearer <token>   (draft convention: {{ env.JUDGE_TOKEN }})
      - X-Architect-Token: <token>      (legacy)
    Default placeholder never matches a real request.
    """
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif x_architect_token:
        token = x_architect_token.strip()

    if token != settings.architect_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="architect token required",
        )


def _active_metric(store: Store, team_id: str) -> str:
    """Return the scorer name for a team: architect-defined else default."""
    m = store.get_metric(team_id)
    return m[0] if m else settings.scoring_fn


def _active_metric_pair(store: Store, team_id: str) -> tuple[str, dict[str, Any]]:
    """Return (scorer_name, params) for a team; default accuracy, {}."""
    m = store.get_metric(team_id)
    if m:
        return m[0], dict(m[1])
    return settings.scoring_fn, {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _do_submit(req: ScoreRequest, store: Store) -> ScoreResponse:
    """Core submission logic shared by /submit and /score."""
    # --- holdout isolation ---
    # Treat unknown AND holdout examples identically to avoid set-membership leaks.
    label = store.get_label(req.example_id)
    if label is None or store.is_holdout(req.example_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="example not found",
        )

    # --- budget / rate limit ---
    try:
        team_remaining, coder_remaining = check_and_consume(
            store, req.team_id, req.coder_id
        )
    except BudgetExhausted as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "submit budget exhausted",
                "team_id": exc.team_id,
                "remaining": exc.remaining,
            },
        )

    # --- score (per-example feedback is always accuracy; the aggregate metric
    #     such as macro_f1 is applied at /aggregate and /evaluate over the set) ---
    s, correct = score(req.prediction, label, "accuracy")

    # --- audit ---
    store.add_submission(AuditEntry(
        team_id=req.team_id,
        coder_id=req.coder_id,
        example_id=req.example_id,
        prediction=req.prediction,
        score=s,
        timestamp=datetime.now(timezone.utc),
    ))

    return ScoreResponse(
        example_id=req.example_id,
        prediction=req.prediction,
        score=s,
        correct=correct,
        remaining_team_budget=team_remaining,
        remaining_coder_budget=coder_remaining,
    )


@app.post("/submit", response_model=ScoreResponse)
def submit(req: ScoreRequest, store: Store = Depends(store_dep)) -> ScoreResponse:
    """Coder submits one sandbox prediction for scoring (rate-limited).

    Draft contract: coder-app.dsl.yaml -> eval_submit_limited -> POST /submit.
    """
    return _do_submit(req, store)


@app.post("/score", response_model=ScoreResponse)
def score_prediction(req: ScoreRequest, store: Store = Depends(store_dep)) -> ScoreResponse:
    """Alias of /submit for backward compatibility."""
    return _do_submit(req, store)


@app.get("/budget/{team_id}", response_model=BudgetResponse)
def get_budget(team_id: str, store: Store = Depends(store_dep)) -> BudgetResponse:
    total = store.get_team_budget(team_id)
    used = store.get_team_used(team_id)
    return BudgetResponse(
        team_id=team_id,
        total_budget=total,
        used=used,
        remaining=total - used,
        per_coder_cap=settings.per_coder_cap if settings.per_coder_cap > 0 else None,
    )


@app.post("/audit", response_model=AuditReport)
def post_audit(
    req: AuditRequest,
    store: Store = Depends(store_dep),
    _auth: None = Depends(require_architect),
) -> AuditReport:
    """Architect pulls the audit report for a team.

    Draft contract: architect-app.dsl.yaml -> eval_audit -> POST /audit.
    """
    return build_report(store, req.team_id)


@app.get("/audit/{team_id}", response_model=AuditReport)
def get_audit(
    team_id: str,
    store: Store = Depends(store_dep),
    _auth: None = Depends(require_architect),
) -> AuditReport:
    """Alias of POST /audit for backward compatibility."""
    return build_report(store, team_id)


@app.post("/define-metric", response_model=DefineMetricResponse)
def define_metric(
    req: DefineMetricRequest,
    store: Store = Depends(store_dep),
    _auth: None = Depends(require_architect),
) -> DefineMetricResponse:
    """Architect defines the sole external metric. Sole write-right.

    Draft contract: architect-app.dsl.yaml -> metric_define -> POST /define-metric.
    Replaces any prior definition for the team; /submit then uses this scorer.
    """
    store.set_metric(req.team_id, req.metric_name, req.params)
    return DefineMetricResponse(
        team_id=req.team_id,
        metric_name=req.metric_name,
        params=req.params,
        active=True,
    )


@app.post("/aggregate", response_model=AggregateResponse)
def aggregate(
    req: AggregateRequest,
    store: Store = Depends(store_dep),
    _auth: None = Depends(require_architect),
) -> AggregateResponse:
    """Harness aggregates N coder results into one score.

    Draft contract: harness-workflow.dsl.yaml -> validator -> POST /aggregate.
    Scores the merged predictions against the sandbox labels (NOT holdout).
    Does NOT consume submit budget.
    """
    if not req.predictions:
        raise HTTPException(status_code=400, detail="predictions required")

    metric_fn, params = _active_metric_pair(store, req.team_id)
    k = int(params.get("k", 2))
    # aggregate only scores sandbox examples; holdout stays isolated
    sandbox_preds = {
        ex_id: pred
        for ex_id, pred in req.predictions.items()
        if not store.is_holdout(ex_id)
    }
    agg, n = compute_aggregate(sandbox_preds, store.get_label, metric_fn, k)

    return AggregateResponse(
        team_id=req.team_id,
        aggregate_score=agg,
        n_examples=n,
    )


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate_holdout(
    req: EvaluateRequest,
    store: Store = Depends(store_dep),
    _auth: None = Depends(require_architect),
) -> EvaluateResponse:
    """Final evaluation on the holdout set. Architect-only.

    Takes predictions for ALL holdout examples. Missing examples count as
    incorrect. This endpoint does NOT consume submit budget.
    """
    holdout_ids = store.get_holdout_ids()
    if not holdout_ids:
        raise HTTPException(status_code=500, detail="holdout set not loaded")

    metric_fn, params = _active_metric_pair(store, req.team_id)
    k = int(params.get("k", 2))
    # Map missing holdout predictions to -1 (guaranteed wrong) so they count
    # against the score instead of being skipped.
    holdout_preds = {
        ex_id: (req.predictions.get(ex_id) if req.predictions.get(ex_id) is not None else -1)
        for ex_id in holdout_ids
    }
    missing = [ex_id for ex_id in holdout_ids if ex_id not in req.predictions]
    holdout_score, _ = compute_aggregate(
        holdout_preds, store.get_label, metric_fn, k, skip_missing_pred=False
    )

    return EvaluateResponse(
        team_id=req.team_id,
        holdout_score=holdout_score,
        n_examples=len(holdout_ids),
        missing=missing,
    )


# --- startup: load dataset ---
@app.on_event("startup")
def _bootstrap() -> None:
    import json
    import os

    global _store
    if _store is None:
        _store = get_store()

    labels_path = os.getenv("JUDGE_LABELS_PATH", "")
    if labels_path and os.path.exists(labels_path):
        with open(labels_path, "r", encoding="utf-8") as f:
            labels = json.load(f)
        _store.load_dataset(labels)  # type: ignore[union-attr]
    # If no labels loaded, /submit returns 404 for everything (safe default).

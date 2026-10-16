"""Pydantic schemas for the helper-api service.

Field shapes are pinned to the OQ decision card (@see _design/helper-api-oq-decision-card.md):
  - /train  : spec-required {run_id, status} + metrics extras (OQ-1).
  - /infer  : inputs tightened to [{query_id, text}] (OQ-2.1);
              outputs = [{query_id, prediction:int, confidence:float}] (OQ-2.2).
  - /baselines : baselines[] = {name, metric, value} + top-level n_examples (OQ-3.3);
              metrics constrained to macro_f1 + accuracy (OQ-3.2, judge same-scale).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- /train ---


class TrainRequest(BaseModel):
    task_id: str | None = Field(
        default=None, description="Task identifier (aligns with judge team_id)."
    )
    dataset_id: str | None = Field(
        default=None, description="Dify dataset id; None -> default for task_kind."
    )
    mode: Literal["full", "incremental"] = Field(
        default="full", description="OQ-1.3: v0.1 supports full (sync, blocking)."
    )
    config: dict = Field(
        default_factory=dict,
        description="Declarative task config; reads config.task_kind (OQ-1.1).",
    )


class TrainResponse(BaseModel):
    run_id: str
    status: str  # "completed"
    task_kind: str
    n_train: int
    metrics: dict  # {"macro_f1": float, "accuracy": float}


# --- /infer ---


class InferInputItem(BaseModel):
    query_id: str = Field(..., description="Example id; aligns with judge example_id.")
    text: str | None = Field(
        default=None,
        description="Raw utterance (intent_clf). Required for classification runs.",
    )
    features: list[float] | None = Field(
        default=None,
        description="Feature vector (uplift_attribution, 10 floats). Required for uplift runs.",
    )


class InferRequest(BaseModel):
    run_id: str | None = Field(
        default=None, description="Trained artifact id; None -> latest run."
    )
    inputs: list[InferInputItem] = Field(..., min_length=1)


class InferOutputItem(BaseModel):
    query_id: str
    # v0.2: widened int -> float. Classification still emits integer class ids
    # (JSON numbers); uplift emits tau_hat as a float (judge /submit takes Any).
    prediction: float
    confidence: float | None = None  # None for regression (uplift) runs


class InferResponse(BaseModel):
    outputs: list[InferOutputItem]
    run_id: str


# --- /shapley (v0.2 uplift_attribution, dual-level) ---


class ShapleyRequest(BaseModel):
    run_id: str | None = Field(
        default=None, description="Uplift run id; None -> latest run."
    )
    level: Literal["feature", "store"] = Field(
        ..., description="feature = 表4.3 (10 features); store = 表4.10 (6 stores)."
    )
    algorithm: Literal["exact", "monte_carlo"] = Field(
        ...,
        description=(
            "Caller-explicit (paper L303 discloses the n>20 bottleneck but defines "
            "NO auto-switch). exact: O(2^n), hard cap n<=20 -> 400 otherwise."
        ),
    )
    n_permutations: int = Field(
        default=100, ge=2, le=1000, description="monte_carlo only."
    )
    seed: int | None = Field(
        default=None, description="MC sampling seed; None -> run's training seed."
    )


class ShapleyResponse(BaseModel):
    run_id: str
    level: str
    algorithm: str
    n_players: int
    # feature level: per-feature mean phi (model side); store level: per-store phi
    phi_means: dict[str, float]
    # exact: ~machine epsilon; monte_carlo: permutation-order residual
    efficiency_max_residual: float
    sampling_variance: dict[str, float] | None = Field(
        default=None, description="Per-player MC sampling variance (monte_carlo only)."
    )
    details: dict = Field(default_factory=dict)


# --- /baselines ---


class BaselinesRequest(BaseModel):
    task_id: str | None = Field(default=None, description="Task id; None -> default.")
    metrics: list[str] | None = Field(
        default=None,
        description="Metric filter; default [macro_f1, accuracy] (OQ-3.2).",
    )


class BaselineEntry(BaseModel):
    name: str  # one of: majority-class, uniform-random, tfidf-logreg
    metric: str  # macro_f1 | accuracy
    value: float


class BaselinesResponse(BaseModel):
    baselines: list[BaselineEntry]
    n_examples: int  # eval sample count (credibility of scores, OQ-3.3)
    task_kind: str

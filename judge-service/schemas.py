"""Pydantic schemas for the judge service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Requests ---


class ScoreRequest(BaseModel):
    """A single prediction submitted by a coder for scoring.

    Only sandbox examples are accepted. Holdout examples are rejected with
    a generic 404 to avoid leaking which examples are held out.
    """

    team_id: str = Field(..., description="Architect-issued team identifier.")
    coder_id: str = Field(..., description="Coder agent identifier within the team.")
    example_id: str = Field(..., description="Example to score (must be in sandbox set).")
    prediction: Any = Field(..., description="Model prediction (type depends on task).")


class EvaluateRequest(BaseModel):
    """Final evaluation on the holdout set. Architect-only."""

    team_id: str
    # Map of example_id -> prediction for ALL holdout examples.
    predictions: dict[str, Any]


class DefineMetricRequest(BaseModel):
    """Architect defines the sole external metric. Sole write-right.

    Stored so /submit uses this scorer. Replaces any prior definition.
    """

    team_id: str
    metric_name: str = Field(..., description="e.g. 'accuracy', 'f1', 'mae'")
    params: dict[str, Any] = Field(default_factory=dict)


class DefineMetricResponse(BaseModel):
    team_id: str
    metric_name: str
    params: dict[str, Any]
    active: bool = True


class AuditRequest(BaseModel):
    """POST /audit body (architect-only)."""

    team_id: str


class AggregateRequest(BaseModel):
    """harness workflow aggregates N coder results.

    predictions: example_id -> prediction (best-of-N or merged).
    """

    team_id: str
    predictions: dict[str, Any]
    coder_ids: list[str] = Field(default_factory=list)


class AggregateResponse(BaseModel):
    team_id: str
    aggregate_score: float
    n_examples: int
    per_coder_scores: dict[str, float] = Field(default_factory=dict)


# --- Responses ---


class ScoreResponse(BaseModel):
    example_id: str
    prediction: Any
    score: float
    correct: bool
    remaining_team_budget: int
    remaining_coder_budget: int | None = None


class BudgetResponse(BaseModel):
    team_id: str
    total_budget: int
    used: int
    remaining: int
    per_coder_cap: int | None
    coders: dict[str, dict[str, int]] = Field(default_factory=dict)


class AuditEntry(BaseModel):
    team_id: str
    coder_id: str
    example_id: str
    prediction: Any
    score: float
    timestamp: datetime


class AuditFlag(BaseModel):
    team_id: str
    rule: str
    severity: Literal["warn", "escalate"]
    detail: str
    examples: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    team_id: str
    total_submissions: int
    distinct_examples: int
    flags: list[AuditFlag] = Field(default_factory=list)
    entries: list[AuditEntry] = Field(default_factory=list)


class EvaluateResponse(BaseModel):
    team_id: str
    holdout_score: float
    n_examples: int
    missing: list[str] = Field(default_factory=list)

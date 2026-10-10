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
    text: str = Field(..., description="Raw utterance / query text.")


class InferRequest(BaseModel):
    run_id: str | None = Field(
        default=None, description="Trained artifact id; None -> latest run."
    )
    inputs: list[InferInputItem] = Field(..., min_length=1)


class InferOutputItem(BaseModel):
    query_id: str
    prediction: int  # integer class id (judge same-scale contract, OQ-2.2)
    confidence: float


class InferResponse(BaseModel):
    outputs: list[InferOutputItem]
    run_id: str


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

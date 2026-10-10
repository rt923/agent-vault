"""Metric computation. v0.1 implements ONLY macro_f1 (primary) + accuracy
(secondary), matching judge's sole external scale (OQ-3.2). AUC/NDCG/uplift are
explicitly out of scope (v0.2) to preserve the "single external ruler" principle.
"""
from __future__ import annotations

from typing import Iterable

from sklearn.metrics import accuracy_score, f1_score


def compute_metrics(
    y_true: Iterable[int], y_pred: Iterable[int]
) -> dict[str, float]:
    """Return {macro_f1, accuracy} over the same eval set.

    zero_division=0 keeps the contract stable when a class has no support.
    """
    yt = list(y_true)
    yp = list(y_pred)
    return {
        "macro_f1": float(f1_score(yt, yp, average="macro", zero_division=0)),
        "accuracy": float(accuracy_score(yt, yp)),
    }

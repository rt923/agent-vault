"""Inference logic for /infer (OQ-2.2).

Returns integer class id (judge same-scale) + confidence (max softmax prob).
"""
from __future__ import annotations

from typing import Any

import numpy as np


def infer(model: Any, texts: list[str]) -> tuple[list[int], list[float]]:
    """Predict class ids and confidences for a batch of texts."""
    proba = model.predict_proba(texts)
    preds = proba.argmax(axis=1).tolist()
    confs = proba.max(axis=1).astype(float).tolist()
    return [int(p) for p in preds], [float(c) for c in confs]

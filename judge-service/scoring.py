"""Scoring functions.

The judge is the sole external metric: `score()` returns the ground-truth-
based score for a single prediction. Extend here for non-classification
tasks (regression MAE, ranking NDCG, etc.).
"""
from __future__ import annotations

from typing import Any


def accuracy(prediction: Any, label: Any) -> tuple[float, bool]:
    """1.0 if prediction == label else 0.0. Returns (score, correct)."""
    correct = prediction == label
    return (1.0 if correct else 0.0), correct


SCORERS = {
    "accuracy": accuracy,
}


def _macro_f1_from_pairs(pairs: list[tuple[Any, Any]], k: int) -> float:
    """Macro-averaged F1 over k classes from (pred, label) pairs.

    Uses per-class dicts (not fixed-size arrays) so out-of-range / missing
    predictions (-1 sentinel) never crash indexing -- they simply lower recall.
    """
    from collections import defaultdict

    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    for pred, label in pairs:
        pred_i = int(pred)
        label_i = int(label)
        if pred_i == label_i:
            tp[label_i] += 1
        else:
            fp[pred_i] += 1
            fn[label_i] += 1

    f1s: list[float] = []
    for c in range(k):
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) > 0 else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        f1s.append(f1)
    return sum(f1s) / k if k > 0 else 0.0


def compute_aggregate(
    preds: dict[str, Any],
    label_fn,
    metric_name: str,
    k: int = 2,
    skip_missing_pred: bool = True,
) -> tuple[float, int]:
    """Aggregate metric over a predictions dict.

    Used by /aggregate (sandbox) and /evaluate (holdout). For /evaluate, pass
    missing predictions as -1 and skip_missing_pred=False so they count wrong.

    Returns (aggregate_score, n_examples_scored).
    """
    pairs: list[tuple[Any, Any]] = []
    for ex_id, pred in preds.items():
        lbl = label_fn(ex_id)
        if lbl is None:
            continue
        if pred is None:
            if skip_missing_pred:
                continue
            pred = -1  # guaranteed-wrong sentinel for holdout missing
        pairs.append((pred, lbl))

    if not pairs:
        return 0.0, 0

    if metric_name == "macro_f1":
        return _macro_f1_from_pairs(pairs, k), len(pairs)

    # default: accuracy (mean of per-example correctness)
    correct = sum(1.0 for p, l in pairs if p == l)
    return correct / len(pairs), len(pairs)


def score(prediction: Any, label: Any, fn_name: str = "accuracy") -> tuple[float, bool]:
    fn = SCORERS.get(fn_name, accuracy)
    return fn(prediction, label)

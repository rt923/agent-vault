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

# v0.2 causal metrics. Require ground-truth individual treatment effect
# tau_i = Y_i(1) - Y_i(0) (NOT the class label). Inert unless tau is supplied
# via the store; we never fabricate causal ground truth.
# Values mirror schemas.DefineMetricRequest.metric_name Literal + the OpenAPI
# enum in dify-dsl/judge-api.openapi.yaml.
SUPPORTED_METRICS = ("accuracy", "macro_f1", "pehe", "ate_bias")


def pehe_score(pairs: list[tuple[float, float]]) -> float:
    """PEHE = sqrt( (1/N) * sum_i (tau_hat_i - tau_i)^2 ).

    Precision in Estimation of Heterogeneous Effect -- lower is better.
    """
    if not pairs:
        return 0.0
    err = sum((th - t) ** 2 for th, t in pairs)
    return (err / len(pairs)) ** 0.5


def ate_bias_score(pairs: list[tuple[float, float]]) -> float:
    """ATE bias = | mean_i(tau_hat_i) - mean_i(tau_i) |.

    Bias of the estimated Average Treatment Effect -- lower is better.
    """
    if not pairs:
        return 0.0
    ate_hat = sum(th for th, _ in pairs) / len(pairs)
    ate = sum(t for _, t in pairs) / len(pairs)
    return abs(ate_hat - ate)


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
    tau_fn=None,
) -> tuple[float, int]:
    """Aggregate metric over a predictions dict.

    Used by /aggregate (sandbox) and /evaluate (holdout). For /evaluate, pass
    missing predictions as -1 and skip_missing_pred=False so they count wrong.

    Causal metrics (pehe, ate_bias) require ground-truth individual treatment
    effect tau, supplied via tau_fn(example_id) -> float | None. They operate
    on (tau_hat, tau) pairs where tau_hat is the prediction. If metric_name is
    causal but tau_fn is None (dataset carries no tau), raise ValueError -- we
    never fabricate causal ground truth.

    Returns (aggregate_score, n_examples_scored).
    """
    # --- causal metrics (set-level; need tau, not the class label) ---
    if metric_name in ("pehe", "ate_bias"):
        if tau_fn is None:
            raise ValueError(
                "metric '%s' requires causal ground-truth tau; "
                "load_dataset(taus=...) was not provided" % metric_name
            )
        pairs: list[tuple[float, float]] = []
        for ex_id, pred in preds.items():
            tau = tau_fn(ex_id)
            if tau is None:
                continue
            if pred is None:
                if skip_missing_pred:
                    continue
                # No natural sentinel for a missing continuous effect estimate;
                # skip rather than invent a penalty (honest scoring).
                continue
            try:
                pairs.append((float(pred), float(tau)))
            except (TypeError, ValueError):
                continue
        if not pairs:
            raise ValueError(
                "metric '%s' produced no (tau_hat, tau) pairs: dataset has no "
                "causal tau (load_dataset taus=) or all predictions missing"
                % metric_name
            )
        if metric_name == "pehe":
            return pehe_score(pairs), len(pairs)
        # ate_bias
        return ate_bias_score(pairs), len(pairs)

    # --- classification metrics (unchanged) ---
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

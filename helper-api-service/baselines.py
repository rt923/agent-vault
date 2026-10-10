"""Baseline comparison for /baselines (OQ-3).

Three offline-reproducible baselines (OQ-3.1):
  1. majority-class : always predict the training-set mode label.
  2. uniform-random : predict a uniform-random class per example.
  3. tfidf-logreg  : a fresh default-param TF-IDF + LogReg (reference model).

Metrics are MACRO_F1 (primary) + ACCURACY (secondary) only (OQ-3.2), matching
judge's sole external scale. Each (baseline, metric) pair becomes one entry.
"""
from __future__ import annotations

import random
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

try:
    from .metrics import compute_metrics
    from .schemas import BaselineEntry
except ImportError:  # flat run
    from metrics import compute_metrics
    from schemas import BaselineEntry

MACRO_F1 = "macro_f1"
ACCURACY = "accuracy"
SUPPORTED_METRICS = (MACRO_F1, ACCURACY)

BASELINE_NAMES = ("majority-class", "uniform-random", "tfidf-logreg")


def _majority_class(y_true: list[int]) -> list[int]:
    maj = Counter(y_true).most_common(1)[0][0]
    return [maj] * len(y_true)


def _uniform_random(y_true: list[int], n_classes: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.randrange(n_classes) for _ in y_true]


def _tfidf_logreg(texts: list[str], y_true: list[int]) -> list[int]:
    pipe = LogisticRegression(max_iter=1000)
    pipe.fit(TfidfVectorizer().fit_transform(texts), y_true)
    return pipe.predict(TfidfVectorizer().fit_transform(texts))


def compute_baselines(
    texts: list[str],
    y_true: list[int],
    seed: int = 42,
    metrics: tuple[str, ...] = SUPPORTED_METRICS,
) -> list[BaselineEntry]:
    """Compute the 3 baselines' scores on (texts, y_true) and emit entries."""
    if not y_true:
        raise ValueError("cannot compute baselines on empty eval set")

    n_classes = len(set(y_true))
    preds_map = {
        "majority-class": _majority_class(y_true),
        "uniform-random": _uniform_random(y_true, n_classes, seed),
        "tfidf-logreg": _tfidf_logreg(texts, y_true),
    }

    entries: list[BaselineEntry] = []
    for name in BASELINE_NAMES:
        m = compute_metrics(y_true, preds_map[name])
        for metric in metrics:
            entries.append(
                BaselineEntry(name=name, metric=metric, value=float(m[metric]))
            )
    return entries

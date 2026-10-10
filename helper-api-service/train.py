"""Training logic for the v0.1 `intent_clf` task kind.

TF-IDF (1-2 grams, sublinear) + LogisticRegression(C=10, balanced).
CPU-training 8622 rows is sub-second; full sync blocking per OQ-1.3.
"""
from __future__ import annotations

from typing import Any

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer


def build_intent_clf() -> Pipeline:
    """Construct the intent_clf pipeline (declarative task_kind=intent_clf)."""
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    stop_words="english",
                    ngram_range=(1, 2),
                    sublinear_tf=True,
                    min_df=2,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=1000,
                    C=10.0,
                    class_weight="balanced",
                ),
            ),
        ]
    )


def train_intent_clf(texts: list[str], labels: list[int]) -> Any:
    """Fit and return the trained pipeline."""
    pipe = build_intent_clf()
    pipe.fit(texts, labels)
    return pipe

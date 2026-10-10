"""Storage backend for the judge service.

Default: in-memory (process-local, lost on restart).
The `Store` protocol lets you swap in Redis/Postgres without touching
endpoint code -- just implement the protocol and return it from
`get_store()`.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Protocol

from .config import settings
from .schemas import AuditEntry


class Store(Protocol):
    # --- budget ---
    def get_team_budget(self, team_id: str) -> int: ...
    def set_team_budget(self, team_id: str, budget: int) -> None: ...
    def get_team_used(self, team_id: str) -> int: ...
    def get_coder_used(self, team_id: str, coder_id: str) -> int: ...
    def increment_used(self, team_id: str, coder_id: str) -> int: ...

    # --- submissions / audit ---
    def add_submission(self, entry: AuditEntry) -> None: ...
    def get_submissions(self, team_id: str) -> list[AuditEntry]: ...
    def get_predictions_for_example(self, team_id: str, example_id: str) -> list[Any]: ...

    # --- holdout ---
    def is_holdout(self, example_id: str) -> bool: ...
    def get_holdout_ids(self) -> set[str]: ...
    def get_label(self, example_id: str) -> Any | None: ...

    # --- metric definition (sole write-right = architect) ---
    def get_metric(self, team_id: str) -> tuple[str, dict[str, Any]] | None: ...
    def set_metric(self, team_id: str, metric_name: str, params: dict[str, Any]) -> None: ...


class InMemoryStore:
    """Thread-safe in-memory store. Good enough for single-process smoke runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._team_budget: dict[str, int] = {}
        self._team_used: dict[str, int] = defaultdict(int)
        self._coder_used: dict[tuple[str, str], int] = defaultdict(int)
        self._submissions: dict[str, list[AuditEntry]] = defaultdict(list)
        # ground-truth labels, loaded externally (see bootstrap below)
        self._labels: dict[str, Any] = {}
        self._holdout_ids: set[str] = set()
        # active metric per team (set by architect via /define-metric)
        self._metrics: dict[str, tuple[str, dict[str, Any]]] = {}

    # --- budget ---
    def get_team_budget(self, team_id: str) -> int:
        with self._lock:
            return self._team_budget.get(team_id, settings.default_submit_budget)

    def set_team_budget(self, team_id: str, budget: int) -> None:
        with self._lock:
            self._team_budget[team_id] = budget

    def get_team_used(self, team_id: str) -> int:
        with self._lock:
            return self._coder_used_total(team_id)

    def get_coder_used(self, team_id: str, coder_id: str) -> int:
        with self._lock:
            return self._coder_used[(team_id, coder_id)]

    def _coder_used_total(self, team_id: str) -> int:
        return sum(v for (t, _), v in self._coder_used.items() if t == team_id)

    def increment_used(self, team_id: str, coder_id: str) -> int:
        with self._lock:
            self._coder_used[(team_id, coder_id)] += 1
            return self._coder_used[(team_id, coder_id)]

    # --- submissions ---
    def add_submission(self, entry: AuditEntry) -> None:
        with self._lock:
            self._submissions[entry.team_id].append(entry)

    def get_submissions(self, team_id: str) -> list[AuditEntry]:
        with self._lock:
            return list(self._submissions.get(team_id, []))

    def get_predictions_for_example(self, team_id: str, example_id: str) -> list[Any]:
        with self._lock:
            return [
                e.prediction
                for e in self._submissions.get(team_id, [])
                if e.example_id == example_id
            ]

    # --- holdout / labels ---
    def is_holdout(self, example_id: str) -> bool:
        with self._lock:
            return example_id in self._holdout_ids

    def get_holdout_ids(self) -> set[str]:
        with self._lock:
            return set(self._holdout_ids)

    def get_label(self, example_id: str) -> Any | None:
        with self._lock:
            return self._labels.get(example_id)

    # --- metric definition ---
    def get_metric(self, team_id: str) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            return self._metrics.get(team_id)

    def set_metric(self, team_id: str, metric_name: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._metrics[team_id] = (metric_name, dict(params))

    # --- bootstrap (called once at startup) ---
    def load_dataset(self, labels: dict[str, Any]) -> None:
        """Load ground-truth labels and split into sandbox / holdout.

        This must be called before serving. In production, load from a file
        or DB -- never ship labels in the repo.
        """
        import random

        with self._lock:
            self._labels = dict(labels)
            all_ids = list(labels.keys())
            rng = random.Random(settings.holdout_seed)
            rng.shuffle(all_ids)
            n_holdout = max(1, int(len(all_ids) * settings.holdout_fraction))
            self._holdout_ids = set(all_ids[:n_holdout])


def get_store() -> Store:
    """Factory. Swap for Redis/PG store by checking settings.store_backend."""
    # TODO: implement RedisStore behind settings.store_backend == "redis"
    return InMemoryStore()

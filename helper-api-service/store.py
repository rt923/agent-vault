"""Model artifact store.

Saves / loads trained artifacts under settings.models_dir as
`{run_id}/model.joblib` + `{run_id}/metrics.json` (OQ-1.4: pod emptyDir).
Mirrors judge_service's store protocol but is simpler: one artifact per run,
latest-run tracking for /infer default resolution.
"""
from __future__ import annotations

import json
import os
from typing import Any

try:
    import joblib
except ImportError:  # pragma: no cover
    joblib = None  # joblib ships with scikit-learn; only reachable if sklearn missing


class ModelStore:
    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _run_dir(self, run_id: str) -> str:
        # Guard against path traversal from client-supplied run_id.
        if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
            raise ValueError(f"invalid run_id: {run_id!r}")
        return os.path.join(self.root, run_id)

    def save(
        self,
        run_id: str,
        model: Any,
        metrics: dict,
        meta: dict,
    ) -> None:
        if joblib is None:  # pragma: no cover
            raise RuntimeError("joblib unavailable (scikit-learn missing)")
        d = self._run_dir(run_id)
        os.makedirs(d, exist_ok=True)
        joblib.dump(model, os.path.join(d, "model.joblib"))
        with open(os.path.join(d, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"run_id": run_id, "metrics": metrics, **meta},
                f,
                ensure_ascii=False,
                indent=2,
            )

    def exists(self, run_id: str) -> bool:
        try:
            return os.path.exists(os.path.join(self._run_dir(run_id), "model.joblib"))
        except ValueError:
            return False

    def load(self, run_id: str) -> tuple[Any, dict]:
        if not self.exists(run_id):
            raise FileNotFoundError(f"run_id not found: {run_id}")
        d = self._run_dir(run_id)
        model = joblib.load(os.path.join(d, "model.joblib"))
        with open(os.path.join(d, "metrics.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
        return model, meta

    def latest_run_id(self) -> str | None:
        """Most recently created run dir (by mtime)."""
        try:
            dirs = [
                d
                for d in os.listdir(self.root)
                if os.path.isdir(os.path.join(self.root, d))
                and (":" not in d and "/" not in d and "\\" not in d and ".." not in d)
            ]
        except FileNotFoundError:
            return None
        if not dirs:
            return None
        best = max(
            dirs,
            key=lambda d: os.path.getmtime(os.path.join(self.root, d)),
        )
        return best

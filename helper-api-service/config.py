"""helper-api service configuration.

All switches are env-driven; the app never ships real credentials. v0.1 only
implements the `intent_clf` task kind (a BANKING77 proxy task used to wire the
full /train -> /infer -> /baselines loop). Business-mainline task kinds
(Shapley cross-store attribution, etc.) are v0.2.

Data adapter:
  HELPER_DATA_SOURCE=dify  -> pull from the Dify Dataset API (production adapter,
                            single source of truth per the OQ-1.2 decision).
  HELPER_DATA_SOURCE=local (default) -> bundled offline BANKING77 fallback, used
                            for dev / smoke and when no Dify dataset is wired.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_local_dataset() -> str:
    """Bundled offline BANKING77 training file (sits next to this module)."""
    here = Path(__file__).resolve().parent
    return str(here / "data" / "banking77_train.json")


@dataclass
class Settings:
    # --- Model storage ---
    # Pod emptyDir mount in k3d (OQ-1.4). On Windows dev, override to a temp dir.
    models_dir: str = os.getenv("HELPER_MODELS_DIR", "/models")

    # --- Task kinds (declarative; OQ-1.1) ---
    # v0.1 ships exactly one: intent_clf (BANKING77 proxy). Others -> 400.
    supported_task_kinds: tuple[str, ...] = ("intent_clf",)
    default_task_kind: str = os.getenv("HELPER_TASK_KIND", "intent_clf")

    # --- Data adapter (OQ-1.2) ---
    # "dify" = Dify Dataset API (production); "local" = bundled offline fallback.
    data_source: str = os.getenv("HELPER_DATA_SOURCE", "local")
    local_dataset_path: str = os.getenv("HELPER_LOCAL_DATASET", _default_local_dataset())

    # Dify Dataset API (production adapter)
    dify_api_base: str = os.getenv("DIFY_API_BASE", "http://127.0.0.1:30869")
    dify_dataset_id: str = os.getenv("DIFY_DATASET_ID", "")
    dify_api_key: str = os.getenv("DIFY_API_KEY", "")

    # --- Train/eval split (deterministic, so baselines compare apples-to-apples) ---
    eval_fraction: float = float(os.getenv("HELPER_EVAL_FRACTION", "0.2"))
    eval_seed: int = int(os.getenv("HELPER_EVAL_SEED", "42"))

    # --- Service ---
    host: str = os.getenv("HELPER_HOST", "0.0.0.0")
    port: int = int(os.getenv("HELPER_PORT", "8799"))


settings = Settings()

"""Judge service configuration.

All secrets/tokens are read from environment variables. The app never ships
real credentials in source. Replace placeholders via env at deploy time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Auth ---
    # Architect bearer token for privileged endpoints (/evaluate, /audit,
    # /define-metric). The drafts reference {{ env.JUDGE_TOKEN }}; we accept
    # JUDGE_TOKEN with JUDGE_ARCHITECT_TOKEN as fallback.
    # Use a long random string in production. Default placeholder is harmless.
    architect_token: str = os.getenv(
        "JUDGE_TOKEN", os.getenv("JUDGE_ARCHITECT_TOKEN", "operator-context-present")
    )

    # --- Rate limiting ---
    # Default total submission budget per team. Overridable per team via store.
    default_submit_budget: int = _env_int("JUDGE_DEFAULT_SUBMIT_BUDGET", 200)
    # Per-coder sub-cap (0 = no per-coder cap, only team cap applies).
    per_coder_cap: int = _env_int("JUDGE_PER_CODER_CAP", 0)

    # --- Holdout ---
    # Fraction of examples held out (never scored via /score).
    holdout_fraction: float = float(os.getenv("JUDGE_HOLDOUT_FRACTION", "0.3"))
    # Seed for deterministic holdout split (so restarts keep same split).
    holdout_seed: int = _env_int("JUDGE_HOLDOUT_SEED", 42)

    # --- Audit thresholds ---
    # Flag if same (team, example) gets >= this many submissions.
    max_subs_per_example: int = _env_int("JUDGE_MAX_SUBS_PER_EXAMPLE", 3)
    # Flag if a team flips prediction on the same example.
    flag_prediction_flip: bool = os.getenv("JUDGE_FLAG_FLIP", "1") != "0"
    # Escalate if a team probes >= this many distinct examples with flips.
    escalate_flip_examples: int = _env_int("JUDGE_ESCALATE_FLIP_EXAMPLES", 5)

    # --- Scoring ---
    # Scoring function name. "accuracy" for classification; extend in scoring.py.
    scoring_fn: str = os.getenv("JUDGE_SCORING_FN", "accuracy")

    # --- Storage ---
    # "memory" (default) or "redis". Redis needs JUDGE_REDIS_URL.
    store_backend: str = os.getenv("JUDGE_STORE_BACKEND", "memory")
    redis_url: str = os.getenv("JUDGE_REDIS_URL", "")


settings = Settings()

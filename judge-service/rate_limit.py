"""Rate limiting (submit budget) for the judge service.

Budget is the primary anti-entropy / anti-value-leak guard:
- Each team has a total `submit_budget`.
- Each coder may have a sub-cap.
- Once exhausted, /score returns 429 and does NOT evaluate the prediction
  (so exhausting budget cannot be used to extract more signal).
"""
from __future__ import annotations

from .config import settings
from .store import Store


class BudgetExhausted(Exception):
    def __init__(self, team_id: str, remaining: int) -> None:
        super().__init__(f"team {team_id} budget exhausted (remaining={remaining})")
        self.team_id = team_id
        self.remaining = remaining


def check_and_consume(store: Store, team_id: str, coder_id: str) -> tuple[int, int | None]:
    """Check budget; if OK, consume one and return (team_remaining, coder_remaining).

    Raises BudgetExhausted if either limit is hit.
    """
    team_total = store.get_team_budget(team_id)
    team_used = store.get_team_used(team_id)
    team_remaining = team_total - team_used
    if team_remaining <= 0:
        raise BudgetExhausted(team_id, 0)

    coder_remaining: int | None = None
    if settings.per_coder_cap > 0:
        coder_used = store.get_coder_used(team_id, coder_id)
        coder_remaining = settings.per_coder_cap - coder_used
        if coder_remaining <= 0:
            raise BudgetExhausted(team_id, team_remaining)

    store.increment_used(team_id, coder_id)
    return team_remaining - 1, (coder_remaining - 1 if coder_remaining is not None else None)

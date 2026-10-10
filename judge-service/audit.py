"""Submission audit & label-reversal attack detection.

The value-leak threat we harden against:
  A coder flips a single prediction on a binary task to reverse-engineer
  the true label. E.g., submit pred=0 -> score 1.0 (so label=0), or
  submit pred=0 then pred=1 on the same example and read the score delta.

Detection rules (heuristic, extendable):
  R1  same (team, example) submitted >= max_subs_per_example times -> warn
  R2  prediction flips on the same (team, example) between submissions -> warn
  R3  >= escalate_flip_examples distinct examples probed with flips -> escalate

These are not security boundaries on their own -- combine with budget
limits and holdout isolation. The audit log is architect-visible only.
"""
from __future__ import annotations

from collections import defaultdict

from .config import settings
from .schemas import AuditFlag, AuditReport
from .store import Store


def build_report(store: Store, team_id: str) -> AuditReport:
    entries = store.get_submissions(team_id)
    flags: list[AuditFlag] = []

    # group by example
    by_example: dict[str, list] = defaultdict(list)
    for e in entries:
        by_example[e.example_id].append(e)

    flipped_examples: list[str] = []
    oversubscribed: list[str] = []

    for ex_id, ex_entries in by_example.items():
        # R1: over-subscription
        if len(ex_entries) >= settings.max_subs_per_example:
            oversubscribed.append(ex_id)

        # R2: prediction flip
        preds = [e.prediction for e in ex_entries]
        if len(set(preds)) > 1 and settings.flag_prediction_flip:
            flipped_examples.append(ex_id)

    if oversubscribed:
        flags.append(AuditFlag(
            team_id=team_id,
            rule="oversubscribed_example",
            severity="warn",
            detail=(
                f"{len(oversubscribed)} example(s) submitted >= "
                f"{settings.max_subs_per_example} times"
            ),
            examples=oversubscribed,
        ))

    if flipped_examples:
        flags.append(AuditFlag(
            team_id=team_id,
            rule="prediction_flip",
            severity="warn",
            detail=(
                f"{len(flipped_examples)} example(s) had flipped predictions "
                "(possible label probing)"
            ),
            examples=flipped_examples,
        ))

    # R3: escalation threshold
    if len(flipped_examples) >= settings.escalate_flip_examples:
        flags.append(AuditFlag(
            team_id=team_id,
            rule="escalate_flip_probe",
            severity="escalate",
            detail=(
                f"team probed {len(flipped_examples)} distinct examples with "
                f"flipped predictions (>= {settings.escalate_flip_examples}); "
                "likely reverse-engineering labels"
            ),
            examples=flipped_examples,
        ))

    return AuditReport(
        team_id=team_id,
        total_submissions=len(entries),
        distinct_examples=len(by_example),
        flags=flags,
        entries=entries,
    )

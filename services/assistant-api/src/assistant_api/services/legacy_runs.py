"""Classify pre-existing `nexus_runs` rows from structural evidence only.

The rule set is deliberately narrow. Prompt text is never used: an internal
commute instruction and a user question are both free text, and a failed commute
run may have no `commute_checks.ai_run_id` link even though it is an internal
task. The evidence that does exist is:

* a check row that points at the run (the business record), or
* the server-internal idempotency key marker written by the commute caller.

Anything else came from the only remaining creation path (chat) and gets a
`user_asks` association. A row with contradictory evidence, or no key at all, is
kept and marked `legacy_unknown` instead of being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from assistant_api.services import ai_tasks

INTERNAL_KEY_PREFIXES = ("commute-ai:", "commute:")

# Machine-readable reasons, printed by the migration report.
REASON_CHECK_LINK = "check_link"
REASON_INTERNAL_KEY = "internal_key_marker"
REASON_CHAT = "no_internal_marker"
REASON_UNKNOWN = "unclassifiable"

TRIGGER_BY_CHECK = {"manual": ai_tasks.TRIGGER_MANUAL, "auto": ai_tasks.TRIGGER_SCHEDULE}


@dataclass(frozen=True)
class Classification:
    purpose: str
    reason: str
    check_id: str | None = None


def check_id_from_key(idempotency_key: str | None) -> str | None:
    """Extract the business record id from an internal key, or return None."""

    key = (idempotency_key or "").strip()
    for prefix in INTERNAL_KEY_PREFIXES:
        if key.startswith(prefix):
            candidate = key[len(prefix):].split(":", 1)[0].strip()
            return candidate or None
    return None


def classify(
    *,
    local_run_id: str,
    idempotency_key: str | None,
    purpose: str | None,
    linked_check_ids: set[str],
) -> Classification:
    """Return the purpose and the evidence used for one legacy row."""

    if purpose:
        # Already classified, including a recorded "cannot tell" verdict: a
        # repeated migration must not re-open or re-count it.
        return Classification(purpose, reason_for_existing(purpose))
    if local_run_id in linked_check_ids:
        return Classification(
            ai_tasks.COMMUTE_INTERNAL,
            REASON_CHECK_LINK,
            check_id_from_key(idempotency_key),
        )
    check_id = check_id_from_key(idempotency_key)
    if check_id is not None:
        return Classification(ai_tasks.COMMUTE_INTERNAL, REASON_INTERNAL_KEY, check_id)
    if not (idempotency_key or "").strip():
        return Classification(ai_tasks.LEGACY_UNKNOWN, REASON_UNKNOWN)
    return Classification(ai_tasks.CHAT, REASON_CHAT)


def reason_for_existing(purpose: str) -> str:
    return f"already:{purpose}"


__all__ = [
    "Classification",
    "REASON_CHAT",
    "REASON_CHECK_LINK",
    "REASON_INTERNAL_KEY",
    "REASON_UNKNOWN",
    "TRIGGER_BY_CHECK",
    "check_id_from_key",
    "classify",
]

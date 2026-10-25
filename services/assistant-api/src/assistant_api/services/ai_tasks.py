"""Purpose registry for the shared AI task service.

Every AI run has one purpose. A purpose decides how its input is prepared and how
its result is checked; `run_service` owns the single implementation of "create a
local run, call Nexus, observe the result, and resume it". A new feature adds a
spec here instead of copying the Nexus call path.

Two rules keep this boundary honest:

* the purpose is chosen by the server from a checked business record, never from
  a request body;
* purpose and trigger are different facts. A commute analysis the user tapped is
  still `commute_internal` with trigger `manual`, not chat.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Purposes that exist today. A new feature registers its own; no empty placeholder
# rows or endpoints are created for features that do not exist yet.
CHAT = "chat"
COMMUTE_INTERNAL = "commute_internal"
# Rows the migration could not classify. They are kept, never listed as chat.
LEGACY_UNKNOWN = "legacy_unknown"

TRIGGER_CHAT = "chat"
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULE = "schedule"
VALID_TRIGGERS = frozenset({TRIGGER_CHAT, TRIGGER_MANUAL, TRIGGER_SCHEDULE})


class AiTaskConfigurationError(RuntimeError):
    """Raised when a purpose is used without a registered spec."""


class AiTaskResultRejected(Exception):
    """The model answered, but the purpose's result check refused it."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AiTaskSpec:
    """Prepare one purpose's input and check its result.

    `prepare_input` runs before the local run exists, so it must be pure and
    deterministic: it receives facts the caller already persisted or validated.
    `validate_result` runs after the workflow is terminal and returns the value
    the caller stores; it may raise :class:`AiTaskResultRejected`.
    """

    purpose: str
    input_version: int
    # Chat replays must carry the same text; a business retry reuses its stored
    # input instead of failing, so the input stays fixed for one task.
    strict_input_match: bool
    prepare_input: Callable[[dict[str, Any]], str]
    validate_result: Callable[[str | None, dict[str, Any]], Any]


_SPECS: dict[str, AiTaskSpec] = {}


def register(spec: AiTaskSpec) -> AiTaskSpec:
    """Register (or replace) one purpose; later features add their own spec."""

    _SPECS[spec.purpose] = spec
    return spec


def spec_for(purpose: str) -> AiTaskSpec:
    try:
        return _SPECS[purpose]
    except KeyError as exc:  # pragma: no cover - programming error, never user input
        raise AiTaskConfigurationError(
            f"no AI task spec is registered for purpose {purpose!r}"
        ) from exc


def registered_purposes() -> frozenset[str]:
    return frozenset(_SPECS)


def _chat_input(context: dict[str, Any]) -> str:
    return str(context["text"])


def _chat_result(text: str | None, _context: dict[str, Any]) -> str | None:
    """Chat has no facts to recompute: the answer is the public final text."""

    return text or None


register(
    AiTaskSpec(
        purpose=CHAT,
        input_version=1,
        strict_input_match=True,
        prepare_input=_chat_input,
        validate_result=_chat_result,
    )
)


__all__ = [
    "AiTaskConfigurationError",
    "AiTaskResultRejected",
    "AiTaskSpec",
    "CHAT",
    "COMMUTE_INTERNAL",
    "LEGACY_UNKNOWN",
    "TRIGGER_CHAT",
    "TRIGGER_MANUAL",
    "TRIGGER_SCHEDULE",
    "VALID_TRIGGERS",
    "register",
    "registered_purposes",
    "spec_for",
]

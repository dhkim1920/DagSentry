"""Incident lifecycle contracts."""

from __future__ import annotations

from enum import StrEnum


class IncidentStatus(StrEnum):
    """Lifecycle of an operational Incident."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECOVERED = "RECOVERED"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class IncidentTransitionInitiator(StrEnum):
    """Actor category requesting an Incident state transition."""

    OPERATOR = "OPERATOR"
    SYSTEM = "SYSTEM"
    AI = "AI"


ACTIVE_INCIDENT_STATUSES = frozenset({IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED})
TERMINAL_INCIDENT_STATUSES = frozenset({IncidentStatus.RESOLVED, IncidentStatus.IGNORED})

_ALLOWED_TRANSITIONS = {
    IncidentStatus.OPEN: frozenset(
        {
            IncidentStatus.ACKNOWLEDGED,
            IncidentStatus.RECOVERED,
            IncidentStatus.RESOLVED,
            IncidentStatus.IGNORED,
        }
    ),
    IncidentStatus.ACKNOWLEDGED: frozenset(
        {
            IncidentStatus.RECOVERED,
            IncidentStatus.RESOLVED,
            IncidentStatus.IGNORED,
        }
    ),
    IncidentStatus.RECOVERED: frozenset({IncidentStatus.RESOLVED, IncidentStatus.IGNORED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.IGNORED: frozenset(),
}


def validate_incident_transition(
    current: IncidentStatus,
    target: IncidentStatus,
    initiator: IncidentTransitionInitiator,
    *,
    allow_terminal_override: bool = False,
) -> None:
    """Reject invalid transitions and reserve resolution for an operator."""
    if current == target:
        return
    if target == IncidentStatus.RESOLVED and initiator != IncidentTransitionInitiator.OPERATOR:
        raise ValueError("only an operator may resolve an Incident")
    if target == IncidentStatus.ACKNOWLEDGED and initiator != IncidentTransitionInitiator.OPERATOR:
        raise ValueError("only an operator may acknowledge an Incident")
    if target == IncidentStatus.IGNORED and initiator != IncidentTransitionInitiator.OPERATOR:
        raise ValueError("only an operator may ignore an Incident")
    if target == IncidentStatus.RECOVERED and initiator == IncidentTransitionInitiator.AI:
        raise ValueError("AI may not change Incident state")
    if target in _ALLOWED_TRANSITIONS[current]:
        return
    if (
        allow_terminal_override
        and initiator == IncidentTransitionInitiator.OPERATOR
        and current in TERMINAL_INCIDENT_STATUSES
        and target
        in {
            IncidentStatus.OPEN,
            IncidentStatus.ACKNOWLEDGED,
            IncidentStatus.RESOLVED,
            IncidentStatus.IGNORED,
        }
    ):
        return
    raise ValueError(f"invalid Incident transition: {current} -> {target}")

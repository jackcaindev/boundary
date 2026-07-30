"""Task 3 Boundary-owned operational lifecycle rules."""

from __future__ import annotations


TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "invalid",
}

LEGAL_TRANSITIONS = {
    "accepted": {
        "running",
        "failed",
        "cancelled",
        "timed_out",
        "invalid",
    },
    "running": {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "invalid",
    },
}


def is_legal_transition(current: str, target: str) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, set())


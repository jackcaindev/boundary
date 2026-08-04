"""Validated Task 8 runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from boundary.sut.contract_v1 import (
    MAX_EVENT_BYTES,
    MAX_TARGET_EVENTS,
    MAX_TARGET_EVENT_BYTES,
)


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BoundarySettings:
    sut_base_url: str
    boundary_internal_base_url: str
    run_deadline_ms: int = 30_000
    cancellation_grace_ms: int = 2_000
    target_poll_interval_ms: int = 100
    tool_client_timeout_ms: int = 500
    injected_hold_ms: int = 1_000
    max_event_bytes: int = MAX_EVENT_BYTES
    max_target_events: int = MAX_TARGET_EVENTS
    max_target_event_bytes: int = MAX_TARGET_EVENT_BYTES
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.sut_base_url.startswith(("http://", "https://")):
            raise ConfigurationError("SUT_BASE_URL must be an HTTP URL")
        if not self.boundary_internal_base_url.startswith(("http://", "https://")):
            raise ConfigurationError(
                "BOUNDARY_INTERNAL_BASE_URL must be an HTTP URL"
            )
        exact = {
            "RUN_DEADLINE_MS": (self.run_deadline_ms, 30_000),
            "CANCELLATION_GRACE_MS": (self.cancellation_grace_ms, 2_000),
            "TARGET_POLL_INTERVAL_MS": (self.target_poll_interval_ms, 100),
            "TOOL_CLIENT_TIMEOUT_MS": (self.tool_client_timeout_ms, 500),
            "INJECTED_HOLD_MS": (self.injected_hold_ms, 1_000),
            "MAX_EVENT_BYTES": (self.max_event_bytes, MAX_EVENT_BYTES),
            "MAX_TARGET_EVENTS": (self.max_target_events, MAX_TARGET_EVENTS),
            "MAX_TARGET_EVENT_BYTES": (
                self.max_target_event_bytes,
                MAX_TARGET_EVENT_BYTES,
            ),
        }
        mismatches = [name for name, (actual, expected) in exact.items() if actual != expected]
        if mismatches:
            raise ConfigurationError(
                "configuration conflicts with the immutable Phase 1 definition: "
                + ", ".join(mismatches)
            )
        if self.log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError("LOG_LEVEL is invalid")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "BoundarySettings":
        values = os.environ if environment is None else environment

        def integer(name: str, default: int) -> int:
            try:
                return int(values.get(name, str(default)))
            except ValueError:
                raise ConfigurationError(f"{name} must be an integer") from None

        return cls(
            sut_base_url=values.get("SUT_BASE_URL", "http://sample-agent:8001"),
            boundary_internal_base_url=values.get(
                "BOUNDARY_INTERNAL_BASE_URL", "http://boundary:8000"
            ),
            run_deadline_ms=integer("RUN_DEADLINE_MS", 30_000),
            cancellation_grace_ms=integer("CANCELLATION_GRACE_MS", 2_000),
            target_poll_interval_ms=integer("TARGET_POLL_INTERVAL_MS", 100),
            tool_client_timeout_ms=integer("TOOL_CLIENT_TIMEOUT_MS", 500),
            injected_hold_ms=integer("INJECTED_HOLD_MS", 1_000),
            max_event_bytes=integer("MAX_EVENT_BYTES", MAX_EVENT_BYTES),
            max_target_events=integer("MAX_TARGET_EVENTS", MAX_TARGET_EVENTS),
            max_target_event_bytes=integer(
                "MAX_TARGET_EVENT_BYTES", MAX_TARGET_EVENT_BYTES
            ),
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
        )

from __future__ import annotations

import asyncio

import pytest

from boundary.injection.timeout import AsyncioMonotonicWaiter


class EarlyWakeClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def monotonic_ns(self) -> int:
        return self.now_ns


@pytest.mark.asyncio
async def test_wait_until_rechecks_clock_after_early_wakeup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = EarlyWakeClock()
    requested_delays: list[float] = []

    async def wake_early(delay: float) -> None:
        requested_delays.append(delay)
        clock.now_ns = 999 if len(requested_delays) == 1 else 1_000

    monkeypatch.setattr(asyncio, "sleep", wake_early)

    await AsyncioMonotonicWaiter().wait_until(1_000, clock)

    assert clock.monotonic_ns() >= 1_000
    assert requested_delays == [1_000 / 1_000_000_000, 1 / 1_000_000_000]

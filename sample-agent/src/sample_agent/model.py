"""Deterministic fake-model port for the Task 3 sample workflow."""

from __future__ import annotations

from dataclasses import dataclass


EXPECTED_TOOL = "boundary.phase1.lookup"


@dataclass(frozen=True, slots=True)
class ToolSelection:
    tool: str
    arguments: dict[str, str]


class DeterministicFakeModel:
    """Select the one reviewed tool without network or model variability."""

    async def select_tool(self, query: str) -> ToolSelection:
        return ToolSelection(
            tool=EXPECTED_TOOL,
            arguments={"query": query},
        )


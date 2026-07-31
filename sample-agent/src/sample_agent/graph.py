"""Minimal LangGraph initial selection; recovery remains ordinary code."""

from __future__ import annotations

from typing import Protocol, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from sample_agent.model import EXPECTED_TOOL, DeterministicFakeModel
from sample_agent.tool_contract_v1 import LookupResponse


class ControlState(TypedDict, total=False):
    query: str
    run_id: UUID
    trace_id: UUID
    fault_id: UUID | None
    tool_endpoint: str
    tool_capability: str
    selected_tool: str
    selected_arguments: dict[str, str]


class ToolLookupPort(Protocol):
    async def lookup(
        self,
        *,
        endpoint: str,
        capability: str,
        run_id: UUID,
        trace_id: UUID,
        fault_id: UUID | None,
        arguments: dict[str, str],
        tool_call_id: UUID | None = None,
    ) -> LookupResponse: ...


def build_control_graph(
    model: DeterministicFakeModel | None = None,
    tool_client: ToolLookupPort | None = None,
):
    """Compile only the model-controlled initial tool selection."""
    fake_model = model or DeterministicFakeModel()
    del tool_client

    async def select_tool(state: ControlState) -> ControlState:
        selection = await fake_model.select_tool(state["query"])
        return {
            "selected_tool": selection.tool,
            "selected_arguments": selection.arguments,
        }

    async def validate_selection(state: ControlState) -> ControlState:
        if state["selected_tool"] != EXPECTED_TOOL:
            raise ValueError("model selected an unsupported tool")
        return {}

    graph = StateGraph(ControlState)
    graph.add_node("select_tool", select_tool)
    graph.add_node("validate_selection", validate_selection)
    graph.add_edge(START, "select_tool")
    graph.add_edge("select_tool", "validate_selection")
    graph.add_edge("validate_selection", END)
    return graph.compile()

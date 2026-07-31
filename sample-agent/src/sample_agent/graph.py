"""Minimal LangGraph selection followed by the real Boundary tool call."""

from __future__ import annotations

from typing import Protocol, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from sample_agent.model import EXPECTED_TOOL, DeterministicFakeModel
from sample_agent.tool_client import Phase1ToolClient
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
    output: str


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
    """Compile selection plus one real no-fault lookup attempt."""
    fake_model = model or DeterministicFakeModel()
    lookup_client = tool_client or Phase1ToolClient()

    async def select_tool(state: ControlState) -> ControlState:
        selection = await fake_model.select_tool(state["query"])
        return {
            "selected_tool": selection.tool,
            "selected_arguments": selection.arguments,
        }

    async def complete_control(state: ControlState) -> ControlState:
        if state["selected_tool"] != EXPECTED_TOOL:
            raise ValueError("model selected an unsupported tool")
        response = await lookup_client.lookup(
            endpoint=state["tool_endpoint"],
            capability=state["tool_capability"],
            run_id=state["run_id"],
            trace_id=state["trace_id"],
            fault_id=state["fault_id"],
            arguments=state["selected_arguments"],
        )
        return {"output": response.result.value}

    graph = StateGraph(ControlState)
    graph.add_node("select_tool", select_tool)
    graph.add_node("complete_control", complete_control)
    graph.add_edge(START, "select_tool")
    graph.add_edge("select_tool", "complete_control")
    graph.add_edge("complete_control", END)
    return graph.compile()

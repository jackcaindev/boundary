"""Minimal LangGraph workflow used by the normal vulnerable control."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from sample_agent.model import DeterministicFakeModel


class ControlState(TypedDict, total=False):
    query: str
    selected_tool: str
    selected_arguments: dict[str, str]
    output: str


def build_control_graph(
    model: DeterministicFakeModel | None = None,
):
    """Compile the two-node fake selection and normal completion path."""
    fake_model = model or DeterministicFakeModel()

    async def select_tool(state: ControlState) -> ControlState:
        selection = await fake_model.select_tool(state["query"])
        return {
            "selected_tool": selection.tool,
            "selected_arguments": selection.arguments,
        }

    async def complete_control(state: ControlState) -> ControlState:
        del state
        return {"output": "control-ok"}

    graph = StateGraph(ControlState)
    graph.add_node("select_tool", select_tool)
    graph.add_node("complete_control", complete_control)
    graph.add_edge(START, "select_tool")
    graph.add_edge("select_tool", "complete_control")
    graph.add_edge("complete_control", END)
    return graph.compile()


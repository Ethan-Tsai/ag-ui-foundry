"""State management for PowerBI QA Assistant."""

from __future__ import annotations

from agent_framework import tool
from pydantic import BaseModel, Field


class DataPoint(BaseModel):
    """A specific data metric or finding."""

    metric_name: str = Field(..., description="Name of the metric or data point")
    value: str = Field(..., description="Value of the metric")
    context: str = Field(..., description="Additional context or formatting details")


class PowerBIState(BaseModel):
    """The current state of the PowerBI QA analysis."""

    dashboard_context: str = Field(..., description="The dashboard or dataset currently in context")
    current_insights: str = Field(..., description="Key insights or analysis from the latest query")
    data_points: list[DataPoint] = Field(..., description="Key metrics identified during analysis")


STATE_SCHEMA = PowerBIState

PREDICT_STATE_CONFIG: dict[str, dict[str, str]] = {
    "dashboard_context": {
        "tool": "update_dashboard_context",
        "tool_argument": "dashboard_context",
    },
    "current_insights": {
        "tool": "update_insights",
        "tool_argument": "current_insights",
    },
    "data_points": {
        "tool": "add_data_points",
        "tool_argument": "data_points",
    },
}

INITIAL_POWERBI_STATE: dict[str, object] = {
    "dashboard_context": "",
    "current_insights": "",
    "data_points": [],
}


@tool
def update_dashboard_context(dashboard_context: str) -> str:
    """Update the current dashboard context.

    Args:
        dashboard_context: The name or context of the dataset/dashboard.

    Returns:
        Confirmation that the dashboard context was updated.
    """
    return "Dashboard context updated."


@tool
def update_insights(current_insights: str) -> str:
    """Update the current analysis or insights based on user queries.

    Args:
        current_insights: The updated insights summary.

    Returns:
        Confirmation that insights were updated.
    """
    return "Insights updated."


@tool
def add_data_points(data_points: list[DataPoint]) -> str:
    """Update the extracted key data points.

    You MUST provide the complete list, including all existing items you want to keep,
    plus any newly added or modified data point.

    Args:
        data_points: The complete, updated data points list.

    Returns:
        Confirmation that data points were updated.
    """
    return "Data points updated."


def _rebuild_tool_models() -> None:
    tool_models = (
        update_dashboard_context,
        update_insights,
        add_data_points,
    )
    type_namespace = globals()
    for tool_fn in tool_models:
        input_model = getattr(tool_fn, "input_model", None)
        if input_model is not None:
            input_model.model_rebuild(_types_namespace=type_namespace)


_rebuild_tool_models()

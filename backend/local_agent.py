# Copyright (c) Microsoft. All rights reserved.

"""Local agent demonstrating shared state management."""

from __future__ import annotations

from typing import Any

from agent_framework import Agent, SupportsChatGetResponse
from agent_framework.ag_ui import AgentFrameworkAgent
from backend.agent_tool import build_foundry_qa_tool
from backend.state import (
    PREDICT_STATE_CONFIG,
    STATE_SCHEMA,
    add_data_points,
    update_dashboard_context,
    update_insights,
)


_AGENT_INSTRUCTIONS = """
    You are helping users analyze PowerBI data using shared state.

    Rules:
    1. You receive current state in system context.
    2. Use the smallest matching tool:
       - update_dashboard_context(dashboard_context)
       - update_insights(current_insights)
       - add_data_points(data_points)
    3. Preserve existing data. Do not remove data points unless the user explicitly asks.
    4. For add_data_points, send the full updated data points list.
    5. Never call multi_tool_use.parallel. Make tool calls sequentially.
    6. For multi-field updates, prefer sequential tool calls.
    7. After tool calls, reply briefly (1-2 sentences).
    8. Use ask_agent(question, context) to talk with another agent for information.
       Pass the current project state as context so the agent can give relevant answers.
"""

def _build_tools(client: SupportsChatGetResponse[Any]) -> list[Any]:
    tools: list[Any] = [
        update_dashboard_context,
        update_insights,
        add_data_points,
        build_foundry_qa_tool(),
    ]

    return tools


def local_agent(client: SupportsChatGetResponse[Any]) -> AgentFrameworkAgent:
    """Create a local agent with streaming state updates.

    Args:
        client: The chat client to use for the agent

    Returns:
        A configured AgentFrameworkAgent instance with state management
    """
    agent = Agent(
        name="local_agent",
        instructions=_AGENT_INSTRUCTIONS,
        client=client,
        tools=_build_tools(client),
    )

    return AgentFrameworkAgent(
        agent=agent,
        name="local_agent",
        description="PowerBI QA Assistant",
        state_schema=STATE_SCHEMA,
        predict_state_config=PREDICT_STATE_CONFIG,
        require_confirmation=False,
    )

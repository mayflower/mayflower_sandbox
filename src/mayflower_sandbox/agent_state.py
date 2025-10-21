"""
Custom agent state schema for mayflower-sandbox.

Extends LangGraph's AgentState to track created files.
"""

from operator import add
from typing import Annotated

from langchain.agents import AgentState
from typing_extensions import NotRequired


class SandboxAgentState(AgentState):
    """
    Extended agent state that tracks files created during execution.

    Attributes:
        messages: Required list of messages (inherited from AgentState)
        created_files: Optional list of file paths created by execute_python tool.
                      Uses add reducer to accumulate files from multiple tools.
    """

    created_files: NotRequired[Annotated[list[str], add]]

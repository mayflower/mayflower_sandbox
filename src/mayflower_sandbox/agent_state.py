"""
Custom agent state schema for mayflower-sandbox.

Extends LangGraph's AgentState to track created files.
"""

from operator import add
from typing import Annotated

from typing_extensions import TypedDict


class SandboxAgentState(TypedDict, total=False):
    """
    Extended agent state that tracks files created during execution.

    Attributes:
        messages: List of messages (from LangGraph)
        created_files: Optional list of file paths created by execute_python tool.
                      Uses add reducer to accumulate files from multiple tools.
    """

    messages: Annotated[list, add]
    created_files: Annotated[list[str], add]

"""
Tool factory for creating Mayflower Sandbox tools.
"""

import asyncpg

from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.tools.execute import ExecutePythonTool
from mayflower_sandbox.tools.file_delete import FileDeleteTool
from mayflower_sandbox.tools.file_list import FileListTool
from mayflower_sandbox.tools.file_read import FileReadTool
from mayflower_sandbox.tools.file_write import FileWriteTool


def create_sandbox_tools(
    db_pool: asyncpg.Pool,
    thread_id: str,
    include_tools: list[str] | None = None,
) -> list[SandboxTool]:
    """
    Create a set of sandbox tools for LangGraph.

    Args:
        db_pool: PostgreSQL connection pool
        thread_id: Thread ID for session isolation
        include_tools: List of tool names to include (default: all tools)
                      Options: "execute_python", "read_file", "write_file",
                              "list_files", "delete_file"

    Returns:
        List of configured SandboxTool instances

    Example:
        ```python
        import asyncpg
        from mayflower_sandbox.tools import create_sandbox_tools

        db_pool = await asyncpg.create_pool(...)

        # Create all tools
        tools = create_sandbox_tools(db_pool, "user_123")

        # Create only specific tools
        tools = create_sandbox_tools(
            db_pool,
            "user_123",
            include_tools=["execute_python", "read_file", "write_file"]
        )

        # Use with LangGraph
        from langgraph.prebuilt import create_react_agent

        agent = create_react_agent(llm, tools)
        ```
    """
    all_tools = {
        "execute_python": ExecutePythonTool,
        "read_file": FileReadTool,
        "write_file": FileWriteTool,
        "list_files": FileListTool,
        "delete_file": FileDeleteTool,
    }

    if include_tools is None:
        include_tools = list(all_tools.keys())

    tools = []
    for tool_name in include_tools:
        if tool_name not in all_tools:
            raise ValueError(f"Unknown tool: {tool_name}. Valid tools: {list(all_tools.keys())}")

        tool_class = all_tools[tool_name]
        tools.append(tool_class(db_pool=db_pool, thread_id=thread_id))

    return tools

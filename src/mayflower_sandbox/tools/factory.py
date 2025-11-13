"""
Tool factory for creating Mayflower Sandbox tools.
"""

import asyncpg

from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.tools.execute import ExecutePythonTool
from mayflower_sandbox.tools.execute_code import ExecuteCodeTool
from mayflower_sandbox.tools.file_delete import FileDeleteTool
from mayflower_sandbox.tools.file_edit import FileEditTool
from mayflower_sandbox.tools.file_glob import FileGlobTool
from mayflower_sandbox.tools.file_grep import FileGrepTool
from mayflower_sandbox.tools.file_list import FileListTool
from mayflower_sandbox.tools.file_read import FileReadTool
from mayflower_sandbox.tools.file_write import FileWriteTool
from mayflower_sandbox.tools.run_file import RunPythonFileTool
from mayflower_sandbox.tools_skills_mcp import MCPBindHttpTool, SkillInstallTool


def create_sandbox_tools(
    db_pool: asyncpg.Pool,
    thread_id: str | None = None,
    include_tools: list[str] | None = None,
) -> list[SandboxTool]:
    """
    Create a set of sandbox tools for LangGraph.

    Args:
        db_pool: PostgreSQL connection pool
        thread_id: Thread ID for session isolation. If None, tools will read
                  thread_id from callback context at runtime (recommended for LangGraph).
                  If provided, tools will use this thread_id for all operations.
        include_tools: List of tool names to include (default: all tools)
                      Options: "python_run", "python_run_file", "python_run_prepared",
                              "file_read", "file_write", "file_list", "file_delete",
                              "file_edit", "file_glob", "file_grep"

    Returns:
        List of configured SandboxTool instances

    Example:
        ```python
        import asyncpg
        from mayflower_sandbox.tools import create_sandbox_tools

        db_pool = await asyncpg.create_pool(...)

        # Create context-aware tools (recommended for LangGraph)
        tools = create_sandbox_tools(db_pool, thread_id=None)

        # Create tools with fixed thread_id
        tools = create_sandbox_tools(db_pool, thread_id="user_123")

        # Create only specific tools
        tools = create_sandbox_tools(
            db_pool,
            thread_id=None,
            include_tools=["python_run", "file_read", "file_write"]
        )

        # Use with LangGraph
        from langgraph.prebuilt import create_react_agent

        agent = create_react_agent(llm, tools)
        ```
    """
    all_tools = {
        "python_run": ExecutePythonTool,
        "python_run_file": RunPythonFileTool,
        "python_run_prepared": ExecuteCodeTool,
        "file_read": FileReadTool,
        "file_write": FileWriteTool,
        "file_list": FileListTool,
        "file_delete": FileDeleteTool,
        "file_edit": FileEditTool,
        "file_glob": FileGlobTool,
        "file_grep": FileGrepTool,
        "skill_install": SkillInstallTool,
        "mcp_bind_http": MCPBindHttpTool,
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

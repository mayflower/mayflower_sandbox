"""
ExecuteCodeTool - Execute code from graph state.
"""

import logging
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.sandbox_executor import SandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)


class ExecuteCodeInput(BaseModel):
    """Input schema for ExecuteCodeTool."""

    file_path: str = Field(
        description="Path where code will be saved (e.g., /tmp/visualization.py)"
    )
    description: str = Field(
        description="Brief description of what the code does"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class ExecuteCodeTool(SandboxTool):
    """
    Tool for executing Python code from graph state.

    This is a single-tool solution for handling large code blocks that cause
    tool call parameter serialization issues in AG-UI/LangGraph.

    Workflow:
    1. LLM generates code and stores in graph state (pending_code field)
    2. LLM calls execute_code(file_path, description)
    3. Tool extracts code from state, writes to VFS, executes

    This avoids passing code through tool call parameters entirely.
    """

    name: str = "execute_code"
    description: str = """Execute Python code from graph state.

Use this for complex Python code (20+ lines, subplots, multi-step analysis).

Before calling this tool, generate the complete Python code and it will be
automatically stored in graph state. Then call this tool to execute it.

Args:
    file_path: Where to save the code (e.g., /tmp/visualization.py)
    description: Brief description of what the code does

Returns:
    Execution result (output, files created, errors)
"""
    args_schema: type[BaseModel] = ExecuteCodeInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        description: str,
        _state: dict,
        tool_call_id: str = "",
        _config: dict | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute code from graph state."""
        # Extract thread_id from config (passed by custom_tool_node)
        thread_id = None
        if _config:
            thread_id = _config.get("configurable", {}).get("thread_id")
        if not thread_id:
            thread_id = self._get_thread_id(run_manager)

        # Access code from graph state
        code = _state.get("pending_code", "")

        if not code:
            logger.error("execute_code: No code found in state")
            return (
                "Error: No code found in graph state. "
                "Generate Python code first before calling this tool."
            )

        logger.info(f"execute_code: Found {len(code)} chars of code in state")

        # Write code to VFS
        vfs = VirtualFilesystem(self.db_pool, thread_id)
        try:
            await vfs.write_file(file_path, code.encode("utf-8"))
            logger.info(f"execute_code: Wrote {len(code)} bytes to {file_path}")
        except Exception as e:
            logger.error(f"execute_code: Failed to write file: {e}")
            return f"Error writing code to file: {e}"

        # Execute the code
        executor = SandboxExecutor(self.db_pool, thread_id)
        try:
            result = await executor.execute_from_file(file_path, timeout=300)
            logger.info("execute_code: Execution completed")

            # Clear pending_code from state after successful execution
            if tool_call_id:
                try:
                    from langchain_core.messages import ToolMessage
                    from langgraph.types import Command

                    state_update = {
                        "pending_code": "",  # Clear after execution
                        "messages": [ToolMessage(content=result, tool_call_id=tool_call_id)],
                    }

                    return Command(update=state_update, resume=result)  # type: ignore[return-value]
                except ImportError:
                    pass

            return result
        except Exception as e:
            logger.error(f"execute_code: Execution failed: {e}")
            return f"Error executing code: {e}"

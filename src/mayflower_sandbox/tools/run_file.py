"""
RunPythonFileTool - Execute Python files from sandbox VFS.
"""

import logging
import os
from typing import Annotated, Any

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.sandbox_executor import ExecutionResult, SandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.tools.execute import add_error_to_history

logger = logging.getLogger(__name__)

# Image extensions for inline display
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


def _format_error_analysis(analysis: dict[str, str]) -> str:
    """Format LLM error analysis for display."""
    parts = ["⚠️ **Error Analysis:**"]
    if analysis.get("explanation"):
        parts.append(f"**What happened:** {analysis['explanation']}")
    if analysis.get("recommendation"):
        parts.append(f"**Try this instead:** {analysis['recommendation']}")
    return "\n".join(parts)


def _format_created_files(created_files: list[str]) -> list[str]:
    """Format created files for display, separating images from other files."""
    image_files = []
    other_files = []

    for path in created_files:
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_EXTENSIONS:
            image_files.append(path)
        else:
            other_files.append(path)

    parts = []
    if image_files:
        parts.append("\n\n".join(f"![Generated image]({path})" for path in image_files))
    if other_files:
        parts.append("\n".join(f"- [{os.path.basename(path)}]({path})" for path in other_files))

    return parts


def _build_response_message(
    file_path: str,
    result: ExecutionResult,
    analysis: dict[str, str],
) -> str:
    """Build the complete response message."""
    parts = [f"**Executed:** {file_path}"]

    if not result.success and analysis:
        parts.append(_format_error_analysis(analysis))

    if result.stdout:
        parts.append(result.stdout.strip())

    if result.stderr:
        parts.append(f"Error:\n{result.stderr}")

    if result.created_files:
        parts.extend(_format_created_files(result.created_files))

    if result.success:
        return "\n\n".join(parts) if parts else f"Executed {file_path} successfully (no output)"
    return "\n\n".join(parts) if parts else "Execution failed"


def _try_langgraph_command(
    message: str,
    tool_call_id: str,
    created_files: list[str],
) -> Any | None:
    """Try to return a LangGraph Command if available."""
    try:
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        state_update = {
            "created_files": created_files,
            "messages": [ToolMessage(content=message, tool_call_id=tool_call_id)],
        }
        return Command(update=state_update, resume=message)
    except ImportError:
        return None


class RunPythonFileInput(BaseModel):
    """Input schema for RunPythonFileTool."""

    file_path: str = Field(
        description="Path to Python file to execute (e.g., /tmp/script.py, /data/analysis.py)"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class RunPythonFileTool(SandboxTool):
    """
    Tool for executing Python files stored in the sandbox VFS.

    Reads the Python file from PostgreSQL storage and executes it in Pyodide.
    Useful for running previously created or uploaded scripts.
    """

    name: str = "python_run_file"
    description: str = """Execute a Python file from the sandbox filesystem.

Use this to run Python scripts that have been previously created or uploaded.
The file is read from PostgreSQL storage and executed in the Pyodide sandbox.

Args:
    file_path: Path to the Python file to execute (e.g., /tmp/script.py, /data/analysis.py)

Returns:
    Execution output (stdout, stderr, and any created files)

Example workflow:
1. Create a script using write_file or execute_python
2. Run it later with run_python_file

This is useful for:
- Running multi-step analysis scripts
- Executing uploaded Python files
- Re-running previously created scripts
- Organizing code into reusable modules
"""
    args_schema: type[BaseModel] = RunPythonFileInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        tool_call_id: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute Python file from VFS."""
        thread_id = self._get_thread_id(run_manager)
        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            code = await self._read_python_file(vfs, file_path)
            result = await self._execute_code(thread_id, code)
            analysis = await self._get_error_analysis(thread_id, code, result)

            message = _build_response_message(file_path, result, analysis)

            if result.created_files and tool_call_id:
                command = _try_langgraph_command(message, tool_call_id, result.created_files)
                if command is not None:
                    return command  # type: ignore[return-value]

            return message

        except FileNotFoundError:
            return f"Error: Python file not found: {file_path}\n\nUse list_files to see available files."
        except UnicodeDecodeError as e:
            return f"Error: File {file_path} is not a valid text file: {e}"
        except Exception as e:
            return f"Error running Python file: {e}"

    async def _read_python_file(self, vfs: VirtualFilesystem, file_path: str) -> str:
        """Read Python file content from VFS."""
        file_info = await vfs.read_file(file_path)
        code = file_info["content"].decode("utf-8", errors="replace")

        if not file_path.endswith(".py"):
            logger.warning(f"File {file_path} does not have .py extension")

        return code

    async def _execute_code(self, thread_id: str, code: str) -> ExecutionResult:
        """Execute code in sandbox."""
        executor = SandboxExecutor(
            self.db_pool, thread_id, allow_net=True, timeout_seconds=60.0, stateful=True
        )
        return await executor.execute(code)

    async def _get_error_analysis(
        self,
        thread_id: str,
        code: str,
        result: ExecutionResult,
    ) -> dict[str, str]:
        """Get LLM error analysis if execution failed."""
        if not result.success and result.stderr:
            return await add_error_to_history(thread_id, code, result.stderr)
        return {}

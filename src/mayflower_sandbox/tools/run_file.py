"""
RunPythonFileTool - Execute Python files from sandbox VFS.
"""

import logging
import os
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.sandbox_executor import SandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.tools.execute import add_error_to_history

logger = logging.getLogger(__name__)


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

        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            # Read the Python file from VFS
            file_info = await vfs.read_file(file_path)
            code = file_info["content"].decode("utf-8", errors="replace")

            # Validate it's a Python file
            if not file_path.endswith(".py"):
                logger.warning(f"File {file_path} does not have .py extension")

            # Create executor with network access for micropip
            executor = SandboxExecutor(self.db_pool, thread_id, allow_net=True, timeout_seconds=60.0)

            # Execute the code
            result = await executor.execute(code)

            # Track errors with LLM analysis (reuse from execute tool)
            analysis = {}
            if not result.success and result.stderr:
                analysis = await add_error_to_history(thread_id, code, result.stderr)

            # Format response - keep it clean and user-friendly
            response_parts = []

            # Show which file was executed
            response_parts.append(f"**Executed:** {file_path}")

            # Show LLM analysis if this execution failed
            if not result.success and analysis:
                warning_parts = ["⚠️ **Error Analysis:**"]

                if analysis.get("explanation"):
                    warning_parts.append(f"**What happened:** {analysis['explanation']}")

                if analysis.get("recommendation"):
                    warning_parts.append(f"**Try this instead:** {analysis['recommendation']}")

                response_parts.append("\n".join(warning_parts))

            # Show stdout without "Output:" label
            if result.stdout:
                response_parts.append(result.stdout.strip())

            if result.stderr:
                response_parts.append(f"Error:\n{result.stderr}")

            # Show created/modified files with inline images
            if result.created_files:
                # Separate image files from other files
                image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
                image_files = []
                other_files = []

                for path in result.created_files:
                    ext = os.path.splitext(path)[1].lower()
                    if ext in image_extensions:
                        image_files.append(path)
                    else:
                        other_files.append(path)

                # Show image files inline
                if image_files:
                    images_md = "\n\n".join(f"![Generated image]({path})" for path in image_files)
                    response_parts.append(images_md)

                # Show other files as markdown links
                if other_files:
                    files_md = "\n".join(
                        f"- [{os.path.basename(path)}]({path})" for path in other_files
                    )
                    response_parts.append(files_md)

            # Build response message
            if result.success:
                message = (
                    "\n\n".join(response_parts)
                    if response_parts
                    else f"Executed {file_path} successfully (no output)"
                )
            else:
                message = "\n\n".join(response_parts) if response_parts else "Execution failed"

            # Update agent state with created files if using LangGraph and tool_call_id provided
            if result.created_files and tool_call_id:
                try:
                    from langchain_core.messages import ToolMessage
                    from langgraph.types import Command

                    # Build state update with both custom field and ToolMessage
                    state_update = {
                        "created_files": result.created_files,
                        "messages": [ToolMessage(content=message, tool_call_id=tool_call_id)],
                    }

                    return Command(update=state_update, resume=message)  # type: ignore[return-value]
                except ImportError:
                    pass

            return message

        except FileNotFoundError:
            return f"Error: Python file not found: {file_path}\n\nUse list_files to see available files."
        except UnicodeDecodeError as e:
            return f"Error: File {file_path} is not a valid text file: {e}"
        except Exception as e:
            return f"Error running Python file: {e}"

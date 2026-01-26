"""
ExecuteCodeTool - Execute code from graph state.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.sandbox_executor import ExecutionResult, SandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)

# Image extensions for inline display
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


def _format_execution_result(exec_result: ExecutionResult) -> str:
    """Format execution result for display."""
    parts = []

    if exec_result.stdout:
        parts.append(exec_result.stdout.strip())

    if exec_result.created_files:
        parts.extend(_format_created_files(exec_result.created_files))

    return "\n\n".join(parts) if parts else "Code executed successfully"


def _format_created_files(created_files: list[str]) -> list[str]:
    """Format created files for display, separating images from other files."""
    image_files = [p for p in created_files if os.path.splitext(p)[1].lower() in _IMAGE_EXTENSIONS]
    other_files = [
        p for p in created_files if os.path.splitext(p)[1].lower() not in _IMAGE_EXTENSIONS
    ]

    parts = []
    if image_files:
        parts.append("\n\n".join(f"![Generated image]({path})" for path in image_files))
    if other_files:
        parts.append("\n".join(f"- [{os.path.basename(path)}]({path})" for path in other_files))

    return parts


class AguiEventEmitter:
    """Helper class for emitting AG-UI streaming events."""

    def __init__(self, tool_call_id: str):
        self.tool_call_id = tool_call_id
        self._writer: Callable[[Any], None] | None = None

    def _get_writer(self):
        """Get the stream writer, caching for reuse."""
        if self._writer is None:
            try:
                from langgraph.config import get_stream_writer

                self._writer = get_stream_writer()
            except Exception:
                # Stream writer not available (not in langgraph context)
                self._writer = None
        return self._writer

    def emit_start(self, file_path: str, description: str, code_size: int) -> None:
        """Emit execution start events."""
        writer = self._get_writer()
        if not writer:
            return

        try:
            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallStart",
                        "toolCallId": self.tool_call_id,
                        "toolCallName": "python_execution",
                    }
                }
            )
            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallArgs",
                        "toolCallId": self.tool_call_id,
                        "delta": json.dumps(
                            {
                                "file_path": file_path,
                                "description": description,
                                "code_size": code_size,
                                "status": "preparing",
                            }
                        ),
                    }
                }
            )
            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallArgs",
                        "toolCallId": self.tool_call_id,
                        "delta": json.dumps(
                            {"status": "executing", "message": f"Executing {description}..."}
                        ),
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to emit start events: {e}")

    def emit_success(self, description: str, file_path: str, exec_result: ExecutionResult) -> None:
        """Emit execution success events."""
        writer = self._get_writer()
        if not writer:
            return

        try:
            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallArgs",
                        "toolCallId": self.tool_call_id,
                        "delta": json.dumps(
                            {
                                "status": "completed",
                                "message": "Execution completed successfully",
                                "files_created": len(exec_result.created_files)
                                if exec_result.created_files
                                else 0,
                            }
                        ),
                    }
                }
            )

            result_content: dict[str, Any] = {
                "description": description,
                "file_path": file_path,
                "success": True,
            }
            if exec_result.stdout:
                result_content["stdout"] = exec_result.stdout[:1000]
            if exec_result.created_files:
                result_content["files"] = exec_result.created_files

            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallResult",
                        "toolCallId": self.tool_call_id,
                        "messageId": str(uuid4()),
                        "role": "tool",
                        "content": result_content,
                    }
                }
            )
            writer({"aguiTool": {"type": "ToolCallEnd", "toolCallId": self.tool_call_id}})
        except Exception as e:
            logger.error(f"Failed to emit success events: {e}")

    def emit_error(self, description: str, file_path: str, error: str) -> None:
        """Emit execution error events."""
        writer = self._get_writer()
        if not writer:
            return

        try:
            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallArgs",
                        "toolCallId": self.tool_call_id,
                        "delta": json.dumps(
                            {"status": "error", "message": f"Execution failed: {error}"}
                        ),
                    }
                }
            )
            writer(
                {
                    "aguiTool": {
                        "type": "ToolCallResult",
                        "toolCallId": self.tool_call_id,
                        "messageId": str(uuid4()),
                        "role": "tool",
                        "content": {
                            "description": description,
                            "file_path": file_path,
                            "success": False,
                            "error": error,
                        },
                    }
                }
            )
            writer({"aguiTool": {"type": "ToolCallEnd", "toolCallId": self.tool_call_id}})
        except Exception:
            # Streaming not available - emit silently fails
            logger.debug("Failed to emit error event - streaming not available")


class ExecuteCodeInput(BaseModel):
    """Input schema for ExecuteCodeTool."""

    file_path: str = Field(
        default="/tmp/script.py",  # nosec B108 - sandbox VFS path, not host filesystem
        description="Path where code will be saved (e.g., /tmp/visualization.py)",
    )
    description: str = Field(
        default="Python script", description="Brief description of what the code does"
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

    name: str = "python_run_prepared"
    description: str = """Execute Python code from graph state.

Use this for complex Python code (20+ lines, subplots, multi-step analysis).

Before calling this tool, generate the complete Python code and it will be
automatically stored in graph state. Then call this tool to execute it.

CRITICAL: This runs in Pyodide (Python in WebAssembly).

DOCUMENT GENERATION (Excel, PDF, Word, PowerPoint):
Use preloaded helper modules - they auto-install dependencies:

```python
# Excel files - No manual installation needed!
from document.xlsx_helpers import xlsx_write_cells, xlsx_to_dict
# OR use openpyxl directly after: await micropip.install('openpyxl')

# PDF creation
from document.pdf_creation import pdf_create_simple
# OR use fpdf2 directly after: await micropip.install('fpdf2')

# PDF manipulation
from document.pdf_manipulation import pdf_merge, pdf_split

# Word/PowerPoint (pure Python XML manipulation, no install needed)
from document.docx_ooxml import docx_find_replace
from document.pptx_ooxml import pptx_replace_text
```

OTHER PACKAGES require micropip installation:
```python
import micropip
await micropip.install('pandas')  # Data analysis
await micropip.install('matplotlib')  # ALL charts, plots, diagrams, visualizations
await micropip.install('numpy')  # Numerical operations
```

IMPORTANT: For matplotlib visualizations, ALWAYS install first:
```python
import micropip
await micropip.install('matplotlib')
import matplotlib.pyplot as plt
# ... your plotting code
```

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
        _state: dict | None = None,
        tool_call_id: str = "",
        _config: dict | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute code from graph state."""
        if _state is None:
            return (
                "Error: This tool requires graph state. "
                "Please use with a custom tool node that injects state, "
                "or use the standard python_run tool."
            )

        thread_id = self._extract_thread_id(_config, run_manager)
        code = self._get_code_from_state(_state, tool_call_id)

        if not code:
            return (
                "Error: No code found in graph state. "
                "Generate Python code first before calling this tool."
            )

        vfs = VirtualFilesystem(self.db_pool, thread_id)
        try:
            await vfs.write_file(file_path, code.encode("utf-8"))
            logger.info(f"execute_code: Wrote {len(code)} bytes to {file_path}")
        except Exception as e:
            logger.error(f"execute_code: Failed to write file: {e}")
            return f"Error writing code to file: {e}"

        emitter = AguiEventEmitter(f"python_exec:{uuid4()}")
        emitter.emit_start(file_path, description, len(code))

        executor = SandboxExecutor(self.db_pool, thread_id, allow_net=True, stateful=True)

        try:
            exec_result = await executor.execute(code)
            logger.info("execute_code: Execution completed")

            result = _format_execution_result(exec_result)
            logger.info(f"execute_code: Generated result with {len(result)} chars")

            emitter.emit_success(description, file_path, exec_result)

            return self._return_with_state_update(
                result, tool_call_id, _state, exec_result.created_files
            )

        except Exception as e:
            logger.error(f"execute_code: Execution failed: {e}")
            emitter.emit_error(description, file_path, str(e))
            return f"Error executing code: {e}"

    def _extract_thread_id(
        self,
        _config: dict | None,
        run_manager: AsyncCallbackManagerForToolRun | None,
    ) -> str:
        """Extract thread_id from config or run_manager."""
        if _config:
            thread_id = _config.get("configurable", {}).get("thread_id")
            if thread_id:
                return thread_id
        return self._get_thread_id(run_manager)

    def _get_code_from_state(self, _state: dict, tool_call_id: str) -> str:
        """Get code from graph state's pending_content_map."""
        pending_content_map = _state.get("pending_content_map", {})
        logger.info(f"execute_code: Looking for tool_call_id={tool_call_id}")
        logger.info(f"execute_code: pending_content_map keys: {list(pending_content_map.keys())}")

        code = pending_content_map.get(tool_call_id, "")
        if code:
            logger.info(f"execute_code: Found {len(code)} chars of code")
        else:
            logger.error(f"execute_code: No code found for tool_call_id={tool_call_id}")
        return code

    def _return_with_state_update(
        self,
        result: str,
        tool_call_id: str,
        _state: dict,
        created_files: list[str] | None,
    ):
        """Return result with LangGraph state update if possible."""
        if not tool_call_id:
            return result

        try:
            from langchain_core.messages import ToolMessage
            from langgraph.types import Command

            pending_content_map = _state.get("pending_content_map", {})
            updated_map = {k: v for k, v in pending_content_map.items() if k != tool_call_id}

            state_update = {
                "pending_content_map": updated_map,
                "created_files": created_files,
                "messages": [ToolMessage(content=result, tool_call_id=tool_call_id)],
            }
            return Command(update=state_update, resume=result)  # type: ignore[return-value]
        except ImportError:
            return result

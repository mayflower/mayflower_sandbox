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
        default="/tmp/script.py",
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
        # If _state is None, tool is being called without custom state injection
        if _state is None:
            return (
                "Error: This tool requires graph state. "
                "Please use with a custom tool node that injects state, "
                "or use the standard python_run tool."
            )
        # Extract thread_id from config (passed by custom_tool_node)
        thread_id = None
        if _config:
            thread_id = _config.get("configurable", {}).get("thread_id")
        if not thread_id:
            thread_id = self._get_thread_id(run_manager)

        # Access code from graph state using tool_call_id
        pending_content_map = _state.get("pending_content_map", {})

        # Debug logging
        logger.info(f"execute_code: Looking for tool_call_id={tool_call_id}")
        logger.info(f"execute_code: pending_content_map keys: {list(pending_content_map.keys())}")
        logger.info(
            f"execute_code: pending_content_map sizes: {[(k[:12], len(v)) for k, v in pending_content_map.items()]}"
        )

        code = pending_content_map.get(tool_call_id, "")

        if not code:
            logger.error(f"execute_code: No code found in state for tool_call_id={tool_call_id}")
            logger.error(f"execute_code: Available keys in map: {list(pending_content_map.keys())}")
            return (
                "Error: No code found in graph state. "
                "Generate Python code first before calling this tool."
            )

        logger.info(
            f"execute_code: Found {len(code)} chars of code in state for tool_call_id={tool_call_id[:8]}..."
        )

        # Write code to VFS
        vfs = VirtualFilesystem(self.db_pool, thread_id)
        try:
            await vfs.write_file(file_path, code.encode("utf-8"))
            logger.info(f"execute_code: Wrote {len(code)} bytes to {file_path}")
        except Exception as e:
            logger.error(f"execute_code: Failed to write file: {e}")
            return f"Error writing code to file: {e}"

        # Emit ToolCall events for progress streaming
        try:
            from langgraph.config import get_stream_writer
            from uuid import uuid4
            import json

            writer = get_stream_writer()
            exec_tool_call_id = f"python_exec:{uuid4()}"

            # Emit ToolCallStart
            writer({"aguiTool": {
                "type": "ToolCallStart",
                "toolCallId": exec_tool_call_id,
                "toolCallName": "python_execution"
            }})
            logger.debug(f"Emitted ToolCallStart for Python execution")

            # Emit file path and description
            writer({"aguiTool": {
                "type": "ToolCallArgs",
                "toolCallId": exec_tool_call_id,
                "delta": json.dumps({
                    "file_path": file_path,
                    "description": description,
                    "code_size": len(code),
                    "status": "preparing"
                })
            }})

            # Emit execution start
            writer({"aguiTool": {
                "type": "ToolCallArgs",
                "toolCallId": exec_tool_call_id,
                "delta": json.dumps({
                    "status": "executing",
                    "message": f"Executing {description}..."
                })
            }})

        except Exception as e:
            logger.error(f"execute_code: Failed to emit start events: {e}", exc_info=True)

        # Execute the code with network access enabled for micropip package installation
        executor = SandboxExecutor(self.db_pool, thread_id, allow_net=True, stateful=True)
        try:
            exec_result = await executor.execute(code)
            logger.info("execute_code: Execution completed")

            # Format result similar to ExecutePythonTool
            response_parts = []

            # Add stdout if present
            if exec_result.stdout:
                response_parts.append(exec_result.stdout.strip())

            # Add created files with inline images
            if exec_result.created_files:
                # Separate image files from other files
                image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
                image_files = []
                other_files = []

                for path in exec_result.created_files:
                    import os

                    ext = os.path.splitext(path)[1].lower()
                    if ext in image_extensions:
                        image_files.append(path)
                    else:
                        other_files.append(path)

                # Show image files inline as markdown
                if image_files:
                    images_md = "\n\n".join(f"![Generated image]({path})" for path in image_files)
                    response_parts.append(images_md)

                # Show other files as markdown links
                if other_files:
                    import os

                    files_md = "\n".join(
                        f"- [{os.path.basename(path)}]({path})" for path in other_files
                    )
                    response_parts.append(files_md)

            result = "\n\n".join(response_parts) if response_parts else "Code executed successfully"
            logger.info(
                f"execute_code: Generated result with {len(result)} chars: {result[:200]}..."
            )

            # Emit execution completion events
            try:
                from langgraph.config import get_stream_writer
                import json

                writer = get_stream_writer()

                # Emit success status
                writer({"aguiTool": {
                    "type": "ToolCallArgs",
                    "toolCallId": exec_tool_call_id,
                    "delta": json.dumps({
                        "status": "completed",
                        "message": "Execution completed successfully",
                        "files_created": len(exec_result.created_files) if exec_result.created_files else 0
                    })
                }})

                # Emit stdout if present as ToolCallResult
                result_content = {
                    "description": description,
                    "file_path": file_path,
                    "success": True
                }

                if exec_result.stdout:
                    result_content["stdout"] = exec_result.stdout[:1000]  # Truncate for streaming

                if exec_result.created_files:
                    result_content["files"] = exec_result.created_files

                writer({"aguiTool": {
                    "type": "ToolCallResult",
                    "toolCallId": exec_tool_call_id,
                    "messageId": str(uuid4()),
                    "role": "tool",
                    "content": result_content
                }})

                # Emit ToolCallEnd
                writer({"aguiTool": {
                    "type": "ToolCallEnd",
                    "toolCallId": exec_tool_call_id
                }})

                logger.debug(f"Emitted Python execution completion events")

            except Exception as e:
                logger.error(f"execute_code: Failed to emit completion events: {e}", exc_info=True)

            # Clear this tool_call_id's content from state after successful execution
            if tool_call_id:
                try:
                    from langchain_core.messages import ToolMessage
                    from langgraph.types import Command

                    # Remove this tool_call_id from pending_content_map
                    updated_map = {
                        k: v for k, v in pending_content_map.items() if k != tool_call_id
                    }

                    state_update = {
                        "pending_content_map": updated_map,  # Clear this tool's content
                        "created_files": exec_result.created_files,
                        "messages": [ToolMessage(content=result, tool_call_id=tool_call_id)],
                    }

                    return Command(update=state_update, resume=result)  # type: ignore[return-value]
                except ImportError:
                    pass

            return result
        except Exception as e:
            logger.error(f"execute_code: Execution failed: {e}")

            # Emit error events
            try:
                from langgraph.config import get_stream_writer
                import json

                writer = get_stream_writer()

                # Emit error status
                writer({"aguiTool": {
                    "type": "ToolCallArgs",
                    "toolCallId": exec_tool_call_id,
                    "delta": json.dumps({
                        "status": "error",
                        "message": f"Execution failed: {str(e)}"
                    })
                }})

                # Emit error result
                writer({"aguiTool": {
                    "type": "ToolCallResult",
                    "toolCallId": exec_tool_call_id,
                    "messageId": str(uuid4()),
                    "role": "tool",
                    "content": {
                        "description": description,
                        "file_path": file_path,
                        "success": False,
                        "error": str(e)
                    }
                }})

                # Emit ToolCallEnd
                writer({"aguiTool": {
                    "type": "ToolCallEnd",
                    "toolCallId": exec_tool_call_id
                }})

            except:
                pass  # Don't fail if streaming fails

            return f"Error executing code: {e}"

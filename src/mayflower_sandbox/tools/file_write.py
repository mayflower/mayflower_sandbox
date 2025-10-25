"""
FileWriteTool - Write files to sandbox VFS.
"""

import logging
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)


class FileWriteInput(BaseModel):
    """Input schema for FileWriteTool."""

    file_path: str = Field(description="Path where to write the file (e.g., /tmp/data.txt)")
    description: str = Field(
        default="File content",
        description="Brief description of what the file contains"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class FileWriteTool(SandboxTool):
    """
    Tool for writing files to the sandbox VFS from graph state.

    This is a state-based tool similar to python_run_prepared. Content is extracted
    from graph state to avoid tool parameter serialization issues with large files.

    Workflow:
    1. LLM generates file content in a message
    2. Custom tool node extracts content and stores in state["pending_content"]
    3. LLM calls file_write(file_path="/tmp/data.csv")
    4. Tool reads content from state, writes to VFS, clears pending_content

    Files are stored in PostgreSQL with 20MB size limit.
    """

    name: str = "file_write"
    description: str = """Write content to a file in the sandbox filesystem.

Before calling this tool, generate the complete file content and it will be
automatically stored in graph state. Then call this tool to write it.

Files are stored in PostgreSQL with a 20MB size limit per file.
Use this to create or overwrite files that Python code can read.

Args:
    file_path: Path where to write (e.g., /tmp/input.csv, /data/config.json)

Returns:
    Confirmation message
"""
    args_schema: type[BaseModel] = FileWriteInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        description: str,
        _state: dict,
        tool_call_id: str = "",
        _config: dict | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Write file to VFS from graph state."""
        # Extract thread_id from config (passed by custom_tool_node)
        thread_id = None
        if _config:
            thread_id = _config.get("configurable", {}).get("thread_id")
        if not thread_id:
            thread_id = self._get_thread_id(run_manager)

        # Access content from graph state
        content = _state.get("pending_content", "")

        if not content:
            logger.error("file_write: No content found in state")
            return (
                "Error: No content found in graph state. "
                "Generate file content first before calling this tool."
            )

        logger.info(f"file_write: Found {len(content)} chars of content in state")

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            content_bytes = content.encode("utf-8")
            await vfs.write_file(file_path, content_bytes)
            logger.info(f"file_write: Wrote {len(content_bytes)} bytes to {file_path}")
            message = f"Successfully wrote {len(content_bytes)} bytes to {file_path}"

            # Clear pending_content from state after successful write
            if tool_call_id:
                try:
                    from langchain_core.messages import ToolMessage
                    from langgraph.types import Command

                    state_update = {
                        "pending_content": "",  # Clear after write
                        "created_files": [file_path],
                        "messages": [ToolMessage(content=message, tool_call_id=tool_call_id)],
                    }

                    return Command(update=state_update, resume=message)  # type: ignore[return-value]
                except ImportError:
                    pass

            return message
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"file_write: Failed to write file: {e}")
            return f"Error writing file: {e}"

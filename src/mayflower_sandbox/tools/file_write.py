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
        default="File content", description="Brief description of what the file contains"
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
        _state: dict | None = None,
        tool_call_id: str = "",
        _config: dict | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Write file to VFS from graph state."""
        # If _state is None, tool is being called without custom state injection
        if _state is None:
            return (
                "Error: This tool requires graph state. "
                "Please use with a custom tool node that injects state, "
                "or use standard file operations."
            )
        # Extract thread_id from config (passed by custom_tool_node)
        thread_id = None
        if _config:
            thread_id = _config.get("configurable", {}).get("thread_id")
        if not thread_id:
            thread_id = self._get_thread_id(run_manager)

        # Access content from graph state using tool_call_id
        pending_content_map = _state.get("pending_content_map", {})
        content = pending_content_map.get(tool_call_id, "")

        if not content:
            logger.error(f"file_write: No content found in state for tool_call_id={tool_call_id}")
            return (
                "Error: No content found in graph state. "
                "Generate file content first before calling this tool."
            )

        logger.info(
            f"file_write: Found {len(content)} chars of content in state for tool_call_id={tool_call_id[:8]}..."
        )

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            content_bytes = content.encode("utf-8")
            await vfs.write_file(file_path, content_bytes)
            logger.info(f"file_write: Wrote {len(content_bytes)} bytes to {file_path}")

            # Format message with markdown link (similar to python_run_prepared)
            # This makes the file appear as a download link in the chat
            import os

            filename = os.path.basename(file_path)

            # Check if it's an image file
            image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
            ext = os.path.splitext(file_path)[1].lower()

            if ext in image_extensions:
                # Use markdown image syntax for images
                message = f"Successfully wrote {len(content_bytes)} bytes to {file_path}\n\n![{filename}]({file_path})"
            else:
                # Use markdown link syntax for other files
                message = f"Successfully wrote {len(content_bytes)} bytes to {file_path}\n\n[{filename}]({file_path})"

            # Clear this tool_call_id's content from state after successful write
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
                        "created_files": [file_path],
                        "messages": [ToolMessage(content=message, tool_call_id=tool_call_id)],
                    }

                    logger.info(
                        f"file_write: Returning Command with state_update keys: {list(state_update.keys())}"
                    )
                    logger.info(f"file_write: created_files = {state_update['created_files']}")
                    logger.info(f"file_write: resume message length = {len(message)}")
                    logger.info(f"file_write: resume message = {message}")
                    logger.info(f"file_write: resume message repr = {repr(message)}")
                    return Command(update=state_update, resume=message)  # type: ignore[return-value]
                except ImportError:
                    pass

            return message
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"file_write: Failed to write file: {e}")
            return f"Error writing file: {e}"

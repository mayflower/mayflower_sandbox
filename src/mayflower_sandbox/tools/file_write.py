"""
FileWriteTool - Write files to sandbox VFS.
"""

import logging
import re
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
    content: str | None = Field(
        default=None,
        description="Content to write (omit for large content - use extract_from_response=True instead)"
    )
    extract_from_response: bool = Field(
        default=False,
        description="Extract content from previous AI response markdown block (for large files > 1000 chars)"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class FileWriteTool(SandboxTool):
    """
    Tool for writing files to the sandbox VFS.

    Files are stored in PostgreSQL with 20MB size limit.
    Supports two modes:
    1. Direct content: Pass content parameter (for small files < 1000 chars)
    2. Extract from response: Set extract_from_response=True (for large files)
    """

    name: str = "file_write"
    description: str = """Write content to a file in the sandbox filesystem.

Files are stored in PostgreSQL with a 20MB size limit per file.
Use this to create or overwrite files that Python code can read.

Two modes:
1. Small content (< 1000 chars): Pass content parameter directly
2. Large content (> 1000 chars): Set extract_from_response=True and provide content in previous response

Args:
    file_path: Path where to write (e.g., /tmp/input.csv, /data/config.json)
    content: Content to write (for small files)
    extract_from_response: Extract content from previous AI response markdown block (for large files)

Returns:
    Confirmation message
"""
    args_schema: type[BaseModel] = FileWriteInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        content: str | None = None,
        extract_from_response: bool = False,
        tool_call_id: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Write file to VFS."""
        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        # Handle extract_from_response mode
        if extract_from_response:
            logger.info("write_file: Extracting content from conversation history")

            # Access conversation history via callback metadata
            metadata = run_manager.metadata if run_manager else {}
            messages = metadata.get("messages", [])

            if not messages:
                return (
                    "Error: No conversation history found. "
                    "Make sure to provide content or enable message passing."
                )

            # Find last AIMessage
            last_ai_msg = None
            for msg in reversed(messages):
                if hasattr(msg, "type") and msg.type == "ai":
                    last_ai_msg = msg
                    break
                elif msg.__class__.__name__ == "AIMessage":
                    last_ai_msg = msg
                    break

            if not last_ai_msg:
                return "Error: No AI message found in conversation history"

            # Extract content from markdown block or use full content
            ai_content = last_ai_msg.content
            block_match = re.search(r'```(?:\w+)?\n(.*?)\n```', ai_content, re.DOTALL)

            if block_match:
                content = block_match.group(1)
                logger.info(f"write_file: Extracted {len(content)} chars from markdown block")
            else:
                # No markdown block, use full content
                content = ai_content
                logger.info(f"write_file: Using full AI response ({len(content)} chars)")

        # Validate content
        if not content:
            return "Error: No content provided. Use content parameter or extract_from_response=True"

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            content_bytes = content.encode("utf-8")
            await vfs.write_file(file_path, content_bytes)
            message = f"Successfully wrote {len(content_bytes)} bytes to {file_path}"

            # Update agent state with created file if using LangGraph and tool_call_id provided
            if tool_call_id:
                try:
                    from langchain_core.messages import ToolMessage
                    from langgraph.types import Command

                    # Build state update with both custom field and ToolMessage
                    state_update = {
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
            return f"Error writing file: {e}"

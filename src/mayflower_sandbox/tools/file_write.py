"""
FileWriteTool - Write files to sandbox VFS.
"""

from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


class FileWriteInput(BaseModel):
    """Input schema for FileWriteTool."""

    file_path: str = Field(description="Path where to write the file (e.g., /tmp/data.txt)")
    content: str = Field(description="Content to write to the file")
    tool_call_id: Annotated[str, InjectedToolCallId]


class FileWriteTool(SandboxTool):
    """
    Tool for writing files to the sandbox VFS.

    Files are stored in PostgreSQL with 20MB size limit.
    """

    name: str = "write_file"
    description: str = """Write content to a file in the sandbox filesystem.

Files are stored in PostgreSQL with a 20MB size limit per file.
Use this to create or overwrite files that Python code can read.

Args:
    file_path: Path where to write (e.g., /tmp/input.csv, /data/config.json)
    content: Content to write to the file

Returns:
    Confirmation message
"""
    args_schema: type[BaseModel] = FileWriteInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        content: str,
        tool_call_id: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Write file to VFS."""
        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            content_bytes = content.encode("utf-8")
            await vfs.write_file(file_path, content_bytes)
            message = f"Successfully wrote {len(content_bytes)} bytes to {file_path}"

            # Update agent state with created file if using LangGraph
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
                return message
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"

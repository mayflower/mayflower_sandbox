"""
FileDeleteTool - Delete files from sandbox VFS.
"""

from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.callbacks import AsyncCallbackManagerForToolRun

from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.filesystem import VirtualFilesystem


class FileDeleteInput(BaseModel):
    """Input schema for FileDeleteTool."""

    file_path: str = Field(description="Path to the file to delete (e.g., /tmp/data.txt)")


class FileDeleteTool(SandboxTool):
    """
    Tool for deleting files from the sandbox VFS.

    Removes files from PostgreSQL storage.
    """

    name: str = "delete_file"
    description: str = """Delete a file from the sandbox filesystem.

Permanently removes a file from PostgreSQL storage.
Use with caution - deletions cannot be undone.

Args:
    file_path: Path to the file to delete (e.g., /tmp/old_data.txt)

Returns:
    Confirmation message
"""
    args_schema: type[BaseModel] = FileDeleteInput

    async def _arun(
        self,
        file_path: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        """Delete file from VFS."""
        vfs = VirtualFilesystem(self.db_pool, self.thread_id)

        try:
            deleted = await vfs.delete_file(file_path)
            if deleted:
                return f"Successfully deleted: {file_path}"
            else:
                return f"Error: File not found: {file_path}"
        except Exception as e:
            return f"Error deleting file: {e}"

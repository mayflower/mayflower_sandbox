"""
FileListTool - List files in sandbox VFS.
"""

from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.callbacks import AsyncCallbackManagerForToolRun

from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.filesystem import VirtualFilesystem


class FileListInput(BaseModel):
    """Input schema for FileListTool."""

    prefix: str = Field(
        default="",
        description="Optional path prefix to filter files (e.g., /tmp/, /data/)",
    )


class FileListTool(SandboxTool):
    """
    Tool for listing files in the sandbox VFS.

    Shows all files for the current thread_id.
    """

    name: str = "list_files"
    description: str = """List all files in the sandbox filesystem.

Shows files stored in PostgreSQL for the current session.
Optionally filter by path prefix.

Args:
    prefix: Optional path prefix to filter files (default: all files)

Returns:
    List of files with paths, sizes, and types
"""
    args_schema: type[BaseModel] = FileListInput

    async def _arun(
        self,
        prefix: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        """List files in VFS."""
        vfs = VirtualFilesystem(self.db_pool, self.thread_id)

        try:
            # Convert prefix to SQL LIKE pattern
            pattern = f"{prefix}%" if prefix else None
            files = await vfs.list_files(pattern=pattern)

            if not files:
                return "No files found" if not prefix else f"No files found with prefix: {prefix}"

            lines = [f"Found {len(files)} file(s):\n"]
            for file_info in files:
                size_kb = file_info["size"] / 1024
                lines.append(
                    f"  {file_info['file_path']}\n"
                    f"    Size: {size_kb:.2f} KB\n"
                    f"    Type: {file_info['content_type']}"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"Error listing files: {e}"

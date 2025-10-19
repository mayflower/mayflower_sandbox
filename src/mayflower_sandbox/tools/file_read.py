"""
FileReadTool - Read files from sandbox VFS.
"""

from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.callbacks import AsyncCallbackManagerForToolRun

from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.filesystem import VirtualFilesystem


class FileReadInput(BaseModel):
    """Input schema for FileReadTool."""

    file_path: str = Field(description="Path to the file to read (e.g., /tmp/data.txt)")


class FileReadTool(SandboxTool):
    """
    Tool for reading files from the sandbox VFS.

    Files are stored in PostgreSQL and isolated by thread_id.
    """

    name: str = "read_file"
    description: str = """Read a file from the sandbox filesystem.

The sandbox has a persistent filesystem backed by PostgreSQL.
Use this to read files created by Python code or previously uploaded.

Args:
    file_path: Path to the file (e.g., /tmp/output.txt, /data/results.csv)

Returns:
    File contents as text
"""
    args_schema: type[BaseModel] = FileReadInput

    async def _arun(
        self,
        file_path: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        """Read file from VFS."""
        vfs = VirtualFilesystem(self.db_pool, self.thread_id)

        try:
            file_info = await vfs.read_file(file_path)
            content = file_info["content"].decode("utf-8", errors="replace")
            return f"File: {file_path}\nSize: {file_info['size']} bytes\nType: {file_info['content_type']}\n\nContent:\n{content}"
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except Exception as e:
            return f"Error reading file: {e}"

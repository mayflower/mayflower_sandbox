"""
FileReadTool - Read files from sandbox VFS.
"""

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


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

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Read file from VFS."""
        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            file_info = await vfs.read_file(file_path)
            content = file_info["content"].decode("utf-8", errors="replace")
            return f"File: {file_path}\nSize: {file_info['size']} bytes\nType: {file_info['content_type']}\n\nContent:\n{content}"
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except Exception as e:
            return f"Error reading file: {e}"

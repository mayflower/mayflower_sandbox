"""
FileWriteTool - Write files to sandbox VFS.
"""

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


class FileWriteInput(BaseModel):
    """Input schema for FileWriteTool."""

    file_path: str = Field(description="Path where to write the file (e.g., /tmp/data.txt)")
    content: str = Field(description="Content to write to the file")


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
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Write file to VFS."""
        vfs = VirtualFilesystem(self.db_pool, self.thread_id)

        try:
            content_bytes = content.encode("utf-8")
            await vfs.write_file(file_path, content_bytes)
            return f"Successfully wrote {len(content_bytes)} bytes to {file_path}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"

"""
FileEditTool - Edit files using string replacement.
"""

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


class FileEditInput(BaseModel):
    """Input schema for FileEditTool."""

    file_path: str = Field(description="Path to the file to edit (e.g., /tmp/config.txt)")
    old_string: str = Field(description="Unique string to replace (must appear exactly once)")
    new_string: str = Field(description="New string to replace it with")


class FileEditTool(SandboxTool):
    """
    Tool for editing files using string replacement.

    Replaces a unique string in a file. The old_string must appear exactly once
    in the file for the operation to succeed.
    """

    name: str = "str_replace"
    description: str = """Edit a file by replacing a unique string with a new string.

The old_string must appear exactly once in the file. If it appears zero times
or multiple times, the operation will fail.

Use this for:
- Modifying code in existing files
- Updating configuration values
- Fixing bugs in specific locations

Args:
    file_path: Path to the file to edit (e.g., /tmp/script.py)
    old_string: Unique string to find and replace (must appear exactly once)
    new_string: New string to replace it with

Returns:
    Confirmation message or error if string not found or not unique

Example:
    file_path: /tmp/config.py
    old_string: DEBUG = False
    new_string: DEBUG = True
"""
    args_schema: type[BaseModel] = FileEditInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Edit file using string replacement."""
        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            # Read file
            file_info = await vfs.read_file(file_path)
            content = file_info["content"].decode("utf-8", errors="replace")

            # Check if old_string appears exactly once
            count = content.count(old_string)
            if count == 0:
                return f"Error: String not found in {file_path}\n\nSearched for:\n{old_string}"
            elif count > 1:
                return (
                    f"Error: String appears {count} times in {file_path} (must be unique)\n\n"
                    f"Searched for:\n{old_string}\n\n"
                    f"Please provide a longer, more specific string that appears exactly once."
                )

            # Replace
            new_content = content.replace(old_string, new_string, 1)
            new_content_bytes = new_content.encode("utf-8")

            # Write back
            await vfs.write_file(file_path, new_content_bytes)

            return (
                f"Successfully edited {file_path}\n\nReplaced:\n{old_string}\n\nWith:\n{new_string}"
            )

        except FileNotFoundError:
            return f"Error: File not found: {file_path}"
        except Exception as e:
            return f"Error editing file: {e}"

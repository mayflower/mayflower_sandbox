"""
FileGlobTool - Find files by glob pattern.
"""

import fnmatch
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


def _match_recursive_pattern(file_path: str, pattern: str) -> bool:
    """Match file path against a ** recursive glob pattern."""
    pattern_parts = pattern.split("**")
    if len(pattern_parts) != 2:
        return False

    prefix, suffix = pattern_parts
    if prefix and not file_path.startswith(prefix):
        return False

    remaining = file_path[len(prefix) :] if prefix else file_path
    return fnmatch.fnmatch(remaining, suffix.lstrip("/"))


def _matches_pattern(file_path: str, pattern: str) -> bool:
    """Check if a file path matches the given glob pattern."""
    if fnmatch.fnmatch(file_path, pattern):
        return True
    if "**" in pattern:
        return _match_recursive_pattern(file_path, pattern)
    return False


def _format_file_info(file_info: dict[str, Any]) -> str:
    """Format a single file's info for display."""
    size_kb = file_info["size"] / 1024
    return (
        f"  {file_info['file_path']}\n"
        f"    Size: {size_kb:.2f} KB\n"
        f"    Type: {file_info['content_type']}"
    )


class FileGlobInput(BaseModel):
    """Input schema for FileGlobTool."""

    pattern: str = Field(description="Glob pattern to match files (e.g., **/*.py, *.txt, /data/*)")


class FileGlobTool(SandboxTool):
    """
    Tool for finding files by glob pattern.

    Supports standard glob patterns like *.py, **/*.txt, /data/*.json
    """

    name: str = "file_glob"
    description: str = """Find files matching a glob pattern.

Supports patterns like:
- *.py - All Python files in any directory
- **/*.txt - All .txt files recursively
- /tmp/*.json - All JSON files in /tmp
- /data/**/*.csv - All CSV files under /data recursively

Args:
    pattern: Glob pattern to match file paths

Returns:
    List of matching file paths
"""
    args_schema: type[BaseModel] = FileGlobInput

    async def _arun(  # type: ignore[override]
        self,
        pattern: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Find files matching glob pattern."""
        thread_id = self._get_thread_id(run_manager)
        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            all_files = await vfs.list_files()
            matching_files = [f for f in all_files if _matches_pattern(f["file_path"], pattern)]

            if not matching_files:
                return f"No files found matching pattern: {pattern}"

            lines = [f"Found {len(matching_files)} file(s) matching '{pattern}':\n"]
            lines.extend(_format_file_info(f) for f in matching_files)

            return "\n".join(lines)
        except Exception as e:
            return f"Error finding files: {e}"

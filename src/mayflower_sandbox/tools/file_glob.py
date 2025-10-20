"""
FileGlobTool - Find files by glob pattern.
"""

import fnmatch

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


class FileGlobInput(BaseModel):
    """Input schema for FileGlobTool."""

    pattern: str = Field(description="Glob pattern to match files (e.g., **/*.py, *.txt, /data/*)")


class FileGlobTool(SandboxTool):
    """
    Tool for finding files by glob pattern.

    Supports standard glob patterns like *.py, **/*.txt, /data/*.json
    """

    name: str = "glob_files"
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
        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            # Get all files
            all_files = await vfs.list_files()

            # Filter by glob pattern
            matching_files = []
            for file_info in all_files:
                file_path = file_info["file_path"]

                # Support both ** (recursive) and * (non-recursive) patterns
                if fnmatch.fnmatch(file_path, pattern):
                    matching_files.append(file_info)
                # Also try matching with ** expansion
                elif "**" in pattern:
                    # Convert ** pattern to regex-like matching
                    pattern_parts = pattern.split("**")
                    if len(pattern_parts) == 2:
                        prefix, suffix = pattern_parts
                        # Check if path starts with prefix and ends with suffix pattern
                        if file_path.startswith(prefix) or not prefix:
                            remaining = file_path[len(prefix) :] if prefix else file_path
                            if fnmatch.fnmatch(remaining, suffix.lstrip("/")):
                                matching_files.append(file_info)

            if not matching_files:
                return f"No files found matching pattern: {pattern}"

            lines = [f"Found {len(matching_files)} file(s) matching '{pattern}':\n"]
            for file_info in matching_files:
                size_kb = file_info["size"] / 1024
                lines.append(
                    f"  {file_info['file_path']}\n"
                    f"    Size: {size_kb:.2f} KB\n"
                    f"    Type: {file_info['content_type']}"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"Error finding files: {e}"

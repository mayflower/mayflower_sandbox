"""
FileGrepTool - Search file contents using regex.
"""

import re

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


class FileGrepInput(BaseModel):
    """Input schema for FileGrepTool."""

    pattern: str = Field(description="Regular expression pattern to search for")
    output_mode: str = Field(
        default="files_with_matches",
        description="Output mode: files_with_matches, content, or count",
    )
    case_insensitive: bool = Field(
        default=False, description="Whether to perform case-insensitive search"
    )


class FileGrepTool(SandboxTool):
    """
    Tool for searching file contents using regular expressions.

    Supports multiple output modes for different use cases.
    """

    name: str = "grep_files"
    description: str = """Search file contents using regex patterns.

Output modes:
- files_with_matches: Show only file paths containing matches (default)
- content: Show matching lines with context
- count: Show count of matches per file

Args:
    pattern: Regular expression pattern to search for
    output_mode: files_with_matches, content, or count (default: files_with_matches)
    case_insensitive: Perform case-insensitive search (default: False)

Returns:
    Search results in the specified format

Examples:
- Find files containing "TODO": pattern="TODO"
- Find function definitions: pattern="def \\w+\\("
- Case-insensitive search: pattern="error", case_insensitive=True
"""
    args_schema: type[BaseModel] = FileGrepInput

    async def _arun(  # type: ignore[override]
        self,
        pattern: str,
        output_mode: str = "files_with_matches",
        case_insensitive: bool = False,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Search file contents with regex."""
        vfs = VirtualFilesystem(self.db_pool, self.thread_id)

        # Validate output_mode
        valid_modes = {"files_with_matches", "content", "count"}
        if output_mode not in valid_modes:
            return f"Error: Invalid output_mode '{output_mode}'. Must be one of: {', '.join(valid_modes)}"

        try:
            # Compile regex pattern
            flags = re.IGNORECASE if case_insensitive else 0
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                return f"Error: Invalid regex pattern: {e}"

            # Get all files
            all_files = await vfs.list_files()

            # Search files
            results = []
            for file_info in all_files:
                file_path = file_info["file_path"]

                # Read file content
                try:
                    file_data = await vfs.read_file(file_path)
                    content = file_data["content"].decode("utf-8", errors="replace")
                except Exception:
                    # Skip files that can't be read as text
                    continue

                # Search for pattern
                if output_mode == "files_with_matches":
                    if regex.search(content):
                        results.append(file_path)

                elif output_mode == "content":
                    lines = content.split("\n")
                    matches = []
                    for line_num, line in enumerate(lines, 1):
                        if regex.search(line):
                            matches.append(f"  {line_num}: {line}")

                    if matches:
                        results.append(
                            f"{file_path}:\n"
                            + "\n".join(matches[:10])  # Limit to 10 matches per file
                        )
                        if len(matches) > 10:
                            results[-1] += f"\n  ... ({len(matches) - 10} more matches)"

                elif output_mode == "count":
                    matches = regex.findall(content)
                    if matches:
                        results.append(f"{file_path}: {len(matches)} match(es)")

            if not results:
                return f"No matches found for pattern: {pattern}"

            if output_mode == "files_with_matches":
                return f"Found {len(results)} file(s) matching '{pattern}':\n" + "\n".join(
                    f"  {path}" for path in results
                )
            elif output_mode == "content":
                return f"Matches for '{pattern}':\n\n" + "\n\n".join(results)
            else:  # count
                return f"Match counts for '{pattern}':\n" + "\n".join(results)

        except Exception as e:
            return f"Error searching files: {e}"

"""
FileGrepTool - Search file contents using regex.
"""

import re
from re import Pattern

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool

# Valid output modes
_VALID_OUTPUT_MODES = {"files_with_matches", "content", "count"}
_MAX_MATCHES_PER_FILE = 10


def _search_files_with_matches(regex: Pattern[str], content: str) -> bool:
    """Check if content matches the pattern."""
    return regex.search(content) is not None


def _search_content_mode(regex: Pattern[str], content: str) -> list[str]:
    """Find matching lines in content."""
    lines = content.split("\n")
    return [f"  {line_num}: {line}" for line_num, line in enumerate(lines, 1) if regex.search(line)]


def _format_content_result(file_path: str, matches: list[str]) -> str:
    """Format content mode result for a single file."""
    result = f"{file_path}:\n" + "\n".join(matches[:_MAX_MATCHES_PER_FILE])
    if len(matches) > _MAX_MATCHES_PER_FILE:
        result += f"\n  ... ({len(matches) - _MAX_MATCHES_PER_FILE} more matches)"
    return result


def _search_count_mode(regex: Pattern[str], content: str) -> int:
    """Count matches in content."""
    return len(regex.findall(content))


def _format_results(output_mode: str, pattern: str, results: list) -> str:
    """Format final results based on output mode."""
    if not results:
        return f"No matches found for pattern: {pattern}"

    if output_mode == "files_with_matches":
        return f"Found {len(results)} file(s) matching '{pattern}':\n" + "\n".join(
            f"  {path}" for path in results
        )
    if output_mode == "content":
        return f"Matches for '{pattern}':\n\n" + "\n\n".join(results)
    # count mode
    return f"Match counts for '{pattern}':\n" + "\n".join(results)


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

    name: str = "file_grep"
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
        thread_id = self._get_thread_id(run_manager)
        vfs = VirtualFilesystem(self.db_pool, thread_id)

        if output_mode not in _VALID_OUTPUT_MODES:
            return f"Error: Invalid output_mode '{output_mode}'. Must be one of: {', '.join(_VALID_OUTPUT_MODES)}"

        try:
            regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        try:
            results = await self._search_all_files(vfs, regex, output_mode)
            return _format_results(output_mode, pattern, results)
        except Exception as e:
            return f"Error searching files: {e}"

    async def _search_all_files(
        self,
        vfs: VirtualFilesystem,
        regex: Pattern[str],
        output_mode: str,
    ) -> list:
        """Search all files and collect results."""
        all_files = await vfs.list_files()
        results = []

        for file_info in all_files:
            file_path = file_info["file_path"]
            content = await self._read_file_content(vfs, file_path)
            if content is None:
                continue

            result = self._process_file(file_path, content, regex, output_mode)
            if result is not None:
                results.append(result)

        return results

    async def _read_file_content(self, vfs: VirtualFilesystem, file_path: str) -> str | None:
        """Read file content, returning None if unreadable."""
        try:
            file_data = await vfs.read_file(file_path)
            return file_data["content"].decode("utf-8", errors="replace")
        except Exception:
            return None

    def _process_file(
        self,
        file_path: str,
        content: str,
        regex: Pattern[str],
        output_mode: str,
    ):
        """Process a single file based on output mode."""
        if output_mode == "files_with_matches":
            return file_path if _search_files_with_matches(regex, content) else None

        if output_mode == "content":
            matches = _search_content_mode(regex, content)
            return _format_content_result(file_path, matches) if matches else None

        # count mode
        count = _search_count_mode(regex, content)
        return f"{file_path}: {count} match(es)" if count else None

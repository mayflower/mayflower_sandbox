"""DeepAgents backend adapter for Mayflower Sandbox.

Implements BackendProtocol and SandboxBackendProtocol using the PostgreSQL-backed
VirtualFilesystem and the Pyodide SandboxExecutor.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
from datetime import datetime
from typing import Any

try:
    from deepagents.backends.protocol import (
        EditResult,
        ExecuteResponse,
        FileDownloadResponse,
        FileInfo,
        FileUploadResponse,
        GrepMatch,
        SandboxBackendProtocol,
        WriteResult,
    )
except Exception as exc:  # pragma: no cover - optional dependency
    raise ImportError("deepagents is required to use MayflowerSandboxBackend") from exc

from .filesystem import FileNotFoundError, InvalidPathError, VirtualFilesystem
from .sandbox_executor import SandboxExecutor


def _format_line_numbers(lines: list[str], start_line: int) -> str:
    width = 6
    return "\n".join(f"{idx:{width}d}\t{line}" for idx, line in enumerate(lines, start_line))


def _empty_content_warning(content: str) -> str | None:
    if not content or content.strip() == "":
        return "System reminder: File exists but has empty contents"
    return None


def _normalize_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _format_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Running in event loop: execute in a fresh loop in a worker thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


class MayflowerSandboxBackend(SandboxBackendProtocol):
    """DeepAgents backend adapter for Mayflower Sandbox."""

    def __init__(
        self,
        db_pool: Any,
        thread_id: str,
        *,
        allow_net: bool = False,
        stateful: bool = True,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._thread_id = thread_id
        self._vfs = VirtualFilesystem(db_pool, thread_id)
        self._executor = SandboxExecutor(
            db_pool,
            thread_id,
            allow_net=allow_net,
            stateful=stateful,
            timeout_seconds=timeout_seconds,
        )

    @property
    def id(self) -> str:
        return f"mayflower:{self._thread_id}"

    def ls_info(self, path: str) -> list[FileInfo]:
        try:
            normalized = self._vfs.validate_path(path)
        except InvalidPathError:
            return []

        prefix = normalized
        if prefix != "/" and not prefix.endswith("/"):
            prefix += "/"

        files = _run_async(self._vfs.list_files())

        infos: list[FileInfo] = []
        subdirs: set[str] = set()

        for file_row in files:
            file_path = file_row.get("file_path", "")
            if not file_path.startswith(prefix):
                continue
            rel = file_path[len(prefix) :]
            if rel == "":
                continue
            if "/" in rel:
                subdir_name = rel.split("/", 1)[0]
                subdirs.add(prefix + subdir_name + "/")
                continue
            infos.append(
                {
                    "path": file_path,
                    "is_dir": False,
                    "size": int(file_row.get("size", 0) or 0),
                    "modified_at": _format_timestamp(file_row.get("modified_at")),
                }
            )

        for subdir in sorted(subdirs):
            infos.append(
                {
                    "path": subdir,
                    "is_dir": True,
                    "size": 0,
                    "modified_at": "",
                }
            )

        infos.sort(key=lambda item: item.get("path", ""))
        return infos

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        try:
            record = _run_async(self._vfs.read_file(file_path))
        except (FileNotFoundError, InvalidPathError):
            return f"Error: File '{file_path}' not found"

        content_bytes = record.get("content", b"") or b""
        content = content_bytes.decode("utf-8", errors="replace")
        empty_msg = _empty_content_warning(content)
        if empty_msg:
            return empty_msg

        lines = content.splitlines()
        if offset >= len(lines):
            return f"Error: Line offset {offset} exceeds file length ({len(lines)} lines)"

        start_idx = max(0, offset)
        end_idx = min(len(lines), start_idx + limit)
        selected = lines[start_idx:end_idx]
        return _format_line_numbers(selected, start_line=start_idx + 1)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            normalized = self._vfs.validate_path(file_path)
        except InvalidPathError as exc:
            return WriteResult(error=str(exc))

        exists = _run_async(self._vfs.file_exists(normalized))
        if exists:
            return WriteResult(
                error=(
                    f"Cannot write to {normalized} because it already exists. "
                    "Read and then make an edit, or write to a new path."
                )
            )

        try:
            _run_async(self._vfs.write_file(normalized, content.encode("utf-8"), "text/plain"))
        except InvalidPathError as exc:
            return WriteResult(error=str(exc))

        return WriteResult(path=normalized, files_update=None)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            record = _run_async(self._vfs.read_file(file_path))
        except (FileNotFoundError, InvalidPathError):
            return EditResult(error=f"Error: File '{file_path}' not found")

        content_bytes = record.get("content", b"") or b""
        content = content_bytes.decode("utf-8", errors="replace")

        occurrences = content.count(old_string)
        if occurrences == 0:
            return EditResult(error=f"Error: String not found in file: '{old_string}'")
        if occurrences > 1 and not replace_all:
            return EditResult(
                error=(
                    f"Error: String '{old_string}' appears {occurrences} times in file. "
                    "Use replace_all=True to replace all instances, or provide a more specific string with surrounding context."
                )
            )

        new_content = content.replace(old_string, new_string)
        try:
            _run_async(self._vfs.write_file(file_path, new_content.encode("utf-8"), "text/plain"))
        except InvalidPathError as exc:
            return EditResult(error=str(exc))

        return EditResult(
            path=_normalize_path(file_path), files_update=None, occurrences=occurrences
        )

    def _grep_file_matches(
        self,
        file_row: dict[str, Any],
        regex: re.Pattern[str],
    ) -> list[GrepMatch]:
        """Extract matching lines from a file."""
        file_path = file_row.get("file_path", "")
        content_bytes = file_row.get("content", b"") or b""
        content = content_bytes.decode("utf-8", errors="replace")
        return [
            {"path": file_path, "line": idx, "text": line}
            for idx, line in enumerate(content.splitlines(), start=1)
            if regex.search(line)
        ]

    def _matches_glob_filter(self, file_path: str, base: str, glob_pattern: str | None) -> bool:
        """Check if file matches the glob filter."""
        if not glob_pattern:
            return True
        rel = file_path[len(base) :] if base != "/" else file_path.lstrip("/")
        return fnmatch.fnmatch(rel, glob_pattern)

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[GrepMatch] | str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"Invalid regex pattern: {exc}"

        base = _normalize_path(path or "/")
        if base != "/" and not base.endswith("/"):
            base += "/"

        files = _run_async(self._vfs.list_files())
        if base != "/" and not any(f.get("file_path", "").startswith(base) for f in files):
            return f"Error: Path '{path}' not found"

        matches: list[GrepMatch] = []
        for file_row in files:
            file_path = file_row.get("file_path", "")
            if not file_path.startswith(base):
                continue
            if not self._matches_glob_filter(file_path, base, glob):
                continue
            matches.extend(self._grep_file_matches(file_row, regex))

        return matches

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        base = _normalize_path(path)
        if base != "/" and not base.endswith("/"):
            base += "/"

        files = _run_async(self._vfs.list_files())
        infos: list[FileInfo] = []

        for file_row in files:
            file_path = file_row.get("file_path", "")
            if not file_path.startswith(base):
                continue
            rel = file_path[len(base) :] if base != "/" else file_path.lstrip("/")
            if not fnmatch.fnmatch(rel, pattern):
                continue
            infos.append(
                {
                    "path": file_path,
                    "is_dir": False,
                    "size": int(file_row.get("size", 0) or 0),
                    "modified_at": _format_timestamp(file_row.get("modified_at")),
                }
            )

        infos.sort(key=lambda item: item.get("path", ""))
        return infos

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                normalized = self._vfs.validate_path(path)
            except InvalidPathError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                _run_async(self._vfs.write_file(normalized, content, None))
            except InvalidPathError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
            except Exception:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            else:
                responses.append(FileUploadResponse(path=normalized, error=None))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                record = _run_async(self._vfs.read_file(path))
            except FileNotFoundError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
                continue
            except InvalidPathError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue

            content_bytes = record.get("content", b"") or b""
            responses.append(FileDownloadResponse(path=path, content=content_bytes, error=None))
        return responses

    def _execute_shell(self, command: str) -> ExecuteResponse:
        """Stub for shell execution (busybox patch will implement)."""
        result = _run_async(self._executor.execute_shell(command))
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        exit_code = result.exit_code
        if exit_code is None:
            exit_code = 0 if result.success else 1
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)

    def execute(self, command: str) -> ExecuteResponse:
        if command.startswith("__PYTHON__"):
            code = command[len("__PYTHON__") :].lstrip("\n")
            result = _run_async(self._executor.execute(code))
            output = result.stdout or ""
            if result.stderr:
                output = f"{output}\n{result.stderr}" if output else result.stderr
            exit_code = 0 if result.success else 1
            return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)
        return self._execute_shell(command)

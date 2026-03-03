"""DeepAgents backend adapters for Mayflower Sandbox.

Provides two backend implementations:

1. PostgresBackend - Implements BackendProtocol for file storage using PostgreSQL.
   Can be used standalone or composed with CompositeBackend.

2. MayflowerSandboxBackend - Extends PostgresBackend with SandboxBackendProtocol,
   adding Python/shell execution via Pyodide and BusyBox WASM.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import shlex
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    from deepagents.backends.protocol import (
        BackendProtocol,
        EditResult,
        ExecuteResponse,
        FileDownloadResponse,
        FileInfo,
        FileUploadResponse,
        GrepMatch,
        SandboxBackendProtocol,
        WriteResult,
    )

    DEEPAGENTS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    DEEPAGENTS_AVAILABLE = False
    # Provide fallback types for testing without deepagents installed
    from dataclasses import dataclass, field

    @dataclass
    class ExecuteResponse:  # type: ignore[no-redef]
        output: str = ""
        exit_code: int = 0
        truncated: bool = False

    @dataclass
    class EditResult:  # type: ignore[no-redef]
        error: str | None = None
        path: str | None = None
        files_update: dict[str, Any] | None = None
        occurrences: int | None = None

    @dataclass
    class WriteResult:  # type: ignore[no-redef]
        error: str | None = None
        path: str | None = None
        files_update: dict[str, Any] | None = None

    @dataclass
    class FileInfo:  # type: ignore[no-redef]
        path: str = ""
        is_dir: bool = False
        size: int = 0
        modified_at: str = ""

    @dataclass
    class FileUploadResponse:  # type: ignore[no-redef]
        path: str = ""
        error: str | None = None

    @dataclass
    class FileDownloadResponse:  # type: ignore[no-redef]
        path: str = ""
        content: bytes | None = field(default_factory=bytes)
        error: str | None = None

    @dataclass
    class GrepMatch:  # type: ignore[no-redef]
        path: str = ""
        line: int = 0
        text: str = ""

    # Base class for the protocol when deepagents is not installed
    # Use a single base class to avoid metaclass conflicts
    class BackendProtocol:  # type: ignore[no-redef]
        pass

    # SandboxBackendProtocol is the same as BackendProtocol for fallback
    SandboxBackendProtocol = BackendProtocol  # type: ignore[misc, assignment]


from .filesystem import FileNotFoundError, InvalidPathError, VirtualFilesystem  # noqa: E402
from .sandbox_executor import SandboxExecutor  # noqa: E402


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


def _create_file_data(content: str, created_at: str | None = None) -> dict[str, Any]:
    """Create a FileData dict matching DeepAgents StateBackend format.

    This ensures backends populate the `files` state field the same way
    StateBackend does, enabling the frontend to list files.

    Args:
        content: File content as string.
        created_at: Optional creation timestamp (ISO format).

    Returns:
        FileData dict with content lines and timestamps.
    """
    lines = content.split("\n") if isinstance(content, str) else content
    from datetime import timezone

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "content": lines,
        "created_at": created_at or now,
        "modified_at": now,
    }


class PostgresBackend(BackendProtocol):
    """DeepAgents BackendProtocol implementation using PostgreSQL for file storage.

    This backend stores files in a PostgreSQL database via VirtualFilesystem.
    It implements the full BackendProtocol interface for file operations but
    does NOT support command execution (no execute() method).

    Use this backend:
    - Standalone for persistent file storage
    - As a route in CompositeBackend (e.g., for /memories/)
    - As a base for MayflowerSandboxBackend which adds execution

    Example:
        ```python
        from deepagents.backends import CompositeBackend, StateBackend

        # Use PostgresBackend for persistent memories
        postgres = PostgresBackend(db_pool, thread_id)

        composite = CompositeBackend(
            default=StateBackend(runtime),
            routes={"/memories/": postgres}
        )
        ```
    """

    def __init__(self, db_pool: Any, thread_id: str) -> None:
        """Initialize PostgresBackend.

        Args:
            db_pool: asyncpg connection pool for PostgreSQL.
            thread_id: Thread/session identifier for file isolation.
        """
        self._thread_id = thread_id
        self._vfs = VirtualFilesystem(db_pool, thread_id)
        self._db_pool = db_pool
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        try:
            self._loop = asyncio.get_running_loop()
            self._loop_thread_id = threading.get_ident()
        except RuntimeError:
            self._loop = None
            self._loop_thread_id = None

    def _run_async(self, coro: Any) -> Any:
        """Run async coroutine from sync context."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is None:
            return asyncio.run(coro)

        if self._loop is None:
            self._loop = running_loop
            self._loop_thread_id = threading.get_ident()

        if self._loop is None or not self._loop.is_running():
            return asyncio.run(coro)

        if threading.get_ident() == self._loop_thread_id:
            raise RuntimeError(
                "Synchronous backend method called from the event loop; "
                "use the async methods instead."
            )

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=120.0)

    # -------------------------------------------------------------------------
    # ls_info
    # -------------------------------------------------------------------------

    def ls_info(self, path: str) -> list[FileInfo]:
        return self._run_async(self.als_info(path))

    async def als_info(self, path: str) -> list[FileInfo]:
        try:
            normalized = self._vfs.validate_path(path)
        except InvalidPathError:
            logger.debug(f"als_info: invalid path '{path}'")
            return []

        prefix = normalized
        if prefix != "/" and not prefix.endswith("/"):
            prefix += "/"

        files = await self._vfs.list_files()

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

    # -------------------------------------------------------------------------
    # read
    # -------------------------------------------------------------------------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        return self._run_async(self.aread(file_path, offset=offset, limit=limit))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        try:
            record = await self._vfs.read_file(file_path)
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

    # -------------------------------------------------------------------------
    # write
    # -------------------------------------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._run_async(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        try:
            normalized = self._vfs.validate_path(file_path)
        except InvalidPathError as exc:
            return WriteResult(error=str(exc))

        exists = await self._vfs.file_exists(normalized)
        if exists:
            return WriteResult(
                error=(
                    f"Cannot write to {normalized} because it already exists. "
                    "Read and then make an edit, or write to a new path."
                )
            )

        try:
            await self._vfs.write_file(normalized, content.encode("utf-8"), "text/plain")
        except InvalidPathError as exc:
            return WriteResult(error=str(exc))

        # Return files_update to populate the `files` state field (matches StateBackend)
        file_data = _create_file_data(content)
        return WriteResult(path=normalized, files_update={normalized: file_data})

    # -------------------------------------------------------------------------
    # edit
    # -------------------------------------------------------------------------

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._run_async(
            self.aedit(file_path, old_string, new_string, replace_all=replace_all)
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            record = await self._vfs.read_file(file_path)
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
            await self._vfs.write_file(file_path, new_content.encode("utf-8"), "text/plain")
        except InvalidPathError as exc:
            return EditResult(error=str(exc))

        # Return files_update to populate the `files` state field
        created_at = _format_timestamp(record.get("created_at"))
        normalized = _normalize_path(file_path)
        file_data = _create_file_data(new_content, created_at=created_at or None)
        return EditResult(
            path=normalized, files_update={normalized: file_data}, occurrences=occurrences
        )

    # -------------------------------------------------------------------------
    # grep_raw
    # -------------------------------------------------------------------------

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
        return self._run_async(self.agrep_raw(pattern, path=path, glob=glob))

    async def agrep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[GrepMatch] | str:
        try:
            regex = re.compile(re.escape(pattern))
        except re.error as exc:
            return f"Invalid pattern: {exc}"

        base = _normalize_path(path or "/")
        if base != "/" and not base.endswith("/"):
            base += "/"

        files = await self._vfs.list_files()
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

    # -------------------------------------------------------------------------
    # glob_info
    # -------------------------------------------------------------------------

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        return self._run_async(self.aglob_info(pattern, path=path))

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        base = _normalize_path(path)
        if base != "/" and not base.endswith("/"):
            base += "/"

        files = await self._vfs.list_files()
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

    # -------------------------------------------------------------------------
    # upload_files / download_files
    # -------------------------------------------------------------------------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._run_async(self.aupload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                normalized = self._vfs.validate_path(path)
            except InvalidPathError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                await self._vfs.write_file(normalized, content, None)
            except InvalidPathError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
            except PermissionError:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            except Exception as e:
                logger.error(
                    f"Upload failed for {path} (reporting as permission_denied): {e}", exc_info=True
                )
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            else:
                responses.append(FileUploadResponse(path=normalized, error=None))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._run_async(self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                record = await self._vfs.read_file(path)
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


class MayflowerSandboxBackend(PostgresBackend, SandboxBackendProtocol):
    """DeepAgents SandboxBackendProtocol implementation with PostgreSQL storage and Pyodide execution.

    Extends PostgresBackend with command execution capabilities:
    - Python scripts via Pyodide WebAssembly sandbox
    - Shell commands via BusyBox WebAssembly sandbox

    Example:
        ```python
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # File operations (inherited from PostgresBackend)
        backend.write("/app/script.py", "print('hello')")

        # Command execution (SandboxBackendProtocol)
        result = backend.execute("python /app/script.py")
        print(result.output)  # "hello"
        ```
    """

    def __init__(
        self,
        db_pool: Any,
        thread_id: str,
        *,
        allow_net: bool = False,
        stateful: bool = True,
        timeout_seconds: float = 60.0,
    ) -> None:
        """Initialize MayflowerSandboxBackend.

        Args:
            db_pool: asyncpg connection pool for PostgreSQL.
            thread_id: Thread/session identifier for file and execution isolation.
            allow_net: Whether to allow network access in Pyodide sandbox.
            stateful: Whether to maintain state between executions.
            timeout_seconds: Execution timeout in seconds.
        """
        super().__init__(db_pool, thread_id)
        self._executor = SandboxExecutor(
            db_pool,
            thread_id,
            allow_net=allow_net,
            stateful=stateful,
            timeout_seconds=timeout_seconds,
        )
        self._timeout_seconds = timeout_seconds

    def _run_async(self, coro: Any) -> Any:
        """Run async coroutine from sync context with execution timeout."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is None:
            return asyncio.run(coro)

        if self._loop is None:
            self._loop = running_loop
            self._loop_thread_id = threading.get_ident()

        if self._loop is None or not self._loop.is_running():
            return asyncio.run(coro)

        if threading.get_ident() == self._loop_thread_id:
            raise RuntimeError(
                "Synchronous sandbox backend method called from the event loop; "
                "use the async backend methods instead."
            )

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        # Use configured timeout plus buffer for cross-thread communication
        timeout = self._timeout_seconds + 10.0
        return future.result(timeout=timeout)

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend instance."""
        return f"mayflower:{self._thread_id}"

    # -------------------------------------------------------------------------
    # execute (SandboxBackendProtocol)
    # -------------------------------------------------------------------------

    # Sentinel prefix used by ToolCallContentMiddleware to pass extracted
    # Python code directly (e.g. "__PYTHON__\nprint('hello')").
    _PYTHON_SENTINEL = "__PYTHON__"

    def _parse_python_command(self, command: str) -> tuple[str, list[str]] | None:
        """Parse a python command to extract script path and arguments.

        Detects commands like:
        - python script.py
        - python3 script.py
        - python /path/to/script.py arg1 arg2

        Returns:
            Tuple of (script_path, args) if detected, None otherwise.
        """
        parts = command.strip().split()
        if not parts:
            return None

        if parts[0] not in ("python", "python3"):
            return None

        if len(parts) < 2:
            return None

        script_path = parts[1]
        if not script_path.endswith(".py"):
            return None

        args = parts[2:] if len(parts) > 2 else []
        return (script_path, args)

    @staticmethod
    def _extract_inline_python(command: str) -> str | None:
        """Extract Python code from ``python -c "..."`` or ``python3 -c '...'``.

        Returns the code string if the command matches, None otherwise.
        """
        match = re.match(r"^python3?\s+-c\s+(.+)$", command.strip(), re.DOTALL)
        if not match:
            return None

        code_part = match.group(1).strip()

        # Try shell-style unquoting (handles escaped quotes etc.)
        try:
            tokens = shlex.split(code_part)
            if tokens:
                return tokens[0]
        except ValueError:
            pass

        # Fallback: strip outer quotes manually
        if (code_part.startswith('"') and code_part.endswith('"')) or (
            code_part.startswith("'") and code_part.endswith("'")
        ):
            return code_part[1:-1]

        return code_part

    # Class-level store for files created during execute(), keyed by thread_id.
    # Consumed by the tool node to inject into LangGraph state.files.
    _pending_files_lock = threading.Lock()
    _pending_files_by_thread: dict[str, dict[str, Any]] = {}

    def _store_pending_files(self, result: Any) -> None:
        """Build files_update from created files and store for later consumption."""
        if not result.created_files:
            return
        files_update: dict[str, Any] = {}
        for file_path in result.created_files:
            try:
                record = self._run_async(self._vfs.read_file(file_path))
                content_bytes = record.get("content", b"") or b""
                content_str = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                logger.warning(
                    "Failed to read created file %s for files_update", file_path, exc_info=True
                )
                content_str = ""
            normalized = _normalize_path(file_path)
            files_update[normalized] = _create_file_data(content_str)
        if files_update:
            with MayflowerSandboxBackend._pending_files_lock:
                MayflowerSandboxBackend._pending_files_by_thread[self._thread_id] = files_update

    async def _astore_pending_files(self, result: Any) -> None:
        """Async version: build files_update from created files and store."""
        if not result.created_files:
            return
        files_update: dict[str, Any] = {}
        for file_path in result.created_files:
            try:
                record = await self._vfs.read_file(file_path)
                content_bytes = record.get("content", b"") or b""
                content_str = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                logger.warning(
                    "Failed to read created file %s for files_update", file_path, exc_info=True
                )
                content_str = ""
            normalized = _normalize_path(file_path)
            files_update[normalized] = _create_file_data(content_str)
        if files_update:
            with MayflowerSandboxBackend._pending_files_lock:
                MayflowerSandboxBackend._pending_files_by_thread[self._thread_id] = files_update

    @classmethod
    def consume_pending_files_update(cls, thread_id: str) -> dict[str, Any] | None:
        """Retrieve and clear pending files_update for a thread.

        Called by the tool node after execute() to inject created files into
        LangGraph state so they appear in the frontend Files panel.
        """
        with cls._pending_files_lock:
            return cls._pending_files_by_thread.pop(thread_id, None)

    def _execute_python_code(self, code: str) -> ExecuteResponse:
        """Execute Python code via Pyodide."""
        result = self._run_async(self._executor.execute(code))
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        py_exit_code = 0 if result.success else 1
        self._store_pending_files(result)
        return ExecuteResponse(output=output, exit_code=py_exit_code, truncated=False)

    async def _aexecute_python_code(self, code: str) -> ExecuteResponse:
        """Execute Python code via Pyodide (async)."""
        result = await self._executor.execute(code)
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        py_exit_code = 0 if result.success else 1
        await self._astore_pending_files(result)
        return ExecuteResponse(output=output, exit_code=py_exit_code, truncated=False)

    def _execute_shell(self, command: str) -> ExecuteResponse:
        """Execute shell command via BusyBox WASM sandbox."""
        result = self._run_async(self._executor.execute_shell(command))
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        exit_code: int | None = result.exit_code
        if exit_code is None:
            exit_code = 0 if result.success else 1
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command in the sandbox.

        Automatically routes:
        - ``__PYTHON__\\n<code>`` sentinel → Pyodide (direct code execution)
        - ``python -c "..."`` inline → Pyodide
        - ``python script.py`` or ``python3 script.py`` → Pyodide (file-based)
        - Other commands → BusyBox shell

        Args:
            command: Shell command string to execute.

        Returns:
            ExecuteResponse with combined output, exit code, and truncation flag.
        """
        # Handle __PYTHON__ sentinel (produced by ToolCallContentMiddleware)
        sentinel_prefix = f"{self._PYTHON_SENTINEL}\n"
        if command.startswith(sentinel_prefix):
            code = command[len(sentinel_prefix) :]
            logger.info("Routing __PYTHON__ sentinel to Pyodide (%d chars)", len(code))
            return self._execute_python_code(code)

        # Handle python -c "..." inline execution
        inline_code = self._extract_inline_python(command)
        if inline_code:
            logger.info("Routing python -c to Pyodide (%d chars)", len(inline_code))
            return self._execute_python_code(inline_code)

        # Handle python script.py (file-based execution)
        python_cmd = self._parse_python_command(command)
        if python_cmd:
            script_path, args = python_cmd
            try:
                record = self._run_async(self._vfs.read_file(script_path))
            except (FileNotFoundError, InvalidPathError):
                return ExecuteResponse(
                    output=f"python: can't open file '{script_path}': No such file",
                    exit_code=2,
                    truncated=False,
                )
            content_bytes = record.get("content", b"") or b""
            code = content_bytes.decode("utf-8", errors="replace")
            if args:
                argv_setup = f"import sys; sys.argv = [{repr(script_path)}, {', '.join(repr(a) for a in args)}]\n"
                code = argv_setup + code
            return self._execute_python_code(code)

        return self._execute_shell(command)

    async def aexecute(self, command: str) -> ExecuteResponse:
        """Async version of execute."""
        # Handle __PYTHON__ sentinel (produced by ToolCallContentMiddleware)
        sentinel_prefix = f"{self._PYTHON_SENTINEL}\n"
        if command.startswith(sentinel_prefix):
            code = command[len(sentinel_prefix) :]
            logger.info("Routing __PYTHON__ sentinel to Pyodide (%d chars)", len(code))
            return await self._aexecute_python_code(code)

        # Handle python -c "..." inline execution
        inline_code = self._extract_inline_python(command)
        if inline_code:
            logger.info("Routing python -c to Pyodide (%d chars)", len(inline_code))
            return await self._aexecute_python_code(inline_code)

        # Handle python script.py (file-based execution)
        python_cmd = self._parse_python_command(command)
        if python_cmd:
            script_path, args = python_cmd
            try:
                record = await self._vfs.read_file(script_path)
            except (FileNotFoundError, InvalidPathError):
                return ExecuteResponse(
                    output=f"python: can't open file '{script_path}': No such file",
                    exit_code=2,
                    truncated=False,
                )
            content_bytes = record.get("content", b"") or b""
            code = content_bytes.decode("utf-8", errors="replace")
            if args:
                argv_setup = f"import sys; sys.argv = [{repr(script_path)}, {', '.join(repr(a) for a in args)}]\n"
                code = argv_setup + code
            return await self._aexecute_python_code(code)

        result = await self._executor.execute_shell(command)
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        exit_code: int | None = result.exit_code
        if exit_code is None:
            exit_code = 0 if result.success else 1
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)

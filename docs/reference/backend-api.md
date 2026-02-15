# Backend API Reference

Mayflower Sandbox implements the [DeepAgents](https://github.com/mayflower/deepagents) `SandboxBackendProtocol` and `BackendProtocol` interfaces, providing two backend classes: `MayflowerSandboxBackend` (full execution + file operations) and `PostgresBackend` (file operations only).

Every public method has both sync and async variants: `method()` and `amethod()`.

## MayflowerSandboxBackend

Full sandbox backend with Python and shell execution, plus file operations.

### Constructor

```python
from mayflower_sandbox import MayflowerSandboxBackend

backend = MayflowerSandboxBackend(
    db_pool: Any,              # asyncpg connection pool
    thread_id: str,            # unique session/user identifier
    *,
    allow_net: bool = False,   # allow network access in Pyodide
    stateful: bool = True,     # persist variables across executions
    timeout_seconds: float = 60.0,  # execution timeout
)
```

### Properties

```python
backend.id  # returns "mayflower:<thread_id>"
```

### execute / aexecute

Run shell commands or Python scripts. Routes automatically based on command pattern.

```python
def execute(self, command: str) -> ExecuteResponse
async def aexecute(self, command: str) -> ExecuteResponse
```

See [Command Routing](#command-routing) for how commands are dispatched.

### read / aread

Read file contents with optional line offset and limit.

```python
def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str
async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str
```

Returns the file content as a string with line numbers.

### write / awrite

Create a new file. Fails if the file already exists.

```python
def write(self, file_path: str, content: str) -> WriteResult
async def awrite(self, file_path: str, content: str) -> WriteResult
```

### edit / aedit

Replace a string in an existing file.

```python
def edit(
    self,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> EditResult
async def aedit(
    self,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> EditResult
```

### ls_info / als_info

List directory contents.

```python
def ls_info(self, path: str) -> list[FileInfo]
async def als_info(self, path: str) -> list[FileInfo]
```

### glob_info / aglob_info

Find files matching a glob pattern.

```python
def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]
async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]
```

### grep_raw / agrep_raw

Search file contents with regex.

```python
def grep_raw(
    self,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
) -> list[GrepMatch] | str
async def agrep_raw(
    self,
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
) -> list[GrepMatch] | str
```

### upload_files / aupload_files

Batch upload files as binary.

```python
def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]
async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]
```

### download_files / adownload_files

Batch download files as binary.

```python
def download_files(self, paths: list[str]) -> list[FileDownloadResponse]
async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]
```

### consume_pending_files_update

Class method to retrieve files created during execution, for injecting into LangGraph state.

```python
@classmethod
def consume_pending_files_update(cls, thread_id: str) -> dict[str, Any] | None
```

Pops and returns the pending files dict for a thread, or `None` if no files are pending.

## Command Routing

`execute()` automatically detects the command type and routes to the appropriate engine:

| Pattern | Routed to | Description |
|---------|-----------|-------------|
| `__PYTHON__\n<code>` | Pyodide | Sentinel used by `ToolCallContentMiddleware` |
| `python -c "print('hello')"` | Pyodide | Inline Python detection (shlex parsing) |
| `python script.py` / `python3 script.py arg1` | Pyodide | File-based execution, reads script from VFS |
| Everything else | BusyBox WASM | Shell execution with pipe support |

## PostgresBackend

File-only backend implementing `BackendProtocol`. No execution capabilities.

### Constructor

```python
from mayflower_sandbox import PostgresBackend

backend = PostgresBackend(
    db_pool: Any,     # asyncpg connection pool
    thread_id: str,   # unique session/user identifier
)
```

Supports the same file operation methods as `MayflowerSandboxBackend`: `read`, `write`, `edit`, `ls_info`, `glob_info`, `grep_raw`, `upload_files`, `download_files` (and their async variants).

### CompositeBackend Usage

Use `PostgresBackend` as a route in DeepAgents' `CompositeBackend` for persistent storage of specific paths:

```python
from deepagents.backends import CompositeBackend, StateBackend
from mayflower_sandbox import PostgresBackend

composite = CompositeBackend(
    default=StateBackend(runtime),
    routes={"/memories/": PostgresBackend(db_pool, thread_id)},
)
```

## Return Types

These types are defined in the [DeepAgents protocol](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/backends/protocol.py).

### ExecuteResponse

```python
@dataclass
class ExecuteResponse:
    output: str                    # combined stdout and stderr
    exit_code: int | None = None   # 0 = success, non-zero = failure
    truncated: bool = False        # output was truncated
```

### WriteResult

```python
@dataclass
class WriteResult:
    error: str | None = None
    path: str | None = None
    files_update: dict[str, Any] | None = None  # None for external backends
```

### EditResult

```python
@dataclass
class EditResult:
    error: str | None = None
    path: str | None = None
    files_update: dict[str, Any] | None = None  # None for external backends
    occurrences: int | None = None
```

### FileInfo

`FileInfo` is a `TypedDict`. Only `path` is required; other fields are best-effort.

```python
class FileInfo(TypedDict):
    path: str                          # required
    is_dir: NotRequired[bool]
    size: NotRequired[int]             # bytes
    modified_at: NotRequired[str]      # ISO timestamp
```

### FileUploadResponse

```python
@dataclass
class FileUploadResponse:
    path: str
    error: FileOperationError | None = None
```

### FileDownloadResponse

```python
@dataclass
class FileDownloadResponse:
    path: str
    content: bytes | None = None
    error: FileOperationError | None = None
```

### GrepMatch

`GrepMatch` is a `TypedDict`.

```python
class GrepMatch(TypedDict):
    path: str
    line: int     # 1-indexed
    text: str     # full line content
```

### FileOperationError

Standardized error codes for file upload/download operations:

```python
FileOperationError = Literal[
    "file_not_found",
    "permission_denied",
    "is_directory",
    "invalid_path",
]
```

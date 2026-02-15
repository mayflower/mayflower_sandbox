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

All return types are dataclasses:

### ExecuteResponse

```python
@dataclass
class ExecuteResponse:
    output: str = ""          # stdout + stderr combined
    exit_code: int = 0        # 0 = success
    truncated: bool = False   # output was truncated
```

### WriteResult

```python
@dataclass
class WriteResult:
    error: str | None = None
    path: str | None = None
    files_update: dict[str, Any] | None = None
```

### EditResult

```python
@dataclass
class EditResult:
    error: str | None = None
    path: str | None = None
    files_update: dict[str, Any] | None = None
    occurrences: int | None = None
```

### FileInfo

```python
@dataclass
class FileInfo:
    path: str = ""
    is_dir: bool = False
    size: int = 0
    modified_at: str = ""
```

### FileUploadResponse

```python
@dataclass
class FileUploadResponse:
    path: str = ""
    error: str | None = None
```

### FileDownloadResponse

```python
@dataclass
class FileDownloadResponse:
    path: str = ""
    content: bytes | None = b""
    error: str | None = None
```

### GrepMatch

```python
@dataclass
class GrepMatch:
    path: str = ""
    line: int = 0
    text: str = ""
```

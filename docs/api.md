# API Reference

Low-level API documentation for Mayflower Sandbox components.

## SandboxExecutor

Main executor for running Python code with VFS integration.

```python
from mayflower_sandbox import SandboxExecutor

executor = SandboxExecutor(
    db_pool: asyncpg.Pool,
    thread_id: str,
    allow_net: bool = False,
    timeout_seconds: float = 120.0
)
```

### Parameters

- `db_pool` (asyncpg.Pool): PostgreSQL connection pool
- `thread_id` (str): Unique identifier for thread/user isolation
- `allow_net` (bool): Enable network access in Pyodide
- `timeout_seconds` (float): Maximum execution time

### Methods

#### execute()

```python
result = await executor.execute(
    code: str,
    session_bytes: bytes | None = None,
    session_metadata: dict | None = None
) -> ExecutionResult
```

Execute Python code with automatic VFS sync.

**Returns:** `ExecutionResult` with fields:
- `success` (bool): Whether execution succeeded
- `stdout` (str): Standard output
- `stderr` (str): Standard error
- `result` (Any): Return value (if code returned something)
- `execution_time` (float): Time taken in seconds
- `created_files` (list[str] | None): Paths of files created
- `session_bytes` (bytes | None): Pyodide session state
- `session_metadata` (dict | None): Session metadata

## StatefulExecutor

Executor with persistent state across executions.

```python
from mayflower_sandbox.session import StatefulExecutor

executor = StatefulExecutor(
    db_pool: asyncpg.Pool,
    thread_id: str,
    allow_net: bool = False,
    timeout_seconds: float = 120.0
)
```

### Methods

#### execute()

```python
result = await executor.execute(code: str) -> ExecutionResult
```

Execute code with automatic state recovery and persistence.

#### reset_session()

```python
await executor.reset_session()
```

Clear all session state for this thread.

## VirtualFilesystem

PostgreSQL-backed virtual filesystem.

```python
from mayflower_sandbox.filesystem import VirtualFilesystem

vfs = VirtualFilesystem(db_pool: asyncpg.Pool, thread_id: str)
```

### Methods

#### read_file()

```python
content = await vfs.read_file(file_path: str) -> bytes
```

Read file from VFS. Raises `FileNotFoundError` if file doesn't exist.

#### write_file()

```python
await vfs.write_file(file_path: str, content: bytes)
```

Write file to VFS (20MB limit).

#### list_files()

```python
files = await vfs.list_files(prefix: str = "") -> list[str]
```

List all files, optionally filtered by prefix.

#### delete_file()

```python
await vfs.delete_file(file_path: str)
```

Delete file from VFS. Raises `FileNotFoundError` if file doesn't exist.

#### file_exists()

```python
exists = await vfs.file_exists(file_path: str) -> bool
```

Check if file exists.

#### get_file_metadata()

```python
metadata = await vfs.get_file_metadata(file_path: str) -> dict
```

Get file metadata (size, timestamps).

Returns dict with:
- `path` (str): File path
- `size` (int): Size in bytes
- `created_at` (datetime): Creation timestamp
- `updated_at` (datetime): Last update timestamp

#### get_all_files_for_pyodide()

```python
files = await vfs.get_all_files_for_pyodide() -> dict[str, bytes]
```

Get all files as dict for Pyodide mounting.

## SandboxManager

Manages sandbox sessions and lifecycle.

```python
from mayflower_sandbox.manager import SandboxManager

manager = SandboxManager(db_pool: asyncpg.Pool)
```

### Methods

#### create_session()

```python
await manager.create_session(
    thread_id: str,
    expiration_days: int = 180
)
```

Create new session.

#### get_or_create_session()

```python
await manager.get_or_create_session(thread_id: str)
```

Get existing session or create new one.

#### session_exists()

```python
exists = await manager.session_exists(thread_id: str) -> bool
```

Check if session exists.

#### get_session()

```python
session = await manager.get_session(thread_id: str) -> dict
```

Get session details.

Returns dict with:
- `thread_id` (str)
- `created_at` (datetime)
- `expires_at` (datetime)

#### extend_session()

```python
await manager.extend_session(thread_id: str, days: int = 180)
```

Extend session expiration.

#### delete_session()

```python
await manager.delete_session(thread_id: str)
```

Delete session and all associated files.

## FileServer

HTTP server for file downloads.

```python
from mayflower_sandbox.server import FileServer

server = FileServer(
    db_pool: asyncpg.Pool,
    host: str = "0.0.0.0",
    port: int = 8000
)
```

### Methods

#### run()

```python
server.run()
```

Start server (blocking).

### Endpoints

- `GET /health` - Health check
- `GET /files/{thread_id}/{file_path}` - Download file
- `GET /files/{thread_id}?prefix=/tmp/` - List files

## CleanupJob

Automatic cleanup of expired sessions.

```python
from mayflower_sandbox.cleanup import CleanupJob

cleanup = CleanupJob(
    db_pool: asyncpg.Pool,
    interval_seconds: int = 3600
)
```

### Methods

#### run_once()

```python
stats = await cleanup.run_once() -> dict
```

Run cleanup once.

Returns dict with:
- `sessions_deleted` (int)
- `files_deleted` (int)

#### start()

```python
cleanup.start()
```

Start periodic cleanup in background.

#### stop()

```python
cleanup.stop()
```

Stop periodic cleanup.

## Tools

### create_sandbox_tools()

```python
from mayflower_sandbox.tools import create_sandbox_tools

tools = create_sandbox_tools(
    db_pool: asyncpg.Pool,
    thread_id: str,
    allow_net: bool = True,
    timeout_seconds: float = 120.0
) -> list[BaseTool]
```

Create all 5 LangChain tools.

Returns list of:
- `ExecutePythonTool`
- `FileReadTool`
- `FileWriteTool`
- `FileListTool`
- `FileDeleteTool`

## Database Schema

### sandbox_sessions

```sql
CREATE TABLE sandbox_sessions (
    thread_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '180 days'
);
```

### sandbox_filesystem

```sql
CREATE TABLE sandbox_filesystem (
    thread_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content BYTEA NOT NULL CHECK (octet_length(content) <= 20971520),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (thread_id, file_path),
    FOREIGN KEY (thread_id) REFERENCES sandbox_sessions(thread_id) ON DELETE CASCADE
);
```

### sandbox_session_bytes

```sql
CREATE TABLE sandbox_session_bytes (
    thread_id TEXT PRIMARY KEY,
    session_bytes BYTEA NOT NULL,
    session_metadata JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (thread_id) REFERENCES sandbox_sessions(thread_id) ON DELETE CASCADE
);
```

## Helper Modules

Helper modules are automatically loaded into Pyodide VFS at `/home/pyodide/` and can be imported:

```python
# In Pyodide execution
from document.docx_ooxml import docx_extract_text
from document.pptx_ooxml import pptx_extract_text
from document.xlsx_helpers import xlsx_read_cells
from document.pdf_manipulation import pdf_merge
```

See [Helpers Reference](helpers.md) for complete documentation.

## Error Handling

All operations can raise:
- `FileNotFoundError` - File doesn't exist
- `ValueError` - Invalid parameters
- `RuntimeError` - Execution failed
- `asyncpg.PostgresError` - Database errors
- `asyncio.TimeoutError` - Execution timeout

## Related Documentation

- [Tools Reference](tools.md) - The 5 LangChain tools
- [Advanced Features](advanced.md) - Stateful execution, file server, cleanup
- [Examples](examples.md) - Complete working examples

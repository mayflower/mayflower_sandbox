# Advanced Features

## Stateful Execution

Execute Python code with persistent state across executions and application restarts.

### How It Works

Pyodide session state (pickled Python namespace) is stored in PostgreSQL and automatically restored on each execution.

### Usage

```python
from mayflower_sandbox.session import StatefulExecutor

# Create stateful executor
executor = StatefulExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    allow_net=False,
    timeout_seconds=120.0
)

# First execution - define variables and functions
result1 = await executor.execute("""
x = 42
y = 100

def calculate():
    return x + y

print(f"Sum: {calculate()}")
""")
print(result1.stdout)  # Output: Sum: 142

# Second execution - state persists!
result2 = await executor.execute("""
print(f"x is still {x}")
print(f"calculate() = {calculate()}")
x = 50  # Modify state
""")
print(result2.stdout)
# Output:
# x is still 42
# calculate() = 142

# Third execution - modified state persists
result3 = await executor.execute("""
print(f"x is now {x}")
print(f"calculate() = {calculate()}")
""")
print(result3.stdout)
# Output:
# x is now 50
# calculate() = 150

# Reset session to clear all state
await executor.reset_session()
```

### State Persistence

State survives:
- Multiple executions
- Application restarts
- Database connections being reset

State is stored per `thread_id`, so each user/session has isolated state.

### Resetting State

```python
# Clear all state for this thread
await executor.reset_session()

# Next execution starts fresh
await executor.execute("print(x)")  # NameError: x is not defined
```

## File Server

Serve files from the VFS via HTTP for download.

### Starting the Server

```python
from mayflower_sandbox.server import FileServer

# Create server
server = FileServer(
    db_pool=db_pool,
    host="0.0.0.0",
    port=8080
)

# Start server (blocking)
server.run()
```

### Endpoints

#### Health Check

```bash
GET /health
```

Response:
```json
{"status": "ok"}
```

#### Download File

```bash
GET /files/{thread_id}/{file_path}
```

Example:
```bash
curl http://localhost:8080/files/user_123/tmp/data.csv
```

Returns the file content with appropriate `Content-Type` header.

#### List Files

```bash
GET /files/{thread_id}?prefix=/tmp/
```

Example:
```bash
curl "http://localhost:8080/files/user_123?prefix=/tmp/"
```

Returns JSON array of file paths:
```json
["/tmp/data.csv", "/tmp/config.json"]
```

## Automatic Cleanup

Configure automatic cleanup of expired sessions and files.

### Default Expiration

Sessions expire after **180 days** by default (configurable at session creation).

### Manual Cleanup

```python
from mayflower_sandbox.cleanup import CleanupJob

# Create cleanup job
cleanup = CleanupJob(db_pool)

# Run cleanup once
stats = await cleanup.run_once()
print(f"Deleted {stats['sessions_deleted']} sessions")
print(f"Deleted {stats['files_deleted']} files")
```

### Periodic Cleanup

```python
# Create cleanup job (runs every hour)
cleanup = CleanupJob(
    db_pool=db_pool,
    interval_seconds=3600  # 1 hour
)

# Start periodic cleanup in background
cleanup.start()

# Later: stop cleanup
cleanup.stop()
```

### Cleanup Logic

The cleanup job:
1. Finds expired sessions (`expires_at < NOW()`)
2. Deletes files for those sessions
3. Deletes session state
4. Deletes session records

## Session Management

### Creating Sessions

Sessions are created automatically when you use tools or executors:

```python
from mayflower_sandbox.manager import SandboxManager

manager = SandboxManager(db_pool)

# Create or get session (180 day expiration)
await manager.get_or_create_session("user_123")

# Create with custom expiration
await manager.create_session(
    thread_id="user_123",
    expiration_days=30
)
```

### Checking Session Status

```python
# Check if session exists
exists = await manager.session_exists("user_123")

# Get session details
session = await manager.get_session("user_123")
print(f"Created: {session['created_at']}")
print(f"Expires: {session['expires_at']}")
```

### Extending Session

```python
# Extend session by 180 days from now
await manager.extend_session("user_123", days=180)
```

### Deleting Sessions

```python
# Delete session and all associated files
await manager.delete_session("user_123")
```

## Performance Tuning

### Database Connection Pooling

Use connection pooling for better performance:

```python
import asyncpg

db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres",
    min_size=5,    # Minimum connections
    max_size=20,   # Maximum connections
    command_timeout=60.0
)
```

### Timeout Configuration

Adjust timeouts based on your workload:

```python
# Long-running data processing
executor = SandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    timeout_seconds=300.0  # 5 minutes
)

# Quick operations
executor = SandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    timeout_seconds=30.0  # 30 seconds
)
```

### File Size Limits

Files are limited to 20MB each, enforced at the database level:

```sql
-- In migrations/001_sandbox_schema.sql
CREATE TABLE sandbox_filesystem (
    ...
    content BYTEA CHECK (octet_length(content) <= 20971520),  -- 20MB
    ...
);
```

## Security Considerations

### Sandboxing

Python code runs in Pyodide WebAssembly sandbox:
- No access to host filesystem
- No access to host network (unless `allow_net=True`)
- No ability to execute system commands
- Memory limits enforced by WebAssembly

### Network Access Control

Control network access per executor:

```python
# Disable network (secure)
executor = SandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    allow_net=False
)

# Enable network (for micropip packages)
executor = SandboxExecutor(
    db_pool=db_pool,
    thread_id="user_123",
    allow_net=True
)
```

### Thread Isolation

Complete isolation between threads:
- Files are isolated by `thread_id`
- Sessions are isolated by `thread_id`
- State is isolated by `thread_id`
- No cross-thread access possible

### Path Validation

All file operations validate paths:
- Must be absolute paths
- No `..` directory traversal
- No invalid characters
- Length limits enforced

## Related Documentation

- [Tools Reference](../user-guide/tools.md) - The 10 LangChain tools
- [API Reference](../reference/api.md) - Low-level API documentation
- [Examples](../user-guide/examples.md) - Complete working examples

# Security Model

How Mayflower Sandbox isolates untrusted code execution and protects the host system.

## Sandboxing Layers

### 1. WebAssembly Sandbox

All code execution happens inside WebAssembly:

- **Python** runs in [Pyodide](https://pyodide.org/) (CPython compiled to WASM)
- **Shell commands** run in BusyBox compiled to WASM

WebAssembly provides memory isolation by design -- sandbox code cannot access the host filesystem, network, or memory space outside its own linear memory.

### 2. Deno Permission Model

Deno's permission system provides a second layer:

- `--allow-read` scoped to the executor script only
- `--allow-net` controlled per execution (`allow_net` parameter)
- No `--allow-write` to the host filesystem
- No `--allow-env` access to host environment

### 3. Worker-Based Isolation

Shell pipelines use separate Deno Workers per stage:

```
echo hello | cat | grep hello
     |         |         |
  Worker 1   Worker 2   Worker 3
```

Each Worker gets its own BusyBox WASM instance, preventing global-state contamination between pipeline stages.

## Thread Isolation

Complete data isolation between users/sessions via PostgreSQL `thread_id`:

- Files are partitioned by `thread_id` (composite primary key)
- Sessions are isolated by `thread_id`
- Execution state is isolated by `thread_id`
- No cross-thread access is possible at the database level

```sql
-- Files are always scoped to a thread
PRIMARY KEY (thread_id, file_path)
FOREIGN KEY (thread_id) REFERENCES sandbox_sessions(thread_id) ON DELETE CASCADE
```

## Path Validation

All file operations validate paths before accessing the database:

- Paths must be absolute (start with `/`)
- No `..` directory traversal components allowed
- No invalid or control characters
- Length limits enforced

Path validation happens in the backend layer before any VFS operation, preventing injection attacks regardless of how the backend is called.

## File Size Limits

Files are limited to **20MB** each, enforced at the database level:

```sql
content BYTEA NOT NULL CHECK (octet_length(content) <= 20971520)
```

This prevents denial-of-service through excessive storage consumption.

## Network Access Control

Network access is disabled by default and controlled per backend instance:

- **Default** (`allow_net=False`): No outbound network access from Pyodide
- **Enabled** (`allow_net=True`): Allows `cdn.jsdelivr.net` for micropip and the local MCP bridge (`127.0.0.1`)
- **MCP bridge**: Communication with MCP servers uses a localhost-only bridge, keeping Pyodide sandboxed
- **Custom hosts**: Use `MAYFLOWER_SANDBOX_NET_ALLOW` to whitelist additional hosts
- **MCP allowlist**: Use `MAYFLOWER_MCP_ALLOWLIST` to restrict which MCP servers can be bound

## Session Expiration

Sessions expire automatically after **180 days** (configurable). The `CleanupJob` periodically:

1. Finds expired sessions (`expires_at < NOW()`)
2. Deletes all associated files (cascading foreign key)
3. Deletes session state
4. Deletes session records

This prevents indefinite storage growth from abandoned sessions.

## MCP Security

When binding external MCP servers:

- `MAYFLOWER_MCP_ALLOWLIST` restricts which servers can be bound (name or host-suffix matching)
- MCP calls are rate-limited (`MAYFLOWER_MCP_CALL_INTERVAL`, default 0.1s)
- Sessions have a TTL (`MAYFLOWER_MCP_SESSION_TTL`, default 5 minutes)
- All MCP traffic is proxied through a localhost bridge -- Pyodide never contacts external servers directly

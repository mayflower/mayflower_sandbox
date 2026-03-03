# System Design

## Overview

Mayflower Sandbox is a production-ready Python sandbox with PostgreSQL-backed virtual filesystem, designed as a backend for [DeepAgents](https://github.com/mayflower/deepagents) applications. It provides secure, isolated Python and shell execution with persistent file storage.

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│ DeepAgents Framework                                    │
│ ├─ ToolCallContentMiddleware  (routes code to backend)  │
│ ├─ SkillsMiddleware           (discovers & loads skills)│
│ └─ CompositeBackend           (routes paths to backends)│
└──────────────────┬──────────────────────────────────────┘
                   │ SandboxBackendProtocol
┌──────────────────▼──────────────────────────────────────┐
│ MayflowerSandboxBackend                                 │
│ ├─ execute()    → routes to Pyodide or BusyBox          │
│ ├─ read/write/edit/ls_info/glob_info/grep_raw           │
│ └─ upload_files/download_files                          │
│                                                         │
│ PostgresBackend (file operations only)                  │
│ └─ Can be used standalone or in CompositeBackend routes │
├─────────────────────────────────────────────────────────┤
│ Core Engine                                             │
│ ├─ SandboxExecutor   (VFS + Pyodide integration)        │
│ ├─ ShellExecutor     (BusyBox WASM + pipes)             │
│ ├─ VirtualFilesystem (PostgreSQL storage)               │
│ ├─ Helper Modules    (auto-loaded into VFS)             │
│ ├─ SandboxManager    (session lifecycle)                │
│ └─ CleanupJob        (automatic expiration)             │
├─────────────────────────────────────────────────────────┤
│ Infrastructure                                          │
│ ├─ PostgreSQL  (persistent storage)                     │
│ ├─ Deno + Pyodide    (Python execution)                 │
│ └─ Deno + BusyBox WASM (shell execution)                │
└─────────────────────────────────────────────────────────┘
```

## Core Components

### MayflowerSandboxBackend

The primary entry point implementing `SandboxBackendProtocol`. Provides:

- **Command routing**: Automatically dispatches to Pyodide or BusyBox based on command pattern
- **File operations**: Full CRUD on the PostgreSQL-backed virtual filesystem
- **Sync/async duality**: Every method available as `method()` and `amethod()`

### PostgresBackend

A lighter backend implementing `BackendProtocol` for file operations only (no execution). Useful as a route in `CompositeBackend` for persistent storage of specific paths.

### SandboxExecutor

The execution engine that coordinates:

- **VFS Synchronization**: Files loaded from PostgreSQL before execution and saved after
- **Pyodide Integration**: Manages Deno subprocess running Pyodide WebAssembly
- **Helper Loading**: Automatically loads helper modules into VFS at `/home/pyodide/`
- **Worker Pool**: Optionally uses persistent Deno workers for 70--95% performance improvement

### ShellExecutor

BusyBox WASM-based shell execution:

- **BusyBox WASM**: Compiled BusyBox running in WebAssembly
- **VFS Integration**: Files from PostgreSQL mounted into MEMFS
- **Pipe Support**: Full pipe support via Worker-based isolation (`echo | cat | grep`)
- **SharedArrayBuffer**: Ring buffer pipes with Atomics for synchronization
- **Command Chaining**: Supports `&&`, `||`, and `;` operators

### VirtualFilesystem (VFS)

PostgreSQL-backed file storage providing:

- **Persistence**: Files survive application restarts
- **Thread Isolation**: Complete separation via `thread_id`
- **Size Limits**: 20MB per file, enforced at database level
- **Path Validation**: Prevents directory traversal attacks

### SandboxManager

Session lifecycle management:

- **Session Creation**: Auto-created on first use
- **Expiration**: 180-day default TTL
- **Cleanup**: Automatic cleanup of expired sessions and files

### Worker Pool

Optional performance optimization:

- **3 persistent Deno workers** (configurable)
- **Pyodide pre-loaded** in each worker
- **Round-robin** load balancing
- **Health monitoring** and auto-recovery
- **70--95% faster** than one-shot execution

## Database Schema

| Table | Purpose |
|-------|---------|
| `sandbox_sessions` | Session tracking with expiration |
| `sandbox_filesystem` | File storage (20MB per file) |
| `sandbox_session_bytes` | Serialized Pyodide session state |
| `sandbox_skills` | Installed Claude Skills metadata |
| `sandbox_mcp_servers` | MCP server bindings |

See [Configuration Reference](../reference/configuration.md) for full schema.

## Execution Flow

### Code Execution

```
1. Backend receives command via execute()
2. Command routing detects type (Python/shell)
3. VFS files loaded from PostgreSQL
4. Files serialized to JSON for Deno
5. Deno subprocess spawned (or pool worker used)
6. Pyodide loads Python runtime / BusyBox processes command
7. Files mounted in execution environment
8. Code executed
9. Modified files extracted
10. Files saved back to PostgreSQL
11. ExecuteResponse returned
```

### File Operations

```
1. Backend receives file operation (read/write/edit/...)
2. Path validated (no traversal)
3. PostgreSQL query executed via VirtualFilesystem
4. Result returned as typed dataclass
```

## Thread Isolation

All operations use `thread_id` for isolation:

```
Thread A (user_123)          Thread B (user_456)
├── /tmp/data.csv            ├── /tmp/data.csv
├── /tmp/script.py           ├── /tmp/report.pdf
└── session state            └── session state

(Completely separate -- no cross-access possible)
```

## Helper Module System

1. Helpers discovered in `src/mayflower_sandbox/helpers/`
2. Recursively scanned for `.py` files
3. Loaded into VFS at `/home/pyodide/<path>`
4. Available for import in Pyodide code

Categories: `document/` (Word, Excel, PowerPoint, PDF), `data/`, `web/`, `utils/`.

## Performance Characteristics

### Without Worker Pool (Legacy)

- Cold start: ~4--5 seconds (Pyodide loading)
- File operations: < 50ms
- Helper loading: < 100ms

### With Worker Pool (Recommended)

- First request: ~5 seconds (pool initialization)
- Subsequent requests: 0.2--2 seconds
- 70--95% improvement

See [Performance](performance.md) for benchmarks and tuning.

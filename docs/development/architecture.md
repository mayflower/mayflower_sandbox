# Architecture

## Overview

Mayflower Sandbox is a production-ready Python sandbox with PostgreSQL-backed virtual filesystem, designed for LangGraph agents. It provides secure, isolated Python code execution with persistent file storage.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      LangGraph Agent                             │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    LangChain Tools (12)                      ││
│  │  ┌────────────┐ ┌────────────┐ ┌────────────────────────┐   ││
│  │  │ Execution  │ │    File    │ │    Skills & MCP        │   ││
│  │  │ python_run │ │ file_read  │ │ skill_install          │   ││
│  │  │ python_run_│ │ file_write │ │ mcp_bind_http          │   ││
│  │  │ file       │ │ file_edit  │ │                        │   ││
│  │  │ python_run_│ │ file_list  │ └────────────────────────┘   ││
│  │  │ prepared   │ │ file_delete│                               ││
│  │  └────────────┘ │ file_glob  │                               ││
│  │                 │ file_grep  │                               ││
│  │                 └────────────┘                               ││
│  └─────────────────────────────────────────────────────────────┘│
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    Mayflower Sandbox Core                        │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐ │
│  │SandboxExecutor │  │ VirtualFile-   │  │  SandboxManager    │ │
│  │ • VFS sync     │  │ system (VFS)   │  │  • Session life-   │ │
│  │ • Pyodide exec │  │ • PostgreSQL   │  │    cycle           │ │
│  │ • Helper load  │  │   storage      │  │  • Expiration      │ │
│  └────────┬───────┘  │ • 20MB limit   │  │  • Cleanup         │ │
│           │          │ • Thread iso-  │  └────────────────────┘ │
│           │          │   lation       │                         │
│           │          └───────┬────────┘                         │
│  ┌────────▼───────┐          │                                  │
│  │  Worker Pool   │          │                                  │
│  │ • 3 Deno procs │          │                                  │
│  │ • Pyodide pre- │          │                                  │
│  │   loaded       │          │                                  │
│  │ • Round-robin  │          │                                  │
│  └────────────────┘          │                                  │
└────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                      Infrastructure                              │
│  ┌────────────────────────┐  ┌────────────────────────────────┐ │
│  │      PostgreSQL        │  │      Deno + Pyodide            │ │
│  │ • sandbox_sessions     │  │ • WebAssembly sandbox          │ │
│  │ • sandbox_filesystem   │  │ • Python 3.12 runtime          │ │
│  │ • sandbox_session_bytes│  │ • micropip for packages        │ │
│  │ • sandbox_skills       │  │                                │ │
│  │ • sandbox_mcp_servers  │  │                                │ │
│  └────────────────────────┘  └────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Core Components

### SandboxExecutor

The main execution engine that coordinates:
- **VFS Synchronization**: Files are loaded from PostgreSQL before execution and saved after
- **Pyodide Integration**: Manages Deno subprocess running Pyodide WebAssembly
- **Helper Loading**: Automatically loads helper modules into VFS at `/home/pyodide/`
- **Worker Pool**: Optionally uses persistent Deno workers for 70-95% performance improvement

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
- **70-95% faster** than one-shot execution

## Database Schema

### sandbox_sessions
Tracks active sessions with expiration.

```sql
CREATE TABLE sandbox_sessions (
    thread_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '180 days'
);
```

### sandbox_filesystem
Stores files with 20MB limit per file.

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
Stores Pyodide session state for stateful execution.

```sql
CREATE TABLE sandbox_session_bytes (
    thread_id TEXT PRIMARY KEY,
    session_bytes BYTEA NOT NULL,
    session_metadata JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (thread_id) REFERENCES sandbox_sessions(thread_id) ON DELETE CASCADE
);
```

### sandbox_skills
Tracks installed Claude Skills.

### sandbox_mcp_servers
Stores MCP server bindings.

## Execution Flow

### Code Execution

```
1. Tool receives code from LLM
2. SandboxExecutor.execute() called
3. VFS files loaded from PostgreSQL
4. Files serialized to JSON
5. Deno subprocess spawned (or worker pool used)
6. Pyodide loads Python runtime
7. Files mounted in Pyodide VFS
8. Python code executed
9. Modified files extracted
10. Files saved back to PostgreSQL
11. Result returned to tool
```

### File Operations

```
1. Tool receives file operation
2. VirtualFilesystem method called
3. Path validated (no traversal)
4. PostgreSQL query executed
5. Result returned
```

## Security Model

### Sandboxing Layers

1. **WebAssembly**: Pyodide runs in WebAssembly sandbox
2. **Deno Permissions**: Network access controlled via `--allow-net`
3. **Thread Isolation**: Each thread_id has isolated filesystem
4. **Path Validation**: All paths validated before use
5. **Size Limits**: 20MB per file prevents abuse
6. **HITL Approval**: Destructive operations require user consent

### Network Access Control

- Default: Network disabled
- `allow_net=True`: Enables network for micropip
- CDN traffic allowed for package installation
- MCP bridge communication via localhost only

## Helper Module System

### Loading

1. Helpers discovered in `src/mayflower_sandbox/helpers/`
2. Recursively scan for `.py` files
3. Load into VFS at `/home/pyodide/<path>`
4. Available for import in Pyodide code

### Available Categories

- **document/**: Word, Excel, PowerPoint, PDF processing
- **data/**: CSV, JSON processing (placeholder)
- **web/**: HTML/markdown utilities (placeholder)
- **utils/**: General utilities (placeholder)

## Thread Isolation

All operations use `thread_id` for isolation:

```
Thread A (user_123)          Thread B (user_456)
├── /tmp/data.csv            ├── /tmp/data.csv
├── /tmp/script.py           ├── /tmp/report.pdf
└── session state            └── session state

(Completely separate - no cross-access possible)
```

## Performance Characteristics

### Without Worker Pool (Legacy)
- Cold start: ~4-5 seconds (Pyodide loading)
- File operations: < 50ms
- Helper loading: < 100ms

### With Worker Pool (Recommended)
- First request: ~5 seconds (pool initialization)
- Subsequent requests: 0.2-2 seconds
- 70-95% improvement

## Related Documentation

- [Tools Reference](../user-guide/tools.md) - The 12 LangChain tools
- [API Reference](../reference/api.md) - Low-level API documentation
- [Worker Pool](../advanced/worker-pool.md) - Performance optimization
- [MCP Integration](../advanced/mcp.md) - Skills and MCP servers

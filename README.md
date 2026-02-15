# Mayflower Sandbox

[![CI](https://github.com/mayflower/mayflower_sandbox/actions/workflows/ci.yml/badge.svg)](https://github.com/mayflower/mayflower_sandbox/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue.svg)](https://mypy-lang.org/)
[![Security: Bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://bandit.readthedocs.io/)
[![SBOM: CycloneDX](https://img.shields.io/badge/SBOM-CycloneDX-blue.svg)](https://cyclonedx.org/)

Production-ready Python sandbox implementing the [DeepAgents](https://github.com/mayflower/deepagents) `SandboxBackendProtocol`, with PostgreSQL-backed virtual filesystem and document processing helpers.

## Overview

Mayflower Sandbox provides secure, isolated Python and shell execution with persistent file storage. It implements the `SandboxBackendProtocol` and `BackendProtocol` interfaces from DeepAgents, making it a drop-in backend for any DeepAgents-based application. Execute untrusted Python code via Pyodide WebAssembly, run shell commands via BusyBox WASM, process documents (Word, Excel, PowerPoint, PDF), and maintain persistent state across sessions -- all with complete thread isolation.

## Key Features

- **Secure Python Execution** -- Pyodide WebAssembly sandbox with configurable network access
- **Shell Execution** -- BusyBox WASM sandbox with pipe support (`echo | cat | grep`)
- **Persistent Virtual Filesystem** -- PostgreSQL-backed storage (20MB per file)
- **Document Processing** -- Built-in helpers for Word, Excel, PowerPoint, and PDF
- **Stateful Execution** -- Variables and state persist across executions and restarts
- **Thread Isolation** -- Complete isolation between users/sessions via `thread_id`
- **DeepAgents Integration** -- Implements `SandboxBackendProtocol` and `BackendProtocol`
- **Skills & MCP** -- Install Claude Skills and bind MCP servers as typed Python code
- **Worker Pool** -- 70-95% faster execution by keeping Pyodide loaded in memory
- **Automatic Cleanup** -- Configurable session expiration (180 days default)

## Quick Start

### Installation

```bash
# Install Deno (required for Pyodide and BusyBox WASM)
curl -fsSL https://deno.land/x/install/install.sh | sh

# Install package
pip install -e .

# Setup database
createdb mayflower_test
psql -d mayflower_test -f migrations/001_sandbox_schema.sql
```

### Basic Usage

```python
import asyncpg
from mayflower_sandbox import MayflowerSandboxBackend

db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres",
)

backend = MayflowerSandboxBackend(
    db_pool,
    thread_id="user_123",
    allow_net=False,
    stateful=True,
    timeout_seconds=60.0,
)

# Execute Python
result = await backend.aexecute("python /tmp/script.py")
print(result.output, result.exit_code)

# Execute shell commands
result = await backend.aexecute("echo hello | grep hello")

# File operations
await backend.awrite("/tmp/data.csv", "name,value\nfoo,42")
content = await backend.aread("/tmp/data.csv")
files = await backend.als_info("/tmp")
```

## Architecture

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

## Backend API

### SandboxBackendProtocol Methods

`MayflowerSandboxBackend` implements the full `SandboxBackendProtocol` interface. Every method has both sync and async variants (`method()` / `amethod()`).

| Method | Description |
|--------|-------------|
| `execute(command)` | Run shell commands or `python script.py` |
| `read(path, offset, limit)` | Read file with line numbers |
| `write(path, content)` | Create new file (fails if exists) |
| `edit(path, old, new)` | Replace string in file |
| `ls_info(path)` | List directory |
| `glob_info(pattern, path)` | Find files by pattern |
| `grep_raw(pattern, path)` | Search file contents |
| `upload_files(files)` | Batch upload `list[tuple[path, bytes]]` |
| `download_files(paths)` | Batch download |

### Command Routing

`execute()` automatically detects the command type:

| Pattern | Routed to |
|---------|-----------|
| `python script.py` / `python3 script.py arg1` | Pyodide (file-based) |
| `python -c "print('hello')"` | Pyodide (inline) |
| `__PYTHON__\n<code>` sentinel | Pyodide (direct, used by ToolCallContentMiddleware) |
| Everything else | BusyBox WASM shell |

### PostgresBackend (File-Only)

`PostgresBackend` implements `BackendProtocol` for file operations without execution. Use it standalone or as a route in `CompositeBackend`:

```python
from deepagents.backends import CompositeBackend, StateBackend
from mayflower_sandbox import PostgresBackend

# Persistent storage for /memories/, in-memory for everything else
composite = CompositeBackend(
    default=StateBackend(runtime),
    routes={"/memories/": PostgresBackend(db_pool, thread_id)},
)
```

## Shell Execution

BusyBox WASM provides Unix shell execution:

- **Commands:** `echo`, `cat`, `grep`, `wc`, `ls`, `mkdir`, `rm`, `sed`, `awk`, etc.
- **Pipes:** `echo hello | cat | grep hello`
- **Chaining:** `cmd1 && cmd2`, `cmd1 ; cmd2`
- **Redirections:** `>`, `>>`, `<`

Each pipe stage runs in a separate Deno Worker with SharedArrayBuffer ring buffers.

## Document Processing Helpers

Built-in helpers are automatically available inside the sandbox at `/home/pyodide/`:

- **Word (DOCX)** -- Extract text, read tables, find/replace, add comments, convert to markdown
- **Excel (XLSX)** -- Read/write cells, convert to dictionaries, detect formulas
- **PowerPoint (PPTX)** -- Extract text, replace content, inventory slides, generate HTML
- **PDF** -- Merge, split, extract text, rotate pages, get metadata

## Skills & MCP Integration

Skills and MCP servers are managed via direct function calls in `mayflower_sandbox.integrations`:

```python
from mayflower_sandbox.integrations import install_skill, add_http_mcp_server

# Install a Claude Skill from GitHub
skill = await install_skill(db_pool, thread_id, "github:anthropics/skills/algorithmic-art")

# Bind an MCP server -- generates typed Python wrappers
server = await add_http_mcp_server(
    db_pool, thread_id,
    name="deepwiki",
    url="https://mcp.deepwiki.com/mcp",
)
```

After binding, the LLM writes Python code that calls typed functions (no tool-call tokens):

```python
# Generated wrapper code, available inside the sandbox:
from servers.deepwiki import read_wiki_structure
result = await read_wiki_structure(repoName="langchain-ai/langchain")
```

This follows the [Code Mode](https://blog.cloudflare.com/code-mode/) pattern -- LLMs write typed Python instead of tool calls, improving batching and context efficiency.

In DeepAgents, skills are discovered via `SkillsMiddleware` which uses the backend's file operations (`ls_info`, `download_files`) and executes scripts via `backend.execute()`.

## Performance

### Worker Pool (Default in Production)

The worker pool keeps Pyodide loaded in memory for **70-95% faster execution**:

| Operation | Without Pool | With Pool | Improvement |
|-----------|-------------|-----------|-------------|
| Simple code | 4.5s | **0.5s** | 89% faster |
| With numpy | 4.5s | **0.2s** | 96% faster |
| With matplotlib | 14s | **1.5s** | 89% faster |

```bash
export PYODIDE_USE_POOL=true          # Enable (recommended)
export PYODIDE_POOL_SIZE=3            # Number of workers (default: 3)
export PYODIDE_WORKER_REQUEST_LIMIT=1000  # Recycle after N requests
export PYODIDE_HEALTH_CHECK_INTERVAL=30   # Health check seconds
```

### Legacy Mode

Without the pool (`PYODIDE_USE_POOL=false`), each execution starts a fresh Deno process (~4-5s per execution).

## Testing

```bash
# Setup PostgreSQL and run migrations
make db-setup

# Install dependencies
uv venv && uv pip install -e ".[dev]"

# Run tests
POSTGRES_PORT=5433 uv run pytest -v

# Run quality checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/ --ignore-missing-imports

# Stop database
make db-down
```

## Configuration

### Environment Variables

```bash
export POSTGRES_HOST=localhost
export POSTGRES_DB=mayflower_test
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export POSTGRES_PORT=5432
```

### Database Schema

- **sandbox_sessions** -- Session tracking (180-day expiration)
- **sandbox_filesystem** -- File storage (20MB per file limit)
- **sandbox_session_bytes** -- Stateful execution support

## Security

- WebAssembly sandboxing (Pyodide for Python, BusyBox WASM for shell)
- Worker-based isolation (each shell pipeline stage in separate Deno Worker)
- Path validation (prevents directory traversal)
- Size limits (20MB per file)
- Thread isolation (complete separation via PostgreSQL)
- Configurable network access
- Automatic session expiration

## License

MIT

## Related Projects

- [DeepAgents](https://github.com/mayflower/deepagents) -- Agent framework with `SandboxBackendProtocol`
- [Pyodide](https://pyodide.org/) -- Python in WebAssembly
- [BusyBox](https://busybox.net/) -- Unix utilities in a single executable

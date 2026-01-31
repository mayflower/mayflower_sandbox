# Mayflower Sandbox

[![CI](https://github.com/mayflower/mayflower_sandbox/actions/workflows/ci.yml/badge.svg)](https://github.com/mayflower/mayflower_sandbox/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue.svg)](https://mypy-lang.org/)

Production-ready Python sandbox with PostgreSQL-backed virtual filesystem and document processing helpers for LangGraph agents.

## Overview

Mayflower Sandbox provides secure, isolated Python code execution with persistent file storage, designed for LangChain and LangGraph applications. Execute untrusted Python code, process documents (Word, Excel, PowerPoint, PDF), and maintain persistent state across sessions—all with complete thread isolation.

## Key Features

- ✅ **Secure Python Execution** - Pyodide WebAssembly sandbox with configurable network access
- ✅ **Shell Execution** - BusyBox WASM sandbox with pipe support (`echo | cat | grep`)
- ✅ **Persistent Virtual Filesystem** - PostgreSQL-backed storage (20MB file limit per file)
- ✅ **Document Processing Helpers** - Built-in helpers for Word, Excel, PowerPoint, and PDF
- ✅ **Stateful Execution** - Variables and state persist across executions and restarts
- ✅ **Thread Isolation** - Complete isolation between users/sessions via `thread_id`
- ✅ **LangChain Integration** - All tools extend `BaseTool` for seamless LangGraph integration
- ✅ **DeepAgents Integration** - `SandboxBackendProtocol` adapter for DeepAgents framework
- ✅ **HITL Support** - Human-in-the-Loop approval for destructive operations (CopilotKit integration)
- ✅ **HTTP File Server** - Download files via REST API
- ✅ **Automatic Cleanup** - Configurable session expiration (180 days default)

## Quick Start

### Installation

```bash
# Install Deno (required for Pyodide)
curl -fsSL https://deno.land/x/install/install.sh | sh

# Install package
pip install -e .

# Setup database
createdb mayflower_test
psql -d mayflower_test -f migrations/001_sandbox_schema.sql
```

See [Installation Guide](docs/getting-started/installation.md) for detailed setup instructions.

### Basic Usage

```python
import asyncpg
from mayflower_sandbox.tools import create_sandbox_tools
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

# Setup database
db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres"
)

# Create tools for a specific user
tools = create_sandbox_tools(db_pool, thread_id="user_123")

# Create LangGraph agent
llm = ChatAnthropic(model="claude-sonnet-4.5")
agent = create_react_agent(llm, tools)

# Use the agent
result = await agent.ainvoke({
    "messages": [("user", "Create a CSV file and calculate the sum")]
})
```

See [Quick Start Guide](docs/getting-started/quickstart.md) for a complete tutorial.

## Documentation

### Getting Started
- **[Installation Guide](docs/getting-started/installation.md)** - Install and configure Mayflower Sandbox
- **[Quick Start](docs/getting-started/quickstart.md)** - Get started in 5 minutes
- **[Examples](docs/user-guide/examples.md)** - Complete working examples

### Reference
- **[Tools Reference](docs/user-guide/tools.md)** - Documentation for the 12 LangChain tools
- **[Helpers Reference](docs/user-guide/helpers.md)** - Document processing helpers (Word, Excel, PowerPoint, PDF)
- **[HITL Guide](#human-in-the-loop-hitl-approval)** - Human-in-the-Loop approval for destructive operations
- **[Advanced Features](docs/advanced/stateful-execution.md)** - Stateful execution, file server, cleanup
- **[Worker Pool](docs/advanced/worker-pool.md)** - Performance optimization with worker pool
- **[MCP Integration](docs/advanced/mcp.md)** - Model Context Protocol support
- **[API Reference](docs/reference/api.md)** - Low-level API documentation

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ LangGraph Agent              OR      DeepAgents     │
│ ├─ ExecutePythonTool                 Framework      │
│ ├─ RunPythonFileTool          ┌──────────────────┐  │
│ ├─ ExecuteCodeTool            │ MayflowerSandbox │  │
│ ├─ FileReadTool               │ Backend          │  │
│ ├─ FileWriteTool              │ (implements      │  │
│ ├─ FileEditTool               │  SandboxBackend  │  │
│ ├─ FileListTool               │  Protocol)       │  │
│ ├─ FileDeleteTool             └──────────────────┘  │
│ ├─ FileGlobTool                                     │
│ └─ FileGrepTool                                     │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│ Mayflower Sandbox                                   │
│ ├─ SandboxExecutor (VFS + Pyodide integration)     │
│ ├─ ShellExecutor (BusyBox WASM + pipes)            │
│ ├─ VirtualFilesystem (PostgreSQL storage)          │
│ ├─ Helper Modules (auto-loaded into VFS)           │
│ ├─ SandboxManager (Session lifecycle)              │
│ └─ CleanupJob (Automatic expiration)               │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│ Infrastructure                                      │
│ ├─ PostgreSQL (Persistent storage)                 │
│ ├─ Deno + Pyodide (Python execution)               │
│ └─ Deno + BusyBox WASM (Shell execution)           │
└─────────────────────────────────────────────────────┘
```

## The 12 Tools

Mayflower Sandbox provides 12 LangChain tools:

### Code Execution Tools

1. **ExecutePythonTool** (`python_run`) - Execute Python code directly via tool parameter
   - Best for: Small code snippets, simple calculations, quick operations
   - Code passed as tool parameter (subject to serialization limits)

2. **RunPythonFileTool** (`python_run_file`) - Execute existing Python files from VFS
   - Best for: Re-running scripts, organized multi-file projects
   - Reads and executes .py files already stored in VFS

3. **ExecuteCodeTool** (`python_run_prepared`) - Execute code from graph state (state-based extraction)
   - Best for: Large/complex code (20+ lines), subplots, multi-step analysis
   - Solves AG-UI/LangGraph tool parameter serialization issues
   - LLM generates code, stores in graph state, tool extracts and executes
   - **Use this for complex visualizations and large code blocks**

### File Management Tools

4. **FileReadTool** (`file_read`) - Read files from PostgreSQL VFS
5. **FileWriteTool** (`file_write`) - Write files to PostgreSQL VFS (20MB limit, HITL approval)
6. **FileEditTool** (`file_edit`) - Edit files by replacing unique strings
7. **FileListTool** (`file_list`) - List files with optional prefix filtering
8. **FileDeleteTool** (`file_delete`) - Delete files from VFS (HITL approval required)

### File Search Tools

9. **FileGlobTool** (`file_glob`) - Find files matching glob patterns
10. **FileGrepTool** (`file_grep`) - Search file contents with regex

### Skills & MCP Tools (Code Mode)

11. **SkillInstallTool** (`skill_install`) - Install Claude Skills into sandbox
12. **MCPBindHttpTool** (`mcp_bind_http`) - Bind Streamable HTTP MCP servers

**Code Mode Pattern:** MCP tools are converted to **typed local Python code** rather than tool-call tokens. This follows [Cloudflare's Code Mode](https://blog.cloudflare.com/code-mode/) approach—LLMs write Python code that calls typed functions with IDE-style autocompletion, improving batching and context efficiency.

```python
# After binding an MCP server, LLM writes code like:
from servers.deepwiki import read_wiki_structure
result = await read_wiki_structure(repoName="langchain-ai/langchain")
```

See [MCP Integration Guide](docs/advanced/mcp.md) for detailed documentation.

### When to Use Which Execution Tool?

**Use `python_run`** for:
- Simple calculations and data processing
- Code under ~10 lines
- Quick operations where code fits comfortably in tool parameters

**Use `python_run_file`** for:
- Re-running previously created scripts
- Organized multi-file projects
- Scripts stored permanently in VFS

**Use `python_run_prepared`** for:
- Complex visualizations with subplots
- Large code blocks (20+ lines)
- Multi-step data analysis pipelines
- When you encounter "missing required parameter" errors with `python_run`
- Any code too large for tool parameter serialization

**State-Based Code Execution Pattern (`python_run_prepared`):**

The `python_run_prepared` tool solves a critical issue with LangGraph/AG-UI: when LLMs try to pass large code blocks through tool parameters, the serialization layer can drop or truncate them, causing "missing required parameter" errors.

How it works:
1. LLM generates Python code (automatically stored in graph state's `pending_code` field)
2. LLM calls `python_run_prepared(file_path="/tmp/viz.py", description="Create subplot visualization")`
3. Tool extracts code from state, saves to VFS, and executes
4. Code is cleared from state after successful execution

This pattern enables complex visualizations and large-scale data processing without serialization limits.

## DeepAgents Backend

Mayflower Sandbox provides a `SandboxBackendProtocol` adapter for the [DeepAgents](https://github.com/mayflower/deepagents) framework, enabling secure Python and shell execution in DeepAgents-powered applications.

### SandboxBackendProtocol Methods

The `MayflowerSandboxBackend` class implements all methods required by DeepAgents:

| Method | Async Version | Description |
|--------|---------------|-------------|
| `execute(command)` | `aexecute()` | Run shell commands or Python (with `__PYTHON__` prefix) |
| `read(path, offset, limit)` | `aread()` | Read file content with line numbers |
| `write(path, content)` | `awrite()` | Create new file (fails if exists) |
| `edit(path, old, new, replace_all)` | `aedit()` | Replace string in existing file |
| `ls_info(path)` | `als_info()` | List directory contents |
| `glob_info(pattern, path)` | `aglob_info()` | Find files matching glob pattern |
| `grep_raw(pattern, path, glob)` | `agrep_raw()` | Search file contents with regex |
| `upload_files(files)` | `aupload_files()` | Batch upload files |
| `download_files(paths)` | `adownload_files()` | Batch download files |

### Usage

```python
import asyncpg
from mayflower_sandbox.deepagents_backend import MayflowerSandboxBackend

# Create database pool
db_pool = await asyncpg.create_pool(
    host="localhost",
    database="mayflower_test",
    user="postgres",
    password="postgres"
)

# Create backend for a specific thread/user
backend = MayflowerSandboxBackend(
    db_pool,
    thread_id="user_123",
    allow_net=False,      # Enable network access for pip installs
    stateful=True,        # Preserve Python state across executions
    timeout_seconds=60.0  # Execution timeout
)

# Execute shell commands
result = await backend.aexecute("echo hello > /tmp/test.txt")
result = await backend.aexecute("cat /tmp/test.txt | grep hello")

# Execute Python code (use __PYTHON__ prefix)
result = await backend.aexecute("__PYTHON__\nprint('Hello from Python')")

# File operations
content = await backend.aread("/tmp/test.txt")
await backend.awrite("/tmp/new.txt", "content")
await backend.aedit("/tmp/test.txt", "hello", "world")

# Search operations
files = await backend.aglob_info("*.txt", "/tmp")
matches = await backend.agrep_raw("hello", path="/tmp")
```

### Shell Execution Features

The backend supports shell command execution via BusyBox WASM:

- **Basic commands:** `echo`, `cat`, `grep`, `wc`, `ls`, `mkdir`, `rm`, etc.
- **Pipes:** `echo hello | cat | grep hello`
- **Command chaining:** `cmd1 && cmd2`, `cmd1 ; cmd2`
- **Redirections:** `>`, `>>`, `<`

**Pipeline architecture:** Each pipe stage runs in a separate Deno Worker connected via SharedArrayBuffer ring buffers with Atomics synchronization.

## Document Processing Helpers

Built-in helpers for document processing (automatically available in sandbox):

- **Word (DOCX)** - Extract text, read tables, find/replace, add comments, convert to markdown
- **Excel (XLSX)** - Read/write cells, convert to dictionaries, detect formulas
- **PowerPoint (PPTX)** - Extract text, replace content, inventory slides, generate HTML
- **PDF** - Merge, split, extract text, rotate pages, get metadata

See [Helpers Reference](docs/helpers.md) for complete documentation.

## Human-in-the-Loop (HITL) Approval

Mayflower Sandbox supports Human-in-the-Loop approval for destructive operations, allowing users to confirm actions before they execute. This is particularly important for file deletions and modifications.

### How HITL Works

The HITL mechanism uses CopilotKit's `renderAndWaitForResponse` pattern to create a seamless approval workflow:

```
┌─────────┐      ┌─────────┐      ┌──────────┐      ┌──────┐
│   LLM   │─────▶│ Backend │─────▶│ Frontend │─────▶│ User │
└─────────┘      └─────────┘      └──────────┘      └──────┘
     │                │                  │               │
     │ 1. Call tool   │                  │               │
     │ (no approval)  │                  │               │
     ├───────────────▶│                  │               │
     │                │ 2. Return        │               │
     │                │ "WAIT_FOR_USER_  │               │
     │                │ APPROVAL"        │               │
     │◀───────────────┤                  │               │
     │                │ 3. Trigger       │               │
     │                │ approval dialog  │               │
     │                ├─────────────────▶│               │
     │                │                  │ 4. Show UI    │
     │                │                  ├──────────────▶│
     │                │                  │ 5. User       │
     │                │                  │ approves      │
     │                │                  │◀──────────────┤
     │                │ 6. Re-call with │               │
     │                │ approved=true   │               │
     │                │◀─────────────────┤               │
     │ 7. Execute     │                  │               │
     │ & return       │                  │               │
     │◀───────────────┤─────────────────▶│──────────────▶│
```

### Implementation Example

**Backend (file_delete.py):**

```python
class FileDeleteInput(BaseModel):
    file_path: str = Field(description="Path to the file to delete")
    approved: bool = Field(
        default=False,
        description="User approval status for deletion"
    )

class FileDeleteTool(SandboxTool):
    async def _arun(
        self,
        file_path: str,
        approved: bool = False,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        # HITL: If not approved, return special message
        if not approved:
            return "WAIT_FOR_USER_APPROVAL"

        # User approved - proceed with deletion
        vfs = VirtualFilesystem(self.db_pool, thread_id)
        deleted = await vfs.delete_file(file_path)
        return f"Successfully deleted: {file_path}"
```

**Frontend (CopilotKit integration):**

```typescript
useCopilotAction({
    name: 'file_delete',
    description: 'Delete a file. Requires user approval.',
    parameters: [
        {
            name: 'file_path',
            type: 'string',
            required: true,
        },
        // NOTE: 'approved' parameter intentionally NOT defined here
        // CopilotKit detects it's missing and triggers approval flow
    ],
    renderAndWaitForResponse: ({ args, respond }) => {
        // Show confirmation dialog
        // When user approves: respond({ approved: true })
        // When user cancels: respond({ approved: false })
    },
});
```

### Key Design Patterns

1. **Parameter Omission Detection**
   - Frontend omits `approved` parameter from tool definition
   - Backend requires `approved` parameter with `default=False`
   - CopilotKit detects the mismatch and triggers approval flow

2. **Special Return Value**
   - `"WAIT_FOR_USER_APPROVAL"` signals approval needed
   - Not an error—it's a control flow signal

3. **Stateless Re-invocation**
   - Frontend re-calls tool with `approved` parameter
   - No server-side state needed

4. **Security by Default**
   - Default is always `approved=False` (safe)
   - Destructive operations require explicit user consent

### Tools with HITL Support

- **FileDeleteTool** - Requires approval before deleting files
- **FileWriteTool** - Requires approval for overwriting existing files

### Adding HITL to Your Tools

To add HITL approval to any tool:

1. Add `approved: bool = Field(default=False)` to input schema
2. Check approval status at the start of `_arun()`
3. Return `"WAIT_FOR_USER_APPROVAL"` if not approved
4. Omit `approved` from frontend parameter definition
5. Implement `renderAndWaitForResponse` in frontend

## Testing

### Quick Start with Docker

```bash
# Setup PostgreSQL in Docker and run migrations
make db-setup

# Install dependencies and run tests
uv venv
uv pip install -e ".[dev]"
POSTGRES_PORT=5433 uv run pytest -v

# When done, stop database
make db-down
```

### Manual Testing

```bash
# Start database
make db-up

# Run all tests
pytest -v

# Run specific test suites
pytest tests/test_executor.py -v
pytest tests/test_pptx_helpers.py -v

# Stop database
make db-down
```

### Test Status

**Core Tests:** ✅ All passing
- Executor: 12/12
- Filesystem: 12/12
- Manager: 9/9
- Tools: 10/10
- Session Recovery: 16/16

**Helper Tests:** ✅ All passing
- PPTX: 5/5
- XLSX: 4/4
- Word: 4/4
- PDF: 4/4

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

- **sandbox_sessions** - Session tracking (180-day expiration)
- **sandbox_filesystem** - File storage (20MB per file limit)
- **sandbox_session_bytes** - Stateful execution support

See [API Reference](docs/api.md#database-schema) for complete schema.

## Performance

### Worker Pool (Enabled by default in production)

The worker pool provides **70-95% performance improvement** by keeping Pyodide loaded in memory:

| Operation | Without Pool | With Pool | Improvement |
|-----------|--------------|-----------|-------------|
| Simple code | 4.5s | **0.5s** | 89% faster |
| With numpy | 4.5s | **0.2s** | 96% faster |
| With matplotlib | 14s | **1.5s** | 89% faster |

**Enable worker pool:**
```python
import os
os.environ["PYODIDE_USE_POOL"] = "true"  # Enable (recommended)

# Optional configuration
os.environ["PYODIDE_POOL_SIZE"] = "3"  # Number of workers (default: 3)
os.environ["PYODIDE_WORKER_REQUEST_LIMIT"] = "1000"  # Recycle after N requests
os.environ["PYODIDE_HEALTH_CHECK_INTERVAL"] = "30"  # Health check seconds
```

**How it works:**
- 3 long-running Deno workers keep Pyodide + micropip loaded
- Round-robin load balancing across workers
- Automatic health monitoring and recovery
- Session state preserved between executions

See [Worker Pool Documentation](docs/WORKER_POOL.md) for details.

### Legacy Mode (One-shot execution)

When worker pool is disabled (`PYODIDE_USE_POOL=false`):
- File operations: < 50ms
- Python execution: ~4-5s per execution (loads Pyodide each time)
- Helper loading: < 100ms
- Thread isolation: 100% via PostgreSQL

## Security

- ✅ WebAssembly sandboxing (Pyodide for Python, BusyBox WASM for shell)
- ✅ Worker-based isolation (each shell pipeline stage in separate Deno Worker)
- ✅ Path validation (prevents directory traversal)
- ✅ Size limits (20MB per file)
- ✅ Thread isolation (complete separation via PostgreSQL)
- ✅ Configurable network access
- ✅ Automatic session expiration
- ✅ HITL approval for destructive operations (file deletion, overwrites)

## Development

```bash
# Setup
git clone <repo>
cd mayflower-sandbox
pip install -e ".[dev]"

# Run linters
ruff check src/ tests/
ruff format src/ tests/

# Run tests
pytest -v
```

## License

MIT

## Support

- **Documentation**: See [docs/](docs/) directory
- **Issues**: [GitHub Issues](https://github.com/mayflower/mayflower-sandbox/issues)

## Related Projects

- [DeepAgents](https://github.com/mayflower/deepagents) - Advanced agent framework with sandbox support
- [LangChain](https://github.com/langchain-ai/langchain) - Framework for LLM applications
- [LangGraph](https://github.com/langchain-ai/langgraph) - Build stateful agents
- [Pyodide](https://pyodide.org/) - Python in WebAssembly
- [BusyBox](https://busybox.net/) - Unix utilities in a single executable

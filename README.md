# Mayflower Sandbox

Production-ready Python sandbox with PostgreSQL-backed virtual filesystem and document processing helpers for LangGraph agents.

## Overview

Mayflower Sandbox provides secure, isolated Python code execution with persistent file storage, designed for LangChain and LangGraph applications. Execute untrusted Python code, process documents (Word, Excel, PowerPoint, PDF), and maintain persistent state across sessions—all with complete thread isolation.

## Key Features

- ✅ **Secure Python Execution** - Pyodide WebAssembly sandbox with configurable network access
- ✅ **Persistent Virtual Filesystem** - PostgreSQL-backed storage (20MB file limit per file)
- ✅ **Document Processing Helpers** - Built-in helpers for Word, Excel, PowerPoint, and PDF
- ✅ **Stateful Execution** - Variables and state persist across executions and restarts
- ✅ **Thread Isolation** - Complete isolation between users/sessions via `thread_id`
- ✅ **LangChain Integration** - All tools extend `BaseTool` for seamless LangGraph integration
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

See [Installation Guide](docs/installation.md) for detailed setup instructions.

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

See [Quick Start Guide](docs/quickstart.md) for a complete tutorial.

## Documentation

### Getting Started
- **[Installation Guide](docs/installation.md)** - Install and configure Mayflower Sandbox
- **[Quick Start](docs/quickstart.md)** - Get started in 5 minutes
- **[Examples](docs/examples.md)** - Complete working examples

### Reference
- **[Tools Reference](docs/tools.md)** - Documentation for the 10 LangChain tools
- **[Helpers Reference](docs/helpers.md)** - Document processing helpers (Word, Excel, PowerPoint, PDF)
- **[Advanced Features](docs/advanced.md)** - Stateful execution, file server, cleanup
- **[API Reference](docs/api.md)** - Low-level API documentation

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ LangGraph Agent                                     │
│ ├─ ExecutePythonTool (direct code execution)       │
│ ├─ RunPythonFileTool (run existing .py files)      │
│ ├─ ExecuteCodeTool (state-based for large code)    │
│ ├─ FileReadTool                                     │
│ ├─ FileWriteTool                                    │
│ ├─ FileEditTool (str_replace)                       │
│ ├─ FileListTool                                     │
│ ├─ FileDeleteTool                                   │
│ ├─ FileGlobTool (glob_files)                        │
│ └─ FileGrepTool (grep_files)                        │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│ Mayflower Sandbox                                   │
│ ├─ SandboxExecutor (VFS + Pyodide integration)     │
│ ├─ VirtualFilesystem (PostgreSQL storage)          │
│ ├─ Helper Modules (auto-loaded into VFS)           │
│ ├─ SandboxManager (Session lifecycle)              │
│ └─ CleanupJob (Automatic expiration)               │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│ Infrastructure                                      │
│ ├─ PostgreSQL (Persistent storage)                 │
│ └─ Deno + Pyodide (Python execution)               │
└─────────────────────────────────────────────────────┘
```

## The 10 Tools

Mayflower Sandbox provides 10 LangChain tools:

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
5. **FileWriteTool** (`file_write`) - Write files to PostgreSQL VFS (20MB limit)
6. **FileEditTool** (`file_edit`) - Edit files by replacing unique strings
7. **FileListTool** (`file_list`) - List files with optional prefix filtering
8. **FileDeleteTool** (`file_delete`) - Delete files from VFS

### File Search Tools

9. **FileGlobTool** (`file_glob`) - Find files matching glob patterns
10. **FileGrepTool** (`file_grep`) - Search file contents with regex

See [Tools Reference](docs/tools.md) for detailed documentation.

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

## Document Processing Helpers

Built-in helpers for document processing (automatically available in sandbox):

- **Word (DOCX)** - Extract text, read tables, find/replace, add comments, convert to markdown
- **Excel (XLSX)** - Read/write cells, convert to dictionaries, detect formulas
- **PowerPoint (PPTX)** - Extract text, replace content, inventory slides, generate HTML
- **PDF** - Merge, split, extract text, rotate pages, get metadata

See [Helpers Reference](docs/helpers.md) for complete documentation.

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

- ✅ WebAssembly sandboxing (Pyodide)
- ✅ Path validation (prevents directory traversal)
- ✅ Size limits (20MB per file)
- ✅ Thread isolation (complete separation)
- ✅ Configurable network access
- ✅ Automatic session expiration

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

- [LangChain](https://github.com/langchain-ai/langchain) - Framework for LLM applications
- [LangGraph](https://github.com/langchain-ai/langgraph) - Build stateful agents
- [Pyodide](https://pyodide.org/) - Python in WebAssembly

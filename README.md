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
llm = ChatAnthropic(model="claude-3-5-sonnet-20241022")
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
- **[Tools Reference](docs/tools.md)** - Documentation for the 5 LangChain tools
- **[Helpers Reference](docs/helpers.md)** - Document processing helpers (Word, Excel, PowerPoint, PDF)
- **[Advanced Features](docs/advanced.md)** - Stateful execution, file server, cleanup
- **[API Reference](docs/api.md)** - Low-level API documentation

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ LangGraph Agent                                     │
│ ├─ ExecutePythonTool (with helper modules)         │
│ ├─ FileReadTool                                     │
│ ├─ FileWriteTool                                    │
│ ├─ FileListTool                                     │
│ └─ FileDeleteTool                                   │
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

## The 5 Tools

Mayflower Sandbox provides 5 LangChain tools:

1. **ExecutePythonTool** - Execute Python code with automatic VFS sync
2. **FileReadTool** - Read files from PostgreSQL VFS
3. **FileWriteTool** - Write files to PostgreSQL VFS (20MB limit)
4. **FileListTool** - List files with optional prefix filtering
5. **FileDeleteTool** - Delete files from VFS

See [Tools Reference](docs/tools.md) for detailed documentation.

## Document Processing Helpers

Built-in helpers for document processing (automatically available in sandbox):

- **Word (DOCX)** - Extract text, read tables, find/replace, add comments, convert to markdown
- **Excel (XLSX)** - Read/write cells, convert to dictionaries, detect formulas
- **PowerPoint (PPTX)** - Extract text, replace content, inventory slides, generate HTML
- **PDF** - Merge, split, extract text, rotate pages, get metadata

See [Helpers Reference](docs/helpers.md) for complete documentation.

## Testing

```bash
# Run all tests
pytest -v

# Run specific test suites
pytest tests/test_executor.py -v
pytest tests/test_pptx_helpers.py -v
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

- File operations: < 50ms
- Python execution: ~2-5s first run, <1s subsequent
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

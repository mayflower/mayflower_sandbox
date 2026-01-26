# Mayflower Sandbox

Production-ready Python sandbox with PostgreSQL-backed virtual filesystem for LangGraph agents.

## Overview

Mayflower Sandbox provides secure, isolated Python code execution with persistent file storage, designed for LangChain and LangGraph applications. Execute untrusted Python code, process documents (Word, Excel, PowerPoint, PDF), and maintain persistent state across sessions—all with complete thread isolation.

## Key Features

- **Secure Python Execution** - Pyodide WebAssembly sandbox with configurable network access
- **Persistent Virtual Filesystem** - PostgreSQL-backed storage (20MB file limit per file)
- **Document Processing Helpers** - Built-in helpers for Word, Excel, PowerPoint, and PDF
- **Stateful Execution** - Variables and state persist across executions and restarts
- **Thread Isolation** - Complete isolation between users/sessions via `thread_id`
- **LangChain Integration** - All tools extend `BaseTool` for seamless LangGraph integration
- **HITL Support** - Human-in-the-Loop approval for destructive operations
- **Worker Pool** - 70-95% performance improvement (enabled by default)
- **Automatic Cleanup** - Configurable session expiration (180 days default)

## Quick Start

```bash
# Install Deno (required for Pyodide)
curl -fsSL https://deno.land/x/install/install.sh | sh

# Install package
pip install -e .

# Setup database
createdb mayflower_test
psql -d mayflower_test -f migrations/001_sandbox_schema.sql
```

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

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ LangGraph Agent                                     │
│ ├─ ExecutePythonTool (direct code execution)       │
│ ├─ RunPythonFileTool (run existing .py files)      │
│ ├─ ExecuteCodeTool (state-based for large code)    │
│ ├─ FileReadTool, FileWriteTool, FileEditTool       │
│ ├─ FileListTool, FileDeleteTool                    │
│ └─ FileGlobTool, FileGrepTool                      │
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

## The 12 Tools

### Code Execution Tools

| Tool | Name | Description |
|------|------|-------------|
| **ExecutePythonTool** | `python_run` | Execute Python code directly |
| **RunPythonFileTool** | `python_run_file` | Execute .py files from VFS |
| **ExecuteCodeTool** | `python_run_prepared` | State-based execution for large code |

### File Management Tools

| Tool | Name | Description |
|------|------|-------------|
| **FileReadTool** | `file_read` | Read files from VFS |
| **FileWriteTool** | `file_write` | Write files to VFS (HITL for overwrites) |
| **FileEditTool** | `file_edit` | Edit files by string replacement |
| **FileListTool** | `file_list` | List files with prefix filtering |
| **FileDeleteTool** | `file_delete` | Delete files (HITL required) |
| **FileGlobTool** | `file_glob` | Find files with glob patterns |
| **FileGrepTool** | `file_grep` | Search file contents with regex |

### Skills & MCP Tools

| Tool | Name | Description |
|------|------|-------------|
| **SkillInstallTool** | `skill_install` | Install Claude Skills into sandbox |
| **MCPBindHttpTool** | `mcp_bind_http` | Bind Streamable HTTP MCP servers |

## Document Processing Helpers

Built-in helpers available in the sandbox:

- **Word (DOCX)** - Extract text, read tables, find/replace, add comments
- **Excel (XLSX)** - Read/write cells, convert to dictionaries, detect formulas
- **PowerPoint (PPTX)** - Extract text, replace content, generate HTML
- **PDF** - Merge, split, extract text, rotate pages, get metadata

## Documentation

- [Installation](getting-started/installation.md) - Setup instructions
- [Quick Start](getting-started/quickstart.md) - Get started in 5 minutes
- [Tools Reference](user-guide/tools.md) - Complete tools documentation
- [Document Helpers](user-guide/helpers.md) - Document processing helpers
- [Examples](user-guide/examples.md) - Working examples
- [Stateful Execution](advanced/stateful-execution.md) - Advanced features
- [Worker Pool](advanced/worker-pool.md) - Performance optimization
- [MCP Integration](advanced/mcp.md) - Model Context Protocol support
- [API Reference](reference/api.md) - Low-level API documentation
- [Architecture](development/architecture.md) - System design

## Security

- WebAssembly sandboxing (Pyodide)
- Path validation (prevents directory traversal)
- Size limits (20MB per file)
- Thread isolation (complete separation)
- Configurable network access
- Automatic session expiration
- HITL approval for destructive operations

## License

MIT

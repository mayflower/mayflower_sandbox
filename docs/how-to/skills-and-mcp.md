# Skills and MCP Integration

Mayflower Sandbox supports Claude Skills and Model Context Protocol (MCP) servers using a **Code Mode** pattern. Rather than exposing MCP tools as tool-call tokens, external capabilities are converted to **typed local Python code** that LLMs can call directly -- improving efficiency and enabling more natural code generation.

## Code Mode: How It Works

This implementation follows [Cloudflare's Code Mode approach](https://blog.cloudflare.com/code-mode/) and [Anthropic's code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp), adapted for Python/Pyodide instead of TypeScript.

### Why Code Mode?

Traditional MCP integration requires LLMs to use tool-call tokens for each operation. Code Mode offers several advantages:

| Aspect | Traditional Tool Calls | Code Mode |
|--------|----------------------|-----------|
| **Batching** | One tool call per operation | Multiple calls in single code block |
| **LLM Proficiency** | Limited training examples | Extensive code generation training |
| **Context Efficiency** | Re-processing between calls | Single execution, no re-processing |
| **Validation** | Runtime errors only | Pydantic validation + type hints |

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ MCP Server (e.g., DeepWiki)                                             │
│   GET /mcp → Returns tool schemas                                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ 1. Fetch schemas
┌──────────────────────────────────▼──────────────────────────────────────┐
│ add_http_mcp_server()                                                   │
│   • Discovers tools from MCP server                                     │
│   • Generates typed Python wrappers                                     │
│   • Writes to /site-packages/servers/<name>/                            │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ 2. Generate Python code
┌──────────────────────────────────▼──────────────────────────────────────┐
│ Generated Python Package                                                │
│                                                                         │
│   /site-packages/servers/deepwiki/                                      │
│   ├── __init__.py      # Exports all tool functions                     │
│   ├── models.py        # Pydantic models from JSON Schema               │
│   ├── tools.py         # Typed async wrapper functions                  │
│   └── schemas.json     # Original schemas for reference                 │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ 3. LLM writes code using wrappers
┌──────────────────────────────────▼──────────────────────────────────────┐
│ LLM-Generated Code (in Pyodide)                                         │
│                                                                         │
│   from servers.deepwiki import read_wiki_structure                      │
│   result = await read_wiki_structure(repoName="langchain-ai/langchain") │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ 4. Call routed via bridge
┌──────────────────────────────────▼──────────────────────────────────────┐
│ MCP Bridge (mayflower_mcp.call → __MCP_CALL__ → HTTP bridge)            │
│   • Validates request against registered servers                        │
│   • Proxies call to actual MCP server                                   │
│   • Returns result to Pyodide                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### Generated Code Example

When you bind an MCP server, Mayflower generates typed Python wrappers:

**models.py** (Pydantic models from JSON Schema):

```python
from pydantic import BaseModel, Field

class ReadWikiStructureArgs(BaseModel):
    repoName: str = Field(..., description="Repository name (e.g., 'owner/repo')")
```

**tools.py** (Typed async wrappers):

```python
async def read_wiki_structure(repoName: str) -> Any:
    """Read the structure of a wiki repository.

    Args:
        repoName: Repository name (e.g., 'owner/repo')
    """
    from mayflower_mcp import call
    from .models import ReadWikiStructureArgs

    validated = ReadWikiStructureArgs(repoName=repoName)
    payload = validated.model_dump(exclude_none=True)
    return await call("deepwiki", "read_wiki_structure", payload)
```

### Bridge Layer

The `mayflower_mcp` module is automatically written to `/site-packages/` and provides the bridge:

```python
# /site-packages/mayflower_mcp.py (auto-generated)
async def call(server: str, tool: str, args: dict) -> Any:
    # __MCP_CALL__ is injected into Pyodide builtins
    return await __MCP_CALL__(server, tool, args)
```

The `__MCP_CALL__` function makes HTTP requests to a local bridge server that proxies to the actual MCP server, keeping Pyodide sandboxed while enabling external tool calls.

## Installing Skills

Skills and MCP servers are managed via direct function calls in `mayflower_sandbox.integrations`:

```python
from mayflower_sandbox.integrations import install_skill

skill = await install_skill(
    db_pool,
    thread_id,
    "github:anthropics/skills/algorithmic-art",
)
```

### Function Signature

```python
async def install_skill(
    db_pool,
    thread_id: str,
    source: str,
    *,
    compile_python: bool = True,
    into: str = "/site-packages/skills",
) -> dict[str, Any]
```

**Parameters:**

- `source` -- URL or `github:owner/repo/path[@branch]` format
- `compile_python` -- Whether to extract and write code blocks from SKILL.md
- `into` -- Base path for skill installation

**Returns** a dict with `name`, `package`, `path`, and `description`.

### How It Works

1. Fetches `SKILL.md` via HTTP (with GitHub shorthand support)
2. Parses YAML frontmatter for metadata
3. Extracts Python code blocks and writes to `lib/snippet_*.py`
4. Creates package `__init__.py` with exports
5. Updates `index.json`
6. Stores metadata in `sandbox_skills` database table

Generated modules are importable as `from skills.<skill_name> import instructions`. If the markdown includes fenced Python blocks, the snippets are materialised under `skills.<skill_name>.lib`.

## Binding Streamable HTTP MCP Servers

```python
from mayflower_sandbox.integrations import add_http_mcp_server

server = await add_http_mcp_server(
    db_pool,
    thread_id,
    name="deepwiki",
    url="https://mcp.deepwiki.com/mcp",
)
```

### Function Signature

```python
async def add_http_mcp_server(
    db_pool,
    thread_id: str,
    name: str,
    url: str,
    headers: dict | None = None,
    auth: dict | None = None,
    *,
    discover: bool = True,
    typed: bool = True,
    into: str = "/site-packages/servers",
) -> dict[str, Any]
```

**Parameters:**

- `name` -- Server identifier for imports (e.g., `"deepwiki"`)
- `url` -- Streamable HTTP MCP endpoint URL
- `headers` -- Optional HTTP headers (bearer tokens, API keys)
- `auth` -- Optional authentication config
- `discover` -- Discover tools via introspection (default `True`)
- `typed` -- Generate typed Pydantic stubs (default `True`)
- `into` -- Base path for generated package

**Returns** a dict with `name`, `package`, `path`, `url`, `discover`, and `typed`.

After binding, tools are available via `from servers.<name> import <tool_function>`, and calls are routed back to the host via `mayflower_mcp.call`.

For safety, set `MAYFLOWER_MCP_ALLOWLIST` (comma-separated names or host suffixes) to restrict which servers can be bound.

In DeepAgents, skills are discovered via `SkillsMiddleware` which uses the backend's file operations (`ls_info`, `download_files`) and executes scripts via `backend.execute()`.

## Public MCP Servers

The following public MCP servers have been tested with Mayflower Sandbox:

| Server | URL | Description |
|--------|-----|-------------|
| [DeepWiki](https://mcp.deepwiki.com) | `https://mcp.deepwiki.com/mcp` | Wiki/documentation search |
| [Semgrep](https://semgrep.dev) | `https://mcp.semgrep.ai/mcp` | Code analysis and security scanning |

Example:

```python
import os
os.environ["MAYFLOWER_MCP_ALLOWLIST"] = "deepwiki,mcp.deepwiki.com"

from mayflower_sandbox.integrations import add_http_mcp_server

await add_http_mcp_server(
    db_pool, thread_id,
    name="deepwiki",
    url="https://mcp.deepwiki.com/mcp",
)

# Now LLM can write code using the generated wrappers:
# from servers.deepwiki import read_wiki_structure
# result = await read_wiki_structure(repoName="langchain-ai/langchain")
```

## Network Model & Sessions

- General outbound networking from Pyodide is disabled. Only CDN traffic required for `micropip` (`cdn.jsdelivr.net`) and the local MCP bridge (`127.0.0.1:<port>`) are permitted. Extend the allowlist explicitly with `MAYFLOWER_SANDBOX_NET_ALLOW` if a host must be reachable.
- MCP client sessions are pooled per `(thread_id, server)` with a configurable TTL (`MAYFLOWER_MCP_SESSION_TTL`, default 5 minutes). Calls are lightly rate limited (`MAYFLOWER_MCP_CALL_INTERVAL`, default 0.1s) to avoid rapid-fire hammering of remote endpoints.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAYFLOWER_MCP_ALLOWLIST` | (none) | Comma-separated server names or host suffixes to allow |
| `MAYFLOWER_MCP_SESSION_TTL` | `300` | Session TTL in seconds (5 minutes) |
| `MAYFLOWER_MCP_CALL_INTERVAL` | `0.1` | Minimum interval between calls in seconds |
| `MAYFLOWER_SANDBOX_NET_ALLOW` | (none) | Additional hosts to allow outbound connections to |

## Related Documentation

- [Cloudflare Code Mode Blog Post](https://blog.cloudflare.com/code-mode/)
- [Anthropic: Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)

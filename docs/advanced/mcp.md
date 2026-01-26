# Skills and MCP Integration

Mayflower Sandbox supports Claude Skills and Model Context Protocol (MCP) servers using a **Code Mode** pattern. Rather than exposing MCP tools as LangChain tool-call tokens, external capabilities are converted to **typed local Python code** that LLMs can call directly—improving efficiency and enabling more natural code generation.

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
│ MCPBindHttpTool                                                         │
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

**\_\_init\_\_.py** (Exports):
```python
from .tools import read_wiki_structure, ask_question
__all__ = ["read_wiki_structure", "ask_question"]
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

- Invoke the `skill_install` tool with a `source` such as `github:anthropics/skills/algorithmic-art`. The helper downloads `SKILL.md`, parses the YAML front matter, and writes the package under `/site-packages/skills/<skill>/`.
- Generated modules are importable as `from skills.<skill_name> import instructions`. If the markdown includes ` ```python ` fences, the snippets are materialised under `skills.<skill_name>.lib`.
- Metadata is persisted to `sandbox_skills` and tracked in `/site-packages/skills/index.json`, so repeating the install updates the existing entry.

## Binding Streamable HTTP MCP Servers

- Use the `mcp_bind_http` tool with a server `name`, Streamable HTTP `url`, and optional headers (bearer tokens, API keys). Most MCP HTTP servers expose a `.../mcp` endpoint—provide that full path.
- The tool caches connection metadata in `sandbox_mcp_servers`, opens a persistent session through `MCPBindingManager`, and generates wrappers in `/site-packages/servers/<name>/`.
- After discovery, tools are available via `from servers.<name> import tools` (or directly `from servers.<name>.tools import echo`), and calls are routed back to the host via the injected `mayflower_mcp.call`.
- For safety, set `MAYFLOWER_MCP_ALLOWLIST` (comma-separated names or host suffixes) to the servers you intend to bind—`mcp_bind_http` refuses anything outside this list.

## Quickstart

The repository ships `examples/skills_mcp_quickstart.py`, which:

1. Installs `algorithmic-art` from the Claude Skills collection.
2. Binds an MCP server at `http://localhost:8000/mcp` (override via environment variables).
3. Executes code inside `SandboxExecutor` to read skill instructions and list discovered tools.

Run `make db-up`, ensure a Streamable HTTP server is reachable, then execute the script to verify both flows end‐to‐end. Any imports added under `/site-packages` are automatically made available to user code because the sandbox bootstraps that path on `sys.path`.

## Network Model & Sessions

- General outbound networking from Pyodide is disabled. Only CDN traffic required for `micropip` (`cdn.jsdelivr.net`) and the local MCP bridge (`127.0.0.1:<port>`) are permitted. Extend the allowlist explicitly with `MAYFLOWER_SANDBOX_NET_ALLOW` if a host must be reachable.
- MCP client sessions are pooled per `(thread_id, server)` with a configurable TTL (`MAYFLOWER_MCP_SESSION_TTL`, default 5 minutes). Calls are lightly rate limited (`MAYFLOWER_MCP_CALL_INTERVAL`, default 0.1s) to avoid rapid-fire hammering of remote endpoints.

## Public MCP Servers

The following public MCP servers have been tested with Mayflower Sandbox:

| Server | URL | Description |
|--------|-----|-------------|
| [DeepWiki](https://mcp.deepwiki.com) | `https://mcp.deepwiki.com/mcp` | Wiki/documentation search |
| [Semgrep](https://semgrep.dev) | `https://mcp.semgrep.ai/mcp` | Code analysis and security scanning |

Example binding:
```python
# Allow the server in your environment
os.environ["MAYFLOWER_MCP_ALLOWLIST"] = "deepwiki,mcp.deepwiki.com"

# Bind the server
tools = create_sandbox_tools(db_pool, thread_id="user_123")
mcp_tool = next(t for t in tools if t.name == "mcp_bind_http")
await mcp_tool._arun(name="deepwiki", url="https://mcp.deepwiki.com/mcp")

# Now LLM can write code using the generated wrappers:
# from servers.deepwiki import read_wiki_structure
# result = await read_wiki_structure(repoName="langchain-ai/langchain")
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAYFLOWER_MCP_ALLOWLIST` | (none) | Comma-separated server names or host suffixes to allow |
| `MAYFLOWER_MCP_SESSION_TTL` | `300` | Session TTL in seconds (5 minutes) |
| `MAYFLOWER_MCP_CALL_INTERVAL` | `0.1` | Minimum interval between calls in seconds |

## Related Documentation

- [Cloudflare Code Mode Blog Post](https://blog.cloudflare.com/code-mode/)
- [Anthropic: Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)

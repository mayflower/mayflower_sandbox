# Skills and MCP Integration

Mayflower Sandbox now understands Claude Skills and Model Context Protocol (MCP) servers. Use the LangChain tools `skill_install` and `mcp_bind_http` to mirror remote capabilities into the sandbox’s virtual filesystem and expose them as importable Python modules during Pyodide execution.

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

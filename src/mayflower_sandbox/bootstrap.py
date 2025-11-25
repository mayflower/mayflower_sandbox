from __future__ import annotations

from pathlib import PurePosixPath

SITE_PACKAGES_PATH = PurePosixPath("/site-packages")

MCP_SHIM = """\
# Auto-written by Mayflower Sandbox at thread bootstrap.
# Provides an async 'call' wrapper that jumps back to host via __MCP_CALL__ (injected).
from typing import Any, Dict
import builtins

__MCP_CALL__ = getattr(builtins, "__MCP_CALL__", None)

async def call(server: str, tool: str, args: Dict[str, Any]) -> Any:
    if __MCP_CALL__ is None:
        raise RuntimeError("MCP binding not available in this sandbox.")
    return await __MCP_CALL__(server, tool, args)
"""

MAISTACK_TOOLS_SHIM = """\
# Auto-written by Mayflower Sandbox at thread bootstrap.
# Convenience wrappers for calling MAI Stack tools from sandbox code.
\"\"\"MAI Stack tool bridge - convenience wrappers over mayflower_mcp.\"\"\"
import asyncio
from typing import Any

import mayflower_mcp


async def call_tool(tool_name: str, **kwargs: Any) -> Any:
    \"\"\"Call any MAI Stack tool from sandbox code.\"\"\"
    return await mayflower_mcp.call("maistack", tool_name, kwargs)


async def list_collections() -> list[str]:
    \"\"\"List all available document collections.\"\"\"
    return await call_tool("list_collections")


async def search_all_collections(
    query: str, k: int = 5, method: str = "hybrid"
) -> dict[str, Any]:
    \"\"\"
    Search all document collections in parallel.

    Args:
        query: Search query string
        k: Number of results per collection
        method: Search method ('hybrid', 'semantic', 'keyword')

    Returns:
        Dictionary mapping collection name to search results (or error dict)
    \"\"\"
    collections = await list_collections()

    async def search_one(collection: str) -> tuple[str, Any]:
        try:
            result = await call_tool(
                f"{collection}_search", query=query, k=k, method=method
            )
            return collection, result
        except Exception as e:
            return collection, {"error": str(e)}

    tasks = [search_one(c) for c in collections]
    results = await asyncio.gather(*tasks)
    return dict(results)


async def graph_search_all_collections(
    query: str,
    k: int = 10,
    max_depth: int = 3,
    completeness_threshold: float = 0.8,
) -> dict[str, Any]:
    \"\"\"
    GraphRAG search across all collections in parallel.

    Args:
        query: Search query string
        k: Number of results per collection
        max_depth: Maximum traversal depth for graph search
        completeness_threshold: Threshold for completeness scoring

    Returns:
        Dictionary mapping collection name to search results (or error dict)
    \"\"\"
    collections = await list_collections()

    async def search_one(collection: str) -> tuple[str, Any]:
        try:
            result = await call_tool(
                f"{collection}_graph_search",
                query=query,
                k=k,
                max_depth=max_depth,
                completeness_threshold=completeness_threshold,
            )
            return collection, result
        except Exception as e:
            return collection, {"error": str(e)}

    tasks = [search_one(c) for c in collections]
    results = await asyncio.gather(*tasks)
    return dict(results)
"""

SITE_PACKAGES_INIT = """\
import sys
from pathlib import Path

_site = Path("/site-packages")
_site_str = str(_site)
if _site_str not in sys.path:
    sys.path.append(_site_str)
"""


async def write_bootstrap_files(vfs, *, thread_id: str) -> None:
    """
    Write bootstrap files into the thread's VFS.

    Writes:
    - /site-packages/mayflower_mcp.py: Low-level MCP call wrapper
    - /site-packages/maistack_tools.py: Convenience wrappers for MAI Stack tools

    'vfs' is the repo's VirtualFilesystem instance.
    """
    site = SITE_PACKAGES_PATH
    await vfs.write_file(str(site / "mayflower_mcp.py"), MCP_SHIM.encode("utf-8"))
    await vfs.write_file(str(site / "maistack_tools.py"), MAISTACK_TOOLS_SHIM.encode("utf-8"))

    # Ensure /site-packages is on sys.path via standard sitecustomize hook.
    # Avoid overwriting existing customization if present.
    sitecustomize_path = "/sitecustomize.py"
    if not await vfs.file_exists(sitecustomize_path):
        await vfs.write_file(sitecustomize_path, SITE_PACKAGES_INIT.encode("utf-8"))

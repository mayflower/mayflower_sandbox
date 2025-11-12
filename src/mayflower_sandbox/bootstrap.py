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
    Write /site-packages/mayflower_mcp.py into the thread's VFS and ensure /site-packages exists.
    'vfs' is the repo's VirtualFilesystem instance.
    """
    site = SITE_PACKAGES_PATH
    await vfs.write_file(str(site / "mayflower_mcp.py"), MCP_SHIM.encode("utf-8"))

    # Ensure /site-packages is on sys.path via standard sitecustomize hook.
    # Avoid overwriting existing customization if present.
    sitecustomize_path = "/sitecustomize.py"
    if not await vfs.file_exists(sitecustomize_path):
        await vfs.write_file(sitecustomize_path, SITE_PACKAGES_INIT.encode("utf-8"))

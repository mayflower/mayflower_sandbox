import os
import shutil
import sys

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Tests assume the Dockerized database is exposed on localhost:5433.
# Allow overrides via env, but default to the container mapping.
os.environ.setdefault("POSTGRES_PORT", "5433")

# Check if Deno is available
DENO_AVAILABLE = shutil.which("deno") is not None

# Marker for tests that require Deno
requires_deno = pytest.mark.skipif(
    not DENO_AVAILABLE,
    reason="Deno is not installed (required for Pyodide sandbox execution)",
)


@pytest.fixture(scope="function", autouse=True)
async def cleanup_worker_pool():
    """Clean up worker pool and MCP bridge after each test to prevent event loop issues."""
    yield
    # Clean up pool and bridge after test
    from mayflower_sandbox.sandbox_executor import SandboxExecutor

    if SandboxExecutor._pool is not None:
        await SandboxExecutor._pool.shutdown()
        SandboxExecutor._pool = None

    if SandboxExecutor._mcp_bridge is not None:
        await SandboxExecutor._mcp_bridge.shutdown()
        SandboxExecutor._mcp_bridge = None

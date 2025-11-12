import os
import sys

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Tests assume the Dockerized database is exposed on localhost:5433.
# Allow overrides via env, but default to the container mapping.
os.environ.setdefault("POSTGRES_PORT", "5433")


@pytest.fixture(scope="function", autouse=True)
async def cleanup_worker_pool():
    """Clean up worker pool after each test to prevent event loop issues."""
    yield
    # Clean up pool after test
    from mayflower_sandbox.sandbox_executor import SandboxExecutor

    if SandboxExecutor._pool is not None:
        await SandboxExecutor._pool.shutdown()
        SandboxExecutor._pool = None

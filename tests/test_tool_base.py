"""
Direct unit tests for SandboxTool base class.
"""

import os
from unittest.mock import MagicMock

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "mayflower_test"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )
    yield pool
    await pool.close()


class TestSandboxToolBase:
    """Tests for SandboxTool base class."""

    def test_get_thread_id_from_instance(self, db_pool):
        """Test that thread_id is read from instance when no callback."""
        from mayflower_sandbox.tools.base import SandboxTool

        class TestTool(SandboxTool):
            name: str = "test_tool"
            description: str = "Test tool"

            async def _arun(self, **kwargs):
                return "test"

        tool = TestTool(db_pool=db_pool, thread_id="instance_thread")
        thread_id = tool._get_thread_id(None)
        assert thread_id == "instance_thread"

    def test_get_thread_id_default_fallback(self, db_pool):
        """Test that thread_id defaults to 'default' when nothing set."""
        from mayflower_sandbox.tools.base import SandboxTool

        class TestTool(SandboxTool):
            name: str = "test_tool"
            description: str = "Test tool"

            async def _arun(self, **kwargs):
                return "test"

        tool = TestTool(db_pool=db_pool)
        thread_id = tool._get_thread_id(None)
        assert thread_id == "default"

    def test_get_thread_id_from_metadata(self, db_pool):
        """Test thread_id extraction from callback metadata."""
        from mayflower_sandbox.tools.base import SandboxTool

        class TestTool(SandboxTool):
            name: str = "test_tool"
            description: str = "Test tool"

            async def _arun(self, **kwargs):
                return "test"

        tool = TestTool(db_pool=db_pool, thread_id="instance_thread")

        # Mock a run_manager with metadata
        run_manager = MagicMock()
        run_manager.metadata = {"configurable": {"thread_id": "metadata_thread"}}
        run_manager.tags = None

        thread_id = tool._get_thread_id(run_manager)
        assert thread_id == "metadata_thread"

    def test_get_thread_id_from_tags(self, db_pool):
        """Test thread_id extraction from callback tags."""
        from mayflower_sandbox.tools.base import SandboxTool

        class TestTool(SandboxTool):
            name: str = "test_tool"
            description: str = "Test tool"

            async def _arun(self, **kwargs):
                return "test"

        tool = TestTool(db_pool=db_pool, thread_id="instance_thread")

        # Mock a run_manager with tags but no metadata
        run_manager = MagicMock()
        run_manager.metadata = {}
        run_manager.tags = ["other_tag", "thread_id:tag_thread", "another_tag"]

        thread_id = tool._get_thread_id(run_manager)
        assert thread_id == "tag_thread"

    def test_get_thread_id_metadata_priority(self, db_pool):
        """Test that metadata takes priority over tags."""
        from mayflower_sandbox.tools.base import SandboxTool

        class TestTool(SandboxTool):
            name: str = "test_tool"
            description: str = "Test tool"

            async def _arun(self, **kwargs):
                return "test"

        tool = TestTool(db_pool=db_pool, thread_id="instance_thread")

        # Mock run_manager with both metadata and tags
        run_manager = MagicMock()
        run_manager.metadata = {"configurable": {"thread_id": "metadata_thread"}}
        run_manager.tags = ["thread_id:tag_thread"]

        thread_id = tool._get_thread_id(run_manager)
        assert thread_id == "metadata_thread"

    def test_arun_not_implemented(self, db_pool):
        """Test that base _arun raises NotImplementedError."""
        from mayflower_sandbox.tools.base import SandboxTool

        # Use the base class directly
        tool = SandboxTool(db_pool=db_pool, name="base_tool", description="Base")

        with pytest.raises(NotImplementedError, match="Subclasses must implement"):
            import asyncio

            asyncio.get_event_loop().run_until_complete(tool._arun())

    def test_sync_run_method(self, db_pool):
        """Test the synchronous _run wrapper."""
        from mayflower_sandbox.tools.base import SandboxTool

        class TestTool(SandboxTool):
            name: str = "test_tool"
            description: str = "Test tool"

            async def _arun(self, **kwargs):
                return "async_result"

        tool = TestTool(db_pool=db_pool)
        # Call the sync _run method
        result = tool._run()
        assert result == "async_result"

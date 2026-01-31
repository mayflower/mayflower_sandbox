"""
Direct unit tests for SandboxTool base class.
"""

from unittest.mock import MagicMock

import pytest

from mayflower_sandbox.tools.base import SandboxTool


class MockTool(SandboxTool):
    """Test subclass for SandboxTool."""

    name: str = "test_tool"
    description: str = "Test tool"

    async def _arun(self, run_manager=None, **kwargs):
        return "test"


class TestSandboxToolBase:
    """Tests for SandboxTool base class."""

    def test_get_thread_id_from_instance(self):
        """Test that thread_id is read from instance when no callback."""
        # Use model_construct to bypass Pydantic validation
        tool = MockTool.model_construct(
            db_pool=MagicMock(),
            thread_id="instance_thread",
            name="test_tool",
            description="Test tool",
        )
        thread_id = tool._get_thread_id(None)
        assert thread_id == "instance_thread"

    def test_get_thread_id_default_fallback(self):
        """Test that thread_id defaults to 'default' when nothing set."""
        tool = MockTool.model_construct(
            db_pool=MagicMock(),
            thread_id=None,
            name="test_tool",
            description="Test tool",
        )
        thread_id = tool._get_thread_id(None)
        assert thread_id == "default"

    def test_get_thread_id_from_metadata(self):
        """Test thread_id extraction from callback metadata."""
        tool = MockTool.model_construct(
            db_pool=MagicMock(),
            thread_id="instance_thread",
            name="test_tool",
            description="Test tool",
        )

        # Mock a run_manager with metadata
        run_manager = MagicMock()
        run_manager.metadata = {"configurable": {"thread_id": "metadata_thread"}}
        run_manager.tags = None

        thread_id = tool._get_thread_id(run_manager)
        assert thread_id == "metadata_thread"

    def test_get_thread_id_from_tags(self):
        """Test thread_id extraction from callback tags."""
        tool = MockTool.model_construct(
            db_pool=MagicMock(),
            thread_id="instance_thread",
            name="test_tool",
            description="Test tool",
        )

        # Mock a run_manager with tags but no metadata
        run_manager = MagicMock()
        run_manager.metadata = {}
        run_manager.tags = ["other_tag", "thread_id:tag_thread", "another_tag"]

        thread_id = tool._get_thread_id(run_manager)
        assert thread_id == "tag_thread"

    def test_get_thread_id_metadata_priority(self):
        """Test that metadata takes priority over tags."""
        tool = MockTool.model_construct(
            db_pool=MagicMock(),
            thread_id="instance_thread",
            name="test_tool",
            description="Test tool",
        )

        # Mock run_manager with both metadata and tags
        run_manager = MagicMock()
        run_manager.metadata = {"configurable": {"thread_id": "metadata_thread"}}
        run_manager.tags = ["thread_id:tag_thread"]

        thread_id = tool._get_thread_id(run_manager)
        assert thread_id == "metadata_thread"

    def test_arun_not_implemented(self):
        """Test that base _arun raises NotImplementedError."""
        # Use the base class directly
        tool = SandboxTool.model_construct(
            db_pool=MagicMock(),
            name="base_tool",
            description="Base",
        )

        with pytest.raises(NotImplementedError, match="Subclasses must implement"):
            import asyncio

            asyncio.get_event_loop().run_until_complete(tool._arun())

    def test_sync_run_method(self):
        """Test the synchronous _run wrapper."""
        tool = MockTool.model_construct(
            db_pool=MagicMock(),
            thread_id=None,
            name="test_tool",
            description="Test tool",
        )
        # Call the sync _run method
        result = tool._run()
        assert result == "test"

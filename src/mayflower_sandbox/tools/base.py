"""
Base class for all Mayflower Sandbox tools.
"""

import asyncpg
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from pydantic import ConfigDict


class SandboxTool(BaseTool):
    """
    Base class for all sandbox tools.

    Provides connection to PostgreSQL and thread isolation.

    The thread_id can be provided at initialization or will be read from
    the callback context at runtime (recommended for LangGraph).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    db_pool: asyncpg.Pool
    thread_id: str | None = None

    def _run(
        self,
        run_manager: CallbackManagerForToolRun | None = None,
        **kwargs,
    ) -> str:
        """Sync interface - runs async method in event loop."""
        import asyncio

        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, need to use run_coroutine_threadsafe

                future = asyncio.run_coroutine_threadsafe(self._arun(**kwargs), loop)
                return future.result()
            else:
                return loop.run_until_complete(self._arun(**kwargs))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self._arun(**kwargs))

    def _get_thread_id(self, run_manager: AsyncCallbackManagerForToolRun | None = None) -> str:
        """Get thread_id from callback context or use instance default.

        Priority order:
        1. From LangGraph config via callback metadata
        2. From callback tags (alternative location)
        3. From instance thread_id (if set)
        4. Default fallback: "default"
        """
        return (
            self._get_thread_id_from_metadata(run_manager)
            or self._get_thread_id_from_tags(run_manager)
            or self.thread_id
            or "default"
        )

    def _get_thread_id_from_metadata(
        self,
        run_manager: AsyncCallbackManagerForToolRun | None,
    ) -> str | None:
        """Extract thread_id from callback manager metadata."""
        if not run_manager or not hasattr(run_manager, "metadata"):
            return None
        metadata = run_manager.metadata or {}
        return metadata.get("configurable", {}).get("thread_id")

    def _get_thread_id_from_tags(
        self,
        run_manager: AsyncCallbackManagerForToolRun | None,
    ) -> str | None:
        """Extract thread_id from callback manager tags."""
        if not run_manager or not hasattr(run_manager, "tags"):
            return None
        for tag in run_manager.tags or []:
            if isinstance(tag, str) and tag.startswith("thread_id:"):
                return tag.split(":", 1)[1]
        return None

    async def _arun(
        self,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs,
    ) -> str:  # type: ignore[override]
        """Async implementation - override in subclasses."""
        raise NotImplementedError("Subclasses must implement _arun")

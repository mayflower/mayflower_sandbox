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
        # Try to get from LangGraph config via callback manager
        if run_manager and hasattr(run_manager, "metadata"):
            metadata = run_manager.metadata or {}
            if "configurable" in metadata:
                thread_id = metadata["configurable"].get("thread_id")
                if thread_id:
                    return thread_id

        # Try to get from tags (alternative location)
        if run_manager and hasattr(run_manager, "tags"):
            tags = run_manager.tags or []
            for tag in tags:
                if isinstance(tag, str) and tag.startswith("thread_id:"):
                    return tag.split(":", 1)[1]

        # Fallback to instance thread_id
        if self.thread_id:
            return self.thread_id

        # Last resort default
        return "default"

    async def _arun(
        self,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs,
    ) -> str:  # type: ignore[override]
        """Async implementation - override in subclasses."""
        raise NotImplementedError("Subclasses must implement _arun")

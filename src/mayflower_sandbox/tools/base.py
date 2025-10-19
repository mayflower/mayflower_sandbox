"""
Base class for all Mayflower Sandbox tools.
"""

from typing import Optional
from pydantic import ConfigDict
from langchain_core.tools import BaseTool
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
import asyncpg


class SandboxTool(BaseTool):
    """
    Base class for all sandbox tools.

    Provides connection to PostgreSQL and thread isolation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    db_pool: asyncpg.Pool
    thread_id: str

    def _run(
        self,
        run_manager: Optional[CallbackManagerForToolRun] = None,
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

    async def _arun(
        self,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        **kwargs,
    ) -> str:
        """Async implementation - override in subclasses."""
        raise NotImplementedError("Subclasses must implement _arun")

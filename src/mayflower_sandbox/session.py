"""
Session recovery and persistence management.

Enables stateful Python execution where variables persist across
executor restarts by storing session state in PostgreSQL.
"""

import json
import logging

import asyncpg

from mayflower_sandbox.manager import SandboxManager
from mayflower_sandbox.sandbox_executor import ExecutionResult, SandboxExecutor

logger = logging.getLogger(__name__)


class SessionRecovery:
    """Handles session state persistence and recovery.

    Manages:
    - Saving session_bytes to PostgreSQL after execution
    - Loading session_bytes from PostgreSQL before execution
    - Recovery after long periods of inactivity
    """

    def __init__(self, db_pool: asyncpg.Pool):
        """Initialize session recovery.

        Args:
            db_pool: PostgreSQL connection pool
        """
        self.db = db_pool

    async def save_session_bytes(
        self,
        thread_id: str,
        session_bytes: bytes | None,
        session_metadata: dict | None,
    ) -> None:
        """Save session bytes to database.

        Args:
            thread_id: Thread identifier
            session_bytes: Pyodide session state bytes (pickled with dill)
            session_metadata: Session metadata
        """
        if session_bytes is None:
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sandbox_session_bytes (
                    thread_id, session_bytes, session_metadata
                ) VALUES ($1, $2, $3)
                ON CONFLICT (thread_id) DO UPDATE SET
                    session_bytes = EXCLUDED.session_bytes,
                    session_metadata = EXCLUDED.session_metadata,
                    updated_at = NOW()
            """,
                thread_id,
                session_bytes,
                json.dumps(session_metadata or {}),
            )

            logger.debug(f"Saved session bytes for thread {thread_id} ({len(session_bytes)} bytes)")

    async def load_session_bytes(
        self,
        thread_id: str,
    ) -> tuple[bytes | None, dict | None]:
        """Load session bytes from database.

        Args:
            thread_id: Thread identifier

        Returns:
            Tuple of (session_bytes, session_metadata)
        """
        async with self.db.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT session_bytes, session_metadata
                FROM sandbox_session_bytes
                WHERE thread_id = $1
            """,
                thread_id,
            )

            if result:
                logger.debug(
                    f"Loaded session bytes for thread {thread_id} "
                    f"({len(result['session_bytes'])} bytes)"
                )
                metadata = result["session_metadata"]
                # Parse JSON string to dict if needed
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                return result["session_bytes"], metadata

            return None, None

    async def delete_session_bytes(self, thread_id: str) -> bool:
        """Delete session bytes for a thread.

        Args:
            thread_id: Thread identifier

        Returns:
            True if session was deleted, False if didn't exist
        """
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM sandbox_session_bytes
                WHERE thread_id = $1
            """,
                thread_id,
            )

            deleted = int(result.split()[-1])
            if deleted > 0:
                logger.debug(f"Deleted session bytes for thread {thread_id}")
            return deleted > 0


class StatefulExecutor:
    """Executor with automatic session persistence.

    Combines SandboxExecutor with SessionRecovery to automatically
    save and load session state from PostgreSQL.

    This enables stateful Python execution where variables persist
    across executions, even after system restarts.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        thread_id: str,
        allow_net: bool = False,
        timeout_seconds: float = 60.0,
    ):
        """Initialize stateful executor.

        Args:
            db_pool: PostgreSQL connection pool
            thread_id: Thread identifier for this session
            allow_net: Allow network access in Python code
            timeout_seconds: Execution timeout
        """
        self.db_pool = db_pool
        self.thread_id = thread_id
        self.executor = SandboxExecutor(db_pool, thread_id, allow_net=allow_net, stateful=True)
        self.recovery = SessionRecovery(db_pool)
        self.manager = SandboxManager(db_pool)

    async def execute(
        self,
        code: str,
    ) -> ExecutionResult:
        """Execute code with automatic session recovery.

        The session state (variables, imports, etc.) is automatically:
        1. Loaded from PostgreSQL before execution
        2. Saved back to PostgreSQL after successful execution

        This means variables persist across calls, even across restarts.

        Args:
            code: Python code to execute

        Returns:
            ExecutionResult with stdout, stderr, created files, etc.

        Example:
            >>> executor = StatefulExecutor(db_pool, "user_123")
            >>> await executor.execute("x = 42")
            >>> result = await executor.execute("print(x)")
            >>> assert "42" in result.stdout  # x persisted!
        """
        # Ensure session exists
        await self.manager.get_or_create_session(self.thread_id)

        # Load previous session state from PostgreSQL
        session_bytes, session_metadata = await self.recovery.load_session_bytes(self.thread_id)

        if session_bytes:
            logger.info(
                f"Loaded session state for thread {self.thread_id} ({len(session_bytes)} bytes)"
            )

        # Execute code with loaded state
        result = await self.executor.execute(
            code, session_bytes=session_bytes, session_metadata=session_metadata
        )

        # Save updated session state back to PostgreSQL (only on success)
        if result.success and result.session_bytes:
            await self.recovery.save_session_bytes(
                self.thread_id,
                result.session_bytes,
                result.session_metadata,
            )
            logger.info(
                f"Saved session state for thread {self.thread_id} "
                f"({len(result.session_bytes)} bytes)"
            )

        # Update last accessed timestamp
        await self.manager.update_last_accessed(self.thread_id)

        return result

    async def reset_session(self) -> None:
        """Reset the session by deleting stored state.

        This clears all variables, imports, etc. The next execution
        will start with a fresh Python environment.
        """
        deleted = await self.recovery.delete_session_bytes(self.thread_id)
        if deleted:
            logger.info(f"Reset session state for thread {self.thread_id}")
        else:
            logger.debug(f"No session state to reset for thread {self.thread_id}")

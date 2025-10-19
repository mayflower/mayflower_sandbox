"""Session lifecycle management for sandbox threads."""

import json
import logging
from datetime import datetime, timedelta

import asyncpg


logger = logging.getLogger(__name__)


class SessionNotFoundError(Exception):
    """Session does not exist."""


class SessionExpiredError(Exception):
    """Session has expired."""


class SandboxManager:
    """Manages sandbox session lifecycle and expiration.

    Handles:
    - Session creation and retrieval
    - Expiration tracking (default 6 months)
    - Last accessed timestamp updates
    - Cleanup of expired sessions
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        default_expiration_days: int = 180,
    ):
        """Initialize manager.

        Args:
            db_pool: PostgreSQL connection pool
            default_expiration_days: Default session lifetime (default: 180 days)
        """
        self.db = db_pool
        self.default_expiration_days = default_expiration_days

    async def get_or_create_session(
        self,
        thread_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """Get existing session or create new one.

        Args:
            thread_id: Thread identifier
            metadata: Optional metadata for new session

        Returns:
            Session record with all fields

        Raises:
            SessionExpiredError: If session exists but has expired
        """
        async with self.db.acquire() as conn:
            # Try to get existing session
            session = await conn.fetchrow(
                "SELECT * FROM sandbox_sessions WHERE thread_id = $1", thread_id
            )

            if session:
                # Check if expired
                if session["expires_at"] < datetime.now():
                    raise SessionExpiredError(
                        f"Session {thread_id} expired at {session['expires_at']}"
                    )

                # Update last accessed
                await self.update_last_accessed(thread_id)
                return dict(session)

            # Create new session
            expires_at = datetime.now() + timedelta(days=self.default_expiration_days)
            session = await conn.fetchrow(
                """
                INSERT INTO sandbox_sessions (
                    thread_id, expires_at, metadata
                ) VALUES ($1, $2, $3::jsonb)
                RETURNING *
            """,
                thread_id,
                expires_at,
                json.dumps(metadata or {}),
            )

            logger.info(f"Created new session for thread {thread_id}, expires {expires_at}")
            return dict(session)

    async def get_session(self, thread_id: str) -> dict:
        """Get existing session.

        Args:
            thread_id: Thread identifier

        Returns:
            Session record

        Raises:
            SessionNotFoundError: If session doesn't exist
            SessionExpiredError: If session has expired
        """
        async with self.db.acquire() as conn:
            session = await conn.fetchrow(
                "SELECT * FROM sandbox_sessions WHERE thread_id = $1", thread_id
            )

            if not session:
                raise SessionNotFoundError(f"Session {thread_id} not found")

            if session["expires_at"] < datetime.now():
                raise SessionExpiredError(f"Session {thread_id} expired at {session['expires_at']}")

            return dict(session)

    async def update_last_accessed(self, thread_id: str) -> None:
        """Update last accessed timestamp.

        Args:
            thread_id: Thread identifier
        """
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE sandbox_sessions
                SET last_accessed = NOW()
                WHERE thread_id = $1
            """,
                thread_id,
            )

    async def cleanup_expired_sessions(self) -> int:
        """Delete all expired sessions.

        Cascade deletes files and session bytes.

        Returns:
            Number of sessions deleted
        """
        async with self.db.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM sandbox_sessions
                WHERE expires_at < NOW()
            """)

            # Parse "DELETE N" result
            count = int(result.split()[-1])
            if count > 0:
                logger.info(f"Cleaned up {count} expired sessions")
            return count

    async def list_active_sessions(self, limit: int = 100) -> list[dict]:
        """List active (non-expired) sessions.

        Args:
            limit: Maximum number of sessions to return

        Returns:
            List of session records
        """
        async with self.db.acquire() as conn:
            sessions = await conn.fetch(
                """
                SELECT * FROM sandbox_sessions
                WHERE expires_at > NOW()
                ORDER BY last_accessed DESC
                LIMIT $1
            """,
                limit,
            )

            return [dict(s) for s in sessions]

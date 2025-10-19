"""
Cleanup job for expired sessions and orphaned files.
"""

import asyncio
import logging
from datetime import datetime

import asyncpg

from mayflower_sandbox.manager import SandboxManager

logger = logging.getLogger(__name__)


class CleanupJob:
    """Periodic cleanup job for sandbox resources."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        interval_seconds: int = 3600,  # 1 hour default
        dry_run: bool = False,
    ):
        """Initialize cleanup job.

        Args:
            db_pool: PostgreSQL connection pool
            interval_seconds: How often to run cleanup (default: 1 hour)
            dry_run: If True, report what would be deleted without deleting
        """
        self.db_pool = db_pool
        self.interval_seconds = interval_seconds
        self.dry_run = dry_run
        self.manager = SandboxManager(db_pool)
        self._running = False
        self._task: asyncio.Task | None = None

    async def cleanup_expired_sessions(self) -> dict:
        """Clean up expired sessions and their files.

        Returns:
            Dict with cleanup statistics
        """
        stats = {"sessions_deleted": 0, "files_deleted": 0, "bytes_freed": 0}

        async with self.db_pool.acquire() as conn:
            # Get expired sessions
            expired = await conn.fetch("""
                SELECT thread_id FROM sandbox_sessions
                WHERE expires_at < NOW()
            """)

            if not expired:
                logger.info("No expired sessions to clean up")
                return stats

            thread_ids = [row["thread_id"] for row in expired]
            logger.info(f"Found {len(thread_ids)} expired sessions to clean up")

            # Get file counts and sizes before deletion
            file_stats = await conn.fetch(
                """
                SELECT thread_id, COUNT(*) as file_count, SUM(size) as total_size
                FROM sandbox_filesystem
                WHERE thread_id = ANY($1::text[])
                GROUP BY thread_id
            """,
                thread_ids,
            )

            for row in file_stats:
                stats["files_deleted"] += row["file_count"]
                stats["bytes_freed"] += row["total_size"] or 0

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Would delete {len(thread_ids)} sessions, "
                    f"{stats['files_deleted']} files, "
                    f"{stats['bytes_freed']} bytes"
                )
                return stats

            # Delete sessions (cascades to files and session_bytes)
            deleted_count = await self.manager.cleanup_expired_sessions()
            stats["sessions_deleted"] = deleted_count

            logger.info(
                f"Cleanup complete: {deleted_count} sessions, "
                f"{stats['files_deleted']} files, "
                f"{stats['bytes_freed'] / (1024 * 1024):.2f} MB freed"
            )

        return stats

    async def cleanup_orphaned_files(self) -> dict:
        """Clean up files without corresponding sessions.

        Returns:
            Dict with cleanup statistics
        """
        stats = {"files_deleted": 0, "bytes_freed": 0}

        async with self.db_pool.acquire() as conn:
            # Find orphaned files
            orphaned = await conn.fetch("""
                SELECT f.thread_id, f.file_path, f.size
                FROM sandbox_filesystem f
                LEFT JOIN sandbox_sessions s ON f.thread_id = s.thread_id
                WHERE s.thread_id IS NULL
            """)

            if not orphaned:
                logger.info("No orphaned files to clean up")
                return stats

            stats["files_deleted"] = len(orphaned)
            stats["bytes_freed"] = sum(row["size"] for row in orphaned)

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Would delete {stats['files_deleted']} orphaned files, "
                    f"{stats['bytes_freed']} bytes"
                )
                return stats

            # Delete orphaned files
            await conn.execute("""
                DELETE FROM sandbox_filesystem f
                WHERE NOT EXISTS (
                    SELECT 1 FROM sandbox_sessions s
                    WHERE s.thread_id = f.thread_id
                )
            """)

            logger.info(
                f"Deleted {stats['files_deleted']} orphaned files, "
                f"{stats['bytes_freed'] / (1024 * 1024):.2f} MB freed"
            )

        return stats

    async def run_once(self) -> dict:
        """Run cleanup once.

        Returns:
            Combined statistics from all cleanup operations
        """
        logger.info("Starting cleanup cycle")
        start_time = datetime.now()

        session_stats = await self.cleanup_expired_sessions()
        orphan_stats = await self.cleanup_orphaned_files()

        elapsed = (datetime.now() - start_time).total_seconds()

        combined_stats = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": elapsed,
            "sessions_deleted": session_stats["sessions_deleted"],
            "files_deleted": session_stats["files_deleted"] + orphan_stats["files_deleted"],
            "bytes_freed": session_stats["bytes_freed"] + orphan_stats["bytes_freed"],
        }

        logger.info(f"Cleanup cycle complete in {elapsed:.2f}s: {combined_stats}")
        return combined_stats

    async def _run_loop(self):
        """Internal loop for periodic cleanup."""
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Error in cleanup cycle: {e}", exc_info=True)

            # Wait for next cycle
            await asyncio.sleep(self.interval_seconds)

    def start(self):
        """Start periodic cleanup in background."""
        if self._running:
            logger.warning("Cleanup job already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Started cleanup job (interval: {self.interval_seconds}s, dry_run: {self.dry_run})"
        )

    async def stop(self):
        """Stop periodic cleanup."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Stopped cleanup job")

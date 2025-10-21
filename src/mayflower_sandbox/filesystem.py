"""PostgreSQL-backed virtual filesystem for sandbox.

This VFS serves as the persistent layer. Files stored here will be:
- Loaded into Pyodide memfs before execution (pre-load)
- Saved from Pyodide memfs after execution (post-save)
"""

import logging
import mimetypes
import re
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)


class FileNotFoundError(Exception):
    """File does not exist."""


class FileTooLargeError(Exception):
    """File exceeds size limit."""


class InvalidPathError(Exception):
    """Path is invalid or outside allowed sandbox."""


class VirtualFilesystem:
    """Thread-isolated virtual filesystem backed by PostgreSQL.

    Features:
    - Per-thread file isolation
    - 20MB file size limit
    - Automatic MIME type detection
    - Path validation and sanitization
    """

    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

    def __init__(self, db_pool: asyncpg.Pool, thread_id: str):
        """Initialize filesystem for specific thread.

        Args:
            db_pool: PostgreSQL connection pool
            thread_id: Thread identifier for isolation
        """
        self.db = db_pool
        self.thread_id = thread_id

    def validate_path(self, file_path: str) -> str:
        """Validate and normalize file path.

        Args:
            file_path: Path to validate

        Returns:
            Normalized path

        Raises:
            InvalidPathError: If path is invalid
        """
        # Normalize path
        normalized = str(Path(file_path).as_posix())

        # Ensure absolute path
        if not normalized.startswith("/"):
            normalized = "/" + normalized

        # Reject parent directory references
        if ".." in normalized:
            raise InvalidPathError(f"Path traversal detected: {file_path}")

        # Reject special characters that could cause issues
        if re.search(r'[<>:"|?*\x00-\x1f]', normalized):
            raise InvalidPathError(f"Invalid characters in path: {file_path}")

        return normalized

    def detect_content_type(self, file_path: str, content: bytes) -> str:
        """Detect MIME type from file extension and content.

        Args:
            file_path: File path for extension detection
            content: File content for magic detection

        Returns:
            MIME type string
        """
        # Try extension-based detection first
        mime_type, _ = mimetypes.guess_type(file_path)

        if mime_type:
            return mime_type

        # Fallback based on content inspection
        if content.startswith(b"\x89PNG"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"GIF8"):
            return "image/gif"
        if content.startswith(b"%PDF"):
            return "application/pdf"

        # Check if likely text
        try:
            content[:1024].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            return "application/octet-stream"

    async def write_file(
        self,
        file_path: str,
        content: bytes,
        content_type: str | None = None,
    ) -> dict:
        """Write file to filesystem.

        Args:
            file_path: Path to write
            content: File content as bytes
            content_type: Optional MIME type (auto-detected if None)

        Returns:
            File metadata dict

        Raises:
            InvalidPathError: If path is invalid
            FileTooLargeError: If content exceeds 20MB
        """
        # Validate path
        normalized_path = self.validate_path(file_path)

        # Check size
        size = len(content)
        if size > self.MAX_FILE_SIZE:
            raise FileTooLargeError(
                f"File size {size} bytes exceeds limit of {self.MAX_FILE_SIZE} bytes"
            )

        # Detect content type if not provided
        if content_type is None:
            content_type = self.detect_content_type(normalized_path, content)

        # Upsert file
        async with self.db.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO sandbox_filesystem (
                    thread_id, file_path, content, content_type, size
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (thread_id, file_path)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    content_type = EXCLUDED.content_type,
                    size = EXCLUDED.size,
                    modified_at = NOW()
                RETURNING *
            """,
                self.thread_id,
                normalized_path,
                content,
                content_type,
                size,
            )

            logger.debug(f"Wrote file {normalized_path} ({size} bytes) for thread {self.thread_id}")

            return dict(result) if result else {}

    async def read_file(self, file_path: str) -> dict:
        """Read file from filesystem.

        Args:
            file_path: Path to read

        Returns:
            Dict with content, content_type, size, etc.

        Raises:
            InvalidPathError: If path is invalid
            FileNotFoundError: If file doesn't exist
        """
        normalized_path = self.validate_path(file_path)

        async with self.db.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT * FROM sandbox_filesystem
                WHERE thread_id = $1 AND file_path = $2
            """,
                self.thread_id,
                normalized_path,
            )

            if not result:
                raise FileNotFoundError(
                    f"File {normalized_path} not found in thread {self.thread_id}"
                )

            return dict(result)

    async def delete_file(self, file_path: str) -> bool:
        """Delete file from filesystem.

        Args:
            file_path: Path to delete

        Returns:
            True if file was deleted, False if didn't exist

        Raises:
            InvalidPathError: If path is invalid
        """
        normalized_path = self.validate_path(file_path)

        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM sandbox_filesystem
                WHERE thread_id = $1 AND file_path = $2
            """,
                self.thread_id,
                normalized_path,
            )

            # Parse "DELETE N" result
            deleted = int(result.split()[-1])
            return deleted > 0

    async def list_files(self, pattern: str | None = None) -> list[dict]:
        """List all files in filesystem.

        Args:
            pattern: Optional SQL LIKE pattern for filtering

        Returns:
            List of file metadata dicts
        """
        async with self.db.acquire() as conn:
            if pattern:
                files = await conn.fetch(
                    """
                    SELECT * FROM sandbox_filesystem
                    WHERE thread_id = $1 AND file_path LIKE $2
                    ORDER BY file_path
                """,
                    self.thread_id,
                    pattern,
                )
            else:
                files = await conn.fetch(
                    """
                    SELECT * FROM sandbox_filesystem
                    WHERE thread_id = $1
                    ORDER BY file_path
                """,
                    self.thread_id,
                )

            return [dict(f) for f in files]

    async def file_exists(self, file_path: str) -> bool:
        """Check if file exists.

        Args:
            file_path: Path to check

        Returns:
            True if file exists
        """
        try:
            normalized_path = self.validate_path(file_path)
            async with self.db.acquire() as conn:
                result = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM sandbox_filesystem
                        WHERE thread_id = $1 AND file_path = $2
                    )
                """,
                    self.thread_id,
                    normalized_path,
                )
                return result
        except InvalidPathError:
            return False

    async def get_all_files_for_pyodide(self) -> dict[str, bytes]:
        """Get all files as dict for Pyodide pre-load.

        Returns:
            Dict mapping file_path → content (bytes)
        """
        files = await self.list_files()
        return {f["file_path"]: f["content"] for f in files}

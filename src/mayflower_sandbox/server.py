"""
HTTP server for serving files from sandbox VFS.

Provides endpoints for downloading files created by sandbox executions.
"""

import logging

import asyncpg
from aiohttp import web

from mayflower_sandbox.filesystem import VirtualFilesystem

logger = logging.getLogger(__name__)


class FileServer:
    """HTTP server for serving sandbox files."""

    def __init__(self, db_pool: asyncpg.Pool, host: str = "0.0.0.0", port: int = 8080):  # nosec B104 - intentional for container deployment
        """Initialize file server.

        Args:
            db_pool: PostgreSQL connection pool
            host: Server host (default: 0.0.0.0)
            port: Server port (default: 8080)
        """
        self.db_pool = db_pool
        self.host = host
        self.port = port
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        """Setup HTTP routes."""
        self.app.router.add_get("/health", self.health_check)
        self.app.router.add_get("/files/{thread_id}/{file_path:.*}", self.serve_file)
        self.app.router.add_get("/files/{thread_id}", self.list_files)

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint with database connectivity verification."""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return web.json_response({"status": "healthy", "service": "mayflower-sandbox"})
        except Exception:
            return web.json_response(
                {
                    "status": "unhealthy",
                    "service": "mayflower-sandbox",
                    "error": "database unavailable",
                },
                status=503,
            )

    async def serve_file(self, request: web.Request) -> web.Response:
        """Serve a file from VFS.

        URL: GET /files/{thread_id}/{file_path}

        Example: GET /files/user_123/tmp/data.csv
        """
        thread_id = request.match_info["thread_id"]
        file_path = "/" + request.match_info["file_path"]

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            file_info = await vfs.read_file(file_path)

            # Determine if we should force download or display inline
            disposition = request.query.get("disposition", "inline")

            headers = {
                "Content-Type": file_info["content_type"],
                "Content-Disposition": f'{disposition}; filename="{file_path.split("/")[-1]}"',
            }

            return web.Response(body=file_info["content"], headers=headers)

        except Exception as e:
            # Check if it's a FileNotFoundError (could be built-in or custom)
            if "not found" in str(e).lower() or isinstance(e, FileNotFoundError):
                return web.json_response(
                    {"error": "File not found", "thread_id": thread_id, "file_path": file_path},
                    status=404,
                )
            logger.error(f"Error serving file {file_path} for thread {thread_id}: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def list_files(self, request: web.Request) -> web.Response:
        """List files for a thread.

        URL: GET /files/{thread_id}?prefix=/tmp/

        Query params:
            prefix: Optional path prefix filter
        """
        thread_id = request.match_info["thread_id"]
        prefix = request.query.get("prefix", "")

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            pattern = f"{prefix}%" if prefix else None
            files = await vfs.list_files(pattern=pattern)

            # Convert bytes to base64 for JSON serialization (exclude content)
            file_list = [
                {
                    "file_path": f["file_path"],
                    "size": f["size"],
                    "content_type": f["content_type"],
                    "created_at": f["created_at"].isoformat(),
                    "modified_at": f["modified_at"].isoformat(),
                }
                for f in files
            ]

            return web.json_response(
                {"thread_id": thread_id, "count": len(file_list), "files": file_list}
            )

        except Exception as e:
            logger.error(f"Error listing files for thread {thread_id}: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def start(self):
        """Start the server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"File server started on http://{self.host}:{self.port}")
        return runner

    def run(self):
        """Run the server (blocking)."""
        web.run_app(self.app, host=self.host, port=self.port)


def create_file_server(
    db_pool: asyncpg.Pool,
    host: str = "0.0.0.0",  # nosec B104 - intentional for container deployment
    port: int = 8080,
) -> FileServer:
    """Create and configure file server.

    Args:
        db_pool: PostgreSQL connection pool
        host: Server host
        port: Server port

    Returns:
        Configured FileServer instance
    """
    return FileServer(db_pool, host, port)

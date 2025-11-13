"""
Mayflower Sandbox - Pyodide Worker Pool

Manages a pool of long-running Deno workers for fast Python execution.
Provides 70-95% performance improvement over one-shot processes.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncio.subprocess import Process

logger = logging.getLogger(__name__)


class PyodideWorker:
    """Single long-running Deno worker process with JSON-RPC communication."""

    def __init__(self, worker_id: int, executor_path: Path):
        self.worker_id = worker_id
        self.executor_path = executor_path
        self.process: Process | None = None
        self.busy = False
        self.request_count = 0
        self.lock = asyncio.Lock()
        self._request_id = 0

    async def _read_large_line(self, reader: asyncio.StreamReader) -> bytes:
        """Read a line with support for large responses (up to 10MB)."""
        chunks = []
        total_size = 0
        max_size = 10 * 1024 * 1024  # 10MB

        while True:
            chunk = await reader.read(8192)
            if not chunk:
                break

            chunks.append(chunk)
            total_size += len(chunk)

            if total_size > max_size:
                raise RuntimeError("Response exceeded 10MB limit")

            # Check if we've read a complete line
            if b"\n" in chunk:
                # Find the newline and keep only up to it
                full_data = b"".join(chunks)
                newline_pos = full_data.find(b"\n")
                # Put back the extra data
                if newline_pos < len(full_data) - 1:
                    extra = full_data[newline_pos + 1 :]
                    reader._buffer = extra + reader._buffer  # type: ignore[attr-defined]
                return full_data[: newline_pos + 1]

        return b"".join(chunks)

    async def start(self) -> None:
        """Start the Deno worker process."""
        logger.info(f"[Worker {self.worker_id}] Starting...")

        # Allow PyPI for micropip.install() to work
        allowed_hosts = "cdn.jsdelivr.net,pypi.org,files.pythonhosted.org"

        self.process = await asyncio.create_subprocess_exec(
            "deno",
            "run",
            f"--allow-net={allowed_hosts}",
            "--allow-read",
            "--allow-write",
            str(self.executor_path / "worker_server.ts"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for initialization (read stderr for "Ready" message)
        try:
            await asyncio.wait_for(self._wait_ready(), timeout=15.0)
            logger.info(f"[Worker {self.worker_id}] Ready (PID: {self.process.pid})")
        except asyncio.TimeoutError:
            logger.error(f"[Worker {self.worker_id}] Initialization timeout")
            await self.kill()
            raise RuntimeError(f"Worker {self.worker_id} failed to initialize")

    async def _wait_ready(self) -> None:
        """Wait for worker to print 'Ready' to stderr."""
        if not self.process or not self.process.stderr:
            raise RuntimeError("Process not started")

        while True:
            line = await self.process.stderr.readline()
            if not line:
                raise RuntimeError("Worker process terminated during initialization")
            if b"Ready" in line:
                logger.debug(f"[Worker {self.worker_id}] {line.decode().strip()}")
                return

    async def execute(
        self,
        code: str,
        thread_id: str,
        stateful: bool = False,
        session_bytes: bytes | None = None,
        session_metadata: dict | None = None,
        files: dict[str, bytes] | None = None,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Execute code in this worker via JSON-RPC."""

        async with self.lock:
            if not self.process or self.process.returncode is not None:
                raise RuntimeError(f"Worker {self.worker_id} not running")

            self.busy = True
            self.request_count += 1
            self._request_id += 1

            try:
                # Build JSON-RPC request
                request = {
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": "execute",
                    "params": {
                        "code": code,
                        "thread_id": thread_id,
                        "stateful": stateful,
                        "timeout_ms": timeout_ms,
                    },
                }

                if session_bytes:
                    request["params"]["session_bytes"] = list(session_bytes)  # type: ignore[index]
                if session_metadata:
                    request["params"]["session_metadata"] = session_metadata  # type: ignore[index]
                if files:
                    request["params"]["files"] = {  # type: ignore[index]
                        path: list(content) for path, content in files.items()
                    }

                # Send request
                request_line = json.dumps(request) + "\n"
                if not self.process.stdin:
                    raise RuntimeError("Worker stdin not available")

                self.process.stdin.write(request_line.encode())
                await self.process.stdin.drain()

                # Read response (with timeout)
                if not self.process.stdout:
                    raise RuntimeError("Worker stdout not available")

                # Use large limit for responses with file contents (e.g., images)
                # Note: asyncio StreamReader has an internal limit that we need to work around
                # by reading in chunks if the response is very large
                try:
                    response_line = await asyncio.wait_for(
                        self._read_large_line(self.process.stdout),
                        timeout=timeout_ms / 1000.0 + 5.0,
                    )
                except asyncio.LimitOverrunError:
                    raise RuntimeError("Response too large (>10MB)")

                if not response_line:
                    raise RuntimeError("Worker closed stdout")

                response = json.loads(response_line)

                if "error" in response:
                    raise RuntimeError(f"Worker error: {response['error']['message']}")

                return response["result"]

            finally:
                self.busy = False

    async def health_check(self) -> dict[str, Any]:
        """Check worker health."""
        async with self.lock:
            if not self.process or self.process.returncode is not None:
                return {"status": "dead", "error": "Process not running"}

            try:
                request = {"jsonrpc": "2.0", "id": 0, "method": "health", "params": {}}

                if not self.process.stdin or not self.process.stdout:
                    return {"status": "dead", "error": "Streams not available"}

                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()

                response_line = await asyncio.wait_for(self.process.stdout.readline(), timeout=5.0)

                if not response_line:
                    return {"status": "dead", "error": "No response"}

                response = json.loads(response_line)
                return response.get("result", {"status": "unknown"})

            except asyncio.TimeoutError:
                return {"status": "timeout", "error": "Health check timeout"}
            except Exception as e:
                return {"status": "error", "error": str(e)}

    async def shutdown(self) -> None:
        """Gracefully shutdown the worker."""
        if not self.process or self.process.returncode is not None:
            return

        try:
            # Send shutdown command
            request = {"jsonrpc": "2.0", "id": -1, "method": "shutdown", "params": {}}

            if self.process.stdin:
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()

            # Wait for graceful shutdown
            await asyncio.wait_for(self.process.wait(), timeout=2.0)
        except (asyncio.TimeoutError, BrokenPipeError):
            # Force kill if graceful shutdown fails
            await self.kill()

    async def kill(self) -> None:
        """Force kill the worker process."""
        if self.process:
            try:
                self.process.kill()
                await self.process.wait()
            except ProcessLookupError:
                pass
            self.process = None


class WorkerPool:
    """Pool of Pyodide workers with load balancing and auto-recovery."""

    def __init__(self, size: int = 3, executor_path: Path | None = None):
        self.size = size
        self.executor_path = executor_path or Path(__file__).parent
        self.workers: list[PyodideWorker] = []
        self.next_worker_idx = 0
        self.started = False
        self._health_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start all workers in the pool."""
        if self.started:
            return

        logger.info(f"Starting worker pool (size={self.size})...")

        # Create workers
        self.workers = [PyodideWorker(i, self.executor_path) for i in range(self.size)]

        # Start workers in parallel
        start_tasks = [w.start() for w in self.workers]
        results = await asyncio.gather(*start_tasks, return_exceptions=True)

        # Check for failures
        failed = [i for i, r in enumerate(results) if isinstance(r, Exception)]
        if failed:
            logger.error(f"Failed to start workers: {failed}")
            await self.shutdown()
            raise RuntimeError(f"Failed to start {len(failed)} workers")

        self.started = True
        logger.info(f"Worker pool ready with {self.size} workers")

        # Start health monitoring
        self._health_task = asyncio.create_task(self._health_monitor())

    async def execute(
        self,
        code: str,
        thread_id: str,
        stateful: bool = False,
        session_bytes: bytes | None = None,
        session_metadata: dict | None = None,
        files: dict[str, bytes] | None = None,
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        """Execute code using next available worker (round-robin with fallback)."""
        if not self.started:
            raise RuntimeError("Worker pool not started")

        # Try to find idle worker
        for _ in range(self.size):
            worker = self.workers[self.next_worker_idx]
            self.next_worker_idx = (self.next_worker_idx + 1) % self.size

            if not worker.busy:
                try:
                    return await worker.execute(
                        code=code,
                        thread_id=thread_id,
                        stateful=stateful,
                        session_bytes=session_bytes,
                        session_metadata=session_metadata,
                        files=files,
                        timeout_ms=timeout_ms,
                    )
                except Exception as e:
                    logger.error(f"Worker {worker.worker_id} execution failed: {e}")
                    # Try to restart worker
                    asyncio.create_task(self._restart_worker(worker))
                    # Continue to next worker

        # All busy or failed, use next in rotation anyway
        worker = self.workers[self.next_worker_idx]
        self.next_worker_idx = (self.next_worker_idx + 1) % self.size

        return await worker.execute(
            code=code,
            thread_id=thread_id,
            stateful=stateful,
            session_bytes=session_bytes,
            session_metadata=session_metadata,
            files=files,
            timeout_ms=timeout_ms,
        )

    async def _restart_worker(self, worker: PyodideWorker) -> None:
        """Restart a failed worker."""
        logger.warning(f"[Worker {worker.worker_id}] Restarting...")
        try:
            await worker.kill()
            await worker.start()
            logger.info(f"[Worker {worker.worker_id}] Restarted successfully")
        except Exception as e:
            logger.error(f"[Worker {worker.worker_id}] Restart failed: {e}")

    async def _health_monitor(self) -> None:
        """Background task to monitor worker health."""
        check_interval = int(os.getenv("PYODIDE_HEALTH_CHECK_INTERVAL", "30"))
        request_limit = int(os.getenv("PYODIDE_WORKER_REQUEST_LIMIT", "1000"))

        while self.started:
            await asyncio.sleep(check_interval)

            for worker in self.workers:
                try:
                    health = await worker.health_check()

                    if health.get("status") != "healthy":
                        logger.warning(
                            f"[Worker {worker.worker_id}] Unhealthy: {health.get('error')}, restarting..."
                        )
                        await self._restart_worker(worker)
                        continue

                    # Check for request limit (recycle to prevent memory leaks)
                    if health.get("request_count", 0) >= request_limit:
                        logger.info(
                            f"[Worker {worker.worker_id}] Reached request limit ({request_limit}), recycling..."
                        )
                        await self._restart_worker(worker)

                except Exception as e:
                    logger.error(f"[Worker {worker.worker_id}] Health check failed: {e}")
                    await self._restart_worker(worker)

    async def health_check_all(self) -> list[dict[str, Any]]:
        """Check health of all workers."""
        return await asyncio.gather(*[w.health_check() for w in self.workers])

    async def shutdown(self) -> None:
        """Shut down all workers gracefully."""
        logger.info("Shutting down worker pool...")
        self.started = False

        # Cancel health monitoring
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Shutdown all workers
        await asyncio.gather(*[w.shutdown() for w in self.workers], return_exceptions=True)

        logger.info("Worker pool shut down")

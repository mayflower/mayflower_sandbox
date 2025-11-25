"""
Mayflower Sandbox Executor - Clean VFS + Pyodide integration.

Written from scratch with better architecture than langchain-sandbox.
Provides unified interface for executing Python code with automatic VFS sync.
"""

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncpg

from .bootstrap import write_bootstrap_files
from .filesystem import VirtualFilesystem
from .mcp_bindings import MCPBindingManager

if TYPE_CHECKING:
    from .mcp_bridge_server import MCPBridgeServer
    from .worker_pool import WorkerPool

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result from executing Python code."""

    success: bool
    stdout: str
    stderr: str
    result: Any = None
    execution_time: float = 0.0
    created_files: list[str] | None = None
    session_bytes: bytes | None = None
    session_metadata: dict | None = None


class SandboxExecutor:
    """
    High-level executor that coordinates VFS and Pyodide.

    Clean API: Just call execute() and it handles everything:
    - Pre-loads files from PostgreSQL VFS
    - Executes code in Pyodide
    - Post-saves created files back to PostgreSQL
    """

    # Class-level worker pool (shared across all instances)
    _pool: "WorkerPool | None" = None
    _pool_lock = asyncio.Lock()
    _use_pool = os.getenv("PYODIDE_USE_POOL", "false").lower() == "true"

    # Class-level MCP bridge (shared across all pool instances)
    _mcp_bridge: "MCPBridgeServer | None" = None
    _mcp_bridge_lock = asyncio.Lock()

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        thread_id: str,
        *,
        allow_net: bool = False,
        stateful: bool = False,
        timeout_seconds: float = 60.0,
        max_memory_mb: int = 512,
        max_file_size_mb: int = 20,
        max_files: int = 100,
    ):
        """
        Initialize sandbox executor.

        Args:
            db_pool: PostgreSQL connection pool
            thread_id: Thread ID for VFS isolation
            allow_net: Allow network access
            stateful: Maintain state between executions
            timeout_seconds: Execution timeout
            max_memory_mb: Maximum memory usage (not enforced yet, placeholder)
            max_file_size_mb: Maximum total file size in VFS
            max_files: Maximum number of files in VFS
        """
        self.db_pool = db_pool
        self.thread_id = thread_id
        self.vfs = VirtualFilesystem(db_pool, thread_id)
        self._mcp_manager = MCPBindingManager()
        self.allow_net = allow_net
        self.stateful = stateful
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_file_size_mb = max_file_size_mb
        self.max_files = max_files

        # Get path to TypeScript executor
        self.executor_path = self._get_executor_path()

        # Verify Deno is installed
        self._check_deno()

        # Track if helpers are loaded
        self._helpers_loaded = False

    @classmethod
    async def _ensure_mcp_bridge(cls, db_pool: asyncpg.Pool, thread_id: str) -> int | None:
        """
        Ensure MCP bridge is started (lazy initialization).

        Returns the bridge port if started, or None if no MCP servers configured.
        """
        if cls._mcp_bridge is None:
            async with cls._mcp_bridge_lock:
                if cls._mcp_bridge is None:
                    from .mcp_bridge_server import MCPBridgeServer

                    bridge = MCPBridgeServer(db_pool, thread_id)
                    port = await bridge.start()

                    # Only keep it if there are MCP servers configured
                    if bridge._servers_cache:
                        cls._mcp_bridge = bridge
                        logger.info(
                            f"MCP bridge started on port {port} "
                            f"with servers: {list(bridge._servers_cache.keys())}"
                        )
                        return port
                    else:
                        # No servers configured, shut it down
                        await bridge.shutdown()
                        logger.debug("No MCP servers configured, bridge not started")
                        return None

        return cls._mcp_bridge.port if cls._mcp_bridge else None

    @classmethod
    async def _ensure_pool(cls, mcp_bridge_port: int | None = None) -> None:
        """Ensure worker pool is started (lazy initialization)."""
        if not cls._use_pool:
            return

        if cls._pool is None:
            async with cls._pool_lock:
                if cls._pool is None:  # Double-check locking
                    from .worker_pool import WorkerPool

                    pool_size = int(os.getenv("PYODIDE_POOL_SIZE", "3"))
                    logger.info(f"Initializing Pyodide worker pool (size={pool_size})...")

                    cls._pool = WorkerPool(
                        size=pool_size,
                        executor_path=Path(__file__).parent,
                        mcp_bridge_port=mcp_bridge_port,
                    )
                    await cls._pool.start()

                    logger.info("Pyodide worker pool ready!")

    def _get_executor_path(self) -> Path:
        """Get path to TypeScript executor."""
        executor = Path(__file__).parent / "executor.ts"
        if not executor.exists():
            raise RuntimeError(f"Executor not found at {executor}")
        return executor

    def _check_deno(self):
        """Verify Deno is installed."""
        try:
            subprocess.run(
                ["deno", "--version"],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(
                "Deno is not installed or not in PATH. Install from https://deno.land/"
            ) from e

    def _build_command(
        self,
        code: str,
        session_bytes: bytes | None = None,
        session_metadata: dict | None = None,
        *,
        mcp_bridge_port: int | None = None,
    ) -> list[str]:
        """Build Deno command."""
        cmd: list[str] = [
            "deno",
            "run",
            "--allow-read",
            "--allow-write",
        ]

        # Allow PyPI for micropip.install() to work
        allowed_hosts = {"cdn.jsdelivr.net", "pypi.org", "files.pythonhosted.org"}
        env_allow = os.environ.get("MAYFLOWER_SANDBOX_NET_ALLOW")
        if env_allow:
            for host in env_allow.split(","):
                host = host.strip()
                if host:
                    allowed_hosts.add(host)
        if mcp_bridge_port is not None:
            allowed_hosts.add(f"127.0.0.1:{mcp_bridge_port}")
        if self.allow_net:
            logger.warning(
                "allow_net=True requested for thread %s, but general network access is disabled. "
                "Only %s are permitted.",
                self.thread_id,
                ", ".join(sorted(allowed_hosts)),
            )
        cmd.append(f"--allow-net={','.join(sorted(allowed_hosts))}")

        cmd.extend(
            [
                str(self.executor_path),
                "-c",
                code,
            ]
        )

        if self.stateful:
            cmd.append("-s")

        if session_bytes:
            cmd.extend(["-b", json.dumps(list(session_bytes))])

        if session_metadata:
            cmd.extend(["-m", json.dumps(session_metadata)])

        return cmd

    def _prepare_stdin(self, files: dict[str, bytes]) -> bytes | None:
        """Prepare files for stdin using MFS binary protocol."""
        if not files:
            return None

        # MFS protocol: "MFS\x01" + length(4) + JSON metadata + file contents
        metadata = {
            "files": [{"path": path, "size": len(content)} for path, content in files.items()]
        }

        metadata_json = json.dumps(metadata).encode("utf-8")

        # Header: magic + version + length
        header = b"MFS\x01" + len(metadata_json).to_bytes(4, byteorder="big")

        # Concatenate: header + metadata + all file contents
        result = bytearray(header)
        result.extend(metadata_json)

        for content in files.values():
            result.extend(content)

        return bytes(result)

    async def _check_resource_quotas(self) -> tuple[bool, str | None]:
        """
        Check if resource quotas are exceeded.

        Returns:
            (within_limits, error_message) - error_message is None if within limits
        """
        vfs_files = await self.vfs.list_files()
        num_files = len(vfs_files)
        total_size = sum(f["size"] for f in vfs_files)
        total_size_mb = total_size / 1024 / 1024

        if num_files >= self.max_files:
            return False, (
                f"Error: File limit exceeded ({num_files}/{self.max_files}).\n"
                f"Delete some files first before creating new ones."
            )

        if total_size_mb >= self.max_file_size_mb:
            return False, (
                f"Error: Storage quota exceeded ({total_size_mb:.1f}MB/{self.max_file_size_mb}MB).\n"
                f"Delete some files first to free up space."
            )

        return True, None

    async def _preload_helpers(self) -> None:
        """Load all helper modules into VFS at /home/pyodide/"""
        if self._helpers_loaded:
            return

        helpers_dir = Path(__file__).parent / "helpers"

        if not helpers_dir.exists():
            logger.warning(f"Helpers directory not found at {helpers_dir}")
            self._helpers_loaded = True
            return

        helper_count = 0
        for py_file in helpers_dir.rglob("*.py"):
            # Calculate VFS path maintaining directory structure
            rel_path = py_file.relative_to(helpers_dir)
            vfs_path = f"/home/pyodide/{rel_path}"

            # Read file content
            content = py_file.read_bytes()

            # Write to VFS (persists across executions)
            await self.vfs.write_file(vfs_path, content)
            helper_count += 1

        logger.info(f"Preloaded {helper_count} helper modules into VFS for thread {self.thread_id}")
        self._helpers_loaded = True

    async def _get_mcp_server_configs(self) -> dict[str, dict[str, Any]]:
        async with self.db_pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT name, url, headers, auth
                    FROM sandbox_mcp_servers
                    WHERE thread_id = $1
                    """,
                    self.thread_id,
                )
            except asyncpg.UndefinedTableError:
                rows = []

        servers: dict[str, dict[str, Any]] = {}
        for row in rows:
            raw_headers = row["headers"] or {}
            if isinstance(raw_headers, str):
                raw_headers = json.loads(raw_headers)
            raw_auth = row["auth"] or {}
            if isinstance(raw_auth, str):
                raw_auth = json.loads(raw_auth)
            servers[row["name"]] = {
                "url": row["url"],
                "headers": dict(raw_headers),
                "auth": dict(raw_auth),
            }
        return servers

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, str | int | float | bool) or value is None:
            return value
        if isinstance(value, list | tuple | set):
            return list(value)
        if isinstance(value, dict):
            return value
        return str(value)

    async def _handle_mcp_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        servers: dict[str, dict[str, Any]],
    ) -> None:
        status = "200 OK"
        body = b"{}"
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            parts = request_line.decode("ascii", errors="ignore").strip().split()
            if len(parts) < 3:
                raise ValueError("Malformed HTTP request line")
            method, path = parts[0], parts[1]

            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                key, _, value = line.decode("ascii", errors="ignore").partition(":")
                headers[key.strip().lower()] = value.strip()

            content_length = int(headers.get("content-length", "0"))
            payload = await reader.readexactly(content_length) if content_length > 0 else b""

            if method != "POST" or path != "/call":
                status = "404 Not Found"
                body = json.dumps({"error": "Endpoint not found"}).encode("utf-8")
            else:
                data = json.loads(payload.decode("utf-8"))
                server_name = data.get("server")
                tool_name = data.get("tool")
                args = data.get("args") or {}

                if server_name not in servers:
                    raise RuntimeError(
                        f"MCP server '{server_name}' is not registered for this thread."
                    )

                config = servers[server_name]
                result = await self._mcp_manager.call(
                    self.thread_id,
                    server_name,
                    tool_name,
                    args,
                    url=config["url"],
                    headers=config.get("headers"),
                )
                body = json.dumps({"result": result}, default=self._json_default).encode("utf-8")
        except Exception as exc:  # noqa: BLE001 - return error payload to sandbox
            status = "500 Internal Server Error"
            body = json.dumps({"error": str(exc)}).encode("utf-8")
        finally:
            writer.write(
                (
                    f"HTTP/1.1 {status}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode()
            )
            writer.write(body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

    async def _start_mcp_bridge(
        self, servers: dict[str, dict[str, Any]]
    ) -> tuple[asyncio.AbstractServer, int]:
        server = await asyncio.start_server(
            lambda r, w: self._handle_mcp_request(r, w, servers),
            host="127.0.0.1",
            port=0,
        )

        sock = server.sockets[0]
        assert sock is not None
        port = sock.getsockname()[1]
        return server, port

    @staticmethod
    def _build_mcp_prelude(servers: dict[str, dict[str, Any]], port: int) -> str:
        config_literal = json.dumps(servers)
        return (
            "import json\n"
            "import builtins\n"
            "from js import fetch\n"
            f"__MCP_SERVER_CONFIG = json.loads({config_literal!r})\n"
            "async def __MCP_CALL__(server, tool, args):\n"
            "    if server not in __MCP_SERVER_CONFIG:\n"
            "        raise RuntimeError(f\"MCP server '{server}' is not registered for this thread.\")\n"
            "    try:\n"
            "        from pyodide.ffi import to_py as _mcp_to_py  # type: ignore\n"
            "        args = _mcp_to_py(args)\n"
            "    except (ImportError, AttributeError, TypeError):\n"
            "        pass\n"
            "    if not isinstance(args, dict):\n"
            "        try:\n"
            "            args = dict(args)\n"
            "        except (TypeError, ValueError):\n"
            "            pass\n"
            "    if not isinstance(args, dict):\n"
            "        try:\n"
            "            import js\n"
            "            args = json.loads(js.JSON.stringify(args))\n"
            "        except Exception:\n"
            "            pass\n"
            "    try:\n"
            "        import js\n"
            "        payload_dict = json.loads(js.JSON.stringify({'server': server, 'tool': tool, 'args': args}))\n"
            "    except Exception:\n"
            "        payload_dict = {'server': server, 'tool': tool, 'args': args}\n"
            "    response = await fetch(\n"
            f"        'http://127.0.0.1:{port}/call',\n"
            "        {\n"
            "            'method': 'POST',\n"
            "            'headers': [['Content-Type', 'application/json']],\n"
            "            'body': json.dumps(payload_dict),\n"
            "        },\n"
            "    )\n"
            "    if not getattr(response, 'ok', False):\n"
            "        text = await response.text()\n"
            "        raise RuntimeError(f\"MCP call failed ({getattr(response, 'status', 'unknown')}): {text}\")\n"
            "    data = await response.json()\n"
            "    if 'error' in data:\n"
            "        raise RuntimeError(data['error'])\n"
            "    return data.get('result')\n"
            "builtins.__MCP_CALL__ = __MCP_CALL__\n"
        )

    @staticmethod
    def _build_site_prelude() -> str:
        return (
            "import sys\n"
            "import importlib\n"
            "site_path = '/site-packages'\n"
            "if site_path not in sys.path:\n"
            "    sys.path.append(site_path)\n"
            "importlib.invalidate_caches()\n"
        )

    async def _bootstrap_site_packages(self) -> None:
        """Ensure mayflower MCP shim and sitecustomize are present in VFS."""
        await write_bootstrap_files(self.vfs, thread_id=self.thread_id)

    async def _execute_with_pool(
        self,
        code: str,
        session_bytes: bytes | None,
        session_metadata: dict | None,
    ) -> ExecutionResult:
        """Execute code using the worker pool (fast path)."""
        import hashlib
        import time

        start_time = time.time()
        code_hash = hashlib.sha256(code.encode()).hexdigest()

        logger.info(
            "Code execution started (pool)",
            extra={
                "thread_id": self.thread_id,
                "code_hash": code_hash,
                "code_size": len(code),
                "allow_net": self.allow_net,
                "timeout": self.timeout_seconds,
                "stateful": self.stateful,
            },
        )

        try:
            # Check resource quotas
            within_limits, quota_error = await self._check_resource_quotas()
            if not within_limits:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=quota_error or "Resource quota exceeded",
                    execution_time=time.time() - start_time,
                )

            # Start MCP bridge if needed (before pool, so port is available)
            bridge_port = await self._ensure_mcp_bridge(self.db_pool, self.thread_id)

            # Ensure pool is started with MCP bridge port
            await self._ensure_pool(mcp_bridge_port=bridge_port)

            if self._pool is None:
                raise RuntimeError("Worker pool not available")

            # Pre-load helpers and bootstrap
            await self._preload_helpers()
            await self._bootstrap_site_packages()

            # Build code with preludes
            prelude_parts = [self._build_site_prelude()]

            # Add MCP bridge prelude if bridge is running
            if bridge_port:
                servers = await self._get_mcp_server_configs()
                if servers:
                    prelude_parts.append(self._build_mcp_prelude(servers, bridge_port))

            combined_prelude = "\n".join(prelude_parts)
            code_to_run = combined_prelude + ("\n" if not code.startswith("\n") else "") + code

            # Get VFS files (already dict[str, bytes])
            files_dict = await self.vfs.get_all_files_for_pyodide()

            # Execute via pool
            result = await self._pool.execute(
                code=code_to_run,
                thread_id=self.thread_id,
                stateful=self.stateful,
                session_bytes=session_bytes,
                session_metadata=session_metadata,
                files=files_dict,
                timeout_ms=int(self.timeout_seconds * 1000),
            )

            # Save created files to VFS
            created_files = []
            if result.get("created_files") and result.get("success"):
                # Save files to PostgreSQL VFS
                for file_info in result["created_files"]:
                    file_path = file_info["path"]
                    file_content = bytes(file_info["content"])
                    await self.vfs.write_file(file_path, file_content)
                    created_files.append(file_path)
                logger.debug(f"Created {len(created_files)} files via pool")

            execution_time = time.time() - start_time
            logger.info(
                "Code execution completed (pool)",
                extra={
                    "thread_id": self.thread_id,
                    "code_hash": code_hash,
                    "success": result.get("success", False),
                    "execution_time": execution_time,
                    "created_files": created_files,
                },
            )

            return ExecutionResult(
                success=result.get("success", False),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                result=result.get("result"),
                execution_time=execution_time,
                created_files=created_files if created_files else None,
                session_bytes=bytes(result["session_bytes"])
                if result.get("session_bytes")
                else None,
                session_metadata=result.get("session_metadata"),
            )

        except Exception as e:
            logger.error(f"Pool execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Pool execution error: {e}",
                execution_time=time.time() - start_time,
            )

    async def execute(
        self,
        code: str,
        *,
        session_bytes: bytes | None = None,
        session_metadata: dict | None = None,
    ) -> ExecutionResult:
        """
        Execute Python code with automatic VFS integration.

        Args:
            code: Python code to execute
            session_bytes: Optional session state (for stateful execution)
            session_metadata: Optional session metadata

        Returns:
            ExecutionResult with output and created files
        """
        # Route to pool or legacy execution based on feature flag
        if self._use_pool:
            return await self._execute_with_pool(code, session_bytes, session_metadata)

        # Legacy execution (one-shot process)
        import hashlib
        import time

        start_time = time.time()

        # Provenance logging: Hash content for audit trail
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        logger.info(
            "Code execution started",
            extra={
                "thread_id": self.thread_id,
                "code_hash": code_hash,
                "code_size": len(code),
                "allow_net": self.allow_net,
                "timeout": self.timeout_seconds,
                "stateful": self.stateful,
            },
        )

        try:
            stdout_bytes = b""
            stderr_bytes = b""
            before_vfs_files: set[str] = set()

            # Check resource quotas before execution
            within_limits, quota_error = await self._check_resource_quotas()
            if not within_limits:
                logger.warning(
                    "Resource quota exceeded",
                    extra={
                        "thread_id": self.thread_id,
                        "code_hash": code_hash,
                        "error": quota_error,
                    },
                )
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=quota_error or "Resource quota exceeded",
                    execution_time=time.time() - start_time,
                )
            bridge_server: asyncio.AbstractServer | None = None
            bridge_port: int | None = None
            bridge_prelude = ""

            try:
                # Step 0: Pre-load helper modules into VFS (once per executor instance)
                await self._preload_helpers()
                await self._bootstrap_site_packages()

                servers = await self._get_mcp_server_configs()
                if servers:
                    bridge_server, bridge_port = await self._start_mcp_bridge(servers)
                    bridge_prelude = self._build_mcp_prelude(servers, bridge_port)

                # Step 1: Pre-load files from PostgreSQL VFS
                vfs_files = await self.vfs.get_all_files_for_pyodide()
                logger.debug(
                    f"Pre-loaded {len(vfs_files)} files from VFS for thread {self.thread_id}"
                )

                # Track existing VFS files for fallback detection (compiled libraries issue)
                before_vfs_files = {f["file_path"] for f in await self.vfs.list_files()}

                # Step 2: Build command and prepare stdin
                prelude_parts = [self._build_site_prelude()]
                if bridge_prelude:
                    prelude_parts.append(bridge_prelude)
                combined_prelude = "\n".join(prelude_parts)
                code_to_run = combined_prelude + ("\n" if not code.startswith("\n") else "") + code
                cmd = self._build_command(
                    code_to_run,
                    session_bytes,
                    session_metadata,
                    mcp_bridge_port=bridge_port,
                )
                stdin_data = self._prepare_stdin(vfs_files)

                # Step 3: Execute in Pyodide
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if stdin_data else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(input=stdin_data),
                        timeout=self.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return ExecutionResult(
                        success=False,
                        stdout="",
                        stderr=f"Execution timed out after {self.timeout_seconds} seconds",
                        execution_time=time.time() - start_time,
                    )
            finally:
                if bridge_server is not None:
                    bridge_server.close()
                    await bridge_server.wait_closed()

            # Step 4: Parse result
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")

            try:
                # Handle case where stdout has extra output before JSON (e.g., "Loading micropip")
                # Find the first '{' which marks the start of the JSON output
                json_start = stdout_text.find("{")
                if json_start > 0:
                    # Strip everything before the JSON
                    stdout_text = stdout_text[json_start:]

                result_data = json.loads(stdout_text)

                # Extract session bytes if present
                result_session_bytes = None
                if result_data.get("sessionBytes"):
                    result_session_bytes = bytes(result_data["sessionBytes"])

                # Step 5: Post-save created files to PostgreSQL VFS
                # Only save files if execution succeeded to avoid tracking incomplete/corrupted files
                created_files = []
                if result_data.get("files") and result_data.get("success", False):
                    for file_info in result_data["files"]:
                        file_path = file_info["path"]
                        file_content = bytes(file_info["content"])

                        # Save to PostgreSQL
                        await self.vfs.write_file(file_path, file_content)
                        created_files.append(file_path)

                    logger.debug(
                        f"Post-saved {len(created_files)} files to VFS for thread {self.thread_id}"
                    )

                # Step 5b: VFS Fallback - detect files missed by TypeScript snapshot
                # This handles files created by compiled libraries (openpyxl, xlsxwriter)
                # that may not be immediately visible to Pyodide's FS snapshot mechanism
                logger.info(
                    f"VFS fallback check: created_files={created_files}, success={result_data.get('success', False)}"
                )
                if not result_data.get("success", False):
                    logger.error(f"Execution failed - stderr: {result_data.get('stderr', '')}")
                    logger.info(f"Execution stdout: {result_data.get('stdout', '')}")
                if not created_files and result_data.get("success", False):
                    after_vfs_files = {f["file_path"] for f in await self.vfs.list_files()}
                    logger.info(f"VFS fallback: before={before_vfs_files}, after={after_vfs_files}")
                    vfs_created = list(after_vfs_files - before_vfs_files)

                    if vfs_created:
                        created_files = vfs_created
                        logger.info(
                            f"VFS fallback detected {len(vfs_created)} files from compiled libraries "
                            f"for thread {self.thread_id}: {vfs_created}"
                        )
                    else:
                        logger.info("VFS fallback: No new files detected")

                # Provenance logging: Record execution completion
                execution_time = time.time() - start_time
                logger.info(
                    "Code execution completed",
                    extra={
                        "thread_id": self.thread_id,
                        "code_hash": code_hash,
                        "success": result_data.get("success", False),
                        "execution_time": execution_time,
                        "created_files": created_files,
                        "num_created_files": len(created_files) if created_files else 0,
                        "stdout_size": len(result_data.get("stdout", "")),
                        "stderr_size": len(result_data.get("stderr", "")),
                    },
                )

                return ExecutionResult(
                    success=result_data.get("success", False),
                    stdout=result_data.get("stdout", ""),
                    stderr=result_data.get("stderr", ""),
                    result=result_data.get("result"),
                    execution_time=execution_time,
                    created_files=created_files if created_files else None,
                    session_bytes=result_session_bytes,
                    session_metadata=result_data.get("sessionMetadata"),
                )

            except json.JSONDecodeError as e:
                return ExecutionResult(
                    success=False,
                    stdout=stdout_text,
                    stderr=f"Failed to parse executor output: {e}\n{stderr_text}",
                    execution_time=time.time() - start_time,
                )

        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Execution failed: {e}",
                execution_time=time.time() - start_time,
            )

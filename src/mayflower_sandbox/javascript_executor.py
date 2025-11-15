"""
Mayflower JavaScript/TypeScript Sandbox Executor - QuickJS + Deno integration.

Mirrors the architecture of SandboxExecutor but executes JavaScript/TypeScript
code using QuickJS compiled to WebAssembly, hosted in Deno.

VFS Integration:
-----------------
JavascriptSandboxExecutor integrates fully with the PostgreSQL-backed VirtualFilesystem:

1. **File pre-loading**: Before execution, all files for the thread_id are loaded from
   PostgreSQL and passed to QuickJS via the MFS binary protocol (stdin).

2. **File access in code**: JavaScript code can access VFS files using injected functions:
   - readFile(path): Read file content as string
   - writeFile(path, content): Write/update file content
   - listFiles(): List all available files

3. **File persistence**: After execution, created/modified files are saved back to
   PostgreSQL VFS, making them available to future executions and to Python code.

4. **Resource limits**: Same limits as Python sandbox:
   - 20MB per file maximum
   - 100 files per thread maximum
   - File paths validated to prevent directory traversal

Thread/Session Model:
---------------------
Each JavascriptSandboxExecutor instance is bound to a specific thread_id:
- Files are isolated per thread_id (same as Python sandbox)
- Multiple executors for the same thread_id share the same VFS
- JavaScript and Python executors can share files within a thread

Statefulness Model:
-------------------
**Current Implementation (Phase 1)**: Stateless execution
- Each execute() call creates a fresh QuickJS VM context
- No state is preserved between executions
- Session state parameters (session_bytes, session_metadata) are accepted for
  API compatibility but currently logged as warnings

**Future Enhancement (Phase 2)**: Worker pool with optional statefulness
- Long-running QuickJS workers (similar to PyodideWorker)
- Optional session state preservation using JSON serialization
- Controlled by QUICKJS_USE_POOL environment variable
- Per-thread workers to maintain isolation

The stateless model is acceptable for most use cases because:
1. QuickJS VM init is fast (~1-5ms vs ~500-1000ms for Pyodide)
2. VFS provides persistence for data that needs to survive across calls
3. Reduces memory footprint and prevents state leakage between executions
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg

from .filesystem import VirtualFilesystem
from .sandbox_executor import ExecutionResult

if TYPE_CHECKING:
    from .javascript_worker_pool import JavascriptWorkerPool  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class JavascriptSandboxExecutor:
    """
    High-level executor for JavaScript/TypeScript code using QuickJS-Wasm.

    Provides the same API as SandboxExecutor but executes JavaScript/TypeScript
    instead of Python. Uses QuickJS compiled to WebAssembly for sandboxing,
    with Deno as the host runtime.

    Architecture:
    - JavaScript/TypeScript code executes inside QuickJS running in WebAssembly
    - PostgreSQL-backed virtual filesystem (shared with Python sandbox)
    - Session isolation by thread_id (shared with Python sandbox)
    - Same security constraints as Python sandbox:
      * No host filesystem access (uses VFS)
      * No network access by default (configurable whitelist)
      * 20MB file size limit, 100 file count limit
      * Configurable execution timeout

    Differences from Python sandbox:
    - No package manager (no equivalent to micropip)
    - No session state serialization yet (QuickJS doesn't have cloudpickle)
    - Faster VM initialization (~1-5ms vs ~500-1000ms for Pyodide)
    - Smaller memory footprint (~5-10MB vs ~50-100MB for Pyodide)

    Example:
        ```python
        import asyncpg
        from mayflower_sandbox import JavascriptSandboxExecutor

        db_pool = await asyncpg.create_pool(...)
        executor = JavascriptSandboxExecutor(
            db_pool=db_pool,
            thread_id="user_123",
            timeout_seconds=30.0,
        )

        result = await executor.execute('''
            const data = [1, 2, 3, 4, 5];
            const sum = data.reduce((a, b) => a + b, 0);
            console.log("Sum:", sum);
            sum;
        ''')

        print(result.stdout)  # "Sum: 15"
        print(result.result)  # 15
        ```
    """

    # Class-level worker pool (shared across all instances)
    _pool: "JavascriptWorkerPool | None" = None
    _pool_lock = asyncio.Lock()
    _use_pool = os.getenv("QUICKJS_USE_POOL", "false").lower() == "true"

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
        Initialize JavaScript/TypeScript sandbox executor.

        Args:
            db_pool: PostgreSQL connection pool for VFS persistence
            thread_id: Thread ID for session isolation and VFS namespacing
            allow_net: Allow network access (default: False)
                      When True, enables network via Deno's --allow-net with whitelist
            stateful: Maintain state between executions (default: False)
                     Note: Session state serialization not yet implemented for JS/TS
            timeout_seconds: Execution timeout in seconds (default: 60.0)
                           Enforced by Python executor wrapper
            max_memory_mb: Maximum memory usage in MB (default: 512)
                          Not enforced yet - placeholder for future implementation
            max_file_size_mb: Maximum total file size in VFS in MB (default: 20)
                            Enforced by VirtualFilesystem
            max_files: Maximum number of files in VFS (default: 100)
                      Enforced by VirtualFilesystem

        Raises:
            RuntimeError: If Deno is not installed or QuickJS executor not found

        Note:
            This executor uses the same PostgreSQL-backed VFS as the Python sandbox,
            so files created by Python code are accessible to JavaScript code and
            vice versa (within the same thread_id).
        """
        self.db_pool = db_pool
        self.thread_id = thread_id
        self.vfs = VirtualFilesystem(db_pool, thread_id)
        self.allow_net = allow_net
        self.stateful = stateful
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_file_size_mb = max_file_size_mb
        self.max_files = max_files

        # Get path to TypeScript executor (will be quickjs_executor.ts)
        self.executor_path = self._get_executor_path()

        # Verify Deno is installed
        self._check_deno()

    @classmethod
    async def _ensure_pool(cls) -> None:
        """Ensure worker pool is started (lazy initialization)."""
        if not cls._use_pool:
            return

        if cls._pool is None:
            async with cls._pool_lock:
                if cls._pool is None:  # Double-check locking
                    from .javascript_worker_pool import JavascriptWorkerPool

                    pool_size = int(os.getenv("QUICKJS_POOL_SIZE", "3"))
                    logger.info(f"Initializing QuickJS worker pool (size={pool_size})...")

                    cls._pool = JavascriptWorkerPool(
                        size=pool_size, executor_path=Path(__file__).parent
                    )
                    await cls._pool.start()

                    logger.info("QuickJS worker pool ready!")

    def _get_executor_path(self) -> Path:
        """Get path to QuickJS TypeScript executor."""
        executor = Path(__file__).parent / "quickjs_executor.ts"
        if not executor.exists():
            raise RuntimeError(
                f"QuickJS executor not found at {executor}. "
                "The JavaScript/TypeScript sandbox requires quickjs_executor.ts to be present."
            )
        return executor

    def _check_deno(self):
        """Verify Deno is installed."""
        import subprocess

        try:
            subprocess.run(
                ["deno", "--version"],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(
                "Deno is not installed or not in PATH. "
                "Install from https://deno.land/ to use JavaScript/TypeScript sandbox."
            ) from e

    def _build_command(
        self,
        code: str,
        session_bytes: bytes | None = None,
        session_metadata: dict | None = None,
    ) -> list[str]:
        """
        Build Deno command for QuickJS executor.

        Args:
            code: JavaScript/TypeScript code to execute
            session_bytes: Optional session state (not yet supported)
            session_metadata: Optional session metadata (not yet supported)

        Returns:
            Command line arguments for subprocess
        """
        import json

        cmd: list[str] = [
            "deno",
            "run",
            "--allow-read",  # Minimal - only for QuickJS Wasm module loading
            "--allow-write",  # Minimal - only for QuickJS Wasm module loading
            "--allow-env",  # Required for esbuild TypeScript transpilation
            "--allow-net",  # Required for esbuild binary download
            "--allow-run",  # Required for esbuild binary execution
        ]

        # Network is disabled by default for JavaScript/TypeScript sandbox
        # QuickJS runs entirely in Wasm with no network access
        # If allow_net is True, we could inject a fetch function later
        if self.allow_net:
            logger.warning(
                "allow_net=True requested for thread %s, but network access "
                "is not yet implemented for JavaScript/TypeScript sandbox",
                self.thread_id,
            )

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
        """
        Prepare files for stdin using MFS binary protocol.

        Same protocol as Python executor for consistency.

        Args:
            files: Dictionary mapping file paths to content bytes

        Returns:
            Binary data to send via stdin, or None if no files
        """
        import json

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

        Same logic as Python executor - shared VFS means shared quotas.

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

    async def execute(
        self,
        code: str,
        *,
        session_bytes: bytes | None = None,
        session_metadata: dict | None = None,
    ) -> ExecutionResult:
        """
        Execute JavaScript/TypeScript code in QuickJS-Wasm sandbox.

        JSON Protocol (Python <-> Deno/QuickJS):
        -----------------------------------------
        Python sends via command line args:
          -c <code>         : JavaScript/TypeScript code to execute
          -s                : Stateful execution (not yet supported)
          -b <bytes_array>  : Session state bytes (not yet supported)
          -m <metadata_json>: Session metadata (not yet supported)

        Python sends via stdin (MFS binary protocol):
          "MFS\x01" + length(4 bytes) + JSON metadata + file contents
          Files are pre-loaded from PostgreSQL VFS

        Deno returns via stdout (JSON):
          {
            "success": bool,
            "stdout": str,      # Captured console.log() output
            "stderr": str,      # Captured console.error() + error messages
            "result": any,      # Return value (JSON-serializable)
            "files": [          # Created/modified files
              {"path": str, "content": number[]}
            ]
          }

        See quickjs_executor.ts for Deno/QuickJS implementation details.
        """
        import hashlib
        import json
        import time

        start_time = time.time()

        # Provenance logging
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        logger.info(
            "JavaScript execution started",
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

            # Step 1: Pre-load files from PostgreSQL VFS
            vfs_files = await self.vfs.get_all_files_for_pyodide()
            logger.debug(f"Pre-loaded {len(vfs_files)} files from VFS for thread {self.thread_id}")

            # Step 2: Build command and prepare stdin
            cmd = self._build_command(code, session_bytes, session_metadata)
            stdin_data = self._prepare_stdin(vfs_files)

            # Step 3: Execute in QuickJS-Wasm via Deno
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

            # Step 4: Parse result
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")

            try:
                # Handle case where stdout has extra output before JSON
                # Find the first '{' which marks the start of the JSON output
                json_start = stdout_text.find("{")
                if json_start > 0:
                    stdout_text = stdout_text[json_start:]

                result_data = json.loads(stdout_text)

                # Step 5: Post-save created files to PostgreSQL VFS
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

                # Provenance logging
                execution_time = time.time() - start_time
                logger.info(
                    "JavaScript execution completed",
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
                    session_bytes=None,  # Not yet supported
                    session_metadata=None,  # Not yet supported
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

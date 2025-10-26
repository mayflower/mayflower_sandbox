"""
Mayflower Sandbox Executor - Clean VFS + Pyodide integration.

Written from scratch with better architecture than langchain-sandbox.
Provides unified interface for executing Python code with automatic VFS sync.
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from .filesystem import VirtualFilesystem

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
    ) -> list[str]:
        """Build Deno command."""
        cmd = [
            "deno",
            "run",
            "--allow-read",
            "--allow-write",
            "--allow-net" if self.allow_net else "--allow-net=cdn.jsdelivr.net",
            str(self.executor_path),
            "-c",
            code,
        ]

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
            }
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
                    }
                )
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=quota_error or "Resource quota exceeded",
                    execution_time=time.time() - start_time,
                )
            # Step 0: Pre-load helper modules into VFS (once per executor instance)
            await self._preload_helpers()

            # Step 1: Pre-load files from PostgreSQL VFS
            vfs_files = await self.vfs.get_all_files_for_pyodide()
            logger.debug(f"Pre-loaded {len(vfs_files)} files from VFS for thread {self.thread_id}")

            # Track existing VFS files for fallback detection (compiled libraries issue)
            before_vfs_files = set(f["file_path"] for f in await self.vfs.list_files())

            # Step 2: Build command and prepare stdin
            cmd = self._build_command(code, session_bytes, session_metadata)
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
                logger.info(f"VFS fallback check: created_files={created_files}, success={result_data.get('success', False)}")
                if not result_data.get('success', False):
                    logger.error(f"Execution failed - stderr: {result_data.get('stderr', '')}")
                    logger.info(f"Execution stdout: {result_data.get('stdout', '')}")
                if not created_files and result_data.get("success", False):
                    after_vfs_files = set(f["file_path"] for f in await self.vfs.list_files())
                    logger.info(f"VFS fallback: before={before_vfs_files}, after={after_vfs_files}")
                    vfs_created = list(after_vfs_files - before_vfs_files)

                    if vfs_created:
                        created_files = vfs_created
                        logger.info(
                            f"VFS fallback detected {len(vfs_created)} files from compiled libraries "
                            f"for thread {self.thread_id}: {vfs_created}"
                        )
                    else:
                        logger.info(f"VFS fallback: No new files detected")

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
                    }
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

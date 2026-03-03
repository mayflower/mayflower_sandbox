"""Unit tests for WorkerPool and PyodideWorker — covers code paths without Deno."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mayflower_sandbox.worker_pool import PyodideWorker, WorkerPool

# ---------------------------------------------------------------------------
# PyodideWorker._split_at_newline
# ---------------------------------------------------------------------------


class TestSplitAtNewline:
    def test_splits_at_newline(self):
        w = PyodideWorker(0, Path("/fake"))
        result = w._split_at_newline(b"line1\nline2\n")
        assert result == b"line1\n"
        assert w._read_buffer == b"line2\n"

    def test_no_remainder(self):
        w = PyodideWorker(0, Path("/fake"))
        result = w._split_at_newline(b"line1\n")
        assert result == b"line1\n"
        assert w._read_buffer == b""


# ---------------------------------------------------------------------------
# PyodideWorker._read_large_line
# ---------------------------------------------------------------------------


class TestReadLargeLine:
    @pytest.mark.asyncio
    async def test_reads_from_buffer(self):
        w = PyodideWorker(0, Path("/fake"))
        w._read_buffer = b'{"ok": true}\nextra'
        reader = AsyncMock()
        result = await w._read_large_line(reader)
        assert result == b'{"ok": true}\n'
        assert w._read_buffer == b"extra"
        reader.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_reads_from_stream(self):
        w = PyodideWorker(0, Path("/fake"))
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[b'{"result": 1}\n', b""])
        result = await w._read_large_line(reader)
        assert result == b'{"result": 1}\n'

    @pytest.mark.asyncio
    async def test_exceeds_size_limit(self):
        w = PyodideWorker(0, Path("/fake"))
        reader = AsyncMock()
        # Return chunks without newline to trigger size limit
        big_chunk = b"x" * (5 * 1024 * 1024)
        reader.read = AsyncMock(side_effect=[big_chunk, big_chunk, big_chunk])
        with pytest.raises(RuntimeError, match="10MB"):
            await w._read_large_line(reader)

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        w = PyodideWorker(0, Path("/fake"))
        reader = AsyncMock()
        reader.read = AsyncMock(return_value=b"")
        result = await w._read_large_line(reader)
        assert result == b""

    @pytest.mark.asyncio
    async def test_multi_chunk_before_newline(self):
        w = PyodideWorker(0, Path("/fake"))
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[b"part1", b"part2\nrest"])
        result = await w._read_large_line(reader)
        assert result == b"part1part2\n"
        assert w._read_buffer == b"rest"


# ---------------------------------------------------------------------------
# PyodideWorker.execute
# ---------------------------------------------------------------------------


class TestWorkerExecute:
    @pytest.mark.asyncio
    async def test_not_running_raises(self):
        w = PyodideWorker(0, Path("/fake"))
        w.process = None
        with pytest.raises(RuntimeError, match="not running"):
            await w.execute("print(1)", "thread-1")

    @pytest.mark.asyncio
    async def test_dead_process_raises(self):
        w = PyodideWorker(0, Path("/fake"))
        w.process = MagicMock()
        w.process.returncode = 1
        with pytest.raises(RuntimeError, match="not running"):
            await w.execute("print(1)", "thread-1")

    @pytest.mark.asyncio
    async def test_event_loop_change_raises(self):
        w = PyodideWorker(0, Path("/fake"))
        w.process = MagicMock()
        w.process.returncode = None
        w._loop = MagicMock()  # Different loop
        with pytest.raises(RuntimeError, match="different event loop"):
            await w.execute("print(1)", "thread-1")

    @pytest.mark.asyncio
    async def test_successful_execute(self):
        w = PyodideWorker(0, Path("/fake"))
        w._loop = asyncio.get_running_loop()
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = MagicMock()
        w.process = mock_proc

        expected = {"success": True, "stdout": "hello\n"}
        response = {"jsonrpc": "2.0", "id": 1, "result": expected}

        with patch.object(w, "_read_large_line", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = json.dumps(response).encode() + b"\n"
            result = await w.execute("print('hello')", "t1")

        assert result == expected
        assert w.request_count == 1
        assert w.busy is False

    @pytest.mark.asyncio
    async def test_worker_error_response(self):
        w = PyodideWorker(0, Path("/fake"))
        w._loop = asyncio.get_running_loop()
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = MagicMock()
        w.process = mock_proc

        response = {"jsonrpc": "2.0", "id": 1, "error": {"message": "boom"}}

        with patch.object(w, "_read_large_line", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = json.dumps(response).encode() + b"\n"
            with pytest.raises(RuntimeError, match="boom"):
                await w.execute("bad code", "t1")


# ---------------------------------------------------------------------------
# PyodideWorker.health_check
# ---------------------------------------------------------------------------


class TestWorkerHealthCheck:
    @pytest.mark.asyncio
    async def test_dead_process(self):
        w = PyodideWorker(0, Path("/fake"))
        w.process = None
        result = await w.health_check()
        assert result["status"] == "dead"

    @pytest.mark.asyncio
    async def test_healthy_response(self):
        w = PyodideWorker(0, Path("/fake"))
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(
            return_value=json.dumps({"result": {"status": "healthy"}}).encode() + b"\n"
        )
        w.process = mock_proc

        result = await w.health_check()
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_timeout_response(self):
        w = PyodideWorker(0, Path("/fake"))
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        w.process = mock_proc

        result = await w.health_check()
        assert result["status"] == "timeout"


# ---------------------------------------------------------------------------
# PyodideWorker.shutdown / kill
# ---------------------------------------------------------------------------


class TestWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_shutdown_already_stopped(self):
        w = PyodideWorker(0, Path("/fake"))
        w.process = None
        await w.shutdown()  # should not raise

    @pytest.mark.asyncio
    async def test_kill_process_lookup_error(self):
        w = PyodideWorker(0, Path("/fake"))
        mock_proc = MagicMock()
        mock_proc.kill = MagicMock(side_effect=ProcessLookupError)
        mock_proc.wait = AsyncMock()
        w.process = mock_proc

        await w.kill()
        assert w.process is None

    @pytest.mark.asyncio
    async def test_shutdown_sends_command(self):
        w = PyodideWorker(0, Path("/fake"))
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.wait = AsyncMock()
        w.process = mock_proc

        await w.shutdown()
        # Verify shutdown JSON-RPC was sent
        call_args = mock_proc.stdin.write.call_args[0][0]
        msg = json.loads(call_args.decode())
        assert msg["method"] == "shutdown"


# ---------------------------------------------------------------------------
# PyodideWorker constructor
# ---------------------------------------------------------------------------


class TestWorkerInit:
    def test_default_allowed_hosts(self):
        w = PyodideWorker(0, Path("/fake"))
        assert "pypi.org" in w.allowed_hosts
        assert "cdn.jsdelivr.net" in w.allowed_hosts

    def test_custom_allowed_hosts(self):
        w = PyodideWorker(0, Path("/fake"), allowed_hosts={"example.com"})
        assert w.allowed_hosts == {"example.com"}


# ---------------------------------------------------------------------------
# WorkerPool
# ---------------------------------------------------------------------------


class TestWorkerPool:
    @pytest.mark.asyncio
    async def test_execute_not_started_raises(self):
        pool = WorkerPool(size=1)
        with pytest.raises(RuntimeError, match="not started"):
            await pool.execute("x", "t1")

    @pytest.mark.asyncio
    async def test_shutdown_cancels_health_task(self):
        pool = WorkerPool(size=1)
        pool.started = True

        # Create a real task that we can cancel
        async def _noop():
            await asyncio.sleep(999)

        pool._health_task = asyncio.create_task(_noop())
        pool.workers = []

        await pool.shutdown()
        assert pool._health_task.cancelled()
        assert pool.started is False

    @pytest.mark.asyncio
    async def test_start_already_started(self):
        pool = WorkerPool(size=1)
        pool.started = True
        await pool.start()  # should be no-op

    @pytest.mark.asyncio
    async def test_mcp_bridge_port_in_allowed_hosts(self):
        pool = WorkerPool(size=1, mcp_bridge_port=5555)
        pool.started = False

        with patch.object(PyodideWorker, "start", new_callable=AsyncMock):
            await pool.start()

        assert pool._allowed_hosts is not None
        assert "127.0.0.1:5555" in pool._allowed_hosts
        await pool.shutdown()

    @pytest.mark.asyncio
    async def test_restart_worker_no_loop(self):
        pool = WorkerPool(size=1)
        worker = PyodideWorker(0, Path("/fake"))

        with patch("asyncio.get_running_loop", side_effect=RuntimeError):
            await pool._restart_worker(worker)
        # Should not raise, just log warning

    @pytest.mark.asyncio
    async def test_health_check_all(self):
        pool = WorkerPool(size=2)
        w1 = PyodideWorker(0, Path("/fake"))
        w2 = PyodideWorker(1, Path("/fake"))
        with (
            patch.object(
                w1, "health_check", new_callable=AsyncMock, return_value={"status": "healthy"}
            ),
            patch.object(
                w2, "health_check", new_callable=AsyncMock, return_value={"status": "healthy"}
            ),
        ):
            pool.workers = [w1, w2]

            results = await pool.health_check_all()
            assert len(results) == 2
            assert all(r["status"] == "healthy" for r in results)

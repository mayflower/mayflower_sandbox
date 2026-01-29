import os
from pathlib import Path

import asyncpg
import pytest
from conftest import requires_deno

from mayflower_sandbox.sandbox_executor import SandboxExecutor


@pytest.fixture
async def db_pool():
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
    }

    pool = await asyncpg.create_pool(**db_config)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_shell', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
            """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_shell'")
    yield
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_shell'")


@requires_deno
async def test_shell_executes_and_persists_file(db_pool, clean_files, monkeypatch):
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    result = await executor.execute_shell("echo hello > /tmp/hello.txt && cat /tmp/hello.txt")

    assert result.success is True
    assert "hello" in result.stdout
    assert result.exit_code == 0
    assert result.created_files is not None
    assert "/tmp/hello.txt" in result.created_files

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_shell' AND file_path = '/tmp/hello.txt'
            """
        )
        assert row is not None
        assert row["content"] == b"hello\n"

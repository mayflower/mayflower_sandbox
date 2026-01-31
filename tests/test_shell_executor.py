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


@requires_deno
async def test_shell_command_failure(db_pool, clean_files, monkeypatch):
    """Test that command failures return proper exit codes."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    # Try to cat a non-existent file
    result = await executor.execute_shell("cat /nonexistent/file.txt")

    assert result.success is False
    assert result.exit_code != 0


@requires_deno
async def test_shell_command_chaining_and_operator(db_pool, clean_files, monkeypatch):
    """Test && operator stops on first failure."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    # First command fails, second should not run
    result = await executor.execute_shell("cat /nonexistent && echo success")

    assert result.success is False
    assert "success" not in result.stdout


@requires_deno
async def test_shell_command_chaining_semicolon(db_pool, clean_files, monkeypatch):
    """Test ; operator continues regardless of previous failure."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    # First command fails, but second should still run due to ;
    result = await executor.execute_shell("cat /nonexistent 2>/dev/null; echo continued")

    # Should contain output from second command
    assert "continued" in result.stdout


@requires_deno
async def test_shell_file_append_redirection(db_pool, clean_files, monkeypatch):
    """Test >> append redirection creates and appends to files."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    result = await executor.execute_shell(
        "echo first >> /tmp/append.txt && echo second >> /tmp/append.txt && cat /tmp/append.txt"
    )

    assert result.success is True
    assert "first" in result.stdout
    assert "second" in result.stdout


@requires_deno
async def test_shell_vfs_file_round_trip(db_pool, clean_files, monkeypatch):
    """Test VFS file is persisted to database and can be read back."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)

    # Create a file in first execution
    result1 = await executor.execute_shell("echo 'persistent data' > /tmp/persist.txt")
    assert result1.success is True
    assert "/tmp/persist.txt" in (result1.created_files or {})

    # Read it back in second execution (stateful mode)
    result2 = await executor.execute_shell("cat /tmp/persist.txt")
    assert result2.success is True
    assert "persistent data" in result2.stdout


@requires_deno
async def test_shell_multiple_file_creation(db_pool, clean_files, monkeypatch):
    """Test creating multiple files in a single execution."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    result = await executor.execute_shell(
        "echo a > /tmp/a.txt && echo b > /tmp/b.txt && echo c > /tmp/c.txt"
    )

    assert result.success is True
    created = result.created_files or []
    assert "/tmp/a.txt" in created
    assert "/tmp/b.txt" in created
    assert "/tmp/c.txt" in created


@requires_deno
async def test_shell_nested_directory_creation(db_pool, clean_files, monkeypatch):
    """Test writing to nested directories creates parent dirs."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    result = await executor.execute_shell(
        "mkdir -p /tmp/deep/nested/dir && echo nested > /tmp/deep/nested/dir/file.txt && cat /tmp/deep/nested/dir/file.txt"
    )

    assert result.success is True
    assert "nested" in result.stdout


@requires_deno
async def test_shell_pipe_simple(db_pool, clean_files, monkeypatch):
    """Test simple pipe: echo | cat."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    result = await executor.execute_shell("echo hello | cat")

    assert result.success is True
    assert "hello" in result.stdout
    assert result.exit_code == 0


@requires_deno
async def test_shell_pipe_multi_stage(db_pool, clean_files, monkeypatch):
    """Test multi-stage pipe: echo | cat | cat."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    result = await executor.execute_shell("echo hello world | cat | cat")

    assert result.success is True
    assert "hello world" in result.stdout
    assert result.exit_code == 0


@requires_deno
async def test_shell_pipe_grep(db_pool, clean_files, monkeypatch):
    """Test pipe with grep filtering."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    # Create file first, then pipe through grep
    await executor.execute_shell(
        "echo apple > /tmp/grep_test.txt && echo banana >> /tmp/grep_test.txt && echo apricot >> /tmp/grep_test.txt"
    )
    result = await executor.execute_shell("cat /tmp/grep_test.txt | grep '^a'")

    assert result.success is True
    assert "apple" in result.stdout
    assert "apricot" in result.stdout
    assert "banana" not in result.stdout


@requires_deno
async def test_shell_pipe_wc(db_pool, clean_files, monkeypatch):
    """Test pipe with wc (word count)."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    # Create file with 3 lines, then count
    await executor.execute_shell(
        "echo one > /tmp/wc_test.txt && echo two >> /tmp/wc_test.txt && echo three >> /tmp/wc_test.txt"
    )
    result = await executor.execute_shell("cat /tmp/wc_test.txt | wc -l")

    assert result.success is True
    # wc -l should output 3 (three lines)
    assert "3" in result.stdout


@requires_deno
async def test_shell_pipe_with_file(db_pool, clean_files, monkeypatch):
    """Test pipe reading from file: cat file | grep."""
    busybox_dir = Path(__file__).resolve().parent.parent / "src" / "mayflower_sandbox" / "busybox"
    if not (busybox_dir / "busybox.js").exists():
        pytest.skip("busybox assets not available")

    monkeypatch.setenv("MAYFLOWER_BUSYBOX_DIR", str(busybox_dir))

    executor = SandboxExecutor(db_pool, "test_shell", allow_net=False)
    # First create a file using echo
    await executor.execute_shell(
        "echo foo > /tmp/test.txt && echo bar >> /tmp/test.txt && echo baz >> /tmp/test.txt"
    )

    # Then use it in a pipe
    result = await executor.execute_shell("cat /tmp/test.txt | grep bar")

    assert result.success is True
    assert "bar" in result.stdout
    assert "foo" not in result.stdout

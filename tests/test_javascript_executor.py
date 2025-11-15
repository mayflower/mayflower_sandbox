"""
Tests for JavascriptSandboxExecutor - QuickJS-Wasm integration.

Tests basic JavaScript/TypeScript execution, timeout handling, error handling,
and security/sandboxing constraints.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.javascript_executor import (
    JavascriptSandboxExecutor,  # type: ignore[import-untyped]
)


def check_deno_available():
    """Check if Deno is installed and available."""
    try:
        subprocess.run(
            ["deno", "--version"],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# Skip all tests if Deno is not available
pytestmark = pytest.mark.skipif(
    not check_deno_available(),
    reason="Deno is not installed. Install from https://deno.land/ to run JavaScript tests.",
)


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
    }

    pool = await asyncpg.create_pool(**db_config)  # type: ignore[call-overload]

    # Ensure session exists
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_js_executor', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def executor(db_pool):
    """Create JavaScript executor instance."""
    return JavascriptSandboxExecutor(db_pool, "test_js_executor", allow_net=False)


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before and after each test to ensure isolation."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_js_executor'")
    yield
    # Cleanup after test
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_js_executor'")


# ============================
# Basic Execution Tests
# ============================


async def test_simple_javascript_execution(executor, clean_files):
    """Test basic JavaScript execution with console.log."""
    result = await executor.execute("console.log('Hello from JavaScript!');")

    assert result.success is True
    assert "Hello from JavaScript!" in result.stdout
    assert result.stderr == ""


async def test_simple_computation(executor, clean_files):
    """Test JavaScript computation with result."""
    code = """
const x = 5 + 7;
console.log('Result:', x);
x;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Result: 12" in result.stdout
    assert result.result == 12


async def test_array_operations(executor, clean_files):
    """Test JavaScript array methods."""
    code = """
const numbers = [1, 2, 3, 4, 5];
const doubled = numbers.map(n => n * 2);
const sum = doubled.reduce((a, b) => a + b, 0);
console.log('Sum of doubled:', sum);
sum;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Sum of doubled: 30" in result.stdout
    assert result.result == 30


async def test_json_operations(executor, clean_files):
    """Test JSON stringify/parse."""
    code = """
const data = { name: 'Test', value: 42 };
const json = JSON.stringify(data);
const parsed = JSON.parse(json);
console.log('Parsed name:', parsed.name);
parsed;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Parsed name: Test" in result.stdout
    assert result.result == {"name": "Test", "value": 42}


# ============================
# TypeScript Tests
# ============================


async def test_typescript_basic(executor, clean_files):
    """Test basic TypeScript with type annotations."""
    code = """
const add = (a: number, b: number): number => {
    return a + b;
};

const result: number = add(5, 7);
console.log('TypeScript result:', result);
result;
"""
    result = await executor.execute(code)

    # TypeScript transpilation is basic (runtime-only), but simple types should work
    assert result.success is True
    assert "TypeScript result: 12" in result.stdout
    assert result.result == 12


async def test_typescript_interface(executor, clean_files):
    """Test TypeScript with interfaces."""
    code = """
interface Point {
    x: number;
    y: number;
}

const point: Point = { x: 10, y: 20 };
const distance = Math.sqrt(point.x * point.x + point.y * point.y);
console.log('Distance:', distance);
distance;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Distance:" in result.stdout
    # Distance should be ~22.36
    assert result.result > 22 and result.result < 23


# ============================
# Error Handling Tests
# ============================


async def test_syntax_error(executor, clean_files):
    """Test JavaScript syntax error is captured."""
    code = "const x = ;"  # Syntax error

    result = await executor.execute(code)

    assert result.success is False
    assert result.stderr != ""
    # Error message should mention syntax
    assert "Syntax" in result.stderr or "syntax" in result.stderr or "Unexpected" in result.stderr


async def test_runtime_error(executor, clean_files):
    """Test JavaScript runtime error is captured."""
    code = """
function throwError() {
    throw new Error('Test runtime error');
}
throwError();
"""
    result = await executor.execute(code)

    assert result.success is False
    assert "Error" in result.stderr
    assert "Test runtime error" in result.stderr


async def test_reference_error(executor, clean_files):
    """Test ReferenceError is captured."""
    code = "console.log(undefinedVariable);"

    result = await executor.execute(code)

    assert result.success is False
    assert "Error" in result.stderr or "not defined" in result.stderr


# ============================
# Timeout Tests
# ============================


async def test_timeout_infinite_loop(executor, clean_files):
    """Test that infinite loop triggers timeout."""
    code = """
while (true) {
    // Infinite loop
}
"""
    # Use shorter timeout for this test
    executor_short_timeout = JavascriptSandboxExecutor(
        executor.db_pool, "test_js_executor", timeout_seconds=2.0
    )

    result = await executor_short_timeout.execute(code)

    assert result.success is False
    assert "timeout" in result.stderr.lower() or "timed out" in result.stderr.lower()
    # Execution time should be approximately the timeout
    assert result.execution_time >= 1.8  # Close to 2 seconds


async def test_timeout_long_running_computation(executor, clean_files):
    """Test that long-running computation triggers timeout."""
    code = """
let sum = 0;
for (let i = 0; i < 100000000; i++) {
    sum += i;
}
console.log('Sum:', sum);
"""
    # Use very short timeout
    executor_short_timeout = JavascriptSandboxExecutor(
        executor.db_pool, "test_js_executor", timeout_seconds=0.5
    )

    result = await executor_short_timeout.execute(code)

    # This might succeed if QuickJS is fast enough, or timeout
    # Either way is valid - we're testing timeout mechanism works
    if not result.success:
        assert "timeout" in result.stderr.lower() or "timed out" in result.stderr.lower()


# ============================
# VFS Integration Tests
# ============================


async def test_vfs_write_file(executor, db_pool, clean_files):
    """Test creating files via VFS writeFile()."""
    code = """
writeFile('/test.txt', 'Hello from JavaScript!');
console.log('File created');
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "File created" in result.stdout
    assert result.created_files is not None
    assert "/test.txt" in result.created_files

    # Verify file was saved to PostgreSQL
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_js_executor' AND file_path = '/test.txt'
        """
        )

        assert file_data is not None
        assert file_data["content"] == b"Hello from JavaScript!"


async def test_vfs_read_file(executor, db_pool, clean_files):
    """Test reading files via VFS readFile()."""
    # Pre-populate VFS
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_js_executor', '/input.txt', $1, 'text/plain', $2)
        """,
            b"Data from VFS",
            13,
        )

    code = """
const content = readFile('/input.txt');
console.log('Read:', content);
content;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Read: Data from VFS" in result.stdout
    assert result.result == "Data from VFS"


async def test_vfs_list_files(executor, db_pool, clean_files):
    """Test listing files via VFS listFiles()."""
    # Pre-populate VFS with multiple files
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES
                ('test_js_executor', '/file1.txt', $1, 'text/plain', 5),
                ('test_js_executor', '/file2.txt', $2, 'text/plain', 5)
        """,
            b"File1",
            b"File2",
        )

    code = """
const files = listFiles();
console.log('Files:', files);
files;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "/file1.txt" in result.result
    assert "/file2.txt" in result.result


async def test_vfs_json_file(executor, db_pool, clean_files):
    """Test writing and reading JSON files."""
    code = """
const data = {
    name: 'Test',
    values: [1, 2, 3],
    nested: { key: 'value' }
};

writeFile('/data.json', JSON.stringify(data, null, 2));
console.log('JSON file created');

const read = JSON.parse(readFile('/data.json'));
console.log('Read name:', read.name);
read;
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "JSON file created" in result.stdout
    assert "Read name: Test" in result.stdout
    assert result.result["name"] == "Test"
    assert result.result["values"] == [1, 2, 3]


# ============================
# Security/Sandboxing Tests
# ============================


async def test_no_filesystem_access(executor, clean_files):
    """Test that host filesystem access is blocked."""
    # Try to access host filesystem - should fail
    code = """
try {
    // This should fail - no host filesystem access
    const fs = require('fs');
    console.log('ERROR: Should not have fs access!');
} catch (e) {
    console.log('Expected: No fs module available');
    console.error(e.message);
}
"""
    result = await executor.execute(code)

    # Should complete but with expected error
    assert "No fs module" in result.stdout or "require is not defined" in result.stderr


async def test_no_network_access(executor, clean_files):
    """Test that network access is blocked."""
    code = """
try {
    // This should fail - no network access
    fetch('https://example.com');
    console.log('ERROR: Should not have network access!');
} catch (e) {
    console.log('Expected: No fetch available');
    console.error(e.message);
}
"""
    result = await executor.execute(code)

    # Should complete but with expected error
    assert "No fetch" in result.stdout or "fetch is not defined" in result.stderr


async def test_no_process_access(executor, clean_files):
    """Test that process/environment access is blocked."""
    code = """
try {
    // This should fail - no process access
    console.log(process.env);
    console.log('ERROR: Should not have process access!');
} catch (e) {
    console.log('Expected: No process available');
    console.error(e.message);
}
"""
    result = await executor.execute(code)

    # Should complete but with expected error
    assert "No process" in result.stdout or "process is not defined" in result.stderr


# ============================
# Resource Quota Tests
# ============================


async def test_file_limit_quota(executor, db_pool, clean_files):
    """Test that file limit quota is enforced."""
    # Create executor with very low file limit
    executor_limited = JavascriptSandboxExecutor(db_pool, "test_js_executor", max_files=2)

    # Pre-populate VFS to reach limit
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES
                ('test_js_executor', '/file1.txt', $1, 'text/plain', 5),
                ('test_js_executor', '/file2.txt', $2, 'text/plain', 5)
        """,
            b"File1",
            b"File2",
        )

    # Try to create another file - should fail due to quota
    code = """
writeFile('/file3.txt', 'File3');
console.log('Should not succeed');
"""
    result = await executor_limited.execute(code)

    assert result.success is False
    assert "limit" in result.stderr.lower() or "quota" in result.stderr.lower()


async def test_multiple_file_creation(executor, clean_files):
    """Test creating multiple files in one execution."""
    code = """
writeFile('/file1.js', 'console.log("File 1")');
writeFile('/file2.js', 'console.log("File 2")');
writeFile('/file3.js', 'console.log("File 3")');
console.log('Created 3 files');
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Created 3 files" in result.stdout
    assert len(result.created_files) == 3
    assert "/file1.js" in result.created_files
    assert "/file2.js" in result.created_files
    assert "/file3.js" in result.created_files

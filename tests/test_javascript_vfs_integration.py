"""
Test JavaScript/TypeScript VFS integration with PostgreSQL-backed filesystem.

Tests that JavascriptSandboxExecutor properly integrates with VirtualFilesystem:
- File pre-loading and post-saving
- Resource quotas and limits
- Path validation and security
- Cross-language file sharing (Python ↔ JavaScript)
- Thread isolation
"""

import asyncpg
import pytest

from mayflower_sandbox.filesystem import VirtualFilesystem  # type: ignore[import-untyped]
from mayflower_sandbox.javascript_executor import (  # type: ignore[import-untyped]
    JavascriptSandboxExecutor,
)


@pytest.fixture
async def db_pool():
    """Database connection pool."""
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5433,
        database="sandbox",
        user="sandbox",
        password="sandbox",
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def thread_id():
    """Unique thread ID for test isolation."""
    import uuid

    return f"test-js-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_javascript_file_creation(db_pool, thread_id):
    """Test that JavaScript can create files and they're saved to VFS."""
    executor = JavascriptSandboxExecutor(db_pool, thread_id)
    vfs = VirtualFilesystem(db_pool, thread_id)

    # Execute JavaScript that creates a file
    result = await executor.execute("""
        writeFile('/test.txt', 'Hello from JavaScript!');
        'File created';
    """)

    assert result.success
    assert result.created_files == ["/test.txt"]

    # Verify file exists in VFS
    file_exists = await vfs.file_exists("/test.txt")
    assert file_exists

    # Read file from VFS
    file_info = await vfs.read_file("/test.txt")
    content = file_info["content"].decode("utf-8")
    assert content == "Hello from JavaScript!"


@pytest.mark.asyncio
async def test_javascript_file_reading(db_pool, thread_id):
    """Test that JavaScript can read files from VFS."""
    vfs = VirtualFilesystem(db_pool, thread_id)
    executor = JavascriptSandboxExecutor(db_pool, thread_id)

    # Create file in VFS
    await vfs.write_file("/input.txt", b"Hello from VFS!")

    # Execute JavaScript that reads the file
    result = await executor.execute("""
        const content = readFile('/input.txt');
        console.log('File content:', content);
        content;
    """)

    assert result.success
    assert result.result == "Hello from VFS!"
    assert "File content: Hello from VFS!" in result.stdout


@pytest.mark.asyncio
async def test_javascript_file_modification(db_pool, thread_id):
    """Test that JavaScript can modify existing VFS files."""
    vfs = VirtualFilesystem(db_pool, thread_id)
    executor = JavascriptSandboxExecutor(db_pool, thread_id)

    # Create initial file
    await vfs.write_file("/data.txt", b"Initial content")

    # Modify file with JavaScript
    result = await executor.execute("""
        const original = readFile('/data.txt');
        const modified = original + ' - Modified by JavaScript!';
        writeFile('/data.txt', modified);
        modified;
    """)

    assert result.success
    assert result.result == "Initial content - Modified by JavaScript!"

    # Verify modification persisted
    file_info = await vfs.read_file("/data.txt")
    content = file_info["content"].decode("utf-8")
    assert content == "Initial content - Modified by JavaScript!"


@pytest.mark.asyncio
async def test_javascript_json_file(db_pool, thread_id):
    """Test JavaScript working with JSON files."""
    executor = JavascriptSandboxExecutor(db_pool, thread_id)
    vfs = VirtualFilesystem(db_pool, thread_id)

    # Create JSON file with JavaScript
    result = await executor.execute("""
        const data = {
            name: "Test",
            values: [1, 2, 3, 4, 5],
            timestamp: new Date().toISOString()
        };
        writeFile('/data.json', JSON.stringify(data, null, 2));
        data;
    """)

    assert result.success
    assert result.result["name"] == "Test"
    assert result.result["values"] == [1, 2, 3, 4, 5]

    # Read and parse JSON file
    file_info = await vfs.read_file("/data.json")
    content = file_info["content"].decode("utf-8")
    import json

    data = json.loads(content)
    assert data["name"] == "Test"
    assert data["values"] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_javascript_multiple_files(db_pool, thread_id):
    """Test JavaScript creating multiple files in one execution."""
    executor = JavascriptSandboxExecutor(db_pool, thread_id)

    result = await executor.execute("""
        writeFile('/file1.txt', 'Content 1');
        writeFile('/file2.txt', 'Content 2');
        writeFile('/file3.txt', 'Content 3');
        'Created 3 files';
    """)

    assert result.success
    assert len(result.created_files) == 3
    assert set(result.created_files) == {"/file1.txt", "/file2.txt", "/file3.txt"}


@pytest.mark.asyncio
async def test_javascript_list_files(db_pool, thread_id):
    """Test JavaScript can list VFS files."""
    vfs = VirtualFilesystem(db_pool, thread_id)
    executor = JavascriptSandboxExecutor(db_pool, thread_id)

    # Create some files in VFS
    await vfs.write_file("/file1.txt", b"Content 1")
    await vfs.write_file("/file2.txt", b"Content 2")
    await vfs.write_file("/data/file3.txt", b"Content 3")

    # List files with JavaScript
    result = await executor.execute("""
        const files = listFiles();
        console.log('Files:', files);
        files;
    """)

    assert result.success
    assert "/file1.txt" in result.result
    assert "/file2.txt" in result.result
    assert "/data/file3.txt" in result.result


@pytest.mark.asyncio
async def test_javascript_file_not_found(db_pool, thread_id):
    """Test JavaScript error handling for missing files."""
    executor = JavascriptSandboxExecutor(db_pool, thread_id)

    result = await executor.execute("""
        try {
            readFile('/nonexistent.txt');
        } catch (e) {
            console.error('Error:', e.message);
            e.message;
        }
    """)

    assert result.success
    assert "File not found" in result.result


@pytest.mark.asyncio
async def test_javascript_respects_quota(db_pool, thread_id):
    """Test that JavaScript executor respects resource quotas."""
    executor = JavascriptSandboxExecutor(
        db_pool,
        thread_id,
        max_files=2,  # Low limit for testing
    )
    vfs = VirtualFilesystem(db_pool, thread_id)

    # Create files up to the limit
    await vfs.write_file("/file1.txt", b"Content 1")
    await vfs.write_file("/file2.txt", b"Content 2")

    # Try to execute JavaScript - should fail due to quota
    result = await executor.execute("""
        writeFile('/file3.txt', 'Content 3');
        'Should not succeed';
    """)

    assert not result.success
    assert "File limit exceeded" in result.stderr or "quota" in result.stderr.lower()


@pytest.mark.asyncio
async def test_javascript_path_validation(db_pool, thread_id):
    """Test that VFS path validation works for JavaScript."""
    vfs = VirtualFilesystem(db_pool, thread_id)

    # Attempt path traversal
    with pytest.raises(Exception) as exc_info:
        await vfs.write_file("/../etc/passwd", b"hacked")

    assert "traversal" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_cross_language_file_sharing(db_pool, thread_id):
    """Test that Python and JavaScript can share files via VFS.

    This test demonstrates the key integration point: files created
    by Python code can be read by JavaScript code, and vice versa.
    """
    from mayflower_sandbox.sandbox_executor import SandboxExecutor  # type: ignore[import-untyped]

    # Step 1: Python writes a file
    py_executor = SandboxExecutor(db_pool, thread_id)
    py_result = await py_executor.execute("""
with open('/shared.txt', 'w') as f:
    f.write('Hello from Python!')
    """)

    assert py_result.success

    # Step 2: JavaScript reads the Python-created file
    js_executor = JavascriptSandboxExecutor(db_pool, thread_id)
    js_result = await js_executor.execute("""
        const content = readFile('/shared.txt');
        console.log('Read from Python:', content);
        content;
    """)

    assert js_result.success
    assert js_result.result == "Hello from Python!"

    # Step 3: JavaScript writes a file
    js_result2 = await js_executor.execute("""
        writeFile('/js_created.json', JSON.stringify({
            message: 'Hello from JavaScript!',
            numbers: [1, 2, 3]
        }));
        'File created';
    """)

    assert js_result2.success

    # Step 4: Python reads the JavaScript-created file
    py_result2 = await py_executor.execute("""
import json
with open('/js_created.json', 'r') as f:
    data = json.load(f)
print('Read from JavaScript:', data)
data
    """)

    assert py_result2.success
    assert py_result2.result["message"] == "Hello from JavaScript!"
    assert py_result2.result["numbers"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_thread_isolation(db_pool):
    """Test that different thread_ids have isolated VFS."""
    import uuid

    thread1 = f"test-{uuid.uuid4()}"
    thread2 = f"test-{uuid.uuid4()}"

    executor1 = JavascriptSandboxExecutor(db_pool, thread1)
    executor2 = JavascriptSandboxExecutor(db_pool, thread2)

    # Thread 1 creates a file
    result1 = await executor1.execute("""
        writeFile('/secret.txt', 'Thread 1 data');
        'File created';
    """)
    assert result1.success

    # Thread 2 cannot see Thread 1's file
    result2 = await executor2.execute("""
        const files = listFiles();
        files.includes('/secret.txt');
    """)
    assert result2.success
    assert result2.result is False  # File not in thread 2's VFS

    # Thread 2 creates its own file with the same name
    result3 = await executor2.execute("""
        writeFile('/secret.txt', 'Thread 2 data');
        readFile('/secret.txt');
    """)
    assert result3.success
    assert result3.result == "Thread 2 data"

    # Thread 1's file is unchanged
    vfs1 = VirtualFilesystem(db_pool, thread1)
    file1 = await vfs1.read_file("/secret.txt")
    assert file1["content"].decode("utf-8") == "Thread 1 data"

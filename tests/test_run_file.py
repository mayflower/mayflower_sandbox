"""
Test RunPythonFileTool - executing Python files from VFS.
"""

import os

import asyncpg
import pytest

from mayflower_sandbox.tools import RunPythonFileTool
from mayflower_sandbox.filesystem import VirtualFilesystem


@pytest.fixture
async def db_pool():
    """Create PostgreSQL connection pool for testing."""
    pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "mayflower_test"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )
    yield pool
    await pool.close()


@pytest.fixture
async def setup_vfs(db_pool):
    """Setup VFS with test thread_id."""
    thread_id = "test_run_file"
    vfs = VirtualFilesystem(db_pool, thread_id)
    await vfs.ensure_session()

    # Clean up any existing files
    files = await vfs.list_files()
    for file_info in files:
        await vfs.delete_file(file_info["file_path"])

    yield vfs, thread_id

    # Cleanup after test
    files = await vfs.list_files()
    for file_info in files:
        await vfs.delete_file(file_info["file_path"])


@pytest.mark.asyncio
async def test_run_simple_script(db_pool, setup_vfs):
    """Test running a simple Python script."""
    vfs, thread_id = setup_vfs

    # Create a simple Python script
    script_content = """
print("Hello from script!")
print("Sum:", 10 + 20)
"""
    await vfs.write_file("/tmp/test_script.py", script_content.encode("utf-8"))

    # Run the script
    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/test_script.py")

    assert "Hello from script!" in result
    assert "Sum: 30" in result
    assert "Executed:** /tmp/test_script.py" in result


@pytest.mark.asyncio
async def test_run_script_with_file_creation(db_pool, setup_vfs):
    """Test running a script that creates files."""
    vfs, thread_id = setup_vfs

    # Create a script that creates a file
    script_content = """
with open("/tmp/output.txt", "w") as f:
    f.write("Created by script")
print("File created!")
"""
    await vfs.write_file("/tmp/create_file.py", script_content.encode("utf-8"))

    # Run the script
    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/create_file.py")

    assert "File created!" in result
    assert "/tmp/output.txt" in result or "output.txt" in result

    # Verify file was created in VFS
    file_info = await vfs.read_file("/tmp/output.txt")
    assert file_info["content"] == b"Created by script"


@pytest.mark.asyncio
async def test_run_script_with_imports(db_pool, setup_vfs):
    """Test running a script that uses imports."""
    vfs, thread_id = setup_vfs

    # Create a script with imports
    script_content = """
import json
import math

data = {"value": math.pi}
print(json.dumps(data))
"""
    await vfs.write_file("/tmp/imports.py", script_content.encode("utf-8"))

    # Run the script
    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/imports.py")

    assert "value" in result
    assert "3.14" in result  # Part of pi


@pytest.mark.asyncio
async def test_run_nonexistent_file(db_pool, setup_vfs):
    """Test running a file that doesn't exist."""
    vfs, thread_id = setup_vfs

    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/nonexistent.py")

    assert "Error" in result
    assert "not found" in result


@pytest.mark.asyncio
async def test_run_script_with_error(db_pool, setup_vfs):
    """Test running a script with a Python error."""
    vfs, thread_id = setup_vfs

    # Create a script with an error
    script_content = """
print("Before error")
x = 1 / 0  # This will raise ZeroDivisionError
print("After error")  # This won't execute
"""
    await vfs.write_file("/tmp/error_script.py", script_content.encode("utf-8"))

    # Run the script
    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/error_script.py")

    assert "Before error" in result
    assert "Error" in result or "ZeroDivisionError" in result
    assert "After error" not in result  # Should not execute


@pytest.mark.asyncio
async def test_run_non_python_file(db_pool, setup_vfs):
    """Test running a non-Python file (should still work but may warn)."""
    vfs, thread_id = setup_vfs

    # Create a text file with Python code
    script_content = 'print("This is in a .txt file")'
    await vfs.write_file("/tmp/script.txt", script_content.encode("utf-8"))

    # Run the file (should still work since it's valid Python)
    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/script.txt")

    # Should execute successfully despite .txt extension
    assert "This is in a .txt file" in result


@pytest.mark.asyncio
async def test_run_script_creates_image(db_pool, setup_vfs):
    """Test running a script that creates an image file."""
    vfs, thread_id = setup_vfs

    # Create a script that creates a simple image
    script_content = """
# Create a simple 1x1 PNG (smallest valid PNG)
import base64

# Minimal 1x1 transparent PNG
png_data = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

with open("/tmp/test_image.png", "wb") as f:
    f.write(png_data)

print("Image created at /tmp/test_image.png")
"""
    await vfs.write_file("/tmp/create_image.py", script_content.encode("utf-8"))

    # Run the script
    tool = RunPythonFileTool(db_pool=db_pool, thread_id=thread_id)
    result = await tool._arun(file_path="/tmp/create_image.py")

    assert "Image created" in result
    # Should include markdown image syntax for PNG files
    assert "![Generated image]" in result or "/tmp/test_image.png" in result

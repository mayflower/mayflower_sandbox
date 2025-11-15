"""
Tests for JavaScript/TypeScript LangChain tools.

Tests ExecuteJavascriptTool, RunJavascriptFileTool, and ExecuteJavascriptCodeTool.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.tools import (  # type: ignore[import-untyped]
    ExecuteJavascriptCodeTool,
    ExecuteJavascriptTool,
    RunJavascriptFileTool,
    create_sandbox_tools,
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
            VALUES ('test_js_tools', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_js_tools'")
    yield


# ============================
# Tool Factory Tests
# ============================


async def test_tool_factory_with_javascript(db_pool, clean_files):
    """Test creating tools with enable_javascript=True."""
    tools = create_sandbox_tools(db_pool, "test_js_tools", enable_javascript=True)

    # Should have 12 core tools + 3 JavaScript tools = 15 total
    assert len(tools) == 15

    tool_names = {tool.name for tool in tools}
    assert "javascript_run" in tool_names
    assert "javascript_run_file" in tool_names
    assert "javascript_run_prepared" in tool_names


async def test_tool_factory_without_javascript(db_pool, clean_files):
    """Test creating tools with enable_javascript=False (default)."""
    tools = create_sandbox_tools(db_pool, "test_js_tools")

    # Should have only 12 core tools (no JavaScript tools)
    assert len(tools) == 12

    tool_names = {tool.name for tool in tools}
    assert "javascript_run" not in tool_names
    assert "javascript_run_file" not in tool_names
    assert "javascript_run_prepared" not in tool_names


async def test_tool_factory_specific_javascript_tools(db_pool, clean_files):
    """Test creating only specific JavaScript tools."""
    tools = create_sandbox_tools(
        db_pool,
        "test_js_tools",
        include_tools=["javascript_run", "file_read"],
        enable_javascript=True,
    )

    assert len(tools) == 2
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"javascript_run", "file_read"}


# ============================
# ExecuteJavascriptTool Tests
# ============================


async def test_execute_javascript_tool(db_pool, clean_files):
    """Test ExecuteJavascriptTool with simple code."""
    tool = ExecuteJavascriptTool(db_pool=db_pool, thread_id="test_js_tools")

    result = await tool._arun(code="console.log('Hello from JavaScript tool!');")

    assert "Hello from JavaScript tool!" in result


async def test_execute_javascript_tool_with_computation(db_pool, clean_files):
    """Test ExecuteJavascriptTool with computation."""
    tool = ExecuteJavascriptTool(db_pool=db_pool, thread_id="test_js_tools")

    code = """
const numbers = [1, 2, 3, 4, 5];
const sum = numbers.reduce((a, b) => a + b, 0);
console.log('Sum:', sum);
"""
    result = await tool._arun(code=code)

    assert "Sum: 15" in result


async def test_execute_javascript_tool_with_error(db_pool, clean_files):
    """Test ExecuteJavascriptTool handles errors."""
    tool = ExecuteJavascriptTool(db_pool=db_pool, thread_id="test_js_tools")

    result = await tool._arun(code="throw new Error('Test error');")

    assert "Error:" in result
    assert "Test error" in result


async def test_execute_javascript_tool_creates_files(db_pool, clean_files):
    """Test ExecuteJavascriptTool creates files in VFS."""
    tool = ExecuteJavascriptTool(db_pool=db_pool, thread_id="test_js_tools")

    code = """
writeFile('/test_output.txt', 'Created by JavaScript tool!');
console.log('File created');
"""
    result = await tool._arun(code=code)

    assert "File created" in result
    assert "/test_output.txt" in result

    # Verify file in VFS
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_js_tools' AND file_path = '/test_output.txt'
        """
        )

        assert file_data is not None
        assert file_data["content"] == b"Created by JavaScript tool!"


async def test_execute_javascript_tool_json_operations(db_pool, clean_files):
    """Test ExecuteJavascriptTool with JSON operations."""
    tool = ExecuteJavascriptTool(db_pool=db_pool, thread_id="test_js_tools")

    code = """
const data = {
    name: 'Test',
    values: [10, 20, 30],
    sum: [10, 20, 30].reduce((a, b) => a + b, 0)
};

writeFile('/data.json', JSON.stringify(data, null, 2));
console.log('JSON file created with sum:', data.sum);
"""
    result = await tool._arun(code=code)

    assert "JSON file created with sum: 60" in result
    assert "/data.json" in result


# ============================
# RunJavascriptFileTool Tests
# ============================


async def test_run_javascript_file_tool(db_pool, clean_files):
    """Test RunJavascriptFileTool executes .js files."""
    tool = RunJavascriptFileTool(db_pool=db_pool, thread_id="test_js_tools")

    # First, create a JavaScript file in VFS
    async with db_pool.acquire() as conn:
        js_code = b"console.log('Hello from JS file!');\nconsole.log('File execution works!');"
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_js_tools', '/script.js', $1, 'text/javascript', $2)
        """,
            js_code,
            len(js_code),
        )

    # Execute the file
    result = await tool._arun(file_path="/script.js")

    assert "Executed:" in result
    assert "/script.js" in result
    assert "Hello from JS file!" in result
    assert "File execution works!" in result


async def test_run_javascript_file_tool_typescript(db_pool, clean_files):
    """Test RunJavascriptFileTool executes .ts files."""
    tool = RunJavascriptFileTool(db_pool=db_pool, thread_id="test_js_tools")

    # Create a TypeScript file
    async with db_pool.acquire() as conn:
        ts_code = b"const add = (a: number, b: number): number => a + b;\nconsole.log('Result:', add(5, 7));"
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_js_tools', '/script.ts', $1, 'text/typescript', $2)
        """,
            ts_code,
            len(ts_code),
        )

    # Execute the TypeScript file
    result = await tool._arun(file_path="/script.ts")

    assert "Executed:" in result
    assert "/script.ts" in result
    assert "Result: 12" in result


async def test_run_javascript_file_tool_with_vfs_operations(db_pool, clean_files):
    """Test RunJavascriptFileTool with VFS file operations."""
    tool = RunJavascriptFileTool(db_pool=db_pool, thread_id="test_js_tools")

    # Create a JavaScript file that creates another file
    async with db_pool.acquire() as conn:
        js_code = b"""
writeFile('/output.txt', 'Generated by script');
console.log('Output file created');
"""
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_js_tools', '/generator.js', $1, 'text/javascript', $2)
        """,
            js_code,
            len(js_code),
        )

    # Execute the file
    result = await tool._arun(file_path="/generator.js")

    assert "Output file created" in result
    assert "/output.txt" in result

    # Verify the generated file
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_js_tools' AND file_path = '/output.txt'
        """
        )

        assert file_data is not None
        assert file_data["content"] == b"Generated by script"


async def test_run_javascript_file_tool_error(db_pool, clean_files):
    """Test RunJavascriptFileTool handles errors in files."""
    tool = RunJavascriptFileTool(db_pool=db_pool, thread_id="test_js_tools")

    # Create a file with an error
    async with db_pool.acquire() as conn:
        js_code = b"throw new Error('Error in file');"
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_js_tools', '/error.js', $1, 'text/javascript', $2)
        """,
            js_code,
            len(js_code),
        )

    # Execute the file
    result = await tool._arun(file_path="/error.js")

    assert "Execution failed" in result or "Error:" in result
    assert "Error in file" in result


# ============================
# ExecuteJavascriptCodeTool Tests
# ============================


async def test_execute_javascript_code_tool_from_state(db_pool, clean_files):
    """Test ExecuteJavascriptCodeTool extracts and executes code from state."""
    tool = ExecuteJavascriptCodeTool(db_pool=db_pool, thread_id="test_js_tools")

    # Simulate graph state with pending_content_map
    tool_call_id = "test_js_exec_123"
    state = {
        "pending_content_map": {
            tool_call_id: """
const numbers = [1, 2, 3, 4, 5];
const squared = numbers.map(n => n * n);
const sum = squared.reduce((a, b) => a + b, 0);

console.log('Sum of squares:', sum);
console.log('JavaScript execution successful!');
"""
        }
    }

    # Execute the tool
    result = await tool._arun(
        file_path="/tmp/test_code.js",
        description="Test state-based JavaScript extraction",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    try:
        from langgraph.types import Command

        if isinstance(result, Command):
            result_str = result.resume
        else:
            result_str = result
    except ImportError:
        result_str = result

    # Verify execution succeeded
    assert "Sum of squares: 55" in result_str
    assert "JavaScript execution successful!" in result_str
    assert "Error" not in result_str


async def test_execute_javascript_code_tool_with_file_creation(db_pool, clean_files):
    """Test ExecuteJavascriptCodeTool can create files and they persist."""
    tool = ExecuteJavascriptCodeTool(db_pool=db_pool, thread_id="test_js_tools")

    tool_call_id = "test_js_file_create_456"
    state = {
        "pending_content_map": {
            tool_call_id: """
const data = {
    message: 'Hello from state-based JavaScript!',
    timestamp: new Date().toISOString(),
    values: [100, 200, 300]
};

writeFile('/js_created.json', JSON.stringify(data, null, 2));
console.log('JSON file created successfully');
"""
        }
    }

    # Execute the tool
    result = await tool._arun(
        file_path="/tmp/test_file.js",
        description="Test file creation",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    try:
        from langgraph.types import Command

        if isinstance(result, Command):
            result_str = result.resume
        else:
            result_str = result
    except ImportError:
        result_str = result

    assert "JSON file created successfully" in result_str
    assert "/js_created.json" in result_str

    # Verify file exists in VFS
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_js_tools' AND file_path = '/js_created.json'
        """
        )

        assert file_data is not None
        content = file_data["content"].decode("utf-8")
        assert "Hello from state-based JavaScript!" in content
        assert "values" in content


async def test_execute_javascript_code_tool_no_state(db_pool, clean_files):
    """Test ExecuteJavascriptCodeTool returns error when state is missing."""
    tool = ExecuteJavascriptCodeTool(db_pool=db_pool, thread_id="test_js_tools")

    # Call without state
    result = await tool._arun(
        file_path="/tmp/test.js",
        description="Test without state",
        _state=None,
    )

    assert "Error: This tool requires graph state" in result


async def test_execute_javascript_code_tool_missing_code_in_state(db_pool, clean_files):
    """Test ExecuteJavascriptCodeTool handles missing code in state."""
    tool = ExecuteJavascriptCodeTool(db_pool=db_pool, thread_id="test_js_tools")

    # State with different tool_call_id
    tool_call_id = "test_js_missing_789"
    state = {
        "pending_content_map": {
            "different_id": "console.log('This is not the code we are looking for');"
        }
    }

    # Execute the tool
    result = await tool._arun(
        file_path="/tmp/test.js",
        description="Test missing code",
        _state=state,
        tool_call_id=tool_call_id,
    )

    assert "Error: No code found in graph state" in result


async def test_execute_javascript_code_tool_typescript(db_pool, clean_files):
    """Test ExecuteJavascriptCodeTool with TypeScript code."""
    tool = ExecuteJavascriptCodeTool(db_pool=db_pool, thread_id="test_js_tools")

    tool_call_id = "test_ts_exec_101"
    state = {
        "pending_content_map": {
            tool_call_id: """
interface Point {
    x: number;
    y: number;
}

const point: Point = { x: 3, y: 4 };
const distance: number = Math.sqrt(point.x ** 2 + point.y ** 2);

console.log('Distance from origin:', distance);
"""
        }
    }

    # Execute the tool with .ts extension
    result = await tool._arun(
        file_path="/tmp/test_code.ts",
        description="Test TypeScript execution",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    try:
        from langgraph.types import Command

        if isinstance(result, Command):
            result_str = result.resume
        else:
            result_str = result
    except ImportError:
        result_str = result

    assert "Distance from origin: 5" in result_str

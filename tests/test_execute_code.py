"""
Test ExecuteCodeTool (python_run_prepared) with state-based code extraction.
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from mayflower_sandbox.tools.execute_code import ExecuteCodeTool  # noqa: E402


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

    pool = await asyncpg.create_pool(**db_config)

    # Ensure session exists
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_execute_code', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_execute_code'")
    yield


async def test_execute_code_from_state(db_pool, clean_files):
    """Test that python_run_prepared extracts and executes code from state."""
    tool = ExecuteCodeTool(db_pool=db_pool, thread_id="test_execute_code")

    # Simulate graph state with pending_content_map
    tool_call_id = "test_exec_123"
    state = {
        "pending_content_map": {
            tool_call_id: """
import math

# Calculate some values
x = 42
y = math.sqrt(x)

print(f"Square root of {x} is {y}")
print("Code executed successfully!")
"""
        }
    }

    # Execute the tool (with tool_call_id = returns Command)
    result = await tool._arun(
        file_path="/tmp/test_code.py",
        description="Test state-based code extraction",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    from langgraph.types import Command

    if isinstance(result, Command):
        result_str = result.resume
    else:
        result_str = result

    # Verify execution succeeded
    assert "Square root of 42 is" in result_str
    assert "Code executed successfully!" in result_str
    assert "Error" not in result_str


async def test_execute_code_with_file_creation(db_pool, clean_files):
    """Test that python_run_prepared can create files and they persist."""
    tool = ExecuteCodeTool(db_pool=db_pool, thread_id="test_execute_code")

    tool_call_id = "test_file_create_456"
    state = {
        "pending_content_map": {
            tool_call_id: """
# Create a simple data file
data = "Hello from state-based execution!"

with open('/tmp/output.txt', 'w') as f:
    f.write(data)

print(f"Created file with {len(data)} characters")
"""
        }
    }

    result = await tool._arun(
        file_path="/tmp/create_file.py",
        description="Create a file from state code",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    from langgraph.types import Command

    if isinstance(result, Command):
        result_str = result.resume
    else:
        result_str = result

    # Verify execution succeeded
    assert "Created file with" in result_str
    assert "characters" in result_str

    # Verify file was created and persisted
    assert "/tmp/output.txt" in result_str or "Created files:" in result_str


async def test_execute_code_with_computation(db_pool, clean_files):
    """Test python_run_prepared with computational code."""
    tool = ExecuteCodeTool(db_pool=db_pool, thread_id="test_execute_code")

    tool_call_id = "test_fib_789"
    state = {
        "pending_content_map": {
            tool_call_id: """
# Fibonacci calculation
def fib(n):
    if n <= 1:
        return n
    return fib(n-1) + fib(n-2)

result = fib(10)
print(f"Fibonacci(10) = {result}")

# Verify the result
assert result == 55, "Fibonacci calculation failed"
print("Computation verified!")
"""
        }
    }

    result = await tool._arun(
        file_path="/tmp/fibonacci.py",
        description="Calculate Fibonacci numbers",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    from langgraph.types import Command

    if isinstance(result, Command):
        result_str = result.resume
    else:
        result_str = result

    # Verify execution succeeded
    assert "Fibonacci(10) = 55" in result_str
    assert "Computation verified!" in result_str


async def test_execute_code_no_code_in_state(db_pool, clean_files):
    """Test error handling when no code is in state."""
    tool = ExecuteCodeTool(db_pool=db_pool, thread_id="test_execute_code")

    # Empty state - no pending_content
    state = {}

    result = await tool._arun(
        file_path="/tmp/empty.py",
        description="Should fail - no code",
        _state=state,
        tool_call_id="",
    )

    # Verify error message
    assert "Error" in result
    assert "No code found in graph state" in result


async def test_execute_code_with_imports(db_pool, clean_files):
    """Test python_run_prepared with package imports."""
    tool = ExecuteCodeTool(db_pool=db_pool, thread_id="test_execute_code")

    tool_call_id = "test_numpy_111"
    state = {
        "pending_content_map": {
            tool_call_id: """
import micropip
await micropip.install('numpy')

import numpy as np

# Create array and calculate
arr = np.array([1, 2, 3, 4, 5])
mean = np.mean(arr)
std = np.std(arr)

print(f"Array mean: {mean}")
print(f"Array std: {std:.4f}")
"""
        }
    }

    result = await tool._arun(
        file_path="/tmp/numpy_test.py",
        description="Test with numpy",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    from langgraph.types import Command

    if isinstance(result, Command):
        result_str = result.resume
    else:
        result_str = result

    # Verify numpy code executed
    assert "Array mean: 3.0" in result_str or "Array mean: 3" in result_str
    assert "Array std:" in result_str

"""
Test that the stateful fix works correctly.
"""

import pytest
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

load_dotenv()

from mayflower_sandbox.tools.execute import get_sandbox, get_session_state, save_session_state


@pytest.mark.asyncio
async def test_sandbox_instance_creation():
    """Test that we can create a stateful sandbox instance."""
    try:
        sandbox = await get_sandbox()
        assert sandbox is not None
        print(f"✅ Sandbox created: {type(sandbox)}")
        print(f"✅ Stateful: {getattr(sandbox, 'stateful', 'unknown')}")
    except RuntimeError as e:
        pytest.skip(f"langchain-sandbox not available: {e}")


@pytest.mark.asyncio
async def test_session_state_persistence():
    """Test that session state persists between executions."""
    try:
        sandbox = await get_sandbox()
    except RuntimeError as e:
        pytest.skip(f"langchain-sandbox not available: {e}")

    thread_id = "test_stateful"

    # First execution: set a variable
    code1 = "x = 42\nprint(f'Set x = {x}')"
    result1 = await sandbox.execute(code1, timeout_seconds=10.0)

    assert result1.status == "success", f"First execution failed: {result1.stderr}"
    assert "42" in result1.stdout

    # Save session state
    if result1.session_bytes:
        save_session_state(thread_id, result1.session_bytes, result1.session_metadata)
        print(f"✅ Saved session state: {len(result1.session_bytes)} bytes")
    else:
        pytest.fail("No session_bytes returned - stateful mode not working!")

    # Second execution: use the variable (should persist)
    session_bytes, session_metadata = get_session_state(thread_id)
    assert session_bytes is not None, "Session state not found in cache"

    code2 = "print(f'x still equals {x}')"
    result2 = await sandbox.execute(
        code2,
        session_bytes=session_bytes,
        session_metadata=session_metadata,
        timeout_seconds=10.0,
    )

    assert result2.status == "success", f"Second execution failed: {result2.stderr}"
    assert "42" in result2.stdout, "Variable x was not preserved between executions!"
    print("✅ Variable persisted between executions")


@pytest.mark.asyncio
async def test_file_persistence_in_session():
    """Test that files created in one execution are available in the next."""
    try:
        sandbox = await get_sandbox()
    except RuntimeError as e:
        pytest.skip(f"langchain-sandbox not available: {e}")

    thread_id = "test_files"

    # First execution: create a file
    code1 = """
with open('/tmp/test_data.txt', 'w') as f:
    f.write('Hello from first execution')
print('File created')
"""
    result1 = await sandbox.execute(code1, timeout_seconds=10.0)

    assert result1.status == "success", f"First execution failed: {result1.stderr}"
    assert "File created" in result1.stdout

    # Save session state
    if result1.session_bytes:
        save_session_state(thread_id, result1.session_bytes, result1.session_metadata)
        print(f"✅ Saved session with file: {len(result1.session_bytes)} bytes")
    else:
        pytest.fail("No session_bytes returned!")

    # Second execution: read the file
    session_bytes, session_metadata = get_session_state(thread_id)

    code2 = """
with open('/tmp/test_data.txt', 'r') as f:
    content = f.read()
print(f'File content: {content}')
"""
    result2 = await sandbox.execute(
        code2,
        session_bytes=session_bytes,
        session_metadata=session_metadata,
        timeout_seconds=10.0,
    )

    assert result2.status == "success", f"Second execution failed: {result2.stderr}"
    assert "Hello from first execution" in result2.stdout, "File was not preserved!"
    print("✅ File persisted between executions")

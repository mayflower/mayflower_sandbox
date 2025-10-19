import pytest
import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.executor import PyodideExecutor


@pytest.fixture
def executor():
    """Create basic executor."""
    return PyodideExecutor(allow_net=True)


@pytest.fixture
def stateful_executor():
    """Create stateful executor."""
    return PyodideExecutor(allow_net=True, stateful=True)


async def test_simple_execution(executor):
    """Test basic code execution."""
    result = await executor.execute("print('Hello, World!')")

    assert result.status == "success"
    assert "Hello, World!" in result.stdout
    assert result.stderr is None or result.stderr == ""


async def test_computation(executor):
    """Test computation with result."""
    code = """
x = 5 + 7
print(f"Result: {x}")
"""
    result = await executor.execute(code)

    assert result.status == "success"
    assert "Result: 12" in result.stdout


async def test_error_handling(executor):
    """Test error is captured."""
    code = "raise ValueError('test error')"
    result = await executor.execute(code)

    assert result.status == "error"
    assert "ValueError" in result.stderr
    assert "test error" in result.stderr


async def test_timeout():
    """Test timeout handling."""
    executor = PyodideExecutor(timeout_seconds=1.0)

    code = """
import time
time.sleep(5)
"""
    result = await executor.execute(code)

    assert result.status == "error"
    assert "timed out" in result.stderr.lower()


async def test_file_preload(executor):
    """Test loading files from VFS into Pyodide memfs."""
    # Pre-load files (simulating PostgreSQL VFS)
    files = {
        "/tmp/data.txt": b"Hello from VFS!",
        "/data/numbers.txt": b"1\n2\n3\n",
    }

    code = """
# Read files that were pre-loaded from PostgreSQL
with open('/tmp/data.txt', 'r') as f:
    content = f.read()
    print(f"Data: {content}")

with open('/data/numbers.txt', 'r') as f:
    numbers = f.read()
    print(f"Numbers: {numbers}")
"""

    result = await executor.execute(code, files=files)

    assert result.status == "success"
    assert "Data: Hello from VFS!" in result.stdout
    assert "Numbers: 1" in result.stdout


async def test_file_creation_detection(executor):
    """Test detecting files created by Python code."""
    code = """
# Create a file in Pyodide memfs
with open('/tmp/output.txt', 'w') as f:
    f.write("Created in Pyodide!")

print("File created")
"""

    result = await executor.execute(code)

    assert result.status == "success"
    assert "File created" in result.stdout
    # Check that created_files list contains the path
    assert result.created_files is not None
    assert "/tmp/output.txt" in result.created_files


async def test_stateful_session(stateful_executor):
    """Test stateful session preserves variables."""
    # First execution: set variable
    result1 = await stateful_executor.execute("x = 42")
    assert result1.status == "success"
    assert result1.session_bytes is not None

    # Second execution: use variable
    result2 = await stateful_executor.execute(
        "print(x)",
        session_bytes=result1.session_bytes,
        session_metadata=result1.session_metadata,
    )

    assert result2.status == "success"
    assert "42" in result2.stdout


async def test_numpy_execution(executor):
    """Test numpy import and usage."""
    code = """
import numpy as np
x = np.array([1, 2, 3])
print(f"Array: {x}")
print(f"Sum: {x.sum()}")
"""
    result = await executor.execute(code)

    assert result.status == "success"
    assert "Array:" in result.stdout
    assert "Sum: 6" in result.stdout


async def test_file_creation_with_matplotlib(executor):
    """Test matplotlib creates files that are detected."""
    code = """
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(0, 10, 100)
plt.plot(x, np.sin(x))
plt.savefig('/tmp/plot.png')
print("Plot saved")
"""
    result = await executor.execute(code)

    assert result.status == "success"
    assert "Plot saved" in result.stdout
    assert result.created_files is not None
    assert "/tmp/plot.png" in result.created_files


async def test_vfs_integration_roundtrip(executor):
    """Test full VFS integration: pre-load → execute → detect created files."""
    # Simulate loading data from PostgreSQL VFS
    input_files = {
        "/data/input.csv": b"name,age\nAlice,30\nBob,25",
    }

    code = """
import pandas as pd

# Read file pre-loaded from PostgreSQL
df = pd.read_csv('/data/input.csv')
print(f"Read {len(df)} rows")

# Process and save
df['age_plus_10'] = df['age'] + 10
df.to_csv('/tmp/output.csv', index=False)
print("Output saved")
"""

    result = await executor.execute(code, files=input_files)

    assert result.status == "success"
    assert "Read 2 rows" in result.stdout
    assert "Output saved" in result.stdout
    # Verify output file was detected
    assert result.created_files is not None
    assert "/tmp/output.csv" in result.created_files


async def test_multiple_file_operations(executor):
    """Test creating multiple files in one execution."""
    code = """
# Create multiple files
with open('/tmp/file1.txt', 'w') as f:
    f.write("File 1")

with open('/tmp/file2.txt', 'w') as f:
    f.write("File 2")

with open('/data/result.json', 'w') as f:
    import json
    json.dump({"status": "success"}, f)

print("All files created")
"""

    result = await executor.execute(code)

    assert result.status == "success"
    assert "All files created" in result.stdout
    assert result.created_files is not None
    assert len(result.created_files) >= 3
    assert "/tmp/file1.txt" in result.created_files
    assert "/tmp/file2.txt" in result.created_files
    assert "/data/result.json" in result.created_files

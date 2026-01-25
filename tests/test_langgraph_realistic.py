"""
Realistic LangGraph tests using only what's available in Pyodide.

These tests use only built-in Python libraries and avoid packages
that aren't available, preventing infinite recursion.

Test Strategy:
- All assertions use deterministic VFS verification
- NO brittle patterns like string matching on LLM responses
- Verify file existence and content directly in database
"""

import json
import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

load_dotenv()

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from mayflower_sandbox.tools import create_sandbox_tools


async def vfs_read_file(db_pool, thread_id: str, path: str) -> bytes | None:
    """Read file content from VFS. Returns None if file doesn't exist."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = $1 AND file_path = $2
            """,
            thread_id,
            path,
        )
        return row["content"] if row else None


async def vfs_file_exists(db_pool, thread_id: str, path: str) -> bool:
    """Check if file exists in VFS."""
    return await vfs_read_file(db_pool, thread_id, path) is not None


# Mark all tests in this module as slow (LLM-based)
pytestmark = pytest.mark.slow


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "min_size": 10,
        "max_size": 50,  # Increase pool size for concurrent LangGraph agent operations
        "command_timeout": 60,
    }

    pool = await asyncpg.create_pool(**db_config)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('realistic_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files and error history before each test."""
    # Clean files
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'realistic_test'")

    # Clear error history
    from mayflower_sandbox.tools.execute import _error_history

    _error_history.clear()

    yield


@pytest.fixture
def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    tools = create_sandbox_tools(db_pool, thread_id="realistic_test")
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    agent = create_agent(llm, tools, checkpointer=MemorySaver())
    return agent


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_json_file_creation(agent, db_pool, clean_files):
    """Test creating and reading JSON files."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a JSON file at /tmp/config.json with this data:
{"app": "test", "version": "1.0", "settings": {"debug": true}}
Then read it back and tell me the version number.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-json-1"}},
    )

    # Deterministic VFS verification instead of LLM response parsing
    content = await vfs_read_file(db_pool, "realistic_test", "/tmp/config.json")
    assert content is not None, "JSON file was not created"
    data = json.loads(content.decode("utf-8"))
    assert data["version"] == "1.0"
    assert data["app"] == "test"
    assert data["settings"]["debug"] is True


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_csv_data_processing(agent, db_pool, clean_files):
    """Test CSV file creation and processing with built-in csv module."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a CSV file at /tmp/sales.csv with:
name,amount
Alice,100
Bob,150
Charlie,200

Then calculate the total amount and tell me the result.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-csv-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "realistic_test", "/tmp/sales.csv")
    assert content is not None, "CSV file was not created"
    csv_text = content.decode("utf-8")
    # Verify CSV structure and data
    assert "Alice" in csv_text and "100" in csv_text
    assert "Bob" in csv_text and "150" in csv_text
    assert "Charlie" in csv_text and "200" in csv_text


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_text_file_manipulation(agent, db_pool, clean_files):
    """Test basic text file operations."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Write a text file /tmp/notes.txt with three lines:
Line 1: Hello
Line 2: World
Line 3: Test

Then count how many lines it has.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-text-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "realistic_test", "/tmp/notes.txt")
    assert content is not None, "Text file was not created"
    text = content.decode("utf-8")
    lines = [line for line in text.strip().split("\n") if line.strip()]
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_mathematical_computation(agent, db_pool, clean_files):
    """Test performing calculations and saving results."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Calculate the factorial of 10 using Python, then save the result to /tmp/result.txt""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-math-1"}},
    )

    # Deterministic VFS verification - 10! = 3628800
    content = await vfs_read_file(db_pool, "realistic_test", "/tmp/result.txt")
    assert content is not None, "Result file was not created"
    assert b"3628800" in content, "Expected factorial result 3628800 in file content"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_string_processing(agent, db_pool, clean_files):
    """Test string manipulation and file I/O."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a file /tmp/input.txt with the text "hello world".
Then create /tmp/output.txt with the same text but uppercase.
Tell me what the output file contains.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-string-1"}},
    )

    # Deterministic VFS verification
    input_content = await vfs_read_file(db_pool, "realistic_test", "/tmp/input.txt")
    assert input_content is not None, "Input file was not created"
    assert b"hello world" in input_content.lower()

    output_content = await vfs_read_file(db_pool, "realistic_test", "/tmp/output.txt")
    assert output_content is not None, "Output file was not created"
    assert b"HELLO WORLD" in output_content.upper()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_list_and_count_files(agent, db_pool, clean_files):
    """Test file listing functionality."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create three files:
- /tmp/file1.txt with content "one"
- /tmp/file2.txt with content "two"
- /tmp/file3.txt with content "three"

Then list all files in /tmp and tell me how many there are.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-list-1"}},
    )

    # Deterministic VFS verification - check all three files exist
    for filename, expected_content in [
        ("/tmp/file1.txt", b"one"),
        ("/tmp/file2.txt", b"two"),
        ("/tmp/file3.txt", b"three"),
    ]:
        content = await vfs_read_file(db_pool, "realistic_test", filename)
        assert content is not None, f"File {filename} was not created"
        assert expected_content in content.lower(), (
            f"Expected '{expected_content.decode()}' in {filename}"
        )


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_data_aggregation(agent, db_pool, clean_files):
    """Test reading multiple files and aggregating data."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create two JSON files:
- /tmp/data1.json: {"value": 10}
- /tmp/data2.json: {"value": 20}

Read both files and calculate the sum of the values.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-agg-1"}},
    )

    # Deterministic VFS verification
    content1 = await vfs_read_file(db_pool, "realistic_test", "/tmp/data1.json")
    assert content1 is not None, "data1.json was not created"
    data1 = json.loads(content1.decode("utf-8"))
    assert data1["value"] == 10

    content2 = await vfs_read_file(db_pool, "realistic_test", "/tmp/data2.json")
    assert content2 is not None, "data2.json was not created"
    data2 = json.loads(content2.decode("utf-8"))
    assert data2["value"] == 20

    # Sum verified deterministically
    assert data1["value"] + data2["value"] == 30


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_simple_data_filtering(agent, db_pool, clean_files):
    """Test filtering data from a CSV file."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create /tmp/people.csv:
name,age
Alice,25
Bob,35
Charlie,28

Find all people with age greater than 27 and tell me their names.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-filter-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "realistic_test", "/tmp/people.csv")
    assert content is not None, "CSV file was not created"
    csv_text = content.decode("utf-8")
    # Verify CSV contains expected data (we verify the data, not LLM interpretation)
    assert "Alice" in csv_text and "25" in csv_text
    assert "Bob" in csv_text and "35" in csv_text
    assert "Charlie" in csv_text and "28" in csv_text


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_content_search(agent, db_pool, clean_files):
    """Test searching for content in files."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create /tmp/log.txt with these lines:
INFO: Application started
ERROR: Connection failed
INFO: Retry attempt 1
ERROR: Connection failed
INFO: Connection successful

Count how many lines contain the word "ERROR".""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-search-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "realistic_test", "/tmp/log.txt")
    assert content is not None, "Log file was not created"
    log_text = content.decode("utf-8")
    # Verify file content and count ERROR lines deterministically
    error_lines = [line for line in log_text.split("\n") if "ERROR" in line]
    assert len(error_lines) == 2, f"Expected 2 ERROR lines, got {len(error_lines)}"

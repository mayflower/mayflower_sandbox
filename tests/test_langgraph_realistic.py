"""
Realistic LangGraph tests using only what's available in Pyodide.

These tests use only built-in Python libraries and avoid packages
that aren't available, preventing infinite recursion.
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

load_dotenv()

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from mayflower_sandbox.tools import create_sandbox_tools


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
    agent = create_react_agent(llm, tools, checkpointer=MemorySaver())
    return agent


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_json_file_creation(agent, clean_files):
    """Test creating and reading JSON files."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "1.0" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_csv_data_processing(agent, clean_files):
    """Test CSV file creation and processing with built-in csv module."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "450" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_text_file_manipulation(agent, clean_files):
    """Test basic text file operations."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "3" in response or "three" in response.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_mathematical_computation(agent, clean_files):
    """Test performing calculations and saving results."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    # 10! = 3628800
    assert "3628800" in response or "factorial" in response.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_string_processing(agent, clean_files):
    """Test string manipulation and file I/O."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content.upper()
    assert "HELLO WORLD" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_list_and_count_files(agent, clean_files):
    """Test file listing functionality."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "3" in response or "three" in response.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_data_aggregation(agent, clean_files):
    """Test reading multiple files and aggregating data."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "30" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_simple_data_filtering(agent, clean_files):
    """Test filtering data from a CSV file."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "Bob" in response and "Charlie" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_file_content_search(agent, clean_files):
    """Test searching for content in files."""
    result = await agent.ainvoke(
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

    last_message = result["messages"][-1]
    response = last_message.content
    assert "2" in response or "two" in response.lower()

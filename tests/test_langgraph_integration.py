"""
Integration tests for LangGraph with Mayflower Sandbox tools.

These tests use real LLM calls to verify the sandbox tools work correctly
with LangGraph agents.

Test Strategy:
- All assertions use deterministic VFS verification
- NO brittle patterns like string matching on LLM responses
- Verify file existence and content directly in database
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load environment variables
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

    # Ensure session exists
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('langgraph_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'langgraph_test'")
    yield


@pytest.fixture
async def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    # Create sandbox tools
    tools = create_sandbox_tools(db_pool, thread_id="langgraph_test")

    # Create LLM
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)

    # Create ReAct agent with checkpointer
    agent = create_agent(llm, tools, checkpointer=MemorySaver())

    return agent


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_file_creation(agent, db_pool, clean_files):
    """Test agent can create files using write_file tool."""
    await agent.ainvoke(
        {
            "messages": [
                ("user", "Create a file called /tmp/hello.txt with the content 'Hello, World!'")
            ]
        },
        config={"configurable": {"thread_id": "test-file-creation"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/hello.txt")
    assert content is not None, "File was not created"
    assert b"Hello" in content and b"World" in content


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_python_execution(agent, db_pool, clean_files):
    """Test agent can execute Python code and save result."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Execute Python code to calculate the sum of numbers from 1 to 10 "
                    "and save the result to /tmp/sum_result.txt",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-python"}},
    )

    # Deterministic VFS verification - sum(1..10) = 55
    content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/sum_result.txt")
    assert content is not None, "Result file was not created"
    assert b"55" in content, "Expected sum 55 in file content"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_file_operations_workflow(agent, db_pool, clean_files):
    """Test agent can perform complete file workflow: write, read, process."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Do the following:
1. Write a CSV file /tmp/data.csv with headers 'name,age' and two rows: 'Alice,30' and 'Bob,25'
2. Execute Python code to read the CSV and calculate the average age
3. Save the average to /tmp/average.txt""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-workflow"}},
    )

    # Deterministic VFS verification
    csv_content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/data.csv")
    assert csv_content is not None, "CSV file was not created"
    csv_text = csv_content.decode("utf-8")
    assert "Alice" in csv_text and "30" in csv_text
    assert "Bob" in csv_text and "25" in csv_text

    # Average (30 + 25) / 2 = 27.5
    avg_content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/average.txt")
    assert avg_content is not None, "Average file was not created"
    assert b"27" in avg_content  # Could be 27.5 or 27


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_read_file(agent, db_pool, clean_files):
    """Test agent can read files."""
    # Pre-create a file
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('langgraph_test', '/tmp/secret.txt', $1, 'text/plain', $2)
        """,
            b"The secret is: 42",
            18,
        )

    await agent.ainvoke(
        {
            "messages": [
                ("user", "Read the file /tmp/secret.txt and copy its content to /tmp/copy.txt")
            ]
        },
        config={"configurable": {"thread_id": "test-read"}},
    )

    # Deterministic VFS verification - original file should still exist
    original = await vfs_read_file(db_pool, "langgraph_test", "/tmp/secret.txt")
    assert original is not None, "Original file should still exist"
    assert b"42" in original

    # Copy should have been created with same content
    copy = await vfs_read_file(db_pool, "langgraph_test", "/tmp/copy.txt")
    assert copy is not None, "Copy file should have been created"
    assert b"42" in copy or b"secret" in copy.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_list_and_delete(agent, db_pool, clean_files):
    """Test agent can list and delete files."""
    # Pre-create some files
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES
                ('langgraph_test', '/tmp/file1.txt', $1, 'text/plain', 5),
                ('langgraph_test', '/tmp/file2.txt', $2, 'text/plain', 5)
        """,
            b"test1",
            b"test2",
        )

    # Ask agent to list and delete
    await agent.ainvoke(
        {"messages": [("user", "List all files in /tmp/ and then delete /tmp/file1.txt")]},
        config={"configurable": {"thread_id": "test-list-delete"}},
    )

    # Deterministic VFS verification - file1 should be deleted, file2 should remain
    file1 = await vfs_read_file(db_pool, "langgraph_test", "/tmp/file1.txt")
    assert file1 is None, "file1.txt should have been deleted"

    file2 = await vfs_read_file(db_pool, "langgraph_test", "/tmp/file2.txt")
    assert file2 is not None, "file2.txt should still exist"
    assert file2 == b"test2"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_data_analysis_task(agent, db_pool, clean_files):
    """Test agent can perform a complete data analysis task."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Analyze sample sales data:
1. Create a CSV file /tmp/sales.csv with columns: product,quantity,price
2. Add 3 rows of sample data (make up realistic values)
3. Execute Python to calculate total revenue (quantity * price for each row, then sum)
4. Save the analysis to /tmp/analysis.txt""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-analysis"}},
    )

    # Deterministic VFS verification
    csv_content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/sales.csv")
    assert csv_content is not None, "CSV file was not created"
    csv_text = csv_content.decode("utf-8")
    # Should have header and data rows
    assert "product" in csv_text.lower() and "quantity" in csv_text.lower()

    analysis_content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/analysis.txt")
    assert analysis_content is not None, "Analysis file was not created"
    # Analysis should contain some numeric value (the revenue)
    assert any(char.isdigit() for char in analysis_content.decode("utf-8"))


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_error_handling(agent, db_pool, clean_files):
    """Test agent handles errors gracefully."""
    # Ask agent to read non-existent file
    await agent.ainvoke(
        {
            "messages": [
                ("user", "Read the file /tmp/this_does_not_exist.txt and tell me what it says")
            ]
        },
        config={"configurable": {"thread_id": "test-error"}},
    )

    # Deterministic verification - file should not exist
    content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/this_does_not_exist.txt")
    assert content is None, "Non-existent file should remain non-existent"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_python_with_file_output(agent, db_pool, clean_files):
    """Test agent can execute Python that creates files."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Execute Python code that:
1. Creates a list of numbers from 1 to 5
2. Writes each number to a file /tmp/numbers.txt (one per line)""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-file-output"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_test", "/tmp/numbers.txt")
    assert content is not None, "Numbers file was not created"
    text = content.decode("utf-8")
    # Should contain numbers 1-5
    for num in ["1", "2", "3", "4", "5"]:
        assert num in text, f"Expected number {num} in file content"

"""
Integration tests for LangGraph with Mayflower Sandbox tools.

These tests use real LLM calls to verify the sandbox tools work correctly
with LangGraph agents.
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
async def test_agent_file_creation(agent, clean_files):
    """Test agent can create files using write_file tool."""
    result = await agent.ainvoke(
        {
            "messages": [
                ("user", "Create a file called /tmp/hello.txt with the content 'Hello, World!'")
            ]
        },
        config={"configurable": {"thread_id": "test-file-creation"}},
    )

    # Check the agent's response
    last_message = result["messages"][-1]
    assert last_message.content is not None

    # Verify file was created by checking with list_files
    result2 = await agent.ainvoke(
        {"messages": [("user", "List all files in /tmp/")]},
        config={"configurable": {"thread_id": "test-file-creation"}},
    )

    last_message2 = result2["messages"][-1]
    response = last_message2.content.lower()
    assert "hello.txt" in response or "/tmp/hello.txt" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_python_execution(agent, clean_files):
    """Test agent can execute Python code."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Execute Python code to calculate the sum of numbers from 1 to 10 and print the result",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-python"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content

    # Should mention the sum (55)
    assert "55" in response or "fifty" in response.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_file_operations_workflow(agent, clean_files):
    """Test agent can perform complete file workflow: write, read, process."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Do the following:
1. Write a CSV file /tmp/data.csv with headers 'name,age' and two rows: 'Alice,30' and 'Bob,25'
2. Execute Python code to read the CSV and calculate the average age
3. Tell me the average age""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-workflow"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content

    # Should calculate average: (30 + 25) / 2 = 27.5
    assert "27.5" in response or "27" in response or "28" in response


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

    result = await agent.ainvoke(
        {"messages": [("user", "Read the file /tmp/secret.txt and tell me what it says")]},
        config={"configurable": {"thread_id": "test-read"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content

    assert "42" in response or "secret" in response.lower()


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
    result = await agent.ainvoke(
        {"messages": [("user", "List all files in /tmp/ and then delete /tmp/file1.txt")]},
        config={"configurable": {"thread_id": "test-list-delete"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()

    # Should mention the operation was successful
    assert "delete" in response or "removed" in response or "success" in response


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_data_analysis_task(agent, clean_files):
    """Test agent can perform a complete data analysis task."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Analyze sample sales data:
1. Create a CSV file /tmp/sales.csv with columns: product,quantity,price
2. Add 3 rows of sample data (make up realistic values)
3. Execute Python to calculate total revenue
4. Save the analysis to /tmp/analysis.txt
5. Tell me the total revenue""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-analysis"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content

    # Should mention some revenue amount
    assert any(char.isdigit() for char in response)
    assert "revenue" in response.lower() or "total" in response.lower()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_error_handling(agent, clean_files):
    """Test agent handles errors gracefully."""
    # Ask agent to read non-existent file
    result = await agent.ainvoke(
        {
            "messages": [
                ("user", "Read the file /tmp/this_does_not_exist.txt and tell me what it says")
            ]
        },
        config={"configurable": {"thread_id": "test-error"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()

    # Should mention file not found or similar
    assert (
        "not found" in response
        or "doesn't exist" in response
        or "does not exist" in response
        or "error" in response
    )


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_python_with_file_output(agent, clean_files):
    """Test agent can execute Python that creates files."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Execute Python code that:
1. Creates a list of numbers from 1 to 5
2. Writes each number to a file /tmp/numbers.txt (one per line)
3. Prints 'Done!'""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-file-output"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()

    # Should indicate success
    assert "done" in response or "success" in response or "created" in response

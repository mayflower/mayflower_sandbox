"""
Test LangGraph agent state tracking for created files.

Verifies that files created by execute_python tool are tracked in agent state.
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from langchain.agents import create_agent  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from mayflower_sandbox.agent_state import SandboxAgentState  # noqa: E402
from mayflower_sandbox.tools import create_sandbox_tools  # noqa: E402


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
            VALUES ('agent_state_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'agent_state_test'")
    yield


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_state_tracks_created_files(db_pool, clean_files):
    """Test that created files are tracked in agent state."""
    tools = create_sandbox_tools(db_pool, "agent_state_test")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    # Create agent with custom state schema
    agent = create_agent(llm, tools, checkpointer=MemorySaver(), state_schema=SandboxAgentState)

    # Create a file via execute_python
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Create a simple text file /tmp/test.txt with content 'Hello State!'",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-state-tracking"}},
    )

    # Check that created_files is in the state
    assert "created_files" in result, "created_files not found in agent state"
    assert isinstance(result["created_files"], list), "created_files should be a list"
    assert any("/tmp/test.txt" in path for path in result["created_files"]), (
        "Created file not tracked in state"
    )

    # Verify file was actually created in database
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'agent_state_test' AND file_path = '/tmp/test.txt'
        """
        )
        assert file_data is not None, "File not found in database"
        assert b"Hello State!" in file_data["content"]


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_state_tracks_multiple_files(db_pool, clean_files):
    """Test that multiple created files are all tracked in state."""
    tools = create_sandbox_tools(db_pool, "agent_state_test")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    agent = create_agent(llm, tools, checkpointer=MemorySaver(), state_schema=SandboxAgentState)

    # Create multiple files in one execution
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Create three files: /tmp/file1.txt, /tmp/file2.txt, and /tmp/file3.txt",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-multi-files"}},
    )

    # Check that all files are tracked
    assert "created_files" in result, "created_files not found in agent state"
    assert isinstance(result["created_files"], list)

    # Should have all three files
    created_paths = result["created_files"]
    assert any("/tmp/file1.txt" in path for path in created_paths)
    assert any("/tmp/file2.txt" in path for path in created_paths)
    assert any("/tmp/file3.txt" in path for path in created_paths)


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_can_reference_created_files(db_pool, clean_files):
    """Test that agent can reference files from state in subsequent actions."""
    tools = create_sandbox_tools(db_pool, "agent_state_test")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    agent = create_agent(llm, tools, checkpointer=MemorySaver(), state_schema=SandboxAgentState)

    config = {"configurable": {"thread_id": "test-file-reference"}}

    # First: create a file
    result1 = await agent.ainvoke(
        {"messages": [("user", "Create a file /tmp/data.txt with content 'important data'")]},
        config=config,
    )

    assert "created_files" in result1
    assert any("/tmp/data.txt" in path for path in result1["created_files"])

    # Second: ask agent to read the file it created
    result2 = await agent.ainvoke(
        {"messages": [("user", "Read the file you just created")]}, config=config
    )

    # Agent should be able to access the file
    last_message = result2["messages"][-1]
    assert "important data" in last_message.content.lower()

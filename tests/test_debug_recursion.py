"""
Debug test to understand what packages are available in Pyodide.
"""

import pytest
import asyncpg
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

load_dotenv()

from mayflower_sandbox.tools import create_sandbox_tools
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver


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
            VALUES ('debug_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'debug_test'")
    yield


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_check_available_data_packages(db_pool, clean_files):
    """Test what data processing packages are available."""
    tools = create_sandbox_tools(db_pool, thread_id="debug_test")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    agent = create_react_agent(llm, tools, checkpointer=MemorySaver())

    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Check which of these packages can be imported:
- pandas
- numpy
- matplotlib
- json
- csv
- sqlite3

Print SUCCESS for each one that works, or FAIL with error for those that don't.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "debug-packages"}},
    )

    last_message = result["messages"][-1]
    print(f"\n\nPackage availability:\n{last_message.content}\n\n")


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_micropip_install(db_pool, clean_files):
    """Test if micropip can install packages."""
    tools = create_sandbox_tools(db_pool, thread_id="debug_test")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    agent = create_react_agent(llm, tools, checkpointer=MemorySaver())

    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Try to use micropip to list available packages.
Run: import micropip; print(micropip.list())""",
                )
            ]
        },
        config={"configurable": {"thread_id": "debug-micropip"}},
    )

    last_message = result["messages"][-1]
    print(f"\n\nMicropip result:\n{last_message.content}\n\n")

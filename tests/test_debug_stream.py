"""
Debug test with streaming to see what the agent is actually doing.
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
            VALUES ('debug_stream', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'debug_stream'")
    yield


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_stream_csv_processing(db_pool, clean_files):
    """Stream the agent output to see what's happening."""
    tools = create_sandbox_tools(db_pool, thread_id="debug_stream")
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    agent = create_react_agent(llm, tools, checkpointer=MemorySaver())

    print("\n" + "=" * 80)
    print("STARTING CSV PROCESSING TEST")
    print("=" * 80)

    step_count = 0
    async for chunk in agent.astream(
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
        config={"configurable": {"thread_id": "test-csv-stream"}},
        stream_mode="updates",
    ):
        step_count += 1
        print(f"\n--- STEP {step_count} ---")
        print(chunk)

        if step_count >= 15:
            print("\n!!! STOPPING AT STEP 15 TO PREVENT TIMEOUT !!!")
            break

    print("\n" + "=" * 80)
    print(f"FINISHED AFTER {step_count} STEPS")
    print("=" * 80)

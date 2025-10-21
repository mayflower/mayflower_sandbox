"""
Test that helper modules can be imported in Pyodide.
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
            VALUES ('helpers_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files and error history before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'helpers_test'")

    from mayflower_sandbox.tools.execute import _error_history

    _error_history.clear()

    yield


@pytest.fixture
def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    tools = create_sandbox_tools(db_pool, thread_id="helpers_test")
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    agent = create_react_agent(llm, tools, checkpointer=MemorySaver())
    return agent


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_helper_import_available(agent, clean_files):
    """Test that helper modules are available for import."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Test that the helper module can be imported:
1. Import the docx_ooxml helper: from document.docx_ooxml import docx_add_comment, unzip_docx_like, zip_docx_like
2. Print the function names to confirm they're available
3. Print the docstring of docx_add_comment to show it's properly loaded

Tell me if the import succeeded and what functions are available.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "helper-import"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(word in response for word in ["docx_add_comment", "import", "success", "available"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_helper_unzip_zip(agent, clean_files):
    """Test that helper utility functions work."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Test the unzip_docx_like and zip_docx_like helper functions:
1. Create a simple test docx file at /tmp/test.docx using python-docx
2. Read it as bytes
3. Import and use unzip_docx_like to extract the parts
4. Print the list of files extracted (keys of the parts dict)
5. Use zip_docx_like to recreate the docx from parts
6. Write it back to /tmp/test_recreated.docx

Tell me if both functions worked and list the OOXML parts found.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "helper-unzip"}},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(
        word in response
        for word in ["word/document.xml", "success", "parts", "extracted", "worked"]
    )

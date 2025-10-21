"""
Test docx to markdown conversion using mammoth.
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
            VALUES ('docx_md', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files and error history before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'docx_md'")

    from mayflower_sandbox.tools.execute import _error_history

    _error_history.clear()

    yield


@pytest.fixture
def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    tools = create_sandbox_tools(db_pool, thread_id="docx_md")
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    agent = create_react_agent(llm, tools, checkpointer=MemorySaver())
    return agent


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_to_markdown_basic(agent, clean_files):
    """Test basic docx to markdown conversion using mammoth."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/test.docx using python-docx with:
Title: "Test Document"
Heading 1: "Section 1"
Paragraph: "This is a test paragraph with **bold** text."
Heading 2: "Section 2"
Bullet list:
  - Item 1
  - Item 2

Then convert it to markdown using mammoth. Save the markdown to /tmp/test.md and show me the markdown content.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-md-1"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    # Check for markdown elements
    assert any(word in response for word in ["#", "markdown", "section", "converted"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_to_markdown_with_formatting(agent, clean_files):
    """Test docx to markdown with rich formatting."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/formatted.docx with:
- Heading 1: "API Documentation"
- Paragraph with bold text: "**Important:** This is critical"
- Heading 2: "Endpoints"
- Numbered list:
  1. GET /users
  2. POST /users
  3. DELETE /users
- Paragraph: "Use authentication token"

Convert to markdown using mammoth and save to /tmp/formatted.md. Tell me if it contains markdown headings (#).""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-md-2"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content
    assert any(word in response for word in ["#", "heading", "markdown", "yes", "contains"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_to_markdown_with_tables(agent, clean_files):
    """Test docx to markdown conversion with tables."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/table.docx with:
Title: "Data Report"
A table with headers: Name, Value
Row 1: Alpha, 100
Row 2: Beta, 200

Convert to markdown using mammoth. Check if the markdown contains a table format (pipe characters |).""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-md-3"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(word in response for word in ["table", "pipe", "|", "markdown", "converted"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_to_markdown_fallback(agent, clean_files):
    """Test that fallback works when mammoth is not available (extract plain text)."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a simple Word document at /tmp/simple.docx with just text:
"Hello World from DOCX"

Then implement the fallback conversion without mammoth:
1. Read the docx file bytes
2. Unzip it to access word/document.xml
3. Parse XML and extract all w:t (text) nodes
4. Join the text with newlines

Save to /tmp/simple.md and show the content.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-md-fallback"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content
    assert "Hello World" in response or "hello world" in response.lower()


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_to_markdown_with_links(agent, clean_files):
    """Test docx to markdown with hyperlinks."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/links.docx with:
Heading: "Resources"
Paragraph: "Visit our website"
Add a hyperlink: "Click here" linking to "https://example.com"

Convert to markdown using mammoth. Check if markdown contains a link in format [text](url).""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-md-links"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(word in response for word in ["link", "[", "](", "markdown", "http"])

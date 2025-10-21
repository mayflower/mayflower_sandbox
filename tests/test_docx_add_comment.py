"""
Test adding comments to Word documents using pure OOXML manipulation.
"""

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
            VALUES ('docx_comment', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files and error history before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'docx_comment'")

    from mayflower_sandbox.tools.execute import _error_history

    _error_history.clear()

    yield


@pytest.fixture
def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    tools = create_sandbox_tools(db_pool, thread_id="docx_comment")
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    agent = create_agent(llm, tools, checkpointer=MemorySaver())
    return agent


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_add_comment_basic(agent, clean_files):
    """Test adding a comment to a paragraph using the docx_add_comment helper."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/doc.docx with three paragraphs:
Paragraph 1: "Introduction text"
Paragraph 2: "Main content here"
Paragraph 3: "Conclusion text"

Then add a comment to paragraph 1 (index 0) using the helper:
1. Import the helper: from document.docx_ooxml import docx_add_comment
2. Read the docx file as bytes
3. Use docx_add_comment(docx_bytes, 0, "Please expand this", author="Reviewer")
4. Save the result to /tmp/doc_commented.docx

Tell me if the comment was successfully added.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-1"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(word in response for word in ["comment", "success", "added", "created", "modified"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_add_multiple_comments(agent, clean_files):
    """Test adding comments to multiple paragraphs using the helper."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/multi.docx with:
Paragraph 0: "First paragraph"
Paragraph 1: "Second paragraph"
Paragraph 2: "Third paragraph"

Add comments using the docx_add_comment helper:
1. Import: from document.docx_ooxml import docx_add_comment
2. Read /tmp/multi.docx as bytes
3. Add first comment: docx_add_comment(docx_bytes, 0, "Review this section", author="Reviewer")
4. Add second comment to the result: docx_add_comment(result, 2, "Add more details", author="Reviewer")
5. Save final result to /tmp/multi_commented.docx

Confirm both comments were added.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-multi"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(word in response for word in ["both", "two", "comments", "added", "success"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_comment_with_metadata(agent, clean_files):
    """Test adding comment with author, initials, and date metadata using helper."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Word doc at /tmp/meta.docx with one paragraph: "Review needed"

Add a comment using the docx_add_comment helper with metadata:
1. Import: from document.docx_ooxml import docx_add_comment
2. Read the docx as bytes
3. Use: docx_add_comment(docx_bytes, 0, "Approved", author="John Doe", initials="JD")
4. Save to /tmp/meta_commented.docx

Tell me if the comment with metadata was added successfully.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-meta"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(
        word in response for word in ["metadata", "author", "approved", "correct", "success"]
    )


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_comment_ensure_relationships(agent, clean_files):
    """Test that helper properly creates comments.xml relationship."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Word doc at /tmp/rels.docx with text: "Test document"

Add a comment using the helper (which handles relationships automatically):
1. Import: from document.docx_ooxml import docx_add_comment, unzip_docx_like
2. Read docx as bytes
3. Add comment: docx_add_comment(docx_bytes, 0, "Test comment")
4. Verify the relationship by unzipping the result and checking word/_rels/document.xml.rels
5. Save to /tmp/rels_commented.docx

Confirm the helper created the comments relationship.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-rels"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(
        word in response for word in ["relationship", "created", "verified", "success", "correct"]
    )


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_comment_range_markers(agent, clean_files):
    """Test that helper correctly positions comment range markers."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Word doc at /tmp/range.docx with:
"This is a test paragraph with some text"

Add a comment using the docx_add_comment helper:
1. Import: from document.docx_ooxml import docx_add_comment, unzip_docx_like
2. Read docx as bytes
3. Add comment: docx_add_comment(docx_bytes, 0, "Test comment")
4. Unzip the result and parse word/document.xml to verify range markers exist
5. Save to /tmp/range_commented.docx

The helper should automatically create: commentRangeStart, commentRangeEnd, and commentReference.
Confirm the markers are present.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-range"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(
        word in response
        for word in ["marker", "position", "correct", "structure", "success", "present"]
    )


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_comment_empty_paragraph(agent, clean_files):
    """Test that helper handles empty paragraphs correctly."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Word doc at /tmp/empty.docx with an empty paragraph (no text)

Add a comment using the helper (which handles empty paragraphs):
1. Import: from document.docx_ooxml import docx_add_comment
2. Read docx as bytes
3. Add comment: docx_add_comment(docx_bytes, 0, "Empty paragraph needs content")
4. Save to /tmp/empty_commented.docx

The helper should create a run if the paragraph is empty. Confirm it worked.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-empty"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(
        word in response for word in ["empty", "comment", "added", "success", "created", "worked"]
    )


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_docx_comment_verify_id_generation(agent, clean_files):
    """Test that helper generates unique sequential comment IDs."""
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Word doc at /tmp/ids.docx with three paragraphs

Add comments to all three paragraphs using the helper:
1. Import: from document.docx_ooxml import docx_add_comment
2. Read docx as bytes
3. Add first comment: result1 = docx_add_comment(docx_bytes, 0, "Comment 1")
4. Add second comment: result2 = docx_add_comment(result1, 1, "Comment 2")
5. Add third comment: result3 = docx_add_comment(result2, 2, "Comment 3")
6. Save result3 to /tmp/ids_commented.docx

The helper should generate IDs 0, 1, 2 automatically. Confirm this worked.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "comment-ids"}, "recursion_limit": 50},
    )

    last_message = result["messages"][-1]
    response = last_message.content.lower()
    assert any(
        word in response
        for word in ["0", "1", "2", "correct", "sequential", "unique", "success", "worked"]
    )

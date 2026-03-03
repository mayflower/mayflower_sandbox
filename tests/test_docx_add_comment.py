"""
Test adding comments to Word documents using pure OOXML manipulation.
Uses MayflowerSandboxBackend to execute Python code in the sandbox.
"""

import os

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv()

from mayflower_sandbox.deepagents_backend import MayflowerSandboxBackend


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "mayflower_test"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )

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
async def backend(db_pool):
    """Create MayflowerSandboxBackend instance."""
    return MayflowerSandboxBackend(db_pool, thread_id="docx_comment")


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'docx_comment'")
    yield


@pytest.mark.asyncio
async def test_docx_add_comment_basic(backend, clean_files):
    """Test adding a comment to a paragraph using the docx_add_comment helper."""
    # Create a Word document with paragraphs
    create_doc_code = """
from document.docx_ooxml import create_docx_bytes

paragraphs = ["Introduction text", "Main content here", "Conclusion text"]
docx_bytes = create_docx_bytes(paragraphs)

with open("/tmp/doc.docx", "wb") as f:
    f.write(docx_bytes)

print("Document created with", len(paragraphs), "paragraphs")
"""

    # Upload and execute the script
    await backend.aupload_files([("/tmp/create_doc.py", create_doc_code.encode())])
    result = await backend.aexecute("python /tmp/create_doc.py")
    assert result.exit_code == 0, f"Failed to create doc: {result.output}"
    assert "3 paragraphs" in result.output

    # Add comment using the helper
    add_comment_code = """
from document.docx_ooxml import docx_add_comment

with open("/tmp/doc.docx", "rb") as f:
    docx_bytes = f.read()

result_bytes = docx_add_comment(docx_bytes, 0, "Please expand this", author="Reviewer")

with open("/tmp/doc_commented.docx", "wb") as f:
    f.write(result_bytes)

print("Comment added successfully")
"""

    await backend.aupload_files([("/tmp/add_comment.py", add_comment_code.encode())])
    result = await backend.aexecute("python /tmp/add_comment.py")
    assert result.exit_code == 0, f"Failed to add comment: {result.output}"
    assert "successfully" in result.output.lower()


@pytest.mark.asyncio
async def test_docx_add_multiple_comments(backend, clean_files):
    """Test adding comments to multiple paragraphs using the helper."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_add_comment

# Create document
paragraphs = ["First paragraph", "Second paragraph", "Third paragraph"]
docx_bytes = create_docx_bytes(paragraphs)

# Add first comment
result1 = docx_add_comment(docx_bytes, 0, "Review this section", author="Reviewer")

# Add second comment
result2 = docx_add_comment(result1, 2, "Add more details", author="Reviewer")

with open("/tmp/multi_commented.docx", "wb") as f:
    f.write(result2)

print("Both comments added successfully")
"""

    await backend.aupload_files([("/tmp/multi_comment.py", code.encode())])
    result = await backend.aexecute("python /tmp/multi_comment.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "both" in result.output.lower()


@pytest.mark.asyncio
async def test_docx_comment_with_metadata(backend, clean_files):
    """Test adding comment with author, initials, and date metadata."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_add_comment

docx_bytes = create_docx_bytes(["Review needed"])
result = docx_add_comment(docx_bytes, 0, "Approved", author="John Doe", initials="JD")

with open("/tmp/meta_commented.docx", "wb") as f:
    f.write(result)

print("Comment with metadata added successfully")
"""

    await backend.aupload_files([("/tmp/meta_comment.py", code.encode())])
    result = await backend.aexecute("python /tmp/meta_comment.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "metadata" in result.output.lower() or "successfully" in result.output.lower()


@pytest.mark.asyncio
async def test_docx_comment_verify_structure(backend, clean_files):
    """Test that helper correctly creates comment structure in document."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_add_comment, unzip_docx_like

docx_bytes = create_docx_bytes(["This is a test paragraph with some text"])
result = docx_add_comment(docx_bytes, 0, "Test comment")

# Verify the structure
files = unzip_docx_like(result)

# Check comments.xml exists
assert "word/comments.xml" in files, "comments.xml not created"

# Check document.xml has comment markers
doc_xml = files["word/document.xml"]
assert b"commentRangeStart" in doc_xml, "Missing commentRangeStart"
assert b"commentRangeEnd" in doc_xml, "Missing commentRangeEnd"
assert b"commentReference" in doc_xml, "Missing commentReference"

with open("/tmp/structure_verified.docx", "wb") as f:
    f.write(result)

print("Comment structure verified successfully")
"""

    await backend.aupload_files([("/tmp/verify_structure.py", code.encode())])
    result = await backend.aexecute("python /tmp/verify_structure.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "verified" in result.output.lower() or "successfully" in result.output.lower()


@pytest.mark.asyncio
async def test_docx_comment_sequential_ids(backend, clean_files):
    """Test that helper generates unique sequential comment IDs."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_add_comment, unzip_docx_like

docx_bytes = create_docx_bytes(["Para 1", "Para 2", "Para 3"])

# Add three comments
result1 = docx_add_comment(docx_bytes, 0, "Comment 1")
result2 = docx_add_comment(result1, 1, "Comment 2")
result3 = docx_add_comment(result2, 2, "Comment 3")

# Verify IDs
files = unzip_docx_like(result3)
comments_xml = files["word/comments.xml"].decode("utf-8")

assert 'w:id="0"' in comments_xml, "Missing comment ID 0"
assert 'w:id="1"' in comments_xml, "Missing comment ID 1"
assert 'w:id="2"' in comments_xml, "Missing comment ID 2"

with open("/tmp/ids_commented.docx", "wb") as f:
    f.write(result3)

print("Sequential IDs 0, 1, 2 verified successfully")
"""

    await backend.aupload_files([("/tmp/verify_ids.py", code.encode())])
    result = await backend.aexecute("python /tmp/verify_ids.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "0, 1, 2" in result.output or "successfully" in result.output.lower()

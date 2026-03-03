"""
Test docx to markdown conversion.
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
            VALUES ('docx_md', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def backend(db_pool):
    """Create MayflowerSandboxBackend instance."""
    return MayflowerSandboxBackend(db_pool, thread_id="docx_md")


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'docx_md'")
    yield


@pytest.mark.asyncio
async def test_docx_to_markdown_basic(backend, clean_files):
    """Test basic docx to markdown conversion."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_to_markdown

# Create a document with headings and paragraphs
paragraphs = [
    "Test Document",
    "Section 1",
    "This is a test paragraph.",
    "Section 2",
    "Another paragraph here."
]
docx_bytes = create_docx_bytes(paragraphs)

# Convert to markdown
markdown = docx_to_markdown(docx_bytes)

with open("/tmp/test.md", "w") as f:
    f.write(markdown)

print("Markdown content:")
print(markdown)
"""

    await backend.aupload_files([("/tmp/convert.py", code.encode())])
    result = await backend.aexecute("python /tmp/convert.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "Test Document" in result.output or "Section" in result.output


@pytest.mark.asyncio
async def test_docx_to_markdown_preserves_text(backend, clean_files):
    """Test that markdown conversion preserves text content."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_to_markdown

paragraphs = [
    "Important Header",
    "This paragraph contains important information.",
    "Another paragraph with more details."
]
docx_bytes = create_docx_bytes(paragraphs)

markdown = docx_to_markdown(docx_bytes)

# Verify all text is present
assert "Important Header" in markdown, "Header missing"
assert "important information" in markdown, "Content missing"
assert "more details" in markdown, "Details missing"

print("All text preserved in markdown conversion")
print(markdown)
"""

    await backend.aupload_files([("/tmp/preserve.py", code.encode())])
    result = await backend.aexecute("python /tmp/preserve.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "preserved" in result.output.lower()


@pytest.mark.asyncio
async def test_docx_extract_text_fallback(backend, clean_files):
    """Test plain text extraction from docx."""
    code = """
from document.docx_ooxml import create_docx_bytes, unzip_docx_like
import re

paragraphs = ["Hello World from DOCX", "Second paragraph here"]
docx_bytes = create_docx_bytes(paragraphs)

# Extract text using XML parsing
files = unzip_docx_like(docx_bytes)
doc_xml = files["word/document.xml"].decode("utf-8")

# Extract text from w:t elements
text_pattern = r'<w:t[^>]*>([^<]*)</w:t>'
matches = re.findall(text_pattern, doc_xml)
extracted_text = " ".join(matches)

print("Extracted text:", extracted_text)
assert "Hello World" in extracted_text, "Text extraction failed"
print("Text extraction successful")
"""

    await backend.aupload_files([("/tmp/extract.py", code.encode())])
    result = await backend.aexecute("python /tmp/extract.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "Hello World" in result.output


@pytest.mark.asyncio
async def test_docx_roundtrip(backend, clean_files):
    """Test creating docx and converting back to text."""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_to_markdown

original_paragraphs = [
    "Document Title",
    "First section content.",
    "Second section content."
]

# Create docx
docx_bytes = create_docx_bytes(original_paragraphs)

# Convert back to markdown
markdown = docx_to_markdown(docx_bytes)

# Verify roundtrip
for para in original_paragraphs:
    assert para in markdown, f"Lost paragraph: {para}"

print("Roundtrip successful - all content preserved")
"""

    await backend.aupload_files([("/tmp/roundtrip.py", code.encode())])
    result = await backend.aexecute("python /tmp/roundtrip.py")
    assert result.exit_code == 0, f"Failed: {result.output}"
    assert "successful" in result.output.lower()

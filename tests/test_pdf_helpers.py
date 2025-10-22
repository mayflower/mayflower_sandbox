"""
Direct tests for PDF helper functions.
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.sandbox_executor import SandboxExecutor  # noqa: E402


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
            VALUES ('pdf_helpers_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


async def test_pdf_import(db_pool):
    """Test that PDF helpers can be imported."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=60.0
    )

    code = """
import micropip
await micropip.install('pypdf')

from document.pdf_manipulation import (
    pdf_num_pages,
    pdf_merge,
    pdf_split,
    pdf_rotate,
    pdf_extract_text,
    pdf_extract_text_by_page,
    pdf_get_metadata
)

print("✓ All PDF helpers imported successfully")
"""

    result = await executor.execute(code)

    assert result.success, f"Import failed: {result.stderr}"
    assert "All PDF helpers imported successfully" in result.stdout


async def test_pdf_merge(db_pool):
    """Test pdf_merge function."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=90.0
    )

    code = """
import micropip
import io

await micropip.install('pypdf')
await micropip.install('fpdf2')

# Create two simple PDFs
from fpdf import FPDF

pdf1 = FPDF()
pdf1.add_page()
pdf1.set_font("Arial", size=12)
pdf1.cell(200, 10, txt="PDF 1", ln=True)
pdf1_bytes = pdf1.output()

pdf2 = FPDF()
pdf2.add_page()
pdf2.set_font("Arial", size=12)
pdf2.cell(200, 10, txt="PDF 2", ln=True)
pdf2_bytes = pdf2.output()

# Test merge
from document.pdf_manipulation import pdf_merge, pdf_num_pages

merged = pdf_merge([pdf1_bytes, pdf2_bytes])

num_pages = pdf_num_pages(merged)
print(f"Merged PDF has {num_pages} pages")

assert num_pages == 2, f"Expected 2 pages, got {num_pages}"

print("✓ pdf_merge works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pdf_merge works" in result.stdout


async def test_pdf_split(db_pool):
    """Test pdf_split function."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=90.0
    )

    code = """
import micropip
await micropip.install('pypdf')
await micropip.install('fpdf2')

from fpdf import FPDF

# Create a 3-page PDF
pdf = FPDF()
for i in range(3):
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Page {i+1}", ln=True)

pdf_bytes = pdf.output()

# Test split
from document.pdf_manipulation import pdf_split, pdf_num_pages

pages = pdf_split(pdf_bytes)

print(f"Split into {len(pages)} pages")

assert len(pages) == 3, f"Expected 3 pages, got {len(pages)}"

# Verify each page is a valid single-page PDF
for i, page_pdf in enumerate(pages):
    num_pages = pdf_num_pages(page_pdf)
    assert num_pages == 1, f"Page {i} should have 1 page, got {num_pages}"

print("✓ pdf_split works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pdf_split works" in result.stdout


async def test_pdf_extract_text(db_pool):
    """Test pdf_extract_text function."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=90.0
    )

    code = """
import micropip
await micropip.install('pypdf')
await micropip.install('fpdf2')

from fpdf import FPDF

# Create a PDF with text
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.cell(200, 10, txt="Hello World from PDF", ln=True)
pdf.cell(200, 10, txt="Second line of text", ln=True)

pdf_bytes = pdf.output()

# Test text extraction
from document.pdf_manipulation import pdf_extract_text

text = pdf_extract_text(pdf_bytes)

print(f"Extracted text: {text}")

assert "Hello World from PDF" in text
assert "Second line of text" in text

print("✓ pdf_extract_text works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pdf_extract_text works" in result.stdout


async def test_pdf_create_simple(db_pool):
    """Test pdf_create_simple function (ASCII only)."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=90.0
    )

    code = """
import micropip
await micropip.install('fpdf2')

from document.pdf_creation import pdf_create_simple

# Create a simple PDF with ASCII content
paragraphs = [
    "This is the first paragraph.",
    "This is the second paragraph with some data.",
    "Final paragraph of the document."
]

path = pdf_create_simple(
    title="Test Report",
    content_paragraphs=paragraphs,
    output_path="/tmp/simple_test.pdf"
)

# Verify file exists and is not empty
import os
assert os.path.exists(path), "PDF file was not created"
file_size = os.path.getsize(path)
assert file_size > 0, "PDF file is empty"

# Print success message
print(f"PDF created successfully at {path} ({file_size} bytes)")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert result.created_files is not None
    assert "/tmp/simple_test.pdf" in result.created_files


async def test_pdf_create_with_unicode(db_pool):
    """Test pdf_create_with_unicode function with special characters."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=120.0
    )

    code = """
import micropip
await micropip.install('fpdf2')

from document.pdf_creation import pdf_create_with_unicode

# Create a PDF with Unicode characters
paragraphs = [
    "Temperature: 180°C (π radians)",
    "Measurement: 5.2µm ± 0.1µm",
    "Cost: €125.50 (≈ $135)",
    "Greek letters: α β γ δ θ λ σ Ω"
]

path = await pdf_create_with_unicode(
    title="Lab Report with Unicode",
    content_paragraphs=paragraphs,
    output_path="/tmp/unicode_test.pdf"
)

# Verify file exists and has reasonable size
import os
assert os.path.exists(path), "PDF file was not created"
file_size = os.path.getsize(path)
assert file_size > 1000, f"PDF file too small: {file_size} bytes"

print(f"Unicode PDF created successfully ({file_size} bytes)")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert result.created_files is not None
    assert "/tmp/unicode_test.pdf" in result.created_files


async def test_pdf_create_simple_with_replacements(db_pool):
    """Test pdf_create_simple with ASCII replacements for Unicode characters."""
    executor = SandboxExecutor(
        db_pool, "pdf_helpers_test", allow_net=True, stateful=True, timeout_seconds=90.0
    )

    code = """
import micropip
await micropip.install('fpdf2')

from document.pdf_creation import pdf_create_simple, COMMON_UNICODE_REPLACEMENTS

# Create a PDF with Unicode characters replaced with ASCII
paragraphs = [
    "Temperature: 180°C (π radians)",
    "Measurement: 5.2µm ± 0.1µm",
]

path = pdf_create_simple(
    title="Report with ASCII replacements",
    content_paragraphs=paragraphs,
    output_path="/tmp/ascii_replaced.pdf",
    ascii_replacements=COMMON_UNICODE_REPLACEMENTS
)

# Verify file exists
import os
assert os.path.exists(path), "PDF file was not created"
file_size = os.path.getsize(path)
assert file_size > 0, "PDF file is empty"

print(f"PDF with ASCII replacements created ({file_size} bytes)")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert result.created_files is not None
    assert "/tmp/ascii_replaced.pdf" in result.created_files

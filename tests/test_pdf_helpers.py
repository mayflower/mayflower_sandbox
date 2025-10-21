"""
Direct tests for PDF helper functions.
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

load_dotenv()

from mayflower_sandbox.sandbox_executor import SandboxExecutor


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

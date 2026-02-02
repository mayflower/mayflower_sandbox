"""
Comprehensive document processing tests matching maistack skills.
Uses MayflowerSandboxBackend to execute Python code in the sandbox.

Tests all operations from:
- excel_skill.py
- pdf_skill.py
- powerpoint_skill.py
- word_skill.py

Test Strategy:
- All assertions use deterministic VFS verification
- Execute Python scripts directly via backend.execute("python script.py")
- Verify file existence and format directly in database
"""

import os

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv()

from mayflower_sandbox.deepagents_backend import MayflowerSandboxBackend


async def vfs_read_file(db_pool, thread_id: str, path: str) -> bytes | None:
    """Read file content from VFS. Returns None if file doesn't exist."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = $1 AND file_path = $2
            """,
            thread_id,
            path,
        )
        return row["content"] if row else None


async def vfs_file_exists(db_pool, thread_id: str, path: str) -> bool:
    """Check if file exists in VFS."""
    return await vfs_read_file(db_pool, thread_id, path) is not None


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
            VALUES ('doc_skills', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def backend(db_pool):
    """Create MayflowerSandboxBackend instance."""
    return MayflowerSandboxBackend(db_pool, thread_id="doc_skills", allow_net=True)


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'doc_skills'")
    yield


# ============================================================================
# EXCEL TESTS (matching excel_skill.py)
# ============================================================================


@pytest.mark.asyncio
async def test_excel_create_workbook(backend, db_pool, clean_files):
    """Test: ExcelSkill.create_workbook()"""
    code = """
from document.xlsx import create_xlsx_bytes

# Create workbook with data
headers = ["Product", "Quantity", "Price", "Total"]
data = [
    ["Laptop", 5, 1200, "=B2*C2"],
    ["Mouse", 25, 30, "=B3*C3"],
]

xlsx_bytes = create_xlsx_bytes(headers, data)

with open("/tmp/sales.xlsx", "wb") as f:
    f.write(xlsx_bytes)

print("Excel file created successfully")
"""

    await backend.aupload_files([("/tmp/create_excel.py", code.encode())])
    result = await backend.aexecute("python /tmp/create_excel.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Deterministic VFS verification - Excel files are ZIP-based (PK magic bytes)
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/sales.xlsx")
    assert content is not None, "Excel file was not created"
    assert len(content) > 100, "Excel file seems too small"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


@pytest.mark.asyncio
async def test_excel_read_workbook(backend, db_pool, clean_files):
    """Test: ExcelSkill.read_workbook()"""
    code = """
from document.xlsx import create_xlsx_bytes, xlsx_to_dict

# Create workbook
headers = ["Name", "Score"]
data = [["Alice", 95], ["Bob", 87]]
xlsx_bytes = create_xlsx_bytes(headers, data)

with open("/tmp/data.xlsx", "wb") as f:
    f.write(xlsx_bytes)

# Read it back
with open("/tmp/data.xlsx", "rb") as f:
    content = f.read()

result = xlsx_to_dict(content)
print(f"Read {len(result)} rows")
print(f"Alice's score: {result[0]['Score']}")
"""

    await backend.aupload_files([("/tmp/read_excel.py", code.encode())])
    result = await backend.aexecute("python /tmp/read_excel.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/data.xlsx")
    assert content is not None, "Excel file was not created"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


# ============================================================================
# PDF TESTS (matching pdf_skill.py)
# ============================================================================


@pytest.mark.asyncio
async def test_pdf_create(backend, db_pool, clean_files):
    """Test: PDFCreator.create_pdf()"""
    code = """
from document.pdf import create_pdf_bytes

sections = [
    ("Q4 Financial Report", ""),
    ("Executive Summary", "Revenue increased 25%"),
    ("Key Metrics", "Customer growth at 40%"),
]

pdf_bytes = create_pdf_bytes(sections)

with open("/tmp/report.pdf", "wb") as f:
    f.write(pdf_bytes)

print("PDF created successfully")
"""

    await backend.aupload_files([("/tmp/create_pdf.py", code.encode())])
    result = await backend.aexecute("python /tmp/create_pdf.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Deterministic VFS verification - PDF files start with %PDF
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/report.pdf")
    assert content is not None, "PDF file was not created"
    assert content[:4] == b"%PDF", "PDF file should start with %PDF header"


@pytest.mark.asyncio
async def test_pdf_merge(backend, db_pool, clean_files):
    """Test: PDFSkill.merge_pdfs()"""
    code = """
from document.pdf import create_pdf_bytes, merge_pdfs

# Create two PDFs
pdf1 = create_pdf_bytes([("Page 1", "Content for page 1")])
pdf2 = create_pdf_bytes([("Page 2", "Content for page 2")])

with open("/tmp/doc1.pdf", "wb") as f:
    f.write(pdf1)
with open("/tmp/doc2.pdf", "wb") as f:
    f.write(pdf2)

# Merge them
with open("/tmp/doc1.pdf", "rb") as f1, open("/tmp/doc2.pdf", "rb") as f2:
    merged = merge_pdfs([f1.read(), f2.read()])

with open("/tmp/merged.pdf", "wb") as f:
    f.write(merged)

print("PDFs merged successfully")
"""

    await backend.aupload_files([("/tmp/merge_pdf.py", code.encode())])
    result = await backend.aexecute("python /tmp/merge_pdf.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Deterministic VFS verification - all three PDFs should exist
    for path in ["/tmp/doc1.pdf", "/tmp/doc2.pdf", "/tmp/merged.pdf"]:
        content = await vfs_read_file(db_pool, "doc_skills", path)
        assert content is not None, f"{path} was not created"
        assert content[:4] == b"%PDF", f"{path} should be a valid PDF"


# ============================================================================
# POWERPOINT TESTS (matching powerpoint_skill.py)
# ============================================================================


@pytest.mark.asyncio
async def test_powerpoint_create(backend, db_pool, clean_files):
    """Test: PowerPointSkill.create_simple_presentation()"""
    code = """
from document.pptx import create_pptx_bytes

slides = [
    {"title": "Company Overview 2024", "content": ""},
    {"title": "Mission", "content": "Innovate, Lead, Grow"},
    {"title": "Metrics", "content": "40% growth"},
]

pptx_bytes = create_pptx_bytes(slides)

with open("/tmp/pitch.pptx", "wb") as f:
    f.write(pptx_bytes)

print("PowerPoint created successfully")
"""

    await backend.aupload_files([("/tmp/create_pptx.py", code.encode())])
    result = await backend.aexecute("python /tmp/create_pptx.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Deterministic VFS verification - PPTX files are ZIP-based (PK magic bytes)
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/pitch.pptx")
    assert content is not None, "PowerPoint file was not created"
    assert content[:2] == b"PK", "PowerPoint file should be ZIP-based (OOXML)"


@pytest.mark.asyncio
async def test_powerpoint_extract_text(backend, db_pool, clean_files):
    """Test: PowerPointSkill.extract_text()"""
    code = """
from document.pptx import create_pptx_bytes, pptx_extract_text

slides = [{"title": "Company Code: XYZ789", "content": "Important info"}]
pptx_bytes = create_pptx_bytes(slides)

with open("/tmp/info.pptx", "wb") as f:
    f.write(pptx_bytes)

# Extract text
with open("/tmp/info.pptx", "rb") as f:
    text = pptx_extract_text(f.read())

with open("/tmp/extracted.txt", "w") as f:
    f.write(text)

print("Text extracted:", text)
"""

    await backend.aupload_files([("/tmp/extract_pptx.py", code.encode())])
    result = await backend.aexecute("python /tmp/extract_pptx.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Verify PowerPoint was created with correct format
    pptx_content = await vfs_read_file(db_pool, "doc_skills", "/tmp/info.pptx")
    assert pptx_content is not None, "PowerPoint file was not created"
    assert pptx_content[:2] == b"PK", "PowerPoint file should be ZIP-based (OOXML)"

    # Verify extraction worked
    extracted = await vfs_read_file(db_pool, "doc_skills", "/tmp/extracted.txt")
    assert extracted is not None, "Extracted text file was not created"
    assert b"XYZ789" in extracted, f"Expected company code. Got: {extracted.decode()}"


# ============================================================================
# WORD TESTS (matching word_skill.py)
# ============================================================================


@pytest.mark.asyncio
async def test_word_create_document(backend, db_pool, clean_files):
    """Test: WordSkill.create_document()"""
    code = """
from document.docx_ooxml import create_docx_bytes

paragraphs = [
    "Annual Report 2024",
    "Overview - Business performance summary",
    "Achievements:",
    "Revenue growth 35%",
    "Customer satisfaction 95%",
]

docx_bytes = create_docx_bytes(paragraphs)

with open("/tmp/report.docx", "wb") as f:
    f.write(docx_bytes)

print("Word document created successfully")
"""

    await backend.aupload_files([("/tmp/create_docx.py", code.encode())])
    result = await backend.aexecute("python /tmp/create_docx.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Deterministic VFS verification - DOCX files are ZIP-based (PK magic bytes)
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/report.docx")
    assert content is not None, "Word document was not created"
    assert content[:2] == b"PK", "Word document should be ZIP-based (OOXML)"


@pytest.mark.asyncio
async def test_word_extract_text(backend, db_pool, clean_files):
    """Test: WordSkill.extract_text()"""
    code = """
from document.docx_ooxml import create_docx_bytes, docx_to_markdown

paragraphs = ["Access Code: BETA456", "Additional info here"]
docx_bytes = create_docx_bytes(paragraphs)

with open("/tmp/secret.docx", "wb") as f:
    f.write(docx_bytes)

# Extract text
with open("/tmp/secret.docx", "rb") as f:
    text = docx_to_markdown(f.read())

with open("/tmp/extracted.txt", "w") as f:
    f.write(text)

print("Text extracted:", text)
"""

    await backend.aupload_files([("/tmp/extract_docx.py", code.encode())])
    result = await backend.aexecute("python /tmp/extract_docx.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Verify Word document was created with correct format
    docx_content = await vfs_read_file(db_pool, "doc_skills", "/tmp/secret.docx")
    assert docx_content is not None, "Word document was not created"
    assert docx_content[:2] == b"PK", "Word document should be ZIP-based (OOXML)"

    # Verify extraction worked
    extracted = await vfs_read_file(db_pool, "doc_skills", "/tmp/extracted.txt")
    assert extracted is not None, "Extracted text file was not created"
    assert b"BETA456" in extracted, f"Expected access code. Got: {extracted.decode()}"


# ============================================================================
# INTEGRATION TESTS (Complex workflows)
# ============================================================================


@pytest.mark.asyncio
async def test_multi_format_workflow(backend, db_pool, clean_files):
    """Test creating multiple document formats in one workflow."""
    code = """
from document.xlsx import create_xlsx_bytes
from document.pdf import create_pdf_bytes
from document.docx_ooxml import create_docx_bytes

# Create Excel
xlsx = create_xlsx_bytes(["Product", "Q1", "Q2"], [["Widget", 100, 150], ["Gadget", 200, 250]])
with open("/tmp/sales.xlsx", "wb") as f:
    f.write(xlsx)

# Create PDF
pdf = create_pdf_bytes([("Sales Report", "Quarterly sales summary")])
with open("/tmp/sales.pdf", "wb") as f:
    f.write(pdf)

# Create Word
docx = create_docx_bytes(["Executive Summary", "Q1 and Q2 sales exceeded targets."])
with open("/tmp/sales.docx", "wb") as f:
    f.write(docx)

print("All three documents created successfully")
"""

    await backend.aupload_files([("/tmp/multi_format.py", code.encode())])
    result = await backend.aexecute("python /tmp/multi_format.py")
    assert result.exit_code == 0, f"Failed: {result.output}"

    # Deterministic VFS verification - all three files should exist
    xlsx = await vfs_read_file(db_pool, "doc_skills", "/tmp/sales.xlsx")
    assert xlsx is not None, "Excel file was not created"
    assert xlsx[:2] == b"PK", "Excel file should be ZIP-based"

    pdf = await vfs_read_file(db_pool, "doc_skills", "/tmp/sales.pdf")
    assert pdf is not None, "PDF file was not created"
    assert pdf[:4] == b"%PDF", "PDF file should start with %PDF header"

    docx = await vfs_read_file(db_pool, "doc_skills", "/tmp/sales.docx")
    assert docx is not None, "Word document was not created"
    assert docx[:2] == b"PK", "Word document should be ZIP-based"

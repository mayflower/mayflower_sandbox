"""
Comprehensive document processing tests matching maistack skills.

Tests all operations from:
- excel_skill.py
- pdf_skill.py
- powerpoint_skill.py
- word_skill.py

Test Strategy:
- All assertions use deterministic VFS verification
- NO brittle patterns like string matching on LLM responses
- Verify file existence and format directly in database
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

# Mark all tests in this module as slow (LLM-based)
pytestmark = pytest.mark.slow


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "min_size": 10,
        "max_size": 50,
        "command_timeout": 60,
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
async def clean_files(db_pool):
    """Clean files and error history before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'doc_skills'")

    from mayflower_sandbox.tools.execute import _error_history

    _error_history.clear()

    yield


@pytest.fixture
async def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    tools = create_sandbox_tools(db_pool, thread_id="doc_skills")
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    agent = create_agent(llm, tools, checkpointer=MemorySaver())
    return agent


# ============================================================================
# EXCEL TESTS (matching excel_skill.py)
# ============================================================================


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_excel_create_workbook(agent, db_pool, clean_files):
    """Test: ExcelSkill.create_workbook()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create an Excel file at /tmp/sales.xlsx using openpyxl with:
- Headers: Product, Quantity, Price, Total
- Row 1: Laptop, 5, 1200, =B2*C2
- Row 2: Mouse, 25, 30, =B3*C3
Format headers with bold font and light blue background.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "excel-create"}},
    )

    # Deterministic VFS verification - Excel files are ZIP-based (PK magic bytes)
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/sales.xlsx")
    assert content is not None, "Excel file was not created"
    assert len(content) > 100, "Excel file seems too small"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_excel_read_workbook(agent, db_pool, clean_files):
    """Test: ExcelSkill.read_workbook()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create an Excel file at /tmp/data.xlsx with data:
Headers: Name, Score
Alice, 95
Bob, 87

Then read the Excel file back and tell me Alice's score.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "excel-read"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/data.xlsx")
    assert content is not None, "Excel file was not created"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_excel_extract_text(agent, db_pool, clean_files):
    """Test: ExcelSkill.extract_text()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Excel at /tmp/report.xlsx with text:
Sheet1: "Q4 Financial Summary"
A1: Revenue
A2: $100,000

Then extract all text content from the Excel file.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "excel-extract"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/report.xlsx")
    assert content is not None, "Excel file was not created"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


# ============================================================================
# PDF TESTS (matching pdf_skill.py)
# ============================================================================


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_pdf_create(agent, db_pool, clean_files):
    """Test: PDFCreator.create_pdf()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PDF at /tmp/report.pdf using fpdf2 with:
Title: Q4 Financial Report
Section 1: Executive Summary - Revenue increased 25%
Section 2: Key Metrics - Customer growth at 40%""",
                )
            ]
        },
        config={"configurable": {"thread_id": "pdf-create"}},
    )

    # Deterministic VFS verification - PDF files start with %PDF
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/report.pdf")
    assert content is not None, "PDF file was not created"
    assert content[:4] == b"%PDF", "PDF file should start with %PDF header"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_pdf_extract_text(agent, db_pool, clean_files):
    """Test: PDFReader.extract_text()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PDF at /tmp/test.pdf with text "Secret Code: ALPHA123".
Then extract and tell me the secret code from the PDF.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "pdf-extract"}, "recursion_limit": 50},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/test.pdf")
    assert content is not None, "PDF file was not created"
    assert content[:4] == b"%PDF", "PDF file should start with %PDF header"
    # Text content should be embedded in PDF
    assert b"ALPHA123" in content, "PDF should contain the secret code"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_pdf_merge(agent, db_pool, clean_files):
    """Test: PDFSkill.merge_pdfs()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Using fpdf2 and pypdf:
1. Create /tmp/doc1.pdf with text "Page 1 Content"
2. Create /tmp/doc2.pdf with text "Page 2 Content"
3. Merge both into /tmp/merged.pdf using pypdf
4. Confirm the merged PDF was created""",
                )
            ]
        },
        config={"configurable": {"thread_id": "pdf-merge"}, "recursion_limit": 50},
    )

    # Deterministic VFS verification - all three PDFs should exist
    for path in ["/tmp/doc1.pdf", "/tmp/doc2.pdf", "/tmp/merged.pdf"]:
        content = await vfs_read_file(db_pool, "doc_skills", path)
        assert content is not None, f"{path} was not created"
        assert content[:4] == b"%PDF", f"{path} should be a valid PDF"


# ============================================================================
# POWERPOINT TESTS (matching powerpoint_skill.py)
# ============================================================================


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_powerpoint_create_simple_presentation(agent, db_pool, clean_files):
    """Test: PowerPointSkill.create_simple_presentation()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PowerPoint at /tmp/pitch.pptx using python-pptx with:
Slide 1: Title "Company Overview 2024"
Slide 2: Title "Mission" with bullets:
  - Innovate
  - Lead
  - Grow
Slide 3: Title "Metrics" with text "40% growth""",
                )
            ]
        },
        config={"configurable": {"thread_id": "pptx-create"}},
    )

    # Deterministic VFS verification - PPTX files are ZIP-based (PK magic bytes)
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/pitch.pptx")
    assert content is not None, "PowerPoint file was not created"
    assert content[:2] == b"PK", "PowerPoint file should be ZIP-based (OOXML)"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_powerpoint_read_presentation(agent, db_pool, clean_files):
    """Test: PowerPointSkill.read_presentation()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PowerPoint at /tmp/test.pptx with 3 slides:
Slide 1: "Title Slide"
Slide 2: "Content Slide"
Slide 3: "End Slide"

Then read the presentation and tell me how many slides it has.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "pptx-read"}, "recursion_limit": 50},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/test.pptx")
    assert content is not None, "PowerPoint file was not created"
    assert content[:2] == b"PK", "PowerPoint file should be ZIP-based (OOXML)"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_powerpoint_extract_text(agent, db_pool, clean_files):
    """Test: PowerPointSkill.extract_text()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create PowerPoint at /tmp/info.pptx with:
Slide 1: Title "Company Code: XYZ789"

Then extract all text from the presentation and tell me the company code.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "pptx-extract"}, "recursion_limit": 50},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/info.pptx")
    assert content is not None, "PowerPoint file was not created"
    assert content[:2] == b"PK", "PowerPoint file should be ZIP-based (OOXML)"
    # Text content should be in XML inside ZIP
    assert b"XYZ789" in content, "PowerPoint should contain the company code"


# ============================================================================
# WORD TESTS (matching word_skill.py)
# ============================================================================


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_word_create_document(agent, db_pool, clean_files):
    """Test: WordSkill.create_document()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/report.docx using python-docx with:
Title: Annual Report 2024
Section 1: Overview - Business performance summary
Section 2: Achievements:
  • Revenue growth 35%
  • Customer satisfaction 95%
Section 3: Next Steps (numbered list)""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-create"}},
    )

    # Deterministic VFS verification - DOCX files are ZIP-based (PK magic bytes)
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/report.docx")
    assert content is not None, "Word document was not created"
    assert content[:2] == b"PK", "Word document should be ZIP-based (OOXML)"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_word_read_document(agent, db_pool, clean_files):
    """Test: WordSkill.read_document()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/data.docx with:
Paragraph 1: Project Alpha Status
Paragraph 2: Completion: 75%

Then read the document and tell me the completion percentage.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-read"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/data.docx")
    assert content is not None, "Word document was not created"
    assert content[:2] == b"PK", "Word document should be ZIP-based (OOXML)"
    # Text content should be in XML inside ZIP
    assert b"75" in content, "Document should contain the completion percentage"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_word_extract_text(agent, db_pool, clean_files):
    """Test: WordSkill.extract_text()"""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create Word document at /tmp/secret.docx with text:
"Access Code: BETA456"

Then extract all text from the document and tell me the access code.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "docx-extract"}, "recursion_limit": 50},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "doc_skills", "/tmp/secret.docx")
    assert content is not None, "Word document was not created"
    assert content[:2] == b"PK", "Word document should be ZIP-based (OOXML)"
    # Text content should be in XML inside ZIP
    assert b"BETA456" in content, "Document should contain the access code"


# ============================================================================
# INTEGRATION TESTS (Complex workflows)
# ============================================================================


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_multi_format_workflow(agent, db_pool, clean_files):
    """Test creating multiple document formats in one workflow."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a sales report in multiple formats:
1. Excel /tmp/sales.xlsx: Headers (Product, Q1, Q2) with 2 data rows
2. PDF /tmp/sales.pdf: Title "Sales Report" with summary
3. Word /tmp/sales.docx: Executive summary paragraph

Tell me when all three files are created.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "multi-format"}, "recursion_limit": 50},
    )

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

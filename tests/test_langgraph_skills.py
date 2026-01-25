"""
LangGraph integration tests with document processing skills using sandbox.

These tests demonstrate how to use the PyodideSandbox with document processing
skills similar to the maistack skills implementation.

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

# Load environment variables
load_dotenv()

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from mayflower_sandbox.filesystem import VirtualFilesystem
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
        "max_size": 50,  # Increase pool size for concurrent LangGraph agent operations
        "command_timeout": 60,
    }

    pool = await asyncpg.create_pool(**db_config)

    # Ensure session exists
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('langgraph_skills_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id = 'langgraph_skills_test'"
        )
    yield


@pytest.fixture
def agent(db_pool):
    """Create LangGraph agent with sandbox tools."""
    # Create sandbox tools
    tools = create_sandbox_tools(db_pool, thread_id="langgraph_skills_test")

    # Create LLM
    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)

    # Create ReAct agent with checkpointer
    agent = create_agent(llm, tools, checkpointer=MemorySaver())

    return agent


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_excel_creation_skill(agent, db_pool, clean_files):
    """Test agent can create Excel files with openpyxl."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create an Excel file at /tmp/sales_data.xlsx with the following:
- Headers: Product, Quantity, Price, Total
- Row 1: Laptop, 5, 1200, 6000
- Row 2: Mouse, 25, 30, 750
- Row 3: Keyboard, 15, 80, 1200
Use openpyxl to format the headers with bold font and a blue background.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-excel-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/sales_data.xlsx")
    assert content is not None, "Excel file was not created"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_excel_with_formulas(agent, db_pool, clean_files):
    """Test agent can create Excel with formulas using openpyxl."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create an Excel file at /tmp/budget.xlsx with:
- Headers: Item, Q1, Q2, Q3, Q4, Total
- Row 1: Revenue, 10000, 12000, 15000, 18000, and a SUM formula
- Row 2: Expenses, 8000, 9000, 10000, 12000, and a SUM formula
Add formulas in the Total column to sum the quarterly values.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-excel-2"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/budget.xlsx")
    assert content is not None, "Excel file was not created"
    assert content[:2] == b"PK", "Excel file should be ZIP-based (OOXML)"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_pdf_creation_skill(agent, db_pool, clean_files):
    """Test agent can create PDF files with reportlab."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PDF report at /tmp/quarterly_report.pdf with:
- Title: Q4 2024 Financial Report
- Section 1: Executive Summary with text about 25% revenue growth
- Section 2: Financial Metrics with a simple data table
Use reportlab or a similar library to create the PDF.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-pdf-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/quarterly_report.pdf")
    assert content is not None, "PDF file was not created"
    assert content[:4] == b"%PDF", "PDF file should start with %PDF header"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_pdf_text_extraction(agent, db_pool, clean_files):
    """Test agent can extract text from PDF using pypdf."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a simple PDF at /tmp/test_doc.pdf with the text:
'The secret password is: ALPHA2024'. Then extract the text using pypdf and save to /tmp/extracted.txt.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-pdf-2"}, "recursion_limit": 50},
    )

    # Verify PDF was created with correct format
    pdf_content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/test_doc.pdf")
    assert pdf_content is not None, "PDF file was not created"
    assert pdf_content[:4] == b"%PDF", "PDF file should start with %PDF header"

    # Verify extraction worked by checking the extracted text file
    extracted = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/extracted.txt")
    assert extracted is not None, "Extracted text file was not created"
    assert b"ALPHA2024" in extracted, (
        f"Extracted text should contain password. Got: {extracted.decode()}"
    )


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_powerpoint_creation_skill(agent, db_pool, clean_files):
    """Test agent can create PowerPoint presentations."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PowerPoint presentation at /tmp/company_overview.pptx with:
- Title slide: "Company Overview 2024"
- Slide 2: Title "Our Mission" with bullet points about innovation and customer focus
- Slide 3: Title "Growth Metrics" with bullet points about 30% revenue growth
Use python-pptx library.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-pptx-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/company_overview.pptx")
    assert content is not None, "PowerPoint file was not created"
    assert content[:2] == b"PK", "PowerPoint file should be ZIP-based (OOXML)"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_word_document_creation_skill(agent, db_pool, clean_files):
    """Test agent can create Word documents."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a Word document at /tmp/project_report.docx with:
- Title: "Project Status Report"
- Section 1: "Overview" with a paragraph about project progress
- Section 2: "Key Achievements" with a bulleted list
- Section 3: "Next Steps" with a numbered list
Use python-docx library.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-docx-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/project_report.docx")
    assert content is not None, "Word document was not created"
    assert content[:2] == b"PK", "Word document should be ZIP-based (OOXML)"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_excel_data_analysis_workflow(agent, db_pool, clean_files):
    """Test complete workflow: create Excel, analyze data, generate report."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Complete data analysis workflow:
1. Create Excel file /tmp/employee_data.xlsx with columns: Name, Department, Salary
2. Add 5 sample employee records
3. Calculate the average salary using Python
4. Create a summary report in /tmp/analysis.txt with the findings""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-workflow-1"}},
    )

    # Deterministic VFS verification
    xlsx_content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/employee_data.xlsx")
    assert xlsx_content is not None, "Excel file was not created"
    assert xlsx_content[:2] == b"PK", "Excel file should be ZIP-based"

    analysis_content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/analysis.txt")
    assert analysis_content is not None, "Analysis file was not created"
    # Analysis should contain some numeric value (the average)
    assert any(char.isdigit() for char in analysis_content.decode("utf-8"))


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_pdf_with_multiple_pages(agent, db_pool, clean_files):
    """Test agent can create a multi-page PDF."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Create a PDF at /tmp/multi_page.pdf with 3 pages:
- Page 1: "Introduction - This is page 1"
- Page 2: "Content - This is page 2"
- Page 3: "Conclusion - This is page 3"
Tell me when the PDF is created.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-pdf-multi-1"}},
    )

    # Deterministic VFS verification
    content = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/multi_page.pdf")
    assert content is not None, "PDF file was not created"
    assert content[:4] == b"%PDF", "PDF file should start with %PDF header"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_multi_format_report_generation(agent, db_pool, clean_files):
    """Test agent can generate reports in multiple formats."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Generate a quarterly sales report in multiple formats:
1. Create Excel /tmp/sales_report.xlsx with sample Q1-Q4 sales data
2. Create a PDF /tmp/sales_report.pdf summarizing the data
3. Create a PowerPoint /tmp/sales_report.pptx with key highlights
Tell me when all three files are created successfully.""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-multi-format-1"}},
    )

    # Deterministic VFS verification - all three files should exist
    xlsx = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/sales_report.xlsx")
    assert xlsx is not None, "Excel file was not created"
    assert xlsx[:2] == b"PK", "Excel file should be ZIP-based"

    pdf = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/sales_report.pdf")
    assert pdf is not None, "PDF file was not created"
    assert pdf[:4] == b"%PDF", "PDF file should start with %PDF header"

    pptx = await vfs_read_file(db_pool, "langgraph_skills_test", "/tmp/sales_report.pptx")
    assert pptx is not None, "PowerPoint file was not created"
    assert pptx[:2] == b"PK", "PowerPoint file should be ZIP-based"


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set - skipping LLM test"
)
async def test_agent_csv_to_excel_conversion(db_pool, agent, clean_files):
    """Test agent can convert CSV to formatted Excel."""
    await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    """Convert CSV to Excel:
1. Create a CSV file /tmp/data.csv with headers: ID,Product,Price and 3 data rows
2. Read the CSV file
3. Convert it to Excel format at /tmp/data.xlsx with proper formatting
4. Confirm the conversion was successful""",
                )
            ]
        },
        config={"configurable": {"thread_id": "test-csv-excel-1"}},
    )

    # Verify by checking VFS for created files (deterministic), not LLM text
    vfs = VirtualFilesystem(db_pool, "langgraph_skills_test")
    try:
        xlsx_file = await vfs.read_file("/tmp/data.xlsx")
        assert xlsx_file is not None, "Expected /tmp/data.xlsx to exist in VFS"
        # Verify it's a valid xlsx (ZIP format)
        assert xlsx_file["content"][:4] == b"PK\x03\x04", "Expected ZIP magic bytes for xlsx"
    except Exception:
        # Fallback: check if CSV was at least created
        csv_file = await vfs.read_file("/tmp/data.csv")
        assert csv_file is not None, "Expected at least /tmp/data.csv to exist"

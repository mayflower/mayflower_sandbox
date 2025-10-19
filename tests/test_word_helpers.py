"""
Direct tests for new Word helper functions.
"""

import pytest
import asyncpg
import os
import sys
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
            VALUES ('word_helpers_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


async def test_docx_extract_text(db_pool):
    """Test docx_extract_text function."""
    executor = SandboxExecutor(db_pool, "word_helpers_test", allow_net=True, timeout_seconds=60.0)

    code = """
# Create a minimal Word document
import zipfile
import io

# Minimal docx structure
document_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Third paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>'''

content_types = '''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', content_types)
    zf.writestr('word/document.xml', document_xml)

docx_bytes = buf.getvalue()

# Test extraction
from document.docx_ooxml import docx_extract_text

text = docx_extract_text(docx_bytes)
print(f"Extracted text:\\n{text}")

assert "First paragraph" in text
assert "Second paragraph" in text
assert "Third paragraph" in text

print("✓ docx_extract_text works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "docx_extract_text works" in result.stdout


async def test_docx_extract_paragraphs(db_pool):
    """Test docx_extract_paragraphs function."""
    executor = SandboxExecutor(db_pool, "word_helpers_test", allow_net=True, timeout_seconds=60.0)

    code = """
import zipfile
import io

document_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Para 1</w:t></w:r></w:p>
    <w:p><w:r><w:t>Para 2</w:t></w:r></w:p>
  </w:body>
</w:document>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('word/document.xml', document_xml)

docx_bytes = buf.getvalue()

from document.docx_ooxml import docx_extract_paragraphs

paragraphs = docx_extract_paragraphs(docx_bytes)
print(f"Paragraphs: {paragraphs}")

assert len(paragraphs) == 2
assert paragraphs[0] == "Para 1"
assert paragraphs[1] == "Para 2"

print("✓ docx_extract_paragraphs works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "docx_extract_paragraphs works" in result.stdout


async def test_docx_read_tables(db_pool):
    """Test docx_read_tables function."""
    executor = SandboxExecutor(db_pool, "word_helpers_test", allow_net=True, timeout_seconds=60.0)

    code = """
import zipfile
import io

document_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Header 1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Header 2</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Cell 1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Cell 2</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('word/document.xml', document_xml)

docx_bytes = buf.getvalue()

from document.docx_ooxml import docx_read_tables

tables = docx_read_tables(docx_bytes)
print(f"Tables: {tables}")

assert len(tables) == 1
assert len(tables[0]) == 2  # 2 rows
assert len(tables[0][0]) == 2  # 2 columns
assert tables[0][0][0] == "Header 1"
assert tables[0][0][1] == "Header 2"
assert tables[0][1][0] == "Cell 1"
assert tables[0][1][1] == "Cell 2"

print("✓ docx_read_tables works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "docx_read_tables works" in result.stdout


async def test_docx_find_replace(db_pool):
    """Test docx_find_replace function."""
    executor = SandboxExecutor(db_pool, "word_helpers_test", allow_net=True, timeout_seconds=60.0)

    code = """
import zipfile
import io

document_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Company Name</w:t></w:r></w:p>
    <w:p><w:r><w:t>Year 2024</w:t></w:r></w:p>
  </w:body>
</w:document>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('word/document.xml', document_xml)

docx_bytes = buf.getvalue()

from document.docx_ooxml import docx_find_replace, docx_extract_text

replacements = {
    "Company Name": "ACME Corp",
    "Year 2024": "Year 2025"
}

modified = docx_find_replace(docx_bytes, replacements)
text = docx_extract_text(modified)

print(f"Modified text:\\n{text}")

assert "ACME Corp" in text
assert "Year 2025" in text
assert "Company Name" not in text
assert "Year 2024" not in text

print("✓ docx_find_replace works")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "docx_find_replace works" in result.stdout

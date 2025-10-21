"""
Direct unit tests for helper functions without agent.
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
            VALUES ('helper_direct_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


async def test_helper_import_direct(db_pool):
    """Test that helpers can be imported directly in Pyodide."""
    executor = SandboxExecutor(db_pool, "helper_direct_test", allow_net=True, timeout_seconds=30.0)

    code = """
from document.docx_ooxml import docx_add_comment, unzip_docx_like, zip_docx_like

print("Available functions:")
print(f"  docx_add_comment: {callable(docx_add_comment)}")
print(f"  unzip_docx_like: {callable(unzip_docx_like)}")
print(f"  zip_docx_like: {callable(zip_docx_like)}")
print(f"\\nDocstring: {docx_add_comment.__doc__[:100]}")
"""

    result = await executor.execute(code)

    assert result.success
    assert "docx_add_comment: True" in result.stdout
    assert "unzip_docx_like: True" in result.stdout
    assert "zip_docx_like: True" in result.stdout


async def test_helper_unzip_zip_direct(db_pool):
    """Test unzip_docx_like and zip_docx_like functions."""
    executor = SandboxExecutor(db_pool, "helper_direct_test", allow_net=True, timeout_seconds=30.0)

    code = """
# Create a minimal valid docx file from scratch using zipfile
import zipfile
import io

# Minimal docx structure
document_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Test paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>'''

content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''

# Create minimal docx
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', content_types)
    zf.writestr('word/document.xml', document_xml)

docx_bytes = buf.getvalue()
print(f"Created minimal docx: {len(docx_bytes)} bytes")

# Now test the helper functions
from document.docx_ooxml import unzip_docx_like, zip_docx_like

# Unzip
parts = unzip_docx_like(docx_bytes)
print(f"Extracted {len(parts)} parts")
print(f"Parts: {list(parts.keys())}")

# Verify word/document.xml exists
assert 'word/document.xml' in parts
print("✓ word/document.xml found")

# Re-zip
new_docx = zip_docx_like(parts)
print(f"Re-zipped size: {len(new_docx)} bytes")

# Verify re-zipped content
new_parts = unzip_docx_like(new_docx)
assert 'word/document.xml' in new_parts
print("✓ Successfully recreated and verified docx")
"""

    result = await executor.execute(code)

    assert result.success, f"Execution failed: {result.stderr}"
    assert "word/document.xml found" in result.stdout
    assert "Successfully recreated and verified docx" in result.stdout

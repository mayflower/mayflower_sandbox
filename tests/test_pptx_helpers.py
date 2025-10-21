"""
Direct unit tests for PPTX helper functions.
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
            VALUES ('pptx_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


async def test_pptx_import(db_pool):
    """Test that PPTX helpers can be imported in Pyodide."""
    executor = SandboxExecutor(db_pool, "pptx_test", allow_net=True, timeout_seconds=30.0)

    code = """
from document.pptx_ooxml import (
    pptx_extract_text,
    pptx_inventory,
    pptx_replace_text,
    pptx_rearrange,
    pptx_contact_sheet_html,
    unzip_pptx_like,
    zip_pptx_like
)

print("Available functions:")
print(f"  pptx_extract_text: {callable(pptx_extract_text)}")
print(f"  pptx_inventory: {callable(pptx_inventory)}")
print(f"  pptx_replace_text: {callable(pptx_replace_text)}")
print(f"  pptx_rearrange: {callable(pptx_rearrange)}")
print(f"  pptx_contact_sheet_html: {callable(pptx_contact_sheet_html)}")
print(f"  unzip_pptx_like: {callable(unzip_pptx_like)}")
print(f"  zip_pptx_like: {callable(zip_pptx_like)}")
"""

    result = await executor.execute(code)

    assert result.success, f"Failed to import: {result.stderr}"
    assert "pptx_extract_text: True" in result.stdout
    assert "pptx_inventory: True" in result.stdout
    assert "pptx_replace_text: True" in result.stdout


async def test_pptx_extract_text(db_pool):
    """Test pptx_extract_text function."""
    executor = SandboxExecutor(db_pool, "pptx_test", allow_net=True, timeout_seconds=60.0)

    code = """
# Create a minimal PPTX file
import zipfile
import io

# Minimal PPTX structure with two slides
presentation_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
    <p:sldId id="257" r:id="rId2"/>
  </p:sldIdLst>
</p:presentation>'''

slide1_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>Slide 1 Title</a:t></a:r></a:p>
          <a:p><a:r><a:t>Slide 1 Content</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>'''

slide2_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>Slide 2 Title</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>'''

rels_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                Target="slides/slide1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                Target="slides/slide2.xml"/>
</Relationships>'''

# Create PPTX
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('ppt/presentation.xml', presentation_xml)
    zf.writestr('ppt/slides/slide1.xml', slide1_xml)
    zf.writestr('ppt/slides/slide2.xml', slide2_xml)
    zf.writestr('ppt/_rels/presentation.xml.rels', rels_xml)

pptx_bytes = buf.getvalue()

# Test extraction
from document.pptx_ooxml import pptx_extract_text

texts = pptx_extract_text(pptx_bytes)
print(f"Extracted text: {texts}")

assert 1 in texts, "Slide 1 not found"
assert 2 in texts, "Slide 2 not found"
assert "Slide 1 Title" in texts[1], f"Slide 1 title not found in {texts[1]}"
assert "Slide 1 Content" in texts[1], f"Slide 1 content not found in {texts[1]}"
assert "Slide 2 Title" in texts[2], f"Slide 2 title not found in {texts[2]}"

print("✓ pptx_extract_text works correctly")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pptx_extract_text works correctly" in result.stdout


async def test_pptx_replace_text(db_pool):
    """Test pptx_replace_text function."""
    executor = SandboxExecutor(db_pool, "pptx_test", allow_net=True, timeout_seconds=60.0)

    code = """
import zipfile
import io

# Create minimal PPTX with text to replace
slide1_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>Company Name</a:t></a:r></a:p>
          <a:p><a:r><a:t>Year 2024</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('ppt/slides/slide1.xml', slide1_xml)

pptx_bytes = buf.getvalue()

# Test replacement
from document.pptx_ooxml import pptx_replace_text, pptx_extract_text

replacements = {
    "ppt/slides/slide1.xml": {
        "Company Name": "ACME Corp",
        "Year 2024": "Year 2025"
    }
}

modified = pptx_replace_text(pptx_bytes, replacements)

# Verify replacements
texts = pptx_extract_text(modified)
print(f"Modified text: {texts}")

assert "ACME Corp" in texts[1], f"Company name not replaced: {texts[1]}"
assert "Year 2025" in texts[1], f"Year not replaced: {texts[1]}"
assert "Company Name" not in texts[1], f"Old company name still present: {texts[1]}"

print("✓ pptx_replace_text works correctly")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pptx_replace_text works correctly" in result.stdout


async def test_pptx_contact_sheet_html(db_pool):
    """Test pptx_contact_sheet_html function."""
    executor = SandboxExecutor(db_pool, "pptx_test", allow_net=True, timeout_seconds=60.0)

    code = """
import zipfile
import io

# Create minimal PPTX
slide1_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>Introduction Slide</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('ppt/slides/slide1.xml', slide1_xml)

pptx_bytes = buf.getvalue()

# Test HTML generation
from document.pptx_ooxml import pptx_contact_sheet_html

html = pptx_contact_sheet_html(pptx_bytes)
print(f"Generated HTML length: {len(html)} chars")

assert "<html>" in html, "HTML structure missing"
assert "Slide 1" in html, "Slide 1 label not found"
assert "Introduction Slide" in html, "Slide content not found"

print("✓ pptx_contact_sheet_html works correctly")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pptx_contact_sheet_html works correctly" in result.stdout


async def test_pptx_inventory(db_pool):
    """Test pptx_inventory function."""
    executor = SandboxExecutor(db_pool, "pptx_test", allow_net=True, timeout_seconds=60.0)

    code = """
import zipfile
import io

# Create minimal PPTX
slide1_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>First text</a:t></a:r></a:p>
          <a:p><a:r><a:t>Second text</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>'''

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    zf.writestr('ppt/slides/slide1.xml', slide1_xml)

pptx_bytes = buf.getvalue()

# Test inventory
from document.pptx_ooxml import pptx_inventory

inv = pptx_inventory(pptx_bytes)
print(f"Inventory: {inv}")

assert "ppt/slides/slide1.xml" in inv, "Slide 1 not in inventory"
assert len(inv["ppt/slides/slide1.xml"]) == 2, f"Expected 2 text items, got {len(inv['ppt/slides/slide1.xml'])}"

texts = [item["text"] for item in inv["ppt/slides/slide1.xml"]]
assert "First text" in texts, f"First text not found in {texts}"
assert "Second text" in texts, f"Second text not found in {texts}"

print("✓ pptx_inventory works correctly")
"""

    result = await executor.execute(code)

    assert result.success, f"Test failed: {result.stderr}"
    assert "pptx_inventory works correctly" in result.stdout

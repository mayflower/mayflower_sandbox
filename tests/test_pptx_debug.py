"""
Debug test for PPTX helpers.
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
            VALUES ('pptx_debug', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


async def test_pptx_debug(db_pool):
    """Debug PPTX extraction."""
    executor = SandboxExecutor(db_pool, "pptx_debug", allow_net=True, timeout_seconds=60.0)

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
          <a:p><a:r><a:t>Test Text</a:t></a:r></a:p>
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

# Debug: Check what's in the zip
with zipfile.ZipFile(io.BytesIO(pptx_bytes), 'r') as zf:
    print("Files in PPTX:")
    for name in zf.namelist():
        print(f"  {name}")

# Test extraction
from document.pptx_ooxml import unzip_pptx_like, pptx_extract_text
import xml.etree.ElementTree as ET

parts = unzip_pptx_like(pptx_bytes)
print(f"\\nParts extracted: {list(parts.keys())}")

# Check if slide file exists
if 'ppt/slides/slide1.xml' in parts:
    print("\\n✓ slide1.xml found in parts")

    # Parse XML
    slide = ET.fromstring(parts['ppt/slides/slide1.xml'])
    print(f"XML root tag: {slide.tag}")

    # Try to find text with namespace
    NS = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    }
    texts = slide.findall('.//a:t', NS)
    print(f"\\nText nodes found with NS: {len(texts)}")
    for t in texts:
        print(f"  - {t.text}")

    # Try without namespace
    texts_no_ns = slide.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t')
    print(f"\\nText nodes found with full NS path: {len(texts_no_ns)}")
    for t in texts_no_ns:
        print(f"  - {t.text}")
else:
    print("\\n✗ slide1.xml NOT found in parts")

# Now test the helper
print("\\nTesting helper function:")
texts = pptx_extract_text(pptx_bytes)
print(f"Helper result: {texts}")

# Manual test of the extraction logic
print("\\nManual extraction test:")
for name, data in parts.items():
    print(f"Checking file: {name}")
    if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
        print(f"  ✓ Matches pattern")
        try:
            slide = ET.fromstring(data)
            print(f"  ✓ XML parsed")

            NS = {
                'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
                'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
            }

            all_t_nodes = slide.findall('.//a:t', NS)
            print(f"  Found {len(all_t_nodes)} <a:t> nodes")

            texts = [t.text or "" for t in all_t_nodes if t.text]
            print(f"  Extracted texts: {texts}")

            idx = int(name.split("slide")[1].split(".xml")[0])
            print(f"  Slide index: {idx}")

            if texts:
                print(f"  ✓ Would add to result: {idx}: {texts}")
            else:
                print(f"  ✗ texts list is empty!")

        except Exception as e:
            print(f"  ✗ Exception: {e}")
            import traceback
            traceback.print_exc()
"""

    result = await executor.execute(code)

    print("STDOUT:", result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    assert result.success, f"Debug test failed: {result.stderr}"

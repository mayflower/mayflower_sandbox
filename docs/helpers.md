# Helper Module System for Mayflower Sandbox

## Overview

The Mayflower Sandbox supports a flexible helper module system that allows you to provide reusable Python functions to agents running in the Pyodide environment. Helpers are automatically loaded into the Pyodide virtual filesystem and are available for import in agent code.

## Architecture

### Directory Structure

```
src/mayflower_sandbox/helpers/
├── __init__.py              # Package initialization
├── manifest.json            # Helper metadata and documentation
├── document/
│   ├── __init__.py
│   ├── docx_ooxml.py       # Pure OOXML manipulation for Word
│   ├── docx_conversions.py # Format conversions (docx to markdown, etc.)
│   ├── pdf_manipulation.py # PDF operations (merge, split, extract)
│   └── excel_formulas.py   # Advanced Excel operations
├── data/
│   ├── __init__.py
│   ├── csv_processing.py   # CSV analysis and transformation
│   ├── json_utils.py        # JSON manipulation utilities
│   └── xml_parsing.py       # XML/OOXML parsing helpers
├── web/
│   ├── __init__.py
│   ├── html_parsing.py      # HTML parsing and extraction
│   └── markdown_utils.py    # Markdown processing
└── utils/
    ├── __init__.py
    ├── file_helpers.py      # General file operations
    └── text_processing.py   # Text manipulation utilities
```

### How It Works

1. **Auto-Discovery**: When `SandboxExecutor` initializes, it recursively scans the `helpers/` directory
2. **VFS Loading**: All `.py` files are loaded into the VFS at `/home/pyodide/`
3. **Import Path**: Pyodide's default `sys.path` includes `/home/pyodide`, making helpers importable
4. **Persistence**: Helpers persist in VFS across executions within the same thread

## Creating New Helpers

### Step 1: Choose a Category

Organize your helper by domain:
- **document/** - Document processing (Word, PDF, Excel, PowerPoint)
- **data/** - Data manipulation (CSV, JSON, XML, databases)
- **web/** - Web scraping, HTML/markdown processing
- **utils/** - General utilities

### Step 2: Create the Helper File

**Example: `helpers/document/docx_ooxml.py`**

```python
"""
OOXML manipulation helpers for Word documents.

Pure Python implementation using xml.etree.ElementTree and zipfile.
No external dependencies required.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Any

# Namespace mappings for OOXML
NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def unzip_docx_like(docx_bytes: bytes) -> Dict[str, bytes]:
    """Extract all files from a docx (zip archive)."""
    parts = {}
    with zipfile.ZipFile(io.BytesIO(docx_bytes), 'r') as zf:
        for name in zf.namelist():
            parts[name] = zf.read(name)
    return parts


def zip_docx_like(parts: Dict[str, bytes]) -> bytes:
    """Create a docx (zip archive) from parts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx_add_comment(
    docx_bytes: bytes,
    paragraph_index: int,
    text: str,
    author: str = "Pyodide Tool",
    initials: str = "PT",
    date_iso: str = None
) -> bytes:
    """
    Add a comment to the n-th paragraph in a Word document.

    Uses pure OOXML manipulation - no lxml dependency.
    Creates word/comments.xml if missing and adds proper relationships.

    Args:
        docx_bytes: Input Word document as bytes
        paragraph_index: Zero-based paragraph index
        text: Comment text
        author: Comment author name
        initials: Author initials
        date_iso: ISO timestamp (auto-generated if None)

    Returns:
        Modified Word document as bytes

    Example:
        >>> docx_bytes = open('/tmp/doc.docx', 'rb').read()
        >>> modified = docx_add_comment(docx_bytes, 0, "Please review this")
        >>> open('/tmp/commented.docx', 'wb').write(modified)
    """
    parts = unzip_docx_like(docx_bytes)

    # Load document.xml
    doc = ET.fromstring(parts["word/document.xml"])
    paras = doc.findall(".//w:body/w:p", NS)
    if paragraph_index < 0 or paragraph_index >= len(paras):
        raise IndexError(f"paragraph_index {paragraph_index} out of range (0-{len(paras)-1})")
    p = paras[paragraph_index]

    # Ensure comments.xml exists
    if "word/comments.xml" not in parts:
        comments = ET.Element(f"{{{NS['w']}}}comments")
        parts["word/comments.xml"] = ET.tostring(comments, encoding="utf-8", xml_declaration=True)
    else:
        comments = ET.fromstring(parts["word/comments.xml"])

    # Ensure relationship to comments.xml
    rels_path = "word/_rels/document.xml.rels"
    if rels_path not in parts:
        rels = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
        parts[rels_path] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
    else:
        rels = ET.fromstring(parts[rels_path])

    # Check if comments relationship exists
    has_comment_rel = False
    for r in rels.findall(".//Relationship", NS):
        if r.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments":
            has_comment_rel = True
            break

    if not has_comment_rel:
        next_id = f"rId{len(rels.findall('.//Relationship')) + 1}"
        ET.SubElement(rels, "Relationship", {
            "Id": next_id,
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
            "Target": "comments.xml"
        })

    # Generate comment ID
    existing_ids = [int(c.get(f"{{{NS['w']}}}id", "0")) for c in comments.findall(".//w:comment", NS)]
    cid = (max(existing_ids) + 1) if existing_ids else 0
    date_iso = date_iso or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    # Create comment in comments.xml
    cmt = ET.SubElement(comments, f"{{{NS['w']}}}comment", {
        f"{{{NS['w']}}}id": str(cid),
        f"{{{NS['w']}}}author": author,
        f"{{{NS['w']}}}initials": initials,
        f"{{{NS['w']}}}date": date_iso,
    })
    cp = ET.SubElement(cmt, f"{{{NS['w']}}}p")
    cr = ET.SubElement(cp, f"{{{NS['w']}}}r")
    ct = ET.SubElement(cr, f"{{{NS['w']}}}t")
    ct.text = text

    # Insert comment range markers in paragraph
    first_run = p.find(".//w:r", NS)
    if first_run is None:
        # Create empty run if paragraph has none
        first_run = ET.SubElement(p, f"{{{NS['w']}}}r")
        ET.SubElement(first_run, f"{{{NS['w']}}}t").text = ""

    run_index = list(p).index(first_run)

    # commentRangeStart before first run
    start = ET.Element(f"{{{NS['w']}}}commentRangeStart", {f"{{{NS['w']}}}id": str(cid)})
    p.insert(run_index, start)

    # commentRangeEnd after first run
    end = ET.Element(f"{{{NS['w']}}}commentRangeEnd", {f"{{{NS['w']}}}id": str(cid)})
    p.insert(run_index + 2, end)

    # commentReference after end
    rr = ET.Element(f"{{{NS['w']}}}r")
    rpr = ET.SubElement(rr, f"{{{NS['w']}}}rPr")
    ET.SubElement(rpr, f"{{{NS['w']}}}rStyle", {f"{{{NS['w']}}}val": "CommentReference"})
    ET.SubElement(rr, f"{{{NS['w']}}}commentReference", {f"{{{NS['w']}}}id": str(cid)})
    p.insert(run_index + 3, rr)

    # Save modified parts
    parts["word/comments.xml"] = ET.tostring(comments, encoding="utf-8", xml_declaration=True)
    parts[rels_path] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8", xml_declaration=True)

    return zip_docx_like(parts)
```

### Step 3: Update manifest.json

**`helpers/manifest.json`:**

```json
{
  "version": "1.0.0",
  "helpers": {
    "document.docx_ooxml": {
      "description": "Pure OOXML manipulation for Word documents",
      "functions": [
        {
          "name": "docx_add_comment",
          "description": "Add a comment to a paragraph",
          "parameters": ["docx_bytes", "paragraph_index", "text"],
          "returns": "bytes"
        },
        {
          "name": "unzip_docx_like",
          "description": "Extract docx contents",
          "parameters": ["docx_bytes"],
          "returns": "Dict[str, bytes]"
        },
        {
          "name": "zip_docx_like",
          "description": "Create docx from parts",
          "parameters": ["parts"],
          "returns": "bytes"
        }
      ],
      "dependencies": {
        "stdlib": ["xml.etree.ElementTree", "zipfile", "io", "datetime"],
        "micropip": []
      },
      "examples": [
        "from document.docx_ooxml import docx_add_comment",
        "modified = docx_add_comment(docx_bytes, 0, 'Review this')"
      ]
    },
    "document.pdf_manipulation": {
      "description": "PDF operations using pypdf",
      "functions": [
        {
          "name": "merge_pdfs",
          "description": "Merge multiple PDF files",
          "parameters": ["pdf_paths"],
          "returns": "bytes"
        }
      ],
      "dependencies": {
        "stdlib": [],
        "micropip": ["pypdf"]
      }
    }
  }
}
```

### Step 4: No Code Changes Needed!

The helper system auto-discovers new files. Just:
1. Add your `.py` file to the appropriate category
2. Update `manifest.json` with documentation
3. Restart the executor (or implement hot-reload)

## Using Helpers in Agent Code

### Basic Import

```python
# Import helper functions
from document.docx_ooxml import docx_add_comment, unzip_docx_like
from document.pdf_manipulation import merge_pdfs

# Use helpers
docx_bytes = open('/tmp/doc.docx', 'rb').read()
commented = docx_add_comment(docx_bytes, 0, "Great work!")
open('/tmp/commented.docx', 'wb').write(commented)
```

### With Error Handling

```python
try:
    from document.docx_ooxml import docx_add_comment

    docx_bytes = open('/tmp/doc.docx', 'rb').read()
    modified = docx_add_comment(docx_bytes, 0, "Review this section")
    open('/tmp/output.docx', 'wb').write(modified)
    print("Comment added successfully")
except ImportError:
    print("Helper not available")
except IndexError as e:
    print(f"Paragraph index error: {e}")
```

### Combining Multiple Helpers

```python
from document.docx_ooxml import docx_add_comment
from document.pptx_ooxml import pptx_extract_text, pptx_replace_text

# Add comment to Word doc
docx_bytes = open('/tmp/input.docx', 'rb').read()
commented = docx_add_comment(docx_bytes, 0, "TODO: Expand this")
open('/tmp/commented.docx', 'wb').write(commented)

# Extract text from PowerPoint
pptx_bytes = open('/tmp/presentation.pptx', 'rb').read()
texts = pptx_extract_text(pptx_bytes)
print(f"Found {len(texts)} slides with text")
```

## Available Helpers Reference

### PowerPoint (PPTX) Helpers

Pure OOXML manipulation for PowerPoint presentations. No external dependencies.

```python
from document.pptx_ooxml import (
    pptx_extract_text,
    pptx_inventory,
    pptx_replace_text,
    pptx_rearrange,
    pptx_contact_sheet_html,
    unzip_pptx_like,
    zip_pptx_like
)
```

**Extract all slide text:**
```python
pptx_bytes = open('/tmp/presentation.pptx', 'rb').read()
texts = pptx_extract_text(pptx_bytes)
# Returns: {1: ['Title', 'Content'], 2: ['Slide 2 Title'], ...}

for slide_num, slide_texts in texts.items():
    print(f"Slide {slide_num}: {' | '.join(slide_texts)}")
```

**Find and replace text across all slides:**
```python
replacements = {
    "ppt/slides/slide1.xml": {"Company Name": "ACME Corp"},
    "ppt/slides/slide2.xml": {"2024": "2025", "Q3": "Q4"}
}
modified = pptx_replace_text(pptx_bytes, replacements)
open('/tmp/updated.pptx', 'wb').write(modified)
```

**Reorder slides:**
```python
# Move slide 3 to first position: [3, 1, 2, 4, 5]
new_order = [3, 1, 2, 4, 5]
reordered = pptx_rearrange(pptx_bytes, new_order)
open('/tmp/reordered.pptx', 'wb').write(reordered)
```

**Generate HTML preview (no PowerPoint needed):**
```python
html = pptx_contact_sheet_html(pptx_bytes)
open('/tmp/preview.html', 'w').write(html)
# Open preview.html in browser to see all slide content
```

**Inventory all text elements:**
```python
inv = pptx_inventory(pptx_bytes)
for slide_path, items in inv.items():
    print(f"{slide_path}: {len(items)} text elements")
    for item in items:
        print(f"  - {item['text']}")
```

### Excel (XLSX) Helpers

Helper functions for working with Excel files using openpyxl.

```python
# Install dependencies first
import micropip
await micropip.install('openpyxl')

from document.xlsx_helpers import (
    xlsx_get_sheet_names,
    xlsx_read_cells,
    xlsx_write_cells,
    xlsx_to_dict,
    xlsx_has_formulas,
    xlsx_read_with_formulas
)
```

**Get sheet names:**
```python
xlsx_bytes = open('/tmp/workbook.xlsx', 'rb').read()
sheets = xlsx_get_sheet_names(xlsx_bytes)
print(sheets)  # ['Sheet1', 'Sheet2', 'Data']
```

**Read specific cells:**
```python
values = xlsx_read_cells(xlsx_bytes, 'Sheet1', ['A1', 'B2', 'C3'])
print(values)  # {'A1': 'Name', 'B2': 42, 'C3': 3.14}
```

**Write to cells:**
```python
modified = xlsx_write_cells(xlsx_bytes, 'Sheet1', {'A1': 'Updated', 'B2': 100})
open('/tmp/output.xlsx', 'wb').write(modified)
```

**Convert sheet to dictionaries:**
```python
data = xlsx_to_dict(xlsx_bytes, 'Sheet1')
print(data)  # [{'Name': 'Alice', 'Age': 30}, {'Name': 'Bob', 'Age': 25}]
```

**Find which cells have formulas:**
```python
formulas = xlsx_has_formulas(xlsx_bytes)
print(formulas)  # {'Sheet1': ['A3', 'B5'], 'Sheet2': ['D2']}
```

**Read both values and formulas:**
```python
data = xlsx_read_with_formulas(xlsx_bytes)
print(data['values']['Sheet1']['A1'])  # Cell value
print(data['formulas']['Sheet1']['A3'])  # Formula text like "=SUM(A1:A2)"
```

## Implementation in SandboxExecutor

### Loading Helpers into VFS

```python
# In sandbox_executor.py

class SandboxExecutor:
    def __init__(self, db_pool, thread_id, **kwargs):
        self.vfs = VirtualFilesystem(db_pool, thread_id)
        # ... other init ...

        # Preload helpers into VFS
        asyncio.run(self._preload_helpers())

    async def _preload_helpers(self):
        """Load all helper modules into VFS at /home/pyodide/"""
        helpers_dir = Path(__file__).parent / "helpers"

        if not helpers_dir.exists():
            return

        for py_file in helpers_dir.rglob("*.py"):
            # Calculate VFS path maintaining directory structure
            rel_path = py_file.relative_to(helpers_dir)
            vfs_path = f"/home/pyodide/{rel_path}"

            # Read file content
            content = py_file.read_bytes()

            # Write to VFS (persists across executions)
            await self.vfs.write(vfs_path, content)

        logger.info(f"Preloaded {len(list(helpers_dir.rglob('*.py')))} helper modules")
```

### Passing Helpers to Pyodide

The helpers are already in VFS and will be passed to Pyodide in the existing file loading mechanism at `executor.ts:162-171`.

### Invalidating Import Cache

After loading helpers, ensure Python's import cache is invalidated:

```python
# In executor.ts after mounting files:

// After loading all files (line ~171), invalidate cache
await pyodide.runPythonAsync(`
import importlib
importlib.invalidate_caches()
`);
```

## Tool Description Update

Add helper documentation to `execute_python` tool description:

```python
description: str = """Execute Python code in a secure Pyodide sandbox.

...existing description...

AVAILABLE HELPER MODULES (pre-loaded):

Document Processing:
  from document.docx_ooxml import docx_add_comment, unzip_docx_like
  from document.docx_conversions import docx_to_markdown
  from document.pdf_manipulation import merge_pdfs, split_pdf

Data Processing:
  from data.csv_processing import analyze_csv
  from data.json_utils import deep_merge

Web Utilities:
  from web.html_parsing import extract_links
  from web.markdown_utils import markdown_to_html

See HELPERS.md for complete documentation.
"""
```

## Testing Helpers

### Unit Tests

Create tests for each helper module:

```python
# tests/test_docx_ooxml_helper.py

def test_docx_add_comment():
    """Test the helper function directly."""
    from document.docx_ooxml import docx_add_comment

    # Create test docx
    docx_bytes = create_test_docx()

    # Add comment
    result = docx_add_comment(docx_bytes, 0, "Test comment")

    # Verify
    assert b"Test comment" in result
```

### Integration Tests

Test helpers through the agent:

```python
# tests/test_agent_with_helpers.py

async def test_agent_uses_helper(agent):
    """Test that agent can import and use helper."""
    result = await agent.ainvoke({
        "messages": [(
            "user",
            "Use the docx_add_comment helper to add a comment to paragraph 0"
        )]
    })

    assert "from document.docx_ooxml import docx_add_comment" in result
```

## Best Practices

### 1. Pure Python When Possible
- Prefer stdlib over external dependencies
- Keep helpers self-contained
- Document all dependencies in manifest

### 2. Clear Documentation
- Docstrings with examples
- Type hints for parameters
- Clear error messages

### 3. Error Handling
- Validate inputs
- Raise descriptive exceptions
- Handle edge cases

### 4. Performance
- Lazy imports for heavy dependencies
- Cache expensive operations
- Profile complex operations

### 5. Testing
- Unit test each function
- Integration test with agent
- Test error cases

## Troubleshooting

### Helper Not Found
```python
ImportError: No module named 'document.docx_ooxml'
```
**Solution**: Check that file exists in `helpers/document/docx_ooxml.py` and VFS loading worked.

### Import Works But Function Missing
```python
AttributeError: module 'document.docx_ooxml' has no attribute 'docx_add_comment'
```
**Solution**: Check function is defined and not prefixed with `_` (private).

### Dependency Missing
```python
ModuleNotFoundError: No module named 'pypdf'
```
**Solution**: Install with micropip in your code before importing helper:
```python
import micropip
await micropip.install('pypdf')
from document.pdf_manipulation import merge_pdfs
```

## Future Enhancements

- **Hot Reloading**: Reload helpers without restart
- **Version Management**: Track helper versions, handle updates
- **Dependency Auto-Install**: Automatically install micropip deps
- **Helper Marketplace**: Share community helpers
- **Performance Monitoring**: Track helper usage and performance
- **Documentation Generation**: Auto-generate docs from code

## Contributing Helpers

When contributing new helpers:

1. ✅ Follow directory structure by category
2. ✅ Add comprehensive docstrings with examples
3. ✅ Update `manifest.json`
4. ✅ Add unit and integration tests
5. ✅ Document all dependencies
6. ✅ Use type hints
7. ✅ Handle errors gracefully
8. ✅ Keep functions focused and reusable

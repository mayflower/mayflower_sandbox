# Document Processing

Mayflower Sandbox includes built-in helper modules for processing Word, Excel, PowerPoint, and PDF files inside the Pyodide sandbox. Helpers are automatically loaded into the virtual filesystem at `/home/pyodide/` and can be imported directly in sandbox code.

## Architecture

### Directory Structure

```
src/mayflower_sandbox/helpers/
├── __init__.py              # Package initialization
├── _install.py              # micropip installation helpers
├── manifest.json            # Helper metadata and documentation
├── document/
│   ├── __init__.py
│   ├── docx_ooxml.py       # Pure OOXML manipulation for Word
│   ├── pdf_manipulation.py # PDF operations (merge, split, extract)
│   ├── pdf_creation.py     # PDF creation utilities
│   ├── pptx_ooxml.py       # Pure OOXML manipulation for PowerPoint
│   └── xlsx_helpers.py     # Excel operations with openpyxl
├── data/
│   └── __init__.py         # Placeholder for future data helpers
├── web/
│   └── __init__.py         # Placeholder for future web helpers
└── utils/
    └── __init__.py         # Placeholder for future utilities
```

Currently only `document/` helpers are fully implemented. The `data/`, `web/`, and `utils/` directories are placeholders for future expansion.

### How It Works

1. **Auto-Discovery**: When `SandboxExecutor` initializes, it recursively scans the `helpers/` directory
2. **VFS Loading**: All `.py` files are loaded into the VFS at `/home/pyodide/`
3. **Import Path**: Pyodide's default `sys.path` includes `/home/pyodide`, making helpers importable
4. **Persistence**: Helpers persist in VFS across executions within the same thread

## Using Helpers in Sandbox Code

### Basic Import

```python
from document.docx_ooxml import docx_add_comment, unzip_docx_like
from document.pdf_manipulation import merge_pdfs

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

docx_bytes = open('/tmp/input.docx', 'rb').read()
commented = docx_add_comment(docx_bytes, 0, "TODO: Expand this")
open('/tmp/commented.docx', 'wb').write(commented)

pptx_bytes = open('/tmp/presentation.pptx', 'rb').read()
texts = pptx_extract_text(pptx_bytes)
print(f"Found {len(texts)} slides with text")
```

## Creating New Helpers

### Step 1: Choose a Category

Organize your helper by domain:

- **document/** -- Document processing (Word, PDF, Excel, PowerPoint)
- **data/** -- Data manipulation (CSV, JSON, XML, databases)
- **web/** -- Web scraping, HTML/markdown processing
- **utils/** -- General utilities

### Step 2: Create the Helper File

Place your `.py` file in the appropriate category directory. Use pure Python (stdlib) when possible to avoid micropip dependencies.

```python
"""
Example helper: Pure OOXML manipulation for Word documents.
Uses xml.etree.ElementTree and zipfile -- no external dependencies.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
}

def docx_extract_text(docx_bytes: bytes) -> str:
    """Extract all text from a Word document."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes), 'r') as zf:
        doc = ET.fromstring(zf.read("word/document.xml"))
    paragraphs = doc.findall(".//w:p", NS)
    return "\n".join(
        "".join(t.text or "" for t in p.findall(".//w:t", NS))
        for p in paragraphs
    )
```

### Step 3: Update manifest.json

Add metadata and documentation for your helper:

```json
{
  "version": "1.0.0",
  "helpers": {
    "document.docx_ooxml": {
      "description": "Pure OOXML manipulation for Word documents",
      "functions": [
        {
          "name": "docx_extract_text",
          "description": "Extract all text from a Word document",
          "parameters": ["docx_bytes"],
          "returns": "str"
        }
      ],
      "dependencies": {
        "stdlib": ["xml.etree.ElementTree", "zipfile", "io"],
        "micropip": []
      }
    }
  }
}
```

### Step 4: No Code Changes Needed

The helper system auto-discovers new files. Just:

1. Add your `.py` file to the appropriate category
2. Update `manifest.json` with documentation
3. Restart the executor (or implement hot-reload)

## Testing Helpers

### Unit Tests

```python
def test_docx_add_comment():
    from document.docx_ooxml import docx_add_comment

    docx_bytes = create_test_docx()
    result = docx_add_comment(docx_bytes, 0, "Test comment")
    assert b"Test comment" in result
```

### Integration Tests

Test helpers through the backend:

```python
async def test_backend_uses_helper(backend):
    result = await backend.aexecute(
        'python -c "from document.docx_ooxml import docx_extract_text; print(\'ok\')"'
    )
    assert result.exit_code == 0
```

## Best Practices

1. **Pure Python When Possible** -- Prefer stdlib over external dependencies. Keep helpers self-contained. Document all dependencies in manifest.
2. **Clear Documentation** -- Include docstrings with examples, type hints for parameters, and clear error messages.
3. **Error Handling** -- Validate inputs, raise descriptive exceptions, handle edge cases.
4. **Performance** -- Lazy imports for heavy dependencies, cache expensive operations.
5. **Testing** -- Unit test each function, integration test with the backend, test error cases.

## Troubleshooting

### Helper Not Found

```
ImportError: No module named 'document.docx_ooxml'
```

Check that the file exists in `helpers/document/docx_ooxml.py` and VFS loading completed successfully.

### Import Works But Function Missing

```
AttributeError: module 'document.docx_ooxml' has no attribute 'docx_add_comment'
```

Check that the function is defined and not prefixed with `_` (private).

### Dependency Missing

```
ModuleNotFoundError: No module named 'pypdf'
```

Install with micropip before importing the helper:

```python
import micropip
await micropip.install('pypdf')
from document.pdf_manipulation import merge_pdfs
```

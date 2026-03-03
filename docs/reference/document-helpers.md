# Document Helpers API Reference

API reference for document processing helper functions available inside the Pyodide sandbox at `/home/pyodide/`.

For a how-to guide on using and creating helpers, see [Document Processing](../how-to/document-processing.md).

## Word (DOCX) -- `document.docx_ooxml`

Pure OOXML manipulation using `xml.etree.ElementTree` and `zipfile`. No external dependencies.

```python
from document.docx_ooxml import (
    docx_extract_text,
    docx_extract_paragraphs,
    docx_read_tables,
    docx_add_comment,
    docx_find_replace,
    docx_to_markdown,
    unzip_docx_like,
    zip_docx_like,
)
```

| Function | Parameters | Returns | Description |
|----------|-----------|---------|-------------|
| `docx_extract_text` | `docx_bytes: bytes` | `str` | Extract all text from a Word document |
| `docx_extract_paragraphs` | `docx_bytes: bytes` | `list[str]` | Extract paragraphs as a list |
| `docx_read_tables` | `docx_bytes: bytes` | `list[list[list[str]]]` | Extract all tables |
| `docx_add_comment` | `docx_bytes, paragraph_index, text, author?, initials?, date_iso?` | `bytes` | Add a comment to a paragraph |
| `docx_find_replace` | `docx_bytes: bytes, replacements: dict[str, str]` | `bytes` | Find and replace text |
| `docx_to_markdown` | `docx_bytes: bytes` | `str` | Convert document to markdown |
| `unzip_docx_like` | `docx_bytes: bytes` | `dict[str, bytes]` | Extract all files from a docx |
| `zip_docx_like` | `parts: dict[str, bytes]` | `bytes` | Create a docx from parts |

## Excel (XLSX) -- `document.xlsx_helpers`

Helper functions using openpyxl. **Requires:** `await micropip.install('openpyxl')` before use.

```python
import micropip
await micropip.install('openpyxl')

from document.xlsx_helpers import (
    xlsx_get_sheet_names,
    xlsx_read_cells,
    xlsx_write_cells,
    xlsx_to_dict,
    xlsx_has_formulas,
    xlsx_read_with_formulas,
)
```

| Function | Parameters | Returns | Description |
|----------|-----------|---------|-------------|
| `xlsx_get_sheet_names` | `xlsx_bytes: bytes` | `list[str]` | Get sheet names |
| `xlsx_read_cells` | `xlsx_bytes, sheet_name, cells: list[str]` | `dict[str, Any]` | Read specific cells (e.g., `['A1', 'B2']`) |
| `xlsx_write_cells` | `xlsx_bytes, sheet_name, values: dict[str, Any]` | `bytes` | Write to cells |
| `xlsx_to_dict` | `xlsx_bytes, sheet_name` | `list[dict]` | Convert sheet to list of dicts (header row as keys) |
| `xlsx_has_formulas` | `xlsx_bytes: bytes` | `dict[str, list[str]]` | Find cells with formulas per sheet |
| `xlsx_read_with_formulas` | `xlsx_bytes: bytes` | `dict` | Read both values and formula text |

## PowerPoint (PPTX) -- `document.pptx_ooxml`

Pure OOXML manipulation for PowerPoint presentations. No external dependencies.

```python
from document.pptx_ooxml import (
    pptx_extract_text,
    pptx_inventory,
    pptx_replace_text,
    pptx_rearrange,
    pptx_contact_sheet_html,
    unzip_pptx_like,
    zip_pptx_like,
)
```

| Function | Parameters | Returns | Description |
|----------|-----------|---------|-------------|
| `pptx_extract_text` | `pptx_bytes: bytes` | `dict[int, list[str]]` | Extract text from all slides |
| `pptx_inventory` | `pptx_bytes: bytes` | `dict[str, list[dict]]` | Inventory all text elements per slide |
| `pptx_replace_text` | `pptx_bytes, replacements: dict[str, dict[str, str]]` | `bytes` | Find and replace text across slides |
| `pptx_rearrange` | `pptx_bytes, new_order: list[int]` | `bytes` | Reorder slides |
| `pptx_contact_sheet_html` | `pptx_bytes: bytes` | `str` | Generate HTML preview of all slides |
| `unzip_pptx_like` | `pptx_bytes: bytes` | `dict[str, bytes]` | Extract all files from a pptx |
| `zip_pptx_like` | `parts: dict[str, bytes]` | `bytes` | Create a pptx from parts |

## PDF -- `document.pdf_manipulation`

PDF operations. **Requires:** `await micropip.install('pypdf')` before use.

```python
import micropip
await micropip.install('pypdf')

from document.pdf_manipulation import (
    pdf_merge,
    pdf_split,
    pdf_extract_text,
    pdf_rotate_pages,
    pdf_num_pages,
    pdf_get_metadata,
)
```

| Function | Parameters | Returns | Description |
|----------|-----------|---------|-------------|
| `pdf_merge` | `pdf_list: list[bytes]` | `bytes` | Merge multiple PDFs |
| `pdf_split` | `pdf_bytes, start, end` | `bytes` | Extract page range |
| `pdf_extract_text` | `pdf_bytes: bytes` | `str` | Extract all text |
| `pdf_rotate_pages` | `pdf_bytes, rotation: int` | `bytes` | Rotate all pages |
| `pdf_num_pages` | `pdf_bytes: bytes` | `int` | Get page count |
| `pdf_get_metadata` | `pdf_bytes: bytes` | `dict` | Get PDF metadata |

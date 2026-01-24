"""
OOXML manipulation helpers for Word documents.

Pure Python implementation using xml.etree.ElementTree and zipfile.
No external dependencies required.
"""

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

import defusedxml.ElementTree as DefusedET

# Namespace mappings for OOXML
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}

# File paths within docx archive
_DOCUMENT_XML = "word/document.xml"
_COMMENTS_XML = "word/comments.xml"

# XPath expressions
_XPATH_TEXT = ".//w:t"

# Register namespaces for cleaner XML output
for prefix, uri in NS.items():
    if prefix not in ("rel", "ct"):
        ET.register_namespace(prefix, uri)


def unzip_docx_like(docx_bytes: bytes) -> dict[str, bytes]:
    """Extract all files from a docx (zip archive)."""
    parts = {}
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
        for name in zf.namelist():
            parts[name] = zf.read(name)
    return parts


def zip_docx_like(parts: dict[str, bytes]) -> bytes:
    """Create a docx (zip archive) from parts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx_add_comment(
    docx_bytes: bytes,
    paragraph_index: int,
    text: str,
    author: str = "Pyodide Tool",
    initials: str = "PT",
    date_iso: str | None = None,
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
    doc = DefusedET.fromstring(parts[_DOCUMENT_XML])
    paras = doc.findall(".//w:body/w:p", NS)
    if paragraph_index < 0 or paragraph_index >= len(paras):
        raise IndexError(f"paragraph_index {paragraph_index} out of range (0-{len(paras) - 1})")
    p = paras[paragraph_index]

    # Ensure comments.xml exists
    if _COMMENTS_XML not in parts:
        comments = ET.Element(f"{{{NS['w']}}}comments")
        parts[_COMMENTS_XML] = ET.tostring(comments, encoding="utf-8", xml_declaration=True)
    else:
        comments = DefusedET.fromstring(parts[_COMMENTS_XML])

    # Ensure relationship to comments.xml
    rels_path = "word/_rels/document.xml.rels"
    if rels_path not in parts:
        rels = ET.Element(
            "Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships"
        )
        parts[rels_path] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
    else:
        rels = DefusedET.fromstring(parts[rels_path])

    # Check if comments relationship exists
    has_comment_rel = False
    for r in rels.findall(".//Relationship", NS):
        if (
            r.get("Type")
            == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
        ):
            has_comment_rel = True
            break

    if not has_comment_rel:
        next_id = f"rId{len(rels.findall('.//Relationship')) + 1}"
        ET.SubElement(
            rels,
            "Relationship",
            {
                "Id": next_id,
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
                "Target": "comments.xml",
            },
        )

    # Generate comment ID
    existing_ids = [
        int(c.get(f"{{{NS['w']}}}id", "0")) for c in comments.findall(".//w:comment", NS)
    ]
    cid = (max(existing_ids) + 1) if existing_ids else 0
    date_iso = date_iso or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )

    # Create comment in comments.xml
    cmt = ET.SubElement(
        comments,
        f"{{{NS['w']}}}comment",
        {
            f"{{{NS['w']}}}id": str(cid),
            f"{{{NS['w']}}}author": author,
            f"{{{NS['w']}}}initials": initials,
            f"{{{NS['w']}}}date": date_iso,
        },
    )
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
    parts[_COMMENTS_XML] = ET.tostring(comments, encoding="utf-8", xml_declaration=True)
    parts[rels_path] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
    parts[_DOCUMENT_XML] = ET.tostring(doc, encoding="utf-8", xml_declaration=True)

    return zip_docx_like(parts)


def docx_extract_text(docx_bytes: bytes) -> str:
    """
    Extract all text from Word document.

    Pure OOXML implementation - extracts all w:t text nodes.

    Args:
        docx_bytes: Word document as bytes

    Returns:
        Plain text content with paragraphs separated by newlines

    Example:
        >>> docx_bytes = open('/tmp/doc.docx', 'rb').read()
        >>> text = docx_extract_text(docx_bytes)
        >>> print(text)
    """
    parts = unzip_docx_like(docx_bytes)

    if _DOCUMENT_XML not in parts:
        return ""

    doc = DefusedET.fromstring(parts[_DOCUMENT_XML])

    # Extract all text nodes
    texts = [t.text or "" for t in doc.findall(_XPATH_TEXT, NS) if t.text]

    return "\n".join(texts)


def docx_extract_paragraphs(docx_bytes: bytes) -> list[str]:
    """
    Extract paragraphs as a list of strings.

    Args:
        docx_bytes: Word document as bytes

    Returns:
        List of paragraph texts

    Example:
        >>> paragraphs = docx_extract_paragraphs(docx_bytes)
        >>> for i, para in enumerate(paragraphs):
        ...     print(f"{i}: {para}")
    """
    parts = unzip_docx_like(docx_bytes)

    if _DOCUMENT_XML not in parts:
        return []

    doc = DefusedET.fromstring(parts[_DOCUMENT_XML])

    paragraphs = []
    for para in doc.findall(".//w:p", NS):
        # Get all text runs in this paragraph
        texts = [t.text or "" for t in para.findall(_XPATH_TEXT, NS)]
        para_text = "".join(texts)
        if para_text:  # Only include non-empty paragraphs
            paragraphs.append(para_text)

    return paragraphs


def docx_read_tables(docx_bytes: bytes) -> list[list[list[str]]]:
    """
    Extract all tables from Word document.

    Args:
        docx_bytes: Word document as bytes

    Returns:
        List of tables, where each table is a list of rows,
        and each row is a list of cell texts

    Example:
        >>> tables = docx_read_tables(docx_bytes)
        >>> for table_idx, table in enumerate(tables):
        ...     print(f"Table {table_idx}:")
        ...     for row in table:
        ...         print(" | ".join(row))
    """
    parts = unzip_docx_like(docx_bytes)

    if _DOCUMENT_XML not in parts:
        return []

    doc = DefusedET.fromstring(parts[_DOCUMENT_XML])

    tables = []
    for table in doc.findall(".//w:tbl", NS):
        table_data = []
        for row in table.findall(".//w:tr", NS):
            row_data = []
            for cell in row.findall(".//w:tc", NS):
                # Get all text in cell
                cell_texts = [t.text or "" for t in cell.findall(_XPATH_TEXT, NS)]
                row_data.append("".join(cell_texts))
            table_data.append(row_data)
        tables.append(table_data)

    return tables


def docx_find_replace(docx_bytes: bytes, replacements: dict[str, str]) -> bytes:
    """
    Find and replace text in Word document.

    Replaces exact text matches in all text nodes.

    Args:
        docx_bytes: Word document as bytes
        replacements: Dictionary mapping old text to new text

    Returns:
        Modified Word document as bytes

    Example:
        >>> replacements = {
        ...     "Company Name": "ACME Corp",
        ...     "2024": "2025"
        ... }
        >>> modified = docx_find_replace(docx_bytes, replacements)
        >>> open('/tmp/updated.docx', 'wb').write(modified)
    """
    parts = unzip_docx_like(docx_bytes)

    if _DOCUMENT_XML not in parts:
        return docx_bytes

    doc = DefusedET.fromstring(parts[_DOCUMENT_XML])

    # Replace in all text nodes
    for text_node in doc.findall(_XPATH_TEXT, NS):
        if text_node.text in replacements:
            text_node.text = replacements[text_node.text]

    parts[_DOCUMENT_XML] = ET.tostring(doc, encoding="utf-8", xml_declaration=True)

    return zip_docx_like(parts)


def docx_to_markdown(docx_bytes: bytes) -> str:
    """
    Convert Word document to Markdown.

    Attempts to use mammoth for rich conversion. Falls back to
    plain text extraction if mammoth is not available.

    Args:
        docx_bytes: Word document as bytes

    Returns:
        Markdown text

    Requirements:
        - Optional: await micropip.install('mammoth') for rich conversion
        - Fallback: Uses plain text extraction (no formatting)

    Example:
        >>> # Try with mammoth
        >>> import micropip
        >>> await micropip.install('mammoth')
        >>> markdown = docx_to_markdown(docx_bytes)
        >>>
        >>> # Or fallback to plain text
        >>> markdown = docx_to_markdown(docx_bytes)  # Works without mammoth
    """
    try:
        import mammoth

        result = mammoth.convert_to_markdown(io.BytesIO(docx_bytes))
        return result.value
    except ImportError:
        # Fallback: extract plain text from w:t nodes
        return docx_extract_text(docx_bytes)

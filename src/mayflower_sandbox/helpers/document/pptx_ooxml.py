"""
OOXML manipulation helpers for PowerPoint presentations.

Pure Python implementation using xml.etree.ElementTree and zipfile.
No external dependencies required.
"""

import io
import xml.etree.ElementTree as ET
import zipfile

import defusedxml.ElementTree as DefusedET

# Namespace mappings for PowerPoint OOXML
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# File paths within pptx archive
_PRESENTATION_XML = "ppt/presentation.xml"


def unzip_pptx_like(pptx_bytes: bytes) -> dict[str, bytes]:
    """Extract all files from a pptx (zip archive)."""
    parts = {}
    with zipfile.ZipFile(io.BytesIO(pptx_bytes), "r") as zf:
        for name in zf.namelist():
            parts[name] = zf.read(name)
    return parts


def zip_pptx_like(parts: dict[str, bytes]) -> bytes:
    """Create a pptx (zip archive) from parts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def pptx_extract_text(pptx_bytes: bytes) -> dict[int, list[str]]:
    """
    Extract all text from PowerPoint slides.

    Args:
        pptx_bytes: PowerPoint file as bytes

    Returns:
        Dictionary mapping slide number to list of text strings found on that slide

    Example:
        >>> pptx_bytes = open('/tmp/presentation.pptx', 'rb').read()
        >>> texts = pptx_extract_text(pptx_bytes)
        >>> print(texts[1])  # Text from first slide
        ['Title Text', 'Subtitle Text', 'Bullet point 1']
    """
    parts = unzip_pptx_like(pptx_bytes)
    result = {}

    for name, data in parts.items():
        if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
            try:
                slide = DefusedET.fromstring(data)
                # Find all text nodes (a:t)
                texts = [t.text or "" for t in slide.findall(".//a:t", NS) if t.text]
                # Extract slide number from filename: ppt/slides/slide1.xml -> 1
                # Get the basename (slide1.xml), remove "slide" prefix and ".xml" suffix
                basename = name.split("/")[-1]  # slide1.xml
                idx = int(basename.replace("slide", "").replace(".xml", ""))
                result[idx] = texts
            except (ET.ParseError, ValueError, IndexError):
                continue

    return dict(sorted(result.items()))


def pptx_inventory(pptx_bytes: bytes) -> dict:
    """
    Inventory all text elements in PowerPoint slides.

    Provides detailed information about each text element for
    advanced manipulation.

    Args:
        pptx_bytes: PowerPoint file as bytes

    Returns:
        Dictionary mapping slide paths to list of text elements with metadata

    Example:
        >>> inv = pptx_inventory(pptx_bytes)
        >>> print(inv['ppt/slides/slide1.xml'])
        [{'xpath': './/a:r', 'text': 'Title'}, {'xpath': './/a:r', 'text': 'Content'}]
    """
    parts = unzip_pptx_like(pptx_bytes)
    inv = {}

    for name, data in parts.items():
        if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
            try:
                slide = DefusedET.fromstring(data)
                items = []
                # Find all text runs
                for run in slide.findall(".//a:r", NS):
                    t = run.find("./a:t", NS)
                    if t is not None and t.text:
                        items.append({"xpath": ".//a:r", "text": t.text})
                inv[name] = items
            except ET.ParseError:
                continue

    return inv


def pptx_replace_text(pptx_bytes: bytes, replacements: dict[str, dict[str, str]]) -> bytes:
    """
    Replace text in PowerPoint slides.

    Shape-agnostic replacement that finds and replaces text
    across all slides.

    Args:
        pptx_bytes: PowerPoint file as bytes
        replacements: Nested dict mapping slide paths to text replacements
                     Format: {"ppt/slides/slide1.xml": {"Old text": "New text"}}

    Returns:
        Modified PowerPoint file as bytes

    Example:
        >>> replacements = {
        ...     "ppt/slides/slide1.xml": {"Company Name": "ACME Corp"},
        ...     "ppt/slides/slide2.xml": {"2024": "2025"}
        ... }
        >>> modified = pptx_replace_text(pptx_bytes, replacements)
        >>> open('/tmp/updated.pptx', 'wb').write(modified)
    """
    parts = unzip_pptx_like(pptx_bytes)

    for path, mapping in replacements.items():
        if path not in parts:
            continue

        try:
            slide = DefusedET.fromstring(parts[path])
            # Replace text in all text nodes
            for tnode in slide.findall(".//a:t", NS):
                if tnode.text in mapping:
                    tnode.text = mapping[tnode.text]
            parts[path] = ET.tostring(slide, encoding="utf-8", xml_declaration=True)
        except ET.ParseError:
            continue

    return zip_pptx_like(parts)


def pptx_rearrange(pptx_bytes: bytes, new_order: list[int]) -> bytes:
    """
    Rearrange slides in PowerPoint presentation.

    Reorders slides by modifying the presentation.xml slide list.

    Args:
        pptx_bytes: PowerPoint file as bytes
        new_order: List of slide numbers in desired order (1-indexed)

    Returns:
        Modified PowerPoint file as bytes

    Example:
        >>> # Swap first two slides
        >>> new_order = [2, 1, 3, 4, 5]
        >>> modified = pptx_rearrange(pptx_bytes, new_order)
        >>> open('/tmp/reordered.pptx', 'wb').write(modified)
    """
    parts = unzip_pptx_like(pptx_bytes)

    if _PRESENTATION_XML not in parts:
        return pptx_bytes

    try:
        pres = DefusedET.fromstring(parts[_PRESENTATION_XML])
        slide_id_list = pres.find(".//p:slide_id_list", NS)

        if slide_id_list is None:
            return pptx_bytes

        # Load relationships to map rId -> slide file
        rels_path = "ppt/_rels/presentation.xml.rels"
        if rels_path not in parts:
            return pptx_bytes

        rels = DefusedET.fromstring(parts[rels_path])
        relmap = {}
        for rel in rels.findall(".//Relationship"):
            relmap[rel.get("Id")] = rel

        # Get current slide IDs
        ids = slide_id_list.findall("./p:sldId", NS)
        if len(ids) != len(new_order):
            return pptx_bytes

        # Build mapping of slide number -> rId
        slide_to_rid = {}
        for sld in ids:
            rid = sld.get(f"{{{NS['r']}}}id")
            if rid in relmap:
                target = relmap[rid].get("Target")
                # Extract slide number from target like "slides/slide1.xml"
                try:
                    # Get basename and extract number
                    basename = target.split("/")[-1]  # slide1.xml
                    slide_num = int(basename.replace("slide", "").replace(".xml", ""))
                    slide_to_rid[slide_num] = rid
                except (IndexError, ValueError):
                    continue

        # Reorder by updating the rId references
        for i, sld in enumerate(ids):
            new_slide_num = new_order[i]
            if new_slide_num in slide_to_rid:
                sld.set(f"{{{NS['r']}}}id", slide_to_rid[new_slide_num])

        parts[_PRESENTATION_XML] = ET.tostring(pres, encoding="utf-8", xml_declaration=True)
        parts[rels_path] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
    except (ET.ParseError, KeyError, ValueError):
        return pptx_bytes

    return zip_pptx_like(parts)


def pptx_contact_sheet_html(pptx_bytes: bytes) -> str:
    """
    Generate HTML contact sheet showing all slide text.

    Creates a simple HTML preview of presentation content
    without rendering slides.

    Args:
        pptx_bytes: PowerPoint file as bytes

    Returns:
        HTML string showing slide text

    Example:
        >>> html = pptx_contact_sheet_html(pptx_bytes)
        >>> open('/tmp/preview.html', 'w').write(html)
        >>> # Open preview.html in browser to see slide content
    """
    slides = pptx_extract_text(pptx_bytes)
    rows = []

    for idx, texts in slides.items():
        # Combine all text from slide, truncate at 200 chars
        preview = " · ".join([t.strip() for t in texts if t.strip()])[:200]
        rows.append(
            f'<div style="border:1px solid #ccc;padding:8px;margin:6px">'
            f"<b>Slide {idx}</b><br>{preview}</div>"
        )

    return "<html><body><h3>Slides</h3>" + "\n".join(rows) + "</body></html>"

"""
PDF manipulation helpers using pypdf.

Pure Python PDF operations. Requires pypdf via micropip.

Usage:
    import micropip
    await micropip.install('pypdf')
    from document.pdf_manipulation import pdf_merge, pdf_split, pdf_extract_text
"""

import io
from typing import Any


def pdf_num_pages(pdf_bytes: bytes) -> int:
    """
    Get number of pages in PDF.

    Args:
        pdf_bytes: PDF file as bytes

    Returns:
        Number of pages

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> pdf_bytes = open('/tmp/document.pdf', 'rb').read()
        >>> num_pages = pdf_num_pages(pdf_bytes)
        >>> print(f"PDF has {num_pages} pages")
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return len(reader.pages)


def pdf_merge(pdf_list: list[bytes]) -> bytes:
    """
    Merge multiple PDF files into one.

    Args:
        pdf_list: List of PDF file bytes to merge

    Returns:
        Merged PDF as bytes

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> pdf1 = open('/tmp/doc1.pdf', 'rb').read()
        >>> pdf2 = open('/tmp/doc2.pdf', 'rb').read()
        >>> merged = pdf_merge([pdf1, pdf2])
        >>> open('/tmp/merged.pdf', 'wb').write(merged)
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    writer = PdfWriter()

    for pdf_bytes in pdf_list:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def pdf_split(pdf_bytes: bytes) -> list[bytes]:
    """
    Split PDF into individual page PDFs.

    Args:
        pdf_bytes: PDF file as bytes

    Returns:
        List of single-page PDF bytes

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> pdf_bytes = open('/tmp/document.pdf', 'rb').read()
        >>> pages = pdf_split(pdf_bytes)
        >>> for i, page_pdf in enumerate(pages, 1):
        ...     open(f'/tmp/page_{i}.pdf', 'wb').write(page_pdf)
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    result = []

    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        result.append(output.getvalue())

    return result


def pdf_rotate(pdf_bytes: bytes, angle: int = 90, pages: list[int] | None = None) -> bytes:
    """
    Rotate pages in PDF.

    Args:
        pdf_bytes: PDF file as bytes
        angle: Rotation angle (90, 180, 270 degrees clockwise)
        pages: List of 0-based page indices to rotate (None = all pages)

    Returns:
        Rotated PDF as bytes

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> # Rotate all pages 90 degrees
        >>> rotated = pdf_rotate(pdf_bytes, 90)
        >>>
        >>> # Rotate only first and third pages
        >>> rotated = pdf_rotate(pdf_bytes, 180, pages=[0, 2])
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    pages_set = set(pages) if pages else set(range(len(reader.pages)))

    for i, page in enumerate(reader.pages):
        if i in pages_set:
            # Try new API first, fallback to old API
            try:
                page.rotate(angle)
            except (AttributeError, TypeError):
                page.rotate_clockwise(angle)

        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def pdf_extract_text(pdf_bytes: bytes) -> str:
    """
    Extract all text from PDF.

    Uses pypdf for text extraction. For scanned PDFs with no text,
    this will return empty or minimal content. Use OCR for scanned PDFs.

    Args:
        pdf_bytes: PDF file as bytes

    Returns:
        Extracted text with pages separated by newlines

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> pdf_bytes = open('/tmp/document.pdf', 'rb').read()
        >>> text = pdf_extract_text(pdf_bytes)
        >>> print(text)
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    texts = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            texts.append(page_text)

    return "\n\n".join(texts)


def pdf_extract_text_by_page(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """
    Extract text from PDF with page numbers.

    Args:
        pdf_bytes: PDF file as bytes

    Returns:
        List of dicts with 'page' (1-indexed) and 'text' keys

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> pages = pdf_extract_text_by_page(pdf_bytes)
        >>> for page_info in pages:
        ...     print(f"Page {page_info['page']}: {page_info['text'][:100]}...")
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    result = []

    for page_num, page in enumerate(reader.pages, 1):
        page_text = page.extract_text() or ""
        result.append({"page": page_num, "text": page_text})

    return result


def pdf_get_metadata(pdf_bytes: bytes) -> dict[str, Any]:
    """
    Get PDF metadata.

    Args:
        pdf_bytes: PDF file as bytes

    Returns:
        Dictionary with metadata (title, author, subject, etc.)

    Requirements:
        - pypdf: await micropip.install('pypdf')

    Example:
        >>> metadata = pdf_get_metadata(pdf_bytes)
        >>> print(f"Title: {metadata.get('title')}")
        >>> print(f"Author: {metadata.get('author')}")
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError("pypdf is required. Install with: await micropip.install('pypdf')") from e

    reader = PdfReader(io.BytesIO(pdf_bytes))
    metadata = reader.metadata

    if metadata:
        return {
            "title": metadata.get("/Title"),
            "author": metadata.get("/Author"),
            "subject": metadata.get("/Subject"),
            "creator": metadata.get("/Creator"),
            "producer": metadata.get("/Producer"),
            "creation_date": metadata.get("/CreationDate"),
            "modification_date": metadata.get("/ModDate"),
        }
    return {}

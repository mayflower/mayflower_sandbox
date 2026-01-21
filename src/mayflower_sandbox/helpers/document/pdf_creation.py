"""
PDF creation helpers using fpdf2.

Pure Python PDF generation with Unicode support via font loading.
Automatically installs fpdf2 via micropip in Pyodide.

Usage:
    from document.pdf_creation import pdf_create_simple, pdf_create_with_unicode
    # fpdf2 is automatically installed if not present
"""

# Import from package __init__ (works when loaded into VFS at /home/pyodide/document/)
try:
    from . import ensure_package
except ImportError:
    # Fallback for when called from document.pdf_creation directly in Pyodide
    from document import ensure_package


async def load_dejavu_font() -> bytes:
    """
    Fetch DejaVu Sans TrueType font from CDN.

    Returns:
        Font file as bytes

    Requirements:
        - Pyodide environment with pyfetch available

    Example:
        >>> font_bytes = await load_dejavu_font()
        >>> with open('/tmp/DejaVuSans.ttf', 'wb') as f:
        ...     f.write(font_bytes)
    """
    from pyodide.http import pyfetch

    # Fetch from jsDelivr CDN (reliable and fast)
    url = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf"
    response = await pyfetch(url)

    if response.status != 200:
        raise RuntimeError(f"Failed to fetch font: HTTP {response.status}")

    return await response.bytes()


async def pdf_create_with_unicode(
    title: str, content_paragraphs: list[str], output_path: str = "/tmp/document.pdf"
) -> str:
    """
    Create a PDF with Unicode support by loading DejaVu font.

    Handles special characters like π, °, µ, €, etc.

    Args:
        title: Document title
        content_paragraphs: List of text paragraphs (can contain Unicode)
        output_path: Where to save the PDF

    Returns:
        Path to the created PDF

    Requirements:
        - fpdf2: await micropip.install('fpdf2')

    Example:
        >>> paragraphs = [
        ...     "Temperature: 180°C (π radians)",
        ...     "Measurement: 5.2µm ± 0.1µm",
        ...     "Cost: €125.50"
        ... ]
        >>> path = await pdf_create_with_unicode("Lab Report", paragraphs)
        >>> print(f"Created: {path}")
    """
    ensure_package("fpdf2", "fpdf")
    from fpdf import FPDF, XPos, YPos

    # Load Unicode font
    font_bytes = await load_dejavu_font()
    font_path = "/tmp/DejaVuSans.ttf"
    # Use run_in_executor for non-blocking file write (works in both CPython and Pyodide)
    import asyncio
    from pathlib import Path

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: Path(font_path).write_bytes(font_bytes))

    # Create PDF
    pdf = FPDF()
    pdf.add_page()

    # Register the Unicode font
    pdf.add_font("DejaVu", "", font_path)
    pdf.set_font("DejaVu", size=12)

    # Add title
    pdf.set_font("DejaVu", size=16)
    pdf.cell(0, 10, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # Add content
    pdf.set_font("DejaVu", size=12)
    for paragraph in content_paragraphs:
        pdf.multi_cell(0, 10, paragraph, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)

    # Save
    pdf.output(output_path)
    return output_path


def pdf_create_simple(
    title: str,
    content_paragraphs: list[str],
    output_path: str = "/tmp/document.pdf",
    ascii_replacements: dict[str, str] | None = None,
) -> str:
    """
    Create a PDF using built-in fonts (ASCII only).

    For Unicode characters, either use pdf_create_with_unicode() or provide
    ascii_replacements mapping.

    Args:
        title: Document title
        content_paragraphs: List of text paragraphs
        output_path: Where to save the PDF
        ascii_replacements: Optional dict mapping Unicode to ASCII (e.g., {'π': 'pi', '°': 'deg'})

    Returns:
        Path to the created PDF

    Requirements:
        - fpdf2: await micropip.install('fpdf2')

    Example:
        >>> replacements = {'π': 'pi', '°': 'deg', 'µ': 'micro'}
        >>> paragraphs = ["Temperature: 180°C", "π radians"]
        >>> path = pdf_create_simple("Report", paragraphs, ascii_replacements=replacements)
    """
    ensure_package("fpdf2", "fpdf")
    from fpdf import FPDF, XPos, YPos

    # Apply ASCII replacements if provided
    if ascii_replacements:
        title = _replace_unicode(title, ascii_replacements)
        content_paragraphs = [_replace_unicode(p, ascii_replacements) for p in content_paragraphs]

    # Create PDF with built-in font
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    # Add title
    pdf.set_font("Helvetica", "B", size=16)
    pdf.cell(0, 10, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # Add content
    pdf.set_font("Helvetica", size=12)
    for paragraph in content_paragraphs:
        pdf.multi_cell(0, 10, paragraph, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)

    # Save
    pdf.output(output_path)
    return output_path


def _replace_unicode(text: str, replacements: dict[str, str]) -> str:
    """Replace Unicode characters with ASCII equivalents."""
    for unicode_char, ascii_replacement in replacements.items():
        text = text.replace(unicode_char, ascii_replacement)
    return text


# Common ASCII replacement mappings
COMMON_UNICODE_REPLACEMENTS = {
    "π": "pi",
    "°": "deg",
    "µ": "micro",
    "±": "+/-",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "×": "x",
    "÷": "/",
    "≈": "~=",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "θ": "theta",
    "λ": "lambda",
    "σ": "sigma",
    "Ω": "Omega",
}

"""Document processing helpers for Word, PDF, Excel, PowerPoint."""


# Allowlist of packages that can be auto-installed via micropip
# This prevents arbitrary package installation for security
ALLOWED_PACKAGES = {
    # Document processing
    "openpyxl",
    "python-pptx",
    "python-docx",
    "pypdf",
    "fpdf2",
    # Data analysis
    "pandas",
    "numpy",
    "scipy",
    # Visualization
    "matplotlib",
    "seaborn",
    "plotly",
    # Utilities
    "requests",
    "beautifulsoup4",
    "lxml",
    "pillow",
    "xlrd",
    "xlsxwriter",
}


def ensure_package(package_name: str, import_name: str | None = None) -> None:
    """
    Ensure a package is installed, auto-installing via micropip in Pyodide.

    Args:
        package_name: Package name for micropip (e.g., 'openpyxl')
        import_name: Import name if different from package name (e.g., 'pptx' for 'python-pptx')

    Raises:
        SecurityError: If package is not in the allowlist
    """
    if import_name is None:
        import_name = package_name

    try:
        __import__(import_name)
    except ImportError:
        # Check if we're in Pyodide
        import sys

        if "pyodide" in sys.modules or "micropip" in sys.modules:
            # Security: Check package allowlist
            if package_name not in ALLOWED_PACKAGES:
                raise PermissionError(
                    f"Package '{package_name}' is not in the allowlist. "
                    f"Allowed packages: {', '.join(sorted(ALLOWED_PACKAGES))}"
                )
            # Auto-install in Pyodide
            import asyncio

            import micropip

            # Get or create event loop
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Install package
            loop.run_until_complete(micropip.install(package_name))

            # Try importing again
            __import__(import_name)
        else:
            raise ImportError(
                f"{import_name} is required. "
                f"Install with: pip install {package_name} (regular Python) "
                f"or await micropip.install('{package_name}') (Pyodide)"
            )

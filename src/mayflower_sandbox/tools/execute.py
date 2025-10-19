"""
ExecutePythonTool - Execute Python code in sandbox.
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.callbacks import AsyncCallbackManagerForToolRun

from mayflower_sandbox.tools.base import SandboxTool
from mayflower_sandbox.sandbox_executor import SandboxExecutor

logger = logging.getLogger(__name__)

# Error history cache: thread_id -> list of errors
_error_history: dict[str, list[dict]] = {}

# Maximum errors to keep
MAX_ERROR_HISTORY = 5


def get_error_history(thread_id: str) -> list[dict]:
    """Get error history for thread."""
    return _error_history.get(thread_id, [])


def add_error_to_history(thread_id: str, code_snippet: str, error: str):
    """Add error to history."""
    if thread_id not in _error_history:
        _error_history[thread_id] = []

    _error_history[thread_id].append({"code_snippet": code_snippet, "error": error})

    # Keep only recent errors
    if len(_error_history[thread_id]) > MAX_ERROR_HISTORY:
        _error_history[thread_id] = _error_history[thread_id][-MAX_ERROR_HISTORY:]


class ExecutePythonInput(BaseModel):
    """Input schema for ExecutePythonTool."""

    code: str = Field(
        description="Python code to execute in the sandbox. Use print() to show output."
    )


class ExecutePythonTool(SandboxTool):
    """
    Tool for executing Python code in a sandboxed Pyodide environment.

    Files are automatically synced with PostgreSQL VFS and persist across executions.
    """

    name: str = "execute_python"
    description: str = """Execute Python code in a secure Pyodide sandbox environment.

The sandbox has access to a persistent filesystem backed by PostgreSQL.
Files created in /tmp or /data will persist across executions.

PRE-INSTALLED PACKAGES (use directly):
- Standard library: json, csv, math, random, datetime, sqlite3, etc.
- Data science: numpy, pandas, matplotlib, scipy
- HTTP: requests, aiohttp

DOCUMENT PROCESSING PACKAGES (install via micropip):
⚠️ IMPORTANT: You MUST use 'await' with micropip.install()!

For Excel files:
  import micropip
  await micropip.install('openpyxl')
  from openpyxl import Workbook

For PDF creation:
  import micropip
  await micropip.install('fpdf2')
  from fpdf import FPDF

For PDF manipulation (merge, split, extract):
  import micropip
  await micropip.install('pypdf')
  from pypdf import PdfReader, PdfWriter

For PowerPoint files:
  import micropip
  await micropip.install('python-pptx')
  from pptx import Presentation

For Word documents:
  import micropip
  await micropip.install('python-docx')
  from docx import Document

AVAILABLE HELPER MODULES (pre-loaded):

Document Processing - Word:
  from document.docx_ooxml import (
      docx_extract_text,       # Extract all text
      docx_extract_paragraphs, # Extract paragraphs as list
      docx_read_tables,        # Extract tables
      docx_find_replace,       # Find and replace text
      docx_to_markdown,        # Convert to markdown (uses mammoth if available)
      docx_add_comment,        # Add comment to paragraph
  )
  # Pure OOXML manipulation (no external deps except mammoth for markdown)

Document Processing - PowerPoint:
  from document.pptx_ooxml import (
      pptx_extract_text,       # Extract all slide text
      pptx_replace_text,       # Find and replace text
      pptx_rearrange,          # Reorder slides
      pptx_contact_sheet_html, # Generate HTML preview
      pptx_inventory           # Inventory all text elements
  )
  # Pure OOXML manipulation (no external deps)

Document Processing - PDF:
  from document.pdf_manipulation import (
      pdf_merge,               # Merge PDFs
      pdf_split,               # Split into pages
      pdf_extract_text,        # Extract text
      pdf_rotate,              # Rotate pages
      pdf_num_pages,           # Count pages
  )
  # Requires: await micropip.install('pypdf')

See HELPERS.md for complete documentation and examples.

Examples:
- Create Excel with formulas using openpyxl
- Generate PDFs with fpdf2
- Merge PDFs with pypdf
- Create presentations with python-pptx
- Process Word documents with python-docx
- Add comments to Word docs using docx_add_comment helper
- Data analysis with pandas and matplotlib
"""
    args_schema: type[BaseModel] = ExecutePythonInput

    async def _arun(
        self,
        code: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        """Execute Python code in sandbox."""

        # Get error history
        error_history = get_error_history(self.thread_id)

        # Check for similar previous errors
        code_snippet = code[:200]
        previous_errors = [
            entry["error"] for entry in error_history if entry.get("code_snippet", "")[:100] in code
        ]

        # Create executor with network access for micropip
        executor = SandboxExecutor(
            self.db_pool, self.thread_id, allow_net=True, timeout_seconds=60.0
        )

        # Execute
        result = await executor.execute(code)

        # Track errors
        if not result.success and result.stderr:
            add_error_to_history(self.thread_id, code_snippet, result.stderr)

        # Format response
        response_parts = []

        # Show previous errors FIRST if they exist
        if previous_errors:
            response_parts.append(
                "⚠️ **WARNING - Previous Similar Attempt Failed:**\n"
                "This code is similar to code that failed before:\n"
                + "\n".join(f"```\n{err[:500]}\n```" for err in previous_errors[-2:])
                + "\n**Consider trying a completely different approach.**\n"
            )

        if result.stdout:
            response_parts.append(f"Output:\n{result.stdout}")

        if result.stderr:
            response_parts.append(f"Error:\n{result.stderr}")

        # List created files
        if result.created_files:
            files_str = "\n".join(f"  - {path}" for path in result.created_files)
            response_parts.append(f"Created files:\n{files_str}")

        if result.success:
            return (
                "\n\n".join(response_parts)
                if response_parts
                else "Execution successful (no output)"
            )
        else:
            return "\n\n".join(response_parts) if response_parts else "Execution failed"

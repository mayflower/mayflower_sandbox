"""
ExecutePythonTool - Execute Python code in sandbox.
"""

import logging
import os
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.sandbox_executor import SandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)

# Error history cache: thread_id -> list of analyses
_error_history: dict[str, list[dict]] = {}

# Maximum analyses to keep
MAX_ERROR_HISTORY = 5


def get_error_history(thread_id: str) -> list[dict]:
    """Get error analysis history for thread."""
    return _error_history.get(thread_id, [])


async def analyze_error_with_llm(
    current_code: str,
    current_error: str,
    previous_analysis: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Use LLM to analyze current error, building on previous analysis.

    Returns dict with 'explanation' and 'recommendation' keys.
    """
    try:
        if not os.getenv("OPENAI_API_KEY"):
            return {}

        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-5-mini", temperature=0)

        # Build context from previous analysis
        previous_context = ""
        if previous_analysis and (
            previous_analysis.get("explanation") or previous_analysis.get("recommendation")
        ):
            previous_context = f"""
Previous error analysis:
- Explanation: {previous_analysis.get("explanation", "N/A")}
- Recommendation: {previous_analysis.get("recommendation", "N/A")}

The user tried again and got another error. Analyze if this is:
1. The same root issue (e.g., package unavailable) → recommend completely different approach
2. Progress being made → refine the recommendation
3. A new issue → provide fresh guidance
"""

        analysis_prompt = f"""Analyze this Python execution error in Pyodide (WebAssembly Python).
{previous_context}
Current code:
```python
{current_code}
```

Error:
```
{current_error}
```

CONTEXT:
- Pyodide is Python compiled to WebAssembly - most pure Python packages work
- Built-in packages: pillow, numpy, pandas, matplotlib, scipy, networkx, scikit-learn
- Packages that work via micropip: fpdf2, pypdf, python-pptx, python-docx, openpyxl
- Packages that DON'T work: reportlab (C extensions - use fpdf2 instead!), lxml (use defusedxml)
- Install packages: await micropip.install('package')

CRITICAL RECOMMENDATIONS FOR COMMON TASKS:
- PDF creation: Use fpdf2 (NOT reportlab)
- PDF manipulation: Use pypdf
- Image processing: Use pillow (already built-in)
- Data analysis: Use pandas/numpy (already built-in)

Provide:
1. Explanation: What's actually happening? (1-2 sentences)
2. Recommendation: What to try next? (1-2 sentences)
   - If trying reportlab → recommend fpdf2
   - If package not available → suggest working alternatives

Format:
EXPLANATION: [explanation]
RECOMMENDATION: [recommendation]"""

        response = await llm.ainvoke(analysis_prompt)
        response_text = str(response.content)

        # Parse
        explanation = ""
        recommendation = ""

        if "EXPLANATION:" in response_text:
            explanation = response_text.split("EXPLANATION:")[1].split("RECOMMENDATION:")[0].strip()
        if "RECOMMENDATION:" in response_text:
            recommendation = response_text.split("RECOMMENDATION:")[1].strip()

        return {"explanation": explanation, "recommendation": recommendation}

    except Exception as e:
        logger.warning(f"Failed to analyze error with LLM: {e}")
        return {}


async def add_error_to_history(thread_id: str, code: str, error: str) -> dict[str, str]:
    """Analyze error and add to history. Returns the analysis."""
    if thread_id not in _error_history:
        _error_history[thread_id] = []

    # Get previous analysis
    previous = _error_history[thread_id][-1] if _error_history[thread_id] else None

    # Analyze building on previous
    analysis = await analyze_error_with_llm(code, error, previous)

    # Store only analysis
    _error_history[thread_id].append(
        {
            "explanation": analysis.get("explanation", ""),
            "recommendation": analysis.get("recommendation", ""),
        }
    )

    # Keep recent only
    if len(_error_history[thread_id]) > MAX_ERROR_HISTORY:
        _error_history[thread_id] = _error_history[thread_id][-MAX_ERROR_HISTORY:]

    return analysis


class ExecutePythonInput(BaseModel):
    """Input schema for ExecutePythonTool."""

    code: str = Field(
        description="Python code to execute in the sandbox. Use print() to show output."
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class ExecutePythonTool(SandboxTool):
    """
    Tool for executing Python code in a sandboxed Pyodide environment.

    Files are automatically synced with PostgreSQL VFS and persist across executions.
    """

    name: str = "execute_python"
    description: str = """Execute Python code in a secure Pyodide sandbox environment.

The sandbox has access to a persistent filesystem backed by PostgreSQL.
Files created in /tmp or /data will persist across executions.

PRE-INSTALLED PACKAGES (standard library - use directly):
- json, csv, math, random, datetime, sqlite3, etc.

SCIENTIFIC PACKAGES (install via micropip):
⚠️ IMPORTANT: NumPy, pandas, matplotlib, scipy are available but must be installed first!

  import micropip
  await micropip.install('numpy')
  import numpy as np

DOCUMENT PROCESSING PACKAGES (install via micropip):
⚠️ IMPORTANT: You MUST use 'await' with micropip.install()!

For Excel files:
  import micropip
  await micropip.install('openpyxl')
  from openpyxl import Workbook

For PDF creation (use fpdf2, NOT reportlab):
  import micropip
  await micropip.install('fpdf2')
  from fpdf import FPDF
  # fpdf2 is pure Python and works in Pyodide
  # reportlab has C extensions and does NOT work

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
- Generate PDFs with fpdf2 (simple, pure Python - works great!)
- Merge/split PDFs with pypdf
- Create presentations with python-pptx
- Process Word documents with python-docx
- Add comments to Word docs using docx_add_comment helper
- Data analysis with pandas and matplotlib

⚠️ IMPORTANT PACKAGE NOTES:
- Use fpdf2 for PDF creation (NOT reportlab - it doesn't work in Pyodide)
- Use pypdf for PDF manipulation (merge, split, extract text)
- pillow, numpy, pandas, matplotlib are built-in (no install needed)
"""
    args_schema: type[BaseModel] = ExecutePythonInput

    async def _arun(  # type: ignore[override]
        self,
        code: str,
        tool_call_id: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute Python code in sandbox."""

        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        # Create executor with network access for micropip
        executor = SandboxExecutor(self.db_pool, thread_id, allow_net=True, timeout_seconds=60.0)

        # Execute
        result = await executor.execute(code)

        # Track errors with LLM analysis
        analysis = {}
        if not result.success and result.stderr:
            analysis = await add_error_to_history(thread_id, code, result.stderr)

        # Format response
        response_parts = []

        # Show LLM analysis if this execution failed
        if not result.success and analysis:
            warning_parts = ["⚠️ **Error Analysis:**"]

            if analysis.get("explanation"):
                warning_parts.append(f"**What happened:** {analysis['explanation']}")

            if analysis.get("recommendation"):
                warning_parts.append(f"**Try this instead:** {analysis['recommendation']}")

            response_parts.append("\n".join(warning_parts) + "\n")

        if result.stdout:
            response_parts.append(f"Output:\n{result.stdout}")

        if result.stderr:
            response_parts.append(f"Error:\n{result.stderr}")

        # List created files
        if result.created_files:
            files_str = "\n".join(f"  - {path}" for path in result.created_files)
            response_parts.append(f"Created files:\n{files_str}")

        # Build response message
        if result.success:
            message = (
                "\n\n".join(response_parts)
                if response_parts
                else "Execution successful (no output)"
            )
        else:
            message = "\n\n".join(response_parts) if response_parts else "Execution failed"

        # Update agent state with created files if using LangGraph
        # Return Command to update created_files in state, or plain message otherwise
        if result.created_files:
            try:
                from langchain_core.messages import ToolMessage
                from langgraph.types import Command

                # Build state update with both custom field and ToolMessage
                state_update = {
                    "created_files": result.created_files,
                    "messages": [ToolMessage(content=message, tool_call_id=tool_call_id)],
                }

                return Command(update=state_update, resume=message)  # type: ignore[return-value]
            except ImportError:
                # LangGraph not available, return plain message
                return message
        else:
            return message

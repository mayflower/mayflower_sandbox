"""
ExecuteFromResponseTool - Extract and execute code from conversation.
"""

import logging
import re
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.sandbox_executor import SandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)


class ExecuteFromResponseInput(BaseModel):
    """Input schema for ExecuteFromResponseTool."""

    file_path: str = Field(
        description="Path used in prepare_code() call (e.g., /tmp/visualization.py)"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class ExecuteFromResponseTool(SandboxTool):
    """
    Tool for extracting and executing Python code from conversation history.

    This is Step 2 of the extract-from-response pattern for handling
    large code blocks that cause tool call parameter serialization issues.

    Workflow:
    1. prepare_code() signals code generation
    2. LLM generates code in markdown code block
    3. execute_prepared_code() extracts code from conversation and executes
    """

    name: str = "execute_prepared_code"
    description: str = """Step 2: Execute Python code from your previous response.

Extracts code from the last markdown code block in conversation history
and executes it in the Pyodide sandbox.

The code should have been provided after calling prepare_code(), formatted as:

```python
import matplotlib.pyplot as plt
# Your code here
```

Args:
    file_path: Path specified in prepare_code() call

Returns:
    Execution result (output, files created, errors)
"""
    args_schema: type[BaseModel] = ExecuteFromResponseInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        tool_call_id: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Extract code from conversation and execute."""
        thread_id = self._get_thread_id(run_manager)

        # Access conversation history via callback metadata
        # The maistack agent passes messages in metadata
        metadata = run_manager.metadata if run_manager else {}
        messages = metadata.get("messages", [])

        logger.info(
            f"execute_prepared_code: Received {len(messages)} messages in metadata"
        )

        if not messages:
            return (
                "Error: No conversation history found. "
                "Make sure the agent passes messages in run_manager.metadata."
            )

        # Find last AIMessage (the one with the code)
        last_ai_msg = None
        for msg in reversed(messages):
            # Check for AIMessage by type attribute or class name
            if hasattr(msg, "type") and msg.type == "ai":
                last_ai_msg = msg
                break
            elif msg.__class__.__name__ == "AIMessage":
                last_ai_msg = msg
                break

        if not last_ai_msg:
            return "Error: No AI message found in conversation history"

        content = last_ai_msg.content
        logger.info(
            f"execute_prepared_code: Extracting code from AI message (length: {len(content)})"
        )

        # Extract Python code from markdown
        # Try with language specifier first
        code_match = re.search(r'```python\n(.*?)\n```', content, re.DOTALL)

        if not code_match:
            # Try without language specifier
            code_match = re.search(r'```\n(.*?)\n```', content, re.DOTALL)
            logger.info(
                "execute_prepared_code: No ```python block found, trying generic ```"
            )

        if not code_match:
            # Last resort: look for any code-like content
            # Sometimes LLMs put code without markdown
            logger.warning(
                "execute_prepared_code: No markdown code block found, using full content"
            )
            code = content
        else:
            code = code_match.group(1)
            logger.info(
                f"execute_prepared_code: Extracted {len(code)} chars of code"
            )

        if not code.strip():
            return "Error: Extracted code is empty. Please provide Python code in a markdown code block."

        # Write code to VFS
        vfs = VirtualFilesystem(self.db_pool, thread_id)
        try:
            await vfs.write_file(file_path, code.encode("utf-8"))
            logger.info(
                f"execute_prepared_code: Wrote {len(code)} bytes to {file_path}"
            )
        except Exception as e:
            return f"Error writing code to file: {e}"

        # Execute the code
        executor = SandboxExecutor(self.db_pool, thread_id)
        try:
            result = await executor.execute_from_file(file_path, timeout=300)
            logger.info("execute_prepared_code: Execution completed")
            return result
        except Exception as e:
            logger.error(f"execute_prepared_code: Execution failed: {e}")
            return f"Error executing code: {e}"

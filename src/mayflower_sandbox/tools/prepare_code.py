"""
PrepareCodeTool - Signal code generation for complex Python.
"""

from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.tools.base import SandboxTool


class PrepareCodeInput(BaseModel):
    """Input schema for PrepareCodeTool."""

    file_path: str = Field(
        description="Path where code will be saved (e.g., /tmp/visualization.py)"
    )
    description: str = Field(
        description="Brief description of what the code will do"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class PrepareCodeTool(SandboxTool):
    """
    Tool for signaling that complex Python code will be generated.

    This is Step 1 of the extract-from-response pattern for handling
    large code blocks that cause tool call parameter serialization issues.

    Workflow:
    1. Call prepare_code(file_path, description)
    2. Generate complete Python code in markdown code block
    3. Call execute_prepared_code(file_path) to extract and execute
    """

    name: str = "prepare_code"
    description: str = """Step 1 for complex Python code (20+ lines, subplots, multi-step analysis).

Signal that you will generate Python code in your next response.

After calling this tool, provide the complete Python code in a markdown code block:

```python
import matplotlib.pyplot as plt
# Your complete code here
```

Then call execute_prepared_code() to extract and run the code.

Args:
    file_path: Where to save the code (e.g., /tmp/visualization.py)
    description: Brief description of what the code does

Returns:
    Instructions for next step
"""
    args_schema: type[BaseModel] = PrepareCodeInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        description: str,
        tool_call_id: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Signal code generation preparation."""
        return (
            f"Preparing to generate Python code for: {description}\n\n"
            f"Please provide the complete Python code in a markdown code block now, "
            f"then call execute_prepared_code(file_path='{file_path}') to run it."
        )

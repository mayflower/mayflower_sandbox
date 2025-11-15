"""
ExecuteJavascriptTool - Execute JavaScript/TypeScript code in QuickJS-Wasm sandbox.
"""

import logging
import os
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.javascript_executor import JavascriptSandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)


class ExecuteJavascriptInput(BaseModel):
    """Input schema for ExecuteJavascriptTool."""

    code: str = Field(
        description="JavaScript or TypeScript code to execute in the QuickJS-Wasm sandbox. Use console.log() to show output."
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class ExecuteJavascriptTool(SandboxTool):
    """
    Tool for executing JavaScript/TypeScript code in a sandboxed QuickJS-Wasm environment.

    Files are automatically synced with PostgreSQL VFS and persist across executions.
    Files created by JavaScript are accessible to Python, and vice versa.
    """

    name: str = "javascript_run"
    description: str = """Execute JavaScript or TypeScript code in a secure QuickJS-Wasm sandbox.

⚠️ **EXPERIMENTAL FEATURE**: JavaScript/TypeScript execution in WebAssembly sandbox.

**Runtime**: QuickJS compiled to WebAssembly, hosted in Deno
- Sandboxed execution with no host filesystem or network access
- Fast initialization (~1-5ms vs ~500-1000ms for Python)
- Shared VFS with Python sandbox (files are interchangeable)

⚠️ CRITICAL: Use console.log() to display output! The sandbox only shows what you log.

**FILE OPERATIONS**: You CAN create and read files via VFS functions:
- `writeFile(path, content)` - Create/update file in VFS
- `readFile(path)` - Read file from VFS as string
- `listFiles()` - List all VFS files

Files persist across executions and are accessible from Python sandbox.

**Example - Create JSON file:**
```javascript
const data = {
    name: "Analysis Results",
    values: [1, 2, 3, 4, 5],
    sum: [1, 2, 3, 4, 5].reduce((a, b) => a + b, 0)
};

writeFile('/data/results.json', JSON.stringify(data, null, 2));
console.log('Created results.json');
console.log('Sum:', data.sum);
```

**Example - Read and process file:**
```javascript
const content = readFile('/data/input.txt');
const lines = content.split('\\n');
const processed = lines.map(line => line.toUpperCase());

writeFile('/data/output.txt', processed.join('\\n'));
console.log('Processed', lines.length, 'lines');
```

**Example - Data processing:**
```javascript
const numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const evens = numbers.filter(n => n % 2 === 0);
const squared = evens.map(n => n * n);
const sum = squared.reduce((a, b) => a + b, 0);

console.log('Even numbers:', evens);
console.log('Squared:', squared);
console.log('Sum:', sum);
```

**BUILT-IN JAVASCRIPT FEATURES**:
- Standard JavaScript/ES6+ syntax
- JSON parsing and stringification
- Array methods (map, filter, reduce, etc.)
- String manipulation
- Math operations
- Date and time

**LIMITATIONS**:
- No Node.js built-ins (fs, http, path, etc.) - use VFS functions instead
- No npm packages - use pure JavaScript only
- No async/await for external operations (no fetch, no network)
- No DOM or browser APIs
- No CommonJS or ES modules - code runs in single context

**FILE PATHS**:
- Use absolute paths starting with / (e.g., /data/file.txt)
- Files persist in PostgreSQL VFS
- Same files accessible from Python sandbox

**CROSS-LANGUAGE WORKFLOW**:
1. Python creates `/data/input.json` with analysis data
2. JavaScript reads, processes, and writes `/data/output.json`
3. Python reads the results and continues

Use this tool for:
- JSON manipulation and transformation
- Text processing and string operations
- Data filtering and aggregation
- File format conversion
- Quick calculations without library dependencies
"""
    args_schema: type[BaseModel] = ExecuteJavascriptInput

    async def _arun(  # type: ignore[override]
        self,
        code: str,
        tool_call_id: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute JavaScript/TypeScript code in QuickJS-Wasm sandbox."""

        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        # Create executor (no network access for JavaScript)
        executor = JavascriptSandboxExecutor(
            self.db_pool,
            thread_id,
            allow_net=False,  # Network not yet implemented for JS
            timeout_seconds=60.0,
            stateful=False,  # Stateless by default (fast init)
        )

        # Execute
        result = await executor.execute(code)

        # Format response - keep it clean and user-friendly
        response_parts = []

        # Show execution status if failed
        if not result.success:
            response_parts.append("⚠️ **Execution failed**\n")

        # Show stdout (console.log output)
        if result.stdout:
            response_parts.append(result.stdout.strip())

        # Show stderr (console.error + errors)
        if result.stderr:
            response_parts.append(f"Error:\n{result.stderr}")

        # Show created/modified files with inline images
        if result.created_files:
            # Separate image files from other files
            image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
            image_files = []
            other_files = []

            for path in result.created_files:
                ext = os.path.splitext(path)[1].lower()
                if ext in image_extensions:
                    image_files.append(path)
                else:
                    other_files.append(path)

            # Show image files inline
            if image_files:
                images_md = "\n\n".join(f"![Generated image]({path})" for path in image_files)
                response_parts.append(images_md)

            # Show other files as markdown links
            if other_files:
                files_md = "\n".join(
                    f"- [{os.path.basename(path)}]({path})" for path in other_files
                )
                response_parts.append(files_md)

        # Build response message
        if result.success:
            message = (
                "\n\n".join(response_parts)
                if response_parts
                else "Execution successful (no output)"
            )
        else:
            message = "\n\n".join(response_parts) if response_parts else "Execution failed"

        return message

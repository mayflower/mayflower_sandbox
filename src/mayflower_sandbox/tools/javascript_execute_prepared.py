"""
ExecuteJavascriptCodeTool - Execute JavaScript/TypeScript code from graph state.
"""

import logging
import os
from typing import Annotated

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.javascript_executor import JavascriptSandboxExecutor
from mayflower_sandbox.tools.base import SandboxTool

logger = logging.getLogger(__name__)


class ExecuteJavascriptCodeInput(BaseModel):
    """Input schema for ExecuteJavascriptCodeTool."""

    file_path: str = Field(
        default="/tmp/script.js",
        description="Path where code will be saved (e.g., /tmp/analysis.js, /data/processor.ts)",
    )
    description: str = Field(
        default="JavaScript script", description="Brief description of what the code does"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class ExecuteJavascriptCodeTool(SandboxTool):
    """
    Tool for executing JavaScript/TypeScript code from graph state.

    This is a single-tool solution for handling large JavaScript/TypeScript code blocks
    that cause tool call parameter serialization issues in AG-UI/LangGraph.

    Workflow:
    1. LLM generates JavaScript/TypeScript code and stores in graph state (pending_content_map)
    2. LLM calls javascript_run_prepared(file_path, description)
    3. Tool extracts code from state, writes to VFS, executes

    This avoids passing code through tool call parameters entirely.
    """

    name: str = "javascript_run_prepared"
    description: str = """Execute JavaScript or TypeScript code from graph state.

⚠️ **EXPERIMENTAL FEATURE**: JavaScript/TypeScript execution in WebAssembly sandbox.

Use this for complex JavaScript/TypeScript code (20+ lines, multi-step analysis).

Before calling this tool, generate the complete JavaScript/TypeScript code and it will be
automatically stored in graph state. Then call this tool to execute it.

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

**Example - Data processing:**
```javascript
const data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const evens = data.filter(n => n % 2 === 0);
const squared = evens.map(n => n * n);
const sum = squared.reduce((a, b) => a + b, 0);

console.log('Even numbers:', evens);
console.log('Squared:', squared);
console.log('Sum:', sum);

// Save results
writeFile('/data/results.json', JSON.stringify({
    evens, squared, sum
}, null, 2));
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

Args:
    file_path: Where to save the code (e.g., /tmp/analysis.js, /data/script.ts)
    description: Brief description of what the code does

Returns:
    Execution result (output, files created, errors)
"""
    args_schema: type[BaseModel] = ExecuteJavascriptCodeInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        description: str,
        _state: dict | None = None,
        tool_call_id: str = "",
        _config: dict | None = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute JavaScript/TypeScript code from graph state."""
        # If _state is None, tool is being called without custom state injection
        if _state is None:
            return (
                "Error: This tool requires graph state. "
                "Please use with a custom tool node that injects state, "
                "or use the standard javascript_run tool."
            )

        # Extract thread_id from config (passed by custom_tool_node)
        thread_id = None
        if _config:
            thread_id = _config.get("configurable", {}).get("thread_id")
        if not thread_id:
            thread_id = self._get_thread_id(run_manager)

        # Access code from graph state using tool_call_id
        pending_content_map = _state.get("pending_content_map", {})

        # Debug logging
        logger.info(f"javascript_run_prepared: Looking for tool_call_id={tool_call_id}")
        logger.info(
            f"javascript_run_prepared: pending_content_map keys: {list(pending_content_map.keys())}"
        )
        logger.info(
            f"javascript_run_prepared: pending_content_map sizes: "
            f"{[(k[:12], len(v)) for k, v in pending_content_map.items()]}"
        )

        code = pending_content_map.get(tool_call_id, "")

        if not code:
            logger.error(
                f"javascript_run_prepared: No code found in state for tool_call_id={tool_call_id}"
            )
            logger.error(
                f"javascript_run_prepared: Available keys in map: {list(pending_content_map.keys())}"
            )
            return (
                "Error: No code found in graph state. "
                "Generate JavaScript/TypeScript code first before calling this tool."
            )

        logger.info(
            f"javascript_run_prepared: Found {len(code)} chars of code in state for "
            f"tool_call_id={tool_call_id[:8]}..."
        )

        # Validate file extension
        if not (file_path.endswith(".js") or file_path.endswith(".ts")):
            logger.warning(
                f"javascript_run_prepared: File {file_path} does not have .js or .ts extension"
            )

        # Write code to VFS
        vfs = VirtualFilesystem(self.db_pool, thread_id)
        try:
            await vfs.write_file(file_path, code.encode("utf-8"))
            logger.info(f"javascript_run_prepared: Wrote {len(code)} bytes to {file_path}")
        except Exception as e:
            logger.error(f"javascript_run_prepared: Failed to write file: {e}")
            return f"Error writing code to file: {e}"

        # Execute the JavaScript/TypeScript code (no network access)
        executor = JavascriptSandboxExecutor(
            self.db_pool,
            thread_id,
            allow_net=False,
            timeout_seconds=60.0,
            stateful=False,
        )

        try:
            exec_result = await executor.execute(code)
            logger.info("javascript_run_prepared: Execution completed")

            # Format result similar to ExecuteJavascriptTool
            response_parts = []

            # Show which file was executed
            response_parts.append(f"**Executed:** {file_path}")

            # Show execution status if failed
            if not exec_result.success:
                response_parts.append("⚠️ **Execution failed**")

            # Show stdout without extra label
            if exec_result.stdout:
                response_parts.append(exec_result.stdout.strip())

            if exec_result.stderr:
                response_parts.append(f"Error:\n{exec_result.stderr}")

            # Show created/modified files with inline images
            if exec_result.created_files:
                # Separate image files from other files
                image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
                image_files = []
                other_files = []

                for path in exec_result.created_files:
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
            if exec_result.success:
                result = (
                    "\n\n".join(response_parts)
                    if response_parts
                    else "Execution successful (no output)"
                )
            else:
                result = "\n\n".join(response_parts) if response_parts else "Execution failed"

            logger.info(
                f"javascript_run_prepared: Generated result with {len(result)} chars: "
                f"{result[:200]}..."
            )

            # Clear this tool_call_id's content from state after successful execution
            if tool_call_id:
                try:
                    from langchain_core.messages import ToolMessage
                    from langgraph.types import Command

                    # Remove this tool_call_id from pending_content_map
                    updated_map = {
                        k: v for k, v in pending_content_map.items() if k != tool_call_id
                    }

                    state_update = {
                        "pending_content_map": updated_map,  # Clear this tool's content
                        "created_files": exec_result.created_files,
                        "messages": [ToolMessage(content=result, tool_call_id=tool_call_id)],
                    }

                    return Command(update=state_update, resume=result)  # type: ignore[return-value]
                except ImportError:
                    pass

            return result

        except Exception as e:
            logger.error(f"javascript_run_prepared: Execution failed: {e}")
            return f"Error executing code: {e}"

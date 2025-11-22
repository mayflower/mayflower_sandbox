"""
RunJavascriptFileTool - Execute JavaScript/TypeScript files from sandbox VFS.
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


class RunJavascriptFileInput(BaseModel):
    """Input schema for RunJavascriptFileTool."""

    file_path: str = Field(
        description="Path to JavaScript/TypeScript file to execute (e.g., /data/script.js, /tmp/analysis.ts)"
    )
    tool_call_id: Annotated[str, InjectedToolCallId]


class RunJavascriptFileTool(SandboxTool):
    """
    Tool for executing JavaScript/TypeScript files stored in the sandbox VFS.

    Reads the JavaScript/TypeScript file from PostgreSQL storage and executes it
    in the QuickJS-Wasm sandbox. Useful for running previously created or uploaded scripts.
    """

    name: str = "javascript_run_file"
    description: str = """Execute a JavaScript or TypeScript file from the sandbox filesystem.

Use this to run JavaScript/TypeScript scripts that have been previously created or uploaded.
The file is read from PostgreSQL storage and executed in the QuickJS-Wasm sandbox.

Args:
    file_path: Path to the JavaScript/TypeScript file to execute
              (e.g., /data/script.js, /tmp/analysis.ts, /utils/helpers.js)

Returns:
    Execution output (stdout, stderr, and any created files)

Example workflow:
1. Create a JavaScript script using write_file or javascript_run
2. Run it later with javascript_run_file

This is useful for:
- Running multi-step analysis scripts
- Executing uploaded JavaScript files
- Re-running previously created scripts
- Organizing code into reusable modules
- Separating data processing logic from main code

⚠️ **Note**: Both .js and .ts files are supported, but TypeScript transpilation
is basic (runtime-only). Use simple JavaScript/TypeScript syntax for best results.
"""
    args_schema: type[BaseModel] = RunJavascriptFileInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        tool_call_id: str = "",
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Execute JavaScript/TypeScript file from VFS."""

        # Get thread_id from context
        thread_id = self._get_thread_id(run_manager)

        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            # Read the JavaScript/TypeScript file from VFS
            file_info = await vfs.read_file(file_path)
            code = file_info["content"].decode("utf-8", errors="replace")

            # Validate it's a JavaScript or TypeScript file
            if not (file_path.endswith(".js") or file_path.endswith(".ts")):
                logger.warning(f"File {file_path} does not have .js or .ts extension")

            # Create executor (no network access for JavaScript)
            executor = JavascriptSandboxExecutor(
                self.db_pool,
                thread_id,
                allow_net=False,
                timeout_seconds=60.0,
                stateful=False,
            )

            # Execute the code
            result = await executor.execute(code)

            # Format response - keep it clean and user-friendly
            response_parts = []

            # Show which file was executed
            response_parts.append(f"**Executed:** {file_path}")

            # Show execution status if failed
            if not result.success:
                response_parts.append("⚠️ **Execution failed**")

            # Show stdout without extra label
            if result.stdout:
                response_parts.append(result.stdout.strip())

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

        except Exception as e:
            logger.error(f"Failed to execute JavaScript file {file_path}: {e}")
            return f"Error: Failed to execute file {file_path}: {e}"

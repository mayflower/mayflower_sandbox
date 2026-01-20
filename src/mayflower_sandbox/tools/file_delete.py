"""
FileDeleteTool - Delete files from sandbox VFS with HITL approval.
"""

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.base import SandboxTool


class FileDeleteInput(BaseModel):
    """Input schema for FileDeleteTool."""

    file_path: str = Field(description="Path to the file to delete (e.g., /tmp/data.txt)")
    approved: bool = Field(
        default=False,
        description="User approval status for deletion. When False, triggers approval dialog in frontend.",
    )


class FileDeleteTool(SandboxTool):
    """
    Tool for deleting files from the sandbox VFS with Human-in-the-Loop approval.

    Removes files from PostgreSQL storage after user confirmation.
    """

    name: str = "file_delete"
    description: str = """Delete a file from the sandbox filesystem. Requires user approval.

Permanently removes a file from PostgreSQL storage.
Use with caution - deletions cannot be undone.

Args:
    file_path: Path to the file to delete (e.g., /tmp/old_data.txt)
    approved: User approval status (default: False). When False, triggers approval dialog.

Returns:
    Confirmation message if approved, or "WAIT_FOR_USER_APPROVAL" if not approved

HITL Flow:
1. LLM calls tool without 'approved' parameter (defaults to False)
2. Tool returns "WAIT_FOR_USER_APPROVAL"
3. Frontend shows approval dialog to user
4. User approves/denies → Frontend re-calls tool with approved=True/False
5. Tool executes deletion if approved=True, or returns cancellation message if approved=False
"""
    args_schema: type[BaseModel] = FileDeleteInput

    async def _arun(  # type: ignore[override]
        self,
        file_path: str,
        approved: bool = False,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> str:
        """Delete file from VFS with user approval."""

        # HITL: If not approved, return special message to trigger frontend approval dialog
        if not approved:
            return "WAIT_FOR_USER_APPROVAL"

        # User approved - proceed with deletion
        thread_id = self._get_thread_id(run_manager)
        vfs = VirtualFilesystem(self.db_pool, thread_id)

        try:
            deleted = await vfs.delete_file(file_path)
            if deleted:
                return f"Successfully deleted: {file_path}"
            else:
                return f"Error: File not found: {file_path}"
        except Exception as e:
            return f"Error deleting file: {e}"

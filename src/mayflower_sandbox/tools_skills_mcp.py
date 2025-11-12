from __future__ import annotations

from typing import Any, Dict, Optional, Type

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from pydantic import BaseModel, Field

from mayflower_sandbox.tools.base import SandboxTool

from .integrations import add_http_mcp_server, install_skill


class SkillInstallArgs(BaseModel):
    source: str = Field(
        ...,
        description="Skill source, e.g. 'github:anthropics/skills/algorithmic-art'",
    )


class SkillInstallTool(SandboxTool):
    name: str = "skill_install"
    description: str = (
        "Install a Claude Skill into the sandbox VFS and make it importable (skills.<name>)."
    )
    args_schema: Type[BaseModel] = SkillInstallArgs

    async def _arun(  # type: ignore[override]
        self,
        source: str,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> dict[str, Any]:
        thread_id = self._get_thread_id(run_manager)
        return await install_skill(self.db_pool, thread_id, source)


class MCPBindArgs(BaseModel):
    name: str = Field(..., description="Short server name, becomes 'servers.<name>'")
    url: str = Field(..., description="Streamable HTTP MCP base URL (often ends with /mcp)")
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional auth headers",
    )


class MCPBindHttpTool(SandboxTool):
    name: str = "mcp_bind_http"
    description: str = (
        "Bind an HTTP MCP server and generate importable Python wrappers (servers.<name>.*)."
    )
    args_schema: Type[BaseModel] = MCPBindArgs

    async def _arun(  # type: ignore[override]
        self,
        name: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
    ) -> dict[str, Any]:
        thread_id = self._get_thread_id(run_manager)
        return await add_http_mcp_server(
            self.db_pool,
            thread_id,
            name=name,
            url=url,
            headers=headers,
        )

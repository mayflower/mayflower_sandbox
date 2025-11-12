import os
from pathlib import PurePosixPath

import asyncpg
import pytest

from mayflower_sandbox.integrations import install_skill
from mayflower_sandbox.sandbox_executor import SandboxExecutor
from mayflower_sandbox.filesystem import VirtualFilesystem


@pytest.mark.asyncio
async def test_install_skill_and_import():
    db = await asyncpg.create_pool(
        database=os.environ.get("PGDATABASE", "mayflower_test"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5433")),
    )

    try:
        thread_id = "test_skill_thread"
        skill = await install_skill(db, thread_id, "github:anthropics/skills/algorithmic-art")

        vfs = VirtualFilesystem(db, thread_id)
        pkg_root = PurePosixPath(skill["path"])
        for relative in ("SKILL.md", "__init__.py"):
            entry = await vfs.read_file(str(pkg_root / relative))
            assert entry["content"], f"{relative} missing in VFS"

        executor = SandboxExecutor(db, thread_id)
        code = """
from skills.algorithmic_art import instructions
print(instructions()[:64])
"""
        result = await executor.execute(code)
        assert result.success
        assert "algorithmic art" in result.stdout.lower()
    finally:
        await db.close()

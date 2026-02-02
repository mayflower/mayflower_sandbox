"""Mayflower Sandbox - Python execution sandbox with persistent VFS."""

from typing import Any

from .filesystem import VirtualFilesystem
from .manager import SandboxManager
from .sandbox_executor import SandboxExecutor

__all__ = ["SandboxManager", "VirtualFilesystem", "SandboxExecutor"]

PostgresBackend: Any | None
MayflowerSandboxBackend: Any | None
try:
    from .deepagents_backend import MayflowerSandboxBackend as _MayflowerSandboxBackend
    from .deepagents_backend import PostgresBackend as _PostgresBackend
except ImportError:  # pragma: no cover - optional dependency
    PostgresBackend = None
    MayflowerSandboxBackend = None
else:
    PostgresBackend = _PostgresBackend
    MayflowerSandboxBackend = _MayflowerSandboxBackend
    __all__.extend(["PostgresBackend", "MayflowerSandboxBackend"])

__version__ = "0.2.0"

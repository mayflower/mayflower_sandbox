"""Mayflower Sandbox - Python execution sandbox with persistent VFS."""

from typing import Any

from .filesystem import VirtualFilesystem
from .manager import SandboxManager
from .sandbox_executor import SandboxExecutor

__all__ = ["SandboxManager", "VirtualFilesystem", "SandboxExecutor"]

MayflowerSandboxBackend: Any | None
try:
    from .deepagents_backend import MayflowerSandboxBackend as _MayflowerSandboxBackend
except ImportError:  # pragma: no cover - optional dependency
    MayflowerSandboxBackend = None
else:
    MayflowerSandboxBackend = _MayflowerSandboxBackend
    __all__.append("MayflowerSandboxBackend")

__version__ = "0.2.0"

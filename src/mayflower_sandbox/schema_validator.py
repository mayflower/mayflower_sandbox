"""
Schema Validator - Runtime validation for MCP tool calls.

Provides jsonschema-based validation at the bridge level for security enforcement.
This ensures all MCP calls are validated even if sandbox-side validation is bypassed.
"""

import logging
from typing import Any

from jsonschema.validators import validator_for

logger = logging.getLogger(__name__)


class MCPSchemaValidator:
    """
    Validates MCP tool call arguments against JSON schemas.

    Caches compiled validators per (server, tool) for performance.
    Fails open for unknown tools to maintain backwards compatibility.
    """

    def __init__(self) -> None:
        # Cache: {server_name: {tool_name: validator}}
        # Validators are instances returned by jsonschema.validator_for()
        self._validators: dict[str, dict[str, Any]] = {}
        # Raw schemas for introspection
        self._schemas: dict[str, dict[str, dict[str, Any]]] = {}

    def load_schemas(self, server_name: str, schemas: dict[str, dict[str, Any]]) -> None:
        """
        Load and compile schemas for a server's tools.

        Args:
            server_name: Name of the MCP server (e.g., "github")
            schemas: Dict mapping tool_name to inputSchema
        """
        if server_name not in self._validators:
            self._validators[server_name] = {}
            self._schemas[server_name] = {}

        for tool_name, schema in schemas.items():
            if not schema:
                continue

            try:
                # Auto-detect schema version and create validator
                validator_cls = validator_for(schema)
                # Check schema validity
                validator_cls.check_schema(schema)
                # Create and cache validator instance
                self._validators[server_name][tool_name] = validator_cls(schema)
                self._schemas[server_name][tool_name] = schema
                logger.debug(f"Loaded schema for {server_name}.{tool_name}")
            except Exception as e:
                logger.warning(f"Failed to compile schema for {server_name}.{tool_name}: {e}")

    def unload_server(self, server_name: str) -> None:
        """Remove all cached validators for a server."""
        self._validators.pop(server_name, None)
        self._schemas.pop(server_name, None)

    def has_schema(self, server_name: str, tool_name: str) -> bool:
        """Check if a schema exists for the given server/tool."""
        return server_name in self._validators and tool_name in self._validators[server_name]

    def get_schema(self, server_name: str, tool_name: str) -> dict[str, Any] | None:
        """Get the raw schema for a tool, if loaded."""
        return self._schemas.get(server_name, {}).get(tool_name)

    def validate(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> list[str]:
        """
        Validate tool call arguments against the schema.

        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool being called
            args: Arguments to validate

        Returns:
            List of validation error messages (empty if valid or no schema)
        """
        # Fail open if no schema loaded (backwards compatibility)
        if not self.has_schema(server_name, tool_name):
            logger.debug(f"No schema for {server_name}.{tool_name}, skipping validation")
            return []

        validator = self._validators[server_name][tool_name]
        errors: list[str] = []

        for error in validator.iter_errors(args):
            # Format error message with path context
            path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "root"
            errors.append(f"{path}: {error.message}")

        if errors:
            logger.warning(
                f"Validation failed for {server_name}.{tool_name}: {len(errors)} error(s)"
            )

        return errors

    def validate_or_raise(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> None:
        """
        Validate and raise ValueError if invalid.

        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool being called
            args: Arguments to validate

        Raises:
            ValueError: If validation fails, with all error messages
        """
        errors = self.validate(server_name, tool_name, args)
        if errors:
            error_list = "\n  - ".join(errors)
            raise ValueError(f"Validation failed for {server_name}.{tool_name}:\n  - {error_list}")

    def list_servers(self) -> list[str]:
        """List all servers with loaded schemas."""
        return list(self._validators.keys())

    def list_tools(self, server_name: str) -> list[str]:
        """List all tools with schemas for a server."""
        return list(self._validators.get(server_name, {}).keys())


# Global validator instance for the bridge
_global_validator: MCPSchemaValidator | None = None


def get_validator() -> MCPSchemaValidator:
    """Get or create the global validator instance."""
    global _global_validator
    if _global_validator is None:
        _global_validator = MCPSchemaValidator()
    return _global_validator


def reset_validator() -> None:
    """Reset the global validator (for testing)."""
    global _global_validator
    _global_validator = None

# mypy: ignore-errors
"""
Tests for schema_codegen module - Pydantic model generation from JSON schemas.
"""


from mayflower_sandbox.schema_codegen import (
    generate_init_module,
    generate_model_for_tool,
    generate_models_module,
    generate_server_package,
    generate_tools_module,
    generate_typed_wrapper,
)


class TestGenerateModelForTool:
    """Tests for generate_model_for_tool function."""

    def test_simple_schema(self):
        """Test generation from simple schema with required and optional fields."""
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body"},
            },
            "required": ["title"],
        }

        result = generate_model_for_tool("create_issue", schema)

        assert result is not None
        assert "class CreateIssueArgs" in result
        assert "title" in result
        assert "body" in result

    def test_array_property(self):
        """Test generation with array property."""
        schema = {
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"type": "string"}},
            },
        }

        result = generate_model_for_tool("add_labels", schema)

        assert result is not None
        assert "labels" in result

    def test_empty_schema_returns_none(self):
        """Test that empty schema returns None."""
        result = generate_model_for_tool("empty_tool", {})
        assert result is None

    def test_nested_object(self):
        """Test generation with nested object property."""
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "timeout": {"type": "integer"},
                    },
                },
            },
        }

        result = generate_model_for_tool("configure", schema)
        assert result is not None


class TestGenerateTypedWrapper:
    """Tests for generate_typed_wrapper function."""

    def test_basic_wrapper(self):
        """Test generation of typed wrapper function."""
        tool = {
            "name": "create_issue",
            "description": "Create a GitHub issue.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string"},
                },
                "required": ["title"],
            },
        }

        result = generate_typed_wrapper("github", tool)

        assert "async def create_issue" in result
        assert "title: str" in result
        assert "body: str | None = None" in result
        assert "from mayflower_mcp import call" in result
        assert 'await call("github", "create_issue"' in result

    def test_wrapper_with_array_param(self):
        """Test wrapper with array parameter."""
        tool = {
            "name": "add_labels",
            "description": "Add labels to an issue.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "labels": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["labels"],
            },
        }

        result = generate_typed_wrapper("github", tool)

        assert "async def add_labels" in result
        assert "labels: list[str]" in result


class TestGenerateModelsModule:
    """Tests for generate_models_module function."""

    def test_multiple_tools(self):
        """Test generation of models module with multiple tools."""
        tools = [
            {
                "name": "create_issue",
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            },
            {
                "name": "list_issues",
                "inputSchema": {
                    "type": "object",
                    "properties": {"state": {"type": "string"}},
                },
            },
        ]

        result = generate_models_module(tools)

        assert "Auto-generated Pydantic models" in result
        assert "from pydantic import BaseModel" in result

    def test_empty_tools_list(self):
        """Test generation with empty tools list."""
        result = generate_models_module([])

        assert "Auto-generated Pydantic models" in result
        assert "from pydantic import BaseModel" in result


class TestGenerateToolsModule:
    """Tests for generate_tools_module function."""

    def test_tools_module_generation(self):
        """Test generation of tools.py module."""
        tools = [
            {
                "name": "create_issue",
                "description": "Create an issue",
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            },
        ]

        result = generate_tools_module("github", tools)

        assert "Auto-generated typed wrappers for github" in result
        assert "async def create_issue" in result


class TestGenerateInitModule:
    """Tests for generate_init_module function."""

    def test_init_module_exports(self):
        """Test that init module exports all tools."""
        tools = [
            {
                "name": "create_issue",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "list_issues",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

        result = generate_init_module("github", tools)

        assert "from .tools import create_issue, list_issues" in result
        assert '__all__ = ["create_issue", "list_issues"]' in result


class TestGenerateServerPackage:
    """Tests for generate_server_package function."""

    def test_complete_package_generation(self):
        """Test generation of complete server package."""
        tools = [
            {
                "name": "create_issue",
                "description": "Create an issue",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        ]

        package = generate_server_package("github", tools)

        assert "__init__.py" in package
        assert "models.py" in package
        assert "tools.py" in package
        assert "schemas.json" in package

        # Check schemas.json content
        import json

        schemas = json.loads(package["schemas.json"])
        assert "create_issue" in schemas

    def test_empty_tools_package(self):
        """Test package generation with no tools."""
        package = generate_server_package("empty", [])

        assert "__init__.py" in package
        assert "models.py" in package
        assert "tools.py" in package
        assert "schemas.json" in package

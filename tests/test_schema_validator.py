# mypy: ignore-errors
"""
Tests for schema_validator module - jsonschema validation for MCP calls.
"""

import pytest

from mayflower_sandbox.schema_validator import (
    MCPSchemaValidator,
    get_validator,
    reset_validator,
)


@pytest.fixture
def validator():
    """Create a fresh validator for each test."""
    return MCPSchemaValidator()


@pytest.fixture
def sample_schemas():
    """Sample schemas for testing."""
    return {
        "create_issue": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
        },
        "list_issues": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    }


class TestMCPSchemaValidator:
    """Tests for MCPSchemaValidator class."""

    def test_load_schemas(self, validator, sample_schemas):
        """Test loading schemas for a server."""
        validator.load_schemas("github", sample_schemas)

        assert validator.has_schema("github", "create_issue")
        assert validator.has_schema("github", "list_issues")
        assert not validator.has_schema("github", "unknown_tool")
        assert not validator.has_schema("unknown_server", "create_issue")

    def test_validate_valid_args(self, validator, sample_schemas):
        """Test validation with valid arguments."""
        validator.load_schemas("github", sample_schemas)

        errors = validator.validate("github", "create_issue", {"title": "Bug report"})
        assert errors == []

        errors = validator.validate(
            "github",
            "create_issue",
            {"title": "Feature", "body": "Description", "labels": ["enhancement"]},
        )
        assert errors == []

    def test_validate_missing_required(self, validator, sample_schemas):
        """Test validation fails when required field is missing."""
        validator.load_schemas("github", sample_schemas)

        errors = validator.validate("github", "create_issue", {"body": "No title"})

        assert len(errors) == 1
        assert "title" in errors[0].lower() or "required" in errors[0].lower()

    def test_validate_wrong_type(self, validator, sample_schemas):
        """Test validation fails with wrong type."""
        validator.load_schemas("github", sample_schemas)

        errors = validator.validate(
            "github",
            "create_issue",
            {"title": 123},  # Should be string
        )

        assert len(errors) >= 1

    def test_validate_enum_violation(self, validator, sample_schemas):
        """Test validation fails when enum constraint violated."""
        validator.load_schemas("github", sample_schemas)

        errors = validator.validate("github", "list_issues", {"state": "invalid_state"})

        assert len(errors) >= 1

    def test_validate_range_violation(self, validator, sample_schemas):
        """Test validation fails when range constraint violated."""
        validator.load_schemas("github", sample_schemas)

        errors = validator.validate("github", "list_issues", {"limit": 200})

        assert len(errors) >= 1

    def test_validate_unknown_server_fails_open(self, validator):
        """Test that unknown server passes validation (fail-open)."""
        errors = validator.validate("unknown", "some_tool", {"any": "args"})
        assert errors == []

    def test_validate_unknown_tool_fails_open(self, validator, sample_schemas):
        """Test that unknown tool passes validation (fail-open)."""
        validator.load_schemas("github", sample_schemas)

        errors = validator.validate("github", "unknown_tool", {"any": "args"})
        assert errors == []

    def test_validate_or_raise_valid(self, validator, sample_schemas):
        """Test validate_or_raise doesn't raise on valid input."""
        validator.load_schemas("github", sample_schemas)

        # Should not raise
        validator.validate_or_raise("github", "create_issue", {"title": "Valid"})

    def test_validate_or_raise_invalid(self, validator, sample_schemas):
        """Test validate_or_raise raises ValueError on invalid input."""
        validator.load_schemas("github", sample_schemas)

        with pytest.raises(ValueError) as exc_info:
            validator.validate_or_raise("github", "create_issue", {})

        assert "Validation failed" in str(exc_info.value)
        assert "github.create_issue" in str(exc_info.value)

    def test_unload_server(self, validator, sample_schemas):
        """Test unloading a server's schemas."""
        validator.load_schemas("github", sample_schemas)
        assert validator.has_schema("github", "create_issue")

        validator.unload_server("github")
        assert not validator.has_schema("github", "create_issue")

    def test_get_schema(self, validator, sample_schemas):
        """Test retrieving raw schema."""
        validator.load_schemas("github", sample_schemas)

        schema = validator.get_schema("github", "create_issue")
        assert schema is not None
        assert schema["type"] == "object"
        assert "title" in schema["properties"]

        # Unknown returns None
        assert validator.get_schema("github", "unknown") is None

    def test_list_servers(self, validator, sample_schemas):
        """Test listing servers with loaded schemas."""
        assert validator.list_servers() == []

        validator.load_schemas("github", sample_schemas)
        validator.load_schemas("gitlab", {"merge_mr": {"type": "object"}})

        servers = validator.list_servers()
        assert "github" in servers
        assert "gitlab" in servers

    def test_list_tools(self, validator, sample_schemas):
        """Test listing tools for a server."""
        validator.load_schemas("github", sample_schemas)

        tools = validator.list_tools("github")
        assert "create_issue" in tools
        assert "list_issues" in tools

        # Unknown server returns empty
        assert validator.list_tools("unknown") == []


class TestGlobalValidator:
    """Tests for global validator functions."""

    def test_get_validator_singleton(self):
        """Test that get_validator returns singleton."""
        reset_validator()

        v1 = get_validator()
        v2 = get_validator()

        assert v1 is v2

    def test_reset_validator(self):
        """Test that reset_validator clears the singleton."""
        v1 = get_validator()
        reset_validator()
        v2 = get_validator()

        assert v1 is not v2


class TestComplexSchemas:
    """Tests for more complex schema scenarios."""

    def test_nested_object_validation(self, validator):
        """Test validation of nested objects."""
        schema = {
            "config_tool": {
                "type": "object",
                "properties": {
                    "settings": {
                        "type": "object",
                        "properties": {
                            "timeout": {"type": "integer"},
                            "retries": {"type": "integer"},
                        },
                        "required": ["timeout"],
                    },
                },
                "required": ["settings"],
            },
        }
        validator.load_schemas("app", schema)

        # Valid nested object
        errors = validator.validate(
            "app", "config_tool", {"settings": {"timeout": 30, "retries": 3}}
        )
        assert errors == []

        # Missing nested required field
        errors = validator.validate("app", "config_tool", {"settings": {"retries": 3}})
        assert len(errors) >= 1

    def test_array_items_validation(self, validator):
        """Test validation of array items."""
        schema = {
            "add_users": {
                "type": "object",
                "properties": {
                    "users": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "email": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                },
            },
        }
        validator.load_schemas("app", schema)

        # Valid array
        errors = validator.validate(
            "app",
            "add_users",
            {"users": [{"name": "Alice", "email": "alice@example.com"}]},
        )
        assert errors == []

        # Invalid item in array
        errors = validator.validate(
            "app", "add_users", {"users": [{"email": "no-name@example.com"}]}
        )
        assert len(errors) >= 1

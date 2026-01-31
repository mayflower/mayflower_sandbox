"""Unit tests for deepagents_backend module.

Tests helper functions and the MayflowerSandboxBackend class.
Uses mocking to handle the optional deepagents dependency.
"""

import importlib.util
import re
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Check if deepagents is available
DEEPAGENTS_AVAILABLE = importlib.util.find_spec("deepagents") is not None


# Mock deepagents protocol types so we can import the module for testing
@pytest.fixture(scope="module", autouse=True)
def mock_deepagents():
    """Mock deepagents module if not installed."""
    if DEEPAGENTS_AVAILABLE:
        yield
        return

    # Create mock protocol types
    mock_protocol = MagicMock()
    mock_protocol.EditResult = dict
    mock_protocol.ExecuteResponse = dict
    mock_protocol.FileDownloadResponse = dict
    mock_protocol.FileInfo = dict
    mock_protocol.FileUploadResponse = dict
    mock_protocol.GrepMatch = dict
    mock_protocol.WriteResult = dict
    mock_protocol.SandboxBackendProtocol = object

    # Patch the modules
    with patch.dict(
        sys.modules,
        {
            "deepagents": MagicMock(),
            "deepagents.backends": MagicMock(),
            "deepagents.backends.protocol": mock_protocol,
        },
    ):
        # Force reimport of our module with mocked dependencies
        if "mayflower_sandbox.deepagents_backend" in sys.modules:
            del sys.modules["mayflower_sandbox.deepagents_backend"]
        yield


# Import after the mock is set up
def get_module():
    """Get the deepagents_backend module (with mocking if needed)."""
    if not DEEPAGENTS_AVAILABLE:
        # Set up mocks before import
        mock_protocol = MagicMock()
        mock_protocol.EditResult = dict
        mock_protocol.ExecuteResponse = dict
        mock_protocol.FileDownloadResponse = dict
        mock_protocol.FileInfo = dict
        mock_protocol.FileUploadResponse = dict
        mock_protocol.GrepMatch = dict
        mock_protocol.WriteResult = dict
        mock_protocol.SandboxBackendProtocol = object

        sys.modules["deepagents"] = MagicMock()
        sys.modules["deepagents.backends"] = MagicMock()
        sys.modules["deepagents.backends.protocol"] = mock_protocol

    from mayflower_sandbox import deepagents_backend

    return deepagents_backend


class TestFormatLineNumbers:
    """Tests for _format_line_numbers helper."""

    def test_single_line(self):
        module = get_module()
        result = module._format_line_numbers(["hello"], 1)
        assert result == "     1\thello"

    def test_multiple_lines(self):
        module = get_module()
        result = module._format_line_numbers(["line1", "line2", "line3"], 1)
        assert result == "     1\tline1\n     2\tline2\n     3\tline3"

    def test_starting_from_offset(self):
        module = get_module()
        result = module._format_line_numbers(["line10"], 10)
        assert result == "    10\tline10"

    def test_empty_lines(self):
        module = get_module()
        result = module._format_line_numbers([], 1)
        assert result == ""

    def test_large_line_numbers(self):
        module = get_module()
        result = module._format_line_numbers(["line"], 100000)
        assert result == "100000\tline"


class TestEmptyContentWarning:
    """Tests for _empty_content_warning helper."""

    def test_empty_string(self):
        module = get_module()
        assert (
            module._empty_content_warning("")
            == "System reminder: File exists but has empty contents"
        )

    def test_whitespace_only(self):
        module = get_module()
        assert (
            module._empty_content_warning("   \n\t  ")
            == "System reminder: File exists but has empty contents"
        )

    def test_non_empty_content(self):
        module = get_module()
        assert module._empty_content_warning("hello") is None

    def test_none_content(self):
        module = get_module()
        # Note: The actual function expects str, but handles None gracefully
        assert (
            module._empty_content_warning(None)  # type: ignore[arg-type]
            == "System reminder: File exists but has empty contents"
        )


class TestNormalizePath:
    """Tests for _normalize_path helper."""

    def test_path_with_leading_slash(self):
        module = get_module()
        assert module._normalize_path("/path/to/file") == "/path/to/file"

    def test_path_without_leading_slash(self):
        module = get_module()
        assert module._normalize_path("path/to/file") == "/path/to/file"

    def test_single_filename(self):
        module = get_module()
        assert module._normalize_path("file.txt") == "/file.txt"

    def test_root_path(self):
        module = get_module()
        assert module._normalize_path("/") == "/"


class TestFormatTimestamp:
    """Tests for _format_timestamp helper."""

    def test_none_value(self):
        module = get_module()
        assert module._format_timestamp(None) == ""

    def test_datetime_value(self):
        module = get_module()
        dt = datetime(2024, 1, 15, 10, 30, 0)
        assert module._format_timestamp(dt) == "2024-01-15T10:30:00"

    def test_object_with_isoformat(self):
        module = get_module()

        class CustomDate:
            def isoformat(self):
                return "2024-01-15T12:00:00"

        assert module._format_timestamp(CustomDate()) == "2024-01-15T12:00:00"

    def test_object_with_failing_isoformat(self):
        module = get_module()

        class BrokenDate:
            def isoformat(self):
                raise ValueError("broken")

        result = module._format_timestamp(BrokenDate())
        assert "BrokenDate" in result  # Falls back to str()

    def test_string_value(self):
        module = get_module()
        assert module._format_timestamp("2024-01-15") == "2024-01-15"

    def test_integer_value(self):
        module = get_module()
        assert module._format_timestamp(12345) == "12345"


class TestMayflowerSandboxBackend:
    """Tests for MayflowerSandboxBackend class."""

    @pytest.fixture
    def mock_vfs(self):
        vfs = AsyncMock()
        vfs.validate_path = MagicMock(side_effect=lambda p: p if p.startswith("/") else f"/{p}")
        vfs.file_exists = AsyncMock(return_value=False)
        vfs.list_files = AsyncMock(return_value=[])
        vfs.read_file = AsyncMock(return_value={"content": b"test content"})
        vfs.write_file = AsyncMock()
        return vfs

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        result = MagicMock()
        result.stdout = "output"
        result.stderr = ""
        result.success = True
        result.exit_code = 0
        executor.execute = AsyncMock(return_value=result)
        executor.execute_shell = AsyncMock(return_value=result)
        return executor

    @pytest.fixture
    def backend(self, mock_vfs, mock_executor):
        module = get_module()
        mock_db_pool = MagicMock()
        with (
            patch.object(module, "VirtualFilesystem", return_value=mock_vfs),
            patch.object(module, "SandboxExecutor", return_value=mock_executor),
        ):
            return module.MayflowerSandboxBackend(mock_db_pool, "test_thread")

    def test_id_property(self, backend):
        assert backend.id == "mayflower:test_thread"

    @pytest.mark.asyncio
    async def test_aread_success(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"line1\nline2\nline3"})
        result = await backend.aread("/test.txt")
        assert "     1\tline1" in result
        assert "     2\tline2" in result
        assert "     3\tline3" in result

    @pytest.mark.asyncio
    async def test_aread_with_offset(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"line1\nline2\nline3"})
        result = await backend.aread("/test.txt", offset=1, limit=1)
        assert "     2\tline2" in result
        assert "line1" not in result
        assert "line3" not in result

    @pytest.mark.asyncio
    async def test_aread_file_not_found(self, backend, mock_vfs):
        from mayflower_sandbox.filesystem import FileNotFoundError

        mock_vfs.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
        result = await backend.aread("/missing.txt")
        assert "Error:" in result
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_aread_empty_file(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b""})
        result = await backend.aread("/empty.txt")
        assert "empty contents" in result

    @pytest.mark.asyncio
    async def test_aread_offset_exceeds_length(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"line1\nline2"})
        result = await backend.aread("/test.txt", offset=100)
        assert "Error:" in result
        assert "exceeds file length" in result

    @pytest.mark.asyncio
    async def test_awrite_success(self, backend, mock_vfs):
        result = await backend.awrite("/new_file.txt", "content")
        assert result.get("path") == "/new_file.txt"
        assert result.get("error") is None

    @pytest.mark.asyncio
    async def test_awrite_file_exists(self, backend, mock_vfs):
        mock_vfs.file_exists = AsyncMock(return_value=True)
        result = await backend.awrite("/existing.txt", "content")
        assert result.get("error") is not None
        assert "already exists" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aedit_success(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"hello world"})
        result = await backend.aedit("/test.txt", "world", "universe")
        assert result.get("path") == "/test.txt"
        assert result.get("occurrences") == 1

    @pytest.mark.asyncio
    async def test_aedit_string_not_found(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"hello world"})
        result = await backend.aedit("/test.txt", "missing", "replacement")
        assert "Error:" in result.get("error", "")
        assert "not found" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aedit_multiple_occurrences_without_replace_all(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"hello hello hello"})
        result = await backend.aedit("/test.txt", "hello", "hi")
        assert "Error:" in result.get("error", "")
        assert "3 times" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aedit_multiple_occurrences_with_replace_all(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"hello hello hello"})
        result = await backend.aedit("/test.txt", "hello", "hi", replace_all=True)
        assert result.get("occurrences") == 3

    @pytest.mark.asyncio
    async def test_agrep_raw_success(self, backend, mock_vfs):
        mock_vfs.list_files = AsyncMock(
            return_value=[
                {
                    "file_path": "/test.py",
                    "content": b"def hello():\n    pass\ndef world():",
                },
            ]
        )
        result = await backend.agrep_raw(r"def .*\(\)")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["line"] == 1
        assert result[1]["line"] == 3

    @pytest.mark.asyncio
    async def test_agrep_raw_invalid_regex(self, backend):
        result = await backend.agrep_raw("[invalid")
        assert isinstance(result, str)
        assert "Invalid regex" in result

    @pytest.mark.asyncio
    async def test_agrep_raw_with_path_filter(self, backend, mock_vfs):
        mock_vfs.list_files = AsyncMock(
            return_value=[
                {"file_path": "/src/a.py", "content": b"match"},
                {"file_path": "/tests/b.py", "content": b"match"},
            ]
        )
        result = await backend.agrep_raw("match", path="/src")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["path"] == "/src/a.py"

    @pytest.mark.asyncio
    async def test_agrep_raw_with_glob_filter(self, backend, mock_vfs):
        mock_vfs.list_files = AsyncMock(
            return_value=[
                {"file_path": "/src/a.py", "content": b"match"},
                {"file_path": "/src/b.txt", "content": b"match"},
            ]
        )
        result = await backend.agrep_raw("match", path="/src", glob="*.py")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["path"] == "/src/a.py"

    @pytest.mark.asyncio
    async def test_agrep_raw_path_not_found(self, backend, mock_vfs):
        mock_vfs.list_files = AsyncMock(return_value=[])
        result = await backend.agrep_raw("pattern", path="/nonexistent")
        assert isinstance(result, str)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_als_info(self, backend, mock_vfs):
        mock_vfs.list_files = AsyncMock(
            return_value=[
                {"file_path": "/dir/file1.txt", "size": 100, "modified_at": None},
                {"file_path": "/dir/file2.txt", "size": 200, "modified_at": None},
                {"file_path": "/dir/subdir/file3.txt", "size": 300, "modified_at": None},
            ]
        )
        result = await backend.als_info("/dir")
        assert len(result) == 3  # 2 files + 1 subdir
        paths = [r["path"] for r in result]
        assert "/dir/file1.txt" in paths
        assert "/dir/file2.txt" in paths
        assert "/dir/subdir/" in paths

    @pytest.mark.asyncio
    async def test_aglob_info(self, backend, mock_vfs):
        mock_vfs.list_files = AsyncMock(
            return_value=[
                {"file_path": "/src/a.py", "size": 100, "modified_at": None},
                {"file_path": "/src/b.txt", "size": 200, "modified_at": None},
                {"file_path": "/src/c.py", "size": 300, "modified_at": None},
            ]
        )
        result = await backend.aglob_info("*.py", path="/src")
        assert len(result) == 2
        assert all(r["path"].endswith(".py") for r in result)

    @pytest.mark.asyncio
    async def test_aupload_files_success(self, backend, mock_vfs):
        files = [("/file1.txt", b"content1"), ("/file2.txt", b"content2")]
        result = await backend.aupload_files(files)
        assert len(result) == 2
        assert all(r.get("error") is None for r in result)

    @pytest.mark.asyncio
    async def test_aupload_files_invalid_path(self, backend, mock_vfs):
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.validate_path = MagicMock(side_effect=InvalidPathError("bad path"))
        result = await backend.aupload_files([("../bad", b"content")])
        assert result[0].get("error") == "invalid_path"

    @pytest.mark.asyncio
    async def test_adownload_files_success(self, backend, mock_vfs):
        mock_vfs.read_file = AsyncMock(return_value={"content": b"file content"})
        result = await backend.adownload_files(["/test.txt"])
        assert len(result) == 1
        assert result[0].get("content") == b"file content"
        assert result[0].get("error") is None

    @pytest.mark.asyncio
    async def test_adownload_files_not_found(self, backend, mock_vfs):
        from mayflower_sandbox.filesystem import FileNotFoundError

        mock_vfs.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
        result = await backend.adownload_files(["/missing.txt"])
        assert result[0].get("error") == "file_not_found"

    @pytest.mark.asyncio
    async def test_aexecute_python(self, backend, mock_executor):
        result = await backend.aexecute("__PYTHON__\nprint('hello')")
        assert result.get("output") == "output"
        assert result.get("exit_code") == 0

    @pytest.mark.asyncio
    async def test_aexecute_shell(self, backend, mock_executor):
        result = await backend.aexecute("ls -la")
        assert result.get("output") == "output"
        assert result.get("exit_code") == 0


class TestGrepFileMatches:
    """Tests for _grep_file_matches method."""

    @pytest.fixture
    def backend(self):
        module = get_module()
        mock_db_pool = MagicMock()
        with (
            patch.object(module, "VirtualFilesystem"),
            patch.object(module, "SandboxExecutor"),
        ):
            return module.MayflowerSandboxBackend(mock_db_pool, "test")

    def test_matches_found(self, backend):
        file_row = {"file_path": "/test.py", "content": b"line1\nmatch here\nline3"}
        regex = re.compile("match")
        result = backend._grep_file_matches(file_row, regex)
        assert len(result) == 1
        assert result[0]["path"] == "/test.py"
        assert result[0]["line"] == 2
        assert "match here" in str(result[0]["text"])

    def test_multiple_matches(self, backend):
        file_row = {"file_path": "/test.py", "content": b"match1\nmatch2\nno match"}
        regex = re.compile(r"match\d")
        result = backend._grep_file_matches(file_row, regex)
        assert len(result) == 2

    def test_no_matches(self, backend):
        file_row = {"file_path": "/test.py", "content": b"nothing here"}
        regex = re.compile("missing")
        result = backend._grep_file_matches(file_row, regex)
        assert len(result) == 0

    def test_empty_content(self, backend):
        file_row = {"file_path": "/test.py", "content": b""}
        regex = re.compile("pattern")
        result = backend._grep_file_matches(file_row, regex)
        assert len(result) == 0

    def test_none_content(self, backend):
        file_row = {"file_path": "/test.py", "content": None}
        regex = re.compile("pattern")
        result = backend._grep_file_matches(file_row, regex)
        assert len(result) == 0


class TestMatchesGlobFilter:
    """Tests for _matches_glob_filter method."""

    @pytest.fixture
    def backend(self):
        module = get_module()
        mock_db_pool = MagicMock()
        with (
            patch.object(module, "VirtualFilesystem"),
            patch.object(module, "SandboxExecutor"),
        ):
            return module.MayflowerSandboxBackend(mock_db_pool, "test")

    def test_no_pattern_always_matches(self, backend):
        assert backend._matches_glob_filter("/any/path/file.txt", "/any", None) is True

    def test_pattern_matches(self, backend):
        assert backend._matches_glob_filter("/src/file.py", "/src/", "*.py") is True

    def test_pattern_not_matches(self, backend):
        assert backend._matches_glob_filter("/src/file.txt", "/src/", "*.py") is False

    def test_recursive_pattern(self, backend):
        assert backend._matches_glob_filter("/src/deep/file.py", "/src/", "**/*.py") is True

    def test_root_base_path(self, backend):
        assert backend._matches_glob_filter("/file.py", "/", "*.py") is True


class TestErrorPaths:
    """Tests for error handling paths in MayflowerSandboxBackend."""

    @pytest.fixture
    def mock_vfs(self):
        vfs = AsyncMock()
        vfs.validate_path = MagicMock(side_effect=lambda p: p if p.startswith("/") else f"/{p}")
        vfs.file_exists = AsyncMock(return_value=False)
        vfs.list_files = AsyncMock(return_value=[])
        vfs.read_file = AsyncMock(return_value={"content": b"test content"})
        vfs.write_file = AsyncMock()
        return vfs

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        result = MagicMock()
        result.stdout = "output"
        result.stderr = ""
        result.success = True
        result.exit_code = 0
        executor.execute = AsyncMock(return_value=result)
        executor.execute_shell = AsyncMock(return_value=result)
        return executor

    @pytest.fixture
    def backend(self, mock_vfs, mock_executor):
        module = get_module()
        mock_db_pool = MagicMock()
        with (
            patch.object(module, "VirtualFilesystem", return_value=mock_vfs),
            patch.object(module, "SandboxExecutor", return_value=mock_executor),
        ):
            return module.MayflowerSandboxBackend(mock_db_pool, "test_thread")

    @pytest.mark.asyncio
    async def test_awrite_invalid_path(self, backend, mock_vfs):
        """Test write with invalid path returns error."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.validate_path = MagicMock(side_effect=InvalidPathError("Path traversal"))
        result = await backend.awrite("../bad/path", "content")
        assert result.get("error") is not None
        assert "Path traversal" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_awrite_write_file_invalid_path_error(self, backend, mock_vfs):
        """Test write when write_file raises InvalidPathError."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.write_file = AsyncMock(side_effect=InvalidPathError("Invalid destination"))
        result = await backend.awrite("/file.txt", "content")
        assert result.get("error") is not None
        assert "Invalid destination" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aedit_file_not_found(self, backend, mock_vfs):
        """Test edit on non-existent file returns error."""
        from mayflower_sandbox.filesystem import FileNotFoundError

        mock_vfs.read_file = AsyncMock(side_effect=FileNotFoundError("File missing"))
        result = await backend.aedit("/missing.txt", "old", "new")
        assert result.get("error") is not None
        assert "not found" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aedit_invalid_path(self, backend, mock_vfs):
        """Test edit with invalid path returns error."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.read_file = AsyncMock(side_effect=InvalidPathError("Bad path"))
        result = await backend.aedit("../escape.txt", "old", "new")
        assert result.get("error") is not None
        assert "not found" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aedit_write_invalid_path_error(self, backend, mock_vfs):
        """Test edit when write_file raises InvalidPathError."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.read_file = AsyncMock(return_value={"content": b"old content"})
        mock_vfs.write_file = AsyncMock(side_effect=InvalidPathError("Write failed"))
        result = await backend.aedit("/file.txt", "old", "new")
        assert result.get("error") is not None
        assert "Write failed" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_aupload_files_write_exception(self, backend, mock_vfs):
        """Test upload when write_file raises unexpected exception."""
        mock_vfs.write_file = AsyncMock(side_effect=Exception("Database error"))
        result = await backend.aupload_files([("/file.txt", b"content")])
        assert result[0].get("error") == "permission_denied"

    @pytest.mark.asyncio
    async def test_aupload_files_write_invalid_path(self, backend, mock_vfs):
        """Test upload when write_file raises InvalidPathError."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.write_file = AsyncMock(side_effect=InvalidPathError("Bad destination"))
        result = await backend.aupload_files([("/file.txt", b"content")])
        assert result[0].get("error") == "invalid_path"

    @pytest.mark.asyncio
    async def test_adownload_files_invalid_path(self, backend, mock_vfs):
        """Test download with invalid path returns error."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.read_file = AsyncMock(side_effect=InvalidPathError("Bad path"))
        result = await backend.adownload_files(["../escape.txt"])
        assert result[0].get("error") == "invalid_path"

    @pytest.mark.asyncio
    async def test_aexecute_python_with_stderr(self, backend, mock_executor):
        """Test execute with stderr appends to output."""
        result_mock = MagicMock()
        result_mock.stdout = "stdout output"
        result_mock.stderr = "stderr output"
        result_mock.success = True
        result_mock.exit_code = 0
        mock_executor.execute = AsyncMock(return_value=result_mock)

        result = await backend.aexecute("__PYTHON__\nprint('test')")
        assert "stdout output" in result.get("output", "")
        assert "stderr output" in result.get("output", "")

    @pytest.mark.asyncio
    async def test_aexecute_python_failure(self, backend, mock_executor):
        """Test execute with failure returns exit code 1."""
        result_mock = MagicMock()
        result_mock.stdout = ""
        result_mock.stderr = "error message"
        result_mock.success = False
        result_mock.exit_code = 1
        mock_executor.execute = AsyncMock(return_value=result_mock)

        result = await backend.aexecute("__PYTHON__\nraise Exception()")
        assert result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_aexecute_shell_with_stderr(self, backend, mock_executor):
        """Test shell execute with stderr appends to output."""
        result_mock = MagicMock()
        result_mock.stdout = "shell stdout"
        result_mock.stderr = "shell stderr"
        result_mock.success = True
        result_mock.exit_code = 0
        mock_executor.execute_shell = AsyncMock(return_value=result_mock)

        result = await backend.aexecute("ls -la")
        assert "shell stdout" in result.get("output", "")
        assert "shell stderr" in result.get("output", "")

    @pytest.mark.asyncio
    async def test_aexecute_shell_none_exit_code(self, backend, mock_executor):
        """Test shell execute with None exit_code defaults based on success."""
        result_mock = MagicMock()
        result_mock.stdout = "output"
        result_mock.stderr = ""
        result_mock.success = False
        result_mock.exit_code = None
        mock_executor.execute_shell = AsyncMock(return_value=result_mock)

        result = await backend.aexecute("failing_command")
        assert result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_aexecute_shell_none_exit_code_success(self, backend, mock_executor):
        """Test shell execute with None exit_code and success=True defaults to 0."""
        result_mock = MagicMock()
        result_mock.stdout = "output"
        result_mock.stderr = ""
        result_mock.success = True
        result_mock.exit_code = None
        mock_executor.execute_shell = AsyncMock(return_value=result_mock)

        result = await backend.aexecute("success_command")
        assert result.get("exit_code") == 0

    @pytest.mark.asyncio
    async def test_als_info_invalid_path(self, backend, mock_vfs):
        """Test ls_info with invalid path returns empty list."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.validate_path = MagicMock(side_effect=InvalidPathError("Bad path"))
        result = await backend.als_info("../escape")
        assert result == []

    @pytest.mark.asyncio
    async def test_aread_invalid_path(self, backend, mock_vfs):
        """Test read with invalid path returns error message."""
        from mayflower_sandbox.filesystem import InvalidPathError

        mock_vfs.read_file = AsyncMock(side_effect=InvalidPathError("Bad path"))
        result = await backend.aread("../escape.txt")
        assert "Error:" in result
        assert "not found" in result


class TestSyncWrappers:
    """Tests for synchronous wrapper methods."""

    @pytest.fixture
    def mock_vfs(self):
        vfs = AsyncMock()
        vfs.validate_path = MagicMock(side_effect=lambda p: p if p.startswith("/") else f"/{p}")
        vfs.file_exists = AsyncMock(return_value=False)
        vfs.list_files = AsyncMock(return_value=[])
        vfs.read_file = AsyncMock(return_value={"content": b"test content"})
        vfs.write_file = AsyncMock()
        return vfs

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        result = MagicMock()
        result.stdout = "output"
        result.stderr = ""
        result.success = True
        result.exit_code = 0
        executor.execute = AsyncMock(return_value=result)
        executor.execute_shell = AsyncMock(return_value=result)
        return executor

    @pytest.fixture
    def backend(self, mock_vfs, mock_executor):
        module = get_module()
        mock_db_pool = MagicMock()
        with (
            patch.object(module, "VirtualFilesystem", return_value=mock_vfs),
            patch.object(module, "SandboxExecutor", return_value=mock_executor),
        ):
            return module.MayflowerSandboxBackend(mock_db_pool, "test_thread")

    def test_read_sync(self, backend, mock_vfs):
        """Test synchronous read wrapper."""
        mock_vfs.read_file = AsyncMock(return_value={"content": b"line1\nline2"})
        result = backend.read("/test.txt")
        assert "line1" in result
        assert "line2" in result

    def test_write_sync(self, backend, mock_vfs):
        """Test synchronous write wrapper."""
        result = backend.write("/new.txt", "content")
        assert result.get("path") == "/new.txt"

    def test_edit_sync(self, backend, mock_vfs):
        """Test synchronous edit wrapper."""
        mock_vfs.read_file = AsyncMock(return_value={"content": b"old text"})
        result = backend.edit("/file.txt", "old", "new")
        assert result.get("path") == "/file.txt"

    def test_ls_info_sync(self, backend, mock_vfs):
        """Test synchronous ls_info wrapper."""
        mock_vfs.list_files = AsyncMock(
            return_value=[{"file_path": "/test.txt", "size": 100, "modified_at": None}]
        )
        result = backend.ls_info("/")
        assert len(result) == 1

    def test_grep_raw_sync(self, backend, mock_vfs):
        """Test synchronous grep_raw wrapper."""
        mock_vfs.list_files = AsyncMock(
            return_value=[{"file_path": "/test.txt", "content": b"match here"}]
        )
        result = backend.grep_raw("match")
        assert isinstance(result, list)

    def test_glob_info_sync(self, backend, mock_vfs):
        """Test synchronous glob_info wrapper."""
        mock_vfs.list_files = AsyncMock(
            return_value=[{"file_path": "/test.py", "size": 100, "modified_at": None}]
        )
        result = backend.glob_info("*.py")
        assert len(result) == 1

    def test_upload_files_sync(self, backend, mock_vfs):
        """Test synchronous upload_files wrapper."""
        result = backend.upload_files([("/file.txt", b"content")])
        assert len(result) == 1

    def test_download_files_sync(self, backend, mock_vfs):
        """Test synchronous download_files wrapper."""
        mock_vfs.read_file = AsyncMock(return_value={"content": b"content"})
        result = backend.download_files(["/file.txt"])
        assert len(result) == 1

    def test_execute_sync(self, backend, mock_executor):
        """Test synchronous execute wrapper."""
        result = backend.execute("ls")
        assert result.get("output") == "output"

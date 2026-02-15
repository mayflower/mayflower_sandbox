"""Unit tests for server.py — covers health check failure and list_files error path."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from mayflower_sandbox.server import FileServer, create_file_server

# ---------------------------------------------------------------------------
# create_file_server factory
# ---------------------------------------------------------------------------


class TestCreateFileServer:
    def test_returns_file_server(self):
        mock_pool = MagicMock()
        server = create_file_server(mock_pool, host="127.0.0.1", port=9090)
        assert isinstance(server, FileServer)
        assert server.host == "127.0.0.1"
        assert server.port == 9090


# ---------------------------------------------------------------------------
# Health check failure path (DB unavailable)
# ---------------------------------------------------------------------------


class TestHealthCheckUnhealthy:
    @pytest.fixture
    async def unhealthy_client(self):
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=Exception("connection refused"))
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        server = FileServer(mock_pool, host="127.0.0.1", port=0)
        async with TestClient(TestServer(server.app)) as client:
            yield client

    @pytest.mark.asyncio
    async def test_unhealthy_returns_503(self, unhealthy_client):
        resp = await unhealthy_client.get("/health")
        assert resp.status == 503
        data = await resp.json()
        assert data["status"] == "unhealthy"
        assert "database unavailable" in data["error"]


# ---------------------------------------------------------------------------
# serve_file error path (non-FileNotFoundError)
# ---------------------------------------------------------------------------


class TestServeFileError:
    @pytest.fixture
    async def error_client(self):
        mock_pool = MagicMock()
        server = FileServer(mock_pool, host="127.0.0.1", port=0)

        with patch("mayflower_sandbox.server.VirtualFilesystem") as mock_vfs_cls:
            mock_vfs = AsyncMock()
            mock_vfs.read_file = AsyncMock(side_effect=RuntimeError("disk error"))
            mock_vfs_cls.return_value = mock_vfs

            async with TestClient(TestServer(server.app)) as client:
                yield client

    @pytest.mark.asyncio
    async def test_internal_error_returns_500(self, error_client):
        resp = await error_client.get("/files/thread1/tmp/test.txt")
        assert resp.status == 500
        data = await resp.json()
        assert "disk error" in data["error"]


# ---------------------------------------------------------------------------
# list_files error path
# ---------------------------------------------------------------------------


class TestListFilesError:
    @pytest.fixture
    async def list_error_client(self):
        mock_pool = MagicMock()
        server = FileServer(mock_pool, host="127.0.0.1", port=0)

        with patch("mayflower_sandbox.server.VirtualFilesystem") as mock_vfs_cls:
            mock_vfs = AsyncMock()
            mock_vfs.list_files = AsyncMock(side_effect=RuntimeError("query failed"))
            mock_vfs_cls.return_value = mock_vfs

            async with TestClient(TestServer(server.app)) as client:
                yield client

    @pytest.mark.asyncio
    async def test_list_error_returns_500(self, list_error_client):
        resp = await list_error_client.get("/files/thread1")
        assert resp.status == 500
        data = await resp.json()
        assert "query failed" in data["error"]

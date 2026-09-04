from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from backend.app.services.print_scheduler import upload_elegoo_file_async


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = f"status {status_code}"

    def json(self) -> dict[str, str]:
        return {"code": "000000"}


class _AsyncClient:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.kwargs: dict[str, Any] = {}

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, *, data: dict[str, str], files: dict[str, Any]) -> _Response:
        self.calls.append({"url": url, "data": data, "files": files})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_elegoo_upload_uses_one_megabyte_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client = _AsyncClient([_Response(200), _Response(200)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    local_path = tmp_path / "job.gcode"
    local_path.write_bytes(b"x" * (1024 * 1024 + 1))

    assert await upload_elegoo_file_async("192.0.2.1", "", local_path, "job.gcode") is True

    chunks = [call["files"]["File"][1] for call in client.calls]
    assert [len(chunk) for chunk in chunks] == [1024 * 1024, 1]


@pytest.mark.asyncio
async def test_elegoo_upload_returns_false_on_http_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client = _AsyncClient([_Response(500)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    local_path = tmp_path / "job.gcode"
    local_path.write_bytes(b"gcode")

    assert await upload_elegoo_file_async("192.0.2.1", "", local_path, "job.gcode") is False


@pytest.mark.asyncio
async def test_elegoo_upload_timeout_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client = _AsyncClient([_Response(200)])
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: (client.kwargs.update(kwargs) or client),
    )
    local_path = tmp_path / "job.gcode"
    local_path.write_bytes(b"gcode")

    assert await upload_elegoo_file_async("192.0.2.1", "", local_path, "job.gcode") is True

    timeout = client.kwargs["timeout"]
    assert timeout.connect == 15.0
    assert timeout.read == 180.0


@pytest.mark.asyncio
async def test_elegoo_upload_logs_exception_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    class _FailingClient(_AsyncClient):
        async def post(self, url: str, *, data: dict[str, str], files: dict[str, Any]) -> _Response:
            raise httpx.ConnectTimeout("printer HTTP service unavailable")

    client = _FailingClient([])
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: (client.kwargs.update(kwargs) or client),
    )
    local_path = tmp_path / "job.gcode"
    local_path.write_bytes(b"gcode")

    with caplog.at_level(logging.WARNING):
        assert await upload_elegoo_file_async("192.0.2.1", "", local_path, "job.gcode") is False

    assert "ConnectTimeout" in caplog.text

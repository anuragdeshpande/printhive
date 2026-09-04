from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.config import settings as app_settings
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.archive import ArchiveService
from backend.app.services.virtual_printer.elegoo_sdcp_server import ElegooSDCPServer


@pytest.mark.asyncio
@pytest.mark.integration
async def test_elegoo_link_gcode_upload_creates_queue_item(
    async_client: AsyncClient,
    db_session,
    printer_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A successful Orca upload must produce a visible pending queue item."""
    printer = await printer_factory(model="CC1", name="Elegoo Test Printer")
    monkeypatch.setattr(app_settings, "base_dir", tmp_path)
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")

    filename = "ECC_test.gcode"
    content = b"; generated gcode\nG28\n"
    response = await async_client.post(
        "/uploadFile/upload",
        data={"Offset": "0", "TotalSize": str(len(content))},
        files={"File": (filename, content, "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["filename"] == filename

    result = await db_session.execute(
        select(PrintQueueItem).where(
            PrintQueueItem.printer_id == printer.id,
            PrintQueueItem.status == "pending",
        )
    )
    item = result.scalar_one()
    assert item.archive_id is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_elegoo_link_upload_reports_enqueue_failure(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Orca must not receive success when archiving or queueing fails."""
    monkeypatch.setattr(app_settings, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(
        ArchiveService,
        "archive_print",
        AsyncMock(side_effect=RuntimeError("archive unavailable")),
    )

    filename = "ECC_failed.gcode"
    content = b"; generated gcode\nG28\n"
    response = await async_client.post(
        "/uploadFile/upload",
        data={"Offset": "0", "TotalSize": str(len(content))},
        files={"File": (filename, content, "text/plain")},
    )

    assert response.status_code == 500
    assert response.json()["code"] == 500
    assert not (app_settings.archive_dir / "temp" / filename).exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sdcp_options_only_update_matching_pending_upload(
    async_client: AsyncClient,
    db_session,
    printer_factory,
    archive_factory,
) -> None:
    """SDCP options must not leak to unrelated pending queue items."""
    printer = await printer_factory(model="CC1")
    target_archive = await archive_factory(printer.id, filename="target.gcode")
    other_archive = await archive_factory(printer.id, filename="other.gcode")
    target_item = PrintQueueItem(
        printer_id=printer.id,
        archive_id=target_archive.id,
        position=1,
        status="pending",
    )
    other_item = PrintQueueItem(
        printer_id=printer.id,
        archive_id=other_archive.id,
        position=2,
        status="pending",
    )
    db_session.add_all([target_item, other_item])
    await db_session.commit()

    await ElegooSDCPServer()._update_pending_queue_item("target.gcode", 1, 1)

    await db_session.refresh(target_item)
    await db_session.refresh(other_item)
    assert target_item.bed_levelling == "on"
    assert target_item.timelapse is True
    assert other_item.bed_levelling == "auto"
    assert other_item.timelapse is False

"""Unit tests for scheduler dispatch safety and exception handling.

Guarantees that:
1. `_start_print` handles unexpected exceptions (like TypeErrors from upstream signature changes)
   gracefully without crashing the background dispatch task.
2. `self._terminal_since` is cleared on print start so stale idle times cannot trigger
   immediate cascade cancellations of the queue.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest

from backend.app.services.print_scheduler import PrintScheduler
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.models.archive import PrintArchive


class TestSchedulerDispatchSafety:
    @pytest.fixture
    def scheduler(self):
        return PrintScheduler()

    @pytest.mark.asyncio
    async def test_start_print_handles_typeerror_gracefully(self, scheduler, tmp_path):
        """If printer_manager.start_print raises a TypeError (e.g. unexpected argument from upstream),
        _start_print must catch it, log it, and not crash.
        """
        dummy_file = tmp_path / "test.gcode"
        dummy_file.write_text("M104 S200\n")

        mock_db = AsyncMock()
        printer = Printer(id=2, name="Elegoo CC1", ip_address="192.168.1.235", model="CC1", serial_number="CC1_SERIAL")
        archive = PrintArchive(id=50, filename="test.gcode", file_path="test.gcode")
        item = PrintQueueItem(id=999, printer_id=2, archive_id=50, status="pending", plate_id=1, gcode_injection=False)

        def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            q_str = str(query)
            if "printers" in q_str:
                m.scalar_one_or_none.return_value = printer
            elif "print_archives" in q_str:
                m.scalar_one_or_none.return_value = archive
            else:
                m.scalar_one_or_none.return_value = None
            m.rowcount = 1
            return m

        mock_db.execute.side_effect = mock_execute

        # Pre-seed stale idle timer for printer 2
        scheduler._terminal_since[2] = 12345.0

        with (
            patch("backend.app.services.print_scheduler.settings.base_dir", tmp_path),
            patch("backend.app.services.print_scheduler.get_ftp_retry_settings", new_callable=AsyncMock, return_value=(True, 3, 1.0, 30.0)),
            patch("backend.app.services.print_scheduler.estimate_queue_source_cost", new_callable=AsyncMock, return_value=0.0),
            patch("backend.app.services.print_scheduler.validate_print_budget", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.create_budget_reservation", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.with_ftp_retry", new_callable=AsyncMock, return_value=True),
            patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
            patch("backend.app.services.print_scheduler.notification_service", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.ws_manager", new_callable=AsyncMock),
            patch("backend.app.main.register_expected_print"),
        ):
            mock_pm.is_connected.return_value = True
            mock_pm.get_status.return_value = MagicMock(state="IDLE")
            mock_pm.get_client.return_value = MagicMock()
            mock_pm.start_print.side_effect = TypeError("start_print() got an unexpected keyword argument 'nozzle_slot_extruders'")
            mock_pm.get_model.return_value = "CC1"
            mock_pm.get_printer_settings.return_value = {}

            # Execute _start_print - must not raise exception
            await scheduler._start_print(mock_db, item)

            mock_pm.start_print.assert_called_once()
            # Verify the stale idle timer was cleared so it cannot immediately cascade-cancel
            assert 2 not in scheduler._terminal_since

    @pytest.mark.asyncio
    async def test_start_print_handles_runtime_error_gracefully(self, scheduler, tmp_path):
        """If printer_manager.start_print raises an unexpected RuntimeError, it must be caught safely."""
        dummy_file = tmp_path / "test.3mf"
        dummy_file.write_text("dummy 3mf content")

        mock_db = AsyncMock()
        printer = Printer(id=1, name="Bambu X1C", ip_address="192.168.1.113", model="X1C", serial_number="X1C_SERIAL")
        archive = PrintArchive(id=51, filename="test.3mf", file_path="test.3mf")
        item = PrintQueueItem(id=1000, printer_id=1, archive_id=51, status="pending", plate_id=1, gcode_injection=False)

        def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            q_str = str(query)
            if "printers" in q_str:
                m.scalar_one_or_none.return_value = printer
            elif "print_archives" in q_str:
                m.scalar_one_or_none.return_value = archive
            else:
                m.scalar_one_or_none.return_value = None
            m.rowcount = 1
            return m

        mock_db.execute.side_effect = mock_execute

        scheduler._terminal_since[1] = 54321.0

        with (
            patch("backend.app.services.print_scheduler.settings.base_dir", tmp_path),
            patch("backend.app.services.print_scheduler.get_ftp_retry_settings", new_callable=AsyncMock, return_value=(True, 3, 1.0, 30.0)),
            patch("backend.app.services.print_scheduler.estimate_queue_source_cost", new_callable=AsyncMock, return_value=0.0),
            patch("backend.app.services.print_scheduler.validate_print_budget", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.create_budget_reservation", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.with_ftp_retry", new_callable=AsyncMock, return_value=True),
            patch("backend.app.services.print_scheduler.printer_manager") as mock_pm,
            patch("backend.app.services.print_scheduler.notification_service", new_callable=AsyncMock),
            patch("backend.app.services.print_scheduler.ws_manager", new_callable=AsyncMock),
            patch("backend.app.main.register_expected_print"),
        ):
            mock_pm.is_connected.return_value = True
            mock_pm.get_status.return_value = MagicMock(state="IDLE")
            mock_pm.get_client.return_value = MagicMock()
            mock_pm.start_print.side_effect = RuntimeError("WebSocket closed unexpectedly")
            mock_pm.get_model.return_value = "X1C"
            mock_pm.get_printer_settings.return_value = {}

            await scheduler._start_print(mock_db, item)

            mock_pm.start_print.assert_called_once()
            assert 1 not in scheduler._terminal_since

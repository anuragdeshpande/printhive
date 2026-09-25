import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

from backend.app.services.elegoo_client import ElegooCentauriClient, is_elegoo_model
from backend.app.services.bambu_mqtt import PrinterState
from pycentauri.models import Status, Attributes, PrintInfo

def test_is_elegoo_model():
    assert is_elegoo_model("Centauri Carbon") is True
    assert is_elegoo_model("CC1") is True
    assert is_elegoo_model("CC2") is True
    assert is_elegoo_model("Elegoo CC2") is True
    assert is_elegoo_model("Bambu X1C") is False
    assert is_elegoo_model(None) is False

class TestElegooCentauriClient:
    @pytest.fixture
    def client(self):
        return ElegooCentauriClient(
            ip_address="192.168.1.150",
            serial_number="ELEGOO123",
            access_code="Abcd12",
            model="CC2",
        )

    def test_init(self, client):
        assert client.ip_address == "192.168.1.150"
        assert client.serial_number == "ELEGOO123"
        assert client.access_code == "Abcd12"
        assert client.model == "CC2"
        assert isinstance(client.state, PrinterState)
        assert client.connected is False

    @patch("backend.app.services.elegoo_client.elegoo_discover", new_callable=AsyncMock)
    @patch("backend.app.services.elegoo_client.connect_auto", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_async_connect(self, mock_connect, mock_discover, client):
        mock_discover.return_value = []
        async def mock_watch():
            if False:
                yield
            raise asyncio.CancelledError()

        mock_printer = MagicMock()
        mock_printer.watch = mock_watch
        mock_printer.close = AsyncMock()
        mock_connect.return_value = mock_printer

        client._loop = asyncio.get_running_loop()
        await client._async_connect()

        assert client.state.connected is True
        mock_discover.assert_called_once_with(
            timeout=2.0,
            broadcast_address="192.168.1.150",
        )
        mock_connect.assert_called_once_with(
            "192.168.1.150",
            access_code="Abcd12",
            connect_timeout=5.0,
            enable_control=True,
            mainboard_id=None,
        )

    @patch("backend.app.services.elegoo_client.elegoo_discover", new_callable=AsyncMock)
    @patch("backend.app.services.elegoo_client.connect_auto", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_async_connect_marks_normal_watch_disconnect_offline(
        self, mock_connect, mock_discover, client, monkeypatch
    ):
        mock_discover.return_value = []
        monkeypatch.setattr("backend.app.services.elegoo_client.asyncio.sleep", AsyncMock())

        async def mock_watch():
            if False:
                yield

        mock_printer = MagicMock()
        mock_printer.mainboard_id = None
        mock_printer.watch = mock_watch
        mock_printer.close = AsyncMock()
        mock_connect.side_effect = [mock_printer, asyncio.CancelledError()]

        await client._async_connect()

        assert client.state.connected is False
        assert mock_printer.close.await_count == 1

    def test_update_state_running(self, client):
        status_payload = {
            "TempOfNozzle": 210,
            "TempTargetNozzle": 220,
            "TempOfHotbed": 60,
            "TempTargetHotbed": 60,
            "CurrentFanSpeed": {
                "ModelFan": 80,
                "AuxiliaryFan": 50,
                "BoxFan": 0,
            },
            "LightStatus": {
                "SecondLight": 1,
            },
            "PrintInfo": {
                "Status": 13,  # Printing
                "Filename": "test_job.gcode",
                "Progress": 42,
                "CurrentLayer": 15,
                "TotalLayer": 150,
                "PrintSpeedPct": 100,
            }
        }
        status = Status.from_payload(status_payload)
        client._update_state(status)

        assert client.state.temperatures["nozzle"] == 210.0
        assert client.state.temperatures["nozzle_target"] == 220.0
        assert client.state.temperatures["bed"] == 60.0
        assert client.state.temperatures["bed_target"] == 60.0
        assert client.state.cooling_fan_speed == 80
        assert client.state.big_fan1_speed == 50
        assert client.state.big_fan2_speed == 0
        assert client.state.chamber_light is True
        assert client.state.current_print == "test_job.gcode"
        assert client.state.progress == 42.0
        assert client.state.layer_num == 15
        assert client.state.total_layers == 150
        assert client.state.speed_level == 2
        assert client.state.state == "RUNNING"
        assert client.state.stg_cur == 0

    def test_update_state_preparation_stages(self, client):
        prep_codes = [
            (10, 52),  # File Checking -> Material check
            (11, 44),  # Printer Checking -> Platform check
            (15, 1),   # Auto Bed Leveling
            (16, 2),   # Preheating
            (17, 3),   # Vibration Compensation
            (18, 74),  # Starting Print / Preparing
        ]

        for code, expected_stg in prep_codes:
            raw = {"PrintInfo": {"Status": code, "Filename": "", "Progress": 0}}
            status = Status.from_payload(raw)
            client.state.current_print = "active_job.gcode"
            client._update_state(status)

            assert client.state.state == "PREPARE"
            assert client.state.stg_cur == expected_stg
            # Verify optimistic current_print preservation during PREPARE
            assert client.state.current_print == "active_job.gcode"

    def test_update_state_terminal_clears_job(self, client):
        client.state.current_print = "finished_job.gcode"
        client.state.subtask_name = "finished_job.gcode"
        client.state.gcode_file = "finished_job.gcode"

        raw = {"PrintInfo": {"Status": 0, "Filename": "", "Progress": 0}}  # Status 0 = IDLE
        status = Status.from_payload(raw)
        client._update_state(status)

        assert client.state.state == "IDLE"
        assert client.state.current_print == ""
        assert client.state.subtask_name == ""
        assert client.state.gcode_file == ""

    def test_control_commands(self, client):
        client._printer = MagicMock()
        client._printer.stop = AsyncMock()
        client._printer.pause = AsyncMock()
        client._printer.resume = AsyncMock()
        client._printer.set_print_speed = AsyncMock()
        client._printer.set_temperatures = AsyncMock()
        client._printer.set_fan_speed = AsyncMock()
        client._printer.start_print = AsyncMock()
        client._loop = MagicMock()

        assert client.stop_print() is True
        assert client.pause_print() is True
        assert client.resume_print() is True
        assert client.set_print_speed(1) is True
        assert client.set_nozzle_temperature(230) is True
        assert client.set_bed_temperature(65) is True
        assert client.set_fan_speed(1, 128) is True

    def test_start_print_platform_types(self, client):
        from backend.app.services.elegoo_client import map_bed_type_to_elegoo_platform_type

        # Verify platform type mapping
        assert map_bed_type_to_elegoo_platform_type("Textured PEI Plate") == 0
        assert map_bed_type_to_elegoo_platform_type("textured_plate") == 0
        assert map_bed_type_to_elegoo_platform_type("High Temp Plate") == 1
        assert map_bed_type_to_elegoo_platform_type("Smooth PEI Plate") == 1
        assert map_bed_type_to_elegoo_platform_type("hot_plate") == 1
        assert map_bed_type_to_elegoo_platform_type("Engineering Plate") == 2
        assert map_bed_type_to_elegoo_platform_type("eng_plate") == 2
        assert map_bed_type_to_elegoo_platform_type("Cool Plate") == 1
        assert map_bed_type_to_elegoo_platform_type("cool_plate") == 1
        assert map_bed_type_to_elegoo_platform_type("SuperTack Plate") == 4
        assert map_bed_type_to_elegoo_platform_type("epoxy_plate") == 4
        assert map_bed_type_to_elegoo_platform_type(None) == 0

        # Verify start_print delegates with correct platform_type
        mock_printer = MagicMock()
        mock_printer.start_print = AsyncMock()
        client._printer = mock_printer
        client._loop = MagicMock()

        # Test with High Temp / Smooth PEI
        client.start_print("test.gcode", bed_type="High Temp Plate")
        mock_printer.start_print.assert_called_with(
            "test.gcode",
            storage="local",
            auto_leveling=True,
            timelapse=False,
            platform_type=1,
        )

        # Test with Engineering Plate
        client.start_print("test.gcode", bed_type="Engineering Plate")
        mock_printer.start_print.assert_called_with(
            "test.gcode",
            storage="local",
            auto_leveling=True,
            timelapse=False,
            platform_type=2,
        )

        # Test with Cool Plate
        client.start_print("test.gcode", bed_type="Cool Plate")
        mock_printer.start_print.assert_called_with(
            "test.gcode",
            storage="local",
            auto_leveling=True,
            timelapse=False,
            platform_type=1,
        )

    def test_pause_and_resume_telemetry(self, client):
        mock_printer = MagicMock()
        client._printer = mock_printer

        client.pause_telemetry()
        mock_printer.pause_watch.assert_called_once()

        client.resume_telemetry()
        mock_printer.resume_watch.assert_called_once()

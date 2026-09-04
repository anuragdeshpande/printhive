"""Unit tests for ElegooAdapter in printer feature pipeline."""

from unittest.mock import MagicMock, patch
import pytest

from backend.app.services.printer_pipeline.adapters.elegoo_adapter import ElegooAdapter
from backend.app.services.printer_pipeline.capabilities import PrinterCapability
from backend.app.services.printer_pipeline.models import (
    JogRequest,
    MotionAxis,
    StandardPrintJobRequest,
)


class TestElegooAdapter:
    @pytest.fixture
    def mock_elegoo_client(self):
        client = MagicMock()
        client.state.connected = True
        client.state.temperatures = {"nozzle": 200, "bed": 60, "chamber": 35}
        client.state.raw_data = {}
        return client

    @pytest.fixture
    def adapter(self, mock_elegoo_client):
        return ElegooAdapter(
            printer_id=2,
            ip_address="192.168.1.236",
            serial_number="107319580103d66400000c0000000000",
            model="Centauri Carbon",
            client=mock_elegoo_client,
        )

    def test_capabilities_report(self, adapter):
        caps = adapter.capabilities
        assert caps.has(PrinterCapability.TELEMETRY)
        assert caps.has(PrinterCapability.PRINT_CONTROL)
        assert caps.has(PrinterCapability.START_PRINT)
        assert caps.has(PrinterCapability.MOTION_HOME)
        assert caps.has(PrinterCapability.MOTION_JOG)
        assert caps.has(PrinterCapability.HEATED_BED)
        assert caps.has(PrinterCapability.EXTRUDER_HEATER)
        assert caps.has(PrinterCapability.CHAMBER_SENSOR)
        assert caps.has(PrinterCapability.FAN_PART)
        assert caps.has(PrinterCapability.FAN_AUX)
        assert caps.has(PrinterCapability.FAN_CHAMBER)
        assert caps.has(PrinterCapability.CHAMBER_LIGHT)
        assert caps.has(PrinterCapability.STORAGE_LOCAL)
        assert caps.has(PrinterCapability.FILE_LIST)
        assert caps.has(PrinterCapability.FILE_DOWNLOAD)
        assert caps.has(PrinterCapability.GCODE_CONSOLE)

    def test_motion_delegation(self, adapter, mock_elegoo_client):
        mock_elegoo_client.home_axes.return_value = True
        mock_elegoo_client.move_axis.return_value = True

        assert adapter.home("XYZ") is True
        mock_elegoo_client.home_axes.assert_called_once_with("XYZ")

        assert adapter.jog(JogRequest(axis=MotionAxis.Z, distance_mm=10.0)) is True
        mock_elegoo_client.move_axis.assert_called_once_with(axis="Z", distance=10.0, speed=3000)

    def test_job_control_delegation(self, adapter, mock_elegoo_client):
        mock_elegoo_client.start_print.return_value = True
        mock_elegoo_client.pause_print.return_value = True
        mock_elegoo_client.resume_print.return_value = True
        mock_elegoo_client.stop_print.return_value = True

        req = StandardPrintJobRequest(filename="print.gcode", plate_id=1, bed_levelling="auto")
        assert adapter.start_print(req) is True
        mock_elegoo_client.start_print.assert_called_once_with(
            filename="print.gcode",
            plate_id=1,
            ams_mapping=None,
            auto_level=True,
            timelapse=False,
            bed_type=None,
        )

        assert adapter.pause_print() is True
        mock_elegoo_client.pause_print.assert_called_once()

        assert adapter.resume_print() is True
        mock_elegoo_client.resume_print.assert_called_once()

        assert adapter.stop_print() is True
        mock_elegoo_client.stop_print.assert_called_once()

    def test_thermal_and_fans_delegation(self, adapter, mock_elegoo_client):
        mock_elegoo_client.set_bed_temperature.return_value = True
        mock_elegoo_client.set_nozzle_temperature.return_value = True
        mock_elegoo_client.set_fan_speed.return_value = True

        assert adapter.set_bed_temperature(60) is True
        mock_elegoo_client.set_bed_temperature.assert_called_once_with(60.0)

        assert adapter.set_nozzle_temperature(220, nozzle=0) is True
        mock_elegoo_client.set_nozzle_temperature.assert_called_once_with(220.0, nozzle=0)

        assert adapter.set_part_fan(255) is True
        mock_elegoo_client.set_fan_speed.assert_called_once_with(1, 255)

    def test_gcode_console_delegation(self, adapter, mock_elegoo_client):
        mock_elegoo_client.send_gcode.return_value = True
        assert adapter.send_gcode("G28\nG1 Z10\nM104 S200") is True
        mock_elegoo_client.send_gcode.assert_called_once_with("G28\nG1 Z10\nM104 S200")

    def test_download_file_uses_correct_endpoint(self, adapter):
        with patch("httpx.Client.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"GCODE_DATA"
            mock_get.return_value = mock_resp

            content = adapter.download_file("test.gcode")
            assert content == b"GCODE_DATA"
            mock_get.assert_called_once_with("http://192.168.1.236:3030/downloadFile/test.gcode")

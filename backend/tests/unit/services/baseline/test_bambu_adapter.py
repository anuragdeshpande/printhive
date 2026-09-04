"""Unit tests for BambuAdapter in printer feature pipeline."""

from unittest.mock import MagicMock
import pytest

from backend.app.services.printer_pipeline.adapters.bambu_adapter import BambuAdapter
from backend.app.services.printer_pipeline.capabilities import PrinterCapability
from backend.app.services.printer_pipeline.models import (
    JogRequest,
    MotionAxis,
    StandardPrintJobRequest,
)


class TestBambuAdapter:
    @pytest.fixture
    def mock_mqtt(self):
        mqtt = MagicMock()
        mqtt.state.connected = True
        mqtt.state.firmware_version = "01.09.00.00"
        mqtt.state.raw_data = {}
        return mqtt

    @pytest.fixture
    def adapter(self, mock_mqtt):
        return BambuAdapter(
            printer_id=1,
            ip_address="192.168.1.114",
            serial_number="00M09A123456789",
            access_code="12345678",
            model="X1C",
            mqtt_client=mock_mqtt,
        )

    def test_capabilities_report(self, adapter):
        caps = adapter.capabilities
        assert caps.has(PrinterCapability.TELEMETRY)
        assert caps.has(PrinterCapability.PRINT_CONTROL)
        assert caps.has(PrinterCapability.MOTION_JOG)
        assert caps.has(PrinterCapability.MOTION_HOME)
        assert caps.has(PrinterCapability.CHAMBER_SENSOR)  # X1C has sensor
        assert caps.has(PrinterCapability.DRYING_REMOTE)   # X1C with 01.09+

    def test_motion_delegation(self, adapter, mock_mqtt):
        mock_mqtt.home_axes.return_value = True
        mock_mqtt.move_axis.return_value = True
        mock_mqtt.disable_motors.return_value = True

        assert adapter.home("XYZ") is True
        mock_mqtt.home_axes.assert_called_once_with("XYZ")

        assert adapter.jog(JogRequest(axis=MotionAxis.X, distance_mm=15.0, speed_mm_min=2400)) is True
        mock_mqtt.move_axis.assert_called_once_with(axis="X", distance=15.0, speed=2400)

        assert adapter.disable_motors() is True
        mock_mqtt.disable_motors.assert_called_once()

    def test_job_control_delegation(self, adapter, mock_mqtt):
        mock_mqtt.start_print.return_value = True
        mock_mqtt.pause_print.return_value = True
        mock_mqtt.resume_print.return_value = True
        mock_mqtt.stop_print.return_value = True

        req = StandardPrintJobRequest(filename="plate.gcode", plate_id=2, ams_mapping=[0, 1])
        assert adapter.start_print(req) is True
        assert mock_mqtt.start_print.call_count == 1

        assert adapter.pause_print() is True
        mock_mqtt.pause_print.assert_called_once()

        assert adapter.resume_print() is True
        mock_mqtt.resume_print.assert_called_once()

        assert adapter.stop_print() is True
        mock_mqtt.stop_print.assert_called_once()

    def test_thermal_and_fan_delegation(self, adapter, mock_mqtt):
        mock_mqtt.set_bed_temperature.return_value = True
        mock_mqtt.set_nozzle_temperature.return_value = True
        mock_mqtt.set_part_fan.return_value = True

        assert adapter.set_bed_temperature(65) is True
        mock_mqtt.set_bed_temperature.assert_called_once_with(65)

        assert adapter.set_nozzle_temperature(210, nozzle=0) is True
        mock_mqtt.set_nozzle_temperature.assert_called_once_with(210, nozzle=0)

        assert adapter.set_part_fan(200) is True
        mock_mqtt.set_part_fan.assert_called_once_with(200)

"""Contract tests for non-Bambu printer clients (Elegoo, Flashforge).

Enforces that all printer client implementations are forward-compatible with
any arbitrary keyword arguments passed by PrinterManager or upstream Bambuddy,
preventing TypeErrors when upstream adds new dispatch or control parameters.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.app.services.elegoo_client import ElegooCentauriClient
from backend.app.services.flashforge_client import FlashforgeClient


CONTROL_METHODS = [
    ("start_print", {"filename": "test.gcode"}),
    ("stop_print", {}),
    ("pause_print", {}),
    ("resume_print", {}),
    ("set_nozzle_temperature", {"target": 220.0}),
    ("set_bed_temperature", {"target": 60.0}),
    ("set_fan_speed", {"fan_id": 1, "pwm_speed": 128}),
]


class TestElegooClientContract:
    @pytest.fixture
    def client(self):
        c = ElegooCentauriClient(
            ip_address="192.168.1.235",
            serial_number="test_serial",
            access_code="test_code",
            model="CC1",
        )
        c._printer = MagicMock()
        c._printer.start_print = AsyncMock()
        c._printer.stop = AsyncMock()
        c._printer.pause = AsyncMock()
        c._printer.resume = AsyncMock()
        c._printer.set_temperatures = AsyncMock()
        c._printer.set_fan_speed = AsyncMock()
        c._printer.set_print_speed = AsyncMock()
        c._loop = MagicMock()
        c._run_async = MagicMock(return_value=True)
        return c

    @pytest.mark.parametrize("method_name,base_kwargs", CONTROL_METHODS)
    def test_elegoo_methods_accept_arbitrary_kwargs(self, client, method_name, base_kwargs):
        """Every control method must accept unexpected kwargs without raising TypeError."""
        method = getattr(client, method_name)
        # Verify inspection: must accept VAR_KEYWORD (**kwargs)
        sig = inspect.signature(method)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        assert has_var_kw, f"ElegooCentauriClient.{method_name} must accept **kwargs"

        # Call with unexpected kwargs introduced by upstream merges
        kwargs = {
            **base_kwargs,
            "nozzle_slot_extruders": [0, 1],
            "nozzle_mapping": "{}",
            "future_upstream_arg_xyz": 12345,
            "random_extra_flag": True,
        }
        # Calling should not raise TypeError
        try:
            method(**kwargs)
        except TypeError as exc:
            pytest.fail(f"ElegooCentauriClient.{method_name} failed with unexpected kwargs: {exc}")

    def test_elegoo_update_state_when_print_info_is_none(self, client):
        """Elegoo status updates must process print_status even if print_info is None."""
        on_start = MagicMock()
        client.state_machine.on_print_start = on_start
        client.state_machine.record_dispatch(filename="bench.gcode")

        # Fake Status with print_info = None and print_status = 15 (auto bed leveling -> PREPARE)
        fake_status = MagicMock()
        fake_status.print_info = None
        fake_status.print_status = 15
        fake_status.temp_nozzle = 210.0
        fake_status.temp_nozzle_target = 210.0
        fake_status.temp_bed = 60.0
        fake_status.temp_bed_target = 60.0
        fake_status.temp_chamber = 0.0
        fake_status.temp_chamber_target = 0.0
        fake_status.fan_speed = {}
        fake_status.light = {}

        client._update_state(fake_status)

        assert client.state.state == "PREPARE"
        assert client.state.stg_cur == 1  # 15 mapped to Bambu stage 1 (bed leveling)
        assert client.state.current_print == "bench.gcode"
        assert client.state.subtask_id == client.last_dispatch_subtask_id
        on_start.assert_called_once()
        assert on_start.call_args[0][0]["filename"] == "bench.gcode"

    def test_elegoo_start_print_records_subtask_id(self, client):
        """start_print must mint a subtask_id on client and state_machine."""
        assert client.last_dispatch_subtask_id is None
        res = client.start_print("my_print.gcode")
        assert res is True
        assert client.last_dispatch_subtask_id is not None
        assert client.state.subtask_id == client.last_dispatch_subtask_id
        assert client.state.current_print == "my_print.gcode"


class TestFlashforgeClientContract:
    @pytest.fixture
    def client(self):
        c = FlashforgeClient(
            ip_address="192.168.1.200",
            serial_number="test_serial",
            access_code="test_code",
            model="Creator 5",
        )
        c._send_command = MagicMock(return_value=True)
        return c

    @pytest.mark.parametrize("method_name,base_kwargs", [
        ("start_print", {"filename": "test.gcode"}),
        ("stop_print", {}),
        ("pause_print", {}),
        ("resume_print", {}),
        ("set_nozzle_temperature", {"temp": 220}),
        ("set_bed_temperature", {"temp": 60}),
        ("set_fan_speed", {"fan_id": 1, "speed_pwm": 128}),
        ("set_chamber_light", {"on": True}),
        ("select_toolhead", {"tool_index": 0}),
    ])
    def test_flashforge_methods_accept_arbitrary_kwargs(self, client, method_name, base_kwargs):
        """Every Flashforge control method must accept unexpected kwargs without raising TypeError."""
        method = getattr(client, method_name)
        sig = inspect.signature(method)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        assert has_var_kw, f"FlashforgeClient.{method_name} must accept **kwargs"

        kwargs = {
            **base_kwargs,
            "nozzle_slot_extruders": [0, 1],
            "nozzle_mapping": "{}",
            "future_upstream_arg_xyz": 12345,
        }
        try:
            method(**kwargs)
        except TypeError as exc:
            pytest.fail(f"FlashforgeClient.{method_name} failed with unexpected kwargs: {exc}")

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

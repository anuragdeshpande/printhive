"""Baseline Characterization Tests for Bambu Lab integration and PrinterManager.

This test suite establishes the "Golden Baseline" for all existing Bambu Lab
functionality and contracts BEFORE any architecture refactoring.
It guarantees:
1. Complete fidelity in Bambu telemetry ingestion, state transitions, and normalization.
2. Exact wire command payload structures (start_print, motion, fans, lights, temperatures, AMS).
3. Contract compliance for PrinterManager <-> Client interactions and printer_state_to_dict outputs.
"""

import json
import time
from unittest.mock import MagicMock, patch, call

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, PrinterState, HMSError
from backend.app.services.printer_manager import (
    PrinterManager,
    printer_state_to_dict,
    supports_chamber_temp,
    supports_chamber_heater,
    supports_drying,
    supports_drying_while_printing,
    drying_screen_only,
    has_stg_cur_idle_bug,
    get_derived_status_name,
)


# ============================================================================
# Realistic Bambu Telemetry Fixtures
# ============================================================================

SAMPLE_BAMBU_PUSH_ALL_PAYLOAD = {
    "print": {
        "command": "push_status",
        "msg": 1,
        "sequence_id": "1",
        "gcode_state": "RUNNING",
        "mc_percent": 45,
        "mc_remaining_time": 72,
        "layer_num": 120,
        "total_layer_num": 350,
        "subtask_name": "benchy.gcode",
        "gcode_file": "/Metadata/plate_1.gcode",
        "nozzle_temper": 220.5,
        "nozzle_target_temper": 220.0,
        "bed_temper": 60.1,
        "bed_target_temper": 60.0,
        "chamber_temper": 38.0,
        "cooling_fan_speed": "12",  # 12/15 -> ~80%
        "heatbreak_fan_speed": "15",
        "big_fan1_speed": "8",     # Aux fan
        "big_fan2_speed": "4",     # Chamber fan
        "spd_lvl": 2,              # Standard
        "stg_cur": 0,
        "wifi_signal": "-52dBm",
        "lights_report": [{"node": "chamber_light", "mode": "on"}],
        "hms": [
            134250496  # Example raw HMS code (0x08008000)
        ],
        "ams": {
            "ams": [
                {
                    "id": "0",
                    "temp": "28.5",
                    "humidity": "3",
                    "humidity_raw": "24",
                    "sn": "00M00A123456789",
                    "sw_ver": "00.00.06.40",
                    "dry_time": 0,
                    "dry_status": 0,
                    "dry_sub_status": 0,
                    "dry_sf_reason": [],
                    "tray": [
                        {
                            "id": "0",
                            "tray_color": "FF0000FF",
                            "tray_type": "PLA",
                            "tray_sub_brands": "Bambu PLA Basic",
                            "tray_id_name": "PLA Basic Red",
                            "tray_info_idx": "GFA00",
                            "remain": 85,
                            "k": 0.022,
                            "cali_idx": 0,
                            "tag_uid": "0102030405060708",
                            "tray_uuid": "112233445566778899aabbccddeeff00",
                            "nozzle_temp_min": 190,
                            "nozzle_temp_max": 240,
                            "drying_temp": 55,
                            "drying_time": 8,
                            "state": 0,
                        },
                        {
                            "id": "1",
                            "tray_color": "00FF00FF",
                            "tray_type": "PETG",
                            "tray_sub_brands": "Bambu PETG HF",
                            "tray_id_name": "PETG Green",
                            "tray_info_idx": "GFG00",
                            "remain": 40,
                            "k": 0.035,
                            "cali_idx": 1,
                            "tag_uid": "A1A2A3A4A5A6A7A8",
                            "tray_uuid": "99887766554433221100aabbccddeeff",
                            "nozzle_temp_min": 220,
                            "nozzle_temp_max": 260,
                            "drying_temp": 65,
                            "drying_time": 8,
                            "state": 0,
                        },
                    ],
                }
            ],
            "ams_exist_bits": "1",
            "tray_exist_bits": "3",
            "tray_now": "0",
            "tray_pre": "0",
            "tray_tar": "0",
        },
        "vt_tray": {
            "id": "254",
            "tray_color": "FFFFFFFF",
            "tray_type": "TPU",
            "tray_sub_brands": "Generic TPU",
            "remain": 90,
            "k": 0.080,
            "nozzle_temp_min": 200,
            "nozzle_temp_max": 240,
        },
    }
}


# ============================================================================
# Telemetry Baseline Tests
# ============================================================================

class TestBambuTelemetryBaseline:
    """Characterizes Bambu MQTT telemetry ingestion and state management."""

    @pytest.fixture
    def client(self):
        client = BambuMQTTClient(
            ip_address="192.168.1.114",
            serial_number="00M09A123456789",
            access_code="12345678",
            model="X1C",
        )
        client._client = MagicMock()
        client.state.connected = True
        return client

    def test_full_push_status_updates_core_state(self, client):
        """Verify full push_status payload updates state correctly."""
        client._process_message(SAMPLE_BAMBU_PUSH_ALL_PAYLOAD)

        assert client.state.connected is True
        assert client.state.state == "RUNNING"
        assert client.state.progress == 45
        assert client.state.remaining_time == 72
        assert client.state.layer_num == 120
        assert client.state.total_layers == 350
        assert client.state.subtask_name == "benchy.gcode"
        assert client.state.gcode_file == "/Metadata/plate_1.gcode"
        assert client.state.speed_level == 2
        assert client.state.wifi_signal == -52
        assert client.state.wired_network is False

    def test_temperature_parsing_baseline(self, client):
        """Verify nozzle, bed, and chamber temperatures are parsed accurately."""
        client._process_message(SAMPLE_BAMBU_PUSH_ALL_PAYLOAD)

        temps = client.state.temperatures
        assert temps["nozzle"] == 220.5
        assert temps["nozzle_target"] == 220.0
        assert temps["bed"] == 60.1
        assert temps["bed_target"] == 60.0
        assert temps["chamber"] == 38.0

    def test_fan_speed_parsing_baseline(self, client):
        """Verify fan speeds (0-15 scale or percent) are converted properly."""
        client._process_message(SAMPLE_BAMBU_PUSH_ALL_PAYLOAD)

        # cooling_fan_speed '12' -> 12 / 15 * 100 = 80%
        assert client.state.cooling_fan_speed == 80
        # heatbreak_fan_speed '15' -> 100%
        assert client.state.heatbreak_fan_speed == 100
        # big_fan1 (aux) '8' -> 8 / 15 * 100 = 53%
        assert client.state.big_fan1_speed == 53
        # big_fan2 (chamber) '4' -> 4 / 15 * 100 = 27%
        assert client.state.big_fan2_speed == 27

    def test_ams_and_trays_parsing_baseline(self, client):
        """Verify AMS units, multi-tray structures, colors, and RFID info."""
        client._process_message(SAMPLE_BAMBU_PUSH_ALL_PAYLOAD)

        raw_ams = client.state.raw_data.get("ams")
        assert raw_ams is not None
        assert isinstance(raw_ams, list)
        assert len(raw_ams) == 1

        ams0 = raw_ams[0]
        assert ams0["id"] == "0"
        assert ams0["humidity_raw"] == "24"
        assert len(ams0["tray"]) == 2

        tray0 = ams0["tray"][0]
        assert tray0["id"] == "0"
        assert tray0["tray_color"] == "FF0000FF"
        assert tray0["tray_type"] == "PLA"
        assert tray0["remain"] == 85
        assert tray0["k"] == 0.022
        assert tray0["tag_uid"] == "0102030405060708"

        tray1 = ams0["tray"][1]
        assert tray1["id"] == "1"
        assert tray1["tray_color"] == "00FF00FF"
        assert tray1["tray_type"] == "PETG"
        assert tray1["remain"] == 40

    def test_vt_tray_external_spool_parsing_baseline(self, client):
        """Verify external spool (vt_tray) is parsed and captured in raw_data."""
        client._process_message(SAMPLE_BAMBU_PUSH_ALL_PAYLOAD)

        raw_vt = client.state.raw_data.get("vt_tray")
        assert raw_vt is not None
        # Normalizes to list or dict in raw_data
        vt_dict = raw_vt[0] if isinstance(raw_vt, list) else raw_vt
        assert vt_dict["id"] == "254"
        assert vt_dict["tray_type"] == "TPU"
        assert vt_dict["remain"] == 90

    def test_timelapse_tracking_during_running(self, client):
        """Verify timelapse flag is recorded during RUNNING state."""
        client.state.state = "RUNNING"
        client._was_running = True
        client._parse_xcam_data({"timelapse": "enable"})
        assert client.state.timelapse is True
        assert client._timelapse_during_print is True

    def test_chamber_light_state_baseline(self, client):
        """Verify chamber light state is tracked from lights_report."""
        client._process_message(SAMPLE_BAMBU_PUSH_ALL_PAYLOAD)
        assert client.state.chamber_light is True

        # Turn off
        off_payload = {"print": {"lights_report": [{"node": "chamber_light", "mode": "off"}]}}
        client._process_message(off_payload)
        assert client.state.chamber_light is False


# ============================================================================
# Command Dispatch Baseline Tests
# ============================================================================

class TestBambuCommandsBaseline:
    """Characterizes the wire commands dispatched to Bambu printers."""

    @pytest.fixture
    def client(self):
        client = BambuMQTTClient(
            ip_address="192.168.1.114",
            serial_number="00M09A123456789",
            access_code="12345678",
            model="X1C",
        )
        client._client = MagicMock()
        client.state.connected = True
        client.state.state = "IDLE"
        return client

    def test_start_print_wire_payload_baseline(self, client):
        """Verify start_print sends valid project_file command structure."""
        success = client.start_print(
            filename="test_model.gcode",
            plate_id=1,
            ams_mapping=[0, 1, -1, -1],
            bed_levelling="auto",
            flow_cali="auto",
            vibration_cali=True,
            layer_inspect=False,
            timelapse=True,
            use_ams=True,
            nozzle_offset_cali="auto",
            bed_type="textured_plate",
        )
        assert success is True

        # Inspect published payload
        client._client.publish.assert_called_once()
        publish_args = client._client.publish.call_args
        topic = publish_args[0][0]
        payload = json.loads(publish_args[0][1])

        assert topic == client.topic_publish
        assert "print" in payload
        cmd_dict = payload["print"]
        assert cmd_dict["command"] == "project_file"
        assert cmd_dict["param"] == "Metadata/plate_1.gcode"
        assert cmd_dict["subtask_name"] == "test_model"
        assert cmd_dict["timelapse"] is True
        assert cmd_dict["use_ams"] is True
        assert cmd_dict["bed_type"] == "textured_plate"
        assert cmd_dict["ams_mapping"] == [0, 1, -1, -1]

    def test_pause_resume_stop_commands_baseline(self, client):
        """Verify pause, resume, and stop print command formats."""
        # Pause
        client.pause_print()
        pause_payload = json.loads(client._client.publish.call_args[0][1])
        assert pause_payload["print"]["command"] == "pause"

        # Resume
        client.resume_print()
        resume_payload = json.loads(client._client.publish.call_args[0][1])
        assert resume_payload["print"]["command"] == "resume"

        # Stop
        client.stop_print()
        stop_payload = json.loads(client._client.publish.call_args[0][1])
        assert stop_payload["print"]["command"] == "stop"

    def test_temperature_commands_baseline(self, client):
        """Verify G-code commands for nozzle, bed, and chamber temperatures."""
        # Bed
        client.set_bed_temperature(65)
        bed_cmd = json.loads(client._client.publish.call_args[0][1])
        assert bed_cmd["print"]["command"] == "gcode_line"
        assert bed_cmd["print"]["param"] == "M140 S65"

        # Nozzle 0 (right/primary)
        client.set_nozzle_temperature(215, nozzle=0)
        noz_cmd = json.loads(client._client.publish.call_args[0][1])
        assert noz_cmd["print"]["param"] == "M104 T0 S215"

        # Chamber
        client.set_chamber_temperature(45)
        chamb_cmd = json.loads(client._client.publish.call_args[0][1])
        assert chamb_cmd["print"]["param"] == "M141 S45"

    def test_fan_commands_baseline(self, client):
        """Verify fan speed commands (P1=part, P2=aux, P3=chamber)."""
        client.set_part_fan(255)
        p1 = json.loads(client._client.publish.call_args[0][1])
        assert p1["print"]["param"] == "M106 P1 S255"

        client.set_aux_fan(128)
        p2 = json.loads(client._client.publish.call_args[0][1])
        assert p2["print"]["param"] == "M106 P2 S128"

        client.set_chamber_fan(64)
        p3 = json.loads(client._client.publish.call_args[0][1])
        assert p3["print"]["param"] == "M106 P3 S64"

    def test_motion_commands_baseline(self, client):
        """Verify homing, axis jogging, motor disable/enable."""
        # Home
        client.home_axes("XYZ")
        home = json.loads(client._client.publish.call_args[0][1])
        assert home["print"]["param"] == "G28"

        # Move X +10mm
        client.move_axis("X", 10.0, speed=3000)
        move = json.loads(client._client.publish.call_args[0][1])
        assert "G91\nG0 X10.00 F3000\nG90" == move["print"]["param"]

        # Disable motors
        client.disable_motors()
        m18 = json.loads(client._client.publish.call_args[0][1])
        assert m18["print"]["param"] == "M18"

        # Enable motors
        client.enable_motors()
        m17 = json.loads(client._client.publish.call_args[0][1])
        assert m17["print"]["param"] == "M17"

    def test_chamber_light_command_baseline(self, client):
        """Verify chamber light command toggles ledctrl node."""
        client.set_chamber_light(True)
        # Publishes for chamber_light and chamber_light2
        calls = client._client.publish.call_args_list[-2:]
        light1 = json.loads(calls[0][0][1])
        assert light1["system"]["command"] == "ledctrl"
        assert light1["system"]["led_node"] == "chamber_light"
        assert light1["system"]["led_mode"] == "on"

        client.set_chamber_light(False)
        calls = client._client.publish.call_args_list[-2:]
        light_off = json.loads(calls[0][0][1])
        assert light_off["system"]["led_mode"] == "off"

    def test_print_speed_command_baseline(self, client):
        """Verify setting print speed modes (1..4)."""
        client.set_print_speed(3)  # Sport
        payload = json.loads(client._client.publish.call_args[0][1])
        assert payload["print"]["command"] == "print_speed"
        assert payload["print"]["param"] == "3"

    def test_ams_load_unload_filament_baseline(self, client):
        """Verify ams_change_filament and ams_unload commands."""
        # Load slot 2 (AMS 0 slot 2)
        client.ams_load_filament(tray_id=2)
        load_cmd = json.loads(client._client.publish.call_args[0][1])
        assert load_cmd["print"]["command"] == "ams_change_filament"
        assert load_cmd["print"]["ams_id"] == 0
        assert load_cmd["print"]["slot_id"] == 2
        assert load_cmd["print"]["target"] == 2

        # Unload
        client.state.tray_now = 2
        client.ams_unload_filament()
        unload_cmd = json.loads(client._client.publish.call_args[0][1])
        assert unload_cmd["print"]["command"] == "ams_change_filament"
        assert unload_cmd["print"]["target"] == 255

    def test_skip_objects_command_baseline(self, client):
        """Verify skip_objects command sends valid list while printing."""
        client.state.state = "RUNNING"
        client.skip_objects([1, 2, 5])
        skip_payload = json.loads(client._client.publish.call_args[0][1])
        assert skip_payload["print"]["command"] == "skip_objects"
        assert skip_payload["print"]["obj_list"] == [1, 2, 5]
        assert client.state.skipped_objects == [1, 2, 5]


# ============================================================================
# PrinterManager & printer_state_to_dict Baseline Tests
# ============================================================================

class TestPrinterManagerBambuContractBaseline:
    """Characterizes PrinterManager contracts and state serialization."""

    @pytest.fixture
    def manager(self):
        return PrinterManager()

    @pytest.fixture
    def populated_state(self):
        state = PrinterState()
        state.connected = True
        state.state = "RUNNING"
        state.current_print = "test.gcode"
        state.subtask_name = "test.gcode"
        state.gcode_file = "/Metadata/plate_1.gcode"
        state.progress = 50
        state.remaining_time = 45
        state.layer_num = 60
        state.total_layers = 120
        state.temperatures = {
            "nozzle": 220.0,
            "nozzle_target": 220.0,
            "bed": 60.0,
            "bed_target": 60.0,
            "chamber": 35.0,
            "chamber_target": 40.0,
            "chamber_heating": True,
        }
        state.raw_data = SAMPLE_BAMBU_PUSH_ALL_PAYLOAD["print"]
        state.speed_level = 2
        state.cooling_fan_speed = 80
        state.heatbreak_fan_speed = 100
        state.big_fan1_speed = 50
        state.big_fan2_speed = 30
        state.chamber_light = True
        state.wifi_signal = "-60dBm"
        state.firmware_version = "01.07.02.00"
        return state

    def test_printer_state_to_dict_full_contract(self, populated_state):
        """Verify all keys expected by frontend/mobile clients are present."""
        d = printer_state_to_dict(
            state=populated_state,
            printer_id=1,
            model="X1C",
        )

        # Basic identity & connection
        assert d["connected"] is True
        assert d["state"] == "RUNNING"
        assert d["current_print"] == "test.gcode"
        assert d["subtask_name"] == "test.gcode"
        assert d["gcode_file"] == "/Metadata/plate_1.gcode"
        assert d["current_plate_id"] == 1

        # Metrics
        assert d["progress"] == 50
        assert d["remaining_time"] == 45
        assert d["layer_num"] == 60
        assert d["total_layers"] == 120
        assert d["speed_level"] == 2
        assert d["wifi_signal"] == "-60dBm"

        # Temperatures (X1C preserves chamber temp)
        assert d["temperatures"]["nozzle"] == 220.0
        assert d["temperatures"]["bed"] == 60.0
        assert d["temperatures"]["chamber"] == 35.0

        # Fans & lights
        assert d["cooling_fan_speed"] == 80
        assert d["heatbreak_fan_speed"] == 100
        assert d["big_fan1_speed"] == 50
        assert d["big_fan2_speed"] == 30
        assert d["chamber_light"] is True

        # AMS
        assert d["ams"] is not None
        assert len(d["ams"]) == 1
        assert len(d["ams"][0]["tray"]) == 2
        assert d["ams"][0]["tray"][0]["tray_color"] == "FF0000FF"

        # Cover URL
        assert d["cover_url"] == "/api/v1/printers/1/cover"

    def test_model_specific_chamber_temp_filtering(self, populated_state):
        """Verify chamber temp is stripped for models lacking chamber sensor."""
        # P1S lacks chamber temp sensor
        p1s_dict = printer_state_to_dict(state=populated_state, printer_id=1, model="P1S")
        assert "chamber" not in p1s_dict["temperatures"]
        assert "chamber_target" not in p1s_dict["temperatures"]

        # A1 lacks chamber temp sensor
        a1_dict = printer_state_to_dict(state=populated_state, printer_id=1, model="A1")
        assert "chamber" not in a1_dict["temperatures"]

        # X1C has chamber sensor
        x1c_dict = printer_state_to_dict(state=populated_state, printer_id=1, model="X1C")
        assert "chamber" in x1c_dict["temperatures"]
        assert x1c_dict["temperatures"]["chamber"] == 35.0

    def test_model_drying_capabilities_baseline(self):
        """Verify model drying compatibility checks."""
        # A1 / A1 Mini: unsupported
        assert supports_drying("A1", "01.03.00.00") is False
        assert supports_drying("A1MINI", "01.03.00.00") is False

        # P1P / P1S: screen-only drying
        assert drying_screen_only("P1P") is True
        assert drying_screen_only("P1S") is True
        # X1C: requires >= 01.09.00.00
        assert supports_drying("X1C", "01.08.02.00") is False
        assert supports_drying("X1C", "01.09.00.00") is True
        assert supports_drying("X1C", "01.10.00.00") is True

    def test_stg_cur_idle_bug_detection(self):
        """Verify detection of models with known idle stg_cur=0 bug."""
        assert has_stg_cur_idle_bug("A1") is True
        assert has_stg_cur_idle_bug("A1MINI") is True
        assert has_stg_cur_idle_bug("P1S") is True
        assert has_stg_cur_idle_bug("X1C") is False

    def test_hms_error_parsing_dict_format(self):
        """Verify HMS error dict format parsing into HMSError instances."""
        client = BambuMQTTClient(
            ip_address="192.168.1.114",
            serial_number="00M09A123456789",
            access_code="12345678",
            model="X1C",
        )
        client._client = MagicMock()
        client.state.connected = True

        hms_payload = {
            "print": {
                "hms": [
                    {
                        "attr": 0x03000800,  # module 3, severity 8 -> 8 & 0xF = 8
                        "code": 0x4001,      # >= 0x4000 valid error code
                    }
                ]
            }
        }
        client._process_message(hms_payload)
        assert len(client.state.hms_errors) == 1
        err = client.state.hms_errors[0]
        assert err.module == 3
        assert err.code == "0x4001"
        assert err.attr == 0x03000800

    def test_printer_manager_delegation_to_bambu_client(self, manager):
        """Verify PrinterManager forwards control calls to the active Bambu client."""
        mock_client = MagicMock()
        mock_client.start_print.return_value = True
        mock_client.stop_print.return_value = True
        mock_client.send_drying_command.return_value = True
        mock_client.request_status_update.return_value = True

        manager._clients[42] = mock_client

        assert manager.stop_print(42) is True
        mock_client.stop_print.assert_called_once()

        assert manager.start_print(42, "model.gcode") is True
        assert mock_client.start_print.call_count == 1

        assert manager.send_drying_command(42, 0, 50, 4) is True
        mock_client.send_drying_command.assert_called_once_with(0, 50, 4, 1, "", False)

        assert manager.request_status_update(42) is True
        mock_client.request_status_update.assert_called_once()

        # Routes use get_client to call printer-specific control actions directly
        client = manager.get_client(42)
        assert client is mock_client


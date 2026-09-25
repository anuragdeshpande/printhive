import asyncio
import logging
from collections.abc import Callable
from typing import Any
import json
import urllib.request
import urllib.parse

from backend.app.services.bambu_mqtt import PrinterState, NozzleInfo, PrintOptions

logger = logging.getLogger(__name__)

def is_flashforge_model(model: str | None) -> bool:
    """Check if the model string indicates a Flashforge printer."""
    if not model:
        return False
    m = model.upper()
    return any(x in m for x in ["FLASHFORGE", "CREATOR", "FF-C5", "ADVENTURER", "5M", "5MPRO"])


class FlashforgeClient:
    """Compatibility wrapper for Flashforge printers (Creator 5 / Adventurer series).
    Acts as a drop-in replacement for BambuMQTTClient / ElegooCentauriClient.
    Connects via HTTP REST API (port 8898) and/or TCP control commands (port 8899).
    """

    def __init__(
        self,
        ip_address: str,
        serial_number: str,
        access_code: str,
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
        on_ams_change: Callable[[list], None] | None = None,
        on_layer_change: Callable[[int], None] | None = None,
        on_bed_temp_update: Callable[[float], None] | None = None,
        on_drying_complete: Callable[[int], None] | None = None,
        on_print_running_observed: Callable[[dict], None] | None = None,
        on_finish_photo_moment: Callable[[dict], None] | None = None,
    ):
        self.ip_address = ip_address
        self.serial_number = serial_number
        self.access_code = access_code
        self.model = model or "Creator 5"

        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete
        self.on_ams_change = on_ams_change
        self.on_layer_change = on_layer_change
        self.on_bed_temp_update = on_bed_temp_update
        self.on_drying_complete = on_drying_complete
        self.on_print_running_observed = on_print_running_observed
        self.on_finish_photo_moment = on_finish_photo_moment

        self.state = PrinterState()
        # Ensure multi-toolhead nozzle objects (default 2 nozzles for Creator 5 IDEX)
        self.state.nozzles = [
            NozzleInfo(nozzle_type="hardened_steel", nozzle_diameter="0.4"),
            NozzleInfo(nozzle_type="hardened_steel", nozzle_diameter="0.4"),
        ]

        self._loop: asyncio.AbstractEventLoop | None = None
        self._poll_task: asyncio.Task | None = None
        self._was_running = False
        self._completion_triggered = False
        self._last_layer_num = 0
        self._last_bed_temp = 0.0
        self.port = 8898

    @property
    def connected(self) -> bool:
        return self.state.connected

    @property
    def logging_enabled(self) -> bool:
        return False

    def enable_logging(self, enabled: bool):
        pass

    def get_logs(self) -> list:
        return []

    def clear_logs(self):
        pass

    def connect(self):
        """Start async polling loop for Flashforge REST API."""
        self._loop = asyncio.get_running_loop()
        self._poll_task = self._loop.create_task(self._async_poll_loop())

    def disconnect(self, timeout=None):
        """Clean up tasks and set connected = False."""
        if self._poll_task:
            self._poll_task.cancel()
        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)

    async def _async_poll_loop(self):
        """Poll Flashforge printer status endpoint periodically."""
        while True:
            try:
                status_data = await self._loop.run_in_executor(None, self._fetch_status)
                if status_data:
                    self._update_state(status_data)
                else:
                    self.state.connected = False
                    if self.on_state_change:
                        self.on_state_change(self.state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error polling Flashforge printer at {self.ip_address}: {e}")
                self.state.connected = False
                if self.on_state_change:
                    self.on_state_change(self.state)

            await asyncio.sleep(3)

    def _fetch_status(self) -> dict | None:
        """Fetch REST status payload from Flashforge HTTP API (port 8898)."""
        url = f"http://{self.ip_address}:{self.port}/detail"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Bambuddy/1.0", "CheckCode": self.access_code or ""})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
        except Exception:
            pass
        return None

    def _send_command(self, cmd_dict: dict) -> bool:
        """Send command JSON to Flashforge HTTP API."""
        url = f"http://{self.ip_address}:{self.port}/control"
        try:
            data = json.dumps(cmd_dict).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "CheckCode": self.access_code or ""},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Flashforge command failed ({cmd_dict}): {e}")
            return False

    def _update_state(self, data: dict):
        """Map Flashforge status payload into Bambuddy PrinterState."""
        self.state.connected = True
        self.state.raw_data = data

        # Temperatures (Multi-toolhead support for Tool 0 and Tool 1)
        # Flashforge payload formats: nozzle_temp_0, nozzle_temp_1, bed_temp, chamber_temp
        noz0 = float(data.get("nozzle_temp_0", data.get("extruder_temp", 0.0)))
        noz0_t = float(data.get("nozzle_target_0", data.get("extruder_target", 0.0)))
        noz1 = float(data.get("nozzle_temp_1", 0.0))
        noz1_t = float(data.get("nozzle_target_1", 0.0))
        bed = float(data.get("bed_temp", 0.0))
        bed_t = float(data.get("bed_target", 0.0))
        chamber = float(data.get("chamber_temp", 0.0))
        chamber_t = float(data.get("chamber_target", 0.0))

        self.state.temperatures = {
            "nozzle": noz0,
            "nozzle_target": noz0_t,
            "nozzle_0": noz0,
            "nozzle_target_0": noz0_t,
            "nozzle_1": noz1,
            "nozzle_target_1": noz1_t,
            "bed": bed,
            "bed_target": bed_t,
            "chamber": chamber,
            "chamber_target": chamber_t,
        }

        # Active extruder (0 = Tool 0, 1 = Tool 1)
        self.state.active_extruder = int(data.get("active_extruder", data.get("tool_head", 0)))

        # Toolhead / Extruder filament colors & types
        # Tool 0 & Tool 1 filament info mapping
        t0_type = data.get("tool0_material", "PLA")
        t0_color = data.get("tool0_color", "808080").lstrip("#")
        t1_type = data.get("tool1_material", "PLA")
        t1_color = data.get("tool1_color", "00FF00").lstrip("#")

        # Map to AMS units for UI compatibility (Tool 0 = Tray 0, Tool 1 = Tray 1)
        self.state.ams = [{
            "id": 0,
            "tray": [
                {
                    "id": 0,
                    "tray_type": t0_type,
                    "tray_color": f"{t0_color}FF" if len(t0_color) == 6 else t0_color,
                    "tray_sub_brands": f"Tool 0 ({t0_type})",
                    "state": 11,
                    "remain": 100,
                },
                {
                    "id": 1,
                    "tray_type": t1_type,
                    "tray_color": f"{t1_color}FF" if len(t1_color) == 6 else t1_color,
                    "tray_sub_brands": f"Tool 1 ({t1_type})",
                    "state": 11,
                    "remain": 100,
                }
            ],
            "serial_number": "FLASHFORGE_IDEX",
            "sw_ver": "1.0.0",
        }]
        self.state.ams_exists = True

        # Fans & Lights
        self.state.cooling_fan_speed = int(data.get("cooling_fan", 0))
        self.state.big_fan1_speed = int(data.get("aux_fan", 0))
        self.state.chamber_light = bool(data.get("chamber_light", False))

        # Job Metadata
        job_status = str(data.get("machine_status", data.get("status", "IDLE"))).upper()
        filename = data.get("print_filename", data.get("current_file", ""))
        if filename:
            self.state.current_print = filename
            self.state.subtask_name = filename
            self.state.gcode_file = filename

        self.state.progress = float(data.get("progress", 0.0))
        self.state.layer_num = int(data.get("current_layer", 0))
        self.state.total_layers = int(data.get("total_layers", 0))
        self.state.remaining_time = int(data.get("remaining_time_min", 0))

        # Map Flashforge state strings & stage codes to Bambu PrinterState schema
        # State mapping: READY/BUILDING/PREPARE/PAUSED/COMPLETED/ABORTED
        state_str = "IDLE"
        stg_cur = -1

        if "BUILDING" in job_status or "PRINTING" in job_status:
            state_str = "RUNNING"
            stg_cur = 0
        elif "PAUSE" in job_status:
            state_str = "PAUSE"
            stg_cur = 16
        elif "COMPLETE" in job_status or "FINISHED" in job_status:
            state_str = "FINISH"
        elif "ABORT" in job_status or "CANCEL" in job_status or "ERROR" in job_status:
            state_str = "FAILED"
        elif "PREPARE" in job_status or "HEATING" in job_status or "CALIBRAT" in job_status:
            state_str = "PREPARE"
            # Map Flashforge sub-stages
            if "HEATING" in job_status:
                stg_cur = 2  # Heatbed preheating
            elif "LEVELING" in job_status:
                stg_cur = 1  # Auto bed leveling
            elif "HOMING" in job_status:
                stg_cur = 13  # Homing toolhead
            else:
                stg_cur = 74  # Preparing

        self.state.state = state_str
        self.state.stg_cur = stg_cur

        # Callbacks
        if state_str in ("RUNNING", "PREPARE") and not self._was_running:
            self._was_running = True
            if self.on_print_start:
                self.on_print_start({
                    "filename": self.state.current_print,
                    "subtask_name": self.state.subtask_name,
                    "remaining_time": self.state.remaining_time,
                })

        if state_str == "FINISH" and not self._completion_triggered:
            self._completion_triggered = True
            if self.on_print_complete:
                self.on_print_complete({
                    "filename": self.state.current_print,
                    "subtask_name": self.state.subtask_name or self.state.current_print,
                    "status": "completed",
                })


        if state_str in ("IDLE", "FINISH", "FAILED"):
            self._was_running = False
            self._completion_triggered = False
            self.state.current_print = ""
            self.state.subtask_name = ""
            self.state.gcode_file = ""

        if self.state.layer_num != self._last_layer_num:
            self._last_layer_num = self.state.layer_num
            if self.on_layer_change:
                self.on_layer_change(self.state.layer_num)

        if abs(bed - self._last_bed_temp) > 0.1:
            self._last_bed_temp = bed
            if self.on_bed_temp_update:
                self.on_bed_temp_update(bed)

        if self.on_state_change:
            self.on_state_change(self.state)

    def start_print(self, filename: str, **kwargs) -> bool:
        """Start printing a file on the Flashforge printer."""
        self.state.current_print = filename
        self.state.subtask_name = filename
        self.state.gcode_file = filename
        self.state.state = "PREPARE"
        self.state.stg_cur = 74
        return self._send_command({"cmd": "start_print", "file": filename})

    def stop_print(self, *args, **kwargs) -> bool:
        """Cancel the active print."""
        return self._send_command({"cmd": "cancel_print"})

    def pause_print(self, *args, **kwargs) -> bool:
        """Pause the active print."""
        return self._send_command({"cmd": "pause_print"})

    def resume_print(self, *args, **kwargs) -> bool:
        """Resume the paused print."""
        return self._send_command({"cmd": "resume_print"})

    def set_nozzle_temperature(self, temp: int, nozzle_index: int = 0, *args, **kwargs) -> bool:
        """Set nozzle target temperature for Tool 0 or Tool 1."""
        return self._send_command({"cmd": "set_temperature", "nozzle_index": nozzle_index, "target": temp})

    def set_bed_temperature(self, temp: int, *args, **kwargs) -> bool:
        """Set heatbed target temperature."""
        return self._send_command({"cmd": "set_temperature", "type": "bed", "target": temp})

    def set_fan_speed(self, fan_id: int, speed_pwm: int, *args, **kwargs) -> bool:
        """Set fan speed (pwm 0-255)."""
        pct = max(0, min(100, int((speed_pwm / 255.0) * 100)))
        return self._send_command({"cmd": "set_fan", "fan_id": fan_id, "speed": pct})

    def set_chamber_light(self, on: bool, *args, **kwargs) -> bool:
        """Toggle chamber enclosure light."""
        return self._send_command({"cmd": "set_light", "on": on})

    def select_toolhead(self, tool_index: int, *args, **kwargs) -> bool:
        """Select active toolhead (0 = Tool 0 / Left, 1 = Tool 1 / Right)."""
        return self._send_command({"cmd": "select_tool", "tool": tool_index})

    def check_staleness(self, *args, **kwargs) -> bool:
        return self.state.connected

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pycentauri import connect_auto, Printer
from pycentauri.discovery import discover as elegoo_discover
from pycentauri.models import Status, Attributes, CanvasStatus

from backend.app.services.bambu_mqtt import PrinterState, NozzleInfo, PrintOptions

logger = logging.getLogger(__name__)

def is_elegoo_model(model: str | None) -> bool:
    """Check if the model string indicates an Elegoo printer."""
    if not model:
        return False
    m = model.upper()
    return any(x in m for x in ["CENTAURI", "CC1", "CC2", "ELEGOO"])


# Mapping of slicer bed/plate type names to Elegoo SDCP PrintPlatformType integers:
# 0 = Textured PEI / Default
# 1 = Smooth PEI / High Temp Plate / Cool Plate (OrcaSlicer uses 1 for Cool Plate on Elegoo CC1)
# 2 = Engineering Plate
# 4 = SuperTack / Epoxy Plate
ELEGOO_PLATFORM_TYPES: dict[str, int] = {
    # 0: Textured PEI / Default
    "textured pei plate": 0,
    "textured pei": 0,
    "textured_plate": 0,
    "pei plate": 0,
    "pei": 0,
    "default": 0,
    "auto": 0,
    # 1: Smooth PEI / High Temp Plate / Cool Plate
    "high temp plate": 1,
    "smooth pei plate": 1,
    "smooth pei": 1,
    "hot_plate": 1,
    "smooth_plate": 1,
    "cool plate": 1,
    "cool_plate": 1,
    "cool": 1,
    "pc plate": 1,
    "pc_plate": 1,
    # 2: Engineering Plate
    "engineering plate": 2,
    "eng_plate": 2,
    "engineering": 2,
    # 4: SuperTack / Epoxy Plate
    "supertack plate": 4,
    "cool plate (supertack)": 4,
    "bambu cool plate supertack": 4,
    "epoxy_plate": 4,
    "supertack": 4,
}


def map_bed_type_to_elegoo_platform_type(bed_type: str | int | None) -> int:
    """Map a slicer bed-type string or numeric code to the Elegoo PrintPlatformType integer."""
    if bed_type is None:
        return 0
    if isinstance(bed_type, int):
        return bed_type
    try:
        return int(bed_type)
    except (ValueError, TypeError):
        pass
    norm = str(bed_type).strip().lower()
    return ELEGOO_PLATFORM_TYPES.get(norm, 0)


class ElegooCentauriClient:
    """Compatibility wrapper that acts as a drop-in replacement for BambuMQTTClient,
    routing operations to an Elegoo printer via the pycentauri SDK.
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
        self.model = model
        
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
        self._printer: Printer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        
        self._was_running = False
        self._completion_triggered = False
        self._last_known_job_name = ""
        self._last_layer_num = 0
        self._last_bed_temp = 0.0
        self._drying_targets = {}
        self._last_message_time: float | None = time.time()
        self.last_connect_error: str | None = None

    @property
    def connected(self) -> bool:
        return self.state.connected

    def force_reconnect_stale_session(self, reason: str = ""):
        """Force reconnection when connection watchdog detects a stalled session."""
        logger.info("Forcing reconnect of Elegoo client (%s)", reason)
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
        if self._loop and self._loop.is_running():
            self._connect_task = self._loop.create_task(self._async_connect())

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
        """Start async connection task on the running loop."""
        self._loop = asyncio.get_running_loop()
        self._connect_task = self._loop.create_task(self._async_connect())

    def disconnect(self, timeout=None):
        """Disconnect and clean up tasks."""
        if self._connect_task:
            self._connect_task.cancel()
        if self._printer and self._loop:
            self._loop.create_task(self._printer.close())
        self.state.connected = False
        if self.on_state_change:
            self.on_state_change(self.state)

    def pause_telemetry(self):
        """Pause active telemetry polling (e.g. during file uploads)."""
        if self._printer:
            self._printer.pause_watch()

    def resume_telemetry(self):
        """Resume active telemetry polling after upload finishes."""
        if self._printer:
            self._printer.resume_watch()

    async def _async_connect(self):
        # Discover the mainboard_id so we can pass it to connect_auto.
        # The CC1 printer only pushes its mainboard ID when idle/active — when
        # paused or errored it is silent and any call that needs the ID (e.g.
        # attributes(), watch() internally) will time out waiting for it.
        # Passing mainboard_id= explicitly bypasses that wait entirely.
        _cached_mainboard_id: str | None = None

        while True:
            if not _cached_mainboard_id:
                try:
                    # Targeted discovery works from Docker/LXC networks where
                    # LAN broadcast packets are not forwarded.
                    discovered = await elegoo_discover(
                        timeout=2.0,
                        broadcast_address=self.ip_address,
                    )
                    for d in discovered:
                        if d.host == self.ip_address and d.mainboard_id:
                            _cached_mainboard_id = d.mainboard_id
                            logger.info("Discovered Elegoo mainboard_id=%s for %s", _cached_mainboard_id, self.ip_address)
                            break
                except Exception as e:
                    logger.debug("Elegoo discovery attempt failed for %s: %s", self.ip_address, e)

            try:
                logger.info("Connecting to Elegoo printer at %s (mainboard_id=%s)", self.ip_address, _cached_mainboard_id)
                self._printer = await connect_auto(
                    self.ip_address,
                    access_code=self.access_code,
                    connect_timeout=5.0,
                    enable_control=True,
                    mainboard_id=_cached_mainboard_id,
                )
                if self._printer.mainboard_id:
                    _cached_mainboard_id = self._printer.mainboard_id
                self.state.connected = True

                if self.on_state_change:
                    self.on_state_change(self.state)

                # Iterate watch loop to receive telemetry pushes
                async for status in self._printer.watch():
                    if self._printer.mainboard_id:
                        _cached_mainboard_id = self._printer.mainboard_id
                    self._update_state(status)

            except asyncio.CancelledError:
                logger.info("Connection task cancelled for Elegoo printer at %s", self.ip_address)
                break
            except Exception as e:
                logger.warning(
                    "Connection lost or failed to connect to Elegoo printer at %s: %s. Retrying in 5s...",
                    self.ip_address,
                    e,
                )
                self.state.connected = False
                if self.on_state_change:
                    self.on_state_change(self.state)

                if self._printer:
                    try:
                        await self._printer.close()
                    except Exception:
                        pass
                    self._printer = None

                await asyncio.sleep(5.0)
                continue

            if self._printer:
                try:
                    await self._printer.close()
                except Exception:
                    pass
                self._printer = None

            if self.state.connected:
                self.state.connected = False
                if self.on_state_change:
                    self.on_state_change(self.state)
            await asyncio.sleep(5.0)



    def _update_state(self, status: Status):
        self._last_message_time = time.time()
        self.state.connected = True
        self.state.temperatures = {
            "nozzle": status.temp_nozzle or 0.0,
            "nozzle_target": status.temp_nozzle_target or 0.0,
            "bed": status.temp_bed or 0.0,
            "bed_target": status.temp_bed_target or 0.0,
            "chamber": status.temp_chamber or 0.0,
            "chamber_target": status.temp_chamber_target or 0.0,
        }

        # Map fan speeds
        self.state.cooling_fan_speed = status.fan_speed.get("ModelFan", 0)
        self.state.big_fan1_speed = status.fan_speed.get("AuxiliaryFan", 0)
        self.state.big_fan2_speed = status.fan_speed.get("BoxFan", 0)

        # Map light status (SecondLight represents the enclosure LED on CC2 FDM)
        self.state.chamber_light = bool(status.light.get("SecondLight", 0))

        if status.print_info:
            if status.print_info.filename:
                self.state.current_print = status.print_info.filename
                self.state.subtask_name = status.print_info.filename
                self.state.gcode_file = status.print_info.filename
                self._last_known_job_name = status.print_info.filename
            self.state.progress = float(status.print_info.progress or 0)

            self.state.layer_num = status.print_info.current_layer or 0
            self.state.total_layers = status.print_info.total_layer or 0
            
            # Map remaining time from ticks if available (ticks are in seconds).
            # We divide by 60 to store in minutes to match expected schema.
            cur_ticks = status.print_info.current_ticks or 0.0
            tot_ticks = status.print_info.total_ticks or 0.0
            if tot_ticks > cur_ticks:
                self.state.remaining_time = max(1 if self.state.progress < 100 else 0, int(round((tot_ticks - cur_ticks) / 60.0)))
            elif cur_ticks > 0 and 0 < self.state.progress < 100:
                est_total = cur_ticks / (self.state.progress / 100.0)
                self.state.remaining_time = max(1, int(round((est_total - cur_ticks) / 60.0)))
            else:
                self.state.remaining_time = 0

            # Map speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
            pct = status.print_info.print_speed
            if pct == 50:
                self.state.speed_level = 1
            elif pct == 100:
                self.state.speed_level = 2
            elif pct == 130:
                self.state.speed_level = 3
            elif pct == 160:
                self.state.speed_level = 4
            else:
                self.state.speed_level = 2

            # Map print status code to Bambu-compatible string and stage
            print_status_code = status.print_status
            state_str = "IDLE"
            stg_cur = -1

            if print_status_code == 0:
                state_str = "IDLE"
            elif print_status_code in (5, 6):
                state_str = "PAUSE"
                stg_cur = 16
            elif print_status_code in (7, 8):
                state_str = "IDLE"
            elif print_status_code == 9:
                state_str = "FINISH"
            elif print_status_code == 14:
                state_str = "FAILED"
            elif print_status_code in (1, 12, 13, 27, 28, 29):
                state_str = "RUNNING"
                stg_cur = 0
            elif print_status_code is not None and print_status_code != 0:
                state_str = "PREPARE"
                # Map Elegoo preparation sub-states to Bambu stage codes for granular UI stage names
                ELEGOO_PREP_STAGES = {
                    10: 52,  # File Checking / Checking Material
                    11: 44,  # Printer Checking / Auto Check: Platform
                    15: 1,   # Auto Bed Leveling
                    16: 2,   # Heatbed / Nozzle Preheating
                    17: 3,   # Resonance Testing / Vibration Compensation
                    18: 74,  # Starting Print / Preparing
                }
                stg_cur = ELEGOO_PREP_STAGES.get(print_status_code, 74)

            self.state.state = state_str
            self.state.stg_cur = stg_cur

            if state_str == "FINISH" and not self.state.subtask_name:
                if self._last_known_job_name:
                    self.state.subtask_name = self._last_known_job_name
                    self.state.current_print = self._last_known_job_name
                    self.state.gcode_file = self._last_known_job_name
                else:
                    asyncio.create_task(self._recover_finished_job_name())

            # Trigger state callbacks
            if state_str in ("RUNNING", "PREPARE") and not self._was_running:
                self._was_running = True
                self._completion_triggered = False
                if self.on_print_start:
                    self.on_print_start({
                        "filename": self.state.current_print,
                        "subtask_name": self.state.subtask_name,
                        "remaining_time": self.state.remaining_time,
                    })

            if state_str == "FINISH" and not self._completion_triggered:
                self._completion_triggered = True
                self._was_running = False
                if self.on_print_complete:
                    self.on_print_complete({
                        "filename": self.state.current_print,
                        "subtask_name": self.state.subtask_name or self.state.current_print,
                        "status": "completed",
                    })

            if state_str in ("IDLE", "FAILED"):
                self._was_running = False
                self._completion_triggered = False
                self.state.current_print = ""
                self.state.subtask_name = ""
                self.state.gcode_file = ""
            elif state_str == "FINISH":
                self._was_running = False
                # Do NOT clear current_print or subtask_name in FINISH state (waiting for plate clear)
                # and do NOT reset _completion_triggered to false to avoid repeated callbacks.



            if self.state.layer_num != self._last_layer_num:
                self._last_layer_num = self.state.layer_num
                if self.on_layer_change:
                    self.on_layer_change(self.state.layer_num)

        bed_temp = self.state.temperatures.get("bed", 0.0)
        if abs(bed_temp - self._last_bed_temp) > 0.1:
            self._last_bed_temp = bed_temp
            if self.on_bed_temp_update:
                self.on_bed_temp_update(bed_temp)

        # Trigger AMS/Canvas state updates
        if hasattr(self._printer, "canvas_status") and self._loop:
            self._loop.create_task(self._update_canvas_status())

        if self.on_state_change:
            self.on_state_change(self.state)

    async def _recover_finished_job_name(self):
        if not self._printer or not hasattr(self._printer, "print_history"):
            return
        try:
            hist = await self._printer.print_history()
            tasks = (hist or {}).get("history_task_list") or []
            if tasks:
                latest = tasks[-1]
                t_name = latest.get("task_name")
                if t_name and not self.state.subtask_name:
                    self.state.subtask_name = t_name
                    self.state.current_print = t_name
                    self.state.gcode_file = t_name
                    self._last_known_job_name = t_name
                    logger.info("Recovered finished job name from Elegoo print history: %s", t_name)
                    if self.on_state_change:
                        self.on_state_change(self.state)
        except Exception as e:
            logger.debug("Failed to recover finished job name from Elegoo history: %s", e)

    async def _update_canvas_status(self):
        try:
            canvas = await self._printer.canvas_status()
            ams_units = []
            for unit in canvas.canvas_list:
                trays = []
                for tray in unit.tray_list:
                    color = tray.filament_color.lstrip("#") if tray.filament_color else ""
                    trays.append({
                        "id": tray.tray_id,
                        "tray_type": tray.filament_type or "PLA",
                        "tray_color": color,
                        "tray_sub_brands": tray.filament_name or "",
                        "state": 11 if tray.status == 1 else 9,
                        "nozzle_temp_min": tray.min_nozzle_temp,
                        "nozzle_temp_max": tray.max_nozzle_temp,
                    })
                ams_units.append({
                    "id": unit.canvas_id,
                    "tray": trays,
                    "serial_number": f"CANVAS{unit.canvas_id}",
                    "sw_ver": "1.0.0",
                })
            self.state.ams = ams_units
            self.state.ams_exists = len(ams_units) > 0
            if self.on_ams_change:
                self.on_ams_change(self.state.ams)
        except Exception:
            pass

    def _run_async(self, coro, action_name: str = "action") -> bool:
        if self._loop and self._loop.is_running():
            task = self._loop.create_task(coro)
            def _handle_done(t: asyncio.Task):
                if not t.cancelled():
                    exc = t.exception()
                    if exc:
                        logger.error("Elegoo %s task failed on %s: %s", action_name, self.ip_address, exc)
                    else:
                        logger.info("Elegoo %s completed successfully on %s: %s", action_name, self.ip_address, t.result())
            task.add_done_callback(_handle_done)
            return True
        return False


    # --- Control Interface implementation ---

    def start_print(
        self,
        filename: str,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        bed_levelling: bool | str = True,
        flow_cali: bool | str = False,
        vibration_cali: bool = True,
        layer_inspect: bool = False,
        timelapse: bool = False,
        use_ams: bool = True,
        nozzle_offset_cali: bool | str = False,
        nozzle_mapping: str | None = None,
        bed_type: str | int | None = None,
    ) -> bool:
        if not self._printer:
            logger.error("ElegooCentauriClient: cannot start_print on %s — printer client not connected", self.ip_address)
            return False
        # Optimistically record filename on state so current_print is available during PREPARE
        self.state.current_print = filename
        self.state.subtask_name = filename
        self.state.gcode_file = filename
        auto_level = bed_levelling != "off" if isinstance(bed_levelling, str) else bool(bed_levelling)
        platform_type = map_bed_type_to_elegoo_platform_type(bed_type)
        logger.info(
            "ElegooCentauriClient: Sending start_print to %s: file=%s, auto_level=%s, platform_type=%s, timelapse=%s",
            self.ip_address,
            filename,
            auto_level,
            platform_type,
            timelapse,
        )
        return self._run_async(
            self._printer.start_print(
                filename,
                storage="local",
                auto_leveling=auto_level,
                timelapse=timelapse,
                platform_type=platform_type,
            ),
            action_name=f"start_print({filename})",
        )


    def stop_print(self) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.stop(), action_name="stop_print")

    def pause_print(self) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.pause(), action_name="pause_print")

    def resume_print(self) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.resume())

    def set_print_speed(self, mode: int) -> bool:
        if not self._printer:
            return False
        level_map = {1: "silent", 2: "balanced", 3: "sport", 4: "ludicrous"}
        speed_mode = level_map.get(mode, "balanced")
        return self._run_async(self._printer.set_print_speed(speed_mode))

    def set_nozzle_temperature(self, target: float, nozzle: int = 0) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_temperatures(nozzle=target))

    def set_bed_temperature(self, target: float) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_temperatures(bed=target))

    def set_chamber_temperature(self, target: float) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_temperatures(chamber=target))

    def set_fan_speed(self, fan_id: int, pwm_speed: int) -> bool:
        if not self._printer:
            return False
        speed_pct = round(pwm_speed * 100 / 255)
        if fan_id == 1:
            return self._run_async(self._printer.set_fan_speed(model=speed_pct))
        elif fan_id == 2:
            return self._run_async(self._printer.set_fan_speed(auxiliary=speed_pct))
        elif fan_id == 3:
            return self._run_async(self._printer.set_fan_speed(chamber=speed_pct))
        return False

    def home_axes(self, axes: str = "XYZ") -> bool:
        if not self._printer:
            return False
        if hasattr(self._printer, "home"):
            return self._run_async(self._printer.home(axes), action_name="home")
        if hasattr(self._printer, "_cc2_request"):
            return self._run_async(self._printer._cc2_request(1026, {}), action_name="home_cc2")
        return False

    def move_axis(self, axis: str, distance: float, speed: int = 3000) -> bool:
        if not self._printer:
            return False
        axis_clean = axis.strip().upper()
        if hasattr(self._printer, "jog_axis"):
            return self._run_async(
                self._printer.jog_axis(axis_clean, distance),
                action_name=f"jog_axis({axis_clean}, {distance})",
            )
        if hasattr(self._printer, "_cc2_request"):
            return self._run_async(
                self._printer._cc2_request(1027, {"axis": axis_clean, "step": float(distance)}),
                action_name=f"move_axis_cc2({axis_clean})",
            )
        return False

    def set_chamber_light(self, on: bool) -> bool:
        if not self._printer:
            return False
        if hasattr(self._printer, "set_light"):
            return self._run_async(self._printer.set_light(on), action_name="set_light")
        if hasattr(self._printer, "_cc2_request"):
            status = 1 if on else 0
            return self._run_async(self._printer._cc2_request(1029, {"status": status}), action_name="set_light_cc2")
        return False

    def send_gcode(self, gcode_string: str) -> bool:
        """Translate common G-code commands to native SDCP commands for Elegoo printers."""
        if not self._printer:
            return False

        import re

        success = True
        for line in gcode_string.strip().splitlines():
            line = line.split(";")[0].strip()  # Strip comments
            if not line:
                continue

            line_upper = line.upper()

            # Homing
            if "G28" in line_upper:
                axes = "XYZ"
                if "X" in line_upper and "Y" not in line_upper and "Z" not in line_upper:
                    axes = "X"
                elif "Y" in line_upper and "X" not in line_upper and "Z" not in line_upper:
                    axes = "Y"
                elif "Z" in line_upper and "X" not in line_upper and "Y" not in line_upper:
                    axes = "Z"
                success = self.home_axes(axes) and success

            # Jog / Movement
            elif line_upper.startswith("G0 ") or line_upper.startswith("G1 "):
                for axis in ("X", "Y", "Z"):
                    match = re.search(rf"{axis}\s*(-?\d+(\.\d+)?)", line_upper)
                    if match:
                        dist = float(match.group(1))
                        success = self.move_axis(axis, dist) and success

            # Nozzle Temperature: M104 / M109 S<temp>
            elif line_upper.startswith("M104") or line_upper.startswith("M109"):
                match = re.search(r"S\s*(\d+(\.\d+)?)", line_upper)
                if match:
                    temp = float(match.group(1))
                    success = self.set_nozzle_temperature(temp) and success

            # Bed Temperature: M140 / M190 S<temp>
            elif line_upper.startswith("M140") or line_upper.startswith("M190"):
                match = re.search(r"S\s*(\d+(\.\d+)?)", line_upper)
                if match:
                    temp = float(match.group(1))
                    success = self.set_bed_temperature(temp) and success

            # Fan Speed: M106 P<fan> S<speed>
            elif line_upper.startswith("M106"):
                p_match = re.search(r"P\s*(\d+)", line_upper)
                s_match = re.search(r"S\s*(\d+)", line_upper)
                fan_id = int(p_match.group(1)) if p_match else 1
                pwm = int(s_match.group(1)) if s_match else 255
                success = self.set_fan_speed(fan_id, pwm) and success

            # Fan Off: M107
            elif line_upper.startswith("M107"):
                p_match = re.search(r"P\s*(\d+)", line_upper)
                fan_id = int(p_match.group(1)) if p_match else 1
                success = self.set_fan_speed(fan_id, 0) and success

        return success

    def list_files_sync(self, storage: str = "local", path: str = "/") -> list[dict]:
        if not self._printer or not hasattr(self._printer, "list_files"):
            return []
        res = self._run_async(self._printer.list_files(storage=storage, path=path), action_name="list_files")
        if isinstance(res, dict):
            file_list = res.get("file_list") or res.get("FileList") or []
            return file_list if isinstance(file_list, list) else []
        return []

    def delete_files_sync(self, file_paths: list[str], storage: str = "local") -> bool:
        if not self._printer or not hasattr(self._printer, "delete_files"):
            return False
        return bool(self._run_async(self._printer.delete_files(file_paths, storage=storage), action_name="delete_files"))

    def get_disk_info_sync(self) -> dict:
        if not self._printer or not hasattr(self._printer, "disk_info"):
            return {}
        res = self._run_async(self._printer.disk_info(), action_name="disk_info")
        return res if isinstance(res, dict) else {}

    def request_status_update(self) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.status())

    def set_xcam_option(self, *args, **kwargs) -> bool:
        return False

    def set_ams_filament_backup(self, enabled: bool) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_auto_refill(enabled))

    def start_calibration(self, *args, **kwargs) -> bool:
        return False

    def ams_set_filament_setting(self, *args, **kwargs) -> bool:
        return False

    def extrusion_cali_sel(self, *args, **kwargs) -> bool:
        return False

    def extrusion_cali_set(self, *args, **kwargs) -> bool:
        return False

    def reset_ams_slot(self, *args, **kwargs) -> bool:
        return False

    def clear_hms_errors(self) -> bool:
        return False

    def skip_objects(self, *args, **kwargs) -> bool:
        return False

    def ams_refresh_tray(self, *args, **kwargs) -> tuple[bool, str]:
        return False, "Not supported"

    def ams_load_filament(self, *args, **kwargs) -> bool:
        return False

    def ams_unload_filament(self) -> bool:
        return False

    def execute_hms_action(self, *args, **kwargs) -> bool:
        return False

    def check_staleness(self) -> bool:
        return self.state.connected

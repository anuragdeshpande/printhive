import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pycentauri import connect_auto, Printer
from pycentauri.discovery import discover as elegoo_discover
from pycentauri.models import Status, Attributes, CanvasStatus

from backend.app.services.bambu_mqtt import PrinterState, NozzleInfo, PrintOptions
from backend.app.services.printer_state_machine import PrinterStateMachine

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

        self.state_machine = PrinterStateMachine(
            serial_number=serial_number,
            on_state_change=on_state_change,
            on_print_start=on_print_start,
            on_print_complete=on_print_complete,
            on_print_running_observed=on_print_running_observed,
            on_layer_change=on_layer_change,
            on_bed_temp_update=on_bed_temp_update,
            on_finish_photo_moment=on_finish_photo_moment,
        )
        self.state = self.state_machine.state
        self._printer: Printer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        
        self._drying_targets = {}
        self._last_message_time: float | None = time.time()
        self.last_connect_error: str | None = None
        self._cached_mainboard_id: str | None = None
        self._last_status: Status | None = None

    @property
    def last_dispatch_subtask_id(self) -> str | None:
        return self.state_machine.last_dispatch_subtask_id

    @property
    def connected(self) -> bool:
        return self.state.connected

    async def _close_printer(self, printer: Printer | None = None) -> None:
        """Close the active pycentauri Printer instance and clear self._printer."""
        target = printer or self._printer
        if target is not None:
            if self._printer is target:
                self._printer = None
            try:
                await target.close()
            except Exception:
                pass

    def force_reconnect_stale_session(self, reason: str = ""):
        """Force reconnection when connection watchdog detects a stalled session."""
        logger.info("Forcing reconnect of Elegoo client (%s)", reason)
        self.state.connected = False
        prev_task = self._connect_task
        if self._loop and self._loop.is_running():
            self._connect_task = self._loop.create_task(self._async_connect(prev_task=prev_task))
        elif prev_task and not prev_task.done():
            prev_task.cancel()

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
        prev_task = self._connect_task
        self._connect_task = self._loop.create_task(self._async_connect(prev_task=prev_task))

    def disconnect(self, timeout=None):
        """Disconnect and clean up tasks."""
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
        printer = self._printer
        self._printer = None
        if printer and self._loop and self._loop.is_running():
            self._loop.create_task(self._close_printer(printer))
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

    async def _async_connect(self, prev_task: asyncio.Task[None] | None = None):
        # Ensure any previous connection task and its WebSocket are completely
        # closed before opening a new connection. Otherwise the CC1 firmware
        # rejects the new WebSocket handshake with HTTP 500 while the old
        # connection slot is still open.
        if prev_task is not None and prev_task is not asyncio.current_task() and not prev_task.done():
            prev_task.cancel()
            try:
                await prev_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_printer()

        # Discover the mainboard_id so we can pass it to connect_auto.
        # The CC1 printer only pushes its mainboard ID when idle/active — when
        # paused or errored it is silent and any call that needs the ID (e.g.
        # attributes(), watch() internally) will time out waiting for it.
        # Passing mainboard_id= explicitly bypasses that wait entirely.
        try:
            while True:
                if not self._cached_mainboard_id:
                    try:
                        # Targeted discovery works from Docker/LXC networks where
                        # LAN broadcast packets are not forwarded.
                        discovered = await elegoo_discover(
                            timeout=2.0,
                            broadcast_address=self.ip_address,
                        )
                        for d in discovered:
                            if d.host == self.ip_address and d.mainboard_id:
                                self._cached_mainboard_id = d.mainboard_id
                                logger.info(
                                    "Discovered Elegoo mainboard_id=%s for %s",
                                    self._cached_mainboard_id,
                                    self.ip_address,
                                )
                                break
                    except Exception as e:
                        logger.debug("Elegoo discovery attempt failed for %s: %s", self.ip_address, e)

                try:
                    logger.info(
                        "Connecting to Elegoo printer at %s (mainboard_id=%s)",
                        self.ip_address,
                        self._cached_mainboard_id,
                    )
                    self._printer = await connect_auto(
                        self.ip_address,
                        access_code=self.access_code,
                        connect_timeout=5.0,
                        enable_control=True,
                        mainboard_id=self._cached_mainboard_id,
                    )
                    if self._printer.mainboard_id:
                        self._cached_mainboard_id = self._printer.mainboard_id
                    self.state.connected = True

                    if self.on_state_change:
                        self.on_state_change(self.state)

                    # Iterate watch loop to receive telemetry pushes
                    async for status in self._printer.watch():
                        if self._printer.mainboard_id:
                            self._cached_mainboard_id = self._printer.mainboard_id
                        self._update_state(status)

                except asyncio.CancelledError:
                    logger.info("Connection task cancelled for Elegoo printer at %s", self.ip_address)
                    break
                except Exception as e:
                    logger.warning(
                        "Connection lost or failed to connect to Elegoo printer at %s: %s. Reconnecting in 2s...",
                        self.ip_address,
                        e,
                    )
                    self.state.connected = False
                    if self.on_state_change:
                        self.on_state_change(self.state)

                    await self._close_printer()
                    await asyncio.sleep(2.0)
                    continue

                await self._close_printer()

                if self.state.connected:
                    self.state_machine.set_connected(False)
                await asyncio.sleep(2.0)
        finally:
            await self._close_printer()

    def _update_state(self, status: Status):
        self._last_message_time = time.time()
        self._last_status = status
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

        current_file = None
        total_layers = 0
        if status.print_info:
            current_file = status.print_info.filename
            self.state.progress = float(status.print_info.progress or 0)
            self.state.layer_num = status.print_info.current_layer or 0
            self.state.total_layers = status.print_info.total_layer or 0
            total_layers = self.state.total_layers

            # Map remaining time from ticks if available (ticks are in seconds).
            # Stored in minutes on state to match expected schema.
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

        if state_str == "FINISH" and not self.state.subtask_name and not self.state_machine._last_known_job_name:
            asyncio.create_task(self._recover_finished_job_name())

        # Trigger AMS/Canvas state updates
        if hasattr(self._printer, "canvas_status") and self._loop:
            self._loop.create_task(self._update_canvas_status())

        # Delegate lifecycle evaluation & callbacks to shared state machine
        self.state_machine.process_transition(
            state_str=state_str,
            current_file=current_file,
            stage_code=stg_cur,
            total_layers_from_frame=total_layers,
        )

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
        if asyncio.iscoroutine(coro):
            coro.close()
        return False

    @staticmethod
    def _is_file_checking(status: Status | None) -> bool:
        """Return True if the printer reports post-upload file checking (CurrentStatus 8 or PrintStatus 10)."""
        if status is None:
            return False
        cur = getattr(status, "current_status", None) or []
        if 8 in cur:
            return True
        if getattr(status, "print_status", None) == 10:
            return True
        return False

    async def _ensure_connected(self, timeout: float = 10.0) -> bool:
        """Wait for the printer client to finish connecting if a reconnect is in flight."""
        if self._printer is not None:
            return True
        if self._loop and self._loop.is_running() and (self._connect_task is None or self._connect_task.done()):
            self._connect_task = self._loop.create_task(self._async_connect())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._printer is not None:
                return True
            await asyncio.sleep(0.25)
        return self._printer is not None

    async def wait_for_upload_ready(
        self,
        filename: str,
        timeout: float = 30.0,
        settle_delay: float = 1.0,
    ) -> None:
        """Wait for the Elegoo printer to finish verifying and moving an uploaded file into /local/.

        Matches OrcaSlicer ElegooLink::loopUpload + checkResult:
          1. Wait 1s after the final HTTP upload chunk completes.
          2. Poll printer status while CurrentStatus contains 8 (FILE_CHECKING)
             or PrintStatus == 10 (up to timeout).
          3. Wait 1s settle delay before sending START_PRINT (Cmd 128).
        """
        if settle_delay > 0:
            await asyncio.sleep(settle_delay)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._printer or not hasattr(self._printer, "status"):
                break
            try:
                st = await self._printer.status()
                if st is not None:
                    self._update_state(st)
                if not self._is_file_checking(st):
                    break
                logger.info(
                    "Elegoo printer %s is finalizing uploaded file %s (current_status=%s, print_status=%s) — waiting...",
                    self.ip_address,
                    filename,
                    getattr(st, "current_status", None),
                    getattr(st, "print_status", None),
                )
            except Exception as e:
                logger.debug("Elegoo post-upload status check on %s failed: %s", self.ip_address, e)
                break
            await asyncio.sleep(1.0)

        if settle_delay > 0:
            await asyncio.sleep(settle_delay)

    # --- Control Interface implementation ---

    async def start_print_async(
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
        max_retries: int = 5,
        retry_delay: float = 1.5,
        initial_coro=None,
        **kwargs,
    ) -> bool:
        """Send START_PRINT (Cmd 128) and await the printer's Ack, retrying if the
        printer is still finalizing the uploaded file in /local/ (Ack=2) or reconnecting.
        """
        if not await self._ensure_connected(timeout=10.0) or not self._printer:
            if asyncio.iscoroutine(initial_coro):
                initial_coro.close()
            logger.error(
                "ElegooCentauriClient: cannot start_print on %s — printer client not connected after waiting",
                self.ip_address,
            )
            return False

        if self._is_file_checking(self._last_status):
            await self.wait_for_upload_ready(filename, settle_delay=0.5)

        self.state_machine.record_dispatch(filename=filename)
        auto_level = bed_levelling != "off" if isinstance(bed_levelling, str) else bool(bed_levelling)
        platform_type = map_bed_type_to_elegoo_platform_type(bed_type)

        for attempt in range(1, max(1, max_retries) + 1):
            if not await self._ensure_connected(timeout=10.0) or not self._printer:
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    continue
                logger.error(
                    "ElegooCentauriClient: start_print(%s) failed on %s — printer disconnected",
                    filename,
                    self.ip_address,
                )
                return False

            logger.info(
                "ElegooCentauriClient: Sending start_print to %s (attempt %d/%d): file=%s, auto_level=%s, platform_type=%s, timelapse=%s",
                self.ip_address,
                attempt,
                max_retries,
                filename,
                auto_level,
                platform_type,
                timelapse,
            )
            try:
                if attempt == 1 and initial_coro is not None:
                    coro = initial_coro
                    initial_coro = None
                else:
                    coro = self._printer.start_print(
                        filename,
                        storage="local",
                        auto_leveling=auto_level,
                        timelapse=timelapse,
                        platform_type=platform_type,
                    )
                resp = await coro
                logger.info(
                    "Elegoo start_print(%s) completed successfully on %s (attempt %d/%d): %s",
                    filename,
                    self.ip_address,
                    attempt,
                    max_retries,
                    resp,
                )
                return True
            except Exception as exc:
                err_str = str(exc)
                # Ack=1 means printer is already busy printing
                if "Printer is busy" in err_str:
                    logger.warning(
                        "Elegoo start_print(%s) rejected on %s because printer is busy: %s",
                        filename,
                        self.ip_address,
                        exc,
                    )
                    return False
                if attempt < max_retries:
                    logger.warning(
                        "Elegoo start_print(%s) attempt %d/%d on %s failed (%s) — retrying in %.1fs...",
                        filename,
                        attempt,
                        max_retries,
                        self.ip_address,
                        exc,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        "Elegoo start_print(%s) task failed on %s after %d attempts: %s",
                        filename,
                        self.ip_address,
                        max_retries,
                        exc,
                    )
                    return False
        return False

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
        **kwargs,
    ) -> bool:
        if not self._printer and not (self._connect_task and not self._connect_task.done()):
            logger.error("ElegooCentauriClient: cannot start_print on %s — printer client not connected", self.ip_address)
            return False
        # Optimistically record dispatch on state_machine
        self.state_machine.record_dispatch(filename=filename)
        auto_level = bed_levelling != "off" if isinstance(bed_levelling, str) else bool(bed_levelling)
        platform_type = map_bed_type_to_elegoo_platform_type(bed_type)
        initial_coro = None
        if self._printer is not None:
            initial_coro = self._printer.start_print(
                filename,
                storage="local",
                auto_leveling=auto_level,
                timelapse=timelapse,
                platform_type=platform_type,
            )
        return self._run_async(
            self.start_print_async(
                filename=filename,
                plate_id=plate_id,
                ams_mapping=ams_mapping,
                bed_levelling=bed_levelling,
                flow_cali=flow_cali,
                vibration_cali=vibration_cali,
                layer_inspect=layer_inspect,
                timelapse=timelapse,
                use_ams=use_ams,
                nozzle_offset_cali=nozzle_offset_cali,
                nozzle_mapping=nozzle_mapping,
                bed_type=bed_type,
                initial_coro=initial_coro,
                **kwargs,
            ),
            action_name=f"start_print({filename})",
        )


    def stop_print(self, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.stop(), action_name="stop_print")

    def pause_print(self, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.pause(), action_name="pause_print")

    def resume_print(self, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.resume())

    def set_print_speed(self, mode: int, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        level_map = {1: "silent", 2: "balanced", 3: "sport", 4: "ludicrous"}
        speed_mode = level_map.get(mode, "balanced")
        return self._run_async(self._printer.set_print_speed(speed_mode))

    def set_nozzle_temperature(self, target: float, nozzle: int = 0, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_temperatures(nozzle=target))

    def set_bed_temperature(self, target: float, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_temperatures(bed=target))

    def set_chamber_temperature(self, target: float, *args, **kwargs) -> bool:
        if not self._printer:
            return False
        return self._run_async(self._printer.set_temperatures(chamber=target))

    def set_fan_speed(self, fan_id: int, pwm_speed: int, *args, **kwargs) -> bool:
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
        # CC1 SDCP (WebSocket) firmware V1.4.49 drops the WebSocket connection on Cmd 258
        if hasattr(self._printer, "_ws"):
            return []
        eff_storage = "usb" if ("usb" in path.lower() or "udisk" in path.lower()) else storage
        res = self._run_async(self._printer.list_files(storage=eff_storage), action_name="list_files")
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
        # CC1 over SDCP does not support disk_info (CC2 MQTT method 1048 only)
        if hasattr(self._printer, "_ws"):
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

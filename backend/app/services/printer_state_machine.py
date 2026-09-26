"""Shared Printer State Machine and Lifecycle Handler.

Unifies printer state management, lifecycle tracking, and callback dispatch
across all printer hardware drivers (Bambu Lab, Elegoo, Flashforge, etc.).

Ensures consistent handling of:
- State transitions (IDLE, PREPARE, RUNNING, PAUSE, FINISH, FAILED)
- Print start detection (is_new_print, file changes, restart recovery)
- Print completion detection (FINISH, FAILED, abort from IDLE)
- Automatic subtask ID management and persistence across dispatches
- Standardized callback payloads (converting remaining_time minutes to seconds)
- Granular layer change and temperature change callbacks
- Proper preservation of job state on FINISH (for plate-clear confirmation)
- Clean reset of job state on terminal completion (IDLE, FAILED)
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from backend.app.services.bambu_mqtt import PrinterState

logger = logging.getLogger(__name__)

ACTIVE_PRINT_STATES = frozenset({"PREPARE", "SLICING", "RUNNING", "PAUSE"})


class PrinterStateMachine:
    """Manages the lifecycle, state transitions, and callback notifications for a printer."""

    def __init__(
        self,
        serial_number: str,
        initial_state: PrinterState | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
        on_print_running_observed: Callable[[dict], None] | None = None,
        on_layer_change: Callable[[int], None] | None = None,
        on_bed_temp_update: Callable[[float], None] | None = None,
        on_finish_photo_moment: Callable[[dict], None] | None = None,
    ):
        self.serial_number = serial_number
        self.state: PrinterState = initial_state or PrinterState()
        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete
        self.on_print_running_observed = on_print_running_observed
        self.on_layer_change = on_layer_change
        self.on_bed_temp_update = on_bed_temp_update
        self.on_finish_photo_moment = on_finish_photo_moment

        # Internal lifecycle flags
        self._was_running: bool = False
        self._completion_triggered: bool = False
        self._finish_photo_captured: bool = False
        self._timelapse_during_print: bool = False
        self._previous_gcode_state: str | None = None
        self._previous_gcode_file: str | None = None
        self._last_layer_num: int = 0
        self._last_bed_temp: float = 0.0
        self._last_valid_progress: float = 0.0
        self._last_valid_layer_num: int = 0
        self._last_known_job_name: str | None = None
        self.last_dispatch_subtask_id: str | None = None
        self.last_dispatch_time: float = 0.0

    def record_dispatch(
        self,
        subtask_id: str | None = None,
        filename: str | None = None,
    ) -> str:
        """Record an outbound print dispatch from PrintHive.

        Generates and stores a unique subtask_id if none was provided, and updates
        state optimistically so that the scheduler watchdog and UI can track it.
        """
        if not subtask_id:
            subtask_id = str(int(time.time() * 1000) % 2_147_483_647 or 1)
        self.last_dispatch_subtask_id = subtask_id
        self.last_dispatch_time = time.time()
        self.state.subtask_id = subtask_id
        if filename:
            self.state.current_print = filename
            self.state.subtask_name = filename
            self.state.gcode_file = filename
            self._last_known_job_name = filename
        logger.info(
            "[%s] Recorded dispatch: subtask_id=%s, filename=%s",
            self.serial_number,
            subtask_id,
            filename,
        )
        return subtask_id

    def set_connected(self, connected: bool) -> None:
        """Update connection status and notify if changed."""
        if self.state.connected != connected:
            self.state.connected = connected
            if not connected:
                self.state.state = "unknown"
            if self.on_state_change:
                self.on_state_change(self.state)

    def process_transition(
        self,
        state_str: str,
        current_file: str | None = None,
        stage_code: int = -1,
        raw_data: dict | None = None,
        ams_mapping: Any = None,
        hms_errors: list | None = None,
        total_layers_from_frame: int = 0,
    ) -> None:
        """Evaluate canonical printer state transitions and trigger registered callbacks.

        Standardizes start, running, completion, layer change, bed temp, and status
        change behaviors across all printer drivers.
        """
        normalized_state = (state_str or "IDLE").strip().upper()
        if normalized_state not in ("IDLE", "PREPARE", "SLICING", "RUNNING", "PAUSE", "FINISH", "FAILED"):
            normalized_state = "IDLE"

        self.state.state = normalized_state
        self.state.gcode_state = normalized_state
        self.state.stg_cur = stage_code

        # Subtask and filename management
        if current_file:
            self.state.current_print = current_file
            self.state.gcode_file = current_file
            if not self.state.subtask_name:
                self.state.subtask_name = current_file
            self._last_known_job_name = current_file

        if normalized_state in ("RUNNING", "PREPARE"):
            if not self.state.subtask_id and self.last_dispatch_subtask_id:
                self.state.subtask_id = self.last_dispatch_subtask_id

        if normalized_state == "FINISH" and not self.state.subtask_name:
            if self._last_known_job_name:
                self.state.subtask_name = self._last_known_job_name
                self.state.current_print = self._last_known_job_name
                self.state.gcode_file = self._last_known_job_name

        active_file = current_file or self.state.gcode_file or self.state.current_print or self.state.subtask_name or ""

        # --- Detect Print Start ---
        # 1. Normal print start: transitions to RUNNING or PREPARE with a file
        # Dispatched prints (last_dispatch_subtask_id is set) or active transitions from non-running states
        is_new_print = (
            normalized_state in ("RUNNING", "PREPARE")
            and (self._previous_gcode_state not in ("RUNNING", "PREPARE", "PAUSE"))
            and bool(active_file)
            and not self._was_running
            and (self._previous_gcode_state is not None or self.last_dispatch_subtask_id is not None)
        )
        # 2. File change while active
        is_file_change = (
            normalized_state in ("RUNNING", "PREPARE")
            and bool(active_file)
            and active_file != self._previous_gcode_file
            and self._previous_gcode_file is not None
        )

        running_first_observed = False
        if normalized_state in ("RUNNING", "PREPARE") and active_file:
            if not self._was_running:
                logger.debug("[%s] Tracking active state for %s (state=%s)", self.serial_number, active_file, normalized_state)
                running_first_observed = True
            self._was_running = True
            self._completion_triggered = False

        if is_new_print or is_file_change:
            self.state.hms_errors = []
            self.state.layer_num = 0
            if total_layers_from_frame > 0:
                self.state.total_layers = total_layers_from_frame
            self._was_running = True
            self._completion_triggered = False
            self._finish_photo_captured = False
            self._last_valid_progress = 0.0
            self._last_valid_layer_num = 0

            if self.on_print_start:
                logger.info(
                    "[%s] PRINT START detected - file: %s, subtask: %s, state: %s",
                    self.serial_number,
                    active_file,
                    self.state.subtask_name,
                    normalized_state,
                )
                self.on_print_start({
                    "filename": active_file,
                    "subtask_name": self.state.subtask_name or active_file,
                    "remaining_time": self.state.remaining_time * 60 if self.state.remaining_time > 0 else None,
                    "raw_data": raw_data or {},
                    "ams_mapping": ams_mapping,
                })
        elif running_first_observed and self.on_print_running_observed:
            logger.info(
                "[%s] RUNNING observed without PRINT START (restart-recovery) - file: %s, subtask: %s",
                self.serial_number,
                active_file,
                self.state.subtask_name,
            )
            self.on_print_running_observed({
                "filename": active_file,
                "subtask_name": self.state.subtask_name or active_file,
                "remaining_time": self.state.remaining_time * 60 if self.state.remaining_time > 0 else None,
                "raw_data": raw_data or {},
                "ams_mapping": ams_mapping,
            })

        # --- Detect Print Completion ---
        should_trigger_completion = (
            normalized_state in ("FINISH", "FAILED")
            and not self._completion_triggered
            and bool(self.on_print_complete)
            and (
                self._previous_gcode_state in ("RUNNING", "PAUSE")
                or (self._was_running and self._previous_gcode_state != normalized_state)
                or (normalized_state == "FAILED" and self._previous_gcode_state in ("PREPARE", "SLICING"))
            )
        )
        if (
            normalized_state == "IDLE"
            and self._previous_gcode_state in ("RUNNING", "PAUSE", "PREPARE")
            and self._was_running
            and not self._completion_triggered
            and bool(self.on_print_complete)
        ):
            should_trigger_completion = True

        if should_trigger_completion:
            if normalized_state == "FINISH":
                status = "completed"
            elif normalized_state == "FAILED":
                status = "failed"
            else:
                status = "aborted"

            completed_file = self._previous_gcode_file or active_file
            logger.info(
                "[%s] PRINT COMPLETE detected - state: %s, status: %s, file: %s",
                self.serial_number,
                normalized_state,
                status,
                completed_file,
            )

            if status == "completed" and not self._finish_photo_captured and self.on_finish_photo_moment:
                self._finish_photo_captured = True
                self.on_finish_photo_moment({
                    "trigger": "finish_state",
                    "filename": completed_file,
                    "subtask_name": self.state.subtask_name,
                    "timelapse_was_active": self._timelapse_during_print,
                })

            self._completion_triggered = True
            self._was_running = False
            self._timelapse_during_print = False

            self.on_print_complete({
                "status": status,
                "filename": completed_file,
                "subtask_name": self.state.subtask_name or completed_file,
                "raw_data": raw_data or {},
                "timelapse_was_active": self._timelapse_during_print,
                "hms_errors": hms_errors or [],
                "ams_mapping": ams_mapping,
                "last_progress": self._last_valid_progress,
                "last_layer_num": self._last_valid_layer_num,
            })

        # Terminal state cleanup
        if normalized_state in ("IDLE", "FAILED"):
            self._was_running = False
            self._completion_triggered = False
            self.state.current_print = ""
            self.state.subtask_name = ""
            self.state.gcode_file = ""
            self.state.subtask_id = ""
            self.last_dispatch_subtask_id = None
        elif normalized_state == "FINISH":
            self._was_running = False
            # Preserve subtask_name, current_print, and gcode_file while awaiting plate clear

        # Layer tracking & callback
        if self.state.layer_num != self._last_layer_num:
            self._last_layer_num = self.state.layer_num
            if self.state.layer_num > 0:
                self._last_valid_layer_num = self.state.layer_num
            if self.on_layer_change:
                self.on_layer_change(self.state.layer_num)

        # Bed temp tracking & callback
        bed_temp = float(self.state.temperatures.get("bed", 0.0) or 0.0)
        if abs(bed_temp - self._last_bed_temp) > 0.1:
            self._last_bed_temp = bed_temp
            if self.on_bed_temp_update:
                self.on_bed_temp_update(bed_temp)

        # Progress tracking
        if self.state.progress > 0:
            self._last_valid_progress = self.state.progress

        self._previous_gcode_state = normalized_state
        if active_file:
            self._previous_gcode_file = active_file

        # Notify state change
        if self.on_state_change:
            self.on_state_change(self.state)

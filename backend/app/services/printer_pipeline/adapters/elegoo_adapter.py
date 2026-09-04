"""Elegoo Feature Pipeline Adapter.

Integrates Elegoo Centauri Carbon (CC1 / CC2) printers into the PrintHive
Feature Pipeline via SDCP, native HTTP file endpoints, and pycentauri.
"""

import asyncio
import logging
from typing import Callable, Any
import httpx

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.elegoo_client import ElegooCentauriClient
from backend.app.services.printer_pipeline.capabilities import (
    PrinterCapabilities,
    PrinterCapability,
)
from backend.app.services.printer_pipeline.interfaces import IPrinterAdapter
from backend.app.services.printer_pipeline.models import (
    JogRequest,
    FileInfo,
    StorageInfo,
    MultiMaterialSlot,
    StandardPrintJobRequest,
)

logger = logging.getLogger(__name__)


class ElegooAdapter(IPrinterAdapter):
    """Adapter for Elegoo printers (Centauri Carbon 1 & 2)."""

    def __init__(
        self,
        printer_id: int,
        ip_address: str,
        serial_number: str,
        access_code: str = "",
        model: str | None = None,
        client: ElegooCentauriClient | None = None,
    ):
        self._printer_id = printer_id
        self._ip_address = ip_address
        self._serial_number = serial_number
        self._access_code = access_code
        self._model = model or "Centauri Carbon"

        if client is not None:
            self._client = client
        else:
            self._client = ElegooCentauriClient(
                ip_address=ip_address,
                serial_number=serial_number,
                access_code=access_code,
                model=self._model,
            )

    @property
    def client(self) -> ElegooCentauriClient:
        return self._client

    @property
    def printer_id(self) -> int:
        return self._printer_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def serial_number(self) -> str:
        return self._serial_number

    @property
    def ip_address(self) -> str:
        return self._ip_address

    @property
    def capabilities(self) -> PrinterCapabilities:
        flags = (
            PrinterCapability.TELEMETRY
            | PrinterCapability.PRINT_CONTROL
            | PrinterCapability.START_PRINT
            | PrinterCapability.MOTION_HOME
            | PrinterCapability.MOTION_JOG
            | PrinterCapability.HEATED_BED
            | PrinterCapability.EXTRUDER_HEATER
            | PrinterCapability.CHAMBER_SENSOR
            | PrinterCapability.FAN_PART
            | PrinterCapability.FAN_AUX
            | PrinterCapability.FAN_CHAMBER
            | PrinterCapability.CHAMBER_LIGHT
            | PrinterCapability.MULTI_MATERIAL
            | PrinterCapability.STORAGE_LOCAL
            | PrinterCapability.FILE_LIST
            | PrinterCapability.FILE_UPLOAD
            | PrinterCapability.FILE_DOWNLOAD
            | PrinterCapability.FILE_DELETE
            | PrinterCapability.GCODE_CONSOLE
            | PrinterCapability.CAMERA_STREAM
            | PrinterCapability.CAMERA_SNAPSHOT
            | PrinterCapability.PRINT_SPEED_MODE
        )
        return PrinterCapabilities(flags=flags)

    # =========================================================================
    # Lifecycle & Telemetry
    # =========================================================================

    def connect(self, loop=None) -> None:
        self._client.connect(loop=loop)

    def disconnect(self, timeout: float = 0) -> None:
        self._client.disconnect(timeout=timeout)

    def get_state(self) -> PrinterState:
        return self._client.state

    def is_connected(self) -> bool:
        return bool(self._client.state.connected)

    def request_status_update(self) -> bool:
        return self._client.request_status_update()

    # =========================================================================
    # Print Job Control
    # =========================================================================

    def start_print(self, request: StandardPrintJobRequest) -> bool:
        auto_level = request.bed_levelling in ("auto", "always", True)
        return self._client.start_print(
            filename=request.filename,
            plate_id=request.plate_id,
            ams_mapping=request.ams_mapping,
            auto_level=auto_level,
            timelapse=request.timelapse,
            bed_type=request.bed_type,
        )

    def pause_print(self) -> bool:
        return self._client.pause_print()

    def resume_print(self) -> bool:
        return self._client.resume_print()

    def stop_print(self) -> bool:
        return self._client.stop_print()

    def set_print_speed(self, mode: int) -> bool:
        return self._client.set_print_speed(mode)

    # =========================================================================
    # Motion Control
    # =========================================================================

    def home(self, axes: str = "XYZ") -> bool:
        return self._client.home_axes(axes)

    def jog(self, request: JogRequest) -> bool:
        axis_str = request.axis.value if hasattr(request.axis, "value") else str(request.axis)
        return self._client.move_axis(axis=axis_str, distance=request.distance_mm, speed=request.speed_mm_min)

    def disable_motors(self) -> bool:
        # Elegoo SDCP has no bare M18 command, returns True as safe no-op
        return True

    def enable_motors(self) -> bool:
        # Homing or movement re-energizes motors
        return True

    # =========================================================================
    # Thermal Control
    # =========================================================================

    def set_bed_temperature(self, target_c: int) -> bool:
        return self._client.set_bed_temperature(float(target_c))

    def set_nozzle_temperature(self, target_c: int, nozzle: int = 0) -> bool:
        return self._client.set_nozzle_temperature(float(target_c), nozzle=nozzle)

    def set_chamber_temperature(self, target_c: int) -> bool:
        return self._client.set_chamber_temperature(float(target_c))

    # =========================================================================
    # Fan Control
    # =========================================================================

    def set_part_fan(self, speed: int) -> bool:
        return self._client.set_fan_speed(1, speed)

    def set_aux_fan(self, speed: int) -> bool:
        return self._client.set_fan_speed(2, speed)

    def set_chamber_fan(self, speed: int) -> bool:
        return self._client.set_fan_speed(3, speed)

    def set_fan_speed(self, fan_index: int, speed: int) -> bool:
        return self._client.set_fan_speed(fan_index, speed)

    # =========================================================================
    # Light Control
    # =========================================================================

    def set_chamber_light(self, on: bool) -> bool:
        return self._client.set_chamber_light(on)

    # =========================================================================
    # Multi-Material & Canvas Control
    # =========================================================================

    def get_slots(self) -> list[MultiMaterialSlot]:
        slots = []
        raw_data = self._client.state.raw_data or {}
        ams_list = raw_data.get("ams", {}).get("ams", []) if isinstance(raw_data.get("ams"), dict) else raw_data.get("ams", [])
        if isinstance(ams_list, list):
            for ams_unit in ams_list:
                for tray in ams_unit.get("tray", []):
                    slots.append(
                        MultiMaterialSlot(
                            slot_id=int(tray.get("id", 0)),
                            name=tray.get("tray_id_name"),
                            color_hex=tray.get("tray_color", "FFFFFFFF")[:6],
                            filament_type=tray.get("tray_type", "PLA"),
                            remain_pct=tray.get("remain"),
                            temp_min=tray.get("nozzle_temp_min"),
                            temp_max=tray.get("nozzle_temp_max"),
                        )
                    )
        return slots

    def load_slot(self, slot_id: int) -> bool:
        return False

    def unload_slot(self) -> bool:
        return False

    # =========================================================================
    # File Storage Control (SDCP + HTTP /downloadFile)
    # =========================================================================

    def list_files(self, path: str = "/") -> list[FileInfo]:
        p = path.lower()
        if "timelapse" in p or "ips" in p:
            return self._list_timelapses()
        target_url = "/usb" if "usb" in p or "udisk" in p else "/local"
        raw_files = self._client.list_files_sync(storage="local", path=target_url)
        result = []
        for f in raw_files:
            name = f.get("name") or f.get("Name") or ""
            if "/" in name:
                name = name.split("/")[-1]
            size = f.get("size") or f.get("Size") or f.get("FileSize") or 0
            mtime = f.get("modifyTime") or f.get("ModifyTime") or f.get("CreateTime")
            result.append(
                FileInfo(
                    name=name,
                    path=f"{target_url}/{name}",
                    size_bytes=int(size) if size else 0,
                    modified_timestamp=float(mtime) if mtime else None,
                )
            )
        return result

    def _list_timelapses(self) -> list[FileInfo]:
        if not self._client or not self._client._printer:
            return []
        try:
            res = self._client._run_async(self._client._printer.print_history(), action_name="print_history")
            tasks = res.get("history_task_list", []) if isinstance(res, dict) else []
            result = []
            for t in tasks:
                tname = t.get("task_name") or ""
                if not tname:
                    continue
                vname = f"{tname}.mp4"
                result.append(
                    FileInfo(
                        name=vname,
                        path=f"/timelapse/{vname}",
                        size_bytes=0,
                        modified_timestamp=t.get("end_time"),
                    )
                )
            return result
        except Exception:
            logger.exception("[%s] Failed to query timelapses", self._serial_number)
            return []

    def get_storage_info(self) -> StorageInfo | None:
        info = self._client.get_disk_info_sync()
        if not info:
            return None
        total = info.get("total") or info.get("Total") or 0
        free = info.get("free") or info.get("Free") or 0
        used = total - free if total >= free else 0
        return StorageInfo(total_bytes=int(total), used_bytes=int(used), free_bytes=int(free))

    def delete_file(self, remote_path: str) -> bool:
        return self._client.delete_files_sync([remote_path], storage="local")

    def download_file(self, remote_path: str) -> bytes | None:
        """Download file from physical Elegoo CC1 printer via /downloadFile/{path} or timelapse."""
        p = remote_path.lower()
        if "timelapse" in p or "ips" in p:
            filename = remote_path.split("/")[-1]
            url = f"http://{self._ip_address}:3030/local/aic_tlp/{filename}"
        else:
            clean_path = remote_path.lstrip("/")
            url = f"http://{self._ip_address}:3030/downloadFile/{clean_path}"
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.content
                logger.warning("[%s] Download failed with status %s from %s", self._serial_number, resp.status_code, url)
        except Exception as e:
            logger.exception("[%s] Error downloading file from %s: %s", self._serial_number, url, e)
        return None

    def upload_file(self, local_path: str, filename: str) -> bool:
        # Handled asynchronously in print_scheduler with pause_telemetry()
        return False

    # =========================================================================
    # Camera
    # =========================================================================

    def get_stream_url(self) -> str | None:
        return f"http://{self._ip_address}:3030/mjpeg"

    def get_snapshot(self) -> bytes | None:
        if self._client._printer and hasattr(self._client._printer, "snapshot"):
            try:
                return self._client._run_async(self._client._printer.snapshot(), action_name="snapshot")
            except Exception:
                logger.exception("[%s] Snapshot fetch failed", self._serial_number)
        return None

    # =========================================================================
    # G-code Console
    # =========================================================================

    def send_gcode(self, gcode: str) -> bool:
        return self._client.send_gcode(gcode)

"""Bambu Lab Feature Pipeline Adapter.

Wraps BambuMQTTClient and BambuFTPClient to provide unified IPrinterAdapter
contracts for Bambu Lab printers (X1, P1, A1, H2, X2 series).
"""

import logging
from typing import Callable, Any

from backend.app.services.bambu_mqtt import BambuMQTTClient, PrinterState
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
from backend.app.services.printer_manager import (
    supports_chamber_temp,
    supports_chamber_heater,
    supports_drying,
    supports_drying_while_printing,
    drying_screen_only,
)

logger = logging.getLogger(__name__)


class BambuAdapter(IPrinterAdapter):
    """Adapter for Bambu Lab printers."""

    def __init__(
        self,
        printer_id: int,
        ip_address: str,
        serial_number: str,
        access_code: str,
        model: str | None = None,
        mqtt_client: BambuMQTTClient | None = None,
    ):
        self._printer_id = printer_id
        self._ip_address = ip_address
        self._serial_number = serial_number
        self._access_code = access_code
        self._model = model or "Bambu"

        if mqtt_client is not None:
            self._client = mqtt_client
        else:
            self._client = BambuMQTTClient(
                ip_address=ip_address,
                serial_number=serial_number,
                access_code=access_code,
                model=model,
            )

    @property
    def client(self) -> BambuMQTTClient:
        """Underlying BambuMQTTClient instance."""
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
            | PrinterCapability.MOTOR_CONTROL
            | PrinterCapability.HEATED_BED
            | PrinterCapability.EXTRUDER_HEATER
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
            | PrinterCapability.SKIP_OBJECTS
            | PrinterCapability.PRINT_SPEED_MODE
        )

        caps = PrinterCapabilities(flags=flags)

        if supports_chamber_temp(self._model):
            caps.add(PrinterCapability.CHAMBER_SENSOR)

        if supports_chamber_heater(self._model):
            caps.add(PrinterCapability.CHAMBER_HEATER)

        fw = self._client.state.firmware_version
        if supports_drying(self._model, fw):
            caps.add(PrinterCapability.DRYING_REMOTE)

        if drying_screen_only(self._model):
            caps.add(PrinterCapability.DRYING_SCREEN_ONLY)

        if supports_drying_while_printing(self._model, fw):
            caps.add(PrinterCapability.DRYING_WHILE_PRINTING)

        return caps

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
        return self._client.start_print(
            filename=request.filename,
            plate_id=request.plate_id,
            ams_mapping=request.ams_mapping,
            bed_levelling=request.bed_levelling,
            flow_cali=request.flow_cali,
            vibration_cali=request.vibration_cali,
            layer_inspect=request.layer_inspect,
            timelapse=request.timelapse,
            use_ams=request.use_ams,
            nozzle_offset_cali=request.nozzle_offset_cali,
            nozzle_mapping=request.nozzle_mapping,
            bed_type=request.bed_type,
        )

    def pause_print(self) -> bool:
        return self._client.pause_print()

    def resume_print(self) -> bool:
        return self._client.resume_print()

    def stop_print(self) -> bool:
        return self._client.stop_print()

    def skip_objects(self, object_ids: list[int]) -> bool:
        return self._client.skip_objects(object_ids)

    def set_print_speed(self, mode: int) -> bool:
        return self._client.set_print_speed(mode)

    # =========================================================================
    # Motion Control
    # =========================================================================

    def home(self, axes: str = "XYZ") -> bool:
        return self._client.home_axes(axes)

    def jog(self, request: JogRequest) -> bool:
        axis_str = request.axis.value if hasattr(request.axis, "value") else str(request.axis)
        return self._client.move_axis(
            axis=axis_str,
            distance=request.distance_mm,
            speed=request.speed_mm_min,
        )

    def disable_motors(self) -> bool:
        return self._client.disable_motors()

    def enable_motors(self) -> bool:
        return self._client.enable_motors()

    # =========================================================================
    # Thermal Control
    # =========================================================================

    def set_bed_temperature(self, target_c: int) -> bool:
        return self._client.set_bed_temperature(target_c)

    def set_nozzle_temperature(self, target_c: int, nozzle: int = 0) -> bool:
        return self._client.set_nozzle_temperature(target_c, nozzle=nozzle)

    def set_chamber_temperature(self, target_c: int) -> bool:
        return self._client.set_chamber_temperature(target_c)

    # =========================================================================
    # Fan Control
    # =========================================================================

    def set_part_fan(self, speed: int) -> bool:
        return self._client.set_part_fan(speed)

    def set_aux_fan(self, speed: int) -> bool:
        return self._client.set_aux_fan(speed)

    def set_chamber_fan(self, speed: int) -> bool:
        return self._client.set_chamber_fan(speed)

    def set_fan_speed(self, fan_index: int, speed: int) -> bool:
        return self._client.set_fan_speed(fan_index, speed)

    # =========================================================================
    # Light Control
    # =========================================================================

    def set_chamber_light(self, on: bool) -> bool:
        return self._client.set_chamber_light(on)

    # =========================================================================
    # Multi-Material & AMS Control
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
                            rfid=tray.get("tag_uid"),
                            brand=tray.get("tray_sub_brands"),
                        )
                    )
        return slots

    def load_slot(self, slot_id: int) -> bool:
        return self._client.ams_load_filament(slot_id)

    def unload_slot(self) -> bool:
        return self._client.ams_unload_filament()

    def send_drying_command(
        self, ams_id: int, temp: int, duration: int, mode: int = 1, filament: str = "", rotate_tray: bool = False
    ) -> bool:
        return self._client.send_drying_command(
            ams_id=ams_id,
            temp=temp,
            duration=duration,
            mode=mode,
            filament=filament,
            rotate_tray=rotate_tray,
        )

    # =========================================================================
    # File Storage Control (FTP)
    # =========================================================================

    def list_files(self, path: str = "/") -> list[FileInfo]:
        # Handled via BambuFTPClient
        return []

    def get_storage_info(self) -> StorageInfo | None:
        return None

    def delete_file(self, remote_path: str) -> bool:
        return False

    def download_file(self, remote_path: str) -> bytes | None:
        return None

    def upload_file(self, local_path: str, filename: str) -> bool:
        return False

    # =========================================================================
    # Camera
    # =========================================================================

    def get_stream_url(self) -> str | None:
        return f"rtsps://bblp:{self._access_code}@{self._ip_address}:322/streaming/live/1"

    def get_snapshot(self) -> bytes | None:
        return None

    # =========================================================================
    # G-code Console
    # =========================================================================

    def send_gcode(self, gcode: str) -> bool:
        return self._client.send_gcode(gcode)

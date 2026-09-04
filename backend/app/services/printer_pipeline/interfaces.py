"""Feature Pipeline interface definitions.

Defines the clean, modular interfaces for all 3D printer integrations in PrintHive.
Printer adapters implement these interfaces to provide their native functionality,
while routes and orchestration services interact strictly through standard contracts.
"""

from abc import ABC, abstractmethod
from typing import Callable, Any, AsyncIterator

from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_pipeline.capabilities import PrinterCapabilities, PrinterCapability
from backend.app.services.printer_pipeline.models import (
    JogRequest,
    FileInfo,
    StorageInfo,
    MultiMaterialSlot,
    StandardPrintJobRequest,
)


class ITelemetryProvider(ABC):
    """Provides real-time printer telemetry and connection status."""

    @abstractmethod
    def get_state(self) -> PrinterState:
        """Return the current normalized printer state snapshot."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if connection to the printer is currently active."""
        pass

    @abstractmethod
    def request_status_update(self) -> bool:
        """Request an immediate status refresh from the printer."""
        pass


class IPrintJobControl(ABC):
    """Controls print job lifecycle (start, pause, resume, stop, skip objects)."""

    @abstractmethod
    def start_print(self, request: StandardPrintJobRequest) -> bool:
        """Start printing a file with specified plate and calibration options."""
        pass

    @abstractmethod
    def pause_print(self) -> bool:
        """Pause the active print job."""
        pass

    @abstractmethod
    def resume_print(self) -> bool:
        """Resume a paused print job."""
        pass

    @abstractmethod
    def stop_print(self) -> bool:
        """Cancel/abort the active print job."""
        pass

    def skip_objects(self, object_ids: list[int]) -> bool:
        """Skip specific object IDs in the active print (default no-op)."""
        return False


class IMotionControl(ABC):
    """Controls printer toolhead and bed motion."""

    @abstractmethod
    def home(self, axes: str = "XYZ") -> bool:
        """Home specified axes or full homing sequence."""
        pass

    @abstractmethod
    def jog(self, request: JogRequest) -> bool:
        """Execute relative jog movement."""
        pass

    @abstractmethod
    def disable_motors(self) -> bool:
        """De-energize stepper motors."""
        pass

    @abstractmethod
    def enable_motors(self) -> bool:
        """Energize stepper motors."""
        pass


class IThermalControl(ABC):
    """Controls heaters (bed, nozzle, heated chamber)."""

    @abstractmethod
    def set_bed_temperature(self, target_c: int) -> bool:
        """Set the heated bed target temperature."""
        pass

    @abstractmethod
    def set_nozzle_temperature(self, target_c: int, nozzle: int = 0) -> bool:
        """Set the nozzle target temperature."""
        pass

    def set_chamber_temperature(self, target_c: int) -> bool:
        """Set the active chamber heater target temperature (optional)."""
        return False


class IFanControl(ABC):
    """Controls cooling and ventilation fans."""

    @abstractmethod
    def set_part_fan(self, speed: int) -> bool:
        """Set part cooling fan speed (0-255)."""
        pass

    @abstractmethod
    def set_aux_fan(self, speed: int) -> bool:
        """Set auxiliary cooling fan speed (0-255)."""
        pass

    @abstractmethod
    def set_chamber_fan(self, speed: int) -> bool:
        """Set chamber circulation or exhaust fan speed (0-255)."""
        pass

    @abstractmethod
    def set_fan_speed(self, fan_index: int, speed: int) -> bool:
        """Set fan speed by numeric index (1=part, 2=aux, 3=chamber)."""
        pass


class ILightControl(ABC):
    """Controls chamber illumination."""

    @abstractmethod
    def set_chamber_light(self, on: bool) -> bool:
        """Turn chamber LED illumination on or off."""
        pass


class IFileStorageControl(ABC):
    """Manages file storage, uploads, downloads, and disk capacity on the printer."""

    @abstractmethod
    def list_files(self, path: str = "/") -> list[FileInfo]:
        """List files currently stored on the printer at the given path."""
        pass

    @abstractmethod
    def get_storage_info(self) -> StorageInfo | None:
        """Query total and available storage capacity on the printer."""
        pass

    @abstractmethod
    def delete_file(self, remote_path: str) -> bool:
        """Delete a file from printer storage."""
        pass

    @abstractmethod
    def download_file(self, remote_path: str) -> bytes | None:
        """Download file content from printer storage."""
        pass

    @abstractmethod
    def upload_file(self, local_path: str, filename: str) -> bool:
        """Upload a file to printer storage."""
        pass


class IMultiMaterialControl(ABC):
    """Controls multi-material feeder units (AMS / multi-color slots) and drying."""

    @abstractmethod
    def get_slots(self) -> list[MultiMaterialSlot]:
        """Return list of configured multi-material slots."""
        pass

    @abstractmethod
    def load_slot(self, slot_id: int) -> bool:
        """Load filament from a specific slot."""
        pass

    @abstractmethod
    def unload_slot(self) -> bool:
        """Unload filament from current extruder back to feeder."""
        pass

    def send_drying_command(
        self, ams_id: int, temp: int, duration: int, mode: int = 1, filament: str = "", rotate_tray: bool = False
    ) -> bool:
        """Send AMS drying start/stop command (optional)."""
        return False


class ICameraProvider(ABC):
    """Provides camera stream URLs and snapshots."""

    @abstractmethod
    def get_stream_url(self) -> str | None:
        """Return RTSP / WebRTC / HTTP MJPEG URL for camera streaming."""
        pass

    @abstractmethod
    def get_snapshot(self) -> bytes | None:
        """Fetch a single JPEG snapshot from the chamber camera."""
        pass


class IGcodeConsole(ABC):
    """Executes direct G-code commands or translates standard G-codes for the printer."""

    @abstractmethod
    def send_gcode(self, gcode: str) -> bool:
        """Send raw or translated G-code command(s) to the printer."""
        pass


class IPrinterAdapter(
    ITelemetryProvider,
    IPrintJobControl,
    IMotionControl,
    IThermalControl,
    IFanControl,
    ILightControl,
    IFileStorageControl,
    IMultiMaterialControl,
    ICameraProvider,
    IGcodeConsole,
    ABC,
):
    """Unified printer adapter interface.

    Combines all feature capabilities under one clean contract.
    Adapters implement the capabilities supported by their hardware and protocol,
    declaring what they support via the `capabilities` property.
    """

    @property
    @abstractmethod
    def capabilities(self) -> PrinterCapabilities:
        """Return the capabilities manifest for this printer."""
        pass

    @property
    @abstractmethod
    def printer_id(self) -> int:
        """Unique ID of the printer within PrintHive."""
        pass

    @property
    @abstractmethod
    def model(self) -> str:
        """Hardware model name (e.g. 'X1C', 'Centauri Carbon', 'Adventurer 5M')."""
        pass

    @property
    @abstractmethod
    def serial_number(self) -> str:
        """Printer serial number or hardware identifier."""
        pass

    @property
    @abstractmethod
    def ip_address(self) -> str:
        """Printer network IP address."""
        pass

    @abstractmethod
    def connect(self, loop=None) -> None:
        """Establish network connection and start telemetry polling/subscription."""
        pass

    @abstractmethod
    def disconnect(self, timeout: float = 0) -> None:
        """Disconnect and release network resources."""
        pass

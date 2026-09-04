"""Normalized data models for the PrintHive Feature Pipeline.

Provides standard domain types so higher-level services and API endpoints
never interact with raw vendor JSON schemas or bespoke dict formats.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MotionAxis(str, Enum):
    """Standard motion axes."""
    X = "X"
    Y = "Y"
    Z = "Z"
    E = "E"


@dataclass
class JogRequest:
    """Request to jog a toolhead or bed along a specific axis."""
    axis: MotionAxis | str
    distance_mm: float
    speed_mm_min: int = 3000

    def __post_init__(self):
        if isinstance(self.axis, str):
            self.axis = MotionAxis(self.axis.upper())


@dataclass
class FileInfo:
    """Standardized metadata for a file stored on or accessible by the printer."""
    name: str
    path: str
    size_bytes: int = 0
    modified_timestamp: float | None = None
    thumbnail_url: str | None = None
    layer_height: float | None = None
    filament_type: str | None = None
    print_time_seconds: int | None = None


@dataclass
class StorageInfo:
    """Storage capacity information for local printer filesystem or SD card."""
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0

    @property
    def free_mb(self) -> float:
        return self.free_bytes / (1024 * 1024)

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)

    @property
    def used_pct(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return (self.used_bytes / self.total_bytes) * 100.0


@dataclass
class MultiMaterialSlot:
    """Normalized multi-material slot or AMS tray description."""
    slot_id: int
    name: str | None = None
    color_hex: str = "FFFFFF"
    filament_type: str = "PLA"
    remain_pct: int | None = None
    temp_min: int | None = None
    temp_max: int | None = None
    is_active: bool = False
    rfid: str | None = None
    brand: str | None = None


@dataclass
class StandardPrintJobRequest:
    """Standard parameters for dispatching a print job to any printer adapter."""
    filename: str
    plate_id: int = 1
    ams_mapping: list[int] | None = None
    use_ams: bool = True
    bed_type: str | int | None = None
    timelapse: bool = False
    bed_levelling: str = "auto"
    flow_cali: str = "auto"
    vibration_cali: bool = True
    layer_inspect: bool = False
    nozzle_offset_cali: str = "auto"
    nozzle_mapping: str | None = None

"""Printer capabilities system for PrintHive Feature Pipeline.

Allows feature-based querying so routes and services don't rely on
hardcoded manufacturer names or fragile model checks.
"""

from enum import Flag, auto
from dataclasses import dataclass, field


class PrinterCapability(Flag):
    """Flags denoting specific capabilities supported by a printer or adapter."""
    NONE = 0
    
    # Core Telemetry & Job Lifecycle
    TELEMETRY = auto()
    PRINT_CONTROL = auto()          # pause, resume, stop
    START_PRINT = auto()            # start job with file and plate options
    
    # Motion & Positioning
    MOTION_HOME = auto()            # home all or individual axes
    MOTION_JOG = auto()             # manual relative jog (X, Y, Z)
    MOTOR_CONTROL = auto()          # enable/disable stepper motors
    
    # Thermal & Environmental
    HEATED_BED = auto()             # bed temperature control
    EXTRUDER_HEATER = auto()        # nozzle temperature control
    CHAMBER_HEATER = auto()         # active chamber heating
    CHAMBER_SENSOR = auto()         # chamber temperature sensor readout
    
    # Fans
    FAN_PART = auto()               # part cooling fan
    FAN_AUX = auto()                # auxiliary chamber blower
    FAN_CHAMBER = auto()            # chamber exhaust/recirculation fan
    
    # Lighting
    CHAMBER_LIGHT = auto()          # chamber LED on/off
    
    # Multi-Material & AMS
    MULTI_MATERIAL = auto()         # multi-material unit (AMS / slot changer)
    DRYING_REMOTE = auto()          # remote drying start/stop control
    DRYING_SCREEN_ONLY = auto()     # drying supported only via printer touchscreen
    DRYING_WHILE_PRINTING = auto()  # concurrent drying and printing
    
    # Storage & Files
    STORAGE_LOCAL = auto()          # query internal storage capacity
    FILE_LIST = auto()              # list remote files on printer
    FILE_UPLOAD = auto()            # upload print jobs directly
    FILE_DOWNLOAD = auto()          # download G-code / 3MF from printer
    FILE_DELETE = auto()            # delete files from printer
    
    # Console & Scripting
    GCODE_CONSOLE = auto()          # terminal G-code execution or emulation
    
    # Vision & Camera
    CAMERA_STREAM = auto()          # live video stream (RTSP, WebRTC, MJPEG)
    CAMERA_SNAPSHOT = auto()        # static snapshot / photo moment
    
    # Advanced Print Features
    SKIP_OBJECTS = auto()           # skip specific objects during print
    PRINT_SPEED_MODE = auto()       # speed level modes (silent, standard, sport, ludicrous)


@dataclass
class PrinterCapabilities:
    """Convenience container wrapping capability flags and model-specific metadata."""
    flags: PrinterCapability = PrinterCapability.NONE
    max_nozzles: int = 1
    max_slots: int = 1
    supported_axes: tuple[str, ...] = ("X", "Y", "Z")

    def has(self, capability: PrinterCapability) -> bool:
        """Check if a specific capability flag is present."""
        return bool(self.flags & capability)

    def add(self, capability: PrinterCapability) -> None:
        """Add a capability flag."""
        self.flags |= capability

    def remove(self, capability: PrinterCapability) -> None:
        """Remove a capability flag."""
        self.flags &= ~capability

"""Domain model of the Smart Bin (Ubiquitous Language, see arc42 chapter 12).

Pure data, no technology. Python >= 3.8 compatible.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Fraction(str, Enum):
    """Waste fraction of a bin."""
    PET = "PET"
    ALU = "ALU"
    REST = "REST"


class Direction(str, Enum):
    """Where a module's flap moves the item."""
    DIREKT = "DIREKT"   # into the module's own bin
    LINKS = "LINKS"     # towards the left neighbour
    RECHTS = "RECHTS"   # towards the right neighbour
    HALTEN = "HALTEN"   # keep the item on the flap (return to user)


class Signal(str, Enum):
    """LED pattern shown to the user."""
    AUS = "AUS"
    OK = "OK"               # green, short
    FEHLWURF = "FEHLWURF"   # red, blinking
    UNBEKANNT = "UNBEKANNT" # yellow
    STOERUNG = "STOERUNG"   # red, steady


class Outcome(str, Enum):
    """Result of one Vorgang, stored in the statistics."""
    KORREKT = "KORREKT"
    FEHLWURF_UMGELENKT = "FEHLWURF_UMGELENKT"
    FEHLWURF_NICHT_UMLENKBAR = "FEHLWURF_NICHT_UMLENKBAR"
    UNBEKANNT = "UNBEKANNT"
    ABGEBROCHEN = "ABGEBROCHEN"


class Routing(str, Enum):
    """Mechanical concept for misthrows (open point in the architecture doc).

    NEIGHBOUR_BIN: tilting left/right drops the item directly into the
                   neighbour's bin. Non-adjacent bins are unreachable.
    VIA_FLAPS:     tilting left/right hands the item to the neighbour's flap,
                   which then tilts on. Every bin is reachable, but each hop
                   costs time.
    """
    NEIGHBOUR_BIN = "neighbour_bin"
    VIA_FLAPS = "via_flaps"


@dataclass(frozen=True)
class Classification:
    """Result of the classifier. fraction None means UNBEKANNT."""
    fraction: Optional[Fraction]
    confidence: float


@dataclass(frozen=True)
class ModuleConfig:
    module_id: int
    fraction: Fraction
    position: int          # 0 = leftmost
    port: str = ""         # serial port on the Jetson, unused in simulation


@dataclass(frozen=True)
class Timeouts:
    image_s: float = 2.0          # ITEM_DETECTED -> IMAGE
    sort_s: float = 6.0           # SORT -> SORT_DONE (incl. waiting for PIR)
    transit_s: float = 3.0        # item handed over -> ITEM_DETECTED at neighbour
    heartbeat_interval_s: float = 1.0
    heartbeat_timeout_s: float = 3.0


@dataclass(frozen=True)
class StationConfig:
    modules: List[ModuleConfig]
    confidence_threshold: float = 0.6
    routing: Routing = Routing.NEIGHBOUR_BIN
    timeouts: Timeouts = field(default_factory=Timeouts)

    def module(self, module_id: int) -> ModuleConfig:
        for m in self.modules:
            if m.module_id == module_id:
                return m
        raise KeyError("unknown module %r" % module_id)

    def module_for(self, fraction: Fraction) -> Optional[ModuleConfig]:
        for m in self.modules:
            if m.fraction == fraction:
                return m
        return None

    def module_at(self, position: int) -> Optional[ModuleConfig]:
        for m in self.modules:
            if m.position == position:
                return m
        return None


@dataclass(frozen=True)
class ThrowEvent:
    """One row in the statistics table `einwurf`. No personal data."""
    timestamp: float               # seconds since epoch (or sim time)
    vorgang_id: str                # "<module>-<seq>", correlation id
    module_id: int
    chosen: Fraction
    detected: Optional[Fraction]
    confidence: float
    outcome: Outcome
    duration_ms: int               # ITEM_DETECTED -> finished
    feedback_ms: Optional[int]     # ITEM_DETECTED -> SIGNAL sent

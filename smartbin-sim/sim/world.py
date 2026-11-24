"""Things that exist in the simulated world: items and their (fake) photos."""
import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from smartbin.core.model import Fraction

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
MAGIC = b"SIMITEM"


@dataclass
class Item:
    item_id: int
    true: Fraction
    t_throw: float = 0.0
    chosen_module: int = -1
    module_idle_at_throw: bool = True      # module BEREIT with empty flap
    vorgang_id: Optional[str] = None       # set at first detection
    t_led: Optional[float] = None          # first LED signal at origin
    led_signal: Optional[str] = None
    final: Optional[str] = None            # "BIN:<fraction>", "RETURNED", None = still on a flap
    t_final: Optional[float] = None
    path: List[Tuple[float, str]] = field(default_factory=list)


def fake_jpeg(item: Item, size: int) -> bytes:
    """A stand-in photo: carries the ground truth so the fake classifier can
    simulate a given accuracy. Padded to a realistic JPEG size so the serial
    transfer time is realistic."""
    meta = json.dumps({"id": item.item_id, "true": item.true.value}).encode()
    body = JPEG_SOI + MAGIC + len(meta).to_bytes(2, "little") + meta
    pad = max(0, size - len(body) - len(JPEG_EOI))
    return body + bytes(pad) + JPEG_EOI


def parse_fake_jpeg(data: bytes) -> Tuple[int, Fraction]:
    if not data.startswith(JPEG_SOI + MAGIC):
        raise ValueError("not a simulated image")
    n = int.from_bytes(data[9:11], "little")
    meta = json.loads(data[11:11 + n].decode())
    return int(meta["id"]), Fraction(meta["true"])

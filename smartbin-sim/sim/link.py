"""Simulated USB-serial line: bytes arrive after len*10/baud seconds (8N1),
one frame after another. Optional fault injection: corrupted bytes."""
import random
from typing import Callable, Optional

from .engine import Sim


class SimLink:
    def __init__(self, sim: Sim, baud: int, deliver: Callable[[bytes], None],
                 corrupt_rate: float = 0.0, rng: Optional[random.Random] = None) -> None:
        self.sim = sim
        self.baud = baud
        self.deliver = deliver
        self.corrupt_rate = corrupt_rate
        self.rng = rng or random.Random(0)
        self._busy_until = 0.0
        self.bytes_sent = 0
        self.corrupted = 0

    def write(self, data: bytes) -> None:
        start = max(self.sim.now, self._busy_until)
        self._busy_until = start + len(data) * 10.0 / self.baud
        self.bytes_sent += len(data)
        if self.corrupt_rate and self.rng.random() < self.corrupt_rate and data:
            buf = bytearray(data)
            i = self.rng.randrange(len(buf))
            buf[i] ^= 0xFF
            data = bytes(buf)
            self.corrupted += 1
        self.sim.at(self._busy_until, self.deliver, data)

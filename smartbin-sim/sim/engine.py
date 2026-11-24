"""Discrete-event simulation kernel: a clock plus a time-ordered event queue.

Simulated time jumps from event to event, so 1000 throws (~2 hours of station
time) run in a few seconds and every run with the same seed is identical.
"""
import heapq
import itertools
from typing import Any, Callable, List, Optional, Tuple


class Sim:
    def __init__(self) -> None:
        self.now = 0.0
        self._queue: List[Tuple[float, int, Callable[..., Any], tuple]] = []
        self._counter = itertools.count()
        self.on_step: Optional[Callable[[], None]] = None  # observer, e.g. sim/trace.py

    def at(self, t: float, fn: Callable[..., Any], *args: Any) -> None:
        heapq.heappush(self._queue, (max(t, self.now), next(self._counter), fn, args))

    def after(self, dt: float, fn: Callable[..., Any], *args: Any) -> None:
        self.at(self.now + dt, fn, *args)

    def every(self, dt: float, fn: Callable[[], Any]) -> None:
        def tick() -> None:
            fn()
            self.after(dt, tick)
        self.after(dt, tick)

    def run_until(self, t_end: float) -> None:
        while self._queue and self._queue[0][0] <= t_end:
            t, _, fn, args = heapq.heappop(self._queue)
            self.now = t
            fn(*args)
            if self.on_step is not None:
                self.on_step()
        self.now = t_end

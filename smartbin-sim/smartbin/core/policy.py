"""Fehlwurf-Entscheid: compares chosen and detected fraction and plans the route."""
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Tuple

from .model import (Classification, Direction, Fraction, ModuleConfig, Outcome,
                    Routing, Signal, StationConfig)

Hop = Tuple[int, Direction]  # (module_id that moves, direction)


@dataclass(frozen=True)
class Decision:
    detected: Optional[Fraction]   # after confidence threshold
    target: Optional[Fraction]     # bin the item should end in (None = hold)
    signal: Signal
    route: List[Hop]
    outcome: Outcome               # outcome if the route completes


class MisthrowPolicy:
    def __init__(self, config: StationConfig) -> None:
        self.config = config
        self._unavailable: FrozenSet[int] = frozenset()

    def decide(self, chosen_module_id: int, result: Classification,
               unavailable: Iterable[int] = ()) -> Decision:
        """`unavailable`: modules in STOERUNG or offline. With via_flaps an
        item is never handed to such a module."""
        self._unavailable = frozenset(unavailable)
        chosen = self.config.module(chosen_module_id)

        detected = result.fraction
        if detected is not None and result.confidence < self.config.confidence_threshold:
            detected = None
        if detected is not None and self.config.module_for(detected) is None:
            # e.g. ALU detected at a 2er station without ALU bin -> treat as REST
            detected = None

        if detected is None:
            rest = self.config.module_for(Fraction.REST)
            if rest is None:
                return Decision(None, None, Signal.UNBEKANNT,
                                [(chosen.module_id, Direction.HALTEN)], Outcome.UNBEKANNT)
            route = self._route(chosen, rest)
            if route is None:
                # REST not reachable: hold the item, user takes it out
                return Decision(None, None, Signal.UNBEKANNT,
                                [(chosen.module_id, Direction.HALTEN)], Outcome.UNBEKANNT)
            return Decision(None, Fraction.REST, Signal.UNBEKANNT, route, Outcome.UNBEKANNT)

        if detected == chosen.fraction:
            return Decision(detected, detected, Signal.OK,
                            [(chosen.module_id, Direction.DIREKT)], Outcome.KORREKT)

        target = self.config.module_for(detected)
        assert target is not None
        route = self._route(chosen, target)
        if route is None:
            # Misthrow detected but mechanically unreachable: tell the user,
            # drop it into the chosen bin (it would have landed there anyway).
            return Decision(detected, chosen.fraction, Signal.FEHLWURF,
                            [(chosen.module_id, Direction.DIREKT)],
                            Outcome.FEHLWURF_NICHT_UMLENKBAR)
        return Decision(detected, detected, Signal.FEHLWURF, route, Outcome.FEHLWURF_UMGELENKT)

    def _route(self, start: ModuleConfig, target: ModuleConfig) -> Optional[List[Hop]]:
        if start.module_id == target.module_id:
            return [(start.module_id, Direction.DIREKT)]
        step = 1 if target.position > start.position else -1
        direction = Direction.RECHTS if step == 1 else Direction.LINKS
        distance = abs(target.position - start.position)

        if self.config.routing == Routing.NEIGHBOUR_BIN:
            if distance == 1:
                return [(start.module_id, direction)]
            return None

        # VIA_FLAPS: hand over flap by flap, the target module drops it DIREKT
        hops: List[Hop] = []
        pos = start.position
        while pos != target.position:
            module = self.config.module_at(pos)
            if module is None:
                return None
            if pos != start.position and module.module_id in self._unavailable:
                return None
            hops.append((module.module_id, direction))
            pos += step
        if target.module_id in self._unavailable:
            return None
        hops.append((target.module_id, Direction.DIREKT))
        return hops

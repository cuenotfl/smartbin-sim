"""Einwurf-Ablauf: the core use case (arc42 chapter 6).

Event driven and single threaded: adapters call the on_* methods, the core
answers through the ModulePort. Time is passed in explicitly (`now`, seconds),
so the same code runs in real time on the Jetson and in simulated time.

One Vorgang = one item, from the first ITEM_DETECTED until it lies in a bin.
With routing via_flaps an item visits several modules; every visit is an
`Entry` keyed by (module_id, that module's seq).
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

from .model import (Classification, Fraction, Outcome, StationConfig,
                    ThrowEvent)
from .policy import Decision, MisthrowPolicy
from .ports import ClassifierPort, EventStorePort, ModulePort

log = logging.getLogger("smartbin.core")

SORT_RESULT_OK = 0
SORT_RESULT_HELD = 1


class Phase(Enum):
    WAIT_IMAGE = "WAIT_IMAGE"
    WAIT_SORT_DONE = "WAIT_SORT_DONE"


@dataclass
class Vorgang:
    vid: str
    origin_module: int
    chosen: Fraction
    t_start: float
    classification: Optional[Classification] = None
    decision: Optional[Decision] = None
    t_feedback: Optional[float] = None
    hop: int = 0                     # index in decision.route reached so far
    finished: bool = False


@dataclass
class Entry:
    vorgang: Vorgang
    hop: int
    phase: Phase
    deadline: float


@dataclass
class Transit:
    vorgang: Vorgang
    hop: int                         # hop index the receiving module will execute
    deadline: float


@dataclass
class ModuleStatus:
    online: bool = False
    faulted: bool = False
    last_seen: float = 0.0


class SortingService:
    def __init__(self, config: StationConfig, classifier: ClassifierPort,
                 modules: ModulePort, store: EventStorePort) -> None:
        self.config = config
        self.policy = MisthrowPolicy(config)
        self.classifier = classifier
        self.modules = modules
        self.store = store
        self.active: Dict[Tuple[int, int], Entry] = {}
        self.in_transit: Dict[int, Deque[Transit]] = {m.module_id: deque() for m in config.modules}
        self.status: Dict[int, ModuleStatus] = {m.module_id: ModuleStatus() for m in config.modules}
        self._next_heartbeat = 0.0

    # ------------------------------------------------------------ events in
    def on_hello(self, module_id: int, now: float) -> None:
        st = self.status[module_id]
        st.online, st.last_seen = True, now
        log.info("module %d online, sending CONFIG", module_id)
        self.modules.send_config(module_id)

    def on_heartbeat(self, module_id: int, now: float, faulted: Optional[bool] = None,
                     last_done: Optional[Tuple[int, int]] = None) -> None:
        """The module's heartbeat carries its state and its last finished
        SORT, so a lost FAULT or SORT_DONE frame is recovered within 1 s."""
        self._touch(module_id, now)
        if faulted is not None:
            if faulted and not self.status[module_id].faulted:
                self.on_fault(module_id, 0, 0, now)
            elif not faulted and self.status[module_id].faulted:
                log.info("module %d reports healthy again", module_id)
                self.status[module_id].faulted = False
        if last_done is not None:
            seq, result = last_done
            e = self.active.get((module_id, seq))
            if e is not None and e.phase == Phase.WAIT_SORT_DONE:
                self.on_sort_done(module_id, seq, result, now)

    def on_item_detected(self, module_id: int, seq: int, now: float,
                         via_handover: bool = False) -> None:
        self._touch(module_id, now)
        known = self.active.get((module_id, seq))
        if known is not None:  # retransmission by the module
            if known.phase == Phase.WAIT_SORT_DONE:
                self._resend(known, module_id, seq)
            return
        transit = self.in_transit[module_id]
        if via_handover and transit:
            t = transit.popleft()
            v = t.vorgang
            if v.finished:
                return
            log.info("[%s] arrived at module %d", v.vid, module_id)
            self._execute_hop(v, module_id, seq, t.hop, now)
            return

        chosen = self.config.module(module_id).fraction
        v = Vorgang(vid="%d-%d" % (module_id, seq), origin_module=module_id,
                    chosen=chosen, t_start=now)
        self.active[(module_id, seq)] = Entry(v, 0, Phase.WAIT_IMAGE,
                                              now + self.config.timeouts.image_s)
        log.info("[%s] item detected in %s opening%s", v.vid, chosen.value,
                 " (unexpected handover)" if via_handover else "")

    def on_image(self, module_id: int, seq: int, image: bytes, now: float) -> None:
        self._touch(module_id, now)
        e = self.active.get((module_id, seq))
        if e is None:
            return  # unknown item (its ITEM_DETECTED was lost; the module retries)
        if e.phase == Phase.WAIT_SORT_DONE:
            self._resend(e, module_id, seq)  # module did not get SIGNAL/SORT
            return
        v = e.vorgang
        try:
            result = self.classifier.classify(image)
        except Exception:  # unreadable image etc. -> treat as UNBEKANNT
            log.exception("[%s] classification failed", v.vid)
            result = Classification(None, 0.0)
        unavailable = [mid for mid, st in self.status.items() if st.faulted or not st.online]
        decision = self.policy.decide(module_id, result, unavailable)
        v.classification, v.decision, v.t_feedback = result, decision, now
        log.info("[%s] detected=%s conf=%.2f -> %s, route=%s", v.vid,
                 decision.detected.value if decision.detected else "UNBEKANNT",
                 result.confidence, decision.signal.value,
                 [(m, d.value) for m, d in decision.route])
        self.modules.send_signal(module_id, seq, decision.signal)
        self._execute_hop(v, module_id, seq, 0, now)

    def on_sort_done(self, module_id: int, seq: int, result: int, now: float) -> None:
        self._touch(module_id, now)
        e = self.active.pop((module_id, seq), None)
        if e is None or e.vorgang.finished:
            return
        v = e.vorgang
        if result not in (SORT_RESULT_OK, SORT_RESULT_HELD):
            self._finish(v, Outcome.ABGEBROCHEN, now)
            return
        assert v.decision is not None
        if e.hop == len(v.decision.route) - 1:
            self._finish(v, v.decision.outcome, now)
        # otherwise the item is on its way to the next flap (Transit)

    def on_fault(self, module_id: int, seq: int, code: int, now: float) -> None:
        self._touch(module_id, now)
        self.status[module_id].faulted = True
        log.warning("module %d FAULT code=%d", module_id, code)
        self._abort_module(module_id, now)

    # ------------------------------------------------------------ commands
    def reset_fault(self, module_id: int) -> None:
        """Called by the Hausdienst (button or dashboard)."""
        self.status[module_id].faulted = False
        self.modules.send_reset_fault(module_id)

    def tick(self, now: float) -> None:
        t = self.config.timeouts
        if now >= self._next_heartbeat:
            for mid, st in self.status.items():
                if st.online:
                    self.modules.send_heartbeat(mid)
            self._next_heartbeat = now + t.heartbeat_interval_s

        for mid, st in self.status.items():
            if st.online and now - st.last_seen > t.heartbeat_timeout_s:
                st.online = False
                log.warning("module %d lost (no heartbeat)", mid)
                self._abort_module(mid, now)

        for key, e in list(self.active.items()):
            if now > e.deadline:
                del self.active[key]
                if not e.vorgang.finished:
                    log.warning("[%s] timeout in %s at module %d", e.vorgang.vid, e.phase.value, key[0])
                    self._finish(e.vorgang, Outcome.ABGEBROCHEN, now)
        for mid, queue in self.in_transit.items():
            while queue and now > queue[0].deadline:
                tr = queue.popleft()
                if not tr.vorgang.finished:
                    log.warning("[%s] item never arrived at module %d", tr.vorgang.vid, mid)
                    self._finish(tr.vorgang, Outcome.ABGEBROCHEN, now)

    # ------------------------------------------------------------ helpers
    def _execute_hop(self, v: Vorgang, module_id: int, seq: int, hop: int, now: float) -> None:
        assert v.decision is not None
        hop_module, direction = v.decision.route[hop]
        assert hop_module == module_id, "route and physical position disagree"
        v.hop = hop
        t = self.config.timeouts
        self.active[(module_id, seq)] = Entry(v, hop, Phase.WAIT_SORT_DONE, now + t.sort_s)
        if hop + 1 < len(v.decision.route):
            # expect the item on the next flap before we even get SORT_DONE:
            # it may arrive there earlier than the SORT_DONE reaches us.
            nxt = v.decision.route[hop + 1][0]
            self.in_transit[nxt].append(Transit(v, hop + 1, now + t.sort_s + t.transit_s))
        signal = v.decision.signal if hop == 0 else None
        self.modules.send_sort(module_id, seq, direction, signal)

    def _resend(self, e: Entry, module_id: int, seq: int) -> None:
        """Commands are idempotent per (module, seq): sending again is safe."""
        v = e.vorgang
        assert v.decision is not None
        if e.hop == 0:
            self.modules.send_signal(module_id, seq, v.decision.signal)
        self.modules.send_sort(module_id, seq, v.decision.route[e.hop][1],
                               v.decision.signal if e.hop == 0 else None)

    def _touch(self, module_id: int, now: float) -> None:
        st = self.status[module_id]
        st.last_seen = now
        st.online = True

    def _abort_module(self, module_id: int, now: float) -> None:
        for key, e in list(self.active.items()):
            if key[0] == module_id:
                del self.active[key]
                if not e.vorgang.finished:
                    self._finish(e.vorgang, Outcome.ABGEBROCHEN, now)
        queue = self.in_transit[module_id]
        while queue:
            tr = queue.popleft()
            if not tr.vorgang.finished:
                self._finish(tr.vorgang, Outcome.ABGEBROCHEN, now)

    def _finish(self, v: Vorgang, outcome: Outcome, now: float) -> None:
        v.finished = True
        cls = v.classification
        event = ThrowEvent(
            timestamp=v.t_start,
            vorgang_id=v.vid,
            module_id=v.origin_module,
            chosen=v.chosen,
            detected=v.decision.detected if v.decision else None,
            confidence=cls.confidence if cls else 0.0,
            outcome=outcome,
            duration_ms=int(round((now - v.t_start) * 1000)),
            feedback_ms=int(round((v.t_feedback - v.t_start) * 1000)) if v.t_feedback is not None else None,
        )
        log.info("[%s] finished: %s", v.vid, outcome.value)
        self.store.save(event)

    def open_vorgaenge(self) -> List[Vorgang]:
        found = {id(e.vorgang): e.vorgang for e in self.active.values() if not e.vorgang.finished}
        for q in self.in_transit.values():
            for tr in q:
                if not tr.vorgang.finished:
                    found[id(tr.vorgang)] = tr.vorgang
        return list(found.values())

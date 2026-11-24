"""Records what happens in a simulated station as a stream of events for the
3D viewer (sim/viewer.html, served by sim/live.py).

Observer only: it diffs module state, LED, PIR, Jetson load and items after
every simulation event, wraps the serial links and the statistics store, and
gets flap motion from the module twin (SimModule.trace_motion). It draws no
random numbers, so a recorded run is identical to an unrecorded one.

Event records (lists, JSON friendly; times in simulated seconds):

    ["state",  t, module, state]
    ["led",    t, module, led]
    ["pir",    t, module, until]
    ["motion", t0, module, t1, from, to, direction]   flap 0 = rest, 1 = fully tilted
    ["frame",  t0, t1, module, "up"|"down", type, seq, info, corrupted]
    ["infer",  t0, t1]
    ["fault",  t, module, code]
    ["item",   t, {...}]                                 full item snapshot on change
"""
from typing import Any, Dict, List, Optional

from smartbin.adapters import protocol as p
from smartbin.core.model import ThrowEvent

from .station import PhysicalStation
from .world import Item


def _r(t: float) -> float:
    return round(t, 4)


def _frame_info(mtype: int, payload: bytes, up: bool) -> str:
    try:
        if mtype == p.MsgType.IMAGE:
            return "%.1f KB" % (len(payload) / 1000.0)
        if mtype == p.MsgType.SORT:
            s = p.DIRECTION_NAMES[payload[0]]
            if len(payload) > 1 and payload[1]:
                s += " + " + p.SIGNAL_NAMES[payload[1]]
            return s
        if mtype == p.MsgType.SIGNAL:
            return p.SIGNAL_NAMES[payload[0]]
        if mtype == p.MsgType.FAULT:
            return p.FaultCode(payload[0]).name
        if mtype == p.MsgType.SORT_DONE:
            return {0: "ok", 1: "gehalten"}.get(payload[0], "fehler %d" % payload[0])
        if mtype == p.MsgType.ITEM_DETECTED:
            return "Weitergabe" if payload[0] else "Oeffnung"
        if mtype == p.MsgType.CONFIG:
            return "%s pos %d" % (p.FRACTION_NAMES[payload[0]], payload[1])
        if mtype == p.MsgType.HEARTBEAT and up:
            return p.ModuleStateCode(payload[0]).name
    except (IndexError, KeyError, ValueError):
        pass
    return ""


class Recorder:
    def __init__(self, st: PhysicalStation, heartbeats: bool = False) -> None:
        self.st = st
        self.heartbeats = heartbeats
        self.events: List[list] = []
        self.inputs: Dict[int, Dict[str, Any]] = {}   # item id -> what the user entered
        self._state: Dict[int, str] = {}
        self._led: Dict[int, str] = {}
        self._pir: Dict[int, float] = {}
        self._faults: Dict[int, int] = {}
        self._busy_until = 0.0
        self._results: Dict[str, ThrowEvent] = {}
        self._items_seen = 0
        self._open: Dict[int, Item] = {}
        self._snap: Dict[int, Any] = {}

        self._flap: Dict[int, List[float]] = {}      # module -> [t0, t1, from, to]
        self._flap_dir: Dict[int, str] = {}
        for mid, mod in st.modules.items():
            mod.trace_motion = (lambda d, to, dur, mid=mid: self._motion(mid, d, to, dur))
            self._flap[mid] = [0.0, 0.0, 0.0, 0.0]
            self._flap_dir[mid] = "DIREKT"
        # links were created as [down, up] per module, in config order
        for i, m in enumerate(st.config.modules):
            self._wrap_link(st.links[2 * i], m.module_id, "down")
            self._wrap_link(st.links[2 * i + 1], m.module_id, "up")
        save = st.store.save

        def recording_save(e: ThrowEvent) -> None:
            save(e)
            self._results[e.vorgang_id] = e
        st.store.save = recording_save  # type: ignore[method-assign]
        st.sim.on_step = self.step
        self.step()

    # ------------------------------------------------------------ hooks
    def _wrap_link(self, link: Any, module_id: int, direction: str) -> None:
        write = link.write

        def recording_write(data: bytes) -> None:
            t0 = max(self.st.sim.now, link._busy_until)
            before = link.corrupted
            write(data)
            mtype = data[1] if len(data) > 1 else 0
            if mtype == p.MsgType.HEARTBEAT and not self.heartbeats:
                return
            seq = int.from_bytes(data[2:4], "little") if len(data) >= 4 else 0
            payload = data[p.HEADER.size:-p.CRC.size]
            try:
                name = p.MsgType(mtype).name
            except ValueError:
                name = "0x%02X" % mtype
            self.events.append(["frame", _r(t0), _r(link._busy_until), module_id, direction, name,
                                seq, _frame_info(mtype, payload, direction == "up"),
                                link.corrupted != before])
        link.write = recording_write

    def _motion(self, mid: int, direction: Optional[str], to: float, dur: float) -> None:
        now = self.st.sim.now
        t0, t1, a, b = self._flap[mid]
        cur = b if now >= t1 or t1 <= t0 else a + (b - a) * (now - t0) / (t1 - t0)
        if direction is not None:
            self._flap_dir[mid] = direction
        self._flap[mid] = [now, now + dur, cur, to]
        self.events.append(["motion", _r(now), mid, _r(now + dur), round(cur, 3), to,
                            self._flap_dir[mid]])

    def mark_input(self, item_id: int, data: Dict[str, Any]) -> None:
        self.inputs[item_id] = data

    # ------------------------------------------------------------ diff after each event
    def step(self) -> None:
        st = self.st
        now = _r(st.sim.now)
        for mid, mod in st.modules.items():
            s = mod.state.value
            if self._state.get(mid) != s:
                self._state[mid] = s
                self.events.append(["state", now, mid, s])
            if self._led.get(mid) != mod.led:
                self._led[mid] = mod.led
                self.events.append(["led", now, mid, mod.led])
            until = st.pir_until[mid]
            if self._pir.get(mid) != until:
                self._pir[mid] = until
                if until > st.sim.now:
                    self.events.append(["pir", now, mid, _r(until)])
            nf = len(mod.faults)
            if self._faults.get(mid, 0) != nf:
                for t, code in mod.faults[self._faults.get(mid, 0):]:
                    self.events.append(["fault", _r(t), mid, p.FaultCode(code).name])
                self._faults[mid] = nf
        if st.node.busy_until != self._busy_until:
            self._busy_until = st.node.busy_until
            self.events.append(["infer", _r(st.node.busy_until - st.node.inference_s),
                                _r(st.node.busy_until)])
        while self._items_seen < len(st.items):
            it = st.items[self._items_seen]
            self._open[it.item_id] = it
            self._items_seen += 1
        for iid, it in list(self._open.items()):
            snap = self.item_dict(it)
            key = repr(snap)
            if self._snap.get(iid) != key:
                self._snap[iid] = key
                self.events.append(["item", now, snap])
            if snap["outcome"] is not None and it.final is not None:
                del self._open[iid]
                self._snap.pop(iid, None)

    def item_dict(self, it: Item) -> Dict[str, Any]:
        path = []
        last_flap = it.chosen_module
        for t, what in it.path:
            kind, arg = what.split(":", 1)
            if kind == "flap":
                last_flap = int(arg)
                path.append([_r(t), "flap", last_flap])
            elif kind == "thrown":
                path.append([_r(t), "thrown", int(arg)])
            elif kind == "BIN":
                path.append([_r(t), "bin", arg])
        if it.final == "RETURNED" and it.t_final is not None:
            path.append([_r(it.t_final), "returned", last_flap])
        e = self._results.get(it.vorgang_id or "")
        return {
            "id": it.item_id, "true": it.true.value, "chosen": it.chosen_module,
            "vid": it.vorgang_id, "t_throw": _r(it.t_throw), "path": path, "final": it.final,
            "t_led": None if it.t_led is None else _r(it.t_led), "led": it.led_signal,
            "detected": (e.detected.value if e.detected else "UNBEKANNT") if e else None,
            "confidence": round(e.confidence, 3) if e else None,
            "outcome": e.outcome.value if e else None,
            "input": self.inputs.get(it.item_id),
        }

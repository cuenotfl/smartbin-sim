"""Digital twin of the ESP32 module firmware (arc42 5.3 and 8.1).

Implements the same state machine and the same safety rules the C++ firmware
must implement, so it doubles as an executable specification for the
firmware. Speaks the real binary protocol over a simulated serial link.

    INIT -> BEREIT -> ERKANNT -> WARTEN -> SORTIEREN -> BEREIT
    ERKANNT / WARTEN / SORTIEREN --fault--> STOERUNG --RESET_FAULT--> BEREIT
"""
import random
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Deque, Dict, List, Optional, Tuple

from smartbin.adapters import protocol as p
from smartbin.core.model import Direction, Fraction

from .engine import Sim
from .world import Item, fake_jpeg

if TYPE_CHECKING:
    from .link import SimLink
    from .station import PhysicalStation


class State(Enum):
    INIT = "INIT"
    BEREIT = "BEREIT"
    ERKANNT = "ERKANNT"
    WARTEN = "WARTEN"
    SORTIEREN = "SORTIEREN"
    STOERUNG = "STOERUNG"


@dataclass
class ModuleParams:
    boot_s: float = 0.5
    capture_s: float = 0.15           # camera exposure + JPEG encoding
    image_bytes: int = 15000          # QVGA JPEG, assumption
    tilt_s: float = 0.6               # flap tilts
    return_s: float = 0.4             # flap back to rest position
    pir_recheck_s: float = 0.05
    pir_max_wait_s: float = 4.0       # hand in opening longer -> FAULT
    retry_s: float = 0.6              # no SORT yet -> resend ITEM_DETECTED + IMAGE
    sort_cmd_timeout_s: float = 4.0   # no SORT after ITEM_DETECTED -> FAULT
    heartbeat_interval_s: float = 1.0
    heartbeat_timeout_s: float = 3.0  # Jetson silent -> safe state
    p_blockade: float = 0.0           # probability that a tilt jams


class SimModule:
    def __init__(self, sim: Sim, module_id: int, params: ModuleParams, rng: random.Random) -> None:
        self.sim = sim
        self.module_id = module_id
        self.params = params
        self.rng = rng
        self.state = State.INIT
        self.fraction: Optional[Fraction] = None
        self.position: Optional[int] = None
        self.seq = 0
        self.flap: Deque[Item] = deque()
        self.handover_flags: Dict[int, bool] = {}
        self.led = "AUS"
        self.last_hb_rx = 0.0
        self.decoder = p.FrameDecoder()
        self.uplink: Optional["SimLink"] = None
        self.station: Optional["PhysicalStation"] = None
        self.faults: List[Tuple[float, int]] = []
        self.safety_stops = 0
        self.retries = 0
        self.state_log: List[Tuple[float, str]] = []
        self.powered = True
        self.last_done = (0, 0)           # (seq, result) of the last finished SORT

    # ------------------------------------------------------------ lifecycle
    def boot(self) -> None:
        self.sim.after(self.params.boot_s, self._send, p.MsgType.HELLO, 0,
                       bytes([self.module_id, 1, 0, p.PROTOCOL_VERSION]))
        self.sim.every(self.params.heartbeat_interval_s, self._heartbeat)

    def _heartbeat(self) -> None:
        if not self.powered:
            return
        code = p.ModuleStateCode[self.state.value]
        seq, result = self.last_done
        self._send(p.MsgType.HEARTBEAT, 0,
                   bytes([int(code)]) + seq.to_bytes(2, "little") + bytes([result]))
        if self.state == State.INIT and self.sim.now >= self.params.boot_s:
            # repeat HELLO until CONFIG arrives (lost HELLO/CONFIG, Jetson started later)
            self._send(p.MsgType.HELLO, 0, bytes([self.module_id, 1, 0, p.PROTOCOL_VERSION]))
        configured = self.state != State.INIT
        silent = self.sim.now - self.last_hb_rx > self.params.heartbeat_timeout_s
        if configured and silent and self.state != State.STOERUNG:
            self._fault(p.FaultCode.HEARTBEAT_LOST)

    def _set_state(self, s: State) -> None:
        self.state = s
        self.state_log.append((self.sim.now, s.value))

    def _send(self, mtype: p.MsgType, seq: int, payload: bytes = b"") -> None:
        assert self.uplink is not None
        self.uplink.write(p.encode(p.Frame(int(mtype), seq, payload)))

    # ------------------------------------------------------------ inbound frames
    def on_bytes(self, data: bytes) -> None:
        if not self.powered:
            return
        for f in self.decoder.feed(data):
            self._handle(f)

    def _handle(self, f: p.Frame) -> None:
        t = f.type
        if t == p.MsgType.HEARTBEAT:
            self.last_hb_rx = self.sim.now
        elif t == p.MsgType.CONFIG:
            self.fraction = Fraction(p.FRACTION_NAMES[f.payload[0]])
            self.position = f.payload[1]
            self.last_hb_rx = self.sim.now
            if self.state == State.INIT:
                self._set_state(State.BEREIT)
                self._try_next_item()
        elif t == p.MsgType.SIGNAL:
            self._show_signal(f.seq, f.payload[0])
        elif t == p.MsgType.SORT:
            if len(f.payload) >= 2 and f.payload[1]:
                self._show_signal(f.seq, f.payload[1])
            if f.seq == self.seq and self.state in (State.ERKANNT, State.WARTEN):
                self._start_sort(Direction(p.DIRECTION_NAMES[f.payload[0]]), self.seq)
        elif t == p.MsgType.RESET_FAULT:
            if self.state == State.STOERUNG:
                self.led = "AUS"
                self.last_hb_rx = self.sim.now
                self._set_state(State.BEREIT)
                self._try_next_item()
        # unknown types: ignore (Postel)

    def _show_signal(self, seq: int, code: int) -> None:
        if seq == self.seq and self.flap and self.state in (State.ERKANNT, State.WARTEN):
            self.led = p.SIGNAL_NAMES[code]
            item = self.flap[0]
            if item.t_led is None and item.chosen_module == self.module_id:
                item.t_led, item.led_signal = self.sim.now, self.led

    # ------------------------------------------------------------ item handling
    def receive_item(self, item: Item) -> None:
        """Called by the physical world: something landed on the flap.

        Firmware rule: an item that arrives while the PIR sees no hand came
        from the neighbour's flap (handover), not through the opening."""
        assert self.station is not None
        via_handover = not self.station.pir_active(self.module_id)
        item.path.append((self.sim.now, "flap:%d" % self.module_id))
        self.flap.append(item)
        self.handover_flags[id(item)] = via_handover
        if self.state == State.BEREIT and len(self.flap) == 1:
            self._detect()

    def _try_next_item(self) -> None:
        if self.state == State.BEREIT and self.flap:
            self._detect()

    def _detect(self) -> None:
        item = self.flap[0]
        self.seq = (self.seq + 1) & 0xFFFF
        seq = self.seq
        if item.vorgang_id is None:
            item.vorgang_id = "%d-%d" % (self.module_id, seq)
        self._set_state(State.ERKANNT)
        self.led = "AUS"
        source = 1 if self.handover_flags.get(id(item)) else 0
        self._send(p.MsgType.ITEM_DETECTED, seq, bytes([source]))
        self.sim.after(self.params.capture_s, self._image_captured, seq)
        self.sim.after(self.params.sort_cmd_timeout_s, self._sort_cmd_timeout, seq)

    def _image_captured(self, seq: int) -> None:
        if self.state != State.ERKANNT or seq != self.seq or not self.flap:
            return  # SORT already arrived (item in transit) or fault
        self._send(p.MsgType.IMAGE, seq, fake_jpeg(self.flap[0], self.params.image_bytes))
        self._set_state(State.WARTEN)
        self.sim.after(self.params.retry_s, self._retry, seq)

    def _retry(self, seq: int) -> None:
        """No SORT yet: a frame got lost somewhere. Report the item again."""
        if self.state != State.WARTEN or seq != self.seq or not self.flap:
            return
        self.retries += 1
        source = 1 if self.handover_flags.get(id(self.flap[0])) else 0
        self._send(p.MsgType.ITEM_DETECTED, seq, bytes([source]))
        self._send(p.MsgType.IMAGE, seq, fake_jpeg(self.flap[0], self.params.image_bytes))
        self.sim.after(self.params.retry_s, self._retry, seq)

    def _sort_cmd_timeout(self, seq: int) -> None:
        if seq == self.seq and self.state in (State.ERKANNT, State.WARTEN):
            self._fault(p.FaultCode.TIMEOUT_SORT_CMD)

    def _start_sort(self, direction: Direction, seq: int) -> None:
        if direction == Direction.HALTEN:
            item = self.flap.popleft()
            item.final, item.t_final = "RETURNED", self.sim.now
            self.last_done = (seq, 1)
            self._send(p.MsgType.SORT_DONE, seq, bytes([1]))
            self._set_state(State.BEREIT)
            self._try_next_item()
            return
        self._set_state(State.SORTIEREN)
        self._wait_pir_then(direction, seq, waited=0.0, phase=0)

    def _wait_pir_then(self, direction: Direction, seq: int, waited: float, phase: int) -> None:
        """Safety rule (ADR-003): move only while the PIR reports no hand."""
        if self.state != State.SORTIEREN or seq != self.seq:
            return
        assert self.station is not None
        if self.station.pir_active(self.module_id):
            if phase == 1 and waited == 0.0:
                self.safety_stops += 1
            if waited >= self.params.pir_max_wait_s:
                self._fault(p.FaultCode.PIR_BLOCKED)
                return
            self.sim.after(self.params.pir_recheck_s, self._wait_pir_then, direction, seq,
                           waited + self.params.pir_recheck_s, phase)
            return
        # move half of the tilt, then check the PIR again (safety stop in between)
        nxt = self._tilt_second_half if phase == 0 else self._tilt_done
        self.sim.after(self.params.tilt_s / 2, nxt, direction, seq)

    def _tilt_second_half(self, direction: Direction, seq: int) -> None:
        self._wait_pir_then(direction, seq, waited=0.0, phase=1)

    def _tilt_done(self, direction: Direction, seq: int) -> None:
        if self.state != State.SORTIEREN or seq != self.seq:
            return
        if self.rng.random() < self.params.p_blockade:
            self._fault(p.FaultCode.BLOCKADE)
            return
        item = self.flap.popleft()
        assert self.station is not None
        self.station.deliver(item, self.module_id, direction)
        self.sim.after(self.params.return_s, self._back_at_rest, seq)

    def _back_at_rest(self, seq: int) -> None:
        if self.state != State.SORTIEREN or seq != self.seq:
            return
        self.last_done = (seq, 0)
        self._send(p.MsgType.SORT_DONE, seq, bytes([0]))
        self._set_state(State.BEREIT)
        self._try_next_item()

    def _fault(self, code: int) -> None:
        """Safe state: servos to rest position, item stays on the flap."""
        self.faults.append((self.sim.now, int(code)))
        self._set_state(State.STOERUNG)
        self.led = "STOERUNG"
        self._send(p.MsgType.FAULT, self.seq, bytes([int(code), 5]))

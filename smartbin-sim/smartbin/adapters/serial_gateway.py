"""Serial-Gateway: driving adapter (frames in -> core events) and driven adapter
(ModulePort -> frames out). Transport-agnostic: real USB serial on the Jetson,
simulated links in the simulator.
"""
import logging
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional

from ..core.flow import SortingService
from ..core.model import Direction, Signal, StationConfig
from ..core.ports import ModulePort
from . import protocol as p

log = logging.getLogger("smartbin.gateway")


class Transport(ABC):
    @abstractmethod
    def write(self, data: bytes) -> None: ...


class SerialGateway(ModulePort):
    def __init__(self, config: StationConfig, transports: Dict[int, Transport],
                 clock: Callable[[], float]) -> None:
        self.config = config
        self.transports = transports
        self.clock = clock
        self.decoders = {mid: p.FrameDecoder() for mid in transports}
        self.core: Optional[SortingService] = None
        self.stats = {"frames_in": 0, "frames_out": 0, "unknown": 0}

    def attach(self, core: SortingService) -> None:
        self.core = core

    # ---------------------------------------------------------- inbound
    def on_bytes(self, module_id: int, data: bytes) -> None:
        for frame in self.decoders[module_id].feed(data):
            self.stats["frames_in"] += 1
            self._dispatch(module_id, frame)

    def _dispatch(self, module_id: int, f: p.Frame) -> None:
        assert self.core is not None, "attach() the core first"
        now = self.clock()
        t = f.type
        if t == p.MsgType.HELLO:
            if len(f.payload) >= 4 and f.payload[3] != p.PROTOCOL_VERSION:
                log.warning("module %d speaks protocol %d", module_id, f.payload[3])
            self.core.on_hello(module_id, now)
        elif t == p.MsgType.HEARTBEAT:
            if len(f.payload) >= 4:
                state = f.payload[0]
                last_seq = int.from_bytes(f.payload[1:3], "little")
                self.core.on_heartbeat(module_id, now,
                                       faulted=state == p.ModuleStateCode.STOERUNG,
                                       last_done=(last_seq, f.payload[3]))
            else:
                self.core.on_heartbeat(module_id, now)
        elif t == p.MsgType.ITEM_DETECTED:
            via_handover = bool(f.payload[0]) if f.payload else False
            self.core.on_item_detected(module_id, f.seq, now, via_handover)
        elif t == p.MsgType.IMAGE:
            self.core.on_image(module_id, f.seq, f.payload, now)
        elif t == p.MsgType.SORT_DONE:
            result = f.payload[0] if f.payload else 255
            self.core.on_sort_done(module_id, f.seq, result, now)
        elif t == p.MsgType.FAULT:
            code = f.payload[0] if f.payload else 0
            self.core.on_fault(module_id, f.seq, code, now)
        else:
            self.stats["unknown"] += 1  # Postel: ignore unknown types

    # ---------------------------------------------------------- outbound (ModulePort)
    def _send(self, module_id: int, mtype: p.MsgType, seq: int = 0, payload: bytes = b"") -> None:
        self.stats["frames_out"] += 1
        self.transports[module_id].write(p.encode(p.Frame(int(mtype), seq, payload)))

    def send_config(self, module_id: int) -> None:
        m = self.config.module(module_id)
        payload = bytes([p.FRACTION_CODES[m.fraction.value], m.position,
                         p.ROUTING_CODES[self.config.routing.value]])
        self._send(module_id, p.MsgType.CONFIG, 0, payload)

    def send_signal(self, module_id: int, seq: int, signal: Signal) -> None:
        self._send(module_id, p.MsgType.SIGNAL, seq, bytes([p.SIGNAL_CODES[signal.value]]))

    def send_sort(self, module_id: int, seq: int, direction: Direction,
                  signal: Optional[Signal] = None) -> None:
        sig = p.SIGNAL_CODES[signal.value] if signal is not None else 0
        self._send(module_id, p.MsgType.SORT, seq,
                   bytes([p.DIRECTION_CODES[direction.value], sig]))

    def send_heartbeat(self, module_id: int) -> None:
        self._send(module_id, p.MsgType.HEARTBEAT)

    def send_reset_fault(self, module_id: int) -> None:
        self._send(module_id, p.MsgType.RESET_FAULT)

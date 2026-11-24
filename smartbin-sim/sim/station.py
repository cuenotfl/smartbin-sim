"""The physical world: bins, flaps, PIR sensors, people throwing things,
the Jetson node, and the wiring of the real core to all of it."""
import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from smartbin.adapters.serial_gateway import SerialGateway, Transport
from smartbin.adapters.sqlite_store import SqliteEventStore
from smartbin.core.flow import SortingService
from smartbin.core.model import Classification, Direction, Fraction, Routing, StationConfig
from smartbin.core.ports import ClassifierPort

from .engine import Sim
from .link import SimLink
from .module import ModuleParams, SimModule, State
from .world import Item, parse_fake_jpeg

log = logging.getLogger("sim")


@dataclass
class SimParams:
    seed: int = 1
    baud: int = 921600
    corrupt_rate: float = 0.0          # share of frames with a flipped byte
    classifier_accuracy: float = 0.95  # share of items the model gets right
    inference_s: float = 0.08          # model latency on the Jetson (assumption)
    misthrow_rate: float = 0.3
    mean_interval_s: float = 8.0       # mean time between two throws
    hand_s: float = 0.4                # hand in opening during a normal throw
    p_hand_lingers: float = 0.03       # someone keeps the hand in the opening
    linger_s: float = 2.0
    fall_s: float = 0.2                # item falls onto the flap
    handover_s: float = 0.3            # item slides to neighbour flap (via_flaps)
    janitor_reset_s: float = 120.0     # Hausdienst quits a fault after this time
    module: ModuleParams = field(default_factory=ModuleParams)


class FakeClassifier(ClassifierPort):
    """Simulates a model with a given accuracy. Deterministic per item."""

    def __init__(self, accuracy: float, seed: int, fractions: List[Fraction],
                 on_inference=None) -> None:
        self.accuracy = accuracy
        self.seed = seed
        self.fractions = fractions
        self.on_inference = on_inference

    def classify(self, image: bytes) -> Classification:
        if self.on_inference:
            self.on_inference()
        item_id, true = parse_fake_jpeg(image)
        r = random.Random(self.seed * 1000003 + item_id)
        if r.random() < self.accuracy:
            return Classification(true, r.uniform(0.65, 0.99))
        wrong = [f for f in Fraction if f != true]
        return Classification(r.choice(wrong), r.uniform(0.3, 0.8))


class JetsonNode:
    """The Jetson as seen by the simulator: can be busy (inference) or dead."""

    def __init__(self, sim: Sim, inference_s: float) -> None:
        self.sim = sim
        self.inference_s = inference_s
        self.busy_until = 0.0
        self.alive = True
        self.gateway: Optional[SerialGateway] = None

    def inference_started(self) -> None:
        self.busy_until = max(self.sim.now, self.busy_until) + self.inference_s

    def receive(self, module_id: int, data: bytes) -> None:
        if not self.alive:
            return
        if self.sim.now < self.busy_until:  # CPU busy: process after inference
            self.sim.at(self.busy_until, self.receive, module_id, data)
            return
        assert self.gateway is not None
        self.gateway.on_bytes(module_id, data)


class JetsonTx(Transport):
    """Jetson-side transport: outbound bytes leave after the CPU is free."""

    def __init__(self, node: JetsonNode, link: SimLink) -> None:
        self.node = node
        self.link = link

    def write(self, data: bytes) -> None:
        if not self.node.alive:
            return
        self.node.sim.at(max(self.node.sim.now, self.node.busy_until), self.link.write, data)


class PhysicalStation:
    """Wires the REAL core (smartbin package) to simulated hardware."""

    def __init__(self, config: StationConfig, params: SimParams) -> None:
        self.config = config
        self.params = params
        self.sim = Sim()
        self.rng = random.Random(params.seed)
        self.items: List[Item] = []
        self.bins: Dict[Fraction, List[Item]] = {m.fraction: [] for m in config.modules}
        self.pir_until: Dict[int, float] = {m.module_id: -1.0 for m in config.modules}

        self.node = JetsonNode(self.sim, params.inference_s)
        self.modules: Dict[int, SimModule] = {}
        transports: Dict[int, Transport] = {}
        self.links: List[SimLink] = []
        for m in config.modules:
            mod = SimModule(self.sim, m.module_id, params.module, random.Random(params.seed + m.module_id))
            mod.station = self
            down = SimLink(self.sim, params.baud, mod.on_bytes, params.corrupt_rate,
                           random.Random(params.seed * 7 + m.module_id))
            up = SimLink(self.sim, params.baud, lambda d, mid=m.module_id: self.node.receive(mid, d),
                         params.corrupt_rate, random.Random(params.seed * 11 + m.module_id))
            mod.uplink = up
            transports[m.module_id] = JetsonTx(self.node, down)
            self.modules[m.module_id] = mod
            self.links += [down, up]

        self.store = SqliteEventStore()
        self.gateway = SerialGateway(config, transports, clock=lambda: self.sim.now)
        classifier = FakeClassifier(params.classifier_accuracy, params.seed,
                                    [m.fraction for m in config.modules],
                                    on_inference=self.node.inference_started)
        self.core = SortingService(config, classifier, self.gateway, self.store)
        self.gateway.attach(self.core)
        self.node.gateway = self.gateway

        self.sim.every(0.1, self._jetson_tick)
        self.sim.every(1.0, self._janitor)
        self._fault_since: Dict[int, float] = {}
        for mod in self.modules.values():
            mod.boot()

    # ------------------------------------------------------------ world
    def _jetson_tick(self) -> None:
        if self.node.alive:
            self.core.tick(self.sim.now)

    def _janitor(self) -> None:
        for mid, mod in self.modules.items():
            if mod.state == State.STOERUNG:
                since = self._fault_since.setdefault(mid, self.sim.now)
                if self.sim.now - since >= self.params.janitor_reset_s and self.node.alive:
                    log.info("Hausdienst resets module %d", mid)
                    self.core.reset_fault(mid)
                    self._fault_since.pop(mid)
            else:
                self._fault_since.pop(mid, None)

    def pir_active(self, module_id: int) -> bool:
        return self.sim.now < self.pir_until[module_id]

    def throw(self, item: Item, module_id: int, hand_s: Optional[float] = None) -> None:
        item.t_throw, item.chosen_module = self.sim.now, module_id
        mod = self.modules[module_id]
        item.module_idle_at_throw = mod.state == State.BEREIT and not mod.flap
        item.path.append((self.sim.now, "thrown:%d" % module_id))
        self.items.append(item)
        hand = self.params.hand_s if hand_s is None else hand_s
        self.pir_until[module_id] = max(self.pir_until[module_id], self.sim.now + hand)
        self.sim.after(self.params.fall_s, self.modules[module_id].receive_item, item)

    def deliver(self, item: Item, module_id: int, direction: Direction) -> None:
        """A flap released an item: where does it physically go?"""
        pos = self.config.module(module_id).position
        if direction == Direction.DIREKT:
            self._into_bin(item, pos)
            return
        npos = pos + (1 if direction == Direction.RECHTS else -1)
        neighbour = self.config.module_at(npos)
        if neighbour is None:
            self._into_bin(item, pos)  # no neighbour: falls into own bin
            return
        if self.config.routing == Routing.NEIGHBOUR_BIN:
            self._into_bin(item, npos)
        else:
            self.sim.after(self.params.handover_s,
                           self.modules[neighbour.module_id].receive_item, item)

    def _into_bin(self, item: Item, pos: int) -> None:
        m = self.config.module_at(pos)
        assert m is not None
        self.bins[m.fraction].append(item)
        item.final, item.t_final = "BIN:%s" % m.fraction.value, self.sim.now
        item.path.append((self.sim.now, item.final))

    def kill_jetson(self) -> None:
        self.node.alive = False

    # ------------------------------------------------------------ scenario helpers
    def generate_throws(self, n: int, start: float = 2.0) -> float:
        """Random users: returns the time of the last throw."""
        fractions = [m.fraction for m in self.config.modules]
        t = start
        for i in range(n):
            true = self.rng.choice(fractions)
            target = self.config.module_for(true)
            assert target is not None
            if self.rng.random() < self.params.misthrow_rate:
                chosen = self.rng.choice([m for m in self.config.modules if m.fraction != true])
            else:
                chosen = target
            hand = self.params.linger_s if self.rng.random() < self.params.p_hand_lingers else None
            item = Item(item_id=i + 1, true=true)
            self.sim.at(t, self.throw, item, chosen.module_id, hand)
            t += max(1.0, self.rng.expovariate(1.0 / self.params.mean_interval_s))
        return t

    def run(self, until: float) -> None:
        self.sim.run_until(until)

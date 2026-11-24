"""Core use case with fake adapters only (ADR-001): no simulator, no serial."""
from typing import List, Tuple

from smartbin.adapters.sqlite_store import SqliteEventStore
from smartbin.core.flow import SortingService
from smartbin.core.model import Classification, Direction, Fraction, Outcome, Routing, Signal
from smartbin.core.ports import ClassifierPort, ModulePort

from .conftest import station_config


class FixedClassifier(ClassifierPort):
    def __init__(self):
        self.next = Classification(Fraction.PET, 0.9)

    def classify(self, image: bytes) -> Classification:
        return self.next


class RecordingModules(ModulePort):
    def __init__(self):
        self.sent: List[Tuple] = []

    def send_config(self, m): self.sent.append(("CONFIG", m))
    def send_signal(self, m, seq, s): self.sent.append(("SIGNAL", m, seq, s))
    def send_sort(self, m, seq, d, signal=None): self.sent.append(("SORT", m, seq, d))
    def send_heartbeat(self, m): self.sent.append(("HB", m))
    def send_reset_fault(self, m): self.sent.append(("RESET", m))


def make(routing=Routing.VIA_FLAPS):
    cls, mods, store = FixedClassifier(), RecordingModules(), SqliteEventStore()
    core = SortingService(station_config(routing), cls, mods, store)
    for m in (1, 2, 3):
        core.on_hello(m, 0.0)
    return core, cls, mods, store


def test_correct_throw():
    core, cls, mods, store = make()
    core.on_item_detected(1, 1, 1.0)
    core.on_image(1, 1, b"img", 1.2)
    assert ("SIGNAL", 1, 1, Signal.OK) in mods.sent
    assert ("SORT", 1, 1, Direction.DIREKT) in mods.sent
    core.on_sort_done(1, 1, 0, 2.0)
    [e] = store.all()
    assert e.outcome == Outcome.KORREKT and e.feedback_ms == 200 and e.duration_ms == 1000


def test_misthrow_via_flaps_two_hops():
    core, cls, mods, store = make()
    cls.next = Classification(Fraction.REST, 0.9)
    core.on_item_detected(1, 5, 1.0)                    # PET opening
    core.on_image(1, 5, b"img", 1.2)
    assert ("SIGNAL", 1, 5, Signal.FEHLWURF) in mods.sent
    assert ("SORT", 1, 5, Direction.RECHTS) in mods.sent
    core.on_item_detected(2, 9, 2.0, via_handover=True)  # arrives before SORT_DONE
    assert ("SORT", 2, 9, Direction.RECHTS) in mods.sent
    core.on_sort_done(1, 5, 0, 2.1)
    core.on_item_detected(3, 4, 3.0, via_handover=True)
    assert ("SORT", 3, 4, Direction.DIREKT) in mods.sent
    core.on_sort_done(2, 9, 0, 3.1)
    assert store.all() == []                             # not finished yet
    core.on_sort_done(3, 4, 0, 4.0)
    [e] = store.all()
    assert e.outcome == Outcome.FEHLWURF_UMGELENKT and e.vorgang_id == "1-5"


def test_user_throw_is_not_mistaken_for_expected_handover():
    core, cls, mods, store = make()
    cls.next = Classification(Fraction.ALU, 0.9)
    core.on_item_detected(1, 1, 1.0)
    core.on_image(1, 1, b"img", 1.2)                     # expects item at module 2
    core.on_item_detected(2, 1, 1.5, via_handover=False) # someone throws into ALU
    assert ("2-1" in [v.vid for v in core.open_vorgaenge()])


def test_missing_image_times_out():
    core, cls, mods, store = make()
    core.on_item_detected(1, 1, 1.0)
    core.tick(5.0)
    [e] = store.all()
    assert e.outcome == Outcome.ABGEBROCHEN


def test_fault_aborts_vorgang_and_blocks_handover():
    core, cls, mods, store = make()
    core.on_item_detected(2, 1, 1.0)
    core.on_fault(2, 1, 3, 1.1)
    assert store.all()[0].outcome == Outcome.ABGEBROCHEN
    cls.next = Classification(Fraction.REST, 0.9)
    core.on_item_detected(1, 1, 2.0)
    core.on_image(1, 1, b"img", 2.2)
    assert ("SORT", 1, 1, Direction.DIREKT) in mods.sent  # ALU module faulted
    core.reset_fault(2)
    assert ("RESET", 2) in mods.sent


def test_heartbeats_and_module_loss():
    core, cls, mods, store = make()
    core.tick(0.0)
    assert sum(1 for s in mods.sent if s[0] == "HB") == 3
    core.on_item_detected(3, 1, 0.5)
    core.on_heartbeat(1, 3.0)
    core.on_heartbeat(2, 3.0)
    core.tick(4.0)                                       # module 3 silent > 3 s
    assert not core.status[3].online
    assert store.all()[0].outcome == Outcome.ABGEBROCHEN


def test_late_or_duplicate_messages_are_ignored():
    core, cls, mods, store = make()
    core.on_image(1, 99, b"img", 1.0)
    core.on_sort_done(1, 99, 0, 1.0)
    assert store.all() == []

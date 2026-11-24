from smartbin.core.model import Classification, Direction, Fraction, Outcome, Routing, Signal
from smartbin.core.policy import MisthrowPolicy

from .conftest import station_config

PET, ALU, REST = 1, 2, 3  # module ids, positions 0, 1, 2


def decide(routing, module, fraction, conf=0.9, unavailable=(), fractions=("PET", "ALU", "REST")):
    pol = MisthrowPolicy(station_config(routing, fractions))
    return pol.decide(module, Classification(Fraction(fraction) if fraction else None, conf), unavailable)


def test_correct_throw_goes_direct():
    d = decide(Routing.NEIGHBOUR_BIN, PET, "PET")
    assert (d.signal, d.outcome, d.route) == (Signal.OK, Outcome.KORREKT, [(PET, Direction.DIREKT)])


def test_adjacent_misthrow_neighbour_bin():
    d = decide(Routing.NEIGHBOUR_BIN, ALU, "PET")
    assert d.signal == Signal.FEHLWURF
    assert d.route == [(ALU, Direction.LINKS)]
    assert d.outcome == Outcome.FEHLWURF_UMGELENKT


def test_non_adjacent_misthrow_is_unreachable_with_neighbour_bin():
    d = decide(Routing.NEIGHBOUR_BIN, PET, "REST")
    assert d.signal == Signal.FEHLWURF            # user is still told
    assert d.route == [(PET, Direction.DIREKT)]
    assert d.outcome == Outcome.FEHLWURF_NICHT_UMLENKBAR


def test_via_flaps_reaches_every_bin():
    d = decide(Routing.VIA_FLAPS, PET, "REST")
    assert d.route == [(PET, Direction.RECHTS), (ALU, Direction.RECHTS), (REST, Direction.DIREKT)]
    d = decide(Routing.VIA_FLAPS, REST, "ALU")
    assert d.route == [(REST, Direction.LINKS), (ALU, Direction.DIREKT)]


def test_low_confidence_is_unknown_and_goes_to_rest():
    d = decide(Routing.NEIGHBOUR_BIN, ALU, "PET", conf=0.4)
    assert d.detected is None and d.signal == Signal.UNBEKANNT
    assert d.route == [(ALU, Direction.RECHTS)] and d.target == Fraction.REST


def test_unknown_unreachable_rest_is_held():
    d = decide(Routing.NEIGHBOUR_BIN, PET, None, conf=0.0)
    assert d.route == [(PET, Direction.HALTEN)]


def test_two_bin_station_maps_alu_to_unknown():
    d = decide(Routing.NEIGHBOUR_BIN, 2, "ALU", fractions=("REST", "PET"))
    assert d.detected is None
    assert d.route == [(2, Direction.LINKS)]   # PET module -> REST neighbour


def test_faulted_module_is_not_used_for_handover():
    d = decide(Routing.VIA_FLAPS, PET, "REST", unavailable=[ALU])
    assert d.outcome == Outcome.FEHLWURF_NICHT_UMLENKBAR
    assert d.route == [(PET, Direction.DIREKT)]

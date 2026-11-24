"""End-to-end scenarios: real core + real protocol + simulated hardware.
These mirror arc42 chapter 6.2 and the quality scenarios in chapter 10."""
from smartbin.adapters.config_loader import load
from smartbin.core.model import Fraction, Outcome, Routing
from sim.module import State
from sim.report import evaluate
from sim.world import Item


def outcomes(st):
    return [e.outcome for e in st.store.all()]


def test_correct_throw_lands_in_own_bin(make_station):
    st = make_station()
    item = Item(1, Fraction.PET)
    st.throw(item, 1)
    st.run(10)
    assert item.final == "BIN:PET"
    assert item.led_signal == "OK"
    assert outcomes(st) == [Outcome.KORREKT]


def test_misthrow_is_signalled_within_2s_and_redirected(make_station):  # QS-5, MUSS-03
    st = make_station()
    item = Item(1, Fraction.PET)
    st.throw(item, 2)                    # PET into ALU opening
    st.run(10)
    assert item.led_signal == "FEHLWURF"
    assert item.t_led - item.t_throw <= 2.0
    assert item.final == "BIN:PET"
    assert outcomes(st) == [Outcome.FEHLWURF_UMGELENKT]


def test_two_hops_via_flaps(make_station):
    st = make_station(Routing.VIA_FLAPS)
    item = Item(1, Fraction.REST)
    st.throw(item, 1)                    # REST into PET opening, ALU in between
    st.run(15)
    assert [p for _, p in item.path] == ["thrown:1", "flap:1", "flap:2", "flap:3", "BIN:REST"]
    assert outcomes(st) == [Outcome.FEHLWURF_UMGELENKT]


def test_neighbour_bin_cannot_reach_far_bin(make_station):
    st = make_station(Routing.NEIGHBOUR_BIN)
    item = Item(1, Fraction.REST)
    st.throw(item, 1)
    st.run(10)
    assert item.led_signal == "FEHLWURF"
    assert item.final == "BIN:PET"
    assert outcomes(st) == [Outcome.FEHLWURF_NICHT_UMLENKBAR]


def test_flap_does_not_move_while_hand_in_opening(make_station):  # QS-1
    st = make_station()
    item = Item(1, Fraction.ALU)
    st.throw(item, 1, hand_s=2.5)        # hand stays 2.5 s
    st.run(10)
    first_tilt = [t for t, s in st.modules[1].state_log if s == "SORTIEREN"][0]
    assert item.final == "BIN:ALU"
    assert item.t_final >= item.t_throw + 2.5   # waited for the hand
    assert first_tilt < item.t_throw + 2.5      # sort was requested earlier, but held


def test_hand_stays_too_long_gives_fault(make_station):
    st = make_station(pir_max_wait_s=1.0)
    item = Item(1, Fraction.ALU)
    st.throw(item, 1, hand_s=5.0)
    st.run(10)
    assert st.modules[1].state == State.STOERUNG
    assert item.final is None            # item stays safely on the flap
    assert outcomes(st) == [Outcome.ABGEBROCHEN]


def test_blockade_safe_state_and_janitor_reset(make_station):  # MUSS-05
    st = make_station(p_blockade=1.0, janitor_reset_s=5.0)
    item = Item(1, Fraction.PET)
    st.throw(item, 1)
    st.run(4)
    assert st.modules[1].state == State.STOERUNG
    assert st.modules[1].led == "STOERUNG"
    st.modules[1].params.p_blockade = 0.0   # someone cleared the jam
    st.run(15)
    assert item.final == "BIN:PET"          # re-detected after reset
    assert outcomes(st) == [Outcome.ABGEBROCHEN, Outcome.KORREKT]


def test_jetson_crash_puts_all_modules_in_safe_state(make_station):  # QS-2
    st = make_station()
    st.run(3)
    st.kill_jetson()
    t_dead = st.sim.now
    st.run(t_dead + 5)
    for mod in st.modules.values():
        assert mod.state == State.STOERUNG
        fault_time = mod.faults[0][0]
        assert fault_time - t_dead <= 3.0 + mod.params.heartbeat_interval_s


def test_noisy_serial_line_still_works(make_station):
    st = make_station(corrupt_rate=0.05)
    last = st.generate_throws(200)
    st.run(last + 30)
    r = evaluate(st)
    assert r.numbers["crc_errors"] > 0
    assert r.numbers["end_in_correct_bin"] > 0.85


def test_two_bin_station_only_needs_another_config(config_path):  # QS-7
    from sim.station import PhysicalStation
    from .conftest import ideal_params
    st = PhysicalStation(load(config_path("station-2.yaml")), ideal_params())
    st.run(1)
    item = Item(1, Fraction.PET)
    st.throw(item, 1)                    # PET into REST opening
    st.run(10)
    assert item.final == "BIN:PET"


def test_random_day_meets_muss_criteria_with_via_flaps(config_path):
    """The same check CI runs: 1000 random throws, realistic imperfections."""
    from sim.run import main
    assert main(["--config", config_path("station-3-via-flaps.yaml"),
                 "--throws", "1000", "--seed", "7"]) == 0


def test_random_day_shows_neighbour_bin_misses_muss_02(config_path):
    """Documents the open mechanical question: with neighbour_bin, misthrows
    between PET and REST cannot be corrected."""
    from sim.run import main
    assert main(["--config", config_path("station-3.yaml"),
                 "--throws", "1000", "--seed", "7"]) == 1


def test_lost_hello_is_repeated_until_config_arrives():
    """A module whose HELLO gets lost (or whose Jetson starts later) must not
    stay in INIT forever: it repeats HELLO with every heartbeat while in INIT."""
    from sim.station import PhysicalStation
    from tests.conftest import ideal_params, station_config
    st = PhysicalStation(station_config(), ideal_params())
    mod = st.modules[1]
    real_write, dropped = mod.uplink.write, []

    def drop_first_frame(data):
        if not dropped:
            dropped.append(data)
            return
        real_write(data)
    mod.uplink.write = drop_first_frame
    st.run(3.0)
    assert dropped and dropped[0][1] == 0x01          # the HELLO was the frame lost
    assert mod.state == State.BEREIT
    item = Item(1, Fraction.PET)
    st.throw(item, 1)
    st.run(10)
    assert item.final == "BIN:PET"

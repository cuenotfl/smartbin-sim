"""Interactive 3D simulation (sim/live.py) and its recorder (sim/trace.py)."""
import dataclasses
import os

from smartbin.adapters.config_loader import from_dict, load_raw
from sim.live import VIEWER, LiveSim
from sim.report import evaluate
from sim.run import build_params
from sim.station import PhysicalStation
from sim.trace import Recorder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    raw = load_raw(os.path.join(ROOT, "config", name))
    return from_dict(raw["station"]), build_params(raw, 3)


def _live(name="station-3-via-flaps.yaml"):
    config, params = _load(name)
    sim = LiveSim(config, params)
    sim.advance(2.0)  # boot, CONFIG
    return sim


def _run(sim, seconds):
    for _ in range(int(seconds * 10)):
        sim.advance(0.1)


def _item(sim, item_id):
    snaps = [e[2] for e in sim.rec.events if e[0] == "item" and e[2]["id"] == item_id]
    return snaps[-1]


def test_recorder_does_not_change_the_run():
    config, params = _load("station-3-via-flaps.yaml")
    results = []
    for record in (False, True):
        st = PhysicalStation(config, dataclasses.replace(params))
        if record:
            Recorder(st, heartbeats=True)
        st.run(st.generate_throws(60) + 30)
        r = evaluate(st)
        results.append(([c.value for c in r.criteria], r.counts, r.numbers))
    assert results[0] == results[1]


def test_user_input_is_the_classification():
    sim = _live()
    # REST thrown into the PET opening, user says REST: two hops to the right
    iid = sim.throw(1, "REST", 0.9)
    _run(sim, 6)
    it = _item(sim, iid)
    assert it["detected"] == "REST"
    assert it["outcome"] == "FEHLWURF_UMGELENKT"
    assert [p[1:] for p in it["path"]] == [["thrown", 1], ["flap", 1], ["flap", 2], ["flap", 3], ["bin", "REST"]]
    assert it["led"] == "FEHLWURF"
    assert it["input"] == {"detected": "REST", "confidence": 0.9, "true": "REST", "linger": False}


def test_low_confidence_becomes_unbekannt_and_wrong_input_counts_as_error():
    sim = _live()
    low = sim.throw(2, "ALU", 0.3)                    # below threshold 0.6
    wrong = sim.throw(1, "ALU", 0.95, true="PET")      # model is wrong: PET seen as ALU
    _run(sim, 8)
    assert _item(sim, low)["outcome"] == "UNBEKANNT"
    assert _item(sim, low)["final"] == "BIN:REST"
    w = _item(sim, wrong)
    assert w["detected"] == "ALU" and w["final"] == "BIN:ALU"
    report = sim.report()
    assert report["criteria"][0]["key"] == "MUSS-01" and report["criteria"][0]["value"] < 1.0


def test_flap_motion_returns_to_rest_and_respects_pir():
    sim = _live()
    sim.throw(1, "ALU", 0.9, linger=True)  # hand stays 2 s: flap must wait
    _run(sim, 8)
    motion = [e for e in sim.rec.events if e[0] == "motion" and e[2] == 1]
    pir_until = max(e[3] for e in sim.rec.events if e[0] == "pir" and e[2] == 1)
    assert motion, "flap never moved"
    assert motion[0][1] >= pir_until - 1e-6, "flap moved while the PIR saw a hand"
    assert motion[-1][5] == 0.0  # back at rest
    for a, b in zip(motion, motion[1:]):
        assert b[1] >= a[1]


def test_random_users_and_restart_controls():
    sim = _live()
    sim.control("random", True)
    _run(sim, 30)
    items = {e[2]["id"] for e in sim.rec.events if e[0] == "item"}
    assert items
    sim.control("jetson", False)
    _run(sim, 5)
    assert any(e[0] == "fault" and e[3] == "HEARTBEAT_LOST" for e in sim.rec.events)


def test_viewer_has_station_placeholder():
    with open(VIEWER, encoding="utf-8") as fh:
        assert "__STATION_INFO__" in fh.read()


def test_web_glue_runs_from_the_built_site(tmp_path, monkeypatch):
    """What the browser does (web/worker.js), with CPython instead of Pyodide."""
    import json
    import sys
    import zipfile

    from tools.build_web import build

    site = tmp_path / "site"
    build(str(site))
    assert (site / "index.html").read_text(encoding="utf-8").count("__STATION_INFO__") >= 1
    assert (site / "worker.js").exists()
    app_dir = tmp_path / "app"
    zipfile.ZipFile(str(site / "py.zip")).extractall(str(app_dir))
    monkeypatch.chdir(str(app_dir))
    monkeypatch.setattr(sys, "path", [str(app_dir)] + sys.path)
    for name in [n for n in sys.modules if n == "sim" or n.startswith(("sim.", "smartbin"))]:
        monkeypatch.delitem(sys.modules, name)
    from sim.web import WebApp

    app = WebApp("config/station-3.yaml")
    info = json.loads(app.info_json())
    assert info["routing"] == "neighbour_bin" and "config/station-2.yaml" in info["configs"]
    assert WebApp("../../etc/passwd").config_path == "config/station-3-via-flaps.yaml"
    for _ in range(8):
        app.advance(0.25)
    assert json.loads(app.request("throw", json.dumps(
        {"module": 1, "detected": "REST", "confidence": 0.9})))["ok"]
    for _ in range(40):
        app.advance(0.25)
    events = json.loads(app.request("poll", json.dumps({"from": 0, "epoch": None})))["events"]
    # PET and REST are not neighbours in this variant (README, Erkenntnis 1)
    assert [e[2]["outcome"] for e in events if e[0] == "item"][-1] == "FEHLWURF_NICHT_UMLENKBAR"
    assert json.loads(app.request("nope", "{}"))["ok"] is False

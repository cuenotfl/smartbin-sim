"""Interactive 3D simulation: the real core runs in real time, you throw items.

    python -m sim.live                                    # http://127.0.0.1:8090
    python -m sim.live --config config/station-3.yaml --open

The camera and model are out of scope (ClassifierPort only), so for each throw
the user enters what the classifier reports: Fraktion (or UNBEKANNT) and
Konfidenz. Optionally also what the item really is (ground truth), to try
wrong classifications. Everything else is the same as in sim.run: the real
core, protocol and statistics, the ESP32 twin, the serial lines.

Standard library only (http.server + JSON polling). Serves on localhost only;
the browser loads three.js from a CDN.
"""
import argparse
import json
import logging
import os
import queue
import random
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from smartbin.adapters.config_loader import from_dict, load_raw
from smartbin.core.model import Classification, Fraction, StationConfig

from .report import evaluate
from .run import apply_sets, build_params
from .station import FakeClassifier, PhysicalStation, SimParams
from .trace import Recorder
from .world import Item, parse_fake_jpeg

log = logging.getLogger("sim.live")
VIEWER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.html")
UNBEKANNT = "UNBEKANNT"


class ManualClassifier(FakeClassifier):
    """Returns what the user entered for an item; items without input (random
    users) fall back to the simulated model accuracy."""

    def __init__(self, accuracy: float, seed: int, fractions: List[Fraction]) -> None:
        super().__init__(accuracy, seed, fractions)
        self.answers: Dict[int, Classification] = {}

    def classify(self, image: bytes) -> Classification:
        item_id, _ = parse_fake_jpeg(image)
        answer = self.answers.get(item_id)
        if answer is None:
            return super().classify(image)
        if self.on_inference:
            self.on_inference()
        return answer


class LiveSim:
    """One station running in (scaled) real time. All access under self.lock."""

    def __init__(self, config: StationConfig, params: SimParams) -> None:
        self.config = config
        self.params = params
        self.lock = threading.RLock()
        self.epoch = int(time.time() * 1000)
        self.classifier = ManualClassifier(params.classifier_accuracy, params.seed,
                                           [m.fraction for m in config.modules])
        self.st = PhysicalStation(config, params, classifier=self.classifier)
        self.rec = Recorder(self.st)
        self.speed = 1.0
        self.paused = False
        self.random_users = False
        self._next_random: Optional[float] = None
        self._next_id = 1
        self._rng = random.Random(params.seed + 4711)

    # ------------------------------------------------------------ time
    def advance(self, real_dt: float) -> None:
        with self.lock:
            if self.paused:
                return
            target = self.st.sim.now + real_dt * self.speed
            while self.random_users and self._next_random is not None and self._next_random <= target:
                self._schedule_random(self._next_random)
                self._next_random += max(1.0, self._rng.expovariate(1.0 / self.params.mean_interval_s))
            self.st.run(target)

    def _schedule_random(self, t: float) -> None:
        fractions = [m.fraction for m in self.config.modules]
        true = self._rng.choice(fractions)
        target = self.config.module_for(true)
        assert target is not None
        chosen = target
        if self._rng.random() < self.params.misthrow_rate:
            chosen = self._rng.choice([m for m in self.config.modules if m.fraction != true])
        item = Item(item_id=self._new_id(), true=true)
        self.rec.mark_input(item.item_id, {"random": True})
        self.st.sim.at(t, self.st.throw, item, chosen.module_id, None)

    def _new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    # ------------------------------------------------------------ user actions
    def throw(self, module: int, detected: str, confidence: float,
              true: Optional[str] = None, linger: bool = False) -> int:
        self.config.module(module)  # KeyError for unknown modules
        confidence = min(1.0, max(0.0, float(confidence)))
        fraction = None if detected == UNBEKANNT else Fraction(detected)
        truth = Fraction(true) if true else (fraction or Fraction.REST)
        with self.lock:
            item = Item(item_id=self._new_id(), true=truth)
            self.classifier.answers[item.item_id] = Classification(fraction, confidence)
            self.rec.mark_input(item.item_id, {"detected": detected, "confidence": confidence,
                                               "true": truth.value, "linger": linger})
            hand = self.params.linger_s if linger else None
            self.st.sim.at(self.st.sim.now, self.st.throw, item, module, hand)
            return item.item_id

    def control(self, action: str, value: Any = None) -> None:
        with self.lock:
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
            elif action == "speed":
                self.speed = min(50.0, max(0.05, float(value)))
            elif action == "reset_fault":
                if self.st.node.alive:
                    self.st.core.reset_fault(int(value))
            elif action == "jetson":
                self.st.node.alive = bool(value)
            elif action == "random":
                self.random_users = bool(value)
                self._next_random = self.st.sim.now + 1.0 if value else None
            else:
                raise ValueError("unknown action %r" % action)

    # ------------------------------------------------------------ views
    def poll(self, start: int) -> Dict[str, Any]:
        with self.lock:
            events = self.rec.events[start:]
            return {"epoch": self.epoch, "now": round(self.st.sim.now, 4), "speed": self.speed,
                    "paused": self.paused, "jetson": self.st.node.alive,
                    "random": self.random_users, "next": start + len(events), "events": events}

    def report(self) -> Dict[str, Any]:
        with self.lock:
            r = evaluate(self.st)
            return {"text": r.to_text(), "passed": r.passed, "throws": r.throws,
                    "criteria": [{"key": c.key, "name": c.name, "value": c.value,
                                  "target": c.target, "passed": c.passed, "detail": c.detail}
                                 for c in r.criteria]}

    def static_info(self, config_path: str) -> Dict[str, Any]:
        p = self.params
        return {
            "config": config_path, "routing": self.config.routing.value,
            "threshold": self.config.confidence_threshold,
            "modules": [{"id": m.module_id, "fraction": m.fraction.value, "position": m.position}
                        for m in self.config.modules],
            "params": {"fall_s": p.fall_s, "handover_s": p.handover_s, "linger_s": p.linger_s,
                       "tilt_s": p.module.tilt_s, "return_s": p.module.return_s,
                       "baud": p.baud, "image_bytes": p.module.image_bytes},
        }


class Host:
    """Builds the station from a config and rebuilds it on restart. Used by
    the local server (App) and by the website (sim/web.py, Pyodide)."""

    def __init__(self, config_path: str, sets: Optional[List[str]] = None, seed: int = 1) -> None:
        self.config_path = config_path
        self.sets = list(sets or [])
        self.seed = seed
        self.sim: Optional[LiveSim] = None
        self.info: Dict[str, Any] = {}

    def build(self) -> None:
        raw = load_raw(self.config_path)
        params = build_params(raw, self.seed)
        apply_sets(params, self.sets)
        speed = self.sim.speed if self.sim is not None else 1.0
        self.sim = LiveSim(from_dict(raw["station"]), params)
        self.sim.speed = speed
        self.info = self.sim.static_info(self.config_path)

    def restart(self) -> None:
        self.build()


def handle_api(host: Host, op: str, args: Dict[str, Any]) -> Any:
    """The simulation API, independent of the transport (HTTP or web worker).
    Raises KeyError/ValueError/TypeError on bad input."""
    sim = host.sim
    assert sim is not None
    if op == "poll":
        start = int(args.get("from") or 0)
        return sim.poll(0 if str(args.get("epoch")) != str(sim.epoch) else start)
    if op == "report":
        return sim.report()
    if op == "throw":
        iid = sim.throw(int(args["module"]), str(args["detected"]), float(args["confidence"]),
                        args.get("true") or None, bool(args.get("linger")))
        return {"ok": True, "item": iid}
    if op == "control":
        if args.get("action") == "restart":
            host.restart()
        else:
            sim.control(str(args.get("action")), args.get("value"))
        return {"ok": True}
    raise KeyError("unknown operation %r" % op)


class App(Host):
    """Local server: owns the station on a single sim thread (SQLite and the
    core are not thread safe). HTTP handlers hand work to it via call()."""

    def __init__(self, config_path: str, sets: List[str], seed: int) -> None:
        super().__init__(config_path, sets, seed)
        self._jobs: "queue.Queue[Tuple[Callable[[], Any], Dict[str, Any], threading.Event]]" = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="sim", daemon=True).start()
        self._ready.wait()

    def call(self, fn: Callable[[], Any]) -> Any:
        """Run fn() on the sim thread and return its result (or raise)."""
        box: Dict[str, Any] = {}
        done = threading.Event()
        self._jobs.put((fn, box, done))
        if not done.wait(5.0):
            raise TimeoutError("simulation thread does not respond")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _run(self) -> None:
        self.build()
        self._ready.set()
        last = time.monotonic()
        while not self._stop.is_set():
            try:
                job, box, done = self._jobs.get(timeout=0.02)
                try:
                    box["result"] = job()
                except Exception as exc:  # handed back to the HTTP thread
                    box["error"] = exc
                done.set()
            except queue.Empty:
                pass
            now = time.monotonic()
            assert self.sim is not None
            self.sim.advance(min(0.25, now - last))  # cap after a stall
            last = now


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            log.debug(fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _api(self, op: str, args: Dict[str, Any]) -> None:
            try:
                self._send(200, json.dumps(app.call(lambda: handle_api(app, op, args))).encode("utf-8"),
                           "application/json")
            except (KeyError, ValueError, TypeError) as exc:
                self._send(400, json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"),
                           "application/json")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                with open(VIEWER, encoding="utf-8") as fh:
                    html = fh.read()
                info = json.dumps(app.info).replace("</", "<\\/")
                self._send(200, html.replace("__STATION_INFO__", info).encode("utf-8"),
                           "text/html; charset=utf-8")
            elif url.path.startswith("/api/"):
                self._api(url.path[5:], {k: v[0] for k, v in parse_qs(url.query).items()})
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            url = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = None
            if not url.path.startswith("/api/") or not isinstance(body, dict):
                self._send(400, b"bad request", "text/plain")
                return
            self._api(url.path[5:], body)

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Smart Bin interactive 3D simulation")
    ap.add_argument("--config", default="config/station-3-via-flaps.yaml")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(name)s %(levelname)s %(message)s")

    app = App(args.config, args.set, args.seed)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    url = "http://127.0.0.1:%d" % args.port
    print("Smart Bin 3D-Simulation: %s  (Ctrl+C beendet)" % url)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

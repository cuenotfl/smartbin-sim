"""Real-hardware entry point for the Jetson (not used by the simulator).

    python -m smartbin.app --config config/station-3.yaml --model model.onnx --db smartbin.db

Wiring only: builds the adapters, attaches them to the core and runs the loop.
Reader threads push received bytes into one queue, so the core stays single
threaded. The dashboard (web server threads) talks to the core only through
the same queue and a status snapshot.

`port` in the station config may be a device (/dev/ttyUSB0) or a pyserial URL,
e.g. rfc2217://localhost:4000 for the Wokwi-simulated firmware.
"""
import argparse
import logging
import queue
import threading
import time
from typing import Any, Dict, List

from .adapters.config_loader import load
from .adapters.dashboard import StationControl
from .adapters.serial_gateway import SerialGateway, Transport
from .adapters.sqlite_store import SqliteEventStore
from .core.flow import SortingService
from .core.model import StationConfig
from .core.ports import ClassifierPort

RESET = "reset"


class PySerialTransport(Transport):
    def __init__(self, port: str, baud: int) -> None:
        import serial  # pyserial
        self.ser = serial.serial_for_url(port, baudrate=baud, timeout=0.05)

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def read(self) -> bytes:
        return self.ser.read(4096)


class QueuedStationControl(StationControl):
    """Thread-safe bridge between dashboard threads and the core loop."""

    def __init__(self, core: SortingService, inbox: "queue.Queue") -> None:
        self._inbox = inbox
        self._fractions = {m.module_id: m.fraction.value for m in core.config.modules}
        self._snapshot: List[Dict[str, Any]] = []
        self.refresh(core)

    def refresh(self, core: SortingService) -> None:
        """Called from the core loop only. Replacing the list is atomic."""
        self._snapshot = [{"id": mid, "fraktion": self._fractions[mid], "online": st.online,
                           "stoerung": st.faulted} for mid, st in sorted(core.status.items())]

    def modules(self) -> List[Dict[str, Any]]:
        return list(self._snapshot)

    def reset_fault(self, module_id: int) -> bool:
        if module_id not in self._fractions:
            return False
        self._inbox.put((RESET, module_id))
        return True


class NullTransport(Transport):
    """For a configured module without a port (not connected / not built yet)."""

    def write(self, data: bytes) -> None:
        pass


def run(config: StationConfig, classifier: ClassifierPort, db: str, baud: int,
        dashboard_host: str = "127.0.0.1", dashboard_port: int = 0) -> None:
    """Build the adapters around the core and run forever."""
    serials = {m.module_id: PySerialTransport(m.port, baud) for m in config.modules if m.port}
    transports: Dict[int, Transport] = {m.module_id: serials.get(m.module_id, NullTransport())
                                        for m in config.modules}
    gateway = SerialGateway(config, transports, clock=time.time)
    core = SortingService(config, classifier, gateway, SqliteEventStore(db))
    gateway.attach(core)

    inbox: "queue.Queue" = queue.Queue()
    control = QueuedStationControl(core, inbox)

    def reader(mid: int, t: PySerialTransport) -> None:
        while True:
            data = t.read()
            if data:
                inbox.put((mid, data))

    for mid, t in serials.items():
        threading.Thread(target=reader, args=(mid, t), daemon=True).start()

    if dashboard_port:
        import uvicorn
        from .adapters.dashboard import create_app
        server = uvicorn.Server(uvicorn.Config(create_app(db, control), host=dashboard_host,
                                               port=dashboard_port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        logging.info("dashboard on http://%s:%d", dashboard_host, dashboard_port)

    while True:
        try:
            key, data = inbox.get(timeout=0.05)
            if key == RESET:
                logging.info("dashboard: RESET_FAULT for module %d", data)
                core.reset_fault(data)
            else:
                gateway.on_bytes(key, data)
        except queue.Empty:
            pass
        core.tick(time.time())
        control.refresh(core)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--db", default="smartbin.db")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--dashboard-port", type=int, default=8080, help="0 = no dashboard")
    ap.add_argument("--dashboard-host", default="127.0.0.1",
                    help="the dashboard has no login: only bind to a trusted network")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from .adapters.onnx_classifier import OnnxClassifier
    run(load(args.config), OnnxClassifier(args.model), args.db, args.baud,
        args.dashboard_host, args.dashboard_port)


if __name__ == "__main__":
    main()

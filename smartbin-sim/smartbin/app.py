"""Real-hardware entry point for the Jetson (not used by the simulator).

    python -m smartbin.app --config config/station-3.yaml --model model.onnx --db smartbin.db

Wiring only: builds the adapters, attaches them to the core and runs the loop.
Reader threads push received bytes into one queue, so the core stays single
threaded.
"""
import argparse
import logging
import queue
import threading
import time

from .adapters.config_loader import load
from .adapters.serial_gateway import SerialGateway, Transport
from .adapters.sqlite_store import SqliteEventStore
from .core.flow import SortingService


class PySerialTransport(Transport):
    def __init__(self, port: str, baud: int) -> None:
        import serial  # pyserial
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def read(self) -> bytes:
        return self.ser.read(4096)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--db", default="smartbin.db")
    ap.add_argument("--baud", type=int, default=921600)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from .adapters.onnx_classifier import OnnxClassifier
    config = load(args.config)
    transports = {m.module_id: PySerialTransport(m.port, args.baud) for m in config.modules}
    gateway = SerialGateway(config, transports, clock=time.time)
    core = SortingService(config, OnnxClassifier(args.model), gateway, SqliteEventStore(args.db))
    gateway.attach(core)

    inbox: "queue.Queue" = queue.Queue()

    def reader(mid: int, t: PySerialTransport) -> None:
        while True:
            data = t.read()
            if data:
                inbox.put((mid, data))

    for mid, t in transports.items():
        threading.Thread(target=reader, args=(mid, t), daemon=True).start()

    while True:
        try:
            mid, data = inbox.get(timeout=0.05)
            gateway.on_bytes(mid, data)
        except queue.Empty:
            pass
        core.tick(time.time())


if __name__ == "__main__":
    main()

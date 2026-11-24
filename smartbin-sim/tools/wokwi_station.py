"""Jetson side for testing the real firmware in the Wokwi simulator.

Runs the real core, serial gateway, SQLite store and dashboard (smartbin.app)
against the ESP32 simulated in Wokwi, which exposes its UART as RFC2217 port.
Instead of the camera model, the classifier answers from a script, so every
path can be tried by pressing the "Einwurf" button in Wokwi:

    pip install pyserial -r requirements-dashboard.txt
    (start the simulation in VS Code: firmware/README.md)
    python tools/wokwi_station.py --detect ALU PET REST UNBEKANNT

1st item -> ALU (OK, flap DIREKT), 2nd -> PET (FEHLWURF, flap LINKS),
3rd -> REST (FEHLWURF, RECHTS), 4th -> UNBEKANNT (-> REST, RECHTS), then again.
Dashboard with statistics and reset button: http://127.0.0.1:8080
"""
import argparse
import itertools
import logging
import os
import sys
from typing import Iterator, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smartbin.adapters.config_loader import load  # noqa: E402
from smartbin.app import run  # noqa: E402
from smartbin.core.model import Classification, Fraction  # noqa: E402
from smartbin.core.ports import ClassifierPort  # noqa: E402


class ScriptedClassifier(ClassifierPort):
    """Returns the next fraction of a fixed list, ignores the image."""

    def __init__(self, answers: List[str]) -> None:
        self._answers: Iterator[str] = itertools.cycle(answers)

    def classify(self, image: bytes) -> Classification:
        answer = next(self._answers)
        logging.info("image %d bytes -> scripted answer %s", len(image), answer)
        if answer == "UNBEKANNT":
            return Classification(None, 0.0)
        return Classification(Fraction(answer), 0.95)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default="config/station-wokwi.yaml")
    ap.add_argument("--detect", nargs="+", default=["ALU", "PET", "REST", "UNBEKANNT"],
                    choices=["PET", "ALU", "REST", "UNBEKANNT"])
    ap.add_argument("--db", default="wokwi.db")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--dashboard-port", type=int, default=8080, help="0 = no dashboard")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    run(load(args.config), ScriptedClassifier(args.detect), args.db, args.baud,
        dashboard_port=args.dashboard_port)


if __name__ == "__main__":
    main()

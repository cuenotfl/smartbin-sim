"""Reads station.yaml (arc42 8.6)."""
from typing import Any, Dict

import yaml

from ..core.model import Fraction, ModuleConfig, Routing, StationConfig, Timeouts


def from_dict(data: Dict[str, Any]) -> StationConfig:
    modules = [ModuleConfig(module_id=int(m["id"]), fraction=Fraction(m["fraction"]),
                            position=int(m["position"]), port=str(m.get("port", "")))
               for m in data["modules"]]
    positions = sorted(m.position for m in modules)
    if positions != list(range(len(modules))):
        raise ValueError("module positions must be 0..n-1 without gaps")
    if len({m.fraction for m in modules}) != len(modules):
        raise ValueError("each fraction may appear only once")
    t = data.get("timeouts", {}) or {}
    return StationConfig(
        modules=modules,
        confidence_threshold=float(data.get("confidence_threshold", 0.6)),
        routing=Routing(data.get("routing", Routing.NEIGHBOUR_BIN.value)),
        timeouts=Timeouts(**{k: float(v) for k, v in t.items()}),
    )


def load(path: str) -> StationConfig:
    with open(path, encoding="utf-8") as fh:
        return from_dict(yaml.safe_load(fh)["station"])


def load_raw(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)

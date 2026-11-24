import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smartbin.core.model import Fraction, ModuleConfig, Routing, StationConfig  # noqa: E402
from sim.module import ModuleParams  # noqa: E402
from sim.station import PhysicalStation, SimParams  # noqa: E402


def station_config(routing=Routing.VIA_FLAPS, fractions=("PET", "ALU", "REST")):
    return StationConfig(
        modules=[ModuleConfig(i + 1, Fraction(f), i) for i, f in enumerate(fractions)],
        routing=routing,
    )


def ideal_params(**overrides):
    """Deterministic world: perfect classifier, no jams, no random hands."""
    module = ModuleParams(p_blockade=0.0)
    params = SimParams(classifier_accuracy=1.0, p_hand_lingers=0.0, module=module)
    for k, v in overrides.items():
        setattr(params.module if hasattr(module, k) and not hasattr(params, k) else params, k, v)
    return params


@pytest.fixture
def make_station():
    def _make(routing=Routing.VIA_FLAPS, fractions=("PET", "ALU", "REST"), **overrides):
        st = PhysicalStation(station_config(routing, fractions), ideal_params(**overrides))
        st.run(1.0)  # boot: HELLO -> CONFIG
        return st
    return _make


@pytest.fixture
def config_path():
    return lambda name: os.path.join(ROOT, "config", name)

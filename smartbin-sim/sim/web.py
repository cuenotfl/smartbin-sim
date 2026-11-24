"""Glue for the static website: the same LiveSim as `python -m sim.live`, but
running inside the browser (Pyodide in a web worker, see web/worker.js).

No server and no threads: the worker calls advance() from a timer and
request() for every API call of the viewer. Everything crosses the JS
boundary as JSON strings.
"""
import json
import os
from typing import List

from .live import Host, handle_api

CONFIG_DIR = "config"


def available_configs() -> List[str]:
    if not os.path.isdir(CONFIG_DIR):
        return []
    return sorted("%s/%s" % (CONFIG_DIR, f) for f in os.listdir(CONFIG_DIR)
                  if f.endswith(".yaml") and "wokwi" not in f)


class WebApp(Host):
    def __init__(self, config_path: str = "", seed: int = 1) -> None:
        configs = available_configs()
        if config_path not in configs:
            config_path = "%s/station-3-via-flaps.yaml" % CONFIG_DIR
        super().__init__(config_path, [], seed)
        self.build()

    def info_json(self) -> str:
        info = dict(self.info)
        info["configs"] = available_configs()
        info["web"] = True
        return json.dumps(info)

    def advance(self, real_dt: float) -> None:
        assert self.sim is not None
        self.sim.advance(min(0.25, max(0.0, float(real_dt))))

    def request(self, op: str, args_json: str) -> str:
        try:
            return json.dumps(handle_api(self, op, json.loads(args_json or "{}")))
        except (KeyError, ValueError, TypeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)})

# CLAUDE.md – guide for AI assistants working on this repo

Smart Bin: a retrofit for FHNW recycling stations (PET | ALU | REST). A Jetson Nano
classifies each item from an ESP32-CAM photo, tells the user via LED if it was a
misthrow, and tilts a flap so the item reaches the right bin. This repo holds the
Jetson software and a discrete-event simulation of the whole station, because the
team has no Jetson yet. The architecture doc (arc42, German) is the source of truth
for intent; this file is the source of truth for how to change code.

## Commands

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                   # must stay green
python -m sim.run --throws 1000 --seed 42             # must print RESULT: PASS
python -m sim.run --throws 1000 --seed 42 --set corrupt_rate=0.02   # must PASS
python -m sim.run -v --throws 20                      # per-Vorgang log for debugging
python -m sim.live --open                             # interactive 3D view, manual throws
python tools/build_web.py                             # static website -> site/ (Pyodide)
```

Run all three before you report a change as done. CI runs the same.

## Architecture rules (do not break)

1. **Ports and Adapters (ADR-001).** `smartbin/core/` must never import from
   `smartbin/adapters/`, `sim/`, or any I/O library. The core talks only through the
   ports in `core/ports.py`. Time is always passed in as `now`; never call
   `time.time()` in the core.
2. **The core is single-threaded and event-driven.** Adapters call `on_*` methods
   and `tick(now)`. No sleeping, no threads in the core.
3. **Same code on laptop and Jetson.** `sim/` may only replace adapters
   (classifier, transports). If you need a hook for the simulation, add it to the
   sim side, not the core.
4. **Safety lives in the ESP32 (ADR-003).** `sim/module.py` is the executable spec
   of the firmware: a flap moves only in state SORTIEREN and only while the PIR is
   clear; any fault goes to STOERUNG (safe state) and leaves only via RESET_FAULT.
   Never move safety decisions into the Jetson core.
5. **Protocol is a contract (ADR-004).** Frame layout and enum codes are in
   `adapters/protocol.py`. Never renumber codes; only append. Any change to the
   wire format must update `CONTRACT_VECTORS` in `tests/test_protocol.py` and be
   called out explicitly in your summary, because the C++ firmware must follow.
   Receivers ignore unknown message types (Postel).
6. **Commands are idempotent per (module_id, seq).** The core may resend SIGNAL/SORT;
   the module ignores duplicates. Keep it that way when adding messages.
7. **No personal data (revDSG).** Images are never stored; the statistics row
   `ThrowEvent` has no person-related field. Do not add one.
8. **Python >= 3.8** (the Jetson Nano image is old). No `match`, no `X | Y` types,
   no `list[int]` at runtime. `vermin --target=3.8- smartbin sim tests tools` must pass.

9. **The website runs this code in Pyodide.** Everything `sim.web` imports must stay
   pure Python (stdlib + PyYAML + sqlite3); no threads or sockets on that path.
   One viewer (`sim/viewer.html`) serves both the local server and the website.

## Vocabulary (use these names in code and messages)

Fraktion (PET/ALU/REST), Einwurf, Vorgang (one item, id "<module>-<seq>"),
gewaehlt/chosen vs. erkannt/detected, Fehlwurf, UNBEKANNT (below confidence
threshold → REST), Modul, Klappe (flap), Route/Hop, STOERUNG, Hausdienst (janitor).
Routing variants: `neighbour_bin` (flap drops into neighbour's bin) and `via_flaps`
(item handed from flap to flap). Which one the hardware will use is still open.

## Where things are

| Change | File |
| --- | --- |
| Misthrow decision, routes | `smartbin/core/policy.py` |
| Flow, timeouts, retries, heartbeat handling | `smartbin/core/flow.py` |
| Wire format | `smartbin/adapters/protocol.py` + `tests/test_protocol.py` |
| Firmware behaviour (spec) | `sim/module.py` |
| Physics, users, Jetson latency | `sim/station.py` |
| What counts as PASS | `sim/report.py` (mirrors MUSS-01..03 of the project brief) |
| Station layouts | `config/*.yaml` |
| 3D view: recorded events, server, browser page | `sim/trace.py`, `sim/live.py`, `sim/viewer.html` |
| Website (same viewer, Python in the browser) | `sim/web.py`, `web/worker.js`, `tools/build_web.py` |

## How to work

- Write or extend a scenario test in `tests/test_scenarios.py` that reproduces a
  bug or describes a new behaviour **before** changing code.
- Prefer a deterministic test (`make_station` fixture: perfect classifier, no jams)
  over a statistical one.
- Do not loosen a PASS criterion in `sim/report.py` to make a run green. If a
  requirement cannot be met, say so and show the numbers.
- When a simulation finding affects hardware or the architecture (baud rate, image
  size, mechanics), add it to "Erkenntnisse" in README.md and mention it in your
  summary so the team can update the arc42 doc.
- Keep the README in German, code and comments in English.

"""Command line: run a random-throw simulation and check the MUSS criteria.

    python -m sim.run --config config/station-3-via-flaps.yaml --throws 1000 --seed 42

Exit code 0 = all criteria passed, 1 = at least one failed (used by CI).
"""
import argparse
import dataclasses
import json
import logging
import sys

from smartbin.adapters.config_loader import from_dict, load_raw

from .module import ModuleParams
from .report import evaluate
from .station import PhysicalStation, SimParams


def build_params(raw: dict, seed: int) -> SimParams:
    data = dict(raw.get("simulation", {}) or {})
    module = ModuleParams(**(data.pop("module", {}) or {}))
    params = SimParams(module=module, **data)
    params.seed = seed
    return params


def export_db(st: PhysicalStation, path: str) -> int:
    """Copy the simulated statistics into a database file, shifted so the
    simulated day ends now (demo data for the dashboard)."""
    import time
    from smartbin.adapters.sqlite_store import SqliteEventStore
    events = st.store.all()
    offset = time.time() - st.sim.now
    out = SqliteEventStore(path)
    for e in events:
        out.save(dataclasses.replace(e, timestamp=e.timestamp + offset))
    out.close()
    return len(events)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Smart Bin station simulation")
    ap.add_argument("--config", default="config/station-3-via-flaps.yaml")
    ap.add_argument("--throws", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a simulation parameter, e.g. --set classifier_accuracy=0.85")
    ap.add_argument("--csv", help="export the core statistics as CSV")
    ap.add_argument("--json", help="write the report as JSON")
    ap.add_argument("--db", help="append the statistics to a SQLite file (sim time mapped to"
                    " the last hours before now), e.g. to try the dashboard")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(name)s %(levelname)s %(message)s")

    raw = load_raw(args.config)
    config = from_dict(raw["station"])
    params = build_params(raw, args.seed)
    for kv in args.set:
        key, value = kv.split("=", 1)
        target = params.module if hasattr(params.module, key) and not hasattr(params, key) else params
        setattr(target, key, type(getattr(target, key))(value))

    st = PhysicalStation(config, params)
    last = st.generate_throws(args.throws)
    st.run(last + 30.0)
    report = evaluate(st)

    print("Config: %s | routing=%s | seed=%d | simulated %.0f min" % (
        args.config, config.routing.value, args.seed, st.sim.now / 60))
    print(report.to_text())
    if args.csv:
        n = st.store.export_csv(args.csv, iso_time=False)
        print("CSV: %d rows -> %s" % (n, args.csv))
    if args.db:
        n = export_db(st, args.db)
        print("DB: %d rows -> %s" % (n, args.db))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(report), fh, indent=2)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())

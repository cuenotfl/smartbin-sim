"""Builds the static website with the interactive 3D simulation.

    python tools/build_web.py                  # -> site/
    python -m http.server -d site 8000         # try it: http://127.0.0.1:8000

The site needs no server-side code: the real Python core (smartbin/, sim/)
runs in the visitor's browser via Pyodide (web/worker.js). Any static host
works, e.g. GitHub Pages (.github/workflows/pages.yml).
"""
import argparse
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("smartbin", "sim")
PLACEHOLDER = "__STATION_INFO__"


def build(out: str) -> None:
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    with open(os.path.join(ROOT, "sim", "viewer.html"), encoding="utf-8") as fh:
        html = fh.read()
    if PLACEHOLDER not in html:
        sys.exit("sim/viewer.html lost its %s placeholder" % PLACEHOLDER)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    shutil.copy(os.path.join(ROOT, "web", "worker.js"), os.path.join(out, "worker.js"))
    open(os.path.join(out, ".nojekyll"), "w").close()

    zpath = os.path.join(out, "py.zip")
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for pkg in PACKAGES:
            for base, dirs, files in os.walk(os.path.join(ROOT, pkg)):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(".py") or f == "viewer.html":
                        full = os.path.join(base, f)
                        z.write(full, os.path.relpath(full, ROOT).replace(os.sep, "/"))
                        n += 1
        cdir = os.path.join(ROOT, "config")
        for f in sorted(os.listdir(cdir)):
            if f.endswith(".yaml"):
                z.write(os.path.join(cdir, f), "config/" + f)
                n += 1
    print("site: %s (%d files in py.zip, %.0f KB)" % (out, n, os.path.getsize(zpath) / 1024))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "site"))
    build(ap.parse_args(argv).out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

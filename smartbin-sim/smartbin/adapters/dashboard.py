"""Hausdienst dashboard (ADR-005): statistics per day and Fraktion, CSV download,
reset of a module in STOERUNG.

Driving adapter. It never touches the core directly: it reads the statistics
through its own read-only SQLite connection, and module status / reset go
through a `StationControl` that the Jetson main loop provides (see app.py),
so the core stays single threaded (CLAUDE.md rule 2).

Standalone, read-only (no reset buttons), e.g. on a copy of the database:

    python -m smartbin.adapters.dashboard --db smartbin.db

Optional dependencies: fastapi, uvicorn (requirements-dashboard.txt).
"""
import argparse
import html
import io
from typing import Any, Dict, List, Optional

from ..core.model import Outcome
from .sqlite_store import SqliteEventStore

OUTCOME_LABELS = [
    (Outcome.KORREKT, "korrekt"),
    (Outcome.FEHLWURF_UMGELENKT, "Fehlwurf umgelenkt"),
    (Outcome.FEHLWURF_NICHT_UMLENKBAR, "Fehlwurf nicht umlenkbar"),
    (Outcome.UNBEKANNT, "unbekannt"),
    (Outcome.ABGEBROCHEN, "abgebrochen"),
]


class StationControl:
    """What the dashboard may ask of the running station. Implementations
    must be thread safe: the dashboard calls them from web server threads."""

    def modules(self) -> List[Dict[str, Any]]:
        """[{"id": 1, "fraktion": "PET", "online": True, "stoerung": False}, ...]"""
        raise NotImplementedError

    def reset_fault(self, module_id: int) -> bool:
        """Queue a RESET_FAULT for the module. False if the module is unknown."""
        raise NotImplementedError


def create_app(db_path: str, control: Optional[StationControl] = None, days: int = 14):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, RedirectResponse, Response

    app = FastAPI(title="Smart Bin Dashboard")

    def stats(n_days: Optional[int]) -> List[Dict[str, Any]]:
        store = SqliteEventStore(db_path, readonly=True)  # one connection per request/thread
        try:
            return store.daily_stats(n_days)
        finally:
            store.close()

    def modules() -> List[Dict[str, Any]]:
        return control.modules() if control is not None else []

    def reset(module_id: int) -> None:
        if control is None:
            raise HTTPException(503, "dashboard runs without station (read-only)")
        if not control.reset_fault(module_id):
            raise HTTPException(404, "unknown module %d" % module_id)

    @app.get("/api/stats")
    def api_stats(days: Optional[int] = days) -> List[Dict[str, Any]]:
        return stats(days)

    @app.get("/api/modules")
    def api_modules() -> List[Dict[str, Any]]:
        return modules()

    @app.post("/api/modules/{module_id}/reset", status_code=202)
    def api_reset(module_id: int) -> Dict[str, Any]:
        reset(module_id)
        return {"module": module_id, "reset": "queued"}

    @app.post("/modules/{module_id}/reset")
    def form_reset(module_id: int):
        reset(module_id)
        return RedirectResponse("/?reset=%d" % module_id, status_code=303)

    @app.get("/statistik.csv")
    def csv_download() -> Response:
        store = SqliteEventStore(db_path, readonly=True)
        try:
            buf = io.StringIO()
            store.write_csv(buf)
        finally:
            store.close()
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="smartbin-statistik.csv"'})

    @app.get("/", response_class=HTMLResponse)
    def index(reset: Optional[int] = None) -> str:
        return render_page(stats(days), modules(), control is not None, days, reset)

    return app


# ---------------------------------------------------------------- HTML
CSS = """
body{font:15px/1.45 system-ui,sans-serif;margin:0;color:#1d2125;background:#f6f7f8}
main{max-width:980px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 8px}
.sub{color:#5b636b;margin:0 0 8px}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #dde1e4}
th,td{padding:6px 10px;border-bottom:1px solid #eceff1;text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{background:#eef1f3;font-weight:600;font-size:13px}
tr.day td{border-top:2px solid #cfd5da}
.ok{color:#1b7a3a}.warn{color:#a15c00}.bad{color:#b3261e;font-weight:600}
.cards{display:flex;gap:12px;flex-wrap:wrap}
.card{background:#fff;border:1px solid #dde1e4;border-radius:6px;padding:12px 14px;min-width:170px}
.card b{display:block;font-size:16px}
button,.btn{font:inherit;padding:5px 12px;border-radius:4px;border:1px solid #8a2a22;
  background:#b3261e;color:#fff;cursor:pointer;text-decoration:none}
.btn.sec{background:#fff;color:#1d2125;border-color:#9aa3ab}
.note{background:#e8f3ec;border:1px solid #b7dcc3;padding:8px 12px;border-radius:4px}
"""


def _pct(part: int, total: int) -> str:
    return "%.1f %%" % (100.0 * part / total) if total else "-"


def render_page(rows: List[Dict[str, Any]], mods: List[Dict[str, Any]], has_control: bool,
                days: int, reset_done: Optional[int] = None) -> str:
    e = html.escape
    out = ["<!doctype html><html lang='de'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<meta http-equiv='refresh' content='15'>",
           "<title>Smart Bin – Hausdienst</title><style>%s</style></head><body><main>" % CSS,
           "<h1>Smart Bin – Hausdienst</h1>",
           "<p class='sub'>Statistik der letzten %d Tage pro Fraktion (gewählte Öffnung). "
           "Keine Bilder, keine Personendaten.</p>" % days]

    if reset_done is not None:
        out.append("<p class='note'>Reset für Modul %d gesendet. Der Status aktualisiert sich "
                   "mit dem nächsten Heartbeat.</p>" % reset_done)

    out.append("<h2>Module</h2>")
    if not has_control:
        out.append("<p class='sub'>Nur Lesemodus: Dashboard läuft ohne Station, "
                   "kein Modulstatus und kein Reset.</p>")
    else:
        out.append("<div class='cards'>")
        for m in mods:
            if m.get("stoerung"):
                status = "<span class='bad'>STÖRUNG</span>"
                action = ("<form method='post' action='/modules/%d/reset' "
                          "onsubmit=\"return confirm('Klappe frei und Störung behoben? "
                          "Modul %d zurücksetzen.')\"><button>Störung zurücksetzen</button></form>"
                          % (int(m["id"]), int(m["id"])))
            elif m.get("online"):
                status, action = "<span class='ok'>bereit</span>", ""
            else:
                status, action = "<span class='warn'>offline</span>", ""
            out.append("<div class='card'>Modul %d · %s<b>%s</b>%s</div>"
                       % (int(m["id"]), e(str(m.get("fraktion", ""))), status, action))
        out.append("</div>")

    out.append("<h2>Einwürfe pro Tag und Fraktion</h2>")
    out.append("<p><a class='btn sec' href='/statistik.csv'>CSV herunterladen (alle Einwürfe)</a></p>")
    if not rows:
        out.append("<p class='sub'>Noch keine Einwürfe gespeichert.</p>")
    else:
        head = "".join("<th>%s</th>" % e(label) for _, label in OUTCOME_LABELS)
        out.append("<table><tr><th>Tag</th><th>Fraktion</th><th>Total</th>%s"
                   "<th>Fehlwurf-Quote</th><th>Rückmeldung Ø</th></tr>" % head)
        last_day = None
        for r in rows:
            total = r["total"]
            misthrows = r[Outcome.FEHLWURF_UMGELENKT.value] + r[Outcome.FEHLWURF_NICHT_UMLENKBAR.value]
            cells = "".join("<td>%d</td>" % r[o.value] for o, _ in OUTCOME_LABELS)
            fb = r["rueckmeldung_avg_ms"]
            cls = " class='day'" if r["tag"] != last_day and last_day is not None else ""
            out.append("<tr%s><td>%s</td><td>%s</td><td>%d</td>%s<td>%s</td><td>%s</td></tr>" % (
                cls, e(r["tag"]) if r["tag"] != last_day else "", e(r["fraktion"]), total, cells,
                _pct(misthrows, total), "-" if fb is None else "%.2f s" % (fb / 1000.0)))
            last_day = r["tag"]
        out.append("</table>")
    out.append("</main></body></html>")
    return "".join(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Smart Bin dashboard, read-only")
    ap.add_argument("--db", default="smartbin.db")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(args.db, None, args.days), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

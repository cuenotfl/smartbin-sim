"""SQLite event store (ADR-005). Every Vorgang is committed immediately (KANN-05)."""
import csv
import datetime
import sqlite3
from typing import IO, Any, Dict, List, Optional

from ..core.model import Fraction, Outcome, ThrowEvent
from ..core.ports import EventStorePort

SCHEMA = """
CREATE TABLE IF NOT EXISTS einwurf (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    REAL    NOT NULL,
    vorgang_id   TEXT    NOT NULL,
    module_id    INTEGER NOT NULL,
    chosen       TEXT    NOT NULL,
    detected     TEXT,
    confidence   REAL    NOT NULL,
    outcome      TEXT    NOT NULL,
    duration_ms  INTEGER NOT NULL,
    feedback_ms  INTEGER
);
"""

CSV_HEADER = ["zeit", "vorgang", "modul", "gewaehlt", "erkannt", "konfidenz",
              "ergebnis", "dauer_ms", "rueckmeldung_ms"]


class SqliteEventStore(EventStorePort):
    def __init__(self, path: str = ":memory:", readonly: bool = False) -> None:
        """`readonly` opens an existing database for readers in other threads
        or processes (dashboard); the core keeps the only writing connection."""
        if readonly:
            self.conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            return
        self.conn = sqlite3.connect(path)
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def save(self, e: ThrowEvent) -> None:
        self.conn.execute(
            "INSERT INTO einwurf (timestamp, vorgang_id, module_id, chosen, detected, confidence,"
            " outcome, duration_ms, feedback_ms) VALUES (?,?,?,?,?,?,?,?,?)",
            (e.timestamp, e.vorgang_id, e.module_id, e.chosen.value,
             e.detected.value if e.detected else None, e.confidence, e.outcome.value,
             e.duration_ms, e.feedback_ms))
        self.conn.commit()

    def all(self) -> List[ThrowEvent]:
        rows = self.conn.execute(
            "SELECT timestamp, vorgang_id, module_id, chosen, detected, confidence, outcome,"
            " duration_ms, feedback_ms FROM einwurf ORDER BY id").fetchall()
        return [ThrowEvent(r[0], r[1], r[2], Fraction(r[3]), Fraction(r[4]) if r[4] else None,
                           r[5], Outcome(r[6]), r[7], r[8]) for r in rows]

    def daily_stats(self, days: Optional[int] = None) -> List[Dict[str, Any]]:
        """One row per (local day, chosen fraction) with a count per Outcome,
        newest day first. `days` limits the result to the last n days."""
        where, args = "", []  # type: str, List[Any]
        if days is not None:
            where = "WHERE date(timestamp, 'unixepoch', 'localtime') >= date('now', 'localtime', ?)"
            args.append("-%d days" % max(days - 1, 0))
        rows = self.conn.execute(
            "SELECT date(timestamp, 'unixepoch', 'localtime') AS tag, chosen, outcome,"
            " COUNT(*), AVG(feedback_ms) FROM einwurf " + where +
            " GROUP BY tag, chosen, outcome", args).fetchall()
        table = {}  # type: Dict[Any, Dict[str, Any]]
        for day, chosen, outcome, n, avg_fb in rows:
            row = table.get((day, chosen))
            if row is None:
                row = {"tag": day, "fraktion": chosen, "total": 0, "_fb_sum": 0.0, "_fb_n": 0}
                row.update({o.value: 0 for o in Outcome})
                table[(day, chosen)] = row
            row[outcome] = row.get(outcome, 0) + n
            row["total"] += n
            if avg_fb is not None:
                row["_fb_sum"] += avg_fb * n
                row["_fb_n"] += n
        order = {f.value: i for i, f in enumerate(Fraction)}
        result = sorted(table.values(), key=lambda r: (r["tag"], -order.get(r["fraktion"], 99)),
                        reverse=True)
        for row in result:
            fb_sum, fb_n = row.pop("_fb_sum"), row.pop("_fb_n")
            row["rueckmeldung_avg_ms"] = int(round(fb_sum / fb_n)) if fb_n else None
        return result

    def write_csv(self, fh: IO[str], iso_time: bool = True) -> int:
        """Semicolon CSV for Excel (KANN-04) into an open text stream."""
        events = self.all()
        w = csv.writer(fh, delimiter=";")
        w.writerow(CSV_HEADER)
        for e in events:
            t = (datetime.datetime.fromtimestamp(e.timestamp).isoformat(timespec="seconds")
                 if iso_time else e.timestamp)
            w.writerow([t, e.vorgang_id, e.module_id, e.chosen.value,
                        e.detected.value if e.detected else "UNBEKANNT",
                        "%.2f" % e.confidence, e.outcome.value, e.duration_ms,
                        "" if e.feedback_ms is None else e.feedback_ms])
        return len(events)

    def export_csv(self, path: str, iso_time: Optional[bool] = True) -> int:
        """Semicolon CSV for Excel (KANN-04). Returns number of rows."""
        with open(path, "w", newline="", encoding="utf-8") as fh:
            return self.write_csv(fh, bool(iso_time))

    def close(self) -> None:
        self.conn.close()

"""SQLite event store (ADR-005). Every Vorgang is committed immediately (KANN-05)."""
import csv
import sqlite3
from typing import List, Optional

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


class SqliteEventStore(EventStorePort):
    def __init__(self, path: str = ":memory:") -> None:
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

    def export_csv(self, path: str, iso_time: Optional[bool] = True) -> int:
        """Semicolon CSV for Excel (KANN-04). Returns number of rows."""
        import datetime
        events = self.all()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["zeit", "vorgang", "modul", "gewaehlt", "erkannt", "konfidenz",
                        "ergebnis", "dauer_ms", "rueckmeldung_ms"])
            for e in events:
                t = (datetime.datetime.fromtimestamp(e.timestamp).isoformat(timespec="seconds")
                     if iso_time else e.timestamp)
                w.writerow([t, e.vorgang_id, e.module_id, e.chosen.value,
                            e.detected.value if e.detected else "UNBEKANNT",
                            "%.2f" % e.confidence, e.outcome.value, e.duration_ms,
                            "" if e.feedback_ms is None else e.feedback_ms])
        return len(events)

    def close(self) -> None:
        self.conn.close()

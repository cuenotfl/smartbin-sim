"""Dashboard (ADR-005): statistics per day and Fraktion, CSV download, reset."""
import datetime
import queue
import time

import pytest

from smartbin.adapters.sqlite_store import SqliteEventStore
from smartbin.core.model import Fraction, Outcome, ThrowEvent


def ts(day_offset: int, hour: int = 12) -> float:
    d = datetime.date.today() - datetime.timedelta(days=day_offset)
    return time.mktime(datetime.datetime(d.year, d.month, d.day, hour).timetuple())


def ev(t: float, chosen: Fraction, outcome: Outcome, feedback_ms=400) -> ThrowEvent:
    return ThrowEvent(t, "1-1", 1, chosen, Fraction.ALU, 0.9, outcome, 1500, feedback_ms)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "smartbin.db")
    store = SqliteEventStore(path)
    store.save(ev(ts(0), Fraction.PET, Outcome.KORREKT, 200))
    store.save(ev(ts(0), Fraction.PET, Outcome.FEHLWURF_UMGELENKT, 600))
    store.save(ev(ts(0), Fraction.ALU, Outcome.ABGEBROCHEN, None))
    store.save(ev(ts(1), Fraction.PET, Outcome.KORREKT))
    store.save(ev(ts(40), Fraction.REST, Outcome.UNBEKANNT))
    return path


def test_daily_stats_per_day_and_fraction(db):
    rows = SqliteEventStore(db, readonly=True).daily_stats()
    today = datetime.date.today().isoformat()
    assert [(r["tag"], r["fraktion"]) for r in rows][:2] == [(today, "PET"), (today, "ALU")]
    pet = rows[0]
    assert pet["total"] == 2
    assert pet["KORREKT"] == 1 and pet["FEHLWURF_UMGELENKT"] == 1 and pet["ABGEBROCHEN"] == 0
    assert pet["rueckmeldung_avg_ms"] == 400
    assert rows[1]["rueckmeldung_avg_ms"] is None
    assert len(rows) == 4


def test_daily_stats_limited_to_recent_days(db):
    rows = SqliteEventStore(db, readonly=True).daily_stats(days=14)
    assert {r["fraktion"] for r in rows} == {"PET", "ALU"}   # REST row is 40 days old


def test_readonly_store_cannot_write(db):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        SqliteEventStore(db, readonly=True).save(ev(ts(0), Fraction.PET, Outcome.KORREKT))


# ------------------------------------------------------------------ web app
from smartbin.adapters.dashboard import StationControl, create_app  


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    def make(db_path, control=None):
        return TestClient(create_app(db_path, control))
    return make


class FakeControl(StationControl):
    def __init__(self):
        self.resets = []

    def modules(self):
        return [{"id": 1, "fraktion": "PET", "online": True, "stoerung": False},
                {"id": 2, "fraktion": "ALU", "online": True, "stoerung": True}]

    def reset_fault(self, module_id):
        if module_id not in (1, 2):
            return False
        self.resets.append(module_id)
        return True


def test_page_shows_stats_and_reset_button_only_for_faulted_module(db, client):
    r = client(db, FakeControl()).get("/")
    assert r.status_code == 200
    assert "STÖRUNG" in r.text and "Fehlwurf umgelenkt" in r.text
    assert "/modules/2/reset" in r.text and "/modules/1/reset" not in r.text


def test_api_stats(db, client):
    client = client(db, FakeControl())
    rows = client.get("/api/stats").json()
    assert rows[0]["fraktion"] == "PET" and rows[0]["total"] == 2
    assert len(client.get("/api/stats?days=60").json()) == 4


def test_csv_download(db, client):
    r = client(db).get("/statistik.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("zeit;vorgang;modul") and len(lines) == 6


def test_reset_button_queues_command(db, client):
    control = FakeControl()
    client = client(db, control)
    r = client.post("/modules/2/reset", follow_redirects=False)
    assert r.status_code == 303
    assert client.post("/api/modules/2/reset").status_code == 202
    assert client.post("/api/modules/9/reset").status_code == 404
    assert control.resets == [2, 2]


def test_readonly_dashboard_has_no_reset(db, client):
    client = client(db)
    assert client.post("/api/modules/1/reset").status_code == 503
    assert "Nur Lesemodus" in client.get("/").text


def test_app_bridge_resets_through_core_loop(make_station):
    """The real bridge only queues: the core sees the reset in its own loop."""
    from smartbin.app import RESET, QueuedStationControl
    st = make_station()
    inbox = queue.Queue()
    control = QueuedStationControl(st.core, inbox)
    st.core.status[1].faulted = True
    control.refresh(st.core)
    assert [m["stoerung"] for m in control.modules()][0] is True
    assert control.reset_fault(1) and not control.reset_fault(99)
    assert inbox.get_nowait() == (RESET, 1)
    assert inbox.empty()

import os

import pytest

from smartbin.adapters.sqlite_store import SqliteEventStore
from smartbin.core.model import Fraction, Outcome, ThrowEvent


def event(i):
    return ThrowEvent(1700000000.0 + i, "1-%d" % i, 1, Fraction.PET, Fraction.ALU, 0.8,
                      Outcome.FEHLWURF_UMGELENKT, 1500, 300)


def test_events_survive_restart(tmp_path):  # KANN-05
    path = str(tmp_path / "smartbin.db")
    store = SqliteEventStore(path)
    for i in range(3):
        store.save(event(i))
    del store                       # no close(): simulates power loss after commit
    assert len(SqliteEventStore(path).all()) == 3


def test_csv_export_for_excel(tmp_path):  # KANN-04
    store = SqliteEventStore()
    store.save(event(1))
    path = str(tmp_path / "out.csv")
    assert store.export_csv(path) == 1
    with open(path, encoding="utf-8") as fh:
        header, row = fh.read().strip().split("\n")
    assert header.startswith("zeit;vorgang;modul")
    assert ";FEHLWURF_UMGELENKT;" in row


def test_onnx_classifier_pipeline_with_fake_session():
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    import io

    from smartbin.adapters.onnx_classifier import OnnxClassifier

    class In:
        name = "input"

    class FakeSession:
        def get_inputs(self):
            return [In()]

        def run(self, _, feeds):
            x = feeds["input"]
            assert x.shape == (1, 3, 224, 224)
            return [np.array([[0.1, 3.0, 0.2]])]   # ALU, PET, REST

    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (200, 30, 30)).save(buf, format="JPEG")
    c = OnnxClassifier("unused", session=FakeSession())
    r = c.classify(buf.getvalue())
    assert r.fraction == Fraction.PET and r.confidence > 0.8

"""Evaluates a simulation run against the MUSS criteria of the project brief.

Ground truth comes from the simulated world (where did each item really end
up, when did the LED really light up); the core's own statistics are joined
via the vorgang id.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from smartbin.core.model import Outcome

from .station import PhysicalStation


def _pct(a: int, b: int) -> Optional[float]:
    return None if b == 0 else a / b


def _quantile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


@dataclass
class Criterion:
    key: str
    name: str
    value: Optional[float]
    target: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    throws: int
    criteria: List[Criterion]
    counts: Dict[str, int] = field(default_factory=dict)
    numbers: Dict[str, Optional[float]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.criteria)

    def to_text(self) -> str:
        lines = ["Smart Bin simulation report", "=" * 27, "Throws: %d" % self.throws, ""]
        for c in self.criteria:
            v = "-" if c.value is None else ("%.1f %%" % (100 * c.value) if c.key != "MUSS-03-max"
                                              else "%.2f s" % c.value)
            lines.append("[%s] %-9s %-44s %9s  (target %s) %s" % (
                "PASS" if c.passed else "FAIL", c.key, c.name, v, c.target, c.detail))
        lines.append("")
        lines.append("Outcomes (core statistics):")
        for k, n in sorted(self.counts.items()):
            lines.append("  %-28s %d" % (k, n))
        lines.append("")
        for k, v in self.numbers.items():
            lines.append("  %-28s %s" % (k, "-" if v is None else ("%.3f" % v)))
        lines.append("")
        lines.append("RESULT: %s" % ("PASS" if self.passed else "FAIL"))
        return "\n".join(lines)


def evaluate(st: PhysicalStation, feedback_limit_s: float = 2.0) -> Report:
    events = {e.vorgang_id: e for e in st.store.all()}
    items = st.items

    # MUSS-01: classification of items that were classified (not aborted before)
    classified = [(i, events.get(i.vorgang_id or "")) for i in items]
    classified = [(i, e) for i, e in classified if e is not None and e.outcome != Outcome.ABGEBROCHEN]
    correct = sum(1 for i, e in classified if e.detected == i.true)

    # MUSS-02: detected misthrows that reached the detected target bin
    detected_mis = [(i, e) for i, e in classified
                    if e.detected is not None and e.detected != e.chosen]
    sorted_ok = sum(1 for i, e in detected_mis if i.final == "BIN:%s" % e.detected.value)

    # MUSS-03: throw -> LED for every detected misthrow into a free module.
    # Items thrown while the module was busy or faulted wait in a queue; they
    # are reported separately (load case), not hidden.
    free = [(i, e) for i, e in detected_mis if i.module_idle_at_throw]
    lat = [i.t_led - i.t_throw for i, e in free if i.t_led is not None]
    missing_led = sum(1 for i, e in free if i.t_led is None)
    within = sum(1 for x in lat if x <= feedback_limit_s)
    busy_lat = [i.t_led - i.t_throw for i, e in detected_mis
                if not i.module_idle_at_throw and i.t_led is not None]

    end_correct = sum(1 for i in items if i.final == "BIN:%s" % i.true.value)
    passive = sum(1 for i in items if i.chosen_module == st.config.module_for(i.true).module_id)
    on_flap = sum(1 for i in items if i.final is None)

    criteria = [
        Criterion("MUSS-01", "Klassifikation korrekt", _pct(correct, len(classified)), ">= 90 %",
                  (_pct(correct, len(classified)) or 0) >= 0.9, "(%d/%d)" % (correct, len(classified))),
        Criterion("MUSS-02", "Erkannte Fehlwuerfe richtig umgelenkt", _pct(sorted_ok, len(detected_mis)),
                  ">= 90 %", (_pct(sorted_ok, len(detected_mis)) or 0) >= 0.9 if detected_mis else True,
                  "(%d/%d)" % (sorted_ok, len(detected_mis))),
        Criterion("MUSS-03", "Rueckmeldung <= %.0f s bei Fehlwurf" % feedback_limit_s,
                  _pct(within, len(free)), "100 %",
                  missing_led == 0 and within == len(free),
                  "(%d/%d, p95 %.2f s, max %.2f s)" % (within, len(free),
                                                      _quantile(lat, 0.95) or 0, max(lat) if lat else 0)),
    ]
    counts: Dict[str, int] = {}
    for e in events.values():
        counts[e.outcome.value] = counts.get(e.outcome.value, 0) + 1
    faults = sum(len(m.faults) for m in st.modules.values())
    numbers = {
        "end_in_correct_bin": _pct(end_correct, len(items)),
        "without_smart_bin": _pct(passive, len(items)),
        "items_still_on_flap": float(on_flap),
        "module_faults": float(faults),
        "safety_stops": float(sum(m.safety_stops for m in st.modules.values())),
        "feedback_p50_s": _quantile(lat, 0.5),
        "feedback_p95_s": _quantile(lat, 0.95),
        "misthrows_into_busy_module": float(len(detected_mis) - len(free)),
        "feedback_max_busy_module_s": max(busy_lat) if busy_lat else None,
        "module_retries": float(sum(m.retries for m in st.modules.values())),
        "crc_errors": float(sum(d.crc_errors for d in st.gateway.decoders.values())
                            + sum(m.decoder.crc_errors for m in st.modules.values())),
    }
    return Report(len(items), criteria, counts, numbers)

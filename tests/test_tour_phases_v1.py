"""TUR FAZ SÜRELERİ (2026-10-01) — yalnız ölçüm.

`tour()` her numaralı adımın sonunda bir faz işareti koyar; süreler `health.json["phases"]`a yazılır ve tek bir
"tur fazları: ..." satırı loglanır. Bu testler üç şeyi kilitler:

1. Anahtar kümesi sabit ve tur sırasıyla; değerler negatif değil; toplam `seconds` ile uyumlu.
2. Saat `time.time`: dondurulmuş saatte bütün fazlar 0.0 (altın bayt-eşitlik testleri health.json'u koşular arası karşılaştırır).
3. health.json okuyan her tüketici yeni anahtarı tolere eder (panel, `health` CLI, evren raporu, bildirim kurtarma yolu).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.ops.tour_phases import PHASES, SUB_PHASES, TourPhases  # noqa: E402

KEYS = [k for k, _ in PHASES] + [k for k, _, _ in SUB_PHASES]


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_lap_timer_accumulates_in_order_with_a_fixed_key_set():
    c = _Clock()
    ph = TourPhases(clock=c)
    c.t += 1.24
    ph.lap("prep")
    c.t += 10.0
    ph.lap("symbols")
    ph.add_sub("pattern_evidence", 3.33)
    c.t += 5.0
    ph.lap("coin_heads")
    c.t += 0.5
    ph.lap("coin_heads")                         # aynı faz iki kez işaretlenirse toplanır
    h = ph.as_health()
    assert list(h) == KEYS
    assert h["prep"] == 1.2 and h["symbols"] == 10.0 and h["coin_heads"] == 5.5 and h["pattern_evidence"] == 3.3
    assert h["scan"] == 0.0 and h["obsidian"] == 0.0
    line = ph.log_line()
    assert line.startswith("tur fazları: hazırlık 1.2s · tarama 0.0s · semboller 10.0s · coin head 5.5s (pattern kanıtı 3.3s)")
    assert line.endswith("· toplam 16.7s")


def test_a_clock_going_backwards_never_produces_a_negative_phase():
    c = _Clock()
    ph = TourPhases(clock=c)
    c.t -= 5.0
    ph.lap("prep")
    ph.add_sub("pattern_evidence", -1.0)
    assert ph.as_health()["prep"] == 0.0 and ph.as_health()["pattern_evidence"] == 0.0


def test_a_broken_clock_does_not_raise():
    def bad():
        raise RuntimeError("saat yok")
    ph = TourPhases(clock=lambda: 0.0)
    ph._clock = bad
    ph.lap("prep")                               # istisna YOK
    assert ph.as_health()["prep"] == 0.0


def test_the_tour_writes_phases_to_health_and_logs_one_line(tmp_path, monkeypatch, caplog):
    eng = TE._engine(tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="tradingbot.engine_v3"):
        summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    h = json.loads((eng.cfg.state_path / "health.json").read_text(encoding="utf-8"))
    ph = h["phases"]
    assert list(ph) == KEYS
    assert all(isinstance(v, float) and v >= 0.0 for v in ph.values())
    total = sum(ph[k] for k, _ in PHASES)
    # her faz 0,1 sn'ye yuvarlanır: toplam `seconds`tan en çok faz sayısı × 0,05 sapar
    assert abs(total - h["seconds"]) <= 0.05 * len(PHASES) + 0.1, (total, h["seconds"])
    assert ph["pattern_evidence"] <= ph["coin_heads"] + 0.1
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tur fazları:")]
    assert len(lines) == 1 and "semboller" in lines[0] and "coin head" in lines[0]
    assert summ["seconds"] >= h["seconds"]


def test_frozen_clock_gives_identical_phases_across_runs(tmp_path, monkeypatch):
    """Altın testler health.json'u iki koşu arasında bayt bayt karşılaştırır (dondurulmuş saat)."""
    import pytest
    time_machine = pytest.importorskip("time_machine")
    from datetime import UTC, datetime
    with time_machine.travel(datetime.now(UTC), tick=False):
        eng = TE._engine(tmp_path, monkeypatch)
        eng.tour(do_scan=False, obsidian=False, charts=False)
    h = json.loads((eng.cfg.state_path / "health.json").read_text(encoding="utf-8"))
    assert set(h["phases"].values()) == {0.0} and h["seconds"] == 0.0


# ------------------------------------------------------------------ tüketiciler yeni anahtarı tolere eder
def _health_with_phases() -> dict:
    return {"state": "HEALTHY", "at": "2026-10-01T09:00:00+00:00", "run_id": "r1", "seconds": 412.3, "symbols": 40,
            "decisions": 40, "opened": 0, "closed": 0, "kill_trips": [], "mode": "PAPER", "profile": "PAPER_RESEARCH",
            "phases": {k: 1.0 for k in KEYS}}


def test_dashboard_pages_render_with_phases_in_health(tmp_path):
    from test_dashboard_terminal_v1 import _client, _w
    _w(tmp_path, "health.json", _health_with_phases())
    c = _client(tmp_path)
    for url in ("/", "/health", "/health/ready", "/metrics", "/api/overview", "/api/live/health"):
        r = c.get(url)
        assert r.status_code in (200, 503), (url, r.status_code)
    assert "HEALTHY" in c.get("/health").text


def test_cli_health_universe_report_and_recovery_tolerate_phases(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from tradingbot.universe_report import resources_section
    st = tmp_path / "state"
    st.mkdir()
    (st / "health.json").write_text(json.dumps(_health_with_phases()), encoding="utf-8")
    res = resources_section(st, tmp_path, health=_health_with_phases(), llm=None, universe_size=40)
    assert res["last_tour_seconds"] == 412.3
    from tradingbot import cli_v3
    cfg = SimpleNamespace(state_path=st, v3=SimpleNamespace(monitoring=SimpleNamespace(heartbeat_stale_s=10**9)))
    assert cli_v3.cmd_health(cfg, SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "phases" in out and "HEALTHY" in out

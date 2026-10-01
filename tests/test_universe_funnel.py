"""Dinamik evren + tier hunisi kabul testleri (görev §15/19-27).

Panel top listesi analiz kapsamı DEĞİLDİR: bot 40-60 uygun sembolü ucuz tarar (Tier A),
umut vadedenleri derinleştirir (Tier B), gerçek fırsatları sıralar (Tier C) ve HER Tier-A
adayı için bounded bir journal kaydı üretir. Sayı yapay doldurulmaz.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.universe_eval import build_eval_universe


# ------------------------------------------------------------------ üretim şemalı fixture

@dataclass
class _Row:
    symbol: str
    perp: str = ""
    price: float = 100.0
    chg24_pct: float = 1.0
    vol24_usdt: float = 0.0
    score: int = 0
    atr_pct: float = 1.2
    rsi_4h: float = 55.0
    error: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class _Scan:
    generated_at: str = "2026-08-25T12:00:00+00:00"
    universe: int = 0
    scanned: int = 0
    flagged: int = 0
    setups: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    min_volume: float = 20e6
    seconds: float = 1.0

    def to_dict(self) -> dict:
        return {"generated_at": self.generated_at, "universe": self.universe,
                "scanned": self.scanned, "flagged": self.flagged,
                "setups": [r.to_dict() for r in self.setups],
                "rows": [r.to_dict() for r in self.rows],
                "min_volume": self.min_volume, "seconds": self.seconds}


def _mk_scan(n_ok: int = 55, n_err: int = 5, top_n: int = 12, flag: int = 60) -> _Scan:
    rows = [_Row(symbol=f"C{i:02d}/USDT", perp=f"C{i:02d}/USDT:USDT",
                 vol24_usdt=1e9 - i * 1e7, score=90 - i) for i in range(n_ok)]
    flagged = [r for r in rows if r.score >= flag]
    return _Scan(universe=n_ok + n_err, scanned=n_ok, flagged=len(flagged),
                 setups=flagged[:top_n], rows=rows)


# ================================================================== 19-21) evren + tier sınırları

def test_19_production_schema_fixture_yields_40plus_universe():
    scan = _mk_scan(n_ok=55)
    doc = build_eval_universe(scan, target_min=40, target=50, target_max=60,
                              run_id="r1", now_iso="2026-08-25T12:00:00+00:00",
                              flag_score=60, deep_symbols=tuple(r.symbol for r in scan.setups))
    c = doc["counts"]
    assert 40 <= c["eligible"] <= 60
    assert c["eligible"] == 55 and doc["below_target_reason"] is None
    assert c["data_error"] == 5, "veri hatası verenler dürüstçe sayılmalı"
    for s in doc["symbols"]:
        assert s["tier"] in ("A", "B") and s["rank"] >= 1 and s["vol24_usdt"] > 0


def test_20_21_tier_a_fully_journaled_tier_bc_bounded(tmp_path: Path, monkeypatch):
    """Tier A'nın TAMAMI journal paydasına girer; B/C sınırlı kısa listedir."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    scan = _mk_scan(n_ok=48)
    from tradingbot.core import iso, utc_now
    doc = build_eval_universe(scan, target_min=40, target=50, target_max=60,
                              run_id="rX", now_iso=iso(utc_now()), flag_score=60,
                              deep_symbols=tuple(eng.cfg.coins))
    eng._eval_universe = doc
    eng.tour(do_scan=False, obsidian=False, charts=False)

    rows = list(eng.decision_journal.iter_all_rows())
    deep = [r for r in rows if r.get("kind") == "decision"
            and r.get("outcome_kind") != "SCREENED_OUT"]
    screened = [r for r in rows if r.get("outcome_kind") == "SCREENED_OUT"]
    n_deep_syms = len({r["symbol"] for r in deep})
    # payda: evrendeki her sembol ya derin kayıt ya stage-1 kayıt almalı
    uni_syms = {s["symbol"] for s in doc["symbols"]}
    journaled_syms = {r["symbol"] for r in deep} | {r["symbol"] for r in screened}
    assert uni_syms <= journaled_syms, "Tier A'da kayıtsız sembol KALAMAZ"
    assert eng._evaluated_last_tour == eng._journaled_last_tour, "kapsam 1.0 olmalı"
    # stage-1 kayıtlar bounded (ham mum/feature yok) ve gerekçeli
    for r in screened:
        assert len(json.dumps(r)) < 2_000, "stage-1 kaydı KÜÇÜK olmalı"
        assert r.get("outcome_reason") in ("BELOW_FLAG_SCORE", "NOT_IN_TOP_N",
                                          "NOT_DEEP_ANALYZED")
        assert r.get("tier") == "A" and r.get("universe_artifact_sha")
        assert "features" not in r or not r["features"]
    # Tier B/C sınırlı
    t = eng._tier_counts
    assert t["tier_b_deep"] <= 25 and t["tier_b_deep"] == n_deep_syms
    assert t["tier_c_ranked"] <= t["tier_b_deep"]
    # huni dosyasına yazıldı
    fun = json.loads((Path(eng.cfg.state_path) / "decision_funnel.json")
                     .read_text(encoding="utf-8"))
    assert fun["tiers"]["tier_a_universe"] == 48
    assert fun["coverage"]["journaled"] == fun["coverage"]["evaluated"]


# ================================================================== 22) panel vs evren

def test_22_dashboard_shows_top15_but_api_carries_full_universe(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    scan = _mk_scan(n_ok=50)
    from tradingbot.core import atomic_write_json, iso, utc_now
    doc = build_eval_universe(scan, run_id="rD", now_iso=iso(utc_now()), flag_score=60,
                              deep_symbols=tuple(eng.cfg.coins))
    st = Path(eng.cfg.state_path)
    atomic_write_json(st / "universe_eval.json", doc)
    eng._eval_universe = doc
    eng.tour(do_scan=False, obsidian=False, charts=False)

    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    r = client.get("/api/universe")
    assert r.status_code == 200
    body = r.json()
    assert body["evaluated_universe"] == 50
    assert body["displayed_top"] <= 15
    assert len(body["symbols"]) == 50, "API TÜM evreni taşımalı (arama/filtre istemcide)"
    assert body["tiers"]["tier_a_universe"] == 50
    assert client.get("/quant").status_code == 200
    # 42) mutasyon metotları 405
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/api/universe").status_code == 405


def test_22b_missing_universe_snapshot_never_500s(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    st = Path(eng.cfg.state_path)
    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    r = client.get("/api/universe")
    assert r.status_code == 200 and r.json()["available"] is False
    (st / "universe_eval.json").write_text("{ bozuk", encoding="utf-8")
    assert client.get("/api/universe").status_code == 200


# ================================================================== 23) yapay doldurma yok

def test_23_insufficient_eligibility_is_reported_not_padded():
    scan = _mk_scan(n_ok=22, n_err=30)
    doc = build_eval_universe(scan, target_min=40, run_id="r2",
                              now_iso="2026-08-25T12:00:00+00:00", flag_score=60)
    assert doc["counts"]["eligible"] == 22, "sayı 40'a YAPAY tamamlanamaz"
    assert doc["below_target_reason"] and "INSUFFICIENT" in doc["below_target_reason"]
    assert doc["counts"]["data_error"] == 30


# ================================================================== 24) deterministik snapshot

def test_24_snapshot_is_point_in_time_and_deterministic():
    scan = _mk_scan(n_ok=45)
    kw = dict(target_min=40, target=50, target_max=60, run_id="rZ",
              now_iso="2026-08-25T12:00:00+00:00", flag_score=60,
              deep_symbols=("C00/USDT",))
    d1 = build_eval_universe(scan, **kw)
    d2 = build_eval_universe(scan, **kw)
    assert d1 == d2 and d1["artifact_sha"] == d2["artifact_sha"]
    assert "point_in_time" in d1["provenance"]
    assert d1["provenance"]["extra_api_calls"] == 0
    # değişim takibi
    scan2 = _mk_scan(n_ok=44)
    d3 = build_eval_universe(scan2, prev=d1, **kw)
    assert d3["changes"]["removed"] == ["C44/USDT"]
    assert d3["changes"]["prev_as_of"] == d1["as_of"]


# ================================================================== 25) cadence/rate-limit sözleşmesi

def test_25_universe_refresh_makes_no_new_api_calls(tmp_path: Path, monkeypatch):
    """Evren snapshot'ı tarayıcının MEVCUT verisinden türer; hiçbir sağlayıcı çağrısı yapmaz."""
    import tradingbot.universe_eval as ue
    calls = {"n": 0}

    class Boom:
        def __getattr__(self, name):
            calls["n"] += 1
            raise AssertionError("evren üretimi API'ye DOKUNAMAZ")

    scan = _mk_scan(n_ok=42)
    doc = ue.build_eval_universe(scan, run_id="r", now_iso="t", flag_score=60)
    assert calls["n"] == 0 and doc["counts"]["eligible"] == 42
    src = Path("tradingbot/universe_eval.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.strip().startswith(("import ", "from "))]
    for banned in ("requests", "urllib", "httpx", "ccxt"):
        assert not any(banned in ln for ln in imports),             f"universe_eval ağ istemcisi import edemez: {banned}"
    assert "fetch_" not in src, "universe_eval sağlayıcı çağrısı yapamaz"


# ================================================================== 26) tek coin turu düşürmez

def test_26_single_symbol_failure_does_not_kill_tour(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    victim = eng.cfg.coins[1]
    import tradingbot.learn.snapshot as snapmod
    orig = snapmod.build_snapshot

    def flaky(*a, **kw):
        sym = kw.get("symbol") or (a[0] if a else "")
        if str(sym) == victim:
            raise RuntimeError("sembol verisi patladı")
        return orig(*a, **kw)

    monkeypatch.setattr(snapmod, "build_snapshot", flaky)
    summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert summ is not None, "tek sembol arızası TURU DÜŞÜREMEZ"
    rows = [r for r in eng.decision_journal.iter_all_rows() if r.get("kind") == "decision"]
    assert {r["symbol"] for r in rows} >= set(eng.cfg.coins), \
        "arızalı sembol dahil HER aday yine kayıt almalı"


# ================================================================== 27) tur üst üste binmez

def test_27_single_writer_contract_prevents_cycle_overlap():
    """Worker tek süreçtir (SingletonLock + authority) ve tur döngüsü sıralıdır."""
    src = Path("tradingbot/cli.py").read_text(encoding="utf-8")
    assert "SingletonLock" in src and "AlreadyRunningError" in src
    assert "authority_check" in src
    # motor giriş kilidi: aday değerlendirme→fill tek seri kritik bölge
    esrc = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    assert "_entry_lock" in esrc

"""Outcome Learning — Long-Term Retrieval Audit.

Kapatılan boşluk: saklama kayıpsızdı ama canlı retrieval `HOT_ONLY` idi — bir gölge sonuç
aktif `shadow_book.json` penceresinden çıkıp arşive taşındığı anda karar etkisinden düşüyordu.

Sözleşme: **her outcome kalıcı saklanmalı VE sonraki benzer coin/yön/rejim/setup kararında
no-lookahead ve bounded kurallarıyla kullanılabilir kalmalıdır.**

Testler gerçek `ShadowBook`, gerçek `SegmentArchive`, gerçek `ExperienceIndexStore`, gerçek
`prepare_pool`/`query_pool` ve gerçek `TradingEngineV3.tour()` zincirini kullanır.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.learn.experience import (REAL_PAPER, SHADOW, prepare_pool, query_pool)
from tradingbot.learn.experience_index import (SCOPE_DEGRADED, SCOPE_FULL_HISTORY,
                                               SCOPE_HOT_ONLY, SCOPE_HOT_PLUS_INDEXED,
                                               ExperienceIndexStore)
from tradingbot.learn.influence import InfluenceConfig, apply_influence, weighted_adjustment
from tradingbot.learn.journal_archive import SegmentArchive
from tradingbot.learn.shadow import ShadowBook, ShadowTrade

UTC = timezone.utc


def _ms(y=2026, mo=3, d=2, h=0) -> int:
    return int(datetime(y, mo, d, h, tzinfo=UTC).timestamp() * 1000)


# ------------------------------------------------------------------ gerçek tipli kurulum

def _book(tmp_path: Path, *, max_trades: int = 3) -> tuple[ShadowBook, SegmentArchive]:
    arc = SegmentArchive(tmp_path / "shadow_archive", stream_id="shadow_book",
                         record_schema_version="shadow_trade_v1")
    book = ShadowBook(tmp_path / "shadow_book.json", archive=arc)
    book.MAX_TRADES = max_trades
    return book, arc


def _trade(i: int, *, symbol="BTC/USDT", direction="LONG", variant="as_planned",
           r: float | None = 1.0, labeled_h: int = 0, created_h: int | None = None,
           outcome: dict | None = None) -> ShadowTrade:
    o = outcome if outcome is not None else ({"r_multiple": r} if r is not None else None)
    return ShadowTrade(
        id=f"sh{i}", plan_id=f"p{i}", symbol=symbol, market_type="futures",
        direction=direction, created_at=f"2026-03-01T{(created_h if created_h is not None else i) % 24:02d}:00:00+00:00",
        entry=100.0, stop=95.0, targets=[110.0], horizon_bars=4, variant=variant,
        reason_not_opened=["RISK_ENGINE_BLOCKED"], label_ts="2026-03-02T00:00:00+00:00",
        outcome=o, labeled_at=f"2026-03-02T{labeled_h % 24:02d}:00:00+00:00" if o else None)


def _store(tmp_path: Path, arc: SegmentArchive, **kw) -> ExperienceIndexStore:
    return ExperienceIndexStore(tmp_path / "experience_index", arc, **kw)


def _pool(book: ShadowBook, store: ExperienceIndexStore | None, memory_rows=None):
    hist = store.rows() if store is not None else []
    return prepare_pool(memory_rows=list(memory_rows or []),
                        shadow_trades=[t.to_dict() for t in book.trades],
                        indexed_history=hist)


# ================================================================== 1+2+3 çekirdek boşluk

def test_1_2_3_archived_shadow_outcome_returns_to_live_retrieval(tmp_path: Path):
    """1) arşive taşınır 2) aktifte YOK 3) sonraki aynı coin/yön/setup kararında BULUNUR."""
    book, arc = _book(tmp_path, max_trades=2)
    for i in range(9):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()

    # 1) taşındı
    assert book.stats()["archived"] == 7 and arc.stats()["n_segments"] >= 1
    # 2) aktif dosyada YOK
    active_ids = {t.id for t in book.trades}
    on_disk = {t["id"] for t in json.loads(
        (tmp_path / "shadow_book.json").read_text(encoding="utf-8"))["trades"]}
    assert active_ids == on_disk and len(active_ids) == 2
    assert "sh0" not in active_ids and "sh0" not in on_disk

    # BOŞLUK KANITI: indekssiz havuz yalnız aktif pencereyi görür
    hot_only = _pool(book, None)
    assert len(hot_only.experiences) == 2

    # 3) indeksli havuz arşivlenmiş sonucu geri getirir
    store = _store(tmp_path, arc)
    store.refresh()
    pooled = _pool(book, store)
    assert len(pooled.experiences) == 9
    hits = query_pool(pooled, {"symbol": "BTC/USDT", "direction": "LONG",
                               "setup_type": "as_planned"},
                      as_of_ms=_ms(d=3), top_k=20)
    assert len(hits) == 9, "arşivlenmiş sonuçlar benzer kararda görünmeli"
    assert store.stats()["retrieval_scope"] == SCOPE_FULL_HISTORY


# ================================================================== 4) SHADOW baseline

def test_4_shadow_learned_diverges_but_effective_equals_baseline(tmp_path: Path):
    """Arşivden gelen kanıt counterfactual olarak HESAPLANIR; SHADOW'da baseline DEĞİŞMEZ."""
    book, arc = _book(tmp_path, max_trades=1)
    for i in range(8):
        book.trades.append(_trade(i, r=2.0, labeled_h=i))
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    pool = _pool(book, store)
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=_ms(d=3), top_k=8)
    assert len(hits) == 8

    cfg = InfluenceConfig(mode="SHADOW")
    adj = weighted_adjustment(hits, baseline=0.60, cfg=cfg, prior_leaf_n=0.0)
    out = apply_influence(adj, cfg=cfg, mode_value="PAPER")
    assert adj["learned"] != pytest.approx(0.60), "arşiv kanıtı counterfactual etki üretmeli"
    assert out["applied"] is False
    assert out["effective"] == pytest.approx(0.60), "SHADOW baseline'ı DEĞİŞTİREMEZ"
    assert "MODE_SHADOW" in out["blockers"]


# ================================================================== 5) çift sayım yok

def test_5_duplicate_across_active_and_archive_counted_once(tmp_path: Path):
    """Aynı outcome hem arşivde hem aktifte görünse bile TEK kez sayılır."""
    book, arc = _book(tmp_path, max_trades=2)
    for i in range(7):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()

    # Arşivlenmiş bir kaydı AKTİF listeye geri koy (çökme sonrası yeniden mühürleme senaryosu)
    book.trades.insert(0, _trade(0, labeled_h=0))
    pool = _pool(book, store)
    ids = [e.outcome_id for e in pool.experiences]
    assert len(ids) == len(set(ids)) == 7, "aynı kimlik iki deneyim üretmemeli"

    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=_ms(d=3), top_k=50)
    adj = weighted_adjustment(hits, baseline=0.6, cfg=InfluenceConfig(), prior_leaf_n=0.0)
    assert adj["n_experience"] == 7
    assert len(set(adj["counted_outcome_ids"])) == 7


# ================================================================== 6) real > shadow

def test_6_real_paper_beats_archived_shadow_with_same_identity(tmp_path: Path):
    """Aynı kimlikte gerçek fill varsa arşivlenmiş gölge sürüm havuza GİRMEZ."""
    book, arc = _book(tmp_path, max_trades=1)
    for i in range(5):
        book.trades.append(_trade(i, r=-1.0, created_h=0, variant="as_planned",
                                  labeled_h=i, symbol="ETH/USDT"))
    # created_at ve variant aynı → hepsi AYNI kimlik; ilki aktif, kalanı arşive gider
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()

    mem = [{"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "as_planned",
            "opened_at": "2026-03-01T00:00:00+00:00", "recorded_at": "2026-03-01T00:00:00+00:00",
            "features": {"atr_pct": 0.3, "rr": 2.0},
            "outcome": {"r_multiple": 2.5, "closed_at": "2026-03-02T00:00:00+00:00",
                        "opened_at": "2026-03-01T00:00:00+00:00"}}]
    pool = _pool(book, store, memory_rows=mem)
    same = [e for e in pool.experiences if e.symbol == "ETH/USDT"]
    assert len(same) == 1, "tek kimlik → tek deneyim"
    assert same[0].source == REAL_PAPER and same[0].r_multiple == pytest.approx(2.5)
    assert same[0].weight == pytest.approx(1.0), "gerçek fill tam ağırlık taşır"


# ================================================================== 7) no-lookahead

def test_7_future_labeled_archived_shadow_is_invisible_before_as_of(tmp_path: Path):
    book, arc = _book(tmp_path, max_trades=1)
    for i in range(6):
        book.trades.append(_trade(i, labeled_h=i))          # 2026-03-02T0i:00
    # gelecekte etiketlenmiş kayıt
    future = _trade(99, labeled_h=0)
    future.labeled_at = "2026-03-09T00:00:00+00:00"
    book.trades.append(future)
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    pool = _pool(book, store)

    as_of = _ms(d=2, h=3)                                    # 2026-03-02T03:00
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=as_of, top_k=50)
    assert hits, "as_of'tan önce etiketlenmiş arşiv kanıtı görünmeli"
    for h in hits:
        assert h.label_ts_ms is not None and h.label_ts_ms <= as_of
    assert all(h.outcome_id != "" for h in hits)
    # gelecekteki kayıt as_of sonrasında görünür olmalı (elenmesi zamansaldır, kalıcı değil)
    later = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                              "setup_type": "as_planned"}, as_of_ms=_ms(d=10), top_k=50)
    assert len(later) > len(hits)


def test_7b_row_without_label_time_is_fail_closed_out_of_index(tmp_path: Path):
    book, arc = _book(tmp_path, max_trades=1)
    for i in range(4):
        book.trades.append(_trade(i, labeled_h=i))
    bad = _trade(50, labeled_h=0)
    bad.labeled_at = None                                    # etiket zamanı YOK
    bad.label_ts = "not-a-timestamp"
    book.trades.append(bad)
    book.save()
    for i in range(60, 64):                                  # bozuk kaydı arşive ittir
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    assert all(e.label_ts_ms is not None for e, _, _ in store.rows())
    assert store.stats()["skipped_rows"] >= 1


# ================================================================== 8) bozuk checksum

def test_8_corrupt_segment_is_not_indexed(tmp_path: Path):
    book, arc = _book(tmp_path, max_trades=1)
    for i in range(5):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    for i in range(5, 10):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    segs = arc.segments()
    assert len(segs) >= 2

    victim = arc.segments_dir / segs[0]["file"]
    victim.write_bytes(gzip.compress(b'{"id": "TAMPERED", "symbol": "BTC/USDT",'
                                     b' "outcome": {"r_multiple": 99.0},'
                                     b' "labeled_at": "2026-03-02T00:00:00+00:00"}\n'))
    store = _store(tmp_path, arc)
    res = store.refresh()
    assert res["corrupt"] >= 1
    st = store.stats()
    assert st["corrupt_segments"] >= 1 and st["index_health"] == "DEGRADED"
    assert st["retrieval_scope"] == SCOPE_DEGRADED, "bozuk indeks dürüstçe DEGRADED raporlanmalı"
    ids = [e.outcome_id for e, _, _ in store.rows()]
    assert not any("TAMPERED" in i for i in ids)


# ================================================================== 9) bozuk indeks → baseline

def test_9_broken_index_does_not_break_baseline(tmp_path: Path):
    book, arc = _book(tmp_path, max_trades=2)
    for i in range(8):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    assert len(store.rows()) > 0

    (tmp_path / "experience_index" / "manifest.json").write_text("{ bozuk", encoding="utf-8")
    broken = _store(tmp_path, arc)
    assert broken.manifest()["health"] == "DEGRADED"
    rows = broken.rows()                                     # istisna YOK
    pool = _pool(book, broken)
    assert len(pool.experiences) >= 2, "baseline (aktif pencere) korunmalı"
    assert isinstance(rows, list)
    assert broken.stats()["retrieval_scope"] in (SCOPE_DEGRADED, SCOPE_HOT_ONLY)


def test_9b_missing_index_reports_hot_only_not_a_lie(tmp_path: Path):
    _, arc = _book(tmp_path, max_trades=2)
    store = _store(tmp_path, arc)
    st = store.stats()
    assert st["indexed_experiences"] == 0
    assert st["retrieval_scope"] == SCOPE_HOT_ONLY, "hazır olmayan indeks genişletilmiş kapsam YAZAMAZ"


# ================================================================== 10+11+12 rebuild/determinizm

def _fingerprint(store: ExperienceIndexStore) -> str:
    import hashlib
    h = hashlib.sha256()
    for e, v, _ in sorted(store.rows(), key=lambda t: t[0].outcome_id):
        h.update(json.dumps([e.outcome_id, e.source, e.symbol, e.direction, e.setup,
                             e.r_multiple, round(e.weight, 9), e.label_ts_ms,
                             [round(x, 6) for x in v]], sort_keys=True).encode())
    return h.hexdigest()


def test_10_index_deleted_is_deterministically_rebuilt_from_archive(tmp_path: Path):
    import shutil
    book, arc = _book(tmp_path, max_trades=2)
    for i in range(20):
        book.trades.append(_trade(i, r=(1.0 if i % 2 else -1.0), labeled_h=i))
        if i % 6 == 0:
            book.save()
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    before = _fingerprint(store)
    n_before = store.stats()["indexed_experiences"]
    assert n_before > 0

    shutil.rmtree(tmp_path / "experience_index")             # TÜREV veri silinir
    rebuilt = _store(tmp_path, arc)
    rebuilt.refresh()
    assert _fingerprint(rebuilt) == before, "arşivden yeniden kurulum DETERMİNİSTİK olmalı"
    assert rebuilt.stats()["indexed_experiences"] == n_before

    explicit = _store(tmp_path, arc)
    explicit.rebuild()
    assert _fingerprint(explicit) == before
    assert explicit.manifest()["last_rebuild_at"]


def test_11_reprocessing_same_segment_does_not_change_counts(tmp_path: Path):
    book, arc = _book(tmp_path, max_trades=2)
    for i in range(12):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    store = _store(tmp_path, arc)
    first = store.refresh()
    n1 = store.stats()["indexed_experiences"]
    seg1 = store.stats()["processed_segments"]
    assert first["new_segments"] >= 1

    for _ in range(3):
        again = store.refresh()
        assert again["new_segments"] == 0, "AYNI segment ikinci kez işlenmemeli"
        assert again["new_rows"] == 0
    assert store.stats()["indexed_experiences"] == n1
    assert store.stats()["processed_segments"] == seg1
    assert len(store.rows()) == n1


def test_12_restart_yields_identical_result_hash(tmp_path: Path):
    book, arc = _book(tmp_path, max_trades=3)
    for i in range(15):
        book.trades.append(_trade(i, r=(2.0 if i % 3 else -1.5), labeled_h=i))
        if i % 5 == 0:
            book.save()
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    q = {"symbol": "BTC/USDT", "direction": "LONG", "setup_type": "as_planned"}
    pool = _pool(book, store)
    before = [(h.outcome_id, h.similarity, h.r_multiple, h.weight)
              for h in query_pool(pool, q, as_of_ms=_ms(d=3), top_k=10)]
    fp = _fingerprint(store)

    # YENİDEN BAŞLATMA: yeni ShadowBook + yeni arşiv + yeni store nesneleri
    arc2 = SegmentArchive(tmp_path / "shadow_archive", stream_id="shadow_book",
                          record_schema_version="shadow_trade_v1")
    book2 = ShadowBook(tmp_path / "shadow_book.json", archive=arc2)
    book2.MAX_TRADES = 3
    store2 = _store(tmp_path, arc2)
    store2.refresh()
    pool2 = _pool(book2, store2)
    after = [(h.outcome_id, h.similarity, h.r_multiple, h.weight)
             for h in query_pool(pool2, q, as_of_ms=_ms(d=3), top_k=10)]
    assert after == before, "restart sonrası sonuç DEĞİŞMEMELİ"
    assert _fingerprint(store2) == fp


# ================================================================== 13) residual double-count

def test_13_hierarchical_prior_residual_protection_still_applies(tmp_path: Path):
    """Arşiv kanıtı prior'da temsil edildiği ölçüde KISILIR — çift sayım korunur."""
    book, arc = _book(tmp_path, max_trades=1)
    for i in range(10):
        book.trades.append(_trade(i, r=2.0, labeled_h=i))
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    pool = _pool(book, store)
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=_ms(d=3), top_k=10)
    cfg = InfluenceConfig()
    no_prior = weighted_adjustment(hits, baseline=0.6, cfg=cfg, prior_leaf_n=0.0)
    heavy = weighted_adjustment(hits, baseline=0.6, cfg=cfg, prior_leaf_n=200.0)
    assert no_prior["residual_share"] == pytest.approx(1.0)
    assert heavy["residual_share"] < 0.2, "prior ağırsa similarity kanalı KISILMALI"
    assert abs(heavy["fraction"]) < abs(no_prior["fraction"]), "çift sayım koruması sürmeli"
    assert abs(no_prior["fraction"]) <= cfg.max_fraction + 1e-9


# ================================================================== 14) maliyet dersi

def test_14_cost_dominated_archived_loss_produces_negative_lesson(tmp_path: Path):
    """Arşivden gelen maliyet-baskın zarar NEGATİF ders üretir (yön doğru)."""
    from tradingbot.learn.experience import cost_sensitivity
    assert cost_sensitivity({"r_multiple": -0.2, "fee_drag_r": 0.3,
                             "funding_drag_r": 0.2, "slippage_drag_r": 0.1}) == "COST_DOMINATED"

    book, arc = _book(tmp_path, max_trades=1)
    for i in range(9):
        book.trades.append(_trade(i, labeled_h=i, outcome={
            "r_multiple": -0.6, "fee_drag_r": 0.25, "funding_drag_r": 0.2,
            "slippage_drag_r": 0.1, "exit_reason": "stop"}))
    book.save()
    store = _store(tmp_path, arc)
    store.refresh()
    pool = _pool(book, store)
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=_ms(d=3), top_k=9)
    assert len(hits) == 9
    adj = weighted_adjustment(hits, baseline=0.60, cfg=InfluenceConfig(), prior_leaf_n=0.0)
    assert adj["signal"] < 0, "zarar kanıtı negatif sinyal üretmeli"
    assert adj["learned"] < adj["baseline"], "arşivlenmiş zarar olasılığı DÜŞÜRMELİ"


# ================================================================== 15) ilk kanıt sıfır değil

def test_15_first_archived_outcome_has_small_but_nonzero_effect(tmp_path: Path):
    """'Yetersiz örnek' öğrenmeyi ENGELLEMEZ: TEK arşiv kanıtı da küçük ama sıfır olmayan etki."""
    book, arc = _book(tmp_path, max_trades=1)
    book.trades.append(_trade(0, r=2.0, labeled_h=0))
    book.trades.append(_trade(1, r=1.0, labeled_h=1))
    book.save()
    assert book.stats()["archived"] == 1
    store = _store(tmp_path, arc)
    store.refresh()
    assert store.stats()["indexed_experiences"] == 1

    only_archived = prepare_pool(memory_rows=[], shadow_trades=[],
                                 indexed_history=store.rows())
    hits = query_pool(only_archived, {"symbol": "BTC/USDT", "direction": "LONG",
                                      "setup_type": "as_planned"},
                      as_of_ms=_ms(d=3), top_k=5)
    assert len(hits) == 1, "tek arşiv kanıtı retrieval'da bulunmalı"
    cfg = InfluenceConfig()
    adj = weighted_adjustment(hits, baseline=0.60, cfg=cfg, prior_leaf_n=0.0)
    assert adj["fraction"] != 0.0, "ilk kanıt SIFIR OLMAYAN etki üretmeli"
    assert abs(adj["fraction"]) <= cfg.max_fraction + 1e-9, "etki sınırlı kalmalı"
    assert abs(adj["learned"] - 0.60) < 0.60 * cfg.max_fraction + 1e-9


# ================================================================== 16) dashboard

@pytest.mark.parametrize("scenario", ["missing", "corrupt", "legacy", "empty_totals"])
def test_16_dashboard_reports_scope_honestly_and_never_500s(tmp_path: Path, scenario: str):
    from tradingbot.dashboard.state import StateReader
    st = tmp_path / "state"
    (st / "experience_index").mkdir(parents=True, exist_ok=True)
    payload = {"corrupt": "{ bozuk json",
               "legacy": json.dumps({"schema_version": "experience_index_v0", "processed": []}),
               "empty_totals": json.dumps({"schema_version": "experience_index_v1",
                                           "processed": [], "totals": None})}
    if scenario != "missing":
        (st / "experience_index" / "manifest.json").write_text(payload[scenario],
                                                               encoding="utf-8")
    view = StateReader(st).experience_index()
    assert isinstance(view, dict)
    assert view["retrieval_scope"] in (SCOPE_HOT_ONLY, SCOPE_DEGRADED)
    assert view["retrieval_scope"] not in (SCOPE_HOT_PLUS_INDEXED, SCOPE_FULL_HISTORY), "hazır olmayan indeks yalan söyleyemez"
    # saklama görünümü de aynı dürüst kapsamı taşır
    assert StateReader(st).decision_retention()["retrieval_scope"] == view["retrieval_scope"]


def test_16b_dashboard_shows_real_counts_when_index_ready(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    from tradingbot.dashboard.state import StateReader
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    st = Path(eng.cfg.state_path)
    book, arc = _book(st, max_trades=2)
    for i in range(11):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    _store(st, arc).refresh()
    eng.tour(do_scan=False, obsidian=False, charts=False)

    view = StateReader(st).experience_index()
    assert view["available"] is True
    assert view["indexed_experiences"] == 9
    assert view["indexed_shadow"] == 9 and view["indexed_real"] == 0
    assert view["processed_segments"] >= 1 and view["corrupt_segments"] == 0
    assert view["index_lag_segments"] == 0
    assert view["retrieval_scope"] == SCOPE_FULL_HISTORY
    assert view["oldest_label_ms"] and view["newest_label_ms"]

    client = TestClient(create_app(st, Path(eng.cfg.cache_path), None, DashboardConfig()))
    r = client.get("/api/learning-loop")
    assert r.status_code == 200
    body = r.json()
    assert body.get("retrieval_scope") == SCOPE_FULL_HISTORY
    assert (body.get("experience_index") or {}).get("indexed_experiences") == 9
    assert client.get("/quant").status_code == 200

    # bozuk manifest → 200, dürüst DEGRADED
    (st / "experience_index" / "manifest.json").write_text("{ bozuk", encoding="utf-8")
    r2 = client.get("/api/learning-loop")
    assert r2.status_code == 200
    assert r2.json().get("retrieval_scope") in (SCOPE_DEGRADED, SCOPE_HOT_ONLY)
    assert client.get("/quant").status_code == 200


# ================================================================== 17) risk/emir izolasyonu

def test_17_index_layer_touches_no_risk_or_order_path(tmp_path: Path, monkeypatch):
    src = Path("tradingbot/learn/experience_index.py").read_text(encoding="utf-8")
    for forbidden in ("RiskEngine", "ledger", "outbox", "gateway", "place_order", "submit",
                      "stop_loss", "take_profit", "/opt/"):
        assert forbidden not in src, f"indeks modülü {forbidden!r} ile ilgilenmemeli"

    eng = _engine(tmp_path, monkeypatch, symbols=3)
    st = Path(eng.cfg.state_path)
    book, arc = _book(st, max_trades=1)
    for i in range(7):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    _store(st, arc).refresh()
    eng.tour(do_scan=False, obsidian=False, charts=False)
    before = (eng.ledger2.summary(), len(eng.ledger2.positions),
              eng.ledger2.history_dicts(), eng.spot2.history_dicts())
    # İNDEKS İŞLEMLERİ TEK BAŞINA defteri/pozisyonları DEĞİŞTİREMEZ.
    eng.exp_index_store.refresh()
    eng.exp_index_store.rows()
    eng.exp_index_store.rebuild()
    eng._prepared_experience_pool(eng.influence_cfg)
    after = (eng.ledger2.summary(), len(eng.ledger2.positions),
             eng.ledger2.history_dicts(), eng.spot2.history_dicts())
    assert before == after, "indeks katmanı risk/emir durumunu DEĞİŞTİREMEZ"


def test_17b_decision_journal_archive_is_not_an_experience_source(tmp_path: Path, monkeypatch):
    """Karar günlüğü ÜÇÜNCÜ bir deneyim kaynağı DEĞİLDİR — aksi halde çift sayım olurdu."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    store = eng.exp_index_store
    assert store is not None
    assert store.archive is eng.shadow_archive, "indeks yalnız GÖLGE arşivini okur"
    assert store.archive is not getattr(eng.decision_journal, "archive", None)


# ================================================================== 19) instrumentation

def test_19_no_per_candidate_archive_scan(tmp_path: Path, monkeypatch):
    """Aday başına arşiv OKUNMAZ: segment okuması yalnız YENİ segment göründüğünde olur."""
    eng = _engine(tmp_path, monkeypatch, symbols=4)
    st = Path(eng.cfg.state_path)
    book = eng.shadow
    book.MAX_TRADES = 1
    for i in range(13):
        book.trades.append(_trade(i, labeled_h=i))
    book.save()
    n_segments = len(eng.shadow_archive.segments())
    assert n_segments >= 1

    calls = {"read_segment": 0, "iter_rows": 0}
    real_read = SegmentArchive.read_segment
    real_iter = SegmentArchive.iter_rows

    def counted_read(self, seg):
        calls["read_segment"] += 1
        return real_read(self, seg)

    def counted_iter(self, **kw):
        calls["iter_rows"] += 1
        return real_iter(self, **kw)

    monkeypatch.setattr(SegmentArchive, "read_segment", counted_read)
    monkeypatch.setattr(SegmentArchive, "iter_rows", counted_iter)

    eng.tour(do_scan=False, obsidian=False, charts=False)     # ilk tur: indeksleme
    first = calls["read_segment"]
    n_candidates = len(getattr(eng, "last_decisions", {}) or {}) or 4
    assert first <= n_segments, f"segment okuması segment sayısını aşmamalı ({first} > {n_segments})"

    calls["read_segment"] = 0
    for _ in range(3):
        eng.tour(do_scan=False, obsidian=False, charts=False)
    assert calls["read_segment"] == 0, "yeni segment yokken arşiv HİÇ okunmamalı"
    assert calls["iter_rows"] == 0, "sıcak yol tam arşiv taraması YAPMAMALI"
    assert n_candidates >= 1


# ================================================================== 20) uçtan uca üretim turu

def test_20_e2e_archived_outcome_reaches_next_similar_decision(tmp_path: Path, monkeypatch):
    """Gerçek `TradingEngineV3.tour()`: arşivlenmiş kapanış → sonraki benzer kararda kanıt."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    sym = eng.cfg.coins[0]
    book = eng.shadow
    book.MAX_TRADES = 2
    for i in range(12):                                       # 10'u arşive gidecek
        book.trades.append(_trade(i, symbol=sym, r=(2.0 if i % 2 else 1.5), labeled_h=i,
                                  created_h=i))
    book.save()
    assert book.stats()["archived"] == 10
    active_ids = {t.id for t in book.trades}
    assert len(active_ids) == 2

    eng.tour(do_scan=False, obsidian=False, charts=False)

    st = eng.exp_index_store.stats()
    assert st["indexed_experiences"] == 10
    assert st["retrieval_scope"] == SCOPE_FULL_HISTORY

    pool = eng._prepared_experience_pool(eng.influence_cfg)
    ids = [e.outcome_id for e in pool.experiences]
    assert len(ids) == len(set(ids)), "havuzda duplicate olmamalı"
    assert len(pool.experiences) >= 12, "arşiv + aktif birlikte havuzda olmalı"

    # Karar günlüğüne yazılan etki kaydı arşiv kanıtını görmüş olmalı
    rows = [r for r in eng.decision_journal.iter_all_rows()
            if r.get("learning_influence") and r.get("symbol") == sym]
    assert rows, "en az bir aday için öğrenme etkisi kaydı olmalı"
    li = rows[-1]["learning_influence"]
    assert li["mode"] == "SHADOW"
    assert li.get("applied") is not True, "SHADOW'da etki UYGULANMAZ"
    assert (li.get("n_experience") or 0) >= 1, "arşivlenmiş kanıt karara ULAŞMALI"


# ================================================================== 18) ölçek benchmark

@pytest.mark.slow
def test_18_hundred_thousand_indexed_experiences_are_bounded(tmp_path: Path):
    """100k arşiv deneyimi: sorgu maliyeti aday başına sınırlı, refresh artımlı."""
    arc = SegmentArchive(tmp_path / "shadow_archive", stream_id="shadow_book",
                         record_schema_version="shadow_trade_v1")
    n_seg, per_seg = 20, 5_000
    for s in range(n_seg):
        block = [json.dumps({
            "id": f"sh{s}_{i}", "plan_id": f"p{s}_{i}", "symbol": f"S{i % 20}/USDT",
            "market_type": "futures", "direction": "LONG" if i % 2 else "SHORT",
            # kimlik = (symbol, direction, variant, created_at) → BENZERSİZ olmalı, aksi halde
            # dedup 100k'yı birkaç yüze indirir ve ölçüm anlamsızlaşır
            "created_at": f"2026-03-01T{(s * per_seg + i) // 3600 % 24:02d}:"
                          f"{(s * per_seg + i) // 60 % 60:02d}:{(s * per_seg + i) % 60:02d}+00:00",
            "variant": f"as_planned_{(s * per_seg + i) // 86400}",
            "label_ts": "2026-03-02T00:00:00+00:00",
            "labeled_at": f"2026-03-02T{i % 24:02d}:00:00+00:00",
            "outcome": {"r_multiple": (i % 7) - 3.0}}) for i in range(per_seg)]
        arc.commit(arc.seal(block))

    store = _store(tmp_path, arc)
    t0 = time.perf_counter()
    store.refresh()
    build_s = time.perf_counter() - t0
    rows = store.rows()
    load_s = time.perf_counter() - t0 - build_s
    assert len(rows) == n_seg * per_seg == 100_000

    idx_bytes = sum(p.stat().st_size for p in (tmp_path / "experience_index" / "shards").glob("*"))

    t1 = time.perf_counter()
    pool = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=rows)
    prep_s = time.perf_counter() - t1
    assert len(pool.experiences) == 100_000

    def _lat(pl, n=30):
        xs = []
        for k in range(n):
            q = {"symbol": f"S{k % 20}/USDT", "direction": "LONG", "setup_type": "as_planned"}
            t2 = time.perf_counter()
            hits = query_pool(pl, q, as_of_ms=_ms(d=3), top_k=5)
            xs.append(time.perf_counter() - t2)
            assert len(hits) <= 5
        xs.sort()
        return xs[len(xs) // 2], xs[int(len(xs) * 0.95)]

    p50, p95 = _lat(pool)
    # SINIRLILIK KAPISI: 10× daha büyük havuzda aday başına maliyet DOĞRUSAL BÜYÜMEMELİ.
    small = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=rows[:10_000])
    s50, _ = _lat(small)
    ratio = p50 / max(s50, 1e-9)
    assert ratio < 3.0, (f"sorgu maliyeti arşiv boyutuyla doğrusal büyüyor "
                         f"(10k={s50 * 1000:.1f}ms, 100k={p50 * 1000:.1f}ms, oran={ratio:.1f}×)")
    assert p95 < 0.25, f"aday başına sorgu p95 sınırlı olmalı, ölçülen {p95 * 1000:.0f}ms"

    # ARTIMLI: tek yeni segment eklemek TÜM indeksi yeniden kurmaz
    arc.commit(arc.seal([json.dumps({
        "id": f"new{i}", "plan_id": f"np{i}", "symbol": "BTC/USDT", "market_type": "futures",
        "direction": "LONG", "created_at": f"2026-03-03T{i // 3600 % 24:02d}:"
                                          f"{i // 60 % 60:02d}:{i % 60:02d}+00:00",
        "variant": "as_planned_new",
        "label_ts": "2026-03-04T00:00:00+00:00", "labeled_at": "2026-03-04T00:00:00+00:00",
        "outcome": {"r_multiple": 1.0}}) for i in range(per_seg)]))
    t3 = time.perf_counter()
    inc = store.refresh()
    incr_s = time.perf_counter() - t3
    assert inc["new_segments"] == 1 and inc["new_rows"] == per_seg
    assert incr_s < build_s / 4, (f"artımlı ekleme tam kurulumdan çok daha ucuz olmalı "
                                  f"({incr_s:.2f}s vs {build_s:.2f}s)")
    # kararlı durum: yeni segment yokken maliyet ~0
    t4 = time.perf_counter()
    noop = store.refresh()
    noop_s = time.perf_counter() - t4
    assert noop["new_segments"] == 0 and noop_s < 0.5

    print(f"\n[bench-index] build(100k)={build_s:.2f}s load={load_s:.2f}s "
          f"prep={prep_s:.2f}s query_p50={p50 * 1000:.2f}ms query_p95={p95 * 1000:.2f}ms "
          f"incr(+5k)={incr_s:.3f}s noop={noop_s * 1000:.1f}ms "
          f"index={idx_bytes / 1e6:.2f}MB rows={len(rows)} "
          f"q10k_p50={s50 * 1000:.2f}ms scale_ratio={ratio:.2f}x")


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("TRADINGBOT_BENCH_1M") != "1",
                    reason="1M ölçek ölçümü opt-in: TRADINGBOT_BENCH_1M=1")
def test_18b_one_million_indexed_experiences_stay_bounded(tmp_path: Path):
    """1M arşiv deneyimi: aday başına sorgu SINIRLI kalır (ölçüm CI'da opt-in)."""
    arc = SegmentArchive(tmp_path / "shadow_archive", stream_id="shadow_book",
                         record_schema_version="shadow_trade_v1")
    n_seg, per_seg = 50, 20_000
    for s in range(n_seg):
        block = []
        for i in range(per_seg):
            g = s * per_seg + i
            block.append(json.dumps({
                "id": f"sh{g}", "plan_id": f"p{g}", "symbol": f"S{g % 40}/USDT",
                "market_type": "futures", "direction": "LONG" if g % 2 else "SHORT",
                "created_at": f"2026-03-01T{g // 3600 % 24:02d}:"
                              f"{g // 60 % 60:02d}:{g % 60:02d}+00:00",
                "variant": f"as_planned_{g // 86400}",
                "label_ts": "2026-03-02T00:00:00+00:00",
                "labeled_at": f"2026-03-02T{g % 24:02d}:00:00+00:00",
                "outcome": {"r_multiple": (g % 7) - 3.0}}))
        arc.commit(arc.seal(block))

    store = _store(tmp_path, arc)
    t0 = time.perf_counter()
    store.refresh()
    build_s = time.perf_counter() - t0
    rows = store.rows()
    assert len(rows) == 1_000_000
    pool = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=rows)

    lat = []
    for k in range(20):
        q = {"symbol": f"S{k % 40}/USDT", "direction": "LONG", "setup_type": "as_planned_0"}
        t = time.perf_counter()
        query_pool(pool, q, as_of_ms=_ms(d=3), top_k=5)
        lat.append(time.perf_counter() - t)
    lat.sort()
    p50, p95 = lat[len(lat) // 2], lat[int(len(lat) * 0.95)]
    assert p95 < 0.30, f"1M'de aday başına sorgu p95 sınırlı olmalı, ölçülen {p95 * 1000:.0f}ms"

    t1 = time.perf_counter()
    noop = store.refresh()
    assert noop["new_segments"] == 0 and (time.perf_counter() - t1) < 1.0
    idx_bytes = sum(p.stat().st_size
                    for p in (tmp_path / "experience_index" / "shards").glob("*"))
    print(f"\n[bench-index-1M] build={build_s:.1f}s query_p50={p50 * 1000:.1f}ms "
          f"query_p95={p95 * 1000:.1f}ms index={idx_bytes / 1e6:.1f}MB")

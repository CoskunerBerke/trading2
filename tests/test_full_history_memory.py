"""Full-history bounded memory — hiçbir arşiv sonucu "erişilemez veri" olamaz.

Kanıtlanan boşluk: exemplar penceresi (`retrieval_max_scan`) dışında kalan eski ama benzer
arşiv sonuçları HİÇBİR kanala katkı veremiyordu. Sözleşme:

    Recent exact exemplars  +  coin/yön/rejim/setup/profil sınırlı toplam istatistikleri
    = full-history bounded memory

Sahiplik: prior=gerçek tam geçmiş (residual), exemplar=pencere içi, aggregate=arşiv kalanı
(sayılan exemplar'lar düşülür). Aynı outcome hiçbir kombinasyonda iki kez sayılmaz.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.learn.aggregate_memory import AggregateBook, level_keys_for
from tradingbot.learn.experience import Experience, prepare_pool, query_pool
from tradingbot.learn.experience_index import (SCOPE_FULL_HISTORY, ExperienceIndexStore)
from tradingbot.learn.influence import InfluenceConfig, apply_influence, weighted_adjustment
from tradingbot.learn.journal_archive import SegmentArchive
from tradingbot.learn.shadow import ShadowBook, ShadowTrade

UTC = timezone.utc


def _ms(y=2026, mo=6, d=1, h=0) -> int:
    return int(datetime(y, mo, d, h, tzinfo=UTC).timestamp() * 1000)


def _row(i: int, *, symbol="BTC/USDT", direction="LONG", variant="as_planned",
         r=1.0, month=1, day=1) -> str:
    return json.dumps({
        "id": f"sh{i}", "plan_id": f"p{i}", "symbol": symbol, "market_type": "futures",
        "direction": direction, "variant": variant,
        "created_at": f"2026-{month:02d}-{day:02d}T{i % 24:02d}:{i // 24 % 60:02d}:00+00:00",
        "label_ts": f"2026-{month:02d}-{day:02d}T23:00:00+00:00",
        "labeled_at": f"2026-{month:02d}-{day:02d}T{i % 24:02d}:{i // 24 % 60:02d}:30+00:00",
        "outcome": {"r_multiple": r}})


def _setup(tmp_path: Path) -> tuple[SegmentArchive, ExperienceIndexStore]:
    arc = SegmentArchive(tmp_path / "shadow_archive", stream_id="shadow_book",
                         record_schema_version="shadow_trade_v1")
    return arc, ExperienceIndexStore(tmp_path / "experience_index", arc)


# ================================================================== 1) her sonuç katkıda

def test_1_every_outcome_contributes_via_exemplar_or_aggregate(tmp_path: Path):
    arc, store = _setup(tmp_path)
    # eski ay (Ocak): 60 kayıt; yeni ay (Mayıs): 40 kayıt
    arc.commit(arc.seal([_row(i, month=1, r=2.0) for i in range(60)]))
    arc.commit(arc.seal([_row(1000 + i, month=5, r=1.0) for i in range(40)]))
    store.refresh()
    rows = store.rows()
    pool = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=rows,
                        aggregate_base=store.aggregates())

    as_of = _ms(mo=6)
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=as_of, top_k=5)
    agg = pool.aggregate_book.query(symbol="BTC/USDT", direction="LONG", regime=None,
                                    setup="as_planned", profile=None,
                                    as_of_ms=as_of, subtract=hits)
    counted_exemplar = len(hits)
    counted_aggregate = agg["n"] if agg else 0
    assert counted_exemplar + counted_aggregate == 100, \
        "exemplar + aggregate birleşimi kayıtların TAMAMINI kapsamalı"
    assert agg and agg["subtracted_exemplars"] == counted_exemplar, \
        "sayılan exemplar'lar aggregate'ten düşülmeli (çift sayım yasak)"


# ================================================================== 2) 5000'den eski nadir pattern

def test_2_rare_pattern_older_than_scan_window_still_influences(tmp_path: Path):
    """KİLİT TEST: en yeni 5.000 kaydın DIŞINDA kalan eski, nadir, benzer sonuçlar karar
    etkisine hâlâ katılabiliyor (aggregate kanalından)."""
    arc, store = _setup(tmp_path)
    # 6.000 alakasız YENİ kayıt (farklı sembol) — exemplar penceresini tamamen doldurur
    bulk = [_row(10_000 + i, symbol=f"S{i % 20}/USDT", month=5, day=1 + i % 27, r=0.5)
            for i in range(6_000)]
    # 30 ESKİ, NADİR, hedefe çok benzer kayıt (aynı sembol/yön/setup) — hepsi ZARARDA
    rare = [_row(i, symbol="RARE/USDT", month=1, day=1 + i % 27, r=-2.0) for i in range(30)]
    arc.commit(arc.seal(rare))
    for k in range(0, len(bulk), 2000):
        arc.commit(arc.seal(bulk[k:k + 2000]))
    store.refresh()
    rows = store.rows()
    assert len(rows) == 6_030

    pool = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=rows,
                        aggregate_base=store.aggregates())
    as_of = _ms(mo=6)
    q = {"symbol": "RARE/USDT", "direction": "LONG", "setup_type": "as_planned"}
    hits = query_pool(pool, q, as_of_ms=as_of, top_k=5, max_scan=5_000)

    agg = pool.aggregate_book.query(symbol="RARE/USDT", direction="LONG", regime=None,
                                    setup="as_planned", profile=None,
                                    as_of_ms=as_of, subtract=hits)
    assert agg is not None and agg["n"] >= 25, "eski nadir pattern aggregate'te BULUNMALI"
    assert agg["mean_r"] < 0, "zarar geçmişi negatif kanıt vermeli"

    cfg = InfluenceConfig()
    with_agg = weighted_adjustment(hits, baseline=0.60, cfg=cfg, prior_leaf_n=0.0,
                                   aggregate=agg)
    without = weighted_adjustment(hits, baseline=0.60, cfg=cfg, prior_leaf_n=0.0)
    assert with_agg["fraction"] < without["fraction"], \
        "eski zarar geçmişi p_win ayarını AŞAĞI çekmeli — erişilemez veri YASAK"
    assert with_agg["fraction"] < 0
    assert abs(with_agg["fraction"]) <= cfg.max_fraction + 1e-9, "etki yine SINIRLI"
    assert "INCLUDES_FULL_HISTORY_AGGREGATE" in with_agg["reasons"]
    assert with_agg["aggregate_weight"] > 0 and with_agg["exemplar_weight"] >= 0


# ================================================================== 3) aday başına arşiv taranmaz

def test_3_aggregate_query_opens_no_files(tmp_path: Path, monkeypatch):
    arc, store = _setup(tmp_path)
    arc.commit(arc.seal([_row(i, month=2) for i in range(500)]))
    store.refresh()
    book = store.aggregates()
    import builtins
    real_open = builtins.open
    calls = {"n": 0}

    def counting_open(*a, **k):
        calls["n"] += 1
        return real_open(*a, **k)

    monkeypatch.setattr(builtins, "open", counting_open)
    for _ in range(50):
        book.query(symbol="BTC/USDT", direction="LONG", regime=None,
                   setup="as_planned", profile=None, as_of_ms=_ms(mo=6))
    assert calls["n"] == 0, "aggregate sorgusu DOSYA AÇMAMALI (bellek içi O(hücre))"


# ================================================================== 4-5) dedup + real üstünlüğü

def test_4_duplicate_outcome_counted_once_across_all_channels(tmp_path: Path):
    arc, store = _setup(tmp_path)
    arc.commit(arc.seal([_row(i, month=3, r=2.0) for i in range(20)]))
    store.refresh()
    rows = store.rows()
    pool = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=rows,
                        aggregate_base=store.aggregates())
    as_of = _ms(mo=6)
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=as_of, top_k=20)
    assert len(hits) == 20
    agg = pool.aggregate_book.query(symbol="BTC/USDT", direction="LONG", regime=None,
                                    setup="as_planned", profile=None,
                                    as_of_ms=as_of, subtract=hits)
    assert agg is None, "bütün kayıtlar exemplar'da sayıldıysa aggregate KALANI SIFIR olmalı"


def test_5_real_beats_shadow_and_aggregate_owns_only_archive(tmp_path: Path):
    from tradingbot.learn.experience import REAL_PAPER
    arc, store = _setup(tmp_path)
    arc.commit(arc.seal([_row(i, symbol="ETH/USDT", month=2, r=-1.0) for i in range(10)]))
    store.refresh()
    mem = [{"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "as_planned",
            "opened_at": "2026-02-01T00:00:00+00:00", "recorded_at": "2026-02-01T00:00:00+00:00",
            "features": {"atr_pct": 0.3},
            "outcome": {"r_multiple": 3.0, "closed_at": "2026-02-02T00:00:00+00:00",
                        "opened_at": "2026-02-01T00:00:00+00:00"}}]
    pool = prepare_pool(memory_rows=mem, shadow_trades=[], indexed_history=store.rows(),
                        aggregate_base=store.aggregates())
    real = [e for e in pool.experiences if e.source == REAL_PAPER]
    assert len(real) == 1 and real[0].weight == pytest.approx(1.0)
    # aggregate defteri yalnız ARŞİV gölgelerini içerir — gerçek kapanış prior'ın malıdır
    g = pool.aggregate_book.cells.get("5|GLOBAL") or {}
    total = sum(int(st["n"]) for st in g.values())
    assert total == 10, "aggregate yalnız arşivlenmiş gölgeleri saymalı"


# ================================================================== 6) üçlü sayım yok

def test_6_prior_exemplar_aggregate_never_triple_count(tmp_path: Path):
    arc, store = _setup(tmp_path)
    arc.commit(arc.seal([_row(i, month=2, r=2.0) for i in range(40)]))
    store.refresh()
    pool = prepare_pool(memory_rows=[], shadow_trades=[], indexed_history=store.rows(),
                        aggregate_base=store.aggregates())
    as_of = _ms(mo=6)
    hits = query_pool(pool, {"symbol": "BTC/USDT", "direction": "LONG",
                             "setup_type": "as_planned"}, as_of_ms=as_of, top_k=5)
    agg = pool.aggregate_book.query(symbol="BTC/USDT", direction="LONG", regime=None,
                                    setup="as_planned", profile=None,
                                    as_of_ms=as_of, subtract=hits)
    cfg = InfluenceConfig()
    # prior ağır → residual payı hem exemplar hem aggregate katkısını AYNI oranda kısar
    light = weighted_adjustment(hits, baseline=0.6, cfg=cfg, prior_leaf_n=0.0, aggregate=agg)
    heavy = weighted_adjustment(hits, baseline=0.6, cfg=cfg, prior_leaf_n=500.0, aggregate=agg)
    assert heavy["residual_share"] < 0.05
    assert abs(heavy["fraction"]) < abs(light["fraction"]) * 0.2, \
        "prior'da temsil edilen kanıt residual dışında tekrar sayılamaz"
    assert light["aggregate_weight"] + light["exemplar_weight"] == pytest.approx(
        light["effective_n"], rel=1e-6), "toplam kanıt = exemplar + aggregate (başka kanal yok)"


# ================================================================== 7) no-lookahead

def test_7_future_outcome_cannot_enter_aggregate_before_decision_time(tmp_path: Path):
    arc, store = _setup(tmp_path)
    arc.commit(arc.seal([_row(i, month=3, r=5.0) for i in range(10)]))     # gelecek: Mart
    arc.commit(arc.seal([_row(100 + i, month=1, r=-1.0) for i in range(10)]))  # geçmiş: Ocak
    store.refresh()
    book = store.aggregates()
    as_of_feb = _ms(mo=2, d=15)                     # karar anı: 15 Şubat
    agg = book.query(symbol="BTC/USDT", direction="LONG", regime=None,
                     setup="as_planned", profile=None, as_of_ms=as_of_feb)
    assert agg is not None and agg["n"] == 10, "yalnız Ocak kayıtları sayılmalı"
    assert agg["mean_r"] < 0, "gelecekteki Mart kazançları SIZAMAZ"
    # ay bitmeden hiçbir kayıt sayılmaz (bitmemiş ay fail-closed dışarıda)
    agg_jan = book.query(symbol="BTC/USDT", direction="LONG", regime=None,
                         setup="as_planned", profile=None, as_of_ms=_ms(mo=1, d=20))
    assert agg_jan is None, "bitmemiş ay kovası sayılamaz (no-lookahead fail-closed)"


# ================================================================== 8) bozuk index → baseline

def test_8_corrupt_aggregates_self_heal_from_shards(tmp_path: Path):
    arc, store = _setup(tmp_path)
    arc.commit(arc.seal([_row(i, month=2, r=1.0) for i in range(30)]))
    store.refresh()
    before = store.aggregates().to_dict()

    (tmp_path / "experience_index" / "aggregates.json").write_text("{ bozuk", encoding="utf-8")
    healed = ExperienceIndexStore(tmp_path / "experience_index", arc)
    book = healed.aggregates()
    assert book is not None and book.to_dict()["cells"] == before["cells"], \
        "bozuk aggregate dosyası shard'lardan DETERMİNİSTİK yeniden kurulmalı"


# ================================================================== 9) rebuild deterministik

def test_9_rebuild_reproduces_identical_aggregates(tmp_path: Path):
    import shutil
    arc, store = _setup(tmp_path)
    for k in range(4):
        arc.commit(arc.seal([_row(k * 100 + i, month=1 + k, r=(1.0 if i % 2 else -1.0))
                             for i in range(50)]))
    store.refresh()
    before = json.dumps(store.aggregates().to_dict(), sort_keys=True)

    shutil.rmtree(tmp_path / "experience_index")
    rebuilt = ExperienceIndexStore(tmp_path / "experience_index", arc)
    rebuilt.refresh()
    after = json.dumps(rebuilt.aggregates().to_dict(), sort_keys=True)
    assert after == before, "arşivden yeniden kurulan aggregate BİREBİR aynı olmalı"
    assert rebuilt.stats()["retrieval_scope"] == SCOPE_FULL_HISTORY


# ================================================================== 10) ölçek: sorgu O(hücre)

def test_10_aggregate_query_is_bounded_at_scale(tmp_path: Path):
    book = AggregateBook()
    # 100k sentetik deneyim → hücre sayısı SINIRLI kalmalı
    for i in range(100_000):
        book.add(Experience(outcome_id=f"o{i}", source="SHADOW",
                            symbol=f"S{i % 50}/USDT", direction="LONG" if i % 2 else "SHORT",
                            setup=f"v{i % 6}", regime=None, r_multiple=(i % 7) - 3.0,
                            weight=0.125, label_ts_ms=_ms(mo=1 + i % 5, d=1 + i % 27)))
    st = book.stats()
    assert st["cells"] < 20_000, f"hücre kardinalitesi sınırlı kalmalı ({st['cells']})"
    t0 = time.perf_counter()
    for k in range(200):
        book.query(symbol=f"S{k % 50}/USDT", direction="LONG", regime=None,
                   setup=f"v{k % 6}", profile=None, as_of_ms=_ms(mo=6))
    per = (time.perf_counter() - t0) / 200
    assert per < 0.005, f"aggregate sorgusu aday başına sınırlı olmalı ({per * 1000:.2f} ms)"


# ================================================================== kardinalite tavanı

def test_cardinality_cap_folds_without_losing_evidence(tmp_path: Path):
    book = AggregateBook(max_cells=200)
    for i in range(2_000):
        book.add(Experience(outcome_id=f"o{i}", source="SHADOW",
                            symbol=f"UNIQ{i}/USDT", direction="LONG",
                            setup=f"setup{i}", regime=f"R{i % 3}", r_multiple=1.0,
                            weight=0.125, label_ts_ms=_ms(mo=2)))
    st = book.stats()
    assert st["folded_cells"] > 0, "tavan aşımı katlama üretmeli"
    g = book.query(symbol="YOK/USDT", direction="LONG", regime=None, setup=None,
                   profile=None, as_of_ms=_ms(mo=6))
    assert g is not None and g["level"] == "L5" and g["n"] == 2_000, \
        "katlanan kayıtlar GLOBAL seviyede yine de sayılmalı (kanıt kaybolmaz)"


# ================================================================== üretim E2E

def test_e2e_engine_uses_aggregate_channel(tmp_path: Path, monkeypatch):
    """Gerçek tur: arşivlenmiş eski kayıtlar exemplar penceresi dolu OLMASA da aggregate
    alanları influence kaydında görünür ve SHADOW baseline'ı değişmez."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    sym = eng.cfg.coins[0]
    book = eng.shadow
    book.MAX_TRADES = 2
    for i in range(30):
        book.trades.append(ShadowTrade(
            id=f"sh{i}", plan_id=f"p{i}", symbol=sym, market_type="futures",
            direction="LONG", created_at=f"2026-03-01T{i % 24:02d}:00:00+00:00",
            entry=100.0, stop=95.0, targets=[110.0], horizon_bars=4, variant="as_planned",
            reason_not_opened=["X"], label_ts="2026-03-02T00:00:00+00:00",
            outcome={"r_multiple": 1.5},
            labeled_at=f"2026-03-02T{i % 24:02d}:00:00+00:00"))
    book.save()
    eng.tour(do_scan=False, obsidian=False, charts=False)

    st = eng.exp_index_store.stats()
    assert st["retrieval_scope"] == SCOPE_FULL_HISTORY
    assert (st.get("aggregate") or {}).get("outcomes", 0) >= 28

    rows = [r for r in eng.decision_journal.iter_all_rows()
            if r.get("learning_influence") and r.get("symbol") == sym]
    assert rows, "öğrenme etkisi kaydı olmalı"
    li = rows[-1]["learning_influence"]
    assert li["mode"] == "SHADOW" and li.get("applied") is not True
    assert "aggregate_weight" in li and "decision_changed_by_learning" in li

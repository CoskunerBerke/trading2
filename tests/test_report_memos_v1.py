"""RAPOR MEMOLARI (2026-10-01) — `entry_snapshot.trade_links` ve kapanmış işlem çıkış değerlendirmesi.

İkisi de yalnız rapor katmanıdır (karar okumaz). Bu testler kilitler:

1. `trade_links()` sıcak dosya imzası değişmedikçe yeniden ayrıştırmaz; sonuç taze ayrıştırmayla AYNI; çağırana KOPYA verir;
   yeni bağ satırı imzayı değiştirir ve görünür; arşivli çağrı memolanmaz; anahtar kapalıyken eski yol.
2. `_write_exit_eval` aynı kapanış + aynı config + aynı yol için `evaluate_trade`i yeniden çağırmaz; `exit_eval.json` memo
   AÇIK/KAPALI bayt bayt aynı; kapanış kaydı ya da yolu değişen işlem yeniden oynatılır; memo kapanış sayısıyla sınırlı.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import tradingbot.learn.entry_snapshot as ES  # noqa: E402
from tradingbot.learn.entry_snapshot import EntrySnapshotStore  # noqa: E402

UTC = timezone.utc


# ------------------------------------------------------------------ 1) trade_links
def _fill(store: EntrySnapshotStore, rnd: random.Random, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        store.append({"schema_version": "entry_snapshot_v1", "candidate_id": "cand%05d" % i, "symbol": "ETH/USDT",
                      "features": {"x": rnd.random()}})
        if rnd.random() < 0.4:
            store.link_trade("cand%05d" % i, "T%05d" % i)
        if rnd.random() < 0.05:                       # aynı işleme ikinci bağ: ilki otoritedir
            store.link_trade("cand%05d" % (i + 1000), "T%05d" % i)


def _count_parses(monkeypatch, store) -> dict:
    n = {"k": 0}
    real = store.iter_hot_rows

    def counting():
        n["k"] += 1
        yield from real()
    monkeypatch.setattr(store, "iter_hot_rows", counting)
    return n


def test_trade_links_memo_is_identical_to_a_fresh_parse_and_reparses_only_on_change(tmp_path, monkeypatch):
    rnd = random.Random(5)
    p = tmp_path / "entry_snapshot.jsonl"
    store = EntrySnapshotStore(p)
    _fill(store, rnd, 200)
    n = _count_parses(monkeypatch, store)
    a = store.trade_links()
    b = store.trade_links()
    c = store.trade_links()
    assert n["k"] == 1, "imza değişmedi: tek ayrıştırma"
    assert a == b == c == EntrySnapshotStore(p).trade_links() and len(a) > 50
    b["T_extra"] = "x"                                   # çağıran sözlüğe ekler (motor bunu yapıyor) → memo bozulmaz
    assert "T_extra" not in store.trade_links()
    store.link_trade("cand00001", "T_new")                  # yeni bağ → imza değişir → yeniden ayrıştırma
    d = store.trade_links()
    assert n["k"] == 2 and d["T_new"] == "cand00001" and d == EntrySnapshotStore(p).trade_links()
    _fill(store, rnd, 50, start=500)
    assert store.trade_links() == EntrySnapshotStore(p).trade_links() and n["k"] == 3


def test_trade_links_memo_survives_drop_hot_cache_and_switch_off_restores_old_path(tmp_path, monkeypatch):
    p = tmp_path / "entry_snapshot.jsonl"
    store = EntrySnapshotStore(p)
    _fill(store, random.Random(2), 60)
    n = _count_parses(monkeypatch, store)
    store.trade_links()
    store.drop_hot_cache()                               # by_candidate memoları bırakılır; bağ memosu küçük, korunur
    store.trade_links()
    assert n["k"] == 1
    monkeypatch.setattr(ES, "LINKS_MEMO", False)
    store.trade_links()
    store.trade_links()
    assert n["k"] == 3


def test_trade_links_with_archive_is_not_memoised(tmp_path, monkeypatch):
    p = tmp_path / "entry_snapshot.jsonl"
    store = EntrySnapshotStore(p)
    _fill(store, random.Random(3), 30)
    n = _count_parses(monkeypatch, store)
    store.trade_links(include_archive=True)
    store.trade_links(include_archive=True)
    assert n["k"] == 2
    assert store.trade_links(include_archive=True) == store.trade_links()


def test_trade_links_on_a_missing_file_is_empty_and_not_cached(tmp_path):
    store = EntrySnapshotStore(tmp_path / "yok.jsonl")
    assert store.trade_links() == {} and store._tl_cache is None


# ------------------------------------------------------------------ 2) çıkış değerlendirmesi
_BASE = {"schema_version": "position_path_v1", "symbol": "ETH/USDT", "side": "LONG", "tick_kind": "last_only",
         "entry": 2500.0, "qty": 0.01, "initial_qty": 0.01, "remaining_fraction": 1.0, "initial_stop": 2450.0,
         "current_stop": 2450.0, "stop_distance_pct": 2.0, "targets": [2550.0, 2600.0], "targets_hit": 0, "tp1_done": False,
         "leverage": 3.0, "initial_risk_usdt": 0.5, "fees_paid": 0.02, "funding_net": 0.0, "fee_drag_r": 0.04,
         "funding_drag_r": 0.0, "bars_held": 3, "regime": "TREND_UP", "coinhead_action": "HOLD", "economics_evaluated": True,
         "p_win": 0.52}


def _write_paths(path_file: Path, n_trades: int, rows: int, seed: int = 1, extra: dict | None = None) -> list[dict]:
    """Sentetik fiyat yolları (60 sn aralık) + kapanış kayıtları. `extra`: {trade_index: ek satır sayısı}."""
    rnd = random.Random(seed)
    t_start = datetime(2026, 9, 18, tzinfo=UTC)
    closes = []
    with open(path_file, "a", encoding="utf-8") as fh:
        for t in range(n_trades):
            tid = "trade_%04d" % t
            t0 = t_start + timedelta(hours=t * 3)
            k = rows + (extra or {}).get(t, 0)
            for i in range(k):
                ts = t0 + timedelta(seconds=60 * i)
                mark = 2500.0 + 25.0 * rnd.uniform(-2, 3)
                r = (mark - 2500.0) / 50.0
                fh.write(json.dumps(dict(_BASE, trade_id=tid, snapshot_id="%s_%d" % (tid, i), ts=ts.isoformat(),
                                         ts_ms=int(ts.timestamp() * 1000), mark=mark, gross_r=round(r, 6),
                                         net_r=round(r - 0.05, 6), mfe_r=max(0.0, r) + 0.5 * (i % 7 == 0),
                                         mae_r=min(0.0, r), position_age_hours=round(i / 60.0, 4),
                                         opened_at=t0.isoformat())) + "\n")
            closes.append({"id": tid, "symbol": "ETH/USDT", "side": "LONG", "opened_at": t0.isoformat(),
                           "closed_at": (t0 + timedelta(seconds=60 * k)).isoformat(), "exit_reason": "stop",
                           "r_multiple": round(rnd.uniform(-1.2, 2.5), 4), "net_pnl": -0.5, "fees": 0.02, "funding": 0.0})
    return closes


def _engine(tmp_path, monkeypatch):
    import test_engine_v3 as TE
    eng = TE._engine(tmp_path, monkeypatch)
    assert eng.path_store is not None and eng.exit_policy_cfg is not None
    return eng


def _count_evals(monkeypatch) -> dict:
    import tradingbot.learn.exit_eval as EE
    n = {"k": 0}
    real = EE.evaluate_trade

    def counting(**kw):
        n["k"] += 1
        return real(**kw)
    monkeypatch.setattr(EE, "evaluate_trade", counting)
    return n


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _exit_doc(eng) -> bytes:
    eng._write_exit_eval(NOW)
    raw = (eng.cfg.state_path / "exit_eval.json").read_bytes()
    return raw.replace(str(eng.cfg.project_root).encode("utf-8"), b"<ROOT>")    # yol sağlığı satırı mutlak yolu taşır


def test_exit_eval_memo_reuses_frozen_closes_and_the_report_is_byte_identical(tmp_path, monkeypatch):
    on, off = _engine(tmp_path / "on", monkeypatch), _engine(tmp_path / "off", monkeypatch)
    monkeypatch.setattr(off, "EXIT_EVAL_MEMO", False)
    closes = {}
    for e in (on, off):
        closes[id(e)] = _write_paths(Path(e.path_store.path), 12, 40)
        e.ledger2.history = list(closes[id(e)])
    n = _count_evals(monkeypatch)
    doc_on, doc_off = _exit_doc(on), _exit_doc(off)
    assert doc_on == doc_off and n["k"] == 24
    assert json.loads(doc_on)["n_path_complete"] >= 10, "anlamlı senaryo: tam yollar değerlendirildi"
    # ikinci tur: girdiler aynı → AÇIK yol hiç yeniden oynatmaz, KAPALI 12 kez oynatır; rapor aynı
    assert _exit_doc(on) == _exit_doc(off) == doc_on and n["k"] == 24 + 12
    # bir kapanış kaydı değişti (ör. funding uzlaştırması) → yalnız o işlem yeniden oynatılır
    for e in (on, off):
        e.ledger2.history[3] = dict(e.ledger2.history[3], r_multiple=0.77, net_pnl=0.31)
    assert _exit_doc(on) == _exit_doc(off) and n["k"] == 36 + 1 + 12
    # bir işlemin yoluna satır eklendi → yalnız o işlem yeniden oynatılır
    for e in (on, off):
        with open(e.path_store.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(_BASE, trade_id="trade_0005", snapshot_id="trade_0005_late", ts="2026-09-18T15:41:00+00:00",
                                     ts_ms=int(datetime(2026, 9, 18, 15, 41, tzinfo=UTC).timestamp() * 1000), mark=2600.0,
                                     gross_r=2.0, net_r=1.95, mfe_r=2.0, mae_r=0.0, opened_at="2026-09-18T15:00:00+00:00")) + "\n")
    assert _exit_doc(on) == _exit_doc(off) and n["k"] == 49 + 1 + 12
    # bir işlem geçmişten düştü → memo onu tutmaz (bellek kapanış sayısıyla sınırlı)
    for e in (on, off):
        e.ledger2.history = e.ledger2.history[:-2]
    assert _exit_doc(on) == _exit_doc(off)
    assert len(on.__dict__["_exit_eval_memo"]) == 10 and on.__dict__["_exit_eval_memo"] is not None


def test_exit_eval_memo_holds_slim_entries_without_action_lists(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng.ledger2.history = _write_paths(Path(eng.path_store.path), 4, 60, seed=9)
    eng._write_exit_eval(NOW)
    memo = eng.__dict__["_exit_eval_memo"]
    assert len(memo) == 4
    for v in memo.values():
        for r in (v.get("results") or {}).values():
            assert "actions" not in r


def test_an_unpicklable_close_falls_back_to_recomputing(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    closes = _write_paths(Path(eng.path_store.path), 3, 30, seed=4)
    closes[1] = dict(closes[1], weird=lambda: None)          # özetlenemez → memo yok, yine doğru hesap
    eng.ledger2.history = closes
    n = _count_evals(monkeypatch)
    a = _exit_doc(eng)
    b = _exit_doc(eng)
    assert a == b and n["k"] == 3 + 1


def test_switches_are_on_by_default():
    from tradingbot.engine_v3 import TradingEngineV3
    assert TradingEngineV3.EXIT_EVAL_MEMO is True and ES.LINKS_MEMO is True

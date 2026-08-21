"""Öğrenme katmanı: memory namespace izolasyonu, hiyerarşik shrinkage (market/cluster) + recency decay, walk-forward pencereleri,
deterministik replay (aynı seed → aynı hash), replay state gerçek state'ten ayrı, replay resume (ikinci koşu aynı sonuç)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_patterns import _candles  # noqa: E402

from tradingbot.history import HistoryStore  # noqa: E402
from tradingbot.learn.memory import TradeMemory  # noqa: E402
from tradingbot.learn.model import HierarchicalRate  # noqa: E402
from tradingbot.replay import HistoricalReplay, walk_forward_windows  # noqa: E402

H4 = 4 * 3_600_000


def test_memory_source_namespace_isolation(tmp_path: Path):
    live = TradeMemory(tmp_path / "trade_memory.jsonl")                       # varsayılan LIVE_PAPER
    rep = TradeMemory(tmp_path / "replay" / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    live.record_entry({"trade_id": "L1", "symbol": "SUI/USDT"})
    rep.record_entry({"trade_id": "R1", "symbol": "SUI/USDT"})
    rep.record_exit("R1", {"symbol": "SUI/USDT", "r_multiple": 1.0})
    rows_live = [json.loads(l) for l in (tmp_path / "trade_memory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows_live[0]["source"] == "LIVE_PAPER" and len(rows_live) == 1
    rows_rep = list(rep.iter_rows())
    assert {r["source"] for r in rows_rep} == {"HISTORICAL_REPLAY"} and len(rows_rep) == 2
    # aynı dosyada karışık kayıtlar olsa bile okuyucu namespace'e göre ayırır
    mixed = TradeMemory(tmp_path / "mixed.jsonl")
    mixed.record_entry({"trade_id": "A", "source": "HISTORICAL_REPLAY"})
    mixed.record_entry({"trade_id": "B"})
    assert [r["trade_id"] for r in mixed.iter_rows()] == ["B"]                 # auto → LIVE_PAPER
    assert [r["trade_id"] for r in mixed.iter_rows(source="HISTORICAL_REPLAY")] == ["A"]
    assert len(list(mixed.iter_rows(source=None))) == 2
    with pytest.raises(ValueError):
        TradeMemory(tmp_path / "x.jsonl", source="BOGUS")


def test_hierarchical_shrinkage_market_cluster_and_recency():
    h = HierarchicalRate(alpha=10, prior_mean=0.5)
    for _ in range(200):
        h.add(1.0, market="futures", cluster="L1")                             # küme çok kazançlı
    for _ in range(3):
        h.add(0.0, market="futures", cluster="L1", leaf="NEW/USDT|pullback")   # az verili coin: 3 kayıp
    m_leaf, n = h.estimate(market="futures", cluster="L1", leaf="NEW/USDT|pullback")
    m_cluster, _ = h.estimate(market="futures", cluster="L1")
    assert 0.5 < m_leaf < m_cluster                                            # yaprak ebeveyne (küme) çekildi, 0'a çökmedi
    m_other, _ = h.estimate(market="spot")
    assert abs(m_other - h.estimate()[0]) < 1e-9                               # verisiz market: global ebeveyne düşer
    # recency: eski kayıplar yarı ömürle sönümlenir
    hr = HierarchicalRate(alpha=1, prior_mean=0.5, half_life_days=30)
    for _ in range(50):
        hr.add(0.0, leaf="X", age_days=365)                                    # çok eski kayıplar
    for _ in range(10):
        hr.add(1.0, leaf="X", age_days=1)                                      # yeni kazançlar
    m_rec, n_eff = hr.estimate(leaf="X")
    assert m_rec > 0.8 and n_eff < 15                                          # yenilik ağırlığı: eski 50 kayıp neredeyse yok sayıldı
    d = hr.to_dict(); hr2 = HierarchicalRate.from_dict(d)
    assert abs(hr2.estimate(leaf="X")[0] - m_rec) < 1e-9 and hr2.half_life_days == 30


def test_walk_forward_windows_forward_only_with_purge():
    ws = walk_forward_windows(0, 400 * 86_400_000, train_days=180, test_days=30, purge_bars=6, embargo_bars=6, tf="4h")
    assert len(ws) >= 5
    for a, b in zip(ws, ws[1:]):
        assert a.test_end <= b.test_end and b.test_start > a.test_start        # ileri yönlü
        assert a.test_start >= a.train_end + 12 * H4                           # purge+embargo boşluğu
        assert a.train_start == 0                                              # anchored


def _store_with_synthetic(tmp_path: Path, n=1200):
    st = HistoryStore(tmp_path / "hist")
    for i, sym in enumerate(("BTC/USDT", "AAA/USDT")):
        df = _candles(n, seed=11 + i, drift=0.0006 if i else 0.0002, vol=0.012, tf_ms=H4)
        st.write("futures", sym, "4h", df)
    return st


def _cfg(tmp_path: Path):
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    cfg = BotConfig(); cfg.project_root = tmp_path; cfg.scanner.enabled = False
    cfg.v3 = load_v3({"coin_heads": {"consensus_threshold": 0.05, "min_confidence": 0.05}})
    (cfg.state_path).mkdir(parents=True, exist_ok=True)
    return cfg


def test_replay_deterministic_isolated_and_resumable(tmp_path: Path):
    st = _store_with_synthetic(tmp_path)
    cfg = _cfg(tmp_path)
    live_ledger = cfg.state_path / "futures_ledger.json"
    live_ledger.write_text('{"schema_version": 2, "kind": "futures", "wallet_balance": "50", "positions": {}, "history": [], "entries": []}', encoding="utf-8")
    h0 = live_ledger.read_bytes()

    def run(run_id):
        r = HistoricalReplay(cfg, run_id=run_id, store=st, symbols=["BTC/USDT", "AAA/USDT"], market="futures", tf="4h", seed=7, decision_stride=2, min_bars=250)
        from test_patterns import T0
        return r.run(windows=walk_forward_windows(T0, T0 + 1200 * H4, train_days=60, test_days=20, tf="4h"))
    r1 = run("rep_a")
    r2 = run("rep_b")
    assert r1.n_decisions > 100 and r1.determinism_hash == r2.determinism_hash and len(r1.trades) == len(r2.trades)
    assert (cfg.state_path / "replay" / "rep_a" / "replay_result.json").exists() and (cfg.state_path / "replay" / "rep_a" / "trade_memory.jsonl").exists() or r1.n_opened == 0
    assert live_ledger.read_bytes() == h0                                      # gerçek PAPER state'e dokunulmadı
    for f in ("trade_memory.jsonl", "learn_v2.json"):
        assert not (cfg.state_path / f).exists()                               # replay hafızası/öğrenmesi gerçek state'e yazılmadı
    if r1.trades:
        mem = TradeMemory(cfg.state_path / "replay" / "rep_a" / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
        assert all(r["source"] == "HISTORICAL_REPLAY" for r in mem.iter_rows())
        assert r1.metrics["all"]["n"] == len(r1.trades) and "out_of_sample" in r1.metrics
    # replay state dizini gerçek state olamaz
    with pytest.raises(ValueError):
        HistoricalReplay(cfg, run_id="..", store=st, symbols=["BTC/USDT"], state_root=cfg.state_path / "replay")

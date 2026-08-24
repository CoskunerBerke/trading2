"""Quant Evaluation V1 — TAM `HistoricalReplay` zinciri uçtan uca (offline, ağsız).

Kanıtlanan zincir (ledger doğrudan çağrılarak ATLANMAZ):

    HistoryStore (üretim şeması)
    → data-quality gate
    → HistoricalReplay  (üretim CoinHead/Chief/RiskEngine karar yolu)
    → FuturesLedgerV2   (fee/funding/slippage)
    → TradeMemory       (HISTORICAL_REPLAY namespace)
    → quant.run         (journal → coverage → attribution → senaryolar → kanıt köprüsü)
    → quant raporu

Ayrıca: aynı dataset/config/seed ile İKİ ayrı tam replay → aynı trade sonuçları, aynı attribution
ve aynı sonuç hash'i; wall-clock/telemetri alanları karşılaştırmaya GİRMEZ.

Bu test sentetik ama üretim-şemalı veriyle çalışır ve AĞ GEREKTİRMEZ. Gerçek public-data smoke
ayrı bir komuttur (`scripts/quant_public_smoke.py`).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.core import read_json
from tradingbot.history.store import HistoryStore
from tradingbot.market.quality import DataQualityGate
from tradingbot.quant.run import main as quant_main
from tradingbot.replay.engine import HistoricalReplay, walk_forward_windows

H4 = 14_400_000
T0 = 1_700_000_000_000


def _candles(n=900, seed=1, drift=0.0004, vol=0.012, start=T0, tf_ms=H4) -> pd.DataFrame:
    """Deterministik, ÜRETİM şemalı mumlar (seed sabit → rastgelelik yok)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, vol, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, vol, n))
    v = rng.uniform(50, 150, n)
    ts = start + np.arange(n) * tf_ms
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c,
                         "volume": v, "quote_volume": v * c, "trades": 10,
                         "taker_buy_base": v * 0.5, "taker_buy_quote": v * c * 0.5,
                         "close_time": ts + tf_ms - 1})


def _store(tmp_path: Path) -> HistoryStore:
    st = HistoryStore(tmp_path / "hist")
    for i, sym in enumerate(("BTC/USDT", "AAA/USDT")):
        st.write("futures", sym, "4h", _candles(seed=11 + i, drift=0.0006 if i else 0.0002))
    return st


def _cfg(tmp_path: Path):
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.scanner.enabled = False
    cfg.v3 = load_v3({"coin_heads": {"consensus_threshold": 0.05, "min_confidence": 0.05}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    return cfg


def _run_replay(cfg, store, run_id: str):
    rep = HistoricalReplay(cfg, run_id=run_id, store=store, symbols=["BTC/USDT", "AAA/USDT"],
                           market="futures", tf="4h", seed=7, decision_stride=2, min_bars=250)
    windows = walk_forward_windows(T0, T0 + 900 * H4, train_days=60, test_days=20, tf="4h")
    return rep.run(windows=windows)


def _comparable(result) -> dict:
    """Karşılaştırma görünümü — wall-clock/telemetri ve dizin yolu HARİÇ."""
    d = result.to_dict()
    return {"trades": d["trades"], "metrics": {k: v for k, v in d["metrics"].items()
                                               if k not in ("learner", "ledger")},
            "n_decisions": d["n_decisions"], "n_actionable": d["n_actionable"],
            "n_opened": d["n_opened"], "windows": d["windows"],
            "determinism_hash": d["determinism_hash"], "rejections": d["rejections"]}


def test_full_historical_replay_chain_into_quant_report(tmp_path: Path):
    store = _store(tmp_path)
    cfg = _cfg(tmp_path)

    # --- 0) veri kalitesi kapısı gerçek üretim kapısıdır
    df = store.read("futures", "BTC/USDT", "4h")
    q = DataQualityGate().check_klines(df, "4h", int(df["timestamp"].iloc[-1]) + H4)
    assert q.verdict != "DATA_INVALID", q.codes

    # --- 1) canlı PAPER state'e dokunulmadığını kanıtlamak için önce imza al
    live_ledger = cfg.state_path / "futures_ledger.json"
    live_ledger.write_text('{"schema_version": 2, "kind": "futures", "wallet_balance": "50",'
                           ' "positions": {}, "history": [], "entries": []}', encoding="utf-8")
    live_sig = live_ledger.read_bytes()

    # --- 2) TAM HistoricalReplay (üretim karar yolu + gerçek ledger)
    res = _run_replay(cfg, store, "quant_e2e_a")
    assert res.n_decisions > 50, "replay üretim karar yolunu çalıştırmalı"
    replay_dir = Path(res.state_dir)
    mem_path = replay_dir / "trade_memory.jsonl"
    assert mem_path.exists(), "replay TradeMemory üretmeli"

    # canlı state DOKUNULMADI
    assert live_ledger.read_bytes() == live_sig
    assert not (cfg.state_path / "trade_memory.jsonl").exists()

    # --- 3) replay çıktısı → quant.run (üretim dosya biçimleri)
    shadow = tmp_path / "shadow_book.json"
    shadow.write_text(json.dumps({"trades": []}), encoding="utf-8")
    out = tmp_path / "quant_eval.json"
    rc = quant_main(["--memory", str(mem_path), "--shadow", str(shadow), "--out", str(out),
                     "--run-id", "e2e", "--code-sha", "test", "--min-sample", "1"])
    assert rc == 0
    doc = read_json(out)
    assert doc["schema_version"] == "quant_eval_v1"
    assert doc["journal"]["n_records"] >= 0
    # replay işlem ürettiyse maliyetler ve senaryolar gerçekten akmalı
    if res.trades:
        assert doc["journal"]["n_records"] > 0
        assert doc["overall"]["n"] > 0
        sc = doc["execution_scenarios"]["results"]
        assert set(sc) == {"base", "adverse", "stress"}
    # kanıt yok → fail-closed
    assert doc["champion_challenger"]["decision"] == "KEEP_CHAMPION"
    assert doc["champion_challenger"]["auto_promotion"] is False
    assert json.dumps(doc, allow_nan=False)                      # RFC-safe


def test_two_full_replays_are_identical_excluding_wallclock(tmp_path: Path):
    store = _store(tmp_path)
    cfg = _cfg(tmp_path)
    a = _run_replay(cfg, store, "det_a")
    b = _run_replay(cfg, store, "det_b")

    assert a.determinism_hash == b.determinism_hash
    assert len(a.trades) == len(b.trades)
    assert _comparable(a) == _comparable(b)                      # trade + metrik + fold aynı

    # telemetri (wall/cpu/memory) karşılaştırmaya girmez ama gerçekten AYRI alanda durur
    assert "telemetry" in a.to_dict()
    assert "telemetry" not in _comparable(a)

    # attribution da aynı olmalı → aynı quant raporu
    shadow = tmp_path / "shadow.json"
    shadow.write_text(json.dumps({"trades": []}), encoding="utf-8")
    outs = []
    for tag, res in (("a", a), ("b", b)):
        o = tmp_path / f"q_{tag}.json"
        assert quant_main(["--memory", str(Path(res.state_dir) / "trade_memory.jsonl"),
                           "--shadow", str(shadow), "--out", str(o),
                           "--run-id", "fixed", "--code-sha", "fixed",
                           "--min-sample", "1"]) == 0
        d = read_json(o)
        d.pop("manifest", None)                                  # run_id/sha sabit ama yine de ayrık tut
        outs.append(d)
    assert outs[0] == outs[1]


def test_replay_state_is_isolated_from_live(tmp_path: Path):
    store = _store(tmp_path)
    cfg = _cfg(tmp_path)
    res = _run_replay(cfg, store, "iso_a")
    rdir = Path(res.state_dir)
    assert rdir.is_relative_to(cfg.state_path / "replay")
    for leaked in ("trade_memory.jsonl", "learn_v2.json", "models.json"):
        assert not (cfg.state_path / leaked).exists()             # canlı state'e sızmadı
    from tradingbot.learn.memory import TradeMemory
    mem = TradeMemory(rdir / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    rows = list(mem.iter_rows())
    assert all(r.get("source") == "HISTORICAL_REPLAY" for r in rows)


def test_replay_result_reports_survivorship_honestly(tmp_path: Path):
    res = _run_replay(_cfg(tmp_path), _store(tmp_path), "surv_a")
    d = res.to_dict()
    assert d["point_in_time"] is False                            # point-in-time evren YOK
    assert d["survivorship_bias"]["present"] is True              # dürüstçe bildiriliyor

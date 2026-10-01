"""Aday sonuç etiketleyici (KANIT ONARIMI V1) — sözleşme testleri.

  * üçlü bariyer: hedef/stop/zaman, aynı bar → STOP, giriş = ts'deki ilk bar AÇILIŞI, SHORT simetrik
  * label_pending: ufuk, idempotentlik, spot atlanır, eksik geometri atlanır, tavan (en eski önce),
    sağlayıcı arızası diğer sembolleri durdurmaz, satır şeması, özet
  * motor kancası: kapalıyken ağa çıkmaz; açıkken durum belgesi yazar
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.learn.outcome_labeler import (OUTCOME_STOP, OUTCOME_TARGET, OUTCOME_TIMEOUT, SCHEMA_VERSION,  # noqa: E402
                                              OutcomeStore, frame_to_bars, label_pending, summarize, triple_barrier)

H = 3_600_000
T0 = 1_788_000_000_000            # 2026-08-30 civarı, ms


def _bars(start_ms, closes, *, spread=0.5):
    """Her saat bir bar: open=önceki close, high/low = close ± spread. Boş seri → boş liste."""
    if not closes:
        return []
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append((start_ms + i * H, prev, max(prev, c) + spread, min(prev, c) - spread, c))
        prev = c
    return out


# ----------------------------------------------------------------------------- üçlü bariyer
def test_01_target_first_is_win_and_entry_is_market_open():
    bars = _bars(T0, [100, 101, 103, 106, 104])                  # ilk bar open=100
    r = triple_barrier(bars, start_ms=T0, stop=97.5, target=105.0, long_=True, horizon_ms=48 * H, cost_r=0.16)
    assert r["outcome"] == OUTCOME_TARGET and r["label"] == 1
    assert r["entry_ref"] == 100.0                                # plan girişi DEĞİL, ts'deki açılış
    assert abs(r["r_net"] - (5.0 / 2.5 - 0.16)) < 1e-9 and r["bars"] == 4


def test_01b_stop_first_is_loss_and_same_bar_is_stop():
    bars = _bars(T0, [100, 99, 96, 110])
    r = triple_barrier(bars, start_ms=T0, stop=97.5, target=105.0, long_=True, horizon_ms=48 * H, cost_r=0.16)
    assert r["outcome"] == OUTCOME_STOP and r["label"] == 0 and abs(r["r_net"] + 1.16) < 1e-9
    wide = [(T0, 100.0, 106.0, 97.0, 101.0)]                      # aynı barda stop da hedef de → STOP
    r = triple_barrier(wide, start_ms=T0, stop=97.5, target=105.0, long_=True, horizon_ms=48 * H, cost_r=0.16)
    assert r["outcome"] == OUTCOME_STOP


def test_01c_timeout_is_marked_to_market_and_short_is_symmetric():
    bars = _bars(T0, [100, 100.5, 101, 101.5])
    r = triple_barrier(bars, start_ms=T0, stop=97.5, target=105.0, long_=True, horizon_ms=3 * H, cost_r=0.16)
    assert r["outcome"] == OUTCOME_TIMEOUT and r["label"] is None
    assert abs(r["r_net"] - ((101.5 - 100) / 2.5 - 0.16)) < 1e-9
    bars = _bars(T0, [100, 99, 97, 94])                           # SHORT: hedef aşağıda
    r = triple_barrier(bars, start_ms=T0, stop=102.5, target=95.0, long_=False, horizon_ms=48 * H, cost_r=0.16)
    assert r["outcome"] == OUTCOME_TARGET and r["label"] == 1
    r = triple_barrier(bars, start_ms=T0, stop=97.5, target=105.0, long_=True, horizon_ms=48 * H, cost_r=0.16)
    assert r["outcome"] == OUTCOME_STOP
    assert triple_barrier([], start_ms=T0, stop=97.5, target=105.0, long_=True, horizon_ms=H, cost_r=0.16) is None
    bad = triple_barrier(_bars(T0, [100, 101]), start_ms=T0, stop=101.0, target=105.0, long_=True, horizon_ms=H, cost_r=0.16)
    assert bad["outcome"] == "INVALID" and bad["label"] is None   # stop girişin yanlış tarafında


def test_01d_frame_to_bars_accepts_ms_seconds_and_timestamps():
    df = pd.DataFrame({"timestamp": [T0, T0 + H], "open": [1, 2], "high": [3, 4], "low": [0.5, 1.5], "close": [2, 3]})
    assert [b[0] for b in frame_to_bars(df)] == [T0, T0 + H]
    df2 = df.assign(timestamp=[T0 / 1000, (T0 + H) / 1000])
    assert [b[0] for b in frame_to_bars(df2)] == [T0, T0 + H]
    df3 = df.assign(timestamp=pd.to_datetime([T0, T0 + H], unit="ms", utc=True))
    assert [b[0] for b in frame_to_bars(df3)] == [T0, T0 + H]
    assert frame_to_bars(pd.DataFrame({"x": [1]})) == []


# ----------------------------------------------------------------------------- label_pending
class _Prov:
    name = "fake_fapi"

    def __init__(self, series: dict[str, list[float]], fail: set[str] = frozenset()):
        self.series, self.fail, self.calls = series, set(fail), []

    def klines(self, symbol, interval, limit=1000, start_ms=None, end_ms=None):
        self.calls.append((symbol, interval, limit, start_ms))
        if symbol in self.fail:
            raise RuntimeError("provider down")
        closes = self.series.get(symbol) or []
        bars = _bars(T0, closes)
        rows = [b for b in bars if start_ms is None or b[0] >= start_ms][:limit]
        return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])


def _snap(cid, symbol, *, ts_ms=T0, direction="LONG", entry=100.0, stop=97.5, targets=(105.0, 107.5), market="futures", **extra):
    return {"candidate_id": cid, "symbol": symbol, "direction": direction, "ts_ms": ts_ms,
            "ts": pd.Timestamp(ts_ms, unit="ms", tz="UTC").isoformat(), "entry_price": entry, "stop_price": stop,
            "targets": list(targets), "market_type": market, **extra}


def test_02_label_pending_labels_due_only_and_is_idempotent(tmp_path):
    store = OutcomeStore(tmp_path / "entry_outcomes.jsonl")
    prov = _Prov({"ETH/USDT": [100, 101, 103, 106, 104] + [104] * 200, "SOL/USDT": [100, 99, 96, 95] + [95] * 200})
    now = T0 + 168 * H + 5 * H
    rows = [_snap("c1", "ETH/USDT"), _snap("c2", "SOL/USDT"),
            _snap("c3", "ETH/USDT", ts_ms=T0 + 100 * H),                    # ufuk dolmadı
            _snap("c4", "BTC/USDT", market="spot"),                           # spot atlanır
            _snap("c5", "ADA/USDT", entry=None),                              # geometri eksik
            {"kind": "link", "candidate_id": "c1", "trade_id": "F1"}]          # link satırı snapshot değil
    st = label_pending(rows, store, prov, now_ms=now, horizon_h=168, cost_r=0.16, max_symbols=15, run_id="r1")
    assert st.considered == 5 and st.due == 2 and st.not_due == 1 and st.skipped_spot == 1 and st.skipped_invalid == 1
    assert st.labeled == 2 and st.wins == 1 and st.losses == 1 and st.symbols_fetched == 2 and not st.errors
    got = {r["candidate_id"]: r for r in store.iter_rows()}
    assert set(got) == {"c1", "c2"}
    r1 = got["c1"]
    assert r1["schema_version"] == SCHEMA_VERSION and r1["kind"] == "outcome" and r1["outcome"] == OUTCOME_TARGET
    assert r1["label"] == 1 and r1["entry_ref"] == 100.0 and r1["entry_ref_kind"] == "MARKET_OPEN_AT_TS"
    assert r1["horizon_h"] == 168 and r1["cost_r"] == 0.16 and r1["run_id"] == "r1" and r1["source"] == "fake_fapi"
    assert got["c2"]["outcome"] == OUTCOME_STOP and got["c2"]["label"] == 0
    # ikinci çalıştırma: hiçbir şey yeniden etiketlenmez, sağlayıcıya gidilmez
    n_calls = len(prov.calls)
    st2 = label_pending(rows, store, prov, now_ms=now, horizon_h=168, max_symbols=15)
    assert st2.labeled == 0 and st2.already_labeled == 2 and len(prov.calls) == n_calls
    s = summarize(store)
    assert s["n"] == 2 and s["wins"] == 1 and s["losses"] == 1 and s["win_rate_resolved"] == 0.5 and s["n_r"] == 2


def test_02b_symbol_cap_oldest_first_and_provider_failure_isolated(tmp_path):
    store = OutcomeStore(tmp_path / "o.jsonl")
    prov = _Prov({"A/USDT": [100] * 200, "B/USDT": [100] * 200, "C/USDT": [100] * 200}, fail={"B/USDT"})
    now = T0 + 400 * H
    rows = [_snap("a", "A/USDT", ts_ms=T0 + 10 * H), _snap("b", "B/USDT", ts_ms=T0), _snap("c", "C/USDT", ts_ms=T0 + 20 * H)]
    st = label_pending(rows, store, prov, now_ms=now, horizon_h=168, max_symbols=2)
    fetched = [c[0] for c in prov.calls]
    assert fetched == ["B/USDT", "A/USDT"] and st.symbols_deferred == 1        # en eski önce: B (T0), A (T0+10h)
    assert "B/USDT" in st.errors[0] and st.labeled == 1 and st.timeouts == 1  # A zaman aşımıyla etiketlendi
    st = label_pending(rows, store, prov, now_ms=now, horizon_h=168, max_symbols=5)
    assert st.labeled == 1 and st.already_labeled == 1 and len(st.errors) == 1  # C etiketlendi, B yine arızalı


def test_02c_no_bars_counts_as_no_data_and_store_survives_bad_lines(tmp_path):
    p = tmp_path / "o.jsonl"
    p.write_text('{"candidate_id":"x","outcome":"TARGET","r_net":1.0}\nnot json\n\n', encoding="utf-8")
    store = OutcomeStore(p)
    assert store.labeled_ids() == {"x"}
    prov = _Prov({})
    st = label_pending([_snap("y", "Z/USDT")], store, prov, now_ms=T0 + 400 * H, horizon_h=168)
    assert st.no_data == 1 and st.labeled == 0 and st.symbols_fetched == 1


# ----------------------------------------------------------------------------- motor kancası
class _Store:
    """Sahte snapshot deposu: kanca yalnız SICAK satırları okur (arşiv sıcak döngüde açılmaz)."""

    def __init__(self, rows):
        self._rows = rows
        self.archive_opened = False

    def iter_hot_rows(self):
        return iter(self._rows)

    def iter_all_rows(self):
        self.archive_opened = True
        return iter(self._rows)


def test_03_engine_hook_disabled_by_default_and_writes_status_when_enabled(tmp_path, monkeypatch):
    from tradingbot.core import utc_now
    prov = _Prov({"ETH/USDT": [100, 101, 103, 106] + [106] * 300})
    eng = TE._engine(tmp_path / "off", monkeypatch)
    eng._gap_provider_factory = lambda: prov
    assert eng._label_entry_outcomes(utc_now()).get("skipped") == "disabled" and not prov.calls
    eng = TE._engine(tmp_path / "on", monkeypatch, v3_overrides={"learning_v3": {"outcome_labeling_enabled": True}})
    eng._gap_provider_factory = lambda: prov
    now = utc_now()
    ts = int(now.timestamp() * 1000) - 200 * H
    eng.entry_snapshot_store = _Store([_snap("c1", "ETH/USDT", ts_ms=ts)])
    # sahte sağlayıcı barları T0'dan üretir; adayın ts'i sonrasına düşsün diye seri T0'a göre uzun
    prov.series["ETH/USDT"] = [100] * ((ts - T0) // H + 2) + [101, 103, 106] + [106] * 200
    doc = eng._label_entry_outcomes(now)
    assert doc.get("ok") and doc["labeled"] == 1 and doc["wins"] == 1
    assert eng.entry_snapshot_store.archive_opened is False        # sıcak döngü arşivi AÇMAZ
    status = json.loads((eng.cfg.state_path / "entry_outcomes_status.json").read_text(encoding="utf-8"))
    assert status["labeled"] == 1 and status["summary"]["n"] == 1 and status["run_id"] == eng.run_id
    rows = [json.loads(l) for l in (eng.cfg.state_path / "entry_outcomes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[0]["candidate_id"] == "c1" and rows[0]["outcome"] == OUTCOME_TARGET

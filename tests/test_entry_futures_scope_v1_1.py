"""Futures-kovası giriş bağlamı (snapshot) + entry_v1.1.0 E ailesi — kapsam eşleme paketi.

Doğrulanmış kusur (2026-09-07, F00036): `portfolio_open_risk_usdt` = futures stop riski
(4.850588) + STOPSUZ spot BNB tam notional (8.226) = 13.076588 — panelde
`diagnostic_ratio_not_enforced` etiketli BİRLEŞİK tanı değeri — `risk_budget_usdt` = 6.0 ise
yalnız FUTURES kovasının uygulanan bütçesi. Aynı birim, farklı kapsam. entry_v1.0.0 E ailesi bu
ikisini bölüyordu.

Onarım (ileriye dönük): snapshot'a kapsamı eşleşen iki yeni alan (`portfolio_futures_stop_
risk_usdt`, `same_direction_open_futures`) eklenir; eski alanlar AYNEN korunur ve birleşik tanı
olarak belgelenir; entry_v1.1.0 E yalnız yeni alanları + snapshot'ta DONMUŞ bütçeyi kullanır,
eksikte açık ABSTAIN eder ve eski alana ASLA düşmez. entry_v1.0.0 tarihsel kayıtlar için aynen
okunur.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn.entry_snapshot import MEASURED, MISSING, build_entry_snapshot

# --------------------------------------------------------------- fixture: F00036 benzeri durum


def _pos(sym, side, risk, market="USDM_PERP", notional=20.0, stop=95.0):
    return types.SimpleNamespace(symbol=sym, market_type=market, side=side, risk_usdt=risk,
                                 notional=notional, stop=stop, entry=100.0)


def state_f00036_like():
    """11 futures (10 LONG + 1 SHORT; stop riski toplam 4.850588 = F00036 anı) + 1 STOPSUZ SPOT BNB LONG (8.226)."""
    fut_risks = [0.561851, 0.0072, 0.01508, 0.444606, 0.581012, 0.567561, 0.01729, 0.218442,
                 0.529662, 1.368637]                           # 10 LONG
    rows = [_pos(f"L{i}/USDT", "LONG", r) for i, r in enumerate(fut_risks)]
    rows.append(_pos("GOOGL/USDT", "SHORT", 0.539249))
    rows.append(_pos("BNB/USDT", "LONG", 8.226, market="SPOT", notional=8.226, stop=None))
    combined = sum(p.risk_usdt for p in rows)
    return types.SimpleNamespace(equity=100.0, starting_equity=100.0, open_positions=rows,
                                 total_open_risk_usdt=combined)


class _Plan:
    valid = True
    entry = 0.3892
    stop = 0.36823195984508783
    targets = (0.4311360803098243,)
    entry_type = "kirilim"
    notional = 30.0
    leverage = 1.0


class _Decision:
    direction = "LONG"
    specialist_reports = ()
    opportunity: dict = {"conservative_net_edge_r": 0.677204, "avg_win_r": 1.851148,
                         "avg_loss_r": -1.0, "sample_size": 12}
    p_win = 0.293
    market_type = "USDM_PERP"


def _engine(tmp_path: Path):
    from tradingbot.learn.entry_snapshot import EntrySnapshotStore
    eng = types.SimpleNamespace(
        entry_snapshot_store=EntrySnapshotStore(tmp_path / "entry_snapshot.jsonl", max_per_cycle=50),
        _entry_pending=[], runner=types.SimpleNamespace(last_frames={}),
        profile=types.SimpleNamespace(size_on_live_equity=False, max_total_open_risk_pct=6.0))
    eng._decision_time_risk_budget = lambda st: TradingEngineV3._decision_time_risk_budget(eng, st)
    eng._futures_bucket_context = TradingEngineV3._futures_bucket_context
    return eng


def _capture(eng, state, *, direction="LONG"):
    from tradingbot.core import utc_now
    dec = _Decision()
    dec.direction = direction
    TradingEngineV3._entry_capture(eng, "ONDO/USDT", dec, _Plan(), {"allow": True}, state,
                                   {"risk_allowed": True, "risk_reasons": [], "trade_id": None},
                                   "USDM_PERP", utc_now())
    assert eng._entry_pending, "aday tampona alınmadı"
    return eng._entry_pending[-1]["chief"]


def _snap_from(ctx: dict) -> dict:
    return build_entry_snapshot(run_id="r", cycle_id="85", symbol="ONDO/USDT", direction="LONG",
                                decision=_Decision(), plan=_Plan(), chief_permission=ctx,
                                policy_version="entry_v1.1.0", code_sha="x", config_hash="y")


# --------------------------------------------------------------- 1-2: alanlar var

def test_01_02_new_snapshots_contain_futures_bucket_fields(tmp_path: Path):
    ctx = _capture(_engine(tmp_path), state_f00036_like())
    s = _snap_from(ctx)
    assert s["portfolio_futures_stop_risk_usdt"] == pytest.approx(4.850588)
    assert s["same_direction_open_futures"] == 10
    assert s["sources"]["portfolio_futures_stop_risk_usdt"] == MEASURED
    assert s["sources"]["same_direction_open_futures"] == MEASURED
    assert s["portfolio_scope"]["portfolio_futures_stop_risk_usdt"] == "FUTURES_STOP_RISK_BUCKET"
    assert s["portfolio_scope"]["risk_budget_usdt"] == "FUTURES_STOP_RISK_BUCKET"
    assert s["portfolio_scope"]["candidate_included"] is False
    assert s["portfolio_scope"]["measured_at"] == "RANKING_PRE_ENTRY"


# --------------------------------------------------------------- 3: fill'den ÖNCE donar

def test_03_values_are_frozen_before_ledger_open(tmp_path: Path):
    eng = _engine(tmp_path)
    st = state_f00036_like()
    ctx = _capture(eng, st)
    before = json.dumps(ctx, sort_keys=True, default=str)
    # "Fill" simülasyonu: aday defterde açıldı, durum yenilendi — tampondaki değerler DEĞİŞMEZ.
    st.open_positions.append(_pos("ONDO/USDT", "LONG", 1.138621))
    st.total_open_risk_usdt += 1.138621
    assert json.dumps(eng._entry_pending[-1]["chief"], sort_keys=True, default=str) == before
    assert ctx["futures_stop_risk_usdt"] == pytest.approx(4.850588)
    assert ctx["same_direction_open_futures"] == 10
    # Motor kaynak sırası: capture → defter açılışı → flush (aynı giriş döngüsünde). Defter açılışı
    # artık `_execute_futures_entry(...)` yardımcısı üzerinden yapılır (yürütme hassasiyeti: kural
    # nesnesi önizleme→risk→dolum boyunca aynı kalır); sıra sözleşmesi ÇAĞRI YERİ ile ölçülür.
    src = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    i_cap, i_open, i_flush = (src.index("self._entry_capture(sym"), src.index("self._execute_futures_entry("),
                              src.index("self._entry_flush(now)"))
    assert i_cap < i_open < i_flush
    # Yardımcı gerçekten defteri açar (ikinci bir açılış yolu yok).
    helper = src[src.index("def _execute_futures_entry("):]
    assert "self.ledger2.open(" in helper[:helper.index("def _trigger_fired(")]
    assert src.count("self.ledger2.open(") == 1


# --------------------------------------------------------------- 4-5: SPOT ve aday HARİÇ

def test_04_spot_holdings_never_enter_the_futures_fields(tmp_path: Path):
    ctx = _capture(_engine(tmp_path), state_f00036_like())
    assert ctx["futures_stop_risk_usdt"] == pytest.approx(4.850588)      # 8.226 spot YOK
    assert ctx["same_direction_open_futures"] == 10                     # spot LONG YOK
    # Birleşik tanı alanları spotu İÇERİR (mevcut anlam korunur).
    assert ctx["open_positions"] == 12
    assert ctx["same_direction_open"] == 11
    assert ctx["total_open_risk_usdt"] == pytest.approx(13.076588)
    # SHORT aday için yön sayımı: futures SHORT 1 (spot LONG sayılmaz); birleşik de 1.
    ctx_s = _capture(_engine(tmp_path / "s"), state_f00036_like(), direction="SHORT")
    assert ctx_s["same_direction_open_futures"] == 1 and ctx_s["same_direction_open"] == 1


def test_05_current_candidate_is_excluded_from_pre_entry_fields():
    fut = TradingEngineV3._futures_bucket_context
    rows = [_pos("A/USDT", "LONG", 1.0), _pos("B/USDT", "LONG", 2.0),
            _pos("BNB/USDT", "LONG", 8.0, market="SPOT", stop=None)]
    out = fut(rows, "LONG")
    assert out == {"futures_stop_risk_usdt": 3.0, "same_direction_open_futures": 2}
    # Aday listede DEĞİL; "sonradan eklense" bile önceki ölçüm değişmez (saf fonksiyon).
    rows2 = rows + [_pos("ONDO/USDT", "LONG", 1.1386)]
    assert fut(rows, "LONG") == out and fut(rows2, "LONG")["futures_stop_risk_usdt"] == 4.1386


# --------------------------------------------------------------- 6: eski alanlar aynı

def test_06_existing_combined_diagnostic_fields_are_unchanged(tmp_path: Path):
    ctx = _capture(_engine(tmp_path), state_f00036_like())
    s = _snap_from(ctx)
    assert s["portfolio_open_positions"] == 12.0
    assert s["same_direction_open"] == 11.0
    assert s["portfolio_open_risk_usdt"] == pytest.approx(13.076588)
    assert s["risk_budget_usdt"] == 6.0
    for k in ("portfolio_open_positions", "portfolio_open_risk_usdt", "same_direction_open",
              "risk_budget_usdt"):
        assert s["sources"][k] == MEASURED
    assert s["portfolio_scope"]["portfolio_open_risk_usdt"] == "COMBINED_SPOT_FUTURES_DIAGNOSTIC"
    # Eski üretici: ctx anahtarları aynen (geriye uyumluluk).
    for k in ("open_positions", "total_open_risk_usdt", "same_direction_open", "risk_budget_usdt"):
        assert k in ctx


# --------------------------------------------------------------- 11: ölçülmüş sıfır / eksik

def test_11_measured_zero_stays_zero_and_missing_stays_missing(tmp_path: Path):
    only_spot = types.SimpleNamespace(equity=100.0, starting_equity=100.0,
                                      open_positions=[_pos("BNB/USDT", "LONG", 8.226, market="SPOT", stop=None)],
                                      total_open_risk_usdt=8.226)
    ctx = _capture(_engine(tmp_path), only_spot)
    assert ctx["futures_stop_risk_usdt"] == 0.0 and ctx["same_direction_open_futures"] == 0
    s = _snap_from(ctx)
    assert s["portfolio_futures_stop_risk_usdt"] == 0.0
    assert s["sources"]["portfolio_futures_stop_risk_usdt"] == MEASURED
    assert s["same_direction_open_futures"] == 0.0
    # Bir futures pozisyonunun riski ölçülemiyorsa toplam UYDURULMAZ → None → MISSING.
    bad = TradingEngineV3._futures_bucket_context([_pos("A/USDT", "LONG", None)], "LONG")
    assert bad["futures_stop_risk_usdt"] is None and bad["same_direction_open_futures"] == 1
    s2 = build_entry_snapshot(run_id="r", cycle_id="c", symbol="X/USDT", direction="LONG",
                              chief_permission={"open_positions": 1})
    assert s2["portfolio_futures_stop_risk_usdt"] is None
    assert s2["sources"]["portfolio_futures_stop_risk_usdt"] == MISSING
    assert "portfolio_futures_stop_risk_usdt" in s2["missing_fields"]
    assert s2["sources"]["same_direction_open_futures"] == MISSING

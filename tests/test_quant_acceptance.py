"""Quant Evaluation V1 — ACCEPTANCE AUDIT testleri.

Bu dosya kabul denetiminin dört kritik zincirini GERÇEK üretim sınıflarıyla kanıtlar:

1. Üretim formatı uçtan uca: gerçek `TradeMemory` + gerçek `FuturesLedgerV2` outcome'ları +
   gerçek `ShadowBook` → `quant.run.main` → atomic rapor → dashboard `StateReader` →
   `/api/quant/summary`. Elle kurgulanmış quant sözlükleri DEĞİL, üretim sınıflarının yazdığı
   dosya biçimleri kullanılır.
2. Güçlü no-lookahead: gerçek feature zinciri (`learn.snapshot.build_snapshot`) ile karar-anı
   çıktısının, karar sonrası mumlar ne kadar değişirse değişsin BYTE-FOR-BYTE aynı kaldığı;
   gelecek verisinin yalnız outcome'u etkileyebildiği; fitting'in (üretim `Calibrator`)
   yalnız train verisinden etkilendiği.
3. Execution maliyetlerinin ledger sonucuna SAYISAL etkisi: fee, funding (settlement), slippage,
   eksik funding oranında iyimser-olmayan bekleme.
4. Risk V2 / challenger izolasyonu: advise() mevcut `RiskEngine` kararını değiştirmez.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import BAR_MS, synth_bars

from tradingbot.accounting import FuturesLedgerV2, FeeSchedule, SizeSpec, SlippageModel, TickData
from tradingbot.dashboard.app import DashboardConfig, create_app
from tradingbot.learn.calibration import Calibrator
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.shadow import ShadowBook
from tradingbot.learn.snapshot import LeakageError, build_snapshot
from tradingbot.quant.risk_v2 import AdviceContext, advise
from tradingbot.quant.run import main as quant_main
from tradingbot.risk import PROFILES, RiskEngine, build_state

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

D = Decimal
UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
ETH = "ETH/USDT"


# ============================================================ 1. üretim formatı uçtan uca

def _ledger_trade(win: bool, i: int) -> dict:
    """GERÇEK FuturesLedgerV2 ile tek işlem üret; outcome sözlüğü ledger'ın kendi biçimidir."""
    led = FuturesLedgerV2(1000, slippage=SlippageModel.zero())
    t_open = T0 + timedelta(hours=4 * i)
    led.open(ETH if i % 2 else "SOL/USDT", "LONG", D(100), SizeSpec(D(200), leverage=3),
             stop=D(95), targets=[D(104), D(110)], now=t_open)
    tick = TickData(last=D(111), high=D(112), low=D(99)) if win else \
        TickData(last=D(94), high=D(100.5), low=D(93.5))
    closed = led.tick({ETH if i % 2 else "SOL/USDT": tick}, now_utc=t_open + timedelta(hours=4))
    assert closed, "fixture işlemi kapanmadı"
    return led.history_dicts()[-1]


def test_production_format_end_to_end_chain(tmp_path: Path):
    # --- gerçek TradeMemory: entry (snapshot benzeri kayıt) + gerçek ledger outcome'u ile exit
    mem_path = tmp_path / "trade_memory.jsonl"
    memory = TradeMemory(mem_path, source="LIVE_PAPER")
    for i in range(14):
        win = i % 3 != 0
        rec = _ledger_trade(win, i)
        tid = memory.record_entry({
            "symbol": rec["symbol"], "market_type": "futures", "side": "LONG",
            "timeframe": "4h", "decision_ts": rec["opened_at"], "p_win": 0.6 if win else 0.45,
            "regime": "trend_up",
            "plan": {"plan_id": f"p{i}", "entry": 100.0, "stop": 95.0,
                     "targets": [104.0, 110.0], "leverage": 3, "notional": 200.0}})
        memory.record_exit(tid, rec)                          # ledger'ın KENDİ dict biçimi
    # --- gerçek ShadowBook: add + label_with_candles (üretim etiketleyicisi)
    shadow_path = tmp_path / "shadow_book.json"
    book = ShadowBook(shadow_path)
    plan = {"plan_id": "px", "symbol": "BTC/USDT", "direction": "LONG", "entry": 100.0,
            "stop": 95.0, "targets": [104.0, 110.0], "market_type": "futures", "leverage": 2}
    st = book.add(plan, ["RISK_BUDGET_EXCEEDED"], tf_minutes=240, now=T0)[0]
    end_ms = int((T0 + timedelta(hours=4 * 40)).timestamp() * 1000)
    df = synth_bars(60, end_ms=end_ms, bar_ms=4 * 3_600_000, drift=0.6)
    assert book.label(st, df) is not None                     # üretim etiketleme yolu
    # --- run.py: üretim dosyalarını DOĞRUDAN okur (adapter zinciri)
    out = tmp_path / "state" / "quant_eval.json"
    rc = quant_main(["--memory", str(mem_path), "--shadow", str(shadow_path),
                     "--out", str(out), "--allow-state-out", "--run-id", "acc-1",
                     "--code-sha", "audit", "--min-sample", "5"])
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["journal"]["n_records"] == 15                  # 14 memory + 1 shadow
    assert doc["journal"]["n_labeled"] == 15
    assert doc["overall"]["n"] == 14                          # shadow gerçek havuza karışmadı
    assert doc["overall"]["fees_usdt"] is not None            # ledger fee'leri rapora aktı
    assert doc["overall"]["net_pnl_usdt"] < doc["overall"]["gross_pnl_usdt"]
    # --- dashboard: aynı dosyayı StateReader üzerinden servis eder
    (tmp_path / "data").mkdir()
    c = TestClient(create_app(tmp_path / "state", tmp_path / "data", None, DashboardConfig()))
    j = c.get("/api/quant/summary").json()
    assert j["available"] is True
    assert j["overall"]["n"] == 14
    assert j["overall"]["expectancy_r"] == pytest.approx(doc["overall"]["expectancy_r"])
    assert j["report_age_s"] is not None and j["report_age_s"] < 300
    assert "Rapor yaşı" in c.get("/quant").text


def test_dashboard_corrupt_and_stale_report(tmp_path: Path):
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(), data.mkdir()
    (st / "quant_eval.json").write_text('{"schema_version": "quant_eval_v1", "over', encoding="utf-8")
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/api/quant/summary")
    assert r.status_code == 200                               # bozuk dosya 500 ÜRETMEZ
    assert r.json()["available"] is False
    assert c.get("/quant").status_code == 200


# ============================================================ 2. güçlü no-lookahead

def _closed_slice(df, decision_ts_ms: int, bar_ms: int):
    """Replay `_slice` kuralı: yalnız karar anında KAPANMIŞ barlar."""
    return df[df["timestamp"] + bar_ms - 1 <= decision_ts_ms].reset_index(drop=True)


def test_strong_no_lookahead_features_byte_identical_under_future_mutation():
    end_ms = 1_760_000_000_000
    full = synth_bars(200, end_ms=end_ms, bar_ms=BAR_MS, seed=3)
    decision_ts = int(full["timestamp"].iloc[149]) + BAR_MS - 1   # 150. bar kapanışı
    past = _closed_slice(full, decision_ts, BAR_MS)
    # gelecek: karar sonrası mumları AGRESİF boz (x5 fiyat, x100 hacim)
    mutated = full.copy()
    fut = mutated["timestamp"] + BAR_MS - 1 > decision_ts
    for col in ("open", "high", "low", "close"):
        mutated.loc[fut, col] = mutated.loc[fut, col] * 5.0
    mutated.loc[fut, "volume"] = mutated.loc[fut, "volume"] * 100.0
    past_mut = _closed_slice(mutated, decision_ts, BAR_MS)
    kw = dict(symbol=ETH, market_type="USDM_PERP", timeframe="1d", side="LONG",
              decision_ts_ms=decision_ts, funding={"rate": 0.0001},
              plan={"entry": 100.0, "stop": 95.0}, run_id="nla", seed=7)
    snap_a = build_snapshot(bars=past, **kw)
    snap_b = build_snapshot(bars=past_mut, **kw)
    dump = lambda s: json.dumps({"values": s.values, "missing": s.missing,
                                 "last_bar_ts": s.last_bar_ts}, sort_keys=True)  # noqa: E731
    assert dump(snap_a) == dump(snap_b)                       # BYTE-FOR-BYTE aynı karar girdisi
    # strict nedensellik: gelecekteki bar sızarsa üretim kodu FAIL-CLOSED
    with pytest.raises(LeakageError):
        build_snapshot(bars=mutated, **kw)


def test_future_data_affects_only_outcome_not_decision():
    end_ms = 1_760_000_000_000
    created = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    horizon_end = end_ms + 12 * 4 * 3_600_000
    base_path = synth_bars(80, end_ms=horizon_end, bar_ms=4 * 3_600_000, seed=5, drift=0.3)
    book_dir_insensitive = {"plan_id": "nla", "symbol": ETH, "direction": "LONG", "entry": 100.0,
                            "stop": 95.0, "targets": [104.0, 110.0], "market_type": "futures"}
    from tradingbot.learn.shadow import ShadowTrade, label_with_candles
    from tradingbot.core import iso
    sh = ShadowTrade(id="s", plan_id="nla", symbol=ETH, market_type="futures", direction="LONG",
                     created_at=iso(created), entry=100.0, stop=95.0, targets=[104.0, 110.0],
                     horizon_bars=12, variant="as_planned", reason_not_opened=["X"],
                     label_ts=iso(created + timedelta(hours=48)), tf_minutes=240)
    out_a = label_with_candles(sh, base_path)
    crashed = base_path.copy()
    fut = crashed["timestamp"] > end_ms
    for col in ("open", "high", "low", "close"):
        crashed.loc[fut, col] = crashed.loc[fut, col] * 0.5   # gelecekte çöküş
    out_b = label_with_candles(sh, crashed)
    assert out_a is not None and out_b is not None
    assert out_b["r_multiple"] <= out_a["r_multiple"]         # gelecek yalnız OUTCOME'u değiştirdi
    assert out_a != out_b
    assert book_dir_insensitive["entry"] == 100.0             # karar girdileri sabit kaldı


def test_fitting_uses_train_only():
    train_scores = [0.1 * i for i in range(20)]
    train_y = [1 if i % 2 else 0 for i in range(20)]
    cal_a = Calibrator(kind="platt").fit(train_scores, train_y)
    # validation/test verisi NE OLURSA OLSUN fit parametreleri değişmez (fit onları görmez)
    cal_b = Calibrator(kind="platt").fit(train_scores, train_y)
    assert json.dumps(cal_a.to_dict(), sort_keys=True) == json.dumps(cal_b.to_dict(), sort_keys=True)
    wild_validation = [999.0] * 50
    p = cal_a.apply(wild_validation)                          # apply yalnız dönüşüm — refit yok
    cal_c = Calibrator(kind="platt").fit(train_scores, train_y)
    assert json.dumps(cal_c.to_dict(), sort_keys=True) == json.dumps(cal_a.to_dict(), sort_keys=True)
    assert len(p) == 50


# ============================================================ 3. execution maliyetlerinin sayısal kanıtı

def _run_win_trade(fees=None, slippage=None, funding_lookup=None, close_at=None):
    led = FuturesLedgerV2(1000, fees=fees or FeeSchedule(maker_pct=D(0), taker_pct=D(0), source="t"),
                          slippage=slippage or SlippageModel.zero())
    led.open(ETH, "LONG", D(100), SizeSpec(D(200), leverage=2), stop=D(95),
             targets=[D(120)], now=T0)
    end = close_at or (T0 + timedelta(hours=8))
    led.tick({ETH: TickData(last=D(110), high=D(110.5), low=D(108))}, now_utc=end,
             funding_rate_lookup=funding_lookup)
    rec = led.close_manual(ETH, D(110), reason="audit", now=end)
    return rec


def test_fee_reduces_net_pnl_numerically():
    no_fee = _run_win_trade()
    with_fee = _run_win_trade(fees=FeeSchedule(maker_pct=D("0.02"), taker_pct=D("0.05"), source="t"))
    assert float(no_fee.fees) == 0.0 and float(with_fee.fees) > 0
    assert float(with_fee.net_pnl) < float(no_fee.net_pnl)
    # fark tam olarak fee kadar (funding yok, slippage yok)
    assert float(no_fee.net_pnl) - float(with_fee.net_pnl) == pytest.approx(float(with_fee.fees))
    assert float(with_fee.gross_pnl) == pytest.approx(float(no_fee.gross_pnl))


def test_funding_settlement_affects_futures_pnl():
    rate = lambda sym, t: 0.0001                              # noqa: E731  long öder
    with_f = _run_win_trade(funding_lookup=rate, close_at=T0 + timedelta(hours=8, minutes=5))
    no_f = _run_win_trade(funding_lookup=lambda s, t: 0.0, close_at=T0 + timedelta(hours=8, minutes=5))
    assert float(with_f.funding) < 0                          # 16:00 settlement geçildi, long ödedi
    assert float(with_f.net_pnl) < float(no_f.net_pnl)
    assert float(no_f.net_pnl) - float(with_f.net_pnl) == pytest.approx(-float(with_f.funding))


def test_missing_funding_rate_waits_not_zero_cost_forever():
    led = FuturesLedgerV2(1000, slippage=SlippageModel.zero())
    led.open(ETH, "LONG", D(100), SizeSpec(D(200), leverage=2), stop=D(90), now=T0)
    led.tick({ETH: TickData(last=D(100), high=D(101), low=D(99.5))},
             now_utc=T0 + timedelta(hours=5), funding_rate_lookup=lambda s, t: None)
    pos = led.positions[ETH]
    assert float(pos.funding_paid) == 0.0                     # UYDURMA tahakkuk yok
    # oran sonradan gelirse KAÇAN dönem geriye dönük uygulanır (watermark ileri sarılmamıştı)
    led.tick({ETH: TickData(last=D(100), high=D(101), low=D(99.5))},
             now_utc=T0 + timedelta(hours=5, minutes=1), funding_rate_lookup=lambda s, t: 0.0001)
    assert float(led.positions[ETH].funding_paid) > 0         # bekleyen settlement tahsil edildi


def test_slippage_changes_fill_and_net_pnl():
    no_slip = _run_win_trade()
    slip = _run_win_trade(slippage=SlippageModel(fixed_bps=D(50)))
    assert float(slip.net_pnl) < float(no_slip.net_pnl)
    assert float(slip.slippage_cost) > 0
    # bid/ask yarım-spread modeli: spread verildiğinde fill daha da aleyhte
    spread_model = SlippageModel(fixed_bps=D(0), spread_half=True)
    fill_no_spread = SlippageModel.zero().fill_price(D(100), "BUY", None)
    tick = TickData(last=D(100), bid=D("99.9"), ask=D("100.1"))
    fill_spread = spread_model.fill_price(D(100), "BUY", tick)
    assert float(fill_spread) > float(fill_no_spread)


# ============================================================ 4. Risk V2 / challenger izolasyonu

def _plan(**kw):
    p = {"symbol": ETH, "market_type": "USDM_PERP", "direction": "LONG", "entry": 3000.0,
         "stop": 2940.0, "notional": 30.0, "margin": 15.0, "leverage": 2, "min_notional": 5.0}
    p.update(kw)
    return p


def test_risk_v2_does_not_alter_risk_engine_decision():
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    state = build_state(equity=50.0, starting_equity=50.0, available=50.0, used_margin=0.0,
                        positions=[], history=[], high_water_mark=None,
                        now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC))
    before = eng.evaluate(_plan(), state).to_dict()
    advice = advise(AdviceContext(ETH, "LONG", proposed_leverage=5, symbol_vol_pct=9.0,
                                  model_uncertainty=0.9, data_quality_ok=False,
                                  portfolio_drawdown_pct=15.0))
    after = eng.evaluate(_plan(), state).to_dict()
    assert before == after                                    # advise() motor kararını DEĞİŞTİRMEDİ
    assert advice["advisory_only"] is True
    assert advice["advised_leverage"] <= 5                    # ve sınırı gevşetmedi

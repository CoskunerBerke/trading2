"""Quant Evaluation V1 — replay maliyet gerçekçiliği ve manifest testleri.

İki katman:
1. DAVRANIŞ: gerçek `FuturesLedgerV2.tick` üzerinde aynı-bar stop+TP çakışması → STOP kazanır
   (konservatif; geleceği bilen iyimser seçim yok), gap-through mark'tan fill, fee/slippage/funding
   gerçekten düşülür. Bunlar replay'in kullandığı AYNI kod yoludur.
2. MANİFEST: deterministik hash, veri parmak izi, maliyet modeli ve intrabar politika beyanı;
   data-quality kapısı geçmeden `valid_backtest=True` OLMAZ.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from tradingbot.accounting import (FeeSchedule, FuturesLedgerV2, SizeSpec, SlippageModel,
                                   TickData, EXIT_STOP)
from tradingbot.quant.manifest import (INTRABAR_POLICY, SCHEMA_VERSION, build_manifest,
                                       cost_model_declaration, dataset_fingerprint, write_manifest)

D = Decimal
UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
ETH = "ETH/USDT"


def _open_long(led: FuturesLedgerV2, entry=100, stop=95, targets=(110, 120)):
    pos = led.open(ETH, "LONG", D(entry), SizeSpec(D(200), leverage=2), stop=D(stop),
                   targets=[D(t) for t in targets], now=T0)
    assert pos is not None, f"pozisyon açılamadı: {led.rejections[-1] if led.rejections else '?'}"
    return pos


def test_same_bar_stop_and_tp_conservative_stop_wins():
    led = FuturesLedgerV2(1000, slippage=SlippageModel.zero())
    _open_long(led)
    # Aynı barda hem stop (low=94) hem TP (high=125) görülür → STOP seçilmeli.
    closed = led.tick({ETH: TickData(last=D(100), high=D(125), low=D(94))}, now_utc=T0)
    assert len(closed) == 1
    rec = closed[0]
    assert rec.exit_reason == EXIT_STOP
    assert rec.net_pnl < 0                                   # iyimser TP fill'i YOK


def test_gap_through_fills_at_mark_not_stop():
    led = FuturesLedgerV2(1000, slippage=SlippageModel.zero())
    _open_long(led, entry=100, stop=95)
    # Fiyat 90'a gap'ledi: fill stop(95) değil mark(90) üzerinden olmalı (iyimser fill yok).
    closed = led.tick({ETH: TickData(last=D(90), high=D(91), low=D(89))}, now_utc=T0)
    assert len(closed) == 1
    rec = closed[0]
    # qty = 200/100 = 2; stop(95) fill'i -10 verirdi, mark(90) fill'i -20 verir.
    assert float(rec.pnl) <= -19.0
    led2 = FuturesLedgerV2(1000, slippage=SlippageModel.zero())
    _open_long(led2, entry=100, stop=95)
    normal = led2.tick({ETH: TickData(last=D(95), high=D(96), low=D(94.5))}, now_utc=T0)[0]
    assert rec.net_pnl < normal.net_pnl                      # gap daha kötü sonuç verir


def test_fees_and_slippage_are_charged():
    fees = FeeSchedule(maker_pct=D("0.02"), taker_pct=D("0.05"), source="test")
    led = FuturesLedgerV2(1000, fees=fees, slippage=SlippageModel(fixed_bps=D(10)))
    _open_long(led)
    closed = led.tick({ETH: TickData(last=D(94), high=D(94.5), low=D(93.5))}, now_utc=T0)
    rec = closed[0]
    assert rec.fees > 0
    assert rec.net_pnl < rec.gross_pnl                       # net = gross - maliyetler
    led0 = FuturesLedgerV2(1000, slippage=SlippageModel.zero())
    _open_long(led0)
    rec0 = led0.tick({ETH: TickData(last=D(94), high=D(94.5), low=D(93.5))}, now_utc=T0)[0]
    assert rec.net_pnl < rec0.net_pnl                        # slippage+fee gerçekten aleyhte


def test_manifest_deterministic_and_declares_costs(tmp_path):
    f1 = tmp_path / "candles_a.parquet"
    f1.write_bytes(b"deterministic-bytes")
    dq_ok = {"passed": True, "checks": ["ts_order", "no_dup", "no_future"], "gaps": 0}
    m1 = build_manifest(run_id="r1", code_sha="abc123", config_obj={"fees": {"x": 1}},
                        dataset_paths=[f1], start_utc="2026-01-01T00:00:00+00:00",
                        end_utc="2026-02-01T00:00:00+00:00", universe_version="u7",
                        seed=7, feature_schema_version=3, result_obj={"oos_r": 0.1},
                        data_quality=dq_ok)
    m2 = build_manifest(run_id="r1", code_sha="abc123", config_obj={"fees": {"x": 1}},
                        dataset_paths=[f1], start_utc="2026-01-01T00:00:00+00:00",
                        end_utc="2026-02-01T00:00:00+00:00", universe_version="u7",
                        seed=7, feature_schema_version=3, result_obj={"oos_r": 0.1},
                        data_quality=dq_ok)
    assert m1 == m2 and m1["manifest_hash"] == m2["manifest_hash"]
    assert m1["schema_version"] == SCHEMA_VERSION
    assert m1["cost_model"]["intrabar_policy"] == INTRABAR_POLICY
    assert m1["cost_model"]["price_approximation"].startswith("bar_ohlc")
    assert m1["valid_backtest"] is True and "TEST DATA" in m1["label"]
    m3 = build_manifest(run_id="r1", code_sha="DIFFERENT", dataset_paths=[f1], data_quality=dq_ok)
    assert m3["manifest_hash"] != m1["manifest_hash"]
    p = write_manifest(tmp_path / "manifest.json", m1)
    assert p.exists()


def test_manifest_quality_gate_fail_closed(tmp_path):
    bad = build_manifest(run_id="r2", code_sha="abc",
                         data_quality={"passed": False, "checks": ["duplicate_candle"]})
    assert bad["valid_backtest"] is False
    none_given = build_manifest(run_id="r3", code_sha="abc")
    assert none_given["valid_backtest"] is False             # rapor yoksa geçerli sayılmaz
    fp = dataset_fingerprint([tmp_path / "yok.parquet"])
    assert fp["n_missing"] == 1                              # eksik dosya sessizce atlanmaz


def test_cost_model_declaration_reads_real_config_section():
    class FakeFees:
        futures_maker_pct = 0.02
        futures_taker_pct = 0.05
        spot_taker_pct = 0.1
        slippage_bps = 5.0

    class FakeCfg:
        fees = FakeFees()

    cm = cost_model_declaration(FakeCfg())
    assert cm["taker_pct"] == 0.05 and cm["slippage_bps_fixed"] == 5.0
    empty = cost_model_declaration(None)
    assert empty["taker_pct"] is None and empty["intrabar_policy"] == INTRABAR_POLICY

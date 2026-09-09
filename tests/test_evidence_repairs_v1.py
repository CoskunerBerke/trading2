"""KANIT ONARIMI V1 — 2026-09-09 ölçümünden türeyen üç onarımın sözleşme testleri.

  1) SHORT_DISABLED / NOT_SPOT_LISTED sert kapıları (kayıtlı, fail-closed, günlüğe düşer)
  2) SpotListing önbelleği (üç değerli cevap, bayat-ama-kullanılabilir, boş sonuç ezmez, atomik)
  3) Defterde MFE tabanlı başa-baş (TP1 beklemez, yalnız sıkılaştırır, bir kez, kalıcı, etiketi doğru)
  4) Config varsayılanları ESKİ davranışı korur; yaml anahtarları yüklenir
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_accounting as TA  # noqa: E402
import test_engine_v3 as TE  # noqa: E402

from tradingbot.accounting import EXIT_BE_STOP, AmountType, FuturesLedgerV2, SizeSpec, TickData  # noqa: E402
from tradingbot.coinhead.schema import TradePlanV3, Verdict, new_decision  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.decision_gates import HARD_SAFETY, GateLedger, gate_class  # noqa: E402
from tradingbot.market.spot_listing import SpotListing  # noqa: E402

D = Decimal


# ----------------------------------------------------------------------------- 1) kapı kodları
def test_01_new_gate_codes_are_registered_hard():
    assert gate_class("SHORT_DISABLED") == HARD_SAFETY
    assert gate_class("NOT_SPOT_LISTED") == HARD_SAFETY
    g = GateLedger().block("SHORT_DISABLED", detail="test")
    assert g.blocked
    assert "SHORT_DISABLED" in g.hard


# ----------------------------------------------------------------------------- 4) config
def test_02_config_defaults_preserve_old_behaviour():
    v3 = load_v3({})
    assert v3.futures_v3.allow_short is True
    assert v3.futures_v3.breakeven_at_mfe_r == 0.0
    assert v3.universe.require_spot_listing is False
    assert v3.universe.spot_listing_ttl_minutes == 1440


def test_02b_config_keys_load_from_yaml_dict():
    v3 = load_v3({"futures_v3": {"allow_short": False, "breakeven_at_mfe_r": 1.0},
                  "universe": {"require_spot_listing": True, "spot_listing_ttl_minutes": 60}})
    assert v3.futures_v3.allow_short is False
    assert v3.futures_v3.breakeven_at_mfe_r == 1.0
    assert v3.universe.require_spot_listing is True
    assert v3.universe.spot_listing_ttl_minutes == 60


# ----------------------------------------------------------------------------- 2) SpotListing
class _Prov:
    name = "fake_spot"

    def __init__(self, rows=None, raise_exc=None):
        self.rows = rows
        self.raise_exc = raise_exc

    def exchange_info(self):
        if self.raise_exc:
            raise self.raise_exc
        return self.rows


def _rows(*syms, status="TRADING"):
    return [{"symbol": s, "status": status} for s in syms]


def test_03_spot_listing_three_valued_and_persisted(tmp_path):
    p = tmp_path / "spot_listing.json"
    sl = SpotListing(p, ttl_minutes=60)
    assert sl.is_listed("ETH/USDT") is None                       # veri yok → None
    assert sl.is_stale()
    res = sl.refresh(_Prov(_rows("ETHUSDT", "BTCUSDT") + _rows("OLDUSDT", status="BREAK")), now=1_000_000.0)
    assert res["ok"] and res["n"] == 2 and res["persisted"]
    assert sl.is_listed("ETH/USDT") is True
    assert sl.is_listed("ETH/USDT:USDT") is True                   # perp gösterimi de ham ada iner
    assert sl.is_listed("NVDA/USDT") is False
    assert sl.is_listed("OLD/USDT") is False                       # TRADING değilse listeli sayılmaz
    assert not sl.is_stale(now=1_000_000.0 + 30 * 60)
    assert sl.is_stale(now=1_000_000.0 + 61 * 60)
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["schema_version"] == "spot_listing_v1" and doc["n"] == 2 and "ETHUSDT" in doc["symbols"]
    assert not p.with_suffix(".json.tmp").exists()
    sl2 = SpotListing(p, ttl_minutes=60)                          # diskten yeniden yükle
    assert sl2.available and sl2.size == 2 and sl2.is_listed("BTC/USDT") is True


def test_03b_refresh_failure_keeps_old_cache_and_empty_does_not_overwrite(tmp_path):
    sl = SpotListing(tmp_path / "s.json", ttl_minutes=60)
    assert sl.refresh(_Prov(_rows("ETHUSDT")), now=100.0)["ok"]
    r = sl.refresh(_Prov(raise_exc=RuntimeError("boom")), now=200.0)
    assert not r["ok"] and "boom" in r["error"] and sl.is_listed("ETH/USDT") is True
    r = sl.refresh(_Prov([]), now=300.0)
    assert not r["ok"] and sl.size == 1                            # boş sonuç eski listeyi EZMEZ
    r = sl.refresh(_Prov(_rows("XUSDT", status="HALT")), now=400.0)
    assert not r["ok"] and sl.is_listed("ETH/USDT") is True


# ----------------------------------------------------------------------------- 1) motor kapısı
def _decision(symbol: str, direction: str):
    d = new_decision("head", "run", "snap", symbol)
    d.verdict = Verdict.FUTURES_SHORT if direction == "SHORT" else Verdict.FUTURES_LONG
    d.direction = direction
    d.regime = "TREND_DOWN" if direction == "SHORT" else "TREND_UP"
    d.p_win = 0.4
    if direction == "SHORT":
        plan = TradePlanV3(market_type="futures", direction="SHORT", entry_type="pullback", entry_zone=(100.0, 100.0),
                           stop=102.5, targets=[95.0, 92.5], valid=True, expected_cost_pct=0.18, expected_r=1.9)
    else:
        plan = TradePlanV3(market_type="futures", direction="LONG", entry_type="pullback", entry_zone=(100.0, 100.0),
                           stop=97.5, targets=[105.0, 107.5], valid=True, expected_cost_pct=0.18, expected_r=1.9)
    d.futures_plan = plan
    assert d.is_actionable and d.active_plan is plan
    return d


def _codes(d) -> list[str]:
    return list((d.opportunity or {}).get("hard_block_codes") or [])


def test_04_short_disabled_blocks_only_when_configured(tmp_path, monkeypatch):
    eng = TE._engine(tmp_path / "a", monkeypatch, v3_overrides={"futures_v3": {"allow_short": False}})
    d = _decision("ETH/USDT", "SHORT")
    eng._assess_opportunities({"ETH/USDT": d}, briefs=[])
    assert "SHORT_DISABLED" in _codes(d)
    assert float(d.opportunity["size_multiplier"]) == 0.0           # SIZE_MULTIPLIER_ZERO → emir yok
    dl = _decision("ETH/USDT", "LONG")
    eng._assess_opportunities({"ETH/USDT": dl}, briefs=[])
    assert "SHORT_DISABLED" not in _codes(dl)                      # LONG etkilenmez
    eng2 = TE._engine(tmp_path / "b", monkeypatch)                  # varsayılan: eski davranış
    d2 = _decision("ETH/USDT", "SHORT")
    eng2._assess_opportunities({"ETH/USDT": d2}, briefs=[])
    assert "SHORT_DISABLED" not in _codes(d2)


def test_05_not_spot_listed_gate_is_fail_closed(tmp_path, monkeypatch):
    eng = TE._engine(tmp_path / "a", monkeypatch, v3_overrides={"universe": {"require_spot_listing": True}})
    # veri YOK → fail-closed
    d = _decision("ETH/USDT", "LONG")
    eng._assess_opportunities({"ETH/USDT": d}, briefs=[])
    assert "NOT_SPOT_LISTED" in _codes(d)
    # veri var: ETH listeli, NVDA değil
    eng.spot_listing.refresh(_Prov(_rows("ETHUSDT")), now=1.0)
    d_ok, d_no = _decision("ETH/USDT", "LONG"), _decision("NVDA/USDT", "LONG")
    eng._assess_opportunities({"ETH/USDT": d_ok, "NVDA/USDT": d_no}, briefs=[])
    assert "NOT_SPOT_LISTED" not in _codes(d_ok)
    assert "NOT_SPOT_LISTED" in _codes(d_no) and float(d_no.opportunity["size_multiplier"]) == 0.0
    # kapı kapalı → veri olmasa bile engel yok (eski davranış)
    eng2 = TE._engine(tmp_path / "b", monkeypatch)
    d3 = _decision("NVDA/USDT", "LONG")
    eng2._assess_opportunities({"NVDA/USDT": d3}, briefs=[])
    assert "NOT_SPOT_LISTED" not in _codes(d3)


def test_05b_ensure_spot_listing_only_hits_network_when_gate_on(tmp_path, monkeypatch):
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _Prov(_rows("ETHUSDT", "SOLUSDT"))

    eng = TE._engine(tmp_path / "off", monkeypatch)
    eng._spot_provider_factory_override = factory
    assert eng.ensure_spot_listing()["skipped"] == "gate_off" and calls["n"] == 0
    eng = TE._engine(tmp_path / "on", monkeypatch, v3_overrides={"universe": {"require_spot_listing": True}})
    eng._spot_provider_factory_override = factory
    r = eng.ensure_spot_listing()
    assert r["ok"] and r["n"] == 2 and calls["n"] == 1
    assert eng.ensure_spot_listing()["skipped"] == "fresh" and calls["n"] == 1   # TTL içinde tekrar istek yok
    assert (eng.cfg.state_path / "spot_listing.json").exists()


# ----------------------------------------------------------------------------- 3) MFE başa-baş
ETH, T0, _f = TA.ETH, TA.T0, TA._f


def _open_long(led, targets=(3300, 3500)):
    return led.open(ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900, targets=list(targets), filters=_f(), now=T0)


def test_06_breakeven_moves_at_mfe_threshold_without_tp1(tmp_path):
    led = TA._led(breakeven_at_mfe_r=D("1.0"))
    pos = _open_long(led)                                          # risk = 100 / 3000 = %3,333 = 1R
    closed = led.tick({ETH: TickData(last=3050, high=3080)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)   # 0,8R
    assert not closed and pos.stop == D("2900") and not pos.meta.get("be_by_mfe")
    closed = led.tick({ETH: TickData(last=3090, high=3101)}, now_utc=T0 + timedelta(hours=2), bar_advance=True)   # 1,01R
    assert not closed and not pos.tp1_done and pos.targets_hit == 0
    be = pos.stop
    assert D("3000") < be < D("3005")                              # gerçek başa-baş: giriş + komisyon + kayma
    meta = pos.meta["be_by_mfe"]
    assert meta["mfe_r"] >= 1.0 and D(meta["stop"]) == be
    # daha yüksek MFE stop'u yeniden HESAPLAMAZ (bir kez), asla gevşetmez
    led.tick({ETH: TickData(last=3200, high=3250)}, now_utc=T0 + timedelta(hours=3), bar_advance=True)
    assert pos.stop == be
    # BE'ye dönüş → EXIT_BE_STOP etiketi ve net ≥ 0
    closed = led.tick({ETH: TickData(last=be, low=be - 1)}, now_utc=T0 + timedelta(hours=4), bar_advance=True)
    assert len(closed) == 1 and closed[0].exit_reason == EXIT_BE_STOP
    assert closed[0].net_pnl >= 0 and not closed[0].tp1_done


def test_06b_breakeven_is_off_by_default_and_short_is_symmetric():
    led = TA._led()                                                # varsayılan 0 → eski davranış
    pos = _open_long(led)
    led.tick({ETH: TickData(last=3150, high=3200)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)   # 2R
    assert pos.stop == D("2900") and not pos.meta.get("be_by_mfe")
    led = TA._led(breakeven_at_mfe_r=D("1.0"))
    pos = led.open(ETH, "SHORT", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=3100, targets=[2700, 2500], filters=_f(), now=T0)
    led.tick({ETH: TickData(last=2910, low=2899)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)    # 1,01R
    assert D("2995") < pos.stop < D("3000") and pos.meta.get("be_by_mfe")


def test_06c_breakeven_never_loosens_and_never_crosses_mark():
    led = TA._led(breakeven_at_mfe_r=D("1.0"))
    pos = _open_long(led)
    pos.stop = D("3010")                                           # stop zaten BE'nin üstünde
    led.tick({ETH: TickData(last=3090, high=3101)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)
    assert pos.stop == D("3010") and not pos.meta.get("be_by_mfe")
    led = TA._led(breakeven_at_mfe_r=D("1.0"))
    pos = _open_long(led)
    # MFE eşiği geçildi ama mark BE'nin ALTINA düştü: stop mark'ın yanlış tarafına konmaz, tetiklenmez
    led.tick({ETH: TickData(last=2990, high=3101, low=2985)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)
    assert pos.stop == D("2900") and not pos.meta.get("be_by_mfe")


def test_06d_breakeven_knob_and_meta_round_trip(tmp_path):
    led = TA._led(breakeven_at_mfe_r=D("1.0"))
    pos = _open_long(led)
    led.tick({ETH: TickData(last=3090, high=3101)}, now_utc=T0 + timedelta(hours=1), bar_advance=True)
    be = pos.stop
    p = tmp_path / "fut.json"
    led.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert D(str(raw["breakeven_at_mfe_r"])) == D("1.0")
    l2 = FuturesLedgerV2.load(p)
    assert l2.breakeven_at_mfe_r == D("1.0")
    p2 = l2.positions[ETH]
    assert p2.stop == be and p2.meta.get("be_by_mfe")
    # yüklenen defterde tekrar tetiklenmez, stop korunur
    l2.tick({ETH: TickData(last=3150, high=3200)}, now_utc=T0 + timedelta(hours=2), bar_advance=True)
    assert p2.stop == be


def test_06e_old_ledger_without_knob_loads_with_zero(tmp_path):
    led = TA._led()
    _open_long(led)
    p = tmp_path / "old.json"
    led.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.pop("breakeven_at_mfe_r", None)
    p.write_text(json.dumps(raw), encoding="utf-8")
    l2 = FuturesLedgerV2.load(p)
    assert l2.breakeven_at_mfe_r == D("0")

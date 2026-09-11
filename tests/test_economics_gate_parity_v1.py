# -*- coding: utf-8 -*-
"""EKONOMI KAPISI — canli motor ile replay AYNI kapiyi calistirir.

Olculmus kusur (2026-09-12, `350928c` oncesi kanit): `replay/engine.py` icinde `assess`,
`opportunity`, `size_multiplier` gecen TEK BIR satir bile yoktu. Yani backtest, uretim karar
yolunun ekonomi kapisini HIC calistirmadan olcuyordu ve "uretim karar yolu" diye raporlanan
sonuc, kapisi olmayan bir aday populasyonunun sonucuydu.

Bu dosya iki seyi sabitler:
  1. Iki motor da `economics_gate.assess_one` cagirir; ceza tablosu tek kaynaktir.
  2. Kapinin senaryo davranisi: p_win None / 0.0 / normal / sinir, sert engel, boyut carpani.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.economics_gate import SOFT_PENALTY_R, assess_one      # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "tradingbot"


class _Learner:
    """Sabit hiyerarsik istatistik — kapinin kendi davranisini izole eder."""

    class _H:
        def __init__(self, v, n):
            self.v, self.n = v, n

        def estimate(self, regime=None, leaf=None):
            return self.v, self.n

    def __init__(self, p=0.5, n=0.0, exp_r=0.0):
        self.win = self._H(p, n)
        self.exp_r = self._H(exp_r, n)


def _calls(rel: str, func: str) -> int:
    src = (ROOT / rel).read_text(encoding="utf-8")
    return sum(1 for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Call)
               and (getattr(node.func, "id", None) == func or getattr(node.func, "attr", None) == func))


# ------------------------------------------------------------------ 1) tek kaynak
def test_both_engines_call_the_same_gate():
    assert _calls("engine_v3.py", "assess_one") >= 1, "canli motor ortak kapiyi cagirmiyor"
    assert _calls("replay/engine.py", "assess_one") >= 1, "replay ortak kapiyi cagirmiyor"


def test_no_engine_keeps_its_own_penalty_table():
    for rel in ("engine_v3.py", "replay/engine.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                keys = {getattr(k, "value", None) for k in node.value.keys}
                assert not (keys & set(SOFT_PENALTY_R)), (
                    "%s ikinci bir ceza tablosu tutuyor: %s" % (rel, sorted(keys & set(SOFT_PENALTY_R))[:3]))


def test_replay_defaults_to_running_the_gate():
    src = (ROOT / "replay" / "engine.py").read_text(encoding="utf-8")
    assert "economics_gate: bool = True" in src, "replay kapiyi varsayilan olarak CALISTIRMALI"
    assert "_economics_pass" in src


# ------------------------------------------------------------------ 2) senaryolar
def _base(**kw):
    args = dict(symbol="SOL/USDT", direction="LONG", setup="pullback", regime="TREND_UP",
                soft_flags=[], redteam_warnings=[], stop_pct=4.0, expected_cost_pct=0.16,
                expected_r=1.96, is_spot=False, learner=_Learner(), p_win_override=None,
                short_penalty_r=0.0, futures_only_penalty_r=0.0, spot_listed=True,
                risk_per_trade_pct=2.0)
    args.update(kw)
    return assess_one(**args)[0]


def test_probability_none_falls_back_to_the_hierarchical_estimate():
    a = _base(p_win_override=None)
    assert a.p_win_calibrated == 0.5                      # ogrenici prior'u
    assert a.net_expectancy_r > 0


def test_probability_zero_is_used_and_is_not_the_same_as_none():
    zero, none_ = _base(p_win_override=0.0), _base(p_win_override=None)
    assert zero.p_win_calibrated == 0.05                  # kelepce; DUSMEDI
    assert zero.net_expectancy_r < 0 and not zero.tradeable
    assert zero.net_expectancy_r != none_.net_expectancy_r


def test_probability_is_clamped_at_both_ends():
    assert _base(p_win_override=1.5).p_win_calibrated == 0.95
    assert _base(p_win_override=-1.0).p_win_calibrated == 0.05


def test_production_probability_level_makes_the_edge_negative():
    """Uretimde olculen medyan p_win 0.278; ayni geometride kapi NEGATIF demeli."""
    a = _base(p_win_override=0.278)
    assert a.net_expectancy_r < 0 and not a.tradeable


def test_zero_stop_distance_is_a_hard_block():
    a = _base(stop_pct=0.0)
    assert "ZERO_STOP_DISTANCE" in a.hard_block_codes and a.size_multiplier == 0.0


def test_unknown_soft_code_fails_closed():
    a, unknown = assess_one(symbol="X/USDT", direction="LONG", setup="pullback", regime=None,
                            soft_flags=["KILL_SWITCH_ACTIV"], redteam_warnings=[], stop_pct=4.0,
                            expected_cost_pct=0.16, expected_r=1.96, is_spot=False,
                            learner=_Learner(), p_win_override=0.5)
    assert unknown == ["KILL_SWITCH_ACTIV"]
    assert "UNKNOWN_GATE_CODE" in a.hard_block_codes and not a.tradeable


def test_short_penalty_pushes_a_positive_point_estimate_into_research_only():
    a = _base(direction="SHORT", short_penalty_r=0.50)
    assert a.net_expectancy_r > 0 and a.conservative_net_edge_r < 0
    assert a.research_only and not a.tradeable


def test_futures_only_penalty_is_skipped_for_a_listed_symbol():
    listed = _base(futures_only_penalty_r=0.35, spot_listed=True)
    unknown = _base(futures_only_penalty_r=0.35, spot_listed=None)
    assert listed.conservative_net_edge_r > unknown.conservative_net_edge_r
    codes = [e["code"] for e in unknown.soft_evidence]
    assert "FUTURES_ONLY_SEGMENT_PENALTY" in codes


def test_size_multiplier_scales_with_the_conservative_edge():
    weak, strong = _base(p_win_override=0.52), _base(p_win_override=0.80)
    assert 0 < weak.size_multiplier <= strong.size_multiplier <= 1.0
    assert strong.risk_pct_requested <= 2.0               # risk profili tavani asilmaz


# ------------------------------------------------------------------ 3) borsa kurallari
def test_zero_fill_price_is_rejected_not_raised():
    """Fiyat tick'e kuantize edilirken sifira duserse defter RET vermeli, CRASH etmemeli.

    Olculdu: varsayilan filtreler TUM sembollerde `price_tick=0.01` verir; DOGE 2021'de
    ~0.004 dolardi ve `raw_qty = notional / fill` `DivisionByZero` firlatiyordu — tum
    backtest turu cokuyordu.
    """
    from decimal import Decimal

    from tradingbot.accounting.futures_ledger import FuturesLedgerV2
    from tradingbot.accounting.models import SizeSpec

    # SHORT acilis emri SATIS yonunde agresif kuantize edilir -> 0.004 AŞAĞI, 0.00'a duser.
    led = FuturesLedgerV2(Decimal("100"))
    pos = led.open("DOGE/USDT", "SHORT", Decimal("0.004"), SizeSpec(Decimal("20")),
                   stop=Decimal("0.0045"), targets=[Decimal("0.003")], mark_price=Decimal("0.004"))
    assert pos is None
    assert "fill=0" in led.last_reject_reason or "PRICE" in led.last_reject_reason.upper()
    assert not led.positions


def test_replay_loads_real_exchange_filters():
    src = (ROOT / "replay" / "engine.py").read_text(encoding="utf-8")
    assert "FiltersCache" in src and "symbol_filters.json" in src
    assert "filters=self._filters_for(sym)" in src, "defter acilisi GERCEK kurallari almiyor"


def test_real_filters_differ_from_defaults_where_it_matters():
    """Varsayilanla gercek kural arasindaki fark olculebilir ve ONEMLI."""
    from pathlib import Path as _P

    from tradingbot.accounting.filters import FiltersCache, default_filters
    from tradingbot.accounting.models import MarketType

    path = _P(r"C:/Users/berke/wt-ten/data/symbol_filters.json")
    if not path.exists():
        import pytest
        pytest.skip("arsiv filtre dosyasi bu makinede yok")
    fc = FiltersCache(path)
    doge = fc.get("DOGE/USDT", MarketType.USDM_PERP)
    dflt = default_filters("DOGE/USDT", MarketType.USDM_PERP)
    assert doge.source == "binance_api"
    assert doge.price_tick < dflt.price_tick          # 0.00001 vs 0.01
    assert doge.qty_step > dflt.qty_step              # 1 vs 0.001
    btc = fc.get("BTC/USDT", MarketType.USDM_PERP)
    assert btc.min_notional > dflt.min_notional       # 50 vs 5

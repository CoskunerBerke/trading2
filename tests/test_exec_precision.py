"""Yurutme hassasiyeti regresyonlari — varsayilan tick bozulmasi ve provenans sozlesmesi."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from tradingbot.accounting.models import MarketType, SymbolFilters
from tradingbot.accounting.slippage import SlippageModel
from tradingbot.execspec import (NO_ENTRY_UNRESOLVED_PRECISION, SYNTHETIC_SPEC, UNRESOLVED,
                                 VERIFIED_VENUE, affected_universe, build_proposal, resolve_rule)

# --- URETIM KANITI (2026-09-08T13:51:19Z, cycle 9, run_01M20MK67FN7BV0GTWHP7A735S)
TRX = {"symbol": "TRX/USDT", "ref": "0.3386", "stop": "0.3341872085677379",
       "targets": ["0.34742558286452413", "0.3518383742967862"], "qty": "60", "filled": "0.34"}
NATGAS = {"symbol": "NATGAS/USDT", "ref": "2.932", "stop": "2.8599766913815676",
          "targets": ["3.0760466172368646", "3.148069925855297"], "qty": "4.194", "filled": "2.94"}


class _Cache:
    """`FiltersCache` sozlesmesinin test ikizi."""

    def __init__(self, data: dict[str, SymbolFilters]):
        self._d = data

    def has(self, s, mt=MarketType.USDM_PERP):
        return s in self._d

    def get(self, s, mt=MarketType.USDM_PERP):
        return self._d.get(s) or SymbolFilters(symbol=s, source="default")


def _verified(sym, tick, step="0.001"):
    return SymbolFilters(symbol=sym, price_tick=D(tick), qty_step=D(step),
                         source="binance:usdm:exchangeInfo", verified_at="2026-09-08T00:00:00Z")


# ---------------------------------------------------------------- eski bozulma
@pytest.mark.parametrize("case", [TRX, NATGAS], ids=["TRX", "NATGAS"])
def test_legacy_default_tick_reproduces_the_measured_distortion(case):
    """0.01 varsayilan tick, olculmus dolumu ve geometri kaybini AYNEN uretir."""
    filters = SymbolFilters(symbol=case["symbol"], price_tick=D("0.01"), qty_step=D("0.001"))
    from tradingbot.core.money import quantize_price
    fill = quantize_price(D(case["ref"]), filters.price_tick, "BUY", aggressive=True)
    assert fill == D(case["filled"])                       # uretimdeki dolumun ta kendisi
    planned_risk = D(case["ref"]) - D(case["stop"])
    filled_risk = fill - D(case["stop"])
    planned_r = (D(case["targets"][0]) - D(case["ref"])) / planned_risk
    filled_r = (D(case["targets"][0]) - fill) / filled_risk
    assert planned_r == pytest.approx(D("2"), abs=1e-9)    # plan TAM 2R
    assert filled_r < planned_r                            # dolum geometriyi BOZDU
    assert filled_risk > planned_risk


def test_trx_and_natgas_measured_degradation_values():
    """Rapor edilen sayilar: TRX 2.0000R -> 1.2775R, NATGAS 2.0000R -> 1.7001R."""
    from tradingbot.core.money import quantize_price
    got = {}
    for case in (TRX, NATGAS):
        fill = quantize_price(D(case["ref"]), D("0.01"), "BUY", aggressive=True)
        got[case["symbol"]] = float((D(case["targets"][0]) - fill) / (fill - D(case["stop"])))
    assert got["TRX/USDT"] == pytest.approx(1.27746, abs=1e-4)
    assert got["NATGAS/USDT"] == pytest.approx(1.70009, abs=1e-4)


# ---------------------------------------------------------------- kural cozumu
def test_missing_metadata_never_becomes_a_default_tick():
    f, prov = resolve_rule("NATGAS/USDT", cache=_Cache({}))
    assert f is None and prov.rule_class == UNRESOLVED
    assert prov.reason == "NO_VENUE_METADATA"
    assert not prov.usable


def test_default_sourced_filters_are_not_accepted_as_verified():
    """`source='default'` DOGRULANMIS sayilmaz — sessiz 0.01 buradan sizardi."""
    cache = _Cache({"TRX/USDT": SymbolFilters(symbol="TRX/USDT", source="default")})
    f, prov = resolve_rule("TRX/USDT", cache=cache)
    assert f is None and prov.rule_class == UNRESOLVED
    assert prov.reason == "UNVERIFIED_DEFAULT_FILTERS"


def test_verified_venue_metadata_is_used_with_provenance():
    cache = _Cache({"BTC/USDT": _verified("BTC/USDT", "0.10")})
    f, prov = resolve_rule("BTC/USDT", cache=cache)
    assert prov.rule_class == VERIFIED_VENUE and prov.usable
    assert f.price_tick == D("0.10")
    assert prov.source == "binance:usdm:exchangeInfo" and prov.verified_at


def test_synthetic_spec_requires_version_and_effective_date():
    incomplete = {"NATGAS/USDT": {"price_tick": "0.001", "qty_step": "0.001"}}
    f, prov = resolve_rule("NATGAS/USDT", cache=_Cache({}), synthetic_specs=incomplete)
    assert f is None and prov.rule_class == UNRESOLVED
    assert prov.reason.startswith("SYNTHETIC_SPEC_INCOMPLETE")

    full = {"NATGAS/USDT": {"spec_version": "natgas_paper_v1",
                            "effective_from": "2026-09-08T00:00:00Z",
                            "price_tick": "0.001", "qty_step": "0.001",
                            "contract": "SYNTHETIC_PAPER", "multiplier": "1"}}
    f2, prov2 = resolve_rule("NATGAS/USDT", cache=_Cache({}), synthetic_specs=full)
    assert prov2.rule_class == SYNTHETIC_SPEC and prov2.usable
    assert prov2.contract == "SYNTHETIC_PAPER" and f2.price_tick == D("0.001")


def test_shipped_synthetic_specs_are_empty_by_design():
    """Modul kendiliginden sentetik kural URETMEZ; operator yazar."""
    from tradingbot.execspec import SYNTHETIC_SPECS
    assert SYNTHETIC_SPECS == {}


def test_affected_universe_lists_blocked_symbols_with_reasons():
    cache = _Cache({"BTC/USDT": _verified("BTC/USDT", "0.10"),
                    "SOL/USDT": SymbolFilters(symbol="SOL/USDT", source="default")})
    rep = affected_universe(["BTC/USDT", "SOL/USDT", "NATGAS/USDT"], cache=cache)
    by = {r["symbol"]: r for r in rep["rows"]}
    assert rep["n_entry_blocked"] == 2
    assert by["BTC/USDT"]["no_entry_reason"] is None
    assert by["SOL/USDT"]["no_entry_reason"] == NO_ENTRY_UNRESOLVED_PRECISION
    assert by["NATGAS/USDT"]["detail"] == "NO_VENUE_METADATA"


# ---------------------------------------------------------------- teklif izi
def _prop(sym, ref, tick, qty, stop, targets, side="LONG", slip=None):
    cache = _Cache({sym: _verified(sym, tick, "0.001")})
    f, prov = resolve_rule(sym, cache=cache)
    return build_proposal(symbol=sym, side=side, ref_price=ref, qty=qty, stop=stop,
                          targets=targets, filters=f, provenance=prov, slippage=slip,
                          planned_entry=ref)


def test_verified_tick_preserves_planned_geometry():
    """TRX gercek 4 haneli bir tick ile: 2R plani KORUNUR."""
    p = _prop("TRX/USDT", TRX["ref"], "0.0001", TRX["qty"], TRX["stop"], TRX["targets"])
    assert p.blocked_reason == ""
    assert p.executable_price == D("0.3386")               # tam tick -> kayma YOK
    g = p.geometry()
    assert float(g["executable_target_r"][0]) == pytest.approx(2.0, abs=1e-6)
    assert float(g["unit_risk_growth"]) == pytest.approx(0.0, abs=1e-9)


def test_exact_tick_multiple_is_stable():
    p = _prop("X/USDT", "100.00", "0.01", "1", "99.00", ["102.00"])
    assert p.executable_price == D("100.00")               # zaten tick kati -> DEGISMEZ


def test_short_rounds_the_other_way():
    """SHORT girisi ALEYHTE asagi yuvarlanir (LONG yukari)."""
    p = _prop("X/USDT", "100.006", "0.01", "1", "101.00", ["98.00"], side="SHORT")
    assert p.executable_price == D("100.00")
    p2 = _prop("X/USDT", "100.006", "0.01", "1", "99.00", ["102.00"], side="LONG")
    assert p2.executable_price == D("100.01")


def test_rounding_is_applied_once_not_twice_with_slippage():
    """Kayma + tick TEK aleyhte adimdir; stop/hedefler ikinci kez kaydirilmaz."""
    slip = SlippageModel(fixed_bps=D("10"))                # %0.10
    p = _prop("X/USDT", "100.00", "0.01", "1", "99.00", ["102.00"], slip=slip)
    assert p.slipped_price == D("100.10")
    assert p.executable_price == D("100.10")               # zaten tick kati; EK yuvarlama yok
    assert p.stop == D("99.00") and p.targets == [D("102.00")]


def test_quantity_and_risk_are_rechecked_on_the_final_proposal():
    p = _prop("X/USDT", "100.00", "0.01", "1.23456", "99.00", ["102.00"])
    assert p.qty == D("1.234")                             # step'e asagi yuvarlandi
    g = p.geometry()
    assert g["risk_usdt"] == D("1.00") * D("1.234")
    assert g["valid_geometry"] is True


def test_impossible_geometry_after_rounding_is_invalidated():
    """Yuvarlama stop'un yanlis tarafina gecerse teklif GECERSIZ olur (hedef tasinmaz)."""
    p = _prop("X/USDT", "99.995", "0.01", "1", "100.00", ["102.00"])
    assert p.executable_price == D("100.00")
    assert p.blocked_reason == "INVALID_GEOMETRY_AFTER_ROUNDING"
    assert p.geometry()["valid_geometry"] is False


def test_unresolved_rule_cannot_fabricate_a_fill():
    f, prov = resolve_rule("NATGAS/USDT", cache=_Cache({}))
    p = build_proposal(symbol="NATGAS/USDT", side="LONG", ref_price="2.932", qty="4.194",
                       stop="2.8599766913815676", targets=["3.0760466172368646"],
                       filters=f, provenance=prov)
    assert p.blocked_reason == NO_ENTRY_UNRESOLVED_PRECISION
    assert p.executable_price == D("2.932")                # yuvarlanmadi; dolum URETILMEDI
    assert p.provenance.rule_class == UNRESOLVED


def test_proposal_records_planned_versus_executable():
    p = _prop("TRX/USDT", TRX["ref"], "0.01", TRX["qty"], TRX["stop"], TRX["targets"])
    d = p.to_dict()
    assert d["contract_version"] == "exec_precision_v1"
    assert d["planned_entry"] == "0.3386" and d["executable_price"] == "0.34"
    g = p.geometry()
    assert float(g["planned_target_r"][0]) == pytest.approx(2.0, abs=1e-9)
    assert float(g["executable_target_r"][0]) == pytest.approx(1.27746, abs=1e-4)
    assert float(g["unit_risk_growth"]) == pytest.approx(0.3172, abs=1e-3)


def test_second_same_cycle_entry_sees_the_first_actual_fill():
    """Ikinci aday, birincinin GERCEK dolum riskini gorur (tahmini plan riskini degil)."""
    p1 = _prop("TRX/USDT", TRX["ref"], "0.01", TRX["qty"], TRX["stop"], TRX["targets"])
    used = p1.geometry()["risk_usdt"]
    assert float(used) == pytest.approx(0.348767, abs=1e-5)    # uretimdeki portfoy deltasi
    budget, already = D("6.0"), D("5.312461930892333")
    remaining = budget - already - used
    assert remaining > 0
    p2 = _prop("NATGAS/USDT", NATGAS["ref"], "0.01", NATGAS["qty"], NATGAS["stop"],
               NATGAS["targets"])
    assert float(p2.geometry()["risk_usdt"]) == pytest.approx(0.335618, abs=1e-5)
    assert p2.geometry()["risk_usdt"] <= remaining             # butceye SIGAR


def test_open_position_exit_path_is_untouched_by_rule_resolution():
    """Kural cozumu YALNIZ yeni giris icindir; acik pozisyonun stop'u kendi meta'sindan gelir."""
    import inspect

    from tradingbot.accounting import futures_ledger
    src = inspect.getsource(futures_ledger.FuturesLedgerV2.tick)
    assert "execspec" not in src and "resolve_rule" not in src
    # cikis tetigi pozisyonun KENDI kayitli filtresini kullanir (ledger:303)
    assert 'pos.meta.get("filters"' in inspect.getsource(futures_ledger.FuturesLedgerV2)

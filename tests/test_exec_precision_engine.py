"""MOTOR orkestrasyonu — kural çözümü → yürütülebilir önizleme → risk kabulü → defter dolumu.

Testler `TradingEngineV3`'ün GERÇEK metotlarını (`ensure_symbol_filters`, `_resolve_entry_filters`,
`_execution_entry`, `_execute_futures_entry`) gerçek `FuturesLedgerV2`, gerçek `FiltersCache`,
gerçek `RiskEngine(PAPER_RESEARCH)` ve gerçek `execspec` ile çalıştırır. Fikstür kuralları
2026-09-08T15:52Z'de çekilen RESMİ `/fapi/v1/exchangeInfo` kayıtlarıdır (pricePrecision DEĞİL,
PRICE_FILTER.tickSize). Ayrıca `tour()` içindeki çağrı zincirinin aynı `f_sym` nesnesini
önizleme ve dolumda kullandığı AST ile kanıtlanır.
"""
from __future__ import annotations

import ast
import json
import pathlib
from decimal import Decimal as D

import pytest

from tradingbot.accounting import FiltersCache, FuturesLedgerV2, MarketType, SlippageModel, TickData
from tradingbot.accounting.filters import refresh_futures_filters
from tradingbot.accounting.futures_ledger import R_UNVERIFIED_PRECISION
from tradingbot.accounting.models import SymbolFilters
from tradingbot.risk.engine import RiskEngine
from tradingbot.risk.profiles import PROFILES
from tradingbot.risk.state import OpenPosition, PortfolioState

ENGINE_SRC = pathlib.Path(__file__).resolve().parents[1] / "tradingbot" / "engine_v3.py"

# --- RESMİ exchangeInfo (fapi, serverTime 1788865758198) — ilgili alanlar birebir
TRXUSDT = {"symbol": "TRXUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "TRX",
           "quoteAsset": "USDT", "marginAsset": "USDT", "pricePrecision": 5, "quantityPrecision": 0,
           "underlyingType": "COIN", "onboardDate": 1579075500000,
           "filters": [{"filterType": "PRICE_FILTER", "minPrice": "0.00132", "maxPrice": "20000", "tickSize": "0.00001"},
                       {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "20000000", "stepSize": "1"},
                       {"filterType": "MARKET_LOT_SIZE", "minQty": "1", "maxQty": "5000000", "stepSize": "1"},
                       {"filterType": "MIN_NOTIONAL", "notional": "5"}]}
NATGASUSDT = {"symbol": "NATGASUSDT", "contractType": "TRADIFI_PERPETUAL", "status": "TRADING",
              "baseAsset": "NATGAS", "quoteAsset": "USDT", "marginAsset": "USDT", "pricePrecision": 5,
              "quantityPrecision": 1, "underlyingType": "COMMODITY", "onboardDate": 1775035200000,
              "filters": [{"filterType": "PRICE_FILTER", "minPrice": "0.00100", "maxPrice": "2000", "tickSize": "0.00100"},
                          {"filterType": "LOT_SIZE", "minQty": "0.1", "maxQty": "3000000", "stepSize": "0.1"},
                          {"filterType": "MARKET_LOT_SIZE", "minQty": "0.1", "maxQty": "300000", "stepSize": "0.1"},
                          {"filterType": "MIN_NOTIONAL", "notional": "5"}]}
QUARTER = {"symbol": "BTCUSDT_260925", "contractType": "CURRENT_QUARTER", "status": "TRADING", "baseAsset": "BTC",
           "quoteAsset": "USDT", "filters": TRXUSDT["filters"]}
BROKEN = {"symbol": "XXXUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "XXX",
          "quoteAsset": "USDT", "filters": [{"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"}]}   # PRICE_FILTER YOK

# --- üretim referansları (2026-09-08T13:51:19Z, cycle 9)
TRX = dict(ref="0.3386", stop="0.3341872085677379", tp=["0.34742558286452413", "0.3518383742967862"], notional="20.4", lev=3)
NATGAS = dict(ref="2.932", stop="2.8599766913815676", tp=["3.0760466172368646", "3.148069925855297"], notional="12.33", lev=3)


class _Provider:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, 0

    def exchange_info(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fapi 5xx / ağ yok")
        return list(self.rows)


class _Exec:
    def __init__(self, gate: bool, max_age_h: float = 24.0):
        self.require_verified_precision = gate
        self.filters_max_age_hours = max_age_h
        self.filters_refresh_on_start = True


def _engine(tmp_path, *, gate: bool, provider=None, max_age_h: float = 24.0):
    """Kısmi TradingEngineV3: GERÇEK defter/filtre/risk; ağ yok."""
    from tradingbot.engine_v3 import TradingEngineV3

    e = object.__new__(TradingEngineV3)
    e.filters = FiltersCache(tmp_path / "symbol_filters.json")
    e.ledger2 = FuturesLedgerV2(D("100"), slippage=SlippageModel(fixed_bps=D("3.0")),
                                require_verified_precision=gate)
    e.ledger_path = tmp_path / "ledger.json"
    e.risk = RiskEngine(PROFILES["PAPER_RESEARCH"])
    e.profile = PROFILES["PAPER_RESEARCH"]
    e._gap_provider_factory = (lambda: provider) if provider is not None else None
    e._filters_refresh_result = None

    class _Data:
        rate_budget_safety = 0.7

    class _V3:
        execution = _Exec(gate, max_age_h)
        data = _Data()

    class _Cfg:
        v3 = _V3()
        state_path = tmp_path

    e.cfg = _Cfg()
    return e


def _state(e) -> PortfolioState:
    """Gerçek defterden PortfolioState (ikinci aday BİRİNCİNİN gerçek dolumunu görür)."""
    ops = []
    for sym, p in e.ledger2.positions.items():
        notional = float(p.qty * p.entry_avg)
        risk = abs(float(p.entry_avg) - float(p.stop)) * float(p.qty) if p.stop else 0.0
        ops.append(OpenPosition(symbol=sym, market_type="USDM_PERP", side=p.side.value, notional=notional,
                                margin=float(p.isolated_margin), risk_usdt=risk, entry=float(p.entry_avg),
                                stop=float(p.stop) if p.stop else None, leverage=p.leverage))
    return PortfolioState(equity=float(e.ledger2.wallet_balance), starting_equity=100.0, high_water_mark=100.0,
                          available=float(e.ledger2.wallet_balance), open_positions=ops)


def _run_chain(e, sym, side, case, *, tick_px=None):
    """ÜRETİM ZİNCİRİ (tour() ile aynı sıra): çöz → önizle → risk → dolum."""
    f_sym, prov = e._resolve_entry_filters(sym, "USDM_PERP")
    if f_sym is None:
        return None, prov, None, None
    tick = TickData(last=D(tick_px or case["ref"]))
    exec_entry = e._execution_entry(sym, "USDM_PERP", side, float(case["ref"]), tick, filters=f_sym)
    plan = {"symbol": sym, "market_type": "USDM_PERP", "direction": side, "entry": exec_entry,
            "stop": float(case["stop"]), "targets": [float(t) for t in case["tp"]],
            "notional": float(case["notional"]), "margin": float(case["notional"]) / case["lev"],
            "leverage": case["lev"], "amount_type": "NOTIONAL", "expected_r": 2.0,
            "min_notional": float(f_sym.min_notional)}
    rd = e.risk.evaluate(plan, _state(e), {"now_utc": None})
    if not rd.allowed:
        return None, prov, exec_entry, rd
    pos = e._execute_futures_entry(symbol=sym, direction=side, ref_price=D(case["ref"]), notional=case["notional"],
                                   leverage=int(rd.adjusted_leverage or case["lev"]), stop=D(case["stop"]),
                                   targets=[D(t) for t in case["tp"]], filters=f_sym, provenance=prov, tick=tick)
    return pos, prov, exec_entry, rd


# ------------------------------------------------------------------ yapısal kanıt (tour zinciri)
def test_tour_uses_one_resolved_filter_object_from_preview_to_fill():
    tree = ast.parse(ENGINE_SRC.read_text(encoding="utf-8"))
    # Giriş bloğunu içeren ÜRETİM fonksiyonu: `_resolve_entry_filters` çağrısının sahibi (tour ya da
    # onun çağırdığı yardımcı) — ad sabitlenmez, çağrı zinciri aranır.
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]

    def _owner(call):
        enc = [f for f in funcs if f.lineno <= call.lineno <= (f.end_lineno or f.lineno)
               and f.name not in ("_resolve_entry_filters", "_execution_entry", "_execute_futures_entry")]
        return min(enc, key=lambda f: (f.end_lineno or 0) - f.lineno)

    owners = {}
    for c in ast.walk(tree):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr in (
                "_resolve_entry_filters", "_execution_entry", "_execute_futures_entry"):
            o = _owner(c)
            if isinstance(o.body, list) and any(True for _ in [0]):
                owners.setdefault(o.name, {}).setdefault(c.func.attr, []).append(c)
    entry_fn = next(n for n, m in owners.items()
                    if {"_resolve_entry_filters", "_execution_entry", "_execute_futures_entry"} <= set(m))
    calls = owners[entry_fn]
    # zincir tek bir üretim fonksiyonunda ve tour() ondan (en fazla 3 adımda) ulaşılabilir:
    # tour → _execute → _execute_locked (giriş bloğunun sahibi)
    by_name = {f.name: f for f in funcs}

    def _callees(fn):
        return {c.func.attr for c in ast.walk(fn)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}

    reach, frontier = {"tour"}, {"tour"}
    for _ in range(3):
        nxt = set()
        for name in frontier:
            if name in by_name:
                nxt |= _callees(by_name[name])
        frontier = nxt - reach
        reach |= nxt
    assert entry_fn in reach, (entry_fn, sorted(reach)[:8])
    fk = {k.arg: k.value for k in calls["_execution_entry"][0].keywords}
    ok = {k.arg: k.value for k in calls["_execute_futures_entry"][0].keywords}
    assert isinstance(fk["filters"], ast.Name) and isinstance(ok["filters"], ast.Name)
    assert fk["filters"].id == ok["filters"].id == "f_sym"           # AYNI nesne, iki yerde
    src = ENGINE_SRC.read_text(encoding="utf-8")
    assert "filters=self.filters.get(sym, MarketType.USDM_PERP)" not in src   # eski ikinci .get() yolu kalktı


# ------------------------------------------------------------------ sağlayıcı → önbellek
def test_refresh_parses_official_contracts_strictly_and_skips_others(tmp_path):
    cache = FiltersCache(tmp_path / "f.json")
    res = refresh_futures_filters(cache, _Provider([TRXUSDT, NATGASUSDT, QUARTER, BROKEN]))
    assert res["ok"] and res["n_ok"] == 2
    assert res["skipped"]["BTC/USDT"] == "CONTRACT_CURRENT_QUARTER"     # vadeli kabul EDİLMEZ
    assert "XXX/USDT" in res["errors"]                                   # PRICE_FILTER yok → varsayılan YOK
    trx = cache.get("TRX/USDT", MarketType.USDM_PERP)
    assert trx.price_tick == D("0.00001") and trx.qty_step == D("1") and trx.contract_type == "PERPETUAL"
    ng = cache.get("NATGAS/USDT", MarketType.USDM_PERP)
    assert ng.price_tick == D("0.00100") and ng.contract_type == "TRADIFI_PERPETUAL"
    assert (tmp_path / "f.json").exists() and cache.verified_at
    again = FiltersCache(tmp_path / "f.json")                           # diskten geri okunur
    assert again.get("TRX/USDT").source == "binance_api" and again.get("TRX/USDT").contract_type == "PERPETUAL"


def test_provider_failure_keeps_old_cache_and_is_explicit(tmp_path):
    cache = FiltersCache(tmp_path / "f.json")
    refresh_futures_filters(cache, _Provider([TRXUSDT]))
    before = cache.verified_at
    res = refresh_futures_filters(cache, _Provider(fail=True))
    assert res["ok"] is False and "RuntimeError" in res["error"]
    assert cache.verified_at == before and cache.get("TRX/USDT").price_tick == D("0.00001")


def test_engine_refresh_only_when_gate_on_and_stale(tmp_path):
    prov = _Provider([TRXUSDT])
    off = _engine(tmp_path, gate=False, provider=prov)
    assert off.ensure_symbol_filters() == {"ok": True, "skipped": "gate_off"} and prov.calls == 0
    on = _engine(tmp_path, gate=True, provider=prov)
    r1 = on.ensure_symbol_filters()
    assert r1["ok"] and r1["n_ok"] == 1 and prov.calls == 1
    r2 = on.ensure_symbol_filters()                                        # taze → istek yok
    assert r2.get("skipped") == "fresh" and prov.calls == 1


# ------------------------------------------------------------------ uçtan uca zincir
def test_verified_contract_fills_at_official_tick_and_keeps_planned_geometry(tmp_path):
    e = _engine(tmp_path, gate=True, provider=_Provider([TRXUSDT, NATGASUSDT]))
    e.ensure_symbol_filters()
    pos, prov, exec_entry, rd = _run_chain(e, "TRX/USDT", "LONG", TRX)
    assert prov.rule_class == "VERIFIED_VENUE" and prov.contract == "PERPETUAL"
    assert pos is not None and rd.allowed
    # 3 bps kayma + 0.00001 tick: 0.3386 → 0.33870158 → 0.33871 (tek aleyhte yuvarlama)
    assert pos.entry_avg == D("0.33871")
    assert float(exec_entry) == float(pos.entry_avg)                       # önizleme == dolum
    r_tp1 = (D(TRX["tp"][0]) - pos.entry_avg) / (pos.entry_avg - D(TRX["stop"]))
    assert 1.92 < float(r_tp1) < 2.0                                       # eski varsayılanla 1.2775 idi
    assert pos.qty == D("60")                                              # LOT step 1 (20.4/0.33871=60.2→60)
    assert pos.meta["precision"]["rule_class"] == "VERIFIED_VENUE"
    assert pos.meta["filters"]["price_tick"] == "0.00001"                  # kural pozisyonla kalıcı


def test_default_tick_distortion_reproduced_without_the_fix(tmp_path):
    """KUSUR: kapı KAPALI + önbellek YOK → varsayılan 0.01 ile aynen üretimdeki 0.34 dolumu."""
    e = _engine(tmp_path, gate=False)
    pos, prov, exec_entry, rd = _run_chain(e, "TRX/USDT", "LONG", TRX)
    assert prov.rule_class == "UNRESOLVED" and pos is not None            # eski davranış korunur
    assert pos.entry_avg == D("0.34")                                      # ölçülen bozulma
    assert pos.meta["filters"]["source"] == "default"


def test_unresolved_rule_cannot_create_a_fill_when_gate_on(tmp_path):
    e = _engine(tmp_path, gate=True, provider=_Provider([TRXUSDT]))
    e.ensure_symbol_filters()
    pos, prov, exec_entry, rd = _run_chain(e, "NATGAS/USDT", "LONG", NATGAS)   # NATGAS önbellekte YOK
    assert pos is None and prov.rule_class == "UNRESOLVED" and prov.reason == "NO_VENUE_METADATA"
    assert exec_entry is None and not e.ledger2.positions                 # önizleme bile yapılmadı


def test_spot_rule_never_serves_a_derivative_request(tmp_path):
    e = _engine(tmp_path, gate=True)
    from tradingbot.accounting.filters import from_binance_spot
    spot = from_binance_spot(dict(TRXUSDT, filters=[{"filterType": "PRICE_FILTER", "tickSize": "0.0001"},
                                                    {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1"}]))
    spot.symbol = "TRX/USDT"
    e.filters.put(spot)                                                     # yalnız SPOT kovası dolu
    f, prov = e._resolve_entry_filters("TRX/USDT", "USDM_PERP")
    assert f is None and prov.rule_class == "UNRESOLVED"                   # USD-M isteğine spot kuralı verilmedi


def test_expired_metadata_is_explicit_not_silent(tmp_path):
    e = _engine(tmp_path, gate=True, provider=_Provider([TRXUSDT]), max_age_h=0.0)
    refresh_futures_filters(e.filters, _Provider([TRXUSDT]))
    f, prov = e._resolve_entry_filters("TRX/USDT", "USDM_PERP")
    assert f is None and prov.reason.startswith("METADATA_EXPIRED")


def test_ledger_rejects_unverified_when_gate_on_even_if_called_directly(tmp_path):
    e = _engine(tmp_path, gate=True)
    pos = e._execute_futures_entry(symbol="TRX/USDT", direction="LONG", ref_price=D(TRX["ref"]), notional=TRX["notional"],
                                   leverage=3, stop=D(TRX["stop"]), targets=[D(t) for t in TRX["tp"]],
                                   filters=SymbolFilters(symbol="TRX/USDT", source="default"), provenance=None,
                                   tick=TickData(last=D(TRX["ref"])))
    assert pos is None and e.ledger2.last_reject_reason == R_UNVERIFIED_PRECISION


def test_short_rounds_down_and_exact_tick_is_stable(tmp_path):
    e = _engine(tmp_path, gate=True, provider=_Provider([NATGASUSDT]))
    e.ensure_symbol_filters()
    e.ledger2.slippage = SlippageModel.zero()
    f, prov = e._resolve_entry_filters("NATGAS/USDT", "USDM_PERP")
    assert e._execution_entry("NATGAS/USDT", "USDM_PERP", "SHORT", 2.9326, None, filters=f) == 2.932   # aşağı
    assert e._execution_entry("NATGAS/USDT", "USDM_PERP", "LONG", 2.9326, None, filters=f) == 2.933    # yukarı
    assert e._execution_entry("NATGAS/USDT", "USDM_PERP", "LONG", 2.932, None, filters=f) == 2.932     # tick katı → sabit


def test_second_candidate_sees_first_actual_fill(tmp_path):
    e = _engine(tmp_path, gate=True, provider=_Provider([TRXUSDT, NATGASUSDT]))
    e.ensure_symbol_filters()
    assert _state(e).futures_stop_risk_usdt == 0.0
    pos1, *_ = _run_chain(e, "TRX/USDT", "LONG", TRX)
    st = _state(e)
    used = abs(float(pos1.entry_avg) - float(pos1.stop)) * float(pos1.qty)
    assert st.futures_stop_risk_usdt == pytest.approx(used) and len(st.open_positions) == 1
    pos2, prov2, exec2, rd2 = _run_chain(e, "NATGAS/USDT", "LONG", NATGAS)
    assert pos2 is not None and _state(e).futures_stop_risk_usdt > used     # ikinci, birinciyi GÖRDÜ
    assert rd2.allowed and len(e.ledger2.positions) == 2


def test_rule_identity_is_frozen_from_validation_to_fill(tmp_path):
    """Doğrulama ile dolum arasında arka planda yenileme olsa bile AYNI nesne kullanılır."""
    e = _engine(tmp_path, gate=True, provider=_Provider([TRXUSDT]))
    e.ensure_symbol_filters()
    f_sym, prov = e._resolve_entry_filters("TRX/USDT", "USDM_PERP")
    exec_entry = e._execution_entry("TRX/USDT", "USDM_PERP", "LONG", float(TRX["ref"]), None, filters=f_sym)
    changed = dict(TRXUSDT, filters=[{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                                     {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"}])
    refresh_futures_filters(e.filters, _Provider([changed]))               # arka plan yenileme (kaba tick)
    assert e.filters.get("TRX/USDT").price_tick == D("0.01")              # önbellek DEĞİŞTİ
    pos = e._execute_futures_entry(symbol="TRX/USDT", direction="LONG", ref_price=D(TRX["ref"]), notional=TRX["notional"],
                                   leverage=3, stop=D(TRX["stop"]), targets=[D(t) for t in TRX["tp"]],
                                   filters=f_sym, provenance=prov)
    assert float(pos.entry_avg) == pytest.approx(exec_entry)               # dolum, doğrulanan nesneyle
    assert pos.meta["filters"]["price_tick"] == "0.00001"


def test_existing_position_exits_survive_gate_and_refresh(tmp_path):
    e = _engine(tmp_path, gate=False)
    pos, *_ = _run_chain(e, "TRX/USDT", "LONG", TRX)                        # eski kuralla açılmış pozisyon
    assert pos is not None
    e.ledger2.require_verified_precision = True
    refresh_futures_filters(e.filters, _Provider([TRXUSDT]))               # kurallar SONRADAN geldi
    assert pos.meta["filters"]["price_tick"] == "0.01"                     # eski pozisyon YENİDEN YAZILMADI
    closed = e.ledger2.tick({"TRX/USDT": TickData(last=D("0.33"), mark=D("0.33"))})
    assert [c.exit_reason for c in closed] == ["stop"]                      # koruyucu çıkış çalıştı


# ------------------------------------------------------------------ config → defter (runtime)
def test_config_key_reaches_the_ledger_through_the_real_loader(tmp_path):
    """`execution.require_verified_precision` GERÇEK yükleyiciden geçip defter bayrağına ulaşır."""
    from tradingbot.config_v3 import load_v3

    base = {"mode": {"mode": "PAPER"}}
    off = load_v3(dict(base, execution={"gateway": "paper"}))
    on = load_v3(dict(base, execution={"gateway": "paper", "require_verified_precision": True,
                                       "filters_max_age_hours": 6}))
    assert off.execution.require_verified_precision is False        # varsayılan KAPALI
    assert on.execution.require_verified_precision is True and on.execution.filters_max_age_hours == 6
    assert on.execution.gateway == "paper"                          # ilgisiz alanlar korunur
    assert not [w for w in on.warnings if "execution" in w]         # anahtar TANINDI (yok sayılmadı)

    # motorun kurduğu ifade: defter bayrağı config'ten gelir ve KAPI GERÇEKTEN uygular
    led = FuturesLedgerV2(D("100"),
                          require_verified_precision=bool(on.execution.require_verified_precision))
    assert led.require_verified_precision is True
    assert led.open("TRX/USDT", "LONG", D("0.3386"), __import__("tradingbot.accounting", fromlist=["SizeSpec"]).SizeSpec(D("20.4"), __import__("tradingbot.accounting", fromlist=["AmountType"]).AmountType.NOTIONAL, 3),
                    stop=D("0.3341872085677379")) is None
    assert led.last_reject_reason == R_UNVERIFIED_PRECISION


def test_engine_reads_the_flag_from_config_not_a_hardcoded_default(tmp_path):
    """Kısmi motor: `_resolve_entry_filters` kapı davranışı config'ten okunur (runtime)."""
    e_off = _engine(tmp_path, gate=False)
    f_off, p_off = e_off._resolve_entry_filters("TRX/USDT", "USDM_PERP")
    assert f_off is not None and f_off.source == "default"           # kapalı → eski davranış
    e_on = _engine(tmp_path, gate=True)
    f_on, p_on = e_on._resolve_entry_filters("TRX/USDT", "USDM_PERP")
    assert f_on is None and p_on.rule_class == "UNRESOLVED"          # açık → varsayılana DÜŞMEZ

# -*- coding: utf-8 -*-
"""ÖĞRENME MODU — FORMASYON DEFTERİ (2026-09-28, öğrenme modu; SPEC D1/D2/D3/D5/D6/D7/D16/D17/D18, S9, §4).

Gerçek `PatternBook`/`PatternScanner`/`FuturesLedgerV2`/`RiskEngine` zinciri; ağ yerine `MockProvider`. Anahtar KAPALIYKEN
(bölüm yok ya da `enabled: false`) defter dosyaları bit-aynıdır; açıkken sınırlar kalkar ve açılmayan sinyal karşı-olgusal
kayda düşer. Birim testleri `_try_open`i elle kurulmuş v3 planıyla doğrudan çağırır (tick/likidite/derinlik kontrollü).
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_pattern_trader_v1 as P  # noqa: E402
from test_pattern_trader_momentum_v3 import _series_4h  # noqa: E402
from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.learning_mode import LearningMode  # noqa: E402
from tradingbot.market.providers import MockProvider  # noqa: E402
from tradingbot.pattern_trader.learning import LEARNING_MIN_DEPTH_USDT, cf_horizon, min_depth_for  # noqa: E402
from tradingbot.pattern_trader.strategy import PL_CANCELLED, PL_EXPIRED, PL_MANAGED, PL_REJECTED, PL_TRIGGERED  # noqa: E402
from tradingbot.pattern_trader.strategy_v3 import FAMILY_V3, PROTOCOL_V3, V3_MAX_HOLD_15M  # noqa: E402
from tradingbot.pattern_trader.universe import discover  # noqa: E402

H1, H4, M15, DAY = P.H1, P.H4, P.M15, P.DAY
_reset_clock = P._reset_clock      # autouse saat sıfırlama
LAB5 = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")
LEARN = {"enabled": True, "books": {"pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3}}}
LEARN_OFF = {"enabled": False, "books": {"pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3}}}


# ---------------------------------------------------------------- kurulum
def _cfg(tmp_path: Path, *, learning: dict | None = None, **over):
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    raw = {"pattern_trader": {"enabled": True, "protocol": "momentum_4h_v3", **over}}
    if learning is not None:
        raw["learning_mode"] = json.loads(json.dumps(learning))
    cfg.v3 = load_v3(raw)
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    return cfg


def _bl(cfg, **repl):
    """Motorun vereceği görünüm: `LearningMode.book("pattern_trader")` (kapı açık)."""
    lm = LearningMode.from_config(cfg, mode_gate=lambda: (True, "OK"))
    lm.refresh()
    bl = lm.book("pattern_trader")
    assert bl is not None and bl.on
    return dataclasses.replace(bl, **repl) if repl else bl


def _multi(tmp_path, symbols=LAB5, *, learning=None, extend_15m: int = 0, **over):
    """Her sembolde aynı taze 4h üç beyaz asker + RSI>70 (v3 sinyali) — tek taramada hepsi tetiklenir. `extend_15m`:
    `end`den sonra kapanacak 15m barları (saat ilerledikçe görünür; giriş dilimi bayatlamaz)."""
    end = P.CLOCK[0]
    rows4h = _series_4h(80, end_ts=end, rsi_high=True)
    last = rows4h[-1]["close"]
    rows1h = P._trend_series(60, start_ts=end - 60 * H1, step=H1, px0=last - 6, drift=0.1)
    rows15 = P._trend_series(60 + int(extend_15m), start_ts=end - 60 * M15, step=M15, px0=last - 1.5, drift=0.025)
    ents = [(s.replace("/", ""), s.split("/")[0], "USDT", "PERPETUAL", "TRADING", end - 900 * DAY) for s in symbols]
    ex = P._exinfo(ents)
    raws = [e[0] for e in ents]
    candles = {s: {"15m": P._df(rows15), "1h": P._df(rows1h), "4h": P._df(rows4h)} for s in symbols}
    prov = P._provider(candles, ex, tickers={r: P._ticker(r, px=last) for r in raws},
                       marks={r: {"mark": last, "ts": end, "funding_rate": 0.0} for r in raws})
    prov.books = {r: P._book(r, px=last) for r in raws}
    prov.depths = {r: P._depth(px=last) for r in raws}
    cfg = _cfg(tmp_path, learning=learning, **over)
    sc, book = P._scanner(cfg, prov)
    return sc, book, prov, rows4h


_WALL = {"updated_at", "generated_at", "verified_at", "last_seen_at", "first_seen_at"}


def _norm(x):
    """Duvar saati alanlarını at (iki koşu aynı saniyeye düşmeyebilir); kalan her şey bit-bit karşılaştırılır."""
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items() if k not in _WALL}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


def _files(cfg) -> dict[str, str]:
    st = cfg.state_path
    out = {}
    for rel in ("pattern_trader/futures_ledger.json", "pattern_trader/plans.json", "pattern_trader/findings.json", "pattern_trader.json"):
        out[rel] = json.dumps(_norm(json.loads((st / rel).read_text(encoding="utf-8"))), sort_keys=True)
    return out


# ---------------------------------------------------------------- doğrudan `_try_open` (birim) kurulumu
NOW = P.T0 + 10 * H4
ST = {"15m": {"source": "mock", "market": "USDM_PERP", "is_stale": False, "last_open_ms": NOW - M15},
      "4h": {"source": "mock", "market": "USDM_PERP", "is_stale": False, "last_open_ms": NOW - H4}}


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _ue(now_ms=NOW, *, tick="0.001", step="0.001", min_notional="5"):
    return {"eligible": True, "status": "TRADING", "as_of_ms": now_ms, "cohort": "90d+", "age_h": 9000.0,
            "filters": {"tick_size": tick, "step_size": step, "min_qty": step, "min_notional": min_notional, "raw": {}}}


def _plan(symbol="BTC/USDT", *, mark=100.0, stop=98.0, atr=2.0, now_ms=NOW, min_rr=1.0, pid=None):
    return {"plan_id": pid or ("p_" + symbol.split("/")[0]), "symbol": symbol, "side": "LONG", "family": FAMILY_V3,
            "version": PROTOCOL_V3, "status": PL_TRIGGERED, "trigger": {"level": mark, "rule": "close_above", "tf": "4h", "text_tr": "t"},
            "stop": stop, "atr": atr, "target": mark + 2.0 * (mark - stop), "target_source": "entry_rr_2.0",
            "target_from_entry_rr": 2.0, "risk_atr_bounds": [0.1, 5.0], "min_rr_after_cost": min_rr, "chase_atr": 1.0,
            "leverage": 1, "max_hold_bars": V3_MAX_HOLD_15M, "entry_tf": "4h", "created_at_ms": now_ms,
            "expires_at_ms": now_ms + 60 * 60_000, "triggered_at_ms": now_ms, "trigger_close": mark, "status_history": [],
            "reasons": [], "cohort": "90d+", "evidence": {"rsi14_at_confirm": 75.0}, "position_id": None}


def _liq(depth=100_000.0, spread=0.01):
    return lambda: {"spread_pct": spread, "depth_0_5pct": depth, "depth_1pct": None if depth is None else depth * 2,
                    "source": "mock", "ts": NOW}


def _price(mark=100.0, now_ms=NOW):
    return {"ok": True, "mark": mark, "price_ts_ms": now_ms, "age_s": 0.0}


def _direct(tmp_path, *, learning=True, bl_over=None, cfg_learning=None, cfg_over=None):
    cfg = _cfg(tmp_path, learning=(cfg_learning if cfg_learning is not None else (LEARN if learning else None)), **(cfg_over or {}))
    book = P._book_obj(cfg)
    if learning:
        book.set_learning(_bl(cfg, **(bl_over or {})))
    book.begin_cycle(_dt(NOW))
    return cfg, book


def _open(book, pl, *, now_ms=NOW, price=None, liq=None, ue=None):
    book.plans[pl["plan_id"]] = pl
    return book._try_open(pl, now=_dt(now_ms), as_of_ms=now_ms, price=price or _price(now_ms=now_ms), statuses=ST,
                          liquidity=liq or _liq(), universe_entry=ue or _ue(now_ms), decision_ms=now_ms)


# ====================================================================== OFF paritesi
def test_off_parity_absent_and_disabled_section_write_identical_files(tmp_path):
    """Bölüm yok / `enabled: false` / motor `set_learning(None)` verdi — üçünde de defter, plan, bulgu ve özet dosyaları
    duvar saati alanları dışında BİT-AYNI; tavan defterde (3), 4. ve 5. sinyal bugünkü kodla (MAX_POSITIONS) reddedilir."""
    runs = {}
    for name, learning in (("absent", None), ("disabled", LEARN_OFF), ("none_view", None)):
        P.CLOCK[0] = P.T0
        sc, book, prov, _ = _multi(tmp_path / name, learning=learning)
        if name == "none_view":
            book.set_learning(None, universe_symbols=["LNG/USDT"], gate=lambda: (True, "OK"))
        sc.scan_cycle(now_ms=P.CLOCK[0])
        assert book.ledger.enforce_position_cap is True and book.learning is None and book.cf is None
        assert len(book.ledger.positions) == 3 and book.rejections == {"MAX_POSITIONS": 2}, book.rejections
        doc = json.loads((book.cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
        assert "learning" not in doc
        assert not (book.state_dir / "counterfactual_trades.json").exists()
        runs[name] = _files(book.cfg)
    assert runs["absent"] == runs["disabled"] == runs["none_view"]


def test_off_parity_discover_without_carry_is_unchanged():
    now = P.T0
    ex = P._exinfo([("AAAUSDT", "AAA", "USDT", "PERPETUAL", "TRADING", now - 400 * DAY)])
    ok = MockProvider(symbols_info=ex, tickers={"AAAUSDT": P._ticker("AAAUSDT")}, books={"AAAUSDT": P._book("AAAUSDT")}, clock_ms=lambda: now)
    u1 = discover(ok, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15)
    boom = MockProvider(symbols_info=ex, books={"AAAUSDT": P._book("AAAUSDT")}, clock_ms=lambda: now, fail={"ticker24h"})
    a = discover(boom, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u1)
    b = discover(boom, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u1, carry_volume=False)
    assert _norm(a) == _norm(b)
    assert a["entries"]["AAA/USDT"]["reason"] == "VOLUME_UNKNOWN" and "volume_carried" not in a["entries"]["AAA/USDT"]


def test_off_path_keeps_every_baseline_reject(tmp_path):
    """Kapalıyken bugünkü retler aynen: R/R tabanı, yuvarlama sonrası risk, likidite (TERMİNAL), derinlik, soğuma."""
    _, book = _direct(tmp_path / "rr", learning=False)
    assert _open(book, _plan(min_rr=5.0)) == "REJECTED" and book.rejections == {"RR_BELOW_MIN_AT_ENTRY": 1}
    _, book = _direct(tmp_path / "round", learning=False)
    assert _open(book, _plan(), ue=_ue(tick="0.5")) == "REJECTED" and "RISK_ABOVE_CAP_AFTER_ROUNDING" in book.rejections
    _, book = _direct(tmp_path / "liq", learning=False)
    pl = _plan()
    assert _open(book, pl, liq=lambda: None) == "REJECTED" and pl["status"] == PL_REJECTED
    assert book.rejections == {"LIQUIDITY_UNKNOWN": 1} and book.cf is None
    _, book = _direct(tmp_path / "depth", learning=False)
    assert _open(book, _plan(), liq=_liq(depth=5_000.0)) == "REJECTED" and book.rejections == {"THIN_DEPTH": 1}


# ====================================================================== D1/D2/D3: 4. ve 5. pozisyon
def test_learning_opens_the_4th_and_5th_position_with_slot_sizing(tmp_path):
    sc, book, prov, _ = _multi(tmp_path, learning=LEARN)
    # defter bugünkü gibi (tavanlı) kurulur; tavan YALNIZ aktif öğrenme turunda kalkar (`begin_cycle`)
    assert book.learning_capable and book.ledger.enforce_position_cap is True
    book.set_learning(_bl(book.cfg), universe_symbols=[], gate=lambda: (True, "OK"))
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert book.ledger.enforce_position_cap is False
    assert set(book.ledger.positions) == set(LAB5), (book.rejections, [pl.get("reasons") for pl in book.plans.values()])
    assert not book.rejections
    E = float(book.ledger.starting_equity)
    tot_margin = 0.0
    for i, pos in enumerate(sorted(book.ledger.positions.values(), key=lambda p: p.id)):
        m = pos.meta["learning"]
        assert {"size_rule", "slots", "risk_pct", "risk_fraction_of_budget", "learning_unlocked_by"} <= set(m)
        assert m["size_rule"] == "SLOT" and m["slots"] == 30 and m["risk_pct"] == 0.5 and 1 <= pos.leverage <= 3
        risk = float(pos.qty) * abs(float(pos.entry_avg) - float(pos.stop))
        assert risk <= 0.005 * E + 1e-9 and 0 < m["risk_fraction_of_budget"] <= 1.0
        assert pos.features["learning"]["size_rule"] == "SLOT" and pos.features["in_lab_universe"] is True
        tot_margin += float(pos.isolated_margin)
        if i >= 3:
            assert "MAX_POSITIONS" in m["learning_unlocked_by"], m
    assert tot_margin <= 0.95 * E
    # kalıcı `allow_shrink` özniteliği değişmez (yalnız çağrı başına ezme)
    book.save(None, sc._now_dt())
    led = json.loads((book.state_dir / "futures_ledger.json").read_text(encoding="utf-8"))
    assert led["allow_shrink"] is False and led["enforce_position_cap"] is False
    doc = json.loads((book.cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    lm = doc["learning"]
    assert lm["active"] is True and lm["reason"] == "ACTIVE" and lm["counters"]["opened"] == 5
    assert lm["position_cap"] == {"max_open_positions": 3, "ledger_enforced": False, "applies_now": False}
    assert lm["open_under_learning"] == 5 and lm["book"]["slots"] == 30
    assert all(p.get("learning", {}).get("size_rule") == "SLOT" for p in doc["positions"].values())


def test_baseline_count_cap_label_ignores_learning_extra_positions(tmp_path):
    """(2026-09-28, ikinci doğrulama turu) Taban görünümü: defterdeki 3 pozisyon öğrenme-ekstra (R/R tabanı altında —
    taban bunları hiç açmazdı) → 4. sinyal için taban adet tavanı (3) DOLU sayılmaz; taban da açardı → etiket boş
    (politika) ve taban boyutu kaydedilir. Eskiden öğrenme defterinin adedi sayılıyordu (MAX_POSITIONS etiketi)."""
    _, book = _direct(tmp_path)
    syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    for s in syms[:3]:
        assert _open(book, _plan(s, min_rr=5.0)) == "OPENED"
        assert "RR_BELOW_MIN_AT_ENTRY" in book.ledger.positions[s].meta["learning"]["learning_unlocked_by"]
    assert _open(book, _plan(syms[3])) == "OPENED"
    m = book.ledger.positions[syms[3]].meta["learning"]
    assert m["learning_unlocked_by"] == [], m
    assert m["baseline_size"]["notional"] > 0 and m["baseline_size"]["leverage"] >= 1


# ====================================================================== D1: çalışma zamanı askısı tavanı geri getirir
@pytest.mark.parametrize("how", ["no_view", "gate_down"])
def test_runtime_suspension_restores_the_count_cap(tmp_path, how):
    sc, book, prov, _ = _multi(tmp_path, learning=LEARN)
    book.set_learning(_bl(book.cfg), gate=lambda: (True, "OK"))
    sc.scan_cycle(now_ms=P.CLOCK[0], limit=4)                 # 4 sembol taranır → 4 pozisyon (tavan 3'ün ÜSTÜ)
    assert len(book.ledger.positions) == 4 and not book.rejections
    if how == "no_view":
        book.set_learning(None, status={"reason": "LEARNING_MODE_SUSPENDED:MODE_TESTNET"})
    else:
        book.set_learning(_bl(book.cfg), gate=lambda: (False, "LEARNING_MODE_SUSPENDED:LIVE_ORDER_PATH_ENABLED"))
    P.CLOCK[0] += 60_000
    sc.scan_cycle(now_ms=P.CLOCK[0])                           # 5. sembol: öğrenme askıda → bugünkü tavan
    assert book.learning is None and len(book.ledger.positions) == 4
    assert book.rejections == {"MAX_POSITIONS": 1}, book.rejections
    assert book.learning_counters["max_positions_blocked"] == 1
    assert book.ledger.enforce_position_cap is True              # askıda defter bayrağı bugünküyle aynı
    doc = json.loads((book.cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    assert doc["learning"]["active"] is False and doc["learning"]["reason"].startswith("LEARNING_MODE_SUSPENDED:")
    assert doc["learning"]["position_cap"]["applies_now"] is True


def test_suspended_book_rejects_in_baseline_order_and_persists_baseline_cap_flag(tmp_path):
    """Askıdaki defter (config açık, motor görünümü yok) bugünkü sırayı izler: kill switch devrede + tavan dolu iken RED
    KODU baseline'daki gibi RiskEngine'den (KILL_SWITCH_ACTIVE) gelir, kitap düzeyi MAX_POSITIONS ön-kontrolü YOK; kalıcı
    `enforce_position_cap` bayrağı da baseline ile aynı (true)."""
    def run(sub, cfg_learning):
        cfg = _cfg(tmp_path / sub, learning=cfg_learning)
        book = P._book_obj(cfg)
        book.begin_cycle(_dt(NOW))                                 # görünüm yok → öğrenme yok (config açıksa askıda)
        assert book.learning is None
        opened = [_open(book, _plan(s)) for s in ("BTC/USDT", "ETH/USDT", "SOL/USDT")]
        book.risk.ks.trip("TEST", "", source="test")            # kill switch testin kendi state dizininde
        res = _open(book, _plan("BNB/USDT"))
        book.ledger.save(tmp_path / sub / "led.json")
        led = json.loads((tmp_path / sub / "led.json").read_text(encoding="utf-8"))
        return opened, res, dict(book.rejections), led["enforce_position_cap"]

    base = run("base", None)
    susp = run("susp", LEARN)
    assert base[0] == susp[0] == ["OPENED"] * 3 and base[1] == susp[1] == "REJECTED"
    assert base[2] == susp[2] == {"KILL_SWITCH_ACTIVE": 1}, (base[2], susp[2])
    assert base[3] is True and susp[3] is True


# ====================================================================== D5: soğuma kurulmaz
def test_learning_does_not_set_a_cooldown_after_a_loss(tmp_path):
    for learning, expect_cd in ((True, False), (False, True)):
        P.CLOCK[0] = P.T0
        sc, book, prov, _ = _multi(tmp_path / str(learning), symbols=("BTC/USDT",), learning=LEARN if learning else None)
        if learning:
            book.set_learning(_bl(book.cfg))
        sc.scan_cycle(now_ms=P.CLOCK[0])
        pos = book.ledger.positions["BTC/USDT"]
        P.CLOCK[0] += 5 * 60_000
        P._set_mark(prov, "BTC/USDT", float(pos.stop) * 0.99)
        sc.exit_check()
        assert not book.ledger.positions and float(book.ledger.history_dicts()[-1]["net_pnl"]) < 0
        assert ("BTC/USDT" in book.cooldown_until) is expect_cd
        assert book.learning_counters["cooldown_skipped"] == (1 if learning else 0)
    # öğrenmede önceden kalmış soğuma da giriş engellemez
    _, b2 = _direct(tmp_path / "pre", learning=True)
    b2.cooldown_until["BTC/USDT"] = NOW + DAY
    assert _open(b2, _plan()) == "OPENED"
    assert "COOLDOWN_AFTER_LOSS" in b2.ledger.positions["BTC/USDT"].meta["learning"]["learning_unlocked_by"]


# ====================================================================== D6: yuvarlama riski → ölçekle (ret yok)
def test_rounding_risk_rescales_the_notional_instead_of_rejecting(tmp_path):
    # slot tavanı bağlamasın (1 slot): notional risk bütçesiyle belirlenir → 1/oran ölçeği görünür
    _, book = _direct(tmp_path, bl_over={"slots": 1})
    pl = _plan()
    assert _open(book, pl, ue=_ue(tick="0.5")) == "OPENED", (book.rejections, pl.get("learning"))
    ratio = pl["rr_at_entry"]["risk_ratio_after_rounding"]
    assert ratio > 1.02 and pl["rr_at_entry"]["risk_rescaled"]["ratio"] == pytest.approx(ratio)
    pos = book.ledger.positions["BTC/USDT"]
    E = 100.0
    n_at_mark = 0.005 * E / (2.0 / 100.0)                      # bugünkü formül mark'la: 25 USDT
    assert float(pos.qty * pos.entry_avg) == pytest.approx(n_at_mark / ratio, rel=0.01)
    risk = float(pos.qty) * abs(float(pos.entry_avg) - 98.0)
    assert 0.49 <= risk <= 0.5 + 1e-9, "gerçekleşen risk bütçede (yuvarlama riski büyütmedi)"
    assert "RISK_ABOVE_CAP_AFTER_ROUNDING" in pos.meta["learning"]["learning_unlocked_by"]
    assert book.learning_counters["rescaled_after_rounding"] == 1


# ====================================================================== D7: R/R tabanı yok, değer özellik
def test_min_rr_is_ignored_and_recorded_as_a_feature(tmp_path):
    _, book = _direct(tmp_path)
    pl = _plan(min_rr=5.0)
    assert _open(book, pl) == "OPENED"
    f = book.ledger.positions["BTC/USDT"].features["learning"]
    assert f["rr_below_min"] is True and f["min_rr_after_cost"] == 5.0 and 0 < f["rr_after_cost"] < 5.0
    assert "RR_BELOW_MIN_AT_ENTRY" in f["learning_unlocked_by"] and book.learning_counters["rr_floor_ignored"] == 1


# ====================================================================== D16: likidite bekler, pencere sonunda CF
def test_liquidity_unknown_waits_and_retries_inside_the_window(tmp_path):
    _, book = _direct(tmp_path)
    pl = _plan()
    assert _open(book, pl, liq=lambda: None) == "WAIT_LIQUIDITY"
    assert pl["status"] == PL_TRIGGERED and not book.rejections and pl["liquidity_wait"]["attempts"] == 1
    t1 = NOW + 5 * 60_000
    assert _open(book, pl, now_ms=t1, liq=_liq(depth=None)) == "WAIT_LIQUIDITY"   # derinlik bilinmiyor da bekler
    assert pl["liquidity_wait"]["codes"] == ["LIQUIDITY_UNKNOWN", "DEPTH_UNKNOWN"]
    t2 = NOW + 10 * 60_000
    assert _open(book, pl, now_ms=t2, price=_price(now_ms=t2)) == "OPENED"
    assert pl["status"] == PL_MANAGED and pl["liquidity_wait"]["pending"] is False
    tags = book.ledger.positions["BTC/USDT"].meta["learning"]["learning_unlocked_by"]
    assert "LIQUIDITY_UNKNOWN" in tags and "DEPTH_UNKNOWN" in tags
    assert book.cf is None or book.cf.stats()["recorded_total"] == 0


def test_liquidity_still_unknown_at_expiry_means_no_trade_and_a_counterfactual(tmp_path):
    cfg, book = _direct(tmp_path)
    pl = _plan("ETH/USDT")
    assert _open(book, pl, liq=lambda: None) == "WAIT_LIQUIDITY"
    t1 = NOW + 30 * 60_000
    assert _open(book, pl, now_ms=t1, price=_price(101.0, t1), liq=lambda: None) == "WAIT_LIQUIDITY"
    t2 = NOW + 61 * 60_000
    assert _open(book, pl, now_ms=t2, price=_price(101.0, t2), liq=lambda: None) == "EXPIRED"
    assert pl["status"] == PL_EXPIRED and not book.ledger.positions and pl["expired_while"] == "LIQUIDITY_UNKNOWN"
    st = book.cf.stats()
    assert st["recorded_total"] == 1 and st["pending"] == 1
    t = book.cf.sb.trades[0]
    assert t.reason_not_opened == ["LIQUIDITY_UNKNOWN"] and t.signal_key == pl["plan_id"] == t.plan_id
    assert t.book == "pattern_trader" and t.label_kind == "TARGET_STOP_TIME" and t.approx is False
    assert (t.tf_minutes, t.horizon_bars) == (60, 96) == cf_horizon(V3_MAX_HOLD_15M), "96 saat, 1h barlarla"
    assert t.entry == 101.0 and t.stop == 98.0 and t.targets == [pytest.approx(101.0 + 2 * 3.0)]
    assert t.features["entry_ref"] == "last_mark" and t.features["in_lab_universe"] is True
    # etiket: defterin ELİNDEKİ 1h barlarıyla (hedef önce) — kayıt defteri/özkaynağı ETKİLEMEZ
    eq0 = float(book.ledger.wallet_balance)
    created = t2
    bars = [P._bar(created - H1 + i * H1, 101.0, 101.5 + (i * 2 if i > 1 else 0), 100.8, 101.2) for i in range(6)]
    assert book.label_counterfactuals("ETH/USDT", {"1h": bars}, _dt(created + 6 * H1)) == 1
    assert t.outcome["exit_reason"] == "target" and t.outcome["r_multiple"] == pytest.approx(2.0)
    assert float(book.ledger.wallet_balance) == eq0
    book.save(None, _dt(created + 6 * H1))
    doc = json.loads((book.state_dir / "counterfactual_trades.json").read_text(encoding="utf-8"))
    assert doc["meta"]["book"] == "pattern_trader" and doc["trades"][0]["signal_key"] == pl["plan_id"]
    summ = json.loads((cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    assert summ["learning"]["counterfactual"]["labeled"] == 1 and summ["learning"]["counters"]["counterfactual_recorded"] == 1


# ====================================================================== D17: derinlik eşiği notional'a bağlı
def test_depth_threshold_follows_the_learning_notional(tmp_path):
    _, book = _direct(tmp_path / "ok")
    assert _open(book, _plan(), liq=_liq(depth=5_000.0)) == "OPENED"       # 20.000 sabiti öğrenmede YOK
    m = book.ledger.positions["BTC/USDT"].meta["learning"]
    assert m["min_depth_0_5pct"] == pytest.approx(min_depth_for(m["notional"]), rel=1e-3) and "THIN_DEPTH" in m["learning_unlocked_by"]
    _, book = _direct(tmp_path / "floor")
    assert _open(book, _plan(), liq=_liq(depth=LEARNING_MIN_DEPTH_USDT - 1)) == "REJECTED" and book.rejections == {"THIN_DEPTH": 1}
    # büyük notional (E=1.000, 1 slot) → eşik 20 × notional > 2.000 (E=100'de tek pozisyon tavanı 90 USDT: taban bağlar)
    _, book = _direct(tmp_path / "mult", bl_over={"slots": 1}, cfg_over={"starting_equity_usdt": 1000.0})
    pl = _plan()
    assert _open(book, pl, liq=_liq(depth=2_500.0)) == "REJECTED" and book.rejections == {"THIN_DEPTH": 1}
    need = pl["learning"]["min_depth_0_5pct"]
    assert need > 2_500.0 and need == pytest.approx(20.0 * pl["learning"]["fit"]["notional"])
    _, book = _direct(tmp_path / "mult_ok", bl_over={"slots": 1}, cfg_over={"starting_equity_usdt": 1000.0})
    assert _open(book, _plan(), liq=_liq(depth=need + 1.0)) == "OPENED"


# ====================================================================== S9: birleşik evren
def test_entry_universe_is_protocol_coins_union_engine_universe_while_learning(tmp_path):
    sc, book, prov, _ = _multi(tmp_path, symbols=("BTC/USDT", "LNG/USDT"), learning=LEARN)
    assert "LNG/USDT" not in book.allowed_symbols
    sc.scan_cycle(now_ms=P.CLOCK[0])                                       # görünüm yok → baseline evren
    assert set(book.ledger.positions) == {"BTC/USDT"}
    book.set_learning(_bl(book.cfg), universe_symbols=["LNG/USDT", "ZZZ/USDT"])
    P.CLOCK[0] += 60_000
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert set(book.ledger.positions) == {"BTC/USDT", "LNG/USDT"}
    assert book.ledger.positions["LNG/USDT"].features["in_lab_universe"] is False
    assert "NOT_IN_PROTOCOL_UNIVERSE" in book.ledger.positions["LNG/USDT"].meta["learning"]["learning_unlocked_by"]
    assert book.entry_symbols() == book.allowed_symbols | {"LNG/USDT", "ZZZ/USDT"}
    # askıya alınınca birleşik evrenden kurulmuş bekleyen plan açılmaz
    _, b2 = _direct(tmp_path / "susp")
    pl = _plan("LNG/USDT")
    pl["in_lab_universe"] = False
    b2.set_learning(None)
    b2.begin_cycle(_dt(NOW))
    assert _open(b2, pl) == "CANCELLED" and pl["reasons"][-1] == "NOT_IN_PROTOCOL_UNIVERSE" and not b2.ledger.positions


# ====================================================================== D18: hacim taşıma
def test_volume_is_carried_forward_when_ticker24h_fails():
    now = P.T0
    ex = P._exinfo([("AAAUSDT", "AAA", "USDT", "PERPETUAL", "TRADING", now - 400 * DAY)])
    ok = MockProvider(symbols_info=ex, tickers={"AAAUSDT": P._ticker("AAAUSDT", vol=40e6)}, books={"AAAUSDT": P._book("AAAUSDT")},
                      clock_ms=lambda: now)
    u1 = discover(ok, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15)
    boom = MockProvider(symbols_info=ex, books={"AAAUSDT": P._book("AAAUSDT")}, clock_ms=lambda: now, fail={"ticker24h"})
    u2 = discover(boom, now_ms=now + H1, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u1, carry_volume=True)
    e = u2["entries"]["AAA/USDT"]
    assert e["eligible"] is True and e["quote_volume_24h"] == 40e6 and e["volume_carried"] is True
    assert e["volume_carried_from_ms"] == now and any("ticker24h" in x for x in u2["errors"])
    u3 = discover(boom, now_ms=now + 2 * H1, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u2, carry_volume=True)
    assert u3["entries"]["AAA/USDT"]["volume_carried_from_ms"] == now, "taşınan hacmin kaynağı ilk ölçüm anı"
    fresh = discover(ok, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u2, carry_volume=True)
    assert fresh["entries"]["AAA/USDT"]["volume_carried"] is False


def test_scanner_carries_volume_only_while_learning(tmp_path):
    for learning in (False, True):
        P.CLOCK[0] = P.T0
        sc, book, prov, _ = _multi(tmp_path / str(learning), symbols=("BTC/USDT",), learning=LEARN if learning else None,
                                   universe_refresh_minutes=0.0)
        if learning:
            book.set_learning(_bl(book.cfg))
        sc.scan_cycle(now_ms=P.CLOCK[0])
        prov.fail = {"ticker24h"}
        P.CLOCK[0] += 60_000
        sc.scan_cycle(now_ms=P.CLOCK[0])
        e = sc.universe["entries"]["BTC/USDT"]
        assert e["eligible"] is learning and (e.get("volume_carried") is True) is learning


# ====================================================================== §4: karşı-olgusallar
def test_chase_limit_writes_one_counterfactual_keyed_by_the_plan_id(tmp_path):
    sc, book, prov, rows4h = _multi(tmp_path, symbols=("BTC/USDT",), learning=LEARN)
    book.set_learning(_bl(book.cfg))
    last = rows4h[-1]["close"]
    P._set_mark(prov, "BTC/USDT", last + 20.0)                            # tetikten > 1 ATR: kovalama
    sc.scan_cycle(now_ms=P.CLOCK[0])
    pl = next(iter(book.plans.values()))
    assert pl["status"] == PL_CANCELLED and pl["reasons"][-1] == "CHASE_LIMIT" and not book.ledger.positions
    assert book.cf.stats()["recorded_total"] == 1
    t = book.cf.sb.trades[0]
    assert t.signal_key == pl["plan_id"] and t.reason_not_opened == ["CHASE_LIMIT"] and t.entry == pytest.approx(last + 20.0)
    assert t.features["family"] == FAMILY_V3 and t.features["rsi14_at_confirm"] > 70
    # aynı plan ikinci kez yazılmaz (P1: sinyal anahtarı turdan bağımsız)
    assert book._cf_record(pl, "CHASE_LIMIT", entry=last + 20.0, at=sc._now_dt()) is False
    for _ in range(3):
        P.CLOCK[0] += 15 * 60_000
        sc.scan_cycle(now_ms=P.CLOCK[0])
    assert book.cf.stats()["recorded_total"] == 1
    assert (book.state_dir / "counterfactual_trades.json").exists()
    # kapalıyken aynı senaryo karşı-olgusal YAZMAZ
    P.CLOCK[0] = P.T0
    sc2, b2, prov2, _ = _multi(tmp_path / "off", symbols=("BTC/USDT",))
    P._set_mark(prov2, "BTC/USDT", last + 20.0)
    sc2.scan_cycle(now_ms=P.CLOCK[0])
    assert b2.cf is None and not (b2.state_dir / "counterfactual_trades.json").exists()


def test_process_symbol_labels_counterfactuals_with_the_bars_it_holds(tmp_path, monkeypatch):
    sc, book, prov, rows4h = _multi(tmp_path, symbols=("BTC/USDT",), learning=LEARN)
    book.set_learning(_bl(book.cfg))
    P._set_mark(prov, "BTC/USDT", rows4h[-1]["close"] + 20.0)
    sc.scan_cycle(now_ms=P.CLOCK[0])
    seen = []
    orig = book.label_counterfactuals
    monkeypatch.setattr(book, "label_counterfactuals", lambda sym, bars, now: seen.append((sym, sorted(bars))) or orig(sym, bars, now))
    P.CLOCK[0] += 60_000
    sc.scan_cycle(now_ms=P.CLOCK[0])
    assert seen and seen[0] == ("BTC/USDT", ["15m", "1h", "4h"])


def test_position_open_and_exchange_rejects_write_counterfactuals(tmp_path, monkeypatch):
    # (a) aynı sembolde pozisyon açık: plan iptal + POSITION_OPEN karşı-olgusalı
    _, book = _direct(tmp_path / "occ")
    assert _open(book, _plan(pid="first")) == "OPENED"
    pl2 = _plan(pid="second")
    assert _open(book, pl2) == "CANCELLED" and pl2["reasons"][-1] == "SAME_SYMBOL_POSITION_OPEN"
    assert [t.reason_not_opened for t in book.cf.sb.trades] == [["POSITION_OPEN"]]
    # pozisyon AÇIKKEN taze sinyal (plan kurulmaz): v3 kurucu bir plan döndürür → POSITION_OPEN kaydı
    import tradingbot.pattern_trader.strategy_v3 as S3
    fresh = _plan(pid="third")
    monkeypatch.setattr(S3, "build_plans_v3", lambda *a, **k: ([dict(fresh)], []))
    n = book._cf_signals_while_open("BTC/USDT", as_of_ms=NOW, dec_ms=NOW, analyses={}, bars_by_tf={}, ue=_ue(), ds={},
                                    price=_price())
    assert n == 1 and "third" not in book.plans and book.cf.stats()["recorded_total"] == 2
    # (b) min-notional çıkarma KAPALI + borsa minimumu 50 → MIN_ORDER_CONFLICT reddi + karşı-olgusal
    _, b2 = _direct(tmp_path / "mno", bl_over={"min_notional_bump": False})
    pl = _plan()
    assert _open(b2, pl, ue=_ue(min_notional="50")) == "REJECTED" and b2.rejections == {"MIN_ORDER_CONFLICT": 1}
    assert b2.cf.sb.trades[0].reason_not_opened == ["MIN_ORDER_CONFLICT"] and pl["learning"]["fit"]["detail"]["why"] == "BUMP_DISABLED"
    # (c) çıkarma AÇIK: %2 tavan ve serbest marj içinde min-notional'a çıkarılır (adım YUKARI)
    _, b3 = _direct(tmp_path / "bump")
    assert _open(b3, _plan(), ue=_ue(min_notional="50")) == "OPENED"
    pos = b3.ledger.positions["BTC/USDT"]
    assert pos.meta["learning"]["size_rule"] == "BUMP_MIN_NOTIONAL" and float(pos.qty * pos.entry_avg) >= 50.0
    assert float(pos.qty) * abs(float(pos.entry_avg) - 98.0) <= 2.0 and b3.learning_counters["min_notional_bumped"] == 1
    # veri/geometri güvenilmezse (NEVER sınıfı) kayıt YOK
    _, b4 = _direct(tmp_path / "never")
    assert b4._cf_record(_plan(), "DATA_STALE_15M", entry=100.0, at=_dt(NOW)) is False
    assert b4._cf_record(_plan(), "STOP_BEYOND_QUANTIZED_ENTRY", entry=100.0, at=_dt(NOW)) is False


def test_position_open_counterfactual_is_superseded_when_the_same_plan_opens_later(tmp_path, monkeypatch):
    """F7 (Formasyon, 2026-09-28 öğrenme modu): pozisyon açıkken taze sinyal POSITION_OPEN kaydına düşer (plan kurulmaz);
    pozisyon aynı 4h barında kapanır, öğrenmede soğuma yok (D5), aynı plan kimliği yeniden kurulup GERÇEKTEN açılır →
    kayıt düşer (aynı gözlem hem dolum hem "açılmadı" SAYILMAZ), anahtar tekillikte kalır (yeniden yazılmaz)."""
    _, book = _direct(tmp_path)
    assert _open(book, _plan(pid="first")) == "OPENED"
    import tradingbot.pattern_trader.strategy_v3 as S3
    fresh = _plan(pid="third")
    monkeypatch.setattr(S3, "build_plans_v3", lambda *a, **k: ([dict(fresh)], []))
    assert book._cf_signals_while_open("BTC/USDT", as_of_ms=NOW, dec_ms=NOW, analyses={}, bars_by_tf={}, ue=_ue(), ds={},
                                       price=_price()) == 1
    assert "third" not in book.plans and [t.signal_key for t in book.cf.sb.trades] == ["third"]
    assert book.ledger.close_manual("BTC/USDT", 99.0, reason="STOP_TEST", now=_dt(NOW + 60_000)) is not None
    assert book._entry_block_reason("BTC/USDT", _ue(), NOW + 120_000) is None
    assert _open(book, _plan(pid="third", now_ms=NOW + 120_000), now_ms=NOW + 120_000) == "OPENED"
    assert book.ledger.positions["BTC/USDT"].meta.get("plan_id") == "third"
    assert book.cf.sb.trades == [] and book.cf.stats()["superseded"] == 1
    assert book.learning_counters["counterfactual_superseded"] == 1
    # aynı sinyal (kapanan pozisyon sonrası) yeniden taranırsa YENİDEN kaydedilmez
    assert book._cf_record(_plan(pid="third"), "POSITION_OPEN", entry=100.0, at=_dt(NOW + 180_000)) is False
    book.save({"BTC/USDT": 100.0}, _dt(NOW + 180_000))
    doc = json.loads((book.state_dir / "counterfactual_trades.json").read_text(encoding="utf-8"))
    assert doc["trades"] == [] and doc["meta"]["superseded"] == 1
    summ = json.loads((book.cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    assert summ["learning"]["counters"]["counterfactual_superseded"] == 1


def test_learning_counters_survive_an_off_process_via_the_counterfactual_backup(tmp_path):
    """(2026-09-28, ikinci doğrulama turu) Özet `learning` alanı kapalı süreçte (ve eski kodda) yazılmaz; sayaçlar
    `counterfactual_trades.json` `meta.book_counters` yedeğinden ilk aktif turda geri gelir (0'dan başlamaz)."""
    cfg, book = _direct(tmp_path)
    assert _open(book, _plan("BTC/USDT")) == "OPENED" and _open(book, _plan("ETH/USDT")) == "OPENED"
    assert book.learning_counters["opened"] == 2
    book.save({"BTC/USDT": 100.0, "ETH/USDT": 100.0}, _dt(NOW))
    doc = json.loads((book.state_dir / "counterfactual_trades.json").read_text(encoding="utf-8"))
    assert doc["meta"]["book_counters"]["opened"] == 2
    off_cfg = _cfg(tmp_path, learning=LEARN_OFF)                     # yeni süreç, öğrenme KAPALI: özet alanı düşer
    off = P._book_obj(off_cfg)
    assert off.learning_counters["opened"] == 2                      # (özetten; kapalı kaydetme alanı düşürür)
    off.begin_cycle(_dt(NOW + 60_000))
    off.save({"BTC/USDT": 100.0, "ETH/USDT": 100.0}, _dt(NOW + 60_000))
    assert "learning" not in json.loads((off_cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    on = P._book_obj(_cfg(tmp_path, learning=LEARN))                 # yeniden AÇIK
    assert on.learning_counters["opened"] == 0
    on.set_learning(_bl(on.cfg))
    on.begin_cycle(_dt(NOW + 120_000))
    assert on.learning_counters["opened"] == 2, on.learning_counters


def test_learning_view_is_fixed_per_cycle_and_the_gate_is_asked_once(tmp_path):
    cfg, book = _direct(tmp_path, learning=False, cfg_learning=LEARN)
    calls = []

    def gate():
        calls.append(1)
        return True, "OK"
    book.set_learning(_bl(cfg), gate=gate)
    assert book.learning is None, "görünüm tarayıcının SONRAKİ turunda devreye girer"
    assert book.begin_cycle(_dt(NOW)) is True and book.learning_on and len(calls) == 1
    book.set_learning(None)                                                # tur ortasında değişmez
    assert book.learning_on
    assert book.begin_cycle(_dt(NOW + 60_000)) is False and book._lm_reason.startswith("LEARNING_MODE_SUSPENDED:")

    def boom():
        raise RuntimeError("x")
    book.set_learning(_bl(cfg), gate=boom)
    assert book.begin_cycle(_dt(NOW + 120_000)) is False and "MODE_GATE_ERROR" in book._lm_reason


def test_depth_unknown_waits_across_scanner_cycles_then_opens_or_expires(tmp_path):
    """Uçtan uca (gerçek PriceService/MarketFeed): derinlik ölçülemedi → plan bekler, kuyrukta kalır; ölçüm gelince
    açılır. Pencere boyunca ölçülemezse: işlem YOK + LIQUIDITY_UNKNOWN karşı-olgusalı."""
    for outcome in ("opens", "expires"):
        P.CLOCK[0] = P.T0
        sc, book, prov, rows4h = _multi(tmp_path / outcome, symbols=("BTC/USDT",), learning=LEARN, extend_15m=8)
        book.set_learning(_bl(book.cfg))
        last = rows4h[-1]["close"]
        prov.fail = {"depth"}
        sc.scan_cycle(now_ms=P.CLOCK[0])
        pl = next(iter(book.plans.values()))
        assert pl["status"] == PL_TRIGGERED and pl["liquidity_wait"]["codes"] == ["DEPTH_UNKNOWN"] and not book.rejections
        assert ("BTC/USDT", "open_position") not in sc.queue(now_ms=P.CLOCK[0])
        assert ("BTC/USDT", "pending_plan") in sc.queue(now_ms=P.CLOCK[0])
        if outcome == "opens":
            P.CLOCK[0] += 10 * 60_000
            P._set_mark(prov, "BTC/USDT", last)
            prov.fail = set()
            sc.scan_cycle(now_ms=P.CLOCK[0])
            assert "BTC/USDT" in book.ledger.positions and pl["status"] == PL_MANAGED, pl.get("reasons")
            assert "DEPTH_UNKNOWN" in book.ledger.positions["BTC/USDT"].meta["learning"]["learning_unlocked_by"]
            assert book.cf is None or book.cf.stats()["recorded_total"] == 0
        else:
            for mins in (20, 40, 61):
                P.CLOCK[0] = P.T0 + mins * 60_000
                P._set_mark(prov, "BTC/USDT", last)
                sc.scan_cycle(now_ms=P.CLOCK[0])
            assert pl["status"] == PL_EXPIRED and not book.ledger.positions and not book.rejections
            assert book.cf.stats()["recorded_total"] == 1 and book.cf.sb.trades[0].reason_not_opened == ["LIQUIDITY_UNKNOWN"]
            assert (book.state_dir / "counterfactual_trades.json").exists()


# ====================================================================== D2: çıkarma RiskEngine yuvarlamasından sağ çıkar
@pytest.mark.parametrize("px", [2500.73, 2500.77])
def test_min_notional_bump_survives_risk_engine_4dp_rounding(px):
    """RiskEngine `adjusted_notional`ı 4 haneye yuvarlar; göreli tolerans (1e-6 × 20 = 2e-5) bu artığı (≤ 5e-5) gerçek aşağı
    ayar sanıyordu → çıkarılan adım kayboluyor, defter MIN_NOTIONAL veriyordu. Mutlak tolerans: pozisyon açılır."""
    from decimal import Decimal

    from tradingbot.accounting import FeeSchedule, FuturesLedgerV2, SlippageModel, TickData
    from tradingbot.accounting.models import MarketType, SymbolFilters
    from tradingbot.learning_mode import BookLearning, profile_for
    from tradingbot.pattern_trader.learning import open_learning
    from tradingbot.risk import RiskEngine, build_state
    from tradingbot.risk.killswitch import KillSwitch
    from tradingbot.risk.profiles import PROFILES
    from tradingbot.strategy_paper import DataVerdict

    now = _dt(NOW)
    E = 100.0
    led = FuturesLedgerV2(starting_equity=E, fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05")),
                          slippage=SlippageModel(fixed_bps=Decimal("3")))
    filt = SymbolFilters(symbol="ETH/USDT", market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"),
                         qty_step=Decimal("0.001"), min_qty=Decimal("0.001"), min_notional=Decimal("20"), max_leverage=20)
    tick = TickData(last=Decimal(str(px)), mark=Decimal(str(px)), ts=now.isoformat())
    fill = float(led.market_fill_price("ETH/USDT", "LONG", Decimal(str(px)), filters=filt, tick=tick))
    st = build_state(equity=E, starting_equity=E, available=float(led.available), used_margin=0.0, positions=[],
                     history=[], high_water_mark=0.0, now=now)
    bl = BookLearning(on=True, name="pattern_trader", slots=30, leverage_max=3, risk_pct=0.5, reserve_pct=5.0,
                      liq_buffer_mult=2.0, min_notional_bump=True, counterfactual=False, max_pending=10,
                      min_stop_pct=None, symbols=None)
    act = {"action": "OPEN", "direction": "LONG", "stop": px * 0.93, "targets": [px * 1.2], "name": "p", "leverage": 1}
    dv = DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="t", tour_id="r", bars={})
    r = open_learning(act=act, symbol="ETH/USDT", price=px, fill_price=fill, tick=tick, now=now, ledger=led,
                      risk=RiskEngine(profile_for(PROFILES["PAPER_RESEARCH"]), KillSwitch()), state=st, filters=filt,
                      run_id="r", learning=bl, data=dv, max_position_pct=30.0, depth=None)
    assert r.info["fit"]["size_rule"] == "BUMP_MIN_NOTIONAL", r.info
    assert r.pos is not None, (r.reason, r.info["fit"])
    assert float(r.pos.qty * r.pos.entry_avg) >= 20.0 and r.pos.meta["learning"]["size_rule"] == "BUMP_MIN_NOTIONAL"


def test_pattern_counterfactual_recorder_has_the_lossless_archive(tmp_path):
    """Formasyon kayıtçısı da strateji defterleri gibi arşivli: etiketli kayıtlar taşınca SİLİNMEZ, arşive mühürlenir."""
    _, book = _direct(tmp_path)
    cf = book._cf_recorder(book.learning)
    assert cf.sb.archive is not None
    assert str(cf.sb.archive.root).startswith(str(book.state_dir))


def test_pattern_trade_memory_entry_rows_carry_learning_tags_only_under_learning(tmp_path):
    out = {}
    for name, learning in (("off", False), ("on", True)):
        _, book = _direct(tmp_path / name, learning=learning)
        assert _open(book, _plan()) == "OPENED", book.rejections
        rows = [r for r in book.memory.iter_rows() if r.get("kind") == "entry"]
        assert len(rows) == 1
        out[name] = rows[0]["features"]
    assert "learning" not in out["off"] and "in_lab_universe" not in out["off"]
    assert out["on"]["learning"]["size_rule"] in ("SLOT", "BUMP_MIN_NOTIONAL") and out["on"]["learning"]["slots"] == 30
    assert out["on"]["in_lab_universe"] is True

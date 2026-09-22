# -*- coding: utf-8 -*-
"""ORTAK YAPI → T2 / M2 / Box GERÇEK KARAR ZİNCİRİ (structures_v1).

Zincir: geçerli OHLCV (SENTETİK, etiketli: `structure_fixtures`) → ortak analiz (`structures.analyze`, gerçek dedektör)
→ bot politikası (`structures.policy`) → `paper_rules.decide_with_structures` → `StrategyBook.step` → `apply_action`
(risk motoru + defter) → pozisyon → yönetim/kapanış → karar deposu (panelin okuduğu kayıt). Dedektör ve karar kodu
taklit EDİLMEZ; yalnız veri sentetiktir. Her senaryoda aynı veriyle mod OFF kontrol kolu koşar (fark = yapının etkisi).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_fixtures import (DAY, M5, bearish_engulf_confirmed, box_day, breakout_bar, neutral_trend,  # noqa: E402
                                pole_and_consolidation, swing_high_then_sweep)

from tradingbot.accounting import TickData  # noqa: E402
from tradingbot.accounting.filters import FiltersCache  # noqa: E402
from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.risk.killswitch import KillSwitch  # noqa: E402
from tradingbot.risk.profiles import PROFILES  # noqa: E402
from tradingbot.strategy_paper import StrategyBook, book_specs  # noqa: E402
from tradingbot.structures.store import StructureStore  # noqa: E402

SYM = "ETH/USDT"
BTC = "BTC/USDT"
RUN = "RUN-1"
T0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY
BOX_PARAMS = {"near_frac": 0.10, "trigger": "break_prev", "long_stop": "day_low", "exit_kind": "box_mid", "exit_r": 2.0,
              "min_stop_pct": 2.22, "eod_close": True, "allow_long": True, "allow_short": True, "leverage": 3, "leverage_max": 4}


def _cfg(tmp_path: Path, mode: str, box_params: dict | None = None) -> BotConfig:
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({
        "strategy_paper": {"enabled": True, "name": "t2_trend_regime", "starting_equity_usdt": 200,
                           "rule_params": {"leverage": 2, "leverage_max": 4},
                           "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2", "starting_equity_usdt": 200,
                                      "rule_params": {"leverage": 2, "leverage_max": 4}},
                                     {"name": "b1_box_fade", "state_dir": "strategy_paper_box", "starting_equity_usdt": 200,
                                      "rule_params": dict(box_params or BOX_PARAMS)}]},
        "structures": {"enabled": True, "t2_trend_regime": mode, "m2_tsmom28": mode, "b1_box_fade": mode}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    return cfg


def _book(cfg: BotConfig, name: str) -> StrategyBook:
    spec = next(s for s in book_specs(cfg.v3) if s.name == name)
    return StrategyBook(cfg, profile=PROFILES["PAPER_RESEARCH"], killswitch=KillSwitch(),
                        filters_cache=FiltersCache(cfg.cache_path / "symbol_filters.json"), run_id=RUN, spec=spec)


def _df(rows):
    return pd.DataFrame(rows)


def _step(book: StrategyBook, frames: dict, *, as_of_ms: int, price: float, btc: list | None = None) -> None:
    fbs = {SYM: {tf: _df(r) for tf, r in frames.items()}}
    if btc is not None:
        fbs[BTC] = {"1d": _df(btc)}
    prov = {s: {"market": "USDM_PERP", "source": "test:sentetik", "entry_ok": True, "tour_id": RUN,
                "frames": {tf: {"last_ts": int(df["timestamp"].iloc[-1]), "n": len(df)} for tf, df in fr.items()}}
            for s, fr in fbs.items()}
    book.run_id = RUN
    book.step(symbols=[SYM], frames_by_symbol=fbs, marks={SYM: TickData(last=Decimal(str(price)), mark=Decimal(str(price)))},
              marks_f={SYM: float(price)}, now=datetime.fromtimestamp(as_of_ms / 1000.0, tz=timezone.utc),
              provenance_by_symbol=prov, data_gaps={})


def _daily_flag():
    base = neutral_trend(230, start_ms=T0, step=DAY, up=True, scale=0.3)
    flag = pole_and_consolidation(base, step=DAY)
    top = max(r["high"] for r in flag[-5:])
    return flag, breakout_bar(flag, step=DAY, above=top)


def _btc(n: int) -> list:
    return neutral_trend(n, start_ms=T0, step=DAY, up=True, scale=0.3)


def _asof(rows, step=DAY) -> int:
    return int(rows[-1]["timestamp"]) + step + 60_000


# ============================================================================ T2
def test_t2_waits_for_a_forming_flag_and_enters_on_its_breakout_control_enters_immediately(tmp_path):
    flag, brk = _daily_flag()
    btc = _btc(len(brk))
    enf = _book(_cfg(tmp_path / "enf", "ENFORCE"), "t2_trend_regime")
    off = _book(_cfg(tmp_path / "off", "OFF"), "t2_trend_regime")
    # tur 1: bayrak OLUŞUYOR (kural OPEN diyor) → tetiği bekle; kontrol kolu hemen açar
    _step(enf, {"1d": flag}, as_of_ms=_asof(flag), price=flag[-1]["close"], btc=btc[:len(flag)])
    _step(off, {"1d": flag}, as_of_ms=_asof(flag), price=flag[-1]["close"], btc=btc[:len(flag)])
    assert SYM not in enf.ledger.positions and SYM in off.ledger.positions
    d = enf.structure_decisions[SYM]
    assert d["action"] == "WAIT_TRIGGER" and d["primary"]["name"] == "BULL_FLAG" and d["primary"]["status"] == "FORMING"
    assert enf.last_actions[SYM]["reason"] == "STRUCTURE_WAIT_TRIGGER"
    assert "kapanışı" in d["text_tr"] and d["plan"]["trigger"]["level"] > 0 and d["plan"]["expires_at_ms"] > _asof(flag)
    # tur 2: kırılış barı KAPANDI → teyitli uyumlu yapı → giriş (teyit kapanışından sonraki doğrulanmış fiyat)
    _step(enf, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc)
    pos = enf.ledger.positions[SYM]
    s = pos.features["structure"]
    assert s["name"] == "BULL_FLAG" and s["action"] == "ENTER" and s["status"] == "CONFIRMED"
    assert s["policy_version"] == "structures_v1" and s["confirmed_at_ms"] <= _asof(brk)
    assert float(pos.stop) < float(pos.entry_avg), "T2 stopu kendi kuralından (kapanış − 3×ATR); yapı yönü çevirmez"
    # karar deposu: işlem kimliği + değişmez analiz anlık görüntüsü (panel aynı kaydı çizer)
    st = StructureStore(enf.cfg.state_path)
    row = st.latest_decisions()["strategy_paper|USDM_PERP|%s" % SYM]
    assert row["action"] == "ENTER" and row["trade_id"] == pos.id and row["applied"] == "OPENED"
    snap = st.load_snapshot(s["analysis_id"])
    assert snap is not None and any(r["pattern_id"] == s["pattern_id"] for r in snap["records"])


def test_t2_rescan_and_restart_do_not_open_a_second_trade_from_the_same_structure(tmp_path):
    flag, brk = _daily_flag()
    btc = _btc(len(brk))
    cfg = _cfg(tmp_path, "ENFORCE")
    b1 = _book(cfg, "t2_trend_regime")
    _step(b1, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc)
    first = b1.ledger.positions[SYM].id
    b1.save({SYM: brk[-1]["close"]}, datetime.fromtimestamp(_asof(brk) / 1000, tz=timezone.utc))
    _step(b1, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc)          # aynı tarama
    b2 = _book(cfg, "t2_trend_regime")                                                   # yeniden başlatma
    _step(b2, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc)
    assert list(b2.ledger.positions) == [SYM] and b2.ledger.positions[SYM].id == first
    assert b2.counters["opened"] == 1
    # pozisyon kapansa bile AYNI yapı ikinci girişi üretmez (kullanılmış yapı defterden okunur)
    b2.ledger.close_manual(SYM, brk[-1]["close"], reason="test", now=datetime.fromtimestamp(_asof(brk) / 1000, tz=timezone.utc))
    from tradingbot.structures.bots import used_patterns_of
    assert b2.ledger.history[-1].features["structure"]["pattern_id"] in used_patterns_of(b2.ledger)


def test_t2_confirmed_opposing_candle_blocks_the_entry_control_enters(tmp_path):
    rows = bearish_engulf_confirmed(neutral_trend(230, start_ms=T0, step=DAY, up=True, scale=0.3), step=DAY)
    btc = _btc(len(rows))
    enf = _book(_cfg(tmp_path / "enf", "ENFORCE"), "t2_trend_regime")
    off = _book(_cfg(tmp_path / "off", "OFF"), "t2_trend_regime")
    for b in (enf, off):
        _step(b, {"1d": rows}, as_of_ms=_asof(rows), price=rows[-1]["close"], btc=btc)
    assert SYM not in enf.ledger.positions and SYM in off.ledger.positions
    d = enf.structure_decisions[SYM]
    assert d["action"] == "WAIT" and d["reason_code"] == "OPPOSING_CONFIRMED" and d["primary"]["side"] == "SHORT"
    assert d["side"] == "LONG", "T2'nin yönü (LONG) mum yüzünden SHORT'a ÇEVRİLMEDİ; yalnız beklendi"


# ============================================================================ M2
def test_m2_times_the_entry_on_the_flag_and_exits_on_a_structure_confirmed_after_entry(tmp_path):
    flag, brk = _daily_flag()
    after = swing_high_then_sweep(brk, step=DAY)
    btc = _btc(len(after))
    m2 = _book(_cfg(tmp_path, "ENFORCE"), "m2_tsmom28")
    _step(m2, {"1d": flag}, as_of_ms=_asof(flag), price=flag[-1]["close"], btc=btc[:len(flag)])
    assert SYM not in m2.ledger.positions and m2.structure_decisions[SYM]["action"] == "WAIT_TRIGGER"
    _step(m2, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc[:len(brk)])
    pos = m2.ledger.positions[SYM]
    assert pos.features["structure"]["name"] == "BULL_FLAG"
    # tutarken: girişten SONRA teyitli süpürme (başarısız kırılım) → M2 yapı çıkışı (kuralın kendi çıkışı gelmeden)
    _step(m2, {"1d": after}, as_of_ms=_asof(after), price=after[-1]["close"], btc=btc)
    assert SYM not in m2.ledger.positions
    rec = m2.ledger.history[-1]
    assert rec.exit_reason == "M2_STRUCTURE_EXIT" and rec.features["exit_structure"]["name"] in ("SWEEP_RECLAIM", "DOUBLE_TOP", "HEAD_AND_SHOULDERS")
    assert rec.features["exit_structure"]["confirmed_at_ms"] > int(datetime.fromisoformat(str(rec.opened_at).replace("Z", "+00:00")).timestamp() * 1000)
    assert m2.structure_decisions[SYM]["action"] == "EXIT" and m2.structure_decisions[SYM]["applied"] == "CLOSED"


def test_m2_exits_when_its_entry_structure_fails_after_entry(tmp_path):
    flag, brk = _daily_flag()
    inv = min(r["low"] for r in flag[-5:])
    lb = brk[-1]
    fail = list(brk) + [
        {"timestamp": lb["timestamp"] + DAY, "open": lb["close"] * 0.998, "high": lb["close"] * 0.999,
         "low": inv * 0.985, "close": inv * 0.99, "volume": 1.0}]
    btc = _btc(len(fail))
    m2 = _book(_cfg(tmp_path, "ENFORCE"), "m2_tsmom28")
    _step(m2, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc[:len(brk)])
    assert SYM in m2.ledger.positions
    _step(m2, {"1d": fail}, as_of_ms=_asof(fail), price=fail[-1]["close"], btc=btc)
    assert SYM not in m2.ledger.positions and m2.ledger.history[-1].exit_reason == "ENTRY_STRUCTURE_FAILED"


# ============================================================================ Box
def _box_frames(ending: str, day0: int):
    daily, m5 = box_day(day0=day0, ending=ending)
    return daily, m5


def test_box_sweep_at_the_top_opens_a_short_fade_and_an_outside_breakout_exits_it(tmp_path):
    day0 = T0 + 300 * DAY
    daily, m5 = _box_frames("sweep", day0)
    box = _book(_cfg(tmp_path, "ENFORCE"), "b1_box_fade")
    _step(box, {"1d": daily, "5m": m5}, as_of_ms=_asof(m5, M5), price=m5[-1]["close"])
    pos = box.ledger.positions[SYM]
    s = pos.features["structure"]
    assert pos.side.value == "SHORT" and s["name"] == "SWEEP_RECLAIM" and s["action"] == "ENTER"
    assert float(pos.stop) > max(r["high"] for r in m5[-1:]), "stop = kaydın geçersizliği (taşma ucu) + tampon"
    assert [float(t) for t in pos.targets] == [100.0], "hedef Box kuralından (kutu ortası), yapı hedefi uydurulmaz"
    # giriş fill'i canlı mark'tan; teyit barının kapanışı yalnız kayma kapısı referansı (geriye yazılmaz)
    assert abs(float(pos.meta["ref_entry"]) - m5[-1]["close"]) < 1e-9
    # sonra kutu tepesinin ÜSTÜNDE ardışık iki kapanış → teyitli dış kırılım → açık fade ÇIKIŞ
    hi = 105.0
    t = m5[-1]["timestamp"]
    more = list(m5) + [{"timestamp": t + M5, "open": 104.7, "high": 105.7, "low": 104.6, "close": 105.4, "volume": 1.0},
                       {"timestamp": t + 2 * M5, "open": 105.4, "high": 106.0, "low": 105.2, "close": 105.8, "volume": 1.0}]
    _step(box, {"1d": daily, "5m": more}, as_of_ms=_asof(more, M5), price=more[-1]["close"])
    assert SYM not in box.ledger.positions
    assert box.ledger.history[-1].exit_reason == "BOX_OUTSIDE_BREAKOUT_EXIT" and more[-1]["close"] > hi


def test_box_confirmed_outside_breakout_cancels_the_opposite_fade(tmp_path):
    day0 = T0 + 300 * DAY
    daily, m5 = _box_frames("breakout", day0)
    box = _book(_cfg(tmp_path, "ENFORCE"), "b1_box_fade")
    _step(box, {"1d": daily, "5m": m5}, as_of_ms=_asof(m5, M5), price=m5[-1]["close"])
    assert SYM not in box.ledger.positions
    d = box.structure_decisions[SYM]
    assert d["action"] == "CANCEL" and d["reason_code"] == "BOX_OUTSIDE_BREAKOUT" and d["primary"]["name"] == "RANGE_BREAKOUT"


def test_box_old_red_candle_trigger_no_longer_opens_without_a_confirmed_edge_structure(tmp_path):
    """Kontrol (üretim parametreleri, asgari stop %2,22): eski tetik — tepe bandında önceki mumun dibinin ALTINDA kapanan
    kırmızı mum — OFF modda boyutlanabilir stopla AÇAR; ENFORCE'ta katalog kaydı henüz TEYİTSİZ (yutan oluşuyor) olduğu
    için AÇMAZ. Kutu 90–110; önceki mum kutuyu AŞMAZ (taşma-geri dönüş yok)."""
    from structure_fixtures import _bar
    day0 = T0 + 300 * DAY
    daily = [_bar(day0 - i * DAY, 100.0, 102.0, 98.0, 100.5) for i in range(5, 1, -1)] + [_bar(day0 - DAY, 100.0, 110.0, 90.0, 101.0)]
    m5 = neutral_trend(46, start_ms=day0, step=M5, px0=101.0, up=True, scale=0.2)
    t = m5[-1]["timestamp"]
    m5 += [_bar(t + M5, 108.5, 109.9, 107.9, 109.0),          # topaç (tarafsız); tepe bandında (≥108), kutu içinde
           _bar(t + 2 * M5, 109.1, 109.2, 107.2, 107.4)]      # kırmızı: önceki dibin (107.9) altında kapanış; stop 109.9 → %2,33
    enf = _book(_cfg(tmp_path / "enf", "ENFORCE"), "b1_box_fade")
    off = _book(_cfg(tmp_path / "off", "OFF"), "b1_box_fade")
    for b in (enf, off):
        _step(b, {"1d": daily, "5m": m5}, as_of_ms=_asof(m5, M5), price=m5[-1]["close"])
    assert SYM in off.ledger.positions and off.last_actions[SYM]["reason"] == "BOX_FADE_TOP", "eski tetik kontrol kolunda ATEŞLEDİ"
    assert SYM not in enf.ledger.positions
    d = enf.structure_decisions[SYM]
    assert d["action"] in ("WAIT_TRIGGER", "NO_EFFECT") and enf.last_actions[SYM]["action"] == "NONE"
    if d["action"] == "WAIT_TRIGGER":
        assert d["primary"]["side"] == "SHORT" and d["primary"]["status"] == "FORMING"


# ============================================================================ replay ↔ PAPER paritesi
def test_replay_strategy_mode_makes_the_same_structure_decisions_as_the_live_book(tmp_path):
    """Aynı günlük veri: canlı `StrategyBook.step` ve replay strateji modu (`paper_rules.replay_strategy`) AYNI
    `decide_with_structures` girişini kullanır → oluşan bayrakta ikisi de BEKLER, kırılış barında ikisi de AYNI yapı
    kimliğiyle açar. Karar anı: canlıda bar kapanışı + 60 sn, replay'de bar kapanışı (aynı kapanmış satırlar → aynı
    analiz kimliği). Dolum: canlıda doğrulanmış perp mark, replay'de adım barının kapanışı (her ikisi `apply_action`)."""
    from tradingbot.accounting import TickData as TD
    from tradingbot.ema200_trend import TrendParams
    from tradingbot.history import HistoryStore
    from tradingbot.paper_rules import replay_strategy
    from tradingbot.replay.engine import HistoricalReplay

    flag, brk = _daily_flag()
    btc = _btc(len(brk))
    live = _book(_cfg(tmp_path / "live", "ENFORCE"), "t2_trend_regime")
    _step(live, {"1d": flag}, as_of_ms=_asof(flag), price=flag[-1]["close"], btc=btc[:len(flag)])
    _step(live, {"1d": brk}, as_of_ms=_asof(brk), price=brk[-1]["close"], btc=btc)
    live_pid = live.ledger.positions[SYM].features["structure"]["pattern_id"]
    live_open_bar = int(brk[-1]["timestamp"])

    store = HistoryStore(tmp_path / "hist")
    store.write("futures", SYM, "1d", _df(brk), source="test:sentetik")
    store.write("futures", BTC, "1d", _df(btc), source="test:sentetik")
    cfg = _cfg(tmp_path / "rep", "ENFORCE")
    rep = HistoricalReplay(cfg, run_id="parity", store=store, symbols=[SYM], market="futures", tf="1d", seed=0,
                           strategy=replay_strategy("t2_trend_regime", params=TrendParams(atr_mult=3.0, leverage=2, leverage_max=4)))
    rep.frames = {SYM: {"1d": _df(brk)}, BTC: {"1d": _df(btc)}}
    rep.primary = {SYM: rep.frames[SYM]["1d"]}
    opened_at_bar = None
    for row in brk[-3:]:
        t = int(row["timestamp"])
        now = datetime.fromtimestamp((t + DAY) / 1000, tz=timezone.utc)
        px = float(row["close"])
        rep._strategy_step(t, now, {SYM: TD(last=Decimal(str(px)), mark=Decimal(str(px)))}, {SYM: px})
        if SYM in rep.ledger2.positions and opened_at_bar is None:
            opened_at_bar = t
    assert opened_at_bar == live_open_bar, (opened_at_bar, live_open_bar)
    assert rep.ledger2.positions[SYM].features["structure"]["pattern_id"] == live_pid
    log = rep._structure_log
    assert [x["action"] for x in log[-2:]] == ["WAIT_TRIGGER", "ENTER"], log[-3:]

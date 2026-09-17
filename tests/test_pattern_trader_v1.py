# -*- coding: utf-8 -*-
"""FORMASYON PAPER TRADER V1 — keşif → 15m/1h/4h veri → şekil → koşullu plan → tetik → risk → PAPER giriş → yönetim →
kapanış → rapor zincirinin UÇTAN UCA kanıtı.

ETİKET: gerçek `PatternBook`/`FuturesLedgerV2`/`RiskEngine`/`apply_action`/`PatternScanner`/`MarketFeed` kodu çalışır;
AĞ yerine `market.providers.MockProvider` (deterministik mumlar, exchangeInfo, mark, orderbook) kullanılır. Hiçbir
doğrulama fonksiyonu mock'lanmaz. Bu GERÇEK PİYASA SONUCU DEĞİLDİR: sentetik fiyat yoluyla davranış kanıtıdır.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.accounting.filters import FiltersCache  # noqa: E402
from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.market.feed import MarketFeed  # noqa: E402
from tradingbot.market.providers import MockProvider  # noqa: E402
from tradingbot.pattern_trader.book import PatternBook  # noqa: E402
from tradingbot.pattern_trader.data import READY_MIN_BARS, REQUIREMENTS, CsvCandleCache, DataService, PriceService  # noqa: E402
from tradingbot.pattern_trader.detect import ST_BROKEN, ST_CONFIRMED, ST_EXPIRED  # noqa: E402
from tradingbot.pattern_trader.report import MIN_TRADES_FOR_VERDICT, VERDICT_UNDECIDED, build_report  # noqa: E402
from tradingbot.pattern_trader.scheduler import PatternScanner  # noqa: E402
from tradingbot.pattern_trader.strategy import PL_AWAITING, PL_CANCELLED, PL_CLOSED, PL_EXPIRED, PL_MANAGED, PL_REJECTED  # noqa: E402
from tradingbot.pattern_trader.universe import COHORTS, PRIORITY_MAX_AGE_H, cohort_of, discover  # noqa: E402
from tradingbot.risk.killswitch import KillSwitch  # noqa: E402
from tradingbot.risk.profiles import PROFILES  # noqa: E402

M15 = 900_000
H1 = 3_600_000
H4 = 14_400_000
DAY = 86_400_000
T0 = 1_780_000_000_000 // DAY * DAY          # UTC gün sınırına hizalı sabit başlangıç
CLOCK: list[int] = [T0]


def _now() -> int:
    return CLOCK[0]


# ---------------------------------------------------------------- seri kurucular
def _bar(ts, o, h, lo, c, v=100.0):
    return {"timestamp": int(ts), "open": o, "high": h, "low": lo, "close": c, "volume": v}


def _df(rows) -> pd.DataFrame:
    return pd.DataFrame([{k: r[k] for k in ("timestamp", "open", "high", "low", "close", "volume")} for r in rows])


def _trend_series(n, *, start_ts, step, px0, drift, up=True):
    """Düz eğimli seri (trend bağlamı için)."""
    rows, px = [], px0
    for i in range(n):
        o = px
        c = px + drift if up else px - drift
        rows.append(_bar(start_ts + i * step, o, max(o, c) + abs(drift) * 0.6, min(o, c) - abs(drift) * 0.6, c))
        px = c
    return rows


def long_15m(start_ts=T0):
    """Yükseliş → geri çekilme → BOĞA YUTAN (38) → teyit (39) → tetik (40) → hedefe yürüyüş (41+)."""
    rows, px = [], 100.0
    for i in range(30):
        o, c = px, px + 0.4
        rows.append(_bar(start_ts + i * M15, o, c + 0.3, o - 0.3, c))
        px = c
    for i in range(30, 37):
        o, c = px, px - 1.3
        rows.append(_bar(start_ts + i * M15, o, o + 0.3, c - 0.3, c))
        px = c
    rows.append(_bar(start_ts + 37 * M15, px, px + 0.5, px - 1.5, px - 1.0))
    p_open, p_close = px, px - 1.0
    e_open, e_close = p_close - 0.5, p_open + 2.0
    rows.append(_bar(start_ts + 38 * M15, e_open, e_close + 0.5, e_open - 3.0, e_close))            # yutan (derin dip: stop mesafesi gerçekçi)
    rows.append(_bar(start_ts + 39 * M15, e_close, e_close + 0.4, e_close - 0.2, e_close + 0.3))    # teyit
    rows.append(_bar(start_ts + 40 * M15, e_close + 0.3, e_close + 0.9, e_close, e_close + 0.8))    # tetik
    px = e_close + 0.8
    for i in range(41, 60):                                                                          # hedefe yürüyüş
        o, c = px, px + 0.6
        rows.append(_bar(start_ts + i * M15, o, c + 0.2, o - 0.2, c))
        px = c
    return rows


def short_15m(start_ts=T0):
    """Düşüş → yukarı tepki → AYI YUTAN (38) → teyit (39) → tetik (40) → yukarı dönüş: STOP (41+)."""
    rows, px = [], 100.0
    for i in range(30):
        o, c = px, px - 0.4
        rows.append(_bar(start_ts + i * M15, o, o + 0.3, c - 0.3, c))
        px = c
    for i in range(30, 37):
        o, c = px, px + 1.3
        rows.append(_bar(start_ts + i * M15, o, c + 0.3, o - 0.3, c))
        px = c
    rows.append(_bar(start_ts + 37 * M15, px, px + 1.5, px - 0.5, px + 1.0))
    p_open, p_close = px, px + 1.0
    e_open, e_close = p_close + 0.5, p_open - 2.0
    rows.append(_bar(start_ts + 38 * M15, e_open, e_open + 3.0, e_close - 0.5, e_close))            # ayı yutan (derin tepe)
    rows.append(_bar(start_ts + 39 * M15, e_close, e_close + 0.2, e_close - 0.4, e_close - 0.3))    # teyit
    rows.append(_bar(start_ts + 40 * M15, e_close - 0.3, e_close, e_close - 0.9, e_close - 0.8))    # tetik
    px = e_close - 0.8
    for i in range(41, 60):                                                                          # yukarı dönüş → stop
        o, c = px, px + 1.4
        rows.append(_bar(start_ts + i * M15, o, c + 0.3, o - 0.3, c))
        px = c
    return rows


def compression_15m(start_ts=T0, n_pre=24, *, breakout=False):
    """Kısa geçmiş kolu (C): yeterli 15m bar + aralık ~0.9×ATR (ölçülü hareket min R/R'yi geçsin). `breakout=True`
    ise aralığın üstünde kapanan bir bar eklenir (tetik)."""
    rows, px = [], 50.0
    for i in range(n_pre):
        o, c = px, px + (0.25 if i % 2 == 0 else -0.2)
        rows.append(_bar(start_ts + i * M15, o, max(o, c) + 0.65, min(o, c) - 0.65, c))
        px = c
    for i in range(n_pre, n_pre + 6):                                                                # sıkışma: aralık ~0.8×ATR
        rows.append(_bar(start_ts + i * M15, px, px + 0.525, px - 0.525, px))
    if breakout:
        rows.append(_bar(start_ts + (n_pre + 6) * M15, px, px + 1.4, px - 0.05, px + 1.3))
    return rows


# ---------------------------------------------------------------- sağlayıcı / motor kurulumu
def _exinfo(entries):
    """entries: [(raw, base, contractType, status, onboard_ms)] → resmi exchangeInfo satırları."""
    return [{"symbol": raw, "baseAsset": base, "quoteAsset": quote, "contractType": ct, "status": st, "onboardDate": onb,
             "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.001"}, {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                         {"filterType": "MIN_NOTIONAL", "notional": "5"}]}
            for raw, base, quote, ct, st, onb in entries]


def _ticker(raw, px=100.0, vol=50e6):
    return {"symbol": raw, "lastPrice": str(px), "quoteVolume": str(vol), "bidPrice": str(px * 0.9999), "askPrice": str(px * 1.0001), "closeTime": T0}


def _book(raw, px=100.0):
    return {"symbol": raw, "bidPrice": str(px * 0.9999), "askPrice": str(px * 1.0001), "bidQty": "500", "askQty": "500"}


def _depth(px=100.0):
    return {"bids": [[px * (1 - i * 0.0005), 500.0] for i in range(1, 20)], "asks": [[px * (1 + i * 0.0005), 500.0] for i in range(1, 20)]}


def _cfg(tmp_path: Path, **over):
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({"pattern_trader": {"enabled": True, **over}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    return cfg


def _book_obj(cfg) -> PatternBook:
    return PatternBook(cfg, profile=PROFILES["PAPER_RESEARCH"], killswitch=KillSwitch(),
                       filters_cache=FiltersCache(cfg.cache_path / "symbol_filters.json"), section=cfg.v3.pattern_trader)


def _scanner(cfg, provider, book=None, **over):
    feed = MarketFeed([provider], cache_store=CsvCandleCache(cfg.cache_path), clock_ms=_now)
    b = book or _book_obj(cfg)
    pt = cfg.v3.pattern_trader
    sc = PatternScanner(book=b, data=DataService(feed, clock_ms=_now), price=PriceService(provider, feed, clock_ms=_now, ttl_s=0.0),
                        universe_provider=provider, state_path=cfg.state_path, min_quote_volume_24h=float(pt.min_quote_volume_24h),
                        max_spread_pct=float(pt.max_spread_pct), max_symbols_per_cycle=int(over.get("cycle", pt.max_symbols_per_cycle)),
                        universe_refresh_minutes=float(over.get("uni_min", 0.0)), cycle_seconds=float(pt.scan_seconds),
                        clock=lambda: CLOCK[0] / 1000.0, run_id=lambda: "test-run")
    return sc, b


def _provider(candles, exinfo, *, tickers=None, marks=None, **kw):
    """`kw`: MockProvider'a doğrudan geçer (örn. `funding_rates`, `funding_info`, `funding_interval_hours`)."""
    raws = [e["symbol"] for e in exinfo]
    return MockProvider(candles=candles, symbols_info=exinfo, clock_ms=_now, **kw,
                        tickers=tickers or {r: _ticker(r) for r in raws},
                        books={r: _book(r) for r in raws}, depths={r: _depth() for r in raws},
                        marks=marks or {r: {"mark": 100.0, "ts": T0, "funding_rate": 0.0} for r in raws})


def _set_mark(provider, symbol: str, px: float, *, ts: int | None = None):
    from tradingbot.market.providers import to_raw
    provider.marks[to_raw(symbol)] = {"mark": float(px), "ts": int(ts if ts is not None else CLOCK[0]), "funding_rate": 0.0}


@pytest.fixture(autouse=True)
def _reset_clock():
    CLOCK[0] = T0
    yield
    CLOCK[0] = T0


# ====================================================================== 0) defter API sözleşmesi (sessiz gerileme kapanı)
def test_0_both_paper_books_expose_the_shared_book_api():
    """Ortak defter yüzeyi: tur adımı, canlı fiyat tick'i, kapanmış bar uçları, boşluk kaydı, özet yazımı.

    Bu kapı GERÇEK bir sessiz gerilemeyi yakaladı: ortak yardımcı fonksiyon sınıf gövdesinin ORTASINA eklenince
    `StrategyBook.save` modül düzeyine düşmüş ve T2/M2 turu her turda sessizce `AttributeError` ile başarısız olmuştu.
    """
    from tradingbot.strategy_paper import StrategyBook
    for cls, names in ((StrategyBook, ("step", "tick", "apply_closed_bars", "save", "record_gaps", "_state", "_on_closed")),
                       (PatternBook, ("process_symbol", "tick", "apply_closed_bars", "save", "record_gaps", "_state", "_on_closed"))):
        missing = [n for n in names if not callable(getattr(cls, n, None))]
        assert not missing, "%s eksik metot: %s" % (cls.__name__, missing)


# ====================================================================== 1) keşif: resmi metadata, isim listesi DEĞİL
def test_1_universe_is_discovered_from_official_metadata_without_age_or_count_caps():
    now = T0 + 60 * DAY
    entries = [("PERP%dUSDT" % i, "PERP%d" % i, "USDT", "PERPETUAL", "TRADING", now - (400 + i) * DAY) for i in range(14)]
    entries += [("NEWXUSDT", "NEWX", "USDT", "PERPETUAL", "TRADING", now - 2 * DAY),          # 1-7d
                ("FRESHUSDT", "FRESH", "USDT", "PERPETUAL", "TRADING", now - 6 * 3_600_000),  # 0-24h
                ("MIDUSDT", "MID", "USDT", "PERPETUAL", "TRADING", now - 20 * DAY),           # 7-30d
                ("QTRUSDT", "QTR", "USDT", "CURRENT_QUARTER", "TRADING", now - 100 * DAY),    # tarihli kontrat
                ("COINMUSD", "COINM", "USD", "PERPETUAL", "TRADING", now - 100 * DAY),        # COIN-M (quote USD)
                ("SETTLUSDT", "SETTL", "USDT", "PERPETUAL", "SETTLING", now - 100 * DAY),     # işlem dışı
                ("THINUSDT", "THIN", "USDT", "PERPETUAL", "TRADING", now - 100 * DAY),        # düşük hacim
                ("NOAGEUSDT", "NOAGE", "USDT", "PERPETUAL", "TRADING", None)]                 # onboardDate yok
    ex = _exinfo(entries)
    tick = {e["symbol"]: _ticker(e["symbol"]) for e in ex}
    tick["THINUSDT"] = _ticker("THINUSDT", vol=1_000.0)
    p = MockProvider(symbols_info=ex, tickers=tick, books={e["symbol"]: _book(e["symbol"]) for e in ex}, clock_ms=lambda: now)
    u = discover(p, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15)
    e = u["entries"]
    assert u["counts"]["discovered"] == len(entries) == 22 and u["counts"]["eligible"] == 18, u["counts"]
    assert e["QTR/USDT"]["reason"] == "CONTRACT_CURRENT_QUARTER" and e["COINM/USD"]["reason"] == "QUOTE_USD"
    assert e["SETTL/USDT"]["reason"] == "STATUS_SETTLING" and e["THIN/USDT"]["reason"] == "LOW_VOLUME"
    assert all(x["market"] == "USDM_PERP" for x in e.values())
    # yaş = futures ilk işlem zamanı; token doğumu / spot listeleme AYRI ve bilinmiyor
    assert e["NEWX/USDT"]["cohort"] == "1-7d" and e["FRESH/USDT"]["cohort"] == "0-24h" and e["MID/USDT"]["cohort"] == "7-30d"
    assert e["PERP0/USDT"]["cohort"] == "90d+" and e["NOAGE/USDT"]["cohort"] == "UNKNOWN" and e["NOAGE/USDT"]["priority"] is False
    assert all(e[s]["token_birth_ms"] is None and e[s]["spot_listing_ms"] is None for s in ("NEWX/USDT", "FRESH/USDT"))
    assert e["NEWX/USDT"]["futures_first_trade_ms"] == now - 2 * DAY and abs(e["NEWX/USDT"]["age_h"] - 48.0) < 0.01
    # 60 günlük yaş engeli ve 200 sembol tavanı MİRAS DEĞİL
    assert u["policy"]["min_listing_age_days"] == 0 and u["policy"]["max_symbols"] is None
    assert e["FRESH/USDT"]["eligible"] is True and e["NEWX/USDT"]["priority"] is True and e["PERP0/USDT"]["priority"] is False
    assert u["counts"]["priority"] == 3
    # kohortlar örtüşmez ve sınırlar tam saat
    assert [cohort_of(h) for h in (0.0, 24.0, 24.1, 168.0, 168.1, 720.0, 720.1, 2160.0, 2160.1)] == \
        ["0-24h", "0-24h", "1-7d", "1-7d", "7-30d", "7-30d", "30-90d", "30-90d", "90d+"]
    assert cohort_of(None) == "UNKNOWN" and PRIORITY_MAX_AGE_H == 720.0 and len(COHORTS) == 5
    # ikinci tur: yeni listeleme / delist / durum olayları
    ex2 = [x for x in ex if x["symbol"] != "NEWXUSDT"] + _exinfo([("JUSTUSDT", "JUST", "USDT", "PERPETUAL", "TRADING", now - 3_600_000)])
    p2 = MockProvider(symbols_info=ex2, tickers={**tick, "JUSTUSDT": _ticker("JUSTUSDT")},
                      books={x["symbol"]: _book(x["symbol"]) for x in ex2}, clock_ms=lambda: now)
    u2 = discover(p2, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u)
    assert [c["symbol"] for c in u2["changes"]["new_listings"]] == ["JUST/USDT"]
    assert [c["symbol"] for c in u2["changes"]["delisted"]] == ["NEWX/USDT"]
    assert u2["entries"]["PERP0/USDT"]["first_seen_at"] == u["entries"]["PERP0/USDT"]["first_seen_at"], "ilk görülme korunur"
    # metadata alınamazsa eski evren KORUNUR (uydurma yok)
    boom = MockProvider(symbols_info=ex, clock_ms=lambda: now, fail={"exchange_info"})
    u3 = discover(boom, now_ms=now, min_quote_volume_24h=5e6, max_spread_pct=0.15, previous=u2)
    assert u3["ok"] is False and set(u3["entries"]) == set(u2["entries"]) and "exchange_info" in u3["error"]


# ====================================================================== 2) yeni listeleme: dilim başına hazır/yetersiz; C kolu kısa geçmişte çalışır
def test_2_new_listing_readiness_is_per_timeframe_and_short_history_family_still_trades(tmp_path: Path):
    start = T0 - 40 * M15
    CLOCK[0] = start + 30 * M15
    rows15 = compression_15m(start_ts=start, n_pre=24)   # kırılım YOK: planlar TETİK BEKLER
    rows1h = _trend_series(6, start_ts=start - 6 * H1, step=H1, px0=50.0, drift=0.1)      # 1h: yetersiz
    rows4h = _trend_series(2, start_ts=start - 2 * H4, step=H4, px0=50.0, drift=0.2)      # 4h: yetersiz
    ex = _exinfo([("NEWXUSDT", "NEWX", "USDT", "PERPETUAL", "TRADING", CLOCK[0] - 2 * DAY)])
    p = _provider({"NEWX/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex,
                  tickers={"NEWXUSDT": _ticker("NEWXUSDT", px=51.0)}, marks={"NEWXUSDT": {"mark": 51.1, "ts": CLOCK[0], "funding_rate": 0.0}})
    cfg = _cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    rep = sc.scan_cycle(now_ms=CLOCK[0])
    assert rep["totals"]["scanned"] == 1
    scan = book.symbol_scans["NEWX/USDT"]
    assert scan["n_15m"] >= READY_MIN_BARS and scan["n_1h"] == len(rows1h) and scan["n_4h"] == len(rows4h)
    # hazır/yetersiz AYRI; eksik üst dilim uydurulmaz
    doc = json.loads((cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    assert doc["policy"]["requirements"] == REQUIREMENTS
    assert scan["trend_4h"] == "UNKNOWN" and scan["n_zones_1h"] == 0, "4h/1h yetersiz: bağlam UYDURULMAZ"
    # 15m şekil görülüyor (üst dilim eksik diye susturulmuyor) ve C kolu plan üretiyor; A/B gerekçeli atlanıyor
    assert book.counters["findings"] > 0
    fams = {pl["family"] for pl in book.plans.values()}
    assert "C_COMPRESSION_BREAKOUT" in fams, [pl["family"] for pl in book.plans.values()]
    assert {pl["side"] for pl in book.plans.values() if pl["family"] == "C_COMPRESSION_BREAKOUT"} == {"LONG", "SHORT"}
    reasons = {s.get("reason") for s in rep["details"][0].get("rejected", [])} if rep["details"] else set()
    assert not reasons
    # 60/210 günlük gizli engel YOK: 2 günlük coin plan üretebildi
    assert all(pl["cohort"] == "1-7d" and pl["age_h_at_plan"] is not None for pl in book.plans.values())
    assert all(pl["version"] == doc["protocol_version"] for pl in book.plans.values())


# ====================================================================== 3) LONG: tetik → risk → giriş → yönetim → HEDEF kapanışı
def test_3_long_chain_opens_through_the_shared_applier_and_closes_at_target(tmp_path: Path):
    rows15 = long_15m()
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2)
    ex = _exinfo([("LNGUSDT", "LNG", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])
    p = _provider({"LNG/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg = _cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    trig_bar = rows15[40]
    # --- bar 38 kapandı: bulgu var, teyit YOK, plan YOK
    CLOCK[0] = T0 + 39 * M15
    _set_mark(p, "LNG/USDT", rows15[38]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert book.counters["findings"] > 0 and book.counters["plans"] == 0 and not book.ledger.positions
    # --- bar 39 kapandı: TEYİT + plan (koşullu LONG); hâlâ emir YOK
    CLOCK[0] = T0 + 40 * M15
    _set_mark(p, "LNG/USDT", rows15[39]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    pls = [pl for pl in book.plans.values() if pl["family"] == "A_TREND_PULLBACK" and pl["side"] == "LONG"]
    assert len(pls) == 1 and pls[0]["status"] == PL_AWAITING and not book.ledger.positions
    plan = pls[0]
    assert plan["trigger"]["level"] == pytest.approx(rows15[38]["high"]) and plan["stop"] < plan["trigger"]["level"] < plan["target"]
    assert plan["rr_after_cost"] >= float(cfg.v3.pattern_trader.min_rr_after_cost) and plan["p_win"] is None
    assert "kapanışı" in plan["trigger"]["text_tr"] and plan["target_source"] in ("opposing_1h_zone", "fallback_rr", "measured_move")
    assert any(f["status"] == ST_CONFIRMED for f in book.findings.values())
    # --- bar 40 kapandı: TETİK → risk → GİRİŞ
    CLOCK[0] = T0 + 41 * M15
    _set_mark(p, "LNG/USDT", trig_bar["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert "LNG/USDT" in book.ledger.positions, (plan["status"], plan.get("reasons"))
    pos = book.ledger.positions["LNG/USDT"]
    assert pos.side.value == "LONG" and float(pos.stop) == pytest.approx(plan["stop"]) and [float(t) for t in pos.targets] == [pytest.approx(plan["target"])]
    assert plan["status"] == PL_MANAGED and plan["position_id"] == pos.id and plan["triggered_at_ms"] == trig_bar["timestamp"] + M15
    assert float(pos.entry_avg) == pytest.approx(trig_bar["close"], rel=2e-3), "fill TETİK kapanışından SONRAKİ doğrulanmış fiyat"
    assert pos.meta["plan_id"] == plan["plan_id"] and pos.features["family"] == "A_TREND_PULLBACK" and pos.meta["price_source"]["kind"] == "usdm_perp_mark"
    assert pos.meta["cohort"] == "90d+" and float(pos.qty * pos.entry_avg) <= 30.001, "tek coin risk tavanı (%30) uygulandı"
    assert plan["liquidity_at_trigger"]["spread_pct"] is not None and plan["rr_at_entry"]["after_cost"] >= 1.5
    # --- fiyat hedefe yürüyor: pozisyon HEDEFTE kapanır, kayıt net sonuç + neden taşır
    CLOCK[0] = T0 + 50 * M15
    _set_mark(p, "LNG/USDT", float(plan["target"]) + 0.5)
    recs = sc.exit_check()
    assert not book.ledger.positions and len(recs) == 1
    h = book.ledger.history_dicts()[-1]
    assert float(h["exit_price"]) >= float(plan["target"]) and float(h["net_pnl"]) > 0
    assert h["exit_reason"].startswith("hedef") or "TP" in str(h["exit_reason"]).upper(), h["exit_reason"]
    assert plan["status"] == PL_CLOSED and plan["net_r"] > 0
    assert (h.get("features") or {}).get("plan_id") == plan["plan_id"] and (h.get("features") or {}).get("cohort") == "90d+"
    doc = json.loads((cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    assert doc["counters"]["opened"] == 1 and doc["counters"]["closed"] == 1 and doc["mode"] == "PAPER"
    assert doc["summary"]["equity_mtm"] > 100.0 and doc["policy"]["p_win"] == "ÖLÇÜLMEDİ"


# ====================================================================== 4) SHORT: tetik → giriş → STOP kapanışı
def test_4_short_chain_opens_and_protective_stop_closes_it(tmp_path: Path):
    rows15 = short_15m()
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=130.0, drift=1.0, up=False)
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=105.0, drift=0.2, up=False)
    ex = _exinfo([("SHRTUSDT", "SHRT", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])
    p = _provider({"SHRT/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg = _cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    for i in (39, 40, 41):
        CLOCK[0] = T0 + i * M15
        _set_mark(p, "SHRT/USDT", rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    assert "SHRT/USDT" in book.ledger.positions, {k: (v["status"], v.get("reasons")) for k, v in book.plans.items()}
    pos = book.ledger.positions["SHRT/USDT"]
    plan = next(pl for pl in book.plans.values() if pl.get("position_id") == pos.id)
    assert pos.side.value == "SHORT" and plan["side"] == "SHORT" and float(pos.stop) > float(pos.entry_avg) > float(pos.targets[0])
    assert plan["trigger"]["rule"] == "close_below" and "altında" in plan["trigger"]["text_tr"]
    # fiyat stop'un üstüne çıkar → koruyucu stop
    CLOCK[0] = T0 + 45 * M15
    _set_mark(p, "SHRT/USDT", float(pos.stop) * 1.01)
    sc.exit_check()
    assert not book.ledger.positions
    h = book.ledger.history_dicts()[-1]
    assert h["side"] == "SHORT" and "stop" in str(h["exit_reason"]).lower() and float(h["net_pnl"]) < 0
    assert plan["status"] == PL_CLOSED and book.cooldown_until.get("SHRT/USDT", 0) > CLOCK[0], "zarardan sonra soğuma (tersleme YOK)"


# ====================================================================== 5) emir AÇILMAYAN yollar: tetiklenmeyen/bozulan/süresi dolan/riske sığmayan + tekrar/restart/kesinti
def test_5_untriggered_broken_expired_and_rejected_plans_never_open_an_order(tmp_path: Path):
    rows15 = long_15m()
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2)
    ex = _exinfo([("LNGUSDT", "LNG", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])
    # (a) tetik gelmeden süre dolar: 39. bardan sonra fiyat yatay → 8 bar sonra EXPIRED, emir YOK
    flat = rows15[:40] + [_bar(T0 + i * M15, rows15[39]["close"], rows15[39]["close"] + 0.1, rows15[39]["close"] - 0.1, rows15[39]["close"]) for i in range(40, 52)]
    p = _provider({"LNG/USDT": {"15m": _df(flat), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg = _cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    CLOCK[0] = T0 + 40 * M15
    _set_mark(p, "LNG/USDT", flat[39]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert book.counters["plans"] >= 1
    CLOCK[0] = T0 + 52 * M15
    _set_mark(p, "LNG/USDT", flat[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert not book.ledger.positions and book.counters["opened"] == 0
    assert any(pl["status"] == PL_EXPIRED for pl in book.plans.values()) and book.counters["expired"] >= 1
    # (b) bozulma: teyitten sonra kapanış geçersizlik seviyesinin altına iner → BROKEN, emir YOK
    broken = rows15[:40] + [_bar(T0 + 40 * M15, rows15[39]["close"], rows15[39]["close"] + 0.1, rows15[38]["low"] - 2, rows15[38]["low"] - 1.5)]
    p2 = _provider({"LNG/USDT": {"15m": _df(broken), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg2 = _cfg(tmp_path / "b")
    sc2, book2 = _scanner(cfg2, p2)
    CLOCK[0] = T0 + 40 * M15
    _set_mark(p2, "LNG/USDT", broken[39]["close"])
    sc2.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15
    _set_mark(p2, "LNG/USDT", broken[40]["close"])
    sc2.scan_cycle(now_ms=CLOCK[0])
    assert not book2.ledger.positions and book2.counters["broken"] >= 1
    assert any(f["status"] == ST_BROKEN for f in book2.findings.values())
    # (c) veri kesintisi: doğrulanmış fiyat yok → giriş yok, plan TETİKLENMİŞ bekler, uydurma fill YOK
    p3 = _provider({"LNG/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg3 = _cfg(tmp_path / "c")
    sc3, book3 = _scanner(cfg3, p3)
    for i in (39, 40):
        CLOCK[0] = T0 + i * M15
        _set_mark(p3, "LNG/USDT", rows15[i - 1]["close"])
        sc3.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15
    _set_mark(p3, "LNG/USDT", rows15[40]["close"], ts=CLOCK[0] - 3_600_000)      # fiyat 1 saat eski → BAYAT
    sc3.scan_cycle(now_ms=CLOCK[0])
    assert not book3.ledger.positions and book3.data_gaps.get("LNG/USDT", {}).get("reason") == "STALE_FUTURES_PRICE"
    pl3 = next(pl for pl in book3.plans.values() if pl["family"] == "A_TREND_PULLBACK")
    assert pl3["status"] == "TRIGGERED" and pl3["position_id"] is None
    # fiyat gelince aynı plan açılır (tekrar denenir) ve YİNELENMEZ
    CLOCK[0] = T0 + 42 * M15
    _set_mark(p3, "LNG/USDT", rows15[40]["close"])
    sc3.scan_cycle(now_ms=CLOCK[0])
    assert "LNG/USDT" in book3.ledger.positions and book3.counters["opened"] == 1
    sc3.scan_cycle(now_ms=CLOCK[0])
    sc3.scan_cycle(now_ms=CLOCK[0])
    assert book3.counters["opened"] == 1 and len(book3.ledger.positions) == 1, "tekrar tarama ikinci emir AÇMAZ"
    # (d) yeniden başlatma: plan/bulgu/pozisyon ve sayaçlar korunur, yinelenen emir yok
    book3.save({"LNG/USDT": float(rows15[40]["close"])}, __import__("tradingbot.core", fromlist=["utc_now"]).utc_now())
    reborn = _book_obj(cfg3)
    assert set(reborn.ledger.positions) == {"LNG/USDT"} and reborn.counters["opened"] == 1
    assert reborn.plans[pl3["plan_id"]]["status"] == PL_MANAGED and len(reborn.findings) == len(book3.findings)
    sc4, _ = _scanner(cfg3, p3, book=reborn)
    sc4.scan_cycle(now_ms=CLOCK[0])
    assert reborn.counters["opened"] == 1
    # (e) risk sığmıyor: kill-switch açıkken tetiklenen plan REDDEDİLİR (emir yok, gerekçe kayıtta)
    p5 = _provider({"LNG/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg5 = _cfg(tmp_path / "e")
    ks = KillSwitch()
    ks.trip("TEST_HALT") if hasattr(ks, "trip") else setattr(ks, "state", "TRIPPED")
    b5 = PatternBook(cfg5, profile=PROFILES["PAPER_RESEARCH"], killswitch=ks, filters_cache=FiltersCache(cfg5.cache_path / "f.json"), section=cfg5.v3.pattern_trader)
    sc5, _ = _scanner(cfg5, p5, book=b5)
    for i in (39, 40, 41):
        CLOCK[0] = T0 + i * M15
        _set_mark(p5, "LNG/USDT", rows15[i - 1]["close"])
        sc5.scan_cycle(now_ms=CLOCK[0])
    assert not b5.ledger.positions and b5.counters["opened"] == 0
    assert any(pl["status"] == PL_REJECTED for pl in b5.plans.values()) and b5.rejections, b5.rejections
    assert "KILL_SWITCH_ACTIVE" in "".join(b5.rejections)


# ====================================================================== 6) ileriye bakış yok: gelecek barlar geçmiş kararı DEĞİŞTİRMEZ
def test_6_adding_future_bars_does_not_change_a_past_decision(tmp_path: Path):
    rows15 = long_15m()
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2)
    ex = _exinfo([("LNGUSDT", "LNG", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])

    def run(rows, upto_bar):
        cfg = _cfg(tmp_path / ("r%d" % upto_bar))
        p = _provider({"LNG/USDT": {"15m": _df(rows), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
        sc, book = _scanner(cfg, p)
        CLOCK[0] = T0 + upto_bar * M15
        _set_mark(p, "LNG/USDT", rows[upto_bar - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
        return book

    # aynı karar anı (bar 39 kapanışı), biri kısa seri biri gelecek barlarla dolu → AYNI plan kimliği ve seviyeleri
    short_rows, full_rows = rows15[:40], rows15
    a, b = run(short_rows, 40), run(full_rows, 40)
    pa = {k: (v["family"], v["side"], v["trigger"]["level"], v["stop"], v["target"], v["status"]) for k, v in a.plans.items()}
    pb = {k: (v["family"], v["side"], v["trigger"]["level"], v["stop"], v["target"], v["status"]) for k, v in b.plans.items()}
    assert pa == pb and pa, (pa, pb)
    assert {f["finding_id"]: f["status"] for f in a.findings.values()} == {f["finding_id"]: f["status"] for f in b.findings.values()}
    assert not a.ledger.positions and not b.ledger.positions, "bar 40 HENÜZ kapanmadı: tetik yok"
    # kapanmamış bar hiçbir bulguya girmez
    for f in b.findings.values():
        assert f["bar_ts"] + M15 <= T0 + 40 * M15 and all(t + M15 <= T0 + 40 * M15 for t in f["bars_used"])
    # aynı kayıt tekrar okunduğunda değişmez (idempotent)
    before = json.dumps(pb, sort_keys=True, default=str)
    run(full_rows, 40)
    assert json.dumps(pb, sort_keys=True, default=str) == before


# ====================================================================== 7) kapsama / öncelik / dönen kuyruk; çıkış izleyicisi taramadan BAĞIMSIZ
def test_7_rotation_priority_and_coverage_do_not_block_the_exit_monitor(tmp_path: Path):
    CLOCK[0] = T0 + 40 * M15
    rows15 = long_15m()
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2)
    syms = [("S%02dUSDT" % i, "S%02d" % i, "USDT", "PERPETUAL", "TRADING", T0 - (500 - i) * DAY) for i in range(12)]
    syms += [("NEW1USDT", "NEW1", "USDT", "PERPETUAL", "TRADING", CLOCK[0] - 3 * DAY),
             ("NEW2USDT", "NEW2", "USDT", "PERPETUAL", "TRADING", CLOCK[0] - 10 * DAY)]
    ex = _exinfo(syms)
    candles = {"%s/USDT" % b: {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)} for _, b, _q, _c, _s, _o in syms}
    tick = {r: _ticker(r, vol=50e6 - i * 1e6) for i, (r, *_rest) in enumerate(syms)}
    p = _provider(candles, ex, tickers=tick)
    cfg = _cfg(tmp_path, max_symbols_per_cycle=5)
    sc, book = _scanner(cfg, p, cycle=5)
    for s in candles:
        _set_mark(p, s, rows15[39]["close"])
    r1 = sc.scan_cycle(now_ms=CLOCK[0])
    assert r1["batch"] == 5 and r1["queue_depth"] == 14
    # öncelik: yeni listelenenler (<=30g) yerleşik hacim sıralamasının ÖNÜNDE
    first = [s for s, g in sc.queue(now_ms=CLOCK[0])][:2]
    assert set(first) == {"NEW1/USDT", "NEW2/USDT"}, first
    # dönen kuyruk: art arda turlarda evren tamamlanır — hiçbir sembol sonsuza kadar taranmadan KALMAZ
    for _ in range(5):
        sc.scan_cycle(now_ms=CLOCK[0])
    st = sc.status()
    assert st["coverage"]["eligible"] == 14 and st["coverage"]["never_scanned"] == 0 and st["coverage"]["scanned_at_least_once"] == 14
    assert st["universe"]["counts"]["priority"] == 2 and st["last_cycle"]["by_group"]
    assert (cfg.state_path / "pattern_scan.json").exists() and (cfg.state_path / "pattern_universe.json").exists()
    # açık pozisyon → sonraki turda 'open_position' grubu ilk sırada; exit_check kuyruktan BAĞIMSIZ çalışır
    CLOCK[0] = T0 + 41 * M15
    for s in candles:
        _set_mark(p, s, rows15[40]["close"])
    for _ in range(3):
        sc.scan_cycle(now_ms=CLOCK[0])
    assert book.ledger.positions, "tetik turlarında en az bir pozisyon açılmalı"
    opened = sorted(book.ledger.positions)
    assert [g for s, g in sc.queue(now_ms=CLOCK[0])][:len(opened)] == ["open_position"] * len(opened)
    calls = {"n": 0}
    orig = sc.data.bars

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)
    sc.data.bars = counting
    sc.exit_check()
    assert calls["n"] == 0, "çıkış izleyicisi mum indirmez/taramayı BEKLEMEZ"
    assert book.counters["scans"] >= 14


# ====================================================================== 8) rapor: sıklık vs sonuç, kohort, belirsiz hüküm
def test_8_report_separates_frequency_from_outcome_and_refuses_a_verdict_on_a_small_sample(tmp_path: Path):
    cfg = _cfg(tmp_path)
    book = _book_obj(cfg)
    book.cohort_stats = {"1-7d": {"scans": 10, "bars_15m_scanned": 2000, "findings": 40, "confirmed": 12, "plans": 6, "triggered": 3, "opened": 2},
                         "90d+": {"scans": 40, "bars_15m_scanned": 20000, "findings": 120, "confirmed": 30, "plans": 20, "triggered": 8, "opened": 5}}
    book.findings = {"a%d" % i: {"finding_id": "a%d" % i, "cohort": "1-7d", "status": "CONFIRMED" if i < 12 else "UNCONFIRMED"} for i in range(40)}
    book.findings.update({"b%d" % i: {"finding_id": "b%d" % i, "cohort": "90d+", "status": "CONFIRMED" if i < 30 else "FOUND"} for i in range(120)})
    hist = [{"symbol": "N/USDT", "side": "LONG", "r_multiple": r, "net_pnl": r * 2, "gross_pnl": r * 2 + 0.1, "fees": 0.08, "funding": 0.02,
             "exit_reason": "hedef1" if r > 0 else "stop", "features": {"cohort": "1-7d", "family": "A_TREND_PULLBACK", "plan_id": "p%d" % i}}
            for i, r in enumerate([1.8, -1.0, -1.0, 2.1, -1.0])]
    hist += [{"symbol": "E/USDT", "side": "SHORT", "r_multiple": r, "net_pnl": r * 2, "gross_pnl": r * 2 + 0.1, "fees": 0.08, "funding": 0.0,
              "exit_reason": "stop" if r < 0 else "hedef1", "features": {"cohort": "90d+", "family": "B_LEVEL_REVERSAL", "plan_id": "q%d" % i}}
             for i, r in enumerate([-1.0, -1.0, 1.6])]
    rep = build_report({"cohort_stats": book.cohort_stats, "positions": {"OPEN/USDT": {"cohort": "1-7d"}}},
                       history=hist, findings=book.findings, plans={}, scan={"coverage": {"eligible": 14}})
    # (1) sıklık ve (2) sonuç AYRI
    q1 = rep["new_vs_established"]["question_1_frequency"]
    assert q1["new_listings"]["findings_per_1k_bars"] == 20.0 and q1["established"]["findings_per_1k_bars"] == 6.0
    q2 = rep["new_vs_established"]["question_2_outcome"]
    assert q2["new_listings"]["outcome"]["n"] == 5 and q2["established"]["outcome"]["n"] == 3
    # az gözlem → hüküm YOK (sıklık yüksek olsa bile "yeni coin avantajı" İLAN EDİLMEZ)
    assert rep["new_vs_established"]["verdict"] == VERDICT_UNDECIDED and rep["totals"]["verdict"] == VERDICT_UNDECIDED
    assert MIN_TRADES_FOR_VERDICT == 30 and len(rep["new_vs_established"]["limitations_tr"]) >= 4
    # maliyet sonrası ölçütler ve belirsizlik
    o = rep["totals"]["outcome"]
    assert o["n"] == 8 and o["mean_r"] == pytest.approx(sum(t["r_multiple"] for t in hist) / 8, abs=1e-4)
    assert o["profit_factor"] is not None and o["max_drawdown_r"] < 0 and o["ci95_mean_r"] and len(o["ci95_mean_r"]) == 2
    assert rep["totals"]["costs"]["cost_usdt"] > 0 and rep["totals"]["open_positions"] == 1
    assert all(r["cohort"] for r in rep["by_cohort"]) and {r["cohort"] for r in rep["by_cohort"]} == {c[0] for c in COHORTS} | {"UNKNOWN"}
    assert rep["exit_reasons"] and any(f["family"] == "A_TREND_PULLBACK" for f in rep["by_family"])
    # kapanmamış pozisyon SONUÇ bölümüne girmez
    assert rep["totals"]["closed_trades"] == 8
    # bootstrap deterministik
    assert build_report({"cohort_stats": book.cohort_stats}, history=hist)["totals"]["outcome"]["ci95_mean_r"] == o["ci95_mean_r"]


# ====================================================================== 9) motor bağı: tur tarayıcıyı başlatır, çıkış izleyicisi defteri tick'ler, rapor yazılır
def test_9_engine_starts_the_scanner_in_the_background_and_writes_the_report(tmp_path: Path, monkeypatch):
    from test_engine_v3 import _engine
    from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile
    ex = _exinfo([("LNGUSDT", "LNG", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])
    rows15, rows1h, rows4h = long_15m(), _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2), _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    p = _provider({"LNG/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    ov = _profile(6.0) | {"pattern_trader": {"enabled": True, "scan_seconds": 3600.0, "universe_refresh_minutes": 0.0},
                          "chart_analysis": {"enabled": False}, "news": {"enabled": False}}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: p)
    assert eng.pattern_book is not None and eng.pattern_scanner is None
    started: list[bool] = []
    monkeypatch.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: started.append(True))
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert started == [True] and eng.pattern_scanner is not None, "tarayıcı ARKA PLANDA başlatıldı (tur beklemez)"
    rep = json.loads((eng.cfg.state_path / "pattern_report.json").read_text(encoding="utf-8"))
    assert rep["schema_version"] == "pattern_report_v1" and rep["totals"]["closed_trades"] == 0
    assert rep["totals"]["verdict"] == VERDICT_UNDECIDED and "kârlılık kanıtı YOK" in rep["status_tr"]
    doc = json.loads((eng.cfg.state_path / "pattern_trader.json").read_text(encoding="utf-8"))
    assert doc["key"] == "pattern_trader" and doc["mode"] == "PAPER" and doc["summary"]["equity_mtm"] == 100.0
    # ana bot ve T2/M2 defterleri ETKİLENMEDİ
    assert not eng.ledger2.positions and not eng.ledger2.history_dicts()
    # çıkış izleyicisi formasyon defterini de kapsar ve pozisyon yokken ağ isteği YAPMAZ
    CLOCK[0] = T0 + 41 * M15
    sc = eng.pattern_scanner
    sc.price.provider = p
    eng._pattern_exit_check()
    assert sc.price.stats["mark_calls"] == 0
    # açık pozisyon varken: doğrulanmış mark ile tick, boşlukta uydurma fill YOK
    b = eng.pattern_book
    from decimal import Decimal
    from tradingbot.accounting import SizeSpec
    from tradingbot.accounting.models import AmountType
    pos = b.ledger.open("LNG/USDT", "LONG", 100.0, SizeSpec(Decimal("20"), AmountType.NOTIONAL, 1), stop=95.0, targets=[110.0], now=__import__("tradingbot.core", fromlist=["utc_now"]).utc_now())
    assert pos is not None
    import time as _t
    _set_mark(p, "LNG/USDT", 94.0, ts=int(_t.time() * 1000))       # motor gerçek saatle çalışır: fiyat GÜNCEL olmalı
    eng._pattern_exit_check()
    assert not b.ledger.positions and "stop" in str(b.ledger.history_dicts()[-1]["exit_reason"]).lower()

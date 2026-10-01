# -*- coding: utf-8 -*-
"""BULGU 2 — YENİ LİSTELENEN SÖZLEŞME UYDURMA TICK/STEP İLE AÇILIYOR.

`universe.discover()` resmi `exchangeInfo` filtrelerini (tick_size/step_size/min_qty/min_notional) evren kaydına
YAZAR, ama giriş yolu (`PatternBook._try_open` → `apply_action(..., filters=self.filters_cache.get(...))`) bu
kaydı HİÇ kullanmaz. `FiltersCache` iki yenileme arasında listelenen sözleşmeyi bilmediği için
`default_filters()` döner (price_tick 0.01 / qty_step 0.001 / source "default") ve emir gerçek borsa kuralında
var olmayan bir fiyat-miktar ızgarasına kuantize edilir.

ETİKET: gerçek `PatternScanner`/`PatternBook`/`FuturesLedgerV2`/`apply_action` kodu çalışır; AĞ yerine
`market.providers.MockProvider` kullanılır (tests/test_pattern_trader_v1.py ile aynı düzen). SENTETİK sözleşme:
gerçek bir coin hakkında iddia DEĞİLDİR, kural yolunun davranış kanıtıdır.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.accounting import MarketType  # noqa: E402
from tradingbot.accounting.filters import FiltersCache, refresh_futures_filters  # noqa: E402
from tradingbot.core import iso  # noqa: E402
from tradingbot.pattern_trader.book import PatternBook  # noqa: E402
from tradingbot.risk.killswitch import KillSwitch  # noqa: E402
from tradingbot.risk.profiles import PROFILES  # noqa: E402

from test_pattern_trader_v1 import (CLOCK, DAY, H1, H4, M15, T0, _cfg, _df, _provider, _scanner,  # noqa: E402
                                    _set_mark, _ticker, _trend_series, long_15m)

# Yeni listelenen sözleşmenin RESMİ kuralları: kuruş-altı tick, TAM SAYI lot.
OFFICIAL_TICK = "0.00001"
OFFICIAL_STEP = "1"
SYMBOL, RAW = "NEWL/USDT", "NEWLUSDT"
SCALE = 0.00117              # 100'lük sentetik seriyi ~0,12 USDT'lik bir sözleşmeye ölçekler
MARK_AT_TRIGGER = 0.12345    # tetik seviyesinin hemen üstünde doğrulanmış perp mark


@pytest.fixture(autouse=True)
def _reset_clock():
    CLOCK[0] = T0
    yield
    CLOCK[0] = T0


def _exinfo_new_listing(onboard_ms: int) -> list[dict]:
    """Ara yenileme olmadan listelenmiş bir sözleşmenin resmi exchangeInfo satırı."""
    return [{"symbol": RAW, "baseAsset": "NEWL", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING",
             "onboardDate": onboard_ms,
             "filters": [{"filterType": "PRICE_FILTER", "tickSize": OFFICIAL_TICK},
                         {"filterType": "LOT_SIZE", "stepSize": OFFICIAL_STEP, "minQty": OFFICIAL_STEP},
                         {"filterType": "MIN_NOTIONAL", "notional": "5"}]}]


def _scaled(rows: list[dict], k: float = SCALE) -> list[dict]:
    """Aynı geometri, düşük fiyat seviyesi (R/R ölçekten bağımsızdır)."""
    return [dict(r, open=r["open"] * k, high=r["high"] * k, low=r["low"] * k, close=r["close"] * k) for r in rows]


def _make(tmp_path: Path, cache: FiltersCache | None = None):
    """Gerçek tarayıcı + gerçek defter; yalnız ağ sahte. `cache=None` → boş önbellek (sembol BİLİNMİYOR)."""
    rows15 = _scaled(long_15m())
    rows1h = _scaled(_trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2))
    rows4h = _scaled(_trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0))
    ex = _exinfo_new_listing(T0 - 2 * DAY)
    p = _provider({SYMBOL: {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex,
                  tickers={RAW: _ticker(RAW)})
    cfg = _cfg(tmp_path)
    fc = cache if cache is not None else FiltersCache(cfg.cache_path / "symbol_filters.json")
    book = PatternBook(cfg, profile=PROFILES["PAPER_RESEARCH"], killswitch=KillSwitch(), filters_cache=fc,
                       section=cfg.v3.pattern_trader)
    sc, _ = _scanner(cfg, p, book=book)
    return sc, book, p, rows15, cfg


def _run_to_entry(sc, book, p, rows15):
    """39 → 40 → 41: bulgu/teyit → plan → tetik → giriş. Son turda mark SABİT (kuantizasyon ölçülebilsin)."""
    for i in (39, 40):
        CLOCK[0] = T0 + i * M15
        _set_mark(p, SYMBOL, rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15
    _set_mark(p, SYMBOL, MARK_AT_TRIGGER)
    sc.scan_cycle(now_ms=CLOCK[0])
    return book.ledger.positions.get(SYMBOL)


# ====================================================================== KUSUR: resmi filtreler keşfedilir, giriş yolu KULLANMAZ
def test_defect_new_listing_must_open_with_the_discovered_official_filters(tmp_path: Path):
    """İki yenileme arasında listelenen sözleşme, keşfedilmiş RESMİ kuralıyla açılmalıdır.

    ÖLÇÜLEN (onarımdan önce): kaynak "default", price_tick 0,01, qty_step 0,001 → fill 0,13 (mark 0,12345'ten
    %5,3 sapma) ve miktar 213,78 (resmi step_size=1 ile OLANAKSIZ). Aynı kural önbellekte varken yol doğrudur
    (karşı örnek): fill 0,12349, miktar 225.
    """
    sc, book, p, rows15, _cfg_ = _make(tmp_path)
    assert book.filters_cache.has(SYMBOL, MarketType.USDM_PERP) is False, "kurulum: önbellek sembolü BİLMEMELİ"
    pos = _run_to_entry(sc, book, p, rows15)
    assert pos is not None, {k: (v["status"], v.get("reasons")) for k, v in book.plans.items()}

    # (1) resmi kurallar KEŞFEDİLDİ: evren kaydı doğru tick/step taşıyor — ve ARTIK giriş yolunda kullanılıyor
    ue = sc.universe["entries"][SYMBOL]
    assert ue["filters"]["tick_size"] == OFFICIAL_TICK and ue["filters"]["step_size"] == OFFICIAL_STEP
    # PAYLAŞILAN önbellek KİRLETİLMEZ: ana botun resmî yenileme damgası (`verified_at`) bozulmaz ve kilitsiz
    # nesneye tarayıcı iş parçacığından yazılmaz; tur içi tekrar defterin KENDİ belleğinden karşılanır.
    assert book.filters_cache.has(SYMBOL, MarketType.USDM_PERP) is False, "paylaşılan önbelleğe YAZILMAMALI"
    assert book.filters_cache.verified_at == "", "ana botun yenileme damgası dokunulmadan kalmalı"
    assert SYMBOL in book._filters_memo, "tur içi tekrar defterin kendi belleğinden karşılanmalı"
    ev = next(pl for pl in book.plans.values() if pl.get("position_id") == pos.id)["filters_used"]
    assert ev["origin"] == "universe_entry" and ev["source"] == "binance_api:universe", ev
    assert ev["verified_at"] and float(ev["price_tick"]) == float(OFFICIAL_TICK), ev

    used = pos.meta["filters"]
    ctx = {"source": used["source"], "price_tick": used["price_tick"], "qty_step": used["qty_step"],
           "fill": float(pos.entry_avg), "qty": float(pos.qty), "mark": MARK_AT_TRIGGER}
    # (2) uydurma kuralla emir açılmamalı
    assert used["source"] != "default", "giriş UYDURMA kuralla açıldı (evren kaydındaki resmi filtre yok sayıldı): %s" % ctx
    assert float(used["price_tick"]) == float(OFFICIAL_TICK), "price_tick resmi değil: %s" % ctx
    assert float(used["qty_step"]) == float(OFFICIAL_STEP), "qty_step resmi değil: %s" % ctx
    # (3) sonuç: fiyat resmi ızgarada, miktar resmi lot adımında olmalı
    assert float(pos.qty) % float(OFFICIAL_STEP) == 0.0, "miktar resmi step_size ile OLANAKSIZ: %s" % ctx
    assert abs(float(pos.entry_avg) - MARK_AT_TRIGGER) / MARK_AT_TRIGGER < 0.005, \
        "fill mark'tan koptu (0,01 tick'e yuvarlandı): %s" % ctx


# ====================================================================== KARŞI ÖRNEK: önbellek resmi filtreleri taşıyorsa yol DOĞRU
def test_counterexample_when_the_cache_holds_official_filters_the_open_uses_them(tmp_path: Path):
    ex = _exinfo_new_listing(T0 - 2 * DAY)
    seed = _provider({}, ex, tickers={RAW: _ticker(RAW)})
    cache = FiltersCache(tmp_path / "seeded_filters.json")
    res = refresh_futures_filters(cache, seed, save=False)         # gerçek strict ayrıştırma yolu
    assert res["ok"] and res["n_ok"] == 1, res
    sc, book, p, rows15, _cfg_ = _make(tmp_path / "ok", cache=cache)
    pos = _run_to_entry(sc, book, p, rows15)
    assert pos is not None, {k: (v["status"], v.get("reasons")) for k, v in book.plans.items()}

    used = pos.meta["filters"]
    assert used["source"] == "binance_api", used
    assert float(used["price_tick"]) == 1e-05 and float(used["qty_step"]) == 1.0, used
    # fiyat resmi tick ızgarasında ve mark'a yapışık; miktar TAM SAYI lot
    fill = float(pos.entry_avg)
    assert fill == pytest.approx(0.12349, abs=1e-9), fill        # 0,00001 ızgarasında, mark'ın hemen üstünde
    assert abs(fill - MARK_AT_TRIGGER) / MARK_AT_TRIGGER < 0.005, fill
    assert float(pos.qty) == pytest.approx(225.0, abs=1e-9) and float(pos.qty) % 1.0 == 0.0, float(pos.qty)


# ====================================================================== EKSİK / GEÇERSİZ / BAYAT filtre → GİRİŞ YOK
@pytest.mark.parametrize(("adi", "filtreler"), [
    ("filtre_yok", []),
    ("tick_eksik", [{"filterType": "LOT_SIZE", "stepSize": OFFICIAL_STEP, "minQty": OFFICIAL_STEP}]),
    ("tick_sifir", [{"filterType": "PRICE_FILTER", "tickSize": "0"},
                    {"filterType": "LOT_SIZE", "stepSize": OFFICIAL_STEP, "minQty": OFFICIAL_STEP}]),
    ("step_eksik", [{"filterType": "PRICE_FILTER", "tickSize": OFFICIAL_TICK}]),
])
def test_missing_or_invalid_official_filters_block_the_entry(tmp_path: Path, adi, filtreler):
    """Resmî kural DOĞRULANAMIYORSA giriş yapılmaz ve gerekçe GÖRÜNÜR olur — varsayılana sessiz düşüş YOK.
    Bozulma BORSA KAYDINDA yapılır (`exchangeInfo`), keşif çıktısında değil: gerçek yol uçtan uca koşar."""
    sc, book, p, rows15, _cfg_ = _make(tmp_path / adi)
    for i in (39, 40):
        CLOCK[0] = T0 + i * M15
        _set_mark(p, SYMBOL, rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    p.symbols_info[0]["filters"] = filtreler       # borsa bu sözleşme için kuralı yayımlamıyor
    CLOCK[0] = T0 + 41 * M15
    _set_mark(p, SYMBOL, MARK_AT_TRIGGER)
    sc.scan_cycle(now_ms=CLOCK[0])
    assert SYMBOL not in book.ledger.positions, "doğrulanmamış kuralla pozisyon açıldı (%s)" % adi
    assert book.rejections.get("FILTERS_UNVERIFIED"), book.rejections
    pl = next(pl for pl in book.plans.values() if "FILTERS_UNVERIFIED" in (pl.get("reasons") or []))
    assert pl["filters_used"].get("detail"), pl["filters_used"]          # gerekçe kayıtta GÖRÜNÜR


def test_stale_universe_record_blocks_the_entry(tmp_path: Path):
    """BAYAT keşif kaydı resmî kural SAYILMAZ: `as_of_ms` politikadan eski ise giriş reddedilir.
    Gerçek giriş yolu (`PatternBook.process_symbol`) doğrudan sürülür; bayat kayıt açıkça verilir."""
    from tradingbot.pattern_trader.book import FILTERS_MAX_AGE_MS
    sc, book, p, rows15, _cfg_ = _make(tmp_path)
    for i in (39, 40):
        CLOCK[0] = T0 + i * M15
        _set_mark(p, SYMBOL, rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15
    _set_mark(p, SYMBOL, MARK_AT_TRIGGER)
    sc.refresh_universe(now_ms=CLOCK[0], force=True)
    ue = dict(sc.universe["entries"][SYMBOL])
    ue["as_of_ms"] = CLOCK[0] - FILTERS_MAX_AGE_MS - 1                    # sınırın 1 ms ötesinde
    bars, statuses = {}, {}
    for tf in ("15m", "1h", "4h"):
        bars[tf], statuses[tf] = sc.data.bars(SYMBOL, tf, as_of_ms=CLOCK[0])
    book.process_symbol(SYMBOL, bars_by_tf=bars, statuses=statuses, as_of_ms=CLOCK[0], universe_entry=ue,
                        price=sc.price.mark(SYMBOL, now_ms=CLOCK[0]), liquidity=lambda: sc.price.liquidity(SYMBOL),
                        run_id="test-run")
    assert SYMBOL not in book.ledger.positions and book.rejections.get("FILTERS_STALE"), book.rejections
    pl = next(pl for pl in book.plans.values() if "FILTERS_STALE" in (pl.get("reasons") or []))
    fu = pl["filters_used"]
    assert fu["discovered_ms"] == CLOCK[0] - FILTERS_MAX_AGE_MS - 1 and fu["age_s"] >= fu["max_age_s"], fu


def test_rounding_is_rechecked_before_the_entry(tmp_path: Path):
    """YUVARLAMA SONRASI GEOMETRİ: kuantize giriş fiyatıyla maliyet sonrası R/R yeniden hesaplanır ve plana
    yazılır — gerçekleşme ızgaraya oturduktan sonra da asgari R/R sağlanmalıdır. Önizleme defterin KENDİ
    `market_fill_price` yolundan geçer, ayrı bir tahmin değildir."""
    sc, book, p, rows15, _cfg_ = _make(tmp_path)
    pos = _run_to_entry(sc, book, p, rows15)
    assert pos is not None
    pl = next(pl for pl in book.plans.values() if pl.get("position_id") == pos.id)
    rr = pl["rr_at_entry"]
    assert "quantized_entry" in rr and "after_cost_quantized" in rr, rr
    assert rr["quantized_entry"] == pytest.approx(float(pos.entry_avg), rel=1e-9), (rr, float(pos.entry_avg))
    assert rr["after_cost_quantized"] >= float(pl["min_rr_after_cost"]), rr


# ====================================================================== PAYLAŞILAN ÖNBELLEK KİRLETİLMEZ
def test_pattern_book_never_stamps_the_shared_filters_cache(tmp_path: Path):
    """Karşıt doğrulama bulgusu: `FiltersCache.put()` önbellek GENELİ `verified_at` damgasını yeniler ve ana botun
    `ensure_symbol_filters` yenilemesi bunu "taze" sanıp resmî exchangeInfo çekimini ATLAR (engine_v3, kapı açıkken
    per-sembol yaşı ilerlemeye devam eder). Formasyon defteri paylaşılan nesneye YAZMAMALI."""
    ex = _exinfo_new_listing(T0 - 2 * DAY)
    seed = _provider({}, ex, tickers={RAW: _ticker(RAW)})
    cache = FiltersCache(tmp_path / "shared_filters.json")
    refresh_futures_filters(cache, seed, save=False)           # ana botun resmî yenilemesi: damga kurulur
    stamped = cache.verified_at
    assert stamped, "kurulum: ana bot damgası var"
    before = {s: (str(f.price_tick), str(f.qty_step), f.verified_at) for s, f in cache._data[MarketType.USDM_PERP].items()}
    sc, book, p, rows15, _cfg_ = _make(tmp_path / "run", cache=cache)
    assert _run_to_entry(sc, book, p, rows15) is not None, "kurulum: giriş açılmalı"
    assert cache.verified_at == stamped, "paylaşılan önbelleğin yenileme damgası DEĞİŞTİ (ana botun yenilemesi aç kalır)"
    after = {s: (str(f.price_tick), str(f.qty_step), f.verified_at) for s, f in cache._data[MarketType.USDM_PERP].items()}
    assert after == before, "paylaşılan önbelleğin içeriği değişti"


def test_a_cached_but_stale_filter_is_not_reused(tmp_path: Path):
    """Yaş denetimi HER İKİ kaynakta: önbellekteki kayıt da bayatsa kabul edilmez ve o turun keşif kaydına dönülür.
    ÖNCE (ara sürüm): önbellek dalında yaş denetimi HİÇ yoktu — bir kez yazılan kural sonsuza dek kullanılıyordu."""
    from tradingbot.pattern_trader.book import FILTERS_MAX_AGE_MS
    from tradingbot.accounting.filters import from_universe_entry
    sc, book, p, rows15, _cfg_ = _make(tmp_path)
    for i in (39, 40):
        CLOCK[0] = T0 + i * M15
        _set_mark(p, SYMBOL, rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15
    ue = sc.universe["entries"][SYMBOL]
    stale = from_universe_entry(SYMBOL, ue, MarketType.USDM_PERP)
    stale.price_tick = Decimal("0.5")                          # önbellekte BAYAT ve YANLIŞ bir kural
    stale.verified_at = iso(datetime.fromtimestamp((CLOCK[0] - FILTERS_MAX_AGE_MS - 1) / 1000.0, tz=timezone.utc))
    book.filters_cache.put(stale)
    f, why, ev = book._resolve_filters(SYMBOL, ue, CLOCK[0])
    assert why == "" and f is not None and str(f.price_tick) == OFFICIAL_TICK, (why, ev)
    assert ev["origin"] == "universe_entry", "bayat önbellek kaydı KULLANILMAMALI"


def test_an_undated_record_is_refused_rather_than_assumed_fresh(tmp_path: Path):
    """FAIL-CLOSED: keşif kaydının zamanı çözülemiyorsa "bayat değil" varsayılmaz — giriş reddedilir.
    ÖNCE (ara sürüm): `if seen and ...` kalıbı, zaman yoksa yaş denetimini tamamen ATLIYORDU."""
    sc, book, p, rows15, _cfg_ = _make(tmp_path)
    for i in (39, 40):
        CLOCK[0] = T0 + i * M15
        _set_mark(p, SYMBOL, rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15
    ue = dict(sc.universe["entries"][SYMBOL], as_of_ms=None, last_seen_at=None)
    f, why, ev = book._resolve_filters(SYMBOL, ue, CLOCK[0])
    assert f is None and why == "FILTERS_UNDATED", (why, ev)
    ue2 = dict(sc.universe["entries"][SYMBOL], as_of_ms="lorem", last_seen_at="ipsum")
    assert book._resolve_filters(SYMBOL, ue2, CLOCK[0])[1] == "FILTERS_UNDATED", "çözülemeyen zaman da RET"

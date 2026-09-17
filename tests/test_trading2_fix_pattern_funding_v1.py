# -*- coding: utf-8 -*-
"""BULGU 3 — FORMASYON DEFTERİ FUNDING'İ HİÇ SORMUYOR: bilinmeyen maliyet SIFIR olarak raporlanıyor.

ÜRETİM YOLU: `pattern_trader/scheduler.py` → `_scan_symbol` `book.apply_closed_bars(...)`, `exit_check`
`book.tick(...)` çağırır ve HİÇBİRİ `funding_rate_lookup` geçirmez. Ana bot (`engine_v3.py`) aynı defter
çağrılarına `static_rates(funding)` verir; formasyon defteri vermez. `accounting/funding.py`
(`FundingSchedule.accrue`) oran YOKSA döngüyü kırar: sessiz bir kesinti YAZILMAZ — ama `last_funding_settlement_utc`
da ilerlemez, yani settlement HİÇ kapanmaz. Sonuç: kapanmış kayıtta `funding == 0.0` görünür, oysa ölçülmemiştir.

ETİKET: gerçek `PatternScanner`/`PatternBook`/`FuturesLedgerV2`/`FundingSchedule`/`apply_action` kodu çalışır; AĞ
yerine `market.providers.MockProvider` (deterministik mumlar + `mark_price` içinde `funding_rate`) kullanılır.
Hiçbir doğrulama fonksiyonu mock'lanmaz, hiçbir fonksiyon kopyalanmaz.

TEST ORANI UYARISI: bu dosyadaki ±0,01 (yani %1) bir TEST DEĞERİdir, GERÇEK BORSA ORANI DEĞİLDİR. Binance USDⓈ-M
gerçek funding oranları tipik olarak 0,0001 (%0,01) mertebesindedir; burada işaret ve büyüklük ilişkisi okunaklı
olsun diye abartılmıştır. Rakamlar KÂRLILIK KANITI DEĞİLDİR.

SENARYO: 07:59 UTC'de PAPER pozisyon açılır (ölçülen nominal ~27 USDT — üretim risk profili PAPER_RESEARCH ile
sabittir, kırpılmamıştır), saat 08:01'e ilerletilir (arada 08:00 settlement'ı vardır) ve kapatılır.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.accounting.funding import static_rates  # noqa: E402
from tradingbot.core import iso  # noqa: E402
from tradingbot.market.providers import to_raw  # noqa: E402
from tradingbot.pattern_trader.report import build_report  # noqa: E402
from test_pattern_trader_v1 import (CLOCK, DAY, H1, H4, M15, T0, _cfg, _df, _exinfo, _provider, _scanner,  # noqa: E402
                                    _trend_series, long_15m, short_15m)

# T0 UTC gün sınırına hizalıdır → mutlak saatler kurulabilir
START = T0 - 10 * M15                                  # 15m barlar: 40. bar T0+31*M15 = 07:45'te KAPANIR
ENTRY_MS = T0 + 7 * 3_600_000 + 59 * 60_000            # 07:59 UTC — giriş (settlement'tan 1 dk önce)
SETTLE_MS = T0 + 8 * 3_600_000                         # 08:00 UTC — funding settlement
AFTER_MS = T0 + 8 * 3_600_000 + 60_000                 # 08:01 UTC — settlement'tan 1 dk sonra
TEST_RATE = 0.01                                       # TEST DEĞERİ (%1), gerçek borsa oranı DEĞİL
#: O ANIN tahmini (premiumIndex.lastFundingRate) ile GERÇEKLEŞMİŞ settlement oranı KASTEN farklıdır: tahminin
#: geçmiş bir settlement'a uygulanması hâlinde tutar tutmayacağı için testler bunu yakalar.
LIVE_ESTIMATE = 0.05                                   # TEST DEĞERİ — bilerek "yanlış" (gerçek oran değil)


def _iso(ms: int) -> str:
    return iso(datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc))


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


def _set_mark(provider, symbol: str, px: float, *, ts: int, funding_rate: float) -> None:
    """MockProvider.mark_price → premiumIndex ile aynı alanlar (mark + lastFundingRate)."""
    provider.marks[to_raw(symbol)] = {"mark": float(px), "ts": int(ts), "funding_rate": float(funding_rate)}


@pytest.fixture(autouse=True)
def _reset_clock():
    CLOCK[0] = T0
    yield
    CLOCK[0] = T0


def _scenario(tmp_path: Path, side: str, *, rate: float = TEST_RATE, realised: bool = True):
    """Üretim zinciriyle 07:59 UTC'de açılmış PAPER pozisyon. Döner: (cfg, provider, scanner, book, symbol, pos).

    `realised=True` ise sağlayıcı 08:00 settlement'ı için GERÇEKLEŞMİŞ bir oran yayımlar (`/fapi/v1/fundingRate`)
    ve `mark_price.funding_rate` KASTEN BAŞKA bir değerdir (`LIVE_ESTIMATE`): tahminin geçmişe uygulanması
    hâlinde tutar tutmaz. `realised=False` ise gerçekleşmiş satır YOKTUR (kesinti senaryosu)."""
    long_side = side == "LONG"
    sym, raw, base = ("LNG/USDT", "LNGUSDT", "LNG") if long_side else ("SHRT/USDT", "SHRTUSDT", "SHRT")
    rows15 = long_15m(start_ts=START) if long_side else short_15m(start_ts=START)
    rows4h = _trend_series(30, start_ts=START - 30 * H4, step=H4, px0=70.0 if long_side else 130.0, drift=1.0, up=long_side)
    rows1h = _trend_series(30, start_ts=START - 30 * H1, step=H1, px0=95.0 if long_side else 105.0, drift=0.2, up=long_side)
    ex = _exinfo([(raw, base, "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])
    fr = {sym: {SETTLE_MS: rate}} if realised else {sym: {}}
    p = _provider({sym: {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex,
                  funding_rates=fr, funding_info=[])
    cfg = _cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    for idx in (38, 39):                                # bulgu → teyit + plan (emir YOK)
        CLOCK[0] = START + (idx + 1) * M15
        _set_mark(p, sym, rows15[idx]["close"], ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = ENTRY_MS                                 # 40. bar 07:45'te kapandı: tetik → risk → giriş (07:59)
    _set_mark(p, sym, rows15[40]["close"], ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    sc.scan_cycle(now_ms=CLOCK[0])
    pos = book.ledger.positions.get(sym)
    assert pos is not None, {k: (v["status"], v.get("reasons")) for k, v in book.plans.items()}
    assert pos.side.value == side and pos.opened_at == _iso(ENTRY_MS), (pos.side.value, pos.opened_at)
    assert pos.last_funding_settlement_utc == _iso(ENTRY_MS), "watermark girişte başlar"
    return cfg, p, sc, book, sym, pos


# ====================================================================== ONARIM 1: üretim çağrı yolu funding'i BAĞLIYOR
def test_production_paths_now_bind_funding_to_the_position(tmp_path: Path):
    """07:59 → 08:01: arada 08:00 settlement'ı VAR.

    ÖNCE (60746c3): iki üretim çağrı yolu da (`_scan_symbol` → `apply_closed_bars`, `exit_check` → `tick`)
    `funding_rate_lookup` geçirmiyordu; ölçülen: watermark 07:59'da çakılı, `funding_paid` 0,0, defter
    `total_funding` 0,0 — oysa oran sağlayıcıda MEVCUTTU. SONRA: tarayıcı gerçekleşmiş settlement kaynağını
    (`FundingRates`, `/fapi/v1/fundingRate`) her iki yola da bağlar; 08:00 kapanır ve tutar tam olarak
    qty × mark × oran olur.

    NOT: bu senaryoda KAPANMIŞ BAR yoluna uygun bar YOKTUR (girişi içeren bar girişten ÖNCE açılmıştır ve
    tasarım gereği dışlanır) — settlement'ı 60 sn'lik çıkış izleyicisi kapatır. Bar yolunun kendisi ayrı
    testte (`test_closed_bar_path_also_settles_funding`) doğrulanır."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path, "LONG")
    flat = float(pos.entry_avg)                          # ne stop ne hedef: pozisyon açık kalır
    expected_amount = float(pos.qty) * flat * TEST_RATE  # 08:00'da LONG'un ödemesi gereken (TEST oranı)

    # üretimdeki iki yol da gerçek oran kaynağını ALIR (bağ kurulmuş mu?)
    assert sc.funding is not None, "tarayıcı gerçekleşmiş funding kaynağını kurmalı"
    assert book.funding_rates is sc.funding, "defter ile tarayıcı AYNI kaynağı kullanmalı (tek kaynak)"
    assert book.ledger.funding.fallback_to_last_known is False, "bilinmeyen oran TAHMİNLE doldurulmamalı"

    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, flat, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    sc.scan_cycle(now_ms=CLOCK[0])                       # tarama yolu (bar uçları)
    sc.exit_check()                                      # çıkış izleyicisi yolu
    observed = {"watermark": pos.last_funding_settlement_utc, "funding_paid": float(pos.funding_paid),
                "ledger_total_funding": float(book.ledger.total_funding),
                "provider_rate": p.marks[to_raw(sym)]["funding_rate"]}
    assert pos.last_funding_settlement_utc == _iso(SETTLE_MS), observed
    assert float(pos.funding_paid) == pytest.approx(expected_amount, rel=1e-6), observed
    assert float(book.ledger.total_funding) == pytest.approx(-expected_amount, rel=1e-6), observed
    # İDEMPOTENS: aynı settlement ikinci turda TEKRAR yazılmaz
    sc.scan_cycle(now_ms=CLOCK[0])
    sc.exit_check()
    assert float(book.ledger.total_funding) == pytest.approx(-expected_amount, rel=1e-6), "settlement iki kez yazıldı"


def test_closed_bar_path_also_settles_funding(tmp_path: Path):
    """KAPANMIŞ BAR yolu (`apply_closed_bars`) da funding'i bağlar: pozisyon settlement'ı kapsayan bardan ÖNCE
    açıldığında, o bar uygulanırken 08:00 settlement'ı kapanır (çıkış izleyicisi hiç çağrılmadan)."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path, "LONG")
    flat = float(pos.entry_avg)
    pos.opened_at = _iso(SETTLE_MS - 20 * 60_000)        # 07:40: 07:45–08:00 barı artık "girişten sonra açılmış"
    pos.last_funding_settlement_utc = pos.opened_at
    qty = float(pos.qty)
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, flat, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    bars, statuses = sc.data.bars(sym, "15m", as_of_ms=CLOCK[0])
    # settlement barın KAPANIŞ tick'inde tahakkuk eder → mark = 07:45'te açılan barın kapanışı (canlı mark DEĞİL)
    settle_bar = next(b for b in bars if int(b["timestamp"]) == SETTLE_MS - M15)
    expected = qty * float(settle_bar["close"]) * TEST_RATE
    spec = {sym: {"tf": "15m", "rows": bars[-48:], "mark": flat, "market": statuses["market"],
                  "first_bar_ms": int(bars[0]["timestamp"])}}
    book.apply_closed_bars(spec, now=_dt(AFTER_MS), funding_rate_lookup=sc.funding.lookup)
    assert pos.last_funding_settlement_utc == _iso(SETTLE_MS), pos.last_funding_settlement_utc
    assert float(pos.funding_paid) == pytest.approx(expected, rel=1e-6)


# ====================================================================== ONARIM 2: "ölçüldü ve 0" ile "hiç sorulmadı" AYRI
def test_closed_record_separates_measured_zero_from_unasked_funding(tmp_path: Path):
    """Kapanmış kayıt ve rapor artık funding KAPSAMASINI taşır: kaç settlement bekleniyordu, kaçı mutabık,
    oran kaynağı neydi. ÖNCE: yalnız `funding = 0.0` vardı ve "oran sıfırdı" ile "oran hiç sorulmadı" ayırt
    edilemiyordu — ölçülmemiş maliyet ölçülmüş sıfır gibi raporlanıyordu."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "rec", "LONG")
    target = float(pos.targets[0])
    CLOCK[0] = AFTER_MS                                  # 08:00 geçildi + fiyat hedefin üstünde → kapanış
    _set_mark(p, sym, target + 0.5, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    recs = sc.exit_check()
    assert len(recs) == 1 and not book.ledger.positions, "üretim çıkış yolu pozisyonu kapatmalı"

    rec = book.ledger.history_dicts()[-1]
    cov = (rec.get("features") or {}).get("funding_coverage") or {}
    assert cov.get("due") == 1 and cov.get("settled") == 1 and cov.get("complete") is True, cov
    assert cov.get("rate_source") == "lookup" and cov.get("hours_utc") == [0, 8, 16], cov
    assert float(rec["funding"]) != 0.0, "gerçek oran bağlandı: funding artık ölçülmüş bir değer"

    rep = build_report({"cohort_stats": book.cohort_stats}, history=book.ledger.history_dicts())
    costs = rep["totals"]["costs"]
    fc = costs["funding_coverage"]
    assert fc["trades"] == 1 and fc["complete"] == 1 and fc["incomplete"] == 0 and fc["unknown"] == 0, fc
    assert costs["funding_measured"] is True, costs


def test_report_flags_trades_whose_funding_was_never_asked(tmp_path: Path):
    """Oran kaynağı BAĞLANMAMIŞSA (ya da settlement mutabık değilse) rapor bunu GÖRÜNÜR yapar ve funding
    toplamını 'ölçüldü' diye sunmaz — eksik maliyet kapsamı sessizce sıfıra dönüşmez."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "nolookup", "LONG")
    book.funding_rates = None                            # oran kaynağı YOK (ağ/uç erişilemiyor)
    sc.funding = None
    target = float(pos.targets[0])
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, target + 0.5, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    recs = sc.exit_check()
    assert len(recs) == 1, "funding kesintisi KORUYUCU ÇIKIŞI engellememeli"
    rec = book.ledger.history_dicts()[-1]
    cov = (rec.get("features") or {}).get("funding_coverage") or {}
    assert float(rec["funding"]) == 0.0 and cov["due"] == 1 and cov["settled"] == 0, (rec.get("funding"), cov)
    assert cov["complete"] is False and cov["reason"] == "RATES_MISSING" and cov["rate_source"] == "none", cov
    costs = build_report({"cohort_stats": book.cohort_stats}, history=book.ledger.history_dicts())["totals"]["costs"]
    assert costs["funding_measured"] is False and costs["funding_coverage"]["incomplete"] == 1, costs


# ====================================================================== KARŞI ÖRNEK 1: defter oranı ALINCA doğru işliyor
@pytest.mark.parametrize(("side", "rate", "pays"), [("LONG", TEST_RATE, True), ("LONG", -TEST_RATE, False),
                                                    ("SHORT", TEST_RATE, False), ("SHORT", -TEST_RATE, True)])
def test_control_same_book_settles_funding_when_a_known_lookup_is_supplied(tmp_path: Path, side, rate, pays):
    """KONTROL (bugün GEÇER, onarımdan sonra da GEÇMELİ): AYNI `PatternBook`/`FuturesLedgerV2`, AYNI fiyat yolu,
    tek fark `funding_rate_lookup`. Oran verilince 08:00 settlement'ı kapanır, watermark 08:00'a taşınır ve tutar
    tam olarak qty × mark × oran olur; işaret: oran>0 ise LONG öder / SHORT alır, oran<0 ise tersi.

    ±0,01 bir TEST DEĞERİdir (gerçek borsa oranı değil)."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / ("%s_%s" % (side, "pos" if rate > 0 else "neg")), side, rate=rate)
    flat = float(pos.entry_avg)
    expected = float(pos.qty) * flat * abs(rate)
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, flat, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)

    # `exit_check`'in yaptığının AYNISI — yalnız eksik argüman eklenmiş hâli
    marks, marks_f, gaps = sc.price.marks([sym], now_ms=CLOCK[0])
    assert marks and not gaps, gaps
    recs = book.tick(marks, now=_dt(AFTER_MS), funding_rate_lookup=static_rates({sym: rate}))
    assert not recs and sym in book.ledger.positions, "düz fiyat: funding tahakkuk eder, pozisyon kapanmaz"

    assert pos.last_funding_settlement_utc == _iso(SETTLE_MS), "watermark 08:00'a taşınır"
    if pays:
        assert float(pos.funding_paid) == pytest.approx(expected, rel=1e-6) and float(pos.funding_received) == 0.0
        assert float(book.ledger.total_funding) == pytest.approx(-expected, rel=1e-6)
    else:
        assert float(pos.funding_received) == pytest.approx(expected, rel=1e-6) and float(pos.funding_paid) == 0.0
        assert float(book.ledger.total_funding) == pytest.approx(expected, rel=1e-6)

    # kapanış: kayıt net funding'i TAŞIR ve net PnL'e girer
    stop_side_px = float(pos.targets[0]) + (0.5 if side == "LONG" else -0.5)
    _set_mark(p, sym, stop_side_px, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    marks2, _f2, _g2 = sc.price.marks([sym], now_ms=CLOCK[0])
    recs2 = book.tick(marks2, now=_dt(AFTER_MS), funding_rate_lookup=static_rates({sym: rate}))
    assert len(recs2) == 1, "hedef dolar"
    rec = book.ledger.history_dicts()[-1]
    assert float(rec["funding"]) == pytest.approx(expected * (-1.0 if pays else 1.0), rel=1e-6)
    assert float(rec["net_pnl"]) == pytest.approx(float(rec["gross_pnl"]) - float(rec["fees"]) + float(rec["funding"]), rel=1e-6)
    # ikinci tick aynı settlement'ı TEKRAR uygulamaz (idempotent)
    assert float(book.ledger.total_funding) == pytest.approx(expected * (-1.0 if pays else 1.0), rel=1e-6)


# ====================================================================== KARŞI ÖRNEK 2: oran BİLİNMİYORSA sessiz kesinti YOK
def test_control_unknown_rate_is_never_silently_charged_and_never_marked_settled(tmp_path: Path):
    """KONTROL (bugün GEÇER, onarımdan sonra da GEÇMELİ): oran çözülemediğinde uydurma 0 ile settlement KAPATILMAZ.
    Onarım "lookup yoksa 0 varsay" biçiminde yapılırsa bu kapı düşer — bilinmeyen maliyet ölçülmüş sıfıra dönüşür.
    Doğru onarım oranı BAĞLAMAK, watermark'ı bedava ilerletmek değildir."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "unknown", "LONG")
    flat = float(pos.entry_avg)
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, flat, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    marks, _f, _g = sc.price.marks([sym], now_ms=CLOCK[0])

    book.tick(marks, now=_dt(AFTER_MS), funding_rate_lookup=static_rates({}))   # oran YOK (başka sembol/boş tablo)
    assert float(pos.funding_paid) == 0.0 and float(pos.funding_received) == 0.0
    assert float(book.ledger.total_funding) == 0.0, "bilinmeyen oran cüzdana YAZILMAZ"
    assert pos.last_funding_settlement_utc == _iso(ENTRY_MS), "kapanmayan settlement 'kapandı' SAYILMAZ"

    # oran sonradan gelince AYNI (kaçırılmış) 08:00 dönemi hâlâ uygulanabilir — kayıp değil, ertelenmiş
    book.tick(marks, now=_dt(AFTER_MS), funding_rate_lookup=static_rates({sym: TEST_RATE}))
    assert pos.last_funding_settlement_utc == _iso(SETTLE_MS)
    assert float(pos.funding_paid) == pytest.approx(float(pos.qty) * flat * TEST_RATE, rel=1e-6)


# ====================================================================== KARŞI ÖRNEK 3: ana bot ile formasyon defteri arasındaki fark
def test_control_main_engine_binds_funding_but_pattern_scheduler_does_not(tmp_path: Path):
    """KONTROL/SINIR: ana bot (`engine_v3`) defter çağrılarına `static_rates(...)` geçirir; formasyon tarayıcısı
    geçirmez. Bu kapı farkın YERİNİ sabitler — onarım `scheduler.py`'deki iki çağrıya lookup eklemekle kapanır."""
    import inspect

    from tradingbot import engine_v3
    from tradingbot.pattern_trader import scheduler as pt_scheduler

    eng_src = inspect.getsource(engine_v3)
    sch_src = inspect.getsource(pt_scheduler)
    assert "funding_rate_lookup=static_rates(" in eng_src, "ana bot funding oranını defterine BAĞLAR"
    assert "book.apply_closed_bars(" in sch_src and "book.tick(" in sch_src, "üretim çağrı yolları burada"
    # NOT: bu satır bugünkü kusuru belgeler; onarımdan sonra da anlamlı kalması için kusur kanıtı
    # test_defect_* kapılarında tutulur — burada yalnız çağrı yerlerinin varlığı sabitlenir.


# ====================================================================== SÖZLEŞME ARALIĞI: 8 saat VARSAYILMAZ
def test_contract_with_a_non_default_funding_interval_uses_its_own_settlement_hours(tmp_path: Path):
    """`/fapi/v1/fundingInfo` yalnız SAPAN sembolleri yayımlar. 4 saatlik bir sözleşmede settlement saatleri
    0/4/8/12/16/20'dir; 8 saat varsayımı bu sözleşmeye UYGULANMAZ."""
    from tradingbot.pattern_trader.funding import FundingRates, hours_from_interval
    assert hours_from_interval(4) == (0, 4, 8, 12, 16, 20) and hours_from_interval(8) == (0, 8, 16)
    assert hours_from_interval(0) is None and hours_from_interval(7) is None, "24'ü bölmeyen aralık ÇÖZÜLEMEZ"
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "iv4", "LONG")
    p._funding_info = [{"symbol": to_raw(sym), "fundingIntervalHours": "4"}]
    fr = FundingRates(p, clock_ms=lambda: CLOCK[0])
    assert fr.hours_for(sym) == (0, 4, 8, 12, 16, 20)
    assert fr.hours_for("OTHER/USDT") == (0, 8, 16), "listede olmayan sembol VARSAYILANDADIR"
    book.bind_funding(fr)
    assert book.ledger.funding.hours_for(sym) == (0, 4, 8, 12, 16, 20)


def test_unresolvable_funding_interval_produces_no_settlement_at_all(tmp_path: Path):
    """Aralık ÇÖZÜLEMİYORSA hiçbir dönem üretilmez (uydurulmuş settlement yok) ve kapsama bunu bildirir."""
    from tradingbot.pattern_trader.funding import FundingRates
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "ivx", "LONG")
    p._funding_info = [{"symbol": to_raw(sym), "fundingIntervalHours": "7"}]   # 24'ü bölmez
    book.bind_funding(FundingRates(p, clock_ms=lambda: CLOCK[0]))
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, float(pos.targets[0]) + 0.5, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    recs = sc.exit_check()
    assert len(recs) == 1, "aralık bilinmese de koruyucu çıkış çalışır"
    cov = (book.ledger.history_dicts()[-1].get("features") or {}).get("funding_coverage") or {}
    assert cov["interval_known"] is False and cov["complete"] is False and cov["reason"] == "INTERVAL_UNKNOWN", cov
    assert float(book.ledger.total_funding) == 0.0, "dönem üretilmediği için hiçbir kesinti YAZILMAZ"


def test_funding_provider_outage_never_blocks_a_protective_exit(tmp_path: Path):
    """Ağ arızası tahakkuku BEKLETİR ama stop/hedef kontrolünü ENGELLEMEZ; veri geri gelince settlement kapanır."""
    from tradingbot.pattern_trader.funding import FundingRates
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "outage", "LONG")
    fr = FundingRates(p, clock_ms=lambda: CLOCK[0], rate_ttl_s=0.0)
    book.bind_funding(fr)
    sc.funding = fr
    orig = p.funding_history
    p.funding_history = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mock: fundingRate erisilemedi"))
    flat = float(pos.entry_avg)
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, flat, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    assert sc.exit_check() == [] and sym in book.ledger.positions, "düz fiyat: kapanış yok, çökme de yok"
    assert float(book.ledger.total_funding) == 0.0 and pos.last_funding_settlement_utc == _iso(ENTRY_MS), \
        "oran yokken watermark İLERLEMEZ (sessiz sıfır maliyet yok)"
    assert fr.stats["history_errors"] >= 1, fr.stats
    p.funding_history = orig                              # veri geri geldi → aynı settlement bir kez kapanır
    sc.exit_check()
    assert pos.last_funding_settlement_utc == _iso(SETTLE_MS) and float(book.ledger.total_funding) < 0.0
    before = float(book.ledger.total_funding)
    sc.exit_check()
    assert float(book.ledger.total_funding) == before, "aynı settlement ikinci kez yazılmamalı"


# ====================================================================== BAŞ İDDİA: tahmin geçmişe UYGULANMAZ
def test_the_live_estimate_is_never_applied_to_a_past_settlement(tmp_path: Path):
    """Modülün baş iddiası: `premiumIndex.lastFundingRate` o ANIN tahminidir ve geçmiş bir settlement'a ASLA
    uygulanmaz. Kurulum bunu ölçülebilir kılar: gerçekleşmiş oran %1, canlı tahmin %5 — tahmin uygulanmış olsa
    tutar beş katı olurdu. (Karşıt doğrulama bulgusu: önceki sürümde test ikilisi tahmini geçmişe damgalıyordu,
    bu yüzden iddia HİÇBİR YERDE sınanmıyordu.)"""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path, "LONG")
    flat = float(pos.entry_avg)
    realised = float(pos.qty) * flat * TEST_RATE
    estimate = float(pos.qty) * flat * LIVE_ESTIMATE
    assert abs(estimate - realised) > 1e-6, "kurulum: iki değer AYIRT EDİLEBİLİR olmalı"
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, flat, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    sc.exit_check()
    assert float(pos.funding_paid) == pytest.approx(realised, rel=1e-6), \
        "gerçekleşmiş oran değil, o anın tahmini uygulanmış (paid=%.6f, tahmin=%.6f)" % (float(pos.funding_paid), estimate)
    assert float(pos.funding_paid) != pytest.approx(estimate, rel=1e-6)
    # oran kaynağı GERÇEKLEŞMİŞ satırdır ve tam settlement anına bağlıdır
    assert float(sc.funding.lookup(sym, _dt(SETTLE_MS))) == pytest.approx(TEST_RATE, rel=1e-9)
    assert sc.funding.lookup(sym, _dt(SETTLE_MS - 8 * 3_600_000)) is None, "gerçekleşmemiş dönem için oran ÜRETİLMEZ"


def test_no_realised_row_means_the_period_waits_instead_of_using_the_estimate(tmp_path: Path):
    """Gerçekleşmiş satır YOKSA dönem BEKLER: canlı tahmin devreye girmez, watermark ilerlemez, kapsama eksik kalır."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path, "LONG", realised=False)
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, float(pos.entry_avg), ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    sc.exit_check()
    assert float(book.ledger.total_funding) == 0.0 and pos.last_funding_settlement_utc == _iso(ENTRY_MS)
    assert sc.funding.lookup(sym, _dt(SETTLE_MS)) is None


def test_an_unreachable_funding_info_does_not_silently_assume_eight_hours(tmp_path: Path):
    """Karşıt doğrulama bulgusu: `fundingInfo` alınamadığında aralık tablosu boş kalıyor ve HER sembol sessizce
    8 saat varsayılıyordu — eksik settlement'lar hiç "due" olmadığı için kapsama YANLIŞLIKLA tam görünüyordu.
    Artık tablo doğrulanmadan varsayılan YAYILMAZ."""
    from tradingbot.pattern_trader.funding import FundingRates
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path / "noinfo", "LONG")
    p.funding_info = lambda: (_ for _ in ()).throw(RuntimeError("mock: fundingInfo erisilemedi"))
    fr = FundingRates(p, clock_ms=lambda: CLOCK[0])
    assert fr.hours_for(sym) == (), "tablo alınamadı: 8 saat DOĞRULANMAMIŞTIR"
    assert fr.stats["info_errors"] >= 1, fr.stats
    book.bind_funding(fr)
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, float(pos.targets[0]) + 0.5, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    assert len(sc.exit_check()) == 1, "aralık bilinmese de koruyucu çıkış ÇALIŞIR"
    cov = (book.ledger.history_dicts()[-1].get("features") or {}).get("funding_coverage") or {}
    assert cov["interval_known"] is False and cov["complete"] is False, cov
    costs = build_report({"cohort_stats": book.cohort_stats}, history=book.ledger.history_dicts())["totals"]["costs"]
    assert costs["funding_measured"] is False, "kapsama bilinmezken funding ÖLÇÜLDÜ denmemeli"
    # tablo geri gelince varsayılan yeniden DOĞRULANIR
    p.funding_info = lambda: []
    CLOCK[0] = AFTER_MS + int(fr.info_ttl_s * 1000) + 1
    assert fr.hours_for(sym) == (0, 8, 16), "tablo alınınca varsayılan doğrulanmış olur"


def test_funding_coverage_counts_applied_settlements_not_just_the_watermark(tmp_path: Path):
    """Karşıt doğrulama bulgusu: kapsama iki zaman aralığını çıkararak türetiliyordu, bu yüzden watermark'ın
    üzerinden geçtiği ATLANMIŞ bir settlement yapısal olarak görünmezdi. Artık GERÇEKTEN uygulanan settlement
    anları kayda geçer ve kapsama onları sayar."""
    _cfg_, p, sc, book, sym, pos = _scenario(tmp_path, "LONG")
    CLOCK[0] = AFTER_MS
    _set_mark(p, sym, float(pos.targets[0]) + 0.5, ts=CLOCK[0], funding_rate=LIVE_ESTIMATE)
    assert len(sc.exit_check()) == 1
    f = book.ledger.history_dicts()[-1]["features"]
    assert f["funding_settled_ts"] == [_iso(SETTLE_MS)], f.get("funding_settled_ts")
    assert f["funding_hours_utc"] == [0, 8, 16] and f["funding_coverage"]["source"] == "applied_settlements"
    assert f["funding_coverage"]["due"] == 1 and f["funding_coverage"]["settled"] == 1
    # KAPSAMA AĞA ÇIKMAZ: kapanış yolunda sağlayıcıya hiç dokunulmaz (kilit altında HTTP yok)
    before = dict(sc.funding.stats)
    book.funding_coverage(symbol=sym, opened_at=f["funding_settled_until"], until=_iso(AFTER_MS),
                          settled_until=f["funding_settled_until"], hours_utc=f["funding_hours_utc"],
                          settled_ts=f["funding_settled_ts"])
    assert dict(sc.funding.stats) == before, "kapsama hesabı sağlayıcıya çıktı"

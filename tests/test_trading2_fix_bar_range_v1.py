# -*- coding: utf-8 -*-
"""BAR ARALIK BANDI KUSURU V1 — sert fitiller stop kontrolünden KALICI olarak düşüyor.

ETİKET: gerçek `tradingbot.strategy_paper.apply_closed_bars_to_ledger` ve gerçek `FuturesLedgerV2` çalışır.
Hiçbir fonksiyon kopyalanmaz, taklit edilmez; ağ yolu bu sözleşmede zaten yoktur (bar satırları çağrının
girdisidir). Bu GERÇEK PİYASA SONUCU DEĞİLDİR: sentetik bar dizisiyle davranış kanıtıdır.

KUSUR (60746c3, ÖNCE): `sane` ifadesi VERİ BÜTÜNLÜĞÜ kontrolleriyle (sonlu, pozitif, lo <= cl <= hi) canlı
mark'a karşı FİYAT MAKULLÜĞÜ bandını (±%20, barın UÇLARINA) aynı koşulda birleştiriyordu. Band dışına düşen bar
BAR_OUT_OF_RANGE ile atlanıyor, ama `cursor = o` kontrolden ÖNCE atandığı ve imleç kalıcı olduğu için bar
sonsuza dek tüketilmiş sayılıyordu — koruyucu stop o fitili HİÇ göremiyordu. Ölçülen: LONG 100/stop 95,
mark 101, bar (h=103, l=70, c=101) → olay BAR_OUT_OF_RANGE, imleç barın açılışında, aynı bar yeniden
beslendiğinde sessiz no-op, pozisyon AÇIK.

ONARIM (SONRA): üç ayrı hüküm — bütünlük (`BAR_CORRUPT`, uç büyüklüğü dahil), ölçek (`BAR_SCALE_UNVERIFIED`,
YALNIZ barın KAPANIŞIYLA: fitil ölçmek istediğimiz şeydir) ve piyasa kimliği (`BAR_MARKET_MISMATCH`).
Uygulanamayan bar TÜKETİLMİŞ sayılmaz: `pos.meta['ohlc_gaps']` içinde görünür kalır ve veri düzelince YENİDEN
denenir; ama SONRAKİ geçerli barı da ENGELLEMEZ (karşıt doğrulamada ölçüldü: zinciri durdurmak, 48 barlık pencere
boyunca koruyucu stop'u düşürüyor ve zarar işlemini kâr olarak kaydettirebiliyordu).
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.accounting import AmountType, FuturesLedgerV2, MarketType, SizeSpec  # noqa: E402
from tradingbot.accounting.filters import default_filters  # noqa: E402
from tradingbot.core import iso  # noqa: E402
from tradingbot.strategy_paper import BAR_TIMEFRAME, apply_closed_bars_to_ledger  # noqa: E402

H1 = 3_600_000
SYM = "TEST/USDT"
#: Saat sınırına hizalı sabit bar açılışı (determinizm).
BAR_OPEN = 1_780_000_000_000 // H1 * H1
#: Pozisyon bardan bir saat ÖNCE açılmış olsun → bar "girişten sonra açılmış" sayılır.
OPENED_MS = BAR_OPEN - H1
#: Bar `now` anında kapanmış olsun.
NOW = datetime.fromtimestamp((BAR_OPEN + 2 * H1) / 1000.0, tz=timezone.utc)


# ------------------------------------------------------------------ kurucular
def _ledger() -> FuturesLedgerV2:
    """Kaldıraçsız (1x) izole kâğıt defter: likidasyon yolu devrede değil, tek değişken stop'tur."""
    return FuturesLedgerV2(Decimal("10000"), enforce_position_cap=False)


def _open(side: str, *, entry: float, stop: float) -> FuturesLedgerV2:
    led = _ledger()
    pos = led.open(SYM, side, Decimal(str(entry)), SizeSpec(Decimal("1000"), AmountType.NOTIONAL, 1),
                   stop=Decimal(str(stop)), filters=default_filters(SYM, MarketType.USDM_PERP),
                   now=datetime.fromtimestamp(OPENED_MS / 1000.0, tz=timezone.utc))
    assert pos is not None, "kurulum: pozisyon açılmalıydı (%s)" % led.last_reject_reason
    assert float(pos.entry_avg) == pytest.approx(entry), "kurulum: kayma yok, giriş tam"
    return led


def _spec(*, mark: float, high: float, low: float, close: float, ts: int = BAR_OPEN) -> dict:
    return {SYM: {"tf": BAR_TIMEFRAME, "mark": mark,
                  "rows": [{"timestamp": ts, "high": high, "low": low, "close": close}]}}


def _events() -> tuple[list, callable]:
    seen: list[dict] = []

    def on_event(sym, kind, reason, at, **extra):
        seen.append({"symbol": sym, "kind": kind, "reason": reason, **extra})

    return seen, on_event


def _cursor(led: FuturesLedgerV2) -> int | None:
    pos = led.positions.get(SYM)
    if pos is None:
        return None
    return (pos.meta.get("ohlc_cursor") or {}).get(BAR_TIMEFRAME)


# ================================================================== A) KARŞI ÖRNEK — bugün DOĞRU, bozulmamalı
def test_counterexample_long_bar_inside_band_triggers_stop():
    """LONG 100 / stop 95, mark 101, bar (h=103, l=94, c=101): band İÇİNDE → stop fitilden tetiklenir."""
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=103.0, low=94.0, close=101.0),
                                       now=NOW, on_event=on_event)
    assert len(recs) == 1 and SYM not in led.positions, "band içi fitil stop'u çalıştırmalıydı"
    assert recs[0].exit_reason == "stop"
    assert recs[0].closed_at == iso(datetime.fromtimestamp((BAR_OPEN + H1) / 1000.0, tz=timezone.utc)), \
        "kapanış zamanı barın kapanışı (gerçek olay zamanı)"
    assert seen == [], "geçerli bar için atlama olayı yazılmaz"


def test_counterexample_short_bar_inside_band_triggers_stop():
    """SHORT 100 / stop 105, mark 99, bar (h=106, l=97, c=99): band İÇİNDE → stop fitilden tetiklenir."""
    led = _open("SHORT", entry=100.0, stop=105.0)
    recs = apply_closed_bars_to_ledger(led, _spec(mark=99.0, high=106.0, low=97.0, close=99.0), now=NOW)
    assert len(recs) == 1 and SYM not in led.positions, "band içi fitil SHORT stop'unu çalıştırmalıydı"
    assert recs[0].exit_reason == "stop"


def test_counterexample_bar_before_entry_is_never_consumed():
    """Girişten ÖNCE açılmış bar ne uygulanır ne de imleci ilerletir (mevcut doğru davranış)."""
    led = _open("LONG", entry=100.0, stop=95.0)
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=103.0, low=70.0, close=101.0,
                                                  ts=OPENED_MS - H1), now=NOW)
    assert recs == [] and SYM in led.positions
    assert _cursor(led) is None, "girişten önceki bar imleci ilerletmez"


# ================================================================== B) ONARIM — sert fitil artık stop kontrolüne giriyor
def test_long_hard_wick_outside_the_old_band_now_triggers_the_stop():
    """LONG 100 / stop 95, mark 101, bar (h=103, l=70, c=101).

    Fitil stop'un 25 birim ALTINA iner; bütünlük kontrolleri (sonlu, pozitif, lo<=cl<=hi) TAMAMEN geçerlidir ve
    barın KAPANIŞI (101) canlı mark'la (101) aynı ölçektedir — yalnız ESKİ ±%20 bandının `low`a uygulanmasıyla
    eleniyordu. ÖNCE (60746c3): olay BAR_OUT_OF_RANGE, imleç barın açılışında, aynı bar yeniden beslendiğinde
    sessiz no-op, pozisyon AÇIK. SONRA: bar uygulanır, koruyucu stop çalışır.
    """
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=103.0, low=70.0, close=101.0),
                                       now=NOW, on_event=on_event)
    assert seen == [], "geçerli fitil artık atlama olayı üretmez"
    assert SYM not in led.positions and len(recs) == 1 and recs[0].exit_reason == "stop", \
        "stop'un 25 birim altına inen sert fitil koruyucu stop'u çalıştırmalı"
    assert float(recs[0].exit_price) <= 95.0, "gerçekleşme stop seviyesinde ya da altında (gap-through)"


def test_short_hard_wick_outside_the_old_band_now_triggers_the_stop():
    """SHORT 100 / stop 105, mark 99, bar (h=130, l=97, c=99): tepe stop'un 25 birim üstünde, eski band dışı."""
    led = _open("SHORT", entry=100.0, stop=105.0)
    seen, on_event = _events()
    recs = apply_closed_bars_to_ledger(led, _spec(mark=99.0, high=130.0, low=97.0, close=99.0),
                                       now=NOW, on_event=on_event)
    assert seen == []
    assert SYM not in led.positions and len(recs) == 1 and recs[0].exit_reason == "stop", \
        "stop'un 25 birim üstüne çıkan sert fitil SHORT stop'unu çalıştırmalı"
    assert float(recs[0].exit_price) >= 105.0


# ================================================================== C) BÜTÜNLÜK ile BAND aynı koşulda — bugünkü hâl
@pytest.mark.parametrize(("adi", "high", "low", "close"), [
    ("lo_gt_close", 103.0, 102.0, 100.0),              # lo <= cl <= hi bozuk: gerçekten BOZUK bar
    ("nan_close", 103.0, 99.0, float("nan")),          # sonlu değil: gerçekten BOZUK bar
    ("nonpositive_low", 103.0, 0.0, 101.0),            # pozitif değil: gerçekten BOZUK bar
])
def test_corrupt_bar_is_skipped_with_its_own_reason_and_is_not_consumed(adi, high, low, close):
    """BOZUK bar uygulanmaz (uydurulmuş uç yok) ama TÜKETİLMİŞ de sayılmaz: ayrı gerekçe, imleç ilerlemez,
    boşluk pozisyonun kalıcı kaydında görünür. ÖNCE: gerekçe band ile aynıydı (BAR_OUT_OF_RANGE) ve imleç
    ilerliyordu → aynı bar düzgün veriyle gelse bile bir daha işlenemezdi."""
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=high, low=low, close=close),
                                       now=NOW, on_event=on_event)
    assert recs == [] and SYM in led.positions, "bozuk bar uydurulmaz: hiçbir uç uygulanmaz (%s)" % adi
    assert [e["reason"] for e in seen] == ["BAR_CORRUPT"], "bütünlük ihlalinin KENDİ gerekçesi var"
    assert not any(math.isnan(v) for v in (float(led.positions[SYM].mae_pct), float(led.positions[SYM].mfe_pct)))
    gaps = (led.positions[SYM].meta.get("ohlc_gaps") or {}).get(BAR_TIMEFRAME) or []
    assert [g["reason"] for g in gaps] == ["BAR_CORRUPT"] and gaps[0]["bar_open_ms"] == BAR_OPEN, gaps
    # veri DÜZELİNCE aynı bar yeniden denenir ve uygulanır (kalıcı kayıp yok)
    ok = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=103.0, low=94.0, close=101.0), now=NOW)
    assert len(ok) == 1 and ok[0].exit_reason == "stop", "düzelmiş veriyle AYNI bar işlenebilmeli"


def test_scale_broken_bar_is_not_applied_and_not_consumed():
    """mark'ın ~3 katı bir bar (ölçek/birim bozulması) uygulanmaz — ÖLÇEK hükmü barın KAPANIŞINA bakar.
    Uygulanmayan bar tüketilmez: imleç ilerlemez, boşluk görünür, mark düzelince aynı bar yeniden denenir."""
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=310.0, low=280.0, close=300.0),
                                       now=NOW, on_event=on_event)
    assert recs == [] and SYM in led.positions, "ölçeği bozuk bar hedef/MFE üretmemeli"
    assert float(led.positions[SYM].mfe_pct) < 50.0, "ölçeği bozuk barın tepesi MFE'ye yazılmamalı"
    assert [e["reason"] for e in seen] == ["BAR_SCALE_UNVERIFIED"]
    gaps = (led.positions[SYM].meta.get("ohlc_gaps") or {}).get(BAR_TIMEFRAME) or []
    assert [g["reason"] for g in gaps] == ["BAR_SCALE_UNVERIFIED"] and gaps[0]["close"] == 300.0, gaps


def test_bar_from_a_different_market_is_refused_by_identity_not_by_a_price_band():
    """PİYASA KİMLİĞİ: çerçeve bildirdiği piyasa beklenenden farklıysa bar uygulanmaz (fiyat bandıyla TAHMİN
    edilmez) ve tüketilmez. ÖNCE: böyle bir kontrol YOKTU; kimlik yalnız ±%20 bandının yan etkisiydi."""
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    spec = _spec(mark=101.0, high=103.0, low=94.0, close=101.0)
    spec[SYM]["market"] = "SPOT"
    recs = apply_closed_bars_to_ledger(led, spec, now=NOW, on_event=on_event)
    assert recs == [] and SYM in led.positions and _cursor(led) is None, "piyasa uyuşmazlığında hiçbir bar ELE ALINMAZ"
    assert [e["reason"] for e in seen] == ["BAR_MARKET_MISMATCH"] and seen[0]["market"] == "SPOT"
    spec[SYM]["market"] = "USDM_PERP"                     # doğru piyasa: AYNI bar normal işlenir
    assert len(apply_closed_bars_to_ledger(led, spec, now=NOW)) == 1


def test_applied_bar_clears_a_previously_recorded_gap():
    """Boşluk kaydı KALICI değildir: aynı bar sonunda uygulanınca kayıt temizlenir (kapsama dürüst kalır)."""
    led = _open("LONG", entry=100.0, stop=90.0)
    apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=310.0, low=280.0, close=300.0), now=NOW)
    assert (led.positions[SYM].meta.get("ohlc_gaps") or {}).get(BAR_TIMEFRAME)
    apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=103.0, low=99.0, close=101.0), now=NOW)
    assert not (led.positions[SYM].meta.get("ohlc_gaps") or {}).get(BAR_TIMEFRAME), "uygulanan bar boşluğu kapatır"
    assert _cursor(led) == BAR_OPEN


def test_counterexample_unclosed_bar_is_not_consumed():
    """Henüz KAPANMAMIŞ bar hiç uygulanmaz ve imleci ilerletmez (mevcut doğru davranış)."""
    led = _open("LONG", entry=100.0, stop=95.0)
    now = datetime.fromtimestamp(BAR_OPEN / 1000.0, tz=timezone.utc) + timedelta(minutes=30)
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=103.0, low=70.0, close=101.0), now=now)
    assert recs == [] and SYM in led.positions and _cursor(led) is None


# ================================================================== D) ZİNCİR DURMAZ (karşıt doğrulama bulgusu)
def _multi(*rows, mark: float) -> dict:
    return {SYM: {"tf": BAR_TIMEFRAME, "mark": mark, "rows": list(rows)}}


def _row(ts, h, lo, c):
    return {"timestamp": ts, "high": h, "low": lo, "close": c}


@pytest.mark.parametrize(("adi", "bad"), [
    ("bozuk", _row(BAR_OPEN, 103.0, 0.0, 101.0)),                 # BAR_CORRUPT
    ("olcek_disi", _row(BAR_OPEN, 132.0, 128.0, 130.0)),          # BAR_SCALE_UNVERIFIED (kapanış mark'ın %30 üstü)
])
def test_an_unusable_bar_does_not_block_the_protective_exit_in_a_later_bar(adi, bad):
    """ASIL KUSUR (zincir): uygulanamayan bar, KENDİSİNDEN SONRAKİ geçerli barı engellememeli.

    Ölçülen (zinciri `break` ile durduran ara sürümde): bozuk/ölçek dışı bar #1 yüzünden, stop'u delen geçerli
    bar #2 pencere boyunca HİÇ uygulanmıyordu — koruyucu stop 48 saat gecikiyor, bu arada canlı fiyat hedefe
    ulaşırsa gerçek bir zarar işlemi KÂR olarak kaydediliyordu."""
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    good = _row(BAR_OPEN + H1, 105.0, 94.0, 100.0)                 # stop'u delen GEÇERLİ bar
    now = datetime.fromtimestamp((BAR_OPEN + 3 * H1) / 1000.0, tz=timezone.utc)
    recs = apply_closed_bars_to_ledger(led, _multi(bad, good, mark=101.0), now=now, on_event=on_event)
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and SYM not in led.positions,         "%s bar zinciri durdurdu: sonraki geçerli barın koruyucu stop'u UYGULANMADI" % adi
    assert [e["reason"] for e in seen] and seen[0]["bar_open_ms"] == BAR_OPEN, seen


def test_a_recorded_gap_is_retried_on_a_later_tour_even_after_the_cursor_moved_past_it():
    """Boşluk kaydı KALICI bir yeniden deneme hakkıdır: imleç bozuk barı geçmiş olsa bile, veri düzeldiğinde
    aynı bar yeniden uygulanır (kalıcı kayıp yok)."""
    led = _open("LONG", entry=100.0, stop=95.0)
    bad, good = _row(BAR_OPEN, 103.0, 0.0, 101.0), _row(BAR_OPEN + H1, 102.0, 99.0, 100.0)
    now = datetime.fromtimestamp((BAR_OPEN + 3 * H1) / 1000.0, tz=timezone.utc)
    assert apply_closed_bars_to_ledger(led, _multi(bad, good, mark=101.0), now=now) == []
    assert _cursor(led) == BAR_OPEN + H1, "sonraki geçerli bar UYGULANDI (imleç ilerledi)"
    gaps = (led.positions[SYM].meta.get("ohlc_gaps") or {}).get(BAR_TIMEFRAME) or []
    assert [g["bar_open_ms"] for g in gaps] == [BAR_OPEN], gaps
    # sonraki tur: AYNI bar artık düzgün veriyle geliyor ve stop'u deliyor → uygulanır
    fixed = _row(BAR_OPEN, 103.0, 94.0, 101.0)
    recs = apply_closed_bars_to_ledger(led, _multi(fixed, good, mark=101.0), now=now)
    assert len(recs) == 1 and recs[0].exit_reason == "stop", "boşluk kaydı olan bar yeniden denenmedi"
    assert recs[0].closed_at == iso(datetime.fromtimestamp((BAR_OPEN + H1) / 1000.0, tz=timezone.utc)),         "kapanış zamanı barın KENDİ kapanışı (geç uygulansa da olay zamanı gerçek)"


@pytest.mark.parametrize(("adi", "high", "low", "close"), [
    ("uc_10000_kat", 1_010_000.0, 99.0, 101.0),
    ("dip_10000_kat", 103.0, 0.0101, 101.0),
])
def test_a_fabricated_extreme_is_still_refused_even_though_the_close_is_in_scale(adi, high, low, close):
    """Ölçek hükmü kapanışa bakınca uçlar bağsız KALMAZ: barın KENDİ kapanışına göre uç büyüklüğü sınırlıdır.
    Derin ama gerçek fitil geçer (yukarıdaki testler), 10.000 katlık artefakt geçmez."""
    led = _open("LONG", entry=100.0, stop=95.0)
    seen, on_event = _events()
    recs = apply_closed_bars_to_ledger(led, _spec(mark=101.0, high=high, low=low, close=close),
                                       now=NOW, on_event=on_event)
    assert recs == [] and SYM in led.positions, "uydurma uç deftere UYGULANDI (%s)" % adi
    assert [e["reason"] for e in seen] == ["BAR_CORRUPT"], seen
    assert float(led.positions[SYM].mfe_pct) < 100.0 and float(led.positions[SYM].mae_pct) > -100.0

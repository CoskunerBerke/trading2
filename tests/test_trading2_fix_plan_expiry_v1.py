# -*- coding: utf-8 -*-
"""BULGU 4 — SÜRESİ DOLMUŞ ama ZATEN TETİKLENMİŞ plan, fiyat gelince hâlâ emir açıyor.

`PatternBook._try_open` içinde `expires_at_ms` denetimi YALNIZ "doğrulanmış fiyat yok" dalında yaşıyor:

    if not price or not price.get("ok"):
        ... TRIGGERED ...
        if int(as_of_ms) > int(pl["expires_at_ms"]): -> CANCELLED EXPIRED_WAITING_PRICE
        return "WAIT_PRICE"

Fiyat GELDİĞİNDE aynı denetim hiç tekrarlanmaz: önceki taramada TETİKLENMİŞ olan, fiyat boşluğunu bekleyen ve bu
arada süresi dolan bir plan, geçerli fiyat gelir gelmez OPENED/MANAGED'e ulaşır.

ONARIM (2026-09-17): sınır kuralı TEK yerde tanımlandı (`PatternBook.is_expired`: `as_of_ms > expires_at_ms`,
sınır anı hâlâ geçerli) ve `_try_open`ın EN BAŞINDA — yani her giriş yolunda, gerçekleşmeden önce — uygulanıyor.
Süresi dolan plan tek terminal durumda (`PL_EXPIRED` / `EXPIRED_BEFORE_ENTRY`) biter; sonraki taramada ya da
yeniden başlatmada DİRİLMEZ.

ETİKET: gerçek `PatternBook`/`PatternScanner`/`FuturesLedgerV2`/`RiskEngine`/`apply_action` kodu çalışır; AĞ yerine
`market.providers.MockProvider` kullanılır (test_pattern_trader_v1 ile aynı kurulum). Hiçbir doğrulama fonksiyonu
mock'lanmaz; ölçülen şey gerçek karar yoludur.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.pattern_trader.strategy import PL_EXPIRED, PL_MANAGED, PL_OPENED, PL_TRIGGERED  # noqa: E402
from test_pattern_trader_v1 import (CLOCK, DAY, H1, H4, M15, T0, _cfg, _df, _exinfo, _provider, _scanner, _set_mark,  # noqa: E402
                                    _trend_series, long_15m)

HOUR_MS = 3_600_000


@pytest.fixture(autouse=True)
def _reset_clock():
    """Saat her testte T0'dan başlar (paylaşılan modül düzeyi saati ödünç alınıyor)."""
    CLOCK[0] = T0
    yield
    CLOCK[0] = T0


def _triggered_plan_waiting_for_price(tmp_path: Path):
    """Meşru yol: bar 40 kapanışında plan TETİKLENİR, ama doğrulanmış fiyat BAYAT olduğu için giriş olmaz.

    Döner: (provider, scanner, book, plan). Plan `TRIGGERED` durumunda, süresi HENÜZ dolmamış."""
    rows15 = long_15m()
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2)
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    ex = _exinfo([("LNGUSDT", "LNG", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)])
    p = _provider({"LNG/USDT": {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, ex)
    cfg = _cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    for i in (39, 40):                                   # bulgu → teyit + plan (henüz tetik yok)
        CLOCK[0] = T0 + i * M15
        _set_mark(p, "LNG/USDT", rows15[i - 1]["close"])
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 41 * M15                             # bar 40 kapandı: TETİK — ama fiyat 1 saat eski (BAYAT)
    _set_mark(p, "LNG/USDT", rows15[40]["close"], ts=CLOCK[0] - HOUR_MS)
    sc.scan_cycle(now_ms=CLOCK[0])
    plan = next(pl for pl in book.plans.values() if pl["family"] == "A_TREND_PULLBACK" and pl["side"] == "LONG")
    assert plan["status"] == PL_TRIGGERED and plan["position_id"] is None, (plan["status"], plan.get("reasons"))
    assert not book.ledger.positions and book.data_gaps.get("LNG/USDT", {}).get("reason") == "STALE_FUTURES_PRICE"
    assert int(plan["expires_at_ms"]) > CLOCK[0], "kurulum: plan tetiklendiğinde süresi HENÜZ dolmamış olmalı"
    return p, sc, book, plan, rows15


# ====================================================================== KUSUR: süresi dolmuş plan fiyat gelince açılıyor
def test_defect_expired_triggered_plan_still_opens_when_price_returns(tmp_path: Path):
    """Süresi DOLMUŞ (fiyat boşluğunda bekleyen) plan, geçerli fiyat gelince emir AÇMAMALI.

    Bugün açıyor: `_try_open` fiyat varken `expires_at_ms`'i hiç okumuyor — bu yüzden bu test BAŞARISIZ olur."""
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    expiry = int(plan["expires_at_ms"])
    # fiyat boşluğu süre dolana kadar sürüyor (her taramada BAYAT fiyat: plan TETİKLENMİŞ bekler)
    for ts in (expiry - 3 * M15, expiry - M15):
        CLOCK[0] = ts
        _set_mark(p, "LNG/USDT", rows15[40]["close"], ts=CLOCK[0] - HOUR_MS)
        sc.scan_cycle(now_ms=CLOCK[0])
        assert plan["status"] == PL_TRIGGERED and not book.ledger.positions
    # süre DOLDU; bir tur sonra fiyat geri geliyor (diğer tüm koşullar sağlanıyor: kovalama/stop/likidite/R-R tamam)
    CLOCK[0] = expiry + M15
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert CLOCK[0] > expiry, "kurulum: giriş denemesi süre dolduktan SONRA"
    assert plan["status"] not in (PL_OPENED, PL_MANAGED), (
        "süresi %s'de dolan plan %s'de emir açtı (durum=%s, nedenler=%s): expires_at_ms denetimi yalnız "
        "'fiyat yok' dalında var" % (plan["expires_at"], CLOCK[0], plan["status"], plan.get("reasons")))
    assert "LNG/USDT" not in book.ledger.positions, "süresi dolmuş plan POZİSYON açtı"
    assert book.counters["opened"] == 0, book.counters


# ====================================================================== KARŞI ÖRNEK: süresi DOLMAMIŞ plan normal açılır
def test_unexpired_triggered_plan_still_opens_when_price_returns(tmp_path: Path):
    """Onarımın bozmaması gereken davranış: süresi DOLMAMIŞ, fiyat boşluğunda bekleyen plan, fiyat gelince AÇILIR."""
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    expiry = int(plan["expires_at_ms"])
    CLOCK[0] = expiry - M15                               # hâlâ geçerlilik penceresi içinde
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert CLOCK[0] < expiry, "kurulum: giriş denemesi süre dolmadan ÖNCE"
    assert "LNG/USDT" in book.ledger.positions, (plan["status"], plan.get("reasons"))
    pos = book.ledger.positions["LNG/USDT"]
    assert plan["status"] == PL_MANAGED and plan["position_id"] == pos.id and book.counters["opened"] == 1
    # aynı plan ikinci emri AÇMAZ (yinelenme kapanı)
    sc.scan_cycle(now_ms=CLOCK[0])
    assert book.counters["opened"] == 1 and len(book.ledger.positions) == 1


# ====================================================================== SINIR: as_of_ms == expires_at_ms (BUGÜNKÜ davranış)
def test_expiry_boundary_is_strictly_greater_today(tmp_path: Path):
    """Sınır kaydı: bugün kod `as_of_ms > expires_at_ms` (KESİN büyük) kullanıyor — tam eşitlikte plan HÂLÂ geçerli.

    Onarım sırasında kural `>=` yapılırsa bu test bilerek kırılır: `>` / `>=` seçimi TEK yerde tanımlanıp her yola
    (fiyat yok dalı, fiyat var dalı, AWAITING süre denetimi) aynı biçimde uygulanmalı."""
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    expiry = int(plan["expires_at_ms"])
    # (1) tam sınırda, fiyat hâlâ yok → bugün İPTAL YOK, plan TETİKLENMİŞ bekliyor
    CLOCK[0] = expiry
    _set_mark(p, "LNG/USDT", rows15[40]["close"], ts=CLOCK[0] - HOUR_MS)
    sc.scan_cycle(now_ms=CLOCK[0])
    assert plan["status"] == PL_TRIGGERED, (plan["status"], plan.get("reasons"))
    assert (plan.get("reasons") or [])[-1].startswith("PRICE_GAP_"), plan.get("reasons")
    # (2) sınırın 1 ms ötesinde, fiyat hâlâ yok → İPTAL (EXPIRED_WAITING_PRICE)
    CLOCK[0] = expiry + 1
    _set_mark(p, "LNG/USDT", rows15[40]["close"], ts=CLOCK[0] - HOUR_MS)
    sc.scan_cycle(now_ms=CLOCK[0])
    # ONARIM: süresi dolan plan TEK terminal durumda biter (PL_EXPIRED / EXPIRED_BEFORE_ENTRY) — hangi yoldan
    # gelirse gelsin (fiyat yok dalı, fiyat var dalı, bekleme dönüşü). ÖNCE: yalnız fiyat yok dalında ve
    # PL_CANCELLED / EXPIRED_WAITING_PRICE olarak.
    assert plan["status"] == PL_EXPIRED and (plan.get("reasons") or [])[-1] == "EXPIRED_BEFORE_ENTRY", \
        (plan["status"], plan.get("reasons"))
    assert not book.ledger.positions and book.counters["opened"] == 0


# ====================================================================== YENİDEN BAŞLATMA: süresi dolmuş plan DİRİLMEZ
def test_expired_plan_does_not_revive_after_a_restart(tmp_path: Path):
    """Terminal durum KALICIDIR: defter dosyasından yeniden yüklenen süresi dolmuş plan, geçerli fiyat gelse bile
    yeniden giriş denemez ve aynı sembolde pozisyon açmaz."""
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    expiry = int(plan["expires_at_ms"])
    CLOCK[0] = expiry + M15
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert plan["status"] == PL_EXPIRED and not book.ledger.positions
    book.save({}, sc._now_dt())                           # plan durumu diske yazılsın
    # YENİDEN BAŞLATMA: aynı state dizininden yeni defter + yeni tarayıcı
    sc2, book2 = _scanner(_cfg(tmp_path), p)
    revived = book2.plans.get(plan["plan_id"])
    assert revived is not None and revived["status"] == PL_EXPIRED, (revived or {}).get("status")
    for i in (1, 2):
        CLOCK[0] = expiry + (1 + i) * M15
        _set_mark(p, "LNG/USDT", rows15[40]["close"])
        sc2.scan_cycle(now_ms=CLOCK[0])
    assert revived["status"] == PL_EXPIRED, "yeniden başlatma süresi dolmuş planı DİRİLTMEMELİ"
    assert not any(pl["plan_id"] == plan["plan_id"] and pl.get("position_id") for pl in book2.plans.values())


# ====================================================================== KARAR ANI tur referansından TAZEdir
def test_expiry_uses_a_fresh_decision_time_not_the_stale_tour_reference(tmp_path: Path):
    """Uzun bir tarama turunda `as_of_ms` (tur referansı) sembol sırası geldiğinde dakikalarca eski olabilir.
    Geçerlilik süresi o ESKİ zamanla denetlenirse, gerçekte süresi dolmuş plan yine açılır. Onarım karar anını
    (`decision_ms`) sembolün GERÇEK tarama anından alır."""
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    expiry = int(plan["expires_at_ms"])
    stale_as_of = expiry - M15                            # tur referansı: süre DOLMAMIŞ görünüyor
    CLOCK[0] = expiry + 5 * 60_000                        # gerçek an: süre 5 dk ÖNCE doldu
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    bars, statuses = {}, {}
    for tf in ("15m", "1h", "4h"):
        bars[tf], statuses[tf] = sc.data.bars("LNG/USDT", tf, as_of_ms=stale_as_of)
    book.process_symbol("LNG/USDT", bars_by_tf=bars, statuses=statuses, as_of_ms=stale_as_of,
                        universe_entry=sc.universe["entries"]["LNG/USDT"],
                        price=sc.price.mark("LNG/USDT", now_ms=CLOCK[0]),
                        liquidity=lambda: sc.price.liquidity("LNG/USDT"), run_id="test-run",
                        decision_ms=CLOCK[0])
    assert plan["status"] == PL_EXPIRED, (plan["status"], plan.get("reasons"))
    assert "LNG/USDT" not in book.ledger.positions, "bayat tur referansıyla süresi dolmuş plan açıldı"


# ====================================================================== FAIL-CLOSED: okunamayan geçerlilik alanı
@pytest.mark.parametrize("bozuk", [None, "", "lorem", float("nan"), -1])
def test_an_unreadable_expiry_field_is_treated_as_expired_not_as_never_expiring(tmp_path: Path, bozuk):
    """Karşıt doğrulama bulgusu: `is_expired` istisnayı yutup False dönüyordu — bozuk/eski bir `plans.json`
    kaydı SÜRESİZ emir hakkı kazanıyordu. Artık okunamayan alan GEÇERSİZ sayılır (fail-closed)."""
    from tradingbot.pattern_trader.book import PatternBook
    assert PatternBook.is_expired({"expires_at_ms": bozuk}, 0) is True, bozuk
    assert PatternBook.is_expired({}, 0) is True, "alan hiç yoksa da geçersiz"
    # sınır ve normal davranış korunur
    assert PatternBook.is_expired({"expires_at_ms": 1_780_000_000_000}, 1_780_000_000_000) is False
    assert PatternBook.is_expired({"expires_at_ms": 1_780_000_000_001}, 1_780_000_000_000) is False
    assert PatternBook.is_expired({"expires_at_ms": 1_780_000_000_000}, 1_780_000_000_001) is True
    # ALAN ADI `_ms`: sayısal değer DOĞRUDAN milisaniyedir; saniye/ms sezgisi UYGULANMAZ (küçük bir sayıyı
    # saniye sanmak geçerlilik penceresini 1000 kat uzatırdı — dağıtım betiğinin değişmez kapısı yakaladı).
    assert PatternBook.is_expired({"expires_at_ms": 100}, 100) is False
    assert PatternBook.is_expired({"expires_at_ms": 100}, 101) is True
    # ISO metin eski kayıtlar için geri düşüş olarak çözülür
    assert PatternBook.is_expired({"expires_at_ms": "2026-05-28T08:00:00+00:00"}, 1_780_000_000_000) is True


def test_a_corrupt_persisted_plan_cannot_open_after_a_restart(tmp_path: Path):
    """Uçtan uca: `plans.json` içindeki geçerlilik alanı bozulmuşsa plan emir AÇAMAZ."""
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    plan["expires_at_ms"] = "bozuk"
    CLOCK[0] = int(plan.get("triggered_at_ms") or CLOCK[0]) + M15
    _set_mark(p, "LNG/USDT", rows15[40]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert plan["status"] == PL_EXPIRED and "LNG/USDT" not in book.ledger.positions, (plan["status"], plan.get("reasons"))


# ====================================================================== TERMİNAL plan yeniden ele alınmaz
def test_a_plan_cancelled_because_the_sibling_filled_is_not_relabelled_expired(tmp_path: Path):
    """Karşıt doğrulama bulgusu: `_try_open` planın mevcut durumunu hiç okumuyordu; kardeş plan dolduğu için
    CANCELLED olan plan aynı turda yeniden işlenip PL_EXPIRED'e çevriliyor ve HEM `cancelled` HEM `expired`
    sayaçlarına giriyordu. Terminal plan artık dokunulmadan bırakılır."""
    from tradingbot.pattern_trader.strategy import PL_CANCELLED
    p, sc, book, plan, rows15 = _triggered_plan_waiting_for_price(tmp_path)
    book._set_status(plan, PL_CANCELLED, CLOCK[0], "OTHER_PLAN_FILLED:x")
    book.counters["cancelled"] += 1
    c0, e0 = book.counters["cancelled"], book.counters["expired"]
    CLOCK[0] = int(plan["expires_at_ms"]) + M15                # süre dolmuş: eski kod burada EXPIRED'e çevirirdi
    res = book._try_open(plan, now=sc._now_dt(), as_of_ms=CLOCK[0], price=sc.price.mark("LNG/USDT", now_ms=CLOCK[0]),
                         statuses={}, liquidity=lambda: None, universe_entry={}, decision_ms=CLOCK[0])
    assert res == PL_CANCELLED and plan["status"] == PL_CANCELLED, (res, plan["status"])
    assert (book.counters["cancelled"], book.counters["expired"]) == (c0, e0), "terminal plan sayaçları şişirdi"

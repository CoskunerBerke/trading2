"""FUNDING SETTLEMENT V1 — kaçırılan HER settlement kendi oranını (ve o andaki mark'ını) alır.

Onarılan kusur: canlı tur yolu `static_rates(anlık_oran)` veriyordu; `static_rates` settlement
zamanını YOK SAYAR, dolayısıyla 15 dönemlik bir boşlukta 15 settlement'ın hepsi aynı anlık oranı ve
aynı anlık mark'ı alıyordu. Üretim defterindeki 29 kapanmış işlem gerçek Binance `fundingRate`
geçmişiyle uzlaştırıldığında kayıtlı funding +0,067382 USDT, gerçek +0,004014 USDT çıktı.

KIRMIZI OLDUĞU DOĞRULANDI (geçici revert ile ölçüldü, 2026-09-09):
  * `accrue` tek mark'a döndürülünce: `test_per_settlement_mark_is_used_when_known`,
    `test_single_settlement_is_unchanged_or_more_accurate`,
    `test_venue_unreachable_keeps_cache_and_falls_back_to_snapshot` BAŞARISIZ.
  * Motor kablolaması `static_rates(funding)`'a döndürülünce:
    `test_engine_tour_uses_per_settlement_rates_not_the_snapshot` BAŞARISIZ.
DÜRÜSTLÜK NOTU: `accrue` ESKİDEN DE lookup'ı settlement başına çağırıyordu; zaman-duyarlı bir
lookup verilseydi doğru çalışırdı. Asıl kusur ÇAĞIRANDAYDI (motor `static_rates` veriyordu).
Bu yüzden `test_per_settlement_rates_beat_static_snapshot` eski `accrue`'ya karşı KIRMIZI DEĞİLDİR
— o testin kanıtladığı şey, iki lookup semantiğinin ölçülebilir biçimde farklı defter ürettiğidir;
kablolamanın kanıtı motor testidir.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingbot.accounting import (
    AmountType,
    FundingQuote,
    FundingSchedule,
    FuturesLedgerV2,
    LedgerKind,
    MarketType,
    PositionSide,
    SizeSpec,
    SlippageModel,
    SymbolFilters,
    TickData,
    chained_rates,
    static_rates,
)
from tradingbot.market.funding_rates import FundingRateCache, hour_key

D = Decimal
UTC = timezone.utc
ETH = "ETH/USDT"


def _led(equity="1000", **kw) -> FuturesLedgerV2:
    return FuturesLedgerV2(equity, slippage=SlippageModel.zero(), **kw)


def _f() -> SymbolFilters:
    return SymbolFilters(symbol=ETH, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                         min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _open(led: FuturesLedgerV2, side: str, at: datetime, price="3000", notional=300, lev=2):
    return led.open(ETH, side, price, SizeSpec(notional, AmountType.NOTIONAL, lev), stop=None, filters=_f(), now=at)


# gerçek Binance biçimine yakın: her settlement AYRI oran, AYRI mark
OPEN_AT = datetime(2026, 8, 18, 7, 30, tzinfo=UTC)
NOW = datetime(2026, 8, 19, 17, 0, tzinfo=UTC)          # 08,16,00,08,16 → 5 settlement
SETTLEMENTS = [datetime(2026, 8, 18, 8, tzinfo=UTC), datetime(2026, 8, 18, 16, tzinfo=UTC),
               datetime(2026, 8, 19, 0, tzinfo=UTC), datetime(2026, 8, 19, 8, tzinfo=UTC),
               datetime(2026, 8, 19, 16, tzinfo=UTC)]
RATES = [D("0.0001"), D("-0.00025"), D("0.00005"), D("0.00015"), D("-0.0001")]      # toplam −0,00005
MARKS = [D("3010"), D("2950"), D("3100"), D("2900"), D("3200")]
SNAPSHOT_RATE = D("0.0002")          # "şu anki" oran — eski kodun 5 dönemin HEPSİNE uyguladığı değer


def _history_lookup(with_marks: bool = True):
    table = {t: (r, m) for t, r, m in zip(SETTLEMENTS, RATES, MARKS)}

    def _lookup(symbol: str, when: datetime):
        row = table.get(when)
        if row is None:
            return None
        return FundingQuote(rate=row[0], mark=row[1] if with_marks else None)
    return _lookup


# --------------------------------------------------------------------------- 1) her settlement kendi oranını alır
def test_settlements_due_covers_every_missed_period():
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    assert FundingSchedule().settlements_due(pos, NOW) == SETTLEMENTS


def test_per_settlement_rates_beat_static_snapshot():
    """İki lookup semantiğinin ÖLÇÜLEBİLİR farkı: `static_rates` beş döneme de aynı oranı uygular.

    (Bu test eski `accrue` koduna karşı kırmızı DEĞİLDİR — bkz. dosya başlığındaki dürüstlük notu.)
    """
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    events = FundingSchedule().accrue(pos, NOW, D("3000"), _history_lookup(with_marks=False))

    assert [e.rate for e in events] == RATES                    # her dönem KENDİ oranı
    assert [e.ts[:16] for e in events] == [t.isoformat()[:16] for t in SETTLEMENTS]
    assert not any(e.estimated for e in events)

    # aynı pozisyon eski yoldan: tek anlık oran → beş özdeş dönem
    led_old = _led()
    pos_old = _open(led_old, "LONG", OPEN_AT)
    old = FundingSchedule().accrue(pos_old, NOW, D("3000"), static_rates({ETH: SNAPSHOT_RATE}))
    assert [e.rate for e in old] == [SNAPSHOT_RATE] * 5
    assert {e.rate for e in events} != {e.rate for e in old}     # yeni davranış eskisinden FARKLI

    # ekonomik fark ölçülebilir: net funding işaret bile değiştirir
    qty = pos.qty
    want = sum(-(qty * D("3000") * r) for r in RATES)            # LONG: pozitif oran ÖDER
    got = sum(e.amount for e in events)
    assert got == want
    assert got > 0 > sum(e.amount for e in old)                  # gerçek oranlar net ALACAK, snapshot net BORÇ


def test_per_settlement_mark_is_used_when_known():
    """Mark da settlement'a ait olmalı; bilinmiyorsa çağıranın anlık mark'ına düşülür."""
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    events = FundingSchedule().accrue(pos, NOW, D("3000"), _history_lookup(with_marks=True))
    assert [e.mark for e in events] == MARKS
    qty = pos.qty
    assert [e.amount for e in events] == [-(qty * m * r) for m, r in zip(MARKS, RATES)]

    led2 = _led()
    pos2 = _open(led2, "LONG", OPEN_AT)
    no_mark = FundingSchedule().accrue(pos2, NOW, D("3000"), _history_lookup(with_marks=False))
    assert [e.mark for e in no_mark] == [D("3000")] * 5          # mark yoksa eski davranış


def test_scalar_lookup_still_supported():
    """`static_rates` ve `ops/gap.py` gibi SKALER dönen çağıranlar birebir eskisi gibi çalışır."""
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    events = FundingSchedule().accrue(pos, NOW, D("3000"), lambda s, w: 0.0001)
    assert len(events) == 5 and all(e.rate == D("0.0001") and e.mark == D("3000") for e in events)


def test_single_settlement_is_unchanged_or_more_accurate():
    """En sık hal: TEK dönem. Zincir → gerçek oran; kaynak yoksa snapshot (eski davranış birebir)."""
    now1 = datetime(2026, 8, 18, 8, 30, tzinfo=UTC)
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    ev = FundingSchedule().accrue(pos, now1, D("3000"), chained_rates(_history_lookup(), static_rates({ETH: SNAPSHOT_RATE})))
    assert len(ev) == 1 and ev[0].rate == RATES[0] and ev[0].mark == MARKS[0]

    led2 = _led()
    pos2 = _open(led2, "LONG", OPEN_AT)
    ev2 = FundingSchedule().accrue(pos2, now1, D("3000"), chained_rates(lambda s, w: None, static_rates({ETH: SNAPSHOT_RATE})))
    assert len(ev2) == 1 and ev2[0].rate == SNAPSHOT_RATE and ev2[0].mark == D("3000")
    assert ev2[0].estimated is False


# --------------------------------------------------------------------------- 2) fail-closed
def test_fail_closed_unresolvable_rate_waits_and_writes_nothing():
    """Oran GERÇEKTEN çözülemiyorsa: kayıt YOK, watermark İLERLEMEZ, sessiz sıfır YOK."""
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    before = pos.last_funding_settlement_utc
    assert not pos.meta.get("last_funding_rate")
    n_entries = len(led.entries)

    events = FundingSchedule().accrue(pos, NOW, D("3000"), lambda s, w: None)
    assert events == []
    assert pos.last_funding_settlement_utc == before
    assert pos.funding_paid == 0 and pos.funding_received == 0
    assert len(led.entries) == n_entries

    # aynı şey defter tick'i üzerinden: FUNDING kaydı YAZILMAZ
    led.tick({ETH: TickData(last=D("3000"), mark=D("3000"))}, now_utc=NOW, funding_rate_lookup=lambda s, w: None)
    assert [e for e in led.entries if e.kind is LedgerKind.FUNDING] == []
    assert led.positions[ETH].last_funding_settlement_utc == before
    assert led.total_funding == 0


def test_fail_closed_stops_at_first_gap_and_later_periods_wait():
    """İlk iki dönem çözülür, üçüncü çözülemez → 3. ve SONRASI bekler, watermark 2. dönemde durur."""
    table = {SETTLEMENTS[0]: RATES[0], SETTLEMENTS[1]: RATES[1]}
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    sched = FundingSchedule(fallback_to_last_known=False)        # "son bilinen oran" yedeği KAPALI
    events = sched.accrue(pos, NOW, D("3000"), lambda s, w: table.get(w))
    assert [e.rate for e in events] == RATES[:2]
    assert pos.last_funding_settlement_utc.startswith("2026-08-18T16:00")

    # boşluk kapanınca kalan dönemler AYNEN ve BİR KEZ işlenir
    rest = sched.accrue(pos, NOW, D("3000"), _history_lookup(with_marks=False))
    assert [e.rate for e in rest] == RATES[2:]
    assert pos.last_funding_settlement_utc.startswith("2026-08-19T16:00")


def test_corrupt_quote_is_treated_as_unresolvable_not_zero():
    """Bozuk/çözülemeyen dönüş sessizce 0 oran DEĞİL, 'bilinmiyor' sayılır."""
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    events = FundingSchedule().accrue(pos, NOW, D("3000"), lambda s, w: "not-a-number")
    assert events == [] and pos.last_funding_settlement_utc == pos.opened_at


# --------------------------------------------------------------------------- 3) çift tahakkuk yok
def test_no_double_accrual_on_repeated_ticks():
    led = _led()
    _open(led, "LONG", OPEN_AT)
    look = _history_lookup()
    tick = {ETH: TickData(last=D("3000"), mark=D("3000"))}

    led.tick(tick, now_utc=NOW, funding_rate_lookup=look)
    first = [e for e in led.entries if e.kind is LedgerKind.FUNDING]
    total_after_first = led.total_funding
    wallet_after_first = led.wallet_balance
    assert len(first) == 5

    for _ in range(3):                                          # aynı `now` ile tekrar tekrar
        led.tick(tick, now_utc=NOW, funding_rate_lookup=look)
    assert [e for e in led.entries if e.kind is LedgerKind.FUNDING] == first
    assert led.total_funding == total_after_first and led.wallet_balance == wallet_after_first

    # kaydet → yükle → tekrar tick: watermark kalıcı, yine çift kayıt yok
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ledger.json"
        led.save(p)
        led2 = FuturesLedgerV2.load(p, starting_equity="1000")
        led2.tick(tick, now_utc=NOW, funding_rate_lookup=look)
        assert len([e for e in led2.entries if e.kind is LedgerKind.FUNDING]) == 5
        assert led2.total_funding == total_after_first


# --------------------------------------------------------------------------- 4) işaret sözleşmesi
@pytest.mark.parametrize("side,rate,expect_sign", [
    ("LONG", D("0.0003"), -1),      # rate>0 → LONG ÖDER
    ("SHORT", D("0.0003"), +1),     # rate>0 → SHORT ALIR
    ("LONG", D("-0.0003"), +1),
    ("SHORT", D("-0.0003"), -1),
])
def test_sign_convention(side, rate, expect_sign):
    at = datetime(2026, 8, 18, 7, 30, tzinfo=UTC)
    now = datetime(2026, 8, 18, 8, 30, tzinfo=UTC)
    led = _led()
    pos = _open(led, side, at)
    ev = FundingSchedule().accrue(pos, now, D("3000"), lambda s, w: FundingQuote(rate=rate, mark=D("3000")))
    assert len(ev) == 1
    amount = ev[0].amount
    assert (amount > 0) if expect_sign > 0 else (amount < 0)
    assert abs(amount) == pos.qty * D("3000") * abs(rate)
    if expect_sign > 0:
        assert pos.funding_received == abs(amount) and pos.funding_paid == 0
    else:
        assert pos.funding_paid == abs(amount) and pos.funding_received == 0
    assert ev[0].side == PositionSide[side].value


# --------------------------------------------------------------------------- 5) önbellek / venue erişilemezliği
class _Provider:
    """`funding_history` sunan asgari sahte sağlayıcı."""

    def __init__(self, rows=None, exc=None):
        self.rows = rows if rows is not None else []
        self.exc = exc
        self.calls: list[tuple] = []

    def funding_history(self, symbol, limit=100, start_ms=None, end_ms=None):
        self.calls.append((symbol, start_ms, end_ms))
        if self.exc is not None:
            raise self.exc
        return list(self.rows)


def _rows():
    return [{"symbol": "ETHUSDT", "funding_ts": int(t.timestamp() * 1000), "rate": float(r), "mark": float(m)}
            for t, r, m in zip(SETTLEMENTS, RATES, MARKS)]


def test_cache_lookup_returns_real_rate_and_mark(tmp_path):
    c = FundingRateCache(tmp_path / "fr.json")
    prov = _Provider(_rows())
    res = c.ensure(lambda: prov, {ETH: SETTLEMENTS}, now=NOW.timestamp())
    assert res["ok"] and res["added"] == 5 and len(prov.calls) == 1
    for t, r, m in zip(SETTLEMENTS, RATES, MARKS):
        q = c.lookup(ETH, t)
        assert q is not None and q.rate == r and q.mark == m
    assert c.lookup(ETH, datetime(2026, 8, 20, 0, tzinfo=UTC)) is None      # kapsam dışı → None

    # ikinci `ensure` AĞA ÇIKMAZ (hepsi kapsanıyor)
    res2 = c.ensure(lambda: prov, {ETH: SETTLEMENTS}, now=NOW.timestamp())
    assert res2.get("skipped") == "kapsanıyor" and len(prov.calls) == 1

    # kalıcı: yeni nesne aynı dosyadan okur
    c2 = FundingRateCache(tmp_path / "fr.json")
    assert c2.lookup(ETH, SETTLEMENTS[0]).rate == RATES[0]


def test_cache_is_robust_to_off_by_milliseconds_funding_time(tmp_path):
    c = FundingRateCache(tmp_path / "fr.json")
    ts = int(SETTLEMENTS[0].timestamp() * 1000) - 1              # 07:59:59.999
    prov = _Provider([{"symbol": "ETHUSDT", "funding_ts": ts, "rate": 0.0001, "mark": 3010.0}])
    c.ensure(lambda: prov, {ETH: [SETTLEMENTS[0]]}, now=NOW.timestamp())
    assert c.lookup(ETH, SETTLEMENTS[0]).rate == D("0.0001")
    assert hour_key(ts) == hour_key(SETTLEMENTS[0])


def test_venue_unreachable_keeps_cache_and_falls_back_to_snapshot(tmp_path):
    """Venue erişilemez: tur düşmez, önbellek korunur, davranış ESKİSİYLE aynı (snapshot oranı)."""
    c = FundingRateCache(tmp_path / "fr.json", retry_cooldown_s=3600)
    good = _Provider(_rows()[:1])                               # yalnız ilk settlement önbelleğe girer
    c.ensure(lambda: good, {ETH: SETTLEMENTS[:1]}, now=NOW.timestamp())

    dead = _Provider(exc=ConnectionError("fapi unreachable"))
    res = c.ensure(lambda: dead, {ETH: SETTLEMENTS}, now=NOW.timestamp())
    assert res["ok"] is False and len(dead.calls) == 1
    assert c.lookup(ETH, SETTLEMENTS[0]).rate == RATES[0]        # eski kayıt EZİLMEDİ

    # cooldown: aynı sembol yeniden denenmez → arızalı venue turu bloklamaz
    res2 = c.ensure(lambda: dead, {ETH: SETTLEMENTS}, now=NOW.timestamp() + 60)
    assert len(dead.calls) == 1 and res2["cooldown"] == ["ETHUSDT"]
    # cooldown dolunca yeniden denenir
    c.ensure(lambda: dead, {ETH: SETTLEMENTS}, now=NOW.timestamp() + 4000)
    assert len(dead.calls) == 2

    # zincir: önbellekte olan gerçek oranı, olmayan snapshot'ı alır → eski davranıştan KÖTÜ değil
    led = _led()
    pos = _open(led, "LONG", OPEN_AT)
    ev = FundingSchedule().accrue(pos, NOW, D("3000"), chained_rates(c.lookup, static_rates({ETH: SNAPSHOT_RATE})))
    assert [e.rate for e in ev] == [RATES[0]] + [SNAPSHOT_RATE] * 4
    assert [e.mark for e in ev] == [MARKS[0]] + [D("3000")] * 4


def test_empty_or_incomplete_history_does_not_hammer_the_venue(tmp_path):
    """Kayıt yayımlanmamışsa/eksikse KISA soğuma: her turda aynı istek TEKRARLANMAZ."""
    c = FundingRateCache(tmp_path / "fr.json", retry_cooldown_s=120)
    empty = _Provider([])
    r = c.ensure(lambda: empty, {ETH: SETTLEMENTS}, now=NOW.timestamp())
    assert r["ok"] and r["results"][0].get("empty") and len(empty.calls) == 1
    c.ensure(lambda: empty, {ETH: SETTLEMENTS}, now=NOW.timestamp() + 30)
    assert len(empty.calls) == 1                                 # soğumada
    c.ensure(lambda: empty, {ETH: SETTLEMENTS}, now=NOW.timestamp() + 200)
    assert len(empty.calls) == 2                                 # soğuma bitti

    # kısmi cevap: satır geldi ama İSTENEN dönem yok → yine kısa soğuma
    c2 = FundingRateCache(tmp_path / "fr2.json", retry_cooldown_s=120)
    partial = _Provider(_rows()[:1])
    r2 = c2.ensure(lambda: partial, {ETH: SETTLEMENTS}, now=NOW.timestamp())
    assert r2["results"][0].get("still_missing") and c2.lookup(ETH, SETTLEMENTS[0]) is not None
    c2.ensure(lambda: partial, {ETH: SETTLEMENTS}, now=NOW.timestamp() + 30)
    assert len(partial.calls) == 1


def test_provider_factory_not_called_when_nothing_missing(tmp_path):
    c = FundingRateCache(tmp_path / "fr.json")
    c.ensure(lambda: _Provider(_rows()), {ETH: SETTLEMENTS}, now=NOW.timestamp())

    def _boom():
        raise AssertionError("eksik dönem yokken sağlayıcı YARATILMAMALI")
    assert c.ensure(_boom, {ETH: SETTLEMENTS}, now=NOW.timestamp())["ok"]


def test_provider_factory_failure_is_contained(tmp_path):
    c = FundingRateCache(tmp_path / "fr.json")

    def _boom():
        raise RuntimeError("sağlayıcı kurulamadı")
    res = c.ensure(_boom, {ETH: SETTLEMENTS}, now=NOW.timestamp())
    assert res["ok"] is False and "sağlayıcı açılamadı" in res["error"]
    assert c.lookup(ETH, SETTLEMENTS[0]) is None


def test_corrupt_cache_file_is_ignored(tmp_path):
    p = tmp_path / "fr.json"
    p.write_text("{ bozuk", encoding="utf-8")
    c = FundingRateCache(p)
    assert c.size == 0 and c.lookup(ETH, SETTLEMENTS[0]) is None


def test_cache_prunes_old_settlements(tmp_path):
    p = tmp_path / "fr.json"
    c = FundingRateCache(p, max_age_days=1)
    old = datetime(2026, 7, 1, 8, tzinfo=UTC)
    prov = _Provider([{"symbol": "ETHUSDT", "funding_ts": int(old.timestamp() * 1000), "rate": 0.0001, "mark": 3000.0},
                      {"symbol": "ETHUSDT", "funding_ts": int(SETTLEMENTS[-1].timestamp() * 1000), "rate": 0.0002, "mark": 3100.0}])
    c.ensure(lambda: prov, {ETH: [old, SETTLEMENTS[-1]]}, now=NOW.timestamp())
    doc = json.loads(p.read_text(encoding="utf-8"))
    keys = {int(k) for k in doc["rates"]["ETHUSDT"]}
    assert hour_key(SETTLEMENTS[-1]) in keys and hour_key(old) not in keys


# --------------------------------------------------------------------------- 6) MOTOR KABLOLAMASI (asıl kusurun yeri)
def _seeded_rows(start: datetime, end: datetime) -> list[dict]:
    """Aralıktaki her 00/08/16 UTC settlement'ı için BİRBİRİNDEN FARKLI oran/mark üretir."""
    from tradingbot.core import funding_settlements_between
    rows = []
    for t in funding_settlements_between(start, end):
        k = hour_key(t)
        rows.append({"symbol": "ETHUSDT", "funding_ts": int(t.timestamp() * 1000),
                     "rate": float(Decimal(k % 97 + 1) / Decimal(1_000_000)), "mark": float(3000 + k % 53)})
    return rows


def test_engine_tour_uses_per_settlement_rates_not_the_snapshot(tmp_path, monkeypatch):
    """ESKİ KABLOLAMAYA KARŞI KIRMIZI.

    Motor eskiden `static_rates(anlık_funding)` veriyordu; `static_rates` settlement zamanını YOK
    SAYAR, dolayısıyla kaçırılan bütün dönemler TEK bir orana kaydediliyordu. Bu test kaçırılan her
    dönemin KENDİ oranını ve kendi mark'ını aldığını defter kayıtlarından doğrular.
    """
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent))
    import test_engine_v3 as E                                   # ağsız motor kurulumu

    from tradingbot.core import from_iso, funding_settlements_between, iso, utc_now

    eng = E._engine(tmp_path, monkeypatch, symbols=["ETH/USDT"], equity=1000)
    now0 = utc_now()
    opened_at = now0 - timedelta(hours=40)

    pos = eng.ledger2.open(ETH, "LONG", "3000", SizeSpec(300, AmountType.NOTIONAL, 2), stop=None,
                           filters=_f(), now=opened_at)
    assert pos is not None, eng.ledger2.last_reject_reason
    qty0 = pos.qty                                               # tur pozisyonu kapatabilir (likidasyon/stop)
    # önbelleği ağsız doldur: geçmiş 48 saatteki HER settlement ayrı oran/mark alır
    prov = _Provider(_seeded_rows(now0 - timedelta(hours=48), now0 + timedelta(hours=1)))
    eng.funding_rates.refresh(prov, ETH, now0 - timedelta(hours=48), now0)

    def _no_network():
        raise AssertionError("önbellek kapsıyorken AĞA ÇIKILMAMALI")
    eng._funding_provider_factory_override = _no_network

    eng.tour(do_scan=False, obsidian=False, charts=False)

    fund = [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING and e.ref_id == pos.id]
    due = funding_settlements_between(from_iso(iso(opened_at)), utc_now())
    assert len(fund) == len(due) >= 4                             # 40 saat → en az 4 settlement
    assert len({e.note for e in fund}) == len(fund)               # her kaydın oranı FARKLI (eski yolda hepsi aynıydı)
    by_hour = {e.ts[:13]: e for e in fund}
    for t in due:
        q = eng.funding_rates.lookup(ETH, t)
        assert q is not None and q.mark is not None
        e = by_hour[iso(t)[:13]]
        assert e.note == f"funding rate={q.rate}"                 # tahmini DEĞİL (" est" yok), gerçek dönem oranı
        assert e.amount == -(qty0 * q.mark * q.rate)              # LONG: rate>0 öder; mark da O settlement'ın


# --------------------------------------------------------------------------- 7) zincir davranışı
def test_chained_rates_first_non_none_wins_and_survives_exceptions():
    def boom(sym, when):
        raise RuntimeError("kaynak arızası")

    chain = chained_rates(boom, lambda s, w: None, lambda s, w: D("0.0007"))
    assert chain(ETH, SETTLEMENTS[0]) == D("0.0007")
    assert chained_rates(boom, lambda s, w: None)(ETH, SETTLEMENTS[0]) is None
    assert chained_rates(None, lambda s, w: D("1"))(ETH, SETTLEMENTS[0]) == D("1")

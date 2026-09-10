"""FUNDING BUTUNLUGU V2 + PROVENANS/YENILEME MUTASYON KAPILARI.

Bagimsiz incelemenin (2026-09-10) acik biraktigi dort bulgu, GERCEK motor/monitor/tur yollarindan
sinaniyor — yardimci fonksiyon ya da kaynak metni kontrolu DEGIL:

* **D1** Cikis monitoru (60 sn) ile ana tur (15 dk) ayni settlement olayini AYNI kuralla isler.
  Dogrulanmamis oran watermark'i ilerletemez, dolayisiyla olayi kaybettiremez.
* **D2** Settlement zamanlari venue'nun GERCEK kayitlarindan gelir; sabit 00/08/16 grid'i yok.
  4 saatlik bir sozlesme 8 saatlik grid'in gormedigi donemleri de tahakkuk ettirir.
* **D4** Kaynaktan DOGRULANMIS sifir oran ile eksik verinin varsayilan sifiri ayridir.
* **D3/M9** Turdan `ensure_funding_rates` cikarilirsa onbellek hic dolmaz ve funding tamamen olur;
  bu dosyadaki tur testi o mutasyonu DUSURUR.
* **D3/M2** `engine_v3._marks` provenansi bos birakirsa butun bar uclari sessizce dusurulur;
  asagidaki `_marks` -> defter testi o mutasyonu DUSURUR.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.accounting import AmountType, LedgerKind, SizeSpec, TickData  # noqa: E402
from tradingbot.accounting.funding import FundingSchedule  # noqa: E402
from tradingbot.accounting.models import MarketType, Position, PositionSide, TradeRecord  # noqa: E402
from tradingbot.market.funding_rates import FundingRateCache  # noqa: E402

D = Decimal
UTC = timezone.utc
ETH = "ETH/USDT"


class _Provider:
    """`funding_history` sozlesmesini karsilayan agsiz saglayici; cagri sayisini sayar."""

    def __init__(self, rows, exc: Exception | None = None):
        self.rows, self.exc, self.calls = rows, exc, []

    def funding_history(self, symbol, limit=100, start_ms=None, end_ms=None):
        self.calls.append((symbol, start_ms, end_ms))
        if self.exc is not None:
            raise self.exc
        return [r for r in self.rows
                if (start_ms is None or r["funding_ts"] >= start_ms)
                and (end_ms is None or r["funding_ts"] <= end_ms)]


def _rows(start: datetime, end: datetime, interval_h: int, rate: str = "0.0001", mark: str = "3000"):
    """Venue kaydi: `interval_h` saatte BIR settlement (4 ve 1 saatlik sozlesmeler dahil)."""
    t = start.replace(minute=0, second=0, microsecond=0)
    out = []
    while t <= end:
        if t.hour % interval_h == 0 and t >= start:
            out.append({"symbol": "ETHUSDT", "funding_ts": int(t.timestamp() * 1000),
                        "rate": float(rate), "mark": float(mark)})
        t += timedelta(hours=1)
    return out


def _pos(opened_at: datetime, qty="0.01", entry="3000") -> Position:
    return Position(id="F00001", symbol=ETH, market_type=MarketType.USDM_PERP, side=PositionSide.LONG,
                    qty=D(qty), entry_avg=D(entry), opened_at=opened_at.isoformat(),
                    last_funding_settlement_utc=opened_at.isoformat())


# ============================================================ D2 — gercek settlement zamanlari
def test_four_hour_contract_accrues_every_real_settlement_not_the_8h_grid(tmp_path):
    """4 saatlik sozlesme: 24 saatte 6 settlement. Sabit 00/08/16 grid'i yalniz 3 gorurdu."""
    start = datetime(2026, 9, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=24)
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Provider(_rows(start, end, interval_h=4)), ETH, start, end)

    sched = FundingSchedule(settlement_source=cache.settlements_in)
    pos = _pos(start)
    due = sched.settlements_due(pos, end)
    assert [t.hour for t in due] == [4, 8, 12, 16, 20, 0]      # 6 donem
    grid = FundingSchedule().settlements_due(pos, end)          # eski sabit grid
    assert [t.hour for t in grid] == [8, 16, 0]                 # yalniz 3 — ölçülen D2 boslugu

    ev = sched.accrue(pos, end, D("3000"), cache.lookup)
    assert len(ev) == 6 and all(e.verified for e in ev)
    assert pos.last_funding_settlement_utc == due[-1].isoformat()


def test_one_hour_contract_is_also_covered(tmp_path):
    start = datetime(2026, 9, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=6)
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Provider(_rows(start, end, interval_h=1)), ETH, start, end)
    sched = FundingSchedule(settlement_source=cache.settlements_in)
    pos = _pos(start)
    assert len(sched.settlements_due(pos, end)) == 6
    assert len(sched.accrue(pos, end, D("3000"), cache.lookup)) == 6


# ============================================================ D4 — dogrulanmis sifir
def test_verified_zero_rate_settles_and_is_recorded(tmp_path):
    """Kaynaktan DOGRULANMIS sifir: donem KAPANIR ve olay olarak KAYDA GECER."""
    start = datetime(2026, 9, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=9)
    cache = FundingRateCache(tmp_path / "fr.json")
    cache.refresh(_Provider(_rows(start, end, interval_h=8, rate="0.0")), ETH, start, end)
    sched = FundingSchedule(settlement_source=cache.settlements_in)
    pos = _pos(start)
    ev = sched.accrue(pos, end, D("3000"), cache.lookup)
    assert len(ev) == 1
    assert ev[0].zero_rate is True and ev[0].verified is True and ev[0].amount == 0
    assert pos.last_funding_settlement_utc == datetime(2026, 9, 8, 8, tzinfo=UTC).isoformat()


def test_unverified_default_zero_settles_nothing():
    """Eksik verinin varsayilan sifiri dogrulanmis sifirla AYNI SEY DEGILDIR.

    Uretimde uc canli pozisyon `meta.last_funding_rate = "0.0"` tasiyordu; eski kod bu degerle
    donemleri olaysiz KAPATIYOR ve watermark'i ilerletiyordu (bagimsiz inceleme D4).
    """
    start = datetime(2026, 9, 8, 0, tzinfo=UTC)
    for lookup in (lambda s, w: None,
                   lambda s, w: D("0"),
                   lambda s, w: {"rate": 0.0},
                   lambda s, w: {"rate": 0.0, "verified": False}):
        pos = _pos(start)
        pos.meta["last_funding_rate"] = "0.0"
        sched = FundingSchedule()
        assert sched.accrue(pos, start + timedelta(hours=9), D("3000"), lookup) == []
        assert pos.last_funding_settlement_utc == start.isoformat()      # watermark DURDU
        assert sched.pending_settlements >= 1


# ============================================================ D1 — monitor ile tur ayni kural
def _engine_with_position(tmp_path, monkeypatch, opened_hours_ago: float = 30.0):
    """Tur/monitor boyunca ACIK KALAN bir pozisyon: giris canli fiyata esit, stop cok uzakta.

    Aksi halde `exit_check` pozisyonu ilk cagride kapatir ve funding sozlesmesi hic sinanmaz.
    """
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000, seed_funding=False)  # SOGUK onbellek: konusu kapsamanin YOKLUGU
    px = float(((eng.runner.live.snapshot(ETH) or {}).get("ticker") or {}).get("last") or 0)
    assert px > 0, "sahte canli fiyat yok"
    now0 = datetime.now(UTC).replace(microsecond=0)
    opened = now0 - timedelta(hours=opened_hours_ago)
    pos = eng.ledger2.open(ETH, "LONG", D(str(px)), SizeSpec(300, AmountType.NOTIONAL, 1),
                           stop=D(str(px * 0.2)), targets=[D(str(px * 5))], now=opened)
    assert pos is not None, eng.ledger2.last_reject_reason
    return eng, pos, now0, opened


def test_exit_monitor_cannot_consume_a_settlement_without_a_verified_rate(tmp_path, monkeypatch):
    """D1 REGRESYON KAPISI — GERCEK `exit_check` yolu.

    Onbellek bosken cikis monitoru bir settlement sinirini gecer. Eski kodda `meta.last_funding_rate`
    ile tahmini tahakkuk yapilir, watermark ilerler ve tur gercek orani HIC sormazdi. Artik monitor
    hicbir seyi kapatmaz; tur gercek oranla TAM BIR KEZ kapatir.
    """
    eng, pos, now0, opened = _engine_with_position(tmp_path, monkeypatch)
    pos.meta["last_funding_rate"] = "0.005"                 # tahmini yedek ELDE
    before = pos.last_funding_settlement_utc

    eng.exit_check()                                        # AGA CIKMAZ, onbellek BOS
    assert pos.last_funding_settlement_utc == before, "cikis monitoru dogrulanmadan donemi tuketti"
    assert pos.funding_paid == 0 and pos.funding_received == 0
    assert not [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING]

    # Tur: gercek oranlari ceker ve donemleri kapatir
    prov = _Provider(_rows(now0 - timedelta(hours=48), now0, interval_h=8, rate="0.0001"))
    eng._funding_provider_factory_override = lambda: prov
    eng.tour(do_scan=False, obsidian=False, charts=False)

    fund = [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING]
    assert fund, "tur gercek oranla tahakkuk etmedi"
    assert pos.last_funding_settlement_utc > before
    n_first = len(fund)

    # Ikinci tur: ayni donemler TEKRAR uygulanmaz (restart/retry cift tahakkuk kapisi)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert len([e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING]) == n_first


def test_tour_without_ensure_funding_rates_would_accrue_nothing(tmp_path, monkeypatch):
    """D3/M9 MUTASYON KAPISI — turdan `ensure_funding_rates` cikarilirsa bu test DUSER.

    Onbellek bos baslar; yalnizca turun yenilemesi onu doldurabilir. Yenileme yoksa dogrulanmis
    oran hic olusmaz ve funding tamamen olur — sessizce.
    """
    eng, pos, now0, _ = _engine_with_position(tmp_path, monkeypatch)
    assert eng.funding_rates.size == 0
    prov = _Provider(_rows(now0 - timedelta(hours=48), now0, interval_h=8, rate="0.0001"))
    eng._funding_provider_factory_override = lambda: prov

    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert prov.calls, "tur venue'ya HIC sormadi -> ensure_funding_rates kaldirilmis olabilir"
    assert eng.funding_rates.size > 0, "onbellek dolmadi"
    assert [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING], "funding tahakkuk etmedi"
    assert pos.funding_paid > 0


def test_dead_venue_does_not_block_the_tour_and_loses_no_settlement(tmp_path, monkeypatch):
    """Venue erisilemez: tur tamamlanir, hicbir donem sessizce kapanmaz, sonra TAM BIR KEZ kapanir."""
    eng, pos, now0, _ = _engine_with_position(tmp_path, monkeypatch)
    before = pos.last_funding_settlement_utc
    eng._funding_provider_factory_override = lambda: _Provider([], exc=ConnectionError("fapi down"))
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert pos.last_funding_settlement_utc == before
    assert not [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING]

    eng.funding_rates._retry_after.clear()                  # sogumayi gecir (zaman ilerlemis say)
    prov = _Provider(_rows(now0 - timedelta(hours=48), now0, interval_h=8, rate="0.0001"))
    eng._funding_provider_factory_override = lambda: prov
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING]


# ============================================================ D3/M2 — provenans mutasyon kapisi
def _frame(last_open: datetime, high: float, low: float, close: float) -> pd.DataFrame:
    """`prepare` ciktisiyla ayni sekil: `timestamp` sutunu + UTC zaman indeksi."""
    n = 300
    base = int(last_open.timestamp() * 1000) - (n - 1) * 3_600_000
    ts = [base + i * 3_600_000 for i in range(n)]
    df = pd.DataFrame({"timestamp": ts, "open": [close] * n, "high": [close] * n,
                       "low": [close] * n, "close": [close] * n, "volume": [1.0] * n})
    df.loc[df.index[-1], ["high", "low"]] = [high, low]
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("dt")


class _Brief:
    def __init__(self, symbol: str, price: float):
        self.symbol, self.price, self.reports = symbol, price, []


def test_marks_carries_provenance_so_in_life_bar_extremes_still_reach_the_ledger(tmp_path, monkeypatch):
    """D3/M2 MUTASYON KAPISI — `_marks` provenansi bos birakirsa bu test DUSER.

    Sozlesme testi `bar_open=` ANAHTARINI zorunlu kilar ama `bar_open=""` ile de gecer. Burada
    GERCEK `_marks` ciktisi GERCEK deftere verilir: pozisyonun omru icinde acilmis bir barin ucu
    MFE'ye ULASMALIDIR. Provenans bos olsaydi uc sessizce dusurulur ve MFE yalniz mark'tan gelirdi.
    """
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000, seed_funding=False)  # SOGUK onbellek: konusu kapsamanin YOKLUGU
    now0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    bar_open = now0 - timedelta(hours=1)
    opened = bar_open - timedelta(hours=3)                   # bar TAMAMEN pozisyonun omru icinde
    pos = eng.ledger2.open(ETH, "LONG", "3000", SizeSpec(300, AmountType.NOTIONAL, 2),
                           stop=D("2000"), now=opened)
    assert pos is not None, eng.ledger2.last_reject_reason

    eng.runner.last_frames[ETH] = {"1h": _frame(bar_open, high=3300.0, low=2900.0, close=3000.0)}
    marks = eng._marks([_Brief(ETH, 3000.0)])                # GERCEK uretim fonksiyonu
    assert marks[ETH].bar_open, "provenans bos -> butun bar uclari sessizce dusurulur"

    eng.ledger2.tick(marks, now_utc=now0)
    assert pos.mfe_pct >= D("9"), "omur ici barin ucu MFE'ye ULASMADI (provenans kayboldu mu?)"
    assert pos.mae_pct <= D("-3")


def test_marks_provenance_still_blocks_a_pre_entry_bar(tmp_path, monkeypatch):
    """Ayni GERCEK yol, ters yon: giristen ONCE acilmis bar MFE'ye yazamaz."""
    eng = TE._engine(tmp_path, monkeypatch, symbols=[ETH], equity=1000, seed_funding=False)  # SOGUK onbellek: konusu kapsamanin YOKLUGU
    now0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    bar_open = now0 - timedelta(hours=1)
    opened = bar_open + timedelta(minutes=20)                # bar, pozisyondan ONCE acildi
    pos = eng.ledger2.open(ETH, "LONG", "3000", SizeSpec(300, AmountType.NOTIONAL, 2),
                           stop=D("2000"), now=opened)
    assert pos is not None, eng.ledger2.last_reject_reason
    eng.runner.last_frames[ETH] = {"1h": _frame(bar_open, high=3300.0, low=2900.0, close=3000.0)}
    eng.ledger2.tick(eng._marks([_Brief(ETH, 3000.0)]), now_utc=now0)
    assert pos.mfe_pct == 0                                  # barin +10% ucu MFE'ye ULASMADI
    assert pos.mae_pct > D("-1")                             # barin -3.3% dibi de ULASMADI
    assert eng.ledger2.bar_extremes_skipped == 1


# ============================================================ eski (sisirilmis) MFE korumasi
def _led_with_be():
    import test_accounting as TA
    return TA._led(breakeven_at_mfe_r=D("1.0"))


def test_legacy_inflated_peak_cannot_trigger_a_new_breakeven_move():
    """Onarim ONCESI birikmis sisirilmis tepe yeni bir basa-bas hareketi TETIKLEYEMEZ.

    Uretimde olculdu: NATGAS 0.65R kayitli / 0.12R gercek, GPS 0.67R / 0.21R. Bu pozisyonlarda
    kural, amaclanan esigin kabaca YARISINDA atesleyebiliyordu. Sembol ya da guncel deger
    VARSAYILMAZ: kural genel — damgadan ONCEKI hicbir tepe guvenilir sayilmaz.
    """
    import test_accounting as TA
    led = _led_with_be()
    pos = led.open(TA.ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   targets=[3300], filters=TA._f(), now=TA.T0)
    # DEVRALINAN SISIRILMIS tepe 1.0R ESIGINI GECMELI, yoksa test kapi degildir:
    # risk %3.3333 -> 4.0 puan = 1.20R. Eski kod bunu ATESLERDI.
    pos.mfe_pct = D("4.0")
    assert not pos.meta.get("mfe_trusted_from")

    t = TA.T0 + timedelta(hours=1)
    led.tick({TA.ETH: TickData(last=3005, high=3006, low=3004, bar_open=t.isoformat())}, now_utc=t)
    assert pos.meta.get("mfe_trusted_from")                  # damga konuldu
    assert float(pos.meta["mfe_pct_legacy_at_stamp"]) == pytest.approx(4.0)
    assert pos.mfe_pct >= D("4.0")                           # KAYIT alani yeniden yazilmadi
    assert not pos.meta.get("be_by_mfe"), "sisirilmis tepe basa-bas hareketini tetikledi"
    assert pos.stop == D("2900"), "stop erken tasindi"

    # gercek hareket 1R'ye ulasinca kural NORMAL calisir
    t2 = TA.T0 + timedelta(hours=2)
    led.tick({TA.ETH: TickData(last=3090, high=3101, low=3080, bar_open=t2.isoformat())}, now_utc=t2)
    assert pos.meta.get("be_by_mfe") and pos.stop > D("3000")
    assert pos.meta["be_by_mfe"]["basis"] == "trusted"


def test_new_position_breakeven_behaviour_is_unchanged():
    """Onarimdan SONRA acilan pozisyonda guvenilir tepe ile kayit tepesi BIREBIR ilerler."""
    import test_accounting as TA
    led = _led_with_be()
    pos = led.open(TA.ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   targets=[3300], filters=TA._f(), now=TA.T0)
    t1 = TA.T0 + timedelta(hours=1)
    led.tick({TA.ETH: TickData(last=3050, high=3080, low=3040, bar_open=t1.isoformat())}, now_utc=t1)
    assert pos.mfe_pct_trusted == pos.mfe_pct and not pos.meta.get("be_by_mfe")     # 0.8R
    t2 = TA.T0 + timedelta(hours=2)
    led.tick({TA.ETH: TickData(last=3090, high=3101, low=3085, bar_open=t2.isoformat())}, now_utc=t2)
    assert pos.mfe_pct_trusted == pos.mfe_pct
    assert pos.meta.get("be_by_mfe") and pos.stop > D("3000")


def test_trusted_peak_survives_save_and_reload(tmp_path):
    """Damga ve guvenilir tepe kalicidir: restart eski sismeyi geri getirmez."""
    import test_accounting as TA
    from tradingbot.accounting import FuturesLedgerV2
    led = _led_with_be()
    pos = led.open(TA.ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   targets=[3300], filters=TA._f(), now=TA.T0)
    pos.mfe_pct = D("4.0")
    t = TA.T0 + timedelta(hours=1)
    led.tick({TA.ETH: TickData(last=3005, high=3006, low=3004, bar_open=t.isoformat())}, now_utc=t)
    p = tmp_path / "fut.json"
    led.save(p)
    led2 = FuturesLedgerV2.load(p)
    pos2 = led2.positions[TA.ETH]
    assert pos2.meta.get("mfe_trusted_from") == pos.meta["mfe_trusted_from"]
    assert pos2.mfe_pct_trusted == pos.mfe_pct_trusted < pos2.mfe_pct
    led2.breakeven_at_mfe_r = D("1.0")
    t2 = TA.T0 + timedelta(hours=2)
    led2.tick({TA.ETH: TickData(last=3005, high=3006, low=3004, bar_open=t2.isoformat())}, now_utc=t2)
    assert not pos2.meta.get("be_by_mfe"), "restart sonrasi sisirilmis tepe geri geldi"


# ============================================================ tekrar / eszamanli / restart
def test_restart_does_not_reapply_settled_periods(tmp_path, monkeypatch):
    """Watermark kalicidir: kaydet -> yeniden yukle -> tekrar tahakkuk YOK."""
    from tradingbot.accounting import FuturesLedgerV2
    eng, pos, now0, _ = _engine_with_position(tmp_path, monkeypatch)
    prov = _Provider(_rows(now0 - timedelta(hours=48), now0, interval_h=8, rate="0.0001"))
    eng._funding_provider_factory_override = lambda: prov
    eng.tour(do_scan=False, obsidian=False, charts=False)
    n = len([e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING])
    assert n > 0
    paid = pos.funding_paid

    p = tmp_path / "reload.json"
    eng.ledger2.save(p)
    led2 = FuturesLedgerV2.load(p)
    led2.funding.settlement_source = eng.funding_rates.settlements_in
    led2.tick({ETH: TickData(last=pos.last_price, mark=pos.last_price)},
              now_utc=datetime.now(UTC), funding_rate_lookup=eng.funding_rates.lookup)
    assert led2.positions[ETH].funding_paid == paid, "restart sonrasi donemler TEKRAR uygulandi"


def test_concurrent_monitor_and_tour_accrue_each_settlement_exactly_once(tmp_path, monkeypatch):
    """Cikis monitoru ile tur ayni anda calisirsa bile her settlement TAM BIR KEZ tahakkuk eder.

    Ikisi de defterin `_exit_lock`'unu alir; ic ice tick yoktur. Watch dongusu bugun tek is
    parcacikli olsa da bu sozlesme kilit kaldirilirsa DUSER.
    """
    import threading
    eng, pos, now0, _ = _engine_with_position(tmp_path, monkeypatch)
    prov = _Provider(_rows(now0 - timedelta(hours=48), now0, interval_h=8, rate="0.0001"))
    eng._funding_provider_factory_override = lambda: prov
    eng.funding_rates.refresh(prov, ETH, now0 - timedelta(hours=48), now0)   # onbellek HAZIR
    due = eng.ledger2.funding.settlements_due(pos, datetime.now(UTC))
    assert len(due) >= 2

    errs: list[BaseException] = []

    def _run(fn):
        try:
            fn()
        except BaseException as exc:                       # noqa: BLE001 — testin kendisi raporlar
            errs.append(exc)

    t1 = threading.Thread(target=_run, args=(lambda: eng.exit_check(),))
    t2 = threading.Thread(target=_run, args=(lambda: eng.tour(do_scan=False, obsidian=False, charts=False),))
    t1.start(); t2.start(); t1.join(30); t2.join(30)
    assert not errs, errs

    fund = [e for e in eng.ledger2.entries if e.kind is LedgerKind.FUNDING]
    stamps = [e.ts for e in fund]
    assert len(stamps) == len(set(stamps)), "ayni settlement birden fazla kez tahakkuk etti"
    assert len(fund) <= len(due)


# ============================================================ kapsama disi = SESSIZ KAYIP OLMAZ
def test_cache_covering_only_a_later_window_cannot_skip_earlier_settlements(tmp_path):
    """Kendi onarimimin regresyonu: kapsanmamis bir donemi atlayip SONRAKINI kapatmak yasak.

    Onbellek yalnizca gec bir pencereyi tasiyorsa, watermark'tan itibaren bitisik degildir;
    sonraki settlement kapatilirsa watermark ileri sarar ve ARADAKI donem sonsuza kadar kaybolur.
    Bu, onarilan D1 kusurundan daha kotu olurdu.
    """
    t0 = datetime(2026, 9, 8, 0, tzinfo=UTC)
    cache = FundingRateCache(tmp_path / "fr.json")
    # yalniz 16:00 ve sonrasi cekildi; 08:00 kapsanmadi
    cache.refresh(_Provider(_rows(t0 + timedelta(hours=16), t0 + timedelta(hours=24), interval_h=8)),
                  ETH, t0 + timedelta(hours=16), t0 + timedelta(hours=24))
    assert cache.size > 0
    sched = FundingSchedule(settlement_source=cache.settlements_in)
    pos = _pos(t0)                                       # watermark 00:00 — kapsama disinda
    assert sched.settlements_due(pos, t0 + timedelta(hours=24)) == []
    assert sched.accrue(pos, t0 + timedelta(hours=24), D("3000"), cache.lookup) == []
    assert pos.last_funding_settlement_utc == t0.isoformat()

    # watermark'tan itibaren cekilince donemler SIRAYLA kapanir
    cache.refresh(_Provider(_rows(t0, t0 + timedelta(hours=24), interval_h=8)), ETH, t0, t0 + timedelta(hours=24))
    due = sched.settlements_due(pos, t0 + timedelta(hours=24))
    assert [t.hour for t in due] == [8, 16, 0]
    assert len(sched.accrue(pos, t0 + timedelta(hours=24), D("3000"), cache.lookup)) == 3


def test_window_refresh_does_not_fetch_on_every_tour(tmp_path):
    """Pencere tabanli yenileme her turda istek ATMAMALI (olculdu: duzeltmeden once 96/96)."""
    t0 = datetime(2026, 9, 8, 0, tzinfo=UTC)
    cache = FundingRateCache(tmp_path / "fr.json")
    prov = _Provider(_rows(t0, t0 + timedelta(hours=48), interval_h=8))
    now = t0 + timedelta(hours=1)
    for _ in range(96):                                  # 24 saat boyunca 15 dk'lik turlar
        cache.ensure_window(lambda: prov, {ETH: (t0, now)}, now=now.timestamp(), save=False)
        now += timedelta(minutes=15)
    assert len(prov.calls) < 40, f"her turda istek atiyor ({len(prov.calls)}/96)"
    assert len(cache.settlements_in(ETH, t0, t0 + timedelta(hours=25))) == 3


# ============================================================ URETIM KABLOLAMASI KAPILARI
def test_engine_wires_the_venue_settlement_source_into_the_ledger(tmp_path, monkeypatch):
    """DEF-3 MUTASYON KAPISI — `settlement_source = None` yapilirsa bu test DUSER.

    D2 testlerinin geri kalani `FundingSchedule(settlement_source=...)`'i ELLE kuruyordu; uretim
    kablolamasini hicbiri sinamiyordu. Burada 4 saatlik bir sozlesmenin donemleri MOTORUN kendi
    defteri uzerinden sayiliyor: sabit grid'e dusulurse 6 yerine 3 gorunur.
    """
    eng, pos, now0, _ = _engine_with_position(tmp_path, monkeypatch, opened_hours_ago=24.0)
    assert eng.ledger2.funding.settlement_source is not None, "settlement kaynagi deftere baglanmamis"
    assert eng.ledger2.funding.require_verified is True
    wm = eng.ledger2.funding.window_start(pos)
    prov = _Provider(_rows(wm, now0, interval_h=4))
    eng.funding_rates.refresh(prov, ETH, wm, now0)
    due = eng.ledger2.funding.settlements_due(pos, now0)
    assert len(due) >= 5, f"4 saatlik sozlesmede yalniz {len(due)} donem gorundu -> sabit grid'e dusulmus"


def test_tour_tick_runs_under_the_exit_monitor_lock(tmp_path, monkeypatch):
    """DEF-5 MUTASYON KAPISI — turdaki `with self._exit_lock:` kaldirilirsa bu test DUSER.

    Bloklama ile olculemez: `tour` zaten `ensure_gap_reconciled` icinde ayni kilidi aliyor, o
    yuzden tur her hâlükârda bloklanir. Burada kilidin GERCEKTEN tick sirasinda TUTULUP tutulmadigi
    gozlenir: kilit bir sayaç proxy'siyle sarilir ve `ledger2.tick` cagrildigi ANDA derinlik
    okunur. Kilit kaldirilirsa gap'in `with` blogu cikmis olacagi icin derinlik 0 olur.
    """
    eng, pos, now0, _ = _engine_with_position(tmp_path, monkeypatch)
    prov = _Provider(_rows(now0 - timedelta(hours=48), now0, interval_h=8, rate="0.0001"))
    eng._funding_provider_factory_override = lambda: prov
    eng.funding_rates.refresh(prov, ETH, now0 - timedelta(hours=48), now0)

    class _CountingLock:
        """Gercek RLock'a delege eder, yalniz TUTMA DERINLIGINI sayar."""

        def __init__(self, real):
            self._real, self.depth = real, 0

        def __enter__(self):
            self._real.acquire()
            self.depth += 1
            return self

        def __exit__(self, *exc):
            self.depth -= 1
            self._real.release()
            return False

        def acquire(self, *a, **k):
            got = self._real.acquire(*a, **k)
            if got:
                self.depth += 1
            return got

        def release(self):
            self.depth -= 1
            self._real.release()

    eng._exit_lock = _CountingLock(eng._exit_lock)
    seen: list[int] = []
    real_tick = eng.ledger2.tick

    def _spy(*a, **k):
        seen.append(eng._exit_lock.depth)          # tick ANINDA kilit tutuluyor mu?
        return real_tick(*a, **k)

    monkeypatch.setattr(eng.ledger2, "tick", _spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert seen, "tur defteri hic tick etmedi"
    assert all(d >= 1 for d in seen), (
        f"tur tick'i cikis monitorunun kilidini TUTMUYOR (olculen derinlikler: {seen})")


def test_pending_settlement_is_recorded_on_the_close_not_written_as_zero(tmp_path):
    """DEF-2 KAPISI — kapanista cozulememis donem SESSIZ SIFIR olarak yazilamaz.

    "Donem bekler, kaybolmaz" garantisi yalnizca pozisyon ACIKKEN gecerlidir. Kapanista bir daha
    tahakkuk sansi yoktur; kayit bu yuzden kac donemin cozulemedigini TASIMALIDIR.
    """
    import test_accounting as TA
    led = TA._led()
    t_open = datetime(2026, 8, 18, 7, 0, tzinfo=UTC)
    pos = led.open(TA.ETH, "LONG", 3000, SizeSpec(48, AmountType.NOTIONAL, 2), stop=2900,
                   filters=TA._f(), now=t_open)
    pos.meta["last_funding_rate"] = "0.0001"                 # tahmini yedek ELDE olsa bile
    t_stop = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)         # 08:00 settlement gecildi
    closed = led.tick({TA.ETH: TickData(last=2890, low=2880, high=2895, bar_open=t_stop.isoformat())},
                      now_utc=t_stop, funding_rate_lookup=lambda s, w: None)
    assert closed, "kurulum kapanmadi"
    rec = closed[0]
    assert rec.funding == 0                                  # uydurma oran YAZILMADI
    assert rec.funding_pending_settlements >= 1, "cozulememis donem kayitta GORUNMUYOR"
    # Kapsama BOSLUGU degil: settlement zamani BILINIYORDU, kapatamayan sey orandi. Iki durum
    # ayri kalmali, yoksa "kac donem" sorusunun cevabi bilinmiyormus gibi gorunur.
    assert rec.funding_coverage_gap is False
    # Kanit KALICI olmali: pozisyon `meta`si kapanista atilir, kayit ise deftere yazilir.
    assert rec.to_legacy_dict()["funding_incomplete"] is True
    assert TradeRecord.from_dict(rec.to_dict()).funding_pending_settlements == rec.funding_pending_settlements

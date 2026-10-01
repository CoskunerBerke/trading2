# -*- coding: utf-8 -*-
"""KAPANMIŞ BAR ÖLÇEK DOĞRULAMASI — büyük gerçek hareket bozuk veri SAYILMAZ (2026-09-22; REVIEW-2026-09-22 F1).

ÖLÇÜLEN KUSUR (e9ed196): `apply_closed_bars_to_ledger` barın KAPANIŞINI yalnız GÜNCEL mark'ın ±%20 bandıyla
karşılaştırıyordu. Kimliği doğru (USDM_PERP), OHLC'si kendi içinde geçerli bir stop barı, mark sonradan toparlandığı
için BAR_SCALE_UNVERIFIED ile atlanıyor; pozisyon açık kalıp bir dakika sonra hedefte KÂR yazıyordu:
  LONG 100 / stop 95 / hedef 110, notional 10, 1x; 10:00–10:15 bar (open 100, high 103, low 70, close 70);
  10:16 mark 101 → bar atlandı; 10:17 mark 112 → hedef1, net +0,9895. Kontrol (bant içi mark) → stop, net −3,0085.
Ayrıca `mark <= 0` ölçek kontrolünü TAMAMEN atlatıyordu.

ONARIM: ölçek, doğrulanmış bir referansa bağlanabilen bardan kabul edilir — (a) SÜREKLİLİK: barın açılışı, bu
pozisyonda daha önce doğrulanmış en yakın önceki barın kapanışına (yoksa girişe) ±%20 içinde; ya da (b) eski yol:
kapanış güncel mark'a ±%20. İkisi de yoksa bar uygulanmaz (mark=0 dahil). Çözülemeyen boşluk, pozisyon hangi yoldan
kapanırsa kapansın kayda `features.path_unverified` olarak taşınır.

ETİKET: gerçek `apply_closed_bars_to_ledger`, `FuturesLedgerV2`, `PatternBook` ve motor/replay bar kurucuları çalışır;
fiyatlar SENTETİKTİR. Gerçek piyasa sonucu ya da kârlılık kanıtı DEĞİLDİR.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.accounting import (  # noqa: E402
    AmountType,
    FuturesLedgerV2,
    MarketType,
    SizeSpec,
    SlippageModel,
    SymbolFilters,
    TickData,
)
from tradingbot.strategy_paper import BAR_CORRUPT, BAR_SCALE_UNVERIFIED, apply_closed_bars_to_ledger  # noqa: E402

UTC = timezone.utc
SYM = "NEW/USDT"
M15 = 15 * 60_000
DAY = datetime(2026, 9, 21, tzinfo=UTC)
T_OPEN = DAY.replace(hour=9, minute=59)
T_BAR = DAY.replace(hour=10)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _filters() -> SymbolFilters:
    return SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                         min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _ledger(side="LONG", stop=95, targets=(110,)):
    led = FuturesLedgerV2("100", slippage=SlippageModel.zero())
    pos = led.open(SYM, side, 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=stop, targets=list(targets),
                   filters=_filters(), now=T_OPEN)
    assert pos is not None, led.last_reject_reason
    return led


def _row(t: datetime, o, h, lo, c) -> dict:
    r = {"timestamp": _ms(t), "high": float(h), "low": float(lo), "close": float(c)}
    if o is not None:
        r["open"] = float(o)
    return r


def _spec(*rows, mark: float, market="USDM_PERP") -> dict:
    return {SYM: {"tf": "15m", "rows": list(rows), "mark": float(mark), "market": market,
                  "first_bar_ms": rows[0]["timestamp"] if rows else 0}}


def _gaps(led):
    pos = led.positions.get(SYM)
    return [g["reason"] for g in ((pos.meta.get("ohlc_gaps") or {}).get("15m") or [])] if pos else []


# ------------------------------------------------------------------ F1 senaryosu
def test_a_valid_stop_bar_is_applied_even_when_the_live_mark_has_since_recovered():
    led = _ledger()
    recs = apply_closed_bars_to_ledger(led, _spec(_row(T_BAR, 100, 103, 70, 70), mark=101), now=T_BAR + timedelta(minutes=16))
    assert len(recs) == 1 and SYM not in led.positions, "ÖNCE: bar BAR_SCALE_UNVERIFIED ile atlanıyor, pozisyon açık kalıyordu"
    rec = recs[0]
    assert rec.exit_reason == "stop" and rec.exit_price == D("70.0")          # kapanış stopun ötesinde: ihtiyatlı dolum
    assert float(rec.net_pnl) == pytest.approx(-3.0085, abs=1e-9)
    assert "path_unverified" not in rec.features
    # 10:17'de mark 112: pozisyon zaten kapalı → hedef KÂRI yazılamaz
    assert led.tick({SYM: TickData(last=112, mark=112)}, now_utc=T_BAR + timedelta(minutes=17)) == []
    assert len(led.history) == 1


def test_short_mirror_valid_stop_bar_far_from_the_live_mark_is_applied():
    led = _ledger(side="SHORT", stop=105, targets=(90,))
    recs = apply_closed_bars_to_ledger(led, _spec(_row(T_BAR, 100, 130, 99, 130), mark=99), now=T_BAR + timedelta(minutes=16))
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and recs[0].exit_price == D("130.0")


def test_the_old_wick_case_still_fires_the_stop_at_its_level():
    """Fitil vakası (low=70, close=101, mark=101) — 2026-09-17 onarımı BOZULMADI."""
    led = _ledger()
    recs = apply_closed_bars_to_ledger(led, _spec(_row(T_BAR, 100, 103, 70, 101), mark=101), now=T_BAR + timedelta(minutes=16))
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and recs[0].exit_price == D("95")
    assert float(recs[0].net_pnl) == pytest.approx(-0.50975, abs=1e-9)


# ------------------------------------------------------------------ korumalar KORUNDU
def test_a_unit_artifact_is_still_refused_even_with_a_plausible_looking_open_field():
    led = _ledger()
    art = _row(T_BAR, 100_000, 103_000, 99_000, 101_000)             # ×1000 birim artefaktı (açılış dahil)
    assert apply_closed_bars_to_ledger(led, _spec(art, mark=101), now=T_BAR + timedelta(minutes=16)) == []
    assert SYM in led.positions and _gaps(led) == [BAR_SCALE_UNVERIFIED]


def test_mark_zero_no_longer_disables_the_scale_check():
    """ÖNCE: `ref > 0 and ...` yüzünden mark=0 gönderilince ölçek kontrolü TAMAMEN atlanıyordu."""
    led = _ledger()
    no_open = _row(T_BAR, None, 310, 280, 300)                        # açılışsız, girişin 3 katı
    assert apply_closed_bars_to_ledger(led, _spec(no_open, mark=0), now=T_BAR + timedelta(minutes=16)) == []
    assert SYM in led.positions and _gaps(led) == [BAR_SCALE_UNVERIFIED]
    assert float(led.positions[SYM].mfe_pct) < 50.0, "doğrulanmamış barın tepesi MFE'ye yazılmamalı"


def test_an_open_outside_the_bar_range_is_corrupt_and_a_foreign_market_is_refused():
    led = _ledger()
    bad = _row(T_BAR, 120, 103, 70, 70)                              # açılış high'ın üstünde
    assert apply_closed_bars_to_ledger(led, _spec(bad, mark=101), now=T_BAR + timedelta(minutes=16)) == []
    assert _gaps(led) == [BAR_CORRUPT]
    led2 = _ledger()
    good = _row(T_BAR, 100, 103, 70, 70)
    assert apply_closed_bars_to_ledger(led2, _spec(good, mark=101, market="SPOT"), now=T_BAR + timedelta(minutes=16)) == []
    assert SYM in led2.positions


def test_a_genuine_crash_bar_is_not_called_corrupt_by_the_extreme_ratio():
    """Uç sınırı gövdeye göre: açılış 100, dip 15, kapanış 18 gerçek bir çöküştür (kapanışa göre tepe/kapanış > 5)."""
    led = _ledger()
    recs = apply_closed_bars_to_ledger(led, _spec(_row(T_BAR, 100, 103, 15, 18), mark=18), now=T_BAR + timedelta(minutes=16))
    assert len(recs) == 1 and recs[0].exit_reason == "stop"


# ------------------------------------------------------------------ çözülmemiş boşluk: sonuç doğrulanmış SAYILMAZ
def test_a_later_profitable_exit_carries_the_unresolved_gap_instead_of_looking_verified():
    led = _ledger()
    unverifiable = _row(T_BAR, None, 103, 70, 70)                     # açılış yok + kapanış mark'tan uzak
    assert apply_closed_bars_to_ledger(led, _spec(unverifiable, mark=101), now=T_BAR + timedelta(minutes=16)) == []
    recs = led.tick({SYM: TickData(last=112, mark=112)}, now_utc=T_BAR + timedelta(minutes=17))
    assert len(recs) == 1 and recs[0].exit_reason == "hedef1"
    pu = recs[0].features.get("path_unverified")
    assert pu and pu["unresolved_bars"] == 1 and pu["any_stop_crossed"] is True, pu
    assert pu["bars"][0]["reason"] == BAR_SCALE_UNVERIFIED and pu["bars"][0]["low"] == 70.0


def test_pattern_book_summary_row_flags_the_unverified_path(tmp_path):
    import test_pattern_trader_v1 as tp
    cfg = tp._cfg(tmp_path)
    book = tp._book_obj(cfg)
    assert book.ledger.open(SYM, "LONG", 100, SizeSpec(10, AmountType.NOTIONAL, leverage=1), stop=95, targets=[110],
                            filters=_filters(), now=T_OPEN) is not None
    book.apply_closed_bars(_spec(_row(T_BAR, None, 103, 70, 70), mark=101), now=T_BAR + timedelta(minutes=16))
    book.tick({SYM: TickData(last=112, mark=112)}, now=T_BAR + timedelta(minutes=17))
    row = book.closed_recent[-1]
    assert row["exit_reason"] == "hedef1" and row["path_unverified"] is True, row


# ------------------------------------------------------------------ tekrar deneme, yeniden başlatma, tek uygulama
def test_retry_restart_and_a_bar_is_never_applied_twice(tmp_path):
    led = _ledger(stop=80)
    b1 = _row(T_BAR, 100, 104, 96, 103)
    b2 = _row(T_BAR + timedelta(minutes=15), 103, 106, 101, 104)
    now = T_BAR + timedelta(minutes=31)
    assert apply_closed_bars_to_ledger(led, _spec(b1, b2, mark=104), now=now) == []
    pos = led.positions[SYM]
    mfe, cursor = pos.mfe_pct, pos.meta["ohlc_cursor"]["15m"]
    assert cursor == b2["timestamp"] and [r[0] for r in pos.meta["ohlc_verified"]["15m"]] == [b1["timestamp"], b2["timestamp"]]
    apply_closed_bars_to_ledger(led, _spec(b1, b2, mark=104), now=now)     # aynı barlar: TÜKETİLMİŞ
    assert led.positions[SYM].mfe_pct == mfe and len(led.history) == 0
    path = tmp_path / "ledger.json"
    led.save(path)
    led2 = FuturesLedgerV2.load(path)                                   # YENİDEN BAŞLATMA
    apply_closed_bars_to_ledger(led2, _spec(b1, b2, mark=104), now=now)
    assert led2.positions[SYM].mfe_pct == mfe and led2.positions[SYM].meta["ohlc_cursor"]["15m"] == cursor
    # süreklilik referansı restart sonrası meta'dan: b3 açılışı b2 kapanışına bağlı, mark'tan %40 uzak → UYGULANIR
    b3 = _row(T_BAR + timedelta(minutes=30), 104, 105, 75, 76)
    recs = apply_closed_bars_to_ledger(led2, _spec(b1, b2, b3, mark=130), now=T_BAR + timedelta(minutes=46))
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and recs[0].exit_price == D("76.0")


def test_a_gap_bar_is_retried_and_resolved_when_its_data_is_corrected():
    led = _ledger()
    now = T_BAR + timedelta(minutes=16)
    assert apply_closed_bars_to_ledger(led, _spec(_row(T_BAR, None, 103, 70, 70), mark=101), now=now) == []
    assert _gaps(led) == [BAR_SCALE_UNVERIFIED]
    assert led.positions[SYM].features.get("path_unverified")
    recs = apply_closed_bars_to_ledger(led, _spec(_row(T_BAR, 100, 103, 70, 70), mark=101), now=now)
    assert len(recs) == 1 and recs[0].exit_reason == "stop"
    assert "path_unverified" not in recs[0].features, "boşluk bu tikte çözüldü: kayıt eski boşluğu taşımamalı"


# ------------------------------------------------------------------ üretim bar kurucuları açılışı TAŞIYOR
def test_engine_closed_bar_rows_carry_the_bar_open():
    from tradingbot.engine_v3 import TradingEngineV3
    ts = [_ms(T_BAR) - 3_600_000 * i for i in range(3)][::-1]
    df = pd.DataFrame({"timestamp": ts, "open": [100.0, 101.0, 102.0], "high": [103.0] * 3, "low": [99.0] * 3,
                       "close": [101.0, 102.0, 70.0]})
    fake = SimpleNamespace(_frame_provenance={SYM: {"tour_id": "r1", "market": "USDM_PERP", "source": "t"}},
                           run_id="r1", runner=SimpleNamespace(last_frames={SYM: {"1h": df}}))
    out = TradingEngineV3._paper_closed_bars(fake, [SYM], {SYM: 101.0})
    assert [r["open"] for r in out[SYM]["rows"]] == [100.0, 101.0, 102.0]


def test_replay_bar_tick_carries_the_bar_open():
    from tradingbot.replay.engine import HistoricalReplay
    seen = {}

    class _Led:
        def tick(self, marks, **kw):
            seen.update(marks)
            return []

    t0 = _ms(T_BAR)
    df = pd.DataFrame({"timestamp": [t0, t0 + 4 * 3_600_000], "open": [100.0, 99.0], "high": [101.0, 100.0],
                       "low": [98.0, 90.0], "close": [99.0, 95.0]})
    fake = SimpleNamespace(primary={SYM: df}, tf="4h", ledger2=_Led(), funding_rates=None, _on_closed=lambda r: None)
    HistoricalReplay._advance(fake, t0, T_BAR)
    assert seen[SYM].open == D("99.0") and seen[SYM].low == D("90.0")

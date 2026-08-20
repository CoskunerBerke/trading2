"""Offline gap reconciliation testleri — kesinti penceresi, olay-zamanı stop/TP/liq/funding uzlaştırması,
fail-closed GAP_AMBIGUOUS, watermark idempotency, bağımsız heartbeat ile ilişkili duplicate=0 garantileri."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from tradingbot.accounting.futures_ledger import FuturesLedgerV2
from tradingbot.accounting.models import AmountType, SizeSpec
from tradingbot.ops.gap import GapReconciler, choose_timeframe, read_gap_status, read_watermark, write_watermark

UTC = timezone.utc
T0 = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)          # kesinti başlangıcı (watermark)


def _mk_ledger(tmp_path: Path, *, short: bool = True) -> tuple[FuturesLedgerV2, Path]:
    led = FuturesLedgerV2(50)
    if short:
        pos = led.open("SUI/USDT", "SHORT", Decimal("0.65"), SizeSpec(Decimal("15"), AmountType.NOTIONAL, 1),
                       stop=Decimal("0.6777"), targets=[Decimal("0.6053"), Decimal("0.5812")], setup_type="pullback")
    else:
        pos = led.open("BZ/USDT", "LONG", Decimal("90.61"), SizeSpec(Decimal("15"), AmountType.NOTIONAL, 1),
                       stop=Decimal("88.3408"), targets=[Decimal("95.0585"), Decimal("97.2977")], setup_type="pullback")
    assert pos is not None
    p = tmp_path / "futures_ledger.json"
    led.save(p)
    return led, p


def _candles(start: datetime, n: int, *, o: float, h: float, lo: float, c: float, tf_ms: int = 60_000,
             special: dict[int, tuple[float, float, float, float]] | None = None) -> pd.DataFrame:
    rows = []
    for i in range(n):
        ts = int(start.timestamp() * 1000) + i * tf_ms
        oo, hh, ll, cc = (special or {}).get(i, (o, h, lo, c))
        rows.append({"timestamp": ts, "open": oo, "high": hh, "low": ll, "close": cc,
                     "close_time": ts + tf_ms - 1, "volume": 1.0, "quote_volume": 1.0, "trades": 1,
                     "taker_buy_base": 0.0, "taker_buy_quote": 0.0, "is_closed": True})
    return pd.DataFrame(rows)


class FakeProvider:
    """Deterministik kline/funding sağlayıcısı; ağ yok."""
    max_kline_limit = 1000

    def __init__(self, frames: dict[str, pd.DataFrame], funding: list[dict] | None = None):
        self.frames = frames
        self.funding = funding or []
        self.kline_calls = 0

    def klines(self, symbol, tf, limit=1000, start_ms=None, end_ms=None):
        self.kline_calls += 1
        df = self.frames.get(symbol)
        if df is None:
            return pd.DataFrame()
        m = df[(df["timestamp"] >= (start_ms or 0)) & (df["timestamp"] <= (end_ms or 2**62))]
        return m.head(limit).reset_index(drop=True)

    def funding_history(self, symbol, limit=1000, start_ms=None, end_ms=None):
        return [r for r in self.funding if r["symbol"] == symbol and (start_ms or 0) <= r["funding_ts"] <= (end_ms or 2**62)]


def _rec(tmp_path, led, path, provider, now):
    return GapReconciler(led, path, tmp_path, lambda: provider, now_fn=lambda: now)


def test_choose_timeframe_tiers():
    assert choose_timeframe(3600) == "1m" and choose_timeframe(3 * 86400) == "5m" and choose_timeframe(30 * 86400) == "15m"


# 1) PC 10 dk kapalı, olay yok → fill yok, pozisyon aynen, watermark ilerler
def test_gap_no_event_holds_position(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(minutes=10)
    prov = FakeProvider({"SUI/USDT": _candles(T0 - timedelta(minutes=1), 12, o=0.65, h=0.655, lo=0.648, c=0.652)})
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    assert rep.status == "OK" and not rep.closed and not rep.blocked and rep.bars_replayed >= 10
    assert "SUI/USDT" in led.positions and len(led.positions["SUI/USDT"].fills) == 1
    assert read_watermark(tmp_path) == now


# 2) Kapalıyken stop → tarihi barda, tarihi zamanla, TEK exit fill
def test_gap_stop_closes_at_historical_bar(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(hours=2)
    # 30. dakikada stop delinir (SHORT: high 0.69 > stop 0.6777)
    prov = FakeProvider({"SUI/USDT": _candles(T0 - timedelta(minutes=1), 121, o=0.65, h=0.655, lo=0.648, c=0.652,
                                              special={30: (0.652, 0.69, 0.65, 0.685)})})
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    assert rep.status == "OK" and len(rep.closed) == 1
    r = rep.closed[0]
    assert r.exit_reason == "stop" and "SUI/USDT" not in led.positions
    closed_at = datetime.fromisoformat(r.closed_at)
    assert (closed_at - T0).total_seconds() < 32 * 60          # olay zamanı ≈ 30. dk barı, "şimdi" değil
    exit_fills = [f for f in r.fills if f.kind != "entry"]
    assert len(exit_fills) == 1 and len({f.id for f in r.fills}) == len(r.fills)
    assert rep.decisions and "worst-case" in rep.decisions[0]


# 3) Kapalıyken TP → target fill
def test_gap_tp_closes(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(hours=1)
    prov = FakeProvider({"SUI/USDT": _candles(T0 - timedelta(minutes=1), 61, o=0.65, h=0.652, lo=0.57, c=0.575)})
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    assert rep.status == "OK" and len(rep.closed) == 1
    assert "hedef" in str(rep.closed[0].exit_reason).lower()          # TP fill (hedef1/hedef2)


# 4) Aynı mumda stop + TP → worst-case: stop kazanır ve karar kayda geçer
def test_gap_same_candle_stop_and_tp_worst_case(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(minutes=30)
    prov = FakeProvider({"SUI/USDT": _candles(T0 - timedelta(minutes=1), 32, o=0.65, h=0.655, lo=0.648, c=0.652,
                                              special={10: (0.65, 0.70, 0.55, 0.60)})})     # hem stop hem TP2 aynı bar
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    assert len(rep.closed) == 1 and rep.closed[0].exit_reason == "stop"
    assert any("worst-case" in d for d in rep.decisions)
    st = read_gap_status(tmp_path)
    assert st["status"] == "OK" and st["decisions"]


# 5) Funding sınırı: kesinti 08:00 UTC settlement'ı kapsar → gerçek oranla TAM BİR KEZ uygulanır
def test_gap_funding_applied_once(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    led.positions["SUI/USDT"].last_funding_settlement_utc = "2026-08-20T00:00:00+00:00"   # duvar saatinden bağımsız sabit pencere
    led.save(p)
    start = datetime(2026, 8, 20, 7, 50, tzinfo=UTC)
    write_watermark(tmp_path, start)
    now = datetime(2026, 8, 20, 8, 20, tzinfo=UTC)
    f_ts = int(datetime(2026, 8, 20, 8, 0, tzinfo=UTC).timestamp() * 1000)
    prov = FakeProvider({"SUI/USDT": _candles(start - timedelta(minutes=1), 45, o=0.65, h=0.652, lo=0.649, c=0.65)},
                        funding=[{"symbol": "SUI/USDT", "funding_ts": f_ts, "rate": 0.0001, "mark": 0.65}])
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    pos = led.positions["SUI/USDT"]
    assert rep.status == "OK" and pos.funding_received > 0 and pos.funding_paid == 0      # SHORT, rate>0 → alır
    assert pos.last_funding_settlement_utc == "2026-08-20T08:00:00+00:00"
    got = pos.funding_received
    # ikinci restart: aynı pencere tekrar → funding TEKRAR uygulanmaz
    led2 = FuturesLedgerV2.load(p, starting_equity=50)
    rep2 = _rec(tmp_path, led2, p, prov, now + timedelta(minutes=6)).reconcile()
    assert rep2.status in ("OK", "NOOP") and led2.positions["SUI/USDT"].funding_received == got


# 6) İki restart idempotency: ikinci uzlaştırma yeni fill/kapanış üretmez
def test_gap_two_restarts_idempotent(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(hours=1)
    prov = FakeProvider({"SUI/USDT": _candles(T0 - timedelta(minutes=1), 61, o=0.65, h=0.69, lo=0.648, c=0.685)})
    rep1 = _rec(tmp_path, led, p, prov, now).reconcile()
    assert len(rep1.closed) == 1
    h1 = json.loads(p.read_text(encoding="utf-8"))["history"]
    led2 = FuturesLedgerV2.load(p, starting_equity=50)
    rep2 = _rec(tmp_path, led2, p, prov, now + timedelta(minutes=10)).reconcile()
    assert rep2.closed == [] and not led2.positions
    h2 = json.loads(p.read_text(encoding="utf-8"))["history"]
    assert len(h1) == len(h2) == 1
    fills = [f["id"] for f in h2[0]["fills"]]
    assert len(fills) == len(set(fills)) == 2


# 7) Bozuk/eksik veri → GAP_AMBIGUOUS fail-closed: tick yok, fill yok, watermark İLERLEMEZ
def test_gap_missing_data_fail_closed(tmp_path: Path):
    led, p = _mk_ledger(tmp_path)
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(hours=2)
    prov = FakeProvider({})                                   # veri yok
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    assert rep.status == "GAP_AMBIGUOUS" and rep.blocked and not rep.closed
    assert "SUI/USDT" in led.positions and len(led.positions["SUI/USDT"].fills) == 1
    assert read_watermark(tmp_path) == T0                     # ilerlemedi → ikinci deneme aynı pencereyi görür
    assert read_gap_status(tmp_path)["status"] == "GAP_AMBIGUOUS"
    # kapsama eksikse de aynı: yalnız yarım pencere veri
    prov2 = FakeProvider({"SUI/USDT": _candles(T0 - timedelta(minutes=1), 30, o=0.65, h=0.652, lo=0.649, c=0.65)})
    rep2 = _rec(tmp_path, led, p, prov2, now).reconcile()
    assert rep2.status == "GAP_AMBIGUOUS" and rep2.blocked


# 8) Mevcut F00004/F00005 benzeri iki pozisyon: uzlaştırma sonrası aynı ID/fill, duplicate yok
def test_gap_two_open_positions_preserved(tmp_path: Path):
    led = FuturesLedgerV2(50)
    led.open("BZ/USDT", "LONG", Decimal("90.61"), SizeSpec(Decimal("15"), AmountType.NOTIONAL, 1),
             stop=Decimal("88.3408"), targets=[Decimal("95.0585")], setup_type="pullback")
    led.open("XAUT/USDT", "LONG", Decimal("4479.32"), SizeSpec(Decimal("15"), AmountType.NOTIONAL, 1),
             stop=Decimal("4401.1487"), targets=[Decimal("4631.6126")], setup_type="pullback")
    p = tmp_path / "futures_ledger.json"
    led.save(p)
    ids = {k: (v.id, [f.id for f in v.fills]) for k, v in led.positions.items()}
    write_watermark(tmp_path, T0)
    now = T0 + timedelta(minutes=45)
    prov = FakeProvider({"BZ/USDT": _candles(T0 - timedelta(minutes=1), 47, o=90.6, h=90.9, lo=90.2, c=90.5),
                         "XAUT/USDT": _candles(T0 - timedelta(minutes=1), 47, o=4479.0, h=4485.0, lo=4470.0, c=4480.0)})
    rep = _rec(tmp_path, led, p, prov, now).reconcile()
    assert rep.status == "OK" and not rep.closed
    led2 = FuturesLedgerV2.load(p, starting_equity=50)
    for k, (pid, fills) in ids.items():
        assert led2.positions[k].id == pid and [f.id for f in led2.positions[k].fills] == fills


# 9) Funding oranı bilinmiyorsa dönem BEKLER (sessiz kayıp yok) ve sonra tam bir kez uygulanır
def test_funding_unknown_rate_stays_pending_then_settles_once(tmp_path: Path):
    from tradingbot.accounting.models import TickData
    led, p = _mk_ledger(tmp_path)
    pos = led.positions["SUI/USDT"]
    pos.meta.pop("last_funding_rate", None)
    pos.last_funding_settlement_utc = "2026-08-20T00:00:00+00:00"
    t1 = datetime(2026, 8, 20, 8, 5, tzinfo=UTC)
    led.tick({"SUI/USDT": TickData(last=Decimal("0.65"))}, now_utc=t1, funding_rate_lookup=None)
    assert pos.last_funding_settlement_utc == "2026-08-20T00:00:00+00:00"      # ileri SARILMADI
    assert pos.funding_received == 0 and pos.funding_paid == 0
    led.tick({"SUI/USDT": TickData(last=Decimal("0.65"))}, now_utc=t1 + timedelta(minutes=5),
             funding_rate_lookup=lambda s, t: Decimal("0.0001"))
    assert pos.last_funding_settlement_utc == "2026-08-20T08:00:00+00:00"
    first = pos.funding_received
    assert first > 0
    led.tick({"SUI/USDT": TickData(last=Decimal("0.65"))}, now_utc=t1 + timedelta(minutes=10),
             funding_rate_lookup=lambda s, t: Decimal("0.0001"))
    assert pos.funding_received == first                                        # çift uygulama yok

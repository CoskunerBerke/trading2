# -*- coding: utf-8 -*-
"""P2b testleri için yardımcı (ağ YOK; test dosyası DEĞİLDİR): gerçekçi boyutta sentetik dünya — gece zamanlaması
(P2 kabul 13) ve özet/sorgu sınırları (kabul 12) için.

* Piyasa: 4 sembol (SOL, ETH, ADA, BTC) — vektörel, sabit tohumlu rastgele yürüyüş. Son `days` gün 1m (tembel 1m'nin
  kapsadığı işlem günleri gibi), 1m'den türetilmiş 5m; daha eski `history_days` gün bağımsız 5m (rastgele kontrolün 120
  günlük geçmişi); 1h/4h/1d hepsi 5m'den türetilir (dilimler tutarlı). Depo `ResearchStore.write` + mühür (veri biriminin
  biçimi), gece okuyucusu `SealedReader`.
* İşlemler: gerçek `FuturesLedgerV2` defterlerinde (Box %50, ana bot vadeli %15, T2 %10, M2 %10, D4 %8, C4 %7) rastgele
  açılış, defterin kendi `tick`iyle CANLI izleme benzetimiyle yürütülür (2026-10-08: 60 sn fiyat örnekleri + kural
  diliminin bar tiki; stop seviyenin ötesindeki ilk örnekten dolar — `walk_sampled`), kural çıkışı tutma süresi sonunda
  `close_manual`. Hedefler girişteki `xp_entry` satırından
  (strateji defterleri) ya da provenance'tan (ana bot) — gerçekte de ÖLÇÜLMÜŞ kaynaklar bunlardır; böylece ızgaraya giren
  işlem oranı gerçekçi olur (rehydrate'in kendisi P2a testlerinde sınanır).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import numpy as np
import pandas as pd

from research_engine_p2_fixtures import KCOLS, M1, M5, STEP, StoreFixture, aggregate, dt_of, ms, walk_sampled

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, SlippageModel
from tradingbot.research_engine.ledgers import iso

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 1, 40, tzinfo=UTC)
RECENT_TO = datetime(2026, 10, 6, tzinfo=UTC)
SYMS = {"SOLUSDT": 150.0, "ETHUSDT": 2000.0, "ADAUSDT": 20.0, "BTCUSDT": 60000.0}
#: defter → (pay, sembol, yönler, tutma dk aralığı, stop % aralığı, hedef R'leri, çıkış nedeni)
MIX = {
    "strategy_paper_box": (0.50, "SOL/USDT", ("LONG", "SHORT"), (45, 240), (0.32, 1.0), (1.0, 2.0), "BOX_EOD_FLAT"),
    "": (0.15, "ETH/USDT", ("LONG", "SHORT"), (60, 360), (0.8, 2.0), (1.5,), "manuel"),
    "strategy_paper": (0.10, "ETH/USDT", ("LONG",), (480, 2880), (2.0, 4.0), (), "TRAIL_GIVEBACK"),
    "strategy_paper_m2": (0.10, "ADA/USDT", ("LONG",), (480, 2880), (2.0, 4.0), (), "TRAIL_GIVEBACK"),
    "strategy_paper_trend4h": (0.08, "ADA/USDT", ("LONG",), (240, 1440), (1.5, 3.0), (), "DONCHIAN_EXIT_LOW10"),
    "strategy_paper_candle4h": (0.07, "ADA/USDT", ("LONG", "SHORT"), (240, 1440), (1.0, 2.5), (1.5,), "TIME_STOP_6_BARS"),
}


#: canlı izleme benzetimi (2026-10-08, P2 incelemesi B1): 60 sn fiyat örnekleri + kural diliminin bar tiki (Box 5m, ana bot /
#: D4 / C4 4h, T2 / M2 1d) — stop seviyenin ötesindeki ilk örnekten dolar (gerçek defter kuralı); eski kırpma (seviyeden
#: dolum) gerçekçi değildi
RULE_BAR_MS = {"strategy_paper_box": M5, "": 4 * STEP["1h"], "strategy_paper": STEP["1d"], "strategy_paper_m2": STEP["1d"],
               "strategy_paper_trend4h": 4 * STEP["1h"], "strategy_paper_candle4h": 4 * STEP["1h"]}


def _ohlc(rng: np.random.Generator, n: int, p0: float, vol: float, start_ms: int, step: int) -> pd.DataFrame:
    rets = rng.normal(0.0, vol, n)
    close = p0 * np.exp(np.cumsum(rets))
    opn = np.concatenate([[p0], close[:-1]])
    wick = np.abs(rng.normal(0.0, vol * 0.6, (2, n)))
    high = np.maximum(opn, close) * (1 + wick[0])
    low = np.minimum(opn, close) * (1 - wick[1])
    o, h, lo, c = (np.round(x, 2) for x in (opn, high, low, close))
    h = np.maximum.reduce([h, o, c])
    lo = np.minimum.reduce([lo, o, c])
    ts = start_ms + np.arange(n, dtype="int64") * step
    v = np.round(100 + 50 * rng.random(n), 3)
    df = pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v})
    df["close_time"] = df["timestamp"] + step - 1
    df["quote_volume"] = (df["volume"] * df["close"]).round(4)
    df["trades"] = 10
    df["taker_buy_base"] = (df["volume"] / 2).round(3)
    df["taker_buy_quote"] = (df["taker_buy_base"] * df["close"]).round(4)
    return df[KCOLS]


def big_market(*, days: int, history_days: int, seed: int = 11) -> dict[str, dict[str, pd.DataFrame]]:
    """Sembol → dilim → barlar (yukarıdaki düzen)."""
    rng = np.random.default_rng(seed)
    b = ms(RECENT_TO)
    a1 = b - days * 86_400_000
    a5 = a1 - history_days * 86_400_000
    out = {}
    for sym, p0 in SYMS.items():
        old = _ohlc(rng, (a1 - a5) // M5, p0, 0.0025, a5, M5)
        m1 = _ohlc(rng, (b - a1) // M1, float(old["close"].iloc[-1]), 0.0011, a1, M1)
        m5 = pd.concat([old, aggregate(m1, M5)], ignore_index=True)
        fr = {"1m": m1, "5m": m5}
        for tf in ("1h", "4h", "1d"):
            fr[tf] = aggregate(m5, STEP[tf])
        out[sym] = fr
    return out


def big_world(v, *, n_trades: int, days: int, history_days: int, seed: int = 5) -> dict:
    """Sahte VPS'e (`FakeVps`) defterler + yan kaynaklar yaz ve depoyu mühürle. Dönen: defter → işlem sayısı."""
    mk = big_market(days=days, history_days=history_days, seed=seed + 6)
    rng = random.Random(seed)
    b = ms(RECENT_TO)
    a = b - days * 86_400_000 + 2 * 3_600_000
    counts: dict[str, int] = {}
    xp_rows, prov_rows = [], []
    for book, (share, sym, sides, hold, stop_pct, tgt_r, reason) in MIX.items():
        n = max(1, int(round(n_trades * share)))
        led = v.futs.get(book) or FuturesLedgerV2(D("100000"), slippage=SlippageModel(fixed_bps=D("3")), max_positions=50,
                                                  enforce_position_cap=False, history_keep=5000)
        v.futs[book] = led
        m1 = mk[sym.replace("/", "")]["1m"]
        span = (b - a - 3 * 86_400_000) // n
        for i in range(n):
            t = a + i * span + rng.randint(0, max(1, span // 3)) // 1000 * 1000 + 13_000
            hold_ms = rng.randint(*hold) * M1
            if t + hold_ms + M1 >= b:
                hold_ms = max(10 * M1, b - t - 2 * M1)
            row = m1[m1["timestamp"] == (t // M1) * M1]
            e = float(row["open"].iloc[0])
            side = rng.choice(sides)
            sg = 1 if side == "LONG" else -1
            sp = rng.uniform(*stop_pct) / 100
            stop = round(e * (1 - sg * sp), 2)
            tg = [round(e + sg * r * abs(e - stop), 2) for r in tgt_r]
            now = dt_of(t)
            pos = led.open(sym, side, D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=stop, targets=tg,
                           setup_type="strategy", now=now, meta={"strategy": book or "main"})
            if pos is None:
                continue
            key = f"{book or 'main_fut'}|{pos.id}|{pos.opened_at}"
            if book:
                xp_rows.append({"kind": "xp_entry", "trade_key": key, "targets": tg})
            else:
                from tradingbot.learn.provenance import build_entry_provenance
                pr = build_entry_provenance(trade_id=pos.id, symbol=sym, direction=side, decision_id=f"d-{pos.id}",
                                            code_sha="c" * 40, config_hash="f" * 16, stop=stop, targets=tg,
                                            features={"n_dissent": float(i % 3), "n_vetoes": 0.0, "rr": tgt_r[0]},
                                            opened_at=pos.opened_at)
                pr["recorded_at"] = pos.opened_at
                prov_rows.append(pr)
            until = (t // M1) * M1 + hold_ms
            rec = walk_sampled(led, sym, m1, opened_ms=t, until_ms=until - M1, bar_ms=RULE_BAR_MS[book])
            if rec is None:
                px = m1[m1["timestamp"] == until - M1]["close"].iloc[0]
                led.close_manual(sym, D(str(float(px))), reason=reason, now=dt_of(until))
            counts[book or "main_fut"] = counts.get(book or "main_fut", 0) + 1
    xp = v.state / "shared_experience" / "experience.jsonl"
    xp.parent.mkdir(parents=True, exist_ok=True)
    xp.write_text("".join(json.dumps(r) + "\n" for r in xp_rows), encoding="utf-8")
    (v.state / "entry_provenance.jsonl").write_text("".join(json.dumps(r) + "\n" for r in prov_rows), encoding="utf-8")
    sf = StoreFixture(v.paths, now=NOW - timedelta(minutes=50))
    for sym, fr in mk.items():
        sf.add_all(sym, fr)
    sf.seal()
    return counts


def stage_seconds(st: dict) -> dict[str, float]:
    return {k: float(s.get("duration_s") or 0.0) for k, s in (st.get("stages") or {}).items()}


__all__ = ["BOX_TIGHT_N", "BOX_WIDE_N", "MIX", "NOW", "RECENT_TO", "big_market", "big_world", "box_epoch_world", "iso",
           "stage_seconds"]


# ============================================================================ §5.10 Box stop genişliği (kabul 11)
BOX_TIGHT_N, BOX_WIDE_N = 72, 176


def box_epoch_world(v, *, seed: int = 21, drift_bps: float = 0.4, vol: float = 0.0011) -> dict:
    """§5.10 Box bulgusunun SENTETİK yeniden üretimi (gerçek veri testte yoktur): eski config döneminde (E0,
    2026-09-30 öncesi; `min_stop_pct` 0,32) 72 işlem stop < %0,5 ve 176 işlem stop %0,5–1 (gerçek bulgunun sayıları).
    Her işlemin penceresinde fiyat işlem yönüne küçük bir kayma (`drift_bps`/dk) taşır — iki kovanın BRÜT avantajı aynı
    tanımlıdır; fark yalnız stop genişliğinden gelir (gürültüye dar stop + 1/stop ile ölçeklenen maliyet). İşlemler
    gerçek `FuturesLedgerV2` ile 1m yolda canlı izleme benzetimiyle yürütülür (60 sn örnek + 5m bar tiki; 2026-10-08);
    kural çıkışı `BOX_EOD_FLAT`."""
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    a = ms(datetime(2026, 6, 1, tzinfo=UTC))
    b = ms(datetime(2026, 9, 28, tzinfo=UTC))
    n = (b - a) // M1
    # stop % dolumdan ölçülür (kayma 3 bps ekler): kova sınırlarından uzak dur
    stops = [rng.uniform(0.32, 0.46) for _ in range(BOX_TIGHT_N)] + [rng.uniform(0.54, 0.95) for _ in range(BOX_WIDE_N)]
    rng.shuffle(stops)
    span = (b - a - 2 * 86_400_000) // len(stops)
    plan = []
    drift = np.zeros(n)
    for i, sp in enumerate(stops):
        t = a + 86_400_000 + i * span + 7_000
        hold = rng.randint(60, 240) * M1
        side = rng.choice(("LONG", "SHORT"))
        k0 = (t - a) // M1 + 1
        drift[k0:k0 + hold // M1] = (1 if side == "LONG" else -1) * drift_bps / 10_000
        plan.append((t, hold, side, sp))
    rets = nrng.normal(0.0, vol, n) + drift
    close = 150.0 * np.exp(np.cumsum(rets))
    opn = np.concatenate([[150.0], close[:-1]])
    wick = np.abs(nrng.normal(0.0, vol * 0.6, (2, n)))
    o, c = np.round(opn, 2), np.round(close, 2)
    h = np.maximum.reduce([np.round(np.maximum(opn, close) * (1 + wick[0]), 2), o, c])
    lo = np.minimum.reduce([np.round(np.minimum(opn, close) * (1 - wick[1]), 2), o, c])
    ts = a + np.arange(n, dtype="int64") * M1
    m1 = pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": 100.0})
    m1["close_time"] = m1["timestamp"] + M1 - 1
    m1["quote_volume"] = (m1["volume"] * m1["close"]).round(4)
    m1["trades"] = 10
    m1["taker_buy_base"] = 50.0
    m1["taker_buy_quote"] = (50.0 * m1["close"]).round(4)
    m1 = m1[KCOLS]
    led = FuturesLedgerV2(D("10000"), slippage=SlippageModel(fixed_bps=D("3")), max_positions=50, enforce_position_cap=False)
    v.futs["strategy_paper_box"] = led
    xp_rows = []
    for t, hold, side, sp in plan:
        row = m1[m1["timestamp"] == (t // M1) * M1]
        e = float(row["open"].iloc[0])
        sg = 1 if side == "LONG" else -1
        stop = round(e * (1 - sg * sp / 100), 2)
        tg = [round(e + sg * r * abs(e - stop), 2) for r in (1.0, 2.0)]
        pos = led.open("SOL/USDT", side, D(str(e)), SizeSpec(D("200"), AmountType.NOTIONAL, 3), stop=stop, targets=tg,
                       setup_type="box_fade", now=dt_of(t), meta={"strategy": "b1_box_fade"})
        assert pos is not None, led.last_reject_reason
        xp_rows.append({"kind": "xp_entry", "trade_key": f"strategy_paper_box|{pos.id}|{pos.opened_at}", "targets": tg})
        until = (t // M1) * M1 + hold
        if walk_sampled(led, "SOL/USDT", m1, opened_ms=t, until_ms=until - M1, bar_ms=M5) is None:
            px = float(m1[m1["timestamp"] == until - M1]["close"].iloc[0])
            led.close_manual("SOL/USDT", D(str(px)), reason="BOX_EOD_FLAT", now=dt_of(until))
    xp = v.state / "shared_experience" / "experience.jsonl"
    xp.parent.mkdir(parents=True, exist_ok=True)
    xp.write_text("".join(json.dumps(r) + "\n" for r in xp_rows), encoding="utf-8")
    sf = StoreFixture(v.paths, now=datetime(2026, 9, 29, 0, 50, tzinfo=UTC))
    fr = {"1m": m1, "5m": aggregate(m1, M5)}
    for tf in ("1h", "4h", "1d"):
        fr[tf] = aggregate(fr["5m"], STEP[tf])
    sf.add_all("SOLUSDT", fr)
    btc = _ohlc(nrng, n // 5, 60000.0, 0.002, a, M5)
    sf.add_all("BTCUSDT", {"5m": btc, "4h": aggregate(btc, STEP["4h"]), "1d": aggregate(btc, STEP["1d"])})
    sf.seal()
    return {"tight": BOX_TIGHT_N, "wide": BOX_WIDE_N}

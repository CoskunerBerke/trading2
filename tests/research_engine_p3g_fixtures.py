# -*- coding: utf-8 -*-
"""P3g testleri için yardımcı (ağ YOK; test dosyası DEĞİLDİR): keşif dünyasını (`synthetic.explore_world`) mühürlü depoya
yazar, giriş evrenini (S1b'nin dondurulmuş 40 coin'i) config'e ve fiyat adımlarını `state/universe.json`'a koyar.

* Depo: her coin için 1d/4h/1h/5m barları, fonlama (satır mark'ı NaN — gerçek arşiv gibi) ve `markpx_1h`; hepsi `archive`
  kaynağıyla (`ResearchStore.write`) + mühür (`StoreFixture.seal`; veri biriminin biçimi).
* Config: `entry_universe: {enabled: true, symbols: [...]}` (S1b sırasıyla) — worker'ın giriş evreni okuması.
* `state/universe.json`: vadeli girdiler ve `filters._tick_size` (gece S4x/S4w fiyat adımı kaynağı, salt-okunur).
* `ag=True` (S1d LIB_AG_V1): her coin'e 5m'den toplanan 15m barları ve AG evreninin 41. sembolü XAUUSDT (`XAU`; GEÇ
  listelenmiş: barları mühürden yalnız `xau_days` önce başlar, öncesi yoktur) + fiyat adımı.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from research_engine_p2_fixtures import StoreFixture

from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import synthetic as SY
from tradingbot.research_engine import universe as U

UTC = timezone.utc
D1 = 86_400_000
COINS40 = list(U.ENTRY_UNIVERSE_FROZEN)
SEAL = int(RG.T_SEAL_MS)
TICK = "0.0001"
M15 = 900_000
XAU = "XAU"


def night_after_seal(days: int, hour: int = 1, minute: int = 40) -> datetime:
    d0 = datetime.fromtimestamp(SEAL // D1 * D1 / 1000, tz=UTC)
    return d0 + timedelta(days=int(days), hours=hour, minutes=minute)


def config_text(coins=COINS40, *, enabled: bool = True) -> str:
    syms = ", ".join(f"{c}/USDT" for c in coins)
    return ("risk: {starting_equity_usdt: 1000}\n"
            "learning_mode: {extra_entries: record_selectivity}\n"
            f"entry_universe: {{enabled: {'true' if enabled else 'false'}, symbols: [{syms}]}}\n")


def write_universe_json(state: Path, coins=COINS40, tick: str = TICK) -> None:
    rows = [{"symbol": f"{c}/USDT", "market_type": "futures", "status": "TRADING",
             "filters": {"_tick_size": tick, "PRICE_FILTER": {"tickSize": tick}}} for c in coins]
    state.mkdir(parents=True, exist_ok=True)
    (state / "universe.json").write_text(json.dumps({"generated_at": "2026-10-13T00:00:00+00:00", "merged": rows}),
                                         encoding="utf-8")


def write_world(paths, world: dict, *, now: datetime, rest_after_ms: int | None = None) -> str:
    """Keşif dünyasını depoya yaz ve mühürle. `rest_after_ms`: bu andan sonra açılan barlar `rest` (doğrulanmamış)."""
    sf = StoreFixture(paths, now=now)
    for coin, w in world.items():
        sym = f"{coin}USDT"
        for tf, rows in w["bars"].items():
            df = pd.DataFrame(rows)[["timestamp", "open", "high", "low", "close", "volume"]]
            if rest_after_ms is None:
                sf.store.write("futures", sym, tf, df, src="archive", now_ms=sf.now_ms)
            else:
                a, b = df[df["timestamp"] < rest_after_ms], df[df["timestamp"] >= rest_after_ms]
                if len(a):
                    sf.store.write("futures", sym, tf, a, src="archive", now_ms=sf.now_ms)
                if len(b):
                    sf.store.write("futures", sym, tf, b, src="rest", now_ms=sf.now_ms)
        fdf = pd.DataFrame(w["funding"], columns=["timestamp", "rate", "mark"])
        fdf["mark"] = float("nan")
        sf.store.write("futures", sym, "funding", fdf, src="archive", now_ms=sf.now_ms)
        sf.store.write("futures", sym, "markpx_1h", pd.DataFrame(w["markpx_1h"])[["timestamp", "open", "high", "low",
                                                                                      "close"]],
                       src="archive", now_ms=sf.now_ms)
    return sf.seal()


def add_ag(world: dict, *, xau_from_ms: int | None = None) -> dict:
    """AG ekleri (yerinde): 15m = 5m'nin toplamı; `XAU` varsa listelenme anından (`xau_from_ms`) önceki satırları yok."""
    for coin, w in world.items():
        if "5m" in w["bars"] and w["bars"]["5m"]:
            w["bars"]["15m"] = SY.aggregate(w["bars"]["5m"], M15)
        if coin == XAU and xau_from_ms is not None:
            w["bars"] = {tf: [r for r in rows if int(r["timestamp"]) >= xau_from_ms] for tf, rows in w["bars"].items()}
            w["funding"] = [x for x in w["funding"] if int(x[0]) >= xau_from_ms]
            w["markpx_1h"] = [r for r in w["markpx_1h"] if int(r["timestamp"]) >= xau_from_ms]
    return world


def p3_world(paths, state: Path, *, coins=COINS40, seed: int = 31, fwd_days: int = 6, pre_1d_days: int = 400,
             pre_1h_days: int = 40, pre_5m_days: int = 2, now: datetime | None = None, ag: bool = False,
             xau_days: int = 30) -> dict:
    """Depo + evren dosyası; dönen: dünya (coin → diziler). `ag`: 15m + XAUUSDT (modül belgesi)."""
    coins = list(coins) + ([XAU] if ag and XAU not in coins else [])
    w = SY.explore_world(coins, seed=seed, seal_ms=SEAL, fwd_days=fwd_days, pre_1d_days=pre_1d_days,
                         pre_1h_days=pre_1h_days, pre_5m_days=pre_5m_days)
    if ag:
        add_ag(w, xau_from_ms=SEAL // D1 * D1 - int(xau_days) * D1)
    write_world(paths, w, now=now or night_after_seal(fwd_days + 1, 1, 0))
    write_universe_json(state, COINS40 + ([XAU] if ag else []))   # fiyat adımları bütün evren için
    return w


def big_p3_world(v, state: Path, *, coins=COINS40, n_trades: int = 120, days: int = 10, history_days: int = 30,
                 seed: int = 41, fwd_days: int = 7, pre_1d_days: int = 400, pre_1h_days: int = 60,
                 pre_5m_days: int = 7, ag: bool = False, xau_days: int = 30) -> dict:
    """Büyük sentetik dünya (P3g kabulü): P2b dünyası (gerçek `FuturesLedgerV2` defterleri, Box %50 …; 1m/5m/1h/4h/1d —
    `research_engine_p2b_fixtures.big_world`) + P3 yükü (40 coin'lik keşif dünyası, fonlama, markpx). P2b'nin sembolleri
    giriş evreniyle çakışmasın diye `P2…` adlarıyla yazılır (iki ayrı fiyat yolu aynı seriye karışmaz); P2b'nin zaman
    tabanı mühürden önceki son 10 güne taşınır. Dönen: {"p2b": defter → işlem, "world": keşif dünyası}."""
    import research_engine_p2b_fixtures as P2BF
    end = datetime.fromtimestamp(SEAL // D1 * D1 / 1000, tz=UTC)
    saved = (P2BF.SYMS, P2BF.MIX, P2BF.RECENT_TO, P2BF.NOW)
    try:
        P2BF.SYMS = {f"P2{k}": p for k, p in saved[0].items()}
        P2BF.MIX = {b: (m[0], "P2" + m[1], *m[2:]) for b, m in saved[1].items()}
        P2BF.RECENT_TO, P2BF.NOW = end, end + timedelta(hours=1, minutes=40)
        counts = P2BF.big_world(v, n_trades=n_trades, days=days, history_days=history_days)
    finally:
        P2BF.SYMS, P2BF.MIX, P2BF.RECENT_TO, P2BF.NOW = saved
    coins_w = list(coins) + ([XAU] if ag and XAU not in coins else [])
    w = SY.explore_world(coins_w, seed=seed, seal_ms=SEAL, fwd_days=fwd_days, pre_1d_days=pre_1d_days,
                         pre_1h_days=pre_1h_days, pre_5m_days=pre_5m_days)
    if ag:
        add_ag(w, xau_from_ms=SEAL // D1 * D1 - int(xau_days) * D1)
    write_world(v.paths, w, now=night_after_seal(fwd_days + 1, 1, 0))
    write_universe_json(state, coins_w)
    return {"p2b": counts, "world": w}


__all__ = ["COINS40", "M15", "SEAL", "TICK", "XAU", "add_ag", "big_p3_world", "config_text", "night_after_seal", "p3_world",
           "write_universe_json", "write_world"]

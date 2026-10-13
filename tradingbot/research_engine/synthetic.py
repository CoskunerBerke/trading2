"""P3b — sentetik dünya: belirlenimci bar ve fonlama serileri (gerçek veri YOK, ağ yok). Yalnız kabul testleri, parite
(a) ve hız ölçümü içindir (§2.1, §5.3; "P3b'nin hız ölçümü yalnız sentetik veriyle yapılır", §8.2); gece birimi bu
modülü import etmez.

Seriler `random.Random(tohum)` ile üretilir (tohum sabit → bayt-aynı seri; `strategy_lib` saf kalır). Rejimli rastgele yürüyüş: her
`regime_len` barda bir yön/eğim seçilir (EMA200 kesişimleri, Donchian kırılımları ve mum formasyonları çıksın diye);
ara sıra açılış boşlukları (boşluk dolumu testleri). Günlük barlar 5m barlarından toplanır (Box'un kutusu tutarlı olsun).
4h/1d dünyalarında her sinyal barının **1h alt barları** (`subdivide`; ayrı tohumlu, sinyal serisini DEĞİŞTİRMEZ) barla
tutarlı kurulur: ilk açılış = barın açılışı, son kapanış = barın kapanışı, uçların en büyüğü/küçüğü = barın yüksek/düşüğü,
her alt bar barın aralığında (çekirdek ve parite (a) başvurusu canlı defter gibi 1h tikleriyle çıkar; §2.1).
Fonlama: 00/08/16 UTC uzlaşmaları, küçük oranlar, mark = o andaki barın açılışı. P3b-2b: her dünya örtü `F_BTC` için
BTC 4h barları taşır (`btc_4h`; 1d dünyasında BTC 1d serisinin tutarlı alt barları, diğerlerinde ayrı tohumlu yürüyüş);
yalnız `F_BTC` okur (diğer varyantların sonuçları değişmez).
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any

from . import kernel as KN

M5, H1, H4, D1 = 300_000, 3_600_000, 14_400_000, 86_400_000
T0 = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def walk(n: int, *, seed: int, step: int, start: int = T0, p0: float = 100.0, vol: float = 0.02, trend: float = 0.004,
         regime_len: int = 60, gap_p: float = 0.02, gap_vol: float = 0.03, wick: float = 0.6,
         up_bias: float = 0.0) -> list[dict[str, Any]]:
    rnd = random.Random(seed)
    out: list[dict[str, Any]] = []
    c = p0
    mu = 0.0
    for i in range(n):
        if i % regime_len == 0:
            mu = rnd.choice((-trend, 0.0, trend, trend * up_bias if up_bias else 0.0))
        o = c * math.exp(rnd.gauss(0.0, gap_vol)) if rnd.random() < gap_p else c
        r = mu + rnd.gauss(0.0, vol)
        cl = o * math.exp(r)
        hi = max(o, cl) * (1.0 + abs(rnd.gauss(0.0, vol * wick)))
        lo = min(o, cl) * (1.0 - abs(rnd.gauss(0.0, vol * wick)))
        out.append({"timestamp": start + i * step, "open": o, "high": hi, "low": lo, "close": cl,
                    "volume": 1000.0 * (1.0 + abs(rnd.gauss(0.0, 0.5)))})
        c = cl
    return out


def aggregate(rows: list[dict[str, Any]], step: int) -> list[dict[str, Any]]:
    """Küçük dilim barlarını `step` barlarına topla (yalnız TAM barlar)."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    n_need = None
    cnt = 0
    for r in rows:
        k = int(r["timestamp"]) // step * step
        if cur is None or cur["timestamp"] != k:
            if cur is not None and cnt == n_need:
                out.append(cur)
            cur = {"timestamp": k, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                   "volume": r["volume"]}
            n_need = step // (int(rows[1]["timestamp"]) - int(rows[0]["timestamp"]))
            cnt = 1
        else:
            cur["high"] = max(cur["high"], r["high"])
            cur["low"] = min(cur["low"], r["low"])
            cur["close"] = r["close"]
            cur["volume"] += r["volume"]
            cnt += 1
    if cur is not None and cnt == n_need:
        out.append(cur)
    return out


def subdivide(rows: list[dict[str, Any]], step: int, sub_step: int, *, seed: int) -> list[dict[str, Any]]:
    """Her `step` barını `step // sub_step` TUTARLI alt bara böl (belirlenimci, `random.Random(seed)`): yol noktaları
    açılıştan kapanışa bir Brown köprüsü (barın aralığına kırpılır); alt bar k: açılış = nokta k, kapanış = nokta k+1,
    uçlar gövdeden küçük fitillerle (aralığa kırpılır); barın yükseği yolun en yüksek, düşüğü en düşük alt barına
    yazılır. Böylece max(alt yüksek) = yüksek, min(alt düşük) = düşük, ilk açılış/son kapanış = bar açılış/kapanışı."""
    rnd = random.Random(seed)
    n = step // sub_step
    out: list[dict[str, Any]] = []
    for r in rows:
        o, h, lo, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        t0 = int(r["timestamp"])
        if n <= 1:
            out.append(dict(r))
            continue
        w = [0.0]
        for _k in range(n):
            w.append(w[-1] + rnd.gauss(0.0, 1.0))
        scale = (h - lo) / (2.0 * math.sqrt(n))
        pts = [min(h, max(lo, o + (c - o) * k / n + scale * (w[k] - w[n] * k / n))) for k in range(n + 1)]
        pts[0], pts[n] = o, c
        wick = (h - lo) * 0.15 / math.sqrt(n)
        subs = []
        for k in range(n):
            a, b = pts[k], pts[k + 1]
            subs.append([a, min(h, max(a, b) + abs(rnd.gauss(0.0, wick))), max(lo, min(a, b) - abs(rnd.gauss(0.0, wick))), b])
        k_hi = max(range(n), key=lambda k: (max(subs[k][0], subs[k][3]), -k))
        k_lo = min(range(n), key=lambda k: (min(subs[k][0], subs[k][3]), k))
        subs[k_hi][1], subs[k_lo][2] = h, lo
        vols = [rnd.random() + 0.2 for _k in range(n)]
        tot = sum(vols)
        for k, (a, hh, ll, b) in enumerate(subs):
            out.append({"timestamp": t0 + k * sub_step, "open": a, "high": hh, "low": ll, "close": b,
                        "volume": float(r.get("volume") or 0.0) * vols[k] / tot})
    return out


def funding_rows(rows: list[dict[str, Any]], step: int, *, seed: int, hours=(0, 8, 16)) -> list[tuple[int, float, float]]:
    """00/08/16 UTC uzlaşmaları; mark = uzlaşma anını içeren barın açılışı (tam açılışta o barın açılışı)."""
    rnd = random.Random(seed)
    if not rows:
        return []
    start, end = int(rows[0]["timestamp"]), int(rows[-1]["timestamp"]) + step
    by_open = {int(r["timestamp"]): r for r in rows}
    out = []
    t = start // H1 * H1
    while t <= end + D1:
        if (t // H1) % 24 in hours:
            k = t // step * step
            bar = by_open.get(k) or by_open.get(k - step)
            mark = float(bar["open"]) if bar is not None else float(rows[-1]["close"])
            out.append((t, rnd.gauss(0.0001, 0.0003), mark))
        t += H1
    return out


def btc_4h_walk(start_ms: int, end_ms: int, *, seed: int, warm: int = 300) -> list[dict[str, Any]]:
    """Örtü `F_BTC` (P3b-2b) için BTC 4h barları: serinin başından `warm` bar ÖNCE başlar (EMA200 rejimi ilk karardan
    ölçülebilsin) ve sonuna kadar sürer; uzun yön rejimleri (UP ve DOWN ikisi de görülür). Ayrı tohum: sinyal serisi
    değişmez."""
    t0 = int(start_ms) // H4 * H4 - warm * H4
    n = (int(end_ms) - t0) // H4 + 1
    return walk(n, seed=seed, step=H4, start=t0, p0=20000.0, vol=0.012, trend=0.004, regime_len=240, gap_p=0.0)


#: BTC 4h serisinin tohum kayması
BTC4_SEED = 7000


def series(symbol: str, bars: dict[str, list[dict[str, Any]]], *, signal_tf: str, btc: list | None = None,
           seed: int = 7, tick: float | None = None, btc_4h: list | None = None) -> KN.SeriesData:
    """`tick`: verilmezse ilk fiyatın ≈ 1e-5'i (10'un kuvveti; büyük coin'lerin göreli adımı mertebesinde). `btc_4h`
    verilmezse sinyal serisini örten `btc_4h_walk` (ayrı tohum; örtü `F_BTC` dışında hiçbir varyant okumaz)."""
    step = {"5m": M5, "1h": H1, "4h": H4, "1d": D1}[signal_tf]
    if tick is None:
        tick = 10.0 ** math.floor(math.log10(float(bars[signal_tf][0]["open"]) * 1e-5))
    if btc_4h is None:
        rows = bars[signal_tf]
        btc_4h = btc_4h_walk(int(rows[0]["timestamp"]), int(rows[-1]["timestamp"]) + step, seed=seed + BTC4_SEED)
    return KN.SeriesData(symbol=symbol, bars=bars, btc_1d=btc, funding=funding_rows(bars[signal_tf], step, seed=seed),
                         price_tick=tick, btc_4h=btc_4h)


# ---------------------------------------------------------------------------- aile dünyaları
#: alt bar serisinin tohum kayması (sinyal serisinin tohumundan bağımsız; sinyal serisi değişmez)
SUB_SEED = 5000


def _with_1h(bars: dict[str, list], tf: str, seed: int, sub: bool) -> dict[str, list]:
    """`sub` → sinyal dilimine (4h/1d) tutarlı 1h alt barlar ekle (`subdivide`); `sub=False` alt barsız dünya (çekirdeğin
    `PATH_1H_GAP` yedeği için)."""
    if sub:
        step = {"4h": H4, "1d": D1}[tf]
        bars = dict(bars, **{"1h": subdivide(bars[tf], step, H1, seed=seed + SUB_SEED)})
    return bars


def trend_world(seed: int, n: int = 900, *, sub: bool = True) -> KN.SeriesData:
    """T2/M2: 1d; BTC uzun yükseliş rejimleri (rejim UP sık); coin EMA200 çevresinde sık kesişir."""
    btc = walk(n, seed=seed + 1000, step=D1, p0=20000.0, vol=0.025, trend=0.006, regime_len=150, up_bias=1.0)
    d1 = walk(n, seed=seed, step=D1, p0=50.0, vol=0.03, trend=0.004, regime_len=15, gap_p=0.03)
    return series(f"SYN{seed}", _with_1h({"1d": d1}, "1d", seed, sub), signal_tf="1d", btc=btc, seed=seed,
                  btc_4h=subdivide(btc, D1, H4, seed=seed + BTC4_SEED))


def d4_world(seed: int, n: int = 2200, *, sub: bool = True) -> KN.SeriesData:
    h4 = walk(n, seed=seed, step=H4, p0=30.0, vol=0.015, trend=0.004, regime_len=50, gap_p=0.02, gap_vol=0.015)
    return series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)


def c4_world(seed: int, n: int = 2600, *, sub: bool = True) -> KN.SeriesData:
    h4 = walk(n, seed=seed, step=H4, p0=30.0, vol=0.02, trend=0.003, regime_len=40, gap_p=0.01, gap_vol=0.01, wick=0.9)
    return series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)


def fm_world(seed: int, n: int = 2600, *, sub: bool = True) -> KN.SeriesData:
    h4 = walk(n, seed=seed, step=H4, p0=10.0, vol=0.015, trend=0.012, regime_len=12, gap_p=0.01, gap_vol=0.01,
              wick=0.25, up_bias=1.5)
    return series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)


def box_world(seed: int, days: int = 60, *, sub: bool = True) -> KN.SeriesData:
    """Box: sinyal dilimi 5m (≤ 1h) → çıkış tikleri 5m barlarının kendisidir; `sub` yok sayılır (dünyaların ortak imzası)."""
    del sub
    m5 = walk(days * 288, seed=seed, step=M5, p0=100.0, vol=0.0025, trend=0.0004, regime_len=36, gap_p=0.002,
              gap_vol=0.002, wick=0.8)
    d1 = aggregate(m5, D1)
    return series(f"SYN{seed}", {"1d": d1, "5m": m5}, signal_tf="5m", seed=seed)


def h1_world(seed: int, n: int = 3000, *, sub: bool = True) -> KN.SeriesData:
    """E/F1 1h (P3b-2): sinyal dilimi 1h → çıkış tikleri barın kendisi (`sub` yok sayılır); trend ve yatay rejimler (ER20
    iki yanda), ara sıra boşluk."""
    del sub
    h1 = walk(n, seed=seed, step=H1, p0=40.0, vol=0.008, trend=0.002, regime_len=48, gap_p=0.005, gap_vol=0.004, wick=0.7)
    return series(f"SYN{seed}", {"1h": h1}, signal_tf="1h", seed=seed)


def ar_funding_rows(rows: list[dict[str, Any]], step: int, *, seed: int, hours=(0, 8, 16), base: float = 0.0001,
                    phi: float = 0.92, vol: float = 0.00012, switch_ms: int | None = None, hours_after=(0, 4, 8, 12, 16, 20),
                    jitter_ms: int = 0, drop_ms: tuple[int, ...] = ()) -> list[tuple[int, float, float]]:
    """H ailesi (P3b-2): KALICI fonlama rejimleri — AR(1) 8 saatlik eşdeğer oran (taban + φ × (önceki − taban) + gürültü)
    `hours` UTC uzlaşmalarında; mark = uzlaşma anını içeren barın açılışı (`funding_rows` ile aynı). İnceleme düzeltmesi
    (P3b-2a, 2026-10-12): `switch_ms`'den itibaren `hours_after` saatleri (aralık değişimi; uzlaşmanın oranı örttüğü
    dönemle — önceki nominal uzlaşmadan bu yana geçen saat / 8 — ölçeklenir → f8 sürekli; geçişin ilk uzlaşması hâlâ 8
    saati örter); `jitter_ms` > 0: damgaya [0, jitter_ms] ms kayma (borsanın ms kayması; ayrı tohum, oran dizisi
    değişmez); `drop_ms`: bu NOMİNAL anlardaki uzlaşmalar yazılmaz (deponun eksik satırı; borsada uzlaşma olmuştur, oran
    süreci ve dönem ölçeği ilerler). Varsayılanlarla P3b-2a dünyasıyla bayt-aynı."""
    rnd = random.Random(seed)
    jit = random.Random(seed * 7919 + 1)
    if not rows:
        return []
    start, end = int(rows[0]["timestamp"]), int(rows[-1]["timestamp"]) + step
    by_open = {int(r["timestamp"]): r for r in rows}
    drop = {int(x) for x in drop_ms}
    out = []
    r = base
    prev: int | None = None
    t = start // H1 * H1
    while t <= end + D1:
        hs = hours if switch_ms is None or t < int(switch_ms) else hours_after
        if (t // H1) % 24 in hs:
            k = t // step * step
            bar = by_open.get(k) or by_open.get(k - step)
            mark = float(bar["open"]) if bar is not None else float(rows[-1]["close"])
            r = base + phi * (r - base) + rnd.gauss(0.0, vol)
            iv = (t - prev) // H1 if prev is not None else 24 // len(hs)
            prev = t
            off = jit.randint(0, int(jitter_ms)) if jitter_ms > 0 else 0
            if t not in drop:
                out.append((t + off, r * iv / 8.0 if iv != 8 else r, mark))
        t += H1
    return out


def fund_world(seed: int, n: int = 2200, *, sub: bool = True) -> KN.SeriesData:
    """H (P3b-2): 4h barlar (tutarlı 1h alt barlarla) + kalıcı rejimli fonlama (`ar_funding_rows`). İnceleme düzeltmesi
    (P3b-2a, 2026-10-12): seri zaman ekseninin %55'inde 8h'ten (00/08/16) 4h'e (00/04/…/20) geçer, her damgada 0–999 ms
    kayma vardır ve iki uzlaşma eksiktir (8h döneminde %30'daki, 4h döneminde %80'deki ilk uzlaşma) — aralık kuralı, damga
    yuvarlaması ve boşluk (çekirdek ↔ defter) parite (a)'da sınanır."""
    h4 = walk(n, seed=seed, step=H4, p0=25.0, vol=0.015, trend=0.003, regime_len=40, gap_p=0.01, gap_vol=0.01)
    d = series(f"SYN{seed}", _with_1h({"4h": h4}, "4h", seed, sub), signal_tf="4h", seed=seed)
    t0, t1 = int(h4[0]["timestamp"]), int(h4[-1]["timestamp"]) + H4
    switch = (t0 + (t1 - t0) * 55 // 100) // D1 * D1
    drops = ((t0 + (t1 - t0) * 30 // 100) // (8 * H1) * (8 * H1) + 8 * H1,
             (t0 + (t1 - t0) * 80 // 100) // (4 * H1) * (4 * H1) + 4 * H1)
    fr = ar_funding_rows(h4, H4, seed=seed + 77, switch_ms=switch, jitter_ms=999, drop_ms=drops)
    return KN.SeriesData(symbol=d.symbol, bars=d.bars, btc_1d=None, funding=fr, price_tick=d.price_tick)


#: K kopyası → aile birimi ve dünyası (parite (a) ve hız ölçümü)
WORLDS = {"T2": trend_world, "M2": trend_world, "BOX": box_world, "D4": d4_world, "C4": c4_world, "FM": fm_world}


def world_for(variant_id: str, signal_tf: str, family: str):
    """P3b-2 saf aileleri ve F2 için dünya: F2 → Box (5m); H → `fund_world`; aksi sinyal dilimine göre (1d → trend,
    4h → D4, 1h → `h1_world`)."""
    del variant_id
    if family == "F2":
        return box_world
    if family == "H":
        return fund_world
    return {"1d": trend_world, "4h": d4_world, "1h": h1_world}[signal_tf]


# ---------------------------------------------------------------------------- P3d: Kapı A dünyası (işlem düzeyi)
#: Kapı A sentetik dünyasının satırları: bu ailelerin `POOLED_P23` satırları (gerçek satır kimlikleri; BH ailesi 818)
GATE_ROW_FAMILIES = ("B", "C", "D", "E")
GATE_SYMBOLS = ("BTC", "ETH", "SOL", "BNB")


def gate_rows(families: tuple[str, ...] = GATE_ROW_FAMILIES) -> list[dict[str, Any]]:
    from . import registry as RG
    vs = RG.variants_by_id()
    return [r for r in RG.gate_a_rows() if r["row_type"] == "POOLED" and vs[r["unit"]]["family"] in families]


def _atr14(rows: list[dict[str, Any]], n: int = 14) -> list[float]:
    out, acc, prev = [], [], None
    for r in rows:
        h, lo = float(r["high"]), float(r["low"])
        tr = h - lo if prev is None else max(h - lo, abs(h - prev), abs(lo - prev))
        acc.append(tr)
        out.append(sum(acc[-n:]) / n if len(acc) >= n else math.nan)
        prev = float(r["close"])
    return out


_COST_FRAC: dict[str, float] = {}


def _cost_frac() -> dict[str, float]:
    """Senaryo başına gidiş-dönüş maliyet payı (2 × taker + 2 × kayma; mühürlü `WF_PROTOCOL_V1.thresholds.costs`)."""
    if not _COST_FRAC:
        from . import registry as RG
        c = RG.WF_PROTOCOL_V1["thresholds"]["costs"]
        _COST_FRAC.update({k: 2.0 * float(v["taker_fee_pct"]) / 100.0 + 2.0 * float(v["slippage_bps"]) / 10_000.0
                           for k, v in c.items()})
    return _COST_FRAC


def light_trade(rows: list[dict[str, Any]], atr: list[float], i: int, long: bool, hold: int, k_atr: float, *,
                stop_active: bool = True) -> dict[str, Any] | None:
    """Hafif günlük yol: karar i. barın kapanışında, dolum i+1 açılışında; stop = kapanış ∓ k × ATR14 (boşlukta açılıştan),
    yoksa i+hold kapanışında çık. R = yön × (çıkış − giriş) / |giriş − stop| − maliyet payı (senaryo başına; 1 USDT risk →
    pnl = R). `stop_active=False` (plasebo, R5 maruziyet eşleşmesi): stop yalnız R birimidir, çıkış tam `hold` sonunda."""
    if i + hold >= len(rows) or not (atr[i] > 0):
        return None
    c = float(rows[i]["close"])
    stop = c - k_atr * atr[i] if long else c + k_atr * atr[i]
    e = float(rows[i + 1]["open"])
    if stop <= 0 or (long and e <= stop) or (not long and e >= stop):
        return None
    dist = abs(e - stop)
    x, j = float(rows[i + hold]["close"]), i + hold
    for j in range(i + 1, i + hold + 1 if stop_active else i + 1):
        o, h, lo = float(rows[j]["open"]), float(rows[j]["high"]), float(rows[j]["low"])
        if long and lo <= stop:
            x = min(o, stop)
            break
        if not long and h >= stop:
            x = max(o, stop)
            break
    gross = (x - e) / dist * (1.0 if long else -1.0)
    r = {k: gross - f * e / dist for k, f in _cost_frac().items()}
    r["GROSS"] = gross                                                      # brüt R (R5; ücret/kayma/spread yok)
    t0 = int(rows[i]["timestamp"])
    return {"ts_ms": t0 + D1, "as_of_ms": t0 + D1, "exit_ms": int(rows[j]["timestamp"]) + D1, "day": (t0 + D1) // D1,
            "signal_ts": t0, "side": "LONG" if long else "SHORT", "initial_stop": stop, "entry_ref": e, "exit_ref": x,
            "bars_held": j - i, "r": r, "pnl": dict(r), "flags": [], "excluded": False}


def vol_walk(n: int, *, seed: int, start: int = T0, p0: float = 100.0, sig_lo: float = 0.005, sig_hi: float = 0.03,
             mean_regime: int = 40, drift_sharpe: float = 0.0) -> list[dict[str, Any]]:
    """Oynaklık kümelenmeli günlük yürüyüş (P3d inceleme düzeltmesi 5a): rejimler (ortalama `mean_regime` gün, geometrik)
    düşük/yüksek oynaklık arasında geçer; sürüklenme o günün oynaklığıyla orantılı (μ_t = `drift_sharpe` × σ_t: yalnız risk
    primi). Her bar rejimin σ'sını taşır (`sigma`); `gate_world` bu dünyada stopu gecikmesiz oynaklıkla ölçer (ATR =
    1,25 × σ_t × kapanış) → her girişin beklenen BRÜT R'ı aynıdır (zamanlama avantajı yok); yüksek oynaklıkta yalnız R
    cinsinden maliyet düşüktür."""
    rnd = random.Random(seed)
    out: list[dict[str, Any]] = []
    c, hi = p0, False
    for i in range(n):
        if rnd.random() < 1.0 / mean_regime:
            hi = not hi
        sg = sig_hi if hi else sig_lo
        o = c
        cl = o * math.exp(drift_sharpe * sg + rnd.gauss(0.0, sg))
        h = max(o, cl) * (1.0 + abs(rnd.gauss(0.0, sg * 0.6)))
        lo = min(o, cl) * (1.0 - abs(rnd.gauss(0.0, sg * 0.6)))
        out.append({"timestamp": start + i * D1, "open": o, "high": h, "low": lo, "close": cl, "volume": 1000.0,
                    "sigma": sg})
        c = cl
    return out


def _realized_vol(rows: list[dict[str, Any]], i: int, n: int = 10) -> float:
    if i < n:
        return math.nan
    xs = [math.log(float(rows[j]["close"]) / float(rows[j - 1]["close"])) for j in range(i - n + 1, i + 1)]
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _hold_cap(unit: str) -> int:
    """Satırın mühürlü sınır boşluğuna sığan en uzun hafif tutuş (gün): ⌊(1 + max_hold) × bar / gün⌋ (sızıntısız)."""
    from . import wf as WF
    tf = str(WF._resolve(unit)["signal_tf"])
    return max(1, (1 + WF.max_hold_bars(unit)) * WF.TF_MS[tf] // D1)


def gate_world(seed: int, *, days: int = 2200, symbols: tuple[str, ...] = GATE_SYMBOLS, planted: str | None = None,
               edge: float = 0.85, k_atr: float = 2.0, seal_days: int = 120, flag_rate: float = 0.0,
               rows: list[dict[str, Any]] | None = None, vol_clustered: bool = False, vol_timed: str | None = None,
               drift_sharpe: float = 0.0) -> dict[str, Any]:
    """İşlem düzeyi Kapı A dünyası (P3d kabulü: "200 boş dünya" ve "dikilmiş avantaj"): her sembolde rastgele yürüyüş
    günlük barları (`walk`, eğim 0; `vol_clustered` → `vol_walk`); her satır (gerçek `POOLED_P23` satır kimliği) her
    sembolde düzlükte günlük olasılık q ile sinyal verir (tutuş 2–5 gün ama satırın mühürlü sınır boşluğuna sığacak kadar,
    stop 2 × ATR14), yön boş dünyada yazı-tura. `planted` satırında yön, tutuşun sonundaki getirinin işaretine `edge`
    olasılıkla uyar (geleceği gören kâhin = dikilmiş avantaj). `vol_timed` satırı yalnız gerçekleşen oynaklık yüksekken
    (10 günlük std > (σ_düşük + σ_yüksek) / 2) ve yalnız long girer — zamanlama avantajı yoktur, yalnız sürüklenme ve
    yüksek oynaklıkta R cinsinden düşük maliyet (inceleme düzeltmesi 5a'nın tuzağı). `flag_rate` payında işlem
    `FUNDING_GAP` işaretlenir (kapı dışı). İleri okuma = mühür anından (`seal_ms` = son − `seal_days`) sonraki işlemler."""
    from .stats import mtm_allocation
    rows = rows if rows is not None else gate_rows()
    bars: dict[str, list[dict[str, Any]]] = {}
    atrs: dict[str, list[float]] = {}
    for k, sym in enumerate(symbols):
        if vol_clustered:
            b = vol_walk(days, seed=seed * 101 + k, p0=100.0 + 10 * k, drift_sharpe=drift_sharpe)
            bars[sym], atrs[sym] = b, [1.25 * float(x["sigma"]) * float(x["close"]) for x in b]
        else:
            b = walk(days, seed=seed * 101 + k, step=D1, p0=100.0 + 10 * k, vol=0.02, trend=0.0, regime_len=days + 1,
                     gap_p=0.0)
            bars[sym], atrs[sym] = b, _atr14(b)
    vol_cut = (0.005 + 0.03) / 2.0
    close_t = {sym: [int(x["timestamp"]) + D1 for x in b] for sym, b in bars.items()}
    trades: dict[str, list[dict[str, Any]]] = {}
    signals: dict[tuple[str, str], set[int]] = {}
    params: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid, unit = row["row_id"], row["unit"]
        prng = random.Random(f"{seed}|{rid}|params")
        cap = _hold_cap(unit)
        hold = prng.randint(min(2, cap), min(5, cap))
        q = 1.0 if rid == vol_timed else (0.25 if rid == planted else prng.uniform(0.08, 0.2))
        params[rid] = {"hold": hold, "q": q, "planted": rid == planted, "vol_timed": rid == vol_timed}
        out: list[dict[str, Any]] = []
        for sym in symbols:
            rng = random.Random(f"{seed}|{rid}|{sym}")
            b, a = bars[sym], atrs[sym]
            sig: set[int] = set()
            i = 20
            while i < len(b) - hold - 1:
                if rid == vol_timed and not _realized_vol(b, i) > vol_cut:
                    i += 1
                    continue
                if rng.random() >= q:
                    i += 1
                    continue
                if rid == planted:
                    up = float(b[i + hold]["close"]) >= float(b[i + 1]["open"])
                    long = up if rng.random() < edge else not up
                elif rid == vol_timed:
                    long = True
                else:
                    long = rng.random() < 0.5
                t = light_trade(b, a, i, long, hold, k_atr)
                sig.add(int(b[i]["timestamp"]) + D1)
                if t is None:
                    i += 1
                    continue
                t.update(symbol=sym, option=unit, trade_key=f"{unit}|{sym}:{t['signal_ts']}:{t['side']}|{t['ts_ms']}")
                t["mtm"], t["mtm_method"] = mtm_allocation(t, b, D1, close_t=close_t[sym])
                if flag_rate and rng.random() < flag_rate:
                    t["flags"] = ["FUNDING_GAP"]
                    t["excluded"] = KN.gate_excluded(t)
                out.append(t)
                i += t["bars_held"] + 1
            signals[(rid, sym)] = sig
        trades[rid] = out
    end = T0 + days * D1
    return {"seed": seed, "bars": bars, "atr": atrs, "symbols": tuple(symbols), "rows": rows, "trades": trades,
            "signals": signals, "params": params, "first_ms": T0, "end_ms": end, "seal_ms": end - seal_days * D1,
            "k_atr": k_atr, "planted": planted, "vol_timed": vol_timed}


def gate_world_placebos(world: dict[str, Any], row_id: str, real: list[dict[str, Any]], *,
                        k_per_trade: int | None = None) -> list[dict[str, Any]]:
    """Satırın OOS gerçek işlemlerinin plaseboları (`placebo.eligible_times` + `draw_placebos`; hafif yol, aynı yön/tutuş/
    ATR katı)."""
    from . import placebo as PL
    kpt = PL.K_PER_TRADE if k_per_trade is None else k_per_trade
    out: list[dict[str, Any]] = []
    allt = world["trades"][row_id]
    for sym in world["symbols"]:
        b, a = world["bars"][sym], world["atr"][sym]
        idx = {int(r["timestamp"]) + D1: i for i, r in enumerate(b)}
        dec = sorted(idx)
        spans = [(t["ts_ms"], max(t["exit_ms"] - t["ts_ms"], D1)) for t in allt if t["symbol"] == sym]
        cache: dict[tuple[int, int], list[int]] = {}
        for tr in (x for x in real if x["symbol"] == sym):
            w = (int(tr["test_window"][0]), int(tr["test_window"][1]))
            if w not in cache:
                cache[w] = PL.eligible_times(dec, w[0], w[1], world["signals"][(row_id, sym)], spans)
            long, hold = tr["side"] == "LONG", int(tr["bars_held"])

            def sim(t: int, long: bool = long, hold: int = hold) -> dict[str, Any] | None:
                x = light_trade(b, a, idx[t], long, hold, world["k_atr"], stop_active=False)
                return None if x is None else {"day": x["day"], "r": x["r"], "excluded": False}
            out += PL.draw_placebos(row_id, str(tr["trade_key"]), cache[w], sim, k_per_trade=kpt)
    return out


def gate_world_fwd(world: dict[str, Any], row_id: str) -> dict[str, Any]:
    """Mühür sonrası ileri okuma: `seal_ms`'ten sonra açılan (kapı dışı olmayan) işlemler."""
    f = [t for t in world["trades"][row_id] if t["ts_ms"] >= world["seal_ms"] and not t["excluded"]]
    return {"days": (world["end_ms"] - world["seal_ms"]) // D1, "n": len(f),
            "mean_adverse": (sum(t["r"]["ADVERSE"] for t in f) / len(f)) if f else None, "intraday": False}


__all__ = ["BTC4_SEED", "WORLDS", "D1", "GATE_ROW_FAMILIES", "GATE_SYMBOLS", "H1", "H4", "M5", "SUB_SEED", "T0", "aggregate",
           "ar_funding_rows", "box_world", "gate_rows", "gate_world", "gate_world_fwd", "gate_world_placebos", "light_trade",
           "vol_walk",
           "btc_4h_walk", "c4_world",
           "d4_world", "fm_world", "fund_world", "funding_rows", "h1_world", "series", "subdivide", "trend_world", "walk",
           "world_for"]

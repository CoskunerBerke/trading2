"""Yeni hafif uzman ajanlar (saf fonksiyonlar, ağ yok) + legacy ajan adaptörü.

Her uzman `SpecialistReport` döndürür. Girdiler düz dict/DataFrame'lerdir; hata durumunda rapor `error` taşır,
istisna dışarı sızmaz (fakat sessizce yutulmaz: error alanı dolar, veri kalitesine yansır).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..core import iso, stable_id, utc_now
from .factors import LEGACY_GROUP, split_market_report
from .schema import SpecialistReport, stance_from_bias

REGIMES = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_VOL", "SQUEEZE", "BREAKOUT", "PANIC", "EUPHORIC", "ILLIQUID", "UNKNOWN")


@dataclass
class SpecialistContext:
    symbol: str
    run_id: str
    snapshot_id: str
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)          # {"1d","4h","1h"} snapshot göstergeli (ema20/50/200, rsi14, atr14, adx14, atr_pct)
    live: dict[str, Any] = field(default_factory=dict)                     # ticker/orderbook/funding/open_interest/long_short (+ opsiyonel funding_history, oi_history)
    market_type: str = "both"
    quality: dict[str, Any] = field(default_factory=dict)                  # DataQualityGate raporu {ok, verdict, issues, details}
    btc_frames: dict[str, pd.DataFrame] | None = None
    eth_frames: dict[str, pd.DataFrame] | None = None
    equity_usdt: float = 50.0
    risk_pct: float = 2.0
    stop_pct: float | None = None                                          # plan stop mesafesi (%)
    target_notional: float | None = None
    filters: dict[str, Any] = field(default_factory=dict)                  # {min_notional, qty_step, price_tick, max_leverage}
    fee_taker_pct: float = 0.05
    max_leverage: int = 5
    now_ms: int | None = None
    listing_age_days: float | None = None


def _base(ctx: SpecialistContext, name: str, group: str, version: str = "v3.0") -> SpecialistReport:
    return SpecialistReport(analysis_id=stable_id("analysis", ctx.run_id, ctx.snapshot_id, ctx.symbol, name), run_id=ctx.run_id,
                            snapshot_id=ctx.snapshot_id, symbol=ctx.symbol, market_type=ctx.market_type, agent_name=name,
                            agent_version=version, as_of_utc=iso(utc_now()), factor_group=group)


def _finish(rep: SpecialistReport, t0: float) -> SpecialistReport:
    rep.bias = float(max(-1.0, min(1.0, rep.bias)))
    rep.confidence_raw = float(max(0.0, min(100.0, rep.confidence_raw)))
    rep.confidence_calibrated = round(rep.confidence_raw / 100.0, 4)
    rep.stance = stance_from_bias(rep.bias)
    rep.latency_ms = round((time.time() - t0) * 1000, 2)
    return rep


def _run(name: str, group: str, fn: Callable[[SpecialistContext, SpecialistReport], None]) -> Callable[[SpecialistContext], SpecialistReport]:
    def runner(ctx: SpecialistContext) -> SpecialistReport:
        t0 = time.time()
        rep = _base(ctx, name, group)
        try:
            fn(ctx, rep)
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError, AttributeError) as exc:
            rep.error = f"{type(exc).__name__}: {exc}"
            rep.bias, rep.confidence_raw = 0.0, 0.0
        return _finish(rep, t0)
    runner.agent_name = name  # type: ignore[attr-defined]
    return runner


def _last(df: pd.DataFrame, col: str, k: int = 1) -> float:
    return float(df[col].iloc[-k])


def _pct_rank(s: pd.Series, v: float) -> float:
    s = s.dropna()
    return float((s < v).mean() * 100) if len(s) >= 20 else 50.0


# ---------------------------------------------------------------- DATA_INTEGRITY
def _data_integrity(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    q = ctx.quality or {}
    issues = list(q.get("issues", []))
    have = [tf for tf in ("1d", "4h", "1h") if ctx.frames.get(tf) is not None and len(ctx.frames[tf]) > 50]
    rep.timeframes = have
    rep.data_sources = list(q.get("sources", [])) or ["frames"]
    if "4h" not in have:
        issues.append("MISSING_4H_FRAME")
    live = ctx.live or {}
    if not (live.get("ticker") or {}).get("last"):
        issues.append("NO_LIVE_TICKER")
    ts = live.get("ts")
    if ts and ctx.now_ms:
        age = ctx.now_ms / 1000 - float(ts)
        rep.data_freshness_seconds = round(age, 1)
        if age > 300:
            issues.append("STALE_TICKER")
    rep.metrics = {"issues": issues, "frames": have, "quality_verdict": q.get("verdict", "UNKNOWN")}
    hard = {"MISSING_4H_FRAME", "STALE_CANDLE", "MISSING_BARS", "DUPLICATE_BARS", "UNSORTED", "ZERO_PRICE", "CLOCK_DRIFT", "PRICE_DIVERGENCE", "INSUFFICIENT_BARS"}
    bad = [i for i in issues if i in hard] or (["DATA_INVALID"] if q.get("verdict") == "DATA_INVALID" else [])
    if bad:
        rep.veto, rep.veto_reason = True, "NO_TRADE_DATA_INVALID: " + ", ".join(bad)
        rep.warnings.append(rep.veto_reason)
        rep.evidence_against = [f"Veri bütünlüğü: {b}" for b in bad]
        rep.confidence_raw = 90
    else:
        rep.evidence_for = ["Bar sırası/eksik/duplicate kontrolü temiz", f"Zaman dilimleri: {', '.join(have)}"]
        rep.confidence_raw = 80
        if "STALE_TICKER" in issues or "NO_LIVE_TICKER" in issues:
            rep.warnings.append("Canlı ticker bayat/yok — kapanış fiyatı yalnızca analiz için, fill için değil")


DataIntegrityAgent = _run("data_integrity", "risk", _data_integrity)


# ---------------------------------------------------------------- MARKET_REGIME
def classify_regime(df: pd.DataFrame, btc_regime: str | None = None) -> tuple[str, dict]:
    c = df["close"]
    adx = _last(df, "adx14")
    e20, e50, e200 = _last(df, "ema20"), _last(df, "ema50"), _last(df, "ema200")
    slope = (e50 / float(df["ema50"].iloc[-11]) - 1) * 100 if len(df) > 11 else 0.0
    atr_rank = _pct_rank(df["atr_pct"].iloc[-200:], _last(df, "atr_pct"))
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    bbw = ((mid + 2 * std) - (mid - 2 * std)) / mid * 100
    bbw_rank = _pct_rank(bbw.iloc[-200:], float(bbw.iloc[-1]))
    rets = np.log(c).diff().dropna().iloc[-30:]
    ac1 = float(rets.autocorr(1)) if len(rets) > 5 else 0.0
    chg5 = float(c.iloc[-1] / c.iloc[-6] - 1) * 100 if len(c) > 6 else 0.0
    m = {"adx": round(adx, 1), "ema50_slope_pct": round(slope, 3), "atr_pct_rank": round(atr_rank), "bb_width_rank": round(bbw_rank),
         "autocorr_1": round(ac1, 3), "chg_5bar_pct": round(chg5, 2), "ema_alignment": "bull" if e20 > e50 > e200 else ("bear" if e20 < e50 < e200 else "mixed")}
    if atr_rank > 90 and chg5 < -8:
        r = "PANIC"
    elif atr_rank > 90 and chg5 > 8:
        r = "EUPHORIC"
    elif bbw_rank < 15:
        r = "SQUEEZE"
    elif bbw_rank > 85 and adx > 25 and abs(chg5) > 3:
        r = "BREAKOUT"
    elif adx >= 22 and slope > 0 and e20 > e50:
        r = "TREND_UP"
    elif adx >= 22 and slope < 0 and e20 < e50:
        r = "TREND_DOWN"
    elif atr_rank > 75:
        r = "HIGH_VOL"
    elif atr_rank < 20:
        r = "LOW_VOL"
    else:
        r = "RANGE"
    m["btc_regime"] = btc_regime or "UNKNOWN"
    return r, m


def _market_regime(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    df = ctx.frames.get("4h")
    if df is None or len(df) < 60:
        raise ValueError("4h verisi yok")
    btc = None
    if ctx.btc_frames and ctx.btc_frames.get("4h") is not None and len(ctx.btc_frames["4h"]) > 60:
        btc, _ = classify_regime(ctx.btc_frames["4h"])
    regime, m = classify_regime(df, btc)
    rep.metrics = {"regime": regime, **m}
    rep.timeframes = ["4h"]
    rep.bias = {"TREND_UP": 0.4, "TREND_DOWN": -0.4, "BREAKOUT": 0.2 * (1 if m["chg_5bar_pct"] > 0 else -1), "PANIC": -0.2, "EUPHORIC": 0.1}.get(regime, 0.0)
    rep.confidence_raw = 55 + min(30, abs(m["adx"] - 20))
    rep.evidence_for = [f"Rejim {regime}: ADX {m['adx']}, EMA50 eğim %{m['ema50_slope_pct']}, ATR yüzdelik {m['atr_pct_rank']}, BB genişlik yüzdelik {m['bb_width_rank']}"]
    if regime in ("PANIC", "EUPHORIC"):
        rep.warnings.append(f"Aşırı rejim ({regime}): boyut yarım, kaldıraç ≤2x")
    if regime == "SQUEEZE":
        rep.warnings.append("Sıkışma: yön teyidi olmadan pozisyon açma")
    if regime == "ILLIQUID":
        rep.warnings.append("Likidite düşük/gürültülü")


MarketRegimeAgent = _run("market_regime", "risk", _market_regime)


# ---------------------------------------------------------------- MULTI_TIMEFRAME
def _mtf(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    votes, tfs = [], []
    for tf, w in (("1d", 0.5), ("4h", 0.3), ("1h", 0.2)):
        df = ctx.frames.get(tf)
        if df is None or len(df) < 60:
            continue
        c, e20, e50, e200 = _last(df, "close"), _last(df, "ema20"), _last(df, "ema50"), _last(df, "ema200")
        v = (0.4 if c > e200 else -0.4) + (0.3 if e20 > e50 else -0.3) + (0.3 if c > e20 else -0.3)
        votes.append((tf, v, w))
        tfs.append(tf)
    if not votes:
        raise ValueError("çerçeve yok")
    rep.timeframes = tfs
    total = sum(v * w for _, v, w in votes) / sum(w for _, _, w in votes)
    signs = {(v > 0) for _, v, _ in votes}
    aligned = len(signs) == 1
    rep.bias = total
    rep.confidence_raw = 45 + (35 if aligned else 0) + 20 * min(1.0, abs(total))
    rep.metrics = {"alignment": "aligned" if aligned else "mixed", **{f"vote_{tf}": round(v, 2) for tf, v, _ in votes}}
    rep.evidence_for = [f"{tf}: {'yukarı' if v > 0 else 'aşağı'} ({v:+.2f})" for tf, v, _ in votes]
    if not aligned:
        rep.warnings.append("Zaman dilimleri uyumsuz — karşı-trend riski, boyut yarım")


MultiTimeframeAgent = _run("multi_timeframe", "trend", _mtf)


# ---------------------------------------------------------------- DERIVATIVES
def _derivatives(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    lv = ctx.live or {}
    f = lv.get("funding") or {}
    if f.get("rate") is None and not lv.get("open_interest"):
        rep.error = "türev verisi yok"
        return
    rep.data_sources = ["binance_usdm"]
    b = 0.0
    m: dict[str, Any] = {}
    if f.get("rate") is not None:
        fr = float(f["rate"]) * 100
        m["funding_pct"] = round(fr, 4)
        m["funding_annual_pct"] = round(fr * 3 * 365, 1)
        hist = lv.get("funding_history") or []
        if len(hist) >= 10:
            arr = np.array([float(h.get("rate", h) if isinstance(h, dict) else h) for h in hist]) * 100
            z = (fr - arr.mean()) / (arr.std() + 1e-9)
            m["funding_z"] = round(float(z), 2)
            if z > 2:
                b -= 0.4; rep.warnings.append(f"Funding z-skoru {z:.1f}: long kalabalık, squeeze/taşıma riski")
            elif z < -2:
                b += 0.4; rep.warnings.append(f"Funding z-skoru {z:.1f}: short kalabalık, short squeeze riski")
        if fr > 0.03:
            b -= 0.3; rep.warnings.append(f"Funding aşırı pozitif %{fr:.3f}/8s")
        elif fr < -0.02:
            b += 0.3; rep.warnings.append(f"Funding aşırı negatif %{fr:.3f}/8s")
        if f.get("mark") and (lv.get("ticker") or {}).get("last"):
            basis = (float(f["mark"]) / float(lv["ticker"]["last"]) - 1) * 100
            m["basis_pct"] = round(basis, 4)
        if f.get("next"):
            m["next_funding"] = f["next"]
    oi = lv.get("open_interest") or {}
    if oi.get("amount"):
        m["open_interest"] = float(oi["amount"])
        hist = lv.get("oi_history") or []
        if len(hist) >= 2:
            prev = float(hist[0].get("oi", hist[0]) if isinstance(hist[0], dict) else hist[0])
            m["oi_change_pct"] = round((float(oi["amount"]) / prev - 1) * 100, 2) if prev else 0.0
            px_chg = float((lv.get("ticker") or {}).get("percentage") or 0)
            if m["oi_change_pct"] > 5 and px_chg > 0:
                b += 0.15; rep.evidence_for.append("OI ↑ + fiyat ↑: yeni long'lar (trend teyidi)")
            elif m["oi_change_pct"] > 5 and px_chg < 0:
                b -= 0.15; rep.evidence_against.append("OI ↑ + fiyat ↓: yeni short'lar")
    ls = lv.get("long_short") or {}
    if ls.get("ratio"):
        r = float(ls["ratio"]); m["long_short_ratio"] = round(r, 2)
        if r > 2.5:
            b -= 0.2; rep.warnings.append(f"Kitle çok long ({r:.1f}) — kontraryen risk")
        elif r < 0.7:
            b += 0.2
    tk = lv.get("taker_ratio")
    if tk:
        m["taker_buy_sell_ratio"] = round(float(tk), 3)
    rep.metrics = m
    rep.bias = b
    rep.confidence_raw = 50 + 10 * min(3, len(m))
    if not rep.evidence_for:
        rep.evidence_for = [f"funding %{m.get('funding_pct', 0):.4f}, OI {m.get('open_interest', '-')}, LSR {m.get('long_short_ratio', '-')}"]


DerivativesAgent = _run("derivatives", "derivatives", _derivatives)


# ---------------------------------------------------------------- CORRELATION_BETA
def _corr_beta(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    df = ctx.frames.get("4h")
    if df is None or len(df) < 100:
        raise ValueError("4h verisi yok")
    r = np.log(df["close"]).diff().dropna()
    m: dict[str, Any] = {}
    for name, fr in (("btc", ctx.btc_frames), ("eth", ctx.eth_frames)):
        if not fr or fr.get("4h") is None:
            continue
        rb = np.log(fr["4h"]["close"]).diff().dropna()
        j = pd.concat([r, rb], axis=1, join="inner").dropna().iloc[-120:]
        if len(j) < 40:
            continue
        x, y = j.iloc[:, 1].to_numpy(), j.iloc[:, 0].to_numpy()
        corr = float(np.corrcoef(x, y)[0, 1])
        beta = float(np.cov(x, y)[0, 1] / (np.var(x) + 1e-12))
        m[f"corr_{name}_120b"] = round(corr, 3)
        m[f"beta_{name}_120b"] = round(beta, 3)
    if not m:
        rep.error = "BTC/ETH referans çerçevesi yok"
        return
    rep.metrics = m
    rep.timeframes = ["4h"]
    c = m.get("corr_btc_120b", 0.0)
    rep.bias = 0.0
    rep.confidence_raw = 60
    rep.evidence_for = [f"BTC korelasyonu {c:+.2f}, beta {m.get('beta_btc_120b', 0):+.2f} (120×4h)"]
    if abs(c) > 0.8:
        rep.warnings.append("BTC ile yüksek korelasyon: bu pozisyon fiilen BTC yönü riski taşır (küme riski)")


CorrelationBetaAgent = _run("correlation_beta", "correlation", _corr_beta)


# ---------------------------------------------------------------- ORDERBOOK_LIQUIDITY
def _orderbook(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    ob = (ctx.live or {}).get("orderbook") or {}
    if not ob:
        rep.error = "emir defteri yok"
        return
    spread = float(ob.get("spread_pct", 0.0) or 0.0)
    bid_v, ask_v = float(ob.get("bid_usdt", 0) or 0), float(ob.get("ask_usdt", 0) or 0)
    imb = float(ob.get("imbalance", 0.5) or 0.5)
    depth = bid_v + ask_v
    tn = ctx.target_notional or 0.0
    impact = (tn / max(depth / 2, 1e-9)) * spread if depth else None
    rep.metrics = {"spread_pct": round(spread, 4), "depth_top20_usdt": round(depth), "imbalance": round(imb, 3),
                   "impact_est_pct": round(impact, 5) if impact is not None else None, "depth_0_5pct_usdt": ob.get("depth_0_5pct_usdt"),
                   "depth_1pct_usdt": ob.get("depth_1pct_usdt")}
    rep.bias = max(-1.0, min(1.0, (imb - 0.5) * 2))
    rep.confidence_raw = 40
    rep.evidence_for = [f"Spread %{spread:.4f}, ilk 20 kademe derinlik {depth/1e3:,.0f}K USDT, alış payı %{imb*100:.0f}"]
    rep.warnings.append("Tek anlık emir defteri görüntüsü — spoofing olabilir; ağırlık düşük")
    if spread > 0.3:
        rep.veto, rep.veto_reason = True, f"WIDE_SPREAD %{spread:.3f}"
        rep.warnings.append(rep.veto_reason)
    elif depth and depth < 50_000:
        rep.warnings.append("Derinlik düşük (<50K USDT ilk 20 kademe): kayma riski")


OrderbookLiquidityAgent = _run("orderbook_liquidity", "liquidity", _orderbook)


# ---------------------------------------------------------------- RISK_SIZING
def _risk_sizing(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    from ..risk.engine import size_position
    df = ctx.frames.get("4h")
    stop_pct = ctx.stop_pct
    if stop_pct is None and df is not None and len(df) > 20:
        stop_pct = _last(df, "atr_pct") * 2.5
    if not stop_pct:
        raise ValueError("stop mesafesi bilinmiyor")
    entry = 100.0
    f = ctx.filters or {}
    res = size_position(equity=ctx.equity_usdt, risk_pct=ctx.risk_pct, entry=entry, stop=entry * (1 - stop_pct / 100),
                        min_notional=float(f.get("min_notional", 5.0)), max_leverage=int(f.get("max_leverage", ctx.max_leverage)),
                        max_position_pct=30.0, liq_buffer_mult=3.0)
    fee_rt = ctx.fee_taker_pct * 2
    rep.metrics = {**res.to_dict(), "stop_pct": round(stop_pct, 3), "fee_roundtrip_pct": fee_rt,
                   "fee_burden_r": round(fee_rt / stop_pct, 3) if stop_pct else None}
    rep.bias = 0.0
    rep.confidence_raw = 80
    if not res.ok:
        rep.veto, rep.veto_reason = True, res.reason
        rep.evidence_against = [f"Boyutlandırma: {res.reason} (güvenli notional {res.notional} < min)"]
    else:
        rep.evidence_for = [f"Risk {ctx.risk_pct}% → {res.risk_usdt} USDT; notional {res.notional} USDT, kaldıraç {res.leverage}x, marj {res.margin}"]
    if stop_pct and fee_rt / stop_pct > 0.25:
        rep.warnings.append(f"Komisyon yükü stop mesafesinin %{fee_rt/stop_pct*100:.0f}'i — edge'i yiyor")


RiskSizingAgent = _run("risk_sizing", "risk", _risk_sizing)


# ---------------------------------------------------------------- NEWS_CATALYST (stub)
def _news(ctx: SpecialistContext, rep: SpecialistReport) -> None:
    src = (ctx.live or {}).get("news_source")
    rep.bias, rep.confidence_raw = 0.0, 0.0
    if not src:
        rep.evidence_for = ["Haber kaynağı yapılandırılmamış — katalizör değerlendirilmedi (uydurulmadı)"]
        rep.metrics = {"configured": False}
    else:
        rep.metrics = {"configured": True, "items": 0}
        rep.evidence_for = ["Kaynak yapılandırılmış fakat bu sürümde çekim yapılmadı"]


NewsCatalystAgent = _run("news_catalyst", "catalyst", _news)


# ---------------------------------------------------------------- legacy adaptörü
def adapt_legacy_reports(reports: list[Any], ctx: SpecialistContext) -> list[SpecialistReport]:
    """`tradingbot.agents` raporları → SpecialistReport listesi (market ajanı likidite+türev olarak bölünür)."""
    out: list[SpecialistReport] = []
    for r in reports:
        groups = LEGACY_GROUP.get(r.agent, ("risk",))
        base = SpecialistReport.from_legacy(r, groups[0], ctx.run_id, ctx.snapshot_id, ctx.symbol, ctx.market_type, timeframes=["1d", "4h", "1h"])
        if r.agent == "market":
            out.extend(split_market_report(base))
        else:
            out.append(base)
    return out


NEW_SPECIALISTS: tuple[Callable[[SpecialistContext], SpecialistReport], ...] = (
    DataIntegrityAgent, MarketRegimeAgent, MultiTimeframeAgent, DerivativesAgent, CorrelationBetaAgent,
    OrderbookLiquidityAgent, RiskSizingAgent, NewsCatalystAgent,
)

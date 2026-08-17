"""Teknik uzman ajanlar: volatilite, trend/çizgiler, mum yapısı, hacim, seviyeler, momentum, edge."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from .base import Agent, AgentReport, CoinContext, fmt_px, pct

TF_W = {"1d": 0.5, "4h": 0.3, "1h": 0.2}
TF_TR = {"1d": "günlük", "4h": "4 saatlik", "1h": "saatlik"}


def _last(df: pd.DataFrame, col: str, k: int = 1) -> float:
    return float(df[col].iloc[-k])


def _percentile_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if len(s) < 20:
        return 50.0
    return float((s < value).mean() * 100.0)


# ---------------------------------------------------------------- 1) Volatilite
class VolatilityAgent(Agent):
    name, title, weight = "volatility", "Volatilite Ajanı", 0.0   # yön vermez, riski belirler

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        d1, h4 = ctx.frame("1d"), ctx.frame("4h")
        df = d1 if d1 is not None else h4
        if df is None:
            raise ValueError("veri yok")
        atr_pct = _last(df, "atr_pct")
        atr_rank = _percentile_rank(df["atr_pct"].iloc[-200:], atr_pct)
        lower, mid, upper = ind.bollinger(df["close"], 20, 2.0)
        bbw = float(((upper - lower) / mid).iloc[-1] * 100.0)
        bbw_rank = _percentile_rank(((upper - lower) / mid).iloc[-200:] * 100.0, bbw)
        rv = float(np.log(df["close"]).diff().iloc[-30:].std() * np.sqrt(365) * 100.0)
        h4_atr = _last(h4, "atr_pct") if h4 is not None else atr_pct
        regime = "DÜŞÜK" if atr_rank < 30 else ("YÜKSEK" if atr_rank > 70 else "NORMAL")
        # stop mesafesi (günlük ATR × çarpan) → kaldıraç tavanı: stop hareketi likidasyon mesafesinin ≤ 1/3'ü olsun
        stop_pct = atr_pct * ctx.atr_stop_mult
        lev_cap = max(1, min(5, int(33.0 / max(stop_pct, 1e-6))))
        if regime == "YÜKSEK":
            lev_cap = min(lev_cap, 2)
        rep.metrics.update(dict(atr_pct_1d=round(atr_pct, 2), atr_pct_4h=round(h4_atr, 2), atr_rank=round(atr_rank),
                                bb_width_pct=round(bbw, 2), bb_width_rank=round(bbw_rank), realized_vol_30d=round(rv, 1),
                                regime=regime, stop_pct=round(stop_pct, 2), max_leverage=lev_cap))
        rep.levels["stop_distance_pct"] = round(stop_pct, 2)
        rep.confidence = 70
        rep.summary = f"Volatilite {regime}: günlük ATR %{atr_pct:.2f} (yüzdelik {atr_rank:.0f}), 30g gerçekleşen vol %{rv:.0f}. Önerilen max kaldıraç {lev_cap}x, stop mesafesi ≈ %{stop_pct:.1f}."
        rep.findings += [
            f"Günlük ATR %{atr_pct:.2f} → son 200 günün %{atr_rank:.0f}'inden yüksek",
            f"Bollinger bant genişliği %{bbw:.1f} (yüzdelik {bbw_rank:.0f}) → " + ("sıkışma, patlama yakın olabilir" if bbw_rank < 20 else ("genişlemiş, hareket başlamış" if bbw_rank > 80 else "normal")),
            f"4h ATR %{h4_atr:.2f}",
        ]
        if regime == "YÜKSEK":
            rep.warnings.append("Volatilite yüksek: kaldıracı 2x ile sınırla, boyutu yarıya indir")
        if bbw_rank < 15:
            rep.warnings.append("Bollinger sıkışması: yön belli olmadan (kırılım kapanışı) pozisyon açma")


# ---------------------------------------------------------------- 2) Trend / çizgiler
class TrendAgent(Agent):
    name, title, weight = "trend", "Trend & EMA Çizgileri Ajanı", 0.25

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        total, wsum = 0.0, 0.0
        price = ctx.price
        for tf, w in TF_W.items():
            df = ctx.frame(tf)
            if df is None:
                continue
            c = _last(df, "close")
            e20, e50, e200 = _last(df, "ema20"), _last(df, "ema50"), _last(df, "ema200")
            e100 = float(ind.ema(df["close"], 100).iloc[-1])
            adx = _last(df, "adx14")
            slope50 = pct(e50, float(df["ema50"].iloc[-11])) if len(df) > 11 else 0.0
            b = 0.0
            b += 0.35 if c > e200 else -0.35
            b += 0.25 if e20 > e50 else -0.25
            b += 0.2 if e50 > e200 else -0.2
            b += 0.2 if slope50 > 0 else -0.2
            if adx < 18:
                b *= 0.5
            total += b * w
            wsum += w
            align = "boğa dizilimi (20>50>200)" if e20 > e50 > e200 else ("ayı dizilimi (20<50<200)" if e20 < e50 < e200 else "karışık dizilim")
            rep.findings.append(f"{TF_TR[tf].capitalize()}: fiyat EMA200'ün {'üstünde' if c > e200 else 'altında'}, {align}, EMA50 eğimi %{slope50:+.2f}/10bar, ADX {adx:.0f}")
            if tf == "1d":
                rep.levels.update({"ema20_1d": e20, "ema50_1d": e50, "ema100_1d": e100, "ema200_1d": e200})
                rep.metrics.update({"adx_1d": round(adx), "ema50_slope_1d": round(slope50, 2)})
            if tf == "4h":
                rep.levels.update({"ema20_4h": e20, "ema50_4h": e50, "ema200_4h": e200})
        rep.bias = total / wsum if wsum else 0.0
        rep.confidence = int(40 + 50 * min(1.0, abs(rep.bias)))
        # TradingView "Key facts" tarzı özet
        lv = rep.levels
        if price and "ema20_1d" in lv:
            near = min(("20g EMA", lv["ema20_1d"]), ("50g EMA", lv["ema50_1d"]), ("100g EMA", lv["ema100_1d"]), ("200g EMA", lv["ema200_1d"]), key=lambda t: abs(t[1] - price))
            sup = [(k, v) for k, v in (("20g EMA", lv["ema20_1d"]), ("50g EMA", lv["ema50_1d"]), ("100g EMA", lv["ema100_1d"]), ("200g EMA", lv["ema200_1d"])) if v < price]
            res = [(k, v) for k, v in (("20g EMA", lv["ema20_1d"]), ("50g EMA", lv["ema50_1d"]), ("100g EMA", lv["ema100_1d"]), ("200g EMA", lv["ema200_1d"])) if v > price]
            s = max(sup, key=lambda t: t[1]) if sup else None
            r = min(res, key=lambda t: t[1]) if res else None
            rep.summary = f"Fiyat {near[0]} (~{fmt_px(near[1])}) yakınında" + (f"; yakın destek {s[0]} ~{fmt_px(s[1])}" if s else "") + (f"; yakın direnç {r[0]} ~{fmt_px(r[1])}" if r else "") + f". Çoklu zaman dilimi eğilimi: {rep.stance}."
            if s:
                rep.levels["ema_support"] = s[1]
            if r:
                rep.levels["ema_resistance"] = r[1]
        else:
            rep.summary = f"Çoklu zaman dilimi eğilimi: {rep.stance}."
        d1 = ctx.frame("1d")
        h4 = ctx.frame("4h")
        if d1 is not None and h4 is not None:
            if _last(d1, "close") < _last(d1, "ema200") and _last(h4, "ema20") > _last(h4, "ema50"):
                rep.warnings.append("Günlük trend aşağı iken 4h yükseliş = karşı-trend; long boyutunu yarıya indir, hızlı kâr al")
            if _last(d1, "close") > _last(d1, "ema200") and _last(h4, "ema20") < _last(h4, "ema50"):
                rep.warnings.append("Günlük trend yukarı iken 4h düşüş = geri çekilme; short kovalamak yerine destekten long ara")


# ---------------------------------------------------------------- 3) Mum yapısı
class CandleAgent(Agent):
    name, title, weight = "candles", "Mum Yapısı Ajanı", 0.12

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        total, wsum = 0.0, 0.0
        for tf, w in (("1d", 0.5), ("4h", 0.5)):
            df = ctx.frame(tf)
            if df is None:
                continue
            o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            rng = max(h[-1] - l[-1], 1e-12)
            clv = (c[-1] - l[-1]) / rng                       # 0 = dipte kapandı, 1 = tepede kapandı
            body = abs(c[-1] - o[-1]) / rng
            up_wick = (h[-1] - max(o[-1], c[-1])) / rng
            dn_wick = (min(o[-1], c[-1]) - l[-1]) / rng
            b = (clv - 0.5) * 1.2
            pats = []
            if body < 0.1:
                pats.append("doji (kararsızlık)")
                b *= 0.3
            if dn_wick > 0.55 and body < 0.35 and c[-1] >= o[-1]:
                pats.append("çekiç/pin bar (alıcı reddi)"); b += 0.35
            if up_wick > 0.55 and body < 0.35 and c[-1] <= o[-1]:
                pats.append("kayan yıldız (satıcı reddi)"); b -= 0.35
            if c[-1] > o[-1] and c[-2] < o[-2] and c[-1] > o[-2] and o[-1] < c[-2]:
                pats.append("boğa yutan"); b += 0.4
            if c[-1] < o[-1] and c[-2] > o[-2] and c[-1] < o[-2] and o[-1] > c[-2]:
                pats.append("ayı yutan"); b -= 0.4
            if h[-1] < h[-2] and l[-1] > l[-2]:
                pats.append("iç bar (sıkışma)")
            ups = int((c[-5:] > o[-5:]).sum())
            # yapı: son 40 barda swing yüksek/alçak
            hh = h[-20:].max() > h[-40:-20].max()
            hl = l[-20:].min() > l[-40:-20].min()
            struct = "HH+HL (yükselen yapı)" if hh and hl else ("LH+LL (alçalan yapı)" if (not hh and not hl) else "karışık yapı")
            b += 0.3 if (hh and hl) else (-0.3 if (not hh and not hl) else 0.0)
            total += b * w
            wsum += w
            rep.findings.append(f"{TF_TR[tf].capitalize()} son mum: aralığın %{clv*100:.0f}'inde kapandı, gövde %{body*100:.0f}, üst fitil %{up_wick*100:.0f}, alt fitil %{dn_wick*100:.0f}" + (f" — {', '.join(pats)}" if pats else "") + f"; son 5 mumun {ups}'i yeşil; {struct}")
            rep.metrics[f"clv_{tf}"] = round(clv, 2)
            rep.metrics[f"structure_{tf}"] = struct
            if tf == "4h" and clv > 0.8 and ups >= 4:
                rep.warnings.append("4h'de üst üste güçlü yeşil mumlar: tepeden kovalama, geri çekilme bekle")
            if tf == "4h" and clv < 0.2 and ups <= 1:
                rep.warnings.append("4h'de üst üste güçlü kırmızı mumlar: dipten short kovalama, tepki bekle")
        rep.bias = total / wsum if wsum else 0.0
        rep.confidence = int(35 + 45 * min(1.0, abs(rep.bias)))
        rep.summary = f"Mum yapısı {rep.stance_lower}: " + (rep.findings[0].split(": ", 1)[1][:120] if rep.findings else "-")


# ---------------------------------------------------------------- 4) Hacim
class VolumeAgent(Agent):
    name, title, weight = "volume", "Hacim Ajanı", 0.10

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        total, wsum = 0.0, 0.0
        for tf, w in (("1d", 0.5), ("4h", 0.5)):
            df = ctx.frame(tf)
            if df is None:
                continue
            v = df["volume"].to_numpy(float)
            c = df["close"].to_numpy(float)
            avg20 = v[-21:-1].mean() if len(v) > 21 else v.mean()
            ratio = v[-1] / avg20 if avg20 else 1.0
            up_v = v[-20:][np.diff(c[-21:]) > 0].sum()
            dn_v = v[-20:][np.diff(c[-21:]) <= 0].sum()
            uv_ratio = up_v / dn_v if dn_v else 2.0
            obv = np.cumsum(np.sign(np.diff(c)) * v[1:])
            obv_slope = (obv[-1] - obv[-21]) / (abs(obv[-21]) + 1e-9) if len(obv) > 21 else 0.0
            price_up = c[-1] > c[-2]
            confirm = (price_up and ratio > 1.0) or ((not price_up) and ratio > 1.0)
            b = 0.0
            b += 0.4 if uv_ratio > 1.2 else (-0.4 if uv_ratio < 0.8 else 0.0)
            b += 0.3 if obv_slope > 0.02 else (-0.3 if obv_slope < -0.02 else 0.0)
            b += (0.3 if price_up else -0.3) if ratio > 1.3 else 0.0
            total += b * w
            wsum += w
            rep.findings.append(f"{TF_TR[tf].capitalize()}: son bar hacmi 20-bar ortalamasının {ratio:.2f} katı ({'yüksek' if ratio > 1.3 else 'düşük' if ratio < 0.7 else 'normal'}); alım/satım hacmi oranı {uv_ratio:.2f}; OBV 20-bar eğimi {obv_slope*100:+.1f}%; hareket hacimle {'onaylı' if confirm else 'onaysız'}")
            rep.metrics[f"vol_ratio_{tf}"] = round(ratio, 2)
            rep.metrics[f"updown_vol_{tf}"] = round(uv_ratio, 2)
            if tf == "4h" and ratio < 0.6:
                rep.warnings.append("4h hacim zayıf: kırılımlara güvenme (sahte kırılım riski)")
        rep.bias = total / wsum if wsum else 0.0
        rep.confidence = int(35 + 45 * min(1.0, abs(rep.bias)))
        rep.summary = f"Hacim {rep.stance_lower}: " + ("alıcı hacmi baskın" if rep.bias > 0.15 else "satıcı hacmi baskın" if rep.bias < -0.15 else "dengeli")


# ---------------------------------------------------------------- 5) Seviyeler (destek/direnç)
def _swings(h: np.ndarray, l: np.ndarray, k: int = 3) -> tuple[list[float], list[float]]:
    highs, lows = [], []
    for i in range(k, len(h) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append(float(h[i]))
        if l[i] == l[i - k:i + k + 1].min():
            lows.append(float(l[i]))
    return highs, lows


def _cluster(vals: list[float], tol: float) -> list[float]:
    """Birbirine %tol içinde olan seviyeleri tek seviyede (dokunuş ağırlıklı ortalama) birleştirir."""
    out: list[float] = []
    group: list[float] = []
    for v in vals:
        if group and (v / group[0] - 1.0) > tol:
            out.append(sum(group) / len(group))
            group = []
        group.append(v)
    if group:
        out.append(sum(group) / len(group))
    return out


class LevelsAgent(Agent):
    name, title, weight = "levels", "Destek/Direnç Ajanı", 0.13

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        price = ctx.price
        d1, h4 = ctx.frame("1d"), ctx.frame("4h")
        highs, lows = [], []
        for df, n in ((d1, 120), (h4, 200)):
            if df is None:
                continue
            hh, ll = _swings(df["high"].to_numpy(float)[-n:], df["low"].to_numpy(float)[-n:])
            highs += hh
            lows += ll
        if not highs and not lows:
            raise ValueError("swing bulunamadı")
        atr_pct = float((h4 if h4 is not None else d1)["atr_pct"].iloc[-1])
        tol = max(0.4, 0.6 * atr_pct) / 100.0          # aynı seviye sayılacak yakınlık
        clustered = _cluster(sorted(highs + lows), tol)
        res = [x for x in clustered if x > price * (1 + tol / 2)]
        sup = [x for x in clustered if x < price * (1 - tol / 2)][::-1]
        r1 = res[0] if res else None
        r2 = res[1] if len(res) > 1 else None
        s1 = sup[0] if sup else None
        s2 = sup[1] if len(sup) > 1 else None
        rng_hi = max(highs) if highs else price
        rng_lo = min(lows) if lows else price
        pos = (price - rng_lo) / (rng_hi - rng_lo) if rng_hi > rng_lo else 0.5
        # Donchian
        if h4 is not None:
            rep.levels["donchian20_high_4h"] = float(h4["high"].iloc[-21:-1].max())
            rep.levels["donchian20_low_4h"] = float(h4["low"].iloc[-21:-1].min())
        if d1 is not None:
            rep.levels["high_20d"] = float(d1["high"].iloc[-21:-1].max())
            rep.levels["low_20d"] = float(d1["low"].iloc[-21:-1].min())
        for k, v in (("r1", r1), ("r2", r2), ("s1", s1), ("s2", s2)):
            if v is not None:
                rep.levels[k] = v
        rep.metrics["resistances"] = [round(x, 8) for x in res[:6]]
        rep.metrics["supports"] = [round(x, 8) for x in sup[:6]]
        rep.metrics.update(dict(range_position=round(pos, 2), range_high=rng_hi, range_low=rng_lo))
        d_r = pct(r1, price) if r1 else None
        d_s = pct(price, s1) if s1 else None
        rep.bias = 0.0
        if d_s is not None and d_s < 1.5:
            rep.bias += 0.3          # desteğe yakın: long için risk/ödül iyi
        if d_r is not None and d_r < 1.5:
            rep.bias -= 0.3          # dirence yakın: long kovalama
        rep.bias += (0.5 - pos) * 0.4
        rep.confidence = 55
        rep.summary = (f"Yakın direnç {fmt_px(r1)} (+{d_r:.1f}%)" if r1 else "Üstte belirgin direnç yok") + \
                      (f"; yakın destek {fmt_px(s1)} (−{d_s:.1f}%)" if s1 else "; altta belirgin destek yok") + \
                      f". Fiyat son aralığın %{pos*100:.0f}'inde."
        if r1:
            rep.findings.append(f"{fmt_px(r1)} üstünde 4h kapanış → hedef {fmt_px(r2) if r2 else 'aralık üstü ' + fmt_px(rng_hi)}")
        if s1:
            rep.findings.append(f"{fmt_px(s1)} altında 4h kapanış → risk {fmt_px(s2) if s2 else 'aralık altı ' + fmt_px(rng_lo)}")
        rep.findings.append(f"20 günlük en yüksek {fmt_px(rep.levels.get('high_20d', rng_hi))}, en düşük {fmt_px(rep.levels.get('low_20d', rng_lo))}")
        if d_r is not None and d_r < 1.0:
            rep.warnings.append(f"Direnç {fmt_px(r1)} hemen üstte: kırılım kapanışı görmeden long açma")
        if d_s is not None and d_s < 1.0:
            rep.warnings.append(f"Destek {fmt_px(s1)} hemen altta: kırılım kapanışı görmeden short açma")


# ---------------------------------------------------------------- 6) Momentum
class MomentumAgent(Agent):
    name, title, weight = "momentum", "Momentum Ajanı", 0.15

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        total, wsum = 0.0, 0.0
        for tf, w in TF_W.items():
            df = ctx.frame(tf)
            if df is None:
                continue
            c = df["close"]
            rsi = _last(df, "rsi14")
            ema12, ema26 = ind.ema(c, 12), ind.ema(c, 26)
            macd = ema12 - ema26
            sig = ind.ema(macd, 9)
            hist = float((macd - sig).iloc[-1])
            hist_prev = float((macd - sig).iloc[-2])
            roc = pct(float(c.iloc[-1]), float(c.iloc[-11])) if len(c) > 11 else 0.0
            b = 0.0
            b += 0.35 if hist > 0 else -0.35
            b += 0.15 if hist > hist_prev else -0.15
            b += 0.25 if rsi > 55 else (-0.25 if rsi < 45 else 0.0)
            b += 0.25 if roc > 0 else -0.25
            # aşırılık: RSI>75 boğa momentumu ama kovalama; <25 tersi
            if rsi > 75:
                b -= 0.2
                rep.warnings.append(f"{TF_TR[tf].capitalize()} RSI {rsi:.0f} aşırı alım: yeni long açma, geri çekilme bekle")
            if rsi < 25:
                b += 0.2
                rep.warnings.append(f"{TF_TR[tf].capitalize()} RSI {rsi:.0f} aşırı satım: yeni short açma, tepki bekle")
            # basit uyumsuzluk: fiyat 30 barda yeni yüksek, RSI değil
            if len(df) > 35:
                r = df["rsi14"]
                if float(c.iloc[-1]) >= float(c.iloc[-30:].max()) * 0.999 and float(r.iloc[-1]) < float(r.iloc[-30:].max()) - 5:
                    b -= 0.2
                    rep.findings.append(f"{TF_TR[tf].capitalize()}: fiyat yeni yüksek, RSI değil → negatif uyumsuzluk")
                if float(c.iloc[-1]) <= float(c.iloc[-30:].min()) * 1.001 and float(r.iloc[-1]) > float(r.iloc[-30:].min()) + 5:
                    b += 0.2
                    rep.findings.append(f"{TF_TR[tf].capitalize()}: fiyat yeni düşük, RSI değil → pozitif uyumsuzluk")
            total += b * w
            wsum += w
            rep.findings.append(f"{TF_TR[tf].capitalize()}: RSI14 {rsi:.0f}, MACD hist {'+' if hist > 0 else '−'} ({'genişliyor' if abs(hist) > abs(hist_prev) else 'daralıyor'}), ROC10 %{roc:+.1f}")
            rep.metrics[f"rsi_{tf}"] = round(rsi)
            rep.metrics[f"macd_hist_{tf}"] = round(hist, 4)
        rep.bias = total / wsum if wsum else 0.0
        rep.confidence = int(40 + 50 * min(1.0, abs(rep.bias)))
        rep.summary = f"Momentum {rep.stance_lower} (RSI 1d/4h/1h: {rep.metrics.get('rsi_1d','-')}/{rep.metrics.get('rsi_4h','-')}/{rep.metrics.get('rsi_1h','-')})."


# ---------------------------------------------------------------- 7) Backtest edge (WFO)
class EdgeAgent(Agent):
    name, title, weight = "edge", "Backtest/Edge Ajanı", 0.20

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        a = ctx.analysis
        if a is None:
            rep.summary = "WFO analizi yok (önce `run`)."
            rep.confidence = 0
            return
        wfo = a.test_metrics or {}
        rep.metrics.update(dict(has_edge=a.has_edge, best=a.best_strategy, wfo_sharpe=wfo.get("sharpe"), wfo_pf=wfo.get("profit_factor"),
                                wfo_trades=wfo.get("trades"), strategy_position=a.strategy_position, signal=a.signal))
        b = 0.0
        if a.has_edge:
            if a.signal == "BUY":
                b = 0.9
            elif a.strategy_position == "LONG":
                b = 0.5
            elif a.signal == "SELL":
                b = -0.5
            rep.confidence = int(50 + min(40, max(0.0, float(wfo.get("sharpe", 0) or 0)) * 20))
        else:
            b = 0.0
            rep.confidence = 40
            rep.warnings.append("Walk-forward'da doğrulanmış edge yok: sistematik strateji sinyaline değil, sadece güçlü çoklu-ajan uyumuna işlem aç; boyutu küçük tut")
        rep.bias = b
        rep.summary = f"En iyi strateji {a.best_family_title or '-'} · {a.edge_note}"
        rep.findings.append(f"Strateji durumu: {a.strategy_position}, sinyal: {a.signal}, skor {a.score}")
        if a.strategy_stop:
            rep.levels["strategy_stop"] = float(a.strategy_stop)


TECHNICAL_AGENTS = [VolatilityAgent(), TrendAgent(), CandleAgent(), VolumeAgent(), LevelsAgent(), MomentumAgent(), EdgeAgent()]

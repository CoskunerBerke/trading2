"""Binance canlı piyasa verisi ajanı: 24s istatistik, emir defteri dengesi, funding, open interest, long/short oranı.

Tüm uç noktalar public'tir (API key gerekmez). Yalnızca OKUR.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .base import Agent, AgentReport, CoinContext, fmt_px, pct

log = logging.getLogger(__name__)


class BinanceLive:
    """ccxt üzerinden Binance spot + USDⓈ-M futures public verisi (önbellekli, hata toleranslı)."""

    def __init__(self, ttl_sec: int = 60):
        self._spot = None
        self._fut = None
        self.ttl = ttl_sec
        self._cache: dict[str, tuple[float, dict]] = {}

    def _init(self):
        import ccxt
        if self._spot is None:
            self._spot = ccxt.binance({"enableRateLimit": True})
        if self._fut is None:
            self._fut = ccxt.binanceusdm({"enableRateLimit": True})

    def snapshot(self, symbol: str) -> dict[str, Any]:
        now = time.time()
        hit = self._cache.get(symbol)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        out: dict[str, Any] = {"symbol": symbol, "ts": now, "errors": []}
        try:
            self._init()
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"ccxt yok: {exc}")
            return out
        fut_sym = f"{symbol}:USDT"
        src = self._spot
        try:
            t = self._spot.fetch_ticker(symbol)
        except Exception:  # noqa: BLE001  — spot'ta yoksa (yalnızca perpetual olan semboller) futures'tan
            src = self._fut
            try:
                t = self._fut.fetch_ticker(fut_sym)
            except Exception as exc:  # noqa: BLE001
                t = None
                out["errors"].append(f"ticker: {exc}")
        if t:
            out["ticker"] = {k: t.get(k) for k in ("last", "high", "low", "quoteVolume", "baseVolume", "percentage", "bid", "ask")}
        try:
            ob = src.fetch_order_book(symbol if src is self._spot else fut_sym, limit=20)
            bid_v = sum(b[1] * b[0] for b in ob["bids"][:20])
            ask_v = sum(a[1] * a[0] for a in ob["asks"][:20])
            out["orderbook"] = {"bid_usdt": bid_v, "ask_usdt": ask_v, "imbalance": bid_v / (bid_v + ask_v) if (bid_v + ask_v) else 0.5,
                                "spread_pct": pct(ob["asks"][0][0], ob["bids"][0][0]) if ob["asks"] and ob["bids"] else 0.0}
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"orderbook: {exc}")
        try:
            fr = self._fut.fetch_funding_rate(fut_sym)
            out["funding"] = {"rate": fr.get("fundingRate"), "mark": fr.get("markPrice"), "next": fr.get("fundingDatetime")}
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"funding: {exc}")
        try:
            oi = self._fut.fetch_open_interest(fut_sym)
            out["open_interest"] = {"amount": oi.get("openInterestAmount"), "value": oi.get("openInterestValue")}
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"oi: {exc}")
        try:
            lsr = self._fut.fapiDataGetGlobalLongShortAccountRatio({"symbol": symbol.replace("/", ""), "period": "1h", "limit": 1})
            if lsr:
                out["long_short"] = {"ratio": float(lsr[0]["longShortRatio"]), "long_pct": float(lsr[0]["longAccount"]) * 100}
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"lsr: {exc}")
        self._cache[symbol] = (now, out)
        return out


class MarketDataAgent(Agent):
    name, title, weight = "market", "Binance Canlı Piyasa Ajanı", 0.12

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        lv = ctx.live or {}
        if not lv or (not lv.get("ticker") and not lv.get("orderbook")):
            rep.summary = "Canlı veri alınamadı" + (f" ({'; '.join(lv.get('errors', []))[:120]})" if lv.get("errors") else "")
            rep.confidence = 0
            return
        b = 0.0
        t = lv.get("ticker") or {}
        if t.get("last") and t.get("high") and t.get("low"):
            last, hi, lo = float(t["last"]), float(t["high"]), float(t["low"])
            pos = (last - lo) / (hi - lo) if hi > lo else 0.5
            rep.metrics.update(dict(chg24_pct=round(float(t.get("percentage") or 0), 2), high24=hi, low24=lo, pos24=round(pos, 2),
                                    vol24_usdt=round(float(t.get("quoteVolume") or 0))))
            rep.levels.update({"high_24h": hi, "low_24h": lo})
            rep.findings.append(f"24s: %{float(t.get('percentage') or 0):+.2f}, aralık {fmt_px(lo)}–{fmt_px(hi)}, fiyat aralığın %{pos*100:.0f}'inde, hacim {float(t.get('quoteVolume') or 0)/1e6:,.0f}M USDT")
            b += (pos - 0.5) * 0.4
        ob = lv.get("orderbook") or {}
        if ob:
            imb = float(ob.get("imbalance", 0.5))
            rep.metrics.update(dict(ob_imbalance=round(imb, 2), spread_pct=round(float(ob.get("spread_pct", 0)), 4)))
            rep.findings.append(f"Emir defteri (ilk 20 kademe): alış {ob['bid_usdt']/1e3:,.0f}K / satış {ob['ask_usdt']/1e3:,.0f}K USDT → alış payı %{imb*100:.0f}")
            b += (imb - 0.5) * 1.2
        f = lv.get("funding") or {}
        if f.get("rate") is not None:
            fr = float(f["rate"]) * 100
            ann = fr * 3 * 365
            rep.metrics.update(dict(funding_pct=round(fr, 4), funding_annual_pct=round(ann, 1)))
            rep.findings.append(f"Funding %{fr:.4f} / 8s (yıllık ≈ %{ann:.0f}) → {'longlar ödüyor (long kalabalık)' if fr > 0.01 else 'shortlar ödüyor (short kalabalık)' if fr < -0.005 else 'nötr'}")
            if fr > 0.03:
                b -= 0.3
                rep.warnings.append(f"Funding aşırı pozitif (%{fr:.3f}): long kalabalık, kaldıraçlı long'da funding maliyeti + sıkışma riski")
            elif fr < -0.02:
                b += 0.3
                rep.warnings.append(f"Funding negatif (%{fr:.3f}): short kalabalık, short squeeze riski")
        oi = lv.get("open_interest") or {}
        if oi.get("amount"):
            rep.metrics["open_interest"] = float(oi["amount"])
            rep.findings.append(f"Açık pozisyon (OI): {float(oi['amount']):,.0f} {ctx.base}")
        ls = lv.get("long_short") or {}
        if ls:
            r = ls["ratio"]
            rep.metrics.update(dict(long_short_ratio=round(r, 2), long_pct=round(ls["long_pct"], 1)))
            rep.findings.append(f"Global long/short hesap oranı {r:.2f} (long %{ls['long_pct']:.0f})")
            if r > 2.5:
                b -= 0.2
                rep.warnings.append(f"Kitle çok long ({r:.1f}): kontraryen risk, long'da sıkı stop")
            elif r < 0.7:
                b += 0.2
        rep.bias = b
        rep.confidence = 50
        rep.summary = f"Canlı akış {rep.stance_lower}: emir defteri alış payı %{rep.metrics.get('ob_imbalance', 0.5)*100:.0f}, funding %{rep.metrics.get('funding_pct', 0):.4f}, 24s %{rep.metrics.get('chg24_pct', 0):+.2f}."

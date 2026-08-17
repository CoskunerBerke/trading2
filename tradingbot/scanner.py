"""PİYASA TARAYICI — tüm Binance USDT perpetual evrenini tarar, huni mantığıyla setup çıkarır.

  TARANDI (likidite filtresi: 24s hacim ≥ min)  →  İŞARETLENDİ (skor ≥ eşik)  →  SETUP (ilk N)

Her sembol için hızlı özellikler (Binance futures 4h + 1d mumları) ve 0-100 "AI skoru":
  Trend (25) · Momentum (25) · Hacim (25) · Tetikleyici (25: kırılım / sıkışma-patlama / hacim şoku / funding anomalisi)
  + Risk düzeltmesi (volatilite aşırıysa, R/R zayıfsa puan kesilir)
Long ve short için ayrı skor; yüksek olan setup yönüdür. Setup'lar sonra derin ajan analizine gider.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind

log = logging.getLogger(__name__)


@dataclass
class ScanRow:
    symbol: str                   # spot tarzı: BTC/USDT
    perp: str                     # BTC/USDT:USDT
    price: float = 0.0
    chg24_pct: float = 0.0
    vol24_usdt: float = 0.0
    funding_pct: float = 0.0
    trend: float = 0.0            # 0-25
    momentum: float = 0.0
    volume: float = 0.0
    catalyst: float = 0.0
    risk: float = 0.0             # 0-25 (yüksek = düşük risk)
    score_long: int = 0
    score_short: int = 0
    direction: str = "-"
    score: int = 0
    tags: list[str] = field(default_factory=list)
    atr_pct: float = 0.0
    rsi_4h: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    generated_at: str
    universe: int
    scanned: int
    flagged: int
    setups: list[ScanRow]
    rows: list[ScanRow]
    min_volume: float
    seconds: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["setups"] = [r.to_dict() for r in self.setups]
        d["rows"] = [r.to_dict() for r in self.rows]
        return d


class MarketScanner:
    def __init__(self, min_volume_usdt: float = 20e6, flag_score: int = 60, top_n: int = 12, exclude: tuple = ("USDC/", "FDUSD/", "BUSD/", "TUSD/", "USD1/", "EUR/", "XAU/", "XAG/"), max_symbols: int = 160):
        self.min_volume = min_volume_usdt
        self.flag_score = flag_score
        self.top_n = top_n
        self.exclude = exclude
        self.max_symbols = max_symbols
        self._fu = None

    def _ex(self):
        if self._fu is None:
            import ccxt
            self._fu = ccxt.binanceusdm({"enableRateLimit": True})
            self._fu.load_markets()
        return self._fu

    # ---------------------------------------------------------------- evren
    def universe(self) -> list[tuple[str, dict]]:
        ex = self._ex()
        tk = ex.fetch_tickers()
        rows = []
        for perp, m in ex.markets.items():
            if not (m.get("swap") and m.get("quote") == "USDT" and m.get("active")):
                continue
            if any(perp.startswith(x) for x in self.exclude):
                continue
            t = tk.get(perp)
            if not t or (t.get("quoteVolume") or 0) < self.min_volume:
                continue
            rows.append((perp, t))
        rows.sort(key=lambda r: -(r[1].get("quoteVolume") or 0))
        return rows[: self.max_symbols]

    # ---------------------------------------------------------------- özellikler
    def _features(self, perp: str, t: dict) -> ScanRow:
        ex = self._ex()
        row = ScanRow(symbol=perp.split(":")[0], perp=perp, price=float(t.get("last") or 0), chg24_pct=float(t.get("percentage") or 0),
                      vol24_usdt=float(t.get("quoteVolume") or 0))
        h4 = pd.DataFrame(ex.fetch_ohlcv(perp, "4h", limit=220), columns=["timestamp", "open", "high", "low", "close", "volume"])
        d1 = pd.DataFrame(ex.fetch_ohlcv(perp, "1d", limit=120), columns=["timestamp", "open", "high", "low", "close", "volume"])
        if len(h4) < 60 or len(d1) < 30:
            row.error = "yetersiz veri"
            return row
        h4 = h4.iloc[:-1]  # açık barı at
        d1 = d1.iloc[:-1]
        c4, c1 = h4["close"], d1["close"]
        e20, e50, e200 = ind.ema(c4, 20), ind.ema(c4, 50), ind.ema(c4, 200)
        d_e20, d_e50 = ind.ema(c1, 20), ind.ema(c1, 50)
        rsi4 = float(ind.rsi(c4, 14).iloc[-1])
        rsi1 = float(ind.rsi(c1, 14).iloc[-1])
        atr4 = ind.atr(h4["high"], h4["low"], h4["close"], 14)
        atr_pct = float(atr4.iloc[-1] / c4.iloc[-1] * 100)
        adx4 = float(ind.adx(h4["high"], h4["low"], h4["close"], 14).iloc[-1])
        last = float(c4.iloc[-1])
        row.atr_pct, row.rsi_4h = round(atr_pct, 2), round(rsi4, 1)

        # --- trend (yönlü, -1..1) ----
        tr = 0.0
        tr += 0.3 if last > float(e200.iloc[-1]) else -0.3
        tr += 0.25 if float(e20.iloc[-1]) > float(e50.iloc[-1]) else -0.25
        tr += 0.25 if float(c1.iloc[-1]) > float(d_e20.iloc[-1]) else -0.25
        tr += 0.2 if float(d_e20.iloc[-1]) > float(d_e50.iloc[-1]) else -0.2
        adx_f = min(1.0, max(0.3, adx4 / 30))
        tr *= adx_f
        # --- momentum (yönlü) ----
        roc = float(last / c4.iloc[-13] - 1) * 100
        mo = 0.0
        mo += 0.4 * max(-1, min(1, (rsi4 - 50) / 20))
        mo += 0.3 * max(-1, min(1, roc / (atr_pct * 3 + 1e-9)))
        mo += 0.3 * max(-1, min(1, (rsi1 - 50) / 20))
        # --- hacim (0..1) ----
        v = h4["volume"].to_numpy(float)
        vr = v[-1] / (v[-21:-1].mean() + 1e-9)
        vr6 = v[-6:].mean() / (v[-30:-6].mean() + 1e-9)
        up_v = v[-20:][np.diff(c4.to_numpy(float)[-21:]) > 0].sum()
        dn_v = v[-20:][np.diff(c4.to_numpy(float)[-21:]) <= 0].sum()
        vol_dir = 1.0 if up_v > dn_v * 1.15 else (-1.0 if dn_v > up_v * 1.15 else 0.0)
        vol_score = min(1.0, 0.5 * min(vr, 3) / 3 * 2 + 0.5 * min(vr6, 2) / 2)
        # --- tetikleyici ----
        tags = []
        hi20 = float(h4["high"].iloc[-21:-1].max()); lo20 = float(h4["low"].iloc[-21:-1].min())
        cat_long = cat_short = 0.0
        if last > hi20:
            cat_long += 0.6; tags.append("20-bar kırılım ↑")
        if last < lo20:
            cat_short += 0.6; tags.append("20-bar kırılım ↓")
        lower, mid, upper = ind.bollinger(c4, 20, 2.0)
        bbw = ((upper - lower) / mid)
        if float(bbw.iloc[-2]) <= float(bbw.iloc[-120:].quantile(0.15)) and float(bbw.iloc[-1]) > float(bbw.iloc[-2]) * 1.15:
            tags.append("sıkışma→patlama")
            if last > float(mid.iloc[-1]):
                cat_long += 0.4
            else:
                cat_short += 0.4
        if vr > 2.5:
            tags.append("hacim şoku")
            if float(c4.iloc[-1]) > float(c4.iloc[-2]):
                cat_long += 0.3
            else:
                cat_short += 0.3
        try:
            fr = ex.fetch_funding_rate(perp)
            row.funding_pct = round(float(fr.get("fundingRate") or 0) * 100, 4)
            if row.funding_pct > 0.05:
                tags.append("funding aşırı +"); cat_short += 0.2
            elif row.funding_pct < -0.03:
                tags.append("funding aşırı −"); cat_long += 0.2
        except Exception:  # noqa: BLE001
            pass
        if abs(row.chg24_pct) > 12:
            tags.append(f"24s %{row.chg24_pct:+.0f}")
        # --- risk (0..1, yüksek = iyi) ----
        risk = 1.0
        if atr_pct > 6:
            risk -= 0.4
        elif atr_pct > 4:
            risk -= 0.2
        if rsi4 > 78 or rsi4 < 22:
            risk -= 0.3
        if abs(row.chg24_pct) > 20:
            risk -= 0.3
        risk = max(0.0, risk)
        # --- skorlar ----
        def total(direction: int, cat: float) -> int:
            t_ = 25 * max(0.0, tr * direction)
            m_ = 25 * max(0.0, mo * direction)
            v_ = 25 * vol_score * (1.0 if vol_dir == direction or vol_dir == 0 else 0.4)
            c_ = 25 * min(1.0, cat)
            r_ = 25 * risk
            return int(round(t_ + m_ + v_ + c_) * (0.6 + 0.4 * risk))
        row.trend, row.momentum, row.volume, row.risk = round(25 * abs(tr), 1), round(25 * abs(mo), 1), round(25 * vol_score, 1), round(25 * risk, 1)
        row.score_long, row.score_short = total(1, cat_long), total(-1, cat_short)
        if row.score_long >= row.score_short:
            row.direction, row.score, row.catalyst = "LONG", row.score_long, round(25 * min(1.0, cat_long), 1)
        else:
            row.direction, row.score, row.catalyst = "SHORT", row.score_short, round(25 * min(1.0, cat_short), 1)
        row.tags = tags
        return row

    # ---------------------------------------------------------------- tarama
    def scan(self, extra_symbols: list[str] | None = None) -> ScanResult:
        t0 = time.time()
        uni = self.universe()
        rows: list[ScanRow] = []
        for perp, t in uni:
            try:
                rows.append(self._features(perp, t))
            except Exception as exc:  # noqa: BLE001
                rows.append(ScanRow(symbol=perp.split(":")[0], perp=perp, error=str(exc)[:80]))
        ok = [r for r in rows if not r.error]
        ok.sort(key=lambda r: -r.score)
        flagged = [r for r in ok if r.score >= self.flag_score]
        setups = flagged[: self.top_n]
        res = ScanResult(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), universe=len(uni), scanned=len(ok),
                         flagged=len(flagged), setups=setups, rows=ok, min_volume=self.min_volume, seconds=round(time.time() - t0, 1))
        log.info("Tarama: evren %d, tarandı %d, işaretlendi %d, setup %d (%.0fs)", len(uni), len(ok), len(flagged), len(setups), res.seconds)
        return res


def persist_scan(res: ScanResult, state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / "scan.json"
    p.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def scanner_note(res: ScanResult) -> str:
    local = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["---", f"updated: {res.generated_at}", "tags: [trading, scanner]", "---",
             "# 🔎 Piyasa Tarayıcı — tüm Binance USDT perpetual evreni",
             f"> {local} · Evren **{res.universe}** (24s hacim ≥ {res.min_volume/1e6:.0f}M) → tarandı **{res.scanned}** → işaretlendi **{res.flagged}** → setup **{len(res.setups)}** · {res.seconds}s", "",
             "```mermaid", "flowchart TB",
             f'    A["🌐 EVREN<br/>{res.universe} sembol"] --> B["🔎 TARANDI<br/>{res.scanned}"]',
             f'    B --> C["🚩 İŞARETLENDİ<br/>{res.flagged} (skor ≥ {60})"]',
             f'    C --> D["⭐ SETUP<br/>{len(res.setups)} → derin ajan analizi"]',
             "    classDef f fill:#1e3a5f,stroke:#64b5f6,color:#fff", "    class A,B,C,D f", "```", "",
             "## ⭐ En iyi setuplar (AI skoru)",
             "| # | Sembol | Yön | Skor | Trend/25 | Momentum/25 | Hacim/25 | Tetikleyici/25 | Risk/25 | Fiyat | 24s % | Hacim 24s | Funding % | RSI 4h | ATR % | Etiketler |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(res.setups, 1):
        base = r.symbol.split("/")[0]
        lines.append(f"| {i} | [[Agents/{base}\\|{r.symbol}]] | {'🟢' if r.direction=='LONG' else '🔴'} {r.direction} | **{r.score}** | {r.trend} | {r.momentum} | {r.volume} | {r.catalyst} | {r.risk} | "
                     f"{r.price:.6g} | {r.chg24_pct:+.1f} | {r.vol24_usdt/1e6:,.0f}M | {r.funding_pct:.4f} | {r.rsi_4h:.0f} | {r.atr_pct:.2f} | {', '.join(r.tags) or '-'} |")
    lines += ["", "## Tüm işaretlenenler", "| Sembol | Yön | Skor | Long | Short | 24s % | Hacim | Etiketler |", "|---|---|---|---|---|---|---|---|"]
    for r in [x for x in res.rows if x.score >= 60][:60]:
        lines.append(f"| {r.symbol} | {r.direction} | {r.score} | {r.score_long} | {r.score_short} | {r.chg24_pct:+.1f} | {r.vol24_usdt/1e6:,.0f}M | {', '.join(r.tags) or '-'} |")
    lines += ["", "Skor = Trend + Momentum + Hacim + Tetikleyici (her biri 25), volatilite/aşırılık riskiyle çarpılır. "
              "Setup'lar `Agents/` altında 8 uzman ajan + yönetici tarafından derinlemesine analiz edilir.", "", "[[Dashboard]]"]
    return "\n".join(lines)

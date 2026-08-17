"""Sinyal görselleri (PNG) — TradingView tarzı: mumlar, EMA 20/50/200, ZigZag dalga etiketleri (1)…(5),
giriş çizgisi, STOP (kırmızı) ve HEDEF (yeşil) kutuları, TP1/TP2 okları, kilit seviyeler.
matplotlib (Agg) ile üretilir; Obsidian notlarına ![[Charts/…png]] olarak gömülür.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BG, FG, GRID = "#0e1116", "#d7dde5", "#1c2430"
UP, DN = "#26a69a", "#ef5350"


def zigzag(high: np.ndarray, low: np.ndarray, close: np.ndarray, threshold_pct: float) -> list[tuple[int, float, str]]:
    """Basit ZigZag: threshold% ters hareketle pivotlar. (idx, fiyat, 'H'|'L')"""
    piv: list[tuple[int, float, str]] = []
    if len(close) < 5:
        return piv
    trend = 0
    last_i, last_p = 0, close[0]
    ext_i, ext_p = 0, close[0]
    for i in range(1, len(close)):
        if trend >= 0:
            if high[i] > ext_p or trend == 0 and high[i] >= ext_p:
                ext_i, ext_p = i, high[i]
            if low[i] < ext_p * (1 - threshold_pct / 100):
                piv.append((ext_i, ext_p, "H"))
                trend, ext_i, ext_p = -1, i, low[i]
        else:
            if low[i] < ext_p:
                ext_i, ext_p = i, low[i]
            if high[i] > ext_p * (1 + threshold_pct / 100):
                piv.append((ext_i, ext_p, "L"))
                trend, ext_i, ext_p = 1, i, high[i]
    piv.append((ext_i, ext_p, "H" if trend >= 0 else "L"))
    return piv


_EMOJI_RE = None


def _plain(s: str) -> str:
    """Yazı tipinde olmayan emoji/sembolleri temizle (matplotlib uyarısı vermesin)."""
    return "".join(ch for ch in s if ord(ch) < 0x2600 or 0x2190 <= ord(ch) <= 0x21FF).replace("  ", " ")


def render_signal_chart(df: pd.DataFrame, out_path: Path, *, title: str, plan=None, levels: dict | None = None,
                        bars: int = 110, position: dict | None = None, footer: str = "") -> Path:
    import warnings
    warnings.filterwarnings("ignore", message="Glyph")
    title, footer = _plain(title), _plain(footer)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    d = df.iloc[-bars:].copy()
    n = len(d)
    x = np.arange(n)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    fig, ax = plt.subplots(figsize=(13, 6.5), dpi=110)
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.tick_params(colors=FG, labelsize=8)
    ax.grid(color=GRID, linewidth=0.5, alpha=0.6)
    # mumlar
    for i in range(n):
        col = UP if c[i] >= o[i] else DN
        ax.plot([i, i], [l[i], h[i]], color=col, linewidth=0.8)
        ax.add_patch(Rectangle((i - 0.35, min(o[i], c[i])), 0.7, max(abs(c[i] - o[i]), (h[i] - l[i]) * 0.02 + 1e-12), facecolor=col, edgecolor=col))
    # EMA'lar
    for col_name, color, lbl in (("ema20", "#f5c542", "EMA20"), ("ema50", "#ff8a65", "EMA50"), ("ema200", "#ba68c8", "EMA200")):
        if col_name in d:
            ax.plot(x, d[col_name].to_numpy(float), color=color, linewidth=1.1, label=lbl, alpha=0.9)
    # ZigZag dalga etiketleri
    atr_pct = float(d["atr_pct"].iloc[-1]) if "atr_pct" in d and pd.notna(d["atr_pct"].iloc[-1]) else 1.5
    piv = zigzag(h, l, c, threshold_pct=max(1.0, atr_pct * 2.2))
    if len(piv) >= 2:
        px_ = [p[0] for p in piv]; py_ = [p[1] for p in piv]
        ax.plot(px_, py_, color="#90caf9", linewidth=1.0, alpha=0.8, linestyle="--")
        for k, (i, p, t) in enumerate(piv[-6:], start=max(0, len(piv) - 6)):
            lab = f"({(k % 5) + 1})"
            ax.annotate(lab, (i, p), textcoords="offset points", xytext=(0, 8 if t == "H" else -14), ha="center", fontsize=8, color="#90caf9")
    price = float(c[-1])
    xr = n + 14
    ax.set_xlim(-1, xr)
    # plan / pozisyon kutuları
    p = plan
    if position:
        p = type("P", (), {})()
        p.direction, p.entry, p.stop, p.target1, p.target2, p.valid, p.trigger_text = position["side"], position["entry"], position["stop"], position["target1"], position["target2"], True, "AÇIK POZİSYON"
    if p is not None and getattr(p, "direction", "BEKLE") != "BEKLE" and p.entry and p.stop and p.target1:
        x0, w = n - 1, 13
        e, s, t1 = float(p.entry), float(p.stop), float(p.target1)
        t2 = float(p.target2) if p.target2 else t1
        # stop kutusu
        ax.add_patch(Rectangle((x0, min(e, s)), w, abs(e - s), facecolor=DN, alpha=0.22, edgecolor=DN, linewidth=0.8))
        # hedef kutuları
        ax.add_patch(Rectangle((x0, min(e, t1)), w, abs(t1 - e), facecolor=UP, alpha=0.20, edgecolor=UP, linewidth=0.8))
        ax.add_patch(Rectangle((x0, min(t1, t2)), w, abs(t2 - t1), facecolor=UP, alpha=0.10, edgecolor=UP, linewidth=0.6, linestyle="--"))
        for yv, lbl, col in ((e, f"GİRİŞ {e:.6g}", "#ffffff"), (s, f"STOP {s:.6g}", DN), (t1, f"TP1 {t1:.6g} ({(t1/e-1)*100:+.2f}%)", UP), (t2, f"TP2 {t2:.6g} ({(t2/e-1)*100:+.2f}%)", UP)):
            ax.axhline(yv, xmin=(x0 + 1) / (xr + 1), color=col, linewidth=0.9, linestyle=":" if lbl.startswith("TP") else "-")
            ax.text(xr - 0.3, yv, lbl, color=col, fontsize=8, ha="right", va="bottom", fontweight="bold",
                    bbox=dict(facecolor=BG, edgecolor="none", alpha=0.7, pad=1))
        ax.annotate("", xy=(x0 + w / 2, t1), xytext=(x0 + w / 2, e), arrowprops=dict(arrowstyle="->", color=UP, lw=1.6))
        head = _plain(f"{p.direction} · {'PLAN GEÇERLİ' if getattr(p, 'valid', False) else 'PLAN GEÇERSİZ: ' + getattr(p, 'invalid_reason', '')} · {getattr(p, 'trigger_text', '')}")
    else:
        head = "BEKLE — yön yok"
    # seviyeler
    for k, v in (levels or {}).items():
        if k in ("r1", "r2", "s1", "s2") and v:
            ax.axhline(float(v), color="#b0bec5", linewidth=0.7, alpha=0.6, linestyle="--")
            ax.text(0, float(v), f" {k.upper()} {float(v):.6g}", color="#b0bec5", fontsize=7, va="bottom")
    ax.axhline(price, color="#e0e0e0", linewidth=0.6, alpha=0.5)
    ax.text(n - 0.5, price, f" {price:.6g}", color=BG, fontsize=8, va="center", ha="left",
            bbox=dict(facecolor="#e0e0e0", edgecolor="none", pad=1.5))
    # eksen etiketleri (tarih)
    ticks = list(range(0, n, max(1, n // 8)))
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(d.index[i])[5:16] for i in ticks], rotation=0, fontsize=7)
    ax.set_title(f"{title}\n{head}", color=FG, fontsize=11, loc="left")
    ax.legend(loc="upper left", fontsize=7, facecolor=BG, edgecolor=GRID, labelcolor=FG)
    if footer:
        fig.text(0.01, 0.01, footer, color="#78909c", fontsize=7)
    fig.text(0.99, 0.01, "Trading Bot · Claude Code · paper only · not financial advice", color="#78909c", fontsize=7, ha="right")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=BG)
    plt.close(fig)
    return out_path

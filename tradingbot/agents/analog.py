"""GEÇMİŞ BENZERLİK (ANALOG) AJANI — "geçmişte böyle olduğunda sonra ne oldu?"

Her zaman diliminde (1h / 4h / 1d):
  1. Şu anki durumun "parmak izi" çıkarılır:
     - ŞEKİL: son W barın normalize fiyat yolu (log-fiyat, ortalaması 0, std 1)
     - GÖSTERGE: RSI14, EMA20/50/200'e uzaklık (%), ATR%, hacim oranı, 20-bar konum
  2. Geçmişteki her W-bar pencere için aynı parmak izi hesaplanır; benzerlik =
     0.6 × şekil korelasyonu + 0.4 × gösterge yakınlığı (z-uzaklıktan)
  3. En benzer K pencere (korelasyon ≥ eşik, birbirinden en az W/2 bar uzak) seçilir;
     her birinin SONRAKİ H bar getirisi ölçülür → dağılım (ortalama, medyan, yukarı oranı, MAE/MFE)
  4. Bias = ortalama ileri getiri / beklenen ATR hareketi (sınırlı); güven = örnek sayısı × tutarlılık × benzerlik
Uyarı: geçmiş benzerlik = olasılık, garanti değil. Kaç örnek olduğunu ve tutarlılığı raporlar; azsa güven düşer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from .base import Agent, AgentReport, CoinContext, fmt_px

CFG = {"1d": {"W": 20, "H": 5, "w": 0.45}, "4h": {"W": 30, "H": 6, "w": 0.35}, "1h": {"W": 36, "H": 12, "w": 0.20}}
TF_TR = {"1d": "günlük", "4h": "4 saatlik", "1h": "saatlik"}
K, MIN_CORR = 30, 0.55


def _shape(logp: np.ndarray) -> np.ndarray:
    x = logp - logp.mean()
    s = x.std()
    return x / s if s > 1e-12 else x


def _indicator_matrix(df: pd.DataFrame) -> np.ndarray:
    c = df["close"]
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    rsi = ind.rsi(c, 14)
    atrp = ind.atr(df["high"], df["low"], c, 14) / c * 100
    v = df["volume"]
    vr = v / v.rolling(20).mean()
    pos20 = (c - df["low"].rolling(20).min()) / (df["high"].rolling(20).max() - df["low"].rolling(20).min())
    m = np.column_stack([
        (rsi - 50) / 20, (c / e20 - 1) * 100, (c / e50 - 1) * 100, (c / e200 - 1) * 100, atrp, np.log(vr.clip(lower=0.05)), (pos20 - 0.5) * 2,
    ])
    return m


def find_analogs(df: pd.DataFrame, W: int, H: int, k: int = K, min_corr: float = MIN_CORR) -> dict | None:
    n = len(df)
    if n < W + H + 250:
        return None
    logp = np.log(df["close"].to_numpy(float))
    M = _indicator_matrix(df)
    M = np.nan_to_num(M, nan=0.0)
    # standardize indicator columns
    mu, sd = M.mean(axis=0), M.std(axis=0) + 1e-9
    Z = (M - mu) / sd
    cur_shape = _shape(logp[n - W:n])
    cur_ind = Z[n - 1]
    scores = []
    last = n - W - H          # ileri getiri ölçülebilsin diye
    for i in range(W, last):
        seg = _shape(logp[i - W:i])
        corr = float(np.dot(seg, cur_shape) / W)
        if corr < min_corr:
            continue
        dist = float(np.linalg.norm(Z[i - 1] - cur_ind)) / np.sqrt(Z.shape[1])
        sim = 0.6 * corr + 0.4 * max(0.0, 1.0 - dist / 2.0)
        scores.append((sim, corr, i))
    if not scores:
        return {"count": 0}
    scores.sort(reverse=True)
    picked: list[tuple[float, float, int]] = []
    for s in scores:
        if all(abs(s[2] - p[2]) >= W // 2 for p in picked):
            picked.append(s)
        if len(picked) >= k:
            break
    fwd = np.array([(logp[i + H - 1] - logp[i - 1]) for _, _, i in picked]) * 100  # H bar sonraki getiri %
    mfe = np.array([(np.max(logp[i:i + H]) - logp[i - 1]) for _, _, i in picked]) * 100
    mae = np.array([(np.min(logp[i:i + H]) - logp[i - 1]) for _, _, i in picked]) * 100
    atr_h = float((ind.atr(df["high"], df["low"], df["close"], 14) / df["close"]).iloc[-1] * 100 * np.sqrt(H))
    return {"count": len(picked), "mean_sim": float(np.mean([p[0] for p in picked])), "mean_corr": float(np.mean([p[1] for p in picked])),
            "fwd_mean": float(fwd.mean()), "fwd_median": float(np.median(fwd)), "up_ratio": float((fwd > 0).mean()),
            "mfe_mean": float(mfe.mean()), "mae_mean": float(mae.mean()), "atr_h": atr_h, "H": H, "W": W,
            "dates": [str(df.index[i - 1])[:16] for _, _, i in picked[:5]], "fwd_top": [round(float(f), 2) for f in fwd[:5]]}


class AnalogAgent(Agent):
    name, title, weight = "analog", "Geçmiş Benzerlik Ajanı", 0.15

    def analyze(self, ctx: CoinContext, rep: AgentReport) -> None:
        total, wsum = 0.0, 0.0
        for tf, c in CFG.items():
            df = ctx.frame(tf)
            if df is None:
                continue
            res = find_analogs(df, c["W"], c["H"])
            if not res:
                rep.findings.append(f"{TF_TR[tf].capitalize()}: benzerlik için yeterli geçmiş yok")
                continue
            if res["count"] < 5:
                rep.findings.append(f"{TF_TR[tf].capitalize()}: son {c['W']} bara yeterince benzeyen geçmiş bulunamadı ({res['count']} örnek) → görüş yok")
                continue
            edge = res["fwd_mean"] / max(res["atr_h"], 1e-9)          # ATR-normalize beklenen hareket
            consist = abs(res["up_ratio"] - 0.5) * 2                   # 0 (yazı-tura) … 1 (hepsi aynı yön)
            b = max(-1.0, min(1.0, edge * 1.5)) * (0.5 + 0.5 * consist)
            conf_tf = min(1.0, res["count"] / 20) * (0.5 + 0.5 * consist) * min(1.0, res["mean_corr"] / 0.8)
            total += b * c["w"] * (0.5 + 0.5 * conf_tf)
            wsum += c["w"] * (0.5 + 0.5 * conf_tf)
            arrow = "↑" if res["fwd_mean"] > 0 else "↓"
            rep.findings.append(
                f"{TF_TR[tf].capitalize()}: son {c['W']} bara benzeyen {res['count']} geçmiş durum (ort. korelasyon {res['mean_corr']:.2f}); "
                f"sonraki {c['H']} barda ort. %{res['fwd_mean']:+.2f} {arrow} (medyan %{res['fwd_median']:+.2f}), yukarı oranı %{res['up_ratio']*100:.0f}, "
                f"ort. lehte %{res['mfe_mean']:+.2f} / aleyhte %{res['mae_mean']:+.2f} — örn. {', '.join(res['dates'][:3])}"
            )
            rep.metrics[f"analog_{tf}"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res.items() if k not in ("dates", "fwd_top")}
            rep.metrics[f"analog_{tf}_dates"] = res["dates"]
            if res["count"] >= 15 and consist > 0.5:
                rep.levels[f"analog_target_{tf}"] = float(ctx.price * (1 + res["fwd_mean"] / 100))
        rep.bias = total / wsum if wsum else 0.0
        rep.confidence = int(30 + 60 * min(1.0, abs(rep.bias))) if wsum else 0
        best = max((rep.metrics.get(f"analog_{tf}") for tf in CFG if isinstance(rep.metrics.get(f"analog_{tf}"), dict)),
                   key=lambda r: r["count"] * abs(r["up_ratio"] - 0.5), default=None)
        if best:
            rep.summary = (f"Geçmiş benzerlik {rep.stance_lower}: en güçlü kanıt {best['count']} örnekte {best['H']} bar sonra "
                           f"ort. %{best['fwd_mean']:+.2f}, yukarı %{best['up_ratio']*100:.0f}. Olasılık, garanti değil.")
        else:
            rep.summary = "Geçmişte yeterince benzer durum yok → benzerlik ajanı görüş bildirmiyor."
        if wsum and abs(rep.bias) > 0.3 and any((rep.metrics.get(f"analog_{tf}") or {}).get("count", 0) < 10 for tf in CFG if rep.metrics.get(f"analog_{tf}")):
            rep.warnings.append("Benzerlik sinyali az örneğe dayanıyor (<10): tek başına işlem gerekçesi yapma")

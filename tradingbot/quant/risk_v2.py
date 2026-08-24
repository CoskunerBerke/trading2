"""Risk Engine V2 — SALT TAVSİYE (advisory/shadow) challenger bileşeni (`quant_risk_v2`).

Sözleşme (kod düzeyinde zorunlu):
* Default DISABLED ve PAPER-only; emir, outbox, ana ledger veya gateway yolu YOKTUR — bütün
  fonksiyonlar saf hesaptır, hiçbir state dosyasına yazmaz.
* Mevcut `RiskEngine`/`KillSwitch`/`LeverageConfig` sınırları DIŞ SINIRDIR: buradaki öneri asla
  o sınırları gevşetemez; yalnız AZALTICI yönde sapabilir.
* Kaldıraç önerisi 2x–5x bandının dışına çıkamaz; düşük veri kalitesi veya yüksek belirsizlikte
  risk ARTIRILMAZ (konservatif fallback).
* Pozitif korelasyonlu pozisyonlar bağımsız bahis sayılmaz: rolling korelasyon kümeleri tek risk
  kümesi olarak raporlanır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "quant_risk_v2"

ABS_MIN_LEVERAGE = 2
ABS_MAX_LEVERAGE = 5


@dataclass
class RiskV2Config:
    """Advisory Risk V2 ayarları — güvenli varsayılanlar: kapalı, yalnız tavsiye."""
    enabled: bool = False
    advisory_only: bool = True                 # False DESTEKLENMEZ (validate fail-closed)
    vol_window: int = 20
    vol_min_obs: int = 10
    target_vol_pct: float = 2.0                # hedef bar-volatilitesi (%)
    corr_window: int = 30
    corr_min_obs: int = 20
    cluster_threshold: float = 0.7
    max_cluster_weight: float = 0.5            # tek kümenin toplam risk payı uyarı eşiği
    drawdown_derisk_pct: float = 8.0           # portföy DD bu eşiği aşarsa kaldıraç önerisi düşer

    def validate(self) -> None:
        if not self.advisory_only:
            raise ValueError("RISK_V2_ADVISORY_ONLY: advisory_only=false desteklenmiyor — "
                             "Risk V2 emir/limit yolu değildir")
        if self.vol_window < 2 or self.corr_window < 2:
            raise ValueError("risk_v2 pencere ayarları geçersiz (>=2 olmalı)")
        if not (0.0 < self.cluster_threshold <= 1.0):
            raise ValueError("risk_v2.cluster_threshold (0,1] aralığında olmalı")


# ------------------------------------------------------------------ volatilite

def realized_vol_pct(returns: Iterable[float], *, window: int = 20, min_obs: int = 10) -> float | None:
    """Son `window` getirinin (yüzde) örnek std'si. Yetersiz/sonlu-olmayan veri → None (uydurma 0 yok)."""
    rs = [float(r) for r in returns if isinstance(r, (int, float)) and math.isfinite(float(r))]
    rs = rs[-window:]
    if len(rs) < max(2, min_obs):
        return None
    mean = sum(rs) / len(rs)
    var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
    return math.sqrt(var)


def vol_target_scale(current_vol_pct: float | None, target_vol_pct: float) -> tuple[float, str]:
    """Boyut ölçeği (0.25..1.0]. Volatilite hedefin üstünde → küçült; veri yoksa konservatif 0.5.
    ASLA 1.0'ın üstüne çıkmaz (volatilite düşük diye risk BÜYÜTÜLMEZ)."""
    if current_vol_pct is None or current_vol_pct <= 0:
        return 0.5, "VOL_UNKNOWN_CONSERVATIVE"
    if current_vol_pct <= target_vol_pct:
        return 1.0, "VOL_WITHIN_TARGET"
    return max(0.25, target_vol_pct / current_vol_pct), "VOL_ABOVE_TARGET_SCALED_DOWN"


# ------------------------------------------------------------------ korelasyon kümeleri

def rolling_correlation(returns_by_symbol: Mapping[str, list[float]], *,
                        window: int = 30, min_obs: int = 20) -> dict[tuple[str, str], float | None]:
    """Sembol çifti → Pearson korelasyonu (son `window` ortak gözlem). Yetersiz örnek → None."""
    syms = sorted(returns_by_symbol)
    out: dict[tuple[str, str], float | None] = {}
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            ra = [x for x in returns_by_symbol[a] if isinstance(x, (int, float)) and math.isfinite(float(x))][-window:]
            rb = [x for x in returns_by_symbol[b] if isinstance(x, (int, float)) and math.isfinite(float(x))][-window:]
            n = min(len(ra), len(rb))
            if n < min_obs:
                out[(a, b)] = None
                continue
            ra, rb = ra[-n:], rb[-n:]
            ma, mb = sum(ra) / n, sum(rb) / n
            cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
            va = math.sqrt(sum((x - ma) ** 2 for x in ra))
            vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
            out[(a, b)] = (cov / (va * vb)) if va > 0 and vb > 0 else None
    return out


def correlation_clusters(corr: Mapping[tuple[str, str], float | None], symbols: Iterable[str], *,
                         threshold: float = 0.7) -> list[list[str]]:
    """Eşik üstü POZİTİF korelasyonlu sembolleri union-find ile kümeler (deterministik, sıralı)."""
    syms = sorted(set(symbols))
    parent = {s: s for s in syms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), c in sorted(corr.items()):
        if c is not None and c >= threshold and a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = {}
    for s in syms:
        groups.setdefault(find(s), []).append(s)
    return sorted(groups.values())


def cluster_exposure(positions: Iterable[Mapping[str, Any]], clusters: list[list[str]]) -> dict[str, Any]:
    """Pozisyonlar → küme bazlı YÖNLÜ maruziyet + toplam LONG/SHORT.

    `positions`: {symbol, direction (LONG/SHORT), risk_usdt | notional_usdt}. Aynı yönlü küme
    maruziyeti tek bahis gibi toplanır (AAVE+ETH+LDO LONG üç bağımsız bahis DEĞİLDİR).
    """
    sym_cluster = {s: i for i, grp in enumerate(clusters) for s in grp}
    per: dict[int, dict[str, Any]] = {}
    tot_long = tot_short = 0.0
    total = 0.0
    for p in positions:
        sym = str(p.get("symbol"))
        amt = p.get("risk_usdt", p.get("notional_usdt"))
        if not isinstance(amt, (int, float)) or not math.isfinite(float(amt)):
            continue
        amt = abs(float(amt))
        direction = str(p.get("direction", "LONG")).upper()
        ci = sym_cluster.get(sym, -1)
        d = per.setdefault(ci, {"symbols": [], "long_usdt": 0.0, "short_usdt": 0.0})
        d["symbols"].append(sym)
        if direction == "SHORT":
            d["short_usdt"] += amt
            tot_short += amt
        else:
            d["long_usdt"] += amt
            tot_long += amt
        total += amt
    rows = []
    for ci, d in sorted(per.items()):
        net = d["long_usdt"] - d["short_usdt"]
        rows.append({"cluster": ci, "symbols": sorted(set(d["symbols"])),
                     "long_usdt": round(d["long_usdt"], 4), "short_usdt": round(d["short_usdt"], 4),
                     "net_usdt": round(net, 4),
                     "share_of_total": round((d["long_usdt"] + d["short_usdt"]) / total, 4) if total > 0 else None})
    return {"clusters": rows, "total_long_usdt": round(tot_long, 4),
            "total_short_usdt": round(tot_short, 4), "total_usdt": round(total, 4)}


# ------------------------------------------------------------------ tavsiye

@dataclass
class AdviceContext:
    """Tek karar için tavsiye girdisi. Bütün alanlar opsiyoneldir; eksik veri riski ARTIRMAZ."""
    symbol: str
    direction: str
    proposed_leverage: int
    stop_distance_pct: float | None = None
    symbol_vol_pct: float | None = None
    spread_bps: float | None = None
    calibrated_edge: float | None = None       # kalibre p_win - 0.5 gibi (pozitif = lehte)
    model_uncertainty: float | None = None     # 0..1 (1 = tam belirsiz)
    portfolio_drawdown_pct: float | None = None
    cluster_share: float | None = None         # bu sembolün kümesinin toplam risk payı
    data_quality_ok: bool = True
    reasons: list[str] = field(default_factory=list)


def advise(ctx: AdviceContext, cfg: RiskV2Config | None = None) -> dict[str, Any]:
    """Advisory kaldıraç/boyut önerisi. ASLA emir üretmez; çıktı yalnız araştırma raporudur.

    Kural: öneri `proposed_leverage`'ı hiçbir koşulda AŞAMAZ (risk artırma yok); 2x tabanının
    altına inmesi gereken durumda `leverage=2` + `stand_aside=True` önerilir.
    """
    cfg = cfg or RiskV2Config()
    cfg.validate()
    reasons: list[str] = []
    lev = max(ABS_MIN_LEVERAGE, min(ABS_MAX_LEVERAGE, int(ctx.proposed_leverage)))
    if lev != ctx.proposed_leverage:
        reasons.append(f"LEV_CLAMPED_{ctx.proposed_leverage}->{lev}")
    scale, why = vol_target_scale(ctx.symbol_vol_pct, cfg.target_vol_pct)
    reasons.append(why)
    stand_aside = False
    steps = 0
    if scale < 0.75:
        steps += 1
    if not ctx.data_quality_ok:
        steps += 1
        reasons.append("DATA_QUALITY_DEGRADED_DERISK")
    if ctx.model_uncertainty is not None and ctx.model_uncertainty > 0.6:
        steps += 1
        reasons.append("MODEL_UNCERTAINTY_HIGH")
    if ctx.calibrated_edge is not None and ctx.calibrated_edge <= 0:
        steps += 1
        stand_aside = True
        reasons.append("NO_CALIBRATED_EDGE")
    if ctx.cluster_share is not None and ctx.cluster_share > cfg.max_cluster_weight:
        steps += 1
        reasons.append("CLUSTER_CONCENTRATION_HIGH")
    if ctx.portfolio_drawdown_pct is not None and ctx.portfolio_drawdown_pct >= cfg.drawdown_derisk_pct:
        steps += 1
        reasons.append("PORTFOLIO_DRAWDOWN_DERISK")
    if ctx.spread_bps is not None and ctx.spread_bps > 20:
        steps += 1
        reasons.append("SPREAD_WIDE")
    if ctx.stop_distance_pct is not None and ctx.stop_distance_pct <= 0:
        stand_aside = True
        reasons.append("STOP_DISTANCE_INVALID")
    advised = lev - steps
    if advised < ABS_MIN_LEVERAGE:
        advised = ABS_MIN_LEVERAGE
        stand_aside = stand_aside or steps >= lev - ABS_MIN_LEVERAGE + 1
    return {"schema_version": SCHEMA_VERSION, "advisory_only": True, "enabled": cfg.enabled,
            "symbol": ctx.symbol, "direction": ctx.direction,
            "proposed_leverage": ctx.proposed_leverage,
            "advised_leverage": int(advised), "risk_scale": round(min(scale, 1.0), 4),
            "stand_aside": stand_aside, "reasons": reasons,
            "outer_limits": {"min": ABS_MIN_LEVERAGE, "max": ABS_MAX_LEVERAGE,
                             "note": "mevcut RiskEngine/KillSwitch/LeverageConfig dış sınırdır"}}

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

#: Mevcut kaldıraç advisory tabanının (2x) altındayken öneri onu YUKARI itemez.
HOLD_BELOW_MIN = "HOLD_CURRENT_BELOW_ADVISORY_MIN"

#: Korelasyon kanıtının kalitesi. Yalnız `OK` iken gerçek clustering'e güvenilir.
CORR_OK, CORR_FALLBACK, CORR_UNAVAILABLE = "OK", "FALLBACK", "UNAVAILABLE"

#: Korelasyon bilinmiyorken kullanılan konservatif yön kümeleri (sembol adı HARD-CODE EDİLMEZ).
UNKNOWN_CORR_LONG = "UNKNOWN_CORRELATION_LONG"
UNKNOWN_CORR_SHORT = "UNKNOWN_CORRELATION_SHORT"


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


def correlation_quality(corr: Mapping[tuple[str, str], float | None],
                        symbols: Iterable[str]) -> str:
    """Çift bazında korelasyon kanıtının kalitesi: `OK` (hepsi ölçülebildi) / `FALLBACK` (kısmi) /
    `UNAVAILABLE` (hiçbiri). Tek sembol ya da sembolsüz portföyde korelasyon anlamsızdır → `OK`."""
    syms = sorted(set(symbols))
    pairs = [(a, b) for i, a in enumerate(syms) for b in syms[i + 1:]]
    if not pairs:
        return CORR_OK
    known = sum(1 for p in pairs if corr.get(p) is not None)
    if known == len(pairs):
        return CORR_OK
    return CORR_UNAVAILABLE if known == 0 else CORR_FALLBACK


def conservative_direction_clusters(positions: Iterable[Mapping[str, Any]]
                                    ) -> tuple[list[list[str]], list[str]]:
    """Korelasyon bilinmiyorken FAIL-CLOSED gruplama: aynı YÖNDEKİ bütün pozisyonlar TEK kümede.

    Gerekçe: kanıt yokken pozisyonları bağımsız bahis saymak konsantrasyonu olduğundan düşük
    gösterir. Gruplama yalnız `direction` metadata'sına dayanır; hiçbir sembol adı kodda
    özel-durum DEĞİLDİR. Dönen: (küme sembol listeleri, küme etiketleri).
    """
    long_syms: set[str] = set()
    short_syms: set[str] = set()
    for p in positions:
        sym = str(p.get("symbol") or "")
        if not sym:
            continue
        (short_syms if str(p.get("direction", "LONG")).upper() == "SHORT" else long_syms).add(sym)
    # Aynı sembol iki yönde açıksa (one-way defterde olmaz) LONG kümesinde tekilleştirilir.
    short_syms -= long_syms
    clusters: list[list[str]] = []
    labels: list[str] = []
    if long_syms:
        clusters.append(sorted(long_syms))
        labels.append(UNKNOWN_CORR_LONG)
    if short_syms:
        clusters.append(sorted(short_syms))
        labels.append(UNKNOWN_CORR_SHORT)
    return clusters, labels


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

    FAIL-CLOSED DEĞİŞMEZ: öneri `proposed_leverage`'ı HİÇBİR koşulda aşamaz.

    Kaldıraç YALNIZCA AŞAĞI kırpılır. `proposed < ABS_MIN_LEVERAGE` (ör. mevcut 1x pozisyon)
    olduğunda 2x'lik advisory tabanı UYGULANMAZ — aksi halde eksik/stale/NaN veride bile öneri
    riski artırırdı. Bu durumda mevcut kaldıraç korunur ve `HOLD_CURRENT_BELOW_ADVISORY_MIN`
    gerekçesi eklenir. 2–5x PAPER bandı, "riski artırma" kuralını GEÇERSİZ KILAMAZ.
    """
    cfg = cfg or RiskV2Config()
    cfg.validate()
    reasons: list[str] = []
    proposed = int(ctx.proposed_leverage)
    below_min = proposed < ABS_MIN_LEVERAGE
    lev = min(ABS_MAX_LEVERAGE, proposed)          # YALNIZCA asagi kirpma; yukari ASLA
    if lev != proposed:
        reasons.append(f"LEV_CLAMPED_DOWN_{proposed}->{lev}")
    if below_min:
        reasons.append(HOLD_BELOW_MIN)
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
    # Taban: normalde 2x; mevcut kaldıraç 2x'in ALTINDAysa taban mevcut kaldıraçtır (yukarı itilmez).
    floor = lev if below_min else ABS_MIN_LEVERAGE
    if advised < floor:
        advised = floor
        stand_aside = stand_aside or steps >= lev - floor + 1
    # SERT DEĞİŞMEZ: öneri hiçbir yoldan teklifi/mevcudu aşamaz.
    advised = min(advised, lev)
    return {"schema_version": SCHEMA_VERSION, "advisory_only": True, "enabled": cfg.enabled,
            "symbol": ctx.symbol, "direction": ctx.direction,
            "proposed_leverage": ctx.proposed_leverage,
            "advised_leverage": int(advised), "risk_scale": round(min(scale, 1.0), 4),
            "increases_risk": int(advised) > proposed,          # sözleşme gereği DAİMA False
            "held_below_advisory_min": below_min,
            "stand_aside": stand_aside, "reasons": reasons,
            "outer_limits": {"min": ABS_MIN_LEVERAGE, "max": ABS_MAX_LEVERAGE,
                             "note": "mevcut RiskEngine/KillSwitch/LeverageConfig dış sınırdır; "
                                     "2-5x bandı 'riski artırma' kuralını geçersiz kılamaz"}}


# ------------------------------------------------------------------ offline rapor

ADVISORY_BANNER = "ADVISORY ONLY — ACTIVE RISK ENGINE UNCHANGED"


def positions_from_ledger(ledger_doc: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """ÜRETİM `futures_ledger.json` şemasından SALT OKUNUR pozisyon çıkarımı.

    Yalnız açık (`status == "OPEN"`) pozisyonlar alınır; sayısal alanlar Decimal string olabilir.
    Ledger'a hiçbir şey yazılmaz.
    """
    out: list[dict[str, Any]] = []
    for p in ((ledger_doc or {}).get("positions") or {}).values():
        if not isinstance(p, dict):
            continue
        if str(p.get("status", "OPEN")).upper() not in ("OPEN", ""):
            continue
        qty, entry = _num(p.get("qty")), _num(p.get("entry_avg"))
        stop = _num(p.get("stop"))
        notional = qty * entry if (qty is not None and entry is not None) else None
        risk = abs((entry - stop) * qty) if (None not in (qty, entry, stop)) else None
        out.append({"symbol": str(p.get("symbol") or ""),
                    "direction": str(p.get("side") or "LONG").upper(),
                    "notional_usdt": notional,
                    "risk_usdt": risk,
                    "leverage": int(_num(p.get("leverage")) or 1),
                    "margin_usdt": _num(p.get("isolated_margin")),
                    "opened_at": p.get("opened_at")})
    return sorted(out, key=lambda d: d["symbol"])


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def offline_risk_report(positions: Iterable[Mapping[str, Any]],
                        returns_by_symbol: Mapping[str, list[float]] | None = None,
                        *, cfg: RiskV2Config | None = None,
                        vol_by_symbol: Mapping[str, float] | None = None,
                        portfolio_drawdown_pct: float | None = None,
                        data_as_of_ms: int | None = None,
                        now_ms: int | None = None,
                        data_quality_ok: bool = True,
                        max_data_age_ms: int | None = None) -> dict[str, Any]:
    """Offline (salt okunur) Risk V2 raporu — küme, maruziyet, risk katkısı ve advisory kaldıraç.

    AKTİF KARAR YOLUNA BAĞLI DEĞİLDİR: dönen sözlük yalnız rapordur, hiçbir emir/limit/ledger
    davranışını değiştirmez (`applies_to_active_engine=False`). Veri eksik/eski ise risk ARTIRAN
    öneri üretilmez; tersine konservatif tarafa sapılır.
    """
    cfg = cfg or RiskV2Config()
    cfg.validate()
    pos = [dict(p) for p in positions]
    rets = {k: list(v) for k, v in (returns_by_symbol or {}).items()}
    warnings: list[str] = []

    stale = False
    data_age_ms = None
    if data_as_of_ms is not None and now_ms is not None:
        data_age_ms = max(0, int(now_ms) - int(data_as_of_ms))
        if max_data_age_ms is not None and data_age_ms > max_data_age_ms:
            stale = True
            warnings.append("STALE_MARKET_DATA — konservatif tarafa sapıldı")
    quality_ok = bool(data_quality_ok) and not stale
    if not data_quality_ok:
        warnings.append("DATA_QUALITY_DEGRADED — risk artırıcı öneri üretilmez")

    symbols = sorted({str(p.get("symbol")) for p in pos if p.get("symbol")})
    missing_returns = [s for s in symbols if len(rets.get(s, [])) < cfg.corr_min_obs]
    corr = rolling_correlation(rets, window=cfg.corr_window, min_obs=cfg.corr_min_obs) if rets else {}
    corr_quality = correlation_quality(corr, symbols) if symbols else CORR_OK

    # FAIL-CLOSED: korelasyon kanıtı tam DEĞİLSE pozisyonlar bağımsız bahis SAYILMAZ; aynı yöndeki
    # pozisyonlar tek "bilinmeyen korelasyon" kümesinde toplanır. Kanıt tamsa gerçek rolling
    # korelasyon clustering'i aynen çalışır.
    if not symbols:
        clusters, cluster_labels, cluster_basis = [], [], "no_positions"
    elif corr_quality == CORR_OK:
        clusters = correlation_clusters(corr, symbols, threshold=cfg.cluster_threshold)
        cluster_labels = [f"corr_cluster_{i}" for i in range(len(clusters))]
        cluster_basis = "realized_rolling_correlation"
    else:
        clusters, cluster_labels = conservative_direction_clusters(pos)
        cluster_basis = "conservative_direction_fallback"
        warnings.append(
            f"KORELASYON KANITI {corr_quality} (eksik: {', '.join(missing_returns) or 'kismi cift'}) — "
            "pozisyonlar BAĞIMSIZ SAYILMADI; aynı yöndekiler tek konservatif kümede toplandı "
            f"({', '.join(cluster_labels) or 'kume yok'})")
    exposure = cluster_exposure(pos, clusters)
    for row in exposure.get("clusters", []):
        ci = row.get("cluster")
        row["label"] = cluster_labels[ci] if isinstance(ci, int) and 0 <= ci < len(cluster_labels) else "unassigned"

    total = exposure.get("total_usdt") or 0.0
    sym_cluster = {s: i for i, grp in enumerate(clusters) for s in grp}
    contributions = []
    advisories = []
    for p in pos:
        sym = str(p.get("symbol") or "")
        amt = _num(p.get("risk_usdt"))
        if amt is None:
            amt = _num(p.get("notional_usdt"))
        share = (abs(amt) / total) if (amt is not None and total > 0) else None
        ci = sym_cluster.get(sym, -1)
        cluster_row = next((c for c in exposure["clusters"] if c["cluster"] == ci), None)
        cluster_share = cluster_row.get("share_of_total") if cluster_row else None
        contributions.append({"symbol": sym, "direction": p.get("direction"),
                              "cluster": ci, "risk_usdt": amt,
                              "risk_share_of_total": round(share, 6) if share is not None else None,
                              "cluster_share_of_total": cluster_share,
                              "leverage": p.get("leverage")})
        vol = None
        if vol_by_symbol and sym in vol_by_symbol:
            vol = _num(vol_by_symbol.get(sym))
        elif rets.get(sym):
            vol = realized_vol_pct(rets[sym], window=cfg.vol_window, min_obs=cfg.vol_min_obs)
        # Teklif = MEVCUT kaldıraç. Eksikse 1 varsayılır (ABS_MIN_LEVERAGE DEĞİL) — aksi halde
        # bilinmeyen kaldıraç sessizce 2x'e yükseltilmiş sayılırdı.
        cur_lev = int(p.get("leverage") or 1)
        adv = advise(AdviceContext(symbol=sym, direction=str(p.get("direction") or "LONG"),
                                   proposed_leverage=cur_lev,
                                   symbol_vol_pct=vol, cluster_share=cluster_share,
                                   portfolio_drawdown_pct=portfolio_drawdown_pct,
                                   data_quality_ok=quality_ok), cfg)
        advisories.append({"symbol": sym, "direction": adv["direction"],
                           "current_leverage": cur_lev,
                           "advised_leverage": adv["advised_leverage"],
                           "risk_scale": adv["risk_scale"],
                           "stand_aside": adv["stand_aside"],
                           "held_below_advisory_min": adv["held_below_advisory_min"],
                           "derisk_reasons": adv["reasons"],
                           "realized_vol_pct": round(vol, 6) if vol is not None else None})
    # SERT DEĞİŞMEZ: hiçbir advisory mevcut kaldıracı ARTIRMAZ. Karşılaştırma DOĞRUDAN mevcut
    # kaldıraçladır — `max(current, ABS_MIN_LEVERAGE)` kullanmak 1x→2x artışını maskeliyordu.
    increases = [a for a in advisories if a["advised_leverage"] > a["current_leverage"]]
    biggest = max(exposure["clusters"], key=lambda c: (c.get("share_of_total") or 0.0),
                  default=None) if exposure["clusters"] else None
    return {"schema_version": SCHEMA_VERSION,
            "advisory_only": True, "enabled": cfg.enabled,
            "applies_to_active_engine": False,
            "banner": ADVISORY_BANNER,
            "n_positions": len(pos), "n_clusters": len(clusters),
            "clusters": clusters,
            "cluster_labels": cluster_labels,
            "cluster_basis": cluster_basis,
            "correlation_quality": corr_quality,
            "symbols_missing_returns": missing_returns,
            "exposure": exposure,
            "largest_cluster": biggest,
            "risk_contributions": contributions,
            "advisories": advisories,
            "leverage_bounds": {"min": ABS_MIN_LEVERAGE, "max": ABS_MAX_LEVERAGE,
                                "paper_only": True},
            "data_age_ms": data_age_ms, "data_stale": stale,
            "data_quality_ok": quality_ok,
            "warnings": warnings,
            "increases_risk": bool(increases),
            "note": "mevcut RiskEngine/KillSwitch/leverage sınırları DIŞ SINIRDIR; "
                    "bu rapor hiçbir aktif kararı değiştirmez"}

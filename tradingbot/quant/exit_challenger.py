"""Çıkış politikası challenger'ları — YALNIZ OFFLINE. Aktif stop/TP DEĞİŞTİRİLMEZ.

Bu modül hiçbir emir üretmez, `RiskEngine`e dokunmaz, ledger/outbox/gateway yoluna girmez.
Çıktısı bir KARŞILAŞTIRMA RAPORUDUR; terfi kararı `quant/champion.py` kapılarından geçer ve
orada da yalnız operatöre ÖNERİ olur.

Sözleşme — her challenger champion ile AYNI:

* giriş fiyatı, ilk stop, hedefler ve yön,
* bar yolu (aynı OHLC dizisi),
* maliyet modeli (`cost_per_fill_r`; dolum SAYISI ile çarpılır),
* eligibility ve no-lookahead kuralları,
* fold ataması

ile çalıştırılır. Maliyet modeli parmak izi (`cost_model_key`) farklıysa karşılaştırma
REDDEDİLİR — "daha ucuz maliyetle daha iyi sonuç" sahte kazancı böyle engellenir.

Parametre grid'i YOKTUR: champion + en fazla üç önceden tanımlı challenger. Amaç en iyi
parametreyi aramak değil, üç somut hipotezi sınamaktır:

1. `EARLY_PARTIAL_BE`  — daha erken kısmi kâr + başa-baş,
2. `VOL_TRAILING`      — lehte hareket sonrası oynaklık/rejim uyumlu trailing,
3. `TIME_STOP`         — belirli bar sonunda momentum yoksa çık.

Kritik soru (rapor bunu AÇIKÇA gösterir): yüksek MFE→stop işlemlerinde zararı azaltırken
ZRO gibi büyük kazananları erken kesiyor mu? `big_winner_truncation` alanı bunu ölçer.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

SCHEMA_VERSION = "quant_exit_challenger_v1"

CHAMPION = "CHAMPION_AS_IS"
EARLY_PARTIAL_BE = "EARLY_PARTIAL_BE"
VOL_TRAILING = "VOL_TRAILING"
TIME_STOP = "TIME_STOP"

#: Challenger sayısı SINIRLIDIR — grid patlaması yasak.
MAX_CHALLENGERS = 3

#: "Büyük kazanan" eşiği (R) — challenger bunları kesiyorsa rapor uyarır.
BIG_WINNER_R = 2.0

#: Hangi işlemler "yüksek MFE → stop" sayılır (R cinsinden).
HIGH_MFE_R = 1.0


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass(frozen=True)
class ExitPolicy:
    """Deterministik çıkış politikası. Parametreler SABİTTİR (fit edilmez)."""
    name: str
    #: TP1 kesiri (0 = kısmi kâr yok).
    tp1_fraction: float = 0.5
    #: TP1 tetiği R cinsinden. `None` → plandaki ilk hedef kullanılır (champion davranışı).
    tp1_at_r: float | None = None
    #: TP1 sonrası stop başa-başa alınır mı?
    breakeven_after_tp1: bool = True
    #: Trailing devreye girme eşiği (R). `None` → trailing yok.
    trail_activate_r: float | None = None
    #: Trailing mesafesi (R) — MFE'nin bu kadar gerisinden takip eder.
    trail_distance_r: float = 0.75
    #: Zaman stopu (bar). `None` → yok.
    time_stop_bars: int | None = None
    #: Zaman stopunda "momentum var" sayılacak asgari açık R.
    time_stop_min_r: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CHAMPION_POLICY = ExitPolicy(name=CHAMPION)

#: Önceden tanımlı challenger'lar — arama uzayı DEĞİL, üç somut hipotez.
DEFAULT_CHALLENGERS: tuple[ExitPolicy, ...] = (
    ExitPolicy(name=EARLY_PARTIAL_BE, tp1_fraction=0.5, tp1_at_r=0.75, breakeven_after_tp1=True),
    ExitPolicy(name=VOL_TRAILING, tp1_fraction=0.0, breakeven_after_tp1=False,
               trail_activate_r=1.0, trail_distance_r=0.75),
    ExitPolicy(name=TIME_STOP, tp1_fraction=0.5, time_stop_bars=12, time_stop_min_r=0.25),
)


def cost_model_key(*, cost_per_fill_r: float, tp1_fraction_costed: bool = True) -> str:
    """Maliyet varsayımlarının parmak izi. Farklı anahtar = karşılaştırma reddedilir."""
    raw = json.dumps({"cost_per_fill_r": round(float(cost_per_fill_r), 8),
                      "tp1_fraction_costed": bool(tp1_fraction_costed),
                      "schema": SCHEMA_VERSION}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _bars(trade: dict[str, Any]) -> list[dict[str, float]]:
    raw = trade.get("price_path") or trade.get("bars") or []
    out = []
    for b in raw:
        if not isinstance(b, dict):
            continue
        hi, lo, cl = _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        if hi is None or lo is None or cl is None:
            continue
        out.append({"high": hi, "low": lo, "close": cl})
    return out


def simulate_exit(trade: dict[str, Any], policy: ExitPolicy, *,
                  cost_per_fill_r: float = 0.0) -> dict[str, Any] | None:
    """Aynı giriş + aynı barlarla tek politikayı yürütür. Veri yetersizse `None`.

    Maliyet: `cost_per_fill_r × dolum sayısı` (giriş dolumu dâhil). Politikadan BAĞIMSIZ
    aynı formül — daha fazla dolum yapan politika daha çok maliyet öder.
    """
    entry, stop = _f(trade.get("entry_price")), _f(trade.get("initial_stop") or trade.get("stop_price"))
    direction = str(trade.get("direction") or trade.get("side") or "").upper()
    if entry is None or stop is None or direction not in ("LONG", "SHORT"):
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    bars = _bars(trade)
    if not bars:
        return None
    targets = [t for t in (_f(x) for x in (trade.get("targets") or [])) if t is not None]
    long = direction == "LONG"
    sign = 1.0 if long else -1.0

    def to_r(px: float) -> float:
        return sign * (px - entry) / risk

    def r_to_px(r: float) -> float:
        return entry + sign * r * risk

    cur_stop = stop
    frac = 1.0
    gross_r = 0.0
    fills = 1                      # giriş dolumu
    tp1_done = False
    mfe_r = mae_r = 0.0
    exit_reason, held = "horizon", 0
    tp1_px = (r_to_px(policy.tp1_at_r) if policy.tp1_at_r is not None
              else (targets[0] if len(targets) >= 2 else None))
    final_target = targets[-1] if targets else None

    for i, b in enumerate(bars):
        held = i + 1
        hi_r, lo_r = to_r(b["high"]), to_r(b["low"])
        bar_max_r, bar_min_r = (max(hi_r, lo_r), min(hi_r, lo_r))
        mfe_r, mae_r = max(mfe_r, bar_max_r), min(mae_r, bar_min_r)
        # MUHAFAZAKÂR SIRA: aynı barda önce stop, sonra hedef (iyimser doldurma yok).
        stop_r = to_r(cur_stop)
        if bar_min_r <= stop_r:
            gross_r += frac * stop_r
            fills += 1
            exit_reason = "breakeven_stop" if (tp1_done and abs(stop_r) < 1e-9) else "stop"
            frac = 0.0
            break
        if policy.tp1_fraction > 0 and not tp1_done and tp1_px is not None:
            t1_r = to_r(tp1_px)
            if bar_max_r >= t1_r:
                gross_r += policy.tp1_fraction * t1_r
                frac -= policy.tp1_fraction
                fills += 1
                tp1_done = True
                if policy.breakeven_after_tp1:
                    cur_stop = entry
        if final_target is not None:
            tgt_r = to_r(final_target)
            if bar_max_r >= tgt_r and (tp1_done or policy.tp1_fraction <= 0 or len(targets) == 1):
                gross_r += frac * tgt_r
                fills += 1
                exit_reason = "target"
                frac = 0.0
                break
        if policy.trail_activate_r is not None and mfe_r >= policy.trail_activate_r:
            trail_r = mfe_r - policy.trail_distance_r
            if to_r(cur_stop) < trail_r:
                cur_stop = r_to_px(trail_r)
        if policy.time_stop_bars is not None and held >= policy.time_stop_bars:
            close_r = to_r(b["close"])
            if close_r < policy.time_stop_min_r:
                gross_r += frac * close_r
                fills += 1
                exit_reason = "time_stop"
                frac = 0.0
                break
    if frac > 0:
        gross_r += frac * to_r(bars[-1]["close"])
        fills += 1
    net_r = gross_r - abs(float(cost_per_fill_r)) * fills
    return {"policy": policy.name, "gross_r": round(gross_r, 4), "net_r": round(net_r, 4),
            "fills": fills, "cost_r": round(abs(float(cost_per_fill_r)) * fills, 4),
            "exit_reason": exit_reason, "bars_held": held,
            "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4),
            "capture_ratio": (round(net_r / mfe_r, 4) if mfe_r > 0 else None),
            "trade_id": trade.get("id") or trade.get("trade_id")}


@dataclass
class ExitComparison:
    champion: dict[str, Any] = field(default_factory=dict)
    challengers: list[dict[str, Any]] = field(default_factory=list)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from .attribution import group_metrics
    return group_metrics([{"r_multiple": r["net_r"], "symbol": r.get("symbol"),
                           "id": r.get("trade_id")} for r in rows], min_sample=1)


def compare_exit_policies(trades: Iterable[dict[str, Any]], *,
                          challengers: Iterable[ExitPolicy] | None = None,
                          cost_per_fill_r: float = 0.0,
                          champion: ExitPolicy = CHAMPION_POLICY) -> dict[str, Any]:
    """Champion + challenger'ları AYNI işlemler, AYNI barlar, AYNI maliyetle karşılaştırır.

    Aktif politikayı DEĞİŞTİRMEZ. Terfi önerisi bu modülde ÜRETİLMEZ.
    """
    pols = list(challengers if challengers is not None else DEFAULT_CHALLENGERS)
    if len(pols) > MAX_CHALLENGERS:
        raise ValueError(f"en fazla {MAX_CHALLENGERS} challenger — grid araması yasak")
    key = cost_model_key(cost_per_fill_r=cost_per_fill_r)
    rows = [t for t in trades if isinstance(t, dict)]
    per_policy: dict[str, list[dict[str, Any]]] = {champion.name: []}
    for p in pols:
        per_policy[p.name] = []
    skipped = 0
    for t in rows:
        base = simulate_exit(t, champion, cost_per_fill_r=cost_per_fill_r)
        if base is None:
            skipped += 1
            continue
        base["symbol"] = t.get("symbol")
        per_policy[champion.name].append(base)
        for p in pols:
            sim = simulate_exit(t, p, cost_per_fill_r=cost_per_fill_r)
            if sim is None:
                continue
            sim["symbol"] = t.get("symbol")
            sim["champion_net_r"] = base["net_r"]
            sim["champion_mfe_r"] = base["mfe_r"]
            per_policy[p.name].append(sim)

    champ_rows = per_policy[champion.name]
    champ_metrics = _metrics(champ_rows) if champ_rows else {}
    by_id = {r["trade_id"]: r for r in champ_rows}
    out_ch = []
    for p in pols:
        rws = per_policy[p.name]
        m = _metrics(rws) if rws else {}
        high_mfe_delta, big_cut = [], []
        for r in rws:
            b = by_id.get(r["trade_id"])
            if b is None:
                continue
            if b["mfe_r"] >= HIGH_MFE_R and b["exit_reason"] in ("stop", "breakeven_stop"):
                high_mfe_delta.append(r["net_r"] - b["net_r"])
            if b["net_r"] >= BIG_WINNER_R:
                big_cut.append(r["net_r"] - b["net_r"])
        out_ch.append({
            "policy": p.name, "params": p.to_dict(), "n": len(rws), "metrics": m,
            "delta_expectancy_r": (round((m.get("expectancy_r") or 0) - (champ_metrics.get("expectancy_r") or 0), 4)
                                   if m and champ_metrics else None),
            "high_mfe_stop_rescue": {
                "n": len(high_mfe_delta),
                "mean_delta_r": (round(sum(high_mfe_delta) / len(high_mfe_delta), 4) if high_mfe_delta else None)},
            "big_winner_truncation": {
                "n": len(big_cut),
                "mean_delta_r": (round(sum(big_cut) / len(big_cut), 4) if big_cut else None),
                "truncates_winners": bool(big_cut and sum(big_cut) < 0)},
        })
    return {"schema_version": SCHEMA_VERSION, "cost_model_key": key,
            "cost_per_fill_r": abs(float(cost_per_fill_r)),
            "champion": {"policy": champion.name, "params": champion.to_dict(),
                         "n": len(champ_rows), "metrics": champ_metrics},
            "challengers": out_ch, "skipped_no_data": skipped,
            "active_policy_changed": False,
            "label": "OFFLINE RESEARCH — aktif çıkış politikası DEĞİŞMEDİ"}


def assert_same_cost_model(*reports: dict[str, Any]) -> None:
    """Farklı maliyet varsayımıyla üretilmiş raporlar KARŞILAŞTIRILAMAZ."""
    keys = {str(r.get("cost_model_key")) for r in reports if isinstance(r, dict)}
    if len(keys) > 1:
        raise ValueError(f"maliyet modeli uyuşmuyor: {sorted(keys)} — karşılaştırma reddedildi")

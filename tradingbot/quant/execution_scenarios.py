"""Execution maliyeti senaryoları ve veri kaynağı sınıflandırması (`quant_exec_scenarios_v1`).

Historical bid/ask veya order-book verisi YOKKEN sahte kesinlik üretmemek için her maliyet
bileşeninin kaynağı açıkça sınıflandırılır:

* `OBSERVED`    — gerçekten gözlemlenmiş veriden (ör. borsanın yayımladığı funding oranı)
* `MODELED`     — gözlemlenen OHLCV'den TÜRETİLMİŞ (ör. hacim katılımına bağlı etki)
* `FALLBACK`    — veri yok, konservatif sabit varsayım
* `UNAVAILABLE` — ne veri ne makul model var → hesap yapılmaz, alan `None` kalır

Üç senaryo desteklenir: `base` (mevcut konservatif varsayımlar), `adverse` (geniş spread, yüksek
slippage, gecikmeli fill etkisi) ve `stress` (likidite düşüşü, spread sıçraması, gap, yüksek
funding). Parametreler senaryo sırasına göre MONOTON artar; dolayısıyla daha kötü execution
hiçbir girdide daha iyi PnL üretemez.

LATENCY: bar verisiyle milisaniyelik fill iddiası YAPILMAZ. Gecikme yalnız "bar kesri" cinsinden
bir yaklaşıklıktır (`latency_bars`) ve ek aleyhte fiyat kayması olarak modellenir; kullanılan
yaklaşıklık `manifest` üzerinden raporlanır.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

SCHEMA_VERSION = "quant_exec_scenarios_v1"

OBSERVED, MODELED, FALLBACK, UNAVAILABLE = "OBSERVED", "MODELED", "FALLBACK", "UNAVAILABLE"

#: Modellenen maliyet bileşenlerinin üst sınırı (bps) — model sınırsız büyümez.
MAX_COMPONENT_BPS = 500.0


@dataclass(frozen=True)
class CostScenario:
    """Tek senaryonun maliyet varsayımları. Bütün alanlar senaryo sırasında monoton artar."""
    name: str
    spread_bps: float                    # tam spread (yarısı tek yönde ödenir)
    base_slippage_bps: float             # emir tipinden bağımsız taban kayma
    impact_coef_bps: float               # hacim katılımına bağlı etki katsayısı
    vol_spread_coef: float               # volatilite → spread genişlemesi
    latency_bars: float                  # bar kesri cinsinden gecikme YAKLAŞIKLIĞI (ms iddiası yok)
    latency_bps_per_bar: float           # gecikmenin aleyhte fiyat karşılığı
    funding_multiplier: float
    gap_extra_bps: float                 # stop ötesi gap için ek konservatif maliyet
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _prov(spread: str) -> dict[str, str]:
    return {"fee": OBSERVED, "funding": OBSERVED, "spread": spread,
            "slippage": MODELED, "impact": MODELED, "latency": FALLBACK,
            "historical_bid_ask": UNAVAILABLE, "order_book": UNAVAILABLE}


BASE = CostScenario("base", spread_bps=2.0, base_slippage_bps=3.0, impact_coef_bps=20.0,
                    vol_spread_coef=0.30, latency_bars=0.10, latency_bps_per_bar=5.0,
                    funding_multiplier=1.0, gap_extra_bps=0.0, provenance=_prov(FALLBACK))
ADVERSE = CostScenario("adverse", spread_bps=8.0, base_slippage_bps=8.0, impact_coef_bps=60.0,
                       vol_spread_coef=0.80, latency_bars=0.35, latency_bps_per_bar=12.0,
                       funding_multiplier=1.5, gap_extra_bps=5.0, provenance=_prov(FALLBACK))
STRESS = CostScenario("stress", spread_bps=25.0, base_slippage_bps=20.0, impact_coef_bps=150.0,
                      vol_spread_coef=2.00, latency_bars=1.00, latency_bps_per_bar=30.0,
                      funding_multiplier=3.0, gap_extra_bps=25.0, provenance=_prov(FALLBACK))

SCENARIOS: dict[str, CostScenario] = {s.name: s for s in (BASE, ADVERSE, STRESS)}
SCENARIO_ORDER = ("base", "adverse", "stress")


def _pos(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or v < 0:
        return default
    return v


def one_way_cost_bps(scenario: CostScenario, *, notional_usdt: float,
                     bar_quote_volume: float | None = None,
                     volatility_pct: float | None = None,
                     gap: bool = False) -> dict[str, Any]:
    """Tek yön (giriş veya çıkış) için modellenen maliyet (bps) ve bileşen dökümü.

    Monotonluk garantileri (testlerle sabitlenir):
    * `notional_usdt` arttıkça etki bileşeni AZALAMAZ,
    * `volatility_pct` arttıkça spread bileşeni AZALAMAZ,
    * senaryo base→adverse→stress ilerledikçe toplam AZALAMAZ.
    Her bileşen `MAX_COMPONENT_BPS` ile sınırlıdır → model sınırsız büyümez.
    """
    notional = _pos(notional_usdt)
    vol = _pos(volatility_pct)
    half_spread = min(MAX_COMPONENT_BPS,
                      scenario.spread_bps * (1.0 + scenario.vol_spread_coef * vol) / 2.0)
    if bar_quote_volume is None or _pos(bar_quote_volume) <= 0:
        # Hacim bilinmiyor → katılım hesaplanamaz; konservatif olarak TAM katsayı uygulanır
        # (iyimser sıfır etki YOK) ve kaynak FALLBACK olarak işaretlenir.
        impact = min(MAX_COMPONENT_BPS, scenario.impact_coef_bps)
        impact_src = FALLBACK
    else:
        participation = min(1.0, notional / _pos(bar_quote_volume, 1.0))
        impact = min(MAX_COMPONENT_BPS, scenario.impact_coef_bps * math.sqrt(participation))
        impact_src = MODELED
    latency = min(MAX_COMPONENT_BPS, scenario.latency_bars * scenario.latency_bps_per_bar)
    gap_extra = scenario.gap_extra_bps if gap else 0.0
    total = half_spread + scenario.base_slippage_bps + impact + latency + gap_extra
    return {"total_bps": round(total, 6),
            "components_bps": {"half_spread": round(half_spread, 6),
                               "base_slippage": round(scenario.base_slippage_bps, 6),
                               "impact": round(impact, 6), "latency": round(latency, 6),
                               "gap_extra": round(gap_extra, 6)},
            "provenance": {**scenario.provenance, "impact": impact_src},
            "latency_note": f"~{scenario.latency_bars} bar yaklaşıklığı — milisaniye iddiası YOK"}


def apply_scenario(trades: Iterable[dict[str, Any]], scenario: CostScenario) -> dict[str, Any]:
    """Kapanmış işlemlere senaryo maliyetlerini uygular ve maliyet-sonrası metrikleri döndürür.

    `trades` alanları: `gross_pnl`, `notional`, `risk_usdt` (R hesabı için), opsiyonel
    `bar_quote_volume`, `volatility_pct`, `funding`, `fees`, `gap` (stop ötesi gap oldu mu).
    Hesaplanamayan alan uydurulmaz; ilgili işlem `skipped` sayılır.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    for t in trades:
        gross = t.get("gross_pnl")
        notional = t.get("notional")
        risk = t.get("risk_usdt")
        if not isinstance(gross, (int, float)) or not math.isfinite(float(gross)) or \
           not isinstance(notional, (int, float)) or _pos(notional) <= 0:
            skipped += 1
            continue
        one_way = one_way_cost_bps(scenario, notional_usdt=float(notional),
                                   bar_quote_volume=t.get("bar_quote_volume"),
                                   volatility_pct=t.get("volatility_pct"),
                                   gap=bool(t.get("gap")))
        exec_cost = float(notional) * one_way["total_bps"] / 10_000.0 * 2.0   # giriş + çıkış
        fees = _pos(t.get("fees"))
        funding_raw = t.get("funding")
        funding = float(funding_raw) if isinstance(funding_raw, (int, float)) and math.isfinite(float(funding_raw)) else 0.0
        funding_scaled = funding * scenario.funding_multiplier
        net = float(gross) - fees - exec_cost + funding_scaled
        r = (net / float(risk)) if isinstance(risk, (int, float)) and _pos(risk) > 0 else None
        rows.append({"symbol": t.get("symbol"), "gross_pnl": float(gross), "fees": fees,
                     "funding": round(funding_scaled, 6), "exec_cost": round(exec_cost, 6),
                     "net_pnl": round(net, 6), "r_multiple": round(r, 6) if r is not None else None,
                     "cost_bps_one_way": one_way["total_bps"]})
    nets = [r["net_pnl"] for r in rows]
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    peak = cum = 0.0
    dd = 0.0
    for v in rs:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {"scenario": scenario.name, "n": len(rows), "n_skipped": skipped,
            "net_pnl_usdt": round(sum(nets), 6) if nets else None,
            "expectancy_usdt": round(sum(nets) / len(nets), 6) if nets else None,
            "expectancy_r": round(sum(rs) / len(rs), 6) if rs else None,
            "max_drawdown_r": round(dd, 6) if rs else None,
            "total_exec_cost_usdt": round(sum(r["exec_cost"] for r in rows), 6) if rows else None,
            "total_fees_usdt": round(sum(r["fees"] for r in rows), 6) if rows else None,
            "total_funding_usdt": round(sum(r["funding"] for r in rows), 6) if rows else None,
            "provenance": dict(scenario.provenance),
            "assumptions": scenario.to_dict()}


def compare_scenarios(trades: Iterable[dict[str, Any]],
                      scenarios: Iterable[CostScenario] | None = None) -> dict[str, Any]:
    """base/adverse/stress karşılaştırması + maliyet hassasiyeti + avantajın kaybolduğu senaryo."""
    trades = list(trades)
    scs = list(scenarios) if scenarios else [SCENARIOS[n] for n in SCENARIO_ORDER]
    results = {s.name: apply_scenario(trades, s) for s in scs}
    exps = {n: results[n].get("expectancy_r") for n in results}
    positive = {n: (e is not None and e > 0) for n, e in exps.items()}
    robust = all(positive.values()) if all(e is not None for e in exps.values()) else None
    lost = [n for n, ok in positive.items() if not ok]
    base_e, stress_e = exps.get("base"), exps.get("stress")
    sensitivity = None
    if base_e is not None and stress_e is not None and base_e != 0:
        sensitivity = round((base_e - stress_e) / abs(base_e), 6)
    if robust is True:
        verdict = "bütün senaryolarda pozitif expectancy"
    elif robust is False:
        verdict = f"avantaj şu senaryolarda kayboluyor: {', '.join(sorted(lost))}"
    else:
        verdict = "senaryo karşılaştırması hesaplanamadı (yetersiz veri)"
    return {"schema_version": SCHEMA_VERSION, "n_trades": len(trades),
            "results": results,
            "expectancy_r_by_scenario": exps,
            "robust_across_scenarios": robust,
            "advantage_lost_in": sorted(lost) if robust is not None else None,
            "cost_sensitivity_base_to_stress": sensitivity,
            "verdict": verdict,
            "latency_model": "bar-kesri yaklaşıklığı — milisaniyelik fill iddiası YOK",
            "label": "TEST DATA / RESEARCH — kârlılık kanıtı değildir"}

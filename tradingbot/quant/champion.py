"""Champion–challenger kapı değerlendirmesi (`quant_champion_v1`) — YALNIZ araştırma önerisi.

Champion mevcut `2113f7e` davranışıdır. Bu modül hiçbir şeyi terfi ETTİRMEZ; emir/config/policy
değiştirmez. Çıktı üç karardan biridir ve varsayılan `KEEP_CHAMPION`'dır:
* `REJECT_CHALLENGER` — leakage/veri kalitesi başarısız ya da challenger izolasyonu kanıtlanamadı,
* `KEEP_CHAMPION`     — yetersiz örnek/kanıt veya herhangi bir kapı geçilemedi (varsayılan),
* `PROMOTE_CANDIDATE` — BÜTÜN kapılar geçildi; yine de yalnız operatöre öneridir, otomatik
  terfi yolu yoktur (config'te `auto_promotion=true` ConfigError'dur).

Metrik sözlükleri `attribution.group_metrics` çıktısıyla uyumludur; challenger, champion ile AYNI
maliyet varsayımlarıyla (aynı replay/manifest) üretilmiş olmalıdır — manifest hash'leri farklıysa
karşılaştırma reddedilir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "quant_champion_v1"

PROMOTE_CANDIDATE = "PROMOTE_CANDIDATE"
KEEP_CHAMPION = "KEEP_CHAMPION"
REJECT_CHALLENGER = "REJECT_CHALLENGER"


@dataclass
class PromotionGates:
    """Terfi önerisi kapıları — hepsi geçilmeden PROMOTE önerilmez."""
    min_samples: int = 100
    min_oos_expectancy_r: float = 0.0          # maliyet sonrası OOS beklentisi pozitif olmalı
    min_delta_expectancy_r: float = 0.05       # champion'a anlamlı fark
    require_ci_low_above: float = 0.0          # bootstrap CI alt sınırı bu değerin üstünde olmalı
    max_drawdown_worse_ratio: float = 1.2      # challenger DD, champion DD'nin en fazla 1.2 katı
    min_fold_consistency: float = 0.6
    max_symbol_share: float = 0.5
    max_trade_share: float = 0.3
    max_tail_worse_ratio: float = 1.5          # CVaR5 champion'dan en fazla 1.5 kat kötü


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def evaluate_challenger(champion: dict[str, Any], challenger: dict[str, Any], *,
                        gates: PromotionGates | None = None,
                        leakage_passed: bool | None = None,
                        data_quality_passed: bool | None = None,
                        isolation_verified: bool | None = None,
                        same_cost_model: bool | None = None,
                        fold_consistency: float | None = None,
                        pbo_state: str | None = None,
                        extra_hard_gates: dict[str, Any] | None = None,
                        extra_soft_gates: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kapı değerlendirmesi. Bilinmeyen/verilmemiş güvenlik kanıtı GEÇMİŞ sayılmaz (fail-closed).

    `extra_hard_gates` / `extra_soft_gates`: `{KOD: (passed, detay)}` ya da `{KOD: passed}`.
    Evidence bridge bunlarla senaryo dayanıklılığı, journal kapsaması gibi ek kapıları enjekte eder.
    VERİLMEYEN kapı hiç eklenmez → mevcut çağıranların sonucu değişmez (geriye uyumlu).
    """
    g = gates or PromotionGates()
    checks: list[dict[str, Any]] = []
    hard_fail = False

    def gate(code: str, ok: bool | None, detail: str, *, hard: bool = False) -> None:
        nonlocal hard_fail
        passed = bool(ok)
        checks.append({"code": code, "passed": passed, "detail": detail})
        if hard and ok is False:
            hard_fail = True

    def _unpack(v: Any) -> tuple[Any, str]:
        if isinstance(v, tuple) and len(v) == 2:
            return v[0], str(v[1])
        return v, ""

    # --- sert kapılar: başarısızlık = REJECT
    gate("LEAKAGE", leakage_passed, "walk-forward leakage denetimi", hard=True)
    gate("DATA_QUALITY", data_quality_passed, "veri kalitesi kapısı", hard=True)
    gate("ISOLATION", isolation_verified,
         "challenger ana ledger/outbox/gateway'e dokunmadı kanıtı", hard=True)
    gate("SAME_COST_MODEL", same_cost_model,
         "champion ile aynı maliyet varsayımları (manifest uyumu)", hard=True)
    for code, raw in sorted((extra_hard_gates or {}).items()):
        ok, detail = _unpack(raw)
        gate(str(code), ok, detail or "ek sert kapı", hard=True)
    if hard_fail:
        return _result(REJECT_CHALLENGER, checks,
                       "sert kapı başarısız — challenger reddedildi")

    n = challenger.get("n")
    if not isinstance(n, int) or n < g.min_samples or challenger.get("insufficient_sample"):
        checks.append({"code": "MIN_SAMPLES", "passed": False,
                       "detail": f"n={n} < {g.min_samples} — yetersiz örnekle terfi önerilmez"})
        return _result(KEEP_CHAMPION, checks, "yetersiz örnek — varsayılan champion korunur")
    checks.append({"code": "MIN_SAMPLES", "passed": True, "detail": f"n={n}"})

    ch_exp, cm_exp = _f(challenger.get("expectancy_r")), _f(champion.get("expectancy_r"))
    gate("OOS_EXPECTANCY", ch_exp is not None and ch_exp > g.min_oos_expectancy_r,
         f"challenger OOS expectancy_r={ch_exp}")
    gate("DELTA_VS_CHAMPION",
         ch_exp is not None and cm_exp is not None and (ch_exp - cm_exp) >= g.min_delta_expectancy_r,
         f"delta_r={None if ch_exp is None or cm_exp is None else round(ch_exp - cm_exp, 4)}"
         f" (eşik {g.min_delta_expectancy_r})")
    ci = challenger.get("bootstrap_ci_mean_r") or {}
    ci_low = _f(ci.get("low"))
    gate("CONFIDENCE_INTERVAL", ci.get("state") == "ok" and ci_low is not None and ci_low > g.require_ci_low_above,
         f"CI_low={ci_low} (state={ci.get('state')})")
    ch_dd, cm_dd = _f(challenger.get("max_drawdown_r")), _f(champion.get("max_drawdown_r"))
    dd_ok = ch_dd is not None and (cm_dd is None or cm_dd == 0 or abs(ch_dd) <= abs(cm_dd) * g.max_drawdown_worse_ratio)
    gate("MAX_DRAWDOWN", dd_ok, f"challenger_dd={ch_dd} champion_dd={cm_dd}")
    ch_tail, cm_tail = _f(challenger.get("tail_loss_r_cvar5")), _f(champion.get("tail_loss_r_cvar5"))
    tail_ok = ch_tail is not None and (cm_tail is None or cm_tail == 0 or abs(ch_tail) <= abs(cm_tail) * g.max_tail_worse_ratio)
    gate("TAIL_LOSS", tail_ok, f"challenger_cvar5={ch_tail} champion_cvar5={cm_tail}")
    gate("FOLD_CONSISTENCY", fold_consistency is not None and fold_consistency >= g.min_fold_consistency,
         f"fold_consistency={fold_consistency}")
    conc = challenger.get("concentration") or {}
    sym_share, trade_share = _f(conc.get("top_symbol_share")), _f(conc.get("top_trade_share"))
    gate("SYMBOL_CONCENTRATION", sym_share is not None and sym_share <= g.max_symbol_share,
         f"top_symbol_share={sym_share}")
    gate("TRADE_CONCENTRATION", trade_share is not None and trade_share <= g.max_trade_share,
         f"top_trade_share={trade_share}")
    for code, raw in sorted((extra_soft_gates or {}).items()):
        ok, detail = _unpack(raw)
        gate(str(code), ok, detail or "ek yumuşak kapı")
    if pbo_state not in ("ok",):
        checks.append({"code": "PBO_WARNING", "passed": True,
                       "detail": f"multiple-testing ölçüsü: {pbo_state or 'hesaplanamadı'} — "
                                 "uyarı olarak kaydedildi"})

    failed = [c["code"] for c in checks if not c["passed"]]
    if failed:
        return _result(KEEP_CHAMPION, checks, f"geçilemeyen kapılar: {', '.join(failed)}")
    return _result(PROMOTE_CANDIDATE, checks,
                   "bütün kapılar geçildi — yalnız operatöre öneri; otomatik terfi YOK")


def _result(decision: str, checks: list[dict], note: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "decision": decision, "note": note,
            "auto_promotion": False, "checks": checks,
            "label": "TEST DATA / RESEARCH — kârlılık kanıtı değildir"}

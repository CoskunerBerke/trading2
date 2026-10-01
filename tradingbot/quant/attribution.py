"""Çok boyutlu, maliyet-sonrası performans attribution (`quant_attribution_v1`).

İlkeler:
* Trade-bazlı metrikler ile zaman-bazlı metrikler AYRIDIR; burada yalnız trade-bazlı hesap yapılır
  (`basis="trade"`). Annualized Sharpe/Sortino zaman ölçeği doğrulanamadığı için ÜRETİLMEZ
  (`time_metrics: "not_computed_time_scale_unverified"`).
* Sıfıra bölme ve sonsuzluk güvenlidir: profit factor kayıpsız grupta `null` +
  `profit_factor_state="no_losses"` olur; JSON'a asla Infinity yazılmaz.
* Küçük örnek kesin sonuç üretmez: `n < min_sample` → `insufficient_sample=True`, yalnız sayım
  alanları raporlanır.
* Bootstrap deterministiktir (sabit seed'li kendi LCG'si) — aynı girdi aynı raporu üretir.
* Win rate TEK BAŞINA terfi ölçütü değildir; rapor expectancy/maliyet/tail odaklıdır ve belirli
  bir kazanma oranına optimize edilmez.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

SCHEMA_VERSION = "quant_attribution_v1"

#: Varsayılan gruplayıcı boyutlar — journal (`quant_journal_v1`) alan adlarıyla hizalı.
DIMENSIONS: dict[str, Callable[[dict], Any]] = {
    "symbol": lambda r: r.get("symbol"),
    "market_type": lambda r: r.get("market_type"),
    "direction": lambda r: r.get("direction"),
    "regime": lambda r: r.get("regime"),
    "timeframe": lambda r: r.get("timeframe"),
    "setup": lambda r: r.get("setup_id"),
    "specialist": lambda r: _top_specialist(r),
    "leverage": lambda r: _lev_bucket(r.get("planned_leverage")),
    "exit_reason": lambda r: r.get("exit_reason"),
    "hour_bucket": lambda r: _hour_bucket(r.get("event_ts_utc")),
    "policy": lambda r: r.get("policy_id"),
    "data_quality": lambda r: "flagged" if r.get("quality_flags") else "clean",
}


def _top_specialist(r: dict) -> str | None:
    sc = r.get("specialist_scores")
    if not isinstance(sc, dict) or not sc:
        return None
    try:
        return max(sc.items(), key=lambda kv: abs(float(kv[1] or 0)))[0]
    except (TypeError, ValueError):
        return None


def _lev_bucket(lev: Any) -> str | None:
    if lev is None:
        return None
    try:
        v = float(lev)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return "1x" if v < 2 else f"{int(v)}x"


def _hour_bucket(ts: Any) -> str | None:
    s = str(ts or "")
    if len(s) < 13 or "T" not in s:
        return None
    try:
        h = int(s.split("T")[1][:2])
    except ValueError:
        return None
    return f"{(h // 6) * 6:02d}-{(h // 6) * 6 + 6:02d}utc"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class _Lcg:
    """Deterministik küçük RNG (numpy'a bağımlılık yok; platformdan bağımsız aynı akış)."""

    def __init__(self, seed: int):
        self.s = (int(seed) * 2654435761 + 1) % (2 ** 31)

    def randint(self, n: int) -> int:
        self.s = (1103515245 * self.s + 12345) % (2 ** 31)
        return self.s % max(1, n)


def _bootstrap_ci(rs: list[float], *, iters: int = 500, seed: int = 7,
                  min_n: int = 20) -> dict[str, Any]:
    if len(rs) < min_n:
        return {"low": None, "high": None, "state": "insufficient_sample"}
    rng = _Lcg(seed)
    means = []
    n = len(rs)
    for _ in range(iters):
        means.append(sum(rs[rng.randint(n)] for _ in range(n)) / n)
    means.sort()
    return {"low": round(means[int(0.025 * iters)], 4), "high": round(means[int(0.975 * iters)], 4),
            "state": "ok", "iters": iters, "seed": seed}


def _max_drawdown_r(rs: list[float]) -> float:
    """Kronolojik R serisinin kümülatif tepe-çukur düşüşü (trade-bazlı; zaman-bazlı DEĞİL)."""
    peak = cum = 0.0
    dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 4)


def _brier(rows: list[dict]) -> dict[str, Any]:
    pairs = [(p, 1.0 if r.get("outcome_class") == "WIN" else 0.0)
             for r in rows if (p := _f(r.get("p_win"))) is not None and r.get("outcome_class") is not None]
    if len(pairs) < 10:
        return {"brier": None, "n": len(pairs), "state": "insufficient_sample"}
    return {"brier": round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4),
            "n": len(pairs), "state": "ok"}


def group_metrics(rows: list[dict], *, min_sample: int = 10, seed: int = 7) -> dict[str, Any]:
    """Tek grup için maliyet-sonrası metrik seti. `rows` outcome'u etiketli journal kayıtlarıdır."""
    n = len(rows)
    out: dict[str, Any] = {"n": n, "basis": "trade",
                           "time_metrics": "not_computed_time_scale_unverified"}
    if n < min_sample:
        out["insufficient_sample"] = True
        return out
    out["insufficient_sample"] = False
    rs = [v for r in rows if (v := _f(r.get("r_multiple"))) is not None]
    pnls = [v for r in rows if (v := _f(r.get("net_pnl"))) is not None]
    gross = [v for r in rows if (v := _f(r.get("gross_pnl"))) is not None]
    fees = [v for r in rows if (v := _f(r.get("fees"))) is not None]
    funding = [v for r in rows if (v := _f(r.get("funding"))) is not None]
    slip = [v for r in rows if (v := _f((r.get("cost_estimate") or {}).get("slippage_usdt"))) is not None]
    wins = [r for r in rs if r > 0.25]
    losses = [r for r in rs if r < -0.25]
    breakeven = len(rs) - len(wins) - len(losses)
    out.update({
        "wins": len(wins), "losses": len(losses), "breakeven": breakeven,
        "win_rate": round(len(wins) / len(rs), 4) if rs else None,
        "gross_pnl_usdt": round(sum(gross), 4) if gross else None,
        "net_pnl_usdt": round(sum(pnls), 4) if pnls else None,
        "fees_usdt": round(sum(fees), 4) if fees else None,
        "funding_usdt": round(sum(funding), 4) if funding else None,
        "slippage_usdt": round(sum(slip), 4) if slip else None,
        "mean_r": round(sum(rs) / len(rs), 4) if rs else None,
        "median_r": round(sorted(rs)[len(rs) // 2], 4) if rs else None,
        "expectancy_usdt": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else None,
        "max_drawdown_r": _max_drawdown_r(rs) if rs else None,
    })
    gp = sum(r for r in rs if r > 0)
    gl = -sum(r for r in rs if r < 0)
    if gl > 0:
        out["profit_factor"], out["profit_factor_state"] = round(gp / gl, 4), "ok"
    elif gp > 0:
        out["profit_factor"], out["profit_factor_state"] = None, "no_losses"
    else:
        out["profit_factor"], out["profit_factor_state"] = None, "no_profit_no_loss"
    aw, al = out["avg_win_r"], out["avg_loss_r"]
    out["payoff_ratio"] = round(aw / abs(al), 4) if (aw is not None and al) else None
    maes = [v for r in rows if (v := _f(r.get("mae_pct"))) is not None]
    mfes = [v for r in rows if (v := _f(r.get("mfe_pct"))) is not None]
    out["mae_pct_mean"] = round(sum(maes) / len(maes), 4) if maes else None
    out["mfe_pct_mean"] = round(sum(mfes) / len(mfes), 4) if mfes else None
    holds = [v for r in rows if (v := _f(r.get("bars_held"))) is not None]
    out["holding_bars_mean"] = round(sum(holds) / len(holds), 2) if holds else None
    # tail: en kötü %5 R ortalaması (CVaR benzeri, trade-bazlı)
    if len(rs) >= 20:
        k = max(1, len(rs) // 20)
        out["tail_loss_r_cvar5"] = round(sum(sorted(rs)[:k]) / k, 4)
    else:
        out["tail_loss_r_cvar5"] = None
    out["bootstrap_ci_mean_r"] = _bootstrap_ci(rs, seed=seed)
    out["calibration"] = _brier(rows)
    # yoğunlaşma: tek sembol / tek trade kâr payı
    if pnls and sum(p for p in pnls if p > 0) > 0:
        pos_total = sum(p for p in pnls if p > 0)
        by_sym: dict[str, float] = {}
        for r in rows:
            p = _f(r.get("net_pnl"))
            if p is not None and p > 0:
                by_sym[str(r.get("symbol"))] = by_sym.get(str(r.get("symbol")), 0.0) + p
        out["concentration"] = {"top_symbol_share": round(max(by_sym.values()) / pos_total, 4) if by_sym else None,
                                "top_trade_share": round(max(p for p in pnls if p > 0) / pos_total, 4)}
    else:
        out["concentration"] = {"top_symbol_share": None, "top_trade_share": None}
    return out


def attribution_v1(rows: Iterable[dict], *, dims: Iterable[str] | None = None,
                   min_sample: int = 10, seed: int = 7) -> dict[str, Any]:
    """Etiketli journal kayıtları → boyut bazında maliyet-sonrası rapor (deterministik).

    Gerçek fill'ler (`is_counterfactual=False`) ile counterfactual shadow etiketleri AYRI
    havuzlarda raporlanır; ikisi asla tek istatistikte karıştırılmaz.
    """
    rows = [r for r in rows if r.get("outcome_labeled")]
    real = [r for r in rows if not r.get("is_counterfactual")]
    shadow = [r for r in rows if r.get("is_counterfactual")]
    use_dims = list(dims) if dims else list(DIMENSIONS)
    unknown = [d for d in use_dims if d not in DIMENSIONS]
    if unknown:
        raise ValueError(f"bilinmeyen attribution boyutu: {unknown}")

    def _by_dim(pool: list[dict]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for d in use_dims:
            key_fn = DIMENSIONS[d]
            groups: dict[str, list[dict]] = {}
            for r in pool:
                k = key_fn(r)
                if k is None:
                    k = "(bilinmiyor)"
                groups.setdefault(str(k), []).append(r)
            res[d] = {k: group_metrics(v, min_sample=min_sample, seed=seed)
                      for k, v in sorted(groups.items())}
        return res

    return {"schema_version": SCHEMA_VERSION, "seed": seed, "min_sample": min_sample,
            "n_input": len(rows), "n_real": len(real), "n_counterfactual": len(shadow),
            "overall_real": group_metrics(real, min_sample=min_sample, seed=seed),
            "overall_counterfactual": group_metrics(shadow, min_sample=min_sample, seed=seed),
            "by_dimension_real": _by_dim(real),
            "by_dimension_counterfactual": _by_dim(shadow)}


def render_text(report: dict[str, Any]) -> str:
    """İnsan okunur kısa özet (deterministik). Kârlılık kanıtı DEĞİL; araştırma çıktısıdır."""
    lines = [f"QUANT ATTRIBUTION ({report.get('schema_version')})  seed={report.get('seed')}",
             f"girdi: {report.get('n_input')} etiketli kayıt "
             f"({report.get('n_real')} gerçek, {report.get('n_counterfactual')} counterfactual)"]
    ov = report.get("overall_real") or {}
    if ov.get("insufficient_sample"):
        lines.append(f"GENEL (gerçek): n={ov.get('n')} — insufficient_sample, metrik üretilmedi")
    else:
        pf = ov.get("profit_factor")
        lines.append(f"GENEL (gerçek): n={ov.get('n')} exp_R={ov.get('expectancy_r')} "
                     f"exp_USDT={ov.get('expectancy_usdt')} winrate={ov.get('win_rate')} "
                     f"PF={'∞(no_losses)' if ov.get('profit_factor_state') == 'no_losses' else pf} "
                     f"maxDD_R={ov.get('max_drawdown_r')} CVaR5_R={ov.get('tail_loss_r_cvar5')}")
    for dim, groups in (report.get("by_dimension_real") or {}).items():
        strong = {k: v for k, v in groups.items() if not v.get("insufficient_sample")}
        if not strong:
            continue
        lines.append(f"- {dim}:")
        for k, v in strong.items():
            lines.append(f"    {k}: n={v['n']} exp_R={v.get('expectancy_r')} "
                         f"net={v.get('net_pnl_usdt')} USDT dd_R={v.get('max_drawdown_r')}")
    lines.append("NOT: zaman-bazlı (annualized) metrik üretilmedi; counterfactual satırlar gerçek fill değildir.")
    return "\n".join(lines)

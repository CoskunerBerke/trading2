"""Çıkış politikası karşı-olgusal değerlendirmesi (`exit_eval_v1`).

Aynı GERÇEK fiyat yolu üzerinde champion ile challenger'ları karşılaştırır. Simülasyon
uydurulmuş fiyat üretmez: yalnız `position_path.jsonl` içindeki gerçekten gözlenmiş
snapshot'ları kullanır.

**No-lookahead sözleşmesi.** Her karar YALNIZ o snapshot'a kadar görülmüş bilgiyle verilir.
`replay_policy` snapshot'ları kronolojik sırayla işler ve gelecekteki hiçbir alana bakmaz;
sonucu gördükten sonra geçmiş bir kararı DEĞİŞTİRMEZ. Kapanış sonucu yalnız en sonda,
politikanın kendi çıkış noktası belirlendikten sonra okunur.

**Eksik yol dürüstlüğü.** Bir işlemin tam yolu yoksa challenger sonucu ÜRETİLMEZ; kayıt
`NO_COMPLETE_PATH` taşır. Defterdeki `mfe_pct`/`mae_pct` özetinden sahte fiyat yolu
türetmek yasaktır: o iki sayı ne zaman ne de sırayla ilgili bilgi taşımaz.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from ..core import iso, utc_now
from .exit_policy import (CHALLENGER_A, CHALLENGER_B, CHALLENGER_C, CHAMPION, EXIT, HOLD,
                          REDUCE, TIGHTEN_STOP, ExitPolicyConfig, evaluate_all)
from .position_path import path_completeness, side_sign

SCHEMA_VERSION = "exit_eval_v1"

NO_COMPLETE_PATH = "NO_COMPLETE_PATH"
INSUFFICIENT_EXIT_SAMPLE = "INSUFFICIENT_EXIT_SAMPLE"
ELIGIBLE_FOR_PAPER_BOUNDED = "ELIGIBLE_FOR_PAPER_BOUNDED"

#: Terfi kapıları — hiçbiri bu görevde otomatik AÇILMAZ.
GATE_MIN_CLOSED = 50
GATE_MIN_DAYS = 30


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _cost_r(snap: dict[str, Any], *, fee_rate: float, slip_rate: float,
            fraction: float = 1.0) -> float:
    """Bir çıkış işleminin R cinsinden maliyeti (komisyon + kayma), kapatılan orana göre."""
    risk = _f(snap.get("initial_risk_usdt"))
    qty, mark = _f(snap.get("qty")), _f(snap.get("mark"))
    if not risk or qty is None or mark is None:
        return 0.0
    notional = abs(qty * mark) * max(0.0, min(1.0, fraction))
    return (notional * (fee_rate + slip_rate)) / risk


def replay_policy(path: list[dict[str, Any]], policy: str, cfg: ExitPolicyConfig, *,
                  final_r: float | None, fee_rate: float = 0.0005,
                  slip_rate: float = 0.0003) -> dict[str, Any]:
    """Tek politikayı gerçek yol üzerinde kronolojik oynatır.

    Dönen kayıt politikanın çıkış noktasını, gerçekleşen R'sini ve maliyetlerini taşır.
    `final_r` gerçek defter kapanışının R'sidir ve YALNIZ politika kendi çıkışını üretmediğinde
    (yani pozisyon champion'ın stop/TP'siyle kapandığında) kullanılır.
    """
    rows = sorted(path, key=lambda x: int(x.get("ts_ms") or 0))
    actions: list[dict[str, Any]] = []
    reduces_done = 0
    locked_stop_r: float | None = None
    realized_r = 0.0                 # kısmi azaltmalardan KİLİTLENEN R
    remaining = 1.0                  # kalan pozisyon oranı
    exit_ts = exit_reason = None
    exit_r: float | None = None
    cost_r = 0.0
    last_seen_r: float | None = None
    for snap in rows:
        cur_r = _f(snap.get("gross_r"))
        if cur_r is not None:
            last_seen_r = cur_r
        # --- KİLİTLİ STOP KONTROLÜ (bu snapshot'a kadarki bilgiyle) ---------------------
        # Stop kilitlendiyse ve fiyat o seviyeye geldiyse politika ORADA çıkar. Gelecek
        # snapshot'lara bakılmaz.
        if locked_stop_r is not None and cur_r is not None and remaining > 0 \
                and cur_r <= locked_stop_r:
            c = _cost_r(snap, fee_rate=fee_rate, slip_rate=slip_rate, fraction=remaining)
            realized_r += locked_stop_r * remaining
            cost_r += c
            exit_ts, exit_reason = snap.get("ts"), "PROFIT_LOCK_STOP"
            remaining = 0.0
            break
        decs = evaluate_all(snap, cfg, reduces_done=reduces_done)
        d = decs.get(policy)
        if d is None or d["action"] == HOLD:
            continue
        actions.append(d)
        if d["action"] == TIGHTEN_STOP:
            lr = _f(d.get("locked_r"))
            if lr is not None and (locked_stop_r is None or lr > locked_stop_r):
                locked_stop_r = lr          # stop yalnız SIKILAŞIR
        elif d["action"] == REDUCE and remaining > 0:
            frac = _f(d.get("reduce_fraction")) or 0.0
            closed_now = remaining * frac
            c = _cost_r(snap, fee_rate=fee_rate, slip_rate=slip_rate, fraction=closed_now)
            realized_r += (cur_r or 0.0) * closed_now
            cost_r += c
            remaining -= closed_now
            reduces_done += 1
        elif d["action"] == EXIT and remaining > 0:
            c = _cost_r(snap, fee_rate=fee_rate, slip_rate=slip_rate, fraction=remaining)
            realized_r += (cur_r or 0.0) * remaining
            cost_r += c
            exit_ts, exit_reason = snap.get("ts"), "POLICY_EXIT"
            remaining = 0.0
            break
    # Kalan pozisyon champion'ın gerçek çıkışıyla (stop/TP/likidasyon) kapanır.
    if remaining > 0:
        base = final_r if final_r is not None else last_seen_r
        if base is None:
            return {"policy_id": policy, "status": NO_COMPLETE_PATH,
                    "reason": "FINAL_R_UNKNOWN", "n_actions": len(actions)}
        realized_r += base * remaining
        exit_reason = exit_reason or "LEDGER_EXIT"
        exit_ts = exit_ts or (rows[-1].get("ts") if rows else None)
    net_r = realized_r - cost_r
    mfe_r = max((_f(r.get("mfe_r")) or 0.0) for r in rows) if rows else 0.0
    return {
        "policy_id": policy,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "status": "OK",
        "n_snapshots": len(rows),
        "n_actions": len(actions),
        "actions": actions,
        "exit_ts": exit_ts,
        "exit_reason": exit_reason,
        "gross_r": round(realized_r, 6),
        "exit_cost_r": round(cost_r, 6),
        "net_r": round(net_r, 6),
        "mfe_r": round(mfe_r, 6),
        "captured_r": round(net_r, 6),
        "giveback_r": round(mfe_r - net_r, 6),
        "capture_ratio": (round(net_r / mfe_r, 6) if mfe_r > 1e-9 else None),
        "capture_ratio_state": "OK" if mfe_r > 1e-9 else "NO_FAVORABLE_EXCURSION",
        "reduces": reduces_done,
        "locked_stop_r": locked_stop_r,
    }


def evaluate_trade(*, trade_id: str, path: list[dict[str, Any]], close: dict[str, Any],
                   cfg: ExitPolicyConfig, fee_rate: float = 0.0005,
                   slip_rate: float = 0.0003) -> dict[str, Any]:
    """Bir kapanmış işlem için champion ve challenger sonuçları.

    Yol eksikse hiçbir challenger sonucu üretilmez — sahte karşılaştırma yapmaktansa
    ölçemediğimizi söylemek doğrudur.
    """
    comp = path_completeness(path, opened_at=close.get("opened_at"),
                             closed_at=close.get("closed_at"))
    final_r = _f(close.get("r_multiple"))
    base = {
        "schema_version": SCHEMA_VERSION,
        "trade_id": str(trade_id),
        "symbol": close.get("symbol"),
        "side": close.get("side"),
        "closed_at": close.get("closed_at"),
        "exit_reason": close.get("exit_reason"),
        "actual_r": final_r,
        "actual_net_pnl": _f(close.get("net_pnl", close.get("pnl"))),
        "path": comp,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
    }
    if not comp["complete"]:
        base.update({"status": NO_COMPLETE_PATH, "results": {},
                     "note_tr": ("Tam fiyat yolu yok — challenger sonucu ÜRETİLMEDİ. "
                                 "Defterin MFE/MAE özetinden sahte yol türetilmez.")})
        return base
    results = {p: replay_policy(path, p, cfg, final_r=final_r, fee_rate=fee_rate,
                                slip_rate=slip_rate)
               for p in (CHAMPION, CHALLENGER_A, CHALLENGER_B, CHALLENGER_C)}
    ch = results.get(CHAMPION) or {}
    for p, r in results.items():
        if p == CHAMPION or r.get("status") != "OK" or ch.get("status") != "OK":
            continue
        d = (r.get("net_r") or 0.0) - (ch.get("net_r") or 0.0)
        r["delta_vs_champion_r"] = round(d, 6)
        # Erken çıkıp kaçırılan kazanç ve kaçınılan zarar AYRI raporlanır: ikisi aynı
        # sayının işaretleri değildir ve bir politikayı yalnız net farkla yargılamak yanıltır.
        r["missed_gain_r"] = round(max(0.0, -d), 6)
        r["avoided_loss_r"] = round(max(0.0, d), 6)
        r["fee_churn_r"] = round((r.get("exit_cost_r") or 0.0) - (ch.get("exit_cost_r") or 0.0), 6)
    base.update({"status": "OK", "results": results})
    return base


def _stats(values: list[float]) -> dict[str, Any]:
    """Bir R serisinin özeti. Boş seride sayı UYDURULMAZ."""
    n = len(values)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "profit_factor": None, "profit_factor_state": "no_data",
                "max_drawdown_r": None, "tail_loss_r_cvar5": None, "payoff_ratio": None,
                "win_rate": None}
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gp, gl = sum(wins), -sum(losses)
    if gl > 0:
        pf, pf_state = gp / gl, "ok"
    elif gp > 0:
        pf, pf_state = None, "no_losses"
    else:
        pf, pf_state = None, "no_data"
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for v in values:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    k = max(1, int(round(n * 0.05)))
    tail = sorted(values)[:k]
    return {
        "n": n,
        "expectancy_r": round(sum(values) / n, 6),
        "profit_factor": round(pf, 6) if pf is not None else None,
        "profit_factor_state": pf_state,
        "max_drawdown_r": round(dd, 6),
        "tail_loss_r_cvar5": round(sum(tail) / len(tail), 6) if tail else None,
        "payoff_ratio": (round((gp / len(wins)) / (gl / len(losses)), 6)
                         if wins and losses else None),
        "win_rate": round(len(wins) / n, 6),
    }


def aggregate(evaluations: Iterable[dict[str, Any]], *, cfg: ExitPolicyConfig,
              now=None) -> dict[str, Any]:
    """Bütün değerlendirmelerden politika bazlı özet + terfi kapısı durumu."""
    evs = [e for e in evaluations if isinstance(e, dict)]
    complete = [e for e in evs if e.get("status") == "OK"]
    incomplete = [e for e in evs if e.get("status") == NO_COMPLETE_PATH]
    by_policy: dict[str, list[float]] = {}
    conc_symbol: dict[str, int] = {}
    conc_side: dict[str, int] = {}
    conc_regime: dict[str, int] = {}
    fees: dict[str, float] = {}
    for e in complete:
        conc_symbol[str(e.get("symbol"))] = conc_symbol.get(str(e.get("symbol")), 0) + 1
        conc_side[str(e.get("side"))] = conc_side.get(str(e.get("side")), 0) + 1
        for p, r in (e.get("results") or {}).items():
            if r.get("status") != "OK":
                continue
            v = _f(r.get("net_r"))
            if v is not None:
                by_policy.setdefault(p, []).append(v)
            fees[p] = fees.get(p, 0.0) + (_f(r.get("exit_cost_r")) or 0.0)
    stats = {p: _stats(v) | {"total_exit_cost_r": round(fees.get(p, 0.0), 6)}
             for p, v in by_policy.items()}
    champ = stats.get(CHAMPION) or {}
    for p, s in stats.items():
        if p == CHAMPION:
            continue
        ce, pe = champ.get("expectancy_r"), s.get("expectancy_r")
        s["delta_expectancy_r"] = (round(pe - ce, 6) if (ce is not None and pe is not None) else None)
        s["fee_delta_r"] = round(s["total_exit_cost_r"] - (champ.get("total_exit_cost_r") or 0.0), 6)
    days = None
    ts = sorted(str(e.get("closed_at") or "") for e in complete if e.get("closed_at"))
    if len(ts) >= 2:
        try:
            from ..core import from_iso
            days = round((from_iso(ts[-1]) - from_iso(ts[0])).total_seconds() / 86400.0, 2)
        except (ValueError, TypeError):
            days = None
    n = len(complete)
    top_symbol = max(conc_symbol.values()) / n if (n and conc_symbol) else None
    gates = [
        {"code": "MIN_PATH_COMPLETE_CLOSES", "passed": n >= GATE_MIN_CLOSED,
         "detail": f"{n}/{GATE_MIN_CLOSED}"},
        {"code": "MIN_OBSERVATION_DAYS", "passed": bool(days is not None and days >= GATE_MIN_DAYS),
         "detail": f"{days}/{GATE_MIN_DAYS}" if days is not None else "ölçülemedi"},
        {"code": "WALK_FORWARD_EVIDENCE", "passed": False,
         "detail": "rolling walk-forward kanıtı sağlanmadı — 'bilinmiyor' geçti sayılmaz"},
        {"code": "SYMBOL_CONCENTRATION", "passed": bool(top_symbol is not None and top_symbol <= 0.5),
         "detail": (f"en yoğun sembol payı {top_symbol:.2f}" if top_symbol is not None else "ölçülemedi")},
        {"code": "CONFIDENCE_INTERVAL_EXCLUDES_ZERO", "passed": False,
         "detail": "güven aralığı hesaplanmadı — yetersiz örnek"},
    ]
    verdict = (ELIGIBLE_FOR_PAPER_BOUNDED if all(g["passed"] for g in gates)
               else INSUFFICIENT_EXIT_SAMPLE)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now or utc_now()),
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "n_evaluated": len(evs),
        "n_path_complete": n,
        "n_no_complete_path": len(incomplete),
        "no_complete_path_ids": sorted(str(e.get("trade_id")) for e in incomplete),
        "observation_days": days,
        "by_policy": stats,
        "concentration": {"symbol": conc_symbol, "side": conc_side, "regime": conc_regime,
                          "top_symbol_share": (round(top_symbol, 4) if top_symbol is not None else None)},
        "promotion_gates": gates,
        "verdict": verdict,
        "auto_promotion": False,
        "note_tr": ("Challenger otomatik AKTİFLEŞMEZ. Terfi yalnız bütün kapılar geçildikten "
                    "sonra açık operatör onayıyla değerlendirilir."),
    }


__all__ = ["SCHEMA_VERSION", "NO_COMPLETE_PATH", "INSUFFICIENT_EXIT_SAMPLE",
           "ELIGIBLE_FOR_PAPER_BOUNDED", "GATE_MIN_CLOSED", "GATE_MIN_DAYS",
           "replay_policy", "evaluate_trade", "aggregate"]

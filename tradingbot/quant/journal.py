"""Birleşik karar→sonuç günlüğü (`quant_journal_v1`) — kabul edilen (TradeMemory) ve açılmayan/
gölge (ShadowBook) adayları TEK şemada, sonraki outcome ile bağlanabilir biçimde birleştirir.

Tasarım ilkeleri:
* SALT OKUNUR JOINER: canlı state'i değiştirmez; `export_journal` yalnız çağıranın verdiği yola
  atomic yazar. Otomatik migration/backfill YOK.
* Geriye uyumluluk: eski memory/shadow kayıtlarındaki eksik alanlar `null` + availability flag
  olarak temsil edilir; okuma hiçbir eski kaydı bozmaz.
* RFC-JSON güvenliği: bare `NaN/Infinity/-Infinity` çıktıya ASLA yazılmaz — sonlu olmayan sayı
  `null` olur ve `quality_flags` içinde `NON_FINITE:<alan>` işaretlenir.
* Idempotency: kimlik deterministiktir (`stable_id`); aynı girdi aynı `decision_id`'yi üretir ve
  `unify` duplicate kimlikleri tekilleştirir.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from ..core import atomic_write_json, stable_id
from ..learn.labels import label_outcome

SCHEMA_VERSION = "quant_journal_v1"

#: Karar kaynağı türleri — replay/live-paper/shadow ayrımı analizde net tutulur.
SOURCE_KINDS = ("LIVE_PAPER", "HISTORICAL_REPLAY", "SHADOW", "TESTNET", "LIVE")


def _finite(x: Any, field: str, flags: list[str]) -> float | None:
    """Sonlu float ya da None. Sonlu olmayan değer null'a düşer ve bayraklanır (sessiz 0 YOK)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        flags.append(f"NON_FINITE:{field}")
        return None
    return v


def _s(x: Any) -> str | None:
    return None if x is None or x == "" else str(x)


def _availability(row: dict[str, Any], fields: Iterable[str]) -> dict[str, bool]:
    return {f: row.get(f) is not None for f in fields}


_CORE_FIELDS = ("p_win", "expected_r", "ensemble_score", "regime", "setup_id", "planned_entry",
                "planned_stop", "planned_leverage", "net_pnl", "r_multiple", "exit_reason")


def _base_record(*, source_kind: str, flags: list[str]) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        flags.append(f"UNKNOWN_SOURCE:{source_kind}")
    return {"schema_version": SCHEMA_VERSION, "source_kind": source_kind, "quality_flags": flags}


def row_from_memory(entry: dict[str, Any], exit_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """TradeMemory entry(+exit) → birleşik kayıt. Kabul edilmiş karardır (`accepted=True`)."""
    flags: list[str] = []
    src = str(entry.get("source") or "LIVE_PAPER")
    rec = _base_record(source_kind=src, flags=flags)
    plan = entry.get("plan") if isinstance(entry.get("plan"), dict) else {}
    decision = entry.get("decision") if isinstance(entry.get("decision"), dict) else {}
    trade_id = _s(entry.get("trade_id"))
    symbol = _s(entry.get("symbol") or plan.get("symbol"))
    direction = _s(entry.get("side") or entry.get("direction") or plan.get("direction"))
    rec.update({
        "decision_id": stable_id("qj", src, "memory", trade_id, symbol, direction),
        "candidate_id": _s(plan.get("plan_id") or plan.get("id") or entry.get("plan_id")),
        "event_ts_utc": _s(entry.get("decision_ts") or entry.get("recorded_at")),
        "as_of_ts_utc": _s(entry.get("last_bar_ts") or entry.get("as_of")),
        "symbol": symbol,
        "market_type": _s(entry.get("market_type") or plan.get("market_type")),
        "direction": direction,
        "timeframe": _s(entry.get("timeframe") or plan.get("timeframe")),
        "setup_id": _s(entry.get("setup") or plan.get("setup") or entry.get("strategy_version")),
        "regime": _s(entry.get("regime") or (entry.get("values") or {}).get("regime")),
        "feature_snapshot": entry.get("values") if isinstance(entry.get("values"), dict) else None,
        "specialist_scores": entry.get("agents") if isinstance(entry.get("agents"), dict) else None,
        "veto_results": decision.get("vetoes") if isinstance(decision.get("vetoes"), list) else None,
        "ensemble_score": _finite(decision.get("score") or entry.get("score"), "ensemble_score", flags),
        "p_win": _finite(entry.get("p_win") or decision.get("p_win"), "p_win", flags),
        "expected_r": _finite(entry.get("expected_r") or decision.get("expected_r"), "expected_r", flags),
        "accepted": True,
        "reject_reason": None,
        "planned_entry": _finite(plan.get("entry"), "planned_entry", flags),
        "planned_stop": _finite(plan.get("stop"), "planned_stop", flags),
        "planned_targets": [t for t in (plan.get("targets") or []) if isinstance(t, (int, float)) and math.isfinite(float(t))] or None,
        "planned_leverage": _finite(plan.get("leverage") or entry.get("leverage"), "planned_leverage", flags),
        "planned_notional": _finite(plan.get("notional"), "planned_notional", flags),
        "cost_estimate": entry.get("cost_estimate") if isinstance(entry.get("cost_estimate"), dict) else None,
        "policy_id": _s(entry.get("model_version") or entry.get("policy_id")) or "champion",
        "outcome_ref": trade_id,
        "variant": None,
        "is_counterfactual": False,
    })
    outcome = (exit_row or {}).get("outcome") if isinstance((exit_row or {}).get("outcome"), dict) else None
    if outcome is not None:
        lab = label_outcome(outcome)
        rec.update({
            "exit_reason": _s(outcome.get("exit_reason")),
            "gross_pnl": _finite(outcome.get("gross_pnl"), "gross_pnl", flags),
            "net_pnl": _finite(outcome.get("net_pnl", outcome.get("pnl")), "net_pnl", flags),
            "fees": _finite(outcome.get("fees"), "fees", flags),
            "funding": _finite(outcome.get("funding"), "funding", flags),
            "r_multiple": _finite(outcome.get("r_multiple"), "r_multiple", flags),
            "mae_pct": _finite(outcome.get("mae_pct"), "mae_pct", flags),
            "mfe_pct": _finite(outcome.get("mfe_pct"), "mfe_pct", flags),
            "bars_held": int(outcome.get("bars_held")) if isinstance(outcome.get("bars_held"), (int, float)) else None,
            "outcome_class": lab.get("outcome_class"),
            "outcome_labeled": True,
        })
    else:
        rec.update({"exit_reason": None, "gross_pnl": None, "net_pnl": None, "fees": None,
                    "funding": None, "r_multiple": None, "mae_pct": None, "mfe_pct": None,
                    "bars_held": None, "outcome_class": None, "outcome_labeled": False})
    rec["availability"] = _availability(rec, _CORE_FIELDS)
    return rec


def row_from_shadow(t: dict[str, Any]) -> dict[str, Any]:
    """ShadowBook kaydı → birleşik kayıt. Red edilmiş/açılmamış karardır (`accepted=False`,
    `is_counterfactual=True`); outcome varsa counterfactual etikettir, gerçek fill DEĞİLDİR."""
    flags: list[str] = ["COUNTERFACTUAL"]
    rec = _base_record(source_kind="SHADOW", flags=flags)
    symbol, direction = _s(t.get("symbol")), _s(t.get("direction"))
    variant = _s(t.get("variant")) or "as_planned"
    rec.update({
        "decision_id": stable_id("qj", "SHADOW", _s(t.get("plan_id")), symbol, direction, variant),
        "candidate_id": _s(t.get("plan_id")),
        "event_ts_utc": _s(t.get("created_at")),
        "as_of_ts_utc": _s(t.get("created_at")),
        "symbol": symbol,
        "market_type": _s(t.get("market_type")),
        "direction": direction,
        "timeframe": f"{int(t['tf_minutes'])}m" if isinstance(t.get("tf_minutes"), (int, float)) else None,
        "setup_id": None,
        "regime": None,
        "feature_snapshot": None,
        "specialist_scores": None,
        "veto_results": list(t.get("reason_not_opened") or []) or None,
        "ensemble_score": None,
        "p_win": None,
        "expected_r": None,
        "accepted": False,
        "reject_reason": "; ".join(str(x) for x in (t.get("reason_not_opened") or [])) or None,
        "planned_entry": _finite(t.get("entry"), "planned_entry", flags),
        "planned_stop": _finite(t.get("stop"), "planned_stop", flags),
        "planned_targets": [x for x in (t.get("targets") or []) if isinstance(x, (int, float)) and math.isfinite(float(x))] or None,
        "planned_leverage": _finite(t.get("leverage"), "planned_leverage", flags),
        "planned_notional": None,
        "cost_estimate": None,
        "policy_id": "champion",
        "outcome_ref": _s(t.get("id")),
        "variant": variant,
        "is_counterfactual": True,
    })
    out = t.get("outcome") if isinstance(t.get("outcome"), dict) else None
    if out is not None:
        rec.update({
            "exit_reason": _s(out.get("exit_reason")),
            "gross_pnl": None, "net_pnl": None, "fees": None, "funding": None,
            "r_multiple": _finite(out.get("r_multiple"), "r_multiple", flags),
            "mae_pct": _finite(out.get("mae_pct"), "mae_pct", flags),
            "mfe_pct": _finite(out.get("mfe_pct"), "mfe_pct", flags),
            "bars_held": int(out.get("bars")) if isinstance(out.get("bars"), (int, float)) else None,
            "outcome_class": "WIN" if out.get("won") else ("LOSS" if _finite(out.get("r_multiple"), "r", []) is not None and float(out.get("r_multiple", 0)) <= 0 else "SCRATCH"),
            "outcome_labeled": True,
        })
    else:
        rec.update({"exit_reason": None, "gross_pnl": None, "net_pnl": None, "fees": None,
                    "funding": None, "r_multiple": None, "mae_pct": None, "mfe_pct": None,
                    "bars_held": None, "outcome_class": None, "outcome_labeled": False})
    rec["availability"] = _availability(rec, _CORE_FIELDS)
    return rec


def rows_from_memory(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSONL satırları (entry/exit karışık) → entry+exit trade_id ile birleştirilmiş kayıtlar.
    Exit'i olmayan entry açık/etiketsiz kalır; entry'si olmayan exit `ORPHAN_EXIT` bayrağıyla atlanır."""
    entries: dict[str, dict] = {}
    exits: dict[str, dict] = {}
    order: list[str] = []
    orphans = 0
    for r in rows:
        tid = str(r.get("trade_id") or "")
        if not tid:
            continue
        if r.get("kind") == "entry":
            if tid not in entries:
                order.append(tid)
            entries[tid] = r
        elif r.get("kind") == "exit":
            if tid in entries:
                exits[tid] = r
            else:
                orphans += 1
    out = [row_from_memory(entries[t], exits.get(t)) for t in order]
    if orphans and out:
        out[0]["quality_flags"] = list(out[0]["quality_flags"]) + [f"ORPHAN_EXITS:{orphans}"]
    return out


def unify(memory_rows: Iterable[dict[str, Any]], shadow_trades: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kabul + gölge kayıtlarını tek listede toplar; duplicate `decision_id` tekilleştirilir
    (etiketli kayıt etiketsize tercih edilir — idempotent tekrar çalıştırma güvenli)."""
    seen: dict[str, dict] = {}
    order: list[str] = []
    for rec in list(rows_from_memory(memory_rows)) + [row_from_shadow(t) for t in shadow_trades]:
        did = rec["decision_id"]
        if did not in seen:
            seen[did] = rec
            order.append(did)
        elif rec.get("outcome_labeled") and not seen[did].get("outcome_labeled"):
            seen[did] = rec
    return [seen[d] for d in order]


def export_journal(path: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Birleşik günlüğü atomic olarak yazar. Deterministiktir (zaman damgası yok — aynı girdi
    aynı byte'ları üretir); RFC-safe (non-finite değerler zaten null'a indirildi)."""
    doc = {"schema_version": SCHEMA_VERSION, "n_records": len(rows),
           "n_labeled": sum(1 for r in rows if r.get("outcome_labeled")),
           "n_accepted": sum(1 for r in rows if r.get("accepted")),
           "records": rows}
    atomic_write_json(path, doc)
    return doc

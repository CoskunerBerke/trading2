"""Golge akis (shadow stream) — aday basina, KABUL EDILEN VE REDDEDILEN, temel + rakip yan yana.

Bu akisin tek isi kanit tasimaktir. Bir karari uygulamaz, bir modeli egitmez, hicbir seyi dagitmaz.

KIRMIZI CIZGI — benzetim sonuclari gercek kapanmis islemlerin ogrenme kayitlarina ASLA yazilmaz.
Bunu niyete birakmiyoruz: `assert_safe_output` uretim durum dizinine (state/ ve altindaki her sey,
`learn*`, `futures_ledger`, `entry_snapshot`, `position_path`, `learning*` adlari) yazmayi REDDEDER.
Kayitlar ayri bir ad alaninda (`namespace="research_shadow"`, `schema_version="research_shadow_v1"`)
durur; uretim okuyucularinin hicbiri bu ad alanini tanimaz.

Bagimsizlik — ayni kurulumun tekrar degerlendirmeleri BAGIMSIZ ISLEM DEGILDIR. Her kayit uc
bagimlilik etiketi tasir (`episode`, `cluster`, `day`) ve `episode_size` alani o bolumde kac tekrar
degerlendirme oldugunu soyler. Bu akistan bir sayi cikaran her hesap kume tabanli olmak ZORUNDADIR.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

SCHEMA = "research_shadow_v1"
NAMESPACE = "research_shadow"

#: Uretim durumunu tasiyan ad parcalari. Ciktinin bunlara dokunmasi YASAK.
FORBIDDEN_PARTS = ("state", "learn_v2", "learning", "learned_closes", "learning_chain",
                   "futures_ledger", "entry_snapshot", "position_path", "coin_heads", "entry_selectivity")

LABELLING_RULE = (
    "R = FuturesLedgerV2 net_pnl / |giris_dolumu - ilk_stop| * miktar; gercek 1m fapi mumlariyla, "
    "uretim komisyon/kayma/funding ayarlariyla, bar ici siralama likidasyon->STOP->hedef (kotumser). "
    "Karar aninin ONCESINDEKI hicbir mum gorulmez. Ufukta kapanmayan pozisyon ufuk sonu mum "
    "kapanisindan piyasa emriyle kapatilir ve exit='HORIZON' ile isaretlenir; bu bir politika degil "
    "OLCUM SINIRIDIR ve butun kollara AYNI uygulanir. ts + ufuk > veri_sonu ise kayit mature=false "
    "olur ve birincil tahminlerden DISLANIR."
)


def assert_safe_output(path: Path | str) -> Path:
    """Cikti yolunu dogrular. Uretim durumuna yazmayi engeller — niyet degil, kapi."""
    p = Path(path).resolve()
    parts = {x.lower() for x in p.parts} | {p.stem.lower()}
    hit = sorted(x for x in FORBIDDEN_PARTS if any(x in q for q in parts))
    if hit:
        raise RuntimeError(f"golge akis uretim durumuna yazamaz: {p} (yasak ad parcasi: {hit})")
    return p


def _sources(d: Mapping[str, Any]) -> dict:
    return dict(d.get("sources") or {})


def build_record(raw: Mapping[str, Any], evaluated: Mapping[str, Any] | None, *, horizon_h: float,
                 data_end_ms: int, c3_field: str, c3_tau: float, provenance: Mapping[str, Any],
                 episode_size: int) -> dict[str, Any]:
    """Tek adayin golge kaydi. `evaluated` yoksa (degerlendirilemeyen aday) sonuc alanlari None olur."""
    src = _sources(raw)
    ts_ms = int(raw["ts_ms"])
    mat_ms = ts_ms + int(horizon_h * 3_600_000)
    ev = dict(evaluated or {})
    res: dict[str, Any] = {}
    for arm in ("baseline", "C1_time_box_24h_mfe1R", "C2_tp1_at_1R"):
        a = ev.get(arm) or {}
        res[arm] = {"r_multiple": a.get("r"), "exit_reason": a.get("exit"), "mfe_r": a.get("mfe_r"),
                    "minutes_held": a.get("minutes"), "time_boxed": a.get("time_boxed"),
                    "excluded_reason": a.get("excluded") or None,
                    "unevaluable_reason": (a.get("note") or None) if a.get("r") is None else None,
                    "ambiguous_bars": a.get("amb")}
    cne = raw.get(c3_field)
    res["C3_gate_median"] = {"kind": "selection_filter", "field": c3_field, "tau": c3_tau,
                             "passes": (cne is not None and float(cne) >= c3_tau),
                             "r_multiple": (ev.get("baseline") or {}).get("r")
                             if (cne is not None and float(cne) >= c3_tau) else None,
                             "note": "secim filtresi: sonucu temel kolun sonucudur, ayri bir cikis yolu YOKTUR"}
    return {
        "schema_version": SCHEMA, "namespace": NAMESPACE,
        "candidate_id": raw.get("candidate_id"), "decision_id": raw.get("decision_id"),
        "ts": raw.get("ts"), "ts_ms": ts_ms, "run_id": raw.get("run_id"), "cycle_id": raw.get("cycle_id"),
        "symbol": raw.get("symbol"), "direction": raw.get("direction"), "setup": raw.get("setup"),
        "timeframe": raw.get("timeframe"), "market_type": raw.get("market_type"),
        # --- karar: kabul mu ret mi, ve neden. REDDEDILENLER SILINMEZ.
        "decision": {"accepted": bool(raw.get("baseline_accepted")),
                     "reject_reason": raw.get("baseline_reject_reason"),
                     "rank": raw.get("baseline_rank"), "chief_allow": raw.get("chief_allow"),
                     "risk_allowed": raw.get("risk_allowed"), "risk_reasons": raw.get("risk_reasons"),
                     "link_status": raw.get("link_status")},
        # --- karar aninda DONDURULMUS girdiler + kod/ayar surumu
        "frozen_inputs": {k: raw.get(k) for k in (
            "p_win", "confidence", "consensus_score", "expected_r", "regime", "n_dissent", "n_vetoes",
            "conservative_net_edge_r", "net_expectancy_r", "gross_expectancy_r", "expectancy_basis",
            "sample_size", "avg_win_r", "avg_loss_r", "entry_price", "stop_price", "targets",
            "stop_distance_pct", "portfolio_open_positions", "portfolio_open_risk_usdt",
            "same_direction_open", "weekly_structure", "candle_context", "mtf_context")},
        "specialist_scores": raw.get("specialist_scores"),
        "missing_fields": raw.get("missing_fields"), "n_missing": raw.get("n_missing"),
        "code_version": {"code_sha": raw.get("code_sha"), "config_hash": raw.get("config_hash"),
                         "policy_version": raw.get("policy_version"),
                         "snapshot_schema": raw.get("schema_version")},
        # --- kullanilan olasilik VE KAYNAGI
        "probability": {"value": raw.get("p_win"), "source": src.get("p_win"),
                        "note": "MODELED = kalibre model tahmini; HEAD onseli DEGIL (1c4cba1 kapi "
                                "sirasi onarimindan sonra kapi bu alani okur)"},
        # --- maliyet ve cezalar
        "cost": {"expected_cost_pct": raw.get("expected_cost_pct"),
                 "expected_cost_in_R": (float(raw["expected_cost_pct"]) / float(raw["stop_distance_pct"]))
                 if raw.get("expected_cost_pct") is not None and raw.get("stop_distance_pct") else None,
                 "expected_cost_source": src.get("expected_cost_pct"),
                 "funding_rate": raw.get("funding_rate"), "spread_pct": raw.get("spread_pct"),
                 "est_slippage_pct": raw.get("est_slippage_pct")},
        "penalties": {"uncertainty_penalty_r": raw.get("uncertainty_penalty_r"),
                      "short_penalty_r_config": 0.5 if str(raw.get("direction")).upper() == "SHORT" else 0.0,
                      "futures_only_penalty_r_config": 0.35},
        "size": {"planned_notional": raw.get("planned_notional"), "planned_leverage": raw.get("planned_leverage"),
                 "size_multiplier": raw.get("size_multiplier")},
        # --- olgunluk ve etiketleme kurali
        "maturity": {"horizon_h": horizon_h, "matures_at_ms": mat_ms,
                     "matures_at": datetime.fromtimestamp(mat_ms / 1000, tz=timezone.utc).isoformat(),
                     "data_end_ms": data_end_ms, "mature": mat_ms <= data_end_ms},
        "labelling_rule": LABELLING_RULE,
        # --- bagimlilik: tekrar degerlendirmeler BAGIMSIZ ISLEM DEGILDIR
        "dependence": {"episode": ev.get("episode"), "cluster": ev.get("cluster"), "day": ev.get("day"),
                       "episode_size": episode_size,
                       "rule": "bagimsiz birim = episode (sembol|yon|ufuk blogu). Kume-saglam hata payi "
                               "zorunlu; satir sayisi ornek buyuklugu DEGILDIR."},
        "results": res,
        "provenance": dict(provenance),
    }


def iter_shadow(entry_snapshot: Path | str, evaluated_rows: Mapping[str, Mapping[str, Any]], *,
                horizon_h: float, data_end_ms: int, c3_field: str, c3_tau: float,
                provenance: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """`entry_snapshot.jsonl`'i akitarak golge kayitlari uretir (93 MB dosya belege ALINMAZ)."""
    ep_size: dict[str, int] = {}
    for ev in evaluated_rows.values():
        if ev.get("episode"):
            ep_size[ev["episode"]] = ep_size.get(ev["episode"], 0) + 1
    with io.open(str(entry_snapshot), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if raw.get("ts_ms") is None or raw.get("kind") is not None:
                continue
            ev = evaluated_rows.get(str(raw.get("candidate_id")))
            yield build_record(raw, ev, horizon_h=horizon_h, data_end_ms=data_end_ms, c3_field=c3_field,
                               c3_tau=c3_tau, provenance=provenance,
                               episode_size=ep_size.get((ev or {}).get("episode", ""), 0))


__all__ = ["FORBIDDEN_PARTS", "LABELLING_RULE", "NAMESPACE", "SCHEMA", "assert_safe_output",
           "build_record", "iter_shadow"]

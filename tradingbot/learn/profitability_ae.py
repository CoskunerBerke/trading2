"""Kârlılık deneyi için KARAR ANI A/E kararları (`profitability_ae_v1`) — değişmez giriş snapshot'ından.

**Neden bu modül var (doğrulanmış kusur, 2026-09-06):** `pfexp_v1` P1/P4 politikaları A/E
kararını `entry_selectivity.json.trades` üzerinden alıyordu. O koleksiyon yalnız KAPANMIŞ
işlemlerin atıf satırlarını taşır (`entry_eval.evaluate_closes` ← `canonical_closes(history)`);
yeni açılan bir pozisyonun orada satırı YOKTUR. Dolayısıyla P1 giriş anında A/E'yi hiç
göremiyor, P4 de P1 bacağını alamıyor ve ikisi güvenli biçimde `ABSTAIN` ediyordu — yani
yapısal olarak ATIL kalıyordu. Fail-safe davranış doğruydu; girdi borusu yanlıştı.

**Tek kanonik yol.** Girdi YALNIZ değişmez giriş snapshot'ıdır (`entry_snapshot_v1`); as-of
anı snapshot'ın kendi `ts` alanıdır. Mevcut kanonik challenger değerlendiricisi
(`entry_challenger.challenger_a` / `challenger_e`) AYNEN yeniden kullanılır: eşik kopyalanmaz,
çatallanmaz, yeniden ayarlanmaz.

**Sızıntı sözleşmesi.** Bu modül kanonik kapanış geçmişini, sonuç günlüğünü, dersleri,
öğrenilmiş indeksi, pozisyon yolunu ya da ileri getirileri OKUMAZ. `realized_payoff` parametresi
BİLİNÇLİ olarak verilmez: kapanış-atıf raporu genişleyen pencereden ödeme oranı türetebilir
(o bir kapanış-sonrası raporlama girdisidir), fakat canlı giriş kararı yalnız snapshot'ın
kendi karar anı `avg_win_r/avg_loss_r` alanlarına (ya da challenger'ın varsayılanına) dayanır.
Challenger'lara snapshot'ın TAMAMI değil, `ALLOWED_SNAPSHOT_KEYS` ile sınırlı bir görünümü
verilir; böylece ileride snapshot'a eklenecek bir alan bile fark edilmeden okunamaz.

**Eksik → ABSTAIN.** Kanonik challenger eksik veride `ACCEPT + blockers` döner
(`MISSING_MEANS_ACCEPT`: ölçemediği için reddetmez). Deney için bu bir KARAR DEĞİLDİR: gerekli
alan ölçülmemişse bacak `ABSTAIN` olur ve eksik alanlar gerekçe koduyla açıkça listelenir.
Ölçülmüş sıfır sıfırdır; ölçülmemiş alan `None`/`MISSING` kalır. `VETO` ise ölçülmüş bir
boyuttaki kesin karardır ve `FILTER` bacağıdır.

Bu modül saftır, ağ/sağlayıcı isteği yapmaz, hiçbir yere yazmaz ve `applied` daima `False`tur.
"""
from __future__ import annotations

import math
from typing import Any

from .entry_challenger import (VETO, EntryChallengerConfig, R_MISSING, challenger_a,
                               challenger_e)
from .entry_eval import FORBIDDEN_OUTCOME_FIELDS

SCHEMA_VERSION = "profitability_ae_v1"

#: Provenans — her çıktı bunu taşır.
SOURCE_ENTRY_SNAPSHOT = "ENTRY_SNAPSHOT"
STAGE_RANKING = "RANKING"

#: Bacak sonuçları — deneyin üç değerli karar uzayıyla AYNI.
ACCEPT = "ACCEPT"
FILTER = "FILTER"
ABSTAIN = "ABSTAIN"

#: Gerekçe kodları (deney olay defterinde görünür).
R_A_VETO = "ENTRY_FAMILY_A_VETO"
R_E_VETO = "ENTRY_FAMILY_E_VETO"
R_A_INPUT_MISSING = "ENTRY_FAMILY_A_INPUT_MISSING"
R_E_INPUT_MISSING = "ENTRY_FAMILY_E_INPUT_MISSING"
R_NO_SNAPSHOT = "ENTRY_SNAPSHOT_UNAVAILABLE"
R_NOT_POINT_IN_TIME = "ENTRY_SNAPSHOT_NOT_POINT_IN_TIME"
R_OUTCOME_LEAK = "ENTRY_SNAPSHOT_CONTAINS_OUTCOME_FIELD"
R_AS_OF_MISSING = "ENTRY_SNAPSHOT_AS_OF_MISSING"
R_OK = "PASSES_ENTRY_FAMILY"

#: A ve E ailelerinin karar verebilmesi için GEREKLİ snapshot alanları.
REQUIRED_A: tuple[str, ...] = ("p_win", "conservative_net_edge_r")
REQUIRED_E: tuple[str, ...] = ("portfolio_open_risk_usdt", "same_direction_open",
                               "risk_budget_usdt")
#: Ödeme oranı yedeği (A) — karar anı `opportunity.assess` istatistiği; kapanış geçmişi DEĞİL.
OPTIONAL_A: tuple[str, ...] = ("avg_win_r", "avg_loss_r")

#: Challenger'ların GÖREBİLECEĞİ snapshot alanları. Bunun dışındaki hiçbir alan okunmaz.
ALLOWED_SNAPSHOT_KEYS: frozenset[str] = frozenset({
    # kimlik
    "candidate_id", "decision_id", "ts", "ts_ms", "symbol", "direction", "baseline_accepted",
    "link_status", "policy_version", "code_sha", "config_hash", "schema_version", "provenance",
    "sources",
    # A
    *REQUIRED_A, *OPTIONAL_A,
    # E
    *REQUIRED_E, "portfolio_open_positions",
})


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _abstain_all(reason: str, *, detail: dict[str, Any] | None = None,
                 snapshot: dict[str, Any] | None = None,
                 cfg: EntryChallengerConfig | None = None) -> dict[str, Any]:
    leg = {"decision": ABSTAIN, "reason_codes": [reason], "missing": [], "raw_decision": None,
           "raw_reason_codes": [], "blockers": [], "evidence": dict(detail or {})}
    return _envelope(snapshot, cfg, {"A": dict(leg), "E": dict(leg)}, available=False)


def _envelope(snapshot: dict[str, Any] | None, cfg: EntryChallengerConfig | None,
              families: dict[str, dict[str, Any]], *, available: bool) -> dict[str, Any]:
    s = snapshot if isinstance(snapshot, dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "available": bool(available),
        "candidate_id": s.get("candidate_id"),
        "decision_id": s.get("decision_id"),
        "as_of": s.get("ts"),
        "symbol": s.get("symbol"),
        "direction": s.get("direction"),
        "entry_policy_version": (cfg.policy_version if cfg is not None else None),
        "entry_config_id": (cfg.config_id if cfg is not None else None),
        "families": families,
        "provenance": {
            "source": SOURCE_ENTRY_SNAPSHOT,
            "stage": STAGE_RANKING,
            "sees_outcome": False,
            "realized_payoff_source": "SNAPSHOT_AVG_WIN_LOSS_OR_ASSUMED",
            "close_history_read": False,
            "learned_results_read": False,
            "network": False,
            "evaluator": "entry_challenger.challenger_a/challenger_e",
        },
        "applied": False,
    }


def _leg(verdict: dict[str, Any], *, veto_code: str, missing_code: str,
         required: tuple[str, ...], view: dict[str, Any],
         extra_missing: list[str] | None = None) -> dict[str, Any]:
    """Kanonik aile hükmünü deneyin üç değerli bacağına çevirir. ABSTAIN ASLA sessizce
    FILTER/ACCEPT olmaz; VETO ASLA sessizce ACCEPT olmaz."""
    raw = str(verdict.get("decision") or "")
    raw_codes = list(verdict.get("reason_codes") or [])
    blockers = list(verdict.get("blockers") or [])
    missing = sorted({*(k for k in required if _f(view.get(k)) is None),
                      *(str(b).split(":", 1)[1] for b in blockers if ":" in str(b)),
                      *(extra_missing or [])})
    ev = dict(verdict.get("evidence") or {})
    if raw == VETO:
        return {"decision": FILTER, "reason_codes": [veto_code, *raw_codes],
                "missing": missing, "raw_decision": raw, "raw_reason_codes": raw_codes,
                "blockers": blockers, "evidence": ev}
    if missing or R_MISSING in raw_codes:
        return {"decision": ABSTAIN, "reason_codes": [missing_code], "missing": missing,
                "raw_decision": raw, "raw_reason_codes": raw_codes, "blockers": blockers,
                "evidence": ev}
    return {"decision": ACCEPT, "reason_codes": [R_OK], "missing": [], "raw_decision": raw,
            "raw_reason_codes": raw_codes, "blockers": blockers, "evidence": ev}


def point_in_time_ae(snapshot: dict[str, Any] | None, cfg: EntryChallengerConfig | None
                     ) -> dict[str, Any]:
    """Değişmez giriş snapshot'ından A ve E bacakları. SAF ve DETERMİNİSTİK.

    Girdi snapshot'ın kendisidir; sonuç/kapanış/ders/indeks/yol OKUNMAZ. Aynı snapshot + aynı
    `cfg` → bayt bayt aynı çıktı. Gerekli alan yoksa bacak açık gerekçe koduyla `ABSTAIN` olur.
    """
    if cfg is None:
        return _abstain_all(R_NO_SNAPSHOT, detail={"why": "ENTRY_POLICY_CONFIG_UNAVAILABLE"},
                            snapshot=snapshot)
    if not isinstance(snapshot, dict) or not snapshot.get("candidate_id"):
        return _abstain_all(R_NO_SNAPSHOT, detail={"why": "NO_LINKED_ENTRY_SNAPSHOT"}, cfg=cfg)
    prov = snapshot.get("provenance") if isinstance(snapshot.get("provenance"), dict) else {}
    if prov.get("sees_outcome") or str(prov.get("written_at_stage") or "") != STAGE_RANKING:
        return _abstain_all(R_NOT_POINT_IN_TIME,
                            detail={"written_at_stage": prov.get("written_at_stage"),
                                    "sees_outcome": prov.get("sees_outcome")},
                            snapshot=snapshot, cfg=cfg)
    leaked = [k for k in FORBIDDEN_OUTCOME_FIELDS if k in snapshot]
    if leaked:
        return _abstain_all(R_OUTCOME_LEAK, detail={"fields": leaked},
                            snapshot=snapshot, cfg=cfg)
    if not snapshot.get("ts"):
        return _abstain_all(R_AS_OF_MISSING, snapshot=snapshot, cfg=cfg)

    # SINIRLI GÖRÜNÜM: challenger yalnız izinli alanları görebilir.
    view = {k: snapshot.get(k) for k in ALLOWED_SNAPSHOT_KEYS if k in snapshot}
    budget = _f(view.get("risk_budget_usdt"))
    # `realized_payoff` BİLEREK verilmez (kapanış geçmişi okunmaz).
    a_raw = challenger_a(view, cfg, realized_payoff=None)
    e_raw = challenger_e(view, cfg, risk_budget_usdt=budget)
    leg_a = _leg(a_raw, veto_code=R_A_VETO, missing_code=R_A_INPUT_MISSING,
                 required=REQUIRED_A, view=view)
    # E: bütçe ölçülmemişse ısı oranı hiç hesaplanmadı; VETO yoksa bu bir ACCEPT DEĞİL, ABSTAIN'dir.
    leg_e = _leg(e_raw, veto_code=R_E_VETO, missing_code=R_E_INPUT_MISSING,
                 required=REQUIRED_E, view=view,
                 extra_missing=(["risk_budget_usdt"] if budget is None else None))
    fams = {"A": leg_a | {"family": a_raw.get("family")},
            "E": leg_e | {"family": e_raw.get("family")}}
    return _envelope(snapshot, cfg, fams, available=True)


__all__ = ["SCHEMA_VERSION", "SOURCE_ENTRY_SNAPSHOT", "STAGE_RANKING",
           "ACCEPT", "FILTER", "ABSTAIN",
           "R_A_VETO", "R_E_VETO", "R_A_INPUT_MISSING", "R_E_INPUT_MISSING", "R_NO_SNAPSHOT",
           "R_NOT_POINT_IN_TIME", "R_OUTCOME_LEAK", "R_AS_OF_MISSING", "R_OK",
           "REQUIRED_A", "REQUIRED_E", "OPTIONAL_A", "ALLOWED_SNAPSHOT_KEYS",
           "point_in_time_ae"]

"""PAPER araştırma politikası — botun "bir şeyi değiştirip tekrar denemesi" için GÜVENLİ katman.

Bu katman CHAMPION DEĞİLDİR ve olamaz. LIVE/TESTNET'e uygulanamaz, riski yükseltemez, açık pozisyonun
entry/qty/stop/TP/leverage değerlerine dokunamaz. Yapabildiği tek şey YENİ bir girişi REDDETMEK ya da
boyutunu KÜÇÜLTMEK'tir.

Durum makinesi (geçişleri `ResearchCoordinator` yürütür; elle çağrı gerekmez):

    PROPOSED ─offline walk-forward─▶ SHADOW ─istatistik kapıları─▶ PAPER_RESEARCH_ACTIVE
        │                              │                                  │
        └──── REJECTED verdict ────────┴──────────────────────────────────┴──▶ RETIRED
                                                                          └──▶ MANUAL_REVIEW_READY

* `REJECTED` offline verdict → doğrudan RETIRED (kötü aday araştırmaya hiç girmez).
* `RESEARCH_ONLY` verdict (PIT=false / survivorship) → SHADOW'da kalır, ASLA aktifleşemez.
* Aynı anda **en fazla bir SHADOW ve bir ACTIVE** kayıt bulunabilir.
* `MANUAL_REVIEW_READY` bile CHAMPION/LIVE anlamına GELMEZ — yalnız operatörün bakacağı bir işaret.

Ölçüm sözleşmesi (önemli): aday bir girişi ELERSE katkısı 0'dır; KÜÇÜLTÜRSE gerçek işlem değişmez,
yalnız adayın **risk bütçesinin** ne kazanacağı hesaplanır. Bu yüzden metrik `baseline_r` karşısında
`risk_budget_contribution_r` adını taşır — "işlemin R'si değişti" DEĞİLDİR.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now
from .policy import CandidatePolicy, PolicyBoundsError, validate_policy

SCHEMA = "paper_research_policy_v2"
PROPOSED, SHADOW = "PROPOSED", "SHADOW"
ACTIVE, RETIRED, REVIEW = "PAPER_RESEARCH_ACTIVE", "RETIRED", "MANUAL_REVIEW_READY"
OFFLINE_VALIDATED = "OFFLINE_VALIDATED"          # geriye dönük uyum: eski state dosyaları
STATES = (PROPOSED, OFFLINE_VALIDATED, SHADOW, ACTIVE, RETIRED, REVIEW)
# Bu katmanın ASLA üretemeyeceği durumlar — kod düzeyinde kontrol edilir.
FORBIDDEN_STATES = ("CHAMPION", "LIVE", "TESTNET", "PROMOTED", "SHADOW_LIVE")
BLOCKED, SIZE_SCALED, UNCHANGED = "blocked", "size_scaled", "unchanged"


class ResearchSafetyError(ValueError):
    """Araştırma katmanı güvenlik sözleşmesi ihlali (fail-closed)."""


@dataclass
class ResearchGates:
    """Kapılar: yetersiz örnekle ya da her işlemden sonra politika DEĞİŞTİRİLMEZ."""
    min_shadow_obs: int = 20            # aktifleşmeden önce gereken eşleşmiş gözlem
    min_active_obs: int = 20            # emeklilik kararı için gereken gözlem
    min_review_obs: int = 60            # manuel inceleme işareti için gereken gözlem
    cooldown_hours: float = 24.0        # iki durum değişikliği arasındaki asgari süre
    retire_delta_r: float = -0.10       # bu kadar kötüleşirse otomatik baseline'a dönülür
    activate_delta_r: float = 0.0       # aktifleşme için gereken asgari eşleşmiş fark
    review_delta_r: float = 0.05
    min_fold_consistency: float = 0.6   # offline fold tutarlılığı tabanı

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("min_shadow_obs", "min_active_obs", "min_review_obs", "cooldown_hours",
                 "retire_delta_r", "activate_delta_r", "review_delta_r", "min_fold_consistency")}


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


@dataclass
class ResearchRecord:
    policy_id: str
    policy: dict
    state: str = PROPOSED
    created_at: str = ""
    state_changed_at: str = ""
    offline: dict = field(default_factory=dict)
    research_only: bool = False
    observations: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    retired_reason: str = ""

    def stats(self) -> dict:
        """Eşleşmiş istatistik. `delta` = adayın risk bütçesi − baseline (AYNI işlemler üzerinde)."""
        base = [float(o["baseline_r"]) for o in self.observations]
        cand = [float(o.get("risk_budget_contribution_r", o.get("candidate_r", 0.0))) for o in self.observations]
        bm, cm = _mean(base), _mean(cand)
        diff = [c - b for c, b in zip(cand, base)]
        dm = _mean(diff)
        lo = None
        if dm is not None and len(diff) > 1:
            sd = math.sqrt(sum((x - dm) ** 2 for x in diff) / (len(diff) - 1))
            lo = round(dm - 1.96 * sd / math.sqrt(len(diff)), 4) if sd > 0 else round(dm, 4)
        return {"n_obs": len(self.observations),
                "baseline_expectancy_r": (round(bm, 4) if bm is not None else None),
                "candidate_risk_budget_expectancy_r": (round(cm, 4) if cm is not None else None),
                "delta_r": (round(dm, 4) if dm is not None else None),
                "delta_ci95_low": lo,
                "metric": "risk_budget_contribution_r",
                "blocked": sum(1 for o in self.observations if o.get("kind") == BLOCKED),
                "size_reduced": sum(1 for o in self.observations if o.get("kind") == SIZE_SCALED)}

    def to_dict(self) -> dict:
        p = self.policy or {}
        return {"policy_id": self.policy_id, "state": self.state, "created_at": self.created_at,
                "state_changed_at": self.state_changed_at, "research_only": self.research_only,
                "rationale": p.get("rationale", ""), "changed_params": p.get("changed_params", []),
                "source_findings": p.get("source_findings", []),
                "offline_verdict": (self.offline or {}).get("verdict"),
                "offline": self.offline, "stats": self.stats(), "history": self.history[-30:],
                "observations": self.observations[-200:],
                "retired_reason": self.retired_reason, "policy": p}


class ResearchPolicyBook:
    """Araştırma adaylarının kalıcı defteri. `state/research_policy.json` (atomik, idempotent).

    Yükleme sırasında her kayıt yeniden doğrulanır: sınır ihlali olan / yasak anahtar taşıyan /
    çözümlenemeyen politika **karantinaya alınıp RETIRED** edilir ve asla uygulanmaz (fail-closed).
    """

    def __init__(self, path: Path | str | None = None, gates: ResearchGates | None = None,
                 *, risk_profile_max_leverage: float = 1.0):
        self.path = Path(path) if path else None
        self.gates = gates or ResearchGates()
        self.risk_profile_max_leverage = float(risk_profile_max_leverage)
        self.records: list[ResearchRecord] = []
        self.pending: dict[str, dict] = {}
        self.quarantined: list[dict] = []
        if self.path:
            d = read_json(self.path, default=None)
            self.pending = dict((d or {}).get("pending") or {})
            for r in ((d or {}).get("records") or []):
                rec = ResearchRecord(
                    policy_id=str(r.get("policy_id") or ""), policy=dict(r.get("policy") or {}),
                    state=str(r.get("state") or PROPOSED), created_at=str(r.get("created_at") or ""),
                    state_changed_at=str(r.get("state_changed_at") or ""),
                    offline=dict(r.get("offline") or {}), research_only=bool(r.get("research_only")),
                    observations=list(r.get("observations") or []), history=list(r.get("history") or []),
                    retired_reason=str(r.get("retired_reason") or ""))
                self._quarantine_if_unsafe(rec)
                self.records.append(rec)

    # ------------------------------------------------------------ güvenlik
    def _quarantine_if_unsafe(self, rec: ResearchRecord) -> None:
        """Diskten gelen politika artık güvenli değilse uygulanmadan RETIRED edilir."""
        if rec.state in (RETIRED,):
            return
        try:
            p = policy_from_dict(rec.policy)
            validate_policy(p, risk_profile_max_leverage=self.risk_profile_max_leverage)
            assert_filter_or_shrink_only(p)
        except Exception as exc:  # noqa: BLE001 — her hata karantina sebebidir
            reason = f"QUARANTINED: kalıcı politika güvenli değil → {type(exc).__name__}: {exc}"
            rec.history.append({"from": rec.state, "to": RETIRED, "at": iso(utc_now()), "reason": reason})
            rec.state, rec.retired_reason = RETIRED, reason
            self.quarantined.append({"policy_id": rec.policy_id, "reason": reason})

    def _set_state(self, rec: ResearchRecord, state: str, *, at: Any, reason: str = "") -> None:
        if str(state).upper() in FORBIDDEN_STATES:
            raise ResearchSafetyError(f"araştırma katmanı {state} durumu ÜRETEMEZ")
        if state not in STATES:
            raise ResearchSafetyError(f"bilinmeyen durum: {state}")
        rec.history.append({"from": rec.state, "to": state, "at": iso(at), "reason": reason})
        rec.state, rec.state_changed_at = state, iso(at)
        if state == RETIRED:
            rec.retired_reason = reason

    # ------------------------------------------------------------ erişim
    def get(self, policy_id: str) -> ResearchRecord | None:
        return next((r for r in self.records if r.policy_id == policy_id), None)

    def active(self) -> ResearchRecord | None:
        return next((r for r in self.records if r.state == ACTIVE), None)

    def shadow(self) -> ResearchRecord | None:
        return next((r for r in self.records if r.state == SHADOW), None)

    def active_policy(self) -> CandidatePolicy | None:
        """GERÇEK girişlere uygulanacak politika. Aktif aday yoksa None → baseline aynen sürer."""
        rec = self.active()
        return policy_from_dict(rec.policy) if rec else None

    def shadow_policy(self) -> CandidatePolicy | None:
        """Yalnız KARŞI-OLGUSAL değerlendirme için. Gerçek girişi ASLA değiştirmez."""
        rec = self.shadow()
        return policy_from_dict(rec.policy) if rec else None

    def cooldown_ok(self, rec: ResearchRecord, now: Any) -> bool:
        if not rec.state_changed_at:
            return True
        try:
            return (now - from_iso(rec.state_changed_at)).total_seconds() >= self.gates.cooldown_hours * 3600
        except (ValueError, TypeError):
            return True

    def last_change_at(self) -> str:
        return max((r.state_changed_at for r in self.records if r.state_changed_at), default="")

    # ------------------------------------------------------------ geçişler
    def propose(self, policy: CandidatePolicy, *, now: Any | None = None) -> ResearchRecord:
        """Adayı deftere alır. Sınır ihlali olan aday DEFTERE GİREMEZ (fail-closed)."""
        now = now or utc_now()
        validate_policy(policy, risk_profile_max_leverage=self.risk_profile_max_leverage)
        assert_filter_or_shrink_only(policy)
        existing = self.get(policy.policy_id)
        if existing:
            return existing                                  # idempotent
        rec = ResearchRecord(policy_id=policy.policy_id, policy=policy.to_dict(),
                             created_at=iso(now), state_changed_at=iso(now))
        rec.history.append({"from": "", "to": PROPOSED, "at": iso(now), "reason": "kayıp analizinden önerildi"})
        self.records.append(rec)
        self.save()
        return rec

    def record_offline(self, policy_id: str, report: dict, *, now: Any | None = None) -> str:
        """Walk-forward sonucunu işler.

        REJECTED → RETIRED. SHADOW_CANDIDATE → SHADOW (aktifleşebilir).
        RESEARCH_ONLY → SHADOW ama `research_only=True` (ASLA aktifleşemez).
        Aynı anda birden fazla SHADOW olamaz.
        """
        now = now or utc_now()
        rec = self.get(policy_id)
        if rec is None:
            raise ResearchSafetyError(f"bilinmeyen aday: {policy_id}")
        verdict = str((report or {}).get("verdict") or "REJECTED").upper()
        rec.offline = {"verdict": verdict, "gates": (report or {}).get("gates"),
                       "failed_gates": (report or {}).get("failed_gates"),
                       "candidate": (report or {}).get("candidate"), "baseline": (report or {}).get("baseline"),
                       "fold_consistency": (report or {}).get("fold_consistency"),
                       "delta_expectancy_r": (report or {}).get("delta_expectancy_r"),
                       "scored_folds": (report or {}).get("scored_folds"),
                       "evaluated_at": iso(now)}
        if verdict == "REJECTED":
            self._set_state(rec, RETIRED, at=now, reason=f"offline REJECTED: {(report or {}).get('failed_gates')}")
        elif self.shadow() is not None and self.shadow() is not rec:
            self._set_state(rec, RETIRED, at=now, reason="aynı anda yalnız bir SHADOW aday olabilir")
        else:
            rec.research_only = (verdict != "SHADOW_CANDIDATE")
            self._set_state(rec, SHADOW, at=now,
                            reason=f"offline {verdict} → gölge gözlem başladı"
                                   + (" (RESEARCH_ONLY: aktifleşemez)" if rec.research_only else ""))
        self.save()
        return rec.state

    def observe(self, policy_id: str, *, trade_id: str, baseline_r: float,
                risk_budget_contribution_r: float, kind: str = UNCHANGED,
                size_multiplier: float = 1.0, reasons: list[str] | None = None,
                at: Any | None = None) -> bool:
        """Eşleşmiş gözlem ekler. Aynı `trade_id` iki kez yazılamaz (çift sayım yok).

        `risk_budget_contribution_r`: adayın risk bütçesinin AYNI işlemde ne kazanacağı.
        Eleme → 0.0; küçültme → gerçekleşen R × çarpan; dokunmama → gerçekleşen R.
        Gerçek işlemin R'si bu değerden ETKİLENMEZ.
        """
        rec = self.get(policy_id)
        if rec is None or rec.state == RETIRED:
            return False
        if any(o.get("trade_id") == trade_id for o in rec.observations):
            return False
        rec.observations.append({"trade_id": trade_id, "baseline_r": round(float(baseline_r), 6),
                                 "risk_budget_contribution_r": round(float(risk_budget_contribution_r), 6),
                                 "kind": str(kind), "size_multiplier": round(float(size_multiplier), 4),
                                 "reasons": list(reasons or []), "at": iso(at or utc_now())})
        self.save()
        return True

    # ------------------------------------------------------------ kapılar
    def activation_gates(self, rec: ResearchRecord, *, now: Any) -> tuple[bool, dict]:
        """SHADOW → ACTIVE için BÜTÜN kapılar. Biri geçmezse aktifleşme yok."""
        g, st = self.gates, rec.stats()
        off = rec.offline or {}
        fc = off.get("fold_consistency")
        cand_exp, base_exp = st["candidate_risk_budget_expectancy_r"], st["baseline_expectancy_r"]
        gates = {
            "state_is_shadow": rec.state == SHADOW,
            "not_research_only": not rec.research_only,
            "offline_shadow_candidate": str(off.get("verdict") or "") == "SHADOW_CANDIDATE",
            "enough_shadow_obs": st["n_obs"] >= g.min_shadow_obs,
            "cooldown_elapsed": self.cooldown_ok(rec, now),
            "paired_delta_positive": st["delta_r"] is not None and st["delta_r"] > g.activate_delta_r,
            "paired_delta_ci95_low_above_zero": st["delta_ci95_low"] is not None and st["delta_ci95_low"] > 0,
            "candidate_beats_baseline": (cand_exp is not None and base_exp is not None and cand_exp > base_exp),
            "fold_consistency_ok": fc is not None and float(fc) >= g.min_fold_consistency,
            "no_other_active": self.active() is None,
        }
        return all(gates.values()), gates

    def maybe_activate(self, *, now: Any | None = None) -> str | None:
        """Kapılar geçilirse SHADOW → PAPER_RESEARCH_ACTIVE. Aynı anda tek aktif aday."""
        now = now or utc_now()
        rec = self.shadow()
        if rec is None:
            return None
        ok, gates = self.activation_gates(rec, now=now)
        if not ok:
            return None
        st = rec.stats()
        self._set_state(rec, ACTIVE, at=now,
                        reason=(f"gölgede {st['n_obs']} eşleşmiş gözlem, fark {st['delta_r']:+.4f}R "
                                f"(CI95 alt {st['delta_ci95_low']:+.4f})"))
        self.save()
        return rec.policy_id

    def evaluate_active(self, *, now: Any | None = None) -> str | None:
        """Aktif adayı denetler: kötüleşirse ATOMİK RETIRED (anında baseline), sürekli iyiyse REVIEW."""
        now = now or utc_now()
        rec = self.active()
        if rec is None:
            return None
        g, st = self.gates, rec.stats()
        if st["n_obs"] >= g.min_active_obs and st["delta_r"] is not None and st["delta_r"] <= g.retire_delta_r:
            self._set_state(rec, RETIRED, at=now,
                            reason=(f"kötüleşme: {st['n_obs']} gözlemde fark {st['delta_r']:+.4f}R "
                                    f"(eşik {g.retire_delta_r:+.2f}) → baseline'a dönüldü"))
            self.save()
            return RETIRED
        if (st["n_obs"] >= g.min_review_obs and st["delta_r"] is not None
                and st["delta_r"] >= g.review_delta_r and self.cooldown_ok(rec, now)):
            self._set_state(rec, REVIEW, at=now,
                            reason=(f"sürdürülen iyileşme: {st['n_obs']} gözlemde fark {st['delta_r']:+.4f}R "
                                    f"(CHAMPION/LIVE DEĞİL — yalnız manuel inceleme işareti)"))
            self.save()
            return REVIEW
        return None

    # ------------------------------------------------------------ bekleyen eşleşmeler
    @staticmethod
    def pending_key(policy_id: str, trade_id: str) -> str:
        return f"{policy_id}|{trade_id}"

    def add_pending(self, policy_id: str, trade_id: str, payload: dict) -> None:
        """Girişte alınan aday kararını, işlem kapanana kadar saklar (restart'a dayanıklı, idempotent)."""
        self.pending[self.pending_key(policy_id, trade_id)] = dict(payload) | {
            "policy_id": policy_id, "trade_id": trade_id}
        self.save()

    def pop_pending_for_trade(self, trade_id: str) -> list[dict]:
        """Bu işlemle ilgili bütün bekleyen aday kararlarını çıkarır (ACTIVE ve/veya SHADOW)."""
        keys = [k for k, v in self.pending.items() if v.get("trade_id") == trade_id]
        out = [self.pending.pop(k) for k in keys]
        if out:
            self.save()
        return out

    # ------------------------------------------------------------ kalıcılık / özet
    def save(self) -> None:
        if self.path is not None:
            atomic_write_json(self.path, self.to_dict())

    def to_dict(self) -> dict:
        act, sh = self.active(), self.shadow()
        return {"schema": SCHEMA, "updated_at": iso(utc_now()), "gates": self.gates.to_dict(),
                "active_policy_id": (act.policy_id if act else None),
                "active_rationale": ((act.policy or {}).get("rationale") if act else None),
                "active_changed_params": ((act.policy or {}).get("changed_params") if act else []),
                "active_stats": (act.stats() if act else None),
                "shadow_policy_id": (sh.policy_id if sh else None),
                "shadow_stats": (sh.stats() if sh else None),
                "counts": {s: sum(1 for r in self.records if r.state == s) for s in STATES},
                "quarantined": list(self.quarantined),
                "auto_promotion_possible": False,
                "note": "Bu katman CHAMPION/LIVE/TESTNET üretemez; yalnız YENİ girişi filtreler ya da küçültür.",
                "pending": dict(self.pending),
                "records": [r.to_dict() for r in self.records]}


# --------------------------------------------------------------------------- yardımcılar
def assert_filter_or_shrink_only(p: CandidatePolicy) -> None:
    """Kod düzeyinde son savunma hattı: aday yalnız eleyebilir ya da küçültebilir."""
    if not p.filters_enabled:
        raise ResearchSafetyError("baseline politikası araştırma adayı olamaz")
    if float(p.size_multiplier) > 1.0:
        raise ResearchSafetyError("size_multiplier > 1.0 — araştırma adayı riski YÜKSELTEMEZ")
    if p.high_vol_size_multiplier is not None and float(p.high_vol_size_multiplier) > float(p.size_multiplier):
        raise ResearchSafetyError("high_vol_size_multiplier taban çarpanı aşamaz")
    if float(p.max_leverage_cap) > 1.0:
        raise ResearchSafetyError("max_leverage_cap kaldıraç YÜKSELTEMEZ")
    caps = (p.to_dict().get("capabilities") or {})
    if not caps.get("can_only_filter_or_shrink"):
        raise ResearchSafetyError("politika yetenek sözleşmesi ihlali")


_POLICY_FIELDS = ("seed", "min_p_win", "min_expected_net_r", "min_consensus", "size_multiplier",
                  "max_leverage_cap", "side_veto", "symbol_veto", "regime_veto", "side_penalty",
                  "agent_weights", "filters_enabled", "notes", "max_vol_regime", "max_spread_pct",
                  "min_pattern_ci_low", "max_n_dissent", "high_vol_size_multiplier", "side_regime_veto",
                  "rationale", "parent_id")


def policy_from_dict(d: dict) -> CandidatePolicy:
    kw = {k: d[k] for k in _POLICY_FIELDS if k in d and d[k] is not None}
    kw.setdefault("seed", 0)
    return CandidatePolicy(policy_id=str(d.get("policy_id") or ""),
                           source_findings=list(d.get("source_findings") or []), **kw)


def apply_research_policy(policy: CandidatePolicy | None, snap_values: dict, *, side: str, symbol: str,
                          p_win: float, expected_net_r: float) -> dict:
    """Tek karar noktası: politika yoksa baseline (değişiklik yok).

    Dönen `size_multiplier` her zaman `[0.0, 1.0]` aralığındadır — çağıran taraf bunu yalnız KÜÇÜLTME
    çarpanı olarak kullanabilir.
    """
    if policy is None:
        return {"allow": True, "size_multiplier": 1.0, "reasons": ["NO_POLICY"], "policy_id": None}
    d = policy.decide(snap_values, side=side, symbol=symbol, p_win=p_win, expected_net_r=expected_net_r)
    mult = float(d["size_multiplier"])
    if mult > 1.0:                                            # ulaşılamaz; yine de fail-closed
        raise PolicyBoundsError("araştırma politikası boyutu YÜKSELTEMEZ")
    return {"allow": bool(d["allow"]), "size_multiplier": max(0.0, min(1.0, mult)),
            "reasons": list(d["reasons"]), "policy_id": policy.policy_id}


def contribution_of(decision: dict, realized_r: float) -> tuple[float, str]:
    """Adayın AYNI işlemdeki risk bütçesi katkısı + katkı türü.

    Eleme → 0.0 (işlem hiç açılmazdı). Küçültme → gerçekleşen R × çarpan (gerçek işlem DEĞİŞMEZ,
    yalnız adayın bütçesi ölçeklenir). Dokunmama → gerçekleşen R.
    """
    if not decision.get("allow"):
        return 0.0, BLOCKED
    mult = float(decision.get("size_multiplier", 1.0))
    if mult < 1.0:
        return float(realized_r) * mult, SIZE_SCALED
    return float(realized_r), UNCHANGED


__all__ = ["ACTIVE", "BLOCKED", "FORBIDDEN_STATES", "OFFLINE_VALIDATED", "PROPOSED", "RETIRED", "REVIEW",
           "SCHEMA", "SHADOW", "SIZE_SCALED", "STATES", "UNCHANGED", "ResearchGates", "ResearchPolicyBook",
           "ResearchRecord", "ResearchSafetyError", "apply_research_policy", "assert_filter_or_shrink_only",
           "contribution_of", "policy_from_dict"]

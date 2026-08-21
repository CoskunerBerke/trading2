"""PAPER araştırma politikası — botun "bir şeyi değiştirip tekrar denemesi" için GÜVENLİ katman.

Bu katman CHAMPION DEĞİLDİR ve olamaz. LIVE/TESTNET'e uygulanamaz, riski yükseltemez, açık pozisyonun
stop/TP'sine dokunmaz. Yapabildiği tek şey yeni bir girişi REDDETMEK ya da boyutunu KÜÇÜLTMEK'tir.

Durum makinesi (tek yön, geri dönüş yalnız RETIRED):

    PROPOSED ─offline değerlendirme─▶ OFFLINE_VALIDATED ─▶ SHADOW ─▶ PAPER_RESEARCH_ACTIVE
        │                                   │                  │              │
        └────── REJECTED verdict ───────────┴──────────────────┴──────────────┴──▶ RETIRED
                                                                               └──▶ MANUAL_REVIEW_READY

* `REJECTED` offline verdict → doğrudan RETIRED (kötü aday araştırmaya hiç girmez).
* `RESEARCH_ONLY` verdict (PIT=false / survivorship) → SHADOW'a kadar gidebilir, ASLA aktifleşemez.
* Aktifleşme için: yeterli shadow gözlemi + cooldown + pozitif fark + başka aktif aday olmaması.
* Aktifken kötüleşirse otomatik RETIRED ve baseline'a dönülür (yalnız araştırma politikası kalkar;
  açık pozisyona dokunulmaz).
* `MANUAL_REVIEW_READY` bile CHAMPION/LIVE anlamına GELMEZ — yalnız operatörün bakabileceği bir işaret.

Gözlemler `trade_id` ile tekilleştirilir: aynı işlem iki gruba yazılamaz. Karşılaştırma eşleşmiştir —
her işlem için hem baseline (passthrough) hem candidate sonucu aynı piyasa yolundan hesaplanır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, from_iso, iso, read_json, utc_now
from .policy import CandidatePolicy, PolicyBoundsError, validate_policy

SCHEMA = "paper_research_policy_v1"
PROPOSED, OFFLINE_VALIDATED, SHADOW = "PROPOSED", "OFFLINE_VALIDATED", "SHADOW"
ACTIVE, RETIRED, REVIEW = "PAPER_RESEARCH_ACTIVE", "RETIRED", "MANUAL_REVIEW_READY"
STATES = (PROPOSED, OFFLINE_VALIDATED, SHADOW, ACTIVE, RETIRED, REVIEW)
# Bu katmanın ASLA üretemeyeceği durumlar — kod düzeyinde kontrol edilir.
FORBIDDEN_STATES = ("CHAMPION", "LIVE", "TESTNET", "PROMOTED")


class ResearchSafetyError(ValueError):
    """Araştırma katmanı güvenlik sözleşmesi ihlali (fail-closed)."""


@dataclass
class ResearchGates:
    """Kapılar: yetersiz örnekle ya da her işlemden sonra politika DEĞİŞTİRİLMEZ."""
    min_shadow_obs: int = 20            # aktifleşmeden önce gereken eşleşmiş gözlem
    min_active_obs: int = 20            # emeklilik kararı için gereken gözlem
    min_review_obs: int = 60            # manuel inceleme işareti için gereken gözlem
    cooldown_hours: float = 24.0        # iki durum değişikliği arasındaki asgari süre
    retire_delta_r: float = -0.10       # bu kadar kötüleşirse otomatik emeklilik
    activate_delta_r: float = 0.0       # aktifleşme için gereken asgari fark
    review_delta_r: float = 0.05        # manuel inceleme işareti için gereken fark

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("min_shadow_obs", "min_active_obs", "min_review_obs", "cooldown_hours",
                 "retire_delta_r", "activate_delta_r", "review_delta_r")}


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

    # ------------------------------------------------------------ istatistik
    def stats(self) -> dict:
        base = [float(o["baseline_r"]) for o in self.observations]
        cand = [float(o["candidate_r"]) for o in self.observations]
        bm, cm = _mean(base), _mean(cand)
        diff = [c - b for c, b in zip(cand, base)]
        dm = _mean(diff)
        sd = (math.sqrt(sum((x - dm) ** 2 for x in diff) / len(diff)) if dm is not None and len(diff) > 1 else 0.0)
        return {"n_obs": len(self.observations),
                "baseline_expectancy_r": (round(bm, 4) if bm is not None else None),
                "candidate_expectancy_r": (round(cm, 4) if cm is not None else None),
                "delta_r": (round(dm, 4) if dm is not None else None),
                "delta_ci95_low": (round(dm - 1.96 * sd / math.sqrt(len(diff)), 4)
                                   if dm is not None and len(diff) > 1 and sd > 0 else None),
                "blocked": sum(1 for o in self.observations if not o.get("allowed")),
                "size_reduced": sum(1 for o in self.observations
                                    if o.get("allowed") and float(o.get("size_multiplier", 1.0)) < 1.0)}

    def to_dict(self) -> dict:
        p = self.policy or {}
        return {"policy_id": self.policy_id, "state": self.state, "created_at": self.created_at,
                "state_changed_at": self.state_changed_at, "research_only": self.research_only,
                "rationale": p.get("rationale", ""), "changed_params": p.get("changed_params", []),
                "source_findings": p.get("source_findings", []),
                "offline_verdict": (self.offline or {}).get("verdict"),
                "offline": self.offline, "stats": self.stats(), "history": self.history[-30:],
                "retired_reason": self.retired_reason, "policy": p}


class ResearchPolicyBook:
    """Araştırma adaylarının kalıcı defteri. `state/research_policy.json` (atomik, idempotent)."""

    def __init__(self, path: Path | str | None = None, gates: ResearchGates | None = None):
        self.path = Path(path) if path else None
        self.gates = gates or ResearchGates()
        self.records: list[ResearchRecord] = []
        self.pending: dict[str, dict] = {}
        if self.path:
            d = read_json(self.path, default=None)
            self.pending = dict((d or {}).get("pending") or {})
            for r in ((d or {}).get("records") or []):
                self.records.append(ResearchRecord(
                    policy_id=str(r.get("policy_id") or ""), policy=dict(r.get("policy") or {}),
                    state=str(r.get("state") or PROPOSED), created_at=str(r.get("created_at") or ""),
                    state_changed_at=str(r.get("state_changed_at") or ""),
                    offline=dict(r.get("offline") or {}), research_only=bool(r.get("research_only")),
                    observations=list(r.get("observations") or []), history=list(r.get("history") or []),
                    retired_reason=str(r.get("retired_reason") or "")))

    # ------------------------------------------------------------ yardımcı
    def get(self, policy_id: str) -> ResearchRecord | None:
        return next((r for r in self.records if r.policy_id == policy_id), None)

    def active(self) -> ResearchRecord | None:
        return next((r for r in self.records if r.state == ACTIVE), None)

    def active_policy(self) -> CandidatePolicy | None:
        """Uygulanacak politika. Aktif aday yoksa None → bot baseline davranışını AYNEN sürdürür."""
        rec = self.active()
        if rec is None:
            return None
        return _policy_from_dict(rec.policy)

    def _set_state(self, rec: ResearchRecord, state: str, *, at: Any, reason: str = "") -> None:
        if str(state).upper() in FORBIDDEN_STATES:
            raise ResearchSafetyError(f"araştırma katmanı {state} durumu ÜRETEMEZ")
        if state not in STATES:
            raise ResearchSafetyError(f"bilinmeyen durum: {state}")
        rec.history.append({"from": rec.state, "to": state, "at": iso(at), "reason": reason})
        rec.state, rec.state_changed_at = state, iso(at)
        if state == RETIRED:
            rec.retired_reason = reason

    def _cooldown_ok(self, rec: ResearchRecord, now: Any) -> bool:
        if not rec.state_changed_at:
            return True
        try:
            return (now - from_iso(rec.state_changed_at)).total_seconds() >= self.gates.cooldown_hours * 3600
        except (ValueError, TypeError):
            return True

    # ------------------------------------------------------------ geçişler
    def propose(self, policy: CandidatePolicy, *, now: Any | None = None,
                risk_profile_max_leverage: float = 1.0) -> ResearchRecord:
        """Adayı deftere alır. Sınır ihlali olan aday DEFTERE GİREMEZ (fail-closed)."""
        now = now or utc_now()
        validate_policy(policy, risk_profile_max_leverage=risk_profile_max_leverage)
        _assert_filter_or_shrink_only(policy)
        existing = self.get(policy.policy_id)
        if existing:
            return existing                                  # idempotent
        rec = ResearchRecord(policy_id=policy.policy_id, policy=policy.to_dict(),
                             created_at=iso(now), state_changed_at=iso(now))
        rec.history.append({"from": "", "to": PROPOSED, "at": iso(now), "reason": "önerildi"})
        self.records.append(rec)
        self.save()
        return rec

    def record_offline(self, policy_id: str, report: dict, *, now: Any | None = None) -> str:
        """Walk-forward sonucunu işler. REJECTED → doğrudan RETIRED (araştırmaya hiç girmez)."""
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
                       "evaluated_at": iso(now)}
        if verdict == "REJECTED":
            self._set_state(rec, RETIRED, at=now, reason=f"offline REJECTED: {(report or {}).get('failed_gates')}")
        else:
            rec.research_only = (verdict != "SHADOW_CANDIDATE")
            self._set_state(rec, OFFLINE_VALIDATED, at=now, reason=f"offline {verdict}")
        self.save()
        return rec.state

    def start_shadow(self, policy_id: str, *, now: Any | None = None) -> str:
        now = now or utc_now()
        rec = self.get(policy_id)
        if rec is None or rec.state != OFFLINE_VALIDATED:
            raise ResearchSafetyError(f"SHADOW yalnız {OFFLINE_VALIDATED} durumundan başlar: "
                                      f"{policy_id} = {rec.state if rec else 'YOK'}")
        self._set_state(rec, SHADOW, at=now, reason="gölge gözlem başladı")
        self.save()
        return rec.state

    # ------------------------------------------------------------ bekleyen eşleşmeler
    def add_pending(self, key: str, payload: dict) -> None:
        """Girişte alınan aday kararını, işlem/gölge kapanana kadar saklar (restart'a dayanıklı)."""
        self.pending[str(key)] = dict(payload)
        self.save()

    def pop_pending(self, key: str) -> dict | None:
        out = self.pending.pop(str(key), None)
        if out is not None:
            self.save()
        return out

    def observe(self, policy_id: str, *, trade_id: str, baseline_r: float, candidate_r: float,
                allowed: bool, size_multiplier: float, reasons: list[str] | None = None,
                at: Any | None = None) -> bool:
        """Eşleşmiş gözlem ekler. Aynı `trade_id` iki kez yazılamaz (çift sayım yok)."""
        rec = self.get(policy_id)
        if rec is None or rec.state in (RETIRED,):
            return False
        if any(o.get("trade_id") == trade_id for o in rec.observations):
            return False
        rec.observations.append({"trade_id": trade_id, "baseline_r": round(float(baseline_r), 6),
                                 "candidate_r": round(float(candidate_r), 6), "allowed": bool(allowed),
                                 "size_multiplier": round(float(size_multiplier), 4),
                                 "reasons": list(reasons or []), "at": iso(at or utc_now())})
        self.save()
        return True

    def maybe_activate(self, *, now: Any | None = None) -> str | None:
        """Kapılar geçilirse SHADOW → PAPER_RESEARCH_ACTIVE. Aynı anda tek aktif aday olabilir."""
        now = now or utc_now()
        if self.active() is not None:
            return None
        g = self.gates
        for rec in self.records:
            if rec.state != SHADOW or rec.research_only:
                continue
            st = rec.stats()
            if st["n_obs"] < g.min_shadow_obs or not self._cooldown_ok(rec, now):
                continue
            if st["delta_r"] is None or st["delta_r"] <= g.activate_delta_r:
                continue
            self._set_state(rec, ACTIVE, at=now,
                            reason=f"gölgede {st['n_obs']} gözlem, fark {st['delta_r']:+.4f}R")
            self.save()
            return rec.policy_id
        return None

    def evaluate_active(self, *, now: Any | None = None) -> str | None:
        """Aktif adayı denetler: kötüleşirse RETIRED (baseline'a dönüş), sürekli iyiyse REVIEW işareti."""
        now = now or utc_now()
        rec = self.active()
        if rec is None:
            return None
        g, st = self.gates, rec.stats()
        if st["n_obs"] >= g.min_active_obs and st["delta_r"] is not None and st["delta_r"] <= g.retire_delta_r:
            self._set_state(rec, RETIRED, at=now,
                            reason=f"kötüleşme: {st['n_obs']} gözlemde fark {st['delta_r']:+.4f}R "
                                   f"(eşik {g.retire_delta_r:+.2f}) → baseline'a dönüldü")
            self.save()
            return RETIRED
        if (st["n_obs"] >= g.min_review_obs and st["delta_r"] is not None
                and st["delta_r"] >= g.review_delta_r and self._cooldown_ok(rec, now)):
            self._set_state(rec, REVIEW, at=now,
                            reason=f"sürdürülen iyileşme: {st['n_obs']} gözlemde fark {st['delta_r']:+.4f}R "
                                   f"(CHAMPION/LIVE DEĞİL — yalnız manuel inceleme işareti)")
            self.save()
            return REVIEW
        return None

    # ------------------------------------------------------------ kalıcılık / özet
    def save(self) -> None:
        if self.path is not None:
            atomic_write_json(self.path, self.to_dict())

    def to_dict(self) -> dict:
        act = self.active()
        return {"schema": SCHEMA, "updated_at": iso(utc_now()), "gates": self.gates.to_dict(),
                "active_policy_id": (act.policy_id if act else None),
                "active_rationale": ((act.policy or {}).get("rationale") if act else None),
                "active_changed_params": ((act.policy or {}).get("changed_params") if act else []),
                "active_stats": (act.stats() if act else None),
                "counts": {s: sum(1 for r in self.records if r.state == s) for s in STATES},
                "auto_promotion_possible": False,
                "note": "Bu katman CHAMPION/LIVE/TESTNET üretemez; yalnız filtreler ya da küçültür.",
                "pending": dict(self.pending),
                "records": [r.to_dict() for r in self.records]}


# --------------------------------------------------------------------------- yardımcılar
def _assert_filter_or_shrink_only(p: CandidatePolicy) -> None:
    """Kod düzeyinde son savunma hattı: aday yalnız eleyebilir ya da küçültebilir."""
    if not p.filters_enabled:
        raise ResearchSafetyError("baseline politikası araştırma adayı olamaz")
    if float(p.size_multiplier) > 1.0:
        raise ResearchSafetyError("size_multiplier > 1.0 — araştırma adayı riski YÜKSELTEMEZ")
    if p.high_vol_size_multiplier is not None and float(p.high_vol_size_multiplier) > float(p.size_multiplier):
        raise ResearchSafetyError("high_vol_size_multiplier taban çarpanı aşamaz")
    caps = (p.to_dict().get("capabilities") or {})
    if not caps.get("can_only_filter_or_shrink"):
        raise ResearchSafetyError("politika yetenek sözleşmesi ihlali")


_POLICY_FIELDS = ("seed", "min_p_win", "min_expected_net_r", "min_consensus", "size_multiplier",
                  "max_leverage_cap", "side_veto", "symbol_veto", "regime_veto", "side_penalty",
                  "agent_weights", "filters_enabled", "notes", "max_vol_regime", "max_spread_pct",
                  "min_pattern_ci_low", "max_n_dissent", "high_vol_size_multiplier", "side_regime_veto",
                  "rationale", "parent_id")


def _policy_from_dict(d: dict) -> CandidatePolicy:
    kw = {k: d[k] for k in _POLICY_FIELDS if k in d and d[k] is not None}
    kw.setdefault("seed", 0)
    return CandidatePolicy(policy_id=str(d.get("policy_id") or ""),
                           source_findings=list(d.get("source_findings") or []), **kw)


def apply_research_policy(policy: CandidatePolicy | None, snap_values: dict, *, side: str, symbol: str,
                          p_win: float, expected_net_r: float) -> dict:
    """Tek giriş noktası: aktif aday yoksa baseline (değişiklik yok) döner.

    Dönen `size_multiplier` her zaman `[0.0, 1.0]` aralığındadır — çağıran taraf bunu yalnız
    KÜÇÜLTME çarpanı olarak kullanabilir.
    """
    if policy is None:
        return {"allow": True, "size_multiplier": 1.0, "reasons": ["NO_ACTIVE_RESEARCH_POLICY"],
                "policy_id": None}
    d = policy.decide(snap_values, side=side, symbol=symbol, p_win=p_win, expected_net_r=expected_net_r)
    mult = max(0.0, min(1.0, float(d["size_multiplier"])))
    if mult > 1.0:                                            # ulaşılamaz; yine de fail-closed
        raise PolicyBoundsError("araştırma politikası boyutu YÜKSELTEMEZ")
    return {"allow": bool(d["allow"]), "size_multiplier": mult, "reasons": list(d["reasons"]),
            "policy_id": policy.policy_id}


__all__ = ["ACTIVE", "FORBIDDEN_STATES", "MANUAL_REVIEW_READY_STATE", "OFFLINE_VALIDATED", "PROPOSED",
           "RETIRED", "REVIEW", "SCHEMA", "SHADOW", "STATES", "ResearchGates", "ResearchPolicyBook",
           "ResearchRecord", "ResearchSafetyError", "apply_research_policy"]

MANUAL_REVIEW_READY_STATE = REVIEW

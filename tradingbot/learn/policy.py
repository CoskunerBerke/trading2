"""Bounded challenger policy — model KAYNAK KODU DEĞİŞTİRMEZ; yalnız sınırlandırılmış, sürümlü bir
karar politikası artefaktı üretir.

Sözleşme (kod düzeyinde zorlanır):
* Her parametrenin izin verilen aralığı `POLICY_BOUNDS` ile sabittir; dışına çıkan aday reddedilir.
* Aday hiçbir zaman risk limitini YÜKSELTEMEZ (size çarpanı ≤ 1.0), kill switch'i geçemez,
  LIVE/TESTNET açamaz, gerçek emir yolunu etkinleştiremez, açık pozisyonun stop/TP'sini değiştiremez.
* Üretim deterministiktir: aynı seed + aynı ızgara → aynı aday listesi (aynı hash).
* Politika yalnız FİLTRELER: bir adayı reddedebilir ya da boyutu KÜÇÜLTEBİLİR. Yeni işlem icat edemez.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
POLICY_VERSION = 1
POLICY_SCHEMA = "bounded_challenger_policy_v1"

# (alt, üst) — kod düzeyinde sabit; config bunları GENİŞLETEMEZ, yalnız içinde kalabilir.
POLICY_BOUNDS: dict[str, tuple[float, float]] = {
    "min_p_win": (0.0, 0.90),
    "min_expected_net_r": (-1.0, 3.0),
    "min_consensus": (0.0, 1.0),
    "size_multiplier": (0.10, 1.00),          # ASLA > 1.0 (risk artırma yasak)
    "max_leverage_cap": (1.0, 5.0),           # risk profili tavanını AŞAMAZ (aşağıdan sınırlanır)
    "agent_weight": (0.0, 2.0),
}
FORBIDDEN_KEYS = ("allow_live", "live_order_path_enabled", "testnet", "kill_switch", "risk_per_trade_pct",
                  "max_total_open_risk_pct", "max_open_positions", "stop", "targets", "source_code", "exec")


class PolicyBoundsError(ValueError):
    """Aday politika izin verilen sınırların dışına çıktı (fail-closed)."""


@dataclass
class CandidatePolicy:
    policy_id: str
    seed: int
    min_p_win: float = 0.0
    min_expected_net_r: float = 0.0
    min_consensus: float = 0.0
    size_multiplier: float = 1.0
    max_leverage_cap: float = 1.0
    side_veto: list[str] = field(default_factory=list)          # ör. ["SHORT"]
    symbol_veto: list[str] = field(default_factory=list)
    regime_veto: list[str] = field(default_factory=list)        # vol_regime_code değerleri (metin)
    side_penalty: dict[str, float] = field(default_factory=dict)
    agent_weights: dict[str, float] = field(default_factory=dict)
    filters_enabled: bool = True        # baseline=False → mevcut bot davranışı (hiçbir ek filtre yok)
    notes: str = ""

    def to_dict(self) -> dict:
        return {"schema": POLICY_SCHEMA, "policy_version": POLICY_VERSION, "policy_id": self.policy_id,
                "seed": self.seed, "min_p_win": self.min_p_win, "min_expected_net_r": self.min_expected_net_r,
                "min_consensus": self.min_consensus, "size_multiplier": self.size_multiplier,
                "max_leverage_cap": self.max_leverage_cap, "side_veto": sorted(self.side_veto),
                "symbol_veto": sorted(self.symbol_veto), "regime_veto": sorted(self.regime_veto),
                "side_penalty": dict(sorted(self.side_penalty.items())),
                "agent_weights": dict(sorted(self.agent_weights.items())),
                "filters_enabled": self.filters_enabled, "notes": self.notes,
                "capabilities": {"can_change_risk_limits": False, "can_enable_live": False,
                                 "can_modify_open_positions": False, "can_modify_source": False,
                                 "can_only_filter_or_shrink": True}}

    def hash(self) -> str:
        d = {k: v for k, v in self.to_dict().items() if k != "policy_id"}
        return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    # ---------------------------------------------------------------- uygulama
    def decide(self, snap_values: dict[str, float], *, side: str, symbol: str,
               p_win: float, expected_net_r: float) -> dict:
        """Bir aday için karar: {allow, size_multiplier, reasons}. YALNIZ reddeder ya da küçültür."""
        reasons: list[str] = []
        allow = True
        if not self.filters_enabled:                      # baseline: karar motorunun çıktısı aynen geçer
            return {"allow": True, "size_multiplier": 1.0, "effective_expected_r": round(expected_net_r, 6),
                    "reasons": ["BASELINE_PASSTHROUGH"]}
        if side.upper() in {s.upper() for s in self.side_veto}:
            allow, _ = False, reasons.append(f"SIDE_VETO:{side}")
        if symbol.upper() in {s.upper() for s in self.symbol_veto}:
            allow, _ = False, reasons.append(f"SYMBOL_VETO:{symbol}")
        reg = snap_values.get("vol_regime_code")
        if reg is not None and str(int(reg)) in {str(r) for r in self.regime_veto}:
            allow, _ = False, reasons.append(f"REGIME_VETO:{int(reg)}")
        if p_win < self.min_p_win:
            allow, _ = False, reasons.append(f"P_WIN<{self.min_p_win}")
        pen = float(self.side_penalty.get(side.upper(), 0.0))
        eff_r = expected_net_r - pen
        if eff_r < self.min_expected_net_r:
            allow, _ = False, reasons.append(f"EXP_NET_R<{self.min_expected_net_r}")
        cons = snap_values.get("consensus_score")
        if cons is not None and abs(cons) < self.min_consensus:
            allow, _ = False, reasons.append(f"CONSENSUS<{self.min_consensus}")
        return {"allow": allow, "size_multiplier": (self.size_multiplier if allow else 0.0),
                "effective_expected_r": round(eff_r, 6), "reasons": reasons}


def validate_policy(p: CandidatePolicy, *, risk_profile_max_leverage: float = 1.0,
                    risk_profile_risk_pct: float | None = None) -> None:
    """Sınır ihlallerinde `PolicyBoundsError`. Risk profili tavanı AŞILAMAZ."""
    checks = {"min_p_win": p.min_p_win, "min_expected_net_r": p.min_expected_net_r,
              "min_consensus": p.min_consensus, "size_multiplier": p.size_multiplier,
              "max_leverage_cap": p.max_leverage_cap}
    for k, val in checks.items():
        lo, hi = POLICY_BOUNDS[k]
        if not (lo <= float(val) <= hi):
            raise PolicyBoundsError(f"{k}={val} izin verilen aralık dışında [{lo}, {hi}]")
    for a, w in (p.agent_weights or {}).items():
        lo, hi = POLICY_BOUNDS["agent_weight"]
        if not (lo <= float(w) <= hi):
            raise PolicyBoundsError(f"agent_weight[{a}]={w} aralık dışında [{lo}, {hi}]")
    if p.size_multiplier > 1.0:
        raise PolicyBoundsError("size_multiplier > 1.0 — risk artırma yasak")
    if p.max_leverage_cap > float(risk_profile_max_leverage):
        raise PolicyBoundsError(f"max_leverage_cap {p.max_leverage_cap} > risk profili tavanı {risk_profile_max_leverage}")
    d = p.to_dict()
    for key in FORBIDDEN_KEYS:
        if key in d or key in (p.notes or ""):
            raise PolicyBoundsError(f"yasak anahtar politikada yer alamaz: {key}")
    if risk_profile_risk_pct is not None and p.size_multiplier * float(risk_profile_risk_pct) > float(risk_profile_risk_pct):
        raise PolicyBoundsError("etkin risk yüzdesi profil değerini aşamaz")


def generate_candidates(*, seed: int = 7, max_candidates: int = 24, risk_profile_max_leverage: float = 1.0,
                        grid: dict[str, list] | None = None) -> list[CandidatePolicy]:
    """Deterministik ızgara: aynı seed + aynı ızgara → aynı liste (sıra ve id dahil). Rastgelelik yok."""
    g = grid or {
        "min_p_win": [0.0, 0.50, 0.55],
        "min_expected_net_r": [0.0, 0.10],
        "min_consensus": [0.0, 0.20],
        "size_multiplier": [1.0, 0.75],
        "side_veto": [[], ["SHORT"]],
    }
    keys = sorted(g)
    combos = list(itertools.product(*[g[k] for k in keys]))
    combos.sort(key=lambda c: json.dumps([str(x) for x in c], sort_keys=True))     # deterministik sıra
    out: list[CandidatePolicy] = []
    for i, combo in enumerate(combos[:max_candidates]):
        params = dict(zip(keys, combo))
        p = CandidatePolicy(policy_id="", seed=int(seed),
                            min_p_win=float(params.get("min_p_win", 0.0)),
                            min_expected_net_r=float(params.get("min_expected_net_r", 0.0)),
                            min_consensus=float(params.get("min_consensus", 0.0)),
                            size_multiplier=float(params.get("size_multiplier", 1.0)),
                            max_leverage_cap=float(min(1.0, risk_profile_max_leverage)),
                            side_veto=list(params.get("side_veto", [])),
                            notes=f"grid#{i}")
        validate_policy(p, risk_profile_max_leverage=risk_profile_max_leverage)
        p.policy_id = f"cand_{seed}_{p.hash()[:10]}"
        out.append(p)
    return out


def baseline_policy(seed: int = 0) -> CandidatePolicy:
    """Mevcut davranış: hiçbir ek filtre, tam boyut (karşılaştırma tabanı)."""
    return CandidatePolicy(policy_id="baseline", seed=seed, filters_enabled=False,
                           notes="baseline: mevcut bot davranışı (filtre yok, boyut 1.0)")


__all__ = ["CandidatePolicy", "FORBIDDEN_KEYS", "POLICY_BOUNDS", "POLICY_SCHEMA", "POLICY_VERSION",
           "PolicyBoundsError", "baseline_policy", "generate_candidates", "validate_policy"]

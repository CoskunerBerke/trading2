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
    # --- kayıp analizinden türeyen filtreler (hepsi yalnız ELER ya da KÜÇÜLTÜR) ---
    "max_vol_regime": (0.0, 3.0),             # vol_regime_code bunun üstündeyse işlem reddedilir
    "max_spread_pct": (0.0, 1.0),             # spread bunu aşarsa reddedilir
    "min_pattern_ci_low": (-1.0, 0.50),       # pattern CI95 alt sınırı bunun altındaysa reddedilir
    "max_n_dissent": (0.0, 11.0),             # ajan anlaşmazlığı bunu aşarsa reddedilir
    "high_vol_size_multiplier": (0.10, 1.00), # yüksek volatilitede KÜÇÜLTME çarpanı (asla > 1.0)
}
_VOL_LABELS = ("LOW_VOL", "NORMAL", "HIGH_VOL", "EXTREME")


def _vol_label(reg) -> str | None:
    try:
        i = int(reg)
    except (TypeError, ValueError):
        return None
    return _VOL_LABELS[i] if 0 <= i < len(_VOL_LABELS) else None
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
    # --- kayıp analizinden türeyen filtreler (None = kapalı) ---
    max_vol_regime: float | None = None
    max_spread_pct: float | None = None
    min_pattern_ci_low: float | None = None
    max_n_dissent: float | None = None
    high_vol_size_multiplier: float | None = None
    side_regime_veto: list[str] = field(default_factory=list)      # "SHORT|HIGH_VOL" biçiminde
    # --- köken: davranışın parçası DEĞİL, hash'e girmez ---
    rationale: str = ""
    source_findings: list[dict] = field(default_factory=list)
    parent_id: str = "baseline"

    def changed_params(self) -> list[str]:
        """Baseline'a göre GERÇEKTEN değiştirilen davranış parametreleri (açıklanabilirlik ölçüsü).

        Aday başına az sayıda değişiklik hedeflenir; hangi değişikliğin işe yaradığı ancak böyle ölçülür.
        """
        base = CandidatePolicy(policy_id="", seed=self.seed)
        return sorted(k for k in ("min_p_win", "min_expected_net_r", "min_consensus", "size_multiplier",
                                  "side_veto", "symbol_veto", "regime_veto", "side_penalty", "agent_weights",
                                  "max_vol_regime", "max_spread_pct", "min_pattern_ci_low", "max_n_dissent",
                                  "high_vol_size_multiplier", "side_regime_veto")
                      if getattr(self, k) != getattr(base, k))

    def to_dict(self) -> dict:
        return {"schema": POLICY_SCHEMA, "policy_version": POLICY_VERSION, "policy_id": self.policy_id,
                "seed": self.seed, "min_p_win": self.min_p_win, "min_expected_net_r": self.min_expected_net_r,
                "min_consensus": self.min_consensus, "size_multiplier": self.size_multiplier,
                "max_leverage_cap": self.max_leverage_cap, "side_veto": sorted(self.side_veto),
                "symbol_veto": sorted(self.symbol_veto), "regime_veto": sorted(self.regime_veto),
                "side_penalty": dict(sorted(self.side_penalty.items())),
                "agent_weights": dict(sorted(self.agent_weights.items())),
                "filters_enabled": self.filters_enabled, "notes": self.notes,
                "max_vol_regime": self.max_vol_regime, "max_spread_pct": self.max_spread_pct,
                "min_pattern_ci_low": self.min_pattern_ci_low, "max_n_dissent": self.max_n_dissent,
                "high_vol_size_multiplier": self.high_vol_size_multiplier,
                "side_regime_veto": sorted(self.side_regime_veto),
                "rationale": self.rationale, "source_findings": list(self.source_findings),
                "parent_id": self.parent_id, "changed_params": self.changed_params(),
                "capabilities": {"can_change_risk_limits": False, "can_enable_live": False,
                                 "can_modify_open_positions": False, "can_modify_source": False,
                                 "can_only_filter_or_shrink": True}}

    _PROVENANCE = ("policy_id", "rationale", "source_findings", "parent_id", "notes", "changed_params")

    def hash(self) -> str:
        """Kimlik = DAVRANIŞ. Köken alanları (gerekçe/bulgu/parent/not) hash'e girmez: aynı filtreleri
        uygulayan iki aday aynı politikadır."""
        d = {k: v for k, v in self.to_dict().items() if k not in self._PROVENANCE}
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
        # --- kayıp analizinden türeyen filtreler (hepsi yalnız REDDEDER) ---
        reg_lab = _vol_label(reg)
        if self.side_regime_veto and reg_lab and f"{side.upper()}|{reg_lab}" in {v.upper() for v in self.side_regime_veto}:
            allow, _ = False, reasons.append(f"SIDE_REGIME_VETO:{side.upper()}|{reg_lab}")
        if self.max_vol_regime is not None and reg is not None and float(reg) > float(self.max_vol_regime):
            allow, _ = False, reasons.append(f"VOL_REGIME>{self.max_vol_regime}")
        sp = snap_values.get("spread_pct")
        if self.max_spread_pct is not None and sp is not None and float(sp) > float(self.max_spread_pct):
            allow, _ = False, reasons.append(f"SPREAD>{self.max_spread_pct}")
        pci = snap_values.get("pattern_ci_low")
        if self.min_pattern_ci_low is not None and pci is not None and float(pci) < float(self.min_pattern_ci_low):
            allow, _ = False, reasons.append(f"PATTERN_CI_LOW<{self.min_pattern_ci_low}")
        nd = snap_values.get("n_dissent")
        if self.max_n_dissent is not None and nd is not None and float(nd) > float(self.max_n_dissent):
            allow, _ = False, reasons.append(f"DISSENT>{self.max_n_dissent}")
        # KÜÇÜLTME (reddetme değil): yüksek volatilitede boyut düşürülür; ASLA yükseltilmez
        mult = float(self.size_multiplier)
        if allow and self.high_vol_size_multiplier is not None and reg is not None and float(reg) >= 2.0:
            mult = min(mult, float(self.high_vol_size_multiplier))
            reasons.append(f"HIGH_VOL_SHRINK:{mult}")
        return {"allow": allow, "size_multiplier": (mult if allow else 0.0),
                "effective_expected_r": round(eff_r, 6), "reasons": reasons}


def validate_policy(p: CandidatePolicy, *, risk_profile_max_leverage: float = 1.0,
                    risk_profile_risk_pct: float | None = None) -> None:
    """Sınır ihlallerinde `PolicyBoundsError`. Risk profili tavanı AŞILAMAZ."""
    checks = {"min_p_win": p.min_p_win, "min_expected_net_r": p.min_expected_net_r,
              "min_consensus": p.min_consensus, "size_multiplier": p.size_multiplier,
              "max_leverage_cap": p.max_leverage_cap}
    for k in ("max_vol_regime", "max_spread_pct", "min_pattern_ci_low", "max_n_dissent",
              "high_vol_size_multiplier"):
        if getattr(p, k) is not None:
            checks[k] = getattr(p, k)
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
    if p.high_vol_size_multiplier is not None and p.high_vol_size_multiplier > p.size_multiplier:
        raise PolicyBoundsError("high_vol_size_multiplier taban çarpanı AŞAMAZ (yalnız küçültme)")
    for sv in (p.side_regime_veto or []):
        if "|" not in str(sv):
            raise PolicyBoundsError(f"side_regime_veto biçimi 'TARAF|REJIM' olmalı: {sv!r}")
    if p.max_leverage_cap > float(risk_profile_max_leverage):
        raise PolicyBoundsError(f"max_leverage_cap {p.max_leverage_cap} > risk profili tavanı {risk_profile_max_leverage}")
    d = p.to_dict()
    text = f"{p.notes or ''} {p.rationale or ''}"
    for key in FORBIDDEN_KEYS:
        if key in d or key in text:
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


# --------------------------------------------------------------------------- bulgudan aday üretimi
MAX_CHANGES_PER_CANDIDATE = 2
MIN_FINDING_SAMPLES = 8


def candidates_from_attribution(report: dict, *, seed: int = 7, max_candidates: int = 12,
                                risk_profile_max_leverage: float = 1.0,
                                min_samples: int = MIN_FINDING_SAMPLES) -> list[CandidatePolicy]:
    """`attribution_report()` bulgularından deterministik, sınırlandırılmış, AÇIKLANABİLİR adaylar.

    Yalnız `direction == "NEGATIF"` ve yeterli örnekli bulgular aday üretir. Her aday en fazla
    `MAX_CHANGES_PER_CANDIDATE` davranış parametresi değiştirir ve gerekçesini + kaynak bulgusunu taşır;
    böylece hangi değişikliğin işe yaradığı OOS'ta tek tek ölçülebilir. Aday riski YÜKSELTEMEZ.
    """
    neg = [f for f in (report or {}).get("findings", [])
           if f.get("direction") == "NEGATIF" and int(f.get("n") or 0) >= min_samples]
    neg.sort(key=lambda f: (f.get("delta_vs_baseline_r", 0.0), f.get("cut", ""), f.get("label", "")))
    cap = float(min(1.0, risk_profile_max_leverage))
    out: list[CandidatePolicy] = []
    seen: set[str] = set()

    def emit(rationale: str, finding: dict, **params) -> None:
        if len(out) >= max_candidates:
            return
        p = CandidatePolicy(policy_id="", seed=int(seed), max_leverage_cap=cap, rationale=rationale,
                            source_findings=[{k: finding.get(k) for k in
                                              ("cut", "label", "n", "expectancy_r", "ci95_low",
                                               "delta_vs_baseline_r")}], **params)
        if len(p.changed_params()) > MAX_CHANGES_PER_CANDIDATE:
            return
        validate_policy(p, risk_profile_max_leverage=risk_profile_max_leverage)
        h = p.hash()
        if h in seen:
            return
        seen.add(h)
        p.policy_id = f"attr_{seed}_{h[:10]}"
        out.append(p)

    for f in neg:
        cut, lab = str(f.get("cut")), str(f.get("label"))
        txt = f.get("text") or f"{cut}={lab}"
        if cut == "side" and lab in ("LONG", "SHORT"):
            emit(f"{lab} tarafı OOS'ta negatif → taraf vetosu · {txt}", f, side_veto=[lab])
        elif cut == "symbol" and lab != "?":
            emit(f"{lab} sembolü OOS'ta negatif → sembol vetosu · {txt}", f, symbol_veto=[lab])
        elif cut == "vol_regime" and lab in ("HIGH_VOL", "EXTREME"):
            emit(f"{lab} rejiminde negatif → rejim üstü işlem reddi · {txt}", f,
                 max_vol_regime=float(_VOL_LABELS.index(lab)) - 1.0)
            emit(f"{lab} rejiminde negatif → boyut küçültme · {txt}", f, high_vol_size_multiplier=0.5)
        elif cut == "side_x_regime" and "|" in lab:
            emit(f"{lab} kombinasyonu negatif → taraf×rejim vetosu · {txt}", f, side_regime_veto=[lab])
        elif cut == "liquidity" and lab in ("SPREAD_ORTA", "SPREAD_GENİŞ"):
            emit(f"Geniş spread'de negatif → spread tavanı · {txt}", f,
                 max_spread_pct=(0.15 if lab == "SPREAD_GENİŞ" else 0.05))
        elif cut == "pattern_conf" and lab in ("PATTERN_NEGATİF", "PATTERN_ZAYIF"):
            emit(f"Düşük pattern güveninde negatif → pattern CI tabanı · {txt}", f, min_pattern_ci_low=0.0)
        elif cut == "agent_disagreement" and lab in ("DISSENT_AZ", "DISSENT_ÇOK"):
            emit(f"Yüksek ajan anlaşmazlığında negatif → dissent tavanı · {txt}", f,
                 max_n_dissent=(2.0 if lab == "DISSENT_ÇOK" else 0.0))
        elif cut == "consensus" and lab == "KONSENSÜS_ZAYIF":
            emit(f"Zayıf konsensüste negatif → konsensüs tabanı · {txt}", f, min_consensus=0.2)
    base = (report or {}).get("baseline") or {}
    if base.get("expectancy_r") is not None and base["expectancy_r"] < 0 and int(base.get("n") or 0) >= min_samples:
        f = {"cut": "baseline", "label": "ALL", "n": base.get("n"), "expectancy_r": base.get("expectancy_r"),
             "ci95_low": base.get("ci95_low"), "delta_vs_baseline_r": 0.0}
        emit("Genel beklenti negatif → minimum beklenen net R eşiği yükseltiliyor", f, min_expected_net_r=0.10)
        emit("Genel beklenti negatif → minimum p_win eşiği yükseltiliyor", f, min_p_win=0.55)
    return out


def baseline_policy(seed: int = 0) -> CandidatePolicy:
    """Mevcut davranış: hiçbir ek filtre, tam boyut (karşılaştırma tabanı)."""
    return CandidatePolicy(policy_id="baseline", seed=seed, filters_enabled=False,
                           notes="baseline: mevcut bot davranışı (filtre yok, boyut 1.0)")


__all__ = ["CandidatePolicy", "FORBIDDEN_KEYS", "MAX_CHANGES_PER_CANDIDATE", "MIN_FINDING_SAMPLES",
           "POLICY_BOUNDS", "POLICY_SCHEMA", "POLICY_VERSION", "PolicyBoundsError", "baseline_policy",
           "candidates_from_attribution", "generate_candidates", "validate_policy"]

"""Kârlılık deneyi (`profitability_experiment_v1`) — İZOLE, İLERİYE DÖNÜK, SHADOW PAPER.

Bu modül **yeni bir strateji eklemez**. Sistemde zaten bulunan politikaları, aynı doğal
adaylar üzerinde, aynı maliyet modeliyle, adil ve ölçülebilir bir portföy yarışmasına
çevirir.

**Bu deney kâr vaat etmez ve kârı kanıtlamaz.** Amacı tek bir sorudur: sabit politikalardan
hangisi — varsa — maliyet sonrası POZİTİF İLERİYE DÖNÜK beklenti üretir? Bir challenger'ın
tek bir kaybedeni elemesi başarı SAYILMAZ.

**İzolasyon sözleşmesi.** Bu modül saftır ve yalnız kendi durumuna yazar. Kanonik futures/
spot defterine, RiskEngine'e, muhasebeye, gateway/emir yoluna, sermaye durumuna, öğrenme
ağırlıklarına ve mevcut giriş/çıkış raporlarına **DOKUNMAZ**. `applied` daima `False`tur.

**FİLTRE-ONLY.** Challenger'lar yalnız şampiyonun ZATEN kabul ettiği girişleri elemek ya da
aynen aynalamak için vardır. Şampiyonun reddettiği bir işlem **simüle EDİLMEZ**: aksi hâlde
hiç var olmamış bir dolum uydurulur ve karşılaştırılabilirlik kaybolur.

**Eksik veri sıfır DEĞİLDİR.** Fiyat ya da gerekli alan yoksa politika `ABSTAIN` eder;
uydurma yapılmaz.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Iterable

from ..core import iso, stable_id, utc_now

SCHEMA_VERSION = "profitability_experiment_v1"

# --- politikalar (TAM BEŞ, DONMUŞ) --------------------------------------------------------
P0 = "P0_CHAMPION_MIRROR"
P1 = "P1_SELECTIVE_AE"
P2 = "P2_DIRECTIONAL_DIVERSIFICATION"
P3 = "P3_PROFIT_PROTECTION"
P4 = "P4_COMBINED"
POLICIES: tuple[str, ...] = (P0, P1, P2, P3, P4)
N_TRIALS = len(POLICIES)

# --- giriş kararları ----------------------------------------------------------------------
ACCEPT = "ACCEPT"
FILTER = "FILTER"
ABSTAIN = "ABSTAIN"
DECISIONS = (ACCEPT, FILTER, ABSTAIN)

# --- kanıt sınıfları -----------------------------------------------------------------------
PRE_EXPERIMENT = "PRE_EXPERIMENT_OBSERVATION_ONLY"
IN_EXPERIMENT = "IN_EXPERIMENT"

# --- gerekçe kodları -----------------------------------------------------------------------
R_MIRROR = "MIRRORS_CHAMPION"
R_AE_VETO = "ENTRY_FAMILY_A_OR_E_VETO"
R_AE_UNKNOWN = "ENTRY_FAMILY_DECISION_UNAVAILABLE"
R_DIR_SHARE = "SAME_DIRECTION_RISK_SHARE_EXCEEDED"
R_CLUSTER_SHARE = "CORRELATION_CLUSTER_RISK_SHARE_EXCEEDED"
R_CLUSTER_COUNT = "CORRELATION_CLUSTER_POSITION_COUNT_EXCEEDED"
R_CORR_UNKNOWN = "CORRELATION_UNKNOWN_INSUFFICIENT_OVERLAP"
R_NO_PRICE = "ENTRY_PRICE_UNAVAILABLE"
R_NO_RISK = "RISK_UNMEASURABLE"
R_PRE_EXPERIMENT = "OPENED_BEFORE_EVALUATION_START"
R_NOT_CHAMPION_ACCEPTED = "CHAMPION_DID_NOT_ACCEPT"
R_OK = "PASSES_POLICY"

# --- çıkış nedenleri (simülasyon) -----------------------------------------------------------
X_CANONICAL = "CANONICAL_CLOSE_MIRRORED"
X_POLICY_EXIT = "POLICY_EXIT_ON_PATH"
X_POLICY_STOP = "POLICY_TIGHTENED_STOP_HIT"
X_OPEN = "STILL_OPEN"

#: Kayma kaynağı — ölçüldü / modellendi / yok. Asla sessizce sıfırlanmaz.
SLIP_MEASURED = "MEASURED"
SLIP_MODELED = "MODELED"
SLIP_MISSING = "MISSING"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _s(x: Any) -> str | None:
    return None if x is None else str(x)


def _dt(x: Any) -> datetime | None:
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _side_sign(side: Any) -> float | None:
    s = str(side or "").upper()
    if s.endswith("LONG"):
        return 1.0
    if s.endswith("SHORT"):
        return -1.0
    return None


# ======================================================================= donmuş yapılandırma

@dataclass(frozen=True)
class ExperimentConfig:
    """Deney kimliği ve DONMUŞ eşikler.

    Bütün eşikler **ileriye dönük sonuçlar görülmeden ÖNCE** sabitlenmiştir ve
    `config_id` içine girer. Değerler risk profilinin KENDİ bütçesinden türetilmiştir;
    23 tarihsel kapanışa göre optimize EDİLMEMİŞTİR (bkz. `docs/PROFITABILITY_EXPERIMENT_V1.md`).
    """
    experiment_id: str = "pfexp_v1"
    policy_version: str = "pfexp_v1.0.0"
    #: Bu andan ÖNCE açılmış her pozisyon `PRE_EXPERIMENT_OBSERVATION_ONLY`dir.
    evaluation_start_at: str = ""
    frozen_at: str = ""
    code_sha: str | None = None

    # --- P2: yön / korelasyon yoğunlaşma tavanları -------------------------------------
    #: Aynı yöndeki açık riskin toplam açık riske azami oranı. Risk profilinin
    #: `max_total_open_risk_pct` bütçesinin çoğunluğu tek yöne gidemez ilkesi.
    max_same_direction_risk_share: float = 0.60
    #: Tek bir ölçülmüş korelasyon kümesinin azami risk payı.
    max_cluster_risk_share: float = 0.35
    #: Tek kümede azami eşzamanlı pozisyon.
    max_positions_per_cluster: int = 3
    #: Korelasyon hesabı için gereken asgari ÖRTÜŞEN kapanmış 1s barı.
    correlation_min_overlap: int = 30
    #: Bu eşiğin üstündeki |korelasyon| aynı küme sayılır.
    correlation_cluster_threshold: float = 0.60
    #: Korelasyonda kullanılacak azami geriye dönük kapanmış bar.
    correlation_lookback_bars: int = 120

    # --- erken yön göstergesi / terfi ---------------------------------------------------
    #: Bilgilendirici erken sinyalin görünmeye başladığı karşılaştırılabilir kapanış.
    early_directionality_min_closes: int = 10
    #: Terfi için asgari karşılaştırılabilir kapanış (mevcut kapı KORUNUR).
    promotion_min_closes: int = 50
    #: Terfi için asgari takvim günü (mevcut kapı KORUNUR).
    promotion_min_days: int = 30

    def validate(self) -> None:
        if not (0.0 < self.max_same_direction_risk_share <= 1.0):
            raise ValueError("max_same_direction_risk_share (0,1] olmalı")
        if not (0.0 < self.max_cluster_risk_share <= 1.0):
            raise ValueError("max_cluster_risk_share (0,1] olmalı")
        if self.max_positions_per_cluster < 1:
            raise ValueError("max_positions_per_cluster >= 1 olmalı")
        if self.correlation_min_overlap < 5:
            raise ValueError("correlation_min_overlap >= 5 olmalı")
        if not (0.0 < self.correlation_cluster_threshold < 1.0):
            raise ValueError("correlation_cluster_threshold (0,1) olmalı")
        if self.correlation_lookback_bars < self.correlation_min_overlap:
            raise ValueError("lookback >= min_overlap olmalı")
        if self.promotion_min_closes < 50 or self.promotion_min_days < 30:
            raise ValueError("MEVCUT TERFİ KAPILARI GEVŞETİLEMEZ (>=50 kapanış, >=30 gün)")
        if self.early_directionality_min_closes < 1:
            raise ValueError("early_directionality_min_closes >= 1 olmalı")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ExperimentConfig":
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in dict(d or {}).items() if k in allowed})
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        """Sonuçlardan BAĞIMSIZ kimlik: yalnız eşikler + sürüm + deney kimliği."""
        d = {k: v for k, v in self.to_dict().items() if k not in ("frozen_at", "code_sha")}
        return stable_id("pfexpcfg", self.policy_version, d)

    def identity(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "experiment_id": self.experiment_id,
                "policy_version": self.policy_version, "config_id": self.config_id,
                "code_sha": self.code_sha, "frozen_at": self.frozen_at,
                "evaluation_start_at": self.evaluation_start_at,
                "number_of_trials": N_TRIALS, "policies": list(POLICIES)}


# =========================================================================== korelasyon

def closed_returns(bars: Any, *, as_of_ms: int | None, lookback: int) -> list[float]:
    """KAPANMIŞ 1s barlardan getiri serisi. Yeni sağlayıcı isteği YOK.

    `bar.close_time <= as_of_ms` koşulunu sağlamayan bar KULLANILMAZ; kapanmamış ya da
    gelecek bar sessizce içeri alınmaz.
    """
    from .multitimeframe_context import closed_bars_as_of
    rows, _meta = closed_bars_as_of(bars, frame_key="1h", as_of_ms=as_of_ms)
    rows = rows[-max(2, int(lookback)):]
    out: list[float] = []
    for i in range(1, len(rows)):
        p0, p1 = rows[i - 1]["close"], rows[i]["close"]
        if p0 and p0 > 0:
            out.append((p1 - p0) / p0)
    return out


def correlation(a: list[float], b: list[float], *, min_overlap: int) -> float | None:
    """Pearson korelasyonu. Örtüşme yetersizse `None` — SIFIR DEĞİL."""
    n = min(len(a), len(b))
    if n < max(2, int(min_overlap)):
        return None
    x, y = a[-n:], b[-n:]
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((u - mx) * (v - my) for u, v in zip(x, y))
    sxx = sum((u - mx) ** 2 for u in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0 or syy <= 0:
        return None
    r = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, round(r, 6)))


# =============================================================== simüle portföy defteri

@dataclass
class SimPosition:
    """Simüle pozisyon. Kanonik defterle HİÇBİR bağı yoktur."""
    trade_id: str
    policy: str
    symbol: str
    side: str
    qty: float
    entry: float
    initial_stop: float
    stop: float
    targets: list[float]
    leverage: float | None
    risk_usdt: float
    opened_at: str
    entry_fee: float
    slippage_cost: float | None
    slippage_provenance: str
    candidate_id: str | None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    reduces_done: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class SimClose:
    trade_id: str
    policy: str
    symbol: str
    side: str
    opened_at: str
    closed_at: str
    exit_kind: str
    exit_price: float
    r_multiple: float
    net_pnl: float
    fees: float
    funding: float
    slippage_cost: float | None
    slippage_provenance: str
    mfe_r: float | None
    mae_r: float | None
    risk_usdt: float

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# =============================================================== giriş kararı (FİLTRE-ONLY)

def _ae_decision(candidate: dict[str, Any]) -> tuple[str | None, str | None]:
    """A ve E ailelerinin KARAR ANI çıktısı. Yoksa `None` — ABSTAIN'e yol açar.

    Mevcut A/E sürümü ve eşikleri **YENİDEN AYARLANMAZ**; snapshot'ta ne yazıyorsa o.
    `ABSTAIN`/`UNKNOWN` **VETO'ya çevrilmez**.
    """
    fam = candidate.get("entry_families")
    if not isinstance(fam, dict) or not fam:
        return None, None
    def pick(prefix: str) -> str | None:
        for k, v in fam.items():
            if str(k).upper().startswith(prefix):
                d = (v.get("decision") if isinstance(v, dict) else v)
                return str(d) if d is not None else None
        return None
    return pick("A"), pick("E")


def decide_p1(candidate: dict[str, Any], cfg: ExperimentConfig) -> dict[str, Any]:
    """P1 — yalnız A ya da E kesin `VETO` derse filtreler."""
    a, e = _ae_decision(candidate)
    if a is None and e is None:
        return {"decision": ABSTAIN, "reason_codes": [R_AE_UNKNOWN],
                "evidence": {"A": a, "E": e}}
    vetoed = [n for n, d in (("A", a), ("E", e)) if str(d).upper() == "VETO"]
    if vetoed:
        return {"decision": FILTER, "reason_codes": [R_AE_VETO],
                "evidence": {"A": a, "E": e, "vetoed_by": vetoed}}
    return {"decision": ACCEPT, "reason_codes": [R_OK], "evidence": {"A": a, "E": e}}


def decide_p2(candidate: dict[str, Any], book: "PolicyBook", cfg: ExperimentConfig
              ) -> dict[str, Any]:
    """P2 — aynı yön ve korelasyon kümesi yoğunlaşmasını sınırlar.

    Yalnız GİRİŞTEN ÖNCE bilinen bilgi kullanılır: mevcut simüle açık pozisyonlar ve
    karar anına kadar KAPANMIŞ 1s barlardan hesaplanan korelasyon. Korelasyon
    ölçülemezse küme kısıtı UYGULANMAZ ve bu açıkça `UNKNOWN` olarak raporlanır.
    """
    risk = _f(candidate.get("risk_usdt"))
    if risk is None or risk <= 0:
        return {"decision": ABSTAIN, "reason_codes": [R_NO_RISK], "evidence": {}}
    side = str(candidate.get("side") or "").upper()
    open_pos = list(book.positions.values())
    total_risk = sum(p.risk_usdt for p in open_pos) + risk
    same_dir_risk = sum(p.risk_usdt for p in open_pos
                        if str(p.side).upper() == side) + risk
    share = (same_dir_risk / total_risk) if total_risk > 0 else None
    ev: dict[str, Any] = {"same_direction_risk": round(same_dir_risk, 6),
                          "total_risk": round(total_risk, 6),
                          "same_direction_share": (round(share, 6) if share else None),
                          "limit": cfg.max_same_direction_risk_share,
                          "n_open": len(open_pos)}
    # Tek pozisyonluk portföyde "aynı yön payı" daima 1.0'dır; bu bir yoğunlaşma
    # DEĞİLDİR. Kısıt ancak birden fazla pozisyon varken anlamlıdır.
    if len(open_pos) >= 1 and share is not None and share > cfg.max_same_direction_risk_share:
        return {"decision": FILTER, "reason_codes": [R_DIR_SHARE], "evidence": ev}

    rets = candidate.get("returns_1h")
    corrs: dict[str, float | None] = {}
    cluster_risk = risk
    cluster_n = 1
    unknown = 0
    for p in open_pos:
        other = (book.returns.get(p.symbol) if isinstance(book.returns, dict) else None)
        c = (correlation(rets, other, min_overlap=cfg.correlation_min_overlap)
             if (isinstance(rets, list) and isinstance(other, list)) else None)
        corrs[p.symbol] = c
        if c is None:
            unknown += 1
            continue
        if abs(c) >= cfg.correlation_cluster_threshold:
            cluster_risk += p.risk_usdt
            cluster_n += 1
    ev.update({"correlations": corrs, "n_correlation_unknown": unknown,
               "cluster_positions": cluster_n,
               "cluster_risk": round(cluster_risk, 6),
               "cluster_share": (round(cluster_risk / total_risk, 6) if total_risk > 0 else None),
               "cluster_share_limit": cfg.max_cluster_risk_share,
               "cluster_count_limit": cfg.max_positions_per_cluster})
    if unknown and unknown == len(open_pos) and open_pos:
        ev["correlation_state"] = "UNKNOWN"
        ev.setdefault("note", "Korelasyon ölçülemedi; küme kısıtı UYGULANMADI.")
        return {"decision": ACCEPT, "reason_codes": [R_OK, R_CORR_UNKNOWN], "evidence": ev}
    if cluster_n > cfg.max_positions_per_cluster:
        return {"decision": FILTER, "reason_codes": [R_CLUSTER_COUNT], "evidence": ev}
    if total_risk > 0 and (cluster_risk / total_risk) > cfg.max_cluster_risk_share \
            and cluster_n > 1:
        return {"decision": FILTER, "reason_codes": [R_CLUSTER_SHARE], "evidence": ev}
    codes = [R_OK] + ([R_CORR_UNKNOWN] if unknown else [])
    return {"decision": ACCEPT, "reason_codes": codes, "evidence": ev}


def decide_entry(policy: str, candidate: dict[str, Any], book: "PolicyBook",
                 cfg: ExperimentConfig) -> dict[str, Any]:
    """Bir politikanın giriş kararı. **Yalnız şampiyonun kabul ettiği aday** girdi olabilir."""
    base = {"policy": policy, "trade_id": candidate.get("trade_id"),
            "candidate_id": candidate.get("candidate_id"),
            "symbol": candidate.get("symbol"), "side": candidate.get("side"),
            "as_of": candidate.get("opened_at"), "applied": False}
    if not candidate.get("champion_accepted"):
        return base | {"decision": FILTER, "reason_codes": [R_NOT_CHAMPION_ACCEPTED],
                       "evidence": {}}
    if _f(candidate.get("entry")) is None:
        return base | {"decision": ABSTAIN, "reason_codes": [R_NO_PRICE], "evidence": {}}
    if _f(candidate.get("risk_usdt")) is None:
        return base | {"decision": ABSTAIN, "reason_codes": [R_NO_RISK], "evidence": {}}

    if policy in (P0, P3):
        return base | {"decision": ACCEPT, "reason_codes": [R_MIRROR], "evidence": {}}
    if policy == P1:
        return base | decide_p1(candidate, cfg)
    if policy == P2:
        return base | decide_p2(candidate, book, cfg)
    if policy == P4:
        d1 = decide_p1(candidate, cfg)
        if d1["decision"] == FILTER:
            return base | d1
        d2 = decide_p2(candidate, book, cfg)
        if d2["decision"] == FILTER:
            return base | d2
        if ABSTAIN in (d1["decision"], d2["decision"]):
            return base | {"decision": ABSTAIN,
                           "reason_codes": sorted(set(d1["reason_codes"] + d2["reason_codes"])),
                           "evidence": {"P1": d1.get("evidence"), "P2": d2.get("evidence")}}
        return base | {"decision": ACCEPT,
                       "reason_codes": sorted(set(d1["reason_codes"] + d2["reason_codes"])),
                       "evidence": {"P1": d1.get("evidence"), "P2": d2.get("evidence")}}
    return base | {"decision": ABSTAIN, "reason_codes": ["UNKNOWN_POLICY"], "evidence": {}}


# =========================================================================== defter

class PolicyBook:
    """Tek bir politikanın simüle portföyü. Kanonik defterle bağı YOKTUR."""

    def __init__(self, policy: str) -> None:
        self.policy = policy
        self.positions: dict[str, SimPosition] = {}
        self.closes: list[SimClose] = []
        self.returns: dict[str, list[float]] = {}
        self.n_accept = self.n_filter = self.n_abstain = 0

    def to_dict(self) -> dict[str, Any]:
        return {"policy": self.policy,
                "positions": {k: v.to_dict() for k, v in sorted(self.positions.items())},
                "closes": [c.to_dict() for c in self.closes],
                "counters": {"accept": self.n_accept, "filter": self.n_filter,
                             "abstain": self.n_abstain}}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyBook":
        b = cls(str(d.get("policy")))
        for k, v in (d.get("positions") or {}).items():
            b.positions[k] = SimPosition(**v)
        for c in (d.get("closes") or []):
            b.closes.append(SimClose(**c))
        ctr = d.get("counters") or {}
        b.n_accept = int(ctr.get("accept", 0))
        b.n_filter = int(ctr.get("filter", 0))
        b.n_abstain = int(ctr.get("abstain", 0))
        return b


def open_simulated(book: PolicyBook, candidate: dict[str, Any]) -> SimPosition:
    """Şampiyon girişini AYNEN aynalar: fiyat, miktar, stop, hedef ve maliyet modeli aynıdır."""
    slip = _f(candidate.get("slippage_cost"))
    prov = SLIP_MEASURED if slip is not None else SLIP_MISSING
    p = SimPosition(
        trade_id=str(candidate.get("trade_id")), policy=book.policy,
        symbol=str(candidate.get("symbol")), side=str(candidate.get("side")),
        qty=float(_f(candidate.get("qty")) or 0.0),
        entry=float(_f(candidate.get("entry"))),
        initial_stop=float(_f(candidate.get("initial_stop")) or 0.0),
        stop=float(_f(candidate.get("initial_stop")) or 0.0),
        targets=[float(t) for t in (candidate.get("targets") or []) if _f(t) is not None],
        leverage=_f(candidate.get("leverage")),
        risk_usdt=float(_f(candidate.get("risk_usdt"))),
        opened_at=str(candidate.get("opened_at")),
        entry_fee=float(_f(candidate.get("entry_fee")) or 0.0),
        slippage_cost=slip, slippage_provenance=prov,
        candidate_id=_s(candidate.get("candidate_id")))
    book.positions[p.trade_id] = p
    if isinstance(candidate.get("returns_1h"), list):
        book.returns[p.symbol] = list(candidate["returns_1h"])
    return p


def close_simulated(book: PolicyBook, trade_id: str, *, exit_price: float, closed_at: str,
                    exit_kind: str, fees: float, funding: float,
                    mfe_r: float | None = None, mae_r: float | None = None) -> SimClose | None:
    """Simüle kapanış. R ve PnL şampiyonla AYNI maliyet modeliyle hesaplanır."""
    p = book.positions.pop(str(trade_id), None)
    if p is None:
        return None
    sign = _side_sign(p.side) or 1.0
    gross = (exit_price - p.entry) * sign * p.qty
    net = gross - abs(fees) + funding
    r = (net / p.risk_usdt) if p.risk_usdt else 0.0
    c = SimClose(trade_id=p.trade_id, policy=book.policy, symbol=p.symbol, side=p.side,
                 opened_at=p.opened_at, closed_at=str(closed_at), exit_kind=exit_kind,
                 exit_price=float(exit_price), r_multiple=round(r, 6),
                 net_pnl=round(net, 8), fees=round(abs(fees), 8), funding=round(funding, 8),
                 slippage_cost=p.slippage_cost, slippage_provenance=p.slippage_provenance,
                 mfe_r=(round(mfe_r, 6) if mfe_r is not None else p.mfe_r),
                 mae_r=(round(mae_r, 6) if mae_r is not None else p.mae_r),
                 risk_usdt=p.risk_usdt)
    book.closes.append(c)
    return c


def apply_mark(book: PolicyBook, trade_id: str, mark: float) -> None:
    """MFE/MAE güncellemesi — YALNIZ kanonik marka ile. Fiyat uydurulmaz."""
    p = book.positions.get(str(trade_id))
    m = _f(mark)
    if p is None or m is None or not p.risk_usdt:
        return
    sign = _side_sign(p.side) or 1.0
    r = (m - p.entry) * sign * p.qty / p.risk_usdt
    p.mfe_r = max(p.mfe_r, round(r, 6))
    p.mae_r = min(p.mae_r, round(r, 6))


# =========================================================================== olay defteri

def event_id(cfg: ExperimentConfig, policy: str, kind: str, *keys: Any) -> str:
    """DETERMİNİSTİK olay kimliği: aynı girdi aynı kimliği verir → idempotent replay."""
    return stable_id("pfexpev", cfg.experiment_id, cfg.config_id, policy, kind, *[str(k) for k in keys])


def make_event(cfg: ExperimentConfig, policy: str, kind: str, payload: dict[str, Any],
               *keys: Any, now=None) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "experiment_id": cfg.experiment_id,
            "policy_version": cfg.policy_version, "config_id": cfg.config_id,
            "code_sha": cfg.code_sha, "policy": policy, "kind": kind,
            "event_id": event_id(cfg, policy, kind, *keys),
            "recorded_at": iso(now or utc_now()), "applied": False, "payload": payload}


# =========================================================================== metrikler

def _stats(rs: list[float]) -> dict[str, Any]:
    if not rs:
        return {"n": 0, "win_rate": None, "expectancy_r": None, "profit_factor": None,
                "profit_factor_state": "no_data", "total_r": None, "max_drawdown_r": None,
                "cvar5_r": None, "avg_winner_r": None, "avg_loser_r": None}
    w = [v for v in rs if v > 0]
    l = [v for v in rs if v < 0]
    gp, gl = sum(w), -sum(l)
    if gl > 0:
        pf, pfs = round(gp / gl, 6), "ok"
    elif w:
        pf, pfs = None, "no_losses"
    else:
        pf, pfs = None, "no_data"
    eq = peak = dd = 0.0
    for v in rs:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    k = max(1, int(round(len(rs) * 0.05)))
    return {"n": len(rs), "win_rate": round(len(w) / len(rs), 6),
            "expectancy_r": round(sum(rs) / len(rs), 6),
            "profit_factor": pf, "profit_factor_state": pfs,
            "total_r": round(sum(rs), 6), "max_drawdown_r": round(dd, 6),
            "cvar5_r": round(sum(sorted(rs)[:k]) / k, 6),
            "avg_winner_r": (round(sum(w) / len(w), 6) if w else None),
            "avg_loser_r": (round(-sum(l) / len(l), 6) if l else None)}


def bootstrap_ci(vals: list[float], *, n_boot: int = 2000, alpha: float = 0.05
                 ) -> dict[str, Any]:
    """Deterministik bootstrap GA — tohum verinin kendisinden türer."""
    if len(vals) < 2:
        return {"state": "LOW_SAMPLE", "lo": None, "hi": None, "excludes_zero": False}
    import random
    rnd = random.Random(stable_id("pfexpci", [round(v, 8) for v in vals]))
    means = []
    for _ in range(n_boot):
        s = [vals[rnd.randrange(len(vals))] for _ in vals]
        means.append(sum(s) / len(s))
    means.sort()
    lo, hi = means[int(alpha / 2 * n_boot)], means[int((1 - alpha / 2) * n_boot)]
    return {"state": "ok", "lo": round(lo, 6), "hi": round(hi, 6),
            "excludes_zero": bool(lo > 0 or hi < 0), "n_boot": n_boot}


def sidak_alpha(alpha: float = 0.05, n: int = N_TRIALS) -> float:
    """Beş politika aynı anda test ediliyor: çoklu karşılaştırma düzeltmesi ZORUNLU."""
    return round(1.0 - (1.0 - alpha) ** (1.0 / max(1, n)), 6)


def policy_report(book: PolicyBook, cfg: ExperimentConfig) -> dict[str, Any]:
    rs = [c.r_multiple for c in book.closes]
    pnl = [c.net_pnl for c in book.closes]
    fees = [c.fees for c in book.closes]
    fund = [c.funding for c in book.closes]
    slip = [c.slippage_cost for c in book.closes if c.slippage_cost is not None]
    mfe = [c.mfe_r for c in book.closes if c.mfe_r is not None]
    gb = [(c.mfe_r - c.r_multiple) for c in book.closes if c.mfe_r is not None]
    dec = book.n_accept + book.n_filter
    n_seen = dec + book.n_abstain
    open_pos = list(book.positions.values())
    total_risk = sum(p.risk_usdt for p in open_pos)
    by_side: dict[str, float] = {}
    for p in open_pos:
        by_side[str(p.side).upper()] = by_side.get(str(p.side).upper(), 0.0) + p.risk_usdt
    return {
        "policy": book.policy,
        "opened": book.n_accept, "closed": len(book.closes), "open_now": len(open_pos),
        "filtered": book.n_filter, "abstained": book.n_abstain,
        "coverage": (round(dec / n_seen, 6) if n_seen else None),
        "abstain_rate": (round(book.n_abstain / n_seen, 6) if n_seen else None),
        "net_pnl_usdt": (round(sum(pnl), 6) if pnl else None),
        **_stats(rs),
        "delta_ci95": bootstrap_ci(rs),
        "fees_usdt": (round(sum(fees), 8) if fees else None),
        "funding_usdt": (round(sum(fund), 8) if fund else None),
        "slippage_cost_usdt": (round(sum(slip), 8) if slip else None),
        "n_slippage_measured": len(slip),
        "n_slippage_missing": len(book.closes) - len(slip),
        "avg_mfe_r": (round(sum(mfe) / len(mfe), 6) if mfe else None),
        "avg_giveback_r": (round(sum(gb) / len(gb), 6) if gb else None),
        "mfe_retained": (round(sum(rs) / sum(mfe), 6)
                         if (mfe and sum(mfe) > 1e-9) else None),
        "turnover_trades": book.n_accept,
        "open_risk_usdt": round(total_risk, 6),
        "same_direction_risk_share": (round(max(by_side.values()) / total_risk, 6)
                                      if (by_side and total_risk > 0) else None),
        "applied": False,
    }


def compare(books: dict[str, PolicyBook], cfg: ExperimentConfig, *, now=None
            ) -> dict[str, Any]:
    """Beş politikanın AYNI marklar ve AYNI maliyet varsayımıyla karşılaştırması."""
    reps = {p: policy_report(books[p], cfg) for p in POLICIES if p in books}
    base = reps.get(P0) or {}
    base_rs = {c.trade_id: c.r_multiple for c in (books[P0].closes if P0 in books else [])}
    for p, rep in reps.items():
        if p == P0:
            rep["avoided_loss_r"] = 0.0
            rep["missed_gain_r"] = 0.0
            continue
        got = {c.trade_id for c in books[p].closes}
        skipped = [r for tid, r in base_rs.items() if tid not in got]
        rep["avoided_loss_r"] = round(-sum(v for v in skipped if v < 0), 6)
        rep["missed_gain_r"] = round(sum(v for v in skipped if v > 0), 6)
        be, ce = base.get("expectancy_r"), rep.get("expectancy_r")
        rep["expectancy_delta_vs_p0_r"] = (round(ce - be, 6)
                                           if (be is not None and ce is not None) else None)
    n_comparable = min((r["closed"] for r in reps.values()), default=0)
    n_p0 = (reps.get(P0) or {}).get("closed", 0)
    early = _early_directionality(reps, cfg, n_p0)
    promo = _promotion_eligibility(reps, cfg, books, now=now)
    return {
        **cfg.identity(),
        "generated_at": iso(now or utc_now()),
        "applied_to_canonical": False,
        "mode": "SHADOW_PAPER_ONLY",
        "n_comparable_closes": n_p0,
        "n_comparable_min_across_policies": n_comparable,
        "multiple_testing": {"n_trials": N_TRIALS, "family_alpha": 0.05,
                             "per_trial_alpha_sidak": sidak_alpha()},
        "policies": reps,
        "early_directionality": early,
        "promotion_eligibility": promo,
        "honesty_note_tr": (
            "Bu deney kâr KANITLAMAZ. Bir challenger'ın tek bir kaybedeni elemesi başarı "
            "sayılmaz. Beş politika aynı anda test edildiği için çoklu karşılaştırma "
            "düzeltmesi uygulanır ve düşük örneklemde sonuç NOT_EVALUABLE'dır."),
    }


def _early_directionality(reps: dict[str, Any], cfg: ExperimentConfig, n: int
                          ) -> dict[str, Any]:
    """Yalnız BİLGİLENDİRİCİ. Hiçbir şeyi aktive edemez, terfi ettiremez."""
    if n < cfg.early_directionality_min_closes:
        return {"state": "NOT_EVALUABLE_LOW_SAMPLE", "n": n,
                "required": cfg.early_directionality_min_closes,
                "activates_anything": False,
                "note_tr": "Erken yön göstergesi için yeterli karşılaştırılabilir kapanış yok."}
    ranked = sorted(((p, r.get("expectancy_r")) for p, r in reps.items()
                     if r.get("expectancy_r") is not None),
                    key=lambda kv: kv[1], reverse=True)
    return {"state": "INFORMATIONAL_ONLY", "n": n,
            "required": cfg.early_directionality_min_closes,
            "activates_anything": False,
            "ranking_by_expectancy_r": ranked,
            "confidence_intervals": {p: r.get("delta_ci95") for p, r in reps.items()},
            "low_sample_warning_tr": (
                f"n={n} < terfi eşiği {cfg.promotion_min_closes}. Bu sıralama GÖZLEMDİR, "
                "kanıt değildir; çoklu karşılaştırma düzeltmesi yapılmamış ham sıralamadır."),
            "activation_blocked_reason": "EARLY_DIRECTIONALITY_CANNOT_PROMOTE"}


def _observation_days(book: PolicyBook) -> float | None:
    ts = sorted(c.closed_at for c in book.closes if c.closed_at)
    if len(ts) < 2:
        return None
    a, b = _dt(ts[0]), _dt(ts[-1])
    return round((b - a).total_seconds() / 86400.0, 4) if (a and b) else None


def _promotion_eligibility(reps: dict[str, Any], cfg: ExperimentConfig,
                           books: dict[str, PolicyBook], *, now=None) -> dict[str, Any]:
    """Mevcut kapılar KORUNUR: >=50 karşılaştırılabilir kapanış ve >=30 takvim günü.

    Örneklem ön koşulu düşerse bağımlı kapılar `NOT_EVALUABLE_LOW_SAMPLE` olur ve
    **PASS SAYILMAZ**. Otomatik terfi hiçbir koşulda mümkün değildir.
    """
    base = reps.get(P0) or {}
    out: dict[str, Any] = {}
    for p, rep in reps.items():
        if p == P0:
            continue
        book = books[p]
        n = rep.get("closed") or 0
        days = _observation_days(book)
        ci = rep.get("delta_ci95") or {}
        dd_b, dd_c = base.get("max_drawdown_r"), rep.get("max_drawdown_r")
        cv_b, cv_c = base.get("cvar5_r"), rep.get("cvar5_r")
        cs_b, cs_c = base.get("same_direction_risk_share"), rep.get("same_direction_risk_share")
        pf = rep.get("profit_factor")
        exp = rep.get("expectancy_r")
        gates = [
            ("MIN_COMPARABLE_CLOSES", n >= cfg.promotion_min_closes,
             f"{n}/{cfg.promotion_min_closes}"),
            ("MIN_OBSERVATION_DAYS", days is not None and days >= cfg.promotion_min_days,
             f"{days}/{cfg.promotion_min_days}" if days is not None else "ölçülemedi"),
            ("POSITIVE_POST_COST_EXPECTANCY", exp is not None and exp > 0.0,
             f"{exp}"),
            ("PROFIT_FACTOR_ABOVE_ONE", pf is not None and pf > 1.0, f"{pf}"),
            ("CONFIDENCE_INTERVAL_EXCLUDES_ZERO", ci.get("excludes_zero"),
             f"[{ci.get('lo')}, {ci.get('hi')}] ({ci.get('state')})"),
            ("DRAWDOWN_NOT_WORSE",
             dd_b is not None and dd_c is not None and dd_c >= dd_b, f"{dd_b} → {dd_c}"),
            ("CVAR5_NOT_WORSE",
             cv_b is not None and cv_c is not None and cv_c >= cv_b, f"{cv_b} → {cv_c}"),
            ("CONCENTRATION_NOT_WORSE",
             cs_b is None or cs_c is None or cs_c <= cs_b, f"{cs_b} → {cs_c}"),
            ("SUFFICIENT_COVERAGE",
             (rep.get("coverage") or 0) >= 0.30, f"{rep.get('coverage')}"),
            ("MULTIPLE_TESTING_ACCOUNTED", True,
             f"n_trials={N_TRIALS}, sidak_alpha={sidak_alpha()}"),
        ]
        low = n < cfg.promotion_min_closes
        rows = []
        for code, passed, detail in gates:
            status = "EVALUATED"
            if low and code not in ("MIN_COMPARABLE_CLOSES", "MIN_OBSERVATION_DAYS",
                                    "MULTIPLE_TESTING_ACCOUNTED"):
                status = "NOT_EVALUABLE_LOW_SAMPLE"
                rows.append({"code": code, "passed": False, "raw_passed": bool(passed),
                             "status": status,
                             "detail": f"{detail} — NOT_EVALUABLE_LOW_SAMPLE "
                                       f"({n}/{cfg.promotion_min_closes})"})
            else:
                rows.append({"code": code, "passed": bool(passed), "status": status,
                             "detail": detail})
        out[p] = {"gates": rows,
                  "n_passed": sum(1 for g in rows if g["passed"]),
                  "n_total": len(rows),
                  "all_passed": all(g["passed"] for g in rows),
                  "not_evaluable": [g["code"] for g in rows
                                    if g["status"] == "NOT_EVALUABLE_LOW_SAMPLE"],
                  "auto_promotion": False,
                  "promotion_possible": False,
                  "note_tr": ("Terfi yalnız manuel operatör onayıyla ve bütün kapılar "
                              "geçildikten sonra düşünülebilir; otomatik terfi YOKTUR.")}
    return out


def root_cause_summary(closes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Kanonik kapanışlardan TEMKİNLİ kök-neden özeti. Yalnız GÖZLEM üretir.

    Hiçbir satır nedensellik iddia etmez; her blok `evidence_grade` ve düşük örneklem
    uyarısı taşır. Panonun "Neden Zarar Ediyoruz?" bölümü bunu olduğu gibi gösterir.
    """
    rows = [c for c in closes if isinstance(c, dict)]
    rs = [_f(c.get("r_multiple")) for c in rows]
    rs = [v for v in rs if v is not None]
    pnl = [v for v in (_f(c.get("net_pnl")) for c in rows) if v is not None]
    if not rs:
        return {"state": "NO_DATA", "n": 0, "evidence_grade": "UNKNOWN"}
    w = [v for v in rs if v > 0]
    l = [v for v in rs if v < 0]
    aw = (sum(w) / len(w)) if w else None
    al = (-sum(l) / len(l)) if l else None
    payoff = (aw / al) if (aw and al) else None
    wr = len(w) / len(rs)
    ci = bootstrap_ci(rs)
    exits: dict[str, int] = {}
    for c in rows:
        k = str(c.get("exit_reason") or "UNKNOWN")
        exits[k] = exits.get(k, 0) + 1
    obs = []
    if payoff:
        be = 1.0 / (1.0 + payoff)
        obs.append({"code": "WIN_RATE_BELOW_BREAKEVEN",
                    "claim_tr": (f"Ölçülen ödeme oranı {payoff:.2f} ile başabaş için "
                                 f"%{be*100:.1f} kazanma oranı gerekir; ölçülen %{wr*100:.1f}."),
                    "kind": "OBSERVATION", "causal": False, "n": len(rs),
                    "evidence_grade": "CANONICAL_LEDGER_ARITHMETIC"})
    obs.append({"code": "LOSSES_ARE_STOP_EXITS",
                "claim_tr": f"Çıkış nedeni dağılımı: {exits}.",
                "kind": "OBSERVATION", "causal": False, "n": len(rs),
                "evidence_grade": "CANONICAL_LEDGER"})
    return {
        "state": ("LOW_SAMPLE" if len(rs) < 50 else "SAMPLE_SUFFICIENT"),
        "n": len(rs), "wins": len(w), "losses": len(l),
        "win_rate": round(wr, 6),
        "net_pnl_usdt": (round(sum(pnl), 6) if pnl else None),
        **_stats(rs),
        "avg_winner_r": (round(aw, 6) if aw else None),
        "avg_loser_r": (round(al, 6) if al else None),
        "payoff_ratio": (round(payoff, 6) if payoff else None),
        "breakeven_win_rate": (round(1.0 / (1.0 + payoff), 6) if payoff else None),
        "required_payoff_at_measured_wr": (round((1 - wr) / wr, 6) if wr > 0 else None),
        "expectancy_ci95": ci,
        "expectancy_ci_excludes_zero": ci.get("excludes_zero"),
        "exit_reason_distribution": exits,
        "observations": obs,
        "evidence_grade": "OBSERVATION_ONLY",
        "causal": False,
        "note_tr": ("Bu blok GÖZLEMDİR, NEDENSELLİK DEĞİLDİR. Örneklem küçükken beklenti "
                    "güven aralığı sıfırı içerebilir; bu durumda 'sistem zarar ediyor' "
                    "iddiası bile istatistiksel olarak KANITLANMAMIŞTIR."),
    }


__all__ = [
    "SCHEMA_VERSION", "P0", "P1", "P2", "P3", "P4", "POLICIES", "N_TRIALS",
    "root_cause_summary",
    "ACCEPT", "FILTER", "ABSTAIN", "DECISIONS", "PRE_EXPERIMENT", "IN_EXPERIMENT",
    "SLIP_MEASURED", "SLIP_MODELED", "SLIP_MISSING",
    "X_CANONICAL", "X_POLICY_EXIT", "X_POLICY_STOP", "X_OPEN",
    "ExperimentConfig", "SimPosition", "SimClose", "PolicyBook",
    "closed_returns", "correlation", "decide_p1", "decide_p2", "decide_entry",
    "open_simulated", "close_simulated", "apply_mark",
    "event_id", "make_event", "bootstrap_ci", "sidak_alpha",
    "policy_report", "compare",
    "R_MIRROR", "R_AE_VETO", "R_AE_UNKNOWN", "R_DIR_SHARE", "R_CLUSTER_SHARE",
    "R_CLUSTER_COUNT", "R_CORR_UNKNOWN", "R_NO_PRICE", "R_NO_RISK", "R_PRE_EXPERIMENT",
    "R_NOT_CHAMPION_ACCEPTED", "R_OK",
]

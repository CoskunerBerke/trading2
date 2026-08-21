"""Karar kapılarının TEK merkezi sözleşmesi — aynı kontrol her modülde aynı sertlikte yorumlanır.

Neden: giriş yolu (scanner → specialists → coin head → red team → chief → trigger → research policy →
risk engine → ledger) boyunca onlarca kontrol vardı ve zayıf kanıtlar (MA konumu, RSI, konsensüs,
rejim uyumu, sabit R/R, aynı yön adedi) tıpkı gerçek güvenlik ihlalleri gibi **sert veto** üretiyordu.
Üst üste dizilen bu engeller, maliyet sonrası pozitif fırsatları sistematik olarak öldürüyordu.

Üç sınıf:

* ``HARD_SAFETY``   — tek başına işlemi REDDEDER. Yalnız gerçek güvenlik/ekonomi ihlalleri.
* ``SOFT_EVIDENCE`` — tek başına ASLA reddetmez; fırsat puanını ve pozisyon boyutunu düşürür.
* ``RESEARCH_ONLY`` — kanıt zayıf ama point-estimate pozitif: küçük araştırma boyutu ya da
  karşı-olgusal gölge. Negatif beklentili işlem SIRF veri toplamak için AÇILMAZ.

Bu dosya davranışın kaynağıdır: yeni bir kontrol eklendiğinde buraya sınıfıyla kaydedilmelidir.
`tests/test_decision_gates.py` kayıtsız kod üretilmesini engeller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HARD_SAFETY = "HARD_SAFETY"
SOFT_EVIDENCE = "SOFT_EVIDENCE"
RESEARCH_ONLY = "RESEARCH_ONLY"
CLASSES = (HARD_SAFETY, SOFT_EVIDENCE, RESEARCH_ONLY)


@dataclass(frozen=True)
class Gate:
    code: str
    cls: str
    stage: str          # scanner | specialists | coin_head | red_team | chief | trigger | research | risk | execution
    why: str


def _g(code: str, cls: str, stage: str, why: str) -> Gate:
    return Gate(code, cls, stage, why)


# --------------------------------------------------------------------------- HARD_SAFETY
# Bunlar TEK BAŞINA işlemi reddedebilir. Liste bilinçli olarak kısadır ve yalnız gerçek
# güvenlik/veri bütünlüğü/ekonomi ihlallerini içerir.
_HARD: tuple[Gate, ...] = (
    _g("MODE_NOT_TRADEABLE", HARD_SAFETY, "execution", "PAPER dışı / live-order-path açık"),
    _g("KILL_SWITCH_ACTIVE", HARD_SAFETY, "risk", "kill switch girişleri durdurdu"),
    _g("SHUTDOWN_REQUESTED", HARD_SAFETY, "execution", "kooperatif durdurma istendi"),
    _g("RISK_STATE_PERSIST_FAILED", HARD_SAFETY, "risk", "risk durumu yazılamadı (fail-closed)"),
    _g("GAP_RECONCILE_PENDING", HARD_SAFETY, "execution", "kesinti penceresi uzlaştırılmadı"),
    _g("DATA_INVALID", HARD_SAFETY, "specialists", "eksik/bozuk/bayat veri"),
    _g("TIMESTAMP_LEAKAGE", HARD_SAFETY, "coin_head", "geleceğe bakan veri"),
    _g("STOP_PRESENT", HARD_SAFETY, "risk", "stop yok ya da geçersiz"),
    _g("ZERO_STOP_DISTANCE", HARD_SAFETY, "coin_head", "stop mesafesi sıfır"),
    _g("PLAN_GEOMETRY_INVALID", HARD_SAFETY, "coin_head", "entry/stop/target geometrisi geçersiz"),
    _g("NO_TRADE_MIN_ORDER_CONFLICT", HARD_SAFETY, "execution", "min-notional/step-size uyumsuz"),
    _g("STEP_ZERO_QTY", HARD_SAFETY, "execution", "adım yuvarlaması sonrası miktar sıfır"),
    _g("DUPLICATE_SIGNAL", HARD_SAFETY, "trigger", "aynı benzersiz sinyal tekrarı"),
    _g("ALREADY_OPEN_SAME_SYMBOL", HARD_SAFETY, "risk", "aynı sembolde açık pozisyon"),
    _g("OPPOSITE_EXPOSURE_CONFLICT", HARD_SAFETY, "risk", "aynı coinde ters yönlü çakışma"),
    _g("TOTAL_OPEN_RISK", HARD_SAFETY, "risk", "toplam açık risk bütçesi doldu"),
    _g("MARGIN_UTILIZATION", HARD_SAFETY, "risk", "margin kapasitesi doldu"),
    _g("LIQ_BUFFER_TOO_THIN", HARD_SAFETY, "risk", "likidasyon tamponu yetersiz"),
    _g("RISK_CAPACITY_BLOCKED", HARD_SAFETY, "chief", "portföy risk kapasitesi doldu (KOTA DEĞİL)"),
    _g("MARKET_UNAVAILABLE", HARD_SAFETY, "specialists", "borsa bakımda / işlem yapılamaz"),
    _g("LIQUIDITY_UNTRADEABLE", HARD_SAFETY, "specialists", "işlem yapılamayacak kadar kötü likidite"),
    _g("NEGATIVE_NET_EDGE", HARD_SAFETY, "coin_head", "maliyet ve belirsizlik sonrası negatif beklenti"),
    _g("RED_TEAM_HARD_VETO", HARD_SAFETY, "red_team", "gerçek güvenlik/veri ihlali vetosu"),
    _g("MAX_POSITIONS", HARD_SAFETY, "risk", "profil pozisyon adedi tavanı (yalnız TESTNET/LIVE)"),
    _g("MAX_POSITIONS_MARKET", HARD_SAFETY, "risk", "profil piyasa başına adet tavanı (yalnız TESTNET/LIVE)"),
)

# --------------------------------------------------------------------------- SOFT_EVIDENCE
# Bunlar TEK BAŞINA reddetmez; `opportunity_score` ve `size_multiplier` üzerinden etkir.
_SOFT: tuple[Gate, ...] = (
    _g("MA_POSITION", SOFT_EVIDENCE, "specialists", "MA25/MA99 konumu"),
    _g("MA_CROSS", SOFT_EVIDENCE, "specialists", "MA kesişim yönü/yaşı"),
    _g("RSI_LEVEL", SOFT_EVIDENCE, "specialists", "RSI seviyesi"),
    _g("MOMENTUM_WEAK", SOFT_EVIDENCE, "specialists", "momentum zayıf"),
    _g("TREND_MISALIGNED", SOFT_EVIDENCE, "specialists", "trend uyumsuz"),
    _g("VOLUME_WEAK", SOFT_EVIDENCE, "specialists", "hacim zayıf"),
    _g("VOL_REGIME_HIGH", SOFT_EVIDENCE, "specialists", "yüksek volatilite rejimi"),
    _g("FUNDING_ADVERSE", SOFT_EVIDENCE, "specialists", "funding aleyhte"),
    _g("BTC_CORRELATION", SOFT_EVIDENCE, "specialists", "BTC korelasyonu/beta"),
    _g("MARKET_REGIME_MISMATCH", SOFT_EVIDENCE, "chief", "RISK-ON/RISK-OFF yön uyumsuzluğu"),
    _g("PATTERN_WEAK", SOFT_EVIDENCE, "coin_head", "pattern sonucu/örnek sayısı/CI genişliği"),
    _g("LOW_CONSENSUS", SOFT_EVIDENCE, "coin_head", "ajan konsensüsü zayıf"),
    _g("HIGH_DISSENT", SOFT_EVIDENCE, "coin_head", "ajan anlaşmazlığı yüksek"),
    _g("LOW_CONFIDENCE", SOFT_EVIDENCE, "coin_head", "kalibre güven düşük"),
    _g("SPREAD_WIDE", SOFT_EVIDENCE, "specialists", "normal ama ideal olmayan spread/depth"),
    _g("RR_BELOW_PREFERRED", SOFT_EVIDENCE, "coin_head", "sabit R/R tercihinin altında"),
    _g("SAME_DIRECTION_CROWDED", SOFT_EVIDENCE, "chief", "aynı yönde yığılma"),
    _g("CLUSTER_CROWDED", SOFT_EVIDENCE, "chief", "aynı korelasyon kümesinde yığılma"),
    _g("RED_TEAM_SOFT_PENALTY", SOFT_EVIDENCE, "red_team", "red-team kanıt zayıflığı cezası"),
    _g("RESEARCH_POLICY_PENALTY", SOFT_EVIDENCE, "research", "araştırma politikası boyut küçültmesi"),
    _g("SMALL_SAMPLE", SOFT_EVIDENCE, "coin_head", "geçmiş örnek sayısı düşük"),
)

# --------------------------------------------------------------------------- RESEARCH_ONLY
_RESEARCH: tuple[Gate, ...] = (
    _g("RESEARCH_SIZE_ONLY", RESEARCH_ONLY, "coin_head", "point-estimate pozitif, belirsizlik yüksek"),
    _g("RESEARCH_SHADOW_ONLY", RESEARCH_ONLY, "research", "yalnız karşı-olgusal gözlem"),
    _g("RESEARCH_POLICY_BLOCK", RESEARCH_ONLY, "research", "aktif araştırma adayı girişi eledi"),
)

GATES: dict[str, Gate] = {g.code: g for g in (_HARD + _SOFT + _RESEARCH)}

# Sabit işlem SAYISI kotası bu sistemde YOKTUR. Aşağıdaki kodlar yasaklıdır; testler kaynakta
# yeniden belirmelerini engeller. Risk kapasitesi dolduğunda `RISK_CAPACITY_BLOCKED` kullanılır.
FORBIDDEN_QUOTA_CODES = ("DAILY_LIMIT", "PER_RUN_LIMIT", "DAILY_TRADE_CAP", "PER_RUN_TRADE_CAP",
                         "MAX_NEW_POSITIONS_PER_RUN", "MAX_TRADES_PER_DAY", "TRADE_QUOTA")


def gate_class(code: str) -> str:
    g = GATES.get(str(code))
    return g.cls if g else SOFT_EVIDENCE          # bilinmeyen kod ASLA sert olamaz (fail-open değil: yalnız puan düşürür)


def is_hard(code: str) -> bool:
    return gate_class(code) == HARD_SAFETY


def hard_codes() -> tuple[str, ...]:
    return tuple(sorted(g.code for g in GATES.values() if g.cls == HARD_SAFETY))


def soft_codes() -> tuple[str, ...]:
    return tuple(sorted(g.code for g in GATES.values() if g.cls == SOFT_EVIDENCE))


@dataclass
class SoftSignal:
    """Bir yumuşak kanıt: puanı ve boyutu düşürür, ASLA tek başına reddetmez."""
    code: str
    penalty_r: float = 0.0          # conservative edge'den düşülecek R cezası (>= 0)
    detail: str = ""
    value: float | None = None

    def __post_init__(self) -> None:
        if is_hard(self.code):
            raise ValueError(f"{self.code} HARD_SAFETY — yumuşak kanıt olarak kullanılamaz")
        self.penalty_r = max(0.0, float(self.penalty_r))

    def to_dict(self) -> dict:
        return {"code": self.code, "penalty_r": round(self.penalty_r, 6), "detail": self.detail,
                "value": self.value, "class": gate_class(self.code)}


@dataclass
class GateLedger:
    """Bir aday için toplanan kapılar. Sert olanlar reddeder; yumuşak olanlar ceza toplar."""
    hard: list[str] = field(default_factory=list)
    soft: list[SoftSignal] = field(default_factory=list)

    def block(self, code: str, detail: str = "") -> "GateLedger":
        if not is_hard(code):
            raise ValueError(f"{code} HARD_SAFETY değil — sert engel olarak kullanılamaz")
        if code not in self.hard:
            self.hard.append(code)
        return self

    def penalise(self, code: str, penalty_r: float, detail: str = "", value: float | None = None) -> "GateLedger":
        self.soft.append(SoftSignal(code, penalty_r, detail, value))
        return self

    @property
    def blocked(self) -> bool:
        return bool(self.hard)

    def soft_penalty_r(self, *, cap: float = 0.60) -> float:
        """Toplam yumuşak ceza — ÜST SINIRLI: çok sayıda orta zayıflık, otomatik veto'ya dönüşmez."""
        return min(cap, sum(s.penalty_r for s in self.soft))

    def to_dict(self) -> dict:
        return {"hard_block_codes": list(self.hard), "soft_evidence": [s.to_dict() for s in self.soft],
                "soft_penalty_r": round(self.soft_penalty_r(), 6)}


__all__ = ["CLASSES", "FORBIDDEN_QUOTA_CODES", "GATES", "Gate", "GateLedger", "HARD_SAFETY",
           "RESEARCH_ONLY", "SOFT_EVIDENCE", "SoftSignal", "gate_class", "hard_codes", "is_hard",
           "soft_codes"]

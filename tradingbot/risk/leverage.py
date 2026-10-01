"""DİNAMİK FUTURES KALDIRACI (2x–5x) — deterministik, config ile yönetilen, test edilebilir.

TEMEL İLKE — KALDIRAÇ RİSKİ ARTIRMAZ:
    Pozisyon `notional`'ı ÖNCE risk bütçesi ve stop mesafesinden hesaplanır
    (`risk.engine.size_position`: ``notional = risk_usdt / stop_frac``). Kaldıraç YALNIZCA gerekli
    teminatı belirler:

        initial_margin = notional / leverage

    Bu nedenle 2x, 3x, 4x ve 5x için stopta beklenen maksimum dolar zararı AYNIDIR. Aynı teminatı
    koruyup notional'ı kaldıraç kadar büyütmek YASAKTIR.

NEDEN GEREKLİ: `CoinHead._plan_from_atr` planı `PlanSize(..., leverage=1)` ile üretiyor ve
`size_position(requested_leverage=1)` bunu `max(1, min(max_leverage, 1)) = 1` yapıyordu; bu yüzden
bütün futures işlemleri 1x açılıyordu.

ZAYIF SİNYAL 2x AÇMAZ: taban seviyeye (2x) uygun olmayan aday `leverage=0` (`NO_TRADE`) döner.
Kaldıraç asla "en azından 2x" diye işlem açtırmaz.

Seviyeler kümülatiftir: 5x için 2x/3x/4x koşullarının TAMAMI da sağlanmalıdır.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

NO_TRADE = 0
ABSOLUTE_MAX_LEVERAGE = 5                 # MUTLAK üst sınır — config ile aşılamaz
ABSOLUTE_MIN_LEVERAGE = 2                 # 1x yeni futures girişi YASAK


def validate_leverage_settings(*, enabled: bool, paper_only: bool, min_leverage: int,
                               max_leverage: int, mode: str | None = None) -> None:
    """TEK KANONİK KALDIRAÇ DOĞRULAMASI — config yükleyici VE motor kurulumu bunu çağırır.

    Aynı kuralın iki ayrı yerde kopyalanması denetimde boşluk üretti: `LeverageConfig.validate()`
    üretimde HİÇ çağrılmıyordu ve kütüphane varsayılanı 1x/6x'e mutasyona uğradığında hiçbir test
    düşmüyordu. Kurallar artık burada TEK yerde durur.
    """
    from ..core import ConfigError
    if max_leverage > ABSOLUTE_MAX_LEVERAGE:
        raise ConfigError(f"leverage.max_leverage {max_leverage} > {ABSOLUTE_MAX_LEVERAGE} — mutlak üst sınır aşılamaz")
    if min_leverage < ABSOLUTE_MIN_LEVERAGE:
        raise ConfigError(f"leverage.min_leverage {min_leverage} < {ABSOLUTE_MIN_LEVERAGE} — yeni futures işlemleri 1x açılamaz")
    if min_leverage > max_leverage:
        raise ConfigError(f"leverage.min_leverage {min_leverage} > leverage.max_leverage {max_leverage}")
    if enabled and paper_only and mode is not None and str(mode).upper() != "PAPER":
        raise ConfigError(f"leverage.enabled=true fakat mod {mode} — dinamik kaldıraç yalnız PAPER'da "
                          "açılabilir (LIVE/TESTNET için paper_only=false bilinçli olarak verilmelidir)")


@dataclass(frozen=True)
class LeverageConfig:
    """Deterministik eşikler. `enabled=False` iken davranış eskisi gibidir (1x plan kaldıracı)."""
    enabled: bool = False                     # feature flag — yalnız PAPER araştırma profilinde açılır
    paper_only: bool = True                   # LIVE/TESTNET'te açılamaz (bilinçli olarak false verilmeli)
    min_leverage: int = 2                     # taban: bunun altına DÜŞÜLMEZ, işlem açılmaz
    max_leverage: int = 5                     # MUTLAK üst sınır
    # --- taban (2x) kapıları: bunlar sağlanmazsa NO_TRADE ---
    min_confidence: float = 0.30              # kalibre güven
    max_stop_atr_mult: float = 4.0            # stop, ATR'ın bu katından uzaksa taban dahi verilmez
    min_stop_atr_mult: float = 0.5            # stop ATR'a göre anlamsız yakınsa gürültü riski
    min_depth_usdt: float = 25_000.0
    max_spread_pct: float = 0.30
    min_liq_buffer_mult: float = 3.0          # (1/lev − mmr) ≥ k × stop_frac
    # --- seviye eşikleri (kümülatif) ---
    conf_3x: float = 0.45
    conf_4x: float = 0.58
    conf_5x: float = 0.70
    edge_3x: float = 0.15                     # conservative_net_edge_r
    edge_4x: float = 0.30
    edge_5x: float = 0.45
    max_atr_pct_3x: float = 8.0               # volatilite tavanı (4h ATR %)
    max_atr_pct_4x: float = 6.0
    max_atr_pct_5x: float = 4.0
    min_depth_4x: float = 100_000.0
    min_depth_5x: float = 250_000.0
    max_spread_4x: float = 0.12
    max_spread_5x: float = 0.06
    max_funding_4x: float = 0.03              # aleyhte funding % (8s)
    max_funding_5x: float = 0.015
    max_open_risk_frac_4x: float = 0.70       # toplam açık riskin bütçeye oranı
    max_open_risk_frac_5x: float = 0.50
    max_same_dir_4x: int = 3                  # aynı yönde açık pozisyon
    max_same_dir_5x: int = 2
    max_corr_5x: float = 0.80                 # portföyle korelasyon
    liq_buffer_4x: float = 3.5
    liq_buffer_5x: float = 4.5                # 5x için DAHA SERT tampon
    require_regime_alignment_5x: bool = True
    mmr: float = 0.004

    def validate(self, *, mode: str | None = None) -> None:
        """KANONİK doğrulama — `validate_leverage_settings`'e delege eder (tek kural kümesi)."""
        validate_leverage_settings(enabled=self.enabled, paper_only=self.paper_only,
                                   min_leverage=self.min_leverage, max_leverage=self.max_leverage,
                                   mode=mode)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LeverageContext:
    """Karar girdileri. `None` = BİLİNMİYOR → ilgili seviye yükseltmesi VERİLMEZ (fail-closed)."""
    stop_frac: float | None = None            # |entry−stop| / entry
    atr_pct: float | None = None
    confidence: float | None = None           # kalibre güven 0..1
    conservative_net_edge_r: float | None = None
    depth_usdt: float | None = None
    spread_pct: float | None = None
    funding_pct: float | None = None          # aleyhte ise pozitif
    regime_aligned: bool | None = None
    open_risk_frac: float | None = None       # kullanılan / toplam risk bütçesi (0..1)
    same_direction_open: int | None = None
    portfolio_corr: float | None = None
    data_stale: bool = False
    data_conflict: bool = False
    profile_max_leverage: int = 5
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LeverageDecision:
    leverage: int                             # 0 = NO_TRADE
    reasons: list[str]                        # bu seviye NEDEN seçildi
    blocked_higher: list[str]                 # daha yükseği NEDEN seçilmedi
    liq_buffer_mult: float | None = None       # (1/lev − mmr) / stop_frac
    tier_checks: dict[str, list[str]] = field(default_factory=dict)

    @property
    def tradeable(self) -> bool:
        return self.leverage >= 1

    def to_dict(self) -> dict:
        return {"leverage": self.leverage, "reasons": list(self.reasons),
                "blocked_higher": list(self.blocked_higher),
                "liq_buffer_mult": self.liq_buffer_mult, "tier_checks": dict(self.tier_checks)}


def _liq_buffer(lev: int, stop_frac: float, mmr: float) -> float:
    """Yaklaşık likidasyon mesafesinin stop mesafesine oranı."""
    if stop_frac <= 0:
        return 0.0
    return (1.0 / lev - mmr) / stop_frac


def _base_gate_failures(ctx: LeverageContext, cfg: LeverageConfig) -> list[str]:
    """Taban (min_leverage) için ZORUNLU kapılar. Boş değilse NO_TRADE."""
    f: list[str] = []
    if ctx.data_stale:
        f.append("DATA_STALE")
    if ctx.data_conflict:
        f.append("DATA_CONFLICT")
    if ctx.stop_frac is None or ctx.stop_frac <= 0:
        f.append("STOP_UNKNOWN")
    if ctx.confidence is None:
        f.append("CONFIDENCE_UNKNOWN")
    elif ctx.confidence < cfg.min_confidence:
        f.append("CONFIDENCE_BELOW_BASE")
    if ctx.atr_pct is not None and ctx.stop_frac:
        k = (ctx.stop_frac * 100.0) / ctx.atr_pct if ctx.atr_pct else None
        if k is not None and k > cfg.max_stop_atr_mult:
            f.append("STOP_TOO_FAR_FOR_LEVERAGE")
        elif k is not None and k < cfg.min_stop_atr_mult:
            f.append("STOP_TOO_TIGHT_FOR_LEVERAGE")
    if ctx.depth_usdt is not None and ctx.depth_usdt < cfg.min_depth_usdt:
        f.append("DEPTH_BELOW_BASE")
    if ctx.spread_pct is not None and ctx.spread_pct > cfg.max_spread_pct:
        f.append("SPREAD_ABOVE_BASE")
    if ctx.stop_frac and _liq_buffer(cfg.min_leverage, ctx.stop_frac, cfg.mmr) < cfg.min_liq_buffer_mult:
        f.append("LIQ_BUFFER_TOO_THIN_FOR_BASE")
    return f


def _tier_failures(lev: int, ctx: LeverageContext, cfg: LeverageConfig) -> list[str]:
    """`lev` seviyesinin EK koşulları (kümülatif; çağıran alt seviyeleri de kontrol eder)."""
    f: list[str] = []

    def need(value, limit, code, *, at_least=True):
        if value is None:
            f.append(f"{code}_UNKNOWN")            # bilinmiyor → yükseltme YOK (fail-closed)
        elif (value < limit) if at_least else (value > limit):
            f.append(code)

    if lev >= 3:
        need(ctx.confidence, cfg.conf_3x, "CONFIDENCE_BELOW_3X")
        need(ctx.conservative_net_edge_r, cfg.edge_3x, "EDGE_BELOW_3X")
        need(ctx.atr_pct, cfg.max_atr_pct_3x, "VOLATILITY_ABOVE_3X", at_least=False)
    if lev >= 4:
        need(ctx.confidence, cfg.conf_4x, "CONFIDENCE_BELOW_4X")
        need(ctx.conservative_net_edge_r, cfg.edge_4x, "EDGE_BELOW_4X")
        need(ctx.atr_pct, cfg.max_atr_pct_4x, "VOLATILITY_ABOVE_4X", at_least=False)
        need(ctx.depth_usdt, cfg.min_depth_4x, "DEPTH_BELOW_4X")
        need(ctx.spread_pct, cfg.max_spread_4x, "SPREAD_ABOVE_4X", at_least=False)
        need(ctx.funding_pct, cfg.max_funding_4x, "FUNDING_ABOVE_4X", at_least=False)
        need(ctx.open_risk_frac, cfg.max_open_risk_frac_4x, "OPEN_RISK_ABOVE_4X", at_least=False)
        need(ctx.same_direction_open, cfg.max_same_dir_4x, "CROWDED_ABOVE_4X", at_least=False)
        if ctx.stop_frac and _liq_buffer(4, ctx.stop_frac, cfg.mmr) < cfg.liq_buffer_4x:
            f.append("LIQ_BUFFER_BELOW_4X")
    if lev >= 5:
        need(ctx.confidence, cfg.conf_5x, "CONFIDENCE_BELOW_5X")
        need(ctx.conservative_net_edge_r, cfg.edge_5x, "EDGE_BELOW_5X")
        need(ctx.atr_pct, cfg.max_atr_pct_5x, "VOLATILITY_ABOVE_5X", at_least=False)
        need(ctx.depth_usdt, cfg.min_depth_5x, "DEPTH_BELOW_5X")
        need(ctx.spread_pct, cfg.max_spread_5x, "SPREAD_ABOVE_5X", at_least=False)
        need(ctx.funding_pct, cfg.max_funding_5x, "FUNDING_ABOVE_5X", at_least=False)
        need(ctx.open_risk_frac, cfg.max_open_risk_frac_5x, "OPEN_RISK_ABOVE_5X", at_least=False)
        need(ctx.same_direction_open, cfg.max_same_dir_5x, "CROWDED_ABOVE_5X", at_least=False)
        if ctx.portfolio_corr is None:
            f.append("CORRELATION_UNKNOWN")
        elif abs(ctx.portfolio_corr) > cfg.max_corr_5x:
            f.append("CORRELATION_ABOVE_5X")
        if cfg.require_regime_alignment_5x and not ctx.regime_aligned:
            f.append("REGIME_NOT_ALIGNED_5X")
        if ctx.stop_frac and _liq_buffer(5, ctx.stop_frac, cfg.mmr) < cfg.liq_buffer_5x:
            f.append("LIQ_BUFFER_BELOW_5X")
    return f


def select_leverage(ctx: LeverageContext, cfg: LeverageConfig | None = None) -> LeverageDecision:
    """En yüksek uygun seviyeden aşağı inerek DETERMİNİSTİK kaldıraç seçimi.

    Rastgelelik yoktur; aynı girdi her zaman aynı sonucu verir. Taban kapıları geçilemezse
    `leverage=0` (NO_TRADE) döner — zayıf sinyal 2x ile AÇILMAZ.
    """
    cfg = cfg or LeverageConfig()
    base_fail = _base_gate_failures(ctx, cfg)
    if base_fail:
        return LeverageDecision(NO_TRADE, reasons=["BASE_GATES_FAILED"], blocked_higher=base_fail,
                                tier_checks={"base": base_fail})
    ceiling = min(cfg.max_leverage, max(1, int(ctx.profile_max_leverage or cfg.max_leverage)))
    checks: dict[str, list[str]] = {"base": []}
    blocked: list[str] = []
    for lev in range(ceiling, cfg.min_leverage - 1, -1):
        fails = _tier_failures(lev, ctx, cfg)
        checks[f"{lev}x"] = fails
        if not fails:
            reasons = ["BASE_GATES_PASSED", f"TIER_{lev}X_SATISFIED"]
            if lev < ceiling:
                reasons.append("HIGHER_TIER_BLOCKED")
            return LeverageDecision(lev, reasons=reasons, blocked_higher=blocked,
                                    liq_buffer_mult=round(_liq_buffer(lev, ctx.stop_frac or 0.0, cfg.mmr), 4),
                                    tier_checks=checks)
        blocked.extend(f"{lev}x:{c}" for c in fails)
    # min_leverage bile ek koşulları sağlamadıysa taban yine de geçerlidir (ek koşul yok).
    return LeverageDecision(cfg.min_leverage, reasons=["BASE_GATES_PASSED", "TIER_MIN_FALLBACK"],
                            blocked_higher=blocked,
                            liq_buffer_mult=round(_liq_buffer(cfg.min_leverage, ctx.stop_frac or 0.0, cfg.mmr), 4),
                            tier_checks=checks)


__all__ = ["ABSOLUTE_MAX_LEVERAGE", "ABSOLUTE_MIN_LEVERAGE", "LeverageConfig", "LeverageContext",
           "LeverageDecision", "NO_TRADE", "select_leverage", "validate_leverage_settings"]

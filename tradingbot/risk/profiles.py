"""Risk profilleri — deterministik, LLM'den bağımsız.

PAPER_RESEARCH mevcut davranışı AYNEN korur (risk %2, tek coin %30, 3 pozisyon, kaldıraç ≤5, günlük/haftalık/DD
limitleri yok). Diğer profiller muhafazakâr başlangıç değerleridir; kullanıcı gevşetirse `warnings` üretilir.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ..core import ConfigError


@dataclass(frozen=True)
class RiskProfile:
    name: str
    risk_per_trade_pct: float                 # işlem başına riske atılan equity %
    max_total_open_risk_pct: float            # açık pozisyonların toplam riski %
    futures_max_leverage: int
    max_open_positions: int | None             # spot + futures toplam; None = ADET limiti YOK
    max_positions_per_market: int | None       # None = ADET limiti YOK (risk butcesi karar verir)
    max_position_pct: float = 30.0            # tek coine max notional/equity %
    daily_loss_stop_pct: float | None = None  # None = kapalı
    weekly_loss_stop_pct: float | None = None
    max_drawdown_kill_pct: float | None = None
    correlated_cluster_cap: int | None = None # aynı kümede aynı yönde max pozisyon
    altcoin_net_exposure_cap_pct: float | None = None
    futures_margin_utilization_cap_pct: float | None = None
    min_liquidation_buffer_mult: float | None = None   # liq mesafesi ≥ k × stop mesafesi
    consecutive_loss_cooldown_n: int | None = None
    cooldown_hours: float = 0.0
    symbol_cooldown_hours: float = 0.0
    max_spread_pct: float | None = None
    min_expected_r: float | None = None
    size_on_live_equity: bool = False         # False: eski davranış (starting_equity), True: canlı equity
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, RiskProfile] = {
    # PAPER_RESEARCH: pozisyon ADEDI ana risk mekanizmasi DEGILDIR -> None. Karar toplam acik risk
    # (%6), islem basina risk tavani (%2), margin, liq buffer ve same-symbol kapilariyla verilir.
    # `risk_per_trade_pct` bir TAVAN'dir; her islemde zorunlu kullanilan miktar degildir.
    "PAPER_RESEARCH": RiskProfile("PAPER_RESEARCH", 2.0, 6.0, 5, None, None, 30.0),
    "TESTNET": RiskProfile("TESTNET", 0.5, 2.0, 2, 3, 3, 30.0, 2.0, 4.0, 8.0, 2, 50.0, 60.0, 3.0, 3, 24.0, 24.0, 0.3, 1.0, True),
    "SHADOW_LIVE": RiskProfile("SHADOW_LIVE", 0.5, 2.0, 2, 3, 3, 30.0, 2.0, 4.0, 8.0, 2, 50.0, 60.0, 3.0, 3, 24.0, 24.0, 0.3, 1.0, True),
    "LIVE_LIMITED": RiskProfile("LIVE_LIMITED", 0.25, 1.0, 1, 2, 2, 20.0, 1.0, 2.0, 5.0, 1, 30.0, 40.0, 3.0, 3, 48.0, 48.0, 0.2, 1.5, True),
    "LIVE": RiskProfile("LIVE", 0.5, 2.0, 2, 3, 3, 30.0, 2.0, 4.0, 8.0, 2, 50.0, 60.0, 3.0, 3, 24.0, 24.0, 0.3, 1.0, True),
}
DEFAULT_PROFILE = "PAPER_RESEARCH"
_RECOMMENDED = PROFILES["TESTNET"]


def resolve_profile(name: str | None = None, overrides: dict[str, Any] | None = None, *, i_understand: bool = False) -> RiskProfile:
    """İsim + kullanıcı ezmeleri → doğrulanmış profil. Saçma değerlerde ConfigError (program başlamamalı)."""
    name = (name or DEFAULT_PROFILE).upper()
    if name not in PROFILES:
        raise ConfigError(f"bilinmeyen risk profili: {name} (geçerli: {', '.join(PROFILES)})")
    base = PROFILES[name]
    ov = {k: v for k, v in (overrides or {}).items() if v is not None and k in base.__dataclass_fields__ and k != "name"}
    p = replace(base, **ov)
    if p.risk_per_trade_pct <= 0 or p.max_total_open_risk_pct <= 0:
        raise ConfigError("risk_per_trade_pct ve max_total_open_risk_pct pozitif olmalı")
    if p.risk_per_trade_pct > 10 and not i_understand:
        raise ConfigError("risk_per_trade_pct > 10 — bilinçli kabul (i_understand=true) olmadan başlatılmaz")
    if not (1 <= p.futures_max_leverage <= 125):
        raise ConfigError("futures_max_leverage 1..125 aralığında olmalı")
    for _k in ("max_open_positions", "max_positions_per_market"):
        _v = getattr(p, _k)
        if _v is not None and _v < 1:
            raise ConfigError("pozisyon limitleri ≥ 1 olmalı (ya da None = adet limiti yok)")
    for k in ("daily_loss_stop_pct", "weekly_loss_stop_pct", "max_drawdown_kill_pct"):
        v = getattr(p, k)
        if v is not None and not (0 < v <= 100):
            raise ConfigError(f"{k} 0..100 aralığında olmalı")
    return p


def warn_if_below_recommended(p: RiskProfile) -> list[str]:
    """PAPER dışı modlar için: TESTNET önerilerinden gevşek her ayar bir uyarı üretir (sessizce kabul edilmez)."""
    w: list[str] = []
    r = _RECOMMENDED
    if p.risk_per_trade_pct > r.risk_per_trade_pct:
        w.append(f"risk_per_trade_pct {p.risk_per_trade_pct} > önerilen {r.risk_per_trade_pct}")
    if p.futures_max_leverage > r.futures_max_leverage:
        w.append(f"futures_max_leverage {p.futures_max_leverage}x > önerilen {r.futures_max_leverage}x")
    if p.max_total_open_risk_pct > r.max_total_open_risk_pct:
        w.append(f"max_total_open_risk_pct {p.max_total_open_risk_pct} > önerilen {r.max_total_open_risk_pct}")
    for k in ("daily_loss_stop_pct", "weekly_loss_stop_pct", "max_drawdown_kill_pct"):
        v, rv = getattr(p, k), getattr(r, k)
        if v is None or (rv is not None and v > rv):
            w.append(f"{k} {'kapalı' if v is None else v} (önerilen {rv})")
    return w

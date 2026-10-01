"""Panel yapılandırması."""
from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass

from ..core import ConfigError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    if h in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    auth_token: str | None = None          # Bearer token; None → yalnızca loopback'e izin
    read_only: bool = True                 # her zaman True (POST/PUT yok)
    max_bars: int = 600
    allow_insecure_public: bool = False    # host loopback değil ve token yoksa açıkça izin gerekir
    heartbeat_max_age_s: float = 900.0     # /health/ready eşiği
    sse_heartbeat_s: float = 15.0
    title: str = "Trading Bot"
    # --- canli yenileme (tarayici polling; borsaya DOGRUDAN baglanti YOK) ---
    poll_positions_s: int = 7
    poll_portfolio_s: int = 20
    poll_health_s: int = 12
    stale_price_s: int = 90
    stale_run_s: int = 2400
    background_backoff_mult: int = 4
    timezone_label: str = "UTC"

    def validate(self) -> None:
        if not is_loopback(self.host) and not self.auth_token and not self.allow_insecure_public:
            raise ConfigError(
                f"panel host={self.host!r} loopback değil ve auth_token yok; ya auth_token verin ya da "
                "allow_insecure_public=True (önerilmez — reverse proxy + TLS arkasında kullanın)")
        if not (0 < int(self.port) < 65536):
            raise ConfigError(f"geçersiz port: {self.port}")
        if int(self.max_bars) < 50:
            raise ConfigError("max_bars en az 50 olmalı")
        for _f in ("poll_positions_s", "poll_portfolio_s", "poll_health_s"):
            if int(getattr(self, _f)) < 2:
                raise ConfigError(f"{_f} en az 2 saniye olmalı (istek fırtınası koruması)")
        if int(self.stale_price_s) < int(self.poll_positions_s):
            raise ConfigError("stale_price_s, poll_positions_s'ten küçük olamaz")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["auth_token"] = "***" if self.auth_token else None
        return d


__all__ = ["DashboardConfig", "is_loopback"]

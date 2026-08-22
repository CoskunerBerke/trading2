"""`tradingbot worker-alert` — worker SÜRECİ ÖLDÜĞÜNDE harici uyarı.

NEDEN GEREKLİ: `TradeNotifier` worker sürecinin İÇİNDE yaşar. Süreç çökerse/OOM ile öldürülürse
kendi ölümünü bildiremez. Bu komut systemd `OnFailure=` hook'undan (bkz.
`deploy/tradingbot-alert@.service`) AYRI ve KISA ÖMÜRLÜ bir süreç olarak çalışır; sürekli çalışan
yeni bir daemon YOKTUR.

GÜVENLİK SÖZLEŞMESİ
* Token KOMUT SATIRI ARGÜMANI OLARAK ALINMAZ (process list / `systemctl status` / journal'a sızmaz);
  yalnız `EnvironmentFile` ile verilen ortam değişkeninden okunur — kabuk üzerinden interpolasyon yok.
* Telegram kapalıysa temiz no-op (çıkış 0, ağ çağrısı yok).
* Aynı `--ref` ile iki kez çalışsa bile tek mesaj gider (outbox idempotency).
* Gerçek emir açmaz, mod değiştirmez, defter yazmaz — yalnız outbox'a yazar ve gönderir.
* Normal `systemctl stop` `OnFailure=` TETİKLEMEZ (systemd sözleşmesi) → yanlış "çöktü" mesajı olmaz.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_UNIT = "tradingbot-worker.service"


def _notifier(cfg: Any, *, http: Any = None, env: dict[str, str] | None = None):
    from .service import TradeNotifier
    return TradeNotifier.from_config(cfg.v3.telegram, cfg.state_path, http=http, env=env)


def worker_failure(cfg: Any, *, unit: str = DEFAULT_UNIT, result: str = "", ref: str = "",
                   will_restart: bool = True, http: Any = None,
                   env: dict[str, str] | None = None) -> int:
    """`OnFailure=` hook'u: worker BAŞARISIZ oldu.

    `ref` idempotency anahtarıdır. systemd `MONITOR_INVOCATION_ID` sağlıyorsa o kullanılır; yoksa
    DAKİKA hassasiyetli zaman damgası — böylece aynı başarısızlık döngüsü mesaj yağmuruna dönüşmez
    (rate limit).
    """
    from ..core import iso, utc_now
    from .events import build_worker_failure
    env = os.environ if env is None else env
    n = _notifier(cfg, http=http, env=env)
    if not n.enabled:
        log.info("telegram kapalı — worker-alert no-op")
        return 0                                   # temiz no-op, ağ çağrısı YOK
    now = utc_now()
    ref = ref or str(env.get("MONITOR_INVOCATION_ID") or "")[:32] or iso(now)[:16]
    result = result or str(env.get("MONITOR_EXIT_STATUS") or env.get("SERVICE_RESULT") or "")
    n.enqueue(build_worker_failure(unit, result=result, when=iso(now),
                                   will_restart=will_restart, ref=ref, created_at=iso(now)))
    n.flush()
    return 0


def worker_recovery(cfg: Any, *, unit: str = DEFAULT_UNIT, http: Any = None,
                    env: dict[str, str] | None = None) -> int:
    """Kurtarma: worker GERÇEKTEN ready/healthy ise ve bekleyen bir failure varsa bildirir.

    Heartbeat bayatsa ya da sağlık durumu iyi değilse HİÇBİR ŞEY gönderilmez — "yeniden başladı"
    mesajı yalnız gerçek iyileşmede gider. Aynı kurtarma iki kez gönderilmez (failure ref'ine bağlı).
    """
    from ..core import iso, read_json, utc_now
    from .events import build_worker_recovered
    env = os.environ if env is None else env
    n = _notifier(cfg, http=http, env=env)
    if not n.enabled:
        return 0
    ref = n.pending_worker_failure()
    if not ref:
        return 0                                   # bekleyen başarısızlık yok → kurtarma da yok
    health = read_json(Path(cfg.state_path) / "health.json", default=None) or {}
    state = str(health.get("state") or "UNKNOWN").upper()
    hb_age = health.get("heartbeat_age_s")
    ready = state in ("HEALTHY", "OK")
    if not ready:
        log.info("worker henüz sağlıklı değil (%s) — kurtarma bildirimi gönderilmedi", state)
        return 0
    n.enqueue(build_worker_recovered(unit, ref=ref, heartbeat_age_s=hb_age, ready=True,
                                     created_at=iso(utc_now())))
    n.flush()
    return 0


def cmd_worker_alert(cfg: Any, args: Any) -> int:
    """CLI girişi: `tradingbot worker-alert --event failure|recovery`."""
    unit = getattr(args, "unit", None) or DEFAULT_UNIT
    if getattr(args, "event", "failure") == "recovery":
        return worker_recovery(cfg, unit=unit)
    return worker_failure(cfg, unit=unit, result=getattr(args, "result", "") or "",
                          ref=getattr(args, "ref", "") or "",
                          will_restart=not getattr(args, "no_restart", False))


def register(sub: Any) -> None:
    s = sub.add_parser("worker-alert", help="Worker çöktü/iyileşti bildirimi (systemd OnFailure hook)")
    s.add_argument("--event", choices=["failure", "recovery"], default="failure")
    s.add_argument("--unit", default=DEFAULT_UNIT)
    s.add_argument("--result", default="", help="systemd $SERVICE_RESULT (timeout/oom-kill/exit-code…)")
    s.add_argument("--ref", default="", help="idempotency anahtarı (systemd invocation id)")
    s.add_argument("--no-restart", action="store_true", help="systemd yeniden başlatmayacak")
    s.set_defaults(fn=cmd_worker_alert)


__all__ = ["DEFAULT_UNIT", "cmd_worker_alert", "register", "worker_failure", "worker_recovery"]

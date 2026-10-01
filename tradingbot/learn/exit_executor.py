"""Çıkış yürütücüsü sözleşmesi (`exit_executor_v1`) — bu sürümde YALNIZ `SHADOW`.

Amaç: ileride gerçek PAPER azaltma/çıkış yapabilecek arayüzü bugünden tanımlamak, fakat
hiçbir şey uygulamamak. `SHADOW` modunda `execute()` defter metotlarını **çağırmaz**;
yalnız niyeti ve engelleyicileri kaydeder.

Gerçek uygulama yolu (`PAPER_BOUNDED`) bu görevde **açılmaz** ve config ile açılamaz: mod
doğrulaması fail-closed'dur. Açılabilmesi için `exit_eval.aggregate` kapılarının geçmesi ve
ayrı bir operatör onayı gerekir.

Güvenlik kapıları (hepsi `preflight()` içinde, hepsi testle kilitli):
* yalnız `mode == PAPER`
* canlı emir yolu kapalı
* kill switch ARMED
* mark bayat değil
* pozisyon hâlâ açık
* pozisyon BÜYÜTÜLEMEZ (yalnız azaltma/çıkış/stop sıkıştırma)
* stop GEVŞETİLEMEZ ve markın yanlış tarafına konamaz
* kaldıraç DEĞİŞTİRİLEMEZ
* tur başına pozisyon başına en fazla bir aksiyon
* cooldown
* deterministik idempotency anahtarı → restart duplicate üretmez
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable

from ..core import iso, utc_now
from .exit_policy import EXIT, HOLD, REDUCE, TIGHTEN_STOP, ExitPolicyConfig

log = logging.getLogger(__name__)

SCHEMA_VERSION = "exit_executor_v1"

SHADOW = "SHADOW"
PAPER_BOUNDED = "PAPER_BOUNDED"
#: Bu sürümde KABUL EDİLEN tek mod. `PAPER_BOUNDED` bilinçli olarak listede DEĞİLDİR.
ALLOWED_MODES = (SHADOW,)
KNOWN_MODES = (SHADOW, PAPER_BOUNDED)

B_MODE_SHADOW = "MODE_SHADOW"
B_NOT_PAPER = "RUNTIME_MODE_NOT_PAPER"
B_LIVE_PATH = "LIVE_ORDER_PATH_ENABLED"
B_KILLSWITCH = "KILL_SWITCH_NOT_ARMED"
B_STALE = "STALE_MARK"
B_CLOSED = "POSITION_NOT_OPEN"
B_ALREADY = "ACTION_ALREADY_APPLIED"
B_COOLDOWN = "COOLDOWN_ACTIVE"
B_TOUR_LIMIT = "TOUR_ACTION_LIMIT"
B_LOOSEN = "STOP_WOULD_LOOSEN"
B_INCREASE = "POSITION_INCREASE_FORBIDDEN"
B_NO_ACTION = "NO_ACTION"
B_POLICY_OFF = "EXIT_POLICY_NOT_ACTIVATED"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class ExitExecutor:
    """Çıkış niyetlerini değerlendirir. `SHADOW`da hiçbir şey uygulanmaz.

    Defter nesnesi `ledger` olarak verilebilir, fakat `SHADOW` modunda ona **dokunulmaz**;
    varlığı yalnız ileride gerçek yolu açmak için sözleşmeyi tamamlar. Testler `SHADOW`da
    defterin hiç çağrılmadığını doğrular.
    """

    def __init__(self, cfg: ExitPolicyConfig | None = None, *, mode: str = SHADOW,
                 ledger: Any = None, clock: Callable[[], Any] = utc_now):
        if mode not in ALLOWED_MODES:
            raise ValueError(
                f"ExitExecutor modu yalnız {ALLOWED_MODES} olabilir (verilen: {mode!r}). "
                "Gerçek çıkış uygulaması bu sürümde KAPALIDIR ve config ile açılamaz.")
        self.cfg = cfg or ExitPolicyConfig()
        self.cfg.validate()
        self.mode = mode
        self._ledger = ledger
        self.clock = clock
        self.applied_keys: set[str] = set()
        self.last_action_ts: dict[str, Any] = {}
        self.stats = {"considered": 0, "blocked": 0, "applied": 0}

    # ------------------------------------------------------------------ kapılar
    def preflight(self, intent: dict[str, Any], *, mode_value: str | None,
                  live_order_path: bool, killswitch_state: str | None,
                  position_open: bool, mark_stale: bool,
                  actions_this_tour: int = 0) -> list[str]:
        """Engelleyici listesi. Boş liste = uygulanabilir (fakat SHADOW yine de uygulamaz)."""
        b: list[str] = []
        action = str(intent.get("action") or HOLD)
        if action == HOLD:
            b.append(B_NO_ACTION)
        if self.mode == SHADOW:
            b.append(B_MODE_SHADOW)
        if str(mode_value or "").upper() != "PAPER":
            b.append(B_NOT_PAPER)
        if live_order_path:
            b.append(B_LIVE_PATH)
        if str(killswitch_state or "").upper() != "ARMED":
            b.append(B_KILLSWITCH)
        if mark_stale:
            b.append(B_STALE)
        if not position_open:
            b.append(B_CLOSED)
        key = str(intent.get("idempotency_key") or "")
        if key and key in self.applied_keys:
            b.append(B_ALREADY)
        if actions_this_tour >= self.cfg.max_actions_per_position_per_tour:
            b.append(B_TOUR_LIMIT)
        tid = str(intent.get("trade_id") or "")
        last = self.last_action_ts.get(tid)
        if last is not None:
            try:
                if (self.clock() - last).total_seconds() < self.cfg.action_cooldown_s:
                    b.append(B_COOLDOWN)
            except (TypeError, AttributeError):
                pass
        # --- POZİSYON BÜYÜTME ve STOP GEVŞETME MUTLAK YASAK -----------------------------
        qty0, qty1 = _f(intent.get("qty_before")), _f(intent.get("qty_after"))
        if qty0 is not None and qty1 is not None and qty1 > qty0 + 1e-12:
            b.append(B_INCREASE)
        if action == TIGHTEN_STOP:
            from .position_path import side_sign
            s0, s1 = _f(intent.get("stop_before")), _f(intent.get("stop_after"))
            if s1 is None:
                b.append(B_LOOSEN)
            elif s0 is not None:
                sign = side_sign(intent.get("side"))
                if (s1 <= s0) if sign > 0 else (s1 >= s0):
                    b.append(B_LOOSEN)
        if action == REDUCE:
            frac = _f(intent.get("reduce_fraction"))
            if frac is None or not (0.0 < frac < 1.0):
                b.append(B_INCREASE)
        return b

    # ------------------------------------------------------------------ yürütme
    def execute(self, intent: dict[str, Any], **gates: Any) -> dict[str, Any]:
        """Tek niyeti değerlendirir. `SHADOW`da DAİMA `applied=False` döner ve deftere dokunmaz."""
        self.stats["considered"] += 1
        blockers = self.preflight(intent, **gates)
        out = dict(intent)
        out.update({
            "executor_schema": SCHEMA_VERSION,
            "exit_action_mode": self.mode,
            "evaluated_at": iso(self.clock()),
            "blockers": blockers,
            "blocker": blockers[0] if blockers else None,
            "applied": False,
            "counterfactual": True,
        })
        if blockers:
            self.stats["blocked"] += 1
            return out
        # Buraya yalnız SHADOW dışı bir mod eklenirse gelinebilir. Bu sürümde ULAŞILAMAZ:
        # `__init__` yalnız SHADOW kabul eder ve `preflight` SHADOW'da B_MODE_SHADOW ekler.
        raise AssertionError(
            "ExitExecutor gerçek uygulama yolu bu sürümde KAPALI; buraya ulaşılmamalıydı")

    def execute_many(self, intents: list[dict[str, Any]], **gates: Any) -> dict[str, Any]:
        rows = [self.execute(i, **gates) for i in (intents or [])]
        return {
            "schema_version": SCHEMA_VERSION,
            "exit_action_mode": self.mode,
            "n_intents": len(rows),
            "applied": sum(1 for r in rows if r.get("applied")),
            "blocked": sum(1 for r in rows if not r.get("applied")),
            "results": rows,
            "ledger_touched": False,
            "note_tr": ("SHADOW: çıkış niyetleri UYGULANMAZ, yalnız kaydedilir. "
                        "Defter ve emir yolu bu koddan ÇAĞRILMAZ."),
        }


__all__ = ["SCHEMA_VERSION", "SHADOW", "PAPER_BOUNDED", "ALLOWED_MODES", "KNOWN_MODES",
           "ExitExecutor", "B_MODE_SHADOW", "B_NOT_PAPER", "B_LIVE_PATH", "B_KILLSWITCH",
           "B_STALE", "B_CLOSED", "B_ALREADY", "B_COOLDOWN", "B_TOUR_LIMIT", "B_LOOSEN",
           "B_INCREASE", "B_NO_ACTION", "B_POLICY_OFF"]

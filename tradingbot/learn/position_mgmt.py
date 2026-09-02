"""Açık pozisyon yönetim gözlemi (`position_mgmt_v1`) — SALT OKUNUR, tavsiye niteliğinde.

Bu modül açık pozisyonlar için her doğal turda bir anlık görüntü üretir. HİÇBİR emir üretmez,
pozisyon kapatmaz, stop/TP değiştirmez ve RiskEngine'e dokunmaz.

**Neden var:** `coinhead/head.py` açık pozisyonlu bir sembol için `HOLD/REDUCE/EXIT` kararı
üretir (bkz. `head.py:226-236`) fakat motor bu kararları HİÇ TÜKETMEZ: `_execute_locked` yalnız
`d.is_actionable` adayları işler ve `is_actionable` sadece `SPOT_LONG/FUTURES_LONG/FUTURES_SHORT`
için True'dur (`schema.py:288`). Yani bugün `REDUCE`/`EXIT` yalnız ekrana yazılan bir görüştür.
Panel bunu `ADVISORY_ONLY` olarak AÇIKÇA göstermek zorundadır; aksi halde kullanıcı botun
pozisyonu yönettiğini sanır.

**Sahte sıfır yasağı:** aynı erken dönüş (`head.py:236`) ekonomik değerlendirmeden ÖNCE olur.
Bu yüzden açık pozisyonlarda `expected_r` dataclass varsayılanı olan `0.0`da kalır ve `p_win`
model önselidir. 2026-09-02 ölçümünde sekiz açık pozisyonun sekizinde de `expected_r=0.0` ve
`p_win≈0.38` çıktı. Bu ölçüm DEĞİL, doldurulmamış alandır. Bu modül böyle bir alanı `UNKNOWN`
olarak raporlar; `0.00` ya da `0.50` ÜRETMEZ.
"""
from __future__ import annotations

import math
from typing import Any

from ..core import from_iso, iso, utc_now

SCHEMA_VERSION = "position_mgmt_v1"

#: Ölçülemeyen ekonomi alanının değeri. Sayı DEĞİLDİR ve sayı yerine geçmez.
UNKNOWN = "UNKNOWN"

#: Önerinin bugünkü uygulanma durumu.
ADVISORY_ONLY = "ADVISORY_ONLY"
EXECUTABLE = "EXECUTABLE"

#: Önerilebilecek eylemler (CoinHead `Verdict` ile aynı adlar).
HOLD, REDUCE, EXIT = "HOLD", "REDUCE", "EXIT"
ACTIONS = (HOLD, REDUCE, EXIT)


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _age_hours(opened_at: Any, now=None) -> float | None:
    if not opened_at:
        return None
    try:
        t = from_iso(str(opened_at))
    except (ValueError, TypeError):
        return None
    return round(max(0.0, ((now or utc_now()) - t).total_seconds()) / 3600.0, 3)


def economics_available(decision: Any) -> bool:
    """Ekonomi GERÇEKTEN değerlendirildi mi.

    `_assess_opportunities` yalnız `is_actionable` adaylar için `opportunity` doldurur; açık
    pozisyonların kararı oraya hiç girmez. Dolayısıyla `opportunity` yoksa ekonomi
    DEĞERLENDİRİLMEMİŞTİR ve alanlar `UNKNOWN` olmalıdır.
    """
    if decision is None:
        return False
    opp = getattr(decision, "opportunity", None)
    if opp is None and isinstance(decision, dict):
        opp = decision.get("opportunity")
    return isinstance(opp, dict) and bool(opp)


def _opp_value(decision: Any, key: str) -> Any:
    opp = getattr(decision, "opportunity", None)
    if opp is None and isinstance(decision, dict):
        opp = decision.get("opportunity")
    return (opp or {}).get(key) if isinstance(opp, dict) else None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def r_metrics(position: Any, mark: Any) -> dict[str, Any]:
    """R cinsinden yaşayan metrikler. Stop mesafesi ölçülemezse hepsi `None` (uydurma yok).

    `giveback_r`: en iyi noktadan bugüne geri verilen R. `capture_ratio`: mevcut R'nin MFE_R'ye
    oranı; MFE sıfırsa oran TANIMSIZDIR ve `None` döner (0'a bölme ya da sahte 0 yok).
    """
    entry = _f(_get(position, "entry_avg")) or _f(_get(position, "entry"))
    stop0 = _f(_get(position, "initial_stop"))
    px = _f(mark)
    side = str(_get(position, "side", "") or "")
    side = getattr(side, "value", side)
    sign = 1.0 if str(side).upper().endswith("LONG") else -1.0
    dist = abs(entry - stop0) if (entry is not None and stop0 is not None) else None
    out: dict[str, Any] = {"entry": entry, "mark": px, "initial_stop": stop0,
                           "stop_distance_pct": round(dist / entry * 100.0, 4) if (dist and entry) else None}
    if not dist or entry is None:
        out.update({"current_net_r": None, "mfe_r": None, "mae_r": None,
                    "giveback_r": None, "capture_ratio": None,
                    "capture_ratio_state": "NO_STOP_DISTANCE"})
        return out
    cur_r = ((px - entry) * sign / dist) if px is not None else None
    mfe = _f(_get(position, "mfe_pct"))
    mae = _f(_get(position, "mae_pct"))
    mfe_r = abs(mfe) / 100.0 * entry / dist if mfe is not None else None
    mae_r = abs(mae) / 100.0 * entry / dist if mae is not None else None
    giveback = (mfe_r - cur_r) if (mfe_r is not None and cur_r is not None) else None
    if mfe_r is None or cur_r is None:
        cap, cap_state = None, "NOT_MEASURABLE"
    elif mfe_r <= 1e-9:
        cap, cap_state = None, "NO_FAVORABLE_EXCURSION"
    else:
        cap, cap_state = round(cur_r / mfe_r, 4), "OK"
    out.update({
        "current_net_r": round(cur_r, 4) if cur_r is not None else None,
        "mfe_r": round(mfe_r, 4) if mfe_r is not None else None,
        "mae_r": round(mae_r, 4) if mae_r is not None else None,
        "giveback_r": round(giveback, 4) if giveback is not None else None,
        "capture_ratio": cap, "capture_ratio_state": cap_state})
    return out


def proposed_action(decision: Any, position: Any) -> tuple[str, str]:
    """CoinHead görüşünü `HOLD/REDUCE/EXIT` + gerekçeye çevirir.

    Karar yoksa `HOLD` + `NO_DECISION` döner: veri yokluğu "çık" anlamına GELMEZ.
    """
    if decision is None:
        return HOLD, "NO_DECISION"
    v = _get(decision, "verdict")
    v = str(getattr(v, "value", v) or "").upper()
    reason = str(_get(decision, "no_trade_reason") or "") or ""
    if v in ACTIONS:
        return v, reason or v
    return HOLD, reason or f"VERDICT_{v or 'UNKNOWN'}"


def management_snapshot(*, position: Any, mark: Any, decision: Any = None,
                        trade_id: str | None = None, now=None,
                        executor_mode: str = ADVISORY_ONLY) -> dict[str, Any]:
    """Tek açık pozisyon için salt okunur yönetim gözlemi.

    Ekonomi değerlendirilmediyse `p_win`, `expected_net_return` ve `remaining_edge` alanları
    `UNKNOWN` dizesi olur. Bu bilinçlidir: sayı bekleyen bir tüketici `UNKNOWN`u sessizce
    0 sanamaz, açıkça patlar ya da atlar.
    """
    evaluated = economics_available(decision)
    m = r_metrics(position, mark)
    action, reason = proposed_action(decision, position)
    targets = _get(position, "targets") or []
    tid = trade_id or str(_get(position, "id") or "")
    fees = _f(_get(position, "fees_paid"))
    f_paid = _f(_get(position, "funding_paid")) or 0.0
    f_recv = _f(_get(position, "funding_received")) or 0.0
    side = _get(position, "side")
    row = {
        "schema_version": SCHEMA_VERSION,
        "at": iso(now or utc_now()),
        "trade_id": tid,
        "symbol": _get(position, "symbol"),
        "side": str(getattr(side, "value", side) or ""),
        "qty": _f(_get(position, "qty")),
        "leverage": _f(_get(position, "leverage")),
        "opened_at": _get(position, "opened_at"),
        "position_age_hours": _age_hours(_get(position, "opened_at"), now),
        "bars_held": _get(position, "bars_held"),
        "stop": _f(_get(position, "stop")),
        "targets": [t for t in (_f(x) for x in targets) if t is not None][:6],
        "targets_hit": _get(position, "targets_hit"),
        "tp1_done": bool(_get(position, "tp1_done")),
        "fees_paid": fees,
        "funding_net": round(f_recv - f_paid, 8),
        "regime": _get(decision, "regime"),
        "consensus_score": _f(_get(decision, "consensus_score")),
        "consensus_confidence": _f(_get(decision, "consensus_confidence")),
        # --- EKONOMİ: ölçülmediyse UNKNOWN. Varsayılan 0.00/0.50 ÜRETİLMEZ. ---
        "economics_evaluated": bool(evaluated),
        "p_win": (_f(_get(decision, "p_win")) if evaluated else UNKNOWN),
        "expected_net_return": (_f(_opp_value(decision, "conservative_net_edge_r"))
                                if evaluated else UNKNOWN),
        "remaining_edge": (_f(_opp_value(decision, "net_edge_r")) if evaluated else UNKNOWN),
        "economics_reason": (None if evaluated else
                             "OPEN_POSITION_NOT_ECONOMICALLY_EVALUATED"),
        # --- ÖNERİ: bugün YALNIZ tavsiye. Motor bu alanı okumaz. ---
        "proposed_action": action,
        "action_reason": reason,
        "action_mode": executor_mode,
        "executable": executor_mode == EXECUTABLE,
        "note_tr": ("Öneri yalnız gözlemdir; motor açık pozisyonu bu alandan yönetmez."
                    if executor_mode == ADVISORY_ONLY else
                    "Öneri uygulanabilir modda."),
    }
    row.update(m)
    return row


def build_snapshot_doc(rows: list[dict[str, Any]], *, run_id: str | None = None,
                       executor_mode: str = ADVISORY_ONLY, now=None) -> dict[str, Any]:
    """State dosyasına yazılacak belge. Sayılar UYDURULMAZ; boş liste boş kalır."""
    n_unknown = sum(1 for r in rows if not r.get("economics_evaluated"))
    by_action: dict[str, int] = {}
    for r in rows:
        by_action[r.get("proposed_action") or HOLD] = by_action.get(r.get("proposed_action") or HOLD, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now or utc_now()),
        "run_id": run_id,
        "action_mode": executor_mode,
        "executable": executor_mode == EXECUTABLE,
        "n_positions": len(rows),
        "n_economics_unknown": n_unknown,
        "by_action": by_action,
        "positions": rows,
        "note_tr": ("REDUCE/EXIT önerileri bugün UYGULANMAZ (ADVISORY_ONLY): motor açık "
                    "pozisyonları yalnız stop/TP/likidasyon ile kapatır."
                    if executor_mode == ADVISORY_ONLY else
                    "Öneriler uygulanabilir modda."),
    }


class ManagementExecutor:
    """İleride gerçek PAPER azaltma/çıkış uygulayabilecek sözleşme — BUGÜN YALNIZ `SHADOW`.

    Yapısal izolasyon: bu sınıf bir execution gateway, order outbox ya da defter nesnesi KABUL
    ETMEZ. `plan()` saf bir niyet listesi döndürür ve `execute()` `SHADOW` modunda o niyeti
    yalnız kaydeder. Gerçek uygulama yolu ayrı ve açık bir operatör onayıyla, out-of-sample
    kanıt oluştuktan sonra eklenecektir.

    `mode` yalnız `SHADOW` olabilir; başka bir değer `ValueError` ile REDDEDİLİR (fail-closed).
    """

    SHADOW = "SHADOW"
    MODES = (SHADOW,)

    def __init__(self, mode: str = SHADOW):
        if mode not in self.MODES:
            raise ValueError(
                f"ManagementExecutor modu yalnız {self.MODES} olabilir (verilen: {mode!r}); "
                "gerçek çıkış uygulaması bu sürümde KAPALIDIR")
        self.mode = mode
        self.planned = 0

    def plan(self, snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Niyet listesi üretir. Emir DEĞİLDİR ve hiçbir yere gönderilmez."""
        out = []
        for r in snapshot_rows or []:
            act = r.get("proposed_action")
            if act in (REDUCE, EXIT):
                out.append({"schema_version": SCHEMA_VERSION, "mode": self.mode,
                            "trade_id": r.get("trade_id"), "symbol": r.get("symbol"),
                            "intent": act, "reason": r.get("action_reason"),
                            "applied": False, "blocker": "EXIT_POLICY_NOT_ACTIVATED"})
        self.planned += len(out)
        return out

    def execute(self, intents: list[dict[str, Any]]) -> dict[str, Any]:
        """SHADOW: hiçbir şey uygulanmaz. `applied` daima False'tur."""
        return {"schema_version": SCHEMA_VERSION, "mode": self.mode,
                "n_intents": len(intents or []), "applied": 0,
                "blocked": len(intents or []),
                "note_tr": "SHADOW modunda çıkış niyeti UYGULANMAZ; yalnız kaydedilir."}


__all__ = ["SCHEMA_VERSION", "UNKNOWN", "ADVISORY_ONLY", "EXECUTABLE", "HOLD", "REDUCE", "EXIT",
           "ACTIONS", "economics_available", "r_metrics", "proposed_action",
           "management_snapshot", "build_snapshot_doc", "ManagementExecutor"]

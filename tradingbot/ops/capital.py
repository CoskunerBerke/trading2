"""PAPER sermaye katkısı — denetlenebilir, idempotent, atomik; PnL'ye ASLA dokunmaz.

Sözleşme:
* Sermaye katkısı KÂR DEĞİLDİR: `starting_equity` (contributed capital) ve nakit/bakiye AYNI
  tutarda artar → `equity − starting_equity` (toplam PnL) DEĞİŞMEZ.
* Pozisyonlar, stop/TP, history, fee/funding, entries, seq — kısacası katkı dışındaki HER bayt
  AYNEN korunur: dosya ham JSON olarak okunur, YALNIZ hedef alanlar güncellenir, atomik yazılır
  (round-trip serileştirme riski yok).
* İdempotent: aynı `adjustment_id` ikinci kez uygulanmaz (audit kaydı döner, no-op).
* Yalnız PAPER modunda ve WORKER DURMUŞKEN çalışır (canlı yazıcıyla yarış yasak).
* Denetim: `state/capital_adjustments.json` append-only kayıt (miktar, önce/sonra taban,
  zaman, run-id, code SHA, dosya sha256'ları).
* LIVE/TESTNET ile İLGİSİ YOKTUR; gerçek para hareketi üretemez.
"""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json, utc_now

CAPITAL_SCHEMA_VERSION = "paper_capital_v1"
AUDIT_NAME = "capital_adjustments.json"


class CapitalError(Exception):
    """Katkı uygulanamadı — fail-closed: hiçbir dosya değişmedi."""


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _dnum(x: Any) -> Decimal:
    return Decimal(str(x if x is not None else "0"))


def _worker_running(state_dir: Path) -> bool:
    """`.lock` dosyasındaki PID canlıysa worker çalışıyordur (Linux/Windows uyumlu)."""
    lock = state_dir / ".lock"
    if not lock.exists():
        return False
    try:
        pid = int(lock.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return True                                # okunamayan kilit → muhafazakâr: çalışıyor say
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False                               # bayat kilit — süreç yok
    except PermissionError:
        return True
    except OSError:
        return True


def _position_fingerprint(doc: dict[str, Any]) -> str:
    """Pozisyonların değişmezlik parmak izi — katkı ÖNCESİ/SONRASI birebir aynı olmalı."""
    pos = doc.get("positions") or {}
    items = pos.items() if isinstance(pos, dict) else ((p.get("symbol"), p) for p in pos)
    src = sorted(json.dumps({"s": k, **{f: v for f, v in (p or {}).items()}},
                            sort_keys=True, default=str) for k, p in items)
    return hashlib.sha256("\n".join(src).encode()).hexdigest()[:16]


def contribute_paper_capital(state_dir: Path | str, *, futures_add: float, spot_add: float,
                             adjustment_id: str, operator: str = "operator",
                             code_sha: str | None = None,
                             expected_mode: str = "PAPER") -> dict[str, Any]:
    """Her iki PAPER defterine sermaye katkısı uygular. Dönen: denetim kaydı.

    Sıra: ön koşullar → idempotency → futures (starting+wallet) → spot (starting+cash)
    → audit. Her dosya atomik yazılır; ilk hata `CapitalError` ile durur (o ana kadar
    yazılmamış dosyalar dokunulmamış kalır; audit ancak İKİSİ de başarılıysa yazılır).
    """
    st = Path(state_dir)
    f_add, s_add = _dnum(futures_add), _dnum(spot_add)
    if f_add < 0 or s_add < 0:
        raise CapitalError("negatif katkı desteklenmez (çekim ayrı ve açık bir karardır)")
    if not adjustment_id or len(str(adjustment_id)) < 4:
        raise CapitalError("adjustment_id zorunlu (idempotency anahtarı)")

    mode_doc = read_json(st / "mode.json", default=None) or {}
    mode = str(mode_doc.get("mode") or "")
    if mode != expected_mode:
        raise CapitalError(f"yalnız {expected_mode} modunda çalışır (mevcut: {mode or 'yok'})")
    if bool(mode_doc.get("live_order_path_enabled")):
        raise CapitalError("live_order_path açık — katkı reddedildi (fail-closed)")
    if _worker_running(st):
        raise CapitalError("worker çalışıyor (.lock aktif) — önce kontrollü durdurun")

    audit_path = st / AUDIT_NAME
    audit = read_json(audit_path, default=None) or {"schema_version": CAPITAL_SCHEMA_VERSION,
                                                    "adjustments": []}
    for a in audit.get("adjustments") or []:
        if str(a.get("adjustment_id")) == str(adjustment_id):
            return {**a, "applied": False, "idempotent_noop": True}

    fut_path, spot_path = st / "futures_ledger.json", st / "spot_ledger.json"
    fut = read_json(fut_path, default=None)
    spot = read_json(spot_path, default=None)
    if not isinstance(fut, dict) or "starting_equity" not in fut:
        raise CapitalError(f"futures defteri okunamadı/şema eski: {fut_path}")
    if not isinstance(spot, dict) or "starting_equity" not in spot:
        raise CapitalError(f"spot defteri okunamadı/şema eski: {spot_path}")

    fp_before = _position_fingerprint(fut)
    n_hist_before = len(fut.get("history") or [])
    fut_start_prev = _dnum(fut.get("starting_equity"))
    fut_wallet_prev = _dnum(fut.get("wallet_balance", fut.get("equity")))
    spot_start_prev = _dnum(spot.get("starting_equity"))
    spot_cash_prev = _dnum(spot.get("cash"))
    pnl_fut_before = fut_wallet_prev - fut_start_prev          # gerçekleşmiş taraf (cüzdan bazlı)

    # --- YALNIZ hedef alanlar güncellenir; diğer her anahtar AYNEN kalır ---
    fut["starting_equity"] = str(fut_start_prev + f_add)
    fut["wallet_balance"] = str(fut_wallet_prev + f_add)
    if "equity" in fut:                                        # v2 ayna alanı
        fut["equity"] = fut["wallet_balance"]
    meta = fut.get("meta") if isinstance(fut.get("meta"), dict) else {}
    contribs = list(meta.get("capital_contributions") or [])
    contribs.append({"adjustment_id": str(adjustment_id), "amount": str(f_add),
                     "at": iso(utc_now()), "operator": str(operator)})
    meta["capital_contributions"] = contribs
    fut["meta"] = meta

    spot["starting_equity"] = str(spot_start_prev + s_add)
    spot["cash"] = str(spot_cash_prev + s_add)
    smeta = spot.get("meta") if isinstance(spot.get("meta"), dict) else {}
    scontribs = list(smeta.get("capital_contributions") or [])
    scontribs.append({"adjustment_id": str(adjustment_id), "amount": str(s_add),
                      "at": iso(utc_now()), "operator": str(operator)})
    smeta["capital_contributions"] = scontribs
    spot["meta"] = smeta

    # --- değişmezlik doğrulaması YAZMADAN ÖNCE ---
    if _position_fingerprint(fut) != fp_before:
        raise CapitalError("pozisyon parmak izi değişti — uygulanmadı (iç hata)")
    if len(fut.get("history") or []) != n_hist_before:
        raise CapitalError("history değişti — uygulanmadı (iç hata)")
    pnl_fut_after = _dnum(fut["wallet_balance"]) - _dnum(fut["starting_equity"])
    if pnl_fut_after != pnl_fut_before:
        raise CapitalError("PnL korunmadı — uygulanmadı (iç hata)")

    atomic_write_json(fut_path, fut, keep_backup=True)
    atomic_write_json(spot_path, spot, keep_backup=True)

    rec = {"schema_version": CAPITAL_SCHEMA_VERSION, "adjustment_id": str(adjustment_id),
           "at": iso(utc_now()), "operator": str(operator), "mode": mode,
           "code_sha": code_sha,
           "futures": {"added": str(f_add),
                       "starting_prev": str(fut_start_prev),
                       "starting_new": str(fut_start_prev + f_add),
                       "wallet_prev": str(fut_wallet_prev),
                       "wallet_new": str(fut_wallet_prev + f_add),
                       "pnl_preserved": str(pnl_fut_before),
                       "position_fingerprint": fp_before,
                       "n_open_positions": len(fut.get("positions") or {}),
                       "n_history": n_hist_before,
                       "file_sha_after": _sha(fut_path)},
           "spot": {"added": str(s_add),
                    "starting_prev": str(spot_start_prev),
                    "starting_new": str(spot_start_prev + s_add),
                    "cash_prev": str(spot_cash_prev),
                    "cash_new": str(spot_cash_prev + s_add),
                    "file_sha_after": _sha(spot_path)},
           "total_contributed_after": str(fut_start_prev + f_add + spot_start_prev + s_add),
           "applied": True}
    audit["adjustments"] = (audit.get("adjustments") or []) + [rec]
    atomic_write_json(audit_path, audit, keep_backup=True)
    return rec


__all__ = ["AUDIT_NAME", "CAPITAL_SCHEMA_VERSION", "CapitalError", "contribute_paper_capital"]

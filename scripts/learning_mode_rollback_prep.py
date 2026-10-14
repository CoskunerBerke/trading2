# -*- coding: utf-8 -*-
"""ÖĞRENME MODU — GERİ ALMA HAZIRLIĞI (2026-09-28, öğrenme modu; ikinci doğrulama turu). Yalnız stdlib; PAPER durumu.

Neden: öğrenme modundan ÖNCEKİ kod (ör. 6662d5b / 7ad8832) Formasyon defterinin bekleyen planlarını evren ayrımı yapmadan
doldurur (`scheduler.queue` her bekleyen planı sıraya alır; NOT_IN_PROTOCOL_UNIVERSE yalnız YENİ planda denetlenir).
Öğrenmenin S9 ile kurduğu protokol DIŞI sembol planı ya da D16 likidite beklemesindeki plan, geri alınan kodda sıradan
protokol işlemi olarak (taban boyut, 1x, öğrenme etiketi YOK) açılırdı. Bu betik bu planları CANCELLED yapar
(neden `LEARNING_MODE_ROLLBACK`); başka hiçbir plana ve dosyaya dokunmaz.

Ne zaman: worker DURDURULMUŞKEN, eski koda dönmeden (checkout) ÖNCE ve eski kod başlatılmadan önce. Hizmet kullanıcısıyla
koşulması önerilir; root ile koşulursa dosyanın özgün sahipliği korunur. Özgün dosya yanına yedeklenir
(`plans.json.pre-rollback-<UTC>`).

Kullanım:  python scripts/learning_mode_rollback_prep.py --state /opt/tradingbot/data/state [--dry-run]
Çıkış kodu: 0 = değişiklik yok ya da yapıldı; 2 = plans.json okunamadı/bozuk (dosyaya dokunulmadı — elle bakın).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLANS_REL = Path("pattern_trader") / "plans.json"
#: Doldurulabilir (terminal olmayan, pozisyonu olmayan) plan durumları — `pattern_trader.strategy` ile aynı metinler.
PENDING = ("AWAITING_TRIGGER", "TRIGGERED", "RISK_CHECK")
CANCELLED = "CANCELLED"
REASON = "LEARNING_MODE_ROLLBACK"


def needs_cancel(pl: dict[str, Any]) -> str | None:
    """Plan eski kodda protokol dışı işlem olarak dolabilir mi? Döner: neden (etiket) ya da None."""
    if str(pl.get("status") or "") not in PENDING:
        return None
    if pl.get("in_lab_universe") is False:
        return "NOT_IN_PROTOCOL_UNIVERSE"
    if (pl.get("liquidity_wait") or {}).get("pending"):
        return "LIQUIDITY_WAIT"
    return None


def _atomic_write(path: Path, doc: Any, owner: tuple[int, int] | None) -> None:
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if owner is not None:
            try:
                os.chown(tmp, *owner)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def prepare(state_dir: Path | str, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """`state_dir/pattern_trader/plans.json` içinde eski kodun protokol dışı dolduracağı bekleyen planları iptal eder.
    Döner: {"path", "cancelled": [{plan_id, symbol, status, why}], "backup", "written"}. Dosya yoksa değişiklik yok."""
    path = Path(state_dir) / PLANS_REL
    out: dict[str, Any] = {"path": str(path), "cancelled": [], "backup": None, "written": False}
    if not path.exists():
        return out
    doc = json.loads(path.read_text(encoding="utf-8"))          # bozuksa ValueError → çağıran 2 ile çıkar
    plans = doc.get("plans") if isinstance(doc, dict) else None
    if not isinstance(plans, dict):
        raise ValueError("plans.json beklenen biçimde değil (plans sözlüğü yok)")
    now = now or datetime.now(timezone.utc)
    at_ms = int(now.timestamp() * 1000)
    at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    for pid, pl in plans.items():
        if not isinstance(pl, dict):
            continue
        why = needs_cancel(pl)
        if why is None:
            continue
        out["cancelled"].append({"plan_id": pl.get("plan_id") or pid, "symbol": pl.get("symbol"),
                                 "status": pl.get("status"), "why": why})
        if dry_run:
            continue
        pl["status"] = CANCELLED
        pl.setdefault("status_history", []).append({"status": CANCELLED, "at_ms": at_ms, "at": at,
                                                    "reason": "%s:%s" % (REASON, why)})
        pl.setdefault("reasons", []).append("%s:%s" % (REASON, why))
    if dry_run or not out["cancelled"]:
        return out
    st = path.stat()
    owner = (st.st_uid, st.st_gid) if hasattr(os, "chown") else None
    backup = path.with_name(path.name + ".pre-rollback-" + now.strftime("%Y%m%dT%H%M%SZ"))
    shutil.copy2(path, backup)
    if owner is not None:
        try:
            os.chown(backup, *owner)
        except OSError:
            pass
    _atomic_write(path, doc, owner)
    out.update(backup=str(backup), written=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Öğrenme modu geri alma hazırlığı (Formasyon planları)")
    ap.add_argument("--state", required=True, help="state dizini (ör. /opt/tradingbot/data/state)")
    ap.add_argument("--dry-run", action="store_true", help="yalnız raporla, yazma")
    a = ap.parse_args(argv)
    try:
        res = prepare(a.state, dry_run=a.dry_run)
    except (OSError, ValueError) as exc:
        print("HATA: plans.json okunamadı/yazılamadı (%s) — dosyaya dokunulmadı, elle bakın" % exc, file=sys.stderr)
        return 2
    for c in res["cancelled"]:
        print("   %s %s %s (%s → CANCELLED)" % ("İPTAL EDİLECEK" if a.dry_run else "İPTAL", c["symbol"], c["plan_id"],
                                              c["status"]) + " neden=" + c["why"])
    print("   öğrenme planı temizliği: %d plan%s" % (len(res["cancelled"]), " (kuru koşu)" if a.dry_run else "")
          + ("; yedek " + res["backup"] if res["backup"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

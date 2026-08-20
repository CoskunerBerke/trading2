"""Systemd başlangıç preflight'i — doctor sonucundan TİPLENMİŞ karar üretir (substring/grep YOK).

Karar tablosu (yalnız systemd ExecStartPre katmanı; normal `doctor` komutu gevşetilmez/sıkılmaz):
* heartbeat check'i TAM OLARAK BİR kez bulunmalı; yoksa/birden fazlaysa           → BLOCK
* HEARTBEAT_OK (ok=true)  + başka fail yok                                        → ALLOW
* HEARTBEAT_STALE (ok=false, severity=fail) + başarısızlık kümesi yalnız bu check → ALLOW + WARNING
  (worker kapalıyken heartbeat'in bayatlaması beklenen tek meşru istisnadır)
* HEARTBEAT_MISSING / HEARTBEAT_MALFORMED / boş ya da bilinmeyen code             → BLOCK
  (report.ok=true olsa bile: eksik/bozuk heartbeat "stale" SAYILMAZ, fail-closed)
* başka herhangi bir fail (tek başına ya da stale ile birlikte)                   → BLOCK
* report.ok ile check'lerden türetilen sonuç tutarsızsa                           → BLOCK
* doctor crash / geçersiz-eksik structured sonuç                                  → BLOCK

Karar yalnız `checks[].name`, `checks[].ok`, `checks[].severity`, `checks[].code` alanlarına bakar;
insan-okunur `detail` metni hiçbir zaman karara girmez. Hiçbir state/heartbeat dosyası yazılmaz.
"""
from __future__ import annotations

from typing import Any

STALE_CODE = "HEARTBEAT_STALE"
OK_CODE = "HEARTBEAT_OK"


def decide(report: Any) -> tuple[bool, str]:
    """(izin, gerekçe). `report` = DoctorReport.to_dict() sözlüğü; geçersiz/eksik her şey fail-closed BLOCK."""
    if not isinstance(report, dict):
        return False, "BLOCK: doctor sonucu yok/geçersiz (fail-closed)"
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        return False, "BLOCK: doctor checks listesi yok/boş (fail-closed)"
    for c in checks:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not isinstance(c.get("ok"), bool):
            return False, "BLOCK: doctor check kaydı şemasız (fail-closed)"
    hb_checks = [c for c in checks if c["name"] == "heartbeat"]
    if len(hb_checks) != 1:
        return False, f"BLOCK: heartbeat check sayısı {len(hb_checks)} (tam olarak 1 olmalı; fail-closed)"
    hb = hb_checks[0]
    code = str(hb.get("code") or "")
    failures = [c for c in checks if not c["ok"] and str(c.get("severity", "fail")) == "fail"]
    if bool(report.get("ok")) != (not failures):
        return False, "BLOCK: report.ok ile check sonuçları tutarsız (fail-closed)"
    if code == OK_CODE and hb["ok"]:
        if not failures:
            return True, "ALLOW: doctor tamamen başarılı (heartbeat taze)"
        names = ", ".join(f"{c['name']}({c.get('code') or c.get('severity')})" for c in failures)
        return False, f"BLOCK: doctor hataları: {names}"
    if code == STALE_CODE and not hb["ok"] and str(hb.get("severity", "fail")) == "fail":
        if failures == [hb]:
            return True, ("WARNING: yalnız bayat heartbeat (HEARTBEAT_STALE) — worker kapalıyken beklenen durum; "
                          "başlangıca izin verildi, başka hata yok")
        names = ", ".join(f"{c['name']}({c.get('code') or c.get('severity')})" for c in failures if c is not hb)
        return False, f"BLOCK: bayat heartbeat'e ek doctor hataları: {names or 'tutarsız heartbeat kaydı'}"
    # MISSING / MALFORMED / boş / bilinmeyen kod ya da kod-durum uyumsuzluğu → stale sayılmaz
    return False, f"BLOCK: heartbeat durumu başlangıç için yeterli değil (code={code or 'yok'}, ok={hb['ok']}; fail-closed)"


__all__ = ["decide", "STALE_CODE", "OK_CODE"]

"""Systemd başlangıç preflight'i — doctor sonucundan TİPLENMİŞ karar üretir (substring/grep YOK).

Karar tablosu (yalnız systemd ExecStartPre katmanı; normal `doctor` komutu gevşetilmez):
* doctor tamamen başarılı (ok=true)                          → ALLOW  (exit 0)
* başarısızlık kümesi TAM OLARAK {heartbeat & HEARTBEAT_STALE} → ALLOW + açık WARNING (worker kapalıyken
  heartbeat'in bayatlaması beklenen durumdur; başka hiçbir hata yoksa güvenli başlangıç engellenmez)
* stale-heartbeat + başka herhangi bir hata                  → BLOCK
* başka herhangi bir hata (tek başına dahil)                 → BLOCK
* heartbeat missing/malformed/unknown kodu ile FAIL          → BLOCK (stale sayılmaz)
* doctor crash / geçersiz-eksik structured sonuç             → BLOCK (fail-closed)

Karar yalnız `checks[].name`, `checks[].ok`, `checks[].severity`, `checks[].code` alanlarına bakar;
insan-okunur `detail` metni hiçbir zaman karara girmez.
"""
from __future__ import annotations

from typing import Any

STALE_CODE = "HEARTBEAT_STALE"


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
    if report.get("ok") is True:
        return True, "ALLOW: doctor tamamen başarılı"
    failures = [c for c in checks if not c["ok"] and str(c.get("severity", "fail")) == "fail"]
    if not failures:
        # ok=false ama fail yok → tutarsız rapor: fail-closed
        return False, "BLOCK: doctor ok=false ama fail listesi boş (tutarsız sonuç, fail-closed)"
    if len(failures) == 1 and failures[0]["name"] == "heartbeat" and failures[0].get("code") == STALE_CODE:
        return True, ("WARNING: yalnız bayat heartbeat (HEARTBEAT_STALE) — worker kapalıyken beklenen durum; "
                      "başlangıca izin verildi, başka hata yok")
    names = ", ".join(f"{c['name']}({c.get('code') or c.get('severity')})" for c in failures)
    return False, f"BLOCK: doctor hataları: {names}"


__all__ = ["decide", "STALE_CODE"]

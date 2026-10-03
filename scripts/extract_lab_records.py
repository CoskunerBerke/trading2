# -*- coding: utf-8 -*-
"""LABORATUVAR KAYDINI İŞ GÜNLÜĞÜNDEN ÇIKAR — `signal-lab.yml`in "Print lab records" adımının bastığı blokları okur ve
her kaydı BAYT BAYT yazar (çıktı/artifact indirmesi engelliyse). Yalnız standart kütüphane.

    python scripts/extract_lab_records.py job.log                      # → tradingbot/candle_lab_records/
    python scripts/extract_lab_records.py job.log --dry-run            # yalnız denetle, yazma
    python scripts/extract_lab_records.py job.log --out-dir /tmp/kayit

Desteklenen günlükler: ham günlük indirmesi (web), API `/actions/jobs/<id>/logs`, `gh run view <id> --log [--job <id>]`.
Blok biçimi (satırlarda GitHub zaman damgası öneki, `gh`in "<iş>\\t<adım>\\t" öneki, sonda boşluk ve \\r olabilir):

    === LAB RECORD BEGIN <ad>.json sha256=<64 onaltılık> bytes=<n>
    <dosyanın base64'ü, 76 karakterde sarılı>
    === LAB RECORD END <ad>.json

Her kayıt için base64 çözülür, bayt sayısı ve sha256 denetlenir. Uyuşmazlık, yinelenen ad, ad dışında yol parçası (yalnız
`[A-Za-z0-9_.-]+\\.json`), bozuk base64 ya da yarım blok → o kayıt YAZILMAZ ve çıkış kodu 2 olur (sağlam kayıtlar yine
yazılır). Hiç blok yoksa çıkış kodu 1. Her kayıt için bir satır basılır.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tradingbot" / "candle_lab_records"
#: GitHub iş günlüğü satır öneki, ör. "2026-09-26T14:07:13.4464420Z ". `gh run view --log` önüne ayrıca
#: "<iş>\t<adım>\t" koyar; o da (yalnız ardından zaman damgası geliyorsa) atılır.
_TS = re.compile(r"^(?:[^\t]*\t[^\t]*\t)?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ")
_BEGIN = re.compile(r"^=== LAB RECORD BEGIN (.+) sha256=([0-9a-f]{64}) bytes=(\d+)$")
_END = re.compile(r"^=== LAB RECORD END (.+)$")
_NAME = re.compile(r"[A-Za-z0-9_.-]+\.json")
_B64 = re.compile(r"[A-Za-z0-9+/=]*")


def _lines(text: str) -> list[str]:
    out = []
    for raw in text.split("\n"):
        line = raw.lstrip("\ufeff")
        m = _TS.match(line)
        # Sondaki boşluk/sekme/\r atılır (kopyala-yapıştır); işaret ve base64 satırları boşlukla bitmez.
        out.append((line[m.end():] if m else line).rstrip(" \t\r"))
    return out


def parse_log(text: str) -> list[dict]:
    """Günlükteki bloklar, sırayla: {"name", "sha256", "bytes", "data" (bytes | None), "error" (str | None)}."""
    blocks: list[dict] = []
    cur: dict | None = None

    def close(error: str | None) -> None:
        cur["error"] = cur["error"] or error
        blocks.append(cur)

    for line in _lines(text):
        mb, me = _BEGIN.match(line), _END.match(line)
        if mb:
            if cur is not None:
                close("yarım blok (END yok)")
            cur = {"name": mb.group(1), "sha256": mb.group(2), "bytes": int(mb.group(3)), "b64": [], "data": None,
                   "error": None}
        elif me:
            if cur is None:
                blocks.append({"name": me.group(1), "sha256": None, "bytes": None, "b64": [], "data": None,
                               "error": "yarım blok (BEGIN yok)"})
                continue
            if me.group(1) != cur["name"]:
                close(f"END adı uyuşmuyor ({me.group(1)})")
            else:
                close(None)
            cur = None
        elif cur is not None:
            if not _B64.fullmatch(line):
                cur["error"] = cur["error"] or "bozuk base64 satırı"
            cur["b64"].append(line)
    if cur is not None:
        close("yarım blok (END yok)")
    for b in blocks:
        chunk = "".join(b.pop("b64"))
        if b["error"]:
            continue
        if not _NAME.fullmatch(b["name"]):
            b["error"] = "geçersiz ad (yalnız [A-Za-z0-9_.-]+.json)"
            continue
        try:
            data = base64.b64decode(chunk, validate=True)
        except (binascii.Error, ValueError):
            b["error"] = "bozuk base64"
            continue
        if len(data) != b["bytes"]:
            b["error"] = f"bayt sayısı uyuşmuyor ({len(data)} ≠ {b['bytes']})"
        elif hashlib.sha256(data).hexdigest() != b["sha256"]:
            b["error"] = "sha256 uyuşmuyor"
        else:
            b["data"] = data
    names = [b["name"] for b in blocks]
    for b in blocks:
        if names.count(b["name"]) > 1:
            b["error"], b["data"] = "yinelenen ad", None
    return blocks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="signal-lab iş günlüğünden laboratuvar kayıtlarını bayt bayt çıkarır.")
    ap.add_argument("log", type=Path, help="kaydedilmiş iş günlüğü (metin)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="hedef klasör (varsayılan: tradingbot/candle_lab_records)")
    ap.add_argument("--dry-run", action="store_true", help="yalnız denetle, hiçbir şey yazma")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    blocks = parse_log(a.log.read_bytes().decode("utf-8", errors="replace"))
    if not blocks:
        print("kayıt bloğu bulunamadı")
        return 1
    bad = 0
    for b in blocks:
        if b["error"]:
            bad += 1
            print(f"RED {b['name']}: {b['error']} — yazılmadı")
            continue
        dest = a.out_dir / b["name"]
        if a.dry_run:
            print(f"OK  {b['name']} {b['bytes']} bayt sha256={b['sha256']} → {dest} (deneme: yazılmadı)")
            continue
        a.out_dir.mkdir(parents=True, exist_ok=True)
        same = dest.exists() and dest.read_bytes() == b["data"]
        dest.write_bytes(b["data"])
        print(f"OK  {b['name']} {b['bytes']} bayt sha256={b['sha256']} → {dest}{' (aynısı vardı)' if same else ''}")
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

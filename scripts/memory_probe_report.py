#!/usr/bin/env python3
"""`memory_probe.jsonl` özeti — cgroup sınırına ne kadar yaklaşıldı, sınır aşıldı mı, restart oldu mu.

Kullanım:
    python scripts/memory_probe_report.py [--file PATH] [--since ISO] [--rows N]

Kural: bütün bellek alanları BAYT'tır; rapor MiB olarak yazar ve birimi her satırda söyler.
Örnek yoksa ya da pencere kısaysa bunu AÇIKÇA söyler — "yeterli veri var" varsayımı yapılmaz.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime

try:                                   # Windows konsolunda cp1254; rapor UTF-8 yazar
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 — yeniden yapilandirilamayan akis sorun degil
    pass

MIB = 1024 * 1024
DEFAULT = "/opt/tradingbot/data/logs/memory_probe.jsonl"


def _num(v) -> float | None:
    if v in (None, "", "max"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(path: str, since: str | None) -> list[dict]:
    rows: list[dict] = []
    try:
        fh = io.open(path, encoding="utf-8")
    except OSError as exc:
        print(f"örnek dosyası okunamadı: {exc}", file=sys.stderr)
        return rows
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue                       # yarım yazılmış son satır — atla, uydurma
            if since and str(r.get("ts", "")) < since:
                continue
            rows.append(r)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--since", default=None, help="ISO-8601, ör. 2026-09-10T00:00:00Z")
    ap.add_argument("--rows", type=int, default=0, help="son N örneği tablo olarak yaz")
    a = ap.parse_args()

    rows = load(a.file, a.since)
    if not rows:
        print("ÖRNEK YOK. Timer kurulmamış ya da pencere boş olabilir:")
        print("  systemctl status tradingbot-memprobe.timer")
        return 2

    first, last = rows[0], rows[-1]
    t0 = datetime.fromisoformat(first["ts"].replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(last["ts"].replace("Z", "+00:00"))
    hours = (t1 - t0).total_seconds() / 3600.0

    limit = _num(last.get("memory_max_bytes"))
    cur = [(_num(r.get("memory_current_bytes")), r) for r in rows]
    cur = [(v, r) for v, r in cur if v is not None]
    peaks = [(_num(r.get("memory_peak_bytes")), r) for r in rows]
    peaks = [(v, r) for v, r in peaks if v is not None]
    rss = [_num(r.get("rss_bytes")) for r in rows]
    rss = [v for v in rss if v is not None]

    ev_max = max((r.get("events", {}).get("max", 0) or 0) for r in rows)
    ev_oom = max((r.get("events", {}).get("oom", 0) or 0) for r in rows)
    ev_kill = max((r.get("events", {}).get("oom_kill", 0) or 0) for r in rows)
    ev_max0 = min((r.get("events", {}).get("max", 0) or 0) for r in rows)
    ev_kill0 = min((r.get("events", {}).get("oom_kill", 0) or 0) for r in rows)
    restarts = sorted({int(r.get("nrestarts") or 0) for r in rows})
    tours = [_num(r.get("last_tour_seconds")) for r in rows]
    tours = [v for v in tours if v]

    print(f"pencere      : {first['ts']} -> {last['ts']}  ({hours:.2f} saat, {len(rows)} örnek)")
    print(f"birim        : bütün bellek değerleri BAYT olarak toplanır, burada MiB olarak yazılır")
    if limit:
        print(f"cgroup sınırı: {limit/MIB:,.0f} MiB")
    if cur:
        hi_cur, hi_row = max(cur, key=lambda x: x[0])
        print(f"memory.current: son {cur[-1][0]/MIB:,.0f} MiB · tepe {hi_cur/MIB:,.0f} MiB @ {hi_row['ts']}"
              + (f" · sınırın %{hi_cur/limit*100:.1f}'i" if limit else ""))
    if peaks:
        hi_pk = max(v for v, _ in peaks)
        print(f"memory.peak  : {hi_pk/MIB:,.0f} MiB" + (f" · sınırın %{hi_pk/limit*100:.1f}'i" if limit else "")
              + "   (cgroup'un kendi tuttuğu en yüksek değer)")
    if rss:
        print(f"süreç RSS    : son {rss[-1]/MIB:,.0f} MiB · tepe {max(rss)/MIB:,.0f} MiB")
    print(f"memory.events: max {ev_max0}->{ev_max} · oom {ev_oom} · oom_kill {ev_kill0}->{ev_kill}")
    print(f"NRestarts    : {restarts}")
    if tours:
        print(f"tur süresi   : son {tours[-1]:.0f} sn · en uzun {max(tours):.0f} sn (n={len(tours)})")
    snap = _num(last.get("entry_snapshot_bytes")) or 0
    snap0 = _num(first.get("entry_snapshot_bytes")) or 0
    print(f"entry_snapshot.jsonl: {snap0/MIB:,.1f} -> {snap/MIB:,.1f} MiB"
          + (f"  (+{(snap-snap0)/MIB/max(hours,1e-9):,.2f} MiB/saat)" if hours > 0.5 else ""))

    print()
    verdict = []
    if ev_kill > ev_kill0:
        verdict.append("PENCEREDE OOM-KILL OLDU — onarım yetersiz.")
    elif ev_max > ev_max0:
        verdict.append("Sınıra DAYANMA (memory.events.max arttı) var ama öldürme yok.")
    else:
        verdict.append("Pencerede sınıra dayanma ve OOM-kill YOK.")
    if len(restarts) > 1:
        verdict.append(f"Worker bu pencerede yeniden başladı (NRestarts {restarts[0]}->{restarts[-1]}).")
    if hours < 6:
        verdict.append(f"UYARI: pencere {hours:.1f} saat — uzun süreli OOM doğrulaması için KISA. "
                       "En az bir tam gün örnek biriktirmeden 'OOM kapandı' denmez.")
    for v in verdict:
        print(" ·", v)

    if a.rows:
        print()
        print(f"{'ts':21} {'current MiB':>12} {'peak MiB':>10} {'RSS MiB':>9} {'restart':>8} {'tur sn':>7}")
        for r in rows[-a.rows:]:
            c = _num(r.get("memory_current_bytes")) or 0
            p = _num(r.get("memory_peak_bytes")) or 0
            s = _num(r.get("rss_bytes")) or 0
            t = _num(r.get("last_tour_seconds")) or 0
            print(f"{r['ts']:21} {c/MIB:12,.0f} {p/MIB:10,.0f} {s/MIB:9,.0f} {int(r.get('nrestarts') or 0):8d} {t:7.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

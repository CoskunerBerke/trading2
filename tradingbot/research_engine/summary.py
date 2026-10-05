"""S7 — özet (`digest_tr.md`, `engine_summary.json`, `run_status.json`) ve `engine-status [--brief]` metni (§6.1 S7,
§7.7, §8; P1a kabul 5 ve 17).

* `digest_tr.md` ≤ 8 KB (AI'nin okuduğu tek özet; ≈ 2,5k token). Başlık satırları §7.7 biçimindedir
  (`daily_target.render_brief`): yalnız-gerçekleşmiş bir satırda `TUTTU` / `HEDEF GÜNÜ` kelimesi HİÇ geçmez; her
  gerçekleşmiş sayının yanında açık pozisyonların gerçekleşmemiş kârı yazar; `HEDEF GÜNÜ` yalnız KESİN bir MTM gününün
  satırında ve "başarı değildir" notuyla görünür. `TUTTU` hiçbir yerde yazılmaz. k*/kaldıraç satırı YOKTUR (§7.6).
* `engine_summary.json` ≤ 256 KB (panel P5'te yalnız JSON olarak okur; şema sabiti `SUMMARY_SCHEMA`).
* `engine-status --brief` ≤ 60 satır: son çalıştırma, öz-denetim sonucu, SKEW, A/B durumu, rotasyon payları, günlük hedef
  tablosu (GEÇİCİ/KESİN). `engine-status` (ayrıntılı) aşama hatalarını, defter uzlaştırmasını, son çalıştırmaları ve 14
  günlük tabloyu ekler. Durum komutu SALT-OKUNURDUR: kilit almaz, hiçbir şey yazmaz, ledger okumaz; yalnız
  `data/research` altındaki son çıktıları okur.

Bütün metin Türkçe ve şablondandır (LLM yok); sayılar Türkçe ondalık virgülüyle yazılır.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import ENGINE_VERSION
from .daily_target import (
    FORBIDDEN_ON_REALIZED,
    MIN_KESIN_DAYS,
    TGT_VERSION,
    latest_rows,
    render_brief,
    render_table,
)
from .ledgers import book_name, iso, parse_ts, utc_now
from .paths import EnginePaths

SUMMARY_SCHEMA = "engine_summary_v1"
DIGEST_MAX_BYTES = 8 * 1024
SUMMARY_MAX_BYTES = 256 * 1024
STATUS_MAX_LINES = 60
BANNER = "PAPER · tanımlayıcı; karar yok; kanıt değil"
TARGET_NOTE = ("Günlük +%1 hedefi ÖLÇÜLÜR, iddia edilmez: gün satırı ≥ 3 gün ve fonlama kapsaması tamamlanana kadar "
               "GEÇİCİ; hükümler yalnız KESİN günlerle ve aylık kayıtlı bakışta.")
SECTION_TARGET = f"GÜNLÜK HEDEF ({TGT_VERSION} — ölçülür, iddia edilmez; hüküm yalnız KESİN günler ve aylık bakışta)"


# ============================================================================ biçim yardımcıları
def _f(v: float | None, nd: int = 1) -> str:
    return "—" if v is None else f"{v:.{nd}f}".replace(".", ",")


def _hm(ts: str | None) -> str:
    t = parse_ts(ts)
    return t.strftime("%m-%d %H:%M") if t else "?"


def _sha7(s: str | None) -> str:
    return s[:7] if s else "?"


def _mb(b: int | float | None) -> str:
    return "—" if b is None else f"{b / 1_048_576:.0f} MB"


def _gb(b: int | float | None) -> str:
    return "—" if b is None else _f(b / 1e9, 2) + " GB"


def _read_json(p: Path) -> dict | None:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ============================================================================ okuma (salt-okunur)
def last_status(paths: EnginePaths) -> dict | None:
    """Son çalıştırmanın kaydı (`summary/run_status.json`): S7 bunu çalıştırma ortasında ara kayıt olarak yazar,
    bitişte kesin sonuçla yeniden yazılır. `result == "RUNNING"` kalmışsa çalıştırma S7'den sonra (S7b sırasında)
    sert durmayla öldürülmüştür. Kilit atlaması (`SKIPPED_LOCKED`) buraya yazılmaz (`latest_attempt` gösterir)."""
    return _read_json(paths.summary / "run_status.json")


def latest_attempt(paths: EnginePaths) -> dict | None:
    """`runs/` altındaki en yeni deneme (RUNNING / SKIPPED_LOCKED olabilir)."""
    if not paths.runs.is_dir():
        return None
    dirs = sorted((p for p in paths.runs.iterdir() if p.is_dir()), key=lambda p: p.name)
    for p in reversed(dirs):
        d = _read_json(p / "run_status.json")
        if d is not None:
            return d
    return None


def recent_attempts(paths: EnginePaths, n: int = 10) -> list[dict]:
    if not paths.runs.is_dir():
        return []
    out = []
    for p in sorted((p for p in paths.runs.iterdir() if p.is_dir()), key=lambda p: p.name)[-n:]:
        d = _read_json(p / "run_status.json")
        if d is not None:
            out.append(d)
    return out


def last_with_stage(paths: EnginePaths, stage: str, current: dict | None = None) -> dict | None:
    """`stage` aşaması OK bitmiş en yeni çalıştırma (önce `current`); A/B KAPALI gecelerde arşiv/yedek bilgisi
    kaybolmasın diye durum ve özet bunu kullanır."""
    def _ok(d: dict | None) -> bool:
        s = ((d or {}).get("stages") or {}).get(stage) or {}
        return s.get("status") == "OK" and bool(s.get("result"))
    if _ok(current):
        return current
    if not paths.runs.is_dir():
        return None
    for p in sorted((p for p in paths.runs.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        d = _read_json(p / "run_status.json")
        if _ok(d):
            return d
    return None


def target_summary(paths: EnginePaths) -> dict | None:
    return _read_json(paths.summary / "daily_target.json")


# ============================================================================ satırlar
def _stage_line(st: dict) -> str:
    parts = []
    for name in ("S0", "S1a", "S3", "S7", "S7b"):
        s = (st.get("stages") or {}).get(name)
        if not s:
            continue
        stt = s.get("status", "?")
        if stt == "NOT_PLANNED":
            parts.append(f"{name} —")
            continue
        dur = s.get("duration_s")
        parts.append(f"{name} {stt}" + (f" {_f(dur, 1)}s" if dur is not None else ""))
    return "  aşamalar: " + " · ".join(parts) if parts else "  aşamalar: —"


def _selfcheck_line(st: dict) -> str:
    sc = st.get("selfcheck") or {}
    paper = sc.get("paper") or {}
    iso_r = sc.get("isolation") or {}
    ch = iso_r.get("checks") or {}
    if paper.get("status") and paper.get("status") != "OK":
        return f"Öz-denetim: NOT_PAPER — {paper.get('error') or paper.get('source')}"
    if not iso_r:
        return "Öz-denetim: yapılmadı (" + str(st.get("result")) + ")"
    if iso_r.get("status") != "OK":
        return "Öz-denetim: ISOLATION_BROKEN — bozuk: " + ", ".join(iso_r.get("broken") or ["?"])
    wd = "/".join(str((ch.get(f"write_denied_{k}") or {}).get("errno") or (ch.get(f"write_denied_{k}") or {}).get("status"))
                  for k in ("state", "market", "app"))
    sock = (ch.get("socket_denied") or {}).get("errno") or "?"
    mm = ch.get("memory_max") or {}
    return (f"Öz-denetim: OK — state/market/app yazma reddi {wd} · soket reddi {sock} · memory.max "
            f"{mm.get('actual')} = beklenen · /tmp ve research yazılabilir · mod PAPER")


def _skew_line(st: dict) -> str:
    sk = (st.get("selfcheck") or {}).get("skew") or {}
    if not sk:
        return "SKEW: denetlenmedi"
    rel = sk.get("relation")
    if sk.get("status") == "OK":
        return f"SKEW: yok — app {_sha7(sk.get('app_sha'))} ⊑ engine {_sha7(sk.get('engine_sha'))} ({rel})"
    return (f"SKEW: VAR — app {_sha7(sk.get('app_sha'))} / engine {_sha7(sk.get('engine_sha'))} ({rel}; "
            f"{sk.get('reason') or ''}) → yalnız S0/S1a/S7; engine-app yeniden sabitlenmeli")


def _ab_line(st: dict) -> str:
    ab = (st.get("selfcheck") or {}).get("ab") or {}
    if not ab:
        return "A/B: —"
    w = ab.get("window") or [None, None]
    win = f" (pencere {str(w[0])[5:]} → {str(w[1])[5:]})" if w[0] else ""
    names = {"AB_ON": "AÇIK gece", "AB_OFF": "KAPALI gece (S0 sonrası çıkış)",
             "AB_OFF_ZORUNLU_ARŞİV": "KAPALI gece, ZORUNLU ARŞİV (rotasyon payı < 3 gün; karşılaştırma dışı)",
             "AB_OUTSIDE": "pencere dışı", "AB_RELEASE_DAY": "sürüm günü (A/B dışı, tam çalışma)"}
    night = f" {ab.get('night')}/14" if ab.get("night") else ""
    return f"A/B: {names.get(ab.get('status'), ab.get('status'))}{night}{win}"


def _backup_unit_line(st: dict) -> str:
    bk = (st.get("selfcheck") or {}).get("backup_unit") or {}
    if not bk:
        return "Yedek birimi: —"
    s = bk.get("status")
    if s == "IDLE":
        return "Yedek birimi (tradingbot-backup): çakışma yok"
    if s == "WAITED":
        return f"Yedek birimi: {bk.get('waited_s')} sn beklendi, sonra devam"
    if s == "BACKUP_OVERLAP":
        return f"Yedek birimi: BACKUP_OVERLAP — {bk.get('waited_s')} sn beklendi, hâlâ çalışıyordu; devam edildi"
    return "Yedek birimi: durum bilinmiyor (systemctl cevap vermedi; beklemeden devam)"


def _config_line(st: dict) -> str:
    c = st.get("config") or {}
    if not c:
        return "Config: —"
    ch = "DEĞİŞTİ (önceki " + str(c.get("prev_sha256") or "")[:12] + ")" if c.get("changed") else "değişmedi"
    ok = "" if c.get("ok") else f" · OKUNAMADI: {c.get('error')}"
    return (f"Config (ham, salt-okunur): sha {str(c.get('sha256') or '?')[:12]} ({ch}) · "
            f"extra_entries={c.get('extra_entries')}{ok}")


def _resource_line(st: dict) -> str:
    r = st.get("resources") or {}
    d = (st.get("selfcheck") or {}).get("disk") or {}
    ratio = r.get("memory_peak_ratio")
    mem = (f"tepe bellek {_mb(r.get('memory_peak_bytes'))} / {_mb(r.get('memory_max_bytes'))} "
           f"(%{_f((ratio or 0) * 100, 0)}; kabul ≤ %80)"
           if r.get("memory_peak_bytes") is not None else f"tepe RSS {_mb(r.get('maxrss_bytes'))} (cgroup yok)")
    cpu = (r.get("cpu_self_s") or 0) + (r.get("cpu_children_s") or 0)
    return (f"Kaynak: CPU {_f(cpu, 1)} sn · {mem} · disk: araştırma kökü {_gb(d.get('research_bytes'))}, boş "
            f"{_gb(d.get('free_bytes'))} ({d.get('status', '?')})")


def _flags_line(st: dict) -> str:
    fl = st.get("flags") or []
    return "Bayraklar: " + (", ".join(fl) if fl else "—")


def _books_lines(st: dict | None, *, detail: bool = False, current_id: str | None = None) -> list[str]:
    s1 = (((st or {}).get("stages") or {}).get("S1a") or {}).get("result") or {}
    books = s1.get("books") or {}
    if not books:
        return ["Arşiv: henüz S1a çalışmadı"]
    src = "" if (st or {}).get("run_id") == current_id else f" — son S1a: {(st or {}).get('run_id')}"
    out = [f"Arşiv ve rotasyon payı (gün; < 3 UYARI){src}:",
           f"  {'defter':<30}{'kapanış':>8}{'hareket':>8}  {'uzlaştırma k/c':<22}pay history / entries"]
    for b, x in books.items():
        if x.get("status") != "OK":
            out.append(f"  {book_name(b)[:29]:<30}{'—':>8}{'—':>8}  {x.get('status', '?'):<22}—")
            continue
        rd = x.get("rotation_days") or {}
        warn = " UYARI" if x.get("rotation_warn") else ""
        rec = f"{x.get('recon_record') or '—'}/{x.get('recon_wallet') or '—'}"
        out.append(f"  {book_name(b)[:29]:<30}{'+' + str(x.get('new_closes', 0)):>8}{'+' + str(x.get('new_entries', 0)):>8}  "
                   f"{rec[:21]:<22}{_f(rd.get('history'))} / {_f(rd.get('entries'))}{warn}")
        if detail and (x.get("flags") or x.get("restored_away") or x.get("vanished")):
            out.append(f"      bayrak {', '.join(x.get('flags') or []) or '—'} · RESTORED_AWAY {x.get('restored_away', 0)} · "
                       f"kaybolan {x.get('vanished', 0)} · hizalama {x.get('align')}")
    return out


def _backup_line(st: dict | None, last_ok: dict | None) -> str:
    b = (((st or {}).get("stages") or {}).get("S7b") or {})
    if b.get("status") not in (None, "OK", "NOT_PLANNED"):
        return f"Araştırma yedeği: {b.get('status')} {b.get('error') or b.get('reason') or ''}".rstrip()
    r = (((last_ok or {}).get("stages") or {}).get("S7b") or {}).get("result") or {}
    if not r:
        return "Araştırma yedeği: henüz yok"
    return (f"Araştırma yedeği: {r.get('file')} · {'doğrulandı' if r.get('verified') else 'DOĞRULANAMADI'} · "
            f"{r.get('members')} dosya · {_mb(r.get('bytes'))} · sha256 {str(r.get('sha256'))[:12]}…")


def _run_header(st: dict | None, attempt: dict | None) -> list[str]:
    if not st:
        lines = ["Son çalıştırma: henüz yok"]
    else:
        dur = None
        t0, t1 = parse_ts(st.get("started_at")), parse_ts(st.get("finished_at"))
        if t0 and t1:
            dur = (t1 - t0).total_seconds()
        wall = (st.get("resources") or {}).get("wall_s")
        lines = [f"Son çalıştırma: {st.get('run_id')} · {st.get('result')} (çıkış {st.get('exit_code')}) · "
                 f"{_hm(st.get('started_at'))} → {_hm(st.get('finished_at'))} UTC · "
                 f"{_f(wall if wall is not None else dur, 1)} sn · plan {' '.join(st.get('plan') or [])}",
                 _stage_line(st)]
    if attempt and st and attempt.get("run_id") != st.get("run_id"):
        lines.append(f"  en yeni deneme: {attempt.get('run_id')} · {attempt.get('result')}"
                     + (f" ({((attempt.get('stages') or {}).get('S0') or {}).get('reason')})"
                        if attempt.get("result") == "SKIPPED_LOCKED" else ""))
    elif attempt and not st:
        lines.append(f"  en yeni deneme: {attempt.get('run_id')} · {attempt.get('result')}")
    return lines


def _target_lines(paths: EnginePaths, *, days: int, today: str | None) -> list[str]:
    ts = target_summary(paths) or {}
    latest = latest_rows(paths)
    head = ts.get("header_tr")
    if not head:
        head = render_brief(latest, ts.get("last_look"), ts.get("rolling"), today=today)
    lines = [SECTION_TARGET]
    if ts.get("computed_at"):
        lines.append(f"  (son S3: {_hm(ts.get('computed_at'))} UTC)")
    lines += ["  " + h for h in head]
    if latest:
        lines += ["  " + t for t in render_table(latest, days=days)]
    return lines


def status_lines(paths: EnginePaths, *, brief: bool = True, now: datetime | None = None) -> list[str]:
    """`engine-status [--brief]` metni (salt-okunur). `--brief` ≤ 60 satır."""
    now = now or utc_now()
    st = last_status(paths)
    att = latest_attempt(paths)
    today = now.strftime("%Y-%m-%d")
    top = [f"GECE ÖĞRENME MOTORU ({ENGINE_VERSION}) — {BANNER}"] + _run_header(st, att)
    mid: list[str] = []
    if st:
        mid = [_selfcheck_line(st), _skew_line(st), _ab_line(st), _backup_unit_line(st), _config_line(st),
               _resource_line(st), _flags_line(st)]
        books = _books_lines(last_with_stage(paths, "S1a", st), detail=not brief, current_id=st.get("run_id"))
        bline = [_backup_line(st, last_with_stage(paths, "S7b", st))]
    else:
        books, bline = [], []
    days = 7 if brief else 14
    tgt = _target_lines(paths, days=days, today=today)
    lines = top + mid + books + bline + tgt
    if brief and len(lines) > STATUS_MAX_LINES:
        tgt = _target_lines(paths, days=3, today=today)
        lines = top + mid + books + bline + tgt
    if brief and len(lines) > STATUS_MAX_LINES:
        cut = len(lines) - (STATUS_MAX_LINES - 1)
        lines = lines[:STATUS_MAX_LINES - 1] + [f"… ({cut} satır kısaltıldı; ayrıntı: engine-status)"]
    if not brief:
        lines += _detail_lines(paths, st)
    return lines


def _detail_lines(paths: EnginePaths, st: dict | None) -> list[str]:
    out = ["", "AYRINTI"]
    if st:
        for name, s in (st.get("stages") or {}).items():
            if s.get("status") not in ("OK", "NOT_PLANNED"):
                out.append(f"  {name}: {s.get('status')} {s.get('reason') or s.get('error') or ''}".rstrip())
        sc = st.get("selfcheck") or {}
        for k, v in sorted(((sc.get("isolation") or {}).get("checks") or {}).items()):
            out.append(f"  öz-denetim {k}: {v.get('status')} {v.get('errno') or ''}".rstrip())
        out.append(f"  iç son tarih {st.get('deadline')} · data_seal {st.get('data_seal')} (P1b'den)")
    out.append("  son çalıştırmalar:")
    for a in recent_attempts(paths, 10)[::-1]:
        out.append(f"    {a.get('run_id')} {a.get('result')} (çıkış {a.get('exit_code')})")
    out.append("  " + TARGET_NOTE)
    return out


# ============================================================================ digest_tr.md (≤ 8 KB)
def digest_text(paths: EnginePaths, st: dict, *, now: datetime | None = None) -> str:
    now = now or utc_now()
    today = now.strftime("%Y-%m-%d")
    ts = target_summary(paths) or {}
    latest = latest_rows(paths)
    head = ts.get("header_tr") or render_brief(latest, ts.get("last_look"), ts.get("rolling"), today=today)
    sections: list[tuple[str, list[str]]] = []
    sections.append(("", [f"# Gece öğrenme motoru — özet {today}", "",
                          f"{BANNER} · {ENGINE_VERSION} · çalıştırma {st.get('run_id')}", ""]))
    sections.append(("tgt", ["## Günlük hedef (" + TGT_VERSION + ")", "", *head, ""]))
    if latest:
        sections.append(("tbl", ["## Son 7 gün", "", "```", *render_table(latest, days=7), "```", ""]))
    if st.get("result") == "RUNNING":
        first = (f"{st.get('run_id')} · S7 anı (sonuç ve S7b yedeği bu özetten sonra; kesin sonuç: engine-status) · "
                 f"başlangıç {_hm(st.get('started_at'))} UTC · plan {' '.join(st.get('plan') or [])}")
    else:
        first = _run_header(st, None)[0].replace("Son çalıştırma: ", "")
    run = ["## Çalıştırma", "", "- " + first, "- " + _stage_line(st).strip(),
           "- " + _selfcheck_line(st), "- " + _skew_line(st), "- " + _ab_line(st), "- " + _backup_unit_line(st),
           "- " + _config_line(st), "- " + _resource_line(st), "- " + _flags_line(st), ""]
    sections.append(("run", run))
    books = _books_lines(last_with_stage(paths, "S1a", st), current_id=st.get("run_id"))
    sections.append(("arc", ["## Arşiv", "", "```", *books, "```", "", _backup_line(None, last_with_stage(paths, "S7b", st)),
                             ""]))
    sections.append(("note", ["## Not", "", TARGET_NOTE,
                              f"Hüküm için en az {MIN_KESIN_DAYS} KESİN gün gerekir; ara görünüm hüküm değildir.", ""]))
    text = "\n".join(line for _, ls in sections for line in ls)
    if len(text.encode("utf-8")) <= DIGEST_MAX_BYTES:
        return text
    # sınır: önce arşiv tablosu, sonra 7 günlük tablo kısalır; en sonda sert kesim
    for drop in ("arc", "tbl"):
        sections = [(k, ls if k != drop else ls[:1] + ["", "(boyut sınırı: kısaltıldı — engine-status)", ""])
                    for k, ls in sections]
        text = "\n".join(line for _, ls in sections for line in ls)
        if len(text.encode("utf-8")) <= DIGEST_MAX_BYTES:
            return text
    raw = text.encode("utf-8")[:DIGEST_MAX_BYTES - 64]
    return raw.decode("utf-8", errors="ignore").rsplit("\n", 1)[0] + "\n… (8 KB sınırı: kısaltıldı)\n"


# ============================================================================ engine_summary.json (≤ 256 KB)
def summary_doc(paths: EnginePaths, st: dict, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    ts = target_summary(paths) or {}
    keep = ("run_id", "started_at", "finished_at", "result", "exit_code", "plan", "flags", "shas", "config", "data_seal",
            "resources", "deadline")
    run = {k: st.get(k) for k in keep}
    sc = st.get("selfcheck") or {}
    run["selfcheck"] = {"status": sc.get("status"), "paper": (sc.get("paper") or {}).get("status"),
                        "isolation": (sc.get("isolation") or {}).get("status"),
                        "isolation_broken": (sc.get("isolation") or {}).get("broken"),
                        "skew": (sc.get("skew") or {}).get("status"), "ab": sc.get("ab"),
                        "backup_unit": (sc.get("backup_unit") or {}).get("status"),
                        "disk": {k: (sc.get("disk") or {}).get(k) for k in ("status", "research_bytes", "free_bytes")}}
    run["stages"] = {k: {kk: v.get(kk) for kk in ("status", "duration_s", "cpu_s", "reason", "error")}
                     for k, v in (st.get("stages") or {}).items()}
    s1 = ((st.get("stages") or {}).get("S1a") or {}).get("result")
    return {"schema": SUMMARY_SCHEMA, "engine": ENGINE_VERSION, "generated_at": iso(now), "banner": BANNER,
            "run": run, "archive": s1,
            "daily_target": {"tgt": TGT_VERSION, "computed_at": ts.get("computed_at"), "header_tr": ts.get("header_tr"),
                             "rows": ts.get("rows") or [], "rolling": ts.get("rolling"), "last_look": ts.get("last_look")},
            "backup": ((st.get("stages") or {}).get("S7b") or {}).get("result")}


def _bounded_json(doc: dict[str, Any]) -> bytes:
    raw = (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    rows = list((doc.get("daily_target") or {}).get("rows") or [])
    while len(raw) > SUMMARY_MAX_BYTES and rows:
        rows = rows[1:]
        doc["daily_target"]["rows"] = rows
        doc["daily_target"]["rows_truncated"] = True
        raw = (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if len(raw) > SUMMARY_MAX_BYTES:
        doc["archive"] = {"truncated": True}
        raw = (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return raw


def write_summary(paths: EnginePaths, st: dict, *, now: datetime | None = None) -> dict[str, Any]:
    """S7: `digest_tr.md`, `engine_summary.json` ve (geçici) `summary/run_status.json`. Dönen: boyutlar."""
    now = now or utc_now()
    digest = digest_text(paths, st, now=now)
    if claim_word_violations(digest.splitlines()):
        raise ValueError("özet metninde hüküm kelimesi yasak bir satırda (P1a kabul 5)")
    paths.write_text(paths.summary / "digest_tr.md", digest)
    raw = _bounded_json(summary_doc(paths, st, now=now))
    paths.write_bytes(paths.summary / "engine_summary.json", raw)
    paths.write_json(paths.summary / "run_status.json", st)
    return {"digest_bytes": len(digest.encode("utf-8")), "summary_bytes": len(raw),
            "digest_limit": DIGEST_MAX_BYTES, "summary_limit": SUMMARY_MAX_BYTES}


def claim_word_violations(lines: list[str]) -> list[str]:
    """Metin testi yardımcısı (P1a kabul 5): yasak kelime taşıyan ama KESİN MTM "Son kesin gün" satırı OLMAYAN satırlar."""
    bad = []
    for ln in lines:
        if "TUTTU" in ln:
            bad.append(ln)
            continue
        if any(w in ln for w in FORBIDDEN_ON_REALIZED) and not (ln.strip().startswith("Son kesin gün") and "MTM" in ln):
            bad.append(ln)
    return bad


__all__ = ["BANNER", "DIGEST_MAX_BYTES", "STATUS_MAX_LINES", "SUMMARY_MAX_BYTES", "SUMMARY_SCHEMA", "claim_word_violations",
           "digest_text", "last_status", "last_with_stage", "latest_attempt", "recent_attempts", "status_lines", "summary_doc",
           "write_summary"]

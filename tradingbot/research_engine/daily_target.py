"""S3 — günlük ve aylık hedef ölçümü `tgt_v1` (§7; mühürlü tanımlar). Hedef ÖLÇÜLÜR, iddia edilmez.

Akışlar asla toplanmaz: bu modül yalnız `LIVE_PAPER` akışını üretir (mevcut defterler; ana bot = `main_fut` +
`main_spot`). `PROSPECTIVE_REPLAY`, `EXPLORATION_FWD`, `BACKTEST` satırları (P3/P4) aynı hüküm fonksiyonuna "canlı
değil" olarak girer ve ASLA `KANITLANDI` üretemez (`look_verdicts`).

Gün ve pencere (§7.1): `S(D)`, D gününün gece çalıştırmasında alınan ölçülmüş anlık görüntüdür (`closes.run_s1a`).
P1a'nın MTM günü `W(D) = (S(D), S(D+1)]`'dir; satır iki anlık görüntü zamanını yazar. Ardışık iki takvim gününün anlık
görüntüsü yoksa W(D) yoktur ve gün `EKSİK`tir (sıfır sayılmaz).

Görünümler (defter, ana bot grubu, toplam, enstrüman, altın toplamı):

* **Kayıt görünümü** `pnl_rec(D)`: UTC günü D'de (`closed_at`) kapanan kayıtların SON revizyonlu Σ `TradeRecord.pnl`'i
  (vadeli + spot). Geç fonlama `closed_at` gününe yazılır ve o günü REVİZE eder. Açıklama içindir; hüküm vermez.
* **Cüzdan görünümü** `pnl_wal(W)`: W içinde GÖZLENEN (`observed_at`) PNL + FEE + FUNDING + LIQ_FEE + TAX
  hareketleri. TRANSFER P&L değildir (vadeli: özsermaye düzeltmesi; spot: alış dönüşümü, bkz. `closes` okuma 4).
* **Gerçekleşmemiş** `U(S)` = `LEDGER_MARK` (vadeli: ledger'ın kendi `last_price`'ı; spot: mark zinciri). Hiçbir mark
  yoksa o defterin MTM'i `EKSİK`.
* **MTM** `pnl_mtm(W) = pnl_wal(W) + ΔU` = `E(S(D+1)) − E(S(D)) − TRANSFER(W)` (vadeli); spotta dönüşüm E'yi
  değiştirmediği için `E(S(D+1)) − E(S(D))`. Eşitlik her satırda 1e-6 ile denetlenir; tutmazsa gün `EKSİK`.
* Oranlar yüzde: `r_total = Σ pnl_mtm / Σ E_book(S(D))`; `r_inst` paydası `E_total` ("toplam sermayeye katkı"; hüküm
  paydası, §12 soru 12 cevaplanana kadar); `r_book_inst` paydası `E_book` ("kendi defterinin sermayesine göre"), her
  zaman yanında etiketiyle.

Kesinleşme (§4.2): satır en az 3 gün (çalıştırma günü ≥ D+3) VE o gün kapanan vadeli kayıtların fonlama kapsaması
tamamlanana kadar `GEÇİCİ`; 7 gün sonra kapsama hâlâ eksikse `EKSİK (fonlama)`. Veri eksiği (anlık görüntü yok,
`ENTRIES_GAP`, mark yok, uzlaştırma tutmadı, okunamayan defter) `EKSİK`; geri yükleme penceresi `EKSİK (RESTORE)`.
Revizyonlar `target/daily.jsonl`'a yalnız EKLENİR (`rev`); sayılar ilk yayımdan farklıysa satır görünür `REVİZE` taşır.
**Hükümler yalnız KESİN günleri kullanır.**

Hükümler (§7.3): yalnız aylık kayıtlı bakış gecesinde (her ayın 3. UTC gününden sonraki ilk gece), son 60 takvim
gününün KESİN günleriyle; `p_tgt` = H0 "ortalama günlük % ≤ %1,00" için 5 günlük blok-bootstrap tek yönlü p; hükme
uygun BÜTÜN kapsamlarda Holm; `α_bakış = 0,05/12`. Bootstrap çekim sayısı 20 000'dir: en küçük ulaşılabilir p
(1/20 001), ~50 kapsamlı Holm ailesinde `α_bakış`ın altında kalabilsin diye (5 000 çekimle `KANITLANDI` yapı gereği
imkânsız olurdu). Bakışlar arasında yalnız "ara görünüm" yazılır; ara görünümde hüküm kelimesi kullanılmaz.

Dürüstlük (P1a kabul 5): yalnız-gerçekleşmiş bir satırda `TUTTU` veya `HEDEF GÜNÜ` kelimesi HİÇ geçmez; `HEDEF GÜNÜ`
yalnız KESİN bir MTM satırının "Son kesin gün" satırında görünür ve "asla başarı denmez". `TUTTU` hiçbir yerde yazılmaz.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from bisect import bisect_left
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from .closes import (
    ENTRIES_KEEP,
    PNL_KINDS,
    RECON_GAP,
    RECON_INCONSISTENT,
    RECON_RESTORED,
    ST_RESTORED_AWAY,
    iter_rows,
    latest_closes,
    load_snapshots,
)
from .ledgers import (
    KIND_SPOT,
    add_days,
    book_group,
    book_name,
    day_of,
    day_start,
    dec,
    dec_or_none,
    is_gold,
    iso,
    parse_day,
    parse_ts,
    symbol_key,
    utc_now,
)
from .paths import EnginePaths, read_jsonl

TGT_VERSION = "tgt_v1"
ROW_SCHEMA = "tgt_v1_day"
LOOK_SCHEMA = "tgt_v1_look"
STREAM_LIVE = "LIVE_PAPER"
STREAMS = (STREAM_LIVE, "PROSPECTIVE_REPLAY", "EXPLORATION_FWD", "BACKTEST")

TARGET_PCT = 1.0
FINAL_AFTER_DAYS = 3
FUNDING_EKSIK_AFTER_DAYS = 7
LOOK_AFTER_UTC_DAY = 3
LOOK_WINDOW_DAYS = 60
MIN_KESIN_DAYS = 40
MIN_TRADES = 30
MAX_DAY_SHARE = 0.25
ALPHA_YEAR = 0.05
ALPHA_LOOK = ALPHA_YEAR / 12
BLOCK_LEN = 5
BOOT_P = 20_000
BOOT_CI = 2_000
CHANCE_DRAWS = 1000
BACKFILL_DAYS = 30
ROLLING_WINDOWS = (7, 30, 60, 90)
TOL = Decimal("1e-6")
D0 = Decimal(0)

GECICI, KESIN, EKSIK, REVIZE = "GEÇİCİ", "KESİN", "EKSİK", "REVİZE"
_STATUS_RANK = {KESIN: 0, GECICI: 1, EKSIK: 2}
V_DAY = "HEDEF GÜNÜ"
V_PROVEN = "HEDEF KANITLANDI (PAPER)"
V_ONTRACK = "HEDEF YOLUNDA (umut verici)"
V_BELOW = "HEDEFİN ALTINDA"
V_THIN = "VERİ YETERSİZ"
#: Yalnız-gerçekleşmiş satırlarda asla geçmeyecek kelimeler (metin testi).
FORBIDDEN_ON_REALIZED = ("TUTTU", "HEDEF GÜNÜ")
CHANCE_HEADLINE = "işaret çevirme"
CHANCE_SECONDARY = "brüt işaret çevirme − gerçek maliyet"
DENOM_TOTAL = "toplam sermaye"
DENOM_BOOK = "kendi defterine göre"
SCOPE_TOTAL, SCOPE_GOLD = "total", "gold"


# ============================================================================ küçük yardımcılar
def _fl(x: Decimal | None, nd: int = 8) -> float | None:
    return None if x is None else round(float(x), nd)


def _pct(num: Decimal | None, den: Decimal | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return round(float(num / den * 100), 6)


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 2)) if statuses else KESIN


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12], 16)


def _new_wagg() -> dict[str, Any]:
    return {"pnl": D0, "transfer": D0, "other": D0, "n": 0, "by_kind": {}, "by_inst": {}, "n_by_inst": {}}


def _add_entry(agg: dict[str, Any], e: dict, inst: str) -> None:
    k, amt = str(e.get("kind", "")), dec(e.get("amount"))
    agg["n"] += 1
    agg["by_kind"][k] = agg["by_kind"].get(k, D0) + amt
    if k in PNL_KINDS:
        agg["pnl"] += amt
        agg["by_inst"][inst] = agg["by_inst"].get(inst, D0) + amt
        agg["n_by_inst"][inst] = agg["n_by_inst"].get(inst, 0) + 1
    elif k == "TRANSFER":
        agg["transfer"] += amt
    else:
        agg["other"] += amt


def _new_ragg() -> dict[str, Any]:
    return {"n": 0, "pnl": D0, "gross": D0, "fees": D0, "funding": D0, "slippage": D0, "sum_r": D0, "n_r": 0,
            "n_spot_no_r": 0, "n_r_missing": 0, "funding_pending": 0, "revised_records": 0, "restored_away_n": 0,
            "restored_away_pnl": D0, "by_inst": {}, "trades": []}


# ============================================================================ arşiv taramaları
def scan_closes(paths: EnginePaths, book: str) -> dict[str, dict[str, Any]]:
    """Kayıt görünümü: UTC kapanış günü → toplamlar (son revizyon; RESTORED_AWAY ayrı sayılır)."""
    out: dict[str, dict[str, Any]] = {}
    for _k, st in latest_closes(paths, book).items():
        p = st.proj or {}
        t = parse_ts(p.get("closed_at"))
        if t is None:
            continue
        agg = out.setdefault(day_of(t), _new_ragg())
        pnl = dec(p.get("pnl"))
        if st.status == ST_RESTORED_AWAY:
            agg["restored_away_n"] += 1
            agg["restored_away_pnl"] += pnl
            continue
        agg["n"] += 1
        agg["pnl"] += pnl
        agg["gross"] += dec(p.get("gross"))
        agg["fees"] += dec(p.get("fees"))
        agg["funding"] += dec(p.get("funding"))
        agg["slippage"] += dec(p.get("slippage"))
        if p.get("r") is not None:
            agg["sum_r"] += dec(p.get("r"))
            agg["n_r"] += 1
        elif p.get("venue") == "spot":
            agg["n_spot_no_r"] += 1
        else:
            agg["n_r_missing"] += 1
        if p.get("venue") != "spot" and p.get("funding_complete") is not True:
            agg["funding_pending"] += 1
        if st.rev > 0:
            agg["revised_records"] += 1
        inst = p.get("inst") or symbol_key(p.get("symbol")) or "?"
        bi = agg["by_inst"].setdefault(inst, {"pnl": D0, "n": 0})
        bi["pnl"] += pnl
        bi["n"] += 1
        agg["trades"].append((inst, float(pnl), float(dec(p.get("gross")))))
    return out


def scan_entries(paths: EnginePaths, book: str, times: list[datetime], days: list[str]) -> dict[str, Any]:
    """Cüzdan hareketleri: (a) gözlem penceresine göre (W(D) etiketi = başlangıç anlık görüntüsünün günü; iki anlık
    görüntü ardışık takvim günü değilse etiket yok), (b) defterin kendi zaman damgasının UTC gününe göre."""
    win: dict[str, dict[str, Any]] = {}
    win_gap: set[str] = set()
    walts: dict[str, dict[str, Any]] = {}
    walts_pre: dict[str, dict[str, Any]] = {}
    gap_ts_days: set[str] = set()
    first_ts: datetime | None = None
    n_pre = 0
    for row in iter_rows(paths.book_entries_dir(book)):
        e = row.get("entry") if isinstance(row.get("entry"), dict) else {}
        inst = symbol_key(row.get("symbol")) or "?"
        t = parse_ts(e.get("ts"))
        if t is not None:
            _add_entry(walts.setdefault(day_of(t), _new_wagg()), e, inst)
            first_ts = t if first_ts is None or t < first_ts else first_ts
        if row.get("pre_archive"):
            n_pre += 1
            if t is not None:
                _add_entry(walts_pre.setdefault(day_of(t), _new_wagg()), e, inst)
            continue
        obs = parse_ts(row.get("observed_at"))
        if obs is None:
            continue
        i = bisect_left(times, obs)
        label = None
        if 1 <= i < len(times) and days[i] == add_days(days[i - 1], 1):
            label = days[i - 1]
        if label is not None:
            _add_entry(win.setdefault(label, _new_wagg()), e, inst)
        if row.get("gap_before"):
            if label is not None:
                win_gap.add(label)
            start = day_of(times[i - 1]) if i >= 1 else day_of(obs)
            d = add_days(start, -1)
            while d <= day_of(obs):
                gap_ts_days.add(d)
                d = add_days(d, 1)
    return {"win": win, "win_gap": win_gap, "walts": walts, "walts_pre": walts_pre, "gap_ts_days": gap_ts_days, "first_ts": first_ts,
            "rotated_at_baseline": n_pre >= ENTRIES_KEEP}


def _walts_complete(scan: dict[str, Any], day: str) -> bool:
    """Defter-zaman-damgalı cüzdan günü tam mı: arşivin başlangıcında ledger dolu idiyse (rotasyon olmuş olabilir)
    gün, arşivin en eski hareketinden SONRA başlamalı; boşluk günleri tam değildir."""
    if day in scan["gap_ts_days"]:
        return False
    if scan["rotated_at_baseline"] and scan["first_ts"] is not None and not (scan["first_ts"] < day_start(day)):
        return False
    return True


# ============================================================================ satır kurma
def _scope_empty() -> dict[str, Any]:
    return {"pnl_rec": D0, "n_trades": 0, "fees": D0, "funding": D0, "slippage": D0, "sum_r": D0, "n_r": 0,
            "n_spot_no_r": 0, "n_r_missing": 0, "restored_away_n": 0, "restored_away_pnl": D0, "pnl_wal": D0, "transfer": D0,
            "u_start": D0, "u_end": D0, "pnl_mtm": D0, "e_start": D0, "mtm_ok": True, "open_positions_end": 0}


def _scope_add(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if k in ("mtm_ok",):
            dst[k] = dst[k] and v
        elif isinstance(v, (Decimal, int)) and not isinstance(v, bool) and k in dst:
            dst[k] = dst[k] + v


def _scope_out(s: dict[str, Any], *, status: str, reasons: list[str], e_den: Decimal | None = None) -> dict[str, Any]:
    mtm = s["pnl_mtm"] if s["mtm_ok"] else None
    e = s["e_start"] if s["mtm_ok"] else None
    return {"status": status, "status_reason": sorted(set(reasons)),
            "pnl_rec": _fl(s["pnl_rec"]), "n_trades": s["n_trades"], "fees": _fl(s["fees"]), "funding": _fl(s["funding"]),
            "slippage": _fl(s["slippage"]), "sum_r": _fl(s["sum_r"], 6), "n_r": s["n_r"], "n_spot_no_r": s["n_spot_no_r"],
            "n_r_missing": s["n_r_missing"],
            "restored_away": {"n": s["restored_away_n"], "pnl": _fl(s["restored_away_pnl"])},
            "pnl_wal": _fl(s["pnl_wal"]), "transfer": _fl(s["transfer"]),
            "u_start": _fl(s["u_start"]) if s["mtm_ok"] else None, "u_end": _fl(s["u_end"]) if s["mtm_ok"] else None,
            "pnl_mtm": _fl(mtm), "e_start": _fl(e), "open_positions_end": s["open_positions_end"],
            "r_mtm": _pct(mtm, e_den if e_den is not None else e),
            "r_realized": _pct(s["pnl_wal"], e_den if e_den is not None else e),
            "u_end_pct": _pct(s["u_end"], e_den if e_den is not None else e) if s["mtm_ok"] else None}


def chance_rate(trades: list[tuple[str, float, float]], e_total: float | None, *, mode: str, seed: int,
                draws: int = CHANCE_DRAWS) -> float | None:
    """"Bir enstrümanın şansla +%1'e ulaşma olasılığı" (§7.2): o günün işlem sonuçlarının işaretleri rastgele çevrilir
    (`net`: muhafazakâr başlık; `gross`: brüt işaretleri çevrilir, sonra gerçek maliyet düşülür), enstrüman başına
    toplanır, max/E_total ≥ %1 olan çekimlerin payı. Sabit tohum."""
    if not e_total or e_total <= 0:
        return None
    if not trades:
        return 0.0
    rng = random.Random(seed)
    n = len(trades)
    hits = 0
    for _ in range(draws):
        bits = rng.getrandbits(n)
        sums: dict[str, float] = {}
        for j, (inst, net, gross) in enumerate(trades):
            s = 1.0 if (bits >> j) & 1 else -1.0
            v = s * net if mode == "net" else s * gross - (gross - net)
            sums[inst] = sums.get(inst, 0.0) + v
        if max(sums.values()) / e_total * 100.0 >= TARGET_PCT:
            hits += 1
    return round(hits / draws, 4)


def book_times(snaps: list[dict], book: str) -> tuple[list[datetime], list[str]]:
    """Defterin OK anlık görüntülerinin okuma anları ve günleri (artan)."""
    pts = [(parse_ts(s["books"][book].get("read_at")), s["day"]) for s in snaps
           if (s.get("books") or {}).get(book, {}).get("status") == "OK"]
    pts = [(t, d) for t, d in pts if t is not None]
    return [t for t, _ in pts], [d for _, d in pts]


def build_rows(paths: EnginePaths, *, now: datetime | None = None, backfill_days: int = BACKFILL_DAYS,
               prev_rows: dict[str, dict] | None = None) -> list[dict]:
    """Bütün günlerin `tgt_v1` satırlarını arşiv ve anlık görüntülerden yeniden hesapla (saf okuma)."""
    now = now or utc_now()
    snaps = load_snapshots(paths)
    if not snaps:
        return []
    run_day = day_of(now)
    snap_by_day = {s["day"]: s for s in snaps}
    books = sorted({b for s in snaps for b in (s.get("books") or {})})
    scans_e: dict[str, dict] = {}
    scans_r: dict[str, dict] = {}
    for b in books:
        times, tdays = book_times(snaps, b)
        scans_e[b] = scan_entries(paths, b, times, tdays)
        scans_r[b] = scan_closes(paths, b)
    first, last = snaps[0]["day"], snaps[-1]["day"]
    rows = []
    d = add_days(first, -backfill_days)
    while d < last:
        if d < first:
            r = _pre_row(d, books, scans_r, scans_e, snap_by_day[first], run_day)
        else:
            r = _window_row(d, books, scans_r, scans_e, snap_by_day, run_day, prev_rows or {})
        if r is not None:
            rows.append(r)
        d = add_days(d, 1)
    return rows


def _book_scope(b: str, day: str, b0: dict, b1: dict, rec: dict, w: dict | None, scan_e: dict, run_day: str
                ) -> tuple[dict[str, Any], str, list[str], dict[str, dict[str, Decimal]]]:
    s = _scope_empty()
    reasons: list[str] = []
    status = KESIN
    s["pnl_rec"], s["n_trades"] = rec["pnl"], rec["n"]
    for k in ("fees", "funding", "slippage", "sum_r", "n_r", "n_spot_no_r", "n_r_missing", "restored_away_n", "restored_away_pnl"):
        s[k] = rec[k]
    w = w or _new_wagg()
    s["pnl_wal"], s["transfer"] = w["pnl"], w["transfer"]
    u0, u1 = dec_or_none(b0.get("unrealized")), dec_or_none(b1.get("unrealized"))
    e0, e1 = dec_or_none(b0.get("equity")), dec_or_none(b1.get("equity"))
    s["open_positions_end"] = len(b1.get("positions") or []) if b1.get("kind") != KIND_SPOT else len(b1.get("held") or {})
    if not (b0.get("mtm_complete") and b1.get("mtm_complete")) or None in (u0, u1, e0, e1):
        s["mtm_ok"] = False
        status, reasons = EKSIK, reasons + ["MARK_EKSİK"]
    else:
        s["u_start"], s["u_end"], s["e_start"] = u0, u1, e0
        s["pnl_mtm"] = w["pnl"] + (u1 - u0)
        # P&L dışı hareketler (vadeli TRANSFER; bilgi türleri) eşitliğin sağından düşülür; spot TRANSFER alış dönüşümüdür
        rhs = (e1 - e0) - (w["transfer"] if b1.get("kind") != KIND_SPOT else D0) - w["other"]
        if abs(rhs - s["pnl_mtm"]) > TOL:
            s["mtm_ok"] = False
            status, reasons = EKSIK, reasons + ["MTM_ESİTLİĞİ_TUTMADI"]
    rc = (b1.get("recon") or {}).get("status")
    if rc == RECON_GAP or day in scan_e["win_gap"]:
        status, reasons = EKSIK, reasons + ["ENTRIES_GAP"]
    if rc == RECON_RESTORED or b1.get("restore"):
        status, reasons = EKSIK, reasons + ["RESTORE"]
    if rc == RECON_INCONSISTENT:
        status, reasons = EKSIK, reasons + ["INCONSISTENT"]
    if rec["funding_pending"]:
        if run_day >= add_days(day, FUNDING_EKSIK_AFTER_DAYS):
            status, reasons = EKSIK, reasons + ["EKSİK_FONLAMA"]
        else:
            status, reasons = _worst(status, GECICI), reasons + ["FONLAMA_BEKLİYOR"]
    if run_day < add_days(day, FINAL_AFTER_DAYS):
        status, reasons = _worst(status, GECICI), reasons + ["3_GÜN_DOLMADI"]
    by_inst: dict[str, dict[str, Decimal]] = {}
    if s["mtm_ok"]:
        ub0, ub1 = b0.get("unrealized_by_inst") or {}, b1.get("unrealized_by_inst") or {}
        insts = set(w["by_inst"]) | set(ub0) | set(ub1)
        for inst in insts:
            mtm_i = w["by_inst"].get(inst, D0) + dec(ub1.get(inst)) - dec(ub0.get(inst))
            by_inst[inst] = {"pnl_mtm": mtm_i, "pnl_wal": w["by_inst"].get(inst, D0), "n_entries": w["n_by_inst"].get(inst, 0)}
    for inst, v in rec["by_inst"].items():
        by_inst.setdefault(inst, {"pnl_mtm": D0 if s["mtm_ok"] else None, "pnl_wal": D0})["pnl_rec"] = v["pnl"]
        by_inst[inst]["n"] = v["n"]
    return s, status, reasons, by_inst


def _window_row(day: str, books: list[str], scans_r: dict, scans_e: dict, snap_by_day: dict, run_day: str,
                prev_rows: dict[str, dict]) -> dict | None:
    s0, s1 = snap_by_day.get(day), snap_by_day.get(add_days(day, 1))
    total = _scope_empty()
    total_status, total_reasons = KESIN, []
    groups: dict[str, dict[str, Any]] = {}
    group_status: dict[str, tuple[str, list[str]]] = {}
    books_out: dict[str, dict[str, Any]] = {}
    inst_acc: dict[str, dict[str, Any]] = {}
    added: list[str] = []
    trades: list[tuple[str, float, float]] = []
    e_books: dict[str, Decimal] = {}
    if s0 is None or s1 is None:
        total_status, total_reasons = EKSIK, ["ANLIK_GÖRÜNTÜ_YOK"]
    for b in books:
        rec = scans_r[b].get(day) or _new_ragg()
        b0 = ((s0 or {}).get("books") or {}).get(b)
        b1 = ((s1 or {}).get("books") or {}).get(b)
        if s0 is None or s1 is None:
            if rec["n"]:
                sc = _scope_empty()
                sc["pnl_rec"], sc["n_trades"], sc["mtm_ok"] = rec["pnl"], rec["n"], False
                books_out[b] = _scope_out(sc, status=EKSIK, reasons=["ANLIK_GÖRÜNTÜ_YOK"])
            continue
        ok0 = isinstance(b0, dict) and b0.get("status") == "OK"
        ok1 = isinstance(b1, dict) and b1.get("status") == "OK"
        if b0 is None and ok1:
            added.append(b)                      # yeni defter: bu pencerenin paydasına girmez
            continue
        if b0 is None and b1 is None:
            continue
        if not (ok0 and ok1):
            sc = _scope_empty()
            sc["mtm_ok"] = False
            why = [f"DEFTER_{(b1 or {}).get('status') or (b0 or {}).get('status') or 'YOK'}"]
            books_out[b] = _scope_out(sc, status=EKSIK, reasons=why)
            total_status, total_reasons = EKSIK, total_reasons + why
            total["mtm_ok"] = False                  # okunamayan defter varken toplam MTM yüzdesi yazılmaz (kısmi olurdu)
            g = book_group(b)
            groups.setdefault(g, _scope_empty())["mtm_ok"] = False
            gs = group_status.get(g, (KESIN, []))
            group_status[g] = (EKSIK, gs[1] + why)
            continue
        sc, st, rs, by_inst = _book_scope(b, day, b0, b1, rec, scans_e[b]["win"].get(day), scans_e[b], run_day)
        books_out[b] = {**_scope_out(sc, status=st, reasons=rs), "kind": b1.get("kind"),
                        "mark_source": b1.get("mark_source"), "e_end": _fl(dec_or_none(b1.get("equity")))}
        if sc["mtm_ok"]:
            e_books[b] = sc["e_start"]
        _scope_add(total, sc)
        total_status, total_reasons = _worst(total_status, st), total_reasons + rs
        g = book_group(b)
        gacc = groups.setdefault(g, _scope_empty())
        _scope_add(gacc, sc)
        gs = group_status.get(g, (KESIN, []))
        group_status[g] = (_worst(gs[0], st), gs[1] + rs)
        trades.extend(rec["trades"])
        for inst, v in by_inst.items():
            a = inst_acc.setdefault(inst, {"pnl_mtm": D0, "mtm_ok": True, "pnl_rec": D0, "n": 0, "pnl_wal": D0, "n_entries": 0,
                                           "by_book": {}})
            if v.get("pnl_mtm") is None:
                a["mtm_ok"] = False
            else:
                a["pnl_mtm"] += v["pnl_mtm"]
                a["by_book"][b] = v["pnl_mtm"]
            a["pnl_wal"] += v.get("pnl_wal") or D0
            a["pnl_rec"] += v.get("pnl_rec") or D0
            a["n"] += v.get("n") or 0
            a["n_entries"] += v.get("n_entries") or 0
    e_total = total["e_start"] if total["mtm_ok"] else None
    row: dict[str, Any] = {"schema": ROW_SCHEMA, "tgt": TGT_VERSION, "stream": STREAM_LIVE, "day": day,
                           "window": ({"from": (s0 or {}).get("taken_at"), "to": (s1 or {}).get("taken_at")}
                                      if s0 and s1 else None),
                           "final_on": add_days(day, FINAL_AFTER_DAYS), "books_added": added}
    if s0 is None or s1 is None:
        sc = _scope_empty()
        sc["mtm_ok"] = False
        for b in books:
            rec = scans_r[b].get(day) or _new_ragg()
            sc["pnl_rec"] += rec["pnl"]
            sc["n_trades"] += rec["n"]
        if not sc["n_trades"]:
            return None
        row.update({"status": EKSIK, "status_reason": ["ANLIK_GÖRÜNTÜ_YOK"], "data_completeness": "MISSING",
                    "total": _scope_out(sc, status=EKSIK, reasons=["ANLIK_GÖRÜNTÜ_YOK"]), "books": books_out, "groups": {},
                    "instruments": {}, "best_instrument": None, "gold": None, "chance": None, "hit": None})
        return row
    total_out = _scope_out(total, status=total_status, reasons=total_reasons)
    row["status"] = total_status
    row["status_reason"] = total_out["status_reason"]
    row["data_completeness"] = "MEASURED" if total["mtm_ok"] else "PARTIAL"
    row["total"] = total_out
    row["books"] = books_out
    row["groups"] = {g: _scope_out(acc, status=group_status[g][0], reasons=group_status[g][1]) for g, acc in groups.items()}
    insts: dict[str, Any] = {}
    gold = {"pnl_mtm": D0, "pnl_rec": D0, "pnl_wal": D0, "n": 0, "mtm_ok": True}
    for inst, a in sorted(inst_acc.items()):
        mtm = a["pnl_mtm"] if a["mtm_ok"] else None
        bi = {}
        for b, v in a["by_book"].items():
            bi[b] = _pct(v, e_books.get(b))
        insts[inst] = {"pnl_mtm": _fl(mtm), "r_inst": _pct(mtm, e_total), "r_book_inst": bi, "pnl_rec": _fl(a["pnl_rec"]),
                       "pnl_wal": _fl(a["pnl_wal"]), "n": a["n"], "n_entries": a["n_entries"], "gold": is_gold(inst)}
        if is_gold(inst):
            gold["n"] += a["n"]
            gold["pnl_rec"] += a["pnl_rec"]
            gold["pnl_wal"] += a["pnl_wal"]
            if mtm is None:
                gold["mtm_ok"] = False
            else:
                gold["pnl_mtm"] += mtm
    row["instruments"] = insts
    row["unknown_instrument"] = insts.get("?")
    row["gold"] = {"pnl_mtm": _fl(gold["pnl_mtm"]) if gold["mtm_ok"] else None,
                   "r_inst": _pct(gold["pnl_mtm"], e_total) if gold["mtm_ok"] else None, "pnl_rec": _fl(gold["pnl_rec"]),
                   "r_realized": _pct(gold["pnl_wal"], e_total),
                   "n": gold["n"], "note": "işlem yok" if not gold["n"] and not gold["pnl_mtm"] else ""}
    cands = [(v["r_inst"], k) for k, v in insts.items() if k != "?" and v["r_inst"] is not None]
    if cands:
        r_best, k_best = max(cands)
        bb = insts[k_best]["r_book_inst"]
        main_book = max(bb, key=lambda b: abs(bb[b] or 0.0)) if bb else None
        row["best_instrument"] = {"inst": k_best, "r_inst": r_best, "denominator": DENOM_TOTAL, "book": main_book,
                                  "r_book_inst": bb.get(main_book) if main_book else None, "book_denominator": DENOM_BOOK}
    else:
        row["best_instrument"] = None
    e_tot_f = float(e_total) if e_total is not None else None
    tsha = hashlib.sha256(json.dumps(sorted(trades), separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    prev = (prev_rows.get(day) or {}).get("chance") or {}
    if prev.get("trades_sha") == tsha and prev.get("e_total") == (round(e_tot_f, 8) if e_tot_f else None):
        row["chance"] = prev
    else:
        row["chance"] = {"trades_sha": tsha, "e_total": round(e_tot_f, 8) if e_tot_f else None, "n_trades": len(trades),
                         "headline": chance_rate(trades, e_tot_f, mode="net", seed=_seed(TGT_VERSION, "chance", day, "net")),
                         "headline_null": CHANCE_HEADLINE,
                         "secondary": chance_rate(trades, e_tot_f, mode="gross", seed=_seed(TGT_VERSION, "chance", day, "gross")),
                         "secondary_null": CHANCE_SECONDARY, "draws": CHANCE_DRAWS}
    if row["status"] == KESIN and total_out["r_mtm"] is not None:
        th = total_out["r_mtm"] >= TARGET_PCT
        sh = bool(row["best_instrument"] and row["best_instrument"]["r_inst"] >= TARGET_PCT)
        row["hit"] = {"total_hit": th, "single_hit": sh, "day_hit": th or sh,
                      "single_inst": row["best_instrument"]["inst"] if sh else None}
    else:
        row["hit"] = None
    return row


def _pre_row(day: str, books: list[str], scans_r: dict, scans_e: dict, first_snap: dict, run_day: str) -> dict | None:
    """P1a'dan önceki gün: ölçülmüş anlık görüntü yoktur. Kayıt görünümü ve defter-zaman-damgalı cüzdan görünümü
    (hareketler arşivde duruyorsa `RECONSTRUCTED`, düşmüşse yok) yazılır; MTM yoktur, gün asla KESİN değildir."""
    any_data = False
    total = _scope_empty()
    total["mtm_ok"] = False
    complete = True
    den = D0
    books_out = {}
    for b in books:
        rec = scans_r[b].get(day) or _new_ragg()
        se = scans_e[b]
        w = se["walts"].get(day)
        fb = (first_snap.get("books") or {}).get(b) or {}
        if rec["n"] or w:
            any_data = True
        ok = _walts_complete(se, day)
        complete = complete and ok
        sc = _scope_empty()
        sc["mtm_ok"] = False
        sc["pnl_rec"], sc["n_trades"], sc["fees"], sc["funding"] = rec["pnl"], rec["n"], rec["fees"], rec["funding"]
        sc["pnl_wal"] = (w or _new_wagg())["pnl"] if ok else D0
        # geriye doğru defter değeri: ilk anlık görüntüdeki cüzdan (spot: defter değeri) − o günün başından sonraki hareketler
        base = dec_or_none(fb.get("wallet_balance") if fb.get("kind") != KIND_SPOT else fb.get("book_value"))
        e_rec = None
        if base is not None and ok:
            later = D0
            for dd, agg in se["walts_pre"].items():
                if dd >= day:
                    later += agg["pnl"] + (agg["transfer"] if fb.get("kind") != KIND_SPOT else D0) + agg["other"]
            e_rec = base - later
            den += e_rec
        _scope_add(total, sc)
        if rec["n"] or w:
            books_out[b] = {**_scope_out(sc, status=EKSIK, reasons=["P1A_ÖNCESİ"]), "e_reconstructed": _fl(e_rec),
                            "wallet_view": "RECONSTRUCTED" if ok else "MISSING"}
    if not any_data:
        return None
    out = _scope_out(total, status=EKSIK, reasons=["P1A_ÖNCESİ"])
    out["r_realized"] = _pct(total["pnl_wal"], den) if complete and den > 0 else None
    return {"schema": ROW_SCHEMA, "tgt": TGT_VERSION, "stream": STREAM_LIVE, "day": day, "window": None,
            "final_on": None, "books_added": [], "status": EKSIK, "status_reason": ["P1A_ÖNCESİ"],
            "data_completeness": "RECONSTRUCTED_REALIZED_ONLY" if complete else "MISSING", "total": out, "books": books_out,
            "groups": {}, "instruments": {}, "best_instrument": None, "gold": None, "chance": None, "hit": None}


# ============================================================================ revizyon ve yazım
_VOLATILE = ("rev", "revised", "revision_reason", "computed_at", "run_id", "label", "fp", "nfp", "nfp0")
_STATUS_KEYS = ("status", "status_reason", "label", "hit")


def _strip(obj: Any, drop: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v, drop) for k, v in obj.items() if k not in drop}
    if isinstance(obj, list):
        return [_strip(v, drop) for v in obj]
    return obj


def fingerprint(row: dict) -> str:
    return hashlib.sha256(json.dumps(_strip(row, _VOLATILE), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def numeric_fingerprint(row: dict) -> str:
    """Yalnız sayılar (durum etiketleri ve şans oranı hariç): GEÇİCİ → KESİN geçişi REVİZE sayılmaz."""
    core = _strip({k: row.get(k) for k in ("total", "books", "groups", "instruments", "gold")}, _VOLATILE + _STATUS_KEYS)
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _revision_reason(prev: dict, row: dict) -> list[str]:
    pt, rt = prev.get("total") or {}, row.get("total") or {}
    out = []
    if (pt.get("restored_away") or {}).get("n") != (rt.get("restored_away") or {}).get("n") or "RESTORE" in rt.get("status_reason", []):
        out.append("RESTORE")
    if pt.get("n_trades") != rt.get("n_trades"):
        out.append("GEÇ_GELEN_KAPANIŞ")
    if pt.get("funding") != rt.get("funding"):
        out.append("GEÇ_FONLAMA")
    return out or ["DEĞİŞİKLİK"]


def row_label(row: dict) -> str:
    lab = row.get("status", EKSIK)
    if row.get("revised"):
        lab += f" · {REVIZE}"
        if "RESTORE" in (row.get("revision_reason") or []):
            lab += " (RESTORE)"
    return lab


def latest_rows(paths: EnginePaths) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in read_jsonl(paths.target_daily):
        if r.get("schema") == ROW_SCHEMA and r.get("day"):
            if r["day"] not in out or int(r.get("rev", 0)) >= int(out[r["day"]].get("rev", 0)):
                out[r["day"]] = r
    return out


def merge_rows(existing: dict[str, dict], rows: list[dict], *, now: datetime, run_id: str) -> list[dict]:
    """Yeni hesaplanan satırları mevcut son revizyonlarla karşılaştır; değişenler `rev+1` olarak döner (yalnız ekleme)."""
    out = []
    for r in rows:
        fp, nfp = fingerprint(r), numeric_fingerprint(r)
        prev = existing.get(r["day"])
        if prev is not None and prev.get("fp") == fp:
            continue
        if prev is None:
            r.update(rev=0, nfp0=nfp, revised=False, revision_reason=[])
        else:
            r["rev"] = int(prev.get("rev", 0)) + 1
            r["nfp0"] = prev.get("nfp0") or prev.get("nfp")
            numbers_changed = prev.get("nfp") != nfp
            r["revised"] = bool(prev.get("revised")) or (nfp != r["nfp0"])
            reasons = list(prev.get("revision_reason") or [])
            if numbers_changed:
                reasons = sorted(set(reasons) | set(_revision_reason(prev, r)))
            r["revision_reason"] = reasons
        r.update(fp=fp, nfp=nfp, computed_at=iso(now), run_id=run_id)
        r["label"] = row_label(r)
        out.append(r)
    return out


# ============================================================================ istatistik
def block_bootstrap_means(x: list[float], *, b: int, block: int = BLOCK_LEN, seed: int) -> list[float]:
    """Dairesel blok-bootstrap ortalamaları (blok uzunluğu 5, ⌈n/5⌉ blok)."""
    n = len(x)
    if n == 0:
        return []
    blk = min(block, n)
    k = math.ceil(n / blk)
    ext = x + x[:blk - 1]
    bs = [sum(ext[s:s + blk]) for s in range(n)]
    rng = random.Random(seed)
    length = k * blk
    rr = rng.randrange
    return [sum(bs[rr(n)] for _ in range(k)) / length for _ in range(b)]


def p_tgt(x: list[float], *, target: float = TARGET_PCT, b: int = BOOT_P, seed: int = 0) -> float | None:
    """H0 "ortalama günlük % ≤ target" için tek yönlü blok-bootstrap p (dağılım H0 ortalamasına kaydırılır)."""
    if len(x) < 2:
        return None
    m = statistics.fmean(x)
    y = [v - m + target for v in x]
    means = block_bootstrap_means(y, b=b, seed=seed)
    return (1 + sum(1 for v in means if v >= m)) / (b + 1)


#: Blok-bootstrap aralığı için en az gün (n ≤ blok uzunluğunda yeniden örnekleme dejenere olur).
MIN_CI_DAYS = 2 * BLOCK_LEN


def boot_ci(x: list[float], *, b: int = BOOT_CI, seed: int = 0) -> list[float] | None:
    if len(x) < MIN_CI_DAYS:
        return None
    ms = sorted(block_bootstrap_means(x, b=b, seed=seed))
    return [round(ms[int(0.025 * (b - 1))], 4), round(ms[int(0.975 * (b - 1))], 4)]


def holm(ps: dict[str, float]) -> dict[str, float]:
    items = sorted(ps.items(), key=lambda kv: (kv[1], kv[0]))
    m, run, out = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


# ============================================================================ kapsamlar ve hükümler
def scope_ids(rows: Iterable[dict], declared_instrument: str | None = None) -> tuple[list[str], list[str]]:
    """(hükme uygun kapsamlar, yalnız tanımlayıcı kapsamlar). Bildirilmiş enstrüman yoksa: toplam + 8 defter (gruplar)
    + bütün enstrümanlar + altın toplamı (§7.3)."""
    groups, insts = set(), set()
    for r in rows:
        groups.update((r.get("groups") or {}).keys())
        insts.update(k for k in (r.get("instruments") or {}) if k != "?")
    all_ids = [SCOPE_TOTAL] + [f"group:{g}" for g in sorted(groups)] + [f"inst:{i}" for i in sorted(insts)] + [SCOPE_GOLD]
    if declared_instrument:
        elig = [SCOPE_TOTAL, f"inst:{symbol_key(declared_instrument)}"]
        return elig, [s for s in all_ids if s not in elig]
    return all_ids, []


def scope_value(row: dict, scope: str) -> tuple[str | None, float | None, float | None, int]:
    """(durum, MTM %, yalnız-gerçekleşmiş %, işlem sayısı) — kapsam için. Enstrüman ve altın paydası E_total'dır ve
    durumları toplamın durumudur; o gün hiç hareketi olmayan enstrümanın günü 0'dır (MTM'i olan bir günde)."""
    if scope == SCOPE_TOTAL:
        t = row.get("total") or {}
        return row.get("status"), t.get("r_mtm"), t.get("r_realized"), int(t.get("n_trades") or 0)
    if scope.startswith("group:"):
        g = (row.get("groups") or {}).get(scope[6:]) or {}
        return g.get("status"), g.get("r_mtm"), g.get("r_realized"), int(g.get("n_trades") or 0)
    tot = row.get("total") or {}
    if tot.get("r_mtm") is None:
        return row.get("status"), None, None, 0
    if scope == SCOPE_GOLD:
        g = row.get("gold") or {}
        return row.get("status"), g.get("r_inst"), g.get("r_realized"), int(g.get("n") or 0)
    i = (row.get("instruments") or {}).get(scope[5:])
    if not i:
        return row.get("status"), 0.0, 0.0, 0
    e = tot.get("e_start")
    real = round(i["pnl_wal"] / e * 100, 6) if (e and i.get("pnl_wal") is not None) else None
    return row.get("status"), i.get("r_inst"), real, int(i.get("n") or 0)


def is_look_night(day: str, done_months: Iterable[str]) -> bool:
    """Aylık kayıtlı bakış: her ayın 3. UTC gününden SONRAKİ ilk gece (gün ≥ 4) ve o ay için henüz bakış yoksa."""
    d = parse_day(day)
    return d.day > LOOK_AFTER_UTC_DAY and d.strftime("%Y-%m") not in set(done_months)


def look_verdicts(rows_by_day: dict[str, dict], *, look_day: str, look_night: bool, stream: str = STREAM_LIVE,
                  declared_instrument: str | None = None, b: int = BOOT_P) -> dict[str, Any]:
    """Aylık bakış hükümleri (§7.3). Bakış gecesi değilse HİÇ hüküm yoktur. Pencere: bakış gününden önceki 60 takvim
    günü; yalnız KESİN ve MTM'i olan günler. `KANITLANDI` yalnız `LIVE_PAPER`'da, hükme uygun kapsamda, Holm-düzeltmeli
    p ≤ α_bakış iken, ≥ 40 KESİN gün, ≥ 30 kapanmış işlem, tek gün ≤ %25 VE yalnız-gerçekleşmiş seriyle de aynı koşul."""
    if not look_night:
        return {"look": False, "look_day": look_day, "scopes": {}}
    lo = add_days(look_day, -LOOK_WINDOW_DAYS)
    win = [rows_by_day[d] for d in sorted(rows_by_day) if lo <= d < look_day]
    elig, descr = scope_ids(win, declared_instrument)
    series: dict[str, dict[str, Any]] = {}
    for sc in elig + descr:
        x, xr, n_tr = [], [], 0
        for r in win:
            if r.get("stream") != stream:
                continue
            st, v, vr, n = scope_value(r, sc)
            if st != KESIN or v is None:
                continue
            x.append(float(v))
            xr.append(float(vr) if vr is not None else float("nan"))
            n_tr += n
        series[sc] = {"x": x, "xr": xr, "n_trades": n_tr}
    ps, prs = {}, {}
    for sc in elig:
        s = series[sc]
        enough = len(s["x"]) >= MIN_KESIN_DAYS and s["n_trades"] >= MIN_TRADES
        seed = _seed(TGT_VERSION, "p_tgt", look_day, sc)
        ps[sc] = (p_tgt(s["x"], b=b, seed=seed) or 1.0) if enough else 1.0
        xr_ok = enough and all(v == v for v in s["xr"])
        prs[sc] = (p_tgt(s["xr"], b=b, seed=seed + 1) or 1.0) if xr_ok else 1.0
    ph, prh = holm(ps), holm(prs)
    out: dict[str, Any] = {}
    for sc in elig + descr:
        s = series[sc]
        n = len(s["x"])
        mean = round(statistics.fmean(s["x"]), 6) if s["x"] else None
        tot = sum(s["x"])
        share = (max(s["x"]) / tot) if (s["x"] and tot > 0) else None
        enough = n >= MIN_KESIN_DAYS and s["n_trades"] >= MIN_TRADES
        proven = (stream == STREAM_LIVE and sc in elig and enough and ph.get(sc, 1.0) <= ALPHA_LOOK
                  and prh.get(sc, 1.0) <= ALPHA_LOOK and share is not None and share <= MAX_DAY_SHARE)
        if not enough:
            v = V_THIN
        elif proven:
            v = V_PROVEN
        elif mean is not None and mean >= TARGET_PCT:
            v = V_ONTRACK
        else:
            v = V_BELOW
        out[sc] = {"verdict": v, "eligible": sc in elig, "n_kesin": n, "n_trades": s["n_trades"], "mean_pct": mean,
                   "gap_to_target_pct": round(mean - TARGET_PCT, 6) if mean is not None else None,
                   "ci95": boot_ci(s["x"], seed=_seed(TGT_VERSION, "ci", look_day, sc)) if n >= 2 else None,
                   "p_tgt": ps.get(sc), "p_holm": ph.get(sc), "p_realized": prs.get(sc), "p_realized_holm": prh.get(sc),
                   "max_day_share": round(share, 4) if share is not None else None}
    return {"look": True, "look_day": look_day, "stream": stream, "window": [lo, add_days(look_day, -1)],
            "alpha_look": ALPHA_LOOK, "family_size": len(elig), "declared_instrument": declared_instrument,
            "live": stream == STREAM_LIVE, "scopes": out}


def rolling_stats(rows_by_day: dict[str, dict], *, end_day: str, n: int, scope: str = SCOPE_TOTAL) -> dict[str, Any]:
    """Kayan istatistikler (§7.4; yalnız KESİN günler; ARA GÖRÜNÜM — hüküm değildir)."""
    lo = add_days(end_day, -(n - 1))
    xs, hits, chance = [], 0, []
    for d in sorted(rows_by_day):
        if not (lo <= d <= end_day):
            continue
        r = rows_by_day[d]
        st, v, _vr, _n = scope_value(r, scope)
        if st != KESIN or v is None:
            continue
        xs.append(float(v))
        if scope == SCOPE_TOTAL:
            hits += int(bool((r.get("hit") or {}).get("day_hit")))
            c = (r.get("chance") or {}).get("headline")
            if c is not None:
                chance.append(float(c))
        else:
            hits += int(v >= TARGET_PCT)
    if not xs:
        return {"window_days": n, "n_kesin": 0}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for v in xs:
        eq *= 1 + v / 100.0
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    geo = (eq ** (1 / len(xs)) - 1) * 100.0
    return {"window_days": n, "n_kesin": len(xs), "hit_days": hits, "hit_rate": round(hits / len(xs), 4),
            "hit_rate_wilson95": wilson(hits, len(xs)), "expected_chance_hits": round(sum(chance), 3) if chance else None,
            "mean_pct": round(statistics.fmean(xs), 6), "geo_mean_pct": round(geo, 6), "median_pct": round(statistics.median(xs), 6),
            "ci95": boot_ci(xs, seed=_seed(TGT_VERSION, "roll", end_day, n, scope)), "worst_day_pct": round(min(xs), 6),
            "max_drawdown_pct": round(mdd * 100, 4), "compounded_pct": round((eq - 1) * 100, 4),
            "required_compounded_pct": round(((1 + TARGET_PCT / 100) ** len(xs) - 1) * 100, 4)}


# ============================================================================ S3 orkestrasyonu
def done_look_months(paths: EnginePaths) -> list[str]:
    return [str(r.get("month")) for r in read_jsonl(paths.target_looks) if r.get("schema") == LOOK_SCHEMA]


def last_look(paths: EnginePaths) -> dict | None:
    rows = [r for r in read_jsonl(paths.target_looks) if r.get("schema") == LOOK_SCHEMA]
    return rows[-1] if rows else None


def run_s3(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None,
           declared_instrument: str | None = None) -> dict[str, Any]:
    """S3: satırları yeniden hesapla, değişenleri `target/daily.jsonl`'a EKLE, bakış gecesiyse aylık hükmü
    `target/looks.jsonl`'a ekle, `summary/daily_target.json`'u yaz."""
    now = now or utc_now()
    run_id = run_id or now.strftime("%Y%m%dT%H%M%SZ")
    paths.ensure_tree()
    existing = latest_rows(paths)
    rows = build_rows(paths, now=now, prev_rows=existing)
    new = merge_rows(existing, rows, now=now, run_id=run_id)
    appended = paths.append_jsonl(paths.target_daily, new) if new else 0
    latest = latest_rows(paths)
    run_day = day_of(now)
    look = None
    if is_look_night(run_day, done_look_months(paths)):
        look = look_verdicts(latest, look_day=run_day, look_night=True, declared_instrument=declared_instrument)
        look.update(schema=LOOK_SCHEMA, month=run_day[:7], computed_at=iso(now), run_id=run_id)
        paths.append_jsonl(paths.target_looks, [look])
    end = max(latest) if latest else None
    rolling = {n: rolling_stats(latest, end_day=end, n=n) for n in ROLLING_WINDOWS} if end else {}
    summary = {"schema": "tgt_v1_summary", "tgt": TGT_VERSION, "computed_at": iso(now), "run_id": run_id,
               "rows": [latest[d] for d in sorted(latest)[-14:]], "rolling": rolling, "last_look": last_look(paths),
               "header_tr": render_brief(latest, last_look(paths), rolling, today=run_day)}
    paths.write_json(paths.summary / "daily_target.json", summary)
    return {"stage": "S3", "rows": len(rows), "appended": appended, "new_rev_days": sorted(r["day"] for r in new if r.get("rev")),
            "revised_days": sorted(r["day"] for r in new if r.get("revised")),
            "look": bool(look), "latest_day": end}


# ============================================================================ metin (§7.7)
def fmt_pct(v: float | None, nd: int = 2) -> str:
    return "—" if v is None else f"{v:+.{nd}f}".replace(".", ",") + "%"


def fmt_share(v: float | None) -> str:
    """0–1 payı → Türkçe yüzde ("%12,5")."""
    return "—" if v is None else "%" + f"{v * 100:.1f}".replace(".", ",")


def fmt_num(v: float | None, nd: int = 2) -> str:
    return "—" if v is None else f"{v:+.{nd}f}".replace(".", ",")


def _hm(ts: str | None) -> str:
    t = parse_ts(ts)
    return t.strftime("%m-%d %H:%M") if t else "?"


def render_brief(latest: dict[str, dict], look: dict | None, rolling: dict | None, *, today: str | None = None) -> list[str]:
    """Özet başlık satırları (§7.7 biçimi). Yalnız-gerçekleşmiş satırda `TUTTU`/`HEDEF GÜNÜ` yoktur; her gerçekleşmiş
    sayının yanında açık pozisyonların gerçekleşmemiş kârı yazar. `today` verilirse ve son satır dünün ise "Dün", değilse
    "Gün AA-GG" yazılır."""
    lines: list[str] = []
    if not latest:
        return ["Günlük hedef: henüz satır yok (ilk iki gecelik anlık görüntü gerekir)."]
    yd = max(latest)
    r = latest[yd]
    head = "Dün" if (today is None or add_days(today, -1) == yd) else f"Gün {yd[5:]}"
    t = r.get("total") or {}
    lab = row_label(r)
    if t.get("r_mtm") is not None:
        w = r.get("window") or {}
        lines.append(f"{head} ({lab} · W {_hm(w.get('from'))} → {_hm(w.get('to'))} UTC): MTM toplam {fmt_pct(t['r_mtm'])} "
                     f"(LEDGER_MARK) · gerçekleşmiş {fmt_pct(t.get('r_realized'))} · açık gerçekleşmemiş "
                     f"{fmt_num(t.get('u_end'))} USDT ({fmt_pct(t.get('u_end_pct'))})")
        bi = r.get("best_instrument")
        ch = (r.get("chance") or {}).get("headline")
        if bi:
            lines.append(f"  en iyi enstrüman {bi['inst']} {fmt_pct(bi['r_inst'])} (payda: {DENOM_TOTAL}; {DENOM_BOOK} "
                         f"{fmt_pct(bi.get('r_book_inst'))}) · beklenen şans oranı "
                         f"{fmt_share(ch)} ({CHANCE_HEADLINE})")
        side = "üstünde" if t["r_mtm"] >= TARGET_PCT else "altında"
        tail = f"geçici; kesinleşme {r.get('final_on', '')[5:]}" if r.get("status") == GECICI else lab.lower()
        lines.append(f"  %1'e göre: {side} ({tail})")
    else:
        why = ", ".join(r.get("status_reason") or []) or "veri yok"
        lines.append(f"{head} ({lab}: {why}): yalnız gerçekleşmiş {fmt_pct(t.get('r_realized'))} · kayıt görünümü "
                     f"{fmt_num(t.get('pnl_rec'))} USDT ({t.get('n_trades', 0)} işlem) · açık gerçekleşmemiş "
                     f"{fmt_num(t.get('u_end'))} USDT · MTM yok, hedefle karşılaştırılmaz")
    kes = [d for d in sorted(latest) if latest[d].get("status") == KESIN and (latest[d].get("total") or {}).get("r_mtm") is not None]
    if kes:
        k = latest[kes[-1]]
        h = k.get("hit") or {}
        which = ("toplam" if h.get("total_hit") else f"tek enstrüman {h.get('single_inst')}") if h.get("day_hit") else ""
        lines.append(f"Son kesin gün {kes[-1][5:]}: MTM {fmt_pct((k.get('total') or {}).get('r_mtm'))} → {V_DAY}: "
                     f"{'evet (' + which + '; başarı değildir)' if h.get('day_hit') else 'hayır'}")
    else:
        lines.append("Son kesin gün: henüz yok")
    r60 = (rolling or {}).get(60) or (rolling or {}).get("60") or {}
    ara = (f"ara görünüm 60g ort. {fmt_pct(r60.get('mean_pct'))} [CI {fmt_pct((r60.get('ci95') or [None])[0])}; "
           f"{fmt_pct((r60.get('ci95') or [None, None])[1])}] — hüküm değildir" if r60.get("n_kesin") else
           "ara görünüm: KESİN gün yok — hüküm değildir")
    if look and look.get("scopes"):
        tv = look["scopes"].get(SCOPE_TOTAL) or {}
        extra = f" ({tv.get('n_kesin', 0)} KESİN gün / {MIN_KESIN_DAYS})" if tv.get("verdict") == V_THIN else ""
        lines.append(f"Son bakış {str(look.get('look_day', ''))[5:]}: toplam → {tv.get('verdict', V_THIN)}{extra} · {ara}")
    else:
        lines.append(f"Son bakış: henüz yok (her ayın 3. UTC gününden sonraki ilk gece) · {ara}")
    return lines


def render_table(latest: dict[str, dict], *, days: int = 7) -> list[str]:
    """`engine-status` / `--check` için son N gün: toplam satırı (durum etiketiyle), en iyi enstrüman (hüküm paydasıyla)
    ve şans oranı; en son gün için defter grupları. k* yok; hüküm kelimesi yok."""
    lines = [f"{'gün':<11}{'durum':<22}{'MTM %':>9}{'gerçekl. %':>11}{'kayıt USDT':>12}{'işlem':>6}  en iyi enstrüman (payda: toplam) · şans"]
    sel = sorted(latest)[-days:][::-1]
    for d in sel:
        r = latest[d]
        t = r.get("total") or {}
        bi = r.get("best_instrument") or {}
        ch = (r.get("chance") or {}).get("headline")
        lines.append(f"{d:<11}{row_label(r)[:21]:<22}{fmt_pct(t.get('r_mtm')):>9}{fmt_pct(t.get('r_realized')):>11}"
                     f"{fmt_num(t.get('pnl_rec')):>12}{t.get('n_trades', 0):>6}  "
                     + (f"{bi.get('inst')} {fmt_pct(bi.get('r_inst'))}" if bi else "—")
                     + (f" · şans {fmt_share(ch)}" if ch is not None else ""))
    if sel:
        r = latest[sel[0]]
        lines.append(f"  defterler ({sel[0]}):")
        for g, gv in sorted((r.get("groups") or {}).items()):
            lines.append(f"   {book_name(g):<30}{gv.get('status', ''):<9}MTM {fmt_pct(gv.get('r_mtm'))} · gerçekleşmiş "
                         f"{fmt_pct(gv.get('r_realized'))} · açık gerçekleşmemiş {fmt_num(gv.get('u_end'))} USDT")
    return lines


# ============================================================================ betikle eşitlik görünümü (§7.7 kabul 7)
def ledger_day_views(paths: EnginePaths, *, days: int, now: datetime | None = None) -> dict[str, Any]:
    """`scripts/bot_scorecard.py --daily` ile AYNI tanımlı sayılar (motor arşivinden): defter başına, son N UTC günü için
    kayıt görünümü (`closed_at` günü, son revizyon) ve defter-zaman-damgalı cüzdan görünümü (hareketin kendi `ts` günü,
    P&L türleri; TRANSFER ayrı; günün hareketleri arşivde tam değilse `complete=False`) ile en son anlık görüntünün
    `LEDGER_MARK` gerçekleşmemişi. Betik gözlem anını (`observed_at`) bilemediği için gözlem pencereli görünüm ve MTM
    bu eşitliğe girmez; onlar motorun ölçülmüş anlık görüntülerine dayanır."""
    now = now or utc_now()
    end = day_of(now)
    day_list = [add_days(end, -i) for i in range(days)][::-1]
    snaps = load_snapshots(paths)
    last = snaps[-1] if snaps else {"books": {}}
    out: dict[str, Any] = {"days": day_list, "books": {}}
    for b, st in sorted((last.get("books") or {}).items()):
        if st.get("status") != "OK":
            continue
        rec = scan_closes(paths, b)
        se = scan_entries(paths, b, *book_times(snaps, b))
        bd = {}
        for d in day_list:
            r = rec.get(d) or _new_ragg()
            w = se["walts"].get(d) or _new_wagg()
            bd[d] = {"rec": {"n": r["n"], "net": _fl(r["pnl"]), "fees": _fl(r["fees"]), "funding": _fl(r["funding"]),
                             "slippage": _fl(r["slippage"])},
                     "wal_ts": {"net": _fl(w["pnl"]), "transfer": _fl(w["transfer"]), "complete": _walts_complete(se, d)}}
        out["books"][b] = {"kind": st.get("kind"), "unrealized_now": _fl(dec_or_none(st.get("unrealized"))), "days": bd}
    return out


__all__ = ["ALPHA_LOOK", "BOOT_P", "EKSIK", "FORBIDDEN_ON_REALIZED", "GECICI", "KESIN", "LOOK_SCHEMA", "MIN_KESIN_DAYS",
           "MIN_TRADES", "REVIZE", "ROW_SCHEMA", "SCOPE_GOLD", "SCOPE_TOTAL", "STREAMS", "STREAM_LIVE", "TARGET_PCT",
           "TGT_VERSION", "V_BELOW", "V_DAY", "V_ONTRACK", "V_PROVEN", "V_THIN", "block_bootstrap_means", "boot_ci",
           "build_rows", "chance_rate", "fingerprint", "holm", "is_look_night", "latest_rows", "ledger_day_views",
           "look_verdicts", "merge_rows", "numeric_fingerprint", "p_tgt", "render_brief", "render_table", "rolling_stats",
           "row_label", "run_s3", "scan_closes", "scan_entries", "scope_ids", "scope_value", "wilson"]

"""P2 — UTC günü MTM görünümü (§7.1 "UTC günü MTM (P2)"; P2 kabul 10) — P1a'nın W-günü `LEDGER_MARK` görünümünün YANINDA.

Tanım (§7.1): `E(00:00 D)` = 00:00'daki cüzdan (ARŞİVLENMİŞ hareketlerden) + araştırma deposunun 00:00 kapanışıyla
gerçekleşmemiş `U` (`LAST_PRICE_PROXY`). UTC günü D'nin MTM'i `E(00:00 D+1) − E(00:00 D) − TRANSFER(D)`'dir.

**00:00 cüzdanı (okuma).** Bir ölçülmüş anlık görüntü `S` (okuma anı `R`, `wallet_balance`) ve arşivlenmiş hareketlerle
herhangi bir `T` anı için: `W(T) = W(S) − Σ{gözlem ≤ R, ts > T} + Σ{gözlem > R, ts ≤ T}` (hareketin defter zaman damgası
`ts`; gözlem anı `observed_at`). Sağ taraf her çapa (S) için aynı sonucu vermelidir; motor `T`'yi hem `T`'den SONRAKİ ilk
çapadan (S(D), ≈01:40) hem ÖNCEKİ son çapadan (S(D−1)) hesaplar ve ikisinin 1e-6 içinde eşit olduğunu denetler
(`anchor_check`); ayrıca `W(00:00 D+1) − W(00:00 D) = Σ{ts ∈ (00:00 D, 00:00 D+1]}` (`entries_check`). Tutmazsa ya da
arada hareket boşluğu (`gap_before`) varsa gün `EKSİK`. Spot için aynı formül defter değerine
(B = nakit + kilitli + Σ lot × maliyet; TRANSFER dışı hareketler, `closes` okuma 3) ve lot miktar/maliyetine (alış =
TRANSFER `buy SYM qty@px` notu, satış = kapanış kaydının miktar/maliyeti) uygulanır.

**00:00 gerçekleşmemiş.** 00:00'da açık pozisyonlar: arşivdeki kayıtlardan `opened_at ≤ T < closed_at` olanlar (00:00'daki
miktar = ilk miktar − `ts ≤ T` çıkış dolumları) + `T`'den sonraki ilk çapada hâlâ açık olup `opened_at ≤ T` olanlar (o
çapa ile T arasında kısmi çıkış hareketi varsa miktar bilinmez → `EKSİK`). Fiyat: kapanışı TAM 00:00 olan barın kapanışı
(1m önce; `pathrec.bar_close_at`, kaynak dilim satırda) — `LAST_PRICE_PROXY`. Fiyat yoksa defterin MTM'i `EKSİK`.

**Yan yana ve `tgt_v2` (§7.1, §10 P2).** Satırlar `target/daily_utc.jsonl`'a (şema `tgt_v2_utc_day`) yalnız EKLENİR
(`rev`, `daily_target.merge_rows` ile aynı kural); her satır aynı günün W-günü (`target/daily.jsonl`, `LEDGER_MARK`)
sayılarını yanında taşır. P1a'nın `tgt_v1` satırları ve başlığı DEĞİŞMEZ. Başlık UTC gününe ancak 14 takvim günü yan
yana gösterimden sonra geçer (`tgt_v2_status`); geçişte tanım `TGT_V2_SHA` ile mühürlüdür (test sabitler). Kesinleşme
`tgt_v1` ile aynıdır (≥ 3 gün + o gün kapanan kayıtların fonlama kapsaması; 7 gün sonra hâlâ eksikse `EKSİK (fonlama)`);
ayna defterler (M2X) toplama girmez (`mirror_books`).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .closes import ENTRIES_KEEP, ST_ACTIVE, iter_rows, load_snapshots
from .daily_target import (EKSIK, FINAL_AFTER_DAYS, FUNDING_EKSIK_AFTER_DAYS, GECICI, KESIN, latest_rows, merge_rows)
from .ledgers import (KIND_SPOT, add_days, book_group, dec, dec_or_none, dstr, is_mirror, iso, parse_ts,
                      symbol_key, utc_now)
from .paths import EnginePaths, read_jsonl
from .pathrec import BarSource, bar_close_at

UTC = timezone.utc
UTC_SCHEMA = "tgt_v2_utc_day"
TGT_V2 = "tgt_v2"
MARK_LAST_PRICE_PROXY = "LAST_PRICE_PROXY"
SIDE_BY_SIDE_DAYS = 14
TOL = Decimal("1e-6")
D0 = Decimal(0)
PNL_KINDS = ("PNL", "FEE", "FUNDING", "LIQ_FEE", "TAX")
_BUY_RE = re.compile(r"^buy\s+(\S+)\s+([0-9.Ee+-]+)@([0-9.Ee+-]+)")

#: tgt_v2'nin (UTC günü) tanımı — değişirse sha değişir (test sabitler); başlık geçişi bu tanımla mühürlenir.
TGT_V2_SPEC: dict[str, Any] = {
    "id": TGT_V2, "day": "UTC [00:00 D, 00:00 D+1)", "wallet": "W(T) = W(S) - sum{obs<=R, ts>T} + sum{obs>R, ts<=T}",
    "anchor_check": "first anchor after T == last anchor before T (1e-6)", "entries_check": "dW == sum{ts in (T0,T1]} (1e-6)",
    "unrealized": "open at T: archive opened<=T<closed (qty - exit fills ts<=T) + first anchor after T (opened<=T)",
    "mark": "close of the bar closing exactly at T (1m first)", "mark_label": MARK_LAST_PRICE_PROXY,
    "mtm": "E(T1) - E(T0) - TRANSFER(T0,T1]", "final": "tgt_v1 rules (>=3 days + funding coverage; 7 days -> EKSIK)",
    "side_by_side_days": SIDE_BY_SIDE_DAYS, "mirrors": "excluded from totals",
}
TGT_V2_SHA = hashlib.sha256(json.dumps(TGT_V2_SPEC, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _t(day: str) -> datetime:
    y, m, d = (int(x) for x in day[:10].split("-"))
    return datetime(y, m, d, tzinfo=UTC)


# ============================================================================ defter zaman çizelgesi
@dataclass
class _Entry:
    obs: datetime
    ts: datetime
    kind: str
    amount: Decimal
    ref_id: str
    note: str
    symbol: str | None
    gap_before: bool


@dataclass
class BookTimeline:
    book: str
    kind: str
    anchors: list[tuple[datetime, dict]] = field(default_factory=list)
    entries: list[_Entry] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)               # vadeli: geceyi aşan kayıtlar (açık pozisyon)
    sales: list[dict] = field(default_factory=list)               # spot: satış kayıtları
    closed_by_day: dict[str, list[dict]] = field(default_factory=dict)

    def anchor_after(self, t: datetime) -> tuple[datetime, dict] | None:
        for at, body in self.anchors:
            if at >= t:
                return at, body
        return None

    def anchor_before(self, t: datetime) -> tuple[datetime, dict] | None:
        best = None
        for at, body in self.anchors:
            if at < t:
                best = (at, body)
            else:
                break
        return best


def load_timeline(paths: EnginePaths, book: str, snaps: list[dict]) -> BookTimeline:
    kind = KIND_SPOT
    anchors = []
    for s in snaps:
        body = (s.get("books") or {}).get(book)
        if isinstance(body, dict) and body.get("status") == "OK":
            at = parse_ts(body.get("read_at"))
            if at is not None:
                anchors.append((at, body))
                kind = str(body.get("kind") or kind)
    anchors.sort(key=lambda x: x[0])
    tl = BookTimeline(book=book, kind=kind, anchors=anchors)
    for row in iter_rows(paths.book_entries_dir(book)):
        e = row.get("entry") if isinstance(row.get("entry"), dict) else {}
        obs, ts = parse_ts(row.get("observed_at")), parse_ts(e.get("ts"))
        if obs is None or ts is None:
            continue
        tl.entries.append(_Entry(obs, ts, str(e.get("kind", "")), dec(e.get("amount")), str(e.get("ref_id", "")),
                                 str(e.get("note", "")), row.get("symbol"), bool(row.get("gap_before"))))
    latest: dict[str, dict] = {}
    for row in iter_rows(paths.book_closes_dir(book)):
        if row.get("row") == "derived":
            continue
        k = str(row.get("trade_key"))
        rec = row.get("record") if isinstance(row.get("record"), dict) else None
        prev = latest.get(k)
        latest[k] = {"status": str(row.get("status", ST_ACTIVE)), "obs": parse_ts((prev or {}).get("obs_raw") or row.get("observed_at")),
                     "obs_raw": (prev or {}).get("obs_raw") or row.get("observed_at"),
                     "rec": rec if rec is not None else (prev or {}).get("rec")}
    for k, v in latest.items():
        rec = v.get("rec")
        if v["status"] != ST_ACTIVE or not isinstance(rec, dict):
            continue
        o, c = parse_ts(rec.get("opened_at")), parse_ts(rec.get("closed_at"))
        if o is None or c is None:
            continue
        cday = c.date().isoformat()
        cov = ((rec.get("features") or {}).get("funding_coverage") or {}) if isinstance(rec.get("features"), dict) else {}
        tl.closed_by_day.setdefault(cday, []).append({"funding_complete": cov.get("complete") if tl.kind != KIND_SPOT else True})
        if tl.kind == KIND_SPOT:
            tl.sales.append({"closed": c, "obs": v["obs"], "symbol": str(rec.get("symbol") or ""), "qty": dec(rec.get("quantity")),
                             "cost": dec(rec.get("effective_notional"))})
        elif o.date() != c.date() or (c - o) >= timedelta(days=1):
            exits = [(parse_ts(f.get("ts")), dec(f.get("qty"))) for f in (rec.get("fills") or [])
                     if isinstance(f, dict) and f.get("kind") != "entry"]
            tl.spans.append({"opened": o, "closed": c, "symbol": str(rec.get("symbol") or ""), "side": str(rec.get("side") or ""),
                             "qty": dec(rec.get("quantity")), "entry": dec(rec.get("entry")), "exits": exits, "id": str(rec.get("id"))})
    return tl


# ============================================================================ T anındaki durum
def wallet_at(tl: BookTimeline, t: datetime, anchor: tuple[datetime, dict] | None) -> tuple[Decimal | None, dict]:
    """Çapadan `T` anı cüzdanı (vadeli `wallet_balance`; spot defter değeri B). Bilgi: çapa anı ve kullanılan hareketler."""
    if anchor is None:
        return None, {"anchor": None}
    at, body = anchor
    base = dec_or_none(body.get("wallet_balance")) if tl.kind != KIND_SPOT else dec_or_none(body.get("book_value"))
    if base is None:
        return None, {"anchor": iso(at), "error": "çapada cüzdan yok"}
    adj = D0
    n = 0
    for e in tl.entries:
        if tl.kind == KIND_SPOT and e.kind == "TRANSFER":
            continue
        if e.obs <= at and e.ts > t:
            adj -= e.amount
            n += 1
        elif e.obs > at and e.ts <= t:
            adj += e.amount
            n += 1
    return base + adj, {"anchor": iso(at), "adjusted_entries": n}


def _spot_holdings_at(tl: BookTimeline, t: datetime, anchor: tuple[datetime, dict]) -> dict[str, dict[str, Decimal]]:
    at, body = anchor
    hold: dict[str, dict[str, Decimal]] = {}
    for sym, v in (body.get("lots") or {}).items():
        hold[symbol_key(sym)] = {"qty": dec(v.get("qty")), "cost": dec(v.get("cost")), "symbol": sym}
    for e in tl.entries:
        if e.kind != "TRANSFER":
            continue
        m = _BUY_RE.match(e.note)
        if not m:
            continue
        k = symbol_key(m.group(1))
        q = dec(m.group(2))
        h = hold.setdefault(k, {"qty": D0, "cost": D0, "symbol": m.group(1)})
        if e.obs <= at and e.ts > t:
            h["qty"] -= q
            h["cost"] -= -e.amount
        elif e.obs > at and e.ts <= t:
            h["qty"] += q
            h["cost"] += -e.amount
    for s in tl.sales:
        k = symbol_key(s["symbol"])
        h = hold.setdefault(k, {"qty": D0, "cost": D0, "symbol": s["symbol"]})
        obs = s["obs"] or s["closed"]
        if obs <= at and s["closed"] > t:
            h["qty"] += s["qty"]
            h["cost"] += s["cost"]
        elif obs > at and s["closed"] <= t:
            h["qty"] -= s["qty"]
            h["cost"] -= s["cost"]
    return {k: v for k, v in hold.items() if v["qty"] != 0}


def _futures_open_at(tl: BookTimeline, t: datetime, anchor: tuple[datetime, dict]) -> tuple[list[dict], list[str]]:
    """00:00'da açık pozisyonlar: (sembol, yön, miktar, giriş). Belirsiz miktar → neden listesi."""
    at, body = anchor
    out, issues = [], []
    seen = set()
    for s in tl.spans:
        if s["opened"] <= t < s["closed"]:
            q = s["qty"] - sum((qq for ts, qq in s["exits"] if ts is not None and ts <= t), D0)
            if q > 0:
                out.append({"symbol": s["symbol"], "side": s["side"], "qty": q, "entry": s["entry"], "id": s["id"], "src": "archive"})
            seen.add(s["id"])
    for p in body.get("positions") or []:
        o = parse_ts(p.get("opened_at"))
        pid = str(p.get("id", ""))
        if o is None or o > t or pid in seen:
            continue
        partial = [e for e in tl.entries if e.ref_id == pid and e.kind == "PNL" and t < e.ts <= at]
        if partial:
            issues.append(f"U_QTY_UNKNOWN:{pid}")
            continue
        out.append({"symbol": str(p.get("symbol") or ""), "side": str(p.get("side") or ""), "qty": dec(p.get("qty")),
                    "entry": dec(p.get("entry")), "id": pid, "src": "anchor"})
    return out, issues


def state_at(tl: BookTimeline, t: datetime, src: BarSource | None) -> dict[str, Any]:
    """`T` anı: cüzdan (iki çapadan, denetimli), gerçekleşmemiş (`LAST_PRICE_PROXY`), E."""
    a_after, a_before = tl.anchor_after(t), tl.anchor_before(t)
    out: dict[str, Any] = {"t": iso(t), "ok": True, "reasons": []}
    w1, i1 = wallet_at(tl, t, a_after)
    w0, i0 = wallet_at(tl, t, a_before)
    out["wallet"], out["wallet_anchor"] = dstr(w1), i1
    if w1 is None:
        out["ok"] = False
        out["reasons"].append("ÇAPA_YOK")
        return out
    if w0 is not None:
        diff = w1 - w0
        out["anchor_check"] = {"prev_anchor": i0.get("anchor"), "wallet_prev_anchor": dstr(w0), "diff": dstr(diff),
                               "ok": abs(diff) <= TOL}
        if abs(diff) > TOL:
            out["ok"] = False
            out["reasons"].append("ÇAPA_EŞİTLİĞİ_TUTMADI")
    else:
        out["anchor_check"] = {"prev_anchor": None, "ok": None}
        # T ilk çapadan önce: çapa okumasında ledger hareket listesi DOLUYSA (rotasyon) ve elde kalan en eski hareket
        # T'den sonraysa, T ile o hareket arasındakiler arşive hiç girmemiş olabilir → W(T) bilinmez (dürüst EKSİK)
        at1, body1 = a_after
        if int(body1.get("n_entries") or 0) >= ENTRIES_KEEP:
            first_ts = min((e.ts for e in tl.entries if e.obs <= at1), default=None)
            if first_ts is None or first_ts > t:
                out["ok"] = False
                out["reasons"].append("HAREKETLER_ARŞİVDEN_ÖNCE_DÖNDÜ")
    lo = a_before[0] if a_before else None
    hi = a_after[0]
    if any(e.gap_before and (lo is None or e.obs > lo) and e.obs <= hi for e in tl.entries):
        out["ok"] = False
        out["reasons"].append("ENTRIES_GAP")
    t_ms = int(t.timestamp() * 1000)
    unreal, marks = D0, {}
    if tl.kind == KIND_SPOT:
        for k, h in sorted(_spot_holdings_at(tl, t, a_after).items()):
            px, tf, _u = bar_close_at(src, "spot", k, t_ms)
            if px is None:
                px, tf, _u = bar_close_at(src, "futures", k, t_ms)
                tf = f"futures:{tf}" if tf else None
            if px is None:
                out["ok"] = False
                out["reasons"].append(f"MARK_EKSİK:{k}")
                continue
            unreal += h["qty"] * px - h["cost"]
            marks[k] = {"price": dstr(px), "tf": tf, "qty": dstr(h["qty"]), "cost": dstr(h["cost"])}
    else:
        pos, issues = _futures_open_at(tl, t, a_after)
        if issues:
            out["ok"] = False
            out["reasons"] += issues
        for p in pos:
            k = symbol_key(p["symbol"])
            px, tf, _u = bar_close_at(src, "futures", k, t_ms)
            if px is None:
                out["ok"] = False
                out["reasons"].append(f"MARK_EKSİK:{k}")
                continue
            sg = -1 if p["side"].upper() in ("SHORT", "SELL") else 1
            u = (px - p["entry"]) * p["qty"] * sg
            unreal += u
            marks[f"{k}:{p['id']}"] = {"price": dstr(px), "tf": tf, "qty": dstr(p["qty"]), "entry": dstr(p["entry"]),
                                       "unrealized": dstr(u), "src": p["src"]}
    out["unrealized"] = dstr(unreal) if out["ok"] or not any(r.startswith("MARK_EKSİK") for r in out["reasons"]) else None
    out["marks"] = marks
    out["mark_source"] = MARK_LAST_PRICE_PROXY
    out["equity"] = dstr(w1 + unreal) if out["unrealized"] is not None else None
    return out


def _sum_entries(tl: BookTimeline, t0: datetime, t1: datetime) -> dict[str, Decimal]:
    out = {"pnl": D0, "transfer": D0, "other": D0, "all": D0}
    for e in tl.entries:
        if t0 < e.ts <= t1:
            if tl.kind == KIND_SPOT and e.kind == "TRANSFER":
                continue
            out["all"] += e.amount
            if e.kind in PNL_KINDS:
                out["pnl"] += e.amount
            elif e.kind == "TRANSFER":
                out["transfer"] += e.amount
            else:
                out["other"] += e.amount
    return out


# ============================================================================ satırlar
def _book_day(tl: BookTimeline, day: str, src: BarSource | None, run_day: str) -> dict[str, Any]:
    t0, t1 = _t(day), _t(add_days(day, 1))
    s0, s1 = state_at(tl, t0, src), state_at(tl, t1, src)
    sums = _sum_entries(tl, t0, t1)
    reasons = list(s0["reasons"]) + list(s1["reasons"])
    status = KESIN
    out: dict[str, Any] = {"kind": tl.kind, "start": s0, "end": s1, "pnl_wal_utc": dstr(sums["pnl"]),
                           "transfer": dstr(sums["transfer"]), "other": dstr(sums["other"])}
    w0, w1 = dec_or_none(s0.get("wallet")), dec_or_none(s1.get("wallet"))
    if w0 is not None and w1 is not None:
        diff = (w1 - w0) - sums["all"]
        out["entries_check"] = {"delta_wallet": dstr(w1 - w0), "entries": dstr(sums["all"]), "diff": dstr(diff), "ok": abs(diff) <= TOL}
        if abs(diff) > TOL:
            reasons.append("HAREKET_EŞİTLİĞİ_TUTMADI")
    e0, e1 = dec_or_none(s0.get("equity")), dec_or_none(s1.get("equity"))
    if e0 is not None and e1 is not None and s0["ok"] and s1["ok"]:
        out["e_start"], out["e_end"] = dstr(e0), dstr(e1)
        mtm = e1 - e0 - (sums["transfer"] if tl.kind != KIND_SPOT else D0) - sums["other"]
        out["pnl_mtm_utc"] = dstr(mtm)
        out["r_mtm_utc"] = round(float(mtm / e0 * 100), 6) if e0 > 0 else None
    else:
        out["e_start"] = out["e_end"] = out["pnl_mtm_utc"] = out["r_mtm_utc"] = None
        status = EKSIK
    if reasons:
        status = EKSIK
    pend = [r for r in tl.closed_by_day.get(day, []) if r.get("funding_complete") is not True]
    if pend and status != EKSIK:
        if run_day >= add_days(day, FUNDING_EKSIK_AFTER_DAYS):
            status, reasons = EKSIK, reasons + ["EKSİK_FONLAMA"]
        else:
            status, reasons = GECICI, reasons + ["FONLAMA_BEKLİYOR"]
    if run_day < add_days(day, FINAL_AFTER_DAYS) and status != EKSIK:
        status, reasons = GECICI, reasons + ["3_GÜN_DOLMADI"]
    out["status"], out["status_reason"] = status, sorted(set(reasons))
    return out


def _w_view(w_row: dict | None) -> dict[str, Any] | None:
    if not w_row:
        return None
    t = w_row.get("total") or {}
    return {"window": w_row.get("window"), "status": w_row.get("status"), "pnl_mtm": t.get("pnl_mtm"), "r_mtm": t.get("r_mtm"),
            "pnl_wal": t.get("pnl_wal"), "e_start": t.get("e_start"), "mark_source": "LEDGER_MARK", "rev": w_row.get("rev")}


def build_utc_rows(paths: EnginePaths, *, now: datetime | None = None, src: BarSource | None = None) -> list[dict]:
    """Bütün UTC günlerinin satırları (ilk anlık görüntü gününden son anlık görüntünün önceki gününe)."""
    now = now or utc_now()
    snaps = load_snapshots(paths, light=True)
    if not snaps:
        return []
    run_day = now.date().isoformat()
    books = sorted({b for s in snaps for b in (s.get("books") or {})})
    tls = {b: load_timeline(paths, b, snaps) for b in books}
    days = [s["day"] for s in snaps]
    w_rows = latest_rows(paths)
    rows = []
    d = days[0]
    while d < days[-1]:
        rows.append(_utc_row(d, tls, src, run_day, w_rows.get(d)))
        d = add_days(d, 1)
    return rows


def _utc_row(day: str, tls: dict[str, BookTimeline], src: BarSource | None, run_day: str, w_row: dict | None) -> dict[str, Any]:
    books_out: dict[str, Any] = {}
    mirrors: dict[str, Any] = {}
    tot = {"e_start": D0, "pnl_mtm_utc": D0, "pnl_wal_utc": D0, "ok": True}
    groups: dict[str, dict[str, Any]] = {}
    statuses, reasons = [], []
    for b, tl in sorted(tls.items()):
        if not tl.anchors:
            continue
        bd = _book_day(tl, day, src, run_day)
        if is_mirror(b):
            mirrors[b] = bd
            continue
        books_out[b] = bd
        statuses.append(bd["status"])
        reasons += [f"{b}:{r}" for r in bd["status_reason"]]
        g = groups.setdefault(book_group(b), {"e_start": D0, "pnl_mtm_utc": D0, "ok": True, "books": []})
        g["books"].append(b)
        if bd.get("pnl_mtm_utc") is None:
            tot["ok"] = g["ok"] = False
            continue
        for acc in (tot, g):
            acc["e_start"] += dec(bd["e_start"])
            acc["pnl_mtm_utc"] += dec(bd["pnl_mtm_utc"])
        tot["pnl_wal_utc"] += dec(bd["pnl_wal_utc"])
    rank = {KESIN: 0, GECICI: 1, EKSIK: 2}
    status = max(statuses, key=lambda s: rank.get(s, 2)) if statuses else EKSIK

    def _scope(acc: dict) -> dict:
        ok = acc["ok"]
        e, m = acc["e_start"], acc["pnl_mtm_utc"]
        return {"e_start": dstr(e) if ok else None, "pnl_mtm_utc": dstr(m) if ok else None,
                "r_mtm_utc": round(float(m / e * 100), 6) if ok and e > 0 else None}
    total = {**_scope(tot), "pnl_wal_utc": dstr(tot["pnl_wal_utc"]), "status": status, "status_reason": sorted(set(reasons)),
             "mark_source": MARK_LAST_PRICE_PROXY}
    return {"schema": UTC_SCHEMA, "tgt": TGT_V2, "tgt_sha": TGT_V2_SHA, "stream": "LIVE_PAPER", "day": day,
            "window": {"from": iso(_t(day)), "to": iso(_t(add_days(day, 1))), "kind": "UTC"}, "status": status,
            "total": total, "groups": {g: {**_scope(v), "books": v["books"]} for g, v in sorted(groups.items())},
            "books": books_out, "mirror_books": mirrors, "w_day": _w_view(w_row),
            "side_by_side": {"w_r_mtm": (_w_view(w_row) or {}).get("r_mtm"), "utc_r_mtm": total.get("r_mtm_utc")}}


def latest_utc_rows(paths: EnginePaths) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in read_jsonl(paths.target_daily_utc):
        if r.get("schema") == UTC_SCHEMA and r.get("day"):
            if r["day"] not in out or int(r.get("rev", 0)) >= int(out[r["day"]].get("rev", 0)):
                out[r["day"]] = r
    return out


def tgt_v2_status(paths: EnginePaths, *, today: str | None = None, rows: dict[str, dict] | None = None) -> dict[str, Any]:
    """Başlık geçişi (§7.1): UTC satırı W-günü satırıyla en az `SIDE_BY_SIDE_DAYS` (14) takvim günü yan yana yayımlanmadan
    başlık `tgt_v1` (W-günü) kalır; sonra `tgt_v2` (UTC günü, `TGT_V2_SHA` ile mühürlü tanım)."""
    rows = rows if rows is not None else latest_utc_rows(paths)
    both = sorted(d for d, r in rows.items() if r.get("w_day") and (today is None or d < today))
    first = both[0] if both else None
    n = len(both)
    active = n >= SIDE_BY_SIDE_DAYS
    return {"headline": TGT_V2 if active else "tgt_v1", "active": active, "side_by_side_days": n,
            "required_days": SIDE_BY_SIDE_DAYS, "first_side_by_side_day": first,
            "activates_on": add_days(first, SIDE_BY_SIDE_DAYS) if first else None, "tgt_v2_sha": TGT_V2_SHA}


def run_utc_day(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None,
                src: BarSource | None = None) -> dict[str, Any]:
    """UTC günü satırlarını hesapla, değişenleri `target/daily_utc.jsonl`'a EKLE (rev). P2b gece aşamasına bağlar (S3'ün
    yanında, S1b'den sonra; depo mühürlü okuyucusu `journal.open_store`)."""
    now = now or utc_now()
    run_id = run_id or now.strftime("%Y%m%dT%H%M%SZ")
    existing = latest_utc_rows(paths)
    rows = build_utc_rows(paths, now=now, src=src)
    new = merge_rows(existing, rows, now=now, run_id=run_id)
    appended = paths.append_jsonl(paths.target_daily_utc, new) if new else 0
    latest = latest_utc_rows(paths)
    return {"stage": "S3_UTC", "rows": len(rows), "appended": appended, "latest_day": max(latest) if latest else None,
            "tgt_v2": tgt_v2_status(paths, today=now.date().isoformat(), rows=latest)}


__all__ = ["BookTimeline", "MARK_LAST_PRICE_PROXY", "SIDE_BY_SIDE_DAYS", "TGT_V2", "TGT_V2_SHA", "TGT_V2_SPEC", "UTC_SCHEMA",
           "build_utc_rows", "latest_utc_rows", "load_timeline", "run_utc_day", "state_at", "tgt_v2_status", "wallet_at"]

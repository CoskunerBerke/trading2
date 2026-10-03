# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN testleri için yardımcı (2026-09-29): sentetik ortak deneyim satırları, rastgele depo üreticisi ve
KABA KUVVET kâhini. Test dosyası DEĞİLDİR (pytest toplamaz); testler `sys.path` ile içe aktarır.

Satırlar üretimdeki sözleşmeyle aynı alanları taşır (şema, `row_id = rows.row_id(...)`, `setup_key`, `family_key`,
situation_v1 anlık görüntüsü, `recorded_at` = tur BAŞLANGICI µs hassasiyetinde, olay saatleri saniyeye kırpılmış).
Üretim değişmezi korunur: k. toplu yazımdaki her olay (`opened_at`, `created_at`, `closed_at`, `labeled_at`) bir sonraki
turun başlangıcından (`T_{k+1}`) ÖNCEDİR.

Kâhin (`oracle`) katlamadan BAĞIMSIZ, tanımsal bir hesaptır: her hedef için deponun TAMAMI taranır; kanıt = anahtar
başına ilk görülen bağlam + ilk nitelikli değer/kaybolma olayı; görünür ⇔ `max(saat(değer), saat(bağlam), olay) < as_of`
(saat = satırı yazan toplu yazımın MONOTON saati `max(önceki, recorded_at)`; satırın kendi `recorded_at`'i DEĞİL; saati
ilerletmeyen toplu yazımda katlanan kanıt, saati ilerleten sonraki toplu yazımda o saatle bilinir; hedef yalnız kendi
toplu yazımına kadar BİLİNEN kanıtı görür — geri adımsız depolarda bu koşul `avail < as_of`tan zaten çıkar);
istatistikler hücre başına sıfırdan (`fractions.Fraction`) hesaplanır; yalnız son kayan nokta formülü paylaşılır.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.shared_experience import advisor as A  # noqa: E402
from tradingbot.shared_experience import report as XR  # noqa: E402
from tradingbot.shared_experience import rows as R  # noqa: E402
from tradingbot.shared_experience import situation as S  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 10, 5, 0, 3, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)
MIN = 60_000
HOUR = 3_600_000
DAY = 86_400_000
STEP = 10 * MIN


# ============================================================================ zaman
def iso_s(ms: int) -> str:
    """Olay saati: saniyeye KIRPILMIŞ ISO (üretimdeki `core.iso`)."""
    return datetime.fromtimestamp((ms // 1000), tz=UTC).isoformat(timespec="seconds")


def iso_us(ms: int, us: int = 0) -> str:
    """`recorded_at`: tur başlangıcı µs hassasiyetinde (üretimdeki `ra.isoformat()`)."""
    return (datetime.fromtimestamp(ms // 1000, tz=UTC) + timedelta(milliseconds=ms % 1000, microseconds=us)).isoformat()


def snap(trend: str | None = "UP", vol: str | None = "NORMAL", btc: str | None = "UP", volume: str | None = "NORMAL",
         structure: str | None = "HH_HL", *, schema_sha: str | None = None) -> dict[str, Any]:
    return {"schema_id": S.SCHEMA_ID, "schema_sha": schema_sha or S.SCHEMA_SHA, "status": "OK", "h4_trend": trend,
            "h4_vol_regime": vol, "btc_h4_trend": btc, "h4_vol_bucket": volume, "h4_structure": structure}


# ============================================================================ satırlar
def _env(kind: str, book: str, key: str, rev: int, *, rec: str, symbol: str, side: str, setup: str | None,
         origin: str) -> dict[str, Any]:
    rid = R.row_id(kind, book, key, rev)
    fam = R.family_of(book, setup)
    return {"schema": R.ROW_SCHEMA, "row_id": rid, "decision_id": rid, "kind": kind, "rev": int(rev), "book": book,
            "book_name": R.BOOKS[book][0], "book_type": R.BOOKS[book][1], "symbol": symbol, "side": side,
            "market": "USDM_PERP", "setup_type": setup, "variation": None,
            "setup_key": R.setup_key(book, setup, side) if setup else None, "family": fam,
            "family_key": R.family_key(fam, side), "origin": origin, "recorded_at": rec, "app_mode": "PAPER",
            "code_sha": None, "config_hash": None, "layer_version": "1.0.0"}


def entry(book: str, tid: str, opened_ms: int, *, rec: str, side: str = "LONG", setup: str | None = "trend",
          symbol: str = "SOL/USDT:USDT", sn: dict | None | str = "default", status: str | None = "OK",
          origin: str = "LIVE", sc: str = "policy") -> dict[str, Any]:
    oa = iso_s(opened_ms)
    tk = R.trade_key(book, tid, oa)
    row = _env(R.KIND_ENTRY, book, tk, 0, rec=rec, symbol=symbol, side=side, setup=setup, origin=origin)
    row.update({"trade_key": tk, "trade_id": tid, "opened_at": oa, "as_of_ms": (opened_ms // 1000) * 1000,
                "scorecard_class": sc, "cohort": "POLICY", "pre_learning": sc == "before",
                "snapshot": snap() if sn == "default" else sn, "snapshot_status": status})
    return row


def outcome(book: str, tid: str, opened_ms: int, closed_ms: int, r: float | None, *, rec: str, rev: int = 0,
            final: bool = True, side: str = "LONG", setup: str | None = "trend", symbol: str = "SOL/USDT:USDT",
            origin: str = "LIVE", net: bool = True) -> dict[str, Any]:
    oa = iso_s(opened_ms)
    tk = R.trade_key(book, tid, oa)
    row = _env(R.KIND_OUTCOME, book, tk, rev, rec=rec, symbol=symbol, side=side, setup=setup, origin=origin)
    row.update({"trade_key": tk, "trade_id": tid, "opened_at": oa, "closed_at": iso_s(closed_ms), "final": bool(final),
                "label_version": R.REAL_LABEL_VERSION, "r_basis": R.NET, "in_net_stats": bool(net and r is not None),
                "r_net": r, "scorecard_class": "policy", "fp": "%08x" % (rev + 1)})
    return row


def cf(book: str, cid: str, created_ms: int, *, rec: str, rev: int = 0, status: str = R.PENDING, r: float | None = None,
       labeled_ms: int | None = None, lv: str | None = "cf_label_v3", side: str = "LONG", setup: str | None = "trend",
       symbol: str = "SOL/USDT:USDT", sn: dict | None | str = "default", sst: str | None = "OK", a15: bool = False,
       approx: bool | None = None, amb: bool | None = None, origin: str = "LIVE",
       reason_family: str = "GATE") -> dict[str, Any]:
    ck = R.cf_key(book, cid)
    row = _env(R.KIND_CF, book, ck, rev, rec=rec, symbol=symbol, side=side, setup=setup, origin=origin)
    row.update({"cf_key": ck, "cf_id": cid, "status": status, "final": status != R.PENDING,
                "created_at": iso_s(created_ms), "as_of_ms": (created_ms // 1000) * 1000, "reason": "X",
                "reason_family": reason_family, "baseline_blocked": bool(a15), "approx": approx,
                "label_ts": iso_s(created_ms + 24 * HOUR)})
    if rev == 0:
        row.update({"snapshot": snap() if sn == "default" else sn, "snapshot_status": sst})
    if status == R.LABELLED:
        cls = R.cf_label_class(lv)
        basis = cls if (cls != R.NET or r is not None) else R.NET_UNAVAILABLE
        row.update({"label_version": lv, "r_basis": basis, "in_net_stats": basis == R.NET,
                    "r_net": r if basis == R.NET else None, "r_gross": r, "outcome": {"approx": approx},
                    "intrabar_ambiguous": amb, "labeled_at": iso_s(labeled_ms if labeled_ms is not None else created_ms)})
    row["fp"] = "%08x" % (rev + 7)
    return row


def write_hot(root: Path, rows: Iterable[dict[str, Any]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / "experience.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows),
                 encoding="utf-8")
    return p


def batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    cur: Any = object()
    for r in rows:
        if not out or r.get("recorded_at") != cur:
            out.append([])
        out[-1].append(r)
        cur = r.get("recorded_at")
    return out


def fold_all(rows: list[dict[str, Any]], *, born_ms: int | None = None, collect: bool = False) -> tuple[A.AdvisorFold,
                                                                                                         list[dict]]:
    f = A.AdvisorFold(born_ms=born_ms, collect=collect)
    out: list[dict] = []
    for b in batches(rows):
        out.extend(f.fold_batch(b).advice_rows)
    return f, out


# ============================================================================ rastgele depo
_BOOKS = (("strategy_paper_box", "box_fade"), ("strategy_paper", "trend"), ("main", "pullback"),
          ("strategy_paper_candle4h_strict", "candle:CV001"))
_SYMS = ("SOL/USDT:USDT", "ETH/USDT:USDT", "AVAX/USDT:USDT")
_TRENDS = ("UP", "DOWN")
_VOLS = ("NORMAL", "HIGH")
_RS = (-1.0, -1.0, -0.5, -0.25, 0.3, 0.8, 1.5, 2.2, -1.0, 0.0)


def random_store(seed: int, *, n_batches: int = 18, n_backfill: int = 220, focus: float = 0.7,
                 clock_steps: bool = False) -> list[dict[str, Any]]:
    """Deterministik rastgele depo: geri doldurma kanıtı (çok günlü), sonra canlı turlar — tur ortası olaylar, geç
    etiketler, v1→v3 yeniden etiket, ertelenmiş girişler, geç kesinleşme, tekrarlar, kaybolma, saat eşitlikleri.

    `clock_steps=True` (2026-09-30): turların ~%20'sinde duvar saati GERİ adım atmıştır — o turun `recorded_at`'i ve
    bütün olay saatleri (açılış, kapanış, etiket) 10 dk – 2 sa geriye damgalanır (üretimde yeniden başlatma / NTP adımı).
    Varsayılan (False) rastgele akışı DEĞİŞTİRMEZ."""
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    t_bf = T0_MS - 2 * STEP
    mode = seed % 3                                     # 0 Box odak · 1 TREND ailesi iki kurulum (F seviyeleri) · 2 eksi eğik
    rs = _RS if mode != 2 else (-1.0, -1.0, -1.0, -0.8, -0.5, 0.3, -1.0, 0.5, -1.0, -0.2)
    hot = (_BOOKS[0],) if mode != 1 else (_BOOKS[1], _BOOKS[2])
    rec_bf = iso_us(t_bf, rng.randrange(1, 999_999))

    def dims() -> dict[str, Any]:
        return snap(rng.choice(_TRENDS), rng.choice(_VOLS), rng.choice(_TRENDS), "NORMAL",
                    rng.choice(("HH_HL", "LH_LL", None)))

    def book() -> tuple[str, str]:
        # yoğun hücre: çoğu satır tek kuruluma düşer (n ≥ 30, ≥ 5 gün → yeterli seviyeler de sınanır)
        return rng.choice(hot) if rng.random() < focus else _BOOKS[rng.randrange(len(_BOOKS))]

    def side_of(bk: str) -> str:
        return "LONG" if (any(bk == h[0] for h in hot) and rng.random() < 0.85) else rng.choice(("LONG", "SHORT"))
    # --- geri doldurma: 6–12 gün öncesi, tek toplu yazım
    for i in range(n_backfill):
        bk, st = book()
        side = side_of(bk)
        sym = rng.choice(_SYMS)
        as_of = T0_MS - rng.randrange(6 * DAY, 12 * DAY)
        r = rng.choice(rs)
        if rng.random() < 0.5:
            tid = "BF%03d" % i
            rows.append(entry(bk, tid, as_of, rec=rec_bf, side=side, setup=st, symbol=sym, sn=dims(),
                              origin=R.BACKFILL_HISTORY))
            rows.append(outcome(bk, tid, as_of, as_of + rng.randrange(HOUR, 9 * HOUR), r, rec=rec_bf, side=side,
                                setup=st, symbol=sym, origin=R.BACKFILL_HISTORY))
        else:
            rows.append(cf(bk, "bf%03d" % i, as_of, rec=rec_bf, status=R.LABELLED, r=r,
                           labeled_ms=as_of + rng.randrange(HOUR, 20 * HOUR), side=side, setup=st, symbol=sym,
                           sn=dims(), origin=R.BACKFILL_CF, a15=rng.random() < 0.2, approx=rng.random() < 0.2,
                           amb=rng.random() < 0.2))
    # --- canlı turlar
    open_real: list[tuple] = []
    open_cf: list[tuple] = []
    emitted: list[dict[str, Any]] = []
    for k in range(n_batches):
        tk = T0_MS + k * STEP
        if clock_steps and k >= 2 and rng.random() < 0.2:
            tk -= rng.randrange(STEP, 2 * HOUR)            # duvar saati geri adımı: damgalar geride, satırlar SONRA
        rec = iso_us(tk, rng.randrange(1, 999_999))
        batch: list[dict[str, Any]] = []
        lo, hi = tk - 9 * MIN, tk + 9 * MIN                # olaylar: bu turun penceresi (< T_{k+1})
        # yeni gerçek girişler (bazıları ertelenmiş taslak: as_of saatler önce; bazıları > 72 sa → GEÇ)
        for j in range(rng.randrange(0, 4)):
            bk, st = book()
            side = side_of(bk)
            sym = rng.choice(_SYMS)
            u = rng.random()
            as_of = rng.randrange(lo, hi) if u < 0.8 else (tk - rng.randrange(2 * HOUR, 30 * HOUR) if u < 0.93
                                                            else tk - rng.randrange(73 * HOUR, 90 * HOUR))
            tid = "L%02d%02d" % (k, j)
            batch.append(entry(bk, tid, as_of, rec=rec, side=side, setup=st, symbol=sym,
                               sn=dims() if rng.random() < 0.9 else None, status="OK" if rng.random() < 0.9 else "GAP"))
            open_real.append((bk, tid, as_of, side, st, sym, 0))
        # yeni karşı-olgusallar (bazıları rev 0'da zaten etiketli)
        for j in range(rng.randrange(1, 5)):
            bk, st = book()
            side = side_of(bk)
            sym = rng.choice(_SYMS)
            as_of = rng.randrange(lo, hi)
            cid = "c%02d%02d" % (k, j)
            if rng.random() < 0.2:
                lab = min(as_of + rng.randrange(0, 5 * MIN), hi)
                batch.append(cf(bk, cid, as_of, rec=rec, status=R.LABELLED, r=rng.choice(rs), labeled_ms=lab,
                                side=side, setup=st, symbol=sym, sn=dims(), a15=rng.random() < 0.2))
            else:
                batch.append(cf(bk, cid, as_of, rec=rec, side=side, setup=st, symbol=sym, sn=dims(),
                                a15=rng.random() < 0.2))
                open_cf.append((bk, cid, as_of, side, st, sym, 0))
        # gerçek kapanışlar: geç kesinleşme (rev 0 final=False, sonra rev 1), saat eşitliği (kapanış = başka as_of)
        rng.shuffle(open_real)
        for _ in range(min(len(open_real), rng.randrange(0, 3))):
            bk, tid, as_of, side, st, sym, rev = open_real.pop()
            ca = max(as_of + 1000, rng.randrange(lo, hi))
            if ca >= tk + 10 * MIN:
                ca = tk + 9 * MIN
            r = rng.choice(rs)
            if rng.random() < 0.3:
                batch.append(outcome(bk, tid, as_of, ca, r, rec=rec, rev=rev, final=False, side=side, setup=st,
                                     symbol=sym))
                open_real.append((bk, tid, as_of, side, st, sym, rev + 1))   # sonraki turda kesinleşir (farklı değer)
            else:
                batch.append(outcome(bk, tid, as_of, ca, r, rec=rec, rev=rev, final=True, side=side, setup=st,
                                     symbol=sym))
        # karşı-olgusal revizyonları: v3 etiket, v1 etiket (sonra v3), kaybolma
        rng.shuffle(open_cf)
        keep: list[tuple] = []
        for item in open_cf:
            bk, cid, as_of, side, st, sym, rev = item
            u = rng.random()
            if u < 0.25:
                lab = _lab_in(rng, as_of, lo, hi)
                batch.append(cf(bk, cid, as_of, rec=rec, rev=rev + 1, status=R.LABELLED, r=rng.choice(rs),
                                labeled_ms=lab, side=side, setup=st, symbol=sym, approx=rng.random() < 0.3,
                                amb=rng.random() < 0.3))
            elif u < 0.35:
                lab = _lab_in(rng, as_of, lo, hi)
                batch.append(cf(bk, cid, as_of, rec=rec, rev=rev + 1, status=R.LABELLED, r=rng.choice(rs),
                                labeled_ms=lab, lv=rng.choice(("cf_label_v1", "cf_label_v2")), side=side, setup=st,
                                symbol=sym))
                keep.append((bk, cid, as_of, side, st, sym, rev + 1))   # tembel v3 yeniden etiketi sonra gelebilir
            elif u < 0.42:
                batch.append(cf(bk, cid, as_of, rec=rec, rev=rev + 1, status=rng.choice((R.DROPPED, R.SUPERSEDED)),
                                side=side, setup=st, symbol=sym))
            else:
                keep.append(item)
        open_cf = keep
        # tekrarlar / yeniden eşitleme: daha önce yazılmış bir satırın aynen yeniden yayını
        if emitted and rng.random() < 0.4:
            d = dict(rng.choice(emitted))
            d["recorded_at"] = rec
            batch.append(d)
        rng.shuffle(batch)
        emitted.extend(batch)
        rows.extend(batch)
    return rows


def _lab_in(rng: random.Random, as_of: int, lo: int, hi: int) -> int:
    """Etiket anı bu turun penceresinde, `as_of`tan önce DEĞİL; saat geri adımında pencere `as_of`un gerisinde kalırsa
    damga `as_of`tan önce düşer (üretimdeki gibi saat anomalisi: dışarıda kalır ve sayılır)."""
    lo2 = max(as_of, lo)
    return rng.randrange(lo2, hi) if lo2 < hi else rng.randrange(lo, hi)


# ============================================================================ kaba kuvvet kâhini
def _key(row: dict[str, Any]) -> tuple[str, str] | None:
    k = row.get("kind")
    if row.get("schema") != R.ROW_SCHEMA or k not in R.KINDS or row.get("book") not in R.BOOKS:
        return None
    key = row.get("cf_key") if k == R.KIND_CF else row.get("trade_key")
    if not key:
        return None
    return ("C" if k == R.KIND_CF else "R"), str(key)


def _qualifies(row: dict[str, Any]) -> str | None:
    """"V" nitelikli değer, "X" kaybolma, None aday değil — tanım (§3.2)."""
    k = row.get("kind")
    if k == R.KIND_OUTCOME:
        r = row.get("r_net")
        ok = (row.get("final") is True and row.get("in_net_stats") is True and row.get("r_basis") == R.NET
              and isinstance(r, (int, float)) and not isinstance(r, bool) and math.isfinite(r)
              and A.ms_exact(row.get("closed_at")) is not None)
        return "V" if ok else None
    if k == R.KIND_CF:
        if row.get("status") in R.VANISHED_STATUSES:
            return "X"
        r = row.get("r_net")
        ok = (row.get("status") == R.LABELLED and row.get("r_basis") == R.NET and row.get("in_net_stats") is True
              and row.get("label_version") == "cf_label_v3" and isinstance(r, (int, float))
              and not isinstance(r, bool) and math.isfinite(r) and A.ms_exact(row.get("labeled_at")) is not None)
        return "V" if ok else None
    return None


def _sit(row: dict[str, Any]) -> dict[str, str] | None:
    sn = row.get("snapshot")
    if (row.get("snapshot_status") == "OK" and isinstance(sn, dict) and sn.get("schema_id") == S.SCHEMA_ID
            and sn.get("schema_sha") == S.SCHEMA_SHA):
        b = S.bucket(sn, row.get("side"))
        return {d: b[d] for d in XR.DIMS}
    return None


def oracle(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{hedef anahtarı: {"state", "real", "cf"}} — tanımsal hesap (kümülatif durum YOK)."""
    bs = batches(rows)
    clock: int | None = None
    rec_of: list[int] = []
    advances: list[bool] = []
    ctx: dict[tuple, tuple[int, dict]] = {}
    first_ev: dict[tuple, tuple[int, dict, str]] = {}
    for bi, b in enumerate(bs):
        ms = A.ms_exact(b[0].get("recorded_at"))
        advances.append(clock is None or (ms is not None and ms > clock))
        clock = (ms if ms is not None else 0) if clock is None else (max(clock, ms) if ms is not None else clock)
        rec_of.append(clock)
        for r in b:
            k = _key(r)
            if k is None:
                continue
            is_ctx = r["kind"] == R.KIND_ENTRY or (r["kind"] == R.KIND_CF and r.get("rev") == 0)
            if is_ctx and k not in ctx:
                ctx[k] = (bi, r)
        for r in b:
            k = _key(r)
            if k is None or r["kind"] == R.KIND_ENTRY:
                continue
            q = _qualifies(r)
            if q is not None and k not in first_ev:
                first_ev[k] = (bi, r, q)
    # saati ilerletmeyen toplu yazımda katlanan kanıt, saati ilerleten SONRAKİ toplu yazımda bilinir (o saatle)
    nxt_adv: list[int | None] = [None] * len(bs)
    nx: int | None = None
    for bi in range(len(bs) - 1, -1, -1):
        nxt_adv[bi] = bi if advances[bi] else nx
        if advances[bi]:
            nx = bi
    # kanıt
    ev: list[dict[str, Any]] = []
    for k, (vb, vr, q) in first_ev.items():
        if q != "V" or k not in ctx:
            continue
        cb, cr = ctx[k]
        ch = 1 if k[0] == "C" else 0
        as_of = A.ms_exact(cr.get("created_at") if ch else cr.get("opened_at"))
        dims = _sit(cr)
        if as_of is None or dims is None or (ch == 0 and not cr.get("setup_key")):
            continue
        event = A.ms_exact(vr.get("labeled_at") if ch else vr.get("closed_at"))
        if event < as_of:
            continue                                          # saat anomalisi
        avail = max(rec_of[vb], rec_of[cb], event)          # yazım anı = MONOTON toplu yazım saati (2026-09-30)
        known = nxt_adv[max(vb, cb)]                         # katlandığı (ya da bekleyip bırakıldığı) toplu yazım
        if known is None:
            continue
        avail = max(avail, rec_of[known])
        out = vr.get("outcome") if isinstance(vr.get("outcome"), dict) else {}
        ev.append({"key": k, "ch": ch, "avail": avail, "known": known, "day": as_of // DAY,
                   "r": Fraction(A.r_micro(vr["r_net"])),
                   "setup": cr.get("setup_key"), "family": cr.get("family_key"), "dims": dims,
                   "coin": XR.coin_of(cr.get("symbol")) or "", "live": cr.get("origin") == R.LIVE,
                   "a15": ch == 1 and cr.get("baseline_blocked") is True,
                   "approx": ch == 1 and (vr.get("approx") is True or out.get("approx") is True),
                   "amb": ch == 1 and vr.get("intrabar_ambiguous") is True})
    res: dict[str, dict[str, Any]] = {}
    for k, (cb, cr) in ctx.items():
        ch_t = 1 if k[0] == "C" else 0
        as_of = A.ms_exact(cr.get("created_at") if ch_t else cr.get("opened_at"))
        clk = rec_of[cb]
        if as_of is None or as_of < clk - A.TAIL_H_MS:
            res[k[1]] = {"state": "LATE"}
            continue
        if not cr.get("setup_key") and not cr.get("family_key"):
            res[k[1]] = {"state": "NO_GROUP"}
            continue
        dims = _sit(cr)
        docs = {}
        for ch, name in ((0, "real"), (1, "cf")):
            vis = [e for e in ev if e["ch"] == ch and e["avail"] < as_of and e["known"] <= cb and e["key"] != k]
            docs[name] = _oracle_channel(vis, cr, dims, ch)
        res[k[1]] = {"state": "OK", **docs}
    return res


def _oracle_channel(vis: list[dict], cr: dict, dims: dict | None, ch: int) -> dict[str, Any]:
    levels = A.levels_for(dims, setup_key=cr.get("setup_key"), family_key=cr.get("family_key"))
    coin = XR.coin_of(cr.get("symbol")) or ""
    lv_out: list[list] = []
    for name, grp, kept, dropped in levels:
        gk = cr.get("setup_key") if grp == "setup" else cr.get("family_key")
        cell = [e for e in vis if (e["setup"] if grp == "setup" else e["family"]) == gk
                and all(e["dims"][d] == v for d, v in kept.items())]
        n = len(cell)
        days: dict[int, list[Fraction]] = {}
        for e in cell:
            days.setdefault(e["day"], []).append(e["r"])
        c = len(days)
        s = sum((e["r"] for e in cell), Fraction(0))
        lv_out.append([name, n, c, A._r6(float(s / n) / 1e6) if n >= 10 else None])
        if n >= 30 and c >= 5:
            mean_f = s / n
            dsum = sum(((sum(v, Fraction(0)) - mean_f * len(v)) ** 2 for v in days.values()), Fraction(0))
            d_int = dsum * n * n
            assert d_int.denominator == 1
            d_int = int(d_int)
            mean = float(Fraction(int(s), n)) / 1e6
            se = math.sqrt(float(Fraction(c * d_int, (c - 1) * n ** 4))) / 1e6
            t = A.t975(c - 1)
            lo, hi = mean - t * se, mean + t * se
            lab = "GIRME" if hi < 0 else ("GIR" if lo > 0 else "NOTR")
            coin_cell = [e for e in cell if e["coin"] == coin]
            n_c = len(coin_cell)
            s_c = int(sum((e["r"] for e in coin_cell), Fraction(0)))
            sh = float(Fraction(s_c * n + 20 * int(s), n * (n_c + 20))) / 1e6
            num = s_c * n + 20 * int(s)
            live = sum(1 for e in cell if e["live"])
            return {"advice": lab, "level": name, "n": n, "clusters": c, "mean_r": A._r6(mean),
                    "sum_r": A._r6(float(s) / 1e6), "se_r": A._r6(se), "ci95": [A._r6(lo), A._r6(hi)],
                    "coin": {"coin": coin, "n": n_c, "mean_r": A._r6(float(Fraction(s_c, n_c)) / 1e6) if n_c else None,
                             "shrunk_mean_r": A._r6(sh), "k": 20,
                             "dissent": bool(n_c >= 10 and ((num > 0) - (num < 0)) != ((s > 0) - (s < 0)))},
                    "origin_mix": {"live": live, "backfill": n - live},
                    "cf_flags": ({"a15": sum(e["a15"] for e in cell), "approx": sum(e["approx"] for e in cell),
                                  "ambiguous": sum(e["amb"] for e in cell)} if ch == 1 else None),
                    "levels": lv_out, "visible_evidence": len(vis), "dims_used": kept, "dropped": list(dropped),
                    "group_key": gk}
    return {"advice": "VERI_AZ", "level": None, "n": None, "clusters": None, "mean_r": None, "sum_r": None,
            "se_r": None, "ci95": None, "coin": None, "origin_mix": None, "cf_flags": None, "levels": lv_out,
            "visible_evidence": len(vis), "dims_used": None, "dropped": None, "group_key": None}


ORACLE_FIELDS = ("advice", "level", "n", "clusters", "mean_r", "sum_r", "se_r", "ci95", "coin", "origin_mix", "cf_flags",
                 "levels", "visible_evidence", "dims_used", "dropped", "group_key")


def channel_view(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if doc is None else {k: doc.get(k) for k in ORACLE_FIELDS}


# ============================================================================ elle kurulan kanıt
def ev_real(book: str, tid: str, as_of: int, r: float, *, rec: str, sn: dict | None | str = "default",
            side: str = "LONG", setup: str | None = "trend", symbol: str = "SOL/USDT:USDT", origin: str = "LIVE",
            close_after: int = HOUR) -> list[dict[str, Any]]:
    """Kapanmış gerçek işlem kanıtı: giriş + kesin net sonuç (aynı toplu yazım)."""
    return [entry(book, tid, as_of, rec=rec, side=side, setup=setup, symbol=symbol, sn=sn, origin=origin),
            outcome(book, tid, as_of, as_of + close_after, r, rec=rec, side=side, setup=setup, symbol=symbol,
                    origin=origin)]


def ev_cf(book: str, cid: str, as_of: int, r: float, *, rec: str, sn: dict | None | str = "default", side: str = "LONG",
          setup: str | None = "trend", symbol: str = "SOL/USDT:USDT", origin: str = "LIVE", label_after: int = HOUR,
          a15: bool = False, approx: bool | None = None, amb: bool | None = None) -> list[dict[str, Any]]:
    """Etiketli (cf_label_v3, NET) karşı-olgusal kanıtı: rev 0 zaten LABELLED (bağlam + değer tek satır)."""
    return [cf(book, cid, as_of, rec=rec, status=R.LABELLED, r=r, labeled_ms=as_of + label_after, side=side, setup=setup,
               symbol=symbol, sn=sn, origin=origin, a15=a15, approx=approx, amb=amb)]


def cell_rows(values_by_day: dict[int, list[float]], *, rec: str, book: str = "strategy_paper_box",
              setup: str = "box_fade", side: str = "LONG", sn: dict | None | str = "default", kind: str = "real",
              prefix: str = "E", base_ms: int | None = None, symbol: str = "SOL/USDT:USDT") -> list[dict[str, Any]]:
    """{gün kaydırması: [R, …]} → kanıt satırları (gün = `base − gün·DAY`, gün içinde saat saat)."""
    base = (T0_MS // DAY) * DAY - 20 * DAY if base_ms is None else base_ms
    out: list[dict[str, Any]] = []
    i = 0
    for d, vals in sorted(values_by_day.items()):
        for j, v in enumerate(vals):
            as_of = base + d * DAY + (j + 1) * 600_000
            tag = "%s%d_%d" % (prefix, d, j)
            i += 1
            if kind == "real":
                out += ev_real(book, tag, as_of, v, rec=rec, sn=sn, side=side, setup=setup, symbol=symbol)
            else:
                out += ev_cf(book, tag, as_of, v, rec=rec, sn=sn, side=side, setup=setup, symbol=symbol)
    return out


def target_row(book: str = "strategy_paper_box", tid: str = "TGT", *, as_of: int, rec: str, kind: str = "real",
               setup: str | None = "box_fade", side: str = "LONG", sn: dict | None | str = "default",
               symbol: str = "SOL/USDT:USDT", status: str | None = "OK") -> dict[str, Any]:
    if kind == "real":
        return entry(book, tid, as_of, rec=rec, side=side, setup=setup, symbol=symbol, sn=sn, status=status)
    return cf(book, tid, as_of, rec=rec, side=side, setup=setup, symbol=symbol, sn=sn, sst=status)


GOLDEN_A = {1: [-1, -1, -1, -1, 0.5, -0.2], 2: [-1, -1, -1, 2.0, -1, -0.4], 3: [-1, -1, -1, -1, -1, 1.5],
            4: [-1, 0.8, -1, -1, -0.3, -1], 5: [-1] * 6}
GOLDEN_B = {1: [-1, 2.5, -1, 0.4, 1.2, -1], 2: [1.8, -1, -1, 0.3, 2.2, -1], 3: [-1, -1, 3.0, 0.1, -0.5, 1.0],
            4: [0.6, -1, -1, 1.4, 2.0, -0.2], 5: [-1, 1.1, 0.9, -1, -1, 0.7]}

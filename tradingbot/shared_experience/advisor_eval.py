# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — ÇEVRİMDIŞI DEĞERLENDİRME (walk-forward) (2026-09-29). SALT OKUR; ayrı süreç, ağ YOK.

Soru: "GİRME dediklerini atlasaydık net R artar mıydı?" — önceden kayıtlı protokol (`WF_SPEC`, mühür `WF_SHA`;
docs/ortak_deneyim/DANISMAN_V1.md §4). Bu modül KARAR VERMEZ: bakışlar arasındaki her sayı yalnız TANIMLAYICIDIR;
başarı yalnız L1/L2/L3 bakışlarında ve yalnız bir ÖNERİ yazmaya izin verir (uygulama ayrı mod + kullanıcı onayı ister).

* Katlama canlıyla AYNI koddur (`advisor.AdvisorFold`): ana depo arşiv segmentleri → sıcak dosya, `recorded_at`
  koşuları = toplu yazımlar. Çözülen hedef dizileri hedef başına ~20 bayttır (bir yılda ~10 MB).
* Birincil uç nokta (H1): uygun ileriye dönük CANLI gerçek hedeflerde, birincil (gerçek kanıt) tavsiyeyle
  `U_mean = ort(T \\ G) − ort(T)`; korumalar `U_sum100 = −Σ_G y / |T| · 100` ve `Δ = ort(GİR) − ort(GİRME)`.
  Gün-kümeli bootstrap (B = 10 000, tohum 20261001, PCG64), yüzdelik `sıralı[⌊α/2·B⌋]`, `sıralı[⌊(1−α/2)·B⌋]`.
* H2 (ikincil): olsaydı kanalının tavsiyesi GERÇEK hedeflerde, aynı ölçüt.
* `verify`: katlamanın çekirdekleri ile tavsiye deposundaki `core_sha`lar hedef başına karşılaştırılır.
* `ask`: "ortak hafıza şu an ne derdi?" — deponun tamamı katlanır, `as_of = şimdi` ile sorulur.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import numpy as np

from ..core import iso
from . import report as XR
from . import rows as R
from .advice_store import ADVICE_DIR, ADVICE_HOT_FILE
from .advisor import (ADVICE_SCHEMA, ADVISOR_ID, ADVISOR_SHA, BASE_LEVELS, BOOK_KEYS, CH_CF, CH_REAL, COHORT_CODES,
                      DAY_MS, GIR, GIRME, LABEL_TR, LABELS, LEVEL_NAMES, NOTR, RF_ANOM, RF_LIVE, RF_OK, RF_PROSP,
                      VERI_AZ, AdvisorFold, FoldResult, key_hash, ms_exact)

# ============================================================================ protokol (MÜHÜRLÜ)
WF_ID = "walkforward_v1"
REPORT_SCHEMA = "shared_experience_advisor_report_v1"
SUMMARY_SCHEMA = "shared_experience_advisor_summary_v1"
VERIFY_SCHEMA = "shared_experience_advisor_verify_v1"
ASK_SCHEMA = "shared_experience_advisor_ask_v1"
BOOT_B = 10_000
BOOT_SEED = 20261001
BOOT_CHUNK_BYTES = 4 * 1024 * 1024
ALPHA_DESCRIPTIVE = 0.05
LOOK_OFFSETS_DAYS = (0, 28, 56)
LOOK_ALPHAS = (0.01, 0.01, 0.03)
LOOK_FIRST_DAYS = 28
LOOK_SETTLE_DAYS = 7
LOOK_DEADLINE_WEEKS = 16
MIN_N_T = 500
MIN_N_G = 50
MIN_G_DAYS = 10
MIN_T_DAYS = 28
HALF_SPLIT = 0.5
BASE_WARN_SHARE = 0.8
DELTA_UNDEFINED_MAX = 0.05
AGREEMENT_MIN = 0.995
COHORTS = ("ALL_REAL", "POLICY", "LEARNING_EXTRA", "PRE", "CF")
ELIGIBILITY = ("prospective", "live", "all")
#: Sabit şeritler (panel yerel kopyası test eşitliğine bağlı).
BANNER_TR = ("yalnız KAYIT — karar değişmez; ara bakış kanıt değildir; başarı yalnız önceden kayıtlı bakışlarda "
             "(L1/L2/L3)")
RETRO_BANNER_TR = "RETRO — keşif; kanıt değil; kural değiştirilemez"
VERDICTS = ("NOT_REACHED", "PENDING", "FAIL")

WF_SPEC: dict[str, Any] = {
    "wf_id": WF_ID, "advisor_id": ADVISOR_ID,
    "eligibility": {"primary": "origin LIVE, prospective (batch_clock >= advisor_born_ms), state OK, no clock_anomaly, "
                               "own outcome qualified (real: first final NET; cf: first cf_label_v3 NET), folded by the "
                               "look's settle time", "outcome": "y = r_net of the qualifying revision"},
    "cohorts": {"ALL_REAL": "every real target", "POLICY": "scorecard_class policy (incl. untagged-after)",
                "LEARNING_EXTRA": "scorecard_class learning_extra", "PRE": "scorecard_class before",
                "CF": "every counterfactual target"},
    "groupings": ["all", "book", "family_key", "level class SITU (S0-S4, F0-F4) vs BASE (S5, F5)"],
    "channels": {"advice": "real evidence (primary, H1 on ALL_REAL)", "advice_cf": "cf evidence (H2 on ALL_REAL)"},
    "metrics": {"U_mean": "mean(T minus G) - mean(T); T minus G empty -> -mean(T); G empty -> 0",
                "U_sum100": "-sum_G y / |T| * 100; G empty -> 0", "delta": "mean(GIR) - mean(GIRME); undefined if empty",
                "share_G": "|G| / |T|", "base_warning": "share of GIRME answered at S5/F5 > 0.8"},
    "bootstrap": {"cluster": "UTC day of target as_of, ascending", "B": BOOT_B, "seed": BOOT_SEED, "rng": "numpy PCG64",
                  "draw": "rng.integers(0, D, size=(B, D)) in chunks (<= 4 MB)",
                  "ci": "sorted[floor(alpha/2*B)], sorted[floor((1-alpha/2)*B)]",
                  "delta_undefined": "dropped; > 5% of B -> CI null"},
    "looks": {"L1": "first UTC midnight >= born + 28 d where minimums hold (outcome-blind)", "L2": "L1 + 28 d",
              "L3": "L1 + 56 d", "alpha": list(LOOK_ALPHAS), "settle_days": LOOK_SETTLE_DAYS,
              "data": "targets with as_of < cutoff whose outcome was folded by cutoff + 7 d; computed at cutoff + 7 d",
              "not_reached": "L1 not reached by born + 16 weeks",
              "dates": "L1 is found once on the primary channel's minimums (H1); H2 is evaluated on the SAME dates",
              "minimums": {"N_T": MIN_N_T, "N_G": MIN_N_G, "G_days": MIN_G_DAYS, "T_days": MIN_T_DAYS,
                           "labels": "GIRME counted on the evaluated channel (H1: advice, H2: advice_cf)"},
              "integrity": "per look: eligible targets with as_of < cutoff advised in a batch with batch_clock <= "
                           "cutoff + 7 d, against the first stored advice row per row_id recorded_at <= cutoff + 7 d",
              "integrity_unknown": "integrity not computed, or invariants neither declared green nor red -> PENDING "
                                   "(never PASS, never a terminal FAIL); only an explicit red declaration, agreement "
                                   "< 0.995 or an unexplained mismatch fails the integrity condition"},
    "pass": ["minimums hold on the look's data", "lower bound of the (1-alpha_k) CI of U_mean > 0",
             "U_mean > 0 in both halves (split at the first day where cumulative targets >= 50%)",
             "U_sum100 > 0 and delta > 0 (point)",
             "live-offline core_sha agreement >= 0.995 on eligible rows, 0 unexplained mismatches, invariants green"],
    "stop": "first passing look -> PASS(Lk); none by L3 -> FAIL; evaluation then closed",
    "pass_allows": "a PAPER-only skip-GIRME proposal; never enforcement; GIR never forces an entry or a size",
}
WF_SHA: str = hashlib.sha256(json.dumps(WF_SPEC, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                             .encode("ascii")).hexdigest()[:16]

_LI = {lab: i for i, lab in enumerate(LABELS)}
_G = _LI[GIRME]
_GIR = _LI[GIR]


# ============================================================================ okuma / katlama
def iter_batches(root: Path | str, stats: XR.ReadStats | None = None) -> Iterator[list[dict[str, Any]]]:
    """Ana deponun toplu yazımları (aynı `recorded_at`li ardışık satırlar), yazım sırasıyla — akış (tek segment)."""
    batch: list[dict[str, Any]] = []
    cur: Any = object()
    for row in XR.iter_rows(root, stats=stats):
        ra = row.get("recorded_at")
        if batch and ra != cur:
            yield batch
            batch = []
        batch.append(row)
        cur = ra
    if batch:
        yield batch


def iter_advice(root: Path | str) -> Iterator[dict[str, Any]]:
    """Tavsiye deposu satırları (arşiv → sıcak dosya; salt okur; tekilleştirme YOK)."""
    adv = Path(root) / ADVICE_DIR
    st = XR.ReadStats()
    for seg in XR._segments(XR.manifest(adv)):
        name = str(seg.get("file") or "")
        if not name or "/" in name or "\\" in name or name.startswith("."):
            continue
        try:
            blob = (adv / XR.ARCHIVE_DIR / XR.SEGMENTS_DIR / name).read_bytes()
            if hashlib.sha256(blob).hexdigest() != str(seg.get("sha256") or ""):
                continue
            data = gzip.decompress(blob)
        except (OSError, EOFError, ValueError):
            continue
        yield from XR._parse_lines(data, st)
    try:
        data = (adv / ADVICE_HOT_FILE).read_bytes()
    except OSError:
        return
    yield from XR._parse_lines(data, st)


def load_born(root: Path | str) -> int | None:
    """`advice/advisor_meta.json` (bu mühür); yoksa tavsiye deposundaki ilk dolu `advisor_born_ms`."""
    from .advisor_live import META_FILE
    doc = XR._read_json_ro(Path(root) / ADVICE_DIR / META_FILE, max_bytes=1 << 16)
    ent = ((doc or {}).get("advisors") or {}).get(ADVISOR_SHA) if isinstance(doc, dict) else None
    if isinstance(ent, dict) and isinstance(ent.get("advisor_born_ms"), int):
        return int(ent["advisor_born_ms"])
    for r in iter_advice(root):
        if r.get("advisor_sha") == ADVISOR_SHA and isinstance(r.get("advisor_born_ms"), int):
            return int(r["advisor_born_ms"])
    return None


def fold_store(root: Path | str, *, born_ms: int | None, collect: bool = True, clock: str = "avail",
               on_batch: Callable[[FoldResult], None] | None = None,
               stats: XR.ReadStats | None = None) -> AdvisorFold:
    """Deponun TAMAMINI tek akışla katlar (canlıyla aynı kod)."""
    fold = AdvisorFold(born_ms=born_ms, collect=collect, clock=clock)
    for batch in iter_batches(root, stats=stats):
        res = fold.fold_batch(batch)
        if on_batch is not None:
            on_batch(res)
    return fold


# ============================================================================ nokta ölçüleri
def point_metrics(n: list[int] | np.ndarray, s: list[int] | np.ndarray) -> dict[str, Any]:
    """Etiket başına (n, Σy µR) → U_mean, U_sum100, Δ, pay (TAM sayılardan)."""
    n = [int(x) for x in n]
    s = [int(x) for x in s]
    n_t, s_t = sum(n), sum(s)
    n_g, s_g = n[_G], s[_G]
    out: dict[str, Any] = {"n_T": n_t, "n_G": n_g, "mean_T": _r6(s_t / n_t / 1e6) if n_t else None,
                           "labels": {lab: {"n": n[i], "mean": _r6(s[i] / n[i] / 1e6) if n[i] else None}
                                      for i, lab in enumerate(LABELS)}}
    if not n_t:
        out.update({"U_mean": None, "U_sum100": None, "delta": None, "share_G": None})
        return out
    if n_g == 0:
        u = 0.0
    elif n_t == n_g:
        u = -s_t / n_t / 1e6
    else:
        u = ((s_t - s_g) / (n_t - n_g) - s_t / n_t) / 1e6
    out["U_mean"] = _r6(u)
    out["U_sum100"] = _r6(0.0 if n_g == 0 else -s_g / n_t * 100 / 1e6)
    out["delta"] = _r6(s[_GIR] / n[_GIR] / 1e6 - s_g / n_g / 1e6) if (n[_GIR] and n_g) else None
    out["share_G"] = _r6(n_g / n_t)
    return out


def running_from_tallies(tallies: Mapping[str, Any]) -> dict[str, Any]:
    """Koşan sayaçlar → defter başına (gerçek hedefler, birincil kanal) N_T, N_G, U_mean, U_sum100, Δ + TOPLAM."""
    per: dict[str, list[list[int]]] = {}
    for k, v in (tallies or {}).items():
        try:
            book, coh, chn, lab = str(k).split("|")
            nn, ss = int(v[0]), int(v[1])
        except (ValueError, TypeError, IndexError):
            continue
        if chn != CH_REAL or coh == "cf" or lab not in _LI:
            continue
        for key in (book, "ALL"):
            acc = per.setdefault(key, [[0] * 4, [0] * 4])
            acc[0][_LI[lab]] += nn
            acc[1][_LI[lab]] += ss
    out: dict[str, Any] = {}
    for key, (n, s) in sorted(per.items()):
        pm = point_metrics(n, s)
        out[key] = {"N_T": pm["n_T"], "N_G": pm["n_G"], "U_mean": pm["U_mean"], "U_sum100": pm["U_sum100"],
                    "delta": pm["delta"], "labels": {lab: pm["labels"][lab]["n"] for lab in LABELS}}
    return out


def _r6(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return round(v, 6) + 0.0 if math.isfinite(v) else None


# ============================================================================ bootstrap
def bootstrap(day: np.ndarray, lab: np.ndarray, y: np.ndarray, *, alpha: float = ALPHA_DESCRIPTIVE, b: int = BOOT_B,
              seed: int = BOOT_SEED, chunk_bytes: int = BOOT_CHUNK_BYTES) -> dict[str, Any]:
    """Gün-kümeli bootstrap: U_mean, U_sum100, Δ için (1−α) yüzdelik aralığı. Günler artan sırada (kanonik)."""
    if day.size == 0:
        return {"alpha": alpha, "B": b, "seed": seed, "U_mean": None, "U_sum100": None, "delta": None,
                "delta_dropped": None, "days": 0}
    days, inv = np.unique(day, return_inverse=True)
    d = int(days.size)
    n_mat = np.zeros((d, 4), dtype=np.int64)
    s_mat = np.zeros((d, 4), dtype=np.int64)
    np.add.at(n_mat, (inv, lab), 1)
    np.add.at(s_mat, (inv, lab), y.astype(np.int64))
    rng = np.random.default_rng(seed)
    per = max(1, int(chunk_bytes) // (24 * d))
    um, us, de = [], [], []
    left = b
    while left > 0:
        k = min(per, left)
        idx = rng.integers(0, d, size=(k, d))
        flat = idx + (np.arange(k, dtype=np.int64)[:, None] * d)
        cnt = np.bincount(flat.ravel(), minlength=k * d).reshape(k, d)
        nn = cnt @ n_mat
        ss = (cnt @ s_mat).astype(np.float64)
        nt = nn.sum(1).astype(np.float64)
        st = ss.sum(1)
        ng = nn[:, _G].astype(np.float64)
        sg = ss[:, _G]
        ngr = nn[:, _GIR].astype(np.float64)
        sgr = ss[:, _GIR]
        with np.errstate(divide="ignore", invalid="ignore"):
            rest = np.where(nt > ng, (st - sg) / np.where(nt > ng, nt - ng, 1.0), 0.0)
            u = np.where(ng == 0, 0.0, np.where(nt == ng, -st / nt, rest - st / nt)) / 1e6
            usum = np.where(ng == 0, 0.0, -sg / nt * 100.0) / 1e6
            ok = (ngr > 0) & (ng > 0)
            dl = (sgr[ok] / ngr[ok] - sg[ok] / ng[ok]) / 1e6
        um.append(u)
        us.append(usum)
        de.append(dl)
        left -= k
    um_a, us_a, de_a = np.sort(np.concatenate(um)), np.sort(np.concatenate(us)), np.sort(np.concatenate(de))

    def pct(a: np.ndarray) -> list[float] | None:
        m = int(a.size)
        if m == 0:
            return None
        lo = min(m - 1, int(math.floor(alpha / 2 * m)))
        hi = min(m - 1, int(math.floor((1 - alpha / 2) * m)))
        return [_r6(a[lo]), _r6(a[hi])]
    dropped = b - int(de_a.size)
    return {"alpha": alpha, "B": b, "seed": seed, "days": d, "U_mean": pct(um_a), "U_sum100": pct(us_a),
            "delta": pct(de_a) if dropped <= DELTA_UNDEFINED_MAX * b else None, "delta_dropped": dropped}


def halves(day: np.ndarray, lab: np.ndarray, y: np.ndarray) -> dict[str, Any] | None:
    """Hedef günleri, birikimli hedef sayısının %50'ye ulaştığı İLK günde ikiye bölünür; iki yarının nokta ölçüleri."""
    if day.size == 0:
        return None
    days, cnt = np.unique(day, return_counts=True)
    cum = np.cumsum(cnt)
    k = int(np.argmax(cum >= HALF_SPLIT * day.size))
    split = int(days[k])
    out: dict[str, Any] = {"split_day": split, "split_date": XR._iso_ms(split * DAY_MS)}
    for name, m in (("h1", day <= split), ("h2", day > split)):
        pm = _pm(lab[m], y[m])
        out[name] = {"n_T": pm["n_T"], "U_mean": pm["U_mean"], "U_sum100": pm["U_sum100"], "delta": pm["delta"]}
    return out


def _pm(lab: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    n = np.bincount(lab, minlength=4)[:4] if lab.size else np.zeros(4, np.int64)
    s = [int(y[lab == i].sum()) for i in range(4)]
    return point_metrics(list(n), s)


def metrics(day: np.ndarray, lab: np.ndarray, y: np.ndarray, lvl: np.ndarray | None = None, *,
            alpha: float = ALPHA_DESCRIPTIVE, boot: bool = True) -> dict[str, Any]:
    """Bir hücre (T): nokta ölçüleri + gün sayıları + (isteğe bağlı) bootstrap aralığı + yarılar + temel seviye uyarısı."""
    pm = _pm(lab, y)
    pm["days"] = int(np.unique(day).size) if day.size else 0
    for i, labn in enumerate(LABELS):
        pm["labels"][labn]["days"] = int(np.unique(day[lab == i]).size) if day.size else 0
    if lvl is not None and pm["n_G"]:
        g = lab == _G
        base = np.isin(lvl[g], [LEVEL_NAMES.index(x) for x in BASE_LEVELS])
        share = float(base.mean()) if base.size else 0.0
        pm["base_share_G"] = _r6(share)
        pm["base_warning"] = bool(share > BASE_WARN_SHARE)
    pm["ci"] = bootstrap(day, lab, y, alpha=alpha) if (boot and day.size) else None
    pm["halves"] = halves(day, lab, y)
    return pm


# ============================================================================ seçim
def _cohort_mask(a: Mapping[str, np.ndarray], cohort: str) -> np.ndarray:
    kind, coh = a["kind"], a["coh"]
    if cohort == "ALL_REAL":
        return kind == 0
    if cohort == "CF":
        return kind == 1
    code = {"POLICY": R.SC_POLICY, "LEARNING_EXTRA": R.SC_LEARNING_EXTRA, "PRE": R.SC_BEFORE}[cohort]
    return (kind == 0) & (coh == COHORT_CODES.index(code))


def eligible_mask(a: Mapping[str, np.ndarray], eligibility: str) -> np.ndarray:
    fl = a["flags"].astype(np.int64)
    ok = (fl & RF_OK) > 0
    no_anom = (fl & RF_ANOM) == 0
    if eligibility == "prospective":
        return ok & no_anom & ((fl & RF_LIVE) > 0) & ((fl & RF_PROSP) > 0)
    if eligibility == "live":
        return ok & no_anom & ((fl & RF_LIVE) > 0)
    if eligibility == "all":
        return ok & no_anom
    raise ValueError("eligibility prospective|live|all")


def _block(a: Mapping[str, np.ndarray], m: np.ndarray, chn: str, *, alpha: float, boot: bool,
           groups: Mapping[str, Callable[[], dict[str, np.ndarray]]] | None = None) -> dict[str, Any]:
    labk, lvlk = ("lab_r", "lvl_r") if chn == CH_REAL else ("lab_c", "lvl_c")
    mm = m & (a[labk] >= 0)
    lab = a[labk][mm].astype(np.int64)
    out = metrics(a["day"][mm], lab, a["y"][mm], a[lvlk][mm], alpha=alpha, boot=boot)
    base_codes = [LEVEL_NAMES.index(x) for x in BASE_LEVELS]
    lv = a[lvlk]
    split: dict[str, Any] = {}
    for name, sel in (("SITU", (lv >= 0) & ~np.isin(lv, base_codes)), ("BASE", np.isin(lv, base_codes))):
        k = mm & sel
        split[name] = metrics(a["day"][k], a[labk][k].astype(np.int64), a["y"][k], alpha=alpha, boot=boot)
    out["by_level"] = split
    for gname, fn in (groups or {}).items():
        sub: dict[str, Any] = {}
        for key, sel in fn().items():
            k = mm & sel
            if k.any():
                sub[key] = metrics(a["day"][k], a[labk][k].astype(np.int64), a["y"][k], alpha=alpha, boot=boot)
        out[gname] = sub
    return out


# ============================================================================ bakışlar
def _look_data(a: Mapping[str, np.ndarray], base: np.ndarray, cutoff_ms: int) -> np.ndarray:
    return base & (a["as_of"] < cutoff_ms) & (a["res_clock"] <= cutoff_ms + LOOK_SETTLE_DAYS * DAY_MS)


def minimums(a: Mapping[str, np.ndarray], m: np.ndarray, labk: str = "lab_r") -> dict[str, Any]:
    """Asgariler (sonuca KÖR: yalnız hedef ve etiket sayar, y'ye ASLA bakmaz). GİRME sayıları DEĞERLENDİRİLEN kanalın
    etiketlerinden (`labk`: H1 `lab_r`, H2 `lab_c`; 2026-09-30 inceleme bulgusu — H2 eskiden birincil kanalın GİRME'lerini
    sayıyordu). L1 TARİHİ yalnız birincil kanaldan bulunur (`find_l1`); H2 aynı tarihlerde değerlendirilir."""
    lab = a[labk][m]
    day = a["day"][m]
    g = lab == _G
    out = {"N_T": int(m.sum()), "N_G": int(g.sum()), "G_days": int(np.unique(day[g]).size),
           "T_days": int(np.unique(day).size)}
    out["ok"] = bool(out["N_T"] >= MIN_N_T and out["N_G"] >= MIN_N_G and out["G_days"] >= MIN_G_DAYS
                     and out["T_days"] >= MIN_T_DAYS)
    return out


def _midnight_ceil(ms: int) -> int:
    return -(-int(ms) // DAY_MS) * DAY_MS


def find_l1(a: Mapping[str, np.ndarray], base: np.ndarray, born_ms: int, now_ms: int) -> tuple[int | None, str]:
    """L1 tarihi (sonuca KÖR). Dönüş: (L1 ms | None, durum: FOUND | PENDING | NOT_REACHED)."""
    c = _midnight_ceil(born_ms + LOOK_FIRST_DAYS * DAY_MS)
    deadline = born_ms + LOOK_DEADLINE_WEEKS * 7 * DAY_MS
    while c <= deadline:
        if now_ms < c + LOOK_SETTLE_DAYS * DAY_MS:
            return None, "PENDING"
        if minimums(a, _look_data(a, base, c))["ok"]:
            return c, "FOUND"
        c += DAY_MS
    return None, "NOT_REACHED"


def evaluate_look(a: Mapping[str, np.ndarray], base: np.ndarray, cutoff_ms: int, alpha: float, *,
                  channel: str = CH_REAL, integrity: Mapping[str, Any] | None = None, b: int = BOOT_B) -> dict[str, Any]:
    """Bir bakışın bütün koşulları (her biri ayrı raporlanır). Bütünlük bilinmiyorsa (None) sonuç en iyi PENDING."""
    m = _look_data(a, base, cutoff_ms)
    labk = "lab_r" if channel == CH_REAL else "lab_c"
    mm = m & (a[labk] >= 0)
    day, lab, y = a["day"][mm], a[labk][mm].astype(np.int64), a["y"][mm]
    mins = minimums(a, m, labk)
    pm = _pm(lab, y)
    ci = bootstrap(day, lab, y, alpha=alpha, b=b)
    hv = halves(day, lab, y)
    cond = {"minimums": mins["ok"],
            "ci_lower_gt_0": bool(ci["U_mean"] is not None and ci["U_mean"][0] > 0),
            "halves_positive": bool(hv is not None and (hv["h1"]["U_mean"] or 0) > 0 and (hv["h2"]["U_mean"] or 0) > 0),
            "u_sum_positive": bool(pm["U_sum100"] is not None and pm["U_sum100"] > 0),
            "delta_positive": bool(pm["delta"] is not None and pm["delta"] > 0)}
    integ, why = integrity_verdict(integrity)
    stat_ok = all(cond.values())
    status = "FAIL" if not stat_ok else ("PASS" if integ else ("FAIL" if integ is False else "PENDING"))
    return {"cutoff_ms": cutoff_ms, "cutoff": XR._iso_ms(cutoff_ms), "alpha": alpha, "channel": channel,
            "minimums": mins, "point": {k: pm[k] for k in ("n_T", "n_G", "U_mean", "U_sum100", "delta", "share_G")},
            "ci_U_mean": ci["U_mean"], "halves": hv, "conditions": cond, "integrity_ok": integ,
            "integrity_reason": why, "integrity": _integrity_brief(integrity), "status": status}


def integrity_verdict(integrity: Mapping[str, Any] | None) -> tuple[bool | None, str | None]:
    """(tamam mı, neden). None → bilinmiyor (PENDING): bütünlük hesaplanmadı (`INTEGRITY_UNKNOWN`) ya da operatör
    değişmezleri ne yeşil ne kırmızı beyan etti (`INVARIANTS_UNDECLARED`; CLI varsayılanı — 2026-09-30 inceleme bulgusu:
    eskiden bu, istatistik geçse bile terminal FAIL veriyordu). False yalnız açık kırmızı beyan, uyum < 0,995 ya da
    açıklanamayan uyumsuzlukta."""
    if integrity is None:
        return None, "INTEGRITY_UNKNOWN"
    agr = integrity.get("agreement_eligible")
    if agr is None or agr < AGREEMENT_MIN:
        return False, "AGREEMENT_BELOW_MIN"
    if int(integrity.get("unexplained") or 0) != 0:
        return False, "UNEXPLAINED_MISMATCH"
    inv = integrity.get("invariants_green")
    if inv is False:
        return False, "INVARIANTS_RED"
    if inv is True:
        return True, None
    return None, "INVARIANTS_UNDECLARED"


def _integrity_brief(integrity: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if integrity is None:
        return None
    return {k: integrity.get(k) for k in ("window_cutoff_ms", "eligible_targets", "eligible_in_store",
                                          "agreement_eligible", "unexplained", "missing_eligible", "invariants_green")}


def looks(a: Mapping[str, np.ndarray], base: np.ndarray, born_ms: int | None, now_ms: int, *,
          channel: str = CH_REAL, integrity: Mapping[str, Any] | None = None,
          integrity_at: Callable[[int], Mapping[str, Any] | None] | None = None, b: int = BOOT_B) -> dict[str, Any]:
    """L1/L2/L3 → NOT_REACHED | PENDING | PASS(Lk) | FAIL. `integrity_at(kesim)` (2026-09-30): bakışın KENDİ penceresinin
    bütünlüğü (verilirse `integrity`nin yerine) — bir bakışın hükmü sonradan yeniden çalıştırılınca değişmez."""
    if born_ms is None:
        return {"verdict": "PENDING", "reason": "NO_BORN", "L1": None, "looks": []}
    l1, why = find_l1(a, base, born_ms, now_ms)
    if l1 is None:
        return {"verdict": why if why == "NOT_REACHED" else "PENDING", "reason": why, "L1": None, "looks": []}
    out: list[dict[str, Any]] = []
    for k, (off, alpha) in enumerate(zip(LOOK_OFFSETS_DAYS, LOOK_ALPHAS), start=1):
        c = l1 + off * DAY_MS
        if now_ms < c + LOOK_SETTLE_DAYS * DAY_MS:
            out.append({"look": "L%d" % k, "cutoff": XR._iso_ms(c), "status": "NOT_YET"})
            return {"verdict": "PENDING", "reason": "L%d_NOT_YET" % k, "L1": XR._iso_ms(l1), "looks": out}
        integ_k = integrity_at(c) if integrity_at is not None else integrity
        ev = evaluate_look(a, base, c, alpha, channel=channel, integrity=integ_k, b=b)
        ev["look"] = "L%d" % k
        out.append(ev)
        if ev["status"] == "PASS":
            return {"verdict": "PASS(L%d)" % k, "reason": "PASS", "L1": XR._iso_ms(l1), "looks": out}
        if ev["status"] == "PENDING":
            return {"verdict": "PENDING", "reason": ev.get("integrity_reason") or "INTEGRITY_UNKNOWN",
                    "L1": XR._iso_ms(l1), "looks": out}
    return {"verdict": "FAIL", "reason": "NO_LOOK_PASSED", "L1": XR._iso_ms(l1), "looks": out}


# ============================================================================ doğrulama (canlı ↔ çevrimdışı)
class _CoreCollector:
    """Katlamanın tavsiye çekirdeklerini kompakt dizilerde toplar (hedef başına ~33 bayt): kimlik, çekirdek, bayraklar,
    hedefin `as_of`'u ve tavsiye edildiği toplu yazım saati (bakış penceresi bütünlüğü için; 2026-09-30)."""

    def __init__(self) -> None:
        self.rid: list[int] = []
        self.core: list[int] = []
        self.flags: list[int] = []
        self.as_of: list[int] = []
        self.clock: list[int] = []

    def __call__(self, res: FoldResult) -> None:
        for r in res.advice_rows:
            t = r.get("target") or {}
            elig = (r.get("state") == "OK" and r.get("prospective") and not r.get("clock_anomaly")
                    and t.get("origin") == R.LIVE)
            self.rid.append(int(r["row_id"], 16))
            self.core.append(int(r["core_sha"], 16))
            self.flags.append((1 if elig else 0) | (2 if r.get("clock_anomaly") else 0))
            ao = t.get("as_of_ms")
            self.as_of.append(int(ao) if isinstance(ao, int) and not isinstance(ao, bool) else -1)
            bc = r.get("batch_clock_ms")
            self.clock.append(int(bc) if isinstance(bc, int) and not isinstance(bc, bool) else _NEVER_MS)

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rid, core, flags, _a, _c = self.all_arrays()
        return rid, core, flags

    def all_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        rid = np.array(self.rid, dtype=np.uint64)
        order = np.argsort(rid, kind="stable")
        return (rid[order], np.array(self.core, dtype=np.uint64)[order], np.array(self.flags, dtype=np.int8)[order],
                np.array(self.as_of, dtype=np.int64)[order], np.array(self.clock, dtype=np.int64)[order])


#: Okunamayan / eksik saat: hiçbir pencereye girmez.
_NEVER_MS = (1 << 62)


class _StoreScan:
    """Tavsiye deposunun TEK geçişi: katlama hedefi başına İLK saklanan kopyanın çekirdeği, yazım anı (ms) ve anomali
    bayrağı; hedef dışı / başka mühür / tekrar satırlarının yazım anları (pencereye göre sayım)."""

    def __init__(self, root: Path | str, cc: _CoreCollector) -> None:
        self.rid, self.core, self.flags, self.as_of, self.clock = cc.all_arrays()
        n = self.rid.size
        self.seen = np.zeros(n, dtype=bool)
        self.s_core = np.zeros(n, dtype=np.uint64)
        self.s_rec = np.full(n, _NEVER_MS, dtype=np.int64)
        self.s_anom = np.zeros(n, dtype=bool)
        self.extra_rec: list[int] = []
        self.other_rec: list[int] = []
        self.dup_rec: list[int] = []
        rid = self.rid
        for r in iter_advice(root):
            if r.get("schema") != ADVICE_SCHEMA:
                continue
            rec = ms_exact(r.get("recorded_at"))
            rec = int(rec) if rec is not None else _NEVER_MS
            if r.get("advisor_sha") != ADVISOR_SHA:
                self.other_rec.append(rec)
                continue
            try:
                k = np.uint64(int(str(r.get("row_id")), 16))
                c = int(str(r.get("core_sha")), 16)
            except ValueError:
                self.extra_rec.append(rec)
                continue
            i = int(np.searchsorted(rid, k))
            if i >= rid.size or rid[i] != k:
                self.extra_rec.append(rec)
                continue
            if self.seen[i]:
                self.dup_rec.append(rec)
                continue
            self.seen[i] = True
            self.s_core[i] = np.uint64(c)
            self.s_rec[i] = rec
            self.s_anom[i] = bool(r.get("clock_anomaly"))

    def integrity(self, cutoff_ms: int | None = None) -> dict[str, Any]:
        """Uyum ölçüleri. `cutoff_ms` verilirse bakış penceresi: `as_of < kesim` ve `toplu yazım saati ≤ kesim + 7 g`
        olan hedefler, `recorded_at ≤ kesim + 7 g` olan saklı kopyalarla (bakış o gün hesaplanır)."""
        if cutoff_ms is None:
            sel = np.ones(self.rid.size, dtype=bool)
            lim = None
        else:
            lim = int(cutoff_ms) + LOOK_SETTLE_DAYS * DAY_MS
            sel = (self.as_of >= 0) & (self.as_of < int(cutoff_ms)) & (self.clock <= lim)
        stored = self.seen & sel if lim is None else (self.seen & sel & (self.s_rec <= lim))
        el = sel & ((self.flags & 1) > 0)
        match = stored & (self.s_core == self.core)
        mism = stored & ~match
        expl = mism & (self.s_anom | ((self.flags & 2) > 0))
        n_el = int(el.sum())
        n_st = int(stored.sum())
        agree = int(match.sum())

        def cnt(xs: list[int]) -> int:
            return len(xs) if lim is None else sum(1 for x in xs if x <= lim)
        out = {"fold_targets": int(sel.sum()), "stored_matched": n_st, "agree": agree, "mismatch": int(mism.sum()),
               "mismatch_explained": int(expl.sum()), "unexplained": int(mism.sum()) - int(expl.sum()),
               "missing_in_store": int((sel & ~stored).sum()), "missing_eligible": int((el & ~stored).sum()),
               "extra_in_store": cnt(self.extra_rec), "other_advisor_sha": cnt(self.other_rec),
               "duplicates_ignored": cnt(self.dup_rec), "agreement": _r6(agree / n_st) if n_st else None,
               "eligible_targets": n_el, "eligible_in_store": int((stored & el).sum()),
               "agreement_eligible": _r6(int((match & el).sum()) / n_el) if n_el else None}
        if cutoff_ms is not None:
            out["window_cutoff_ms"] = int(cutoff_ms)
            out["window_settle_ms"] = lim
        return out


def compare_cores(root: Path | str, cc: _CoreCollector, *, cutoff_ms: int | None = None) -> dict[str, Any]:
    """Katlamanın çekirdekleri ↔ tavsiye deposu (satır kimliği başına İLK kopya). `cutoff_ms`: bir bakışın penceresi
    (bkz. `_StoreScan.integrity`); None → deponun tamamı (yalnız tanımlayıcı / `verify`)."""
    return _StoreScan(root, cc).integrity(cutoff_ms)


def verify(root: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Deponun katlanmış çekirdekleri ile tavsiye deposundaki `core_sha`lar (hedef başına)."""
    now = now or datetime.now(timezone.utc)
    born = load_born(root)
    cc = _CoreCollector()
    rs = XR.ReadStats()
    fold = fold_store(root, born_ms=born, collect=False, on_batch=cc, stats=rs)
    cmp_ = compare_cores(root, cc)
    st = XR._read_json_ro(Path(root) / ADVICE_DIR / "advisor_status.json", max_bytes=1 << 20) or {}
    c = st.get("counters") if isinstance(st.get("counters"), dict) else {}
    return {"schema": VERIFY_SCHEMA, "banner": BANNER_TR, "generated_at": iso(now), "advisor_sha": ADVISOR_SHA,
            "advisor_born_ms": born, "source": rs.as_dict(), **cmp_,
            "live_status": {"state": st.get("state"), "advice_dropped": c.get("advice_dropped"),
                            "degraded_steps": c.get("degraded_steps"), "breaker": (st.get("breaker") or {}).get("tripped")},
            "fold": fold.stats()}


# ============================================================================ walk-forward
def _event_sweep(fold: AdvisorFold) -> dict[int, tuple[int, int, int, int]]:
    """Olay saati (keşif) ikinci geçişi: kanıt `avail_event` sırasıyla, hedef `as_of` sırasıyla (eşitlikte hedef ÖNCE)."""
    ev = sorted(fold.deferred_evidence, key=lambda x: x[0])
    tg = sorted(fold.deferred_targets, key=lambda x: x[0])
    agg = AdvisorFold(born_ms=None)
    agg._gk_list, agg._gk = fold._gk_list, fold._gk
    agg._dv_list, agg._dv = fold._dv_list, fold._dv
    agg._coin_list, agg._coin = fold._coin_list, fold._coin
    out: dict[int, tuple[int, int, int, int]] = {}
    i = 0
    for as_of, kh, sit, setup, fam, dcodes, coin_id, coin in tg:
        while i < len(ev) and ev[i][0] < as_of:
            avail, ch, ekh, es, ef, ed, day, ecoin, r, fl = ev[i]
            agg._ingest(ch, avail, ekh, agg._evidence_cells(ch, es, ef, ed), day, ecoin, r, fl, tail=False)
            i += 1
        docs = [agg._channel(ch, agg._target_levels(ch, bool(sit), setup, fam, dcodes), coin_id, coin, as_of, kh,
                             tail=False) for ch in (0, 1)]
        out[kh] = tuple([_LI[d["advice"]] for d in docs] + [LEVEL_NAMES.index(d["level"]) if d["level"] else -1
                                                            for d in docs])
    return out


def resolved_arrays(fold: AdvisorFold) -> dict[str, np.ndarray]:
    return fold.resolved_arrays()


def run_walkforward(root: Path | str, *, eligibility: str = "prospective", since: Any = None, until: Any = None,
                    with_looks: bool = False, clock: str = "avail", book: str | None = None, family: str | None = None,
                    cohort: str = "all", now: datetime | None = None, boot: bool = True,
                    invariants_green: bool | None = None, b: int = BOOT_B) -> dict[str, Any]:
    """Tek akış katlama → çözülen hedefler → kohort × gruplama × kanal ölçüleri (+ isteğe bağlı bakışlar)."""
    if eligibility not in ELIGIBILITY:
        raise XR.ReportError("--eligible prospective|live|all")
    if clock not in ("avail", "event"):
        raise XR.ReportError("--clock avail|event")
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    root = Path(root)
    since_ms, until_ms = XR.parse_when(since), XR.parse_when(until)
    born = load_born(root)
    rs = XR.ReadStats()
    cc = _CoreCollector() if (with_looks and clock == "avail") else None
    fold = fold_store(root, born_ms=born, collect=True, clock=clock, on_batch=cc, stats=rs)
    a = fold.resolved_arrays()
    if clock == "event":
        labs = _event_sweep(fold)
        kh = a["kh"]
        for j in range(kh.size):
            v = labs.get(int(kh[j]))
            if v is not None:
                a["lab_r"][j], a["lab_c"][j], a["lvl_r"][j], a["lvl_c"][j] = v
        eligibility = "all"
    n = a["day"].size
    base = eligible_mask(a, eligibility) if n else np.zeros(0, dtype=bool)
    if since_ms is not None:
        base &= a["as_of"] >= since_ms
    if until_ms is not None:
        base &= a["as_of"] < until_ms
    if book:
        bk = book if book in BOOK_KEYS else R.book_for_name(book)
        if bk not in BOOK_KEYS:
            raise XR.ReportError("bilinmeyen defter: %r" % book)
        base &= a["book"] == BOOK_KEYS.index(bk)
    gk = fold.lookup_tables()["group_keys"]
    fam_str = np.array([gk[i] if 0 <= i < len(gk) else "" for i in a["fam"]], dtype=object) if n else np.zeros(0, object)
    if family:
        base &= np.array([str(x).split("|")[0] == family.upper() for x in fam_str], dtype=bool) if n else base
    cohorts = list(COHORTS) if cohort == "all" else [{"policy": "POLICY", "extra": "LEARNING_EXTRA", "pre": "PRE",
                                                      "cf": "CF"}[cohort]]

    def by_book() -> dict[str, np.ndarray]:
        return {bk: a["book"] == i for i, bk in enumerate(BOOK_KEYS)}

    def by_family() -> dict[str, np.ndarray]:
        return {str(f) or "—": fam_str == f for f in sorted(set(fam_str.tolist()))}
    matrix: dict[str, Any] = {}
    for coh in cohorts:
        m = base & _cohort_mask(a, coh) if n else base
        matrix[coh] = {"advice": _block(a, m, CH_REAL, alpha=ALPHA_DESCRIPTIVE, boot=boot,
                                        groups={"by_book": by_book, "by_family": by_family}),
                       "advice_cf": _block(a, m, CH_CF, alpha=ALPHA_DESCRIPTIVE, boot=boot,
                                           groups={"by_book": by_book, "by_family": by_family})}
    doc: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "wf_id": WF_ID, "wf_sha": WF_SHA, "advisor_id": ADVISOR_ID, "advisor_sha": ADVISOR_SHA,
        "banner": BANNER_TR, "generated_at": iso(now), "root": str(root), "clock": clock, "eligibility": eligibility,
        "window": {"since": XR._iso_ms(since_ms), "until": XR._iso_ms(until_ms)},
        "filters": {"book": book, "family": family, "cohort": cohort}, "advisor_born_ms": born,
        "advisor_born_at": XR._iso_ms(born), "source": rs.as_dict(), "fold": fold.stats(),
        "counters": dict(fold.counters), "resolved_targets": int(n), "eligible_targets": int(base.sum()) if n else 0,
        "matrix": matrix}
    if clock == "event":
        doc["retro_banner"] = RETRO_BANNER_TR
    if with_looks and clock == "avail":
        scan = _StoreScan(root, cc) if cc is not None else None
        integ = scan.integrity() if scan is not None else None
        if integ is not None:
            integ["invariants_green"] = invariants_green

        def integ_at(cutoff_ms: int) -> dict[str, Any] | None:
            # (2026-09-30, inceleme bulgusu) her bakış KENDİ penceresinin bütünlüğüyle: deponun sonraki hâli (ör. L1+7
            # günden sonraki bir kesinti) geçmiş bir bakışın hükmünü DEĞİŞTİRMEZ
            if scan is None:
                return None
            d = scan.integrity(cutoff_ms)
            d["invariants_green"] = invariants_green
            return d
        prim = (eligible_mask(a, "prospective") & (a["kind"] == 0)) if n else np.zeros(0, bool)
        doc["integrity"] = integ
        doc["looks"] = {"H1": looks(a, prim, born, now_ms, channel=CH_REAL, integrity_at=integ_at, b=b),
                        "H2": looks(a, prim, born, now_ms, channel=CH_CF, integrity_at=integ_at, b=b)}
    return doc


# ============================================================================ "şu an ne derdi?"
def ask(root: Path | str, *, book: str, setup: str, side: str, symbol: str, live: bool = False,
        csv_dir: Path | str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Deponun tamamını katlar ve `as_of = şimdi` ile sorar (yalnız bilgi; karar DEĞİL)."""
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    root = Path(root)
    bk = book if book in BOOK_KEYS else R.book_for_name(book)
    if bk not in BOOK_KEYS:
        raise XR.ReportError("bilinmeyen defter: %r" % book)
    sd = str(side or "").upper()
    if sd not in ("LONG", "SHORT"):
        raise XR.ReportError("--side LONG|SHORT")
    sk = R.setup_key(bk, setup, sd)
    fam = R.family_of(bk, setup)
    fk = R.family_key(fam, sd)
    notes: list[str] = []
    sit_doc: dict[str, Any] | None = None
    if live and csv_dir:
        snap = XR.live_snapshot(symbol, csv_dir=csv_dir, as_of_ms=now_ms)
        if snap is not None and snap.get("status") == "OK":
            sit_doc = XR.situation_from_snapshot(snap, source="live_csv")
        else:
            notes.append("canlı anlık görüntü OK değil (%s) — depodaki son anlık görüntü" % ((snap or {}).get("status")
                                                                                          or "YOK"))
    if sit_doc is None:
        o = XR.scan(root)
        sit_doc = XR.situation_for_symbol(o, symbol, snapshot="ok")
        del o
    born = load_born(root)
    fold = fold_store(root, born_ms=born, collect=False)
    dims = None
    if sit_doc is not None:
        # beş boyut yönden bağımsızdır (`situation.bucket`; yalnız `align` yöne bağlıdır ve kullanılmaz)
        dims = {d: str(sit_doc["dims"][d]) for d in XR.DIMS}
    else:
        notes.append("%s için OK anlık görüntü yok — yalnız durumsuz seviyeler (S5/F5)" % symbol)
    setup_id = fold._gk_id(sk)
    fam_id = fold._gk_id(fk)
    dcodes = tuple(fold._dim_code(i, dims[d]) for i, d in enumerate(XR.DIMS)) if dims else (0, 0, 0, 0, 0)
    coin = XR.coin_of(symbol)
    coin_id = fold._coin_id(symbol)
    ch_docs = {}
    for ch, name in ((0, CH_REAL), (1, CH_CF)):
        ch_docs[name] = fold._channel(ch, fold._target_levels(ch, dims is not None, setup_id, fam_id, dcodes), coin_id,
                                      coin, now_ms, key_hash(R.KIND_ENTRY, "ask|%s|%s" % (sk, symbol)))
    return {"schema": ASK_SCHEMA, "banner": BANNER_TR, "generated_at": iso(now), "advisor_sha": ADVISOR_SHA,
            "query": {"book": bk, "setup_type": setup, "side": sd, "symbol": symbol, "coin": coin, "setup_key": sk,
                      "family_key": fk, "as_of": iso(now), "as_of_ms": now_ms, "live": bool(live)},
            "situation": sit_doc, "dims": dims, "real": ch_docs[CH_REAL], "cf": ch_docs[CH_CF],
            "advice": ch_docs[CH_REAL]["advice"], "advice_tr": ch_docs[CH_REAL]["advice_tr"],
            "advice_cf": ch_docs[CH_CF]["advice"], "advice_cf_tr": ch_docs[CH_CF]["advice_tr"], "notes_tr": notes,
            "effect": "NONE", "fold": fold.stats()}


def replay(root: Path | str, out_path: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Yeniden hesaplanan tavsiye satırları → `out_path` (JSONL). Tavsiye deposuna ASLA yazılmaz."""
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    born = load_born(root)
    n = [0]
    out_path = Path(out_path)
    chunks: list[str] = []

    def emit(res: FoldResult) -> None:
        for r in res.advice_rows:
            r["recorded_at"] = stamp
            r["meta"] = {"computed_at": stamp, "lag_s": None, "fold_mode": "replay", "code_sha": None, "config_hash": None}
            chunks.append(json.dumps(r, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            n[0] += 1
    fold = fold_store(root, born_ms=born, collect=False, on_batch=emit)
    out_path.write_text("\n".join(chunks) + ("\n" if chunks else ""), encoding="utf-8")
    return {"schema": "shared_experience_advisor_replay_v1", "rows": n[0], "out": str(out_path), "fold": fold.stats(),
            "advisor_sha": ADVISOR_SHA, "banner": BANNER_TR}


# ============================================================================ özet / metin
def summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Panel kartının okuduğu küçük özet (`advice/walkforward_summary.json`)."""
    mat = (doc.get("matrix") or {}).get("ALL_REAL") or {}
    prim = mat.get("advice") or {}
    h2 = mat.get("advice_cf") or {}

    def slim(m: Mapping[str, Any]) -> dict[str, Any]:
        ci = m.get("ci") or {}
        return {"n_T": m.get("n_T"), "n_G": m.get("n_G"), "U_mean": m.get("U_mean"), "U_sum100": m.get("U_sum100"),
                "delta": m.get("delta"), "U_mean_ci": ci.get("U_mean"), "delta_ci": ci.get("delta"),
                "alpha": ci.get("alpha"), "days": m.get("days")}
    lk = doc.get("looks") or {}
    return {"schema": SUMMARY_SCHEMA, "wf_sha": doc.get("wf_sha"), "advisor_sha": doc.get("advisor_sha"),
            "banner": BANNER_TR, "generated_at": doc.get("generated_at"), "eligibility": doc.get("eligibility"),
            "clock": doc.get("clock"), "advisor_born_ms": doc.get("advisor_born_ms"),
            "primary": slim(prim), "h2": slim(h2),
            "by_book": {k: slim(v) for k, v in (prim.get("by_book") or {}).items()},
            "looks": {h: {"verdict": v.get("verdict"), "reason": v.get("reason"), "L1": v.get("L1"),
                          "looks": [{"look": x.get("look"), "status": x.get("status"), "cutoff": x.get("cutoff")}
                                    for x in (v.get("looks") or [])]} for h, v in lk.items()} or None}


def _n(x: Any, nd: int = 3) -> str:
    return XR._num(x, nd) if x is not None else "—"


def _ci(c: Any) -> str:
    return "[%s ; %s]" % (_n(c[0]), _n(c[1])) if isinstance(c, (list, tuple)) and len(c) == 2 else "—"


def _metric_line(label: str, m: Mapping[str, Any]) -> str:
    ci = m.get("ci") or {}
    labs = m.get("labels") or {}
    return ("%s: T=%s gün=%s · GİR %s (%s) · NÖTR %s (%s) · GİRME %s (%s) · VERİ AZ %s (%s) · U_ort %s %s · Δ %s %s · "
            "U_top100 %s" % (label, m.get("n_T"), m.get("days"),
                             (labs.get(GIR) or {}).get("n"), _n((labs.get(GIR) or {}).get("mean")),
                             (labs.get(NOTR) or {}).get("n"), _n((labs.get(NOTR) or {}).get("mean")),
                             (labs.get(GIRME) or {}).get("n"), _n((labs.get(GIRME) or {}).get("mean")),
                             (labs.get(VERI_AZ) or {}).get("n"), _n((labs.get(VERI_AZ) or {}).get("mean")),
                             _n(m.get("U_mean")), _ci(ci.get("U_mean")), _n(m.get("delta")), _ci(ci.get("delta")),
                             _n(m.get("U_sum100"), 2)))


def render_tr(doc: Mapping[str, Any]) -> str:
    """Türkçe metin (walk-forward / verify / ask belgeleri)."""
    lines: list[str] = ["GÖLGE DANIŞMAN %s — %s" % (ADVISOR_ID, doc.get("banner") or BANNER_TR)]
    if doc.get("retro_banner"):
        lines.append("⚠ " + str(doc["retro_banner"]))
    sch = doc.get("schema")
    if sch == ASK_SCHEMA:
        q = doc.get("query") or {}
        lines.append("Soru: %s %s %s (%s) — as_of %s" % (q.get("book"), q.get("setup_type"), q.get("side"),
                                                        q.get("symbol"), q.get("as_of")))
        lines.append("Durum: %s" % (" · ".join("%s=%s" % (k, v) for k, v in (doc.get("dims") or {}).items()) or "yok"))
        for name, tr in ((CH_REAL, "gerçek (birincil)"), (CH_CF, "olsaydı (ayrı kanal)")):
            d = doc.get(name) or {}
            lines.append("  %s: %s — seviye %s, n=%s, küme=%s, ort %s, aralık %s" % (
                tr, LABEL_TR.get(d.get("advice"), d.get("advice")), d.get("level") or "—", d.get("n"), d.get("clusters"),
                _n(d.get("mean_r")), _ci(d.get("ci95"))))
            for lv in d.get("levels") or []:
                lines.append("      %s: n=%s küme=%s ort=%s" % (lv[0], lv[1], lv[2], _n(lv[3])))
        for n_ in doc.get("notes_tr") or []:
            lines.append("  not: " + str(n_))
        lines.append("Bu cevap hiçbir kararı DEĞİŞTİRMEZ (effect NONE).")
        return "\n".join(lines)
    if sch == VERIFY_SCHEMA:
        lines.append("Doğrulama: katlama %s hedef · depoda eşleşen %s · uyum %s (uygun satırlarda %s) · uyumsuz %s "
                     "(açıklanan %s) · depoda eksik %s · fazla %s" % (
                         doc.get("fold_targets"), doc.get("stored_matched"), _n(doc.get("agreement"), 4),
                         _n(doc.get("agreement_eligible"), 4), doc.get("mismatch"), doc.get("mismatch_explained"),
                         doc.get("missing_in_store"), doc.get("extra_in_store")))
        return "\n".join(lines)
    lines.append("Protokol %s (WF_SHA %s) · danışman %s · saat %s · uygunluk %s · doğum %s" % (
        doc.get("wf_id"), doc.get("wf_sha"), doc.get("advisor_sha"), doc.get("clock"), doc.get("eligibility"),
        doc.get("advisor_born_at") or "yok (henüz canlı değil)"))
    lines.append("Çözülen hedef %s · uygun %s" % (doc.get("resolved_targets"), doc.get("eligible_targets")))
    for coh, chans in (doc.get("matrix") or {}).items():
        for chn, tr in (("advice", "birincil"), ("advice_cf", "olsaydı kanalı")):
            m = chans.get(chn) or {}
            lines.append(_metric_line("%s · %s" % (coh, tr), m))
            if m.get("base_warning"):
                lines.append("   ⚠ GİRME'lerin %%%s'i S5/F5'ten: kazanç durumdan değil kurulum düzeyinden (karne bilgisi)"
                             % _n(100 * (m.get("base_share_G") or 0), 0))
            for lvn, lm in (m.get("by_level") or {}).items():
                if lm.get("n_T"):
                    lines.append("   " + _metric_line(lvn, lm))
            if coh == "ALL_REAL" and chn == "advice":
                for bk, bm in (m.get("by_book") or {}).items():
                    lines.append("   " + _metric_line("defter %s" % bk, bm))
    for h, v in (doc.get("looks") or {}).items():
        lines.append("Bakış %s: %s (%s) L1=%s" % (h, v.get("verdict"), v.get("reason"), v.get("L1") or "—"))
        for x in v.get("looks") or []:
            why = x.get("integrity_reason")
            lines.append("   %s %s: %s%s" % (x.get("look"), x.get("cutoff"), x.get("status"),
                                            (" (bütünlük: %s)" % why) if why else ""))
    lines.append("Ara bakış KANIT DEĞİLDİR; başarı yalnız L1/L2/L3'te ve yalnız bir ÖNERİ yazmaya izin verir.")
    return "\n".join(lines)


__all__ = ["ALPHA_DESCRIPTIVE", "BANNER_TR", "BOOT_B", "BOOT_SEED", "COHORTS", "ELIGIBILITY", "LOOK_ALPHAS",
           "REPORT_SCHEMA", "RETRO_BANNER_TR", "SUMMARY_SCHEMA", "WF_ID", "WF_SHA", "WF_SPEC", "ask", "bootstrap",
           "compare_cores", "eligible_mask", "evaluate_look", "find_l1", "fold_store", "halves", "integrity_verdict",
           "iter_advice",
           "iter_batches", "load_born", "looks", "metrics", "minimums", "point_metrics", "render_tr", "replay",
           "run_walkforward", "running_from_tallies", "summary", "verify"]

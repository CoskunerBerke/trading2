# -*- coding: utf-8 -*-
"""MUM VARYASYONU LABORATUVAR ADAPTÖRÜ — `signal_lab`ın mum varyasyonlarını ölçtüğü TEK yol (2026-09-26).

`signal_lab` bu modülü YALNIZ varyasyon istendiğinde (tembel) içe aktarır; varsayılan laboratuvar yolu buraya girmez.

* Gerçek olaylar: her i barı için o barda biten AYNI `WINDOW` (=500) barlık pencere tam serinin numpy GÖRÜNÜMÜNDEN kurulur
  ve canlı defterin çağırdığı `candle_dsl.detect_last` çağrılır (parite tanım gereği birebir, EMA200 dahil). Pencerede
  `window_from_rows`un reddedeceği bir bar varsa (zaman boşluğu, okunamaz OHLC, hacim yok) o pencere değerlendirilmez —
  canlı defter de o pencerede sinyal üretmez. `atr_sig[i]` = isabetin ATR14'ü (pencere üzerinde): `simulate`ın risk
  sınırları, canlı `apply_action`ın gördüğü ATR ile denetlenir.
* Eşleştirilmiş plasebo `PLACEBO_<id>`: barların `PLACEBO_P` (%10; sabit, AYARLANMAZ) kadarı yalnız zaman damgasından
  seçilir (`signal_lab._h`; seri uzunluğuna ve geleceğe bağlı değil). Aynı yön, aynı bağlam + prior_move
  (`placebo_context`), AYNI RİSK/ATR DAĞILIMI, aynı çıkış ve risk sınırları. Mum şekli stop mesafesini de belirler (boğa
  gövdesi kapanışı dipten uzaklaştırır, kırılım barı formasyon tepesinin üstünde kapanır); stop kuralını son n bara
  uygulamak plaseboya daha dar stop, dolayısıyla R cinsinden daha yüksek maliyet ve farklı sürüklenme verirdi. Bu yüzden
  plasebonun stop'u `c[j] ∓ q × ATR14(j)`dir: q (risk/ATR) ve kırılım teyidinde gecikme (i - p; prior_move'un çapası)
  j'den ÖNCEKİ gerçek isabetlerden birinin değerleridir, seçim yine zaman damgasından (belirlenimci, geleceğe bakmaz).
  j'den önce gerçek isabet yoksa plasebo atlanır (`PLACEBO_<id>:NO_REAL_RISK`). Hüküm tek soruyu cevaplar: mum ŞEKLİ,
  bağlamının ve risk/çıkış geometrisinin ötesinde R katıyor mu?
* Simülasyon: `vcfg` — varyasyonun kendi hedef R'si, en uzun tutma süresi ve risk sınırlarıyla `LabConfig`.
* Kayıt: `build_record` → `<out>/variation_records/<ID>.json`; GitHub çıktısından `candle_lab_records/`e BAYT BAYT
  kopyalanır. Çalıştırma bilgisi GITHUB_* ortam değişkenlerinden (yerel çalıştırmada `github_run_id` None → kapı reddeder).
  `golden_sha` dedektörün anlamını mühürler: DSL_VERSION artırılmadan anlam değişirse CI yakalar.

Geçmiş test; kâr garantisi değildir.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import platform
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from . import candle_dsl as D
from . import signal_lab as L
from .timeframes import tf_ms

RECORD_SCHEMA = "candle_lab/1"
#: Laboratuvar olay ailesi (gerçek varyasyon olayları).
FAMILY = "candle_var"
PLACEBO_PREFIX = "PLACEBO_"
#: Plasebo seçim olasılığı (bar başına). Sabit; sonuca göre AYARLANMAZ.
PLACEBO_P = 0.10
#: `run` çıktısında kayıtların klasörü.
RECORDS_SUBDIR = "variation_records"
#: Kayıttaki keşif amaçlı bağlam dilimi sayısı (doğrulama ort. R'ye göre en iyiler).
TOP_SLICES = 5
#: Altın özet derlemi: 3 tohumlu rastgele yürüyüş × 1200 4h bar (hacimli), her 7. barda biten pencere.
GOLDEN_SEEDS = (20260926, 20260927, 20260928)
GOLDEN_BARS = 1200
GOLDEN_EVERY = 7
_GOLDEN_T0 = 1_600_000_000_000 // 14_400_000 * 14_400_000
_GOLDEN_CACHE: list[dict[str, np.ndarray]] = []


def placebo_name(vid: str) -> str:
    return PLACEBO_PREFIX + vid


# ---------------------------------------------------------------------------- pencereler
def _arrays(df: pd.DataFrame) -> dict[str, np.ndarray]:
    return {"ts": df["timestamp"].to_numpy(dtype=np.int64),
            "o": np.ascontiguousarray(df["open"].to_numpy(dtype=float)),
            "h": np.ascontiguousarray(df["high"].to_numpy(dtype=float)),
            "l": np.ascontiguousarray(df["low"].to_numpy(dtype=float)),
            "c": np.ascontiguousarray(df["close"].to_numpy(dtype=float)),
            "v": np.ascontiguousarray(df["volume"].to_numpy(dtype=float))}


def valid_ends(a: Mapping[str, np.ndarray], step_ms: int) -> np.ndarray:
    """ok[i]: i barında biten pencere `window_from_rows`un bütün denetimlerinden geçer (ardışık zaman damgası, sonlu
    OHLC, h >= max(o,c), l <= min(o,c), sonlu ve negatif olmayan hacim) — canlı defterle aynı fail-closed kural."""
    ts, o, h, lo, c, v = (a[k] for k in ("ts", "o", "h", "l", "c", "v"))
    n, w = len(ts), D.WINDOW
    ok = np.zeros(n, dtype=bool)
    if n < w:
        return ok
    with np.errstate(invalid="ignore"):
        bad = (~(np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)) | (h < np.maximum(o, c))
               | (lo > np.minimum(o, c)) | ~np.isfinite(v) | (v < 0))
    gap = np.zeros(n, dtype=bool)
    gap[1:] = np.diff(ts) != int(step_ms)
    cb = np.concatenate(([0], np.cumsum(bad)))
    cg = np.concatenate(([0], np.cumsum(gap)))
    end = np.arange(w - 1, n)
    ok[w - 1:] = ((cb[end + 1] - cb[end - w + 1]) == 0) & ((cg[end + 1] - cg[end - w + 2]) == 0)
    return ok


def window_at(a: Mapping[str, np.ndarray], i: int, step_ms: int) -> D.Window:
    """i barında biten pencere: tam serinin GÖRÜNÜMÜ (kopya yok; i'den sonrası okunmaz)."""
    s = slice(i - D.WINDOW + 1, i + 1)
    return D.Window(ts=a["ts"][s], o=a["o"][s], h=a["h"][s], l=a["l"][s], c=a["c"][s], v=a["v"][s], step_ms=int(step_ms))


# ---------------------------------------------------------------------------- olaylar
def variation_events(df: pd.DataFrame, symbol: str, tf: str, var: D.Variation,
                     skipped: dict[str, int] | None = None) -> tuple[list[L.Event], np.ndarray]:
    """(olaylar, atr_sig). Olaylar: gerçek isabetler (`candle_var`, ad = id) + eşleştirilmiş plasebo (`placebo`,
    `PLACEBO_<id>`). `atr_sig` tam serinin ATR14'ünün kopyasıdır; olay barlarında pencere ATR14'ü ile değiştirilir.
    `skipped` verilirse kurulamayan plasebolar `PLACEBO_<id>:<neden>` anahtarıyla sayılır."""
    step = tf_ms(tf)
    a = _arrays(df)
    n = len(a["ts"])
    atr_sig = np.array(L.indicators(df)["atr"], dtype=float, copy=True) if n else np.zeros(0, dtype=float)
    out: list[L.Event] = []
    if n < D.WINDOW:
        return out, atr_sig
    s = 1.0 if var.side == D.LONG else -1.0
    ends = [int(i) for i in np.flatnonzero(valid_ends(a, step))]
    geo: list[tuple[int, float, int]] = []                  # gerçek isabetler: (i, q = risk/ATR, gecikme = i - p)
    for i in ends:
        hit = D.detect_last(window_at(a, i, step), var)
        if hit is None:
            continue
        out.append(L.Event(symbol, tf, FAMILY, var.id, var.side, i, int(hit.signal_close_ms), float(hit.stop),
                           trigger=None, target=None))
        atr_sig[i] = hit.atr_i
        geo.append((i, s * (float(hit.close) - float(hit.stop)) / float(hit.atr_i), i - int(hit.p)))
    pname = placebo_name(var.id)
    c, last = a["c"], D.WINDOW - 1

    def skip(why: str) -> None:
        if skipped is not None:
            key = "%s:%s" % (pname, why)
            skipped[key] = skipped.get(key, 0) + 1
    k = 0                                                   # geo[:k] = j'den ÖNCEKİ gerçek isabetler (önek → geleceğe bakmaz)
    for j in ends:
        t_j = int(a["ts"][j])
        if L._h(symbol, tf, pname, t_j) >= PLACEBO_P:
            continue
        while k < len(geo) and geo[k][0] < j:
            k += 1
        if k == 0:
            skip("NO_REAL_RISK")                             # j'den önce gerçek isabet yok: risk geometrisi bilinmiyor
            continue
        _i, q, lag = geo[int(L._h(symbol, tf, pname, "q", t_j) * k)]   # j'den önceki gerçek isabetlerden biri
        good, a_j = D.placebo_context(window_at(a, j, step), var, p=last - lag)
        if not good:
            continue
        stop = float(c[j]) - s * q * a_j
        if not (math.isfinite(stop) and stop > 0 and s * (float(c[j]) - stop) > 0):
            skip("BAD_STOP")
            continue
        out.append(L.Event(symbol, tf, "placebo", pname, var.side, j, t_j + step, stop, trigger=None, target=None))
        atr_sig[j] = a_j
    return out, atr_sig


def vcfg(cfg: L.LabConfig, var: D.Variation) -> L.LabConfig:
    """Varyasyonun kendi çıkışı: hedef = target_r × risk (None → hedef yok), `max_hold_bars` sonunda kapanış, risk
    sınırları = `risk_atr_bounds` (canlı `apply_action`ın `RISK_OUTSIDE_TESTED_RANGE` sınırlarıyla aynı)."""
    lo, hi = var.risk_atr_bounds
    return dataclasses.replace(cfg, default_rr=float(var.target_r) if var.target_r is not None else float("inf"),
                               max_hold_bars=int(var.max_hold_bars), min_risk_atr=float(lo), max_risk_atr=float(hi))


# ---------------------------------------------------------------------------- örtüşmesiz ortalama (bilgi)
def _get(e: Any, k: str) -> Any:
    return e.get(k) if isinstance(e, Mapping) else getattr(e, k, None)


def non_overlap_mean_r(events: Iterable[Any], cutoff_ms: float | None) -> dict[str, Any]:
    """Defterin "sembol başına tek pozisyon" kuralına yaklaşım (BİLGİ amaçlı): her sembolde, girişi önceki (tutulan)
    işlemin tutma süresi içine düşen işlem atılır. Giriş barı j = i+1, çıkış barı j + hold - 1. Dönem ayrımı
    `aggregate`ın kesimiyle (t_ms <= kesim → keşif)."""
    cut = float("inf") if cutoff_ms is None else float(cutoff_ms)
    by_sym: dict[str, list[tuple[int, int, float, int]]] = {}
    for e in events:
        r = _get(e, "r")
        if r is None or not math.isfinite(float(r)):
            continue
        by_sym.setdefault(str(_get(e, "symbol")), []).append((int(_get(e, "i")), int(_get(e, "hold") or 1), float(r),
                                                              int(_get(e, "t_ms"))))
    kept: dict[str, list[float]] = {"IS": [], "OOS": []}
    for rows in by_sym.values():
        last_exit = -1
        for i, hold, r, t in sorted(rows):
            j = i + 1
            if j <= last_exit:
                continue
            kept["IS" if t <= cut else "OOS"].append(r)
            last_exit = j + max(hold, 1) - 1
    mean = lambda xs: round(float(np.mean(xs)), 4) if xs else None  # noqa: E731
    return {"IS": mean(kept["IS"]), "OOS": mean(kept["OOS"]), "n": {"IS": len(kept["IS"]), "OOS": len(kept["OOS"])}}


# ---------------------------------------------------------------------------- altın özet
def golden_corpus() -> list[dict[str, np.ndarray]]:
    """Belirlenimci derlem (test içe aktarmaz): 3 tohumlu rastgele yürüyüş × 1200 4h bar, mum içi 12 alt adım, hacimli.
    Yalnız `random.Random(seed).random()` (Python sürümleri arasında sabit) ve dört işlem kullanılır."""
    if _GOLDEN_CACHE:
        return _GOLDEN_CACHE
    step = tf_ms(D.TIMEFRAME)
    out = []
    for seed in GOLDEN_SEEDS:
        rnd = random.Random(seed)
        cols: dict[str, list] = {k: [] for k in ("ts", "o", "h", "l", "c", "v")}
        px = 100.0
        for k in range(GOLDEN_BARS):
            o = hi = lo = x = px
            for _ in range(12):
                x = x * (1.0 + (rnd.random() - 0.5) * 0.012)
                hi, lo = max(hi, x), min(lo, x)
            v = 50.0 + 100.0 * rnd.random()
            if rnd.random() < 0.05:
                v = v * 3.0                               # ara sıra hacim patlaması (hacimli mum koşulları da sınansın)
            for key, val in (("ts", _GOLDEN_T0 + k * step), ("o", o), ("h", hi), ("l", lo), ("c", x), ("v", v)):
                cols[key].append(val)
            px = x
        arr = {"ts": np.array(cols["ts"], dtype=np.int64)}
        arr.update({key: np.array(cols[key], dtype=float) for key in ("o", "h", "l", "c", "v")})
        out.append(arr)
    _GOLDEN_CACHE[:] = out
    return _GOLDEN_CACHE


def _rounded(x: Any) -> Any:
    """Sayılar 10 ANLAMLI basamağa (mutlak adım değil: fiyat ölçeğinden bağımsız; bağımlılık sürümündeki bir ulp farkı
    yuvarlamayı neredeyse hiç çevirmez)."""
    if isinstance(x, Mapping):
        return {str(k): _rounded(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rounded(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        f = float(x)
        return float("%.10g" % f) + 0.0 if math.isfinite(f) else None
    return x


def _golden_view(e: Mapping[str, Any]) -> dict[str, Any]:
    """`explain_last`ın YAPISAL kısmı: her koşulun adı, aşaması, değeri ve sonucu; eşleşme, isabet, bekleyen kırılımlar,
    adayların ilk başarısız koşulu. Türkçe gösterim metinleri (`text`, `first_fail`) GİRMEZ: ifade düzeltmesi dedektör
    değişikliği sayılmaz."""
    return {"matched": e.get("matched"), "hit": e.get("hit"), "first_fail_clause": e.get("first_fail_clause"),
            "pending_breaks": e.get("pending_breaks"),
            "candidates": [{"p": c.get("p"), "p0": c.get("p0"), "first_fail_clause": c.get("first_fail_clause"),
                            "clauses": [[r.get("clause"), r.get("stage"), r.get("value"), r.get("ok")] for r in c.get("clauses") or []]}
                           for c in e.get("candidates") or []]}


def golden_sha(var: D.Variation) -> str:
    """Derlemin her 7. barında biten pencerede `explain_last`ın yapısal kısmı (koşul değerleri ve sonuçları; gösterim
    metni hariç), 10 anlamlı basamağa yuvarlanıp sha256[:16]. Dedektörün anlamı DSL_VERSION artırılmadan değişirse bu
    özet değişir."""
    step = tf_ms(D.TIMEFRAME)
    out = []
    for a in golden_corpus():
        for i in range(D.WINDOW - 1, len(a["ts"]), GOLDEN_EVERY):
            out.append(_golden_view(D.explain_last(window_at(a, i, step), var)))
    s = json.dumps(_rounded(out), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(s.encode("ascii")).hexdigest()[:16]


# ---------------------------------------------------------------------------- kayıt
def run_meta(env: Mapping[str, str] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """GitHub Actions ortamından çalıştırma bilgisi. Yerelde `github_run_id` None (kapı böyle kaydı reddeder)."""
    env = os.environ if env is None else env
    val = lambda k: (str(env.get(k) or "").strip() or None)  # noqa: E731
    rid, repo = val("GITHUB_RUN_ID"), val("GITHUB_REPOSITORY")
    server = (val("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    t = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {"github_run_id": rid, "run_url": "%s/%s/actions/runs/%s" % (server, repo, rid) if rid and repo else None,
            "commit": val("GITHUB_SHA"), "completed_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            # altın özet uyuşmazlığında teşhis için (bağımlılık sürümleri requirements.txt'te sabit değil)
            "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__}}


def _group(groups: list[dict], tf: str, family: str, name: str, side: str, context: str = "HEPSİ") -> dict | None:
    return next((g for g in groups if g.get("tf") == tf and g.get("family") == family and g.get("name") == name
                 and g.get("side") == side and g.get("context") == context), None)


def _series_counts(report: Mapping[str, Any], tf: str, vid: str) -> dict[str, Any]:
    out: dict[str, Any] = {"signals": 0, "trades": 0, "placebo_signals": 0, "placebo_trades": 0, "skipped": {}}
    pre = vid + ":"
    for m in report.get("series") or []:
        if m.get("tf") != tf:
            continue
        vm = (m.get("variations") or {}).get(vid) or {}
        for k in ("signals", "trades", "placebo_signals", "placebo_trades"):
            out[k] += int(vm.get(k) or 0)
        for k, c in (m.get("skipped") or {}).items():
            if str(k).startswith(pre):
                why = str(k)[len(pre):]
                out["skipped"][why] = out["skipped"].get(why, 0) + int(c)
    return out


def _brief(st: Mapping[str, Any] | None) -> dict[str, Any]:
    st = st or {}
    return {k: st.get(k) for k in ("n", "mean_r", "ci95", "cost_r") if k in st}


def _clean(x: Any) -> Any:
    """JSON'a güvenli: numpy tipleri Python'a, sonlu olmayan sayı None'a."""
    if isinstance(x, Mapping):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    return x


def build_record(report: Mapping[str, Any], var: D.Variation, *, env: Mapping[str, str] | None = None,
                 events: Iterable[Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Makinece okunur laboratuvar kaydı (`candle_lab/1`). Gruplar `(tf, candle_var, id, yön, HEPSİ)`; birincil dilim
    tanımın dilimidir (4h), diğerleri bilgi amaçlıdır; birincil dilim koşulmadıysa hüküm None (kapı reddeder).
    `events` verilirse örtüşmesiz ortalama R de yazılır. `readback`: çalıştırma anında kayıttaki çeviri onayı (mühre
    girmez) — taslak (onaysız) koşunun kaydı kapıdan geçmez. `run.mode`: koşunun kipi (`report["mode"]`); kapı yalnız
    `only_variations` koşusunu kabul eder (katalogla birlikte koşu keşif/doğrulama kesimini değiştirir)."""
    from . import candle_variations as CV
    groups = list(report.get("groups") or [])
    cut = dict(report.get("cutoff_ms") or {})
    tfs = [str(t) for t in (report.get("timeframes") or [])]
    evs = [e for e in (events or []) if _get(e, "family") == FAMILY and _get(e, "name") == var.id] if events is not None else None
    by_tf: dict[str, Any] = {}
    skipped: dict[str, int] = {}
    for tf in tfs:
        g = _group(groups, tf, FAMILY, var.id, var.side)
        cnt = _series_counts(report, tf, var.id)
        for why, c in cnt["skipped"].items():
            skipped[why] = skipped.get(why, 0) + c
        no = non_overlap_mean_r([e for e in evs if _get(e, "tf") == tf], cut.get(tf)) if evs is not None else None
        by_tf[tf] = {"IS": dict(g["IS"]) if g else {"n": 0}, "OOS": dict(g["OOS"]) if g else {"n": 0},
                     "vs_placebo": g.get("vs_placebo") if g else None, "verdict": g["verdict"] if g else L.V_THIN,
                     "symbols": int(g["symbols"]) if g else 0, "non_overlap_mean_r": no, **cnt}
    slices = [g for g in groups if g.get("family") == FAMILY and g.get("name") == var.id and g.get("side") == var.side
              and g.get("context") != "HEPSİ" and (g.get("IS") or {}).get("n") and (g.get("OOS") or {}).get("n")]
    slices = sorted(slices, key=lambda g: -float(g["OOS"]["mean_r"]))[:TOP_SLICES]
    primary = var.timeframe
    run = run_meta(env, now=now)
    run["mode"] = dict(report.get("mode") or {})
    rec = {"record_schema": RECORD_SCHEMA, "id": var.id, "definition_sha": var.definition_sha, "dsl_version": D.DSL_VERSION,
           "window": D.WINDOW, "golden_sha": golden_sha(var), "run": run, "readback": var.readback,
           "universe": {"symbols": list(report.get("symbols") or []), "tfs": tfs, "days": dict(report.get("days") or {})},
           "primary_tf": primary, "verdict": by_tf[primary]["verdict"] if primary in by_tf else None,
           "by_tf": by_tf, "skipped": skipped,
           "context_slices_info": [{"tf": g["tf"], "context": g["context"], "bucket": g["bucket"], "IS": _brief(g["IS"]),
                                    "OOS": _brief(g["OOS"]), "vs_placebo": g.get("vs_placebo"), "verdict": g.get("verdict")}
                                   for g in slices],
           "context_slices_note_tr": "Bağlam dilimleri yalnız KEŞİF amaçlıdır (çoklu test); hüküm HEPSİ satırındadır.",
           "trials_to_date": CV.trials_to_date(), "cutoff_ms": {str(k): int(v) for k, v in cut.items()},
           "side": var.side, "example": bool(var.example), "definition": var.definition}
    return _clean(rec)


def record_path(out_dir: Path, vid: str) -> Path:
    return Path(out_dir) / RECORDS_SUBDIR / ("%s.json" % vid)


def write_record(out_dir: Path, rec: Mapping[str, Any]) -> Path:
    """`<out>/variation_records/<ID>.json` — bu dosya `candle_lab_records/`e bayt bayt kopyalanır."""
    p = record_path(out_dir, str(rec["id"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    return p


__all__ = ["FAMILY", "PLACEBO_P", "PLACEBO_PREFIX", "RECORD_SCHEMA", "RECORDS_SUBDIR", "build_record", "golden_corpus",
           "golden_sha", "non_overlap_mean_r", "placebo_name", "record_path", "run_meta", "valid_ends", "variation_events",
           "vcfg", "window_at", "write_record"]

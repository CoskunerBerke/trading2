"""P3f — dersler: atıftan ders istatistiğine, ders deposu ve yaşam döngüsü, dersten önceden kayıtlı aday spec'e
(docs/ENGINE_BRAIN_V1.md §4.1–§4.4; ana belge §5.7–§5.10; mühürler S1 `LESSON_REGISTRY_V1` + `LIB_V1.transforms` ve
S1c `LESSON_READINGS_V1` — `seal_s1c.py`). Yalnız PAPER araştırma; canlı kod/config/worker'a dokunmaz, ağsız.

* **Kanıt.** P2b atıf satırları (`attribution/YYYY-MM.jsonl.gz`); BÜTÜN defterler. REAL = ölçülmüş net R, CF = cf_aux_v1
  muhafazakâr R ile aynı işlemde cfgrid_v1 hücresi − E_ACTUAL (birleştirilmez). Hücreler config dönemi ve kohort İÇİNDE:
  L0 defter, L1 × taktik, L2 L1 × enstrüman, L3 L1 × kova, L4 L1 × enstrüman × kova. Çift = (hücre, FİLTRE | IZGARA:<hücre>).
* **Bakış yalnız eşiklerde.** Çiftin n'i 30/60/120/240'ı geçtiğinde (uygunsa), gece çift başına en çok bir bakış; her bakış
  bir `LESSON_LOOK` deneme satırı (N_lesson_cum + 1). Alfa 0,01/0,01/0,015/0,015; BH (q 0,10) ailesi = o gecenin BÜTÜN
  bakışları, üst sınır 2.000 (n büyük önce, sonra cell_id), taşan ertesi geceye kalır ve sayılır. Ara geceler yazılmaz.
* **İleri doğrulama yalnız T_k sonrası.** Anlamlı keşif → `RESEARCH_HYPOTHESIS`, bulgu T_k'da hash'lenir; tek atışlık
  doğrulama bakışı T_k ayından sonraki 3. tam UTC ayından sonra, yalnız T_k'dan SONRA kapanan aynı hücre kanıtıyla.
* **Durumlar** `learn/lesson_store.build_lesson` + `transition` (izinli geçişler orada); gece `APPLIED_BOUNDED` YAZAMAZ
  (`RunnerForbidden`; depo yazarı da reddeder). Durumun tek kaynağı deneme defteridir: depo (`lessons/lessons.jsonl`,
  yalnız eklenir, zincirli) defterin `LESSON_LOOK`/`RETIRE` satırlarından belirlenimci olarak türetilir.
* **Dönüşüm → spec.** İleri doğrulamayı geçen FİLTRE (L2 → T1, L3 → T4) ve IZGARA (S_ATR → T2, X_* → T3) dersi örtü
  biçiminde bir spec üretir; canlıda zaten olan kural (ham config + bildirilmiş kod sabiti) dışlanır; LIB_v1 (örtüler dahil),
  türeyen spec'ler ve defterin LIB ailesiyle spec_sha üzerinden tekilleştirilir; `library/derived_specs.jsonl` (yalnız
  eklenir). Ayda bir (1'inden sonraki ilk gece) en çok 20'si `LIB_v1.<AAAA-AA>` olarak mühürlenir: `SPEC` deneme satırı.
* **§5.10** Box stop genişliği bulgusu config dönemi başına ve aynı min_stop rejiminin dönemleri birleştirilerek yeniden
  üretilir; aday değildir (uygulanmış karar yalnız izlenir).
* **Atıf kodu** sonuçtan türer: her dersin mekanizma kodu ve defter × kod betimsel görünümü olarak raporlanır (hüküm yok).
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import math
import os
import tempfile
import time
import warnings
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import numpy as np

from . import registry as RG
from . import seal_s1c as S1C
from . import stats as ST
from . import trials as TR
from .daily_target import wilson
from .ledgers import iso, parse_ts, utc_now
from .paths import EnginePaths

# ============================================================================ mühürlü sabitler (S1 + S1c)
LR = RG.LESSON_REGISTRY_V1
_TH = LR["thresholds"]
N_THRESHOLDS: tuple[int, ...] = tuple(int(x) for x in _TH["looks"]["n_thresholds"])        # 30, 60, 120, 240
ALPHAS: tuple[float, ...] = tuple(float(x) for x in _TH["looks"]["alphas"])                 # 0,01 0,01 0,015 0,015
BH_Q = float(_TH["bh_q"])
PAIR_CAP = int(_TH["nightly_pair_cap"])
MIN_N, MIN_DAYS = int(_TH["eligibility"]["min_n"]), int(_TH["eligibility"]["min_distinct_days"])
HR_ALPHA = float(_TH["hierarchical_rate_alpha"])
CI_B, CI_LEVEL = int(_TH["ci"]["bootstrap_resamples"]), float(_TH["ci"]["level"])
FWD_MIN_FOLDS = int(_TH["forward_validation"]["min_monthly_folds"])
FWD_SIGN_SHARE = float(_TH["forward_validation"]["min_sign_consistent_share"])
MAX_SPECS_PER_MONTH = int(RG.LIB_V1["thresholds"]["derived_specs"]["max_specs_per_month"])
OBSERVATION, RESEARCH_HYPOTHESIS, VALIDATED_POLICY_CANDIDATE, APPLIED_BOUNDED, REJECTED, RETIRED = tuple(LR["states"])

RD = S1C.LESSON_READINGS_V1
GRID_CELLS: tuple[str, ...] = tuple(RD["pairs"]["GRID"]["cells"])
PAIR_TYPES: tuple[str, ...] = ("FILTER",) + tuple(f"GRID:{c}" for c in GRID_CELLS)
LEVELS: tuple[str, ...] = tuple(RD["cells"]["levels"])
PARENT: Mapping[str, str] = RD["cells"]["parent"]
FWD_FIRST_MONTHS = int(RD["forward_validation"]["first_complete_months"])
RUNNER_FORBIDDEN = frozenset(RD["states"]["runner_forbidden"])
RUNNER_TARGETS = frozenset(RD["states"]["runner_targets"])
TRANSFORM_COHORTS = frozenset(RD["transforms"]["cohorts"])
BASES: Mapping[str, str] = RD["transforms"]["bases"]
LIVE = RD["live_rule"]
SANITY = RD["sanity_5_10"]
TRANSFORMS_SHA = RG.sha256_canonical(RG.LIB_V1["transforms"])
FACTOR_OF = {"T1": "T1_CELL_FILTER", "T2": "T2_STOP_FLOOR", "T3": "T3_EXIT_VARIANT", "T4": "T4_EXCLUDE_REGIME"}

EVENT_SCHEMA, DERIVED_SCHEMA, RUN_SCHEMA, MANIFEST_SCHEMA = "lesson_event_v1", "derived_spec_v1", "lessons_run_v1", "lib_month_v1"
GENESIS = "0" * 64
LOOK_PREFIX, FWD_PREFIX = "LESSON|", "LESSON_FWD|"
DAY_MS = 86_400_000
#: `engine-query lessons` sözleşmesi (§8: ≤ 150 satır, ≤ 4 KB)
QUERY_MAX_LINES, QUERY_MAX_BYTES = TR.QUERY_MAX_LINES, TR.QUERY_MAX_BYTES


class RunnerForbidden(RuntimeError):
    """Gece çalıştırıcısı bu durumu yazamaz (APPLIED_BOUNDED yalnız sahibin onay yolu + normal sürümle, P4/P7)."""


class LessonStoreBroken(RuntimeError):
    """Ders deposu ya da türeyen spec dosyası zinciri doğrulanmadı: eklenmez (kapalı hata)."""


class LessonsRefused(RuntimeError):
    """Mühür (S1c) tutmuyor ya da kanıt eksik: ders katmanı çalışmaz."""


# ============================================================================ yardımcılar
def _sha(obj: Any) -> str:
    return RG.sha256_canonical(obj)


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd) + 0.0


def _ms(x: Any) -> int | None:
    t = parse_ts(x)
    return int(t.timestamp() * 1000) if t is not None else None


def month_start_ms(ms: int, add: int = 0) -> int:
    """`ms`'nin UTC ayının başı + `add` ay (ms)."""
    d = datetime.fromtimestamp(ms / 1000, UTC)
    k = d.month - 1 + add
    return int(datetime(d.year + k // 12, k % 12 + 1, 1, tzinfo=UTC).timestamp() * 1000)


def month_label(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, UTC)
    return f"{d.year:04d}-{d.month:02d}"


def coin_of(symbol: Any) -> str | None:
    s = str(symbol or "").upper().strip()
    if not s:
        return None
    for suf in (":USDT", "/USDT", "USDT"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s.replace("/", "")


def class_of(coin: str | None) -> str:
    return "GOLD" if coin and any(coin.startswith(g) for g in RD["cells"]["gold_coins"]) else "COIN"


def _bucket(row: Mapping[str, Any]) -> str | None:
    se = row.get("situation_entry")
    if isinstance(se, dict) and se.get("h4_trend") and se.get("h4_vol_regime"):
        return f"{se['h4_trend']}|{se['h4_vol_regime']}"
    rf = row.get("regime_fit")
    b = rf.get("bucket") if isinstance(rf, dict) else None
    if isinstance(b, (list, tuple)) and len(b) == 2 and all(x not in (None, "") for x in b):
        return f"{b[0]}|{b[1]}"
    return None


def _cons(cell: Any) -> float | None:
    """cfgrid_v1 hücresinin cf_aux_v1 muhafazakâr R'si (analysis.cons_r ile aynı kural)."""
    if not isinstance(cell, dict) or cell.get("status") not in ("OK", "MISSED"):
        return None
    return _f(cell.get("r_cons"))


def current_versions() -> tuple[str, str]:
    """Şimdiki (attribution_v1, cfgrid_v1) sha'ları (tembel import: sorgu yolu pandas grafiğini çekmez)."""
    from . import attribution as AT
    from . import cfgrid as CG
    return AT.ATTRIBUTION_SHA, CG.CFGRID_SHA


# ============================================================================ kanıt (S1c evidence)
class Evidence:
    """Sütunlu kanıt tablosu (kapanış, trade_key sırasıyla). `grid[:, j]` = GRID_CELLS[j]'nin eşli Δ'sı (yoksa NaN)."""

    def __init__(self, rows: Iterable[Mapping[str, Any]], *, attribution_sha: str, cfgrid_sha: str,
                 as_of_ms: int | None = None) -> None:
        self.excluded: dict[str, int] = {}
        recs: dict[str, tuple] = {}
        for r in rows:
            why = self._why_not(r, attribution_sha, cfgrid_sha)
            if why:
                self.excluded[why] = self.excluded.get(why, 0) + 1
                continue
            cm = _ms(r.get("closed_at"))
            if as_of_ms is not None and cm > as_of_ms:
                continue
            g = r.get("grid") or {}
            cells = {c.get("id"): c for c in (g.get("cells") or []) if isinstance(c, dict)}
            base = _cons(cells.get("E_ACTUAL")) if g.get("rank_src") == RD["evidence"]["cf_rank_src"] else None
            deltas = [((_cons(cells.get(c)) - base) if base is not None and _cons(cells.get(c)) is not None else math.nan)
                      for c in GRID_CELLS]
            a = r.get("attribution") or {}
            costs = a.get("costs_r") if isinstance(a.get("costs_r"), dict) else {}
            coin = coin_of(r.get("symbol"))
            key = str(r.get("trade_key"))
            recs[key] = (str(r.get("closed_at")), key, str(r.get("book")), str(r.get("config_epoch")),
                         str(r.get("cohort") or "UNTAGGED"), str(r.get("tactic") or "-"), coin or "-", class_of(coin),
                         _bucket(r), _ms(r.get("opened_at")) // DAY_MS, cm, float(r["net_r"]), deltas,
                         _f(costs.get("cost_all")), list(a.get("codes_loss") or []), list(a.get("codes_win") or []),
                         _f(r.get("stop_dist_pct")))
        items = sorted(recs.values(), key=lambda t: (t[10], t[1]))
        self.n = len(items)
        cols = list(zip(*items)) if items else [[] for _ in range(17)]
        (self.closed_iso, self.keys, self.book, self.epoch, self.cohort, self.tactic, self.coin, self.cls, self.bucket) = \
            (list(c) for c in cols[:9])
        self.entry_day = np.array(cols[9], dtype=np.int64)
        self.close_ms = np.array(cols[10], dtype=np.int64)
        self.net_r = np.array(cols[11], dtype=np.float64)
        self.real = -self.net_r
        self.grid = np.array(cols[12], dtype=np.float64).reshape(self.n, len(GRID_CELLS))
        self.cost_r = np.array([math.nan if c is None else c for c in cols[13]], dtype=np.float64)
        self.codes_loss, self.codes_win = list(cols[14]), list(cols[15])
        self.stop_pct = list(cols[16])

    @property
    def sanity_rows(self) -> list[dict]:
        """§5.10 girdisi (yalnız Box satırları; dönem atanmış kanıt)."""
        return [{"book": self.book[i], "config_epoch": self.epoch[i], "net_r": float(self.net_r[i]),
                 "stop_dist_pct": self.stop_pct[i], "attribution": {"codes_loss": self.codes_loss[i]}}
                for i in range(self.n) if self.book[i] == SANITY["book"]]

    @staticmethod
    def _why_not(r: Mapping[str, Any], attribution_sha: str, cfgrid_sha: str) -> str | None:
        a = r.get("attribution") or {}
        if a.get("error") or not r.get("trade_key"):
            return "ATTRIBUTION_ERROR"
        if not r.get("config_epoch"):
            return "NO_CONFIG_EPOCH"
        if _f(r.get("net_r")) is None or _ms(r.get("closed_at")) is None or _ms(r.get("opened_at")) is None:
            return "NO_NET_R"
        g = r.get("grid") or {}
        if a.get("registry_sha") != attribution_sha or (g.get("registry_sha") not in (None, cfgrid_sha)):
            return "STALE_VERSION"
        return None

    def values(self, ptype: str, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(indisler, değerler) — çiftin kanıtı (IZGARA'da Δ'sı olmayan işlem düşer)."""
        if ptype == "FILTER":
            return idx, self.real[idx]
        v = self.grid[idx, GRID_CELLS.index(ptype.split(":", 1)[1])]
        ok = ~np.isnan(v)
        return idx[ok], v[ok]


def cell_id(level: str, meta: Mapping[str, Any]) -> str:
    parts = [level, f"B={meta['book']}", f"E={meta['epoch']}", f"cohort={meta['cohort']}"]
    dims = RD["cells"]["levels"][level]
    for d, k in (("T", "tactic"), ("I", "coin"), ("X", "bucket")):
        if d in dims:
            parts.append(f"{d}={meta[k]}")
    return "|".join(parts)


def build_cells(ev: Evidence) -> tuple[dict[str, np.ndarray], dict[str, dict]]:
    """Hücre → kanıt indisleri (sıralı) ve hücre bilgisi. Kova bilinmiyorsa L3/L4 yok."""
    acc: dict[str, list[int]] = {}
    meta: dict[str, dict] = {}
    for i in range(ev.n):
        m = {"book": ev.book[i], "epoch": ev.epoch[i], "cohort": ev.cohort[i], "tactic": ev.tactic[i], "coin": ev.coin[i],
             "cls": ev.cls[i], "bucket": ev.bucket[i]}
        for lv in LEVELS:
            if "X" in RD["cells"]["levels"][lv] and m["bucket"] is None:
                continue
            cid = cell_id(lv, m)
            acc.setdefault(cid, []).append(i)
            if cid not in meta:
                dims = RD["cells"]["levels"][lv]
                meta[cid] = {"level": lv, "book": m["book"], "epoch": m["epoch"], "cohort": m["cohort"],
                             "tactic": m["tactic"] if "T" in dims else None, "coin": m["coin"] if "I" in dims else None,
                             "cls": m["cls"] if "I" in dims else None, "bucket": m["bucket"] if "X" in dims else None}
    return {k: np.array(v, dtype=np.int64) for k, v in acc.items()}, meta


def parent_cell(cid: str, meta: Mapping[str, Any]) -> str | None:
    lv = meta["level"]
    if lv == "L0":
        return None
    return cell_id(PARENT[lv], meta)


# ============================================================================ istatistik
def day_ci(days: np.ndarray, vals: np.ndarray, *, seed: int, resamples: int = CI_B, level: float = CI_LEVEL
           ) -> list[float] | None:
    """Gün kümelenmeli yüzdelik bootstrap CI (B6 üreteci, `stats.choice_stream`; tekrar ortalaması = Σ toplam / Σ sayı)."""
    u, inv = np.unique(days, return_inverse=True)
    d = len(u)
    if d < 2:
        return None
    s = np.bincount(inv, weights=vals)
    c = np.bincount(inv).astype(np.float64)
    means = [s[ix].sum(axis=1) / c[ix].sum(axis=1) for ix in ST.choice_stream(seed, d, resamples)]
    m = np.sort(np.concatenate(means))
    a = 1.0 - level
    return [_r(m[int(math.floor(a / 2 * (resamples - 1)))]), _r(m[int(math.ceil((1 - a / 2) * (resamples - 1)))])]


def evidence_digest(ev: Evidence, idx: np.ndarray, vals: np.ndarray) -> str:
    h = hashlib.sha256()
    for i, v in zip(idx.tolist(), vals.tolist()):
        h.update(f"{ev.keys[i]}\t{v:.6f}\n".encode("utf-8"))
    return h.hexdigest()


def _top_codes(ev: Evidence, idx: Sequence[int]) -> list[list]:
    cnt: dict[str, int] = {}
    for i in idx:
        for c in ev.codes_loss[i]:
            cnt[c] = cnt.get(c, 0) + 1
    return [[c, n] for c, n in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[:3]]


def pair_rate(vals: np.ndarray, ptype: str) -> tuple[int, int]:
    """(k, n): FİLTRE'de kazanan işlem (net R > 0), IZGARA'da hücrenin daha iyi olduğu işlem."""
    return (int((vals < 0).sum()) if ptype == "FILTER" else int((vals > 0).sum())), int(len(vals))


def sample_stats(ev: Evidence, idx: np.ndarray, vals: np.ndarray, *, ptype: str, cid: str, row_id: str, look_no: int,
                 parent_rate: float) -> dict[str, Any]:
    """Bir bakışın istatistikleri (§4.1): n, gün, etki (ortalama), CI95, Wilson + HierarchicalRate büzülmesi, p_day,
    karşı kanıt, en iyi/kötü 3 işlem, mekanizma kodu, ortalama maliyet R'si (MALİYET yalnız rapor), kanıt özeti."""
    days = ev.entry_day[idx]
    n = int(len(idx))
    k, _ = pair_rate(vals, ptype)
    order = np.argsort(vals, kind="stable")
    support = [int(i) for i, v in zip(idx.tolist(), vals.tolist()) if v > 0]
    p = ST.p_day(zip(days.tolist(), vals.tolist()), row_id=row_id, look_no=look_no)
    cost = ev.cost_r[idx]
    cost = cost[~np.isnan(cost)]
    return {"n": n, "n_days": int(len(np.unique(days))), "effect": _r(float(vals.mean())),
            "ci95": day_ci(days, vals, seed=ST.seed_int(f"{cid}|{look_no}")), "p_day": p,
            "rate": _r(k / n), "rate_wilson": wilson(k, n), "rate_shrunk": _r((k + HR_ALPHA * parent_rate) / (n + HR_ALPHA)),
            "parent_rate": _r(parent_rate), "counter_n": int((vals < 0).sum()),
            "best": [ev.keys[int(idx[j])] for j in order[::-1][:3]], "worst": [ev.keys[int(idx[j])] for j in order[:3]],
            "mechanism": _top_codes(ev, support), "cost_r_mean": _r(float(cost.mean())) if len(cost) else None,
            "evidence_sha": evidence_digest(ev, idx, vals),
            "window": {"from": ev.closed_iso[int(idx[0])], "to": ev.closed_iso[int(idx[-1])]}}


def folds(ev: Evidence, idx: np.ndarray, vals: np.ndarray) -> dict[str, Any]:
    """Aylık katmanlar (kapanışın UTC ayı, işlemi olan aylar) ve işaret tutarlılığı (katman ortalaması > 0)."""
    acc: dict[str, list[float]] = {}
    for i, v in zip(idx.tolist(), vals.tolist()):
        acc.setdefault(month_label(int(ev.close_ms[i])), []).append(v)
    per = {m: _r(sum(x) / len(x)) for m, x in sorted(acc.items())}
    pos = sum(1 for v in per.values() if v is not None and v > 0)
    share = pos / len(per) if per else 0.0
    return {"months": per, "n_folds": len(per), "positive": pos, "share": _r(share),
            "ok": len(per) >= FWD_MIN_FOLDS and share >= FWD_SIGN_SHARE - 1e-12}


def discovery_verdict(effect: float | None, p: float | None, q: float | None, look_no: int) -> str:
    """Keşif bakışı: anlamlı = etki > 0 VE p_day ≤ α_k VE q_BH ≤ 0,10 (S1c `looks.significant`)."""
    if effect is not None and effect > 0 and p is not None and p <= ALPHAS[look_no - 1] and q is not None and q <= BH_Q:
        return "SIGNIFICANT"
    return "EXHAUSTED" if look_no >= len(N_THRESHOLDS) else "NOT_SIGNIFICANT"


def validation_verdict(effect: float | None, p: float | None, alpha: float, fold_ok: bool) -> str:
    if effect is None or effect <= 0:
        return "FWD_SIGN_FLIP"
    if p is not None and p <= alpha and fold_ok:
        return "FWD_PASS"
    return "FWD_FAIL"


def validation_look_no(k: int) -> int:
    return min(k + 1, len(N_THRESHOLDS))


# ============================================================================ çift durumu (tek kaynak: deneme defteri)
def ledger_lesson_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    return [dict(r) for r in rows if str(r.get("row_id") or "").startswith((LOOK_PREFIX, FWD_PREFIX))
            and r.get("kind") in ("LESSON_LOOK", "RETIRE")]


def pair_states(lrows: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    """Defter satırlarından çift durumu: kuşak, evre (DISCOVERY | VALIDATION | DONE), son bakış, hipotez, temel."""
    st: dict[str, dict] = {}
    for r in lrows:
        pa = r.get("params") or {}
        pid, gen = pa.get("pair_id"), int(pa.get("gen") or 0)
        s = st.setdefault(pid, {"gen": 0, "phase": "DISCOVERY", "last_look": 0, "hyp": None, "basis": None,
                                "lesson_id": None, "retired": False})
        if r["kind"] == "RETIRE":
            if gen == s["gen"]:
                st[pid] = {"gen": gen + 1, "phase": "DISCOVERY", "last_look": 0, "hyp": None, "basis": None,
                           "lesson_id": None, "retired": False}
            continue
        if gen != s["gen"]:
            continue
        s["lesson_id"] = pa.get("lesson_id")
        if pa.get("kind") == "DISCOVERY":
            s["last_look"] = int(r["look_no"])
            s["basis"] = {"as_of_ms": pa.get("as_of_ms"), "evidence_sha": pa.get("evidence_sha"),
                          "attribution_sha": pa.get("attribution_sha"), "cfgrid_sha": pa.get("cfgrid_sha")}
            if r.get("verdict") == "SIGNIFICANT":
                s["phase"] = "VALIDATION"
                s["hyp"] = {"k": int(r["look_no"]), "t_k_ms": int(pa["as_of_ms"]), "hypothesis_sha": pa.get("hypothesis_sha"),
                            "q": r.get("q_bh"), "effect": pa.get("effect")}
            elif r.get("verdict") == "EXHAUSTED":
                s["phase"] = "DONE"
        else:
            s["phase"] = "DONE"
            s["retired"] = r.get("verdict") == "FWD_SIGN_FLIP"
    return st


def lesson_id_of(pair_id: str, gen: int) -> str:
    return "LSN-" + _h(f"{pair_id}|g{gen}")[:20]


# ============================================================================ dönüşüm → spec (S1 §4.3 + S1c)
def _base_module_sha(base: str, module_src_sha: Mapping[str, str]) -> str | None:
    vs = RD["transforms"]["base_variants"][base]
    shas = [module_src_sha.get(v) for v in vs]
    if any(not RG.is_sha256(s) for s in shas):
        return None
    return shas[0] if len(shas) == 1 else _h("\n".join(shas))


def transform_of(meta: Mapping[str, Any], ptype: str) -> tuple[str, Any] | None:
    if ptype == "FILTER":
        t = RD["transforms"]["FILTER"].get(meta["level"])
        if t == "T1":
            return "T1", meta["coin"]
        if t == "T4":
            return "T4", meta["bucket"]
        return None
    m = RD["transforms"]["GRID"].get(ptype.split(":", 1)[1])
    return (m[0], m[1]) if m else None


def build_proposal(meta: Mapping[str, Any], ptype: str, *, module_src_sha: Mapping[str, str] | None) -> dict[str, Any]:
    """Dersin dönüşümü ve spec'i (örtü biçimi). Durum: PROPOSAL | NO_LIB_BASE | NO_TRANSFORM_COHORT | NO_TRANSFORM |
    NO_MODULE_SHA. Hiçbir dönüşüm mevcut defteri değiştirmez (spec yalnız yeni defter olabilir, §7)."""
    base = BASES.get(meta["book"])
    if base is None:
        return {"status": "NO_LIB_BASE"}
    if meta["cohort"] not in TRANSFORM_COHORTS:
        return {"status": "NO_TRANSFORM_COHORT"}
    tf = transform_of(meta, ptype)
    if tf is None:
        return {"status": "NO_TRANSFORM"}
    t, arg = tf
    msha = _base_module_sha(base, module_src_sha or {})
    if msha is None:
        return {"status": "NO_MODULE_SHA", "transform": t, "base": base}
    cond: dict[str, Any] = {}
    if meta["level"] != "L0":
        cond["tactic"] = meta["tactic"]
    if t in ("T2", "T3") and meta.get("bucket"):
        cond["bucket"] = meta["bucket"]
    if t == "T1":
        fp, tag, scope = {"exclude_instruments": [arg]}, f"I-{arg}", "POOLED_OWN"
    elif t == "T4":
        fp, tag, scope = {"exclude_buckets": [arg], "bucket_def": "situation_v1 h4_trend|h4_vol_regime"}, \
            "X-" + str(arg).replace("|", "-"), "POOLED_OWN"
    elif t == "T2":
        fp, tag = {"stop_k_atr": float(arg), "atr": "ATR14 (sinyal dilimi)", "cfgrid_cell": ptype.split(":", 1)[1]}, f"k{arg}"
        scope = "COIN:" + meta["coin"] if RD["transforms"]["scope"][meta["level"]] == "COIN" else "POOLED_OWN"
    else:
        fp, tag = {"exit": arg, "cfgrid_cell": arg}, str(arg)
        scope = "COIN:" + meta["coin"] if RD["transforms"]["scope"][meta["level"]] == "COIN" else "POOLED_OWN"
    if cond:
        fp["condition"] = cond
    factor = FACTOR_OF[t]
    eq = RD["transforms"]["cover_equivalence"].get(f"{t}:{arg}")
    if eq:                                                   # tanımı AYNI örtü → o örtünün spec'i (aynı spec_sha)
        vid = f"O_{base}_{eq}"
        spec = RG.lib_spec(vid, module_src_sha[vid])
    else:
        cond_tag = ("@" + _h(RG.canonical_bytes(cond).decode("utf-8"))[:8]) if cond else ""
        exits = {"stop": "tabanınki", "exit_rule": "tabanınki", "target_r": None, "max_hold_bars": "tabanınki"}
        if t == "T2":
            exits["stop"] = {"kind": "ATR", "atr_mult": float(arg), "atr": "ATR14 (sinyal dilimi)"}
        elif t == "T3":
            exits["exit_rule"] = arg
        spec = {"family": "O", "variant_id": f"D_{base}_{t}_{tag}{cond_tag}",
                "params": {"base": base, "factor": factor, "factor_params": fp,
                           "derived": {"lib_parent": RG.LIB_VERSION, "transform": t, "readings": S1C.LESSON_READINGS_V1_SHA}},
                "timeframes": RG.thaw(RG.LIB_V1["copy_books"][base]["timeframes"]),
                "exits": exits, "sizing": RG.thaw(RG.LIB_V1["sizing"]),
                "module_src_sha": _h(f"{msha}|{TRANSFORMS_SHA}|{S1C.LESSON_READINGS_V1_SHA}")}
    return {"status": "PROPOSAL", "transform": t, "name": RG.LIB_V1["transforms"]["list"][t]["name"], "arg": arg,
            "base": base, "scope": scope, "spec": spec, "spec_sha": RG.spec_sha(spec),
            "row_id": f"{spec['variant_id']}@{scope}"}


def _raw_books(raw: Any) -> tuple[bool, dict, dict]:
    if raw is None:
        return False, {}, {}
    d = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
    return bool(d.get("ok")), dict(d.get("values") or {}), dict(d.get("books") or {})


def _config_name(book: str) -> str | None:
    for rule, books in RG.CONFIG_EPOCHS_V1["book_rules"].items():
        if book in books:
            return rule
    return None


def live_rule_check(prop: Mapping[str, Any], book: str, raw: Any) -> dict[str, Any]:
    """Canlıda zaten olan kural mı (S1c `live_rule`): LIVE | NOT_LIVE | UNAVAILABLE (config okunamadı → spec yazılmaz)."""
    ok, values, books = _raw_books(raw)
    if not ok:
        return {"status": "UNAVAILABLE", "why": LIVE["unreadable"]}
    t, base, arg = prop["transform"], prop["base"], prop["arg"]
    name = _config_name(book)
    b = books.get(name or "", {})
    if t == "T1":
        syms = b.get("symbols")
        src = LIVE["T1"]["raw"]
        if not isinstance(syms, list) and base in LIVE["T1"]["universe_books"]:
            syms, src = values.get(LIVE["T1"]["universe_raw"]), LIVE["T1"]["universe_raw"]
        if not isinstance(syms, list):
            return {"status": "NOT_LIVE", "why": "UNKNOWN_SYMBOLS"}
        live = {coin_of(s) for s in syms}
        return {"status": "LIVE" if arg not in live else "NOT_LIVE", "key": src, "value_has": arg in live}
    if t == "T2":
        spec = LIVE["T2"].get(base)
        if not spec:
            return {"status": "NOT_LIVE", "why": "NO_ATR_STOP_RULE"}
        cur = spec.get("value") if "code" in spec else _f(b.get("atr_mult"))
        src = spec.get("code") or spec.get("raw")
        return {"status": "LIVE" if cur is not None and abs(float(cur) - float(arg)) < 1e-12 else "NOT_LIVE", "key": src,
                "value": cur}
    if t == "T3":
        spec = LIVE["T3"].get(base)
        if not spec:
            return {"status": "NOT_LIVE", "why": "NO_LIVE_EXIT_MAP"}
        rp = b.get("rule_params") or {}
        want = spec["cells"].get(arg)
        same = want is not None and rp.get("exit_kind") == spec["exit_kind"] and _f(rp.get("exit_r")) == float(want)
        return {"status": "LIVE" if same else "NOT_LIVE", "key": spec["raw"],
                "value": {"exit_kind": rp.get("exit_kind"), "exit_r": rp.get("exit_r")}}
    return {"status": "NOT_LIVE", "why": LIVE["T4"]}


def lib_spec_index(module_src_sha: Mapping[str, str] | None) -> dict[tuple[str, str], str]:
    """LIB_v1'in (spec_sha, kapsam) → satır kimliği dizini (kopyalar ve örtüler; modül sha'sı olanlar)."""
    out: dict[tuple[str, str], str] = {}
    vs = RG.variants_by_id()
    for r in RG.gate_a_rows():
        if r["row_type"] not in ("COPY", "COVER"):
            continue
        base = vs[r["unit"]].get("alias_of") or r["unit"]
        msha = (module_src_sha or {}).get(base)
        if RG.is_sha256(msha):
            out[(RG.spec_sha(RG.lib_spec(r["unit"], msha)), r["scope"])] = r["row_id"]
    return out


# ============================================================================ yalnız eklenir, zincirli JSONL (depo + türeyen spec)
def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_atomic(paths: EnginePaths, target: Path, data: bytes) -> None:
    """Eski baytlar + yeni satırlar aynı klasörde geçici dosyaya → fsync → geri okuma → önek ve hedef değişmedi denetimi →
    `os.replace` → klasör fsync (trials.py sözleşmesi). Hata = hedef değişmez."""
    p = paths.require_research(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_bytes() if p.exists() else b""
    new = old + data
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new)
            fh.flush()
            os.fsync(fh.fileno())
        back = Path(tmp).read_bytes()
        if back != new or not back.startswith(old) or (p.read_bytes() if p.exists() else b"") != old:
            raise LessonStoreBroken(f"{p.name}: atomik ekleme denetimi tutmadı")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(p.parent)


class ChainFile:
    """Yalnız eklenir, hash zincirli JSONL (`seq`, `prev_sha`, `row_sha`; satır = kanonik JSON). Kırık zincire eklenmez."""

    def __init__(self, paths: EnginePaths, path: Path, schema: str) -> None:
        self.paths, self.path, self.schema = paths, path, schema
        self.rows: list[dict] = []
        self.head = GENESIS
        self.size = 0

    def load(self) -> "ChainFile":
        raw = self.path.read_bytes() if self.path.exists() else b""
        rows, prev = [], GENESIS
        for n, line in enumerate(raw.split(b"\n")[:-1] if raw else [], 1):
            try:
                r = json.loads(line)
            except ValueError as exc:
                raise LessonStoreBroken(f"{self.path.name}:{n}: BAD_JSON {exc}") from None
            if not isinstance(r, dict) or RG.canonical_bytes(r) != line:
                raise LessonStoreBroken(f"{self.path.name}:{n}: NOT_CANONICAL")
            if r.get("schema") != self.schema or r.get("seq") != n or r.get("prev_sha") != prev:
                raise LessonStoreBroken(f"{self.path.name}:{n}: BAD_SEQ/PREV/SCHEMA")
            if TR.row_sha(r) != r.get("row_sha"):
                raise LessonStoreBroken(f"{self.path.name}:{n}: BAD_ROW_SHA")
            rows.append(r)
            prev = r["row_sha"]
        if raw and not raw.endswith(b"\n"):
            raise LessonStoreBroken(f"{self.path.name}: TORN_TAIL")
        self.rows, self.head, self.size = rows, prev, len(raw)
        return self

    def append(self, items: Iterable[Mapping[str, Any]]) -> list[dict]:
        out, blob, prev, seq = [], [], self.head, len(self.rows)
        for it in items:
            seq += 1
            row = dict(it, schema=self.schema, seq=seq, prev_sha=prev)
            row["row_sha"] = TR.row_sha(row)
            blob.append(RG.canonical_bytes(row) + b"\n")
            prev = row["row_sha"]
            out.append(row)
        if blob:
            cur = self.path.read_bytes() if self.path.exists() else b""
            if len(cur) != self.size:
                raise LessonStoreBroken(f"{self.path.name}: yükleme sonrası değişti")
            _append_atomic(self.paths, self.path, b"".join(blob))
            self.rows += out
            self.head, self.size = prev, self.size + sum(len(b) for b in blob)
        return out


def lessons_dir(paths: EnginePaths) -> Path:
    return paths.research / "lessons"


def library_dir(paths: EnginePaths) -> Path:
    return paths.research / "library"


def store_file(paths: EnginePaths) -> Path:
    return lessons_dir(paths) / "lessons.jsonl"


def derived_file(paths: EnginePaths) -> Path:
    return library_dir(paths) / "derived_specs.jsonl"


def manifest_file(paths: EnginePaths, month: str) -> Path:
    return library_dir(paths) / f"{RG.LIB_VERSION}.{month}.json"


def last_run_file(paths: EnginePaths) -> Path:
    return lessons_dir(paths) / "_last_run.json"


@contextmanager
def _locked(paths: EnginePaths) -> Iterator[None]:
    lp = paths.require_research(lessons_dir(paths) / "lessons.lock")
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ============================================================================ ders deposu (defterden türetilir)
def _guard(state: str | None) -> None:
    if state in RUNNER_FORBIDDEN:
        raise RunnerForbidden(f"gece çalıştırıcısı {state} yazamaz (yalnız sahibin onay yolu + normal sürüm)")


def runner_transition(lesson: dict, to_state: str, *, reason: str, at: str) -> dict:
    """Gecenin TEK geçiş yolu: yasak durum (APPLIED_BOUNDED) önce reddedilir, sonra `learn.lesson_store.transition`
    (izinli geçişler, geçmiş yalnız eklenir)."""
    _guard(to_state)
    if to_state not in RUNNER_TARGETS:
        raise RunnerForbidden(f"{to_state} gece hedefi değil")
    from ..learn.lesson_store import transition
    return transition(lesson, to_state, reason=reason, at=at)


def _new_lesson(pa: Mapping[str, Any], row: Mapping[str, Any]) -> dict:
    from ..learn.lesson_store import build_lesson
    t = pa["type"]
    obs = observation_tr(pa)
    doc = build_lesson(source_trade_id=row["row_id"], symbol=pa.get("coin") or "-", direction="-",
                       setup=f"{pa.get('tactic') or '-'}|{t}", regime=pa.get("bucket") or "-",
                       observation={"observation_tr": obs, "pair_id": pa["pair_id"], "cell_id": pa["cell_id"]},
                       evidence_level=OBSERVATION, n_supporting=int(pa["n"]) - int(pa["counter_n"]),
                       n_conflicting=int(pa["counter_n"]), as_of=pa["as_of"])
    keys = [f"B|{pa['book']}", f"E|{pa['epoch']}", f"T|{pa.get('tactic') or '-'}"]
    if pa.get("coin"):
        keys += [f"I|{pa['coin']}", f"A|{pa.get('cls') or 'COIN'}"]
    if pa.get("bucket"):
        keys.append(f"X|{pa['bucket']}")
    keys.append(f"V|{t}")
    if pa.get("mechanism"):
        keys.append(f"C|{pa['mechanism'][0][0]}")
    doc.update({"lesson_id": pa["lesson_id"], "created_at": pa["as_of"], "as_of": pa["as_of"], "engine": "P3f",
                "lesson_type": "FILTER" if t == "FILTER" else "GRID", "pair_type": t,
                "scope": {k: pa.get(k) for k in ("level", "book", "epoch", "cohort", "tactic", "coin", "cls", "bucket")},
                "keys": keys, "observation_tr": obs, "mechanism_code": pa["mechanism"][0][0] if pa.get("mechanism") else None,
                "evidence": {}, "best_trade_keys": [], "worst_trade_keys": [], "status_history": [],
                "linked_candidate_id": None, "linked_promo_id": None, "gen": pa["gen"]})
    return doc


def observation_tr(pa: Mapping[str, Any]) -> str:
    where = " · ".join(str(x) for x in (pa.get("tactic"), pa.get("coin"), pa.get("bucket")) if x)
    head = f"{pa['book']}{(' ' + where) if where else ''} ({pa['epoch']}, {pa['cohort']})"
    e = pa.get("effect")
    es = "?" if e is None else f"{e:+.3f}".replace(".", ",")
    if pa["type"] == "FILTER":
        return f"{head}: {pa['n']} işlem; atlamak işlem başına {es}R değiştirirdi (FİLTRE)"
    return f"{head}: {pa['n']} işlem; {pa['type'].split(':', 1)[1]} gerçeğe göre işlem başına {es}R (IZGARA, eşli)"


def _look_into(doc: Mapping[str, Any], e: Mapping[str, Any]) -> dict:
    """Bakışın kanıtını derse işle — YENİ sözlük (önceki olaylara verilmiş belge değişmez)."""
    d = dict(doc)
    d["evidence"] = dict(e["evidence"])
    d["best_trade_keys"], d["worst_trade_keys"] = list(e["best"]), list(e["worst"])
    d["n_supporting"], d["n_conflicting"] = e["evidence"]["n"] - e["counter_n"], e["counter_n"]
    if e.get("mechanism"):
        d["mechanism_code"] = e["mechanism"][0][0]
    return d


class LessonStore:
    """`lessons/lessons.jsonl`: olaylar (CREATE, LOOK, TRANSITION, SPEC). Durum = olayların katlanması. Olaylar deneme
    defterinin satırlarından belirlenimci olarak türetilir (`sync`); yasak durum hiçbir olayda yazılamaz."""

    def __init__(self, paths: EnginePaths) -> None:
        self.paths = paths
        self.file = ChainFile(paths, store_file(paths), EVENT_SCHEMA)
        self.lessons: dict[str, dict] = {}
        self.ledger_seq = 0
        self.derived_seq = 0

    def load(self) -> "LessonStore":
        self.file.load()
        for e in self.file.rows:
            self._apply(e)
        return self

    def _apply(self, e: Mapping[str, Any]) -> None:
        k = e["event"]
        if k == "CREATE":
            self.lessons[e["lesson_id"]] = dict(e["lesson"])
        elif k == "LOOK":
            self.lessons[e["lesson_id"]] = _look_into(self.lessons[e["lesson_id"]], e)
        elif k in ("TRANSITION", "UPDATE"):
            self.lessons[e["lesson_id"]] = dict(e["lesson"])
        elif k == "SPEC":
            d = self.lessons.get(e["lesson_id"])
            if d is not None:
                d["linked_candidate_id"] = e.get("row_id")
                d.setdefault("specs", []).append({"status": e["status"], "row_id": e.get("row_id"),
                                                 "spec_sha": e.get("spec_sha")})
        if e.get("ledger_seq"):
            self.ledger_seq = max(self.ledger_seq, int(e["ledger_seq"]))
        if e.get("derived_seq"):
            self.derived_seq = max(self.derived_seq, int(e["derived_seq"]))

    def _check(self, ev: Mapping[str, Any]) -> None:
        _guard(ev.get("to"))
        lesson = ev.get("lesson")
        if isinstance(lesson, Mapping):
            _guard(lesson.get("policy_status"))
            _guard(lesson.get("evidence_level"))

    def append(self, events: Sequence[Mapping[str, Any]]) -> None:
        for ev in events:
            self._check(ev)
        for row in self.file.append(events):
            self._apply(row)

    def events_from_ledger(self, lrows: Iterable[Mapping[str, Any]], at: str) -> list[dict]:
        """Defterin henüz depoya yansımamış ders satırları → olaylar (belirlenimci)."""
        out: list[dict] = []
        shadow = {k: dict(v) for k, v in self.lessons.items()}
        for r in lrows:
            if int(r["seq"]) <= self.ledger_seq:
                continue
            pa = r.get("params") or {}
            lid = pa.get("lesson_id")
            base = {"ledger_seq": int(r["seq"]), "lesson_id": lid, "at": pa.get("as_of") or at}
            if r["kind"] == "RETIRE":
                if lid in shadow and shadow[lid].get("policy_status") != RETIRED:
                    doc = runner_transition(shadow[lid], RETIRED, reason=f"INVALIDATED: {','.join(pa.get('reasons') or [])}",
                                            at=base["at"])
                    shadow[lid] = doc
                    out.append({**base, "event": "TRANSITION", "to": RETIRED, "lesson": doc})
                continue
            if lid not in shadow:
                doc = _new_lesson(pa, r)
                shadow[lid] = doc
                out.append({**base, "event": "CREATE", "lesson": doc})
            ev = {"n": pa["n"], "n_days": pa["n_days"], "mean_r": pa["effect"], "ci95": pa["ci95"], "p_day": r.get("p_day"),
                  "effect_delta_r": pa["effect"], "q_bh": r.get("q_bh"), "look_no": r["look_no"], "kind": pa["kind"],
                  "alpha": pa["alpha"], "rate": pa["rate"], "rate_wilson": pa["rate_wilson"],
                  "rate_shrunk": pa["rate_shrunk"], "cost_r_mean": pa["cost_r_mean"],
                  "discovery_window" if pa["kind"] == "DISCOVERY" else "validation_window": pa["window"],
                  "data_seal": r.get("data_seal"), "registry_sha": r.get("registry_sha"), "config_epoch": pa["epoch"],
                  "verdict": r.get("verdict")}
            look = {**base, "event": "LOOK", "row_id": r["row_id"], "evidence": ev, "best": pa["best"], "worst": pa["worst"],
                    "counter_n": pa["counter_n"], "mechanism": pa.get("mechanism") or []}
            out.append(look)
            shadow[lid] = _look_into(shadow[lid], look)
            to = (pa.get("decision") or {}).get("to")
            if to:
                doc = runner_transition(shadow[lid], to, reason=(pa.get("decision") or {}).get("reason", ""),
                                        at=base["at"])
                if pa.get("hypothesis_sha"):
                    doc = {**doc, "hypothesis_sha": pa["hypothesis_sha"], "t_k": pa["as_of"]}
                if pa["kind"] == "VALIDATION":
                    doc = {**doc, "fwd_validation": r.get("verdict")}
                shadow[lid] = doc
                out.append({**base, "event": "TRANSITION", "to": to, "lesson": doc})
            elif pa["kind"] == "VALIDATION":
                doc = {**shadow[lid], "fwd_validation": r.get("verdict")}
                shadow[lid] = doc
                out.append({**base, "event": "UPDATE", "lesson": doc})
        return out

    def events_from_derived(self, drows: Iterable[Mapping[str, Any]], at: str) -> list[dict]:
        return [{"event": "SPEC", "derived_seq": int(d["seq"]), "lesson_id": d.get("lesson_id"), "at": d.get("at") or at,
                 "status": d["status"], "row_id": d.get("row_id"), "spec_sha": d.get("spec_sha")}
                for d in drows if int(d["seq"]) > self.derived_seq]


# ============================================================================ gece adımı (S6'nın ders bölümü)
def iter_attribution_rows(paths: EnginePaths) -> Iterator[dict]:
    root = paths.attribution
    if not root.is_dir():
        return
    for p in sorted(root.glob("????-??.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def s2_pending(paths: EnginePaths) -> int | None:
    try:
        with open(paths.attribution / "_build.json", "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return int(d.get("pending") or 0) if isinstance(d, dict) else None


def _plan(ev: Evidence, cells: Mapping[str, np.ndarray], meta: Mapping[str, dict], states: Mapping[str, dict], *,
          as_of_ms: int) -> list[dict]:
    """Bu gecenin bakışları (çift başına en çok bir): keşif (en küçük bekleyen eşik, uygunsa) ve doğrulama (vakti gelmiş,
    T_k sonrası örneklem uygunsa)."""
    tasks: list[dict] = []
    for cid in sorted(cells):
        idx_all = cells[cid]
        for ptype in PAIR_TYPES:
            pid = f"{cid}#{ptype}"
            s = states.get(pid) or {"gen": 0, "phase": "DISCOVERY", "last_look": 0, "hyp": None}
            if s["phase"] == "DONE":
                continue
            idx, vals = ev.values(ptype, idx_all)
            if not len(idx):
                continue
            g = int(s["gen"])
            if s["phase"] == "DISCOVERY":
                k = int(s["last_look"]) + 1
                if k > len(N_THRESHOLDS) or len(idx) < N_THRESHOLDS[k - 1]:
                    continue
                if len(idx) < MIN_N or len(np.unique(ev.entry_day[idx])) < MIN_DAYS:
                    continue
                tasks.append({"pair_id": pid, "cell_id": cid, "type": ptype, "kind": "DISCOVERY", "gen": g, "look_no": k,
                              "row_id": f"{LOOK_PREFIX}{pid}|g{g}", "alpha": ALPHAS[k - 1], "idx": idx, "vals": vals})
                continue
            hyp = s["hyp"]
            t_k = int(hyp["t_k_ms"])
            j = FWD_FIRST_MONTHS
            while True:
                end = month_start_ms(t_k, j + 1)
                if end > as_of_ms:
                    break
                m = (ev.close_ms[idx] > t_k) & (ev.close_ms[idx] < end)
                fi, fv = idx[m], vals[m]
                if len(fi) >= MIN_N and len(np.unique(ev.entry_day[fi])) >= MIN_DAYS:
                    ln = validation_look_no(int(hyp["k"]))
                    tasks.append({"pair_id": pid, "cell_id": cid, "type": ptype, "kind": "VALIDATION", "gen": g,
                                  "look_no": ln, "row_id": f"{FWD_PREFIX}{pid}|g{g}|h{hyp['k']}", "alpha": ALPHAS[ln - 1],
                                  "idx": fi, "vals": fv, "hyp": hyp, "window_end_ms": end})
                    break
                j += 1
    return tasks


def _parent_rate(ev: Evidence, cells: Mapping[str, np.ndarray], meta: Mapping[str, dict], cid: str, ptype: str) -> float:
    pc = parent_cell(cid, meta[cid])
    if pc is None or pc not in cells:
        return 0.5
    _i, v = ev.values(ptype, cells[pc])
    k, n = pair_rate(v, ptype)
    return k / n if n else 0.5


def _invalidations(ev: Evidence, cells: Mapping[str, np.ndarray], states: Mapping[str, dict], versions: tuple[str, str]
                   ) -> list[tuple[str, int, str | None, list[str]]]:
    """(pair_id, kuşak, lesson_id, nedenler): sha değişti ya da son keşif bakışının kanıt özeti şimdiki veriyle tutmuyor."""
    out = []
    for pid, s in sorted(states.items()):
        b = s.get("basis")
        if not b or s.get("retired"):
            continue
        why = []
        if b.get("attribution_sha") != versions[0]:
            why.append("ATTRIBUTION_SHA")
        if b.get("cfgrid_sha") != versions[1]:
            why.append("CFGRID_SHA")
        cid, ptype = pid.rsplit("#", 1)
        idx_all = cells.get(cid)
        if idx_all is None:
            why.append("EVIDENCE_DIGEST")
        else:
            idx, vals = ev.values(ptype, idx_all)
            m = ev.close_ms[idx] <= int(b["as_of_ms"])
            if evidence_digest(ev, idx[m], vals[m]) != b.get("evidence_sha"):
                why.append("EVIDENCE_DIGEST")
        if why:
            out.append((pid, int(s["gen"]), s.get("lesson_id"), why))
    return out


def run_lessons(paths: EnginePaths, *, now: datetime | None = None, rows: Iterable[Mapping[str, Any]] | None = None,
                raw: Any = None, module_src_sha: Mapping[str, str] | None = None,
                versions: tuple[str, str] | None = None, check_s2: bool = True, deadline: float | None = None,
                clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """S6'nın ders adımı (bir gece). Sıra: mühür → defter (zincir) → kanıt → geçersizleşme (`RETIRE`) → bakış planı →
    üst sınır + BH → hükümler → `LESSON_LOOK` satırları → türeyen spec önerileri → aylık mühür → depo eşitleme → özet.
    Gece APPLIED_BOUNDED yazamaz. Aynı gece yeniden çalışmak yeni bakış/deneme üretmez (defter tekilleştirir).
    `deadline` (`clock()` biriminde; S6 payı): YALNIZ yazımdan önceki güvenli noktalarda bakılır (geçersizleşme satırları,
    her bakışın istatistiği, bakış satırları) — aşılınca o gecenin bakışları BÜTÜN olarak ertelenir (`DEFERRED_BUDGET`; yarım
    bakış ailesi/BH yazılmaz, ertesi gece aynı plan yeniden kurulur)."""

    def _late() -> bool:
        return deadline is not None and clock() > deadline

    def _deferred(at_step: str) -> dict[str, Any]:
        return {"schema": RUN_SCHEMA, "status": "DEFERRED_BUDGET", "as_of": at, "looks": 0,
                "reason": f"S6 payı bitti ({at_step}); bakışlar ertesi gece"}
    ok, why = S1C.check()
    if not ok:
        raise LessonsRefused(why)
    now = now or utc_now()
    as_of_ms = int(now.timestamp() * 1000)
    at = iso(now)
    if check_s2 and rows is None:
        pend = s2_pending(paths)
        if pend is None or pend > 0:
            return {"schema": RUN_SCHEMA, "status": "S2_PENDING" if pend else "NO_ATTRIBUTION", "as_of": at, "looks": 0}
    versions = versions or current_versions()
    with _locked(paths):
        ledger = TR.TrialsLedger(paths)
        ledger.load()
        lrows = ledger_lesson_rows(TR.iter_rows(ledger.path))
        states = pair_states(lrows)
        ev = Evidence(rows if rows is not None else iter_attribution_rows(paths), attribution_sha=versions[0],
                      cfgrid_sha=versions[1], as_of_ms=as_of_ms)
        cells, meta = build_cells(ev)
        # (1) geçersizleşme → RETIRE (kuşak + 1)
        inval = _invalidations(ev, cells, states, versions)
        if _late():
            return _deferred("kanıt")
        if inval:
            ledger.append([{"kind": "RETIRE", "row_id": f"{LOOK_PREFIX}{pid}|g{g}", "verdict": "INVALIDATED", "at": at,
                            "params": {"pair_id": pid, "gen": g, "lesson_id": lid, "reasons": why, "as_of": at}}
                           for pid, g, lid, why in inval])
            lrows = ledger_lesson_rows(TR.iter_rows(ledger.path))
            states = pair_states(lrows)
        # (2) plan + üst sınır + BH (aile = gecenin bütün bakışları)
        tasks = _plan(ev, cells, meta, states, as_of_ms=as_of_ms)
        deferred = 0
        if not _raw_books(raw)[0]:                  # doğrulama hükmü canlı kural denetimi ister: config yoksa ertelenir
            deferred = sum(1 for t in tasks if t["kind"] == "VALIDATION")
            tasks = [t for t in tasks if t["kind"] != "VALIDATION"]
        tasks.sort(key=lambda t: (-len(t["idx"]), t["cell_id"], t["pair_id"]))
        overflow = max(0, len(tasks) - PAIR_CAP)
        tasks = tasks[:PAIR_CAP]
        for t in tasks:
            if _late():
                return _deferred("bakış istatistikleri")
            if t["kind"] == "VALIDATION":
                t["folds"] = folds(ev, t["idx"], t["vals"])
            t["stats"] = sample_stats(ev, t["idx"], t["vals"], ptype=t["type"], cid=t["cell_id"], row_id=t["row_id"],
                                      look_no=t["look_no"], parent_rate=_parent_rate(ev, cells, meta, t["cell_id"], t["type"]))
        if _late():
            return _deferred("bakış satırları")
        q = ST.bh_qvalues({t["row_id"]: t["stats"]["p_day"] for t in tasks})
        entries = [_look_entry(t, q[t["row_id"]], meta[t["cell_id"]], as_of_ms=as_of_ms, at=at, versions=versions, raw=raw,
                               module_src_sha=module_src_sha) for t in tasks]
        res = ledger.append(entries) if entries else []
        # (3) türeyen spec önerileri (defterdeki FWD_PASS satırlarından; belirlenimci, yeniden çalışmada tamamlanır)
        lrows = ledger_lesson_rows(TR.iter_rows(ledger.path))
        derived = ChainFile(paths, derived_file(paths), DERIVED_SCHEMA).load()
        dres = _propose(derived, lrows, module_src_sha=module_src_sha, at=at, ledger=ledger)
        # (4) aylık LIB_v1.<AAAA-AA> mührü
        seal = seal_month(paths, derived, ledger, now=now)
        # (5) depo eşitleme
        store = LessonStore(paths).load()
        store.append(store.events_from_ledger(lrows, at))
        store.append(store.events_from_derived(derived.rows, at))
        summary = {
            "schema": RUN_SCHEMA, "status": "OK", "as_of": at, "s1c_sha": S1C.S1C_SHA,
            "versions": {"attribution": versions[0], "cfgrid": versions[1]},
            "evidence": {"trades": ev.n, "excluded": dict(sorted(ev.excluded.items())), "cells": len(cells),
                         "books": sorted(set(ev.book))},
            "looks": sum(1 for r in res if r["status"] == "APPENDED"),
            "looks_by_kind": _count(t["kind"] for t in tasks), "family_m": len(tasks), "overflow_carried": overflow,
            "validation_deferred_no_config": deferred,
            "invalidated": len(inval), "verdicts": _count(e["verdict"] for e in entries),
            "derived": dres, "seal": seal, "states": _count(d.get("policy_status") for d in store.lessons.values()),
            "code_view": code_view(ev), "sanity_5_10": sanity_5_10(ev, raw=raw),
            "pending_validation": _pending_validation(states),
            "counters": ledger.counters,
        }
        paths.write_json(last_run_file(paths), summary)
    return summary


def _count(it: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in it:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items()))


def _pending_validation(states: Mapping[str, dict]) -> list[list]:
    out = []
    for pid, s in sorted(states.items()):
        if s["phase"] == "VALIDATION" and s.get("hyp"):
            out.append([pid, month_label(month_start_ms(int(s["hyp"]["t_k_ms"]), FWD_FIRST_MONTHS + 1))])
    return out[:200]


def _look_entry(t: Mapping[str, Any], q: float, meta: Mapping[str, Any], *, as_of_ms: int, at: str,
                versions: tuple[str, str], raw: Any, module_src_sha: Mapping[str, str] | None) -> dict:
    st = t["stats"]
    lid = lesson_id_of(t["pair_id"], t["gen"])
    pa = {"pair_id": t["pair_id"], "cell_id": t["cell_id"], "type": t["type"], "kind": t["kind"], "gen": t["gen"],
          "lesson_id": lid, "alpha": t["alpha"], "as_of_ms": as_of_ms, "as_of": at,
          "attribution_sha": versions[0], "cfgrid_sha": versions[1], **meta,
          **{k: st[k] for k in ("n", "n_days", "effect", "ci95", "rate", "rate_wilson", "rate_shrunk", "parent_rate",
                                "counter_n", "best", "worst", "mechanism", "cost_r_mean", "evidence_sha", "window")}}
    decision = None
    if t["kind"] == "DISCOVERY":
        verdict = discovery_verdict(st["effect"], st["p_day"], q, t["look_no"])
        if verdict == "SIGNIFICANT":
            hyp = {"pair_id": t["pair_id"], "gen": t["gen"], "look_no": t["look_no"], "t_k": at, "effect": st["effect"],
                   "p_day": st["p_day"], "q_bh": q, "n": st["n"], "evidence_sha": st["evidence_sha"], "versions": list(versions),
                   "registry_sha": RG.REGISTRY_SHA, "s1c_sha": S1C.S1C_SHA}
            pa["hypothesis_sha"] = _sha(hyp)
            decision = {"to": RESEARCH_HYPOTHESIS, "reason": f"keşif bakışı {t['look_no']}: p {st['p_day']:.4f} ≤ α "
                                                             f"{t['alpha']}, q {q:.4f} ≤ {BH_Q}"}
    else:
        fo = t["folds"]
        pa["folds"] = fo
        pa["hyp_q"] = t["hyp"].get("q")
        pa["t_k_ms"] = int(t["hyp"]["t_k_ms"])
        pa["window_end_ms"] = int(t["window_end_ms"])
        verdict = validation_verdict(st["effect"], st["p_day"], t["alpha"], bool(fo and fo["ok"]))
        if verdict == "FWD_SIGN_FLIP":
            decision = {"to": RETIRED, "reason": "ileri doğrulama: işaret döndü"}
        elif verdict == "FWD_FAIL":
            decision = {"to": REJECTED, "reason": "ileri doğrulama: p/katman şartı tutmadı"}
        else:
            prop = build_proposal(meta, t["type"], module_src_sha=module_src_sha)
            if prop["status"] == "PROPOSAL":
                prop["live"] = live_rule_check(prop, meta["book"], raw)
            pa["proposal"] = prop
            if prop.get("live", {}).get("status") == "LIVE":
                decision = {"to": VALIDATED_POLICY_CANDIDATE,
                            "reason": "ileri doğrulama geçti; kural canlıda zaten var (kural değişikliği gerekmez)"}
    pa["decision"] = decision
    return {"kind": "LESSON_LOOK", "row_id": t["row_id"], "look_no": int(t["look_no"]), "n": st["n"], "n_days": st["n_days"],
            "p_day": st["p_day"] if st["p_day"] is not None else 1.0, "q_bh": q, "verdict": verdict, "at": at,
            "data_seal": st["evidence_sha"], "code_sha": S1C.S1C_SHA,
            "window": {**st["window"], "as_of": at}, "params": pa}



def _propose(derived: ChainFile, lrows: Sequence[Mapping[str, Any]], *, module_src_sha: Mapping[str, str] | None, at: str,
             ledger: TR.TrialsLedger) -> dict[str, int]:
    """Defterdeki FWD_PASS satırlarından türeyen spec satırları (bir kez; satır = ders bakışı): EXCLUDED_LIVE (canlı kural),
    LINKED (LIB_v1 örtüsü/kopyası, önceki türeyen spec ya da defterin LIB ailesi), PROPOSED (kuyruk). Config okunamadıysa
    (UNAVAILABLE) yazılmaz; ertesi gece yeniden denenir."""
    done = {d.get("look_row_id") for d in derived.rows}
    known: dict[tuple[str, str], str] = {}
    for d in derived.rows:
        if d.get("spec_sha") and d["status"] in ("PROPOSED", "SEALED", "LINKED"):
            known.setdefault((d["spec_sha"], d["scope"]), d.get("linked_to") or d["row_id"])
    lib = lib_spec_index(module_src_sha)
    ledger_trials = {(r.get("spec_sha"), r.get("scope")): f"{r.get('lib_version')}:{r.get('row_id')}"
                     for r in TR.iter_rows(ledger.path) if r.get("kind") in ("SPEC", "OPTION")}
    new: list[dict] = []
    out = {"PROPOSED": 0, "LINKED": 0, "EXCLUDED_LIVE": 0, "DEFERRED_UNAVAILABLE": 0}
    for r in lrows:
        pa = r.get("params") or {}
        if r.get("verdict") != "FWD_PASS" or r["row_id"] in done:
            continue
        prop = pa.get("proposal") or {}
        if prop.get("status") != "PROPOSAL":
            continue
        live = (prop.get("live") or {}).get("status")
        if live == "UNAVAILABLE":
            out["DEFERRED_UNAVAILABLE"] += 1
            continue
        base = {"at": at, "lesson_id": pa["lesson_id"], "pair_id": pa["pair_id"], "look_row_id": r["row_id"],
                "look_seq": int(r["seq"]), "transform": {"T": prop["transform"], "name": prop["name"], "arg": prop["arg"],
                                                         "base": prop["base"]},
                "spec": prop["spec"], "spec_sha": prop["spec_sha"], "scope": prop["scope"], "row_id": prop["row_id"],
                "q": pa.get("hyp_q"), "delta": pa.get("effect"),
                "live": prop.get("live")}
        key = (prop["spec_sha"], prop["scope"])
        if live == "LIVE":
            status, link = "EXCLUDED_LIVE", None
        elif key in lib:
            status, link = "LINKED", f"{RG.LIB_VERSION}:{lib[key]}"
        elif key in known:
            status, link = "LINKED", known[key]
        elif key in ledger_trials:
            status, link = "LINKED", ledger_trials[key]
        else:
            status, link = "PROPOSED", None
            known[key] = prop["row_id"]
        new.append({**base, "status": status, "linked_to": link})
        out[status] += 1
    if new:
        derived.append(new)
    return out


def is_seal_night(paths: EnginePaths, now: datetime) -> bool:
    """Her ayın 1'inden sonraki ilk gece: gün > 1 ve o ay için manifest yok."""
    return now.astimezone(UTC).day > 1 and not manifest_file(paths, now.astimezone(UTC).strftime("%Y-%m")).exists()


def seal_month(paths: EnginePaths, derived: ChainFile, ledger: TR.TrialsLedger, *, now: datetime) -> dict[str, Any]:
    """Aylık `LIB_v1.<AAAA-AA>`: kuyruktan (PROPOSED, henüz mühürlenmemiş) en çok 20 spec (q artan, |Δ| azalan, spec_sha);
    her biri `SPEC` deneme satırı (defter LIB ailesinde tekilleştirir) + `SEALED` satırı + manifest (bir kez yazılır).
    Sığmayan kuyrukta kalır. Yeniden çalışmada kaldığı yerden tamamlanır."""
    if not is_seal_night(paths, now):
        return {"sealed": False}
    month = now.astimezone(UTC).strftime("%Y-%m")
    ver = f"{RG.LIB_VERSION}.{month}"
    sealed_keys = {(d["spec_sha"], d["scope"]) for d in derived.rows if d["status"] in ("SEALED", "SEAL_LINKED")}
    queue = [d for d in derived.rows if d["status"] == "PROPOSED" and (d["spec_sha"], d["scope"]) not in sealed_keys]
    uniq: dict[tuple[str, str], dict] = {}
    for d in queue:
        uniq.setdefault((d["spec_sha"], d["scope"]), d)
    order = sorted(uniq.values(), key=lambda d: (1.0 if d.get("q") is None else float(d["q"]),
                                                 -abs(float(d.get("delta") or 0.0)), d["spec_sha"], d["scope"]))
    already = [d for d in derived.rows if d.get("lib_version") == ver]          # yeniden çalışma: aylık tavan korunur
    room = max(0, MAX_SPECS_PER_MONTH - len(already))
    take, carried = order[:room], max(0, len(order) - room)
    t_seal = (int(now.timestamp() * 1000) // 60_000) * 60_000
    res = ledger.append([{"kind": "SPEC", "lib_version": ver, "spec_sha": d["spec_sha"], "scope": d["scope"],
                          "row_id": d["row_id"], "at": iso(now),
                          "params": {"lesson_id": d["lesson_id"], "transform": d["transform"], "t_seal_ms": t_seal,
                                     "derived_seq": d["seq"]}} for d in take]) if take else []
    own = {int(r["seq"]): r for r in TR.iter_rows(ledger.path) if r.get("kind") == "SPEC"} if res else {}
    rows = []
    for d, rr in zip(take, res):
        prev = own.get(int(rr["seq"])) or {}
        mine = rr["status"] == "APPENDED" or (prev.get("lib_version") == ver
                                              and (prev.get("params") or {}).get("derived_seq") == d["seq"])
        rows.append({"at": iso(now), "lesson_id": d["lesson_id"], "pair_id": d["pair_id"], "look_row_id": d["look_row_id"],
                     "transform": d["transform"], "spec": d["spec"], "spec_sha": d["spec_sha"], "scope": d["scope"],
                     "row_id": d["row_id"], "q": d.get("q"), "delta": d.get("delta"), "live": d.get("live"),
                     "status": "SEALED" if mine else "SEAL_LINKED", "lib_version": ver, "t_seal_ms": t_seal,
                     "trial_seq": rr["seq"], "linked_to": None if mine else f"trials:{rr['seq']}"})
    if rows:
        derived.append(rows)
    specs = [{"row_id": r["row_id"], "spec_sha": r["spec_sha"], "scope": r["scope"], "lesson_id": r["lesson_id"],
              "status": r["status"], "trial_seq": r["trial_seq"], "t_seal_ms": r["t_seal_ms"]} for r in already + rows]
    doc = {"schema": MANIFEST_SCHEMA, "lib_version": ver, "parent": RG.LIB_VERSION, "registry_sha": RG.REGISTRY_SHA,
           "s1c_sha": S1C.S1C_SHA, "t_seal_ms": t_seal, "specs": specs, "carried": carried}
    doc["sha"] = _sha(doc)
    mf = manifest_file(paths, month)
    if not mf.exists():
        paths.write_bytes(mf, RG.canonical_bytes(doc) + b"\n")
    return {"sealed": True, "lib_version": ver, "specs": len(specs), "carried": carried}


# ============================================================================ betimsel görünümler (hüküm yok, deneme yok)
def code_view(ev: Evidence, *, top: int = 6) -> dict[str, Any]:
    """Defter × dönem × atıf kodu: n, ortalama/toplam net R, kayıplardaki payı; kayıp kodunda o işlemlerde ortalama eşli
    Δ'sı en yüksek EX_ANTE hücresi. Kod sonuçtan türer → yalnız betimsel (bakış/aday değil)."""
    by: dict[tuple[str, str], list[int]] = {}
    for i in range(ev.n):
        by.setdefault((ev.book[i], ev.epoch[i]), []).append(i)
    out: dict[str, Any] = {}
    for (b, e), ix in sorted(by.items()):
        losses = sum(1 for i in ix if ev.net_r[i] <= 0)
        cnt: dict[str, list[int]] = {}
        for i in ix:
            for c in ev.codes_loss[i] + ev.codes_win[i]:
                cnt.setdefault(c, []).append(i)
        rows = []
        for c, ci in sorted(cnt.items(), key=lambda x: (-len(x[1]), x[0]))[:top]:
            arr = np.array(ci, dtype=np.int64)
            g = ev.grid[arr]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)          # tümü NaN sütun (hücre yok) → NaN
                means = np.nanmean(g, axis=0)
            best = None
            if not np.all(np.isnan(means)):
                j = int(np.nanargmax(means))
                best = [GRID_CELLS[j], _r(float(means[j]), 4)]
            rows.append({"code": c, "n": len(ci), "mean_r": _r(float(ev.net_r[arr].mean()), 4),
                         "sum_r": _r(float(ev.net_r[arr].sum()), 2), "loss_share": _r(len(ci) / losses, 3) if losses else None,
                         "best_ex_ante": best})
        out[f"{b}|{e}"] = {"n": len(ix), "losses": losses, "sum_r": _r(float(ev.net_r[ix].sum()), 2), "codes": rows}
    return out


def epoch_regimes(book: str, param: str, epochs: Mapping[str, Any] = RG.CONFIG_EPOCHS_V1) -> dict[str, str]:
    """Dönem → rejim kimliği: aynı defterin ardışık dönemleri, sonraki dönemin (TRANSITION dahil) `opened_by`'ı parametreyi
    içermiyorsa aynı rejimdir; PRE_HISTORY ya da parametreyi açan dönem yeni rejim başlatır. TRANSITION'a işlem atanmaz
    (rehydrate). Rejim kimliği = rejimin ilk dönemi."""
    eps = sorted((e for e in epochs["epochs"] if book in (e.get("books") or [])), key=lambda e: str(e.get("from")))
    out: dict[str, str] = {}
    cur = None
    for e in eps:
        if e.get("kind") == "PRE_HISTORY" or param in (e.get("opened_by") or []):
            cur = None
        if e.get("kind") == "TRANSITION":
            continue
        cur = cur or e["id"]
        out[e["id"]] = cur
    return out


def _stop_bucket(x: float) -> str | None:
    for name, lo, hi in SANITY["buckets"]:
        if lo <= x < hi:
            return name
    return None


def sanity_5_10(ev_or_rows: Any, *, raw: Any = None, epochs: Mapping[str, Any] = RG.CONFIG_EPOCHS_V1) -> dict[str, Any]:
    """§5.10 (aday DEĞİL): Box stop genişliği kovaları config dönemi BAŞINA ve aynı min_stop rejiminin dönemleri
    birleştirilerek. Rejimin yeniden üretimi: dar kova ortalaması < 0 < %0,5–1 kovası ve dar kovanın en sık kayıp kodu
    beklenen kodlardan biri. Ham config'te min_stop_pct ≥ 0,5 → APPLIED_DECISION_MONITOR (aday yok)."""
    rows = ev_or_rows.sanity_rows if isinstance(ev_or_rows, Evidence) else ev_or_rows
    reg = epoch_regimes(SANITY["book"], SANITY["param"], epochs)
    per_epoch: dict[str, dict] = {}
    per_regime: dict[str, dict] = {}
    for r in rows or []:
        if str(r.get("book")) != SANITY["book"] or _f(r.get("net_r")) is None or _f(r.get("stop_dist_pct")) is None \
                or not r.get("config_epoch"):
            continue
        b = _stop_bucket(float(r["stop_dist_pct"]))
        ep = str(r.get("config_epoch") or "?")
        codes = list((r.get("attribution") or {}).get("codes_loss") or [])
        for key, acc in ((ep, per_epoch), (reg.get(ep, f"UNMAPPED:{ep}"), per_regime)):
            a = acc.setdefault(key, {}).setdefault(b, {"n": 0, "sum_r": 0.0, "codes": {}})
            a["n"] += 1
            a["sum_r"] += float(r["net_r"])
            for c in codes:
                a["codes"][c] = a["codes"].get(c, 0) + 1

    def fin(acc: dict) -> dict:
        res = {}
        for k, bs in sorted(acc.items()):
            res[k] = {b: {"n": v["n"], "mean_r": _r(v["sum_r"] / v["n"], 4),
                          "codes": dict(sorted(v["codes"].items(), key=lambda x: (-x[1], x[0]))[:4])}
                      for b, v in sorted(bs.items())}
        return res
    pe, pr = fin(per_epoch), fin(per_regime)
    ex = SANITY["expected"]
    anchor = reg.get(SANITY["anchor_epoch"], SANITY["anchor_epoch"])
    a = pr.get(anchor, {})
    nar, wid = a.get(ex["narrow"]), a.get(ex["wide"])
    if not nar or not wid:
        status = "INSUFFICIENT"
    else:
        top = next(iter(nar["codes"]), None)
        status = "REPRODUCED" if (nar["mean_r"] < 0 < wid["mean_r"] and top in ex["narrow_codes"]) else "NOT_REPRODUCED"
    ok, _v, books = _raw_books(raw)
    cur = _f((books.get(SANITY["rule"]) or {}).get("min_stop_pct")) if ok else None
    live = "APPLIED_DECISION_MONITOR" if cur is not None and cur >= float(SANITY["live_min"]) else (
        "LIVE_UNKNOWN" if cur is None else "NOT_APPLIED")
    later = sorted({v for v in reg.values() if v != anchor and v > anchor})
    return {"anchor_regime": anchor, "regime_epochs": sorted(k for k, v in reg.items() if v == anchor), "status": status,
            "per_epoch": pe, "per_regime": pr, "live_rule": {"status": live, "min_stop_pct": cur, "key": SANITY["param"]},
            "monitor_regimes": later, "candidate": None, "reference_real": RG.thaw(SANITY["reference_real"])}


# ============================================================================ engine-query lessons (salt-okunur)
def _short(pid: str) -> str:
    parts = dict(p.split("=", 1) for p in pid.split("#")[0].split("|")[1:] if "=" in p)
    lv = pid.split("|", 1)[0]
    bits = [lv, parts.get("B", "?").replace("strategy_paper", "sp")] + [parts[k] for k in ("T", "I", "X") if k in parts]
    return " ".join(bits) + " " + pid.rsplit("#", 1)[-1]


def _fmt(x: Any) -> str:
    return "—" if x is None else f"{float(x):.4f}"


def query_lines(paths: EnginePaths, *, book: str | None = None, like: str | None = None) -> list[str]:
    """`engine-query lessons [--book B] [--like metin]` — kilit almaz, yazmaz; ≤ 150 satır / ≤ 4 KB."""
    lines = ["DERSLER (P3f) — salt-okunur; hüküm yalnız kayıtlı bakışlarda"]
    try:
        store = LessonStore(paths).load()
    except LessonStoreBroken as exc:
        return TR.bound(lines + [f"DEPO KIRIK: {exc}"])
    ls = list(store.lessons.values())
    if book:
        ls = [d for d in ls if (d.get("scope") or {}).get("book") == book]
    if like:
        ls = [d for d in ls if like.lower() in json.dumps(d.get("observation_tr", ""), ensure_ascii=False).lower()
              or like.lower() in str(d.get("pair_type", "")).lower()]
    st = _count(d.get("policy_status") for d in ls)
    lines.append("Ders: " + str(len(ls)) + " · " + (" · ".join(f"{k} {v}" for k, v in st.items()) or "yok"))
    tst = TR.verify_chain(paths.trials_file)
    lines.append(f"Deneme defteri: {tst['code']} · N_lesson_cum {tst['counters']['N_lesson_cum']} · "
                 f"N_raw_cum {tst['counters']['N_raw_cum']}")
    try:
        last = json.loads(last_run_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    if last:
        lines.append(f"Son gece {str(last.get('as_of'))[:16]}: bakış {last.get('looks', 0)} "
                     f"{last.get('looks_by_kind', {})} · aile {last.get('family_m', 0)} · taşan "
                     f"{last.get('overflow_carried', 0)} · geçersiz {last.get('invalidated', 0)}")
    hyp = sorted((d for d in ls if d.get("policy_status") in (RESEARCH_HYPOTHESIS, VALIDATED_POLICY_CANDIDATE)),
                 key=lambda d: (d.get("evidence", {}).get("q_bh") or 1.0, d.get("lesson_id")))
    if hyp:
        lines.append("Hipotez/aday (q artan):")
        for d in hyp[:12]:
            e = d.get("evidence") or {}
            lines.append(f"  {d['policy_status'][:4]} {_short(d['observation']['pair_id'])[:60]} n {e.get('n')} "
                         f"Δ {_fmt(e.get('mean_r'))} q {_fmt(e.get('q_bh'))} {d.get('fwd_validation') or ''}".rstrip())
    if last and last.get("pending_validation"):
        pv = last["pending_validation"]
        lines.append(f"Doğrulama bekleyen {len(pv)}: " + "; ".join(f"{_short(p)[:40]} ≥ {m}" for p, m in pv[:4]))
    try:
        drows = ChainFile(paths, derived_file(paths), DERIVED_SCHEMA).load().rows
    except LessonStoreBroken as exc:
        drows = []
        lines.append(f"derived_specs KIRIK: {exc}")
    ds = _count(d["status"] for d in drows)
    months = sorted(p.name for p in library_dir(paths).glob(f"{RG.LIB_VERSION}.????-??.json")) if library_dir(paths).is_dir() else []
    lines.append("Türeyen spec: " + (" · ".join(f"{k} {v}" for k, v in ds.items()) or "yok")
                 + (f" · son {months[-1][:-5]}" if months else ""))
    if last and last.get("code_view"):
        lines.append("Defter × kod (betimsel; kod sonuçtan türer, aday değil):")
        for k, v in sorted(last["code_view"].items()):
            if book and not k.startswith(book + "|"):
                continue
            top = ", ".join(f"{c['code']} {c['n']}" for c in v["codes"][:3]) or "—"
            lines.append(f"  {k[:44]} n {v['n']} ΣR {v['sum_r']}: {top}")
    if last and last.get("sanity_5_10"):
        s = last["sanity_5_10"]
        lines.append(f"§5.10 Box min_stop rejimi {s['anchor_regime']}: {s['status']} · canlı {s['live_rule']['status']} "
                     f"(aday yok)")
    return TR.bound(lines, max_lines=QUERY_MAX_LINES, max_bytes=QUERY_MAX_BYTES)


__all__ = ["ALPHAS", "BH_Q", "ChainFile", "Evidence", "GRID_CELLS", "LessonStore", "LessonStoreBroken", "LessonsRefused",
           "N_THRESHOLDS", "PAIR_CAP", "PAIR_TYPES", "RunnerForbidden", "build_cells", "build_proposal", "cell_id",
           "code_view", "day_ci", "derived_file", "discovery_verdict", "epoch_regimes", "evidence_digest", "folds",
           "is_seal_night", "lesson_id_of", "lib_spec_index", "live_rule_check", "manifest_file", "month_start_ms",
           "pair_states", "query_lines", "run_lessons", "runner_transition", "sample_stats", "sanity_5_10", "seal_month",
           "store_file", "validation_look_no", "validation_verdict"]

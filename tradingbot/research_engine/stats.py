"""P3d — istatistik ve Kapı A bakışı (docs/ENGINE_BRAIN_V1.md §3.6, §3.7; ana belge §6.4, §6.7; mühür `STATS_V1`).

* **`p_day`** (tek istatistik): H0 "ortalama net R ≤ 0", ADVERSE, GÜN KÜMELENMELİ tek yönlü bootstrap. Günler (işlemin
  giriş UTC günü; aşağıdaki okunuş) yeniden örneklenir: her tekrar D günden D gün çeker (yerine koyarak), tekrar ortalaması =
  çekilen günlerin R toplamı / işlem sayısı. Dağılım sıfır ortalamaya kaydırılır (her işlemin R'ından gözlenen ortalama m
  çıkarılır) ve p = (1 + #{kaydırılmış tekrar ortalaması ≥ m}) / (B + 1) (hiç 0 değil; `daily_target.p_tgt` ile aynı
  kestirici). B = 10.000 (mühür), 1.000'lik parçalarla; bellek ≤ 16 MB (alt parça boyu D'ye göre).
  **Üreteç (B6, mühür):** `random.Random(int(sha256(f"{row_id}|{look_no}").hexdigest()[:16], 16))`; gün çekimi
  `rng.choices(range(D), k=D)` ile AYNI akıştır (`floor(random() × D)`): hız için aynı Mersenne Twister durumu numpy'ye
  aktarılır (`_mt`); başvuru uygulamasıyla eşitlik testlidir. **Kesin karşılaştırma:** R mikro-R'ye yuvarlanır (tam sayı;
  işlem başına |hata| ≤ 5e-7 R) ve "kaydırılmış ortalama ≥ m" ⟺ ΣS* · N ≥ 2 · S · Σn* tam sayıyla sınanır (kayan nokta
  sırası p'yi oynatamaz).
* **BH-FDR** (q = 0,10, mühür): aile = o bakışta değerlendirilen BÜTÜN etkin LIB satırları (`registry.gate_a_rows`, 818);
  `p_day`'i olmayan satır p = 1 ile girer (m küçülmez). q-değeri = min_{j ≥ i} (m / j · p_(j)), 1'de kırpılır.
* **DSR:** `validation.deflated_sharpe` (yeniden kullanılır); sr = OOS günlük getirinin Sharpe'ı (1 USDT risk / 200 USDT
  taban; işlemsiz gün 0; ADVERSE net USDT, giriş günü), T = OOS gün sayısı, çarpıklık/basıklık ampirik (basıklık fazlalık
  değil), **`sr_var` = ailenin (o bakışta Sharpe'ı hesaplanan satırlar) günlük Sharpe'larının örneklem varyansı, tabanı
  1/(T − 1)** (fonksiyonun 0,01 varsayılanı KULLANILMAZ); N = deneme defterinin o anki `dsr_n()`'i.
* **Kapı A bakışı** (`gate_a_look`; §3.7 yordamı): yalnız bakış gecesinde (her ayın 5'inden sonraki ilk gece; o ay
  defterde `LOOK_A` yoksa) — ara gecelerde hüküm kelimesi yok ("ara görünüm", deftere yazılmaz). Sıra: (1) zincir ve mühür
  denetimi; (2) satır istatistikleri (üç maliyet, `p_day`, plasebo, katman payı, pay/tutarlılık); (3) PBO; (4) BH; (5)
  DSR; (6) ileri okuma (`FWD_READ`); (7) `PASS_A`/`FAIL_A` → `LOOK_A`. Defter yalnız `trials.TrialsLedger.append` ile
  büyür (yeniden yazma yolu yok).
* **Eşikler:** Kapı A eşikleri ana belge §6.7'ye göre S2'de (`PROMOTION_REGISTRY_V1`, P4a) mühürlenir; S1 yalnız q, plasebo
  alt sınırı, tek enstrüman payı ve küme tutarlılığını taşır. `GATE_A_PROVISIONAL` = S1 değerleri + §3.7 METNİNİN geri
  kalanı, `sealed: False` — her `LOOK_A` satırı kaynağını yazar; S2 mühürlenince yerine `PROMOTION_REGISTRY_V1` geçer.
  Plasebo CI'ının tekrar sayısı/tohumu S1'de yoktur (bkz. `placebo.diff_ci`; §8.4 P3d notu).

Kapı istatistiğine giren işlemler `wf` tarafından süzülmüştür (`kernel.gate_excluded`: FUNDING_GAP, PATH_1H_GAP,
F8_UNMEASURABLE); çıkarılanların sayısı satırda taşınır. Ağsız.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from . import cscv as CS
from . import registry as RG
from ..validation import deflated_sharpe

DAY_MS = 86_400_000
_ST = RG.STATS_V1
_TH = _ST["thresholds"]
RESAMPLES = int(_TH["p_day"]["resamples"])                     # 10.000
CHUNK = int(_ST["p_day"]["chunk_resamples"])                    # 1.000
MEMORY_MB_MAX = int(_ST["p_day"]["memory_mb_max"])              # 16
BH_Q = float(_TH["bh_q"])                                       # 0,10
PLACEBO_K = int(_TH["placebo"]["k_per_trade"])                  # 5
PLACEBO_CI_LEVEL = float(_TH["placebo"]["ci_level"])            # 0,95
PLACEBO_MIN_CI_LOWER = float(_TH["placebo"]["min_ci_lower"])    # 0
NULL_WORLDS = int(_TH["null_world"]["worlds"])                  # 200
EFFECTIVE_N_ABS_RHO = float(_TH["effective_n_abs_rho"])         # 0,7 (yalnız ikincil sütun)
GATE_COST = next(k for k, v in RG.WF_PROTOCOL_V1["costs"]["scenarios"].items() if v.get("gate"))   # ADVERSE
SCENARIOS: tuple[str, ...] = tuple(RG.WF_PROTOCOL_V1["thresholds"]["costs"])                     # base, ADVERSE, STRESS
#: DSR günlük getirisi: 1 USDT risk, 200 USDT taban (LIB_V1.sizing)
EQUITY_BASE_USDT = float(RG.LIB_V1["sizing"]["base_equity_usdt"])
R_SCALE = 1_000_000                                             # mikro-R (tam sayı karşılaştırma)
_I62 = 1 << 62

PASS_A, FAIL_A = "PASS_A", "FAIL_A"
INTERIM_LABEL = "ara görünüm"
#: Kapı A bakış gecesi: ayın bu gününden SONRAKİ ilk gece (STATS_V1.looks_tr: "her ayın 5'inden sonraki ilk gece")
LOOK_AFTER_DAY = 5

#: S2 mühürlenene kadar Kapı A eşikleri (ana belge §6.7 / ENGINE_BRAIN §3.7 metni). `sealed: False` — mühür DEĞİL.
GATE_A_PROVISIONAL: Mapping[str, Any] = MappingProxyType({
    "sealed": False,
    "source": "PROVISIONAL_UNSEALED: S1 (q_bh, plasebo alt sınırı, tek enstrüman payı, küme tutarlılığı) + ENGINE_BRAIN "
              "§3.7 / ana belge §6.7 metni; S2 PROMOTION_REGISTRY_V1 (P4a) mühürlenince yerine geçer",
    "q_bh_max": BH_Q,
    "min_oos_trades": 100, "min_oos_days": 730,                  # ≥ 2 takvim yılı
    "gold_min_oos_trades": 60, "gold_min_oos_days": 274,         # altın: ≥ 9 ay (⌈9 × 365,25 / 12⌉)
    "min_positive_fold_share": 0.60,
    "pbo_max_exclusive": 0.25,
    "dsr_min": 0.95,
    "placebo_ci_lower_min_exclusive": PLACEBO_MIN_CI_LOWER,
    "fwd_min_days": 60, "fwd_min_days_intraday": 30, "fwd_min_trades": 20, "fwd_min_mean_adverse": 0.0,
    "max_single_instrument_share": float(RG.LIB_V1["thresholds"]["single_instrument"]["max_oos_profit_share"]),
    "min_cluster_coherence_r": float(RG.LIB_V1["thresholds"]["cluster_coherence"]["min_ex_coin_oos_adverse_mean_r"]),
})


# ============================================================================ tohum ve üreteç (B6)
def seed_int(key: str) -> int:
    """B6 (cfgrid_v1 biçimi): sha256(anahtar)'ın ilk 8 baytı → tam sayı."""
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)


def p_day_seed(row_id: str, look_no: int) -> int:
    return seed_int(f"{row_id}|{look_no}")


def _mt(seed: int) -> np.random.RandomState:
    """`random.Random(seed)` ile AYNI Mersenne Twister durumunda numpy üreteci (`random_sample` = `random()`)."""
    st = random.Random(seed).getstate()[1]
    rs = np.random.RandomState()
    rs.set_state(("MT19937", np.array(st[:624], dtype=np.uint32), int(st[624])))
    return rs


def _block_rows(d: int, arrays: int) -> int:
    """Bir alt parçada kaç tekrar: `arrays` adet tekrar × D × 8 baytlık dizi ≤ bellek tavanının yarısı."""
    budget = MEMORY_MB_MAX * 1024 * 1024 // 2
    return max(1, min(CHUNK, budget // max(1, d * 8 * arrays)))


def choice_stream(seed: int, d: int, total: int, *, rows: int | None = None) -> Iterable[np.ndarray]:
    """`random.Random(seed).choices(range(d), k=d)` × `total` tekrarının indisleri, (satır = tekrar) parça parça. Parça
    boyu akışı değiştirmez (aynı sırayla çekilir); varsayılan boy bellek tavanından."""
    rs = _mt(seed)
    rows = _block_rows(d, 3) if rows is None else max(1, int(rows))
    done = 0
    fd = float(d)
    while done < total:
        k = min(rows, total - done, CHUNK - (done % CHUNK))
        u = rs.random_sample((k, d))
        u *= fd
        yield u.astype(np.int64)                                # u ≥ 0 → kesme = floor
        done += k


# ============================================================================ p_day
def day_clusters(trades: Iterable[tuple[int, float]]) -> tuple[list[int], list[int], list[int]]:
    """(gün, R) → (günler sıralı, gün başına mikro-R toplamı, gün başına işlem sayısı)."""
    acc: dict[int, list[int]] = {}
    for day, r in trades:
        v = float(r)
        if not math.isfinite(v):
            raise ValueError("p_day: sonlu olmayan R")
        a = acc.setdefault(int(day), [0, 0])
        a[0] += int(round(v * R_SCALE))
        a[1] += 1
    days = sorted(acc)
    return days, [acc[d][0] for d in days], [acc[d][1] for d in days]


def p_day(trades: Iterable[tuple[int, float]], *, row_id: str, look_no: int, resamples: int = RESAMPLES,
          stop_above: float | None = None) -> float | None:
    """`trades` = [(giriş UTC günü, ADVERSE R)] (kapı dışı işlemler çıkarılmış). < 2 gün → None (BH'de p = 1).

    Kesin karşılaştırma: kaydırılmış tekrar ortalaması ≥ m ⟺ Σ_{çekilen günler} (S_d · N − 2 · S · n_d) ≥ 0 (tam sayı).
    `stop_above` (yalnız hüküm-eşdeğer hızlı kip, `gate_a_evaluate(full=False)`): aşım sayısı p'yi bu değerin üstüne
    taşıdığı an durur ve `math.inf` döner (p > stop_above kesin; deftere yazılmaz)."""
    _days, s, n = day_clusters(trades)
    d = len(s)
    if d < 2:
        return None
    s_tot, n_tot = sum(s), sum(n)
    w = [si * n_tot - 2 * s_tot * ni for si, ni in zip(s, n)]
    limit = None if stop_above is None else int(math.floor(stop_above * (resamples + 1))) - 1
    seed = p_day_seed(row_id, look_no)
    count = 0
    if max(abs(x) for x in w) * d < _I62:
        wa = np.array(w, dtype=np.int64)
        for idx in choice_stream(seed, d, resamples, rows=64 if limit is not None else None):
            count += int(np.count_nonzero(wa[idx].sum(axis=1) >= 0))
            if limit is not None and count > limit:
                return math.inf
    else:                                                     # taşma riski: Python tam sayıları (aynı akış)
        for idx in choice_stream(seed, d, resamples):
            for row in idx.tolist():
                count += sum(w[i] for i in row) >= 0
            if limit is not None and count > limit:
                return math.inf
    return (1 + count) / (resamples + 1)


# ============================================================================ BH
def bh_qvalues(pvals: Mapping[str, float | None]) -> dict[str, float]:
    """Benjamini–Hochberg q-değerleri; None → p = 1 (m küçülmez). Eşit p'de satır kimliği sırası (belirlenimci)."""
    items = sorted(((1.0 if p is None else float(p)), k) for k, p in pvals.items())
    m = len(items)
    out: dict[str, float] = {}
    run = 1.0
    for i in range(m - 1, -1, -1):
        p, k = items[i]
        run = min(run, p * m / (i + 1))
        out[k] = min(1.0, run)
    return out


# ============================================================================ günlük getiri, Sharpe, DSR
def daily_returns(trades: Iterable[tuple[int, float]], oos_days: Sequence[int], *, equity: float = EQUITY_BASE_USDT
                  ) -> dict[int, float]:
    """(giriş günü, ADVERSE net USDT) → {OOS günü: Σ net / taban}; işlemsiz gün 0. OOS dışı gün hata."""
    out = {int(d): 0.0 for d in oos_days}
    for day, pnl in trades:
        d = int(day)
        if d not in out:
            raise ValueError(f"işlem günü {d} OOS ekseninde değil")
        out[d] += float(pnl) / equity
    return out


def sharpe(x: Sequence[float]) -> float | None:
    """Ortalama / örneklem std (ddof = 1); < 2 gözlem ya da std = 0 → None."""
    n = len(x)
    if n < 2:
        return None
    a = np.asarray(x, dtype=float)
    sd = float(a.std(ddof=1))
    if not sd > 0:
        return None
    return float(a.mean()) / sd


def moments(x: Sequence[float]) -> tuple[float, float]:
    """(çarpıklık, basıklık) — ampirik (popülasyon momentleri; basıklık fazlalık DEĞİL: normal = 3)."""
    a = np.asarray(x, dtype=float)
    c = a - a.mean()
    m2 = float((c * c).mean())
    if not m2 > 0:
        return 0.0, 3.0
    return float((c ** 3).mean()) / m2 ** 1.5, float((c ** 4).mean()) / m2 ** 2


def family_sr_var(sharpes: Iterable[float | None]) -> float | None:
    """Ailenin günlük Sharpe'larının örneklem varyansı (None'lar atlanır; < 2 → None)."""
    xs = [float(s) for s in sharpes if s is not None and math.isfinite(float(s))]
    if len(xs) < 2:
        return None
    return float(np.var(np.asarray(xs), ddof=1))


def sr_var_floored(family_var: float | None, t: int) -> float:
    """`STATS_V1.dsr.sr_var_floor` = 1/(T − 1): ailenin varyansı ile taban'ın büyüğü."""
    floor = 1.0 / (t - 1) if t > 1 else math.inf
    return max(floor, family_var if family_var is not None else 0.0)


def dsr(sr: float | None, *, n_trials: int, t: int, skew: float, kurt: float, sr_var: float) -> float | None:
    """`validation.deflated_sharpe` (ampirik `sr_var` ile; fonksiyonun varsayılan tabanı kullanılmaz)."""
    if sr is None or t <= 1:
        return None
    return float(deflated_sharpe(sr, n_trials=int(n_trials), T=int(t), skew=skew, kurt=kurt, sr_var=float(sr_var)))


# ============================================================================ bakış takvimi
def look_month(night: date) -> str:
    return f"{night.year:04d}-{night.month:02d}"


def is_look_night(night: date, looked_months: Iterable[str]) -> bool:
    """Her ayın 5'inden sonraki ilk gece: gün > 5 ve o ay için henüz `LOOK_A` yok."""
    return night.day > LOOK_AFTER_DAY and look_month(night) not in set(looked_months)


def ledger_looks(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Defterdeki `LOOK_A` satırlarının bakış ayları (yazım sırasıyla, tekil)."""
    seen: list[str] = []
    for r in rows:
        if r.get("kind") == "LOOK_A":
            m = (r.get("window") or {}).get("look_month")
            if m and m not in seen:
                seen.append(m)
    return seen


# ============================================================================ seçim grupları (STATS_V1.pbo.groups)
def _copy_base(variant: Mapping[str, Any]) -> str | None:
    for req in variant.get("requires") or ():
        if str(req).startswith("copy_books."):
            return str(req).split(".", 1)[1]
    return None


def selection_group_key(row_type: str, unit: str, scope: str) -> str:
    """Mühürlü seçim grupları: standart → aynı kapsamdaki ailenin bütün varyantları; `COIN_FIT` → o coin'deki ızgara;
    `COIN` → kopyanın 23 coin satırı; kopya/örtü havuz ve küme satırı → {kopya + örtüleri} (C4'te sekiz varyasyon); altın
    enstrüman başına 6 varyant, XSEC 5, spread 2 → aile × kapsam."""
    if row_type == "COIN_FIT":
        return f"GRID:{unit}@{scope}"
    v = RG.variants_by_id()[unit]
    if row_type == "COPY" and scope.startswith("COIN:"):
        return f"COINSEL:{unit}"
    if row_type == "COPY":
        return f"COVER:{_copy_base(v)}@{scope}"
    if row_type == "COVER":
        return f"COVER:{v['cover']['base']}@{scope}"
    return f"FAM:{v['family']}@{scope}"


# ============================================================================ Kapı A
def _is_gold(row: Mapping[str, Any]) -> bool:
    return row.get("wf_class") == "GOLD_FUT"


def _is_coin_row(row: Mapping[str, Any]) -> bool:
    return row.get("row_type") == "COIN_FIT" or str(row.get("scope", "")).startswith("COIN:")


def _is_xsec(row: Mapping[str, Any]) -> bool:
    return str(row.get("scope")) == "PORT_XSEC"


def top_share(instrument_net_r: Mapping[str, float]) -> float | None:
    """§2.4-4 (B11): en büyük enstrüman net R'ı / pozitif enstrüman net R'larının toplamı; toplam ≤ 0 → None."""
    pos = sum(v for v in instrument_net_r.values() if v > 0)
    if not pos > 0:
        return None
    return max(instrument_net_r.values()) / pos


def row_stats(row: Mapping[str, Any], *, look_no: int, p_stop_above: float | None = None) -> dict[str, Any]:
    """(2) satırın OOS istatistikleri: n, gün, üç maliyet ortalaması, `p_day`, günlük Sharpe/moment, katman payı, pay.
    `p_stop_above`: hızlı kip (bkz. `p_day`); aşılırsa `p_day` None ve `p_day_state` = "CAPPED"."""
    tr = row["trades"]
    n = len(tr)
    days = list(row["oos_days"])
    t = len(days)
    out: dict[str, Any] = {"n": n, "n_days": t}
    for sc in SCENARIOS:
        out[f"mean_r_{sc.lower()}"] = (sum(float(x["r"][sc]) for x in tr) / n) if n else None
    p = p_day(((x["day"], x["r"][GATE_COST]) for x in tr), row_id=row["row_id"], look_no=look_no, stop_above=p_stop_above)
    out["p_day_state"] = "CAPPED" if p == math.inf else ("OK" if p is not None else "NOT_COMPUTABLE")
    out["p_day"] = None if p == math.inf else p
    dr = daily_returns(((x["day"], x["pnl"][GATE_COST]) for x in tr), days)
    series = [dr[d] for d in sorted(dr)]
    out["daily"] = dr
    out["sr"] = sharpe(series)
    out["skew"], out["kurt"] = moments(series) if t else (0.0, 3.0)
    folds = [f for f in row["folds"] if not f.get("leak") and f.get("n", 0) >= int(
        RG.WF_PROTOCOL_V1["thresholds"]["positive_fold_min_trades"])]
    out["pos_fold_share"] = (sum(1 for f in folds if (f.get("mean_adverse") or 0.0) > 0) / len(folds)) if folds else None
    out["n_folds_eligible"] = len(folds)
    out["top_share"] = top_share(row.get("instrument_net_r") or {}) if not _is_coin_row(row) else None
    return out


def gate_a_evaluate(rows: Mapping[str, Mapping[str, Any]], *, look_no: int, n_trials: int,
                    thresholds: Mapping[str, Any] = GATE_A_PROVISIONAL, family: Sequence[str] | None = None,
                    group_series: Mapping[str, Mapping[str, Mapping[int, float]]] | None = None,
                    placebo: Mapping[str, Any] | None = None, fwd: Mapping[str, Mapping[str, Any]] | None = None,
                    pit_ready: bool = False, epoch_ok: Mapping[str, bool] | None = None,
                    full: bool = True) -> dict[str, Any]:
    """Saf Kapı A değerlendirmesi (deftere yazmaz). `rows`: {row_id: wf satır sonucu (`wf.RowResult.to_gate()`)}.
    `family`: BH ailesi (varsayılan bütün LIB satırları). `group_series`: {grup: {yapılandırma: {gün: getiri}}} (COIN_FIT
    ızgarası satırın `option_daily`'sinden kendiliğinden; diğer gruplar üye satırların günlük getirisinden kurulur).
    `placebo`: {row_id: [(gün, ADVERSE R)] ya da çağrılabilir → aynı liste}. `fwd`: {row_id: {days, n, mean_adverse,
    intraday}}. `full=False`: BH'den sonra koşulu zaten tutmayan satırın PBO'su/plasebosu hesaplanmaz (HÜKÜM AYNI; boş dünya
    deneyi için hız). Döner: {rows: {row_id: {...stats, verdict, fails}}, family_m, N, sr_var_family, groups}."""
    th = thresholds
    family = list(family) if family is not None else [r["row_id"] for r in RG.gate_a_rows()]
    fam_set = set(family)
    unknown = [k for k in rows if k not in fam_set]
    if unknown:
        raise ValueError(f"BH ailesinde olmayan satır(lar): {unknown[:3]}")
    placebo = placebo or {}
    fwd = fwd or {}
    epoch_ok = epoch_ok or {}
    # hızlı kip: BH'de geçebilecek bir satırın p'si ≤ q · (değerlendirilen satır sayısı) / m olmalı (daha büyük p'ler
    # hiçbir satırın q ≤ eşik olmasına katkı vermez) → bu sınırı aşan p_day erken durur; hüküm aynı (test)
    cap = None if full else float(th["q_bh_max"]) * len(rows) / len(family)
    st = {rid: row_stats(r, look_no=look_no, p_stop_above=cap) for rid, r in rows.items()}
    # (4) BH — aile tam (p_day yok → 1)
    q = bh_qvalues({rid: (st[rid]["p_day"] if rid in st else None) for rid in family})
    # (5) DSR — sr_var ailenin hesaplanan Sharpe'larından
    fam_var = family_sr_var(s["sr"] for s in st.values())
    out_rows: dict[str, dict[str, Any]] = {}
    for rid, r in rows.items():
        s = st[rid]
        s["q_bh"] = q[rid]
        s["sr_var"] = sr_var_floored(fam_var, s["n_days"])
        s["dsr"] = dsr(s["sr"], n_trials=n_trials, t=s["n_days"], skew=s["skew"], kurt=s["kurt"], sr_var=s["sr_var"])
        out_rows[rid] = s
    # cheap conditions
    for rid, r in rows.items():
        s = out_rows[rid]
        fails: list[str] = []
        if not (s["q_bh"] <= th["q_bh_max"]):
            fails.append("Q_BH")
        gold = _is_gold(r)
        if s["n"] < (th["gold_min_oos_trades"] if gold else th["min_oos_trades"]):
            fails.append("OOS_TRADES")
        if s["n_days"] < (th["gold_min_oos_days"] if gold else th["min_oos_days"]):
            fails.append("OOS_DAYS")
        if s["pos_fold_share"] is None or not (s["pos_fold_share"] >= th["min_positive_fold_share"]):
            fails.append("POS_FOLDS")
        if s["dsr"] is None or not (s["dsr"] >= th["dsr_min"]):
            fails.append("DSR")
        f = fwd.get(rid)
        s["fwd_read"] = dict(f) if f else None
        if not f:
            fails.append("FWD_MISSING")
        else:
            need_days = th["fwd_min_days_intraday"] if f.get("intraday") else th["fwd_min_days"]
            if not (f.get("days", 0) >= need_days and f.get("n", 0) >= th["fwd_min_trades"]
                    and f.get("mean_adverse") is not None and f["mean_adverse"] >= th["fwd_min_mean_adverse"]):
                fails.append("FWD_READ")
        if _is_coin_row(r) or (gold and str(r.get("scope")) in ("XAU", "PAXG")):
            coh = r.get("cluster_coherence_r")
            s["cluster_coherence_r"] = coh
            if coh is None or not (coh >= th["min_cluster_coherence_r"]):
                fails.append("CLUSTER_COHERENCE")
        elif s["top_share"] is None or not (s["top_share"] <= th["max_single_instrument_share"]):
            fails.append("TOP_SHARE")
        if _is_xsec(r) and not pit_ready:
            fails.append("XSEC_REQUIRES_PIT")
        if r.get("row_type") in ("COPY", "COVER") and not epoch_ok.get(rid, False):
            fails.append("EPOCH_COMPARISON_MISSING")
        if r.get("excluded", {}).get("n_leak_folds"):
            s["n_leak_folds"] = r["excluded"]["n_leak_folds"]
        s["fails"] = fails
    # (3) PBO — seçim grupları
    groups: dict[str, dict[str, Mapping[int, float]]] = {k: dict(v) for k, v in (group_series or {}).items()}
    group_of: dict[str, str] = {}
    for rid, r in rows.items():
        g = selection_group_key(r["row_type"], r["unit"], r["scope"])
        group_of[rid] = g
        if r["row_type"] == "COIN_FIT":
            groups.setdefault(g, {k: dict(v) for k, v in (r.get("option_daily") or {}).items()})
        else:
            groups.setdefault(g, {})
            groups[g].setdefault(rid, out_rows[rid]["daily"])
    needed = {group_of[rid] for rid in rows if full or not out_rows[rid]["fails"]}
    pbo_by_group = {g: CS.group_pbo(groups[g]) for g in sorted(needed)}
    for rid in rows:
        s = out_rows[rid]
        g = group_of[rid]
        res = pbo_by_group.get(g)
        s["pbo_group"] = g
        s["pbo"] = res["pbo"] if res else None
        s["pbo_state"] = res["state"] if res else "SKIPPED_LAZY"
        if res is None or res["pbo"] is None or not (res["pbo"] < th["pbo_max_exclusive"]):
            s["fails"].append("PBO")
    # plasebo — çağrılabilir tembel değerlendirilir
    from . import placebo as PL
    for rid, r in rows.items():
        s = out_rows[rid]
        s["placebo_diff_ci"] = None
        if not full and s["fails"]:
            s["placebo_state"] = "SKIPPED_LAZY"
            s["fails"].append("PLACEBO")
            continue
        pl = placebo.get(rid)
        if callable(pl):
            pl = pl()
        if pl is None:
            s["placebo_state"] = "MISSING"
            s["fails"].append("PLACEBO")
            continue
        ci = PL.diff_ci([(x["day"], x["r"][GATE_COST]) for x in r["trades"]], list(pl), row_id=rid, look_no=look_no)
        s["placebo_diff_ci"] = ci
        s["placebo_n"] = len(pl)
        s["placebo_state"] = "OK" if ci is not None else "NOT_COMPUTABLE"
        if ci is None or not (ci[0] > th["placebo_ci_lower_min_exclusive"]):
            s["fails"].append("PLACEBO")
    for rid in rows:
        s = out_rows[rid]
        s["verdict"] = PASS_A if not s["fails"] else FAIL_A
    return {"rows": out_rows, "family_m": len(family), "N": int(n_trials), "sr_var_family": fam_var,
            "groups": {g: {k: v for k, v in r.items() if k != "configs"} | {"configs": list(r.get("configs", []))}
                       for g, r in pbo_by_group.items()},
            "thresholds_source": th.get("source"), "thresholds_sealed": bool(th.get("sealed"))}


def _seal_ok() -> bool:
    """Mühür denetimi: her kaydın kanonik sha'sı S1 kümesindekiyle ve kümenin sha'sı `REGISTRY_SHA` ile aynı (çalışırken
    kurcalama yakalanır; sabit değerler `tests/test_research_engine_registry.py`'de sabitlenir)."""
    shas = RG.S1_SEAL["shas"]
    if set(shas) != set(RG.REGISTRIES):
        return False
    if any(RG.sha256_canonical(reg) != shas[name] for name, reg in RG.REGISTRIES.items()):
        return False
    return RG.sha256_canonical(RG.S1_SEAL) == RG.REGISTRY_SHA


def _iso_day(d: int) -> str:
    return datetime.fromtimestamp(int(d) * DAY_MS / 1000, tz=timezone.utc).date().isoformat()


def gate_a_look(ledger: Any, night: date, rows: Mapping[str, Mapping[str, Any]], *,
                thresholds: Mapping[str, Any] = GATE_A_PROVISIONAL, family: Sequence[str] | None = None,
                group_series: Mapping[str, Mapping[str, Mapping[int, float]]] | None = None,
                placebo: Mapping[str, Any] | None = None, fwd: Mapping[str, Mapping[str, Any]] | None = None,
                pit_ready: bool = False, epoch_ok: Mapping[str, bool] | None = None, at: str | None = None,
                spec_sha: Mapping[str, str] | None = None) -> dict[str, Any]:
    """§3.7 yordamı. `ledger`: `trials.TrialsLedger`. Bakış gecesi değilse "ara görünüm" (hüküm yok, yazım yok). Bakış
    gecesinde (1) zincir + mühür → kırıksa kapı kapalı (`CLOSED`); (2–5) `gate_a_evaluate`; (6) her değerlendirilen
    satıra `FWD_READ`; (7) ailenin her satırına `LOOK_A` (değerlendirilmeyen: p = 1, `NOT_EVALUATED`). Yazım yalnız
    `ledger.append`."""
    from .trials import TrialsChainBroken, iter_rows
    try:
        ledger.load()
    except TrialsChainBroken as exc:
        return {"status": "CLOSED", "reason": "TRIALS_CHAIN_BROKEN", "detail": str(exc)}
    looked = ledger_looks(iter_rows(ledger.path)) if ledger.path.exists() else []
    if not is_look_night(night, looked):
        return {"status": "INTERIM", "label": INTERIM_LABEL, "night": night.isoformat(),
                "rows": {rid: {"n": len(r["trades"]),
                               "mean_r_adverse": (sum(float(x["r"][GATE_COST]) for x in r["trades"]) / len(r["trades"]))
                               if r["trades"] else None} for rid, r in rows.items()}}
    if not _seal_ok():
        return {"status": "CLOSED", "reason": "SEAL_MISMATCH"}
    look_no = len(looked) + 1
    n_trials = ledger.dsr_n()
    ev = gate_a_evaluate(rows, look_no=look_no, n_trials=n_trials, thresholds=thresholds, family=family,
                         group_series=group_series, placebo=placebo, fwd=fwd, pit_ready=pit_ready, epoch_ok=epoch_ok)
    fam = list(family) if family is not None else [r["row_id"] for r in RG.gate_a_rows()]
    scope_of = {r["row_id"]: r["scope"] for r in RG.gate_a_rows()}
    month = look_month(night)
    q_missing = bh_qvalues({rid: (ev["rows"][rid]["p_day"] if rid in ev["rows"] else None) for rid in fam})
    entries: list[dict[str, Any]] = []
    for rid in fam:
        s = ev["rows"].get(rid)
        win: dict[str, Any] = {"look_month": month, "night": night.isoformat()}
        base = {"kind": "LOOK_A", "row_id": rid, "look_no": look_no, "scope": scope_of.get(rid, rows.get(rid, {}).get("scope")),
                "window": win}
        if spec_sha and rid in spec_sha:
            base["spec_sha"] = spec_sha[rid]
        if at is not None:
            base["at"] = at
        if s is None:
            entries.append(dict(base, q_bh=q_missing[rid], verdict=FAIL_A,
                                params={"fails": ["NOT_EVALUATED"], "thresholds_source": ev["thresholds_source"],
                                        "N": ev["N"]}))
            continue
        r = rows[rid]
        if r["oos_days"]:
            win["oos"] = [_iso_day(min(r["oos_days"])), _iso_day(max(r["oos_days"]))]
        if s.get("fwd_read") is not None:
            fr = {"kind": "FWD_READ", "row_id": rid, "look_no": look_no, "scope": base["scope"], "window": dict(win),
                  "fwd_read": s["fwd_read"]}
            if at is not None:
                fr["at"] = at
            entries.append(fr)
        ci = s.get("placebo_diff_ci")
        entries.append(dict(
            base, n=s["n"], n_days=s["n_days"], mean_r_base=s["mean_r_base"], mean_r_adverse=s["mean_r_adverse"],
            mean_r_stress=s["mean_r_stress"], p_day=s["p_day"], q_bh=s["q_bh"], dsr=s["dsr"], pbo=s["pbo"],
            placebo_diff_ci=[round(ci[0], 6), round(ci[1], 6)] if ci else None, verdict=s["verdict"],
            fwd_read=s.get("fwd_read"),
            params={"fails": s["fails"], "thresholds_source": ev["thresholds_source"], "N": ev["N"],
                    "sr": s["sr"], "sr_var": s["sr_var"], "pos_fold_share": s["pos_fold_share"],
                    "top_share": s["top_share"], "cluster_coherence_r": s.get("cluster_coherence_r"),
                    "pbo_group": s["pbo_group"], "pbo_state": s["pbo_state"],
                    "excluded": dict(r.get("excluded") or {})}))
    res = ledger.append(_finite(entries))
    return {"status": "LOOK", "look_no": look_no, "month": month, "N": n_trials, "evaluation": ev,
            "appended": sum(1 for x in res if x["status"] == "APPENDED"),
            "duplicates": sum(1 for x in res if x["status"] == "DUPLICATE")}


def _finite(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deftere yazılacak sayılar sonlu olmalı (JSON dışı değer yok): NaN/sonsuz → None."""
    def clean(x: Any) -> Any:
        if isinstance(x, float) and not math.isfinite(x):
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        return x
    return [clean(e) for e in entries]


__all__ = ["BH_Q", "CHUNK", "EQUITY_BASE_USDT", "FAIL_A", "GATE_A_PROVISIONAL", "GATE_COST", "INTERIM_LABEL",
           "LOOK_AFTER_DAY", "MEMORY_MB_MAX", "NULL_WORLDS", "PASS_A", "PLACEBO_CI_LEVEL", "PLACEBO_K",
           "PLACEBO_MIN_CI_LOWER", "RESAMPLES", "R_SCALE", "SCENARIOS", "bh_qvalues", "choice_stream", "daily_returns",
           "day_clusters", "dsr", "family_sr_var", "gate_a_evaluate", "gate_a_look", "is_look_night", "ledger_looks",
           "look_month", "moments", "p_day", "p_day_seed", "row_stats", "seed_int", "selection_group_key", "sharpe",
           "sr_var_floored", "top_share"]

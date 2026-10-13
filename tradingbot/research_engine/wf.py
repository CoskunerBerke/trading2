"""P3d — walk-forward katmanları, sızıntı, `COIN_FIT` üç yollu seçim ve maliyet senaryoları (docs/ENGINE_BRAIN_V1.md
§3.1–§3.3; mühür `WF_PROTOCOL_V1`; ana belge §6.3).

* **Katmanlar** `quant.walkforward.make_folds(validation_days > 0)` ile (YENİDEN KULLANILIR, çatallanmaz): sınıf
  (`H1_PLUS` çapalı ≥ 365 / 90 / 90, `INTRADAY` kayan 120 / 30 / 14, `GOLD_FUT` çapalı ≥ 180 / 30 / 30; adım = test)
  varyantın mühürlü `wf_class`'ından. Veri başı = serinin ilk barı + 210 ısınma barı, en erken 2020-01-01.
  **Arındırma/ambargo (B1, mühür):** her sınırda toplam 1 sinyal barı + `max_hold`; purge = ⌊(1 + max_hold) / 2⌋, embargo =
  kalan (300 → 150 + 151). `max_hold` varyantın sinyal dilimindeki en çok tutuşudur (`exits.max_hold_bars`; kopya/örtü/F2'de
  `copy_books.<taban>.max_hold`; Box "UTC gün sonu" → bir UTC günü, okunuş). Birden çok varyantlı satırda (ızgara) EN UZUN
  boşluk (ms) kullanılır (muhafazakâr).
* **Sızıntı:** `quant.walkforward.leakage_check` katman başına (+ ek etiket ufku denetimi: eğitim işleminin çıkışı doğrulama
  başlangıcından, doğrulama işleminin çıkışı test başlangıcından sonra olamaz — etiket bir sonraki dilime taşmaz). Geçmeyen
  katman ATILIR (`LEAK`; sayısı satırda).
* **OOS** = LEAK olmayan test katmanlarının birleşimi, her satır türünde aynı tanım; OOS günleri = bu pencerelerin UTC
  günleri (CSCV'nin ortak ekseni). İşlemin günü GİRİŞ UTC günüdür (OOS üyeliği girişle olduğundan her OOS işlemi eksendedir).
* **Sabit parametreli satır** (havuz, küme, kopya, örtü, diğer): seçim yok; `run_three_way` tek adayla koşar (doğrulama
  dilimi yalnız gösterilir).
* **`COIN_FIT` (§3.2, §2.4-2):** `run_three_way` — `fit_fn` YALNIZ eğitim işlemleriyle her ızgara seçeneğinin coin, küme (coin
  hariç) ve havuz ADVERSE toplamlarını; `candidates_fn` büzülmüş puana göre en iyi 3'ü (`s_c = (n_c·r̄_c + k·r̃_K) / (n_c +
  k)`, `r̃_K = (n_K·r̄_K + k·r̄_P) / (n_K + k)`, k = 50); `select_fn` YALNIZ doğrulama işlemleriyle aynı puanla en iyisini, fark
  havuzun seçimine (adaylar içinde doğrulama havuz ortalaması en yüksek olan) < 0,02R ise havuzun seçimini; `evaluate_fn`
  YALNIZ test işlemleriyle seçilen seçeneği. Eşitlikte ızgara sırası. Her değerlendirilen katmanın seçimi deneme defterine
  `SELECT` satırıdır (yalnız `trials.TrialsLedger.append`).
* **Maliyet:** üç senaryo (`base`/`ADVERSE`/`STRESS`) çekirdeğin aynı yol üzerindeki aritmetik yeniden maliyetlendirmesidir
  (`kernel.cost_trade`); seçim, `p_day`, DSR, CSCV, plasebo ADVERSE (mühürde `gate: true`) ile.
* **Kapı dışı işlemler** (`kernel.gate_excluded`; mühürlü `gate_exclusions`) katmanlara atanır (sızıntı denetimi bütün
  işlemleri görür) ama seçim dahil hiçbir istatistiğe girmez; sayısı, payı ve işaret dağılımı satırda.
* **Aileler:** standart ailenin varyantları aynı seride `kernel.run_lockstep`; kopya + örtüler `kernel.make_family` (tek
  paylaşılan akış) + `run_lockstep` (P3b-2b notu (3)).
* **CSCV matrisi:** `trade_rows(sonuçlar, data)` her işleme günlük piyasa değerlemesi dağılımını (`mtm`, fiyat yolu; U3,
  `stats.mtm_allocation`) ekler; ızgaranın günlük serisi buradan kurulur.

Ağsız; gerçek veri koşusu P3g'dedir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import kernel as KN
from . import placebo as PL
from . import registry as RG
from . import stats as ST
from ..quant import walkforward as QW

DAY_MS = 86_400_000
TF_MS: Mapping[str, int] = KN.TF_MS
_WF = RG.WF_PROTOCOL_V1
_TH = _WF["thresholds"]
FOLDS = _TH["folds"]
WARMUP_BARS = int(_TH["warmup_bars"])                            # 210
PURGE_SIGNAL_BARS = int(_TH["purge_signal_bars"])                # 1
POS_FOLD_MIN_TRADES = int(_TH["positive_fold_min_trades"])       # 5
TOP_K = int(_TH["candidates_top_k"])                             # 3
SCENARIOS: tuple[str, ...] = tuple(_TH["costs"])
GATE_COST = ST.GATE_COST
_D0 = date.fromisoformat(str(_WF["data_start_earliest"]))
DATA_START_EARLIEST_MS = int(datetime(_D0.year, _D0.month, _D0.day, tzinfo=timezone.utc).timestamp() * 1000)
_LTH = RG.LIB_V1["thresholds"]
K_SHRINK = float(_LTH["shrinkage"]["k_trades"])                  # 50
POOL_TIE_MARGIN_R = float(_LTH["shrinkage"]["pool_tie_margin_r"])  # 0,02
LEAK = "LEAK"

# make_folds adımı = test uzunluğu: mühürlü `step_equals_test` ve sınıfların adımı bununla tutarlı olmalı (kapalı hata)
if not _WF["step_equals_test"] or any(int(f["step_days"]) != int(f["test_days"]) for f in FOLDS.values()):
    raise RuntimeError("WF_PROTOCOL_V1: adım = test değil; make_folds bu protokolü uygulayamaz")


# ============================================================================ katman planı
def _resolve(variant_id: str) -> Mapping[str, Any]:
    vs = RG.variants_by_id()
    v = vs[variant_id]
    return vs[v["alias_of"]] if v.get("alias_of") else v


def _copy_book_of(v: Mapping[str, Any]) -> str | None:
    if v.get("cover"):
        return str(v["cover"]["base"])
    for req in v.get("requires") or ():
        if str(req).startswith("copy_books."):
            return str(req).split(".", 1)[1]
    return None


def max_hold_bars(variant_id: str) -> int:
    """Varyantın sinyal dilimindeki en çok tutuşu (bar). Tanımsızsa ValueError (UNSPECIFIED; satır koşmaz)."""
    v = _resolve(variant_id)
    tf_ms = TF_MS[v["signal_tf"]]
    mh = v["exits"]["max_hold_bars"]
    if isinstance(mh, Mapping):
        mh = mh.get("max_hold_bars")
    if isinstance(mh, int) and not isinstance(mh, bool) and mh >= 1:
        return mh
    book = _copy_book_of(v)
    if isinstance(mh, str) and book is not None:
        b = RG.LIB_V1["copy_books"][book]["max_hold"]
        if "bars" in b:
            return int(math.ceil(int(b["bars"]) * TF_MS[str(b["tf"])] / tf_ms))
        if "gün sonu" in str(b.get("rule_tr", "")) or "eod" in str(b.get("rule_tr", "")):
            return DAY_MS // tf_ms                                  # UTC gün sonu: tutuş ≤ bir UTC günü
    raise ValueError(f"{variant_id}: max_hold tanımsız ({mh!r})")


def purge_embargo(max_hold: int) -> tuple[int, int]:
    """B1 (b): toplam = 1 sinyal barı + max_hold; purge = ⌊toplam / 2⌋, embargo = kalan."""
    total = PURGE_SIGNAL_BARS + int(max_hold)
    purge = total // 2
    return purge, total - purge


def wf_class(variant_id: str) -> str:
    return str(_resolve(variant_id)["wf_class"])


def data_start_ms(first_bar_ms: int, signal_tf: str) -> int:
    """Veri başı: ilk bar + ısınma barları; en erken 2020-01-01."""
    return max(int(first_bar_ms) + WARMUP_BARS * TF_MS[signal_tf], DATA_START_EARLIEST_MS)


def make_plan(start_ms: int, end_ms: int, *, signal_tf: str, max_hold: int, klass: str) -> dict[str, Any]:
    """`make_folds(validation_days > 0)` planı (sınıfın mühürlü uzunlukları, B1 bölüşümü)."""
    f = FOLDS[klass]
    mode = str(_WF["classes"][klass]["mode"])
    train = int(f["min_train_days"] if "min_train_days" in f else f["train_days"])
    purge, emb = purge_embargo(max_hold)
    plan = QW.make_folds(int(start_ms), int(end_ms), mode=mode, train_days=train, validation_days=int(f["validation_days"]),
                         test_days=int(f["test_days"]), purge_bars=purge, embargo_bars=emb, bar_ms=TF_MS[signal_tf])
    plan["meta"] = {"wf_class": klass, "signal_tf": signal_tf, "max_hold": int(max_hold), "purge_bars": purge,
                    "embargo_bars": emb}
    return plan


def plan_for(variant_ids: Sequence[str], start_ms: int, end_ms: int) -> dict[str, Any]:
    """Bir satırın (ya da ızgaranın) ortak planı: aynı sınıf; boşluğu (1 + max_hold) × bar EN UZUN olan varyantınki."""
    classes = {wf_class(v) for v in variant_ids}
    if len(classes) != 1:
        raise ValueError(f"satırın varyantları farklı WF sınıfında: {sorted(classes)}")
    best = max(variant_ids, key=lambda v: ((1 + max_hold_bars(v)) * TF_MS[_resolve(v)["signal_tf"]], v))
    return make_plan(start_ms, end_ms, signal_tf=str(_resolve(best)["signal_tf"]), max_hold=max_hold_bars(best),
                     klass=classes.pop())


# ============================================================================ işlem satırları
def _iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def trade_key(t: KN.KernelTrade) -> str:
    """tj_v1 biçimi `defter|trade_id|opened_at`: defter = varyant, trade_id = `sembol:sinyal_ts:yön`."""
    return f"{t.variant_id}|{t.symbol}:{int(t.signal_ts)}:{t.side}|{_iso(t.entry_ts)}"


def exit_ref(t: KN.KernelTrade) -> float | None:
    """Bacakların ağırlıklı çıkış referans fiyatı (maliyetten önce)."""
    tot = sum(lg.frac for lg in t.legs)
    return (sum(lg.ref * lg.frac for lg in t.legs) / tot) if tot > 0 else None


def trade_row(t: KN.KernelTrade, data: KN.SeriesData | None = None, *,
              _close_t: dict[str, list[int]] | None = None) -> dict[str, Any]:
    """Çekirdek işleminin WF satırı (`ts_ms` = giriş = karar anı; `as_of_ms` = kararın gördüğü son veri anı). `data`
    verilirse `mtm` = günlük piyasa değerlemesi dağılımı (U3; sinyal diliminin barlarıyla fiyat yolu)."""
    entry = int(t.entry_ts)
    row = {"ts_ms": entry, "as_of_ms": entry, "exit_ms": int(t.exit_ts if t.exit_ts is not None else entry),
            "day": entry // DAY_MS, "symbol": t.symbol, "option": t.variant_id, "side": t.side,
            "signal_ts": int(t.signal_ts), "initial_stop": float(t.initial_stop), "entry_ref": float(t.entry_ref),
            "bars_held": int(t.bars_held), "r": {**{k: float(v) for k, v in t.r.items()}, PL.GROSS: PL.gross_r(t)},
            "pnl": {k: float(v) for k, v in t.pnl_usdt.items()}, "flags": list(t.flags),
            "excluded": KN.gate_excluded(t), "trade_key": trade_key(t), "exit_ref": exit_ref(t)}
    if data is not None:
        tf = str(_resolve(t.variant_id)["signal_tf"])
        bars = data.bars.get(tf)
        ct = None
        if _close_t is not None and bars:
            ct = _close_t.setdefault(tf, [int(b["timestamp"]) + TF_MS[tf] for b in bars])
        row["mtm"], row["mtm_method"] = ST.mtm_allocation(row, bars, TF_MS[tf], close_t=ct)
    return row


def trade_rows(results: Iterable[KN.KernelResult], data: KN.SeriesData | None = None) -> list[dict[str, Any]]:
    cache: dict[str, list[int]] = {}
    return [trade_row(t, data, _close_t=cache) for res in results for t in res.trades]


# ============================================================================ atama ve sızıntı
def assign_and_check(plan: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> tuple[dict, dict[int, list[str]]]:
    """Satırları katmanlara ata (`assign_rows`, yalnız zamanla) ve katman başına `leakage_check` + etiket ufku."""
    a = QW.assign_rows(rows, dict(plan), ts_key="ts_ms")
    leaks: dict[int, list[str]] = {}
    for w, slot in zip(plan["folds"], a["folds"]):
        sub_plan = {"folds": [w], "layout": plan["layout"], "holdout": plan.get("holdout")}
        sub_a = {"folds": [slot], "holdout": a.get("holdout", [])}
        chk = QW.leakage_check(sub_a, sub_plan, ts_key="ts_ms", as_of_key="as_of_ms")
        v = list(chk["violations"])
        v += [f"fold {w.idx}: eğitim işleminin çıkışı doğrulamaya taşıyor ({r['exit_ms']} > {w.val_start})"
              for r in slot["train"] if int(r["exit_ms"]) > w.val_start]
        v += [f"fold {w.idx}: doğrulama işleminin çıkışı teste taşıyor ({r['exit_ms']} > {w.test_start})"
              for r in slot["validation"] if int(r["exit_ms"]) > w.test_start]
        if v:
            leaks[w.idx] = v[:20]
    return a, leaks


def _drop(plan: Mapping[str, Any], a: Mapping[str, Any], leaks: Mapping[int, Any]) -> tuple[dict, dict]:
    keep = [i for i, w in enumerate(plan["folds"]) if w.idx not in leaks]
    return (dict(plan, folds=[plan["folds"][i] for i in keep]), dict(a, folds=[a["folds"][i] for i in keep]))


def window_days(start_ms: int, end_ms: int) -> list[int]:
    return list(range(int(start_ms) // DAY_MS, (int(end_ms) - 1) // DAY_MS + 1))


def oos_days(plan: Mapping[str, Any]) -> list[int]:
    days: set[int] = set()
    for w in plan["folds"]:
        days.update(window_days(w.test_start, w.test_end))
    return sorted(days)


# ============================================================================ satır sonucu
@dataclass
class RowResult:
    row_id: str
    row_type: str
    unit: str
    scope: str
    wf_class: str
    plan_meta: dict[str, Any]
    oos_days: list[int]
    trades: list[dict[str, Any]]
    folds: list[dict[str, Any]]
    excluded: dict[str, Any]
    instrument_net_r: dict[str, float] = field(default_factory=dict)
    cluster_coherence_r: float | None = None
    option_daily: dict[str, dict[int, float]] = field(default_factory=dict)
    selections: list[dict[str, Any]] = field(default_factory=list)

    @property
    def means(self) -> dict[str, float | None]:
        n = len(self.trades)
        return {sc: (sum(t["r"][sc] for t in self.trades) / n if n else None) for sc in SCENARIOS}

    def to_gate(self) -> dict[str, Any]:
        """`stats.gate_a_evaluate` girdisi."""
        return {"row_id": self.row_id, "row_type": self.row_type, "unit": self.unit, "scope": self.scope,
                "wf_class": self.wf_class, "trades": self.trades, "oos_days": self.oos_days, "folds": self.folds,
                "excluded": self.excluded, "instrument_net_r": self.instrument_net_r,
                "cluster_coherence_r": self.cluster_coherence_r, "option_daily": self.option_daily}


def _split(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    inc, exc = [], []
    for r in rows:
        (exc if r.get("excluded") else inc).append(dict(r))
    return inc, exc


def _excluded_summary(n_inc: int, excl: Sequence[Mapping[str, Any]], leaks: Mapping[int, Any]) -> dict[str, Any]:
    flags: dict[str, int] = {}
    for r in excl:
        for f in r.get("flags") or ():
            if f in KN.GATE_EXCLUDED_FLAGS:
                flags[f] = flags.get(f, 0) + 1
    tot = n_inc + len(excl)
    return {"n": len(excl), "share": (len(excl) / tot) if tot else 0.0, "flags": dict(sorted(flags.items())),
            "n_leak_folds": len(leaks), "leak_folds": sorted(leaks)}


def _finish(row_id: str, row_type: str, unit: str, scope: str, plan: Mapping[str, Any], plan_ok: Mapping[str, Any],
            res: Mapping[str, Any], leaks: Mapping[int, Any]) -> RowResult:
    trades: list[dict] = []
    excl: list[dict] = []
    folds: list[dict] = [{"idx": i, "leak": True, "skipped": True, "n": 0, "n_excluded": 0, "mean_adverse": None,
                          "violations": list(v)} for i, v in sorted(leaks.items())]
    for w, f in zip(plan_ok["folds"], res["folds"]):
        tm = f.get("test_metrics") or {}
        inc = tm.get("included", [])
        for t in inc:
            t["fold"] = w.idx
            t["test_window"] = [w.test_start, w.test_end]
        trades += inc
        excl += tm.get("excluded", [])
        folds.append({"idx": w.idx, "leak": False, "skipped": bool(f.get("skipped")), "n": len(inc),
                      "n_excluded": len(tm.get("excluded", [])),
                      "mean_adverse": (sum(t["r"][GATE_COST] for t in inc) / len(inc)) if inc else None,
                      "windows": f["windows"], "selected": _sel_id(f.get("selected"))})
    folds.sort(key=lambda x: x["idx"])
    net: dict[str, float] = {}
    for t in trades:
        net[t["symbol"]] = net.get(t["symbol"], 0.0) + float(t["r"][GATE_COST])
    meta = dict(plan.get("meta") or {})
    return RowResult(row_id=row_id, row_type=row_type, unit=unit, scope=scope, wf_class=str(meta.get("wf_class")),
                     plan_meta=meta, oos_days=oos_days(plan_ok), trades=trades, folds=folds,
                     excluded=_excluded_summary(len(trades), excl, leaks), instrument_net_r=net)


def _sel_id(sel: Any) -> Any:
    return sel.get("selected") if isinstance(sel, Mapping) else sel


def _test_metrics(test: Sequence[Mapping[str, Any]], keep: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    inc, exc = _split(r for r in test if keep(r))
    return {"included": inc, "excluded": exc,
            "expectancy_r": (sum(t["r"][GATE_COST] for t in inc) / len(inc)) if inc else None}


def evaluate_fixed(row_id: str, row_type: str, unit: str, scope: str, rows: Sequence[Mapping[str, Any]],
                   plan: Mapping[str, Any]) -> RowResult:
    """Sabit parametreli satır: seçim yok; OOS = LEAK olmayan test katmanları (tek aday `run_three_way`)."""
    a, leaks = assign_and_check(plan, rows)
    plan_ok, a_ok = _drop(plan, a, leaks)
    res = QW.run_three_way(plan_ok, a_ok, fit_fn=lambda train: None, candidates_fn=lambda fit, train: [unit],
                           select_fn=lambda cands, val: cands[0],
                           evaluate_fn=lambda chosen, test: _test_metrics(test, lambda r: r["option"] == chosen),
                           min_train=0, min_validation=0, min_test=0)
    return _finish(row_id, row_type, unit, scope, plan, plan_ok, res, leaks)


# ============================================================================ COIN_FIT
def grid(family: str) -> list[str]:
    """Ailenin ızgara seçenekleri (mühür sırası; alias'lı üyeler dahil — §2.5)."""
    return [v["variant_id"] for v in RG.LIB_V1["variants"] if v["family"] == family]


def cluster_of(symbol: str) -> list[str]:
    for _name, members in RG.LIB_V1["clusters"]["members"].items():
        if symbol in members:
            return list(members)
    raise KeyError(f"{symbol}: P23 kümesi yok")


def _side_stats(rows: Iterable[Mapping[str, Any]], coin: str, cluster_ex: set[str], options: Sequence[str]
                ) -> dict[str, dict[str, list[float]]]:
    """Seçenek başına {"c"|"K"|"P": [n, Σ ADVERSE R]} (kapı dışı işlemler yok)."""
    st = {o: {"c": [0, 0.0], "K": [0, 0.0], "P": [0, 0.0]} for o in options}
    for r in rows:
        if r.get("excluded") or r["option"] not in st:
            continue
        x = st[r["option"]]
        v = float(r["r"][GATE_COST])
        x["P"][0] += 1
        x["P"][1] += v
        if r["symbol"] == coin:
            x["c"][0] += 1
            x["c"][1] += v
        elif r["symbol"] in cluster_ex:
            x["K"][0] += 1
            x["K"][1] += v
    return st


def shrunk_score(s: Mapping[str, Sequence[float]], k: float = K_SHRINK) -> float:
    """§2.4-2: s_c = (n_c·r̄_c + k·r̃_K) / (n_c + k), r̃_K = (n_K·r̄_K + k·r̄_P) / (n_K + k) (n·r̄ = Σ R; havuz boşsa r̄_P = 0)."""
    n_p, s_p = s["P"]
    r_p = s_p / n_p if n_p else 0.0
    n_k, s_k = s["K"]
    r_k = (s_k + k * r_p) / (n_k + k)
    n_c, s_c = s["c"]
    return (s_c + k * r_k) / (n_c + k)


def evaluate_coin_fit(family: str, coin: str, rows: Sequence[Mapping[str, Any]], plan: Mapping[str, Any], *,
                      ledger: Any = None, at: str | None = None, lib_version: str = RG.LIB_VERSION) -> RowResult:
    """`COIN_FIT:<coin>` satırı. `rows`: ailenin ızgara seçeneklerinin BÜTÜN verilen coin'lerdeki işlemleri (küme ve havuz
    istatistikleri için); her adım `run_three_way` ile yalnız kendi dilimini görür. `ledger` verilirse her değerlendirilen
    katmanın seçimi `SELECT` satırı olarak eklenir."""
    options = grid(family)
    order = {o: i for i, o in enumerate(options)}
    cluster_ex = set(cluster_of(coin)) - {coin}
    scope = f"COIN_FIT:{coin}"
    row_id = f"{family}@{scope}"

    def fit_fn(train: list[dict]) -> dict:
        return _side_stats(train, coin, cluster_ex, options)

    def candidates_fn(fit: dict, train: list[dict]) -> list[str]:
        return sorted(options, key=lambda o: (-shrunk_score(fit[o]), order[o]))[:TOP_K]

    def select_fn(cands: list[str], val: list[dict]) -> dict:
        vs = _side_stats(val, coin, cluster_ex, cands)
        sc = {o: shrunk_score(vs[o]) for o in cands}
        best = sorted(cands, key=lambda o: (-sc[o], order[o]))[0]
        pool_mean = {o: (vs[o]["P"][1] / vs[o]["P"][0]) if vs[o]["P"][0] else 0.0 for o in cands}
        pool = sorted(cands, key=lambda o: (-pool_mean[o], order[o]))[0]
        chosen = pool if sc[best] - sc[pool] < POOL_TIE_MARGIN_R else best
        return {"selected": chosen, "best": best, "pool_choice": pool, "candidates": list(cands),
                "scores_val": {o: round(sc[o], 6) for o in cands}}

    def evaluate_fn(sel: dict, test: list[dict]) -> dict:
        m = _test_metrics(test, lambda r: r["option"] == sel["selected"] and r["symbol"] == coin)
        coh = [r for r in test if r["option"] == sel["selected"] and r["symbol"] in cluster_ex and not r.get("excluded")]
        m["coherence"] = [float(r["r"][GATE_COST]) for r in coh]
        return m

    a, leaks = assign_and_check(plan, rows)
    plan_ok, a_ok = _drop(plan, a, leaks)
    res = QW.run_three_way(plan_ok, a_ok, fit_fn=fit_fn, candidates_fn=candidates_fn, select_fn=select_fn,
                           evaluate_fn=evaluate_fn, min_train=1, min_validation=1, min_test=0)
    rr = _finish(row_id, "COIN_FIT", family, scope, plan, plan_ok, res, leaks)
    coh = [v for f in res["folds"] for v in ((f.get("test_metrics") or {}).get("coherence") or [])]
    rr.cluster_coherence_r = (sum(coh) / len(coh)) if coh else None
    # CSCV seçim grubu: o coin'deki ızgaranın OOS günlük getirisi (yalnız LEAK olmayan test dilimleri)
    days = rr.oos_days
    for o in options:
        tr = [r for slot in a_ok["folds"] for r in slot["test"]
              if r["option"] == o and r["symbol"] == coin and not r.get("excluded")]
        rr.option_daily[o] = ST.mtm_daily(tr, days)                     # günlük piyasa değerlemesi (U3)
    sel_rows = []
    for w, f in zip(plan_ok["folds"], res["folds"]):
        if f.get("skipped"):
            continue
        s = f["selected"]
        rr.selections.append({"fold": w.idx, **s})
        e = {"kind": "SELECT", "lib_version": lib_version, "row_id": row_id, "scope": scope,
             "window": {"fold": w.idx, "train": [w.train_start, w.train_end], "validation": [w.val_start, w.val_end],
                        "test": [w.test_start, w.test_end]},
             "params": {"family": family, "coin": coin, "selected": s["selected"], "best": s["best"],
                        "pool_choice": s["pool_choice"], "candidates": s["candidates"], "scores_val": s["scores_val"]},
             "n": f["n_train"]}
        if at is not None:
            e["at"] = at
        sel_rows.append(e)
    if ledger is not None and sel_rows:
        ledger.append(sel_rows)
    return rr


# ============================================================================ aile koşuları (çekirdek)
def run_variants(variant_ids: Sequence[str], data: KN.SeriesData, *, start_ms: int | None = None,
                 end_ms: int | None = None, record_signals: bool = True
                 ) -> tuple[dict[str, KN.KernelResult], dict[str, set[int]]]:
    """Aynı serinin varyantları `kernel.run_lockstep` ile; `record_signals` → kuralın sinyal anları (plasebo; sonuç aynı)."""
    vs = [KN.make_variant(v) for v in variant_ids]
    return _lockstep(list(variant_ids), vs, data, start_ms, end_ms, record_signals)


def run_cover_family(base: str, data: KN.SeriesData, *, start_ms: int | None = None, end_ms: int | None = None,
                     record_signals: bool = True) -> tuple[dict[str, KN.KernelResult], dict[str, set[int]]]:
    """Kopya + yedi örtü TEK paylaşılan akışla (`kernel.make_family`) + `run_lockstep`. C4'te paylaşılan taban K satırı
    değildir (sekiz varyasyon ayrı K satırlarıdır) ve koşulmaz."""
    shared, covers = KN.make_family(base)
    ids = ([shared.variant_id] if base != "C4" else []) + list(covers)
    vs = ([shared] if base != "C4" else []) + list(covers.values())
    return _lockstep(ids, vs, data, start_ms, end_ms, record_signals)


def _lockstep(ids: list[str], vs: list[Any], data: KN.SeriesData, start_ms: int | None, end_ms: int | None,
              record: bool) -> tuple[dict[str, KN.KernelResult], dict[str, set[int]]]:
    runners = [PL.SignalRecorder(v) for v in vs] if record else vs
    res = KN.run_lockstep(runners, data, start_ms=start_ms, end_ms=end_ms)
    sig = {i: set(r.signal_times) for i, r in zip(ids, runners)} if record else {}
    return dict(zip(ids, res)), sig


__all__ = ["DATA_START_EARLIEST_MS", "GATE_COST", "K_SHRINK", "LEAK", "POOL_TIE_MARGIN_R", "POS_FOLD_MIN_TRADES",
           "PURGE_SIGNAL_BARS", "RowResult", "SCENARIOS", "TOP_K", "WARMUP_BARS", "assign_and_check", "cluster_of",
           "data_start_ms", "evaluate_coin_fit", "evaluate_fixed", "grid", "make_plan", "max_hold_bars", "oos_days",
           "plan_for", "purge_embargo", "run_cover_family", "run_variants", "shrunk_score", "trade_key", "trade_row",
           "trade_rows", "wf_class", "window_days"]

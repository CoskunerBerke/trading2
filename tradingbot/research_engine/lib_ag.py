"""S1d — `LIB_AG_V1`'in motor tarafı (EKLEME API'si; docs/ENGINE_BRAIN_V1.md §8.6 S1d). Yalnız PAPER araştırma kodu:
ağsız, kayıt-yalnız; gerçek veri koşusu ve gece bağlantısı P3g'dedir (S4w/S4x bu fonksiyonları çağırır).

LIB_V1 yolları (`kernel.make_variant`, `wf.max_hold_bars`, `wf.trade_row(data=…)`, `registry.gate_a_rows`,
`stats.selection_group_key`) kimlikleri `registry.variants_by_id()`'den (LIB_V1) çözer ve DEĞİŞMEDİ; AG kimlikleri için
eşdeğerleri buradadır ve aynı mühürlü makineyi (çekirdek, maliyet, fonlama mark'ı, WF katmanları, istatistik) kullanır:

* **Kurulum:** `make_variant(vid)` (`seal_s1d.require()` → tutmazsa `S1dSealError`, kurulum yok), `make_overlay_family(base)`.
* **Koşu:** `run_variants(vids, data, start_ms, end_ms, record_signals)` = `kernel.run_lockstep` + `placebo.SignalRecorder`
  (`wf.run_variants`'ın AG eşi); `trade_rows(sonuçlar, data)` = `wf.trade_row` + AG sinyal dilimiyle günlük MTM (U3).
* **WF:** `max_hold_bars`, `wf_class`, `plan_for(vids, start, end)` (`wf.make_plan`, B1 arındırma) → `evaluate(vid, rows,
  plan)` = `wf.evaluate_fixed(row_id, "AG", vid, "AG_POOLED", rows, plan)`.
* **Deneme defteri:** `gate_a_rows()` (38 satır, `<vid>@AG_POOLED`), `spec_for`/`spec_sha_for`, `module_src_shas()`,
  `seal_entries(module_src_sha)` (38 `SPEC`, `lib_version = LIB_AG_v1` → N_raw + 38; aynı spec iki kez sayılmaz),
  `budget()`, `n_at_seal_with_ag()`.
* **İstatistik:** `selection_group_key(row_id)` / `selection_groups()` (PBO grupları), `cal_flag(start, end)`.
* **Evren:** `universe_symbols()` (41), `universe_diff(entry_symbols)` (S1b farkı + XAUUSDT), `series_plan()` (38 × 41).

`strategy_lib` kataloğuna çekirdeğin zaten içe aktardığı modülden (`kernel.SLC`) erişilir (paket dışı yeni import yok).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from . import kernel as KN
from . import placebo as PL
from . import registry as RG
from . import seal_s1d as S1D
from . import stats as ST
from . import wf as WF

SLC = KN.SLC                                      # strategy_lib.catalog (çekirdeğin içe aktardığı)
LIB = S1D.LIB_AG_V1
LIB_VERSION = S1D.LIB_AG_VERSION
SCOPE = "AG_POOLED"
ROW_TYPE = "AG"
DAY_MS = 86_400_000
CAL_FLAG = str(S1D.AG_CALENDAR_V1["uncovered"]["flag"])


class AgUnknownVariant(KeyError):
    """LIB_AG_V1'de olmayan kimlik."""


# ============================================================================ kayıt görünümü
def variant_ids() -> list[str]:
    return list(LIB["variant_order"])


def variant(vid: str) -> Mapping[str, Any]:
    try:
        return LIB["variants"][vid]
    except KeyError:
        raise AgUnknownVariant(vid) from None


def is_ag(vid: str) -> bool:
    return vid in LIB["variants"]


def variant_spec(vid: str) -> dict[str, Any]:
    """Kurulum girdisi: `{family, timeframes, signal_tf, exits}`; örtüde ayrıca `base_variants` ve onların S1 `LIB_V1`
    mühürlü parametreleri (`base_params`)."""
    v = variant(vid)
    spec = {k: RG.thaw(v[k]) for k in ("family", "timeframes", "signal_tf", "exits")}
    if v["family"] == "AG_O":
        bvs = list(v["cover"]["base_variants"])
        spec["base_variants"] = bvs
        spec["base_params"] = [KN.lib_params(b) for b in bvs]
    return spec


def make_variant(vid: str) -> Any:
    """Mühürlü satırdan `strategy_lib` varyantı. S1d (→ S1c → S1b) tutmazsa `S1dSealError` (kapalı hata)."""
    S1D.require()
    return SLC.make_ag(vid, RG.thaw(variant(vid)["params"]), variant_spec(vid))


def overlays_of(base: str) -> list[str]:
    return [v for v in variant_ids() if variant(v)["family"] == "AG_O" and variant(v)["cover"]["base"] == base]


def make_overlay_family(base: str) -> dict[str, Any]:
    return {v: make_variant(v) for v in overlays_of(base)}


# ============================================================================ WF
def max_hold_bars(vid: str) -> int:
    """Sinyal dilimindeki en çok tutuş (WF arındırma/ambargo, B1). AG ailelerinde mühürlü sayı; örtüde tabanın
    `copy_books.<taban>.max_hold`'u (`wf.max_hold_bars`'ın kopya kuralıyla aynı: Box gün sonu → bir UTC günü)."""
    v = variant(vid)
    mh = v["exits"]["max_hold_bars"]
    if isinstance(mh, int) and not isinstance(mh, bool):
        return int(mh)
    base = str(v["cover"]["base"])
    b = RG.LIB_V1["copy_books"][base]["max_hold"]
    tf_ms = KN.TF_MS[str(v["signal_tf"])]
    if "bars" in b:
        return int(math.ceil(int(b["bars"]) * KN.TF_MS[str(b["tf"])] / tf_ms))
    return DAY_MS // tf_ms


def wf_class(vid: str) -> str:
    return str(variant(vid)["wf_class"])


def plan_for(vids: Sequence[str], start_ms: int, end_ms: int) -> dict[str, Any]:
    """`wf.plan_for`'un AG eşi: aynı sınıf; boşluğu (1 + max_hold) × bar EN UZUN olan varyantınki."""
    classes = {wf_class(v) for v in vids}
    if len(classes) != 1:
        raise ValueError(f"satırın varyantları farklı WF sınıfında: {sorted(classes)}")
    best = max(vids, key=lambda v: ((1 + max_hold_bars(v)) * KN.TF_MS[str(variant(v)["signal_tf"])], v))
    return WF.make_plan(start_ms, end_ms, signal_tf=str(variant(best)["signal_tf"]), max_hold=max_hold_bars(best),
                        klass=classes.pop())


def run_variants(vids: Sequence[str], data: KN.SeriesData, *, start_ms: int | None = None, end_ms: int | None = None,
                 record_signals: bool = True) -> tuple[dict[str, KN.KernelResult], dict[str, set[int]]]:
    """Aynı serinin AG varyantları `kernel.run_lockstep` ile; `record_signals` → kuralın sinyal anları (plasebo)."""
    vs = [make_variant(v) for v in vids]
    runners = [PL.SignalRecorder(v) for v in vs] if record_signals else vs
    res = KN.run_lockstep(runners, data, start_ms=start_ms, end_ms=end_ms)
    sig = {i: set(r.signal_times) for i, r in zip(vids, runners)} if record_signals else {}
    return dict(zip(vids, res)), sig


def trade_rows(results: Iterable[KN.KernelResult], data: KN.SeriesData | None = None) -> list[dict[str, Any]]:
    """`wf.trade_rows`'un AG eşi: `wf.trade_row` (veri olmadan) + `data` verilirse AG varyantının sinyal dilimiyle günlük
    piyasa değerlemesi (`stats.mtm_allocation`, U3)."""
    out = []
    cache: dict[str, list[int]] = {}
    for res in results:
        for t in res.trades:
            row = WF.trade_row(t)
            if data is not None:
                tf = str(variant(t.variant_id)["signal_tf"])
                bars = data.bars.get(tf)
                ct = cache.setdefault(tf, [int(b["timestamp"]) + KN.TF_MS[tf] for b in bars]) if bars else None
                row["mtm"], row["mtm_method"] = ST.mtm_allocation(row, bars, KN.TF_MS[tf], close_t=ct)
            out.append(row)
    return out


def row_id(vid: str) -> str:
    return f"{vid}@{SCOPE}"


def evaluate(vid: str, rows: Sequence[Mapping[str, Any]], plan: Mapping[str, Any]) -> WF.RowResult:
    """Sabit parametreli AG satırı (`wf.evaluate_fixed`; seçim yok, OOS = LEAK olmayan test katmanları)."""
    return WF.evaluate_fixed(row_id(vid), ROW_TYPE, vid, SCOPE, rows, plan)


# ============================================================================ deneme defteri ve bütçe
def gate_a_rows() -> list[dict[str, Any]]:
    """AG Kapı A satırları (sonuçlara bakılmadan): varyant başına bir `AG_POOLED` satırı, ham deneme 1."""
    return [{"row_id": row_id(v), "row_type": ROW_TYPE, "unit": v, "scope": SCOPE, "raw_trials": 1} for v in variant_ids()]


def spec_for(vid: str, module_src_sha: str | None = None) -> dict[str, Any]:
    """§2.1 spec'i (`registry.spec_sha`'nın anahtarları): params, dilimler, çıkışlar mühürden; büyüklük LIB_V1'inki;
    `module_src_sha` = `strategy_lib.identity.module_src_sha(vid)` (AG modülü + bağımlılıkları; örtüde taban modülü)."""
    v = variant(vid)
    if module_src_sha is None:
        module_src_sha = KN.SLI.module_src_sha(vid)          # git HEAD blob'ları (strategy_lib.identity)
    return {"family": v["family"], "variant_id": vid, "params": RG.thaw(v["params"]), "timeframes": RG.thaw(v["timeframes"]),
            "exits": RG.thaw(v["exits"]), "sizing": RG.thaw(LIB["sizing"]), "module_src_sha": module_src_sha}


def spec_sha_for(vid: str, module_src_sha: str | None = None) -> str:
    return RG.spec_sha(spec_for(vid, module_src_sha))


def module_src_shas() -> dict[str, str]:
    return {v: KN.SLI.module_src_sha(v) for v in variant_ids()}


def seal_entries(module_src_sha: Mapping[str, str]) -> list[dict[str, Any]]:
    """AG'nin deneme satırları (`trials.TrialsLedger.append` girdisi): her Kapı A satırına bir `SPEC` (38; N_raw + 38).
    Aynı (LIB_AG_v1, spec_sha, kapsam) ikinci kez sayılmaz (defterin tekilleştirmesi)."""
    return [{"kind": "SPEC", "lib_version": LIB_VERSION, "spec_sha": spec_sha_for(r["unit"], module_src_sha[r["unit"]]),
             "scope": SCOPE, "row_id": r["row_id"], "params": {"row_type": ROW_TYPE, "variant_id": r["unit"]}}
            for r in gate_a_rows()]


def budget() -> dict[str, Any]:
    fc: dict[str, int] = {}
    for v in variant_ids():
        f = str(variant(v)["family"])
        fc[f] = fc.get(f, 0) + 1
    n = len(variant_ids())
    return {"variants": n, "family_counts": fc, "gate_a_rows": len(gate_a_rows()), "raw_trials": n,
            "explore_series": n * len(universe_symbols())}


def n_at_seal_with_ag(lib: Mapping = RG.LIB_V1, prior: Mapping = RG.PRIOR_TRIALS_V1) -> int:
    """DSR N alt sınırı AG ile: LIB_V1 mühür N'i (ham deneme + öncüller) + AG'nin ham denemeleri."""
    return RG.n_at_seal(lib, prior) + budget()["raw_trials"]


def selection_group_key(rid: str) -> str:
    unit = rid.split("@", 1)[0]
    v = variant(unit)
    if v["family"] == "AG_O":
        return f"AG_COVER:{v['cover']['base']}@{SCOPE}"
    return f"AG_FAM:{v['family']}@{SCOPE}"


def selection_groups() -> dict[str, list[str]]:
    return {k: list(v) for k, v in LIB["gates"]["pbo_groups"].items()}


def cal_flag(start_ms: int, end_ms: int) -> str | None:
    """Pencere AG_CALENDAR_V1 kapsamının dışına taşıyorsa `CAL_UNCOVERED` (CAL varyantı orada işlem açmaz). Kapsamın iki
    ucu (1 Ocak 00:00 ve 31 Aralık 24:00 New York) kışın, EST'dedir (UTC−5) → UTC sınırları kesin."""
    c = S1D.AG_CALENDAR_V1
    lo = _day_ms(str(c["covered_from"])) + EST_SHIFT_MS
    hi = _day_ms(str(c["covered_until"])) + DAY_MS + EST_SHIFT_MS
    return CAL_FLAG if int(start_ms) < lo or int(end_ms) >= hi else None


EST_SHIFT_MS = 5 * 3_600_000


def _day_ms(day: str) -> int:
    y, m, d = (int(x) for x in day[:10].split("-"))
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


# ============================================================================ evren
def universe_symbols() -> list[str]:
    return list(S1D.AG_UNIVERSE_V1["symbols"])


def universe_diff(entry_symbols: Iterable[Any]) -> dict[str, Any]:
    """Çalışma anındaki giriş evreni S1b'nin 40'ıyla aynı mı (`universe.entry_universe_diff`); AG evreni = o 40 + XAUUSDT."""
    from .universe import entry_universe_diff
    d = entry_universe_diff(entry_symbols)
    return {**d, "ag_symbols": universe_symbols() if d["ok"] else [], "ag_extra": ["XAUUSDT"]}


def series_plan() -> list[tuple[str, str]]:
    """(varyant, sembol) keşif/WF serileri: 38 × 41 (sembol başına sonuç yalnız rapor)."""
    return [(v, s) for v in variant_ids() for s in universe_symbols()]


__all__ = ["AgUnknownVariant", "CAL_FLAG", "LIB", "LIB_VERSION", "ROW_TYPE", "SCOPE", "budget", "cal_flag", "evaluate",
           "gate_a_rows", "is_ag", "make_overlay_family", "make_variant", "max_hold_bars", "module_src_shas",
           "n_at_seal_with_ag", "overlays_of", "plan_for", "row_id", "run_variants", "seal_entries", "selection_group_key",
           "selection_groups", "series_plan", "spec_for", "spec_sha_for", "trade_rows", "universe_diff", "universe_symbols",
           "variant", "variant_ids", "variant_spec", "wf_class"]

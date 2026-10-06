"""P2b — S2 atıf aşaması (§6.1 S2: kodlar + `cfgrid_v1` + ayrıştırma) ve atıf okuyucuları (özet, `engine-query`).

Girdiler (hepsi SALT-OKUNUR): günlük `journal/tj_v1` (S1b), yol dosyaları `paths/`, mühürlü araştırma deposu
(`journal.open_store`; giriş dilimi ATR'si, BTC hareketi, rastgele kontrol barları ve durum kovası, depo fonlaması) ve
defterlerin ledger JSON'undaki yürütme modeli alanları (`fidelity.exec_params`). Çıktı: `attribution/YYYY-MM.jsonl.gz`
(ay = kapanış ayı; satırlar `(closed_at, trade_key)` sırasında; gzip `mtime=0`) + `_index.json.gz` + `_build.json`.

* **Artımlı ve kaldığı yerden devam.** Satırın girdi özeti (günlük satırının içeriği, iki mühürlü kayıt sha'sı, defterin
  yürütme modeli, okunan depo ay parçalarının listesi, aynı defterin ÖNCEKİ satırlarının özeti — gürültü bandı, aşma
  geçmişi ve `regime_fit` bunlardan gelir) değişmedikçe satır yeniden hesaplanmaz. Değişenler EN YENİ kapanıştan geriye
  hesaplanır; `budget_s` bitince durulur, kalan iş ertesi gece sürer (eski satır o zamana kadar kalır).
* **Aynı mühür → bayt-özdeş çıktı** (kabul 8): her çıktı girdilerin saf fonksiyonudur (sabit anahtar sırası, sabit tohum,
  gzip `mtime=0`); çalıştırmaya özgü sayılar yalnız dönen özettedir.
* **DATA_MOVING:** bir satırın okuduğu seri mühürden sonra değiştiyse satır o gece hesaplanmaz (eski hâli kalır).
* İleriye bakma yok: önceki satırlar `closed_at < opened_at` ile süzülür; depo okumaları giriş anından ÖNCE kapanmış
  barlar (ATR, kova, rastgele kontrol penceresi) ya da işlemin kendi penceresidir (çıkış ATR'si, BTC hareketi).

Bu modül ağ kullanmaz; yalnız `data/research/attribution` altına yazar.
"""
from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from . import attribution as AT
from . import cfgrid as CG
from . import fidelity as FD
from .ledgers import KIND_FUTURES, iso, parse_ts, utc_now
from .paths import EnginePaths, gzip_bytes

UTC = timezone.utc
ATTR_SCHEMA = "attr_v1"
INDEX_SCHEMA = "attr_v1_index"
BUILD_SCHEMA = "attr_v1_build"
ANALYSIS_VERSION = "attr_v1/p2b.1"
DAY_MS = 86_400_000
TF_MS = CG.TF_MS
#: S2'nin depo parça önbelleği üst sınırı (1m ay parçası ~2 MB; bellek bütçesi, §2.5)
S2_MAX_PARTS = 32
#: atıf ay dosyasına akışla birleştirmeden önce bellekte tutulan en çok yeni satır
FLUSH_ROWS = 1000


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def _ms(ts: Any) -> int | None:
    t = parse_ts(ts)
    return int(t.timestamp() * 1000) if t is not None else None


def _iter_gz(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


# ============================================================================ depo ölçüleri (ileriye bakmasız)
class StoreView:
    """S2'nin depo okumaları (mühürlü `BarSource` üstünde); okunan parçalar ve `DATA_MOVING` izlenir."""

    def __init__(self, src: Any) -> None:
        self.src = src
        self.used: set[tuple[str, str, str]] = set()
        self.moving = False

    def frame(self, market: str, symbol: str, tf: str, a: int, b: int) -> pd.DataFrame | None:
        if self.src is None:
            return None
        df, used, st = self.src.frame(market, symbol, tf, a, b)
        self.used.update(tuple(u) for u in used)
        if st is not None:
            self.moving = True
            return None
        return df

    def closed_before(self, market: str, symbol: str, tf: str, as_of_ms: int, n: int) -> pd.DataFrame | None:
        step = TF_MS[tf]
        df = self.frame(market, symbol, tf, as_of_ms - (n + 3) * step, as_of_ms)
        if df is None or not len(df):
            return None
        df = df[df["timestamp"] + step <= as_of_ms]
        return df.tail(n).reset_index(drop=True) if len(df) else None


def atr_pct_at(sv: StoreView, market: str, symbol: str, tf: str, as_of_ms: int) -> tuple[float | None, float | None]:
    """(ATR14, ATR14/kapanış) — `as_of` anında KAPANMIŞ son 60 bar (`indicators.atr`, kuralların kullandığı fonksiyon)."""
    from .. import indicators as ind
    df = sv.closed_before(market, symbol, tf, as_of_ms, 60)
    if df is None or len(df) < 15:
        return None, None
    a = ind.atr(df["high"], df["low"], df["close"], 14)
    v = float(a.iloc[-1]) if len(a) and a.iloc[-1] == a.iloc[-1] else None
    c = float(df["close"].iloc[-1])
    if v is None or c <= 0:
        return None, None
    return v, v / c


def close_at(sv: StoreView, market: str, symbol: str, t_ms: int) -> float | None:
    df = sv.closed_before(market, symbol, "5m", t_ms, 3)
    return float(df["close"].iloc[-1]) if df is not None and len(df) else None


def store_inputs(sv: StoreView, row: dict, prior: "PriorCtx | list[dict]", bars: pd.DataFrame | None) -> AT.AttrInputs:
    from .provider import raw_symbol
    inp = AT.AttrInputs(bars=bars, entry_tf=AT.ENTRY_TF.get(str(row.get("book")), AT.DEFAULT_ENTRY_TF))
    om, cm = _ms(row.get("opened_at")), _ms(row.get("closed_at"))
    sym = raw_symbol(str(row.get("symbol") or ""))
    if om is not None and cm is not None:
        inp.atr_entry, inp.atr_pct_entry = atr_pct_at(sv, "futures", sym, inp.entry_tf, om - 1)
        _a, inp.atr_pct_exit = atr_pct_at(sv, "futures", sym, inp.entry_tf, cm)
        b0, b1 = close_at(sv, "futures", "BTCUSDT", om), close_at(sv, "futures", "BTCUSDT", cm)
        inp.btc_move_pct = (b1 / b0 - 1.0) * 100.0 if (b0 and b1) else None
        if inp.atr_entry is None:
            inp.notes["atr_entry"] = "depoda giriş diliminde yeterli kapanmış bar yok"
    inp.noise_band_pct, inp.noise_band_n = prior.noise_band() if isinstance(prior, PriorCtx) else AT.noise_band(prior)
    return inp


# ============================================================================ tek işlem
def grid_eligibility(row: dict, bars: pd.DataFrame | None) -> str | None:
    """Izgaraya giriş (§5.3): None = uygun; aksi halde neden."""
    if row.get("kind") != KIND_FUTURES:
        return "SPOT"
    fd = row.get("fidelity") or {}
    if fd.get("status") != FD.ST_OK:
        return "FIDELITY_" + str(fd.get("status") or "MISSING") + (":" + str(fd.get("primary_reason")) if fd.get("primary_reason") else "")
    if row.get("net_r") is None or row.get("risk_usdt") is None:
        return "NO_RISK"
    if not isinstance(row.get("targets"), list):
        return "TARGETS_MISSING"
    p = row.get("path") or {}
    if p.get("tf") not in ("1m", "5m") or bars is None or not len(bars):
        return "NO_PATH"
    return None


def analyze_trade(row: dict, *, prior: "PriorCtx | list[dict]", sv: StoreView, exec_par: dict | None, paths: EnginePaths,
                  bucket_cache: dict | None = None) -> dict[str, Any]:
    """Tek günlük satırının atıf satırı (kodlar + ızgara + ayrıştırma + rastgele kontrol + regime_fit)."""
    from .pathrec import read_path
    from .provider import raw_symbol
    pf = (row.get("path") or {}).get("file")
    bars = read_path(paths, pf) if pf else None
    inp = store_inputs(sv, row, prior, bars)
    attr = AT.attribute(row, inp)
    why = grid_eligibility(row, bars)
    grid: dict[str, Any]
    if why is None and exec_par is None:
        why = "NO_EXEC_MODEL"
    if why is not None:
        grid = {"version": CG.CFGRID_VERSION, "registry_sha": CG.CFGRID_SHA, "status": "NOT_ELIGIBLE", "reason": why}
    else:
        plan, err = CG.trade_plan(row, bars, atr=inp.atr_entry)
        if plan is None:
            grid = {"version": CG.CFGRID_VERSION, "registry_sha": CG.CFGRID_SHA, "status": "NOT_ELIGIBLE", "reason": err}
        else:
            tf = str((row.get("path") or {}).get("tf"))
            pb, tf2 = CG.coarsen(bars[["timestamp", "open", "high", "low", "close", "phase"]].reset_index(drop=True), tf)
            sym = raw_symbol(plan.symbol)
            step = TF_MS[tf2]
            eb0 = (plan.opened_ms // step) * step
            ent_bar = sv.frame("futures", sym, tf, (plan.opened_ms // TF_MS[tf]) * TF_MS[tf], plan.opened_ms)
            if ent_bar is not None and len(ent_bar) and tf2 != tf:
                ent_bar = pd.DataFrame({"timestamp": [eb0], "open": [float(ent_bar["open"].iloc[0])],
                                        "high": [float(ent_bar["high"].max())], "low": [float(ent_bar["low"].min())],
                                        "close": [float(ent_bar["close"].iloc[-1])]})
            ci = row.get("cf_inputs") if isinstance(row.get("cf_inputs"), dict) else {}
            hours = tuple(ci.get("funding_hours_utc") or ()) or None
            model = FD.exec_model(exec_par, hours=hours)
            a = plan.opened_ms - (CG.RC_LOOKBACK_DAYS + 2) * DAY_MS
            fdf = sv.frame("futures", sym, "funding", a, plan.horizon_ms)
            sfund = CG.StoreFunding(fdf)
            funding = CG.ChainFunding(CG.recorded_funding(ci), sfund)
            env = CG.GridEnv(plan=plan, bars=pb, tf=tf2, entry_bar=ent_bar, model=model, funding=funding,
                             history=prior.overshoot_history() if isinstance(prior, PriorCtx) else
                             CG.overshoot_history(prior))
            h4 = sv.frame("futures", sym, "4h", a - 220 * TF_MS["4h"], plan.opened_ms)
            rc = CG.random_control(plan, h4=h4, bars_5m=lambda x, y: sv.frame("futures", sym, "5m", x, y), model=model,
                                   funding=sfund, cache=bucket_cache)
            grid = CG.evaluate(env, rc)
    rf = prior.regime_fit(row) if isinstance(prior, PriorCtx) else CG.regime_fit(row, prior)
    keep = ("trade_key", "book", "book_name", "symbol", "side", "tactic", "variation_id", "config_epoch", "opened_at",
            "closed_at", "exit_reason", "exit_basis", "net_pnl", "net_r", "gross_r", "cost_r", "fees_total", "funding_net",
            "slippage_cost", "mfe_r", "mae_r", "order", "capture_ratio", "path_source", "kind", "position_group", "rev",
            "stop_dist_pct", "cohort")
    return {"schema": ATTR_SCHEMA, **{k: row.get(k) for k in keep}, "fidelity": {k: (row.get("fidelity") or {}).get(k) for k in (
                "status", "delta_r", "primary_reason")},
            "attribution": attr, "grid": grid, "regime_fit": rf, "analysis_version": ANALYSIS_VERSION}


# ============================================================================ S2
def _exec_by_book(paths: EnginePaths) -> dict[str, dict | None]:
    from .journal import _exec_params_by_book
    return _exec_params_by_book(paths)


class BookPrior:
    """Bir defterin `(closed_at, trade_key)` sıralı izdüşümleri ve önek yapıları: bir işlemin "öncekileri" (`closed_at <
    opened_at`) için gürültü bandı, aşma geçmişi, `regime_fit` ve önek özeti O(log n) ile bulunur (her işlem için önceki
    satırlar yeniden taranmaz). Sonuçlar `attribution.noise_band` / `cfgrid.overshoot_history` / `cfgrid.regime_fit`'in
    öncekiler listesiyle verdiğiyle AYNIDIR (test)."""

    def __init__(self, rs: list[dict]) -> None:
        self.rs = sorted(rs, key=AT.closed_key)
        self.closed = [AT.closed_key(r)[0] for r in self.rs]
        self.pref: list[str] = []
        h = hashlib.sha256()
        self.pref.append(h.hexdigest())
        self.win_pos: list[int] = []
        self.win_val: list[float] = []
        self.ov_pos: list[int] = []
        self.ov_rows: list[dict] = []
        self.reg: dict[tuple, tuple[list[int], list[float]]] = {}
        for i, r in enumerate(self.rs):
            se = r.get("situation_entry") if isinstance(r.get("situation_entry"), dict) else {}
            ci = r.get("cf_inputs") if isinstance(r.get("cf_inputs"), dict) else {}
            item = [r.get("trade_key"), r.get("net_r"), (r.get("path") or {}).get("mae_pct"), r.get("tactic"),
                    se.get("h4_trend"), se.get("h4_vol_regime"), ci.get("exit_overshoot_pct")]
            h.update(_canon(item).encode("utf-8"))
            self.pref.append(h.hexdigest())
            mp = AT._r6((r.get("path") or {}).get("mae_pct")) if isinstance(r.get("path"), dict) else None
            if AT.outcome_of(r) == AT.WIN and mp is not None:
                self.win_pos.append(i)
                self.win_val.append(abs(mp))
            if ci.get("exit_overshoot_pct") is not None:
                self.ov_pos.append(i)
                self.ov_rows.append(r)
            b = AT._situ(r, "situation_entry")
            if None not in b and r.get("net_r") is not None:
                pos, acc = self.reg.setdefault((r.get("tactic"), b), ([], [0.0]))
                pos.append(i)
                acc.append(acc[-1] + float(r["net_r"]))

    def k_of(self, row: dict) -> int:
        o = parse_ts(row.get("opened_at"))
        return bisect.bisect_left(self.closed, iso(o)) if o is not None else 0       # closed_at < opened_at


class PriorCtx:
    """Bir işlemin öncekileri (defterin ilk `k` satırı)."""

    def __init__(self, bp: BookPrior | None, k: int) -> None:
        self.bp, self.k = bp, k

    @property
    def digest(self) -> str:
        return self.bp.pref[self.k] if self.bp is not None else hashlib.sha256().hexdigest()

    def rows(self) -> list[dict]:
        return self.bp.rs[:self.k] if self.bp is not None else []

    def noise_band(self) -> tuple[float | None, int]:
        if self.bp is None:
            return None, 0
        j = bisect.bisect_left(self.bp.win_pos, self.k)
        vals = sorted(self.bp.win_val[max(0, j - int(AT.TH["NOISE_BAND_WINDOW"])):j])
        if not vals:
            return None, 0
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
        return AT._r6(med), n

    def overshoot_history(self) -> list[Any]:
        if self.bp is None:
            return []
        j = bisect.bisect_left(self.bp.ov_pos, self.k)
        return CG.overshoot_history(self.bp.ov_rows[max(0, j - CG.aux_overshoot_window()):j])

    def regime_fit(self, row: dict) -> dict[str, Any]:
        own = AT._situ(row, "situation_entry")
        if None in own:
            return {"source": "JOURNAL_PRIOR", "status": "NO_BUCKET", "n": 0, "mean_r": None}
        pos, acc = (self.bp.reg.get((row.get("tactic"), own)) if self.bp is not None else None) or ([], [0.0])
        n = bisect.bisect_left(pos, self.k)
        return CG.regime_fit_result(own, n, acc[n])


def _prior_index(rows: list[dict]) -> dict[str, BookPrior]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r.get("book")), []).append(r)
    return {b: BookPrior(rs) for b, rs in by.items()}


def _prior_of(pidx: dict[str, BookPrior], row: dict) -> PriorCtx:
    bp = pidx.get(str(row.get("book")))
    return PriorCtx(bp, bp.k_of(row) if bp is not None else 0)


def _store_digest(src: Any, row: dict) -> str:
    """Satırın S2'de okuyabileceği depo ay parçaları (seri, ay, sha): [açılış − 125 g − 4h pencere, kapanış + 2 g]."""
    if src is None:
        return "no-store"
    from .provider import raw_symbol
    from .store import month_bounds_ym
    om, cm = _ms(row.get("opened_at")), _ms(row.get("closed_at"))
    if om is None or cm is None:
        return "no-time"
    a, b = om - (CG.RC_LOOKBACK_DAYS + 40) * DAY_MS - 230 * TF_MS["4h"], cm + 2 * DAY_MS
    sym = raw_symbol(str(row.get("symbol") or ""))
    out = []
    for mk, s, tf in [("futures", sym, tf) for tf in ("1m", "5m", "1h", "4h", "1d", "funding")] + [("futures", "BTCUSDT", "5m")]:
        lst = src._listing(mk, s, tf)
        for ym in sorted(lst):
            lo, hi = month_bounds_ym(ym)
            if hi > a and lo <= b:
                out.append((mk, s, tf, ym, lst[ym]))
    return _sha(out)


def attr_month_file(paths: EnginePaths, month: str) -> Path:
    return paths.attribution / f"{month}.jsonl.gz"


def _read_index(paths: EnginePaths) -> dict[str, dict]:
    try:
        with gzip.open(paths.attribution / "_index.json.gz", "rt", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError, EOFError):
        return {}
    if not isinstance(d, dict) or d.get("schema") != INDEX_SCHEMA or d.get("version") != ANALYSIS_VERSION:
        return {}
    return dict(d.get("keys") or {})


def iter_attribution(paths: EnginePaths, months: Iterable[str] | None = None) -> Iterator[dict]:
    root = paths.attribution
    if not root.is_dir():
        return
    want = set(months) if months is not None else None
    for p in sorted(root.glob("????-??.jsonl.gz")):
        if want is not None and p.name[:7] not in want:
            continue
        yield from _iter_gz(p)


def _error_row(r: dict, exc: BaseException) -> dict[str, Any]:
    """Hesaplanamayan işlemin satırı: kod yok (uydurulmaz), neden yazılır; ızgara `NOT_ELIGIBLE (ERROR:…)`."""
    why = f"{type(exc).__name__}: {exc}"[:200]
    keep = ("trade_key", "book", "book_name", "symbol", "side", "tactic", "opened_at", "closed_at", "exit_reason", "net_pnl",
            "net_r", "kind", "stop_dist_pct", "config_epoch")
    return {"schema": ATTR_SCHEMA, **{k: r.get(k) for k in keep},
            "attribution": {"version": AT.ATTRIBUTION_VERSION, "registry_sha": AT.ATTRIBUTION_SHA, "outcome": AT.outcome_of(r),
                            "codes_loss": [], "codes_win": [], "primary_code": None, "evidence": {}, "error": why},
            "grid": {"version": CG.CFGRID_VERSION, "registry_sha": CG.CFGRID_SHA, "status": "NOT_ELIGIBLE",
                     "reason": "ERROR:" + type(exc).__name__, "error": why},
            "regime_fit": None, "analysis_version": ANALYSIS_VERSION}


def _proj(r: dict) -> dict:
    """Önceki-satır ölçüleri (gürültü bandı, aşma geçmişi, regime_fit) için küçük izdüşüm."""
    se = r.get("situation_entry") if isinstance(r.get("situation_entry"), dict) else {}
    ci = r.get("cf_inputs") if isinstance(r.get("cf_inputs"), dict) else {}
    return {"trade_key": r.get("trade_key"), "book": r.get("book"), "tactic": r.get("tactic"), "opened_at": r.get("opened_at"),
            "closed_at": r.get("closed_at"), "net_r": r.get("net_r"), "net_pnl": r.get("net_pnl"),
            "path": {"mae_pct": (r.get("path") or {}).get("mae_pct")},
            "situation_entry": {"h4_trend": se.get("h4_trend"), "h4_vol_regime": se.get("h4_vol_regime")},
            "cf_inputs": {"exit_overshoot_pct": ci.get("exit_overshoot_pct")}}


def _journal_months_newest_first(paths: EnginePaths) -> Iterator[tuple[str, dict]]:
    """(ay, satır) — en yeni AY önce; ay içinde dosya sırası (akış; bellekte tek satır)."""
    root = paths.journal_tj
    if not root.is_dir():
        return
    for p in sorted(root.glob("????-??.jsonl.gz"), reverse=True):
        for r in _iter_gz(p):
            yield p.name[:7], r


def _attr_sort_key(r: dict) -> tuple[str, str]:
    return (str(r.get("closed_at") or ""), str(r.get("trade_key")))


def _write_attr_month(paths: EnginePaths, month: str, built: dict[str, dict], new_index: dict[str, dict]) -> bool:
    from .journal import merge_sorted_month
    return merge_sorted_month(paths, attr_month_file(paths, month), built,
                              lambda k: k in new_index and new_index[k]["month"] == month, _attr_sort_key)


def run_s2(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None, src: Any = None,
           budget_s: float | None = None, max_rows: int | None = None, open_src: bool = True) -> dict[str, Any]:
    """S2 (§6.1): kodlar + `cfgrid_v1` + ayrıştırma, artımlı. Dönen: özet (`_build.json` içeriği + çalıştırma sayıları).
    Bellek: günlük iki kez AKIŞLA okunur (1: özet/izdüşüm, 2: yalnız hesaplanacak satırlar, en yeni ay önce); bir anda
    en çok bir ayın satırları bellektedir; depo parça önbelleği sınırlıdır."""
    from .journal import iter_journal, open_store
    now = now or utc_now()
    sinfo: dict[str, Any] = {"status": "GIVEN" if src is not None else "NONE"}
    if src is None and open_src:
        src, sinfo = open_store(paths)
    if src is not None and hasattr(src, "max_parts"):
        src.max_parts = min(int(src.max_parts), S2_MAX_PARTS)
    exec_by = _exec_by_book(paths)
    seal_d = str(getattr(src, "seal", None) or "no-store")
    old = _read_index(paths)
    projs, digests = [], {}
    for r in iter_journal(paths):                                # geçiş 1: izdüşüm + satır özeti
        projs.append(_proj(r))
        digests[str(r.get("trade_key"))] = (_sha(r), _store_digest(src, r))
    pidx = _prior_index(projs)
    new_index: dict[str, dict] = {}
    todo: set[str] = set()
    for pr in projs:
        k = str(pr.get("trade_key"))
        pdg = _prior_of(pidx, pr).digest
        rsha, sdg = digests[k]
        dg = _sha({"v": ANALYSIS_VERSION, "a": AT.ATTRIBUTION_SHA, "g": CG.CFGRID_SHA, "row": rsha, "prior": pdg,
                   "exec": _sha(exec_by.get(str(pr.get("book")))), "store": sdg})
        t = parse_ts(pr.get("closed_at"))
        month = t.strftime("%Y-%m") if t is not None else "unknown"
        new_index[k] = {"digest": dg, "month": month, "book": pr.get("book")}
        o = old.get(k)
        if o is None or o.get("digest") != dg or not attr_month_file(paths, month).exists():
            todo.add(k)
    t0 = time.monotonic()
    bucket_cache: dict = {}
    pending, moving = [], []
    n_built, written, errors = 0, [], 0
    from .journal import MonthWriter, clean_spills
    clean_spills(paths, paths.attribution)
    writer: MonthWriter | None = None
    cur_month = None
    n_month = 0

    def _close() -> None:
        if writer is not None and n_month and writer.close() and cur_month not in written:
            written.append(cur_month)

    for month, r in _journal_months_newest_first(paths):        # geçiş 2: yalnız hesaplanacaklar, en yeni ay önce
        k = str(r.get("trade_key"))
        if k not in todo:
            continue
        if month != cur_month:
            _close()
            cur_month, n_month = month, 0
            writer = MonthWriter(paths, attr_month_file(paths, month),
                                 lambda kk, _m=month: kk in new_index and new_index[kk]["month"] == _m, _attr_sort_key,
                                 flush_rows=FLUSH_ROWS)
        if (budget_s is not None and time.monotonic() - t0 > budget_s) or (max_rows is not None and n_built >= max_rows):
            pending.append(k)
            continue
        prior = _prior_of(pidx, r)
        sv = StoreView(src)
        try:
            out = analyze_trade(r, prior=prior, sv=sv, exec_par=exec_by.get(str(r.get("book"))), paths=paths,
                                bucket_cache=bucket_cache)
        except Exception as exc:  # noqa: BLE001 — tek işlemin arızası aşamayı düşürmez; satır nedeniyle yazılır
            errors += 1
            out = _error_row(r, exc)
        if sv.moving:
            moving.append(k)
            continue
        from .store import ResearchStore
        out["data_seal"] = ResearchStore.seal_of(sorted(sv.used)) if sv.used else None
        writer.add(k, out)
        n_month += 1
        n_built += 1
    _close()
    for k in pending + moving:                          # kurulamayan: eski satır kalır (yoksa dizine girmez)
        o = old.get(k)
        if o is not None and attr_month_file(paths, str(o.get("month"))).exists():
            new_index[k] = o
        else:
            new_index.pop(k, None)
    drop = {v.get("month") for k, v in old.items() if k not in new_index or new_index[k]["month"] != v.get("month")}
    drop.discard(None)
    for month in sorted(drop):
        if _write_attr_month(paths, month, {}, new_index) and month not in written:
            written.append(month)
    idx = {"schema": INDEX_SCHEMA, "version": ANALYSIS_VERSION, "keys": dict(sorted(new_index.items()))}
    ip = paths.attribution / "_index.json.gz"
    idata = gzip_bytes(json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if not (ip.exists() and ip.read_bytes() == idata):
        paths.write_bytes(ip, idata)
    summ = summarize(iter_attribution(paths))
    content = {"schema": BUILD_SCHEMA, "version": ANALYSIS_VERSION, "attribution_sha": AT.ATTRIBUTION_SHA,
               "cfgrid_sha": CG.CFGRID_SHA, "store_seal": seal_d, **summ,
               "sanity_5_10_box_stop_width": sanity_box_stop_width(iter_attribution(paths))}
    p = paths.attribution / "_build.json"
    data = (json.dumps(content, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if not (p.exists() and p.read_bytes() == data):
        paths.write_bytes(p, data)
    return {"stage": "S2", "run_id": run_id, "store": sinfo, "built_rows": n_built, "pending": len(pending), "errors": errors,
            "data_moving": len(moving), "months_written": sorted(written), "rows": content["rows"],
            "grid": content["grid"], "codes": content["codes"], "attribution_sha": AT.ATTRIBUTION_SHA,
            "cfgrid_sha": CG.CFGRID_SHA, "built_at": iso(now)}


# ============================================================================ toplu görünümler (özet + sorgu)
def summarize(rows: Iterable[dict]) -> dict[str, Any]:
    """Atıf satırlarının küçük toplu özeti (deterministik; tek geçiş, akış)."""
    codes: dict[str, int] = {}
    prim: dict[str, int] = {}
    grid: dict[str, int] = {}
    n = 0
    for r in rows:
        n += 1
        a = r.get("attribution") or {}
        for c in (a.get("codes_loss") or []) + (a.get("codes_win") or []):
            codes[c] = codes.get(c, 0) + 1
        pc = a.get("primary_code") or "—"
        prim[pc] = prim.get(pc, 0) + 1
        g = r.get("grid") or {}
        key = "OK" if g.get("status") == "OK" else "NE:" + str(g.get("reason") or "?").split(":")[0]
        grid[key] = grid.get(key, 0) + 1
    return {"rows": n, "codes": dict(sorted(codes.items())), "primary": dict(sorted(prim.items())),
            "grid": dict(sorted(grid.items()))}


#: §5.10 akıl sağlığı (tarihsel yeniden üretim; aday DEĞİL): Box stop genişliği kovaları (% giriş), config dönemine bölünmüş
SANITY_BOX_BOOK = "strategy_paper_box"
SANITY_STOP_BUCKETS: tuple[tuple[str, float, float], ...] = (("<0.5", 0.0, 0.5), ("0.5-1", 0.5, 1.0), (">=1", 1.0, 1e9))


def sanity_box_stop_width(rows: Iterable[dict]) -> dict[str, Any]:
    """§5.10: Box'ın stop genişliği bulgusu, config dönemi BAŞINA (dönem sınırını aşan karşılaştırma yok). Kova başına
    n, ortalama net R ve birincil/tüm kayıp kodlarının sayımı (STOP_TOO_TIGHT / TIGHT_STOP_COST_MULTIPLIER beklenir).
    Uygulanmış sahip kararı (`min_stop_pct: 0.5`, 2026-10-03) için aday ÜRETİLMEZ; yeni dönem yalnız izlenir."""
    out: dict[str, Any] = {}
    for r in rows:
        if str(r.get("book")) != SANITY_BOX_BOOK or r.get("net_r") is None:
            continue
        sdp = r.get("stop_dist_pct")
        if sdp is None:
            continue
        ep = str(r.get("config_epoch") or "?")
        b = next(k for k, lo, hi in SANITY_STOP_BUCKETS if lo <= float(sdp) < hi)
        acc = out.setdefault(ep, {}).setdefault(b, {"n": 0, "sum_r": 0.0, "codes": {}})
        acc["n"] += 1
        acc["sum_r"] += float(r["net_r"])
        for c in (r.get("attribution") or {}).get("codes_loss") or []:
            acc["codes"][c] = acc["codes"].get(c, 0) + 1
    res: dict[str, Any] = {}
    for ep, bs in sorted(out.items()):
        res[ep] = {b: {"n": v["n"], "mean_r": round(v["sum_r"] / v["n"], 4) + 0.0,
                       "codes": dict(sorted(v["codes"].items(), key=lambda x: (-x[1], x[0])))} for b, v in sorted(bs.items())}
        t, w = res[ep].get("<0.5"), res[ep].get("0.5-1")
        if t and w:
            top = ", ".join(f"{c} {n}" for c, n in list(t["codes"].items())[:3]) or "—"
            def _r(x: float) -> str:
                return f"{x:+.2f}".replace(".", ",").replace("-", "−")
            res[ep]["finding_tr"] = (
                f"Box {ep}: stop < %0,5 → {t['n']} işlem, ort. {_r(t['mean_r'])}R; %0,5–1 → {w['n']} işlem, "
                f"ort. {_r(w['mean_r'])}R; dar kovanın baskın kayıp kodları: {top}. Tarihsel yeniden üretim (§5.10), aday "
                "değil; uygulanmış sahip kararı (min_stop_pct 0,5, 2026-10-03) yalnız izlenir.")
    return res


def cell_map(row: dict) -> dict[str, dict]:
    return {c["id"]: c for c in ((row.get("grid") or {}).get("cells") or [])}


def rank_r(c: dict | None) -> float | None:
    if not c or c.get("status") not in ("OK", "MISSED"):
        return None
    return c.get("r_cons") if c.get("r_cons") is not None else c.get("r_net")


class VariantAcc:
    """EX_ANTE varyant başına EŞLİ fark (hücre − taban) birikimi — akışla (bellekte satır tutulmaz)."""

    def __init__(self) -> None:
        self.acc: dict[str, list[float]] = {}            # vid → [n, toplam, daha iyi sayısı]
        self.days: dict[str, set[str]] = {}

    def add(self, r: dict) -> None:
        cm = cell_map(r)
        base = rank_r(cm.get("E_ACTUAL"))
        if base is None:
            return
        day = str(r.get("closed_at") or "")[:10]
        for vid in CG.VARIANT_IDS:
            if vid == "E_ACTUAL":
                continue
            v = rank_r(cm.get(vid))
            if v is None:
                continue
            a = self.acc.setdefault(vid, [0, 0.0, 0])
            a[0] += 1
            a[1] += v - base
            a[2] += 1 if v - base > 0 else 0
            self.days.setdefault(vid, set()).add(day)

    def stats(self, *, min_n: int = 1) -> dict[str, dict[str, Any]]:
        out = {}
        for vid, (n, tot, better) in self.acc.items():
            if n < min_n:
                continue
            out[vid] = {"n": int(n), "n_days": len(self.days[vid]), "mean_delta_r": round(tot / n, 4) + 0.0,
                        "share_better": round(better / n, 3) + 0.0}
        return dict(sorted(out.items()))

    def best(self, *, min_n: int = 5) -> tuple[str, dict] | None:
        st = self.stats(min_n=min_n)
        if not st:
            return None
        vid = max(st, key=lambda v: (st[v]["mean_delta_r"], v))
        return vid, st[vid]


def variant_stats(rows: Iterable[dict], *, min_n: int = 1) -> dict[str, dict[str, Any]]:
    """EX_ANTE varyant başına EŞLİ fark (hücre − taban), sıralama muhafazakâr R ile (`r_rank`). Sabit ızgara noktası her
    işlemde aynıdır → bu bir EX_ANTE kuralının ara görünümüdür (ders değil; ders P3'te, n ≥ 30 ve ≥ 10 gün)."""
    acc = VariantAcc()
    for r in rows:
        acc.add(r)
    return acc.stats(min_n=min_n)


def best_ex_ante(rows: Iterable[dict], *, min_n: int = 5) -> tuple[str, dict] | None:
    acc = VariantAcc()
    for r in rows:
        acc.add(r)
    return acc.best(min_n=min_n)


def iter_window(paths: EnginePaths, *, since: datetime, until: datetime) -> Iterator[dict]:
    """`[since, until]` aralığında kapanmış atıf satırları — akışla (ay dosyaları sırayla)."""
    for r in iter_attribution(paths, months_for(since, until)):
        t = parse_ts(r.get("closed_at"))
        if t is not None and since <= t <= until:
            yield r


def rows_in_day(rows: Iterable[dict], day: str) -> list[dict]:
    return [r for r in rows if str(r.get("closed_at") or "")[:10] == day]


def rows_since(rows: Iterable[dict], since: datetime) -> list[dict]:
    out = []
    for r in rows:
        t = parse_ts(r.get("closed_at"))
        if t is not None and t >= since:
            out.append(r)
    return out


def months_for(since: datetime, until: datetime) -> list[str]:
    out, t = [], since.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while t <= until:
        out.append(t.strftime("%Y-%m"))
        t = (t + timedelta(days=32)).replace(day=1)
    return out


def load_window(paths: EnginePaths, *, since: datetime, until: datetime) -> list[dict]:
    return rows_since(iter_attribution(paths, months_for(since, until)), since)


def build_status(paths: EnginePaths) -> dict | None:
    try:
        with open(paths.attribution / "_build.json", "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


__all__ = ["ANALYSIS_VERSION", "ATTR_SCHEMA", "BookPrior", "PriorCtx", "StoreView", "VariantAcc", "analyze_trade", "atr_pct_at",
           "attr_month_file", "best_ex_ante", "build_status", "cell_map", "grid_eligibility", "iter_attribution", "iter_window",
           "load_window", "rank_r", "rows_in_day", "rows_since", "run_s2", "sanity_box_stop_width", "store_inputs", "summarize",
           "variant_stats"]

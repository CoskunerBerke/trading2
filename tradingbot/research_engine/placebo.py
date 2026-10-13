"""P3d — plasebo (docs/ENGINE_BRAIN_V1.md §3.6; mühür `STATS_V1.placebo`; laboratuvar geleneği, `signal_lab`).

* **Her gerçek işlem için K = 5 (mühür) rastgele işlem:** aynı sembol, aynı yön, aynı tutuş süresi (sinyal barı), aynı ATR
  katı stop (gerçek işlemin |sinyal kapanışı − ilk stop| / ATR14; plasebo stopu = kendi sinyal kapanışı ∓ aynı kat × kendi
  ATR14'ü); giriş zamanı AYNI test katmanında kuralın sinyal vermediği karar anlarından (gerçek işlemlerin ± tutuşu hariç:
  |t − gerçek giriş| ≤ gerçek tutuş olan anlar atılır) düzgün çekilir.
* **Tohum (B6, mühür):** `random.Random(int(sha256(f"{row_id}|{trade_key}|{k}").hexdigest()[:16], 16))`; `trade_key` =
  tj_v1 biçimi `defter|trade_id|opened_at` (çekirdek işleminde defter = varyant, trade_id = `sembol:sinyal_ts:yön`,
  `wf.trade_key`). Çekim `rng.randrange(len(uygun))`. Plasebo girişini çekirdek kapıları reddederse (boşluk stopun ötesinde,
  büyüklük, veri boşluğu) AYNI üreteçten yeniden çekilir (en çok `MAX_TRIES`; tükenirse plasebo eksik sayılır) — okunuş.
* **Çekirdek yolu:** plasebo işlemi `kernel.replay_intent`'in zorlanmış yeniden oynatımıdır (aynı dolum, maliyet, 1h yol,
  fonlama, kapı dışı işaretler); varyant sinyal vermeyen ve yalnız "tut" diyen bir vekildir (`_HoldVariant`), çıkış stop /
  likidasyon ya da zaman stopudur (`max_hold_bars` = gerçek işlemin tutuşunu veren değer). Kuralın sinyal anları
  `SignalRecorder` vekiliyle (sonuç bayt-aynı) kaydedilir. Kapı dışı plasebo işlemleri (`kernel.gate_excluded`) de çıkar.
* **Fark:** gerçek − plasebo ortalaması (ADVERSE R), gün kümelenmeli %95 CI (mühür düzeyi): iki taraf AYRI ve bağımsız
  gün kümeli bootstrap (signal_lab SIKI kuralıyla aynı düzen: gün başına toplam ve sayı, tekrar ortalaması = Σ toplam /
  Σ sayı), yüzdelikler ⌊α/2·(B−1)⌋ / ⌈(1−α/2)·(B−1)⌉. **Mühürde YOK (okunuş, §8.4 P3d notu):** tekrar sayısı B (= p_day'in
  mühürlü 10.000'i), tohumlar (B6 biçimi, anahtar `row_id|look_no|placebo_ci|real` ve `…|placebo`), taraf başına en az gün
  (laboratuvarın 5'i; altında CI yok → kapı geçmez).
"""
from __future__ import annotations

import bisect
import math
import random
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from . import kernel as KN
from . import stats as ST
from ..strategy_lib import LONG, NONE, TF_MS, EntryRef, Intent
from ..strategy_lib.indicators import atr

DAY_MS = 86_400_000
K_PER_TRADE = ST.PLACEBO_K
MAX_TRIES = 20
MIN_CI_DAYS = 5
ATR_N = 14


def placebo_seed(row_id: str, trade_key: str, k: int) -> int:
    return ST.seed_int(f"{row_id}|{trade_key}|{k}")


def eligible_times(decisions: Sequence[int], start_ms: int, end_ms: int, signal_times: Iterable[int],
                   real_spans: Iterable[tuple[int, int]]) -> list[int]:
    """[start, end) içindeki karar anları − kuralın sinyal anları − gerçek işlemlerin ± tutuşu (|t − giriş| ≤ tutuş)."""
    sig = set(int(x) for x in signal_times)
    spans = sorted((int(e) - int(h), int(e) + int(h)) for e, h in real_spans)
    los = [a for a, _b in spans]
    hi_pref: list[int] = []
    m = -math.inf
    for _a, b in spans:
        m = max(m, b)
        hi_pref.append(m)
    out = []
    for t in decisions:
        t = int(t)
        if not (start_ms <= t < end_ms) or t in sig:
            continue
        j = bisect.bisect_right(los, t) - 1                    # başı ≤ t olan aralıkların en uzun sonu
        if j >= 0 and hi_pref[j] >= t:
            continue
        out.append(t)
    return out


def draw_placebos(row_id: str, trade_key: str, eligible: Sequence[int], simulate: Callable[[int], dict | None], *,
                  k_per_trade: int = K_PER_TRADE, max_tries: int = MAX_TRIES) -> list[dict[str, Any]]:
    """Bir gerçek işlemin K plasebosu: k başına kendi üreteci; çekirdek reddederse aynı üreteçten yeniden çekim."""
    out: list[dict[str, Any]] = []
    for k in range(k_per_trade):
        rng = random.Random(placebo_seed(row_id, trade_key, k))
        res, tries, t = None, 0, None
        while eligible and tries < max_tries:
            t = int(eligible[rng.randrange(len(eligible))])
            tries += 1
            res = simulate(t)
            if res is not None:
                break
        if res is None:
            out.append({"k": k, "trade_key": trade_key, "missing": True, "tries": tries})
        else:
            out.append(dict(res, k=k, trade_key=trade_key, decision_ms=t, tries=tries, missing=False))
    return out


def gate_pairs(placebos: Iterable[Mapping[str, Any]], cost: str = ST.GATE_COST) -> list[tuple[int, float]]:
    """Kapıya giren plasebolar: eksik ve kapı dışı olanlar çıkar → [(gün, R)]."""
    return [(int(p["day"]), float(p["r"][cost])) for p in placebos if not p.get("missing") and not p.get("excluded")]


# ============================================================================ çekirdek yolu
class _HoldVariant:
    """Plasebonun çekirdek vekili: sinyal yok, yönetim "tut"; çıkış yalnız stop/likidasyon ve zaman stopu."""

    family = "PLACEBO"
    needs_btc = False
    btc_lookback = 0
    needs_funding = False
    funding_lookback_ms = 0
    needs_btc_4h = False
    btc_4h_lookback = 0
    live_modules: tuple[str, ...] = ()
    params: Mapping[str, Any] = {}

    def __init__(self, base: Any) -> None:
        self.variant_id = f"PLACEBO:{base.variant_id}"
        self.signal_tf = base.signal_tf
        self.timeframes = (base.signal_tf,)
        self.lookback = {base.signal_tf: 1}

    def signals(self, bars: Any, ctx: Any) -> list:
        return []

    def manage(self, pos: Any, bars: Any, ctx: Any) -> Any:
        return NONE


class SignalRecorder:
    """Varyant vekili: `signals` niyet döndürdüğünde karar anını (`ctx["now_ms"]`) kaydeder; diğer her öznitelik ve çağrı
    sarılan varyantınkidir (çekirdek sonucu bayt-aynı — test)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.signal_times: set[int] = set()

    def signals(self, bars: Any, ctx: Mapping[str, Any]) -> list:
        out = self._inner.signals(bars, ctx)
        if out:
            self.signal_times.add(int(ctx["now_ms"]))
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class KernelPlacebo:
    """Bir varyant × sembol için plasebo üreticisi (çekirdeğin zorlanmış yeniden oynatımı)."""

    def __init__(self, variant: Any, data: KN.SeriesData) -> None:
        self.base = variant
        self.var = _HoldVariant(variant)
        self.data = data
        self.tf = variant.signal_tf
        self.step = TF_MS[self.tf]
        rows = list(data.bars[self.tf])
        self.ts = [int(r["timestamp"]) for r in rows]
        self.close = [float(r["close"]) for r in rows]
        self.atr = atr([float(r["high"]) for r in rows], [float(r["low"]) for r in rows], self.close, ATR_N)
        self.decisions = [t + self.step for t in self.ts[:-1]]      # dolum için sonraki bar gerekir

    def _i(self, signal_ts: int) -> int | None:
        i = bisect.bisect_left(self.ts, int(signal_ts))
        return i if i < len(self.ts) and self.ts[i] == int(signal_ts) else None

    def stop_mult(self, trade: Mapping[str, Any]) -> float | None:
        i = self._i(trade["signal_ts"])
        if i is None:
            return None
        a = self.atr[i]
        if not (isinstance(a, float) and math.isfinite(a) and a > 0):
            return None
        return abs(self.close[i] - float(trade["initial_stop"])) / a

    def simulator(self, trade: Mapping[str, Any]) -> Callable[[int], dict | None]:
        k_atr = self.stop_mult(trade)
        side = str(trade["side"])
        hold = max(1, int(trade["bars_held"]) - 1)        # çekirdek: tutuş = karar barından zaman stopu açılışına
        long = side == LONG

        def sim(t: int) -> dict | None:
            if k_atr is None or not k_atr > 0:
                return None
            i = self._i(int(t) - self.step)
            if i is None:
                return None
            a = self.atr[i]
            if not (isinstance(a, float) and math.isfinite(a) and a > 0):
                return None
            c = self.close[i]
            stop = c - k_atr * a if long else c + k_atr * a
            if not stop > 0:
                return None
            intent = Intent(signal_ts=self.ts[i], side=side, entry_ref=EntryRef(price=c), stop=stop, max_hold_bars=hold)
            tr, _why = KN.replay_intent(self.var, self.data, intent, decision_ms=int(t))
            if tr is None:
                return None
            return {"day": int(tr.entry_ts) // DAY_MS, "entry_ts": int(tr.entry_ts), "r": dict(tr.r),
                    "flags": list(tr.flags), "excluded": KN.gate_excluded(tr), "bars_held": int(tr.bars_held),
                    "side": tr.side, "stop_mult": k_atr, "max_hold_bars": hold, "exit_kind": tr.exit_kind}
        return sim

    def for_row(self, row_id: str, real: Sequence[Mapping[str, Any]], all_trades: Sequence[Mapping[str, Any]],
                signal_times: Iterable[int], *, k_per_trade: int = K_PER_TRADE) -> list[dict[str, Any]]:
        """`real`: satırın bu sembolde OOS'a giren gerçek işlemleri (her biri `test_window` = [başlangıç, bitiş) taşır);
        `all_trades`: varyantın bu sembolde BÜTÜN işlemleri (± tutuş dışlaması); `signal_times`: kuralın sinyal anları."""
        spans = [(int(x["ts_ms"]), max(int(x["exit_ms"]) - int(x["ts_ms"]), self.step)) for x in all_trades]
        sig = set(int(x) for x in signal_times)
        cache: dict[tuple[int, int], list[int]] = {}
        out: list[dict[str, Any]] = []
        for tr in real:
            w = (int(tr["test_window"][0]), int(tr["test_window"][1]))
            if w not in cache:
                cache[w] = eligible_times(self.decisions, w[0], w[1], sig, spans)
            out += draw_placebos(row_id, str(tr["trade_key"]), cache[w], self.simulator(tr), k_per_trade=k_per_trade)
        return out


# ============================================================================ fark CI
def _boot_means(pairs: Sequence[tuple[int, float]], seed: int, resamples: int, min_days: int) -> np.ndarray | None:
    acc: dict[int, list[float]] = {}
    for d, r in pairs:
        a = acc.setdefault(int(d), [0.0, 0])
        a[0] += float(r)
        a[1] += 1
    days = sorted(acc)
    if len(days) < min_days:
        return None
    s = np.array([acc[d][0] for d in days], dtype=float)
    n = np.array([acc[d][1] for d in days], dtype=float)
    parts = [s[idx].sum(axis=1) / n[idx].sum(axis=1) for idx in ST.choice_stream(seed, len(days), resamples)]
    return np.concatenate(parts)


def diff_ci(real: Sequence[tuple[int, float]], placebo: Sequence[tuple[int, float]], *, row_id: str, look_no: int,
            resamples: int = ST.RESAMPLES, level: float = ST.PLACEBO_CI_LEVEL, min_days: int = MIN_CI_DAYS
            ) -> list[float] | None:
    """(gerçek ort. R − plasebo ort. R) için gün kümelenmeli CI; taraflardan birinde gün < `min_days` → None."""
    a = _boot_means(real, ST.seed_int(f"{row_id}|{look_no}|placebo_ci|real"), resamples, min_days)
    b = _boot_means(placebo, ST.seed_int(f"{row_id}|{look_no}|placebo_ci|placebo"), resamples, min_days)
    if a is None or b is None:
        return None
    d = np.sort(a - b)
    alpha = 1.0 - float(level)
    lo = d[int(math.floor(alpha / 2.0 * (len(d) - 1)))]
    hi = d[int(math.ceil((1.0 - alpha / 2.0) * (len(d) - 1)))]
    return [float(lo), float(hi)]


__all__ = ["K_PER_TRADE", "KernelPlacebo", "MAX_TRIES", "MIN_CI_DAYS", "SignalRecorder", "diff_ci", "draw_placebos",
           "eligible_times", "gate_pairs", "placebo_seed"]

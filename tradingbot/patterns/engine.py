"""SimilarPatternEngine — deterministik benzer-olay getirme + maliyet-sonrası istatistiksel kanıt (fail-closed).

- Olay: (symbol, market, tf, bar i). Vektör: normalize getiri yolu (W bar, 16 noktaya indirgenmiş, z-skor) + özellik anlık görüntüsü
  (MA mesafe/eğim, momentum, volatilite, hacim, mum anatomisi, rejim, funding) — indeks genelinde standardize.
- Sonuç: triple-barrier (ATR stop, TP 1R/2R, ufuk) LONG ve SHORT için ayrı; net R (fee/slippage/funding sonrası).
- Sorgu: yalnız cutoff_ts < query_ts - embargo olan olaylar (geçmiş; ileri bakış yok); seviyeler: aynı coin / küme / bütün evren aynı rejim.
- Tekrar sayımı önleme: aynı sembolde min_separation bar zorunlu (overlap purge), aynı (symbol, event_ts) tekilleştirme.
- İstatistik: n, win/loss/BE, posterior P(win) (Beta), Wilson CI, mean/median net R, expectancy CI, payoff, PF, maxDD, MAE/MFE, çıkış dağılımı,
  30/90/180/360 gün pencereleri, edge decay, brüt/net; kodlar: INSUFFICIENT_SAMPLE, LOW_CONFIDENCE, NEGATIVE_EXPECTANCY, EDGE_DECAY,
  REGIME_MISMATCH, COST_ERODED_EDGE, DATA_INVALID.
- HIZLI SORGU (2026-10-06, docs/TOUR_CONTENTION_V2.md): sonuç eski olay-başına döngüyle BİREBİR aynıdır (komşular,
  sıraları, mesafeler, istatistik, kodlar). 1. aşama eski süzgeci vektörel uygular ve her aday için eski mesafenin
  KANITLI ALT SINIRINI motor başına bir kez kurulan float32 dizilerle hesaplar; 2. aşama adayları eski TAM sıralamanın
  sırasıyla üretir — mesafe yalnız gereken adaylarda eski kodla (`_pair_dist`) — ve seçimi eski döngü (`_select`) yapar.
  Geri dönüş: `fast_query = False` (config `history.evidence_fast_knn: false`) → eski döngü.
"""
from __future__ import annotations

import heapq
import logging
import math
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features import build_feature_frame
from .outcomes import Outcome, barriers_from_atr, triple_barrier

log = logging.getLogger(__name__)

WINDOWS = (16, 32, 64, 128)
PATH_POINTS = 16
SNAP_COLS = ["sma25_dist", "sma99_dist", "ema21_slope", "ema99_slope", "rsi14", "macd_hist", "adx14", "atr_pct", "vol_pctile", "rv_ratio",
             "vol_z", "rel_vol", "body_range", "close_loc", "consec_dir", "funding", "btc_regime", "dd50"]
DAY_MS = 86_400_000


def regime_code(row: pd.Series) -> str:
    """Basit, deterministik rejim etiketi: trend yönü (sma25 vs sma99) × volatilite (vol_pctile)."""
    tr = row.get("sma25_99_cross", 0.0)
    vp = row.get("vol_pctile", np.nan)
    trend = "UP" if tr > 0 else "DOWN" if tr < 0 else "FLAT"
    vol = "HIGHVOL" if (not np.isnan(vp) and vp >= 70) else "LOWVOL" if (not np.isnan(vp) and vp <= 30) else "MIDVOL"
    return f"{trend}_{vol}"


def _path_vec(close: np.ndarray, i: int, w: int) -> np.ndarray | None:
    if i - w < 0:
        return None
    seg = np.log(close[i - w + 1:i + 1] / close[i - w:i])
    if len(seg) < w or not np.isfinite(seg).all():
        return None
    cum = np.cumsum(seg)
    idx = np.linspace(0, len(cum) - 1, PATH_POINTS).round().astype(int)
    p = cum[idx]
    sd = p.std()
    return (p - p.mean()) / (sd if sd > 1e-12 else 1.0)


@dataclass
class PatternEvent:
    symbol: str
    market: str
    tf: str
    idx: int
    event_ts: int
    cutoff_ts: int
    regime: str
    cluster: str
    path: dict[int, np.ndarray]
    snap: np.ndarray
    outcomes: dict[str, Outcome]          # "LONG"/"SHORT" (spot'ta yalnız LONG)


@dataclass
class EvidenceStats:
    n: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    p_win_posterior: float = 0.5
    p_win_ci: tuple[float, float] = (0.0, 1.0)
    mean_net_r: float = 0.0
    median_net_r: float = 0.0
    expectancy_ci: tuple[float, float] = (0.0, 0.0)
    mean_gross_r: float = 0.0
    payoff_ratio: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_r: float = 0.0
    mae_pct_mean: float = 0.0
    mfe_pct_mean: float = 0.0
    exit_reasons: dict[str, int] = field(default_factory=dict)
    windows: dict[str, dict] = field(default_factory=dict)      # "30d"/"90d"/"180d"/"360d"/"all" → {n, mean_net_r}
    edge_decay: float = 0.0                                     # recent(90d) − all (net R)
    cost_drag_r: float = 0.0                                    # gross − net
    breakdown: dict[str, dict] = field(default_factory=dict)    # by symbol / regime / market
    codes: list[str] = field(default_factory=list)              # fail-closed nedenleri
    ok: bool = False

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["p_win_ci"] = list(self.p_win_ci); d["expectancy_ci"] = list(self.expectancy_ci)
        return d


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, cen - half), min(1.0, cen + half)


def compute_stats(outs: list[tuple[int, Outcome, str, str, str]], *, now_ts: int, min_sample: int = 30, min_expectancy: float = 0.0,
                  query_regime: str | None = None, be_r: float = 0.1) -> EvidenceStats:
    """outs: [(cutoff_ts, Outcome, symbol, regime, market)] — istatistik + fail-closed kodlar."""
    st = EvidenceStats()
    if not outs:
        st.codes = ["INSUFFICIENT_SAMPLE"]
        return st
    outs = sorted(outs, key=lambda x: x[0])
    r = np.array([o.net_r for _, o, *_ in outs], dtype=float)
    g = np.array([o.gross_r for _, o, *_ in outs], dtype=float)
    if not np.isfinite(r).all():
        st.codes = ["DATA_INVALID"]
        return st
    st.n = len(r)
    st.wins = int((r > be_r).sum()); st.losses = int((r < -be_r).sum()); st.breakeven = st.n - st.wins - st.losses
    st.p_win_posterior = (st.wins + 1) / (st.n + 2)
    st.p_win_ci = wilson(st.wins, st.n)
    st.mean_net_r = float(r.mean()); st.median_net_r = float(np.median(r)); st.mean_gross_r = float(g.mean())
    se = float(r.std(ddof=1) / math.sqrt(st.n)) if st.n > 1 else float("inf")
    st.expectancy_ci = (st.mean_net_r - 1.96 * se, st.mean_net_r + 1.96 * se) if st.n > 1 else (-9.0, 9.0)
    pos, neg = r[r > 0], r[r < 0]
    st.payoff_ratio = float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) else (float("inf") if len(pos) and not len(neg) else 0.0)
    st.profit_factor = float(pos.sum() / abs(neg.sum())) if len(neg) else (float("inf") if len(pos) else 0.0)
    eq = np.cumsum(r); st.max_drawdown_r = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    st.mae_pct_mean = float(np.mean([o.mae_pct for _, o, *_ in outs])); st.mfe_pct_mean = float(np.mean([o.mfe_pct for _, o, *_ in outs]))
    for _, o, *_ in outs:
        st.exit_reasons[o.exit_reason] = st.exit_reasons.get(o.exit_reason, 0) + 1
    for days in (30, 90, 180, 360):
        sel = [x for x in outs if now_ts - x[0] <= days * DAY_MS]
        st.windows[f"{days}d"] = {"n": len(sel), "mean_net_r": float(np.mean([o.net_r for _, o, *_ in sel])) if sel else None}
    st.windows["all"] = {"n": st.n, "mean_net_r": st.mean_net_r}
    rec = st.windows["90d"]["mean_net_r"]
    st.edge_decay = float(rec - st.mean_net_r) if rec is not None else 0.0
    st.cost_drag_r = st.mean_gross_r - st.mean_net_r
    for key_i, name in ((2, "symbol"), (3, "regime"), (4, "market")):
        bd: dict[str, list[float]] = {}
        for x in outs:
            bd.setdefault(str(x[key_i]), []).append(x[1].net_r)
        st.breakdown[name] = {k: {"n": len(v), "mean_net_r": float(np.mean(v))} for k, v in sorted(bd.items())}
    codes = []
    if st.n < min_sample:
        codes.append("INSUFFICIENT_SAMPLE")
    if st.mean_net_r <= min_expectancy:
        codes.append("NEGATIVE_EXPECTANCY")
    if st.n >= 2 and st.expectancy_ci[0] <= 0:
        codes.append("LOW_CONFIDENCE")
    if st.mean_gross_r > 0 >= st.mean_net_r:
        codes.append("COST_ERODED_EDGE")
    if rec is not None and st.windows["90d"]["n"] >= 10 and st.mean_net_r > 0 and rec < 0.5 * st.mean_net_r:
        codes.append("EDGE_DECAY")
    if query_regime is not None:
        reg = st.breakdown.get("regime", {})
        share = reg.get(query_regime, {}).get("n", 0) / st.n if st.n else 0
        if share < 0.5:
            codes.append("REGIME_MISMATCH")
    st.codes = codes
    st.ok = not codes
    return st


# ------------------------------------------------------------------ hızlı kNN (2026-10-06; docs/TOUR_CONTENTION_V2.md)
#: 1. aşamanın parça boyu (satır). Geçici diziler parça başına ~1–2 MB.
KNN_CHUNK = 8192
#: |z| bunun altındaysa float32'ye sığar ve eski koddaki kareler taşmaz (mesafe sonlu); değilse eski döngü.
KNN_ZMAX = 1e30
#: float32'ye en yakına yuvarlamanın göreli hata üst sınırı (normal aralık; alt-normal aralığın mutlak hatası
#: 2^-150, `KNN_ABS`'nin içinde).
KNN_F32_REL = 2.0 ** -24
#: Olaydan bağımsız pay: yol korelasyonunun float32 payı (0,25 · 2^-24, 2 kat güvenlikle) + bütün float64 yuvarlama
#: payları (≈ 2·10^-14; 50 kattan fazla güvenlikle). Kanıt: docs/TOUR_CONTENTION_V2.md §4.
KNN_ABS = 2.0 ** -25 + 1e-12


def _codes(values: list) -> tuple[dict, np.ndarray]:
    """Dizge → tamsayı kodu (eşit dizge = eşit kod; `==` anlamı aynen)."""
    ix: dict = {}
    arr = np.fromiter((ix.setdefault(v, len(ix)) for v in values), dtype=np.int32, count=len(values))
    return ix, arr


def _unit_rows(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Satır başına merkezlenmiş BİRİM vektör (float64) ve "iyi koşullu" bayrağı.

    İyi koşullu = sonlu, std ∈ [0,5; 2], |ortalama| ≤ 0,25·std: z-skorlu yollar (std ≈ 1). Hata sınırı (belge §4) yalnız
    bunlar için geçerlidir; diğerleri (sabit ya da normalize edilmemiş küçük yollar) yaklaşık mesafeye GÜVENİLMEZ — olaysa
    her sorguda 2. aşamaya (eski kod) konur, sorguysa sorgu bütünüyle eski döngüyle yapılır."""
    with np.errstate(all="ignore"):
        fin = np.isfinite(p).all(axis=1)
        p = np.where(fin[:, None], p, 0.0)
        m = p.mean(axis=1)
        c = p - m[:, None]
        nrm = np.sqrt((c * c).sum(axis=1))
        sd = nrm / math.sqrt(max(1, p.shape[1]))
        ok = fin & (sd >= 0.5) & (sd <= 2.0) & (np.abs(m) <= 0.25 * sd)
        u = c / np.where(ok, nrm, 1.0)[:, None]
    u[~ok] = 0.0
    return u, ok


def _ts_values(s: pd.Series) -> np.ndarray:
    """Mum zaman damgaları int64 olarak — YALNIZ KAYIPSIZ dönüşümle, `int(ts[i])` == eski `int(df["timestamp"].iloc[i])`.

    Kabul: sütunun KENDİ dtype'ı bir numpy tamsayısıdır (int*; uint* ise değerler int64'e sığar) ya da numpy kayan
    noktasıdır ve her değer sonlu, tam sayı ve |v| < 2^63'tür (int64'e dönüşüm birebir). Başka her şey TypeError (→ bu
    motorda eski döngü; kanıt aynı): pandas genişletme tipleri (ör. NA'lı `Int64` sütunu `to_numpy()` ile float64'e döner
    ve 2^53 üstünü yuvarlar — eski kodun `iloc`'u ise tam değeri verir), object, tarih, bool. Gözden geçirme bulgusu
    2026-10-06; test: tests/test_knn_fast_identity_v1.py `test_lossy_timestamp_columns_never_enter_the_fast_path`."""
    dt = s.dtype
    if not isinstance(dt, np.dtype) or dt.kind not in "iuf":
        raise TypeError(f"timestamp sütunu kayıpsız int64'e çevrilemez (dtype {dt})")
    a = s.to_numpy()
    if a.dtype != dt:
        raise TypeError(f"timestamp sütunu dönüştürüldü ({dt} → {a.dtype})")
    if dt.kind == "i":
        return a.astype(np.int64, copy=False)
    if dt.kind == "u":
        if a.size and int(a.max()) > int(np.iinfo(np.int64).max):
            raise TypeError("timestamp sütunu int64 aralığını aşıyor (uint)")
        return a.astype(np.int64)
    with np.errstate(all="ignore"):
        exact = bool(np.isfinite(a).all() and (np.floor(a) == a).all() and (np.abs(a) < 2.0 ** 63).all())
    if not exact:
        raise TypeError("timestamp sütunu kayan noktalı ve tam sayı değil / sonlu değil / int64 aralığı dışında")
    return a.astype(np.int64)


class _KnnIndex:
    """Sorgudan bağımsız olay dizileri — motor başına TEMBEL (ilk sorguda) kurulur, motorla birlikte serbest kalır.

    Yayım anı ve indeks içeriği DEĞİŞMEZ (kurulum yayımdan sonra, ilk sorguda). Standart anlık görüntüler satır satır
    motorun KENDİ `_std` fonksiyonuyla hesaplanır (eski kodun gördüğü vektörler, bit bit), sonra float32'ye yuvarlanır.
    Yön ve pencere dizileri istendikçe kurulur; HERHANGİ bir dizinin kurulumu başarısız olursa motor işaretlenir ve o
    motorda bir daha denenmez (`SimilarPatternEngine._knn_arrays`). Bellek: olay başına 100 B + sorgulanan her yön için
    9 B + her pencere için 66 B (152,5k olay, pencere 64, iki yön: ≈ 28 MB). Kilit YOK: iki iş parçacığı aynı anda
    kurarsa ikisi de aynı diziyi kurar ve atama tektir (fork'ta kilitli devralınan kilit riski yok)."""

    def __init__(self, eng: "SimilarPatternEngine"):
        ev = eng.events
        n = len(ev)
        self.key = eng._knn_key()
        self.n = n
        self.cutoff = np.fromiter((e.cutoff_ts for e in ev), dtype=np.int64, count=n)
        self.sym_ix, self.sym = _codes([e.symbol for e in ev])
        self.clu_ix, self.clu = _codes([e.cluster for e in ev])
        self.reg_ix, self.reg = _codes([e.regime for e in ev])
        dim = int(np.asarray(eng._mu).shape[0])
        self.z32 = np.empty((n, dim), dtype=np.float32)
        self.zb = np.empty(n, dtype=np.float64)
        zmax = 0.0
        for s in range(0, n, KNN_CHUNK):
            part = ev[s:s + KNN_CHUNK]
            blk = np.empty((len(part), dim), dtype=np.float64)
            for i, e in enumerate(part):
                blk[i] = eng._std(e.snap)
            if len(part):
                zmax = max(zmax, float(np.abs(blk).max()))
            self.z32[s:s + len(part)] = blk
            with np.errstate(all="ignore"):
                rms = np.sqrt((blk * blk).mean(axis=1))
            # |rms(q−z32) − rms(q−z64)| ≤ rms(z32 − z64) ≤ 2^-24·rms(z64); mesafeye etkisi bunun en çok yarısı (belge §4)
            self.zb[s:s + len(part)] = KNN_F32_REL * rms * (1.0 + 1e-6) + KNN_ABS
        self.z_ok = bool(np.isfinite(zmax) and zmax < KNN_ZMAX)
        self.sides: dict = {}
        self.windows: dict = {}

    def side(self, eng: "SimilarPatternEngine", side: str) -> tuple[np.ndarray, np.ndarray]:
        """(yön var mı, çıkış barının zaman damgası) — eski koddaki olay başına `iloc` aramasının aynısı, bir kez."""
        got = self.sides.get(side)
        if got is None:
            ev = eng.events
            has = np.fromiter((side in e.outcomes for e in ev), dtype=bool, count=self.n)
            ex = np.full(self.n, np.iinfo(np.int64).max, dtype=np.int64)
            ts_by_key: dict = {}
            for j in np.flatnonzero(has).tolist():
                e = ev[j]
                k = (e.symbol, e.market, e.tf)
                ts = ts_by_key.get(k)
                if ts is None:
                    ts = ts_by_key[k] = _ts_values(eng.candles[k]["timestamp"])
                ex[j] = int(ts[e.outcomes[side].exit_idx])
            got = self.sides[side] = (has, ex)
        return got

    def window(self, eng: "SimilarPatternEngine", w: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(pencere var mı, iyi koşullu mu, float32 birim yol)."""
        got = self.windows.get(w)
        if got is None:
            ev = eng.events
            has = np.fromiter((w in e.path for e in ev), dtype=bool, count=self.n)
            first = next((e.path[w] for e in ev if w in e.path), None)
            width = len(first) if first is not None else PATH_POINTS
            u32 = np.zeros((self.n, width), dtype=np.float32)
            ok = np.zeros(self.n, dtype=bool)
            for s in range(0, self.n, KNN_CHUNK):
                part = ev[s:s + KNN_CHUNK]
                blk = np.zeros((len(part), width), dtype=np.float64)
                for i, e in enumerate(part):
                    p = e.path.get(w)
                    if p is not None:
                        blk[i] = p
                u, okb = _unit_rows(blk)
                u32[s:s + len(part)] = u
                ok[s:s + len(part)] = okb & has[s:s + len(part)]
            got = self.windows[w] = (has, ok, u32)
        return got

    def nbytes(self) -> int:
        arrs = [self.cutoff, self.sym, self.clu, self.reg, self.z32, self.zb]
        arrs += [a for v in self.sides.values() for a in v] + [a for v in self.windows.values() for a in v]
        return int(sum(a.nbytes for a in arrs))


class _KnnMismatch(RuntimeError):
    """Hızlı yolun ön süzgeci ile eski kodun olay başına kuralı bir adayda ayrıştı (olmamalı) → eski döngü."""


class SimilarPatternEngine:
    #: Hızlı kNN sorgusu (sınıf varsayılanı AÇIK). `TradingEngineV3._build_pattern_index` config'ten
    #: (`history.evidence_fast_knn`) örneğe yazar. False → eski olay-başına döngü (geri dönüş anahtarı). KARAR GİRDİSİ
    #: DEĞİL: iki yol bit-aynı sonuç verir (tests/test_knn_fast_identity_v1.py).
    fast_query: bool = True

    def __init__(self, *, windows=WINDOWS, horizon: int = 24, stop_atr_mult: float = 2.5, tp1_r: float = 1.0, tp2_r: float = 2.0,
                 fee_pct: float = 0.05, slippage_pct: float = 0.03, min_separation: int | None = None, embargo_bars: int = 1,
                 min_sample: int = 30, clusters: dict[str, str] | None = None):
        self.windows = tuple(windows)
        self.horizon, self.stop_atr_mult, self.tp1_r, self.tp2_r = horizon, stop_atr_mult, tp1_r, tp2_r
        self.fee_pct, self.slippage_pct = fee_pct, slippage_pct
        self.min_separation = min_separation
        self.embargo_bars = embargo_bars
        self.min_sample = min_sample
        self.clusters = clusters or {}
        self.events: list[PatternEvent] = []
        self._mu: np.ndarray | None = None
        self._sd: np.ndarray | None = None
        self.frames: dict[tuple[str, str, str], pd.DataFrame] = {}      # (symbol, market, tf) → feature frame
        self.candles: dict[tuple[str, str, str], pd.DataFrame] = {}
        # hızlı kNN: tembel diziler + yalnız teşhis sayaçları (karar girdisi DEĞİL)
        self._knn_ix: _KnnIndex | None = None
        self._knn_broken = ""                 # herhangi bir dizinin kurulumu bir kez başarısız → bu motorda eski döngü
        self._knn_pid = os.getpid()           # uyarı yalnız kuran süreçte (fork edilen kanıt alt süreci LOGLAMAZ)
        self._knn_warned = False
        self.knn_stats = {"fast": 0, "legacy": 0, "fallback": 0, "exact": 0, "error": ""}

    # ------------------------------------------------------------ indeks
    def add_series(self, symbol: str, market: str, tf: str, df: pd.DataFrame, *, btc_df=None, funding_df=None, stride: int = 1,
                   funding_pct_per_bar: float = 0.0) -> int:
        feats = build_feature_frame(df, tf, btc_df=btc_df, funding_df=funding_df)
        self.frames[(symbol, market, tf)] = feats
        self.candles[(symbol, market, tf)] = df.sort_values("timestamp").reset_index(drop=True)
        d = self.candles[(symbol, market, tf)]
        o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        atr_pct = feats["atr_pct"].to_numpy(dtype=float)
        n0 = len(self.events)
        wmax = max(self.windows)
        for i in range(wmax, len(d) - 1, max(1, stride)):
            paths = {}
            ok = True
            for w in self.windows:
                pv = _path_vec(c, i, w)
                if pv is None:
                    ok = False
                    break
                paths[w] = pv
            if not ok or not np.isfinite(atr_pct[i]) or atr_pct[i] <= 0:
                continue
            row = feats.iloc[i]
            snap = np.array([float(row.get(k_, np.nan)) for k_ in SNAP_COLS], dtype=float)
            outs = {}
            entry_ref = float(o[i + 1]); atr_abs = atr_pct[i] / 100 * c[i]
            for side in (("LONG", "SHORT") if market == "futures" else ("LONG",)):
                stp, t1, t2 = barriers_from_atr(entry_ref, atr_abs, side, stop_mult=self.stop_atr_mult, tp1_r=self.tp1_r, tp2_r=self.tp2_r)
                oc = triple_barrier(o, h, l, c, i, side, stop=stp, tp1=t1, tp2=t2, horizon=self.horizon, market=market, fee_pct=self.fee_pct,
                                    slippage_pct=self.slippage_pct, funding_pct_per_bar=funding_pct_per_bar)
                if oc is not None:
                    outs[side] = oc
            if not outs:
                continue
            self.events.append(PatternEvent(symbol, market, tf, i, int(row["event_ts"]), int(row["cutoff_ts"]), regime_code(row),
                                            self.clusters.get(symbol, "default"), paths, snap, outs))
        self._refit_scaler()
        return len(self.events) - n0

    def _refit_scaler(self) -> None:
        if not self.events:
            return
        m = np.array([e.snap for e in self.events], dtype=float)
        self._mu = np.nanmean(m, axis=0)
        sd = np.nanstd(m, axis=0)
        self._sd = np.where(sd > 1e-12, sd, 1.0)

    def _std(self, snap: np.ndarray) -> np.ndarray:
        z = (snap - self._mu) / self._sd
        return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)

    # ------------------------------------------------------------ sorgu
    def query_vector(self, symbol: str, market: str, tf: str, idx: int | None = None) -> tuple[dict[int, np.ndarray], np.ndarray, pd.Series] | None:
        key = (symbol, market, tf)
        if key not in self.frames:
            return None
        feats, d = self.frames[key], self.candles[key]
        i = len(d) - 1 if idx is None else idx
        c = d["close"].to_numpy(dtype=float)
        paths = {}
        for w in self.windows:
            pv = _path_vec(c, i, w)
            if pv is None:
                return None
            paths[w] = pv
        row = feats.iloc[i]
        return paths, np.array([float(row.get(k_, np.nan)) for k_ in SNAP_COLS], dtype=float), row

    def query(self, symbol: str, market: str, tf: str, side: str, *, query_ts: int | None = None, idx: int | None = None, k: int = 60,
              level: str = "auto", window: int = 64, now_ts: int | None = None) -> dict:
        """level: 'same_coin' | 'cluster' | 'universe' | 'auto' (aynı coin yeterliyse onu, değilse genişlet). Dönen: evidence dict."""
        qv = self.query_vector(symbol, market, tf, idx)
        if qv is None or self._mu is None:
            return {"ok": False, "codes": ["DATA_INVALID"], "n": 0}
        paths, snap, row = qv
        d = self.candles[(symbol, market, tf)]
        i = len(d) - 1 if idx is None else idx
        qts = int(query_ts if query_ts is not None else self.frames[(symbol, market, tf)]["cutoff_ts"].iloc[i])
        step = int(d["timestamp"].iloc[1] - d["timestamp"].iloc[0]) if len(d) > 1 else 0
        min_sep = self.min_separation or max(4, window // 4)
        q_regime = regime_code(row)
        cluster = self.clusters.get(symbol, "default")
        qz = self._std(snap)
        qp = paths[window]
        chosen = None
        if getattr(self, "fast_query", True):
            chosen = self._query_fast(symbol, side, qts, step, level, window, k, min_sep, q_regime, cluster, qz, qp)
        else:
            self._knn_count("legacy")
        if chosen is None:                                            # eski olay-başına döngü (geri dönüş / yedek)
            cands = []
            for e in self.events:
                if window not in e.path or side not in e.outcomes:
                    continue
                # geleceğe bakış YOK: komşunun sonucu (exit) da sorgu anından önce bitmiş olmalı
                exit_ts = self.candles[(e.symbol, e.market, e.tf)]["timestamp"].iloc[e.outcomes[side].exit_idx]
                if e.cutoff_ts >= qts - self.embargo_bars * step or int(exit_ts) >= qts:
                    continue
                lvl = "same_coin" if e.symbol == symbol else ("cluster" if e.cluster == cluster else "universe")
                if level == "same_coin" and lvl != "same_coin":
                    continue
                if level == "cluster" and lvl == "universe":
                    continue
                if level in ("universe", "auto") and lvl == "universe" and e.regime != q_regime:
                    continue                                          # evren seviyesi: yalnız aynı rejim
                dist = self._pair_dist(qz, qp, e, window)
                cands.append((dist, lvl, e))
            cands.sort(key=lambda x: (x[0], x[2].symbol, x[2].event_ts))
            chosen = self._select(cands, level, k, min_sep)[0]
        outs = [(e.cutoff_ts, e.outcomes[side], e.symbol, e.regime, e.market) for _, _, e in chosen]
        st = compute_stats(outs, now_ts=int(now_ts if now_ts is not None else qts), min_sample=self.min_sample, query_regime=q_regime)
        levels = {}
        for _, lvl, _e in chosen:
            levels[lvl] = levels.get(lvl, 0) + 1
        return {"ok": st.ok, "codes": st.codes, "n": st.n, "stats": st.to_dict(), "query": {"symbol": symbol, "market": market, "tf": tf, "side": side,
                "query_ts": qts, "regime": q_regime, "window": window, "level": level}, "levels": levels,
                "neighbors": [{"symbol": e.symbol, "event_ts": e.event_ts, "regime": e.regime, "distance": round(dist, 4), "level": lvl,
                               "net_r": round(e.outcomes[side].net_r, 3), "exit": e.outcomes[side].exit_reason, "bars_held": e.outcomes[side].bars_held}
                              for dist, lvl, e in chosen[:20]]}

    # ------------------------------------------------------------ eski döngünün iki parçası (her iki yol AYNI kodu çağırır)
    def _pair_dist(self, qz: np.ndarray, qp: np.ndarray, e: PatternEvent, window: int) -> float:
        """Sorgu–olay mesafesi: eski döngünün satırları AYNEN. Hızlı yolun 2. aşaması da yalnız bunu çağırır."""
        ez = self._std(e.snap)
        d_snap = float(np.sqrt(np.mean((qz - ez) ** 2)))
        corr = float(np.corrcoef(qp, e.path[window])[0, 1]) if e.path[window].std() > 0 else 0.0
        d_path = 1.0 - (corr if np.isfinite(corr) else 0.0)
        return 0.5 * d_snap / (1 + d_snap) + 0.5 * d_path / 2.0

    def _select(self, cands: list, level: str, k: int, min_sep: int) -> tuple[list[tuple[float, str, PatternEvent]], int | None]:
        """Sıralı adaylardan seçim: eski döngünün satırları AYNEN. Dönen: (seçilenler, döngüyü `k` ile bitiren adayın
        sırası — aday listesi tükendiyse None). Seçim yalnız adayların SIRALI ÖNEKİNE bakar (hızlı yolun kanıtı, belge §4)."""
        chosen: list[tuple[float, str, PatternEvent]] = []
        used: dict[str, list[int]] = {}
        seen: set[tuple[str, int]] = set()
        for pos, (dist, lvl, e) in enumerate(cands):
            if level == "auto" and lvl != "same_coin" and len([c_ for c_ in chosen if c_[1] == "same_coin"]) >= self.min_sample:
                continue                                              # auto: aynı coin yeterliyse genişletme
            key = (e.symbol, e.event_ts)
            if key in seen:
                continue
            if any(abs(e.idx - u) < min_sep for u in used.get(e.symbol, [])):
                continue                                              # overlap purge / temporal separation
            chosen.append((dist, lvl, e)); seen.add(key); used.setdefault(e.symbol, []).append(e.idx)
            if len(chosen) >= k:
                return chosen, pos
        return chosen, None

    # ------------------------------------------------------------ hızlı kNN (2026-10-06; docs/TOUR_CONTENTION_V2.md)
    def _knn_key(self) -> tuple:
        """Dizilerin geçerlilik anahtarı: olay listesi (nesne + uzunluk + son olay), ölçekleyici ve mum tabloları."""
        ev = self.events
        return (ev, len(ev), ev[-1] if ev else None, self._mu, self._sd, tuple(self.candles.values()))

    def _knn_fresh(self, ix: _KnnIndex) -> bool:
        a, b = ix.key, self._knn_key()
        return a[1] == b[1] and len(a[5]) == len(b[5]) and all(x is y for x, y in zip(a[:1] + a[2:5], b[:1] + b[2:5])) \
            and all(x is y for x, y in zip(a[5], b[5]))

    def _knn_index(self) -> _KnnIndex:
        ix = getattr(self, "_knn_ix", None)
        if ix is None or not self._knn_fresh(ix):
            self._knn_ix = None                                       # eskisi yenisi kurulmadan bırakılsın (bellek)
            ix = _KnnIndex(self)
            self._knn_ix = ix
        return ix

    def knn_index_nbytes(self) -> int:
        """Hızlı yolun motora eklediği kalıcı dizilerin baytı (kurulmadıysa 0). Yalnız ölçüm."""
        ix = getattr(self, "_knn_ix", None)
        return 0 if ix is None else ix.nbytes()

    def _knn_count(self, what: str, n: int = 1) -> None:
        st = self.__dict__.get("knn_stats")
        if st is None:
            st = self.__dict__["knn_stats"] = {"fast": 0, "legacy": 0, "fallback": 0, "exact": 0, "error": ""}
        st[what] = st.get(what, 0) + n

    def _knn_warn(self, msg: str, exc: BaseException) -> None:
        """Yedeğe düşüş uyarısı: motor başına bir kez, yalnız motoru kuran süreçte (alt süreç loglamaz)."""
        text = f"{type(exc).__name__}: {exc}"[:300]
        st = self.__dict__.get("knn_stats")
        if st is not None:
            st["error"] = text
        if getattr(self, "_knn_warned", False) or os.getpid() != getattr(self, "_knn_pid", None):
            return
        self._knn_warned = True
        try:
            log.warning("pattern kNN hızlı yolu %s; eski döngü kullanılıyor (kanıt aynı, yalnız yavaş): %s", msg, text)
        except Exception:  # noqa: BLE001 — uyarı sorguyu asla bozmaz
            pass

    def _knn_arrays(self, side: str, window: int) -> _KnnIndex:
        """Sorgunun ihtiyaç duyduğu BÜTÜN diziler (motor başına ortak kısım + yön + pencere). Herhangi biri kurulamazsa
        istisna yukarı çıkar ve çağıran motoru işaretler: bozuk bir olay/sütun her sorguda yeniden kurulum + düşüş
        maliyeti ödetmez (gözden geçirme bulgusu 2026-10-06)."""
        ix = self._knn_index()
        ix.side(self, side)
        ix.window(self, window)
        return ix

    def _query_fast(self, symbol: str, side: str, qts: int, step: int, level: str, window: int, k: int, min_sep: int,
                    q_regime: str, cluster: str, qz: np.ndarray, qp: np.ndarray) -> list | None:
        """Hızlı yol: seçilenler ya da None (→ eski döngü; sonuç yine aynı). Hiçbir istisna dışarı sızmaz: arızada eski
        döngü bugünkü sonucu (ya da bugünkü istisnayı) üretir."""
        if getattr(self, "_knn_broken", ""):
            self._knn_count("fallback")
            return None
        try:
            ix = self._knn_arrays(side, window)
        except Exception as exc:  # noqa: BLE001 — dizi kurulamadı (bellek, kayıplı zaman damgası, beklenmeyen veri):
            self._knn_ix = None   # bu motorda bir daha DENENMEZ, eski döngü
            self._knn_broken = f"{type(exc).__name__}: {exc}"[:300]
            self._knn_count("fallback")
            self._knn_warn("kurulamadı", exc)
            return None
        try:
            chosen = self._knn_select(ix, symbol, side, qts, step, level, window, k, min_sep, q_regime, cluster, qz, qp)
        except Exception as exc:  # noqa: BLE001 — bu sorgu eski döngüyle (aynı sonuç)
            self._knn_count("fallback")
            self._knn_warn("bir sorguda başarısız", exc)
            return None
        self._knn_count("fast" if chosen is not None else "fallback")
        return chosen

    def _knn_select(self, ix: _KnnIndex, symbol: str, side: str, qts: int, step: int, level: str, window: int, k: int,
                    min_sep: int, q_regime: str, cluster: str, qz: np.ndarray, qp: np.ndarray) -> list | None:
        """Kesin seçim: eski seçim döngüsü (`_select`) adayları eski TAM sıralamanın sırasıyla alır, ama mesafe yalnız
        gerektiği kadar adayda (eski kodla) hesaplanır. None → eski döngü (sorgu bu yolun koşullarını sağlamıyor)."""
        got = self._knn_candidates(ix, symbol, side, qts, step, level, window, q_regime, cluster, qz, qp)
        if got is None:
            return None
        cand, lo = got
        return self._select(self._knn_ordered(cand, lo, symbol, side, qts, step, level, window, q_regime, cluster, qz, qp),
                            level, k, min_sep)[0]

    def _knn_candidates(self, ix: _KnnIndex, symbol: str, side: str, qts: int, step: int, level: str, window: int,
                        q_regime: str, cluster: str, qz: np.ndarray, qp: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        """1. aşama. Aday kümesi C = eski döngünün süzgecinden geçen olaylar (aynı kurallar, vektörel; olay sırasıyla) ve
        her aday için eski kodun mesafesinin KANITLI ALT SINIRI lo(c) ≤ d(c) (belge §4). İyi koşullu olmayan olaylar için
        lo = −∞ (her zaman eski kodla hesaplanır). None: sorgu bu yolun koşullarını sağlamıyor → eski döngü."""
        if not ix.z_ok:
            return None
        qz64 = np.asarray(qz, dtype=np.float64)                       # yalnız 1. aşama; eski kod `qz`'yi aynen alır
        if not (np.isfinite(qz64).all() and float(np.abs(qz64).max(initial=0.0)) < KNN_ZMAX):
            return None
        qu, qok = _unit_rows(np.asarray(qp, dtype=np.float64)[None, :])
        if not bool(qok[0]):
            return None                                               # sorgu yolu iyi koşullu değil → eski döngü
        q = qu[0]
        w_has, w_ok, u32 = ix.window(self, window)
        if u32.shape[1] != q.shape[0] or ix.z32.shape[1] != qz64.shape[0]:
            return None
        s_has, s_exit = ix.side(self, side)
        m = w_has & s_has & (ix.cutoff < qts - self.embargo_bars * step) & (s_exit < qts)
        same = ix.sym == ix.sym_ix.get(symbol, -1)
        if level == "same_coin":
            m &= same
        elif level == "cluster":
            m &= same | (ix.clu == ix.clu_ix.get(cluster, -1))
        elif level in ("universe", "auto"):
            m &= same | (ix.clu == ix.clu_ix.get(cluster, -1)) | (ix.reg == ix.reg_ix.get(q_regime, -1))
        cand = np.flatnonzero(m)
        lo = np.empty(cand.size, dtype=np.float64)
        dim = float(ix.z32.shape[1])
        for s in range(0, cand.size, KNN_CHUNK):
            j = cand[s:s + KNN_CHUNK]
            dz = ix.z32[j] - qz64                                     # float32 − float64 → float64
            sa = np.sqrt((dz * dz).sum(axis=1) / dim)
            ca = (u32[j] * q).sum(axis=1)                             # birim yolların iç çarpımı (BLAS'sız)
            lo[s:s + j.size] = 0.5 * sa / (1.0 + sa) + 0.25 * (1.0 - ca) - ix.zb[j]
        bad = ~w_ok[cand]
        if not np.isfinite(lo[~bad]).all():
            return None
        lo[bad] = -np.inf
        return cand, lo

    def _knn_ordered(self, cand: np.ndarray, lo: np.ndarray, symbol: str, side: str, qts: int, step: int, level: str,
                     window: int, q_regime: str, cluster: str, qz: np.ndarray, qp: np.ndarray):
        """2. aşama: adayları eski döngünün TAM sıralamasıyla AYNI sırada üretir — (d, sembol, olay_ts, olay sırası);
        olay sırası eski `sort`un kararlılığıdır. d = `_pair_dist` (eski kod). Adaylar lo sırasıyla hesaplanıp bir
        yığına konur; yığının en küçüğü x ancak d(x) < lo(sıradaki hesaplanmamış) iken verilir: hesaplanmamış her c için
        d(c) ≥ lo(c) ≥ o lo > d(x), yani x kalanların en küçüğüdür (tümevarımla dizi tam sıralamanın kendisi). Tüketici
        (`_select`) `k`'da durunca hesap da durur."""
        order = np.argsort(lo, kind="stable")
        los = lo[order]                                               # Python nesnesine yalnız tüketilen kadarı çevrilir
        js = cand[order]
        n = int(js.size)
        events = self.events
        thr = qts - self.embargo_bars * step
        heap: list = []
        p = 0
        try:
            while True:
                while p < n and (not heap or heap[0][0] >= float(los[p])):
                    jj = int(js[p])
                    p += 1
                    e = events[jj]
                    lvl = "same_coin" if e.symbol == symbol else ("cluster" if e.cluster == cluster else "universe")
                    if (window not in e.path or side not in e.outcomes or e.cutoff_ts >= thr
                            or (level == "same_coin" and lvl != "same_coin") or (level == "cluster" and lvl == "universe")
                            or (level in ("universe", "auto") and lvl == "universe" and e.regime != q_regime)):
                        raise _KnnMismatch(f"ön süzgeç eski kuralla ayrıştı: {e.symbol} {e.event_ts}")
                    dist = self._pair_dist(qz, qp, e, window)
                    if not dist == dist:                              # NaN sıralanamaz (korumalar altında olamaz)
                        raise _KnnMismatch(f"mesafe NaN: {e.symbol} {e.event_ts}")
                    heapq.heappush(heap, (dist, e.symbol, e.event_ts, jj, lvl, e))
                if not heap:
                    return
                dist, _sym, _ts, _jj, lvl, e = heapq.heappop(heap)
                yield dist, lvl, e
        finally:
            self._knn_count("exact", p)

__all__ = ["SimilarPatternEngine", "PatternEvent", "EvidenceStats", "compute_stats", "wilson", "regime_code", "WINDOWS", "SNAP_COLS"]

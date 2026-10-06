"""P2 — işlem başına fiyat yolu yeniden kurma (`pathrec`; §5.1) ve tembel 1m ihtiyaç listesi (§3.3 "1m (yol için)").

Kaynak sırası (§5.1): araştırma deposunun **1m** barları, yoksa **5m** (`StoreProvider`'ın okuduğu mühürlü depo;
`provider.SealedReader` ile, mühürden sonra değişen parça `DATA_MOVING`); ana bot için ayrıca `position_path.jsonl`
(gözlenmiş mark'lar); hiçbiri yoksa yalnız defterin kendi uçları (`mfe_pct`/`mae_pct`, `EXTREMES_ONLY`). Etiket her
zaman `RECONSTRUCTED`'dır (kaynak `path_source`'ta).

Okumalar ve kurallar (belgenin açık bıraktığı yerler, P2 uygulama notlarında da):

1. **Yol penceresi** `learning_cf._net_replay`'in girişi (`ts > opened`) ile AYNIDIR: işlem barları = açılışı
   `opened_at`'tan SONRA ve `closed_at`'tan ÖNCE olan barlar (`opened < ts < closed`). Girişi içeren bar yolun parçası
   değildir (girişten önceki fiyatları taşır; "bar provenansı"); çıkışı içeren bar yoldadır; çıkış tam bar sınırındaysa
   (`closed = ts`) o bar çıkıştan SONRAKİ fiyatları taşır ve kuyruğun ilk barıdır. Geriye tarihli kapanışta (`closed_at_backdated`)
   pencere kaydedilen `closed_at`'e göredir (§5.1). Üstüne çıkıştan sonraki **48 bar** kuyruk eklenir (`phase=1`;
   STOPPED_THEN_REVERSED için, P2b).
2. **Bar içi sıra** `learning_cf` kuralıdır: açılış → ters uç → lehte uç → kapanış (`INTRABAR_ORDER`). MFE ve MAE'nin
   aynı barda oluştuğu işlemde sıra GÖZLENMEZ: `order` bu kuralla `MAE_FIRST` yazılır ama `order_ambiguous=True` olur;
   ayrıca her iki koşan ucun da aynı barda yenilendiği barlar (ilk işlem barı hariç) `ambiguous_bars`'ta sayılır. P2b'nin
   sıraya dayanan kodları bu işlemleri dışlar.
3. **Kaynak seçimi:** 1m işlem penceresinde eksiksizse 1m; değilse 5m eksiksizse 5m; ikisi de eksikse barı daha çok olan
   (boşluk sayısı `gaps`'te); hiç bar yoksa ana botta `POSITION_PATH`, sonra `EXTREMES_ONLY`. Bar beklenmeyen kısa
   işlem (pencerede hiç bar yok) `EXTREMES_ONLY`'dir.
4. **R ölçüsü** kaydın R paydasıdır: birim risk = `features.risk_usdt / quantity` (= |giriş − ilk stop|); payda yoksa
   (eski kayıt, özelliksiz spot) R alanları `None`, yüzde alanları yine yazılır.
5. **Satırın mührü:** yolun okuduğu ay parçaları `(seri, ay, parça sha256)` listesi olarak döner (`parts`); günlük
   bunları işlemin kendi `data_seal`'ına katar (aynı `ResearchStore.seal_of`).

Tembel 1m (§3.3): gece birimi AĞSIZDIR; 1m'si eksik işlemlerin gün listesi `paths/needs_1m.json`'a yazılır
(`plan_needs`/`write_needs`) ve ertesi gece veri birimi (`datastore`, yalnız arşiv gün zip'leri, REST YOK) yalnız bu
günleri çeker (`lazy1m`). Kapsam: son 120 günde kapanmış işlemler, son 400 gün.

Bu modül `datastore`'u import ETMEZ (ağ yok); yazımlar yalnız `data/research/paths` altındadır.
"""
from __future__ import annotations

import hashlib
import io
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .lazy1m import NEEDS_SCHEMA, read_needs
from .ledgers import dec_or_none, iso, parse_ts
from .paths import EnginePaths
from .provider import DataMoving, raw_symbol
from .store import SRC_COL, SRC_UNVERIFIED, ResearchStore, cols_for, month_bounds_ym

UTC = timezone.utc
PATH_SCHEMA = "path_v1"
TAIL_BARS = 48
INTRABAR_ORDER = "open>adverse>favourable>close"
TF_STEP = {"1m": 60_000, "5m": 300_000}
SRC_POSITION_PATH, SRC_1M, SRC_5M, SRC_EXTREMES, SRC_MISSING = (
    "POSITION_PATH", "STORE_1M", "STORE_5M", "EXTREMES_ONLY", "MISSING")
#: tembel 1m kapsamı (§3.3 tablosu "1m (yol için)")
LAZY_TRADE_DAYS = 120
LAZY_HISTORY_DAYS = 400
#: gece başına istenen en çok gün (veri birimi ayrıca kendi sınırını uygular)
LAZY_MAX_DAYS = 400
DAY_MS = 86_400_000
PATH_COLS = ["timestamp", "open", "high", "low", "close", "phase"]


# ============================================================================ mühürlü bar okuyucu (önbellekli)
class BarSource:
    """Araştırma deposundan ay parçası ay parçası okuyan, parça önbellekli görünüm. `reader`: `provider.SealedReader`
    (gece birimi: yalnız mühürdeki parçalar, sha256 denetimli) ya da `ResearchStore` (test/araç; manifestin parça
    sha'sı). Okunan her parçanın `(seri, ay, sha)` üçlüsü döner; `DATA_MOVING` seri bu çalıştırmada atlanır.
    `StoreProvider` ile aynı parite kapısı: `archive_unverified` satırlar (`.CHECKSUM`'sız zip) varsayılan olarak
    VERİLMEZ (`include_unverified=False`; o günün yolu eksik sayılır, doğrulama gelince satır yeniden kurulur)."""

    def __init__(self, reader: Any, *, max_parts: int = 96, include_unverified: bool = False) -> None:
        self.reader = reader
        self.max_parts = int(max_parts)
        self.include_unverified = bool(include_unverified)
        self._cache: OrderedDict[tuple[str, str, str, str], pd.DataFrame] = OrderedDict()
        self.moving: dict[str, str] = {}
        self.reads = 0

    @property
    def seal(self) -> str | None:
        return getattr(self.reader, "seal", None)

    def _store(self) -> ResearchStore:
        return self.reader.store if hasattr(self.reader, "listing") else self.reader

    def _listing(self, market: str, symbol: str, tf: str) -> dict[str, str]:
        key = ResearchStore.series_key(market, symbol, tf)
        if hasattr(self.reader, "listing"):
            return dict(self.reader.listing.get(key) or {})
        m = self.reader.manifest(market, symbol, tf)
        return dict(m.part_sha or {})

    def _part(self, market: str, symbol: str, tf: str, ym: str) -> pd.DataFrame | None:
        ck = (market, symbol, tf, ym)
        if ck in self._cache:
            self._cache.move_to_end(ck)
            return self._cache[ck]
        key = ResearchStore.series_key(market, symbol, tf)
        if key in self.moving:
            return None
        a, b = month_bounds_ym(ym)
        try:
            frames = [df for _ym, df in self.reader.iter_parts(market, symbol, tf, a, b - 1, keep_src=True) if _ym == ym]
        except DataMoving as exc:
            self.moving[key] = f"{exc.ym}: {exc.reason}"
            return None
        self.reads += 1
        cols = cols_for(tf)
        df = frames[0] if frames else pd.DataFrame(columns=cols)
        if not self.include_unverified and SRC_COL in df.columns:
            df = df[df[SRC_COL] != SRC_UNVERIFIED]
        df = df[[c for c in cols if c in df.columns]].sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        self._cache[ck] = df
        while len(self._cache) > self.max_parts:
            self._cache.popitem(last=False)
        return df

    def frame(self, market: str, symbol: str, tf: str, since_ms: int | None, until_ms: int | None
              ) -> tuple[pd.DataFrame, list[tuple[str, str, str]], str | None]:
        """[since, until] (açılış ms, iki uç dahil) aralığındaki barlar, okunan parçalar ve durum (None | DATA_MOVING)."""
        sym = raw_symbol(symbol)
        listing = self._listing(market, sym, tf)
        key = ResearchStore.series_key(market, sym, tf)
        used: list[tuple[str, str, str]] = []
        frames: list[pd.DataFrame] = []
        for ym in sorted(listing):
            a, b = month_bounds_ym(ym)
            if (until_ms is not None and a > until_ms) or (since_ms is not None and b <= since_ms):
                continue
            df = self._part(market, sym, tf, ym)
            if df is None:
                return pd.DataFrame(columns=cols_for(tf)), [], "DATA_MOVING"
            used.append((key, ym, listing[ym]))
            if len(df):
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=cols_for(tf)), used, None
        out = pd.concat(frames, ignore_index=True)
        if since_ms is not None:
            out = out[out["timestamp"] >= int(since_ms)]
        if until_ms is not None:
            out = out[out["timestamp"] <= int(until_ms)]
        out = out.drop_duplicates("timestamp", keep="last").sort_values("timestamp", kind="mergesort")
        return out.reset_index(drop=True), used, None

    def last_closed(self, market: str, symbol: str, tf: str, until_open_ms: int, n: int, *, lookback_ms: int
                    ) -> tuple[pd.DataFrame, list[tuple[str, str, str]], str | None]:
        """Açılışı `until_open_ms` ve öncesi olan son `n` bar (geriye `lookback_ms` kadar bakılır)."""
        df, used, st = self.frame(market, symbol, tf, int(until_open_ms) - int(lookback_ms), int(until_open_ms))
        return (df.tail(int(n)).reset_index(drop=True) if len(df) else df), used, st


def store_has_series(src: BarSource | None, market: str, symbol: str, tf: str) -> bool:
    return src is not None and bool(src._listing(market, raw_symbol(symbol), tf))


# ============================================================================ sonuç
@dataclass
class PathResult:
    trade_key: str
    symbol: str
    market: str
    source: str
    tf: str | None
    bars: pd.DataFrame | None
    n_trade_bars: int = 0
    n_tail_bars: int = 0
    expected_trade_bars: int = 0
    gaps: int = 0
    parts: list[tuple[str, str, str]] = field(default_factory=list)
    status: str = "OK"
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.source in (SRC_1M, SRC_5M) and self.gaps == 0

    def summary(self) -> dict[str, Any]:
        """Günlük satırına giren küçük özet (barlar `paths/` dosyasındadır)."""
        return {"schema": PATH_SCHEMA, "path_source": self.source, "tf": self.tf, "n_trade_bars": self.n_trade_bars,
                "n_tail_bars": self.n_tail_bars, "expected_trade_bars": self.expected_trade_bars, "gaps": self.gaps,
                "complete": self.complete, "status": self.status, "intrabar_order": INTRABAR_ORDER,
                "notes": list(self.notes), **self.metrics}


def _q(x: Any) -> float | None:
    if x is None:
        return None
    v = float(x)
    return round(v, 6) + 0.0


def path_metrics(bars: pd.DataFrame | None, *, side: str, entry: Decimal, rpu: Decimal | None, opened_ms: int,
                 exit_price: Decimal | None, step_ms: int) -> dict[str, Any]:
    """İşlem barlarından (yalnız `phase == 0`) MFE/MAE ve türevleri. Fiyat hareketleri girişten (dolum VWAP'ı), R'ler
    birim riske (`rpu`) bölünür; zamanlar dakika: açılıştan, ucun oluştuğu barın KAPANIŞINA kadar."""
    long = str(side).upper() in ("LONG", "BUY")
    sgn = Decimal(1) if long else Decimal(-1)
    out: dict[str, Any] = {"mfe_r": None, "mae_r": None, "mfe_pct": None, "mae_pct": None, "t_mfe": None, "t_mae": None,
                           "order": None, "order_ambiguous": None, "time_to_1r": None, "giveback_r": None,
                           "capture_ratio": None, "exit_r_gross": None, "ambiguous_bars": 0}
    tb = bars[bars["phase"] == 0] if bars is not None and len(bars) else None
    if tb is None or not len(tb) or entry is None or entry <= 0:
        return out
    hi, lo, ts = tb["high"].tolist(), tb["low"].tolist(), tb["timestamp"].tolist()
    best = worst = Decimal(0)
    t_best = t_worst = None
    i_best = i_worst = None
    first_1r = None
    amb = 0
    for i, (h, low_, t) in enumerate(zip(hi, lo, ts)):
        fav = Decimal(repr(float(h if long else low_)))
        adv = Decimal(repr(float(low_ if long else h)))
        fm, am = sgn * (fav - entry), sgn * (adv - entry)
        new_b, new_w = fm > best, am < worst
        if new_b:
            best, t_best, i_best = fm, int(t), i
        if new_w:
            worst, t_worst, i_worst = am, int(t), i
        if new_b and new_w and i > 0:
            amb += 1
        if first_1r is None and rpu is not None and rpu > 0 and fm >= rpu:
            first_1r = int(t)

    def _mins(t: int | None) -> float | None:
        return None if t is None else round((t + step_ms - opened_ms) / 60_000.0, 3)
    out["mfe_pct"] = _q(best / entry * 100)
    out["mae_pct"] = _q(worst / entry * 100)
    out["t_mfe"], out["t_mae"] = _mins(t_best), _mins(t_worst)
    out["ambiguous_bars"] = int(amb)
    if i_best is None and i_worst is None:
        out["order"], out["order_ambiguous"] = None, False
    elif i_worst is None or (i_best is not None and i_best < i_worst):
        out["order"], out["order_ambiguous"] = "MFE_FIRST", False
    elif i_best is None or i_worst < i_best:
        out["order"], out["order_ambiguous"] = "MAE_FIRST", False
    else:                                                  # aynı bar: kural sırası (ters uç önce), sıra GÖZLENMEDİ
        out["order"], out["order_ambiguous"] = "MAE_FIRST", True
    out["time_to_1r"] = _mins(first_1r)
    if rpu is not None and rpu > 0:
        mfe_r, mae_r = best / rpu, worst / rpu
        out["mfe_r"], out["mae_r"] = _q(mfe_r), _q(mae_r)
        if exit_price is not None:
            xr = sgn * (exit_price - entry) / rpu
            out["exit_r_gross"] = _q(xr)
            out["giveback_r"] = _q(mfe_r - xr) if mfe_r > 0 else 0.0
            out["capture_ratio"] = _q(xr / mfe_r) if mfe_r > 0 else None
    return out


def _bars_view(df: pd.DataFrame) -> pd.DataFrame:
    return df[["timestamp", "open", "high", "low", "close"]].astype({"timestamp": "int64", "open": "float64", "high": "float64",
                                                                      "low": "float64", "close": "float64"})


def _expected(opened_ms: int, closed_ms: int, step: int) -> int:
    """`opened < ts < closed` aralığındaki bar açılışı sayısı (ızgara)."""
    first = (opened_ms // step + 1) * step
    last = ((closed_ms - 1) // step) * step
    return max(0, (last - first) // step + 1) if last >= first else 0


def _try_tf(src: BarSource, market: str, symbol: str, tf: str, opened_ms: int, closed_ms: int, tail: int
            ) -> tuple[pd.DataFrame | None, int, int, int, list, str | None]:
    step = TF_STEP[tf]
    df, used, st = src.frame(market, symbol, tf, opened_ms + 1, closed_ms + tail * step)
    if st is not None:
        return None, 0, 0, 0, [], st
    if not len(df):
        return None, 0, 0, _expected(opened_ms, closed_ms, step), used, None
    df = _bars_view(df)
    trade = df[(df["timestamp"] > opened_ms) & (df["timestamp"] < closed_ms)]
    tail_df = df[df["timestamp"] >= closed_ms].head(tail)
    exp = _expected(opened_ms, closed_ms, step)
    out = pd.concat([trade.assign(phase=0), tail_df.assign(phase=1)], ignore_index=True)
    out["phase"] = out["phase"].astype("int8")
    return out[PATH_COLS], len(trade), len(tail_df), exp, used, None


def _position_path_bars(state: Path, trade_id: str, symbol: str, opened_ms: int, closed_ms: int) -> pd.DataFrame | None:
    """Ana bot `position_path.jsonl` gözlemleri (yalnız bu işlem; salt-okunur) → fiyat-yalnız "barlar" (o=h=l=c=mark)."""
    p = state / "position_path.jsonl"
    if not trade_id or not p.exists():
        return None
    pts: list[tuple[int, float]] = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                if trade_id not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if str(row.get("trade_id") or "") != trade_id:
                    continue
                t = row.get("ts_ms")
                m = row.get("mark")
                if not isinstance(t, int) or not isinstance(m, (int, float)):
                    continue
                if opened_ms < t <= closed_ms:
                    pts.append((t, float(m)))
    except OSError:
        return None
    if not pts:
        return None
    pts = sorted(set(pts))
    df = pd.DataFrame({"timestamp": [t for t, _ in pts], "open": [m for _, m in pts], "high": [m for _, m in pts],
                       "low": [m for _, m in pts], "close": [m for _, m in pts]})
    df["phase"] = 0
    df["phase"] = df["phase"].astype("int8")
    return df[PATH_COLS]


def reconstruct(rec: dict, *, trade_key: str, src: BarSource | None, market: str, risk_usdt: Decimal | None,
                state: Path | None = None, use_position_path: bool = False, tail: int = TAIL_BARS) -> PathResult:
    """Tek kapanmış kaydın yolu (§5.1). `rec`: ledger `TradeRecord` sözlüğü (arşivdeki birebir kopya)."""
    symbol = str(rec.get("symbol") or "")
    side = str(rec.get("side") or "LONG")
    o, c = parse_ts(rec.get("opened_at")), parse_ts(rec.get("closed_at"))
    entry = dec_or_none(rec.get("entry"))
    qty = dec_or_none(rec.get("quantity"))
    rpu = (risk_usdt / qty) if (risk_usdt is not None and risk_usdt > 0 and qty) else None
    exit_px = dec_or_none(rec.get("exit_price"))
    res = PathResult(trade_key=trade_key, symbol=raw_symbol(symbol), market=market, source=SRC_MISSING, tf=None, bars=None)
    if o is None or c is None or entry is None:
        res.status = "RECORD_UNREADABLE"
        return res
    om, cm = int(o.timestamp() * 1000), int(c.timestamp() * 1000)
    cands = []
    if src is not None:
        for tf in ("1m", "5m"):
            bars, nt, nl, exp, used, st = _try_tf(src, market, symbol, tf, om, cm, tail)
            if st is not None:
                res.notes.append(f"{tf}:{st}")
                continue
            cands.append((tf, bars, nt, nl, exp, used))
    chosen = None
    for tf, bars, nt, nl, exp, used in cands:
        if exp == 0:
            continue
        if bars is not None and nt >= exp:
            chosen = (tf, bars, nt, nl, exp, used)
            break
    if chosen is None:
        part = [x for x in cands if x[1] is not None and x[2] > 0 and x[4] > 0]
        if part:
            chosen = max(part, key=lambda x: (x[2] / x[4], -TF_STEP[x[0]]))
    if chosen is not None:
        tf, bars, nt, nl, exp, used = chosen
        res.source, res.tf, res.bars = (SRC_1M if tf == "1m" else SRC_5M), tf, bars
        res.n_trade_bars, res.n_tail_bars, res.expected_trade_bars = nt, nl, exp
        res.gaps = max(0, exp - nt)
        res.parts = list(used)
        res.metrics = path_metrics(bars, side=side, entry=entry, rpu=rpu, opened_ms=om, exit_price=exit_px, step_ms=TF_STEP[tf])
        if res.gaps:
            res.status = "PATH_GAP"
        return res
    # parça listesi (bar bulunamasa da hangi parçalara bakıldı) mühüre girer
    res.parts = sorted({p for x in cands for p in x[5]})
    if all(x[4] == 0 for x in cands) and cands:
        res.notes.append("NO_BAR_IN_WINDOW")
    if use_position_path and state is not None:
        pp = _position_path_bars(state, str(rec.get("id") or ""), symbol, om, cm)
        if pp is not None and len(pp):
            res.source, res.tf, res.bars = SRC_POSITION_PATH, None, pp
            res.n_trade_bars = len(pp)
            res.metrics = path_metrics(pp, side=side, entry=entry, rpu=rpu, opened_ms=om, exit_price=exit_px, step_ms=0)
            res.status = "OK"
            return res
    # yalnız defterin uçları (sıra yok)
    mfe, mae = dec_or_none(rec.get("mfe_pct")), dec_or_none(rec.get("mae_pct"))
    if mfe is not None or mae is not None:
        res.source = SRC_EXTREMES
        res.status = "EXTREMES_ONLY"
        m = {k: None for k in path_metrics(None, side=side, entry=entry, rpu=None, opened_ms=om, exit_price=None, step_ms=0)}
        m.update({"mfe_pct": _q(mfe) if mfe is not None else None, "mae_pct": _q(mae) if mae is not None else None,
                  "ambiguous_bars": 0})
        if rpu is not None and rpu > 0:
            m["mfe_r"] = _q(mfe / 100 * entry / rpu) if mfe is not None else None
            m["mae_r"] = _q(mae / 100 * entry / rpu) if mae is not None else None
            if exit_px is not None:
                sgn = Decimal(1) if side.upper() in ("LONG", "BUY") else Decimal(-1)
                m["exit_r_gross"] = _q(sgn * (exit_px - entry) / rpu)
        res.metrics = m
        return res
    res.status = "NO_PATH"
    return res


# ============================================================================ dosyalar
def path_file(paths: EnginePaths, trade_key: str, closed_at: Any) -> Path:
    t = parse_ts(closed_at)
    month = t.strftime("%Y-%m") if t is not None else "unknown"
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in trade_key)[:150]
    h = hashlib.sha256(trade_key.encode("utf-8")).hexdigest()[:10]
    return paths.paths_root / month / f"{safe}~{h}.parquet"


def path_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df[PATH_COLS].reset_index(drop=True).to_parquet(buf, index=False, compression="snappy")
    return buf.getvalue()


def write_path(paths: EnginePaths, res: PathResult, closed_at: Any) -> str | None:
    """Yol barlarını `paths/YYYY-MM/<anahtar>.parquet`'e yaz (içerik aynıysa dosyaya dokunulmaz). Dönen: göreli yol."""
    if res.bars is None or not len(res.bars):
        return None
    p = path_file(paths, res.trade_key, closed_at)
    data = path_bytes(res.bars)
    try:
        if p.exists() and p.read_bytes() == data:
            return str(p.relative_to(paths.research))
    except OSError:
        pass
    paths.write_bytes(p, data)
    return str(p.relative_to(paths.research))


def read_path(paths: EnginePaths, rel: str) -> pd.DataFrame | None:
    """Günlük satırındaki `path_file`'dan barlar (P2b'nin ızgara/ayrıştırma girdisi)."""
    p = paths.research / rel
    if not p.exists():
        return None
    return pd.read_parquet(p)


# ============================================================================ tembel 1m ihtiyacı
def trade_days(opened_ms: int, closed_ms: int, *, tail: int = TAIL_BARS) -> list[str]:
    """İşlemin 1m yolu için gereken UTC günleri: [açılış dakikası, kapanış + kuyruk] (gün zip'i birimi)."""
    a = (int(opened_ms) // 60_000) * 60_000
    b = int(closed_ms) + int(tail) * 60_000
    d = a - a % DAY_MS
    out = []
    while d <= b:
        out.append(datetime.fromtimestamp(d / 1000, tz=UTC).strftime("%Y-%m-%d"))
        d += DAY_MS
    return out


def plan_needs(items: Iterable[dict[str, Any]], *, now: datetime, seal: str | None = None,
               max_days: int = LAZY_MAX_DAYS) -> dict[str, Any]:
    """1m'si eksik işlemlerden gün listesi (deterministik). `items`: {market, symbol, opened_ms, closed_ms}. Kapsam:
    son `LAZY_TRADE_DAYS` günde kapanmış işlemler, son `LAZY_HISTORY_DAYS` gün; bugünün (henüz zip'i yayımlanmamış) günü
    dahil edilmez. Sınır aşılırsa en YENİ günler önce alınır ve kalan sayılır (`capped`)."""
    now_ms = int(now.timestamp() * 1000)
    today = datetime.fromtimestamp(now_ms // DAY_MS * DAY_MS / 1000, tz=UTC).strftime("%Y-%m-%d")
    floor = datetime.fromtimestamp((now_ms - LAZY_HISTORY_DAYS * DAY_MS) // DAY_MS * DAY_MS / 1000, tz=UTC).strftime("%Y-%m-%d")
    trade_from = now_ms - LAZY_TRADE_DAYS * DAY_MS
    want: dict[str, set[str]] = {}
    n_trades = 0
    for it in items:
        if int(it["closed_ms"]) < trade_from:
            continue
        key = f"{it['market']}/{raw_symbol(it['symbol'])}/1m"
        days = [d for d in trade_days(int(it["opened_ms"]), int(it["closed_ms"])) if floor <= d < today]
        if days:
            n_trades += 1
            want.setdefault(key, set()).update(days)
    flat = sorted(((d, k) for k, ds in want.items() for d in ds), key=lambda x: (x[0], x[1]), reverse=True)
    keep = flat[:max(0, int(max_days))]
    series: dict[str, list[str]] = {}
    for d, k in keep:
        series.setdefault(k, []).append(d)
    return {"schema": NEEDS_SCHEMA, "generated_at": iso(now), "data_seal": seal, "trade_days": LAZY_TRADE_DAYS,
            "history_days": LAZY_HISTORY_DAYS, "trades": n_trades,
            "series": {k: sorted(v) for k, v in sorted(series.items())}, "days_total": len(keep),
            "capped": max(0, len(flat) - len(keep))}


def write_needs(paths: EnginePaths, doc: dict[str, Any]) -> Path:
    paths.write_json(paths.needs_1m, doc)
    return paths.needs_1m


def bar_close_at(src: BarSource | None, market: str, symbol: str, t_ms: int, *,
                 tfs: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")) -> tuple[Decimal | None, str | None, list]:
    """`t_ms` anındaki son fiyat: kapanışı TAM `t_ms` olan barın kapanışı (`LAST_PRICE_PROXY`; 1m → 1d sırasıyla ilk
    bulunan dilim). Bir barın kapanışı o andaki son işlem fiyatıdır; bütün dilimlerde aynı ana denk gelir."""
    if src is None:
        return None, None, []
    steps = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": DAY_MS}
    used_all: list = []
    for tf in tfs:
        st = steps[tf]
        if t_ms % st:
            continue
        df, used, s = src.frame(market, symbol, tf, t_ms - st, t_ms - st)
        used_all += used
        if s is None and len(df):
            px = dec_or_none(str(float(df["close"].iloc[-1])))
            if px is not None and px > 0:
                return px, tf, used_all
    return None, None, used_all


__all__ = ["BarSource", "INTRABAR_ORDER", "LAZY_HISTORY_DAYS", "LAZY_MAX_DAYS", "LAZY_TRADE_DAYS", "NEEDS_SCHEMA", "PATH_COLS",
           "PATH_SCHEMA", "PathResult", "SRC_1M", "SRC_5M", "SRC_EXTREMES", "SRC_MISSING", "SRC_POSITION_PATH", "TAIL_BARS",
           "bar_close_at", "path_bytes", "path_file", "path_metrics", "plan_needs", "read_needs", "read_path", "reconstruct",
           "store_has_series", "trade_days", "write_needs", "write_path"]

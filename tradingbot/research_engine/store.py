"""`ResearchStore` — araştırma veri deposu (§3.2; P1b). `history.store.HistoryStore`'un ALT SINIFI; worker'ın deposu
(`data/market/history`) ve sınıfı HİÇ değişmez: bu modül HistoryStore'u yamamaz, yalnız alt sınıfta yöntemleri ezer.

Kök `data/research/store/<market>/<SYM>/<tf>/<YYYY>/<MM>.parquet` (+ `<MM>.parquet.sha256` yan dosyası,
`manifest.json`). `market` ∈ {futures, spot, dukascopy}; `SYM` ham semboldür (`BTCUSDT`, `XAUUSD`).

Kurallar (belge §3.2; her biri testli):

1. **Ay-parçası checksum'ı.** `HistoryStore._recompute` her yazımda bütün seriyi yeniden okur (5m/1m doldurmada
   O(n²)). Burada yalnız DOKUNULAN ayın parçası okunur ve yazılır; parça baytlarının sha256'sı yan dosyaya ve manifeste
   (`part_sha`) yazılır; satır / ilk / son / boşluk / kaynak sayıları parça başına `part_meta`'da tutulur ve seri özeti
   bunlardan (parça okumadan) kurulur.
2. **`data_seal`** = sıralı `(seri, ay, parça sha256)` satırlarının sha256'sı; yalnız manifestlerden hesaplanır (parça
   okunmaz; okuma sayacı testi). Tam kanonik checksum yalnız `deep_validate` ile (haftalık 1/7 örneklem ve istendiğinde)
   hesaplanır ve manifestin `checksum` alanına `checksum_at` ile yazılır (parça parça akışla; bütün seri belleğe
   alınmaz).
3. **Satır kaynağı ve öncelik.** Her satır `_src` ∈ {archive, seed, archive_unverified, rest} taşır; öncelik
   archive > seed > archive_unverified > rest. Düşük öncelikli satır yüksek önceliklinin yerine ASLA geçmez (`blocked`);
   eşit öncelikte son gelen kazanır (HistoryStore gibi). Bir satır başkasının yerine geçerken ya da engellenirken
   değerler karşılaştırılır; farklıysa fark kaydı çıkar (`drain_diffs`; `datastore` bunları
   `runs/data/<run_id>/diffs.jsonl.gz`'a yazar; beklenen 0). Doğrulanmış bir arşiv dosyası kendi dönemini YETKİLİ olarak
   kapsar (`authoritative`): o dönemde arşivde OLMAYAN `rest` / `archive_unverified` satırları kaldırılır ve fark olarak
   kaydedilir (REST ile arşiv zaman damgasının hizalanmadığı bir seride çift satır kalmasın diye).
4. **Kurtarma ile gerçek bozulma ayrıdır.** Yazım sırası: `.inflight.json` işareti → parça (`os.replace`) → parça
   `.sha256` → manifest → işaret silinir. `check_series` her parça için önce ucuz `stat` (boyut, mtime_ns)
   karşılaştırır; işaret, uyuşmazlık ya da manifestte olmayan parça varsa o parçayı derin denetler:
   - parça okunuyor ve kendi `.sha256`'sıyla tutuyor ama manifest geride → `MANIFEST_LAG`: manifest parçadan yeniden
     kurulur, ay arşivden yeniden çekilip karşılaştırılmak üzere `refetch`e girer, seri devam eder;
   - parça okunamıyor / yan dosyası yok / tutmuyor → `CORRUPT`: parça `store/_quarantine/` altına taşınır (silinmez),
     ay manifestten ve arşiv dosya defterinden (`files`) çıkarılır ve `refetch`e girer. Seri yalnız AYNI ay iki ARDIŞIK
     gece yeniden çekilemezse durur (`HALTED`, fail-closed); başarılı yeniden çekim durmayı kaldırır.
5. **Fonlama aralığı veriden.** `step_ms_for("funding")` 8 saati sabit kodlar; burada ay başına ardışık uzlaşma zaman
   damgası farklarının (dakikaya yuvarlanmış) medyanı parçanın `interval_ms`'i olarak kaydedilir. **Boşluklar
   (2026-10-06 inceleme düzeltmesi) YEREL aralıkla sayılır:** her fark, kendisi dahil ±3 komşu farkın medyanına
   bölünür (`round(fark / yerel aralık) − 1`; Binance `calc_time`'ı ±ms oynar, taban bölme bir eksik uzlaşmayı
   kaçırırdı). Aralık ay içinde değişirse (ör. 13'üne kadar 8h, sonra 4h) ayın tek medyanı sahte boşluk sayardı.
   Parça başına farkların sıkıştırılmış dizisi (`diffs`, [fark, adet] koşuları) `part_meta`'da tutulur; seri boşluğu
   parça OKUNMADAN bütün seri üzerinde (ay sınırları dahil) bu dizilerden hesaplanır. Manifestin `interval_ms`'i serinin
   EN SON yerel aralığıdır.
6. **Seri kilidi.** Her yazım seri klasöründeki `.lock` üzerinde fcntl kilidi alır (`data.lock`'un içinde ek güvence).
7. **Yeni türler.** `metrics_5m` (`METRICS_COLS`), `duka_1h` / `duka_1m` (`DUKA_COLS`), vadeli mark ve premium endeks
   1h mumları (`markpx_1h`, `premium_1h`; `PX_COLS`). Kline türleri HistoryStore'un geniş şemasını (`KLINE_COLS`),
   `funding` onun `FUNDING_COLS`'unu kullanır (tohum aynı sütunlarla kopyalanır).
8. **Yalnız kapanmış satır.** Bar türlerinde `açılış + adım ≤ şimdi`, nokta türlerinde (`funding`, `metrics_5m`)
   `zaman ≤ şimdi`; `is_closed` sütunu varsa `False` satırlar da düşer. ≥ 10¹⁴ zaman damgası (µs) depoya YAZILAMAZ.
9. **µs→ms** (`normalize_archive_ts`): Binance SPOT arşivi 2025-01-01'den itibaren µs yazar. Birim değerin
   büyüklüğünden DEĞİL arşiv yolundan (spot) ve dosyanın döneminden belirlenir; sonuç dönemin içinde değilse dosya
   bozuk sayılır (yanlış birim sessizce kabul edilmez).

Bütün yazımlar `guard` (üretimde `EnginePaths.require_research`) denetiminden geçer: depo araştırma kökü dışına
yazamaz. Manifest yan etkisiz okunur (`core.read_json` bozuk dosyayı yeniden adlandırırdı; burada kullanılmaz).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import tempfile
import bisect
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import pandas as pd

from ..history.store import FUNDING_COLS, HAS_PARQUET, KLINE_COLS, HistoryStore, Manifest
from .lock import EngineLock, LockBusy

UTC = timezone.utc
STORE_SCHEMA = "research_store_v1"

# ---- satır kaynağı ve öncelik (madde 3)
SRC_COL = "_src"
SRC_ARCHIVE, SRC_SEED, SRC_UNVERIFIED, SRC_REST = "archive", "seed", "archive_unverified", "rest"
SOURCES: tuple[str, ...] = (SRC_ARCHIVE, SRC_SEED, SRC_UNVERIFIED, SRC_REST)
PRIORITY: dict[str, int] = {SRC_ARCHIVE: 3, SRC_SEED: 2, SRC_UNVERIFIED: 1, SRC_REST: 0}

# ---- türler (madde 7)
MARKETS: tuple[str, ...] = ("futures", "spot", "dukascopy")
TF_MS: dict[str, int] = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000,
                         "1d": 86_400_000}
FUNDING = "funding"
METRICS_5M = "metrics_5m"
MARKPX_1H, PREMIUM_1H = "markpx_1h", "premium_1h"
DUKA_1H, DUKA_1M = "duka_1h", "duka_1m"
METRICS_COLS = ["timestamp", "oi", "oi_usdt", "top_acct_ls", "top_pos_ls", "global_ls", "taker_ls_vol"]
DUKA_COLS = ["timestamp", "open", "high", "low", "close", "volume_proxy"]
PX_COLS = ["timestamp", "open", "high", "low", "close"]
POINT_KINDS = frozenset({FUNDING, METRICS_5M})
KINDS: tuple[str, ...] = (*TF_MS, FUNDING, METRICS_5M, MARKPX_1H, PREMIUM_1H, DUKA_1H, DUKA_1M)

#: ≥ bu değer ms değil µs'dir (2025 µs ≈ 1,7·10¹⁵; ms ≈ 1,7·10¹²).
TS_US_GUARD = 10 ** 14
#: Binance spot arşivinin µs'ye geçtiği an (2025-01-01T00:00:00Z).
SPOT_US_FROM_MS = 1_735_689_600_000

# ---- kurtarma durumları (madde 4)
ST_OK, ST_MANIFEST_LAG, ST_CORRUPT, ST_MISSING = "OK", "MANIFEST_LAG", "CORRUPT", "MISSING"
SERIES_OK, SERIES_HALTED = "OK", "HALTED"
QUARANTINE_DIR = "_quarantine"
INFLIGHT_FILE = ".inflight.json"
LOCK_FILE = ".lock"
DIFF_CAP = 5000
_CODEC: list[str] = []


def parquet_codec() -> str:
    """zstd (gerçek arşiv verisinde snappy'den ~%25 küçük; çıktı deterministik); parquet motorunda yoksa snappy.
    İlk kullanımda pandas üzerinden bir kez denenir (paket pyarrow'u doğrudan import etmez)."""
    if not _CODEC:
        try:
            pd.DataFrame({"x": [1]}).to_parquet(io.BytesIO(), index=False, compression="zstd")
            _CODEC.append("zstd")
        except Exception:  # noqa: BLE001 — codec yoksa varsayılan
            _CODEC.append("snappy")
    return _CODEC[0]


#: nokta türlerinde REST ↔ arşiv zaman damgası oynaması (Binance `calc_time` ±ms): aynı değerli ikiz "fark" sayılmaz
TOLERANCE_MS: dict[str, int] = {FUNDING: 60_000, METRICS_5M: 60_000}
#: fark karşılaştırmasına giren sütunlar (varsayılan: bütün değer sütunları). Fonlamada `mark` yalnız REST'te vardır
#: (arşivin `fundingRate` dosyası taşımaz); bu yüzden yalnız `rate` karşılaştırılır.
COMPARE_COLS: dict[str, list[str]] = {FUNDING: ["rate"]}


class ArchiveUnitError(ValueError):
    """Arşiv zaman damgası yolun ve dönemin gerektirdiği birimde değil (dosya bozuk sayılır)."""


# ============================================================================ tür ve zaman yardımcıları
def cols_for(kind: str) -> list[str]:
    if kind == FUNDING:
        return list(FUNDING_COLS)
    if kind == METRICS_5M:
        return list(METRICS_COLS)
    if kind in (DUKA_1H, DUKA_1M):
        return list(DUKA_COLS)
    if kind in (MARKPX_1H, PREMIUM_1H):
        return list(PX_COLS)
    if kind in TF_MS:
        return list(KLINE_COLS)
    raise ValueError(f"bilinmeyen tür: {kind}")


def step_ms(kind: str) -> int | None:
    """Sabit adımlı türlerde ms adımı; `funding` için None (aralık veriden, madde 5)."""
    if kind == FUNDING:
        return None
    if kind in TF_MS:
        return TF_MS[kind]
    if "_" in kind:
        return TF_MS.get(kind.rsplit("_", 1)[1])
    return None


def ym_of_ms(ts_ms: int) -> str:
    d = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
    return f"{d.year:04d}/{d.month:02d}"


def month_bounds_ym(ym: str) -> tuple[int, int]:
    y, m = (int(x) for x in ym.split("/"))
    a = datetime(y, m, 1, tzinfo=UTC)
    b = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=UTC)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def iso_ms(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).isoformat()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def archive_ts_divisor(market: str, period_start_ms: int) -> int:
    """Madde 9: spot arşivi ve dönem 2025-01-01 ya da sonrası → µs (÷1000); aksi ms (÷1)."""
    return 1000 if market == "spot" and int(period_start_ms) >= SPOT_US_FROM_MS else 1


def normalize_archive_ts(market: str, period_start_ms: int, period_end_ms: int, open_ts: list[int],
                         close_ts: list[int] | None = None, *, tolerance_ms: int = 0) -> tuple[list[int], list[int] | None]:
    """Arşiv dosyasının açılış (ve kapanış) zaman damgalarını ms'ye çevir. Birim yoldan ve dönemden gelir; çevrilmiş
    açılışlar `[period_start − tolerans, period_end + tolerans)` içinde değilse `ArchiveUnitError` (dosya bozuk sayılır;
    tolerans yalnız uzlaşma `calc_time`'ının ±ms oynaması içindir)."""
    div = archive_ts_divisor(market, period_start_ms)
    ot = [int(v) // div for v in open_ts]
    ct = [int(v) // div for v in close_ts] if close_ts is not None else None
    tol = int(tolerance_ms)
    if ot and (min(ot) < int(period_start_ms) - tol or max(ot) >= int(period_end_ms) + tol):
        unit = "µs" if div == 1000 else "ms"
        raise ArchiveUnitError(f"{market} arşivi {iso_ms(period_start_ms)} dönemi için {unit} beklenir; zaman damgaları "
                               f"dönem dışında ({min(ot)}..{max(ot)})")
    return ot, ct


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json_plain(p: Path) -> Any:
    """Yan etkisiz JSON okuma: yoksa/bozuksa None (dosya yeniden adlandırılmaz)."""
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _json_safe(x: Any) -> Any:
    if isinstance(x, float) and math.isnan(x):
        return None
    if hasattr(x, "item"):
        try:
            return _json_safe(x.item())
        except (TypeError, ValueError):
            return str(x)
    return x


# ============================================================================ manifest
@dataclass
class ResearchManifest(Manifest):
    """HistoryStore manifestinin araştırma uzantısı (temel alanlar aynı anlamdadır; `checksum` yalnız
    `deep_validate` anında hesaplanan tam kanonik özettir, `checksum_at` o andır)."""

    store_schema: str = STORE_SCHEMA
    part_sha: dict[str, str] = field(default_factory=dict)           # "YYYY/MM" → parça baytlarının sha256'sı
    part_meta: dict[str, dict] = field(default_factory=dict)         # "YYYY/MM" → rows/first/last/gaps/src/size/...
    files: dict[str, dict] = field(default_factory=dict)             # arşiv dosya defteri: damga → {st, sha, at}
    series_seal: str = ""
    status: str = SERIES_OK
    halted_reason: str | None = None
    refetch: list[str] = field(default_factory=list)
    recovery: dict[str, dict] = field(default_factory=dict)
    interval_ms: int | None = None                                   # funding: son ayın veriden aralığı
    src_rows: dict[str, int] = field(default_factory=dict)
    checksum_at: str | None = None


# ============================================================================ depo
class ResearchStore(HistoryStore):
    """Araştırma deposu. `guard(path)` her yazım hedefini denetler (üretimde `EnginePaths.require_research`);
    `clock_ms()` "yalnız kapanmış satır" kuralının saatidir (testler sabitler)."""

    def __init__(self, root: Path | str, *, provider: str = "binance", clock_ms: Callable[[], int] | None = None,
                 guard: Callable[[Path], Path] | None = None) -> None:
        super().__init__(root, provider=provider)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._guard = guard or (lambda p: Path(p))
        #: okuma sayaçları (madde 2 testi: mühür hesabı parça OKUMAZ)
        self.part_reads = 0
        self.byte_reads = 0
        self._diffs: list[dict] = []
        self.diff_count = 0
        self.diff_cap = DIFF_CAP

    @classmethod
    def for_paths(cls, paths: Any, **kw: Any) -> "ResearchStore":
        return cls(paths.store, guard=paths.require_research, **kw)

    # ------------------------------------------------------------ yollar
    @staticmethod
    def series_key(market: str, symbol: str, timeframe: str) -> str:
        return f"{market}/{symbol}/{timeframe}"

    def sidecar_path(self, part: Path) -> Path:
        return part.with_name(part.name + ".sha256")

    def inflight_path(self, market: str, symbol: str, timeframe: str) -> Path:
        return self.series_dir(market, symbol, timeframe) / INFLIGHT_FILE

    def quarantine_root(self) -> Path:
        return self.root / QUARANTINE_DIR

    def _disk_parts(self, market: str, symbol: str, timeframe: str) -> dict[str, Path]:
        d = self.series_dir(market, symbol, timeframe)
        out: dict[str, Path] = {}
        if not d.is_dir():
            return out
        for p in sorted(d.glob("*/*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            if not (p.name.endswith(".parquet") or p.name.endswith(".csv.gz")):
                continue
            try:
                y, mo = int(p.parent.name), int(p.name.split(".")[0])
            except ValueError:
                continue
            out[f"{y:04d}/{mo:02d}"] = p
        return out

    def _manifest_files(self) -> Iterator[tuple[str, str, str, Path]]:
        for market in MARKETS:
            md = self.root / market
            if not md.is_dir():
                continue
            for sd in sorted(x for x in md.iterdir() if x.is_dir()):
                for td in sorted(x for x in sd.iterdir() if x.is_dir()):
                    mp = td / "manifest.json"
                    if mp.is_file():
                        yield market, sd.name, td.name, mp

    def series(self) -> list[tuple[str, str, str]]:
        return [(m, s, t) for m, s, t, _ in self._manifest_files()]

    # ------------------------------------------------------------ manifest
    def _load_manifest(self, market: str, symbol: str, timeframe: str) -> tuple[ResearchManifest, bool]:
        """(manifest, okunabildi mi). Dosya yoksa okunabilir sayılır (boş seri)."""
        p = self.manifest_path(market, symbol, timeframe)
        d = _read_json_plain(p)
        if isinstance(d, dict):
            m = ResearchManifest.from_dict(d)
            ok = True
        else:
            m = ResearchManifest(provider=self.provider, market=market, symbol=symbol, timeframe=timeframe)
            ok = not p.exists()
        m.market, m.symbol, m.timeframe = market, symbol, timeframe
        return m, ok

    def manifest(self, market: str, symbol: str, timeframe: str) -> ResearchManifest:  # type: ignore[override]
        return self._load_manifest(market, symbol, timeframe)[0]

    def _write_atomic(self, path: Path, data: bytes) -> None:
        p = self._guard(Path(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _save_manifest(self, m: Manifest) -> None:  # type: ignore[override]
        doc = m.to_dict()
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=_json_safe).encode("utf-8")
        self._write_atomic(self.manifest_path(m.market, m.symbol, m.timeframe), raw + b"\n")

    @contextmanager
    def series_lock(self, market: str, symbol: str, timeframe: str) -> Iterator[None]:
        """Seri başına fcntl kilidi (madde 6). Tutuluyorsa `LockBusy` (bekleme yok)."""
        p = self._guard(self.series_dir(market, symbol, timeframe) / LOCK_FILE)
        lk = EngineLock(p)
        if not lk.try_acquire():
            raise LockBusy(f"seri kilidi tutuluyor: {p}")
        try:
            yield
        finally:
            lk.release()

    # ------------------------------------------------------------ parça G/Ç
    def _parse_part(self, data: bytes, name: str) -> pd.DataFrame:
        if name.endswith(".parquet"):
            return pd.read_parquet(io.BytesIO(data))
        with gzip.open(io.BytesIO(data), "rt", encoding="utf-8") as fh:
            return pd.read_csv(fh)

    def _shape(self, df: pd.DataFrame, cols: list[str], keep_src: bool) -> pd.DataFrame:
        for c in cols:
            if c not in df.columns:
                df[c] = float("nan")
        df["timestamp"] = df["timestamp"].astype("int64")
        if keep_src:
            if SRC_COL not in df.columns:
                df[SRC_COL] = SRC_REST                         # kaynağı bilinmeyen satır en düşük öncelikte
            return df[cols + [SRC_COL]]
        return df[cols]

    def _read_part(self, p: Path, cols: list[str], keep_src: bool = False) -> pd.DataFrame:  # type: ignore[override]
        if not p.exists():
            return pd.DataFrame(columns=cols + ([SRC_COL] if keep_src else []))
        with open(p, "rb") as fh:
            data = fh.read()
        self.part_reads += 1
        return self._shape(self._parse_part(data, p.name), cols, keep_src)

    def _encode_part(self, df: pd.DataFrame, name: str) -> bytes:
        buf = io.BytesIO()
        if name.endswith(".parquet"):
            df.to_parquet(buf, index=False, compression=parquet_codec())
            return buf.getvalue()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(df.to_csv(index=False, lineterminator="\n").encode("utf-8"))
        return buf.getvalue()

    def _write_part(self, p: Path, df: pd.DataFrame) -> str:  # type: ignore[override]
        """Parça → yan dosya (madde 4 sırası). Dönen: parça baytlarının sha256'sı."""
        data = self._encode_part(df, p.name)
        sha = _sha(data)
        self._write_atomic(p, data)
        self._write_atomic(self.sidecar_path(p), f"{sha}  {p.name}\n".encode("ascii"))
        return sha

    # ------------------------------------------------------------ parça özeti
    @staticmethod
    def _funding_interval(ts: list[int]) -> int | None:
        diffs = sorted(round((b - a) / 60_000) * 60_000 for a, b in zip(ts, ts[1:]) if b > a)
        diffs = [d for d in diffs if d > 0]
        if not diffs:
            return None
        n = len(diffs)
        med = diffs[n // 2] if n % 2 else (diffs[n // 2 - 1] + diffs[n // 2]) // 2
        return int(med)

    @staticmethod
    def _gaps(ts: list[int], step: int | None, interval: int | None) -> int:
        if len(ts) < 2:
            return 0
        if step:
            return int(sum(max(0, (b - a) // step - 1) for a, b in zip(ts, ts[1:])))
        if interval:
            return funding_gaps(_minute_diffs(ts))[0]
        return 0

    def _part_meta(self, df: pd.DataFrame, kind: str, sha: str, p: Path) -> dict[str, Any]:
        ts = [int(x) for x in df["timestamp"].tolist()]
        src = {k: int(v) for k, v in df[SRC_COL].value_counts().items()} if SRC_COL in df.columns else {}
        interval = self._funding_interval(ts) if kind == FUNDING else None
        st = p.stat()
        out = {"rows": len(ts), "first": ts[0] if ts else None, "last": ts[-1] if ts else None,
               "gaps": self._gaps(ts, step_ms(kind), interval), "interval_ms": interval, "src": src, "sha": sha,
               "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}
        if kind == FUNDING:
            out["diffs"] = _rle(_minute_diffs(ts))
        return out

    def _recompute(self, m: Manifest) -> None:  # type: ignore[override]
        """Seri özetini `part_meta`'dan kur (parça OKUNMAZ; madde 1–2)."""
        assert isinstance(m, ResearchManifest)
        keys = sorted(k for k, pm in m.part_meta.items() if pm.get("rows"))
        rows = sum(int(m.part_meta[k]["rows"]) for k in keys)
        step = step_ms(m.timeframe)
        gaps, prev_last, interval = 0, None, None
        src: dict[str, int] = {}
        fdiffs: list[int] = []                   # fonlama: bütün serinin dakikaya yuvarlanmış farkları (ay sınırı dahil)
        rle_ok = True
        for k in keys:
            pm = m.part_meta[k]
            gaps += int(pm.get("gaps") or 0)
            interval = pm.get("interval_ms") or interval
            if prev_last is not None and pm.get("first") is not None:
                d = int(pm["first"]) - int(prev_last)
                if step:
                    gaps += max(0, d // step - 1)
                elif m.timeframe == FUNDING:
                    fdiffs += _minute_diffs([int(prev_last), int(pm["first"])])
                elif interval:
                    gaps += max(0, round(d / interval) - 1)
            if m.timeframe == FUNDING:
                if isinstance(pm.get("diffs"), list):
                    fdiffs += _unrle(pm["diffs"])
                else:
                    rle_ok = False
            prev_last = pm.get("last")
            for s, n in (pm.get("src") or {}).items():
                src[s] = src.get(s, 0) + int(n)
        if m.timeframe == FUNDING and rle_ok:
            # madde 5: yerel aralıkla, bütün seri üzerinde (ay içi aralık değişimi ve ay sınırı sahte boşluk üretmez)
            gaps, last_iv = funding_gaps(fdiffs)
            interval = last_iv or interval
        m.row_count = rows
        m.first_ts_ms = int(m.part_meta[keys[0]]["first"]) if keys else None
        m.last_ts_ms = int(m.part_meta[keys[-1]]["last"]) if keys else None
        m.gap_count = int(gaps)
        m.parts = {k: int(m.part_meta[k]["rows"]) for k in keys}
        m.src_rows = src
        m.interval_ms = interval if m.timeframe == FUNDING else None
        expected = rows + gaps
        completeness = rows / expected if expected else 1.0
        m.quality_score = round(max(0.0, min(1.0, completeness - 0.1 * len(m.bad_chunks))), 4)
        lines = "".join(f"{k}|{m.part_sha[k]}\n" for k in sorted(m.part_sha))
        m.series_seal = _sha(lines.encode("ascii"))

    # ------------------------------------------------------------ fark kaydı
    def _diff(self, rec: dict) -> None:
        self.diff_count += 1
        if len(self._diffs) < self.diff_cap:
            self._diffs.append(rec)

    def drain_diffs(self) -> tuple[list[dict], int]:
        """(kayıtlar, toplam sayı) ve sıfırla. Kayıt sayısı `diff_cap` ile sınırlıdır; toplam her zaman doğrudur."""
        out, n = self._diffs, self.diff_count
        self._diffs, self.diff_count = [], 0
        return out, n

    @staticmethod
    def _differ(ov: pd.DataFrame, nv: pd.DataFrame) -> tuple[list[bool], dict[str, list[bool]]]:
        per: dict[str, list[bool]] = {}
        anyd = [False] * len(ov)
        for c in ov.columns:
            a = pd.to_numeric(ov[c], errors="coerce").to_numpy(dtype="float64", na_value=float("nan"))
            b = pd.to_numeric(nv[c], errors="coerce").to_numpy(dtype="float64", na_value=float("nan"))
            col = [not ((x == y) or (math.isnan(x) and math.isnan(y))) for x, y in zip(a.tolist(), b.tolist())]
            per[c] = col
            anyd = [p or q for p, q in zip(anyd, col)]
        return anyd, per

    def _merge(self, key: str, ym: str, old: pd.DataFrame | None, new: pd.DataFrame, cols: list[str],
               authoritative: list[tuple[int, int]] | None, src: str, tolerance_ms: int = 0,
               compare: list[str] | None = None) -> tuple[pd.DataFrame, dict[str, int], bool]:
        st = {"new": 0, "upgraded": 0, "changed": 0, "blocked": 0, "blocked_diff": 0, "removed": 0, "realigned": 0}
        if old is None or not len(old):
            st["new"] = len(new)
            return new.reset_index(drop=True), st, len(new) > 0
        vals = [c for c in cols if c != "timestamp"]
        cmp_cols = [c for c in (compare or vals) if c in vals]
        o = old.set_index("timestamp", drop=False)
        n = new.set_index("timestamp", drop=False)
        common = o.index.intersection(n.index)
        only_new = n.index.difference(o.index)
        st["new"] = len(only_new)
        replace_idx: list[int] = []
        if len(common):
            po = [PRIORITY.get(str(s), -1) for s in o.loc[common, SRC_COL].tolist()]
            pn = [PRIORITY.get(str(s), -1) for s in n.loc[common, SRC_COL].tolist()]
            differ, per = self._differ(o.loc[common, cmp_cols], n.loc[common, cmp_cols])
            os_, ns_ = o.loc[common, SRC_COL].tolist(), n.loc[common, SRC_COL].tolist()
            for i, ts in enumerate(common.tolist()):
                take = pn[i] >= po[i]
                if take and (pn[i] > po[i] or differ[i]):
                    replace_idx.append(ts)
                    if pn[i] > po[i]:
                        st["upgraded"] += 1
                    if differ[i]:
                        st["changed"] += 1
                elif not take:
                    st["blocked"] += 1
                    if differ[i]:
                        st["blocked_diff"] += 1
                if differ[i]:
                    dc = {c: [_json_safe(o.at[ts, c]), _json_safe(n.at[ts, c])] for c in cmp_cols if per[c][i]}
                    self._diff({"series": key, "ym": ym, "ts": int(ts), "at": iso_ms(int(ts)),
                                "action": "replaced" if take else "blocked", "old_src": os_[i], "new_src": ns_[i],
                                "cols": dc})
        removed: list[int] = []
        if authoritative and src == SRC_ARCHIVE:
            inr = pd.Series(False, index=o.index)
            for a, b in authoritative:
                inr = inr | ((o["timestamp"] >= a) & (o["timestamp"] < b))
            new_ts = sorted(int(t) for t in n.index.tolist())
            weak = o[SRC_COL].isin([SRC_REST, SRC_UNVERIFIED]) & inr
            cand = [int(t) for t in o.index[weak.to_numpy()].tolist() if t not in n.index]
            if tolerance_ms:
                # nokta türü: tohum satırının ±tolerans içinde arşiv ikizi varsa aynı uzlaşmadır (çift satır kalmaz);
                # ikizi olmayan tohum satırı kanıtsız silinmez
                seeds = o[(o[SRC_COL] == SRC_SEED) & inr]
                cand += [int(t) for t in seeds.index.tolist() if t not in n.index and _nearest(new_ts, int(t), tolerance_ms)
                         is not None]
            for ts in cand:
                removed.append(ts)
                twin = _nearest(new_ts, ts, tolerance_ms) if tolerance_ms else None
                if twin is not None:
                    d1, _ = self._differ(o.loc[[ts], cmp_cols], n.loc[[twin], cmp_cols])
                    if not d1[0]:
                        st["realigned"] += 1             # aynı uzlaşma, ±ms oynamalı zaman damgası: fark değil
                        continue
                self._diff({"series": key, "ym": ym, "ts": ts, "at": iso_ms(ts), "action": "removed",
                            "old_src": str(o.at[ts, SRC_COL]), "new_src": SRC_ARCHIVE, "twin_ts": twin,
                            "cols": {c: [_json_safe(o.at[ts, c]), None] for c in vals}})
            st["removed"] = len(removed)
        changed = bool(st["new"] or replace_idx or removed)
        if not changed:
            return old.reset_index(drop=True), st, False
        keep = o.drop(index=replace_idx + removed)
        parts = [x for x in (keep, n.loc[replace_idx], n.loc[only_new]) if len(x)]
        merged = pd.concat(parts, ignore_index=True) if parts else old.iloc[0:0]
        merged = merged.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        return merged[cols + [SRC_COL]], st, True

    # ------------------------------------------------------------ yazım
    def _prepare(self, df: pd.DataFrame, kind: str, src: str, now_ms: int) -> tuple[pd.DataFrame, int, int]:
        cols = cols_for(kind)
        d = df.copy()
        if "is_closed" in d.columns:
            d = d[d["is_closed"].astype(bool)]
        for c in cols:
            if c not in d.columns:
                d[c] = float("nan")
        d = d[cols].copy()
        d["timestamp"] = d["timestamp"].astype("int64")
        if len(d) and int(d["timestamp"].max()) >= TS_US_GUARD:
            raise ValueError(f"µs zaman damgası depoya yazılamaz ({int(d['timestamp'].max())}); önce ms'ye çevrilmeli")
        step = step_ms(kind)
        if kind in POINT_KINDS or step is None:
            keep = d["timestamp"] <= int(now_ms)
        else:
            keep = d["timestamp"] + int(step) <= int(now_ms)
        dropped_open = int((~keep).sum())
        d = d[keep]
        dups = int(d["timestamp"].duplicated().sum())
        d = d.drop_duplicates("timestamp", keep="last").sort_values("timestamp", kind="mergesort")
        d[SRC_COL] = src
        return d.reset_index(drop=True), dropped_open, dups

    def write(self, market: str, symbol: str, timeframe: str, df: pd.DataFrame | None, *, source: str = "",  # type: ignore[override]
              chunk_id: str = "", src: str | None = None, now_ms: int | None = None,
              authoritative: tuple[int, int] | list[tuple[int, int]] | None = None,
              files: dict[str, dict] | None = None) -> dict[str, Any]:
        """Satırları ay parçalarına öncelik kuralıyla birleştir (madde 1, 3, 8). `src` yoksa `source` bir kaynak adıysa
        o, değilse `rest`. `authoritative`: doğrulanmış arşiv dosyalarının kapsadığı [a, b) aralık(lar)ı (madde 3).
        `files` arşiv dosya defterine aynı manifest yazımında eklenir. Dönen: {rows_in, rows_new, duplicates, parts,
        upgraded, changed, blocked, blocked_diff, removed, realigned, dropped_open}."""
        src = src or (source if source in PRIORITY else SRC_REST)
        if src not in PRIORITY:
            raise ValueError(f"bilinmeyen satır kaynağı: {src}")
        cols = cols_for(timeframe)
        now = int(now_ms if now_ms is not None else self.clock_ms())
        out: dict[str, Any] = {"rows_in": 0 if df is None else int(len(df)), "rows_new": 0, "duplicates": 0, "parts": [],
                               "upgraded": 0, "changed": 0, "blocked": 0, "blocked_diff": 0, "removed": 0,
                               "realigned": 0, "dropped_open": 0}
        d, dropped_open, dups = (self._prepare(df, timeframe, src, now) if df is not None and len(df)
                                 else (pd.DataFrame(columns=cols + [SRC_COL]), 0, 0))
        out["dropped_open"], out["duplicates"] = dropped_open, dups
        key = self.series_key(market, symbol, timeframe)
        groups: dict[str, pd.DataFrame] = {}
        if len(d):
            yms = [ym_of_ms(int(t)) for t in d["timestamp"].tolist()]
            d = d.assign(_ym=yms)
            groups = {ym: g.drop(columns="_ym") for ym, g in d.groupby("_ym", sort=True)}
        months = set(groups)
        ranges: list[tuple[int, int]] = []
        if authoritative is not None and src == SRC_ARCHIVE:
            ranges = [authoritative] if isinstance(authoritative, tuple) else list(authoritative)  # type: ignore[list-item]
            ranges = [(int(a), int(b)) for a, b in ranges if int(b) > int(a)]
            for a, b in ranges:
                months |= {ym_of_ms(a), ym_of_ms(b - 1)}
        if not months and not files:
            return out
        empty = pd.DataFrame({c: [] for c in cols + [SRC_COL]})
        with self.series_lock(market, symbol, timeframe):
            m = self.manifest(market, symbol, timeframe)
            touched: list[str] = []
            pending: list[tuple[str, Path, pd.DataFrame]] = []
            for ym in sorted(months):
                g = groups.get(ym, empty)
                y, mo = (int(x) for x in ym.split("/"))
                p = self.part_path(market, symbol, timeframe, y, mo)
                old = self._read_part(p, cols, keep_src=True) if p.exists() else None
                a, b = month_bounds_ym(ym)
                auth = [(max(a, x), min(b, y)) for x, y in ranges if max(a, x) < min(b, y)]
                if not len(g) and not auth:
                    continue
                merged, st, changed = self._merge(key, ym, old, g, cols, auth or None, src,
                                                  TOLERANCE_MS.get(timeframe, 0), COMPARE_COLS.get(timeframe))
                for k2 in ("upgraded", "changed", "blocked", "blocked_diff", "removed", "realigned"):
                    out[k2] += st[k2]
                out["rows_new"] += st["new"]
                if changed:
                    pending.append((ym, p, merged))
            if not pending and not files:
                return out
            marker = self.inflight_path(market, symbol, timeframe)
            if pending:
                self._write_atomic(marker, json.dumps({"months": [x[0] for x in pending], "at": _now_iso(),
                                                       "pid": os.getpid()}).encode("utf-8"))
            for ym, p, merged in pending:
                if not len(merged):
                    self._remove_part(p)
                    m.part_meta.pop(ym, None)
                    m.part_sha.pop(ym, None)
                    continue
                sha = self._write_part(p, merged)
                m.part_sha[ym] = sha
                m.part_meta[ym] = self._part_meta(merged, timeframe, sha, p)
                touched.append(ym)
            if files:
                m.files.update(files)
            m.provider = m.provider or self.provider
            m.downloaded_at = _now_iso()
            if pending:
                m.history = (m.history + [{"at": m.downloaded_at, "source": source or src, "chunk": chunk_id,
                                           "rows_in": out["rows_in"], "rows_new": out["rows_new"]}])[-50:]
            m.duplicate_count += dups
            self._recompute(m)
            self._save_manifest(m)
            if pending:
                try:
                    os.unlink(self._guard(marker))
                except OSError:
                    pass
        out["parts"] = touched
        return out

    def _remove_part(self, p: Path) -> None:
        for x in (p, self.sidecar_path(p)):
            try:
                os.unlink(self._guard(x))
            except FileNotFoundError:
                pass

    def note_files(self, market: str, symbol: str, timeframe: str, files: dict[str, dict]) -> None:
        """Yalnız arşiv dosya defterini güncelle (veri yazımı yok; ör. kalıcı 404 `m`)."""
        if not files:
            return
        with self.series_lock(market, symbol, timeframe):
            m = self.manifest(market, symbol, timeframe)
            m.files.update(files)
            self._recompute(m)
            self._save_manifest(m)

    def mark_bad_chunk(self, market: str, symbol: str, timeframe: str, chunk_id: str, reason: str) -> None:
        with self.series_lock(market, symbol, timeframe):
            super().mark_bad_chunk(market, symbol, timeframe, chunk_id, reason)

    def clear_bad_chunk(self, market: str, symbol: str, timeframe: str, chunk_id: str) -> None:
        """Yeniden denenip DOĞRULANAN parçanın fail-closed işaretini kaldır."""
        with self.series_lock(market, symbol, timeframe):
            m = self.manifest(market, symbol, timeframe)
            n = len(m.bad_chunks)
            m.bad_chunks = [b for b in m.bad_chunks if b.get("chunk") != chunk_id]
            if len(m.bad_chunks) != n:
                self._recompute(m)
                self._save_manifest(m)

    # ------------------------------------------------------------ okuma
    def iter_parts(self, market: str, symbol: str, timeframe: str, since_ms: int | None = None,
                   until_ms: int | None = None, *, keep_src: bool = False) -> Iterator[tuple[str, pd.DataFrame]]:
        """Ay parçalarını sırayla ver (akış; bütün seri belleğe alınmaz)."""
        cols = cols_for(timeframe)
        for ym, p in self._disk_parts(market, symbol, timeframe).items():
            a, b = month_bounds_ym(ym)
            if (until_ms is not None and a > until_ms) or (since_ms is not None and b <= since_ms):
                continue
            df = self._read_part(p, cols, keep_src=keep_src)
            if since_ms is not None:
                df = df[df["timestamp"] >= since_ms]
            if until_ms is not None:
                df = df[df["timestamp"] <= until_ms]
            yield ym, df.reset_index(drop=True)

    def read(self, market: str, symbol: str, timeframe: str, since_ms: int | None = None,  # type: ignore[override]
             until_ms: int | None = None, *, keep_src: bool = False) -> pd.DataFrame:
        cols = cols_for(timeframe) + ([SRC_COL] if keep_src else [])
        frames = [df for _, df in self.iter_parts(market, symbol, timeframe, since_ms, until_ms, keep_src=keep_src)
                  if len(df)]
        if not frames:
            return pd.DataFrame(columns=cols)
        df = pd.concat(frames, ignore_index=True).drop_duplicates("timestamp", keep="last")
        return df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)[cols]

    def read_with_src(self, market: str, symbol: str, timeframe: str, since_ms: int | None = None,
                      until_ms: int | None = None) -> pd.DataFrame:
        return self.read(market, symbol, timeframe, since_ms, until_ms, keep_src=True)

    # ------------------------------------------------------------ mühür (madde 2)
    def seal_listing(self) -> list[tuple[str, str, str]]:
        """Sıralı (seri, ay, parça sha256) — YALNIZ manifestlerden (parça okunmaz)."""
        out: list[tuple[str, str, str]] = []
        for market, sym, tf, mp in self._manifest_files():
            d = _read_json_plain(mp)
            if not isinstance(d, dict):
                continue
            for ym, sha in (d.get("part_sha") or {}).items():
                out.append((self.series_key(market, sym, tf), str(ym), str(sha)))
        out.sort()
        return out

    @staticmethod
    def seal_of(listing: list[tuple[str, str, str]] | list[list[str]]) -> str:
        lines = "".join(f"{s}|{ym}|{sha}\n" for s, ym, sha in listing)
        return _sha(lines.encode("utf-8"))

    def data_seal(self) -> tuple[str, list[tuple[str, str, str]]]:
        listing = self.seal_listing()
        return self.seal_of(listing), listing

    # ------------------------------------------------------------ denetim ve kurtarma (madde 4)
    def _sidecar_sha(self, p: Path) -> str | None:
        try:
            with open(self.sidecar_path(p), "r", encoding="ascii") as fh:
                t = fh.read().split()
        except (OSError, UnicodeDecodeError):
            return None
        return t[0].lower() if t else None

    def _deep_check(self, p: Path, m: ResearchManifest, ym: str, cols: list[str]) -> tuple[str, str | None, str]:
        """(durum, sha, neden). Parça baytları BİR kez okunur."""
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            return ST_CORRUPT, None, f"okunamadı: {exc}"[:200]
        self.byte_reads += 1
        sha = _sha(data)
        side = self._sidecar_sha(p)
        try:
            self._shape(self._parse_part(data, p.name), cols, True)
        except Exception as exc:  # noqa: BLE001 — çözülemeyen parça bozuktur
            return ST_CORRUPT, sha, f"çözülemedi: {type(exc).__name__}"
        if side is None:
            return ST_CORRUPT, sha, "yan dosya (.sha256) yok"
        if side != sha:
            return ST_CORRUPT, sha, "parça kendi .sha256'sıyla tutmuyor"
        if m.part_sha.get(ym) == sha:
            return ST_OK, sha, ""
        return ST_MANIFEST_LAG, sha, "parça sağlam, manifest geride"

    def check_series(self, market: str, symbol: str, timeframe: str, *, deep: bool = False) -> dict[str, Any]:
        """Ucuz denetim (stat); işaret/uyuşmazlık/manifestte olmayan parça → derin denetim. `deep=True` her parçayı
        derin denetler. Dönen: {ok, states: {ay: durum}, reasons, inflight, manifest_readable}."""
        cols = cols_for(timeframe)
        m, readable = self._load_manifest(market, symbol, timeframe)
        disk = self._disk_parts(market, symbol, timeframe)
        marker = self.inflight_path(market, symbol, timeframe)
        inflight = set()
        if marker.exists():
            d = _read_json_plain(marker)
            inflight = set((d or {}).get("months") or []) if isinstance(d, dict) else set(disk)
        states: dict[str, str] = {}
        reasons: dict[str, str] = {}
        shas: dict[str, str | None] = {}
        for ym, p in disk.items():
            pm = m.part_meta.get(ym) if readable else None
            need = deep or not readable or pm is None or ym in inflight or m.part_sha.get(ym) is None
            if not need:
                try:
                    st = p.stat()
                    need = int(st.st_size) != int(pm.get("size") or -1) or int(st.st_mtime_ns) != int(pm.get("mtime_ns") or -1)
                except OSError:
                    need = True
            if not need:
                states[ym] = ST_OK
                continue
            s, sha, why = self._deep_check(p, m, ym, cols)
            states[ym], shas[ym] = s, sha
            if why:
                reasons[ym] = why
        for ym in m.part_meta:
            if ym not in disk:
                states[ym] = ST_MISSING
                reasons[ym] = "manifestte var, diskte yok"
        bad = {k: v for k, v in states.items() if v != ST_OK}
        stat_refresh = [ym for ym in states if states[ym] == ST_OK and ym in shas]
        return {"ok": not bad and readable and not inflight, "states": states, "reasons": reasons, "shas": shas,
                "inflight": sorted(inflight), "manifest_readable": readable, "stat_refresh": stat_refresh}

    def _quarantine(self, market: str, symbol: str, timeframe: str, ym: str, p: Path) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        dest = self.quarantine_root() / market / symbol / timeframe / f"{ym.replace('/', '-')}-{stamp}{''.join(p.suffixes)}"
        self._guard(dest).parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, self._guard(dest))
        side = self.sidecar_path(p)
        if side.exists():
            os.replace(side, self._guard(dest.with_name(dest.name + ".sha256")))
        return str(dest)

    def recover_series(self, market: str, symbol: str, timeframe: str, *, night: str,
                       deep: bool = False) -> dict[str, Any]:
        """`check_series` sonucuna göre MANIFEST_LAG'ı kendiliğinden düzelt, CORRUPT'u karantinaya al (madde 4).
        Dönen: {ok, manifest_lag, corrupt, missing, quarantined, refetch, halted}."""
        res: dict[str, Any] = {"manifest_lag": [], "corrupt": [], "missing": [], "quarantined": [], "refetch": [],
                               "halted": False}
        chk = self.check_series(market, symbol, timeframe, deep=deep)
        res["reasons"] = chk["reasons"]
        if chk["ok"] and not chk["stat_refresh"]:
            m = self.manifest(market, symbol, timeframe)
            res.update(ok=True, refetch=list(m.refetch), halted=m.status == SERIES_HALTED)
            return res
        cols = cols_for(timeframe)
        with self.series_lock(market, symbol, timeframe):
            m, _readable = self._load_manifest(market, symbol, timeframe)
            disk = self._disk_parts(market, symbol, timeframe)
            at = _now_iso()
            for ym, state in sorted(chk["states"].items()):
                if state == ST_OK:
                    if ym in chk["stat_refresh"] and ym in m.part_meta and ym in disk:
                        st = disk[ym].stat()
                        m.part_meta[ym].update(size=int(st.st_size), mtime_ns=int(st.st_mtime_ns))
                    continue
                if state == ST_MANIFEST_LAG:
                    df = self._read_part(disk[ym], cols, keep_src=True)
                    sha = chk["shas"].get(ym) or ""
                    m.part_sha[ym] = sha
                    m.part_meta[ym] = self._part_meta(df, timeframe, sha, disk[ym])
                    m.recovery[ym] = {"state": ST_MANIFEST_LAG, "at": at, "night": night,
                                      "reason": chk["reasons"].get(ym)}
                    res["manifest_lag"].append(ym)
                else:
                    if state == ST_CORRUPT and ym in disk:
                        res["quarantined"].append(self._quarantine(market, symbol, timeframe, ym, disk[ym]))
                        res["corrupt"].append(ym)
                    else:
                        res["missing"].append(ym)
                    m.part_meta.pop(ym, None)
                    m.part_sha.pop(ym, None)
                    stamp = ym.replace("/", "-")
                    m.files = {k: v for k, v in m.files.items() if not k.startswith(stamp)}
                    prev = m.recovery.get(ym) or {}
                    m.recovery[ym] = {"state": ST_CORRUPT, "at": at, "night": night,
                                      "reason": chk["reasons"].get(ym), "failed_nights": list(prev.get("failed_nights") or [])}
                if ym not in m.refetch:
                    m.refetch.append(ym)
            m.refetch = sorted(set(m.refetch))
            self._recompute(m)
            self._save_manifest(m)
            marker = self.inflight_path(market, symbol, timeframe)
            if marker.exists():
                try:
                    os.unlink(self._guard(marker))
                except OSError:
                    pass
            res["refetch"] = list(m.refetch)
            res["halted"] = m.status == SERIES_HALTED
        res["ok"] = not (res["manifest_lag"] or res["corrupt"] or res["missing"])
        return res

    def refetch_result(self, market: str, symbol: str, timeframe: str, ym: str, *, ok: bool, night: str,
                       reason: str | None = None) -> dict[str, Any]:
        """Yeniden çekimin sonucu (madde 4). Başarı → ay kuyruktan ve kurtarma kaydından çıkar; seri başka bozuk ay
        yoksa yeniden `OK`. Başarısızlık (yalnız CORRUPT ay) → gece eklenir; son iki başarısız gece ARDIŞIK takvim
        günleriyse seri `HALTED`."""
        with self.series_lock(market, symbol, timeframe):
            m = self.manifest(market, symbol, timeframe)
            if ok:
                m.refetch = [x for x in m.refetch if x != ym]
                m.recovery.pop(ym, None)
                if m.status == SERIES_HALTED and not any((r or {}).get("halt") for r in m.recovery.values()):
                    m.status, m.halted_reason = SERIES_OK, None
            else:
                rec = m.recovery.setdefault(ym, {"state": ST_CORRUPT, "at": _now_iso(), "failed_nights": []})
                if rec.get("state") == ST_CORRUPT:
                    nights = sorted(set((rec.get("failed_nights") or []) + [night]))[-7:]
                    rec["failed_nights"] = nights
                    rec["last_error"] = (reason or "")[:200]
                    if len(nights) >= 2 and _consecutive(nights[-2], nights[-1]):
                        rec["halt"] = True
                        m.status = SERIES_HALTED
                        m.halted_reason = (f"{ym} iki ardışık gece ({nights[-2]}, {nights[-1]}) yeniden çekilemedi: "
                                           f"{(reason or '')[:120]}")
            self._recompute(m)
            self._save_manifest(m)
            return {"status": m.status, "refetch": list(m.refetch), "halted_reason": m.halted_reason}

    def validate(self, market: str, symbol: str, timeframe: str) -> dict:  # type: ignore[override]
        """HistoryStore.validate'in araştırma karşılığı (parça bazlı; bütün seri okunmaz)."""
        chk = self.check_series(market, symbol, timeframe)
        m = self.manifest(market, symbol, timeframe)
        issues = [f"{ym}: {st} ({chk['reasons'].get(ym, '')})" for ym, st in sorted(chk["states"].items()) if st != ST_OK]
        if chk["inflight"]:
            issues.append(f"yarım yazım işareti: {', '.join(chk['inflight'])}")
        if not chk["manifest_readable"]:
            issues.append("manifest okunamadı")
        return {"market": market, "symbol": symbol, "timeframe": timeframe, "ok": not issues and not m.bad_chunks,
                "issues": issues, "rows": m.row_count, "gaps": m.gap_count, "duplicates_removed": m.duplicate_count,
                "bad_chunks": len(m.bad_chunks), "quality": m.quality_score, "first_ts_ms": m.first_ts_ms,
                "last_ts_ms": m.last_ts_ms, "states": chk["states"], "status": m.status}

    def deep_validate(self, market: str, symbol: str, timeframe: str) -> dict[str, Any]:
        """Her parçayı derin denetle ve tam kanonik checksum'ı parça parça (akışla) hesapla; sonucu manifeste yaz.
        Kanonik biçim `history.store.canonical_checksum` ile aynıdır (float `%.10g`), parça başına hesaplanıp tek
        başlıkla birleştirilir."""
        chk = self.check_series(market, symbol, timeframe, deep=True)
        cols = cols_for(timeframe)
        h = hashlib.sha256()
        first, rows = True, 0
        for _ym, df in self.iter_parts(market, symbol, timeframe):
            if not len(df):
                continue
            buf = io.StringIO()
            df[cols].sort_values("timestamp", kind="mergesort").to_csv(buf, index=False, header=first,
                                                                       float_format="%.10g", lineterminator="\n")
            h.update(buf.getvalue().encode("utf-8"))
            first, rows = False, rows + len(df)
        canon = h.hexdigest() if rows else hashlib.sha256(b"").hexdigest()
        ok = all(v == ST_OK for v in chk["states"].values()) and not chk["inflight"]
        if ok:
            with self.series_lock(market, symbol, timeframe):
                m = self.manifest(market, symbol, timeframe)
                m.checksum, m.checksum_at = canon, _now_iso()
                self._recompute(m)
                self._save_manifest(m)
        return {"ok": ok, "states": chk["states"], "canonical": canon, "rows": rows}


#: fonlama boşluğunda yerel aralık penceresi: farkın kendisi + iki yanında bu kadar komşu fark (madde 5)
FUNDING_IV_HALF_WINDOW = 3


def _minute_diffs(ts: list[int]) -> list[int]:
    """Ardışık uzlaşma farkları, dakikaya yuvarlanmış (`calc_time` ±ms oynar); sıfır ve negatif fark atılır."""
    out = []
    for a, b in zip(ts, ts[1:]):
        d = round((int(b) - int(a)) / 60_000) * 60_000
        if d > 0:
            out.append(int(d))
    return out


def _rle(xs: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for x in xs:
        if out and out[-1][0] == x:
            out[-1][1] += 1
        else:
            out.append([int(x), 1])
    return out


def _unrle(runs: list) -> list[int]:
    out: list[int] = []
    for r in runs:
        try:
            d, n = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        out += [d] * max(0, n)
    return out


def funding_gaps(diffs: list[int], half: int = FUNDING_IV_HALF_WINDOW) -> tuple[int, int | None]:
    """Madde 5 (2026-10-06 düzeltmesi): her fark, kendisi dahil ±`half` komşu farkın medyanı olan YEREL aralığa
    bölünür; boşluk `max(0, round(fark / yerel) − 1)`. Ay içinde aralık değişimi (8h → 4h) sahte boşluk üretmez; bir
    eksik uzlaşma her rejimde 1 sayılır. Dönen: (boşluk, son farkın yerel aralığı)."""
    n = len(diffs)
    if not n:
        return 0, None
    gaps, last = 0, None
    for i in range(n):
        w = sorted(diffs[max(0, i - half): i + half + 1])
        k = len(w)
        iv = w[k // 2] if k % 2 else (w[k // 2 - 1] + w[k // 2]) // 2
        if iv > 0:
            gaps += max(0, round(diffs[i] / iv) - 1)
        last = iv
    return int(gaps), (int(last) if last else None)


def _nearest(sorted_ts: list[int], t: int, tol: int) -> int | None:
    i = bisect.bisect_left(sorted_ts, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(sorted_ts) and abs(sorted_ts[j] - t) <= tol:
            if best is None or abs(sorted_ts[j] - t) < abs(best - t):
                best = sorted_ts[j]
    return best


def _consecutive(a: str, b: str) -> bool:
    try:
        da, db = datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date()
    except ValueError:
        return False
    return (db - da).days == 1


__all__ = ["ArchiveUnitError", "DUKA_1H", "DUKA_1M", "DUKA_COLS", "FUNDING", "KINDS", "MARKETS", "MARKPX_1H", "METRICS_5M",
           "METRICS_COLS", "POINT_KINDS", "PREMIUM_1H", "PRIORITY", "PX_COLS", "ResearchManifest", "ResearchStore",
           "SERIES_HALTED", "SERIES_OK", "SOURCES", "SPOT_US_FROM_MS", "SRC_ARCHIVE", "SRC_COL", "SRC_REST", "SRC_SEED",
           "SRC_UNVERIFIED", "ST_CORRUPT", "ST_MANIFEST_LAG", "ST_MISSING", "ST_OK", "STORE_SCHEMA", "TF_MS",
           "TS_US_GUARD", "archive_ts_divisor", "cols_for", "funding_gaps", "iso_ms", "month_bounds_ym", "normalize_archive_ts",
           "step_ms", "ym_of_ms", "HAS_PARQUET"]

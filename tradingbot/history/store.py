"""HistoryStore — tarihsel piyasa verisi (OHLCV geniş şema, funding, OI, mark) için partitionlı, idempotent, manifest'li depo.

Yerleşim: root/<market>/<symbol_safe>/<timeframe>/<YYYY>/<MM>.<parquet|csv.gz>  (+ root/<market>/<symbol_safe>/<timeframe>/manifest.json)
- market: "spot" | "futures"; timeframe: kline aralığı ("1m".."1d") ya da veri türü ("funding", "oi_1h", "mark_1h")
- Yazım idempotent: aynı timestamp tekrar gelirse son kayıt kazanır (drop_duplicates keep="last"), sıralı, atomik (tmp+os.replace).
- Manifest: provider, market, symbol, timeframe, first/last event ts, downloaded_at, row_count, gap_count, duplicate_count (yazımda elenen),
  checksum (kanonik satırların sha256), schema_version, quality score, bad_chunks (fail-closed işaretli parçalar), cursor (resume).
- pyarrow yoksa csv.gz kullanılır (aynı API).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..core import atomic_write_json, iso, read_json
from ..market.providers import KLINE_COLUMNS, tf_ms
from ..storage.parquet_store import symbol_safe

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
KLINE_COLS = [c for c in KLINE_COLUMNS if c != "is_closed"]          # geniş şema (quote_volume, trades, taker_*)
FUNDING_COLS = ["timestamp", "rate", "mark"]
OI_COLS = ["timestamp", "oi", "oi_usdt"]
MARK_COLS = ["timestamp", "mark", "index"]
KIND_COLS = {"funding": FUNDING_COLS, "oi": OI_COLS, "mark": MARK_COLS}

try:  # pragma: no cover - ortam bağımlı
    import pyarrow  # noqa: F401
    HAS_PARQUET = True
except ImportError:  # pragma: no cover
    HAS_PARQUET = False


def _month_key(ts_ms: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return d.year, d.month


def step_ms_for(timeframe: str) -> int | None:
    """Kline aralıkları için ms adımı; funding 8h; oi_<p>/mark_<p> için <p>; bilinmeyen → None (gap hesaplanmaz)."""
    if timeframe == "funding":
        return 8 * 3_600_000
    if "_" in timeframe:
        try:
            return tf_ms(timeframe.split("_", 1)[1])
        except Exception:  # noqa: BLE001
            return None
    try:
        return tf_ms(timeframe)
    except Exception:  # noqa: BLE001
        return None


def cols_for(timeframe: str) -> list[str]:
    for k, cols in KIND_COLS.items():
        if timeframe == k or timeframe.startswith(k + "_"):
            return cols
    return KLINE_COLS


def canonical_checksum(df: pd.DataFrame, cols: list[str]) -> str:
    """Satır sıralı kanonik CSV'nin sha256'sı (float'lar repr ile) — depolama biçiminden bağımsız."""
    if df.empty:
        return hashlib.sha256(b"").hexdigest()
    buf = io.StringIO()
    df[cols].sort_values("timestamp").to_csv(buf, index=False, float_format="%.10g", lineterminator="\n")
    return hashlib.sha256(buf.getvalue().encode("utf-8")).hexdigest()


@dataclass
class Manifest:
    provider: str = ""
    market: str = ""
    symbol: str = ""
    timeframe: str = ""
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    downloaded_at: str = ""
    row_count: int = 0
    gap_count: int = 0
    duplicate_count: int = 0          # yazımlarda elenen tekrar satır toplamı
    checksum: str = ""
    schema_version: int = SCHEMA_VERSION
    quality_score: float = 1.0
    bad_chunks: list[dict] = field(default_factory=list)      # fail-closed işaretlenen parçalar
    cursor_ms: int | None = None      # resume: bir sonraki indirmenin başlayacağı ts
    parts: dict[str, int] = field(default_factory=dict)       # "YYYY/MM" → satır
    history: list[dict] = field(default_factory=list)         # kısa yazım günlüğü

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


class HistoryStore:
    def __init__(self, root: Path | str, *, provider: str = "binance"):
        self.root = Path(root)
        self.provider = provider

    # ------------------------------------------------------------ yollar
    def series_dir(self, market: str, symbol: str, timeframe: str) -> Path:
        return self.root / market / symbol_safe(symbol) / timeframe

    def part_path(self, market: str, symbol: str, timeframe: str, year: int, month: int) -> Path:
        ext = "parquet" if HAS_PARQUET else "csv.gz"
        return self.series_dir(market, symbol, timeframe) / f"{year:04d}" / f"{month:02d}.{ext}"

    def manifest_path(self, market: str, symbol: str, timeframe: str) -> Path:
        return self.series_dir(market, symbol, timeframe) / "manifest.json"

    def manifest(self, market: str, symbol: str, timeframe: str) -> Manifest:
        d = read_json(self.manifest_path(market, symbol, timeframe), default=None)
        return Manifest.from_dict(d) if isinstance(d, dict) else Manifest(provider=self.provider, market=market, symbol=symbol, timeframe=timeframe)

    def _save_manifest(self, m: Manifest) -> None:
        atomic_write_json(self.manifest_path(m.market, m.symbol, m.timeframe), m.to_dict(), indent=1)

    # ------------------------------------------------------------ parça IO
    def _read_part(self, p: Path, cols: list[str]) -> pd.DataFrame:
        if not p.exists():
            return pd.DataFrame(columns=cols)
        if p.suffix == ".parquet":
            df = pd.read_parquet(p)
        else:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                df = pd.read_csv(fh)
        for c in cols:
            if c not in df.columns:
                df[c] = float("nan")
        df["timestamp"] = df["timestamp"].astype("int64")
        return df[cols]

    def _write_part(self, p: Path, df: pd.DataFrame) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
        os.close(fd)
        try:
            if p.suffix == ".parquet":
                df.to_parquet(tmp, index=False)
            else:
                with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                    df.to_csv(fh, index=False, lineterminator="\n")
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------ yazım (idempotent)
    def write(self, market: str, symbol: str, timeframe: str, df: pd.DataFrame, *, source: str = "", chunk_id: str = "") -> dict:
        """Satırları ay partition'larına birleştir (dup: son kazanır), manifest'i güncelle. Dönen: {rows_in, rows_new, duplicates, parts}."""
        cols = cols_for(timeframe)
        if df is None or df.empty:
            return {"rows_in": 0, "rows_new": 0, "duplicates": 0, "parts": []}
        d = df.copy()
        for c in cols:
            if c not in d.columns:
                d[c] = float("nan")
        d = d[cols]
        d["timestamp"] = d["timestamp"].astype("int64")
        in_dups = int(d["timestamp"].duplicated().sum())
        d = d.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        m = self.manifest(market, symbol, timeframe)
        rows_new = 0
        touched: list[str] = []
        d["_ym"] = [f"{y:04d}/{mo:02d}" for y, mo in (_month_key(int(t)) for t in d["timestamp"])]
        for ym, grp in d.groupby("_ym", sort=True):
            y, mo = (int(x) for x in ym.split("/"))
            p = self.part_path(market, symbol, timeframe, y, mo)
            old = self._read_part(p, cols)
            before = len(old)
            merged = pd.concat([old, grp[cols]], ignore_index=True).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
            self._write_part(p, merged.reset_index(drop=True))
            rows_new += len(merged) - before
            in_dups += (before + len(grp)) - len(merged)
            m.parts[ym] = int(len(merged))
            touched.append(ym)
        m.duplicate_count += in_dups
        m.provider = m.provider or self.provider
        m.market, m.symbol, m.timeframe = market, symbol, timeframe
        m.downloaded_at = iso()
        m.history = (m.history + [{"at": m.downloaded_at, "source": source, "chunk": chunk_id, "rows_in": int(len(df)), "rows_new": int(rows_new)}])[-50:]
        self._recompute(m)
        self._save_manifest(m)
        return {"rows_in": int(len(df)), "rows_new": int(rows_new), "duplicates": int(in_dups), "parts": touched}

    def mark_bad_chunk(self, market: str, symbol: str, timeframe: str, chunk_id: str, reason: str) -> None:
        """Bozuk/doğrulanamayan parça: veri YAZILMAZ; manifest'e fail-closed işaret düşülür (quality düşer)."""
        m = self.manifest(market, symbol, timeframe)
        m.market, m.symbol, m.timeframe = market, symbol, timeframe
        if not any(b.get("chunk") == chunk_id for b in m.bad_chunks):
            m.bad_chunks.append({"chunk": chunk_id, "reason": reason[:200], "at": iso()})
        self._recompute(m)
        self._save_manifest(m)

    def set_cursor(self, market: str, symbol: str, timeframe: str, cursor_ms: int | None) -> None:
        m = self.manifest(market, symbol, timeframe)
        m.market, m.symbol, m.timeframe = market, symbol, timeframe
        m.cursor_ms = cursor_ms
        self._save_manifest(m)

    def _recompute(self, m: Manifest) -> None:
        cols = cols_for(m.timeframe)
        df = self.read(m.market, m.symbol, m.timeframe)
        m.row_count = int(len(df))
        m.first_ts_ms = int(df["timestamp"].iloc[0]) if len(df) else None
        m.last_ts_ms = int(df["timestamp"].iloc[-1]) if len(df) else None
        m.checksum = canonical_checksum(df, cols)
        step = step_ms_for(m.timeframe)
        gaps = 0
        if step and len(df) > 1:
            diffs = df["timestamp"].diff().iloc[1:]
            gaps = int(((diffs // step) - 1).clip(lower=0).sum())
        m.gap_count = gaps
        expected = (m.row_count + gaps) if step else m.row_count
        completeness = m.row_count / expected if expected else 1.0
        m.quality_score = round(max(0.0, min(1.0, completeness - 0.1 * len(m.bad_chunks))), 4)

    # ------------------------------------------------------------ okuma
    def read(self, market: str, symbol: str, timeframe: str, since_ms: int | None = None, until_ms: int | None = None) -> pd.DataFrame:
        cols = cols_for(timeframe)
        d = self.series_dir(market, symbol, timeframe)
        if not d.exists():
            return pd.DataFrame(columns=cols)
        parts = sorted(p for p in d.glob("*/*") if p.suffix in (".parquet", ".gz"))
        frames = []
        for p in parts:
            try:
                y, mo = int(p.parent.name), int(p.name.split(".")[0])
            except ValueError:
                continue
            first = int(datetime(y, mo, 1, tzinfo=timezone.utc).timestamp() * 1000)
            nxt = int(datetime(y + (mo == 12), (mo % 12) + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            if (until_ms is not None and first > until_ms) or (since_ms is not None and nxt <= since_ms):
                continue
            frames.append(self._read_part(p, cols))
        if not frames:
            return pd.DataFrame(columns=cols)
        df = pd.concat(frames, ignore_index=True).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        if since_ms is not None:
            df = df[df["timestamp"] >= since_ms]
        if until_ms is not None:
            df = df[df["timestamp"] <= until_ms]
        return df.reset_index(drop=True)

    def series(self) -> list[tuple[str, str, str]]:
        out = []
        for mp in self.root.rglob("manifest.json"):
            rel = mp.parent.relative_to(self.root).parts
            if len(rel) == 3:
                out.append((rel[0], rel[1].replace("_", "/", 1) if "/" not in rel[1] else rel[1], rel[2]))
        return sorted(out)

    def validate(self, market: str, symbol: str, timeframe: str) -> dict:
        """Manifest'i diskteki veriden yeniden hesapla ve karşılaştır: checksum/row/gap uyumsuzluğu → ok=False."""
        m = self.manifest(market, symbol, timeframe)
        fresh = Manifest.from_dict(m.to_dict())
        self._recompute(fresh)
        issues = []
        for k in ("row_count", "checksum", "gap_count", "first_ts_ms", "last_ts_ms"):
            if getattr(m, k) != getattr(fresh, k):
                issues.append(f"{k}: manifest={getattr(m, k)} disk={getattr(fresh, k)}")
        return {"market": market, "symbol": symbol, "timeframe": timeframe, "ok": not issues and not m.bad_chunks, "issues": issues,
                "rows": fresh.row_count, "gaps": fresh.gap_count, "duplicates_removed": m.duplicate_count, "bad_chunks": len(m.bad_chunks),
                "quality": fresh.quality_score, "first_ts_ms": fresh.first_ts_ms, "last_ts_ms": fresh.last_ts_ms}


__all__ = ["HistoryStore", "Manifest", "SCHEMA_VERSION", "KLINE_COLS", "canonical_checksum", "step_ms_for", "cols_for", "HAS_PARQUET"]

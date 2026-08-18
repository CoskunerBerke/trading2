"""Mum deposu — sembol/zaman dilimi başına tek dosya: `root/{exchange}/{symbol_safe}/{tf}.parquet`.

pyarrow varsa Parquet, yoksa aynı API ile CSV (`.csv`). Yazım: timestamp'e göre birleştir + tekilleştir + sırala,
atomik yaz. Sütunlar: timestamp(ms), open, high, low, close, volume.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from ..core import StorageError, iso

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _has_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def symbol_safe(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "-")


class CandleStore:
    def __init__(self, root: Path | str, exchange: str = "binance", *, prefer_parquet: bool = True):
        self.root = Path(root)
        self.exchange = exchange
        self.parquet = prefer_parquet and _has_pyarrow()
        self.ext = "parquet" if self.parquet else "csv"

    def path(self, symbol: str, tf: str) -> Path:
        return self.root / self.exchange / symbol_safe(symbol) / f"{tf}.{self.ext}"

    # ------------------------------------------------------------ io
    def _read_file(self, p: Path) -> pd.DataFrame:
        if not p.exists():
            return pd.DataFrame(columns=COLUMNS)
        try:
            df = pd.read_parquet(p) if self.parquet else pd.read_csv(p)
        except (OSError, ValueError) as exc:
            raise StorageError(f"mum dosyası okunamadı: {p}: {exc}") from exc
        return df

    def _write_file(self, p: Path, df: pd.DataFrame) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp-{os.getpid()}")
        try:
            if self.parquet:
                df.to_parquet(tmp, index=False)
            else:
                df.to_csv(tmp, index=False)
            os.replace(tmp, p)
        except (OSError, ValueError) as exc:
            try:
                tmp.unlink(missing_ok=True)
            finally:
                raise StorageError(f"mum dosyası yazılamadı: {p}: {exc}") from exc

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in COLUMNS if c not in df.columns]
        if missing:
            raise StorageError(f"OHLCV sütunları eksik: {missing}")
        out = df[COLUMNS].copy()
        out["timestamp"] = out["timestamp"].astype("int64")
        for c in COLUMNS[1:]:
            out[c] = out[c].astype("float64")
        return out

    def write(self, symbol: str, tf: str, df: pd.DataFrame) -> int:
        """Ekle + timestamp'e göre tekilleştir (yeni kayıt kazanır). Dönen: dosyadaki toplam satır."""
        if df is None or len(df) == 0:
            return len(self._read_file(self.path(symbol, tf)))
        new = self._normalize(df)
        p = self.path(symbol, tf)
        old = self._read_file(p)
        merged = pd.concat([self._normalize(old) if len(old) else old, new], ignore_index=True) if len(old) else new
        merged = merged.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
        self._write_file(p, merged)
        return len(merged)

    def read(self, symbol: str, tf: str, since_ms: int | None = None, until_ms: int | None = None) -> pd.DataFrame:
        df = self._read_file(self.path(symbol, tf))
        if len(df) == 0:
            return pd.DataFrame(columns=COLUMNS)
        df = self._normalize(df)
        if since_ms is not None:
            df = df[df["timestamp"] >= int(since_ms)]
        if until_ms is not None:
            df = df[df["timestamp"] <= int(until_ms)]
        return df.reset_index(drop=True)

    def last_ts(self, symbol: str, tf: str) -> int | None:
        df = self._read_file(self.path(symbol, tf))
        return int(df["timestamp"].max()) if len(df) else None

    def index_row(self, symbol: str, tf: str) -> dict[str, Any]:
        """`candles_index` tablosuna yazılacak satır (Repository.upsert_candles_index)."""
        df = self._read_file(self.path(symbol, tf))
        n = len(df)
        return {"id": f"{self.exchange}|{symbol}|{tf}", "exchange": self.exchange, "symbol": symbol, "timeframe": tf,
                "path": str(self.path(symbol, tf)), "first_ts_ms": int(df["timestamp"].min()) if n else None,
                "last_ts_ms": int(df["timestamp"].max()) if n else None, "rows": n, "updated_at_utc": iso()}

    def symbols(self) -> list[tuple[str, str]]:
        """Depodaki (symbol_safe, tf) çiftleri."""
        base = self.root / self.exchange
        if not base.exists():
            return []
        return sorted((d.name, f.stem) for d in base.iterdir() if d.is_dir() for f in d.glob(f"*.{self.ext}"))


__all__ = ["CandleStore", "symbol_safe", "COLUMNS"]

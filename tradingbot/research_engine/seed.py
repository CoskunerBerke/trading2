"""İlk tohum: worker deposundan (`data/market/history`) TUTARLI, SALT-OKUNUR kopya (§3.2 "İlk tohum"; P1b kabul 11).

Worker'ın elindeki seriler (ör. 89 vadeli 4h serisi) yeniden indirilmesin diye araştırma deposu boş bir seriye
başlarken önce worker'ın parquet'leri kopyalanır. Worker'ın IndexRefresher'ı kopya sırasında yazıyor olabilir; bu
yüzden seri başına:

1. manifest baytları okunur → bütün parçalar (bayt olarak, BİR kez) okunur → manifest baytları YENİDEN okunur;
2. iki manifest aynı değilse ya da kopya manifestle tutmuyorsa (`HistoryStore.validate`'in denetimi: satır sayısı,
   kanonik checksum, ilk/son zaman damgası, boşluk; `bad_chunks` varsa da tutmuyor sayılır) seri BİR kez daha kopyalanır;
3. yine olmazsa seri `REDOWNLOAD` olur: arşivden indirilir (ilk doldurmanın normal yolu; tohum hiçbir koşulda bütün
   işi başarısız saymaz).

Kopyalanan satırlar `_src=seed` ile yazılır (öncelik: archive > seed > archive_unverified > rest; sonradan gelen
doğrulanmış arşiv ayı tohum satırlarının yerine geçer ve fark kaydedilir). Worker dosyaları yalnız `open(..., "rb")`
ile açılır; `HistoryStore.manifest` / `core.read_json` KULLANILMAZ (bozuk bir manifesti yeniden adlandırırlardı = worker
deposuna yazım). Yalnız tohumlanabilir türler kopyalanır: kline aralıkları ve `funding` (worker'ın `oi_*`/`mark_*`
türleri araştırma türleriyle aynı değildir).
"""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..history.store import canonical_checksum, cols_for as worker_cols_for, step_ms_for as worker_step_ms_for
from .store import FUNDING, SRC_SEED, TF_MS, ResearchStore

ST_SEEDED, ST_REDOWNLOAD, ST_ABSENT, ST_SKIPPED, ST_ERROR = "SEEDED", "REDOWNLOAD", "ABSENT", "SKIPPED", "ERROR"
SEEDABLE_KINDS = frozenset(set(TF_MS) | {FUNDING})
ATTEMPTS = 2


class Inconsistent(RuntimeError):
    """Kopya worker manifestiyle tutarlı değil (eşzamanlı yazım ya da bozuk seri)."""


def worker_symbol_dirs(symbol: str) -> list[str]:
    """Ham sembol (`BTCUSDT`) → worker deposunun olası klasör adları (`symbol_safe`: '/'→'_', ':'→'-')."""
    out = []
    for q in ("USDT", "USDC", "BUSD"):
        if symbol.endswith(q) and len(symbol) > len(q):
            base = symbol[: -len(q)]
            out += [f"{base}_{q}", f"{base}_{q}-{q}"]
            break
    out.append(symbol)
    return list(dict.fromkeys(out))


def worker_series_dir(worker_root: Path | str, market: str, symbol: str, kind: str) -> Path | None:
    root = Path(worker_root)
    for name in worker_symbol_dirs(symbol):
        d = root / market / name / kind
        if (d / "manifest.json").is_file():
            return d
    return None


def _read_bytes(p: Path) -> bytes:
    with open(p, "rb") as fh:
        return fh.read()


def _parse(data: bytes, name: str) -> pd.DataFrame:
    if name.endswith(".parquet"):
        return pd.read_parquet(io.BytesIO(data))
    with gzip.open(io.BytesIO(data), "rt", encoding="utf-8") as fh:
        return pd.read_csv(fh)


def _gaps(ts: list[int], step: int | None) -> int:
    if not step or len(ts) < 2:
        return 0
    return int(sum(max(0, (b - a) // step - 1) for a, b in zip(ts, ts[1:])))


def read_consistent(series_dir: Path, kind: str, *, hook: Callable[[Path, int], None] | None = None,
                    attempt: int = 1) -> tuple[list[tuple[str, pd.DataFrame]], dict]:
    """Madde 1–2: tutarlı bir kopya döndür ya da `Inconsistent` yükselt. `hook(series_dir, attempt)` yalnız testte
    (eşzamanlı yazıcı benzetimi; parçalar okunduktan sonra, manifest yeniden okunmadan önce çağrılır)."""
    mp = series_dir / "manifest.json"
    m1 = _read_bytes(mp)
    try:
        man = json.loads(m1.decode("utf-8"))
    except ValueError as exc:
        raise Inconsistent(f"manifest okunamadı: {exc}") from exc
    if not isinstance(man, dict):
        raise Inconsistent("manifest bir JSON nesnesi değil")
    cols = worker_cols_for(kind)
    frames: list[tuple[str, pd.DataFrame]] = []
    for p in sorted(series_dir.glob("*/*")):
        if not p.is_file() or not (p.name.endswith(".parquet") or p.name.endswith(".csv.gz")):
            continue
        try:
            y, mo = int(p.parent.name), int(p.name.split(".")[0])
        except ValueError:
            continue
        try:
            df = _parse(_read_bytes(p), p.name)
        except Exception as exc:  # noqa: BLE001 — okunamayan parça: kopya tutarsız
            raise Inconsistent(f"parça okunamadı {p.parent.name}/{p.name}: {type(exc).__name__}") from exc
        for c in cols:
            if c not in df.columns:
                df[c] = float("nan")
        df["timestamp"] = df["timestamp"].astype("int64")
        frames.append((f"{y:04d}/{mo:02d}", df[cols]))
    if hook is not None:
        hook(series_dir, attempt)
    m2 = _read_bytes(mp)
    if m1 != m2:
        raise Inconsistent("manifest kopya sırasında değişti")
    if man.get("bad_chunks"):
        raise Inconsistent(f"worker manifestinde {len(man['bad_chunks'])} fail-closed parça var")
    full = (pd.concat([f for _, f in frames], ignore_index=True) if frames else pd.DataFrame(columns=cols))
    full = full.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    ts = [int(x) for x in full["timestamp"].tolist()]
    issues = []
    if int(man.get("row_count") or 0) != len(ts):
        issues.append(f"satır manifest={man.get('row_count')} kopya={len(ts)}")
    if str(man.get("checksum") or "") != canonical_checksum(full, cols):
        issues.append("kanonik checksum tutmuyor")
    if (man.get("first_ts_ms"), man.get("last_ts_ms")) != ((ts[0], ts[-1]) if ts else (None, None)):
        issues.append("ilk/son zaman damgası tutmuyor")
    if int(man.get("gap_count") or 0) != _gaps(ts, worker_step_ms_for(kind)):
        issues.append("boşluk sayısı tutmuyor")
    if issues:
        raise Inconsistent("; ".join(issues))
    return frames, man


def seed_series(store: ResearchStore, worker_root: Path | str, market: str, symbol: str, kind: str, *,
                now_ms: int | None = None, attempts: int = ATTEMPTS,
                hook: Callable[[Path, int], None] | None = None) -> dict[str, Any]:
    """Tek seriyi tohumla. Yalnız araştırma serisi BOŞSA çalışır. Dönen `status`: SEEDED | REDOWNLOAD | ABSENT |
    SKIPPED | ERROR (ERROR da arşivden indirmeye düşer; tohum çalıştırmayı durdurmaz)."""
    key = store.series_key(market, symbol, kind)
    if kind not in SEEDABLE_KINDS:
        return {"series": key, "status": ST_SKIPPED, "reason": "tohumlanamaz tür"}
    wdir = worker_series_dir(worker_root, market, symbol, kind)
    if wdir is None:
        return {"series": key, "status": ST_ABSENT}
    if store.manifest(market, symbol, kind).row_count:
        return {"series": key, "status": ST_SKIPPED, "reason": "araştırma serisi dolu"}
    reasons: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            frames, man = read_consistent(wdir, kind, hook=hook, attempt=attempt)
        except Inconsistent as exc:
            reasons.append(f"deneme {attempt}: {exc}"[:200])
            continue
        except OSError as exc:
            reasons.append(f"deneme {attempt}: okunamadı: {exc}"[:200])
            continue
        rows = 0
        try:
            for _ym, df in frames:
                rows += int(store.write(market, symbol, kind, df, src=SRC_SEED, now_ms=now_ms,
                                        chunk_id=f"seed:{wdir.parent.name}")["rows_new"])
        except Exception as exc:  # noqa: BLE001 — yazım hatası: seri arşivden indirilir
            return {"series": key, "status": ST_ERROR, "reason": f"{type(exc).__name__}: {exc}"[:200],
                    "worker_dir": str(wdir)}
        return {"series": key, "status": ST_SEEDED, "attempt": attempt, "rows": rows, "months": len(frames),
                "worker_dir": str(wdir), "worker_rows": int(man.get("row_count") or 0), "reasons": reasons}
    return {"series": key, "status": ST_REDOWNLOAD, "reasons": reasons, "worker_dir": str(wdir)}


__all__ = ["ATTEMPTS", "Inconsistent", "SEEDABLE_KINDS", "ST_ABSENT", "ST_ERROR", "ST_REDOWNLOAD", "ST_SEEDED",
           "ST_SKIPPED", "read_consistent", "seed_series", "worker_series_dir", "worker_symbol_dirs"]

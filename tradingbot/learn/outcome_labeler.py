"""Aday sonuç etiketleyici — her aday anlık görüntüsü ufuk dolunca ileri fiyatla etiketlenir.

KANIT ONARIMI V1 (2026-09-09): `entry_snapshot.jsonl` 2.677 aday değerlendirmesini tam özellik
setiyle tutuyordu ama hiçbirinin SONUCU işlenmiyordu; öğrenme katmanı 27 kapanışla açlıktaydı.
Bu modül o boşluğu kapatır: ufku dolan her aday, üçlü bariyerle (hedef / stop / zaman) etiketlenir.

Sözleşme:
  * Etiket AYRI bir dosyaya yazılır (`entry_outcomes.jsonl`, append-only). `entry_snapshot.jsonl`
    DEĞİŞTİRİLMEZ; mevcut tüketiciler ve rotasyon/arşiv mantığı etkilenmez. Birleştirme anahtarı
    `candidate_id`.
  * Giriş referansı = ts anındaki ilk barın AÇILIŞI (motor piyasadan doldurur; plan girişi değil).
    Stop ve hedef plandan. Aynı bar içinde hem stop hem hedef → STOP (defterin öncelik sırası).
    Ufuk dolarsa ZAMAN: kapanış fiyatından piyasa değeri, `label=None`, `r_net` gerçek.
  * Maliyet: gidiş-dönüş `cost_r` (R cinsinden) her sonuçtan düşülür. Ölçümdeki değer 0,16R.
  * Bir aday BİR kez etiketlenir (idempotent). Sağlayıcı arızası o sembolü atlar, turu DURDURMAZ.
  * Tur başına en fazla `max_symbols` sembol (en eski önce) — oran bütçesi sınırlı kalır.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

SCHEMA_VERSION = "entry_outcome_v1"
OUTCOME_TARGET = "TARGET"
OUTCOME_STOP = "STOP"
OUTCOME_TIMEOUT = "TIMEOUT"
_H_MS = 3_600_000


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _ts_ms(row: dict) -> int | None:
    v = row.get("ts_ms")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    s = row.get("ts")
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")


def _bar_ms(v: Any) -> int | None:
    """Bar zaman damgası: ms int, saniye, ya da datetime/Timestamp olabilir."""
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        try:
            return int(v.timestamp() * 1000)
        except (TypeError, ValueError, OSError):
            return None
    x = _f(v)
    if x is None:
        return None
    return int(x * 1000) if x < 1e11 else int(x)


def frame_to_bars(df: Any) -> list[tuple[int, float, float, float, float]]:
    """DataFrame (timestamp/open/high/low/close) → sıralı (open_ms, o, h, l, c) listesi."""
    out: list[tuple[int, float, float, float, float]] = []
    if df is None or len(df) == 0:
        return out
    try:
        cols = {c: i for i, c in enumerate(df.columns)}
        need = ("timestamp", "open", "high", "low", "close")
        if any(c not in cols for c in need):
            return out
        for row in df.itertuples(index=False, name=None):
            om = _bar_ms(row[cols["timestamp"]])
            o, h, l, c = (_f(row[cols["open"]]), _f(row[cols["high"]]), _f(row[cols["low"]]), _f(row[cols["close"]]))
            if om is None or None in (o, h, l, c):
                continue
            out.append((om, o, h, l, c))
    except Exception as exc:  # noqa: BLE001 — bozuk çerçeve: boş döner, çağıran "veri yok" sayar
        log.warning("bar çerçevesi çözümlenemedi: %s", exc)
        return []
    out.sort()
    return out


def triple_barrier(bars: Iterable[tuple[int, float, float, float, float]], *, start_ms: int, stop: float,
                   target: float, long_: bool, horizon_ms: int, cost_r: float) -> dict | None:
    """Üçlü bariyer. Giriş = start_ms'den itibaren İLK barın açılışı. None = yeterli bar yok."""
    end = start_ms + horizon_ms
    entry: float | None = None
    last_close: float | None = None
    n = 0
    for (om, o, h, l, c) in bars:
        if om < start_ms:
            continue
        if om > end:
            break
        if entry is None:
            entry = o
            risk = abs(entry - stop)
            if risk <= 0 or (long_ and stop >= entry) or ((not long_) and stop <= entry):
                return {"outcome": "INVALID", "label": None, "r_net": None, "entry_ref": entry,
                        "resolved_at_ms": om, "bars": 0, "reason": "stop yanlış tarafta ya da sıfır mesafe"}
        n += 1
        last_close = c
        hit_stop = (l <= stop) if long_ else (h >= stop)
        hit_tgt = (h >= target) if long_ else (l <= target)
        if hit_stop:                                   # aynı barda ikisi de → STOP (muhafazakâr)
            return {"outcome": OUTCOME_STOP, "label": 0, "r_net": round(-1.0 - cost_r, 6), "entry_ref": entry,
                    "resolved_at_ms": om, "bars": n}
        if hit_tgt:
            rr = abs(target - entry) / abs(entry - stop)
            return {"outcome": OUTCOME_TARGET, "label": 1, "r_net": round(rr - cost_r, 6), "entry_ref": entry,
                    "resolved_at_ms": om, "bars": n}
    if entry is None or last_close is None:
        return None
    mtm = (last_close - entry) / abs(entry - stop) * (1.0 if long_ else -1.0)
    return {"outcome": OUTCOME_TIMEOUT, "label": None, "r_net": round(mtm - cost_r, 6), "entry_ref": entry,
            "resolved_at_ms": min(end, bars[-1][0] if isinstance(bars, list) and bars else end), "bars": n}


class OutcomeStore:
    """Append-only `entry_outcomes.jsonl`. Arıza çağıranı ÇÖKERTMEZ."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.errors = 0

    def labeled_ids(self) -> set[str]:
        out: set[str] = set()
        try:
            if not self.path.exists():
                return out
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    cid = r.get("candidate_id")
                    if cid:
                        out.add(str(cid))
        except OSError as exc:
            log.warning("entry_outcomes okunamadı: %s", exc)
        return out

    def iter_rows(self) -> Iterable[dict]:
        try:
            if not self.path.exists():
                return
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError as exc:
            log.warning("entry_outcomes okunamadı: %s", exc)

    def append(self, row: dict) -> bool:
        try:
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            return True
        except (OSError, TypeError, ValueError) as exc:
            self.errors += 1
            log.warning("entry_outcomes yazılamadı: %s", exc)
            return False


@dataclass
class LabelStats:
    considered: int = 0          # okunan snapshot satırı
    already_labeled: int = 0
    not_due: int = 0
    skipped_spot: int = 0
    skipped_invalid: int = 0     # giriş/stop/hedef/ts eksik
    due: int = 0
    symbols_due: int = 0
    symbols_fetched: int = 0
    symbols_deferred: int = 0    # max_symbols tavanı: sonraki tura kaldı
    no_data: int = 0
    labeled: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    invalid_geometry: int = 0
    write_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["errors"] = list(self.errors[:20])
        d["ok"] = not self.errors or self.labeled > 0
        return d


def _fetch_bars(provider: Any, symbol: str, tf: str, start_ms: int, end_ms: int, *, page: int = 1000,
                max_pages: int = 8) -> list[tuple[int, float, float, float, float]]:
    step = _H_MS if tf == "1h" else _H_MS * 4
    out: list[tuple[int, float, float, float, float]] = []
    cursor = start_ms
    for _ in range(max_pages):
        df = provider.klines(symbol, tf, limit=page, start_ms=cursor)
        bars = frame_to_bars(df)
        if not bars:
            break
        out.extend(bars)
        last = bars[-1][0]
        if len(bars) < page or last >= end_ms:
            break
        cursor = last + step
    # yinelenenleri at, sırala
    seen: set[int] = set()
    uniq = []
    for b in sorted(out):
        if b[0] in seen:
            continue
        seen.add(b[0])
        uniq.append(b)
    return uniq


def label_pending(snapshot_rows: Iterable[dict], outcomes: OutcomeStore, provider: Any, *, now_ms: int,
                  horizon_h: int = 168, cost_r: float = 0.16, max_symbols: int = 15, tf: str = "1h",
                  run_id: str | None = None) -> LabelStats:
    """Ufku dolmuş, henüz etiketlenmemiş adayları etiketler. Salt ekleme yapar."""
    st = LabelStats()
    horizon_ms = int(horizon_h) * _H_MS
    done = outcomes.labeled_ids()
    by_symbol: dict[str, list[dict]] = {}
    for r in snapshot_rows:
        if not isinstance(r, dict) or r.get("kind"):          # link/outcome satırları snapshot değildir
            continue
        st.considered += 1
        cid = str(r.get("candidate_id") or "")
        if not cid:
            st.skipped_invalid += 1
            continue
        if cid in done:
            st.already_labeled += 1
            continue
        if str(r.get("market_type") or "").lower() == "spot":
            st.skipped_spot += 1
            continue
        ts = _ts_ms(r)
        entry = _f(r.get("entry_price")); stop = _f(r.get("stop_price"))
        tgts = r.get("targets") or []
        tgt = _f(tgts[0]) if isinstance(tgts, (list, tuple)) and tgts else None
        if ts is None or not r.get("symbol") or not entry or not stop or not tgt:
            st.skipped_invalid += 1
            continue
        if ts + horizon_ms > now_ms:
            st.not_due += 1
            continue
        st.due += 1
        by_symbol.setdefault(str(r["symbol"]), []).append(r | {"_ts": ts, "_stop": stop, "_tgt": tgt})
    st.symbols_due = len(by_symbol)
    order = sorted(by_symbol, key=lambda s: min(x["_ts"] for x in by_symbol[s]))
    take, defer = order[: max(0, int(max_symbols))], order[max(0, int(max_symbols)):]
    st.symbols_deferred = len(defer)
    labeled_at = _iso(now_ms)
    for sym in take:
        rows = by_symbol[sym]
        t0 = min(x["_ts"] for x in rows) - _H_MS
        t1 = max(x["_ts"] for x in rows) + horizon_ms + _H_MS
        try:
            bars = _fetch_bars(provider, sym, tf, t0, t1)
        except Exception as exc:  # noqa: BLE001 — bir sembol diğerlerini durdurmaz
            st.errors.append(f"{sym}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        st.symbols_fetched += 1
        if not bars:
            st.no_data += len(rows)
            continue
        for r in rows:
            long_ = str(r.get("direction") or "").upper() == "LONG"
            res = triple_barrier(bars, start_ms=r["_ts"], stop=r["_stop"], target=r["_tgt"], long_=long_,
                                 horizon_ms=horizon_ms, cost_r=cost_r)
            if res is None:
                st.no_data += 1
                continue
            if res["outcome"] == "INVALID":
                st.invalid_geometry += 1
            row = {"schema_version": SCHEMA_VERSION, "kind": "outcome", "candidate_id": r["candidate_id"],
                   "symbol": sym, "direction": "LONG" if long_ else "SHORT", "snapshot_ts": r.get("ts"),
                   "market_type": r.get("market_type"), "tf": tf, "horizon_h": int(horizon_h),
                   "cost_r": float(cost_r), "entry_ref_kind": "MARKET_OPEN_AT_TS",
                   "plan_entry": _f(r.get("entry_price")), "stop": r["_stop"], "target": r["_tgt"],
                   "source": getattr(provider, "name", type(provider).__name__), "run_id": run_id,
                   "labeled_at": labeled_at, **res,
                   "resolved_at": _iso(int(res["resolved_at_ms"])) if res.get("resolved_at_ms") else None}
            if outcomes.append(row):
                st.labeled += 1
                if res["label"] == 1:
                    st.wins += 1
                elif res["label"] == 0:
                    st.losses += 1
                elif res["outcome"] == OUTCOME_TIMEOUT:
                    st.timeouts += 1
            else:
                st.write_failed += 1
    return st


def summarize(outcomes: OutcomeStore) -> dict:
    """Etiketli kümenin özeti (panel/rapor için). Zaman aşımları ayrı; ikili oran yalnız çözülenlerde."""
    n = wins = losses = timeouts = invalid = 0
    r_sum = 0.0
    r_n = 0
    for r in outcomes.iter_rows():
        n += 1
        oc = r.get("outcome")
        if oc == OUTCOME_TARGET:
            wins += 1
        elif oc == OUTCOME_STOP:
            losses += 1
        elif oc == OUTCOME_TIMEOUT:
            timeouts += 1
        else:
            invalid += 1
        v = _f(r.get("r_net"))
        if v is not None and oc in (OUTCOME_TARGET, OUTCOME_STOP, OUTCOME_TIMEOUT):
            r_sum += v
            r_n += 1
    resolved = wins + losses
    return {"n": n, "wins": wins, "losses": losses, "timeouts": timeouts, "invalid": invalid,
            "win_rate_resolved": round(wins / resolved, 4) if resolved else None,
            "mean_r_net": round(r_sum / r_n, 4) if r_n else None, "n_r": r_n}


__all__ = ["LabelStats", "OUTCOME_STOP", "OUTCOME_TARGET", "OUTCOME_TIMEOUT", "OutcomeStore", "SCHEMA_VERSION",
           "frame_to_bars", "label_pending", "summarize", "triple_barrier"]

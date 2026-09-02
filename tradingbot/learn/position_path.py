"""Açık pozisyon fiyat yolu (`position_path_v1`) — kâr geri verme ölçümünün TEK veri temeli.

Neden gerekli: bugün bir pozisyonun ne kadar lehe gidip ne kadarını geri verdiğini SONRADAN
öğrenmenin yolu yok. Defter yalnız `mfe_pct`/`mae_pct` uç değerlerini tutar; hangi anda, hangi
sırayla, hangi stop/hedef durumundayken oraya gidildiği kaydedilmez. Bu yüzden bir çıkış
politikasını geçmişe dönük değerlendirmek imkânsızdı.

**Yeni veri kaynağı YOK.** Snapshot yalnız motorun ZATEN aldığı mark güncellemelerinden üretilir:

* `tour()` (15 dk) — zengin tick: 1h bar yüksek/düşük uçları dahil.
* `exit_check()` (60 sn) — yalnız son fiyat; bar uçları YOKTUR ve varmış gibi davranılmaz.

Bu ayrım `tick_kind` alanında açıkça taşınır: `bar_extremes` mi yoksa `last_only` mi olduğu
kaydedilir, çünkü bar uçları olmadan hesaplanan MFE gerçek en iyi noktayı KAÇIRABİLİR.

Değişmezler:
* Append-only, atomik satır yazımı; defter DEĞİŞTİRİLMEZ.
* Snapshot kimliği deterministik → restart sonrası duplicate YOK.
* Zaman damgası monotonik; geriye giden ya da gelecek zaman REDDEDİLİR.
* Sonlu olmayan / bayat / sıfır-altı mark REDDEDİLİR.
* LONG ve SHORT hesapları simetriktir (tek `sign` çarpanı).
* Eski kapanışların `mfe_pct`/`mae_pct` özetinden SAHTE yol üretilmez.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core import from_iso, iso, stable_id, utc_now

log = logging.getLogger(__name__)

SCHEMA_VERSION = "position_path_v1"

#: Tick kalitesi — bar uçları var mı yok mu. Yol değerlendirmesi bunu bilmek ZORUNDA.
TICK_BAR_EXTREMES = "bar_extremes"
TICK_LAST_ONLY = "last_only"

#: Reddetme nedenleri (sessiz atlama YOK; sayaç tutulur).
REJ_NON_FINITE = "NON_FINITE_MARK"
REJ_NONPOSITIVE = "NONPOSITIVE_MARK"
REJ_FUTURE_TS = "FUTURE_TIMESTAMP"
REJ_BACKWARD_TS = "BACKWARD_TIMESTAMP"
REJ_STALE = "STALE_MARK"
REJ_NO_ENTRY = "NO_ENTRY_PRICE"
REJ_DUPLICATE = "DUPLICATE_SNAPSHOT"

#: Gelecek zaman toleransı (saat kayması için küçük pay).
FUTURE_TOLERANCE_S = 120.0
#: Varsayılan bayatlık tavanı — bundan eski bir mark ile yol yazılmaz.
DEFAULT_MAX_MARK_AGE_S = 900.0


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _s(x: Any) -> str | None:
    if x is None:
        return None
    t = str(getattr(x, "value", x))
    return t or None


def side_sign(side: Any) -> float:
    """LONG → +1, SHORT → −1. Bütün R hesapları TEK bu çarpandan geçer (simetri garantisi)."""
    return -1.0 if str(_s(side) or "").upper().endswith("SHORT") else 1.0


def r_of(price: Any, *, entry: Any, initial_stop: Any, side: Any) -> float | None:
    """Fiyatın R karşılığı. Stop mesafesi ölçülemezse `None` (sahte 0 YOK)."""
    p, e, s0 = _f(price), _f(entry), _f(initial_stop)
    if p is None or e is None or s0 is None:
        return None
    dist = abs(e - s0)
    if dist <= 0:
        return None
    return (p - e) * side_sign(side) / dist


def snapshot_id(trade_id: Any, ts: Any, mark: Any) -> str:
    """Deterministik kimlik — aynı (işlem, an, fiyat) hep aynı id. Restart duplicate ÜRETMEZ."""
    return stable_id("ppath", str(trade_id), str(ts), format(_f(mark) or 0.0, ".10g"))


def build_snapshot(*, position: Any, mark: Any, now=None, tick_kind: str = TICK_LAST_ONLY,
                   decision: Any = None, run_id: str | None = None,
                   code_sha: str | None = None, config_hash: str | None = None,
                   mark_ts: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    """Tek pozisyon için yol snapshot'ı. Dönen: (kayıt, red_nedeni). İkisinden biri `None`.

    Ekonomi alanları KOPYALANMAZ: yalnız değerlendirilip değerlendirilmediği taşınır. Sayı
    uydurma bu modülün işi değildir (bkz. `position_mgmt.management_snapshot`).
    """
    from .position_mgmt import UNKNOWN, economics_available, proposed_action

    px = _f(mark)
    if px is None:
        return None, REJ_NON_FINITE
    if px <= 0:
        return None, REJ_NONPOSITIVE
    now = now or utc_now()
    entry = _f(getattr(position, "entry_avg", None))
    if entry is None or entry <= 0:
        return None, REJ_NO_ENTRY
    if mark_ts:
        try:
            t = from_iso(str(mark_ts))
            age = (now - t).total_seconds()
            if age < -FUTURE_TOLERANCE_S:
                return None, REJ_FUTURE_TS
            if age > DEFAULT_MAX_MARK_AGE_S:
                return None, REJ_STALE
        except (ValueError, TypeError):
            pass
    side = _s(getattr(position, "side", None))
    stop0 = _f(getattr(position, "initial_stop", None))
    cur_stop = _f(getattr(position, "stop", None))
    qty = _f(getattr(position, "qty", None))
    qty0 = _f(getattr(position, "initial_qty", None))
    mfe_pct, mae_pct = _f(getattr(position, "mfe_pct", None)), _f(getattr(position, "mae_pct", None))
    dist = abs(entry - stop0) if stop0 is not None else None
    sign = side_sign(side)
    cur_r = ((px - entry) * sign / dist) if dist else None
    mfe_r = (abs(mfe_pct) / 100.0 * entry / dist) if (dist and mfe_pct is not None) else None
    mae_r = (-abs(mae_pct) / 100.0 * entry / dist) if (dist and mae_pct is not None) else None
    giveback = (mfe_r - cur_r) if (mfe_r is not None and cur_r is not None) else None
    if mfe_r is None or cur_r is None:
        capture, cap_state = None, "NOT_MEASURABLE"
    elif mfe_r <= 1e-9:
        capture, cap_state = None, "NO_FAVORABLE_EXCURSION"
    else:
        capture, cap_state = round(cur_r / mfe_r, 6), "OK"
    # Başlangıç riski: adet ölçülebiliyorsa GERÇEK USDT karşılığı, yoksa None.
    init_risk = (dist * qty0) if (dist and qty0) else None
    fees = _f(getattr(position, "fees_paid", None))
    f_paid = _f(getattr(position, "funding_paid", None)) or 0.0
    f_recv = _f(getattr(position, "funding_received", None)) or 0.0
    funding_net = f_recv - f_paid
    risk_usdt = init_risk
    tid = _s(getattr(position, "id", None)) or ""
    ts = iso(now)
    evaluated = economics_available(decision)
    action, reason = proposed_action(decision, position)
    rec = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id(tid, ts, px),
        "trade_id": tid,
        "ts": ts,
        "ts_ms": int(now.timestamp() * 1000),
        "run_id": run_id,
        "symbol": _s(getattr(position, "symbol", None)),
        "side": side,
        "tick_kind": tick_kind,
        "entry": entry,
        "mark": px,
        "qty": qty,
        "initial_qty": qty0,
        "remaining_fraction": (qty / qty0) if (qty is not None and qty0) else None,
        "initial_stop": stop0,
        "current_stop": cur_stop,
        "stop_distance_pct": round(dist / entry * 100.0, 6) if dist else None,
        "targets": [t for t in (_f(x) for x in (getattr(position, "targets", None) or [])) if t is not None][:6],
        "targets_hit": getattr(position, "targets_hit", None),
        "tp1_done": bool(getattr(position, "tp1_done", False)),
        "leverage": _f(getattr(position, "leverage", None)),
        "initial_risk_usdt": round(init_risk, 8) if init_risk is not None else None,
        "gross_r": round(cur_r, 6) if cur_r is not None else None,
        # Net R: maliyetler başlangıç riskine bölünür. Risk ölçülemezse None (sessiz 0 YOK).
        "net_r": (round(cur_r - ((fees or 0.0) - funding_net) / risk_usdt, 6)
                  if (cur_r is not None and risk_usdt) else None),
        "mfe_r": round(mfe_r, 6) if mfe_r is not None else None,
        "mae_r": round(mae_r, 6) if mae_r is not None else None,
        "giveback_r": round(giveback, 6) if giveback is not None else None,
        "capture_ratio": capture,
        "capture_ratio_state": cap_state,
        "fees_paid": fees,
        "funding_net": round(funding_net, 8),
        "fee_drag_r": round(fees / risk_usdt, 6) if (risk_usdt and fees is not None) else None,
        "funding_drag_r": round(-funding_net / risk_usdt, 6) if risk_usdt else None,
        "position_age_hours": None,
        "bars_held": getattr(position, "bars_held", None),
        "regime": _s(getattr(decision, "regime", None)),
        "consensus_score": _f(getattr(decision, "consensus_score", None)),
        "consensus_confidence": _f(getattr(decision, "consensus_confidence", None)),
        "coinhead_action": action,
        "coinhead_reason": reason,
        "economics_evaluated": bool(evaluated),
        "p_win": (_f(getattr(decision, "p_win", None)) if evaluated else UNKNOWN),
        "code_sha": code_sha,
        "config_hash": config_hash,
    }
    opened = _s(getattr(position, "opened_at", None))
    if opened:
        try:
            rec["position_age_hours"] = round(
                max(0.0, (now - from_iso(opened)).total_seconds()) / 3600.0, 4)
        except (ValueError, TypeError):
            pass
        rec["opened_at"] = opened
    return rec, None


class PositionPathStore:
    """Append-only yol deposu. Arıza çağıranı ÇÖKERTMEZ; sayaç tutulur.

    Monotoniklik ve tekillik `trade_id` bazında bellekte izlenir ve dosyadan yeniden kurulur.
    """

    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    @classmethod
    def _lock_for(cls, path: Path) -> threading.Lock:
        key = str(path)
        with cls._locks_guard:
            lk = cls._locks.get(key)
            if lk is None:
                lk = cls._locks[key] = threading.Lock()
            return lk

    def __init__(self, path: Path | str, *, min_interval_s: float = 55.0,
                 min_r_change: float = 0.02):
        self.path = Path(path)
        self._lock = self._lock_for(self.path)
        #: Aynı pozisyon için iki snapshot arasındaki asgari süre (60 sn'lik exit-monitor'ü boğmaz).
        self.min_interval_s = float(min_interval_s)
        #: Bu kadar R hareket etmeyen ve yapısal değişiklik taşımayan ara snapshot atlanır.
        self.min_r_change = float(min_r_change)
        self.appended = 0
        self.errors = 0
        self.rejected: dict[str, int] = {}
        self.skipped_unchanged = 0
        self._last: dict[str, dict[str, Any]] | None = None
        self._ids: set[str] | None = None

    # ------------------------------------------------------------------ okuma
    def iter_rows(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("position_path okunamadı: %s", exc)
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("trade_id"):
                yield d

    def paths_by_trade(self) -> dict[str, list[dict[str, Any]]]:
        """`trade_id → kronolojik snapshot listesi`. Duplicate kimlikler tekilleştirilir."""
        out: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for r in self.iter_rows():
            sid = str(r.get("snapshot_id") or "")
            if sid and sid in seen:
                continue
            if sid:
                seen.add(sid)
            out.setdefault(str(r["trade_id"]), []).append(r)
        for rows in out.values():
            rows.sort(key=lambda x: (int(x.get("ts_ms") or 0), str(x.get("snapshot_id") or "")))
        return out

    def _state(self) -> tuple[dict[str, dict[str, Any]], set[str]]:
        if self._last is None or self._ids is None:
            last: dict[str, dict[str, Any]] = {}
            ids: set[str] = set()
            for r in self.iter_rows():
                sid = str(r.get("snapshot_id") or "")
                if sid:
                    ids.add(sid)
                tid = str(r["trade_id"])
                prev = last.get(tid)
                if prev is None or int(r.get("ts_ms") or 0) >= int(prev.get("ts_ms") or 0):
                    last[tid] = r
            self._last, self._ids = last, ids
        return self._last, self._ids

    def last_for(self, trade_id: str) -> dict[str, Any] | None:
        return self._state()[0].get(str(trade_id))

    # ------------------------------------------------------------------ yazım
    def _material_change(self, rec: dict[str, Any], prev: dict[str, Any] | None) -> bool:
        """Ara snapshot yazmaya değer mi. İlk snapshot ve yapısal değişiklikler DAİMA yazılır."""
        if prev is None:
            return True
        for k in ("current_stop", "targets_hit", "tp1_done", "qty"):
            if rec.get(k) != prev.get(k):
                return True
        # Bar uçlu tick, yalnız-son-fiyat tickten daha iyi bilgidir: onu atlama.
        if rec.get("tick_kind") == TICK_BAR_EXTREMES and prev.get("tick_kind") != TICK_BAR_EXTREMES:
            return True
        for k in ("mfe_r", "mae_r"):                      # yeni uç nokta bilgi taşır
            a, b = rec.get(k), prev.get(k)
            if a is not None and b is not None and abs(a - b) > 1e-9:
                return True
        dt = (int(rec.get("ts_ms") or 0) - int(prev.get("ts_ms") or 0)) / 1000.0
        if dt < self.min_interval_s:
            return False
        a, b = rec.get("gross_r"), prev.get("gross_r")
        if a is None or b is None:
            return True
        return abs(a - b) >= self.min_r_change

    def append(self, rec: dict[str, Any], *, force: bool = False) -> bool:
        """Snapshot ekler. Duplicate, geriye giden zaman ve önemsiz ara adım REDDEDİLİR."""
        tid = str((rec or {}).get("trade_id") or "")
        sid = str((rec or {}).get("snapshot_id") or "")
        if not tid or not sid:
            self._bump(REJ_NO_ENTRY)
            return False
        with self._lock:
            last, ids = self._state()
            if sid in ids:
                self._bump(REJ_DUPLICATE)
                return False
            prev = last.get(tid)
            if prev is not None and int(rec.get("ts_ms") or 0) < int(prev.get("ts_ms") or 0):
                self._bump(REJ_BACKWARD_TS)          # zaman DAİMA ileri akar
                return False
            if not force and not self._material_change(rec, prev):
                self.skipped_unchanged += 1
                return False
            try:
                line = json.dumps(rec, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.errors += 1
                log.warning("position_path serileştirilemedi (%s): %s", tid, exc)
                return False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
            except OSError as exc:
                self.errors += 1
                log.warning("position_path yazılamadı (%s): %s", tid, exc)
                return False
            ids.add(sid)
            last[tid] = rec
            self.appended += 1
            return True

    def _bump(self, code: str) -> None:
        self.rejected[code] = self.rejected.get(code, 0) + 1

    def stats(self) -> dict[str, Any]:
        by_trade = self.paths_by_trade()
        return {"schema_version": SCHEMA_VERSION, "path": str(self.path),
                "appended": self.appended, "errors": self.errors,
                "rejected": dict(self.rejected), "skipped_unchanged": self.skipped_unchanged,
                "trades_with_path": len(by_trade),
                "total_snapshots": sum(len(v) for v in by_trade.values())}


def path_completeness(rows: list[dict[str, Any]], *, opened_at: Any = None,
                      closed_at: Any = None, min_snapshots: int = 3,
                      max_gap_s: float = 3600.0) -> dict[str, Any]:
    """Bir yolun değerlendirmeye YETERLİ olup olmadığı. Eksikse dürüstçe eksik denir.

    `complete=False` olan bir işlem için challenger sonucu ÜRETİLMEZ (`NO_COMPLETE_PATH`).
    """
    if not rows:
        return {"complete": False, "reason": "NO_PATH", "n_snapshots": 0,
                "covers_open": False, "covers_close": False, "max_gap_s": None}
    rows = sorted(rows, key=lambda x: int(x.get("ts_ms") or 0))
    gaps = [(int(b.get("ts_ms") or 0) - int(a.get("ts_ms") or 0)) / 1000.0
            for a, b in zip(rows, rows[1:])]
    worst = max(gaps) if gaps else 0.0
    covers_open = covers_close = True
    if opened_at:
        try:
            covers_open = (int(rows[0].get("ts_ms") or 0) / 1000.0
                           - from_iso(str(opened_at)).timestamp()) <= max_gap_s
        except (ValueError, TypeError):
            covers_open = False
    if closed_at:
        try:
            covers_close = (from_iso(str(closed_at)).timestamp()
                            - int(rows[-1].get("ts_ms") or 0) / 1000.0) <= max_gap_s
        except (ValueError, TypeError):
            covers_close = False
    reasons = []
    if len(rows) < min_snapshots:
        reasons.append("TOO_FEW_SNAPSHOTS")
    if worst > max_gap_s:
        reasons.append("GAP_TOO_LARGE")
    if not covers_open:
        reasons.append("MISSING_OPEN_COVERAGE")
    if not covers_close:
        reasons.append("MISSING_CLOSE_COVERAGE")
    return {"complete": not reasons, "reason": ",".join(reasons) or "OK",
            "n_snapshots": len(rows), "covers_open": covers_open, "covers_close": covers_close,
            "max_gap_s": round(worst, 1),
            "first_ts": rows[0].get("ts"), "last_ts": rows[-1].get("ts"),
            "tick_kinds": sorted({str(r.get("tick_kind") or "?") for r in rows})}


__all__ = ["SCHEMA_VERSION", "TICK_BAR_EXTREMES", "TICK_LAST_ONLY", "REJ_NON_FINITE",
           "REJ_NONPOSITIVE", "REJ_FUTURE_TS", "REJ_BACKWARD_TS", "REJ_STALE", "REJ_DUPLICATE",
           "DEFAULT_MAX_MARK_AGE_S", "PositionPathStore", "build_snapshot", "snapshot_id",
           "side_sign", "r_of", "path_completeness"]

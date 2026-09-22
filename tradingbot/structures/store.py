# -*- coding: utf-8 -*-
"""YAPI KAYIT DEPOSU — ortak analiz anlık görüntüleri ve beş botun yapı kararları (panel ve motor AYNI kaydı okur).

Düzen (`state/structures/`):
* `latest/<MARKET>_<SEMBOL>_<DİLİM>.json` — o seri için EN SON analiz (analysis_id değişince yazılır).
* `snapshots/<analysis_id>.json` — bir KARARA (giriş/çıkış/iptal/sıkılaştırma/bekleme) dayanak olmuş analiz; DEĞİŞMEZ
  (aynı kimlik ikinci kez yazılmaz). Geçmiş görüntü kendi anını korur: sonraki mumlar bu dosyayı değiştirmez.
* `decisions.jsonl` — bot kararları; aynı (bot, defter, piyasa, sembol) için karar DEĞİŞMEDİKÇE yeni satır yazılmaz
  (her tur aynı "bekle" tekrar edilmez); son hâl `decisions_latest.json`da, son görülme anıyla.
Yazımlar süreç içi kilitle ve atomik dosya değişimiyle yapılır (tarayıcı iş parçacığı ve ana tur aynı depoyu yazar).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, iso, read_json

DIRNAME = "structures"
DECISIONS_FILE = "decisions.jsonl"
DECISIONS_LATEST = "decisions_latest.json"
MAX_DECISION_LINES = 20_000
ACTIONS_WITH_SNAPSHOT = ("ENTER", "EXIT", "TIGHTEN_STOP", "CANCEL", "WAIT", "WAIT_TRIGGER")
_LOCK = threading.RLock()


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))


def series_key(market: str, symbol: str, timeframe: str) -> str:
    return "%s_%s_%s" % (_safe(market), _safe(symbol), _safe(timeframe))


def decision_id(d: dict[str, Any]) -> str:
    raw = "|".join(str(d.get(k)) for k in ("bot", "book_id", "market", "symbol", "action", "reason_code")) + "|" + \
        ",".join(d.get("pattern_ids") or [])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class StructureStore:
    def __init__(self, state_dir: Path | str):
        self.root = Path(state_dir) / DIRNAME

    # ------------------------------------------------------------------ analiz
    def save_latest(self, an: dict[str, Any]) -> bool:
        if not an or not an.get("analysis_id"):
            return False
        p = self.root / "latest" / (series_key(an["market"], an["symbol"], an["timeframe"]) + ".json")
        with _LOCK:
            prev = read_json(p, default=None) if p.exists() else None
            if isinstance(prev, dict) and prev.get("analysis_id") == an["analysis_id"]:
                return False
            atomic_write_json(p, an)
        return True

    def save_snapshot(self, an: dict[str, Any]) -> bool:
        if not an or not an.get("analysis_id"):
            return False
        p = self.root / "snapshots" / (str(an["analysis_id"]) + ".json")
        with _LOCK:
            if p.exists():
                return False                          # DEĞİŞMEZ: aynı kimlik yeniden yazılmaz
            atomic_write_json(p, an)
        return True

    def load_snapshot(self, analysis_id: str) -> dict[str, Any] | None:
        p = self.root / "snapshots" / (_safe(analysis_id) + ".json")
        d = read_json(p, default=None) if p.exists() else None
        return d if isinstance(d, dict) else None

    def load_latest(self, market: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
        p = self.root / "latest" / (series_key(market, symbol, timeframe) + ".json")
        d = read_json(p, default=None) if p.exists() else None
        return d if isinstance(d, dict) else None

    # ------------------------------------------------------------------ kararlar
    def record_decision(self, decision: dict[str, Any], *, book_id: str, market: str, symbol: str, at_ms: int,
                        analyses: dict[str, Any] | None = None, trade_id: str | None = None) -> dict[str, Any]:
        """Kararı kaydeder; aynı karar sürüyorsa yalnız `last_seen` güncellenir. Dayanak analizler (varsa) değişmez
        anlık görüntü olarak saklanır. Dönen: yazılan/güncellenen satır."""
        row = dict(decision)
        row.update({"book_id": book_id, "market": market, "symbol": symbol, "at_ms": int(at_ms), "at": iso_ms(at_ms)})
        if trade_id:
            row["trade_id"] = trade_id
        row["decision_id"] = decision_id(row)
        key = "%s|%s|%s" % (book_id, market, symbol)
        with _LOCK:
            latest_p = self.root / DECISIONS_LATEST
            latest = read_json(latest_p, default=None) if latest_p.exists() else None
            latest = latest if isinstance(latest, dict) else {}
            prev = latest.get(key) or {}
            changed = prev.get("decision_id") != row["decision_id"] or bool(trade_id and prev.get("trade_id") != trade_id)
            if changed:
                row["first_seen_ms"] = int(at_ms)
                if row.get("action") in ACTIONS_WITH_SNAPSHOT and analyses:
                    for an in analyses.values():
                        if an:
                            self.save_snapshot(an)
                self._append(row)
            else:
                row["first_seen_ms"] = int(prev.get("first_seen_ms") or at_ms)
            row["last_seen_ms"] = int(at_ms)
            latest[key] = row
            atomic_write_json(latest_p, latest)
        return row

    def _append(self, row: dict[str, Any]) -> None:
        p = self.root / DECISIONS_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        try:
            if p.stat().st_size > 60_000_000:
                lines = p.read_text(encoding="utf-8").splitlines()[-MAX_DECISION_LINES:]
                tmp = p.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(p)
        except OSError:
            pass

    def latest_decisions(self) -> dict[str, Any]:
        p = self.root / DECISIONS_LATEST
        d = read_json(p, default=None) if p.exists() else None
        return d if isinstance(d, dict) else {}

    def decisions(self, *, symbol: str | None = None, book_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        p = self.root / DECISIONS_FILE
        if not p.exists():
            return []
        out = []
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for ln in reversed(lines):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if symbol and d.get("symbol") != symbol:
                continue
            if book_id and d.get("book_id") != book_id:
                continue
            out.append(d)
            if len(out) >= int(limit):
                break
        return out[::-1]


def iso_ms(ms: Any) -> str | None:
    try:
        from datetime import datetime, timezone
        return iso(datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


__all__ = ["DIRNAME", "StructureStore", "decision_id", "iso_ms", "series_key"]

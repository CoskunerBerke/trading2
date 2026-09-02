"""Kapanış zinciri uzlaştırması (`learning_reconcile_v1`) — eksik outcome/ders adımlarını tamamlar.

İki çağırma yolu vardır ve İKİSİ DE aynı planlayıcıyı kullanır:

* `python -m tradingbot learning-reconcile --dry-run|--apply` (operatör, tek seferlik onarım)
* `TradingEngineV3.tour()` içindeki tur sonu tamamlama (kendiliğinden, her turda)

Güvenlik sözleşmesi:
* DEFTER SALT OKUNUR. Bu modül `futures_ledger.json`'a asla yazmaz; ekonomi yeniden hesaplanmaz.
  Ders üretimi kapanış kaydının KENDİ sayılarını kullanır.
* Mevcut tarihçe YENİDEN YAZILMAZ; yalnız EKSİK adım eklenir.
* İdempotent: ikinci çalıştırma sıfır değişiklik üretir.
* `--apply` bir audit manifest'i döndürür.
* Tek işlemden politika/terfi ÇIKMAZ; `Learner.learn()` gözlem + hipotez üretir.

**İdempotency neden ayrı bir indekse bağlandı:** "ders var mı" sorusunu ders listesine bakarak
cevaplamak güvenli DEĞİLDİR. Sıcak pencere (`lesson_hot_window`, varsayılan 200) dolduğunda
dersler arşiv segmentlerine döner ve `LessonStore` numaralandırma API'si sunmaz (`query`
retrieval içindir, tam tarama değil). O noktada arşivlenmiş bir ders "eksik" görünür ve İKİNCİ
kez üretilirdi. Bu yüzden tamamlanan her kapanış olayı `learned_closes.jsonl` içine yazılır;
idempotency çıkarım değil, KAYITTIR.

Neden gerekli (ölçüldü, varsayılmadı): `engine_v3.tour()` sırası `ledger2.save()` -> öğrenme
şeklindedir. İki adım arasında süreç ölürse kapanış defterde kalıcıdır, fakat `ledger2.tick()`
onu bir daha döndürmez ve o işlem KALICI OLARAK öğrenilmemiş kalır. Bu modül farkı kapatır.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core import iso, utc_now
from .close_chain import (SCHEMA_VERSION as CHAIN_SCHEMA, canonical_closes, chain_report,
                          close_event_id, pending_work)
from .provenance import ProvenanceStore, legacy_provenance

log = logging.getLogger(__name__)

SCHEMA_VERSION = "learning_reconcile_v1"
INDEX_SCHEMA_VERSION = "learned_closes_v1"

#: `--apply` sırasında tek turda tamamlanacak azami kapanış. Onarım turu worker'ı KİLİTLEMEZ.
DEFAULT_MAX_APPLY = 200

#: Sıcak ders penceresinden türetilen ilk indeks kayıtlarının kaynağı (dürüst köken beyanı).
BOOTSTRAP = "BOOTSTRAP_FROM_EXISTING_LESSONS"
LIVE_TOUR = "LIVE_TOUR"
RECONCILE = "RECONCILE"


class LearnedIndex:
    """Öğrenilmiş kapanış olaylarının append-only indeksi — TEK idempotency otoritesi.

    Bir kapanış bu indekste ise ders ÜRETİLMEZ. İndeks türev veridir: silinirse sıcak ders
    penceresinden yeniden bootstrap edilir (kayıp değil, yalnız kapsam daralması).
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

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = self._lock_for(self.path)
        self.errors = 0
        self.appended = 0

    def load(self) -> dict[str, dict[str, Any]]:
        """`close_event_id -> kayıt`. Bozuk satır atlanır; istisna sızmaz."""
        out: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return out
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("learned index okunamadı: %s", exc)
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = str((d or {}).get("close_event_id") or "")
            if ev and ev not in out:
                out[ev] = d
        return out

    def event_ids(self) -> set[str]:
        return set(self.load().keys())

    def trade_ids(self) -> set[str]:
        return {str(v.get("trade_id")) for v in self.load().values() if v.get("trade_id")}

    def record(self, *, close_ev: str, trade_id: str, steps: Iterable[str],
               source: str, lesson_id: str | None = None,
               r_multiple: float | None = None) -> bool:
        """Kaydı ekler. Aynı olay zaten varsa hiçbir şey yapmaz ve False döner."""
        if not close_ev:
            return False
        with self._lock:
            if close_ev in self.event_ids():
                return False
            row = {"schema_version": INDEX_SCHEMA_VERSION, "close_event_id": close_ev,
                   "trade_id": str(trade_id), "learned_at": iso(utc_now()),
                   "steps": [str(s) for s in steps], "source": str(source),
                   "lesson_id": lesson_id, "r_multiple": r_multiple}
            try:
                line = json.dumps(row, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.errors += 1
                log.warning("learned index serileştirilemedi: %s", exc)
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
                log.warning("learned index yazılamadı: %s", exc)
                return False
            self.appended += 1
            return True


def _hot_lesson_counts(learner: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for les in (getattr(getattr(learner, "state", None), "lessons", None) or []):
        lid = str((les or {}).get("id") or "")
        if lid:
            counts[lid] = counts.get(lid, 0) + 1
    return counts


def bootstrap_index(*, history: Any, learner: Any, index: LearnedIndex,
                    memory_exit_ids: set[str] | None = None) -> int:
    """Mevcut dersleri indekse taşır — özellik açılmadan ÖNCE öğrenilmiş kapanışlar.

    Yalnız GERÇEKTEN dersi olan kapanışlar işaretlenir; ders yoksa kapanış "eksik" kalır ve
    reconcile onu tamamlar. Uydurma yok.
    """
    hot = set(_hot_lesson_counts(learner))
    exits = memory_exit_ids or set()
    n = 0
    for c in canonical_closes(history):
        tid = c["trade_id"]
        if tid not in hot:
            continue
        steps = ["lesson"] + (["outcome"] if tid in exits else [])
        if index.record(close_ev=c["close_event_id"], trade_id=tid, steps=steps,
                        source=BOOTSTRAP, r_multiple=c.get("r_multiple")):
            n += 1
    return n


def _memory_exit_ids(memory: Any) -> set[str]:
    out: set[str] = set()
    try:
        for row in memory.iter_rows():
            if row.get("kind") == "exit" and row.get("trade_id"):
                out.add(str(row["trade_id"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("trade_memory okunamadı: %s", exc)
    return out


def build_plan(*, history: Any, memory: Any, learner: Any,
               index: LearnedIndex | None = None,
               provenance_store: ProvenanceStore | None = None) -> dict[str, Any]:
    """SALT OKUNUR plan. Hiçbir dosyaya yazmaz; ne yapılacağını sayılarla anlatır."""
    closes = canonical_closes(history)
    outcome_ids = _memory_exit_ids(memory)
    hot_counts = _hot_lesson_counts(learner)
    learned_tids = index.trade_ids() if index is not None else set()
    # Bir kapanışın dersi VARDIR eğer: indekste kayıtlı ya da sıcak pencerede görünüyor.
    lesson_ids = learned_tids | set(hot_counts)
    prov = provenance_store.load() if provenance_store is not None else {}
    rep = chain_report(closes, outcome_ids=outcome_ids, lesson_ids=lesson_ids,
                       provenance=prov, lesson_id_counts=hot_counts)
    rep["learned_index_records"] = len(learned_tids)
    rep["hot_lessons"] = sum(hot_counts.values())
    work = pending_work(rep)
    files: list[str] = []
    if any("outcome" in w["missing_steps"] for w in work):
        files.append(str(getattr(memory, "path", "trade_memory.jsonl")))
    if any("lesson" in w["missing_steps"] for w in work):
        files.append(str(getattr(learner, "path", "learning.json")))
    unindexed = [c for c in closes if c["close_event_id"] not in (
        index.event_ids() if index is not None else set())]
    if index is not None and unindexed:
        files.append(str(index.path))
    unprov = [c for c in closes if c["trade_id"] not in prov]
    if provenance_store is not None and unprov:
        files.append(str(provenance_store.path))
    return {
        "schema_version": SCHEMA_VERSION,
        "chain_schema": CHAIN_SCHEMA,
        "generated_at": iso(utc_now()),
        "report": rep,
        "pending": work,
        "will_add_outcomes": sum(1 for w in work if "outcome" in w["missing_steps"]),
        "will_add_lessons": sum(1 for w in work if "lesson" in w["missing_steps"]),
        "will_index": len(unindexed),
        "will_mark_legacy": len(unprov),
        "files_to_change": sorted(set(files)),
        "ledger_written": False,
        "note_tr": ("Defter SALT OKUNUR; yalnız eksik outcome/ders eklenir. "
                    "İkinci çalıştırma sıfır değişiklik üretmelidir."),
    }


def apply_plan(plan: dict[str, Any], *, history: Any, memory: Any, learner: Any,
               index: LearnedIndex | None = None,
               provenance_store: ProvenanceStore | None = None,
               journal_outcome: Callable[[dict, dict | None], None] | None = None,
               max_apply: int = DEFAULT_MAX_APPLY) -> dict[str, Any]:
    """Eksik adımları tamamlar ve audit manifest'i döndürür.

    Ders üretimi `Learner.learn()` üzerinden yapılır: kalibrasyon, ajan ağırlıkları ve
    istatistikler ÜRETİM YOLUYLA AYNI koddan geçer. Ayrı bir "onarım öğrenmesi" yoktur,
    çünkü iki farklı öğrenme yolu iki farklı sonuç üretirdi.
    """
    by_tid = {c["trade_id"]: c for c in canonical_closes(history)}
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    added_out = added_les = added_prov = added_idx = 0
    for w in (plan.get("pending") or [])[:max_apply]:
        tid = w["trade_id"]
        close = by_tid.get(tid)
        if close is None:                    # plan ile defter arasında yarış: sessizce geçme
            errors.append({"trade_id": tid, "error": "CLOSE_NOT_IN_LEDGER"})
            continue
        legacy = dict(close.get("raw") or {})
        legacy.setdefault("id", tid)
        steps: list[str] = []
        if "outcome" in w["missing_steps"]:
            try:
                memory.record_exit(tid, legacy, price_path=[], postmortem={
                    "source": SCHEMA_VERSION, "reason": "CHAIN_RECONCILE",
                    "close_event_id": close["close_event_id"]})
                added_out += 1
                steps.append("outcome")
            except Exception as exc:  # noqa: BLE001
                errors.append({"trade_id": tid, "step": "outcome", "error": str(exc)[:200]})
        lesson = None
        if "lesson" in w["missing_steps"]:
            try:
                lesson = learner.learn(legacy)
                added_les += 1
                steps.append("lesson")
            except Exception as exc:  # noqa: BLE001
                errors.append({"trade_id": tid, "step": "lesson", "error": str(exc)[:200]})
        if provenance_store is not None and tid not in provenance_store.known_ids():
            if provenance_store.record(legacy_provenance(
                    trade_id=tid, symbol=close.get("symbol"), direction=close.get("side"),
                    opened_at=close.get("opened_at"))):
                added_prov += 1
                steps.append("legacy_provenance")
        if journal_outcome is not None and steps:
            try:
                journal_outcome(legacy, lesson)
            except Exception as exc:  # noqa: BLE001 — günlük arızası onarımı geçersiz KILMAZ
                errors.append({"trade_id": tid, "step": "journal", "error": str(exc)[:200]})
        if index is not None and steps:
            if index.record(close_ev=close["close_event_id"], trade_id=tid, steps=steps,
                            source=RECONCILE, lesson_id=(lesson or {}).get("id"),
                            r_multiple=close.get("r_multiple")):
                added_idx += 1
        if steps:
            applied.append({"trade_id": tid, "close_event_id": close["close_event_id"],
                            "steps": steps, "r_multiple": close.get("r_multiple"),
                            "net_pnl": close.get("net_pnl")})
    # Kalan kapanışlar için dürüst legacy işareti ve indeks kaydı bırak (bağlantı UYDURULMAZ).
    for tid, close in by_tid.items():
        if provenance_store is not None and tid not in provenance_store.known_ids():
            if provenance_store.record(legacy_provenance(
                    trade_id=tid, symbol=close.get("symbol"), direction=close.get("side"),
                    opened_at=close.get("opened_at"))):
                added_prov += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "applied_at": iso(utc_now()),
        "outcomes_added": added_out,
        "lessons_added": added_les,
        "legacy_provenance_added": added_prov,
        "indexed": added_idx,
        "trades": applied,
        "errors": errors,
        "ledger_written": False,
        "note_tr": "Defter değiştirilmedi; yalnız eksik öğrenme adımları tamamlandı.",
    }


def complete_missing_chain(*, history: Any, memory: Any, learner: Any,
                           index: LearnedIndex | None = None,
                           provenance_store: ProvenanceStore | None = None,
                           journal_outcome: Callable[[dict, dict | None], None] | None = None,
                           max_apply: int = DEFAULT_MAX_APPLY) -> dict[str, Any]:
    """Tur sonu tamamlama: plan üret, boşsa hiçbir şey yapma, doluysa uygula.

    `engine_v3.tour()` bunu ÖĞRENME DÖNGÜSÜNDEN SONRA çağırır. Normal işleyişte plan boştur
    ve maliyeti yalnız birkaç küme karşılaştırmasıdır; yalnız crash penceresi gerçekleştiğinde
    iş yapar.
    """
    if index is not None and not index.event_ids():
        bootstrap_index(history=history, learner=learner, index=index,
                        memory_exit_ids=_memory_exit_ids(memory))
    plan = build_plan(history=history, memory=memory, learner=learner, index=index,
                      provenance_store=provenance_store)
    if not plan.get("pending") and not plan.get("will_mark_legacy"):
        return {"schema_version": SCHEMA_VERSION, "ran": False, "outcomes_added": 0,
                "lessons_added": 0, "legacy_provenance_added": 0, "indexed": 0,
                "report": plan.get("report"), "trades": [], "errors": []}
    res = apply_plan(plan, history=history, memory=memory, learner=learner, index=index,
                     provenance_store=provenance_store, journal_outcome=journal_outcome,
                     max_apply=max_apply)
    res["ran"] = True
    res["report"] = plan.get("report")
    return res


def note_learned(index: LearnedIndex | None, close: Any, lesson: dict | None,
                 *, source: str = LIVE_TOUR) -> bool:
    """Canlı tur öğrenmesini indekse yazar — sonraki turda AYNI kapanış tekrar öğrenilmesin.

    `close` ham kapanış sözlüğü ya da `TradeRecord` olabilir.
    """
    if index is None:
        return False
    d = close if isinstance(close, dict) else (close.to_dict() if hasattr(close, "to_dict") else {})
    tid = str(d.get("id") or d.get("trade_id") or "")
    if not tid:
        return False
    ev = close_event_id(tid, d.get("closed_at"), d.get("exit_reason"))
    r = d.get("r_multiple")
    try:
        r = float(r) if r is not None else None
    except (TypeError, ValueError):
        r = None
    return index.record(close_ev=ev, trade_id=tid, steps=["outcome", "lesson"], source=source,
                        lesson_id=(lesson or {}).get("id"), r_multiple=r)


__all__ = ["SCHEMA_VERSION", "INDEX_SCHEMA_VERSION", "DEFAULT_MAX_APPLY", "BOOTSTRAP",
           "LIVE_TOUR", "RECONCILE", "LearnedIndex", "bootstrap_index", "build_plan",
           "apply_plan", "complete_missing_chain", "note_learned"]

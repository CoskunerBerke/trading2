"""Ders yaşam döngüsü + KAYIPSIZ saklama.

Neden: `learning.py` her kapanışta `s.lessons = s.lessons[-200:]` yapıyordu. 200. dersten sonra
HER yeni ders bir eskisini KALICI olarak siliyordu; dashboard da bunu "kayıt defteri en fazla
200 ders tutar" diye yazıyordu. Ayrıntılı ders metni, kanıt kodları, ajan katkıları geri
getirilemez biçimde yok oluyordu.

Yeni sözleşme (`journal_archive.SegmentArchive` üstüne kurulur):

    ders üretici (learning.py::_diagnose)
    → sıcak pencere (learning.json → `lessons`, varsayılan 200)
    → atomik mühürlenmiş segment (.jsonl.gz + sha256)
    → manifest + ders indeksi (bağlam anahtarı → segment)
    → sınırlı toplam (aggregate) sayaçları
    → retrieval (HOT / INDEXED / AGGREGATE)
    → dashboard

Değişmezler:

* Arşiv yazımı başarısız ya da checksum tutmuyorsa sıcak pencere **BUDANMAZ** (fail-closed).
  Yani "arşivsiz silme" mümkün değildir; başarısızlıkta sıcak liste büyür, veri kaybolmaz.
* Varsayılan saklama SINIRSIZ (`max_segments=0`).
* Retrieval aday başına TÜM arşivi taramaz: önce sıcak pencere (O(hot)), sonra indeksin
  işaret ettiği EN FAZLA `max_segments_scanned` segment, sonra O(1) toplam sayaçlar.
* Toplam sayaç hücre sayısı sınırlıdır (`max_cells`) — yüksek kardinalite oluşamaz.
* İndeks TÜREV veridir: silinirse arşivden yeniden kurulur; arşiv birincil kayıttır.
* Ders promosyonu GEÇMİŞİ DEĞİŞTİRMEZ — yeni bir durum kaydı eklenir (append-only).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

from ..core import atomic_write_json, iso, new_id, read_json, utc_now
from .edge_execution import (APPLIED_BOUNDED, EVIDENCE_LEVELS, OBSERVATION, REJECTED,
                             RESEARCH_HYPOTHESIS, RETIRED, TERMINAL_LEVELS,
                             VALIDATED_POLICY_CANDIDATE)
from .journal_archive import ArchiveError, SegmentArchive

LESSON_SCHEMA_VERSION = "lesson_v2"
INDEX_SCHEMA_VERSION = "lesson_index_v2"
STREAM_ID = "lesson_book"

#: Sıcak pencere — dashboard ve hızlı erişim içindir, SAKLAMA SINIRI DEĞİLDİR.
DEFAULT_HOT_WINDOW = 200

#: Retrieval kapsamları — dashboard bunları olduğu gibi gösterir.
SCOPE_HOT, SCOPE_INDEXED, SCOPE_AGGREGATE = "HOT", "INDEXED", "AGGREGATE"

#: Politika durumu (ders yaşam döngüsü) — `edge_execution` kanıt seviyeleriyle aynı sözlük.
POLICY_STATES = EVIDENCE_LEVELS + TERMINAL_LEVELS

#: İzin verilen durum geçişleri — atlamalı terfi ve sessiz geri alma YOK.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    OBSERVATION: (RESEARCH_HYPOTHESIS, REJECTED, RETIRED),
    RESEARCH_HYPOTHESIS: (VALIDATED_POLICY_CANDIDATE, REJECTED, RETIRED),
    VALIDATED_POLICY_CANDIDATE: (APPLIED_BOUNDED, REJECTED, RETIRED),
    APPLIED_BOUNDED: (RETIRED, REJECTED),
    REJECTED: (RETIRED,),
    RETIRED: (),
}

#: Toplam sayaç hücre tavanı — sınırsız bağlam anahtarı üretilemez.
DEFAULT_MAX_CELLS = 20_000
#: Bir sorguda okunacak azami segment — O(total archive) tarama yasak.
DEFAULT_MAX_SEGMENTS_SCANNED = 4
#: Bir bağlam anahtarı için indekste TUTULAN azami segment kimliği. İndeks boyutu böylece
#: `hücre × fanout` ile SINIRLANIR; arşiv büyüdükçe indeks büyümez (v1'de segment başına anahtar
#: listesi tutuluyordu ve indeks 100k derste ~11 MB'a çıkıp sorgu p50'sini 129 ms'ye taşıyordu).
DEFAULT_INDEX_FANOUT = 8
#: Asgari mühürleme bloğu. `SegmentArchive.commit()` her çağrıda manifesti BAŞTAN yazar
#: (maliyet O(segment sayısı)); tek tek mühürlemek segment sayısını gereksiz şişirir.
#: Taşma bu eşiğe ulaşana kadar sıcak liste `hot_window`u geçici olarak AŞAR — hiçbir ders
#: silinmez, yalnız mühürleme geciktirilir. `1` yaparsak eski davranış (her taşmada mühürle).
DEFAULT_MIN_ROTATE_BLOCK = 50


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _s(x: Any) -> str:
    return str(x) if x not in (None, "") else "-"


def context_keys(*, symbol: Any, direction: Any, setup: Any, regime: Any) -> list[str]:
    """Bağlam hiyerarşisi — genelden özele. Sabit sayıda anahtar üretir."""
    sym, d, st, rg = _s(symbol), _s(direction), _s(setup), _s(regime)
    return ["G",
            f"D|{d}",
            f"R|{rg}",
            f"S|{st}",
            f"SY|{sym}",
            f"SY|{sym}|D|{d}",
            f"SY|{sym}|S|{st}",
            f"S|{st}|R|{rg}",
            f"SY|{sym}|S|{st}|R|{rg}"]


def build_lesson(*, source_trade_id: Any, symbol: Any, direction: Any, setup: Any, regime: Any,
                 observation: dict[str, Any] | None = None,
                 hypothesis: list[str] | None = None,
                 evidence_level: str = OBSERVATION,
                 n_supporting: int = 1, n_conflicting: int = 0,
                 confidence: float | None = None,
                 calibration_metrics: dict[str, Any] | None = None,
                 agent_contributions: list[dict[str, Any]] | None = None,
                 counterfactuals: dict[str, Any] | None = None,
                 applied_effect: dict[str, Any] | None = None,
                 as_of: Any = None, legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ders kaydı — bölüm 11 sözleşmesi. Tek işlemden gelen ders `OBSERVATION`tır."""
    lvl = evidence_level if evidence_level in POLICY_STATES else OBSERVATION
    now = iso(utc_now())
    doc = {"schema_version": LESSON_SCHEMA_VERSION,
           "lesson_id": new_id("lesson"),
           "source_trade_id": _s(source_trade_id),
           "symbol": _s(symbol), "direction": _s(direction),
           "setup": _s(setup), "regime": _s(regime),
           "observation": observation or {},
           "hypothesis": list(hypothesis or []),
           "evidence_level": lvl,
           "policy_status": lvl,
           "n_supporting": int(n_supporting or 0),
           "n_conflicting": int(n_conflicting or 0),
           "confidence": _f(confidence),
           "calibration_metrics": calibration_metrics or {},
           "agent_contributions": list(agent_contributions or []),
           "counterfactuals": counterfactuals or {},
           "applied_effect": applied_effect or {},
           "created_at": now,
           "as_of": (str(as_of) if as_of else now),
           "causal_claim": False}
    if legacy:
        # Eski `lessons` tablosu (dashboard) bu alanları okur — geriye uyumluluk korunur.
        doc.update({k: v for k, v in legacy.items() if k not in doc})
    return doc


def transition(lesson: dict[str, Any], to_state: str, *, reason: str = "",
               at: Any = None) -> dict[str, Any]:
    """Durum geçişi — GEÇMİŞİ DEĞİŞTİRMEZ; yeni durumu ve geçiş kaydını EKLER.

    İzin verilmeyen geçiş `ValueError` yükseltir (sessiz atlamalı terfi YOK).
    """
    cur = str(lesson.get("policy_status") or lesson.get("evidence_level") or OBSERVATION)
    if to_state not in POLICY_STATES:
        raise ValueError(f"bilinmeyen ders durumu: {to_state}")
    if to_state not in ALLOWED_TRANSITIONS.get(cur, ()):
        raise ValueError(f"izin verilmeyen geçiş: {cur} → {to_state}")
    hist = list(lesson.get("status_history") or [])
    hist.append({"from": cur, "to": to_state, "reason": str(reason)[:300],
                 "at": str(at) if at else iso(utc_now())})
    return {**lesson, "policy_status": to_state, "evidence_level": to_state,
            "status_history": hist}


class LessonStore:
    """Sıcak pencere + kayıpsız arşiv + sınırlı indeks/toplam.

    `hot` listesi ÇAĞIRANIN elindedir (learning.json içindeki `lessons`). `rotate()` taşan
    kısmı arşive mühürler ve YALNIZ başarılı mühürlemeden sonra budanmış listeyi döner.
    """

    def __init__(self, root: Path | str, *, hot_window: int = DEFAULT_HOT_WINDOW,
                 max_segments: int = 0, code_sha: str | None = None,
                 max_cells: int = DEFAULT_MAX_CELLS,
                 max_segments_scanned: int = DEFAULT_MAX_SEGMENTS_SCANNED,
                 min_rotate_block: int = DEFAULT_MIN_ROTATE_BLOCK,
                 index_fanout: int = DEFAULT_INDEX_FANOUT) -> None:
        self.root = Path(root)
        self.hot_window = max(1, int(hot_window))
        self.min_rotate_block = max(1, int(min_rotate_block))
        self.max_cells = int(max_cells)
        self.max_segments_scanned = max(1, int(max_segments_scanned))
        self.index_fanout = max(self.max_segments_scanned, int(index_fanout))
        self.archive = SegmentArchive(self.root, stream_id=STREAM_ID,
                                      record_schema_version=LESSON_SCHEMA_VERSION,
                                      code_sha=code_sha, max_segments=max_segments)
        self.index_path = self.root / "lesson_index.json"
        self.errors = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ indeks
    def _empty_index(self) -> dict[str, Any]:
        return {"schema_version": INDEX_SCHEMA_VERSION, "aggregate": {}, "by_key": {},
                "n_indexed": 0, "n_segments_indexed": 0, "cells_dropped": 0,
                "updated_at": None, "health": "EMPTY"}

    def index(self) -> dict[str, Any]:
        doc = read_json(self.index_path, default=None)
        if not isinstance(doc, dict) or doc.get("schema_version") != INDEX_SCHEMA_VERSION:
            return self._empty_index()
        for k in ("aggregate", "by_key"):
            if not isinstance(doc.get(k), dict):
                doc[k] = {}
        return doc

    def _write_index(self, doc: dict[str, Any]) -> None:
        doc["updated_at"] = iso(utc_now())
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.index_path, doc, keep_backup=True)

    def _index_block(self, doc: dict[str, Any], rows: list[dict[str, Any]], segment_id: str) -> None:
        """Yalnız YENİ mühürlenen bloğu işler — O(block), O(total archive) DEĞİL."""
        agg: dict[str, Any] = doc["aggregate"]
        by_key: dict[str, Any] = doc["by_key"]
        seg_keys: set[str] = set()
        for r in rows:
            keys = context_keys(symbol=r.get("symbol"), direction=r.get("direction"),
                                setup=r.get("setup"), regime=r.get("regime"))
            r_val = _f(r.get("r")) or _f((r.get("observation") or {}).get("realized_r")) or 0.0
            won = bool(r.get("won")) if r.get("won") is not None else (r_val > 0)
            codes = list((r.get("observation") or {}).get("observation_codes") or [])
            for k in keys:
                seg_keys.add(k)
                cell = agg.get(k)
                if cell is None:
                    if len(agg) >= self.max_cells:
                        doc["cells_dropped"] = int(doc.get("cells_dropped") or 0) + 1
                        continue
                    cell = {"n": 0, "wins": 0, "sum_r": 0.0, "codes": {}}
                    agg[k] = cell
                cell["n"] += 1
                cell["wins"] += int(won)
                cell["sum_r"] = round(float(cell["sum_r"]) + r_val, 6)
                for c in codes[:6]:
                    cell["codes"][c] = int(cell["codes"].get(c, 0)) + 1
        # TERS İNDEKS: anahtar → EN YENİ `index_fanout` segment. Segment başına anahtar listesi
        # tutmak indeksi arşivle DOĞRUSAL büyütüyordu; bu yapıda indeks boyutu SABİT kalır.
        for k in seg_keys:
            ids = [x for x in (by_key.get(k) or []) if isinstance(x, str)]
            if segment_id not in ids:
                ids.append(segment_id)
            by_key[k] = ids[-self.index_fanout:]
        doc["n_indexed"] = int(doc.get("n_indexed") or 0) + len(rows)
        doc["n_segments_indexed"] = int(doc.get("n_segments_indexed") or 0) + 1
        doc["health"] = "OK"

    # ------------------------------------------------------------------ döndürme
    def rotate(self, hot: list[dict[str, Any]]) -> dict[str, Any]:
        """Taşan dersleri arşive mühürler. Dönen `hot` YALNIZ mühürleme başarılıysa kısalır.

        Fail-closed: `ArchiveError` ya da beklenmeyen hata → `hot` OLDUĞU GİBİ döner (kayıp yok).
        """
        res: dict[str, Any] = {"archived": 0, "segment_id": None, "hot": hot,
                               "error": None, "recovered": None}
        rows = [x for x in hot if isinstance(x, dict)]
        cut = len(rows) - self.hot_window
        if cut < self.min_rotate_block:
            # Taşma henüz asgari blok kadar değil: mühürleme ERTELENİR, ders SİLİNMEZ.
            return res
        block = rows[:cut]
        try:
            res["recovered"] = self.archive.recover()
            lines = [json.dumps(r, ensure_ascii=False, default=str) for r in block]
            meta = self.archive.seal(lines)
            self.archive.commit(meta)
            doc = self.index()
            self._index_block(doc, block, str(meta.get("segment_id")))
            self._write_index(doc)
            res["archived"] = cut
            res["segment_id"] = meta.get("segment_id")
            res["hot"] = rows[cut:]
        except (ArchiveError, OSError, ValueError, TypeError) as exc:
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            res["error"] = self.last_error
            res["hot"] = rows          # BUDAMA YOK — arşivsiz silme yasak
        return res

    # ------------------------------------------------------------------ retrieval
    def query(self, *, symbol: Any = None, direction: Any = None, setup: Any = None,
              regime: Any = None, hot: Iterable[dict[str, Any]] | None = None,
              k: int = 5, as_of: str | None = None) -> dict[str, Any]:
        """Sınırlı retrieval: HOT örnekler + INDEXED segment örnekleri + AGGREGATE sayaçlar.

        Maliyet: O(hot) + O(`max_segments_scanned` segment) + O(1) toplam. TÜM arşiv TARANMAZ.
        `as_of` verilirse ondan SONRA oluşmuş ders döndürülmez (no-lookahead).
        """
        keys = context_keys(symbol=symbol, direction=direction, setup=setup, regime=regime)
        target = keys[-1] if _s(symbol) != "-" else keys[0]
        scopes: list[str] = []
        out_hot: list[dict[str, Any]] = []
        for r in list(hot or [])[::-1]:
            if not isinstance(r, dict):
                continue
            if as_of and str(r.get("as_of") or r.get("at") or "") > as_of:
                continue
            if symbol and _s(r.get("symbol")) != _s(symbol):
                continue
            out_hot.append(r)
            if len(out_hot) >= k:
                break
        if out_hot:
            scopes.append(SCOPE_HOT)

        doc = self.index()
        exemplars: list[dict[str, Any]] = []
        segs_scanned = 0
        if len(out_hot) < k:
            cand = [x for x in ((doc.get("by_key") or {}).get(target) or []) if isinstance(x, str)]
            for sid in list(reversed(cand))[:self.max_segments_scanned]:
                seg = self.archive.segment_for(sid)
                if seg is None:
                    continue
                rows = self.archive.read_segment(seg)
                segs_scanned += 1
                if not rows:
                    continue
                for r in rows[::-1]:
                    if as_of and str(r.get("as_of") or r.get("at") or "") > as_of:
                        continue
                    if symbol and _s(r.get("symbol")) != _s(symbol):
                        continue
                    exemplars.append(r)
                    if len(exemplars) + len(out_hot) >= k:
                        break
                if len(exemplars) + len(out_hot) >= k:
                    break
            if exemplars:
                scopes.append(SCOPE_INDEXED)

        aggregate = None
        agg_all = doc.get("aggregate") or {}
        for key in reversed(keys):
            cell = agg_all.get(key)
            if isinstance(cell, dict) and int(cell.get("n") or 0) > 0:
                n = int(cell["n"])
                aggregate = {"key": key, "n": n, "wins": int(cell.get("wins") or 0),
                             "win_rate": round(int(cell.get("wins") or 0) / n, 4),
                             "expectancy_r": round(float(cell.get("sum_r") or 0.0) / n, 4),
                             "top_codes": sorted((cell.get("codes") or {}).items(),
                                                 key=lambda kv: -kv[1])[:5]}
                scopes.append(SCOPE_AGGREGATE)
                break
        return {"schema_version": INDEX_SCHEMA_VERSION,
                "hot": out_hot, "indexed": exemplars, "aggregate": aggregate,
                "retrieval_scope": scopes or [SCOPE_HOT],
                "segments_scanned": segs_scanned,
                "max_segments_scanned": self.max_segments_scanned,
                "scanned_whole_archive": False}

    # ------------------------------------------------------------------ durum
    def stats(self, hot_count: int = 0) -> dict[str, Any]:
        arc = self.archive.stats()
        doc = self.index()
        archived = int(arc.get("n_archived_records") or 0)
        health = arc.get("health") or "EMPTY"
        if self.last_error:
            health = "DEGRADED"
        return {"schema_version": INDEX_SCHEMA_VERSION,
                "hot_window": self.hot_window,
                "hot_lessons": int(hot_count),
                "archived_lessons": archived,
                "lifetime_lessons": int(hot_count) + archived,
                "segments": int(arc.get("n_segments") or 0),
                "indexed_lessons": int(doc.get("n_indexed") or 0),
                "indexed_segments": int(doc.get("n_segments_indexed") or 0),
                "index_fanout": self.index_fanout,
                "aggregate_cells": len(doc.get("aggregate") or {}),
                "aggregate_cells_dropped": int(doc.get("cells_dropped") or 0),
                "max_cells": self.max_cells,
                "retention_policy": arc.get("retention_policy"),
                "archive_health": health,
                "archive_errors": self.errors,
                "last_archive_error": self.last_error or arc.get("last_error"),
                "archive_root": arc.get("root"),
                "retrieval_scopes": [SCOPE_HOT, SCOPE_INDEXED, SCOPE_AGGREGATE],
                "deletes_detail_on_overflow": False,
                "note_tr": ("Ekranda son {} ders gösteriliyor. Ömür boyu ayrıntılı dersler "
                            "kayıpsız arşivleniyor. Retrieval kapsamı: HOT / INDEXED / "
                            "AGGREGATE.").format(self.hot_window)}

    def verify(self) -> dict[str, Any]:
        return self.archive.verify()

    def rebuild_index(self) -> dict[str, Any]:
        """İndeksi arşivden yeniden kurar (indeks TÜREV veridir; arşiv birincildir).

        OFFLINE bakım yoludur — sıcak döngüde çağrılmaz.
        """
        doc = self._empty_index()
        for seg in self.archive.segments():
            rows = self.archive.read_segment(seg)
            if rows:
                self._index_block(doc, rows, str(seg.get("segment_id")))
        self._write_index(doc)
        return {"segments": len(self.archive.segments()), "n_indexed": doc["n_indexed"],
                "aggregate_cells": len(doc["aggregate"])}

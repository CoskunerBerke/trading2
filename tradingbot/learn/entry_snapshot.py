"""Giriş adayı point-in-time snapshot'ı (`entry_snapshot_v1`) — seçicilik kanıtının TEK temeli.

Sıralamaya giren HER aday için, karar anında görülebilen alanlarla append-only bir kayıt üretir.
Gelecekten hiçbir veri giremez: fonksiyon yalnız çağrı anındaki karar nesnesini okur ve sonucu
(kapanış R'si, kazandı/kaybetti) GÖRMEZ.

**Neden mevcut karar günlüğü yetmiyor (2026-09-02 üretim ölçümü):**

* `decision_journal.jsonl` içinde `opportunity` / `conservative_net_edge_r` **hiç yok** — oysa
  kabul kararını veren tek ekonomik büyüklük odur (`opportunity.assess`).
* Likidite alanlarının tamamı boş: `spread_pct`, `est_slippage_pct`, `depth_ratio`,
  `liquidity_ok` → 52 ACCEPTED kaydın 0'ında dolu.
* `code_sha`, `config_hash`, `policy_id`, `market_type`, `setup`, `price` → 0/52.
* `stop` özellik sözlüğünde yok, dolayısıyla stop mesafesi türetilemiyor.

Bu snapshot o boşlukları kapatır ve her alanın **kaynağını** (`MEASURED` / `MODELED` /
`DEFAULTED` / `MISSING`) açıkça taşır. Eksik alan sıfır sayılmaz.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core import iso, stable_id, utc_now

log = logging.getLogger(__name__)

SCHEMA_VERSION = "entry_snapshot_v1"

#: Alan kaynağı — "ölçüldü" ile "varsayılan kondu" KARIŞTIRILMAZ.
MEASURED = "MEASURED"        # gerçek piyasa/hesap verisinden ölçüldü
MODELED = "MODELED"          # bir modelin/istatistiğin çıktısı (ör. kalibre p_win)
DEFAULTED = "DEFAULTED"      # örnek yetersizken sabit varsayılana düşüldü
MISSING = "MISSING"          # üretilemedi; UNKNOWN kalır
SOURCES = (MEASURED, MODELED, DEFAULTED, MISSING)

#: Bağlantı durumu. Yalnız `LINKED` terfi kanıtı sayılır.
LINKED = "LINKED"                      # bu özellik tarafından yazılmış gerçek snapshot
LEGACY_MEMORY = "LEGACY_MEMORY"        # yalnız `trade_memory` giriş kaydı var (gözlem, kanıt DEĞİL)
LEGACY_UNLINKED = "LEGACY_UNLINKED"    # hiçbir giriş kaydı yok

#: Snapshot başına taşınacak azami özellik (disk ve tur yükü sınırı).
MAX_FEATURES = 96
MAX_SPECIALISTS = 32


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


def _get(o: Any, k: str, d: Any = None) -> Any:
    if o is None:
        return d
    if isinstance(o, dict):
        return o.get(k, d)
    return getattr(o, k, d)


def candidate_id(run_id: Any, cycle_id: Any, symbol: Any, direction: Any) -> str:
    """Aday kimliği — aynı tur/sembol/yön için DAİMA aynı."""
    return stable_id("cand", str(run_id), str(cycle_id), str(symbol), str(direction))


def decision_id(run_id: Any, cycle_id: Any, symbol: Any, direction: Any) -> str:
    """Karar kimliği — `learn.decision_journal.decision_id_for` ile AYNI türetme.

    İki ayrı kimlik şeması iki ayrı gerçeklik üretirdi; bu yüzden aynı parçalardan hesaplanır.
    """
    return stable_id("dec", str(run_id), str(cycle_id), str(symbol), str(direction))


def _bounded(d: Any, limit: int) -> dict[str, float] | None:
    if not isinstance(d, dict):
        return None
    out: dict[str, float] = {}
    for k in sorted(d.keys(), key=str):
        v = _f(d[k])
        if v is None:
            continue
        out[str(k)[:56]] = v
        if len(out) >= limit:
            break
    return out or None


def build_entry_snapshot(*, run_id: Any, cycle_id: Any, symbol: Any, direction: Any,
                         decision: Any = None, plan: Any = None, brief: Any = None,
                         opportunity: Any = None, chief_permission: Any = None,
                         risk_decision: Any = None, baseline_rank: int | None = None,
                         baseline_accepted: bool | None = None,
                         baseline_reject_reason: Any = None,
                         features: Any = None, specialist_scores: Any = None,
                         code_sha: str | None = None, config_hash: str | None = None,
                         policy_version: str | None = None, now=None) -> dict[str, Any]:
    """Saf fonksiyon: karar anı snapshot'ı. Hiçbir yere yazmaz, sonucu GÖRMEZ.

    Her sayısal alan için `sources[alan]` doldurulur; ölçülemeyen alan `None` kalır ve
    `missing_fields` listesine girer. Sıfıra düşürme YOK.
    """
    now = now or utc_now()
    opp = opportunity if isinstance(opportunity, dict) else (_get(decision, "opportunity") or {})
    opp = opp if isinstance(opp, dict) else {}
    feats = features if isinstance(features, dict) else None
    sources: dict[str, str] = {}
    missing: list[str] = []

    def put(key: str, val: Any, src: str) -> Any:
        v = _f(val) if not isinstance(val, str) else val
        if v is None:
            sources[key] = MISSING
            missing.append(key)
        else:
            sources[key] = src
        return v

    def feat(name: str) -> Any:
        return (feats or {}).get(name)

    stop = _f(_get(plan, "stop"))
    entry_px = _f(_get(plan, "entry"))
    stop_pct = _f(_get(plan, "stop_pct"))
    if stop_pct is None and stop is not None and entry_px:
        stop_pct = abs(entry_px - stop) / entry_px * 100.0

    rec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id(run_id, cycle_id, symbol, direction),
        "decision_id": decision_id(run_id, cycle_id, symbol, direction),
        "ts": iso(now),
        "ts_ms": int(now.timestamp() * 1000),
        "run_id": _s(run_id),
        "cycle_id": _s(cycle_id),
        "symbol": _s(symbol),
        "direction": _s(direction),
        "market_type": _s(_get(decision, "market_type")) or _s(_get(plan, "market_type")),
        "timeframe": _s(_get(decision, "timeframe")) or "4h",
        "setup": _s(_get(plan, "entry_type")),
        "link_status": LINKED,
        "policy_version": policy_version,
        "code_sha": code_sha,
        "config_hash": config_hash,
    }
    # --- karar/olasılık alanları -------------------------------------------------------
    rec["p_win"] = put("p_win", _get(decision, "p_win"), MODELED)
    rec["confidence"] = put("confidence", _get(decision, "consensus_confidence"), MEASURED)
    rec["consensus_score"] = put("consensus_score", _get(decision, "consensus_score"), MEASURED)
    rec["expected_r"] = put("expected_r", _get(decision, "expected_r"), MODELED)
    rec["regime"] = put("regime", _s(_get(decision, "regime")), MEASURED)
    rec["n_dissent"] = put("n_dissent", len(_get(decision, "dissent") or []), MEASURED)
    rec["n_vetoes"] = put("n_vetoes", len(_get(decision, "vetoes") or []), MEASURED)
    # --- ekonomi (kabul kararını VEREN alanlar; günlükte hiç yoktu) ---------------------
    rec["conservative_net_edge_r"] = put("conservative_net_edge_r",
                                         opp.get("conservative_net_edge_r"), MODELED)
    rec["net_expectancy_r"] = put("net_expectancy_r", opp.get("net_expectancy_r"), MODELED)
    rec["gross_expectancy_r"] = put("gross_expectancy_r", opp.get("gross_expectancy_r"), MODELED)
    rec["uncertainty_penalty_r"] = put("uncertainty_penalty_r",
                                       opp.get("uncertainty_penalty_r"), MODELED)
    rec["size_multiplier"] = put("size_multiplier", opp.get("size_multiplier"), MODELED)
    rec["expectancy_basis"] = _s(opp.get("expectancy_basis"))
    # `sample_size` küçükse avg_win/avg_loss VARSAYILANDIR — kaynak buna göre işaretlenir.
    n_samp = _f(opp.get("sample_size"))
    rec["sample_size"] = put("sample_size", n_samp, MEASURED)
    stat_src = DEFAULTED if (n_samp is None or n_samp < 1) else MODELED
    rec["avg_win_r"] = put("avg_win_r", opp.get("avg_win_r"), stat_src)
    rec["avg_loss_r"] = put("avg_loss_r", opp.get("avg_loss_r"), stat_src)
    # --- plan geometrisi ---------------------------------------------------------------
    rec["entry_price"] = put("entry_price", entry_px, MEASURED)
    rec["stop_price"] = put("stop_price", stop, MEASURED)
    rec["stop_distance_pct"] = put("stop_distance_pct", stop_pct, MEASURED)
    rec["planned_notional"] = put("planned_notional", _get(plan, "notional"), MODELED)
    rec["planned_leverage"] = put("planned_leverage", _get(plan, "leverage")
                                  or _get(_get(plan, "size"), "leverage"), MODELED)
    tg = [t for t in (_f(x) for x in (_get(plan, "targets") or [])) if t is not None]
    rec["targets"] = tg[:4] or None
    # --- maliyet ------------------------------------------------------------------------
    rec["expected_cost_pct"] = put("expected_cost_pct",
                                   _get(plan, "expected_cost_pct") or feat("expected_cost_pct"),
                                   MODELED)
    rec["funding_rate"] = put("funding_rate", feat("funding_rate"), MEASURED)
    # --- likidite (üretimde TAMAMEN boştu; UNKNOWN kalması ZORUNLU) ---------------------
    rec["spread_pct"] = put("spread_pct", feat("spread_pct"), MEASURED)
    rec["est_slippage_pct"] = put("est_slippage_pct", feat("est_slippage_pct"), MEASURED)
    rec["depth_ratio"] = put("depth_ratio", feat("depth_ratio"), MEASURED)
    liq = feat("liquidity_ok")
    rec["liquidity_ok"] = bool(liq) if liq is not None else None
    if liq is None:
        sources["liquidity_ok"] = MISSING
        missing.append("liquidity_ok")
    else:
        sources["liquidity_ok"] = MEASURED
    # --- volatilite ---------------------------------------------------------------------
    rec["atr_pct"] = put("atr_pct", feat("atr_pct"), MEASURED)
    rec["bb_width"] = put("bb_width", feat("bb_width"), MEASURED)
    # --- portföy durumu (karar ANINDA) --------------------------------------------------
    rec["portfolio_open_positions"] = put("portfolio_open_positions",
                                          _get(chief_permission, "open_positions"), MEASURED)
    rec["portfolio_open_risk_usdt"] = put("portfolio_open_risk_usdt",
                                          _get(chief_permission, "total_open_risk_usdt"), MEASURED)
    rec["same_direction_open"] = put("same_direction_open",
                                     _get(chief_permission, "same_direction_open"), MEASURED)
    # --- baseline karar (KARŞILAŞTIRMA TABANI) -----------------------------------------
    rec["baseline_rank"] = baseline_rank
    rec["baseline_accepted"] = (None if baseline_accepted is None else bool(baseline_accepted))
    rec["baseline_reject_reason"] = _s(baseline_reject_reason)
    rec["chief_allow"] = (None if chief_permission is None
                          else bool(_get(chief_permission, "allow")))
    rd = risk_decision if isinstance(risk_decision, dict) else (
        risk_decision.to_dict() if hasattr(risk_decision, "to_dict") else None)
    rec["risk_allowed"] = (None if rd is None else bool(rd.get("allowed")))
    rec["risk_reasons"] = [str(x)[:40] for x in (rd or {}).get("reasons", [])][:8] or None
    # --- kanıt paketleri ----------------------------------------------------------------
    rec["specialist_scores"] = _bounded(specialist_scores, MAX_SPECIALISTS)
    rec["features"] = _bounded(feats, MAX_FEATURES)
    rec["sources"] = sources
    rec["missing_fields"] = sorted(set(missing))
    rec["n_missing"] = len(rec["missing_fields"])
    rec["provenance"] = {
        "written_at_stage": "RANKING",
        "sees_outcome": False,
        "note_tr": ("Karar anında görülebilen alanlar. Sonuç (R, kazandı/kaybetti) bu kayda "
                    "GİREMEZ; snapshot kapanıştan ÖNCE yazılır."),
    }
    return rec


class EntrySnapshotStore:
    """Append-only aday snapshot deposu. Arıza çağıranı ÇÖKERTMEZ."""

    _locks: dict[str, threading.Lock] = {}
    _guard = threading.Lock()

    @classmethod
    def _lock_for(cls, path: Path) -> threading.Lock:
        key = str(path)
        with cls._guard:
            lk = cls._locks.get(key)
            if lk is None:
                lk = cls._locks[key] = threading.Lock()
            return lk

    def __init__(self, path: Path | str, *, max_per_cycle: int = 200):
        self.path = Path(path)
        self._lock = self._lock_for(self.path)
        #: Tek turda yazılacak azami snapshot — patolojik bir tur diski şişiremez.
        self.max_per_cycle = int(max_per_cycle)
        self.appended = 0
        self.errors = 0
        self.duplicates = 0
        self._ids: set[str] | None = None

    def iter_rows(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("entry_snapshot okunamadı: %s", exc)
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("candidate_id"):
                yield d

    def by_candidate(self) -> dict[str, dict[str, Any]]:
        """`candidate_id → snapshot`. İlk kayıt otoritedir (karar anı sonradan değişmez)."""
        out: dict[str, dict[str, Any]] = {}
        for r in self.iter_rows():
            cid = str(r["candidate_id"])
            if cid not in out:
                out[cid] = r
        return out

    def known_ids(self) -> set[str]:
        if self._ids is None:
            self._ids = set(self.by_candidate().keys())
        return self._ids

    def append(self, rec: dict[str, Any]) -> bool:
        cid = str((rec or {}).get("candidate_id") or "")
        if not cid:
            return False
        with self._lock:
            ids = self.known_ids()
            if cid in ids:
                self.duplicates += 1
                return False
            try:
                line = json.dumps(rec, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.errors += 1
                log.warning("entry_snapshot serileştirilemedi (%s): %s", cid, exc)
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
                log.warning("entry_snapshot yazılamadı (%s): %s", cid, exc)
                return False
            ids.add(cid)
            self.appended += 1
            return True

    def link_trade(self, candidate_id_: str, trade_id: str) -> bool:
        """Açılan pozisyonu adaya bağlar — AYRI bir satır olarak, snapshot YENİDEN YAZILMAZ."""
        return self.append_link({"schema_version": SCHEMA_VERSION, "kind": "link",
                                 "candidate_id": str(candidate_id_), "trade_id": str(trade_id),
                                 "linked_at": iso(utc_now())})

    def append_link(self, row: dict[str, Any]) -> bool:
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
            log.warning("entry_snapshot link yazılamadı: %s", exc)
            return False

    def trade_links(self) -> dict[str, str]:
        """`trade_id → candidate_id`."""
        out: dict[str, str] = {}
        for r in self.iter_rows():
            if r.get("kind") == "link" and r.get("trade_id"):
                out.setdefault(str(r["trade_id"]), str(r["candidate_id"]))
        return out

    def stats(self) -> dict[str, Any]:
        snaps = self.by_candidate()
        links = self.trade_links()
        return {"schema_version": SCHEMA_VERSION, "path": str(self.path),
                "snapshots": len(snaps), "links": len(links),
                "appended": self.appended, "duplicates": self.duplicates,
                "errors": self.errors}


def snapshot_from_memory_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """`trade_memory` giriş kaydından GÖZLEM amaçlı snapshot türetir.

    Bu kayıtlar açılış anında yazılmıştır ve gerçek point-in-time veridir, fakat bu özellik için
    tasarlanmamıştır: bazı alanları yoktur ve `candidate_id` üretilemez. Bu yüzden
    `LEGACY_MEMORY` olarak işaretlenir ve **terfi kanıtı sayılmaz** (bkz. `entry_eval`).
    """
    tid = _s(row.get("trade_id"))
    if not tid:
        return None
    dec = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    opp = dec.get("opportunity") if isinstance(dec.get("opportunity"), dict) else {}
    snap = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
    vals = snap.get("values") if isinstance(snap.get("values"), dict) else {}
    feats = row.get("features") if isinstance(row.get("features"), dict) else {}
    merged = dict(vals) | dict(feats)
    plan = {"entry": merged.get("entry"), "stop": merged.get("initial_stop") or merged.get("stop"),
            "entry_type": row.get("setup_type"), "notional": merged.get("notional"),
            "leverage": merged.get("leverage"), "targets": None,
            "expected_cost_pct": merged.get("expected_cost_pct")}
    rec = build_entry_snapshot(
        run_id=row.get("run_id"), cycle_id="legacy", symbol=row.get("symbol"),
        direction=row.get("direction"), decision=dec, plan=plan, opportunity=opp,
        features=merged, specialist_scores=None,
        baseline_accepted=True, baseline_rank=None,
        policy_version=None, code_sha=None, config_hash=None)
    rec["link_status"] = LEGACY_MEMORY
    rec["trade_id"] = tid
    rec["candidate_id"] = stable_id("cand", "legacy", tid)
    rec["decision_id"] = None
    rec["provenance"]["note_tr"] = ("`trade_memory` giriş kaydından türetildi — gerçek "
                                    "point-in-time veridir fakat bu özellik için tasarlanmamıştır; "
                                    "TERFİ KANITI SAYILMAZ.")
    return rec


__all__ = ["SCHEMA_VERSION", "MEASURED", "MODELED", "DEFAULTED", "MISSING", "SOURCES",
           "LINKED", "LEGACY_MEMORY", "LEGACY_UNLINKED", "EntrySnapshotStore",
           "build_entry_snapshot", "candidate_id", "decision_id",
           "snapshot_from_memory_entry"]

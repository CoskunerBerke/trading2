"""Giriş kararı provenance'ı (`entry_provenance_v1`) — açılan her pozisyonun KARAR ANI kimliği.

Neden ayrı bir depo: kapanan işlem defterden (`futures_ledger.json`) gelir ve defter kaydında
giriş kararına ait hiçbir kimlik YOKTUR (`TradeRecord` alanlarına bakınız: id/symbol/side/entry/
maliyetler var, `decision_id` yok). Bu yüzden 2026-09-02 denetiminde 18 kapanmış işlemin yalnız
2'si karar günlüğündeki bir `ACCEPTED` kaydına bağlanabildi; geri kalanın giriş kaydı ya hiç
yazılmamış ya da `decision_journal.jsonl` 20.000 satır tavanında arşive dönmüştü.

Tasarım kararı: provenance DEFTERE YAZILMAZ.
* Defterin serileşmesi ve ekonomisi değişmez; `futures_ledger.json` byte düzeyinde etkilenmez.
* Depo append-only JSONL'dir, atomik büyür ve restart'tan sağ çıkar.
* Bağlama anahtarı `trade_id`'dir; defter kaydının `id` alanıyla birebir aynı.

Eski işlemler için kimlik UYDURULMAZ. Gerçek `decision_id` bulunamıyorsa kayıt
`LEGACY_UNLINKED` ile işaretlenir; bu bir hata değil, dürüst bir kapsam beyanıdır. Ekonomi
defterden okunabildiği için o işlemler yine outcome ve ders üretir.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core import atomic_write_bytes, iso, utc_now

log = logging.getLogger(__name__)

SCHEMA_VERSION = "entry_provenance_v1"

#: Gerçek giriş kararı kimliği bulunamayan (bu özellik öncesi açılmış) işlemler.
LEGACY_UNLINKED = "LEGACY_UNLINKED"
#: Kimliği olan, karar günlüğüne bağlanabilen işlemler.
LINKED = "LINKED"

#: Sözleşme: bir provenance kaydının taşıması gereken alanlar. Değeri `None` olabilir,
#: fakat ANAHTAR daima bulunur — böylece "alan yok" ile "ölçülemedi" karışmaz.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "entry_decision_id", "entry_journal_id", "entry_code_sha", "entry_config_hash",
    "entry_policy_id", "entry_run_id", "entry_cycle_id",
    "entry_p_win", "entry_expected_r", "entry_expected_net_return",
    "entry_features", "entry_specialist_scores", "entry_regime", "entry_risk_decision",
    "entry_stop", "entry_targets", "entry_size_usdt", "entry_leverage",
)

#: Aşırı büyümeyi engelleyen sınırlar (tek kayıt worker turunu ve diski şişiremez).
MAX_FEATURES = 120
MAX_SPECIALISTS = 40
MAX_TARGETS = 6


def _f(x: Any) -> float | None:
    """Sonlu float ya da None. NaN/Infinity ASLA yayımlanmaz."""
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
    t = str(x)
    return t if t and t.lower() != "none" else None


def _bounded_numeric(d: Any, limit: int) -> dict[str, float] | None:
    """Sözlükten yalnız SONLU sayısal alanları, deterministik sırayla, sınırlı sayıda alır."""
    if not isinstance(d, dict):
        return None
    out: dict[str, float] = {}
    for k in sorted(d.keys(), key=str):
        v = _f(d[k])
        if v is None:
            continue
        out[str(k)[:64]] = v
        if len(out) >= limit:
            break
    return out or None


def build_entry_provenance(*, trade_id: str, symbol: str, direction: str,
                           decision_id: str | None = None,
                           journal_id: str | None = None,
                           run_id: str | None = None, cycle_id: Any = None,
                           code_sha: str | None = None, config_hash: str | None = None,
                           policy_id: str | None = None,
                           p_win: Any = None, expected_r: Any = None,
                           expected_net_return: Any = None,
                           features: Any = None, specialist_scores: Any = None,
                           regime: Any = None, risk_decision: Any = None,
                           stop: Any = None, targets: Any = None,
                           size_usdt: Any = None, leverage: Any = None,
                           opened_at: str | None = None) -> dict[str, Any]:
    """Saf fonksiyon: karar anı provenance kaydı üretir. Hiçbir yere yazmaz.

    `decision_id` verilmezse kayıt `LEGACY_UNLINKED` olur. Kimlik UYDURULMAZ.
    """
    did = _s(decision_id)
    rec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trade_id": str(trade_id),
        "symbol": _s(symbol),
        "direction": _s(direction),
        "recorded_at": iso(utc_now()),
        "opened_at": _s(opened_at),
        "link_status": LINKED if did else LEGACY_UNLINKED,
        "entry_decision_id": did,
        "entry_journal_id": _s(journal_id),
        "entry_run_id": _s(run_id),
        "entry_cycle_id": _s(cycle_id),
        "entry_code_sha": _s(code_sha),
        "entry_config_hash": _s(config_hash),
        "entry_policy_id": _s(policy_id),
        "entry_p_win": _f(p_win),
        "entry_expected_r": _f(expected_r),
        "entry_expected_net_return": _f(expected_net_return),
        "entry_features": _bounded_numeric(features, MAX_FEATURES),
        "entry_specialist_scores": _bounded_numeric(specialist_scores, MAX_SPECIALISTS),
        "entry_regime": _s(regime),
        "entry_risk_decision": _bounded_numeric(risk_decision, 30) if isinstance(risk_decision, dict) else None,
        "entry_stop": _f(stop),
        "entry_targets": [t for t in (_f(x) for x in (targets or [])) if t is not None][:MAX_TARGETS] or None,
        "entry_size_usdt": _f(size_usdt),
        "entry_leverage": _f(leverage),
    }
    # Sözleşme garantisi: her alan ANAHTAR olarak bulunur.
    for k in PROVENANCE_FIELDS:
        rec.setdefault(k, None)
    return rec


def legacy_provenance(*, trade_id: str, symbol: str | None = None,
                      direction: str | None = None,
                      opened_at: str | None = None,
                      reason: str = "NO_ENTRY_DECISION_RECORD") -> dict[str, Any]:
    """Bu özellik öncesi açılmış işlem için DÜRÜST kayıt: kimlik uydurulmaz.

    Ekonomi defterden okunabildiği için outcome/ders yine üretilir; yalnız karar bağlantısı yoktur.
    """
    rec = build_entry_provenance(trade_id=trade_id, symbol=symbol or "", direction=direction or "",
                                 opened_at=opened_at)
    rec["link_status"] = LEGACY_UNLINKED
    rec["legacy_reason"] = str(reason)[:80]
    return rec


class ProvenanceStore:
    """Append-only, idempotent `trade_id → provenance` deposu.

    * Aynı `trade_id` ikinci kez yazılmaz (ilk kayıt otoritedir; giriş kararı sonradan değişmez).
    * Yazım atomiktir: satır tek `write` ile eklenir ve `fsync` edilir.
    * Arıza çağıranı ÇÖKERTMEZ; sayaç artar ve baseline davranış sürer.
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
        self._seen: set[str] | None = None

    # ------------------------------------------------------------------ okuma
    def load(self) -> dict[str, dict[str, Any]]:
        """`trade_id → kayıt`. Bozuk satırlar ATLANIR, dosya asla istisna sızdırmaz.

        Aynı `trade_id` birden fazla satırdaysa İLK kayıt kazanır (giriş kararı değişmez).
        """
        out: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return out
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("provenance okunamadı: %s", exc)
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            tid = _s(d.get("trade_id"))
            if tid and tid not in out:
                out[tid] = d
        return out

    def get(self, trade_id: str) -> dict[str, Any] | None:
        return self.load().get(str(trade_id))

    def known_ids(self) -> set[str]:
        if self._seen is None:
            self._seen = set(self.load().keys())
        return self._seen

    # ------------------------------------------------------------------ yazım
    def record(self, rec: dict[str, Any]) -> bool:
        """Kaydı ekler. Aynı `trade_id` zaten varsa HİÇBİR ŞEY yapmaz ve False döner."""
        tid = _s((rec or {}).get("trade_id"))
        if not tid:
            return False
        with self._lock:
            seen = self.known_ids()
            if tid in seen:
                return False
            try:
                line = json.dumps(rec, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.errors += 1
                log.warning("provenance serileştirilemedi (%s): %s", tid, exc)
                return False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    try:
                        import os
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
            except OSError as exc:
                self.errors += 1
                log.warning("provenance yazılamadı (%s): %s", tid, exc)
                return False
            seen.add(tid)
            self.appended += 1
            return True

    def record_many(self, recs: Iterable[dict[str, Any]]) -> int:
        return sum(1 for r in recs if self.record(r))

    def rewrite(self, records: Iterable[dict[str, Any]]) -> int:
        """Dosyayı ATOMİK olarak baştan yazar (yalnız bakım/onarım yolları için).

        Normal akış `record()` kullanır; bu yol çağıranın verdiği kümeyi otorite kabul eder.
        """
        rows = [r for r in records if _s((r or {}).get("trade_id"))]
        blob = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)
        atomic_write_bytes(self.path, blob.encode("utf-8"))
        self._seen = {str(r["trade_id"]) for r in rows}
        return len(rows)


def link_summary(prov_by_tid: dict[str, dict[str, Any]], trade_ids: Iterable[str]) -> dict[str, Any]:
    """Bir kapanış kümesi için bağlantı özeti. Sayılar UYDURULMAZ, sayılır."""
    ids = [str(t) for t in trade_ids]
    linked = [t for t in ids if (prov_by_tid.get(t) or {}).get("link_status") == LINKED]
    legacy = [t for t in ids if (prov_by_tid.get(t) or {}).get("link_status") == LEGACY_UNLINKED]
    missing = [t for t in ids if t not in prov_by_tid]
    return {"schema_version": SCHEMA_VERSION, "total": len(ids),
            "linked": len(linked), "legacy_unlinked": len(legacy), "missing": len(missing),
            "linked_ids": linked, "legacy_unlinked_ids": legacy, "missing_ids": missing}


__all__ = ["SCHEMA_VERSION", "LEGACY_UNLINKED", "LINKED", "PROVENANCE_FIELDS",
           "ProvenanceStore", "build_entry_provenance", "legacy_provenance", "link_summary"]

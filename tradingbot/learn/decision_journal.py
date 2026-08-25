"""Karar günlüğü — DEĞERLENDİRİLEN HER ADAY için tekil, değişmez karar snapshot'ı.

Neden: mevcut yolda yalnız AÇILAN işlemler `TradeMemory`ye giriyordu; reddedilen/veto edilen
adayların karar anı yalnız `risk.json` içindeki son 50 kayıtta yaşıyor ve her turda üzerine
yazılıyordu. Bu yüzden "neden açmadım" sorusu geriye dönük ölçülemiyordu.

Sözleşme:
* Append-only JSONL; her satır tek karar ya da tek outcome bağlantısı.
* `decision_id` deterministiktir (`run_id + cycle + symbol + direction`); aynı turda aynı aday
  ikinci kez yazılmaz (retry/duplicate koruması).
* Kayıt BOUNDED: ham mum dizisi yazılmaz, feature/specialist alanları sayı sınırına kırpılır.
* Olmayan alan uydurulmaz → `null` + `availability`/`provenance`.
* Aktif dosya satır sınırını aşınca taşan kayıtlar ÖNCE kayıpsız arşive mühürlenir, ANCAK
  ondan sonra aktif dosyadan çıkarılır (`rotate`). Arşiv yoksa ya da yazımı başarısızsa
  hiçbir kayıt silinmez — sessiz veri kaybı YASAK (bkz. `journal_archive`).
* Ledger/outbox/gateway'e DOKUNMAZ; yalnız kendi dosyasına yazar.
* Yazım hatası çağıranı ÇÖKERTMEZ (`append` False döner); worker baseline davranışını sürdürür.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core import atomic_write_text, iso, stable_id, utc_now
from .journal_archive import ArchiveError, SegmentArchive

SCHEMA_VERSION = "decision_journal_v1"

#: Kayıt türleri.
KIND_DECISION = "decision"
KIND_OUTCOME = "outcome_link"

#: Sonuç sınıfları — her aday bunlardan biriyle etiketlenir.
ACCEPTED, REJECTED, SHADOW = "ACCEPTED", "REJECTED", "SHADOW"
NON_ACTIONABLE = "NON_ACTIONABLE"
NO_VALID_PLAN = "NO_VALID_PLAN"
CHIEF_REJECTED = "CHIEF_REJECTED"
VETOED = "VETOED"
NO_TRIGGER = "NO_TRIGGER"
NEGATIVE_EDGE = "NEGATIVE_EDGE"
DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
RESEARCH_BLOCKED = "RESEARCH_BLOCKED"
LEVERAGE_BLOCKED = "LEVERAGE_BLOCKED"
SIZE_ZERO = "SIZE_ZERO"
RISK_REJECTED = "RISK_REJECTED"
OPEN_FAILED = "OPEN_FAILED"
DATA_INVALID = "DATA_INVALID"
GATE_HALTED = "GATE_HALTED"
#: Tier-A ucuz tarama elemesi — derin analize SEÇİLMEDİ (veri geçerli, skor/sıra yetmedi).
SCREENED_OUT = "SCREENED_OUT"

OUTCOME_CLASSES = (ACCEPTED, OPEN_FAILED, RISK_REJECTED, LEVERAGE_BLOCKED, SIZE_ZERO,
                   RESEARCH_BLOCKED, DUPLICATE_SKIPPED, NEGATIVE_EDGE, NO_TRIGGER, VETOED,
                   CHIEF_REJECTED, NO_VALID_PLAN, NON_ACTIONABLE, DATA_INVALID, GATE_HALTED,
                   SCREENED_OUT, SHADOW, REJECTED)

#: `block_code` → (sonuç sınıfı, üreten aşama). Motorun GERÇEK huni kodlarıyla birebir eşlenir.
_BLOCK_MAP: dict[str, tuple[str, str]] = {
    "CHIEF_BLOCKED": (CHIEF_REJECTED, "chief_ranking"),
    "NO_TRIGGER": (NO_TRIGGER, "trigger"),
    "NEGATIVE_NET_EDGE": (NEGATIVE_EDGE, "economics"),
    "RESEARCH_SIZE_ONLY": (RESEARCH_BLOCKED, "economics_research_only"),
    "DUPLICATE_SIGNAL": (DUPLICATE_SKIPPED, "duplicate_guard"),
    "RESEARCH_POLICY_BLOCK": (RESEARCH_BLOCKED, "research_policy"),
    "SIZE_MULTIPLIER_ZERO": (SIZE_ZERO, "sizing"),
    "LEVERAGE_GATE_BLOCKED": (LEVERAGE_BLOCKED, "leverage_gate"),
    "RISK_CAPACITY_BLOCKED": (RISK_REJECTED, "risk_engine_capacity"),
    "RISK_ENGINE_BLOCKED": (RISK_REJECTED, "risk_engine"),
    "EXCHANGE_REJECTED": (OPEN_FAILED, "ledger_open"),
}
#: Tur genelinde girişleri durduran kapılar (aday bazlı red DEĞİL).
_HALT_REASONS = {"RISK_STATE_PERSIST_FAILED", "SHUTDOWN_REQUESTED", "GAP_RECONCILE_PENDING"}


def classify_outcome(entry: dict[str, Any] | None, *, is_actionable: bool | None,
                     has_valid_plan: bool | None, verdict: str | None = None,
                     shadowed: bool = False) -> tuple[str, str, str | None]:
    """(sonuç sınıfı, aşama, kesin neden) — tek adayın NİHAİ sonucu.

    Sıralama motorun gerçek huni sırasını izler; ilk eşleşen kesin nedendir. Aynı aday için
    yalnız TEK nihai sınıf üretilir (aşama geçmişi ayrı alanda tutulur).
    """
    e = entry or {}
    if e.get("executed_notional") is not None:
        return ACCEPTED, "ledger_open", None
    code = str(e.get("block_code") or "")
    if code in _BLOCK_MAP:
        cls, stage = _BLOCK_MAP[code]
        # Chief'in SERT red-team veto'su ayrı sınıftır (yumuşak sıralama reddinden farklı).
        if cls is CHIEF_REJECTED and code == "CHIEF_BLOCKED" and e.get("hard_veto"):
            return VETOED, "chief_red_team", code
        return cls, stage, code
    for r in (e.get("risk_reasons") or []):
        if str(r) in _HALT_REASONS:
            return GATE_HALTED, "cycle_gate", str(r)
    if str(verdict or "").upper() in ("DATA_INVALID", "NO_DATA"):
        # Geçersiz veri: HENÜZ değerlendirilebilir bir aday değil — gerçek redden AYRIDIR.
        return DATA_INVALID, "data_quality", str(verdict)
    if is_actionable is False:
        return NON_ACTIONABLE, "coin_head", (str(verdict) if verdict else "NOT_ACTIONABLE")
    if has_valid_plan is False:
        return NO_VALID_PLAN, "plan_builder", "PLAN_MISSING_OR_INVALID"
    if shadowed:
        return SHADOW, "shadow_book", None
    return REJECTED, "unclassified", code or None

MAX_FEATURES = 64          # snapshot değerlerinden en fazla bu kadar sayısal alan
MAX_SPECIALISTS = 24
MAX_REASONS = 12
DEFAULT_MAX_LINES = 20_000


def _f(x: Any) -> float | None:
    """Sonlu float ya da None — bare NaN/Infinity JSON'a ASLA çıkmaz."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _s(x: Any) -> str | None:
    return None if x is None or x == "" else str(x)


def _bounded_numeric(d: Any, limit: int) -> dict[str, float] | None:
    """Sözlüğü sayısal, sonlu ve sınırlı bir alt kümeye indirger (ham diziler atılır)."""
    if not isinstance(d, dict) or not d:
        return None
    out: dict[str, float] = {}
    for k in sorted(d):
        if len(out) >= limit:
            break
        v = _f(d[k])
        if v is not None:
            out[str(k)] = v
    return out or None


def decision_id_for(run_id: str, cycle_id: Any, symbol: str, direction: str) -> str:
    """Deterministik kimlik — aynı tur/aday için DAİMA aynı değer (idempotency anahtarı)."""
    return stable_id("dec", str(run_id), str(cycle_id), str(symbol), str(direction))


def build_decision_record(*, run_id: str, cycle_id: Any, symbol: str, direction: str,
                          market_type: str | None = None, decision_ts: str | None = None,
                          entry: dict[str, Any] | None = None,
                          snapshot: Any = None, decision: Any = None,
                          outcome_kind: str = REJECTED, trade_id: str | None = None,
                          code_sha: str | None = None, config_hash: str | None = None,
                          policy_id: str | None = None,
                          price: float | None = None) -> dict[str, Any]:
    """Karar anı kaydını üretir. `entry`: motorun `risk_log` sözlüğü; `snapshot`: FeatureSnapshotV3.

    Saf fonksiyondur; hiçbir yere yazmaz ve girdilerini değiştirmez.
    """
    e = entry or {}
    snap_values = getattr(snapshot, "values", None)
    snap_missing = list(getattr(snapshot, "missing", []) or [])[:MAX_REASONS]
    feats = _bounded_numeric(snap_values, MAX_FEATURES)
    specialists = None
    regime = None
    setup = None
    confidence = p_win = expected_r = None
    verdict = None
    vetoes: list[str] | None = None
    if decision is not None:
        regime = _s(getattr(decision, "regime", None))
        v = getattr(decision, "verdict", None)
        verdict = _s(getattr(v, "value", v))
        confidence = _f(getattr(decision, "consensus_confidence", None))
        p_win = _f(getattr(decision, "p_win", None))
        expected_r = _f(getattr(decision, "expected_r", None))
        raw_vetoes = getattr(decision, "vetoes", None)
        if isinstance(raw_vetoes, (list, tuple)):
            vetoes = [str(x)[:80] for x in raw_vetoes][:MAX_REASONS] or None
        reports = getattr(decision, "specialist_reports", None)
        if isinstance(reports, (list, tuple)):
            sc: dict[str, float] = {}
            for r in reports:
                if len(sc) >= MAX_SPECIALISTS:
                    break
                name = _s(getattr(r, "agent_name", None))
                bias = _f(getattr(r, "bias", None))
                if name and bias is not None:
                    sc[name] = bias
            specialists = sc or None
    if snapshot is not None:
        setup = _s(getattr(snapshot, "strategy_version", None)) or setup
        regime = regime or _s((snap_values or {}).get("regime"))

    rec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_DECISION,
        "decision_id": decision_id_for(run_id, cycle_id, symbol, direction),
        "run_id": _s(run_id),
        "cycle_id": _s(cycle_id),
        "decision_ts": _s(decision_ts) or iso(utc_now()),
        "as_of_ts": _s(getattr(snapshot, "last_bar_ts", None)),
        "symbol": _s(symbol),
        "market_type": _s(market_type) or _s(e.get("market_type")),
        "direction": _s(direction),
        "timeframe": _s(getattr(snapshot, "timeframe", None)),
        "price": _f(price),
        "features": feats,
        "features_missing": snap_missing or None,
        "feature_version": getattr(snapshot, "feature_version", None),
        "specialist_scores": specialists,
        "regime": regime,
        "setup": setup or _s(e.get("setup_type")),
        "confidence": confidence,
        "p_win": p_win,
        "expected_r": expected_r,
        "verdict": verdict or _s(e.get("verdict")),
        "chief_allow": e.get("chief_allow"),
        "chief_reason": _s(e.get("chief_reason")),
        "vetoes": vetoes,
        "block_code": _s(e.get("block_code")),
        "risk_allowed": e.get("risk_allowed"),
        "risk_reasons": [str(x)[:60] for x in (e.get("risk_reasons") or [])][:MAX_REASONS] or None,
        "size_multiplier_total": _f(e.get("size_multiplier_total")),
        "planned_notional": _f(e.get("final_notional")) or _f(e.get("plan_notional")),
        "applied_risk_usdt": _f(e.get("applied_risk_usdt")),
        "leverage": e.get("leverage"),
        "execution_cost_estimate": {"expected_cost_pct": _f((feats or {}).get("expected_cost_pct")),
                                    "spread_pct": _f((feats or {}).get("spread_pct")),
                                    "provenance": "coin_head_estimate"},
        "policy_id": _s(policy_id) or _s(e.get("research_policy_id")),
        "code_sha": _s(code_sha),
        "config_hash": _s(config_hash) or _s(getattr(snapshot, "config_hash", None)),
        "outcome_kind": outcome_kind,
        "trade_id": _s(trade_id),
    }
    rec["availability"] = {k: rec.get(k) is not None for k in
                           ("features", "specialist_scores", "regime", "p_win", "expected_r",
                            "planned_notional", "trade_id")}
    return rec


def build_outcome_link(*, trade_id: str, outcome: dict[str, Any],
                       decision_id: str | None = None,
                       lesson: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kapanış kaydı — aynı `trade_id` üzerinden giriş snapshot'ına bağlanır."""
    o = outcome or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_OUTCOME,
        "trade_id": _s(trade_id),
        "decision_id": _s(decision_id),
        "outcome_ts": _s(o.get("closed_at")) or iso(utc_now()),
        "gross_pnl": _f(o.get("gross_pnl")),
        "net_pnl": _f(o.get("net_pnl", o.get("pnl"))),
        "r_multiple": _f(o.get("r_multiple")),
        "fees": _f(o.get("fees")),
        "funding": _f(o.get("funding")),
        "slippage_cost": _f(o.get("slippage_cost")),
        "mae_pct": _f(o.get("mae_pct")),
        "mfe_pct": _f(o.get("mfe_pct")),
        "bars_held": int(o["bars_held"]) if isinstance(o.get("bars_held"), (int, float)) else None,
        "exit_reason": _s(o.get("exit_reason")),
        "targets_hit": o.get("targets_hit"),
        "opened_at": _s(o.get("opened_at")),
        "lesson_codes": [str(x)[:40] for x in ((lesson or {}).get("codes") or [])][:MAX_REASONS] or None,
        "provenance": "paper_ledger_close",
    }


class DecisionJournal:
    """Append-only karar günlüğü. Arızası çağıranı çökertmez.

    Aynı dosyaya yazan bütün örnekler ORTAK bir kilidi paylaşır: Windows'ta eşzamanlı
    `open(..., "a")` + `fsync` çağrıları satır KAYBEDEBİLİYORDU (ölçüldü: 100 yazımdan 96'sı).
    Kilit yol bazındadır, süreç içidir; worker zaten tek süreçtir (`.worker_instance.json`).
    """

    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    @classmethod
    def _lock_for(cls, path: Path) -> threading.Lock:
        key = str(path.resolve() if path.parent.exists() else path)
        with cls._locks_guard:
            lk = cls._locks.get(key)
            if lk is None:
                lk = cls._locks[key] = threading.Lock()
            return lk

    def __init__(self, path: Path | str, *, max_lines: int = DEFAULT_MAX_LINES,
                 archive: SegmentArchive | None = None):
        self.path = Path(path)
        self.max_lines = int(max_lines)
        #: Kayıpsız arşiv. `None` ise ROTASYON YAPILMAZ ve hiçbir kayıt silinmez.
        self.archive = archive
        self._lock = self._lock_for(self.path)
        self._seen: set[str] = set()
        self._seen_outcomes: set[str] = set()
        self.errors = 0
        self.appended = 0
        self.archive_errors = 0
        self.last_archive_error: str | None = None
        self._line_count = self._count_lines()

    # -------------------------------------------------------------- yazım
    def _write_line(self, obj: dict[str, Any]) -> bool:
        try:
            line = json.dumps(obj, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            self.errors += 1
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:                       # satır kaybını önleyen seri yazım
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                # Sayaçlar da KİLİT ALTINDA: rotasyon `_line_count`'a bakarak karar verir;
                # kilit dışında artırmak eşzamanlı yazımda sayacı geriye düşürebilirdi.
                self.appended += 1
                self._line_count += 1
        except OSError:
            self.errors += 1
            return False
        return True

    def _count_lines(self) -> int:
        """Fiziksel (boş olmayan) satır sayısı — rotasyonun UCUZ erken çıkışı için sayaç temeli."""
        if not self.path.exists():
            return 0
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for ln in fh if ln.strip())
        except OSError:
            self.errors += 1
            return 0

    def append_decision(self, rec: dict[str, Any]) -> bool:
        """Idempotent: aynı `decision_id` ikinci kez yazılmaz."""
        did = str(rec.get("decision_id") or "")
        if not did:
            return False
        with self._lock:                           # kontrol + işaretleme atomik olmalı
            if did in self._seen:
                return False
            self._seen.add(did)
        if self._write_line(rec):
            return True
        with self._lock:
            self._seen.discard(did)                # yazım düştü → tekrar denenebilir
        return False

    def append_outcome(self, rec: dict[str, Any]) -> bool:
        """Idempotent: aynı `trade_id` için ikinci outcome yazılmaz."""
        tid = str(rec.get("trade_id") or "")
        if not tid:
            return False
        with self._lock:
            if tid in self._seen_outcomes:
                return False
            self._seen_outcomes.add(tid)
        if self._write_line(rec):
            return True
        with self._lock:
            self._seen_outcomes.discard(tid)
        return False

    def append_many(self, recs: Iterable[dict[str, Any]]) -> int:
        return sum(1 for r in recs if self.append_decision(r))

    # -------------------------------------------------------------- okuma / bakım
    def iter_rows(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())

        def gen():
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue          # yarım/bozuk satır sessizce atlanır, dosya bozulmaz
        return gen()

    def iter_all_rows(self, *, verify_checksums: bool = True) -> Iterator[dict[str, Any]]:
        """ARŞİV + AKTİF birleşik akış, kimliğe göre TEKİLLEŞTİRİLMİŞ (offline/rapor yolu).

        Sıcak döngü bunu ÇAĞIRMAZ — maliyeti O(toplam arşiv)'dir. Aynı kayıt hem arşivde hem
        aktif dosyada görünse bile (çökme sonrası yeniden mühürleme) TEK kez verilir; checksum'ı
        bozuk segment atlanır ve öğrenmeye katılmaz.
        """
        seen_dec: set[str] = set()
        seen_out: set[str] = set()

        def _emit(row: dict[str, Any]) -> bool:
            if row.get("kind") == KIND_OUTCOME:
                tid = str(row.get("trade_id") or "")
                if not tid or tid in seen_out:
                    return False
                seen_out.add(tid)
                return True
            did = str(row.get("decision_id") or "")
            if did:
                if did in seen_dec:
                    return False
                seen_dec.add(did)
            return True

        if self.archive is not None:
            for row in self.archive.iter_rows(verify_checksums=verify_checksums):
                if _emit(row):
                    yield row
        for row in self.iter_rows():
            if _emit(row):
                yield row

    def load_seen(self) -> None:
        """Yeniden başlatma sonrası duplicate koruması için mevcut kimlikleri belleğe alır.

        Yalnız AKTİF dosya taranır ve bu YETERLİDİR: `decision_id` her süreçte yeni olan
        `run_id`'yi içerir (arşivdeki eski turlarla çakışamaz), `trade_id` ise ancak açık bir
        pozisyon kapanırken yazılır — aktif pencere (varsayılan 20.000 satır ≈ 5-6 gün) bu
        ufku daima kapsar. Arşivin tamamını her açılışta taramak O(toplam arşiv) maliyet
        getirirdi; okuma yolundaki tekilleştirme `iter_all_rows` içindedir.
        """
        for r in self.iter_rows():
            if r.get("kind") == KIND_OUTCOME:
                if r.get("trade_id"):
                    self._seen_outcomes.add(str(r["trade_id"]))
            elif r.get("decision_id"):
                self._seen.add(str(r["decision_id"]))
        self._line_count = self._count_lines()

    # ------------------------------------------------------------ kayıpsız rotasyon
    def _read_lines(self) -> list[str]:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.errors += 1
            return []
        return [ln for ln in text.splitlines() if ln.strip()]

    @staticmethod
    def _block_sha(lines: list[str]) -> str:
        return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()

    def _apply_trim(self, pending: dict[str, Any]) -> int:
        """Arşive alınmış baş bloğu aktif dosyadan çıkarır. ÇÖKME SONRASI IDEMPOTENT.

        Kayıtlar arşive mühürlenip manifest işlendikten SONRA çağrılır. Baş blok beklenen
        sha256'yı taşımıyorsa budama ZATEN yapılmıştır (ya da dosya dışarıdan değişmiştir):
        bu durumda segment doğrulanır ve kayıt temizlenir; veri asla iki kez silinmez.
        """
        n = int(pending.get("n_lines") or 0)
        want = str(pending.get("block_sha256") or "")
        if n <= 0 or not want:
            return 0
        lines = self._read_lines()
        if len(lines) >= n and self._block_sha(lines[:n]) == want:
            rest = lines[n:]
            atomic_write_text(self.path, ("\n".join(rest) + "\n") if rest else "")
            self._line_count = len(rest)
            return n
        return 0

    def rotate(self) -> dict[str, Any]:
        """Aktif dosyayı sınırda tutar — TAŞAN KAYITLARI ÖNCE ARŞİVLER, sonra çıkarır.

        Sıra: kurtarma → bekleyen budama → mühürleme → manifest → budama. Arşiv yoksa ya da
        yazım/checksum başarısızsa AKTİF DOSYA BUDANMAZ; kayıt tutulur ve alarm üretilir.
        """
        res: dict[str, Any] = {"archived": 0, "trimmed": 0, "segment_id": None,
                               "health": "OK", "error": None, "recovered": None}
        if self.archive is None:
            # Fail-safe: arşiv yoksa SİLME YOK. Dosya sınırsız büyür ama kayıp olmaz.
            res["health"] = "NO_ARCHIVE_NO_DELETION"
            return res
        with self._lock:
            try:
                res["recovered"] = self.archive.recover()
                pending = self.archive.pending_trim()
                if pending:
                    res["trimmed"] += self._apply_trim(pending)
                    seg = self.archive.segment_for(str(pending.get("segment_id") or ""))
                    if seg is not None:
                        self.archive.clear_pending_trim()
                if self._line_count <= self.max_lines:
                    res["health"] = self.archive.manifest().get("health") or "OK"
                    return res
                lines = self._read_lines()
                self._line_count = len(lines)
                if len(lines) <= self.max_lines:
                    return res
                cut = len(lines) - self.max_lines
                block = lines[:cut]
                meta = self.archive.seal(block)
                self.archive.commit(meta, pending_trim={
                    "segment_id": meta["segment_id"], "n_lines": cut,
                    "block_sha256": meta["block_sha256"]})
                res["archived"] = cut
                res["segment_id"] = meta["segment_id"]
                res["trimmed"] += self._apply_trim({"n_lines": cut,
                                                    "block_sha256": meta["block_sha256"]})
                self.archive.clear_pending_trim()
            except (ArchiveError, OSError, ValueError) as exc:
                # Arşiv başarısız → BUDAMA YOK. Sessiz kayıp yerine açık alarm.
                self.archive_errors += 1
                self.last_archive_error = f"{type(exc).__name__}: {exc}"[:300]
                res["health"] = "ARCHIVE_FAILED"
                res["error"] = self.last_archive_error
        return res

    def prune(self) -> int:
        """Geriye uyumluluk sarmalayıcısı — artık KAYIPSIZ. Dönen: arşive taşınan satır sayısı.

        Eski davranış (en eskileri doğrudan atmak) KALDIRILDI; kayıtlar `rotate()` ile önce
        arşive mühürlenir. Arşiv yoksa 0 döner ve hiçbir şey silinmez.
        """
        return int(self.rotate().get("archived") or 0)

    def retention_stats(self) -> dict[str, Any]:
        """Aktif + arşiv birleşik saklama özeti — O(1) (manifest okur, segment AÇMAZ)."""
        arc = self.archive.stats() if self.archive is not None else None
        hot = int(self._line_count)
        lifetime = hot + int((arc or {}).get("n_archived_records") or 0)
        health = (arc or {}).get("health") if arc else "NO_ARCHIVE"
        if self.last_archive_error:
            health = "ARCHIVE_FAILED"
        return {"schema_version": SCHEMA_VERSION,
                "hot_records": hot, "hot_max_lines": self.max_lines,
                "archived_records": int((arc or {}).get("n_archived_records") or 0),
                "archived_decisions": int((arc or {}).get("n_archived_decisions") or 0),
                "archived_outcomes": int((arc or {}).get("n_archived_outcomes") or 0),
                "archive_bytes_compressed": int((arc or {}).get("bytes_compressed") or 0),
                "archive_bytes_raw": int((arc or {}).get("bytes_raw") or 0),
                "lifetime_records": lifetime,
                "n_segments": int((arc or {}).get("n_segments") or 0),
                "oldest_ts": (arc or {}).get("oldest_ts"),
                "newest_ts": (arc or {}).get("newest_ts"),
                "last_rotation_at": (arc or {}).get("last_rotation_at"),
                "archive_health": health,
                "last_archive_error": self.last_archive_error or (arc or {}).get("last_error"),
                "retention_policy": (arc or {}).get("retention_policy") or "NO_ARCHIVE",
                "deleted_segments": int((arc or {}).get("deleted_segments") or 0),
                "archive_root": (arc or {}).get("root"),
                "silent_deletion": False,
                "archive_errors": self.archive_errors}

    def stats(self) -> dict[str, Any]:
        """Kapsama sayaçları — dashboard ve quant raporu için (deterministik, salt okunur)."""
        n_dec = n_out = n_acc = n_rej = n_shadow = 0
        n_feat = n_spec = n_regime = n_trade_id = 0
        outcome_ids: set[str] = set()
        trade_ids: set[str] = set()
        for r in self.iter_rows():
            if r.get("kind") == KIND_OUTCOME:
                n_out += 1
                if r.get("trade_id"):
                    outcome_ids.add(str(r["trade_id"]))
                continue
            n_dec += 1
            k = r.get("outcome_kind")
            n_acc += int(k == ACCEPTED)
            n_rej += int(k == REJECTED)
            n_shadow += int(k == SHADOW)
            n_feat += int(bool(r.get("features")))
            n_spec += int(bool(r.get("specialist_scores")))
            n_regime += int(bool(r.get("regime")))
            if r.get("trade_id"):
                n_trade_id += 1
                trade_ids.add(str(r["trade_id"]))

        def ratio(n: int) -> float | None:
            return round(n / n_dec, 6) if n_dec else None

        return {"schema_version": SCHEMA_VERSION,
                "n_decisions": n_dec, "n_outcome_links": n_out,
                "n_accepted": n_acc, "n_rejected": n_rej, "n_shadow": n_shadow,
                "n_with_trade_id": n_trade_id,
                "n_outcome_linked": len(trade_ids & outcome_ids),
                "coverage": {"features": ratio(n_feat), "specialist_scores": ratio(n_spec),
                             "regime": ratio(n_regime), "trade_id": ratio(n_trade_id)},
                "write_errors": self.errors, "appended_this_process": self.appended}


def join_outcomes(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Karar + outcome satırlarını `trade_id` üzerinden birleştirir (offline analiz için).

    Aynı `trade_id` için birden çok outcome varsa İLKİ geçerlidir (idempotency).
    """
    decisions: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == KIND_OUTCOME:
            tid = str(r.get("trade_id") or "")
            if tid and tid not in outcomes:
                outcomes[tid] = r
        elif r.get("kind") == KIND_DECISION:
            decisions.append(r)
    out = []
    for d in decisions:
        rec = dict(d)
        tid = str(d.get("trade_id") or "")
        rec["outcome"] = outcomes.get(tid) if tid else None
        rec["outcome_linked"] = rec["outcome"] is not None
        out.append(rec)
    return out

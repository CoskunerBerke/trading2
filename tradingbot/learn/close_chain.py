"""Kapanış zinciri bütünlüğü (`close_chain_v1`) — kapanan HER PAPER işleminin öğrenilmesini garanti eder.

Kanonik zincir:

    FINAL LEDGER CLOSE
    -> immutable outcome event
    -> entry/outcome link
    -> exactly-one lesson
    -> calibration/statistics update
    -> bounded agent-weight update
    -> learning summary
    -> quant refresh

**Kanonik kaynak DEFTERDİR.** `futures_ledger.json` içindeki `history` listesi yalnız
`FuturesLedgerV2._finalize()` tarafından büyütülür ve `_finalize` yalnız pozisyon TAMAMEN
kapandığında çağrılır. TP1 kısmi azaltması `_close_part` ile yapılır ve `history`'ye HİÇBİR
kayıt eklemez; yalnız kapanış kaydında `tp1_done=True` bırakır. Bu yüzden "history satırı =
final kapanış" eşitliği kod düzeyinde doğrudur ve kısmi TP asla final kapanış sayılmaz.

**Neden ayrı bir bütünlük katmanı gerekti (2026-09-02 ölçümü):** `engine_v3.tour()` defteri
`ledger2.save()` ile ÖNCE kalıcı yapar, öğrenmeyi SONRA çalıştırır. Bu sıra çift öğrenmeyi
önler ama karşılığında bir kayıp penceresi açar: iki adım arasında süreç ölürse kapanış
defterde kalıcıdır ve o işlem BİR DAHA ASLA öğrenilmez, çünkü `ledger2.tick()` bir sonraki
turda o kapanışı tekrar döndürmez. Bu modül eksik adımı sonraki turda tamamlar.

Değişmezler:
* Kapanış olayı kimliği DETERMİNİSTİKTİR (`trade_id` + `closed_at` + `exit_reason`).
* Aynı `trade_id` ikinci kez öğrenilemez (`pending_*` kümeleri kimliğe göre çalışır).
* Bu modül DEFTERİ DEĞİŞTİRMEZ; yalnız okur. Ekonomi burada yeniden hesaplanmaz.
* Sonlu olmayan sayı yayımlanmaz.
* Tek işlemden kesin hüküm ("model haklıydı/yanıldı") ÜRETİLMEZ; bkz. `learn.prob_semantics`.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from ..core import stable_id

SCHEMA_VERSION = "close_chain_v1"

#: Zincir adımları — dashboard ve reconcile aynı adları kullanır.
STEP_OUTCOME = "outcome"
STEP_LESSON = "lesson"
STEP_LINK = "entry_link"
STEPS = (STEP_OUTCOME, STEP_LESSON, STEP_LINK)

#: Zincir durumu kodları.
COMPLETE = "COMPLETE"
MISSING_OUTCOME = "MISSING_OUTCOME"
MISSING_LESSON = "MISSING_LESSON"
MISSING_BOTH = "MISSING_OUTCOME_AND_LESSON"

#: `link_status` değerleri `learn.provenance` ile ORTAKTIR (tek kaynak).
from .provenance import LEGACY_UNLINKED, LINKED  # noqa: E402


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
    t = str(x)
    return t or None


def close_event_id(trade_id: Any, closed_at: Any, exit_reason: Any) -> str:
    """Final kapanış olayının DETERMİNİSTİK kimliği.

    Aynı kapanış kaç kez işlenirse işlensin aynı kimliği üretir; restart/retry sonrası
    idempotency anahtarı budur. `trade_id` tek başına yetmez: teorik olarak aynı defter
    kimliği farklı bir kapanış zamanıyla yeniden yazılırsa bunu fark etmek gerekir.
    """
    return stable_id("close", str(trade_id), str(closed_at or ""), str(exit_reason or ""))


def legacy_view(record: Any) -> dict[str, Any]:
    """Kapanış kaydının ÖĞRENME GİRDİSİ biçimi — canlı turla BİREBİR aynı.

    `engine_v3.tour()` öğrenmeye `TradeRecord.to_legacy_dict()` verir: bütün `Decimal` alanlar
    `float`a çevrilmiştir. Defterin `to_dict()` çıktısı ise `Decimal`'ı DİZE olarak serileştirir
    (`"pnl": "-1.047492438708"`). Onarım yolu ham sözlüğü doğrudan `Learner.learn()`e verirse
    `rec["pnl"] > 0` karşılaştırması `str > int` olur ve ders sessizce üretilemez — bu hata
    geliştirme sırasında gerçekten yaşandı. Bu yüzden onarım da AYNI dönüşümden geçer:
    iki farklı girdi biçimi iki farklı ders üretirdi.
    """
    if hasattr(record, "to_legacy_dict"):
        try:
            return record.to_legacy_dict()
        except Exception:  # noqa: BLE001 — bozuk kayıt onarımı durdurmaz, aşağıda coerce edilir
            pass
    d = record if isinstance(record, dict) else (
        record.to_dict() if hasattr(record, "to_dict") else {})
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if isinstance(v, str):
            f = _f(v)
            out[k] = f if (f is not None and k not in _TEXT_KEYS) else v
        else:
            out[k] = v
    return out


#: Sayıya BENZESE bile dize kalması gereken alanlar (ör. `"2026-08-20T01:42:15+00:00"` değil ama
#: `exit_reason="hedef2"` gibi alanlar yanlışlıkla sayıya çevrilmemeli).
_TEXT_KEYS = frozenset({"id", "symbol", "side", "exit_reason", "closed_at", "opened_at",
                        "setup_type", "trigger_text", "market_type", "amount_type"})


def canonical_closes(history: Iterable[Any]) -> list[dict[str, Any]]:
    """Defter geçmişinden kanonik final kapanış listesi.

    Girdi hem `TradeRecord` nesneleri hem de ham sözlükler olabilir (state dosyasından okunmuş
    olabilir). Kimliksiz satır ATLANIR; sessizce uydurulmaz.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in history or []:
        d = legacy_view(h)
        if not isinstance(d, dict) or not d:
            continue
        tid = _s(d.get("id") or d.get("trade_id"))
        if not tid:
            continue
        ev = close_event_id(tid, d.get("closed_at"), d.get("exit_reason"))
        if ev in seen:                      # aynı kapanış iki kez listelenmez
            continue
        seen.add(ev)
        out.append({
            "close_event_id": ev,
            "trade_id": tid,
            "symbol": _s(d.get("symbol")),
            "side": _s(d.get("side")),
            "opened_at": _s(d.get("opened_at")),
            "closed_at": _s(d.get("closed_at")),
            "exit_reason": _s(d.get("exit_reason")),
            "net_pnl": _f(d.get("net_pnl", d.get("pnl"))),
            "r_multiple": _f(d.get("r_multiple")),
            "fees": _f(d.get("fees")),
            "funding": _f(d.get("funding")),
            "mae_pct": _f(d.get("mae_pct")),
            "mfe_pct": _f(d.get("mfe_pct")),
            # KISMİ TP FİNAL KAPANIŞ DEĞİLDİR: bu bayrak yalnız final kapanıştan ÖNCE bir TP1
            # azaltması olduğunu söyler. Ayrı bir "final close" satırı DEĞİLDİR.
            "tp1_done": bool(d.get("tp1_done")),
            "raw": d,
        })
    return out


def r_normalized(close: dict[str, Any]) -> dict[str, float | None]:
    """MFE/MAE'yi STOP MESAFESİNE bölerek R cinsine çevirir.

    Yüzde bazlı MFE yanıltıcıdır: %3,7 lehe hareket, stop mesafesi %14,7 olan bir işlemde
    yalnız 0,25R'dir. Karar bu ikisini ayırt edemezse "çıkış politikası sorunu" ile "giriş
    kalitesi sorunu" birbirine karışır. Ölçülemezse alan `None` kalır, UYDURULMAZ.
    """
    raw = close.get("raw") or {}
    entry = _f(raw.get("entry"))
    stop = _f((raw.get("features") or {}).get("initial_stop"))
    dist = abs(entry - stop) if (entry is not None and stop is not None) else None
    if not dist or not entry:
        return {"mfe_r": None, "mae_r": None, "stop_distance_pct": None}
    mfe, mae = close.get("mfe_pct"), close.get("mae_pct")
    return {
        "mfe_r": round(abs(mfe) / 100.0 * entry / dist, 4) if mfe is not None else None,
        "mae_r": round(abs(mae) / 100.0 * entry / dist, 4) if mae is not None else None,
        "stop_distance_pct": round(dist / entry * 100.0, 4),
    }


def cost_drags(close: dict[str, Any]) -> dict[str, float | None]:
    """Fee/funding sürüklemesi R cinsinden. Risk ölçülemezse `None` (sessiz 0 YOK)."""
    r, pnl = close.get("r_multiple"), close.get("net_pnl")
    risk = abs(pnl / r) if (r and pnl is not None) else None
    fee, fund = close.get("fees"), close.get("funding")
    return {
        "risk_usdt": round(risk, 6) if risk else None,
        "fee_drag_r": round(fee / risk, 4) if (risk and fee is not None) else None,
        # `funding` alanı NET etkidir (− ödendi, + alındı); sürükleme ters işaretlidir.
        "funding_drag_r": round(-fund / risk, 4) if (risk and fund is not None) else None,
    }


def chain_status(close: dict[str, Any], *, has_outcome: bool, has_lesson: bool,
                 provenance: dict[str, Any] | None) -> dict[str, Any]:
    """Tek bir kapanış için zincir durumu. Sayılar ölçülür, tahmin edilmez."""
    missing: list[str] = []
    if not has_outcome:
        missing.append(STEP_OUTCOME)
    if not has_lesson:
        missing.append(STEP_LESSON)
    if has_outcome and has_lesson:
        state = COMPLETE
    elif not has_outcome and not has_lesson:
        state = MISSING_BOTH
    elif not has_outcome:
        state = MISSING_OUTCOME
    else:
        state = MISSING_LESSON
    link = (provenance or {}).get("link_status") or LEGACY_UNLINKED
    row = {
        "close_event_id": close["close_event_id"],
        "trade_id": close["trade_id"],
        "symbol": close.get("symbol"),
        "side": close.get("side"),
        "opened_at": close.get("opened_at"),
        "closed_at": close.get("closed_at"),
        "exit_reason": close.get("exit_reason"),
        "net_pnl": close.get("net_pnl"),
        "r_multiple": close.get("r_multiple"),
        "tp1_done": close.get("tp1_done"),
        "has_outcome": bool(has_outcome),
        "has_lesson": bool(has_lesson),
        "chain_state": state,
        "missing_steps": missing,
        "link_status": link,
        "entry_decision_id": (provenance or {}).get("entry_decision_id"),
        "entry_code_sha": (provenance or {}).get("entry_code_sha"),
        "entry_p_win": (provenance or {}).get("entry_p_win"),
    }
    row.update(r_normalized(close))
    row.update(cost_drags(close))
    return row


def chain_report(closes: list[dict[str, Any]], *, outcome_ids: Iterable[str],
                 lesson_ids: Iterable[str],
                 provenance: dict[str, dict[str, Any]] | None = None,
                 lesson_id_counts: dict[str, int] | None = None) -> dict[str, Any]:
    """Bütün kapanışlar için zincir raporu. Dashboard ve reconcile AYNI raporu kullanır."""
    out_set = {str(x) for x in outcome_ids}
    les_set = {str(x) for x in lesson_ids}
    prov = provenance or {}
    rows = [chain_status(c, has_outcome=c["trade_id"] in out_set,
                         has_lesson=c["trade_id"] in les_set,
                         provenance=prov.get(c["trade_id"]))
            for c in closes]
    counts = {k: 0 for k in (COMPLETE, MISSING_OUTCOME, MISSING_LESSON, MISSING_BOTH)}
    for r in rows:
        counts[r["chain_state"]] += 1
    dup = {k: v for k, v in (lesson_id_counts or {}).items() if v > 1}
    ids = {c["trade_id"] for c in closes}
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_final_closes": len(closes),
        "outcomes": sum(1 for r in rows if r["has_outcome"]),
        "lessons": sum(1 for r in rows if r["has_lesson"]),
        "entry_linked": sum(1 for r in rows if r["link_status"] == LINKED),
        "legacy_unlinked": sum(1 for r in rows if r["link_status"] == LEGACY_UNLINKED),
        "missing_outcome": counts[MISSING_OUTCOME] + counts[MISSING_BOTH],
        "missing_lesson": counts[MISSING_LESSON] + counts[MISSING_BOTH],
        "complete": counts[COMPLETE],
        "duplicate_lessons": dup,
        "duplicate_lesson_count": sum(v - 1 for v in dup.values()),
        # Deftere karşılık GELMEYEN ders/outcome: veri bütünlüğü uyarısı (sessizce yutulmaz).
        "orphan_lessons": sorted(les_set - ids),
        "orphan_outcomes": sorted(out_set - ids),
        "by_state": counts,
        "rows": rows,
        "note_tr": ("Kanonik kaynak defterdir; kısmi TP final kapanış sayılmaz "
                    "(yalnız `_finalize` history'ye yazar)."),
    }


def pending_work(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Tamamlanması gereken adımlar. Boş liste = zincir eksiksiz."""
    return [{"trade_id": r["trade_id"], "close_event_id": r["close_event_id"],
             "missing_steps": list(r["missing_steps"])}
            for r in (report.get("rows") or []) if r.get("missing_steps")]


__all__ = ["SCHEMA_VERSION", "STEPS", "STEP_OUTCOME", "STEP_LESSON", "STEP_LINK", "legacy_view",
           "COMPLETE", "MISSING_OUTCOME", "MISSING_LESSON", "MISSING_BOTH",
           "LINKED", "LEGACY_UNLINKED",
           "close_event_id", "canonical_closes", "chain_status", "chain_report",
           "pending_work", "r_normalized", "cost_drags"]

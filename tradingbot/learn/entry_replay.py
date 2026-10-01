"""Giriş kararı offline replay sadakat denetimi (`entry_replay_v1`) — FAIL-CLOSED.

Tek soruyu yanıtlar: **mevcut geçmiş veriyle, bir giriş kararı karar anındaki hâliyle sadakatle
yeniden üretilebilir mi?**

Yanıt bugün ÜRETİMDE HAYIRDIR ve bu beklenen sonuçtur. 2026-09-02 ölçümü (VPS, 50 `ACCEPTED`
karar günlüğü kaydı) şunu gösterdi:

* `opportunity` / `conservative_net_edge_r` karar günlüğünde **0/50** — oysa kabul kararını
  veren tek ekonomik büyüklük odur (`opportunity.assess`).
* Likidite alanları **0/50**: `spread_pct`, `est_slippage_pct`, `depth_ratio`, `liquidity_ok`.
* `code_sha`, `config_hash`, `policy_id`, `market_type`, `setup`, `price`, `vetoes`,
  `risk_reasons` → **0/50**.
* `features.stop` yok → stop mesafesi ve dolayısıyla maliyet/risk oranı türetilemiyor.

Bu modül o boşlukları **tam listeyle** raporlar ve `NOT_REPLAYABLE` döner. Eksik alanların
yerine varsayılan koyup "kârlılık" hesaplamak yasaktır: ölçemediğimiz bir eşiği ölçmüş gibi
sunmak, sentetik bir geçmiş uydurmaktır. Bu yüzden bu modülde hiçbir R, PnL ya da beklenti
üretilmez — yalnız **alan bulunabilirliği** ölçülür.

Sadakat yeniden kazanıldığında (yani `entry_snapshot` üretimde birikince) aynı denetim
`LINKED` snapshot kaynağı üzerinden `REPLAYABLE` dönebilir; kapı o zaman kendiliğinden açılır.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from ..core import iso, utc_now
from .entry_snapshot import LEGACY_MEMORY, LINKED

SCHEMA_VERSION = "entry_replay_v1"

REPLAYABLE = "REPLAYABLE"
NOT_REPLAYABLE = "NOT_REPLAYABLE"
NO_DATA = "NO_DATA"

SRC_JOURNAL = "decision_journal"
SRC_MEMORY = "trade_memory"
SRC_SNAPSHOT = "entry_snapshot"

#: Karar anını SADAKATLE yeniden üretmek için zorunlu alanlar. Eksik olan bir tanesi bile
#: replay'i geçersiz kılar: kabul kararı bunların hepsinin bileşimidir.
REQUIRED_FIELDS: tuple[str, ...] = (
    # ekonomi — kabul kararını VEREN büyüklükler
    "conservative_net_edge_r", "net_expectancy_r", "p_win", "avg_win_r", "avg_loss_r",
    "sample_size",
    # plan geometrisi — R'yi tanımlayan iki sayı
    "entry_price", "stop_price", "stop_distance_pct",
    # maliyet ve likidite — net edge'in öteki yarısı
    "expected_cost_pct", "spread_pct", "est_slippage_pct", "depth_ratio", "liquidity_ok",
    # bağlam
    "regime", "consensus_score", "atr_pct", "market_type", "setup",
    # sürüm kimliği — hangi kodun/config'in kararı olduğu bilinmeden replay anlamsızdır
    "code_sha", "config_hash", "policy_version",
    # portföy durumu — E ailesi bunsuz karar veremez
    "portfolio_open_risk_usdt", "same_direction_open",
)


def _present(v: Any) -> bool:
    """Alan GERÇEKTEN dolu mu? `None`, boş dizi/sözlük ve boş metin DOLU SAYILMAZ.

    `0.0` ve `False` DOLUDUR: ölçülmüş bir sıfır ile hiç ölçülmemiş bir alan aynı şey değildir.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return math.isfinite(float(v))
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) > 0
    return True


def field_coverage(rows: Iterable[dict[str, Any]],
                   fields: tuple[str, ...] = REQUIRED_FIELDS) -> dict[str, Any]:
    """Her zorunlu alan için `dolu / toplam` sayımı ve tam eksik listesi."""
    rs = [r for r in rows if isinstance(r, dict)]
    n = len(rs)
    per: dict[str, dict[str, Any]] = {}
    for f in fields:
        filled = sum(1 for r in rs if _present(r.get(f)))
        per[f] = {"filled": filled, "total": n,
                  "ratio": (round(filled / n, 4) if n else None)}
    missing = sorted(f for f, d in per.items() if not n or d["filled"] < n)
    empty = sorted(f for f, d in per.items() if n and d["filled"] == 0)
    return {"n_rows": n, "per_field": per, "missing_fields": missing,
            "completely_empty_fields": empty,
            "complete": bool(n > 0 and not missing)}


def journal_to_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """`decision_journal` satırını denetlenebilir düz alan sözlüğüne indirger.

    Amaç bir snapshot ÜRETMEK değildir — o `entry_snapshot`ın işidir. Amaç, bu kaynağın hangi
    alanları TAŞIDIĞINI dürüstçe ölçmektir; eksik alan burada da uydurulmaz.
    """
    feats = row.get("features") if isinstance(row.get("features"), dict) else {}
    opp = row.get("opportunity") if isinstance(row.get("opportunity"), dict) else {}
    entry = row.get("entry") if isinstance(row.get("entry"), dict) else {}
    return {
        "conservative_net_edge_r": (row.get("conservative_net_edge_r")
                                    or opp.get("conservative_net_edge_r")),
        "net_expectancy_r": opp.get("net_expectancy_r"),
        "p_win": row.get("p_win"),
        "avg_win_r": opp.get("avg_win_r"),
        "avg_loss_r": opp.get("avg_loss_r"),
        "sample_size": opp.get("sample_size"),
        "entry_price": row.get("price") or entry.get("entry"),
        "stop_price": entry.get("stop") or feats.get("stop") or feats.get("initial_stop"),
        "stop_distance_pct": feats.get("stop_pct"),
        "expected_cost_pct": (row.get("execution_cost_estimate")
                              or feats.get("expected_cost_pct")),
        "spread_pct": feats.get("spread_pct"),
        "est_slippage_pct": feats.get("est_slippage_pct"),
        "depth_ratio": feats.get("depth_ratio"),
        "liquidity_ok": feats.get("liquidity_ok"),
        "regime": row.get("regime"),
        "consensus_score": row.get("consensus_score") or row.get("confidence"),
        "atr_pct": feats.get("atr_pct"),
        "market_type": row.get("market_type"),
        "setup": row.get("setup"),
        "code_sha": row.get("code_sha"),
        "config_hash": row.get("config_hash"),
        "policy_version": row.get("policy_id"),
        "portfolio_open_risk_usdt": feats.get("total_open_risk_usdt"),
        "same_direction_open": feats.get("same_direction_open"),
    }


def memory_to_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """`trade_memory` giriş satırını denetlenebilir düz alan sözlüğüne indirger."""
    dec = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    opp = dec.get("opportunity") if isinstance(dec.get("opportunity"), dict) else {}
    feats = row.get("features") if isinstance(row.get("features"), dict) else {}
    snap = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
    vals = snap.get("values") if isinstance(snap.get("values"), dict) else {}
    m = dict(vals) | dict(feats)
    entry, stop = m.get("entry"), (m.get("initial_stop") or m.get("stop"))
    dist = None
    try:
        if entry and stop:
            dist = abs(float(entry) - float(stop)) / float(entry) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        dist = None
    return {
        "conservative_net_edge_r": opp.get("conservative_net_edge_r"),
        "net_expectancy_r": opp.get("net_expectancy_r"),
        "p_win": dec.get("p_win"),
        "avg_win_r": opp.get("avg_win_r"),
        "avg_loss_r": opp.get("avg_loss_r"),
        "sample_size": opp.get("sample_size"),
        "entry_price": entry,
        "stop_price": stop,
        "stop_distance_pct": dist,
        "expected_cost_pct": m.get("expected_cost_pct"),
        "spread_pct": m.get("spread_pct"),
        "est_slippage_pct": m.get("est_slippage_pct"),
        "depth_ratio": m.get("depth_ratio"),
        "liquidity_ok": m.get("liquidity_ok"),
        "regime": row.get("regime") or dec.get("regime"),
        "consensus_score": dec.get("consensus_score"),
        "atr_pct": m.get("atr_pct"),
        "market_type": row.get("market_type"),
        "setup": row.get("setup_type"),
        "code_sha": (row.get("model_versions") or {}).get("code_sha"),
        "config_hash": (row.get("model_versions") or {}).get("config_hash"),
        "policy_version": (row.get("model_versions") or {}).get("policy_id"),
        "portfolio_open_risk_usdt": m.get("total_open_risk_usdt"),
        "same_direction_open": m.get("same_direction_open"),
    }


def replay_audit(*, journal_rows: Iterable[dict[str, Any]] | None = None,
                 memory_rows: Iterable[dict[str, Any]] | None = None,
                 snapshots: Iterable[dict[str, Any]] | None = None,
                 closes: Iterable[dict[str, Any]] | None = None,
                 links: dict[str, str] | None = None, now=None) -> dict[str, Any]:
    """Üç kaynağın replay sadakatini ayrı ayrı ölçer ve FAIL-CLOSED bir hüküm verir.

    Hüküm `REPLAYABLE` yalnız şu koşulda verilir: bir kaynak bütün zorunlu alanları TAM taşıyor
    **ve** her kanonik kapanış o kaynağa bağlanabiliyor. Aksi hâlde `NOT_REPLAYABLE` döner ve
    eksik alanlar tam listeyle bildirilir. Hiçbir koşulda sentetik kârlılık üretilmez.
    """
    jr = [r for r in (journal_rows or []) if isinstance(r, dict)]
    mr = [r for r in (memory_rows or []) if isinstance(r, dict)]
    sr = [r for r in (snapshots or []) if isinstance(r, dict) and r.get("kind") != "link"]
    cl = [c for c in (closes or []) if isinstance(c, dict)]
    lk = dict(links or {})
    sources = {
        SRC_JOURNAL: field_coverage([journal_to_candidate(r) for r in jr]),
        SRC_MEMORY: field_coverage([memory_to_candidate(r) for r in mr]),
        SRC_SNAPSHOT: field_coverage([r for r in sr if r.get("link_status") == LINKED]),
    }
    n_closes = len(cl)
    linked_ids = {str(t) for t in lk}
    close_ids = {str(c.get("trade_id")) for c in cl if c.get("trade_id")}
    n_linked = len(close_ids & linked_ids)
    complete_sources = sorted(k for k, v in sources.items() if v["complete"])
    all_linked = bool(n_closes and n_linked == n_closes)
    if not (jr or mr or sr):
        verdict, why = NO_DATA, "hiçbir kaynakta kayıt yok — sadakat ÖLÇÜLEMEDİ"
    elif complete_sources and all_linked:
        verdict, why = REPLAYABLE, f"tam kaynak: {', '.join(complete_sources)}"
    else:
        parts = []
        if not complete_sources:
            parts.append("hiçbir kaynak zorunlu alanların tamamını taşımıyor")
        if not all_linked:
            parts.append(f"kapanışların {n_linked}/{n_closes}'i karar anına bağlanabiliyor")
        verdict, why = NOT_REPLAYABLE, "; ".join(parts)
    # Bütün kaynaklarda birden BOŞ olan alanlar — asıl kırılma noktası budur.
    empty_everywhere = sorted(
        f for f in REQUIRED_FIELDS
        if all((s["per_field"].get(f) or {}).get("filled", 0) == 0 for s in sources.values()))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now or utc_now()),
        "verdict": verdict,
        "reason_tr": why,
        "required_fields": list(REQUIRED_FIELDS),
        "sources": sources,
        "complete_sources": complete_sources,
        "empty_in_every_source": empty_everywhere,
        "closes": {"total": n_closes, "linked_to_decision": n_linked,
                   "unlinked": sorted(close_ids - linked_ids)[:50]},
        "legacy_snapshots": sum(1 for r in sr if r.get("link_status") == LEGACY_MEMORY),
        "synthetic_profitability": None,
        "note_tr": (
            "Bu denetim YALNIZ alan bulunabilirliğini ölçer. Eksik alanların yerine varsayılan "
            "koyup R/PnL/beklenti üretmek yasaktır: o, ölçülmemiş bir geçmişi ölçülmüş gibi "
            "sunmak olurdu. `NOT_REPLAYABLE` bu aşamada BEKLENEN ve DÜRÜST sonuçtur; tek "
            "başına görevi başarısız kılmaz."),
    }


__all__ = ["SCHEMA_VERSION", "REPLAYABLE", "NOT_REPLAYABLE", "NO_DATA", "REQUIRED_FIELDS",
           "SRC_JOURNAL", "SRC_MEMORY", "SRC_SNAPSHOT", "field_coverage",
           "journal_to_candidate", "memory_to_candidate", "replay_audit"]

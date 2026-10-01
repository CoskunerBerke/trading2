"""Dinamik değerlendirme evreni — 40-50 uygun sembol, gözlemlenebilir koşullarla.

Panelde görünen top liste botun BÜTÜN analiz evreni DEĞİLDİR. Bu modül, tarayıcının zaten
çektiği veriden (YENİ API çağrısı YAPMAZ — rate-limit güvenli) point-in-time bir değerlendirme
evreni türetir: uygun semboller sıralanır, hedef banda (min 40 / hedef 50 / maks 60) kırpılır,
elenenler GEREKÇE KODUYLA listede kalır ve değişimler bir önceki snapshot'a göre kaydedilir.

Kurallar:
* Uygunluk yapay DOLDURULMAZ: uygun sembol 40'ın altındaysa sayı olduğu gibi raporlanır ve
  `below_target_reason` gerçek eksikliği söyler.
* Sabit coin listesi YOKTUR; evren tarayıcının gözlemlenebilir metriklerinden türer
  (hacim, veri bütünlüğü, skor). Sektör uydurulmaz — güvenilir metadata yoksa alan yoktur.
* Snapshot deterministiktir: aynı tarama → aynı doküman → aynı `artifact_sha`.
* Delist edilmişler bugünkü taramada zaten yoktur → `point_in_time` notu survivorship
  sınırını açıkça taşır.
"""
from __future__ import annotations

from typing import Any

from .core import payload_hash

EVAL_UNIVERSE_VERSION = "universe_eval_v1"

#: Eleme gerekçe kodları — journal stage-1 kayıtlarıyla aynı dili konuşur.
DATA_ERROR = "DATA_ERROR"
RANK_BEYOND_CAP = "RANK_BEYOND_CAP"
BELOW_FLAG_SCORE = "BELOW_FLAG_SCORE"
NOT_IN_TOP_N = "NOT_IN_TOP_N"


def build_eval_universe(scan: Any, *, target_min: int = 40, target: int = 50,
                        target_max: int = 60, prev: dict[str, Any] | None = None,
                        run_id: str = "", now_iso: str = "", flag_score: float = 0.0,
                        deep_symbols: tuple[str, ...] = ()) -> dict[str, Any]:
    """Tarama sonucundan (mevcut veriden, ek API çağrısı OLMADAN) evren snapshot'ı üretir.

    `scan`: `scanner.ScanResult` (rows = HATASIZ taranmış semboller, skora göre sıralı;
    veri hatası verenler `universe - scanned` farkı olarak toplam sayıyla raporlanır).
    `flag_score`: tarayıcının işaretleme eşiği (eleme gerekçesi için).
    `deep_symbols`: bu turda derin analize giden semboller (Tier B) — tier etiketi için.
    """
    rows = list(getattr(scan, "rows", []) or [])
    setups = {r.symbol for r in (getattr(scan, "setups", []) or [])}
    # hacme göre sırala (uygunluk sıralaması likidite önceliklidir; skor ayrıca taşınır)
    ranked = sorted(rows, key=lambda r: (-float(getattr(r, "vol24_usdt", 0) or 0),
                                         str(r.symbol)))
    eligible = ranked[:max(0, int(target_max))]
    beyond = ranked[len(eligible):]
    deep = set(deep_symbols)

    symbols: list[dict[str, Any]] = []
    for i, r in enumerate(eligible):
        sym = str(r.symbol)
        if sym in deep:
            tier = "B"
        elif sym in setups:
            tier = "B"
        else:
            tier = "A"
        reason = None
        if tier == "A":
            reason = (BELOW_FLAG_SCORE if (flag_score and float(getattr(r, "score", 0) or 0) < flag_score)
                      else NOT_IN_TOP_N)
        symbols.append({"symbol": sym, "rank": i + 1,
                        "vol24_usdt": round(float(getattr(r, "vol24_usdt", 0) or 0), 2),
                        "scan_score": int(getattr(r, "score", 0) or 0),
                        "atr_pct": getattr(r, "atr_pct", None),
                        "chg24_pct": getattr(r, "chg24_pct", None),
                        "tier": tier, "screen_reason": reason})
    excluded = [{"symbol": str(r.symbol), "reason": RANK_BEYOND_CAP,
                 "vol24_usdt": round(float(getattr(r, "vol24_usdt", 0) or 0), 2)}
                for r in beyond]
    n_data_error = max(0, int(getattr(scan, "universe", 0) or 0) - len(rows))

    prev_syms = {s.get("symbol") for s in ((prev or {}).get("symbols") or [])}
    cur_syms = {s["symbol"] for s in symbols}
    added = sorted(cur_syms - prev_syms)
    removed = sorted(prev_syms - cur_syms)

    n = len(symbols)
    below = None
    if n < int(target_min):
        below = ("INSUFFICIENT_ELIGIBLE_SYMBOLS: tarayıcı yalnız "
                 f"{len(rows)} hatasız sembol üretti (hacim/veri filtreleri gerçek; "
                 "sayı YAPAY doldurulmaz)")
    doc = {"schema_version": EVAL_UNIVERSE_VERSION, "as_of": str(now_iso),
           "run_id": str(run_id),
           "targets": {"min": int(target_min), "target": int(target), "max": int(target_max)},
           "counts": {"eligible": n,
                      "tier_a": sum(1 for s in symbols if s["tier"] == "A"),
                      "tier_b": sum(1 for s in symbols if s["tier"] == "B"),
                      "excluded": len(excluded), "data_error": n_data_error,
                      "scanner_universe": int(getattr(scan, "universe", 0) or 0),
                      "scanned_ok": len(rows)},
           "below_target_reason": below,
           "symbols": symbols, "excluded": excluded,
           "changes": {"added": added, "removed": removed,
                       "prev_as_of": (prev or {}).get("as_of")},
           "provenance": {"source": "market_scanner_ccxt_tickers_ohlcv",
                          "generated_at": str(getattr(scan, "generated_at", "") or ""),
                          "extra_api_calls": 0,
                          "point_in_time": ("bugünün TRADING sembolleri; geçmişte delist "
                                            "edilenler kapsam dışı (survivorship sınırı)"),
                          "sector_metadata": "UNAVAILABLE — uydurulmaz; çeşitlilik "
                                             "hacim/volatilite profiliyle"}}
    doc["artifact_sha"] = payload_hash(doc)[:16]
    return doc


__all__ = ["EVAL_UNIVERSE_VERSION", "BELOW_FLAG_SCORE", "DATA_ERROR", "NOT_IN_TOP_N",
           "RANK_BEYOND_CAP", "build_eval_universe"]

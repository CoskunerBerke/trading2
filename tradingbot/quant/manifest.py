"""Replay/rapor run manifesti (`quant_manifest_v1`) — bir araştırma çıktısının hangi kod, config,
veri ve maliyet varsayımlarıyla üretildiğini TEKRARLANABİLİR biçimde belgeler.

Replay motoruna DOKUNMAZ: mevcut `replay/pipeline.artifact_summary` (dosya SHA'ları) ve gerçek
ledger davranışının beyanını (intrabar politikası, maliyet modeli) tek belgede toplar.

Intrabar gerçeği (kod: `accounting/futures_ledger.py:tick`): aynı barda öncelik sırası
likidasyon → stop → hedefler'dir; stop+TP aynı mumda görünürse STOP kabul edilir (geleceği bilen
iyimser seçim YOK). Gap-through'da fill stop değil mark üzerinden, slippage modeliyle yapılır.
Historical bid/ask yoksa fill'ler yaklaşıktır — `price_approximation` alanı bunu açıkça söyler.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..core import atomic_write_json, payload_hash

SCHEMA_VERSION = "quant_manifest_v1"

#: Ledger'ın kod düzeyinde uyguladığı intrabar politikasının kanonik adı.
INTRABAR_POLICY = "liq_stop_targets_conservative"

#: Bar OHLC'den fill üretildiğinde geçerli yaklaşıklık beyanı.
PRICE_APPROXIMATION = "bar_ohlc_with_slippage_model_no_historical_bidask"


def _sha256_file(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def dataset_fingerprint(paths: list[Path | str]) -> dict[str, Any]:
    """Veri dosyaları → {ad: sha256}. Eksik dosya `null` olarak raporlanır (sessiz atlama yok)."""
    out: dict[str, str | None] = {}
    for p in sorted(str(x) for x in paths):
        out[Path(p).name] = _sha256_file(Path(p))
    return {"files": out, "n_files": len(out), "n_missing": sum(1 for v in out.values() if v is None)}


def cost_model_declaration(cfg: Any | None = None) -> dict[str, Any]:
    """Replay'in kullandığı maliyet modeli beyanı — mevcut V3Config `fees` bölümünden okunur."""
    fees = getattr(cfg, "fees", None)
    return {
        "maker_pct": getattr(fees, "futures_maker_pct", None),
        "taker_pct": getattr(fees, "futures_taker_pct", None),
        "spot_taker_pct": getattr(fees, "spot_taker_pct", None),
        "slippage_bps_fixed": getattr(fees, "slippage_bps", None),
        "funding_model": "settlement_00_08_16_utc_all_missed_periods",
        "intrabar_policy": INTRABAR_POLICY,
        "price_approximation": PRICE_APPROXIMATION,
        "liquidation_check": "bracket_based_worst_price",
    }


def build_manifest(*, run_id: str, code_sha: str, config_obj: Any = None,
                   dataset_paths: list[Path | str] | None = None,
                   start_utc: str | None = None, end_utc: str | None = None,
                   universe_version: str | None = None, seed: int | None = None,
                   feature_schema_version: Any = None, result_obj: Any = None,
                   data_quality: dict[str, Any] | None = None,
                   extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministik manifest üretir (zaman damgası YOK — aynı girdi aynı manifest).

    `data_quality` kapı raporudur; `passed=False` ise manifest `valid_backtest=False` der ve bu
    sonuç asla "geçerli backtest" olarak sunulmamalıdır.
    """
    dq = data_quality or {"passed": None, "checks": [], "note": "data_quality raporu verilmedi"}
    man = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "code_sha": code_sha,
        "config_hash": payload_hash(config_obj) if config_obj is not None else None,
        "dataset": dataset_fingerprint(dataset_paths or []),
        "start_utc": start_utc, "end_utc": end_utc,
        "universe_version": universe_version,
        "cost_model": cost_model_declaration(config_obj),
        "seed": seed,
        "feature_schema_version": feature_schema_version,
        "result_hash": payload_hash(result_obj) if result_obj is not None else None,
        "data_quality": dq,
        "valid_backtest": bool(dq.get("passed")) if dq.get("passed") is not None else False,
        "label": "TEST DATA / RESEARCH — kârlılık kanıtı değildir",
    }
    if extra:
        man["extra"] = extra
    man["manifest_hash"] = payload_hash({k: v for k, v in man.items() if k != "manifest_hash"})
    return man


def write_manifest(path: Path | str, manifest: dict[str, Any]) -> Path:
    atomic_write_json(path, manifest)
    return Path(path)

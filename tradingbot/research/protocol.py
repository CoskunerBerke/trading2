"""Araştırma protokolü — karşılaştırmalardan ÖNCE dondurulan kimlik (`research_protocol_v1`).

Neden: bir karşılaştırma ancak hangi veriyle, hangi kodla, hangi pencerelerle ve hangi
varyantlarla yapıldığı ÖNCEDEN kayıtlıysa yorumlanabilir. Bu modül `research_id`'yi girdi
hash'lerinden deterministik türetir; aynı girdi + aynı kod + aynı config → aynı `research_id`
(önbellek kimliği de budur, dolayısıyla tekrar koşu sonucu ÇOĞALTMAZ).

Denenen bütün varyantlar `attempted_variants` altında kaydedilir — yalnız kazananı raporlamak
yasaktır.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "research_protocol_v1"


def file_sha256(path: Path | str) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ResearchProtocol:
    """Dondurulmuş araştırma sözleşmesi. Karşılaştırma çalıştırılmadan ÖNCE kurulur."""

    run_label: str
    code_sha: str
    cutoff_utc: str
    input_hashes: dict[str, str | None] = field(default_factory=dict)
    universe: list[str] = field(default_factory=list)
    policy_versions: dict[str, Any] = field(default_factory=dict)
    windows: dict[str, Any] = field(default_factory=dict)
    metrics: list[str] = field(default_factory=list)
    seed: int = 7
    #: Önceden kayıtlı deneme sayısı — tek bir holdout'a karşı tekrar tekrar ayar YAPILMAZ.
    max_trials: int = 1
    compute_budget: dict[str, Any] = field(default_factory=dict)
    attempted_variants: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    @property
    def research_id(self) -> str:
        """Girdi + kod + config kimliği. Önbellek anahtarı ve rapor kimliği aynıdır."""
        return stable_hash({
            "schema": SCHEMA_VERSION,
            "code_sha": self.code_sha,
            "cutoff_utc": self.cutoff_utc,
            "inputs": self.input_hashes,
            "universe": sorted(self.universe),
            "policy_versions": self.policy_versions,
            "windows": self.windows,
            "metrics": sorted(self.metrics),
            "seed": self.seed,
            "max_trials": self.max_trials,
        })[:16]

    def record_variant(self, name: str, *, kind: str, params: dict[str, Any] | None = None,
                       outcome: str = "RUN", note: str | None = None) -> None:
        """Denenen HER varyant kaydedilir — başarısız/terk edilenler dahil."""
        self.attempted_variants.append({"name": name, "kind": kind,
                                        "params": dict(params or {}),
                                        "outcome": outcome, "note": note})

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "research_id": self.research_id,
                "run_label": self.run_label, "code_sha": self.code_sha,
                "cutoff_utc": self.cutoff_utc, "input_hashes": dict(self.input_hashes),
                "universe": sorted(self.universe), "policy_versions": dict(self.policy_versions),
                "windows": dict(self.windows), "metrics": list(self.metrics), "seed": self.seed,
                "max_trials": self.max_trials, "compute_budget": dict(self.compute_budget),
                "attempted_variants": list(self.attempted_variants),
                "limitations": list(self.limitations),
                "promotion_effect": "NONE — offline sonuç ileri sayaç artırmaz, kapı karşılamaz"}


__all__ = ["ResearchProtocol", "SCHEMA_VERSION", "file_sha256", "stable_hash"]

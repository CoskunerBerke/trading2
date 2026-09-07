"""Araştırma dersi kataloğu (`research_lesson_v1`) — üretim derslerinden AYRI.

Bu katalog `state/learning.json`, `Learning/Dersler.md` ya da öğrenilmiş indeksi ETKİLEMEZ.
Ayrı bir dosyaya yazılır ve hiçbir ağırlık/eşik/politika değiştirmez.

Her ders ZORUNLU olarak şunları taşır:
* destekleyen VE çelişen işlem/fırsat kimlikleri (yalnız destekleyen kanıt yasak),
* kanıt sınıfı ve eksik ölçüm sayısı,
* örneklem, net etki, belirsizlik,
* alternatif açıklamalar,
* yanlışlanabilir bir sonraki test.

Eksik alan → ders `INCOMPLETE` olur ve rapora "kanıtlanmış bulgu" olarak GİRMEZ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import EVIDENCE_CLASSES

SCHEMA_VERSION = "research_lesson_v1"

REQUIRED = ("lesson_id", "title", "claim", "evidence_class", "sample_size", "net_effect",
            "uncertainty", "supporting_ids", "contradicting_ids", "missing_count",
            "alternative_explanations", "falsifiable_next_test")


@dataclass
class ResearchLesson:
    lesson_id: str
    title: str
    claim: str
    evidence_class: str
    sample_size: int
    net_effect: str
    uncertainty: str
    supporting_ids: list[str] = field(default_factory=list)
    contradicting_ids: list[str] = field(default_factory=list)
    missing_count: int = 0
    alternative_explanations: list[str] = field(default_factory=list)
    falsifiable_next_test: str = ""
    #: Bulgunun olumlu mu olumsuz mu davranışı anlattığı — ikisi de kaydedilir.
    behaviour: str = "NEUTRAL"

    def to_dict(self) -> dict[str, Any]:
        d = {"schema_version": SCHEMA_VERSION, "lesson_id": self.lesson_id, "title": self.title,
             "claim": self.claim, "evidence_class": self.evidence_class,
             "sample_size": int(self.sample_size), "net_effect": self.net_effect,
             "uncertainty": self.uncertainty, "supporting_ids": list(self.supporting_ids),
             "contradicting_ids": list(self.contradicting_ids),
             "missing_count": int(self.missing_count),
             "alternative_explanations": list(self.alternative_explanations),
             "falsifiable_next_test": self.falsifiable_next_test,
             "behaviour": self.behaviour,
             "production_effect": "NONE — üretim ağırlıkları/indeksi DEĞİŞMEZ",
             "counts_toward_promotion": False}
        d["completeness"] = validate(d)
        return d


def validate(lesson: dict[str, Any]) -> dict[str, Any]:
    """Zorunlu alan denetimi — eksikse ders `INCOMPLETE`, sessizce geçmez."""
    missing = [k for k in REQUIRED if lesson.get(k) in (None, "", [], 0)
               and not (k in ("sample_size", "missing_count") and lesson.get(k) == 0)]
    bad_class = lesson.get("evidence_class") not in EVIDENCE_CLASSES
    if bad_class:
        missing.append("evidence_class(geçersiz)")
    # Karşı kanıt aranmamışsa bu AÇIKÇA belirtilmelidir; boş liste tek başına yeterli değildir.
    if not lesson.get("contradicting_ids") and "contradicting_ids" not in missing:
        missing.append("contradicting_ids(boş — karşı kanıt aranmadı mı?)")
    return {"state": ("COMPLETE" if not missing else "INCOMPLETE"), "missing_fields": missing}


def catalog(lessons: list[ResearchLesson]) -> dict[str, Any]:
    rows = [l.to_dict() for l in lessons]
    return {"schema_version": SCHEMA_VERSION, "n": len(rows),
            "n_complete": sum(1 for r in rows if r["completeness"]["state"] == "COMPLETE"),
            "n_incomplete": sum(1 for r in rows if r["completeness"]["state"] == "INCOMPLETE"),
            "lessons": rows,
            "isolation": ("bu katalog üretim ders deposundan AYRIDIR; learned-index, politika "
                          "ağırlıkları ve terfi sayaçları etkilenmez"),
            "behaviour_mix": {b: sum(1 for r in rows if r["behaviour"] == b)
                              for b in sorted({r["behaviour"] for r in rows})}}


__all__ = ["REQUIRED", "SCHEMA_VERSION", "ResearchLesson", "catalog", "validate"]

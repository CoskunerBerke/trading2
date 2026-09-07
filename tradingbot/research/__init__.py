"""Profitability Research Acceleration V1 — OFFLINE ONLY.

Bu paket üretim davranışını DEĞİŞTİRMEZ: worker'a bağlanmaz, canlı state'e yazmaz, emir/config/
risk limiti/politika ağırlığı/learned-index dokunmaz. Yalnız salt okunur bir export dizinini okur
ve verilen çıktı dizinine rapor + önbellek yazar.

Sözleşme (testlerle korunur):
* Her yazım `--out` kökü altındadır; `state/` altına yazım fail-closed reddedilir.
* Sonuçlar kanıt sınıfıyla etiketlenir: CANONICAL_OBSERVED / POINT_IN_TIME /
  RETROSPECTIVE_RESEARCH / MISSING_OR_UNMEASURABLE.
* Eksik kanıt uydurulmaz; ölçülemeyen alan `None` + açık durum kodu olur.
* Offline sonuç hiçbir ileri sayacı artırmaz ve terfi kapısını karşılamaz.
"""
from __future__ import annotations

SCHEMA_VERSION = "profitability_research_v1"
RESEARCH_LABEL = "OFFLINE RESEARCH — üretim davranışı DEĞİŞMEDİ"

#: Kanıt sınıfları — her bulgu bunlardan biriyle etiketlenir.
CANONICAL_OBSERVED = "CANONICAL_OBSERVED"
POINT_IN_TIME = "POINT_IN_TIME"
RETROSPECTIVE_RESEARCH = "RETROSPECTIVE_RESEARCH"
MISSING_OR_UNMEASURABLE = "MISSING_OR_UNMEASURABLE"

EVIDENCE_CLASSES = (CANONICAL_OBSERVED, POINT_IN_TIME, RETROSPECTIVE_RESEARCH,
                    MISSING_OR_UNMEASURABLE)

__all__ = ["CANONICAL_OBSERVED", "EVIDENCE_CLASSES", "MISSING_OR_UNMEASURABLE", "POINT_IN_TIME",
           "RESEARCH_LABEL", "RETROSPECTIVE_RESEARCH", "SCHEMA_VERSION"]

# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 (2026-09-29) — yalnız KAYIT: karar/defter/öğrenici DEĞİŞMEZ.

Bu dosya BİLİNÇLİ olarak hiçbir şey içe aktarmaz: katman KAPALIYKEN (varsayılan) paketin adı anılsa bile alt modüllerden
hiçbiri (situation, cache, …) yüklenmez. Tasarım: docs/ortak_deneyim/SPEC_V1.md §3.1; bağlayıcı kararlar: KARARLAR.md.
"""

#: Satır şeması (her JSONL satırının `schema` alanı) (2026-09-29).
ROW_SCHEMA = "shared_experience_row_v1"
#: Satır türleri; DecisionJournal türleri ("decision", "outcome_link") ile ASLA çakışmaz (2026-09-29).
KINDS = ("xp_entry", "xp_outcome", "xp_cf")
#: Katman sürümü (satır zarfındaki `layer_version`) (2026-09-29).
LAYER_VERSION = "1.0.0"

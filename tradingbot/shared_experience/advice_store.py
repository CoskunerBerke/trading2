# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — AYRI tavsiye deposu (2026-09-29). Yalnız `state/<state_dir>/advice/` altına yazar.

Tavsiye TÜRETİLMİŞ veridir (ana deponun bir fonksiyonu): ana deponun kayıpsız gözlem akışına KARIŞMAZ (geri besleme yok;
`advisor_v2` kalıcı arşivde ikinci kopya bırakmaz; ana deponun baytları danışman AÇIK da KAPALI da olsa aynıdır). Bu yüzden
ana deponun sabitlenmiş `KINDS` / `ROW_SCHEMA` sözleşmesi DEĞİŞMEZ; tavsiye kendi şemasıyla (`shared_experience_advice_v1`,
tür `xp_advice`) kendi akışına yazılır.

`AdviceStore`, `store.ExperienceStore`un aynı toplu / idempotent / kayıpsız döngülü yazım yolunu kullanır; yalnız akış
sözleşmesi (sınıf öznitelikleri) farklıdır: `xp_*` satırları burada RED, `xp_advice` ana depoda RED. Seyreltme KAPALI:
disk tavanının %100'ünde doğrudan DEGRADED (tavsiye yeniden hesaplanabilir; `shared-experience-advisor --mode verify`).
"""
from __future__ import annotations

from pathlib import Path

from .advisor import ADVICE_KIND, ADVICE_SCHEMA
from .store import ExperienceStore

ADVICE_DIR = "advice"
ADVICE_HOT_FILE = "advice.jsonl"
ADVICE_STREAM_ID = "shared_experience_advice"
DEFAULT_ADVICE_HOT_MAX_LINES = 2000
DEFAULT_ADVICE_MAX_TOTAL_MB = 256.0


class AdviceStore(ExperienceStore):
    """Tavsiye akışı (`advice/advice.jsonl` + `advice/archive/`). `row_id` ile idempotent; bir toplu yazım = bir fsync."""

    SCHEMA = ADVICE_SCHEMA
    KINDS_ACCEPTED = (ADVICE_KIND,)
    STREAM_ID = ADVICE_STREAM_ID
    HOT_FILE = ADVICE_HOT_FILE
    THIN_ENABLED = False

    def __init__(self, root: Path | str, *, hot_max_lines: int = DEFAULT_ADVICE_HOT_MAX_LINES,
                 archive_max_segments: int = 0, code_sha: str | None = None,
                 max_total_mb: float | None = DEFAULT_ADVICE_MAX_TOTAL_MB):
        super().__init__(root, hot_max_lines=hot_max_lines, archive_max_segments=archive_max_segments,
                         code_sha=code_sha, max_total_mb=max_total_mb)


__all__ = ["ADVICE_DIR", "ADVICE_HOT_FILE", "ADVICE_STREAM_ID", "AdviceStore", "DEFAULT_ADVICE_HOT_MAX_LINES",
           "DEFAULT_ADVICE_MAX_TOTAL_MB"]

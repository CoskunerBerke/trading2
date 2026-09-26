# -*- coding: utf-8 -*-
"""MUM VARYASYONLARI KAYDI — kullanıcının gönderdiği mum varyasyonlarının TEK listesi, tembel yükleyici ve çalışma
kapısı (2026-09-26).

İÇE AKTARIRKEN HİÇBİR ŞEY AYRIŞTIRILMAZ ve bu modül ASLA hata yükseltmez: `paper_rules` onu içe aktarır, bozuk bir kayıt
diğer defterleri (ana, T2, M2, Box, Formasyon v3, D4) durduramaz. Ayrıştırma ilk `get`/`gate` çağrısında yapılır ve süreç
boyunca önbellekte tutulur (`reset_cache` testler içindir).

Kurallar (docs/CANDLE_VARIATIONS_4H.md):
* Liste EKLEME-YALNIZDIR. Kimlik değişmez; tanım değişirse YENİ kimlik (`supersedes` ile). Ayna (SHORT) ayrı kimliktir.
* `example=True` kayıt kapıdan ASLA geçmez (işlem açmaz); laboratuvarda koşabilir.
* Laboratuvar kaydı `candle_lab_records/<ID>.json` GitHub çıktısından bayt bayt kopyalanır, elle düzenlenmez.
* Kapı sırası: kimlik → tanım → örnek değil → emekli değil → çeviri onayı → laboratuvar kaydı → DSL sürümü/pencere →
  definition_sha → CI kaydı → geçerli kayıt (şema, bilinen hüküm, "yalnız varyasyon" koşusu) → 4h ölçülmüş → çeviri
  onayı sonuçtan ÖNCE → KAYBETTİRİR değil → kullanıcı onayı → onay sonuçtan SONRA → GÜÇLÜ ADAY değilse açık gözlem onayı
  (observation=true).
* "Önce/sonra" GÜN düzeyinde değil, ÇALIŞTIRMAYA bağlıdır: kayıt, koşu anında kayıttaki çeviri onayını taşır (taslak
  koşunun kaydında yoktur → READBACK_AFTER_LAB; sonradan değiştirilen onay da eşleşmez); kullanıcı onayı sonucunu
  gördüğü çalıştırmanın kimliğini taşır (`approval.run_id` = `run.github_run_id`, yoksa APPROVAL_BEFORE_LAB). Tarih
  karşılaştırmaları yedek denetim olarak kalır.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Laboratuvar kayıtları (`<ID>.json`). Başta boştur.
LAB_RECORDS_DIR = Path(__file__).resolve().parent / "candle_lab_records"

#: Kapının ret kodları (sırayla denetlenir). GATE_ERROR yalnız beklenmeyen bir arızada döner (kapı yine yükseltmez).
GATE_REASONS = ("UNKNOWN_ID", "DEFINITION_INVALID", "EXAMPLE", "RETIRED", "NO_READBACK", "NO_LAB_RECORD", "DSL_VERSION_MISMATCH",
                "SHA_MISMATCH", "LAB_NOT_FROM_CI", "LAB_RECORD_INVALID", "LAB_NOT_4H", "READBACK_AFTER_LAB", "VERDICT_LOSS",
                "NO_APPROVAL", "APPROVAL_BEFORE_LAB", "OBSERVATION_NOT_ACKED", "GATE_ERROR")
#: Kaydın şeması (`candle_lab.RECORD_SCHEMA`; burada sabit — kapı laboratuvar modülünü içe aktarmaz).
RECORD_SCHEMA = "candle_lab/1"

# ---------------------------------------------------------------------------- kayıtlar
CV000_EXAMPLE_BULL3: dict[str, Any] = {
    "id": "CV000_EXAMPLE_BULL3",
    "example": True,
    "title_tr": "ÖRNEK (işlem açmaz): düşüş sonrası uzun kırmızı, küçük gövdeli bekleme, hacimli yeşil 1. mumun gövde ortasının "
                "üstünde kapanır",
    "source": {"kind": "example", "received": "2026-09-26", "ref": "tasarım örneği — kullanıcıdan gelmedi"},
    "notes_tr": [
        "c0 'uzun kırmızı' → color bear; body_range ≥ 0.60 (SÖZLÜK uzun gövde); range_atr ≥ 1.3 (SÖZLÜK büyük mum)",
        "c1 'küçük bekleme' → body_range ≤ 0.35 (SÖZLÜK küçük gövde); range_atr ≤ 0.7 (SÖZLÜK küçük mum); gövdesi c0 gövde "
        "ortasının altında",
        "c1 son 10 barın dibini süpürdü → c1.low ≤ min_low(-10..-1)",
        "c2 'hacimli yeşil' → color bull; body_range ≥ 0.60; volume_ratio ≥ 1.5 (SÖZLÜK hacimli); üst fitil ≤ 0.25; kapanış c0 "
        "gövde ortasının üstünde",
        "öncesi düşüş → prior_move 10 bar ≤ −0.5 ATR (SÖZLÜK düşüş sonrası = katalog trend tanımı); RSI14 ≤ 55",
        "teyit c2 kapanışı; stop formasyon dibi − 0.25 ATR; hedef 2R; en çok 24 bar (varsayılan çıkışlar)"],
    "readback": None, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "LONG",
        "timeframe": "4h",
        "bars": [
            {"color": "bear", "body_range": [0.60, None], "range_atr": [1.3, None]},
            {"color": "any", "body_range": [None, 0.35], "range_atr": [None, 0.7]},
            {"color": "bull", "body_range": [0.60, None], "upper_wick_range": [None, 0.25], "volume_ratio": [1.5, None]},
        ],
        "relations": ["c1.body_hi <= c0.body_mid", "c1.low <= min_low(-10..-1)", "c2.close > c0.body_mid"],
        "context": {"prior_move": {"bars": 10, "max": -0.5}, "rsi14": [None, 55]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

#: EKLEME-YALNIZ. Öncelik burada DEĞİL, config listesinin sırasındadır.
VARIATIONS: tuple[dict[str, Any], ...] = (CV000_EXAMPLE_BULL3,)

# ---------------------------------------------------------------------------- tembel yükleyici
#: id → (kayıt nesnesi, Variation | None, ret kodu | None, ayrıntı). Kayıt nesnesi değişirse yeniden ayrıştırılır.
_CACHE: dict[str, tuple[Any, Any, str | None, str]] = {}


def reset_cache() -> None:
    _CACHE.clear()


def _load(vid: Any) -> tuple[Any, str | None, str]:
    """(Variation | None, ret kodu | None, ayrıntı). ASLA yükseltmez."""
    try:
        if not isinstance(vid, str):
            return None, "UNKNOWN_ID", "kimlik metin değil"
        found = [e for e in tuple(VARIATIONS) if isinstance(e, dict) and e.get("id") == vid]
        if not found:
            return None, "UNKNOWN_ID", "kayıtta yok"
        if len(found) > 1:
            return None, "DEFINITION_INVALID", "kimlik kayıtta birden çok kez geçiyor"
        entry = found[0]
        cached = _CACHE.get(vid)
        if cached is not None and cached[0] is entry:
            return cached[1], cached[2], cached[3]
        try:
            from . import candle_dsl
            var, why, detail = candle_dsl.parse_variation(entry), None, ""
        except Exception as exc:  # noqa: BLE001 — bozuk kayıt yalnız kendini kapatır
            var, why, detail = None, "DEFINITION_INVALID", "%s: %s" % (type(exc).__name__, exc)
        _CACHE[vid] = (entry, var, why, detail)
        return var, why, detail
    except Exception as exc:  # noqa: BLE001
        return None, "DEFINITION_INVALID", "%s: %s" % (type(exc).__name__, exc)


def get(vid: str) -> Any:
    """Laboratuvar/CLI için ayrıştırılmış varyasyon — taslak ve örnek DAHİL (kapı uygulanmaz). Bilinmeyen ya da bozuk
    kimlik ValueError verir (laboratuvar sessizce boş rapor üretmesin). Canlı yol `gate` kullanır; o asla yükseltmez."""
    var, why, detail = _load(vid)
    if var is None:
        raise ValueError("%s: %r (%s)" % (why, vid, detail))
    return var


def trials_to_date() -> int:
    """Bugüne kadar denenen varyasyon sayısı (kimliği olan, örnek olmayan tüm kayıtlar; emekliler ve bozuklar DAHİL) —
    çoklu test uyarısı için."""
    try:
        return sum(1 for e in tuple(VARIATIONS) if isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("example") is not True)
    except Exception:  # noqa: BLE001
        return 0


def read_record(vid: str) -> dict[str, Any] | None:
    """`LAB_RECORDS_DIR/<vid>.json` — okunamaz, sözlük değil ya da başka kimliğe aitse None."""
    try:
        from . import candle_dsl
        if not isinstance(vid, str) or not candle_dsl.ID_PATTERN.fullmatch(vid):
            return None
        rec = json.loads((Path(LAB_RECORDS_DIR) / ("%s.json" % vid)).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) and rec.get("id") == vid else None


def _day(x: Any) -> str | None:
    """ISO zaman damgasının tarih kısmı (YYYY-MM-DD); okunamazsa None."""
    import datetime as _dt
    if not isinstance(x, str) or len(x) < 10:
        return None
    try:
        return _dt.date.fromisoformat(x[:10]).isoformat()
    except ValueError:
        return None


def _gate(vid: Any) -> tuple[Any, str | None]:
    var, why, _detail = _load(vid)
    if var is None:
        return None, why
    if var.example:
        return None, "EXAMPLE"
    if var.retired:
        return None, "RETIRED"
    if not var.readback:
        return None, "NO_READBACK"
    rec = read_record(var.id)
    if rec is None:
        return None, "NO_LAB_RECORD"
    from . import candle_dsl
    if rec.get("dsl_version") != candle_dsl.DSL_VERSION or rec.get("window") != candle_dsl.WINDOW:
        return None, "DSL_VERSION_MISMATCH"
    if rec.get("definition_sha") != candle_dsl.definition_sha(var):
        return None, "SHA_MISMATCH"
    run = rec.get("run") if isinstance(rec.get("run"), dict) else {}
    lab_day = _day(run.get("completed_at"))
    if run.get("github_run_id") in (None, "") or lab_day is None:
        return None, "LAB_NOT_FROM_CI"
    from .signal_lab import V_LOSS, V_NONE, V_STRONG, V_THIN, V_WEAK
    verdict = rec.get("verdict")
    mode = run.get("mode") if isinstance(run.get("mode"), dict) else {}
    if rec.get("record_schema") != RECORD_SCHEMA or verdict not in (V_STRONG, V_WEAK, V_NONE, V_THIN, V_LOSS) \
            or mode.get("only_variations") is not True:
        return None, "LAB_RECORD_INVALID"               # katalogla birlikte koşu keşif/doğrulama kesimini değiştirir
    by_tf = rec.get("by_tf") if isinstance(rec.get("by_tf"), dict) else {}
    if rec.get("primary_tf") != candle_dsl.TIMEFRAME or candle_dsl.TIMEFRAME not in by_tf:
        return None, "LAB_NOT_4H"                       # defter yalnız 4h işler; 1h/1d ölçümü yalnız bilgi
    if rec.get("readback") != var.readback or var.readback["date"] > lab_day:
        return None, "READBACK_AFTER_LAB"               # tanım sonuçtan ÖNCE dondurulmuş olmalı (taslak koşu geçmez)
    if verdict == V_LOSS:
        return None, "VERDICT_LOSS"
    if not var.approval:
        return None, "NO_APPROVAL"
    if str(var.approval.get("run_id") or "") != str(run.get("github_run_id")) or var.approval["date"] < lab_day:
        return None, "APPROVAL_BEFORE_LAB"              # onay, sonucu görülen ÇALIŞTIRMAYA bağlı olmalı
    if verdict != V_STRONG and var.approval.get("observation") is not True:
        return None, "OBSERVATION_NOT_ACKED"
    return var, None


def gate(vid: Any) -> tuple[Any, str | None]:
    """Canlı defterin kapısı: (Variation, None) ise işlem açabilir; değilse (None, ret kodu). ASLA yükseltmez — bozuk kayıt
    ya da kayıt dosyası yalnız o varyasyonu kapatır; neden `rule_state`te görünür."""
    try:
        return _gate(vid)
    except Exception:  # noqa: BLE001
        return None, "GATE_ERROR"


__all__ = ["CV000_EXAMPLE_BULL3", "GATE_REASONS", "LAB_RECORDS_DIR", "RECORD_SCHEMA", "VARIATIONS", "gate", "get", "read_record",
           "reset_cache", "trials_to_date"]

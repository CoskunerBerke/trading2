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
* İki hüküm kipi (`gate(vid, verdict_mode)`): `standard` (C4) kaydın `verdict` alanını, `strict` (C4S) `verdict_strict`
  alanını okur. Sıkı kipte denetimler ve sıraları aynıdır; ek olarak sıkı hüküm yoksa `LAB_NO_STRICT_VERDICT`, sıkı ya da
  standart hüküm GÜÇLÜ ADAY değilse `STRICT_NOT_STRONG` (gözlem onayı sıkı deftere varyasyon SOKMAZ). Böylece sıkı
  kapıdan geçen her varyasyon standart kapıdan da geçer (C4S ⊆ C4).
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
#: `UNKNOWN_VERDICT_MODE`, `LAB_NO_STRICT_VERDICT` ve `STRICT_NOT_STRONG` yalnız hüküm kipiyle ilgilidir (ilki her kipte en
#: önce; diğer ikisi yalnız `strict` kipinde).
GATE_REASONS = ("UNKNOWN_VERDICT_MODE", "UNKNOWN_ID", "DEFINITION_INVALID", "EXAMPLE", "RETIRED", "NO_READBACK", "NO_LAB_RECORD",
                "DSL_VERSION_MISMATCH", "SHA_MISMATCH", "LAB_NOT_FROM_CI", "LAB_RECORD_INVALID", "LAB_NO_STRICT_VERDICT",
                "LAB_NOT_4H", "READBACK_AFTER_LAB", "VERDICT_LOSS", "NO_APPROVAL", "APPROVAL_BEFORE_LAB",
                "OBSERVATION_NOT_ACKED", "STRICT_NOT_STRONG", "GATE_ERROR")
#: Ret kodlarının Türkçe etiketleri (panel, günlük, belge).
GATE_REASONS_TR = {
    "UNKNOWN_VERDICT_MODE": "bilinmeyen hüküm kipi (standard ya da strict olmalı)",
    "UNKNOWN_ID": "kimlik kayıtta yok",
    "DEFINITION_INVALID": "tanım bozuk",
    "EXAMPLE": "tasarım örneği (işlem açmaz)",
    "RETIRED": "emekli edildi",
    "NO_READBACK": "çeviri onayı yok",
    "NO_LAB_RECORD": "laboratuvar kaydı yok",
    "DSL_VERSION_MISMATCH": "DSL sürümü ya da pencere uyuşmuyor",
    "SHA_MISMATCH": "tanım mührü (definition_sha) uyuşmuyor",
    "LAB_NOT_FROM_CI": "laboratuvar kaydı CI çalıştırmasından değil",
    "LAB_RECORD_INVALID": "laboratuvar kaydı geçersiz (şema, hüküm ya da koşu kipi)",
    "LAB_NO_STRICT_VERDICT": "kayıtta sıkı hüküm yok (sıkı defter)",
    "LAB_NOT_4H": "4h ölçülmemiş",
    "READBACK_AFTER_LAB": "çeviri onayı laboratuvar sonucundan sonra",
    "VERDICT_LOSS": "hüküm KAYBETTİRİR",
    "NO_APPROVAL": "kullanıcı onayı yok",
    "APPROVAL_BEFORE_LAB": "onay laboratuvar sonucundan önce ya da başka çalıştırmaya ait",
    "OBSERVATION_NOT_ACKED": "GÜÇLÜ ADAY değil ve gözlem onayı yok",
    "STRICT_NOT_STRONG": "sıkı hüküm GÜÇLÜ ADAY değil (sıkı defter gözlem kabul etmez)",
    "GATE_ERROR": "kapıda beklenmeyen arıza",
}
#: Hüküm kipleri: `standard` = kaydın `verdict` alanı (C4), `strict` = `verdict_strict` alanı (C4S).
VERDICT_MODES = ("standard", "strict")
#: Kaydın şeması (`candle_lab.RECORD_SCHEMA`; burada sabit — kapı laboratuvar modülünü içe aktarmaz). Kapı YALNIZ güncel
#: şemayı kabul eder; eski şemalı kayıt `LAB_RECORD_INVALID` (laboratuvar yeniden koşulur).
RECORD_SCHEMA = "candle_lab/2"

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

# ---------------------------------------------------------------------------- 1. parti (CV001–CV008, 2026-09-26)
# Kullanıcının ilettiği strateji listesinin (ChatGPT çıktısı) 1., 2., 4. ve 5. maddeleri; her madde LONG ve SHORT aynası.
# Çeviri kullanıcıya okundu, kullanıcı "en iyi seçeneği yap" diyerek onayladı. Hiçbiri bir defterde etkin DEĞİLDİR
# (config listeleri boş); yalnız laboratuvarda ölçülür. Ortak çıkış bloğu SÖZLÜK varsayılanıdır.
_BATCH1_READBACK_NOTE = "çeviri onayı: kullanıcı 'en iyi seçeneği yap' diyerek onayladı (2026-09-26)"
_BATCH1_EXITS_NOTE = "varsayılan çıkışlar (SÖZLÜK): stop formasyon ucu ± 0.25 ATR, hedef 2R, en çok 24 bar; teyit son mumun kapanışı"


def _batch1_source(item: int) -> dict[str, Any]:
    return {"kind": "text", "received": "2026-09-26",
            "ref": "kullanıcının ilettiği strateji listesi, madde %d (ChatGPT çıktısı); Claude çevirisi" % item}


CV001_BREAKOUT20_TREND_VOL_L: dict[str, Any] = {
    "id": "CV001_BREAKOUT20_TREND_VOL_L",
    "example": False,
    "title_tr": "Trendle 20 bar tepesinin ilk kırılımı, hacmi ≥1,3x ve gövdesi ≥%55 yeşil (LONG)",
    "source": _batch1_source(1),
    "notes_tr": [
        "madde 1; OI filtresi YOK (açık pozisyon verisi yok)",
        "trend → context trend ['with'] (SÖZLÜK trendle: kapanış > EMA50 > EMA200)",
        "20 bar tepesinin kırılımı → c0.close > max_high(-20..-1) (kullanıcının sayısı: 20 bar)",
        "ilk kırılım → max_high(-21..-2) >= max_close(-1..-1): önceki bar kendi 20 bar tepesinin üstünde kapanmamış (varsayım)",
        "güçlü gövde → body_range ≥ 0.55 (kullanıcının sayısı %55)",
        "hacim → volume_ratio ≥ 1.3 (kullanıcının sayısı: 20 bar ortalamasının 1.3 katı)",
        "renk → bull",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "LONG",
        "timeframe": "4h",
        "bars": [{"color": "bull", "body_range": [0.55, None], "volume_ratio": [1.3, None]}],
        "relations": ["c0.close > max_high(-20..-1)", "max_high(-21..-2) >= max_close(-1..-1)"],
        "context": {"trend": ["with"]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV002_BREAKOUT20_TREND_VOL_S: dict[str, Any] = {
    "id": "CV002_BREAKOUT20_TREND_VOL_S",
    "example": False,
    "title_tr": "Trendle 20 bar dibinin ilk kırılımı, hacmi ≥1,3x ve gövdesi ≥%55 kırmızı (SHORT; CV001 aynası)",
    "source": _batch1_source(1),
    "notes_tr": [
        "madde 1 (SHORT aynası); OI filtresi YOK (açık pozisyon verisi yok)",
        "trend → context trend ['with'] (SÖZLÜK trendle: kapanış < EMA50 < EMA200)",
        "20 bar dibinin kırılımı → c0.close < min_low(-20..-1) (kullanıcının sayısı: 20 bar)",
        "ilk kırılım → min_low(-21..-2) <= min_close(-1..-1): önceki bar kendi 20 bar dibinin altında kapanmamış (varsayım)",
        "güçlü gövde → body_range ≥ 0.55 (kullanıcının sayısı %55)",
        "hacim → volume_ratio ≥ 1.3 (kullanıcının sayısı: 20 bar ortalamasının 1.3 katı)",
        "renk → bear",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "SHORT",
        "timeframe": "4h",
        "bars": [{"color": "bear", "body_range": [0.55, None], "volume_ratio": [1.3, None]}],
        "relations": ["c0.close < min_low(-20..-1)", "min_low(-21..-2) <= min_close(-1..-1)"],
        "context": {"trend": ["with"]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV003_PULLBACK_ENGULF_L: dict[str, Any] = {
    "id": "CV003_PULLBACK_ENGULF_L",
    "example": False,
    "title_tr": "Trendde geri çekilme sonrası yutan yeşil (LONG)",
    "source": _batch1_source(2),
    "notes_tr": [
        "madde 2",
        "trend → context trend ['with'] (SÖZLÜK trendle: kapanış > EMA50 > EMA200)",
        "c0 kırmızı, c1 yeşil; c1 gövdesi c0 gövdesini yutar → c1.body_hi >= c0.body_hi, c1.body_lo <= c0.body_lo (SÖZLÜK yutan)",
        "yutan mumun hacmi 'ortalamanın üzerinde' → c1 volume_ratio ≥ 1.0 (kullanıcının sözü)",
        "geri çekilme → RSI14 40–55 (kullanıcının sayıları); 'EMA20/50'ye geri çekilme' DSL'de yazılamaz, RSI bandı onu temsil "
        "eder (varsayım)",
        "giriş yutan mumun kapanışında; formasyon tepesinin kırılımı beklenmedi, çünkü RSI şartı giriş mumuna kayardı (varsayım)",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "LONG",
        "timeframe": "4h",
        "bars": [{"color": "bear"}, {"color": "bull", "volume_ratio": [1.0, None]}],
        "relations": ["c1.body_hi >= c0.body_hi", "c1.body_lo <= c0.body_lo"],
        "context": {"trend": ["with"], "rsi14": [40, 55]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV004_PULLBACK_ENGULF_S: dict[str, Any] = {
    "id": "CV004_PULLBACK_ENGULF_S",
    "example": False,
    "title_tr": "Trendde tepki yükselişi sonrası yutan kırmızı (SHORT; CV003 aynası)",
    "source": _batch1_source(2),
    "notes_tr": [
        "madde 2 (SHORT aynası)",
        "trend → context trend ['with'] (SÖZLÜK trendle: kapanış < EMA50 < EMA200)",
        "c0 yeşil, c1 kırmızı; c1 gövdesi c0 gövdesini yutar → c1.body_hi >= c0.body_hi, c1.body_lo <= c0.body_lo (SÖZLÜK yutan)",
        "yutan mumun hacmi 'ortalamanın üzerinde' → c1 volume_ratio ≥ 1.0 (kullanıcının sözü)",
        "geri çekilme → RSI14 45–60 (kullanıcının sayıları); 'EMA20/50'ye geri çekilme' DSL'de yazılamaz, RSI bandı onu temsil "
        "eder (varsayım)",
        "giriş yutan mumun kapanışında; formasyon dibinin kırılımı beklenmedi, çünkü RSI şartı giriş mumuna kayardı (varsayım)",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "SHORT",
        "timeframe": "4h",
        "bars": [{"color": "bull"}, {"color": "bear", "volume_ratio": [1.0, None]}],
        "relations": ["c1.body_hi >= c0.body_hi", "c1.body_lo <= c0.body_lo"],
        "context": {"trend": ["with"], "rsi14": [45, 60]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV005_SUPPORT_HARAMI_L: dict[str, Any] = {
    "id": "CV005_SUPPORT_HARAMI_L",
    "example": False,
    "title_tr": "Destekte uzun kırmızı + içinde hacmi artan yeşil (boğa harami, LONG)",
    "source": _batch1_source(4),
    "notes_tr": [
        "madde 4",
        "c0 uzun kırmızı → color bear, body_range ≥ 0.60 (SÖZLÜK uzun gövde)",
        "c1 yeşil, gövdesi c0 gövdesinin içinde → c1.body_hi <= c0.body_hi, c1.body_lo >= c0.body_lo (harami)",
        "destek → c0.low <= min_low(-20..-1) + 0.5 atr ve c0.low >= min_low(-20..-1) - 0.5 atr: c0'ın dibi önceki 20 "
        "barın dibine iki yönde de 0.5 ATR'den yakın (kullanıcının onayladığı okuma; desteğin 0.5 ATR'den fazla altına "
        "inen c0 kırılımdır, destek testi değil — süpürme CV007'dir) (varsayım — DSL seviye çizemez)",
        "c1 desteğin üstünde kapanır → c1.close > min_low(-20..-1)",
        "'hacim artıyor' → c1.volume > c0.volume",
        "RSI14 < 40 → rsi14 [None, 40] (kullanıcının sayısı)",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "LONG",
        "timeframe": "4h",
        "bars": [{"color": "bear", "body_range": [0.60, None]}, {"color": "bull"}],
        "relations": ["c1.body_hi <= c0.body_hi", "c1.body_lo >= c0.body_lo", "c0.low <= min_low(-20..-1) + 0.5 atr",
                      "c0.low >= min_low(-20..-1) - 0.5 atr", "c1.close > min_low(-20..-1)", "c1.volume > c0.volume"],
        "context": {"rsi14": [None, 40]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV006_RESIST_HARAMI_S: dict[str, Any] = {
    "id": "CV006_RESIST_HARAMI_S",
    "example": False,
    "title_tr": "Dirençte uzun yeşil + içinde hacmi artan kırmızı (ayı harami, SHORT; CV005 aynası)",
    "source": _batch1_source(4),
    "notes_tr": [
        "madde 4 (SHORT aynası)",
        "c0 uzun yeşil → color bull, body_range ≥ 0.60 (SÖZLÜK uzun gövde)",
        "c1 kırmızı, gövdesi c0 gövdesinin içinde → c1.body_hi <= c0.body_hi, c1.body_lo >= c0.body_lo (harami)",
        "direnç → c0.high >= max_high(-20..-1) - 0.5 atr ve c0.high <= max_high(-20..-1) + 0.5 atr: c0'ın tepesi önceki "
        "20 barın tepesine iki yönde de 0.5 ATR'den yakın (kullanıcının onayladığı okuma; direncin 0.5 ATR'den fazla "
        "üstüne çıkan c0 kırılımdır, direnç testi değil — süpürme CV008'dir) (varsayım — DSL seviye çizemez)",
        "c1 direncin altında kapanır → c1.close < max_high(-20..-1)",
        "'hacim artıyor' → c1.volume > c0.volume",
        "RSI14 > 60 → rsi14 [60, None] (kullanıcının sayısı)",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "SHORT",
        "timeframe": "4h",
        "bars": [{"color": "bull", "body_range": [0.60, None]}, {"color": "bear"}],
        "relations": ["c1.body_hi <= c0.body_hi", "c1.body_lo >= c0.body_lo", "c0.high >= max_high(-20..-1) - 0.5 atr",
                      "c0.high <= max_high(-20..-1) + 0.5 atr", "c1.close < max_high(-20..-1)", "c1.volume > c0.volume"],
        "context": {"rsi14": [60, None]},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV007_SWEEP_REJECT_ENGULF_L: dict[str, Any] = {
    "id": "CV007_SWEEP_REJECT_ENGULF_L",
    "example": False,
    "title_tr": "20 bar dibini süpürüp geri kapanan, alt fitili ≥%45 mum + hacimli yutan yeşil (LONG)",
    "source": _batch1_source(5),
    "notes_tr": [
        "madde 5; trend şartı YOK",
        "c0 dibi süpürür → c0.low < min_low(-20..-1) (kullanıcının sayısı: 20 bar)",
        "c0 dibin üstünde geri kapanır → c0.close > min_low(-20..-1)",
        "c0 reddetme fitili → lower_wick_range ≥ 0.45 (kullanıcının sayısı %45); renk serbest",
        "c1 yeşil, gövdesi c0 gövdesini yutar → c1.body_hi >= c0.body_hi, c1.body_lo <= c0.body_lo (SÖZLÜK yutan)",
        "c1 hacimli → volume_ratio ≥ 1.5 (SÖZLÜK hacimli)",
        "stop süpürme fitilinin altında: formasyon dibi − 0.25 ATR",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "LONG",
        "timeframe": "4h",
        "bars": [{"color": "any", "lower_wick_range": [0.45, None]}, {"color": "bull", "volume_ratio": [1.5, None]}],
        "relations": ["c0.low < min_low(-20..-1)", "c0.close > min_low(-20..-1)", "c1.body_hi >= c0.body_hi",
                      "c1.body_lo <= c0.body_lo"],
        "context": {},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

CV008_SWEEP_REJECT_ENGULF_S: dict[str, Any] = {
    "id": "CV008_SWEEP_REJECT_ENGULF_S",
    "example": False,
    "title_tr": "20 bar tepesini süpürüp geri kapanan, üst fitili ≥%45 mum + hacimli yutan kırmızı (SHORT; CV007 aynası)",
    "source": _batch1_source(5),
    "notes_tr": [
        "madde 5 (SHORT aynası); trend şartı YOK",
        "c0 tepeyi süpürür → c0.high > max_high(-20..-1) (kullanıcının sayısı: 20 bar)",
        "c0 tepenin altında geri kapanır → c0.close < max_high(-20..-1)",
        "c0 reddetme fitili → upper_wick_range ≥ 0.45 (kullanıcının sayısı %45); renk serbest",
        "c1 kırmızı, gövdesi c0 gövdesini yutar → c1.body_hi >= c0.body_hi, c1.body_lo <= c0.body_lo (SÖZLÜK yutan)",
        "c1 hacimli → volume_ratio ≥ 1.5 (SÖZLÜK hacimli)",
        "stop süpürme fitilinin üstünde: formasyon tepesi + 0.25 ATR",
        _BATCH1_EXITS_NOTE,
        _BATCH1_READBACK_NOTE],
    "readback": {"confirmed_by": "user", "date": "2026-09-26"}, "approval": None, "retired": None, "supersedes": None,
    "definition": {
        "side": "SHORT",
        "timeframe": "4h",
        "bars": [{"color": "any", "upper_wick_range": [0.45, None]}, {"color": "bear", "volume_ratio": [1.5, None]}],
        "relations": ["c0.high > max_high(-20..-1)", "c0.close < max_high(-20..-1)", "c1.body_hi >= c0.body_hi",
                      "c1.body_lo <= c0.body_lo"],
        "context": {},
        "confirm": {"kind": "pattern_close"},
        "stop": {"anchor": "pattern", "atr_buffer": 0.25},
        "exit": {"target_r": 2.0, "max_hold_bars": 24},
        "risk_atr_bounds": [0.1, 5.0],
    },
}

#: EKLEME-YALNIZ. Öncelik burada DEĞİL, config listesinin sırasındadır.
VARIATIONS: tuple[dict[str, Any], ...] = (CV000_EXAMPLE_BULL3, CV001_BREAKOUT20_TREND_VOL_L, CV002_BREAKOUT20_TREND_VOL_S,
                                          CV003_PULLBACK_ENGULF_L, CV004_PULLBACK_ENGULF_S, CV005_SUPPORT_HARAMI_L,
                                          CV006_RESIST_HARAMI_S, CV007_SWEEP_REJECT_ENGULF_L, CV008_SWEEP_REJECT_ENGULF_S)

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


def _gate(vid: Any, verdict_mode: str = "standard") -> tuple[Any, str | None]:
    if verdict_mode not in VERDICT_MODES:
        return None, "UNKNOWN_VERDICT_MODE"
    strict = verdict_mode == "strict"
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
    verdict_strict = rec.get("verdict_strict")
    if strict and verdict_strict not in (V_STRONG, V_WEAK, V_NONE, V_THIN, V_LOSS):
        return None, "LAB_NO_STRICT_VERDICT"            # sıkı defter hükmü bilinmeyen kaydı kabul etmez
    by_tf = rec.get("by_tf") if isinstance(rec.get("by_tf"), dict) else {}
    if rec.get("primary_tf") != candle_dsl.TIMEFRAME or candle_dsl.TIMEFRAME not in by_tf:
        return None, "LAB_NOT_4H"                       # defter yalnız 4h işler; 1h/1d ölçümü yalnız bilgi
    if rec.get("readback") != var.readback or var.readback["date"] > lab_day:
        return None, "READBACK_AFTER_LAB"               # tanım sonuçtan ÖNCE dondurulmuş olmalı (taslak koşu geçmez)
    if verdict == V_LOSS or (strict and verdict_strict == V_LOSS):
        return None, "VERDICT_LOSS"
    if not var.approval:
        return None, "NO_APPROVAL"
    if str(var.approval.get("run_id") or "") != str(run.get("github_run_id")) or var.approval["date"] < lab_day:
        return None, "APPROVAL_BEFORE_LAB"              # onay, sonucu görülen ÇALIŞTIRMAYA bağlı olmalı
    if strict:
        # sıkı defter: iki hüküm de GÜÇLÜ ADAY olmalı (gözlem bayrağı burada işlemez) → C4S ⊆ C4
        if verdict_strict != V_STRONG or verdict != V_STRONG:
            return None, "STRICT_NOT_STRONG"
    elif verdict != V_STRONG and var.approval.get("observation") is not True:
        return None, "OBSERVATION_NOT_ACKED"
    return var, None


def verdict_used(vid: Any, verdict_mode: str = "standard") -> str | None:
    """Kipin okuduğu hüküm (`standard` → `verdict`, `strict` → `verdict_strict`); kayıt okunamazsa None. ASLA yükseltmez."""
    try:
        rec = read_record(vid) if isinstance(vid, str) else None
        v = (rec or {}).get("verdict_strict" if verdict_mode == "strict" else "verdict")
        return v if isinstance(v, str) else None
    except Exception:  # noqa: BLE001
        return None


def gate(vid: Any, verdict_mode: str = "standard") -> tuple[Any, str | None]:
    """Canlı defterin kapısı: (Variation, None) ise işlem açabilir; değilse (None, ret kodu). ASLA yükseltmez — bozuk kayıt
    ya da kayıt dosyası yalnız o varyasyonu kapatır; neden `rule_state`te görünür. `verdict_mode`: `standard` (C4) ya da
    `strict` (C4S, `verdict_strict`); başka değer `UNKNOWN_VERDICT_MODE`."""
    try:
        return _gate(vid, verdict_mode)
    except Exception:  # noqa: BLE001
        return None, "GATE_ERROR"


__all__ = ["CV000_EXAMPLE_BULL3", "CV001_BREAKOUT20_TREND_VOL_L", "CV002_BREAKOUT20_TREND_VOL_S", "CV003_PULLBACK_ENGULF_L",
           "CV004_PULLBACK_ENGULF_S", "CV005_SUPPORT_HARAMI_L", "CV006_RESIST_HARAMI_S", "CV007_SWEEP_REJECT_ENGULF_L",
           "CV008_SWEEP_REJECT_ENGULF_S", "GATE_REASONS", "GATE_REASONS_TR", "LAB_RECORDS_DIR", "RECORD_SCHEMA", "VARIATIONS",
           "VERDICT_MODES", "gate", "get", "read_record", "reset_cache", "trials_to_date", "verdict_used"]

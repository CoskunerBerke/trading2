# -*- coding: utf-8 -*-
"""ORTAK YAPI KATALOĞU (`structures_v1`) — beş botun aynı adları, aynı eşikleri ve aynı durum makinesini kullandığı
TEK tanım. Politika matrisi: `docs/structures/POLITIKA_MATRISI_v1.md` (kodlamadan önce yazıldı; eşikler sonuçlara göre
seçilmedi, bu sürümde değişmez).

Tespit burada YENİDEN YAZILMAZ:
* mum şekilleri `learn.candle_context._shapes` (candle_v1.2.0, eşikler `CandleContextConfig`),
* grafik yapıları `chart_patterns.structure_candidates` (eski `detect_chart_patterns` ile aynı aday üreteçleri),
* pivotlar `learn.multitimeframe_context.confirmed_swings` (sağında k kapanmış bar), seviye kümeleri `equal_level_clusters`.
Katalog bunların üzerine bağlam adı (çekiç ↔ asılı adam), taraf, tetik/geçersizlik, durum ve sürüm kimliği ekler.

`geometry_quality` geometrik uyum puanıdır (0..1); kazanma olasılığı DEĞİLDİR. Hiçbir kayıt p_win/beklenti taşımaz.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from ..learn import candle_context as cc

POLICY_VERSION = "structures_v1.1"      # v1.1 (2026-09-23): durum kesinliği + bayrak kimliği; eşikler AYNI
SCHEMA_VERSION = "structures_analysis_v1"

# ---------------------------------------------------------------------------- aileler ve durumlar
FAMILY_CANDLE = "candle"
FAMILY_CHART = "chart"
FAMILY_SCENARIO = "scenario"

ST_FORMING = "FORMING"          # yapı tanındı, tetik kapanışı yok
ST_CONFIRMED = "CONFIRMED"      # tetik seviyesinin ötesinde KAPANIŞ
ST_BROKEN = "BROKEN"            # geçersizlik seviyesinin ötesinde kapanış
ST_EXPIRED = "EXPIRED"          # tetik penceresi doldu ya da teyit bayatladı
STATUSES = (ST_FORMING, ST_CONFIRMED, ST_BROKEN, ST_EXPIRED)
STATUS_TR = {ST_FORMING: "oluşuyor", ST_CONFIRMED: "teyit edildi", ST_BROKEN: "bozuldu", ST_EXPIRED: "süresi doldu"}

LONG, SHORT = "LONG", "SHORT"

ROLE_CONTINUATION = "CONTINUATION"   # yapının tarafı önceki trendle aynı
ROLE_REVERSAL = "REVERSAL"           # yapının tarafı önceki trendin tersi
ROLE_RANGE = "RANGE"                 # önceki trend yok/yatay
ROLE_UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------- mum adları (bağlamla çözülür)
#: `_shapes` etiketinden kanonik ada. İki şekil BAĞLAMA göre iki ada ayrılır (aynı geometri, farklı anlam):
#: çekiç (öncesi düşüş) ↔ asılı adam (öncesi yükseliş); ters çekiç (öncesi düşüş) ↔ kayan yıldız (öncesi yükseliş).
#: Bağlam yoksa (yatay/bilinmiyor) ad `*_SHAPE_NO_TREND` olur ve TARAFI YOKTUR (yön uydurulmaz).
CANDLE_CONTEXT_NAMES = {
    cc.HAMMER_LIKE: {cc.TREND_DOWN: ("HAMMER", LONG), cc.TREND_UP: ("HANGING_MAN", SHORT)},
    cc.INVERTED_HAMMER_LIKE: {cc.TREND_DOWN: ("INVERTED_HAMMER", LONG), cc.TREND_UP: ("SHOOTING_STAR", SHORT)},
}
#: Bağlamdan bağımsız, tek taraflı mum adları (kanonik tanım; akşam yıldızı: 1. uzun BOĞA gövde, 2. küçük gövde,
#: 3. AYI gövde 1. gövdenin ortasının altında kapanır — renk sırası bu kodda `_shapes` ile sabittir).
CANDLE_FIXED_NAMES: dict[str, tuple[str, str | None]] = {
    cc.BULLISH_ENGULFING_LIKE: ("BULLISH_ENGULFING", LONG), cc.BEARISH_ENGULFING_LIKE: ("BEARISH_ENGULFING", SHORT),
    cc.BULLISH_HARAMI_LIKE: ("BULLISH_HARAMI", LONG), cc.BEARISH_HARAMI_LIKE: ("BEARISH_HARAMI", SHORT),
    cc.BULLISH_HARAMI_CROSS_LIKE: ("BULLISH_HARAMI_CROSS", LONG), cc.BEARISH_HARAMI_CROSS_LIKE: ("BEARISH_HARAMI_CROSS", SHORT),
    cc.MORNING_STAR_LIKE: ("MORNING_STAR", LONG), cc.EVENING_STAR_LIKE: ("EVENING_STAR", SHORT),
    cc.MORNING_DOJI_STAR_LIKE: ("MORNING_DOJI_STAR", LONG), cc.EVENING_DOJI_STAR_LIKE: ("EVENING_DOJI_STAR", SHORT),
    cc.BULLISH_ABANDONED_BABY_LIKE: ("BULLISH_ABANDONED_BABY", LONG), cc.BEARISH_ABANDONED_BABY_LIKE: ("BEARISH_ABANDONED_BABY", SHORT),
    cc.BULLISH_DOJI_STAR_LIKE: ("BULLISH_DOJI_STAR", LONG), cc.BEARISH_DOJI_STAR_LIKE: ("BEARISH_DOJI_STAR", SHORT),
    cc.PIERCING_LINE_LIKE: ("PIERCING_LINE", LONG), cc.DARK_CLOUD_COVER_LIKE: ("DARK_CLOUD_COVER", SHORT),
    cc.TWEEZER_BOTTOM_LIKE: ("TWEEZER_BOTTOM", LONG), cc.TWEEZER_TOP_LIKE: ("TWEEZER_TOP", SHORT),
    cc.THREE_WHITE_SOLDIERS_LIKE: ("THREE_WHITE_SOLDIERS", LONG), cc.THREE_BLACK_CROWS_LIKE: ("THREE_BLACK_CROWS", SHORT),
    cc.BULLISH_BELT_HOLD_LIKE: ("BULLISH_BELT_HOLD", LONG), cc.BEARISH_BELT_HOLD_LIKE: ("BEARISH_BELT_HOLD", SHORT),
    cc.BULLISH_KICKER_LIKE: ("BULLISH_KICKER", LONG), cc.BEARISH_KICKER_LIKE: ("BEARISH_KICKER", SHORT),
    cc.BULLISH_MEETING_LINES_LIKE: ("BULLISH_MEETING_LINES", LONG), cc.BEARISH_MEETING_LINES_LIKE: ("BEARISH_MEETING_LINES", SHORT),
    cc.HOMING_PIGEON_LIKE: ("HOMING_PIGEON", LONG), cc.DESCENDING_HAWK_LIKE: ("DESCENDING_HAWK", SHORT),
    # Tarafsızlar: kararsızlık/denge — tek başına yön vermez, TETİK/PLAN üretmez (yalnız bağlam kaydı).
    cc.DOJI_LIKE: ("DOJI", None), cc.SPINNING_TOP_LIKE: ("SPINNING_TOP", None), cc.MARUBOZU_LIKE: ("MARUBOZU", None),
    cc.TRI_STAR_LIKE: ("TRI_STAR", None),
}

# ---------------------------------------------------------------------------- grafik ve senaryo adları
#: Bayrak (PARALEL kanal) ile flama (DARALAN kanal) AYRI adlardır; sınıflanamayan konsolidasyon bayrak SAYILMAZ.
CHART_NAMES = ("DOUBLE_BOTTOM", "DOUBLE_TOP", "TRIPLE_BOTTOM", "TRIPLE_TOP", "INVERSE_HEAD_AND_SHOULDERS",
               "HEAD_AND_SHOULDERS", "ASCENDING_TRIANGLE", "DESCENDING_TRIANGLE", "BULL_FLAG", "BEAR_FLAG",
               "BULL_PENNANT", "BEAR_PENNANT")
SCENARIO_NAMES = ("COMPRESSION_BREAKOUT", "BREAK_RETEST_HOLD", "SWEEP_RECLAIM", "RANGE_BREAKOUT")

#: Belirsiz/sosyal medya adları → bilinen tanım ya da gerekçeli DESTEKLENMİYOR.
ALIASES: dict[str, dict[str, str]] = {
    "PIN_BAR": {"maps_to": "HAMMER | SHOOTING_STAR", "note": "tek uzun fitilli mum; bağlama göre çekiç ya da kayan yıldız"},
    "BULL_TRAP": {"maps_to": "SWEEP_RECLAIM (SHORT)", "note": "yukarı taşma sonra içeride kapanış (başarısız kırılım)"},
    "BEAR_TRAP": {"maps_to": "SWEEP_RECLAIM (LONG)", "note": "aşağı taşma sonra içeride kapanış"},
    "FAKEOUT": {"maps_to": "SWEEP_RECLAIM", "note": "kırılım sonrası aralığa dönüş"},
    "FLAG_OR_PENNANT": {"maps_to": "BULL/BEAR_FLAG | BULL/BEAR_PENNANT", "note": "kanal şekli ayrı ölçülür, tek ad altında birleştirilmez"},
    "LIQUIDITY_GRAB": {"maps_to": "UNSUPPORTED", "note": "aktörün niyeti/bekleyen emirler mumdan bilinemez; yalnız taşma-geri dönüş (SWEEP_RECLAIM) ölçülür"},
    "STOP_HUNT": {"maps_to": "UNSUPPORTED", "note": "gerçek stop emirleri görülmez; niyet iddiası üretilmez"},
    "WOLFE_WAVE": {"maps_to": "UNSUPPORTED", "note": "tanımı öznel, ölçülebilir kurala çevrilmedi (chart_patterns V5 ile aynı karar)"},
}


# ---------------------------------------------------------------------------- sürümlü eşikler
@dataclass(frozen=True)
class StructuresConfig:
    """`structures_v1` eşikleri — politika matrisiyle BİREBİR. Değişiklik yeni `policy_version` ister."""

    policy_version: str = POLICY_VERSION
    candle_lookback_bars: int = 12          # son N kapanmış barda mum kaydı aranır
    candle_trigger_window: int = 3          # mum tetiği bu kadar bar içinde kapanmazsa EXPIRED
    chart_trigger_window: int = 20          # grafik yapısı son pivotun teyidinden sonra bu kadar bar FORMING kalır
    scenario_trigger_window: int = 8        # senaryo penceresi
    fresh_bars: int = 2                     # teyit sonrası karar için taze sayılan kapanmış bar
    stop_buffer_atr: float = 0.25           # stop = geçersizlik ∓ tampon × ATR14 (dilim)
    atr_len: int = 14
    compression_bars: int = 6               # sıkışma: son N bar aralığı ≤ compression_max_atr × ATR
    compression_max_atr: float = 1.0
    retest_window: int = 10                 # kırılımdan sonra seviyeye dönüş aranan bar
    retest_tolerance_atr: float = 0.25      # "seviyeye döndü" toleransı
    breakout_hold_closes: int = 2           # RANGE_BREAKOUT: seviyenin ötesinde ardışık kapanış
    swing_levels: int = 3                   # süpürme/kırılım için bakılan son teyitli tepe/dip sayısı
    zone_tolerance_atr: float = 0.5         # eşit pivot kümesi toleransı
    retracement_ratios: tuple = (0.5, 0.618, 0.705, 0.79)   # BAĞLAM — bu sürümde karar etkisi YOK
    pivot_lookback: int = 3                 # chart_patterns ile aynı (k)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: (list(getattr(self, f.name)) if isinstance(getattr(self, f.name), tuple) else getattr(self, f.name))
                for f in fields(self)}


DEFAULT_CONFIG = StructuresConfig()

#: Dedektör başına GEREKEN kapanmış bar (gerekçeli ret için). Bir dilimin eksikliği başka dilimi ENGELLEMEZ.
REQUIREMENTS = {"candle": 3, "candle_context": 12, "atr": 15, "chart": 2 * 3 + 5 + 2, "compression": 6 + 15,
                "swing_scenarios": 2 * 3 + 2, "levels": 2 * 3 + 1}


def candle_name(shape: str, trend: str) -> tuple[str, str | None]:
    """`_shapes` etiketi + önceki trend → (kanonik ad, taraf|None)."""
    ctx = CANDLE_CONTEXT_NAMES.get(shape)
    if ctx is not None:
        hit = ctx.get(trend)
        if hit is not None:
            return hit
        base = "HAMMER" if shape == cc.HAMMER_LIKE else "INVERTED_HAMMER"
        return base + "_SHAPE_NO_TREND", None
    return CANDLE_FIXED_NAMES.get(shape, (shape.replace("_LIKE", ""), None))


#: Panel/rapor için Türkçe adlar — TEK kaynak (panel ikinci bir ad listesi tutmaz). Eksik ad ham koduyla gösterilir.
NAME_TR: dict[str, str] = {
    "HAMMER": "Çekiç", "HANGING_MAN": "Asılı adam", "INVERTED_HAMMER": "Ters çekiç", "SHOOTING_STAR": "Kayan yıldız",
    "HAMMER_SHAPE_NO_TREND": "Çekiç şekli (trend yok, yönsüz)", "INVERTED_HAMMER_SHAPE_NO_TREND": "Ters çekiç şekli (trend yok, yönsüz)",
    "BULLISH_ENGULFING": "Yutan boğa", "BEARISH_ENGULFING": "Yutan ayı", "BULLISH_HARAMI": "Boğa harami",
    "BEARISH_HARAMI": "Ayı harami", "BULLISH_HARAMI_CROSS": "Boğa harami haç", "BEARISH_HARAMI_CROSS": "Ayı harami haç",
    "MORNING_STAR": "Sabah yıldızı", "EVENING_STAR": "Akşam yıldızı", "MORNING_DOJI_STAR": "Sabah doji yıldızı",
    "EVENING_DOJI_STAR": "Akşam doji yıldızı", "BULLISH_DOJI_STAR": "Boğa doji yıldızı", "BEARISH_DOJI_STAR": "Ayı doji yıldızı",
    "PIERCING_LINE": "Delici hat", "DARK_CLOUD_COVER": "Kara bulut örtüsü", "TWEEZER_BOTTOM": "Cımbız dip",
    "TWEEZER_TOP": "Cımbız tepe", "THREE_WHITE_SOLDIERS": "Üç beyaz asker", "THREE_BLACK_CROWS": "Üç kara karga",
    "BULLISH_BELT_HOLD": "Boğa kuşak tutuşu", "BEARISH_BELT_HOLD": "Ayı kuşak tutuşu", "BULLISH_KICKER": "Boğa tekmesi",
    "BEARISH_KICKER": "Ayı tekmesi", "BULLISH_MEETING_LINES": "Boğa buluşma çizgileri",
    "BEARISH_MEETING_LINES": "Ayı buluşma çizgileri", "HOMING_PIGEON": "Eve dönen güvercin",
    "DESCENDING_HAWK": "Alçalan şahin", "DOJI": "Doji", "SPINNING_TOP": "Topaç", "MARUBOZU": "Marubozu",
    "TRI_STAR": "Üçlü yıldız", "BULLISH_ABANDONED_BABY": "Boğa terk edilmiş bebek",
    "BEARISH_ABANDONED_BABY": "Ayı terk edilmiş bebek",
    "DOUBLE_BOTTOM": "Çift dip", "DOUBLE_TOP": "Çift tepe", "TRIPLE_BOTTOM": "Üçlü dip", "TRIPLE_TOP": "Üçlü tepe",
    "INVERSE_HEAD_AND_SHOULDERS": "Ters omuz-baş-omuz", "HEAD_AND_SHOULDERS": "Omuz-baş-omuz",
    "ASCENDING_TRIANGLE": "Yükselen üçgen", "DESCENDING_TRIANGLE": "Alçalan üçgen", "BULL_FLAG": "Boğa bayrağı",
    "BEAR_FLAG": "Ayı bayrağı", "BULL_PENNANT": "Boğa flaması", "BEAR_PENNANT": "Ayı flaması",
    "COMPRESSION_BREAKOUT": "Sıkışma kırılımı", "BREAK_RETEST_HOLD": "Kırılım · geri test · korunma",
    "SWEEP_RECLAIM": "Taşma ve içeride kapanış", "RANGE_BREAKOUT": "Aralık kırılımı",
}


def name_tr(name: str | None) -> str:
    return NAME_TR.get(str(name or ""), str(name or "—"))


def all_names() -> list[str]:
    """Katalogdaki bütün kanonik adlar (mum bağlamlı + sabit + bağlamsız şekil + grafik + senaryo)."""
    out: list[str] = []
    for d in CANDLE_CONTEXT_NAMES.values():
        out += [n for n, _ in d.values()]
    out += ["HAMMER_SHAPE_NO_TREND", "INVERTED_HAMMER_SHAPE_NO_TREND"]
    out += [n for n, _ in CANDLE_FIXED_NAMES.values()]
    return sorted(set(out) | set(CHART_NAMES) | set(SCENARIO_NAMES))


#: Ortak yapı katmanının modları ve bot anahtarları — config doğrulaması TEK yerde (`validate_settings`).
MODES = ("OFF", "SHADOW", "ENFORCE")
BOT_KEYS = ("main", "t2_trend_regime", "m2_tsmom28", "b1_box_fade", "pattern_trader")
_REAL_MONEY_MODES = ("LIVE", "LIVE_LIMITED")


def validate_settings(*, policy_version: str | None, modes: dict[str, Any], app_mode: str | None) -> dict[str, str]:
    """Config doğrulaması (SAF): normalize edilmiş bot → mod sözlüğü; geçersizse ValueError. `config_v3.validate_v3`
    bunu ConfigError'a sarar (mum/grafik/rejim kapılarıyla AYNI desen). ENFORCE gerçek parayla AÇILAMAZ."""
    if str(policy_version or "") != POLICY_VERSION:
        raise ValueError("structures.policy_version %r desteklenmiyor (kod: %s)" % (policy_version, POLICY_VERSION))
    out: dict[str, str] = {}
    for bot in BOT_KEYS:
        m = str(modes.get(bot) or "OFF").upper()
        if m not in MODES:
            raise ValueError("structures.%s geçersiz mod: %r (%s)" % (bot, modes.get(bot), " | ".join(MODES)))
        if m == "ENFORCE" and str(app_mode or "").upper() in _REAL_MONEY_MODES:
            raise ValueError("STRUCTURES_NOT_VALIDATED_FOR_LIVE: structures.%s=ENFORCE yalnız PAPER/TESTNET/OBSERVE/SHADOW_LIVE" % bot)
        out[bot] = m
    return out


def role_of(side: str | None, trend: str | None) -> str:
    if side not in (LONG, SHORT):
        return ROLE_UNKNOWN
    if trend == cc.TREND_UP:
        return ROLE_CONTINUATION if side == LONG else ROLE_REVERSAL
    if trend == cc.TREND_DOWN:
        return ROLE_CONTINUATION if side == SHORT else ROLE_REVERSAL
    if trend == cc.TREND_RANGE:
        return ROLE_RANGE
    return ROLE_UNKNOWN


def catalog_table() -> dict[str, Any]:
    """Desteklenen adlar, tarafları ve eşikleri — panel/rapor için makine okunur katalog."""
    ccfg = cc.CandleContextConfig()
    return {"policy_version": POLICY_VERSION, "candle_thresholds": ccfg.to_dict(),
            "candles_context": {k: {t: list(v) for t, v in d.items()} for k, d in CANDLE_CONTEXT_NAMES.items()},
            "candles_fixed": {k: list(v) for k, v in CANDLE_FIXED_NAMES.items()},
            "chart": list(CHART_NAMES), "scenarios": list(SCENARIO_NAMES), "aliases": ALIASES,
            "config": DEFAULT_CONFIG.to_dict(), "requirements": dict(REQUIREMENTS),
            "statuses": {s: STATUS_TR[s] for s in STATUSES}}


__all__ = ["ALIASES", "CANDLE_CONTEXT_NAMES", "CANDLE_FIXED_NAMES", "CHART_NAMES", "DEFAULT_CONFIG", "FAMILY_CANDLE",
           "FAMILY_CHART", "FAMILY_SCENARIO", "LONG", "POLICY_VERSION", "REQUIREMENTS", "ROLE_CONTINUATION",
           "ROLE_RANGE", "ROLE_REVERSAL", "ROLE_UNKNOWN", "SCENARIO_NAMES", "SCHEMA_VERSION", "SHORT", "STATUSES",
           "STATUS_TR", "ST_BROKEN", "ST_CONFIRMED", "ST_EXPIRED", "ST_FORMING", "StructuresConfig", "candle_name",
           "BOT_KEYS", "MODES", "NAME_TR", "all_names", "catalog_table", "name_tr", "role_of", "validate_settings"]

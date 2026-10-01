"""Çok zaman dilimli likidite teyidi (`MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1`) —
POINT-IN-TIME, SHADOW, karar UYGULAMAZ.

Hipotez bir eğitim videosundan alınmıştır: **üst zaman dilimi bağlamı, likidite seviyesini ve
hedefi verir; alt zaman dilimi girişi teyit eder.** Video KÂRLILIK KANITI DEĞİLDİR ve burada
yalnız YANLIŞLANABİLİR bir araştırma hipotezi olarak ele alınır. Görsel/grafik iddiaları
körü körüne kopyalanmaz: her kavramın deterministik, karar anında hesaplanabilir ve test
edilebilir bir tanımı vardır.

Desteklenen çift (V1):

* ``D → H1`` — mevcut 1g/1s kareleriyle çalışır, **YENİ SAĞLAYICI İSTEĞİ ÜRETMEZ**.

Şeması tanımlı fakat veri bütçesi doğrulanmadığı için kapalı olan çift:

* ``H4 → M15`` — ``DATA_UNAVAILABLE_ABSTAIN`` (bkz. `docs/MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1.md`).

Kapsam dışı (üretimde HİÇBİR istek üretilmez):

* ``H1 → M5`` ve ``M15 → M1`` — ``FUTURE_RESEARCH_ONLY``.

**Güvenlik sözleşmesi.** Bu modül saftır: hiçbir yere yazmaz, gateway'e dokunmaz, deftere
yazmaz, RiskEngine'i ithal etmez, pozisyon açmaz/kapatmaz, stop/TP/miktar/kaldıraç
değiştirmez ve A–G ailelerinin çıktısını etkilemez. `applied` DAİMA `False`tur.

**Eksik veri sıfır DEĞİLDİR.** Ölçülemeyen her alan `None` kalır, `field_provenance` içinde
`MISSING` olarak işaretlenir ve karar `ABSTAIN` olur. `ABSTAIN` hiçbir zaman `ALLOW` ya da
`VETO` sayılmaz.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

from ..core import stable_id
from .weekly_structure import (ACCEPTED_BREAKOUT, AMBIGUOUS, BREAKOUT_UNCONFIRMED,
                               DATA_UNAVAILABLE, DQ_OK, DQ_PARTIAL, DQ_UNAVAILABLE,
                               HIGH_SWEEP_RECLAIM, LOW_SWEEP_RECLAIM, NO_INTERACTION,
                               SESSION_CRYPTO_CONTINUOUS, SESSION_UNKNOWN, SESSION_WEEKDAY,
                               TOUCH_ONLY, WeeklyStructureConfig, classify_level_interaction,
                               infer_session_profile, rows_from_frame)

SCHEMA_VERSION = "multitimeframe_liquidity_confirmation_v1"

# --- kararlar ----------------------------------------------------------------------------
ALLOW = "ALLOW"
VETO = "VETO"
ABSTAIN = "ABSTAIN"
DECISIONS = (ALLOW, VETO, ABSTAIN)

# --- alan kaynağı ------------------------------------------------------------------------
MEASURED = "MEASURED"
MODELED = "MODELED"
MISSING = "MISSING"
PROVENANCE_KINDS = (MEASURED, MODELED, MISSING)

# --- çiftler -----------------------------------------------------------------------------
PAIR_D_H1 = "D_H1"
PAIR_H4_M15 = "H4_M15"
PAIR_H1_M5 = "H1_M5"
PAIR_M15_M1 = "M15_M1"

#: Üretimde GERÇEKTEN hesaplanan çift. Yeni sağlayıcı isteği üretmez.
SUPPORTED_PAIRS = (PAIR_D_H1,)
#: Şeması tanımlı fakat veri bütçesi doğrulanmadığı için kapalı.
DEFINED_BUT_DISABLED_PAIRS = (PAIR_H4_M15,)
#: Kapsam DIŞI — hiçbir M5/M1 isteği üretilmez.
FUTURE_RESEARCH_ONLY_PAIRS = (PAIR_H1_M5, PAIR_M15_M1)
ALL_PAIRS = SUPPORTED_PAIRS + DEFINED_BUT_DISABLED_PAIRS + FUTURE_RESEARCH_ONLY_PAIRS

#: `çift → (üst çerçeve, alt çerçeve)`.
PAIR_FRAMES: dict[str, tuple[str, str]] = {
    PAIR_D_H1: ("1d", "1h"),
    PAIR_H4_M15: ("4h", "15m"),
    PAIR_H1_M5: ("1h", "5m"),
    PAIR_M15_M1: ("15m", "1m"),
}

#: Çerçeve süresi (ms). Bar `timestamp` alanı AÇILIŞ zamanıdır; kapanış = açılış + süre.
FRAME_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}

# --- gerekçe kodları ---------------------------------------------------------------------
R_OK = "PASSES_FILTER"
R_PAIR_UNSUPPORTED = "PAIR_NOT_SUPPORTED"
R_PAIR_FUTURE_ONLY = "FUTURE_RESEARCH_ONLY"
R_DATA_UNAVAILABLE = "DATA_UNAVAILABLE_ABSTAIN"
R_HTF_MISSING = "HTF_FRAME_MISSING"
R_LTF_MISSING = "LTF_FRAME_MISSING"
R_HTF_STALE = "HTF_FRAME_STALE"
R_LTF_STALE = "LTF_FRAME_STALE"
R_OPEN_CANDLE = "OPEN_CANDLE_REJECTED"
R_INSUFFICIENT_BARS = "INSUFFICIENT_CLOSED_BARS"
R_SESSION_UNKNOWN = "SESSION_UNKNOWN_ABSTAIN"
R_ATR_MISSING = "ATR_UNKNOWN"
R_NO_LIQUIDITY_LEVEL = "NO_LIQUIDITY_LEVEL_FOUND"
R_NO_INTERACTION = "NO_HTF_LEVEL_INTERACTION"
R_INTERACTION_AMBIGUOUS = "HTF_INTERACTION_AMBIGUOUS"
R_TWO_SIDED = "TWO_SIDED_INTERACTION_AMBIGUOUS"
R_ACCEPTED_BREAKOUT_NOT_SWEEP = "ACCEPTED_BREAKOUT_NOT_A_SWEEP"
R_HTF_SUPPORTS = "HTF_HYPOTHESIS_SUPPORTS_DIRECTION"
R_HTF_OPPOSES = "HTF_HYPOTHESIS_OPPOSES_DIRECTION"
R_DIRECTION_UNKNOWN = "BASELINE_DIRECTION_UNKNOWN"
R_LTF_NO_SHIFT = "LTF_STRUCTURE_SHIFT_ABSENT"
R_LTF_SHIFT_ALIGNED = "LTF_STRUCTURE_SHIFT_ALIGNED"
R_LTF_SHIFT_OPPOSES = "LTF_STRUCTURE_SHIFT_OPPOSES_DIRECTION"
R_LTF_AMBIGUOUS = "LTF_STRUCTURE_AMBIGUOUS"
R_DISPLACEMENT_LOW = "DISPLACEMENT_BELOW_THRESHOLD"
R_CLOSE_LOCATION_LOW = "CLOSE_LOCATION_BELOW_THRESHOLD"
R_RETEST_MISSING = "RETEST_REQUIRED_BUT_ABSENT"
R_RETEST_LATE = "RETEST_TOO_LATE"
R_RETEST_OPPOSITE = "RETEST_INVALIDATED_LEVEL"
R_RETEST_PENDING = "RETEST_WINDOW_NOT_ELAPSED"
R_NO_TARGET = "NO_STRUCTURAL_TARGET_ABSTAIN"
R_INVALID_GEOMETRY = "INVALID_GEOMETRY_ABSTAIN"
R_RR_BELOW_MIN = "STRUCTURAL_RR_BELOW_MINIMUM"
R_PROVIDER_FAILURE = "PROVIDER_OR_CACHE_FAILURE"

# --- üst zaman dilimi hipotezi -----------------------------------------------------------
HTF_BULLISH = "BULLISH"
HTF_BEARISH = "BEARISH"
HTF_NEUTRAL = "NEUTRAL"
HTF_UNKNOWN = "UNKNOWN"

# --- alt zaman dilimi yapı durumu --------------------------------------------------------
LTF_SHIFT_UP = "SHIFT_UP"
LTF_SHIFT_DOWN = "SHIFT_DOWN"
LTF_NO_SHIFT = "NO_SHIFT"
LTF_UNKNOWN = "UNKNOWN"

# --- retest durumları --------------------------------------------------------------------
RETEST_CONFIRMED = "RETEST_CONFIRMED"
RETEST_ABSENT = "RETEST_ABSENT"
RETEST_LATE = "RETEST_LATE"
RETEST_INVALIDATED = "RETEST_INVALIDATED"
RETEST_PENDING = "RETEST_PENDING"
RETEST_UNKNOWN = "RETEST_UNKNOWN"

# --- likidite kaynağı --------------------------------------------------------------------
SRC_SWING = "CONFIRMED_SWING"
SRC_EQUAL_CLUSTER = "EQUAL_LEVEL_CLUSTER"
SRC_PREV_PERIOD = "PREVIOUS_CLOSED_PERIOD_EXTREME"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _is_long(direction: Any) -> bool | None:
    d = str(direction or "").upper()
    if d.endswith("LONG"):
        return True
    if d.endswith("SHORT"):
        return False
    return None


def _r(v: float | None, nd: int = 6) -> float | None:
    return None if v is None else round(v, nd)


# =========================================================================== yapılandırma

@dataclass
class MultiTimeframeConfig:
    """Versiyonlu eşikler — `config_id`ye girer, koda gömülü DEĞİLDİR.

    Eşikler **sonuçlara bakılarak seçilmemiştir**. Hiçbir değer F00030'u VETO yapmak için
    ayarlanmamıştır; F00030'un değişmez giriş snapshot'ı H'den ÖNCEDİR ve zaten terfi kanıtı
    sayılamaz.
    """
    policy_version: str = "mtf_v1.0.0"
    variant: str = "H_BALANCED"

    # --- likidite seviyesi tespiti ------------------------------------------------------
    #: Fraktal salınım teyidi için her iki yanda gereken KAPANMIŞ bar sayısı.
    swing_lookback: int = 2
    #: Eşit yüksek/düşük kümesi toleransı (ATR katı).
    equal_level_atr_tolerance: float = 0.10
    #: Seviye adayı taramasında bakılacak azami üst çerçeve barı.
    htf_scan_bars: int = 120
    #: Etkileşim penceresi: seviyeden sonra bakılacak azami alt çerçeve barı.
    ltf_interaction_bars: int = 48

    # --- süpürme / kırılım --------------------------------------------------------------
    #: Aşımın gürültüden ayrılması için asgari mesafe (ATR katı).
    min_sweep_atr: float = 0.10
    #: Kapanışın seviyenin ötesinde sayılması için gereken tampon (ATR katı).
    breakout_close_buffer_atr: float = 0.05
    #: Kabul edilmiş kırılım için gereken KAPANMIŞ teyit sayısı.
    required_breakout_closes: int = 1
    #: Aşımın "kabul edilmiş kırılım" sayılması için asgari mesafe (ATR katı).
    accepted_breakout_atr: float = 0.75

    # --- alt çerçeve teyidi -------------------------------------------------------------
    #: Yer değiştirme: gövde / ATR asgari.
    min_displacement_body_atr: float = 0.50
    #: Yer değiştirme: aralık / ATR asgari.
    min_displacement_range_atr: float = 0.70
    #: Kapanışın mum aralığı içindeki konumu (0..1) asgari — yön lehine.
    close_location_threshold: float = 0.60
    #: Retest için izin verilen azami KAPANMIŞ bar sayısı.
    retest_bar_limit: int = 6
    #: Retest zorunlu mu (varyant sözleşmesi).
    require_retest: bool = False
    #: Yapı kayması sonrası bakılacak azami bar (bayat teyit sayılmaması için).
    max_bars_since_shift: int = 12

    # --- yapısal geometri ---------------------------------------------------------------
    #: Stop, süpürme uç noktasının bu kadar ATR ötesine konur (yalnız ARAŞTIRMA).
    stop_atr_buffer: float = 0.25
    #: Asgari yapısal R:R. Altındaysa varyant sözleşmesine göre VETO ya da ABSTAIN.
    min_structural_rr: float = 1.5
    #: R:R eşiği altında VETO mu üretilsin (aksi hâlde ABSTAIN).
    veto_on_low_rr: bool = True

    # --- veri tazeliği ------------------------------------------------------------------
    #: Üst çerçevenin son kapanmış barı bu kadar çerçeve süresinden eskiyse BAYAT.
    max_htf_staleness_frames: float = 3.0
    #: Alt çerçeve için aynı tavan.
    max_ltf_staleness_frames: float = 6.0
    #: Hesap için gereken asgari kapanmış bar sayısı (her iki çerçeve).
    min_htf_bars: int = 30
    min_ltf_bars: int = 30

    def validate(self) -> None:
        if self.swing_lookback < 1:
            raise ValueError("swing_lookback >= 1 olmalı")
        if self.equal_level_atr_tolerance < 0:
            raise ValueError("equal_level_atr_tolerance negatif olamaz")
        if self.min_sweep_atr <= 0:
            raise ValueError("min_sweep_atr pozitif olmalı")
        if self.accepted_breakout_atr <= self.min_sweep_atr:
            raise ValueError("accepted_breakout_atr, min_sweep_atr'den büyük olmalı")
        if self.breakout_close_buffer_atr < 0:
            raise ValueError("breakout_close_buffer_atr negatif olamaz")
        if self.required_breakout_closes < 1:
            raise ValueError("required_breakout_closes >= 1 olmalı")
        if self.min_displacement_body_atr <= 0 or self.min_displacement_range_atr <= 0:
            raise ValueError("yer değiştirme eşikleri pozitif olmalı")
        if not (0.0 < self.close_location_threshold <= 1.0):
            raise ValueError("close_location_threshold (0, 1] aralığında olmalı")
        if self.retest_bar_limit < 1 or self.max_bars_since_shift < 1:
            raise ValueError("bar sayıları >= 1 olmalı")
        if self.stop_atr_buffer < 0:
            raise ValueError("stop_atr_buffer negatif olamaz")
        if self.min_structural_rr <= 0:
            raise ValueError("min_structural_rr pozitif olmalı")
        if self.htf_scan_bars < 10 or self.ltf_interaction_bars < 5:
            raise ValueError("tarama pencereleri çok küçük")
        if self.min_htf_bars < 1 or self.min_ltf_bars < 1:
            raise ValueError("asgari bar sayıları >= 1 olmalı")
        if self.max_htf_staleness_frames <= 0 or self.max_ltf_staleness_frames <= 0:
            raise ValueError("bayatlık tavanları pozitif olmalı")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "MultiTimeframeConfig":
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in dict(d or {}).items() if k in allowed})
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        return stable_id("mtfcfg", self.policy_version, self.variant, self.to_dict())


#: **ÖNCEDEN TAAHHÜT EDİLMİŞ VARYANTLAR.** Dördü de sonuçlara BAKILMADAN tanımlanmıştır ve
#: hepsi aynı anda gölgede ölçülür. "En iyisi" sonuçlara bakılarak SEÇİLMEZ; her biri kendi
#: terfi kapılarından ayrı ayrı geçmek zorundadır.
CONFIG_VARIANTS: tuple[dict[str, Any], ...] = (
    {"variant": "H_LENIENT", "min_displacement_body_atr": 0.30,
     "min_displacement_range_atr": 0.45, "close_location_threshold": 0.50,
     "min_structural_rr": 1.0, "veto_on_low_rr": False},
    {"variant": "H_BALANCED"},
    {"variant": "H_STRICT", "min_displacement_body_atr": 0.75,
     "min_displacement_range_atr": 1.00, "close_location_threshold": 0.70,
     "min_sweep_atr": 0.20, "min_structural_rr": 2.0},
    {"variant": "H_RETEST_REQUIRED", "require_retest": True, "retest_bar_limit": 4},
)

VARIANT_NAMES = tuple(v["variant"] for v in CONFIG_VARIANTS)


def build_variants(base: dict[str, Any] | None = None) -> list[MultiTimeframeConfig]:
    base = dict(base or {})
    base.pop("variant", None)
    return [MultiTimeframeConfig.from_dict(base | v) for v in CONFIG_VARIANTS]


# =========================================================== KATI POINT-IN-TIME süzgeci

def closed_bars_as_of(frame: Any, *, frame_key: str, as_of_ms: int | None
                      ) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Yalnız `bar.close_time <= as_of_ms` koşulunu sağlayan KAPANMIŞ barlar.

    İki koruma AYNI ANDA uygulanır (sözleşme gereği):

    1. **Kapanmamış bar atılır** — `timestamp + çerçeve_süresi` gelecekteyse bar açıktır.
    2. **Açık `as_of_ms` süzgeci** — karar anından sonraki hiçbir bar görülemez.

    Sunucu saati ile borsa bar saati AYRI tutulur: `as_of_ms` karar anıdır (sunucu), bar
    zamanı borsa açılış damgasıdır. İkisi burada açıkça birbirine karıştırılmaz.
    """
    ms = FRAME_MS.get(str(frame_key))
    rows = rows_from_frame(frame)
    meta = {"frame": frame_key, "frame_ms": ms, "as_of_ms": as_of_ms,
            "rows_in": len(rows), "dropped_future": 0, "dropped_unclosed": 0,
            "last_closed_at_ms": None, "kept": 0}
    if ms is None:
        meta["reason"] = f"UNKNOWN_FRAME:{frame_key}"
        return [], meta
    if as_of_ms is None:
        meta["reason"] = "AS_OF_UNKNOWN"
        return [], meta
    kept: list[dict[str, float]] = []
    for b in rows:
        open_ms = b["timestamp"]
        close_ms = open_ms + ms
        if open_ms > as_of_ms:
            meta["dropped_future"] += 1
            continue
        if close_ms > as_of_ms:                 # bar HENÜZ KAPANMADI
            meta["dropped_unclosed"] += 1
            continue
        kept.append(b)
    meta["kept"] = len(kept)
    if kept:
        meta["last_closed_at_ms"] = int(kept[-1]["timestamp"] + ms)
        meta["last_open_at_ms"] = int(kept[-1]["timestamp"])
    return kept, meta


def staleness_frames(last_closed_ms: int | None, as_of_ms: int | None, frame_key: str
                     ) -> float | None:
    """Son kapanıştan bu yana geçen süre, çerçeve süresi cinsinden. Ölçülemezse `None`."""
    ms = FRAME_MS.get(str(frame_key))
    if last_closed_ms is None or as_of_ms is None or not ms:
        return None
    return round(max(0.0, (as_of_ms - last_closed_ms) / float(ms)), 4)


# ================================================================= MEKANİK TANIMLAR

def confirmed_swings(bars: list[dict[str, float]], *, lookback: int
                     ) -> dict[str, list[dict[str, Any]]]:
    """Fraktal salınım yüksek/düşükleri — **GELECEK BAR KULLANILMADAN** teyit edilir.

    `i` indeksindeki bir salınım, her iki yanında `lookback` KAPANMIŞ bar bulunduğunda teyit
    olur; teyit indeksi `i + lookback`tir. Buradaki sağ taraf barları karar anından SONRAKİ
    barlar değildir — hepsi `as_of_ms`ten önce kapanmıştır. Bir salınım, kendi teyit indeksinden
    ÖNCE kullanılamaz; bunu `confirmed_at_index` alanı zorunlu kılar.
    """
    k = max(1, int(lookback))
    highs: list[dict[str, Any]] = []
    lows: list[dict[str, Any]] = []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        c = bars[i]
        if c["high"] == max(b["high"] for b in w) and \
                sum(1 for b in w if b["high"] == c["high"]) == 1:
            highs.append({"index": i, "confirmed_at_index": i + k, "level": c["high"],
                          "timestamp": c["timestamp"], "side": "high"})
        if c["low"] == min(b["low"] for b in w) and \
                sum(1 for b in w if b["low"] == c["low"]) == 1:
            lows.append({"index": i, "confirmed_at_index": i + k, "level": c["low"],
                         "timestamp": c["timestamp"], "side": "low"})
    return {"highs": highs, "lows": lows}


def equal_level_clusters(swings: list[dict[str, Any]], *, atr: float | None,
                         tolerance_atr: float) -> list[dict[str, Any]]:
    """ATR toleransı içinde kümelenen eşit yüksek/düşükler — likidite yığılması adayı.

    Tek bir salınım küme SAYILMAZ: en az iki salınım gerekir. ATR ölçülemezse küme
    ÜRETİLMEZ (tolerans uydurulmaz).
    """
    a = _f(atr)
    if a is None or a <= 0 or len(swings) < 2:
        return []
    tol = max(0.0, float(tolerance_atr)) * a
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for i, s in enumerate(swings):
        if i in used:
            continue
        members = [s]
        for j in range(i + 1, len(swings)):
            if j in used:
                continue
            if abs(swings[j]["level"] - s["level"]) <= tol:
                members.append(swings[j])
                used.add(j)
        if len(members) >= 2:
            used.add(i)
            lv = [m["level"] for m in members]
            out.append({"level": round(sum(lv) / len(lv), 10), "n_members": len(members),
                        "member_timestamps": [int(m["timestamp"]) for m in members],
                        "confirmed_at_index": max(m["confirmed_at_index"] for m in members),
                        "spread": round(max(lv) - min(lv), 10), "tolerance": round(tol, 10),
                        "side": s["side"], "source": SRC_EQUAL_CLUSTER})
    return out


def previous_closed_period_extremes(bars: list[dict[str, float]]) -> dict[str, Any]:
    """TAMAMLANMIŞ son üst çerçeve barının yüksek/düşüğü — en yalın likidite referansı."""
    if not bars:
        return {"high": None, "low": None, "timestamp": None, "source": SRC_PREV_PERIOD}
    b = bars[-1]
    return {"high": b["high"], "low": b["low"], "timestamp": int(b["timestamp"]),
            "source": SRC_PREV_PERIOD}


def liquidity_levels(htf_bars: list[dict[str, float]], *, atr: float | None,
                     cfg: MultiTimeframeConfig) -> dict[str, Any]:
    """Üst çerçeve likidite seviyeleri — YALNIZ kayıtlı kaynaklardan.

    Kaynak ve destekleyen zaman damgaları AÇIKÇA kaydedilir; "burada likidite var" iddiası
    kaynaksız üretilmez.
    """
    scan = htf_bars[-max(10, int(cfg.htf_scan_bars)):]
    sw = confirmed_swings(scan, lookback=cfg.swing_lookback)
    hi_clusters = equal_level_clusters(sw["highs"], atr=atr,
                                       tolerance_atr=cfg.equal_level_atr_tolerance)
    lo_clusters = equal_level_clusters(sw["lows"], atr=atr,
                                       tolerance_atr=cfg.equal_level_atr_tolerance)
    prev = previous_closed_period_extremes(scan)

    def pick(swings: list[dict[str, Any]], clusters: list[dict[str, Any]],
             prev_level: float | None, side: str) -> dict[str, Any]:
        # Öncelik: eşit-seviye kümesi (en çok emir birikimi) → en son teyitli salınım →
        # önceki tamamlanmış dönem ucu. Seçim SONUÇLARA bakılarak yapılmaz.
        if clusters:
            best = max(clusters, key=lambda c: (c["n_members"], c["confirmed_at_index"]))
            return {"level": best["level"], "source": SRC_EQUAL_CLUSTER,
                    "supporting_timestamps": best["member_timestamps"],
                    "n_members": best["n_members"]}
        if swings:
            best = swings[-1]
            return {"level": best["level"], "source": SRC_SWING,
                    "supporting_timestamps": [int(best["timestamp"])], "n_members": 1}
        if prev_level is not None:
            return {"level": prev_level, "source": SRC_PREV_PERIOD,
                    "supporting_timestamps": ([prev["timestamp"]] if prev["timestamp"]
                                              else []), "n_members": 1}
        return {"level": None, "source": None, "supporting_timestamps": [], "n_members": 0}

    return {
        "buy_side": pick(sw["highs"], hi_clusters, prev["high"], "high"),
        "sell_side": pick(sw["lows"], lo_clusters, prev["low"], "low"),
        "n_swing_highs": len(sw["highs"]), "n_swing_lows": len(sw["lows"]),
        "n_high_clusters": len(hi_clusters), "n_low_clusters": len(lo_clusters),
        "previous_period": prev, "scanned_bars": len(scan),
    }


def structure_shift(ltf_bars: list[dict[str, float]], *, cfg: MultiTimeframeConfig,
                    atr: float | None) -> dict[str, Any]:
    """Alt çerçeve yapı kayması — **KAPANIŞ** temellidir, fitil yetmez.

    * Yükseliş: kapanmış bir mum, DAHA ÖNCE TEYİT EDİLMİŞ bir salınım yükseğinin üstünde
      kapanır.
    * Düşüş: simetrik.

    Salınım, kendi teyit indeksinden SONRAKİ bir mumla kırılmalıdır: teyit edilmemiş bir
    seviyeyi "kırmak" geriye dönük bir iddia olurdu.
    """
    out: dict[str, Any] = {"state": LTF_UNKNOWN, "shift": False, "direction": None,
                           "level": None, "shift_index": None, "shift_timestamp": None,
                           "bars_since_shift": None, "reason": None,
                           "n_bars": len(ltf_bars)}
    if len(ltf_bars) < (2 * int(cfg.swing_lookback) + 2):
        out["reason"] = R_INSUFFICIENT_BARS
        return out
    sw = confirmed_swings(ltf_bars, lookback=cfg.swing_lookback)
    a = _f(atr)
    buf = (max(0.0, cfg.breakout_close_buffer_atr) * a) if (a and a > 0) else 0.0
    last = len(ltf_bars) - 1

    best_up = best_dn = None
    for s in sw["highs"]:
        for j in range(s["confirmed_at_index"] + 1, len(ltf_bars)):
            if ltf_bars[j]["close"] > s["level"] + buf:
                if best_up is None or j > best_up[0]:
                    best_up = (j, s["level"])
                break
    for s in sw["lows"]:
        for j in range(s["confirmed_at_index"] + 1, len(ltf_bars)):
            if ltf_bars[j]["close"] < s["level"] - buf:
                if best_dn is None or j > best_dn[0]:
                    best_dn = (j, s["level"])
                break

    out["close_buffer"] = round(buf, 10)
    out["n_swing_highs"] = len(sw["highs"])
    out["n_swing_lows"] = len(sw["lows"])
    if best_up is None and best_dn is None:
        out.update({"state": LTF_NO_SHIFT, "reason": R_LTF_NO_SHIFT})
        return out
    # En SON gerçekleşen kayma geçerlidir; ikisi de aynı barda ise belirsizdir.
    if best_up is not None and best_dn is not None and best_up[0] == best_dn[0]:
        out.update({"state": LTF_UNKNOWN, "reason": R_LTF_AMBIGUOUS})
        return out
    up_i = best_up[0] if best_up else -1
    dn_i = best_dn[0] if best_dn else -1
    if up_i > dn_i:
        idx, lv, st, d = best_up[0], best_up[1], LTF_SHIFT_UP, "LONG"
    else:
        idx, lv, st, d = best_dn[0], best_dn[1], LTF_SHIFT_DOWN, "SHORT"
    out.update({"state": st, "shift": True, "direction": d, "level": lv,
                "shift_index": idx, "shift_timestamp": int(ltf_bars[idx]["timestamp"]),
                "bars_since_shift": last - idx, "reason": R_LTF_SHIFT_ALIGNED})
    return out


def displacement(bar: dict[str, float] | None, *, atr: float | None, is_up: bool | None
                 ) -> dict[str, Any]:
    """ATR ile normalize edilmiş yer değiştirme. **Büyük mum tek başına yetmez.**

    Gövde/ATR, aralık/ATR, mum içi kapanış konumu ve yön hizası AYRI AYRI ölçülür.
    """
    out = {"body_atr": None, "range_atr": None, "close_location": None,
           "directionally_aligned": None, "body": None, "range": None, "atr": _f(atr)}
    a = _f(atr)
    if not bar or a is None or a <= 0 or is_up is None:
        return out
    rng = bar["high"] - bar["low"]
    body = abs(bar["close"] - bar["open"])
    out["body"] = round(body, 10)
    out["range"] = round(rng, 10)
    out["body_atr"] = round(body / a, 6)
    out["range_atr"] = round(rng / a, 6)
    if rng > 0:
        loc = (bar["close"] - bar["low"]) / rng if is_up else (bar["high"] - bar["close"]) / rng
        out["close_location"] = round(loc, 6)
    out["directionally_aligned"] = bool((bar["close"] > bar["open"]) == bool(is_up)
                                        and bar["close"] != bar["open"])
    return out


def retest(ltf_bars: list[dict[str, float]], *, shift_index: int | None, level: float | None,
           is_up: bool | None, atr: float | None, cfg: MultiTimeframeConfig) -> dict[str, Any]:
    """Kırılan yapı seviyesinin retesti — **yalnız KAPANMIŞ barlarla**, deterministik.

    Yükseliş için: kayma barından sonraki `retest_bar_limit` bar içinde bir bar seviyeye
    tolerans dâhilinde geri döner (`low <= seviye + tol`) ve **seviyenin üstünde kapanır**.
    Seviyenin altında kapanan bir bar yapıyı GEÇERSİZ kılar. Takdire dayalı "order block" /
    "FVG" etiketleri KULLANILMAZ: her koşul sayısal ve tekrarlanabilirdir.
    """
    out = {"state": RETEST_UNKNOWN, "level": level, "bars_checked": 0,
           "retest_index": None, "retest_timestamp": None, "tolerance": None,
           "bars_available_after_shift": None}
    a = _f(atr)
    lv = _f(level)
    if shift_index is None or lv is None or is_up is None or a is None or a <= 0:
        return out
    tol = max(0.0, cfg.equal_level_atr_tolerance) * a
    out["tolerance"] = round(tol, 10)
    after = ltf_bars[shift_index + 1:]
    out["bars_available_after_shift"] = len(after)
    limit = max(1, int(cfg.retest_bar_limit))
    for i, b in enumerate(after):
        out["bars_checked"] = i + 1
        broke_back = (b["close"] < lv - tol) if is_up else (b["close"] > lv + tol)
        if broke_back:
            out["state"] = RETEST_INVALIDATED
            out["retest_index"] = shift_index + 1 + i
            out["retest_timestamp"] = int(b["timestamp"])
            return out
        touched = (b["low"] <= lv + tol) if is_up else (b["high"] >= lv - tol)
        held = (b["close"] > lv) if is_up else (b["close"] < lv)
        if touched and held:
            out["retest_index"] = shift_index + 1 + i
            out["retest_timestamp"] = int(b["timestamp"])
            out["state"] = RETEST_CONFIRMED if i < limit else RETEST_LATE
            return out
    # Hiç retest görülmedi: pencere dolduysa YOK, dolmadıysa HENÜZ BELLİ DEĞİL.
    out["state"] = RETEST_ABSENT if len(after) >= limit else RETEST_PENDING
    return out


def structural_geometry(*, entry: float | None, sweep_extreme: float | None,
                        target: float | None, is_long: bool | None, atr: float | None,
                        cfg: MultiTimeframeConfig) -> dict[str, Any]:
    """Yapısal giriş/stop/hedef ve R:R — **YALNIZ ARAŞTIRMA**.

    Gerçek pozisyonun stop'u, TP'si ve miktarı bu fonksiyondan ETKİLENMEZ; burada üretilen
    sayılar hiçbir emre dönüşmez.
    """
    out: dict[str, Any] = {"entry": _f(entry), "stop": None, "target": _f(target),
                           "rr": None, "risk": None, "reward": None,
                           "stop_source": None, "valid": False, "reason": None}
    px, ext, tgt, a = _f(entry), _f(sweep_extreme), _f(target), _f(atr)
    if px is None or is_long is None:
        out["reason"] = R_INVALID_GEOMETRY
        return out
    if a is None or a <= 0:
        out["reason"] = R_ATR_MISSING
        return out
    if ext is None:
        out["reason"] = R_INVALID_GEOMETRY
        return out
    buf = max(0.0, cfg.stop_atr_buffer) * a
    stop = (ext - buf) if is_long else (ext + buf)
    out["stop"] = round(stop, 10)
    out["stop_source"] = "SWEEP_EXTREME_PLUS_ATR_BUFFER"
    out["stop_atr_buffer"] = round(buf, 10)
    if (is_long and stop >= px) or ((not is_long) and stop <= px):
        out["reason"] = "STOP_ON_WRONG_SIDE"
        return out
    if tgt is None:
        out["reason"] = R_NO_TARGET
        return out
    if (is_long and tgt <= px) or ((not is_long) and tgt >= px):
        out["reason"] = "TARGET_ON_WRONG_SIDE"
        return out
    risk = abs(px - stop)
    reward = abs(tgt - px)
    if risk <= 0:
        out["reason"] = R_INVALID_GEOMETRY
        return out
    out.update({"risk": round(risk, 10), "reward": round(reward, 10),
                "rr": round(reward / risk, 6), "valid": True, "reason": R_OK})
    return out


# ============================================================ ANA BAĞLAM ÜRETİCİSİ

def _session_for(htf_bars: list[dict[str, float]], *, frame_key: str,
                 cfg: MultiTimeframeConfig) -> dict[str, Any]:
    """Seans profili ÖLÇÜLÜR, varsayılmaz. Günlük olmayan çerçevede çıkarım yapılmaz."""
    if frame_key == "1d":
        return infer_session_profile(htf_bars, probe_weeks=6)
    return {"profile": SESSION_CRYPTO_CONTINUOUS if htf_bars else SESSION_UNKNOWN,
            "expected_bars_per_week": None, "observed_weeks": 0,
            "reason": "INTRADAY_FRAME_CONTINUOUS_ASSUMED_ONLY_FOR_UTC_LABEL"}


def build_mtf_context(*, symbol: Any, baseline_direction: Any, as_of_ms: int | None,
                      pair: str, htf_frame: Any, ltf_frame: Any,
                      htf_atr: float | None = None, ltf_atr: float | None = None,
                      current_price: float | None = None,
                      candidate_id: Any = None, decision_id: Any = None,
                      code_sha: str | None = None,
                      cfg: MultiTimeframeConfig | None = None) -> dict[str, Any]:
    """Bir aday için karar anı çok-zaman-dilimli bağlamı. **Saf fonksiyon.**

    Hiçbir yere yazmaz, sonucu GÖRMEZ, aktif hiçbir kararı değiştirmez. Ölçülemeyen alan
    `None` kalır ve karar `ABSTAIN` olur — sıfıra düşürme YOKTUR.
    """
    cfg = cfg or MultiTimeframeConfig()
    prov: dict[str, str] = {}
    reasons: list[str] = []
    frames = PAIR_FRAMES.get(str(pair))
    htf_key, ltf_key = frames if frames else (None, None)

    rec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": cfg.policy_version,
        "variant": cfg.variant,
        "config_id": cfg.config_id,
        "code_sha": code_sha,
        "candidate_id": (str(candidate_id) if candidate_id is not None else None),
        "decision_id": (str(decision_id) if decision_id is not None else None),
        "symbol": (str(symbol) if symbol is not None else None),
        "baseline_direction": (str(baseline_direction) if baseline_direction is not None
                               else None),
        "as_of_ms": (int(as_of_ms) if as_of_ms is not None else None),
        "as_of": (datetime.fromtimestamp(as_of_ms / 1000.0, tz=timezone.utc).isoformat()
                  if as_of_ms is not None else None),
        "pair": str(pair),
        "htf_frame": htf_key,
        "ltf_frame": ltf_key,
        "applied": False,
        "written_at_stage": "RANKING",
        "sees_outcome": False,
        "note_tr": ("SHADOW: karşı-olgusal bağlam; aktif giriş/emir yolunu ETKİLEMEZ. "
                    "Video bir kârlılık kanıtı DEĞİLDİR."),
    }
    # Bütün şema alanları, hesaplanamasa bile ŞEMADA VARDIR (geriye dönük okuyucu kırılmaz).
    for k in ("htf_data_quality", "ltf_data_quality", "htf_last_closed_at", "ltf_last_closed_at",
              "htf_bar_count", "ltf_bar_count", "htf_trend", "htf_atr", "htf_liquidity_side",
              "htf_liquidity_level", "htf_liquidity_source", "htf_interaction",
              "htf_sweep_distance_atr", "htf_reclaim", "htf_accepted_breakout",
              "htf_target_level", "htf_target_distance_r", "ltf_structure_state",
              "ltf_structure_shift", "ltf_shift_direction", "ltf_displacement_body_atr",
              "ltf_displacement_range_atr", "ltf_close_location", "ltf_retest_state",
              "ltf_retest_level", "ltf_bars_since_shift", "structural_entry",
              "structural_stop", "structural_target", "structural_rr",
              "hypothesis_direction", "alignment_with_baseline", "ltf_atr", "session_profile"):
        rec[k] = None
        prov[k] = MISSING

    def finish(decision: str) -> dict[str, Any]:
        rec["decision"] = decision
        rec["reason_codes"] = list(dict.fromkeys(reasons)) or [R_OK]
        rec["field_provenance"] = prov
        rec["missing_fields"] = sorted(k for k, v in prov.items() if v == MISSING)
        rec["n_missing"] = len(rec["missing_fields"])
        return rec

    def setf(key: str, value: Any, kind: str = MEASURED) -> None:
        rec[key] = value
        prov[key] = kind if value is not None else MISSING

    # --- çift kapsamı ------------------------------------------------------------------
    if str(pair) in FUTURE_RESEARCH_ONLY_PAIRS:
        reasons.append(R_PAIR_FUTURE_ONLY)
        rec["scope"] = R_PAIR_FUTURE_ONLY
        return finish(ABSTAIN)
    if frames is None:
        reasons.append(R_PAIR_UNSUPPORTED)
        return finish(ABSTAIN)
    if str(pair) in DEFINED_BUT_DISABLED_PAIRS:
        rec["scope"] = R_DATA_UNAVAILABLE
    else:
        rec["scope"] = "SUPPORTED"

    if as_of_ms is None:
        reasons.append(R_DATA_UNAVAILABLE)
        return finish(ABSTAIN)

    # --- KATI POINT-IN-TIME süzgeci ------------------------------------------------------
    htf_bars, htf_meta = closed_bars_as_of(htf_frame, frame_key=htf_key, as_of_ms=as_of_ms)
    ltf_bars, ltf_meta = closed_bars_as_of(ltf_frame, frame_key=ltf_key, as_of_ms=as_of_ms)
    rec["point_in_time"] = {"htf": htf_meta, "ltf": ltf_meta,
                            "closed_bar_rule": "bar.close_time <= as_of_ms",
                            "server_time_vs_bar_time": "DISTINCT",
                            "timezone": "UTC"}
    setf("htf_bar_count", len(htf_bars))
    setf("ltf_bar_count", len(ltf_bars))
    setf("htf_last_closed_at", htf_meta.get("last_closed_at_ms"))
    setf("ltf_last_closed_at", ltf_meta.get("last_closed_at_ms"))
    if htf_meta.get("dropped_unclosed") or ltf_meta.get("dropped_unclosed"):
        reasons.append(R_OPEN_CANDLE)

    if not htf_bars:
        reasons.append(R_HTF_MISSING)
        setf("htf_data_quality", DQ_UNAVAILABLE)
    if not ltf_bars:
        reasons.append(R_LTF_MISSING)
        setf("ltf_data_quality", DQ_UNAVAILABLE)
    if not htf_bars or not ltf_bars:
        return finish(ABSTAIN)

    # --- tazelik ------------------------------------------------------------------------
    htf_stale = staleness_frames(htf_meta.get("last_closed_at_ms"), as_of_ms, htf_key)
    ltf_stale = staleness_frames(ltf_meta.get("last_closed_at_ms"), as_of_ms, ltf_key)
    rec["htf_staleness_frames"] = htf_stale
    rec["ltf_staleness_frames"] = ltf_stale
    htf_dq = DQ_OK
    ltf_dq = DQ_OK
    if len(htf_bars) < cfg.min_htf_bars:
        htf_dq = DQ_PARTIAL
        reasons.append(R_INSUFFICIENT_BARS)
    if len(ltf_bars) < cfg.min_ltf_bars:
        ltf_dq = DQ_PARTIAL
        reasons.append(R_INSUFFICIENT_BARS)
    if htf_stale is not None and htf_stale > cfg.max_htf_staleness_frames:
        htf_dq = DQ_PARTIAL
        reasons.append(R_HTF_STALE)
    if ltf_stale is not None and ltf_stale > cfg.max_ltf_staleness_frames:
        ltf_dq = DQ_PARTIAL
        reasons.append(R_LTF_STALE)
    setf("htf_data_quality", htf_dq)
    setf("ltf_data_quality", ltf_dq)

    # --- seans: doğrulanamıyorsa ABSTAIN (borsa/emtia için seans UYDURULMAZ) -------------
    sess = _session_for(htf_bars, frame_key=htf_key, cfg=cfg)
    setf("session_profile", sess.get("profile"))
    rec["session"] = sess
    if sess.get("profile") == SESSION_UNKNOWN:
        reasons.append(R_SESSION_UNKNOWN)
        return finish(ABSTAIN)

    # --- ATR: ölçülemezse hiçbir normalizasyon YAPILMAZ ---------------------------------
    h_atr, l_atr = _f(htf_atr), _f(ltf_atr)
    setf("htf_atr", h_atr)
    setf("ltf_atr", l_atr)
    if h_atr is None or h_atr <= 0:
        reasons.append(R_ATR_MISSING)
        return finish(ABSTAIN)

    is_long = _is_long(baseline_direction)
    if is_long is None:
        reasons.append(R_DIRECTION_UNKNOWN)

    # --- HTF likidite seviyeleri ---------------------------------------------------------
    liq = liquidity_levels(htf_bars, atr=h_atr, cfg=cfg)
    rec["liquidity_levels"] = liq
    buy_lv = _f(liq["buy_side"]["level"])
    sell_lv = _f(liq["sell_side"]["level"])
    if buy_lv is None and sell_lv is None:
        reasons.append(R_NO_LIQUIDITY_LEVEL)
        return finish(ABSTAIN)

    # --- Etkileşim: HTF seviyeleri, LTF KAPANMIŞ barlarıyla ölçülür ----------------------
    wcfg = WeeklyStructureConfig(
        touch_tolerance_atr=cfg.equal_level_atr_tolerance,
        min_sweep_atr=cfg.min_sweep_atr,
        accepted_breakout_atr=cfg.accepted_breakout_atr,
        reclaim_confirm_bars=cfg.required_breakout_closes,
        max_bars_to_reclaim=cfg.retest_bar_limit)
    win = ltf_bars[-max(5, int(cfg.ltf_interaction_bars)):]
    hi_i = classify_level_interaction(level=buy_lv, bars=win, side="high", atr=h_atr, cfg=wcfg)
    lo_i = classify_level_interaction(level=sell_lv, bars=win, side="low", atr=h_atr, cfg=wcfg)
    rec["htf_high_interaction"] = hi_i
    rec["htf_low_interaction"] = lo_i
    hi_c, lo_c = hi_i["classification"], lo_i["classification"]

    # --- HTF hipotezi ---------------------------------------------------------------------
    # Sözleşme: satış-tarafı (alt) likiditenin süpürülüp GERİ ALINMASI YÜKSELİŞ bağlamıdır;
    # alış-tarafı (üst) likiditenin süpürülüp geri alınması DÜŞÜŞ bağlamıdır. Kabul edilmiş
    # kırılım ASLA süpürme sayılmaz ve sonradan hindsight ile yeniden etiketlenemez.
    bull_sweep = (lo_c == LOW_SWEEP_RECLAIM)
    bear_sweep = (hi_c == HIGH_SWEEP_RECLAIM)
    accepted_up = (hi_c == ACCEPTED_BREAKOUT)
    accepted_dn = (lo_c == ACCEPTED_BREAKOUT)
    setf("htf_accepted_breakout", bool(accepted_up or accepted_dn))

    if bull_sweep and bear_sweep:
        reasons.append(R_TWO_SIDED)
        setf("htf_trend", HTF_UNKNOWN)
        return finish(ABSTAIN)
    if AMBIGUOUS in (hi_c, lo_c) and not (bull_sweep or bear_sweep):
        reasons.append(R_INTERACTION_AMBIGUOUS)
        setf("htf_trend", HTF_UNKNOWN)
        return finish(ABSTAIN)

    if bull_sweep:
        hyp, side, lvl, src = HTF_BULLISH, "sell_side", sell_lv, liq["sell_side"]["source"]
        inter, sweep_atr = lo_i, lo_i.get("sweep_distance_atr")
    elif bear_sweep:
        hyp, side, lvl, src = HTF_BEARISH, "buy_side", buy_lv, liq["buy_side"]["source"]
        inter, sweep_atr = hi_i, hi_i.get("sweep_distance_atr")
    elif accepted_up:
        hyp, side, lvl, src = HTF_BULLISH, "buy_side", buy_lv, liq["buy_side"]["source"]
        inter, sweep_atr = hi_i, hi_i.get("sweep_distance_atr")
        reasons.append(R_ACCEPTED_BREAKOUT_NOT_SWEEP)
    elif accepted_dn:
        hyp, side, lvl, src = HTF_BEARISH, "sell_side", sell_lv, liq["sell_side"]["source"]
        inter, sweep_atr = lo_i, lo_i.get("sweep_distance_atr")
        reasons.append(R_ACCEPTED_BREAKOUT_NOT_SWEEP)
    elif hi_c in (NO_INTERACTION, TOUCH_ONLY, DATA_UNAVAILABLE) and \
            lo_c in (NO_INTERACTION, TOUCH_ONLY, DATA_UNAVAILABLE):
        reasons.append(R_NO_INTERACTION)
        setf("htf_trend", HTF_NEUTRAL)
        return finish(ABSTAIN)
    else:                      # BREAKOUT_UNCONFIRMED vb. — yön iddiası taşımaz
        reasons.append(R_INTERACTION_AMBIGUOUS)
        setf("htf_trend", HTF_UNKNOWN)
        return finish(ABSTAIN)

    setf("htf_trend", hyp)
    setf("hypothesis_direction", "LONG" if hyp == HTF_BULLISH else "SHORT")
    setf("htf_liquidity_side", side)
    setf("htf_liquidity_level", lvl)
    setf("htf_liquidity_source", src)
    setf("htf_interaction", inter.get("classification"))
    setf("htf_sweep_distance_atr", _f(sweep_atr))
    setf("htf_reclaim", inter.get("reclaim_confirmed"))

    # --- hedef: KARŞI taraftaki doğrulanmış HTF likiditesi -------------------------------
    tgt = buy_lv if hyp == HTF_BULLISH else sell_lv
    setf("htf_target_level", tgt)

    # --- baseline hizası -------------------------------------------------------------------
    if is_long is not None:
        aligned = (is_long and hyp == HTF_BULLISH) or ((not is_long) and hyp == HTF_BEARISH)
        setf("alignment_with_baseline", "ALIGNED" if aligned else "OPPOSED")
        reasons.append(R_HTF_SUPPORTS if aligned else R_HTF_OPPOSES)
    else:
        aligned = None

    # --- LTF yapı kayması ------------------------------------------------------------------
    sh = structure_shift(ltf_bars, cfg=cfg, atr=l_atr)
    rec["ltf_structure"] = sh
    setf("ltf_structure_state", sh["state"])
    setf("ltf_structure_shift", bool(sh["shift"]))
    setf("ltf_shift_direction", sh.get("direction"))
    setf("ltf_bars_since_shift", sh.get("bars_since_shift"))

    shift_bar = (ltf_bars[sh["shift_index"]] if sh.get("shift_index") is not None else None)
    disp = displacement(shift_bar, atr=l_atr, is_up=(None if sh.get("direction") is None
                                                     else sh["direction"] == "LONG"))
    rec["ltf_displacement"] = disp
    setf("ltf_displacement_body_atr", disp["body_atr"])
    setf("ltf_displacement_range_atr", disp["range_atr"])
    setf("ltf_close_location", disp["close_location"])

    rt = retest(ltf_bars, shift_index=sh.get("shift_index"), level=sh.get("level"),
                is_up=(None if sh.get("direction") is None else sh["direction"] == "LONG"),
                atr=l_atr, cfg=cfg)
    rec["ltf_retest"] = rt
    setf("ltf_retest_state", rt["state"])
    setf("ltf_retest_level", _f(rt.get("level")))

    # --- yapısal geometri (YALNIZ ARAŞTIRMA) ----------------------------------------------
    entry_px = _f(current_price)
    if entry_px is None and ltf_bars:
        entry_px = ltf_bars[-1]["close"]
    extreme = (_f(inter.get("level")) if inter else None)
    if inter is not None:
        # Süpürme uç noktası: seviyenin aşıldığı mesafe kadar ötesi (ÖLÇÜLMÜŞ aşım).
        exc = _f(inter.get("sweep_distance"))
        if extreme is not None and exc is not None:
            extreme = (extreme - exc) if hyp == HTF_BULLISH else (extreme + exc)
    geo = structural_geometry(entry=entry_px, sweep_extreme=extreme, target=tgt,
                              is_long=(hyp == HTF_BULLISH), atr=h_atr, cfg=cfg)
    rec["structural_plan"] = geo
    setf("structural_entry", geo["entry"])
    setf("structural_stop", geo["stop"], MODELED)
    setf("structural_target", geo["target"])
    setf("structural_rr", geo["rr"], MODELED)
    if geo.get("risk"):
        setf("htf_target_distance_r", _r((geo["reward"] or 0.0) / geo["risk"]), MODELED)

    # --- KARAR ------------------------------------------------------------------------------
    return finish(_decide(rec, sh=sh, disp=disp, rt=rt, geo=geo, cfg=cfg,
                          is_long=is_long, aligned=aligned, reasons=reasons))


def _decide(rec: dict[str, Any], *, sh: dict[str, Any], disp: dict[str, Any],
            rt: dict[str, Any], geo: dict[str, Any], cfg: MultiTimeframeConfig,
            is_long: bool | None, aligned: bool | None, reasons: list[str]) -> str:
    """`ALLOW` / `VETO` / `ABSTAIN` — sözleşme Faz 6'da tanımlıdır.

    **VETO yalnız TAM ÖLÇÜLMÜŞ veriyle** verilir. Eksik/belirsiz her şey `ABSTAIN`dir ve
    `ABSTAIN` hiçbir zaman `ALLOW` ya da `VETO` sayılmaz.
    """
    # --- VETO: yalnız ölçülmüş çelişki ---------------------------------------------------
    if aligned is False:
        return VETO                                   # HTF yönü baseline'a AÇIKÇA karşıt
    if is_long is None or aligned is None:
        return ABSTAIN

    if sh["state"] == LTF_UNKNOWN:
        reasons.append(sh.get("reason") or R_LTF_AMBIGUOUS)
        return ABSTAIN
    if sh["state"] == LTF_NO_SHIFT:
        # Teyidin karar anında KESİN OLARAK yok olması ölçülmüş bir gerçektir.
        reasons.append(R_LTF_NO_SHIFT)
        return VETO
    shift_long = sh["direction"] == "LONG"
    if shift_long != is_long:
        reasons.append(R_LTF_SHIFT_OPPOSES)
        return VETO                                   # ters yönde ölçülmüş yapı kayması
    if (sh.get("bars_since_shift") is not None
            and sh["bars_since_shift"] > cfg.max_bars_since_shift):
        reasons.append("SHIFT_TOO_OLD")
        return ABSTAIN

    # --- yer değiştirme -------------------------------------------------------------------
    body, rng, loc = disp["body_atr"], disp["range_atr"], disp["close_location"]
    if body is None or rng is None or loc is None:
        reasons.append(R_ATR_MISSING)
        return ABSTAIN
    if body < cfg.min_displacement_body_atr or rng < cfg.min_displacement_range_atr:
        reasons.append(R_DISPLACEMENT_LOW)
        return VETO
    if loc < cfg.close_location_threshold:
        reasons.append(R_CLOSE_LOCATION_LOW)
        return VETO

    # --- retest (varyanta bağlı) ------------------------------------------------------------
    if cfg.require_retest:
        st = rt["state"]
        if st == RETEST_PENDING:
            reasons.append(R_RETEST_PENDING)
            return ABSTAIN
        if st == RETEST_UNKNOWN:
            reasons.append(R_RETEST_MISSING)
            return ABSTAIN
        if st == RETEST_LATE:
            reasons.append(R_RETEST_LATE)
            return VETO
        if st == RETEST_INVALIDATED:
            reasons.append(R_RETEST_OPPOSITE)
            return VETO
        if st == RETEST_ABSENT:
            reasons.append(R_RETEST_MISSING)
            return VETO

    # --- geometri ---------------------------------------------------------------------------
    if not geo.get("valid"):
        reasons.append(geo.get("reason") or R_INVALID_GEOMETRY)
        return ABSTAIN                                # geometri yön sonucunu TAŞIYAMAZ
    if geo["rr"] < cfg.min_structural_rr:
        reasons.append(R_RR_BELOW_MIN)
        return VETO if cfg.veto_on_low_rr else ABSTAIN

    reasons.append(R_OK)
    return ALLOW


def evaluate_variants(*, base: dict[str, Any] | None = None, **kw) -> dict[str, dict[str, Any]]:
    """Bütün önceden taahhüt edilmiş varyantlar AYNI ANDA gölgede ölçülür.

    Hiçbiri sonuçlara bakılarak SEÇİLMEZ; hepsi raporlanır ve her biri kendi terfi
    kapılarından ayrı ayrı geçmek zorundadır.
    """
    kw.pop("cfg", None)
    return {c.variant: build_mtf_context(cfg=c, **kw) for c in build_variants(base)}


def pair_status(pair: str) -> dict[str, Any]:
    """Bir çiftin üretim durumu — panoda olduğu gibi gösterilir."""
    p = str(pair)
    if p in SUPPORTED_PAIRS:
        return {"pair": p, "state": "SUPPORTED", "frames": PAIR_FRAMES.get(p),
                "new_provider_calls": 0,
                "reason": "Mevcut 1g/1s kareleri kullanılır; yeni istek YOK."}
    if p in DEFINED_BUT_DISABLED_PAIRS:
        return {"pair": p, "state": R_DATA_UNAVAILABLE, "frames": PAIR_FRAMES.get(p),
                "new_provider_calls": 0,
                "reason": ("15d karesi üretimde çekilmiyor; etkinleştirmek AgentRunner'ın "
                           "PAYLAŞILAN aktif veri yolunu değiştirirdi ve VPS tarafında "
                           "hız-sınırı/soğuk başlangıç etkisi ÖLÇÜLEMEDİ.")}
    if p in FUTURE_RESEARCH_ONLY_PAIRS:
        return {"pair": p, "state": R_PAIR_FUTURE_ONLY, "frames": PAIR_FRAMES.get(p),
                "new_provider_calls": 0,
                "reason": "Kapsam DIŞI. Üretimde M5/M1 isteği ÜRETİLMEZ."}
    return {"pair": p, "state": R_PAIR_UNSUPPORTED, "frames": None,
            "new_provider_calls": 0, "reason": "Tanımsız çift."}


__all__ = [
    "SCHEMA_VERSION", "ALLOW", "VETO", "ABSTAIN", "DECISIONS",
    "MEASURED", "MODELED", "MISSING", "PROVENANCE_KINDS",
    "PAIR_D_H1", "PAIR_H4_M15", "PAIR_H1_M5", "PAIR_M15_M1",
    "SUPPORTED_PAIRS", "DEFINED_BUT_DISABLED_PAIRS", "FUTURE_RESEARCH_ONLY_PAIRS",
    "ALL_PAIRS", "PAIR_FRAMES", "FRAME_MS",
    "HTF_BULLISH", "HTF_BEARISH", "HTF_NEUTRAL", "HTF_UNKNOWN",
    "LTF_SHIFT_UP", "LTF_SHIFT_DOWN", "LTF_NO_SHIFT", "LTF_UNKNOWN",
    "RETEST_CONFIRMED", "RETEST_ABSENT", "RETEST_LATE", "RETEST_INVALIDATED",
    "RETEST_PENDING", "RETEST_UNKNOWN",
    "SRC_SWING", "SRC_EQUAL_CLUSTER", "SRC_PREV_PERIOD",
    "MultiTimeframeConfig", "CONFIG_VARIANTS", "VARIANT_NAMES", "build_variants",
    "closed_bars_as_of", "staleness_frames", "confirmed_swings", "equal_level_clusters",
    "previous_closed_period_extremes", "liquidity_levels", "structure_shift",
    "displacement", "retest", "structural_geometry", "build_mtf_context",
    "evaluate_variants", "pair_status",
    "R_OK", "R_PAIR_UNSUPPORTED", "R_PAIR_FUTURE_ONLY", "R_DATA_UNAVAILABLE",
    "R_HTF_MISSING", "R_LTF_MISSING", "R_HTF_STALE", "R_LTF_STALE", "R_OPEN_CANDLE",
    "R_INSUFFICIENT_BARS", "R_SESSION_UNKNOWN", "R_ATR_MISSING", "R_NO_LIQUIDITY_LEVEL",
    "R_NO_INTERACTION", "R_INTERACTION_AMBIGUOUS", "R_TWO_SIDED",
    "R_ACCEPTED_BREAKOUT_NOT_SWEEP", "R_HTF_SUPPORTS", "R_HTF_OPPOSES",
    "R_DIRECTION_UNKNOWN", "R_LTF_NO_SHIFT", "R_LTF_SHIFT_ALIGNED", "R_LTF_SHIFT_OPPOSES",
    "R_LTF_AMBIGUOUS", "R_DISPLACEMENT_LOW", "R_CLOSE_LOCATION_LOW", "R_RETEST_MISSING",
    "R_RETEST_LATE", "R_RETEST_OPPOSITE", "R_RETEST_PENDING", "R_NO_TARGET",
    "R_INVALID_GEOMETRY", "R_RR_BELOW_MIN", "R_PROVIDER_FAILURE",
    "SESSION_CRYPTO_CONTINUOUS", "SESSION_WEEKDAY", "SESSION_UNKNOWN",
    "DQ_OK", "DQ_PARTIAL", "DQ_UNAVAILABLE", "BREAKOUT_UNCONFIRMED",
]

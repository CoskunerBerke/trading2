"""ÖĞRENME MODU (2026-09-28, öğrenme modu) — yalnız PAPER; tek anahtar, SAF modül (stdlib + `risk.profiles`).

Amaç: PAPER'da her bot mümkün olan en çok işleme girsin, ortak öğrenme katmanı R deneyimi toplasın. Bir limit
yalnız geçerli bir sinyalin işleme dönüşmesini engelliyorsa gevşer; ölçümü bozuyorsa (veri kimliği, R geometrisi,
çift sayım, imkânsız dolum) ya da gerçek paraya dokunuyorsa KALIR.

Sözleşme:
* Anahtar KAPALI (bölüm yok / `enabled: false`) → hiçbir okuyucu farklı davranmaz (`book()` None, `override()`
  varsayılanı döner). Her öğrenme dalı `if learning is not None and learning.on:` ile korunur.
* `active()` = `enabled` VE mutlak mod kapısı (`research_coordinator.mode_gate` ile AYNI bileşim). Kapı düşerse
  durum `LEARNING_MODE_SUSPENDED:<neden>` olur ve bütün okuyucular baseline yoluna döner.
* Öğrenme risk profili `PROFILES`'a ASLA yazılmaz (`profile_for` yalnız kopya üretir); kill switch ve canlı/testnet
  gateway'leri bu modülden hiçbir şey almaz.
* Boyut (`fit_size`): slot başına eşit marj, %0,5 hedef risk, likidasyon mesafesi ≥ 2 × stop, min-notional'a çıkarma
  yalnız %2 tavan ve serbest marj içinde. R ölçekten bağımsızdır; bu kurallar R'yi değiştirmez.
"""
from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Callable, Iterable, Mapping

from .risk.profiles import RiskProfile

#: Öğrenme modunun tanıdığı defterler (config `learning_mode.books` anahtarları bunlarla sınırlı).
BOOK_NAMES = ("main", "t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10",
              "c4_candle_variations", "c4s_candle_variations_strict", "pattern_trader")
#: §6 kullanıcı cevapları — yalnız öğrenme AKTİFKEN uygulanır (askıda/kapalıyken varsayılan döner).
#: `structures_entry_shadow` bot adı listesidir; diğerleri bool.
OVERRIDE_KEYS = ("regime_gate_shadow", "candle_veto_shadow", "structures_entry_shadow",
                 "economics_exploration", "leverage_confidence_fallback")
LIST_OVERRIDE_KEYS = ("structures_entry_shadow",)

#: Boyut kuralı etiketleri (`pos.meta["learning"]["size_rule"]`, `features["learning"]`).
SIZE_SLOT = "SLOT"
SIZE_BUMP = "BUMP_MIN_NOTIONAL"
SIZE_SHRUNK = "SHRUNK_TO_MARGIN"
SIZE_RULES = (SIZE_SLOT, SIZE_BUMP, SIZE_SHRUNK)

#: Varsayılanlar (config ile aynı; kod varsayılanı KAPALI).
DEFAULT_RISK_PCT = 0.5
DEFAULT_RESERVE_PCT = 5.0
DEFAULT_LIQ_BUFFER_MULT = 2.0
DEFAULT_MAX_PENDING = 2000
DEFAULT_MAX_TOTAL_OPEN_RISK_PCT = 100.0
HARD_CAP_PCT = 2.0                      # profil risk_per_trade tavanı (PAPER_RESEARCH) — min-notional çıkarma bununla sınırlı
#: RiskEngine `adjusted_notional`ı 4 haneye yuvarlar (≤ 5e-5 USDT); bundan küçük fark "gerçek aşağı ayar" SAYILMAZ
#: (2026-09-28, öğrenme modu — aksi halde min-notional çıkarması NOTIONAL'a düşüp defterde bir adım kaybeder).
RISK_NOTIONAL_ROUND_TOL = 1e-4
SYMBOLS_UNIVERSE = "universe"

#: Durum etiketleri (sağlık/panel).
STATE_DISABLED = "DISABLED"
STATE_ACTIVE = "ACTIVE"
SUSPENDED_PREFIX = "LEARNING_MODE_SUSPENDED:"

#: Kaldıraç tabanı düşerse (B1/S7) kullanılacak seviye ve etiket.
LEVERAGE_FALLBACK = 2
LEVERAGE_FALLBACK_REASON = "LEARNING_MIN_LEVERAGE_FALLBACK"
#: Taban kapısı başarısızlıklarından YALNIZ bunlar düşüşe izin verir; DATA_*, STOP_UNKNOWN, CONFIDENCE_UNKNOWN,
#: LIQ_BUFFER_TOO_THIN_FOR_BASE, SPREAD_ABOVE_BASE NO_TRADE kalır (paper maliyet modeli sabit 3 bps).
_FALLBACK_OK = frozenset({"STOP_TOO_FAR_FOR_LEVERAGE", "STOP_TOO_TIGHT_FOR_LEVERAGE", "DEPTH_BELOW_BASE"})
_FALLBACK_CONFIDENCE = "CONFIDENCE_BELOW_BASE"
_MIN_TIGHT_STOP_ATR = 0.1               # STOP_TOO_TIGHT yalnız stop ≥ 0,1 ATR iken düşer

# ---------------------------------------------------------------------------- karşı-olgusal sınıflar
#: Açılmayan sinyal için karşı-olgusal kayıt YAZILABİLİR nedenler (geometri geçerli, veri güvenilir; engel kapasite,
#: borsa kuralı, doluluk, parite ya da strateji kapısı).
COUNTERFACTUAL_OK: frozenset[str] = frozenset({
    # kapasite / borsa
    "TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN", "MIN_NOTIONAL", "MIN_QTY", "STEP_ZERO_QTY", "MAX_QTY",
    "LEVERAGE_TOO_HIGH", "MIN_ORDER_CONFLICT", "NO_TRADE_MIN_ORDER_CONFLICT", "MAX_POSITION_PCT",
    "SPOT_ALLOCATION", "RISK_ABOVE_CAP_AFTER_ROUNDING",
    # doluluk (sembol başına tek pozisyon kalır; bilgi kaybolmaz)
    "ALREADY_OPEN", "ALREADY_OPEN_SAME_SYMBOL", "POSITION_OPEN", "SAME_SYMBOL_POSITION_OPEN",
    "OPPOSITE_EXPOSURE_CONFLICT",
    # laboratuvar paritesi
    "RISK_OUTSIDE_TESTED_RANGE", "CHASE_LIMIT", "EXPIRED_BEFORE_ENTRY", "ENTRY_DRIFT",
    # likidite (pencere sonunda hâlâ bilinmiyorsa)
    "LIQUIDITY_UNKNOWN",
    # ana bot kapıları
    "NEGATIVE_NET_EDGE", "RESEARCH_SIZE_ONLY", "CANDLE_VETO", "REGIME_VETO", "CHART_VETO",
    "LEVERAGE_GATE_BLOCKED", "CHIEF_BLOCKED", "RISK_ENGINE_BLOCKED", "RISK_CAPACITY_BLOCKED",
    "COSTS_EXCEED_EDGE", "KILL_SWITCH_ACTIVE",
    # C4: `also_matched` varyasyonları
    "ALSO_MATCHED",
    # seçicilik-ekstra girişi YALNIZ KAYIT (2026-10-03, sahip kararı): sinyal ve geometri geçerli, açılmadı
    "LEARNING_RECORD_ONLY",
})
#: ASLA kayıt yazılmaz: veri/geometri güvenilmez, R tanımsız ya da aynı gözlem ikinci kez sayılır.
COUNTERFACTUAL_NEVER: frozenset[str] = frozenset({
    "DATA_INVALID", "NO_DATA", "DATA_VERDICT_MISSING", "UNRESOLVED_PRECISION", "UNVERIFIED_PRECISION",
    "STRATEGY_BAD_STOP", "BAD_STOP", "INVALID_STOP", "STOP_PRESENT", "BAD_PRICE",
    "DUPLICATE_SIGNAL", "SIGNAL_ALREADY_USED", "PATTERN_ALREADY_USED", "SAME_BREAK_ALREADY_USED",
    "ALREADY_FIRED_THIS_BAR", "NO_TRIGGER",
    "STRUCTURE_FRAME_MARKET_MISMATCH", "STRUCTURE_ERROR", "STRUCTURE_ANALYSIS_ERROR", "STRATEGY_ERROR",
    "NO_VERIFIED_FUTURES_PRICE", "INVALID_FUTURES_PRICE_TIME", "STALE_FUTURES_PRICE",
    "STOP_BEYOND_QUANTIZED_ENTRY", "MARK_BEYOND_STOP_AT_ENTRY", "PRICE_QUANTIZED_TO_ZERO",
    "FILTERS_STALE", "FILTERS_UNVERIFIED", "FILTERS_UNDATED",
})
_CF_OK_PREFIXES = ("STRUCTURE:", "STRUCTURE_", "CANDLE_VETO", "REGIME_VETO", "CHART_VETO")
_CF_NEVER_PREFIXES = ("DATA_",)
#: Kapı gerekçesinin veri eksikliği bildirdiği son ekler (ör. `CANDLE_VETO:C1_MISSING_BARS`, `REGIME_VETO:R1_NO_REGIME`).
_CF_NEVER_SUFFIXES = ("_MISSING_BARS", "_NO_REGIME", "_SIDE")


def counterfactual_ok(reason: str) -> bool:
    """Bu red nedeni karşı-olgusal kayda uygun mu? Önek duyarlı (`STRUCTURE:*`, `CANDLE_VETO*`, `REGIME_VETO*`).

    `CODE:detay` biçiminde HER parça denetlenir: bir parça ASLA sınıfındaysa (ör.
    `STRUCTURE:STRUCTURE_FRAME_MARKET_MISMATCH:SPOT`) kayıt yazılmaz. Bilinmeyen neden → False (fail-closed)."""
    r = str(reason or "").strip().upper()
    if not r:
        return False
    segs = [s.strip() for s in r.split(":")]
    for s in segs:
        if not s or s == "?":
            return False
        if s in COUNTERFACTUAL_NEVER or s.startswith(_CF_NEVER_PREFIXES) or s.endswith(_CF_NEVER_SUFFIXES):
            return False
    if segs[0] in COUNTERFACTUAL_OK:
        return True
    return r.startswith(_CF_OK_PREFIXES)


# ---------------------------------------------------------------------------- öğrenme-ekstra girişlerin sınıfı
# 2026-10-03 (sahip kararı; her algoritma kendi kâğıt bakiyesinde ayda en az +%1 net hedefler): öğrenme-ekstra giriş
# (`learning_unlocked_by` dolu) NEDENİNE göre ayrılır. Öğrenme dönemi kanıtı (VPS, kapanan işlemler): seçicilik-ekstra
# girişler her yerde ~0 ya da eksi (ana bot NEGATIVE_NET_EDGE 86 işlem −0,04R, STRUCTURE:OPPOSING_CONFIRMED 76 −0,06R,
# CANDLE_VETO 30 −0,09R; M2 STRUCTURE_OPPOSING_CONFIRMED 22 +0,006R; BOOK_UNIVERSE C4 −0,32R, D4 −0,91R); kapasite-ekstra
# M2 TOTAL_OPEN_RISK 10 işlem +0,15R; Box stop < %0,5 72 işlem −0,56R, %0,5–1 176 işlem +0,17R.
#
# * KAPASİTE (A): sinyal taban kurallarından GEÇER; yalnız hesap/defter kapasitesi ya da emir boyutu kapısı durdururdu →
#   bugünkü gibi GERÇEK açılır.
# * SEÇİCİLİK (B): taban SİNYALİ reddederdi (ekonomi, yapı, mum/rejim vetosu, kaldıraç seçicisinin NO_TRADE'i, defter
#   evreni, R/R tabanı …) ya da kod güvenle sınıflanamıyor (BASELINE_UNKNOWN dahil, bilinmeyen her kod) → AÇILMAZ;
#   `LEARNING_RECORD_ONLY` nedenli karşı-olgusal ("olsaydı") olarak kaydedilir. Bir girişte TEK bir (B) kodu yeterlidir.
# * BOX İSTİSNASI (C): BOX_MIN_STOP_PCT (stop tabanın %2,22'sinin altında ama öğrenme tabanının üstünde) GERÇEK kalır.
#
# Sınıf, kapının KODDA ne anlama geldiğinden türetilir (aşağıdaki yorumlar). Kodu üreten yerler: `engine_v3` ana giriş
# yolu (`lm_unlocked`, `_lm_baseline_blockers` → taban `RiskEngine.evaluate`), `strategy_paper._learning_tags` /
# `_baseline_blocks` / `_ledger_preview`, `pattern_trader.book` (`unlocked`, `_open_learning`). Tablo testi
# (`tests/test_learning_record_only_extras.py`) bu yerleri tarar: sınıfsız yeni kod testi düşürür.
EXTRA_OPEN = "open"
EXTRA_RECORD_SELECTIVITY = "record_selectivity"
EXTRA_ENTRIES_MODES = (EXTRA_OPEN, EXTRA_RECORD_SELECTIVITY)
#: Kod varsayılanı: bugünkü davranış (her öğrenme-ekstra açılır). config.yaml `record_selectivity` seçer.
DEFAULT_EXTRA_ENTRIES = EXTRA_OPEN
#: Açılmayan seçicilik-ekstra adayın karşı-olgusal nedeni (`reason_not_opened[0]`); ardından ayrılan kodlar gelir.
LEARNING_RECORD_ONLY = "LEARNING_RECORD_ONLY"
#: Karşı-olgusal kaydın `features` anahtarı: ayrılan kodlar, sınıf ve adayın kullanacağı öğrenme parametreleri.
RECORD_ONLY_FEATURE = "learning_record_only"
CLASS_POLICY, CLASS_CAPACITY, CLASS_SELECTIVITY = "policy", "capacity", "selectivity"
UNLOCK_CLASSES = (CLASS_POLICY, CLASS_CAPACITY, CLASS_SELECTIVITY)

#: (A) KAPASİTE — sinyal geçer, yalnız hesap/defter kapasitesi ya da emir boyutu durdururdu. YALNIZ tam eşleşme.
UNLOCK_CAPACITY: frozenset[str] = frozenset({
    # RiskEngine: TOTAL_OPEN_RISK = adayın piyasa kovasındaki açık stop riski + bu işlem > equity × tavan (%6) — hesap
    # kapasitesi; öğrenme profili tavanı 100'e çıkarır, sinyal yargılanmaz.
    "TOTAL_OPEN_RISK",
    # RiskEngine: MAX_POSITIONS / MAX_POSITIONS_MARKET = açık pozisyon ADEDİ ≥ profil tavanı (PAPER_RESEARCH'te yok);
    # defter ön-izlemesi `ledger.can_open` ve Formasyon `R_MAX_POSITIONS` = defterin pozisyon adedi tavanı — kapasite.
    "MAX_POSITIONS", "MAX_POSITIONS_MARKET",
    # RiskEngine: MAX_POSITION_PCT = notional > equity × %30 × kaldıraç — tek işlemin BOYUT tavanı.
    "MAX_POSITION_PCT",
    # RiskEngine: MIN_ORDER_CONFLICT = taban boyut borsanın en küçük emrinin altında (risk büyütülmez) — emir boyutu.
    # NO_TRADE_MIN_ORDER_CONFLICT = coin head planı tabanda aynı nedenle vetolardı (B5; `plan.learning_min_notional_bump`).
    "MIN_ORDER_CONFLICT", "NO_TRADE_MIN_ORDER_CONFLICT",
    # INSUFFICIENT_MARGIN = taban boyutunun marjı taban görünümünün serbest marjını aşar (ana bot `_lm_baseline_blockers`,
    # defter ön-izlemesi `_ledger_preview`; politika rezervi nedeni de bu koddur) — hesap kapasitesi.
    "INSUFFICIENT_MARGIN",
    # `_ledger_preview` borsa miktar kuralları: adıma yuvarlanan miktar 0 / min miktarın altı / piyasa emri tavanının üstü
    # / min-notional'ın altı — emir boyutu (öğrenme boyutu min-notional'a çıkarır ya da küçültür; sinyal aynı).
    "STEP_ZERO_QTY", "MIN_QTY", "MAX_QTY", "MIN_NOTIONAL",
    # `_ledger_preview`: taban kaldıracı sembolün borsa kaldıraç tavanını aşar — emir kurulumu (öğrenme kaldıracı
    # borsa tavanıyla sınırlar); sinyal yargılanmaz.
    "LEVERAGE_TOO_HIGH",
    # Formasyon: tick ızgarası gerçekleşme fiyatını stoptan uzaklaştırınca TABAN boyutu risk bütçesini aşardı; öğrenme
    # boyutu gerçekleşme fiyatıyla kurar (risk bütçede, R aynı). R/R tabanı ayrı koddur (RR_BELOW_MIN_AFTER_ROUNDING).
    "RISK_ABOVE_CAP_AFTER_ROUNDING",
    # RiskEngine: MARGIN_UTILIZATION = kullanılan marj + bu işlemin marjı > equity tavanı — hesap marj kapasitesi.
    "MARGIN_UTILIZATION",
    # RiskEngine: SPOT_ALLOCATION = spot notional maruziyeti tavanı (ana bot %30, öğrenme 100) — hesap kapasitesi.
    "SPOT_ALLOCATION",
    # RiskEngine: CLUSTER_CAP = aynı kümede aynı yönde pozisyon ADEDİ tavanı; ALTCOIN_EXPOSURE = altcoin notional
    # maruziyeti tavanı — portföy kapasitesi (PAPER_RESEARCH'te yok); sinyal yargılanmaz.
    "CLUSTER_CAP", "ALTCOIN_EXPOSURE",
    # RiskEngine: RISK_PER_TRADE ve LEVERAGE_CAP yalnız KÜÇÜLTÜR (her zaman geçer) — boyut tavanı; bugün kod olarak çıkmaz.
    "RISK_PER_TRADE", "LEVERAGE_CAP",
    # Doluluk: sembolün tek pozisyon yuvası dolu (RiskEngine ALREADY_OPEN_SAME_SYMBOL, `ledger.can_open` ALREADY_OPEN) ya
    # da aynı coinde ters maruziyet (OPPOSITE_EXPOSURE_CONFLICT) — hesap/defter yuvası, sinyal değil. Taban görünümü
    # öğrenme defterinin alt kümesi olduğundan bugün öğrenme-ekstra kodu olarak ULAŞILAMAZ (öğrenme defteri de durur).
    "ALREADY_OPEN_SAME_SYMBOL", "ALREADY_OPEN", "OPPOSITE_EXPOSURE_CONFLICT",
})
#: (C) BOX İSTİSNASI — `strategy_paper._learning_tags`: Box stopu tabanın `min_stop_pct`inin (%2,22) altında ama öğrenme
#: tabanının (config `learning_mode.books.b1_box_fade.min_stop_pct`, %0,5) üstünde. Sahip kararıyla GERÇEK açılır (A gibi):
#: öğrenme tabanının altındaki stoplar kural tarafından hiç üretilmez (`box_theory` `min_stop_pct`).
UNLOCK_BOX_EXCEPTION: frozenset[str] = frozenset({"BOX_MIN_STOP_PCT"})
#: (B) SEÇİCİLİK — taban SİNYALİ reddederdi. Bu liste belgeleme ve tablo testi içindir; listede OLMAYAN her kod da
#: (önek kuralına uymasa bile) seçicilik sayılır (fail-closed).
UNLOCK_SELECTIVITY: frozenset[str] = frozenset({
    # Ana bot ekonomi kapısı: maliyet/belirsizlik sonrası muhafazakâr kenar ≤ 0 (NEGATIVE_NET_EDGE) ya da yalnız nokta
    # tahmini pozitif (RESEARCH_SIZE_ONLY) — sinyalin kendisi reddedilir (S5 keşfi).
    "NEGATIVE_NET_EDGE", "RESEARCH_SIZE_ONLY",
    # Ana bot: fırsat × şef × araştırma boyut çarpanı 0 → taban emir AÇMAZDI (B3; sinyalin kendisi elenir).
    "SIZE_MULTIPLIER_ZERO",
    # Strateji defterleri: sembol defterin KENDİ laboratuvar evreninde değil (D4/C4 `symbols: universe`); Formasyon:
    # protokol evreni dışında — taban bu sembolde sinyal almazdı.
    "BOOK_UNIVERSE", "NOT_IN_PROTOCOL_UNIVERSE",
    # Formasyon: maliyet sonrası R/R (girişte / tick yuvarlamasından sonra) protokol tabanının altında — sinyal kalitesi.
    "RR_BELOW_MIN_AT_ENTRY", "RR_BELOW_MIN_AFTER_ROUNDING",
    # Formasyon: zarardan sonra sembol bekleme süresi — taban bu sembolde sinyali almazdı (protokol kuralı).
    "COOLDOWN_AFTER_LOSS",
    # Formasyon: 0,5% derinliği tabanın 20.000 USDT eşiğinin altında (THIN_DEPTH) ya da likidite/derinlik tetikte
    # ölçülemedi (bekleme kodları LIQUIDITY_UNKNOWN / DEPTH_UNKNOWN) — piyasa kalitesi kapısı, hesap kapasitesi değil.
    "THIN_DEPTH", "LIQUIDITY_UNKNOWN", "DEPTH_UNKNOWN",
    # RiskEngine hesap-geneli işlem durdurma: kill switch, günlük/haftalık zarar, azami düşüş, ardışık zarar / sembol
    # bekleme süreleri — taban hiç işlem açmazdı; kapasite değil (öğrenme profili aynı kuralları taşır).
    "KILL_SWITCH_ACTIVE", "DAILY_LOSS", "WEEKLY_LOSS", "MAX_DRAWDOWN", "CONSEC_LOSS_COOLDOWN", "SYMBOL_COOLDOWN",
    # RiskEngine sinyal/piyasa kapıları: stop yok, spotta short, spread tavanı, en küçük beklenen R.
    "STOP_PRESENT", "SPOT_NO_SHORT", "SPREAD", "MIN_EXPECTED_R",
    # RiskEngine LIQ_BUFFER: taban kaldıracında likidasyon mesafesi < k × stop — taban bu kaldıraçta açmazdı ve RiskEngine
    # daha düşüğünü denemez (kaldıraç seçicisinin NO_TRADE'i gibi; PAPER_RESEARCH'te yok).
    "LIQ_BUFFER",
    # `_ledger_preview`: dolum fiyatı ≤ 0 — veri; `_baseline_blocks`: nedeni boş ret (RISK_DENIED) ve taban kararı
    # hesaplanamadı (BASELINE_UNKNOWN) — taban kararı bilinmiyor, güvenle kapasite denemez.
    "BAD_PRICE", "RISK_DENIED", "BASELINE_UNKNOWN",
})
#: (B) önek kuralları: ana bot mum/rejim vetosu (`CANDLE_VETO:<neden>`, `REGIME_VETO:<neden>`), yapı kapısı (`STRUCTURE:<kod>`,
#: defterler `STRUCTURE_<kod>`), kaldıraç seçicisinin NO_TRADE'i (`LEVERAGE_GATE_BLOCKED:<taban başarısızlıkları>`: taban hiçbir
#: kaldıraçta açmazdı; B1/S7 öğrenmede 2x düşüş).
UNLOCK_SELECTIVITY_PREFIXES: tuple[str, ...] = ("CANDLE_VETO", "REGIME_VETO", "STRUCTURE", "LEVERAGE_GATE_BLOCKED")


def classify_unlock_code(code: Any) -> str:
    """Tek `learning_unlocked_by` kodu → CLASS_CAPACITY | CLASS_SELECTIVITY. Kapasite ve Box istisnası YALNIZ tam
    eşleşmedir (`CODE:detay` biçimi kapasite sayılmaz); geri kalan her şey — bilinen seçicilik kodu, önek kuralı ya da
    bilinmeyen kod — seçiciliktir (fail-closed)."""
    c = str(code or "").strip().upper()
    if c in UNLOCK_CAPACITY or c in UNLOCK_BOX_EXCEPTION:
        return CLASS_CAPACITY
    return CLASS_SELECTIVITY


def classify_unlock_codes(codes: Iterable[Any] | None) -> str:
    """Bir girişin `learning_unlocked_by` listesi → CLASS_POLICY (boş: taban da açardı) | CLASS_CAPACITY (her kod
    kapasite ya da Box istisnası) | CLASS_SELECTIVITY (en az bir seçicilik/bilinmeyen kod)."""
    cs = [c for c in (codes or ()) if str(c or "").strip()]
    if not cs:
        return CLASS_POLICY
    return CLASS_SELECTIVITY if any(classify_unlock_code(c) == CLASS_SELECTIVITY for c in cs) else CLASS_CAPACITY


def selectivity_codes(codes: Iterable[Any] | None) -> list[str]:
    """Listedeki seçicilik (ve bilinmeyen) kodları, sırası ve tekilliği korunarak."""
    return list(dict.fromkeys(str(c) for c in (codes or ())
                              if str(c or "").strip() and classify_unlock_code(c) == CLASS_SELECTIVITY))


def extra_entries_mode(learning: Any) -> str:
    """Defter görünümünün (`BookLearning`) ekstra giriş kipi; alan yoksa/geçersizse kod varsayılanı (`open`)."""
    v = str(getattr(learning, "extra_entries", DEFAULT_EXTRA_ENTRIES) or DEFAULT_EXTRA_ENTRIES)
    return v if v in EXTRA_ENTRIES_MODES else DEFAULT_EXTRA_ENTRIES


def record_only(codes: Iterable[Any] | None, learning: Any) -> dict[str, Any] | None:
    """Açılış kararı KESİNLEŞTİĞİ yerde çağrılır. None → aç (bugünkü yol, bit-aynı). Sözlük → AÇMA, karşı-olgusal kaydet:
    {"reason", "codes" (bütün kodlar), "selectivity_codes", "class", "mode"}. Yalnız öğrenme açık VE kip
    `record_selectivity` VE en az bir seçicilik kodu varken sözlük döner; `open` kipinde hiçbir şey hesaplanmaz."""
    if learning is None or not bool(getattr(learning, "on", False)) \
            or extra_entries_mode(learning) != EXTRA_RECORD_SELECTIVITY:
        return None
    cs = list(dict.fromkeys(str(c) for c in (codes or ()) if str(c or "").strip()))
    if classify_unlock_codes(cs) != CLASS_SELECTIVITY:
        return None
    return {"reason": LEARNING_RECORD_ONLY, "codes": cs, "selectivity_codes": selectivity_codes(cs),
            "class": CLASS_SELECTIVITY, "mode": EXTRA_RECORD_SELECTIVITY}


def is_record_only_cf(reasons: Any) -> bool:
    """Karşı-olgusal kayıt (`reason_not_opened`) bir seçicilik-ekstra YALNIZ KAYIT adayı mı (ilk neden)?"""
    rs = list(reasons or []) if isinstance(reasons, (list, tuple)) else []
    return bool(rs) and str(rs[0]) == LEARNING_RECORD_ONLY


# ---------------------------------------------------------------------------- yapılandırma nesneleri
@dataclass(frozen=True)
class BookLearningCfg:
    """Tek defterin öğrenme ayarı (config `learning_mode.books.<ad>`). Listede olmayan defter KAPALI kalır."""
    enabled: bool = False
    slots: int = 20
    leverage_max: int = 3
    min_stop_pct: float | None = None
    symbols: str | None = None          # None | "universe"

    @classmethod
    def from_any(cls, v: Any) -> "BookLearningCfg":
        """dict ya da hazır nesne → BookLearningCfg. Bilinmeyen anahtar burada YOK SAYILIR (doğrulama config'tedir)."""
        if isinstance(v, BookLearningCfg):
            return v
        if v is None:
            return cls()
        if not isinstance(v, dict):
            raise TypeError("defter ayarı sözlük olmalı: %r" % (v,))
        allowed = cls.__dataclass_fields__
        return cls(**{k: v[k] for k in v if k in allowed})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BookLearning:
    """Bir defterin TEK geçiş için aldığı öğrenme görünümü — değişmez. `LearningMode.book()` yalnız aktifken üretir."""
    on: bool
    name: str
    slots: int
    leverage_max: int
    risk_pct: float                     # 0.5 — hedef risk (equity tabanının %'si)
    reserve_pct: float                  # 5 — Σ marj ≤ (1 − reserve) × E
    liq_buffer_mult: float              # 2.0 — liq mesafesi ≥ k × stop mesafesi
    min_notional_bump: bool
    counterfactual: bool
    max_pending: int
    min_stop_pct: float | None
    symbols: str | None
    hard_cap_pct: float = HARD_CAP_PCT
    #: öğrenme RiskEngine profilinin toplam açık risk tavanı (config `max_total_open_risk_pct`; defterler de uyar)
    max_total_open_risk_pct: float = DEFAULT_MAX_TOTAL_OPEN_RISK_PCT
    #: öğrenme-ekstra giriş kipi (config `extra_entries`; 2026-10-03): `open` | `record_selectivity`
    extra_entries: str = DEFAULT_EXTRA_ENTRIES

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("extra_entries") == DEFAULT_EXTRA_ENTRIES:
            d.pop("extra_entries")              # kod varsayılanında özet/risk.json bit-aynı (2026-10-03)
        return d


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class LearningMode:
    """Tek anahtar. Kurucu yalnız okur; `refresh()` geçiş başına bir kez çağrılır ve sonucu önbelleğe alır.

    `mode_gate` → (ok, neden): motorun `_research_mode_ok` kapısıyla AYNI (PAPER + paper gateway + canlı emir yolu
    kapalı). İstisna fırlatırsa öğrenme o geçiş için ASKIYA alınır (fail-closed)."""

    def __init__(self, section: Any, *, mode_gate: Callable[[], tuple[bool, str]],
                 clock: Callable[[], datetime] | None = None):
        self.section = section
        self.enabled: bool = bool(getattr(section, "enabled", False) is True) if section is not None else False
        self._gate = mode_gate
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._on = False
        self._reason = STATE_DISABLED
        self._since: str | None = None
        self._refreshed = False
        books = getattr(section, "books", None) if section is not None else None
        self._books: dict[str, BookLearningCfg] = {}
        for k, v in (books or {}).items():
            if str(k) in BOOK_NAMES:
                self._books[str(k)] = BookLearningCfg.from_any(v)
        ovr = getattr(section, "strategy_overrides", None) if section is not None else None
        self._overrides: dict[str, Any] = {str(k): v for k, v in (ovr or {}).items()}

    # ------------------------------------------------------------ kurulum
    @classmethod
    def from_config(cls, cfg: Any, mode_gate: Callable[[], tuple[bool, str]], **kw) -> "LearningMode":
        """`cfg`: V3Config ya da `.v3` taşıyan BotConfig. Bölüm yoksa KAPALI örnek döner."""
        sec = getattr(cfg, "learning_mode", None) if cfg is not None else None
        if sec is None and cfg is not None and getattr(cfg, "v3", None) is not None:
            sec = getattr(cfg.v3, "learning_mode", None)
        return cls(sec, mode_gate=mode_gate, **kw)

    # ------------------------------------------------------------ bölüm değerleri
    def _sec(self, name: str, default):
        v = getattr(self.section, name, None) if self.section is not None else None
        return default if v is None else v

    @property
    def risk_pct(self) -> float:
        return float(self._sec("risk_per_trade_pct", DEFAULT_RISK_PCT))

    @property
    def reserve_pct(self) -> float:
        return float(self._sec("margin_reserve_pct", DEFAULT_RESERVE_PCT))

    @property
    def liq_buffer_mult(self) -> float:
        return float(self._sec("liq_buffer_mult", DEFAULT_LIQ_BUFFER_MULT))

    @property
    def max_total_open_risk_pct(self) -> float:
        return float(self._sec("max_total_open_risk_pct", DEFAULT_MAX_TOTAL_OPEN_RISK_PCT))

    @property
    def min_notional_bump(self) -> bool:
        return bool(self._sec("min_notional_bump", True))

    @property
    def counterfactual(self) -> bool:
        return bool(self._sec("counterfactual", True))

    @property
    def max_pending(self) -> int:
        return int(self._sec("counterfactual_max_pending", DEFAULT_MAX_PENDING))

    @property
    def extra_entries(self) -> str:
        """Öğrenme-ekstra giriş kipi (2026-10-03): `open` (kod varsayılanı, bugünkü davranış) | `record_selectivity`.
        Geçersiz değer config doğrulamasında ConfigError'dur; burada savunma olarak varsayılana düşer."""
        v = str(self._sec("extra_entries", DEFAULT_EXTRA_ENTRIES))
        return v if v in EXTRA_ENTRIES_MODES else DEFAULT_EXTRA_ENTRIES

    # ------------------------------------------------------------ çalışma zamanı kapısı
    def active(self) -> tuple[bool, str]:
        """(aktif mi, neden). Kapalı → (False, DISABLED); kapı düşerse (False, LEARNING_MODE_SUSPENDED:<neden>)."""
        if not self.enabled:
            return False, STATE_DISABLED
        try:
            ok, why = self._gate()
        except Exception as exc:  # noqa: BLE001 — kapı arızası öğrenmeyi AÇMAZ (fail-closed)
            return False, SUSPENDED_PREFIX + "MODE_GATE_ERROR:" + type(exc).__name__
        if bool(ok):
            return True, STATE_ACTIVE
        return False, SUSPENDED_PREFIX + str(why or "MODE_GATE")

    def refresh(self) -> bool:
        """Kapıyı yeniden değerlendirir ve önbelleğe alır (geçiş başına bir kez). Döner: `on`."""
        ok, why = self.active()
        with self._lock:
            if not self._refreshed or ok != self._on:
                self._since = _iso(self._clock())
            self._on, self._reason, self._refreshed = bool(ok), why, True
            return self._on

    @property
    def on(self) -> bool:
        """Son `refresh()` sonucu. Hiç `refresh()` yapılmadıysa False (baseline)."""
        return self._on

    @property
    def suspended(self) -> bool:
        return bool(self.enabled and self._refreshed and not self._on)

    def status(self) -> dict:
        """{enabled, active, reason, since, books}: sağlık/panel için. `since` son durum değişiminin zamanı."""
        with self._lock:
            out = {"enabled": self.enabled, "active": self._on, "reason": self._reason, "since": self._since,
                   "books": sorted(n for n, b in self._books.items() if b.enabled)}
        if self.extra_entries != DEFAULT_EXTRA_ENTRIES:
            out["extra_entries"] = self.extra_entries   # kod varsayılanında alan yok → sağlık dosyası bit-aynı
        return out

    # ------------------------------------------------------------ okuyucular
    def book_cfg(self, name: str) -> BookLearningCfg:
        return self._books.get(str(name)) or BookLearningCfg()

    def book(self, name: str) -> BookLearning | None:
        """Aktif VE defter açık → değişmez `BookLearning`; aksi halde None (baseline yolu)."""
        if not self._on:
            return None
        bc = self._books.get(str(name))
        if bc is None or not bc.enabled:
            return None
        return BookLearning(on=True, name=str(name), slots=int(bc.slots), leverage_max=int(bc.leverage_max),
                            risk_pct=self.risk_pct, reserve_pct=self.reserve_pct, liq_buffer_mult=self.liq_buffer_mult,
                            min_notional_bump=self.min_notional_bump, counterfactual=self.counterfactual,
                            max_pending=self.max_pending,
                            min_stop_pct=(None if bc.min_stop_pct is None else float(bc.min_stop_pct)),
                            symbols=bc.symbols, hard_cap_pct=HARD_CAP_PCT,
                            max_total_open_risk_pct=self.max_total_open_risk_pct, extra_entries=self.extra_entries)

    def override(self, key: str, default):
        """Strateji ezmesi: yalnız AKTİFKEN config değeri, aksi halde `default`. Bilinmeyen anahtar KeyError."""
        if key not in OVERRIDE_KEYS:
            raise KeyError("bilinmeyen öğrenme ezmesi: %r (geçerli: %s)" % (key, ", ".join(OVERRIDE_KEYS)))
        if not self._on:
            return default
        v = self._overrides.get(key)
        if v is None:
            return default
        return list(v) if isinstance(v, (list, tuple)) else v

    #: SPEC adı (`lm.effective(key, base)`) — `override` ile aynı.
    effective = override

    def structures_entry_shadow(self, bot: str) -> bool:
        """Bu botun yapı GİRİŞ kararları gölgeye mi alınsın (yönetim ENFORCE kalır)."""
        v = self.override("structures_entry_shadow", None)
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        return str(bot) in {str(x) for x in v}

    def profile_for(self, base: RiskProfile, *, main: bool = False) -> RiskProfile:
        return profile_for(base, main=main, max_total_open_risk_pct=self.max_total_open_risk_pct)


# ---------------------------------------------------------------------------- risk profili
def profile_for(base: RiskProfile, *, main: bool = False,
                max_total_open_risk_pct: float = DEFAULT_MAX_TOTAL_OPEN_RISK_PCT) -> RiskProfile:
    """Öğrenme RiskEngine profili: tabanın KOPYASI, toplam açık risk 100 (ana botta spot tahsisi de 100).

    `risk_per_trade_pct` tabandaki gibi kalır (2.0 = sert tavan; min-notional çıkarma bunun içinde). Sonuç
    `PROFILES`'a YAZILMAZ; `risk_profiles.overrides` kullanılmaz."""
    kw: dict[str, Any] = {"max_total_open_risk_pct": float(max_total_open_risk_pct)}
    if main:
        kw["max_spot_allocation_pct"] = 100.0
    return replace(base, **kw)


# ---------------------------------------------------------------------------- boyutlandırma
@dataclass(frozen=True)
class FitResult:
    ok: bool
    notional: float
    leverage: int
    margin: float
    risk_usdt: float
    size_rule: str | None
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _fail(reason: str, lev: int = 1, **detail) -> FitResult:
    return FitResult(False, 0.0, int(max(1, lev)), 0.0, 0.0, None, reason, detail)


def _num(x) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ceil_to_step(qty: float, step: float) -> Decimal:
    """Miktarı adıma YUKARI yuvarla (Decimal ile; float artığı bir adım fazla/eksik üretmesin)."""
    s = Decimal(str(step))
    q = Decimal(repr(qty))
    return (q / s).to_integral_value(rounding=ROUND_CEILING) * s


def _floor_to_step(qty: float, step: float) -> Decimal:
    s = Decimal(str(step))
    q = Decimal(repr(qty))
    return (q / s).to_integral_value(rounding=ROUND_FLOOR) * s


def fit_size(*, equity, entry, stop, slots, leverage_max, risk_pct, reserve_pct=DEFAULT_RESERVE_PCT,
             liq_buffer_mult=DEFAULT_LIQ_BUFFER_MULT, mmr=0.004, min_notional=5.0, qty_step=None, price_for_step=None,
             hard_cap_pct=HARD_CAP_PCT, min_notional_bump=True, available_margin=None, min_qty=None,
             max_position_pct=None) -> FitResult:
    """SLOT SCALE-TO-FIT (SPEC §3). R ölçekten bağımsızdır; bu fonksiyon yalnız USDT büyüklüğü ve marjı seçer.

        s        = |entry − stop| / entry
        m_slot   = (1 − reserve) × E / K
        L_liq    = en büyük L: 1/L − mmr ≥ liq_buffer_mult × s
        n_risk   = min(r, hard_cap) × E / s
        L        = min(L_max, L_liq, ceil(n_risk / m_slot))         (gerektiği kadar kaldıraç)
        notional = min(n_risk, m_slot × L)                          → boyut yüzünden ASLA ret yok

    `available_margin` verilirse (defter `available`), rezerv (reserve × E) dokunulmaz kalacak şekilde önce kaldıraç
    L_cap'e kadar artırılır; yetmezse notional küçültülür (SHRUNK_TO_MARGIN). Notional `min_notional`'ın altına
    düşerse min-notional'a ÇIKARILIR (qty adıma YUKARI) — yalnız risk ≤ hard_cap × E ve marj serbestse
    (available verilmediyse marj ≤ m_slot). Aksi halde ok=False, reason MIN_ORDER_CONFLICT.
    `max_position_pct` (ops.) RiskEngine MAX_POSITION_PCT tavanını (E × pct × L) önceden uygular."""
    E, px, st = _num(equity), _num(entry), _num(stop)
    if E is None or px is None or st is None or E <= 0 or px <= 0:
        return _fail("INVALID_INPUT")
    if st <= 0 or st == px:
        return _fail("INVALID_STOP")
    try:
        K, Lmax = int(slots), int(leverage_max)
    except (TypeError, ValueError):
        return _fail("INVALID_INPUT")
    r = _num(risk_pct)
    cap_pct = _num(hard_cap_pct)
    res_pct = _num(reserve_pct)
    b = _num(liq_buffer_mult)
    mm = _num(mmr)
    if K < 1 or Lmax < 1 or r is None or r <= 0 or cap_pct is None or cap_pct <= 0 or res_pct is None \
            or not (0 <= res_pct < 100) or b is None or b < 0 or mm is None or mm < 0:
        return _fail("INVALID_INPUT")
    min_n = _num(min_notional) or 0.0
    step = _num(qty_step)
    step = step if (step is not None and step > 0) else None
    p_step = _num(price_for_step)
    p_step = p_step if (p_step is not None and p_step > 0) else px
    s = abs(px - st) / px
    reserve = res_pct / 100.0
    m_slot = (1.0 - reserve) * E / K
    # likidasyon tamponu: 1/L − mmr ≥ b × s (float artığına karşı aşağı doğru düzeltilir)
    denom = b * s + mm
    L_liq = int(math.floor(1.0 / denom)) if denom > 0 else Lmax
    L_liq = min(L_liq, Lmax)
    while L_liq >= 1 and (1.0 / L_liq - mm) < b * s:
        L_liq -= 1
    if L_liq < 1:
        return _fail("LIQ_BUFFER_TOO_THIN", s=s, liq_buffer_mult=b)
    L_cap = L_liq
    r_eff = min(r, cap_pct)
    budget = r_eff / 100.0 * E
    n_risk = budget / s
    L_need = max(1, int(math.ceil(n_risk / m_slot - 1e-12)))
    L = max(1, min(L_cap, L_need))
    notional = min(n_risk, m_slot * L)
    pos_cap = None
    if max_position_pct is not None and _num(max_position_pct) is not None and float(max_position_pct) > 0:
        pos_cap = E * float(max_position_pct) / 100.0
        notional = min(notional, pos_cap * L)
    rule = SIZE_SLOT
    usable = None
    detail: dict[str, Any] = {"stop_frac": s, "m_slot": m_slot, "slots": K, "l_liq": L_liq, "l_need": L_need,
                              "l_max": Lmax, "risk_budget_usdt": budget, "notional_risk": n_risk}
    if available_margin is not None:
        av = _num(available_margin)
        usable = (av if av is not None else 0.0) - reserve * E
        detail["usable_margin"] = usable
        if notional / L > usable * (1.0 + 1e-12) or usable <= 0:
            # önce kaldıraçla sığdır (risk değişmez), yetmezse marja küçült — küçültme notional'ı ASLA büyütmez
            L2 = min(L_cap, int(math.ceil(notional / usable))) if usable > 0 else L_cap
            if usable > 0 and L2 > L and notional / L2 <= usable * (1.0 + 1e-12):
                L = L2
            else:
                L = L_cap
                notional = min(notional, max(0.0, usable) * L)
                rule = SIZE_SHRUNK
    # borsa miktar adımı: defter qty'yi AŞAĞI yuvarlar → min-notional/min-qty kararı yuvarlanmış değerle verilir
    mq = _num(min_qty)
    qty_f = notional / p_step
    if step is not None:
        qty_f = float(_floor_to_step(qty_f, step))
    if notional > 0 and qty_f * p_step >= min_n and (mq is None or qty_f >= mq):
        risk = notional * s
        detail["risk_fraction_of_budget"] = risk / budget if budget > 0 else None
        return FitResult(True, notional, int(L), notional / L, risk, rule, "", detail)
    # ---------------------------------------------------------------- MIN-NOTIONAL'A ÇIKARMA (A7)
    # Serbest marj hiç yoksa boyutlanacak bir şey yok; varsa çıkarma denenir (marja sığmazsa MIN_ORDER_CONFLICT).
    if rule == SIZE_SHRUNK and (usable is None or usable <= 0):
        return _fail("INSUFFICIENT_MARGIN", L, **detail)
    if not min_notional_bump:
        return _fail("MIN_ORDER_CONFLICT", L, why="BUMP_DISABLED", **detail)
    q_need = max(min_n / p_step, mq or 0.0)
    if step is not None:
        qty = _ceil_to_step(q_need, step)
        if qty <= 0:
            qty = Decimal(str(step))
        bump = float(qty) * p_step
        detail["qty"] = format(qty, "f")
    else:
        bump = max(q_need * p_step, notional)
    bump_risk = bump * s
    cap_usdt = cap_pct / 100.0 * E
    detail.update({"bump_notional": bump, "bump_risk_usdt": bump_risk, "hard_cap_usdt": cap_usdt})
    if bump_risk > cap_usdt * (1.0 + 1e-12):
        return _fail("MIN_ORDER_CONFLICT", L, why="RISK_CAP", **detail)
    # önce slota sığacak kaldıraç; serbest marj biliniyorsa gerekirse L_cap'e kadar
    L_b = min(L_cap, max(L, int(math.ceil(bump / m_slot - 1e-12))))
    if usable is not None and usable > 0 and bump / L_b > usable:
        L_b = min(L_cap, max(L_b, int(math.ceil(bump / usable - 1e-12))))
    margin_b = bump / L_b
    margin_cap = usable if usable is not None else m_slot
    if margin_b > margin_cap * (1.0 + 1e-12):
        return _fail("MIN_ORDER_CONFLICT", L_b, why="MARGIN", margin=margin_b, margin_cap=margin_cap, **detail)
    if pos_cap is not None and bump > pos_cap * L_b * (1.0 + 1e-12):
        return _fail("MIN_ORDER_CONFLICT", L_b, why="MAX_POSITION_PCT", **detail)
    detail["risk_fraction_of_budget"] = bump_risk / budget if budget > 0 else None
    return FitResult(True, bump, int(L_b), margin_b, bump_risk, SIZE_BUMP, "", detail)


# ---------------------------------------------------------------------------- kaldıraç tabanı
def leverage_fallback(base_failures: Iterable[str], *, stop_atr: float | None, allow_confidence: bool,
                      min_leverage: int = LEVERAGE_FALLBACK) -> int | None:
    """Taban kaldıraç kapısı düştüğünde öğrenme modunda `min_leverage` (2x) ile açılabilir mi?

    Yalnız başarısızlıkların TAMAMI {STOP_TOO_FAR, STOP_TOO_TIGHT (stop ≥ 0,1 ATR), DEPTH_BELOW_BASE}
    (+ `allow_confidence` ise CONFIDENCE_BELOW_BASE) içindeyse `min_leverage`; aksi halde None (NO_TRADE kalır)."""
    fails = {str(x) for x in (base_failures or ())}
    if not fails:
        return None
    allowed = set(_FALLBACK_OK)
    if allow_confidence:
        allowed.add(_FALLBACK_CONFIDENCE)
    if not fails <= allowed:
        return None
    if "STOP_TOO_TIGHT_FOR_LEVERAGE" in fails:
        sa = _num(stop_atr)
        if sa is None or sa < _MIN_TIGHT_STOP_ATR:
            return None
    return int(min_leverage)


# ---------------------------------------------------------------------------- taban görünümü / politika rezervi
#: POLİTİKA REZERVİ (2026-09-28, öğrenme modu; ikinci doğrulama turu): taban kuralların da AÇACAĞI sinyal
#: (`learning_unlocked_by` boş = politika işlemi) için ayrılan marj, slot cinsinden. Öğrenme-ekstra giriş serbest marjı bu
#: kadar EKSİK görür → keşif işlemleri defteri doldurup politika işlemini dışarıda bırakamaz. Politika girişi rezervi
#: kullanır (yalnız %5 genel rezerv kalır). Boyut kuralı (slot marjı, risk) DEĞİŞMEZ; yalnız sığma sınırı.
POLICY_RESERVE_SLOTS = 2
#: Rezervin üst sınırı (E'nin %'si): az slotlu defterde (K küçük → slot büyük) rezerv defteri yutmasın.
POLICY_RESERVE_MAX_PCT = 10.0
#: Politika pozisyonunun taban boyutu (`pos.meta["learning"]["baseline_size"]`: notional, kaldıraç) — taban görünümü bunu okur.
BASELINE_SIZE_KEY = "baseline_size"
#: Politika pozisyonunun AÇIK kalan payı (qty / açılış qty'si) — kısmi kapanıştan (TP1) sonra taban görünümü taban boyutunu
#: bu payla ölçekler (2026-09-28, öğrenme modu; üçüncü doğrulama turu). Kalıcı etikete YAZILMAZ; `learning_tags` üretir.
OPEN_FRAC_KEY = "_open_frac"
#: Politika rezervi yüzünden sığmayan öğrenme-ekstra adayın `detail.why` değeri (ret nedeni INSUFFICIENT_MARGIN).
WHY_POLICY_RESERVE = "POLICY_RESERVE"


def policy_reserve_usdt(*, equity, slots, reserve_pct=DEFAULT_RESERVE_PCT, n_slots: int = POLICY_RESERVE_SLOTS) -> float:
    """Politika rezervi (USDT) = min(`n_slots` × slot marjı ((1 − rezerv) × E / K), %`POLICY_RESERVE_MAX_PCT` × E).
    Geçersiz girdi → 0 (rezerv yok)."""
    E = _num(equity)
    try:
        K = max(1, int(slots or 1))
    except (TypeError, ValueError):
        K = 1
    r = _num(reserve_pct)
    if E is None or E <= 0 or r is None:
        return 0.0
    return max(0.0, min(float(n_slots) * (1.0 - r / 100.0) * E / K, POLICY_RESERVE_MAX_PCT / 100.0 * E))


def fit_with_reserve(*, policy_reserve: float = 0.0, available_margin=None, **kw) -> FitResult:
    """`fit_size` + politika rezervi (2026-09-28, öğrenme modu; üçüncü doğrulama turu): serbest marjdan `policy_reserve`
    düşülerek boyutlanır. Rezervsiz SIĞACAK aday rezerv yüzünden sığmıyorsa (MIN_ORDER_CONFLICT/MARGIN ya da
    INSUFFICIENT_MARGIN) ret nedeni INSUFFICIENT_MARGIN, `detail.why` = POLICY_RESERVE (fit'in kendi nedeni `fit_reason`
    / `fit_why`de kalır) — kapasite sayacı ve karşı-olgusal nedeni gerçek en küçük emir çatışmasından ayrılır.
    Rezerv 0 (politika adayı) → `fit_size` çağrısı AYNEN (bit-aynı)."""
    res = max(0.0, float(_num(policy_reserve) or 0.0))
    if res <= 0 or available_margin is None:
        return fit_size(available_margin=available_margin, **kw)
    av = float(_num(available_margin) or 0.0)
    fit = fit_size(available_margin=av - res, **kw)
    if fit.ok or not (fit.reason == "INSUFFICIENT_MARGIN"
                      or (fit.reason == "MIN_ORDER_CONFLICT" and fit.detail.get("why") == "MARGIN")):
        return fit
    if not fit_size(available_margin=av, **kw).ok:
        return fit                                  # rezervsiz de sığmıyor: gerçek marj/emir çatışması, neden AYNEN
    return replace(fit, reason="INSUFFICIENT_MARGIN",
                   detail=dict(fit.detail, why=WHY_POLICY_RESERVE, fit_reason=fit.reason, fit_why=fit.detail.get("why")))


def learning_tags(positions: Mapping[str, Any] | None) -> dict[str, Any]:
    """{sembol: `meta["learning"]`} — taban görünümünün girdisi. Taban boyutu kayıtlı POLİTİKA pozisyonu kısmen kapandıysa
    (qty < `initial_qty`, ör. TP1) etiketin KOPYASINA açık pay (`OPEN_FRAC_KEY`) eklenir; pozisyonun kendi etiketi
    DEĞİŞMEZ (2026-09-28, öğrenme modu; üçüncü doğrulama turu)."""
    out: dict[str, Any] = {}
    for s, p in (positions or {}).items():
        lr = (getattr(p, "meta", None) or {}).get("learning")
        if isinstance(lr, dict) and not lr.get("learning_unlocked_by") and isinstance(lr.get(BASELINE_SIZE_KEY), dict):
            q, q0 = _num(getattr(p, "qty", None)), _num(getattr(p, "initial_qty", None))
            if q is not None and q0 is not None and q0 > 0 and 0 < q < q0:
                lr = dict(lr, **{OPEN_FRAC_KEY: q / q0})
        out[s] = lr
    return out


def baseline_size_tag(notional, leverage) -> dict[str, Any] | None:
    """`baseline_size` etiketi (taban boyutu: notional, kaldıraç). Geçersizse None (görünüm gerçek boyutu kullanır)."""
    n, lev = _num(notional), _num(leverage)
    if n is None or n <= 0 or lev is None or lev < 1:
        return None
    return {"notional": round(n, 6), "leverage": int(lev)}


def _open_frac(lr: dict) -> float:
    f = _num(lr.get(OPEN_FRAC_KEY))
    return f if (f is not None and 0 < f < 1) else 1.0


def baseline_view(state: Any, learning_by_symbol: dict[str, Any], *, market_type: str = "USDM_PERP",
                  spot_by_symbol: dict[str, Any] | None = None) -> tuple[Any, float]:
    """Taban defterin ŞU AN tutacağı portföyün YAKLAŞIK görünümü (2026-09-28, öğrenme modu) — `learning_unlocked_by`
    etiketi ve politika önceliği içindir; karar/boyut/defter DEĞİŞMEZ.

    * öğrenme-ekstra açık pozisyon (`learning_unlocked_by` dolu): taban defterde OLMAZDI → çıkarılır (marjı serbest);
    * politika pozisyonu (boş etiket): taban boyutunda (`baseline_size`) sayılır; kayıt yoksa gerçek boyutla. Kısmen
      kapandıysa (TP1; `learning_tags` açık payı ekler) taban boyutu AYNI payla ölçeklenir (üçüncü doğrulama turu);
    * öğrenme etiketi olmayan pozisyon (taban/askıda açılmış): olduğu gibi.
    * SPOT (`spot_by_symbol`, ana botun spot alımlarının etiketleri; üçüncü doğrulama turu): öğrenme-ekstra spot çıkarılır
      (spot tahsisi düşer), politika spotu taban boyutuna orantılı ölçeklenir. Spot nakit farkı `baseline_spot_delta`.

    Döner: (görünüm, marj farkı = görünümün kullanılan (futures) marjı − gerçek). Görünümdeki serbest marj = gerçek − fark.
    YAKLAŞIK: taban defterin burada hiç açılmamış işlemleri ve gerçekleşen P&L farkı bilinmez (özsermaye aynı sayılır).
    Öğrenme etiketi taşıyan pozisyon yoksa durum AYNEN döner (fark 0)."""
    ops = list(getattr(state, "open_positions", None) or [])
    spot_tags = spot_by_symbol or {}
    keep: list[Any] = []
    delta, changed = 0.0, False
    for o in ops:
        mt = getattr(o, "market_type", None)
        if mt == "SPOT" and mt != market_type:
            lr = spot_tags.get(o.symbol)
            if not isinstance(lr, dict):
                keep.append(o)
                continue
            changed = True
            if lr.get("learning_unlocked_by"):
                continue                                  # taban bu spotu hiç almazdı
            k = _spot_scale(o, lr)
            keep.append(o if k is None else replace(o, notional=float(o.notional) * k, margin=float(o.margin) * k,
                                                    risk_usdt=float(o.risk_usdt) * k))
            continue
        lr = learning_by_symbol.get(o.symbol) if mt == market_type else None
        if not isinstance(lr, dict):
            keep.append(o)
            continue
        changed = True
        m0 = float(_num(getattr(o, "margin", None)) or 0.0)
        if lr.get("learning_unlocked_by"):
            delta -= m0
            continue
        bs = lr.get(BASELINE_SIZE_KEY)
        n = _num(bs.get("notional")) if isinstance(bs, dict) else None
        lev = _num(bs.get("leverage")) if isinstance(bs, dict) else None
        entry = _num(getattr(o, "entry", None))
        if n is None or n <= 0 or lev is None or lev < 1 or not entry:
            keep.append(o)
            continue
        n *= _open_frac(lr)
        stop = _num(getattr(o, "stop", None))
        m = n / lev
        keep.append(replace(o, notional=n, margin=m, leverage=float(lev),
                            risk_usdt=(abs(entry - stop) / entry * n) if stop is not None else n))
        delta += m - m0
    if not changed:
        return state, 0.0
    view = replace(state, open_positions=keep, used_margin=max(0.0, float(state.used_margin) + delta),
                   available=float(state.available) - delta)
    return view, delta


def _spot_scale(o: Any, lr: dict) -> float | None:
    """Politika spotunun taban boyutu / gerçek maliyet oranı (açık payla). Kayıt yoksa None (gerçek boyut)."""
    bs = lr.get(BASELINE_SIZE_KEY)
    n = _num(bs.get("notional")) if isinstance(bs, dict) else None
    m0 = _num(getattr(o, "margin", None))
    if n is None or n <= 0 or m0 is None or m0 <= 0:
        return None
    return n * _open_frac(lr) / m0


def baseline_spot_delta(state: Any, spot_by_symbol: dict[str, Any] | None) -> float:
    """Taban görünümünün spot MALİYET farkı (görünüm − gerçek; 2026-09-28, öğrenme modu — üçüncü doğrulama turu):
    öğrenme-ekstra spot çıkarılır (−maliyet), politika spotu taban boyutuna ölçeklenir. Görünümün serbest spot nakdi =
    gerçek nakit − fark. Etiket yoksa 0."""
    tags = spot_by_symbol or {}
    delta = 0.0
    for o in list(getattr(state, "open_positions", None) or []):
        lr = tags.get(o.symbol) if getattr(o, "market_type", None) == "SPOT" else None
        if not isinstance(lr, dict):
            continue
        m0 = float(_num(getattr(o, "margin", None)) or 0.0)
        if lr.get("learning_unlocked_by"):
            delta -= m0
            continue
        k = _spot_scale(o, lr)
        if k is not None:
            delta += m0 * k - m0
    return delta


__all__ = ["CLASS_CAPACITY", "CLASS_POLICY", "CLASS_SELECTIVITY", "DEFAULT_EXTRA_ENTRIES", "EXTRA_ENTRIES_MODES",
           "EXTRA_OPEN", "EXTRA_RECORD_SELECTIVITY", "LEARNING_RECORD_ONLY", "RECORD_ONLY_FEATURE", "UNLOCK_BOX_EXCEPTION",
           "UNLOCK_CAPACITY", "UNLOCK_CLASSES", "UNLOCK_SELECTIVITY", "UNLOCK_SELECTIVITY_PREFIXES", "classify_unlock_code",
           "classify_unlock_codes", "extra_entries_mode", "is_record_only_cf", "record_only", "selectivity_codes",
           "BOOK_NAMES", "OVERRIDE_KEYS", "LIST_OVERRIDE_KEYS", "SIZE_SLOT", "SIZE_BUMP", "SIZE_SHRUNK", "SIZE_RULES",
           "HARD_CAP_PCT", "RISK_NOTIONAL_ROUND_TOL", "SYMBOLS_UNIVERSE", "STATE_DISABLED", "STATE_ACTIVE", "SUSPENDED_PREFIX",
           "LEVERAGE_FALLBACK", "LEVERAGE_FALLBACK_REASON", "COUNTERFACTUAL_OK", "COUNTERFACTUAL_NEVER",
           "BookLearningCfg", "BookLearning", "LearningMode", "FitResult", "profile_for", "fit_size",
           "leverage_fallback", "counterfactual_ok", "POLICY_RESERVE_SLOTS", "POLICY_RESERVE_MAX_PCT",
           "BASELINE_SIZE_KEY", "OPEN_FRAC_KEY", "WHY_POLICY_RESERVE", "policy_reserve_usdt", "fit_with_reserve",
           "learning_tags", "baseline_size_tag", "baseline_view", "baseline_spot_delta"]

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
from typing import Any, Callable, Iterable

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

    def to_dict(self) -> dict:
        return asdict(self)


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
            return {"enabled": self.enabled, "active": self._on, "reason": self._reason, "since": self._since,
                    "books": sorted(n for n, b in self._books.items() if b.enabled)}

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
                            symbols=bc.symbols, hard_cap_pct=HARD_CAP_PCT)

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


__all__ = ["BOOK_NAMES", "OVERRIDE_KEYS", "LIST_OVERRIDE_KEYS", "SIZE_SLOT", "SIZE_BUMP", "SIZE_SHRUNK", "SIZE_RULES",
           "HARD_CAP_PCT", "SYMBOLS_UNIVERSE", "STATE_DISABLED", "STATE_ACTIVE", "SUSPENDED_PREFIX",
           "LEVERAGE_FALLBACK", "LEVERAGE_FALLBACK_REASON", "COUNTERFACTUAL_OK", "COUNTERFACTUAL_NEVER",
           "BookLearningCfg", "BookLearning", "LearningMode", "FitResult", "profile_for", "fit_size",
           "leverage_fallback", "counterfactual_ok"]

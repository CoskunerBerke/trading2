"""ORTAK DENEYİM KATMANI v1 — satır kurucuları (2026-09-29). SAF modül: dosya/ağ YOK, girdisini DEĞİŞTİRMEZ.

Tek hafıza, üç satır türü (şema `shared_experience_row_v1`, append-only JSONL; depo `store.ExperienceStore`):

* `xp_entry`   — gerçek işlemin girişi (rev 0; girişteki durum anlık görüntüsü `snapshot` burada).
* `xp_outcome` — gerçek işlemin kapanışı; geç funding uzlaştırması sonucu değiştirirse YENİ revizyon (`rev + 1`).
* `xp_cf`      — karşı-olgusal (açılmayan geçerli sinyal); etiketlenme, v1 → v2+ net yeniden etiketleme ve kaybolma
  (değiştirildi / süresi doldu / düşürüldü) yeni revizyondur. rev 0 durum anlık görüntüsünü taşır.

Bağlayıcı kararlar `docs/ortak_deneyim/KARARLAR.md` (SPEC_V1'e ÜSTÜNDÜR); tasarım `docs/ortak_deneyim/SPEC_V1.md` §5.

Sözleşmeler (2026-09-29):

* `row_id = stable_id("xp", kind, book, src_id, rev)` (16 hex) DETERMİNİSTİKTİR ve `decision_id`e eşittir: aynı kaynak
  kaydın aynı revizyonu kaç kez kurulursa kursun aynı kimliği alır → depo idempotenttir (tekrar yazılmaz).
* Birleştirme anahtarları: gerçek işlem `trade_key = book|trade_id|opened_at` (defter sıfırlanınca aynı `F00001` başka
  `opened_at` ile ayrı işlemdir); karşı-olgusal `join_key = book|signal_key|symbol|side|variation` — kayıtçının tekillik
  anahtarıyla (`learning_cf.CounterfactualRecorder._key`) AYNI sıra; `cf_id_for` kayıtçının kimliğini yeniden üretir.
* Kurulum: `setup_key = book|setup_type|side` (KARARLAR: `book` = defter anahtarı), kaba aile `family` ∈ TREND / FADE /
  BREAKOUT / CANDLE_PATTERN / MOMENTUM ve `family_key = family|side`. Zıt mantıklar (trend ↔ fade) aynı aileye DÜŞMEZ.
* Kohort (`scripts/bot_scorecard.py` ile BİREBİR): `learning_unlocked_by` dolu → LEARNING_EXTRA; `features.learning`
  sözlük ama liste boş → POLICY; etiket yok → UNTAGGED. `pre_learning` = etiket yok VE giriş (yoksa kapanış) anı
  `learning_mode_since`ten önce (ya da `since` bilinmiyor) — karnenin `_after` kuralının tersi. `scorecard_class` karnenin
  dilimidir: before / policy / learning_extra (etiketsiz-sonra karnede POLİTİKA sayılır).
* Gerçek R (KARARLAR 1): `r_net = r_multiple` (defter, net); `cost_r = (ücret + kayma + ödenen net funding) / risk_usdt`
  (HEPSİ DAHİL — karşı-olgusal `cost_r` ile aynı taban); `r_gross = r_net + cost_r` (referans fiyatlarda, maliyetsiz R).
* Karşı-olgusal net (KARARLAR 1): `r_net` YALNIZ `label_version` = `cf_label_v2` ve sonrası (v2, v3, …) ve sonlu
  `r_net` varsa dolar (`r_basis = NET`, `in_net_stats = True`). Sürüm yok / `cf_label_v1` / `cf_label_v1c` → `r_net = null`,
  `r_basis = GROSS_LEGACY` (v1c'nin kapalı-biçim tahmini yalnız `r_net_approx` bilgisidir, net istatistiğe ASLA girmez);
  tanınmayan sürüm → UNKNOWN_LABEL_VERSION; v2+ ama net üretilememiş → NET_UNAVAILABLE. Kazanç: `r_net > 0` (karne).
* YALNIZ PAPER, YALNIZ USDM_PERP: `env.app_mode != "PAPER"` ya da SPOT kaynak → `RowError` (SPOT v1'de kapsam dışı,
  çağıran sayar). Sonsuz/NaN değer satıra ÇIKMAZ (`null`).
"""
from __future__ import annotations

import hashlib
import math
import numbers
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping

from ..core import from_iso, stable_id
# Şema/tür/sürüm TEK kaynaktan: paket başı (2026-09-29; `__init__` bilinçli olarak hiçbir şey içe aktarmaz).
from . import KINDS, LAYER_VERSION, ROW_SCHEMA

# ============================================================================ sabitler
KIND_ENTRY, KIND_OUTCOME, KIND_CF = "xp_entry", "xp_outcome", "xp_cf"
if KINDS != (KIND_ENTRY, KIND_OUTCOME, KIND_CF):          # paket başıyla sapma sessiz kalmasın
    raise ImportError("shared_experience KINDS uyuşmuyor: %r" % (KINDS,))
MARKET = "USDM_PERP"
APP_MODE_PAPER = "PAPER"
LONG, SHORT = "LONG", "SHORT"

#: Defter türleri (satırın `book_type` alanı).
MAIN, STRATEGY, PATTERN = "MAIN", "STRATEGY", "PATTERN"
#: Defter anahtarı (defter klasörü; ana bot "main") → (öğrenme modu adı `learning_mode.BOOK_NAMES`, tür). Anahtarlar
#: `scripts/bot_scorecard.BOOKS` klasörleriyle, adlar `learning_mode.BOOK_NAMES` ile BİREBİR (test korur).
BOOKS: dict[str, tuple[str, str]] = {
    "main": ("main", MAIN),
    "strategy_paper": ("t2_trend_regime", STRATEGY),
    "strategy_paper_m2": ("m2_tsmom28", STRATEGY),
    "strategy_paper_box": ("b1_box_fade", STRATEGY),
    "strategy_paper_trend4h": ("d4_donchian_20_10", STRATEGY),
    "strategy_paper_candle4h": ("c4_candle_variations", STRATEGY),
    "strategy_paper_candle4h_strict": ("c4s_candle_variations_strict", STRATEGY),
    "pattern_trader": ("pattern_trader", PATTERN),
}
_BOOK_BY_NAME = {name: key for key, (name, _t) in BOOKS.items()}

#: Satır kökeni.
LIVE, BACKFILL_OPEN, BACKFILL_HISTORY, BACKFILL_CF = "LIVE", "BACKFILL_OPEN", "BACKFILL_HISTORY", "BACKFILL_CF"
ORIGINS = (LIVE, BACKFILL_OPEN, BACKFILL_HISTORY, BACKFILL_CF)
#: Durum anlık görüntüsü (situation_v1) durumları (SPEC §4.3).
SNAPSHOT_STATUSES = ("OK", "PARTIAL", "GAP", "NO_BARS", "ERROR")

# ---------------------------------------------------------------------------- aileler (KARARLAR: kaba havuz)
TREND, FADE, BREAKOUT, CANDLE_PATTERN, MOMENTUM = "TREND", "FADE", "BREAKOUT", "CANDLE_PATTERN", "MOMENTUM"
FAMILIES = (TREND, FADE, BREAKOUT, CANDLE_PATTERN, MOMENTUM)
#: Tek mantıklı defterler (2026-09-29): ailenin kaynağı defterin KURALIDIR, `setup_type` değil — T2 ve M2 ikisi de
#: `setup_type="trend"` yazar ama M2 zaman serisi momentumudur (TSMOM28).
_BOOK_FAMILY: dict[str, str | None] = {
    "strategy_paper": TREND,                    # T2: EMA200 trend rejimi
    "strategy_paper_m2": MOMENTUM,              # M2: 28 günlük zaman serisi momentumu
    "strategy_paper_box": FADE,                 # Box: kutu kenarından ortaya dönüş
    "strategy_paper_trend4h": BREAKOUT,         # D4: Donchian 20 kanal kırılımı (DONCHIAN20_BREAKOUT)
    "strategy_paper_candle4h": CANDLE_PATTERN,  # C4: mum varyasyonları
    # C4S (2026-09-29, inceleme bulgusu): C4'ün AYNI kuralı, AYNI coinler, AYNI sinyaller — aile havuzuna katılırsa her
    # piyasa olayı İKİ kez sayılır (n = 30'a 15 bağımsız sinyalle ulaşılır, iid aralık yapay dar). Kurulum anahtarı
    # (`strategy_paper_candle4h_strict|…`) ayrı durur; aile havuzu DIŞINDADIR.
    "strategy_paper_candle4h_strict": None,
}
#: Ana bot `plan.entry_type` (brifing Türkçe adları da) → aile. `market` = anlık fiyattan konsensüs yönünde giriş.
_MAIN_FAMILY = {"breakout": BREAKOUT, "kırılım": BREAKOUT, "pullback": TREND, "geri çekilme": TREND, "market": TREND}
#: Formasyon planı ailesi → kaba aile (klasik, v2 katalog, v3 momentum protokolleri; test tam kapsamı korur).
_PATTERN_FAMILY = {
    "A_TREND_PULLBACK": TREND, "A2_TREND_PULLBACK": TREND,
    "B_LEVEL_REVERSAL": FADE, "B2_LEVEL_REVERSAL": FADE, "E2_SWEEP_RECLAIM": FADE,
    "C_COMPRESSION_BREAKOUT": BREAKOUT, "C2_COMPRESSION": BREAKOUT, "D2_CHART_STRUCTURE": BREAKOUT,
    "F2_RETEST_HOLD": BREAKOUT,
    "M3_MOMENTUM_3WS_RSI70": MOMENTUM,
}

# ---------------------------------------------------------------------------- kohort (bot_scorecard ile aynı)
UNTAGGED, POLICY, LEARNING_EXTRA = "UNTAGGED", "POLICY", "LEARNING_EXTRA"
COHORTS = (UNTAGGED, POLICY, LEARNING_EXTRA)
#: Karnenin dilim adları (`bot_scorecard.learning_split`: "before", POLICY="policy", LEARNING_EXTRA="learning_extra").
SC_BEFORE, SC_POLICY, SC_LEARNING_EXTRA = "before", "policy", "learning_extra"
#: `learning_mode.BASELINE_SIZE_KEY` (test eşitliği korur; saf modül öğrenme modunu içe aktarmaz).
BASELINE_SIZE_KEY = "baseline_size"

# ---------------------------------------------------------------------------- R / etiket
REAL_LABEL_VERSION = "ledger_v2"
CF_LABEL_V1, CF_LABEL_V1C = "cf_label_v1", "cf_label_v1c"
_CF_LABEL_RE = re.compile(r"^cf_label_v(\d+)$")
#: Net taban başlangıcı: `cf_label_v2` ve sonrası (v2 = bar tek tick, v3 = bar içi nedensel yol; ikisi de `r_net` taşır).
CF_NET_MIN_VERSION = 2
NET, GROSS_LEGACY, UNKNOWN_LABEL_VERSION, NET_UNAVAILABLE = "NET", "GROSS_LEGACY", "UNKNOWN_LABEL_VERSION", "NET_UNAVAILABLE"
R_BASES = (NET, GROSS_LEGACY, UNKNOWN_LABEL_VERSION, NET_UNAVAILABLE)
#: `label2`: WIN yalnız `r_net > 0` (KARARLAR 4, karne). `label3`: |R| < 0,25 SCRATCH (`learn.labels` kuralı).
WIN, LOSS, SCRATCH = "WIN", "LOSS", "SCRATCH"
SCRATCH_R = 0.25

# ---------------------------------------------------------------------------- revizyon / karşı-olgusal durum
#: Geç funding penceresi (`FuturesLedgerV2.settle_late_funding(window=500)`) ve kesinleşme süresi (SPEC §5.3).
LATE_FUNDING_WINDOW = 500
FINAL_AFTER_H = 72.0
#: `learning_cf.STALE_GRACE_BARS` (test eşitliği korur).
STALE_GRACE_BARS = 10
PENDING, LABELLED, SUPERSEDED, EXPIRED, DROPPED, VANISHED = ("PENDING", "LABELLED", "SUPERSEDED", "EXPIRED", "DROPPED",
                                                             "VANISHED")
CF_STATUSES = (PENDING, LABELLED, SUPERSEDED, EXPIRED, DROPPED, VANISHED)
#: Kaybolmuş (aktif listeden düşmüş) karşı-olgusal durumları — nihai, net istatistiğe GİRMEZ (yalnız kapsam sayımı).
VANISHED_STATUSES = (SUPERSEDED, EXPIRED, DROPPED, VANISHED)

#: Karşı-olgusal neden aileleri (SPEC §5.6). Önek eşleşmesi `*` ile biten adlar için.
_REASON_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CAPACITY", ("TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN", "MIN_ORDER_CONFLICT", "NO_TRADE_MIN_ORDER_CONFLICT",
                  "MAX_POSITION_PCT", "RISK_ABOVE_CAP_AFTER_ROUNDING", "SPOT_ALLOCATION", "RISK_ENGINE_BLOCKED",
                  "RISK_CAPACITY_BLOCKED")),
    ("EXCHANGE", ("MIN_NOTIONAL", "MIN_QTY", "STEP_ZERO_QTY", "MAX_QTY", "LEVERAGE_TOO_HIGH")),
    ("OCCUPANCY", ("POSITION_OPEN", "ALREADY_OPEN*", "SAME_SYMBOL_POSITION_OPEN", "OPPOSITE_EXPOSURE_CONFLICT")),
    ("PARITY", ("RISK_OUTSIDE_TESTED_RANGE", "CHASE_LIMIT", "EXPIRED_BEFORE_ENTRY", "ENTRY_DRIFT", "ALSO_MATCHED")),
    ("GATE", ("CANDLE_VETO*", "CHART_VETO*", "REGIME_VETO*", "STRUCTURE*", "NEGATIVE_NET_EDGE", "RESEARCH_SIZE_ONLY",
              "COSTS_EXCEED_EDGE", "LEVERAGE_GATE_BLOCKED", "KILL_SWITCH_ACTIVE", "CHIEF_BLOCKED")),
    ("LIQUIDITY", ("LIQUIDITY_UNKNOWN",)),
)

#: Beyaz listeler (SPEC §5.2 / §5.6) — satır sınırlı kalır; ham diziler ve büyük sözlükler KOPYALANMAZ.
_LEARNING_KEYS = ("size_rule", "slots", "risk_pct", "leverage", "leverage_max", "risk_usdt", "risk_fraction_of_budget",
                  "equity_basis", "learning_unlocked_by", "exploration", "leverage_fallback")
_POLICY_BASIS_KEYS = ("policy_basis", "learning_codes", "policy_codes", "p_win", "p_win_policy",
                      "conservative_net_edge_r_policy", "size_multiplier_policy")
_STRUCTURE_KEYS = ("action", "reason_code", "shadow")
_VARIATION_KEYS = ("id", "definition_sha", "target_r", "max_hold_bars", "observation")
_GATES = ("regime_gate", "candle_confirmation", "chart_confirmation")
_CF_FIT_KEYS = ("size_rule", "reason", "why", "policy_grade")
_CF_OUTCOME_KEYS = ("exit_reason", "bars", "mfe_pct", "mae_pct", "label_method", "label_kind", "approx")
_MAX_LIST = 16
_MAX_DEPTH = 6


class RowError(ValueError):
    """Satır KURULAMAZ (geçersiz/eksik kimlik, SPOT, PAPER dışı, bilinmeyen defter). Çağıran sayar; karar ETKİLENMEZ."""


# ============================================================================ küçük yardımcılar
def _get(src: Any, key: str, default: Any = None) -> Any:
    """Sözlük ya da nesne (TradeRecord, Position, ShadowTrade, adaptör olgu sınıfı) — ikisini de okur."""
    if src is None:
        return default
    if isinstance(src, Mapping):
        return src.get(key, default)
    return getattr(src, key, default)


def _f(x: Any) -> float | None:
    """Sonlu float ya da None (Decimal ve sayısal metin kabul; bool/NaN/sonsuz → None)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return v if math.isfinite(v) else None


def _dec(x: Any) -> Decimal | None:
    """Kesin aritmetik için Decimal (float → metin üzerinden); geçersiz/sonsuz → None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        d = x if isinstance(x, Decimal) else Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def _r6(x: Any) -> float | None:
    v = _f(x)
    return None if v is None else round(v, 6)


def _s(x: Any) -> str | None:
    if x is None:
        return None
    v = getattr(x, "value", x) if isinstance(x, Enum) else x
    v = str(v)
    return v or None


def _side(x: Any) -> str:
    s = (_s(x) or "").upper()
    if s not in (LONG, SHORT):
        raise RowError("SIDE_INVALID:%s" % (s or "?"))
    return s


def _ts(x: Any) -> datetime | None:
    """ISO → aware UTC datetime; boş/okunamaz → None (karnenin `_ts`i ile aynı)."""
    if isinstance(x, datetime):
        return x if x.tzinfo is not None else x.replace(tzinfo=timezone.utc)
    try:
        return from_iso(str(x)) if x else None
    except (TypeError, ValueError):
        return None


def _ms(x: Any) -> int | None:
    dt = _ts(x)
    return int(dt.timestamp() * 1000) if dt is not None else None


def _clean(x: Any, depth: int = 0) -> Any:
    """JSON-güvenli DERİN KOPYA: sonlu olmayan sayı → None, Decimal → float, enum → değer, datetime → ISO; sözlük
    anahtarları metin; listeler `_MAX_LIST` ile kırpılır. Girdiyi DEĞİŞTİRMEZ."""
    if depth > _MAX_DEPTH:
        return None
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, Enum):
        return _clean(x.value, depth + 1)
    if type(x).__module__ == "numpy" and type(x).__name__ in ("bool_", "bool"):   # numpy bool (2026-09-29): metin olmasın
        return bool(x)
    if isinstance(x, numbers.Integral):                     # int + numpy tamsayıları
        return int(x)
    if isinstance(x, (numbers.Real, Decimal)):              # float + numpy float + Decimal
        return _f(x)
    if isinstance(x, datetime):
        return _ts(x).isoformat()
    if isinstance(x, Mapping):
        return {str(k): _clean(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        seq = sorted(x, key=str) if isinstance(x, (set, frozenset)) else list(x)
        return [_clean(v, depth + 1) for v in seq[:_MAX_LIST]]
    return str(x)


def _pick(d: Any, keys: Iterable[str]) -> dict[str, Any] | None:
    if not isinstance(d, Mapping):
        return None
    out = {k: _clean(d[k], 1) for k in keys if k in d and d[k] is not None}
    return out or None


def _features(src: Any) -> dict[str, Any]:
    f = _get(src, "features")
    return f if isinstance(f, Mapping) else {}


def _book(book: str) -> tuple[str, str]:
    info = BOOKS.get(str(book))
    if info is None:
        raise RowError("BOOK_UNKNOWN:%s" % book)
    return info


# ============================================================================ kimlik ve anahtarlar
def row_id(kind: str, book: str, src_id: str, rev: int) -> str:
    """Deterministik satır kimliği (16 hex) — aynı (tür, defter, kaynak, revizyon) DAİMA aynı değer (idempotency)."""
    if kind not in KINDS:
        raise RowError("KIND_INVALID:%s" % kind)
    if not src_id:
        raise RowError("SRC_ID_MISSING")
    r = int(rev)
    if r < 0:
        raise RowError("REV_NEGATIVE")
    return stable_id("xp", kind, str(book), str(src_id), r)


def trade_key(book: str, trade_id: Any, opened_at: Any) -> str:
    """Gerçek işlemin birleştirme anahtarı `book|trade_id|opened_at` (defter sıfırlanınca aynı kimlik ayrı işlemdir)."""
    tid, oa = _s(trade_id), _s(opened_at)
    if not tid or not oa:
        raise RowError("TRADE_KEY_INCOMPLETE")
    return "%s|%s|%s" % (book, tid, oa)


def cf_key(book: str, cf_id: Any) -> str:
    cid = _s(cf_id)
    if not cid:
        raise RowError("CF_ID_MISSING")
    return "%s|%s" % (book, cid)


def join_key(*, book: str, signal_key: Any, symbol: Any, side: Any, variation: Any = None) -> str | None:
    """Sinyal birleştirme anahtarı `book|signal_key|symbol|side|variation` (kayıtçı tekillik sırası). Anahtarsız → None."""
    sk = _s(signal_key)
    if not sk:
        return None
    return "%s|%s|%s|%s|%s" % (book, sk, _s(symbol) or "", _side(side), _s(variation) or "")


def cf_id_for(book_name: str, signal_key: Any, symbol: Any, side: Any, variation: Any = None) -> str:
    """Kayıtçının deterministik karşı-olgusal kimliği (`learning_cf.CounterfactualRecorder.record`) — aynı sıra/biçim."""
    return "cf_" + stable_id("cf", str(book_name), str(signal_key), str(symbol), _side(side), str(variation or ""))


def book_for_name(book_name: str) -> str | None:
    """Öğrenme modu adı (`ShadowTrade.book`) → defter anahtarı."""
    return _BOOK_BY_NAME.get(str(book_name))


def setup_key(book: str, setup_type: Any, side: Any) -> str | None:
    """`book|setup_type|side` (KARARLAR). Kurulum bilinmiyorsa None (uydurulmaz)."""
    st = _s(setup_type)
    if not st:
        return None
    return "%s|%s|%s" % (book, st, _side(side))


def family_of(book: str, setup_type: Any) -> str | None:
    """Kaba aile (KARARLAR): tek mantıklı defterde kuraldan; ana botta giriş türünden; Formasyonda plan ailesinden.
    Eşlenmeyen kurulum → None (havuza KATILMAZ; yanlış aileye düşmektense dışarıda kalır)."""
    _book(book)
    if book in _BOOK_FAMILY:
        return _BOOK_FAMILY[book]
    st = _s(setup_type)
    if st is None:
        return None
    if book == "main":
        return _MAIN_FAMILY.get(st.strip().lower())
    return _PATTERN_FAMILY.get(st.strip().upper())


def family_key(family: str | None, side: Any) -> str | None:
    return None if not family else "%s|%s" % (family, _side(side))


def reason_family(reason: Any) -> str:
    """Karşı-olgusal red nedeni → aile (CAPACITY/EXCHANGE/OCCUPANCY/PARITY/GATE/LIQUIDITY/OTHER). `KOD:detay` → KOD."""
    head = (_s(reason) or "").strip().upper().split(":", 1)[0]
    for fam, names in _REASON_FAMILIES:
        for n in names:
            if head == n or (n.endswith("*") and head.startswith(n[:-1])):
                return fam
    return "OTHER"


# ============================================================================ kohort
def cohort(features: Any) -> str:
    """UNTAGGED / POLICY / LEARNING_EXTRA — `bot_scorecard.learning_class` ile aynı öğrenme etiketi okuması."""
    lr = features.get("learning") if isinstance(features, Mapping) else None
    if not isinstance(lr, Mapping):
        return UNTAGGED
    return LEARNING_EXTRA if lr.get("learning_unlocked_by") else POLICY


def pre_learning(features: Any, opened_at: Any, learning_since: Any, *, closed_at: Any = None) -> bool:
    """Öğrenme ÖNCESİ mi? Karnenin `_after` kuralının TERSİ (2026-09-29): etiketli işlem daima sonradır; etiketsizde
    giriş (yoksa kapanış) anı `learning_since`e eşit/sonra ise sonradır; `since` ya da an bilinmiyorsa önce sayılır."""
    if isinstance(features, Mapping) and isinstance(features.get("learning"), Mapping):
        return False
    cut = _ts(learning_since)
    at = _ts(opened_at) or _ts(closed_at)
    return not (cut is not None and at is not None and at >= cut)


def scorecard_class(cohort_: str, pre_learning_: bool) -> str:
    """Karnenin dilimi: önce → "before"; sonra içinde ekstra → "learning_extra"; diğer (etiketsiz-sonra dahil) → "policy"."""
    if pre_learning_:
        return SC_BEFORE
    return SC_LEARNING_EXTRA if cohort_ == LEARNING_EXTRA else SC_POLICY


def _cohort_fields(features: Mapping[str, Any], opened_at: Any, env: Mapping[str, Any], closed_at: Any = None) -> dict:
    c = cohort(features)
    pre = pre_learning(features, opened_at, env.get("learning_since"), closed_at=closed_at)
    return {"cohort": c, "pre_learning": pre, "scorecard_class": scorecard_class(c, pre)}


# ============================================================================ projeksiyonlar
def project_learning(features: Any) -> dict[str, Any] | None:
    """`features.learning` beyaz liste projeksiyonu (SPEC §5.2); `baseline_size` sözlüğü kopyalanmaz, yalnız varlığı."""
    lr = features.get("learning") if isinstance(features, Mapping) else None
    if not isinstance(lr, Mapping):
        return None
    out: dict[str, Any] = {k: _clean(lr[k], 1) for k in _LEARNING_KEYS if k in lr}
    if "learning_unlocked_by" in out and not isinstance(out["learning_unlocked_by"], list):
        out["learning_unlocked_by"] = [] if out["learning_unlocked_by"] in (None, "") else [out["learning_unlocked_by"]]
    pb = _pick(lr.get("policy_basis"), _POLICY_BASIS_KEYS)
    if pb is not None:
        out["policy_basis"] = pb
    out["has_baseline_size"] = isinstance(lr.get(BASELINE_SIZE_KEY), Mapping)
    return out


def _gate_verdict(g: Any) -> Any:
    return _clean(g.get("verdict"), 1) if isinstance(g, Mapping) else None


def book_ctx(book_type: str, features: Any, meta: Any = None) -> dict[str, Any]:
    """Defter türüne göre girişteki karar bağlamı (≤ 16 alan, SPEC §5.2). `meta` yalnız açık pozisyonda vardır."""
    f = features if isinstance(features, Mapping) else {}
    m = meta if isinstance(meta, Mapping) else {}
    out: dict[str, Any] = {}
    if book_type == MAIN:
        for k in ("p_win", "expected_r", "regime", "consensus_score", "consensus_conf", "n_vetoes", "expected_cost_pct",
                  "spread_pct"):
            out[k] = _clean(f.get(k), 1)
        out["structure"] = _pick(f.get("structure"), _STRUCTURE_KEYS)
        for g in _GATES:
            out[g + "_verdict"] = _gate_verdict(f.get(g))
    elif book_type == STRATEGY:
        for k in ("strategy", "regime", "expected_r"):
            out[k] = _clean(f.get(k), 1)
        out["structure"] = _pick(f.get("structure"), _STRUCTURE_KEYS)
        out["candle_variation"] = _pick(f.get("candle_variation"), _VARIATION_KEYS)
    elif book_type == PATTERN:
        for k in ("family", "cohort", "rr_after_cost", "target_source", "side_rule", "age_h_at_entry"):
            out[k] = _clean(f.get(k, m.get(k)), 1)
        out["protocol_version"] = _clean(f.get("protocol_version", m.get("protocol_version")), 1)
    else:
        raise RowError("BOOK_TYPE_INVALID:%s" % book_type)
    return {k: v for k, v in out.items() if v is not None}


def cf_features(features: Any, *, book_type: str) -> dict[str, Any] | None:
    """Karşı-olgusal kaydın özellik beyaz listesi (SPEC §5.6). Karşı-olgusalda `learning` = uyum kaydı (`fit`)."""
    f = features if isinstance(features, Mapping) else None
    if not f:
        return None
    out: dict[str, Any] = {}
    for k in ("strategy", "setup_type", "regime", "signal_close", "atr14", "lab_algo", "plan_id", "family",
              "protocol_version", "cohort", "entry_tf"):
        if f.get(k) is not None:
            out[k] = _clean(f[k], 1)
    st = _pick(f.get("structure"), ("action", "reason_code"))
    if st:
        out["structure"] = st
    fit = _pick(f.get("learning"), _CF_FIT_KEYS)
    if fit:
        out["learning_fit"] = fit
    cv = _pick(f.get("candle_variation"), _VARIATION_KEYS)
    if cv:
        out["candle_variation"] = cv
    if book_type == MAIN:
        for k in ("p_win", "expected_r", "conservative_net_edge_r", "net_expectancy_r"):
            if f.get(k) is not None:
                out[k] = _clean(f[k], 1)
        for g in _GATES:
            v = _gate_verdict(f.get(g))
            if v is not None:
                out[g + "_verdict"] = v
    return out or None


# ============================================================================ R hesapları
def _label2(r: float | None) -> str | None:
    return None if r is None else (WIN if r > 0 else LOSS)


def _label3(r: float | None) -> str | None:
    if r is None:
        return None
    return WIN if r >= SCRATCH_R else (LOSS if r <= -SCRATCH_R else SCRATCH)


def _risk_usdt(closed: Any) -> Decimal | None:
    """R paydası: `features.risk_usdt` (defter `_finalize`); yoksa defterin geç funding yedeğiyle AYNI tanım
    (|giriş − features.initial_stop| × miktar). Bilinmiyorsa None (maliyet ayrıştırması yapılmaz, uydurulmaz)."""
    f = _features(closed)
    r = _dec(f.get("risk_usdt"))
    if r is not None and r > 0:
        return r
    e, st, q = _dec(_get(closed, "entry", _get(closed, "entry_avg"))), _dec(f.get("initial_stop")), _dec(_get(closed, "quantity"))
    if e is not None and st is not None and q is not None:
        r = abs(e - st) * q
        return r if r > 0 else None
    return None


def real_r(closed: Any) -> dict[str, Any]:
    """Kapanmış gerçek işlemin R ayrıştırması (KARARLAR 1 — maliyet HEPSİ DAHİL; SPEC §5.3):

    `r_net = r_multiple` (defter); `fee_r = (giriş + çıkış ücreti)/risk`; `slippage_r = kayma/risk`;
    `funding_r = (ödenen − alınan funding)/risk` (+ = maliyet); `cost_r = fee_r + slippage_r + funding_r`;
    `r_gross = r_net + cost_r` (referans fiyatlarda maliyetsiz R); `r_gross_fill = brüt_pnl/risk` (dolum fiyatlarında,
    yalnız bilgi). `mfe_r`/`mae_r` = MFE/MAE yüzdesi ÷ giriş-stop yüzdesi."""
    r_dec = _dec(_get(closed, "r_multiple"))
    r_net = _f(r_dec)
    risk = _risk_usdt(closed)
    entry = _dec(_get(closed, "entry", _get(closed, "entry_avg")))
    qty = _dec(_get(closed, "quantity"))
    out: dict[str, Any] = {"label_version": REAL_LABEL_VERSION, "r_basis": NET, "in_net_stats": r_net is not None,
                           "r_net": _r6(r_net), "risk_usdt": _r6(risk), "risk_known": risk is not None}
    fee_r = slippage_r = funding_r = cost_r = r_gross = r_gross_fill = risk_pct = None
    if risk is not None:
        entry_fee, exit_fee = _dec(_get(closed, "entry_fee")), _dec(_get(closed, "exit_fee"))
        fees = (entry_fee + exit_fee) if (entry_fee is not None and exit_fee is not None) else _dec(_get(closed, "fees"))
        slip = _dec(_get(closed, "slippage_cost"))
        paid, recv = _dec(_get(closed, "funding_paid")), _dec(_get(closed, "funding_received"))
        if paid is not None and recv is not None:
            fnet = paid - recv
        else:                                            # eski sözlük: yalnız net funding (− ödendi, + alındı)
            fund = _dec(_get(closed, "funding"))
            fnet = -fund if fund is not None else None
        if fees is not None:
            fee_r = fees / risk
        if slip is not None:
            slippage_r = slip / risk
        if fnet is not None:
            funding_r = fnet / risk
        if r_dec is not None and None not in (fee_r, slippage_r, funding_r):
            cost_r = fee_r + slippage_r + funding_r
            r_gross = r_dec + cost_r
        gp = _dec(_get(closed, "gross_pnl"))
        if gp is not None:
            r_gross_fill = gp / risk
        if entry is not None and qty is not None and qty > 0 and entry > 0:
            risk_pct = Decimal(100) * risk / (qty * entry)
    mfe, mae = _f(_get(closed, "mfe_pct")), _f(_get(closed, "mae_pct"))
    rp = _f(risk_pct)
    out.update({"cost_r": _r6(cost_r), "r_gross": _r6(r_gross), "fee_r": _r6(fee_r), "slippage_r": _r6(slippage_r),
                "funding_r": _r6(funding_r), "r_gross_fill": _r6(r_gross_fill), "risk_pct": _r6(rp),
                "mfe_pct": _r6(mfe), "mae_pct": _r6(mae),
                "mfe_r": _r6(mfe / rp) if (mfe is not None and rp) else None,
                "mae_r": _r6(mae / rp) if (mae is not None and rp) else None,
                "label2": _label2(r_net), "label3": _label3(r_net)})
    return out


def cf_label_class(label_version: Any) -> str:
    """Etiket sürümü sınıfı: NET (cf_label_v2+), GROSS_LEGACY (yok / v1 / v1c), UNKNOWN_LABEL_VERSION."""
    if label_version is None:
        return GROSS_LEGACY
    lv = str(label_version)
    if lv in (CF_LABEL_V1, CF_LABEL_V1C):
        return GROSS_LEGACY
    m = _CF_LABEL_RE.match(lv)
    if m and int(m.group(1)) >= CF_NET_MIN_VERSION:
        return NET
    return UNKNOWN_LABEL_VERSION


def _risk_pct_ref(entry: Any, stop: Any) -> float | None:
    e, s = _f(entry), _f(stop)
    if e is None or s is None or e <= 0 or e == s:
        return None
    return abs(e - s) / e * 100.0


def cf_r(outcome: Any, *, entry: Any, stop: Any, tf_minutes: Any = None) -> dict[str, Any]:
    """Karşı-olgusal sonucun R alanları (KARARLAR 1). `r_net` YALNIZ cf_label_v2+ ve sonlu net varken; aksi halde null.
    Etiketsiz (bekleyen) kayıt → boş sözlük."""
    if not isinstance(outcome, Mapping):
        return {}
    lv = _s(outcome.get("label_version"))
    cls = cf_label_class(lv)
    gross = _f(outcome.get("r_gross")) if cls == NET else None
    if gross is None:
        gross = _f(outcome.get("r_multiple"))
    r_net = cost = None
    basis = cls
    if cls == NET:
        r_net = _f(outcome.get("r_net"))
        if r_net is None:
            basis = NET_UNAVAILABLE
        else:
            cost = _f(outcome.get("cost_r"))
            if cost is None and gross is not None:
                cost = gross - r_net
    rp = _risk_pct_ref(entry, stop)
    mfe, mae = _f(outcome.get("mfe_pct")), _f(outcome.get("mae_pct"))
    bars, tfm = _f(outcome.get("bars")), _f(tf_minutes)
    out: dict[str, Any] = {
        "label_version": lv, "r_basis": basis, "in_net_stats": basis == NET,
        "r_gross": _r6(gross), "r_net": _r6(r_net), "cost_r": _r6(cost),
        "mfe_r": _r6(mfe / rp) if (mfe is not None and rp) else None,
        "mae_r": _r6(mae / rp) if (mae is not None and rp) else None,
        "hold_hours": _r6(bars * tfm / 60.0) if (bars is not None and tfm) else None,
        "label2": _label2(r_net), "label3": _label3(r_net)}
    if cls == NET:
        out.update({"won_net": outcome.get("won_net") if isinstance(outcome.get("won_net"), bool) else None,
                    "funding_complete": (outcome.get("funding_complete")
                                         if isinstance(outcome.get("funding_complete"), bool) else None),
                    "net_status": _s(outcome.get("net_status")), "net_exit_reason": _s(outcome.get("net_exit_reason")),
                    "cost_parts_r": _clean(outcome.get("cost_parts_r")) if isinstance(outcome.get("cost_parts_r"),
                                                                                        Mapping) else None})
        # (2026-09-29, inceleme bulgusu) bar içi sırası belirsiz net etiket (`learning_cf._bar_path`): satır süzülebilsin
        amb = outcome.get("intrabar_ambiguous_bars")
        out["intrabar_ambiguous"] = (amb > 0) if isinstance(amb, int) and not isinstance(amb, bool) else None
    elif lv == CF_LABEL_V1C:
        # v1c kapalı-biçim tahmini (2026-09-29): YALNIZ bilgi; `r_net` ve net istatistik ASLA değildir
        out["r_net_approx"] = _r6(outcome.get("r_net_approx"))
        out["net_status"] = _s(outcome.get("net_status"))
    elif lv == CF_LABEL_V1:
        out["net_status"] = _s(outcome.get("net_status"))
    return out


# ============================================================================ revizyon
def _fp(*parts: Any) -> str:
    return hashlib.sha256("|".join("" if p is None else repr(p) for p in parts).encode("utf-8")).hexdigest()[:8]


def outcome_fp(closed: Any) -> str:
    """Gerçek kapanış parmak izi (r_net, funding, funding_complete) — değişirse yeni revizyon (SPEC §5.3)."""
    cov = _features(closed).get("funding_coverage")
    comp = cov.get("complete") if isinstance(cov, Mapping) else None
    return _fp(_r6(_get(closed, "r_multiple")), _r6(_get(closed, "funding")), comp)


def cf_fp(status: str, outcome: Any) -> str:
    """Karşı-olgusal parmak izi sha8(durum, label_version, r_net, r_gross, exit_reason) — yerinde net yeniden
    etiketleme (v1 → v2+) ya da durum değişimi yeni revizyon üretir (SPEC §5.6)."""
    o = outcome if isinstance(outcome, Mapping) else {}
    return _fp(status, _s(o.get("label_version")), _r6(o.get("r_net")), _r6(o.get("r_gross", o.get("r_multiple"))),
               _s(o.get("exit_reason")))


def next_rev(prev: Mapping[str, Any] | None, fp: str) -> int | None:
    """Yazılacak revizyon: önceki yoksa 0; parmak izi aynıysa None (yazma yok); değiştiyse önceki + 1."""
    if not prev:
        return 0
    if str(prev.get("fp")) == str(fp):
        return None
    return int(prev.get("rev") or 0) + 1


def outcome_is_final(closed: Any, *, now: datetime, in_window: bool) -> bool:
    """Kapanış satırı kesin mi: funding kapsaması tam, ya da kayıt geç funding penceresinden çıktı, ya da 72 sa geçti."""
    cov = _features(closed).get("funding_coverage")
    if isinstance(cov, Mapping) and cov.get("complete") is True:
        return True
    if not in_window:
        return True
    ca = _ts(_get(closed, "closed_at"))
    return ca is None or (_ts(now) - ca).total_seconds() >= FINAL_AFTER_H * 3600.0


def classify_vanished(prev_status: str, *, superseded_by_real: bool, now: datetime, label_ts: Any,
                      tf_minutes: Any) -> str | None:
    """Aktif listeden kaybolan karşı-olgusal (özel alan OKUNMAZ, SPEC §5.6): aynı `join_key`li gerçek giriş varsa
    SUPERSEDED; bekleyen idiyse ufuk + (STALE_GRACE_BARS + 1) bar geçtiyse EXPIRED, değilse DROPPED (tavan/arşiv);
    etiketliydi → None (arşiv budaması, revizyon yok)."""
    if prev_status == LABELLED or prev_status in VANISHED_STATUSES:
        return None
    if superseded_by_real:
        return SUPERSEDED
    lt, tf = _ts(label_ts), _f(tf_minutes)
    if lt is not None and tf and (_ts(now) - lt).total_seconds() * 1000.0 > (STALE_GRACE_BARS + 1) * tf * 60_000.0:
        return EXPIRED
    return DROPPED


def latest_rows(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    """(tür, kaynak anahtarı) başına EN YÜKSEK revizyon (rapor kuralı). Eşit revizyonda ilk görülen kalır."""
    best: dict[tuple[str, str], Mapping[str, Any]] = {}
    for r in rows:
        kind = r.get("kind")
        key = r.get("cf_key") if kind == KIND_CF else r.get("trade_key")
        if kind not in KINDS or not key:
            continue
        k = (str(kind), str(key))
        cur = best.get(k)
        if cur is None or int(r.get("rev") or 0) > int(cur.get("rev") or 0):
            best[k] = r
    return best


# ============================================================================ satır kurucuları
def make_env(*, recorded_at: Any, app_mode: str = APP_MODE_PAPER, code_sha: str | None = None,
             config_hash: str | None = None, learning_since: str | None = None) -> dict[str, Any]:
    """Adım başına ortak zarf bağlamı. `learning_since` = `learning_mode.json` "since" (kohort için)."""
    ra = _ts(recorded_at)
    return {"recorded_at": ra.isoformat() if ra else _s(recorded_at), "app_mode": _s(app_mode),
            "code_sha": _s(code_sha), "config_hash": _s(config_hash), "learning_since": _s(learning_since)}


def _check_env(env: Mapping[str, Any]) -> None:
    if not isinstance(env, Mapping) or not env.get("recorded_at"):
        raise RowError("ENV_INCOMPLETE")
    if str(env.get("app_mode") or "").upper() != APP_MODE_PAPER:
        raise RowError("NOT_PAPER:%s" % env.get("app_mode"))


def _check_market(src: Any) -> None:
    mt = _s(_get(src, "market_type")) or MARKET
    if mt.upper() == "SPOT":
        raise RowError("SPOT_EXCLUDED")
    if mt.upper() != MARKET:
        raise RowError("MARKET_UNSUPPORTED:%s" % mt)


def _envelope(kind: str, *, book: str, src_id: str, rev: int, symbol: Any, side: str, setup_type: Any,
              variation: Any, signal_key: Any, signal_key_src: Any, origin: str, env: Mapping[str, Any]) -> dict:
    book_name, book_type = _book(book)
    _check_env(env)
    if origin not in ORIGINS:
        raise RowError("ORIGIN_INVALID:%s" % origin)
    sym = _s(symbol)
    if not sym:
        raise RowError("SYMBOL_MISSING")
    rid = row_id(kind, book, src_id, rev)
    st = _s(setup_type)
    fam = family_of(book, st)
    return {"schema": ROW_SCHEMA, "row_id": rid, "decision_id": rid, "kind": kind, "rev": int(rev),
            "book": book, "book_name": book_name, "book_type": book_type,
            "symbol": sym, "side": side, "market": MARKET,
            "setup_type": st, "variation": _s(variation), "setup_key": setup_key(book, st, side),
            "family": fam, "family_key": family_key(fam, side),
            "signal_key": _s(signal_key), "signal_key_src": _s(signal_key_src) if signal_key else None,
            "join_key": join_key(book=book, signal_key=signal_key, symbol=sym, side=side, variation=variation),
            "origin": origin, "recorded_at": _s(env.get("recorded_at")), "app_mode": APP_MODE_PAPER,
            "code_sha": _s(env.get("code_sha")), "config_hash": _s(env.get("config_hash")),
            "layer_version": LAYER_VERSION}


def _real_setup(book: str, src: Any) -> tuple[Any, Any]:
    """(setup_type, variation) — gerçek işlem: defterin `setup_type`ı (Formasyonda plan ailesi); C4 varyasyonu
    `features.candle_variation.id`."""
    f = _features(src)
    st = _s(_get(src, "setup_type"))
    if BOOKS[book][1] == PATTERN:
        st = _s(f.get("family")) or st
    cv = f.get("candle_variation")
    var = _s(cv.get("id")) if isinstance(cv, Mapping) else None
    return st, var


def _snapshot_fields(snapshot: Any, snapshot_status: Any, snapshot_meta: Any) -> dict[str, Any]:
    ss = _s(snapshot_status)
    if ss is not None and ss not in SNAPSHOT_STATUSES:
        raise RowError("SNAPSHOT_STATUS_INVALID:%s" % ss)
    return {"snapshot": _clean(snapshot) if isinstance(snapshot, Mapping) else None, "snapshot_status": ss,
            "snapshot_meta": _clean(snapshot_meta) if isinstance(snapshot_meta, Mapping) else None}


def entry_row(src: Any, *, book: str, env: Mapping[str, Any], origin: str = LIVE, signal_key: Any = None,
              signal_key_src: Any = None, snapshot: Any = None, snapshot_status: Any = None,
              snapshot_meta: Any = None) -> dict[str, Any]:
    """`xp_entry` (rev 0). `src`: açık `Position` ya da (iki adım arasında açılıp kapanmış) `TradeRecord` — nesne ya da
    sözlük. İlk stop: açıkta `initial_stop`; kapanmışta `giriş ∓ risk_usdt / miktar` (TradeRecord stop taşımaz)."""
    _check_market(src)
    side = _side(_get(src, "side"))
    tid, oa = _s(_get(src, "id")), _s(_get(src, "opened_at"))
    tk = trade_key(book, tid, oa)
    f = _features(src)
    st, var = _real_setup(book, src)
    row = _envelope(KIND_ENTRY, book=book, src_id=tk, rev=0, symbol=_get(src, "symbol"), side=side, setup_type=st,
                    variation=var, signal_key=signal_key, signal_key_src=signal_key_src, origin=origin, env=env)
    entry = _dec(_get(src, "entry_avg", _get(src, "entry")))
    qty = _dec(_get(src, "initial_qty")) or _dec(_get(src, "qty")) or _dec(_get(src, "quantity"))
    stop = _dec(_get(src, "initial_stop"))
    stop_src = "POSITION" if stop is not None else None
    risk = _risk_usdt(src)
    if stop is None and entry is not None and qty and risk is not None:
        stop = entry - risk / qty if side == LONG else entry + risk / qty
        stop_src = "RISK_USDT"
    if risk is None and entry is not None and stop is not None and qty:
        risk = abs(entry - stop) * qty
    tg = _get(src, "targets")
    row.update({
        "trade_key": tk, "trade_id": tid, "opened_at": oa, "as_of_ms": _ms(oa),
        "entry_px": _r6(entry), "initial_stop": _f(stop), "initial_stop_src": stop_src,
        "stop_dist_pct": _r6(_risk_pct_ref(entry, stop)),
        "targets": [_f(t) for t in list(tg)[:_MAX_LIST]] if isinstance(tg, (list, tuple)) else None,
        "leverage": _f(_get(src, "leverage")), "quantity": _f(qty),
        "notional": _r6(qty * entry) if (qty is not None and entry is not None) else None, "risk_usdt": _r6(risk),
        **_cohort_fields(f, oa, env, _get(src, "closed_at")),
        "learning": project_learning(f),
        "exploration": _clean(f.get("exploration"), 1),
        "in_lab_universe": f.get("in_lab_universe") if isinstance(f.get("in_lab_universe"), bool) else None,
        "book_ctx": book_ctx(BOOKS[book][1], f, _get(src, "meta")),
        **_snapshot_fields(snapshot, snapshot_status, snapshot_meta)})
    return row


def outcome_row(closed: Any, *, book: str, rev: int, final: bool, env: Mapping[str, Any], origin: str = LIVE,
                signal_key: Any = None, signal_key_src: Any = None) -> dict[str, Any]:
    """`xp_outcome` — kapanmış `TradeRecord` (nesne ya da sözlük). Revizyon ve `final` çağırandan (`next_rev`,
    `outcome_is_final`); R alanları `real_r`, parmak izi `outcome_fp`."""
    _check_market(closed)
    side = _side(_get(closed, "side"))
    tid, oa, ca = _s(_get(closed, "id")), _s(_get(closed, "opened_at")), _s(_get(closed, "closed_at"))
    if not ca:
        raise RowError("CLOSED_AT_MISSING")
    tk = trade_key(book, tid, oa)
    f = _features(closed)
    st, var = _real_setup(book, closed)
    row = _envelope(KIND_OUTCOME, book=book, src_id=tk, rev=rev, symbol=_get(closed, "symbol"), side=side,
                    setup_type=st, variation=var, signal_key=signal_key, signal_key_src=signal_key_src, origin=origin,
                    env=env)
    o_dt, c_dt = _ts(oa), _ts(ca)
    hold_h = (c_dt - o_dt).total_seconds() / 3600.0 if (o_dt is not None and c_dt is not None) else None
    cov = f.get("funding_coverage")
    ef = f.get("exit_fill")
    bh = _get(closed, "bars_held")
    row.update({
        "trade_key": tk, "trade_id": tid, "opened_at": oa, "closed_at": ca, "final": bool(final),
        "exit_reason": _s(_get(closed, "exit_reason")),
        "exit_basis": _s(ef.get("basis")) if isinstance(ef, Mapping) else None,
        "path_unverified": bool(f.get("path_unverified")),
        **real_r(closed),
        "hold_hours": _r6(hold_h), "hold_bars_4h": _r6(hold_h / 4.0) if hold_h is not None else None,
        "bars_held_ledger": int(bh) if isinstance(bh, int) and not isinstance(bh, bool) else None,
        "net_pnl": _r6(_get(closed, "net_pnl", _get(closed, "pnl"))), "gross_pnl": _r6(_get(closed, "gross_pnl")),
        "entry_fee": _r6(_get(closed, "entry_fee")), "exit_fee": _r6(_get(closed, "exit_fee")),
        "funding": _r6(_get(closed, "funding")), "funding_paid": _r6(_get(closed, "funding_paid")),
        "funding_received": _r6(_get(closed, "funding_received")), "slippage_cost": _r6(_get(closed, "slippage_cost")),
        "funding_complete": cov.get("complete") if isinstance(cov, Mapping) and isinstance(cov.get("complete"),
                                                                                             bool) else None,
        **_cohort_fields(f, oa, env, ca),
        "fp": outcome_fp(closed)})
    return row


def cf_row(src: Any, *, book: str, rev: int, status: str, env: Mapping[str, Any], origin: str = LIVE,
           snapshot: Any = None, snapshot_status: Any = None, snapshot_meta: Any = None) -> dict[str, Any]:
    """`xp_cf` — `ShadowTrade` (nesne ya da `to_dict` sözlüğü; kaybolma revizyonunda imleçte saklanan asgari sözlük).
    Anlık görüntü YALNIZ rev 0'da. LABELLED → sonuç + R (`cf_r`); kaybolmuş durumlar sonuç taşımaz ve nihaidir."""
    if status not in CF_STATUSES:
        raise RowError("CF_STATUS_INVALID:%s" % status)
    _check_market(src)
    book_name, book_type = _book(book)
    src_book = _s(_get(src, "book"))
    if src_book is None:
        raise RowError("LEGACY_SHADOW")                   # öğrenmesiz eski gölge (book None) kayıt dışı
    if src_book != book_name:
        raise RowError("CF_BOOK_MISMATCH:%s!=%s" % (src_book, book_name))
    side = _side(_get(src, "direction"))
    cid = _s(_get(src, "id"))
    ck = cf_key(book, cid)
    f = _features(src)
    st = _s(f.get("family")) if book_type == PATTERN else _s(f.get("setup_type"))
    var = _s(_get(src, "variation"))
    sig = _s(_get(src, "signal_key")) or _s(_get(src, "plan_id"))
    row = _envelope(KIND_CF, book=book, src_id=ck, rev=rev, symbol=_get(src, "symbol"), side=side, setup_type=st,
                    variation=var, signal_key=sig, signal_key_src="CF_RECORD", origin=origin, env=env)
    reasons = [str(x) for x in (_get(src, "reason_not_opened") or [])][:_MAX_LIST]
    bb = f.get("baseline_blocked_by")
    created = _s(_get(src, "created_at"))
    entry, stop = _get(src, "entry"), _get(src, "stop")
    tfm = _get(src, "tf_minutes")
    tg = _get(src, "targets")
    row.update({
        "cf_key": ck, "cf_id": cid, "status": status, "final": status != PENDING,
        "created_at": created, "as_of_ms": _ms(created),
        "reason": reasons[0] if reasons else None, "reasons": reasons or None,
        "reason_family": reason_family(reasons[0]) if reasons else None,
        "entry_ref": _r6(entry), "stop": _r6(stop),
        "targets": [_f(t) for t in list(tg)[:_MAX_LIST]] if isinstance(tg, (list, tuple)) else None,
        "horizon_bars": _clean(_get(src, "horizon_bars")), "tf_minutes": _clean(tfm),
        "label_kind": _s(_get(src, "label_kind")),
        "approx": _get(src, "approx") if isinstance(_get(src, "approx"), bool) else None,
        "rule_version": _s(_get(src, "rule_version")), "label_ts": _s(_get(src, "label_ts")),
        "learning_unlocked": _get(src, "learning_unlocked") if isinstance(_get(src, "learning_unlocked"), bool) else None,
        "in_lab_universe": f.get("in_lab_universe") if isinstance(f.get("in_lab_universe"), bool) else None,
        # A15 (KARARLAR 7): tabanın da bloke ettiği POSITION_OPEN kayıtları istatistikte KALIR, bayrakla süzülebilir
        "baseline_blocked_by": _clean(bb) if bb else None, "baseline_blocked": bool(bb),
        "features": cf_features(f, book_type=book_type)})
    outcome = _get(src, "outcome")
    if rev == 0:
        row.update(_snapshot_fields(snapshot, snapshot_status, snapshot_meta))
    if status == LABELLED:
        if not isinstance(outcome, Mapping):
            raise RowError("LABELLED_WITHOUT_OUTCOME")
        row["outcome"] = _pick(outcome, _CF_OUTCOME_KEYS)
        row.update(cf_r(outcome, entry=entry, stop=stop, tf_minutes=tfm))
        row["labeled_at"] = _s(_get(src, "labeled_at"))
    row["fp"] = cf_fp(status, outcome if status == LABELLED else None)
    return row


__all__ = [
    "APP_MODE_PAPER", "BACKFILL_CF", "BACKFILL_HISTORY", "BACKFILL_OPEN", "BASELINE_SIZE_KEY", "BOOKS", "BREAKOUT",
    "CANDLE_PATTERN", "CF_LABEL_V1", "CF_LABEL_V1C", "CF_NET_MIN_VERSION", "CF_STATUSES", "COHORTS", "DROPPED",
    "EXPIRED", "FADE", "FAMILIES", "FINAL_AFTER_H", "GROSS_LEGACY", "KINDS", "KIND_CF", "KIND_ENTRY", "KIND_OUTCOME",
    "LABELLED", "LATE_FUNDING_WINDOW", "LAYER_VERSION", "LEARNING_EXTRA", "LIVE", "LOSS", "MAIN", "MARKET", "MOMENTUM",
    "NET", "NET_UNAVAILABLE", "ORIGINS", "PATTERN", "PENDING", "POLICY", "REAL_LABEL_VERSION", "ROW_SCHEMA", "R_BASES",
    "RowError", "SCRATCH", "SCRATCH_R", "SC_BEFORE", "SC_LEARNING_EXTRA", "SC_POLICY", "SNAPSHOT_STATUSES",
    "STALE_GRACE_BARS", "STRATEGY", "SUPERSEDED", "TREND", "UNKNOWN_LABEL_VERSION", "UNTAGGED", "VANISHED",
    "VANISHED_STATUSES", "WIN", "book_ctx", "book_for_name", "cf_features", "cf_fp", "cf_id_for", "cf_key",
    "cf_label_class", "cf_r", "cf_row", "classify_vanished", "cohort", "entry_row", "family_key", "family_of",
    "join_key", "latest_rows", "make_env", "next_rev", "outcome_fp", "outcome_is_final", "outcome_row",
    "pre_learning", "project_learning", "real_r", "reason_family", "row_id", "scorecard_class", "setup_key",
    "trade_key",
]

# -*- coding: utf-8 -*-
"""STRATEJİ KÂĞIT DEFTERİ — tek kurallı stratejinin uygulanması; canlı motor ile replay ORTAK yol (V10).

`apply_action` iki motorda da AYNI: boyut = profil işlem riski / stop mesafesi, `risk.evaluate`
(toplam risk tavanı, kaldıraç, cluster), `ledger.open` (gerçek filtre, kayma), `close_manual` (kayma).
Stop/hedef/funding/likidasyon/başa-baş: defterin kendi `tick`i.

`StrategyBook` canlı motorun AYRI kâğıt defteridir: kendi state dizini, kendi trade memory, kendi
özet dosyası. Ana botun defterine, öğrenicisine ve kararlarına DOKUNMAZ. Gerçek para YOK.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, SizeSpec,
                         SlippageModel, TaxPolicy, TickData, default_brackets, default_filters)
from .accounting.funding import FUNDING_SETTLEMENT_CONTRACT
from .accounting.futures_ledger import exit_decision
from .candle_confirmation import closed_bars
from .core import atomic_write_json, from_iso, iso, quantize_qty, utc_now
from .learn import TradeMemory
from .learning_mode import (BASELINE_SIZE_KEY, DEFAULT_MAX_TOTAL_OPEN_RISK_PCT, LEARNING_RECORD_ONLY,
                            RECORD_ONLY_FEATURE, RISK_NOTIONAL_ROUND_TOL, SIZE_BUMP, SIZE_SHRUNK, baseline_size_tag,
                            baseline_view, counterfactual_ok, fit_with_reserve, learning_tags, policy_reserve_usdt,
                            profile_for, record_only)
from .regime_gate import BTC_SYMBOL
from .risk import RiskEngine, build_state, enforces_position_cap
from . import paper_rules
from .ema200_trend import daily_rows_from_frame
from .timeframes import tf_ms

log = logging.getLogger(__name__)
SUMMARY_FILE = "strategy_paper.json"
INDEX_FILE = "strategy_paper_index.json"
SCHEMA_VERSION = "strategy_paper_v1"
#: Kâğıt defterlerin işlem piyasası: USDⓈ-M perpetual. Kural verisi de BU piyasadan doğrulanmalı (2026-09-16 onarımı).
PAPER_MARKET = "USDM_PERP"
#: `decide`/`read_daily` sözleşmesinden: kural GÜNLÜK kapanmış barları okur (T2: EMA200; M2: 28 gün önceki kapanış);
#: BTC rejimi yalnız YENİ girişte gerekir (`decide`: position_open iken BTC'ye bakılmaz). Başka dilim zorunlu değildir.
#: Trend/momentum defterlerinin dilimleri. V15: demet DEFTERE göre değişir (box 5m de okur) —
#: tek kaynak `paper_rules.rule_timeframes`; buradaki sabit yalnız geriye dönük varsayılandır.
RULE_TIMEFRAMES = paper_rules.TREND_TIMEFRAMES
#: ZAMAN SÖZLEŞMESİ (2026-09-16, canlı fiyat): kâğıt defter fiyatı = doğrulanmış USDⓈ-M perp mark (`funding.mark`) ve
#: onun KAYNAK zamanı (`funding.ts`: borsanın mark zaman damgası, ms; yoksa snapshot'ın alınma zamanı `ts`, epoch sn).
#: Alınma zamanı ve karar (kontrol) zamanı ayrıca yazılır; eski bir fiyata yeni zaman damgası basılmaz. Sağlayıcı
#: önbelleği 60 sn (`BinanceLive.ttl`) + izleyici periyodu 60 sn → sağlıklı yaş ≤ ~120 sn; üstü BAYAT: tick YOK, boşluk görünür.
PRICE_MAX_AGE_S = 180.0
#: Kaynak zamanı bundan daha ileri olamaz (saat kayması payı); ötesi geçersiz zaman → tick YOK.
PRICE_FUTURE_SKEW_S = 120.0
#: ZAMAN SÖZLEŞMESİ (günlük sinyal): kural `as_of` anında KAPANMIŞ son günlük barı okur (açılış + 1g <= as_of). Beklenen
#: son kapanış, `as_of`tan önceki UTC gün sınırıdır; sağlayıcı gecikme toleransı içinde (4 tur aralığı; canlı sağlayıcıya
#: karşı ÖLÇÜLMEDİ, kod sabiti) bir önceki bar da güncel sayılır. Daha eskisi BAYAT → ne OPEN ne kural CLOSE (ret gerekçeli).
#: V15 "5m": ÜRETİM TUR TEMPOSUDUR, bir kalite payı DEĞİL. Bot `watch --interval 15` ile 15 dakikada bir
#: tur atar; 5 dakikalık bir kural bu tempoda 5m barlarının ancak 1/3'ünü görür. Tolerans bunu 0 yapıp
#: defteri her turda "bayat" diye reddettirmek yerine AÇIKÇA kabul eder ve `data_policy`de ilan eder.
#: Kaçırılan tetiklerin maliyeti araştırmada AYRI bir kol olarak ölçülür (stride 1 vs 3), varsayılmaz.
#: "4h" (2026-09-25, 4h trend gözlem defteri): bir tur (15 dk). Sağlayıcı yeni 4h barı turdan biraz geç verirse defter
#: bir önceki barı okur; sinyal penceresi sinyal KAPANIŞINDAN ölçüldüğü için bu, geç girişe dönüşmez.
BAR_LAG_TOLERANCE_MS: dict[str, int] = {"1d": 3_600_000, "5m": 900_000, "4h": 900_000}
#: Üretimin tur aralığı (dk) — yalnız ilan/ölçüm için; zamanlamayı servis dosyası belirler.
PRODUCTION_TOUR_INTERVAL_MIN = 15
#: Ham çerçevenin son satırı `as_of`tan bu kadar ileride açılmışsa gelecek zaman damgası (saat sorunu) → ret.
BAR_FUTURE_SKEW_MS = 60_000
#: Bar uçlarının (1h) pozisyona uygulanma dilimi: yalnız pozisyon açılışından SONRA açılmış, kapanmış, bir kez.
BAR_TIMEFRAME = "1h"
#: BAR ÖLÇEK TOLERANSI (2026-09-17): barın canlı mark ile aynı fiyat ölçeğinde olup olmadığı YALNIZ barın
#: KAPANIŞIYLA sınanır — uçlarla DEĞİL. Fitil ölçmek istediğimiz şeydir; makullük ölçütü olamaz (ölçüldü:
#: ±%20 bandı `low`a uygulanınca stop'un 25 birim altına inen GEÇERLİ bir fitil stop kontrolünden düşüyordu).
BAR_SCALE_TOLERANCE = 0.20
#: BAR UCU MAKULLÜK SINIRI: uçlar barın KENDİ kapanışına göre sınırlıdır. Derin ama gerçek bir fitil buradan
#: geçer; 10.000 katlık bir birim/ölçek artefaktı geçmez. (Ölçek hükmü ayrıca kapanışı canlı mark'la sınar.)
BAR_EXTREME_MIN_RATIO = 0.2
BAR_EXTREME_MAX_RATIO = 5.0
#: Barın uygulanmama gerekçeleri. `BAR_OUT_OF_RANGE` ARTIK YOK: bütünlük ihlali, ölçek şüphesi ve piyasa kimliği
#: AYRI gerekçelerdir ve hiçbiri barı TÜKETMİŞ saymaz (boşluk kaydı kalır; veri düzelince aynı bar yeniden denenir).
BAR_CORRUPT = "BAR_CORRUPT"                  # sonlu/pozitif değil ya da low <= close <= high değil
BAR_SCALE_UNVERIFIED = "BAR_SCALE_UNVERIFIED"  # ölçek doğrulanamadı: açılış sürekliliği YOK ve kapanış canlı mark'tan uzak
BAR_MARKET_MISMATCH = "BAR_MARKET_MISMATCH"  # bar çerçevesi beklenen piyasadan değil (SPOT ikamesi vb.)
#: ÖĞRENME MODU (2026-09-28, öğrenme modu): defterin karşı-olgusal kaydı (`state_dir` altında; anahtar hiç açılmazsa YOK).
CF_FILE = "counterfactual_trades.json"
CF_ARCHIVE_DIR = "counterfactual_archive"
#: Likidasyon mesafesi yaklaşığında bakım marjı oranı (`fit_size` ve RiskEngine LIQ_BUFFER ile aynı varsayım).
LEARNING_MMR = 0.004


def parse_ts_ms(x: Any) -> int | None:
    """Zaman damgası → UTC ms (tek sözleşme). Kabul: ISO-8601 metni ('Z'/'+00:00'; naive = UTC), epoch saniye
    (sayı < 1e11, ondalıklı olabilir), epoch milisaniye (sayı >= 1e11), `datetime`. Çözülemezse None (uydurma yok)."""
    try:
        if x is None or isinstance(x, bool):
            return None
        if isinstance(x, datetime):
            return int((x if x.tzinfo else x.replace(tzinfo=timezone.utc)).timestamp() * 1000)
        if isinstance(x, (int, float)):
            if not math.isfinite(float(x)) or float(x) <= 0:
                return None
            return int(round(float(x) * 1000)) if float(x) < 1e11 else int(round(float(x)))
        s = str(x).strip()
        if not s:
            return None
        try:
            v = float(s)
            return parse_ts_ms(v)
        except ValueError:
            return int(from_iso(s).timestamp() * 1000)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def verified_price(snapshot: dict | None, *, now_ms: int, max_age_s: float = PRICE_MAX_AGE_S,
                   future_skew_s: float = PRICE_FUTURE_SKEW_S) -> dict[str, Any]:
    """Canlı snapshot'tan DOĞRULANMIŞ perp fiyatı (SAF). Döner: {ok, mark, price_ts_ms, fetched_at_ms, checked_at_ms,
    age_s, reason, detail}. Şart: sonlu ve pozitif `funding.mark`; kaynak zamanı çözülebilir, `now`dan ileride değil ve
    `max_age_s` içinde. Aksi hâlde ok=False ve gerekçe: NO_VERIFIED_FUTURES_PRICE | INVALID_FUTURES_PRICE_TIME |
    STALE_FUTURES_PRICE. Hüküm yalnız BU kontrol anı içindir; eski fiyat yeni zamanla etiketlenmez."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    out: dict[str, Any] = {"ok": False, "mark": 0.0, "price_ts_ms": None, "fetched_at_ms": None, "source_ts_ms": None,
                           "checked_at_ms": int(now_ms),
                           "age_s": None, "reason": "", "detail": "", "source": "live.snapshot.funding.mark"}
    fm = (snap.get("funding") or {}).get("mark") if isinstance(snap.get("funding"), dict) else None
    try:
        mark = float(fm) if fm is not None else 0.0
    except (TypeError, ValueError):
        mark = 0.0
    if not math.isfinite(mark) or mark <= 0:
        out.update(reason="NO_VERIFIED_FUTURES_PRICE", detail="; ".join(str(e) for e in (snap.get("errors") or []))[:200])
        return out
    fetched = parse_ts_ms(snap.get("ts"))
    src_ts = parse_ts_ms((snap.get("funding") or {}).get("ts")) if isinstance(snap.get("funding"), dict) else None
    price_ts = src_ts if src_ts is not None else fetched
    # `source_ts_ms` (2026-09-24): YALNIZ borsanın mark zamanı (yoksa None) — alınma zamanıyla karıştırılmaz
    out.update(mark=mark, price_ts_ms=price_ts, fetched_at_ms=fetched, source_ts_ms=src_ts)
    if price_ts is None:
        out.update(reason="INVALID_FUTURES_PRICE_TIME", detail="fiyat zamanı yok/çözülemedi")
        return out
    age_s = (int(now_ms) - int(price_ts)) / 1000.0
    out["age_s"] = round(age_s, 3)
    if age_s < -float(future_skew_s):
        out.update(reason="INVALID_FUTURES_PRICE_TIME", detail="fiyat zamanı gelecekte (%.0f sn)" % (-age_s))
        return out
    if age_s > float(max_age_s):
        out.update(reason="STALE_FUTURES_PRICE", detail="fiyat yaşı %.0f sn > %.0f sn" % (age_s, max_age_s))
        return out
    out["ok"] = True
    return out


def expected_last_closed_open(as_of_ms: int, tf: str) -> int:
    """`as_of` anında kapanmış olması gereken SON barın açılış ms'si: açılış + dilim <= as_of (eşitlik dahil)."""
    step = tf_ms(tf)
    return (int(as_of_ms) // step) * step - step


def frame_freshness(frame, tf: str, as_of_ms: int | None, *, tolerance_ms: int | None = None) -> tuple[str, int | None, dict[str, Any]]:
    """Çerçevenin `as_of` anında GERÇEKTEN kullanılabilir son kapanmış barı (SAF). Döner: (neden | "", kullanılan bar
    açılış ms, ayrıntı). Nedenler: AS_OF_MISSING, FRAME_MISSING_<TF> (hiç kapanmış bar yok), FRAME_FUTURE_<TF> (son satır
    as_of'tan ileride açılmış: saat sorunu), FRAME_STALE_<TF> (kullanılan bar beklenen son kapanıştan toleransın ötesinde eski).
    Kapanmamış son bar hata değildir: dışlanır ve kullanılan bar bir öncekidir."""
    tfu = str(tf).upper()
    if as_of_ms is None:
        return "AS_OF_MISSING", None, {}
    step = tf_ms(tf)
    tol = int(BAR_LAG_TOLERANCE_MS.get(str(tf), 0) if tolerance_ms is None else tolerance_ms)
    raw_last = _last_ts(frame)
    if raw_last is None:
        return "FRAME_MISSING_%s" % tfu, None, {}
    if raw_last > int(as_of_ms) + BAR_FUTURE_SKEW_MS:
        return "FRAME_FUTURE_%s" % tfu, None, {"last_open_ms": raw_last, "as_of_ms": int(as_of_ms)}
    used = _closed_last_ts(frame, int(as_of_ms), step)
    if used is None:
        return "FRAME_MISSING_%s" % tfu, None, {"last_open_ms": raw_last, "as_of_ms": int(as_of_ms), "note": "kapanmış bar yok"}
    expected = expected_last_closed_open(int(as_of_ms) - tol, tf)
    detail = {"used_open_ms": used, "used_close_ms": used + step, "expected_open_ms": expected, "as_of_ms": int(as_of_ms),
              "age_s": round((int(as_of_ms) - (used + step)) / 1000.0, 1), "tolerance_ms": tol, "unclosed_last": raw_last != used}
    if used < expected:
        return "FRAME_STALE_%s" % tfu, used, detail
    return "", used, detail


def _closed_last_ts(frame, as_of_ms: int, step: int) -> int | None:
    """Sondan geriye: `açılış + step <= as_of` olan ilk satırın açılış ms'si (kapanmamış/gelecek satırlar dışlanır)."""
    try:
        if frame is None or len(frame) == 0:
            return None
        col = frame["timestamp"] if "timestamp" in frame.columns else None
        n = len(frame)
        for i in range(n - 1, -1, -1):                       # sondan geriye ilk kapanmış satır (döngü orada biter)
            ts = int(col.iloc[i]) if col is not None else int(frame.index[i].value // 1_000_000)
            if ts + step <= as_of_ms:
                return ts
        return None
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return None


# ------------------------------------------------------------------ VERİ KAYNAĞI SÖZLEŞMESİ (2026-09-16)
@dataclass(frozen=True)
class DataVerdict:
    """Kural verisinin kimlik doğrulaması — motor (canlı) ve replay AYNI sözleşmeyi `apply_action`a taşır.

    `ok`: sembolün kural çerçevesi doğrulanmış USDⓈ-M perpetual verisi (piyasa + bu turun provenansı + yüklü barlarla
    bağ). `entry_ok`: ayrıca YENİ giriş için gerekenler: provenansın `entry_ok` bayrağı ve BTC referansının aynı
    doğrulaması. `reason`: ilk başarısızlık kodu (`DATA_*`); boşsa sorun yok. Eksik/geçersiz sözleşme = ret (fail-closed):
    `apply_action` bir `DataVerdict` olmadan ne OPEN ne CLOSE uygular.
    """
    ok: bool
    entry_ok: bool
    reason: str = ""
    market: str | None = None
    source: str | None = None
    tour_id: str | None = None
    bars: dict = field(default_factory=dict)      # {"1d": as_of anında KAPANMIŞ son bar açılış ms, ...} — kuralın gerçekten okuduğu bar
    btc: dict = field(default_factory=dict)       # {"ok", "market", "reason", "bars"}
    as_of_ms: int | None = None                   # değerlendirme (karar) anı — canlıda tur `now`, replay'de simülasyon anı
    detail: dict = field(default_factory=dict)    # güncellik ayrıntısı: kullanılan/beklenen bar, yaş, tolerans

    def to_dict(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "entry_ok": bool(self.entry_ok), "reason": self.reason, "market": self.market,
                "source": self.source, "tour_id": self.tour_id, "bars": dict(self.bars), "btc": dict(self.btc),
                "as_of_ms": self.as_of_ms, "detail": dict(self.detail)}


def _last_ts(frame) -> int | None:
    try:
        if frame is None or len(frame) == 0:
            return None
        if "timestamp" in frame.columns:
            return int(frame["timestamp"].iloc[-1])
        return int(frame.index[-1].value // 1_000_000)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return None


def _check_frames(frames: dict | None, prov: dict | None, run_id: str, want_market: str, tfs=RULE_TIMEFRAMES,
                  *, as_of_ms: int | None = None) -> tuple[str, dict, dict]:
    """Tek sembol için kimlik + güncellik kontrolü. Döner: (neden | "", kullanılan bar zamanları, ayrıntı).

    Kimlik: provenans defter adından ya da beklenen piyasadan UYDURULMAZ; sağlayıcının bu tur için yazdığı kayıt + o kayıtta
    bağlanan ham son bar zaman damgaları ile bellekteki çerçeve birebir eşleşmeli (eski turun onayı / bayat önbellek / kısmi
    indirme eşleşmez). Güncellik (2026-09-16): tur kimliği eşitliği güncellik KANITI DEĞİLDİR — kuralın `as_of` anında
    okuyacağı son KAPANMIŞ bar `frame_freshness` ile beklenen son kapanışa göre denetlenir; kayda o bar yazılır."""
    if not isinstance(prov, dict) or not prov.get("market"):
        return "PROVENANCE_MISSING", {}, {}
    if str(prov.get("tour_id") or "") != str(run_id or "") or not run_id:
        return "PROVENANCE_STALE", {}, {}
    if str(prov.get("market")) != want_market:
        return "MARKET_%s" % str(prov.get("market")).upper(), {}, {}
    bound = prov.get("frames") or {}
    used: dict[str, int] = {}
    detail: dict[str, Any] = {}
    for tf in tfs:
        fr = (frames or {}).get(tf)
        ts = _last_ts(fr)
        if ts is None:
            return "FRAME_MISSING_%s" % tf.upper(), {}, {}
        b = bound.get(tf) or {}
        if b.get("last_ts") is None or int(b.get("last_ts")) != ts:
            return "FRAME_MISMATCH_%s" % tf.upper(), {}, {}
        why, used_ts, d = frame_freshness(fr, tf, as_of_ms)
        detail[tf] = d
        if why:
            return why, ({tf: used_ts} if used_ts is not None else {}), detail
        used[tf] = int(used_ts)
    return "", used, detail


def verify_paper_data(*, symbol: str, frames: dict | None, provenance: dict | None, run_id: str,
                      btc_frames: dict | None = None, btc_provenance: dict | None = None, need_btc: bool = True,
                      want_market: str = PAPER_MARKET, as_of_ms: int | None = None,
                      tfs: tuple[str, ...] = RULE_TIMEFRAMES) -> DataVerdict:
    """Kural verisi doğrulaması (SAF). `need_btc`: yeni giriş yolu (rejim referansı gerekir); açık pozisyonun kural
    kapanışı yalnız sembol verisine bağlıdır (`decide` position_open iken BTC okumaz) → BTC eksikliği kapanışı engellemez.
    `as_of_ms` (2026-09-16): değerlendirme anı — canlıda turun karar saati, replay'de simülasyonun karar anı (duvar saati
    DEĞİL). Verilmezse güncellik denetlenemez → `DATA_AS_OF_MISSING` (fail-closed). `bars`: kuralın bu anda okuduğu son
    kapanmış bar; kapanmamış/gelecek son satır sinyalin ya da kaydın parçası olmaz."""
    why, used, detail = _check_frames(frames, provenance, run_id, want_market, tfs=tfs, as_of_ms=as_of_ms)
    if why:
        return DataVerdict(ok=False, entry_ok=False, reason="DATA_" + why, market=(provenance or {}).get("market") if isinstance(provenance, dict) else None,
                           source=(provenance or {}).get("source") if isinstance(provenance, dict) else None,
                           tour_id=(provenance or {}).get("tour_id") if isinstance(provenance, dict) else None,
                           bars=used, as_of_ms=as_of_ms, detail=detail)
    prov = provenance or {}
    entry_ok, reason = True, ""
    if not bool(prov.get("entry_ok", False)):
        entry_ok, reason = False, "DATA_ENTRY_BLOCKED:%s" % (prov.get("reason") or "PROVIDER")
    btc: dict[str, Any] = {"required": bool(need_btc)}
    if need_btc:
        bwhy, bused, bdetail = _check_frames(btc_frames, btc_provenance, run_id, want_market,
                                             tfs=paper_rules.TREND_TIMEFRAMES, as_of_ms=as_of_ms)
        btc.update({"ok": not bwhy, "market": (btc_provenance or {}).get("market") if isinstance(btc_provenance, dict) else None,
                    "reason": ("DATA_BTC_" + bwhy) if bwhy else "", "bars": bused, "detail": bdetail})
        if bwhy and entry_ok:
            entry_ok, reason = False, "DATA_BTC_" + bwhy
    return DataVerdict(ok=True, entry_ok=entry_ok, reason=reason, market=str(prov.get("market")), source=prov.get("source"),
                       tour_id=str(prov.get("tour_id")), bars=used, btc=btc, as_of_ms=as_of_ms, detail=detail)


class BookSpec:
    """Bir defterin ayarları — ana bölüm ya da `extra` listesindeki sözlük, TEK biçime indirgenir."""

    def __init__(self, *, name: str, starting_equity_usdt: float = 100.0, atr_mult: float = 3.0,
                 breakeven_at_mfe_r: float = 0.0, state_dir: str = "strategy_paper", symbols=None, enabled: bool = True,
                 max_entry_drift_pct: float = 0.0,
                 rule_params: dict | None = None):
        self.name, self.starting_equity_usdt, self.atr_mult = str(name), float(starting_equity_usdt), float(atr_mult)
        self.breakeven_at_mfe_r, self.state_dir = float(breakeven_at_mfe_r), str(state_dir)
        self.symbols, self.enabled = list(symbols or []), bool(enabled)
        #: Kuralin hesapladigi fiyattan bu %'den fazla kaymis bir gerceklesmede giris YAPILMAZ.
        #: 0 = kapali (eski davranis). Bkz. apply_action icindeki GIRIS KAYMASI KAPISI.
        self.max_entry_drift_pct = float(max_entry_drift_pct or 0.0)
        #: Kurala özel ayarlar (box: near_frac/exit_kind/...). Trend defterleri için boştur.
        self.rule_params: dict = dict(rule_params or {})

    @classmethod
    def from_section(cls, sp) -> "BookSpec":
        return cls(name=sp.name, starting_equity_usdt=sp.starting_equity_usdt, atr_mult=sp.atr_mult,
                   breakeven_at_mfe_r=sp.breakeven_at_mfe_r, state_dir=sp.state_dir, symbols=sp.symbols, enabled=sp.enabled,
                   max_entry_drift_pct=getattr(sp, "max_entry_drift_pct", 0.0),
                   rule_params=dict(getattr(sp, "rule_params", None) or {}))

    @classmethod
    def from_dict(cls, d: dict) -> "BookSpec":
        allowed = {"name", "starting_equity_usdt", "atr_mult", "breakeven_at_mfe_r", "state_dir", "symbols", "enabled",
                   "rule_params", "max_entry_drift_pct"}
        return cls(**{k: v for k, v in dict(d).items() if k in allowed})

    @property
    def summary_file(self) -> str:
        return SUMMARY_FILE if self.state_dir == "strategy_paper" else "%s.json" % self.state_dir


def book_specs(v3) -> list["BookSpec"]:
    """Config'ten etkin defter listesi: ana bölüm (enabled ise) + extra (her biri enabled ise)."""
    sp = v3.strategy_paper
    out: list[BookSpec] = []
    if bool(getattr(sp, "enabled", False)):
        out.append(BookSpec.from_section(sp))
    for ex in list(getattr(sp, "extra", None) or []):
        b = BookSpec.from_dict(ex)
        if b.enabled:
            out.append(b)
    return out


def _baseline_size(act: dict[str, Any], *, entry: float, stop: float, profile, state, risk: RiskEngine
                   ) -> tuple[float, int, dict[str, Any] | None]:
    """Anahtar KAPALIYKEN boyut (TEK kaynak): profil işlem riski / stop mesafesi, ihtiyaç kadar kaldıraç, isteyen
    eylemde tavana küçültme. Döner: (notional, kaldıraç, küçültme kaydı | None). SAF — `act` değişmez.
    Öğrenme modu (2026-09-28) bunu yalnız "taban bu işlemi durdurur muydu?" etiketi için yeniden koşar."""
    stop_frac = abs(entry - stop) / entry
    risk_usdt = float(profile.risk_per_trade_pct) / 100.0 * float(state.equity)
    notional = risk_usdt / stop_frac
    lev = int(act.get("leverage") or 1)
    # IHTIYAC KADAR KALDIRAC (2026-09-21) — tavana kadar. Tek pozisyon tavani
    # `equity x max_position_pct/100 x kaldirac`. Stopu DAR olan islem ayni hareketten daha
    # cok R uretir ve tam da tavana takilan odur; bu yuzden kaldirac SABIT degil, islemin
    # ihtiyaci kadar yukselir. Kaldirac islem basina RISKI ARTIRMAZ (risk stop mesafesiyle
    # belirlenir); yalnizca tavani acar ve likidasyonu yaklastirir — bu yuzden GEREKMEDIKCE
    # yukseltilmez. `leverage_max <= lev` iken davranis degismez.
    _lmax = int(act.get("leverage_max") or 0)
    if _lmax > lev:
        _cap1 = float(state.equity) * float(getattr(profile, "max_position_pct", 0.0)) / 100.0
        if _cap1 > 0:
            _need = int(math.ceil(notional / _cap1))
            lev = max(lev, min(_need, _lmax))
    # TAVANA KÜÇÜLTME (2026-09-25, yalnız isteyen eylem — formasyon v3): tek pozisyon tavanını aşan işlem REDDEDİLMEZ,
    # tavana sığacak kadar küçültülür → işlem başına risk bütçenin ALTINA iner (asla üstüne çıkmaz), kaldıraç değişmez.
    scaled = None
    if act.get("cap_notional_to_position_pct"):
        _cap2 = float(risk.equity_basis(state)) * float(getattr(profile, "max_position_pct", 0.0)) / 100.0 * max(lev, 1)
        if _cap2 > 0 and notional > _cap2:
            scaled = {"from": round(notional, 6), "to": round(_cap2 * 0.999, 6),
                      "risk_fraction_of_budget": round(_cap2 * 0.999 / notional, 6)}
            notional = _cap2 * 0.999
    return notional, lev, scaled


def _signal_key(act: dict[str, Any] | None) -> str | None:
    """Karşı-olgusal tekillik anahtarı = kuralın BAR BAŞINA sinyal kimliği (P1; 2026-09-28, öğrenme modu): önce
    `signal_ts` (sinyal barının açılışı), yoksa `signal_close_ms`. Tur kimliğinden ASLA türetilmez; yoksa None (kayıt yok)."""
    for k in ("signal_ts", "signal_close_ms"):
        v = (act or {}).get(k)
        if v is None or isinstance(v, bool):
            continue
        try:
            return "%s:%d" % (k, int(v))
        except (TypeError, ValueError):
            continue
    return None


def _box_eod_bars(act: dict[str, Any], now: datetime) -> int:
    """Box karşı-olgusalının ufku (5m bar): girişten SONRAKİ ilk bardan sinyal gününün son (23:55) barına kadar — kuralın
    gün sonu düzleşmesi. Gün bittiyse 0 (kayıt yok)."""
    try:
        sig = int(act.get("signal_ts"))
    except (TypeError, ValueError):
        return 0
    day, m5 = 86_400_000, 300_000
    eod = sig - sig % day + day
    first = (int(now.timestamp() * 1000) // m5 + 1) * m5
    return max(0, (eod - first) // m5)


def _entry_features(act: dict[str, Any], data: DataVerdict) -> tuple[dict[str, Any], dict[str, Any]]:
    """Girişin kanıt kaydı: (features, data_source). Anahtar sırası sabit (defter JSON'u bit-aynı kalır)."""
    # Kanıt: hangi veriyle girildiği pozisyon/işlem kaydına yazılır (piyasa, kaynak, tur, kullanılan bar zamanları).
    data_src = {"market": data.market, "source": data.source, "tour_id": data.tour_id, "bars": dict(data.bars), "btc": dict(data.btc)}
    feats = {"regime": act.get("regime"), "market_type": "USDM_PERP", "strategy": act.get("name"),
             "expected_r": float(act.get("expected_r") or 0.0), "p_win": None, "data_source": data_src,
             # ORTAK YAPI (structures_v1): girişin dayanağı ve politika sürümü — eski ölçümlerden AYRI
             "structure": dict(act["structure"]) if act.get("structure") else None}
    if act.get("variation"):
        # MUM VARYASYONU (2026-09-26, yalnız isteyen eylem): kimlik, definition_sha, laboratuvar kanıtı, gözlem bayrağı ve
        # çıkış (hedef R, en uzun tutma) — girişteki ANLIK GÖRÜNTÜ; işlem kaydına da geçer. Anahtar yalnız o zaman eklenir.
        feats["candle_variation"] = dict(act["variation"])
    return feats, data_src


def _ledger_preview(ledger: FuturesLedgerV2, symbol: str, direction: str, entry: float, notional: float, lev: int,
                    filters, tick: TickData | None, *, available: Decimal | None = None,
                    n_positions: int | None = None) -> str | None:
    """Defterin `open` kapılarının YAN ETKİSİZ ön-izlemesi (taban davranışı, kalıcı `allow_shrink` ile): ilk ret kodu ya
    da None. Yalnız öğrenme etiketi (`learning_unlocked_by`) içindir (2026-09-28, öğrenme modu). `available` /
    `n_positions`: taban görünümünün serbest marjı ve pozisyon adedi (None → defterin kendisi)."""
    if n_positions is None:
        ok, why = ledger.can_open(symbol)
        if not ok:
            return why
    elif symbol in ledger.positions:
        return "ALREADY_OPEN"
    elif ledger.enforce_position_cap and int(n_positions) >= int(ledger.max_positions):
        return "MAX_POSITIONS"
    f = filters if filters is not None else default_filters(symbol, MarketType.USDM_PERP)
    lev = max(1, int(lev))
    if lev > int(f.max_leverage):
        return "LEVERAGE_TOO_HIGH"
    fill = ledger.market_fill_price(symbol, direction, entry, filters=f, tick=tick)
    if fill <= 0:
        return "BAD_PRICE"
    qty = quantize_qty(Decimal(str(notional)) / fill, f.qty_step)
    cost_rate = Decimal(1) / Decimal(lev) + ledger.fees.rate(False)
    avail = ledger.available if available is None else available
    if qty > 0 and qty * fill * cost_rate > avail and not ledger.allow_shrink:
        return "INSUFFICIENT_MARGIN"
    if qty <= 0:
        return "STEP_ZERO_QTY"
    if qty < f.min_qty:
        return "MIN_QTY"
    if qty > f.market_max_qty:
        return "MAX_QTY"
    if qty * fill < f.min_notional:
        return "MIN_NOTIONAL"
    return None


def _baseline_blocks(act: dict[str, Any], *, symbol: str, direction: str, entry: float, stop: float,
                     tick: TickData | None, now: datetime, ledger: FuturesLedgerV2, risk: RiskEngine, profile, state,
                     filters) -> tuple[list[str], dict[str, Any] | None]:
    """Anahtar KAPALI olsaydı bu işlemi hangi kapı durdururdu? (2026-09-28, öğrenme modu) — ucuz yeniden koşum: taban
    boyut (`_baseline_size`), taban profilli RiskEngine (AYNI kill switch), defter ön-izlemesi. Karar ve durum DEĞİŞMEZ;
    sonuç yalnız etikettir (boş = taban da açardı). Kapılar öğrenme defterinin DEĞİL taban görünümünün (`baseline_view`:
    öğrenme-ekstra pozisyonlar yok, politika pozisyonları taban boyutunda) marjı/riskiyle ölçülür — öğrenme defterinin
    küçük pozisyonları tabanın marj darlığını gizlemesin (ikinci doğrulama turu). YAKLAŞIK. Döner: (kodlar, taban boyutu
    etiketi | None)."""
    try:
        view, dm = baseline_view(state, learning_tags(ledger.positions))
        base = RiskEngine(profile, getattr(risk, "ks", None), getattr(risk, "clusters", None))
        notional, lev, _scaled = _baseline_size(act, entry=entry, stop=stop, profile=profile, state=view, risk=base)
        plan = {"symbol": symbol, "market_type": "USDM_PERP", "direction": direction, "entry": entry, "stop": stop,
                "targets": list(act.get("targets") or []), "notional": notional, "margin": notional / max(1, lev),
                "leverage": lev, "amount_type": "NOTIONAL", "expected_r": float(act.get("expected_r") or 0.0),
                "min_notional": 5.0}
        rd = base.evaluate(plan, view, {"now_utc": now})
        if not rd.allowed:
            return [str(r) for r in (rd.reasons or ["RISK_DENIED"])], None
        n2, lev2 = float(rd.adjusted_notional or notional), int(rd.adjusted_leverage or lev)
        why = _ledger_preview(ledger, symbol, direction, entry, n2, lev2, filters, tick,
                              available=(ledger.available - Decimal(str(dm))) if view is not state else None,
                              n_positions=len(view.open_positions) if view is not state else None)
        if why:
            return [why], None
        # taban boyutu, taban defterin GERÇEKTEN açacağı gibi: dolum fiyatında adıma AŞAĞI yuvarlanmış miktar × dolum
        f = filters if filters is not None else default_filters(symbol, MarketType.USDM_PERP)
        fill = ledger.market_fill_price(symbol, direction, entry, filters=f, tick=tick)
        qty = quantize_qty(Decimal(str(n2)) / fill, f.qty_step) if fill > 0 else Decimal(0)
        return [], baseline_size_tag(float(qty * fill), lev2)
    except Exception as exc:  # noqa: BLE001 — etiket hesaplanamazsa işlem ETKİLENMEZ
        log.warning("öğrenme etiketi (taban kapıları) hesaplanamadı (%s): %s", symbol, exc)
        return ["BASELINE_UNKNOWN"], None


def _open_learning(act: dict[str, Any], *, symbol: str, direction: str, entry: float, stop: float,
                   tick: TickData | None, now: datetime, ledger: FuturesLedgerV2, risk: RiskEngine, profile, state,
                   filters, run_id: str, reject: Callable[[str, str], None],
                   on_opened: Callable[[Any, dict[str, Any]], None] | None, data: DataVerdict, learning: Any) -> str:
    """ÖĞRENME MODU AÇILIŞI (2026-09-28, öğrenme modu) — `apply_action`ın OPEN kolu, yalnız `learning.on` iken.

    Veri, stop, giriş kayması ve test edilmiş risk aralığı kapıları bu çağrıdan ÖNCE aynen uygulanmıştır. Boyut
    `fit_size` (slot başına eşit marj, `risk_pct` hedef risk, likidasyon mesafesi ≥ k × stop, min-notional'a çıkarma
    yalnız %2 tavan ve serbest marj içinde; tek pozisyon tavanı önceden uygulanır → MAX_POSITION_PCT reddi olmaz).
    Taban `equity_basis(state)`. Risk kapısı ÇAĞIRANIN verdiği öğrenme RiskEngine'i (`risk`; toplam açık risk 100, işlem
    tavanı 2 aynen). Defter `allow_shrink=True` YALNIZ bu çağrı için (kalıcı öznitelik değişmez). Etiketler
    `pos.meta["learning"]` ve `features["learning"]`e yazılır: size_rule, slots, risk_pct, risk_fraction_of_budget,
    learning_unlocked_by. Retten sonra `act["_learning"]["fit"]` çağıranın karşı-olgusal kaydına kalır."""
    filt = filters if filters is not None else default_filters(symbol, MarketType.USDM_PERP)
    rprof = getattr(risk, "profile", None) or profile
    lmax = int(learning.leverage_max)
    for cap in (getattr(rprof, "futures_max_leverage", None), getattr(filt, "max_leverage", None)):
        if cap:
            lmax = min(lmax, int(cap))
    E = float(risk.equity_basis(state))
    fill_px = float(ledger.market_fill_price(symbol, direction, entry, filters=filt, tick=tick))
    # stop mesafesi defterin GERÇEKLEŞME fiyatından (kayma + tick yukarı yuvarlama dahil) — ana bot (`exec_entry`) ve
    # Formasyon (`qmark`) ile aynı; mark'tan ölçmek dar stoplarda riski eksik gösteriyordu (2026-09-28, öğrenme modu)
    sz_px = fill_px if fill_px > 0 else float(entry)
    tags = dict(act.get("_learning") or {}) if isinstance(act.get("_learning"), dict) else {}
    # taban kapıları boyuttan ÖNCE (ikinci doğrulama turu): politika işlemi (etiket boş) politika rezervini kullanır,
    # öğrenme-ekstra giriş serbest marjı rezerv kadar EKSİK görür → keşif defteri doldurup politikayı dışarıda bırakamaz
    unlocked = [str(x) for x in (tags.get("unlocked_by") or [])]
    bcodes, bsize = _baseline_blocks(act, symbol=symbol, direction=direction, entry=entry, stop=stop, tick=tick,
                                     now=now, ledger=ledger, risk=risk, profile=profile, state=state, filters=filt)
    unlocked += [x for x in bcodes if x not in unlocked]
    p_res = 0.0
    if unlocked:
        p_res = policy_reserve_usdt(equity=E, slots=int(learning.slots), reserve_pct=float(learning.reserve_pct))
    # rezerv yüzünden sığmayan ekstra aday INSUFFICIENT_MARGIN (why=POLICY_RESERVE) — üçüncü doğrulama turu
    fit = fit_with_reserve(policy_reserve=p_res, available_margin=float(ledger.available),
                           equity=E, entry=sz_px, stop=stop, slots=int(learning.slots), leverage_max=max(1, lmax),
                           risk_pct=float(learning.risk_pct), reserve_pct=float(learning.reserve_pct),
                           liq_buffer_mult=float(learning.liq_buffer_mult), mmr=LEARNING_MMR,
                           min_notional=float(filt.min_notional), qty_step=float(filt.qty_step), price_for_step=sz_px,
                           hard_cap_pct=float(learning.hard_cap_pct), min_notional_bump=bool(learning.min_notional_bump),
                           min_qty=float(filt.min_qty), max_position_pct=getattr(rprof, "max_position_pct", None))
    tags["fit"] = {"ok": fit.ok, "reason": fit.reason, "size_rule": fit.size_rule, "notional": round(fit.notional, 6),
                   "leverage": int(fit.leverage), "margin": round(fit.margin, 6), "risk_usdt": round(fit.risk_usdt, 6),
                   "why": fit.detail.get("why"), "equity_basis": E,
                   "policy_grade": not unlocked, "policy_reserve_usdt": round(p_res, 6)}
    act["_learning"] = tags
    if not fit.ok:
        reject(symbol, fit.reason or "LEARNING_SIZE")
        return "REJECTED"
    rfb = fit.detail.get("risk_fraction_of_budget")
    lrn = {"book": str(getattr(learning, "name", "")), "size_rule": fit.size_rule, "slots": int(learning.slots),
           "risk_pct": float(learning.risk_pct), "leverage": int(fit.leverage), "leverage_max": int(lmax),
           "risk_usdt": round(fit.risk_usdt, 6), "equity_basis": E,
           "risk_fraction_of_budget": round(float(rfb), 6) if rfb is not None else None,
           "learning_unlocked_by": unlocked}
    if not unlocked and bsize is not None:
        lrn[BASELINE_SIZE_KEY] = bsize              # taban görünümü: bu politika pozisyonunu taban boyutunda sayar
    plan_dict = {"symbol": symbol, "market_type": "USDM_PERP", "direction": direction, "entry": sz_px, "stop": stop,
                 "targets": list(act.get("targets") or []), "notional": fit.notional, "margin": fit.margin,
                 "leverage": int(fit.leverage), "amount_type": "NOTIONAL", "expected_r": float(act.get("expected_r") or 0.0),
                 "min_notional": float(filt.min_notional)}
    rd = risk.evaluate(plan_dict, state, {"now_utc": now})
    if not rd.allowed:
        reject(symbol, (rd.reasons or ["RISK_DENIED"])[0])
        return "REJECTED"
    notional = float(rd.adjusted_notional or fit.notional)
    lev = int(rd.adjusted_leverage or fit.leverage)
    if notional <= 0:
        reject(symbol, "ZERO_NOTIONAL")
        return "REJECTED"
    # SEÇİCİLİK-EKSTRA YALNIZ KAYIT (2026-10-03, sahip kararı): açılış kararı burada KESİN (boyut, politika rezervi, risk
    # kapıları bugünkü gibi uygulandı). Kip `record_selectivity` ve en az bir seçicilik kodu → AÇILMAZ; çağıranın
    # karşı-olgusal kaydı (`StrategyBook._learning_after`) ayrılan kodları ve öğrenme boyutunu taşır. `open` → None, bit-aynı.
    div = record_only(unlocked, learning)
    if div is not None:
        tags["record_only"] = dict(div, book=str(getattr(learning, "name", "")), size_rule=fit.size_rule,
                                   notional=round(notional, 6), leverage=int(lev), risk_usdt=round(fit.risk_usdt, 6),
                                   slots=int(learning.slots), risk_pct=float(learning.risk_pct), equity_basis=E)
        reject(symbol, LEARNING_RECORD_ONLY)
        return "REJECTED"
    feats, data_src = _entry_features(act, data)
    feats["learning"] = dict(lrn)
    if "in_lab_universe" in tags:
        feats["in_lab_universe"] = bool(tags["in_lab_universe"])
    qty_s = fit.detail.get("qty")
    # RiskEngine `adjusted_notional`ı 4 haneye YUVARLAR (≤ 5e-5 USDT); bu fark gerçek bir aşağı ayar sayılmaz (ana bot
    # `notional + 1e-4 >= final_notional` ile aynı tolerans). Aksi halde çıkarma NOTIONAL'a düşüp bir adım kaybederdi.
    if fit.size_rule == SIZE_BUMP and qty_s and notional + RISK_NOTIONAL_ROUND_TOL >= fit.notional:
        # çıkarılan miktar adıma YUKARI yuvarlandı: defterin aşağı yuvarlaması bir adım kaybettirmesin (miktarla aç)
        size = SizeSpec(Decimal(str(qty_s)), AmountType.QUANTITY, lev)
    else:
        size = SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, lev)
    pos = ledger.open(symbol, direction, entry, size, filters=filt, stop=stop, targets=list(act.get("targets") or []),
                      setup_type=str(act.get("setup_type") or "strategy"), trigger_text=str(act.get("reason") or ""),
                      features=feats, tick=tick, now=now,
                      meta={"run_id": run_id, "strategy": str(act.get("name") or ""), "data_source": data_src,
                            "learning": dict(lrn)},
                      allow_shrink=True)
    if pos is None:
        reject(symbol, ledger.last_reject_reason or "LEDGER_REJECT")
        return "REJECTED"
    upd: dict[str, Any] = {}
    if isinstance(pos.meta.get("shrunk_to_margin"), dict):
        # yedek emniyet devreye girdi: defter serbest marja küçülttü (R geçerli; risk bütçenin altında)
        upd["size_rule"] = SIZE_SHRUNK
    try:
        # etiketler GERÇEKLEŞEN pozisyondan: defterin miktarı (adıma aşağı) × |dolum − stop| (Formasyon ile aynı)
        r_act = float(pos.qty) * abs(float(pos.entry_avg) - float(stop))
        budget = float(fit.detail.get("risk_budget_usdt") or 0.0)
        upd["risk_usdt"] = round(r_act, 6)
        upd["risk_fraction_of_budget"] = round(r_act / budget, 6) if budget > 0 else None
    except (TypeError, ValueError, ArithmeticError):
        pass
    if upd:
        pos.meta["learning"].update(upd)
        pos.features["learning"].update(upd)
    if on_opened is not None:
        on_opened(pos, act)
    return "OPENED"


def apply_action(act: dict[str, Any] | None, *, symbol: str, price: float, tick: TickData | None, now: datetime,
                 ledger: FuturesLedgerV2, risk: RiskEngine, profile, state, filters, run_id: str,
                 reject: Callable[[str, str], None], on_closed: Callable[[Any], None],
                 on_opened: Callable[[Any, dict[str, Any]], None] | None = None,
                 data: DataVerdict | None = None, max_entry_drift_pct: float = 0.0, learning: Any = None) -> str:
    """Stratejinin kararını uygular. Döner: OPENED | CLOSED | REJECTED | NONE. İki motor da bunu çağırır.

    `data` (2026-09-16): kural verisinin kimlik hükmü. Sözleşme fail-closed'dur — hüküm yoksa ya da `ok` değilse ne
    OPEN ne CLOSE uygulanır (SPOT ikamesi / kaynağı bilinmeyen veriyle futures işlemi yok); OPEN ayrıca `entry_ok`
    (provenansın giriş izni + BTC referansı) ister. Açık pozisyonun stop/hedef/likidasyonu bu fonksiyondan değil,
    defterin `tick`inden yürür ve bu hükümden ETKİLENMEZ.

    `learning` (2026-09-28, öğrenme modu): `BookLearning` ya da None. None ya da `on` değilse davranış bit-bit eskisi
    gibidir. Açıkken YALNIZ boyut/risk/defter adımı değişir (`_open_learning`); veri, stop, kayma ve test edilmiş aralık
    kapıları aynen kalır ve `risk` çağıranın öğrenme RiskEngine'idir. Replay her zaman None geçer."""
    if not act:
        return "NONE"
    action = str(act.get("action") or "").upper()
    pos = ledger.positions.get(symbol)
    if action == "CLOSE":
        if pos is None:
            return "NONE"
        if data is None or not data.ok:
            reject(symbol, (data.reason if (data is not None and data.reason) else "DATA_VERDICT_MISSING"))
            return "REJECTED"
        if act.get("structure"):
            # YAPI ÇIKIŞI (structures_v1): kapanışa dayanak yapı kaydın kendisine yazılır (panel "neden çıktı?").
            pos.features["exit_structure"] = dict(act["structure"])
        rec = ledger.close_manual(symbol, price, reason=str(act.get("reason") or "STRATEGY_EXIT"), now=now, tick=tick)
        if rec is None:
            return "NONE"
        on_closed(rec)
        return "CLOSED"
    if action != "OPEN" or pos is not None:
        return "NONE"
    if data is None or not data.ok or not data.entry_ok:
        reject(symbol, (data.reason if (data is not None and data.reason) else "DATA_VERDICT_MISSING"))
        return "REJECTED"
    direction = str(act.get("direction") or "LONG").upper()
    entry = float(price)
    stop = float(act.get("stop") or 0.0)
    if entry <= 0 or stop <= 0 or (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        reject(symbol, "STRATEGY_BAD_STOP")
        return "REJECTED"
    # GIRIS KAYMASI KAPISI (2026-09-21) — kuralin hesapladigi fiyat ile GERCEKLESME fiyati
    # arasindaki fark. Kural girisi/stopu/hedefi KAPANMIS bardan turetir; defter ise turun
    # CANLI markiyle acar ve arada 15 dakikaya kadar gecikme mesrudur. Kapi olmadan sonuc
    # YONLU bir secim yanliligiydi: mark stopa yaklastiysa notional sisip MAX_POSITION_PCT
    # ile reddediliyor, uzaklastiysa aciliyordu — yani deftere yalnizca "hareket zaten olmus"
    # girisler suzuluyordu. Bir fade kuralinda (box) bu tam olarak en kotu alt kume.
    # `max_entry_drift_pct <= 0` iken kapi KAPALI ve davranis bit-bit eskisi gibidir.
    _sig = act.get("signal_close")
    if max_entry_drift_pct > 0 and _sig is not None:
        try:
            _sigf = float(_sig)
        except (TypeError, ValueError):
            _sigf = 0.0
        if _sigf > 0:
            _drift = abs(entry - _sigf) / _sigf * 100.0
            if _drift > float(max_entry_drift_pct):
                reject(symbol, "ENTRY_DRIFT")
                return "REJECTED"
    # TEST EDİLMİŞ RİSK ARALIĞI (2026-09-25, yalnız isteyen eylem — 4h trend gözlem defteri): girişten stop'a mesafe
    # laboratuvarın kabul ettiği ATR aralığında değilse (0,1 < mesafe/ATR <= 10) girilmez. Kural stop'u KAPANIŞTAN kurar;
    # defter canlı fiyattan girer, arada fiyat stop'a çok yaklaştıysa işlem laboratuvarda sayılmayan bir işlemdir.
    _rb, _atr = act.get("risk_atr_bounds"), act.get("atr14")
    if _rb and _atr:
        try:
            _lo, _hi, _a = float(_rb[0]), float(_rb[1]), float(_atr)
        except (TypeError, ValueError, IndexError):
            _lo = _hi = _a = 0.0
        if not (_a > 0 and _lo * _a < abs(entry - stop) <= _hi * _a):
            reject(symbol, "RISK_OUTSIDE_TESTED_RANGE")
            return "REJECTED"
    # HEDEF GİRİŞTEN (2026-09-26, yalnız isteyen eylem — mum varyasyonları): hedef = gerçek giriş ± R × risk. Laboratuvar
    # hedefi sonraki barın AÇILIŞINDAN ölçer (`simulate`: entry + s·default_rr·risk); kural kapanıştan ölçseydi canlı giriş
    # kaydıkça R değişirdi. Anahtar yoksa `targets` eylemdeki gibi kalır (diğer defterler bit-bit aynı).
    _tr = act.get("target_r_from_entry")
    if _tr:
        _s = 1.0 if direction == "LONG" else -1.0
        act["targets"] = [entry + _s * float(_tr) * abs(entry - stop)]
    if learning is not None and getattr(learning, "on", False):
        # ÖĞRENME MODU (2026-09-28, öğrenme modu): slot boyutu + öğrenme RiskEngine'i + çağrı başına allow_shrink.
        return _open_learning(act, symbol=symbol, direction=direction, entry=entry, stop=stop, tick=tick, now=now,
                              ledger=ledger, risk=risk, profile=profile, state=state, filters=filters, run_id=run_id,
                              reject=reject, on_opened=on_opened, data=data, learning=learning)
    notional, lev, _scaled = _baseline_size(act, entry=entry, stop=stop, profile=profile, state=state, risk=risk)
    if _scaled is not None:
        act["_notional_scaled"] = _scaled
    plan_dict = {"symbol": symbol, "market_type": "USDM_PERP", "direction": direction, "entry": entry, "stop": stop,
                 "targets": list(act.get("targets") or []), "notional": notional, "margin": notional / max(1, lev),
                 "leverage": lev, "amount_type": "NOTIONAL", "expected_r": float(act.get("expected_r") or 0.0),
                 "min_notional": 5.0}
    rd = risk.evaluate(plan_dict, state, {"now_utc": now})
    if not rd.allowed:
        reject(symbol, (rd.reasons or ["RISK_DENIED"])[0])
        return "REJECTED"
    notional = float(rd.adjusted_notional or notional)
    if notional <= 0:
        reject(symbol, "ZERO_NOTIONAL")
        return "REJECTED"
    feats, data_src = _entry_features(act, data)
    pos = ledger.open(symbol, direction, entry, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or lev)),
                      filters=filters, stop=stop, targets=list(act.get("targets") or []),
                      setup_type=str(act.get("setup_type") or "strategy"), trigger_text=str(act.get("reason") or ""),
                      features=feats,
                      tick=tick, now=now, meta={"run_id": run_id, "strategy": str(act.get("name") or ""), "data_source": data_src})
    if pos is None:
        reject(symbol, ledger.last_reject_reason or "LEDGER_REJECT")
        return "REJECTED"
    if on_opened is not None:
        on_opened(pos, act)
    return "OPENED"


class StrategyBook:
    """Canlı motorda tek kurallı stratejinin AYRI kâğıt defteri (ileri test)."""

    def __init__(self, cfg, *, profile, killswitch, filters_cache, run_id: str, spec: "BookSpec | None" = None):
        v3 = cfg.v3
        sp = spec or BookSpec.from_section(v3.strategy_paper)
        self.spec = sp
        self.key = sp.state_dir
        self.summary_file = sp.summary_file
        self.cfg, self.profile, self.filters_cache, self.run_id = cfg, profile, filters_cache, run_id
        self.name = str(sp.name)
        self.atr_mult = float(sp.atr_mult)
        # KURAL KİMLİĞİ (V15): dilimler, BTC ihtiyacı ve parametre nesnesi TEK yerden. Geçersiz ad/parametre
        # burada ValueError verir — defter sessizce yanlış kuralla açılmaz.
        self.rule = paper_rules.spec_for(self.name)
        self.rule_params = paper_rules.build_params(self.name, atr_mult=self.atr_mult, rule_params=sp.rule_params)
        self.symbols: list[str] = list(sp.symbols) if sp.symbols else []
        self.max_entry_drift_pct = float(getattr(sp, "max_entry_drift_pct", 0.0) or 0.0)
        self.state_dir = Path(cfg.state_path) / str(sp.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "futures_ledger.json"
        fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)), source=v3.fees.source)
        slip = SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps)))
        self.ledger = FuturesLedgerV2.load(self.ledger_path, starting_equity=float(sp.starting_equity_usdt),
                                          max_positions=cfg.futures.max_positions,
                                          enforce_position_cap=enforces_position_cap(profile),
                                          fees=fees, slippage=slip, brackets=default_brackets(),
                                          liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
                                          tp1_fraction=Decimal(str(v3.futures_v3.tp1_fraction)),
                                          breakeven_at_mfe_r=Decimal(str(sp.breakeven_at_mfe_r)),
                                          tax_policy=TaxPolicy.disabled())
        self.risk = RiskEngine(profile, killswitch, v3.risk_profiles.clusters or None)
        self.memory = TradeMemory(self.state_dir / "trade_memory.jsonl", source="STRATEGY_PAPER")
        self.lock = threading.RLock()
        self.last_actions: dict[str, dict[str, Any]] = {}
        self.counters = {"opened": 0, "closed": 0, "rejected": 0, "tours": 0, "data_rejected": 0}
        # Kural dongusunun en son KOSTUGU an — `generated_at`ten AYRI (bkz. step()).
        self.rule_evaluated_at: str | None = None
        self.rule_tour: int = 0
        self._rule_ran_since_save: bool = False
        self.rejections: dict[str, int] = {}
        self.regime: str | None = None
        self.closed_recent: list[dict[str, Any]] = []
        # VERİ KAYNAĞI izlenebilirliği (2026-09-16): bu turun sembol başına hükmü, fiyat boşlukları ve son olaylar (kalıcı kuyruk)
        self.data_checks: dict[str, dict[str, Any]] = {}
        self.data_gaps: dict[str, dict[str, Any]] = {}
        self.data_events: list[dict[str, Any]] = []
        #: Gerçekleşmiş funding kaynağı (`bind_funding`); None iken dönemler BEKLER (bekleyen maliyet, tahmin yok).
        self.funding_rates: Any = None
        #: ORTAK YAPI POLİTİKASI (structures_v1): OFF | SHADOW | ENFORCE (config `structures`). Son kararlar özet dosyasına.
        _st = getattr(v3, "structures", None)
        self.structure_mode = _st.mode_for(self.name) if _st is not None else "OFF"
        self.structure_decisions: dict[str, dict[str, Any]] = {}
        self._structure_store = None
        #: İZLEME KESİNTİSİ (2026-09-23): defterin bu süreç açılmadan ÖNCEKİ son kaydı ve o anda açık pozisyonlar;
        #: bu süreçteki İLK defter etkinliğinde (`_resume_once`) bir kez denetlenir.
        self._resume_saved_at = self.ledger.updated_at
        self._resume_positions: list[str] = sorted(self.ledger.positions)
        self._resume_checked = False
        self._gap_until_ms: int | None = None
        self.monitoring_gap: dict[str, Any] | None = None
        #: KORUYUCU İZLEYİCİ (2026-09-24): gözlem ölçümü (`protective_monitor.ObservationLog`); None → ölçüm yok.
        self.observer: Any = None
        #: ÖĞRENME MODU (2026-09-28, öğrenme modu): hepsi TEMBEL kurulur — anahtar bu süreçte hiç açılmazsa hiçbiri oluşmaz
        #: (dosya yok, özet alanı yok). `risk_learning`: taban profilin KOPYASI (toplam açık risk 100; PROFILES'a yazılmaz).
        self.risk_learning: RiskEngine | None = None
        self._risk_learning_key: float | None = None
        self.rule_params_learning: Any = None
        self._rule_params_learning_key: float | None = None
        self.cf: Any = None                           # learning_cf.CounterfactualRecorder (state_dir/CF_FILE)
        self.learning: dict[str, Any] | None = None   # özet dosyasına giden durum (yalnız öğrenme görüldüyse)
        self.learning_counters: dict[str, int] = {}
        self._restore_counters()

    def _restore_counters(self) -> None:
        """Yeniden baslatmada sayaclar SIFIRLANMAZ (V13). opened/closed defterin kendisinden (kalici gercek),
        rejected/tours/retler/son kapanislar bir onceki ozet dosyasindan gelir. Ozet yoksa ya da bozuksa yalniz
        defter gercegi kullanilir; defterin kendisine hicbir kosulda dokunulmaz."""
        try:
            p = Path(self.cfg.state_path) / self.summary_file
            if p.exists():
                prev = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(prev, dict) and prev.get("key", self.key) == self.key and prev.get("name", self.name) == self.name:
                    pc = prev.get("counters") or {}
                    self.counters["rejected"] = int(pc.get("rejected") or 0)
                    self.counters["tours"] = int(pc.get("tours") or 0)
                    self.counters["data_rejected"] = int(pc.get("data_rejected") or 0)
                    self.rejections = {str(k): int(v) for k, v in (prev.get("rejections") or {}).items()}
                    self.closed_recent = list(prev.get("closed_recent") or [])[-50:]
                    self.data_events = [e for e in (prev.get("data_events_recent") or []) if isinstance(e, dict)][-50:]
                    # Kuralin en son KOSTUGU an gercek bir olgudur: yeniden baslatma onu silmez.
                    # Geri yuklenmezse restart sonrasi `rule_stale_s` None kalir ve "kural hic kosmadi"
                    # ile "kural cok once kostu" ayirt edilemez.
                    _rev = prev.get("rule_evaluated_at")
                    self.rule_evaluated_at = str(_rev) if _rev else None
                    self.rule_tour = int(prev.get("rule_tour") or 0)
                    # öğrenme modu sayaçları (2026-09-28): yalnız özet onları taşıyorsa (anahtar hiç açılmadıysa alan yok)
                    _lc = (prev.get("learning") or {}).get("counters") if isinstance(prev.get("learning"), dict) else None
                    if isinstance(_lc, dict):
                        self.learning_counters = {str(k): int(v) for k, v in _lc.items() if isinstance(v, (int, float))}
        except Exception as exc:  # noqa: BLE001 -- ozet bozuksa sayac sifirdan baslar, defter ETKILENMEZ
            log.warning("strateji defter sayaclari geri yuklenemedi (%s): %s", self.key, exc)
        closed = len(self.ledger.history_dicts())
        self.counters["closed"] = closed
        self.counters["opened"] = closed + len(self.ledger.positions)

    # ------------------------------------------------------------------ yardımcılar
    def _state(self, marks_f: dict[str, float]):
        pos = [{"symbol": s, "market_type": "USDM_PERP", "side": p.side.value, "notional": float(p.qty * p.entry_avg),
                "margin": float(p.isolated_margin), "entry": float(p.entry_avg), "stop": float(p.stop) if p.stop else None,
                "leverage": p.leverage, "liq_price": float(p.liquidation_price) if p.liquidation_price else None,
                "opened_at": p.opened_at} for s, p in self.ledger.positions.items()]
        fs = self.ledger.summary(marks_f)
        return build_state(equity=float(fs["equity_mtm"]), starting_equity=float(self.ledger.starting_equity),
                           available=float(fs["available"]), used_margin=float(fs["used_margin"]), positions=pos,
                           history=self.ledger.history_dicts(), high_water_mark=0.0, now=utc_now(),
                           clusters=self.cfg.v3.risk_profiles.clusters or None)

    def _reject(self, symbol: str, reason: str) -> None:
        if str(reason).startswith("DATA_"):
            self.counters["data_rejected"] += 1       # veri kaynağı reddi: risk/defter reddinden AYRI sayaç
        else:
            self.counters["rejected"] += 1
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        self.last_actions[symbol] = {"action": "REJECTED", "reason": reason, "at": iso(utc_now())}

    def _data_event(self, symbol: str, kind: str, reason: str, at: datetime, **extra: Any) -> None:
        ev = {"symbol": symbol, "kind": kind, "reason": reason, "at": iso(at), **extra}
        self.data_events.append(ev)
        self.data_events = self.data_events[-50:]

    def _reject_data(self, symbol: str, verdict: DataVerdict, stage: str, at: datetime, would_act: str | None) -> None:
        """Veri kaynağı reddi: sayaç + gerekçe + olay (sembol/defter/gerekçe bazında görünür; sinyal yokluğu ve risk
        reddinden ayrı). `would_act`: kanıtsız veriyle hesaplanan (UYGULANMAYAN) kural sonucu — yalnız bilgi."""
        self._reject(symbol, verdict.reason or "DATA_UNVERIFIED")
        self.last_actions[symbol] = {"action": "DATA_REJECTED", "reason": verdict.reason, "stage": stage, "at": iso(at),
                                     "market": verdict.market, "tour_id": verdict.tour_id, "would_act": would_act}
        self._data_event(symbol, "REJECT", verdict.reason, at, stage=stage, market=verdict.market, source=verdict.source,
                         tour_id=verdict.tour_id, bars=dict(verdict.bars), btc=dict(verdict.btc), would_act=would_act)

    def record_gaps(self, gaps: dict[str, dict[str, Any]], now: datetime) -> None:
        """Doğrulanmış futures fiyatı olmayan semboller (tick YOK, uydurma gerçekleşme YOK): durum + olay (durum
        değişince bir kez; her 60 sn'de yinelenmez)."""
        with self.lock:
            self._resume_once(now)
            for sym, g in (gaps or {}).items():
                prev = self.data_gaps.get(sym)
                if not prev or prev.get("reason") != g.get("reason"):
                    self._data_event(sym, "PRICE_GAP", str(g.get("reason") or "NO_VERIFIED_FUTURES_PRICE"), now, detail=g.get("detail"))
                self.data_gaps[sym] = dict(g)
            for sym in [s for s in self.data_gaps if s not in (gaps or {})]:
                self._data_event(sym, "PRICE_RESTORED", "", now)
                self.data_gaps.pop(sym, None)

    # ------------------------------------------------------------------ funding (2026-09-22, funding_settlement_v2)
    def bind_funding(self, rates: Any) -> None:
        """Gerçekleşmiş settlement kaynağını (oran + settlement mark'ı) deftere BAĞLAR — formasyon defteriyle AYNI
        kural (`FundingSchedule.bind_source`): sözleşmenin kendi aralığı, bilinmeyen dönem BEKLER (tahmin yok)."""
        self.funding_rates = rates
        self.ledger.funding.bind_source(rates)

    def reconcile_funding(self, now: datetime) -> list[dict]:
        """Kapanmış işlemlerin BEKLEYEN funding'ini, veri bellekte bulununca deftere işler (AĞ YOK; ağ adımı motorun
        `_funding_step`i). Defter + işlem kaydı + özet satırı AYNI anda güncellenir; yinelenmezlik kayıttaki
        `funding_settled_ts`dir. Bkz. `FuturesLedgerV2.settle_late_funding`."""
        rates = getattr(self, "funding_rates", None)
        if rates is None:
            return []
        with self.lock:
            self._resume_once(now)
            posted = self.ledger.settle_late_funding(rates, now=now, hours_for=getattr(rates, "hours_for", None))
            if not posted:
                return []
            touched = {p["trade_id"] for p in posted}
            for rec in self.ledger.history:
                if rec.id not in touched:
                    continue
                cov = (rec.features or {}).get("funding_coverage") or {}
                for row in self.closed_recent:
                    if row.get("id") == rec.id:
                        row.update({"net_pnl": float(rec.net_pnl), "r": float(rec.r_multiple), "funding": float(rec.funding),
                                    "funding_late": True, "funding_complete": cov.get("complete")})
            for p in posted:
                self._data_event(p["symbol"], "FUNDING_LATE_POSTED", "SETTLEMENT_" + p["settlement"], now,
                                 trade_id=p["trade_id"], amount=p["amount"], rate=p["rate"], mark=p["mark"], qty=p["qty"])
            self.ledger.save(self.ledger_path)
            return posted

    def _on_closed(self, rec) -> None:
        self.counters["closed"] += 1
        d = rec.to_legacy_dict() if hasattr(rec, "to_legacy_dict") else {}
        _cov = ((d.get("features") or {}).get("funding_coverage") or {})
        self.closed_recent.append({"id": getattr(rec, "id", None), "symbol": getattr(rec, "symbol", None),
                                   "funding": float(getattr(rec, "funding", 0) or 0),
                                   # funding HENÜZ mutabık değilse net sonuç kesin DEĞİLDİR (bekleyen maliyet)
                                   "funding_complete": _cov.get("complete"),
                                   "exit_reason": getattr(rec, "exit_reason", None),
                                   "net_pnl": float(getattr(rec, "net_pnl", 0) or 0), "r": float(getattr(rec, "r_multiple", 0) or 0),
                                   "closed_at": getattr(rec, "closed_at", None), "features": d.get("features"),
                                   # 2026-09-22: fiyat yolu tam doğrulanmadan kapanan işlem özet satırında GÖRÜNÜR (F1)
                                   "path_unverified": bool((d.get("features") or {}).get("path_unverified")),
                                   "exit_basis": ((d.get("features") or {}).get("exit_fill") or {}).get("basis")})
        self.closed_recent = self.closed_recent[-50:]

    def _record_structure(self, sym: str, sdec: dict[str, Any], analyses: dict[str, Any], *, res: str, now_ms: int,
                          pos_before: Any, mode: str | None = None) -> None:
        """Yapı kararını ortak depoya ve özet dosyasına yazar (panel motorun kaydını çizer). Arıza işlemi ETKİLEMEZ.
        `mode`: bu kararda GERÇEKTEN uygulanan kip (öğrenme modunda giriş gölgesi → SHADOW); None → defterin kipi."""
        try:
            from .structures.store import StructureStore
            if self._structure_store is None:
                self._structure_store = StructureStore(self.cfg.state_path)
            trade_id = None
            if res == "OPENED":
                p = self.ledger.positions.get(sym)
                trade_id = getattr(p, "id", None)
            elif res == "CLOSED" and pos_before is not None:
                trade_id = getattr(pos_before, "id", None)
            for an in (analyses or {}).values():
                if an:
                    self._structure_store.save_latest(an)
            row = self._structure_store.record_decision(dict(sdec, mode=mode or self.structure_mode, applied=res), book_id=self.key,
                                                        market="USDM_PERP", symbol=sym, at_ms=now_ms, analyses=analyses,
                                                        trade_id=trade_id)
            keep = {k: row.get(k) for k in ("bot", "policy_version", "action", "reason_code", "side", "text_tr", "pattern_ids",
                                             "primary", "plan", "analysis_ids", "at_ms", "first_seen_ms", "last_seen_ms",
                                             "decision_id", "trade_id", "mode", "applied")}
            self.structure_decisions[sym] = keep
        except Exception as exc:  # noqa: BLE001
            log.warning("yapı kararı kaydedilemedi (%s %s): %s", self.key, sym, exc)

    def _on_opened(self, pos, act: dict[str, Any]) -> None:
        self.counters["opened"] += 1
        if act.get("_notional_scaled"):
            # tavana küçültülen işlem (bkz. apply_action TAVANA KÜÇÜLTME): risk bütçenin altında — kayıtta görünür
            pos.meta["size_scaled_to_cap"] = dict(act["_notional_scaled"])
        if act.get("one_entry_per_signal"):
            pos.meta["signal"] = {"signal_ts": act.get("signal_ts"), "signal_close_ms": act.get("signal_close_ms"),
                                  "signal_close": act.get("signal_close"), "atr14": act.get("atr14"),
                                  "lab_algo": act.get("lab_algo")}
        try:
            mfeats = {"strategy": act.get("name"), "signal_close": act.get("signal_close"),
                      "ema200": act.get("ema200"), "atr14": act.get("atr14"),
                      # CHART ANALYSIS V1: giris ANINDAKI referanslar (sonradan degismez)
                      "signal_ts": act.get("signal_ts"), "ref_close": act.get("ref_close"),
                      "ref_ts": act.get("ref_ts"), "stop_at_entry": act.get("stop"),
                      "atr_mult": self.atr_mult}
            _lr = (pos.features or {}).get("learning")
            if isinstance(_lr, dict):
                # ÖĞRENME MODU (2026-09-28, öğrenme modu): işlem hafızası satırı da etiketleri taşır (yalnız öğrenmede açılan)
                mfeats["learning"] = dict(_lr)
                if "in_lab_universe" in pos.features:
                    mfeats["in_lab_universe"] = bool(pos.features["in_lab_universe"])
            self.memory.record_entry({"trade_id": pos.id, "symbol": pos.symbol, "direction": pos.side.value, "market_type": "USDM_PERP",
                                      "setup_type": "trend", "regime": act.get("regime"),
                                      "features": mfeats, "run_id": self.run_id, "in_test": True})
        except Exception as exc:  # noqa: BLE001 — bellek arızası işlemi ETKİLEMEZ
            log.warning("strateji bellek kaydı yazılamadı (%s): %s", pos.symbol, exc)

    # ------------------------------------------------------------------ tur
    def step(self, *, symbols: list[str], frames_by_symbol: dict[str, dict], marks: dict[str, TickData],
             marks_f: dict[str, float], now: datetime, provenance_by_symbol: dict[str, dict] | None = None,
             data_gaps: dict[str, dict] | None = None, learning: Any = None,
             structures_entry_shadow: bool = False) -> None:
        """Her turda: veri kimliği doğrulaması → kural → apply_action. Ana bot dokunulmaz; yalnız bu defter değişir.

        `provenance_by_symbol` (2026-09-16): sağlayıcının BU tur için yazdığı çerçeve provenansı (motor
        `_frame_provenance`, tur kimliği ve bar bağıyla). Yoksa/eşleşmiyorsa/SPOT ise kural bu sembol için
        DEĞERLENDİRİLMEZ (ne OPEN ne kural CLOSE'u SPOT ikamesiyle üretilir); ret gerekçeli kaydedilir. Açık
        pozisyonun stop/hedef/likidasyon takibi `tick` ile ayrı sürer. `marks`/`marks_f`: DOĞRULANMIŞ futures
        fiyatı (motor `_paper_marks`); `data_gaps`: fiyatı doğrulanamayan semboller (bu turda tick de yok).

        ÖĞRENME MODU (2026-09-28, öğrenme modu): `learning` = bu geçişin `BookLearning` görünümü (motor ya da Box
        zamanlayıcısı `LearningMode.book(ad)` ile verir) ya da None. None/kapalı → bit-bit eski yol. Açıkken: öğrenme
        RiskEngine'i + `fit_size` (`apply_action(learning=…)`), Box'ta öğrenme `min_stop_pct`i, reddedilen geçerli sinyal
        için karşı-olgusal kayıt (anahtar = barın sinyal kimliği), bekleyen kayıtların eldeki çerçevelerle etiketlenmesi
        (ek API çağrısı yok). `structures_entry_shadow` (motor `lm.structures_entry_shadow(ad)`): yalnız öğrenme açıkken,
        ENFORCE yapı kipinde YENİ GİRİŞ kararı gölgeye alınır (bekle/iptal/geometri yok); açık pozisyonun yönetimi (yapı
        çıkışı) ENFORCE kalır. `symbols` motorca genişletilebilir (D4/C4 evreni); `features.in_lab_universe` defterin
        kendi listesine üyeliktir."""
        with self.lock:
            self._resume_once(now)
            self.counters["tours"] += 1
            # KURAL DONGUSU TAZELIGI (2026-09-19): `generated_at` defterin YAZILDIGI andir ve 60 sn'lik
            # cikis izleyicisi (engine_v3._strategy_paper_exit_check) her dakika `save()` cagirdigi icin
            # KURAL kosmasa bile tazelenir. Ana bot boru hattindaki bir istisna `_strategy_paper_tour`a
            # hic ulasilmamasina yol acarsa (engine_v3.py:1301 korumasizdir ve onunde 66 korumasiz ifade
            # vardir) defter disaridan CANLI gorunur: taze damga + onceki turun hepsi-yesil `data_checks`.
            # Bu yuzden kural dongusunun kendi zamani AYRI ilan edilir; bayatligi olcen kod artik var.
            self.rule_evaluated_at = iso(now)
            self.rule_tour = int(self.counters["tours"])
            # OLGU, cikarim DEGIL: bir sonraki `save` bu turun kural gecisini ilan eder ve bayragi
            # tuketir. Once bu bayrak `now - rule_evaluated_at < 1 sn` ile TURETILIYORDU; yuk
            # altinda step ile save arasi bir saniyeyi asinca kendi testim dustu (2026-09-19 tam
            # paket kosusu). Zaman farkindan turetilen bayrak, tam da bu oturumda panel testlerinde
            # onardigim duvar-saati kirilganligiydi; olcum yerine OLGU tasinir.
            self._rule_ran_since_save = True
            now_ms = int(now.timestamp() * 1000)
            prov_all = provenance_by_symbol or {}
            self.data_checks = {}
            self.record_gaps(data_gaps or {}, now)
            btc_fr = frames_by_symbol.get(BTC_SYMBOL) or {}
            btc = closed_bars(daily_rows_from_frame(btc_fr.get("1d")), now_ms=now_ms, tf="1d")
            from .regime_gate import btc_regime
            self.regime = btc_regime(btc)
            state = self._state(marks_f)
            # ÖĞRENME MODU (2026-09-28): geçiş başına değişmez görünüm; kapalıyken aşağıdaki yol bit-bit eskisi.
            bl = learning if (learning is not None and getattr(learning, "on", False)) else None
            params, risk_eng = self.rule_params, self.risk
            eshadow = False
            if bl is not None:
                eshadow = bool(structures_entry_shadow)
                params, risk_eng = self._learning_params(bl), self._learning_risk(bl)
                self._learning_begin(bl, now, eshadow)
            elif self.learning is not None and self.learning.get("active"):
                # askıya alındı / kapatıldı: bu geçiş baseline; açık pozisyonlar kendi stop/hedefiyle yönetilir
                self.learning = dict(self.learning, active=False, suspended_at=iso(now))
            # KAPSAM (V13): `symbols` = YENI giris izinli evren (olculen on coin). Defterin kendi acik
            # pozisyonlari evren disinda kalsa bile YONETILIR (kural kapanisi + stop); `decide` acik
            # pozisyonda hicbir zaman OPEN dondurmez, dolayisiyla evren disinda yeni giris olamaz.
            for sym in dict.fromkeys(list(symbols) + list(self.ledger.positions)):
                pos_open = sym in self.ledger.positions
                if sym not in marks_f:
                    g = (data_gaps or {}).get(sym)
                    if g:
                        self.data_checks[sym] = {"ok": False, "entry_ok": False, "reason": "DATA_NO_FUTURES_PRICE", "detail": g.get("detail")}
                        self.last_actions[sym] = {"action": "DATA_GAP", "reason": "DATA_NO_FUTURES_PRICE", "at": iso(now)}
                    continue
                fr = frames_by_symbol.get(sym) or {}
                # ZAMAN SÖZLEŞMESİ: değerlendirme anı = turun karar saati (`now`); kural da AYNI anda kapanmış barları okur.
                pos_obj = self.ledger.positions.get(sym)
                verdict = verify_paper_data(symbol=sym, frames=fr, provenance=prov_all.get(sym), run_id=self.run_id,
                                            btc_frames=btc_fr, btc_provenance=prov_all.get(BTC_SYMBOL),
                                            need_btc=self.rule.needs_btc and not pos_open,
                                            as_of_ms=now_ms, tfs=self.rule.timeframes)
                if verdict.ok:
                    # Kaydedilen bar ile kuralın okuduğu bar HER dilimde birebir aynı olmalı; değilse hüküm
                    # KANITSIZ (fail-closed). V15: box defteri 1d + 5m okuduğu için bağ dilim dilim denetlenir.
                    bad_tf, rule_last = self._bar_binding(fr, verdict, now_ms)
                    if bad_tf:
                        verdict = DataVerdict(ok=False, entry_ok=False, reason="DATA_BAR_MISMATCH_%s" % bad_tf.upper(),
                                              market=verdict.market, source=verdict.source,
                                              tour_id=verdict.tour_id, bars=dict(verdict.bars), btc=dict(verdict.btc), as_of_ms=now_ms,
                                              detail=dict(verdict.detail) | {"rule_last_open_ms": rule_last, "rule_tf": bad_tf})
                self.data_checks[sym] = verdict.to_dict()
                if not verdict.ok or (not pos_open and not verdict.entry_ok):
                    # Kanıtsız veriyle kural UYGULANMAZ. Yalnız bilgi için: bu veriyle kural ne derdi (uygulanmadı)?
                    try:
                        wa = paper_rules.decide_for(self.name, frames=fr, btc_rows=btc, now_ms=now_ms,
                                                    position=pos_obj, params=params)
                        would = str((wa or {}).get("action") or "NONE")
                    except Exception:  # noqa: BLE001
                        would = "ERROR"
                    self._reject_data(sym, verdict, "SIGNAL" if not verdict.ok else "ENTRY", now, would)
                    continue
                sdec, sanal = None, {}
                # YAPI KİPİ (öğrenme modu, 2026-09-28): yalnız YENİ GİRİŞ gölgeye alınır; açık pozisyon (yönetim) ENFORCE kalır.
                smode = self.structure_mode
                if eshadow and smode == "ENFORCE" and pos_obj is None:
                    smode = "SHADOW"
                try:
                    if smode != "OFF":
                        # ORTAK YAPI (structures_v1): canlı defter ve replay AYNI girişi kullanır (paper_rules).
                        from .structures.bots import StructureContext, used_patterns_of
                        sctx = StructureContext(mode=smode, symbol=sym, as_of_ms=now_ms, price=float(marks_f[sym]),
                                                used_patterns=used_patterns_of(self.ledger),
                                                provenance={"market": verdict.market, "source": verdict.source,
                                                            "tour_id": verdict.tour_id, "bars": dict(verdict.bars)})
                        act, sdec, sanal = paper_rules.decide_with_structures(self.name, frames=fr, btc_rows=btc, now_ms=now_ms,
                                                                              position=pos_obj, params=params, ctx=sctx)
                        if smode != self.structure_mode and self.rule.family == "trend" and isinstance(sdec, dict):
                            from .structures.bots import BLOCKING_ACTIONS
                            if str(sdec.get("action") or "") not in BLOCKING_ACTIONS:
                                # S4 (2026-09-28, öğrenme modu): giriş gölgesi YALNIZ engelleyecek kararı (bekle/tetik
                                # bekle/iptal) gölgeler. ENFORCE'un da geçireceği karar (ENTER/uyumlu) ENFORCE'taki gibi
                                # işaretsiz kalır: trend kuralında ENFORCE geometriyi değiştirmez, yalnız referansı yazar →
                                # M2 ENTRY_STRUCTURE_FAILED yönetimi ve kullanılmış yapı sayımı ENFORCE ile aynı.
                                smode = self.structure_mode
                                if isinstance((act or {}).get("structure"), dict):
                                    act["structure"].pop("shadow", None)
                        _serr = paper_rules.structure_error_of(sdec)
                        if _serr:
                            # ortak geri düşüş arızayı kararda taşır; SAYAÇ da görür (tur-4 doğrulayıcı #5; replay aynı)
                            self._reject(sym, _serr)
                    else:
                        act = paper_rules.decide_for(self.name, frames=fr, btc_rows=btc, now_ms=now_ms,
                                                     position=pos_obj, params=params)
                except Exception as exc:  # noqa: BLE001 — strateji arızası SESSİZ GEÇMEZ
                    if smode == "OFF":
                        self._reject(sym, "STRATEGY_ERROR:%s" % type(exc).__name__)
                        continue
                    # Arıza nerede: botun KENDİ kuralı yeniden sorulur. Kural da hata verirse arıza kuralındır — yalnız
                    # STRATEGY_ERROR (replay ile aynı; önce ayrıca STRUCTURE_ERROR da sayılıyordu, tur-5 F5). Kural
                    # sağlamsa arıza yapı katmanındadır (bulgu #16): çıkış/yönetim ASLA düşmez; ENFORCE'ta yapı
                    # ölçülemediği için YENİ GİRİŞ yok (fail-closed); SHADOW'da kural aynen uygulanır.
                    sdec, sanal = None, {}
                    try:
                        act = paper_rules.decide_for(self.name, frames=fr, btc_rows=btc, now_ms=now_ms,
                                                     position=pos_obj, params=params)
                    except Exception as exc2:  # noqa: BLE001
                        self._reject(sym, "STRATEGY_ERROR:%s" % type(exc2).__name__)
                        continue
                    self._reject(sym, "STRUCTURE_ERROR:%s" % type(exc).__name__)
                    if smode == "ENFORCE" and str((act or {}).get("action") or "").upper() == "OPEN":
                        continue
                if not pos_open and paper_rules.signal_already_used(act, self.ledger.history, sym):
                    # aynı sinyalle ikinci giriş yok (laboratuvarda her sinyal TEK işlem; stop aynı barda gelirse)
                    act = {"action": "NONE", "reason": "SIGNAL_ALREADY_USED", "name": self.name}
                if bl is not None and not pos_open and act and str(act.get("action") or "").upper() == "OPEN":
                    act["_learning"] = self._learning_tags(sym, act, sdec, smode, price=float(marks_f[sym]))
                res = apply_action(act, symbol=sym, price=float(marks_f[sym]), tick=marks.get(sym), now=now,
                                   ledger=self.ledger, risk=risk_eng, profile=self.profile, state=state,
                                   filters=self.filters_cache.get(sym, MarketType.USDM_PERP), run_id=self.run_id,
                                   reject=self._reject, on_closed=self._on_closed, on_opened=self._on_opened, data=verdict,
                                   max_entry_drift_pct=self.max_entry_drift_pct, learning=bl)
                if sdec is not None:
                    self._record_structure(sym, sdec, sanal, res=res, now_ms=now_ms, pos_before=pos_obj,
                                           mode=(smode if smode != self.structure_mode else None))
                if res in ("OPENED", "CLOSED"):
                    self.last_actions[sym] = {"action": res, "reason": (act or {}).get("reason"), "at": iso(now),
                                              "data": {"market": verdict.market, "source": verdict.source, "tour_id": verdict.tour_id, "bars": dict(verdict.bars)}}
                    state = self._state(marks_f)
                elif act is None or str((act or {}).get("action") or "").upper() == "NONE":
                    # TELEMETRI (2026-09-19): bu kayit ONCEDEN yalniz BIR KEZ yazilirdi
                    # (`sym not in self.last_actions`) ve `last_actions` yeniden baslatmada geri
                    # YUKLENMEZ (bkz. ozet geri yukleme: counters/rejections/closed_recent/data_events).
                    # Sonuc: acik bir pozisyonun hukmu, yeniden baslatmadan SONRAKI ILK turda donuyordu;
                    # panelde ve teshiste "her tur degerlendirildi, cikis sinyali yok" ile "artik hic
                    # degerlendirilmiyor" AYIRT EDILEMIYORDU. Olculdu: 2026-09-18 21:13Z dagitiminin
                    # ardindan T2/M2'nin uc acik pozisyonu 21:16:01'de donmus gorunuyordu; cikis yolu
                    # SAGLAMDI, yalniz GORUNTUSU bayatti — bu, hapsolmus pozisyon suphesini dogurdu.
                    # Kayit artik her turda tazelenir; `tour` ve `held` alanlari bayatligi makineyle
                    # olculebilir kilar. `reason` degismedi: flat sembolde "NO_SIGNAL" sozlesmedir.
                    # "Olcemiyorum" ile "sinyal yok" AYRI kayitlardir (2026-09-19). Kural, acik
                    # pozisyonun cikisini olcemediginde EXIT_UNMEASURABLE dondurur; bu, ozette
                    # kalici ve BIRIKIMLI bir sayac olarak gorunur (rejections restart'ta korunur),
                    # yoksa sessizce tutulan pozisyon "trend bozulmadi" gibi okunur.
                    _reason = str((act or {}).get("reason") or "NO_SIGNAL")
                    self.last_actions[sym] = {"action": "NONE", "reason": _reason, "at": iso(now),
                                              "tour": int(self.counters["tours"]), "held": bool(pos_open)}
                    if _reason != "NO_SIGNAL" and not _reason.startswith("STRUCTURE_ERROR"):
                        # yapı arızası yukarıda `_reject` ile BİR KEZ sayıldı (çift sayım yok)
                        self.rejections[_reason] = self.rejections.get(_reason, 0) + 1
                if bl is not None:
                    self._learning_after(sym, act, res, bl=bl, pos_open=pos_open, pos_obj=pos_obj, verdict=verdict, fr=fr,
                                         btc=btc, now=now, now_ms=now_ms, price=float(marks_f[sym]), params=params,
                                         state=state)
            if self.cf is not None:
                # bekleyen karşı-olgusallar eldeki çerçevelerle (ek API yok); askıdayken de etiketleme sürer (kayıp yok)
                self._learning_label(frames_by_symbol, now)

    # ------------------------------------------------------------------ öğrenme modu (2026-09-28)
    def _learning_params(self, bl: Any) -> Any:
        """Box: öğrenme `min_stop_pct` (0,32) ile AYRI parametre nesnesi — taban nesne değişmez; diğer aileler taban."""
        msp = getattr(bl, "min_stop_pct", None)
        if self.rule.family != "box" or msp is None:
            return self.rule_params
        key = float(msp)
        if self.rule_params_learning is None or self._rule_params_learning_key != key:
            rp = dict(self.spec.rule_params or {})
            rp["min_stop_pct"] = key
            self.rule_params_learning = paper_rules.build_params(self.name, atr_mult=self.atr_mult, rule_params=rp)
            self._rule_params_learning_key = key
        return self.rule_params_learning

    def _learning_risk(self, bl: Any) -> RiskEngine:
        """Öğrenme RiskEngine'i: taban profilin KOPYASI (toplam açık risk tavanı; işlem tavanı 2 aynen), AYNI kill switch."""
        key = float(getattr(bl, "max_total_open_risk_pct", None) or DEFAULT_MAX_TOTAL_OPEN_RISK_PCT)
        if self.risk_learning is None or self._risk_learning_key != key:
            self.risk_learning = RiskEngine(profile_for(self.profile, max_total_open_risk_pct=key), self.risk.ks,
                                            self.risk.clusters)
            self._risk_learning_key = key
        return self.risk_learning

    def _learning_begin(self, bl: Any, now: datetime, eshadow: bool) -> None:
        prev = self.learning or {}
        since = prev.get("since") if prev.get("active") else iso(now)
        self.learning = {"active": True, "since": since, "last_active_at": iso(now),
                         "book": bl.to_dict() if hasattr(bl, "to_dict") else {"name": getattr(bl, "name", None)},
                         "structures_entry_shadow": bool(eshadow and self.structure_mode == "ENFORCE"),
                         "rule_params_effective": ({"min_stop_pct": float(bl.min_stop_pct)}
                                                   if self.rule.family == "box" and getattr(bl, "min_stop_pct", None) is not None
                                                   else None)}
        if getattr(bl, "counterfactual", False):
            self._cf_recorder(bl)

    def _count(self, key: str, n: int = 1) -> None:
        self.learning_counters[key] = int(self.learning_counters.get(key, 0)) + int(n)

    def _cf_recorder(self, bl: Any) -> Any:
        if self.cf is None:
            from .learning_cf import CounterfactualRecorder
            self.cf = CounterfactualRecorder(self.state_dir / CF_FILE, book=self.name,
                                             max_pending=int(getattr(bl, "max_pending", 2000) or 2000),
                                             archive=self._cf_archive())
            # sayaçların kalıcı yedeği: özet `learning` alanını kaybettiyse (öğrenme kapalı geçti / eski kod) geri gelir
            self.learning_counters = self.cf.sync_book_counters(self.learning_counters)
        else:
            self.cf.max_pending = max(1, int(getattr(bl, "max_pending", self.cf.max_pending) or self.cf.max_pending))
        return self.cf

    def _cf_archive(self) -> Any:
        """Etiketlenmiş kayıtlar taşarsa KAYIPSIZ arşiv (ana gölge defteriyle aynı kural); kurulamazsa None → budama yok."""
        lv = getattr(self.cfg.v3, "learning_v3", None)
        if not bool(getattr(lv, "decision_archive_enabled", False)):
            return None
        try:
            from .learn.journal_archive import SegmentArchive
            return SegmentArchive(self.state_dir / CF_ARCHIVE_DIR, stream_id="counterfactual_%s" % self.key,
                                  record_schema_version="shadow_trade_v1",
                                  max_segments=int(getattr(lv, "decision_archive_max_segments", 0) or 0))
        except Exception as exc:  # noqa: BLE001 — arşiv kurulamazsa SİLME de yapılmaz
            log.warning("karşı-olgusal arşivi kurulamadı (%s): %s", self.key, exc)
            return None

    def _learning_tags(self, sym: str, act: dict[str, Any], sdec: dict[str, Any] | None, smode: str, *,
                       price: float) -> dict[str, Any]:
        """Girişin öğrenme etiketleri: kendi evrenine üyelik ve anahtar kapalıyken bu sinyali ÜRETMEYECEK/GEÇİRMEYECEK
        defter düzeyi kapılar (`apply_action` bunlara risk/defter kapılarını ekler → `learning_unlocked_by`)."""
        unlocked: list[str] = []
        tags: dict[str, Any] = {}
        if self.symbols:
            inside = sym in self.symbols
            tags["in_lab_universe"] = inside
            if not inside:
                unlocked.append("BOOK_UNIVERSE")
        if smode != self.structure_mode and isinstance(sdec, dict):
            from .structures.bots import BLOCKING_ACTIONS
            act_s = str(sdec.get("action") or "")
            blocked = (act_s in BLOCKING_ACTIONS) if self.rule.family == "trend" else (act_s != "ENTER")
            if blocked:
                unlocked.append("STRUCTURE_%s" % (sdec.get("reason_code") or act_s))
                self._count("structures_entry_shadowed")
        if self.rule.family == "box":
            msp = float(getattr(self.rule_params, "min_stop_pct", 0.0) or 0.0)
            try:
                px = float(act.get("signal_close") or price)
                st = float(act.get("stop") or 0.0)
            except (TypeError, ValueError):
                px, st = 0.0, 0.0
            if msp > 0 and px > 0 and st > 0 and abs(px - st) / px * 100.0 < msp:
                unlocked.append("BOX_MIN_STOP_PCT")
        tags["unlocked_by"] = unlocked
        return tags

    def _decision_tf(self) -> str:
        intra = [t for t in self.rule.timeframes if t != "1d"]
        return intra[-1] if intra else "1d"

    def _cf_record(self, sym: str, act: dict[str, Any], reason: str, *, price: float, now: datetime,
                   verdict: DataVerdict, variation: str | None = None, rule_version: str | None = None,
                   extra_features: dict[str, Any] | None = None, extra_reasons: list[str] | None = None) -> bool:
        """Açılmayan geçerli sinyalin karşı-olgusal kaydı (P1: anahtar = barın sinyal kimliği, tur kimliği DEĞİL).
        Etiket türü: mum/Box TARGET_STOP_TIME (kuralın kendi hedef/stop/zaman çıkışı), D4/T2/M2 HORIZON (yaklaşık)."""
        from .learning_cf import HORIZON_BARS, LABEL_HORIZON, LABEL_TARGET_STOP_TIME
        key = _signal_key(act)
        if key is None or self.cf is None:
            return False
        direction = str(act.get("direction") or "LONG").upper()
        try:
            entry, stop = float(price), float(act.get("stop") or 0.0)
        except (TypeError, ValueError):
            return False
        tf = self._decision_tf()
        fam = self.rule.family
        targets: list[float] = []
        if fam == "candle":
            var = act.get("variation") if isinstance(act.get("variation"), dict) else {}
            hold = var.get("max_hold_bars")
            tr = act.get("target_r_from_entry")
            if tr:
                s = 1.0 if direction == "LONG" else -1.0
                targets = [entry + s * float(tr) * abs(entry - stop)]
            # zaman sınırı girişin barı DAHİL H bar; etiketleyici girişten SONRAKİ ilk bardan sayar → H − 1
            kind, horizon = LABEL_TARGET_STOP_TIME, max(1, int(hold) - 1) if hold else 0
        elif fam == "box" and bool(getattr(self.rule_params, "eod_close", True)):
            targets = [float(t) for t in (act.get("targets") or [])]
            kind, horizon = LABEL_TARGET_STOP_TIME, _box_eod_bars(act, now)
        else:
            kind, horizon = LABEL_HORIZON, HORIZON_BARS
        if horizon <= 0:
            return False
        feats: dict[str, Any] = {"strategy": self.name, "setup_type": act.get("setup_type"), "regime": act.get("regime"),
                                 "signal_close": act.get("signal_close"), "atr14": act.get("atr14"),
                                 "lab_algo": act.get("lab_algo"), "structure": act.get("structure"),
                                 "data_source": {"market": verdict.market, "source": verdict.source,
                                                 "tour_id": verdict.tour_id, "bars": dict(verdict.bars)},
                                 "learning": (act.get("_learning") or {}).get("fit") if isinstance(act.get("_learning"), dict) else None}
        if act.get("variation"):
            feats["candle_variation"] = dict(act["variation"])
        if self.symbols:
            feats["in_lab_universe"] = sym in self.symbols
        if extra_features:
            feats.update(extra_features)
        ok = self.cf.record(signal_key=key, symbol=sym, direction=direction, entry=entry, stop=stop, targets=targets,
                            reason=str(reason), created_at=now, tf_minutes=tf_ms(tf) // 60_000, horizon_bars=int(horizon),
                            label_kind=kind, features=feats, variation=variation,
                            rule_version=rule_version or act.get("lab_algo") or act.get("params_label") or self.name,
                            **({"extra_reasons": list(extra_reasons)} if extra_reasons else {}))
        if ok:
            self._count("counterfactual_recorded")
        return ok

    def _learning_after(self, sym: str, act: dict[str, Any] | None, res: str, *, bl: Any, pos_open: bool, pos_obj: Any,
                        verdict: DataVerdict, fr: dict, btc: list, now: datetime, now_ms: int, price: float,
                        params: Any, state: Any = None) -> None:
        """Geçişin öğrenme sonrası: sayaçlar + açılmayan geçerli sinyallerin karşı-olgusal kaydı. Arıza işlemi ETKİLEMEZ."""
        try:
            a = act or {}
            is_open = str(a.get("action") or "").upper() == "OPEN"
            if res == "OPENED":
                p = self.ledger.positions.get(sym)
                lr = (getattr(p, "features", None) or {}).get("learning") or {}
                self._count("opened")
                if lr.get("learning_unlocked_by"):
                    self._count("learning_unlocked")
                if lr.get("size_rule") == SIZE_BUMP:
                    self._count("min_notional_bumped")
                elif lr.get("size_rule") == SIZE_SHRUNK:
                    self._count("shrunk_to_margin")
                if self.cf is not None:
                    # önceki turda reddedilip karşı-olgusala yazılan AYNI sinyal şimdi gerçek işlem: kayıt düşer (çift sayım yok)
                    n_sup = self.cf.supersede(signal_key=_signal_key(a), symbol=sym, direction=str(a.get("direction") or "LONG"),
                                              variation=(a.get("lab_algo") if self.rule.family == "candle" else None))
                    if n_sup:
                        self._count("counterfactual_superseded", n_sup)
            if not getattr(bl, "counterfactual", False) or not verdict.ok or not verdict.entry_ok:
                return
            self._cf_recorder(bl)
            fam_candle = self.rule.family == "candle"
            if res == "REJECTED" and is_open:
                why = str((self.last_actions.get(sym) or {}).get("reason") or "")
                ro = (a.get("_learning") or {}).get("record_only") if isinstance(a.get("_learning"), dict) else None
                if why == LEARNING_RECORD_ONLY and isinstance(ro, dict):
                    # seçicilik-ekstra YALNIZ KAYIT (2026-10-03): neden + ayrılan kodlar + kullanacağı öğrenme boyutu
                    self._count("learning_record_only")
                    self._cf_record(sym, a, why, price=price, now=now, verdict=verdict,
                                    variation=(a.get("lab_algo") if fam_candle else None),
                                    extra_features={RECORD_ONLY_FEATURE: dict(ro)},
                                    extra_reasons=list(ro.get("codes") or []))
                elif counterfactual_ok(why):
                    self._cf_record(sym, a, why, price=price, now=now, verdict=verdict,
                                    variation=(a.get("lab_algo") if fam_candle else None))
            elif not pos_open and not is_open and counterfactual_ok(str(a.get("reason") or "")) \
                    and str(a.get("reason") or "").startswith("STRUCTURE"):
                # yapı kapısı (ENFORCE, gölgeye alınmamış) girişi durdurdu: kuralın KENDİ geometrisi kaydedilir
                base = paper_rules.decide_for(self.name, frames=fr, btc_rows=btc, now_ms=now_ms, position=None, params=params)
                if base and str(base.get("action") or "").upper() == "OPEN":
                    base = dict(base, structure=a.get("structure"))       # engelleyen yapı kararı özelliklerde kalır
                    self._cf_record(sym, base, str(a.get("reason")), price=price, now=now, verdict=verdict,
                                    variation=(base.get("lab_algo") if fam_candle else None))
            if fam_candle:
                self._cf_candle(sym, a, is_open=is_open, pos_open=pos_open, pos_obj=pos_obj, verdict=verdict, fr=fr,
                                now=now, now_ms=now_ms, price=price, params=params)
            elif pos_open and self.rule.family in ("box", "donchian"):
                self._cf_held(sym, pos_obj, verdict=verdict, fr=fr, btc=btc, now=now, now_ms=now_ms, price=price,
                              state=state)
        except Exception as exc:  # noqa: BLE001 — karşı-olgusal kaydı işlem yolunu ASLA durdurmaz
            log.warning("öğrenme sonrası adım başarısız (%s %s): %s", self.key, sym, exc)

    def _cf_held(self, sym: str, pos_obj: Any, *, verdict: DataVerdict, fr: dict, btc: list, now: datetime, now_ms: int,
                 price: float, state: Any = None) -> bool:
        """SPEC A15 "+CF" (2026-09-28, öğrenme modu; ikinci doğrulama turu): sembolde pozisyon AÇIKKEN TABAN kuralın (kendi
        parametreleriyle — Box'ta taban `min_stop_pct`) TAZE giriş sinyali POSITION_OPEN karşı-olgusalı olur: tabanın
        alacağı sinyal, öğrenme defterinde daha önce açılmış (ör. dar stoplu) bir işlem sembolü tuttuğu için kaybolmasın.
        Öğrenme parametresiyle (Box 0,32) doğan dar stoplu sinyaller açık sembolde YAZILMAZ: 24 saatlik uçtan uca koşuda
        Box'ta günde ~511 kayıt ediyordu (tabanınki ~47) — aynı hareketin ardışık barları, dosya/bellek yükü. Yalnız OLAY
        tabanlı aileler (Box, D4); mum ailesi `_cf_candle`da. Trend ailesinin girişi bir DURUM koşuludur (pozisyon boyunca
        her bar doğru) → tutulan işlemin günlük tekrarı olurdu, yazılmaz. Açık pozisyonun KENDİ (ya da daha eski) sinyali
        kaydedilmez. Öğrenme parametresi (`_learning_params`) bilerek KULLANILMAZ.

        Tek hareket tek kayıt (2026-09-28, üçüncü doğrulama turu): tutulan pozisyon öğrenme-ekstra DEĞİLSE (politika ya da
        taban/askıda açılmış) taban da aynı pozisyonu tutuyordur → taze sinyali ALMAZDI, kayıt yok. Bu sembolde sonuçlanmamış
        ve taban kapılarından GEÇMİŞ bir POSITION_OPEN kaydı varsa tabanın varsayımsal pozisyonu hâlâ açıktır → yeni sinyal
        kaydedilmez (eskiden aynı hareketin ardışık sinyalleri aynı stop/hedef/etiket anıyla 8 kez sayılıyordu). Taban
        kapıları (`_cf_held_gate`: toplam açık risk, adet, marj) bu sinyali durduracaksa kayıt yine yazılır ama
        `baseline_blocked_by` taşır ve varsayımsal pozisyon SAYILMAZ — tabanın sonraki gerçek girişi bastırılmaz; aynı sembolde
        sonuçlanmamış kayıt varken kapıya takılan yeni sinyal ise yazılmaz (aynı hareketin tekrarı)."""
        held = (getattr(pos_obj, "meta", None) or {}).get("learning")
        if not (isinstance(held, dict) and held.get("learning_unlocked_by")):
            return False
        base = paper_rules.decide_for(self.name, frames=fr, btc_rows=btc, now_ms=now_ms, position=None,
                                      params=self.rule_params)
        if not base or str(base.get("action") or "").upper() != "OPEN":
            return False
        opened = parse_ts_ms(getattr(pos_obj, "opened_at", None))
        sc = base.get("signal_close_ms")
        if sc is None and base.get("signal_ts") is not None:
            sc = int(base["signal_ts"]) + tf_ms(self._decision_tf())
        if opened is None or sc is None or int(opened) >= int(sc):
            return False                                  # pozisyon bu (ya da daha eski) sinyalden açıldı
        if self.cf is not None and self.cf.has_pending(symbol=sym, reason="POSITION_OPEN", hypothetical_only=True):
            return False                                  # tabanın varsayımsal pozisyonu (önceki kayıt) sonuçlanmadı
        extra = None
        if state is not None:
            codes = self._cf_held_gate(sym, base, state=state, now=now, price=price)
            if codes and self.cf is not None and self.cf.has_pending(symbol=sym, reason="POSITION_OPEN"):
                return False                              # aynı hareketin taban kapısına takılan sinyali zaten kayıtlı
            extra = {"baseline_blocked_by": codes}
        return self._cf_record(sym, base, "POSITION_OPEN", price=price, now=now, verdict=verdict, extra_features=extra)

    def _cf_held_gate(self, sym: str, act: dict[str, Any], *, state: Any, now: datetime, price: float) -> list[str]:
        """Taban bu taze sinyali AÇAR MIYDI? (2026-09-28, öğrenme modu; üçüncü doğrulama turu) — boş liste: açardı.

        Sanal taban portföyü = taban görünümü (öğrenme-ekstra yok, politika pozisyonları taban boyutunda) + bu defterin
        sonuçlanmamış, kapıdan geçmiş POSITION_OPEN kayıtları (tabanın varsayımsal pozisyonları) taban boyutunda. Taban profilli
        RiskEngine (AYNI kill switch), defter adet tavanı ve serbest marj. YAKLAŞIK: gerçekleşen P&L farkı ve tabanın başka
        nedenle açıp öğrenme defterinin hiç görmediği işlemler bilinmez. Arıza → ['BASELINE_UNKNOWN']."""
        try:
            from dataclasses import replace as _replace

            from .risk.state import OpenPosition
            view, _dm = baseline_view(state, learning_tags(self.ledger.positions))
            base = RiskEngine(self.profile, getattr(self.risk, "ks", None), getattr(self.risk, "clusters", None))
            held = {o.symbol for o in view.open_positions}
            hyp, m_h = [], 0.0
            for t in list(self.cf.sb.trades if self.cf is not None else []):
                if (t.outcome is not None or t.symbol == sym or t.symbol in held
                        or list(t.reason_not_opened or [])[:1] != ["POSITION_OPEN"]
                        or (t.features or {}).get("baseline_blocked_by")):
                    continue
                n_h, lev_h, _ = _baseline_size(act, entry=float(t.entry), stop=float(t.stop), profile=self.profile,
                                               state=view, risk=base)
                lev_h = max(1, int(lev_h))
                hyp.append(OpenPosition(symbol=t.symbol, market_type="USDM_PERP", side=str(t.direction), notional=n_h,
                                        margin=n_h / lev_h, risk_usdt=abs(float(t.entry) - float(t.stop)) / float(t.entry) * n_h,
                                        entry=float(t.entry), stop=float(t.stop), leverage=float(lev_h)))
                held.add(t.symbol)
                m_h += n_h / lev_h
            if hyp:
                view = _replace(view, open_positions=list(view.open_positions) + hyp,
                                used_margin=float(view.used_margin) + m_h, available=float(view.available) - m_h)
            stop = float(act.get("stop") or 0.0)
            n, lev, _ = _baseline_size(act, entry=float(price), stop=stop, profile=self.profile, state=view, risk=base)
            plan = {"symbol": sym, "market_type": "USDM_PERP", "direction": str(act.get("direction") or "LONG").upper(),
                    "entry": float(price), "stop": stop, "targets": list(act.get("targets") or []), "notional": n,
                    "margin": n / max(1, lev), "leverage": lev, "amount_type": "NOTIONAL",
                    "expected_r": float(act.get("expected_r") or 0.0), "min_notional": 5.0}
            rd = base.evaluate(plan, view, {"now_utc": now})
            if not rd.allowed:
                return [str(r) for r in (rd.reasons or ["RISK_DENIED"])]
            if self.ledger.enforce_position_cap and len(view.open_positions) >= int(self.ledger.max_positions):
                return ["MAX_POSITIONS"]
            n2, lev2 = float(rd.adjusted_notional or n), max(1, int(rd.adjusted_leverage or lev))
            if n2 / lev2 > float(view.available):
                return ["INSUFFICIENT_MARGIN"]
            return []
        except Exception as exc:  # noqa: BLE001 — etiket hesaplanamazsa kayıt yine yazılır (varsayımsal sayılmaz)
            log.warning("A15 taban kapısı hesaplanamadı (%s %s): %s", self.key, sym, exc)
            return ["BASELINE_UNKNOWN"]

    def _cf_candle(self, sym: str, act: dict[str, Any], *, is_open: bool, pos_open: bool, pos_obj: Any,
                   verdict: DataVerdict, fr: dict, now: datetime, now_ms: int, price: float, params: Any) -> None:
        """C4 (C10): `also_matched` varyasyonları ve açık pozisyon yüzünden girilemeyen varyasyonlar — her biri kendi
        stop/hedef R/en uzun tutma süresiyle karşı-olgusal kayıt (defter bölünmez)."""
        also = [str(v) for v in (act.get("also_matched") or [])] if (is_open and not pos_open) else []
        if not pos_open and not also:
            return
        hits = paper_rules.candle_hits_for(self.name, frames=fr, now_ms=now_ms, params=params)
        opened = parse_ts_ms(getattr(pos_obj, "opened_at", None)) if pos_open else None
        for h in hits:
            if pos_open:
                if opened is not None and opened >= int(h["signal_close_ms"]):
                    continue                              # açık pozisyon BU sinyalden açıldı (eşleşenleri o an kaydedildi)
                reason = "POSITION_OPEN"
            elif h["id"] in also:
                lo, hi = h["risk_atr_bounds"]
                dist = abs(float(price) - float(h["stop"]))
                reason = "ALSO_MATCHED" if (h["atr14"] > 0 and lo * h["atr14"] < dist <= hi * h["atr14"]) \
                    else "RISK_OUTSIDE_TESTED_RANGE"
            else:
                continue
            hact = {"direction": h["direction"], "stop": h["stop"], "signal_ts": h["signal_ts"],
                    "signal_close_ms": h["signal_close_ms"], "signal_close": h["signal_close"], "atr14": h["atr14"],
                    "setup_type": h["setup_type"], "lab_algo": h["id"], "target_r_from_entry": h["target_r"],
                    "variation": {"id": h["id"], "definition_sha": h["definition_sha"], "target_r": h["target_r"],
                                  "max_hold_bars": h["max_hold_bars"], "observation": h["observation"]}}
            self._cf_record(sym, hact, reason, price=price, now=now, verdict=verdict, variation=h["id"],
                            rule_version=h["definition_sha"])

    def _learning_label(self, frames_by_symbol: dict[str, dict], now: datetime) -> None:
        try:
            # NET ETİKET (2026-09-29, maliyet sapması): defterin KENDİ yürütme modeli (ücret/kayma/TP1/başa-baş/funding),
            # filtre önbelleği ve gerçekleşmiş funding kaynağı; eski (brüt) etiketler tembel doldurulur
            from .learning_cf import ExecModel
            # `real_history` (2026-10-01, cf_aux_v1, yalnız kayıt): aşma tahmini bu defterin gerçek stop çıkışlarından
            n = self.cf.label_pending(frames_by_symbol or {}, now, exec_model=ExecModel.of_ledger(self.ledger),
                                      filters_for=lambda s: self.filters_cache.get(s, MarketType.USDM_PERP),
                                      funding_lookup=self.funding_rates, real_history=lambda: self.ledger.history)
            if n:
                self._count("counterfactual_labeled", n)
            self.cf.sync_book_counters(self.learning_counters)
            self.cf.save()
        except Exception as exc:  # noqa: BLE001 — etiketleme arızası defteri ETKİLEMEZ
            log.warning("karşı-olgusal etiketleme/kayıt başarısız (%s): %s", self.key, exc)

    def _learning_doc(self) -> dict[str, Any]:
        doc = dict(self.learning or {})
        doc["counters"] = dict(self.learning_counters)
        doc["counterfactual"] = self.cf.stats() if self.cf is not None else None
        rl = self.risk_learning
        doc["risk_profile"] = ({"name": rl.profile.name, "max_total_open_risk_pct": rl.profile.max_total_open_risk_pct,
                                "risk_per_trade_cap_pct": rl.profile.risk_per_trade_pct} if rl is not None else None)
        return doc

    def _bar_binding(self, frames: dict | None, verdict: DataVerdict, now_ms: int) -> tuple[str | None, int | None]:
        """Kuralın BU anda okuyacağı son kapanmış bar, kayda giren barla her dilimde eşleşiyor mu.

        Döner: (uyuşmayan dilim | None, kuralın okuduğu son açılış ms | None). Okuma `paper_rules` üzerinden,
        yani `decide`ın kullandığı YOLUN AYNISI — ikinci bir okuma yolu bilerek yoktur.
        """
        for tf in self.rule.timeframes:
            rows = (paper_rules.daily_rows(frames, now_ms=now_ms) if tf == "1d"
                    else paper_rules.intraday_rows(frames, tf=tf, now_ms=now_ms))
            last = int(rows[-1].get("timestamp")) if rows and rows[-1].get("timestamp") is not None else None
            if last is None or last != int(verdict.bars.get(tf) or -2):
                return tf, last
        return None, None

    def tick(self, marks: dict[str, TickData], *, now: datetime, funding_rate_lookup=None, bar_advance: bool,
             expect: dict[str, str] | None = None, source: str = "tour", apply_clock=None) -> list:
        """CANLI FİYAT KONTROLÜ: defterin stop/hedef/funding/likidasyon kontrolü; ana defterle AYNI çağrı biçimi.
        `marks` yalnız doğrulanmış, güncel perp mark taşır (bar ucu YOK; uçlar `apply_closed_bars` ile ayrı sözleşmede).

        KORUYUCU İZLEYİCİ (2026-09-24): tur, Box zamanlayıcısı ve izleyici bu defteri ayrı iş parçacıklarından tick'ler.
        Tick `protective_monitor.guarded_tick` ile yapılır: `expect` verilirse fiyat alınmadan önceki pozisyon kimliği
        doğrulanır; pozisyona uygulanmış fiyattan daha ESKİ fiyat uygulanmaz; tazelik uygulama anında (`apply_clock`;
        verilmezse `now`) denetlenir. Kesintiden sonraki ilk gözlem kayda geçer."""
        from .protective_monitor import guarded_tick
        with self.lock:
            self._resume_once(now)
            recs, info = guarded_tick(self.ledger, marks, now=now, funding_rate_lookup=funding_rate_lookup,
                                      bar_advance=bar_advance, expect=expect, apply_clock=apply_clock)
            for rec in recs:
                self._on_closed(rec)
            self._after_tick(info, recs, now, source)
            return recs

    def _after_tick(self, info: dict[str, Any], recs: list, now: datetime, source: str) -> None:
        """Ölçüm + kesinti sonrası ilk gözlem (çağıran kilidi tutar)."""
        applied = info.get("applied") or {}
        if not applied:
            return
        note_ids = [str(getattr(r, "id", "")) for r in recs]
        if self.monitoring_gap:
            from .protective_monitor import note_gap_first_observations
            note_gap_first_observations(self.monitoring_gap, book_key=self.key, state_path=self.cfg.state_path,
                                        applied=applied, now=now, closed_ids=note_ids)
        if self.observer is not None:
            opened = {s: parse_ts_ms(p.opened_at) or 0 for s, p in self.ledger.positions.items()}
            self.observer.note(self.key, applied, int(now.timestamp() * 1000), opened_ms=opened, source=source)

    def held_ids(self) -> dict[str, str]:
        """Açık pozisyonların kimlik anlık görüntüsü (kısa kilit) — izleyici fiyatı bunun İÇİN alır ve kilit altında doğrular."""
        with self.lock:
            return {s: str(p.id) for s, p in self.ledger.positions.items()}

    def protect(self, marks: dict[str, TickData], marks_f: dict[str, float], gaps: dict[str, dict] | None, *, now: datetime,
                expect: dict[str, str] | None = None, source: str = "monitor", funding_rate_lookup: Any = "__book__",
                apply_clock=None) -> list:
        """KORUYUCU İZLEME ADIMI (tek kısa atomik bölüm; AĞ YOK): fiyat boşlukları → korumalı tick → kayıt.
        Funding kaynağı yalnız bellekten okunur (ağ adımı turdadır)."""
        frl = self.funding_rates if funding_rate_lookup == "__book__" else funding_rate_lookup
        with self.lock:
            self._resume_once(now)
            self.record_gaps(gaps or {}, now)
            recs = self.tick(marks, now=now, funding_rate_lookup=frl, bar_advance=False, expect=expect, source=source,
                             apply_clock=apply_clock) if marks else []
            self.save(marks_f, now)
            return recs

    def apply_closed_bars(self, bars_by_symbol: dict[str, dict[str, Any]] | None, *, now: datetime, funding_rate_lookup=None) -> list:
        """GEÇMİŞ OHLC BARI İŞLEME (2026-09-16): kapanmış USDM_PERP barlarının uçlarını (stop/hedef/likidasyon/MFE/MAE)
        açık pozisyona uygular — canlı fiyat izlemesinden AYRI sözleşme.

        Kural: bir bar yalnız (1) `now` anında kapanmışsa (açılış + dilim <= now), (2) pozisyon açılışından SONRA açılmışsa
        (açılış >= opened_at; girişi içeren bar dahil değildir — o aralığı canlı fiyat kontrolü kapsar; replay `_advance`
        ile aynı sınır), (3) daha önce UYGULANMAMIŞSA (pozisyon `meta.ohlc_cursor[tf]` imleci kalıcıdır) uygulanır;
        kronolojik sırada, bar başına bir defter tick'i, tick zamanı = barın kapanışı (kayıt zamanı gerçek olay zamanıdır).

        Uygulanamayan bar (2026-09-17): gerekçesi AYRIDIR — `BAR_CORRUPT` (bütünlük/uç büyüklüğü), `BAR_SCALE_UNVERIFIED`
        (kapanış canlı mark ölçeğinden uzak), `BAR_MARKET_MISMATCH` (çerçeve başka piyasadan). Hiçbiri uydurulmaz,
        hiçbiri TÜKETİLMİŞ sayılmaz (`meta.ohlc_gaps` ile görünür kalır ve veri düzelince yeniden denenir) ve hiçbiri
        SONRAKİ geçerli barı engellemez. Tam sözleşme: `apply_closed_bars_to_ledger`.
        `bars_by_symbol[sym] = {"tf": "1h", "rows": [...], "mark": canlı mark, "market": çerçevenin piyasası}`."""
        with self.lock:
            self._resume_once(now)
            return apply_closed_bars_to_ledger(self.ledger, bars_by_symbol, now=now, funding_rate_lookup=funding_rate_lookup,
                                               on_closed=self._on_closed, on_event=self._data_event,
                                               gap_until_ms=self._gap_until_ms)

    def _resume_once(self, now: datetime) -> None:
        """İZLEME KESİNTİSİ: bu süreçteki İLK defter etkinliğinde (adım, fiyat tiki, bar, fiyat boşluğu, funding ya da
        kayıt — hangisi önce gelirse) BİR KEZ denetlenir. Defter uzun süre izlenmediyse kesinti YÜKLEME anındaki
        pozisyonlarla kaydedilir (ilk adım onları kapatsa bile) ve kesintinin bittiği ana kadar kapanmış barlar bu süreç
        boyunca uygulanmaz. Çağıran `self.lock`u tutar."""
        if self._resume_checked:
            return
        self._resume_checked = True
        self._gap_until_ms = monitoring_gap_on_resume(self.ledger, self._resume_saved_at, now=now, book_key=self.key,
                                                      state_path=self.cfg.state_path, positions=self._resume_positions)
        if self._gap_until_ms is not None:
            self.monitoring_gap = {"from": str(self._resume_saved_at), "to": iso(now), "positions": list(self._resume_positions)}


    def save(self, marks_f: dict[str, float], now: datetime) -> None:
        from .structures.catalog import POLICY_VERSION as _ST_POLICY
        with self.lock:
            self._resume_once(now)
            self.ledger.save(self.ledger_path)
            fs = self.ledger.summary(marks_f)
            # `generated_at` = bu YAZMA ani. `rule_evaluated_at` = kural dongusunun son kostugu an.
            # Ikisi AYRI: 60 sn izleyicisi yazar ama kural kosturmaz. `rule_stale_s` farki verir;
            # tur araligi ~20 dk oldugu icin birkac bin saniyeyi asan deger ARIZADIR.
            _stale = None
            if self.rule_evaluated_at:
                try:
                    _stale = round((now - datetime.fromisoformat(self.rule_evaluated_at)).total_seconds(), 1)
                except (TypeError, ValueError):
                    _stale = None
            doc = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now), "run_id": self.run_id,
                   "rule_evaluated_at": self.rule_evaluated_at, "rule_tour": int(self.rule_tour),
                   "rule_stale_s": _stale, "rule_evaluated_this_write": bool(self._rule_ran_since_save),
                   "name": self.name, "atr_mult": self.atr_mult, "regime": self.regime,
                   # V15: defterin kural kimligi ve kurala ozel ayarlari — panel bunlarla AYNI kural
                   # durumunu yeniden uretir (ikinci varsayilan tutmaz).
                   "rule_family": self.rule.family, "rule_params": dict(self.spec.rule_params or {}),
                   "starting_equity": float(self.ledger.starting_equity),
                   "summary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in fs.items()},
                   "positions": {s: {"side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty),
                                     "stop": float(p.stop) if p.stop else None, "leverage": p.leverage,
                                     "opened_at": p.opened_at, "last_price": float(p.last_price) if p.last_price else None}
                                 for s, p in self.ledger.positions.items()},
                   "history_tail": self.ledger.history_dicts()[-50:],
                   # ORTAK YAPI: sembol başına SON yapı kararı (neden girdi/girmedi/çıktı) + mod + politika sürümü
                   "structures": {"mode": self.structure_mode, "policy_version": _ST_POLICY,
                                  "decisions": dict(self.structure_decisions)},
                   # FUNDING (2026-09-22): kaynak ve bekleyen dönemler — mutabık olmayan funding AYRI durum olarak görünür
                   "funding": {"contract": FUNDING_SETTLEMENT_CONTRACT,
                               "source": "settlement_source" if getattr(self, "funding_rates", None) is not None else "none",
                               "pending_positions": {s: dict(p.features.get("funding_pending") or {})
                                                     for s, p in self.ledger.positions.items() if p.features.get("funding_pending")},
                               "incomplete_closed": [h.id for h in self.ledger.history[-200:]
                                                     if ((h.features or {}).get("funding_coverage") or {}).get("complete") is False]},
                   "last_actions": self.last_actions, "counters": dict(self.counters),
                   "rejections": dict(self.rejections), "closed_recent": self.closed_recent[-20:],
                   # İZLEME KESİNTİSİ (2026-09-23): bu süreçte algılanan kesinti (yoksa None); tam kayıt MONITORING_GAPS_FILE
                   "monitoring_gap": self.monitoring_gap,
                   # VERİ KAYNAĞI (2026-09-16): bu turun sembol hükümleri, fiyat boşlukları, son olaylar (yeniden başlatmada korunur)
                   "data_checks": dict(self.data_checks), "data_gaps": dict(self.data_gaps), "data_events_recent": self.data_events[-30:],
                   "data_policy": {"market": PAPER_MARKET, "rule_timeframes": list(self.rule.timeframes), "price_source": "usdm_perp_mark",
                                   # V15: gün içi dilim varsa kuralın GERÇEK örnekleme temposu ilan edilir.
                                   "intraday": ({"tf": [t for t in self.rule.timeframes if t != "1d"][0],
                                                 "tour_interval_min": PRODUCTION_TOUR_INTERVAL_MIN,
                                                 "bars_seen_per_tour": 1,
                                                 "note": "tur %d dk; 5m barlarinin 1/%d'i degerlendirilir — kacirilan tetikler ARASTIRMADA olculur"
                                                         % (PRODUCTION_TOUR_INTERVAL_MIN, max(1, PRODUCTION_TOUR_INTERVAL_MIN // 5))}
                                                if any(t != "1d" for t in self.rule.timeframes) else None),
                                   # ZAMAN SÖZLEŞMELERİ (2026-09-16): canlı fiyat / bar uçları / günlük sinyal güncelliği
                                   "price": {"max_age_s": PRICE_MAX_AGE_S, "future_skew_s": PRICE_FUTURE_SKEW_S,
                                             "time": "funding.ts (borsa mark zamanı) yoksa snapshot.ts (alınma); kontrol anı ayrı"},
                                   "bars": {"tf": BAR_TIMEFRAME, "rule": "kapanmış, pozisyon açılışından sonra açılmış, bir kez (meta.ohlc_cursor)"},
                                   "freshness": {"tolerance_ms": dict(BAR_LAG_TOLERANCE_MS), "future_skew_ms": BAR_FUTURE_SKEW_MS,
                                                 "rule": "as_of anında kapanmış son bar >= beklenen son kapanış (as_of - tolerans)"},
                                   "note_tr": "Yeni giriş yalnız bu turun doğrulanmış ve GÜNCEL USDⓈ-M perpetual çerçevesi (+ BTC referansı) ile; "
                                              "kural kapanışı sembol çerçevesine bağlı; stop/hedef takibi güncel doğrulanmış perp mark fiyatıyla."},
                   "note_tr": "KÂĞIT İLERİ TEST — gerçek para yok. Ana botun defterinden bağımsız."}
            if self.learning is not None:
                # ÖĞRENME MODU (2026-09-28): durum, sayaçlar, karşı-olgusal istatistikleri — anahtar hiç açılmadıysa alan YOK
                doc["learning"] = self._learning_doc()
            doc["key"] = self.key
            atomic_write_json(Path(self.cfg.state_path) / self.summary_file, doc)
            # Bayrak TUKETILIR: bir sonraki yazma (or. 60 sn'lik cikis izleyicisi) kural gecisi
            # olmadan gelirse `rule_evaluated_this_write` FALSE olur ve defter "canli" gorunemez.
            self._rule_ran_since_save = False


#: Pozisyon başına saklanan doğrulanmış bar kapanışı sayısı (süreklilik referansı; yeniden başlatmada `meta` ile korunur).
VERIFIED_BARS_KEEP = 64


def _scale_anchor(pos: Any, tf: str, bar_open_ms: int) -> tuple[float, str]:
    """Süreklilik referansı: bu pozisyonda bu dilimde DOĞRULANMIŞ, `bar_open_ms`ten önce açılmış en yakın barın
    kapanışı; yoksa giriş fiyatı (girişte doğrulanmış perp mark ile açılmıştır). Döner: (fiyat, kaynak)."""
    rows = ((pos.meta.get("ohlc_verified") or {}).get(tf) or []) if isinstance(pos.meta.get("ohlc_verified"), dict) else []
    earlier = [r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 2 and int(r[0]) < int(bar_open_ms)]
    if earlier:
        b, c = max(earlier, key=lambda r: int(r[0]))[:2]
        return float(c), "VERIFIED_BAR:%d" % int(b)
    try:
        return float(pos.entry_avg), "ENTRY"
    except (TypeError, ValueError):
        return 0.0, "NONE"


#: TP1 kısmi dolumunun `Fill.kind`ı (`futures_ledger.EXIT_TP1`): defter bu dolumda stopu başa-başa taşır.
_TP1_FILL_KIND = "hedef1"
#: Stop taşındıktan sonra uygulanan, taşımadan ÖNCE açılmış barın atlama gerekçesi (2026-09-29, canlı muhasebe düzeltmesi):
#: yalnız bar içi sıra BELİRSİZ olduğunda (ters uç yeni stopun ötesinde, eski stopun berisinde) kullanılır.
BAR_OPENED_BEFORE_STOP_MOVE = "OPENED_BEFORE_STOP_MOVE"
#: Taşımadan ÖNCE açılmış barın, o barda GEÇERLİ olan stopla uygulandığını bildiren olay (2026-09-29, inceleme bulgusu).
BAR_PRE_MOVE_STOP = "BAR_PRE_MOVE_STOP"
BAR_PRE_MOVE_APPLIED = "APPLIED_AGAINST_STOPS_IN_FORCE"


def _stop_moved_ms(pos: Any) -> int | None:
    """STOPUN SON TAŞINDIĞI AN (2026-09-29, canlı muhasebe düzeltmesi): MFE başa-başı (`meta.be_by_mfe.at`) ya da TP1 kısmi
    dolumu (defter stopu o dolumda başa-başa çeker; `Fill.ts`). İkisi de defter tick'inin kendi zamanıyla yazılır. Yoksa
    None. (Ortak yapı sıkılaştırması `meta.structure_stop.at` ana motorda `_main_closed_bars` içinde ayrıca süzülür.)"""
    meta = pos.meta if isinstance(getattr(pos, "meta", None), dict) else {}
    out: list[int | None] = []
    be = meta.get("be_by_mfe")
    if isinstance(be, dict):
        out.append(parse_ts_ms(be.get("at")))
    if bool(getattr(pos, "tp1_done", False)):
        out += [parse_ts_ms(getattr(f, "ts", None)) for f in (getattr(pos, "fills", None) or [])
                if getattr(f, "kind", None) == _TP1_FILL_KIND]
    vals = [x for x in out if x is not None]
    return max(vals) if vals else None


def _dec_or_none(x: Any) -> Decimal | None:
    try:
        d = Decimal(str(x)) if x is not None else None
    except (ArithmeticError, ValueError):
        return None
    return d if d is not None and d.is_finite() and d > 0 else None


def _stops_in_bar(pos: Any, bar_open_ms: int, bar_close_ms: int) -> tuple[Decimal | None, Decimal | None]:
    """BAR BOYUNCA GEÇERLİ STOP ARALIĞI (2026-09-29, inceleme bulgusu): (barın açılışında geçerli stop = barın EN GEVŞEK
    stopu, barın kapanışından önce yapılmış son taşımadan sonraki stop = barın EN SIKI stopu). Kaynak pozisyonun kendi
    kayıtlarıdır: ilk stop (`initial_stop`), ortak yapı sıkılaştırması (`meta.structure_stop` at/to), MFE başa-baş
    (`meta.be_by_mfe` at/stop), TP1 dolumu (`Fill.ts`; taşıdığı stop kaydedilmez → SON taşımaysa güncel stop, değilse
    gevşek sınırda bir önceki stop, sıkı sınırda güncel stop — iki sınır da güvenli yöndedir). Stoplar yalnız sıkılaşır.
    İlk stop okunamazsa (None, None)."""
    meta = pos.meta if isinstance(getattr(pos, "meta", None), dict) else {}
    first = _dec_or_none(getattr(pos, "initial_stop", None))
    cur = _dec_or_none(getattr(pos, "stop", None))
    if first is None or cur is None:
        return None, None
    moves: list[tuple[int, Decimal | None]] = []
    for key, val_key in (("structure_stop", "to"), ("be_by_mfe", "stop")):
        m = meta.get(key)
        if isinstance(m, dict):
            t = parse_ts_ms(m.get("at"))
            if t is not None:
                moves.append((t, _dec_or_none(m.get(val_key))))
    if bool(getattr(pos, "tp1_done", False)):
        for f in getattr(pos, "fills", None) or []:
            if getattr(f, "kind", None) == _TP1_FILL_KIND:
                t = parse_ts_ms(getattr(f, "ts", None))
                if t is not None:
                    moves.append((t, None))
    moves.sort(key=lambda x: x[0])
    if moves and moves[-1][1] is None:
        moves[-1] = (moves[-1][0], cur)              # son taşıma TP1 ise taşıdığı stop güncel stoptur
    loose, tight = first, first
    for t, val in moves:
        if t <= bar_open_ms and val is not None:
            loose = val
        if t < bar_close_ms:
            tight = val if val is not None else cur
    return loose, tight


def _tighter(long: bool, a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return a if b is None else b
    return max(a, b) if long else min(a, b)


class _PreMoveView:
    """TAŞIMADAN ÖNCE AÇILMIŞ BARIN TİKİ (2026-09-29, inceleme bulgusu): tick süresince pozisyon, barın açılışındaki
    stop durumuyla görünür — stop = o anda geçerli stop; MFE başa-başı o anda HENÜZ yoksa (`be_by_mfe.at` > bar açılışı)
    işaret geçici olarak boş görünür ve defterin MFE başa-baş eşiği geçici olarak 0 olur (bar kendi yükseğiyle ikinci bir
    taşıma UYDURMAZ; çıkış etiketi o anki duruma göre "stop" olur). Çıkışta (istisnada da) hepsi geri konur; pozisyon açık
    kalırsa stop, tick öncesi stop ile tick'in (ör. TP1 dolumunun) bıraktığı stopun SIKI olanıdır. Çağıran defter kilidini
    tutar (koruyucu izleyici aynı kilitle bekler)."""

    def __init__(self, ledger: FuturesLedgerV2, pos: Any, stop: Decimal, *, hide_be: bool) -> None:
        self.ledger, self.pos, self.stop, self.hide_be = ledger, pos, stop, hide_be
        self.long = str(getattr(getattr(pos, "side", None), "value", "LONG")).upper() == "LONG"

    def __enter__(self) -> "_PreMoveView":
        self._stop0 = self.pos.stop
        self._thr0 = self.ledger.breakeven_at_mfe_r
        self._be0 = self.pos.meta.get("be_by_mfe") if self.hide_be else None
        self.pos.stop = self.stop
        if self._be0 is not None:
            self.pos.meta["be_by_mfe"] = {}              # anahtar sırası korunur; boş = o anda başa-baş yoktu
            self.ledger.breakeven_at_mfe_r = Decimal("0")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ledger.breakeven_at_mfe_r = self._thr0
        if self._be0 is not None and self.pos.meta.get("be_by_mfe") == {}:
            self.pos.meta["be_by_mfe"] = self._be0
        self.pos.stop = _tighter(self.long, self._stop0, self.pos.stop)


def _remember_verified(pos: Any, tf: str, bar_open_ms: int, close: float, route: str) -> None:
    ver = pos.meta.setdefault("ohlc_verified", {})
    rows = [r for r in (ver.get(tf) or []) if isinstance(r, (list, tuple)) and int(r[0]) != int(bar_open_ms)]
    rows.append([int(bar_open_ms), float(close), str(route)])
    ver[tf] = sorted(rows, key=lambda r: int(r[0]))[-VERIFIED_BARS_KEEP:]


def _sync_path_flag(pos: Any) -> None:
    """Çözülmemiş bar boşluklarını pozisyonun `features`ına yansıtır: pozisyon HANGİ yoldan kapanırsa kapansın
    (bar, canlı fiyat, kural, manuel) kayıt `features["path_unverified"]`i taşır — fiyat yolu tam doğrulanmamış bir
    kapanış, eksiksiz doğrulanmış sonuç gibi raporlanmaz. Boşluk kalmayınca alan silinir."""
    gaps = pos.meta.get("ohlc_gaps") if isinstance(pos.meta.get("ohlc_gaps"), dict) else {}
    bars = []
    long = str(getattr(getattr(pos, "side", None), "value", "LONG")).upper() == "LONG"
    stop = float(pos.stop) if getattr(pos, "stop", None) is not None else None
    for tf, rows in sorted(gaps.items()):
        for g in rows or []:
            if not isinstance(g, dict):
                continue
            ext = g.get("low") if long else g.get("high")
            crossed = None
            if stop is not None and isinstance(ext, (int, float)) and math.isfinite(ext):
                crossed = bool(ext <= stop) if long else bool(ext >= stop)
            bars.append({"tf": tf, "bar_open_ms": g.get("bar_open_ms"), "reason": g.get("reason"),
                         "open": g.get("open"), "high": g.get("high"), "low": g.get("low"), "close": g.get("close"),
                         "stop_at_sync": stop, "stop_crossed_in_bar": crossed})
    if bars:
        pos.features["path_unverified"] = {"unresolved_bars": len(bars), "bars": bars[-10:],
                                           "any_stop_crossed": any(b["stop_crossed_in_bar"] for b in bars),
                                           "note": "fiyat yolunda uygulanamamış bar var; sonuç eksiksiz doğrulanmış DEĞİL"}
    else:
        pos.features.pop("path_unverified", None)


#: İZLEME KESİNTİSİ (2026-09-23): defterin son kaydından (`ledger.updated_at`, her tur + 60 sn izleyici yazar) bu kadar
#: süre geçtikten sonra ilk bar uygulamasında, aradaki sürede kapanmış barlar UYGULANMAZ: kesinti açıkça kaydedilir
#: (`MONITORING_GAPS_FILE`) ve geçmiş boşluk bar uçlarından TAHMİNİ işlemlerle doldurulmaz. Olağan tur+bekleme döngüsü
#: ~40 dk'dır (tur ~24 dk + 15 dk); 2 saat olağan işletimi kesinti saymaz.
MONITORING_GAP_S = 7200.0
MONITORING_GAPS_FILE = "monitoring_gaps.jsonl"


def record_monitoring_gap(state_path: Path | str, rec: dict[str, Any]) -> None:
    """Kesinti kaydını ekler (salt ekleme, arıza sessiz geçmez ama çağıranı durdurmaz)."""
    try:
        p = Path(state_path) / MONITORING_GAPS_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        log.warning("izleme kesintisi kaydı yazılamadı: %s (%s)", exc, rec)


def monitoring_gap_on_resume(ledger: FuturesLedgerV2, last_saved: Any, *, now: datetime, book_key: str,
                             state_path: Path | str, positions: Any = None) -> int | None:
    """Süreçteki İLK defter etkinliğinde izleme kesintisi var mı? Varsa kaydeder ve kesintinin bittiği anı (ms) döner;
    o ana kadar kapanmış barlar uygulanmaz. Son kayıt okunamıyorsa kesinti YOK sayılır.

    `positions`: defter YÜKLENİRKEN açık olan pozisyonlar — kesinti boyunca izlenmeyenler bunlardır. İlk adım onları
    kapatmış olsa bile kayıt bu listeyle yazılır (verilmezse o anki pozisyonlar). Açık pozisyon yoksa da kesinti
    KAYDEDİLİR: kural o aralıkta hiç değerlendirilmedi (ileri test verisindeki boşluk görünür kalır)."""
    held = sorted(ledger.positions) if positions is None else sorted(positions)
    if not last_saved:
        return None
    try:
        last = datetime.fromisoformat(str(last_saved))
    except (TypeError, ValueError):
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    gap_s = (now - last).total_seconds()
    if gap_s <= MONITORING_GAP_S:
        return None
    rec = {"kind": "MONITORING_GAP", "book": book_key, "from": iso(last), "to": iso(now), "gap_s": round(gap_s, 1),
           "positions": held, "recorded_at": iso(now),
           "policy": "bars_closed_in_gap_not_applied", "note_tr": ("Defter bu aralıkta izlenmedi; aralıkta kapanan barlar "
                                                                   "UYGULANMADI, geçmiş boşluk tahmini işlemle doldurulmadı. "
                                                                   "Koruyucu izleme güncel fiyatla sürüyor.")}
    # SİMÜLASYON GİRDİSİ (2026-09-24): kesinti anındaki defter (pozisyonlar + cüzdan + maliyet ayarları; geçmiş/kayıt
    # satırları hariç) ayrı dosyaya yazılır. Geçmiş uzlaştırma yalnız bununla, AYRI simülasyon olarak koşar
    # (`python -m tradingbot outage-simulate`); canlı defter değişmez.
    try:
        snap = ledger.to_dict()
        snap["history"], snap["entries"] = [], []
        sp = Path(state_path) / "outage_simulations" / ("%s-%s.ledger_at_load.json" % (book_key, iso(last).replace(":", "").replace("+", "_")))
        atomic_write_json(sp, snap)
        rec["simulation_input"] = str(sp.relative_to(Path(state_path)))
    except Exception as exc:  # noqa: BLE001 — girdi yazılamazsa kesinti kaydı yine yazılır
        log.warning("kesinti simülasyon girdisi yazılamadı (%s): %s", book_key, exc)
    record_monitoring_gap(state_path, rec)
    log.warning("İZLEME KESİNTİSİ %s: %s → %s (%.1f sa), yüklemede %d açık pozisyon; aradaki barlar uygulanmadı",
                book_key, rec["from"], rec["to"], gap_s / 3600.0, len(held))
    return int(now.timestamp() * 1000)


def apply_closed_bars_to_ledger(ledger: FuturesLedgerV2, bars_by_symbol: dict[str, dict[str, Any]] | None, *, now: datetime,
                                funding_rate_lookup=None, on_closed: Callable[[Any], None] | None = None,
                                on_event: Callable[..., None] | None = None, want_market: str = PAPER_MARKET,
                                gap_until_ms: int | None = None) -> list:
    """`StrategyBook.apply_closed_bars` sözleşmesinin defter-bağımsız çekirdeği (T2/M2 ve formasyon defteri AYNI kodu kullanır).
    `on_event(symbol, kind, reason, at, **extra)` atlanan barları raporlar; `on_closed(rec)` kapanan işlem başına çağrılır.

    UYGULANMAMA ≠ TÜKETİLME (2026-09-17 onarımı): uygulanamayan bar `pos.meta['ohlc_gaps'][tf]` içine yazılır ve
    boşluk kaydı durdukça o bar SONRAKİ turlarda YENİDEN DENENİR (imleç onu geçmiş olsa bile). Boşluk, bar sonunda
    uygulandığında silinir.

    ZİNCİR DURMAZ: uygulanamayan bir bar, KENDİSİNDEN SONRAKİ geçerli barları ENGELLEMEZ (ölçüldü: `break` ile bir
    bozuk/ölçek dışı bar, 48 barlık pencere boyunca sonraki geçerli barın içindeki koruyucu stop'u da düşürüyordu —
    gerçek bir zarar işlemi kâr olarak kaydedilebiliyordu). Gerekçeler:

      * `BAR_MARKET_MISMATCH`  — çerçeve beklenen piyasadan değil (spec `market` bildiriyorsa denetlenir).
      * `BAR_CORRUPT`          — sonlu/pozitif değil ya da low <= close <= high değil (uydurulmuş uç kullanılmaz).
      * `BAR_SCALE_UNVERIFIED` — ölçek doğrulanamadı: barın AÇILIŞI önceki doğrulanmış kapanışla (yoksa girişle)
                                 sürekli değil VE kapanışı canlı mark'tan uzak (birim/ölçek şüphesi). Büyük gerçek
                                 hareket tek başına bozuk veri sayılmaz (2026-09-22).

    Ölçek hükmü barın UÇLARINA değil KAPANIŞINA bakar: sert bir fitil geçerli veridir ve stop kontrolüne girer;
    bütün barın (kapanışıyla birlikte) kaymış olması ise veri sorunudur.

    STOP TAŞIMASI (2026-09-29, canlı muhasebe düzeltmesi + inceleme bulgusu): stopun son taşındığı andan (`_stop_moved_ms`:
    MFE başa-baş, TP1 dolumu) ÖNCE açılmış bar GÜNCEL stopa sınanmaz; kendi süresinde geçerli stoplarla (`_stops_in_bar`)
    sınanır: kesin çıkış (açılış ya da ters uç, barın açılışındaki stopun ötesinde) ve hiçbir sırada stop olmayan bar
    (ters uç bar içindeki en sıkı stopun berisinde; hedef dolabilir) barın açılışındaki stop durumuyla uygulanır
    (`BAR_PRE_MOVE_STOP` olayı; çıkışta `exit_fill.stop_in_force = "PRE_MOVE"`); ters uç ikisinin arasındaysa sıra
    belirsizdir → bar tüketilir, `BAR_SKIPPED` / `OPENED_BEFORE_STOP_MOVE` yazılır."""
    out: list = []
    now_ms = int(now.timestamp() * 1000)

    def _ev(sym: str, kind: str, reason: str, **extra: Any) -> None:
        if on_event is not None:
            on_event(sym, kind, reason, now, **extra)

    def _gap(pos: Any, tf: str, o: int, reason: str, detail: dict[str, Any]) -> None:
        """Uygulanamayan barı pozisyonun KALICI boşluk kaydına yazar (aynı bar için tek kayıt; kapsama görünür)."""
        gaps = pos.meta.setdefault("ohlc_gaps", {})
        rows = [g for g in (gaps.get(tf) or []) if int(g.get("bar_open_ms") or -1) != int(o)]
        rows.append({"bar_open_ms": int(o), "reason": reason, "at": iso(now), **detail})
        gaps[tf] = rows[-20:]
        _sync_path_flag(pos)

    for sym, spec in (bars_by_symbol or {}).items():
        pos = ledger.positions.get(sym)
        if pos is None or not isinstance(spec, dict):
            continue
        tf = str(spec.get("tf") or BAR_TIMEFRAME)
        step = tf_ms(tf)
        opened_ms = parse_ts_ms(pos.opened_at)
        if opened_ms is None:
            _ev(sym, "BAR_SKIPPED", "OPENED_AT_UNREADABLE", tf=tf)
            continue
        # PİYASA KİMLİĞİ: çerçeve hangi piyasadan geldiğini bildiriyorsa denetlenir (fiyat bandıyla TAHMİN EDİLMEZ).
        mkt = spec.get("market")
        if mkt is not None and str(mkt) != str(want_market):
            first = spec.get("first_bar_ms")
            if first is None:                            # çağıran bildirmediyse ilk satırdan türetilir (0 damgalanmaz)
                try:
                    first = min(int(r["timestamp"]) for r in (spec.get("rows") or []) if r.get("timestamp") is not None)
                except (TypeError, ValueError):
                    first = -1
            _ev(sym, "BAR_SKIPPED", BAR_MARKET_MISMATCH, tf=tf, market=str(mkt), want_market=str(want_market))
            _gap(pos, tf, int(first), BAR_MARKET_MISMATCH, {"market": str(mkt), "want_market": str(want_market)})
            continue
        cur = pos.meta.get("ohlc_cursor") if isinstance(pos.meta.get("ohlc_cursor"), dict) else {}
        cursor = int(cur.get(tf) or 0)
        # ÇÖZÜLMEMİŞ BOŞLUKLAR: imleç bunları geçmiş olsa bile yeniden denenir (kalıcı kayıp yok).
        pending = {int(g.get("bar_open_ms") or -1) for g in ((pos.meta.get("ohlc_gaps") or {}).get(tf) or [])
                   if isinstance(g, dict)}
        ref = float(spec.get("mark") or 0.0)
        try:
            rows = sorted((r for r in (spec.get("rows") or []) if r.get("timestamp") is not None), key=lambda r: int(r["timestamp"]))
        except (TypeError, ValueError):
            rows = []
        gap_skipped = 0
        for r in rows:
            o = int(r["timestamp"])
            if o + step > now_ms:
                break                                    # kapanmamış (ve sonrakiler de): uçları KULLANILMAZ
            if o < opened_ms or (o <= cursor and o not in pending):
                continue                                 # girişten önce açılmış ya da zaten UYGULANMIŞ
            if gap_until_ms is not None and o + step <= int(gap_until_ms):
                # İZLEME KESİNTİSİ (2026-09-23): bar izlenmeyen aralıkta kapandı → UYGULANMAZ (geçmiş boşluk tahmini
                # işlemle doldurulmaz); imleç ilerler, bir daha denenmez ve bu bara ait eski boşluk kaydı düşer.
                cursor = max(cursor, o)
                gap_skipped += 1
                g = pos.meta.get("ohlc_gaps")
                if o in pending and isinstance(g, dict) and g.get(tf):
                    rest = [x for x in g[tf] if int(x.get("bar_open_ms") or -1) != o]
                    if rest:
                        g[tf] = rest
                    else:
                        g.pop(tf, None)
                    _sync_path_flag(pos)
                continue
            try:
                hi, lo, cl = float(r.get("high")), float(r.get("low")), float(r.get("close"))
            except (TypeError, ValueError):
                hi = lo = cl = float("nan")
            try:
                op = float(r["open"]) if r.get("open") is not None else None
            except (TypeError, ValueError):
                op = float("nan")
            # 1) BÜTÜNLÜK — uydurulmuş uç üretilemez. Uçlar barın KENDİ gövdesine göre sınırlıdır (açılış varsa
            #    min/maks(açılış, kapanış), yoksa kapanış): derin ama gerçek bir fitil ya da büyük bir gerçek hareket
            #    geçer, birim/ölçek artefaktı (örn. 10.000 kat) geçmez. Açılış verildiyse o da bütünlüğe tabidir.
            body_lo = min(cl, op) if op is not None else cl
            body_hi = max(cl, op) if op is not None else cl
            if not (all(math.isfinite(v) and v > 0 for v in (hi, lo, cl) + ((op,) if op is not None else ()))
                    and lo <= cl <= hi and (op is None or lo <= op <= hi)
                    and BAR_EXTREME_MIN_RATIO * body_lo <= lo and hi <= BAR_EXTREME_MAX_RATIO * body_hi):
                _ev(sym, "BAR_SKIPPED", BAR_CORRUPT, tf=tf, bar_open_ms=o, mark=ref)
                _gap(pos, tf, o, BAR_CORRUPT, {"open": op if (op is not None and math.isfinite(op)) else None,
                                               "high": hi if math.isfinite(hi) else None, "low": lo if math.isfinite(lo) else None,
                                               "close": cl if math.isfinite(cl) else None})
                cursor = max(cursor, o)                  # SONRAKİ barlar işlenmeye devam eder; boşluk kaydı kalır
                continue
            # 2) ÖLÇEK (2026-09-22) — bar, fiyat ölçeği DOĞRULANMIŞ bir referansa bağlanabiliyor mu? İki yol:
            #    a) SÜREKLİLİK: barın açılışı, bu pozisyon için daha önce doğrulanmış en yakın ÖNCEKİ barın kapanışına
            #       (yoksa giriş fiyatına — girişte doğrulanmış perp mark) ±BAR_SCALE_TOLERANCE içinde. Perp 7/24 işlem
            #       görür: ardışık barın açılışı öncekinin kapanışıdır; birim artefaktı bu bağı kırar, gerçek hareket
            #       (fitil ya da kapanışta büyük düşüş) kırmaz.
            #    b) CANLI MARK YAKINLIĞI (eski yol): kapanış güncel mark'ın ±BAR_SCALE_TOLERANCE'ı içinde.
            #    ÖNCE yalnız (b) vardı: kapanışı sonradan toparlanan mark'tan %20 uzak GEÇERLİ bir stop barı atlanıyor,
            #    pozisyon açık kalıp sonra hedefte KÂR yazabiliyordu (REVIEW-2026-09-22 F1). `mark<=0` artık doğrulamayı
            #    ATLATMAZ: hiçbir yol doğrulayamıyorsa bar uygulanmaz.
            anchor, anchor_src = _scale_anchor(pos, tf, o)
            cont_ok = op is not None and anchor > 0 and abs(op / anchor - 1.0) <= BAR_SCALE_TOLERANCE
            mark_ok = ref > 0 and (1.0 - BAR_SCALE_TOLERANCE) * ref <= cl <= (1.0 + BAR_SCALE_TOLERANCE) * ref
            if not (cont_ok or mark_ok):
                _ev(sym, "BAR_SKIPPED", BAR_SCALE_UNVERIFIED, tf=tf, bar_open_ms=o, mark=ref, close=cl)
                _gap(pos, tf, o, BAR_SCALE_UNVERIFIED, {"open": op, "high": hi, "low": lo, "close": cl, "mark": ref,
                                                        "continuity_ref": anchor, "continuity_ref_source": anchor_src,
                                                        "tolerance": BAR_SCALE_TOLERANCE})
                cursor = max(cursor, o)
                continue
            cursor = max(cursor, o)                      # ARTIK uygulanıyor
            _remember_verified(pos, tf, o, cl, "CONTINUITY" if cont_ok else "MARK")
            close_dt = datetime.fromtimestamp((o + step) / 1000.0, tz=timezone.utc)
            td = TickData(last=Decimal(str(cl)), mark=Decimal(str(cl)), high=Decimal(str(hi)), low=Decimal(str(lo)), ts=iso(close_dt),
                          open=Decimal(str(op)) if op is not None else None)
            g = pos.meta.get("ohlc_gaps")
            if isinstance(g, dict) and g.get(tf):        # aynı bar sonunda uygulanıyor: boşluk kaydı ÇÖZÜLDÜ
                rest = [x for x in g[tf] if int(x.get("bar_open_ms") or -1) != o]
                if rest:
                    g[tf] = rest
                else:
                    g.pop(tf, None)
                _sync_path_flag(pos)                     # kapanış bu tikte olursa kayıt GÜNCEL durumu taşısın
            # STOP TAŞINDIKTAN SONRA GELEN ESKİ BAR (2026-09-29, canlı muhasebe düzeltmesi + inceleme bulgusu): stop fiyat
            # izlemesiyle (MFE başa-baş / TP1) taşındıysa, taşımadan ÖNCE açılmış barın açılışı ve uçları GÜNCEL stopa
            # sınanamaz (eskiden açılış yeni stopun ötesindeyse pozisyon o daha önceki açılıştan kapanıyordu: stop 98 iken
            # 99,0 açılış, 16:40'ta başa-başa taşındı, 17:05 turunda 98,97'den "başa-baş stop"). Bar, KENDİ süresinde geçerli
            # olan stoplarla sınanır (`_stops_in_bar`): açılıştaki (en gevşek) stopa göre kesin çıkış (açılış ya da ters uç
            # onun ötesinde — sıra ne olursa olsun pozisyon kapanmıştı) → o stopla uygulanır (ihtiyatlı: seviyeden); ters uç
            # bar içindeki EN SIKI stopun da berisindeyse hiçbir sırada stop yoktur → aynı görünümle uygulanır (kesin hedef
            # dolumu kaybolmaz); ters uç ikisinin ARASINDAYSA sıra belirsizdir → bar uygulanmaz, tüketilir ve `BAR_SKIPPED` /
            # `OPENED_BEFORE_STOP_MOVE` yazılır (taşımadan sonraki yolu koruyucu izleyici yeni stopla izledi).
            moved_ms = _stop_moved_ms(pos)
            view = None
            if moved_ms is not None and o < moved_ms:
                long = str(getattr(pos.side, "value", pos.side)).upper() == "LONG"
                s_open, s_tight = _stops_in_bar(pos, o, o + step)
                info = {"tf": tf, "bar_open_ms": o, "stop_moved_at": iso(datetime.fromtimestamp(moved_ms / 1000.0, tz=timezone.utc)),
                        "stop_at_bar_open": str(s_open) if s_open is not None else None,
                        "stop_tightest_in_bar": str(s_tight) if s_tight is not None else None,
                        "stop": str(pos.stop) if pos.stop is not None else None}
                certain = s_open is not None and exit_decision(pos.side, s_open, pos.liquidation_price, td) is not None
                worst = Decimal(str(lo)) if long else Decimal(str(hi))
                if not certain and (s_tight is None or (worst <= s_tight if long else worst >= s_tight)):
                    _ev(sym, "BAR_SKIPPED", BAR_OPENED_BEFORE_STOP_MOVE, detail="AMBIGUOUS_INTRABAR_ORDER", **info)
                    continue
                be = pos.meta.get("be_by_mfe") if isinstance(pos.meta.get("be_by_mfe"), dict) else {}
                be_ms = parse_ts_ms(be.get("at")) if be else None
                view = _PreMoveView(ledger, pos, s_open, hide_be=be_ms is not None and be_ms > o)
                _ev(sym, BAR_PRE_MOVE_STOP, BAR_PRE_MOVE_APPLIED, certain_exit=bool(certain), **info)
            if view is None:
                recs = ledger.tick({sym: td}, now_utc=close_dt, funding_rate_lookup=funding_rate_lookup, bar_advance=False)
            else:
                with view:
                    recs = ledger.tick({sym: td}, now_utc=close_dt, funding_rate_lookup=funding_rate_lookup, bar_advance=False)
                for rec in recs:
                    ef = rec.features.get("exit_fill") if isinstance(rec.features, dict) else None
                    if isinstance(ef, dict):             # hangi stopun geçerli sayıldığı kayıtta görünür
                        rec.features["exit_fill"] = {**ef, "stop_in_force": "PRE_MOVE", "stop_at_bar_open": info["stop_at_bar_open"],
                                                     "stop_after_move": info["stop"], "stop_moved_at": info["stop_moved_at"]}
            if sym in ledger.positions:
                ledger.positions[sym].meta.setdefault("ohlc_cursor", {})[tf] = cursor
            for rec in recs:
                if on_closed is not None:
                    on_closed(rec)
                out.append(rec)
            if sym not in ledger.positions:
                break
        if gap_skipped:
            _ev(sym, "BAR_SKIPPED", "MONITORING_GAP", tf=tf, n_bars=gap_skipped, gap_until_ms=int(gap_until_ms or 0))
        if sym in ledger.positions and cursor:
            ledger.positions[sym].meta.setdefault("ohlc_cursor", {})[tf] = cursor
    return out

def validate_settings(*, enabled: bool, name: str | None, app_mode: str | None, starting_equity: float,
                      atr_mult: float, rule_params: dict | None = None) -> None:
    """Config doğrulaması (SAF). Gerçek parayla (LIVE) etkinleştirilemez.

    V15: `rule_params` de burada doğrulanır — box defterinin bilinmeyen/geçersiz alanı config
    yüklenirken patlar, tur ortasında değil.
    """
    if name not in paper_rules.VARIANTS:
        raise ValueError("strategy_paper.name gecersiz: %r (gecerli: %s)" % (name, ", ".join(paper_rules.VARIANTS)))
    try:
        paper_rules.build_params(name, atr_mult=atr_mult if atr_mult > 0 else 1.0, rule_params=rule_params)
    except TypeError as exc:
        raise ValueError("strategy_paper.rule_params gecersiz alan: %s" % exc) from None
    if enabled and str(app_mode or "").upper() in ("LIVE", "LIVE_LIMITED"):
        raise ValueError("STRATEGY_PAPER_IS_PAPER_ONLY: strategy_paper.enabled yalniz PAPER/TESTNET/OBSERVE/SHADOW_LIVE modda")
    if starting_equity <= 0 or atr_mult <= 0:
        raise ValueError("strategy_paper.starting_equity_usdt ve atr_mult pozitif olmali")


__all__ = ["INDEX_FILE", "PAPER_MARKET", "RULE_TIMEFRAMES", "paper_rules", "SCHEMA_VERSION", "SUMMARY_FILE", "BookSpec", "DataVerdict",
           "StrategyBook", "apply_action", "book_specs", "validate_settings", "verify_paper_data"]

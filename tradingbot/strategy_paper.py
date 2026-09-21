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
                         SlippageModel, TaxPolicy, TickData, default_brackets)
from .candle_confirmation import closed_bars
from .core import atomic_write_json, from_iso, iso, utc_now
from .learn import TradeMemory
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
BAR_LAG_TOLERANCE_MS: dict[str, int] = {"1d": 3_600_000, "5m": 900_000}
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
BAR_SCALE_UNVERIFIED = "BAR_SCALE_UNVERIFIED"  # kapanış canlı mark ölçeğinden uzak: ölçek/birim şüphesi
BAR_MARKET_MISMATCH = "BAR_MARKET_MISMATCH"  # bar çerçevesi beklenen piyasadan değil (SPOT ikamesi vb.)


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
    out: dict[str, Any] = {"ok": False, "mark": 0.0, "price_ts_ms": None, "fetched_at_ms": None, "checked_at_ms": int(now_ms),
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
    out.update(mark=mark, price_ts_ms=price_ts, fetched_at_ms=fetched)
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


def apply_action(act: dict[str, Any] | None, *, symbol: str, price: float, tick: TickData | None, now: datetime,
                 ledger: FuturesLedgerV2, risk: RiskEngine, profile, state, filters, run_id: str,
                 reject: Callable[[str, str], None], on_closed: Callable[[Any], None],
                 on_opened: Callable[[Any, dict[str, Any]], None] | None = None,
                 data: DataVerdict | None = None, max_entry_drift_pct: float = 0.0) -> str:
    """Stratejinin kararını uygular. Döner: OPENED | CLOSED | REJECTED | NONE. İki motor da bunu çağırır.

    `data` (2026-09-16): kural verisinin kimlik hükmü. Sözleşme fail-closed'dur — hüküm yoksa ya da `ok` değilse ne
    OPEN ne CLOSE uygulanır (SPOT ikamesi / kaynağı bilinmeyen veriyle futures işlemi yok); OPEN ayrıca `entry_ok`
    (provenansın giriş izni + BTC referansı) ister. Açık pozisyonun stop/hedef/likidasyonu bu fonksiyondan değil,
    defterin `tick`inden yürür ve bu hükümden ETKİLENMEZ."""
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
    # Kanıt: hangi veriyle girildiği pozisyon/işlem kaydına yazılır (piyasa, kaynak, tur, kullanılan bar zamanları).
    data_src = {"market": data.market, "source": data.source, "tour_id": data.tour_id, "bars": dict(data.bars), "btc": dict(data.btc)}
    pos = ledger.open(symbol, direction, entry, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, int(rd.adjusted_leverage or lev)),
                      filters=filters, stop=stop, targets=list(act.get("targets") or []),
                      setup_type=str(act.get("setup_type") or "strategy"), trigger_text=str(act.get("reason") or ""),
                      features={"regime": act.get("regime"), "market_type": "USDM_PERP", "strategy": act.get("name"),
                                "expected_r": float(act.get("expected_r") or 0.0), "p_win": None, "data_source": data_src},
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
            for sym, g in (gaps or {}).items():
                prev = self.data_gaps.get(sym)
                if not prev or prev.get("reason") != g.get("reason"):
                    self._data_event(sym, "PRICE_GAP", str(g.get("reason") or "NO_VERIFIED_FUTURES_PRICE"), now, detail=g.get("detail"))
                self.data_gaps[sym] = dict(g)
            for sym in [s for s in self.data_gaps if s not in (gaps or {})]:
                self._data_event(sym, "PRICE_RESTORED", "", now)
                self.data_gaps.pop(sym, None)

    def _on_closed(self, rec) -> None:
        self.counters["closed"] += 1
        d = rec.to_legacy_dict() if hasattr(rec, "to_legacy_dict") else {}
        self.closed_recent.append({"id": getattr(rec, "id", None), "symbol": getattr(rec, "symbol", None),
                                   "exit_reason": getattr(rec, "exit_reason", None),
                                   "net_pnl": float(getattr(rec, "net_pnl", 0) or 0), "r": float(getattr(rec, "r_multiple", 0) or 0),
                                   "closed_at": getattr(rec, "closed_at", None), "features": d.get("features")})
        self.closed_recent = self.closed_recent[-50:]

    def _on_opened(self, pos, act: dict[str, Any]) -> None:
        self.counters["opened"] += 1
        try:
            self.memory.record_entry({"trade_id": pos.id, "symbol": pos.symbol, "direction": pos.side.value, "market_type": "USDM_PERP",
                                      "setup_type": "trend", "regime": act.get("regime"),
                                      "features": {"strategy": act.get("name"), "signal_close": act.get("signal_close"),
                                                   "ema200": act.get("ema200"), "atr14": act.get("atr14"),
                                                   # CHART ANALYSIS V1: giris ANINDAKI referanslar (sonradan degismez)
                                                   "signal_ts": act.get("signal_ts"), "ref_close": act.get("ref_close"),
                                                   "ref_ts": act.get("ref_ts"), "stop_at_entry": act.get("stop"),
                                                   "atr_mult": self.atr_mult},
                                      "run_id": self.run_id, "in_test": True})
        except Exception as exc:  # noqa: BLE001 — bellek arızası işlemi ETKİLEMEZ
            log.warning("strateji bellek kaydı yazılamadı (%s): %s", pos.symbol, exc)

    # ------------------------------------------------------------------ tur
    def step(self, *, symbols: list[str], frames_by_symbol: dict[str, dict], marks: dict[str, TickData],
             marks_f: dict[str, float], now: datetime, provenance_by_symbol: dict[str, dict] | None = None,
             data_gaps: dict[str, dict] | None = None) -> None:
        """Her turda: veri kimliği doğrulaması → kural → apply_action. Ana bot dokunulmaz; yalnız bu defter değişir.

        `provenance_by_symbol` (2026-09-16): sağlayıcının BU tur için yazdığı çerçeve provenansı (motor
        `_frame_provenance`, tur kimliği ve bar bağıyla). Yoksa/eşleşmiyorsa/SPOT ise kural bu sembol için
        DEĞERLENDİRİLMEZ (ne OPEN ne kural CLOSE'u SPOT ikamesiyle üretilir); ret gerekçeli kaydedilir. Açık
        pozisyonun stop/hedef/likidasyon takibi `tick` ile ayrı sürer. `marks`/`marks_f`: DOĞRULANMIŞ futures
        fiyatı (motor `_paper_marks`); `data_gaps`: fiyatı doğrulanamayan semboller (bu turda tick de yok)."""
        with self.lock:
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
                                                    position=pos_obj, params=self.rule_params)
                        would = str((wa or {}).get("action") or "NONE")
                    except Exception:  # noqa: BLE001
                        would = "ERROR"
                    self._reject_data(sym, verdict, "SIGNAL" if not verdict.ok else "ENTRY", now, would)
                    continue
                try:
                    act = paper_rules.decide_for(self.name, frames=fr, btc_rows=btc, now_ms=now_ms,
                                                 position=pos_obj, params=self.rule_params)
                except Exception as exc:  # noqa: BLE001 — strateji arızası SESSİZ GEÇMEZ
                    self._reject(sym, "STRATEGY_ERROR:%s" % type(exc).__name__)
                    continue
                res = apply_action(act, symbol=sym, price=float(marks_f[sym]), tick=marks.get(sym), now=now,
                                   ledger=self.ledger, risk=self.risk, profile=self.profile, state=state,
                                   filters=self.filters_cache.get(sym, MarketType.USDM_PERP), run_id=self.run_id,
                                   reject=self._reject, on_closed=self._on_closed, on_opened=self._on_opened, data=verdict,
                                   max_entry_drift_pct=self.max_entry_drift_pct)
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
                    if _reason != "NO_SIGNAL":
                        self.rejections[_reason] = self.rejections.get(_reason, 0) + 1

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

    def tick(self, marks: dict[str, TickData], *, now: datetime, funding_rate_lookup=None, bar_advance: bool) -> list:
        """CANLI FİYAT KONTROLÜ: defterin stop/hedef/funding/likidasyon kontrolü; ana defterle AYNI çağrı biçimi.
        `marks` yalnız doğrulanmış, güncel perp mark taşır (bar ucu YOK; uçlar `apply_closed_bars` ile ayrı sözleşmede)."""
        with self.lock:
            recs = self.ledger.tick(marks, now_utc=now, funding_rate_lookup=funding_rate_lookup, bar_advance=bar_advance)
            for rec in recs:
                self._on_closed(rec)
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
            return apply_closed_bars_to_ledger(self.ledger, bars_by_symbol, now=now, funding_rate_lookup=funding_rate_lookup,
                                               on_closed=self._on_closed, on_event=self._data_event)


    def save(self, marks_f: dict[str, float], now: datetime) -> None:
        with self.lock:
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
                   "last_actions": self.last_actions, "counters": dict(self.counters),
                   "rejections": dict(self.rejections), "closed_recent": self.closed_recent[-20:],
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
            doc["key"] = self.key
            atomic_write_json(Path(self.cfg.state_path) / self.summary_file, doc)
            # Bayrak TUKETILIR: bir sonraki yazma (or. 60 sn'lik cikis izleyicisi) kural gecisi
            # olmadan gelirse `rule_evaluated_this_write` FALSE olur ve defter "canli" gorunemez.
            self._rule_ran_since_save = False


def apply_closed_bars_to_ledger(ledger: FuturesLedgerV2, bars_by_symbol: dict[str, dict[str, Any]] | None, *, now: datetime,
                                funding_rate_lookup=None, on_closed: Callable[[Any], None] | None = None,
                                on_event: Callable[..., None] | None = None, want_market: str = PAPER_MARKET) -> list:
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
      * `BAR_SCALE_UNVERIFIED` — barın KAPANIŞI canlı mark ölçeğinden uzak (ölçek/birim şüphesi).

    Ölçek hükmü barın UÇLARINA değil KAPANIŞINA bakar: sert bir fitil geçerli veridir ve stop kontrolüne girer;
    bütün barın (kapanışıyla birlikte) kaymış olması ise veri sorunudur."""
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
        for r in rows:
            o = int(r["timestamp"])
            if o + step > now_ms:
                break                                    # kapanmamış (ve sonrakiler de): uçları KULLANILMAZ
            if o < opened_ms or (o <= cursor and o not in pending):
                continue                                 # girişten önce açılmış ya da zaten UYGULANMIŞ
            try:
                hi, lo, cl = float(r.get("high")), float(r.get("low")), float(r.get("close"))
            except (TypeError, ValueError):
                hi = lo = cl = float("nan")
            # 1) BÜTÜNLÜK — uydurulmuş uç üretilemez. Uçlar barın KENDİ kapanışına göre de sınırlıdır: derin ama
            #    gerçek bir fitil geçer, birim/ölçek artefaktı (örn. 10.000 kat) geçmez.
            if not (all(math.isfinite(v) and v > 0 for v in (hi, lo, cl)) and lo <= cl <= hi
                    and BAR_EXTREME_MIN_RATIO * cl <= lo and hi <= BAR_EXTREME_MAX_RATIO * cl):
                _ev(sym, "BAR_SKIPPED", BAR_CORRUPT, tf=tf, bar_open_ms=o, mark=ref)
                _gap(pos, tf, o, BAR_CORRUPT, {"high": hi if math.isfinite(hi) else None, "low": lo if math.isfinite(lo) else None,
                                               "close": cl if math.isfinite(cl) else None})
                cursor = max(cursor, o)                  # SONRAKİ barlar işlenmeye devam eder; boşluk kaydı kalır
                continue
            # 2) ÖLÇEK — yalnız KAPANIŞ canlı mark'la karşılaştırılır; fitil derinliği ölçüt DEĞİLDİR.
            if ref > 0 and not ((1.0 - BAR_SCALE_TOLERANCE) * ref <= cl <= (1.0 + BAR_SCALE_TOLERANCE) * ref):
                _ev(sym, "BAR_SKIPPED", BAR_SCALE_UNVERIFIED, tf=tf, bar_open_ms=o, mark=ref, close=cl)
                _gap(pos, tf, o, BAR_SCALE_UNVERIFIED, {"close": cl, "mark": ref, "tolerance": BAR_SCALE_TOLERANCE})
                cursor = max(cursor, o)
                continue
            cursor = max(cursor, o)                      # ARTIK uygulanıyor
            close_dt = datetime.fromtimestamp((o + step) / 1000.0, tz=timezone.utc)
            td = TickData(last=Decimal(str(cl)), mark=Decimal(str(cl)), high=Decimal(str(hi)), low=Decimal(str(lo)), ts=iso(close_dt))
            recs = ledger.tick({sym: td}, now_utc=close_dt, funding_rate_lookup=funding_rate_lookup, bar_advance=False)
            if sym in ledger.positions:
                ledger.positions[sym].meta.setdefault("ohlc_cursor", {})[tf] = cursor
                g = ledger.positions[sym].meta.get("ohlc_gaps")
                if isinstance(g, dict) and g.get(tf):      # aynı bar sonunda uygulandı: boşluk kaydı ÇÖZÜLDÜ
                    rest = [x for x in g[tf] if int(x.get("bar_open_ms") or -1) != o]
                    if rest:
                        g[tf] = rest
                    else:
                        g.pop(tf, None)
            for rec in recs:
                if on_closed is not None:
                    on_closed(rec)
                out.append(rec)
            if sym not in ledger.positions:
                break
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

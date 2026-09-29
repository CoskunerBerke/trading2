"""ÖĞRENME MODU KARŞI-OLGUSAL KAYDI (2026-09-28, öğrenme modu) — defter başına bir kayıtçı.

Öğrenme modunda bile açılamayan geçerli sinyal kaybolmaz: `learn.shadow.ShadowBook` üstüne (aynı `ShadowTrade` şeması,
`is_counterfactual=True`, arşiv) kaydedilir ve kapanmış barlarla etiketlenir. Sözleşme:

* `plan_id` = barın SİNYAL ANAHTARI (P1) — tur kimliğinden TÜRETİLMEZ; aynı bar 16 tur boyunca bloke kalsa da tek kayıt.
* Tekillik anahtarı (defter, sinyal anahtarı, sembol, yön, varyasyon) — etiketlenmiş kayıt da tekrarı engeller.
* Yalnız `as_planned` varyantı; defter, özkaynak ve P&L karnesine DOKUNMAZ.
* Bekleyen kayıt tavanı (`max_pending`): aşılırsa EN ESKİ bekleyen düşürülür ve `dropped` sayılır.
* Etiketleme yalnız `now` anında KAPANMIŞ barlarla (açılış + tf ≤ now), önce stop sonra hedef (`label_with_candles`).
  TARGET_STOP_TIME: ufuk kuralın en uzun tutma süresi. HORIZON: 30 bar, `approx=True`. RULE_EXIT (kural çıkış yüklemi)
  bu sürümde HORIZON ile etiketlenir (`label_method=HORIZON_FALLBACK`, `approx=True`).
* `record`/`label_pending` diske YAZMAZ; geçiş sonunda `save()` çağrılır (tek atomik yazım).
* Aynı sinyal sonraki bir turda GERÇEK işlem olarak açılırsa bekleyen kaydı `supersede` ile düşürülür (aynı gözlem iki kez
  sayılmaz; `superseded` sayacı).
* Etiketleme ucuzdur (2026-09-28, öğrenme modu): yol, stop/hedef hiç değmediyse ve ufuk dolmadıysa yürütülmez (numpy ön
  kontrol); tam yürütme (`label_with_candles`) yalnız sonuç kesinleşebilecekken yapılır — sonuçlar aynıdır.

NET ETİKET — `cf_label_v2` (2026-09-29, maliyet sapması): brüt R (`r_multiple`: referans fiyattan, seviyeden dolum, ücret/
kayma/funding YOK) gerçek işlemlerin NET R'siyle aynı tabanda değildi (Box: +0,40R "olsaydı" / −0,57R gerçek; sıkı stopta
maliyet ~0,45R). Kesinleşen etikete, defterin KENDİ yürütme modeliyle (`ExecModel.of_ledger`: ücret, kayma, TP1 oranı,
MFE başa-baş, likidasyon, funding takvimi — yeniden YAZILMAZ, defter nesnesinden kopyalanır) atılık bir `FuturesLedgerV2`de
AYNI kapanmış barlar üzerinde yeniden oynatılan NET sonuç eklenir (`net_outcome`). Eski alanlar (`r_multiple`, `won`,
`veto_was_right`, `exit_reason`, `exit_price`) v1 anlamıyla (BRÜT) KALIR; yeni alanlar yanına yazılır (`label_version`,
`r_gross`, `r_net`, `cost_r`, `cost_parts_r`, `won_net`, `veto_was_right_net`, `funding_complete`, `exec_model`, ...).
`label_version` alanı olmayan sonuç v1'dir (yalnız brüt). `exec_model` verilmezse çıktı v1 ile BİT-AYNI.
Eski (v1) etiketli kayıtlar TEMBEL yeniden etiketlenir (`relabel_net`): etiketleme çağrısında eldeki kapanmış barlar
pencereyi baştan kapsıyor ve brüt sonuç AYNEN yeniden üretiliyorsa net eklenir (`net_backfilled`); pencere çerçeveden
düşmüşse kayıt `cf_label_v1` + `net_status` ile işaretlenir ve çevrimdışı `scripts/cf_backfill_net.py`e kalır.

BAR İÇİ YOL — `cf_label_v3` / `cf_net_ledger_replay_v2` (2026-09-29, inceleme bulguları): v2 her barı TEK tick (açılış,
yüksek, düşük, kapanış) olarak defterden geçiriyordu; defter MFE başa-başını barın YÜKSEĞİYLE taşıyıp aynı tikte barın
(daha önceki) AÇILIŞINI stopun ötesinde görünce açılıştan satıyordu (nedensel olarak imkânsız sıra), ve 4h/1d barda kapanış
stopun ötesindeyse dolum kapanıştan yapılıyordu (canlı 60 sn izleyici seviyenin yanında doldurur). v3 her barı İHTİYATLI ve
NEDENSEL bir yol olarak yürür: açılış → ters uç → lehte uç → kapanış, her nokta AYRI fiyat-yalnız tick; MFE başa-baş / TP1
yalnız onları tetikleyen noktadan SONRAKİ noktalarda etkilidir. Sıra İLK stop için ihtiyatlıdır (ters uç hedeften önce);
lehte ucun TAŞIDIĞI stop (başa-baş / TP1) için DEĞİLDİR: aynı barın ters ucu yeni stopun ötesindeyse sıra belirsizdir ve
bu yol hep "sürer" dalını alır (inceleme bulgusu, 2026-09-29). Kötü dal (lehte uç → ters uç) karalama Monte Carlo'da daha
KÖTÜ bir tahminci çıktı (ana defter net − gerçek ort. +0,01 → −0,07 R, ort. |fark| 0,04 → 0,10), bu yüzden yol korunur ve
böyle barlar sayılır: net sonuç `intrabar_ambiguous_bars` taşır (> 0 → rapor süzebilir; `net_stats` sayar). Dokunulan stop
SEVİYEDEN (+ defterin çıkış kayması) dolar; yalnız bar stopun ÖTESİNDE AÇILIRSA (gerçek boşluk) dolum açılıştandır — yolun
İLK barı hariç: girişten o barın açılışına kadarki aralığı canlı izleyici izliyordu (karşı-olgusal onu görmez), orada
geçilen stop seviyede dolar. Kalan iyimserlik iki kaynaklıdır: (1) canlı izleyicinin iki 60 sn fiyatı arasında seviyeyi
AŞMASI (seviyeden dolum bunu yüklemez), (2) yukarıdaki belirsiz barlar.
Eski `cf_label_v2` kayıtları sürümlerini KORUR (yeniden oynatılmaz; üretimde v2 kaydı yoktur — v2 hiç dağıtılmadı).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .accounting import (EXIT_BE_STOP, EXIT_STOP, AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, SizeSpec,
                         SlippageModel, TaxPolicy, TickData, default_filters)
from .accounting.funding import FundingSchedule
from .accounting.models import MarketType, SymbolFilters
from .core import D, from_iso, iso, stable_id
from .learn.shadow import ShadowBook, ShadowTrade, label_with_candles
from .learning_mode import counterfactual_ok
from .timeframes import TF_MS

LABEL_TARGET_STOP_TIME = "TARGET_STOP_TIME"
LABEL_RULE_EXIT = "RULE_EXIT"
LABEL_HORIZON = "HORIZON"
LABEL_KINDS = (LABEL_TARGET_STOP_TIME, LABEL_RULE_EXIT, LABEL_HORIZON)
#: HORIZON (ve bu sürümde RULE_EXIT) ufku — bar.
HORIZON_BARS = 30
#: Ufuk + bu kadar bar geçtiği hâlde kapanmış barlarla etiketlenemeyen kayıt (veri boşluğu) bekleyenden çıkarılır.
STALE_GRACE_BARS = 10
SCHEMA_VERSION = "learning_cf_v1"
_FINAL_EXITS = ("stop", "breakeven_stop", "target")
_TF_BY_MIN = {v // 60_000: k for k, v in TF_MS.items()}
#: ETİKET SÜRÜMLERİ (2026-09-29, maliyet sapması). v1 = alan YOK (yalnız brüt); v1 işaretli = net yeniden oynatma bu yoldan
#: yapılamadı (`net_status` nedeni; çevrimdışı dolgu bekler); v2 = brüt + net (`cf_net_ledger_replay_v1`: bar tek tick);
#: v3 = brüt + net (`cf_net_ledger_replay_v2`: bar içi nedensel yol, seviyeden stop dolumu — 2026-09-29); v1c = çevrimdışı
#: kapalı-biçim YAKLAŞIK net (`r_net_approx`; ne alt ne üst sınır: çıkış dolum modeli, ücret dahil başa-baş fiyatı, MFE
#: başa-baş ve funding yok) — net ortalamasına ASLA karışmaz. Net'i olan her sürüm (v2, v3) `r_net` taşır.
LABEL_VERSION = "cf_label_v3"
LABEL_VERSION_V1 = "cf_label_v1"
LABEL_VERSION_V1C = "cf_label_v1c"
LABEL_VERSION_V2 = "cf_label_v2"
NET_CONTRACT = "cf_net_ledger_replay_v2"
#: Bar içi yol sırası (2026-09-29, `cf_net_ledger_replay_v2`): nedensel; İLK stop için ihtiyatlı (önce ters uç). Lehte ucun
#: taşıdığı stop için sıra belirsiz olabilir → `intrabar_ambiguous_bars` (inceleme bulgusu, 2026-09-29).
INTRABAR_PATH = "open>adverse>favourable>close"
#: Karşı-olgusal stop dolum kuralı (2026-09-29): dokunulan stop seviyeden + defterin çıkış kayması; boşluk yalnız bar açılışı.
STOP_FILL_RULE = "level_plus_slippage_gap_only_at_bar_open"
#: `won_net` eşiği — gerçek işlem etiketiyle AYNI kural (`learn.labels.label_outcome`: |R| < 0,25 SCRATCH, R ≥ 0,25 WIN).
WON_NET_MIN_R = 0.25
#: Yeniden oynatma pozisyonu (USDT, 1x). R büyüklükten bağımsızdır (payda aynı miktarla ölçeklenir); taban yalnız adım
#: yuvarlamasını (TP1 kısmi miktarı) önemsiz kılmak için büyütülür (`_replay_notional`).
NET_NOTIONAL_USDT = Decimal("1000")
#: Tembel net dolgu: bir etiketleme çağrısında en fazla bu kadar v1 kayıt yeniden oynatılır (defter kilidi kısa kalsın).
RELABEL_MAX_PER_CALL = 400
#: Doğrulanmamış (varsayılan) filtreyle yeniden oynatma: gerçek defter bu hassasiyetle GİRMEZ (UNRESOLVED_PRECISION), 0,01
#: varsayılan tick'i düşük fiyatlı sembolde dolumu uydururdu → tick/adım yuvarlaması YAPILMAZ (kayıtta `filters_source`).
NEUTRAL_FILTERS_SOURCE = "UNVERIFIED_NEUTRAL"


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _clean(x: Any, depth: int = 0) -> Any:
    """JSON'a güvenli kopya: sonlu olmayan float → None, Decimal → float, datetime → ISO; derinlik sınırlı."""
    if depth > 6:
        return None
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, Decimal):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, datetime):
        return iso(x)
    if isinstance(x, dict):
        return {str(k): _clean(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_clean(v, depth + 1) for v in x]
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return str(x)


def _to_ms(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return int(_aware(v).timestamp() * 1000)
    if isinstance(v, pd.Timestamp):
        t = v.tz_localize("UTC") if v.tzinfo is None else v.tz_convert("UTC")
        return int(t.value // 1_000_000)
    if isinstance(v, str):
        try:
            return int(from_iso(v).timestamp() * 1000)
        except ValueError:
            try:
                return int(float(v))
            except ValueError:
                return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _open_px(v: Any) -> float | None:
    """Bar açılışı (net yeniden oynatmanın ilk gözlemi); okunamaz/sonlu-pozitif değilse None (brüt etiket etkilenmez)."""
    try:
        o = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    return o if (o is not None and math.isfinite(o) and o > 0) else None


def _frame_rows(raw: Any) -> pd.DataFrame | None:
    """DataFrame (timestamp sütunu ya da datetime index) ya da dict satır listesi → `timestamp`(ms)+OHLC DataFrame.
    `open` (2026-09-29): varsa taşınır (net yeniden oynatmanın `exit_fill_v1` boşluk kuralı için); yoksa None. Brüt
    etiketleyici yalnız high/low/close okur — satır kümesi ve brüt sonuç DEĞİŞMEZ (açılışı bozuk satır düşürülmez)."""
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame):
        if raw.empty or not {"high", "low", "close"} <= set(raw.columns):
            return None
        df = raw
        if "timestamp" in df.columns:
            ts = [_to_ms(v) for v in df["timestamp"].tolist()]
        else:
            ts = [_to_ms(v) for v in df.index.tolist()]
        ops = df["open"].tolist() if "open" in df.columns else [None] * len(ts)
        rows = [{"timestamp": t, "open": o, "high": h, "low": lo, "close": c}
                for t, o, h, lo, c in zip(ts, ops, df["high"].tolist(), df["low"].tolist(), df["close"].tolist())]
    elif isinstance(raw, (list, tuple)):
        rows = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            t = _to_ms(r.get("timestamp", r.get("open_time", r.get("ts", r.get("t")))))
            rows.append({"timestamp": t, "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
                         "close": r.get("close")})
    else:
        return None
    clean = []
    for r in rows:
        try:
            t, h, lo, c = r["timestamp"], float(r["high"]), float(r["low"]), float(r["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if t is None or not (math.isfinite(h) and math.isfinite(lo) and math.isfinite(c)):
            continue
        clean.append({"timestamp": int(t), "open": _open_px(r.get("open")), "high": h, "low": lo, "close": c})
    if not clean:
        return None
    df = pd.DataFrame(clean).drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    return df


def _frame_for(frames: dict[str, Any] | None, tf_minutes: int) -> Any:
    if not frames:
        return None
    key = _TF_BY_MIN.get(int(tf_minutes))
    raw = frames.get(key) if key is not None else None
    if raw is None:
        raw = frames.get(int(tf_minutes))
    if raw is None:
        raw = frames.get(str(tf_minutes))
    return raw


def _eval_view(t: ShadowTrade) -> ShadowTrade:
    """Etiketleme görünümü: TARGET_STOP_TIME kaydın kendisi; HORIZON/RULE_EXIT hedefsiz, ufuk kapanışında R
    (stop yine ÖNCE) — `label_with_candles`ın `hold_h` yolu. Kaydın kendi varyantı değişmez."""
    if str(t.label_kind or LABEL_TARGET_STOP_TIME) == LABEL_TARGET_STOP_TIME:
        return t
    return replace(t, variant="hold_h")


def _may_finalise(v: ShadowTrade, arr: tuple, created_ms: int, label_ms: int) -> bool:
    """Ucuz ön kontrol (muhafazakâr ÜST küme): yolda stop ya da hedef seviyesine değen bar var mı. False → tam yürütme
    `horizon` dışında bir sonuç VEREMEZ (ufuk dolmadıysa kesinleşmez), yürütmeye gerek yok."""
    if v.variant not in ("as_planned", "hold_h"):
        return True
    ts, hi, lo = arr
    i0 = int(np.searchsorted(ts, created_ms, side="left"))          # ⊇ `ts > created`
    i1 = int(np.searchsorted(ts, label_ms, side="right"))           # `ts <= label`
    if i1 <= i0:
        return False
    h, lw = hi[i0:i1], lo[i0:i1]
    long = v.direction == "LONG"
    if bool((lw <= v.stop).any()) if long else bool((h >= v.stop).any()):
        return True
    tg = list(v.targets or []) if v.variant != "hold_h" else []
    if tg:
        return bool((h >= min(tg)).any()) if long else bool((lw <= max(tg)).any())
    return False


# ============================================================================ NET ETİKET (2026-09-29, maliyet sapması)
@dataclass(frozen=True)
class ExecModel:
    """Defterin yürütme modeli — `of_ledger` ile defter NESNESİNDEN kopyalanır (config'ten yeniden yazılmaz): ücret tablosu,
    kayma, TP1 oranı, MFE başa-baş eşiği, TP maker bayrağı, likidasyon parametreleri, kaldıraç kademeleri, en-kötü-durum
    bayrağı ve funding takvimi (saatler, bağlı kaynağın `hours_for`u, son orana düşme bayrağı)."""
    fees: FeeSchedule
    slippage: SlippageModel
    tp1_fraction: Decimal
    breakeven_at_mfe_r: Decimal
    tp_maker: bool
    liq_params: LiquidationParams
    brackets: tuple | None
    worst_case: bool
    funding_hours_utc: tuple
    funding_fallback: bool
    funding_hours_for: Any = None

    @classmethod
    def of_ledger(cls, led: Any) -> "ExecModel":
        fd = getattr(led, "funding", None) or FundingSchedule()
        lp = led.liq_params
        return cls(fees=FeeSchedule.from_dict(led.fees.to_dict()),
                   slippage=SlippageModel(fixed_bps=D(led.slippage.fixed_bps), spread_half=bool(led.slippage.spread_half)),
                   tp1_fraction=D(led.tp1_fraction), breakeven_at_mfe_r=D(led.breakeven_at_mfe_r), tp_maker=bool(led.tp_maker),
                   liq_params=LiquidationParams(liq_fee_pct=D(lp.liq_fee_pct), fee_cushion_pct=D(lp.fee_cushion_pct),
                                                use_brackets=bool(lp.use_brackets)),
                   brackets=tuple(led.brackets) if led.brackets else None, worst_case=bool(led.worst_case),
                   funding_hours_utc=tuple(fd.hours_utc), funding_fallback=bool(fd.fallback_to_last_known),
                   funding_hours_for=fd.hours_for_symbol)

    def new_ledger(self) -> FuturesLedgerV2:
        """Atılık defter (yalnız bu kayıt için; hiçbir yere yazılmaz). Özkaynak sınırsız, adet tavanı yok."""
        return FuturesLedgerV2(Decimal("1e12"), max_positions=1, enforce_position_cap=False,
                               fees=FeeSchedule.from_dict(self.fees.to_dict()),
                               slippage=SlippageModel(fixed_bps=self.slippage.fixed_bps, spread_half=self.slippage.spread_half),
                               brackets=list(self.brackets) if self.brackets else None,
                               liq_params=LiquidationParams(liq_fee_pct=self.liq_params.liq_fee_pct,
                                                            fee_cushion_pct=self.liq_params.fee_cushion_pct,
                                                            use_brackets=self.liq_params.use_brackets),
                               funding=FundingSchedule(hours_utc=tuple(self.funding_hours_utc),
                                                       fallback_to_last_known=self.funding_fallback,
                                                       hours_for_symbol=self.funding_hours_for),
                               tax_policy=TaxPolicy.disabled(), tp1_fraction=self.tp1_fraction,
                               breakeven_at_mfe_r=self.breakeven_at_mfe_r, worst_case=self.worst_case, tp_maker=self.tp_maker,
                               entries_keep=64, history_keep=4)

    def to_dict(self, filters: SymbolFilters | None = None) -> dict[str, Any]:
        return {"contract": NET_CONTRACT, "intrabar_path": INTRABAR_PATH, "stop_fill": STOP_FILL_RULE,
                "taker_pct": float(self.fees.taker_pct), "maker_pct": float(self.fees.maker_pct),
                "fee_source": str(self.fees.source), "slippage_bps": float(self.slippage.fixed_bps), "tp_maker": self.tp_maker,
                "tp1_fraction": float(self.tp1_fraction), "breakeven_at_mfe_r": float(self.breakeven_at_mfe_r),
                "filters_source": (str(filters.source) if filters is not None else None),
                "price_tick": (float(filters.price_tick) if filters is not None else None)}


class _PeekRates:
    """Canlı `FundingRates` için YAN ETKİSİZ oran okuyucu: `peek` (istenen an kaydedilmez, sayaç artmaz) → karşı-olgusal
    yeniden oynatma funding ağ adımını TETİKLEMEZ. Settlement mark'ı ve dayanağı kaynaktan aynen iletilir."""

    def __init__(self, src: Any) -> None:
        self._src = src

    def __call__(self, symbol: str, when: datetime):
        return self._src.peek(symbol, when)

    def settlement_mark(self, symbol: str, when: datetime):
        return self._src.settlement_mark(symbol, when)

    def mark_basis(self, symbol: str, when: datetime) -> str:
        fn = getattr(self._src, "mark_basis", None)
        return str(fn(symbol, when)) if callable(fn) else "SETTLEMENT_ROW"


def _quiet_rates(src: Any) -> Any:
    if src is None:
        return None
    if callable(getattr(src, "peek", None)) and callable(getattr(src, "settlement_mark", None)):
        return _PeekRates(src)
    if callable(getattr(src, "peek", None)):
        return src.peek
    return src


def _replay_filters(symbol: str, filters: SymbolFilters | None) -> SymbolFilters:
    f = filters if isinstance(filters, SymbolFilters) else default_filters(symbol, MarketType.USDM_PERP)
    if str(getattr(f, "source", "") or "") in ("", "default"):
        return SymbolFilters(symbol=symbol, market_type=MarketType.USDM_PERP, price_tick=Decimal("0"),
                             qty_step=Decimal("0.00000001"), min_qty=Decimal("0"), max_qty=Decimal("1e15"),
                             market_max_qty=Decimal("1e15"), min_notional=Decimal("0"), max_leverage=125,
                             source=NEUTRAL_FILTERS_SOURCE)
    return f


def _replay_notional(f: SymbolFilters, entry: Decimal) -> Decimal:
    """R'yi değiştirmeyen boyut: en az 1000 USDT; en az ~1000 adım (TP1 kısmi miktarının adım yuvarlaması ≤ %0,1 kalsın);
    min tutar / min miktarın 2 katı. Kaldıraç 1 (R kaldıraçtan bağımsız; likidasyon stopun çok ötesinde)."""
    return max(NET_NOTIONAL_USDT, D(f.min_notional) * 2, D(f.qty_step) * entry * 1000, D(f.min_qty) * entry * 2)


def _r6(x: float) -> float:
    return round(float(x), 6) + 0.0                  # −0,0 yazılmaz


def _bar_path(long: bool, op: float | None, hi: float, lo: float, cl: float) -> list[tuple[str, float]]:
    """BAR İÇİ YOL (2026-09-29, `cf_net_ledger_replay_v2`): açılış → TERS uç → LEHTE uç → kapanış. Bar içi sıra gözlenmez;
    ters ucun önce gelmesi İLK stop için İHTİYATLIDIR (brüt etiketleyicinin "önce stop" kuralıyla aynı) ve her nokta
    kendinden önceki noktalardan SONRA gerçekleşmiştir: bir noktanın taşıdığı stop (MFE başa-baş / TP1) yalnız SONRAKİ
    noktalarla sınanır. Lehte ucun taşıdığı stop için bu sıra ihtiyatlı DEĞİLDİR (aynı barın ters ucu yeni stopun
    ötesindeyse sıra belirsiz; `_net_replay` böyle barları `intrabar_ambiguous_bars` ile sayar). Açılışı okunamayan barda
    yol ters uçtan başlar."""
    adv, fav = (lo, hi) if long else (hi, lo)
    pts = [("open", op)] if op is not None else []
    return pts + [("adverse", adv), ("favourable", fav), ("close", cl)]


def _beyond(long: bool, level: Decimal, px: Decimal) -> bool:
    return px <= level if long else px >= level


def _decompose(rec: Any, view: ShadowTrade, r_gross: float) -> dict[str, float]:
    """r_gross − r_net'in parçaları (POZİTİF = maliyet; toplamları `cost_r`). Kimlik sırası: yol (seviyede dolum, başa-baş
    fiyatı, MFE başa-baş, TP1 miktar yuvarlaması, zaman çıkışı) → çıkış dolum modeli (v3, 2026-09-29: yalnız bar açılışı
    boşluğu; v2: boşluk/kapanış) → giriş dolumu (kayma + tick; payda da dolumdan) → çıkış kayması → ücretler → funding."""
    side = 1.0 if str(view.direction).upper() == "LONG" else -1.0
    fills = list(rec.fills)
    ent = [f for f in fills if f.kind == "entry"][0]
    exits = [f for f in fills if f.kind != "entry"]
    q0 = float(rec.quantity)
    e_ref, e_fill = float(ent.ref_price if ent.ref_price is not None else ent.price), float(ent.price)
    stop0 = float(view.stop)
    risk_ref, risk_fill = abs(e_ref - stop0) * q0, abs(e_fill - stop0) * q0

    def ref_of(f) -> float:
        return float(f.ref_price if f.ref_price is not None else f.price)
    g_rr = sum(side * (ref_of(f) - e_ref) * float(f.qty) for f in exits)
    g_rf = sum(side * (ref_of(f) - e_fill) * float(f.qty) for f in exits)
    g_level = g_rr
    ef = (rec.features or {}).get("exit_fill") or {}
    if exits and rec.exit_reason in (EXIT_STOP, EXIT_BE_STOP) and ef.get("stop") is not None and exits[-1].ref_price is not None:
        g_level = g_rr - side * (float(exits[-1].ref_price) - float(ef["stop"])) * float(exits[-1].qty)
    gross, fees, fund = float(rec.gross_pnl), float(rec.fees), float(rec.funding)
    a0, a = g_level / risk_ref, g_rr / risk_ref
    b, c = g_rf / risk_fill, gross / risk_fill
    dd, e = (gross - fees) / risk_fill, (gross - fees + fund) / risk_fill
    return {"path": _r6(r_gross - a0), "exit_fill_model": _r6(a0 - a), "entry_fill": _r6(a - b), "exit_slippage": _r6(b - c),
            "fees": _r6(c - dd), "funding": _r6(dd - e)}


def net_outcome(view: ShadowTrade, df: pd.DataFrame, gross: dict[str, Any], *, model: ExecModel,
                filters: SymbolFilters | None = None, funding_lookup: Any = None) -> dict[str, Any]:
    """Kesinleşmiş brüt etiketin NET karşılığı (`cf_net_ledger_replay_v2`, 2026-09-29): sinyal atılık bir `FuturesLedgerV2`de
    (defterin kendi modeli) `open()` ile açılır — giriş `market_fill_price`tan (kayma + agresif tick; kayıt zaten dolum
    fiyatıyla yazıldıysa, `features.entry_ref == "quantized_entry"`, İKİNCİ kez kaydırılmaz) — ve brüt etiketleyicinin
    yürüdüğü AYNI kapanmış barlarda (`ts ∈ (created, label_ts]`, en fazla brüt `bars` kadar) her bar `_bar_path` sırasıyla
    (açılış → ters uç → lehte uç → kapanış) AYRI fiyat-yalnız tick'lerle yürünür: TP1, başa-baş fiyatı (ücret dahil), MFE
    başa-baş ve funding GERÇEK defterin uyguladığı gibidir, ama bir noktanın taşıdığı stop yalnız SONRAKİ noktalarla sınanır.
    Stop dolumu (`STOP_FILL_RULE`): stopa değen/geçen nokta SEVİYEDEN (+ çıkış kayması) dolar — canlı 60 sn izleyicinin
    ulaştığı dolum; yalnız bar stopun ötesinde AÇILIRSA (ilk bar hariç) dolum açılıştandır (gerçek boşluk). Son bardan sonra
    hâlâ açıksa kuralın zaman çıkışı (Box gün sonu / zaman stopu / HORIZON) = `close_manual(son kapanış)` (kayma + taker
    ücreti). `r_net` = kaydın `r_multiple`ı (net / |dolum − ilk stop| × miktar). HORIZON türleri (`hold_h` görünümü)
    hedefsizdir. Hata/ret → `r_net` None ve `net_status` nedeni (brüt etkilenmez)."""
    r_gross = float(gross.get("r_multiple") or 0.0)
    out: dict[str, Any] = {"label_version": LABEL_VERSION, "r_gross": r_gross}
    try:
        out.update(_net_replay(view, df, gross, r_gross, model=model, filters=filters, funding_lookup=funding_lookup))
    except Exception as exc:  # noqa: BLE001 — net yeniden oynatma arızası brüt etiketi ETKİLEMEZ
        out.update({"r_net": None, "net_status": "NET_ERROR:%s" % type(exc).__name__})
    return out


def _net_replay(view: ShadowTrade, df: pd.DataFrame, gross: dict[str, Any], r_gross: float, *, model: ExecModel,
                filters: SymbolFilters | None, funding_lookup: Any) -> dict[str, Any]:
    if view.variant not in ("as_planned", "hold_h"):
        return {"r_net": None, "net_status": "VARIANT_UNSUPPORTED:%s" % view.variant}
    sym = str(view.symbol)
    f = _replay_filters(sym, filters)
    xm = model.to_dict(f)
    entry, stop = D(float(view.entry)), D(float(view.stop))
    targets = [] if view.variant == "hold_h" else [D(float(t)) for t in (view.targets or [])]
    created, label_ts = from_iso(view.created_at), from_iso(view.label_ts)
    led = model.new_ledger()
    # FORMASYON qmark (2026-09-29): kayıt girişi defterin `market_fill_price`ından geçmiş DOLUM fiyatıysa ikinci kez kayma +
    # tick yuvarlaması uygulanmaz (dolum = kayıt girişi; ızgaradaki fiyatın agresif yuvarlaması kendisidir).
    pre_filled = str(((view.features or {}) if isinstance(view.features, dict) else {}).get("entry_ref") or "") == \
        "quantized_entry"
    pos = led.open(sym, view.direction, entry, SizeSpec(_replay_notional(f, entry), AmountType.NOTIONAL, 1), stop=stop,
                   targets=targets, filters=f, now=created, slippage=SlippageModel.zero() if pre_filled else None)
    if pos is None:
        return {"r_net": None, "net_status": "OPEN_REJECTED:%s" % (led.last_reject_reason or "?"), "exec_model": xm}
    tf_ms = int(view.tf_minutes) * 60_000
    ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    path = df.loc[((ts > created) & (ts <= label_ts)).values]
    n_bars = int(gross.get("bars") or len(path))
    path = path.iloc[:max(0, n_bars)]
    lookup = _quiet_rates(funding_lookup)
    long = str(view.direction).upper() == "LONG"
    rec, last_c, last_dt = None, None, None
    opens = path["open"].tolist() if "open" in path.columns else [None] * len(path)
    first_bar = True
    ambiguous = 0
    for t, o, h, lo, c in zip(path["timestamp"].tolist(), opens, path["high"].tolist(), path["low"].tolist(),
                              path["close"].tolist()):
        odt = datetime.fromtimestamp(int(t) / 1000.0, tz=timezone.utc)
        cdt = datetime.fromtimestamp((int(t) + tf_ms) / 1000.0, tz=timezone.utc)
        # BAR İÇİ NEDENSEL YOL (2026-09-29): her nokta ayrı fiyat-yalnız tick; açılış barın açılış anında, diğerleri
        # kapanış anında (funding: açılışta boşlukla kapanan pozisyon bar içi settlement ödemez). Stopun ötesindeki nokta
        # SEVİYEYE kırpılır (defter orada `seviye + kayma` doldurur) — boşluk yalnız ilk olmayan barın açılışında.
        stop_before_fav = None
        for i, (point, px) in enumerate(_bar_path(long, _open_px(o), float(h), float(lo), float(c))):
            p = led.positions.get(sym)
            if p is None:
                break
            if point == "favourable":
                stop_before_fav = p.stop
            at = odt if point == "open" else cdt
            price, rule = D(float(px)), None
            if p.stop is not None and _beyond(long, p.stop, price):
                if point == "open" and not first_bar:
                    rule = "GAP_FILL_AT_BAR_OPEN"
                else:
                    price, rule = p.stop, "STOP_AT_LEVEL"
            recs = led.tick({sym: TickData(last=price, mark=price, ts=iso(at))}, now_utc=at, funding_rate_lookup=lookup,
                            bar_advance=(i == 0))
            if recs:
                rec = recs[-1]
                ef = (rec.features or {}).get("exit_fill")
                if rule is not None and isinstance(ef, dict) and rec.exit_reason in (EXIT_STOP, EXIT_BE_STOP):
                    ef.update({"basis": rule, "path_point": point, "path_price": str(D(float(px))),
                               "fill_rule": STOP_FILL_RULE, "net_contract": NET_CONTRACT})
                break
        last_c, last_dt = float(c), cdt
        first_bar = False
        if rec is not None:
            break
        # BELİRSİZ BAR (2026-09-29, inceleme bulgusu): lehte uç stopu taşıdı (başa-baş / TP1) ve aynı barın ters ucu yeni
        # stopun ötesinde → "lehte uç → ters uç" sırasında pozisyon bu barda seviyeden kapanırdı; yol "sürer" dalını aldı.
        p = led.positions.get(sym)
        if (p is not None and p.stop is not None and stop_before_fav is not None and p.stop != stop_before_fav
                and _beyond(long, p.stop, D(float(lo) if long else float(h)))):
            ambiguous += 1
    basis = None
    if rec is None:
        if last_c is None:
            return {"r_net": None, "net_status": "NO_BARS", "exec_model": xm}
        rec = led.close_manual(sym, D(last_c), reason="horizon", now=last_dt)
        basis = "HORIZON_CLOSE" if str(gross.get("exit_reason")) == "horizon" else "CLOSED_AT_GROSS_EXIT_BAR"
    if basis is None:
        basis = ((rec.features or {}).get("exit_fill") or {}).get("basis") or "TARGET_AT_LEVEL"
    r_net = _r6(float(rec.r_multiple))
    parts = _decompose(rec, view, r_gross)
    ent = [x for x in rec.fills if x.kind == "entry"][0]
    cov = (rec.features or {}).get("funding_coverage") or {}
    return {"r_net": r_net, "cost_r": _r6(r_gross - r_net), "cost_parts_r": parts, "net_exit_reason": str(rec.exit_reason),
            "net_exit_basis": str(basis), "net_exit_price": float(rec.exit_price) if rec.exit_price is not None else None,
            "entry_fill": float(ent.price), "won_net": r_net >= WON_NET_MIN_R, "veto_was_right_net": r_net <= 0,
            "funding_complete": bool(cov.get("complete")), "funding_missing": cov.get("missing"),
            "intrabar_ambiguous_bars": int(ambiguous), "net_status": "OK", "exec_model": xm}


def outcome_r(outcome: dict[str, Any] | None) -> float | None:
    """Karşı-olgusal sonucun raporlanan R'si: NET (`r_net`) varsa o, yoksa brüt `r_multiple` (v1). Araştırma eşleşmesi
    gerçek işlemin NET R'siyle aynı tabanda olsun diye (2026-09-29)."""
    o = outcome if isinstance(outcome, dict) else {}
    for k in ("r_net", "r_multiple"):
        try:
            v = float(o.get(k))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            return v
    return None


def _closed_frame(cache: dict, frames_by_symbol: dict[str, dict[str, Any]] | None, t: ShadowTrade,
                  now_ms: int) -> tuple[pd.DataFrame, tuple] | None:
    """Kaydın sembol/diliminin `now` anında KAPANMIŞ barları (önbellekli): (df, (ts, high, low) dizileri) ya da None."""
    tf_ms = int(t.tf_minutes) * 60_000
    ck = (t.symbol, int(t.tf_minutes))
    if ck not in cache:
        df = _frame_rows(_frame_for((frames_by_symbol or {}).get(t.symbol), t.tf_minutes))
        if df is not None:
            df = df[df["timestamp"] + tf_ms <= now_ms].reset_index(drop=True)   # yalnız kapanmış barlar
        if df is not None and not df.empty:
            cache[ck] = (df, (df["timestamp"].to_numpy(dtype="int64"), df["high"].to_numpy(dtype=float),
                              df["low"].to_numpy(dtype=float)))
        else:
            cache[ck] = None
    return cache[ck]


def _filters_of(filters_for: Callable[[str], Any] | None, symbol: str) -> SymbolFilters | None:
    if filters_for is None:
        return None
    try:
        f = filters_for(symbol)
    except Exception:  # noqa: BLE001 — filtre okunamazsa nötr yeniden oynatma (kayıtta `filters_source`)
        return None
    return f if isinstance(f, SymbolFilters) else None


def _same_gross(res: dict[str, Any] | None, stored: dict[str, Any]) -> bool:
    if not isinstance(res, dict):
        return False
    try:
        return (abs(float(res["r_multiple"]) - float(stored.get("r_multiple"))) <= 1e-9
                and str(res.get("exit_reason")) == str(stored.get("exit_reason"))
                and int(res.get("bars") or 0) == int(stored.get("bars") or 0))
    except (TypeError, ValueError, KeyError):
        return False


def relabel_net(trades: list[ShadowTrade], frames_by_symbol: dict[str, dict[str, Any]] | None, now: datetime, *,
                exec_model: ExecModel, filters_for: Callable[[str], Any] | None = None, funding_lookup: Any = None,
                limit: int = RELABEL_MAX_PER_CALL, source: str = "lazy", _cache: dict | None = None) -> dict[str, int]:
    """TEMBEL NET DOLGU (2026-09-29): etiketli ama `label_version` alanı OLMAYAN (v1, yalnız brüt) kayıtlara net eklenir.
    Koşul: eldeki kapanmış barlar pencerenin İLK barını içeriyor ve brüt sonuç (R, çıkış, bar sayısı) AYNEN yeniden
    üretiliyor — o zaman `net_outcome` aynı barlarla koşar, sonuca `net_backfilled` = {at, source} yazılır. Pencere
    çerçeveden düşmüşse ya da brüt tutmuyorsa kayıt `label_version = cf_label_v1` + `net_status` ile işaretlenir (bir daha
    denenmez; çevrimdışı `scripts/cf_backfill_net.py` doldurur). Sembolün çerçevesi hiç yoksa dokunulmaz (sonra denenir).
    Brüt alanlar DEĞİŞMEZ. Döner: {relabeled, unavailable, skipped}."""
    now = _aware(now)
    now_ms = int(now.timestamp() * 1000)
    cache = _cache if _cache is not None else {}
    counts = {"relabeled": 0, "unavailable": 0, "skipped": 0}
    tried = 0
    for t in trades:
        o = t.outcome
        if not isinstance(o, dict) or o.get("label_version") is not None:
            continue
        if tried >= max(0, int(limit)):
            break
        hit = _closed_frame(cache, frames_by_symbol, t, now_ms)
        if hit is None:
            counts["skipped"] += 1
            continue
        tried += 1
        df, _arr = hit
        tf_ms = int(t.tf_minutes) * 60_000
        created_ms = int(from_iso(t.created_at).timestamp() * 1000)
        if int(df["timestamp"].iloc[0]) > (created_ms // tf_ms + 1) * tf_ms:
            o.update({"label_version": LABEL_VERSION_V1, "net_status": "NET_BACKFILL_WINDOW_NOT_IN_FRAME"})
            counts["unavailable"] += 1
            continue
        v = _eval_view(t)
        if not _same_gross(label_with_candles(v, df), o):
            o.update({"label_version": LABEL_VERSION_V1, "net_status": "NET_BACKFILL_GROSS_MISMATCH"})
            counts["unavailable"] += 1
            continue
        o.update(net_outcome(v, df, o, model=exec_model, filters=_filters_of(filters_for, t.symbol),
                             funding_lookup=funding_lookup))
        o["net_backfilled"] = {"at": iso(now), "source": str(source)}
        counts["relabeled"] += 1
    return counts


def approx_net_r(t: ShadowTrade, outcome: dict[str, Any], *, model: ExecModel,
                 filters: SymbolFilters | None = None) -> float | None:
    """KAPALI-BİÇİM YAKLAŞIK NET (`cf_label_v1c`, yalnız çevrimdışı dolgu; bar YOK): saklı brüt çıkışa defterin ücret/kayma
    modeli ve kendi giriş dolumu (`market_fill_price`) uygulanır. Çıkış dolum modeli (boşluk), ücret dahil başa-baş fiyatı
    (başa-baş bacağı ham girişten fiyatlanır → tahmin DÜŞÜK kalır), MFE başa-baş (kurtarılan zarar görünmez → DÜŞÜK) ve
    funding YOK. Hatalar iki yöne de gidebilir: ne alt ne üst sınırdır (2026-09-29 düzeltmesi: eskiden "üst sınır"
    deniyordu, yanlıştı). Net ortalamasına karıştırılmaz; çevrimdışı betik mum bulunca kaydı v3 ile değiştirir."""
    side = str(t.direction).upper()
    sg = 1.0 if side == "LONG" else -1.0
    f = _replay_filters(str(t.symbol), filters)
    led = model.new_ledger()
    e_fill = float(led.market_fill_price(str(t.symbol), side, D(float(t.entry)), filters=f))
    stop = float(t.stop)
    if e_fill <= 0 or e_fill == stop:
        return None
    close_side = "SELL" if side == "LONG" else "BUY"
    slip = model.slippage

    def mkt(px: float) -> float:
        return float(slip.fill_price(D(float(px)), close_side))
    tg = [] if str(t.label_kind or LABEL_TARGET_STOP_TIME) != LABEL_TARGET_STOP_TIME else [float(x) for x in (t.targets or [])]
    fr = float(model.tp1_fraction)
    ex = str(outcome.get("exit_reason") or "")
    taker, maker = float(model.fees.rate(False)), float(model.fees.rate(True))
    tp_fee = maker if model.tp_maker else taker
    legs: list[tuple[float, float, float]] = []                     # (pay, dolum, ücret oranı)
    if ex in ("target", "breakeven_stop") and len(tg) >= 2 and fr >= 1:
        legs = [(1.0, tg[0], tp_fee)]                                # TP1 tamamını kapatır (defterin kuralı)
    elif ex == "target":
        legs = ([(fr, tg[0], tp_fee), (1 - fr, tg[-1], tp_fee)] if len(tg) >= 2
                else [(1.0, tg[-1] if tg else float(outcome.get("exit_price") or 0.0), tp_fee)])
    elif ex == "breakeven_stop" and tg:
        legs = [(fr, tg[0], tp_fee), (1 - fr, mkt(float(t.entry)), taker)]
    elif ex == "stop":
        legs = [(1.0, mkt(stop), taker)]
    else:
        legs = [(1.0, mkt(float(outcome.get("exit_price") or 0.0)), taker)]
    gross = sum(w * sg * (px - e_fill) for w, px, _ in legs)
    fees = taker * e_fill + sum(w * rate * px for w, px, rate in legs)
    return _r6((gross - fees) / abs(e_fill - stop))


def label_records(trades: list[ShadowTrade], frames_by_symbol: dict[str, dict[str, Any]] | None,
                  now: datetime, *, exec_model: ExecModel | None = None, filters_for: Callable[[str], Any] | None = None,
                  funding_lookup: Any = None, _cache: dict | None = None) -> tuple[int, list[ShadowTrade]]:
    """Bekleyen kayıtları (outcome None) YALNIZ `now` anında kapanmış barlarla etiketler; kayıtları yerinde günceller.

    Kesinleşme: stop/hedef (önce stop) ya da ufuk penceresinin son barı kapanmış VE veri pencereyi kapsıyor. Ufuk +
    `STALE_GRACE_BARS` geçtiği hâlde etiketlenemeyen kayıt (veri boşluğu / evrenden çıkmış sembol) bayattır.
    Döner: (bu çağrıda etiketlenen sayı, bayat kayıtlar). Defter başı kayıtçı ve ana botun öğrenme gölgeleri ORTAK.

    `exec_model` (2026-09-29): verilirse kesinleşen her etikete `net_outcome` (v3: net R, maliyet parçaları) eklenir;
    `filters_for(symbol)` defterin filtre önbelleği, `funding_lookup` defterin gerçekleşmiş funding kaynağıdır (yan
    etkisiz okunur). None → çıktı v1 ile BİT-AYNI."""
    now = _aware(now)
    now_ms = int(now.timestamp() * 1000)
    n = 0
    stale: list[ShadowTrade] = []
    cache: dict[tuple, tuple[pd.DataFrame, tuple] | None] = _cache if _cache is not None else {}
    for t in trades:
        if t.outcome is not None:
            continue
        tf_ms = int(t.tf_minutes) * 60_000
        label_ms = int(from_iso(t.label_ts).timestamp() * 1000)
        hit = _closed_frame(cache, frames_by_symbol, t, now_ms)
        horizon_done = now_ms >= label_ms + tf_ms
        if hit is not None:
            df, arr = hit
            v = _eval_view(t)
            created_ms = int(from_iso(t.created_at).timestamp() * 1000)
            res = label_with_candles(v, df) if (horizon_done or _may_finalise(v, arr, created_ms, label_ms)) else None
            if res is not None:
                final = res.get("exit_reason") in _FINAL_EXITS
                if not final and horizon_done:
                    # ufuk penceresinin son barı kapandı; veri pencereyi kapsıyor mu (boşluk → bekle)
                    in_win = df[(df["timestamp"] <= label_ms)]
                    final = (not in_win.empty) and int(in_win["timestamp"].iloc[-1]) > label_ms - tf_ms
                if final:
                    kind = str(t.label_kind or LABEL_TARGET_STOP_TIME)
                    out = dict(res)
                    out.update({"label_kind": kind, "approx": bool(t.approx),
                                "label_method": ("PATH" if kind == LABEL_TARGET_STOP_TIME else
                                                 LABEL_HORIZON if kind == LABEL_HORIZON else "HORIZON_FALLBACK")})
                    if exec_model is not None:
                        # NET ETİKET (2026-09-29): aynı barlar, defterin kendi modeli; brüt alanlar v1 anlamıyla kalır
                        out.update(net_outcome(v, df, res, model=exec_model, filters=_filters_of(filters_for, t.symbol),
                                               funding_lookup=funding_lookup))
                    t.outcome, t.labeled_at = out, iso(now)
                    n += 1
                    continue
        if now_ms > label_ms + (STALE_GRACE_BARS + 1) * tf_ms:
            stale.append(t)
    return n, stale


class CounterfactualRecorder:
    """Defter başına karşı-olgusal kayıtçı (`state_dir/counterfactual_trades.json`)."""

    def __init__(self, path: Path | str, *, book: str, max_pending: int = 2000, archive: Any | None = None):
        self.path = Path(path)
        self.book_name = str(book)
        self.max_pending = max(1, int(max_pending))
        self.sb = ShadowBook(self.path, archive=archive)
        m = self.sb.meta
        self.dropped = int(m.get("dropped", 0) or 0)
        self.expired = int(m.get("expired", 0) or 0)
        self.recorded_total = int(m.get("recorded_total", 0) or 0)
        self.superseded = int(m.get("superseded", 0) or 0)
        #: Tembel net dolguyla (v1 → v3) yeniden etiketlenen kayıt sayısı (2026-09-29, maliyet sapması).
        self.net_backfilled = int(m.get("net_backfilled", 0) or 0)
        #: Defterin öğrenme sayaçlarının KALICI yedeği (2026-09-28, ikinci doğrulama turu): özetin `learning` alanı öğrenme
        #: kapalıyken ve eski kodda yazılmaz (sayaçlar silinirdi); bu dosyaya ise ne kapalı yol ne eski kod dokunur.
        self.book_counters: dict[str, int] = {str(k): int(v) for k, v in (m.get("book_counters") or {}).items()
                                              if isinstance(v, (int, float)) and not isinstance(v, bool)}
        self._keys: set[tuple] = {self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation)
                                  for t in self.sb.trades}
        #: Düşürülen kayıtların anahtarları (süreç içi, sınırlı): aynı bar yeniden kaydı tekrar tetiklemesin.
        self._gone: deque[tuple] = deque(maxlen=self.max_pending * 2)
        self._dirty = False

    # ------------------------------------------------------------ kimlik
    def _key(self, signal_key, symbol, direction, variation) -> tuple:
        return (self.book_name, str(signal_key), str(symbol), str(direction).upper(), str(variation or ""))

    # ------------------------------------------------------------ kayıt
    def record(self, *, signal_key: str, symbol: str, direction: str, entry: float, stop: float, targets: list[float],
               reason: str, created_at: datetime, tf_minutes: int, horizon_bars: int, label_kind: str,
               features: dict | None = None, variation: str | None = None, rule_version: str | None = None) -> bool:
        """Kaydedildiyse True. False: geçersiz geometri/girdi, neden karşı-olgusala uygun değil, ya da tekrar."""
        if not signal_key or not symbol or not counterfactual_ok(reason):
            return False
        side = str(direction or "").upper()
        if side not in ("LONG", "SHORT"):
            return False
        try:
            px, st = float(entry), float(stop)
            tf, h = int(tf_minutes), int(horizon_bars)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(px) and math.isfinite(st)) or px <= 0 or st <= 0 or px == st:
            return False
        if (side == "LONG" and st >= px) or (side == "SHORT" and st <= px):
            return False
        kind = str(label_kind or "").upper()
        if kind not in LABEL_KINDS or tf <= 0 or not isinstance(created_at, datetime):
            return False
        if kind == LABEL_TARGET_STOP_TIME and h <= 0:
            return False
        key = self._key(signal_key, symbol, side, variation)
        if key in self._keys or key in self._gone:
            return False
        tgts = []
        for t in targets or []:
            try:
                tv = float(t)
            except (TypeError, ValueError):
                continue
            if math.isfinite(tv) and tv > 0 and ((tv > px) if side == "LONG" else (tv < px)):
                tgts.append(tv)
        horizon = h if kind == LABEL_TARGET_STOP_TIME else HORIZON_BARS
        created = _aware(created_at)
        st_rec = ShadowTrade(
            id="cf_" + stable_id("cf", *key), plan_id=str(signal_key), symbol=str(symbol), market_type="USDM_PERP",
            direction=side, created_at=iso(created), entry=px, stop=st, targets=tgts, horizon_bars=int(horizon),
            variant="as_planned", reason_not_opened=[str(reason)],
            label_ts=iso(created + timedelta(minutes=tf * horizon)), tf_minutes=tf, leverage=1.0,
            book=self.book_name, signal_key=str(signal_key), variation=(str(variation) if variation else None),
            label_kind=kind, features=_clean(dict(features)) if features else None, learning_unlocked=False,
            rule_version=(str(rule_version) if rule_version else None), approx=(kind != LABEL_TARGET_STOP_TIME))
        self.sb.trades.append(st_rec)
        self._keys.add(key)
        self.recorded_total += 1
        self._dirty = True
        self._enforce_cap()
        return True

    def _enforce_cap(self) -> None:
        pending = [t for t in self.sb.trades if t.outcome is None]
        over = len(pending) - self.max_pending
        if over <= 0:
            return
        drop = {id(t) for t in pending[:over]}          # EN ESKİ bekleyenler (liste ekleme sırası)
        for t in pending[:over]:
            k = self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation)
            self._keys.discard(k)
            self._gone.append(k)
        self.sb.trades = [t for t in self.sb.trades if id(t) not in drop]
        self.dropped += over

    def has_pending(self, *, symbol: str, reason: str, hypothetical_only: bool = False) -> bool:
        """Bu sembolde `reason` nedenli, henüz ETİKETLENMEMİŞ kayıt var mı (yön/varyasyon fark etmez)? A15 "+CF": taban
        kuralın varsayımsal pozisyonu (POSITION_OPEN kaydı) sonuçlanmadan aynı hareketin yeni sinyalleri kaydedilmez
        (2026-09-28, öğrenme modu; üçüncü doğrulama turu). `hypothetical_only`: taban kapılarının durduracağı kayıtlar
        (`features.baseline_blocked_by` dolu) varsayımsal pozisyon SAYILMAZ."""
        sym, r = str(symbol), str(reason)
        return any(t.outcome is None and t.symbol == sym and list(t.reason_not_opened or [])[:1] == [r]
                   and not (hypothetical_only and (t.features or {}).get("baseline_blocked_by"))
                   for t in self.sb.trades)

    # ------------------------------------------------------------ gerçek işlemle değişim
    def supersede(self, *, signal_key: str | None, symbol: str, direction: str, variation: str | None = None) -> int:
        """Aynı sinyal (defter, anahtar, sembol, yön, varyasyon) sonradan GERÇEK işlem olarak açıldı: karşı-olgusal kaydı
        düşürülür (aynı gözlem hem dolum hem "açılmadı" olarak sayılmasın). Anahtar tekillik kümesinde KALIR (yeniden
        kaydedilmez). Döner: düşürülen kayıt sayısı."""
        if not signal_key:
            return 0
        key = self._key(signal_key, symbol, str(direction or "").upper(), variation)
        keep, n = [], 0
        for t in self.sb.trades:
            if self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation) == key:
                n += 1
            else:
                keep.append(t)
        if n:
            self.sb.trades = keep
            self.superseded += n
            self._dirty = True
        return n

    # ------------------------------------------------------------ etiketleme
    @staticmethod
    def _frame_for(frames: dict[str, Any] | None, tf_minutes: int) -> Any:
        return _frame_for(frames, tf_minutes)

    def label_pending(self, frames_by_symbol: dict[str, dict[str, Any]], now: datetime, *,
                      exec_model: ExecModel | None = None, filters_for: Callable[[str], Any] | None = None,
                      funding_lookup: Any = None) -> int:
        """Bekleyen kayıtları yalnız KAPANMIŞ barlarla etiketler (`label_records`). Döner: bu çağrıda etiketlenen sayı.
        `exec_model` (2026-09-29): yeni etiketlere NET eklenir ve eski (v1) etiketliler TEMBEL doldurulur (`relabel_net`,
        çağrı başına en çok `RELABEL_MAX_PER_CALL`); None → eski davranış (bit-aynı)."""
        cache: dict = {}
        n, stale = label_records(self.sb.trades, frames_by_symbol, now, exec_model=exec_model, filters_for=filters_for,
                                 funding_lookup=funding_lookup, _cache=cache)
        if n:
            self._dirty = True
        if exec_model is not None:
            rc = relabel_net(self.sb.trades, frames_by_symbol, now, exec_model=exec_model, filters_for=filters_for,
                             funding_lookup=funding_lookup, _cache=cache)
            if rc["relabeled"] or rc["unavailable"]:
                self.net_backfilled += rc["relabeled"]
                self._dirty = True
        if stale:
            gone = {id(t) for t in stale}
            for t in stale:
                k = self._key(t.signal_key or t.plan_id, t.symbol, t.direction, t.variation)
                self._keys.discard(k)
                self._gone.append(k)
            self.sb.trades = [t for t in self.sb.trades if id(t) not in gone]
            self.expired += len(stale)
            self._dirty = True
        return n

    @staticmethod
    def _eval_view(t: ShadowTrade) -> ShadowTrade:
        return _eval_view(t)

    # ------------------------------------------------------------ kalıcılık / rapor
    def sync_book_counters(self, counters: dict | None) -> dict[str, int]:
        """Defter sayaçlarını yedekle birleştirir (anahtar başına EN BÜYÜK — sayaçlar yalnız artar) ve birleşik sözlüğü
        döner; yedek değiştiyse kayıt kirlenir (sonraki `save` yazar)."""
        merged = dict(self.book_counters)
        for k, v in (counters or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            merged[str(k)] = max(int(v), int(merged.get(str(k), 0)))
        if merged != self.book_counters:
            self.book_counters = merged
            self._dirty = True
        return dict(merged)

    def save(self) -> None:
        """Tek atomik yazım (arşiv taşması `ShadowBook.save` kurallarıyla). Değişiklik yoksa dokunmaz."""
        if not self._dirty and self.path.exists():
            return
        self.sb.meta = {"schema_version": SCHEMA_VERSION, "book": self.book_name, "dropped": self.dropped,
                        "expired": self.expired, "recorded_total": self.recorded_total, "superseded": self.superseded}
        if self.net_backfilled:
            self.sb.meta["net_backfilled"] = self.net_backfilled
        if self.book_counters:
            self.sb.meta["book_counters"] = dict(self.book_counters)
        self.sb.save()
        self._dirty = False

    def stats(self) -> dict:
        pending = sum(1 for t in self.sb.trades if t.outcome is None)
        out = {"book": self.book_name, "pending": pending, "labeled": len(self.sb.trades) - pending,
               "dropped": self.dropped, "expired": self.expired, "recorded_total": self.recorded_total,
               "superseded": self.superseded, "max_pending": self.max_pending}
        out.update(net_stats(t.outcome for t in self.sb.trades))
        out["net_backfilled"] = self.net_backfilled
        return out


def _fin(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def net_stats(outcomes: Any) -> dict[str, Any]:
    """Etiketli karşı-olgusalların R özeti (2026-09-29): raporlanan taban NET (`r_net`, v2/v3); brüt (`r_multiple`) yalnız
    bilgi. `n_gross_only`: net'i olmayan etiketli kayıt (v1 / net hatası); v1c tahminleri net ortalamasına KARIŞMAZ."""
    gross, net, cost, fin = [], [], [], 0
    lab = amb = 0
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        lab += 1
        g, n_ = _fin(o.get("r_multiple")), _fin(o.get("r_net"))
        if g is not None:
            gross.append(g)
        if n_ is not None:
            net.append(n_)
            c = _fin(o.get("cost_r"))
            if c is not None:
                cost.append(c)
            if o.get("funding_complete") is False:
                fin += 1
            if (o.get("intrabar_ambiguous_bars") or 0) > 0:
                amb += 1                               # (2026-09-29) bar içi sırası belirsiz net etiket (bkz. `_bar_path`)

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None
    return {"r_basis": "net", "n_net": len(net), "mean_r_net": mean(net), "mean_r_gross": mean(gross),
            "mean_cost_r": mean(cost), "n_gross_only": lab - len(net), "n_net_funding_incomplete": fin,
            "n_net_intrabar_ambiguous": amb}


__all__ = ["CounterfactualRecorder", "ExecModel", "HORIZON_BARS", "INTRABAR_PATH", "LABEL_HORIZON", "LABEL_KINDS",
           "LABEL_RULE_EXIT", "LABEL_TARGET_STOP_TIME", "LABEL_VERSION", "LABEL_VERSION_V1", "LABEL_VERSION_V1C",
           "LABEL_VERSION_V2", "NET_CONTRACT", "SCHEMA_VERSION", "STOP_FILL_RULE", "WON_NET_MIN_R", "approx_net_r",
           "label_records", "net_outcome", "net_stats", "outcome_r", "relabel_net"]

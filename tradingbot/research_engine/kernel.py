"""P3b — araştırma çekirdeği (docs/ENGINE_BRAIN_V1.md §2.1, §3.3; mühür `WF_PROTOCOL_V1.kernel`, `LIB_V1.sizing`).

Bir `strategy_lib` varyantını tek bir sembolün kapanmış barlarında oynatır; ağsız, saatsiz, belirlenimci (aynı girdi →
bayt-aynı sonuç; test). Kurallar:

* **Karar bar kapanışında**, yalnız o anda KAPANMIŞ barlarla (`timestamp + tf ≤ now_ms`); pozisyon yokken `signals`,
  varken `manage`. Bar başına tek eylem (canlı turdaki gibi). **Dolum sonraki barın açılışında ± kayma**; o bar karar
  anında açılmıyorsa (veri boşluğu: sonraki satırın açılışı ≠ karar barı + tf) giriş `ENTRY_DATA_GAP` ile reddedilir
  (saatler sonraki bir açılıştan dolum yok).
* **Giriş kapıları** (canlı `apply_action` / `PatternBook._try_open` sırasıyla, dolumdan önceki açılış fiyatıyla): giriş
  kayması (`EntryRef.max_drift_pct`), stop doğru tarafta (`STRATEGY_BAD_STOP`), kovalama sınırı (`CHASE_LIMIT`), test
  edilmiş risk aralığı (`RISK_OUTSIDE_TESTED_RANGE`), maliyet sonrası R/R tabanı (`RR_BELOW_MIN_AT_ENTRY`; Formasyon,
  canlı `pattern_trader.strategy.rr_after_cost`), aynı sinyalle tek giriş. R'den hedef GİRİŞTEN ölçülür.
* **MOVE_STOP** yalnız SIKILAŞTIRIR ve fiyatı geçemez: kararın anındaki fiyat (karar barının kapanışı) LONG'da yeni
  stopun üstünde, SHORT'ta altında kalmalı; gevşeten ya da fiyatı geçen istek uygulanmaz, işleme `MOVE_STOP_IGNORED`
  işareti (ve nedeni) yazılır. Eylemin işaretleri (`Action.flags`; ör. H'nin `F8_UNMEASURABLE`'ı) işleme bir kez
  yazılır; karar değişmez.
* **Kapı dışı işaretler** (mühürlü `WF_PROTOCOL_V1.gate_exclusions`, 2026-10-12 P3b-2b): `FUNDING_GAP`, `PATH_1H_GAP`,
  `F8_UNMEASURABLE` taşıyan işlem kapı istatistiklerinden çıkar (sayısı gösterilir); `gate_excluded(işlem)` tek
  uygulayıcıdır (tüketen kapı kodu P3d/P3e).
* **Çıkış yolu = canlı defterin çıkış sözleşmesi** (`kernel_v2`; sahip kararı 2026-10-12, mühür
  `WF_PROTOCOL_V1.kernel.stop_fill`): stop/likidasyon hükmü canlı defterin SAF fonksiyonu
  `accounting.futures_ledger.exit_decision`'ın KENDİSİDİR (`EXIT_FILL_CONTRACT` = `exit_fill_v1`; yeniden yazılmaz,
  çağrılır), canlı defterin gördüğü tiklerde: (1) pozisyon açıkken her sinyal barının AÇILIŞINDA fiyat-yalnız tik (tur
  mark'ı; açılış stopun/likidasyonun ötesindeyse dolum açılıştan — boşluk); (2) sonra barın **1h alt barları** (canlı
  `strategy_paper.BAR_TIMEFRAME`; `apply_closed_bars` gibi alt bar başına bir OHLC tiki: açılış → en kötü uç → kapanış).
  Sinyal dilimi ≤ 1h ise (Box 5m) tik sinyal barının kendisidir: canlı Box defteri 60 sn mark tikleri + 1h barlarla
  korunur; 1m veri olmadan 60 sn gözlem kurulamaz, eldeki en ince bar 5m'dir. Tik içinde `exit_decision` sırası: ilk gözlem
  likidasyonun ötesinde → LİKİDASYON; ilk gözlem stopun ötesinde → STOP ilk gözlemden; en kötü uç iki seviyeyi de
  geçmişse → LİKİDASYON (stop daha yakınsa da: `INTRABAR_ORDER_UNOBSERVED`, ihtiyatlı); yalnız stop → STOP seviyeden,
  **kapanış da stopun ötesindeyse kapanıştan** (`STOP_CLOSE_BEYOND_LEVEL_PRUDENT`); kayma dolumu likidasyonun ötesine
  iterse LİKİDASYON. Çıkış yoksa hedefler aynı tikin lehte ucuyla (aynı tikte stop ve hedef → STOP); hedef seviyede dolar
  (kayma yok, taker ücret); birden çok hedefte ilk hedef yarısını kapatır ve stop ücret+kayma dahil başa-başa çekilir
  (`FuturesLedgerV2.break_even_price` formülü) — yeni stop yalnız SONRAKİ tiklerle sınanır. Giriş barında açılış girişin
  kendisidir (yalnız alt bar tikleri). **Giriş saati** (2026-10-12 okunuşu, mühürlü `WF_PROTOCOL_V1.kernel.entry_hour`):
  girişi içeren 1h saat dilimindeki ([⌊giriş / 1h⌋ × 1h, +1h)) her tikte YALNIZ stop/likidasyon hükmü uygulanır, hedef
  kredisi verilmez — canlıda o saatin 1h barı `apply_closed_bars`'a girmez (açılıştan önce açılmıştır; saati 60 sn izleyici
  örter) ve 1h barının lehte ucu canlının görmediği bir fiyattır; tek-tik yedeğinde (alt bar yok) tik giriş saatini
  içerdiğinden bütün bar böyle işlenir. Hiçbir zaman iyimser değildir (hedef bir sonraki tikte hâlâ oradaysa alınır).
  **Hız:** stop/likidasyon/sıradaki hedef aralığına değmeyen sinyal barında alt bar açılmaz (alt barlar barın aralığının
  içindedir → hiçbir tik bir şey tetikleyemez; fiyat yolu ve R aynı). Alt barlar barı tam ve bitişik örtmüyor ya da
  barın aralığını aşıyorsa o bar tek OHLC tiki olarak işlenir ve işlem `PATH_1H_GAP` işaretlenir (`FUNDING_GAP` gibi kapı
  istatistiğinden çıkar; sayısı gösterilir) — YALNIZ çıkış kararı gereken (değen) barda: değmeyen barın eksik alt barı
  sonucu değiştiremez ve işaret koymaz. Bu yüzden kısaltma her barı açan koşuyla ancak 1h verisi TAMKEN bayt-aynıdır;
  değmeyen bir barın 1h verisi eksikse işlemler, bacaklar ve R yine aynıdır, yalnız her barı açan koşu o işleme
  `PATH_1H_GAP` koyardı (test). Kural CLOSE'u ve zaman stopu sonraki açılışta piyasa emriyle; aynı açılışta önce açılış
  tiki (boşluk) işlenir.
* **Fonlama** deponun GERÇEK uzlaşma satırlarında (`SeriesData.funding` = `[(ts, rate, mark)]`, depodaki `funding_frame`
  biçimi); damga milisaniye kaymasından arındırılıp NOMİNAL dakikaya yuvarlanır (ör. 08:00:00.003 → 08:00; bar
  açılışında dolan girişe o anın uzlaşması yazılmaz). Her uzlaşma s için −yön × açık miktar × mark × oran; açık miktar
  s'den ÖNCE KESİN kapanmamış bacaklardan (`accounting.funding.qty_open_at` sözleşmesi). Bacağın en erken anı: ilk
  gözlemde (açılış tiki ya da alt barın açılışı) o an, bar içi noktalarda (ters/lehte/kapanış) gözlem barının — alt bar
  açıldıysa 1h alt barın — AÇILIŞI (çıkışın bar içinde ne zaman olduğu bilinmez), veri sonunda (END) son kapanış.
  **İhtiyatlı kural (çıkış barındaki uzlaşma):** bacak s'den önce
  gerçekleşmiş OLABİLİYORSA s onun miktarına yazılmaz — s, bar içi bir bacağın barının içindeyse (açılış < s < kapanış)
  belirsizdir: yazılmaz, işlem `FUNDING_EXIT_BAR` işaretlenir ve yazılmayan tutar (`funding_ambiguous_usdt`) ayrıca
  tutulur (bilgi; kapıdan çıkarmaz); s = barın açılışı ise bacak ondan önce olamaz → yazılır; s = barın kapanışı ise
  bacak kesin önce → yazılmaz. Seri pencereyi örtmüyorsa, pencerede okunamaz satır ya da YEREL aralığın (aralığın iki
  yanındaki komşu aralıkların büyüğü; ayrıca iki yandaki üçer aralığın medyanlarının büyüğü) 1,5 katından uzun bir
  boşluk varsa işlem `FUNDING_GAP` işaretlenir (kapı istatistiğinden çıkar; sayısı gösterilir, §3.3). Yerel ölçü,
  8h → 4h'e geçen bir serinin 8h dönemini boşluk saymaz.
* **Büyüklük** sabit 1 USDT risk (200 USDT × %0,5; `LIB_V1.sizing`), canlı `learning_mode.fit_size` ile: kaldıraç
  büyüklükten türer (gerektiği kadar), likidasyon mesafesi ≥ `min_liq_distance_stop_mult` (2) × stop; likidasyon fiyatı
  defterin **bracket formülü** (`accounting.liquidation_price` + `bracket_for`); gerçek bracket'la mesafe tutmazsa
  kaldıraç düşürülür. Likidasyon açılış boşluğunda (ya da kayma dolumu likidasyonun ötesine iterse) olur; sonuç defterin
  `liquidation_outcome` aritmetiğidir (izole marjla sınırlı kayıp + %0,5 likidasyon ücreti). Not: bu büyüklükte (notional
  ≤ 200 × 125) bracket hep birincidir (mmr %0,4 < likidasyon ücreti %0,5) → likidasyon kaybı hep izole marja kırpılır;
  likidasyon FİYATI yalnız tetiklenmeyi belirler (testte defterle aynı fiyat, sınırın iki yanı); kırpılmayan dalın
  aritmetiği ayrıca bracket 2'de defterle eşitlenir.
* **Üç maliyet senaryosu** (`WF_PROTOCOL_V1.thresholds.costs`: base / ADVERSE / STRESS) AYNI yol üzerinde aritmetik
  yeniden maliyetlendirmeyle: giriş = açılış × (1 + yön × kayma) + yön × spread_tick × tick; piyasa çıkışı = referans ×
  (1 − yön × kayma) − yön × spread_tick × tick; hedef çıkışı = seviye − yön × spread_tick × tick (kayma yok); ücret =
  taker % × her bacağın notional'ı; fonlama her senaryoda aynı (gerçek). R = net / (|giriş dolumu − ilk stop| × miktar)
  (defter `TradeRecord.r_multiple` tanımı). Yol (çıkış olayları, kaldıraç, likidasyon fiyatı) base senaryosundan gelir.

* **Örtüler ve paylaşılan akış (P3b-2b).** `make_variant("O_…")` örtüyü mühürlü satırından (taban parametreleri,
  `copy_books.<taban>.max_hold`) kurar; `make_family(taban)` tabanı ve yedi örtüsünü TEK paylaşılan akışla kurar
  (`strategy_lib.stream`): çekirdek her koşunun başında varyantın `bind_series(seri)`'sini çağırır, aynı seri nesnesinde
  tabanın `signals`/`manage`'i karar anı başına bir kez hesaplanır (bayt-aynılık testli); `run_lockstep` ailenin
  koşularını bar bar sırayla ilerletir (aynı çağrılar; canlı içerik adresli önbellekler — T2/M2'nin yapı analizi — aynı
  bar için sıcak kalır; bayt-aynılık testli). `F_BTC` için `SeriesData.btc_4h` → `ctx["btc_4h_bars"]` (karar anında
  kapanmış son `btc_4h_lookback` bar). Hedefin `frac`'ı
  (`X_TPL`): ara hedefte İLK miktarın o payı kapanır (yoksa defterin varsayılanı: kalanın yarısı); son hedef kalanı kapatır;
  ilk hedeften sonra başa-baş stopu (defterin TP1 kuralı) aynen.

Parite (a) bu çekirdeği `FuturesLedgerV2` + `strategy_paper.apply_action` + `tick`'e karşı, canlı gibi 1h alt bar
tikleriyle (kırpma yok) sınar (`parity.py`).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from . import registry as RG
from ..accounting import LiquidationParams, PositionSide, bracket_for, liquidation_price
from ..accounting.futures_ledger import EXIT_FILL_CONTRACT, exit_decision
from ..learning_mode import fit_size
from ..pattern_trader.strategy import rr_after_cost
from ..strategy_lib import (ACT_CLOSE, ACT_MOVE_STOP, LONG, TARGET_PRICE, TF_MS, BookEntry, Intent, PosView, Variant,
                            catalog as SLC, identity as SLI)

#: v2 (2026-10-12): çıkış yolu canlı defterin `exit_decision`'ı + 1h alt bar tikleri (v1: `learning_cf` yolu, stop
#: seviyede). Kalıcı çıktı henüz yok; sürüm, anlamı değişen sonuçların (önbellek/deneme satırı) karışmaması içindir.
KERNEL_VERSION = "kernel_v2"
#: canlı defterin bar ucu dilimi (`strategy_paper.BAR_TIMEFRAME`; eşitliği testli) — çıkış tikleri bu dilimden
PATH_TF = "1h"
#: mühürlü `WF_PROTOCOL_V1.kernel.stop_fill.policy` (eşitliği testli) ve uygulanan canlı sözleşme
STOP_FILL_POLICY = "LIVE_EXIT_DECISION_ON_1H_BARS"
#: mühürlü `WF_PROTOCOL_V1.kernel.entry_hour.policy` (eşitliği testli): girişi içeren 1h saat diliminde yalnız stop/likidasyon
ENTRY_HOUR_POLICY = "STOP_AND_LIQUIDATION_ONLY"
EXIT_CONTRACT = EXIT_FILL_CONTRACT
SCENARIOS: tuple[str, ...] = ("base", "ADVERSE", "STRESS")
_COSTS = RG.WF_PROTOCOL_V1["thresholds"]["costs"]
SIZING = RG.LIB_V1["sizing"]
MIN_LIQ_MULT = float(RG.LIB_V1["thresholds"]["sizing"]["min_liq_distance_stop_mult"])
#: defterin varsayılan ilk-hedef oranı (`FuturesLedgerV2.tp1_fraction`) ve likidasyon ücreti (`LiquidationParams`)
TP1_FRACTION = 0.5
LIQ_FEE_PCT = float(LiquidationParams().liq_fee_pct)
#: fonlama boşluğu: YEREL aralığın bu katından uzun ara; yerel ölçünün iki yandaki pencere genişliği; damga yuvarlaması
FUNDING_GAP_FACTOR = 1.5
FUNDING_LOCAL_SPAN = 3
FUNDING_SNAP_MS = 60_000

# çıkış türleri
EXIT_STOP, EXIT_TARGET, EXIT_RULE, EXIT_TIME, EXIT_LIQ, EXIT_END = "STOP", "TARGET", "RULE", "TIME", "LIQ", "END"
# işaretler
FLAG_FUNDING_GAP, FLAG_OPEN_AT_END = "FUNDING_GAP", "OPEN_AT_END"
FLAG_FUNDING_EXIT_BAR, FLAG_MOVE_STOP_IGNORED = "FUNDING_EXIT_BAR", "MOVE_STOP_IGNORED"
#: çıkış kararı gereken bir sinyal barının 1h alt barları eksik/tutarsız → bar tek OHLC tiki (kapıdan çıkar)
FLAG_PATH_GAP = "PATH_1H_GAP"
#: H ailesinde pozisyon açıkken f8 ölçülemedi (`strategy_lib.h_funding`; eşitliği testli)
FLAG_F8_UNMEASURABLE = "F8_UNMEASURABLE"
#: Fonlama MARK kaynağı (koordinatör kararı 2026-10-13, P3e açık kalanı 1). Çekirdeğin fonlama tutarı arşivin fonlama
#: satırının mark'ına BAĞLI DEĞİLDİR (arşiv `fundingRate` zip'inde mark yok → NaN; REST satırının mark'ı arşiv gelince
#: onunla yer değiştirir): mark = `SeriesData.markpx_1h`'den, uzlaşma anında (nominal dakika) BAŞLAYAN 1h barının
#: AÇILIŞI; yoksa uzlaşma anında BİTEN 1h barının KAPANIŞI; ikisi de yoksa uzlaşma okunamaz → FUNDING_GAP. Uzlaşmadan
#: sonraki bir mark hiç kullanılmaz. Satırın kendi mark'ı yalnız denetimdir (göreli fark > %0,5 → FUNDING_MARK_MISMATCH,
#: bilgi; markpx kullanılır). MÜHÜR BEKLİYOR: `WF_PROTOCOL_V1.kernel.funding` okuması (düzeltme mührü 10) sahibin
#: mühürlü kayıt değişikliği iznine bağlı; o zamana kadar değerler burada, `FUNDING_MARK_SEALED = False`.
FUNDING_MARK_POLICY = "MARKPX_1H_OPEN_OF_BAR_STARTING_AT_SETTLEMENT_ELSE_CLOSE_OF_BAR_ENDING_AT_SETTLEMENT"
FUNDING_MARK_SEALED = False
MARK_TF_MS = 3_600_000
REST_MARK_MAX_REL_DIFF = 0.005
FLAG_FUNDING_MARK_MISMATCH = "FUNDING_MARK_MISMATCH"
#: fonlamadan türeyen işaretler (yol işaretlerinden sonra, bu sırayla eklenir)
FUNDING_FLAGS: tuple[str, ...] = ("FUNDING_GAP", "FUNDING_EXIT_BAR", FLAG_FUNDING_MARK_MISMATCH)
#: kapı istatistiğinden çıkan işaretler — mühürlü liste (`WF_PROTOCOL_V1.gate_exclusions.flags`)
GATE_EXCLUDED_FLAGS: tuple[str, ...] = tuple(RG.WF_PROTOCOL_V1["gate_exclusions"]["flags"])
#: `exit_decision` dayanakları (kayıt `basis`): ilk gözlemde verilen hükümler
_FIRST_OBS = frozenset({"FIRST_OBSERVATION_BEYOND_LIQUIDATION", "GAP_FILL_AT_FIRST_OBSERVATION"})
BASIS_PRUDENT_CLOSE = "STOP_CLOSE_BEYOND_LEVEL_PRUDENT"
BASIS_SLIP_LIQ = "SLIPPAGE_BEYOND_LIQUIDATION"
# giriş retleri
REJ_DRIFT, REJ_BAD_STOP, REJ_CHASE, REJ_RISK_RANGE, REJ_SIZE = ("ENTRY_DRIFT", "STRATEGY_BAD_STOP", "CHASE_LIMIT",
                                                                 "RISK_OUTSIDE_TESTED_RANGE", "SIZE_REJECTED")
REJ_RR, REJ_DATA_GAP = "RR_BELOW_MIN_AT_ENTRY", "ENTRY_DATA_GAP"
# MOVE_STOP ret nedenleri
MOVE_LOOSENS, MOVE_CROSSES_PRICE = "LOOSENS", "CROSSES_PRICE"


@dataclass(frozen=True)
class CostScenario:
    name: str
    taker_fee_pct: float
    slippage_bps: float
    spread_ticks: float

    @property
    def fee(self) -> float:
        return self.taker_fee_pct / 100.0

    @property
    def slip(self) -> float:
        return self.slippage_bps / 10_000.0


def scenario(name: str) -> CostScenario:
    c = _COSTS[name]
    if int(c["fee_sides"]) != 2:
        raise ValueError("maliyet senaryosu iki taraflı ücret varsayar")
    return CostScenario(name, float(c["taker_fee_pct"]), float(c["slippage_bps"]), float(c["spread_ticks"]))


@dataclass(frozen=True)
class SeriesData:
    """Bir sembolün girdisi. `bars[tf]`: kapanmış barlar, kronolojik (`timestamp` = açılış ms). `btc_1d`: BTC 1d
    barları (rejim isteyen kopyalar); `btc_4h`: BTC 4h barları (örtü `F_BTC`). `funding`: `(uzlaşma ms, oran, mark)`
    satırları (deponun gerçek damgaları); None = fonlama verisi yok (her işlem `FUNDING_GAP`). `price_tick`: serinin fiyat
    adımı (ADVERSE/STRESS spread'i). `markpx_1h`: vadeli mark fiyatı 1h barları — fonlamanın mark'ı YALNIZ buradan
    (`settlement_marks`); None = mark yok (uzlaşmayı kapsayan her işlem `FUNDING_GAP`)."""

    symbol: str
    bars: Mapping[str, Sequence[Mapping[str, Any]]]
    btc_1d: Sequence[Mapping[str, Any]] | None = None
    funding: Sequence[tuple[int, float, float]] | None = None
    price_tick: float = 0.0
    btc_4h: Sequence[Mapping[str, Any]] | None = None
    markpx_1h: Sequence[Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class Leg:
    """Bir çıkış bacağı (yol olayı). `ref`: maliyetten önceki referans fiyat; `frac`: ilk miktarın payı; `market`:
    kayma uygulanır mı (stop/kural/zaman/son); `ts`: kaydın damgası (ilk gözlemde o an, bar içinde gözlem barının
    kapanışı); `t_from`: bacağın EN ERKEN olası anı (ilk gözlemde o an, bar içinde gözlem barının açılışı, veri sonunda
    son kapanış) — fonlama bununla yazılır (modül belgesi, ihtiyatlı kural). `basis`: stop/likidasyon bacağında canlı
    `exit_decision` kaydının dayanağı (`exit_fill.basis`)."""

    kind: str
    ref: float
    frac: float
    market: bool
    ts: int
    point: str
    reason: str = ""
    t_from: int | None = None
    basis: str = ""

    @property
    def earliest(self) -> int:
        return int(self.ts if self.t_from is None else self.t_from)


@dataclass
class KernelTrade:
    symbol: str
    variant_id: str
    side: str
    signal_ts: int
    entry_ts: int
    entry_ref: float
    initial_stop: float
    targets: tuple[float, ...]
    leverage: int
    qty: float
    notional: float
    margin: float
    liq_price: float
    risk_usdt: float
    legs: list[Leg] = field(default_factory=list)
    exit_ts: int | None = None
    exit_kind: str | None = None
    exit_reason: str = ""
    bars_held: int = 0
    funding_usdt: float = 0.0
    funding_events: int = 0
    funding_ambiguous_usdt: float = 0.0
    funding_ambiguous_events: int = 0
    flags: list[str] = field(default_factory=list)
    move_stop_ignored: dict[str, int] = field(default_factory=dict)
    r: dict[str, float] = field(default_factory=dict)
    pnl_usdt: dict[str, float] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, int, str]:
        return (self.symbol, int(self.signal_ts), self.side)

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "variant_id": self.variant_id, "side": self.side, "signal_ts": self.signal_ts,
                "entry_ts": self.entry_ts, "entry_ref": self.entry_ref, "initial_stop": self.initial_stop,
                "targets": list(self.targets), "leverage": self.leverage, "qty": self.qty, "notional": self.notional,
                "margin": self.margin, "liq_price": self.liq_price, "risk_usdt": self.risk_usdt,
                "legs": [{"kind": lg.kind, "ref": lg.ref, "frac": lg.frac, "market": lg.market, "ts": lg.ts,
                          "t_from": lg.earliest, "point": lg.point, "reason": lg.reason, "basis": lg.basis}
                         for lg in self.legs],
                "exit_ts": self.exit_ts, "exit_kind": self.exit_kind, "exit_reason": self.exit_reason,
                "bars_held": self.bars_held, "funding_usdt": self.funding_usdt, "funding_events": self.funding_events,
                "funding_ambiguous_usdt": self.funding_ambiguous_usdt,
                "funding_ambiguous_events": self.funding_ambiguous_events, "flags": list(self.flags),
                "move_stop_ignored": dict(sorted(self.move_stop_ignored.items())), "r": dict(self.r),
                "pnl_usdt": dict(self.pnl_usdt)}


@dataclass
class KernelResult:
    variant_id: str
    symbol: str
    trades: list[KernelTrade]
    decisions: int
    bars: int
    rejected: dict[str, int]
    signals: int

    def to_dict(self) -> dict[str, Any]:
        return {"kernel": KERNEL_VERSION, "variant_id": self.variant_id, "symbol": self.symbol, "decisions": self.decisions,
                "bars": self.bars, "signals": self.signals, "rejected": dict(sorted(self.rejected.items())),
                "trades": [t.to_dict() for t in self.trades]}


def gate_excluded(trade: Any) -> bool:
    """İşlem kapı istatistiğinden çıkar mı: mühürlü `WF_PROTOCOL_V1.gate_exclusions.flags` işaretlerinden birini
    taşıyorsa (`KernelTrade`, `KernelTrade.to_dict()` ya da `flags` taşıyan başka bir kayıt)."""
    flags = trade.get("flags") if isinstance(trade, Mapping) else getattr(trade, "flags", None)
    return any(str(f) in GATE_EXCLUDED_FLAGS for f in (flags or ()))


# ============================================================================ kimlik (spec_sha, §2.1)
def lib_params(variant_id: str) -> dict[str, Any]:
    """Varyantın mühürlü parametreleri (alias'lı ızgara üyesi tabanınkini taşır)."""
    vs = RG.variants_by_id()
    v = vs[variant_id]
    if v.get("alias_of"):
        v = vs[v["alias_of"]]
    return RG.thaw(v["params"])


#: saf ailelerin (K dışı) varyantı kurarken aldığı mühürlü satır alanları (`strategy_lib.pure.SPEC_KEYS`)
VARIANT_SPEC_KEYS = ("family", "timeframes", "signal_tf", "sides", "exits")


def variant_spec(variant_id: str) -> dict[str, Any] | None:
    """Saf ailelerin kurulum girdisi: mühürlü satırın `{family, timeframes, signal_tf, sides, exits}` alanları (alias →
    tabanınki); F2'de ayrıca `base_params` = bağımlı olduğu kopyanın (`requires: copy_books.BOX` → `K_BOX`) mühürlü
    parametreleri ("diğer bütün parametreler K_BOX'unki"). O örtüsünde ayrıca `base` (taban adı), `base_variants`
    (mühürlü `cover.base_variants`; C4'te sekiz varyasyon config sırasıyla), `base_params` (her birinin mühürlü
    parametreleri) ve `base_max_hold` (`copy_books.<taban>.max_hold`). Kopyalarda (K) None."""
    vs = RG.variants_by_id()
    v = vs[variant_id]
    v = vs[v["alias_of"]] if v.get("alias_of") else v
    if v["family"] == "K":
        return None
    spec = {k: RG.thaw(v[k]) for k in VARIANT_SPEC_KEYS}
    if v["family"] == "F2":
        (req,) = [r for r in v["requires"] if r.startswith("copy_books.")]
        spec["base_params"] = lib_params("K_" + req.split(".", 1)[1])
    if v["family"] == "O":
        cov = v["cover"]
        spec["base"] = str(cov["base"])
        spec["base_variants"] = list(cov["base_variants"])
        spec["base_params"] = [lib_params(b) for b in cov["base_variants"]]
        spec["base_max_hold"] = RG.thaw(RG.LIB_V1["copy_books"][cov["base"]]["max_hold"])
    return spec


def make_variant(variant_id: str) -> Variant:
    """`strategy_lib` varyantı, mühürlü kayıttaki parametrelerle (tek kaynak); saf ailelerde satırın `spec`'iyle.
    Boşluklu (I, J, G) varyant `catalog.BlockedVariant`; O örtüsü kendi (paylaşımsız) taban akışıyla kurulur (aile
    paylaşımı için `make_family`). Mühürlü pencere okunuşunun
    (`LIB_V1.defaults.window`, P3b-2a inceleme bulgu 5) ailelerinde varyantın penceresi mühürlü değere eşit olmalı
    (değilse ValueError: kapalı hata)."""
    vs = RG.variants_by_id()
    base = vs[variant_id].get("alias_of") or variant_id
    var = SLC.make(base, lib_params(variant_id), variant_spec(variant_id))
    check_window(var)
    return var


def covers_of(base: str) -> list[str]:
    """Bir tabanın yedi örtüsü (mühürlü sıra)."""
    return [v["variant_id"] for v in RG.LIB_V1["variants"] if v["family"] == "O" and v["cover"]["base"] == base]


def make_family(base: str) -> tuple[Variant, dict[str, Variant]]:
    """Tabanın yedi örtüsü TEK paylaşılan akışla (§5.3 önlem 1; `strategy_lib.catalog.make_cover_family`). Döner:
    (paylaşılan taban — T2/M2/BOX/D4'te K satırının koşusu bununla yapılır ve akışı doldurur; C4'te C4 defteri —,
    {örtü: varyant}). Aynı seri nesnesiyle koşulan bütün üyeler tabanın kararlarını paylaşır."""
    members = {vid: (lib_params(vid), variant_spec(vid)) for vid in covers_of(base)}
    return SLC.make_cover_family(members)


def check_window(var: Variant) -> None:
    """Mühürlü pencere (`LIB_V1.defaults.window`): okunuşun ailelerinde `lookback` = {sinyal dilimi: mühürlü bar sayısı}."""
    w = RG.LIB_V1["defaults"]["window"]
    if var.family in w["families"]:
        want = {var.signal_tf: int(w["bars"][var.signal_tf])}
        if dict(var.lookback) != want:
            raise ValueError(f"{var.variant_id}: pencere mühürlü değer olmalı {want}, gelen {dict(var.lookback)}")


def spec_for(variant_id: str) -> dict[str, Any]:
    """Varyantın §2.1 spec'i: `registry.lib_spec` + `strategy_lib` modül kaynağının sha'sı (alias → tabanınki)."""
    base = RG.variants_by_id()[variant_id].get("alias_of") or variant_id
    return RG.lib_spec(variant_id, SLI.module_src_sha(base))


def spec_sha_for(variant_id: str) -> str:
    """`registry.spec_sha` (mühürlü kanonik fonksiyon; yeniden yazılmaz) — deneme defterinin `seal_entries`'iyle aynı."""
    return RG.spec_sha(spec_for(variant_id))


def module_src_shas() -> dict[str, str]:
    """Uygulanmış varyantların `module_src_sha`'sı (`trials.seal_entries` girdisi; P3b-2b: K + B, C, D, E, F1, F2, H + O =
    93; alias'lar tabanınkini kullanır; I/J/G boşluk yüzünden hiç)."""
    return {v: SLI.module_src_sha(v) for v in SLC.IMPLEMENTED}


# ============================================================================ yardımcılar
def _beyond(long: bool, level: float, px: float) -> bool:
    return px <= level if long else px >= level


class Obs:
    """`accounting.TickData`'nın `exit_decision`'ın okuduğu yüzü (float; Decimal dönüşümü yok — float karşılaştırmaları
    `Decimal(repr(x))` karşılaştırmalarıyla aynı sıradadır, eşitliği testli). Bar tiki: `open/high/low/last`; fiyat-yalnız
    tik: yalnız `last` (ilk gözlem fiyatın kendisi)."""

    __slots__ = ("open", "high", "low", "last", "mark")

    def __init__(self, last: float, *, open: float | None = None, high: float | None = None, low: float | None = None):
        self.last, self.mark, self.open, self.high, self.low = last, None, open, high, low

    @property
    def ref(self) -> float:
        return self.last

    @property
    def hi(self) -> float:
        return self.high if self.high is not None else self.last

    @property
    def lo(self) -> float:
        return self.low if self.low is not None else self.last


_SIDE = {True: PositionSide.LONG, False: PositionSide.SHORT}


def _tf_index(rows: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(r["timestamp"]) for r in rows]


def _closed_upto(ts_list: list[int], rows: Sequence[Mapping[str, Any]], step: int, now_ms: int, n: int) -> list:
    j = bisect.bisect_right(ts_list, int(now_ms) - step)
    return list(rows[max(0, j - n):j])


def break_even(side_long: bool, entry_fill: float, fee: float, slip: float) -> float:
    """`FuturesLedgerV2.break_even_price` (giriş ücreti payı = taker, çıkış taker, kayma tamponu), float; tick yuvarlaması
    yok (çekirdek fiyat adımına kuantize etmez)."""
    if side_long:
        return entry_fill * (1.0 + fee) / ((1.0 - slip) * (1.0 - fee))
    return entry_fill * (1.0 - fee) / ((1.0 + slip) * (1.0 + fee))


def size_trade(side: str, fill: float, stop: float) -> tuple[bool, str, int, float, float, float]:
    """(ok, neden, kaldıraç, notional, marj, likidasyon fiyatı) — `learning_mode.fit_size` (slot 1, rezerv 0, hedef risk
    %0,5 × 200) + bracket likidasyon formülü; mesafe ≥ MIN_LIQ_MULT × stop tutmazsa kaldıraç düşürülür."""
    E = float(SIZING["base_equity_usdt"])
    risk_pct = float(SIZING["risk_pct"])
    s = abs(fill - stop) / fill
    n_risk = risk_pct / 100.0 * E / s
    br = bracket_for(Decimal(repr(n_risk)))
    fit = fit_size(equity=E, entry=fill, stop=stop, slots=1, leverage_max=int(br.max_leverage), risk_pct=risk_pct,
                   reserve_pct=0.0, liq_buffer_mult=MIN_LIQ_MULT, mmr=float(br.mmr), min_notional=0.0,
                   hard_cap_pct=risk_pct, min_notional_bump=False)
    if not fit.ok:
        return False, str(fit.reason or "FIT_FAILED"), 0, 0.0, 0.0, 0.0
    lev, notional = int(fit.leverage), float(fit.notional)
    while lev >= 1:
        b = bracket_for(Decimal(repr(notional)))
        qty = notional / fill
        margin = notional / lev
        liq = float(liquidation_price(side, Decimal(repr(fill)), Decimal(repr(qty)), Decimal(repr(margin)), b.mmr,
                                      b.maint_amount))
        if abs(fill - liq) >= MIN_LIQ_MULT * abs(fill - stop) * (1.0 - 1e-12):
            return True, "", lev, notional, margin, liq
        lev -= 1
        notional = min(notional, E * lev) if lev >= 1 else notional
    return False, "LIQ_BUFFER_TOO_THIN", 0, 0.0, 0.0, 0.0


# ============================================================================ fonlama
def snap_ms(t: int) -> int:
    """Uzlaşma damgasını nominal dakikaya yuvarla (borsanın ms kayması: 08:00:00.003 → 08:00:00.000)."""
    return (int(t) + FUNDING_SNAP_MS // 2) // FUNDING_SNAP_MS * FUNDING_SNAP_MS


def _readable(r: Any, m: Any) -> bool:
    try:
        return r is not None and m is not None and math.isfinite(float(r)) and math.isfinite(float(m)) and float(m) > 0
    except (TypeError, ValueError):
        return False


def _median(xs: Sequence[int]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = len(s) // 2
    return float(s[k]) if len(s) % 2 else (s[k - 1] + s[k]) / 2.0


class FundingSeries:
    """Deponun uzlaşma satırları (damgalar nominal dakikaya yuvarlanmış, sıralı). Boşluk ölçüsü YEREL aralıktır: i.
    aralık, iki yanındaki komşu aralıkların büyüğünün ya da iki yandaki `FUNDING_LOCAL_SPAN` aralığın medyanlarının
    büyüğünün `FUNDING_GAP_FACTOR` katını aşarsa boşluktur (seri 8h → 4h'e geçse de 8h dönemi boşluk sayılmaz; gerçek
    eksik uzlaşma — tek ya da arka arkaya iki — yakalanır). Aynı dakikaya düşen çelişen iki satır okunamaz sayılır."""

    def __init__(self, rows: Sequence[tuple[int, float, float]] | None, mismatch: Iterable[int] = ()):
        self.mismatch = frozenset(int(x) for x in mismatch)
        by: dict[int, tuple[Any, Any]] = {}
        self.conflict: set[int] = set()
        for t, r, m in rows or ():
            k = snap_ms(t)
            if k in by and repr(by[k]) != repr((r, m)):
                self.conflict.add(k)
            by[k] = (r, m)
        self.rows = [(k, by[k][0], by[k][1]) for k in sorted(by)]
        self.ts = [t for t, _r, _m in self.rows]
        d = [b - a for a, b in zip(self.ts, self.ts[1:])]
        self.intervals = d
        self.gap_after = [self._is_gap(i) for i in range(len(d))]

    def _is_gap(self, i: int) -> bool:
        d, k = self.intervals, FUNDING_LOCAL_SPAN
        near = [d[j] for j in (i - 1, i + 1) if 0 <= j < len(d)]
        if not near:
            return False                                 # tek aralık: kıyas yok
        wide = [x for x in (_median(d[max(0, i - k):i]), _median(d[i + 1:i + 1 + k])) if x is not None]
        lim1 = FUNDING_GAP_FACTOR * max(near)
        lim2 = FUNDING_GAP_FACTOR * max(wide)
        return d[i] > lim1 or d[i] > lim2

    def window(self, a_ms: int, b_ms: int) -> tuple[list[tuple[int, float, float]], bool]:
        """((a, b] aralığındaki satırlar, boşluk var mı). Boşluk: seri pencereyi örtmüyor (ilk satır a'dan, son satır
        b'den en yakın aralığın `FUNDING_GAP_FACTOR` katından uzak), pencereyi kuşatan ardışık satırlardan biri yerel
        ölçüye göre boşluk, ya da penceredeki bir satır okunamıyor / çelişiyor."""
        if len(self.rows) < 2:
            return [], True
        a, b = int(a_ms), int(b_ms)
        i = bisect.bisect_right(self.ts, a)
        j = bisect.bisect_right(self.ts, b)
        sel = self.rows[i:j]
        d = self.intervals
        gap = (i == 0 and self.ts[0] - a > FUNDING_GAP_FACTOR * d[0]) or \
              (j == len(self.ts) and b - self.ts[-1] > FUNDING_GAP_FACTOR * d[-1])
        if any(self.gap_after[x] for x in range(max(0, i - 1), min(len(d), j))):
            gap = True
        for t, r, m in sel:
            if t in self.conflict or not _readable(r, m):
                gap = True
        return sel, gap


@dataclass(frozen=True)
class FundingOutcome:
    usdt: float
    events: int
    gap: bool
    ambiguous_usdt: float
    ambiguous_events: int
    mark_mismatch: bool = False


def _pos_finite(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def settlement_marks(rows: Sequence[tuple[int, Any, Any]] | None, markpx_1h: Sequence[Mapping[str, Any]] | None
                     ) -> tuple[list[tuple[int, Any, float]], frozenset[int]]:
    """Uzlaşma satırlarına mark (`FUNDING_MARK_POLICY`): uzlaşma anında (nominal dakika) BAŞLAYAN markpx 1h barının
    açılışı; yoksa o anda BİTEN barın kapanışı; ikisi de yoksa NaN (okunamaz → `FUNDING_GAP`). Uzlaşmadan sonraki bir
    fiyat kullanılmaz (başlayan barın açılışı o anın fiyatıdır). Satırın kendi mark'ı yalnız denetim: ikisi de varsa ve
    göreli fark > `REST_MARK_MAX_REL_DIFF` ise o uzlaşma `mismatch` kümesine girer. Döner: ([(damga, oran, mark)],
    uyuşmazlık damgaları (nominal dakika))."""
    opens: dict[int, float] = {}
    closes: dict[int, float] = {}
    for b in markpx_1h or ():
        t = int(b["timestamp"])
        o, c = _pos_finite(b.get("open")), _pos_finite(b.get("close"))
        if o is not None:
            opens[t] = o
        if c is not None:
            closes[t + MARK_TF_MS] = c
    out: list[tuple[int, Any, float]] = []
    mism: set[int] = set()
    for t, r, m_row in rows or ():
        k = snap_ms(t)
        m = opens.get(k)
        if m is None:
            m = closes.get(k)
        if m is None:
            out.append((t, r, float("nan")))
            continue
        rm = _pos_finite(m_row)
        if rm is not None and abs(rm - m) / m > REST_MARK_MAX_REL_DIFF:
            mism.add(k)
        out.append((t, r, m))
    return out, frozenset(mism)


def funding_series(data: "SeriesData") -> "FundingSeries | None":
    """Çekirdeğin fonlama serisi: deponun uzlaşma satırları + `settlement_marks` (WF, keşif, parite aynı yol)."""
    if data.funding is None:
        return None
    rows, mism = settlement_marks(data.funding, data.markpx_1h)
    return FundingSeries(rows, mism)


def funding_of(trade: KernelTrade, fs: FundingSeries | None) -> FundingOutcome:
    """Yazılan fonlama (modül belgesi): s ∈ (giriş, son bacağın en erken anı]; s'de açık miktar = 1 − (en erken anı s'den
    ÖNCE olan bacakların payı). Bar içi bir bacağın barının içine düşen s (açılış < s < kapanış) o bacağın miktarına
    yazılmaz; tutarı ve sayısı `ambiguous` olarak döner."""
    if fs is None:
        return FundingOutcome(0.0, 0, True, 0.0, 0)
    end = max((lg.earliest for lg in trade.legs), default=int(trade.exit_ts or trade.entry_ts))
    sel, gap = fs.window(trade.entry_ts, end)
    sgn = 1.0 if trade.side == LONG else -1.0
    total, n = 0.0, 0
    mism = False
    for t, r, m in sel:
        if not _readable(r, m) or t in fs.conflict:
            continue
        open_frac = 1.0 - sum(lg.frac for lg in trade.legs if lg.earliest < t)
        if open_frac <= 1e-12 or float(r) == 0.0:
            continue
        total += -sgn * trade.qty * open_frac * float(m) * float(r)
        n += 1
        mism = mism or t in fs.mismatch
    amb, n_amb = 0.0, 0
    for lg in trade.legs:
        if lg.earliest >= lg.ts:
            continue
        a, b = max(lg.earliest, int(trade.entry_ts)), int(lg.ts)
        i, j = bisect.bisect_right(fs.ts, a), bisect.bisect_left(fs.ts, b)
        for t, r, m in fs.rows[i:j]:
            if not _readable(r, m) or t in fs.conflict or float(r) == 0.0:
                continue
            amb += -sgn * trade.qty * lg.frac * float(m) * float(r)
            n_amb += 1
    return FundingOutcome(total, n, gap, amb, n_amb, mism)


def apply_funding(trade: KernelTrade, fs: FundingSeries | None) -> FundingOutcome:
    """`funding_of` sonucunu işleme yaz (çekirdeğin döngü sonrası hesabı; keşif aynı fonksiyonu çağırır): tutar, olay
    sayıları ve işaretler `FUNDING_GAP`, `FUNDING_EXIT_BAR`, `FUNDING_MARK_MISMATCH` (bu sırayla)."""
    fo = funding_of(trade, fs)
    trade.funding_usdt, trade.funding_events = fo.usdt, fo.events
    trade.funding_ambiguous_usdt, trade.funding_ambiguous_events = fo.ambiguous_usdt, fo.ambiguous_events
    if fo.gap:
        trade.flags.append(FLAG_FUNDING_GAP)
    if fo.ambiguous_events:
        trade.flags.append(FLAG_FUNDING_EXIT_BAR)
    if fo.mark_mismatch:
        trade.flags.append(FLAG_FUNDING_MARK_MISMATCH)
    return fo


# ============================================================================ maliyet (aritmetik yeniden maliyetlendirme)
def cost_trade(trade: KernelTrade, sc: CostScenario, tick: float) -> tuple[float, float]:
    """(R, net USDT) — aynı yolun bacakları senaryonun ücret/kayma/spread'iyle; fonlama aynı."""
    s = 1.0 if trade.side == LONG else -1.0
    q = trade.qty
    spread = sc.spread_ticks * float(tick or 0.0)
    e_fill = trade.entry_ref * (1.0 + s * sc.slip) + s * spread
    gross = 0.0
    fees = sc.fee * e_fill * q
    margin_left = trade.margin
    for lg in trade.legs:
        ql = q * lg.frac
        if lg.kind == EXIT_LIQ:
            lp = lg.ref
            m_leg = margin_left
            fee = min(ql * lp * LIQ_FEE_PCT / 100.0, m_leg)
            g = (lp - e_fill) * ql * s
            if -g + fee > m_leg:
                g = -(m_leg - fee)
            gross += g
            fees += fee
            margin_left -= m_leg
            continue
        if lg.market:
            x = lg.ref * (1.0 - s * sc.slip) - s * spread
        else:
            x = lg.ref - s * spread
        gross += (x - e_fill) * ql * s
        fees += sc.fee * x * ql
        margin_left -= trade.margin * lg.frac
    net = gross - fees + trade.funding_usdt
    risk = abs(e_fill - trade.initial_stop) * q
    return (net / risk if risk > 0 else 0.0), net


# ============================================================================ çekirdek döngü
class _Pos:
    __slots__ = ("trade", "intent", "stop", "targets", "fracs", "tidx", "frac_left", "entry_idx", "tp1_done", "fill")

    def __init__(self, trade: KernelTrade, intent: Intent, entry_idx: int, fill: float):
        self.trade, self.intent, self.entry_idx, self.fill = trade, intent, entry_idx, fill
        self.stop = trade.initial_stop
        self.targets = list(trade.targets)
        self.fracs = [t.frac for t in intent.targets]
        self.tidx = 0
        self.frac_left = 1.0
        self.tp1_done = False


def _touches(pos: _Pos, h: float, lo: float, long: bool) -> bool:
    """Sinyal barı stopa, likidasyona ya da sıradaki hedefe değiyor mu (`exit_decision` / hedef karşılaştırmalarıyla aynı
    eşitsizlikler). Değmeyen barın alt barları da değmez (alt barlar barın aralığındadır): hiçbir tik bir şey yapamaz, alt
    bar açılmaz (kesinliği testli: 1h verisi tamken her barı açan koşuyla bayt-aynı; değmeyen barın eksik 1h verisi
    yalnız her barı açan koşuda `PATH_1H_GAP` işareti koyardı — yol ve R aynı)."""
    if (lo <= pos.stop or lo <= pos.trade.liq_price) if long else (h >= pos.stop or h >= pos.trade.liq_price):
        return True
    if pos.tidx < len(pos.targets):
        tgt = pos.targets[pos.tidx]
        return (h >= tgt) if long else (lo <= tgt)
    return False


def _resolve_targets(intent: Intent, entry: float) -> tuple[float, ...]:
    s = 1.0 if intent.side == LONG else -1.0
    out = []
    for t in intent.targets:
        out.append(float(t.value) if t.kind == TARGET_PRICE else entry + s * float(t.value) * abs(entry - intent.stop))
    return tuple(out)


def _entry_gate(intent: Intent, px: float) -> str | None:
    """Girişte (dolumdan önceki açılış fiyatıyla) kapılar, canlı sırayla: kovalama (Formasyon `_try_open`'ın ilk kapısı),
    stop yanlış tarafta (`apply_action` `STRATEGY_BAD_STOP`), giriş kayması, test edilmiş risk aralığı, maliyet sonrası
    R/R tabanı (Formasyon `_try_open` `RR_BELOW_MIN_AT_ENTRY`: ilk hedef bu fiyattan çözülür; canlı `rr_after_cost`)."""
    long = intent.side == LONG
    ref = intent.entry_ref
    if ref.anchor is not None and ref.max_anchor_dist is not None and abs(px - ref.anchor) > ref.max_anchor_dist:
        return REJ_CHASE
    if px <= 0 or (long and intent.stop >= px) or (not long and intent.stop <= px):
        return REJ_BAD_STOP
    if ref.max_drift_pct and ref.price > 0 and abs(px - ref.price) / ref.price * 100.0 > float(ref.max_drift_pct):
        return REJ_DRIFT
    rb = intent.risk_atr_bounds
    if rb is not None and not (rb.atr > 0 and rb.lo * rb.atr < abs(px - intent.stop) <= rb.hi * rb.atr):
        return REJ_RISK_RANGE
    if ref.min_rr_after_cost is not None:
        tg = _resolve_targets(intent, px)
        if not tg:
            return REJ_RR
        _g, net = rr_after_cost(px, intent.stop, tg[0], cost_frac=float(ref.rr_cost_frac))
        if net < float(ref.min_rr_after_cost):
            return REJ_RR
    return None


def move_stop_verdict(side: str, current: float, new: float, price: float) -> str | None:
    """MOVE_STOP kabulü: None = uygula; aksi ret nedeni. Yalnız sıkılaştırır (LONG yukarı, SHORT aşağı; eşit = değişiklik
    yok, ret değil) ve kararın anındaki fiyatı geçemez (LONG: yeni stop < fiyat; SHORT: yeni stop > fiyat)."""
    long = side == LONG
    if (long and new < current) or (not long and new > current):
        return MOVE_LOOSENS
    if (long and new >= price) or (not long and new <= price):
        return MOVE_CROSSES_PRICE
    return None


class _Sim:
    """Tek varyant × tek seri oynatıcısı. `run` tam koşu; `signals_at` tek karar anında niyetler; `replay` tek niyetin
    zorlanmış yeniden oynatımı (parite (b): canlı girişin bir kopya işlemi, yol bağımlılığı olmadan)."""

    def __init__(self, variant: Variant, data: SeriesData):
        self.variant, self.data = variant, data
        self.stf = variant.signal_tf
        self.step = TF_MS[self.stf]
        self.rows = list(data.bars.get(self.stf) or ())
        self.row_ts = _tf_index(self.rows)
        self.views = {tf: (_tf_index(data.bars.get(tf) or ()), list(data.bars.get(tf) or ()), TF_MS[tf],
                           int(variant.lookback.get(tf, 0) or len(data.bars.get(tf) or ()))) for tf in variant.timeframes}
        btc = list(data.btc_1d or ())
        self.btc_view = (_tf_index(btc), btc, TF_MS["1d"], int(variant.btc_lookback or len(btc))) if variant.needs_btc else None
        if getattr(variant, "needs_btc_4h", False) and data.btc_4h is None:
            raise ValueError(f"{variant.variant_id}: F_BTC örtüsü BTC 4h barları ister (SeriesData.btc_4h yok)")   # bulgu 3
        b4 = list(data.btc_4h or ())
        self.btc4_view = (_tf_index(b4), b4, TF_MS["4h"], int(getattr(variant, "btc_4h_lookback", 0) or len(b4))) \
            if getattr(variant, "needs_btc_4h", False) else None
        bind = getattr(variant, "bind_series", None)
        if callable(bind):
            bind(data)                          # paylaşılan akış (P3b-2b): aynı seri nesnesi → aynı önbellek
        self.fund_view = None
        if getattr(variant, "needs_funding", False) and data.funding is not None:
            fr = sorted(((int(r[0]), r[1], r[2]) for r in data.funding), key=lambda x: x[0])
            self.fund_view = ([t for t, _r, _m in fr], fr, int(variant.funding_lookback_ms))
        self.hour = TF_MS[PATH_TF]
        # çıkış tikleri: sinyal dilimi PATH_TF'den kabaysa 1h alt barlar; değilse sinyal barının kendisi
        self.sub_step = TF_MS[PATH_TF] if self.step > TF_MS[PATH_TF] else None
        self.sub_rows = list(data.bars.get(PATH_TF) or ()) if self.sub_step else []
        self.sub_ts = _tf_index(self.sub_rows)

    def path_ticks(self, i: int) -> tuple[list[tuple[float, float, float, float, int, int]], bool]:
        """i. sinyal barının çıkış tikleri `[(açılış, yüksek, düşük, kapanış, açılış ms, kapanış ms)]` ve alt barlar
        kullanılabildi mi. Alt barlar ancak barı TAM ve bitişik örtüyorsa ve barın aralığının içindeyse kullanılır (o zaman
        değmeyen barın atlanması kesindir); değilse bar tek OHLC tikidir (çağıran `PATH_1H_GAP` işaretler)."""
        bar = self.rows[i]
        t_open = int(bar["timestamp"])
        o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        whole = [(o, h, lo, c, t_open, t_open + self.step)]
        if self.sub_step is None:
            return whole, True
        n = self.step // self.sub_step
        j = bisect.bisect_left(self.sub_ts, t_open)
        sub = self.sub_rows[j:j + n]
        out = []
        for k, r in enumerate(sub):
            ts = int(r["timestamp"])
            so, sh, sl, sc = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            if ts != t_open + k * self.sub_step or sh > h or sl < lo or not (sl <= min(so, sc) and max(so, sc) <= sh):
                return whole, False
            out.append((so, sh, sl, sc, ts, ts + self.sub_step))
        if len(out) != n:
            return whole, False
        return out, True

    def ctx_for(self, now: int, history: Sequence[BookEntry]) -> dict[str, Any]:
        c: dict[str, Any] = {"now_ms": now, "symbol": self.data.symbol,
                             "history": history if isinstance(history, tuple) else tuple(history)}
        if self.btc_view is not None:
            c["btc_bars"] = _closed_upto(self.btc_view[0], self.btc_view[1], self.btc_view[2], now, self.btc_view[3])
        if self.btc4_view is not None:
            c["btc_4h_bars"] = _closed_upto(self.btc4_view[0], self.btc4_view[1], self.btc4_view[2], now, self.btc4_view[3])
        if getattr(self.variant, "needs_funding", False):
            c["funding"] = None
            if self.fund_view is not None:
                ts, rows, lb = self.fund_view
                c["funding"] = tuple(rows[bisect.bisect_right(ts, int(now) - lb):bisect.bisect_right(ts, int(now))])
        return c

    def bars_for(self, now: int) -> dict[str, list]:
        return {tf: _closed_upto(v[0], v[1], v[2], now, v[3]) for tf, v in self.views.items()}

    def loop(self, *, start_ms: int | None = None, end_ms: int | None = None, forced: tuple[int, Intent] | None = None,
             history0: Sequence[BookEntry] = ()) -> tuple[KernelResult, str | None]:
        """`forced` = (karar anı, niyet): `signals` çağrılmaz; niyet o anın kapanışında bekleyen giriş olur; işlem bitince
        (ya da giriş reddedilince) durur. Dönen ikinci değer zorlanmış girişin ret nedeni (yoksa None)."""
        g = self.steps(start_ms=start_ms, end_ms=end_ms, forced=forced, history0=history0, lockstep=False)
        try:
            while True:
                next(g)
        except StopIteration as stop:
            return stop.value

    def steps(self, *, start_ms: int | None = None, end_ms: int | None = None, forced: tuple[int, Intent] | None = None,
              history0: Sequence[BookEntry] = (), lockstep: bool = False):
        """`loop`'un gövdesi, üreteç olarak: `lockstep=True` iken her sinyal barının sonunda bir kez durur (`run_lockstep`
        aynı serinin varyantlarını bar bar sırayla ilerletir); dönüş değeri `loop`'unkidir (StopIteration.value)."""
        variant, data, rows, step = self.variant, self.data, self.rows, self.step
        base = scenario("base")
        fs = funding_series(data)
        trades: list[KernelTrade] = []
        history: list[BookEntry] = list(history0)
        hist_t: tuple[BookEntry, ...] = tuple(history)          # `ctx["history"]` (yalnız girişte yenilenir)
        used_signals: set[int] = set()
        rejected: dict[str, int] = {}
        pos: _Pos | None = None
        pending: Any = None                    # Intent (aç) | ("CLOSE", neden, tür) | None
        pending_at: int | None = None          # bekleyen girişin karar anı (sonraki açılış = bu an olmalı)
        decisions = signals_n = 0
        forced_why: str | None = None
        i0 = 0
        if forced is not None:
            i0 = bisect.bisect_left(self.row_ts, int(forced[0]) - step)
            if i0 >= len(rows) or int(rows[i0]["timestamp"]) + step != int(forced[0]):
                return KernelResult(variant.variant_id, data.symbol, [], 0, 0, {}, 0), "DECISION_BAR_MISSING"

        def leg(kind: str, ref: float, frac: float, market: bool, ts: int, point: str, reason: str = "",
                t_from: int | None = None, basis: str = "") -> None:
            pos.trade.legs.append(Leg(kind, float(ref), float(frac), market, int(ts), point, reason,
                                      None if t_from is None else int(t_from), basis))
            pos.frac_left -= frac

        def finish(kind: str, reason: str, ts: int, bar_i: int) -> None:
            nonlocal pos
            t = pos.trade
            t.exit_ts, t.exit_kind, t.exit_reason = int(ts), kind, reason
            t.bars_held = bar_i - pos.entry_idx + 1
            trades.append(t)
            pos = None

        def observe(obs: Obs, t_open: int, t_close: int, bar_i: int, long: bool) -> bool:
            """Tek defter tiki, `FuturesLedgerV2.tick`'in çıkış sırasıyla: canlı `exit_decision` (stop/likidasyon), kayma
            dolumu likidasyonun ötesinde mi, sonra hedefler (tikin lehte ucuyla; TP1 → başa-baş stop yalnız sonraki
            tiklerde). `t_open`/`t_close`: gözlemin aralığı (fiyat-yalnız tikte ikisi de o an). Giriş saatiyle kesişen
            tikte (`t_open` < giriş saatinin sonu) hedef kredisi yok (modül belgesi). Kapandıysa True."""
            t = pos.trade
            dec = exit_decision(_SIDE[long], pos.stop, t.liq_price, obs)
            if dec is not None:
                basis = str(dec["record"]["basis"])
                first = obs.high is None or basis in _FIRST_OBS            # hüküm ilk gözlemden (açılış ya da fiyat)
                ts, tf = (t_open, None) if first else (t_close, t_open)
                point = "open" if first else ("close" if basis == BASIS_PRUDENT_CLOSE else "adverse")
                if dec["kind"] == "liquidation":
                    leg(EXIT_LIQ, t.liq_price, pos.frac_left, False, ts, point, "LIQUIDATION", tf, basis)
                    finish(EXIT_LIQ, "LIQUIDATION", ts, bar_i)
                    return True
                ref = float(dec["ref"])
                if _beyond(long, t.liq_price, ref * (1.0 - (1 if long else -1) * base.slip)):
                    leg(EXIT_LIQ, t.liq_price, pos.frac_left, False, ts, point, BASIS_SLIP_LIQ, tf, BASIS_SLIP_LIQ)
                    finish(EXIT_LIQ, BASIS_SLIP_LIQ, ts, bar_i)
                    return True
                reason = "BREAKEVEN_STOP" if pos.tp1_done else ("GAP_STOP" if first else "STOP")
                leg(EXIT_STOP, ref, pos.frac_left, True, ts, point, reason, tf, basis)
                finish(EXIT_STOP, reason, ts, bar_i)
                return True
            if t_open < (pos.trade.entry_ts // self.hour + 1) * self.hour:
                return False                                          # giriş saati: yalnız stop/likidasyon
            best = obs.hi if long else obs.lo
            if obs.high is None:
                ts, tf, point = t_open, None, "open"
            else:
                ts, tf, point = t_close, t_open, "favourable"
            while pos is not None and pos.tidx < len(pos.targets):
                tgt = pos.targets[pos.tidx]
                if not ((best >= tgt) if long else (best <= tgt)):
                    break
                last = pos.tidx == len(pos.targets) - 1
                if last:
                    leg(EXIT_TARGET, tgt, pos.frac_left, False, ts, point, "TARGET%d" % (pos.tidx + 1), tf)
                    pos.tidx += 1
                    finish(EXIT_TARGET, "TARGET%d" % pos.tidx, ts, bar_i)
                    return True
                fr = pos.fracs[pos.tidx]
                fr = pos.frac_left * TP1_FRACTION if fr is None else min(float(fr), pos.frac_left)
                leg(EXIT_TARGET, tgt, fr, False, ts, point, "TARGET%d" % (pos.tidx + 1), tf)
                if pos.tidx == 0:
                    pos.tp1_done = True
                    be = break_even(long, pos.fill, base.fee, base.slip)
                    if (long and be > pos.stop) or (not long and be < pos.stop):
                        pos.stop = be
                pos.tidx += 1
            return False

        for i in range(i0, len(rows)):
            bar = rows[i]
            o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            t_open = int(bar["timestamp"])
            t_close = t_open + step
            # ---------------------------------------------------------------- açılış: bekleyen eylem
            if pos is not None:
                long = pos.intent.side == LONG
                closed = observe(Obs(o), t_open, t_open, i, long)          # tur mark'ı: fiyat-yalnız açılış tiki
                if not closed and isinstance(pending, tuple) and pending[0] == ACT_CLOSE:
                    leg(pending[2], o, pos.frac_left, True, t_open, "open", pending[1])
                    finish(pending[2], pending[1], t_open, i)
                pending = None
            elif isinstance(pending, Intent):
                it, at = pending, pending_at
                pending = pending_at = None
                why = REJ_DATA_GAP if at is not None and t_open != int(at) else _entry_gate(it, o)
                if why is None:
                    long = it.side == LONG
                    fill = o * (1.0 + (1 if long else -1) * base.slip)
                    ok, why2, lev, notional, margin, liq = size_trade(it.side, fill, it.stop)
                    if ok:
                        tr = KernelTrade(symbol=data.symbol, variant_id=variant.variant_id, side=it.side,
                                         signal_ts=it.signal_ts, entry_ts=t_open, entry_ref=o, initial_stop=it.stop,
                                         targets=_resolve_targets(it, o), leverage=lev, qty=notional / fill,
                                         notional=notional, margin=margin, liq_price=liq,
                                         risk_usdt=abs(fill - it.stop) * notional / fill, evidence=dict(it.evidence))
                        pos = _Pos(tr, it, i, fill)
                        history.append(BookEntry(data.symbol, it.side, it.signal_ts, t_open, it.evidence))
                        hist_t = tuple(history)
                        if it.one_entry_per_signal:
                            used_signals.add(int(it.signal_ts))
                    else:
                        why = REJ_SIZE + ":" + why2
                if why is not None:
                    rejected[why] = rejected.get(why, 0) + 1
                    if forced is not None:
                        forced_why = why
                        break
            else:
                pending = pending_at = None
            # ---------------------------------------------------------------- bar içi: 1h alt bar tikleri (canlı defter)
            if pos is not None and _touches(pos, h, lo, pos.intent.side == LONG):
                long = pos.intent.side == LONG
                ticks, sub_ok = self.path_ticks(i)
                if not sub_ok and FLAG_PATH_GAP not in pos.trade.flags:
                    pos.trade.flags.append(FLAG_PATH_GAP)
                for so, sh, sl, sc, a_ms, b_ms in ticks:
                    if observe(Obs(sc, open=so, high=sh, low=sl), a_ms, b_ms, i, long):
                        break
            if forced is not None and trades:
                break
            # ---------------------------------------------------------------- kapanış: karar
            now = t_close
            if pos is not None:
                decisions += 1
                bv = self.bars_for(now)
                pv = PosView(side=pos.intent.side, entry=pos.fill, stop=pos.stop, initial_stop=pos.trade.initial_stop,
                             opened_ms=pos.trade.entry_ts, signal_ts=pos.intent.signal_ts, entry_bar_ts=pos.trade.entry_ts,
                             bars_held=i - pos.entry_idx + 1, intent=pos.intent)
                act = variant.manage(pv, bv, self.ctx_for(now, hist_t))
                for fl in act.flags:                                       # bilgi işaretleri (`Action.flags`)
                    if fl not in pos.trade.flags:
                        pos.trade.flags.append(fl)
                if act.kind == ACT_CLOSE:
                    pending = (ACT_CLOSE, act.reason, EXIT_RULE)
                elif act.kind == ACT_MOVE_STOP:
                    why_m = move_stop_verdict(pos.intent.side, pos.stop, float(act.price), c)
                    if why_m is None:
                        pos.stop = float(act.price)
                    else:
                        t = pos.trade
                        t.move_stop_ignored[why_m] = t.move_stop_ignored.get(why_m, 0) + 1
                        if FLAG_MOVE_STOP_IGNORED not in t.flags:
                            t.flags.append(FLAG_MOVE_STOP_IGNORED)
                mh = pos.intent.max_hold_bars
                if pending is None and mh is not None and (i - pos.entry_idx + 1) >= int(mh):
                    pending = (ACT_CLOSE, "TIME_STOP_%d_BARS" % int(mh), EXIT_TIME)
            elif forced is not None:
                if now == int(forced[0]) and not trades:
                    pending, pending_at = forced[1], now
                    signals_n += 1
            elif (start_ms is None or now >= int(start_ms)) and (end_ms is None or now <= int(end_ms)):
                decisions += 1
                intents = variant.signals(self.bars_for(now), self.ctx_for(now, hist_t))
                for it in intents:
                    if it.one_entry_per_signal and int(it.signal_ts) in used_signals:
                        rejected["SIGNAL_ALREADY_USED"] = rejected.get("SIGNAL_ALREADY_USED", 0) + 1
                        continue
                    signals_n += 1
                    pending, pending_at = it, now
                    break
            if lockstep:
                yield i
        if pos is not None and rows:
            last = rows[-1]
            t_last = int(last["timestamp"]) + step
            leg(EXIT_END, float(last["close"]), pos.frac_left, True, t_last, "close", "END_OF_DATA", t_last)
            pos.trade.flags.append(FLAG_OPEN_AT_END)
            finish(EXIT_END, "END_OF_DATA", t_last, len(rows) - 1)
        if forced is not None and not trades and forced_why is None:
            forced_why = "NO_ENTRY_BAR"
        for t in trades:
            apply_funding(t, fs)
            for name in SCENARIOS:
                r, net = cost_trade(t, scenario(name), data.price_tick)
                t.r[name], t.pnl_usdt[name] = r, net
        res = KernelResult(variant_id=variant.variant_id, symbol=data.symbol, trades=trades, decisions=decisions,
                           bars=len(rows) - i0, rejected=rejected, signals=signals_n)
        return res, forced_why


def run(variant: Variant, data: SeriesData, *, start_ms: int | None = None, end_ms: int | None = None) -> KernelResult:
    """Varyantı `data` üzerinde oynat. `start_ms`/`end_ms`: YENİ GİRİŞ kararlarının anı (bar kapanışı) aralığı
    [start, end]; pencere öncesi barlar yalnız gösterge ısınmasıdır; açık pozisyon pencereden sonra da kendi kuralıyla
    (`manage`, stop, hedef) yönetilir. Belirlenimci."""
    return _Sim(variant, data).loop(start_ms=start_ms, end_ms=end_ms)[0]


def run_lockstep(variants: Sequence[Variant], data: SeriesData, *, start_ms: int | None = None,
                 end_ms: int | None = None) -> list[KernelResult]:
    """Aynı serinin varyantlarını BAR BAR sırayla oynat (P3b-2b; §5.3): her varyantın koşusu `run` ile aynıdır (aynı
    çağrılar, yalnız iç içe sırada; sonuçlar bayt-aynı — test). Kazanç: aynı karar barının içerik adresli canlı önbellekleri
    (ör. ortak yapı analizinin `structures.analysis` LRU önbelleği) ve paylaşılan taban akışı ailenin bütün üyelerinde aynı
    bar için sıcaktır (T2/M2 örtülerinde yapı analizi bar başına bir kez hesaplanır). Döner: `variants` sırasıyla
    sonuçlar."""
    gens = [_Sim(v, data).steps(start_ms=start_ms, end_ms=end_ms, lockstep=True) for v in variants]
    out: list[KernelResult | None] = [None] * len(gens)
    live = list(range(len(gens)))
    while live:
        nxt = []
        for k in live:
            try:
                next(gens[k])
                nxt.append(k)
            except StopIteration as stop:
                out[k] = stop.value[0]
        live = nxt
    return [r for r in out if r is not None]


def signals_at(variant: Variant, data: SeriesData, now_ms: int, history: Sequence[BookEntry] = ()) -> list[Intent]:
    """Tek karar anında (bar kapanışı `now_ms`, pozisyon YOK) varyantın niyetleri; `ctx.history` = verilen geçmiş."""
    sim = _Sim(variant, data)
    return list(variant.signals(sim.bars_for(int(now_ms)), sim.ctx_for(int(now_ms), history)))


def replay_intent(variant: Variant, data: SeriesData, intent: Intent, *, decision_ms: int,
                  history: Sequence[BookEntry] = ()) -> tuple[KernelTrade | None, str | None]:
    """Tek niyetin zorlanmış yeniden oynatımı: `decision_ms` kapanışında bekleyen giriş, sonraki açılışta çekirdek
    kapıları ve dolumu, ardından `manage` + stop/hedef/zaman ile çıkışa kadar; başka giriş yok. (işlem, ret nedeni)."""
    res, why = _Sim(variant, data).loop(forced=(int(decision_ms), intent), history0=history)
    return (res.trades[0] if res.trades else None), why


__all__ = ["BASIS_PRUDENT_CLOSE", "BASIS_SLIP_LIQ", "CostScenario", "ENTRY_HOUR_POLICY", "EXIT_CONTRACT", "EXIT_END",
           "EXIT_LIQ", "EXIT_RULE", "EXIT_STOP", "EXIT_TARGET", "EXIT_TIME", "FLAG_F8_UNMEASURABLE", "FLAG_FUNDING_EXIT_BAR",
           "FLAG_FUNDING_GAP", "FLAG_FUNDING_MARK_MISMATCH", "FLAG_MOVE_STOP_IGNORED", "FUNDING_FLAGS",
           "FUNDING_MARK_POLICY", "FUNDING_MARK_SEALED", "MARK_TF_MS", "REST_MARK_MAX_REL_DIFF", "apply_funding",
           "funding_series", "settlement_marks", "FLAG_OPEN_AT_END", "FLAG_PATH_GAP", "FundingOutcome",
           "FundingSeries", "GATE_EXCLUDED_FLAGS", "KERNEL_VERSION", "KernelResult", "KernelTrade", "Leg",
           "MOVE_CROSSES_PRICE", "MOVE_LOOSENS", "Obs", "PATH_TF", "REJ_DATA_GAP", "REJ_RR", "SCENARIOS", "STOP_FILL_POLICY",
           "SeriesData", "VARIANT_SPEC_KEYS", "break_even", "check_window", "cost_trade", "covers_of", "funding_of",
           "gate_excluded", "lib_params", "make_family", "make_variant", "module_src_shas", "move_stop_verdict",
           "replay_intent", "run", "run_lockstep", "scenario", "signals_at", "size_trade", "snap_ms", "spec_for",
           "spec_sha_for", "variant_spec"]

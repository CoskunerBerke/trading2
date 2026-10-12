"""P3b — parite (docs/ENGINE_BRAIN_V1.md §2.1; ana belge §6.2). İki ölçü, eşikleri `LIB_V1.thresholds`'ta mühürlü:

**(a) Çekirdek ↔ defter** (`parity_kernel`: aile başına ≥ 200 sentetik işlem; sinyal kimliği %100; medyan |ΔR| ≤ 0,02;
p95 ≤ 0,05). Aynı sentetik barlar iki yoldan oynatılır:

* çekirdek: `strategy_lib` kopyası → `kernel.run` (base maliyet);
* başvuru: canlı kural fonksiyonu DOĞRUDAN (`paper_rules.decide_with_structures` / `decide_from_rows` — C4'te kapı
  dahil canlı `candle_book.decide`; Formasyonda `build_plans_v3` + `PatternBook._try_open`'ın kovalama, stop, risk
  aralığı ve maliyet sonrası R/R kapıları) → `strategy_paper.apply_action` → `FuturesLedgerV2.tick`. Defter base
  maliyetiyle kurulur (taker %0,05, 3 bps, bracket likidasyon, uzlaşma anlarında gerçek fonlama — kaynak deftere canlı
  gibi bağlanır, `FundingSchedule.bind_source`; aralığı değişen seride arşiv kaynağının kuralı, `_Lookup`); fiyat adımı serinin
  kendi `price_tick`'i (defter dolumu adıma kuantize eder, çekirdek etmez — ΔR'nin bir kaynağı budur), miktar adımı 1e-6.
  Büyüklük §2.1'indir (canlı defterin öğrenme/slot boyutu değil): başvuru eylemine çekirdeğin `size_trade` kaldıracı
  yazılır, risk ölçeği 50 kat (R ölçekten bağımsızdır; min-notional retlerini önler). Karar anından sonraki satır karar
  anında açılmıyorsa (veri boşluğu) giriş `ENTRY_DATA_GAP` ile reddedilir (çekirdekle aynı). İki kip:

  - `live_1h` (KAPILI ölçü; `kernel_v2`, sahip kararı 2026-10-12): canlı defterin gördüğü tikler, kırpma yok — pozisyon
    açıkken her sinyal barının açılışında fiyat-yalnız tik (tur mark'ı), sonra sinyal barının içindeki BÜTÜN 1h barları
    (`SeriesData.bars["1h"]`; canlı `apply_closed_bars` gibi bar başına bir `TickData(open, high, low, close)`); sinyal
    dilimi ≤ 1h ise (Box 5m) sinyal barının kendisi. Defterin `exit_decision`'ı `STOP_CLOSE_BEYOND_LEVEL_PRUDENT` /
    `PRUDENT_LIQUIDATION` dallarıyla ve "aynı tikte önce stop" kuralıyla çalışır. Başvuru 1h barlarını çekirdekten
    BAĞIMSIZ okur ve her birini tikler (çekirdeğin "değmeyen barda alt bar açma" kısaltması böylece sınanır). Sinyal
    barının 1h barları barı tam ve bitişik örtmüyor, barın aralığını aşıyor ya da kendi içinde tutarsızsa (KISMEN eksik
    dahil) bar tek OHLC tikidir; bar çıkış kararı gerektiriyorsa (aralığı defterin stopuna, likidasyonuna ya da sıradaki
    hedefine değiyorsa) işlem `PATH_1H_GAP` işaretlenir — çekirdeğin yedeği ve işaretiyle aynı kural, defterin kendi
    durumundan okunur (2026-10-12, P3b-2a; önceden kısmi 1h verisinde eldeki 1h barları tikleniyordu). **Giriş saati:**
    girişi içeren 1h saat dilimindeki tiklerde defterin hedefleri maskelenir (yalnız stop/likidasyon; çekirdeğin mühürlü
    okunuşu `WF_PROTOCOL_V1.kernel.entry_hour`: canlıda o saatin 1h barı `apply_closed_bars`'a girmez, saati 60 sn
    izleyici örter ve 1m verisi olmadan kurulamaz — iki taraf aynı ihtiyatlı okunuşu uygular).
  - `ohlc` (RAPOR, kapıya girmez): sinyal barı başına TEK OHLC tiki (P3b-1'in rapor kipi): 4h/1d ailelerinde 1h
    ayrıntısının ölçüye etkisini gösterir.

  Bar içi tikler gözlem barının açılışından 1 ms sonra damgalanır: çekirdeğin fonlama sözleşmesiyle aynı (bar içi çıkış
  barın içinde olur; barın içindeki ve kapanışındaki uzlaşma çıkan miktara yazılmaz; pozisyon bardan çıkmazsa sonraki
  tik onları yazar). Veri sonunda son kapanışta bir fiyat tiki fonlamayı kapatır, sonra elle kapanış.
* Sinyal kimliği = (sembol, sinyal barı, yön) kümelerinin eşitliği (iki yönde); ΔR = |R_çekirdek − R_defter| eşleşen
  işlemlerde (`TradeRecord.r_multiple`); ayrıca işaretli ortalama (R_çekirdek − R_defter).
* **Saf aileler (P3b-2a: B, C, D, E, F1, F2, H).** Canlı karşılıkları olmadığından başvurunun "kuralı" varyantın KENDİ
  `signals`/`manage`'idir (F2'de bu zaten canlı Box kuralıdır); ölçü çekirdeğin yol/maliyet aritmetiğini (R'den hedef,
  zaman stopu, kural çıkışı, fonlama, giriş saati, 1h yedeği) canlı defterle sınar. Zaman stopu (`max_hold_bars`) başvuruda
  sonraki açılışta elle kapanış; `ctx["funding"]` başvuruda çekirdekten bağımsız süzülür.
* **O örtüleri (P3b-2b).** Başvurunun kuralı örtünün kendisidir (`_CoverRule`; taban = canlı kopya, faktör örtünün
  modülü; başvuru akışı BAĞLAMAZ → paylaşımsız düz yol): niyet → `apply_action` → `FuturesLedgerV2.tick`. Başvuru kendi
  `ctx["history"]`'sini (kendi girişleri; T2/M2'nin kullanılmış yapı kümesi) ve `ctx["btc_4h_bars"]`'ını (F_BTC)
  çekirdekten bağımsız kurar. `MOVE_STOP` (X_TRAIL2, X_CHAN3): canlı `apply_action`'da stop taşıma eylemi yoktur; başvuru
  karar anında defter pozisyonunun stopunu kendisi yazar — kural çekirdekten bağımsız yazılmış aynı kuraldır (yalnız
  sıkılaştırır, karar barının kapanışını geçemez; aksi yok sayılır). Zaman stopu niyetin `max_hold_bars`'ından (X_TIME
  dahil). `X_TPL`: üç R hedefi dolum açılışından çözülür (`apply_action`'ın `target_r_from_entry` kuralı, üçü için) ve
  defterin hedef döngüsü aynen çalışır (TP1 → başa-baş dahil); canlı `FuturesLedgerV2` ara hedefte KALANIN `tp1_fraction`'ını
  kapattığından (ilk miktarın üçte birini kapatamaz), başvuru defteri yalnız ara hedef miktarını ilk miktarın payıyla
  veren alt sınıftır (`_FracLedger._close_part`; dolum, ücret, başa-baş, fonlama ve son hedef canlı kod).

**(b) Kopya ↔ canlı defter** (`parity_copy`; ana belge §6.2 "her config dönemi için ayrı, o dönemin parametreleriyle"):

* yalnız POLICY kohortu (`learning_mode.classify_unlock_codes` boş liste → POLICY; öğrenme etiketi olmayan eski kayıt
  `UNTAGGED`, POLICY değil). C4'te kayıtlar VARYASYONA göre süzülür (`features.candle_variation.id`, yoksa
  `setup_type` "candle:<id>"): her `K_C4_CVxxx` yalnız kendi varyasyonunun kayıtlarıyla kıyaslanır; varyasyonu
  okunamayan POLICY kaydı her C4 kopyasında EŞLEŞMEDİ sayılır (kapalı hata).
* dönem `rehydrate.config_epoch` + mühürlü `CONFIG_EPOCHS_V1` (TRANSITION ve kapsam dışı ATANMAZ). Dönem başına kopya o
  dönemin config GÖRÜNÜMÜNDEN kurulur (`view_sha` → görünüm, `params_from_view`); görünüm yoksa, sha'sı tutmuyorsa ya da
  kopyanın parametrelerine eşlenemiyorsa (bilinmeyen kural anahtarı, modellenmeyen değer, farklı yapı politika sürümü,
  Formasyonda farklı ücret) dönem `NOT_COMPARABLE` (raporlanır, kapıya girmez).
* eşleşme: her canlı POLICY girişi için kopyanın `signals`'ı girişin KARAR BARINDA (sinyal barının kapanışı), pozisyonsuz,
  `ctx.history` = canlı defterin kendi geçmişi (o sembolde o andan önce açılmış bütün girişleri) ile çağrılır; aynı
  sinyal barı ve yönde niyet yoksa EŞLEŞMEDİ. ΔR, o TEK niyetin çekirdekte zorlanmış yeniden oynatımından
  (`kernel.replay_intent`; çekirdek kapıları dahil, reddedilirse EŞLEŞMEDİ): yol bağımlılığı (CAPACITY/SELECTIVITY
  pozisyonlarının kopyada başka girişleri engellemesi) böylece kalkar. `features.data_source.bars[sinyal dilimi]` anahtarı
  olmayan POLICY kaydı EŞLEŞMEDİ sayılır (atlanmaz).
* kapı: ≥ 20 POLICY girişli KARŞILAŞTIRILABİLİR dönem kapılıdır: ≥ %95 aynı bar/yön ve eşleşenlerde medyan |ΔR| ≤ 0,05.
  Kapılı bir dönem tutmazsa `PARITY_FAIL`; ≥ 1 kapılı dönem varsa ve hepsi tutarsa `PASS`; yoksa `PARITY_UNPROVEN`
  (koşul gösterilemedi; ana belge §6.2 "ancak bu koşulla girer" → uygun değil).
* T2/M2 öğrenme kipinde (2026-09-28'den beri `structures_entry_shadow`) yapı denetiminin engelleyeceği yeni giriş
  `STRUCTURE_*` koduyla SELECTIVITY'dir (`strategy_paper` `_learning_tags`): POLICY kohortu ENFORCE'un geçirdiği kümeye
  eşittir → bu dönemler mühürlü ENFORCE kopyasıyla karşılaştırılabilir (mühür değişmez).
* P3b-1'de girdiler fixture defterleri ve verilen seridir; gece bağlantısı P3g'dedir.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import kernel as KN
from . import registry as RG
from . import rehydrate as RH

PARITY_KERNEL = RG.LIB_V1["thresholds"]["parity_kernel"]
PARITY_COPY = RG.LIB_V1["thresholds"]["parity_copy"]
PASS, PARITY_FAIL, PARITY_UNPROVEN = "PASS", "PARITY_FAIL", "PARITY_UNPROVEN"
NOT_COMPARABLE = "NOT_COMPARABLE"
MODE_LIVE, MODE_OHLC = "live_1h", "ohlc"
#: başvuru defterinin risk ölçeği (R ölçekten bağımsız; min-notional reddini önler)
REF_SCALE = 50.0
REF_EQUITY = 1_000_000.0
#: bar içi tiklerin damgası: gözlem barının açılışından sonra (çekirdeğin fonlama sözleşmesi)
INTRABAR_TICK_MS = 1
#: canlı defterin kapanmış bar dilimi (`strategy_paper.BAR_TIMEFRAME`; eşitliği testli) — başvuru bunu çekirdekten
#: bağımsız tutar
LIVE_BAR_TF = "1h"
#: kopya → canlı defter (state klasörü; `rehydrate.RULE_BOOKS` ile aynı adlar) ve sinyal dilimi
BOOK_DIR = {"K_T2": "strategy_paper", "K_M2": "strategy_paper_m2", "K_BOX": "strategy_paper_box",
            "K_D4": "strategy_paper_trend4h", **{f"K_C4_CV{i:03d}": "strategy_paper_candle4h" for i in range(1, 9)},
            "K_FM": "pattern_trader"}


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


def _pct(xs: Sequence[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _norm_sym(x: Any) -> str:
    return str(x or "").replace("/", "").replace(":USDT", "")


# ============================================================================ (a) başvuru: canlı kural + defter
class _Lookup:
    """Defterin fonlama kaynağı (`FundingSchedule.bind_source` sözleşmesi; canlı `strategy_paper.bind_funding` gibi
    bağlanır): uzlaşma anındaki gerçek oran ve mark (deponun satırları; damga nominal dakikaya yuvarlanır — çekirdekle
    aynı). Aralık değişen seriler için deponun arşiv kaynağının kuralı (`m2x_sim.ArchiveFunding`, çekirdekten
    bağımsız): `hours_for` = satırların UTC saatlerinin BİRLEŞİMİ; o saatte satır yoksa ve iki yanındaki satırlar
    ≤ 8 saat (+ 120 sn) arayla ise o saat bir uzlaşma DEĞİLDİR → 0 (dönem yok sayılır; 8h dönemindeki 04:00, ya da 4h
    dizisinde tek eksik uzlaşma — çekirdek de onu yazmaz ve işlemi `FUNDING_GAP` işaretler); daha büyük boşluk → None
    (defterin kuralı: dönem BEKLER, sonrakiler yazılmaz)."""

    TOL_MS = 120_000
    MAX_GAP_MS = 8 * 3_600_000

    def __init__(self, rows: Sequence[tuple[int, float, float]] | None):
        self.by = {KN.snap_ms(t): (r, m) for t, r, m in (rows or ())}
        self.ts = sorted(self.by)
        hrs = sorted({(t % 86_400_000) // 3_600_000 for t in self.ts})
        self.hours = tuple(hrs) if hrs else (0, 8, 16)

    def hours_for(self, symbol: str) -> tuple[int, ...]:
        return self.hours

    def _get(self, when: datetime):
        return self.by.get(KN.snap_ms(int(round(when.timestamp() * 1000))))

    def _not_a_settlement(self, when: datetime) -> bool:
        ms = KN.snap_ms(int(round(when.timestamp() * 1000)))
        i = _bisect(self.ts, ms)
        return 0 < i < len(self.ts) and self.ts[i] - self.ts[i - 1] <= self.MAX_GAP_MS + self.TOL_MS

    def __call__(self, symbol: str, when: datetime):
        v = self._get(when)
        if v is None:
            return Decimal(0) if self._not_a_settlement(when) else None
        return Decimal(repr(float(v[0])))

    def settlement_mark(self, symbol: str, when: datetime):
        v = self._get(when)
        return None if v is None else Decimal(repr(float(v[1])))


def live_cost_frac() -> float:
    """Canlı Formasyon defterinin maliyet oranı: `PatternBook.cost_frac` = `cost_fraction(fees.futures_taker_pct,
    fees.slippage_bps)` — canlı config sınıfının (`config_v3.FeesSection`) değerleriyle (kopyanın `LIVE_FEES`'inden
    bağımsız okunur; ikisinin eşitliği testlidir)."""
    from ..config_v3 import FeesSection
    from ..pattern_trader.strategy import cost_fraction
    f = FeesSection()
    return cost_fraction(taker_fee_pct=float(f.futures_taker_pct), slippage_bps=float(f.slippage_bps))


class _LiveRule:
    """Kopyanın canlı karşılığı: canlı kural fonksiyonunun kendisi, canlı defterin pozisyon nesnesiyle."""

    def __init__(self, variant_id: str):
        from ..strategy_lib import catalog as SLC
        self.variant_id = variant_id
        self.book = SLC.BOOK_OF[variant_id]
        self.params = KN.lib_params(variant_id)
        self.copy = KN.make_variant(variant_id)          # yalnız pencere/dilim bilgisi (karar değil)

    def decide(self, bars: Mapping[str, list], ctx: Mapping[str, Any], ledger: Any, symbol: str) -> dict | None:
        from .. import paper_rules
        from ..strategy_lib import livebars
        from ..structures import bots as SB
        now = int(ctx["now_ms"])
        pos = ledger.positions.get(symbol)
        b, p = self.book, self.params
        if b in ("T2", "M2"):
            name = {"T2": "t2_trend_regime", "M2": "m2_tsmom28"}[b]
            params = paper_rules.build_params(name, atr_mult=float(p["atr_mult"]))
            last = (bars.get("1d") or [None])[-1]
            mode = str(p["structures"]).upper()
            sctx = None if mode == "OFF" else SB.StructureContext(
                mode=mode, symbol=symbol, as_of_ms=now, price=float(last["close"]) if last else None,
                used_patterns=SB.used_patterns_of(ledger), provenance={"market": "USDM_PERP", "source": "parity_ref"})
            act, _d, _a = paper_rules.decide_with_structures(
                name, frames={"1d": livebars.frame(bars.get("1d"), "1d", daily_indicators=True)},
                btc_rows=livebars.btc_daily_rows(ctx.get("btc_bars"), now), now_ms=now, position=pos, params=params,
                ctx=sctx)
            return act
        if b == "BOX":
            from dataclasses import fields

            from .. import box_theory
            names = {f.name for f in fields(box_theory.BoxParams)}
            params = paper_rules.build_params("b1_box_fade", rule_params={k: v for k, v in p.items() if k in names})
            return paper_rules.decide_from_rows("b1_box_fade", daily=livebars.daily_rows(bars.get("1d"), now),
                                                intraday=livebars.intraday_rows(bars.get("5m"), "5m", now), btc_rows=None,
                                                position=pos, params=params, now_ms=now)
        if b == "D4":
            params = paper_rules.build_params("d4_donchian_20_10", rule_params={"entry_window_min": int(p["entry_window_min"])})
            return paper_rules.decide_from_rows("d4_donchian_20_10", daily=[],
                                                intraday=livebars.intraday_rows(bars.get("4h"), "4h", now), btc_rows=None,
                                                position=pos, params=params, now_ms=now)
        if b == "C4":
            params = paper_rules.build_params("c4_candle_variations", rule_params={
                "entry_window_min": int(p["entry_window_min"]), "variations": [str(p["variation"])]})
            return paper_rules.decide_from_rows("c4_candle_variations", daily=[],
                                                intraday=livebars.candle_rows(bars.get("4h"), now), btc_rows=None,
                                                position=pos, params=params, now_ms=now)
        if b == "FM":
            if pos is not None:
                return None
            from ..pattern_trader import strategy_v3 as S3
            from ..structures import analyze
            rows = livebars.fm_rows(bars.get("4h"))
            if not rows:
                return None
            an = analyze(market="USDM_PERP", symbol=symbol, timeframe="4h", bars=rows, as_of_ms=now,
                         data_provenance={"market": "USDM_PERP", "source": "parity_ref"})
            plans, _s = S3.build_plans_v3(symbol, as_of_ms=now, analyses={"4h": an}, bars_by_tf={"4h": rows},
                                          used_patterns=SB.used_patterns_of(ledger))
            if not plans:
                return None
            pl = sorted(plans, key=lambda d: int(d.get("triggered_at_ms") or 0))[0]
            st = dict(pl["structure"], action="ENTER", reason_code="PLAN_" + str(pl.get("family")), side=str(pl["side"]),
                      trigger=dict(pl.get("trigger") or {}), bot="pattern_trader")
            return {"action": "OPEN", "direction": pl["side"], "stop": float(pl["stop"]), "targets": [],
                    "target_r_from_entry": float(pl["target_from_entry_rr"]), "leverage": 1, "leverage_max": 0,
                    "reason": "PLAN_" + str(pl["family"]), "name": "pattern_trader", "setup_type": pl["family"],
                    "signal_ts": int(pl["triggered_at_ms"]) - 14_400_000, "signal_close_ms": int(pl["triggered_at_ms"]),
                    "atr14": float(pl["atr"]), "risk_atr_bounds": list(pl["risk_atr_bounds"]), "one_entry_per_signal": True,
                    "structure": st, "_chase": (float(pl["trigger"]["level"]), float(pl["chase_atr"]) * float(pl["atr"])),
                    "_rr": (float(pl["min_rr_after_cost"]), live_cost_frac(), float(pl["target_from_entry_rr"])),
                    "_hold_bars": int(S3.V3_SIGNAL["hold_bars"])}
        raise KeyError(self.variant_id)


class _PureRule:
    """Saf ailenin (K dışı) başvuru kuralı: varyantın kendi `signals`/`manage`'i → `apply_action` eylemi."""

    def __init__(self, variant_id: str):
        self.variant_id = variant_id
        self.copy = KN.make_variant(variant_id)
        self.book = "PURE"
        self.params: dict[str, Any] = {}
        self.max_hold = getattr(self.copy, "max_hold", None)
        self.intent: dict[str, Any] = {}

    def decide(self, bars: Mapping[str, list], ctx: Mapping[str, Any], ledger: Any, symbol: str) -> dict | None:
        from ..strategy_lib import TARGET_R_FROM_ENTRY, PosView
        pos = ledger.positions.get(symbol)
        if pos is None:
            its = self.copy.signals(bars, ctx)
            if not its:
                return None
            it = its[0]
            self.intent[symbol] = it
            act = {"action": "OPEN", "direction": it.side, "stop": float(it.stop), "leverage": 1, "leverage_max": 0,
                   "targets": [float(t.value) for t in it.targets if t.kind != TARGET_R_FROM_ENTRY],
                   "reason": it.exit_rule, "name": self.variant_id, "setup_type": self.copy.family,
                   "signal_ts": int(it.signal_ts), "signal_close": float(it.entry_ref.price)}
            r = [float(t.value) for t in it.targets if t.kind == TARGET_R_FROM_ENTRY]
            if r:
                act["target_r_from_entry"] = r[0]
            return act
        it = self.intent.get(symbol)
        opened = int(round(datetime.fromisoformat(str(pos.opened_at).replace("Z", "+00:00")).timestamp() * 1000))
        pv = PosView(side=str(getattr(pos.side, "value", pos.side)).upper(), entry=float(pos.entry_avg),
                     stop=float(pos.stop), initial_stop=float(pos.initial_stop or pos.stop), opened_ms=opened,
                     signal_ts=int(it.signal_ts) if it else 0, entry_bar_ts=opened, bars_held=0, intent=it)
        a = self.copy.manage(pv, bars, ctx)
        return {"action": "CLOSE", "reason": a.reason} if a.kind == "CLOSE" else None


class _CoverRule(_PureRule):
    """O örtüsünün başvuru kuralı: örtünün kendi `signals`/`manage`'i (taban canlı kopya + faktör), başvurunun kendi
    geçmişiyle; `MOVE_STOP` → başvuru stopu yazar, X_TPL → üç R hedefi + payları."""

    def __init__(self, variant_id: str):
        super().__init__(variant_id)
        self.book = "COVER"
        self.history: list[Any] = []

    def max_hold_for(self, symbol: str) -> int | None:
        it = self.intent.get(symbol)
        return int(it.max_hold_bars) if it is not None and it.max_hold_bars is not None else None

    def opened(self, symbol: str, side: str, opened_ms: int) -> None:
        from ..strategy_lib import BookEntry
        it = self.intent.get(symbol)
        if it is not None:
            self.history.append(BookEntry(symbol, side, int(it.signal_ts), int(opened_ms), it.evidence))

    def decide(self, bars: Mapping[str, list], ctx: Mapping[str, Any], ledger: Any, symbol: str) -> dict | None:
        from ..strategy_lib import TARGET_R_FROM_ENTRY, TF_MS, PosView
        pos = ledger.positions.get(symbol)
        ctx = dict(ctx, history=tuple(self.history))
        if pos is None:
            its = self.copy.signals(bars, ctx)
            if not its:
                return None
            it = its[0]
            self.intent[symbol] = it
            act = {"action": "OPEN", "direction": it.side, "stop": float(it.stop), "leverage": 1, "leverage_max": 0,
                   "targets": [float(t.value) for t in it.targets if t.kind != TARGET_R_FROM_ENTRY],
                   "reason": it.exit_rule, "name": self.variant_id, "setup_type": "cover",
                   "signal_ts": int(it.signal_ts), "signal_close": float(it.entry_ref.price)}
            rs = [(float(t.value), t.frac) for t in it.targets if t.kind == TARGET_R_FROM_ENTRY]
            if len(rs) == 1 and rs[0][1] is None:
                act["target_r_from_entry"] = rs[0][0]
            elif rs:
                act["_r_targets"] = rs
            if it.one_entry_per_signal:
                act["one_entry_per_signal"] = True
                act["signal_close_ms"] = int(it.signal_ts) + TF_MS[self.copy.signal_tf]
            return act
        it = self.intent.get(symbol)
        opened = int(round(datetime.fromisoformat(str(pos.opened_at).replace("Z", "+00:00")).timestamp() * 1000))
        pv = PosView(side=str(getattr(pos.side, "value", pos.side)).upper(), entry=float(pos.entry_avg),
                     stop=float(pos.stop), initial_stop=float(pos.initial_stop or pos.stop), opened_ms=opened,
                     signal_ts=int(it.signal_ts) if it else 0, entry_bar_ts=opened, bars_held=0, intent=it)
        a = self.copy.manage(pv, bars, ctx)
        if a.kind == "CLOSE":
            return {"action": "CLOSE", "reason": a.reason}
        if a.kind == "MOVE_STOP":
            return {"action": "MOVE_STOP", "price": float(a.price), "reason": a.reason}
        return None


def _rule_for(variant_id: str) -> Any:
    from ..strategy_lib import catalog as SLC
    base = RG.variants_by_id()[variant_id].get("alias_of") or variant_id
    fam = SLC.FAMILY_OF.get(base)
    return _LiveRule(base) if fam == "K" else _CoverRule(base) if fam == "O" else _PureRule(base)


def _frac_ledger_cls() -> Any:
    """`X_TPL` başvuru defteri: canlı `FuturesLedgerV2`, yalnız ARA hedefin miktarı pozisyonun `meta["tp_fracs"]`'ındaki
    ilk-miktar payıyla (canlı defter kalanın `tp1_fraction`'ını kapatır); başka her şey canlı kod."""
    from ..accounting import FuturesLedgerV2
    from ..core import quantize_qty

    class _FracLedger(FuturesLedgerV2):
        def _close_part(self, pos, fill_price, qty, kind, ts, **kw):
            fr = pos.meta.get("tp_fracs") if isinstance(getattr(pos, "meta", None), dict) else None
            idx = int(pos.targets_hit)
            if fr and str(kind).startswith("hedef") and idx < len(pos.targets) - 1 and fr[idx] is not None:
                part = quantize_qty(pos.initial_qty * Decimal(repr(float(fr[idx]))), self._step(pos))
                qty = min(pos.qty, part) if part > 0 else qty
            return super()._close_part(pos, fill_price, qty, kind, ts, **kw)

    return _FracLedger


@dataclass
class RefTrade:
    symbol: str
    side: str
    signal_ts: int
    r: float
    exit_reason: str
    opened_at: str
    closed_at: str
    funding: float
    exit_basis: str | None = None
    flags: tuple[str, ...] = ()


def _ref_ledger(data: KN.SeriesData, *, fracs: bool = False) -> tuple[Any, Any, Any, Any, Any]:
    from ..accounting import (FeeSchedule, FundingSchedule, FuturesLedgerV2, LiquidationParams, MarketType,
                              SlippageModel, SymbolFilters)
    from ..risk import PROFILES, KillSwitch, RiskEngine
    base = KN.scenario("base")
    led_cls = _frac_ledger_cls() if fracs else FuturesLedgerV2
    led = led_cls(REF_EQUITY, max_positions=None, enforce_position_cap=False,
                  fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal(repr(base.taker_fee_pct))),
                  slippage=SlippageModel(fixed_bps=Decimal(repr(base.slippage_bps))),
                  liq_params=LiquidationParams(),
                  funding=FundingSchedule(hours_utc=(0, 8, 16), fallback_to_last_known=False),
                  tp1_fraction=Decimal(repr(KN.TP1_FRACTION)), breakeven_at_mfe_r=Decimal("0"))
    tick = Decimal(repr(float(data.price_tick))) if data.price_tick and data.price_tick > 0 else Decimal("1e-8")
    filt = SymbolFilters(symbol=data.symbol, market_type=MarketType.USDM_PERP, price_tick=tick,
                         qty_step=Decimal("1e-6"), min_qty=Decimal("0"), max_qty=Decimal("1e15"),
                         market_max_qty=Decimal("1e15"), min_notional=Decimal("0"), max_leverage=125, source="parity")
    prof = replace(PROFILES["PAPER_RESEARCH"], name="PARITY_A",
                   risk_per_trade_pct=float(KN.SIZING["risk_usdt"]) * REF_SCALE / REF_EQUITY * 100.0,
                   max_total_open_risk_pct=100.0, futures_max_leverage=125, max_position_pct=1e9,
                   max_spot_allocation_pct=None)
    lookup = _Lookup(data.funding)
    led.funding.bind_source(lookup)
    return led, filt, prof, RiskEngine(prof, KillSwitch()), lookup


def reference_run(variant_id: str, data: KN.SeriesData, *, mode: str = MODE_LIVE) -> tuple[list[RefTrade], dict[str, int]]:
    """Canlı yol: canlı kural → `apply_action` → `FuturesLedgerV2.tick`. `mode`: `live_1h` (canlı defterin tikleri:
    açılış fiyat tiki + sinyal barının bütün 1h OHLC barları; kapılı ölçü) ya da `ohlc` (sinyal barı başına tek
    OHLC tiki; rapor). İkisinde de kırpma yok."""
    from ..accounting import TickData
    from ..core import iso
    from ..paper_rules import signal_already_used
    from ..pattern_trader.strategy import rr_after_cost
    from ..risk import build_state
    from ..strategy_lib import TF_MS
    from ..strategy_paper import DataVerdict, apply_action
    if mode not in (MODE_LIVE, MODE_OHLC):
        raise ValueError(f"kip {MODE_LIVE}/{MODE_OHLC} olmalı: {mode!r}")
    rule = _rule_for(variant_id)
    copy = rule.copy
    fund = sorted((int(r[0]), r[1], r[2]) for r in (data.funding or ())) if getattr(copy, "needs_funding", False) else []
    fund_ts = [r[0] for r in fund]
    stf = copy.signal_tf
    step = TF_MS[stf]
    sym = data.symbol
    rows = list(data.bars.get(stf) or ())
    idx = {tf: ([int(r["timestamp"]) for r in (data.bars.get(tf) or ())], list(data.bars.get(tf) or ()), TF_MS[tf],
                int(copy.lookback.get(tf))) for tf in copy.timeframes}
    btc = list(data.btc_1d or ())
    btc_idx = [int(r["timestamp"]) for r in btc]
    btc4 = list(data.btc_4h or ())
    btc4_idx = [int(r["timestamp"]) for r in btc4]
    h1 = TF_MS[LIVE_BAR_TF]
    subs = list(data.bars.get(LIVE_BAR_TF) or ()) if (mode == MODE_LIVE and step > h1) else []
    sub_ts = [int(r["timestamp"]) for r in subs]
    led, filt, prof, risk, lookup = _ref_ledger(data, fracs=rule.book == "COVER")
    move_ignored: dict[str, int] = {}
    rejects: dict[str, int] = {}
    pending: dict | None = None
    entry_bar: dict[str, int] = {}
    entry_hour_end: dict[str, int] = {}                # pozisyon kimliği → giriş saatinin sonu (ms)
    gap_flagged: set[str] = set()                      # PATH_1H_GAP işaretli pozisyon kimlikleri

    def reject(_s: str, why: str) -> None:
        rejects[why] = rejects.get(why, 0) + 1

    def state(now: datetime) -> Any:
        return build_state(equity=float(led.wallet_balance), starting_equity=REF_EQUITY, available=float(led.available),
                           used_margin=float(led.used_margin), positions=[], history=[], now=now)

    def masked(obs_open_ms: int, fn) -> None:
        """Giriş saatiyle kesişen tikte (gözlemin başı < giriş saatinin sonu) hedefleri maskele: yalnız stop/likidasyon."""
        p = led.positions.get(sym)
        end = entry_hour_end.get(p.id) if p is not None else None
        if p is None or end is None or int(obs_open_ms) >= end:
            fn()
            return
        saved = p.targets_hit
        p.targets_hit = len(p.targets)
        try:
            fn()
        finally:
            p.targets_hit = saved

    def tick(px: float, at: datetime, first: bool, obs_open_ms: int) -> None:
        masked(obs_open_ms, lambda: led.tick({sym: TickData(last=Decimal(repr(px)), mark=Decimal(repr(px)), ts=iso(at))},
                                             now_utc=at, funding_rate_lookup=lookup, bar_advance=first))

    def tick_ohlc(o: float, h: float, lo: float, c: float, at: datetime, obs_open_ms: int) -> None:
        td = TickData(last=Decimal(repr(c)), mark=Decimal(repr(c)), high=Decimal(repr(h)), low=Decimal(repr(lo)),
                      open=Decimal(repr(o)), ts=iso(at))
        masked(obs_open_ms, lambda: led.tick({sym: td}, now_utc=at, funding_rate_lookup=lookup, bar_advance=False))

    for i, bar in enumerate(rows):
        o, h, lo, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        t_open = int(bar["timestamp"])
        odt = _dt(t_open)
        pos = led.positions.get(sym)
        if pos is not None:
            tick(o, odt, True, t_open)                     # açılış gözlemi: boşlukta açılıştan
            if led.positions.get(sym) is not None and pending and str(pending.get("action")).upper() == "CLOSE":
                apply_action(pending, symbol=sym, price=o, tick=None, now=odt, ledger=led, risk=risk, profile=prof,
                             state=state(odt), filters=filt, run_id="parity", reject=reject, on_closed=lambda r: None,
                             data=pending["_verdict"])
            pending = None
        elif pending is not None:
            act, pending = pending, None
            chase = act.pop("_chase", None)
            rr = act.pop("_rr", None)
            side = str(act.get("direction") or "LONG").upper()
            stop = float(act["stop"])
            if t_open != int(act.pop("_at")):
                reject(sym, KN.REJ_DATA_GAP)               # karar barından sonraki satır karar anında açılmıyor
            elif chase is not None and abs(o - chase[0]) > chase[1]:
                reject(sym, "CHASE_LIMIT")
            elif rr is not None and (stop <= 0 or rr_after_cost(o, stop, o + (1 if side == "LONG" else -1) * rr[2] * abs(o - stop),
                                                                 cost_frac=rr[1])[1] < rr[0]):
                reject(sym, KN.REJ_RR)                     # PatternBook._try_open RR_BELOW_MIN_AT_ENTRY (mark = açılış)
            else:
                fill = o * (1.0 + (1 if side == "LONG" else -1) * KN.scenario("base").slip)
                ok, _why, lev, _n, _m, _l = KN.size_trade(side, fill, stop) \
                    if stop > 0 and ((side == "LONG") == (stop < o)) else (False, "", 1, 0, 0, 0)
                act["leverage"], act["leverage_max"] = (int(lev) if ok else 1), 0
                r_targets = act.pop("_r_targets", None)
                if r_targets:                              # X_TPL: R hedefleri dolum açılışından (target_r_from_entry kuralı)
                    sg = 1.0 if side == "LONG" else -1.0
                    act["targets"] = [o + sg * k * abs(o - stop) for k, _f in r_targets]
                res = apply_action(act, symbol=sym, price=o, tick=None, now=odt, ledger=led, risk=risk, profile=prof,
                                   state=state(odt), filters=filt, run_id="parity", reject=reject,
                                   on_closed=lambda r: None, data=act["_verdict"],
                                   max_entry_drift_pct=float(rule.params.get("max_entry_drift_pct") or 0.0))
                if res == "OPENED":
                    entry_bar[sym] = i
                    entry_hour_end[led.positions[sym].id] = (t_open // h1 + 1) * h1
                    if r_targets:
                        led.positions[sym].meta["tp_fracs"] = [f for _k, f in r_targets]
                    if rule.book == "COVER":
                        rule.opened(sym, side, t_open)
        if led.positions.get(sym) is not None:
            inner, sub_ok = _ref_hour_ticks(subs, sub_ts, (o, h, lo, c), t_open, step, h1) if subs else \
                ([(o, h, lo, c, t_open)], True)
            p = led.positions[sym]
            if not sub_ok and _ref_touches(p, h, lo):
                gap_flagged.add(p.id)                      # çekirdeğin `PATH_1H_GAP`'i: değen barda tek tik yedeği
            for so, sh, sl, sc, st in inner:               # canlı `apply_closed_bars`: 1h barı başına bir OHLC tiki
                if led.positions.get(sym) is None:
                    break
                tick_ohlc(so, sh, sl, sc, _dt(st + INTRABAR_TICK_MS), st)
        # ---------------------------------------------------------------- kapanış kararı
        now = t_open + step
        bars = {tf: v[1][max(0, _bisect(v[0], now - v[2]) - v[3]):_bisect(v[0], now - v[2])] for tf, v in idx.items()}
        ctx = {"now_ms": now, "symbol": sym}
        if copy.needs_btc:
            j = _bisect(btc_idx, now - TF_MS["1d"])
            ctx["btc_bars"] = btc[max(0, j - int(copy.btc_lookback)):j]
        if getattr(copy, "needs_btc_4h", False):
            j = _bisect(btc4_idx, now - TF_MS["4h"])
            ctx["btc_4h_bars"] = btc4[max(0, j - int(copy.btc_4h_lookback)):j]
        if getattr(copy, "needs_funding", False):
            ctx["funding"] = None if data.funding is None else \
                tuple(fund[_bisect(fund_ts, now - int(copy.funding_lookback_ms)):_bisect(fund_ts, now)])
        act = rule.decide(bars, ctx, led, sym)
        verdict = DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="parity_ref", tour_id="parity",
                              bars={tf: int(b[-1]["timestamp"]) for tf, b in bars.items() if b}, btc={})
        p = led.positions.get(sym)
        if p is not None:
            if act and str(act.get("action") or "").upper() == "MOVE_STOP":
                new = float(act["price"])                  # yalnız sıkılaştırır, karar fiyatını (kapanış) geçemez
                long = str(getattr(p.side, "value", p.side)).upper() == "LONG"
                cur = float(p.stop)
                if (long and new < cur) or (not long and new > cur):
                    move_ignored["LOOSENS"] = move_ignored.get("LOOSENS", 0) + 1
                elif (long and new >= c) or (not long and new <= c):
                    move_ignored["CROSSES_PRICE"] = move_ignored.get("CROSSES_PRICE", 0) + 1
                else:
                    p.stop = Decimal(repr(new))
                act = None
            mh = rule.max_hold_for(sym) if rule.book == "COVER" else None
            if act and str(act.get("action") or "").upper() == "CLOSE":
                pending = dict(act, _verdict=verdict)
            elif rule.book == "FM" and i - entry_bar.get(sym, i) + 1 >= 24:
                pending = {"action": "CLOSE", "reason": "TIME_STOP_24_BARS_4h", "_verdict": verdict}
            elif rule.book == "PURE" and rule.max_hold and i - entry_bar.get(sym, i) + 1 >= int(rule.max_hold):
                pending = {"action": "CLOSE", "reason": "TIME_STOP_%d_BARS" % int(rule.max_hold), "_verdict": verdict}
            elif mh is not None and i - entry_bar.get(sym, i) + 1 >= mh:
                pending = {"action": "CLOSE", "reason": "TIME_STOP_%d_BARS" % mh, "_verdict": verdict}
        elif act and str(act.get("action") or "").upper() == "OPEN":
            if signal_already_used(act, led.history, sym):
                reject(sym, "SIGNAL_ALREADY_USED")
            else:
                pending = dict(act, _verdict=verdict, _at=now)
    if led.positions.get(sym) is not None and rows:
        last = rows[-1]
        end = _dt(int(last["timestamp"]) + step)
        tick(float(last["close"]), end, False, int(last["timestamp"]) + step)   # son barın uzlaşmaları (END bacağı gibi)
        if led.positions.get(sym) is not None:
            led.close_manual(sym, Decimal(repr(float(last["close"]))), reason="END_OF_DATA", now=end)
    for why, n in move_ignored.items():
        rejects["MOVE_STOP_IGNORED:" + why] = n
    out = []
    for rec in led.history:
        ds = ((rec.features or {}).get("data_source") or {}).get("bars") or {}
        ef = (rec.features or {}).get("exit_fill") if isinstance((rec.features or {}).get("exit_fill"), dict) else {}
        out.append(RefTrade(symbol=rec.symbol, side=str(rec.side), signal_ts=int(ds.get(stf)), r=float(rec.r_multiple),
                            exit_reason=str(rec.exit_reason), opened_at=str(rec.opened_at), closed_at=str(rec.closed_at),
                            funding=float(rec.funding), exit_basis=ef.get("basis"),
                            flags=(KN.FLAG_PATH_GAP,) if rec.id in gap_flagged else ()))
    return out, rejects


def _ref_hour_ticks(subs: list, sub_ts: list[int], bar: tuple[float, float, float, float], t_open: int, step: int,
                    h1: int) -> tuple[list[tuple[float, float, float, float, int]], bool]:
    """Sinyal barının 1h tikleri (çekirdekten BAĞIMSIZ yazılmış aynı kural): 1h barları barı tam ve bitişik örtmeli
    (`step / 1h` bar, damgalar açılış + k × 1h), her biri barın aralığında ve kendi içinde tutarlı olmalı; değilse bar tek
    OHLC tikidir (ikinci değer False)."""
    o, h, lo, c = bar
    whole = [(o, h, lo, c, t_open)]
    n = step // h1
    j0 = _bisect_left(sub_ts, t_open)
    rows = subs[j0:j0 + n]
    out = []
    for k, r in enumerate(rows):
        so, sh, sl, sc = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        if int(r["timestamp"]) != t_open + k * h1 or sh > h or sl < lo or not (sl <= min(so, sc) and max(so, sc) <= sh):
            return whole, False
        out.append((so, sh, sl, sc, int(r["timestamp"])))
    if len(out) != n:
        return whole, False
    return out, True


def _ref_touches(p: Any, h: float, lo: float) -> bool:
    """Bar, defter pozisyonunun stopuna, likidasyonuna ya da sıradaki hedefine değiyor mu (defterin kendi durumu)."""
    long = str(getattr(p.side, "value", p.side)).upper() == "LONG"
    lv = [float(x) for x in (p.stop, p.liquidation_price) if x is not None]
    tgt = float(p.targets[p.targets_hit]) if p.targets_hit < len(p.targets) else None
    if long:
        return any(lo <= x for x in lv) or (tgt is not None and h >= tgt)
    return any(h >= x for x in lv) or (tgt is not None and lo <= tgt)


def _bisect(xs: list[int], v: int) -> int:
    import bisect
    return bisect.bisect_right(xs, int(v))


def _bisect_left(xs: list[int], v: int) -> int:
    import bisect
    return bisect.bisect_left(xs, int(v))


@dataclass
class ParityA:
    variant_ids: list[str]
    n_kernel: int
    n_ref: int
    n_matched: int
    identity_share: float
    median_abs_dr: float
    p95_abs_dr: float
    max_abs_dr: float
    mean_signed_dr: float = float("nan")
    n_abs_dr_over_001: int = 0
    only_kernel: list[tuple] = field(default_factory=list)
    only_ref: list[tuple] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"variants": self.variant_ids, "n_kernel": self.n_kernel, "n_ref": self.n_ref, "n_matched": self.n_matched,
                "identity_share": self.identity_share, "median_abs_dr": self.median_abs_dr, "p95_abs_dr": self.p95_abs_dr,
                "max_abs_dr": self.max_abs_dr, "mean_signed_dr": self.mean_signed_dr,
                "n_abs_dr_over_001": self.n_abs_dr_over_001, "only_kernel": self.only_kernel[:10],
                "only_ref": self.only_ref[:10], "passed": self.passed}


def compare_a(pairs: Sequence[tuple[str, KN.KernelResult, list[RefTrade]]], *, min_trades: int | None = None) -> ParityA:
    """(a) ölçüsü: kernel ↔ başvuru, (varyant, sembol, sinyal barı, yön) anahtarıyla."""
    kt: dict[tuple, float] = {}
    rt: dict[tuple, float] = {}
    for vid, kres, refs in pairs:
        for t in kres.trades:
            kt[(vid,) + t.key()] = float(t.r["base"])
        for r in refs:
            rt[(vid, r.symbol, int(r.signal_ts), r.side)] = float(r.r)
    both = sorted(set(kt) & set(rt))
    union = set(kt) | set(rt)
    signed = [kt[k] - rt[k] for k in both]
    d = [abs(x) for x in signed]
    share = (len(both) / len(union)) if union else 1.0
    th = PARITY_KERNEL
    need = int(th["min_synthetic_trades_per_family"]) if min_trades is None else int(min_trades)
    med = statistics.median(d) if d else float("nan")
    p95 = _pct(d, 0.95)
    ok = (len(both) >= need and share >= float(th["min_signal_identity_share"]) and med <= float(th["max_median_abs_dr"])
          and p95 <= float(th["max_p95_abs_dr"]))
    return ParityA(variant_ids=sorted({p[0] for p in pairs}), n_kernel=len(kt), n_ref=len(rt), n_matched=len(both),
                   identity_share=share, median_abs_dr=med, p95_abs_dr=p95, max_abs_dr=max(d) if d else float("nan"),
                   mean_signed_dr=(sum(signed) / len(signed)) if signed else float("nan"),
                   n_abs_dr_over_001=sum(1 for x in d if x > 0.01), only_kernel=sorted(set(kt) - set(rt)),
                   only_ref=sorted(set(rt) - set(kt)), passed=bool(ok))


# ============================================================================ (b) config görünümü → kopya parametreleri
#: kopya ailesi → (görünümdeki defter bölümü öneki, kural adı)
_VIEW_SECTION = {"T2": ("strategy_paper[name=t2_trend_regime]", "t2_trend_regime"),
                 "M2": ("strategy_paper.extra[name=m2_tsmom28]", "m2_tsmom28"),
                 "BOX": ("strategy_paper.extra[name=b1_box_fade]", "b1_box_fade"),
                 "D4": ("strategy_paper.extra[name=d4_donchian_20_10]", "d4_donchian_20_10"),
                 "C4": ("strategy_paper.extra[name=c4_candle_variations]", "c4_candle_variations"),
                 "FM": ("pattern_trader", "pattern_trader")}
#: defter bölümünün kararı DEĞİŞTİRMEYEN anahtarları (büyüklük, bakiye, klasör, ad, açık/kapalı, sembol evreni): eşlenmez.
#: Kopyanın girdisi verilen seridir; sembol evreni eşleşmeyi değil kapsamı belirler.
_NON_DECISION = {"name", "enabled", "state_dir", "starting_equity_usdt", "symbols", "rule_params.leverage",
                 "rule_params.leverage_max"}
#: Formasyon bölümünde protokol v3'te KURAL OLMAYAN anahtarlar (registry `copy_books.FM.reading_tr`): sınıfsal 15m
#: formasyon ayarları ve defterin canlı-yalnız kapıları (likidite/spread/derinlik, bekleme, tarama) — canlı girişi
#: engelleyebilirler, giriş ÜRETMEZLER (parite (b) canlı girişten yola çıkar).
_FM_NON_RULE = {"max_open_positions", "families", "stop_buffer_atr", "min_rr_after_cost", "fallback_rr",
                "measured_move_mult", "max_hold_bars", "cooldown_bars_after_loss", "min_quote_volume_24h", "max_spread_pct",
                "min_depth_0_5pct_usdt", "max_symbols_per_cycle", "scan_seconds", "universe_refresh_minutes"}
#: aile → defter bölümündeki karar anahtarı → kopya parametresi
_DECISION_KEYS = {
    "T2": {"atr_mult": "atr_mult", "breakeven_at_mfe_r": "breakeven_at_mfe_r", "max_entry_drift_pct": "max_entry_drift_pct"},
    "M2": {"atr_mult": "atr_mult", "breakeven_at_mfe_r": "breakeven_at_mfe_r", "max_entry_drift_pct": "max_entry_drift_pct"},
    "BOX": {"breakeven_at_mfe_r": "breakeven_at_mfe_r",
            **{f"rule_params.{k}": k for k in ("near_frac", "trigger", "long_stop", "exit_kind", "exit_r", "eod_close",
                                               "allow_long", "allow_short", "min_stop_pct")}},
    "D4": {"breakeven_at_mfe_r": "breakeven_at_mfe_r", "max_entry_drift_pct": "max_entry_drift_pct",
           "rule_params.entry_window_min": "entry_window_min"},
    "C4": {"breakeven_at_mfe_r": "breakeven_at_mfe_r", "max_entry_drift_pct": "max_entry_drift_pct",
           "rule_params.entry_window_min": "entry_window_min", "rule_params.variations": None},
    "FM": {"protocol": "protocol"},
}
_STRUCTURE_BOOKS = {"T2", "M2", "BOX", "FM"}


def params_from_view(variant_id: str, view: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Bir config dönemi görünümünden (`epochs.view`: {yol: değer}) kopyanın parametreleri; eşlenemezse (None, neden).
    Kapalı hata: defter bölümünde bilinmeyen anahtar, eksik karar yolu, yapı bölümü/politika sürümü belirsiz, Formasyonda
    farklı ücret, kopyanın kuramadığı değer (ör. `breakeven_at_mfe_r` ≠ 0) → NOT_COMPARABLE nedeni."""
    from ..strategy_lib import catalog as SLC
    fam = SLC.BOOK_OF[variant_id]
    prefix, rule = _VIEW_SECTION[fam]
    keys = _DECISION_KEYS[fam]
    sealed = KN.lib_params(variant_id)
    params: dict[str, Any] = {}
    section = {p[len(prefix) + 1:]: v for p, v in view.items() if p.startswith(prefix + ".")}
    for sub in sorted(section):
        if sub in keys or sub in _NON_DECISION or (fam == "FM" and sub in _FM_NON_RULE):
            continue
        if fam == "BOX" and sub in ("max_entry_drift_pct", "rule_params.min_stop_pct"):
            continue                                        # aşağıda: kayma kapısı yalnız kapalıyken (0) eşlenir
        return None, f"UNMAPPED_KEY:{prefix}.{sub}"
    for sub, pkey in keys.items():
        if sub not in section:
            if fam == "BOX" and pkey == "min_stop_pct":
                continue
            return None, f"VIEW_PATH_MISSING:{prefix}.{sub}"
        if pkey is not None:
            params[pkey] = section[sub]
    if fam == "C4":
        var = str(sealed["variation"])
        if var not in list(section.get("rule_params.variations") or []):
            return None, f"VARIATION_NOT_ACTIVE:{var}"
        params["variation"] = var
    if fam == "BOX":
        # öğrenme modunda kural öğrenme tabanıyla çağrılır (`strategy_paper._learning_params`); yoksa kuralın kendi değeri
        lm = view.get("learning_mode.enabled") is True and view.get(f"learning_mode.books.{rule}.enabled") is True
        lp = view.get(f"learning_mode.books.{rule}.min_stop_pct")
        if lm and lp is not None:
            params["min_stop_pct"] = lp
        elif "rule_params.min_stop_pct" in section:
            params["min_stop_pct"] = section["rule_params.min_stop_pct"]
        else:
            return None, f"VIEW_PATH_MISSING:{prefix}.rule_params.min_stop_pct"
        if float(section.get("max_entry_drift_pct") or 0.0) != 0.0:
            return None, "UNMODELLED:max_entry_drift_pct"     # Box kopyasında giriş kayması kapısı yok (0 = kapalı)
    if fam in _STRUCTURE_BOOKS:
        en = view.get("structures.enabled")
        if en is None:
            return None, "VIEW_PATH_MISSING:structures.enabled"
        mode = str(view.get(f"structures.{rule}") or "").upper() if en is True else "OFF"
        if en is True and not mode:
            return None, f"VIEW_PATH_MISSING:structures.{rule}"
        params["structures"] = mode
        if mode != "OFF" and fam != "BOX":
            from ..structures import POLICY_VERSION
            if view.get("structures.policy_version") != POLICY_VERSION:
                return None, f"STRUCTURES_POLICY_VERSION:{view.get('structures.policy_version')}!={POLICY_VERSION}"
    if fam == "FM":
        from ..strategy_lib import k_fm
        for k, v in k_fm.LIVE_FEES.items():
            if view.get(f"fees.{k}") != v:
                return None, f"FEES_DIFFER:fees.{k}={view.get(f'fees.{k}')}"
    missing = sorted(set(sealed) - set(params))
    if missing:
        return None, f"PARAMS_INCOMPLETE:{missing}"
    try:
        SLC.make(variant_id, params)
    except (ValueError, KeyError) as exc:
        return None, f"COPY_REJECTS_PARAMS:{exc}"
    return params, None


def views_from_configs(config_texts: Mapping[str, bytes | str]) -> dict[str, dict[str, Any]]:
    """Belgelenmiş sürümlerin `config.yaml` blob'larından (sürüm sha7 → baytlar; `scripts/gen_config_epochs.py` ile aynı
    girdi) her kuralın her görünümü: `view_sha` → görünüm. Mühürlü dosyadaki `view_sha`'lar bunlarla çözülür."""
    import yaml

    from . import epochs as EP
    flats = {rel: EP.flatten(yaml.safe_load((t if isinstance(t, bytes) else t.encode("utf-8")).decode("utf-8")))
             for rel, t in config_texts.items()}
    base = flats.get("3b0ae8e")
    seq = ([base] if base is not None else []) + [e["flat"] for e in EP.events(flats)]
    out: dict[str, dict[str, Any]] = {}
    for rule in EP.RULES:
        for f in seq:
            v = EP.view(f, rule)
            out[EP.view_sha(v)] = v
    return out


# ============================================================================ (b) kopya ↔ canlı defter
def live_cohort(rec: Mapping[str, Any]) -> str:
    """Canlı kaydın kohortu (`tj_v1` ile aynı okuma): öğrenme etiketi varsa `classify_unlock_codes` (POLICY / CAPACITY /
    SELECTIVITY), yoksa UNTAGGED."""
    from ..learning_mode import classify_unlock_codes
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    lrn = feats.get("learning") if isinstance(feats.get("learning"), dict) else None
    if lrn is None:
        return "UNTAGGED"
    return classify_unlock_codes([str(c) for c in (lrn.get("learning_unlocked_by") or [])]).upper()


def live_signal_key(rec: Mapping[str, Any], signal_tf: str) -> tuple[str, int, str] | None:
    """(sembol, sinyal barı, yön): kuralın okuduğu son kapanmış bar `features.data_source.bars[signal_tf]`."""
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    ds = feats.get("data_source") if isinstance(feats.get("data_source"), dict) else {}
    ts = (ds.get("bars") or {}).get(signal_tf) if isinstance(ds.get("bars"), dict) else None
    if ts is None:
        return None
    try:
        return (_norm_sym(rec.get("symbol")), int(ts), str(rec.get("side") or "").upper())
    except (TypeError, ValueError):
        return None


def record_variation(rec: Mapping[str, Any]) -> str | None:
    """C4 kaydının varyasyonu: `features.candle_variation.id`, yoksa `setup_type` "candle:<id>"."""
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    cv = feats.get("candle_variation") if isinstance(feats.get("candle_variation"), dict) else {}
    if cv.get("id"):
        return str(cv["id"])
    st = str(rec.get("setup_type") or feats.get("setup_type") or "")
    return st.split(":", 1)[1] if st.startswith("candle:") and len(st) > 7 else None


def _book_entry(rec: Mapping[str, Any], signal_tf: str) -> Any | None:
    from ..strategy_lib import BookEntry
    opened = _ms_of(rec.get("opened_at"))
    key = live_signal_key(rec, signal_tf)
    if opened is None:
        return None
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    ev = {"structure": dict(feats["structure"])} if isinstance(feats.get("structure"), dict) else {}
    return BookEntry(_norm_sym(rec.get("symbol")), str(rec.get("side") or "").upper(),
                     int(key[1]) if key else -1, int(opened), ev)


def _ms_of(x: Any) -> int | None:
    from .ledgers import parse_ts
    t = parse_ts(x)
    return int(t.timestamp() * 1000) if t is not None else None


def _book_epochs(book: str, research: Path) -> dict[str, dict]:
    eps, _cov = RH._sealed_doc(research)
    return {str(e.get("id")): e for e in (eps or []) if not e.get("books") or book in e["books"] or "*" in e["books"]}


@dataclass
class EpochParity:
    epoch: str | None
    n_policy: int
    n_matched: int
    share: float
    median_abs_dr: float
    gated: bool
    passed: bool | None
    comparable: bool = True
    reason: str | None = None
    view_sha: str | None = None
    params: dict[str, Any] | None = None
    unmatched: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "n_policy": self.n_policy, "n_matched": self.n_matched, "share": self.share,
                "median_abs_dr": self.median_abs_dr, "gated": self.gated, "passed": self.passed,
                "comparable": self.comparable, "status": None if self.comparable else NOT_COMPARABLE,
                "reason": self.reason, "view_sha": self.view_sha, "params": self.params,
                "unmatched": dict(sorted(self.unmatched.items()))}


@dataclass
class ParityB:
    variant_id: str
    verdict: str
    epochs: list[EpochParity]
    cohorts: dict[str, int]
    copy_extra: int | None
    explained_rejections: int
    unassigned_epoch: int
    other_variation: int = 0
    no_variation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"variant_id": self.variant_id, "verdict": self.verdict, "epochs": [e.to_dict() for e in self.epochs],
                "cohorts": dict(sorted(self.cohorts.items())), "copy_extra": self.copy_extra,
                "explained_rejections": self.explained_rejections, "unassigned_epoch": self.unassigned_epoch,
                "other_variation": self.other_variation, "no_variation": self.no_variation}


def _epoch_copy(variant_id: str, entry: Mapping[str, Any] | None, views: Mapping[str, Mapping[str, Any]] | None
                ) -> tuple[Any | None, str | None, str | None, dict | None]:
    """(kopya, NOT_COMPARABLE nedeni, view_sha, parametreler)."""
    from . import epochs as EP
    from ..strategy_lib import catalog as SLC
    if entry is None:
        return None, "EPOCH_NOT_IN_SEALED_FILE", None, None
    vsha = entry.get("view_sha")
    if not vsha:
        return None, "EPOCH_HAS_NO_VIEW", None, None
    view = (views or {}).get(vsha)
    if view is None:
        return None, "VIEW_UNAVAILABLE", vsha, None
    if EP.view_sha(view) != vsha:
        return None, "VIEW_SHA_MISMATCH", vsha, None
    params, why = params_from_view(variant_id, view)
    if params is None:
        return None, why, vsha, None
    base = RG.variants_by_id()[variant_id].get("alias_of") or variant_id
    return SLC.make(base, params), None, vsha, params


def compare_b(variant_id: str, live_records: Sequence[Mapping[str, Any]], *, data: Mapping[str, KN.SeriesData],
              views: Mapping[str, Mapping[str, Any]] | None, book: str | None = None, research: Path | None = None,
              copy_trades: Sequence[KN.KernelTrade] | None = None,
              explained_rejections: Sequence[Mapping[str, Any]] = ()) -> ParityB:
    """(b): canlı defter kayıtları (`TradeRecord` sözlükleri, bu defterin BÜTÜN kayıtları) ↔ kopya (modül belgesi).
    `data`: sembol (BTCUSDT biçimi) → seri; `views`: `view_sha` → config görünümü (`views_from_configs`); `research`:
    mühürlü `library/config_epochs.json`'un kökü (varsayılan paket). `copy_trades` verilirse canlıda olmayan kopya
    işlemleri (`copy_extra`) sayılır."""
    from ..strategy_lib import TF_MS
    from ..strategy_lib import catalog as SLC
    book = book or BOOK_DIR[variant_id]
    stf = KN.make_variant(variant_id).signal_tf
    research = research if research is not None else RG.PKG_DIR
    entries = _book_epochs(book, research)
    my_var = str(KN.lib_params(variant_id)["variation"]) if SLC.BOOK_OF[variant_id] == "C4" else None
    history: dict[str, list] = {}
    for rec in live_records:
        be = _book_entry(rec, stf)
        if be is not None:
            history.setdefault(be.symbol, []).append(be)
    for v in history.values():
        v.sort(key=lambda e: (e.opened_ms, e.signal_ts))
    cohorts: dict[str, int] = {}
    per: dict[str, list[tuple[str, float | None]]] = {}
    state: dict[str, tuple] = {}
    unassigned = other_var = no_var = 0
    live_keys: set[tuple] = set()
    for rec in live_records:
        key = live_signal_key(rec, stf)
        if key is not None:
            live_keys.add(key)
        rv = record_variation(rec) if my_var is not None else None
        if my_var is not None and rv is not None and rv != my_var:
            other_var += 1
            continue
        coh = live_cohort(rec)
        cohorts[coh] = cohorts.get(coh, 0) + 1
        if coh != "POLICY":
            continue
        ep, _src = RH.config_epoch(book, rec.get("opened_at"), research=research)
        if ep is None:
            unassigned += 1
            continue
        if ep not in state:
            state[ep] = _epoch_copy(variant_id, entries.get(ep), views)
        copy, why_nc, _vsha, _params = state[ep]
        items = per.setdefault(ep, [])
        if my_var is not None and rv is None:
            no_var += 1
            items.append(("NO_VARIATION", None))
            continue
        if copy is None:
            items.append((NOT_COMPARABLE, None))
            continue
        if key is None:
            items.append(("NO_DATA_SOURCE_BARS", None))
            continue
        sd = data.get(key[0])
        if sd is None:
            items.append(("NO_SERIES", None))
            continue
        try:
            r_live = float(rec.get("r_multiple"))
        except (TypeError, ValueError):
            items.append(("NO_R_MULTIPLE", None))
            continue
        decision = int(key[1]) + TF_MS[stf]
        hist = tuple(e for e in history.get(key[0], ()) if e.opened_ms < decision)
        intents = KN.signals_at(copy, sd, decision, hist)
        it = next((x for x in intents if int(x.signal_ts) == int(key[1]) and x.side == key[2]), None)
        if it is None:
            items.append(("NO_COPY_SIGNAL", None))
            continue
        tr, why = KN.replay_intent(copy, sd, it, decision_ms=decision, history=hist)
        if tr is None:
            items.append((f"REPLAY_REJECTED:{why}", None))
            continue
        items.append(("MATCHED", abs(float(tr.r["base"]) - r_live)))
    th = PARITY_COPY
    epochs: list[EpochParity] = []
    for ep in sorted(per, key=str):
        items = per[ep]
        copy, why_nc, vsha, params = state[ep]
        d = [x for tag, x in items if tag == "MATCHED"]
        unmatched: dict[str, int] = {}
        for tag, _x in items:
            if tag != "MATCHED":
                unmatched[tag] = unmatched.get(tag, 0) + 1
        n, m = len(items), len(d)
        share = m / n if n else 0.0
        med = statistics.median(d) if d else float("nan")
        comparable = copy is not None
        gated = comparable and n >= int(th["min_policy_entries_per_epoch"])
        passed = (share >= float(th["min_same_bar_side_share"]) and med <= float(th["max_median_abs_dr"])) if gated else None
        epochs.append(EpochParity(ep, n, m, share, med, gated, passed, comparable, why_nc, vsha, params, unmatched))
    gated = [e for e in epochs if e.gated]
    if any(not e.passed for e in gated):
        verdict = PARITY_FAIL
    elif gated:
        verdict = PASS
    else:
        verdict = PARITY_UNPROVEN
    extra = None
    if copy_trades is not None:
        extra = sum(1 for t in copy_trades if (_norm_sym(t.symbol), int(t.signal_ts), t.side) not in live_keys)
    return ParityB(variant_id=variant_id, verdict=verdict, epochs=epochs, cohorts=cohorts, copy_extra=extra,
                   explained_rejections=len(list(explained_rejections)), unassigned_epoch=unassigned,
                   other_variation=other_var, no_variation=no_var)


def mark_parity(rows: Sequence[Mapping[str, Any]], results: Mapping[str, ParityB]) -> list[dict[str, Any]]:
    """Lider tablosu/kapı satırlarına `PARITY_FAIL` işareti: (b)'si PASS olmayan kopyanın (K_*) satırları uygunluk
    dışı (`eligible=False`, `parity` = hüküm). Kopya olmayan satırlar değişmez."""
    out = []
    for r in rows:
        vid = str(r.get("variant_id") or r.get("unit") or "")
        row = dict(r)
        if vid in BOOK_DIR:
            pb = results.get(vid)
            verdict = pb.verdict if pb is not None else PARITY_UNPROVEN
            row["parity"] = verdict
            row["eligible"] = bool(row.get("eligible", True)) and verdict == PASS
            if verdict != PASS:
                row.setdefault("flags", [])
                row["flags"] = list(row["flags"]) + [PARITY_FAIL if verdict == PARITY_FAIL else PARITY_UNPROVEN]
        out.append(row)
    return out


__all__ = ["BOOK_DIR", "LIVE_BAR_TF", "MODE_LIVE", "MODE_OHLC", "NOT_COMPARABLE", "PARITY_COPY", "PARITY_FAIL", "PARITY_KERNEL",
           "PARITY_UNPROVEN", "PASS", "EpochParity", "ParityA", "ParityB", "RefTrade", "compare_a", "compare_b",
           "live_cohort", "live_cost_frac", "live_signal_key", "mark_parity", "params_from_view", "record_variation",
           "reference_run", "views_from_configs"]

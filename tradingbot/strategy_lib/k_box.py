"""P3b — `K_BOX`: canlı Box defterinin (önceki günün kutusu, 5m'de soluklama) kopyası (SAF).

Yeniden yazılmaz: karar `paper_rules.decide_from_rows("b1_box_fade", …)` (→ `box_theory.decide`). Parametreler
mühürdeki canlı değerlerdir (en az stop %0,5 = öğrenme tabanı, `box_theory.BoxParams.min_stop_pct`). Mühürlü yapı kipi
SHADOW'dur: canlı `decide_with_structures` SHADOW'da botun kararını DEĞİŞTİRMEZ (yalnız kayıt), bu yüzden kopya yapı
analizini çağırmaz (aynı karar; 5m analizinin maliyeti yok). Kip ENFORCE'a dönerse kopya ValueError verir (P3b-2).
Çıkışlar: stop ve kutu hedefi çekirdekte (defter tiki gibi), gün sonu düzleşmesi `manage` (canlı kural).

**Ön-süzgeç (P3b-2b, §5.3; `fast=True`).** Canlı kural her 5m barda çerçeve kurup çağrılınca Box ailesi tam geçmiş
koşusunun ≈ %95'iydi. Kural, `box_theory.decide`'ın KENDİ koşullarından türetilen ve ham barlardan ucuzca okunan bir
GEREK koşul tutmayan barda çağrılmaz (sonuç kanıtla aynı: kural o barda zaten OPEN/CLOSE üretemez). `decide` OPEN için
sırayla ister: (1) ≥ `MIN_M5_BARS` kapanmış 5m satırı (satırlar ham pencerenin alt kümesidir); (2) karar anı sinyal
barının UTC gününde (`now` günü > son 5m barın günü → giriş yok); (3) yan için tetik: SHORT kırmızı mum ve
(`break_prev`'te) kapanış < önceki mumun düşüğü, LONG yeşil mum ve kapanış > önceki mumun tepesi — yalnız izinli yanlar;
(4) önceki UTC günün kutusu (`read_box`, ham 1d barlarında: kuralın günlük satırları aynı barların son 320'sidir, kutu
barı ikisinde de aynı değerlerle bulunur ya da ikisinde de yoktur) ve son `loc_lookback` mumun uçlarından konum
(`box_theory._edge_location`, canlı fonksiyonun kendisi) tetiklenen yanın kenarı (SHORT → TOP, LONG → BOTTOM). Herhangi biri
tutmazsa `signals` boş döner; hepsi tutarsa canlı kural çağrılır ve sonuç onundur. Ham değerler kuralın okuduğu
değerlerle aynı float'lardır (`float(bar[k])`); son iki ham bar kapanmamışsa ya da okunamıyorsa süzgeç karar vermez
(kural çağrılır — muhafazakâr). `manage`: kural açık pozisyonda yalnız gün sonu düzleşmesi verir (`eod_close`, sinyal
barının günü < son 5m barın günü); bu GEREK koşul tutmazsa kural çağrılmaz. Eşitlik uzun sentetik dünyalarda (kutu
kenarı, gün dönümü, eksik 5m/1d barı, kapanmamış bar, iki kenar, tetik eşitliği uçları) ve çekirdek koşusunda `fast=False`
ile bayt-aynılıkla testlidir (`tests/test_strategy_lib_covers_p3b.py`).
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from .. import box_theory, paper_rules
from ..timeframes import DAY_MS
from . import livebars
from .contract import (EXIT_LIVE_RULE, NONE, TARGET_PRICE, Action, EntryRef, Intent, PosView, Target, Variant,
                       check_params, close)

PARAM_KEYS = ("near_frac", "trigger", "long_stop", "exit_kind", "exit_r", "eod_close", "allow_long", "allow_short",
              "breakeven_at_mfe_r", "min_stop_pct", "structures")
RULE = "b1_box_fade"
M5_MS = 300_000
#: kimliğe giren canlı modüller (`identity.module_src_sha`; çalışma izinin üst kümesi, test)
LIVE_MODULES: tuple[str, ...] = (
    "tradingbot.paper_rules",           # decide_from_rows / daily_rows / intraday_rows / build_params: giriş noktası
    "tradingbot.box_theory",            # Box kuralı (decide, BoxParams, rows_from_frame, read_box, _edge_location)
    "tradingbot.ema200_trend",          # daily_rows_from_frame: kutunun günlük satırları
    "tradingbot.indicators",            # canlı günlük çerçevenin ema200 / atr14 sütunları (livebars.frame, fast=False)
    "tradingbot.candle_confirmation",   # closed_bars: kapanmış-bar kesimi
    "tradingbot.timeframes",            # tf_ms / DAY_MS
)


def _day(ts: int) -> int:
    return int(ts) - int(ts) % DAY_MS


class BoxCopy(Variant):
    family = "K"
    signal_tf = "5m"
    timeframes = ("1d", "5m")
    needs_btc = False
    live_modules = LIVE_MODULES
    #: hızlı yol (FastFrame + ön-süzgeç); False = canlı (pandas) yol, süzgeçsiz — eşitlik testlerinin başvurusu
    fast = True

    def __init__(self, variant_id: str, params: Mapping[str, Any]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if float(p["breakeven_at_mfe_r"]) != 0.0:
            raise ValueError(f"{variant_id}: breakeven_at_mfe_r ≠ 0 çekirdekte modellenmez (P3b-1)")
        if str(p["structures"]).upper() not in ("OFF", "SHADOW"):
            raise ValueError(f"{variant_id}: Box yapı kipi ENFORCE kopyada yok (P3b-2)")
        self.variant_id = variant_id
        self.params = dict(p)
        names = {f.name for f in fields(box_theory.BoxParams)}
        self.rule_params = paper_rules.build_params(RULE, rule_params={k: v for k, v in p.items() if k in names})
        self.lookback = {"1d": livebars.LIVE_CLOSED_BARS["1d"], "5m": livebars.LIVE_CLOSED_BARS["5m"]}

    def _rows(self, bars, ctx) -> tuple[list, list, int]:
        now = int(ctx["now_ms"])
        return (livebars.daily_rows(bars.get("1d"), now, fast=self.fast),
                livebars.intraday_rows(bars.get("5m"), "5m", now, fast=self.fast), now)

    # ------------------------------------------------------------------ ön-süzgeç (modül belgesi)
    def cannot_open(self, bars, now: int) -> bool:
        """True → canlı kural bu karar anında OPEN üretemez (GEREK koşullardan biri tutmuyor). False → kural çağrılır."""
        m5 = bars.get("5m") or ()
        if len(m5) < box_theory.MIN_M5_BARS:
            return True
        p = self.rule_params
        try:
            cur, prev = m5[-1], m5[-2]
            t_cur, t_prev = int(cur["timestamp"]), int(prev["timestamp"])
            if t_cur + M5_MS > now or not t_prev < t_cur:
                return False                                  # kapanmamış / sırasız bar: süzgeç karar vermez
            co, cc = float(cur["open"]), float(cur["close"])
            ph, pl = float(prev["high"]), float(prev["low"])
        except (KeyError, TypeError, ValueError):
            return False
        day0 = _day(t_cur)
        if _day(now) > day0:
            return True                                       # (2) sinyal barının günü bitti
        brk = p.trigger == "break_prev"
        short = bool(p.allow_short) and cc < co and (cc < pl if brk else True)
        long = bool(p.allow_long) and cc > co and (cc > ph if brk else True)
        if not (short or long):
            return True                                       # (3) izinli yanda tetik yok
        box = box_theory.read_box(bars.get("1d") or [], before_ms=day0)
        if box is None:
            return True                                       # (4) kutu yok → kural None
        loc = box_theory._edge_location(list(m5[-int(p.loc_lookback):]), box[0], box[1], p)   # kuralın penceresi (bulgu 2)
        return not ((loc == "TOP" and short) or (loc == "BOTTOM" and long))

    def cannot_close(self, pos: PosView, bars, now: int) -> bool:
        """True → açık pozisyonda canlı kural bu karar anında CLOSE (gün sonu) üretemez."""
        if not self.rule_params.eod_close:
            return True
        m5 = bars.get("5m") or ()
        if not m5:
            return True
        try:
            t_cur = int(m5[-1]["timestamp"])
        except (KeyError, TypeError, ValueError):
            return False
        if t_cur + M5_MS > now:
            return False
        sig, opened = int(pos.signal_ts), int(pos.opened_ms)
        ref = sig if 0 < sig <= opened else opened
        return _day(ref) >= _day(t_cur)

    # ------------------------------------------------------------------ paylaşılan akış anahtarları (`stream`)
    def signal_key(self, ctx) -> Any:
        return None                                           # `signals` geçmişe bakmaz

    def manage_key(self, pos: PosView, ctx) -> Any:
        return None                                           # ön-süzgeçle O(1); bellek harcamaya değmez (paylaşılmaz)

    # ------------------------------------------------------------------ sözleşme
    def signals(self, bars, ctx) -> list[Intent]:
        if self.fast and self.cannot_open(bars, int(ctx["now_ms"])):
            return []
        d1, m5, now = self._rows(bars, ctx)
        act = paper_rules.decide_from_rows(RULE, daily=d1, intraday=m5, btc_rows=None, position=None,
                                           params=self.rule_params, now_ms=now)
        if not act or str(act.get("action") or "").upper() != "OPEN":
            return []
        return [Intent(signal_ts=int(act["signal_ts"]), side=str(act["direction"]).upper(),
                       entry_ref=EntryRef(price=float(act["signal_close"])), stop=float(act["stop"]),
                       targets=tuple(Target(TARGET_PRICE, float(t)) for t in (act.get("targets") or [])),
                       exit_rule=EXIT_LIVE_RULE, max_hold_bars=None, one_entry_per_signal=False, risk_atr_bounds=None,
                       evidence={"rule": RULE, "reason": act.get("reason"), "box_high": act.get("box_high"),
                                 "box_low": act.get("box_low"), "params_label": act.get("params_label")})]

    def manage(self, pos: PosView, bars, ctx) -> Action:
        if self.fast and self.cannot_close(pos, bars, int(ctx["now_ms"])):
            return NONE
        d1, m5, now = self._rows(bars, ctx)
        # canlı pozisyonla AYNI alanlar: açılış anı + girişin okuduğu 5m barı (gün sonu sinyal gününe göre)
        position = {"opened_at": int(pos.opened_ms), "features": {"data_source": {"bars": {"5m": int(pos.signal_ts)}}}}
        act = paper_rules.decide_from_rows(RULE, daily=d1, intraday=m5, btc_rows=None, position=position,
                                           params=self.rule_params, now_ms=now)
        if act and str(act.get("action") or "").upper() == "CLOSE":
            return close(str(act.get("reason") or "RULE_EXIT"))
        return NONE


def make(variant_id: str, params: Mapping[str, Any]) -> BoxCopy:
    return BoxCopy(variant_id, params)


__all__ = ["BoxCopy", "LIVE_MODULES", "M5_MS", "PARAM_KEYS", "RULE", "make"]

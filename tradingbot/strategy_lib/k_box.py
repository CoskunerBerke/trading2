"""P3b — `K_BOX`: canlı Box defterinin (önceki günün kutusu, 5m'de soluklama) kopyası (SAF).

Yeniden yazılmaz: karar `paper_rules.decide_from_rows("b1_box_fade", …)` (→ `box_theory.decide`). Parametreler
mühürdeki canlı değerlerdir (en az stop %0,5 = öğrenme tabanı, `box_theory.BoxParams.min_stop_pct`). Mühürlü yapı kipi
SHADOW'dur: canlı `decide_with_structures` SHADOW'da botun kararını DEĞİŞTİRMEZ (yalnız kayıt), bu yüzden kopya yapı
analizini çağırmaz (aynı karar; 5m analizinin maliyeti yok). Kip ENFORCE'a dönerse kopya ValueError verir (P3b-2).
Çıkışlar: stop ve kutu hedefi çekirdekte (defter tiki gibi), gün sonu düzleşmesi `manage` (canlı kural).
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from .. import box_theory, paper_rules
from . import livebars
from .contract import (EXIT_LIVE_RULE, NONE, TARGET_PRICE, Action, EntryRef, Intent, PosView, Target, Variant,
                       check_params, close)

PARAM_KEYS = ("near_frac", "trigger", "long_stop", "exit_kind", "exit_r", "eod_close", "allow_long", "allow_short",
              "breakeven_at_mfe_r", "min_stop_pct", "structures")
RULE = "b1_box_fade"
#: kimliğe giren canlı modüller (`identity.module_src_sha`; çalışma izinin üst kümesi, test)
LIVE_MODULES: tuple[str, ...] = (
    "tradingbot.paper_rules",           # decide_from_rows / daily_rows / intraday_rows / build_params: giriş noktası
    "tradingbot.box_theory",            # Box kuralı (decide, BoxParams, rows_from_frame)
    "tradingbot.ema200_trend",          # daily_rows_from_frame: kutunun günlük satırları
    "tradingbot.indicators",            # canlı günlük çerçevenin ema200 / atr14 sütunları (livebars.frame)
    "tradingbot.candle_confirmation",   # closed_bars: kapanmış-bar kesimi
    "tradingbot.timeframes",            # tf_ms / DAY_MS
)


class BoxCopy(Variant):
    family = "K"
    signal_tf = "5m"
    timeframes = ("1d", "5m")
    needs_btc = False
    live_modules = LIVE_MODULES

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
        return livebars.daily_rows(bars.get("1d"), now), livebars.intraday_rows(bars.get("5m"), "5m", now), now

    def signals(self, bars, ctx) -> list[Intent]:
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


__all__ = ["BoxCopy", "LIVE_MODULES", "PARAM_KEYS", "RULE", "make"]

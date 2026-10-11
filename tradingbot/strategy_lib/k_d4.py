"""P3b — `K_D4`: canlı 4h Donchian 20/10 gözlem defterinin kopyası (SAF).

Yeniden yazılmaz: karar `paper_rules.decide_from_rows("d4_donchian_20_10", …)` (→ `donchian_trend.decide`, göstergeler
laboratuvarın `signal_lab.indicators`/`aux_series`'i). Yapı katmanı yoktur (canlıda da yok). Giriş penceresi canlı
değerdir (60 dk); çekirdeğin "sonraki bar açılışı" dolumu pencerenin içindedir. Risk aralığı (0,1–10 ATR) ve "aynı
sinyalle tek giriş" niyete taşınır; zaman sınırı (300 × 4h) ve kanal çıkışı `manage`dedir (canlı kural), niyetin
`max_hold_bars`'ı aynı sınırı çekirdekte de yazar (aynı an).
"""
from __future__ import annotations

from typing import Any, Mapping

from .. import donchian_trend, paper_rules
from . import livebars
from .contract import (EXIT_LIVE_RULE, NONE, Action, EntryRef, Intent, PosView, RiskAtrBounds, Variant, check_params,
                       close)

PARAM_KEYS = ("entry_window_min", "breakeven_at_mfe_r", "max_entry_drift_pct")
RULE = "d4_donchian_20_10"
#: kimliğe giren canlı modüller (`identity.module_src_sha`; çalışma izinin üst kümesi, test)
LIVE_MODULES: tuple[str, ...] = (
    "tradingbot.paper_rules",           # decide_from_rows / intraday_rows / build_params: giriş noktası
    "tradingbot.donchian_trend",        # D4 kuralı (decide, DonchianParams, MAX_BARS)
    "tradingbot.signal_lab",            # kuralın göstergeleri (indicators / aux_series)
    "tradingbot.box_theory",            # rows_from_frame: gün içi satır okuyucusu (paper_rules.intraday_rows)
    "tradingbot.candle_confirmation",   # closed_bars
    "tradingbot.timeframes",            # tf_ms
)


class DonchianCopy(Variant):
    family = "K"
    signal_tf = donchian_trend.TIMEFRAME
    timeframes = (donchian_trend.TIMEFRAME,)
    needs_btc = False
    live_modules = LIVE_MODULES

    def __init__(self, variant_id: str, params: Mapping[str, Any]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if float(p["breakeven_at_mfe_r"]) != 0.0:
            raise ValueError(f"{variant_id}: breakeven_at_mfe_r ≠ 0 çekirdekte modellenmez (P3b-1)")
        self.variant_id = variant_id
        self.params = dict(p)
        self.drift = float(p["max_entry_drift_pct"]) or None
        self.rule_params = paper_rules.build_params(RULE, rule_params={"entry_window_min": int(p["entry_window_min"])})
        self.lookback = {self.signal_tf: livebars.LIVE_CLOSED_BARS[self.signal_tf]}

    def _rows(self, bars, ctx) -> tuple[list, int]:
        now = int(ctx["now_ms"])
        return livebars.intraday_rows(bars.get(self.signal_tf), self.signal_tf, now), now

    def signals(self, bars, ctx) -> list[Intent]:
        rows, now = self._rows(bars, ctx)
        act = paper_rules.decide_from_rows(RULE, daily=[], intraday=rows, btc_rows=None, position=None,
                                           params=self.rule_params, now_ms=now)
        if not act or str(act.get("action") or "").upper() != "OPEN":
            return []
        lo, hi = (float(x) for x in act["risk_atr_bounds"])
        return [Intent(signal_ts=int(act["signal_ts"]), side=str(act["direction"]).upper(),
                       entry_ref=EntryRef(price=float(act["signal_close"]), max_drift_pct=self.drift),
                       stop=float(act["stop"]), targets=(), exit_rule=EXIT_LIVE_RULE,
                       max_hold_bars=int(donchian_trend.MAX_BARS), one_entry_per_signal=bool(act.get("one_entry_per_signal")),
                       risk_atr_bounds=RiskAtrBounds(lo, hi, float(act["atr14"])),
                       evidence={"rule": RULE, "reason": act.get("reason"), "channel_high20": act.get("channel_high20"),
                                 "atr14": act.get("atr14")})]

    def manage(self, pos: PosView, bars, ctx) -> Action:
        rows, now = self._rows(bars, ctx)
        act = paper_rules.decide_from_rows(RULE, daily=[], intraday=rows, btc_rows=None,
                                           position={"opened_at": int(pos.opened_ms)}, params=self.rule_params, now_ms=now)
        if act and str(act.get("action") or "").upper() == "CLOSE":
            return close(str(act.get("reason") or "RULE_EXIT"))
        return NONE


def make(variant_id: str, params: Mapping[str, Any]) -> DonchianCopy:
    return DonchianCopy(variant_id, params)


__all__ = ["DonchianCopy", "LIVE_MODULES", "PARAM_KEYS", "RULE", "make"]

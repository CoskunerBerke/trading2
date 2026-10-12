"""P3b — `K_D4`: canlı 4h Donchian 20/10 gözlem defterinin kopyası (SAF).

Yeniden yazılmaz: karar `paper_rules.decide_from_rows("d4_donchian_20_10", …)` (→ `donchian_trend.decide`, göstergeler
laboratuvarın `signal_lab.indicators`/`aux_series`'i). Yapı katmanı yoktur (canlıda da yok). Giriş penceresi canlı
değerdir (60 dk); çekirdeğin "sonraki bar açılışı" dolumu pencerenin içindedir. Risk aralığı (0,1–10 ATR) ve "aynı
sinyalle tek giriş" niyete taşınır; zaman sınırı (300 × 4h) ve kanal çıkışı `manage`dedir (canlı kural), niyetin
`max_hold_bars`'ı aynı sınırı çekirdekte de yazar (aynı an).

**Ön-süzgeç (P3b-2b, §5.3; `fast=True`).** Kuralın göstergeleri (`signal_lab`, pandas) her 4h barda 400 satırda yeniden
hesaplanır (kopyanın süresinin ≈ %75'i). `donchian_trend.decide`'ın kendi koşullarından GEREK koşullar, kuralın
satırlarıyla aynı ham değerlerden (`float(bar[k])`; kural satırları ham pencerenin son 400'üdür): OPEN ancak
`_fresh_breakout` ile — c[i] > hi20[i] ve c[i−1] ≤ hi20[i−1], hi20 = ÖNCEKİ 20 barın en yükseği (`aux_series`:
`high.rolling(20).max().shift(1)`; max kesin, aritmetik yok) — mümkündür; tutmuyorsa kural çağrılmaz. CLOSE ancak
girişten sonra kapanmış bir bar k'da kapanış < lo10[k] (ÖNCEKİ 10 barın en düşüğü, kuralın 400 satırında k ≥ 10) ya da
zaman sınırı (`MAX_BARS`) ile mümkündür; kuralın taradığı aynı satırlarda hiçbiri yoksa kural çağrılmaz. Süzgeç
okuyamazsa (eksik/bozuk alan, kapanmamış bar) karar vermez (kural çağrılır). Eşitlik çekirdek koşusunda `fast=False` ile
bayt-aynılıkla testlidir.
"""
from __future__ import annotations

from typing import Any, Mapping

from .. import donchian_trend, paper_rules
from . import livebars
from .contract import (EXIT_LIVE_RULE, NONE, Action, EntryRef, Intent, PosView, RiskAtrBounds, Variant, check_params,
                       close)

PARAM_KEYS = ("entry_window_min", "breakeven_at_mfe_r", "max_entry_drift_pct")
RULE = "d4_donchian_20_10"
#: kuralın satır penceresi (`paper_rules.intraday_rows` varsayılan `tail`; testte eşitlik)
RULE_ROWS = 400
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
    #: hızlı yol (FastFrame + ön-süzgeç); False = canlı (pandas) yol, süzgeçsiz
    fast = True

    def __init__(self, variant_id: str, params: Mapping[str, Any]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if float(p["breakeven_at_mfe_r"]) != 0.0:
            raise ValueError(f"{variant_id}: breakeven_at_mfe_r ≠ 0 çekirdekte modellenmez (P3b-1)")
        self.variant_id = variant_id
        self.params = dict(p)
        self.drift = float(p["max_entry_drift_pct"]) or None
        self.rule_params = paper_rules.build_params(RULE, rule_params={"entry_window_min": int(p["entry_window_min"])})
        self.lookback = {self.signal_tf: livebars.LIVE_CLOSED_BARS[self.signal_tf]}
        self.step = donchian_trend.STEP_MS

    def _rows(self, bars, ctx) -> tuple[list, int]:
        now = int(ctx["now_ms"])
        return livebars.intraday_rows(bars.get(self.signal_tf), self.signal_tf, now, fast=self.fast), now

    # ------------------------------------------------------------------ ön-süzgeç (modül belgesi)
    def _window(self, bars, now: int) -> tuple[list[float], list[float], list[float], list[int]] | None:
        raw = list(bars.get(self.signal_tf) or ())[-RULE_ROWS:]
        try:
            ts = [int(r["timestamp"]) for r in raw]
            if (ts and ts[-1] + self.step > now) or any(a >= b for a, b in zip(ts, ts[1:])):
                return None                                 # kapanmamış / sırasız: süzgeç karar vermez
            return ([float(r["high"]) for r in raw], [float(r["low"]) for r in raw], [float(r["close"]) for r in raw], ts)
        except (KeyError, TypeError, ValueError):
            return None

    def cannot_open(self, bars, now: int) -> bool:
        w = self._window(bars, now)
        if w is None:
            return False
        h, _lo, c, _ts = w
        n = len(c)
        if n < donchian_trend.MIN_BARS or n < 22:
            return True                                     # kural: < MIN_BARS satır → None
        hi_now, hi_prev = max(h[-21:-1]), max(h[-22:-2])
        return not (c[-1] > hi_now and c[-2] <= hi_prev)

    def cannot_close(self, pos: PosView, bars, now: int) -> bool:
        w = self._window(bars, now)
        if w is None:
            return False
        _h, lo, c, ts = w
        n = len(c)
        if n < donchian_trend.MIN_BARS:
            return True
        opened = int(pos.opened_ms)
        entry_bar_open = opened // self.step * self.step
        if ts[-1] + self.step - entry_bar_open >= donchian_trend.MAX_BARS * self.step:
            return False                                    # zaman sınırı: kural çağrılır
        for k in range(10, n):
            if ts[k] + self.step <= opened:
                continue
            if c[k] < min(lo[k - 10:k]):
                return False                                # kanal çıkışı mümkün: kural çağrılır
        return True

    # ------------------------------------------------------------------ paylaşılan akış anahtarları (`stream`)
    def signal_key(self, ctx) -> Any:
        return None                                         # `signals` geçmişe bakmaz (tek giriş çekirdekte)

    def manage_key(self, pos: PosView, ctx) -> Any:
        return ("D4", int(pos.opened_ms))                   # `manage` yalnız açılış anını okur

    # ------------------------------------------------------------------ sözleşme
    def signals(self, bars, ctx) -> list[Intent]:
        if self.fast and self.cannot_open(bars, int(ctx["now_ms"])):
            return []
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
        if self.fast and self.cannot_close(pos, bars, int(ctx["now_ms"])):
            return NONE
        rows, now = self._rows(bars, ctx)
        act = paper_rules.decide_from_rows(RULE, daily=[], intraday=rows, btc_rows=None,
                                           position={"opened_at": int(pos.opened_ms)}, params=self.rule_params, now_ms=now)
        if act and str(act.get("action") or "").upper() == "CLOSE":
            return close(str(act.get("reason") or "RULE_EXIT"))
        return NONE


def make(variant_id: str, params: Mapping[str, Any]) -> DonchianCopy:
    return DonchianCopy(variant_id, params)


__all__ = ["DonchianCopy", "LIVE_MODULES", "PARAM_KEYS", "RULE", "RULE_ROWS", "make"]

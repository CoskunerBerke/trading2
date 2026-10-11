"""P3b — `K_C4_CV001` … `K_C4_CV008`: canlı C4 (4h mum varyasyonları, standart hüküm) defterinin varyasyon başına kopyası.

**Bağdaştırıcı (saf çağrılamayan canlı fonksiyon, belge §8.4 P3b-1 notu).** Canlı giriş `candle_book.decide` → `_matched`
her çağrıda varyasyonun KAPISINI (`candle_variations.gate`) çalıştırır; kapı laboratuvar kaydını DOSYADAN okur ve ret
günlüğe yazılır (`lab_info` de dosya okur). Bu G/Ç saf sözleşmeye girmez. Bu yüzden `signals` kapıyı ATLAYIP girişin saf
parçalarını canlı modüllerin KENDİ fonksiyonlarıyla aynı sırada çağırır: pencere `candle_book.window_rows` →
`candle_dsl.window_from_rows` → giriş penceresi (`entry_window_min`, `_matched` ile aynı eşitsizlik) →
`candle_variations.get` (kayıttaki tanım; kapısız) → `candle_dsl.detect_last`; eylem alanları `candle_book.decide`'ın
OPEN sözlüğüyle aynıdır (stop, ATR14, risk aralığı, tek giriş, hedef GİRİŞTEN `target_r` × risk). Kapı mühür anında
sekiz varyasyonun hepsinde açıktır (testte `candle_variations.gate` ile sınanır); kapı kapanırsa kopya hâlâ sinyal verir
— parite (b) bunu `PARITY_FAIL` olarak yakalar. Kapı kararının dosya okuması motor tarafındadır, bu pakette değil.
`manage` canlı fonksiyonun kendisidir (`paper_rules.decide_from_rows` → `candle_book.decide` → `_time_stop`; açık
pozisyonda kapı çağrılmaz, saf). Parite (a) bağdaştırıcıyı canlı `decide`'a (kapı dahil) karşı sınar.
"""
from __future__ import annotations

from typing import Any, Mapping

from .. import candle_book, candle_dsl, candle_variations, paper_rules
from . import livebars
from .contract import (EXIT_LIVE_RULE, NONE, TARGET_R_FROM_ENTRY, Action, EntryRef, Intent, PosView, RiskAtrBounds,
                       Target, Variant, check_params, close)

PARAM_KEYS = ("entry_window_min", "breakeven_at_mfe_r", "max_entry_drift_pct", "variation")
RULE = "c4_candle_variations"
#: kimliğe giren canlı modüller (`identity.module_src_sha`; çalışma izinin üst kümesi, test)
LIVE_MODULES: tuple[str, ...] = (
    "tradingbot.paper_rules",           # decide_from_rows / build_params: manage'in giriş noktası
    "tradingbot.candle_book",           # window_rows, decide, _time_stop, STEP_MS, SETUP_PREFIX
    "tradingbot.candle_dsl",            # window_from_rows, detect_last (varyasyon tanımının yorumlayıcısı)
    "tradingbot.candle_variations",     # varyasyon TANIMI (get; definition_sha) — yürümese de kararı belirler
    "tradingbot.signal_lab",            # candle_dsl'in göstergeleri
    "tradingbot.candle_confirmation",   # closed_bars
    "tradingbot.timeframes",            # tf_ms
)


class CandleCopy(Variant):
    family = "K"
    signal_tf = candle_book.TIMEFRAME
    timeframes = (candle_book.TIMEFRAME,)
    needs_btc = False
    live_modules = LIVE_MODULES

    def __init__(self, variant_id: str, params: Mapping[str, Any]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if float(p["breakeven_at_mfe_r"]) != 0.0:
            raise ValueError(f"{variant_id}: breakeven_at_mfe_r ≠ 0 çekirdekte modellenmez (P3b-1)")
        self.variant_id = variant_id
        self.params = dict(p)
        self.variation_id = str(p["variation"])
        self.var = candle_variations.get(self.variation_id)          # kayıttaki tanım (kapısız; bilinmeyen → ValueError)
        self.drift = float(p["max_entry_drift_pct"]) or None
        self.rule_params = paper_rules.build_params(RULE, rule_params={"entry_window_min": int(p["entry_window_min"]),
                                                                       "variations": [self.variation_id]})
        self.lookback = {self.signal_tf: livebars.LIVE_CLOSED_BARS[self.signal_tf]}

    def signals(self, bars, ctx) -> list[Intent]:
        now = int(ctx["now_ms"])
        rows = livebars.candle_rows(bars.get(self.signal_tf), now)
        win, _why = candle_dsl.window_from_rows(rows, candle_book.STEP_MS)
        if win is None:
            return []
        signal_close_ms = int(win.ts[-1]) + candle_book.STEP_MS
        if now - signal_close_ms > int(self.rule_params.entry_window_min) * 60_000:
            return []                                   # sinyal kaçtı (candle_book._matched ile aynı eşitsizlik)
        var = self.var
        hit = candle_dsl.detect_last(win, var)
        if hit is None:
            return []
        targets = (Target(TARGET_R_FROM_ENTRY, float(var.target_r)),) if var.target_r is not None else ()
        lo, hi = (float(x) for x in var.risk_atr_bounds)
        return [Intent(signal_ts=int(hit.signal_ts), side=str(var.side).upper(),
                       entry_ref=EntryRef(price=float(hit.close), max_drift_pct=self.drift), stop=float(hit.stop),
                       targets=targets, exit_rule=EXIT_LIVE_RULE, max_hold_bars=int(var.max_hold_bars),
                       one_entry_per_signal=True, risk_atr_bounds=RiskAtrBounds(lo, hi, float(hit.atr_i)),
                       evidence={"rule": RULE, "reason": "CANDLE_" + var.id,
                                 "setup_type": candle_book.SETUP_PREFIX + var.id,
                                 "variation": {"id": var.id, "definition_sha": var.definition_sha,
                                               "max_hold_bars": int(var.max_hold_bars), "target_r": var.target_r},
                                 "signal_close_ms": int(hit.signal_close_ms)})]

    def manage(self, pos: PosView, bars, ctx) -> Action:
        now = int(ctx["now_ms"])
        rows = livebars.candle_rows(bars.get(self.signal_tf), now)
        ev = pos.intent.evidence
        position = {"opened_at": int(pos.opened_ms), "setup_type": ev.get("setup_type") or "",
                    "features": {"candle_variation": dict(ev.get("variation") or {})}}
        act = paper_rules.decide_from_rows(RULE, daily=[], intraday=rows, btc_rows=None, position=position,
                                           params=self.rule_params, now_ms=now)
        if act and str(act.get("action") or "").upper() == "CLOSE":
            return close(str(act.get("reason") or "RULE_EXIT"))
        return NONE


def make(variant_id: str, params: Mapping[str, Any]) -> CandleCopy:
    return CandleCopy(variant_id, params)


__all__ = ["CandleCopy", "LIVE_MODULES", "PARAM_KEYS", "RULE", "make"]

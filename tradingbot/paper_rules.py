# -*- coding: utf-8 -*-
"""KÂĞIT DEFTER KURAL KAYDI — hangi defter hangi kural modülüne, hangi zaman dilimlerine ve BTC
referansına ihtiyaç duyar (V15).

Neden ayrı modül: `StrategyBook.step` 2026-09-17'ye kadar TEK bir kural modülüne (`ema200_trend`) ve TEK
bir dilim demetine (`("1d",)`) sabit bağlıydı. Box Theory gün içi 5m barda tetiklendiği için defterin
doğrulayacağı çerçeve demeti DEFTERE GÖRE değişmek zorunda. Bunu `step` içinde `if name == ...` ile
dallandırmak, veri doğrulamasının (`verify_paper_data`) hangi çerçeveyi istediğini iki ayrı yere yazardı;
o ayrışma daha önce dört kez gerçek kusur üretti (bkz. backtest/üretim paritesi). Kayıt TEK yer:
dilimler, BTC ihtiyacı, karar ve gösterim çağrısı buradan sorulur.

Kural modüllerinin kendisi bu dosyayı İÇE AKTARMAZ — bağımlılık tek yönlüdür (kayıt → kural).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from . import box_theory, ema200_trend
from .candle_confirmation import closed_bars

TREND_VARIANTS: tuple[str, ...] = tuple(ema200_trend.VARIANTS)
BOX_VARIANTS: tuple[str, ...] = tuple(box_theory.VARIANTS)

#: Trend/momentum aileleri yalnız günlük bar okur; box ayrıca gün içi 5m okur.
TREND_TIMEFRAMES: tuple[str, ...] = ("1d",)
BOX_TIMEFRAMES: tuple[str, ...] = ("1d", "5m")


@dataclass(frozen=True)
class RuleSpec:
    """Bir defterin kural kimliği: hangi aile, hangi çerçeveler, BTC rejimi gerekli mi."""

    name: str
    family: str                       # "trend" | "box"
    timeframes: tuple[str, ...]
    needs_btc: bool


_SPECS: dict[str, RuleSpec] = {}
for _v in TREND_VARIANTS:
    _SPECS[_v] = RuleSpec(_v, "trend", TREND_TIMEFRAMES, needs_btc=True)
for _v in BOX_VARIANTS:
    # Box kuralı BTC rejimine HİÇ bakmaz; BTC çerçevesini şart koşmak kanıtsız bir kapı olurdu.
    _SPECS[_v] = RuleSpec(_v, "box", BOX_TIMEFRAMES, needs_btc=False)

VARIANTS: tuple[str, ...] = tuple(_SPECS)


def spec_for(name: str) -> RuleSpec:
    """Defterin kural kimliği. Bilinmeyen ad SESSİZCE trend'e düşmez: ValueError."""
    try:
        return _SPECS[str(name)]
    except KeyError:
        raise ValueError("bilinmeyen kağıt defter kuralı: %r (geçerli: %s)"
                         % (name, ", ".join(VARIANTS))) from None


def rule_timeframes(name: str) -> tuple[str, ...]:
    return spec_for(name).timeframes


def needs_btc(name: str) -> bool:
    return spec_for(name).needs_btc


def build_params(name: str, *, atr_mult: float = ema200_trend.DEFAULT_ATR_MULT,
                 rule_params: dict[str, Any] | None = None) -> Any:
    """Defter ayarlarını kuralın beklediği parametre nesnesine çevirir.

    trend → `atr_mult` (float) · box → doğrulanmış `BoxParams`. Bilinmeyen box alanı SESSİZCE yutulmaz:
    `BoxParams(**...)` TypeError verir, config kapısı bunu yakalar.
    """
    sp = spec_for(name)
    if sp.family == "trend":
        return float(atr_mult)
    return box_theory.BoxParams(**dict(rule_params or {})).validate()


def _rows(frames: dict | None, tf: str, now_ms: int, tail: int) -> list[dict[str, Any]]:
    """Kapanmis satirlar. Gunluk okuma `ema200_trend`in sutun secimini KORUR (ema200/atr14 varsa gelir)."""
    fr = (frames or {}).get(tf)
    rows = (ema200_trend.daily_rows_from_frame(fr, tail=tail) if str(tf) == "1d"
            else box_theory.rows_from_frame(fr, tail=tail))
    return closed_bars(rows, now_ms=now_ms, tf=tf)


def daily_rows(frames: dict | None, *, now_ms: int, tail: int = 320) -> list[dict[str, Any]]:
    """Kapanmış günlük satırlar — `step` ve gösterim AYNI okumayı kullanır (ikinci yol yok)."""
    return _rows(frames, "1d", now_ms, tail)


def intraday_rows(frames: dict | None, *, tf: str, now_ms: int, tail: int = 400) -> list[dict[str, Any]]:
    return _rows(frames, tf, now_ms, tail)


def _opened_ms(position: Any) -> int | None:
    """Pozisyonun açılış anı (ms). Okunamıyorsa None — gün sonu kapanışı KANITSIZ tetiklenmez."""
    if position is None:
        return None
    raw = getattr(position, "opened_at", None)
    if raw is None and isinstance(position, dict):
        raw = position.get("opened_at") or position.get("opened_ts")
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    try:
        s = str(raw).replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return int(d.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def decide_from_rows(name: str, *, daily: list[dict[str, Any]], intraday: list[dict[str, Any]] | None,
                     btc_rows: list[dict[str, Any]] | None, position: Any = None,
                     params: Any = None) -> dict[str, Any] | None:
    """Karar — SATIRLARDAN (SAF). Motor çerçeveden, panel/araştırma satırdan gelir; ikisi de BURAYA düşer."""
    sp = spec_for(name)
    if sp.family == "trend":
        mult = float(params if params is not None else ema200_trend.DEFAULT_ATR_MULT)
        return ema200_trend.decide(name, daily_rows=daily, btc_daily_rows=btc_rows,
                                   position_open=position is not None, atr_mult=mult)
    p = params if isinstance(params, box_theory.BoxParams) else box_theory.DEFAULT_PARAMS
    pos = None
    if position is not None:
        opened = _opened_ms(position)
        if opened is None:
            return None            # açılış anı okunamıyorsa gün sonu hükmü verilemez (fail-closed)
        pos = {"opened_ts": opened}
    return box_theory.decide(name, daily_rows=daily, m5_rows=list(intraday or []), position=pos, params=p)


def state_from_rows(name: str, *, daily: list[dict[str, Any]], intraday: list[dict[str, Any]] | None,
                    btc_rows: list[dict[str, Any]] | None, params: Any = None) -> dict[str, Any]:
    """Kuralın karşılaştırdığı değerler — SATIRLARDAN (SAF), `decide_from_rows` ile AYNI okuma."""
    sp = spec_for(name)
    if sp.family == "trend":
        mult = float(params if params is not None else ema200_trend.DEFAULT_ATR_MULT)
        return ema200_trend.rule_state(name, daily_rows=daily, btc_daily_rows=btc_rows, atr_mult=mult)
    p = params if isinstance(params, box_theory.BoxParams) else box_theory.DEFAULT_PARAMS
    return box_theory.rule_state(name, daily_rows=daily, m5_rows=list(intraday or []), params=p)


def _frames_rows(name: str, frames: dict | None, now_ms: int) -> tuple[list, list | None]:
    sp = spec_for(name)
    d1 = daily_rows(frames, now_ms=now_ms)
    intra = None
    for tf in sp.timeframes:
        if tf != "1d":
            intra = intraday_rows(frames, tf=tf, now_ms=now_ms)
    return d1, intra


def decide_for(name: str, *, frames: dict | None, btc_rows: list[dict[str, Any]] | None,
               now_ms: int, position: Any = None, params: Any = None) -> dict[str, Any] | None:
    """Defterin kararı — ÇERÇEVEDEN (motor yolu). Okumayı yapar, kararı `decide_from_rows`a bırakır."""
    d1, intra = _frames_rows(name, frames, now_ms)
    return decide_from_rows(name, daily=d1, intraday=intra, btc_rows=btc_rows, position=position, params=params)


def state_for(name: str, *, frames: dict | None, btc_rows: list[dict[str, Any]] | None,
              now_ms: int, params: Any = None) -> dict[str, Any]:
    """Kuralın karşılaştırdığı değerler — ÇERÇEVEDEN (motor yolu)."""
    d1, intra = _frames_rows(name, frames, now_ms)
    return state_from_rows(name, daily=d1, intraday=intra, btc_rows=btc_rows, params=params)


__all__ = ["BOX_TIMEFRAMES", "BOX_VARIANTS", "TREND_TIMEFRAMES", "TREND_VARIANTS", "VARIANTS", "RuleSpec",
           "build_params", "daily_rows", "decide_for", "decide_from_rows", "intraday_rows", "needs_btc",
           "rule_timeframes", "spec_for", "state_for", "state_from_rows"]

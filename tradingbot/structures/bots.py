# -*- coding: utf-8 -*-
"""BOT BAĞDAŞTIRICILARI — ortak analiz + sürümlü politika → her botun KENDİ karar sözlüğü.

Canlı `StrategyBook.step` ve replay strateji modu AYNI fonksiyonları (`paper_rules.decide_with_structures`) çağırır;
ana bot ve formasyon botu kendi yollarında aynı `analyze` + `policy` çağrılarını yapar. Mod:
* OFF     — bu modül hiç çağrılmaz (davranış bit-bit eski).
* SHADOW  — analiz ve karar hesaplanır/kaydedilir, botun kararı DEĞİŞMEZ.
* ENFORCE — politika kararı uygulanır (bekle/iptal → giriş yok; giriş adayı → kuralın girişi; çıkış → CLOSE).
Risk/boyut/maliyet kapıları `apply_action`ta aynen kalır; burada yalnız karar SÖZLÜĞÜ üretilir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..timeframes import DAY_MS
from . import catalog as K
from . import policy as P
from .analysis import analyze

MODE_OFF, MODE_SHADOW, MODE_ENFORCE = "OFF", "SHADOW", "ENFORCE"
PAPER_MARKET = "USDM_PERP"


@dataclass
class StructureContext:
    """Bir sembolün yapı kararı için bağlam. `price`: doğrulanmış güncel perp fiyatı (kovalama sınırı)."""

    mode: str
    symbol: str
    as_of_ms: int
    market: str = PAPER_MARKET
    price: float | None = None
    used_patterns: set[str] = field(default_factory=set)
    provenance: dict[str, Any] = field(default_factory=dict)


def compact(dec: dict[str, Any] | None) -> dict[str, Any] | None:
    """İşlem kaydına/aksiyona giren kısa yapı referansı (panel ve M2 çıkışı bununla okur)."""
    if not dec:
        return None
    pr = dec.get("primary") or {}
    return {"bot": dec.get("bot"), "policy_version": dec.get("policy_version"), "action": dec.get("action"),
            "reason_code": dec.get("reason_code"), "pattern_id": pr.get("pattern_id"), "name": pr.get("name"),
            "family": pr.get("family"), "timeframe": pr.get("timeframe"), "status": pr.get("status"),
            "side": pr.get("side"), "confirmed_at_ms": pr.get("confirmed_at_ms"), "analysis_id": pr.get("analysis_id"),
            "trigger": pr.get("trigger"), "invalidation": pr.get("invalidation"), "stop": pr.get("stop"),
            "targets": pr.get("targets"), "text_tr": dec.get("text_tr"), "as_of_ms": dec.get("as_of_ms")}


def used_patterns_of(ledger: Any) -> set[str]:
    """Defterde girişe dayanak olmuş yapı kimlikleri (açık + kapanmış işlemler) — aynı yapı ikinci işlem açmaz;
    kaynak defterin kendisidir (yeniden başlatmada kaybolmaz)."""
    out: set[str] = set()
    for pos in list(getattr(ledger, "positions", {}).values()):
        pid = ((getattr(pos, "features", None) or {}).get("structure") or {}).get("pattern_id")
        if pid:
            out.add(str(pid))
    for rec in list(getattr(ledger, "history", []) or [])[-2000:]:
        f = rec.features if isinstance(getattr(rec, "features", None), dict) else {}
        pid = (f.get("structure") or {}).get("pattern_id")
        if pid:
            out.add(str(pid))
    return out


def _opened_ms(position: Any) -> int | None:
    from ..paper_rules import _opened_ms as om
    return om(position)


def _entry_pid(position: Any) -> str | None:
    f = getattr(position, "features", None)
    if f is None and isinstance(position, dict):
        f = position.get("features")
    return ((f or {}).get("structure") or {}).get("pattern_id")


def _side_of(position: Any) -> str | None:
    s = getattr(position, "side", None)
    if s is None and isinstance(position, dict):
        s = position.get("side")
    s = getattr(s, "value", s)
    return str(s).upper() if s else None


# ---------------------------------------------------------------------------- T2 / M2 (1d trend/momentum)
def trend_decide(name: str, *, daily_rows: list[dict[str, Any]], base: dict[str, Any] | None, position: Any,
                 ctx: StructureContext) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    """(uygulanacak aksiyon, yapı kararı, analizler). Kuralın yönü (LONG) yapı yüzünden değişmez."""
    pol = P.POLICIES[name]
    an = analyze(market=ctx.market, symbol=ctx.symbol, timeframe="1d", bars=daily_rows, as_of_ms=ctx.as_of_ms,
                 data_provenance=ctx.provenance)
    analyses = {"1d": an}
    enforce = ctx.mode == MODE_ENFORCE
    if position is None:
        if not base or str(base.get("action") or "").upper() != "OPEN":
            dec = P._decision(pol, P.ACT_NO_EFFECT, "BASE_RULE_NO_ENTRY", side=None, as_of_ms=ctx.as_of_ms, analyses=analyses,
                              text_tr="Girmedi: botun kendi kuralı giriş koşulunu vermedi (%s)." % ((base or {}).get("reason") or "sinyal yok"))
            return base, dec, analyses
        dec = P.entry_decision(pol, intended_side=str(base.get("direction") or "LONG"), analyses=analyses,
                               as_of_ms=ctx.as_of_ms, price=ctx.price, used_patterns=ctx.used_patterns)
        if enforce and dec["action"] in (P.ACT_WAIT, P.ACT_WAIT_TRIGGER, P.ACT_CANCEL):
            return ({"action": "NONE", "reason": "STRUCTURE_" + dec["reason_code"], "name": name,
                     "structure": compact(dec)}, dec, analyses)
        act = dict(base)
        act["structure"] = compact(dec)
        if not enforce:
            act["structure"]["shadow"] = True
        return act, dec, analyses
    # açık pozisyon: kuralın kendi çıkışı önceliklidir
    if base and str(base.get("action") or "").upper() == "CLOSE":
        dec = P._decision(pol, P.ACT_NO_EFFECT, "BASE_RULE_EXIT", side=_side_of(position), as_of_ms=ctx.as_of_ms,
                          analyses=analyses, text_tr="Çıktı: botun kendi kuralı (%s)." % base.get("reason"))
        return base, dec, analyses
    dec = P.hold_decision(pol, position_side=_side_of(position) or "LONG", opened_at_ms=_opened_ms(position),
                          entry_pattern_id=_entry_pid(position), analyses=analyses, as_of_ms=ctx.as_of_ms)
    if enforce and dec["action"] == P.ACT_EXIT:
        return ({"action": "CLOSE", "reason": dec["reason_code"], "name": name, "structure": compact(dec)}, dec, analyses)
    return base, dec, analyses


# ---------------------------------------------------------------------------- Box (5m, önceki günün kutusu)
def box_decide(name: str, *, daily_rows: list[dict[str, Any]], m5_rows: list[dict[str, Any]], base: dict[str, Any] | None,
               position: Any, params: Any, ctx: StructureContext) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    """Kenarda dönüş / taşma-geri dönüş → fade girişi; teyitli dış kırılım → o kenarın planı İPTAL, açık fade ÇIKIŞ."""
    from .. import box_theory
    pol = P.POLICIES[P.BOT_BOX]
    enforce = ctx.mode == MODE_ENFORCE
    rs = box_theory.rule_state(name, daily_rows=daily_rows, m5_rows=m5_rows, params=params)
    if not rs.get("box_high") or not rs.get("box_low") or not m5_rows:
        dec = P._decision(pol, P.ACT_NO_EFFECT, "BOX_UNMEASURABLE:%s" % (rs.get("reason") or "?"), side=None,
                          as_of_ms=ctx.as_of_ms, analyses={}, text_tr="Kutu ölçülemedi (%s)." % (rs.get("reason") or "?"))
        return (None if enforce and position is None else base), dec, {}
    hi, lo = float(rs["box_high"]), float(rs["box_low"])
    last_ts = int(m5_rows[-1]["timestamp"])
    day0 = last_ts - (last_ts % DAY_MS)
    refs = [{"name": "BOX_HIGH", "level": hi, "kind": "high", "valid_from_ms": day0},
            {"name": "BOX_LOW", "level": lo, "kind": "low", "valid_from_ms": day0}]
    an = analyze(market=ctx.market, symbol=ctx.symbol, timeframe="5m", bars=m5_rows, as_of_ms=ctx.as_of_ms,
                 data_provenance=ctx.provenance, reference_levels=refs)
    analyses = {"5m": an}
    band = (hi - lo) * float(getattr(params, "near_frac", 0.10))
    if position is not None:
        if base and str(base.get("action") or "").upper() == "CLOSE":
            dec = P._decision(pol, P.ACT_NO_EFFECT, "BASE_RULE_EXIT", side=_side_of(position), as_of_ms=ctx.as_of_ms,
                              analyses=analyses, text_tr="Çıktı: gün sonu düzleşmesi (Box kuralı).")
            return base, dec, analyses
        side = _side_of(position) or K.SHORT
        edge = "BOX_HIGH" if side == K.SHORT else "BOX_LOW"
        dec = P.hold_decision(pol, position_side=side, opened_at_ms=_opened_ms(position), entry_pattern_id=None,
                              analyses=analyses, as_of_ms=ctx.as_of_ms, accept=lambda r, _e=edge: r.get("reference") == _e)
        if enforce and dec["action"] == P.ACT_EXIT:
            return ({"action": "CLOSE", "reason": dec["reason_code"], "name": name, "structure": compact(dec)}, dec, analyses)
        return base, dec, analyses
    loc = rs.get("location")
    intended = K.SHORT if (loc == "TOP" and getattr(params, "allow_short", True)) else \
        (K.LONG if (loc == "BOTTOM" and getattr(params, "allow_long", True)) else None)
    if intended is None:
        dec = P._decision(pol, P.ACT_NO_EFFECT, "BOX_NOT_AT_EDGE:%s" % loc, side=None, as_of_ms=ctx.as_of_ms, analyses=analyses,
                          text_tr="Girmedi: fiyat kutunun kenarında değil (%s)." % loc)
        return (None if enforce else base), dec, analyses
    edge = "BOX_HIGH" if intended == K.SHORT else "BOX_LOW"
    brk_side = K.LONG if intended == K.SHORT else K.SHORT
    brk = [r for r in an["records"] if r.get("name") == P.BREAKOUT and r.get("reference") == edge and r.get("side") == brk_side
           and r.get("status") == K.ST_CONFIRMED]
    if brk:
        dec = P._decision(pol, P.ACT_CANCEL, "BOX_OUTSIDE_BREAKOUT", side=intended, as_of_ms=ctx.as_of_ms, analyses=analyses,
                          rec=brk[0], text_tr="İptal: kutu %s dışında teyitli kırılım (ardışık %d kapanış); aksi yöndeki dönüş planı iptal." % (
                              "tepesinin" if intended == K.SHORT else "dibinin", K.DEFAULT_CONFIG.breakout_hold_closes))
        if enforce:
            return {"action": "NONE", "reason": "STRUCTURE_BOX_OUTSIDE_BREAKOUT", "name": name, "structure": compact(dec)}, dec, analyses

    def accept(r: dict[str, Any], _side=intended, _edge=edge) -> bool:
        if r.get("name") == P.SWEEP:
            return r.get("reference") == _edge
        if r.get("family") != K.FAMILY_CANDLE:
            return False
        return (float(r.get("pattern_high") or 0) >= hi - band) if _side == K.SHORT else (float(r.get("pattern_low") or 1e30) <= lo + band)

    if not brk:
        dec = P.entry_decision(pol, intended_side=intended, analyses=analyses, as_of_ms=ctx.as_of_ms, price=ctx.price,
                               used_patterns=ctx.used_patterns, accept=accept)
    if not enforce:
        act = dict(base) if base else None
        if act is not None:
            act["structure"] = dict(compact(dec) or {}, shadow=True)
        return act, dec, analyses
    if dec["action"] != P.ACT_ENTER:
        why = "BOX_NO_CONFIRMED_EDGE_STRUCTURE" if dec["action"] == P.ACT_NO_EFFECT else "STRUCTURE_" + dec["reason_code"]
        if dec["action"] == P.ACT_NO_EFFECT:
            dec = dict(dec, text_tr="Girmedi: kutu %s kenarında teyitli dönüş mumu ya da taşma-geri dönüş yok." % (
                "üst" if intended == K.SHORT else "alt"))
        return {"action": "NONE", "reason": why, "name": name, "structure": compact(dec)}, dec, analyses
    rec = dec["primary"] or {}
    px = float(ctx.price) if ctx.price else float((rec.get("confirm_bar") or {}).get("close") or 0.0)
    stop = rec.get("stop")
    if stop is None or px <= 0 or not math.isfinite(float(stop)):
        return {"action": "NONE", "reason": "BOX_STRUCTURE_STOP_UNDEFINED", "name": name, "structure": compact(dec)}, dec, analyses
    stop = float(stop)
    if (intended == K.SHORT and stop <= px) or (intended == K.LONG and stop >= px):
        dec = dict(dec, action=P.ACT_CANCEL, reason_code="MARK_BEYOND_STRUCTURE_STOP",
                   text_tr="İptal: güncel fiyat yapının stop seviyesinin ötesinde (giriş anında yapı bozulmuş).")
        return {"action": "NONE", "reason": "STRUCTURE_MARK_BEYOND_STOP", "name": name, "structure": compact(dec)}, dec, analyses
    msp = float(getattr(params, "min_stop_pct", 0.0) or 0.0)
    if msp > 0 and abs(px - stop) / px * 100.0 < msp:
        dec = dict(dec, action=P.ACT_CANCEL, reason_code="BOX_STOP_TOO_TIGHT",
                   text_tr="İptal: yapı stopu %%%.2f; Box asgari stop %%%.2f (maliyet riski yer)." % (abs(px - stop) / px * 100.0, msp))
        return {"action": "NONE", "reason": "STRUCTURE_BOX_STOP_TOO_TIGHT", "name": name, "structure": compact(dec)}, dec, analyses
    cb = rec.get("confirm_bar") or {}
    act = {"action": "OPEN", "direction": intended, "stop": stop,
           "targets": box_theory.targets_for(intended, px, stop, hi, lo, params),
           "leverage": int(getattr(params, "leverage", 1)), "leverage_max": int(getattr(params, "leverage_max", 0) or 0),
           "reason": "BOX_STRUCTURE_%s" % rec.get("name"), "name": name, "setup_type": "box_fade", "location": loc,
           "box_high": hi, "box_low": lo, "box_mid": (hi + lo) / 2.0,
           # kayma kapısı referansı: TEYİT barının kapanışı — dolum bundan geriye YAZILMAZ (canlı mark ile açılır)
           "signal_close": cb.get("close"), "signal_ts": cb.get("ts"), "structure": compact(dec),
           "risk_per_unit": abs(px - stop), "exit_kind": getattr(params, "exit_kind", None)}
    return act, dec, analyses


__all__ = ["MODE_ENFORCE", "MODE_OFF", "MODE_SHADOW", "PAPER_MARKET", "StructureContext", "box_decide", "compact",
           "trend_decide", "used_patterns_of"]

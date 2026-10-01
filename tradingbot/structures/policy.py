# -*- coding: utf-8 -*-
"""SÜRÜMLÜ KARAR POLİTİKASI (`structures_v1`) — bot × yapı ailesi × bağlam → GİRİŞ / BEKLE / İPTAL / YÖNETİM / ETKİSİZ.

Tanım: `docs/structures/POLITIKA_MATRISI_v1.md` (kodlamadan önce yazıldı). SAF: dosya/ağ yok. Girdi, ortak analizin
(`structures.analysis.analyze`) dilim başına sonucudur; bütün botlar AYNI kayıtları okur, farklı ROLLERİYLE karar verir.

Değişmezler:
* Botun niyetli yönü (kural/konsensüs) yapı yüzünden TERSİNE ÇEVRİLMEZ; karşı yapı yalnız bekletir/yönetir.
* Risk, boyut ve maliyet kapıları burada DEĞİLDİR ve atlanamaz: politika yalnız zamanlama, bekleme, iptal ve yönetim üretir.
* Aynı `pattern_id` bir defterde bir kez giriş üretir (`used_patterns`).
* Girişten ÖNCE teyit edilmiş karşı yapı açık pozisyonu yönetemez (`confirmed_at > opened_at` şartı).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..timeframes import tf_ms
from . import catalog as K

ACT_ENTER = "ENTER"
ACT_WAIT = "WAIT"
ACT_WAIT_TRIGGER = "WAIT_TRIGGER"
ACT_CANCEL = "CANCEL"
ACT_EXIT = "EXIT"
ACT_TIGHTEN = "TIGHTEN_STOP"
ACT_NO_EFFECT = "NO_EFFECT"
ACTIONS = (ACT_ENTER, ACT_WAIT, ACT_WAIT_TRIGGER, ACT_CANCEL, ACT_EXIT, ACT_TIGHTEN, ACT_NO_EFFECT)
ACTION_TR = {ACT_ENTER: "giriş adayı", ACT_WAIT: "bekle", ACT_WAIT_TRIGGER: "tetiği bekle", ACT_CANCEL: "iptal",
             ACT_EXIT: "çıkış", ACT_TIGHTEN: "stop sıkılaştır", ACT_NO_EFFECT: "etkisiz"}

BOT_MAIN = "main"
BOT_T2 = "t2_trend_regime"
BOT_M2 = "m2_tsmom28"
BOT_BOX = "b1_box_fade"
BOT_PATTERN = "pattern_trader"
BOTS = (BOT_MAIN, BOT_T2, BOT_M2, BOT_BOX, BOT_PATTERN)

# ---------------------------------------------------------------------------- ad kümeleri (katalogdan türer)
CANDLE_LONG = frozenset(n for d in K.CANDLE_CONTEXT_NAMES.values() for n, s in d.values() if s == K.LONG) | \
    frozenset(n for n, s in K.CANDLE_FIXED_NAMES.values() if s == K.LONG)
CANDLE_SHORT = frozenset(n for d in K.CANDLE_CONTEXT_NAMES.values() for n, s in d.values() if s == K.SHORT) | \
    frozenset(n for n, s in K.CANDLE_FIXED_NAMES.values() if s == K.SHORT)
CHART_REVERSAL_LONG = frozenset({"DOUBLE_BOTTOM", "TRIPLE_BOTTOM", "INVERSE_HEAD_AND_SHOULDERS"})
CHART_REVERSAL_SHORT = frozenset({"DOUBLE_TOP", "TRIPLE_TOP", "HEAD_AND_SHOULDERS"})
CHART_CONT_LONG = frozenset({"BULL_FLAG", "BULL_PENNANT"})
CHART_CONT_SHORT = frozenset({"BEAR_FLAG", "BEAR_PENNANT"})
TRIANGLES = frozenset({"ASCENDING_TRIANGLE", "DESCENDING_TRIANGLE"})
SCEN_CONT = frozenset({"COMPRESSION_BREAKOUT", "BREAK_RETEST_HOLD"})
SWEEP = "SWEEP_RECLAIM"
BREAKOUT = "RANGE_BREAKOUT"


@dataclass(frozen=True)
class BotPolicy:
    """Bir botun yapı politikası (matris satırı). `compatible`/`opposing` None → niyetli/ters taraftaki HER ad."""

    bot: str
    decision_tf: str
    context_tfs: tuple[str, ...] = ()
    compatible: frozenset | None = None
    opposing: frozenset | None = None
    hold_action: str = ACT_NO_EFFECT                  # açık pozisyonda karşı yapı: NO_EFFECT | EXIT | TIGHTEN_STOP
    hold_names: frozenset | None = None               # yönetimi tetikleyen karşı adlar (None → opposing)
    entry_failure_exit: bool = False                  # girişe dayanak yapı bozulursa çık (M2)
    confirmed_required_for: tuple[str, ...] = ()      # bu giriş tiplerinde uyumlu TEYİTLİ yapı şart (ana bot: pullback)
    chase_atr: float = 1.0
    hold_reason: str = ""
    version: str = K.POLICY_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)


POLICIES: dict[str, BotPolicy] = {
    BOT_MAIN: BotPolicy(BOT_MAIN, "4h", ("1d",), None, None, hold_action=ACT_TIGHTEN,
                        confirmed_required_for=("pullback",), hold_reason="MAIN_OPPOSING_STRUCTURE_TIGHTEN"),
    # Matris C satırları BİREBİR (docs/structures/POLITIKA_MATRISI_v1.md). Taraf ayrıca denetlenir: ASCENDING_TRIANGLE
    # yalnız LONG kaydıyla uyumlu, DESCENDING_TRIANGLE yalnız SHORT kaydıyla karşı sayılır.
    BOT_T2: BotPolicy(BOT_T2, "1d", (), compatible=CHART_CONT_LONG | frozenset({"ASCENDING_TRIANGLE"}) | SCEN_CONT | CANDLE_LONG,
                      opposing=CHART_REVERSAL_SHORT | CHART_CONT_SHORT | CANDLE_SHORT | {SWEEP},
                      hold_action=ACT_NO_EFFECT, hold_reason="TREND_EXIT_RULE_ONLY"),
    BOT_M2: BotPolicy(BOT_M2, "1d", (), compatible=CHART_CONT_LONG | frozenset({"ASCENDING_TRIANGLE"}) | SCEN_CONT,
                      opposing=CHART_REVERSAL_SHORT | frozenset({"DESCENDING_TRIANGLE", SWEEP}),
                      hold_action=ACT_EXIT, entry_failure_exit=True, hold_reason="M2_STRUCTURE_EXIT"),
    # Box: kenardaki dönüş mumu / SWEEP_RECLAIM (konum süzgeci `accept` ile kutu kenarı). Karşı-taraf "bekle" YOK;
    # teyitli dış kırılım Box sarmalayıcısında İPTAL (BOX_OUTSIDE_BREAKOUT) ve açık fade'de ÇIKIŞ üretir.
    BOT_BOX: BotPolicy(BOT_BOX, "5m", (), compatible=CANDLE_LONG | CANDLE_SHORT | {SWEEP},
                       opposing=frozenset(), hold_action=ACT_EXIT, hold_names=frozenset({BREAKOUT}),
                       hold_reason="BOX_OUTSIDE_BREAKOUT_EXIT"),
    BOT_PATTERN: BotPolicy(BOT_PATTERN, "15m", ("1h", "4h"), None, None, hold_action=ACT_NO_EFFECT,
                           hold_reason="PATTERN_BOOK_LEDGER_EXITS"),
}


# ---------------------------------------------------------------------------- yardımcılar
def _other(side: str) -> str:
    return K.SHORT if side == K.LONG else K.LONG


def summarize(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Karar kaydına giren kısa yapı özeti (panel aynı alanları çizer)."""
    if not rec:
        return None
    keep = ("pattern_id", "family", "name", "side", "timeframe", "status", "detected_at_ms", "confirmed_at_ms",
            "broken_at_ms", "expires_at_ms", "trigger", "invalidation", "stop", "targets", "anchors", "geometry",
            "geometry_quality", "role", "trend_context", "reference", "analysis_id", "confirm_bar", "atr", "reason_codes")
    return {k: rec.get(k) for k in keep}


def _usable(an: dict[str, Any] | None) -> bool:
    return bool(an) and isinstance(an.get("records"), list)


def _match(rec: dict[str, Any], side: str, names: frozenset | None) -> bool:
    return rec.get("side") == side and (names is None or rec.get("name") in names)


def _recent(ts: int | None, as_of_ms: int, tf: str, bars: int) -> bool:
    return ts is not None and int(ts) >= int(as_of_ms) - (bars + 1) * tf_ms(tf)


def _level_tol_pct() -> float:
    from ..chart_patterns import ChartPatternConfig
    return float(ChartPatternConfig().level_tolerance_pct)      # eşit tepe/dip toleransı (düz çiftler bununla kurulur)


def same_break(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """AYNI KIRILIM (tur-5 doğrulayıcı F2) — yalnız GRAFİK yapıları. Aynı seviyenin farklı dayanaklı yorumları (ör. üç
    eşit tepeden kurulan kardeş üçgenler; aynı diplerden çift ve üçlü dip — aynı boyun çizgisi) aynı kırılımı ayrı
    kimliklerle teyit eder; analiz bu yorumları AYRI kayıt olarak verir (geçmiş, panel), tüketici için ise TEK kırılımdır.
    İkisi de teyitli; aynı sembol, dilim ve taraf (AD karşılaştırılmaz — tur-7: çift/üçlü dip ve bayrak/flama aynı
    kırılımı farklı adla verir); teyit anları arasında en çok `fresh_bars` bar; tetikler eşit-seviye toleransında (%)."""
    if not a or not b:
        return False
    if a.get("family") != K.FAMILY_CHART or b.get("family") != K.FAMILY_CHART:
        return False
    for k in ("symbol", "timeframe", "side"):
        if a.get(k) is None or a.get(k) != b.get(k):
            return False
    ca, cb = a.get("confirmed_at_ms"), b.get("confirmed_at_ms")
    ta, tb = (a.get("trigger") or {}).get("level"), (b.get("trigger") or {}).get("level")
    if ca is None or cb is None or ta is None or tb is None:
        return False
    if abs(int(ca) - int(cb)) > int(K.DEFAULT_CONFIG.fresh_bars) * tf_ms(str(a["timeframe"])):
        return False
    ta, tb = float(ta), float(tb)
    return abs(ta - tb) <= _level_tol_pct() / 100.0 * max(abs(ta), abs(tb), 1e-12)


def already_used(rec: dict[str, Any], used: Any) -> bool:
    """Kayıt bu defterde girişe dayanak olmuş mu: aynı kimlik ya da (grafik yapısında) kullanılmış bir girişle AYNI
    kırılım. `used`: kimlik kümesi; `entries` özniteliği varsa (bots.UsedStructures) girişlerin kayıt özeti."""
    if rec.get("pattern_id") in used:
        return True
    return any(same_break(u, rec) for u in (getattr(used, "entries", None) or ()))


def _decision(policy: BotPolicy, action: str, reason: str, *, side: str | None, as_of_ms: int,
              analyses: dict[str, Any], rec: dict[str, Any] | None = None, extra: dict[str, Any] | None = None,
              text_tr: str = "") -> dict[str, Any]:
    plan = None
    if rec is not None and action in (ACT_ENTER, ACT_WAIT_TRIGGER):
        plan = {"trigger": rec.get("trigger"), "invalidation": rec.get("invalidation"), "stop": rec.get("stop"),
                "targets": list(rec.get("targets") or []), "expires_at_ms": rec.get("expires_at_ms"),
                "timeframe": rec.get("timeframe")}
    return {"bot": policy.bot, "policy_version": policy.version, "action": action, "reason_code": reason, "side": side,
            "decision_tf": policy.decision_tf, "as_of_ms": int(as_of_ms),
            "pattern_ids": [rec["pattern_id"]] if rec else [], "primary": summarize(rec), "plan": plan,
            "analysis_ids": {tf: (a or {}).get("analysis_id") for tf, a in analyses.items()},
            "detail": dict(extra or {}), "text_tr": text_tr}


def _tr_rec(rec: dict[str, Any]) -> str:
    st = K.STATUS_TR.get(rec.get("status"), rec.get("status"))
    return "%s %s (%s, %s)" % (rec.get("timeframe"), rec.get("name"), rec.get("side") or "-", st)


# ---------------------------------------------------------------------------- giriş kararı
def entry_decision(policy: BotPolicy, *, intended_side: str, analyses: dict[str, dict[str, Any] | None], as_of_ms: int,
                   price: float | None = None, used_patterns: Iterable[str] = (), entry_type: str | None = None,
                   accept: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    """Politika matrisi B bölümü (öncelik 1..6). `analyses`: dilim → analiz sonucu (None = hesaplanamadı).
    `accept(rec)`: bota özgü ek bağlam süzgeci (Box: kutu kenarı). `price`: doğrulanmış güncel fiyat (kovalama)."""
    side = str(intended_side or "").upper()
    if side not in (K.LONG, K.SHORT):
        return _decision(policy, ACT_NO_EFFECT, "NO_INTENDED_SIDE", side=None, as_of_ms=as_of_ms, analyses=analyses,
                         text_tr="Botun kuralı bu turda yön üretmedi; yapı politikası uygulanmadı.")
    used = used_patterns if isinstance(used_patterns, set) else set(used_patterns or ())
    dtf = policy.decision_tf
    main_an = analyses.get(dtf)
    # 1) analiz yok
    if not _usable(main_an):
        if entry_type and entry_type in policy.confirmed_required_for:
            # Teyitli yapı ŞART olan plan (ana bot geri çekilmesi) analiz yokken GİREMEZ (fail-closed; bulgu #14):
            # önce "etkisiz" dönüp şartı atlıyordu.
            return _decision(policy, ACT_WAIT, "PULLBACK_NEEDS_CONFIRMED_STRUCTURE:ANALYSIS_UNAVAILABLE", side=side,
                             as_of_ms=as_of_ms, analyses=analyses, extra={"missing_tf": dtf},
                             text_tr="Girmedi: geri çekilme planı teyitli yapı istiyor; %s analizi yok." % dtf)
        return _decision(policy, ACT_NO_EFFECT, "STRUCTURE_ANALYSIS_UNAVAILABLE:%s" % dtf, side=side, as_of_ms=as_of_ms,
                         analyses=analyses, extra={"missing_tf": dtf},
                         text_tr="%s analizi yok (bar yetersiz/veri kimliği); bot kendi kuralıyla karar verdi." % dtf)
    tfs = (dtf,) + tuple(t for t in policy.context_tfs if t != dtf)
    # 2) taze teyitli KARŞI yapı (karar + bağlam dilimleri)
    for tf in tfs:
        an = analyses.get(tf)
        if not _usable(an):
            continue
        for rec in an["records"]:
            if rec.get("status") == K.ST_CONFIRMED and _match(rec, _other(side), policy.opposing):
                return _decision(policy, ACT_WAIT, "OPPOSING_CONFIRMED", side=side, as_of_ms=as_of_ms, analyses=analyses,
                                 rec=rec, text_tr="Girmedi: %s teyitli KARŞI yapı var; yapı bozulana ya da bayatlayana kadar bekler." % _tr_rec(rec))
    recs = [r for r in main_an["records"] if (accept is None or accept(r))]
    # 3) son 2 barda BOZULMUŞ uyumlu yapı
    for rec in recs:
        # "bozuldu" = ilk geçersizlik kapanışı (terminal BROKEN ya da süre dolduktan SONRA; `broken_after_expiry`)
        if rec.get("broken_at_ms") is not None and _match(rec, side, policy.compatible) and \
                _recent(rec.get("broken_at_ms"), as_of_ms, dtf, K.DEFAULT_CONFIG.fresh_bars):
            return _decision(policy, ACT_WAIT, "COMPATIBLE_BROKEN", side=side, as_of_ms=as_of_ms, analyses=analyses, rec=rec,
                             text_tr="Girmedi / plan iptal: uyumlu yapı %s bozuldu (geçersizlik kapanışı)." % _tr_rec(rec))
    # 4) taze teyitli uyumlu yapı → giriş adayı (kovalama sınırı)
    for rec in recs:
        if rec.get("status") == K.ST_CONFIRMED and _match(rec, side, policy.compatible):
            if already_used(rec, used):
                continue                                   # aynı yapı / aynı kırılım ikinci işlem açmaz
            trig = (rec.get("trigger") or {}).get("level")
            atr = rec.get("atr")
            if price is not None and trig is not None and atr:
                dist = abs(float(price) - float(trig))
                if dist > policy.chase_atr * float(atr):
                    return _decision(policy, ACT_CANCEL, "CHASE_LIMIT", side=side, as_of_ms=as_of_ms, analyses=analyses,
                                     rec=rec, extra={"distance_atr": round(dist / float(atr), 4), "chase_atr": policy.chase_atr,
                                                     "price": float(price), "trigger": float(trig)},
                                     text_tr="İptal: teyitten sonra fiyat tetikten %.2f ATR uzaklaştı (sınır %.2f)." % (dist / float(atr), policy.chase_atr))
            return _decision(policy, ACT_ENTER, "COMPATIBLE_CONFIRMED", side=side, as_of_ms=as_of_ms, analyses=analyses,
                             rec=rec, text_tr="Girdi: %s teyitli; giriş teyit kapanışından sonraki ilk doğrulanmış fiyattan." % _tr_rec(rec))
    # 5) FORMING uyumlu yapı → tetiği bekle
    for rec in recs:
        if rec.get("status") == K.ST_FORMING and _match(rec, side, policy.compatible) and \
                (rec.get("trigger") or {}).get("level") is not None and rec["pattern_id"] not in used:
            lvl = rec["trigger"]["level"]
            return _decision(policy, ACT_WAIT_TRIGGER, "WAIT_TRIGGER", side=side, as_of_ms=as_of_ms, analyses=analyses, rec=rec,
                             text_tr="Bekliyor: %s oluşuyor; %s kapanışı %s %.6g ötesinde olursa giriş." % (
                                 _tr_rec(rec), dtf, "yukarıda" if side == K.LONG else "aşağıda", float(lvl)))
    # 6) hiçbiri
    if entry_type and entry_type in policy.confirmed_required_for:
        return _decision(policy, ACT_WAIT, "PULLBACK_NEEDS_CONFIRMED_STRUCTURE", side=side, as_of_ms=as_of_ms,
                         analyses=analyses, text_tr="Girmedi: geri çekilme planı uyumlu teyitli yapı (alıcı/satıcı mumu) istiyor; yok.")
    return _decision(policy, ACT_NO_EFFECT, "NO_STRUCTURE", side=side, as_of_ms=as_of_ms, analyses=analyses,
                     text_tr="Uyumlu/karşı yapı yok; bot kendi kuralıyla karar verdi.")


# ---------------------------------------------------------------------------- açık pozisyon yönetimi
def hold_decision(policy: BotPolicy, *, position_side: str, opened_at_ms: int | None, entry_pattern_id: str | None,
                  analyses: dict[str, dict[str, Any] | None], as_of_ms: int,
                  accept: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    """Açık pozisyon: girişten SONRA teyit edilen karşı yapı → politika eylemi (EXIT/TIGHTEN_STOP) ya da ETKİSİZ.
    Girişe dayanak yapı girişten sonra BOZULURSA (`entry_failure_exit`) → EXIT."""
    side = str(position_side or "").upper()
    an = analyses.get(policy.decision_tf)
    if not _usable(an) or side not in (K.LONG, K.SHORT):
        return _decision(policy, ACT_NO_EFFECT, "STRUCTURE_ANALYSIS_UNAVAILABLE:%s" % policy.decision_tf, side=side or None,
                         as_of_ms=as_of_ms, analyses=analyses, text_tr="Açık pozisyon: analiz yok, yönetim değişmedi.")
    if policy.hold_action == ACT_NO_EFFECT:
        return _decision(policy, ACT_NO_EFFECT, policy.hold_reason or "HOLD_NO_EFFECT", side=side, as_of_ms=as_of_ms,
                         analyses=analyses, text_tr="Açık pozisyon: bu bot yapıyla çıkmaz (%s)." % (policy.hold_reason or "-"))
    opened = int(opened_at_ms) if opened_at_ms is not None else None
    if policy.entry_failure_exit and entry_pattern_id:
        for rec in an["records"]:
            if rec.get("pattern_id") == entry_pattern_id and rec.get("broken_at_ms") is not None and \
                    opened is not None and int(rec.get("broken_at_ms") or 0) > opened:
                return _decision(policy, ACT_EXIT, "ENTRY_STRUCTURE_FAILED", side=side, as_of_ms=as_of_ms, analyses=analyses,
                                 rec=rec, text_tr="Çıktı: girişe dayanak yapı %s girişten sonra BOZULDU." % _tr_rec(rec))
    names = policy.hold_names if policy.hold_names is not None else policy.opposing
    for rec in an["records"]:
        if rec.get("status") != K.ST_CONFIRMED or not _match(rec, _other(side), names):
            continue
        if accept is not None and not accept(rec):
            continue
        if opened is None or int(rec.get("confirmed_at_ms") or 0) <= opened:
            continue                                  # girişten ÖNCE teyitli yapı pozisyonu yönetemez
        return _decision(policy, policy.hold_action, policy.hold_reason or "OPPOSING_AFTER_ENTRY", side=side,
                         as_of_ms=as_of_ms, analyses=analyses, rec=rec,
                         text_tr="%s: girişten sonra teyitli karşı yapı %s." % (
                             "Çıktı" if policy.hold_action == ACT_EXIT else "Stop sıkılaştırıldı", _tr_rec(rec)))
    return _decision(policy, ACT_NO_EFFECT, "NO_OPPOSING_AFTER_ENTRY", side=side, as_of_ms=as_of_ms, analyses=analyses,
                     text_tr="Açık pozisyon: girişten sonra teyitli karşı yapı yok.")


def tightened_stop(side: str, current_stop: float | None, rec: dict[str, Any], price: float) -> float | None:
    """TIGHTEN_STOP: teyit barının ucu (LONG: düşük, SHORT: yüksek). Yalnız SIKILAŞTIRIR ve fiyatın doğru tarafında
    kalır; aksi hâlde None (stop değişmez)."""
    cb = rec.get("confirm_bar") or {}
    lvl = cb.get("low") if side == K.LONG else cb.get("high")
    if lvl is None or price is None:
        return None
    lvl = float(lvl)
    if side == K.LONG:
        if lvl >= float(price) or (current_stop is not None and lvl <= float(current_stop)):
            return None
    else:
        if lvl <= float(price) or (current_stop is not None and lvl >= float(current_stop)):
            return None
    return lvl


def policy_table() -> dict[str, Any]:
    """Makine okunur matris (panel/rapor)."""
    out = {}
    for b, p in POLICIES.items():
        out[b] = {"decision_tf": p.decision_tf, "context_tfs": list(p.context_tfs),
                  "compatible": sorted(p.compatible) if p.compatible is not None else "ANY_SAME_SIDE",
                  "opposing": sorted(p.opposing) if p.opposing is not None else "ANY_OPPOSITE_SIDE",
                  "hold_action": p.hold_action, "hold_names": sorted(p.hold_names) if p.hold_names else None,
                  "entry_failure_exit": p.entry_failure_exit, "confirmed_required_for": list(p.confirmed_required_for),
                  "chase_atr": p.chase_atr, "hold_reason": p.hold_reason, "version": p.version}
    return out


__all__ = ["ACTIONS", "ACTION_TR", "ACT_CANCEL", "ACT_ENTER", "ACT_EXIT", "ACT_NO_EFFECT", "ACT_TIGHTEN", "ACT_WAIT",
           "ACT_WAIT_TRIGGER", "BOTS", "BOT_BOX", "BOT_M2", "BOT_MAIN", "BOT_PATTERN", "BOT_T2", "BotPolicy", "POLICIES",
           "entry_decision", "hold_decision", "policy_table", "summarize", "tightened_stop"]

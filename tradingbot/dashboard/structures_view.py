# -*- coding: utf-8 -*-
"""ORTAK YAPI GÖRÜNÜMÜ (structures_v1) — SALT OKUMA, SALT SUNUM.

Panel yapıyı YENİDEN HESAPLAMAZ: motorun (ana bot, T2, M2, Box, Formasyon) `StructureStore`a yazdığı karar satırını
ve o satırın `primary` kaydını AYNEN okur ve çizer. Böylece "motor neyi gördü" ile "panel neyi çiziyor" aynı kayıttır.

Kimlik: defter (`book_id`) + piyasa (USDM_PERP | SPOT) + sembol (+ işlem kimliği). İşlem kimliği verildiğinde o işlemin
GİRİŞ anındaki karar satırı (değişmez `decisions.jsonl` satırı) kullanılır — geçmiş, sonradan gelen analizle
EZİLMEZ. Kayıt yoksa bu açıkça yazılır; başka defterin/piyasanın kaydına düşülmez.
"""
from __future__ import annotations

from typing import Any

from ..structures import catalog as K
from ..structures.policy import ACTION_TR
from ..structures.store import StructureStore, iso_ms
from .templates import badge, esc, fmt, fmt_utc, table

MARKET_ID = {"futures": "USDM_PERP", "spot": "SPOT"}
#: Karar → kullanıcıya tek cümlelik başlık ("neden girdi / girmedi / çıktı").
WHY_TITLE = {"ENTER": "Neden girdi", "EXIT": "Neden çıktı", "TIGHTEN_STOP": "Neden stop sıkılaştı",
             "WAIT_TRIGGER": "Neden bekliyor", "NO_EFFECT": "Yapının etkisi"}
BOT_ORDER = ("main", "t2_trend_regime", "m2_tsmom28", "b1_box_fade", "pattern_trader")


def market_id(market: str) -> str:
    return MARKET_ID.get(str(market or "futures"), "USDM_PERP")


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _store(state) -> StructureStore:
    return StructureStore(state.state_dir)


BOT_LABEL = {"main": "Ana bot", "t2_trend_regime": "T2", "m2_tsmom28": "M2", "b1_box_fade": "Box", "pattern_trader": "Formasyon"}


def _bot_of(book: dict[str, Any]) -> str:
    """Panel defter kaydı → politika botu (ana bot | kural adı | formasyon). Formasyon defterinin özet adı
    (`pattern_v1` vb.) bot kimliği DEĞİLDİR; defter kimliği esastır."""
    bid = str(book.get("book_id") or "")
    if bid in ("main", "pattern_trader"):
        return bid
    return str(book.get("name") or bid)


def _trade_decisions(store: StructureStore, *, book_id: str, mid: str, symbol: str, trade_id: str) -> tuple[dict | None, dict | None]:
    """(giriş satırı, çıkış satırı) — işlem kimliğine bağlı DEĞİŞMEZ satırlar."""
    rows = [r for r in store.decisions(symbol=symbol, book_id=book_id, limit=20_000)
            if str(r.get("trade_id") or "") == str(trade_id) and str(r.get("market") or mid) == mid]
    entry = next((r for r in rows if r.get("action") == "ENTER" or r.get("applied") == "OPENED"), None)
    exit_ = next((r for r in reversed(rows) if r.get("action") == "EXIT" or r.get("applied") == "CLOSED"), None)
    return entry, exit_


def resolve(state, *, book_id: str, market: str, symbol: str, trade_id: str | None = None) -> dict[str, Any]:
    """Seçili kimliğin yapı bağlamı. Hesap YOK; yalnız kayıt okuma."""
    mid = market_id(market)
    store = _store(state)
    latest = store.latest_decisions()
    ctx: dict[str, Any] = {"book_id": book_id, "market": market, "market_id": mid, "symbol": symbol, "trade_id": trade_id,
                           "origin": "none", "decision": None, "exit_decision": None, "trade": None, "position": None}
    if trade_id:
        pos = next((p for p in state.book_positions(book_id, market=market) if str(p.get("id") or "") == str(trade_id)), None)
        tr = None if pos else state.book_trade(book_id, trade_id, market=market)
        ctx["position"], ctx["trade"] = pos, tr
        entry, exit_ = _trade_decisions(store, book_id=book_id, mid=mid, symbol=symbol, trade_id=trade_id)
        rec = pos or tr or {}
        if entry is None:
            # Satır yoksa işlem kaydının KENDİ yapı referansı (girişte yazılmış `features.structure`) kullanılır.
            fs = (rec.get("features") or {}).get("structure") if isinstance(rec.get("features"), dict) else None
            if isinstance(fs, dict) and fs.get("pattern_id"):
                entry = {"action": fs.get("action") or "ENTER", "reason_code": fs.get("reason_code"), "text_tr": fs.get("text_tr"),
                         "bot": fs.get("bot"), "side": fs.get("side") or rec.get("side"), "pattern_ids": [fs.get("pattern_id")],
                         "primary": {k: fs.get(k) for k in ("pattern_id", "name", "family", "timeframe", "status", "trigger",
                                                            "invalidation", "stop", "targets", "confirmed_at_ms", "analysis_id")},
                         "policy_version": fs.get("policy_version"), "trade_id": trade_id, "source": "trade_record"}
        ctx["decision"], ctx["exit_decision"] = entry, exit_
        ctx["origin"] = "trade" if entry else "trade_without_structure"
    else:
        row = latest.get("%s|%s|%s" % (book_id, mid, symbol))
        if row:
            ctx["decision"], ctx["origin"] = row, "latest"
    ctx["cross"] = cross_bot(state, latest, mid=mid, symbol=symbol, primary=(ctx["decision"] or {}).get("primary") or {})
    ctx["exposure"] = exposure(state, symbol=symbol, market=market)
    return ctx


def cross_bot(state, latest: dict[str, Any], *, mid: str, symbol: str, primary: dict[str, Any]) -> list[dict[str, Any]]:
    """Aynı sembolde beş botun SON yapı kararı ve seçili kayıtla aynı yapıya (pattern_id) dayanıp dayanmadığı."""
    pid = primary.get("pattern_id")
    books = {(_bot_of(b)): b for b in state.books()}
    out = []
    for bot in BOT_ORDER:
        b = books.get(bot)
        if b is None:
            out.append({"bot": bot, "label": BOT_LABEL.get(bot, bot), "book_id": None, "row": None, "same": False,
                        "note": "defter bu kurulumda yok"})
            continue
        row = latest.get("%s|%s|%s" % (b["book_id"], mid, symbol))
        same = bool(pid and row and pid in (row.get("pattern_ids") or []))
        out.append({"bot": bot, "label": b.get("label") or bot, "book_id": b["book_id"], "row": row, "same": same,
                    "note": None if row else "bu coin için yapı kararı kaydı yok"})
    return out


def exposure(state, *, symbol: str, market: str) -> list[dict[str, Any]]:
    """Aynı coinde AYRI defterlerdeki açık pozisyonlar — toplanmaz, havuzlanmaz; yalnız görünür kılınır."""
    out = []
    for b in state.books():
        if not state.book_supports_market(b["book_id"], market):
            continue
        for p in state.book_positions(b["book_id"], market=market):
            if str(p.get("symbol") or "") == symbol:
                out.append({"book_id": b["book_id"], "label": b.get("label") or b["book_id"], "side": str(p.get("side") or "").upper(),
                            "qty": _f(p.get("qty")), "entry": _f(p.get("entry_avg") or p.get("entry")), "id": p.get("id")})
    return out


# --------------------------------------------------------------------------- çizim elemanları (grafik katmanı)
def elements(ctx: dict[str, Any], *, chart_tf: str | None = None) -> list[dict[str, Any]]:
    """Seçili kaydın çizim elemanları — YALNIZ bu kayıt (sade varsayılan). Kaynak: motorun karar satırı."""
    dec = ctx.get("decision") or {}
    pr = dec.get("primary") or {}
    if not pr or not pr.get("pattern_id"):
        return []
    tf = pr.get("timeframe")
    nm = "%s %s" % (K.name_tr(pr.get("name")), "(%s)" % tf if tf else "")
    imp = "USED_IN_DECISION" if dec.get("action") in ("ENTER", "WAIT", "WAIT_TRIGGER", "CANCEL", "EXIT", "TIGHTEN_STOP") else "OBSERVATION_ONLY"
    src = {"module": "structures.analysis", "function": "analyze",
           "params": {"pattern_id": pr.get("pattern_id"), "analysis_id": pr.get("analysis_id"),
                      "policy_version": dec.get("policy_version"), "book": ctx.get("book_id"), "trade_id": ctx.get("trade_id")}}
    why = str(dec.get("text_tr") or "")
    base = {"layer": "structure", "decision_impact": imp, "timeframe": tf, "status": pr.get("status"), "source": src,
            "rationale_tr": why, "record_tf": tf, "tf_mismatch": bool(chart_tf and tf and chart_tf != tf)}
    out: list[dict[str, Any]] = []
    t_det = pr.get("detected_at_ms")
    for g in pr.get("geometry") or []:
        if not isinstance(g, dict) or g.get("t0") is None or g.get("y0") is None:
            continue
        out.append(dict(base, kind="structure_geometry", t0=g.get("t0"), y0=g.get("y0"), t1=g.get("t1"), y1=g.get("y1"),
                        label_tr="%s — %s" % (nm, str(g.get("kind") or "geometri"))))
    anc = [a for a in (pr.get("anchors") or []) if isinstance(a, dict) and a.get("ts") is not None and a.get("price") is not None]
    if anc:
        out.append(dict(base, kind="structure_anchors", label_tr="%s — dayanak noktaları" % nm,
                        anchors=[{"timestamp": a.get("ts"), "price": a.get("price"), "role": a.get("role"),
                                  "confirmed_at": a.get("confirmed_at_ms")} for a in anc]))
    lv = [("structure_trigger", (pr.get("trigger") or {}).get("level"), "TETİK"),
          ("structure_invalidation", (pr.get("invalidation") or {}).get("level"), "GEÇERSİZLİK"),
          ("structure_stop", pr.get("stop"), "YAPI STOP")]
    for i, t in enumerate(pr.get("targets") or []):
        lv.append(("structure_target", t, "YAPI HEDEF %d" % (i + 1)))
    # SADE: işlemin GERÇEK stop/hedefi «İşlemler» katmanında zaten çizilir; yapı seviyesi onlarla AYNIYSA ikinci kez
    # çizilmez (farklıysa — ör. sonradan sıkılaşan stop — ikisi de görünür).
    rec = ctx.get("position") or ctx.get("trade") or {}
    actual = [v for v in [_f(rec.get("stop"))] + [_f(t) for t in (rec.get("targets") or [])] if v is not None]

    def _same(v: float) -> bool:
        return any(abs(v - a) <= 1e-9 * max(1.0, abs(a)) for a in actual)
    for kind, price, lab in lv:
        if _f(price) is None:
            continue
        if kind in ("structure_stop", "structure_target") and _same(_f(price)):
            continue
        rule = (pr.get("trigger") or {}).get("rule") if kind == "structure_trigger" else (
            (pr.get("invalidation") or {}).get("rule") if kind == "structure_invalidation" else None)
        out.append(dict(base, kind=kind, price=_f(price), t0=t_det, label_tr="%s %s — %s" % (lab, fmt(price, 6), nm),
                        rule=rule))
    cb = pr.get("confirm_bar") or {}
    if cb.get("ts") is not None and _f(cb.get("close")) is not None:
        out.append(dict(base, kind="structure_confirm", t0=cb.get("ts"), price=_f(cb.get("close")),
                        confirmed_at=pr.get("confirmed_at_ms"), label_tr="%s — teyit kapanışı" % nm))
    return out


# --------------------------------------------------------------------------- plan kutusu satırları
def _status_tr(s: Any) -> str:
    return K.STATUS_TR.get(str(s or ""), str(s or "—"))


K_LEGACY = "pre_v2_unverified"
_WHY_PREFIXES = ("Girdi: ", "Girmedi / plan iptal: ", "Girmedi: ", "Bekliyor: ", "İptal: ", "Çıkış: ", "Çıktı: ")


def _why_text(dec: dict[str, Any]) -> str:
    """Tek cümlelik gerekçe; başlık zaten "Neden girdi/girmedi" dediği için metnin aynı öneki tekrarlanmaz."""
    t = str(dec.get("text_tr") or dec.get("reason_code") or "—")
    for p in _WHY_PREFIXES:
        if t.startswith(p):
            return t[len(p):]
    return t


def _funding_text(rec: dict[str, Any]) -> str:
    """Funding AYRI satır: mutabık tutar ile uzlaşmamış dönem karıştırılmaz (bekleyen dönem tahmin edilmez)."""
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    paid = _f(rec.get("funding") if rec.get("funding") is not None else rec.get("funding_net"))
    if paid is None and (rec.get("funding_paid") is not None or rec.get("funding_received") is not None):
        paid = (_f(rec.get("funding_received")) or 0.0) - (_f(rec.get("funding_paid")) or 0.0)
    cov = feats.get("funding_coverage") or {}
    pend = feats.get("funding_pending") or {}
    amount = ("%s USDT" % fmt(paid, 4)) if paid is not None else "bilinmiyor"
    if cov.get("contract") == K_LEGACY:
        return "funding %s (eski kayıt: sözleşme öncesi, doğrulanmadı)" % amount
    if pend or cov.get("complete") is False:
        n = cov.get("missing")
        return ('funding %s <span class="bad">— %s uzlaşmamış dönem; net sonuç kesin değil (%s)</span>'
                % (amount, esc(str(n)) if n is not None else "bilinmeyen sayıda",
                   esc(str((pend or {}).get("reason") or cov.get("reason") or "bekliyor"))))
    if cov.get("complete"):
        return "funding %s (mutabık)" % amount
    return "funding %s" % amount



def box_html(ctx: dict[str, Any], *, exit_tr=None) -> str:
    """Plan kutusunun yapı bölümü: bot/coin/yön/yapı/dilim/durum, tetik metni, stop/hedef/geçersizlik/süre, gerçek
    giriş-çıkış ve maliyet sonrası sonuç, bekleyen funding AYRI, TEK cümlelik neden, beş botun eşlemesi, çakışan
    pozisyonlar. Kayıt yoksa bu açıkça yazılır."""
    dec = ctx.get("decision") or {}
    pr = dec.get("primary") or {}
    sym = str(ctx.get("symbol") or "")
    out: list[str] = []
    if not dec:
        why = ("Bu işlemin girişinde yapı kararı kaydı yok (yapı katmanı o sırada kapalıydı ya da işlem bu sürümden eski)."
               if ctx.get("trade_id") else "Bu defterde bu coin için yapı kararı kaydı yok (botun kuralı yön üretmedi ya da yapı modu kapalı).")
        out.append('<div class="planrow mut">Yapı: %s</div>' % esc(why))
    else:
        act = str(dec.get("action") or "")
        side = str(dec.get("side") or pr.get("side") or "").upper()
        tf = pr.get("timeframe") or dec.get("decision_tf") or "—"
        head = ('<div class="planrow"><b>Yapı kararı</b> · %s · %s · %s · <b>%s</b> · %s · yapı %s · karar <b>%s</b>%s</div>'
                % (esc(str(dec.get("bot") or ctx.get("book_id"))), esc(sym),
                   badge(side or "—", "ok" if side == "LONG" else ("bad" if side == "SHORT" else "")),
                   esc(K.name_tr(pr.get("name")) if pr else "yapı yok"), esc(str(tf)), esc(_status_tr(pr.get("status"))),
                   esc("girdi · işlem %s" % dec.get("trade_id") if (act == "ENTER" and dec.get("trade_id")) else ACTION_TR.get(act, act)),
                   (' · <span class="mut small">%s</span>' % esc(str(dec.get("mode"))) if dec.get("mode") else "")))
        out.append(head)
        if pr:
            trig = pr.get("trigger") or {}
            ttxt = trig.get("text_tr") or ("%s %s kapanışı %s %s" % (tf, "boğa" if side == "LONG" else "ayı",
                                                                     "üstünde" if side == "LONG" else "altında", fmt(trig.get("level"), 6))
                                           if _f(trig.get("level")) is not None else "—")
            tg = [t for t in (pr.get("targets") or []) if _f(t) is not None]
            out.append('<div class="planrow">Şu kapanıştan sonra giriş: <b>%s</b> · stop <b>%s</b> · hedef <b>%s</b> · '
                       'geçersizlik <b>%s</b> · tetik son geçerlilik %s</div>'
                       % (esc(ttxt), fmt(pr.get("stop"), 6) if _f(pr.get("stop")) is not None else "—",
                          fmt(tg[0], 6) if tg else '<span class="mut">yapısal hedef yok</span>',
                          fmt((pr.get("invalidation") or {}).get("level"), 6) if _f((pr.get("invalidation") or {}).get("level")) is not None else "—",
                          esc(fmt_utc(iso_ms(pr.get("expires_at_ms")))) if pr.get("expires_at_ms") else "—"))
        title = WHY_TITLE.get(act, "Neden girmedi")
        out.append('<div class="planrow"><b>%s:</b> %s</div>' % (esc(title), esc(_why_text(dec))))
    rec = ctx.get("trade") or ctx.get("position")
    if rec:
        closed = ctx.get("trade") is not None
        ex = ctx.get("exit_decision") or {}
        exr = rec.get("exit_reason")
        line = ('Gerçek giriş <b>%s</b> (%s)' % (fmt(rec.get("entry") or rec.get("entry_avg"), 6), esc(fmt_utc(rec.get("opened_at")))))
        if closed:
            line += (' · çıkış <b>%s</b> (%s) · maliyet sonrası net <b>%s USDT</b> · %s'
                     % (fmt(rec.get("exit_price") or rec.get("exit"), 6), esc(fmt_utc(rec.get("closed_at"))),
                        fmt(rec.get("net_pnl"), 4), _funding_text(rec)))
        else:
            line += ' · açık · ' + _funding_text(rec)
        out.append('<div class="planrow">%s</div>' % line)
        if closed:
            etxt = (exit_tr(exr) if exit_tr else str(exr or "—"))
            if ex.get("text_tr"):
                etxt += " — " + str(ex.get("text_tr"))
            out.append('<div class="planrow"><b>Neden çıktı:</b> %s</div>' % esc(etxt))
    rows = []
    for c in ctx.get("cross") or []:
        r = c.get("row") or {}
        rows.append([esc(str(c.get("label"))), esc(ACTION_TR.get(str(r.get("action") or ""), str(r.get("action") or "—"))) if r else '<span class="mut">—</span>',
                     esc(str(r.get("text_tr") or c.get("note") or "")[:160]),
                     ("✔ aynı kayıt" if c.get("same") else ('<span class="mut">farklı / yok</span>' if r else "")),
                     esc(str((r.get("primary") or {}).get("timeframe") or r.get("decision_tf") or ""))])
    if rows:
        out.append('<details class="tdet"><summary>Aynı yapı beş botta nasıl eşlendi</summary>%s</details>'
                   % table(["Bot", "Karar", "Gerekçe", "Seçili kayıt", "Dilim"], rows, empty="kayıt yok"))
    ex = ctx.get("exposure") or []
    if len(ex) >= 2 or (ex and all(e.get("book_id") != ctx.get("book_id") for e in ex)):
        out.append('<div class="planrow warn">Aynı coinde açık pozisyonlar (her defter AYRI sermaye; toplanmaz, havuzlanmaz): %s</div>'
                   % esc(" · ".join("%s %s" % (e.get("label"), e.get("side")) for e in ex)))
    return "".join(out)


__all__ = ["BOT_ORDER", "MARKET_ID", "box_html", "cross_bot", "elements", "exposure", "market_id", "resolve"]

# -*- coding: utf-8 -*-
"""İŞLEM TERMİNALİ GÖRÜNÜMÜ (2026-09-16) — sade, grafik ve işlem odaklı panel. SALT SUNUM.

Buradaki hiçbir fonksiyon karar, fiyat, olasılık ya da muhasebe HESAPLAMAZ: her sayı yetkili kayıttan okunur
(ana bot için `views.build` görünüm modeli, kâğıt defterler için defterin KENDİ özet dosyası). Bilinmeyen değer
`Veri yok` olarak gösterilir, asla 0 sayılmaz. Bağımsız defterlerin özkaynakları TOPLANMAZ: her hesap kendi
sanal sermayesiyle ayrı okunur ("Tüm hesaplar" yalnız işlem LİSTESİ kapsamıdır).

Üç sayfa: `home` (özet + grafik + açık işlemler + son kapanışlar), `trades` (açık/kapanan, arama ve filtre),
`patterns` (mum trader: keşif → formasyon → koşullu plan → işlem zinciri).
"""
from __future__ import annotations

from typing import Any

from .templates import age_text, badge, card, chart_block, esc, fmt, fmt_utc, money_html, pnl_cell, table

#: Çıkış nedeni kodları → kullanıcı diline. Ham kod satır detayında kalır.
EXIT_TR = {"stop": "Stop", "be_stop": "Başa baş stop", "hedef1": "Hedef 1", "hedef2": "Hedef 2", "tp1": "Hedef 1", "tp2": "Hedef 2",
           "likidasyon": "Likidasyon", "liq": "Likidasyon", "manuel": "Elle kapanış", "TIME_STOP": "Zaman stopu",
           "EMA200_CROSS_DOWN": "Strateji çıkışı (EMA200 altına kapanış)", "M2_TSMOM28_CROSS_DOWN": "Strateji çıkışı (28g momentum döndü)",
           "kısmi": "Kısmi kapanış", "STRATEGY_EXIT": "Strateji çıkışı"}
PLAN_STATE_TR = {"AWAITING_TRIGGER": "Teyit/tetik bekliyor", "TRIGGERED": "Giriş şartı oluştu", "RISK_CHECK": "Risk kontrolü",
                 "OPENED": "Pozisyon açık", "MANAGED": "Pozisyon açık", "CLOSED": "Kapandı", "REJECTED": "İşlem açılamadı",
                 "BROKEN": "Yapı bozuldu", "EXPIRED": "Süresi doldu", "CANCELLED": "İptal"}
REJECT_TR = {"TOTAL_OPEN_RISK": "Toplam risk sınırı dolu", "MAX_POSITIONS": "Azami pozisyon sayısı dolu", "MAX_POSITION_PCT": "Tek coin risk tavanı",
             "KILL_SWITCH_ACTIVE": "Risk durdurucusu açık", "MIN_NOTIONAL": "Borsa asgari işlem tutarının altında",
             "ZERO_QTY": "Miktar adımı sağlanamıyor", "INSUFFICIENT_MARGIN": "Serbest teminat yetersiz", "SPREAD_WIDE": "Makas çok geniş",
             "THIN_DEPTH": "Emir defteri derinliği yetersiz", "LIQUIDITY_UNKNOWN": "Likidite ölçülemedi", "DEPTH_UNKNOWN": "Derinlik ölçülemedi",
             "RR_BELOW_MIN_AT_ENTRY": "Maliyet sonrası ödül/risk yetersiz", "DATA_STALE_15M": "15m verisi bayat", "DATA_ERROR": "Veri alınamadı",
             "COOLDOWN_AFTER_LOSS": "Zarardan sonra bekleme", "POSITION_OPEN": "Bu coinde pozisyon zaten açık"}
FINDING_STATE_TR = {"FOUND": "Bulundu", "AWAITING_CONFIRMATION": "Teyit bekliyor", "CONFIRMED": "Teyit edildi",
                    "UNCONFIRMED": "Teyitlenmedi", "BROKEN": "Bozuldu", "EXPIRED": "Süresi doldu"}
NO_DATA = '<span class="mut">Veri yok</span>'


def _f(x: Any) -> float | None:
    try:
        if x is None or isinstance(x, bool):
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def exit_tr(code: Any) -> str:
    c = str(code or "")
    return EXIT_TR.get(c, EXIT_TR.get(c.lower(), c or "—"))


def reject_tr(code: Any) -> str:
    c = str(code or "")
    for k, v in REJECT_TR.items():
        if c.startswith(k):
            return v
    return c or "—"


def usdt(v: Any, nd: int = 2) -> str:
    f = _f(v)
    return NO_DATA if f is None else (fmt(f, nd) + " USDT")


# --------------------------------------------------------------------------- hesap (defter) görünümü
def account_snapshot(state, book_id: str, *, vm: dict | None = None) -> dict[str, Any]:
    """Seçili hesabın YETKİLİ muhasebe kaydından özet. Ana bot: `views.build` görünüm modeli (paneldeki tek
    muhasebe kaynağı). Kâğıt defterler: defterin KENDİ özet dosyası (`FuturesLedgerV2.summary`). Karışık kaynak YOK.

    Bilinmeyen alanlar `None` kalır → ekranda `Veri yok`. Farklı defterlerin özkaynakları TOPLANMAZ.
    """
    b = state.book(book_id) or {"book_id": "main", "label": "Ana bot"}
    out: dict[str, Any] = {"book_id": b["book_id"], "label": b.get("label") or b["book_id"], "name": b.get("name"),
                           "equity": None, "starting_equity": None, "realized": None, "unrealized": None, "net": None,
                           "open_long": 0, "open_short": 0, "open_total": 0, "closed": 0, "wins": None, "losses": None,
                           "source": None, "generated_at": None, "market": "futures", "warnings": []}
    positions = state.book_positions(b["book_id"])
    trades = state.book_trades(b["book_id"], limit=2000)
    out["open_total"] = len(positions)
    out["open_long"] = sum(1 for p in positions if str(p.get("side", "")).upper() == "LONG")
    out["open_short"] = sum(1 for p in positions if str(p.get("side", "")).upper() == "SHORT")
    out["closed"] = len(trades)
    pnls = [_f(t.get("net_pnl")) for t in trades]
    if trades and all(v is not None for v in pnls):
        out["realized"] = round(sum(v for v in pnls if v is not None), 4)
        out["wins"] = sum(1 for v in pnls if (v or 0) > 0)
        out["losses"] = sum(1 for v in pnls if (v or 0) <= 0)
    if b["book_id"] == "main":
        out["source"] = "futures_ledger.json"
        out["equity"] = state.futures_equity()
        led = state.book_ledger("main") or {}
        out["starting_equity"] = _f(led.get("starting_equity"))
        if vm is not None:
            pv = vm.get("portfolio")
            out["unrealized"] = _f(getattr(pv, "net_unrealized_total", None))
            if out["unrealized"] is None:
                vals = [_f(getattr(p, "net_unrealized", None)) for p in (getattr(pv, "positions", None) or [])]
                out["unrealized"] = round(sum(v for v in vals if v is not None), 4) if vals and all(v is not None for v in vals) else None
    else:
        doc = state.book_summary_file(b["book_id"]) or {}
        out["source"] = "%s (defterin kendi özeti)" % (b.get("summary_file") or b["book_id"])
        out["generated_at"] = doc.get("generated_at")
        sm = doc.get("summary") or {}
        out["equity"] = _f(sm.get("equity_mtm"))
        out["starting_equity"] = _f(sm.get("starting_equity")) or _f(doc.get("starting_equity"))
        out["unrealized"] = _f(sm.get("unrealized"))
        if doc.get("data_gaps"):
            out["warnings"].append("%d sembolde doğrulanmış güncel fiyat yok — son bilinen fiyat gösteriliyor." % len(doc["data_gaps"]))
        if doc.get("mode"):
            out["mode"] = doc.get("mode")
    if out["realized"] is not None and out["unrealized"] is not None:
        out["net"] = round(out["realized"] + out["unrealized"], 4)
    elif out["realized"] is not None and not positions:
        out["net"] = out["realized"]
    out["positions"] = positions
    out["trades"] = trades
    return out


def account_bar(state, *, book_id: str, market: str, coin: str | None, fr: dict | None, token_qs: str = "") -> str:
    """Üst çubuk: hesap seçimi + piyasa + güncellik. Hesaplar GERÇEKTEN var olanlardır (dosya adı başlık değildir)."""
    books = state.books()
    opts = "".join('<option value="%s"%s>%s</option>' % (esc(b["book_id"]), " selected" if b["book_id"] == book_id else "", esc(b.get("label") or b["book_id"])) for b in books)
    mkts = "".join('<option value="%s"%s>%s</option>' % (m, " selected" if m == market else "", lab) for m, lab in (("futures", "Futures"), ("spot", "Spot")))
    f = fr or {}
    bits = []
    for key, lab in (("price_age_s", "fiyat"), ("run_age_s", "strateji turu"), ("heartbeat_age_s", "kalp atışı")):
        v = f.get(key)
        cls = "bad" if (key == "price_age_s" and _f(v) is not None and _f(v) > 300) else "mut"
        bits.append('<span class="%s">%s %s</span>' % (cls, esc(lab), esc(age_text(v)) if v is not None else "—"))
    stale = ""
    if _f(f.get("price_age_s")) is not None and _f(f.get("price_age_s")) > 300:
        stale = '<span class="pill bad">⚠ fiyat bayat</span>'
    return ('<div class="acctbar"><label class="chk">Hesap <select id="acct">%s</select></label>'
            '<label class="chk">Piyasa <select id="acctmk">%s</select></label>'
            '<span class="acctsep"></span><span class="small">%s</span>%s'
            '<span class="small mut" id="acctcoin">%s</span><span class="small mut" id="accage"></span></div>'
            '<script>(function(){var qs=%s;function go(){var b=document.getElementById("acct").value,m=document.getElementById("acctmk").value;'
            'var u=new URL(window.location.href);u.searchParams.set("book",b);u.searchParams.set("market",m);window.location.href=u.toString();}'
            'var a=document.getElementById("acct"),k=document.getElementById("acctmk");if(a)a.addEventListener("change",go);if(k)k.addEventListener("change",go);})();</script>'
            % (opts, mkts, " · ".join(bits), stale, esc(("· " + coin) if coin else ""), '"%s"' % esc(token_qs)))


def summary_cards(acc: dict[str, Any]) -> str:
    """EN FAZLA DÖRT kart. Bilinmeyen değer `Veri yok`; sermaye hareketi kâr sayılmaz (net = gerçekleşen + açık)."""
    eq = usdt(acc.get("equity"))
    start = acc.get("starting_equity")
    sub_eq = ("başlangıç %s" % usdt(start)) if start is not None else "başlangıç bilinmiyor"
    net = acc.get("net")
    net_html = money_html(net) if net is not None else NO_DATA
    r, u = acc.get("realized"), acc.get("unrealized")
    sub_net = "gerçekleşen %s · açık %s" % (usdt(r) if r is not None else "Veri yok", usdt(u) if u is not None else "Veri yok")
    open_sub = "LONG %d · SHORT %d" % (int(acc.get("open_long") or 0), int(acc.get("open_short") or 0))
    wins, losses = acc.get("wins"), acc.get("losses")
    closed_sub = ("kazanan %d · kaybeden %d" % (wins, losses)) if wins is not None else "kazanan/kaybeden bilinmiyor"
    return ('<div class="cards4">'
            + card("Özkaynak", eq, sub_eq)
            + card("Net sonuç", net_html, sub_net)
            + card("Açık işlem", str(int(acc.get("open_total") or 0)), open_sub)
            + card("Kapanan işlem", str(int(acc.get("closed") or 0)), closed_sub)
            + "</div>")


# --------------------------------------------------------------------------- açık işlemler
def _pos_price(p: dict[str, Any]) -> tuple[float | None, str]:
    last = _f(p.get("last_price")) or _f(p.get("mark")) or _f(p.get("last"))
    return last, ("defterin son doğrulanmış fiyatı" if last is not None else "fiyat yok")


def _pos_unreal(p: dict[str, Any]) -> tuple[float | None, str]:
    """Açık K/Z — BRÜT (ücret/funding hariç). Bilinmeyen fiyat → None (sıfır DEĞİL)."""
    last, _src = _pos_price(p)
    entry, qty = _f(p.get("entry_avg")) or _f(p.get("entry")), _f(p.get("qty"))
    if None in (last, entry, qty):
        return None, "brüt"
    sign = 1.0 if str(p.get("side", "LONG")).upper() == "LONG" else -1.0
    return round((last - entry) * qty * sign, 4), "brüt"


def positions_block(acc: dict[str, Any], *, book_id: str, market: str, selected: str | None, token_qs: str = "") -> str:
    """Açık işlemler — yetkili defterden. Satır tıklanınca aynı ekranda o coinin grafiği açılır."""
    pos = acc.get("positions") or []
    if not pos:
        return '<div class="empty">Bu hesapta açık işlem yok.</div>'
    rows = []
    for p in sorted(pos, key=lambda d: str(d.get("symbol") or "")):
        sym = str(p.get("symbol") or "")
        base = sym.split("/")[0]
        side = str(p.get("side", "")).upper()
        entry = _f(p.get("entry_avg")) or _f(p.get("entry"))
        last, psrc = _pos_price(p)
        stop = _f(p.get("stop"))
        un, kind = _pos_unreal(p)
        tgts = [t for t in (p.get("targets") or []) if _f(t) is not None]
        sel = " on" if selected and base == selected else ""
        det = ("miktar %s · kaldıraç %sx · açılış %s · ücret %s · funding %s · kaynak %s"
               % (fmt(p.get("qty"), 6), esc(str(p.get("leverage") or 1)), fmt_utc(p.get("opened_at")),
                  usdt(p.get("fees_paid") or p.get("fees")), usdt(p.get("funding_net") or p.get("funding")), esc(psrc)))
        rows.append(
            '<div class="trow%s" data-base="%s" data-book="%s" data-market="%s" tabindex="0" role="button" aria-label="%s grafiğini aç">'
            '<div class="tmain"><span class="sym">%s</span> %s<span class="num">%s</span></div>'
            '<div class="tsub"><span>giriş <b>%s</b></span><span>güncel <b>%s</b></span><span>stop <b>%s</b></span>'
            '<span>hedef <b>%s</b></span><span class="pnl">açık K/Z %s <i>(%s)</i></span></div>'
            '<details class="tdet"><summary>ayrıntı</summary><div class="small mut">%s</div></details></div>'
            % (sel, esc(base), esc(book_id), esc(market), esc(sym), esc(sym),
               badge("LONG" if side == "LONG" else "SHORT", "ok" if side == "LONG" else "bad"),
               "", fmt(entry, 6) if entry is not None else "—", fmt(last, 6) if last is not None else "—",
               fmt(stop, 6) if stop is not None else '<span class="mut">yok</span>',
               (fmt(tgts[0], 6) if tgts else '<span class="mut">Sabit hedef yok — strateji çıkışı</span>'),
               (money_html(un) if un is not None else NO_DATA), esc(kind), det))
    return '<div class="tlist">' + "".join(rows) + "</div>"


#: Satir tiklamasi DELEGE edilir: canli yenileme listeyi yeniden cizdiginde de calisir (innerHTML ile gelen
#: <script> CALISMAZ). Secili satir `__chartBase`e gore yeniden isaretlenir — kullanicinin secimi KAYBOLMAZ.
ROW_DELEGATE_JS = """<script>(function(){
  if(window.__rowDelegate)return;window.__rowDelegate=1;
  function go(el){var b=el.dataset.base;if(!b)return;
    document.querySelectorAll('.trow').forEach(function(x){x.classList.remove('on');});el.classList.add('on');
    if(window.__chartSelect){window.__chartSelect(b,el.dataset.market,el.dataset.book);}
    else{var u=new URL(window.location.href);u.searchParams.set('coin',b);window.location.href=u.toString();}
    var c=document.getElementById('acctcoin');if(c)c.textContent='\u00b7 '+b;
    var pb=document.getElementById('planhost');                       // plan/islem ozeti de SECILEN coine gecer
    if(pb){var q='/api/planbox/'+encodeURIComponent(b)+'?book='+encodeURIComponent(el.dataset.book||'main')
             +'&market='+encodeURIComponent(el.dataset.market||'futures')+(window.__tokenQs?'&'+window.__tokenQs.slice(1):'');
      fetch(q,{headers:window.__authHeaders||{}}).then(function(r){return r.json();}).then(function(d){
        if((window.__chartBase||'').toUpperCase()===String(d.base).toUpperCase())pb.innerHTML=d.html;}).catch(function(){});}}
  document.addEventListener('click',function(e){var el=e.target.closest&&e.target.closest('.trow');
    if(el&&el.dataset.base&&!(e.target.closest('details')))go(el);});
  document.addEventListener('keydown',function(e){if(e.key!=='Enter'&&e.key!==' ')return;
    var el=e.target.closest&&e.target.closest('.trow');if(el&&el.dataset.base){e.preventDefault();go(el);}});
  window.__markSelected=function(){var b=(window.__chartBase||'').toUpperCase();
    document.querySelectorAll('.trow').forEach(function(x){x.classList.toggle('on',(x.dataset.base||'')===b);});};
})();</script>"""


def live_refresh_js(book_id: str, token_qs: str = "", every_ms: int = 20000) -> str:
    """Sayfa YENILENMEDEN kart/pozisyon/kapanis guncellemesi. Grafik SIFIRLANMAZ, secim KAYBOLMAZ.

    SSE `state` olayina baglanir (onceki `__onState` zinciri korunur: grafik kendi yenilemesini yapar) ve ayrica
    periyodik yoklar. Istek basarisiz olursa SON DOGRULANMIS icerik ekranda KALIR ve yasi gorunur olur — bos
    pozisyon listesi gibi GORUNMEZ.
    """
    qs = ("?" + token_qs.lstrip("?")) if token_qs else ""
    return ("""<script>(function(){
  var book=%s, url='/api/book/'+encodeURIComponent(book)+%s, busy=false, failed=0;
  function put(id,html){var el=document.getElementById(id);if(el&&html!=null&&el.innerHTML!==html)el.innerHTML=html;}
  function stamp(ok,at){var el=document.getElementById('accage');if(!el)return;
    el.textContent=ok?('veri '+(at?String(at).replace('T',' ').slice(0,19):'güncel')):('\u26a0 bağlantı yok \u00b7 son doğrulanmış veri gösteriliyor');
    el.className=ok?'small mut':'small bad';}
  function refresh(){if(busy)return;busy=true;
    fetch(url,{headers:window.__authHeaders||{}}).then(function(r){if(!r.ok)throw new Error(r.status);return r.json();})
    .then(function(d){busy=false;failed=0;
      put('cardshost',d.cards_html);put('poshost',d.positions_html);put('closedhost',d.closed_html);
      if(window.__markSelected)window.__markSelected();stamp(true,d.generated_at);})
    .catch(function(){busy=false;failed++;stamp(false);});}
  var prev=window.__onState;
  window.__onState=function(s){try{if(prev)prev(s);}catch(e){}
    if(s&&s.changed&&(s.changed.indexOf('futures_ledger')>=0||s.changed.indexOf('strategy_paper')>=0
      ||s.changed.indexOf('pattern_trader')>=0||s.changed.indexOf('portfolio')>=0||s.changed.indexOf('spot_ledger')>=0))refresh();};
  setInterval(refresh,%d);
})();</script>""" % ('"%s"' % book_id, ("'%s'" % qs) if qs else "''", int(every_ms)))


def closed_block(acc: dict[str, Any], *, limit: int = 8) -> str:
    tr = list(acc.get("trades") or [])[-int(limit):][::-1]
    if not tr:
        return '<div class="empty">Bu hesapta henüz kapanan işlem yok.</div>'
    rows = []
    for t in tr:
        sym = str(t.get("symbol") or "")
        rows.append([('<a href="/coin/%s">%s</a>' % (esc(sym.split("/")[0]), esc(sym))),
                     badge(str(t.get("side", "")).upper() or "—", "ok" if str(t.get("side", "")).upper() == "LONG" else "bad"),
                     fmt(t.get("entry"), 6), fmt(t.get("exit_price") or t.get("exit"), 6),
                     pnl_cell(t.get("net_pnl")), esc(exit_tr(t.get("exit_reason"))), fmt_utc(t.get("closed_at"))])
    # Kenar sutununda DAR gorunum: coin / yon / net / neden. Giris-cikis fiyati ve zamanlar `/trades` sayfasindadir.
    return table(["Coin", "Yön", "Net (USDT)", "Neden"], [[r[0], r[1], r[4], r[5]] for r in rows], num_cols={2},
                 empty="kapanmış işlem yok") + '<div class="small mut"><a href="/trades?tab=closed">tüm kapanışlar →</a></div>' 


# --------------------------------------------------------------------------- plan / işlem kutusu (grafik altı)
def plan_box(state, *, book_id: str, base: str, market: str) -> str:
    """Grafiğin altındaki kısa özet: açık işlem → yön/giriş/stop/çıkış kuralı; bekleyen GERÇEK plan → koşullu
    LONG/SHORT metni; ikisi de yoksa nedeni KAYITTAN okunur (tahmin edilmez)."""
    sym = "%s/USDT" % base.upper()
    pos = state.book_position(book_id, sym)
    out = ['<div class="planbox">']
    if pos:
        tg = [t for t in (pos.get("targets") or []) if _f(t) is not None]
        rule = "Sabit hedef yok — strateji çıkışı / stop" if not tg else ("hedef %s" % fmt(tg[0], 6))
        out.append('<div class="planrow"><b>Açık işlem</b> %s · giriş <b>%s</b> · stop <b>%s</b> · %s</div>'
                   % (badge(str(pos.get("side", "")).upper(), "ok" if str(pos.get("side", "")).upper() == "LONG" else "bad"),
                      fmt(pos.get("entry_avg") or pos.get("entry"), 6),
                      fmt(pos.get("stop"), 6) if _f(pos.get("stop")) is not None else "—", esc(rule)))
    plans = pattern_plans_for(state, sym)
    live = [p for p in plans if str(p.get("status")) in ("AWAITING_TRIGGER", "TRIGGERED")]
    if live:
        for p in sorted(live, key=lambda d: str(d.get("side") or "")):
            out.append('<div class="planrow"><b>%s planı</b> — %s. Stop <b>%s</b>, hedef <b>%s</b> (%s). '
                       'Teyit dilimi %s · geçerlilik %s · geçersizlik %s</div>'
                       % (esc(p.get("side") or "?"), esc((p.get("trigger") or {}).get("text_tr") or ""),
                          fmt(p.get("stop"), 6), fmt(p.get("target"), 6), esc(PLAN_STATE_TR.get(str(p.get("status")), str(p.get("status")))),
                          esc((p.get("trigger") or {}).get("tf") or "—"), fmt_utc(p.get("expires_at")),
                          fmt((p.get("invalidation") or {}).get("level"), 6)))
    elif not pos:
        pt = state.get("pattern_trader") or {}
        if not pt:
            out.append('<div class="planrow mut">Bu coin için bekleyen plan kaydı yok. (Mum trader defteri bu kurulumda yazmıyor.)</div>')
        else:
            recent = [p for p in plans if str(p.get("status")) not in ("AWAITING_TRIGGER", "TRIGGERED")]
            if recent:
                p = sorted(recent, key=lambda d: -(d.get("created_at_ms") or 0))[0]
                why = (p.get("reasons") or [""])[-1]
                out.append('<div class="planrow mut">Bekleyen plan yok. Son plan: %s %s — %s%s</div>'
                           % (esc(p.get("side") or ""), esc(PLAN_STATE_TR.get(str(p.get("status")), str(p.get("status")))),
                              esc(reject_tr(why)), (" (%s)" % esc(fmt_utc(p.get("created_at")))) if p.get("created_at") else ""))
            else:
                scans = (pt.get("symbol_scans") or {}).get(sym) or {}
                if scans:
                    out.append('<div class="planrow mut">Bekleyen plan yok. Son tarama: 15m %s bar · 1h %s bar · 4h %s bar · 4h trend %s · 1h bölge %s</div>'
                               % (esc(str(scans.get("n_15m", "?"))), esc(str(scans.get("n_1h", "?"))), esc(str(scans.get("n_4h", "?"))),
                                  esc(str(scans.get("trend_4h", "?"))), esc(str(scans.get("n_zones_1h", "?")))))
                else:
                    out.append('<div class="planrow mut">Bu coin mum trader tarafından henüz taranmadı (kayıt yok).</div>')
    out.append("</div>")
    return "".join(out)


def pattern_plans_for(state, symbol: str) -> list[dict]:
    pt = state.get("pattern_trader") or {}
    rows = list(pt.get("active_plans") or []) + list(pt.get("recent_plans") or [])
    seen, out = set(), []
    for p in rows:
        if not isinstance(p, dict) or p.get("symbol") != symbol or p.get("plan_id") in seen:
            continue
        seen.add(p.get("plan_id"))
        out.append(p)
    return out


# --------------------------------------------------------------------------- sayfalar
def home(state, *, book_id: str, market: str, coin: str, vm: dict, fr: dict, token_qs: str, max_bars: int,
         extra_sections: str = "") -> str:
    acc = account_snapshot(state, book_id, vm=vm)
    body = account_bar(state, book_id=book_id, market=market, coin=coin, fr=fr, token_qs=token_qs)
    for w in acc.get("warnings") or []:
        body += '<div class="card warn-box">⚠ %s</div>' % esc(w)
    for issue in (vm.get("inconsistencies") or []):
        body += '<div class="card warn-box">⚠ Veri tutarsızlığı — %s</div>' % esc(issue.get("message", ""))
    body += '<div id="cardshost">' + summary_cards(acc) + "</div>"
    books = state.books()
    chart = chart_block(coin, "1h", market, token_qs=token_qs, max_bars=max_bars, book=book_id, books=books)
    body += ('<div class="mainsplit">'
             '<section class="chartcol"><h2 class="h2row">Grafik <span class="mut small" id="charttitle">%s</span></h2>%s%s</section>'
             '<aside class="sidecol"><h2>Açık işlemler</h2><div id="poshost">%s</div>'
             '<h2>Son kapanışlar</h2><div id="closedhost">%s</div></aside>'
             "</div>" % (esc(coin), chart, '<div id="planhost">' + plan_box(state, book_id=book_id, base=coin, market=market) + "</div>",
                         positions_block(acc, book_id=book_id, market=market, selected=coin, token_qs=token_qs),
                         closed_block(acc)))
    body += extra_sections + ROW_DELEGATE_JS + live_refresh_js(book_id, token_qs)
    return body


def trades_page(state, *, book_id: str, market: str, tab: str, q: str, token_qs: str) -> str:
    acc = account_snapshot(state, book_id)
    body = account_bar(state, book_id=book_id, market=market, coin=None, fr=None, token_qs=token_qs)
    body += ('<div class="tabs"><a class="tab%s" href="/trades?book=%s&market=%s&tab=open%s">Açık (%d)</a>'
             '<a class="tab%s" href="/trades?book=%s&market=%s&tab=closed%s">Kapanan (%d)</a></div>'
             % (" on" if tab != "closed" else "", esc(book_id), esc(market), esc(token_qs.replace("?", "&") if token_qs else ""), acc["open_total"],
                " on" if tab == "closed" else "", esc(book_id), esc(market), esc(token_qs.replace("?", "&") if token_qs else ""), acc["closed"]))
    body += ('<div class="filters"><input id="tq" placeholder="coin ara (örn. BTC)" value="%s" aria-label="coin ara">'
             '<span class="mut small">Hesap: %s · Piyasa: %s · Kaynak: %s</span></div>'
             '<script>(function(){var i=document.getElementById("tq");if(!i)return;function f(){var v=(i.value||"").toUpperCase();'
             'document.querySelectorAll("[data-sym]").forEach(function(r){r.style.display=(!v||r.dataset.sym.indexOf(v)>=0)?"":"none";});}'
             'i.addEventListener("input",f);f();})();</script>'
             % (esc(q), esc(acc["label"]), esc(market), esc(str(acc.get("source") or "—"))))
    if tab == "closed":
        tr = list(acc.get("trades") or [])[::-1]
        if not tr:
            body += '<div class="empty">Bu hesapta henüz kapanan işlem yok.</div>'
        else:
            rows = []
            for t in tr:
                sym = str(t.get("symbol") or "")
                rows.append('<tr data-sym="%s"><td><a href="/coin/%s?book=%s&market=%s">%s</a></td><td>%s</td><td class="num">%s</td>'
                            '<td class="num">%s</td><td>%s</td><td>%s</td><td class="num">%s</td><td>%s</td></tr>'
                            % (esc(sym.upper()), esc(sym.split("/")[0]), esc(book_id), esc(market), esc(sym),
                               badge(str(t.get("side", "")).upper() or "—", "ok" if str(t.get("side", "")).upper() == "LONG" else "bad"),
                               fmt(t.get("entry"), 6), fmt(t.get("exit_price") or t.get("exit"), 6),
                               fmt_utc(t.get("opened_at")), fmt_utc(t.get("closed_at")), pnl_cell(t.get("net_pnl")),
                               esc(exit_tr(t.get("exit_reason")))))
            body += ('<table><thead><tr><th>Coin</th><th>Yön</th><th>Giriş</th><th>Çıkış</th><th>Açılış</th><th>Kapanış</th>'
                     '<th>Net (USDT)</th><th>Neden</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")
            body += ('<div class="small mut">Kısmi kapanışlar ana pozisyonun parçasıdır; her satır defterdeki BİR kapanmış '
                     'işlem kaydıdır (sayım defterle uzlaşır: %d).</div>' % acc["closed"])
    else:
        pos = acc.get("positions") or []
        if not pos:
            body += '<div class="empty">Bu hesapta açık işlem yok.</div>'
        else:
            body += '<div class="tlist">'
            for p in sorted(pos, key=lambda d: str(d.get("symbol") or "")):
                sym = str(p.get("symbol") or "")
                un, kind = _pos_unreal(p)
                last, psrc = _pos_price(p)
                tgts = [t for t in (p.get("targets") or []) if _f(t) is not None]
                body += ('<div class="trow" data-sym="%s"><div class="tmain"><a class="sym" href="/coin/%s?book=%s&market=%s">%s</a> %s</div>'
                         '<div class="tsub"><span>giriş <b>%s</b></span><span>güncel <b>%s</b></span><span>stop <b>%s</b></span>'
                         '<span>hedef <b>%s</b></span><span class="pnl">açık K/Z %s <i>(%s)</i></span></div>'
                         '<details class="tdet"><summary>ayrıntı</summary><div class="small mut">miktar %s · kaldıraç %sx · açılış %s · '
                         'ücret %s · funding %s · fiyat kaynağı %s · defter %s</div></details></div>'
                         % (esc(sym.upper()), esc(sym.split("/")[0]), esc(book_id), esc(market), esc(sym),
                            badge(str(p.get("side", "")).upper() or "—", "ok" if str(p.get("side", "")).upper() == "LONG" else "bad"),
                            fmt(p.get("entry_avg") or p.get("entry"), 6), fmt(last, 6) if last is not None else "—",
                            fmt(p.get("stop"), 6) if _f(p.get("stop")) is not None else "—",
                            (fmt(tgts[0], 6) if tgts else '<span class="mut">Sabit hedef yok</span>'),
                            (money_html(un) if un is not None else NO_DATA), esc(kind),
                            fmt(p.get("qty"), 6), esc(str(p.get("leverage") or 1)), fmt_utc(p.get("opened_at")),
                            usdt(p.get("fees_paid") or p.get("fees")), usdt(p.get("funding_net") or p.get("funding")),
                            esc(psrc), esc(acc["label"])))
            body += "</div>"
    return body + ROW_DELEGATE_JS


def patterns_page(state, *, token_qs: str) -> str:
    """Mum trader: keşif → formasyon → koşullu plan → işlem zinciri. Kayıt yoksa BOŞ DURUM açıkça yazılır."""
    pt = state.get("pattern_trader") or {}
    scan = state.get("pattern_scan") or {}
    rep = state.get("pattern_report") or {}
    if not pt:
        return ('<div class="empty">Mum trader defteri bu kurulumda henüz yazmadı.<br>'
                '<span class="small mut">Açıksa ilk tarama turundan sonra burada keşfedilen coinler, formasyonlar ve '
                'koşullu planlar görünür. (Kayıt dosyası: <code>state/pattern_trader.json</code>)</span></div>')
    uni = (scan.get("universe") or {}).get("counts") or {}
    cov = scan.get("coverage") or {}
    c = pt.get("counters") or {}
    body = ('<div class="cards4">'
            + card("Keşfedilen coin", str(uni.get("discovered", "—")), "işleme uygun %s · kuyruk %s" % (uni.get("eligible", "—"), (scan.get("last_cycle") or {}).get("queue_depth", "—")))
            + card("Yeni listelenen", str(uni.get("priority", "—")), "ilk 30 gün · öncelikli tarama")
            + card("Taranan / hiç taranmayan", "%s / %s" % (cov.get("scanned_at_least_once", "—"), cov.get("never_scanned", "—")),
                   "en eski tarama %s" % (age_text(cov.get("max_staleness_s")) if cov.get("max_staleness_s") is not None else "—"))
            + card("Zincir", "%s → %s → %s" % (c.get("confirmed", 0), c.get("plans", 0), c.get("opened", 0)), "teyit → plan → açılan işlem")
            + "</div>")
    body += '<div class="small mut">Son tarama: %s · evren yenileme: %s · hata: %s</div>' % (
        esc(str(scan.get("last_cycle_at") or "—")), esc(str(scan.get("last_universe_at") or "—")), esc(str(scan.get("last_error") or "yok")))
    nl = ((scan.get("universe") or {}).get("recent_new_listings") or [])
    if nl:
        body += "<h2>Yeni listelenen sözleşmeler</h2>" + table(
            ["Coin", "Futures ilk işlem", "Yaş (saat)", "Kohort"],
            [[esc(str(e.get("symbol"))), esc(str(e.get("futures_first_trade") or "—")), fmt(e.get("age_h"), 1), esc(str(e.get("cohort") or "—"))] for e in nl[:12]],
            num_cols={2}, empty="yeni listeleme yok")
        body += ('<div class="small mut">Yaş, <b>bu futures sözleşmesinin ilk işlem zamanına</b> göredir; tokenin çıkışı ya da '
                 'spot listelenmesi AYRI kavramlardır ve burada iddia edilmez.</div>')
    plans = list(pt.get("active_plans") or []) + list(pt.get("recent_plans") or [])
    seen, rows = set(), []
    for p in sorted(plans, key=lambda d: -(d.get("created_at_ms") or 0)):
        if p.get("plan_id") in seen:
            continue
        seen.add(p.get("plan_id"))
        why = (p.get("reasons") or [""])[-1]
        st = str(p.get("status") or "")
        rows.append([esc(str(p.get("symbol") or "")), badge(str(p.get("side") or ""), "ok" if p.get("side") == "LONG" else "bad"),
                     esc(str(p.get("family") or "")), esc((p.get("trigger") or {}).get("tf") or "15m"),
                     badge(PLAN_STATE_TR.get(st, st), "ok" if st in ("OPENED", "MANAGED") else ("bad" if st in ("REJECTED", "BROKEN") else "info")),
                     esc(str(p.get("cohort") or "—")), fmt((p.get("trigger") or {}).get("level"), 6), fmt(p.get("stop"), 6),
                     fmt(p.get("target"), 6), fmt(p.get("rr_after_cost"), 2), esc(reject_tr(why) if st == "REJECTED" else (p.get("target_source") or "")),
                     fmt_utc(p.get("created_at"))])
    body += "<h2>Koşullu planlar</h2>" + (table(
        ["Coin", "Yön", "Aile", "Dilim", "Durum", "Kohort", "Tetik", "Stop", "Hedef", "Ödül/Risk", "Not", "Oluşturma"],
        rows[:60], num_cols={6, 7, 8, 9}, empty="plan yok") if rows else
        '<div class="empty">Henüz koşullu plan yok. Formasyon teyidi ve bağlam şartları oluşunca planlar burada listelenir.</div>')
    fs = pt.get("findings_by_status") or {}
    if fs:
        body += "<h2>Formasyon bulguları</h2><div class=\"pills\">" + "".join(
            '<span class="pill">%s: <b>%d</b></span>' % (esc(FINDING_STATE_TR.get(k, k)), int(v)) for k, v in sorted(fs.items())) + "</div>"
    ss = pt.get("symbol_scans") or {}
    if ss:
        rows2 = []
        for sym, v in sorted(ss.items(), key=lambda kv: -(kv[1].get("last_scan_ms") or 0))[:20]:
            ready = []
            for tf, key in (("15m", "n_15m"), ("1h", "n_1h"), ("4h", "n_4h")):
                n = int(v.get(key) or 0)
                ready.append('<span class="pill %s">%s %d bar</span>' % ("ok" if n >= 16 else "warn", tf, n))
            rows2.append([esc(sym), esc(str(v.get("cohort") or "—")), " ".join(ready), esc(str(v.get("trend_4h") or "—")), esc(str(v.get("n_zones_1h") or 0))])
        body += ("<h2>Veri hazırlığı (son taranan)</h2>" + table(["Coin", "Kohort", "Zaman dilimi başına kapalı bar", "4h trend", "1h bölge"], rows2, empty="tarama yok")
                 + '<div class="small mut">Bir dilimde bar sayısı yetersizse o dilim <b>hazır değildir</b> ve o bağlamı '
                   'şart koşan işlem kolu çalışmaz; eksik mum UYDURULMAZ.</div>')
    tot = (rep.get("totals") or {})
    if tot:
        o = tot.get("outcome") or {}
        body += ("<h2>Ekonomik değerlendirme</h2><div class=\"card\">"
                 + '<div class="small">%s</div>' % esc(str(rep.get("status_tr") or ""))
                 + table(["Kapanan işlem", "Ortalama R", "İsabet", "Profit factor", "Maks. düşüş (R)", "Net (USDT)", "Hüküm"],
                         [[str(tot.get("closed_trades", 0)), fmt(o.get("mean_r"), 3), (fmt((o.get("win_rate") or 0) * 100, 1) + "%") if o.get("win_rate") is not None else "—",
                           esc(str(o.get("profit_factor") or "—")), fmt(o.get("max_drawdown_r"), 2),
                           fmt((tot.get("costs") or {}).get("net_pnl_usdt"), 2), esc(str(tot.get("verdict") or "—"))]],
                         num_cols={0, 1, 2, 4, 5}, empty="")
                 + "</div>")
    return body


__all__ = ["ROW_DELEGATE_JS", "live_refresh_js", "EXIT_TR", "PLAN_STATE_TR", "REJECT_TR", "FINDING_STATE_TR", "NO_DATA", "account_snapshot", "account_bar",
           "summary_cards", "positions_block", "closed_block", "plan_box", "pattern_plans_for", "home", "trades_page",
           "patterns_page", "exit_tr", "reject_tr", "usdt"]

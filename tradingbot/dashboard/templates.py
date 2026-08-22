"""HTML şablonları (jinja2 bağımlılığı yok — saf Python). Mobil-öncelikli koyu tema, satır içi CSS/JS, CDN yok."""
from __future__ import annotations

import html
import json
from typing import Any, Iterable

NAV: list[tuple[str, str]] = [
    ("/", "Genel"), ("/scanner", "Tarayıcı"), ("/portfolio/futures", "Futures"), ("/portfolio/spot", "Spot"),
    ("/orders", "Emirler"), ("/trades", "İşlemler"), ("/risk", "Risk"), ("/learning", "Öğrenme"), ("/backtest", "Backtest"),
    ("/models", "Modeller"), ("/llm", "LLM"), ("/health", "Sağlık"),
]

CSS_EXTRA = """
.live{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;font-size:.86rem}
.warn-box{border-left:3px solid #ef5350;background:rgba(239,83,80,.08)}
.num.flat{color:#9aa4b2}
.small{font-size:.8rem}
"""

CSS = """
:root{--bg:#0e1116;--panel:#161b22;--line:#242c37;--fg:#d7dde5;--mut:#8b98a8;--acc:#4da3ff;--up:#26a69a;--dn:#ef5350;--warn:#f5c542}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);padding:6px 10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
header .brand{font-weight:700;white-space:nowrap}
nav{display:flex;gap:2px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}nav::-webkit-scrollbar{display:none}
nav a{padding:6px 9px;border-radius:6px;white-space:nowrap;color:var(--fg);font-size:13px}nav a.on,nav a:hover{background:var(--line);text-decoration:none}
main{padding:10px;max-width:1400px;margin:0 auto}
h1{font-size:20px;margin:6px 0 10px}h2{font-size:16px;margin:16px 0 8px;color:var(--fg)}h3{font-size:14px;margin:12px 0 6px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px}
.card .k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.card .v{font-size:20px;font-weight:600;margin-top:2px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
table{border-collapse:collapse;width:100%;min-width:480px;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--panel)}tr:last-child td{border-bottom:0}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600;background:var(--line)}
.b-ok{background:#1b4332;color:#95d5b2}.b-warn{background:#4a3b00;color:#ffe08a}.b-bad{background:#4a1c1c;color:#ffb4ab}.b-info{background:#1c2f4a;color:#9ecbff}
.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--mut)}.small{font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
select,button,input{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit}
button{cursor:pointer}button:hover{background:var(--line)}
label.chk{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--mut);margin-right:6px}
#chart{width:100%;height:70vh;min-height:420px}
pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px;overflow-x:auto;font-size:12px}
footer{color:var(--mut);font-size:11px;text-align:center;padding:14px}
.kv td:first-child{color:var(--mut);width:34%}
@media(max-width:600px){main{padding:6px}h1{font-size:17px}.card .v{font-size:17px}table{font-size:12px}}
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def fmt(x: Any, nd: int = 4) -> str:
    if x is None or x == "":
        return "-"
    if isinstance(x, bool):
        return "evet" if x else "hayır"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return esc(x)
    if f != f:
        return "-"
    if abs(f) >= 1000:
        return f"{f:,.2f}"
    if abs(f) >= 1:
        return f"{f:,.{nd}f}".rstrip("0").rstrip(".") if nd else f"{f:.0f}"
    return f"{f:.6g}"


def pct(x: Any, nd: int = 2) -> str:
    try:
        return f"{float(x):+.{nd}f}%"
    except (TypeError, ValueError):
        return "-"


def age_text(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}dk"
    if s < 172800:
        return f"{s // 3600}sa {s % 3600 // 60}dk"
    return f"{s // 86400}g"


def badge(text: Any, kind: str = "info") -> str:
    return f'<span class="badge b-{kind}">{esc(text)}</span>'


def health_badge(state: str) -> str:
    st = str(state or "UNKNOWN").upper()
    kind = {"HEALTHY": "ok", "DEGRADED": "warn", "PAUSED": "warn", "DATA_STALE": "warn", "KILL_SWITCH": "bad",
            "RECONCILIATION_REQUIRED": "bad"}.get(st, "info")
    return badge(st, kind)


def ks_badge(state: str) -> str:
    st = str(state or "ARMED").upper()
    return badge(st, "ok" if st == "ARMED" else "bad")


def verdict_badge(v: str) -> str:
    v = str(v or "")
    kind = "ok" if v in ("SPOT_LONG", "FUTURES_LONG", "LONG") else ("bad" if v in ("FUTURES_SHORT", "SHORT", "EXIT", "RISK_BLOCKED") else "info")
    return badge(v or "-", kind)


def pnl_cell(x: Any, nd: int = 2) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "<td class=num>-</td>"
    cls = "up" if f > 0 else ("dn" if f < 0 else "")
    return f'<td class="num {cls}">{f:+,.{nd}f}</td>'


def money_html(x, *, pct: bool = False, nd: int = 2) -> str:
    """Para/yüzde hücresi. Renk TEK BAŞINA anlam taşımaz — `+/-` işareti HER ZAMAN yazılır.

    |x| < 0.01 iken `+0.00`'a yuvarlanmaz (gerçek küçük K/Z sıfır gibi görünmez).
    """
    from ..pnl import fmt_money, fmt_pct, pnl_class
    if x is None or x == "":
        return "<td class=num>—</td>"
    txt = fmt_pct(x, nd) if pct else fmt_money(x)
    return f'<td class="num {pnl_class(x)}">{esc(txt)}</td>'


def live_bar(fr: dict | None) -> str:
    """CANLI/BAYAT göstergesi. Fiyat yaşı ile STRATEJİ TURU yaşı AYRI gösterilir."""
    if not fr:
        return ""
    tz = esc(fr.get("tz_label") or "UTC")
    pstate = fr.get("price_state")
    dot = {"live": "🟢", "stale": "🔴"}.get(pstate, "⚪")
    label = {"live": "CANLI", "stale": "FİYAT VERİSİ GÜNCEL DEĞİL"}.get(pstate, "FİYAT DURUMU BİLİNMİYOR")
    warn = ' warn-box' if pstate != "live" else ""
    return (f'<div class="card live{warn}" id="livebar" data-tz="{tz}">'
            f'<span id="livedot">{dot}</span> <b id="livelabel">{esc(label)}</b> — '
            f'Son fiyat güncellemesi: <span id="priceage">{esc(age_text(fr.get("price_age_s")))}</span> önce · '
            f'Son strateji turu: <span id="runage">{esc(age_text(fr.get("run_age_s")))}</span> önce · '
            f'Son coin-head kararı: <span id="headsage">{esc(age_text(fr.get("heads_age_s")))}</span> önce · '
            f'saat dilimi {tz}</div>')


def chief_block(cv) -> str:
    """Baş yönetici — UZUN HAM JSON YERİNE etiketli kartlar.

    «İşlem adayı» ile «açık pozisyon» KASITLI olarak ayrı kartlardadır: `breadth.long` son turdaki
    LONG *aday* sayısıdır, açık LONG *pozisyon* sayısı defterden gelir.
    """
    from ..pnl import fmt_money
    c = [card("Karar üretim zamanı", esc(cv.generated_at or "—")),
         card("Piyasa risk modu", badge(cv.market_risk_mode, "info")),
         card("LONG işlem adayı", str(cv.long_candidates), "karar — açık pozisyon DEĞİL"),
         card("SHORT işlem adayı", str(cv.short_candidates), "karar — açık pozisyon DEĞİL"),
         card("NO TRADE", str(cv.no_trade)),
         card("HOLD", str(cv.hold), "açık pozisyonu korunan semboller"),
         card("Veri geçersiz", str(cv.data_invalid)),
         card("Açık LONG pozisyon", str(cv.open_long), "defterden (gerçek)"),
         card("Açık SHORT pozisyon", str(cv.open_short), "defterden (gerçek)"),
         card("Toplam açık pozisyon", str(cv.open_total), "defterden (gerçek)"),
         card("Long notional", fmt_money(cv.long_notional, signed=False, currency="") + " USDT"),
         card("Short notional", fmt_money(cv.short_notional, signed=False, currency="") + " USDT"),
         card("Açık risk", (fmt_money(cv.open_risk_usdt, signed=False, currency="") + " USDT") if cv.open_risk_usdt is not None else "Veri yok"),
         card("Teminat kullanımı", (f"%{float(cv.margin_util_pct):.1f}") if cv.margin_util_pct is not None else "Veri yok"),
         card("Günlük gerçekleşen net K/Z", fmt_money(cv.realized_today)),
         card("Günlük gerçekleşmemiş net K/Z", fmt_money(cv.unrealized_open)),
         card("Drawdown", (f"%{float(cv.drawdown_pct):.2f}") if cv.drawdown_pct is not None else "Veri yok")]
    return "<h2>Baş yönetici</h2>" + f'<div class="grid">{"".join(c)}</div>'


def live_script(cfg) -> str:
    """Hafif POLLING. WebSocket yok; tarayıcı borsaya bağlanmaz; istek fırtınası engellenir.

    * Aynı anda tek istek (overlap koruması), `AbortController` ile zaman aşımı.
    * Arka plan sekmesinde aralık `background_backoff_mult` katına çıkar.
    * Bağlantı koparsa CANLI etiketi YEŞİL KALMAZ.
    """
    pos = int(getattr(cfg, "poll_positions_s", 7)) * 1000
    summ = int(getattr(cfg, "poll_portfolio_s", 20)) * 1000
    heal = int(getattr(cfg, "poll_health_s", 12)) * 1000
    mult = int(getattr(cfg, "background_backoff_mult", 4))
    stale = int(getattr(cfg, "stale_price_s", 90))
    return r"""<script>
(function(){
 var busy={},fails=0;
 function agetxt(s){if(s==null)return '-';s=Math.floor(s);if(s<90)return s+'s';if(s<5400)return Math.floor(s/60)+'dk';
   if(s<172800)return Math.floor(s/3600)+'sa '+Math.floor((s%3600)/60)+'dk';return Math.floor(s/86400)+'g';}
 function setLive(state,fr){
  var d=document.getElementById('livedot'),l=document.getElementById('livelabel'),b=document.getElementById('livebar');
  if(!d||!l||!b)return;
  if(state==='off'){d.textContent='\u26AB';l.textContent='BA\u011eLANTI YOK';b.classList.add('warn-box');return;}
  var st=fr&&fr.price_state;
  d.textContent=st==='live'?'\uD83D\uDFE2':(st==='stale'?'\uD83D\uDD34':'\u26AA');
  l.textContent=st==='live'?'CANLI':(st==='stale'?'F\u0130YAT VER\u0130S\u0130 G\u00dcNCEL DE\u011e\u0130L':'F\u0130YAT DURUMU B\u0130L\u0130NM\u0130YOR');
  b.classList.toggle('warn-box',st!=='live');
  var p=document.getElementById('priceage');if(p&&fr)p.textContent=agetxt(fr.price_age_s);
  var r=document.getElementById('runage');if(r&&fr)r.textContent=agetxt(fr.run_age_s);
  var h=document.getElementById('headsage');if(h&&fr)h.textContent=agetxt(fr.heads_age_s);
 }
 function poll(key,url,cb,base){
  function tick(){
   if(busy[key])return;                                   /* overlap koruması: istek fırtınası yok */
   busy[key]=1;
   var ac=('AbortController' in window)?new AbortController():null;
   var to=setTimeout(function(){if(ac)ac.abort();},Math.max(5000,base));
   fetch(url,{headers:window.__authHeaders||{},signal:ac?ac.signal:undefined})
    .then(function(r){return r.ok?r.json():Promise.reject(r.status);})
    .then(function(d){fails=0;cb(d);})
    .catch(function(){fails++;if(fails>2)setLive('off',null);})
    .then(function(){clearTimeout(to);busy[key]=0;});
  }
  function iv(){return document.hidden?base*BG:base;}   /* arka plan sekmesinde backoff */
  var timer=setInterval(function(){tick();},base);
  document.addEventListener('visibilitychange',function(){clearInterval(timer);timer=setInterval(function(){tick();},iv());});
  tick();
 }
 var BG=__MULT__;
 poll('pos','/api/live/positions'+(window.__tokenQs||'').replace('?','?'),function(d){
   setLive('on',d.freshness);
   var el=document.getElementById('postbl');
   if(el&&d.rows){
     var h='<table><thead><tr>'+d.columns.map(function(c){return '<th>'+c+'</th>';}).join('')+'</tr></thead><tbody>';
     if(!d.rows.length){h+='<tr><td colspan="'+d.columns.length+'" class="mut">a\u00e7\u0131k pozisyon yok</td></tr>';}
     d.rows.forEach(function(r){h+='<tr>'+r.map(function(c,i){
       var s=String(c==null?'\u2014':c);var cls='';
       if(i>=3){cls=' class="num'+(s.charAt(0)==='+'?' up':(s.charAt(0)==='-'?' dn':''))+'"';}
       return '<td'+cls+'>'+s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</td>';}).join('')+'</tr>';});
     el.innerHTML=h+'</tbody></table>';
   }
 },__POS__);
 poll('sum','/api/live/summary',function(){},__SUM__);
 poll('hp','/api/live/health',function(d){
   if(d&&d.price_age_s!=null&&d.price_age_s>__STALE__){setLive('on',{price_state:'stale',price_age_s:d.price_age_s,
     run_age_s:d.last_run_age_s,heads_age_s:null});}
 },__HEAL__);
})();
</script>""".replace("__POS__", str(pos)).replace("__SUM__", str(summ)).replace("__HEAL__", str(heal))             .replace("__MULT__", str(mult)).replace("__STALE__", str(stale))


def card(k: str, v: str, sub: str = "") -> str:
    return f'<div class="card"><div class="k">{esc(k)}</div><div class="v">{v}</div>' + (f'<div class="small mut">{sub}</div>' if sub else "") + "</div>"


def table(headers: list[str], rows: Iterable[Iterable[str]], *, num_cols: set[int] | None = None, empty: str = "kayıt yok") -> str:
    """Hücreler önceden HTML olarak hazırlanmış kabul edilir (esc çağıranın sorumluluğu) — `<td` ile başlıyorsa olduğu gibi konur."""
    num_cols = num_cols or set()
    rows = list(rows)
    if not rows:
        return f'<div class="card mut">{esc(empty)}</div>'
    th = "".join(f'<th class="{"num" if i in num_cols else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = []
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            c = "" if c is None else str(c)
            cells.append(c if c.startswith("<td") else f'<td class="{"num" if i in num_cols else ""}">{c}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def kv_table(d: dict[str, Any], *, skip: set[str] | None = None) -> str:
    skip = skip or set()
    rows = []
    for k, v in d.items():
        if k in skip:
            continue
        if isinstance(v, (dict, list)):
            vv = f"<code>{esc(json.dumps(v, ensure_ascii=False)[:300])}</code>"
        else:
            vv = fmt(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else esc(v)
        rows.append([esc(k), vv])
    if not rows:
        return '<div class="card mut">boş</div>'
    return '<div class="tw"><table class="kv"><tbody>' + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows) + "</tbody></table></div>"


def render_any(obj: Any, depth: int = 0) -> str:
    """Bilinmeyen şekilli JSON'u makul HTML'e çevirir (liste-of-dict → tablo, dict → k/v)."""
    if depth > 3:
        return f"<pre>{esc(json.dumps(obj, ensure_ascii=False, indent=1)[:4000])}</pre>"
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            keys: list[str] = []
            for x in obj[:200]:
                for k in x.keys():
                    if k not in keys:
                        keys.append(k)
            keys = keys[:14]
            rows = [[(fmt(x.get(k)) if isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) else esc(json.dumps(x.get(k), ensure_ascii=False)[:80] if isinstance(x.get(k), (dict, list)) else x.get(k))) for k in keys] for x in obj[:200]]
            return table(keys, rows)
        return "<ul>" + "".join(f"<li>{esc(x) if not isinstance(x, (dict, list)) else render_any(x, depth + 1)}</li>" for x in obj[:200]) + "</ul>"
    if isinstance(obj, dict):
        simple = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        out = [kv_table(simple)] if simple else []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"<h3>{esc(k)}</h3>" + render_any(v, depth + 1))
        return "".join(out) or '<div class="card mut">boş</div>'
    return f"<pre>{esc(obj)}</pre>"


def page(title: str, body: str, active: str = "/", *, brand: str = "Trading Bot", extra_head: str = "", token_qs: str = "") -> str:
    nav = "".join(f'<a href="{href}{token_qs}" class="{"on" if href == active else ""}">{esc(label)}</a>' for href, label in NAV)
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {esc(brand)}</title><style>{CSS}{CSS_EXTRA}</style>{extra_head}</head><body>
<header><span class="brand">📈 {esc(brand)}</span><nav>{nav}</nav></header>
<main><h1>{esc(title)}</h1>{body}</main>
<footer>salt-okunur panel · PAPER · yatırım tavsiyesi değildir · <span id="sse" class="mut">canlı: bağlanıyor…</span></footer>
<script>
(function(){{try{{var es=new EventSource('/events{token_qs}');var el=document.getElementById('sse');
es.addEventListener('heartbeat',function(){{el.textContent='canlı: ok';}});
es.addEventListener('state',function(e){{el.textContent='canlı: güncellendi';if(window.__onState){{window.__onState(JSON.parse(e.data));}}}});
es.onerror=function(){{el.textContent='canlı: bağlantı yok';}};}}catch(e){{}}}})();
</script></body></html>"""


CHART_JS = r"""
(function(){
  var base=window.__chartBase, tf=window.__chartTf||'4h', market=window.__chartMarket||'spot', tokenQs=window.__tokenQs||'';
  var OV=[['sma25','SMA25','#f5c542'],['sma50','SMA50','#ffb74d'],['sma99','SMA99','#ff8a65'],['sma200','SMA200','#ba68c8'],
          ['ema25','EMA25','#4dd0e1'],['ema50','EMA50','#4fc3f7'],['ema99','EMA99','#7986cb'],['ema200','EMA200','#ce93d8'],
          ['vwap','VWAP','#fff176'],['bb_up','BB üst','#90a4ae'],['bb_mid','BB orta','#78909c'],['bb_lo','BB alt','#90a4ae']];
  var DEF={ema25:1,ema99:1,ema200:1,vwap:0,sma25:0,sma50:0,sma99:0,sma200:0,bb_up:0,bb_mid:0,bb_lo:0,ema50:0};
  var box=document.getElementById('ovbox');
  OV.forEach(function(o){var l=document.createElement('label');l.className='chk';var c=document.createElement('input');c.type='checkbox';c.dataset.k=o[0];c.checked=!!DEF[o[0]];l.appendChild(c);l.appendChild(document.createTextNode(o[1]));box.appendChild(l);c.addEventListener('change',function(){toggle(o[0],c.checked);});});
  var traceIdx={};
  function toggle(k,on){if(traceIdx[k]!==undefined){Plotly.restyle('chart',{visible:on?true:'legendonly'},[traceIdx[k]]);}}
  function line(y,dash,color,name){return {type:'scatter',mode:'lines',x:[],y:[],line:{width:1,color:color,dash:dash},name:name,hoverinfo:'skip',xaxis:'x',yaxis:'y'};}
  function load(){
    var sel=document.getElementById('tf');tf=sel?sel.value:tf;var ms=document.getElementById('mk');market=ms?ms.value:market;
    var n=parseInt((document.getElementById('nbars')||{}).value||'300',10);
    fetch('/api/candles/'+base+'?tf='+tf+'&market='+market+'&n='+n+(tokenQs?'&'+tokenQs.slice(1):''),{headers:window.__authHeaders||{}}).then(function(r){return r.json();}).then(draw).catch(function(e){document.getElementById('chart').innerHTML='<div class=card>grafik yüklenemedi: '+e+'</div>';});
  }
  function draw(d){
    if(d.error){document.getElementById('chart').innerHTML='<div class=card>'+d.error+'</div>';return;}
    var x=d.t.map(function(t){return new Date(t);});
    var traces=[{type:'candlestick',x:x,open:d.o,high:d.h,low:d.l,close:d.c,name:base,increasing:{line:{color:'#26a69a'}},decreasing:{line:{color:'#ef5350'}},xaxis:'x',yaxis:'y'}];
    var vcol=d.c.map(function(c,i){return c>=d.o[i]?'rgba(38,166,154,.4)':'rgba(239,83,80,.4)';});
    traces.push({type:'bar',x:x,y:d.v,name:'Hacim',marker:{color:vcol},xaxis:'x',yaxis:'y5',hoverinfo:'skip'});
    traceIdx={};
    OV.forEach(function(o){var s=d.overlays[o[0]];if(!s)return;var t=line(s,o[0].indexOf('bb')===0?'dot':'solid',o[2],o[1]);t.x=x;t.y=s;t.visible=DEF[o[0]]?true:'legendonly';var cb=box.querySelector('input[data-k="'+o[0]+'"]');if(cb)t.visible=cb.checked?true:'legendonly';traceIdx[o[0]]=traces.length;traces.push(t);});
    var p=d.panels||{};
    traces.push({type:'scatter',mode:'lines',x:x,y:p.rsi,name:'RSI14',line:{width:1,color:'#ce93d8'},xaxis:'x',yaxis:'y2'});
    traces.push({type:'bar',x:x,y:p.macd_hist,name:'MACD hist',marker:{color:(p.macd_hist||[]).map(function(v){return v>=0?'rgba(38,166,154,.6)':'rgba(239,83,80,.6)';})},xaxis:'x',yaxis:'y3'});
    traces.push({type:'scatter',mode:'lines',x:x,y:p.macd_line,name:'MACD',line:{width:1,color:'#4fc3f7'},xaxis:'x',yaxis:'y3'});
    traces.push({type:'scatter',mode:'lines',x:x,y:p.macd_signal,name:'Sinyal',line:{width:1,color:'#ffb74d'},xaxis:'x',yaxis:'y3'});
    var hasDeriv=(p.funding&&p.funding.length)||(p.oi&&p.oi.length);
    if(hasDeriv){
      if(p.funding&&p.funding.length){traces.push({type:'bar',x:p.funding.map(function(f){return new Date(f.t||f[0]);}),y:p.funding.map(function(f){return f.rate!==undefined?f.rate:f[1];}),name:'Funding %',marker:{color:'#fff176'},xaxis:'x',yaxis:'y4'});}
      if(p.oi&&p.oi.length){traces.push({type:'scatter',mode:'lines',x:p.oi.map(function(f){return new Date(f.t||f[0]);}),y:p.oi.map(function(f){return f.oi!==undefined?f.oi:f[1];}),name:'OI',line:{width:1,color:'#80cbc4'},xaxis:'x',yaxis:'y6'});}
    }
    var shapes=[],ann=[];var x0=x[0],x1=x[x.length-1];
    function hl(y,color,dash,label){if(y==null)return;shapes.push({type:'line',xref:'x',yref:'y',x0:x0,x1:x1,y0:y,y1:y,line:{color:color,width:1,dash:dash}});ann.push({xref:'paper',x:1,yref:'y',y:y,text:label+' '+y,showarrow:false,font:{size:10,color:color},xanchor:'left',bgcolor:'rgba(14,17,22,.7)'});}
    (d.levels||[]).forEach(function(l){hl(l.price,l.kind==='resistance'?'#ef9a9a':(l.kind==='support'?'#a5d6a7':'#b0bec5'),'dot',l.name);});
    var pl=d.position&&d.position.entry?d.position:(d.plan||{});
    if(pl.entry){hl(pl.entry,'#ffffff','solid','GİRİŞ');hl(pl.stop,'#ef5350','solid','STOP');hl(pl.tp1,'#26a69a','dash','TP1');hl(pl.tp2,'#26a69a','dot','TP2');hl(pl.liq,'#ff7043','dashdot','LIQ');
      if(pl.stop&&pl.tp1){var xa=x[Math.max(0,x.length-15)];shapes.push({type:'rect',xref:'x',yref:'y',x0:xa,x1:x1,y0:Math.min(pl.entry,pl.stop),y1:Math.max(pl.entry,pl.stop),fillcolor:'rgba(239,83,80,.15)',line:{width:0}});shapes.push({type:'rect',xref:'x',yref:'y',x0:xa,x1:x1,y0:Math.min(pl.entry,pl.tp1),y1:Math.max(pl.entry,pl.tp1),fillcolor:'rgba(38,166,154,.15)',line:{width:0}});}}
    var dom=hasDeriv?{y:[0.46,1],y2:[0.31,0.44],y3:[0.16,0.29],y4:[0,0.14]}:{y:[0.42,1],y2:[0.22,0.40],y3:[0,0.20]};
    var layout={paper_bgcolor:'#0e1116',plot_bgcolor:'#0e1116',font:{color:'#d7dde5',size:11},margin:{l:48,r:70,t:10,b:30},showlegend:true,legend:{orientation:'h',y:1.02,font:{size:10}},dragmode:'pan',hovermode:'x',
      xaxis:{rangeslider:{visible:false},gridcolor:'#1c2430',type:'date'},
      yaxis:{domain:dom.y,gridcolor:'#1c2430',side:'right'},
      yaxis5:{domain:dom.y,overlaying:'y',side:'left',showgrid:false,range:[0,Math.max.apply(null,d.v.filter(function(v){return v!=null;}).concat([1]))*4],showticklabels:false},
      yaxis2:{domain:dom.y2,gridcolor:'#1c2430',range:[0,100],side:'right',tickvals:[30,50,70]},
      yaxis3:{domain:dom.y3,gridcolor:'#1c2430',side:'right'},
      shapes:shapes,annotations:ann};
    if(hasDeriv){layout.yaxis4={domain:dom.y4,gridcolor:'#1c2430',side:'right'};layout.yaxis6={domain:dom.y4,overlaying:'y4',side:'left',showgrid:false};}
    Plotly.newPlot('chart',traces,layout,{responsive:true,displaylogo:false,scrollZoom:true,modeBarButtonsToRemove:['lasso2d','select2d']});
  }
  document.getElementById('tf').addEventListener('change',load);var mk=document.getElementById('mk');if(mk)mk.addEventListener('change',load);
  document.getElementById('nbars').addEventListener('change',load);document.getElementById('reload').addEventListener('click',load);
  window.__onState=function(s){if(s&&s.changed&&(s.changed.indexOf('coin_heads')>=0||s.changed.indexOf('futures_ledger')>=0))load();};
  if(typeof Plotly==='undefined'){document.getElementById('chart').innerHTML='<div class=card>plotly.min.js yüklenemedi (plotly paketi kurulu değil?)</div>';}else{load();}
})();
"""


def chart_block(base: str, tf: str = "4h", market: str = "spot", *, token_qs: str = "", max_bars: int = 600) -> str:
    tfs = "".join(f'<option value="{t}" {"selected" if t == tf else ""}>{t}</option>' for t in ("1h", "4h", "1d"))
    mks = "".join(f'<option value="{m}" {"selected" if m == market else ""}>{m}</option>' for m in ("spot", "futures"))
    return f"""<div class="row" style="margin:6px 0">
<label class="chk">TF <select id="tf">{tfs}</select></label><label class="chk">Piyasa <select id="mk">{mks}</select></label>
<label class="chk">Bar <input id="nbars" type="number" min="50" max="{max_bars}" value="300" style="width:70px"></label>
<button id="reload">↻</button><span id="ovbox"></span></div>
<div id="chart"></div>
<script src="/static/plotly.min.js{token_qs}"></script>
<script>window.__chartBase={json.dumps(base)};window.__chartTf={json.dumps(tf)};window.__chartMarket={json.dumps(market)};window.__tokenQs={json.dumps(token_qs)};</script>
<script>{CHART_JS}</script>"""


__all__ = ["page", "table", "kv_table", "render_any", "card", "badge", "health_badge", "ks_badge", "verdict_badge", "pnl_cell",
           "fmt", "pct", "esc", "age_text", "chart_block", "CSS", "NAV", "CHART_JS"]

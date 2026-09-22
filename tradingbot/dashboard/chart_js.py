# -*- coding: utf-8 -*-
"""CHART ANALYSIS V1 — panel grafiği (Plotly). Mevcut mum/gösterge çizimi korunur; analiz katmanları eklenir:
seviyeler, bölgeler, trend çizgileri, formasyon çizgileri, işlemler (plan / gerçekleşen / işaret fiyatı ayrı),
katman aç/kapat, öğe detayı (dayanak, teyit, gerekçe, karar etkisi), açıklama paneli, geçmiş analiz seçimi,
PNG/JSON indirme, veri kaynağı/tazelik satırı, geç gelen istek koruması. SALT SUNUM.

İstek sözleşmesi (bulgu #4 onarımı): istek kapsamı (dilim/piyasa/defter/bar/geçmiş) DOM'dan BİR kez okunur ve o
istekle taşınır; kapsam değişince önceki geçmiş seçimi temizlenir (eski defterin analysis_id'si yeni kapsama
GÖNDERİLMEZ); grafik ve geçmiş yanıtları hem `req` sırası hem de yanıtın taşıdığı kapsam ile eşleştirilir — geç
gelen ya da başka kapsama ait yanıt yeni seçimi/grafiği EZEMEZ. Kimlik: `analysis.analysis_id` (kök alan).
"""

CHART_JS = r"""
(function(){
  var base=window.__chartBase, tokenQs=window.__tokenQs||'';
  var OV=[['sma25','SMA25','#f5c542'],['sma50','SMA50','#ffb74d'],['sma99','SMA99','#ff8a65'],['sma200','SMA200','#ba68c8'],
          ['ema25','EMA25','#4dd0e1'],['ema50','EMA50','#4fc3f7'],['ema99','EMA99','#7986cb'],['ema200','EMA200','#ce93d8'],
          ['vwap','VWAP','#fff176'],['bb_up','BB üst','#90a4ae'],['bb_mid','BB orta','#78909c'],['bb_lo','BB alt','#90a4ae']];
  var DEF={ema25:0,ema99:0,ema200:0,vwap:0,sma25:0,sma50:0,sma99:0,sma200:0,bb_up:0,bb_mid:0,bb_lo:0,ema50:0};
  // VARSAYILAN GORUNUM SADE (2026-09-16): yalniz mumlar + GERCEK islem katmani (giris/cikis/stop/hedef).
  // Seviye/bolge/trend/formasyon/gosterge katmanlari "Katmanlar" altinda ACILIR — ekran hepsi acik gelmez.
  // YAPI (2026-09-23): motorun karar satirindaki TEK kayit (secili islem ya da son karar) — varsayilan ACIK, tek kayit.
  var LAYER_DEF={levels:0,zones:0,trend:0,patterns:0,trades:1,structure:1,volume:0,indicators:0};
  var LAYERS=[['levels','Seviyeler'],['zones','Bölgeler'],['trend','Trend'],['patterns','Formasyonlar'],['trades','İşlemler'],['structure','Yapı (bot kararı)'],
              ['volume','Hacim'],['indicators','Göstergeler (RSI/MACD/EMA)']];
  var IMPACT={USED_IN_DECISION:['kararda kullanıldı','#26a69a'],OBSERVATION_ONLY:['yalnız gözlem','#90a4ae'],UNCONFIRMED:['henüz teyitsiz','#ffb74d'],INVALID:['geçersiz / ihlal','#ef5350']};
  var MKT={USDM_PERP:'futures',SPOT:'spot'};
  var box=document.getElementById('ovbox'), lbox=document.getElementById('laybox');
  OV.forEach(function(o){var l=document.createElement('label');l.className='chk';var c=document.createElement('input');c.type='checkbox';c.dataset.k=o[0];c.checked=!!DEF[o[0]];l.appendChild(c);l.appendChild(document.createTextNode(o[1]));box.appendChild(l);c.addEventListener('change',function(){if(last)draw(last);});});
  LAYERS.forEach(function(o){var l=document.createElement('label');l.className='chk';var c=document.createElement('input');c.type='checkbox';c.dataset.layer=o[0];c.checked=!!LAYER_DEF[o[0]];l.appendChild(c);l.appendChild(document.createTextNode(o[1]));lbox.appendChild(l);c.addEventListener('change',function(){if(last)draw(last);});});
  function layerOn(k){var cb=lbox.querySelector('input[data-layer="'+k+'"]');return !cb||cb.checked;}
  var seq=0, hseq=0, last=null, elIndex=[];
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function enc(s){return encodeURIComponent(String(s==null?'':s));}
  function iso(ms){if(ms==null)return '—';try{return new Date(ms).toISOString().replace('T',' ').slice(0,16)+'Z';}catch(e){return String(ms);}}
  function fmt(v){if(v==null||isNaN(v))return '—';var a=Math.abs(v);return a>=1000?v.toFixed(2):a>=1?v.toFixed(4):v.toPrecision(5);}
  function val(id,dflt){var el=document.getElementById(id);return (el&&el.value!=null&&el.value!=='')?el.value:dflt;}
  function scope(){   // istek kapsami DOM'dan BIR kez okunur; istekle birlikte tasinir
    var n=parseInt(val('nbars','300'),10);
    return {tf:val('tf',window.__chartTf||'4h'),market:val('mk',window.__chartMarket||'spot'),book:val('bk',window.__chartBook||'main'),n:isNaN(n)?300:n,aid:val('hist','')};}
  function sameScope(a,b){return a.tf===b.tf&&a.market===b.market&&a.book===b.book;}
  function tq(){return tokenQs?'&'+tokenQs.slice(1):'';}
  function chartUrl(sc,my){return '/api/chart/'+base+'?tf='+enc(sc.tf)+'&market='+enc(sc.market)+'&book='+enc(sc.book)+'&n='+sc.n+(sc.aid?'&analysis_id='+enc(sc.aid):'')+(window.__chartTrade?'&trade='+enc(window.__chartTrade):'')+(window.__chartAsOf?'&as_of='+enc(window.__chartAsOf):'')+tq()+'&req='+my;}
  function historyUrl(sc,my){return '/api/chart/'+base+'/history?tf='+enc(sc.tf)+'&market='+enc(sc.market)+'&book='+enc(sc.book)+tq()+'&req='+my;}
  function identityMatches(d,sc){var A=d.analysis||{};var a=A.identity||{};
    if(a.timeframe&&a.timeframe!==sc.tf)return false;if(a.book_id&&a.book_id!==sc.book)return false;
    if(a.market_type&&MKT[a.market_type]&&MKT[a.market_type]!==sc.market)return false;
    if(sc.aid&&A.analysis_id&&A.analysis_id!==sc.aid)return false;return true;}
  function load(sc){
    sc=sc||scope();var my=++seq;
    fetch(chartUrl(sc,my),{headers:window.__authHeaders||{}}).then(function(r){return r.json();}).then(function(d){
      if(my!==seq)return;                                                    // daha yeni istek var: eski yanit cizilmez
      if(d.req!==undefined&&d.req!==null&&parseInt(d.req,10)!==my)return;    // yanit baska istegin yankisi
      if(!sameScope(scope(),sc))return;                                      // kullanici bu arada kapsam degistirdi
      if(d.tf&&d.tf!==sc.tf)return;if(d.market&&d.market!==sc.market)return;if(d.book&&d.book!==sc.book)return;
      if(!identityMatches(d,sc))return;                                      // sunucu baska kimlik dondurdu: EZME
      last=d;draw(d);}).catch(function(e){if(my===seq)document.getElementById('chart').innerHTML='<div class=card>grafik yüklenemedi: '+esc(e)+'</div>';});
  }
  function resetHistory(){var h=document.getElementById('hist');if(!h)return;h.innerHTML='<option value="">şimdi (canlı analiz)</option>';h.value='';}
  function loadHistory(sc,keepAid){var h=document.getElementById('hist');if(!h)return;
    sc=sc||scope();var my=++hseq;var want=keepAid||'';
    fetch(historyUrl(sc,my),{headers:window.__authHeaders||{}}).then(function(r){return r.json();}).then(function(d){
      if(my!==hseq)return;if(d.req!==undefined&&d.req!==null&&parseInt(d.req,10)!==my)return;
      if(d.tf!==sc.tf||d.market!==sc.market||d.book!==sc.book)return;        // yanit istek kapsamina ait degil
      if(!sameScope(scope(),sc))return;
      var rows=(d.rows||[]).slice().reverse();
      h.innerHTML='<option value="">şimdi (canlı analiz)</option>'+rows.map(function(r){return '<option value="'+esc(r.analysis_id)+'">'+esc(iso(r.as_of_ms))+' · '+esc(r.analysis_id)+'</option>';}).join('');
      h.value=(want&&rows.some(function(r){return r.analysis_id===want;}))?want:'';}).catch(function(){});}
  function srcLine(d){var s=d.source||{};var A=d.analysis||{};var a=A.identity||{};var parts=[];
    if(s.missing){parts.push('<span class="bad">VERİ YOK: '+esc(s.base)+' '+esc(s.tf)+' '+esc(s.market)+' dosyası bulunamadı (başka piyasa/dilimle doldurulmadı)</span>');}
    else{parts.push('kaynak: '+esc(s.file||'?')+' ('+esc(s.market)+' '+esc(s.tf)+')');parts.push('son bar: '+esc(iso(s.last_bar_ts)));if(s.age_s!=null)parts.push('yaş: '+Math.round(s.age_s/60)+' dk');
      if(s.stale)parts.push('<span class="bad">BAYAT VERİ — güncel değil</span>');}
    if(d.historical)parts.push('<span class="warn">GEÇMİŞ ANALİZ '+esc(a.as_of||'')+' — yalnız o anda kapanmış mumlar; işlem katmanı o anki defter kaydı</span>');
    else if(d.live)parts.push('canlı defter: '+esc(iso(d.live.as_of_ms))+' ('+esc(d.live.source||'defter')+')');
    if(A.analysis_id)parts.push('analiz '+esc(A.analysis_id)+' · an '+esc(iso(a.as_of_ms))+' · '+(d.analysis_stored?'motor kaydı':'panel hesabı (kaydedilmedi)')+' · kod '+esc((a.code_sha||'').slice(0,7))+' · defter '+esc(a.book_id)+' · '+esc(MKT[a.market_type]||a.market_type)+' '+esc(a.timeframe));
    var er=d.engine_record;
    if(er&&!d.historical&&er.status)parts.push('<span class="bad">MOTOR KAYDI YOK: '+(er.status==='MARKET_MISMATCH'?'piyasa uyuşmazlığı':'çerçeve provenansı yok')+' — mum piyasası '+esc(er.bar_market||'bilinmiyor')+', defter piyasası '+esc(er.book_market||'?')+(er.as_of?' ('+esc(er.as_of)+')':'')+'</span>');
    else if(er&&!d.historical&&!er.matches_now)parts.push('<span class="mut">motorun son kaydı '+esc(er.analysis_id)+' ('+esc(iso(er.as_of_ms))+') şimdiki durumdan farklı: '+esc((er.diff||[]).join('; '))+'</span>');
    if(!d.historical&&d.live&&d.live.mark_price_source&&d.live.mark_price!=null)parts.push('canlı fiyat: son mum kapanışı '+esc(fmt(d.live.mark_price))+' ('+esc(d.live.mark_price_source.file||'mum')+(d.live.mark_price_source.bar_closed?', kapanmış bar':', kapanmamış bar')+'; borsa mark fiyatı DEĞİL)');
    if(A.synthetic)parts.push('<span class="warn">SENTETİK GÖSTERİM VERİSİ</span>');
    var S=d.structure||{};if(S.decision)parts.push('yapı katmanı: '+esc(S.decision.bot||'')+' '+esc(S.decision.action||'')+' · '+(S.origin==='trade'?'işlem '+esc(S.trade||'')+' GİRİŞ anındaki kayıt':'son karar kaydı')+(S.record_tf&&S.record_tf!==d.tf?' · kayıt dilimi '+esc(S.record_tf)+' (grafik '+esc(d.tf)+')':''));
    else if(S.note)parts.push('<span class="mut">'+esc(S.note)+'</span>');
    document.getElementById('srcline').innerHTML=parts.join(' · ');}
  function explain(d){var a=d.analysis||{};var ex=a.explanation||[];var html='';
    if(!ex.length)html='<div class="mut">Açıklama yok.</div>';
    ex.forEach(function(l){html+='<div class="xl"><b>'+esc(l.k)+':</b> '+esc(l.v)+'</div>';});
    var el=a.elements||[];var cnt={};el.forEach(function(e){cnt[e.decision_impact]=(cnt[e.decision_impact]||0)+1;});
    html+='<div class="mut small" style="margin-top:6px">'+Object.keys(IMPACT).map(function(k){return '<span style="color:'+IMPACT[k][1]+'">■</span> '+IMPACT[k][0]+' '+(cnt[k]||0);}).join(' · ')+'</div>';
    document.getElementById('explain').innerHTML=html;document.getElementById('detail').innerHTML='<div class="mut">Bir çizgi/işarete tıkla: dayanak, teyit zamanı, gerekçe ve karar etkisi burada açılır.</div>';}
  function detail(e){if(!e)return;var im=IMPACT[e.decision_impact]||[e.decision_impact,'#ccc'];var h='<div><b>'+esc(e.label_tr)+'</b> <span class="badge" style="border-color:'+im[1]+';color:'+im[1]+'">'+esc(im[0])+'</span></div>';
    h+='<div class="small">tür: '+esc(e.kind)+' · katman: '+esc(e.layer)+(e.timeframe?' · dilim: '+esc(e.timeframe):'')+(e.status?' · durum: '+esc(e.status):'')+'</div>';
    if(e.price!=null)h+='<div class="small">fiyat: '+fmt(e.price)+(e.lower!=null?' · aralık '+fmt(e.lower)+'–'+fmt(e.upper):'')+'</div>';
    if(e.t0!=null||e.t1!=null)h+='<div class="small">bar (açılış): '+iso(e.t0)+' → '+iso(e.t1)+'</div>';
    if(e.slope_per_day!=null)h+='<div class="small">eğim: '+fmt(e.slope_per_day)+'/gün · temas '+((e.touches||[]).length)+' · ihlal '+((e.breaks||[]).length)+(e.broken_at!=null?' (kırılış bilindi: '+iso(e.broken_at)+')':'')+'</div>';
    if(e.confirmed_at!=null)h+='<div class="small">teyit (bar kapanışı): '+iso(e.confirmed_at)+'</div>';
    if(e.break_known_at!=null)h+='<div class="small">kırılış barı: '+iso(e.break_at)+' açılış → '+iso(e.break_known_at)+' kapanışta bilindi</div>';
    if((e.anchors||[]).length){h+='<div class="small">dayanaklar:<ul style="margin:2px 0 2px 16px;padding:0">'+e.anchors.map(function(a){return '<li>'+esc(a.role||a.side||'')+' '+fmt(a.price)+' @ '+iso(a.timestamp)+(a.confirmed_at?' (teyit '+iso(a.confirmed_at)+')':' (teyitsiz)')+'</li>';}).join('')+'</ul></div>';}
    h+='<div class="small">gerekçe: '+esc(e.rationale_tr)+'</div>';if(e.invalidation_tr)h+='<div class="small">geçersizleşme: '+esc(e.invalidation_tr)+'</div>';
    if(e.price_source)h+='<div class="small">fiyat kaynağı: '+esc(e.price_source.kind==='candle_close'?('son mum kapanışı · '+(e.price_source.file||'mum')+' · bar '+iso(e.price_source.bar_open_ms)+(e.price_source.bar_closed?' (kapanmış)':' (kapanmamış)')+' · borsa mark fiyatı DEĞİL'):(e.price_source.kind==='ticker_last'?'canlı tik (motor turu)':e.price_source.kind))+(e.unrealized_pnl_gross!=null?' · açık K/Z brüt '+fmt(e.unrealized_pnl_gross)+' USDT (ücret/fonlama hariç)':'')+'</div>';
    if(e.source)h+='<div class="mut small">kaynak: '+esc(e.source.module)+'.'+esc(e.source.function)+' '+esc(JSON.stringify(e.source.params||{}))+(e.source.live?' · canlı '+esc(e.source.live_at||''):'')+'</div>';
    document.getElementById('detail').innerHTML=h;}
  function draw(d){
    srcLine(d);explain(d);
    if(d.error){document.getElementById('chart').innerHTML='<div class=card>'+esc(d.error)+'</div>';return;}
    var x=d.t.map(function(t){return new Date(t);});var x0=x[0],x1=x[x.length-1];var tfms=d.tf_ms||14400000;
    var traces=[{type:'candlestick',x:x,open:d.o,high:d.h,low:d.l,close:d.c,name:base,increasing:{line:{color:'#26a69a'}},decreasing:{line:{color:'#ef5350'}},xaxis:'x',yaxis:'y'}];
    var vcol=d.c.map(function(c,i){return c>=d.o[i]?'rgba(38,166,154,.4)':'rgba(239,83,80,.4)';});
    var showVol=layerOn('volume'), showInd=layerOn('indicators');
    if(showVol)traces.push({type:'bar',x:x,y:d.v,name:'Hacim',marker:{color:vcol},xaxis:'x',yaxis:'y5',hoverinfo:'skip'});
    if(showInd)OV.forEach(function(o){var s=d.overlays[o[0]];if(!s)return;var cb=box.querySelector('input[data-k="'+o[0]+'"]');var on=cb?cb.checked:!!DEF[o[0]];
      traces.push({type:'scatter',mode:'lines',x:x,y:s,line:{width:1,color:o[2],dash:o[0].indexOf('bb')===0?'dot':'solid'},name:o[1],hoverinfo:'skip',visible:on?true:'legendonly',xaxis:'x',yaxis:'y'});});
    var p=d.panels||{};
    if(showInd){
      traces.push({type:'scatter',mode:'lines',x:x,y:p.rsi,name:'RSI14',line:{width:1,color:'#ce93d8'},xaxis:'x',yaxis:'y2'});
      traces.push({type:'bar',x:x,y:p.macd_hist,name:'MACD hist',marker:{color:(p.macd_hist||[]).map(function(v){return v>=0?'rgba(38,166,154,.6)':'rgba(239,83,80,.6)';})},xaxis:'x',yaxis:'y3'});
      traces.push({type:'scatter',mode:'lines',x:x,y:p.macd_line,name:'MACD',line:{width:1,color:'#4fc3f7'},xaxis:'x',yaxis:'y3'});
      traces.push({type:'scatter',mode:'lines',x:x,y:p.macd_signal,name:'Sinyal',line:{width:1,color:'#ffb74d'},xaxis:'x',yaxis:'y3'});}
    var shapes=[],ann=[];elIndex=[];
    function hl(y,color,dash,label,xa){if(y==null)return;shapes.push({type:'line',xref:'x',yref:'y',x0:xa||x0,x1:x1,y0:y,y1:y,line:{color:color,width:1,dash:dash}});ann.push({xref:'paper',x:0.995,yref:'y',y:y,text:label,showarrow:false,font:{size:10,color:color},xanchor:'right',bgcolor:'rgba(14,17,22,.8)',borderpad:1});}
    function mk(e,xs,ys,sym,color,size,name){var idx=elIndex.length;elIndex.push(e);traces.push({type:'scatter',mode:'markers',x:xs,y:ys,name:name||e.label_tr,marker:{symbol:sym,color:color,size:size||9,line:{width:1,color:'#0e1116'}},
      text:xs.map(function(){return e.label_tr+'<br>'+(IMPACT[e.decision_impact]||['?'])[0];}),hoverinfo:'text',customdata:xs.map(function(){return idx;}),showlegend:false,xaxis:'x',yaxis:'y'});}
    function segline(e,t0,y0,t1,y1,color,dash,width){shapes.push({type:'line',xref:'x',yref:'y',x0:new Date(t0),x1:new Date(t1),y0:y0,y1:y1,line:{color:color,width:width||1.4,dash:dash||'solid'}});mk(e,[new Date(t1)],[y1],'circle',color,6);}
    var A=d.analysis||{};var els=(A.elements||[]).concat(((d.structure||{}).elements)||[]);
    els.forEach(function(e){
      if(!layerOn(e.layer))return;
      var imc=(IMPACT[e.decision_impact]||['','#b0bec5'])[1];
      if(e.layer==='zones'){var c=e.kind==='support'?'rgba(165,214,167,.18)':(e.kind==='resistance'?'rgba(239,154,154,.18)':'rgba(176,190,197,.16)');
        shapes.push({type:'rect',xref:'x',yref:'y',x0:e.t0?new Date(e.t0):x0,x1:x1,y0:e.lower,y1:e.upper,fillcolor:c,line:{width:0}});
        mk(e,(e.anchors||[]).map(function(a){return new Date(a.timestamp);}),(e.anchors||[]).map(function(a){return a.price;}),'diamond',e.kind==='support'?'#a5d6a7':(e.kind==='resistance'?'#ef9a9a':'#b0bec5'),8);
        ann.push({xref:'paper',x:0.005,yref:'y',y:e.price,text:e.label_tr,showarrow:false,font:{size:9,color:'#b0bec5'},xanchor:'left',bgcolor:'rgba(14,17,22,.6)'});}
      else if(e.layer==='levels'){var pend=e.kind==='pivot_pending';mk(e,[new Date(e.t0)],[e.price],e.kind==='pivot_high'?'triangle-down':(e.kind==='pivot_low'?'triangle-up':'circle-open'),pend?'#ffb74d':'#90caf9',pend?10:7);}
      else if(e.layer==='trend'){var col=e.status==='BROKEN'?'#ef5350':'#90caf9';segline(e,e.t0,e.y0,e.t1,e.y1,col,e.status==='BROKEN'?'dot':'solid',1.4);
        (e.breaks||[]).slice(0,1).forEach(function(b){mk(e,[new Date(b.timestamp)],[b.close],'x','#ef5350',9,'ihlal');});}
      else if(e.layer==='patterns'){if(e.t0!=null&&e.y0!=null)segline(e,e.t0,e.y0,e.t1,e.y1,imc==='#26a69a'?'#26a69a':'#ce93d8','dash',1.4);
        mk(e,(e.anchors||[]).map(function(a){return new Date(a.timestamp);}),(e.anchors||[]).map(function(a){return a.price;}),'square','#ce93d8',7);
        if(e.break_at!=null)mk(e,[new Date(e.break_at)],[e.break_close],'star','#ffd54f',10,'kırılış');}
      else if(e.layer==='trades'){
        if(e.kind==='trade_entry'){mk(e,[new Date(e.t0)],[e.price],e.side==='SHORT'?'triangle-down':'triangle-up',e.side==='SHORT'?'#ef5350':'#26a69a',12);}
        else if(e.kind==='trade_exit'){mk(e,[new Date(e.t0)],[e.price],'x',(e.net_pnl||0)>=0?'#26a69a':'#ef5350',11);}
        else if(e.kind==='entry'){hl(e.price,'#ffffff','solid','GERÇEK GİRİŞ '+fmt(e.price),e.t0?new Date(e.t0):null);mk(e,[e.t0?new Date(e.t0):x1],[e.price],'circle','#ffffff',7);}
        else if(e.kind==='stop'){hl(e.price,'#ef5350','solid','STOP '+fmt(e.price),e.t0?new Date(e.t0):null);mk(e,[x1],[e.price],'line-ew','#ef5350',8);}
        else if(e.kind==='target'){hl(e.price,'#26a69a','dash',e.label_tr);mk(e,[x1],[e.price],'line-ew','#26a69a',8);}
        else if(e.kind==='no_target'){ann.push({xref:'paper',x:0.995,yref:'paper',y:0.985,text:'TP YOK — çıkış kural/stop',showarrow:false,font:{size:11,color:'#ffb74d'},xanchor:'right',bgcolor:'rgba(14,17,22,.8)'});elIndex.push(e);}
        else if(e.kind==='liq'){hl(e.price,'#ff7043','dashdot','LIQ '+fmt(e.price));}
        else if(e.kind==='mark'){var ps=e.price_source||{};var pl=(ps.kind==='candle_close'?'SON MUM ':'İŞARET ')+fmt(e.price)+(e.unrealized_pnl_gross!=null?' · K/Z '+(e.unrealized_pnl_gross>=0?'+':'')+fmt(e.unrealized_pnl_gross):'');hl(e.price,'#e0e0e0','dot',pl,x[Math.max(0,x.length-8)]);mk(e,[x1],[e.price],'circle-open','#e0e0e0',7);}
        else if(e.kind==='plan_entry'){hl(e.price,'#b3e5fc','dash','PLAN GİRİŞ '+fmt(e.price),x[Math.max(0,x.length-30)]);mk(e,[x1],[e.price],'diamond-open','#b3e5fc',8);}
        else if(e.kind==='plan_stop'){hl(e.price,'#ef9a9a','dash','PLAN STOP '+fmt(e.price),x[Math.max(0,x.length-30)]);mk(e,[x1],[e.price],'diamond-open','#ef9a9a',8);}
        else if(e.kind==='plan_target'){hl(e.price,'#a5d6a7','dot',e.label_tr,x[Math.max(0,x.length-30)]);mk(e,[x1],[e.price],'diamond-open','#a5d6a7',8);}}
      else if(e.layer==='structure'){var sc2='#ffd54f';
        if(e.kind==='structure_geometry'){segline(e,e.t0,e.y0,e.t1!=null?e.t1:e.t0,e.y1!=null?e.y1:e.y0,sc2,'solid',1.6);}
        else if(e.kind==='structure_anchors'){mk(e,(e.anchors||[]).map(function(a){return new Date(a.timestamp);}),(e.anchors||[]).map(function(a){return a.price;}),'star-diamond',sc2,9);}
        else if(e.kind==='structure_trigger'){hl(e.price,'#4fc3f7','dash',e.label_tr,e.t0?new Date(e.t0):null);mk(e,[x1],[e.price],'line-ew','#4fc3f7',8);}
        else if(e.kind==='structure_invalidation'){hl(e.price,'#ff8a65','dot',e.label_tr,e.t0?new Date(e.t0):null);mk(e,[x1],[e.price],'line-ew','#ff8a65',8);}
        else if(e.kind==='structure_stop'){hl(e.price,'#ef5350','dashdot',e.label_tr,e.t0?new Date(e.t0):null);mk(e,[x1],[e.price],'line-ew','#ef5350',8);}
        else if(e.kind==='structure_target'){hl(e.price,'#66bb6a','dashdot',e.label_tr,e.t0?new Date(e.t0):null);mk(e,[x1],[e.price],'line-ew','#66bb6a',8);}
        else if(e.kind==='structure_confirm'){mk(e,[new Date(e.t0)],[e.price],'star',sc2,12,'teyit kapanışı');}}
      else if(e.layer==='indicators'){if(e.kind==='rule_reference'){if(e.t0!=null&&e.t1!=null){segline(e,e.t0,e.price,e.t1,e.price,'#ffd54f','dashdot',1.2);ann.push({xref:'x',x:new Date(e.t1),yref:'y',y:e.price,text:e.label_tr,showarrow:false,font:{size:9,color:'#ffd54f'},yshift:10});}
        else{hl(e.price,'#ffd54f','dashdot',e.label_tr);mk(e,[x1],[e.price],'line-ew','#ffd54f',8);}}}
    });
    var dom=showInd?{y:[0.42,1],y2:[0.22,0.40],y3:[0,0.20]}:{y:[0,1],y2:[0,0.001],y3:[0,0.001]};var mobile=window.innerWidth<600;
    var layout={paper_bgcolor:'#0e1116',plot_bgcolor:'#0e1116',font:{color:'#d7dde5',size:mobile?10:11},margin:{l:mobile?34:48,r:mobile?46:64,t:10,b:30},showlegend:!mobile,legend:{orientation:'h',y:1.02,font:{size:10}},dragmode:'pan',hovermode:'closest',
      xaxis:{rangeslider:{visible:false},gridcolor:'#1c2430',type:'date'},
      yaxis:{domain:dom.y,gridcolor:'#1c2430',side:'right'},
      yaxis5:{domain:dom.y,overlaying:'y',side:'left',showgrid:false,range:[0,Math.max.apply(null,d.v.filter(function(v){return v!=null;}).concat([1]))*4],showticklabels:false},
      yaxis2:{domain:dom.y2,gridcolor:'#1c2430',range:[0,100],side:'right',tickvals:[30,50,70],visible:showInd},
      yaxis3:{domain:dom.y3,gridcolor:'#1c2430',side:'right',visible:showInd},
      shapes:shapes,annotations:ann};
    Plotly.react('chart',traces,layout,{responsive:true,displaylogo:false,scrollZoom:true,modeBarButtonsToRemove:['lasso2d','select2d']});
    var gd=document.getElementById('chart');gd.removeAllListeners&&gd.removeAllListeners('plotly_click');
    gd.on&&gd.on('plotly_click',function(ev){var pt=ev.points&&ev.points[0];if(pt&&pt.customdata!==undefined)detail(elIndex[pt.customdata]);});
  }
  function fileStem(){var d=last||{};var A=d.analysis||{};var a=A.identity||{};var sc=scope();
    var mk=MKT[a.market_type]||sc.market;var stored=!!(A.analysis_id&&(d.analysis_stored||d.historical));
    var stamp=String(a.as_of||(d.live&&d.live.as_of)||'').replace(/[^0-9]/g,'').slice(0,12);
    return base+'_'+(a.timeframe||sc.tf)+'_'+mk+'_'+(a.book_id||sc.book)+'_'+(stored?A.analysis_id:('canli-'+(stamp||'x')));}
  ['tf','mk','bk'].forEach(function(id){var el=document.getElementById(id);if(el)el.addEventListener('change',function(){
    if(id!=='tf'){window.__chartTrade='';window.__chartAsOf='';}   // baska defter/piyasa: secili islem kimligi TASINMAZ
    resetHistory();var sc=scope();loadHistory(sc,'');load(sc);});});
  ['nbars','hist'].forEach(function(id){var el=document.getElementById(id);if(el)el.addEventListener('change',function(){load(scope());});});
  document.getElementById('reload').addEventListener('click',function(){var sc=scope();loadHistory(sc,sc.aid);load(sc);});
  var png=document.getElementById('dl-png');if(png)png.addEventListener('click',function(){Plotly.downloadImage('chart',{format:'png',width:1400,height:800,filename:fileStem()});});
  var js=document.getElementById('dl-json');if(js)js.addEventListener('click',function(){var d=last||{};var A=d.analysis||{};
    if(A.analysis_id&&(d.analysis_stored||d.historical)){window.open('/api/chart/'+base+'/snapshot/'+A.analysis_id+(tokenQs||''),'_blank');}
    else if(last){var blob=new Blob([JSON.stringify(A,null,1)],{type:'application/json'});var u=URL.createObjectURL(blob);var l=document.createElement('a');l.href=u;l.download=fileStem()+'.json';document.body.appendChild(l);l.click();setTimeout(function(){URL.revokeObjectURL(u);l.remove();},500);}});
  window.__onState=function(s){if(s&&s.changed&&(s.changed.indexOf('coin_heads')>=0||s.changed.indexOf('futures_ledger')>=0||s.changed.indexOf('spot_ledger')>=0||s.changed.indexOf('strategy_paper')>=0||s.changed.indexOf('pattern_trader')>=0||s.changed.indexOf('portfolio')>=0||s.changed.indexOf('risk')>=0)){var sc=scope();if(!sc.aid)load(sc);}};
  // TERMINAL PANELI (2026-09-16): acik islem satirina tiklaninca AYNI ekranda coin/defter/piyasa degistirilir.
  // Sayfa yenilenmez, kullanicinin katman/dilim secimi KORUNUR; kapsam degistigi icin gecmis secimi sifirlanir
  // ve gec gelen eski yanit (seq + kapsam kontrolu) yeni grafigi EZEMEZ.
  window.__chartSelect=function(b,market,book,tf,trade,asof){
    if(trade!==undefined){window.__chartTrade=trade||'';window.__chartAsOf=asof||'';}
    if(b){base=String(b).toUpperCase();window.__chartBase=base;}
    if(market){var mk=document.getElementById('mk');if(mk&&mk.value!==market)mk.value=market;}
    if(book){var bk=document.getElementById('bk');if(bk&&bk.value!==book)bk.value=book;}
    if(tf){var tfe=document.getElementById('tf');if(tfe&&tfe.value!==tf)tfe.value=tf;}
    var t=document.getElementById('charttitle');if(t)t.textContent=base;
    resetHistory();var sc=scope();loadHistory(sc,'');load(sc);
    try{var u=new URL(window.location.href);u.searchParams.set('coin',base);history.replaceState(null,'',u.toString());}catch(e){}
  };
  window.__chartTest={scope:scope,fileStem:fileStem,last:function(){return last;},select:window.__chartSelect};
  if(typeof Plotly==='undefined'){document.getElementById('chart').innerHTML='<div class=card>plotly.min.js yüklenemedi (plotly paketi kurulu değil?)</div>';}else{var sc0=scope();loadHistory(sc0,'');load(sc0);}
})();
"""

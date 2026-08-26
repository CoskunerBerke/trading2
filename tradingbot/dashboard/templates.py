"""HTML şablonları (jinja2 bağımlılığı yok — saf Python). Mobil-öncelikli koyu tema, satır içi CSS/JS, CDN yok."""
from __future__ import annotations

import html
import json
import math
from typing import Any, Iterable

NAV: list[tuple[str, str]] = [
    ("/", "Genel"), ("/scanner", "Tarayıcı"), ("/portfolio/futures", "Futures"), ("/portfolio/spot", "Spot"),
    ("/orders", "Emirler"), ("/trades", "İşlemler"), ("/risk", "Risk"), ("/learning", "Öğrenme"), ("/quant", "Quant"), ("/backtest", "Backtest"),
    ("/models", "Modeller"), ("/llm", "LLM"), ("/health", "Sağlık"),
]

# Açık pozisyon tablosunun sticky sütun sınıfı — TEK KAYNAK.
# Sunucu render'ı (`table(..., cls=POS_TABLE_CLS)`) ve polling JS'i AYNI sabiti kullanır; ikisi
# ayrı yazıldığında polling tabloyu sınıfsız kuruyor ve sticky sütunlar sessizce kayboluyordu.
POS_TABLE_CLS = "pos"

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
/* Yatay taşma SAYFAYA değil, tablonun KENDİ kapsayıcısına aittir. */
html,body{max-width:100%;overflow-x:hidden}
main{max-width:100%}
.tw{max-width:100%}
/* Geniş açık-pozisyon tablosunda ilk üç sütun (Sembol · Piyasa · Yön) sabit kalır.
   SÜTUN GENİŞLİKLERİ SABİTLENİR: `left` değerleri ancak genişlikler kesin olduğunda doğrudur.
   Önce genişlik serbestti; `1000000BONKDOWN/USDT` gibi uzun sembolde 1. sütun 192px'e çıkıyor,
   `left:96px`e sabitlenmiş 2. sütun onun ÜZERİNE biniyordu. Uzun sembol artık ellipsis ile
   kısaltılır; tam değer `title` içinde korunur. Toplam: 130 + 96 + 76 = 302px.
   (2. ve 3. sütun genişlikleri en uzun rozetlere göre seçildi: «FUTURES» ve «SHORT».) */
.tw table.pos td:nth-child(-n+3),.tw table.pos th:nth-child(-n+3){position:sticky;background:var(--panel);z-index:1;overflow:hidden;text-overflow:ellipsis}
.tw table.pos td:nth-child(1),.tw table.pos th:nth-child(1){left:0;width:130px;min-width:130px;max-width:130px}
.tw table.pos td:nth-child(2),.tw table.pos th:nth-child(2){left:130px;width:96px;min-width:96px;max-width:96px}
.tw table.pos td:nth-child(3),.tw table.pos th:nth-child(3){left:226px;width:76px;min-width:76px;max-width:76px}
.tw table.pos th:nth-child(-n+3){z-index:2}
/* Uzun damga/etiket/alan adı kartı taşırmaz. Altyazılar `exposure.max_total_open_risk_usdt`
   gibi bölünemeyen uzun tanımlayıcılar içerebilir → `anywhere` altyazıya da gerekli. */
.card .v,.card .small{overflow-wrap:anywhere}
@media(max-width:900px){.tw table.pos td:nth-child(-n+3),.tw table.pos th:nth-child(-n+3){position:static}}
@media(max-width:600px){main{padding:6px}h1{font-size:17px}.card .v{font-size:17px}table{font-size:12px}
  .grid{grid-template-columns:1fr}}      /* mobilde kartlar okunabilir sırayla alt alta */
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
    if not math.isfinite(f):                    # NaN / ±inf → "-" (sahte sayı yok)
        return "-"
    if abs(f) >= 1000:
        return f"{f:,.2f}"
    if abs(f) >= 1:
        return f"{f:,.{nd}f}".rstrip("0").rstrip(".") if nd else f"{f:.0f}"
    return f"{f:.6g}"


def pct(x: Any, nd: int = 2) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "-"
    return f"{f:+.{nd}f}%" if math.isfinite(f) else "-"   # "+inf%" / "+nan%" YAZILMAZ


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


def verdict_kind(v: Any) -> str:
    """Karar rozetinin RENK SINIFI — TEK KAYNAK (sunucu render'ı ve polling JS'i aynı haritayı kullanır)."""
    v = str(v or "")
    if v in ("SPOT_LONG", "FUTURES_LONG", "LONG"):
        return "ok"
    if v in ("FUTURES_SHORT", "SHORT", "EXIT", "RISK_BLOCKED"):
        return "bad"
    return "info"


def verdict_badge(v: str) -> str:
    return badge(str(v or "") or "-", verdict_kind(v))


def money_html_text(txt: Any) -> str:
    """ZATEN BİÇİMLENMİŞ para metnini `<td>`ye sarar; renk `+/-` işaretinden gelir.

    Polling JS'i `buildHeadsTable` içinde AYNI kuralı uygular → iki yüzey aynı sınıfı üretir.
    """
    t = "" if txt is None else str(txt)
    cls = "up" if t.startswith("+") else ("dn" if t.startswith("-") else ("" if t in ("—", "", "-") else "flat"))
    return f'<td class="num {cls}">{esc(t)}</td>'


HEADS_TABLE_CLS = "heads"        # polling JS'i AYNI sabiti ENJEKTE ederek kullanır


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


SAMPLE_BANDS = ((30, "Yetersiz örneklem — performans sonucu kesin değildir", "warn-box"),
                (50, "Sınırlı örneklem — sonuçlar yönlendirici, kesin değil", "warn-box"),
                (None, "Değerlendirilebilir örneklem", ""))


def sample_banner(n_closed: int) -> str:
    """Örneklem durumu — YALNIZ UI açıklamasıdır; algoritmayı ve işlem kararını DEĞİŞTİRMEZ."""
    for limit, text, cls in SAMPLE_BANDS:
        if limit is None or n_closed < limit:
            return f'<div class="card {cls}">Kapanmış işlem: <b>{int(n_closed)}</b> — {esc(text)}</div>'
    return ""


def weight_table(weights: dict, label: str = "Alan") -> str:
    """Ağırlık tablosu: pozitif/negatif RENKLE ayrılır, anlamlı ondalıkla gösterilir.

    4–6 haneli ham ondalık yerine büyüklüğe göre 2–4 hane; işaret her zaman yazılır.
    """
    def _w(v: Any) -> str:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return f'<td>{esc(v)}</td>'
        nd = 2 if abs(f) >= 1 else (3 if abs(f) >= 0.1 else 4)
        cls = "up" if f > 0 else ("dn" if f < 0 else "flat")
        return f'<td class="num {cls}">{f:+.{nd}f}</td>'

    rows = [[esc(TR_FIELDS.get(k, k)), _w(v)] for k, v in weights.items()]
    return table([label, "Ağırlık"], rows, num_cols={1}, empty="ağırlık yok")


# Ham iç alan adı → operatörün anlayacağı Türkçe karşılık.
TR_FIELDS = {"n": "İşlem", "n_trades": "İşlem", "wins": "Kazanan", "n_wins": "Kazanan",
             "losses": "Kaybeden", "sum_r": "Toplam R", "avg_r": "Ortalama R",
             "mae": "Maksimum ters hareket", "mfe": "Maksimum olumlu hareket",
             "exit": "Çıkış nedeni", "bars": "Süre (bar)", "pnl": "Net K/Z", "r": "R sonucu",
             "won": "Sonuç", "why": "Öğrenilen ders", "at": "Tarih", "setup": "Setup",
             "side": "Yön", "symbol": "Sembol", "id": "İşlem ID"}


def lessons_table(lessons: list[dict]) -> str:
    """Dersler — okunur sütunlar. Uzun «why» metni hücreyi büyütmez: kısaltılmış önizleme +
    tam metin `title` (tooltip) ve `<details>` içinde korunur."""
    from ..pnl import fmt_money
    rows = []
    for x in lessons:
        why = x.get("why")
        why_txt = " ".join(why) if isinstance(why, list) else str(why or "")
        preview = (why_txt[:90] + "…") if len(why_txt) > 90 else why_txt
        why_cell = (f'<details><summary title="{esc(why_txt)}">{esc(preview) or "—"}</summary>'
                    f'<div class="small mut">{esc(why_txt)}</div></details>') if why_txt else "—"
        won = x.get("won")
        res = badge("KAZANDI", "ok") if won is True else (badge("KAYBETTİ", "bad") if won is False else "—")
        r = x.get("r")
        rows.append([esc(x.get("id") or "—"), esc(x.get("symbol") or "—"), esc(x.get("side") or "—"),
                     money_html(x.get("pnl")),
                     f'<td class="num">{float(r):+.3f}R</td>'
                     if isinstance(r, (int, float)) and not isinstance(r, bool) and math.isfinite(float(r))
                     else "<td>—</td>",
                     res, esc(x.get("exit") or "—"), fmt(x.get("bars"), 0),
                     pct(x.get("mae")), pct(x.get("mfe")), why_cell,
                     f'<span title="{esc(x.get("at") or "")}">{esc(fmt_utc(x.get("at")))}</span>',
                     esc(x.get("setup") or "—")])
    return table(["İşlem ID", "Sembol", "Yön", "Net K/Z", "R sonucu", "Sonuç", "Çıkış nedeni",
                  "Süre (bar)", "MAE", "MFE", "Öğrenilen ders", "Tarih", "Setup"],
                 rows, num_cols={3, 4, 7, 8, 9}, empty="ders kaydı yok")


def live_bar(fr: dict | None) -> str:
    """CANLI/BAYAT göstergesi. Fiyat yaşı ile STRATEJİ TURU yaşı AYRI gösterilir."""
    if not fr:
        return ""
    tz = esc(fr.get("tz_label") or "UTC")
    pstate = fr.get("price_state")
    dot = {"live": "🟢", "stale": "🔴"}.get(pstate, "⚪")
    label = {"live": "CANLI", "stale": "FİYAT VERİSİ GÜNCEL DEĞİL"}.get(pstate, "FİYAT DURUMU BİLİNMİYOR")
    warn = ' warn-box' if pstate != "live" else ""
    # Fiyat tazeliği ile worker sağlığı AYRI kavramlardır: strateji turu taze olsa bile fiyat
    # bayatsa gösterge KIRMIZI kalır. Eşik yükseltilerek sorun gizlenmez.
    note = ('<div class="small" id="stalenote">Strateji çalışıyor; ancak pozisyon fiyatları '
            'belirtilen süredir güncellenmedi.</div>') if pstate == "stale" else \
           '<div class="small" id="stalenote" style="display:none">Strateji çalışıyor; ancak pozisyon fiyatları belirtilen süredir güncellenmedi.</div>'
    return (f'<div class="card live{warn}" id="livebar" data-tz="{tz}">'
            f'<span id="livedot">{dot}</span> <b id="livelabel">{esc(label)}</b> — '
            f'Son fiyat güncellemesi: <span id="priceage">{esc(age_text(fr.get("price_age_s")))}</span> önce · '
            f'Son strateji turu: <span id="runage">{esc(age_text(fr.get("run_age_s")))}</span> önce · '
            f'Son coin-head kararı: <span id="headsage">{esc(age_text(fr.get("heads_age_s")))}</span> önce · '
            f'saat dilimi {tz}{note}</div>')


def chief_block(cv) -> str:
    """Baş yönetici — UZUN HAM JSON YERİNE etiketli kartlar.

    «İşlem adayı» ile «açık pozisyon» KASITLI olarak ayrı kartlardadır: `breadth.long` son turdaki
    LONG *aday* sayısıdır, açık LONG *pozisyon* sayısı defterden gelir.
    """
    from ..pnl import fmt_money

    def _pct(x, nd=1):
        return f"%{float(x):.{nd}f}" if x is not None else "Veri yok"

    def _usdt(x):
        return (fmt_money(x, signed=False, currency="") + " USDT") if x is not None else "Veri yok"

    def _basis(c):
        """Risk bütçesinin EQUITY TABANI — kartta açıkça yazılır (motor `starting_equity`
        kullanırken panel canlı equity gösterirse aynı büyüklük iki farklı sayı olur)."""
        if getattr(c, "risk_equity_basis_usdt", None) is None:
            return ""
        label = {"starting_equity": "Başlangıç özkaynağı tabanı",
                 "live_equity": "Canlı özkaynak tabanı"}.get(
                     str(getattr(c, "risk_equity_basis_kind", "") or ""), "Özkaynak tabanı")
        return " · %s: %s USDT" % (label, fmt_money(c.risk_equity_basis_usdt, signed=False, currency=""))

    def _spot_no_stop(c):
        """Stopsuz spot AÇIKÇA yazılır — stop alanı boşken 'risk azaldı' izlenimi verilmez."""
        out = ""
        if getattr(c, "spot_exposure_unknown", False):
            bad = list(getattr(c, "spot_symbols_unknown_price", None) or [])
            out += (" · ⚠ fiyat geçersiz (" + esc(", ".join(str(x) for x in bad[:3]))
                    + ") — maruziyet ölçülemedi, yeni spot giriş reddedilir")
        syms = list(getattr(c, "spot_symbols_without_stop", None) or [])
        if syms:
            out += " · stopsuz (stopla sınırlanmamış): " + esc(", ".join(str(x) for x in syms[:4]))
        return out

    def _risk_age(c):
        """Risk anlık görüntüsünün yaşı — fiyat tazeliğinden AYRI etiketlenir."""
        st = getattr(c, "risk_snapshot_state", None)
        if st == "stale":
            return " · ⚠ Risk verisi güncel değil (%s önce)" % age_text(c.risk_snapshot_age_s)
        if st == "unknown" or st is None:
            return " · ⚠ Risk verisi yaşı bilinmiyor"
        return " · risk verisi %s önce" % age_text(c.risk_snapshot_age_s)

    # Uzun ISO damgası kartı taşırıyordu; insan okunur biçim + ham değer tooltip'te.
    gen = f'<span title="{esc(cv.generated_at or "")}">{esc(fmt_utc(cv.generated_at))}</span>'
    c = [card("Karar üretim zamanı", gen),
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
         # AÇIK RİSK ARTIK İKİ AYRI KAVRAM — aynı kartta karıştırılmaz:
         card("Açık stop riski", _usdt(cv.open_stop_risk_usdt),
              "pozisyonların stop'a kadar BRÜT tahmini kaybı (ücret hariç)"),
         card("Risk motoru rezervasyonu", _usdt(cv.open_risk_usdt),
              "risk.json → total_open_risk_usdt (spot+futures BİRLEŞİK toplam)" + _risk_age(cv)),
         # SPOT NOTIONAL ile FUTURES STOP RISKI AYNI KARTTA TOPLANMAZ — kabul kapısı futures
         # kovasını kullanır; spot kendi allocation kapısıyla korunur.
         card("Futures stop riski", _usdt(cv.futures_stop_risk_usdt),
              "yalnız futures pozisyonları — kabul kapısının kovası" + _risk_age(cv)),
         card("Futures bütçe kullanımı", _pct(cv.futures_risk_budget_util_pct),
              "futures stop riski / azami risk bütçesi" + _risk_age(cv)),
         card("Spot maruziyeti", _usdt(cv.spot_exposure_usdt),
              "açık spot notional — RİSK DEĞİL" + _spot_no_stop(cv) + _risk_age(cv)),
         card("Spot allocation kullanımı", _pct(cv.spot_allocation_util_pct),
              "spot notional / spot tavanı (ayrı kapı)" + _risk_age(cv)),
         card("Risk bütçesi kullanımı", _pct(cv.risk_budget_util_pct),
              (("azami " + fmt_money(cv.risk_budget_max_usdt, signed=False, currency="") + " USDT"
                + _basis(cv)) if cv.risk_budget_max_usdt is not None
               else "azami bütçe bilinmiyor — equity tabanı yayımlanmamış") + _risk_age(cv)),
         card("Teminat kullanımı", _pct(cv.margin_util_pct), "açık teminat / futures özkaynak"),
         card("Günlük gerçekleşen net K/Z", fmt_money(cv.realized_today)),
         card("Günlük gerçekleşmemiş net K/Z", fmt_money(cv.unrealized_open)),
         card("Drawdown", _pct(cv.drawdown_pct, 2), "risk motoru (risk.json)")]
    return "<h2>Baş yönetici</h2>" + f'<div class="grid">{"".join(c)}</div>'


def live_script(cfg) -> str:
    """Hafif POLLING. WebSocket yok; tarayıcı borsaya bağlanmaz; istek fırtınası engellenir.

    * Aynı anda tek istek (overlap koruması), `AbortController` ile zaman aşımı.
    * Arka plan sekmesinde aralık `background_backoff_mult` katına çıkar.
    * Bağlantı koparsa CANLI etiketi YEŞİL KALMAZ.
    """
    from .views import POSITION_NUM_COLS, POSITION_PNL_COLS   # hizalama sözleşmesi TEK kaynak
    pos = int(getattr(cfg, "poll_positions_s", 7)) * 1000
    summ = int(getattr(cfg, "poll_portfolio_s", 20)) * 1000
    heal = int(getattr(cfg, "poll_health_s", 12)) * 1000
    mult = int(getattr(cfg, "background_backoff_mult", 4))
    stale = int(getattr(cfg, "stale_price_s", 90))
    # Coin head tablosu portfoy karti temposuyla yenilenir (pozisyon acilis/kapanisini yakalar).
    heads_ms = int(getattr(cfg, "poll_heads_s", getattr(cfg, "poll_portfolio_s", 20))) * 1000
    return r"""<script>
/* TABLO MARKUP SÖZLEŞMESİ — sunucu render'ı (`templates.table` + `app._positions_table`) ile
   AYNI olmak ZORUNDA: `.tw` sarmalayıcı, `<table class="__TCLS__">`, `NUM` sütunlarında sağa
   hizalama, YALNIZ `PNL` sütunlarında up/dn/flat rengi, ilk üç sütunda tam değer `title`'da.
   Sütun listeleri Python'daki `views.POSITION_NUM_COLS / POSITION_PNL_COLS`tan ENJEKTE edilir;
   burada `i>=3` gibi ayrı bir kural YOKTUR (önce vardı: «Açılış» ve «İşlem ID» metin sütunları
   polling'den sonra sağa yaslanıyordu). Fonksiyon `document`a dokunmaz → testte node ile çalışır. */
function buildPosTable(d,NUM,PNL){
  function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
  var h='<table class="__TCLS__"><thead><tr>'+d.columns.map(function(c,i){
    return '<th class="'+(NUM.indexOf(i)>=0?'num':'')+'">'+esc(String(c))+'</th>';}).join('')+'</tr></thead><tbody>';
  if(!d.rows.length){h+='<tr><td colspan="'+d.columns.length+'" class="mut">açık pozisyon yok</td></tr>';}
  d.rows.forEach(function(r){h+='<tr>'+r.map(function(c,i){
    var s=String(c==null?'—':c);var cls=(NUM.indexOf(i)>=0)?'num':'';
    if(PNL.indexOf(i)>=0){var ch=s.charAt(0);
      cls+=(ch==='+')?' up':(ch==='-')?' dn':(s==='—'?'':' flat');}   /* sunucu `money_html` ile aynı */
    var attr=(i<3)?' title="'+esc(s)+'"':'';                                 /* sabit genişlik + ellipsis */
    return '<td class="'+cls+'"'+attr+'>'+esc(s)+'</td>';}).join('')+'</tr>';});
  return '<div class="tw">'+h+'</tbody></table></div>';
}
/* COIN HEAD TABLOSU — sunucu render'i (`app._heads_table`) ile AYNI markup sozlesmesi:
   `.tw` sarmalayici, `<table class="__HCLS__">`, NUM sutunlarinda sag hizalama, PNL sutunlarinda
   +/- rengi, BADGE sutunlarinda `<span class="badge b-KIND">`, sembol sutununda `/coin/<base>`
   baglantisi. IS KURALI BURADA YOK: satirlar/meta sunucudaki `views.coin_head_table`tan gelir. */
function buildHeadsTable(d){
  function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
  var NUM=d.num_cols||[],PNL=d.pnl_cols||[],BADGE=d.badge_cols||[],SC=(d.symbol_col==null?0:d.symbol_col);
  var h='<table class="__HCLS__"><thead><tr>'+d.columns.map(function(c,i){
    return '<th class="'+(NUM.indexOf(i)>=0?'num':'')+'">'+esc(c)+'</th>';}).join('')+'</tr></thead><tbody>';
  if(!d.rows.length){return '<div class="card mut">'+esc(d.empty_text||'kayit yok')+'</div>';}
  d.rows.forEach(function(r,ri){
    var m=(d.meta&&d.meta[ri])||{};
    h+='<tr>'+r.map(function(c,i){
      var s=String(c==null?'':c);
      if(i===SC){return '<td class="">'+'<a href="/coin/'+esc(s.split('/')[0])+'">'+esc(s)+'</a>'+'</td>';}
      if(BADGE.indexOf(i)>=0){
        var kind;
        if(i===2){kind=m.status_kind||'info';}
        else if(m.no_decision){kind='warn';}
        else{kind=(s==='SPOT_LONG'||s==='FUTURES_LONG'||s==='LONG')?'ok':
                  ((s==='FUTURES_SHORT'||s==='SHORT'||s==='EXIT'||s==='RISK_BLOCKED')?'bad':'info');}
        return '<td class="">'+'<span class="badge b-'+esc(kind)+'">'+esc(s||'-')+'</span>'+'</td>';
      }
      if(PNL.indexOf(i)>=0){
        var cls=(s.charAt(0)==='+')?'up':((s.charAt(0)==='-')?'dn':((s==='—'||s===''||s==='-')?'':'flat'));
        return '<td class="num '+cls+'">'+esc(s)+'</td>';
      }
      return '<td class="'+(NUM.indexOf(i)>=0?'num':'')+'">'+esc(s)+'</td>';
    }).join('')+'</tr>';});
  return '<div class="tw">'+h+'</tbody></table></div>';
}
var NUMCOLS=__NUMCOLS__,PNLCOLS=__PNLCOLS__;
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
  /* Yeni fiyat gelince sayfa YENİLENMEDEN tekrar yeşile döner; açıklama notu da gizlenir. */
  var n=document.getElementById('stalenote');if(n){n.style.display=(st==='stale')?'':'none';}
  var p=document.getElementById('priceage');if(p&&fr)p.textContent=agetxt(fr.price_age_s);
  var r=document.getElementById('runage');if(r&&fr)r.textContent=agetxt(fr.run_age_s);
  var h=document.getElementById('headsage');if(h&&fr)h.textContent=agetxt(fr.heads_age_s);
 }
 function poll(key,url,cb,base,onerr){
  function tick(){
   if(busy[key])return;                                   /* overlap koruması: istek fırtınası yok */
   busy[key]=1;
   var ac=('AbortController' in window)?new AbortController():null;
   var to=setTimeout(function(){if(ac)ac.abort();},Math.max(5000,base));
   fetch(url,{headers:window.__authHeaders||{},signal:ac?ac.signal:undefined})
    .then(function(r){return r.ok?r.json():Promise.reject(r.status);})
    .then(function(d){fails=0;cb(d);})
    .catch(function(){fails++;if(fails>2)setLive('off',null);if(onerr){try{onerr();}catch(e){}}})
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
     el.innerHTML=buildPosTable(d,NUMCOLS,PNLCOLS);
   }
 },__POS__);
 poll('sum','/api/live/summary',function(d){
   /* Kartlar polling sonrası GERÇEKTEN güncellenir (eski kod boş callback kullanıyordu). */
   if(!d||!d.cards)return;
   d.cards.forEach(function(c){
     var el=document.getElementById('sc-'+c.key);if(!el)return;
     var v=el.querySelector('.v');if(!v)return;
     var signed=(c.kind==='money'||c.kind==='pct_signed');
     var cls='flat';
     if(signed&&c.value!=null){cls=(c.value>0)?'up':((c.value<0)?'dn':'flat');}
     v.innerHTML='<span class="'+cls+'">'+String(c.display).replace(/&/g,'&amp;')
       .replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</span>';
     var s=el.querySelector('.small');if(s&&c.sub!=null){s.textContent=c.sub;}
   });
 },__SUM__);
 /* COIN HEAD tablosu + kapsam sayaci CANLI guncellenir. Endpoint hata verirse MEVCUT TABLO
    SILINMEZ; yalniz stale uyarisi acilir ve bir sonraki basarili poll'da temizlenir. */
 poll('heads','/api/live/coin-heads',function(d){
   if(!d||!d.rows)return;
   var t=document.getElementById('headstbl');
   if(t){t.innerHTML=buildHeadsTable(d);}
   var cov=document.getElementById('headscov');
   if(cov){
     cov.textContent='Açık pozisyon kapsamı: '+d.open_positions_shown+' / '+d.open_positions_total;
     cov.className='badge '+(d.coverage_complete?'b-ok':'b-bad');
   }
   var ms=document.getElementById('headsmiss');
   if(ms){
     var miss=d.missing_open_symbols||[];
     ms.textContent=miss.length?(d.missing_text||('⚠ '+miss.join(', '))):'';
     ms.style.display=miss.length?'':'none';
   }
   var sn=document.getElementById('headsstale');
   if(sn){sn.style.display='none';}                      /* basarili poll stale uyarisini temizler */
 },__HEADS__,function(){
   var sn=document.getElementById('headsstale');
   if(sn){sn.style.display='';}                          /* tablo KORUNUR, yalniz uyari acilir */
 });
 poll('hp','/api/live/health',function(d){
   if(d&&d.price_age_s!=null&&d.price_age_s>__STALE__){setLive('on',{price_state:'stale',price_age_s:d.price_age_s,
     run_age_s:d.last_run_age_s,heads_age_s:null});}
 },__HEAL__);
})();
</script>""".replace("__POS__", str(pos)).replace("__SUM__", str(summ)).replace("__HEAL__", str(heal))             .replace("__MULT__", str(mult)).replace("__STALE__", str(stale)).replace("__TCLS__", POS_TABLE_CLS)             .replace("__HEADS__", str(heads_ms)).replace("__HCLS__", HEADS_TABLE_CLS)             .replace("__NUMCOLS__", json.dumps(list(POSITION_NUM_COLS))).replace("__PNLCOLS__", json.dumps(list(POSITION_PNL_COLS)))


def card(k: str, v: str, sub: str = "", cid: str = "") -> str:
    i = f' id="{esc(cid)}"' if cid else ""
    return (f'<div class="card"{i}><div class="k">{esc(k)}</div><div class="v">{v}</div>'
            + (f'<div class="small mut">{sub}</div>' if sub else "") + "</div>")


def card_value(c) -> str:
    """Kart değeri — `SummaryCard.display` OLDUĞU GİBİ basılır, yeniden biçimlendirilmez.

    Renk yalnız işaretli (para) kartlarda uygulanır; oran/sayaç kartları nötrdür. Renk TEK BAŞINA
    anlam taşımaz: `+/-` işareti zaten `display` içindedir.
    """
    from ..pnl import pnl_class
    cls = pnl_class(c.value) if (c.signed and c.value is not None) else "flat"
    return f'<span class="{cls}">{esc(c.display)}</span>'


def fmt_utc(iso_ts: Any, *, fallback: str = "—") -> str:
    """ISO zaman damgası → `22.08.2026 22:31:41 UTC` (insan okunur, karttan taşmaz).

    Ham ISO değeri `title` özniteliğinde korunur (tooltip); veri kaybı yoktur.
    """
    s = str(iso_ts or "").strip()
    if not s:
        return fallback
    try:
        from ..core import from_iso
        d = from_iso(s)
    except Exception:  # noqa: BLE001 — biçim bilinmiyorsa ham metin gösterilir
        return esc(s)
    return d.strftime("%d.%m.%Y %H:%M:%S UTC")


def table(headers: list[str], rows: Iterable[Iterable[str]], *, num_cols: set[int] | None = None,
          empty: str = "kayıt yok", cls: str = "") -> str:
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
    c = f' class="{esc(cls)}"' if cls else ""
    return f'<div class="tw"><table{c}><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


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


# --------------------------------------------------------------- öğrenme kalite blokları
#
# Bu dört blok SALT SUNUMDUR: hiçbir öğrenme/risk matematiği burada hesaplanmaz. Hepsi
# eksik/bozuk/stale/null/non-finite girdiye dayanıklıdır — hiçbir koşulda exception atmaz,
# çünkü `/learning` ve `/quant` sayfaları eski şemalı `learning.json` ile de 200 dönmelidir.

NOT_ENOUGH_DATA = "NOT ENOUGH DATA"
RESEARCH_ONLY = "RESEARCH ONLY"
ACTIVE_POLICY_UNCHANGED = "ACTIVE POLICY UNCHANGED"

#: Kanıt seviyesi → rozet türü. Bilinmeyen seviye nötr gösterilir.
_EVIDENCE_KIND = {"OBSERVATION": "", "RESEARCH_HYPOTHESIS": "warn",
                  "VALIDATED_POLICY_CANDIDATE": "ok", "APPLIED_BOUNDED": "ok",
                  "REJECTED": "bad", "RETIRED": ""}


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _num(x: Any, nd: int = 3, suffix: str = "") -> str:
    """Sonlu sayı → biçimli metin; değilse «Veri yok» (sessiz 0 YOK)."""
    from ..pnl import finite_float_or_none
    v = finite_float_or_none(x)
    return "Veri yok" if v is None else f"{v:.{nd}f}{suffix}"


def evidence_badge(level: Any) -> str:
    lv = str(level or "").upper()
    return badge(lv or "—", _EVIDENCE_KIND.get(lv, "")) if lv else "—"


def retention_block(ln: dict) -> str:
    """Ders saklama zinciri — 200'ün SAKLAMA SINIRI OLMADIĞINI açıkça yazar."""
    ret = _d(_d(ln).get("lesson_retention"))
    if not ret:
        return ""
    health = str(ret.get("archive_health") or "—")
    kind = {"OK": "ok", "EMPTY": "", "DEGRADED": "warn", "ARCHIVE_FAILED": "bad",
            "DISABLED": "warn"}.get(health, "")
    scopes = ", ".join(str(x) for x in (ret.get("retrieval_scopes") or [])) or "HOT"
    rows = [
        ["Sıcak pencere (ekran)", _num(ret.get("hot_window"), 0)],
        ["Sıcak dersler", _num(ret.get("hot_lessons"), 0)],
        ["Arşivlenmiş dersler", _num(ret.get("archived_lessons"), 0)],
        ["Ömür boyu ders", _num(ret.get("lifetime_lessons"), 0)],
        ["Segment", _num(ret.get("segments"), 0)],
        ["İndekslenmiş", _num(ret.get("indexed_lessons"), 0)],
        ["Toplam (aggregate) hücre", _num(ret.get("aggregate_cells"), 0)],
        ["Saklama politikası", esc(str(ret.get("retention_policy") or "—"))],
        ["Arşiv sağlığı", badge(health, kind)],
        ["Retrieval kapsamı", esc(scopes)],
        ["Taşmada ayrıntı siliniyor mu?",
         badge("HAYIR", "ok") if ret.get("deletes_detail_on_overflow") is False else badge("BİLİNMİYOR", "warn")],
    ]
    err = ret.get("last_archive_error") or ret.get("last_rotation_error")
    if err:
        rows.append(["Son arşiv hatası", f'<span class="bad">{esc(str(err)[:200])}</span>'])
    note = str(ret.get("note_tr") or "")
    return ("<h2>Ders saklama (kayıpsız)</h2>"
            + table(["Alan", "Değer"], rows, empty="saklama bilgisi yok")
            + f'<p class="mut small">{esc(note)} Arşiv yazılamazsa sıcak pencere BUDANMAZ — '
              'arşivsiz silme yasaktır.</p>')


def calibration_block(ln: dict) -> str:
    """Güvenilirlik kovaları — tek sonuç «model haklıydı/yanıldı» DEMEK DEĞİLDİR."""
    cal = _d(_d(ln).get("calibration"))
    if not cal:
        return ""
    buckets = [b for b in (cal.get("buckets") or []) if isinstance(b, dict)]
    rows = []
    for b in buckets:
        suf = badge("YETERLİ", "ok") if b.get("sufficient") else badge(NOT_ENOUGH_DATA, "warn")
        rows.append([esc(str(b.get("bucket") or "—")), _num(b.get("real_n"), 0),
                     _num(b.get("mean_predicted_p"), 3), _num(b.get("observed_win_rate"), 3),
                     _num(b.get("shrunk_observed_rate"), 3),
                     f"{_num(b.get('ci95_low'), 3)} – {_num(b.get('ci95_high'), 3)}", suf])
    head = ('<div class="grid">'
            + card("Kalibrasyon örneği (gerçek)", _num(cal.get("n_real"), 0), "yalnız GERÇEK PAPER sonuçları")
            + card("ECE", _num(cal.get("ece"), 4), "expected calibration error — düşük daha iyi")
            + card("Yeterli kova", _num(cal.get("n_sufficient_buckets"), 0),
                   f"kova başına asgari örnek: {esc(str(cal.get('min_bucket_sample') or '—'))}")
            + card("Reddedilen (gelecek/çift)",
                   f"{_num(cal.get('rejected_future'), 0)} / {_num(cal.get('rejected_duplicate'), 0)}",
                   "no-lookahead + duplicate koruması")
            + "</div>")
    return ("<h2>Olasılık kalibrasyonu</h2>" + head
            + table(["Kova", "n (gerçek)", "Ortalama tahmin", "Gözlenen", "Büzülmüş", "%95 GA", "Durum"],
                    rows, num_cols={1, 2, 3, 4}, empty="kalibrasyon örneği yok")
            + '<p class="mut small">TEK sonuç bir olasılık tahminini doğrulamaz da yanlışlamaz da: '
              '%29 olasılıklı olay da gerçekleşir. Bu tablo çok sayıda tahminin TOPLU davranışını '
              'ölçer. Kova örneği yetersizse hüküm YOKTUR (' + NOT_ENOUGH_DATA + '). Kalibrasyon '
              'aktif RiskEngine\'e DOKUNMAZ — ' + ACTIVE_POLICY_UNCHANGED + '.</p>')


def quality_block(ln: dict, *, win_rate: Any = None, expectancy_r: Any = None,
                  counters_bad: str = "") -> str:
    """Win rate TEK BAŞINA gösterilmez: payoff ve net beklenti ile BİRLİKTE okunur.

    `win_rate`/`expectancy_r` ÇAĞIRANDAN gelir — bu blok sayacı KENDİ TÜRETMEZ. Çelişkili
    sayaçta (`counters_bad`) birleşik kart «Veri yok» olur; %100 üstü oran UYDURULMAZ.
    """
    from ..pnl import finite_float_or_none
    d = _d(ln)
    q = _d(d.get("quality_metrics"))
    wr = finite_float_or_none(win_rate)
    exp_r = finite_float_or_none(expectancy_r)
    if counters_bad:
        wr = exp_r = None
    if wr is None:
        wr = finite_float_or_none(q.get("win_rate"))
    if exp_r is None:
        exp_r = finite_float_or_none(q.get("expectancy_r"))
    payoff = finite_float_or_none(q.get("payoff_ratio"))
    avg_w, avg_l = finite_float_or_none(q.get("avg_win_r")), finite_float_or_none(q.get("avg_loss_r"))
    if payoff is None and avg_w is not None and avg_l:
        payoff = avg_w / abs(avg_l)
    combined = ("Veri yok" if (wr is None or payoff is None or exp_r is None)
                else f"%{wr * 100:.1f} · {payoff:.2f} · {exp_r:+.3f}R")
    cards = ('<div class="grid">'
             + card("Oran × payoff × beklenti", combined,
                    "üçü BİRLİKTE okunur — tek başına oran başarı ölçüsü DEĞİLDİR")
             + card("Payoff", _num(payoff, 3), "ortalama kazanç R / ortalama kayıp R")
             + card("Net beklenti", _num(exp_r, 4, "R"), "işlem başına maliyet sonrası R")
             + card("Ortalama kazanç / kayıp",
                    f"{_num(avg_w, 3, 'R')} / {_num(avg_l, 3, 'R')}", "R cinsinden")
             + card("Profit factor", _num(q.get("profit_factor"), 3), "brüt kâr / brüt zarar")
             + card("Maks. drawdown", _num(q.get("max_drawdown_r"), 3, "R"), "R cinsinden")
             + card("Tail (CVaR5)", _num(q.get("tail_loss_r_cvar5"), 3, "R"), "en kötü %5 ortalaması")
             + card("En uzun kayıp serisi", _num(q.get("longest_loss_streak"), 0))
             + card("Maliyet sürüklemesi", _num(q.get("cost_drag_r"), 4, "R"), "ücret + funding + kayma")
             + card("Capture ratio", _num(q.get("capture_ratio_mean"), 3), "gerçekleşen R / MFE R")
             + "</div>")
    warn = ('<p class="mut small">YÜKSEK KAZANMA ORANI TEK BAŞINA BAŞARI DEĞİLDİR. Örnek: '
            "4 işlemin 3'ü kazanç (oran 0.75) ama ortalama kazanç +0.40R / ortalama kayıp "
            '−1.00R → beklenti yalnız +0.05R (maliyet öncesi) ve tek kötü seri bunu siler. '
            "Buna karşılık 2 işlemin 1'i kazanç (oran 0.50), +1.50R / −1.00R → beklenti "
            '+0.25R. Terfi kapıları payoff, tail ve yoğunlaşmayı BİRLİKTE arar.</p>')
    return "<h2>Kalite metrikleri (birlikte okunur)</h2>" + cards + warn


def observation_block(lessons: list) -> str:
    """Gözlem ↔ hipotez ayrımı + edge/execution sınıfları. Politika iddiası YOKTUR."""
    rows = []
    for x in (lessons or [])[-30:][::-1]:
        if not isinstance(x, dict):
            continue
        obs = _d(x.get("observation"))
        raw_h = x.get("hypotheses")
        hyps = [h for h in raw_h if isinstance(h, dict)] if isinstance(raw_h, list) else []
        if not obs and not hyps:
            continue
        raw_c = obs.get("observation_codes")
        codes = ", ".join(str(c) for c in raw_c) if isinstance(raw_c, list) else "—"
        codes = codes or "—"
        hcodes = ", ".join(str(h.get("code")) for h in hyps) or "—"
        cap = obs.get("capture_ratio")
        cap_txt = _num(cap, 3) if cap is not None else esc(str(obs.get("capture_ratio_state") or "—"))
        rows.append([esc(str(x.get("id") or "—")), esc(str(x.get("symbol") or "—")),
                     esc(codes), esc(hcodes),
                     _num(obs.get("mfe_r"), 3), _num(obs.get("mae_r"), 3),
                     _num(obs.get("realized_r"), 3), cap_txt,
                     _num(obs.get("cost_drag_total_r"), 4),
                     esc(str(obs.get("data_quality") or "—")),
                     evidence_badge(x.get("evidence_level"))])
    if not rows:
        return ""
    return ("<h3>Gözlem ↔ hipotez (edge vs execution)</h3>"
            + table(["İşlem", "Sembol", "GÖZLEM kodları", "HİPOTEZ kodları", "MFE R", "MAE R",
                     "Gerçekleşen R", "Capture", "Maliyet R", "Veri", "Kanıt"],
                    rows, num_cols={4, 5, 6, 7, 8}, empty="gözlem kaydı yok")
            + f'<p class="mut small">Soldaki kodlar GÖZLEMDİR (ne oldu), sağdakiler '
              f'ARAŞTIRMA HİPOTEZİDİR (ne sorulmalı) — {RESEARCH_ONLY}. Tek işlem '
              f'`OBSERVATION` seviyesini AŞAMAZ ve politika değiştiremez: '
              f'{ACTIVE_POLICY_UNCHANGED}. Nedensellik iddiası yoktur.</p>')


__all__ = ["page", "table", "kv_table", "render_any", "card", "badge", "health_badge", "ks_badge", "verdict_badge", "pnl_cell",
           "fmt", "pct", "esc", "age_text", "chart_block", "CSS", "NAV", "CHART_JS",
           "retention_block", "calibration_block", "quality_block", "observation_block",
           "evidence_badge", "NOT_ENOUGH_DATA", "RESEARCH_ONLY", "ACTIVE_POLICY_UNCHANGED"]

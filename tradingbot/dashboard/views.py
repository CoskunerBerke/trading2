"""Panel GÖRÜNÜM MODELİ — ham JSON yerine etiketli, tutarlılığı denetlenmiş kartlar/tablolar.

Bütün sayılar kanonik `tradingbot.pnl` katmanından gelir; Telegram da AYNI katmanı kullanır.

KAVRAM AYRIMI (kullanıcı şikâyetinin kaynağı):
* `breadth.long` = son STRATEJİ TURUNDAKİ **LONG işlem adayı/kararı** sayısıdır.
* Açık LONG **pozisyon** sayısı defterden gelir ve tamamen ayrı bir büyüklüktür.
  `chief.breadth.long = 3` iken `açık pozisyon = 2` olması tutarsızlık DEĞİLDİR: 3 yeni LONG
  adayı + 2 açık pozisyon (bunlar `breadth.hold` içinde sayılır) demektir.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..pnl import (FRESH_OK, FRESH_STALE, FRESH_UNKNOWN, PF_POSITIVE_INFINITY, PortfolioView,
                   PositionView, canonical_summary, check_invariants, finite_float_or_none,
                   fmt_money, fmt_pct, fmt_qty, money_totals_unavailable_reason, portfolio_view,
                   profit_factor_state)

# Tazelik adları `pnl` katmanında TEK yerde tanımlıdır; burada yalnız yeniden dışa verilir
# (iki ayrı string listesi zamanla ayrışır ve `stale` sessizce `live` gibi görünürdü).
LIVE_OK = FRESH_OK
LIVE_STALE = FRESH_STALE
LIVE_UNKNOWN = FRESH_UNKNOWN


@dataclass(frozen=True)
class Freshness:
    """Her bölümün veri zamanı ayrı ayrı raporlanır — fiyat yaşı ile tur yaşı KARIŞTIRILMAZ."""
    price_age_s: float | None
    run_age_s: float | None
    heads_age_s: float | None
    heartbeat_age_s: float | None
    stale_price_s: int
    stale_run_s: int
    tz_label: str = "UTC"

    @property
    def price_state(self) -> str:
        if self.price_age_s is None:
            return LIVE_UNKNOWN
        return LIVE_OK if self.price_age_s <= self.stale_price_s else LIVE_STALE

    @property
    def run_state(self) -> str:
        if self.run_age_s is None:
            return LIVE_UNKNOWN
        return LIVE_OK if self.run_age_s <= self.stale_run_s else LIVE_STALE

    def to_dict(self) -> dict:
        return {"price_age_s": self.price_age_s, "run_age_s": self.run_age_s,
                "heads_age_s": self.heads_age_s, "heartbeat_age_s": self.heartbeat_age_s,
                "price_state": self.price_state, "run_state": self.run_state,
                "stale_price_s": self.stale_price_s, "stale_run_s": self.stale_run_s,
                "tz_label": self.tz_label}


# ============================================================ COIN HEAD TABLOSU — KAPSAM SÖZLEŞMESİ
# «Coin head'ler» tablosu bir AÇIK POZİSYON LİSTESİ DEĞİLDİR: coin head'lerin son seçkisidir.
# Fakat GERÇEK açık pozisyonların tabloda görünmemesi operatöre "pozisyon yok" izlenimi verir.
# Eski davranış `sorted(heads, key=-confidence)[:10]` idi -> top-N kesimi dışında kalan açık
# pozisyon (ve seçkide hiç yer almayan sembol) tablodan DÜŞÜYORDU. Bu bir SUNUM sorunudur;
# defterde veri kaybı yoktur. Yeni sözleşme:
#   1) BÜTÜN açık pozisyonlar ZORUNLU olarak tabloda yer alır (aday limiti onları düşüremez),
#   2) önce açık pozisyonlar, sonra kalan kapasiteye güvene göre sıralı adaylar,
#   3) aynı sembol İKİ KEZ gösterilmez,
#   4) güncel coin-head kararı olmayan açık pozisyon için karar UYDURULMAZ; açık bir
#      «KARAR VERİSİ YOK» satırı üretilir.
NO_DECISION_VERDICT = "KARAR VERİSİ YOK"
NO_DECISION_REASON = "Pozisyon defterde açık; son coin-head seçkisinde yer almıyor."
COIN_HEAD_CANDIDATE_LIMIT = 10          # aday KOTASI — açık pozisyonlara UYGULANMAZ


def _head_confidence(h: dict) -> float | None:
    """Sonlu kalibre güven; NaN/±Infinity/bozuk/eksik -> None. KANONİK guard kullanılır."""
    return finite_float_or_none(h.get("confidence_calibrated"))


def coin_head_sort_key(h: dict) -> tuple:
    """DETERMİNİSTİK aday sıralaması: güven azalan; güveni ÖLÇÜLEMEYEN aday EN SONA.

    Çıplak `float()` ile NaN sıralama anahtarına girerse karşılaştırmalar `False` döner ve sıra
    girdi sırasına göre KARARSIZ olur. Bu yüzden önce sonluluk bayrağı, sonra `-güven`, sonra
    sembol (eşitlikte tam determinizm) karşılaştırılır.
    """
    c = _head_confidence(h)
    sym = str(h.get("symbol") or "")
    return (1, sym) if c is None else (0, -c, sym)


def json_safe(obj: Any, *, path: str = "", reasons: dict | None = None,
              _seen: frozenset = frozenset()) -> tuple[Any, dict]:
    """RFC-uyumlu JSON'a çevrilebilir kopya + `unavailable_reason` haritası.

    * Sonlu olmayan sayı (RFC dışı float/ondalık dâhil) -> `None` + gerekçe.
      SAHTE `0` / `%0` / `$0.00` ÜRETİLMEZ.
    * JSON'a çevrilemeyen tip -> `None` + gerekçe. Ham nesne KÖRLEMESİNE `str()`e ÇEVRİLMEZ.
    * str/bool/int/None ve dict/list yapıları olduğu gibi korunur.
    Kanonik sonluluk guard'ı `pnl.finite_float_or_none`dur; ikinci bir kopya YOKTUR.

    Girdi YERİNDE DEĞİŞTİRİLMEZ (yeni dict/list üretilir). Döngüsel referans state JSON'undan
    gelemez (dosyadan `json.load` ile okunur) fakat yine de `_seen` ile korunur: uç
    `RecursionError` ile DÜŞMEZ, döngü noktası `null` + gerekçe olur.
    """
    reasons = {} if reasons is None else reasons
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj, reasons
    if isinstance(obj, float):
        v = finite_float_or_none(obj)
        if v is None:
            reasons[path or "<root>"] = "sonlu olmayan sayı — değer ölçülemedi"
        return v, reasons
    if isinstance(obj, Decimal):
        v = finite_float_or_none(obj)
        if v is None:
            reasons[path or "<root>"] = "sonlu olmayan ondalık — değer ölçülemedi"
        return v, reasons
    if isinstance(obj, (dict, list, tuple)):
        if id(obj) in _seen:
            reasons[path or "<root>"] = "döngüsel referans — çözümlenemedi"
            return None, reasons
        seen = _seen | {id(obj)}
        if isinstance(obj, dict):
            out = {}
            for k, val in obj.items():
                key = str(k)
                out[key], reasons = json_safe(val, path=(path + "." + key if path else key),
                                              reasons=reasons, _seen=seen)
            return out, reasons
        out_l = []
        for i, val in enumerate(obj):
            v, reasons = json_safe(val, path="%s[%d]" % (path, i), reasons=reasons, _seen=seen)
            out_l.append(v)
        return out_l, reasons
    reasons[path or "<root>"] = "JSON'a çevrilemeyen tip: %s" % type(obj).__name__
    return None, reasons


def coin_head_api_rows(payload: dict) -> list[dict]:
    """`/api/overview` icin NORMALIZE edilmis coin-head ozeti — HAM model ciktisi DEGIL.

    Ham `heads` sozlugu `specialist_reports[].levels.ema*_1d` gibi ic ic gecmis model alanlari
    tasir ve bunlarin icinde CIPLAK NaN bulunabilir; ham sozluk JSON'a verildiginde uc 500 doner.
    Burada yalniz sunum sozlesmesindeki alanlar, kanonik sonluluk guard'indan gecirilerek yayimlanir.
    """
    out = []
    for h, m in zip(payload.get("heads") or [], payload.get("meta") or []):
        out.append({"symbol": str(h.get("symbol") or ""),
                    "verdict": (None if h.get("verdict") is None else str(h.get("verdict"))),
                    "direction": (None if h.get("direction") is None else str(h.get("direction"))),
                    "regime": (None if h.get("regime") is None else str(h.get("regime"))),
                    "status": m.get("status"), "status_kind": m.get("status_kind"),
                    "no_decision": bool(m.get("no_decision")), "is_open": bool(m.get("is_open")),
                    "position_id": m.get("position_id"), "side": m.get("side"),
                    "confidence_calibrated": finite_float_or_none(h.get("confidence_calibrated")),
                    "p_win": finite_float_or_none(h.get("p_win")),
                    "expected_return_net": finite_float_or_none(h.get("expected_return_net")),
                    "expected_r": finite_float_or_none(h.get("expected_r")),
                    "net_unrealized": finite_float_or_none(m.get("pnl")),
                    "no_trade_reason": str(h.get("no_trade_reason") or "") or None})
    return out


def no_decision_head(symbol: str, position: dict | None = None) -> dict:
    """Açık pozisyon için KARAR UYDURMAYAN yer tutucu satır.

    `verdict` bilinçli olarak boştur; `no_decision` bayrağı panelin bu satırı ayrı etiketlemesini
    sağlar. Yön defterden alınır — model tahmini DEĞİL, defterin gerçeğidir.
    """
    p = position or {}
    return {"symbol": str(symbol), "verdict": None, "no_decision": True,
            "direction": str(p.get("side") or "") or None,
            "no_trade_reason": NO_DECISION_REASON,
            "position_id": str(p.get("id") or "") or None,
            "confidence_calibrated": None, "p_win": None, "expected_return_net": None,
            "expected_r": None, "regime": None, "generated_at": None, "vetoes": []}


def open_coverage(rows: list[dict] | None, open_positions: list[dict] | None) -> dict[str, Any]:
    """AÇIK POZİSYON KAPSAMINI ÖLÇER — varsaymaz.

    Sayaç, GERÇEKTEN üretilen satır kümesinden hesaplanır. Aksi hâlde (sabit "hepsi gösterildi"
    varsayımı) ileride bir satır düştüğünde panel «5 / 5» yazmaya devam eder ve YALAN SÖYLER.
    Panel başlığı da bunu render ettiği satırlar üzerinden yeniden çağırır.
    """
    row_symbols = {str(r.get("symbol") or "") for r in (rows or []) if isinstance(r, dict)}
    open_symbols: list[str] = []
    for p in (open_positions or []):
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "")
        if sym and sym not in open_symbols:
            open_symbols.append(sym)
    shown = [s for s in open_symbols if s in row_symbols]
    missing = [s for s in open_symbols if s not in row_symbols]
    return {"open_positions_total": len(open_symbols), "open_positions_shown": len(shown),
            "missing_open_symbols": missing, "coverage_complete": not missing}


def coin_head_scope(heads: list[dict] | None, open_positions: list[dict] | None, *,
                    candidate_limit: int = COIN_HEAD_CANDIDATE_LIMIT) -> dict[str, Any]:
    """Coin head satırları + AÇIK POZİSYON KAPSAMI ölçümü.

    HTML render'ı ve API AYNI bu fonksiyondan beslenir; iki yüzey farklı satır kümesi gösteremez.
    Toplam satır sayısı = açık pozisyon adedi + `candidate_limit` (aday kotası açıkları düşürmez).
    """
    heads = [h for h in (heads or []) if isinstance(h, dict)]
    positions = [p for p in (open_positions or []) if isinstance(p, dict)]

    by_symbol: dict[str, dict] = {}
    for h in heads:
        sym = str(h.get("symbol") or "")
        if sym and sym not in by_symbol:                 # aynı sembolün ilk (en taze) kaydı
            by_symbol[sym] = h
    pos_by_symbol: dict[str, dict] = {}
    open_symbols: list[str] = []
    for p in positions:
        sym = str(p.get("symbol") or "")
        if sym and sym not in pos_by_symbol:
            pos_by_symbol[sym] = p
            open_symbols.append(sym)

    rows: list[dict] = []
    no_decision: list[str] = []
    for sym in open_symbols:                             # 1) ZORUNLU: bütün açık pozisyonlar
        h = by_symbol.get(sym)
        if h is None:
            no_decision.append(sym)
            h = no_decision_head(sym, pos_by_symbol.get(sym))
        rows.append(h)
    used = set(open_symbols)
    for h in sorted(heads, key=coin_head_sort_key):               # 2) kalan kapasiteye adaylar
        sym = str(h.get("symbol") or "")
        if not sym or sym in used:                       # 3) sembolsuz/bozuk kayit ve duplicate YOK
            continue
        if len(rows) - len(open_symbols) >= max(0, int(candidate_limit)):
            break
        used.add(sym)
        rows.append(h)

    # Kapsam ÖLÇÜLÜR (varsayılmaz): GERÇEKTEN üretilen satırlar sayılır.
    return dict(open_coverage(rows, positions), heads=rows,
                no_decision_symbols=no_decision,
                candidate_limit=max(0, int(candidate_limit)))


# ---------------------------------------------------------------- COIN HEAD TABLOSU — TEK BUILDER
# İlk sunucu render'ı VE canlı polling AYNI bu çıktıyı tüketir. İş kuralı (durum sınıflandırması,
# sıra, fallback, kapsam) HİÇBİR YERDE KOPYALANMAZ; JS yalnızca hücreleri çizer.
# Kolon adlari ALANIN GERCEK KAYNAGINI soyler: "Guven" bir OLASILIK DEGILDIR (konsensus
# sinyal gucu), "P(kazanc)" ise hiyerarsik/legacy harmanidir. Bkz. COIN_HEAD_COLUMN_NOTES.
COIN_HEAD_COLUMNS = ("Sembol", "Karar", "Durum", "Yön", "Konsensüs gücü", "P(kazanç) — istatistiksel tahmin",
                     "Plan getirisi — hedef 1 (maliyet sonrası)", "Plan R/R (maliyet sonrası)",
                     "Beklenen değer (R)", "Rejim", "Spot", "Fut", "Net K/Z",
                     "İşlem ID", "Karar zamanı", "Gerekçe", "Veto")

#: Alan anlamlari — SUNUM metnidir; hicbir model calistirmaz, hicbir eksik alani tahmin etmez.
#: Olasilik notu KANITA baglidir (`learning_status`); kalici sabit bir iddia DEGILDIR.
COIN_HEAD_COLUMN_NOTES_BASE = {
    "Konsensüs gücü": ("uzman ajan konsensüsünün kalibre edilmiş GÜCÜ (`confidence_calibrated`) "
                       "— bir olasılık DEĞİLDİR"),
    "P(kazanç) — istatistiksel tahmin": (
        "hiyerarşik Beta önseli + eski tahmin harmanı (`p_win`); hedefi «kapanışta "
        "r_multiple > +0.25R», ufku işlem ÖMRÜ"),
    "Plan getirisi — hedef 1 (maliyet sonrası)": (
        "% notional; KOŞULLU bir büyüklüktür: hedef 1'e ULAŞILDIĞI varsayımıyla plan "
        "geometrisinden hesaplanır (`|hedef1-giriş|/giriş*100 - maliyet`). Olasılıkla "
        "AĞIRLIKLANDIRILMAMIŞTIR ve beklenen değer DEĞİLDİR. Giriş planı ÜRETİLMEYEN "
        "satırlarda (REDUCE/HOLD/NO_TRADE) HİÇ hesaplanmaz"),
    "Plan R/R (maliyet sonrası)": (
        "(hedef1 - maliyet) / stop — yalnız plan GEOMETRİSİ. ATR planında hedef1 stop "
        "mesafesinin 2 katına kurulduğu için bu sayı her adayda ~2'ye yakındır; ayırt "
        "edici bir büyüklük DEĞİLDİR ve gerçekleşmiş sonuç DEĞİLDİR"),
    "Beklenen değer (R)": (
        "olasılıkla ağırlıklandırılmış beklenti: `p_win × ort_kazanç_R - (1-p_win) × "
        "|ort_kayıp_R|` (maliyet tabana göre bir kez düşülür). Ekonomik kapının kullandığı "
        "büyüklük budur. `p_win`in hedefi «kapanışta r_multiple > +0.25R»tir; hedefe stop'tan "
        "önce ULAŞMA olasılığı DEĞİLDİR. Hesaplanamıyorsa «bilinmiyor» yazar — 0 YAZILMAZ"),
    "Karar zamanı": ("bu satır EN SON değerlendirmedir; açık pozisyonun GİRİŞ anındaki "
                     "kanıtı değildir (giriş kanıtı `entry_snapshot` kaydındadır)"),
}

#: Kalibrasyon durumu bilinmiyorsa KESIN bir sey iddia edilmez.
CALIBRATION_UNKNOWN = "kalibrasyon durumu bu panelden DOĞRULANAMADI"


def calibration_note(learning_status: dict | None) -> str:
    """Kalibrasyon/model durumunu KANITTAN türetir; sabit bir iddia yazmaz.

    `learning_status` beklenen alanlar: `calibrator_n_fit` (int) ve `champion_model`
    (kimlik ya da None). Alan yoksa durum BİLİNMİYOR olarak raporlanır.
    """
    st = learning_status if isinstance(learning_status, dict) else None
    if not st:
        return CALIBRATION_UNKNOWN
    parts = []
    n_fit = st.get("calibrator_n_fit")
    if isinstance(n_fit, (int, float)):
        parts.append("kalibratör fit edilmiş (n_fit=%d)" % int(n_fit) if int(n_fit) > 0
                     else "kalibratör FIT EDİLMEMİŞ (n_fit=0)")
    champ = st.get("champion_model")
    if "champion_model" in st:
        parts.append("champion sınıflandırıcı: %s" % (str(champ) if champ else "YOK"))
    return " · ".join(parts) if parts else CALIBRATION_UNKNOWN


def coin_head_column_notes(learning_status: dict | None = None) -> dict[str, str]:
    notes = dict(COIN_HEAD_COLUMN_NOTES_BASE)
    key = "P(kazanç) — istatistiksel tahmin"
    notes[key] = notes[key] + " · " + calibration_note(learning_status)
    return notes


#: Geriye uyumluluk: kanıt verilmediğinde durum BİLİNMİYOR olarak görünür.
COIN_HEAD_COLUMN_NOTES = coin_head_column_notes(None)

COIN_HEAD_NUM_COLS = (4, 5, 6, 7, 8, 12)
COIN_HEAD_PNL_COLS = (12,)
COIN_HEAD_BADGE_COLS = (1, 2)
COIN_HEAD_SYMBOL_COL = 0

STATUS_OPEN = "AÇIK"
STATUS_CLOSED = "KAPANDI"
STATUS_CANDIDATE = "ADAY"
STATUS_REJECTED = "REDDEDİLDİ"
STATUS_NO_TRADE = "İŞLEM YOK"
_ACTIONABLE = ("SPOT_LONG", "FUTURES_LONG", "FUTURES_SHORT")


def _cell_num(x, nd: int = 2) -> str:
    """Hücre metni — SONLU olmayan değer (`inf`/`nan`) asla yazılmaz (kanonik guard)."""
    v = finite_float_or_none(x)
    return "—" if v is None else f"{v:,.{nd}f}"


#: Giris plani URETILMEYEN satirda beklenti alanlari HIC hesaplanmaz (bkz. coinhead/head.py:
#: `expected_return_net`/`expected_r` yalnizca gecerli plan yolunda atanir; aksi halde
#: dataclass VARSAYILANI 0.0 kalir). Bu bir OLCULMUS sifir DEGILDIR.
NOT_APPLICABLE = "yok (plan üretilmedi)"

#: Ekonomik degerlendirme (`opportunity`) kaydda YOKSA beklenti BILINMIYOR'dur. Eksik bir
#: beklenti "0" diye gosterilemez: 0, "beklenti olculdu ve sifir cikti" demektir.
UNKNOWN_EXPECTANCY = "bilinmiyor"


def _cell_expectancy_r(h: dict, nd: int = 3) -> str:
    """Olasilikla agirliklandirilmis beklenti (R) — `opportunity.net_expectancy_r`.

    Kayitta `opportunity` yoksa ya da alan sonlu degilse `UNKNOWN_EXPECTANCY` doner.
    Bu alan plan geometrisinden (`expected_r`) FARKLIDIR ve onun yerine gecmez.
    """
    o = h.get("opportunity")
    if not isinstance(o, dict):
        return UNKNOWN_EXPECTANCY
    v = finite_float_or_none(o.get("net_expectancy_r"))
    return UNKNOWN_EXPECTANCY if v is None else f"{v:+,.{nd}f}"


def _has_entry_plan(h: dict) -> bool:
    """Bu karar gercekten bir GIRIS PLANI uretti mi? Sozlesme: en az bir gecerli plan."""
    for key in ("futures_plan", "spot_plan"):
        p = h.get(key)
        if isinstance(p, dict) and p.get("valid"):
            return True
    return False


def _cell_pct(x, nd: int = 2) -> str:
    """KESIR girdiyi (0.0862) yuzdeye cevirir. Girdi ZATEN yuzde ise `_cell_pct_points` kullanin."""
    v = finite_float_or_none(x)
    return "—" if v is None else f"%{v * 100:.{nd}f}"


def _cell_pct_points(x, nd: int = 2) -> str:
    """Girdi ZATEN yuzde birimindedir (8.6016 -> "%8.60"); ikinci kez 100 ile CARPILMAZ.

    `coinhead/head.py` `expected_return_gross/net` alanlarini yuzde olarak uretir
    (`abs(target-entry)/entry * 100`). Bu degeri `_cell_pct` ile bicimlendirmek ekranda
    100 kat buyuk bir sayi verir (olculdu: ham 10.8412 -> "%1084.12").
    """
    v = finite_float_or_none(x)
    return "—" if v is None else f"%{v:.{nd}f}"


def _cell_pct_signal(x, nd: int = 0) -> str:
    """Yuzde hucresi — SIFIR OLMAYAN kucuk deger asla "%0" gibi gosterilmez.

    OLCULMUS sifir aynen "%0" kalir; eksik deger "—" olur. Kucuk ama sifir olmayan bir deger
    (or. 0.0019 -> %0.19) yuvarlanip sifira DUSURULMEZ: gosterim hassasiyeti, degeri
    gorunur kilacak kadar artirilir. Gercek sayisal deger DEGISTIRILMEZ.
    """
    v = finite_float_or_none(x)
    if v is None:
        return "—"
    pct = v * 100.0
    if pct == 0.0:
        return f"%{pct:.{nd}f}"
    step = nd
    while step < 4 and abs(round(pct, step)) < 10 ** -step:
        step += 1
    if abs(round(pct, step)) == 0.0:            # 4 hanede bile gorunmuyor -> esik ifadesi
        return "%<0.0001" if pct > 0 else "%>-0.0001"
    return f"%{pct:.{step}f}"


def coin_head_table(heads: list[dict] | None, positions: list[dict] | None,
                    trades: list[dict] | None = None, *, fees: Any = None,
                    learning_status: dict | None = None,
                    candidate_limit: int = COIN_HEAD_CANDIDATE_LIMIT) -> dict[str, Any]:
    """Coin head tablosunun KANONİK yükü: kapsam + kolonlar + DÜZ METİN hücreler + satır meta'sı.

    Hücreler düz metindir: sunucu `badge()`/`money_html()` ile, tarayıcı da AYNI meta'dan CSS
    sınıflarıyla çizer. HTML JSON'da TAŞINMAZ → polling yolunda XSS yüzeyi yoktur.
    """
    from ..pnl import fmt_money, position_view, realized_net
    scope = coin_head_scope(heads, positions, candidate_limit=candidate_limit)
    open_by = {str(p.get("symbol") or ""): p for p in (positions or []) if isinstance(p, dict)}
    last_closed: dict[str, dict] = {}
    for tr in (trades or []):
        if not isinstance(tr, dict):
            continue
        sym = str(tr.get("symbol") or "")
        if sym and sym not in last_closed:
            last_closed[sym] = tr

    rows, meta = [], []
    for h in scope["heads"]:
        sym = str(h.get("symbol") or "")
        pos, closed = open_by.get(sym), last_closed.get(sym)
        pnl_val, trade_id = None, "—"
        if pos is not None:
            status, kind = STATUS_OPEN, "ok"
            trade_id = str(pos.get("id") or "") or "—"
            pnl_val = position_view(pos, mark_price=pos.get("last_price"), fees=fees).net_unrealized
        elif closed is not None:
            # Pozisyon KAPANDIYSA satır AÇIK gibi gösterilmez; son kapanan işlemin sonucu yazılır.
            status, kind = STATUS_CLOSED, "info"
            trade_id = str(closed.get("id") or "") or "—"
            pnl_val = realized_net(closed)
        elif str(h.get("verdict") or "") in _ACTIONABLE:
            status, kind = STATUS_CANDIDATE, "info"
        elif h.get("vetoes"):
            status, kind = STATUS_REJECTED, "bad"
        else:
            status, kind = STATUS_NO_TRADE, "info"
        no_dec = bool(h.get("no_decision"))
        fp, sp = h.get("futures_plan") or {}, h.get("spot_plan") or {}
        rows.append([sym,
                     NO_DECISION_VERDICT if no_dec else (str(h.get("verdict") or "") or "-"),
                     status,
                     str(h.get("direction") or "") or "-",
                     _cell_pct_signal(h.get("confidence_calibrated"), 0),
                     _cell_pct_signal(h.get("p_win"), 0),
                     _cell_pct_points(h.get("expected_return_net")) if _has_entry_plan(h) else NOT_APPLICABLE,
                     _cell_num(h.get("expected_r")) if _has_entry_plan(h) else NOT_APPLICABLE,
                     _cell_expectancy_r(h),
                     str(h.get("regime") or "") or "—",
                     "✅" if sp.get("valid") else "—",
                     "✅" if fp.get("valid") else "—",
                     "—" if pnl_val is None else fmt_money(pnl_val),
                     trade_id,
                     str(h.get("generated_at") or "") or "—",
                     str(h.get("no_trade_reason") or ""),
                     ", ".join(str(v) for v in (h.get("vetoes") or []))[:80]])
        meta.append({"symbol": sym, "status": status, "status_kind": kind, "no_decision": no_dec,
                     "verdict": h.get("verdict"), "is_open": pos is not None,
                     "position_id": None if pos is None else str(pos.get("id") or ""),
                     "side": None if pos is None else str(pos.get("side") or ""),
                     "pnl": None if pnl_val is None else float(pnl_val)})
    return {
        **scope,
        "columns": list(COIN_HEAD_COLUMNS), "rows": rows, "meta": meta,
        "column_notes": coin_head_column_notes(learning_status),
        "num_cols": list(COIN_HEAD_NUM_COLS), "pnl_cols": list(COIN_HEAD_PNL_COLS),
        "badge_cols": list(COIN_HEAD_BADGE_COLS), "symbol_col": COIN_HEAD_SYMBOL_COL}


@dataclass(frozen=True)
class ChiefView:
    """Baş yönetici bölümünün OKUNUR modeli — uzun ham JSON gösterilmez."""
    generated_at: str
    market_risk_mode: str
    long_candidates: int              # İŞLEM ADAYI (açık pozisyon DEĞİL)
    short_candidates: int
    no_trade: int
    hold: int
    data_invalid: int
    open_long: int                    # GERÇEK açık pozisyon (defterden)
    open_short: int
    open_total: int
    long_notional: Any
    short_notional: Any
    open_risk_usdt: Any               # risk motoru REZERVASYONU (stop riskinden AYRI kavram)
    margin_util_pct: Any
    realized_today: Any
    unrealized_open: Any
    drawdown_pct: Any
    open_stop_risk_usdt: Any = None   # stop'a kadar BRÜT tahmini kayıp (defterden hesaplanır)
    risk_budget_max_usdt: Any = None
    risk_budget_util_pct: Any = None
    risk_equity_basis_usdt: Any = None    # motorun KABUL kararında kullandığı taban
    risk_equity_basis_kind: Any = None    # starting_equity | live_equity
    risk_snapshot_age_s: Any = None       # risk.json yaşı (fiyat yaşından AYRI)
    risk_snapshot_state: Any = None       # live | stale | unknown
    # --- ÜÇ AYRI KAVRAM: tek kartta TOPLANMAZ (eski snapshot'ta None → «Veri yok») ---
    futures_stop_risk_usdt: Any = None    # kabul kapısının GERÇEK kovası
    futures_risk_budget_util_pct: Any = None
    spot_exposure_usdt: Any = None        # spot notional maruziyeti — RİSK DEĞİL
    spot_stop_risk_usdt: Any = None       # yalnız gerçek duran stop emri olan spot
    spot_symbols_without_stop: Any = None
    spot_allocation_util_pct: Any = None
    spot_exposure_unknown: Any = None      # geçersiz fiyat → maruziyet ölçülemedi (fail-closed)
    spot_symbols_unknown_price: Any = None

    def to_dict(self) -> dict:
        return {k: (format(v, "f") if hasattr(v, "quantize") else v) for k, v in self.__dict__.items()}


def chief_view(chief: dict | None, pv: PortfolioView, summary: dict | None = None) -> ChiefView:
    """Chief snapshot'ı (ADAYLAR) + defter (AÇIK POZİSYONLAR) → tek okunur model.

    İki kaynak FARKLI zamanlara ait olabilir; bu yüzden aday sayıları ile açık pozisyon sayıları
    ayrı alanlarda tutulur ve panelde ayrı etiketlerle gösterilir.

    RİSK/TEMİNAT/DRAWDOWN alanları artık `coin_heads.json → chief.exposure` yerine KANONİK özetten
    gelir. Sebep: chief snapshot'ı bu alanları strateji turunda kopyalar ve `margin_util_pct` orada
    `float(ps.get("margin_util_pct", 0.0))` ile üretilir — risk durumunda böyle bir anahtar
    OLMADIĞI için her zaman `0.0` yazılıyordu (panelde «%0.0»). Kanonik değer defterden hesaplanır,
    böylece özet kartı ile baş yönetici kartı YAPI GEREĞİ aynı sayıyı gösterir.
    """
    c = chief or {}
    b = c.get("breadth") or {}
    s = summary or {}
    return ChiefView(
        generated_at=str(c.get("generated_at") or ""),
        market_risk_mode=str(c.get("market_risk_mode") or "—"),
        long_candidates=int(b.get("long") or 0), short_candidates=int(b.get("short") or 0),
        no_trade=int(b.get("no_trade") or 0), hold=int(b.get("hold") or 0),
        data_invalid=int(b.get("data_invalid") or 0),
        open_long=pv.open_long, open_short=pv.open_short, open_total=pv.open_total,
        long_notional=pv.long_notional, short_notional=pv.short_notional,
        open_risk_usdt=s.get("risk_engine_reserved_usdt"),
        margin_util_pct=s.get("margin_utilization_pct"),
        realized_today=pv.realized_today, unrealized_open=pv.open_net_unrealized,
        drawdown_pct=s.get("max_drawdown_pct", finite_float_or_none(pv.max_drawdown)),
        open_stop_risk_usdt=s.get("open_stop_risk_usdt"),
        risk_budget_max_usdt=s.get("risk_budget_max_usdt"),
        risk_budget_util_pct=s.get("open_risk_budget_utilization_pct"),
        risk_equity_basis_usdt=s.get("risk_equity_basis_usdt"),
        risk_equity_basis_kind=s.get("risk_equity_basis_kind"),
        risk_snapshot_age_s=s.get("risk_snapshot_age_s"),
        risk_snapshot_state=s.get("risk_snapshot_state"),
        futures_stop_risk_usdt=s.get("futures_stop_risk_usdt"),
        futures_risk_budget_util_pct=s.get("futures_risk_budget_utilization_pct"),
        spot_exposure_usdt=s.get("spot_exposure_usdt"),
        spot_stop_risk_usdt=s.get("spot_stop_risk_usdt"),
        spot_symbols_without_stop=list(s.get("spot_symbols_without_stop") or []),
        spot_allocation_util_pct=s.get("spot_allocation_utilization_pct"),
        spot_exposure_unknown=bool(s.get("spot_exposure_unknown")),
        spot_symbols_unknown_price=list(s.get("spot_symbols_unknown_price") or []))


# --------------------------------------------------------------------------- tablo satırları
POSITION_COLUMNS = ["Sembol", "Piyasa", "Yön", "Coin adedi", "Giriş", "Mark/Son", "Kald.",
                    "Notional (USDT)", "Teminat (USDT)", "Stop", "TP", "Likidasyon",
                    "Açılış ücreti", "Funding", "Brüt K/Z", "Tah. kapanış ücreti",
                    "Net K/Z (USDT)", "Net K/Z (%)", "Açılış", "İşlem ID"]

# HİZALAMA SÖZLEŞMESİ — sunucu render'ı (`app._positions_table`) ve polling JS'i AYNI listeyi
# kullanır. Önce sunucu `range(3, 18)`, JS ise `i >= 3` diyordu → «Açılış» ve «İşlem ID» metin
# sütunları 7 saniyelik ilk polling'den sonra sağa hizalanıyordu (ölçülen: 60 vs 68 `td.num`).
POSITION_NUM_COLS = tuple(range(3, 18))      # sayısal hizalama (sağa yaslı, tabular-nums)
POSITION_PNL_COLS = (16, 17)                 # YALNIZ net K/Z alanları `up/dn/flat` renk alır


def position_row(v: PositionView) -> list[Any]:
    """Bir açık pozisyonun tablo satırı. `Coin adedi` USDT DEĞİL, coin/kontrat adedidir."""
    return [
        v.symbol, v.market, v.side, fmt_qty(v.qty),
        fmt_qty(v.entry_price, 8),
        ("—" if v.mark_price is None else fmt_qty(v.mark_price, 8)),
        ("—" if v.market == "SPOT" else f"{v.leverage}x"),
        fmt_money(v.notional, signed=False, currency=""),
        fmt_money(v.initial_margin, signed=False, currency=""),
        ("—" if v.stop is None else fmt_qty(v.stop, 8)),
        ("—" if v.take_profit is None else fmt_qty(v.take_profit, 8)),
        ("—" if v.liquidation_price is None else fmt_qty(v.liquidation_price, 8)),
        fmt_money(-abs(v.entry_fee), currency=""),
        fmt_money(v.funding_net, currency=""),
        fmt_money(v.gross_unrealized, currency=""),
        fmt_money(-abs(v.exit_fee_est), currency=""),
        fmt_money(v.net_unrealized),
        ("—" if v.net_unrealized_pct is None else fmt_pct(v.net_unrealized_pct)),
        v.opened_at, v.trade_id,
    ]


NO_DATA = "Veri yok"


@dataclass(frozen=True)
class SummaryCard:
    """Kart modeli: `value` MAKİNE için ham sayı, `display` İNSAN için biçimlenmiş metin.

    Panel HTML'i `display`'i OLDUĞU GİBİ basar. Daha önce zaten biçimlenmiş metin ikinci kez
    `money_html()`'e veriliyor, `Decimal("+$2.86")` çözülemediği için sessizce `$0.00`'a düşüyordu.
    """
    key: str
    title: str
    value: Any            # float | None  (None → hesaplanamadı)
    display: str
    kind: str             # money | pct | ratio | pair | count | text
    sub: str = ""

    @property
    def signed(self) -> bool:
        """Renk/işaret uygulanacak mı? Oran ve sayaçlar nötrdür."""
        return self.kind in ("money", "pct_signed")

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "value": self.value,
                "display": self.display, "kind": self.kind, "sub": self.sub}


def profit_factor_value(pv: PortfolioView) -> float | None:
    """Profit factor'ün SAYISAL değeri — `inf`/`NaN` ASLA döndürmez (JSON sınırı için).

    Sonsuzluk bilgisi sayıda değil, `pnl.profit_factor_state()`'in ikinci alanında taşınır.
    """
    return profit_factor_state(pv)[0]


def _pf_display(pf_state: str, pf: float | None) -> str:
    """Profit factor gösterimi: `$` YOK, `+` YOK — bu bir ORANDIR.

    `∞` YALNIZ gerçekten sonsuz olduğunda (zarar 0, kâr > 0) yazılır. `0/0` tanımsızdır ve
    `∞` DEĞİL `Veri yok` gösterilir — aksi hâlde hiç kâr etmemiş bir bot "sonsuz iyi" görünürdü.
    """
    if pf_state == PF_POSITIVE_INFINITY:
        return "∞"
    return NO_DATA if pf is None else f"{pf:.2f}"


def summary_cards(pv: PortfolioView, summary: dict | None = None) -> list[SummaryCard]:
    """Genel kâr/zarar + risk özeti. Hesaplanamayan alan sessizce `0` DEĞİL, `Veri yok` olur."""
    s = summary or {}

    def pct1(x, nd=1):
        return NO_DATA if x is None else f"%{float(x):.{nd}f}"

    _pf, _pf_state = profit_factor_state(pv)
    # Bozuk kayıt varsa toplam kartı `canonical_summary` ile AYNI kuralla «Veri yok» olur —
    # dışlanmış kayıtlarla hesaplanmış sayı (sahte `$0.00` / eksik toplam) GÖSTERİLMEZ.
    bad = money_totals_unavailable_reason(pv)

    def money(x, *blocked_by: str):
        """(value, display, ek-altyazı) — `blocked_by` anahtarlarından biri `bad`ta ise Veri yok."""
        reasons = [bad[k] for k in blocked_by if k in bad]
        if reasons:
            return None, NO_DATA, " · ⚠ " + " · ".join(reasons)
        return finite_float_or_none(x), fmt_money(x), ""

    rt_v, rt_d, rt_n = money(pv.realized_today, "realized")
    ra_v, ra_d, ra_n = money(pv.realized_total, "realized")
    op_v, op_d, op_n = money(pv.open_net_unrealized, "unrealized")
    tn_v, tn_d, tn_n = money(pv.total_net, "realized", "unrealized")
    cards = [
        SummaryCard("today_realized_net_usdt", "Bugün gerçekleşen net K/Z", rt_v, rt_d, "money",
                    "kapanan işlemler (UTC gün)" + rt_n),
        SummaryCard("all_time_realized_net_usdt", "Toplam gerçekleşen net K/Z", ra_v, ra_d, "money",
                    "ücret + funding dahil" + ra_n),
        SummaryCard("open_net_usdt", "Açık pozisyon net K/Z", op_v, op_d, "money",
                    "tahmini kapanış ücreti düşülmüş" + (" · ⚠ fiyat yok" if pv.any_stale_price else "") + op_n),
        SummaryCard("total_net_usdt", "Toplam net K/Z", tn_v, tn_d, "money", "gerçekleşen + açık" + tn_n),
        SummaryCard("win_loss", "Kazanan / Kaybeden", None, f"{pv.wins} / {pv.losses}", "pair",
                    f"başa baş {pv.breakeven} · kapanmış {pv.closed_trades}"),
        SummaryCard("win_rate_pct", "Kazanma oranı", finite_float_or_none(pv.win_rate), pct1(finite_float_or_none(pv.win_rate)), "pct",
                    "kazanan / (kazanan + kaybeden) — başa baş HARİÇ"),
        SummaryCard("profit_factor", "Profit factor", _pf, _pf_display(_pf_state, _pf),
                    "ratio", "brüt kâr / brüt zarar — oran, para birimi değil"),
        SummaryCard("max_drawdown_pct", "Maks. drawdown", finite_float_or_none(pv.max_drawdown),
                    pct1(finite_float_or_none(pv.max_drawdown), 2), "pct", "risk motoru (risk.json)"),
    ]
    if summary is not None:
        mu, sr = s.get("margin_utilization_pct"), s.get("open_stop_risk_usdt")
        partial = stop_risk_note(s)
        stale_note = risk_stale_note(s)
        cards += [
            SummaryCard("open_futures_margin_usdt", "Açık futures teminatı", s.get("open_futures_margin_usdt"),
                        NO_DATA if s.get("open_futures_margin_usdt") is None
                        else fmt_money(s["open_futures_margin_usdt"], signed=False, currency="") + " USDT",
                        "money_plain", "kullanılan başlangıç marjı"),
            SummaryCard("margin_utilization_pct", "Teminat kullanımı", mu, pct1(mu),
                        "pct", "açık teminat / futures özkaynak"),
            SummaryCard("open_stop_risk_usdt", "Açık stop riski", sr,
                        NO_DATA if sr is None else fmt_money(sr, signed=False, currency="") + " USDT",
                        "money_plain", "stop'a kadar BRÜT tahmini kayıp (ücret hariç)" + partial),
            # BIRLESIK KONSERVATIF GOZLEM — pay: futures stop riski + STOPSUZ spot TAM notional.
            # Stop-riski DEGILDIR ve hicbir kapi tarafindan UYGULANMAZ; asagidaki oran TANISALDIR.
            # Gercek kapasite kapisi: `futures_risk_budget_utilization_pct` (futures stop riski / butce).
            SummaryCard("risk_engine_reserved_usdt", "Birleşik konservatif gözlem",
                        s.get("risk_engine_reserved_usdt"),
                        NO_DATA if s.get("risk_engine_reserved_usdt") is None
                        else fmt_money(s["risk_engine_reserved_usdt"], signed=False, currency="") + " USDT",
                        "money_plain",
                        "futures stop riski + STOPSUZ spot notional — stop riski DEĞİL, "
                        "limit olarak UYGULANMAZ (risk.json → total_open_risk_usdt)" + stale_note),
            SummaryCard("open_risk_budget_utilization_pct",
                        "Birleşik gözlem / futures bütçesi (tanısal)",
                        s.get("open_risk_budget_utilization_pct"),
                        pct1(s.get("open_risk_budget_utilization_pct")), "pct",
                        "TANISAL ORAN — LİMİT İHLALİ DEĞİL: pay stopsuz spot notional içerir; "
                        "uygulanan kapı «Futures bütçe kullanımı» kartıdır "
                        "(diagnostic_ratio_not_enforced) · " + risk_budget_sub(s) + stale_note),
            # --- ÜÇ AYRI KAVRAM: spot notional ile futures stop riski AYNI KARTTA TOPLANMAZ ---
            SummaryCard("futures_stop_risk_usdt", "Futures stop riski", s.get("futures_stop_risk_usdt"),
                        _usdt_or_no_data(s.get("futures_stop_risk_usdt")), "money_plain",
                        "yalnız futures pozisyonları — kabul kapısının kovası" + stale_note),
            SummaryCard("futures_risk_budget_utilization_pct", "Futures bütçe kullanımı",
                        s.get("futures_risk_budget_utilization_pct"),
                        pct1(s.get("futures_risk_budget_utilization_pct")), "pct",
                        "futures stop riski / azami risk bütçesi" + stale_note),
            SummaryCard("spot_exposure_usdt", "Spot maruziyeti", s.get("spot_exposure_usdt"),
                        _usdt_or_no_data(s.get("spot_exposure_usdt")), "money_plain",
                        "açık spot notional — RİSK DEĞİL" + spot_stop_note(s) + stale_note),
            SummaryCard("unbounded_spot_warning", "Stopsuz spot maruziyeti",
                        s.get("spot_unbounded_notional_usdt"),
                        (s.get("unbounded_spot_warning")
                         or ("yok" if s.get("spot_unbounded_notional_usdt") in (None, 0)
                             else _usdt_or_no_data(s.get("spot_unbounded_notional_usdt")))),
                        "warn" if s.get("unbounded_spot_warning") else "money_plain",
                        "stop ile SINIRLANMAMIŞ maruziyet — tam notional fail-safe görünürlüğü"),
            SummaryCard("futures_contributed_capital_usdt", "Futures katkı sermayesi",
                        s.get("futures_contributed_capital_usdt"),
                        _usdt_or_no_data(s.get("futures_contributed_capital_usdt")), "money_plain",
                        "PAPER katkı tabanı — KÂR DEĞİL; güncel equity = katkı + toplam PnL"),
            SummaryCard("spot_contributed_capital_usdt", "Spot katkı sermayesi",
                        s.get("spot_contributed_capital_usdt"),
                        _usdt_or_no_data(s.get("spot_contributed_capital_usdt")), "money_plain",
                        "PAPER katkı tabanı — KÂR DEĞİL"),
            SummaryCard("total_contributed_capital_usdt", "Toplam katkı sermayesi",
                        s.get("total_contributed_capital_usdt"),
                        _usdt_or_no_data(s.get("total_contributed_capital_usdt")), "money_plain",
                        "futures + spot PAPER katkısı; net PnL ve güncel equity AYRI alanlardır"),
            SummaryCard("spot_allocation_utilization_pct", "Spot allocation kullanımı",
                        s.get("spot_allocation_utilization_pct"),
                        pct1(s.get("spot_allocation_utilization_pct")), "pct",
                        _spot_cap_sub(s) + stale_note),
        ]
    return cards


def _usdt_or_no_data(v: Any) -> str:
    """Sonlu sayı → «x USDT»; None/NaN → «Veri yok». Sessiz 0 ÜRETİLMEZ."""
    f = finite_float_or_none(v)
    return NO_DATA if f is None else fmt_money(f, signed=False, currency="") + " USDT"


def spot_stop_note(s: dict) -> str:
    """Stopsuz spot ve GEÇERSİZ FİYAT durumu AÇIKÇA yazılır — 'riski azaldı' izlenimi verilmez."""
    out = ""
    bad = list(s.get("spot_symbols_unknown_price") or [])
    if s.get("spot_exposure_unknown"):
        out += (" · ⚠ fiyat geçersiz (" + ", ".join(str(x) for x in bad[:3])
                + ") — maruziyet ölçülemedi, yeni spot giriş reddedilir")
    syms = list(s.get("spot_symbols_without_stop") or [])
    if syms:
        out += " · stopsuz (stopla sınırlanmamış): " + ", ".join(str(x) for x in syms[:4])
    return out


def _spot_cap_sub(s: dict) -> str:
    cap = finite_float_or_none(s.get("spot_allocation_max_usdt"))
    if cap is None:
        return s.get("unavailable_reason", {}).get("spot_allocation_utilization_pct", NO_DATA)
    return "azami %s USDT — spot notional tavanı (stop riskinden AYRI kapı)" % fmt_money(cap, signed=False, currency="")


def risk_budget_sub(s: dict) -> str:
    """Risk bütçesi kartının altyazısı — TABAN AÇIKÇA YAZILIR.

    Operatör `%20.3` ile `%21.5` arasındaki farkın nereden geldiğini kartta görebilmelidir:
    motor `starting_equity` tabanını kullanırken panel canlı equity'yi gösterirse aynı büyüklük
    iki farklı sayı olur. Taban bilinmiyorsa oran zaten `Veri yok`tur.
    """
    if s.get("risk_budget_max_usdt") is None:
        return s.get("unavailable_reason", {}).get("risk_budget_max_usdt", NO_DATA)
    label = {"starting_equity": "Başlangıç özkaynağı tabanı",
             "live_equity": "Canlı özkaynak tabanı"}.get(str(s.get("risk_equity_basis_kind") or ""),
                                                         "Özkaynak tabanı")
    out = "azami %s USDT" % fmt_money(s["risk_budget_max_usdt"], signed=False, currency="")
    basis = s.get("risk_equity_basis_usdt")
    if basis is not None:
        out += " · %s: %s USDT" % (label, fmt_money(basis, signed=False, currency=""))
    return out


def risk_stale_note(s: dict) -> str:
    """Risk anlık görüntüsü bayatsa/zamanı bilinmiyorsa kartın altyazısına eklenen uyarı.

    Değer GİZLENMEZ (operatör son bilinen rezervasyonu görmeye devam eder) fakat «bayat» etiketi
    ZORUNLUDUR — bayat risk verisi taze gibi sunulamaz.
    """
    st = s.get("risk_snapshot_state")
    if st == LIVE_STALE:
        return " · ⚠ Risk verisi güncel değil (%s)" % _age_text(s.get("risk_snapshot_age_s"))
    if st == LIVE_UNKNOWN:
        return " · ⚠ Risk verisi yaşı bilinmiyor"
    return " · risk verisi %s önce" % _age_text(s.get("risk_snapshot_age_s"))


def stop_risk_note(s: dict) -> str:
    """Stop riski toplamına giremeyen pozisyonları AYRI AYRI etiketler (eksik/bozuk/geçersiz)."""
    parts = []
    for key, label in (("positions_without_stop", "stop'suz"),
                       ("positions_stop_malformed", "stop değeri bozuk"),
                       ("positions_invalid_qty", "miktar/giriş geçersiz")):
        n = int(s.get(key) or 0)
        if n:
            parts.append(f"{n} {label}")
    return (" · ⚠ toplama girmeyen: " + ", ".join(parts)) if parts else ""


def _age_text(sec: Any) -> str:
    if sec is None:
        return "bilinmiyor"
    sec = int(sec)
    if sec < 90:
        return f"{sec}sn"
    if sec < 5400:
        return f"{sec // 60}dk"
    if sec < 172800:
        return f"{sec // 3600}sa"
    return f"{sec // 86400}g"




def build(state_positions: list[dict], trades: list[dict], chief: dict | None, *,
          marks: dict[str, Any] | None = None, fees: Any = None, today: str | None = None,
          max_drawdown_pct: Any = None, freshness: Freshness | None = None,
          futures_equity: Any = None, spot_equity: Any = None, risk_state: dict | None = None,
          as_of: str | None = None, risk_age_s: Any = None, risk_stale_s: Any = None,
          futures_ledger_doc: dict | None = None, spot_ledger_doc: dict | None = None) -> dict:
    """Panelin TEK giriş noktası: her bölüm için hazır, tutarlılığı denetlenmiş model.

    `summary` KANONİK özettir; HTML sayfası da `/api/live/summary` de AYNI sözlüğü kullanır,
    böylece iki yüzey farklı sayı gösteremez.
    """
    pv = portfolio_view(state_positions, trades, marks=marks, fees=fees, today=today,
                        max_drawdown_pct=max_drawdown_pct)
    fr = freshness.to_dict() if freshness else None
    summary = canonical_summary(pv, futures_equity=futures_equity, spot_equity=spot_equity,
                                futures_ledger_doc=futures_ledger_doc,
                                spot_ledger_doc=spot_ledger_doc,
                                risk_state=risk_state, as_of=as_of, source_freshness=fr,
                                risk_age_s=risk_age_s, risk_stale_s=risk_stale_s)
    rows = [position_row(v) for v in pv.positions]
    issues = check_invariants(pv, table_rows=len(rows))
    return {"portfolio": pv, "chief": chief_view(chief, pv, summary), "rows": rows,
            "columns": POSITION_COLUMNS, "cards": summary_cards(pv, summary), "summary": summary,
            "inconsistencies": [i.__dict__ for i in issues], "freshness": fr}


# ============================================================================ SABIT GIRIS EVRENI
#: On coinlik analiz ekraninin kolonlari. Her kolon TEK bir soruya cevap verir.
UNIVERSE_COLUMNS = ("Sembol", "Durum", "Karar", "Yön", "Rejim", "Kurulum", "Giriş tetiği",
                    "Fikri geçersiz kılan", "Stop", "Hedefler", "Net E[R]", "P(kazanç)",
                    "Veri", "Açılmama nedeni")
UNIVERSE_NUM_COLS = (8, 10, 11)
UNIVERSE_BADGE_COLS = (1, 2)
UNIVERSE_SYMBOL_COL = 0

UNIVERSE_COLUMN_NOTES = {
    "Durum": "AÇIK = defterde pozisyon var · İZLENİYOR = evrende, pozisyon yok · "
             "EVREN DIŞI = yalnız çıkış yönetimi (yeni giriş kapalı)",
    "Karar": "Coin head kararı. AL/SAT gerçek pozisyon anlamına GELMEZ; risk ve ekonomi "
             "kapıları sonrasında açılıp açılmadığı «Açılmama nedeni» kolonundadır.",
    "P(kazanç)": "İstatistiksel tahmin (kalibre); konsensüs gücü DEĞİLDİR.",
    "Net E[R]": "Maliyet sonrası beklenen R — işlem öncesi tahmindir, gerçekleşen sonuç değildir.",
    "Veri": "Karar çerçevesinin geldiği piyasa. PERP = USDⓈ-M perpetual (doğru kaynak). "
            "SPOT = perpetual çerçeve alınamadı; analiz sürer, YENİ GİRİŞ kapalıdır.",
    "Açılmama nedeni": "Bu turda pozisyon açılmadıysa kapının kodu. Boş = kapı reddi kaydedilmedi.",
}

_UNIVERSE_STATUS = {"OPEN": "AÇIK", "WATCHED": "İZLENİYOR", "OUTSIDE": "EVREN DIŞI"}


def _plan_of(head: dict) -> dict:
    """Karar hangi piyasadaysa o planı ver; yoksa boş sözlük (uydurma alan YOK)."""
    if not isinstance(head, dict):
        return {}
    mt = str(head.get("market_type") or "").lower()
    if mt.startswith("spot"):
        return head.get("spot_plan") or {}
    return head.get("futures_plan") or head.get("spot_plan") or {}


def universe_table(*, universe: list[str] | None, heads: list[dict] | None,
                   positions: list[dict] | None, provenance: dict | None,
                   risk_decisions: list[dict] | None) -> dict[str, Any]:
    """On coinlik analiz ekraninin KANONIK yuku.

    Satir kumesi = `universe` ∪ acik pozisyon sembolleri. Evren disinda kalan acik pozisyon
    EVREN DISI olarak isaretlenir ve satiri DUSURULMEZ: cikis yonetimi surdugu icin panelde
    gorunmesi gerekir.

    Hicbir deger burada TURETILMEZ: kararlar `coin_heads.json`, ret gerekceleri `risk.json`,
    veri kimligi `frame_provenance.json` dosyalarindan OLDUGU GIBI okunur. Bir alan yoksa
    bos birakilir — panel eksik veriyi doldurmaz.
    """
    uni = [str(s) for s in (universe or [])]
    uni_set = set(uni)
    open_by = {str(p.get("symbol") or ""): p for p in (positions or []) if isinstance(p, dict)}
    head_by = {str(h.get("symbol") or ""): h for h in (heads or []) if isinstance(h, dict)}
    prov_by = ((provenance or {}).get("by_symbol") or {}) if isinstance(provenance, dict) else {}
    # Ayni sembolun SON kaydi nihai durumdur (tur icinde birden fazla kapi yazabilir).
    block_by: dict[str, dict] = {}
    for e in (risk_decisions or []):
        if isinstance(e, dict) and e.get("symbol"):
            block_by[str(e["symbol"])] = e

    order = uni + [s for s in open_by if s not in uni_set]
    rows, meta = [], []
    for sym in order:
        h = head_by.get(sym) or {}
        plan = _plan_of(h)
        pos = open_by.get(sym)
        status = "OPEN" if pos else ("WATCHED" if sym in uni_set else "OUTSIDE")
        if pos and sym not in uni_set:
            status = "OUTSIDE"
        pr = prov_by.get(sym) or {}
        market = str(pr.get("market") or "")
        data_cell = {"USDM_PERP": "PERP", "SPOT": "SPOT"}.get(market, "—")
        blk = block_by.get(sym) or {}
        why = str(blk.get("block_code") or "")
        detail = str(blk.get("block_detail") or "")
        if why and detail:
            why = f"{why} ({detail})"
        if not why:
            # Siralamaya HIC girmemis aday: gerekce risk gunlugunde degil, coin head'in
            # kendi `no_trade_reason` alanindadir. Ikisi ayri asamadir ve ikisi de
            # gosterilmelidir — aksi halde "acilmadi ama neden belli degil" satiri kalir.
            why = str(h.get("no_trade_reason") or "")
        targets = plan.get("targets") or h.get("targets") or []
        rows.append([
            sym,
            _UNIVERSE_STATUS[status],
            str(h.get("verdict") or "—"),
            str(h.get("direction") or "—"),
            str(h.get("regime") or "—"),
            str(plan.get("entry_type") or "—"),
            str(plan.get("entry_trigger") or h.get("entry_trigger") or "—"),
            str(plan.get("invalidation") or h.get("invalidation") or "—"),
            _cell_num(plan.get("stop") or h.get("stop"), 6),
            ", ".join(_cell_num(t, 6) for t in targets) if targets else "—",
            _cell_num(h.get("expected_r"), 2),
            _cell_pct_signal(h.get("p_win"), 0),
            data_cell,
            why or "—",
        ])
        meta.append({"symbol": sym, "status": status, "in_universe": sym in uni_set,
                     "entry_ok": bool(pr.get("entry_ok")) if pr else None,
                     "data_reason": str(pr.get("reason") or ""),
                     "has_head": bool(h), "open": bool(pos)})

    outside = [m["symbol"] for m in meta if m["status"] == "OUTSIDE"]
    no_head = [m["symbol"] for m in meta if m["in_universe"] and not m["has_head"]]
    spot_framed = [m["symbol"] for m in meta if m["in_universe"] and m["entry_ok"] is False]
    return {"columns": list(UNIVERSE_COLUMNS), "rows": rows, "meta": meta,
            "num_cols": list(UNIVERSE_NUM_COLS), "badge_cols": list(UNIVERSE_BADGE_COLS),
            "symbol_col": UNIVERSE_SYMBOL_COL, "column_notes": dict(UNIVERSE_COLUMN_NOTES),
            "universe": uni, "universe_size": len(uni),
            "coverage": {"universe_with_decision": len(uni) - len(no_head),
                         "universe_total": len(uni), "missing_decision": no_head,
                         "outside_universe_open": outside,
                         "entry_blocked_on_data": spot_framed},
            "generated_at": str((provenance or {}).get("generated_at") or "") if isinstance(provenance, dict) else ""}


__all__ = ["ChiefView", "Freshness", "LIVE_OK", "LIVE_STALE", "LIVE_UNKNOWN", "NO_DATA",
           "NOT_APPLICABLE", "UNKNOWN_EXPECTANCY", "COIN_HEAD_COLUMNS", "coin_head_table",
           "POSITION_COLUMNS", "POSITION_NUM_COLS", "POSITION_PNL_COLS", "SummaryCard", "build",
           "chief_view", "position_row", "profit_factor_value", "risk_budget_sub",
           "risk_stale_note", "stop_risk_note", "summary_cards",
           "UNIVERSE_COLUMNS", "UNIVERSE_COLUMN_NOTES", "universe_table"]

# -*- coding: utf-8 -*-
"""BOT KARNESİ — beş kâğıt defterin (ana bot, T2, M2, Box, formasyon) GERÇEKLEŞMİŞ işlemlerinden, maliyet (ücret + kayma +
funding) SONRASI sonuç. SALT OKUNUR: defter dosyalarına yazmaz, strateji/parametre değiştirmez, işlem açmaz.

Soru tek: "Hangi bot, bugüne kadar kapattığı işlemlerde para kazandırdı ve bu tesadüf olabilir mi?"

* R = net kâr/zarar (ücret, kayma ve funding dahil) ÷ girişteki risk (giriş–ilk stop mesafesi × miktar). Stopsuz işlemin
  R'si 0 yazılır ve ayrıca sayılır.
* Ortalama R için %95 bootstrap aralığı (deterministik tohum). Hüküm: 30 işlemden azsa `VERİ YETERSİZ`; aralığın tamamı
  0'ın altındaysa `ZARARDA (kanıtlı)`, tamamı üstündeyse `KÂRDA (kanıtlı, PAPER)`, değilse `BELİRSİZ`.
* PAPER sonucudur: gerçek emir defteri, kısmi dolum ve gecikme yoktur. Kesinti içinde kapanan ya da funding'i tam
  mutabık olmayan işlemler ayrıca sayılır; hüküm bunları DÜZELTMEZ, yalnız görünür kılar.
* Mum varyasyonları (C4): `candle:<id>` işlemleri ayrıca varyasyon başına (`by_variation`) — aynı hüküm kuralı, gözlem
  bayrağı ve laboratuvarın doğrulama dönemi (OOS) ortalaması/aralığı. En az 30 işlemde PAPER ortalaması laboratuvar OOS
  aralığının alt ucunun altındaysa `LAB_DRIFT` (canlı davranış laboratuvardan sapıyor). Sıkı eşi C4S aynı satırları
  yazar; anlık görüntüde kip varsa (`verdict_mode`, `verdict_used`) satıra eklenir.
* ÖĞRENME MODU (2026-09-28, öğrenme modu): `state/learning_mode.json` (`learning_mode_since`, ilk aktif an) varsa her
  defter için ayrıca ÖNCE / SONRA (girişi o andan önce/sonra açılan işlemler) ve SONRA içinde üç sütun: politika işlemleri
  (taban kurallar da açardı), öğrenme-ekstra işlemler (`features.learning.learning_unlocked_by` dolu) ve karşı-olgusallar
  (açılmayan geçerli sinyallerin etiketli sonucu — P&L'e ASLA girmez, USDT'si yoktur). R esastır: sonrası USDT öğrenme
  ölçeğindedir (%0,5 risk, slot boyutu) ve öncesiyle karşılaştırılmaz. Dosya yoksa karne çıktısı AYNEN eskisidir.
* KARŞI-OLGUSAL NET R (2026-09-29): karşı-olgusal ort. R, aralık ve hüküm yalnız NET etiketli kayıtlardan (`r_net`,
  `cf_label_v2`: defterin ücret/kayma/funding modeliyle aynı barlarda yeniden oynatma) — gerçek işlemlerle aynı taban.
  Brüt ortalama (`mean_r_gross`) yalnız bilgi; net'i olmayan eski kayıtlar ve kapalı-biçim tahminleri ayrı sayılır.
* İHTİYATLI KARŞI-OLGUSAL (2026-10-01, `cf_aux_v1`, yalnız bilgi): net ortalamanın YANINDA, yardımcı etiketi olan
  kayıtların `outcome.aux.r_net_conservative` ortalaması (giriş barı denetimi + gerçek defterin örneklenmiş stop dolumu
  tahmini) ve kapsamı `n`. Mevcut sayılar (ortalama, aralık, hüküm) DEĞİŞMEZ; alanı olmayan eski kayıtlar kapsama girmez.
* KAYDA ALINAN EKSTRA (2026-10-03, sahip kararı): öğrenme modunun `extra_entries: record_selectivity` kipinde AÇILMAYAN
  seçicilik-ekstra adaylar (karşı-olgusal ilk nedeni `LEARNING_RECORD_ONLY`) defter başına AYRI sınıf: kayıt sayısı ve
  etiketlendikçe net R. «karşı-olgusal» sütunu bunları İÇERMEZ. P&L'e GİRMEZ. Sayı KAYIT sayısıdır, açılmış ekstra işlem
  sayısıyla bire bir kıyaslanmaz (T2/M2'de koşul sürdükçe her günlük bar yeni kayıttır).
* AYLIK HEDEF (2026-10-03, yalnız rapor): sahibin hedefi her algoritmanın kendi kâğıt bakiyesinde ayda en az +%1 net.
  Defter başına içinde bulunulan UTC takvim ayında (bugüne kadar) ve son 30 günde KAPANAN işlemlerin net sonucu (ücret,
  kayma ve funding SONRASI) başlangıç bakiyesinin %'si olarak, işlem sayısı ve hedefe uzaklık. Açık pozisyonların
  gerçekleşmemiş sonucu dahil DEĞİLDİR. Bu bir ölçümdür; kâr iddiası ya da garantisi değildir. Yalnız komut satırı
  (`main`) yazar; `scorecard()` sözlüğü değişmez. Ana botun spot defteri (`state/spot_ledger.json`) varsa
  "Ana bot · spot" satırı olarak bu bloğa da girer (sürekli öğrenme motoru tasarımı §7.5).
* GÜNLÜK HEDEF (`--daily [--days N]`, sürekli öğrenme motoru tasarımı §7.7, yalnız ölçüm): son N UTC günü için defter
  başına ve toplamda (1) KAYIT görünümü: o gün (`closed_at`) kapanan kayıtların net sonucu, (2) CÜZDAN görünümü:
  hareketin kendi zaman damgasının o gününe düşen PNL + FEE + FUNDING + LIQ_FEE + TAX hareketleri (TRANSFER ayrı;
  hareketler ledger'ın 2000'lik rotasyonuyla düşmüşse gün `eksik`), (3) açık pozisyonların ŞİMDİKİ gerçekleşmemiş
  sonucu (`LEDGER_MARK`: vadeli defterin kendi `last_price`'ı; spot için mark zinciri position_path ≤ 60 dk →
  HistoryStore son kapanmış spot barı ≤ 24 saat → aynı sembolün vadeli `last_price`'ı). Spot dahildir. Hesap motordan
  BAĞIMSIZDIR (worker'ın kendi muhasebe sınıflarıyla, salt okunur): sahip motor olmadan doğrulayabilsin diye; aynı
  tanımlı sayılar motorun `daily_target.ledger_day_views` çıktısıyla eşit olmalıdır (test). Bu çıktı hüküm VERMEZ:
  gün satırları yalnız gerçekleşmiş sonuçtur; gün hükmü yalnız motorun ölçülmüş anlık görüntülerle hesapladığı ve
  kesinleşmiş (KESİN) MTM günlerinde, hedef hükmü yalnız aylık kayıtlı bakışta verilir (`engine-status`).
* D4 etiketi (2026-10-05): eski "Trend 4h (gözlem, kanıtlanmadı)" etiketi defterin gerçek adıyla "D4 Donchian 4h".

Kullanım:
    python scripts/bot_scorecard.py --state <state klasörü> [--since 2026-09-01] [--learning-since <ISO>] [--out karne.json]
        [--now <ISO, aylık/günlük hedefin «şimdi»si; varsayılan UTC şimdi>] [--daily [--days N]]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.accounting import FuturesLedgerV2, SpotLedger  # noqa: E402
from tradingbot.core import from_iso  # noqa: E402
from tradingbot.pattern_trader.report import MIN_TRADES_FOR_VERDICT, _funding_coverage, _r_stats  # noqa: E402

LEDGER_FILE = "futures_ledger.json"
#: defter klasörü (state altında) → görünen ad; "" = ana bot (state kökündeki defter)
BOOKS = {"": "Ana bot", "strategy_paper": "T2", "strategy_paper_m2": "M2 (TSMOM28)", "strategy_paper_box": "Box",
         "pattern_trader": "Formasyon", "strategy_paper_trend4h": "D4 Donchian 4h",
         "strategy_paper_candle4h": "C4 Mum varyasyonları (PAPER)",
         "strategy_paper_candle4h_strict": "C4S Mum varyasyonları 4h (sıkı, PAPER)"}
V_THIN, V_LOSS, V_WIN, V_OPEN = "VERİ YETERSİZ", "ZARARDA (kanıtlı)", "KÂRDA (kanıtlı, PAPER)", "BELİRSİZ"
#: Mum varyasyonu işlemlerinin kurulum öneki (`candle_book.SETUP_PREFIX`).
CANDLE_PREFIX = "candle:"
LAB_DRIFT = "LAB_DRIFT"
#: ÖĞRENME MODU (2026-09-28): ilk aktif an dosyası (motor yazar), defter başına karşı-olgusal dosyası ve ana botun gölge
#: defteri (karşı-olgusallar `book == "main"`). Sınıflar: politika (taban da açardı) / öğrenme-ekstra.
LEARNING_SINCE_FILE = "learning_mode.json"
CF_FILE = "counterfactual_trades.json"
MAIN_SHADOW_FILE = "shadow_book.json"
POLICY, LEARNING_EXTRA = "policy", "learning_extra"
#: KAYDA ALINAN EKSTRA (2026-10-03): karşı-olgusalın ilk nedeni (`learning_mode.LEARNING_RECORD_ONLY`) ve karne sınıfı.
RECORD_ONLY_REASON = "LEARNING_RECORD_ONLY"
RECORDED_EXTRA = "recorded_extra"
#: AYLIK HEDEF (2026-10-03, sahip): ayda en az +%1 net (kendi kâğıt bakiyesinde). Yalnız rapor.
MONTHLY_TARGET_PCT = 1.0
#: GÜNLÜK HEDEF (2026-10-05, motor tasarımı §7.7): ana botun spot defteri ve günlük ölçüm (yalnız gerçekleşmiş + şimdiki
#: gerçekleşmemiş; hüküm yok).
SPOT_FILE = "spot_ledger.json"
DAILY_TARGET_PCT = 1.0
DAILY_PNL_KINDS = ("PNL", "FEE", "FUNDING", "LIQ_FEE", "TAX")
ENTRIES_KEEP = 2000
DAILY_NAMES = {"main_fut": "Ana bot · vadeli", "main_spot": "Ana bot · spot"}
#: spot mark zinciri (motorla aynı mühürlü kural; kod bağımsız)
PATH_MARK_MAX_AGE = timedelta(minutes=60)
PATH_MARK_FUTURE_TOL = timedelta(minutes=2)
PATH_TAIL_BYTES = 4 << 20
BAR_MARK_MAX_AGE = timedelta(hours=24)
BAR_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def find_books(state: Path) -> dict[str, Path]:
    """Bilinen defterler + state altında `futures_ledger.json` taşıyan başka klasörler (adıyla)."""
    out: dict[str, Path] = {}
    for sub in BOOKS:
        p = state / sub / LEDGER_FILE if sub else state / LEDGER_FILE
        if p.exists():
            out[sub] = p
    for p in sorted(state.glob(f"*/{LEDGER_FILE}")):
        out.setdefault(p.parent.name, p)
    return out


def verdict(st: dict[str, Any]) -> str:
    if st["n"] < MIN_TRADES_FOR_VERDICT or not st.get("ci95_mean_r"):
        return V_THIN
    lo, hi = st["ci95_mean_r"]
    if hi < 0:
        return V_LOSS
    if lo > 0:
        return V_WIN
    return V_OPEN


def _group(trades: list, key) -> dict[str, dict[str, Any]]:
    groups: dict[str, list] = {}
    for t in trades:
        groups.setdefault(str(key(t) or "?"), []).append(t)
    rows = {}
    for k, ts in groups.items():
        rs = [float(t.r_multiple) for t in ts]
        rows[k] = {"n": len(ts), "net_usdt": round(sum(float(t.pnl) for t in ts), 4),
                   "mean_r": round(sum(rs) / len(rs), 4), "win_rate": round(sum(1 for r in rs if r > 0) / len(rs), 4)}
    return dict(sorted(rows.items(), key=lambda kv: kv[1]["net_usdt"]))


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def by_variation(trades: list) -> dict[str, dict[str, Any]]:
    """Mum varyasyonu başına (`candle:<id>`): n, ortalama R, %95 aralık, kazanma oranı, hüküm; girişteki anlık görüntüden
    (`features.candle_variation`) gözlem bayrağı ve laboratuvarın OOS ortalaması/aralığı. `LAB_DRIFT`: en az
    `MIN_TRADES_FOR_VERDICT` işlemde PAPER ortalaması laboratuvar OOS aralığının alt ucunun altında."""
    groups: dict[str, list] = {}
    for t in trades:
        st_ = str(t.setup_type or "")
        if st_.startswith(CANDLE_PREFIX):
            groups.setdefault(st_[len(CANDLE_PREFIX):], []).append(t)
    out: dict[str, dict[str, Any]] = {}
    for vid, ts in sorted(groups.items()):
        st = _r_stats([float(t.r_multiple) for t in ts])
        snaps = [((t.features or {}).get("candle_variation") or {}) for t in ts]
        snaps = [s_ for s_ in snaps if isinstance(s_, dict)]
        last = snaps[-1] if snaps else {}
        ci = last.get("lab_oos_ci95")
        lab_ci = [_num(ci[0]), _num(ci[1])] if isinstance(ci, (list, tuple)) and len(ci) == 2 else None
        flags = []
        if st["n"] >= MIN_TRADES_FOR_VERDICT and lab_ci and lab_ci[0] is not None and st["mean_r"] < lab_ci[0]:
            flags.append(LAB_DRIFT)
        out[vid] = {"n": st["n"], "mean_r": st["mean_r"], "ci95_mean_r": st["ci95_mean_r"], "win_rate": st["win_rate"],
                    "verdict": verdict(st), "net_usdt": round(sum(float(t.pnl) for t in ts), 4),
                    "observation": any(s_.get("observation") is True for s_ in snaps),
                    "definition_sha": last.get("definition_sha"), "lab_verdict": last.get("lab_verdict"),
                    "lab_oos_mean_r": _num(last.get("lab_oos_mean_r")), "lab_oos_ci95": lab_ci, "flags": flags}
        for k in ("verdict_mode", "verdict_used"):      # yalnız kipi yazan anlık görüntüde (eski işlemlerin çıktısı aynı)
            if isinstance(last.get(k), str):
                out[vid][k] = last[k]
    return out


def book_card(path: Path, *, since: str | None = None) -> dict[str, Any]:
    led = FuturesLedgerV2.load(path)
    trades = [t for t in led.history if not since or str(t.closed_at) >= since]
    trades.sort(key=lambda t: str(t.closed_at))
    rs = [float(t.r_multiple) for t in trades]
    st = _r_stats(rs)
    cov = _funding_coverage([{"features": t.features or {}} for t in trades])
    card = {
        "ledger": str(path), "starting_equity": float(led.starting_equity), "wallet_balance": round(float(led.wallet_balance), 4),
        "return_pct_realized": round((float(led.wallet_balance) / float(led.starting_equity) - 1) * 100, 2) if led.starting_equity else None,
        "open_positions": len(led.positions), "closed_trades_total": len(led.history), "window_since": since,
        "first_close": str(trades[0].closed_at) if trades else None, "last_close": str(trades[-1].closed_at) if trades else None,
        "net_usdt": round(sum(float(t.pnl) for t in trades), 4), "fees_usdt": round(sum(float(t.fees) for t in trades), 4),
        "funding_usdt": round(sum(float(t.funding) for t in trades), 4), "r": st, "verdict": verdict(st),
        "no_stop_trades": sum(1 for t in trades if float(t.r_multiple) == 0 and float(t.pnl) != 0),
        "funding_coverage": {k: cov[k] for k in ("complete", "incomplete", "unknown")},
        "by_setup": _group(trades, lambda t: t.setup_type), "by_exit": _group(trades, lambda t: t.exit_reason),
        "by_month": dict(sorted(_group(trades, lambda t: str(t.closed_at)[:7]).items())),
    }
    bv = by_variation(trades)
    if bv:                       # yalnız mum varyasyonu işlemi olan defterde (diğer defterlerin çıktısı aynı kalır)
        card["by_variation"] = bv
    return card


def learning_since(state: Path) -> str | None:
    """`learning_mode_since`: öğrenmenin İLK aktif olduğu an (motorun `learning_mode.json`u). Yoksa/bozuksa None."""
    try:
        d = json.loads((state / LEARNING_SINCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = d.get("since") if isinstance(d, dict) else None
    return str(v) if v else None


def _ts(x: Any):
    try:
        return from_iso(str(x)) if x else None
    except (TypeError, ValueError):
        return None


def learning_class(t) -> str:
    """Öğrenme dönemindeki işlemin sınıfı: `learning_unlocked_by` DOLU → öğrenme-ekstra; boş ya da etiket yok (askıda
    açılmış) → politika. `BASELINE_UNKNOWN` da ekstra sayılır (taban kararı bilinmiyorsa politika diye iddia edilmez)."""
    lr = (t.features or {}).get("learning")
    return LEARNING_EXTRA if isinstance(lr, dict) and lr.get("learning_unlocked_by") else POLICY


def _slice(trades: list) -> dict[str, Any]:
    """R önce; USDT yalnız bilgi (öğrenme ölçeği öncesiyle KIYASLANMAZ)."""
    st = _r_stats([float(t.r_multiple) for t in trades])
    return {"n": st["n"], "mean_r": st["mean_r"], "ci95_mean_r": st["ci95_mean_r"], "sum_r": st["sum_r"],
            "win_rate": st["win_rate"], "verdict": verdict(st), "net_usdt": round(sum(float(t.pnl) for t in trades), 4)}


def is_record_only(t: dict[str, Any]) -> bool:
    """Karşı-olgusal kayıt bir KAYDA ALINAN EKSTRA mı (ilk neden `LEARNING_RECORD_ONLY`)?"""
    rs = t.get("reason_not_opened")
    return isinstance(rs, list) and bool(rs) and str(rs[0]) == RECORD_ONLY_REASON


def counterfactual_card(path: Path, *, book: str | None = None, record_only: bool | None = None) -> dict[str, Any] | None:
    """Karşı-olgusal kayıtların R özeti — P&L'e GİRMEZ (USDT alanı bilerek yok). `book` verilirse yalnız o defterin
    kayıtları (ana botun `shadow_book.json`u öğrenme öncesi gölgeleri de taşır). Dosya/kayıt yoksa None.
    `record_only` (2026-10-03): None → bütün kayıtlar; False → kayda alınan ekstralar HARİÇ; True → YALNIZ onlar.

    NET TABAN (2026-09-29, maliyet sapması): `mean_r`, aralık, kazanma oranı ve hüküm YALNIZ net etiketli (`r_net`,
    `cf_label_v2`) kayıtlardan — gerçek işlemlerin net R'siyle aynı tabanda. Brüt ortalama (`mean_r_gross`, bütün etiketli
    kayıtlar) yalnız bilgi; yalnız brüt (v1) kayıt sayısı `n_gross_only_v1`, çevrimdışı kapalı-biçim tahminleri (v1c)
    `n_approx_v1c` / `mean_r_net_approx_v1c` ayrı gösterilir ve net ortalamasına KARIŞMAZ."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = [t for t in ((d or {}).get("trades") or []) if isinstance(t, dict) and (book is None or t.get("book") == book)]
    if record_only is not None:
        rows = [t for t in rows if is_record_only(t) == bool(record_only)]
    if not rows:
        return None
    lab = [t for t in rows if isinstance(t.get("outcome"), dict) and _num(t["outcome"].get("r_multiple")) is not None]
    net = [t for t in lab if _num(t["outcome"].get("r_net")) is not None]
    st = _r_stats([float(t["outcome"]["r_net"]) for t in net])
    gst = _r_stats([float(t["outcome"]["r_multiple"]) for t in lab])
    v1c = [_num(t["outcome"].get("r_net_approx")) for t in lab if _num(t["outcome"].get("r_net")) is None]
    v1c = [x for x in v1c if x is not None]
    costs = [c for c in (_num(t["outcome"].get("cost_r")) for t in net) if c is not None]
    # İHTİYATLI (2026-10-01, cf_aux_v1): yalnız `aux` taşıyan net kayıtlar; hesaplanamayan (null) kayıt kapsam dışı
    aux = [t for t in net if isinstance(t["outcome"].get("aux"), dict)]
    cons = [(float(t["outcome"]["r_net"]), c) for t in aux
            if (c := _num(t["outcome"]["aux"].get("r_net_conservative"))) is not None]
    return {"file": str(path), "recorded": len(rows), "labeled": len(lab), "pending": len(rows) - len(lab),
            "approx": sum(1 for t in lab if t.get("approx")), "r_basis": "net", "n_net": st["n"],
            "mean_r": st["mean_r"], "ci95_mean_r": st["ci95_mean_r"], "sum_r": st["sum_r"], "win_rate": st["win_rate"],
            "verdict": verdict(st), "mean_r_gross": gst["mean_r"],
            "mean_r_gross_net_rows": _r_stats([float(t["outcome"]["r_multiple"]) for t in net])["mean_r"],
            "mean_cost_r": round(sum(costs) / len(costs), 4) if costs else None,
            "n_gross_only_v1": len(lab) - len(net) - len(v1c), "n_approx_v1c": len(v1c),
            "mean_r_net_approx_v1c": round(sum(v1c) / len(v1c), 4) if v1c else None,
            "n_net_funding_incomplete": sum(1 for t in net if t["outcome"].get("funding_complete") is False),
            "in_pnl": False, "n_aux": len(aux), "n_conservative": len(cons),
            "mean_r_conservative": round(sum(c for _r, c in cons) / len(cons), 4) if cons else None,
            "mean_r_net_conservative_rows": round(sum(r for r, _c in cons) / len(cons), 4) if cons else None}


def learning_split(path: Path, *, learning_since_iso: str, since: str | None = None, cf_path: Path | None = None,
                   cf_book: str | None = None) -> dict[str, Any]:
    """Tek defterin öğrenme ayrımı: ÖNCE / SONRA (giriş anı `learning_mode_since`e göre; öğrenme etiketi taşıyan işlem
    daima SONRA) ve SONRA içinde politika / öğrenme-ekstra; ayrıca karşı-olgusallar. `since` karne penceresidir."""
    led = FuturesLedgerV2.load(path)
    cut = _ts(learning_since_iso)
    trades = [t for t in led.history if not since or str(t.closed_at) >= since]
    trades.sort(key=lambda t: str(t.closed_at))

    def _after(t) -> bool:
        if isinstance((t.features or {}).get("learning"), dict):
            return True
        at = _ts(t.opened_at) or _ts(t.closed_at)
        return bool(cut is not None and at is not None and at >= cut)
    after = [t for t in trades if _after(t)]
    before = [t for t in trades if not _after(t)]
    return {"before": _slice(before), "after": _slice(after),
            POLICY: _slice([t for t in after if learning_class(t) == POLICY]),
            LEARNING_EXTRA: _slice([t for t in after if learning_class(t) == LEARNING_EXTRA]),
            "counterfactual": (counterfactual_card(cf_path, book=cf_book, record_only=False)
                               if cf_path is not None else None),
            RECORDED_EXTRA: (counterfactual_card(cf_path, book=cf_book, record_only=True)
                             if cf_path is not None else None)}


def scorecard(state: Path, *, since: str | None = None, learning_since_iso: str | None = None) -> dict[str, Any]:
    books = {}
    found = find_books(state)
    for sub, path in found.items():
        try:
            books[sub or "main"] = {"name": BOOKS.get(sub, sub), **book_card(path, since=since)}
        except Exception as exc:  # noqa: BLE001 — bozuk bir defter diğerlerinin karnesini durdurmaz
            books[sub or "main"] = {"name": BOOKS.get(sub, sub), "ledger": str(path), "error": f"{type(exc).__name__}: {exc}"}
    card = {"kind": "BOT_SCORECARD_PAPER", "state": str(state), "since": since, "min_trades_for_verdict": MIN_TRADES_FOR_VERDICT,
            "note_tr": "PAPER sonucu; gerçek emir defteri/kısmi dolum/gecikme yok. Hüküm kâr garantisi değildir.", "books": books}
    # ÖĞRENME MODU (2026-09-28): ilk aktif an biliniyorsa öncesi/sonrası ayrımı (yoksa karne AYNEN eskisi)
    src = "--learning-since" if learning_since_iso else LEARNING_SINCE_FILE
    lsi = learning_since_iso or learning_since(state)
    if lsi:
        split: dict[str, Any] = {}
        for sub, path in found.items():
            key = sub or "main"
            cf_path = (state / MAIN_SHADOW_FILE) if not sub else (path.parent / CF_FILE)
            try:
                split[key] = {"name": BOOKS.get(sub, sub), **learning_split(path, learning_since_iso=lsi, since=since,
                                                                            cf_path=cf_path, cf_book="main" if not sub else None)}
            except Exception as exc:  # noqa: BLE001
                split[key] = {"name": BOOKS.get(sub, sub), "error": f"{type(exc).__name__}: {exc}"}
        card["learning_mode"] = {
            "since": lsi, "source": src, "books": split,
            "note_tr": ("R esastır. learning_mode_since sonrası USDT sonuçları öğrenme ölçeğindedir (%0,5 risk, slot boyutu) ve "
                        "öncesiyle KIYASLANMAZ. Politika: taban kurallar da açardı; öğrenme-ekstra: yalnız öğrenme modu açtı; "
                        "kayda alınan ekstra: taban kuralların sinyali reddedeceği için AÇILMAYIP yalnız kaydedilen öğrenme "
                        "adayı (LEARNING_RECORD_ONLY); karşı-olgusal: açılmayan diğer geçerli sinyallerin etiketli sonucu — "
                        "ikisi de P&L'e GİRMEZ.")}
    return card


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _window(trades: list, start: datetime, end: datetime, equity: float) -> dict[str, Any]:
    """[start, end] içinde KAPANAN işlemler: sayı, net USDT (ücret + kayma + funding SONRASI), başlangıç bakiyesinin %'si,
    hedefe uzaklık (yüzde puan; + üstünde, − altında), ücret ve funding toplamı."""
    sel = []
    for t in trades:
        at = _ts(t.closed_at)
        if at is not None and start <= at <= end:
            sel.append(t)
    net = sum(float(t.pnl) for t in sel)
    pct = round(net / equity * 100.0, 4) if equity else None
    return {"from": start.isoformat(), "to": end.isoformat(), "n": len(sel), "net_usdt": round(net, 4),
            "net_pct": pct, "fees_usdt": round(sum(float(t.fees) for t in sel), 4),
            "funding_usdt": round(sum(float(t.funding) for t in sel), 4),
            "target_pct": MONTHLY_TARGET_PCT,
            "gap_to_target_pct": (round(pct - MONTHLY_TARGET_PCT, 4) if pct is not None else None)}


def monthly_target(state: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """AYLIK HEDEF RAPORU (2026-10-03, yalnız rapor): defter başına içinde bulunulan UTC takvim ayı (bugüne kadar) ve son
    30 gün. Kaynak defterin kapanan işlemleri (`TradeRecord.pnl` = ücret, kayma ve funding SONRASI net); oran defterin
    BAŞLANGIÇ bakiyesine göre. Açık pozisyonlar dahil değil. Salt okunur."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    books: dict[str, Any] = {}
    for sub, path in find_books(state).items():
        key = sub or "main"
        try:
            led = FuturesLedgerV2.load(path)
            eq = float(led.starting_equity)
            hist = list(led.history)
            books[key] = {"name": BOOKS.get(sub, sub), "starting_equity": eq, "open_positions": len(led.positions),
                          "month": _window(hist, _month_start(now), now, eq),
                          "last_30d": _window(hist, now - timedelta(days=30), now, eq)}
        except Exception as exc:  # noqa: BLE001 — bozuk bir defter diğerlerini durdurmaz
            books[key] = {"name": BOOKS.get(sub, sub), "error": f"{type(exc).__name__}: {exc}"}
    sp = state / SPOT_FILE
    if sp.exists():                       # ana botun spot defteri (motor tasarımı §7.5); yoksa blok AYNEN eskisi
        try:
            sled = SpotLedger.load(sp)
            eq = float(sled.starting_equity)
            hist = list(sled.history)
            books["main_spot"] = {"name": DAILY_NAMES["main_spot"], "starting_equity": eq, "open_positions": len(sled.positions()),
                                  "month": _window(hist, _month_start(now), now, eq),
                                  "last_30d": _window(hist, now - timedelta(days=30), now, eq)}
        except Exception as exc:  # noqa: BLE001
            books["main_spot"] = {"name": DAILY_NAMES["main_spot"], "error": f"{type(exc).__name__}: {exc}"}
    return {"now": now.isoformat(), "month": now.strftime("%Y-%m"), "target_pct": MONTHLY_TARGET_PCT, "books": books,
            "note_tr": ("Sahibin hedefi: her algoritma kendi kâğıt bakiyesinde ayda en az +%1 net. Bu bölüm yalnız ölçümdür: "
                        "kapanan işlemlerin ücret, kayma ve funding sonrası net sonucu, defterin başlangıç bakiyesine oranı. "
                        "Açık pozisyonların gerçekleşmemiş sonucu dahil değildir. PAPER sonucudur; kâr iddiası ya da "
                        "garantisi değildir.")}


# ============================================================================ GÜNLÜK HEDEF (--daily; yalnız ölçüm)
def daily_books(state: Path) -> dict[str, tuple[str, Path]]:
    """Günlük ölçümün defterleri: `find_books` (vadeli) + ana botun `spot_ledger.json`'ı. Anahtarlar motorla aynı:
    ana botun vadelisi `main_fut`, spotu `main_spot`, diğerleri klasör adı."""
    out = {("main_fut" if not sub else sub): ("futures", p) for sub, p in find_books(state).items()}
    if (state / SPOT_FILE).exists():
        out["main_spot"] = ("spot", state / SPOT_FILE)
    return out


def _inst(symbol: Any) -> str:
    s_ = str(symbol or "").upper().strip()
    if ":" in s_:
        s_ = s_.split(":", 1)[0]
    return s_.replace("/", "").replace("_", "").replace("-", "")


def _f8(x: Decimal | None) -> float | None:
    return None if x is None else round(float(x), 8)


def _path_marks(state: Path, now: datetime) -> dict[str, tuple[Decimal, datetime]]:
    """position_path.jsonl kuyruğu (son 4 MB): sembol başına `now − 60 dk … now + 2 dk` içindeki en son mark."""
    p = state / "position_path.jsonl"
    out: dict[str, tuple[Decimal, datetime]] = {}
    if not p.exists():
        return out
    with open(p, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - PATH_TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    if size > PATH_TAIL_BYTES and lines:
        lines = lines[1:]
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        t = _ts(row.get("ts"))
        try:
            m = Decimal(str(row.get("mark")))
        except (ArithmeticError, ValueError, TypeError):
            continue
        k = _inst(row.get("symbol"))
        if t is None or not k or not m.is_finite() or m <= 0 or not (now - PATH_MARK_MAX_AGE <= t <= now + PATH_MARK_FUTURE_TOL):
            continue
        if k not in out or t >= out[k][1]:
            out[k] = (m, t)
    return out


def _bar_mark(data_root: Path, symbol: str, now: datetime) -> tuple[Decimal, datetime] | None:
    """Worker HistoryStore'unda son KAPANMIŞ spot barı (≤ 24 saat). Ad adayları: 'ETH_USDT' sonra 'ETHUSDT'; tf 1m→1d;
    kapanışı en yeni olan (eşitlikte ilk aday)."""
    root = data_root / "market" / "history"
    if not (root / "spot").exists():
        return None
    from tradingbot.history.store import HistoryStore  # noqa: PLC0415 — yalnız gerektiğinde (pandas)
    store = HistoryStore(root)
    now_ms = int(now.timestamp() * 1000)
    max_age = int(BAR_MARK_MAX_AGE.total_seconds() * 1000)
    names = []
    for n in (str(symbol), _inst(symbol)):
        if n not in names:
            names.append(n)
    best: tuple[int, Decimal] | None = None
    for name in names:
        for tf, step in BAR_TF_MS.items():
            if not store.series_dir("spot", name, tf).is_dir():
                continue
            try:
                df = store.read("spot", name, tf, since_ms=now_ms - max_age - step, until_ms=now_ms)
            except Exception:  # noqa: BLE001
                continue
            rows = [(int(t) + step, c) for t, c in zip(df["timestamp"], df["close"])
                    if int(t) + step <= now_ms and now_ms - (int(t) + step) <= max_age]
            if not rows:
                continue
            ct, close = max(rows, key=lambda r: r[0])
            try:
                price = Decimal(str(close))
            except (ArithmeticError, ValueError):
                continue
            if not price.is_finite() or price <= 0:
                continue
            if best is None or ct > best[0]:
                best = (ct, price)
    if best is None:
        return None
    return best[1], datetime.fromtimestamp(best[0] / 1000, tz=timezone.utc)


def _spot_marks(state: Path, symbols: list[str], now: datetime, futs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Spot mark zinciri (motor tasarımı §7.1): (1) position_path ≤ 60 dk, (2) HistoryStore son kapanmış spot barı,
    (3) aynı sembolün vadeli `last_price`'ı (`PERP_PROXY`, en yeni `updated_at`). Hiçbiri yoksa mark yok."""
    pp = _path_marks(state, now)
    prox: dict[str, tuple[Decimal, datetime | None]] = {}
    for _k, led in sorted(futs.items()):
        upd = _ts(led.updated_at)
        for pos in led.positions.values():
            lp = pos.last_price
            k = _inst(pos.symbol)
            if lp is None or lp <= 0 or not k:
                continue
            cur = prox.get(k)
            if cur is None or (upd is not None and (cur[1] is None or upd > cur[1])):
                prox[k] = (lp, upd)
    out: dict[str, dict[str, Any]] = {}
    for sym in sorted(set(symbols)):
        k = _inst(sym)
        if k in pp:
            out[sym] = {"price": pp[k][0], "source": "POSITION_PATH", "age_s": round((now - pp[k][1]).total_seconds(), 1)}
            continue
        bm = _bar_mark(state.parent, sym, now)
        if bm is not None:
            out[sym] = {"price": bm[0], "source": "HISTORY_SPOT_BAR", "age_s": round((now - bm[1]).total_seconds(), 1)}
            continue
        if k in prox:
            out[sym] = {"price": prox[k][0], "source": "PERP_PROXY",
                        "age_s": round((now - prox[k][1]).total_seconds(), 1) if prox[k][1] else None}
            continue
        out[sym] = {"price": None, "source": "MISSING", "age_s": None}
    return out


def _spot_base(sym: str, quote: str) -> str:
    s_ = sym.upper()
    if "/" in s_:
        return s_.split("/", 1)[0]
    k = _inst(s_)
    return k[: -len(quote)] if k.endswith(quote) and len(k) > len(quote) else k


def _spot_now(state: Path, sled: Any, now: datetime, futs: dict[str, Any]) -> dict[str, Any]:
    """Spot defterin şimdiki değeri: B = nakit + kilitli + Σ lot × maliyet; E = nakit + kilitli + Σ varlık × mark;
    gerçekleşmemiş U = E − B (brüt; vadeli LEDGER_MARK ile aynı tanım). Bir mark eksikse U yok."""
    quote = sled.quote_asset.upper()
    lots_cost = {sym: sum((l.qty * l.cost_basis for l in ls), Decimal(0)) for sym, ls in sled.lots.items()}
    base_to_sym = {_spot_base(sym, quote): sym for sym in sled.lots}
    held: dict[str, Decimal] = {}
    totals: dict[str, Decimal] = {}
    for src in (sled.assets, sled.locked_assets):
        for a, q in src.items():
            totals[a.upper()] = totals.get(a.upper(), Decimal(0)) + q
    for a, q in totals.items():
        if q != 0:
            held[base_to_sym.get(a, f"{a}/{quote}")] = q
    marks = _spot_marks(state, list(sled.lots) + [f"{a}/{quote}" for src in (sled.assets, sled.locked_assets)
                                                  for a, q in src.items() if q != 0], now, futs)
    book_value = sled.cash + sled.locked_cash + sum(lots_cost.values(), Decimal(0))
    eq, complete = sled.cash + sled.locked_cash, True
    for sym, q in held.items():
        px = (marks.get(sym) or {}).get("price")
        if px is None:
            complete = False
            continue
        eq += q * px
    return {"book_value": book_value, "unrealized": (eq - book_value) if complete else None,
            "marks": {k: {**v, "price": _f8(v["price"])} for k, v in marks.items() if k in held}}


def daily_report(state: Path, *, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    """GÜNLÜK HEDEF ölçümü (salt okunur, motordan bağımsız). Son `days` UTC günü (bugün dahil) için defter başına:
    kayıt görünümü (`closed_at` günü), defter-zaman-damgalı cüzdan görünümü (hareketin `ts` günü; ledger 2000 hareketle
    dolu ise ve gün en eski hareketten önce başlıyorsa `complete=False`), gün başı gerçekleşmiş bakiye (vadeli cüzdan;
    spot defter değeri — hareketlerden geriye doğru) ve şimdiki gerçekleşmemiş (`LEDGER_MARK`). Hüküm YOKTUR."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = now.date()
    day_list = [(end - timedelta(days=i)).isoformat() for i in range(days)][::-1]
    loaded: dict[str, tuple[str, Any]] = {}
    books: dict[str, Any] = {}
    for key, (kind, path) in daily_books(state).items():
        name = DAILY_NAMES.get(key) or BOOKS.get(key, key)
        try:
            loaded[key] = (kind, FuturesLedgerV2.load(path) if kind == "futures" else SpotLedger.load(path))
        except Exception as exc:  # noqa: BLE001 — bozuk bir defter diğerlerini durdurmaz
            books[key] = {"name": name, "kind": kind, "error": f"{type(exc).__name__}: {exc}"}
    futs = {k: led for k, (kind, led) in loaded.items() if kind == "futures"}
    tot = {d: {"rec_n": 0, "rec_net": Decimal(0), "wal_net": Decimal(0), "den": Decimal(0), "complete": True} for d in day_list}
    for key, (kind, led) in loaded.items():
        name = DAILY_NAMES.get(key) or BOOKS.get(key, key)
        rec: dict[str, dict[str, Any]] = {}
        for t in led.history:
            at = _ts(t.closed_at)
            if at is None:
                continue
            r = rec.setdefault(at.astimezone(timezone.utc).date().isoformat(),
                               {"n": 0, "net": Decimal(0), "fees": Decimal(0), "funding": Decimal(0), "slippage": Decimal(0)})
            r["n"] += 1
            r["net"] += t.pnl
            r["fees"] += t.fees
            r["funding"] += t.funding if kind == "futures" else Decimal(0)
            r["slippage"] += t.slippage_cost
        wal: dict[str, dict[str, Decimal]] = {}
        ent_ts = []
        for e in led.entries:
            at = _ts(e.ts)
            if at is None:
                continue
            ent_ts.append((at, e))
            w = wal.setdefault(at.astimezone(timezone.utc).date().isoformat(), {"net": Decimal(0), "transfer": Decimal(0)})
            k = e.kind.value
            if k in DAILY_PNL_KINDS:
                w["net"] += e.amount
            elif k == "TRANSFER":
                w["transfer"] += e.amount
        rotated = len(led.entries) >= ENTRIES_KEEP
        first_ts = min((a for a, _e in ent_ts), default=None)
        if kind == "futures":
            base_now = led.wallet_balance
            unreal = Decimal(0)
            for pos in led.positions.values():
                px = pos.last_price if (pos.last_price is not None and pos.last_price > 0) else pos.entry_avg
                unreal += (px - pos.entry_avg) * pos.qty * pos.side.sign
            marks: dict[str, Any] = {}
            mark_src = "LEDGER_MARK"
        else:
            sn = _spot_now(state, led, now, futs)
            base_now, unreal, marks, mark_src = sn["book_value"], sn["unrealized"], sn["marks"], "SPOT_MARK_CHAIN"
        bd = {}
        for d in day_list:
            start = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
            complete = (not rotated) or (first_ts is not None and first_ts < start)
            r = rec.get(d) or {"n": 0, "net": Decimal(0), "fees": Decimal(0), "funding": Decimal(0), "slippage": Decimal(0)}
            w = wal.get(d) or {"net": Decimal(0), "transfer": Decimal(0)}
            later = sum((e.amount for a, e in ent_ts if a >= start and (kind == "futures" or e.kind.value != "TRANSFER")),
                        Decimal(0))
            den = (base_now - later) if complete else None
            bd[d] = {"rec": {"n": r["n"], "net": _f8(r["net"]), "fees": _f8(r["fees"]), "funding": _f8(r["funding"]),
                             "slippage": _f8(r["slippage"])},
                     "wal_ts": {"net": _f8(w["net"]), "transfer": _f8(w["transfer"]), "complete": complete},
                     "day_start_balance": _f8(den),
                     "realized_pct": round(float(w["net"] / den * 100), 6) if (complete and den and den > 0) else None}
            tt = tot[d]
            tt["rec_n"] += r["n"]
            tt["rec_net"] += r["net"]
            tt["wal_net"] += w["net"]
            if complete and den is not None:
                tt["den"] += den
            else:
                tt["complete"] = False
        books[key] = {"name": name, "kind": kind, "unrealized_now": _f8(unreal), "mark_source": mark_src, "marks": marks,
                      "open_positions": len(led.positions) if kind == "futures" else len(led.positions()), "days": bd}
    total = {d: {"rec_n": v["rec_n"], "rec_net": _f8(v["rec_net"]), "wal_net": _f8(v["wal_net"]), "complete": v["complete"],
                 "realized_pct": (round(float(v["wal_net"] / v["den"] * 100), 6) if (v["complete"] and v["den"] > 0) else None)}
             for d, v in tot.items()}
    un = [b.get("unrealized_now") for b in books.values() if "error" not in b]
    return {"now": now.isoformat(), "days": day_list, "target_pct": DAILY_TARGET_PCT, "books": books, "total": total,
            "unrealized_now_total": round(sum(un), 8) if un and all(u is not None for u in un) else None,
            "note_tr": ("Yalnız ölçüm. Gün satırları GERÇEKLEŞMİŞ sonuçtur (kayıt görünümü: o gün kapanan kayıtlar; cüzdan "
                        "görünümü: hareketin defterdeki zaman damgası); her birinin yanında açık pozisyonların ŞİMDİKİ "
                        "gerçekleşmemiş sonucu yazar. Gün ve hedef hükmü bu çıktıda VERİLMEZ: o, motorun ölçülmüş gece anlık "
                        "görüntüleriyle hesaplanan ve kesinleşmiş (KESİN) MTM günlerinde ve aylık kayıtlı bakışta verilir "
                        "(engine-status). Payda: gün başı gerçekleşmiş bakiye (hareketlerden geriye doğru). PAPER sonucudur; "
                        "kâr iddiası ya da garantisi değildir.")}


def render_daily(card: dict[str, Any]) -> list[str]:
    """GÜNLÜK HEDEF bölümü (yalnız `--daily` ile)."""
    dt = card.get("daily_target")
    if not dt:
        return []
    un = dt.get("unrealized_now_total")
    lines = [f"GÜNLÜK HEDEF (+%{dt['target_pct']:.0f}/gün, yalnız ölçüm; hüküm yok) · son {len(dt['days'])} gün (UTC) · "
             f"şimdi {dt['now'][:16]} · spot dahil · açık gerçekleşmemiş şimdi {_fmt(un)} USDT (LEDGER_MARK)",
             f"{'gün':<12}{'kayıt net USDT':>15}{'işlem':>7}{'cüzdan net USDT':>17}{'gün başı bakiyeye %':>21}  not"]
    for d in dt["days"][::-1]:
        t = dt["total"][d]
        pct = t.get("realized_pct")
        lines.append(f"{d:<12}{_fmt(t['rec_net']):>15}{t['rec_n']:>7}{_fmt(t['wal_net']):>17}"
                     f"{(_fmt(pct) + '%') if pct is not None else '—':>21}  "
                     + ("yalnız gerçekleşmiş" if t["complete"] else "cüzdan hareketleri eksik (rotasyon)"))
    lines.append(f"{'defter':<20}{'kayıt net (işlem)':>20}{'cüzdan net':>13}{'açık gerçekleşmemiş şimdi':>28}  mark")
    for key, b in dt["books"].items():
        if "error" in b:
            lines.append(f"{b['name']:<20} OKUNAMADI: {b['error']}")
            continue
        rn = sum(v["rec"]["net"] for v in b["days"].values())
        rc = sum(v["rec"]["n"] for v in b["days"].values())
        wn = sum(v["wal_ts"]["net"] for v in b["days"].values())
        mk = ", ".join(f"{s_} {v['source']}" + (f" {round(v['age_s'] / 60)} dk" if v.get("age_s") is not None else "")
                       for s_, v in (b.get("marks") or {}).items())
        lines.append(f"{b['name']:<20}{_fmt(rn) + f' ({rc})':>20}{_fmt(wn):>13}{_fmt(b['unrealized_now']):>28}  "
                     f"{b['mark_source']}{(' · ' + mk) if mk else ''}")
    lines.append("   " + dt["note_tr"])
    lines.append("")
    return lines


def _gap_txt(w: dict[str, Any]) -> str:
    g = w.get("gap_to_target_pct")
    if g is None:
        return "—"
    if g >= 0:
        return f"hedefin {g:.2f} puan üstünde"
    return f"hedefe {-g:.2f} puan var"


def render_monthly(card: dict[str, Any]) -> list[str]:
    """AYLIK HEDEF bölümü (yalnız `main()` hesaplatırsa; aksi halde satır yok)."""
    mt = card.get("monthly_target")
    if not mt:
        return []
    lines = [f"AYLIK HEDEF (+%{mt['target_pct']:.0f}/ay, yalnız rapor) · ay {mt['month']} (UTC, bugüne kadar) ve son 30 gün · "
             f"şimdi {mt['now'][:16]}",
             f"{'bot':<14}{'bu ay işlem':>12}{'bu ay net %':>13}  {'hedefe uzaklık':<26}{'30 gün işlem':>13}"
             f"{'30 gün net %':>14}  hedefe uzaklık"]
    for key, b in mt["books"].items():
        if "error" in b:
            lines.append(f"{b['name']:<14} OKUNAMADI: {b['error']}")
            continue
        m, l30 = b["month"], b["last_30d"]
        lines.append(f"{b['name']:<14}{m['n']:>12}{_fmt(m['net_pct']):>13}  {_gap_txt(m):<26}{l30['n']:>13}"
                     f"{_fmt(l30['net_pct']):>14}  {_gap_txt(l30)}")
    lines.append("   " + mt["note_tr"])
    lines.append("")
    return lines


def _fmt(v, nd=2) -> str:
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, (int, float)) else str(v))


def render(card: dict[str, Any]) -> str:
    lines = [f"BOT KARNESİ (PAPER, maliyet sonrası) · state: {card['state']}" + (f" · {card['since']} sonrası" if card["since"] else ""),
             f"{'bot':<14}{'işlem':>6}{'kazanma':>9}{'net USDT':>11}{'ort. R':>8}  {'%95 aralık':<18}{'PF':>6}{'maks.düşüş R':>13}  hüküm"]
    for key, b in card["books"].items():
        if "error" in b:
            lines.append(f"{b['name']:<14} OKUNAMADI: {b['error']}")
            continue
        r = b["r"]
        ci = r.get("ci95_mean_r")
        pf = r.get("profit_factor")
        lines.append(f"{b['name']:<14}{r['n']:>6}{_fmt(r['win_rate'] * 100 if r['win_rate'] is not None else None, 0) + '%':>9}"
                     f"{_fmt(b['net_usdt']):>11}{_fmt(r['mean_r']):>8}  {(f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else '—'):<18}"
                     f"{_fmt(pf) if not isinstance(pf, str) else '∞':>6}{_fmt(r['max_drawdown_r']):>13}  {b['verdict']}")
    lines.append("")
    for key, b in card["books"].items():
        if "error" in b or not b["r"]["n"]:
            continue
        setups = list(b["by_setup"].items())
        row = lambda kv: f"{kv[0]} n={kv[1]['n']} net {kv[1]['net_usdt']:+.2f}"  # noqa: E731
        lines.append(f"{b['name']}: {b['first_close'][:10]} → {b['last_close'][:10]} · bakiye {b['wallet_balance']:.2f} / "
                     f"{b['starting_equity']:.2f} USDT · ücret {b['fees_usdt']:.2f} · funding {b['funding_usdt']:+.2f} · açık {b['open_positions']}")
        if len(setups) <= 4:
            lines.append("   kurulumlar: " + "; ".join(row(kv) for kv in setups[::-1]))
        else:
            lines.append("   en iyi kurulum: " + "; ".join(row(kv) for kv in setups[-2:][::-1]))
            lines.append("   en kötü kurulum: " + "; ".join(row(kv) for kv in setups[:2]))
        fc = b["funding_coverage"]
        if fc["incomplete"] or b["no_stop_trades"]:
            lines.append(f"   dikkat: funding'i eksik {fc['incomplete']} işlem, stopsuz {b['no_stop_trades']} işlem")
        for vid, v in (b.get("by_variation") or {}).items():
            ci, lci = v.get("ci95_mean_r"), v.get("lab_oos_ci95")
            lab = (f" · lab OOS {_fmt(v.get('lab_oos_mean_r'))}" + (f" [{_fmt(lci[0])}, {_fmt(lci[1])}]" if lci else "")
                   if v.get("lab_oos_mean_r") is not None else " · lab OOS —")
            lines.append(f"   varyasyon {vid}{' (gözlem, kanıtlanmadı)' if v.get('observation') else ''}: n={v['n']} "
                         f"ort. R {_fmt(v['mean_r'])} {(f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else '—')} "
                         f"kazanma {_fmt(v['win_rate'] * 100 if v['win_rate'] is not None else None, 0)}% · {v['verdict']}{lab}"
                         + (" · " + ", ".join(v["flags"]) if v.get("flags") else ""))
    lines += render_learning(card)
    lines += render_monthly(card)
    lines += render_daily(card)
    lines.append(f"Hüküm kuralı: {card['min_trades_for_verdict']} işlemden az → VERİ YETERSİZ; ortalama R'nin %95 aralığı tamamen 0'ın "
                 "altında → ZARARDA, tamamen üstünde → KÂRDA; değilse BELİRSİZ. PAPER sonucu, kâr garantisi değildir.")
    return "\n".join(lines)


def _nr(b: dict[str, Any] | None) -> str:
    """«n / ort. R» hücresi (R esas)."""
    if not b:
        return "—"
    return f"{b.get('n', 0)} / {_fmt(b.get('mean_r'))}"


def _rec_txt(c: dict[str, Any] | None) -> str:
    """«kayda alınan ekstra» hücresi: kayıt sayısı / net ort. R (net etiketli yoksa —)."""
    if not c:
        return "0 / —"
    return f"{c.get('recorded', 0)} / {_fmt(c.get('mean_r'))}"


def render_learning(card: dict[str, Any]) -> list[str]:
    """ÖĞRENME MODU ayrımı (yalnız `learning_mode_since` biliniyorsa; aksi halde hiç satır yok → çıktı AYNEN eskisi)."""
    lm = card.get("learning_mode")
    if not lm:
        return []
    lines = [f"ÖĞRENME MODU AYRIMI · learning_mode_since {lm['since']} ({lm['source']}) · R esas; sonrası USDT öğrenme ölçeğinde, "
             "öncesiyle KIYASLANMAZ",
             f"{'bot':<14}{'önce n/ort.R':>16}{'sonra n/ort.R':>16}{'politika':>14}{'öğrenme-ekstra':>16}"
             f"{'kayda alınan ekstra':>22}  karşı-olgusal (P&L dışı) — net R"]
    for key, b in lm["books"].items():
        if "error" in b:
            lines.append(f"{b['name']:<14} OKUNAMADI: {b['error']}")
            continue
        cf = b.get("counterfactual")
        # NET TABAN (2026-09-29): ort.R net etiketlilerden; brüt yalnız parantezde (maliyet öncesi, kıyas için)
        cf_txt = (f"{cf['labeled']}/{cf['recorded']} etiketli · net {cf.get('n_net', 0)} · ort.R {_fmt(cf['mean_r'])} "
                  f"(brüt {_fmt(cf.get('mean_r_gross'))})" if cf else "—")
        if cf:
            # İHTİYATLI (2026-10-01, cf_aux_v1): kapsam n ve aynı kayıtların net ortalaması (kıyas aynı kümede)
            cf_txt += (f" · ihtiyatlı {_fmt(cf.get('mean_r_conservative'))} (n={cf.get('n_conservative', 0)}, "
                       f"aynı kayıtlarda net {_fmt(cf.get('mean_r_net_conservative_rows'))})")
        lines.append(f"{b['name']:<14}{_nr(b['before']):>16}{_nr(b['after']):>16}{_nr(b[POLICY]):>14}{_nr(b[LEARNING_EXTRA]):>16}"
                     f"{_rec_txt(b.get(RECORDED_EXTRA)):>22}  {cf_txt}")
    lines.append("   " + lm["note_tr"])
    lines.append("   «kayda alınan ekstra»: kayıt sayısı / etiketlenenlerin NET ort. R (karşı-olgusalla aynı net taban; "
                 "etiket yoksa —). Bu adaylar açılmadı; bakiyeye dokunmaz. Sayı KAYIT sayısıdır, işlem sayısıyla bire bir "
                 "kıyaslanmaz: trend defterlerinde (T2/M2) koşul sürdükçe aynı adayın her yeni günlük barı yeni kayıttır "
                 "(açılsaydı tek işlem olurdu).")
    lines.append("   Karşı-olgusal ort.R NETtir (defterin ücret/kayma/funding modeliyle aynı barlarda yeniden oynatma, "
                 "cf_label_v2); brüt değer yalnız bilgi. Net'i olmayan eski (brüt) kayıtlar ortalamaya girmez.")
    lines.append("   «ihtiyatlı»: cf_aux_v1 yardımcı etiketi (giriş barındaki stop fitili + gerçek defterin örneklenmiş stop "
                 "dolumu tahmini) — yalnız bilgi, hüküm ve ortalama DEĞİŞMEZ; n = bu değeri olan kayıt sayısı.")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Beş kâğıt defterin maliyet sonrası karnesi (salt okunur).")
    ap.add_argument("--state", required=True, help="state klasörü (ör. C:\\tb-olcum-veri\\state)")
    ap.add_argument("--since", default=None, help="yalnız bu tarihten (YYYY-AA-GG) sonra kapananlar")
    ap.add_argument("--out", default=None, help="ayrıntılı JSON raporu")
    ap.add_argument("--learning-since", default=None,
                    help="öğrenme modu başlangıcı (ISO); verilmezse state/learning_mode.json okunur")
    ap.add_argument("--now", default=None, help="aylık/günlük hedef raporunun «şimdi»si (ISO, UTC); verilmezse şu an")
    ap.add_argument("--daily", action="store_true", help="GÜNLÜK HEDEF bölümü (yalnız ölçüm; spot dahil; hüküm yok)")
    ap.add_argument("--days", type=int, default=7, help="--daily için gün sayısı (UTC, bugün dahil; varsayılan 7)")
    a = ap.parse_args(argv)
    state = Path(a.state)
    if not state.is_dir():
        print(f"state klasörü yok: {state}", file=sys.stderr)
        return 2
    now = _ts(a.now) if a.now else None
    if a.now and now is None:
        print(f"--now çözülemedi: {a.now}", file=sys.stderr)
        return 2
    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    card = scorecard(state, since=a.since, learning_since_iso=a.learning_since)
    if not card["books"]:
        print(f"{state} altında {LEDGER_FILE} bulunamadı", file=sys.stderr)
        return 2
    card["monthly_target"] = monthly_target(state, now=now)
    if a.daily:
        if a.days < 1:
            print("--days en az 1 olmalı", file=sys.stderr)
            return 2
        card["daily_target"] = daily_report(state, days=a.days, now=now)
    print(render(card))
    if a.out:
        Path(a.out).write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ayrıntı: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

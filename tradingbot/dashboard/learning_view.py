# -*- coding: utf-8 -*-
"""ÖĞRENME MODU PANELİ (2026-09-28, öğrenme modu) — SALT SUNUM.

Hiçbir şey HESAPLANMAZ ya da uydurulmaz; her değer motorun yazdığı kayıttan okunur:

* genel durum: `health.json["learning_mode"]` (enabled / active / reason / since) + `learning_mode.json` (İLK aktif an,
  yeniden başlatmada korunur);
* ana bot: `risk.json["learning_mode"]` (yalnız aktifken) + ana defter + `shadow_book.json`daki `book == "main"`
  karşı-olgusalları;
* kâğıt defterler: defterin KENDİ özetindeki `learning` alanı (`strategy_paper*.json`, `pattern_trader.json`) + defter.

Anahtar hiç açılmadıysa (health'te alan yok, `learning_mode.json` yok) bütün fonksiyonlar boş döner → sayfalar bit-aynı.
"""
from __future__ import annotations

from typing import Any

from .templates import badge, esc, fmt, fmt_utc, table

#: Motorun ilk aktif anı yazdığı dosya (`TradingEngineV3.LM_SINCE_FILE`).
SINCE_FILE = "learning_mode.json"
SUSPENDED_PREFIX = "LEARNING_MODE_SUSPENDED:"
#: Boyut kuralı → kısa Türkçe etiket (kod `pos.meta["learning"]["size_rule"]` olduğu gibi `title`da kalır).
SIZE_RULE_TR = {"SLOT": "slot", "BUMP_MIN_NOTIONAL": "min. tutara çıkarıldı", "SHRUNK_TO_MARGIN": "marja küçültüldü"}
EXPLORATION_TR = {"RESEARCH_SIZE": "keşif: araştırma boyutu", "NEG_EDGE": "keşif: negatif kenar"}
#: Ana botun öğrenme huni anahtarları (`engine_v3._LM_FUNNEL_KEYS`; yalnız öğrenme aktif turda yazılır).
FUNNEL_KEYS = ("learning_opened", "learning_unlocked", "learning_exploration", "learning_leverage_fallback",
               "min_notional_bumped", "shrunk_to_margin", "counterfactual_recorded", "counterfactual_dropped",
               "counterfactual_superseded")
FUNNEL_TR = {"learning_opened": "Öğrenmede açılan", "learning_unlocked": "Öğrenme-ekstra açılan",
             "learning_exploration": "Keşif işlemi", "learning_leverage_fallback": "Kaldıraç tabanı 2x",
             "min_notional_bumped": "Min. tutara çıkarılan", "shrunk_to_margin": "Marja küçültülen",
             "counterfactual_recorded": "Karşı-olgusal kayıt", "counterfactual_dropped": "Karşı-olgusal tavandan düşen",
             "counterfactual_superseded": "Karşı-olgusal → gerçek işlem"}


def _f(x: Any) -> float | None:
    try:
        if x is None or isinstance(x, bool):
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


# --------------------------------------------------------------------------- genel durum
def learning_status(state) -> dict[str, Any] | None:
    """Genel öğrenme durumu. Anahtar hiç açılmadıysa None (panel hiçbir şey basmaz)."""
    h = _d(state.get("health"))
    lm = h.get("learning_mode")
    rec = _d(state.get("learning_mode"))
    if not isinstance(lm, dict) and not rec.get("since"):
        return None
    lm = _d(lm)
    reason = str(lm.get("reason") or "")
    risk_pct = _f(_d(_d(_d(state.get("risk")).get("learning_mode")).get("book")).get("risk_pct"))
    return {"enabled": lm.get("enabled") is True, "active": lm.get("active") is True, "reason": reason,
            "suspended_reason": reason[len(SUSPENDED_PREFIX):] if reason.startswith(SUSPENDED_PREFIX) else None,
            "state_since": lm.get("since"), "since": rec.get("since") or lm.get("learning_mode_since"),
            "books": list(lm.get("books") or rec.get("books") or []), "health_at": h.get("at"),
            "in_health": bool(h.get("learning_mode")), "risk_pct": risk_pct}


def banner_html(st: dict[str, Any] | None) -> str:
    """Her sayfanın başındaki şerit. None → "" (kapalıyken sayfa bit-aynı)."""
    if not st:
        return ""
    since = (" · başlangıç %s" % fmt_utc(st.get("since"))) if st.get("since") else ""      # fmt_utc çıktısı kaçışlı
    rp = st.get("risk_pct")
    risk = ("işlem başı risk %%%s" % ("%g" % rp).replace(".", ",")) if rp is not None else "düşük işlem başı risk"
    if st.get("enabled") and st.get("active"):
        return ('<div class="card lmban" style="border-left:3px solid #26a69a">%s <b>ÖĞRENME MODU AÇIK — yalnız PAPER</b>%s'
                '<div class="small mut">Öğrenme ölçeği (%s, slot boyutu): USDT sonuçları öncesiyle karşılaştırılmaz; '
                'R ölçütleri esastır. Karşı-olgusal kayıtlar P&amp;L\'e girmez.</div></div>'
                % (badge("ÖĞRENME", "ok"), since, esc(risk)))
    if st.get("enabled"):
        why = st.get("suspended_reason") or st.get("reason") or "?"
        return ('<div class="card warn-box lmban" title="%s">%s <b>ÖĞRENME MODU ASKIDA: %s</b>%s'
                '<div class="small mut">Mod kapısı geçmiyor — bütün botlar bugünkü (baseline) kurallarla sürüyor; '
                'öğrenme döneminde açılan pozisyonlar kendi stop/hedef/kaldıracıyla yönetiliyor.</div></div>'
                % (esc(st.get("reason")), badge("ASKIDA", "warn"), esc(why), since))
    return ('<div class="card lmban" style="border-left:3px solid #f5c542">%s <b>ÖĞRENME MODU KAPALI</b>%s'
            '<div class="small mut">Öğrenme döneminde açılan pozisyonlar kendi stop/hedef/kaldıracıyla kapanır; toplam açık '
            'risk %%6\'nın altına inene kadar TOTAL_OPEN_RISK yeni girişleri durdurabilir (beklenen davranış).</div></div>'
            % (badge("KAPALI", "info"), since))


# --------------------------------------------------------------------------- işlem etiketleri
def learning_tags(features: Any) -> dict[str, Any] | None:
    """İşlemin öğrenme etiketleri (`features.learning`); öğrenmede açılmamışsa None."""
    f = _d(features)
    lr = f.get("learning")
    if not isinstance(lr, dict) or not lr:
        return None
    unl = lr.get("learning_unlocked_by")
    return {"size_rule": lr.get("size_rule"), "unlocked_by": [str(x) for x in (unl or [])] if isinstance(unl, (list, tuple)) else [],
            "exploration": lr.get("exploration") or f.get("exploration"), "in_lab_universe": f.get("in_lab_universe"),
            "risk_fraction_of_budget": _f(lr.get("risk_fraction_of_budget")), "slots": lr.get("slots")}


def tags_html(features: Any) -> str:
    """Kısa etiket satırı (size_rule, learning_unlocked_by, exploration, in_lab_universe). Etiket yoksa ""."""
    tg = learning_tags(features)
    if tg is None:
        return ""
    parts = ['<span class="pill ok" title="öğrenme modunda açıldı">öğrenme</span>']
    sr = tg.get("size_rule")
    if sr:
        parts.append('<span class="pill" title="size_rule=%s">%s</span>' % (esc(sr), esc(SIZE_RULE_TR.get(str(sr), str(sr)))))
    unl = tg["unlocked_by"]
    if unl:
        parts.append('<span class="pill warn" title="learning_unlocked_by: %s">öğrenme-ekstra: %s</span>'
                     % (esc(", ".join(unl)), esc(", ".join(unl[:3]) + (" …" if len(unl) > 3 else ""))))
    else:
        parts.append('<span class="pill" title="learning_unlocked_by: [] — taban kurallar da açardı">politika</span>')
    ex = tg.get("exploration")
    if ex:
        parts.append('<span class="pill warn" title="exploration=%s">%s</span>' % (esc(ex), esc(EXPLORATION_TR.get(str(ex), str(ex)))))
    iu = tg.get("in_lab_universe")
    if iu is not None:
        parts.append('<span class="pill%s" title="in_lab_universe=%s">%s</span>'
                     % ("" if iu else " warn", esc(str(bool(iu)).lower()), "lab evreni" if iu else "lab evreni dışı"))
    return '<span class="lmtags">' + " ".join(parts) + "</span>"


# --------------------------------------------------------------------------- defter rozetleri
def _cf_main(state) -> dict[str, Any] | None:
    """Ana botun karşı-olgusalları: `shadow_book.json`da `book == "main"` kayıtları (öğrenme öncesi gölgeler SAYILMAZ)."""
    sb = _d(state.get("shadow_book"))
    rows = [t for t in (sb.get("trades") or []) if isinstance(t, dict) and t.get("book") == "main"]
    if not rows:
        return None
    lab = [t for t in rows if isinstance(t.get("outcome"), dict)]
    return {"pending": len(rows) - len(lab), "labeled": len(lab), "dropped": None, "recorded_total": len(rows)}


def book_row(state, b: dict[str, Any]) -> dict[str, Any] | None:
    """Tek defterin öğrenme rozeti: slot kullanımı (açık/K), Σmarj/E, öğrenmede açık, öğrenme-ekstra, karşı-olgusal
    sayıları. Defterin hiç öğrenme kaydı yoksa None."""
    bid = b["book_id"]
    if bid == "main":
        lmd = _d(_d(state.get("risk")).get("learning_mode"))
        led = _d(state.book_ledger("main"))
        eq = _f(lmd.get("equity_basis")) or _f(led.get("starting_equity"))
        cf = _cf_main(state)
        reason = None
    else:
        doc = _d(state.book_summary_file(bid))
        lmd = _d(doc.get("learning"))
        eq = _f(_d(doc.get("summary")).get("starting_equity")) or _f(doc.get("starting_equity"))
        cf = lmd.get("counterfactual") if isinstance(lmd.get("counterfactual"), dict) else None
        reason = lmd.get("reason")
    if not lmd and cf is None:
        return None
    pos = state.book_positions(bid)
    margins = [_f(p.get("isolated_margin")) for p in pos]
    msum = (round(sum(m for m in margins if m is not None), 6) if all(m is not None for m in margins) else None) if pos else 0.0
    tagged = [learning_tags(p.get("features")) for p in pos]
    bk = _d(lmd.get("book"))
    return {"book_id": bid, "label": b.get("label") or bid, "active": (bool(lmd.get("active")) if lmd else None),
            "reason": reason, "since": lmd.get("since"), "slots_k": bk.get("slots"), "slots_used": len(pos),
            "margin_sum": msum, "equity": eq, "margin_frac": (msum / eq) if (msum is not None and eq) else None,
            "open_learning": sum(1 for t in tagged if t is not None),
            "open_extra": sum(1 for t in tagged if t is not None and t["unlocked_by"]),
            "counters": dict(_d(lmd.get("counters"))), "counterfactual": dict(cf) if cf else None,
            "leverage_max": bk.get("leverage_max"), "risk_pct": bk.get("risk_pct")}


def book_rows(state) -> list[dict[str, Any]]:
    """Bütün defterlerin rozetleri (öğrenme kaydı olmayan defter atlanır)."""
    return [r for r in (book_row(state, b) for b in state.books()) if r is not None]


def book_badge_html(row: dict[str, Any] | None) -> str:
    """Seçili hesabın öğrenme rozeti (slot açık/K · Σmarj/E · karşı-olgusal). Satır yoksa ""."""
    if not row:
        return ""
    k = row.get("slots_k")
    slots = "%d/%s" % (int(row.get("slots_used") or 0), k if k is not None else "—")
    mf = row.get("margin_frac")
    cf = row.get("counterfactual") or {}
    state = badge("AKTİF", "ok") if row.get("active") else (badge("ASKIDA", "warn") if row.get("active") is False else badge("—", "info"))
    return ('<div class="pills lmbook">%s<span class="pill" title="açık pozisyon / slot sayısı (K)">slot %s</span>'
            '<span class="pill" title="Σ izole marj / başlangıç özkaynağı (≤ %%95)">Σmarj/E %s</span>'
            '<span class="pill" title="öğrenmede açılmış açık pozisyon (ekstra: taban kurallar açmazdı)">öğrenme %d · ekstra %d</span>'
            '<span class="pill" title="karşı-olgusal kayıt (P&amp;L\'e girmez)">karşı-olgusal %s kayıt · %s etiketli · %s bekleyen</span></div>'
            % (state, esc(slots), esc(("%.0f%%" % (mf * 100)) if mf is not None else "—"),
               int(row.get("open_learning") or 0), int(row.get("open_extra") or 0),
               esc(str(cf.get("recorded_total", "—"))), esc(str(cf.get("labeled", "—"))), esc(str(cf.get("pending", "—")))))


def book_badge_for(state, book_id: str) -> str:
    """Terminal ekranındaki seçili hesabın rozeti. Anahtar hiç açılmadıysa "" (ekran bit-aynı)."""
    if learning_status(state) is None:
        return ""
    b = state.book(book_id)
    return book_badge_html(book_row(state, b)) if b else ""


def books_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="card mut">Öğrenme kaydı olan defter yok.</div>'
    body = []
    for r in rows:
        cf = r.get("counterfactual") or {}
        c = r.get("counters") or {}
        mf = r.get("margin_frac")
        body.append([esc(r["label"]),
                     badge("AKTİF", "ok") if r.get("active") else (badge("ASKIDA", "warn") if r.get("active") is False else badge("—", "info")),
                     esc("%d/%s" % (int(r.get("slots_used") or 0), r.get("slots_k") if r.get("slots_k") is not None else "—")),
                     esc(("%.0f%%" % (mf * 100)) if mf is not None else "—"),
                     fmt(r.get("margin_sum"), 2) if r.get("margin_sum") is not None else "—",
                     esc(str(r.get("open_learning") or 0)), esc(str(r.get("open_extra") or 0)),
                     esc(str(c.get("opened", "—"))), esc(str(c.get("min_notional_bumped", "—"))), esc(str(c.get("shrunk_to_margin", "—"))),
                     esc(str(cf.get("recorded_total", "—"))), esc(str(cf.get("labeled", "—"))), esc(str(cf.get("pending", "—"))),
                     esc(str(cf.get("dropped", "—") if cf.get("dropped") is not None else "—"))])
    return (table(["Defter", "Durum", "Slot (açık/K)", "Σmarj/E", "Σmarj USDT", "Öğrenmede açık", "Öğrenme-ekstra",
                   "Açılan (öğrenme)", "Min. tutara çıkarılan", "Marja küçültülen", "Karşı-olgusal", "Etiketli", "Bekleyen",
                   "Düşürülen"], body, num_cols={4, 5, 6, 7, 8, 9, 10, 11, 12, 13})
            + '<p class="small mut">Slot: eşzamanlı pozisyon sınırı K (Σ marj ≤ %95 E, likidasyon ≥ 2 × stop). '
              '«Öğrenme-ekstra»: taban kuralların AÇMAYACAĞI işlem (<code>learning_unlocked_by</code> dolu). Karşı-olgusal '
              'kayıtlar açılmayan geçerli sinyallerin etiketli sonucudur ve P&amp;L\'e GİRMEZ.</p>')


def section_html(state) -> str:
    """Genel bakıştaki açılır bölüm (defter tablosu). Anahtar hiç açılmadıysa ""."""
    st = learning_status(state)
    if st is None:
        return ""
    return ('<details class="section" open><summary>Öğrenme modu — defterler</summary><div>'
            + books_table_html(book_rows(state)) + "</div></details>")


def api_payload(state) -> dict[str, Any]:
    st = learning_status(state)
    return {"enabled": bool(st and st.get("enabled")), "status": st, "books": book_rows(state) if st else []}


__all__ = ["SINCE_FILE", "learning_status", "banner_html", "learning_tags", "tags_html", "book_row", "book_rows",
           "book_badge_html", "book_badge_for", "books_table_html", "section_html", "api_payload"]

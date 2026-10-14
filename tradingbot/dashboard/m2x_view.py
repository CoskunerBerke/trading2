# -*- coding: utf-8 -*-
"""M2X kartı (2026-10-05; docs/M2_AGGRESSIVE_V1.md §4.4) — SALT OKUMA: yalnız `state/strategy_paper_m2x.json` özetinin
`m2x` bloğunu çizer. Düğme YOK (yeniden başlatma yalnız sahibin VPS komutuyla); panel hiçbir dosyaya yazmaz.

Bugün / ay / 30 gün yüzdeleri başlık DEĞİLDİR: düşüşün ve gerçekleşmiş sonucun YANINDA gösterilir. İlk 28 gün "ısınma"."""
from __future__ import annotations

from typing import Any

from .templates import badge, card, esc, fmt, table

LABEL = "M2X · agresif M2 kopyası (PAPER)"


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _pct(x: Any, nd: int = 2) -> str:
    v = _f(x)
    return "—" if v is None else f"%{v:.{nd}f}"


def _bar(label: str, value: Any, cap: Any, *, highlight: bool) -> str:
    """Yatay çubuk: değer / tavan (yüzde puan). Tavan yoksa yalnız değer yazılır."""
    v, c = _f(value), _f(cap)
    frac = max(0.0, min(1.0, (v / c) if (v is not None and c and c > 0) else 0.0))
    col = "var(--dn)" if highlight else "var(--acc)"
    edge = "border:1px solid var(--warn);" if highlight else ""
    return (f'<div class="small">{esc(label)}: {_pct(v)} / {_pct(c, 0) if c is not None else "—"}'
            + (" · <b>bağlayan</b>" if highlight else "") + "</div>"
            f'<div style="background:var(--line);height:10px;border-radius:3px;{edge}">'
            f'<div style="width:{frac * 100:.1f}%;height:10px;background:{col};border-radius:3px"></div></div>')


def _dd_gauge(dd: Any, bounds: list[Any]) -> str:
    """Gerçek zirveden düşüş göstergesi; kademe sınırları (%15 / %25 / %35 / %50) işaretli."""
    v = max(0.0, min(60.0, _f(dd) or 0.0))
    marks = "".join(f'<div style="position:absolute;left:{min(100.0, float(b) / 60.0 * 100):.1f}%;top:-2px;height:14px;'
                    f'border-left:1px dashed var(--warn)" title="%{float(b):g}"></div>' for b in bounds if _f(b) is not None)
    return ('<div style="position:relative;background:var(--line);height:10px;border-radius:3px;margin:6px 0 2px">'
            f'<div style="width:{v / 60.0 * 100:.1f}%;height:10px;background:var(--dn);border-radius:3px"></div>{marks}</div>'
            '<div class="small mut">ölçek %0–60 · kesikli çizgiler: kademe sınırları ' +
            " / ".join(f"%{float(b):g}" for b in bounds if _f(b) is not None) + "</div>")


def _curve(rows: list[dict[str, Any]]) -> str:
    """Günlük görüntülerden özkaynak eğrisi + P (gerçek zirve) ve Pₖ (kademe zirvesi) çizgileri (inline SVG)."""
    pts = [(r.get("day"), _f(r.get("equity")), _f(r.get("peak")), _f(r.get("tier_peak"))) for r in rows if isinstance(r, dict)]
    pts = [p for p in pts if p[1] is not None]
    if len(pts) < 2:
        return '<div class="small mut">özkaynak eğrisi: en az iki günlük görüntü gerekir</div>'
    vals = [v for p in pts for v in p[1:] if v is not None]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    w, h = 600, 120

    def poly(idx: int, color: str, dash: str = "") -> str:
        xy = [(i / (len(pts) - 1) * w, h - (p[idx] - lo) / span * h) for i, p in enumerate(pts) if p[idx] is not None]
        if len(xy) < 2:
            return ""
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
        return f'<polyline fill="none" stroke="{color}" stroke-width="1.5" {dash} points="{d}"/>'
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="width:100%;height:120px">'
            + poly(2, "var(--warn)", 'stroke-dasharray="4 3"') + poly(3, "var(--mut)", 'stroke-dasharray="2 3"')
            + poly(1, "var(--acc)") + "</svg>"
            f'<div class="small mut">{esc(str(pts[0][0]))} → {esc(str(pts[-1][0]))} · mavi: özkaynak (mark) · sarı kesikli: '
            "gerçek zirve P · gri: kademe zirvesi Pₖ</div>")


def section_html(sp: dict[str, Any]) -> str:
    m = sp.get("m2x") if isinstance(sp.get("m2x"), dict) else None
    if m is None:
        return ""
    halted = bool(m.get("halted"))
    out = ""
    if halted:
        h = m.get("halt") or {}
        out += ('<div class="card warn-box"><b>DURDURULDU</b> — zirveden düşüş %50\'ye ulaştı '
                f'({esc(str(h.get("at") or "")[:16])}). Yeni giriş yok; açık pozisyonlar M2 ile çıkar. '
                f'Sahip incelemesi gerekli: <code>{esc(str(m.get("resume_cmd") or "m2x-resume"))}</code> '
                '(VPS\'te; panelde düğme yoktur).</div>')
    if m.get("warmup"):
        out += ('<div class="card mut">ISINMA: defter ilk 28 gününde (M2X boş başlar, M2 dolu olabilir) — sonuçlar '
                'hüküm değildir.</div>')
    tier = str(m.get("tier") or "—")
    kind = "bad" if halted else ("warn" if tier in ("K3", "K4_SOFT_HALT") else ("info" if tier == "K2" else "ok"))
    st_line = (badge(str(m.get("status") or "—"), kind) + " " + badge(tier, kind)
               + (" " + badge("YENİ GİRİŞ KAPALI", "warn") if m.get("new_entries") is False else "")
               + (" " + badge("M2 KAPALI", "warn") if (m.get("parent") or {}).get("enabled") is False else ""))
    out += f'<div class="card">{st_line} <span class="small mut">politika {esc(str(m.get("policy_version")))} · mühür ' \
           f'{esc(str(m.get("policy_sha")))} · dönem {esc(str(m.get("epoch")))}</span></div>'
    cards = card("Özkaynak (mark)", fmt(m.get("equity_mtm"), 2) + " USDT",
                 f'gerçekleşmiş {fmt(m.get("equity_realised"), 2)} · başlangıçtan {_pct(m.get("total_pct"))} · dönem içi '
                 f'{_pct(m.get("epoch_pct"))}')
    cards += card("Zirveden düşüş", _pct(m.get("dd_pct")),
                  f'zirve P {fmt(m.get("peak"), 2)} · kademe zirvesi Pₖ {fmt(m.get("tier_peak"), 2)} (düşüş '
                  f'{_pct(m.get("tier_dd_pct"))})')
    cards += card("Kademe / işlem riski", f'{esc(tier)} · {_pct(m.get("risk_pct"))}',
                  f'açık risk tavanı {_pct(m.get("open_risk_cap_pct"), 0)} · yukarı çıkış sayacı '
                  f'{esc(str(m.get("step_up_streak")))}/{esc(str(m.get("step_up_needed")))} (son anahtar '
                  f'{esc(str(m.get("last_key") or "—"))})')
    cards += card("Bugün / ay / 30 gün (mark)", f'{_pct(m.get("today_pct"))} / {_pct(m.get("mtd_pct"))} / '
                  f'{_pct(m.get("last30_pct"))}',
                  f'düşüş {_pct(m.get("dd_pct"))} · gerçekleşmiş ay {_pct(m.get("realised_mtd_pct"))} · gerçekleşmiş 30 gün '
                  f'{_pct(m.get("realised_last30_pct"))}')
    out += f'<div class="grid">{cards}</div>'
    bind = str(m.get("binding_cap") or "-")
    out += ('<div class="card">' + _dd_gauge(m.get("dd_pct"), list(m.get("tier_bounds_pct") or []))
            + _bar("Açık risk (mark'tan stopa)", m.get("open_risk_mark_pct"), m.get("open_risk_cap_pct"), highlight=bind == "OR")
            + f'<div class="small mut">girişteki risk {_pct(m.get("open_risk_entry_pct"))}</div>'
            + _bar("Kriz kaybı (%50 düşüş / likidasyon)", m.get("crash_loss_pct"),
                   (_f(m.get("crash_loss_pct")) or 0.0) + (_f(m.get("crash_budget_pct")) or 0.0), highlight=bind == "CL")
            + f'<div class="small mut">kriz bütçesi kalan {_pct(m.get("crash_budget_pct"))} · bağlayan tavan {esc(bind)} · '
              f'son λ {esc(str(m.get("last_lambda")))}</div></div>')
    out += '<div class="card">' + _curve(list(m.get("daily_tail") or [])) + "</div>"
    pos = sp.get("positions") or {}
    rows = []
    for sym, p in (pos.items() if isinstance(pos, dict) else []):
        if not isinstance(p, dict):
            continue
        rows.append([esc(sym), esc(str(p.get("m2_id") or "—")), fmt(p.get("size_ratio_vs_m2"), 2),
                     f'{esc(str(p.get("leverage")))}x', fmt(p.get("liq_price")), fmt(p.get("stop")),
                     fmt(p.get("open_risk_usdt"), 3), fmt(p.get("crash_loss_usdt"), 3), esc(str(p.get("tier_at_entry") or "—"))])
    out += "<h3>M2X pozisyonları</h3>" + table(["Sembol", "M2 eş işlem", "M2X/M2 boyut", "Kaldıraç", "Likidasyon", "Stop",
                                                "OR (USDT)", "CL (USDT)", "Giriş kademesi"], rows,
                                               num_cols={2, 4, 5, 6, 7}, empty="açık M2X pozisyonu yok")
    sk = list(m.get("recent_skips") or [])[::-1]
    out += "<h3>Atlanan M2 girişleri (son 50)</h3>" + table(
        ["Zaman", "Sembol", "M2 işlem", "Neden", "Kademe"],
        [[esc(str(r.get("at") or "")[:16]), esc(str(r.get("symbol"))), esc(str(r.get("m2_id"))), esc(str(r.get("reason"))),
          esc(str(r.get("tier")))] for r in sk if isinstance(r, dict)], empty="atlanan giriş yok")
    rh = list(m.get("resume_history") or [])[::-1]
    out += "<h3>Yeniden başlatma geçmişi</h3>" + table(
        ["İstek", "İşlendi", "Sonuç", "Dönem", "Not"],
        [[esc(str(r.get("requested_at") or "")[:16]), esc(str(r.get("processed_at") or "")[:16]), esc(str(r.get("result"))),
          esc(str(r.get("epoch"))), esc(str(r.get("note") or "")[:120])] for r in rh if isinstance(r, dict)],
        empty="yeniden başlatma isteği yok")
    dv = m.get("divergences") or {}
    out += ('<div class="small mut">ayrışmalar: ' + (", ".join(f"{esc(str(k))} {esc(str(v))}" for k, v in sorted(dv.items()))
                                                    if dv else "yok")
            + " · M2X, M2'nin gerçek işlemlerinin kopyasıdır; ayrı hüküm verilmez. −%50 zarar tavanı DEĞİL, yeni giriş "
              "durdurmasıdır.</div>")
    return out


def overview_sub(sp: dict[str, Any]) -> str:
    """Genel bakış kartının alt satırı (kademe, düşüş, durum)."""
    m = sp.get("m2x") if isinstance(sp.get("m2x"), dict) else {}
    return (f'{esc(str(m.get("status") or "—"))} · kademe {esc(str(m.get("tier") or "—"))} · düşüş {_pct(m.get("dd_pct"))} · '
            f'{len(sp.get("positions") or {})} açık · <a href="/portfolio/strategy">ayrıntı</a>')

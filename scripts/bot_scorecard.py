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
  aralığının alt ucunun altındaysa `LAB_DRIFT` (canlı davranış laboratuvardan sapıyor).

Kullanım:
    python scripts/bot_scorecard.py --state <state klasörü> [--since 2026-09-01] [--out karne.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.accounting import FuturesLedgerV2  # noqa: E402
from tradingbot.pattern_trader.report import MIN_TRADES_FOR_VERDICT, _funding_coverage, _r_stats  # noqa: E402

LEDGER_FILE = "futures_ledger.json"
#: defter klasörü (state altında) → görünen ad; "" = ana bot (state kökündeki defter)
BOOKS = {"": "Ana bot", "strategy_paper": "T2", "strategy_paper_m2": "M2 (TSMOM28)", "strategy_paper_box": "Box",
         "pattern_trader": "Formasyon", "strategy_paper_trend4h": "Trend 4h (gözlem, kanıtlanmadı)",
         "strategy_paper_candle4h": "C4 Mum varyasyonları (PAPER)"}
V_THIN, V_LOSS, V_WIN, V_OPEN = "VERİ YETERSİZ", "ZARARDA (kanıtlı)", "KÂRDA (kanıtlı, PAPER)", "BELİRSİZ"
#: Mum varyasyonu işlemlerinin kurulum öneki (`candle_book.SETUP_PREFIX`).
CANDLE_PREFIX = "candle:"
LAB_DRIFT = "LAB_DRIFT"


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


def scorecard(state: Path, *, since: str | None = None) -> dict[str, Any]:
    books = {}
    for sub, path in find_books(state).items():
        try:
            books[sub or "main"] = {"name": BOOKS.get(sub, sub), **book_card(path, since=since)}
        except Exception as exc:  # noqa: BLE001 — bozuk bir defter diğerlerinin karnesini durdurmaz
            books[sub or "main"] = {"name": BOOKS.get(sub, sub), "ledger": str(path), "error": f"{type(exc).__name__}: {exc}"}
    return {"kind": "BOT_SCORECARD_PAPER", "state": str(state), "since": since, "min_trades_for_verdict": MIN_TRADES_FOR_VERDICT,
            "note_tr": "PAPER sonucu; gerçek emir defteri/kısmi dolum/gecikme yok. Hüküm kâr garantisi değildir.", "books": books}


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
    lines.append(f"Hüküm kuralı: {card['min_trades_for_verdict']} işlemden az → VERİ YETERSİZ; ortalama R'nin %95 aralığı tamamen 0'ın "
                 "altında → ZARARDA, tamamen üstünde → KÂRDA; değilse BELİRSİZ. PAPER sonucu, kâr garantisi değildir.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Beş kâğıt defterin maliyet sonrası karnesi (salt okunur).")
    ap.add_argument("--state", required=True, help="state klasörü (ör. C:\\tb-olcum-veri\\state)")
    ap.add_argument("--since", default=None, help="yalnız bu tarihten (YYYY-AA-GG) sonra kapananlar")
    ap.add_argument("--out", default=None, help="ayrıntılı JSON raporu")
    a = ap.parse_args(argv)
    state = Path(a.state)
    if not state.is_dir():
        print(f"state klasörü yok: {state}", file=sys.stderr)
        return 2
    card = scorecard(state, since=a.since)
    if not card["books"]:
        print(f"{state} altında {LEDGER_FILE} bulunamadı", file=sys.stderr)
        return 2
    print(render(card))
    if a.out:
        Path(a.out).write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ayrıntı: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

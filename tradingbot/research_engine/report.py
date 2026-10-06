"""P2b — Türkçe şablon metinleri: `digest_tr.md`'nin P2 bölümleri ve `engine-query` (`trade`, `why-lost`, `day`) çıktısı.

Bütün metin şablondan üretilir (LLM yok). Sayılar Türkçe ondalık virgülüyle; R işaretli (`+0,42R`). Kurallar (§5.4, §5.5,
§7, §8):

* Tek işlem kodu GÖZLEMDİR ("neden kanıtı değil"); toplu sayımlar ara görünümdür, ders değildir (ders P3).
* İşlem başına "kâra dönen hücreler" ve `hindsight_best` daima **HINDSIGHT** etiketiyle yazılır ("geriye dönük; kural
  değil"); kural olabilecek tek şey defter düzeyinde, BÜTÜN işlemlerde aynı sabit ızgara noktasının (EX_ANTE) eşli
  farkıdır — o da muhafazakâr R (`cf_aux_v1`) ile sıralanır ve "ara görünüm, ders değil" notuyla yazılır.
* Sorgu çıktısı ≤ 150 satır ve ≤ 4 KB (§8); özet ≤ 8 KB (`summary.DIGEST_MAX_BYTES`).
* Yalnız-gerçekleşmiş bir satırda hedef hüküm kelimeleri kullanılmaz (P1a kabul 5; `summary.claim_word_violations`).
"""
from __future__ import annotations

import heapq
from datetime import datetime, timedelta
from typing import Any, Iterable

from . import analysis as AN
from . import attribution as AT
from . import cfgrid as CG
from .ledgers import book_name, parse_ts

QUERY_MAX_LINES = 150
QUERY_MAX_BYTES = 4096
VARIANT_TR = {v[0]: v[3] for v in CG.VARIANTS}
COMP_TR = (("signal_R", "sinyal"), ("timing_R", "zamanlama"), ("stop_R", "stop"), ("exit_R", "çıkış"),
           ("size_lev_R", "boyut/kaldıraç"), ("cost_R", "maliyet"))


# ============================================================================ biçim
def fr(v: Any, nd: int = 2, sign: bool = True) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return s.replace(".", ",").replace("-", "−")


def side_tr(side: Any) -> str:
    return "kısa" if str(side or "").upper() in ("SHORT", "SELL") else "uzun"


def sym_short(sym: Any) -> str:
    return str(sym or "?").replace("/USDT", "").replace("USDT", "")


def code_tr(code: str | None) -> str:
    if not code:
        return "sınıflanmadı"
    return AT.CODE_TR.get(code, code)


def _cost_usdt(r: dict) -> tuple[float, float, float]:
    fee = float(r.get("fees_total") or 0)
    fund = -float(r.get("funding_net") or 0)                    # pozitif = ödenen
    slip = float(r.get("slippage_cost") or 0)
    return fee, fund, slip


def _net(r: dict) -> float:
    try:
        return float(r.get("net_pnl") or 0)
    except (TypeError, ValueError):
        return 0.0


def _nr(r: dict) -> float | None:
    try:
        return float(r["net_r"]) if r.get("net_r") is not None else None
    except (TypeError, ValueError):
        return None


def trade_line(r: dict, *, hindsight_cells: int = 3) -> str:
    a = r.get("attribution") or {}
    g = r.get("grid") or {}
    codes = (a.get("codes_loss") or []) + (a.get("codes_win") or [])
    pc = a.get("primary_code")
    others = [c for c in codes if c != pc]
    cost = (a.get("costs_r") or {}).get("cost_all")
    s = (f"- {sym_short(r.get('symbol'))} {side_tr(r.get('side'))} {fr(_nr(r))}R ({fr(_net(r), 2)} USDT) · "
         f"{code_tr(pc)} [{pc or '—'}]")
    if others:
        s += " · +" + ",".join(others[:3])
    if cost is not None:
        s += f" · maliyet {fr(cost, 2, sign=False)}R"
    if g.get("status") == "OK":
        hb = g.get("hindsight_best") or {}
        cells = [c for c in (g.get("profitable_cells_hindsight") or []) if c != "SKIP"]
        if a.get("outcome") == AT.LOSS:
            if hb.get("cell") and hb.get("cell") not in ("E_ACTUAL", "SKIP"):
                s += f" · en iyi EX_ANTE hücre (HINDSIGHT): {hb['cell']} {fr(hb.get('r_rank'))}R"
            elif hb.get("cell") == "SKIP":
                s += " · en iyi hücre: atlamak (HINDSIGHT)"
            s += (" · kâra dönen: " + ",".join(cells[:hindsight_cells])) if cells else " · kâra dönen hücre yok"
        elif hb.get("cell") and hb.get("cell") != "E_ACTUAL" and (hb.get("delta_vs_actual") or 0) > 0:
            s += f" · en iyi EX_ANTE hücre (HINDSIGHT): {hb['cell']} {fr(hb.get('delta_vs_actual'))}R daha iyi"
    elif a.get("outcome") == AT.LOSS:
        s += f" · ızgara yok ({g.get('reason')})"
    return s


# ============================================================================ defter düzeyi (akış birikimleri)
class BookAgg:
    """Bir defterin pencere birikimi: sayılar, toplamlar, maliyetler, kayıp kodları ve en kötü `keep` işlem satırı
    (yalnız metin tutulur; satırların kendisi bellekte tutulmaz)."""

    def __init__(self, book: str, keep: int = 6) -> None:
        self.book, self.keep = book, keep
        self.n = self.wins = self.n_r = 0
        self.sum_r = self.net = self.fee = self.fund = self.slip = 0.0
        self.losses = 0
        self.loss_r = 0.0
        self.primary: dict[str, int] = {}
        self.codes: dict[str, int] = {}
        self._heap: list[tuple[float, int, str]] = []     # (−değer, sıra, satır): en kötü `keep`
        self._seq = 0

    def add(self, r: dict, *, line_suffix: str = "") -> None:
        a = r.get("attribution") or {}
        self.n += 1
        nr = _nr(r)
        if a.get("outcome") == AT.WIN:
            self.wins += 1
        else:
            self.losses += 1
            self.loss_r += nr or 0.0
            pc = a.get("primary_code") or "SINIFLANMADI"
            self.primary[pc] = self.primary.get(pc, 0) + 1
            for c in a.get("codes_loss") or []:
                self.codes[c] = self.codes.get(c, 0) + 1
        if nr is not None:
            self.n_r += 1
            self.sum_r += nr
        self.net += _net(r)
        f, fu, sl = _cost_usdt(r)
        self.fee, self.fund, self.slip = self.fee + f, self.fund + fu, self.slip + sl
        if self.keep > 0:
            v = nr if nr is not None else _net(r)
            self._seq += 1
            item = (-v, self._seq, trade_line(r) + line_suffix)
            if len(self._heap) < self.keep:
                heapq.heappush(self._heap, item)
            elif item > self._heap[0]:
                heapq.heapreplace(self._heap, item)

    def worst_lines(self) -> list[str]:
        return [ln for _v, _s, ln in sorted(self._heap, key=lambda x: (-x[0], x[1]))]

    def header(self) -> str:
        return (f"{book_name(self.book)}: {self.n} işlem · {self.wins} kâr / {self.n - self.wins} zarar · Σ {fr(self.sum_r)}R "
                f"({self.n_r} R'li) · Σ {fr(self.net)} USDT · maliyet: ücret {fr(self.fee, 2, False)} / fonlama "
                f"{fr(self.fund, 2)} / kayma {fr(self.slip, 2, False)} USDT")

    def primary_sorted(self) -> list[tuple[str, int]]:
        return sorted(self.primary.items(), key=lambda x: (-x[1], x[0]))

    def codes_sorted(self) -> list[tuple[str, int]]:
        return sorted(self.codes.items(), key=lambda x: (-x[1], x[0]))


def by_book(rows: Iterable[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r.get("book")), []).append(r)
    return dict(sorted(out.items()))


def book_header(b: str, rs: list[dict]) -> str:
    agg = BookAgg(b, keep=0)
    for r in rs:
        agg.add(r)
    return agg.header()


def _ex_ante_text(best: tuple[str, dict] | None) -> str | None:
    if best is None:
        return None
    vid, st = best
    return (f"  EX_ANTE en iyi sabit hücre (tüm işlemlerde aynı; n={st['n']}, {st['n_days']} gün): {vid} — "
            f"{VARIANT_TR.get(vid, vid)}: ort. ΔR {fr(st['mean_delta_r'])} (muhafazakâr) · daha iyi olduğu işlem payı "
            f"%{round(st['share_better'] * 100)} — ara görünüm, ders değil")


def ex_ante_line(rows: Iterable[dict], *, min_n: int = 5) -> str | None:
    return _ex_ante_text(AN.best_ex_ante(rows, min_n=min_n))


def code_counts(rows: Iterable[dict], *, losses_only: bool = True) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        a = r.get("attribution") or {}
        if losses_only and a.get("outcome") != AT.LOSS:
            continue
        pc = a.get("primary_code") or "SINIFLANMADI"
        out[pc] = out.get(pc, 0) + 1
    return dict(sorted(out.items(), key=lambda x: (-x[1], x[0])))


# ============================================================================ digest bölümleri
def digest_sections(paths: Any, st: dict, *, now: datetime, max_trades_per_book: int = 6) -> list[tuple[str, list[str]]]:
    """`digest_tr.md`'nin P2 bölümleri (öncelik sırasıyla; boyut sınırı aşılırsa sondan kısaltılır). Son 30 günün atıf
    satırları TEK akışla okunur (bellekte satır tutulmaz)."""
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    since7 = now - timedelta(days=7)
    day_agg: dict[str, BookAgg] = {}
    week_agg: dict[str, BookAgg] = {}
    var_acc: dict[str, AN.VariantAcc] = {}
    for r in AN.iter_window(paths, since=now - timedelta(days=30), until=now):
        b = str(r.get("book"))
        var_acc.setdefault(b, AN.VariantAcc()).add(r)
        t = parse_ts(r.get("closed_at"))
        if t is not None and t >= since7:
            week_agg.setdefault(b, BookAgg(b, keep=0)).add(r)
        if str(r.get("closed_at") or "")[:10] == yday:
            day_agg.setdefault(b, BookAgg(b, keep=max_trades_per_book)).add(r)
    out: list[tuple[str, list[str]]] = []
    lines = [f"## Dün kapanan işlemler ({yday} UTC) — neden kaybetti / kazandı", "",
             "Kod tek işlem için GÖZLEMDİR (kanıt değil). \"Kâra dönen hücre\" ve \"en iyi hücre\" geriye dönüktür "
             "(HINDSIGHT, kural değil).", ""]
    if not day_agg:
        lines.append("Dün kapanan işlem yok (ya da S2 henüz işlemedi).")
    for b, agg in sorted(day_agg.items()):
        lines.append("**" + agg.header() + "**")
        lines += agg.worst_lines()
        if agg.n > max_trades_per_book:
            lines.append(f"- … +{agg.n - max_trades_per_book} işlem (engine-query day {yday})")
    lines.append("")
    out.append(("p2_day", lines))
    lines = ["## Neden kaybetti — son 7 gün (birincil kod, zarar eden işlemler)", ""]
    any_l = False
    for b, agg in sorted(week_agg.items()):
        if not agg.primary:
            continue
        any_l = True
        lines.append(f"- {book_name(b)}: " + " · ".join(f"{k} {v}" for k, v in agg.primary_sorted()[:6]))
    if not any_l:
        lines.append("Son 7 günde atıflı zarar yok.")
    lines.append("")
    out.append(("p2_why", lines))
    lines = ["## Nasıl kâra dönebilirdi — sabit ızgara (EX_ANTE, son 30 gün)", "",
             "Eşli fark: aynı hücre BÜTÜN işlemlerde (seçilmiş değil); sıralama cf_aux_v1 muhafazakâr R. Ders P3'te "
             "(n ≥ 30, ≥ 10 gün, ileri doğrulama).", ""]
    any_x = False
    for b, acc in sorted(var_acc.items()):
        ln = _ex_ante_text(acc.best())
        if ln:
            any_x = True
            lines.append(f"- {book_name(b)}:")
            lines.append(ln)
    if not any_x:
        lines.append("Henüz yeterli ızgara satırı yok (defter başına en az 5 işlem).")
    lines.append("")
    out.append(("p2_exante", lines))
    out.append(("p2_health", health_lines(paths, st)))
    return out


def health_lines(paths: Any, st: dict) -> list[str]:
    stages = st.get("stages") or {}
    s1b = (stages.get("S1b") or {})
    s2 = (stages.get("S2") or {})
    r1 = s1b.get("result") or {}
    r2 = s2.get("result") or {}
    jb = _read_json(paths.journal_tj / "_build.json") or {}
    ab = AN.build_status(paths) or {}
    lines = ["## Veri ve doğruluk sağlığı", ""]
    j = r1.get("journal") or {}
    rec = r1.get("reconcile") or {}
    lines.append(f"- S1b günlük: {s1b.get('status', '—')} · {jb.get('rows', j.get('rows', '—'))} satır · bu gece "
                 f"{j.get('built_rows', '—')} kuruldu · bekleyen {j.get('pending', 0) or 0} · uzlaştırma "
                 f"{rec.get('status', '—')} · sahip alanları {'tam' if jb.get('owner_fields_ok') else 'EKSİK'} · ücret "
                 f"özdeşliği ihlali {jb.get('fee_identity_violations', '—')}")
    fd = jb.get("fidelity") or {}
    fails = fd.get("failures_by_reason") or {}
    lines.append(f"- Fidelity (§5.3): {fd.get('ok', '—')}/{fd.get('eligible', '—')} = %{fr((fd.get('rate') or 0) * 100, 1, False)}"
                 f" (hedef %90: {'geçti' if fd.get('gate_pass') else 'KALDI'}) · nedenler "
                 + (", ".join(f"{k} {v}" for k, v in fails.items()) or "—") + " · uygun değil "
                 + (", ".join(f"{k} {v}" for k, v in (fd.get("not_eligible") or {}).items()) or "—"))
    rh = jb.get("rehydrate") or {}
    parts = []
    for b, eps in rh.items():
        for ep, v in eps.items():
            parts.append(f"{book_name(b)[:14]} {ep} %{fr((v.get('rate') or 0) * 100, 0, False)} ({v.get('n')})")
    lines.append("- Rehydrate eşleşmesi (hedef ≥ %95, dönem başına): " + ("; ".join(parts[:8]) or "—"))
    ps = jb.get("path_sources") or {}
    lines.append("- Yol kaynağı: " + (" · ".join(f"{k} {v}" for k, v in ps.items()) or "—")
                 + (f" · DATA_MOVING {len(jb.get('data_moving') or {})}" if jb.get("data_moving") else ""))
    g = ab.get("grid") or {}
    lines.append(f"- S2 atıf: {s2.get('status', '—')}" + (f" ({s2.get('reason')})" if s2.get("reason") else "")
                 + f" · {ab.get('rows', '—')} satır · ızgara OK {g.get('OK', 0)} · uygun değil "
                 + (", ".join(f"{k[3:]} {v}" for k, v in g.items() if k.startswith("NE:")) or "—")
                 + f" · bekleyen {r2.get('pending', 0) or 0} · attribution_v1 {str(ab.get('attribution_sha', AT.ATTRIBUTION_SHA))[:12]}"
                 + f" · cfgrid_v1 {str(ab.get('cfgrid_sha', CG.CFGRID_SHA))[:12]}")
    ds = (st.get("selfcheck") or {}).get("data") or {}
    if ds.get("status") not in (None, "NONE"):
        lines.append(f"- Veri mührü: {ds.get('status')} · {str(ds.get('data_seal') or '—')[:12]} · yaş {ds.get('age_h')} sa")
    lines.append("")
    return lines


def utc_day_lines(paths: Any, *, now: datetime) -> list[str]:
    from .utc_day import latest_utc_rows, tgt_v2_status
    rows = latest_utc_rows(paths)
    if not rows:
        return []
    st = tgt_v2_status(paths, today=now.strftime("%Y-%m-%d"), rows=rows)
    last = rows[max(rows)]
    t = last.get("total") or {}
    sbs = last.get("side_by_side") or {}
    return [f"UTC günü (00:00 UTC, LAST_PRICE_PROXY; {st['headline']} başlık · yan yana {st['side_by_side_days']}/"
            f"{st['required_days']} gün): {last.get('day')} MTM toplam {fr(t.get('r_mtm_utc'), 2)}% ({last.get('status')}) · "
            f"W-günü {fr(sbs.get('w_r_mtm'), 2)}%"]


def _read_json(p: Any) -> dict | None:
    import json
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ============================================================================ engine-query
def bound(lines: list[str], *, max_lines: int = QUERY_MAX_LINES, max_bytes: int = QUERY_MAX_BYTES) -> list[str]:
    """≤ 150 satır ve ≤ 4 KB (§8); aşılırsa sondan kesilir ve not düşülür."""
    note = f"… (sınır: {max_lines} satır / {max_bytes // 1024} KB; ayrıntı için daha dar bir sorgu)"
    out = list(lines)
    if len(out) > max_lines:
        out = out[:max_lines - 1] + [note]
    while len("\n".join(out).encode("utf-8")) > max_bytes and len(out) > 2:
        out = out[:-2] + [note]
    return out


def _matches(k: str, ident: str) -> bool:
    return k == ident or k.split("|")[1:2] == [ident]


def find_trade(paths: Any, ident: str) -> tuple[dict | None, dict | None, list[str]]:
    """`trade_key` ya da işlem kimliği → (atıf satırı, günlük satırı, bütün eşleşen anahtarlar). İşlem kimliği defter
    başına sayaçtır (ör. `F00001` her defterde vardır): birden çok eşleşmede satır döndürülmez, anahtarlar listelenir."""
    from .journal import _read_index, iter_journal
    idx = _read_index(paths)                                  # anahtar → ay (günlük dizini; satırlar okunmaz)
    keys = sorted(k for k in idx if _matches(k, ident))
    if len(keys) != 1:
        return None, None, keys
    key = keys[0]
    month = str(idx[key].get("month"))
    hit = next((r for r in AN.iter_attribution(paths, [month]) if str(r.get("trade_key")) == key), None)
    jrow = next((r for r in iter_journal(paths, [month]) if str(r.get("trade_key")) == key), None)
    return hit, jrow, keys


def query_trade(paths: Any, ident: str) -> list[str]:
    r, j, keys = find_trade(paths, ident)
    if len(keys) > 1:
        return bound([f"{len(keys)} işlem eşleşti ({ident}); tam trade_key ile sorun:"] + [f"  {k}" for k in keys[:40]])
    if r is None and j is None:
        return [f"İşlem bulunamadı: {ident} (trade_key ya da işlem kimliği; atıf S2'de, günlük S1b'de)"]
    base = r or j or {}
    out = [f"İŞLEM {base.get('trade_key')} — {book_name(str(base.get('book')))} · PAPER · tanımlayıcı, kanıt değil",
           f"{sym_short(base.get('symbol'))} {side_tr(base.get('side'))} · {base.get('opened_at')} → {base.get('closed_at')} · "
           f"çıkış {base.get('exit_reason')} ({base.get('exit_basis')})",
           f"Sonuç: net {fr(_nr(base))}R · {fr(_net(base))} USDT · brüt {fr(base.get('gross_r'))}R · MFE {fr(base.get('mfe_r'))}R · "
           f"MAE {fr(base.get('mae_r'))}R · sıra {base.get('order')} · capture {fr(base.get('capture_ratio'), 2, False)} · "
           f"yol {base.get('path_source')}"]
    if j is not None:
        out.append(f"Plan: giriş {j.get('entry_fill')} · ilk stop {j.get('initial_stop')} ({fr(j.get('stop_dist_pct'), 3, False)}%) · "
                   f"hedefler {j.get('targets')} · taktik {j.get('tactic')} / {j.get('variation_id')} · dönem {j.get('config_epoch')}")
        fd = j.get("fidelity") or {}
        out.append(f"Fidelity: {fd.get('status')} (ΔR {fd.get('delta_r')}; {', '.join(fd.get('reasons') or []) or '—'})")
    if r is None:
        out.append("Atıf yok: S2 bu işlemi henüz işlemedi (bekleyen iş ya da DATA_STALE).")
        return bound(out)
    a = r.get("attribution") or {}
    cp = a.get("costs_r") or {}
    out.append(f"Maliyet (R): ücret {fr(cp.get('fee'), 3, False)} · kayma {fr(cp.get('slippage'), 3, False)} · fonlama "
               f"{fr(cp.get('funding'), 3)} · toplam {fr(cp.get('cost_all'), 3, False)}")
    out.append("")
    out.append(f"NEDEN ({a.get('outcome')}; attribution_v1, gözlem): birincil {a.get('primary_code') or '—'} — {code_tr(a.get('primary_code'))}")
    for c, ev in (a.get("evidence") or {}).items():
        out.append(f"  {c}: {code_tr(c)} · " + ", ".join(f"{k}={v}" for k, v in ev.items())[:110])
    if a.get("excluded_order"):
        out.append("  (bar içi sıra belirsiz/yok: sıraya dayanan kodlar değerlendirilmedi)")
    if a.get("not_evaluable"):
        out.append("  değerlendirilemeyen: " + ", ".join(a["not_evaluable"]))
    g = r.get("grid") or {}
    out.append("")
    if g.get("status") != "OK":
        out.append(f"IZGARA (cfgrid_v1): yok — {g.get('reason')} (yalnız fidelity kapısını geçen vadeli işlemler)")
    else:
        pl = g.get("plan") or {}
        out.append(f"IZGARA (cfgrid_v1; {g.get('tf')} yol; ufuk {pl.get('horizon')}; ATR({pl.get('entry_tf')}) {pl.get('atr')}; "
                   f"taban farkı {fr(g.get('baseline_gap_r'), 3)}R):")
        out.append(f"  {'hücre':<13}{'etiket':<10}{'R net':>8}{'R muh.':>8}  çıkış · durum")
        for c in g.get("cells") or []:
            out.append(f"  {c['id']:<13}{c['label']:<10}{fr(c.get('r_net')):>8}{fr(c.get('r_cons')):>8}  "
                       f"{c.get('exit') or '—'} · {c.get('status')}")
        hb = g.get("hindsight_best") or {}
        if hb:
            out.append(f"  HINDSIGHT en iyi hücre: {hb.get('cell')} {fr(hb.get('r_rank'))}R (gerçeğe göre {fr(hb.get('delta_vs_actual'))}R) "
                       "— geriye dönük seçim, kural değil")
        pc = g.get("profitable_cells_hindsight") or []
        out.append("  Bu işlemde kâra dönen hücreler (HINDSIGHT): " + (", ".join(pc) if pc else "yok"))
        rc = g.get("random_control") or {}
        out.append(f"  Rastgele kontrol: {rc.get('status')} · n {rc.get('n')} / aday {rc.get('n_candidates')} · kova "
                   f"{rc.get('bucket')} · medyan net {fr(rc.get('median_r_net'))}R · signal_excess {fr(g.get('signal_excess_R'))}R")
        d = g.get("decomposition") or {}
        comp = d.get("components") or {}
        out.append("")
        out.append("R AYRIŞTIRMASI (§5.6; sıralı zincir): " + " + ".join(f"{t} {fr(comp.get(k))}" for k, t in COMP_TR)
                   + f" + artık {fr(d.get('residual_R'))} = net {fr(d.get('net_R'))}")
        rp = d.get("residual_parts") or {}
        out.append(f"  artık = rastgele taban {fr(rp.get('random_baseline_R'))} + yeniden oynatma farkı {fr(rp.get('replay_gap_R'))}"
                   + (f" · eksik: {', '.join(d.get('missing') or [])}" if d.get("missing") else ""))
    rf = r.get("regime_fit") or {}
    out.append(f"regime_fit ({rf.get('source')}): {rf.get('status')} · n {rf.get('n')} · ort. {fr(rf.get('mean_r'))}R")
    return bound(out)


def query_why_lost(paths: Any, *, now: datetime, book: str | None = None, days: int = 7) -> list[str]:
    since = now - timedelta(days=int(days))
    aggs: dict[str, BookAgg] = {}
    accs: dict[str, AN.VariantAcc] = {}
    n_all = 0
    for r in AN.iter_window(paths, since=since, until=now):
        b = str(r.get("book"))
        if book and not (b == book or book_name(b) == book):
            continue
        n_all += 1
        accs.setdefault(b, AN.VariantAcc()).add(r)
        if (r.get("attribution") or {}).get("outcome") == AT.LOSS:
            aggs.setdefault(b, BookAgg(b, keep=5)).add(r, line_suffix=f" · {str(r.get('trade_key')).split('|')[1]}")
    n_loss = sum(a.n for a in aggs.values())
    out = [f"NEDEN KAYBETTİ — son {days} gün{(' · ' + book) if book else ''} · {n_loss} zarar / {n_all} işlem "
           "(attribution_v1; tek işlem kodu gözlemdir)"]
    for b, agg in sorted(aggs.items()):
        out.append("")
        out.append(f"{book_name(b)}: {agg.n} zarar · Σ {fr(agg.loss_r)}R")
        out.append("  birincil: " + " · ".join(f"{k} {v} ({code_tr(None if k == 'SINIFLANMADI' else k)})"
                                               for k, v in agg.primary_sorted()[:6]))
        out.append("  tüm kodlar: " + (" · ".join(f"{k} {v}" for k, v in agg.codes_sorted()[:8]) or "—"))
        ln = _ex_ante_text(accs[b].best()) if b in accs else None
        if ln:
            out.append(ln)
        out += ["  " + x for x in agg.worst_lines()]
    if not n_loss:
        out.append("Bu pencerede atıflı zarar yok.")
    return bound(out)


def query_day(paths: Any, day: str) -> list[str]:
    t = parse_ts(day + "T00:00:00+00:00")
    if t is None:
        return [f"Geçersiz gün: {day} (YYYY-MM-DD)"]
    aggs: dict[str, BookAgg] = {}
    lines_by: dict[str, list[str]] = {}
    for r in AN.iter_attribution(paths, [day[:7]]):
        if str(r.get("closed_at") or "")[:10] != day:
            continue
        b = str(r.get("book"))
        aggs.setdefault(b, BookAgg(b, keep=0)).add(r)
        lb = lines_by.setdefault(b, [])
        if len(lb) < QUERY_MAX_LINES:                     # çıktı zaten 150 satırla sınırlı
            lb.append("  " + trade_line(r) + f" · {str(r.get('trade_key')).split('|')[1]}")
    n = sum(a.n for a in aggs.values())
    out = [f"GÜN {day} (UTC kapanış) — {n} işlem · PAPER · tanımlayıcı"]
    from .daily_target import latest_rows
    w = latest_rows(paths).get(day)
    if w:
        tot = w.get("total") or {}
        out.append(f"Günlük hedef (tgt_v1, W-günü): MTM {fr(tot.get('r_mtm'), 2)}% · gerçekleşmiş cüzdan {fr(tot.get('pnl_wal'), 2)} USDT "
                   f"· durum {w.get('status')}")
    from .utc_day import latest_utc_rows
    u = latest_utc_rows(paths).get(day)
    if u:
        out.append(f"UTC günü (LAST_PRICE_PROXY): MTM {fr((u.get('total') or {}).get('r_mtm_utc'), 2)}% · durum {u.get('status')}")
    for b, agg in sorted(aggs.items()):
        out.append("")
        out.append(agg.header())
        out += lines_by[b]
    if not n:
        out.append("Bu gün kapanmış atıflı işlem yok.")
    return bound(out)


__all__ = ["BookAgg", "QUERY_MAX_BYTES", "QUERY_MAX_LINES", "book_header", "bound", "code_counts", "digest_sections", "ex_ante_line",
           "find_trade", "fr", "health_lines", "query_day", "query_trade", "query_why_lost", "trade_line", "utc_day_lines"]

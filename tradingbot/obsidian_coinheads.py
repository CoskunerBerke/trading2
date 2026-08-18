"""Obsidian v3 yazıcısı — Coin Head kararları, portföy, işlemler, koşu günlükleri, modeller, risk, operasyon.

YALNIZCA yeni klasörlere yazar (Coin Heads/, Portfolio/, Trades/, Runs/, Models/, Risk/, Operations/, Data Quality/);
mevcut Agents/, Coins/, Charts/ vb. dosyalara dokunmaz. Bütün yazımlar atomik ve içerik değişmediyse atlanır.

Canvas: deterministik düğüm id'leri `f"{base}:{role}"`, kenar id'leri `f"{base}:{a}->{b}"`, sabit ızgara
(sütun×560, satır×190) ve GROUP düğümleri: uzmanlar → coin head → spot/futures planı → red team → risk motoru
→ baş yönetici → kağıt icra → işlem sonucu → öğrenme.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core import atomic_write_text, from_iso, iso, istanbul, utc_now

# ----------------------------------------------------------------------------- sabitler
DIR_COIN_HEADS = "Coin Heads"
DIR_PORTFOLIO = "Portfolio"
DIR_TRADES = "Trades"
DIR_RUNS = "Runs"
DIR_MODELS = "Models"
DIR_RISK = "Risk"
DIR_OPS = "Operations"
DIR_DQ = "Data Quality"
OWNED_DIRS = (DIR_COIN_HEADS, DIR_PORTFOLIO, DIR_TRADES, DIR_RUNS, DIR_MODELS, DIR_RISK, DIR_OPS, DIR_DQ)

INCIDENT_CAP = 200
COL_W, ROW_H = 560, 190
NODE_W, NODE_H = 520, 170

FACTOR_GROUP_ORDER = ("trend", "momentum", "volatility", "volume_flow", "structure_levels", "liquidity",
                      "derivatives", "historical_edge", "sentiment", "onchain", "macro", "other")
FACTOR_GROUP_TR = {"trend": "Trend", "momentum": "Momentum", "volatility": "Volatilite", "volume_flow": "Hacim/Akış",
                   "structure_levels": "Yapı/Seviyeler", "liquidity": "Likidite", "derivatives": "Türev",
                   "historical_edge": "Tarihsel Edge", "sentiment": "Duygu", "onchain": "On-chain", "macro": "Makro", "other": "Diğer"}
STANCE_TR = {"STRONG_BULL": "GÜÇLÜ BOĞA", "BULL": "BOĞA", "NEUTRAL": "NÖTR", "BEAR": "AYI", "STRONG_BEAR": "GÜÇLÜ AYI"}
VERDICT_TR = {"SPOT_LONG": "SPOT LONG", "FUTURES_LONG": "FUTURES LONG", "FUTURES_SHORT": "FUTURES SHORT", "HOLD": "TUT",
              "REDUCE": "AZALT", "EXIT": "ÇIK", "NO_TRADE": "İŞLEM YOK", "DATA_INVALID": "VERİ GEÇERSİZ", "RISK_BLOCKED": "RİSK ENGELİ"}
VERDICT_EMOJI = {"SPOT_LONG": "🟢", "FUTURES_LONG": "🟢", "FUTURES_SHORT": "🔴", "HOLD": "🟡", "REDUCE": "🟠", "EXIT": "⛔",
                 "NO_TRADE": "⚪", "DATA_INVALID": "⚫", "RISK_BLOCKED": "🚫"}
# Obsidian canvas renkleri: 1 kırmızı, 2 turuncu, 3 sarı, 4 yeşil, 5 camgöbeği, 6 mor
VERDICT_COLOR = {"SPOT_LONG": "4", "FUTURES_LONG": "4", "FUTURES_SHORT": "1", "HOLD": "3", "REDUCE": "2", "EXIT": "1",
                 "NO_TRADE": "6", "DATA_INVALID": "6", "RISK_BLOCKED": "1"}

# Snapshot tablosu: etiket → metrik anahtar adayları (uzman raporu metrics/levels içinde küçük harf aranır)
SNAPSHOT_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Fiyat", ("price", "close", "last", "last_price")),
    ("MA25", ("ma25", "sma25", "sma_25")),
    ("MA99", ("ma99", "sma99", "sma_99")),
    ("EMA25", ("ema25", "ema_25")),
    ("EMA99", ("ema99", "ema_99")),
    ("EMA200", ("ema200", "ema_200")),
    ("RSI", ("rsi", "rsi14", "rsi_14", "rsi_4h")),
    ("MACD", ("macd", "macd_hist", "macd_line", "macd_h")),
    ("ATR", ("atr", "atr14", "atr_pct", "atr_14")),
    ("Bollinger", ("bb_width", "bb_width_pct", "bb_pos", "bollinger", "bb_squeeze")),
    ("Hacim", ("volume", "vol_ratio", "volume_ratio", "rel_volume", "vol24_usdt")),
    ("Orderbook", ("orderbook_imbalance", "ob_imbalance", "imbalance", "spread_bps", "spread_pct")),
    ("Funding", ("funding", "funding_pct", "funding_rate")),
    ("OI", ("oi", "open_interest", "oi_change_pct", "oi_chg_pct")),
    ("Korelasyon", ("corr_btc", "correlation", "corr", "btc_corr")),
    ("Destek", ("s1", "support", "support1")),
    ("Direnç", ("r1", "resistance", "resistance1")),
)

_DOS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ----------------------------------------------------------------------------- yardımcılar
def safe_base(symbol_or_base: str) -> str:
    """'BTC/USDT' → 'BTC'; dosya adı güvenli; DOS ayrılmış adlar `_` soneki alır."""
    base = str(symbol_or_base or "").split("/")[0].split(":")[0].strip().upper() or "UNKNOWN"
    base = _BAD_CHARS.sub("_", base).rstrip(". ")
    if base.upper() in _DOS_RESERVED:
        base += "_"
    return base


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(s or "")).strip("_").lower() or "x"


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None or x == "":
        return "-"
    if isinstance(x, bool):
        return "evet" if x else "hayır"
    if isinstance(x, (int,)):
        return f"{x:,}"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    if f != f:  # NaN
        return "-"
    if abs(f) >= 1000:
        return f"{f:,.2f}"
    if abs(f) >= 1:
        return f"{f:,.{min(nd, 4)}f}".rstrip("0").rstrip(".") if nd else f"{f:.0f}"
    return f"{f:.6g}"


def _pct(x: Any) -> str:
    try:
        return f"{float(x):+.2f}%"
    except (TypeError, ValueError):
        return "-"


def _md_cell(s: Any) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_md_cell(c) for c in r) + " |")
    return out


def _yaml_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml_val(x) for x in v) + "]"
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`,]", s) or s.strip() != s:
        return json.dumps(s, ensure_ascii=False)
    return s


def _frontmatter(d: dict[str, Any]) -> list[str]:
    return ["---", *[f"{k}: {_yaml_val(v)}" for k, v in d.items()], "---"]


def _now_pair(ts: str | None = None) -> tuple[str, str]:
    """(utc iso, istanbul metni)."""
    try:
        dt = from_iso(ts) if ts else utc_now()
    except (ValueError, TypeError):
        dt = utc_now()
    return iso(dt), istanbul(dt)


def _find_metric(reports: list[dict], keys: tuple[str, ...]) -> Any:
    for r in reports:
        for src in (r.get("metrics") or {}, r.get("levels") or {}):
            low = {str(k).lower(): v for k, v in src.items()}
            for k in keys:
                if k in low and low[k] not in (None, ""):
                    return low[k]
    return None


def _spec_sort_key(r: dict) -> tuple[int, str]:
    g = str(r.get("factor_group") or "other")
    idx = FACTOR_GROUP_ORDER.index(g) if g in FACTOR_GROUP_ORDER else len(FACTOR_GROUP_ORDER)
    return idx, str(r.get("agent_name") or "")


def _bias_color(r: dict) -> str:
    if r.get("error") or r.get("veto"):
        return "1" if r.get("veto") else "6"
    b = float(r.get("bias") or 0.0)
    return "4" if b >= 0.15 else ("1" if b <= -0.15 else "3")


def _plan_lines(p: dict | None, kind: str) -> list[str]:
    if not p:
        return [f"- {kind} planı yok."]
    valid = bool(p.get("valid"))
    ez = p.get("entry_zone") or [0, 0]
    tg = p.get("targets") or []
    size = p.get("size") or {}
    lines = [
        f"- Durum: {'✅ GEÇERLİ' if valid else '⛔ GEÇERSİZ — ' + str(p.get('invalid_reason') or '')}",
        f"- Yön: **{p.get('direction', '-')}** · giriş tipi: {p.get('entry_type') or '-'} · tetik: {p.get('entry_trigger') or '-'}",
        f"- Giriş bölgesi: {_fmt(ez[0] if len(ez) > 0 else None)} – {_fmt(ez[1] if len(ez) > 1 else None)} (orta {_fmt(p.get('entry'))})",
        f"- Stop: {_fmt(p.get('stop'))} (%{_fmt(p.get('stop_pct'), 2)}) · Hedefler: {', '.join(_fmt(t) for t in tg) or '-'}",
        f"- Boyut: {_fmt(size.get('amount'))} {size.get('amount_type', 'base')} · kaldıraç {size.get('leverage', 1)}x · marj ≈ {_fmt(p.get('margin'), 2)} USDT · notional ≈ {_fmt(p.get('notional'), 2)} USDT",
        f"- Beklenen maliyet: %{_fmt(p.get('expected_cost_pct'), 3)} · beklenen R: {_fmt(p.get('expected_r'), 2)} · ufuk: {p.get('time_horizon_bars', 0)} bar",
    ]
    if p.get("invalidation"):
        lines.append(f"- Geçersizleşme: {p['invalidation']}")
    return lines


# ----------------------------------------------------------------------------- yazıcı
class ObsidianCoinHeadWriter:
    """Kasa kökü altında yalnızca `OWNED_DIRS` klasörlerine yazar."""

    def __init__(self, vault_root: Path | str) -> None:
        self.root = Path(vault_root)

    # ---- düşük seviye
    def _path(self, folder: str, name: str) -> Path:
        return self.root / folder / name

    def _write(self, path: Path, text: str) -> bool:
        return atomic_write_text(path, text, skip_if_unchanged=True)

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ================================================================= COIN HEAD
    def write_coin_head(self, decision: dict, brief: dict | None = None, chart_rel: str | None = None) -> Path:
        """`Coin Heads/<BASE>.md` + `.canvas`. Dönen: .md yolu."""
        d = decision or {}
        base = safe_base(d.get("symbol", ""))
        reports = sorted([r for r in (d.get("specialist_reports") or []) if isinstance(r, dict)], key=_spec_sort_key)
        chart = chart_rel or (brief or {}).get("chart") or ""
        md = self._coin_note(d, base, reports, brief or {}, chart)
        canvas = self._coin_canvas(d, base, reports, chart)
        md_path = self._path(DIR_COIN_HEADS, f"{base}.md")
        self._write(md_path, md)
        self._write(self._path(DIR_COIN_HEADS, f"{base}.canvas"), json.dumps(canvas, ensure_ascii=False, indent=1))
        return md_path

    def _coin_note(self, d: dict, base: str, reports: list[dict], brief: dict, chart: str) -> str:
        verdict = str(d.get("verdict") or "NO_TRADE")
        vt = VERDICT_TR.get(verdict, verdict)
        upd_utc, upd_local = _now_pair(d.get("generated_at"))
        sp, fp = d.get("spot_plan") or None, d.get("futures_plan") or None
        fm = {
            "symbol": d.get("symbol", ""), "base": base, "verdict": verdict, "direction": d.get("direction") or "",
            "confidence": round(float(d.get("confidence_calibrated") or 0.0), 4), "p_win": round(float(d.get("p_win") or 0.0), 4),
            "regime": d.get("regime") or "UNKNOWN", "market_type": d.get("market_type") or "none",
            "spot_plan_valid": bool(sp and sp.get("valid")), "futures_plan_valid": bool(fp and fp.get("valid")),
            "updated_utc": upd_utc, "updated_local": upd_local, "run_id": d.get("run_id") or "", "tags": ["coin-head"],
        }
        run_date = upd_utc[:10]
        out = _frontmatter(fm)
        out += [f"# 🧠 {d.get('symbol', base)} — Coin Head: {VERDICT_EMOJI.get(verdict, '')} {vt}",
                f"> Güven (kalibre) **%{float(d.get('confidence_calibrated') or 0) * 100:.0f}** · P(kazanç) **%{float(d.get('p_win') or 0) * 100:.0f}** · "
                f"rejim **{d.get('regime') or 'UNKNOWN'}** · piyasa {d.get('market_type') or 'none'} · {upd_local} (TR) / {upd_utc}",
                "",
                f"Şema: [[{DIR_COIN_HEADS}/{base}.canvas]] · Backtest: [[Coins/{base}]] · Eski ajanlar: [[Agents/{base}]] · "
                f"[[{DIR_PORTFOLIO}/Futures]] · [[{DIR_PORTFOLIO}/Spot]] · Koşu: [[{DIR_RUNS}/{run_date}]] · [[{DIR_RISK}/Kill Switch]]", ""]
        if chart:
            out += [f"![[{chart}]]", ""]
        # Snapshot
        rows = []
        price = _find_metric(reports, SNAPSHOT_KEYS[0][1]) or brief.get("price")
        for label, keys in SNAPSHOT_KEYS:
            val = _find_metric(reports, keys) if label != "Fiyat" else price
            if val is None and label in ("Destek", "Direnç"):
                lv = brief.get("key_levels") or {}
                val = lv.get("s1" if label == "Destek" else "r1")
            if val is not None:
                rows.append([label, _fmt(val)])
        out += ["## 📸 Snapshot"]
        out += _table(["Gösterge", "Değer"], rows) if rows else ["- (uzman metrikleri yok)"]
        out.append("")
        # Uzman ajanlar
        out += ["## 🤖 UZMAN AJANLAR"]
        if reports:
            rws = []
            for r in reports:
                st = STANCE_TR.get(str(r.get("stance") or "NEUTRAL"), str(r.get("stance") or "-"))
                ev = (r.get("evidence_for") or [])[:1] + (r.get("evidence_against") or [])[:1]
                flag = "🚫 VETO" if r.get("veto") else ("❌ hata" if r.get("error") else "")
                rws.append([r.get("agent_name", "-"), FACTOR_GROUP_TR.get(str(r.get("factor_group") or ""), r.get("factor_group") or "-"),
                            st, f"{float(r.get('bias') or 0):+.2f}", f"{float(r.get('confidence_raw') or 0):.0f}",
                            "; ".join(str(e)[:90] for e in ev) or (r.get("error") or "-"), flag])
            out += _table(["Ajan", "Grup", "Duruş", "Bias", "Güven", "Kanıt", "Bayrak"], rws)
        else:
            out.append("- (uzman raporu yok)")
        fs = d.get("factor_scores") or []
        if fs:
            out += ["", "**Faktör grubu skorları**"]
            out += _table(["Grup", "Skor", "Güven", "Veri kalitesi", "Bağımsız", "Çatışma"],
                          [[FACTOR_GROUP_TR.get(str(f.get("group")), f.get("group")), f"{float(f.get('score') or 0):+.2f}",
                            f"{float(f.get('confidence') or 0):.2f}", f"{float(f.get('data_quality') or 0):.2f}",
                            f.get("n_independent", 0), f"{float(f.get('conflict') or 0):.2f}"] for f in fs])
        out.append("")
        # Karar
        out += ["## 🧭 COIN HEAD KARARI",
                f"- Karar: **{VERDICT_EMOJI.get(verdict, '')} {vt}**" + (f" · yön **{d.get('direction')}**" if d.get("direction") else ""),
                *([f"- Gerekçe: `{d.get('no_trade_reason')}`"] if d.get("no_trade_reason") else []),
                f"- Konsensüs: {', '.join(f'{FACTOR_GROUP_TR.get(k, k)} {float(v):+.2f}' for k, v in (d.get('consensus') or {}).items()) or '-'}",
                f"- Beklenen net getiri: {_pct(d.get('expected_return_net'))} notional · beklenen R {_fmt(d.get('expected_r'), 2)} · "
                f"beklenen kayıp (ES) {_pct(d.get('expected_shortfall'))}",
                f"- Giriş tetiği: {d.get('entry_trigger') or '-'} · geçersizleşme: {d.get('invalidation') or '-'}",
                f"- Stop {_fmt(d.get('stop'))} · hedefler {', '.join(_fmt(t) for t in (d.get('targets') or [])) or '-'} · ufuk {d.get('time_horizon', 0)} bar",
                f"- Boyut: {_fmt(d.get('position_size'))} base · marj {_fmt(d.get('margin'), 2)} · notional {_fmt(d.get('notional'), 2)} · kaldıraç {d.get('leverage', 1)}x",
                f"- Geçerlilik sonu: {d.get('expires_at') or '-'}"]
        if d.get("dissent"):
            out += ["- Muhalefet:", *[f"  - {x}" for x in d["dissent"][:8]]]
        out.append("")
        out += ["## 🟢 SPOT PLANI", *_plan_lines(sp, "Spot"), "", "## ⚡ FUTURES PLANI", *_plan_lines(fp, "Futures"), ""]
        # Red team
        vetoes = list(d.get("vetoes") or [])
        vet_reports = [r for r in reports if r.get("veto")]
        out += ["## 🔴 RED TEAM"]
        if vetoes or vet_reports:
            out += [f"- 🚫 {v}" for v in vetoes]
            out += [f"- 🚫 {r.get('agent_name')}: {r.get('veto_reason') or '-'}" for r in vet_reports if r.get("veto_reason") not in vetoes]
        else:
            out.append("- Veto yok.")
        warns = [w for r in reports for w in (r.get("warnings") or [])]
        if warns:
            out += ["", "**Uyarılar**", *[f"- ⚠️ {w}" for w in dict.fromkeys(warns)][:12]]
        out.append("")
        # Risk
        nea = d.get("net_exposure_after") or {}
        out += ["## 🛡️ RİSK",
                f"- Risk motoru izni: bkz. [[{DIR_RISK}/Limits]] · [[{DIR_RISK}/Exposure]] · kill switch [[{DIR_RISK}/Kill Switch]]",
                f"- Sonraki net maruziyet: {', '.join(f'{k} {_fmt(v, 2)}' for k, v in nea.items()) or '-'}",
                f"- Onay bayrakları: coin_head_valid · no_red_team_veto · risk_engine_allowed (üçü birden gerekli)", ""]
        # Maliyet
        cost_p = fp if (fp and fp.get("valid")) else sp
        out += ["## 💸 MALİYET TAHMİNİ",
                f"- Brüt beklenen getiri {_pct(d.get('expected_return_gross'))} · maliyet {_pct(d.get('expected_cost'))} · net {_pct(d.get('expected_return_net'))}",
                f"- Plan maliyeti: %{_fmt((cost_p or {}).get('expected_cost_pct'), 3)} (komisyon + kayma + funding tahmini)", ""]
        # Son işlemler / dersler (brief'ten ya da decision içine iliştirilmiş)
        recent = d.get("recent_trades") or brief.get("recent_trades") or []
        out += ["## 📜 SON İŞLEMLER"]
        if recent:
            out += _table(["İşlem", "Yön", "Giriş", "Çıkış", "PnL", "R", "Neden"],
                          [[f"[[{DIR_TRADES}/{t.get('id')}]]", t.get("side"), _fmt(t.get("entry")), _fmt(t.get("exit_price")),
                            _fmt(t.get("pnl"), 2), _fmt(t.get("r_multiple"), 2), t.get("exit_reason")] for t in recent[:10]])
        else:
            out.append("- (bu sembolde kapanmış işlem yok)")
        lessons = d.get("lessons") or brief.get("lessons") or []
        out += ["", "## 🎓 ÖĞRENİLEN DERSLER", *([f"- {x}" for x in lessons[:10]] or ["- (henüz ders yok)"]), ""]
        # Model / veri tazeliği
        mv = d.get("model_versions") or {}
        fresh = d.get("data_freshness") or {}
        out += ["## 🧪 MODEL / VERİ TAZELİĞİ",
                f"- Modeller: {', '.join(f'{k}={v}' for k, v in mv.items()) or '-'} · [[{DIR_MODELS}/Registry]]",
                f"- Veri tazeliği: {', '.join(f'{k}: {v}' for k, v in fresh.items()) or '-'} · [[{DIR_DQ}/Feeds]]",
                f"- run_id `{d.get('run_id') or '-'}` · snapshot `{d.get('snapshot_id') or '-'}` · coin_head_id `{d.get('coin_head_id') or '-'}` · gecikme {_fmt(d.get('latency_ms'), 0)} ms",
                "", "> ⚠️ Otomatik teknik analiz; yatırım tavsiyesi değildir. Gerçek emir gönderilmez (PAPER)."]
        return "\n".join(out) + "\n"

    def _coin_canvas(self, d: dict, base: str, reports: list[dict], chart: str) -> dict:
        verdict = str(d.get("verdict") or "NO_TRADE")
        vc = VERDICT_COLOR.get(verdict, "6")
        nodes: list[dict] = []
        edges: list[dict] = []

        def nid(role: str) -> str:
            return f"{base}:{role}"

        def node(role: str, col: int, row: int, text: str, color: str | None = None, *, w: int = NODE_W, h: int = NODE_H) -> str:
            n = {"id": nid(role), "type": "text", "x": col * COL_W, "y": row * ROW_H, "width": w, "height": h, "text": text}
            if color:
                n["color"] = color
            nodes.append(n)
            return n["id"]

        def edge(a: str, b: str, label: str = "", color: str | None = None, sides: tuple[str, str] = ("right", "left")) -> None:
            e = {"id": f"{base}:{a}->{b}", "fromNode": nid(a), "fromSide": sides[0], "toNode": nid(b), "toSide": sides[1]}
            if label:
                e["label"] = label
            if color:
                e["color"] = color
            edges.append(e)

        n_spec = max(1, len(reports))
        mid = n_spec // 2
        # sütun 0: uzmanlar (grup)
        spec_roles: list[str] = []
        for i, r in enumerate(reports):
            role = f"ag_{_slug(r.get('agent_name'))}"
            spec_roles.append(role)
            st = STANCE_TR.get(str(r.get("stance") or "NEUTRAL"), str(r.get("stance") or "-"))
            ev = (r.get("evidence_for") or r.get("evidence_against") or [""])[0]
            txt = (f"### {r.get('agent_name')} · {FACTOR_GROUP_TR.get(str(r.get('factor_group') or ''), r.get('factor_group') or '')}\n"
                   f"**{st}** · bias {float(r.get('bias') or 0):+.2f} · güven {float(r.get('confidence_raw') or 0):.0f}\n{str(ev)[:200]}")
            if r.get("veto"):
                txt += f"\n🚫 VETO: {str(r.get('veto_reason') or '')[:100]}"
            elif r.get("error"):
                txt += f"\n❌ {str(r.get('error'))[:100]}"
            node(role, 0, i, txt, _bias_color(r))
            edge(role, "head", f"{st.lower()} · {float(r.get('confidence_raw') or 0):.0f}", _bias_color(r))
        if not reports:
            node("ag_none", 0, 0, "### Uzman raporu yok", "6")
            spec_roles.append("ag_none")
            edge("ag_none", "head")
        nodes.append({"id": nid("grp_specialists"), "type": "group", "label": "UZMAN AJANLAR", "x": -20, "y": -60,
                      "width": COL_W, "height": n_spec * ROW_H + 60})
        # sütun 1: coin head
        vt = VERDICT_TR.get(verdict, verdict)
        head_txt = (f"## 🧠 {base} COIN HEAD\n**{VERDICT_EMOJI.get(verdict, '')} {vt}**" + (f" · {d.get('direction')}" if d.get("direction") else "") +
                    f"\nGüven %{float(d.get('confidence_calibrated') or 0) * 100:.0f} · P(kazanç) %{float(d.get('p_win') or 0) * 100:.0f} · rejim {d.get('regime') or '-'}\n"
                    f"Net beklenen getiri {_pct(d.get('expected_return_net'))} · R {_fmt(d.get('expected_r'), 2)}\n"
                    + (f"Gerekçe: {d.get('no_trade_reason')}\n" if d.get("no_trade_reason") else "")
                    + f"[[{DIR_COIN_HEADS}/{base}|Tam not →]]")
        node("head", 1, mid, head_txt, vc, h=NODE_H + 40)
        # sütun 2: planlar
        sp, fp = d.get("spot_plan") or {}, d.get("futures_plan") or {}

        def plan_txt(p: dict, title: str) -> str:
            if not p:
                return f"## {title}\n— plan yok"
            ok = "✅ GEÇERLİ" if p.get("valid") else f"⛔ {p.get('invalid_reason') or 'geçersiz'}"
            return (f"## {title} — {p.get('direction', '-')}\n{ok}\nGiriş ~{_fmt(p.get('entry'))} · stop {_fmt(p.get('stop'))} (%{_fmt(p.get('stop_pct'), 2)})\n"
                    f"Hedefler {', '.join(_fmt(t) for t in (p.get('targets') or [])) or '-'} · R {_fmt(p.get('expected_r'), 2)}\n"
                    f"Marj {_fmt(p.get('margin'), 2)} · notional {_fmt(p.get('notional'), 2)} · {(p.get('size') or {}).get('leverage', 1)}x")

        node("spot_plan", 2, max(0, mid - 1), plan_txt(sp, "🟢 SPOT PLANI"), "4" if sp.get("valid") else "6")
        node("fut_plan", 2, mid + 1, plan_txt(fp, "⚡ FUTURES PLANI"), ("4" if fp.get("direction") == "LONG" else "1") if fp.get("valid") else "6")
        edge("head", "spot_plan", "spot")
        edge("head", "fut_plan", "futures")
        nodes.append({"id": nid("grp_decision"), "type": "group", "label": "COIN HEAD KARARI", "x": COL_W - 20, "y": -60,
                      "width": 2 * COL_W, "height": n_spec * ROW_H + 60})
        # sütun 3: red team
        vetoes = list(d.get("vetoes") or [])
        rt = "## 🔴 RED TEAM\n" + ("\n".join(f"- 🚫 {v}" for v in vetoes[:5]) if vetoes else "- veto yok")
        node("red_team", 3, mid, rt, "1" if vetoes else "4")
        edge("spot_plan", "red_team", "kontrol")
        edge("fut_plan", "red_team", "kontrol")
        # sütun 4: risk motoru
        nea = d.get("net_exposure_after") or {}
        risk_txt = ("## 🛡️ RİSK MOTORU\nOnay bayrakları: coin_head_valid · no_red_team_veto · risk_engine_allowed\n"
                    + (f"Sonraki maruziyet: {', '.join(f'{k} {_fmt(v, 2)}' for k, v in list(nea.items())[:4])}\n" if nea else "")
                    + f"[[{DIR_RISK}/Limits]] · [[{DIR_RISK}/Kill Switch]]")
        node("risk_engine", 4, mid, risk_txt, "5")
        edge("red_team", "risk_engine", "veto yok" if not vetoes else "VETO", "1" if vetoes else None)
        # sütun 5: baş yönetici
        node("chief", 5, mid, f"## 🏛️ BAŞ YÖNETİCİ\nPortföy bağlamı, sıralama, küme limitleri\n[[Agents/Baş Yönetici]] · [[{DIR_PORTFOLIO}/Futures]]", "5")
        edge("risk_engine", "chief", "izin")
        nodes.append({"id": nid("grp_control"), "type": "group", "label": "KONTROL KATMANI", "x": 3 * COL_W - 20, "y": -60,
                      "width": 3 * COL_W, "height": n_spec * ROW_H + 60})
        # sütun 6-8: icra → sonuç → öğrenme
        node("paper_exec", 6, mid, f"## 📝 KAĞIT İCRA\nPAPER modunda kağıt emir; gerçek emir yok.\n[[{DIR_PORTFOLIO}/Futures]] · [[{DIR_PORTFOLIO}/Spot]]", "3")
        node("trade_result", 7, mid, f"## 📊 İŞLEM SONUCU\nKapanış → [[{DIR_TRADES}]] notu (dondurulur), PnL/R/MAE/MFE", "3")
        node("learning", 8, mid, f"## 🎓 ÖĞRENME\nPost-mortem, kalibrasyon, ajan ağırlıkları\n[[{DIR_MODELS}/Registry]] · [[Learning/Öğrenme]]", "6")
        edge("chief", "paper_exec", "onay")
        edge("paper_exec", "trade_result", "kapanış")
        edge("trade_result", "learning", "post-mortem")
        nodes.append({"id": nid("grp_execution"), "type": "group", "label": "İCRA → SONUÇ → ÖĞRENME", "x": 6 * COL_W - 20, "y": -60,
                      "width": 3 * COL_W, "height": n_spec * ROW_H + 60})
        # grafik dosya düğümü
        if chart:
            nodes.append({"id": nid("chart"), "type": "file", "file": chart, "x": 1 * COL_W, "y": -3 * ROW_H - 60, "width": 2 * COL_W - 40, "height": 3 * ROW_H - 20})
            edges.append({"id": f"{base}:chart->head", "fromNode": nid("chart"), "fromSide": "bottom", "toNode": nid("head"), "toSide": "top", "label": "görsel"})
        return {"nodes": nodes, "edges": edges}

    # ================================================================= PORTFÖY
    def write_portfolio(self, spot_summary: dict | None, futures_summary: dict | None, positions: list[dict] | None) -> list[Path]:
        utc, loc = _now_pair()
        positions = positions or []
        spot_pos = [p for p in positions if str(p.get("market_type", "")).lower() in ("spot",)]
        fut_pos = [p for p in positions if p not in spot_pos]
        out: list[Path] = []
        # Spot
        s = spot_summary or {}
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["portfolio", "spot"]})
        lines += ["# 💼 Spot Portföy (kağıt)", f"> {loc} (TR) · {utc}", "",
                  *(_table(["Alan", "Değer"], [[k, _fmt(v, 2)] for k, v in s.items()]) if s else ["- özet yok"]), "", "## Açık pozisyonlar"]
        lines += _table(["Sembol", "Miktar", "Giriş", "Son", "Stop", "PnL", "Açılış"],
                        [[f"[[{DIR_COIN_HEADS}/{safe_base(p.get('symbol', ''))}|{p.get('symbol')}]]", _fmt(p.get("qty") or p.get("units")), _fmt(p.get("entry_avg") or p.get("entry") or p.get("entry_price")),
                          _fmt(p.get("last_price")), _fmt(p.get("stop")), _fmt(p.get("unrealized") or p.get("realized_pnl"), 2), p.get("opened_at") or p.get("entry_time")] for p in spot_pos]) if spot_pos else ["- açık spot pozisyon yok"]
        lines += ["", f"[[{DIR_PORTFOLIO}/Futures]] · [[{DIR_RISK}/Exposure]] · [[Dashboard]]"]
        p1 = self._path(DIR_PORTFOLIO, "Spot.md"); self._write(p1, "\n".join(lines) + "\n"); out.append(p1)
        # Futures
        f = futures_summary or {}
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["portfolio", "futures"]})
        lines += ["# ⚡ Futures Portföy (kağıt, USDⓈ-M)", f"> {loc} (TR) · {utc}", "",
                  *(_table(["Alan", "Değer"], [[k, _fmt(v, 2)] for k, v in f.items()]) if f else ["- özet yok"]), "", "## Açık pozisyonlar"]
        lines += _table(["Sembol", "Yön", "Miktar", "Tip", "Giriş", "Son", "Kaldıraç", "Marj", "Notional", "Stop", "Liq", "Ücret", "Funding", "PnL"],
                        [[f"[[{DIR_COIN_HEADS}/{safe_base(p.get('symbol', ''))}|{p.get('symbol')}]]", p.get("side"), _fmt(p.get("qty") or p.get("units")), p.get("amount_type", "-"),
                          _fmt(p.get("entry_avg") or p.get("entry")), _fmt(p.get("last_price")), f"{p.get('leverage', 1)}x",
                          _fmt(p.get("isolated_margin") or p.get("margin"), 2), _fmt(p.get("notional") or p.get("requested_notional"), 2), _fmt(p.get("stop")),
                          _fmt(p.get("liquidation_price") or p.get("liq_price")), _fmt(p.get("fees_paid") or p.get("fees"), 4),
                          _fmt(p.get("funding_net", (p.get("funding_received") or 0) - (p.get("funding_paid") or 0)) if "funding" not in p else p.get("funding"), 4),
                          _fmt(p.get("unrealized") or p.get("realized_pnl"), 2)] for p in fut_pos]) if fut_pos else ["- açık futures pozisyon yok"]
        lines += ["", f"[[{DIR_PORTFOLIO}/Spot]] · [[{DIR_RISK}/Exposure]] · [[{DIR_RISK}/Kill Switch]] · [[Paper Futures]]"]
        p2 = self._path(DIR_PORTFOLIO, "Futures.md"); self._write(p2, "\n".join(lines) + "\n"); out.append(p2)
        return out

    # ================================================================= İŞLEM
    @staticmethod
    def _is_closed(trade: dict) -> bool:
        st = str(trade.get("status") or "").upper()
        return st == "CLOSED" or (not st and bool(trade.get("closed_at")))

    def write_trade(self, trade: dict, postmortem: dict | None = None) -> Path:
        """`Trades/<trade_id>.md`. Kapanmış işlemin notu bir kez yazıldıktan sonra dondurulur (yeniden yazılmaz)."""
        tid = str(trade.get("id") or trade.get("trade_id") or "unknown")
        path = self._path(DIR_TRADES, f"{_BAD_CHARS.sub('_', tid)}.md")
        if path.exists():
            head = self._read(path)[:600]
            if "status: CLOSED" in head:
                return path
        closed = self._is_closed(trade)
        status = "CLOSED" if closed else "OPEN"
        base = safe_base(trade.get("symbol", ""))
        utc, loc = _now_pair()
        fm = {"trade_id": tid, "symbol": trade.get("symbol", ""), "base": base, "side": trade.get("side", ""), "status": status,
              "market_type": trade.get("market_type", ""), "opened_at": trade.get("opened_at", ""), "closed_at": trade.get("closed_at", ""),
              "pnl": trade.get("pnl", trade.get("net_pnl", 0)), "r_multiple": trade.get("r_multiple", 0), "exit_reason": trade.get("exit_reason", ""),
              "updated_utc": utc, "tags": ["trade", status.lower()]}
        rows = [[k, _fmt(trade.get(k), 4)] for k in ("entry", "exit_price", "quantity", "leverage", "amount_type", "requested_margin", "effective_margin",
                                                     "requested_notional", "effective_notional", "gross_pnl", "fees", "entry_fee", "exit_fee", "funding",
                                                     "funding_paid", "funding_received", "slippage_cost", "spread_cost", "tax_estimate", "net_pnl", "pnl",
                                                     "r_multiple", "mae_pct", "mfe_pct", "bars_held", "liquidation_price", "setup_type", "trigger_text")
                if trade.get(k) not in (None, "", 0, "0")]
        out = _frontmatter(fm)
        out += [f"# 📄 İşlem {tid} — {trade.get('symbol', '')} {trade.get('side', '')} ({status})",
                f"> {loc} (TR) · {utc} · [[{DIR_COIN_HEADS}/{base}]] · [[{DIR_PORTFOLIO}/Futures]] · [[{DIR_RUNS}/{str(trade.get('closed_at') or trade.get('opened_at') or utc)[:10]}]]", "",
                "## Özet", *_table(["Alan", "Değer"], rows), ""]
        costs = trade.get("costs") or {}
        if costs:
            out += ["## Maliyet dökümü", *_table(["Kalem", "USDT"], [[k, _fmt(v, 4)] for k, v in costs.items()]), ""]
        fills = trade.get("fills") or []
        if fills:
            out += ["## Doldurmalar", *_table(["Zaman", "Yön", "Fiyat", "Miktar", "Ücret", "Rol"],
                                            [[f.get("ts") or f.get("time"), f.get("side"), _fmt(f.get("price")), _fmt(f.get("qty")), _fmt(f.get("fee"), 4), f.get("role") or f.get("liquidity", "")] for f in fills]), ""]
        feats = trade.get("features") or {}
        if feats:
            out += ["## Giriş anındaki özellikler", *_table(["Özellik", "Değer"], [[k, _fmt(v, 3) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)[:120]] for k, v in list(feats.items())[:30]]), ""]
        pm = postmortem or trade.get("postmortem") or {}
        out += ["## Post-mortem"]
        if pm:
            for k, v in pm.items():
                if isinstance(v, list):
                    out += [f"- **{k}**:", *[f"  - {x}" for x in v[:10]]]
                else:
                    out.append(f"- **{k}**: {v}")
        else:
            out.append("- (post-mortem yok)" if closed else "- (işlem açık; kapanınca yazılır)")
        out += ["", "> Bu not kapanış sonrası dondurulur; düzeltmeler yeni bir not olarak eklenmelidir."]
        self._write(path, "\n".join(out) + "\n")
        return path

    # ================================================================= KOŞU GÜNLÜĞÜ
    def append_run_event(self, kind: str, text: str, run_id: str | None = None, ts: str | None = None) -> Path:
        """`Runs/YYYY-MM-DD.md` (UTC tarihi) günlük özetine satır ekler; günlük not yoksa başlıkla oluşturur."""
        utc, loc = _now_pair(ts)
        day = utc[:10]
        path = self._path(DIR_RUNS, f"{day}.md")
        cur = self._read(path)
        if not cur:
            cur = "\n".join(_frontmatter({"date": day, "tags": ["run-log"]}) + [f"# 🗓️ Koşu Günlüğü {day}", "",
                                                                                 f"[[{DIR_PORTFOLIO}/Futures]] · [[{DIR_RISK}/Kill Switch]] · [[{DIR_OPS}/Health]] · [[{DIR_OPS}/Incidents]]", "",
                                                                                 "## Olaylar", ""]) + "\n"
        line = f"- `{utc[11:19]}Z` ({loc[-5:]} TR) **{kind}** — {_md_cell(text)}" + (f" · `{run_id}`" if run_id else "")
        atomic_write_text(path, cur.rstrip("\n") + "\n" + line + "\n")
        return path

    # ================================================================= RİSK
    def write_risk(self, risk: dict | None, killswitch: dict | None) -> list[Path]:
        utc, loc = _now_pair()
        risk = risk or {}
        prof = risk.get("profile") or {}
        exp = risk.get("exposure") or {}
        ks = killswitch or risk.get("killswitch") or {}
        out: list[Path] = []
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["risk", "limits"]})
        lines += ["# 🛡️ Risk Limitleri", f"> {loc} (TR) · {utc}", "", *(_table(["Parametre", "Değer"], [[k, _fmt(v, 4)] for k, v in prof.items()]) if prof else ["- profil yok"]),
                  "", f"[[{DIR_RISK}/Exposure]] · [[{DIR_RISK}/Kill Switch]]"]
        p = self._path(DIR_RISK, "Limits.md"); self._write(p, "\n".join(lines) + "\n"); out.append(p)
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["risk", "exposure"]})
        pos = exp.get("positions") or []
        lines += ["# 📐 Maruziyet", f"> {loc} (TR) · {utc}", "",
                  *(_table(["Alan", "Değer"], [[k, _fmt(v, 4)] for k, v in exp.items() if k != "positions"]) if exp else ["- maruziyet yok"]), "", "## Açık pozisyonlar"]
        lines += _table(["Sembol", "Piyasa", "Yön", "Notional", "Risk USDT", "Küme"],
                        [[f"[[{DIR_COIN_HEADS}/{safe_base(o.get('symbol', ''))}|{o.get('symbol')}]]", o.get("market_type"), o.get("side"), _fmt(o.get("notional"), 2), _fmt(o.get("risk_usdt"), 2), o.get("cluster", "-")] for o in pos]) if pos else ["- yok"]
        p = self._path(DIR_RISK, "Exposure.md"); self._write(p, "\n".join(lines) + "\n"); out.append(p)
        st = str(ks.get("state") or "ARMED")
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "state": st, "tags": ["risk", "kill-switch"]})
        lines += [f"# 🔴 Kill Switch — {st}", f"> {loc} (TR) · {utc} · {'✅ ARMED (normal)' if st == 'ARMED' else '⛔ ' + st + ' — manuel reset gerekli'}", "",
                  f"- Beri: {ks.get('since') or '-'}", "", "## Nedenler"]
        reasons = ks.get("reasons") or []
        lines += [f"- **{r.get('code', r) if isinstance(r, dict) else r}** {('— ' + str(r.get('detail') or r.get('reason') or '')) if isinstance(r, dict) else ''} `{r.get('ts', '') if isinstance(r, dict) else ''}`" for r in reasons] or ["- yok"]
        audit = (ks.get("audit") or [])[-30:]
        lines += ["", "## Denetim izi (son 30)", *([f"- `{a.get('ts', '')}` {a.get('action', a.get('event', ''))} {a.get('code', '')} {a.get('source', '')}" for a in audit if isinstance(a, dict)] or ["- yok"])]
        p = self._path(DIR_RISK, "Kill Switch.md"); self._write(p, "\n".join(lines) + "\n"); out.append(p)
        return out

    # ================================================================= MODELLER
    def write_models(self, registry: dict | None) -> Path:
        utc, loc = _now_pair()
        models = (registry or {}).get("models") or []
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["models"]})
        lines += ["# 🧪 Model Kayıt Defteri", f"> {loc} (TR) · {utc}", ""]
        if models:
            lines += _table(["Model", "Tür", "Durum", "Oluşturma", "Metrikler"],
                            [[m.get("id"), m.get("kind"), m.get("status"), m.get("created_at"),
                              ", ".join(f"{k}={_fmt(v, 3)}" for k, v in (m.get("metrics") or {}).items()) or "-"] for m in models])
        else:
            lines.append("- kayıtlı model yok")
        lines += ["", "[[Learning/Öğrenme]] · [[Dashboard]]"]
        p = self._path(DIR_MODELS, "Registry.md"); self._write(p, "\n".join(lines) + "\n")
        return p

    # ================================================================= OPERASYON
    def append_incident(self, incident: dict) -> Path:
        """`Operations/Incidents.md`'ye olay ekler; 200'ü aşan en eski kayıtlar `Incidents Archive YYYY-MM.md`'ye taşınır."""
        utc, loc = _now_pair(incident.get("ts") or incident.get("created_at"))
        sev = str(incident.get("severity") or "warning").upper()
        kind = str(incident.get("kind") or incident.get("code") or "incident")
        text = str(incident.get("text") or incident.get("detail") or incident.get("message") or "")
        line = f"- `{utc}` ({loc} TR) **{sev}** · {kind} — {_md_cell(text)}" + (f" · `{incident.get('run_id')}`" if incident.get("run_id") else "")
        path = self._path(DIR_OPS, "Incidents.md")
        cur = self._read(path)
        header, entries = self._split_incidents(cur)
        entries.append(line)
        if len(entries) > INCIDENT_CAP:
            overflow, entries = entries[:-INCIDENT_CAP], entries[-INCIDENT_CAP:]
            arch = self._path(DIR_OPS, f"Incidents Archive {utc[:7]}.md")
            acur = self._read(arch)
            if not acur:
                acur = "\n".join(_frontmatter({"month": utc[:7], "tags": ["incidents", "archive"]}) + [f"# 🗄️ Olay Arşivi {utc[:7]}", "", "## Kayıtlar", ""]) + "\n"
            atomic_write_text(arch, acur.rstrip("\n") + "\n" + "\n".join(overflow) + "\n")
        body = header + "\n".join(entries) + "\n"
        atomic_write_text(path, body)
        return path

    def _split_incidents(self, text: str) -> tuple[str, list[str]]:
        if not text:
            header = "\n".join(_frontmatter({"tags": ["incidents"], "cap": INCIDENT_CAP}) +
                               ["# 🚨 Olaylar", f"En son {INCIDENT_CAP} kayıt burada; eskiler `Incidents Archive YYYY-MM` notlarına taşınır. [[{DIR_OPS}/Health]] · [[{DIR_RISK}/Kill Switch]]", "", "## Kayıtlar", ""]) + "\n"
            return header, []
        marker = "## Kayıtlar\n"
        idx = text.find(marker)
        if idx < 0:
            return text.rstrip("\n") + "\n\n" + marker, []
        head = text[: idx + len(marker)]
        entries = [ln for ln in text[idx + len(marker):].splitlines() if ln.startswith("- ")]
        return head, entries

    def write_health(self, health: dict | None) -> Path:
        utc, loc = _now_pair((health or {}).get("generated_at"))
        h = health or {}
        st = str(h.get("state") or "UNKNOWN")
        icon = {"HEALTHY": "🟢", "DEGRADED": "🟡", "PAUSED": "⏸️", "KILL_SWITCH": "🔴", "DATA_STALE": "🟠", "RECONCILIATION_REQUIRED": "🟣"}.get(st, "⚪")
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "state": st, "tags": ["operations", "health"]})
        lines += [f"# {icon} Sağlık — {st}", f"> {loc} (TR) · {utc} · {h.get('summary') or ''}", ""]
        checks = h.get("checks") or []
        lines += _table(["Kontrol", "Durum", "Detay"], [[c.get("name"), "✅" if c.get("ok") else "❌", _md_cell(json.dumps(c.get("detail"), ensure_ascii=False) if isinstance(c.get("detail"), (dict, list)) else c.get("detail"))] for c in checks]) if checks else ["- kontrol yok"]
        lines += ["", f"[[{DIR_OPS}/Incidents]] · [[{DIR_RISK}/Kill Switch]] · [[Dashboard]]"]
        p = self._path(DIR_OPS, "Health.md"); self._write(p, "\n".join(lines) + "\n")
        return p

    # ================================================================= VERİ KALİTESİ
    def write_data_quality(self, report: dict | None) -> Path:
        utc, loc = _now_pair((report or {}).get("generated_at"))
        r = report or {}
        feeds = r.get("feeds") or r.get("checks") or []
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["data-quality"]})
        lines += ["# 📡 Veri Kalitesi — Beslemeler", f"> {loc} (TR) · {utc}", ""]
        if isinstance(feeds, dict):
            feeds = [{"name": k, **(v if isinstance(v, dict) else {"detail": v})} for k, v in feeds.items()]
        if feeds:
            lines += _table(["Besleme", "Durum", "Yaş (s)", "Detay"], [[f.get("name") or f.get("feed"), "✅" if f.get("ok", True) else "❌", _fmt(f.get("age_s") or f.get("age_seconds"), 0), _md_cell(f.get("detail") or f.get("error") or "")] for f in feeds])
        else:
            lines.append("- besleme raporu yok")
        extra = {k: v for k, v in r.items() if k not in ("feeds", "checks", "generated_at")}
        if extra:
            lines += ["", *_table(["Alan", "Değer"], [[k, _md_cell(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)] for k, v in extra.items()])]
        lines += ["", f"[[{DIR_DQ}/Universe]] · [[{DIR_OPS}/Health]]"]
        p = self._path(DIR_DQ, "Feeds.md"); self._write(p, "\n".join(lines) + "\n")
        return p

    def write_universe(self, universe: dict | None) -> Path:
        u = universe or {}
        utc, loc = _now_pair(u.get("generated_at"))
        spot, fut = list(u.get("spot") or []), list(u.get("futures") or [])
        counts = u.get("counts") or {"spot": len(spot), "futures": len(fut)}
        lines = _frontmatter({"updated_utc": utc, "updated_local": loc, "tags": ["data-quality", "universe"]})
        lines += ["# 🌐 Evren (işlem görebilir semboller)", f"> {loc} (TR) · {utc}", "",
                  *_table(["Piyasa", "Adet"], [[k, v] for k, v in counts.items()]), "",
                  "## Spot", ", ".join(f"[[{DIR_COIN_HEADS}/{safe_base(s)}|{s}]]" for s in spot[:300]) or "- yok", "",
                  "## Futures", ", ".join(f"[[{DIR_COIN_HEADS}/{safe_base(s)}|{s}]]" for s in fut[:300]) or "- yok", "",
                  f"[[{DIR_DQ}/Feeds]]"]
        p = self._path(DIR_DQ, "Universe.md"); self._write(p, "\n".join(lines) + "\n")
        return p

    # ================================================================= BUDAMA
    def prune_stale(self, active_bases: Iterable[str], older_than_hours: float = 48.0) -> list[Path]:
        """Yalnızca `Coin Heads/` altında: aktif olmayan ve `older_than_hours`'tan eski .md/.canvas dosyalarını siler."""
        active = {safe_base(b) for b in active_bases}
        d = self.root / DIR_COIN_HEADS
        if not d.exists():
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_hours * 3600.0
        removed: list[Path] = []
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix not in (".md", ".canvas"):
                continue
            if p.stem in active:
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed.append(p)
            except OSError:
                continue
        return removed


__all__ = ["ObsidianCoinHeadWriter", "safe_base", "OWNED_DIRS", "INCIDENT_CAP", "COL_W", "ROW_H", "FACTOR_GROUP_ORDER"]

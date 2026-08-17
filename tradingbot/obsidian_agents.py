"""Obsidian — çoklu ajan şemaları.

Her coin için:
  Agents/<COIN>.canvas : sol sütunda uzman ajanlar (renk = ajanın yönü), ortada COIN YÖNETİCİSİ,
                         sağda FUTURES PLANI ve YAP/YAPMA/EĞER-İSE kutuları
  Agents/<COIN>.md     : tüm ajan raporları (bulgular, uyarılar, seviyeler) + yönetici brifingi
Portföy:
  Agents/Baş Yönetici.md : risk modu, sıralama, kurallar
  Agents/Alarmlar.md     : verdict değişiklikleri (7/24 izleme çıktısı)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .agents.base import AgentReport, tr_lower
from .agents.manager import ChiefBrief, CoinBrief

VERDICT_COLOR = {"LONG": "4", "SHORT": "1", "BEKLE": "6"}
VERDICT_EMOJI = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "⚪"}
AGENT_ICON = {"volatility": "🌡️", "trend": "📈", "candles": "🕯️", "volume": "📊", "levels": "🧱", "momentum": "🚀", "edge": "🧪", "market": "📡"}


def _bias_color(r: AgentReport) -> str:
    if r.error or r.confidence == 0:
        return "6"
    if r.bias >= 0.15:
        return "4"
    if r.bias <= -0.15:
        return "1"
    return "3"


def _px(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "-"
    return f"{x:,.0f}" if x >= 1000 else (f"{x:,.2f}" if x >= 1 else f"{x:.4f}")


class ObsidianAgentWriter:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "Agents"

    def write_all(self, briefs: list[CoinBrief], chief: ChiefBrief, alerts: list[str]) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        local = datetime.now().strftime("%Y-%m-%d %H:%M")
        for b in briefs:
            (self.dir / f"{b.base}.canvas").write_text(json.dumps(self._coin_canvas(b, local), ensure_ascii=False, indent=1), encoding="utf-8")
            (self.dir / f"{b.base}.md").write_text(self._coin_note(b, local), encoding="utf-8")
        (self.dir / "Baş Yönetici.md").write_text(self._chief_note(chief, briefs, local), encoding="utf-8")
        if alerts:
            p = self.dir / "Alarmlar.md"
            old = p.read_text(encoding="utf-8") if p.exists() else "# 🔔 Alarmlar (ajan kararı değişiklikleri)\n\n"
            new = "\n".join(f"- **{local}** — {a}" for a in alerts)
            p.write_text(old.rstrip() + "\n" + new + "\n", encoding="utf-8")
        elif not (self.dir / "Alarmlar.md").exists():
            (self.dir / "Alarmlar.md").write_text("# 🔔 Alarmlar (ajan kararı değişiklikleri)\n\nHenüz alarm yok.\n", encoding="utf-8")
        return {"dir": str(self.dir)}

    # ---------------------------------------------------------------- canvas
    def _coin_canvas(self, b: CoinBrief, local: str) -> dict:
        nodes, edges = [], []
        row_h, w, h = 170, 380, 150
        n = len(b.reports)
        mid_y = (n * row_h) // 2
        for i, r in enumerate(b.reports):
            icon = AGENT_ICON.get(r.agent, "🤖")
            text = f"### {icon} {r.title}\n**{r.stance}** · bias {r.bias:+.2f} · güven {r.confidence}\n{r.summary[:220]}"
            if r.warnings:
                text += f"\n⚠️ {r.warnings[0][:120]}"
            nodes.append({"id": f"ag_{r.agent}", "type": "text", "x": 0, "y": i * row_h, "width": w, "height": h,
                          "color": _bias_color(r), "text": text})
            edges.append({"id": f"e_{r.agent}", "fromNode": f"ag_{r.agent}", "fromSide": "right", "toNode": "manager", "toSide": "left",
                          "color": _bias_color(r), "label": f"{r.stance_lower} · {r.confidence}"})
        # yönetici
        vc = VERDICT_COLOR[b.verdict]
        nodes.append({"id": "manager", "type": "text", "x": 620, "y": mid_y - 190, "width": 460, "height": 380, "color": vc,
                      "text": (f"## 🧠 {b.base} YÖNETİCİSİ\n{b.headline}\n\n**Karar: {VERDICT_EMOJI[b.verdict]} {b.verdict}** · kanaat %{b.conviction}\n"
                               f"Skor {b.score:+.2f} (−1 ayı … +1 boğa)\n\n"
                               + "\n".join(f"- {x}" for x in b.do_list[:3])
                               + f"\n\n[[Agents/{b.base}|Tam rapor →]] · [[Coins/{b.base}|Backtest →]]\n_{local}_")})
        # plan
        p = b.plan
        if p.direction != "BEKLE":
            plan_txt = (f"## 🎯 FUTURES PLANI — {p.direction}\n"
                        f"{'✅ GEÇERLİ' if p.valid else '⛔ ' + p.invalid_reason}\n"
                        f"**Tetik:** {p.trigger_text}\n"
                        f"Giriş ~{_px(p.entry)} · Stop {_px(p.stop)} (%{p.stop_pct}) · Hedef1 {_px(p.target1)} · Hedef2 {_px(p.target2)} · R/R {p.rr}\n"
                        f"Kaldıraç ≤ **{p.max_leverage}x** (öneri {p.suggested_leverage}x) · marj ≈ {p.margin_usdt} USDT · notional ≈ {p.notional_usdt} USDT · risk ≈ {p.risk_usdt} USDT")
        else:
            plan_txt = f"## 🎯 FUTURES PLANI\n⚪ **BEKLE** — yön yok, pozisyon açma.\nMax kaldıraç (volatiliteye göre): {p.max_leverage}x\n" + "\n".join(f"- {x}" for x in b.do_list[1:2])
        nodes.append({"id": "plan", "type": "text", "x": 1240, "y": mid_y - 330, "width": 520, "height": 260, "color": vc, "text": plan_txt})
        nodes.append({"id": "dont", "type": "text", "x": 1240, "y": mid_y - 40, "width": 520, "height": 300, "color": "1",
                      "text": "## 🚫 YAPMA\n" + ("\n".join(f"- {x}" for x in b.dont_list[:7]) or "- (uyarı yok)")})
        nodes.append({"id": "ifthen", "type": "text", "x": 1240, "y": mid_y + 290, "width": 520, "height": 220, "color": "5",
                      "text": "## 🔀 EĞER … İSE\n" + ("\n".join(f"- {x}" for x in b.if_then[:5]) or "- (koşul yok)")})
        lv = b.key_levels
        lv_txt = "## 📍 Kilit Seviyeler\n" + "\n".join(
            f"- {k}: {_px(v)}" for k, v in sorted(lv.items(), key=lambda kv: -kv[1])[:10])
        nodes.append({"id": "levels", "type": "text", "x": 620, "y": mid_y + 220, "width": 460, "height": 300, "color": "3", "text": lv_txt})
        if b.chart:
            nodes.append({"id": "chart", "type": "file", "file": b.chart, "x": 1240, "y": mid_y - 700, "width": 520, "height": 340})
            edges.append({"id": "e_chart", "fromNode": "chart", "fromSide": "bottom", "toNode": "plan", "toSide": "top", "label": "görsel"})
        for nid, lbl in (("plan", "plan"), ("dont", "yasaklar"), ("ifthen", "koşullar")):
            edges.append({"id": f"e_m_{nid}", "fromNode": "manager", "fromSide": "right", "toNode": nid, "toSide": "left", "label": lbl})
        edges.append({"id": "e_lv_m", "fromNode": "levels", "fromSide": "top", "toNode": "manager", "toSide": "bottom", "label": "seviyeler"})
        return {"nodes": nodes, "edges": edges}

    # ---------------------------------------------------------------- notlar
    def _coin_note(self, b: CoinBrief, local: str) -> str:
        p = b.plan
        out = ["---", f"symbol: {b.symbol}", f"verdict: {b.verdict}", f"conviction: {b.conviction}", f"price: {b.price}",
               f"updated: {b.generated_at}", "tags: [trading, agents]", "---",
               f"# 🧠 {b.symbol} — Ajan Raporu ({VERDICT_EMOJI[b.verdict]} {b.verdict}, kanaat %{b.conviction})",
               f"> {b.headline}  ·  {local}", "",
               f"Şema: [[Agents/{b.base}.canvas]] · Backtest/karar: [[Coins/{b.base}]] · [[Agents/Baş Yönetici]] · [[Paper Futures]]", "",
               *([f"![[{b.chart}]]", ""] if b.chart else []),
               *([f"🔎 Tarayıcı: {b.scan_direction} skor **{b.scan_score}**"] if b.scan_score else []),
               *([f"🤖 Öğrenen model P(kazanç): **%{b.p_win*100:.0f}**", ""] if b.p_win is not None else []),
               "## 🎯 Futures planı"]
        if p.direction == "BEKLE":
            out.append(f"⚪ **BEKLE** — yön yok. Max kaldıraç (volatiliteye göre) {p.max_leverage}x.")
        else:
            out += [f"- Yön: **{p.direction}** · {'✅ geçerli' if p.valid else '⛔ ' + p.invalid_reason}",
                    f"- Tetik: {p.trigger_text} ({p.entry_type})",
                    f"- Giriş ~{_px(p.entry)} · Stop {_px(p.stop)} (%{p.stop_pct}) · Hedef1 {_px(p.target1)} · Hedef2 {_px(p.target2)} · R/R {p.rr}",
                    f"- Kaldıraç ≤ {p.max_leverage}x (öneri {p.suggested_leverage}x) · marj ≈ {p.margin_usdt} USDT · notional ≈ {p.notional_usdt} USDT · riske atılan ≈ {p.risk_usdt} USDT"]
        out += ["", "## ✅ YAP", *[f"- {x}" for x in b.do_list], "", "## 🚫 YAPMA", *[f"- {x}" for x in b.dont_list],
                "", "## 🔀 EĞER … İSE", *([f"- {x}" for x in b.if_then] or ["- (koşul yok)"]),
                "", "## 📍 Kilit seviyeler", "| Seviye | Fiyat | Uzaklık |", "|---|---|---|"]
        for k, v in sorted(b.key_levels.items(), key=lambda kv: -kv[1]):
            out.append(f"| {k} | {_px(v)} | {((v / b.price - 1) * 100 if b.price else 0):+.2f}% |")
        out += ["", "## 🤖 Uzman ajan raporları"]
        for r in b.reports:
            icon = AGENT_ICON.get(r.agent, "🤖")
            out += [f"### {icon} {r.title} — {r.stance} (bias {r.bias:+.2f}, güven {r.confidence})", f"{r.summary}"]
            if r.findings:
                out += [f"- {f}" for f in r.findings]
            if r.warnings:
                out += [f"- ⚠️ {w}" for w in r.warnings]
            if r.error:
                out.append(f"- ❌ {r.error}")
            if r.metrics:
                out.append("- Metrikler: " + ", ".join(f"{k}={v}" for k, v in list(r.metrics.items())[:12]))
            out.append("")
        out.append("> ⚠️ Bu rapor otomatik teknik analizdir, yatırım tavsiyesi değildir. Gerçek emir gönderilmez; tetik insanda.")
        return "\n".join(out)

    def _chief_note(self, c: ChiefBrief, briefs: list[CoinBrief], local: str) -> str:
        rows = ["| # | Coin | Karar | Kanaat | Plan | R/R | Kaldıraç | Manşet |", "|---|---|---|---|---|---|---|---|"]
        bmap = {b.symbol: b for b in briefs}
        for i, r in enumerate(c.ranking, 1):
            b = bmap[r["symbol"]]
            rows.append(f"| {i} | [[Agents/{b.base}\\|{r['symbol']}]] | {VERDICT_EMOJI[r['verdict']]} {r['verdict']} | {r['conviction']} | "
                        f"{'✅' if r['plan_valid'] else '—'} | {r['rr'] or '-'} | {r['lev']}x | {b.headline} |")
        return "\n".join([
            "---", f"updated: {c.generated_at}", f"risk_mode: {c.risk_mode}", "tags: [trading, agents, chief]", "---",
            f"# 🏛️ Baş Yönetici — {c.risk_mode}", f"> {c.headline} · {local}", "",
            "## Kurallar", *[f"- {x}" for x in c.rules], "",
            "## Coin sıralaması (kanaat ve plan geçerliliğine göre)", *rows, "",
            "Alarmlar: [[Agents/Alarmlar]] · Dashboard: [[Dashboard]]",
        ])


def agent_summary_line(b: CoinBrief) -> str:
    """Ana canvas'taki coin düğümüne eklenen tek satır."""
    p = b.plan
    plan = f" · plan {'✅' if p.valid else '—'}" if b.verdict != "BEKLE" else ""
    return f"🧠 Yönetici: **{VERDICT_EMOJI[b.verdict]} {b.verdict}** (kanaat %{b.conviction}){plan} · [[Agents/{b.base}.canvas|Ajanlar →]]"

"""Obsidian entegrasyonu — Canvas şeması + Mermaid dashboard + coin/sinyal/backtest/portföy notları.

Şema (canvas):
    [Piyasa Rejimi]
    ANALİZ x10  ──►  [KARAR MOTORU]  ──►  [AL / SAT SİNYALİ]  ──►  [Portföy]
    (her coin bir düğüm; renk = karar)                 [Backtest Sıralaması]

Dashboard.md içinde ayrıca Mermaid ile GERÇEK yuvarlaklar (Obsidian yerleşik olarak render eder).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .analyzer import CoinAnalysis
from .decision import Decision, DecisionSummary

# Obsidian canvas renkleri: 1 kırmızı, 2 turuncu, 3 sarı, 4 yeşil, 5 camgöbeği, 6 mor
COLOR = {"BUY": "4", "SELL": "1", "HOLD": "3", "WATCH": "6"}
EMOJI = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "WATCH": "⚪"}
ACTION_TR = {"BUY": "AL", "SELL": "SAT", "HOLD": "TUT", "WATCH": "BEKLE"}
MERMAID_CLASS = {"BUY": "buy", "SELL": "sell", "HOLD": "hold", "WATCH": "watch"}


def _f(x, nd=2) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "-"
    if x != x:  # nan
        return "-"
    return f"{x:,.{nd}f}"


def _px(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "-"
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:,.2f}"
    return f"{x:.4f}"


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-. ]", "_", name)


def _local_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class ObsidianWriter:
    def __init__(self, root: Path, canvas_name: str = "Trading Bot Şeması.canvas"):
        self.root = Path(root)
        self.canvas_name = canvas_name
        self.coins_dir = self.root / "Coins"
        self.signals_dir = self.root / "Signals"
        self.backtests_dir = self.root / "Backtests"

    # ------------------------------------------------------------------ giriş
    def write_all(self, analyses: list[CoinAnalysis], decisions: list[Decision], summary: DecisionSummary,
                  portfolio: dict, executed: list[dict], *, exchange: str, timeframe: str, run_time: str,
                  briefs: list | None = None, chief=None) -> dict:
        for d in (self.root, self.coins_dir, self.signals_dir, self.backtests_dir):
            d.mkdir(parents=True, exist_ok=True)
        dmap = {d.symbol: d for d in decisions}
        meta = {"exchange": exchange, "timeframe": timeframe, "run_time": run_time, "local": _local_now(),
                "briefs": {b.symbol: b for b in (briefs or [])}, "chief": chief}

        canvas_path = self.root / self.canvas_name
        canvas_path.write_text(json.dumps(self._canvas(analyses, dmap, summary, portfolio, meta), ensure_ascii=False, indent=1), encoding="utf-8")
        (self.root / "Dashboard.md").write_text(self._dashboard(analyses, dmap, summary, portfolio, meta), encoding="utf-8")
        for a in analyses:
            (self.coins_dir / f"{a.base}.md").write_text(self._coin_note(a, dmap.get(a.symbol), meta), encoding="utf-8")
        sig_name = datetime.now().strftime("%Y-%m-%d %H%M") + ".md"
        (self.signals_dir / sig_name).write_text(self._signal_note(decisions, executed, summary, meta), encoding="utf-8")
        (self.signals_dir / "Son Sinyal.md").write_text(self._signal_note(decisions, executed, summary, meta), encoding="utf-8")
        (self.backtests_dir / "Sweep.md").write_text(self._sweep_note(analyses, meta), encoding="utf-8")
        (self.root / "Portfolio.md").write_text(self._portfolio_note(portfolio, analyses, meta), encoding="utf-8")
        return {"canvas": str(canvas_path), "dashboard": str(self.root / "Dashboard.md"), "signal": str(self.signals_dir / sig_name)}

    # ------------------------------------------------------------------ canvas
    def _canvas(self, analyses, dmap, summary, portfolio, meta) -> dict:
        nodes, edges = [], []
        n = len(analyses)
        row_h, node_w, node_h = 150, 340, 130
        left_x = 0
        col_h = n * row_h
        mid_y = col_h // 2

        # üst: piyasa rejimi
        nodes.append({
            "id": "regime", "type": "text", "x": 620, "y": -260, "width": 420, "height": 150,
            "color": "5",
            "text": f"## 🌐 Piyasa Rejimi\n**BTC rejimi:** {summary.market_regime}"
                    f"{'  ⚠️ altcoin AL boyutu %50' if summary.btc_filter_active else ''}\n"
                    f"Borsa: {meta['exchange']} · TF: {meta['timeframe']}\nGüncelleme: {meta['local']}",
        })

        # sol: coin analiz düğümleri
        for i, a in enumerate(analyses):
            d = dmap.get(a.symbol)
            act = d.action if d else "WATCH"
            y = i * row_h
            oos = a.test_metrics.get("sharpe", 0.0)
            text = (
                f"### {EMOJI[act]} {a.symbol} — {ACTION_TR[act]} · skor {a.score}\n"
                f"Fiyat **{_px(a.price)}** ({'+' if a.change_24h_pct >= 0 else ''}{_f(a.change_24h_pct,1)}% 24s) · RSI {_f(a.rsi14,0)} · {a.regime}\n"
                f"{a.best_family_title or 'strateji yok'} · WFO Sharpe {_f(oos)} · {'✅ edge' if a.has_edge else '❌ edge yok'}\n"
                f"[[Coins/{a.base}|Detay →]]"
            )
            if a.error:
                text = f"### ⚠️ {a.symbol}\n{a.error}"
            b = meta.get("briefs", {}).get(a.symbol)
            node_h_i = node_h
            if b is not None:
                from .obsidian_agents import agent_summary_line
                text += "\n" + agent_summary_line(b)
                node_h_i = node_h + 30
            nodes.append({"id": f"coin_{a.base}", "type": "text", "x": left_x, "y": y, "width": node_w, "height": node_h_i,
                          "color": COLOR[act], "text": text})
            edges.append({"id": f"e_{a.base}_dec", "fromNode": f"coin_{a.base}", "fromSide": "right",
                          "toNode": "decision", "toSide": "left", "color": COLOR[act],
                          "label": f"{ACTION_TR[act]} · güven {d.confidence if d else 0}"})

        # orta: karar motoru
        nodes.append({
            "id": "decision", "type": "text", "x": 620, "y": mid_y - 170, "width": 420, "height": 340, "color": "2",
            "text": (
                "## ⚖️ KARAR MOTORU\n"
                f"**Equity:** {_f(summary.equity)} USDT · **Nakit:** {_f(summary.cash)}\n"
                f"**Açık pozisyon:** {summary.open_positions}\n\n"
                f"🟢 AL {summary.n_buy} · 🔴 SAT {summary.n_sell} · 🟡 TUT {summary.n_hold} · ⚪ BEKLE {summary.n_watch}\n\n"
                "Kurallar: yalnız OOS-doğrulanmış edge · risk bazlı boyut · ATR iz süren stop · "
                "max açık pozisyon sınırı · BTC rejim filtresi\n"
                + (f"🏛️ Baş Yönetici: **{meta['chief'].risk_mode}** — {meta['chief'].headline} [[Agents/Baş Yönetici|→]]\n" if meta.get("chief") else "")
                + "[[Dashboard|Dashboard →]]"
            ),
        })

        # sağ: sinyal
        buys = [d for d in dmap.values() if d.action == "BUY"]
        sells = [d for d in dmap.values() if d.action == "SELL"]
        holds = [d for d in dmap.values() if d.action == "HOLD"]
        lines = ["## 📣 AL / SAT SİNYALİ"]
        for d in buys:
            lines.append(f"🟢 **AL {d.symbol}** — {_f(d.notional_usdt,0)} USDT @ {_px(d.price)} · stop {_px(d.stop)}")
        for d in sells:
            lines.append(f"🔴 **SAT {d.symbol}** — @ {_px(d.price)} · {d.reasons[0] if d.reasons else ''}")
        for d in holds:
            lines.append(f"🟡 TUT {d.symbol} — stop {_px(d.stop)}")
        if not (buys or sells or holds):
            lines.append("⚪ Bu turda AL/SAT yok — bekleniyor.")
        lines.append("\n_Gerçek emir gönderilmez; tetik sende._ [[Signals/Son Sinyal|Log →]]")
        nodes.append({"id": "signal", "type": "text", "x": 1240, "y": mid_y - 170, "width": 460, "height": 340,
                      "color": "4" if buys else ("1" if sells else "6"), "text": "\n".join(lines)})
        edges.append({"id": "e_dec_sig", "fromNode": "decision", "fromSide": "right", "toNode": "signal", "toSide": "left",
                      "label": "AL/SAT/TUT/BEKLE"})
        edges.append({"id": "e_reg_dec", "fromNode": "regime", "fromSide": "bottom", "toNode": "decision", "toSide": "top",
                      "label": "rejim filtresi"})

        # sağ-alt: portföy
        pos = portfolio.get("positions", {})
        plines = ["## 💼 Kağıt Portföy", f"Equity **{_f(portfolio.get('equity'))}** · Nakit {_f(portfolio.get('cash'))} · Gerçekleşen P&L {_f(portfolio.get('realized_pnl'))}"]
        for s, p in pos.items():
            plines.append(f"• {s}: {_f(p['units'],4)} @ {_px(p['entry_price'])} · stop {_px(p['stop'])} · P&L {_f(portfolio.get('open_pnl',{}).get(s,0))}")
        if not pos:
            plines.append("Açık pozisyon yok.")
        plines.append("[[Portfolio|Detay →]]")
        nodes.append({"id": "portfolio", "type": "text", "x": 1240, "y": mid_y + 220, "width": 460, "height": 220, "color": "5",
                      "text": "\n".join(plines)})
        edges.append({"id": "e_sig_pf", "fromNode": "signal", "fromSide": "bottom", "toNode": "portfolio", "toSide": "top",
                      "label": "kağıt işlem"})

        # orta-alt: backtest sıralaması
        nodes.append({"id": "backtests", "type": "file", "file": "Backtests/Sweep.md", "x": 620, "y": mid_y + 220,
                      "width": 420, "height": 380})
        edges.append({"id": "e_bt_dec", "fromNode": "backtests", "fromSide": "top", "toNode": "decision", "toSide": "bottom",
                      "label": "edge doğrulama"})
        return {"nodes": nodes, "edges": edges}

    # --------------------------------------------------------------- dashboard
    def _dashboard(self, analyses, dmap, summary, portfolio, meta) -> str:
        m = ["```mermaid", "flowchart LR"]
        for a in analyses:
            d = dmap.get(a.symbol)
            act = d.action if d else "WATCH"
            m.append(f'    {a.base}(("{a.base}<br/>{ACTION_TR[act]} · {a.score}")):::{MERMAID_CLASS[act]} --> K')
        m.append(f'    K(("⚖️ KARAR<br/>{summary.market_regime}")):::core --> S(("📣 SİNYAL<br/>AL {summary.n_buy} · SAT {summary.n_sell}")):::core')
        m.append('    S --> P(("💼 PORTFÖY")):::core')
        m += [
            "    classDef buy fill:#1f7a3a,stroke:#3ddc84,color:#fff",
            "    classDef sell fill:#8a1f1f,stroke:#ff5555,color:#fff",
            "    classDef hold fill:#8a6d1f,stroke:#ffd54f,color:#fff",
            "    classDef watch fill:#3a3a3a,stroke:#888,color:#ddd",
            "    classDef core fill:#1e3a5f,stroke:#64b5f6,color:#fff",
            "```",
        ]
        rows = ["| Coin | Spot karar | 🧠 Ajan yöneticisi | Kanaat | Skor | Fiyat | 24s % | RSI | Rejim | Strateji | WFO Sharpe | Edge |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        vemoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "⚪"}
        for a in analyses:
            d = dmap.get(a.symbol)
            act = d.action if d else "WATCH"
            b = meta.get("briefs", {}).get(a.symbol)
            mv = f"[[Agents/{a.base}\\|{vemoji[b.verdict]} {b.verdict}]]" if b else "-"
            rows.append(
                f"| [[Coins/{a.base}\\|{a.symbol}]] | {EMOJI[act]} {ACTION_TR[act]} | {mv} | {b.conviction if b else '-'} | {a.score} | {_px(a.price)} | "
                f"{_f(a.change_24h_pct,1)} | {_f(a.rsi14,0)} | {a.regime} | {a.best_family_title or '-'} | "
                f"{_f(a.test_metrics.get('sharpe'))} | {'✅' if a.has_edge else '❌'} |"
            )
        return "\n".join([
            "---", f"updated: {meta['run_time']}", f"exchange: {meta['exchange']}", f"timeframe: {meta['timeframe']}", "---",
            "# 📊 Trading Bot — Dashboard",
            f"> Son çalışma: **{meta['local']}** · Borsa: {meta['exchange']} · TF: {meta['timeframe']} · "
            f"BTC rejimi: **{summary.market_regime}**",
            "", f"Şema için: [[{self.canvas_name.replace('.canvas','')}]] · Sinyal logu: [[Signals/Son Sinyal]] · "
            "Backtest: [[Backtests/Sweep]] · Portföy: [[Portfolio]]", "",
            "## Akış (analiz → karar → sinyal)", *m, "",
            "## Coin özeti", *rows, "",
            *(["## 🏛️ Baş Yönetici", f"**{meta['chief'].risk_mode}** — {meta['chief'].headline}", *[f"- {x}" for x in meta['chief'].rules], "Detay: [[Agents/Baş Yönetici]] · Alarmlar: [[Agents/Alarmlar]]", ""] if meta.get("chief") else []),
            "## Portföy",
            f"- Equity: **{_f(portfolio.get('equity'))} USDT** (başlangıç {_f(portfolio.get('starting_equity'))})",
            f"- Nakit: {_f(portfolio.get('cash'))} · Açık pozisyon: {len(portfolio.get('positions', {}))} · "
            f"Kapanan işlem: {portfolio.get('closed_trades', 0)} · Gerçekleşen P&L: {_f(portfolio.get('realized_pnl'))}",
            "", "> ⚠️ Bu bot yalnızca analiz/karar/sinyal üretir ve **kağıt** portföy tutar. Gerçek emir göndermez. "
            "Geçmiş performans gelecek getiriyi garanti etmez.",
        ])

    # --------------------------------------------------------------- coin notu
    def _coin_note(self, a: CoinAnalysis, d: Decision | None, meta) -> str:
        act = d.action if d else "WATCH"
        tm, sm, fm = a.train_metrics, a.test_metrics, a.full_metrics
        keys = [("total_return_pct", "Toplam getiri %"), ("cagr_pct", "CAGR %"), ("sharpe", "Sharpe"),
                ("max_drawdown_pct", "Max DD %"), ("win_rate_pct", "Win rate %"), ("profit_factor", "Profit factor"),
                ("trades", "İşlem"), ("exposure_pct", "Exposure %"), ("buy_hold_return_pct", "Buy&Hold %")]
        mt = ["| Metrik | In-sample (tüm veri) | Walk-forward OOS (birleşik) | Son %30 (tek bölme) |", "|---|---|---|---|"]
        for k, t in keys:
            mt.append(f"| {t} | {_f(tm.get(k))} | {_f(sm.get(k))} | {_f(fm.get(k))} |")
        top = ["| # | Strateji | IS Sharpe | IS PF | IS işlem | son%30 Sharpe | son%30 PF | son%30 işlem |", "|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(a.top_configs, 1):
            sp = r["spec"]
            lbl = f"{sp['family']}({', '.join(f'{k}={v}' for k, v in sp['params'].items())})"
            top.append(f"| {i} | `{lbl}` | {_f(r['train'].get('sharpe'))} | {_f(r['train'].get('profit_factor'))} | {r['train'].get('trades')} | "
                       f"{_f(r['test'].get('sharpe'))} | {_f(r['test'].get('profit_factor'))} | {r['test'].get('trades')} |")
        wf = ["| Adım | Eğitim sonu | Test sonu | Seçilen strateji | OOS Sharpe | OOS getiri % | B&H % | İşlem |", "|---|---|---|---|---|---|---|---|"]
        for f in a.wfo_folds:
            wf.append(f"| {f['fold']} | {f['train_end']} | {f['test_end']} | `{f['spec']}` | {_f(f['test_sharpe'])} | {_f(f['test_return_pct'])} | {_f(f['test_buy_hold_pct'],1)} | {f['test_trades']} |")
        if not a.wfo_folds:
            wf.append("| - | - | - | - | - | - | - | - |")
        dec_lines = []
        if d:
            dec_lines = [f"**Karar:** {EMOJI[act]} **{ACTION_TR[act]}** · güven {d.confidence}"]
            if d.action == "BUY":
                dec_lines.append(f"- Boyut: {_f(d.units,6)} adet ≈ {_f(d.notional_usdt,0)} USDT · stop {_px(d.stop)}")
            elif d.action in ("SELL", "HOLD"):
                dec_lines.append(f"- Pozisyon: {_f(d.units,6)} adet ≈ {_f(d.notional_usdt,0)} USDT · stop {_px(d.stop)}")
            dec_lines += [f"- {r}" for r in d.reasons]
        return "\n".join([
            "---", f"symbol: {a.symbol}", f"signal: {act}", f"score: {a.score}", f"price: {a.price}", f"regime: {a.regime}",
            f"has_edge: {str(a.has_edge).lower()}", f"updated: {meta['run_time']}", "tags: [trading, coin]", "---",
            f"# {EMOJI[act]} {a.symbol} — {ACTION_TR[act]} (skor {a.score})",
            f"> Son kapanan bar: {a.last_bar_time} · {meta['exchange']} · {meta['timeframe']} · {a.bars} bar",
            "", "## Anlık görüntü",
            f"| Fiyat | 24s % | RSI14 | EMA20 | EMA50 | EMA200 | ATR14 | ATR % | ADX14 | Rejim |", "|---|---|---|---|---|---|---|---|---|---|",
            f"| {_px(a.price)} | {_f(a.change_24h_pct,1)} | {_f(a.rsi14,0)} | {_px(a.ema20)} | {_px(a.ema50)} | {_px(a.ema200)} | "
            f"{_px(a.atr14)} | {_f(a.atr_pct)} | {_f(a.adx14,0)} | {a.regime} |",
            "", "## Karar", *(dec_lines or ["-"]),
            "", "## Analiz gerekçeleri", *[f"- {r}" for r in a.reasons],
            "", f"## En iyi strateji: {a.best_family_title or '-'}",
            f"`{a.best_strategy or '-'}` · {'✅ OOS edge doğrulandı' if a.has_edge else '❌ ' + a.edge_note}",
            f"- Strateji durumu: **{a.strategy_position}**" + (f" ({a.bars_since_entry} bardır, giriş {_px(a.strategy_entry_price)}, stop {_px(a.strategy_stop)})" if a.strategy_position == "LONG" else ""),
            f"- Son barda giriş sinyali: {'evet' if a.entry_signal_now else 'hayır'} · çıkış sinyali: {'evet' if a.exit_signal_now else 'hayır'}",
            "", "### Metrikler", *mt,
            "", "### Walk-forward adımları", *wf,
            "", "### Tarama — ilk 5 konfigürasyon", *top,
            "", "[[Dashboard]] · [[Backtests/Sweep]]",
        ])

    # -------------------------------------------------------------- sinyal notu
    def _signal_note(self, decisions, executed, summary, meta) -> str:
        rows = ["| Coin | Karar | Güven | Fiyat | Adet | USDT | Stop | Gerekçe |", "|---|---|---|---|---|---|---|---|"]
        for d in sorted(decisions, key=lambda x: ("BUY", "SELL", "HOLD", "WATCH").index(x.action)):
            rows.append(f"| {d.symbol} | {EMOJI[d.action]} {ACTION_TR[d.action]} | {d.confidence} | {_px(d.price)} | "
                        f"{_f(d.units,4) if d.units else '-'} | {_f(d.notional_usdt,0) if d.notional_usdt else '-'} | "
                        f"{_px(d.stop) if d.stop else '-'} | {d.reasons[0] if d.reasons else ''} |")
        ex = ["| Coin | Yön | Fiyat | Adet | USDT | Stop | P&L |", "|---|---|---|---|---|---|---|"]
        for e in executed:
            ex.append(f"| {e['symbol']} | {e['side']} | {_px(e['price'])} | {_f(e.get('units'),4) if e.get('units') else '-'} | "
                      f"{_f(e.get('notional'),0) if e.get('notional') else '-'} | {_px(e.get('stop')) if e.get('stop') else '-'} | {_f(e.get('pnl')) if 'pnl' in e else '-'} |")
        return "\n".join([
            "---", f"run_time: {meta['run_time']}", f"regime: {summary.market_regime}", "tags: [trading, signal]", "---",
            f"# 📣 Sinyal — {meta['local']}",
            f"> BTC rejimi **{summary.market_regime}** · Equity {_f(summary.equity)} · AL {summary.n_buy} · SAT {summary.n_sell} · TUT {summary.n_hold} · BEKLE {summary.n_watch}",
            "", "## Kararlar", *rows,
            "", "## Kağıt portföyde uygulananlar", *(ex if executed else ["Bu turda işlem yok."]),
            "", "_Gerçek emir gönderilmedi. Tetik insanda._ · [[Dashboard]]",
        ])

    # ------------------------------------------------------------- sweep notu
    def _sweep_note(self, analyses, meta) -> str:
        rows = ["| # | Coin | Strateji | IS Sharpe | WFO Sharpe | WFO Max DD | WFO Win % | WFO PF | WFO işlem | WFO getiri % | Buy&Hold % | Edge |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        ranked = sorted([a for a in analyses if a.best_strategy], key=lambda a: a.test_metrics.get("sharpe", -9), reverse=True)
        for i, a in enumerate(ranked, 1):
            t = a.test_metrics
            rows.append(f"| {i} | [[Coins/{a.base}\\|{a.symbol}]] | `{a.best_strategy}` | {_f(a.train_metrics.get('sharpe'))} | {_f(t.get('sharpe'))} | "
                        f"{_f(t.get('max_drawdown_pct'),1)} | {_f(t.get('win_rate_pct'),0)} | {_f(t.get('profit_factor'))} | {t.get('trades')} | "
                        f"{_f(t.get('total_return_pct'),1)} | {_f(t.get('buy_hold_return_pct'),1)} | {'✅' if a.has_edge else '❌'} |")
        n_edge = sum(1 for a in analyses if a.has_edge)
        beat = sum(1 for a in analyses if a.best_strategy and a.test_metrics.get("total_return_pct", -1e9) > a.test_metrics.get("buy_hold_return_pct", 0))
        return "\n".join([
            "---", f"updated: {meta['run_time']}", "tags: [trading, backtest]", "---",
            "# 🧪 Backtest Sıralaması (parametre taraması)",
            f"> {len(analyses)} coin · {meta['timeframe']} · **walk-forward** (anchored, ileriye dönük) OOS · Binance komisyon+kayma+min emir dahil · ATR stop",
            "", f"**{n_edge}/{len(analyses)}** coinde OOS edge doğrulandı · **{beat}/{len(analyses)}** coinde strateji OOS'ta buy&hold'u geçti",
            "", *rows, "",
            "Seçim kuralı: her walk-forward adımında yalnızca o ana kadarki veriyle en iyi konfigürasyon seçilir ve hiç görülmemiş "
            "sonraki dönemde çalıştırılır; bu OOS dönemleri birleştirilir (WFO). WFO Sharpe/PF/işlem eşiği geçilmezse coin **BEKLE**'de kalır. "
            "Yüksek win-rate ≠ yüksek getiri; sıralama Sharpe'a göredir.",
            "", "[[Dashboard]]",
        ])

    # ---------------------------------------------------------- portföy notu
    def _portfolio_note(self, portfolio, analyses, meta) -> str:
        pos = portfolio.get("positions", {})
        prices = {a.symbol: a.price for a in analyses}
        rows = ["| Coin | Adet | Giriş | Şimdi | Stop | P&L USDT | P&L % | Strateji | Giriş zamanı |", "|---|---|---|---|---|---|---|---|---|"]
        for s, p in pos.items():
            now = prices.get(s, p["entry_price"])
            pnl = (now - p["entry_price"]) * p["units"]
            rows.append(f"| {s} | {_f(p['units'],4)} | {_px(p['entry_price'])} | {_px(now)} | {_px(p['stop'])} | {_f(pnl)} | "
                        f"{_f((now/p['entry_price']-1)*100 if p['entry_price'] else 0)} | `{p.get('strategy','')}` | {p.get('entry_time','')} |")
        hist = portfolio.get("history", [])
        h = ["| Coin | Giriş | Çıkış | Giriş fiyat | Çıkış fiyat | P&L | P&L % | Neden |", "|---|---|---|---|---|---|---|---|"]
        for t in list(hist)[-20:][::-1]:
            h.append(f"| {t['symbol']} | {t['entry_time'][:16]} | {t['exit_time'][:16]} | {_px(t['entry_price'])} | {_px(t['exit_price'])} | {_f(t['pnl'])} | {_f(t['pnl_pct'])} | {t.get('reason','')} |")
        return "\n".join([
            "---", f"updated: {meta['run_time']}", "tags: [trading, portfolio]", "---",
            "# 💼 Kağıt Portföy",
            f"- **Equity:** {_f(portfolio.get('equity'))} USDT · başlangıç {_f(portfolio.get('starting_equity'))} · "
            f"getiri {_f((portfolio.get('equity',0)/portfolio.get('starting_equity',1)-1)*100)}%",
            f"- Nakit: {_f(portfolio.get('cash'))} · Gerçekleşen P&L: {_f(portfolio.get('realized_pnl'))} · Kapanan işlem: {portfolio.get('closed_trades',0)}",
            "", "## Açık pozisyonlar", *(rows if pos else ["Yok."]),
            "", "## Son kapanan işlemler", *(h if hist else ["Henüz yok."]),
            "", "[[Dashboard]]",
        ])

"""7/24 MOTOR — bir "tur"da: TARA → ANALİZ ET (ajanlar) → SETUP/PLAN → RİSK → KAĞIT FUTURES → İZLE → ÖĞREN → Obsidian.

Slayttaki döngü: SCAN → ANALYZE → SETUP → RISK → PAPER TRADE → MONITOR → LEARN
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import pandas as pd

from .agents import AgentRunner, persist_agents
from .agents.manager import CoinBrief
from .analyzer import CoinAnalysis
from .charts import render_signal_chart
from .config import BotConfig
from .data import prepare
from .learning import Learner, features_from_brief, learning_notes
from .obsidian import ObsidianWriter
from .obsidian_agents import ObsidianAgentWriter
from .paper_futures import FuturesLedger
from .scanner import MarketScanner, ScanResult, persist_scan, scanner_note

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.runner = AgentRunner(cfg)
        self.scanner = MarketScanner(cfg.scanner.min_volume_usdt, cfg.scanner.flag_score, cfg.scanner.top_n, max_symbols=cfg.scanner.max_symbols) if cfg.scanner.enabled else None
        self.ledger_path = cfg.state_path / "futures_ledger.json"
        self.ledger = self._load_legacy_ledger()      # v3 motoru bunu geçersiz kılar: dosyanın tek sahibi FuturesLedgerV2
        self.learner = Learner(cfg.state_path / "learning.json", cfg.learning.min_trades)
        self.trig_path = cfg.state_path / "triggers.json"
        self.triggers: dict[str, str] = json.loads(self.trig_path.read_text(encoding="utf-8")) if self.trig_path.exists() else {}
        self.vault = cfg.obsidian.root
        self._fu = None
        self.last_scan: ScanResult | None = None
        self.last_scan_at = 0.0

    # ------------------------------------------------------------------ yardımcılar
    def _load_legacy_ledger(self):
        """v1 (legacy) defter yükleyici. Dosya v2 şemasındaysa `LedgerSchemaError` yükselir (fail-closed; dosya korunur):
        legacy motor v2 execution state'ini açamaz/üzerine yazamaz."""
        return FuturesLedger.load(self.ledger_path, self.cfg.futures.starting_equity_usdt, self.cfg.futures.max_positions)

    def _fut(self):
        if self._fu is None:
            import ccxt
            self._fu = ccxt.binanceusdm({"enableRateLimit": True})
        return self._fu

    def perp_frames(self, symbol: str) -> dict:
        """Yalnızca perpetual olan semboller için Binance futures mumları (1d/4h/1h)."""
        ex = self._fut()
        perp = f"{symbol}:USDT"
        out = {}
        for tf, lim in (("1d", 400), ("4h", 700), ("1h", 500)):
            raw = ex.fetch_ohlcv(perp, tf, limit=lim)
            out[tf] = prepare(pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"]))
        return out

    def _load_last_analyses(self) -> dict[str, CoinAnalysis]:
        src = self.cfg.state_path / "signals.json"
        if not src.exists():
            return {}
        rep = json.loads(src.read_text(encoding="utf-8"))
        return {a["symbol"]: CoinAnalysis(**{k: v for k, v in a.items() if k in CoinAnalysis.__dataclass_fields__}) for a in rep["analyses"]}

    # ------------------------------------------------------------------ tur
    def tour(self, *, do_scan: bool = True, symbols_override: list[str] | None = None, charts: bool = True, obsidian: bool = True) -> dict:
        t0 = time.time()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # 1) TARA
        scan = None
        if self.scanner and do_scan and symbols_override is None:
            try:
                scan = self.scanner.scan()
                persist_scan(scan, self.cfg.state_path)
                self.last_scan, self.last_scan_at = scan, time.time()
            except Exception as exc:  # noqa: BLE001
                log.exception("Tarama hatası: %s", exc)
                scan = self.last_scan
        scan_map = {r.symbol: r for r in (scan.setups if scan else [])}
        core = list(self.cfg.scanner.core_coins) if self.scanner else list(self.cfg.coins)
        symbols = symbols_override or list(dict.fromkeys(core + [r.symbol for r in (scan.setups if scan else [])] + list(self.ledger.positions)))
        # 2) ANALİZ (ajanlar) — öğrenilen ağırlıklarla
        self.runner.set_weights(self.learner.learned_agent_weights())
        analyses = self._load_last_analyses()
        core_set = set(self.cfg.coins) | set(core)
        briefs: list[CoinBrief] = []
        for s in symbols:
            pre = None
            if s not in core_set:
                try:
                    pre = self.perp_frames(s)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s perp verisi alınamadı: %s", s, exc)
            try:
                b = self.runner.run_symbol(s, analyses.get(s), pre)
            except Exception as exc:  # noqa: BLE001
                log.exception("%s ajan hatası: %s", s, exc)
                continue
            if s in scan_map:
                b.scan_score, b.scan_direction = scan_map[s].score, scan_map[s].direction
            briefs.append(b)
        chief = self.runner.chief.decide(briefs)
        chief.generated_at = now
        # 3-4) SETUP + RİSK → P(kazanç) ve kağıt futures tetikleri
        prices = {b.symbol: b.price for b in briefs if b.price}
        funding = {}
        for b in briefs:
            mk = next((r for r in b.reports if r.agent == "market"), None)
            if mk and "funding_pct" in mk.metrics:
                funding[b.symbol] = float(mk.metrics["funding_pct"]) / 100.0
            f = features_from_brief(b, chief, b.scan_score or None)
            b.p_win = round(self.learner.predict(f), 3)
        opened = []
        if self.cfg.futures.enabled:
            opened = self._process_triggers(briefs, chief)
        # 5-6) İZLE: stop/TP kontrolü → kapananlar → 7) ÖĞREN
        events = self.ledger.tick(prices, funding)
        lessons = []
        for rec in events:
            lessons.append(self.learner.learn(rec))
        self.ledger.save(self.ledger_path)
        # 8) görseller
        chart_paths = {}
        if charts:
            for b in briefs:
                if b.verdict != "BEKLE" or b.symbol in self.ledger.positions or b.symbol in core_set or b.scan_score:
                    chart_paths[b.symbol] = self._chart(b)
        # 9) kalıcılık + Obsidian
        _, alerts = persist_agents(briefs, chief, self.cfg.state_path)
        for o in opened:
            alerts.append(f"📈 KAĞIT POZİSYON AÇILDI: {o}")
        for l in lessons:
            alerts.append(f"{'✅' if l['won'] else '❌'} KAPANDI {l['symbol']} {l['side']} {l['r']:+.2f}R ({l['exit']}) — {l['why'][0][:120]}")
        if obsidian:
            self._write_obsidian(briefs, chief, alerts, scan, chart_paths, analyses)
            if self.cfg.obsidian.git_sync:
                self._git_sync()
        summary = {"at": now, "symbols": symbols, "scan": {"universe": scan.universe, "scanned": scan.scanned, "flagged": scan.flagged, "setups": len(scan.setups)} if scan else None,
                   "chief": chief.headline, "opened": opened, "closed": [l["symbol"] for l in lessons], "ledger": self.ledger.summary(prices),
                   "learning": self.learner.snapshot(), "seconds": round(time.time() - t0, 1)}
        return summary

    # ------------------------------------------------------------------ tetik / açılış
    def _process_triggers(self, briefs: list[CoinBrief], chief) -> list[str]:
        opened = []
        for b in briefs:
            p = b.plan
            if b.verdict == "BEKLE" or not p.valid or b.symbol in self.ledger.positions or not b.price:
                continue
            if self.learner.is_blacklisted(p.entry_type, b.verdict):
                continue
            if self.learner.ready and (b.p_win or 0) < self.cfg.futures.min_pwin:
                continue
            against = (chief.risk_mode == "RISK-ON" and b.verdict == "SHORT") or (chief.risk_mode == "RISK-OFF" and b.verdict == "LONG")
            if against and b.conviction < 60:
                continue
            fired = False
            if p.entry_type == "kırılım":
                if b.last_bar_4h and self.triggers.get(b.symbol) == b.last_bar_4h:
                    continue  # bu bar için zaten değerlendirildi
                lvl = p.entry / 1.001 if b.verdict == "LONG" else p.entry / 0.999
                fired = (b.last_close_4h > lvl) if b.verdict == "LONG" else (b.last_close_4h < lvl)
                # kovalama sınırı: fiyat girişten %1.5'ten fazla uzaklaşmışsa açma
                if fired and abs(b.price / p.entry - 1) > 0.015:
                    fired = False
                if b.last_bar_4h:
                    self.triggers[b.symbol] = b.last_bar_4h
            else:  # geri çekilme: fiyat seviyeye değdi
                fired = abs(b.price / p.entry - 1) <= 0.0025
            if not fired:
                continue
            feats = features_from_brief(b, chief, b.scan_score or None)
            feats["initial_stop"] = p.stop
            feats["p_win"] = b.p_win
            lev = max(1, min(p.suggested_leverage, p.max_leverage))
            notional = p.notional_usdt or (self.ledger.equity * 0.3 * lev)
            pos = self.ledger.open(b.symbol, b.verdict, b.price, notional, lev, p.stop, p.target1, p.target2,
                                   setup_type=p.entry_type, trigger_text=p.trigger_text, features=feats)
            if pos:
                pos.features["initial_units"] = pos.units
                opened.append(f"{b.symbol} {b.verdict} @ {pos.entry:.6g} · {pos.notional:.2f} USDT · {lev}x · stop {p.stop:.6g} · TP1 {p.target1:.6g} · TP2 {p.target2:.6g} · P(win) %{(b.p_win or 0.5)*100:.0f}")
        self.trig_path.parent.mkdir(parents=True, exist_ok=True)
        self.trig_path.write_text(json.dumps(self.triggers), encoding="utf-8")
        return opened

    # ------------------------------------------------------------------ görsel
    def _chart(self, b: CoinBrief) -> str:
        frames = self.runner.last_frames.get(b.symbol) or {}
        h4 = frames.get("4h")
        if h4 is None or len(h4) < 30:
            return ""
        pos = self.ledger.positions.get(b.symbol)
        posd = {"side": pos.side, "entry": pos.entry, "stop": pos.stop, "target1": pos.target1, "target2": pos.target2} if pos else None
        out = self.vault / "Charts" / f"{b.base}.png"
        try:
            render_signal_chart(h4, out, title=f"{b.symbol} · 4h · {b.headline}", plan=b.plan, levels=b.key_levels, position=posd,
                                footer=f"P(kazanç) %{(b.p_win or 0.5)*100:.0f} · tarayıcı skoru {b.scan_score or '-'} · {b.generated_at}")
            b.chart = f"Charts/{b.base}.png"
            if pos and not (self.vault / "Charts" / "history" / f"{b.base}_{pos.id}.png").exists():
                render_signal_chart(h4, self.vault / "Charts" / "history" / f"{b.base}_{pos.id}.png", title=f"{b.symbol} · {pos.id} açılış", plan=b.plan, levels=b.key_levels, position=posd)
            return b.chart
        except Exception as exc:  # noqa: BLE001
            log.warning("%s grafik hatası: %s", b.symbol, exc)
            return ""

    def _git_sync(self) -> None:
        """Kasa klasörü bir git deposuysa: add + commit + push (bulut → PC/telefon senkronu için)."""
        import subprocess
        v = str(self.vault)
        try:
            if subprocess.run(["git", "-C", v, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True).returncode != 0:
                log.warning("git_sync açık ama kasa git deposu değil: %s", v)
                return
            subprocess.run(["git", "-C", v, "add", "-A"], check=True, capture_output=True)
            r = subprocess.run(["git", "-C", v, "commit", "-m", f"bot: {datetime.now().strftime('%Y-%m-%d %H:%M')}"], capture_output=True, text=True)
            if r.returncode == 0:
                subprocess.run(["git", "-C", v, "pull", "--rebase", "-q"], capture_output=True)
                subprocess.run(["git", "-C", v, "push", "-q"], check=True, capture_output=True, timeout=120)
                log.info("Kasa git'e gönderildi")
        except Exception as exc:  # noqa: BLE001
            log.warning("Kasa git senkronu başarısız: %s", exc)

    # ------------------------------------------------------------------ Obsidian
    def _write_obsidian(self, briefs, chief, alerts, scan, chart_paths, analyses):
        v = self.vault
        v.mkdir(parents=True, exist_ok=True)
        ObsidianAgentWriter(v).write_all(briefs, chief, alerts)
        if scan:
            (v / "Scanner.md").write_text(scanner_note(scan), encoding="utf-8")
        (v / "Paper Futures.md").write_text(self._futures_note(briefs), encoding="utf-8")
        for name, text in learning_notes(self.learner, self.ledger.summary({b.symbol: b.price for b in briefs})).items():
            (v / name).parent.mkdir(parents=True, exist_ok=True)
            (v / name).write_text(text, encoding="utf-8")
        # ana şema (son spot raporu varsa)
        src = self.cfg.state_path / "signals.json"
        if src.exists():
            try:
                from .decision import Decision, DecisionSummary
                rep = json.loads(src.read_text(encoding="utf-8"))
                decisions = [Decision(**{k: v_ for k, v_ in d.items() if k in Decision.__dataclass_fields__}) for d in rep["decisions"]]
                summary = DecisionSummary(**rep["summary"])
                ObsidianWriter(v, self.cfg.obsidian.canvas_name).write_all(
                    list(analyses.values()) or [], decisions, summary, rep["portfolio"], rep.get("executed", []), exchange=rep["exchange"],
                    timeframe=rep["timeframe"], run_time=rep["run_time"], briefs=briefs, chief=chief)
            except Exception as exc:  # noqa: BLE001
                log.warning("Ana şema güncellenemedi: %s", exc)

    def _futures_note(self, briefs) -> str:
        prices = {b.symbol: b.price for b in briefs}
        s = self.ledger.summary(prices)
        local = datetime.now().strftime("%Y-%m-%d %H:%M")
        out = ["---", "tags: [trading, paper, futures]", "---", "# 📈 Kağıt Futures Defteri (gerçek piyasa koşulları, gerçek para YOK)",
               f"> {local} · Equity **{s['equity_mtm']:.2f} USDT** (başlangıç {s['starting_equity']}) · getiri {s['return_pct']:+.2f}% · açık {s['open']} · kapanan {s['closed']} · kazanma %{s['win_rate']} · ort. {s['avg_r']:+.2f}R · komisyon {s['total_fees']:.2f}",
               "", "## Açık pozisyonlar", "| ID | Sembol | Yön | Giriş | Şimdi | P&L | Stop | TP1 | TP2 | Kaldıraç | Marj | MAE/MFE % | Tetik | Görsel |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for sym, p in self.ledger.positions.items():
            px = prices.get(sym, p.last_price)
            out.append(f"| {p.id} | {sym} | {'🟢' if p.side=='LONG' else '🔴'} {p.side} | {p.entry:.6g} | {px:.6g} | {p.unrealized(px):+.3f} | {p.stop:.6g} | {p.target1:.6g}{' ✅' if p.tp1_done else ''} | {p.target2:.6g} | {p.leverage}x | {p.margin:.2f} | {p.mae_pct:.1f}/{p.mfe_pct:.1f} | {p.trigger_text} | ![[Charts/{sym.split('/')[0]}.png\\|200]] |")
        if not self.ledger.positions:
            out.append("| - | Açık pozisyon yok | | | | | | | | | | | | |")
        out += ["", "## Kapanan işlemler (son 30)", "| ID | Sembol | Yön | Giriş | Çıkış nedeni | P&L | R | Bar | Kaldıraç | Setup | Kapanış |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for h in self.ledger.history[-30:][::-1]:
            out.append(f"| {h['id']} | {h['symbol']} | {h['side']} | {h['entry']:.6g} | {h['exit_reason']} | {h['pnl']:+.3f} | {h['r_multiple']:+.2f} | {h['bars_held']} | {h['leverage']}x | {h['setup_type']} | {h['closed_at'][:16]} |")
        out += ["", "Kurallar: TP1'de yarı kapat + stop başa-baş · likidasyon izole marj yaklaşık · funding 8s'te bir · taker %0.05 + kayma %0.03",
                "", "[[Learning/Öğrenme]] · [[Learning/Dersler]] · [[Scanner]] · [[Dashboard]]"]
        return "\n".join(out)

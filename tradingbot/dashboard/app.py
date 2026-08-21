"""FastAPI uygulaması — salt-okunur panel. Sayfalar, JSON API, sağlık uçları, Prometheus metrikleri, SSE.

`create_app(state_dir, data_dir, vault_dir, cfg)` → FastAPI; `run_dashboard(...)` uvicorn ile çalıştırır.
Hiçbir POST/PUT/DELETE ucu yoktur; bot durumunu değiştiremez.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from ..core import ConfigError, utc_now
from .candles import CandleSource, build_candle_payload
from .config import DashboardConfig
from .state import STATE_FILES, StateReader
from .templates import (age_text, badge, card, chart_block, esc, fmt, health_badge, ks_badge, kv_table, page, pct, pnl_cell,
                        render_any, table, verdict_badge)

log = logging.getLogger(__name__)
_PLOTLY_CACHE: dict[str, bytes] = {}
_PUBLIC_PATHS = ("/health/live",)


def _plotly_js() -> bytes | None:
    if "js" in _PLOTLY_CACHE:
        return _PLOTLY_CACHE["js"]
    try:
        from plotly.offline import get_plotlyjs
        js = get_plotlyjs().encode("utf-8")
    except Exception:  # noqa: BLE001 - plotly isteğe bağlı
        js = None
    if js:
        _PLOTLY_CACHE["js"] = js
    return js


def create_app(state_dir: Path | str, data_dir: Path | str, vault_dir: Path | str | None = None,
               cfg: DashboardConfig | None = None) -> FastAPI:
    cfg = cfg or DashboardConfig()
    cfg.validate()
    if not cfg.read_only:
        raise ConfigError("panel yalnızca salt-okunur çalışır (read_only=True)")
    state = StateReader(state_dir)
    candles = CandleSource(data_dir)
    vault = Path(vault_dir) if vault_dir else None
    app = FastAPI(title=cfg.title, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.reader, app.state.candles, app.state.vault = cfg, state, candles, vault
    token_qs = ""  # sorgu parametresiyle token taşımayız; tarayıcıda header/cookie kullanılır

    # ------------------------------------------------------------------ auth
    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"error": "read-only"}, status_code=405)
        if cfg.auth_token and request.url.path not in _PUBLIC_PATHS:
            hdr = request.headers.get("authorization", "")
            tok = hdr[7:] if hdr.lower().startswith("bearer ") else request.cookies.get("tb_token", "")
            if not tok or not secrets.compare_digest(tok, cfg.auth_token):
                return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        resp = await call_next(request)
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        return resp

    def _page(title: str, body: str, active: str, extra_head: str = "") -> HTMLResponse:
        return HTMLResponse(page(title, body, active, brand=cfg.title, extra_head=extra_head, token_qs=token_qs))

    # ------------------------------------------------------------------ static
    @app.get("/static/plotly.min.js")
    def static_plotly():
        js = _plotly_js()
        if js is None:
            raise HTTPException(404, "plotly kurulu değil")
        return Response(js, media_type="application/javascript", headers={"Cache-Control": "public, max-age=86400"})

    # ------------------------------------------------------------------ sayfalar
    @app.get("/", response_class=HTMLResponse)
    def overview():
        ov = state.overview()
        cards = [
            card("Futures özkaynak", fmt(ov["equity_futures"], 2) + " USDT"),
            card("Spot özkaynak", fmt(ov["equity_spot"], 2) + " USDT"),
            card("Açık pozisyon", str(len(ov["open_positions"]))),
            card("Kill switch", ks_badge(ov["killswitch"])),
            card("Mod", badge(ov["mode"], "info" if ov["mode"] in ("PAPER", "OBSERVE", "TESTNET") else "bad")),
            card("Sağlık", health_badge(ov["health"]), esc(ov["health_summary"])),
            card("Son tur", age_text(ov["last_run_age_s"]) + " önce", "kalp atışı " + age_text(ov["heartbeat_age_s"])),
            card("LLM bugün", fmt(ov["llm_spent_usd_today"], 3) + " $"),
        ]
        chief = ov.get("chief") or {}
        body = f'<div class="grid">{"".join(cards)}</div>'
        if chief:
            body += "<h2>Baş yönetici</h2>" + kv_table({k: v for k, v in chief.items() if k in ("market_risk_mode", "risk_mode", "headline", "breadth", "exposure", "generated_at")})
            if chief.get("rules"):
                body += "<ul>" + "".join(f"<li>{esc(r)}</li>" for r in chief["rules"][:8]) + "</ul>"
        body += "<h2>Açık pozisyonlar</h2>" + _positions_table(ov["open_positions"])
        body += "<h2>Coin head'ler</h2>" + _heads_table(ov["top_heads"])
        if not ov["top_heads"]:
            ag = state.get("agents") or {}
            briefs = ag.get("briefs") or []
            if briefs:
                body += "<h3>Eski ajan brifingleri</h3>" + table(["Coin", "Karar", "Kanaat", "Fiyat", "Manşet"],
                                                                 [[f'<a href="/coin/{esc(b.get("symbol", "").split("/")[0])}">{esc(b.get("symbol"))}</a>', verdict_badge(b.get("verdict")), fmt(b.get("conviction"), 0), fmt(b.get("price")), esc(b.get("headline"))] for b in briefs])
        return _page("Genel Bakış", body, "/")

    def _positions_table(pos: list[dict]) -> str:
        rows = []
        for p in pos:
            sym = str(p.get("symbol", ""))
            rows.append([f'<a href="/coin/{esc(sym.split("/")[0])}">{esc(sym)}</a>', verdict_badge(p.get("side")), fmt(p.get("qty")), esc(p.get("amount_type")),
                         fmt(p.get("entry_avg")), fmt(p.get("last_price")), f"{p.get('leverage', 1)}x", fmt(p.get("isolated_margin"), 2), fmt(p.get("notional"), 2),
                         fmt(p.get("stop")), fmt(p.get("liquidation_price")), fmt(p.get("fees_paid"), 4), fmt(p.get("funding_net"), 4), pnl_cell(p.get("unrealized", p.get("realized_pnl", p.get("realized")))), esc(p.get("opened_at"))])
        return table(["Sembol", "Yön", "Miktar", "Tip", "Giriş", "Son", "Kald.", "Marj", "Notional", "Stop", "Liq", "Ücret", "Funding", "PnL", "Açılış"],
                     rows, num_cols={2, 4, 5, 7, 8, 9, 10, 11, 12, 13}, empty="açık pozisyon yok")

    def _heads_table(heads: list[dict]) -> str:
        rows = []
        for h in heads:
            sym = str(h.get("symbol", ""))
            fp, sp = h.get("futures_plan") or {}, h.get("spot_plan") or {}
            rows.append([f'<a href="/coin/{esc(sym.split("/")[0])}">{esc(sym)}</a>', verdict_badge(h.get("verdict")), esc(h.get("direction") or "-"),
                         fmt(float(h.get("confidence_calibrated") or 0) * 100, 0) + "%", fmt(float(h.get("p_win") or 0) * 100, 0) + "%", pct(h.get("expected_return_net")),
                         fmt(h.get("expected_r"), 2), esc(h.get("regime")), "✅" if sp.get("valid") else "—", "✅" if fp.get("valid") else "—",
                         esc(h.get("no_trade_reason") or ""), esc(", ".join(h.get("vetoes") or [])[:80])])
        return table(["Sembol", "Karar", "Yön", "Güven", "P(kazanç)", "Net E[r]", "E[R]", "Rejim", "Spot", "Fut", "Gerekçe", "Veto"], rows,
                     num_cols={3, 4, 5, 6}, empty="coin head kararı yok (coin_heads.json)")

    @app.get("/scanner", response_class=HTMLResponse)
    def scanner():
        sc = state.get("scan") or {}
        rows = sc.get("setups") or sc.get("rows") or []
        body = f'<div class="grid">{card("Evren", fmt(sc.get("universe"), 0))}{card("Taranan", fmt(sc.get("scanned"), 0))}{card("Bayraklı", fmt(sc.get("flagged"), 0))}{card("Süre", fmt(sc.get("seconds"), 1) + "s")}{card("Zaman", esc(sc.get("generated_at")))}</div>'
        body += "<h2>Setup'lar</h2>" + table(["Sembol", "Yön", "Skor", "L/S", "Fiyat", "24s %", "Hacim 24s", "Funding %", "RSI 4h", "ATR %", "Etiketler"],
                                              [[f'<a href="/coin/{esc(str(r.get("symbol", "")).split("/")[0])}">{esc(r.get("symbol"))}</a>', verdict_badge(r.get("direction")), fmt(r.get("score"), 0),
                                                f"{fmt(r.get('score_long'), 0)}/{fmt(r.get('score_short'), 0)}", fmt(r.get("price")), pct(r.get("chg24_pct")), fmt(r.get("vol24_usdt"), 0),
                                                fmt(r.get("funding_pct"), 4), fmt(r.get("rsi_4h"), 1), fmt(r.get("atr_pct"), 2), esc(", ".join(r.get("tags") or []))] for r in rows[:150]],
                                              num_cols={2, 3, 4, 5, 6, 7, 8, 9}, empty="tarama sonucu yok")
        uni = state.get("universe") or {}
        if uni:
            body += "<h2>Evren</h2>" + kv_table(uni.get("counts") or {"spot": len(uni.get("spot") or []), "futures": len(uni.get("futures") or [])})
        return _page("Tarayıcı", body, "/scanner")

    @app.get("/coin/{base}", response_class=HTMLResponse)
    def coin(base: str, tf: str = "4h", market: str = "spot"):
        base = base.upper()[:16]
        h = state.coin_head(base) or {}
        b = state.brief(base) or {}
        sym = h.get("symbol") or b.get("symbol") or f"{base}/USDT"
        body = ""
        if h:
            fp, sp = h.get("futures_plan") or {}, h.get("spot_plan") or {}
            body += f'<div class="grid">{card("Karar", verdict_badge(h.get("verdict")), esc(h.get("no_trade_reason") or ""))}{card("Yön", esc(h.get("direction") or "-"))}{card("Güven", fmt(float(h.get("confidence_calibrated") or 0) * 100, 0) + "%")}{card("P(kazanç)", fmt(float(h.get("p_win") or 0) * 100, 0) + "%")}{card("Net E[r]", pct(h.get("expected_return_net")))}{card("E[R]", fmt(h.get("expected_r"), 2))}{card("Rejim", esc(h.get("regime")))}{card("Piyasa", esc(h.get("market_type")))}</div>'
        elif b:
            body += f'<div class="grid">{card("Karar (eski ajan)", verdict_badge(b.get("verdict")))}{card("Kanaat", fmt(b.get("conviction"), 0) + "%")}{card("Fiyat", fmt(b.get("price")))}{card("P(kazanç)", fmt(float(b.get("p_win") or 0) * 100, 0) + "%")}</div><p class="mut">{esc(b.get("headline"))}</p>'
        else:
            body += '<div class="card mut">Bu coin için karar yok; yalnızca grafik.</div>'
        body += "<h2>Grafik</h2>" + chart_block(base, tf, market, token_qs=token_qs, max_bars=cfg.max_bars)
        if h:
            def plan_kv(p: dict) -> str:
                if not p:
                    return '<div class="card mut">plan yok</div>'
                sz = p.get("size") or {}
                return kv_table({"geçerli": p.get("valid"), "geçersizlik": p.get("invalid_reason"), "yön": p.get("direction"), "giriş tipi": p.get("entry_type"), "tetik": p.get("entry_trigger"),
                                 "giriş bölgesi": p.get("entry_zone"), "giriş": p.get("entry"), "stop": p.get("stop"), "stop %": p.get("stop_pct"), "hedefler": p.get("targets"),
                                 "miktar": f"{sz.get('amount')} {sz.get('amount_type', '')}", "kaldıraç": sz.get("leverage"), "marj": p.get("margin"), "notional": p.get("notional"),
                                 "maliyet %": p.get("expected_cost_pct"), "E[R]": p.get("expected_r"), "ufuk (bar)": p.get("time_horizon_bars"), "geçersizleşme": p.get("invalidation")})
            body += '<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))"><div><h2>Spot planı</h2>' + plan_kv(sp) + '</div><div><h2>Futures planı</h2>' + plan_kv(fp) + "</div></div>"
            body += "<h2>Faktör skorları</h2>" + table(["Grup", "Skor", "Güven", "Veri kalitesi", "Bağımsız", "Çatışma"],
                                                       [[esc(f.get("group")), fmt(f.get("score"), 3), fmt(f.get("confidence"), 2), fmt(f.get("data_quality"), 2), fmt(f.get("n_independent"), 0), fmt(f.get("conflict"), 2)] for f in h.get("factor_scores") or []],
                                                       num_cols={1, 2, 3, 4, 5}, empty="faktör skoru yok")
            cons = h.get("consensus") or {}
            if cons:
                body += "<h3>Konsensüs</h3>" + kv_table(cons)
            body += "<h2>Uzman ajanlar</h2>" + table(["Ajan", "Grup", "Duruş", "Bias", "Güven", "Lehte", "Aleyhte", "Uyarı", "Veto/Hata"],
                                                     [[esc(r.get("agent_name")), esc(r.get("factor_group")), esc(r.get("stance")), fmt(r.get("bias"), 2), fmt(r.get("confidence_raw"), 0),
                                                       esc("; ".join(r.get("evidence_for") or [])[:160]), esc("; ".join(r.get("evidence_against") or [])[:160]), esc("; ".join(r.get("warnings") or [])[:120]),
                                                       (badge("VETO " + str(r.get("veto_reason") or ""), "bad") if r.get("veto") else (badge("hata", "warn") if r.get("error") else ""))] for r in h.get("specialist_reports") or []],
                                                     num_cols={3, 4}, empty="uzman raporu yok")
            body += "<h2>Red team</h2>" + ("<ul>" + "".join(f"<li>{badge('VETO', 'bad')} {esc(v)}</li>" for v in h.get("vetoes") or []) + "</ul>" if h.get("vetoes") else '<div class="card mut">veto yok</div>')
            if h.get("dissent"):
                body += "<h3>Muhalefet</h3><ul>" + "".join(f"<li>{esc(x)}</li>" for x in h["dissent"]) + "</ul>"
            body += "<h2>Meta</h2>" + kv_table({"run_id": h.get("run_id"), "snapshot_id": h.get("snapshot_id"), "coin_head_id": h.get("coin_head_id"), "generated_at": h.get("generated_at"), "expires_at": h.get("expires_at"), "model_versions": h.get("model_versions"), "data_freshness": h.get("data_freshness"), "latency_ms": h.get("latency_ms")})
        elif b:
            body += "<h2>Plan (eski)</h2>" + kv_table(b.get("plan") or {})
            body += "<h2>YAP / YAPMA</h2><ul>" + "".join(f"<li>✅ {esc(x)}</li>" for x in b.get("do_list") or []) + "".join(f"<li>🚫 {esc(x)}</li>" for x in b.get("dont_list") or []) + "</ul>"
            body += "<h2>Ajan raporları</h2>" + render_any(b.get("reports") or [])
        pos = [p for p in state.futures_positions() if str(p.get("symbol", "")).upper().startswith(base + "/")]
        if pos:
            body += "<h2>Açık pozisyon</h2>" + _positions_table(pos)
        return _page(f"{sym}", body, "/")

    @app.get("/portfolio/spot", response_class=HTMLResponse)
    def portfolio_spot():
        pf = state.get("portfolio") or {}
        pos = pf.get("positions") or {}
        body = f'<div class="grid">{card("Nakit", fmt(pf.get("cash"), 2) + " USDT")}{card("Başlangıç", fmt(pf.get("starting_equity"), 2))}{card("Özkaynak (tahmini)", fmt(state.spot_equity(), 2))}{card("Güncelleme", esc(pf.get("updated_at")))}</div>'
        body += "<h2>Açık pozisyonlar</h2>" + table(["Sembol", "Miktar", "Giriş", "Stop", "Strateji", "Açılış"],
                                                    [[f'<a href="/coin/{esc(k.split("/")[0])}">{esc(k)}</a>', fmt(p.get("units")), fmt(p.get("entry_price")), fmt(p.get("stop")), esc(p.get("strategy")), esc(p.get("entry_time"))] for k, p in (pos.items() if isinstance(pos, dict) else [])],
                                                    num_cols={1, 2, 3}, empty="açık spot pozisyon yok")
        hist = pf.get("history") or []
        body += "<h2>Geçmiş</h2>" + render_any(hist[-100:][::-1]) if hist else "<h2>Geçmiş</h2>" + '<div class="card mut">kapanmış spot işlem yok</div>'
        return _page("Spot Portföy", body, "/portfolio/spot")

    @app.get("/portfolio/futures", response_class=HTMLResponse)
    def portfolio_futures():
        led = state.get("futures_ledger") or {}
        pos = state.futures_positions()
        body = f'<div class="grid">{card("Özkaynak", fmt(state.futures_equity(), 2) + " USDT")}{card("Başlangıç", fmt(led.get("starting_equity"), 2))}{card("Toplam ücret", fmt(led.get("total_fees"), 4))}{card("Toplam funding", fmt(led.get("total_funding"), 4))}{card("Açık", str(len(pos)))}{card("Kapanan", str(len(led.get("history") or [])))}{card("Şema", esc(led.get("schema_version", 1)))}{card("Güncelleme", esc(led.get("updated_at")))}</div>'
        body += "<h2>Açık pozisyonlar</h2>" + _positions_table(pos)
        hist = led.get("history") or []
        body += "<h2>Kapanan işlemler</h2>" + _trades_table(hist[::-1][:100])
        return _page("Futures Portföy", body, "/portfolio/futures")

    def _trades_table(trades: list[dict]) -> str:
        rows = []
        for t in trades:
            tid = str(t.get("id") or "")
            rows.append([f'<a href="/trades/{esc(tid)}">{esc(tid[:18])}</a>', esc(t.get("symbol")), verdict_badge(t.get("side")), fmt(t.get("entry")), fmt(t.get("exit_price")),
                         pnl_cell(t.get("pnl", t.get("net_pnl"))), fmt(t.get("r_multiple"), 2), fmt(t.get("fees"), 4), fmt(t.get("funding"), 4), f"{t.get('leverage', 1)}x", esc(t.get("exit_reason")), esc(t.get("closed_at") or t.get("exit_time"))])
        return table(["ID", "Sembol", "Yön", "Giriş", "Çıkış", "PnL", "R", "Ücret", "Funding", "Kald.", "Neden", "Kapanış"], rows, num_cols={3, 4, 5, 6, 7, 8}, empty="işlem yok")

    @app.get("/orders", response_class=HTMLResponse)
    def orders():
        ords = state.orders()
        sb = state.get("shadow_book") or {}
        body = "<h2>Emir günlüğü</h2>" + (render_any(ords[:200]) if ords else '<div class="card mut">emir kaydı yok (orders.json / ledger entries)</div>')
        if sb:
            body += "<h2>Gölge defter (shadow book)</h2>" + render_any(sb)
        return _page("Emirler", body, "/orders")

    @app.get("/trades", response_class=HTMLResponse)
    def trades():
        tr = state.trades()
        wins = [t for t in tr if float(t.get("pnl", t.get("net_pnl", 0)) or 0) > 0]
        tot = sum(float(t.get("pnl", t.get("net_pnl", 0)) or 0) for t in tr)
        body = f'<div class="grid">{card("Toplam işlem", str(len(tr)))}{card("Kazanan", f"{len(wins)} ({(len(wins) / len(tr) * 100 if tr else 0):.0f}%)")}{card("Toplam PnL", f"{tot:+,.2f}")}</div>'
        body += _trades_table(tr[:300])
        return _page("İşlemler", body, "/trades")

    @app.get("/trades/{trade_id}", response_class=HTMLResponse)
    def trade_detail(trade_id: str):
        t = state.trade(trade_id)
        if not t:
            return _page("İşlem", '<div class="card">işlem bulunamadı</div>', "/trades")
        base = str(t.get("symbol", "")).split("/")[0]
        body = f'<p><a href="/trades">← işlemler</a> · <a href="/coin/{esc(base)}">{esc(t.get("symbol"))}</a></p>'
        body += kv_table({k: v for k, v in t.items() if k not in ("features", "fills", "costs")})
        if t.get("costs"):
            body += "<h2>Maliyet dökümü</h2>" + kv_table(t["costs"])
        if t.get("fills"):
            body += "<h2>Doldurmalar</h2>" + render_any(t["fills"])
        if t.get("features"):
            body += "<h2>Giriş özellikleri</h2>" + render_any(t["features"])
        mem = [m for m in state.tail_jsonl("trade_memory", 2000) if str(m.get("trade_id") or m.get("id")) == trade_id]
        if mem:
            body += "<h2>Post-mortem / hafıza</h2>" + render_any(mem)
        return _page(f"İşlem {trade_id[:20]}", body, "/trades")

    @app.get("/learning", response_class=HTMLResponse)
    def learning():
        ln = state.get("learning") or {}
        body = ""
        if ln:
            body += f'<div class="grid">{card("İşlem", fmt(ln.get("n_trades"), 0))}{card("Kazanan", fmt(ln.get("n_wins"), 0))}{card("Σ R", fmt(ln.get("sum_r"), 2))}{card("Güncelleme", esc(ln.get("updated_at")))}</div>'
            if ln.get("weights"):
                body += "<h2>Özellik ağırlıkları</h2>" + kv_table({k: round(float(v), 4) for k, v in ln["weights"].items()})
            if ln.get("agent_weights"):
                body += "<h2>Ajan ağırlıkları</h2>" + kv_table(ln["agent_weights"])
            if ln.get("agent_hits"):
                body += "<h2>Ajan isabet</h2>" + table(["Ajan", "Doğru", "Toplam", "%"], [[esc(a), fmt(h[0], 0), fmt(h[1], 0), fmt(h[0] / h[1] * 100 if h[1] else 0, 0)] for a, h in ln["agent_hits"].items() if isinstance(h, (list, tuple)) and len(h) == 2], num_cols={1, 2, 3})
            for key, title in (("setup_stats", "Setup istatistikleri"), ("symbol_stats", "Sembol istatistikleri"), ("exit_stats", "Çıkış nedenleri")):
                if ln.get(key):
                    body += f"<h2>{title}</h2>" + render_any(ln[key])
            if ln.get("lessons"):
                body += "<h2>Dersler</h2>" + render_any(ln["lessons"][-30:][::-1])
            if ln.get("blacklist"):
                body += "<h2>Kara liste</h2><ul>" + "".join(f"<li>{esc(x)}</li>" for x in ln["blacklist"]) + "</ul>"
        else:
            body = '<div class="card mut">learning.json yok</div>'
        return _page("Öğrenme", body, "/learning")

    @app.get("/backtest", response_class=HTMLResponse)
    def backtest():
        sg = state.get("signals") or {}
        body = ""
        if sg:
            body += f'<div class="grid">{card("Çalışma", esc(sg.get("run_time")))}{card("Borsa", esc(sg.get("exchange")))}{card("TF", esc(sg.get("timeframe")))}{card("Rejim", esc((sg.get("summary") or {}).get("market_regime")))}</div>'
            dec = sg.get("decisions") or []
            body += "<h2>Kararlar</h2>" + table(["Sembol", "Aksiyon", "Güven", "Fiyat", "Strateji", "Nedenler"],
                                                [[f'<a href="/coin/{esc(str(d.get("symbol", "")).split("/")[0])}">{esc(d.get("symbol"))}</a>', verdict_badge(d.get("action")), fmt(d.get("confidence"), 0), fmt(d.get("price")), esc(d.get("strategy")), esc("; ".join(d.get("reasons") or [])[:200])] for d in dec], num_cols={2, 3}, empty="karar yok")
            an = sg.get("analyses") or []
            if an:
                body += "<h2>Analizler / WFO</h2>" + render_any(an)
        else:
            body = '<div class="card mut">signals.json yok</div>'
        return _page("Backtest", body, "/backtest")

    @app.get("/risk", response_class=HTMLResponse)
    def risk():
        r = state.get("risk") or {}
        ks = state.get("killswitch") or r.get("killswitch") or {}
        exp = r.get("exposure") or {}
        md = state.get("mode") or {}
        body = f'<div class="grid">{card("Kill switch", ks_badge(ks.get("state", "ARMED")), esc(ks.get("since") or ""))}{card("Mod", esc(md.get("mode") or "PAPER"))}{card("Özkaynak", fmt(exp.get("equity"), 2))}{card("HWM", fmt(exp.get("hwm"), 2))}{card("Drawdown", pct(exp.get("drawdown_pct")))}{card("Açık risk", fmt(exp.get("total_open_risk_usdt"), 2))}{card("Bugün PnL", fmt(exp.get("pnl_today"), 2))}{card("Hafta PnL", fmt(exp.get("pnl_week"), 2))}</div>'
        if ks.get("reasons"):
            body += "<h2>Kill switch nedenleri</h2>" + render_any(ks["reasons"])
        if ks.get("audit"):
            body += "<h3>Denetim izi (son 30)</h3>" + render_any(list(ks["audit"])[-30:][::-1])
        body += "<h2>Risk profili</h2>" + (kv_table(r.get("profile") or {}) if r.get("profile") else '<div class="card mut">risk.json yok</div>')
        body += "<h2>Maruziyet</h2>" + kv_table({k: v for k, v in exp.items() if k != "positions"})
        if exp.get("positions"):
            body += "<h3>Pozisyonlar</h3>" + render_any(exp["positions"])
        if md.get("history"):
            body += "<h2>Mod geçmişi</h2>" + render_any(list(md["history"])[-20:][::-1])
        return _page("Risk", body, "/risk")

    @app.get("/health", response_class=HTMLResponse)
    def health_page():
        h = state.get("health") or {}
        hb = state.get("heartbeat") or {}
        body = f'<div class="grid">{card("Durum", health_badge(h.get("state", "UNKNOWN")), esc(h.get("summary") or ""))}{card("Kalp atışı", age_text(state.heartbeat_age()) + " önce", esc(hb.get("ts") or ""))}{card("Son tur", age_text(state.last_run_age()) + " önce")}{card("PID", esc(hb.get("pid") or "-"))}{card("run_id", esc(hb.get("run_id") or "-"))}</div>'
        chk = h.get("checks") or []
        body += "<h2>Kontroller</h2>" + table(["Kontrol", "Durum", "Detay", "Önem"], [[esc(c.get("name")), badge("ok", "ok") if c.get("ok") else badge("HATA", "bad"), esc(json.dumps(c.get("detail"), ensure_ascii=False) if isinstance(c.get("detail"), (dict, list)) else c.get("detail")), esc(c.get("severity"))] for c in chk], empty="health.json yok")
        body += "<h2>State dosyaları</h2>" + table(["Dosya", "Yaş"], [[esc(STATE_FILES.get(k, k)), age_text(time.time() - m)] for k, m in sorted(state.mtimes().items())])
        _st = state.snapshot_telemetry()
        _c = _st["counters"]
        body += ('<div class="grid">'
                 + card("Snapshot üretimi", f"{_c['snapshot_success_total']} başarılı",
                        f"{_c['snapshot_failure_total']} hata · {_c['leakage_failure_total']} nedensellik")
                 + card("Model şeması", f"{_c['schema_mismatch_total']} uyuşmazlık",
                        esc(_st["last_failure_code"] or "-"))
                 + '</div>')
        _rp = state.learning_research()
        _rs = _rp.get("active_stats") or {}
        body += ('<div class="grid">'
                 + card("Araştırma politikası", esc(_rp.get("active_policy_id") or "yok (baseline)"),
                        esc(_rp.get("active_rationale") or "aktif aday yok — bot baseline davranışında"))
                 + card("Eşleşmiş gözlem", f"{int(_rs.get('n_obs', 0) or 0)}",
                        f"fark {_rs.get('delta_r')} R · {int(_rs.get('blocked', 0) or 0)} elenen")
                 + card("Otomatik terfi", "KAPALI" if not _rp.get("auto_promotion_possible") else "AÇIK",
                        "CHAMPION yalnız manuel operatör onayıyla")
                 + '</div>')
        body += '<p class="small mut">Uçlar: <a href="/health/live">/health/live</a> · <a href="/health/ready">/health/ready</a> · <a href="/metrics">/metrics</a> · <a href="/api/overview">/api/overview</a></p>'
        return _page("Sağlık", body, "/health")

    @app.get("/llm", response_class=HTMLResponse)
    def llm():
        b = state.get("llm_budget") or {}
        calls = state.tail_jsonl("llm_calls", 200)[::-1]
        body = f'<div class="grid">{card("Gün", esc(b.get("day")))}{card("Harcanan $", fmt(b.get("spent_usd"), 4))}{card("Token", fmt(b.get("spent_tokens"), 0))}{card("Çağrı", fmt(b.get("calls"), 0))}{card("Limit $", fmt(b.get("limit_usd"), 2))}</div>'
        body += "<h2>Son çağrılar</h2>" + (table(["Zaman", "Model", "Amaç", "Girdi tok", "Çıktı tok", "$", "Süre ms", "Şema ok", "Hata"],
                                                 [[esc(c.get("ts") or c.get("created_at")), esc(c.get("model")), esc(c.get("purpose") or c.get("kind")), fmt(c.get("input_tokens"), 0), fmt(c.get("output_tokens"), 0), fmt(c.get("cost_usd"), 5), fmt(c.get("latency_ms"), 0), "✅" if c.get("schema_ok", True) else "❌", esc(c.get("error") or "")] for c in calls], num_cols={3, 4, 5, 6}) if calls else '<div class="card mut">llm_calls.jsonl yok</div>')
        return _page("LLM", body, "/llm")

    @app.get("/models", response_class=HTMLResponse)
    def models():
        m = state.get("models") or {}
        ms = m.get("models") or []
        body = table(["Model", "Tür", "Durum", "Oluşturma", "Metrikler"], [[esc(x.get("id")), esc(x.get("kind")), badge(x.get("status"), "ok" if x.get("status") in ("active", "champion", "ACTIVE") else "info"), esc(x.get("created_at")), esc(", ".join(f"{k}={fmt(v, 3)}" for k, v in (x.get("metrics") or {}).items()))] for x in ms], empty="models.json yok")
        return _page("Modeller", body, "/models")

    # ------------------------------------------------------------------ API
    @app.get("/api/candles/{base}")
    def api_candles(base: str, tf: str = Query("4h"), market: str = Query("spot"), n: int = Query(600)):
        base = base.upper()[:16]
        tf = tf if tf in ("1h", "4h", "1d", "15m", "1w") else "4h"
        market = "futures" if market == "futures" else "spot"
        n = max(50, min(int(n), cfg.max_bars))
        df = candles.load(base, tf, market, n=n + 250)
        if df is None:
            return JSONResponse({"base": base, "tf": tf, "market": market, "t": [], "o": [], "h": [], "l": [], "c": [], "v": [], "overlays": {}, "levels": [], "plan": {}, "position": {}, "panels": {}, "error": f"{base} {tf} mum verisi yok"}, status_code=404)
        h = state.coin_head(base) or {}
        b = state.brief(base) or {}
        plan = None
        ap = (h.get("futures_plan") if market == "futures" else h.get("spot_plan")) or h.get("futures_plan") or h.get("spot_plan")
        if ap and ap.get("valid"):
            plan = ap
        elif b.get("plan") and b["plan"].get("direction") not in (None, "BEKLE"):
            plan = b["plan"]
        pos = next((p for p in state.futures_positions() if str(p.get("symbol", "")).upper().startswith(base + "/")), None)
        levels = b.get("key_levels") or {}
        if not levels:
            for r in h.get("specialist_reports") or []:
                if r.get("levels"):
                    levels = r["levels"]; break
        funding, oi = [], []
        for r in h.get("specialist_reports") or []:
            m = r.get("metrics") or {}
            if isinstance(m.get("funding_history"), list):
                funding = m["funding_history"]
            if isinstance(m.get("oi_history"), list):
                oi = m["oi_history"]
        return JSONResponse(build_candle_payload(df, n=n, plan=plan, position=pos, levels=levels, funding=funding, oi=oi, base=base, tf=tf, market=market))

    @app.get("/api/state/{name}")
    def api_state(name: str):
        if name not in STATE_FILES:
            raise HTTPException(404, "bilinmeyen state adı")
        d = state.get(name)
        if d is None:
            raise HTTPException(404, "dosya yok")
        return JSONResponse(d)

    @app.get("/api/evidence/{base}")
    def api_evidence(base: str):
        """Tarihsel benzer olay kanıtı (state/evidence/<SYM>_USDT.json): paketler + deterministik açıklama."""
        d = state.evidence(base)
        if d is None:
            raise HTTPException(404, "kanıt yok")
        return JSONResponse(d)

    @app.get("/api/overview")
    def api_overview():
        return JSONResponse(state.overview())

    # ------------------------------------------------------------------ sağlık / metrik
    @app.get("/health/live")
    def health_live():
        return JSONResponse({"status": "ok", "ts": utc_now().isoformat(timespec="seconds")})

    @app.get("/health/ready")
    def health_ready():
        checks = _ready_checks()
        ok = all(c["ok"] for c in checks)
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    def _ready_checks() -> list[dict]:
        hb = state.heartbeat_age()
        h = state.get("health") or {}
        return [
            {"name": "state_readable", "ok": state.readable(), "detail": str(state.state_dir)},
            {"name": "heartbeat", "ok": hb is not None and hb <= cfg.heartbeat_max_age_s, "detail": {"age_s": hb, "max_s": cfg.heartbeat_max_age_s}},
            {"name": "health_state", "ok": str(h.get("state", "HEALTHY")) in ("HEALTHY", "DEGRADED"), "detail": h.get("state", "UNKNOWN")},
        ]

    @app.get("/metrics")
    def metrics():
        ov = state.overview()
        ks_map = {"ARMED": 0, "HALT_ENTRIES": 1, "HALT_ALL": 2}
        hs_map = {"HEALTHY": 0, "DEGRADED": 1, "PAUSED": 2, "DATA_STALE": 3, "KILL_SWITCH": 4, "RECONCILIATION_REQUIRED": 5}
        lines = [
            "# HELP tradingbot_up 1 if dashboard is serving", "# TYPE tradingbot_up gauge", "tradingbot_up 1",
            "# HELP tradingbot_tour_age_seconds seconds since last tour output", "# TYPE tradingbot_tour_age_seconds gauge",
            f"tradingbot_tour_age_seconds {ov['last_run_age_s'] if ov['last_run_age_s'] is not None else 'NaN'}",
            "# HELP tradingbot_heartbeat_age_seconds seconds since last engine heartbeat", "# TYPE tradingbot_heartbeat_age_seconds gauge",
            f"tradingbot_heartbeat_age_seconds {ov['heartbeat_age_s'] if ov['heartbeat_age_s'] is not None else 'NaN'}",
            "# HELP tradingbot_open_positions open futures positions", "# TYPE tradingbot_open_positions gauge",
            f"tradingbot_open_positions {len(ov['open_positions'])}",
            "# HELP tradingbot_equity_futures paper futures equity USDT", "# TYPE tradingbot_equity_futures gauge",
            f"tradingbot_equity_futures {ov['equity_futures'] if ov['equity_futures'] is not None else 'NaN'}",
            "# HELP tradingbot_equity_spot paper spot equity USDT", "# TYPE tradingbot_equity_spot gauge",
            f"tradingbot_equity_spot {ov['equity_spot'] if ov['equity_spot'] is not None else 'NaN'}",
            "# HELP tradingbot_killswitch_state 0 ARMED 1 HALT_ENTRIES 2 HALT_ALL", "# TYPE tradingbot_killswitch_state gauge",
            f"tradingbot_killswitch_state {ks_map.get(str(ov['killswitch']), 2)}",
            "# HELP tradingbot_health_state 0 HEALTHY 1 DEGRADED 2 PAUSED 3 DATA_STALE 4 KILL_SWITCH 5 RECONCILIATION_REQUIRED", "# TYPE tradingbot_health_state gauge",
            f"tradingbot_health_state {hs_map.get(str(ov['health']), 1)}",
            "# HELP tradingbot_llm_spent_usd_today LLM spend today USD", "# TYPE tradingbot_llm_spent_usd_today gauge",
            f"tradingbot_llm_spent_usd_today {ov['llm_spent_usd_today'] if ov['llm_spent_usd_today'] is not None else 0}",
        ]
        # FeatureSnapshotV3 telemetrisi: arastirma snapshot'i sessizce kaybolamaz
        _st = (ov.get("snapshot_telemetry") or {}).get("counters") or {}
        for _name, _help in (("snapshot_success_total", "FeatureSnapshotV3 produced"),
                             ("snapshot_failure_total", "FeatureSnapshotV3 failures"),
                             ("leakage_failure_total", "FeatureSnapshotV3 causality violations"),
                             ("schema_mismatch_total", "p_win model schema mismatches (model not used)")):
            lines += [f"# HELP tradingbot_{_name} {_help}", f"# TYPE tradingbot_{_name} counter",
                      f"tradingbot_{_name} {int(_st.get(_name, 0) or 0)}"]
        # PAPER araştırma politikası: aktif mi, kaç eşleşmiş gözlem, baseline'a göre fark
        _rp = ov.get("learning_research") or {}
        _rs = _rp.get("active_stats") or {}
        lines += ["# HELP tradingbot_research_policy_active 1 if a PAPER research policy is active",
                  "# TYPE tradingbot_research_policy_active gauge",
                  f"tradingbot_research_policy_active {1 if _rp.get('active_policy_id') else 0}",
                  "# HELP tradingbot_research_observations paired baseline/candidate observations",
                  "# TYPE tradingbot_research_observations gauge",
                  f"tradingbot_research_observations {int(_rs.get('n_obs', 0) or 0)}",
                  "# HELP tradingbot_research_delta_r candidate minus baseline expectancy (R)",
                  "# TYPE tradingbot_research_delta_r gauge",
                  f"tradingbot_research_delta_r {_rs.get('delta_r') if _rs.get('delta_r') is not None else 'NaN'}",
                  "# HELP tradingbot_auto_promotion_enabled 1 if automatic CHAMPION promotion is possible",
                  "# TYPE tradingbot_auto_promotion_enabled gauge",
                  f"tradingbot_auto_promotion_enabled {1 if _rp.get('auto_promotion_possible') else 0}"]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")

    # ------------------------------------------------------------------ SSE
    @app.get("/events")
    async def events(request: Request):
        async def gen():
            last = state.mtimes()
            last_hb = time.monotonic()
            yield f"event: hello\ndata: {json.dumps({'ts': utc_now().isoformat(timespec='seconds')})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                cur = state.mtimes()
                changed = sorted(k for k in set(cur) | set(last) if cur.get(k) != last.get(k))
                if changed:
                    last = cur
                    yield f"event: state\ndata: {json.dumps({'changed': changed, 'ts': utc_now().isoformat(timespec='seconds')})}\n\n"
                if time.monotonic() - last_hb >= cfg.sse_heartbeat_s:
                    last_hb = time.monotonic()
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': utc_now().isoformat(timespec='seconds')})}\n\n"
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def run_dashboard(state_dir: Path | str, data_dir: Path | str, vault_dir: Path | str | None = None,
                  cfg: DashboardConfig | None = None) -> None:
    import threading

    import uvicorn

    from ..ops.shutdown import InstanceRecord, StopWatcher
    cfg = cfg or DashboardConfig()
    app = create_app(state_dir, data_dir, vault_dir, cfg)
    log.info("panel başlıyor: http://%s:%s (salt-okunur, token=%s)", cfg.host, cfg.port, "evet" if cfg.auth_token else "yok")
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=int(cfg.port), log_level="info", access_log=False))
    inst = InstanceRecord(state_dir, "dashboard", {"host": cfg.host, "port": int(cfg.port)})
    inst.register()
    watcher = StopWatcher(state_dir, inst.token)
    threading.Thread(target=poll_stop_request, args=(server, watcher), daemon=True, name="dashboard-stop-poller").start()
    try:
        server.run()
    finally:
        watcher.consume()
        inst.unregister()
        log.info("panel temiz durduruldu")


def poll_stop_request(server, watcher, interval_s: float = 1.0) -> None:
    """Kooperatif durdurma: istek (doğru token) görülünce uvicorn'a `should_exit` verilir (graceful: bağlantılar kapanır, lifespan biter)."""
    import time as _t
    while not getattr(server, "should_exit", False):
        if watcher.requested():
            server.should_exit = True
            return
        _t.sleep(interval_s)


__all__ = ["create_app", "run_dashboard"]

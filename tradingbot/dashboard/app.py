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
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from ..core import ConfigError, utc_now
from ..learn.entry_eval import GATE_MIN_DAYS, GATE_MIN_LINKED_CLOSES as GATE_MIN_LINKED
from .candles import CandleSource, build_candle_payload
from .config import DashboardConfig
from ..pnl import finite_float_or_none, position_view, realized_net
from .state import STATE_FILES, StateReader
from .views import (NO_DECISION_VERDICT, POSITION_NUM_COLS, coin_head_api_rows,
                    coin_head_table, json_safe, open_coverage)
from .templates import (HEADS_TABLE_CLS, POS_TABLE_CLS, age_text, badge, card, card_value,
                        chart_block, chief_block, esc, fmt, fmt_utc, health_badge, ks_badge,
                        kv_table, lessons_table, live_bar, live_script, money_html,
                        money_html_text, page, pct, pnl_cell, render_any, sample_banner, table,
                        challenger_blocks, observation_block, quality_block,
                        retention_block,
                        calibration_block, verdict_badge, verdict_kind, weight_table)

log = logging.getLogger(__name__)
_PLOTLY_CACHE: dict[str, bytes] = {}
_PUBLIC_PATHS = ("/health/live",)


def _int_or_none(x: Any) -> int | None:
    """Sayaç alanı → int; alan yok/bozuksa `None` («Veri yok»), sessiz `0` DEĞİL.

    `int(x or 0)` kullanılsaydı «alan hiç yok» ile «gerçekten 0 işlem» ayırt edilemezdi.
    """
    if x is None or isinstance(x, bool):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _learning_counters(ln: dict) -> tuple[int | None, int | None, int | None, float | None, str]:
    """`learning.json` sayaçları → (n_trades, n_wins, kazanmayan, oran%, bozukluk-nedeni).

    Geçersiz/çelişkili state'te DÖRT değer de `None` döner ve neden metni dolu gelir:
      * negatif sayaç           → `n_trades=-5` / `n_wins=-2`
      * n_wins > n_trades       → `3 / 7` → «Kazanmayan = -4», «%233.3» gibi uydurma sayı ÜRETİLMEZ
    Değerler KIRPILMAZ (7→3, -5→0 yapılmaz): state bozukluğu kullanıcıdan gizlenmez.
    Geçerli sözleşme (0/0, 5/2, 250/100) ve «n_wins yok → yalnız ilgili kartlar Veri yok» aynen korunur.
    """
    n = _int_or_none(ln.get("n_trades"))
    w = _int_or_none(ln.get("n_wins"))
    bad = ""
    if n is not None and n < 0:
        bad = f"bozuk sayaç: n_trades negatif ({n})"
    elif w is not None and w < 0:
        bad = f"bozuk sayaç: n_wins negatif ({w})"
    elif n is not None and w is not None and w > n:
        bad = f"çelişkili sayaç: n_wins ({w}) > n_trades ({n})"
    if bad:
        return None, None, None, None, bad
    notwin = (n - w) if (n is not None and w is not None) else None
    wr = (w / n * 100.0) if (n and w is not None) else None
    return n, w, notwin, wr, ""


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
    def _view():
        return state.view_model(stale_price_s=cfg.stale_price_s, stale_run_s=cfg.stale_run_s,
                                tz_label=cfg.timezone_label)

    @app.get("/", response_class=HTMLResponse)
    def overview():
        ov = state.overview()
        vm = _view()
        pv, cv, fr = vm["portfolio"], vm["chief"], vm["freshness"]
        body = live_bar(fr)
        # --- TUTARSIZLIK: sessizce yanlis sayi GOSTERME ---
        for issue in vm["inconsistencies"]:
            body += f'<div class="card warn-box">⚠ Veri tutarsızlığı tespit edildi — {esc(issue["message"])}</div>'
        # --- genel kar/zarar ozeti (en ustte) ---
        # `c.display` ZATEN biçimlenmiş metindir; ikinci kez para biçimlendirmesine SOKULMAZ.
        # (Eski kod `money_html("+$2.86")` çağırıyor, `Decimal` çözemediği için `$0.00` basıyordu.)
        body += "<h2>Kâr / Zarar özeti</h2><div class=\"grid\" id=\"sumgrid\">" + "".join(
            card(c.title, card_value(c), c.sub, cid="sc-" + c.key) for c in vm["cards"]) + "</div>"
        body += f'<div class="grid">{"".join([
            card("Futures özkaynak", fmt(ov["equity_futures"], 2) + " USDT"),
            card("Spot özkaynak", fmt(ov["equity_spot"], 2) + " USDT"),
            card("Açık pozisyon", str(pv.open_total), f"LONG {pv.open_long} · SHORT {pv.open_short}"),
            card("Kill switch", ks_badge(ov["killswitch"])),
            card("Mod", badge(ov["mode"], "info" if ov["mode"] in ("PAPER", "OBSERVE", "TESTNET") else "bad")),
            card("Sağlık", health_badge(ov["health"]), esc(ov["health_summary"])),
            card("Son strateji turu", age_text(ov["last_run_age_s"]) + " önce", "kalp atışı " + age_text(ov["heartbeat_age_s"])),
            card("LLM bugün", (fmt(ov["llm_spent_usd_today"], 3) + " $") if ov["llm_spent_usd_today"] is not None else "Veri yok"),
        ])}</div>'
        body += chief_block(cv)
        body += '<h2>Açık pozisyonlar</h2><div id="postbl">' + _positions_table(vm) + "</div>"
        chp = _coin_head_payload()
        body += (_coin_heads_heading(chp) + '<div id="headstbl">' + _heads_table(chp) + "</div>"
                 + '<div id="headsstale" class="warn-box" style="display:none">'
                   '⚠ Coin head verisi yenilenemedi — tablo SON BAŞARILI çekimi gösteriyor.</div>')
        if not ov["top_heads"]:
            ag = state.get("agents") or {}
            briefs = ag.get("briefs") or []
            if briefs:
                body += "<h3>Eski ajan brifingleri</h3>" + table(["Coin", "Karar", "Kanaat", "Fiyat", "Manşet"],
                                                                 [[f'<a href="/coin/{esc(b.get("symbol", "").split("/")[0])}">{esc(b.get("symbol"))}</a>', verdict_badge(b.get("verdict")), fmt(b.get("conviction"), 0), fmt(b.get("price")), esc(b.get("headline"))] for b in briefs])
        return _page("Genel Bakış", body, "/", extra_head=live_script(cfg))

    def _positions_table(vm) -> str:
        """`vm` ya hazır görünüm modeli ya da ham pozisyon listesidir (coin/futures sayfaları)."""
        if isinstance(vm, list):
            from .views import build as _build
            vm = _build(vm, [], None, marks=state.marks(), fees=state.fee_schedule())
        rows = []
        for v, r in zip(vm["portfolio"].positions, vm["rows"]):
            # Sembol sütunu sabit genişliktedir ve uzun sembolde ellipsis'lenir → tam değer
            # `title` içinde kalır (polling JS'i de ilk üç sütuna aynı `title`'ı koyar).
            cells = [f'<a href="/coin/{esc(v.symbol.split("/")[0])}" title="{esc(v.symbol)}">{esc(v.symbol)}</a>',
                     badge(r[1], "info"), verdict_badge(r[2])] + [esc(x) for x in r[3:16]]
            cells.append(money_html(v.net_unrealized))
            cells.append(money_html(v.net_unrealized_pct, pct=True))
            cells += [esc(r[18]), esc(r[19])]
            rows.append(cells)
        note = ('<p class="mut small">Yüzde paydası: FUTURES → kullanılan başlangıç teminatı, '
                'SPOT → yatırılan tutar. «Coin adedi» USDT değil, coin/kontrat adedidir.</p>')
        return table(vm["columns"], rows, num_cols=set(POSITION_NUM_COLS), empty="açık pozisyon yok",
                     cls=POS_TABLE_CLS) + note        # polling JS'i AYNI sabiti kullanır

    def _coin_head_payload() -> dict:
        """Coin head tablosunun KANONİK yükü — HTML render'ı ve `/api/live/coin-heads` AYNI çağrı."""
        return coin_head_table(state.coin_heads(), state.futures_positions(), state.trades(),
                               fees=state.fee_schedule())

    def _coin_heads_heading(chp: dict) -> str:
        """Başlık + AÇIK POZİSYON KAPSAMI sayacı (polling bu düğümleri yerinde günceller).

        Bu tablo bir açık pozisyon listesi DEĞİLDİR (coin head seçkisidir); fakat bütün açık
        pozisyonların yer aldığı ÖLÇÜLEREK gösterilir. Eksik varsa kırmızı uyarı çıkar.
        """
        # Sayaç RENDER EDİLEN satırlardan ÖLÇÜLÜR — iddiaya güvenilmez.
        cov = open_coverage(chp["heads"], state.futures_positions())
        total, shown = int(cov["open_positions_total"]), int(cov["open_positions_shown"])
        missing = list(cov["missing_open_symbols"])
        cls = "b-ok" if not missing else "b-bad"
        out = ("<h2>Coin head'ler <span id=\"headscov\" class=\"badge %s\">"
               "Açık pozisyon kapsamı: %d / %d</span></h2>" % (cls, shown, total))
        out += ("<p class=\"mut small\">Bu tablo AÇIK POZİSYON LİSTESİ DEĞİLDİR — coin head'lerin son "
                "seçkisidir. Açık pozisyonların TAMAMI zorunlu olarak en üstte listelenir; altında "
                "kalan kapasiteye göre sıralanmış adaylar/son kararlar gelir. "
                "Açık pozisyonların yetkili listesi «Açık pozisyonlar» tablosudur.</p>")
        out += ('<div id="headsmiss" class="warn-box"%s>%s</div>'
                % ("" if missing else ' style="display:none"', _missing_text(missing)))
        return out

    def _missing_text(missing: list) -> str:
        if not missing:
            return ""
        return ('⚠ Açık pozisyon tabloda GÖRÜNMÜYOR: %s — panel eksik kapsam bildiriyor, '
                'defter yetkilidir.' % esc(", ".join(str(m) for m in missing)))

    def _heads_table(chp: dict) -> str:
        """Satırları KANONİK yükten çizer — iş kuralı burada YOK (bkz. views.coin_head_table).

        Aynı yük `/api/live/coin-heads` ile tarayıcıya gider; polling `buildHeadsTable` ile
        BU markup'ın aynısını üretir (kolon/sınıf/sıra sözleşmesi tek kaynaktan).
        """
        num, pnlc, badges = set(chp["num_cols"]), set(chp["pnl_cols"]), set(chp["badge_cols"])
        sym_col = int(chp["symbol_col"])
        rows = []
        for r, m in zip(chp["rows"], chp["meta"]):
            cells = []
            for i, raw in enumerate(r):
                txt = "" if raw is None else str(raw)
                if i == sym_col:
                    cells.append('<a href="/coin/%s">%s</a>' % (esc(txt.split("/")[0]), esc(txt)))
                elif i in badges:
                    kind = ("warn" if (i == 1 and m.get("no_decision")) else
                            (m.get("status_kind", "info") if i == 2 else verdict_kind(txt)))
                    cells.append(badge(txt, kind))
                elif i in pnlc:
                    cells.append(money_html_text(txt))
                else:
                    cells.append(esc(txt))
            rows.append(cells)
        note = ('<p class="mut small">«Beklenen Net Getiri» işlem öncesi model tahminidir; gerçekleşen sonuç '
                'değildir. «Net K/Z» sütunu açık pozisyonda anlık, kapanmışta SON KAPANAN işlemin net '
                'sonucudur; işlem açılmamış adaylarda «—» gösterilir. «KARAR VERİSİ YOK» satırları '
                'defterde AÇIK olan fakat son coin-head seçkisinde yer almayan pozisyonlardır.</p>')
        return table(chp["columns"], rows, num_cols=num,
                     empty="coin head kararı yok (coin_heads.json)", cls=HEADS_TABLE_CLS) + note

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
        """Öğrenme ekranı — YALNIZ SUNUM. Ağırlık matematiği ve öğrenme algoritması burada
        HESAPLANMAZ; `learning.json` okunur ve operatör için okunur biçimde gösterilir."""
        ln = state.get("learning") or {}
        if not ln:
            return _page("Öğrenme", '<div class="card mut">learning.json yok</div>', "/learning")
        pv = _view()["portfolio"]
        lessons = [x for x in (ln.get("lessons") or []) if isinstance(x, dict)]
        # ZAMAN PENCERESİ — üst kartların TAMAMI «tüm zaman» sayaçlarından okunur.
        # `LearningState` (learn: `learning.py`) sözleşmesi: `n_trades` her kapanışta artar,
        # `n_wins` yalnız `pnl > 0` olduğunda artar, `lessons` ise `[-200:]` ile BUDANIR.
        # Kazanan/kaybeden daha önce `lessons`'tan sayılıyordu → 200. işlemden sonra "toplam
        # işlem tüm zaman, kazanan son 200" gibi kalıcı bir tutarsızlık oluşuyordu (commit'in
        # kendi fixture'ında n_trades=5 / n_wins=2 iken panel 1/1 · %50 gösteriyordu).
        n, n_wins, n_notwin, wr, bad = _learning_counters(ln)
        # `n_losses`/`n_breakeven` learning state'te YOKTUR; `n_trades - n_wins` başa baş
        # işlemleri de içerir. Bu yüzden «Kaybeden» diye SAYILMAZ — kart açıkça birleşik yazılır.
        # Kazanma oranı = `n_wins / n_trades` — öğrenme motorunun KENDİ tanımı
        # (`LearnerV1.snapshot()`: `100 * n_wins / n_trades`), başa baş DAHİL paydada.
        # Bozuk/çelişkili sayaçlar (negatif, n_wins > n_trades) SESSİZCE KIRPILMAZ — dört kart da
        # «Veri yok» olur ve neden altyazıda yazılır (negatif «Kazanmayan» / %100 üstü oran YOK).
        warn = (" · ⚠ " + bad) if bad else ""
        # sum_r bozuk/NaN/±Inf ise `float()` ValueError → HTTP 500 veriyordu; sonlu değilse Veri yok.
        sum_r = finite_float_or_none(ln.get("sum_r"))
        avg_r = (sum_r / n) if (sum_r is not None and n) else None

        def _r(x, nd=3):
            v = finite_float_or_none(x)
            return "Veri yok" if v is None else f"{v:+.{nd}f}R"

        def _c(x):
            return "Veri yok" if x is None else str(x)

        body = sample_banner(n if n is not None else len(lessons))
        body += ('<div class="grid">'
                 + card("Kapanmış işlem", _c(n), "learning.json → n_trades · TÜM ZAMAN" + warn)
                 + card("Kazanan", _c(n_wins), "learning.json → n_wins · TÜM ZAMAN" + warn)
                 + card("Kazanmayan", _c(n_notwin),
                        "n_trades − n_wins · kaybeden + başa baş BİRLİKTE (ayrı sayaç yok)" + warn)
                 + card("Kazanma oranı", "Veri yok" if wr is None else f"%{wr:.1f}",
                        "n_wins / n_trades · başa baş paydaya DÂHİL" + warn)
                 + card("Toplam R", _r(sum_r), "sum_r — risk katı cinsinden sonuç · TÜM ZAMAN")
                 + card("Ortalama R", _r(avg_r), "toplam R / n_trades · TÜM ZAMAN")
                 + card("Net gerçekleşen K/Z", money_html(pv.realized_total).replace("<td", "<span").replace("</td>", "</span>"),
                        "defterden (kanonik) — ücret + funding dahil")
                 + card("Son güncelleme", f'<span title="{esc(ln.get("updated_at") or "")}">{esc(fmt_utc(ln.get("updated_at")))}</span>')
                 + "</div>")

        if ln.get("weights"):
            body += ("<h2>Özellik ağırlıkları</h2>" + weight_table(ln["weights"], "Özellik")
                     + '<p class="mut small">Bu ağırlık TEK BAŞINA işlem kararı değildir; karar '
                       'motoru kapılar, risk bütçesi ve veto katmanlarıyla birlikte çalışır.</p>')
        if ln.get("agent_weights"):
            body += ("<h2>Ajan ağırlıkları</h2>" + weight_table(ln["agent_weights"], "Ajan")
                     + '<p class="mut small">Bu ağırlık TEK BAŞINA işlem kararı değildir.</p>')
        if ln.get("agent_hits"):
            rows = []
            for a, h in ln["agent_hits"].items():
                if not (isinstance(h, (list, tuple)) and len(h) == 2):
                    continue
                hit, tot = int(h[0] or 0), int(h[1] or 0)
                # 0/0 → «%0» YANILTICIDIR (isabetsiz değil, ÖLÇÜLMEMİŞ). «Veri yok» yazılır.
                rate = f"%{hit / tot * 100:.0f}" if tot > 0 else '<span class="mut">Veri yok</span>'
                rows.append([esc(a), str(hit), str(tot), rate])
            body += ("<h2>Ajan isabet</h2>"
                     + table(["Ajan", "Doğru", "Toplam", "İsabet"], rows, num_cols={1, 2, 3})
                     + '<p class="mut small">Hiç ölçüm yoksa (0/0) oran «Veri yok» yazılır; «%0» '
                       'gösterilmez çünkü bu ajanın yanıldığı anlamına gelmez.</p>')
        for key, title in (("setup_stats", "Setup istatistikleri"), ("symbol_stats", "Sembol istatistikleri"),
                           ("exit_stats", "Çıkış nedenleri")):
            if ln.get(key):
                body += f"<h2>{title}</h2>" + render_any(ln[key])
        body += retention_block(ln)
        body += calibration_block(ln)
        body += quality_block(ln, win_rate=(None if wr is None else wr / 100.0),
                              expectancy_r=avg_r, counters_bad=bad)
        if lessons:
            # `lessons` YALNIZ bu tablo içindir — üst kartlar bu listeden HESAPLANMAZ.
            ret = ln.get("lesson_retention") if isinstance(ln.get("lesson_retention"), dict) else {}
            hot_w = ret.get("hot_window")
            arch = ret.get("archived_lessons")
            # 200 SAKLAMA SINIRI DEĞİLDİR: yalnız ekranda/sıcak dosyada tutulan penceredir.
            win_txt = (f"Ekranda son {len(lessons)} ders gösteriliyor"
                       + (f" (sıcak pencere {hot_w})." if hot_w else "."))
            arch_txt = (f" Ömür boyu ayrıntılı dersler kayıpsız arşivleniyor "
                        f"(arşivde {arch} ders). Retrieval kapsamı: "
                        f"{', '.join(str(x) for x in (ret.get('retrieval_scopes') or ['HOT']))}."
                        if isinstance(arch, int) else
                        " Ders arşivi durumu bilinmiyor — arşivsiz budama YAPILMAZ.")
            body += (f"<h2>Dersler</h2><p class=\"mut small\">{esc(win_txt)}{esc(arch_txt)} "
                     f"Üstteki özet kartları TÜM ZAMAN sayaçlarındandır.</p>"
                     + observation_block(lessons) + lessons_table(lessons[-30:][::-1]))
        # KARA LİSTE — geçersiz miras anahtarları (`-|LONG` gibi) AÇIKÇA işaretlenir.
        # Bunlar "kurulum ölçülemedi" durumunun bir kurulum ADI gibi kaydedilmesinden doğdu;
        # silinmezler, hiçbir kararı ENGELLEYEMEZLER ve burada dürüstçe görünürler.
        if ln.get("blacklist") or ln.get("setup_stats"):
            from ..learning import LEGACY_INVALID_SETUP_KEY, legacy_invalid_setup_keys
            bad = {r["key"] for r in legacy_invalid_setup_keys(ln.get("setup_stats"),
                                                              ln.get("blacklist"))}
            items = []
            for x in (ln.get("blacklist") or []):
                if x in bad:
                    items.append(f"<li>{esc(x)} {badge(LEGACY_INVALID_SETUP_KEY, 'warn')} "
                                 "<span class=\"mut\">kararı engelleyemez</span></li>")
                else:
                    items.append(f"<li>{esc(x)}</li>")
            if items:
                body += "<h2>Kara liste</h2><ul>" + "".join(items) + "</ul>"
            orphan = sorted(bad - set(ln.get("blacklist") or []))
            if orphan:
                body += ('<div class="card mut">Geçersiz miras istatistik anahtarı ('
                         + esc(", ".join(orphan)) + f') — {esc(LEGACY_INVALID_SETUP_KEY)}. '
                         'Geçmiş yeniden yazılmaz; bu anahtarlar hiçbir kararı '
                         'engelleyemez ve ileriye dönük olarak artık üretilmezler.</div>')
        body += (_chain_block() + _position_mgmt_block() + _exit_block() + _entry_block()
                 + _mtf_block())
        return _page("Öğrenme", body, "/learning")

    def _mtf_block() -> str:
        """H — Çok Zaman Dilimli Likidite Teyidi. SALT OKUNUR, SHADOW.

        Hiçbir satır «AL/SAT», «garantili sinyal» ya da «kârlı strateji» dili KULLANMAZ.
        Ölçülemeyen değer `0` gösterilmez; `—` ya da durum kodu yazılır.
        """
        ev = state.get("mtf_eval")
        if not isinstance(ev, dict) or not ev:
            return ("<h2>H — Çok Zaman Dilimli Likidite Teyidi</h2>"
                    '<div class="card mut">mtf_eval.json yok — worker bu sürümle henüz tam '
                    "bir tur tamamlamadı. Bu görünüm salt okunurdur ve aktif karara etkisi "
                    "yoktur.</div>")

        def _i(d, k):
            v = finite_float_or_none((d or {}).get(k))
            return int(v) if v is not None else None

        def _n(x):
            return "—" if x is None else str(x)

        mode = str(ev.get("mode") or "SHADOW")
        st_ = str(ev.get("state") or "—")
        n_snap, n_link = _i(ev, "n_h_snapshots"), _i(ev, "n_h_links")
        n_close, n_pre = _i(ev, "n_h_linked_closes"), _i(ev, "n_pre_h_excluded")
        out = ("<h2>H — Çok Zaman Dilimli Likidite Teyidi</h2>"
               '<div class="card mut">Bir eğitim videosundan alınan hipotez: üst zaman dilimi '
               "bağlam/likidite/hedef, alt zaman dilimi teyit verir. <b>Video kârlılık kanıtı "
               "değildir</b>; burada yalnız yanlışlanabilir bir araştırma hipotezi olarak "
               "ölçülür. Bu katman hiçbir emir üretmez; sıralamayı, yönü, miktarı, kaldıracı, "
               "stop/TP değerlerini ya da RiskEngine sonucunu DEĞİŞTİRMEZ.</div>")
        out += ('<div class="grid">'
                + card("Mod", badge(esc(mode), "ok" if mode == "SHADOW" else "warn"),
                       "yalnız SHADOW; PAPER_BOUNDED/ACTIVE config ile açılamaz")
                + card("Uygulanan karar", badge("0", "ok"),
                       "applied=false — aktif karar DEĞİŞMEDİ")
                + card("Otomatik terfi",
                       badge("KAPALI", "ok") if ev.get("auto_promotion") is False
                       else badge("AÇIK", "warn"), "terfi yalnız manuel operatör onayıyla")
                + card("Durum", badge(esc(st_), "warn" if st_.startswith("PENDING") else "info"),
                       "kanıt birikimi")
                + card("H snapshot", _n(n_snap), "H bağlamı taşıyan aday kaydı")
                + card("H bağı", _n(n_link), "gerçek trade_id ile bağlanmış H adayı")
                + card("H-tam kapanış", _n(n_close), "terfi kapılarına sayılan tek küme")
                + card("Ön-H dışlanan", _n(n_pre), "H'den ÖNCE açıldı — kanıt SAYILMAZ")
                + "</div>")
        out += ('<div class="card mut">Kimlik: politika '
                f'<code>{esc(ev.get("policy_version") or "—")}</code>, config '
                f'<code>{esc(str(ev.get("config_id") or "—")[:16])}</code>, kod '
                f'<code>{esc(str(ev.get("code_sha") or "—")[:12])}</code>. '
                "F00030 ve H yayımından önce açılmış her pozisyon terfi kanıtından "
                "<b>açıkça dışlanır</b>; eski kayıtlara H alanı geriye dönük yazılmaz.</div>")

        # --- çift durumları ---------------------------------------------------------------
        ps = ev.get("pair_status") if isinstance(ev.get("pair_status"), dict) else {}
        if not ps:
            try:
                from ..learn.multitimeframe_context import ALL_PAIRS, pair_status
                ps = {p_: pair_status(p_) for p_ in ALL_PAIRS}
            except Exception:  # noqa: BLE001
                ps = {}
        if ps:
            rows = []
            for name, d in ps.items():
                stt = str((d or {}).get("state") or "—")
                fr = (d or {}).get("frames")
                rows.append([esc(name), badge(esc(stt), "ok" if stt == "SUPPORTED" else "warn"),
                             esc(" → ".join(fr) if fr else "—"),
                             _n(_i(d, "new_provider_calls")),
                             esc(str((d or {}).get("reason") or "")[:200])])
            out += ("<h3>Zaman dilimi çiftleri</h3>" + table(
                ["Çift", "Durum", "Kareler", "Yeni API isteği", "Gerekçe (ölçülmüş)"],
                rows, num_cols={3}, empty="çift yok"))

        # --- varyant sonuçları -------------------------------------------------------------
        vs = ev.get("variants") if isinstance(ev.get("variants"), dict) else {}
        gates = ev.get("promotion_gates") if isinstance(ev.get("promotion_gates"), dict) else {}
        if vs:
            rows = []
            for name, r in sorted(vs.items()):
                g = gates.get(name) or {}
                rr = (r or {}).get("structural_rr") or {}
                rows.append([
                    esc(name), _n(_i(r, "n")),
                    _n(_i(r, "allow_count")), _n(_i(r, "veto_count")),
                    _n(_i(r, "abstain_count")),
                    fmt((r or {}).get("coverage"), 3), fmt((r or {}).get("abstain_rate"), 3),
                    _n(_i(r, "blocked_losers")), _n(_i(r, "blocked_winners")),
                    fmt((r or {}).get("avoided_loss_r"), 3),
                    fmt((r or {}).get("missed_gain_r"), 3),
                    fmt((r or {}).get("allowed_net_r"), 3),
                    fmt((r or {}).get("expectancy_delta_r"), 4),
                    fmt((r or {}).get("max_drawdown_r"), 3),
                    fmt((r or {}).get("cvar5_r"), 3),
                    fmt(rr.get("mean"), 2),
                    f'{_n(_i(g, "n_passed"))}/{_n(_i(g, "n_total"))}',
                ])
            out += ("<h3>Varyant bazlı karşı-olgusal sonuç (yalnız H-tam kapanışlar)</h3>"
                    + table(["Varyant", "n", "ALLOW", "VETO", "ABSTAIN", "Kapsam",
                             "Çekimser oran", "Eng. kaybeden", "Eng. kazanan", "Kaçınılan R",
                             "Kaçırılan R", "İzin verilen net R", "Δ beklenti", "maxDD R",
                             "CVaR5 R", "Yapısal R:R", "Kapı"],
                            rows, num_cols=set(range(1, 17)), empty="varyant sonucu yok"))
            crows = [[esc(name),
                      fmt((r or {}).get("fee_drag_r"), 4),
                      fmt((r or {}).get("funding_drag_r"), 4),
                      fmt((r or {}).get("slippage_drag_r"), 4),
                      fmt((r or {}).get("total_measured_friction_r"), 4),
                      esc(str((r or {}).get("cost_measured_counts") or "—"))]
                     for name, r in sorted(vs.items())]
            out += ("<h3>Maliyet dökümü (bileşenler AYRI — çift sayım yok)</h3>" + table(
                ["Varyant", "Komisyon R", "Funding R", "Kayma R", "Ölçülen toplam R",
                 "Ölçülen bileşen sayısı"], crows, num_cols={1, 2, 3, 4},
                empty="maliyet ölçülmedi"))
            for key, title in (("reason_distribution", "Gerekçe kodu dağılımı"),
                               ("htf_interaction_distribution", "Üst dilim etkileşim dağılımı"),
                               ("ltf_confirmation_distribution", "Alt dilim teyit dağılımı")):
                drows = []
                for name, r in sorted(vs.items()):
                    for k, v in list(((r or {}).get(key) or {}).items())[:12]:
                        drows.append([esc(name), esc(k), _n(int(v))])
                if drows:
                    out += (f"<h3>{title}</h3>" + table(["Varyant", "Kod", "Adet"], drows,
                                                        num_cols={2}, empty="veri yok"))
            wrows = []
            for name, r in sorted(vs.items()):
                ci = (r or {}).get("delta_ci") or {}
                wf = (r or {}).get("walk_forward") or {}
                wrows.append([esc(name),
                              esc(f'[{ci.get("lo")}, {ci.get("hi")}]'),
                              esc(str(ci.get("state") or "—")),
                              badge("SIFIRI DIŞLIYOR", "ok") if ci.get("excludes_zero")
                              else badge("DIŞLAMIYOR", "warn"),
                              esc(str(wf.get("state") or "—")),
                              f'{_n(_i(wf, "n_positive"))}/{_n(_i(wf, "k"))}'])
            out += ("<h3>Güven aralığı ve ileriye dönük tutarlılık</h3>" + table(
                ["Varyant", "Bootstrap GA", "Durum", "Sıfır", "Walk-forward", "Pozitif kat"],
                wrows, num_cols={5}, empty="veri yok"))

        # --- terfi kapıları -----------------------------------------------------------------
        for name, g in sorted(gates.items()):
            gl = (g or {}).get("gates")
            if not isinstance(gl, list) or not gl:
                continue
            rows = []
            for x in gl:
                if not isinstance(x, dict):
                    continue
                if str(x.get("status") or "EVALUATED") == "NOT_EVALUABLE_LOW_SAMPLE":
                    b = badge("DEĞERLENDİRİLEMEZ", "warn")
                elif x.get("passed"):
                    b = badge("GEÇTİ", "ok")
                else:
                    b = badge("DÜŞTÜ", "warn")
                rows.append([esc(x.get("code")), b, esc(x.get("detail"))])
            out += (f"<h3>Terfi kapıları — {esc(name)}</h3>"
                    + table(["Kapı", "Durum", "Ayrıntı"], rows, empty="kapı yok")
                    + '<div class="card mut">Örneklem ön koşulu düşerken bağımlı başarım '
                      "kapıları «GEÇTİ» değil, <b>DEĞERLENDİRİLEMEZ (düşük örneklem)</b> "
                      "olarak raporlanır. En az 50 H-tam bağlı kapanış ve 30 takvim günü "
                      "gerekir; otomatik terfi hiçbir koşulda yapılmaz.</div>")

        iso = ev.get("isolation") if isinstance(ev.get("isolation"), dict) else {}
        if iso:
            out += ('<h3>İzolasyon kanıtı</h3><div class="grid">'
                    + card("Doğrulandı",
                           badge("EVET", "ok") if iso.get("verified") else badge("HAYIR", "warn"),
                           esc(str(iso.get("detail") or "")[:160]))
                    + card("Deftere yazım",
                           badge("YOK", "ok") if iso.get("writes_ledger") is False
                           else badge("VAR", "warn"), "H defter yazmaz")
                    + card("Gateway erişimi",
                           badge("YOK", "ok") if iso.get("touches_gateway") is False
                           else badge("VAR", "warn"), "H gateway'e dokunmaz")
                    + card("Sıralama etkisi",
                           badge("YOK", "ok") if iso.get("changes_ranking") is False
                           else badge("VAR", "warn"), "coin-head çıktısı değişmez")
                    + "</div>")
        return out

    def _weekly_block(ev: dict) -> str:
        """Haftalık yapı + bağlamsal formasyon + F/G aileleri — SALT OKUNUR, SHADOW.

        Hiçbir mum formasyonu AL/SAT talimatı olarak gösterilmez; her satır bağlam,
        teyit durumu ve veri kalitesiyle birlikte verilir.
        """
        w = ev.get("weekly_context") if isinstance(ev.get("weekly_context"), dict) else None
        if not w:
            return ""
        if w.get("enabled") is False:
            return ('<h3>Haftalık bağlam</h3><div class="card mut">Kapalı '
                    f'({esc(w.get("reason") or "—")}). Yalnız SHADOW gözlem katmanıdır; '
                    'aktif karara etkisi yok.</div>')
        if w.get("error"):
            return ('<h3>Haftalık bağlam</h3><div class="card mut">Rapor üretilemedi: '
                    f'{esc(w.get("error"))} — aktif karara etkisi yok.</div>')

        def _i(d, k):
            v = finite_float_or_none((d or {}).get(k))
            return int(v) if v is not None else None

        def _n(x):
            return "—" if x is None else str(x)

        verdict = str(w.get("verdict") or "INSUFFICIENT_ENTRY_SAMPLE")
        out = ('<h3>Haftalık yapı ve bağlamsal fiyat hareketi (F / G)</h3>'
               '<p class="mut small">Yalnız SHADOW gözlem — aktif karara etkisi yok. '
               'Mum formasyonları <b>bağlamsal</b>dır: şekil tek başına yön iddiası '
               'taşımaz, AL/SAT talimatı değildir.</p><div class="grid">'
               + card("Aileler", str(len(w.get("families") or [])),
                      esc(", ".join(str(x).split("_")[0] for x in (w.get("families") or []))))
               + card("Yapılandırma varyantı", _n(_i(w, "n_variants")),
                      "hepsi ayrı ölçülür, seçilmez")
               + card("Terfiye sayılan kapanış", _n(_i(w, "n_linked")),
                      f"kapı {GATE_MIN_LINKED}")
               + card("Uygulanan filtre", str(int(w.get("applied_total") or 0)),
                      "SHADOW'da daima 0")
               + card("Terfi durumu",
                      badge("YETERSİZ ÖRNEK", "warn")
                      if verdict != "ELIGIBLE_FOR_PAPER_BOUNDED" else badge("KAPILAR GEÇTİ", "ok"),
                      "otomatik terfi KAPALI")
               + '</div>')
        variants = w.get("variants") if isinstance(w.get("variants"), dict) else {}
        rows = []
        for vname, v in sorted(variants.items()):
            if not isinstance(v, dict):
                continue
            for fam, rep in sorted((v.get("families") or {}).items()):
                if not isinstance(rep, dict):
                    continue
                b = rep.get("baseline") or {}
                c = rep.get("counterfactual") or {}
                ci = rep.get("delta_ci") or {}
                wf = rep.get("walk_forward_folds") or {}
                rows.append([
                    esc(vname), esc(fam), _n(_i(rep, "n_evaluated")),
                    _n(_i(rep, "n_allow")), _n(_i(rep, "n_block")), _n(_i(rep, "n_abstain")),
                    pct(rep.get("weekly_coverage")), pct(rep.get("abstain_rate")),
                    _n(_i(rep, "n_blocked_loser")), _n(_i(rep, "n_blocked_winner")),
                    fmt(rep.get("avoided_loss_r"), 3), fmt(rep.get("missed_gain_r"), 3),
                    fmt(rep.get("delta_expectancy_r"), 4),
                    fmt(b.get("profit_factor"), 3), fmt(c.get("max_drawdown_r"), 3),
                    fmt(c.get("tail_loss_r_cvar5"), 3),
                    (f"[{fmt(ci.get('lo'), 3)}, {fmt(ci.get('hi'), 3)}]"
                     if ci.get("state") == "ok" else esc(str(ci.get("state") or "—"))),
                    esc(str(wf.get("state") or "—")),
                    f'{_n(_i(rep, "gates_passed"))}/{_n(_i(rep, "gates_total"))}',
                ])
        out += table(["Varyant", "Aile", "n", "ALLOW", "BLOCK", "ABSTAIN", "Haftalık kapsam",
                      "Kararsızlık", "Eng. kaybeden", "Kaç. kazanan", "Kaçınılan R",
                      "Kaçırılan R", "Δ beklenti", "PF", "maxDD R", "CVaR5 R",
                      "Güven aralığı", "Walk-forward", "Kapı"], rows,
                     num_cols={2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
                     empty="varyant sonucu yok")
        base = (variants.get("base") or {})
        for fam, rep in sorted((base.get("families") or {}).items()):
            gl = (rep or {}).get("gates")
            if not isinstance(gl, list) or not gl:
                continue
            out += (f"<h4>Terfi kapıları — {esc(fam)} (varyant base)</h4>" + table(
                ["Kapı", "Durum", "Ayrıntı"],
                [[esc(g.get("code")),
                  badge("GEÇTİ", "ok") if g.get("passed") else badge("DÜŞTÜ", "warn"),
                  esc(g.get("detail"))] for g in gl if isinstance(g, dict)],
                empty="kapı yok"))
        eg = w.get("extra_gates")
        if isinstance(eg, dict) and eg:
            out += ('<div class="card mut">V2 ailelerine özgü EK kapılar (V1 eşikleri '
                    'gevşetilmedi): ' + esc(", ".join(f"{k}={v}" for k, v in sorted(eg.items())))
                    + '</div>')
        return out

    def _entry_block() -> str:
        """Giriş seçiciliği paneli — beş challenger ailesi, SALT OKUNUR.

        Ölçülemeyen değer `0` gösterilmez: `—` ya da durum kodu yazılır. `LEGACY_MEMORY`
        kapanışları AYRI sayılır ve terfi kanıtı olmadığı ekranda açıkça yazar.
        """
        ev = state.get("entry_selectivity")
        if not isinstance(ev, dict) or not ev:
            return ('<h2>Giriş seçiciliği</h2><div class="card mut">entry_selectivity.json yok — '
                    'worker bu sürümle henüz tam bir tur tamamlamadı. Bu görünüm salt '
                    'okunurdur.</div>')

        def _i(d, key):
            v = finite_float_or_none((d or {}).get(key))
            return int(v) if v is not None else None

        def _n(x):
            return "—" if x is None else str(x)

        mode = str(ev.get("entry_mode") or "SHADOW")
        verdict = str(ev.get("verdict") or "INSUFFICIENT_ENTRY_SAMPLE")
        store = ev.get("snapshot_store") if isinstance(ev.get("snapshot_store"), dict) else {}
        cyc = ev.get("snapshot_cycle") if isinstance(ev.get("snapshot_cycle"), dict) else {}
        n_linked, n_legacy = _i(ev, "n_linked"), _i(ev, "n_legacy_memory")
        leak = ev.get("leakage") if isinstance(ev.get("leakage"), dict) else {}
        out = ('<h2>Giriş seçiciliği</h2>'
               '<p class="mut small">Beş challenger ailesi AYRI AYRI ölçülür; birleşik bir '
               'süper filtre bilinçli olarak üretilmez — birleştirmek hangi gerekçenin işe '
               'yaradığını ölçülemez kılar. Eksik veri VETO gerekçesi değildir.</p>'
               '<div class="grid">'
               + card("Giriş modu",
                      badge("SHADOW", "warn") if mode == "SHADOW" else badge(esc(mode), "ok"),
                      "karşı-olgusal karar, UYGULANMAZ" if mode == "SHADOW" else "uygulanır")
               + card("Uygulanan filtre", str(int(ev.get("applied_total") or 0)),
                      "SHADOW'da daima 0")
               + card("Terfiye sayılan kapanış", _n(n_linked),
                      f"kapı {GATE_MIN_LINKED} · yalnız LINKED snapshot")
               + card("Yalnız gözlem (LEGACY)", _n(n_legacy),
                      "terfi kanıtı SAYILMAZ")
               + card("Snapshot deposu",
                      f"{_n(_i(store, 'snapshots'))} / {_n(_i(store, 'links'))}",
                      "aday snapshot / açılışa bağlanan")
               + card("Bu turda yazılan", _n(_i(cyc, "written")),
                      f"{_n(_i(cyc, 'candidates'))} aday · {_n(_i(cyc, 'errors'))} hata")
               + card("Gözlem süresi (gün)", fmt(ev.get("observation_days"), 2),
                      f"kapı {GATE_MIN_DAYS}")
               + card("Sızıntı denetimi",
                      badge("TEMİZ", "ok") if leak.get("clean")
                      else badge(esc(str(leak.get("state") or "ölçülemedi")), "warn"),
                      f"denetlenen {_n(_i(leak, 'checked'))}")
               + card("Terfi durumu",
                      badge("YETERSİZ ÖRNEK", "warn")
                      if verdict != "ELIGIBLE_FOR_PAPER_BOUNDED" else badge("KAPILAR GEÇTİ", "ok"),
                      "otomatik terfi KAPALI")
               + '</div>')
        # KİMLİK: bu raporu hangi politika / config / kod üretti. Kimliksiz kanıt denetlenemez.
        _sha = str(ev.get("code_sha") or "")
        _cfgh = str(ev.get("config_hash") or "")
        out += ('<h3>Politika / config / kod kimliği</h3>' + table(
            ["Alan", "Değer"],
            [["policy_version", esc(ev.get("policy_version") or "—")],
             ["config_id", esc(ev.get("config_id") or "—")],
             ["code_sha", esc(_sha[:12] + "…" if len(_sha) > 12 else (_sha or "—"))],
             ["config_hash", esc(_cfgh[:12] + "…" if len(_cfgh) > 12 else (_cfgh or "—"))],
             ["run_id", esc(ev.get("run_id") or "—")],
             ["rapor üretimi", esc(ev.get("generated_at") or "—")],
             ["şema", esc(ev.get("schema_version") or "—")]],
            empty="kimlik yok"))
        fams = ev.get("families")
        if isinstance(fams, dict) and fams:
            rows = []
            for fam, f in sorted(fams.items()):
                if not isinstance(f, dict):
                    continue
                b = f.get("baseline") or {}
                c = f.get("counterfactual") or {}
                rows.append([
                    esc(fam), _n(_i(f, "n_evaluated")), _n(_i(f, "n_blocked")),
                    _n(_i(f, "n_blocked_loser")), _n(_i(f, "n_blocked_winner")),
                    fmt(f.get("avoided_loss_r"), 3), fmt(f.get("missed_gain_r"), 3),
                    fmt(f.get("discrimination_youden_j"), 4),
                    fmt(b.get("expectancy_r"), 4), fmt(c.get("expectancy_r"), 4),
                    fmt(f.get("delta_expectancy_r"), 4),
                    fmt(c.get("max_drawdown_r"), 3), fmt(c.get("tail_loss_r_cvar5"), 3),
                    f'{_n(_i(f, "gates_passed"))}/{_n(_i(f, "gates_total"))}',
                ])
            out += ("<h3>Aile bazlı karşı-olgusal sonuç (yalnız LINKED kapanışlar)</h3>"
                    + table(["Aile", "n", "Engellenen", "Kaybeden", "Kazanan",
                             "Kaçınılan R", "Kaçırılan R", "Ayrım (J)", "Baseline bekl.",
                             "Karşı-olgusal bekl.", "Δ beklenti", "maxDD R", "CVaR5 R", "Kapı"],
                            rows, num_cols={1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
                            empty="aile sonucu yok"))
        gates = ev.get("promotion_gates")
        if isinstance(gates, dict) and gates:
            for fam, gl in sorted(gates.items()):
                if not isinstance(gl, list) or not gl:
                    continue
                out += (f"<h3>Terfi kapıları — {esc(fam)}</h3>" + table(
                    ["Kapı", "Durum", "Ayrıntı"],
                    [[esc(g.get("code")),
                      badge("GEÇTİ", "ok") if g.get("passed") else badge("DÜŞTÜ", "warn"),
                      esc(g.get("detail"))] for g in gl if isinstance(g, dict)],
                    empty="kapı yok"))
        ra = ev.get("replay_audit") if isinstance(ev.get("replay_audit"), dict) else {}
        if ra:
            srcs = ra.get("sources") if isinstance(ra.get("sources"), dict) else {}
            v = str(ra.get("verdict") or "NO_DATA")
            out += ('<h3>Replay sadakati (geçmiş veriyle karar anı yeniden üretilebilir mi?)</h3>'
                    '<div class="grid">'
                    + card("Hüküm",
                           badge("YENİDEN ÜRETİLEBİLİR", "ok") if v == "REPLAYABLE"
                           else badge(esc(v), "warn"), esc(str(ra.get("reason_tr") or "")[:160]))
                    + card("Sentetik kârlılık", badge("ÜRETİLMEDİ", "ok"),
                           "eksik alan varsayılanla doldurulmaz")
                    + card("Kapanış bağı",
                           f'{_n(_i(ra.get("closes") or {}, "linked_to_decision"))} / '
                           f'{_n(_i(ra.get("closes") or {}, "total"))}',
                           "karar anına bağlanabilen kapanış")
                    + '</div>')
            if srcs:
                out += table(
                    ["Kaynak", "Kayıt", "Tam mı", "Eksik alan", "Tamamen boş alan"],
                    [[esc(k), _n(_i(d, "n_rows")),
                      badge("TAM", "ok") if d.get("complete") else badge("EKSİK", "warn"),
                      str(len(d.get("missing_fields") or [])),
                      esc(", ".join((d.get("completely_empty_fields") or [])[:8]) or "—")]
                     for k, d in sorted(srcs.items()) if isinstance(d, dict)],
                    num_cols={1, 3}, empty="kaynak yok")
            ee_ = ra.get("empty_in_every_source")
            if isinstance(ee_, list) and ee_:
                out += ('<div class="card mut"><b>Her kaynakta boş olan zorunlu alanlar '
                        f'({len(ee_)}):</b> {esc(", ".join(str(x) for x in ee_))}</div>')
        out += _weekly_block(ev)
        trades = ev.get("trades")
        if isinstance(trades, list) and trades:
            fam_ids = sorted({f for t in trades if isinstance(t, dict)
                              for f in (t.get("families") or {})})
            head = (["İşlem", "Sembol", "Yön", "Gerçek R", "Kanıt"]
                    + [f.split("_")[0] for f in fam_ids])
            out += "<h3>İşlem bazlı giriş kararı</h3>" + table(
                head,
                [[esc(t.get("trade_id")), esc(t.get("symbol")), esc(t.get("direction")),
                  fmt(t.get("actual_r"), 3),
                  badge("TERFİ", "ok") if t.get("evidence_grade") == "PROMOTION"
                  else badge("GÖZLEM", "info")]
                 + [((t.get("families") or {}).get(f) or {}).get("decision") or "—"
                    for f in fam_ids]
                 for t in trades[-40:][::-1] if isinstance(t, dict)],
                num_cols={3}, empty="işlem yok")
        return out

    def _exit_block() -> str:
        """Çıkış politikası paneli — champion / challenger karşılaştırması, SALT OKUNUR.

        Boş ya da ölçülemeyen değer `0` gösterilmez: `—` ya da durum kodu yazılır.
        """
        ev = state.get("exit_eval")
        if not isinstance(ev, dict) or not ev:
            return ('<h2>Çıkış politikası</h2><div class="card mut">exit_eval.json yok — '
                    'worker bu sürümle henüz tam bir tur tamamlamadı. Bu görünüm salt okunurdur.</div>')

        def _i(d, key):
            v = finite_float_or_none((d or {}).get(key))
            return int(v) if v is not None else None

        def _n(x):
            return "—" if x is None else str(x)

        mode = str(ev.get("exit_action_mode") or "SHADOW")
        n_complete = _i(ev, "n_path_complete")
        n_missing = _i(ev, "n_no_complete_path")
        verdict = str(ev.get("verdict") or "INSUFFICIENT_EXIT_SAMPLE")
        pm = state.get("position_management") or {}
        pm_n = _i(pm, "n_positions")
        # Yol kapsamı: kaç açık pozisyonun yolu kaydediliyor.
        rows_path = state.tail_jsonl("position_path", 4000)
        covered = len({str(r.get("trade_id")) for r in rows_path if r.get("trade_id")})
        open_ids = {str(p.get("id")) for p in state.futures_positions() if p.get("id")}
        open_covered = len(open_ids & {str(r.get("trade_id")) for r in rows_path})
        out = ('<h2>Çıkış politikası</h2><div class="grid">'
               + card("Çıkış modu",
                      badge("SHADOW", "warn") if mode == "SHADOW" else badge(esc(mode), "ok"),
                      "niyetler kaydedilir, UYGULANMAZ" if mode == "SHADOW" else "uygulanır")
               + card("Uygulanan aksiyon", str(int(ev.get("applied_total") or 0)),
                      "SHADOW'da daima 0")
               + card("Açık pozisyon yol kapsamı",
                      f"{open_covered} / {_n(pm_n)}" if pm_n is not None else _n(None),
                      f"toplam {covered} işlemin yolu kayıtlı")
               + card("Yol tam kapanış", _n(n_complete),
                      f"{_n(n_missing)} işlemde NO_COMPLETE_PATH")
               + card("Gözlem süresi (gün)", fmt(ev.get("observation_days"), 2),
                      f"kapı {ev.get('policy_version') or '—'}")
               + card("Terfi durumu",
                      badge("YETERSİZ ÖRNEK", "warn") if verdict != "ELIGIBLE_FOR_PAPER_BOUNDED"
                      else badge("KAPILAR GEÇTİ", "ok"),
                      "otomatik terfi KAPALI")
               + '</div>')
        gates = ev.get("promotion_gates")
        if isinstance(gates, list) and gates:
            out += "<h3>Terfi kapıları</h3>" + table(
                ["Kapı", "Durum", "Ayrıntı"],
                [[esc(g.get("code")), badge("GEÇTİ", "ok") if g.get("passed") else badge("DÜŞTÜ", "warn"),
                  esc(g.get("detail"))] for g in gates if isinstance(g, dict)],
                empty="kapı yok")
        by = ev.get("by_policy")
        if isinstance(by, dict) and by:
            out += "<h3>Politika karşılaştırması (aynı gerçek fiyat yolu)</h3>" + table(
                ["Politika", "n", "Beklenti R", "Δ champion", "PF", "maxDD R", "CVaR5 R",
                 "Payoff", "Kazanma", "Çıkış maliyeti R", "Δ maliyet"],
                [[esc(p), _n(_i(s, "n")), fmt(s.get("expectancy_r"), 4),
                  (fmt(s.get("delta_expectancy_r"), 4) if p != "champion" else "—"),
                  (fmt(s.get("profit_factor"), 3) if s.get("profit_factor") is not None
                   else badge(esc(s.get("profit_factor_state") or "—"), "info")),
                  fmt(s.get("max_drawdown_r"), 3), fmt(s.get("tail_loss_r_cvar5"), 3),
                  fmt(s.get("payoff_ratio"), 3), pct(s.get("win_rate")),
                  fmt(s.get("total_exit_cost_r"), 4),
                  (fmt(s.get("fee_delta_r"), 4) if p != "champion" else "—")]
                 for p, s in sorted(by.items()) if isinstance(s, dict)],
                num_cols={1, 2, 3, 5, 6, 7, 8, 9, 10}, empty="politika sonucu yok")
        trades = ev.get("trades")
        if isinstance(trades, list) and trades:
            out += "<h3>İşlem bazlı çıkış sonucu</h3>" + table(
                ["İşlem", "Sembol", "Gerçek R", "Yol", "champion R", "A R", "B R", "C R"],
                [[esc(t.get("trade_id")), esc(t.get("symbol")), fmt(t.get("actual_r"), 3),
                  (badge("TAM", "ok") if t.get("status") == "OK"
                   else badge("YOL YOK", "info")),
                  fmt(((t.get("results") or {}).get("champion") or {}).get("net_r"), 3),
                  fmt(((t.get("results") or {}).get("challenger_a_profit_lock") or {}).get("net_r"), 3),
                  fmt(((t.get("results") or {}).get("challenger_b_giveback_reduce") or {}).get("net_r"), 3),
                  fmt(((t.get("results") or {}).get("challenger_c_time_carry") or {}).get("net_r"), 3)]
                 for t in trades[::-1] if isinstance(t, dict)],
                num_cols={2, 4, 5, 6, 7}, empty="kapanış yok")
        out += ('<p class="mut small">Bu tablo bir kârlılık iddiası DEĞİLDİR. Challenger '
                'sonuçları aynı gerçek fiyat yolu üzerinde karşı-olgusal olarak hesaplanır; '
                'hiçbiri uygulanmamıştır.</p>')
        return out

    def _chain_block() -> str:
        """Kapanış zinciri bütünlüğü paneli — defterle uzlaştırılmış SAYILAR.

        Panel ders dosyasındaki satır sayısını «öğrenilmiş işlem» saymaz: kanonik kaynak
        defterdir ve zincir durumu `learning_chain.json` içinde işlem başına yazılıdır.
        """
        ch = state.get("learning_chain")
        if not isinstance(ch, dict) or not ch:
            return ('<h2>Kapanış zinciri</h2><div class="card mut">learning_chain.json yok — '
                    'worker bu sürümle henüz tam bir tur tamamlamadı. Bu görünüm salt okunurdur.</div>')
        age = state.file_age("learning_chain")

        def _i(key, default=None):
            """Şema bozuksa 500 YOK: sayı değilse `default`. Panel dayanıklıdır, iddialı değil."""
            v = finite_float_or_none(ch.get(key))
            return int(v) if v is not None else default

        n = _i("canonical_final_closes", 0)
        miss_o, miss_l = _i("missing_outcome"), _i("missing_lesson")
        dup = _i("duplicate_lessons")
        # Ölçülemeyen bir alan «sorun yok» sayılmaz: hepsi bilinmiyorsa durum BİLİNMİYOR olur.
        known = [x for x in (miss_o, miss_l, dup) if x is not None]
        ok = bool(known) and all(x == 0 for x in known) and len(known) == 3
        def _n(x):
            return "—" if x is None else str(x)

        chain_badge = (badge("EKSİKSİZ", "ok") if ok else
                       (badge("EKSİK", "warn") if known else badge("BİLİNMİYOR", "info")))
        out = ('<h2>Kapanış zinciri</h2><div class="grid">'
               + card("Kanonik final kapanış", _n(n), "defter geçmişi (kısmi TP hariç)")
               + card("Zincir", chain_badge,
                      f"eksik outcome {_n(miss_o)} · eksik ders {_n(miss_l)} · duplicate {_n(dup)}")
               + card("Outcome", _n(_i("outcomes")), f"/ {_n(n)} kapanış")
               + card("Ders", _n(_i("lessons")), f"/ {_n(n)} kapanış")
               + card("Giriş kararına bağlı", _n(_i("entry_linked")),
                      f"{_n(_i('legacy_unlinked'))} LEGACY_UNLINKED (kimlik uydurulmaz)")
               + card("Son öğrenilen", esc(ch.get("last_learned_trade") or "—"),
                      fmt_utc(ch.get("last_learned_at")))
               + card("Öğrenme etkisi", esc(ch.get("influence_mode") or "OFF"),
                      f"bu turda applied={ch.get('influence_applied', 0)}")
               + card("Özet yaşı", age_text(age) + " önce" if age is not None else "bilinmiyor",
                      esc(str(ch.get("code_sha") or "code_sha yok")[:12]))
               + '</div>')
        qn, gap = _i("quant_sample_count"), _i("quant_sample_gap")
        qage = state.file_age("quant_eval")
        if qn is None:
            qbadge, qsub = badge("RAPOR YOK", "info"), "offline quant raporu üretilmedi"
        elif gap:
            qbadge = badge(f"ESKİ (n={qn}/{n})", "warn")
            qsub = f"{gap} kapanış rapora GİRMEDİ — offline rapor yeniden üretilmeli"
        else:
            qbadge, qsub = badge(f"GÜNCEL (n={qn})", "ok"), "kanonik kapanış sayısıyla eşleşiyor"
        out += ('<div class="grid">'
                + card("Quant örneklemi", qbadge, qsub)
                + card("Quant rapor yaşı",
                       age_text(qage) + " önce" if qage is not None else "bilinmiyor",
                       esc(ch.get("quant_run_id") or "—"))
                + '</div>')
        lr = ch.get("last_reconcile")
        lr = lr if isinstance(lr, dict) else {}
        if lr.get("ran"):
            out += (f'<p class="mut small">Son uzlaştırma: +{esc(lr.get("outcomes_added", 0))} outcome, '
                    f'+{esc(lr.get("lessons_added", 0))} ders. Defter DEĞİŞTİRİLMEDİ.</p>')
        orph = ch.get("orphan_lessons")
        orph = orph if isinstance(orph, list) else []
        if orph:
            out += (f'<p class="mut small">⚠ Deftere karşılık gelmeyen ders: '
                    f'{esc(", ".join(str(x) for x in orph[:10]))}</p>')
        raw_rows = ch.get("rows")
        rows = [r for r in raw_rows if isinstance(r, dict)] if isinstance(raw_rows, list) else []
        if rows:
            out += table(
                ["İşlem", "Sembol", "Yön", "Kapanış", "Çıkış", "Net", "R", "MFE_R", "MAE_R",
                 "fee_R", "funding_R", "Zincir", "Bağlantı"],
                [[esc(r.get("trade_id")), esc(r.get("symbol")), esc(r.get("side")),
                  fmt_utc(r.get("closed_at")), esc(r.get("exit_reason")),
                  pnl_cell(r.get("net_pnl")), fmt(r.get("r_multiple"), 3),
                  fmt(r.get("mfe_r"), 3), fmt(r.get("mae_r"), 3),
                  fmt(r.get("fee_drag_r"), 4), fmt(r.get("funding_drag_r"), 4),
                  badge("TAM", "ok") if r.get("chain_state") == "COMPLETE"
                  else badge(esc(r.get("chain_state")), "warn"),
                  badge("BAĞLI", "ok") if r.get("link_status") == "LINKED"
                  else badge("LEGACY", "info")]
                 for r in rows[::-1]],
                num_cols={5, 6, 7, 8, 9, 10}, empty="kapanış yok")
        return out

    def _position_mgmt_block() -> str:
        """Açık pozisyon yönetim gözlemi — ADVISORY_ONLY ve UNKNOWN AÇIKÇA gösterilir."""
        pm = state.get("position_management")
        if not isinstance(pm, dict) or not pm:
            return ""
        # FAIL-CLOSED: `executable` açıkça True değilse öneri TAVSİYEDİR. Bozuk/eksik şema
        # kazara "uygulanabilir" göstermemeli.
        advisory = pm.get("executable") is not True
        by_act = pm.get("by_action")
        by_act = by_act if isinstance(by_act, dict) else {}
        out = ('<h2>Açık pozisyon yönetimi</h2><div class="grid">'
               + card("Öneri modu",
                      badge("YALNIZ TAVSİYE", "warn") if advisory else badge("UYGULANABİLİR", "ok"),
                      "motor bu önerileri UYGULAMAZ" if advisory else "öneriler uygulanır")
               + card("Pozisyon", esc(pm.get("n_positions", "—")),
                      " · ".join(f"{esc(k)} {esc(v)}" for k, v in sorted(by_act.items(), key=lambda kv: str(kv[0]))) or "—")
               + card("Ekonomi ölçülmemiş", esc(pm.get("n_economics_unknown", "—")),
                      "bu pozisyonlarda p_win/beklenen getiri UNKNOWN")
               + '</div>')
        if advisory:
            out += ('<p class="mut small">⚠ <b>ADVISORY_ONLY</b>: CoinHead REDUCE/EXIT görüşü '
                    'üretir fakat motor açık pozisyonları yalnız stop, TP ve likidasyon ile '
                    'kapatır. Bu tablo bir yönetim eylemi DEĞİL, gözlemdir.</p>')

        def _e(v):
            return badge("UNKNOWN", "info") if v == "UNKNOWN" else fmt(v, 4)

        out += table(
            ["İşlem", "Sembol", "Yön", "Mark", "Net R", "MFE_R", "MAE_R", "Geri verilen R",
             "Capture", "Yaş (s)", "Stop", "TP vuruş", "Öneri", "p_win", "Beklenen net"],
            [[esc(r.get("trade_id")), esc(r.get("symbol")), esc(r.get("side")),
              fmt(r.get("mark"), 6), fmt(r.get("current_net_r"), 3), fmt(r.get("mfe_r"), 3),
              fmt(r.get("mae_r"), 3), fmt(r.get("giveback_r"), 3),
              (fmt(r.get("capture_ratio"), 3) if r.get("capture_ratio") is not None
               else badge(esc(r.get("capture_ratio_state") or "—"), "info")),
              fmt(r.get("position_age_hours"), 1), fmt(r.get("stop"), 6),
              esc(r.get("targets_hit")),
              badge(esc(r.get("proposed_action")),
                    "warn" if r.get("proposed_action") in ("REDUCE", "EXIT") else "info")
              + ('<span class="mut small"> ADVISORY</span>' if advisory else ""),
              _e(r.get("p_win")), _e(r.get("expected_net_return"))]
             for r in (pm.get("positions") if isinstance(pm.get("positions"), list) else [])
             if isinstance(r, dict)],
            num_cols={3, 4, 5, 6, 7, 8, 9, 10}, empty="açık pozisyon yok")
        return out

    @app.get("/api/learning-chain")
    def api_learning_chain():
        """Kapanış zinciri özeti — HER ZAMAN RFC-uyumlu JSON, salt okunur.

        Dosya yoksa 200 + `available=false` döner (500 değil). Sonlu olmayan değer yalnız
        ilgili leaf'te `null` olur ve nedeni `unavailable_reason`a yazılır.
        """
        ch = state.get("learning_chain")
        if not ch:
            return JSONResponse({"available": False,
                                 "reason": "learning_chain.json yok — worker bu sürümle tur tamamlamadı"})
        pm = state.get("position_management") or {}
        payload = dict(ch)
        payload.update({
            "available": True,
            "report_age_s": state.file_age("learning_chain"),
            "position_management": {
                "action_mode": pm.get("action_mode"),
                "executable": pm.get("executable"),
                "n_positions": pm.get("n_positions"),
                "n_economics_unknown": pm.get("n_economics_unknown"),
                "by_action": pm.get("by_action"),
                "positions": pm.get("positions"),
            },
        })
        safe, reasons = json_safe(payload)
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

    @app.get("/api/exit-eval")
    def api_exit_eval():
        """Çıkış politikası karşı-olgusal özeti — HER ZAMAN RFC-uyumlu JSON, salt okunur.

        Dosya yoksa 200 + `available=false`. Yol kapsamı burada da ölçülür ki panel ile API
        aynı sayıyı versin.
        """
        ev = state.get("exit_eval")
        if not isinstance(ev, dict) or not ev:
            return JSONResponse({"available": False,
                                 "reason": "exit_eval.json yok — worker bu sürümle tur tamamlamadı"})
        rows = state.tail_jsonl("position_path", 4000)
        path_ids = {str(r.get("trade_id")) for r in rows if r.get("trade_id")}
        open_ids = {str(p.get("id")) for p in state.futures_positions() if p.get("id")}
        payload = dict(ev)
        payload.update({
            "available": True,
            "report_age_s": state.file_age("exit_eval"),
            "path_coverage": {
                "open_positions": len(open_ids),
                "open_positions_with_path": len(open_ids & path_ids),
                "trades_with_path": len(path_ids),
                "snapshots_tail": len(rows),
            },
        })
        safe, reasons = json_safe(payload)
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

    @app.get("/api/entry-selectivity")
    def api_entry_selectivity():
        """Giriş seçiciliği karşı-olgusal özeti — HER ZAMAN RFC-uyumlu JSON, salt okunur.

        Dosya yoksa 200 + `available=false` (500 değil). Snapshot kapsamı burada da ölçülür ki
        panel ile API aynı sayıyı versin; bozuk şema tek bir leaf'i `null` yapar, ucu düşürmez.
        """
        ev = state.get("entry_selectivity")
        if not isinstance(ev, dict) or not ev:
            return JSONResponse({"available": False,
                                 "reason": ("entry_selectivity.json yok — worker bu sürümle "
                                            "tur tamamlamadı")})
        rows = state.tail_jsonl("entry_snapshot", 4000)
        cand = {str(r.get("candidate_id")) for r in rows
                if r.get("candidate_id") and r.get("kind") != "link"}
        linked = {str(r.get("trade_id")) for r in rows
                  if r.get("kind") == "link" and r.get("trade_id")}
        payload = dict(ev)
        payload.update({
            "available": True,
            "report_age_s": state.file_age("entry_selectivity"),
            "applied_total": int(ev.get("applied_total") or 0),
            "auto_promotion": bool(ev.get("auto_promotion") or False),
            "snapshot_coverage": {
                "candidates_tail": len(cand),
                "trade_links_tail": len(linked),
                "rows_tail": len(rows),
            },
        })
        safe, reasons = json_safe(payload)
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

    @app.get("/api/llm-status")
    def api_llm_status():
        """LLM alt sisteminin gerçek durumu — salt okunur, sır İÇERMEZ.

        `api_key_present` yalnız bir booldur; anahtarın kendisi ne okunur ne yazılır. Bu uç
        LLM'i etkinleştirmez ve sağlayıcı eklemez.
        """
        st_ = state.get("llm_status")
        if not isinstance(st_, dict) or not st_:
            return JSONResponse({"available": False, "status": "UNKNOWN",
                                 "reason": ("llm_status.json yok — worker bu sürümle tur "
                                            "tamamlamadı; durum ölçülemedi")})
        payload = {k: v for k, v in st_.items() if k != "api_key_value"}
        payload.update({"available": True, "report_age_s": state.file_age("llm_status")})
        safe, reasons = json_safe(payload)
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

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
        _df = state.decision_funnel()
        _fr = _df.get("run") or {}
        body += ('<div class="grid">'
                 + card("Karar hunisi", f"{_fr.get('opened', 0)} / {_fr.get('actionable', 0)}",
                        f"pozitif edge {_fr.get('positive_conservative_edge', 0)} · "
                        f"negatif {_fr.get('negative_edge_blocked', 0)} · "
                        f"kapasite {_fr.get('risk_capacity_blocked', 0)} · "
                        f"duplicate {_fr.get('duplicate_blocked', 0)}")
                 + card("24s açılan işlem", str(_df.get("trades_opened_24h", 0)),
                        "yalnız gözlem — karar kapısı DEĞİL")
                 + card("İşlem kotası", "YOK", "günlük/tur başına sabit sayı limiti yok")
                 + '</div>')
        body += '<p class="small mut">Uçlar: <a href="/health/live">/health/live</a> · <a href="/health/ready">/health/ready</a> · <a href="/metrics">/metrics</a> · <a href="/api/overview">/api/overview</a></p>'
        return _page("Sağlık", body, "/health")

    @app.get("/llm", response_class=HTMLResponse)
    def llm():
        b = state.get("llm_budget") or {}
        calls = state.tail_jsonl("llm_calls", 200)[::-1]
        # DÜRÜSTLÜK: boş bütçe kartları "bütçe henüz harcanmadı" gibi okunuyordu. Gerçek durum
        # (DISABLED / NOT_CONFIGURED / NO_CALLS / ACTIVE) worker tarafından ÖLÇÜLÜP yazılır;
        # panel onu gösterir. Bu sayfa LLM'i etkinleştirmez, sağlayıcı eklemez, sır basmaz.
        st_ = state.get("llm_status") if isinstance(state.get("llm_status"), dict) else {}
        status = str(st_.get("status") or "UNKNOWN")
        _tone = {"ACTIVE": "ok", "NO_CALLS": "info", "DISABLED": "warn",
                 "NOT_CONFIGURED": "warn"}.get(status, "warn")
        _why = str(st_.get("reason_tr") or
                   ("llm_status.json yok — durum ÖLÇÜLEMEDİ; boş bütçe 'kullanılmıyor' "
                    "anlamına GELMEZ"))
        body = ('<div class="grid">'
                + card("LLM durumu", badge(esc(status), _tone), esc(_why[:120]))
                + card("Mod", esc(st_.get("mode") or "—"),
                       f"sağlayıcı {esc(st_.get('provider') or '—')}")
                + card("Servis bağlı mı",
                       badge("HAYIR", "warn") if st_.get("service_wired") is False
                       else (badge("EVET", "ok") if st_.get("service_wired") else
                             badge("BİLİNMİYOR", "warn")),
                       "motorda kurulu LLM istemcisi")
                + card("Anahtar tanımlı mı",
                       badge("EVET", "ok") if st_.get("api_key_present")
                       else badge("HAYIR", "warn"),
                       f"env adı {esc(st_.get('api_key_env') or '—')} · değer GÖSTERİLMEZ")
                + card("Kayıtlı çağrı", fmt(st_.get("calls_recorded"), 0),
                       "bu ortamda toplam")
                + '</div>')
        body += f'<div class="grid">{card("Gün", esc(b.get("day")))}{card("Harcanan $", fmt(b.get("spent_usd"), 4))}{card("Token", fmt(b.get("spent_tokens"), 0))}{card("Çağrı", fmt(b.get("calls"), 0))}{card("Limit $", fmt(b.get("limit_usd"), 2))}</div>'
        if not b:
            body += ('<div class="card mut">llm_budget.json yok. Bu, bütçenin sıfırlandığı '
                     'anlamına gelmez: yukarıdaki durum kartı gerçek nedeni gösterir.</div>')
        body += "<h2>Son çağrılar</h2>" + (table(["Zaman", "Model", "Amaç", "Girdi tok", "Çıktı tok", "$", "Süre ms", "Şema ok", "Hata"],
                                                 [[esc(c.get("ts") or c.get("created_at")), esc(c.get("model")), esc(c.get("purpose") or c.get("kind")), fmt(c.get("input_tokens"), 0), fmt(c.get("output_tokens"), 0), fmt(c.get("cost_usd"), 5), fmt(c.get("latency_ms"), 0), "✅" if c.get("schema_ok", True) else "❌", esc(c.get("error") or "")] for c in calls], num_cols={3, 4, 5, 6}) if calls else '<div class="card mut">llm_calls.jsonl yok</div>')
        return _page("LLM", body, "/llm")

    @app.get("/quant", response_class=HTMLResponse)
    def quant_page():
        """Quant Evaluation V1 — SALT OKUNUR araştırma görünümü. `state/quant_eval.json` offline
        araç tarafından üretilir; dosya yoksa sayfa crash olmadan «Veri yok» gösterir."""
        q = state.get("quant_eval") or {}
        if not q:
            return _page("Quant", '<div class="card mut">quant_eval.json yok — offline quant '
                                  'raporu henüz üretilmedi. Bu görünüm salt okunurdur; '
                                  'rapor üretimi worker\'dan bağımsızdır.</div>', "/quant")
        cc = q.get("champion_challenger") or {}
        ov = q.get("overall") or {}
        age = state.file_age("quant_eval")
        stale_note = " · ESKİ RAPOR (>24s)" if isinstance(age, (int, float)) and age > 86400 else ""
        pf_state = ov.get("profit_factor_state")
        pf_txt = "∞ (kayıpsız)" if pf_state == "no_losses" else fmt(ov.get("profit_factor"), 2)
        body = ('<div class="grid">'
                + card("Rapor yaşı", age_text(age) + " önce" if age is not None else "bilinmiyor",
                       esc((q.get("manifest") or {}).get("run_id") or "") + stale_note)
                + card("Karar", esc(cc.get("decision") or "KEEP_CHAMPION"),
                       esc(cc.get("note") or "değerlendirme yok — varsayılan champion"))
                + card("Net expectancy (R)", fmt(ov.get("expectancy_r"), 4),
                       f"n={ov.get('n', 0)}" + (" · YETERSİZ ÖRNEK" if ov.get("insufficient_sample") else ""))
                + card("Net expectancy (USDT)", fmt(ov.get("expectancy_usdt"), 4))
                + card("Profit factor", pf_txt, esc(pf_state or ""))
                + card("Max drawdown (R)", fmt(ov.get("max_drawdown_r"), 2), "trade-bazlı seri")
                + card("Tail CVaR5 (R)", fmt(ov.get("tail_loss_r_cvar5"), 2))
                + card("Shadow etiket kapsamı",
                       f"{q.get('journal', {}).get('n_labeled', 0)} / {q.get('journal', {}).get('n_records', 0)}",
                       "etiketli / toplam karar kaydı")
                + card("Backtest durumu", esc(q.get("backtest_status") or "PARTIAL"),
                       esc(q.get("data_kind") or "") or "veri türü belirtilmedi")
                + '</div>')
        costs = {k: ov.get(k) for k in ("fees_usdt", "funding_usdt", "slippage_usdt",
                                        "gross_pnl_usdt", "net_pnl_usdt")}
        body += "<h2>Maliyet dökümü</h2>" + kv_table(costs)
        cal = ov.get("calibration") or {}
        body += "<h2>Kalibrasyon</h2>" + kv_table(cal)
        cov = q.get("coverage") or {}
        if cov:
            body += ("<h2>Journal kapsaması</h2>"
                     + f'<div class="grid">{card("Kapsama kapıları", badge("GEÇTİ", "ok") if cov.get("gates_passed") else badge("DÜŞÜK", "warn"), esc(cov.get("verdict") or ""))}'
                     + card("Kayıt", f"{cov.get('n_records', 0)}",
                            f"{cov.get('n_accepted', 0)} kabul · {cov.get('n_rejected_shadow', 0)} red/gölge")
                     + card("Veri yaşı (gün)", fmt(cov.get("data_age_days"), 2))
                     + "</div>"
                     + kv_table(cov.get("coverage") or {}))
        sc = q.get("execution_scenarios") or {}
        if sc:
            res = sc.get("results") or {}
            body += "<h2>Execution senaryoları (base / adverse / stress)</h2>" + table(
                ["Senaryo", "n", "exp_R", "net USDT", "dd_R", "exec maliyet USDT"],
                [[esc(name), fmt((res.get(name) or {}).get("n"), 0),
                  fmt((res.get(name) or {}).get("expectancy_r"), 4),
                  fmt((res.get(name) or {}).get("net_pnl_usdt"), 2),
                  fmt((res.get(name) or {}).get("max_drawdown_r"), 2),
                  fmt((res.get(name) or {}).get("total_exec_cost_usdt"), 2)]
                 for name in ("base", "adverse", "stress") if name in res],
                num_cols={1, 2, 3, 4, 5}, empty="senaryo sonucu yok")
            body += ('<div class="grid">'
                     + card("Senaryo dayanıklılığı",
                            badge("DAYANIKLI", "ok") if sc.get("robust_across_scenarios") is True
                            else (badge("KIRILGAN", "warn") if sc.get("robust_across_scenarios") is False
                                  else badge("BİLİNMİYOR", "info")),
                            esc(sc.get("verdict") or ""))
                     + card("Maliyet hassasiyeti", fmt(sc.get("cost_sensitivity_base_to_stress"), 3),
                            "base → stress expectancy düşüşü")
                     + '</div>')
            prov = (res.get("base") or {}).get("provenance") or {}
            if prov:
                body += "<h3>Maliyet veri kaynağı (provenance)</h3>" + kv_table(prov)
        body += challenger_blocks(q)
        wf = q.get("walk_forward") or {}
        if wf:
            body += "<h2>Walk-forward</h2>" + kv_table(
                {k: wf.get(k) for k in ("mode", "layout", "n_folds", "oos_sign_consistency",
                                        "oos_expectancy_r_by_fold", "pbo", "pbo_state",
                                        "holdout_locked", "purged_rows", "unassigned_rows")})
            folds = wf.get("folds") or []
            if folds and isinstance(folds[0], dict) and folds[0].get("windows"):
                body += "<h3>Fold pencereleri (train / validation / test)</h3>" + table(
                    ["Fold", "n_train", "n_validation", "n_test", "test exp_R"],
                    [[fmt(f.get("idx"), 0), fmt(f.get("n_train"), 0),
                      fmt(f.get("n_validation"), 0), fmt(f.get("n_test"), 0),
                      fmt((f.get("test") or {}).get("expectancy_r"), 4)] for f in folds],
                    num_cols={0, 1, 2, 3, 4})
        evd = (q.get("champion_challenger") or {}).get("evidence_summary") or {}
        if evd:
            body += "<h2>Champion/challenger kanıtı</h2>" + kv_table(evd)
            missing = (q.get("champion_challenger") or {}).get("missing_critical") or []
            if missing:
                body += ('<div class="card mut">eksik kritik kanıt: '
                         + esc(", ".join(str(m) for m in missing)) + "</div>")
        elig = q.get("eligibility")
        if elig:
            body += "<h2>Point-in-time uygunluk</h2>" + kv_table(elig)
        else:
            body += ('<div class="card mut">point-in-time eligibility artifact yok — '
                     'backtest geçerliliği PARTIAL kabul edilir (bugünkü metadata geçmiş '
                     'gerçeği sayılmaz).</div>')
        attr = q.get("attribution_summary") or {}
        for dim, groups in list(attr.items())[:6]:
            if isinstance(groups, dict) and groups:
                body += f"<h3>Attribution — {esc(dim)}</h3>" + table(
                    ["Grup", "n", "exp_R", "net USDT", "dd_R", "Örnek"],
                    [[esc(k), fmt(v.get("n"), 0), fmt(v.get("expectancy_r"), 4),
                      fmt(v.get("net_pnl_usdt"), 2), fmt(v.get("max_drawdown_r"), 2),
                      badge("YETERSİZ", "warn") if v.get("insufficient_sample") else badge("ok", "ok")]
                     for k, v in groups.items()], num_cols={1, 2, 3, 4})
        ll = _learning_loop_view()
        if ll.get("available"):
            cov = ll.get("coverage") or {}
            ret = ll.get("retrieval") or {}
            infl = ll.get("influence") or {}
            body += ("<h2>Outcome Learning Loop</h2>"
                     + f'<div class="card">{esc(ll.get("guardrail"))}</div>'
                     + '<div class="grid">'
                     + card("Karar kaydı", f"{ll.get('n_decisions', 0)}",
                            f"{ll.get('n_accepted', 0)} kabul · {ll.get('n_rejected', 0)} red")
                     + card("Outcome bağlantısı", f"{ll.get('n_outcome_linked', 0)}",
                            f"{ll.get('n_outcome_links', 0)} kapanış kaydı")
                     + card("Retrieval isabet", fmt(ret.get("hit_rate"), 3),
                            f"ort. benzerlik {fmt(ret.get('avg_similarity'), 3)}")
                     + card("Öğrenme etkisi",
                            esc(", ".join(infl.get("modes") or []) or "yok"),
                            f"ort {fmt(infl.get('avg_abs_fraction'), 5)} · maks "
                            f"{fmt(infl.get('max_abs_fraction'), 5)} · uygulanan "
                            f"{infl.get('n_applied', 0)}")
                     + "</div>"
                     + "<h3>Snapshot kapsaması</h3>" + kv_table(cov))
            if ll.get("lesson_codes"):
                body += "<h3>Ders kodu dağılımı</h3>" + kv_table(ll["lesson_codes"])
        rt = ll.get("retention") or {}
        if rt:
            health = str(rt.get("archive_health") or "ABSENT")
            tone = "ok" if health in ("OK", "EMPTY") else ("info" if health == "ABSENT" else "warn")
            body += ("<h2>Saklama (kayıpsız arşiv)</h2>"
                     + '<div class="card">Aktif günlük sınırlıdır; taşan kayıtlar ÖNCE arşive '
                       'mühürlenir. Sessiz silme YOKTUR.</div>'
                     + '<div class="grid">'
                     + card("Ömür boyu kayıt", f"{rt.get('lifetime_records', 0)}",
                            f"{rt.get('hot_records', 0)} aktif · "
                            f"{rt.get('archived_records', 0)} arşiv")
                     + card("Değerlendirilen aday (ömür)",
                            f"{int(rt.get('archived_decisions') or 0) + int(ll.get('n_decisions') or 0)}",
                            f"outcome bağlı {int(rt.get('archived_outcomes') or 0) + int(ll.get('n_outcome_linked') or 0)}")
                     + card("Segment", f"{rt.get('n_segments', 0)}",
                            f"son rotasyon {esc(rt.get('last_rotation_at') or '—')}")
                     + card("Arşiv sağlığı", badge(health, tone),
                            esc(rt.get("last_archive_error") or "hata yok"))
                     + "</div>"
                     + kv_table({"En eski kayıt": rt.get("oldest_ts") or "—",
                                 "En yeni kayıt": rt.get("newest_ts") or "—",
                                 "Saklama politikası": rt.get("retention_policy") or "—",
                                 "Silinen segment": rt.get("deleted_segments", 0),
                                 "Retrieval kapsamı": rt.get("retrieval_scope") or "—",
                                 "Sessiz silme": "HAYIR" if rt.get("silent_deletion") is False else "?"}))
        xi = ll.get("experience_index") or {}
        if xi:
            ih = str(xi.get("index_health") or "ABSENT")
            scope = str(xi.get("retrieval_scope") or "HOT_ONLY")
            itone = "ok" if ih == "OK" else ("info" if ih in ("ABSENT", "EMPTY") else "warn")
            stone = "ok" if scope == "HOT_PLUS_INDEXED_HISTORY" else "warn"

            def _ms(v):
                if not isinstance(v, (int, float)):
                    return "—"
                from datetime import datetime, timezone
                return datetime.fromtimestamp(v / 1000, timezone.utc).isoformat(timespec="seconds")

            body += ("<h2>Uzun vadeli retrieval (deneyim indeksi)</h2>"
                     + '<div class="card">Arşivlenmiş sonuçlar canlı retrieval\'da kalır. '
                       'İndeks TÜREV veridir; silinirse kayıpsız arşivden yeniden kurulur. '
                       'Aday başına arşiv TARANMAZ.</div>'
                     + '<div class="grid">'
                     + card("Retrieval kapsamı", badge(scope, stone),
                            f"no-lookahead: {esc(xi.get('no_lookahead') or '—')}")
                     + card("İndekslenmiş deneyim", f"{xi.get('indexed_experiences', 0)}",
                            f"{xi.get('indexed_real', 0)} gerçek · "
                            f"{xi.get('indexed_shadow', 0)} gölge")
                     + card("İşlenmiş segment", f"{xi.get('processed_segments', 0)}",
                            f"gecikme {xi.get('index_lag_segments', 0)} · bozuk "
                            f"{xi.get('corrupt_segments', 0)}")
                     + card("İndeks sağlığı", badge(ih, itone),
                            esc(xi.get("last_index_error") or "hata yok"))
                     + "</div>"
                     + kv_table({"En eski kullanılabilir outcome": _ms(xi.get("oldest_label_ms")),
                                 "En yeni kullanılabilir outcome": _ms(xi.get("newest_label_ms")),
                                 "Son refresh": xi.get("last_refresh_at") or "—",
                                 "Son rebuild": xi.get("last_rebuild_at") or "—",
                                 "Atlanan satır": xi.get("skipped_rows", 0),
                                 "Arşivden yeniden kurulabilir":
                                     "EVET" if xi.get("rebuildable_from_archive") else "?"}))
        uni = state.get("universe_eval") or {}
        fun_t = (state.get("decision_funnel") or {})
        if uni or fun_t.get("tiers"):
            uc = uni.get("counts") or {}
            tiers = fun_t.get("tiers") or {}
            cov = fun_t.get("coverage") or {}
            n_disp = min(15, len(state.coin_heads()))
            cov_ratio = (round(cov["journaled"] / cov["evaluated"], 4)
                         if cov.get("evaluated") else None)
            body += ("<h2>Değerlendirme evreni ve huni</h2>"
                     + '<div class="card">Panelde görünen liste analiz kapsamı DEĞİLDİR; '
                       'bot bütün uygun evreni tarar ve HER adayı kaydeder.</div>'
                     + '<div class="grid">'
                     + card("Evren (değerlendirilen)", f"{uc.get('eligible', 0)}",
                            f"hedef {((uni.get('targets') or {}).get('min', '—'))}-"
                            f"{((uni.get('targets') or {}).get('max', '—'))} · panelde {n_disp}")
                     + card("Tier A / B / C",
                            f"{tiers.get('tier_a_universe', '—')} / "
                            f"{tiers.get('tier_b_deep', '—')} / "
                            f"{tiers.get('tier_c_ranked', '—')}",
                            f"stage-1 kayıt {tiers.get('screened_journaled', 0)}")
                     + card("Journal kapsaması", fmt(cov_ratio, 4),
                            f"{cov.get('journaled', 0)} / {cov.get('evaluated', 0)} aday")
                     + card("Evren snapshot", esc(str(uni.get("artifact_sha") or "—")),
                            f"as-of {esc(str(uni.get('as_of') or '—'))}")
                     + "</div>")
            if uni.get("below_target_reason"):
                body += f'<div class="card warn">{esc(uni["below_target_reason"])}</div>'
        rv2 = q.get("risk_v2") or {}
        if rv2:
            big = rv2.get("largest_cluster") or {}
            exp = rv2.get("exposure") or {}
            body += ("<h2>Risk V2 (advisory)</h2>"
                     + f'<div class="card">{esc(rv2.get("banner") or "ADVISORY ONLY — ACTIVE RISK ENGINE UNCHANGED")}</div>'
                     + '<div class="grid">'
                     + card("Küme sayısı", fmt(rv2.get("n_clusters"), 0),
                            f"{rv2.get('n_positions', 0)} pozisyon")
                     + card("En yoğun küme",
                            esc(", ".join(big.get("symbols") or []) or "-"),
                            f"pay {fmt(big.get('share_of_total'), 3)}"
                            + (f" · {esc(big.get('label'))}" if big.get("label") else ""))
                     + card("Korelasyon kanıtı",
                            esc(rv2.get("correlation_quality") or "bilinmiyor"),
                            esc(rv2.get("cluster_basis") or ""))
                     + card("Yönlü maruziyet",
                            f"L {fmt(exp.get('total_long_usdt'), 2)} / S {fmt(exp.get('total_short_usdt'), 2)}",
                            "USDT")
                     + card("Veri yaşı",
                            age_text((rv2.get("data_age_ms") or 0) / 1000) + " önce"
                            if rv2.get("data_age_ms") is not None else "bilinmiyor",
                            "ESKİ VERİ" if rv2.get("data_stale") else "")
                     + "</div>")
            advs = rv2.get("advisories") or []
            if advs:
                body += table(["Sembol", "Yön", "Mevcut kaldıraç", "Advisory kaldıraç",
                               "Risk ölçeği", "Gerekçe"],
                              [[esc(a.get("symbol")), esc(a.get("direction")),
                                fmt(a.get("current_leverage"), 0), fmt(a.get("advised_leverage"), 0),
                                fmt(a.get("risk_scale"), 2),
                                esc(", ".join(a.get("derisk_reasons") or []))] for a in advs],
                              num_cols={2, 3, 4})
            if rv2.get("warnings"):
                body += "<h3>Advisory uyarıları</h3>" + render_any(rv2["warnings"])
        risk = q.get("risk_clusters") or {}
        if risk and not rv2:
            body += "<h2>Risk kümeleri (advisory)</h2>" + render_any(risk)
        man = q.get("manifest") or {}
        if man:
            body += "<h2>Son rapor manifesti</h2>" + kv_table(
                {k: man.get(k) for k in ("run_id", "code_sha", "config_hash", "seed",
                                         "valid_backtest", "manifest_hash", "label")})
        warns = q.get("warnings") or []
        if warns:
            body += "<h2>Uyarılar</h2>" + render_any(warns)
        body += ('<p class="small mut">Bu görünüm salt okunurdur; sonuçlar araştırma çıktısıdır ve '
                 'kârlılık kanıtı DEĞİLDİR. Counterfactual/shadow satırlar gerçek fill değildir.</p>')
        return _page("Quant", body, "/quant")

    def _registry_counts() -> dict:
        try:
            from ..learn.feature_registry import summary as reg_summary
            r = reg_summary()
            return {"n_active": r.get("n_active"), "n_families": len(r.get("families") or []),
                    "by_class": r.get("by_class"),
                    "n_redundancy_groups": len(r.get("redundancy_groups") or {})}
        except Exception:  # noqa: BLE001
            return {"available": False}

    def _learning_loop_view() -> dict:
        """Outcome Learning Loop özeti — SALT OKUNUR, bozuk/eksik günlükte crash etmez."""
        try:
            from ..learn.decision_journal import ACCEPTED, KIND_DECISION, KIND_OUTCOME, REJECTED
            rows = state.tail_jsonl("decision_journal", 4000)
        except Exception:  # noqa: BLE001
            return {"available": False, "reason": "decision_journal.jsonl okunamadı"}
        try:
            retention = state.decision_retention()
        except Exception:  # noqa: BLE001 — bozuk/eksik arşiv metadatası 500 ÜRETMEZ
            retention = {"archive_health": "UNREADABLE", "silent_deletion": False,
                         "archive_available": False, "retrieval_scope": "HOT_ONLY"}
        try:
            exp_index = state.experience_index()
        except Exception:  # noqa: BLE001 — bozuk/eksik indeks metadatası 500 ÜRETMEZ
            exp_index = {"available": False, "index_health": "UNREADABLE",
                         "retrieval_scope": "DEGRADED"}
        if not rows:
            return {"available": False,
                    "reason": "decision_journal.jsonl yok — henüz karar kaydı üretilmedi",
                    "retention": retention, "experience_index": exp_index,
                    "retrieval_scope": exp_index.get("retrieval_scope", "HOT_ONLY")}
        dec = [r for r in rows if r.get("kind") == KIND_DECISION]
        outs = [r for r in rows if r.get("kind") == KIND_OUTCOME]
        n = len(dec)

        def ratio(pred) -> float | None:
            return round(sum(1 for r in dec if pred(r)) / n, 4) if n else None

        linked = {str(r.get("trade_id")) for r in outs if r.get("trade_id")}
        infl = [r.get("learning_influence") or {} for r in dec if r.get("learning_influence")]
        fracs = [abs(float(x["fraction"])) for x in infl
                 if isinstance(x.get("fraction"), (int, float))]
        sims = [float(x["top_similarity"]) for x in infl
                if isinstance(x.get("top_similarity"), (int, float))]
        hits = sum(1 for x in infl if (x.get("n_experience") or 0) > 0)
        modes = {str(x.get("mode")) for x in infl if x.get("mode")}
        lesson_codes: dict[str, int] = {}
        for r in outs:
            for c in (r.get("lesson_codes") or []):
                lesson_codes[str(c)] = lesson_codes.get(str(c), 0) + 1
        return {"available": True,
                "n_decisions": n, "n_outcome_links": len(outs),
                "n_accepted": sum(1 for r in dec if r.get("outcome_kind") == ACCEPTED),
                "n_rejected": sum(1 for r in dec if r.get("outcome_kind") == REJECTED),
                "n_outcome_linked": sum(1 for r in dec if str(r.get("trade_id") or "") in linked),
                "coverage": {"features": ratio(lambda r: bool(r.get("features"))),
                             "specialist_scores": ratio(lambda r: bool(r.get("specialist_scores"))),
                             "regime": ratio(lambda r: bool(r.get("regime"))),
                             "trade_id": ratio(lambda r: bool(r.get("trade_id")))},
                "retrieval": {"n_with_influence": len(infl),
                              "hit_rate": round(hits / len(infl), 4) if infl else None,
                              "avg_similarity": round(sum(sims) / len(sims), 4) if sims else None},
                "influence": {"modes": sorted(modes) or None,
                              "avg_abs_fraction": round(sum(fracs) / len(fracs), 6) if fracs else None,
                              "max_abs_fraction": round(max(fracs), 6) if fracs else None,
                              "n_applied": sum(1 for x in infl if x.get("applied"))},
                "lesson_codes": dict(sorted(lesson_codes.items(), key=lambda kv: -kv[1])[:12]),
                "retention": retention,
                "experience_index": exp_index,
                "feature_registry": _registry_counts(),
                "retrieval_scope": exp_index.get("retrieval_scope", "HOT_ONLY"),
                "guardrail": "LEARNING CANNOT OVERRIDE RISK GATES"}

    @app.get("/api/coin-memory/{base}")
    def api_coin_memory(base: str):
        """Coin'e özel bellek özeti — SALT OKUNUR; eksik/bozuk veride 500 YOK."""
        safe, reasons = json_safe(state.coin_memory(base))
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

    @app.get("/api/feature-registry")
    def api_feature_registry():
        """Feature envanteri — aktif/araştırma sınıfları, aileler, yedek gruplar, tavanlar."""
        try:
            from ..learn.feature_registry import summary as reg_summary
            doc = reg_summary()
        except Exception:  # noqa: BLE001
            doc = {"available": False}
        safe, _ = json_safe(doc)
        return JSONResponse(safe)

    @app.get("/api/universe")
    def api_universe():
        """Değerlendirme evreni — SALT OKUNUR: panelde görünen top liste ile botun analiz
        kapsamı AYRI sayılardır. Tüm semboller aranabilir/filtrelenebilir (istemci tarafı).
        Eksik/bozuk snapshot 500 ÜRETMEZ."""
        doc = state.get("universe_eval") or {}
        heads = state.coin_heads()
        fun = state.get("decision_funnel") or {}
        safe, reasons = json_safe({
            "available": bool(doc),
            "displayed_top": min(15, len(heads)),
            "evaluated_universe": (doc.get("counts") or {}).get("eligible", 0),
            "targets": doc.get("targets"),
            "counts": doc.get("counts"),
            "below_target_reason": doc.get("below_target_reason"),
            "as_of": doc.get("as_of"), "artifact_sha": doc.get("artifact_sha"),
            "changes": doc.get("changes"), "provenance": doc.get("provenance"),
            "tiers": fun.get("tiers"), "coverage": fun.get("coverage"),
            "symbols": doc.get("symbols") or [], "excluded": doc.get("excluded") or []})
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

    @app.get("/api/learning-loop")
    def api_learning_loop():
        """Outcome Learning Loop özeti — read-only, RFC-safe, boş/bozuk veride 500 YOK."""
        safe, reasons = json_safe(_learning_loop_view())
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

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

    @app.get("/api/live/positions")
    def api_live_positions():
        """Açık pozisyon + PnL — tarayıcı bunu kısa aralıkla çeker. Binance'a DOĞRUDAN gidilmez;
        veri worker'ın yazdığı defterden okunur, bu yüzden tarayıcı isteği yeni API çağrısı ÜRETMEZ."""
        vm = _view()
        pv = vm["portfolio"]
        return {"generated_at": utc_now().isoformat(timespec="seconds"),
                "freshness": vm["freshness"], "inconsistencies": vm["inconsistencies"],
                "open_total": pv.open_total, "open_long": pv.open_long, "open_short": pv.open_short,
                "positions": [p.to_dict() for p in pv.positions],
                "rows": vm["rows"], "columns": vm["columns"]}

    @app.get("/api/live/summary")
    def api_live_summary():
        """Bakiye/teminat/risk + K/Z özeti (daha uzun aralıkla çekilir).

        `summary` KANONİK sözlüktür ve HTML sayfası da AYNI sözlükten üretilir → iki yüzey
        farklı sayı gösteremez. `cards[].value` makine için ham sayı (hesaplanamıyorsa `null`),
        `cards[].display` insan için biçimlenmiş metindir.
        """
        vm = _view()
        pv = vm["portfolio"]
        return {"generated_at": utc_now().isoformat(timespec="seconds"),
                "freshness": vm["freshness"], "chief": vm["chief"].to_dict(),
                "summary": vm["summary"],
                "cards": [c.to_dict() for c in vm["cards"]],
                "portfolio": {k: val for k, val in pv.to_dict().items() if k != "positions"}}

    @app.get("/api/live/coin-heads")
    def api_live_coin_heads():
        """Coin head tablosu + AÇIK POZİSYON KAPSAMI — SALT-OKUNUR (state'e YAZMAZ).

        İlk HTML render'ı ile AYNI `views.coin_head_table` çağrısından beslenir; tarayıcı bu yükü
        `buildHeadsTable` ile çizer. Böylece sayfa açıkken bot pozisyon açar/kapatırsa tablo ve
        «Açık pozisyon kapsamı X / Y» sayacı sayfa YENİLENMEDEN güncellenir.
        """
        chp = _coin_head_payload()
        cov = open_coverage(chp["heads"], state.futures_positions())
        return {"generated_at": utc_now().isoformat(timespec="seconds"),
                "coin_head_scope": {k: chp[k] for k in
                                    ("open_positions_total", "open_positions_shown",
                                     "missing_open_symbols", "no_decision_symbols",
                                     "coverage_complete", "candidate_limit")},
                "open_positions_total": cov["open_positions_total"],
                "open_positions_shown": cov["open_positions_shown"],
                "missing_open_symbols": cov["missing_open_symbols"],
                "coverage_complete": cov["coverage_complete"],
                "columns": chp["columns"], "rows": chp["rows"], "meta": chp["meta"],
                "num_cols": chp["num_cols"], "pnl_cols": chp["pnl_cols"],
                "badge_cols": chp["badge_cols"], "symbol_col": chp["symbol_col"],
                "missing_text": _missing_text(cov["missing_open_symbols"]),
                "empty_text": "coin head kararı yok (coin_heads.json)"}

    @app.get("/api/live/health")
    def api_live_health():
        ov = state.overview()
        return {"generated_at": utc_now().isoformat(timespec="seconds"),
                "health": ov["health"], "summary": ov["health_summary"],
                "heartbeat_age_s": ov["heartbeat_age_s"], "last_run_age_s": ov["last_run_age_s"],
                "price_age_s": ov["price_age_s"], "killswitch": ov["killswitch"], "mode": ov["mode"]}

    @app.get("/api/overview")
    def api_overview():
        """Genel bakis — HER ZAMAN RFC-uyumlu JSON.

        KOK NEDEN (gercek state'te dogrulandi): `state.overview()` HAM coin-head sozluklerini
        (`top_heads`, cf284ce'den beri ayrica `coin_head_scope.heads`) dogrudan JSON'a veriyordu.
        Ham sozluk `specialist_reports[].levels.ema100_1d / ema200_1d` altinda CIPLAK `NaN`
        tasiyabilir (worker `coin_heads.json`a `NaN` literali yazar) -> `JSONResponse`in
        `allow_nan=False` serilestirmesi `ValueError` firlatir -> HTTP 500. `/api/live/coin-heads`
        yalniz NORMALIZE edilmis satir/meta sozlesmesini dondurdugu icin etkilenmiyordu.

        Cozum SUNUM SINIRINDA: ham model ciktisi JSON'a KONTROLSUZ verilmez; normalize edilmis
        coin-head sozlesmesi yayimlanir ve kalan yuk kanonik sonluluk guard'indan gecirilir.
        Olculemeyen sayi SAHTE `0` degil `null` olur, gerekcesi `unavailable_reason`a yazilir.
        """
        ov = dict(state.overview())
        chp = _coin_head_payload()
        chp_scope = {k: chp[k] for k in ("heads", "open_positions_total", "open_positions_shown",
                                         "missing_open_symbols", "no_decision_symbols",
                                         "coverage_complete", "candidate_limit")}
        cov = open_coverage(chp["heads"], state.futures_positions())
        # GERIYE DONUK UYUMLULUK: `top_heads` ve `coin_head_scope` ONCEKI SEMALARIYLA KALIR.
        # Sessiz schema kirilmasi YAPILMAZ; yalniz `json_safe` ile RFC-safe hale getirilir —
        # sonlu olmayan degerler SADECE ilgili leaf'te `null` olur, saglam specialist report
        # alanlari (or. `ema50_1d`) KORUNUR. Normalize sunum sozlesmesi EK alanlarda yayimlanir.
        ov["top_heads"] = chp["heads"]
        ov["coin_head_scope"] = dict(chp_scope)
        ov["coin_head_rows"] = coin_head_api_rows(chp)      # EK: normalize, sayisal alanlar sonlu
        ov["coin_head_table"] = {"columns": chp["columns"], "rows": chp["rows"], "meta": chp["meta"],
                                 "num_cols": chp["num_cols"], "pnl_cols": chp["pnl_cols"],
                                 "badge_cols": chp["badge_cols"], "symbol_col": chp["symbol_col"]}
        # Kapsam sayaclari `/api/live/coin-heads` ile AYNI kaynaktan ve AYNI olcumden gelir.
        ov["open_positions_total"] = cov["open_positions_total"]
        ov["open_positions_shown"] = cov["open_positions_shown"]
        ov["missing_open_symbols"] = cov["missing_open_symbols"]
        ov["coverage_complete"] = cov["coverage_complete"]
        safe, reasons = json_safe(ov)
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

    @app.get("/api/quant/summary")
    def api_quant_summary():
        """Quant Evaluation özeti — HER ZAMAN RFC-uyumlu JSON, salt okunur.

        Dosya yoksa 200 + `available=false` döner (500 değil); sonlu olmayan değerler `json_safe`
        ile yalnız ilgili leaf'te `null` olur ve nedeni `unavailable_reason`a yazılır."""
        q = state.get("quant_eval")
        if not q:
            return JSONResponse({"available": False, "schema_version": None,
                                 "reason": "quant_eval.json yok — offline rapor üretilmedi"})
        age = state.file_age("quant_eval")
        payload = {"available": True, "schema_version": q.get("schema_version"),
                   "report_age_s": age,
                   "report_stale": bool(isinstance(age, (int, float)) and age > 86400),
                   "backtest_status": q.get("backtest_status") or "PARTIAL",
                   "data_kind": q.get("data_kind"),
                   "generated_run_id": (q.get("manifest") or {}).get("run_id"),
                   "champion_challenger": q.get("champion_challenger"),
                   "overall": q.get("overall"),
                   "journal": q.get("journal"),
                   "coverage": q.get("coverage"),
                   "execution_scenarios": q.get("execution_scenarios"),
                   "walk_forward": q.get("walk_forward"),
                   "attribution_summary": q.get("attribution_summary"),
                   "risk_v2": q.get("risk_v2"),
                   "eligibility": q.get("eligibility"),
                   "evidence": q.get("evidence"),
                   "risk_clusters": q.get("risk_clusters"),
                   "manifest": q.get("manifest"),
                   "warnings": q.get("warnings")}
        safe, reasons = json_safe(payload)
        if reasons:
            safe["unavailable_reason"] = dict(reasons)
        return JSONResponse(safe)

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
        # Karar hunisi: sabit islem sayisi kotasi YOK -> cap gauge'lari her zaman 0 (null anlaminda)
        _df = ov.get("decision_funnel") or {}
        _fr = _df.get("run") or {}
        for _k in ("actionable", "positive_conservative_edge", "negative_edge_blocked",
                   "risk_capacity_blocked", "duplicate_blocked", "research_small", "opened"):
            lines += [f"# HELP tradingbot_funnel_{_k} decision funnel stage count (this run)",
                      f"# TYPE tradingbot_funnel_{_k} gauge",
                      f"tradingbot_funnel_{_k} {int(_fr.get(_k, 0) or 0)}"]
        lines += ["# HELP tradingbot_trades_opened_24h observation only, NOT a decision gate",
                  "# TYPE tradingbot_trades_opened_24h gauge",
                  f"tradingbot_trades_opened_24h {int(_df.get('trades_opened_24h', 0) or 0)}",
                  "# HELP tradingbot_opportunity_cost_count strong opportunities blocked by risk capacity",
                  "# TYPE tradingbot_opportunity_cost_count gauge",
                  f"tradingbot_opportunity_cost_count {int(_df.get('opportunity_cost_count', 0) or 0)}",
                  "# HELP tradingbot_daily_trade_cap 0 = no fixed daily trade quota exists",
                  "# TYPE tradingbot_daily_trade_cap gauge", "tradingbot_daily_trade_cap 0",
                  "# HELP tradingbot_per_run_trade_cap 0 = no fixed per-run trade quota exists",
                  "# TYPE tradingbot_per_run_trade_cap gauge", "tradingbot_per_run_trade_cap 0"]
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

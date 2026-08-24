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
                        verdict_badge, verdict_kind, weight_table)

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
        if lessons:
            # `lessons` YALNIZ bu tablo içindir — üst kartlar bu listeden HESAPLANMAZ.
            body += (f"<h2>Dersler</h2><p class=\"mut small\">Veri penceresi: son {len(lessons)} ders "
                     f"(kayıt defteri en fazla 200 ders tutar). Üstteki özet kartları TÜM ZAMAN "
                     f"sayaçlarındandır.</p>" + lessons_table(lessons[-30:][::-1]))
        if ln.get("blacklist"):
            body += "<h2>Kara liste</h2><ul>" + "".join(f"<li>{esc(x)}</li>" for x in ln["blacklist"]) + "</ul>"
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
        body = f'<div class="grid">{card("Gün", esc(b.get("day")))}{card("Harcanan $", fmt(b.get("spent_usd"), 4))}{card("Token", fmt(b.get("spent_tokens"), 0))}{card("Çağrı", fmt(b.get("calls"), 0))}{card("Limit $", fmt(b.get("limit_usd"), 2))}</div>'
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

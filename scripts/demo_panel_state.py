# -*- coding: utf-8 -*-
"""PANEL EKRAN KANITI İÇİN SENTETİK DURUM — `python scripts/demo_panel_state.py [dizin] [--seed N]`.

Bu betik SAHTE veri üretir: ekran görüntüsü ve yerleşim doğrulaması içindir, GERÇEK PİYASA SONUCU DEĞİLDİR ve
üretim state dizinine yazılmamalıdır. Bot bu dosyaları okumaz; yalnız panel (salt okunur) okur.

* Mumlar tohumlu rastgele yürüyüştür (15m üretilir, 1h/4h/1d onlardan toplanır); son kapanış her coinin sabit
  demo fiyatıdır. Aynı tohum + aynı saat → aynı dosyalar.
* Defterler GERÇEK muhasebe koduyla (`FuturesLedgerV2`: komisyon, fonlama, izole marj, stop/hedef) bu sentetik
  1h barlar üzerinde oynatılır; kâr/zarar rastgele yürüyüşün sonucudur, seçilmiş değildir.
* Ortak deneyim kartının özeti gerçek rapor koduyla (`shared_experience.report`) sentetik satırlardan hesaplanır.
* Hedef klasörde bu betiğin yazmadığı bir `state/` varsa (işaret dosyası yok) hiçbir şey yazılmaz.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1
D1 = 24 * H1
TFS = (("15m", M15), ("1h", H1), ("4h", H4), ("1d", D1))
N_BARS = 320
MARKER = "DEMO_SYNTHETIC.txt"
MARKER_TEXT = "Bu klasör scripts/demo_panel_state.py tarafından yazıldı: SENTETİK demo verisi, gerçek piyasa sonucu değil.\n"

#: Demo coinleri ve son (sabit) fiyatları. NEWX gerçek bir coin değildir (yeni listeleme örneği).
PRICES = {"BTC": 61250.0, "ETH": 2980.0, "SOL": 205.0, "BNB": 585.0, "XRP": 0.58, "DOGE": 0.118,
          "LINK": 14.2, "AVAX": 27.0, "NEWX": 1.284}
#: 15m log-getiri oynaklığı (yaklaşık yıllık %45-%90).
VOL_15M = {"BTC": 0.0024, "ETH": 0.0030, "SOL": 0.0040, "BNB": 0.0028, "XRP": 0.0038, "DOGE": 0.0045,
           "LINK": 0.0040, "AVAX": 0.0042, "NEWX": 0.0060}

#: Defterler: (anahtar, kural adı, [(coin, yön, kaç saat önce, stop %, [hedef %], marj USDT, kaldıraç, kurulum)]).
#: Sonuçları (stop/hedef/açık) sentetik fiyat yolu belirler.
BOOKS = (
    ("main", "main", [
        ("BNB", "LONG", 150, 2.5, [5.0], 12.0, 2, "trend_pullback"),
        ("XRP", "SHORT", 120, 3.5, [6.0], 10.0, 2, "breakdown"),
        ("SOL", "LONG", 96, 3.0, [4.5, 9.0], 12.0, 2, "trend_pullback"),
        ("LINK", "LONG", 72, 4.5, [7.0], 10.0, 2, "range_breakout"),
        ("AVAX", "SHORT", 48, 3.0, [5.0], 10.0, 2, "failed_breakout"),
        ("BTC", "LONG", 9, 3.0, [6.5], 35.0, 2, "trend_pullback"),
        ("ETH", "SHORT", 3, 3.5, [], 9.0, 2, "breakdown"),
    ]),
    ("strategy_paper", "t2_trend_regime", [
        ("ETH", "LONG", 170, 6.0, [], 20.0, 1, "ema200_trend"),
        ("SOL", "LONG", 52, 7.0, [], 22.0, 1, "ema200_trend"),
    ]),
    ("strategy_paper_m2", "m2_tsmom28", [
        ("DOGE", "LONG", 140, 5.0, [], 20.0, 1, "tsmom28"),
        ("BTC", "LONG", 30, 5.5, [], 70.0, 1, "tsmom28"),
    ]),
    ("strategy_paper_box", "b1_box_fade", [
        ("ETH", "SHORT", 60, 0.9, [1.4], 8.0, 4, "box_fade"),
        ("SOL", "LONG", 40, 1.0, [1.5], 8.0, 4, "box_fade"),
        ("BNB", "SHORT", 26, 0.8, [1.2], 8.0, 4, "box_fade"),
        ("XRP", "LONG", 14, 1.1, [1.6], 8.0, 4, "box_fade"),
        ("LINK", "SHORT", 5, 1.2, [1.8], 8.0, 4, "box_fade"),
    ]),
    ("strategy_paper_trend4h", "d4_donchian_20_10", [
        ("AVAX", "LONG", 110, 5.0, [], 15.0, 2, "donchian_20_10"),
        ("BTC", "SHORT", 44, 4.0, [], 35.0, 2, "donchian_20_10"),
    ]),
    ("strategy_paper_candle4h", "c4_candle_variations", [
        ("LINK", "LONG", 90, 3.5, [7.0], 12.0, 2, "bullish_engulfing_4h"),
        ("DOGE", "SHORT", 20, 4.0, [8.0], 12.0, 2, "shooting_star_4h"),
    ]),
)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------- mumlar
def series_15m(base: str, *, now_ms: int, seed: int) -> list[tuple[int, float, float, float, float, float]]:
    """Tohumlu 15m rastgele yürüyüş (ts, o, h, l, c, v); son kapanış tam olarak PRICES[base]."""
    rng = random.Random("%s:%s" % (seed, base))
    last_open = (now_ms // M15) * M15 - M15                # son KAPANMIŞ 15m bar
    first = (now_ms // D1) * D1 - (N_BARS - 1) * D1         # 1d serisi N_BARS bar olsun
    n = (last_open - first) // M15 + 1
    sig = VOL_15M.get(base, 0.004)
    drift = rng.uniform(-0.6, 0.6) * sig / 8.0             # coin başına hafif eğilim (rejim çeşitliliği)
    rets, regime = [], 1.0
    for i in range(n):
        if i % 2000 == 0:
            regime = rng.uniform(0.6, 1.6)                  # oynaklık kümeleri
        rets.append(rng.gauss(drift, sig * regime))
    total = sum(rets)
    logp = math.log(PRICES[base]) - total                   # yolun sonu tam demo fiyatına otursun
    out = []
    for i, r in enumerate(rets):
        o = math.exp(logp)
        logp += r
        c = math.exp(logp)
        wick = sig * regime * 0.7
        hi = max(o, c) * math.exp(abs(rng.gauss(0.0, wick)))
        lo = min(o, c) * math.exp(-abs(rng.gauss(0.0, wick)))
        vol = 1000.0 * (1.0 + abs(r) / sig) * rng.uniform(0.6, 1.4)
        out.append((first + i * M15, o, hi, lo, c, vol))
    return out


def aggregate(rows, step: int):
    out: dict[int, list] = {}
    for ts, o, h, lo, c, v in rows:
        k = (ts // step) * step
        cur = out.get(k)
        if cur is None:
            out[k] = [k, o, h, lo, c, v]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], lo)
            cur[4] = c
            cur[5] += v
    return [tuple(x) for x in sorted(out.values())]


def write_candles(path: Path, rows) -> None:
    lines = ["timestamp,open,high,low,close,volume"]
    lines += ["%d,%.8g,%.8g,%.8g,%.8g,%.2f" % r for r in rows[-N_BARS:]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------- defterler (gerçek muhasebe)
def simulate_book(trades, bars_1h: dict, last_px: dict, *, now_ms: int):
    """Planlanan işlemleri gerçek `FuturesLedgerV2` ile sentetik 1h barlarda oynatır; defteri döndürür."""
    from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, TickData

    led = FuturesLedgerV2(Decimal("100"), max_positions=None)
    last_closed = (now_ms // H1) * H1 - H1
    plan: dict[int, list] = {}
    for tr in trades:
        plan.setdefault(last_closed - int(tr[2]) * H1, []).append(tr)
    start = min(plan) if plan else last_closed
    for ts in range(start, last_closed + H1, H1):
        close_dt = _dt(ts + H1)
        marks = {}
        for sym in list(led.positions):
            b = bars_1h[sym.split("/")[0]].get(ts)
            if b:
                marks[sym] = TickData(last=Decimal(str(b[4])), high=Decimal(str(b[2])), low=Decimal(str(b[3])),
                                      open=Decimal(str(b[1])))
        if marks:
            led.tick(marks, now_utc=close_dt, bar_advance=True)
        for base, side, _ago, stop_pct, tgts, margin, lev, setup in plan.get(ts, []):
            b = bars_1h[base].get(ts)
            if not b:
                continue
            px = Decimal(str(b[4]))
            sgn = Decimal(1) if side == "LONG" else Decimal(-1)
            stop = px * (Decimal(1) - sgn * Decimal(str(stop_pct)) / 100)
            targets = [px * (Decimal(1) + sgn * Decimal(str(t)) / 100) for t in tgts]
            pos = led.open("%s/USDT" % base, side, px, SizeSpec(Decimal(str(margin)), AmountType.MARGIN, lev),
                           stop=stop, targets=targets, setup_type=setup, now=close_dt,
                           features={"setup_type": setup, "demo": True})
            if pos is None:
                print("demo: %s %s açılmadı (%s)" % (base, side, led.last_reject_reason), file=sys.stderr)
    now_marks = {s: Decimal(str(last_px[s.split("/")[0]])) for s in led.positions}
    if now_marks:
        led.tick(now_marks, now_utc=_dt(now_ms))
    led.updated_at = iso(now_ms)
    return led


def book_summary(key: str, name: str, led, *, now_ms: int) -> dict:
    from tradingbot.strategy_paper import SCHEMA_VERSION
    fs = led.summary()
    return {"schema_version": SCHEMA_VERSION, "key": key, "name": name, "generated_at": iso(now_ms - 240_000),
            "rule_evaluated_at": iso(now_ms - 240_000), "rule_tour": 412, "rule_stale_s": 240.0,
            "rule_evaluated_this_write": True, "regime": "UP", "starting_equity": float(led.starting_equity),
            "summary": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in fs.items()},
            "positions": {s: {"side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty),
                              "stop": float(p.stop) if p.stop else None, "leverage": p.leverage,
                              "opened_at": p.opened_at, "last_price": float(p.last_price) if p.last_price else None}
                          for s, p in led.positions.items()},
            "history_tail": led.history_dicts()[-50:],
            "counters": {"tours": 412, "opened": len(led.positions) + len(led.history), "closed": len(led.history),
                         "data_rejected": 0},
            "rejections": {}, "data_gaps": {}, "last_actions": [],
            "note_tr": "SENTETİK DEMO — KÂĞIT İLERİ TEST görünümü; gerçek para ve gerçek piyasa sonucu yok."}


# ---------------------------------------------------------------------------- ortak deneyim + gölge danışman
def experience_docs(state: Path, *, now_ms: int, seed: int) -> None:
    """Ortak deneyim deposuna sentetik satırlar (gerçek `rows.py` kurucularıyla) + gerçek rapor özeti + durum dosyası."""
    from tradingbot.shared_experience import report as X
    from tradingbot.shared_experience import rows as R
    from tradingbot.shared_experience import situation as S
    from tradingbot.shared_experience.store import ExperienceStore

    rng = random.Random("%s:xp" % seed)
    now = _dt(now_ms)
    env = R.make_env(recorded_at=now - timedelta(minutes=5), learning_since=iso(now_ms - 3 * D1))
    cells = (  # (defter, kurulum, yön, durum, gerçek n, olsaydı n, ort. net R)
        ("strategy_paper_box", "box_fade", "LONG", {"trend": "UP", "vol": "NORMAL", "btc": "UP", "volume": "NORMAL",
                                                    "structure": "HH_HL"}, 14, 41, -0.05),
        ("strategy_paper_box", "box_fade", "SHORT", {"trend": "RANGE", "vol": "LOW", "btc": "RANGE", "volume": "LOW",
                                                     "structure": "MIXED"}, 9, 33, 0.08),
        ("main", "trend_pullback", "LONG", {"trend": "UP", "vol": "HIGH", "btc": "UP", "volume": "HIGH",
                                            "structure": "HH_HL"}, 11, 18, 0.12),
        ("strategy_paper_trend4h", "donchian_20_10", "SHORT", {"trend": "DOWN", "vol": "NORMAL", "btc": "DOWN",
                                                               "volume": "NORMAL", "structure": "LH_LL"}, 4, 12, -0.2),
    )
    syms = [b for b in PRICES if b != "NEWX"]
    rows, i = [], 0
    for book, setup, side, dims, n_real, n_cf, mu in cells:
        for k in range(n_real + n_cf):
            i += 1
            t = now - timedelta(hours=6 + 7 * k + rng.random())
            sym = "%s/USDT" % syms[i % len(syms)]
            r = round(rng.gauss(mu, 1.0), 3)
            snap = {"schema_id": S.SCHEMA_ID, "schema_sha": S.SCHEMA_SHA, "symbol": sym, "as_of_ms": int(t.timestamp() * 1000),
                    "status": "OK", "missing": [], "h4_trend": dims["trend"], "h4_vol_regime": dims["vol"],
                    "btc_h4_trend": dims["btc"], "h4_vol_bucket": dims["volume"], "h4_structure": dims["structure"]}
            stop = 98.0 if side == "LONG" else 102.0
            if k < n_real:
                tid = "X%05d" % i
                f = {"risk_usdt": 2.0, "funding_coverage": {"complete": True}}
                pos = {"id": tid, "opened_at": t.isoformat(), "symbol": sym, "side": side, "setup_type": setup,
                       "entry_avg": 100.0, "initial_stop": stop, "initial_qty": 1.0, "features": f}
                rows.append(R.entry_row(pos, book=book, env=env, snapshot=snap, snapshot_status="OK"))
                rec = {"id": tid, "opened_at": t.isoformat(), "closed_at": (t + timedelta(hours=5)).isoformat(),
                       "symbol": sym, "side": side, "setup_type": setup, "r_multiple": r, "entry": 100.0, "quantity": 1.0,
                       "entry_fee": 0.05, "exit_fee": 0.05, "slippage_cost": 0.06, "funding_paid": 0.0,
                       "funding_received": 0.0, "features": f, "mfe_pct": 1.5, "mae_pct": 0.7,
                       "exit_reason": "TARGET" if r > 0 else "STOP"}
                rows.append(R.outcome_row(rec, book=book, rev=0, final=True, env=env))
            else:
                cid = "C%05d" % i
                src = {"id": cid, "book": R.BOOKS[book][0], "symbol": sym, "direction": side,
                       "signal_key": "signal_ts:%s" % cid, "features": {"setup_type": setup}, "created_at": t.isoformat(),
                       "entry": 100.0, "stop": stop, "tf_minutes": 240, "reason_not_opened": ["TOTAL_OPEN_RISK"],
                       "label_ts": t.isoformat()}
                g = r + 0.08
                out = {"label_version": "cf_label_v3", "r_gross": g, "r_multiple": g, "r_net": r, "cost_r": 0.08,
                       "exit_reason": "TARGET" if r > 0 else "STOP", "mfe_pct": 1.0, "mae_pct": 0.5, "bars": 3}
                rows.append(R.cf_row(src, book=book, rev=0, status="PENDING", env=env, snapshot=snap, snapshot_status="OK"))
                rows.append(R.cf_row({**src, "outcome": out}, book=book, rev=1, status="LABELLED", env=env))
    xp = state / "shared_experience"
    if xp.exists():                       # yalnız bu betiğin kendi demo klasörü (build() işaret dosyasını denetler)
        shutil.rmtree(xp)                 # yeniden çalıştırmada satırlar ikinci kez eklenmesin
    store = ExperienceStore(xp)
    written, rejected = store.append_rows(rows)
    if rejected:
        raise SystemExit("demo: ortak deneyim satırı reddedildi: %s" % (store.last_append,))
    sm = X.summary(X.scan(xp), now=now)
    (xp / X.SUMMARY_FILE).write_text(json.dumps(sm, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds: dict[str, int] = {}
    books: dict[str, int] = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        books[r["book"]] = books.get(r["book"], 0) + 1
    (xp / X.STATUS_FILE).write_text(json.dumps({
        "schema": "shared_experience_status_v1", "state": "OK", "last_step_at": iso(now_ms - 60_000), "steps": 412,
        "step_ms_p50": 38.0, "step_ms_p95": 91.0, "drafts": 0, "breaker": {"tripped": False},
        "counters": {"rows_total": len(rows), "rows_by_kind": kinds, "rows_by_book": books,
                     "snapshot_status_mix": {"OK": sum(1 for r in rows if r["kind"] != "xp_outcome" and r.get("rev", 0) == 0)},
                     "errors_total": 0},
        "store": {"disk_bytes": sum(p.stat().st_size for p in xp.rglob("*") if p.is_file()), "hot_lines": written}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    adv = xp / "advice"
    adv.mkdir(parents=True, exist_ok=True)
    banner = ("yalnız KAYIT — karar değişmez; ara bakış kanıt değildir; başarı yalnız önceden kayıtlı bakışlarda "
              "(L1/L2/L3)")
    (adv / "advisor_status.json").write_text(json.dumps({
        "schema": "shared_experience_advisor_status_v1", "advisor_id": "advisor_v1", "advisor_sha": "demo000000000000",
        "banner": banner, "state": "OK", "mode": "live", "effect": "NONE", "last_step_at": iso(now_ms - 60_000),
        "steps": 412, "step_ms_p50": 3.1, "step_ms_p95": 7.4, "advisor_born_ms": now_ms - 2 * D1,
        "advisor_born_at": iso(now_ms - 2 * D1), "index_mb": 0.21, "errors": 0,
        "position": {"last_row_id": "demo", "batch_clock_ms": now_ms - 60_000, "lag_rows": 0},
        "fold": {"pending_contexts": 6}, "breaker": {"tripped": False, "consecutive_errors": 0},
        "ring_24h": {"strategy_paper_box": {"real": {"GIR": 2, "GIRME": 3, "NOTR": 1, "LATE": 0},
                                            "cf": {"NOTR": 7, "VERI_AZ": 18, "GIRME": 3}},
                     "main": {"real": {"VERI_AZ": 2, "NOTR": 1}, "cf": {"VERI_AZ": 4}},
                     "strategy_paper_trend4h": {"real": {"VERI_AZ": 1}, "cf": {"VERI_AZ": 2}}},
        "running": {"ALL": {"N_T": 9, "N_G": 3, "U_mean": 0.11, "U_sum100": 9.9, "delta": 0.2},
                    "strategy_paper_box": {"N_T": 7, "N_G": 3, "U_mean": 0.14, "U_sum100": 9.8, "delta": 0.25},
                    "main": {"N_T": 2, "N_G": 0, "U_mean": 0.0, "U_sum100": 0.0, "delta": None}}},
        ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------- ana akış
def build(root: Path, *, seed: int = 7, now_ms: int | None = None) -> dict:
    if _foreign_state(root):
        raise ValueError("%s/state bu betiğin yazmadığı dosyalar içeriyor (%s yok)" % (root, MARKER))
    state, data = root / "state", root / "data"
    state.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    (state / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def w(name: str, doc) -> None:
        p = state / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    bars_1h: dict[str, dict[int, tuple]] = {}
    for base in PRICES:
        m15 = series_15m(base, now_ms=now_ms, seed=seed)
        for tf, step in TFS:
            rows = m15 if step == M15 else aggregate(m15, step)
            write_candles(data / ("binanceusdm_%s-USDT_%s.csv" % (base, tf)), rows)
            if tf == "1h":
                bars_1h[base] = {r[0]: r for r in rows}

    index = []
    ledgers = {}
    for key, name, trades in BOOKS:
        led = simulate_book(trades, bars_1h, PRICES, now_ms=now_ms)
        ledgers[key] = led
        if key == "main":
            w("futures_ledger.json", led.to_dict())
            continue
        w("%s/futures_ledger.json" % key, led.to_dict())
        w("%s.json" % key, book_summary(key, name, led, now_ms=now_ms))
        index.append({"key": key, "name": name, "summary_file": "%s.json" % key})
    w("strategy_paper_index.json", {"generated_at": iso(now_ms), "books": index})

    w("pattern_trader.json", {
        "schema_version": "pattern_trader_v1", "key": "pattern_trader", "name": "pattern_v1", "mode": "PAPER",
        "protocol_version": "pattern_protocol_v1.0.0", "generated_at": iso(now_ms - 45_000), "starting_equity": 100.0,
        "summary": {"equity_mtm": 100.86, "wallet_balance": 100.4, "unrealized": 0.46, "starting_equity": 100.0, "open": 1, "closed": 2},
        "positions": {"NEWX/USDT": {"symbol": "NEWX/USDT", "side": "LONG", "entry_avg": 1.2412, "entry": 1.2412, "qty": 18.0,
                                    "stop": 1.1780, "targets": [1.3620], "leverage": 1, "isolated_margin": 22.3,
                                    "opened_at": iso(now_ms - 2 * H1), "last_price": 1.284, "fees_paid": 0.02, "funding_net": 0.0,
                                    "plan_id": "a91c", "family": "C_COMPRESSION_BREAKOUT", "cohort": "1-7d", "age_h_at_entry": 51.4,
                                    "mae_pct": -0.9, "mfe_pct": 3.4}},
        "history_tail": [
            {"id": "F00002", "symbol": "FRSH/USDT", "side": "SHORT", "entry": 3.02, "exit_price": 2.81, "net_pnl": 1.24, "pnl": 1.24,
             "r_multiple": 1.55, "exit_reason": "hedef1", "opened_at": iso(now_ms - 26 * H1), "closed_at": iso(now_ms - 21 * H1),
             "fees": 0.04, "funding": 0.0, "features": {"cohort": "0-24h", "family": "B_LEVEL_REVERSAL", "plan_id": "b12"}},
            {"id": "F00003", "symbol": "ARB/USDT", "side": "LONG", "entry": 0.842, "exit_price": 0.801, "net_pnl": -0.84, "pnl": -0.84,
             "r_multiple": -1.0, "exit_reason": "stop", "opened_at": iso(now_ms - 14 * H1), "closed_at": iso(now_ms - 9 * H1),
             "fees": 0.03, "funding": 0.0, "features": {"cohort": "90d+", "family": "A_TREND_PULLBACK", "plan_id": "c33"}}],
        "counters": {"scans": 1284, "findings": 612, "confirmed": 96, "plans": 31, "triggered": 9, "opened": 3, "closed": 2,
                     "rejected": 6, "expired": 14, "broken": 5, "cancelled": 3, "data_rejected": 0},
        "plans_by_status": {"AWAITING_TRIGGER": 4, "EXPIRED": 14, "CLOSED": 2, "REJECTED": 6, "MANAGED": 1},
        "findings_by_status": {"CONFIRMED": 96, "AWAITING_CONFIRMATION": 21, "UNCONFIRMED": 140, "EXPIRED": 342, "BROKEN": 13},
        "symbol_scans": {
            "NEWX/USDT": {"last_scan_ms": now_ms - 45_000, "cohort": "1-7d", "n_15m": 204, "n_1h": 51, "n_4h": 12, "trend_4h": "UNKNOWN", "n_zones_1h": 2},
            "FRSH/USDT": {"last_scan_ms": now_ms - 60_000, "cohort": "0-24h", "n_15m": 62, "n_1h": 15, "n_4h": 3, "trend_4h": "UNKNOWN", "n_zones_1h": 0},
            "SOL/USDT": {"last_scan_ms": now_ms - 90_000, "cohort": "90d+", "n_15m": 240, "n_1h": 240, "n_4h": 240, "trend_4h": "UPTREND", "n_zones_1h": 4}},
        "active_plans": [
            {"plan_id": "a91c", "symbol": "NEWX/USDT", "family": "C_COMPRESSION_BREAKOUT", "side": "LONG", "status": "MANAGED",
             "created_at": iso(now_ms - 3 * H1), "created_at_ms": now_ms - 3 * H1, "expires_at": iso(now_ms + H1), "cohort": "1-7d",
             "age_h_at_plan": 50.2, "trigger": {"level": 1.2402, "tf": "15m", "rule": "close_above", "text_tr": "15m boğa kapanışı üstünde 1.2402"},
             "invalidation": {"level": 1.1810}, "stop": 1.178, "target": 1.362, "target_source": "measured_move", "rr_after_cost": 1.71,
             "position_id": "F00001", "reasons": []},
            {"plan_id": "d55e", "symbol": "SOL/USDT", "family": "A_TREND_PULLBACK", "side": "LONG", "status": "AWAITING_TRIGGER",
             "created_at": iso(now_ms - 40 * 60_000), "created_at_ms": now_ms - 2_400_000, "expires_at": iso(now_ms + 2 * H1), "cohort": "90d+",
             "age_h_at_plan": 26000.0, "trigger": {"level": 208.4, "tf": "15m", "rule": "close_above", "text_tr": "15m boğa kapanışı üstünde 208.4"},
             "invalidation": {"level": 199.1}, "stop": 197.8, "target": 229.6, "target_source": "opposing_1h_zone", "rr_after_cost": 1.82, "reasons": []},
            {"plan_id": "e77a", "symbol": "ARB/USDT", "family": "B_LEVEL_REVERSAL", "side": "SHORT", "status": "AWAITING_TRIGGER",
             "created_at": iso(now_ms - 75 * 60_000), "created_at_ms": now_ms - 4_500_000, "expires_at": iso(now_ms + H1), "cohort": "90d+",
             "trigger": {"level": 0.7912, "tf": "15m", "rule": "close_below", "text_tr": "15m ayı kapanışı altında 0.7912"},
             "invalidation": {"level": 0.8340}, "stop": 0.8361, "target": 0.7021, "target_source": "fallback_rr", "rr_after_cost": 1.63, "reasons": []}],
        "recent_plans": [
            {"plan_id": "f01b", "symbol": "TIA/USDT", "family": "A_TREND_PULLBACK", "side": "SHORT", "status": "REJECTED",
             "created_at": iso(now_ms - 5 * H1), "created_at_ms": now_ms - 5 * H1, "cohort": "90d+",
             "trigger": {"level": 4.21, "tf": "15m"}, "invalidation": {"level": 4.55}, "stop": 4.56, "target": 3.62,
             "rr_after_cost": 1.58, "reasons": ["TOTAL_OPEN_RISK"]},
            {"plan_id": "g22c", "symbol": "SEI/USDT", "family": "C_COMPRESSION_BREAKOUT", "side": "LONG", "status": "EXPIRED",
             "created_at": iso(now_ms - 7 * H1), "created_at_ms": now_ms - 7 * H1, "cohort": "30-90d",
             "trigger": {"level": 0.412, "tf": "15m"}, "invalidation": {"level": 0.388}, "stop": 0.386, "target": 0.455,
             "rr_after_cost": 1.55, "reasons": ["NOT_TRIGGERED_BEFORE_EXPIRY"]}],
        "data_gaps": {}, "cooldown_until_ms": {},
        "policy": {"families": ["A_TREND_PULLBACK", "B_LEVEL_REVERSAL", "C_COMPRESSION_BREAKOUT"], "entry_tf": "15m",
                   "structure_tf": "1h", "context_tf": "4h", "max_open_positions": 3, "p_win": "ÖLÇÜLMEDİ",
                   "risk_per_trade_pct": 2.0, "cost_round_trip_frac": 0.002},
        "note_tr": "FORMASYON PAPER TRADER — gerçek para yok; kârlılık kanıtı YOK."})
    w("pattern_scan.json", {"schema_version": "pattern_scan_v1", "enabled": True, "cycles": 642, "symbols_scanned": 25680,
                            "errors": 3, "last_error": "", "cycle_seconds": 60.0, "max_symbols_per_cycle": 40,
                            "last_cycle_at": iso(now_ms - 45_000), "last_universe_at": iso(now_ms - 900_000),
                            "coverage": {"eligible": 186, "scanned_at_least_once": 186, "never_scanned": 0,
                                         "never_scanned_sample": [], "max_staleness_s": 412.0, "median_staleness_s": 188.0},
                            "last_cycle": {"elapsed_s": 11.4, "budget": 40, "queue_depth": 186, "batch": 40,
                                           "by_group": {"open_position": 1, "pending_plan": 3, "new_listing": 8, "rotation": 28},
                                           "totals": {"scanned": 40, "findings": 7, "confirmed": 2, "plans": 1, "triggered": 0, "opened": 0}},
                            "universe": {"generated_at": iso(now_ms - 900_000),
                                         "counts": {"discovered": 512, "eligible": 186, "priority": 11,
                                                    "by_cohort": {"0-24h": 2, "1-7d": 4, "7-30d": 9, "30-90d": 18, "90d+": 479},
                                                    "onboard_unknown": 0},
                                         "changes": {"new_listings": 2, "delisted": 0, "status_changes": 0},
                                         "recent_new_listings": [
                                             {"symbol": "NEWX/USDT", "futures_first_trade": iso(now_ms - 51 * H1), "age_h": 51.4, "cohort": "1-7d"},
                                             {"symbol": "FRSH/USDT", "futures_first_trade": iso(now_ms - 14 * H1), "age_h": 14.2, "cohort": "0-24h"}]}})
    w("pattern_report.json", {"schema_version": "pattern_report_v1", "generated_at": iso(now_ms),
                              "status_tr": "ÇALIŞAN ÜRÜN + PAPER DENEYİ — kârlılık kanıtı YOK; sayılar ileri testin BUGÜNE KADARKİ kısmıdır.",
                              "totals": {"closed_trades": 2, "open_positions": 1, "verdict": "BELİRSİZ (örneklem yetersiz)",
                                         "outcome": {"n": 2, "mean_r": 0.275, "win_rate": 0.5, "profit_factor": 1.48,
                                                     "max_drawdown_r": -1.0, "ci95_mean_r": None},
                                         "costs": {"net_pnl_usdt": 0.4, "fees_usdt": 0.07, "funding_usdt": 0.0, "cost_usdt": 0.07}},
                              "min_trades_for_verdict": 30})
    since = iso(now_ms - 3 * D1)
    lm_books = [k for k, _n, _t in BOOKS]
    w("learning_mode.json", {"since": since, "books": lm_books})
    w("heartbeat.json", {"at": iso(now_ms - 20_000), "run_id": "demo", "pid": 0})
    w("health.json", {"state": "HEALTHY", "at": iso(now_ms - 20_000), "summary": "demo",
                      "learning_mode": {"enabled": True, "active": True, "reason": "", "since": since,
                                        "learning_mode_since": since, "books": lm_books}})
    w("mode.json", {"mode": "PAPER", "live_trading": False})
    w("risk.json", {"generated_at": iso(now_ms - 60_000), "last_decisions": []})
    experience_docs(state, now_ms=now_ms, seed=seed)
    return {"state": state, "data": data, "ledgers": ledgers}


def _foreign_state(root: Path) -> bool:
    """Hedefte bu betiğin yazmadığı dolu bir state klasörü var mı (işaret dosyası yok)?"""
    st = root / "state"
    return st.is_dir() and any(st.iterdir()) and not (st / MARKER).exists()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Panel ekran görüntüsü için SENTETİK demo durumu yazar (gerçek veri değil).")
    ap.add_argument("root", nargs="?", default="demo-panel", help="hedef klasör (state/ ve data/ altına yazılır)")
    ap.add_argument("--seed", type=int, default=7, help="rastgele yürüyüş tohumu (varsayılan 7)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if _foreign_state(root):
        print("HATA: %s/state bu betiğin yazmadığı dosyalar içeriyor (%s yok); üretim state'ine yazılmaz."
              % (root, MARKER), file=sys.stderr)
        return 2
    out = build(root, seed=args.seed)
    print("demo state:", out["state"])
    print("demo data :", out["data"])
    print("UYARI: bu veriler SENTETİKTİR — gerçek piyasa sonucu değildir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

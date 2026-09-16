# -*- coding: utf-8 -*-
"""PANEL EKRAN KANITI İÇİN SENTETİK DURUM — `python scripts/demo_panel_state.py <dizin>`.

Bu betik SAHTE veri üretir: ekran görüntüsü ve yerleşim doğrulaması içindir, GERÇEK PİYASA SONUCU DEĞİLDİR ve
üretim state dizinine yazılmamalıdır. Bot bu dosyaları okumaz; yalnız panel (salt okunur) okur.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

H1 = 3_600_000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def candles(path: Path, *, px0: float, n: int, step: int, end_ms: int, seed: int = 3) -> None:
    rows = ["timestamp,open,high,low,close,volume"]
    px = px0
    for i in range(n):
        ts = end_ms - (n - i) * step
        drift = math.sin((i + seed) / 11.0) * px0 * 0.012 + (i - n / 2) * px0 * 0.0004
        o = px
        c = px0 + drift
        hi, lo = max(o, c) * 1.004, min(o, c) * 0.996
        rows.append("%d,%.6f,%.6f,%.6f,%.6f,%.2f" % (ts, o, hi, lo, c, 1000 + i))
        px = c
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows), encoding="utf-8")


def main(root: Path) -> None:
    state, data = root / "state", root / "data"
    state.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    def w(name: str, doc) -> None:
        p = state / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    for base, px in (("BTC", 61250.0), ("ETH", 2980.0), ("SOL", 205.0), ("NEWX", 1.284)):
        for tf, step in (("15m", 900_000), ("1h", H1), ("4h", 4 * H1), ("1d", 24 * H1)):
            candles(data / ("binanceusdm_%s-USDT_%s.csv" % (base, tf)), px0=px, n=320, step=step, end_ms=now_ms, seed=len(base))

    main_pos = {"BTC/USDT": {"symbol": "BTC/USDT", "side": "LONG", "entry_avg": 60120.0, "entry": 60120.0, "qty": 0.0004,
                             "stop": 58400.0, "targets": [64000.0], "leverage": 2, "isolated_margin": 12.02,
                             "opened_at": iso(now_ms - 9 * H1), "last_price": 61250.0, "fees_paid": 0.03, "funding_net": -0.01},
                "ETH/USDT": {"symbol": "ETH/USDT", "side": "SHORT", "entry_avg": 3040.0, "entry": 3040.0, "qty": 0.006,
                             "stop": 3140.0, "targets": [], "leverage": 2, "isolated_margin": 9.12,
                             "opened_at": iso(now_ms - 3 * H1), "last_price": 2980.0, "fees_paid": 0.02, "funding_net": 0.004}}
    hist = [{"id": "F00011", "symbol": "SOL/USDT", "side": "LONG", "entry": 192.4, "exit_price": 201.8, "net_pnl": 1.82,
             "pnl": 1.82, "r_multiple": 0.91, "exit_reason": "hedef1", "opened_at": iso(now_ms - 40 * H1), "closed_at": iso(now_ms - 26 * H1), "fees": 0.05, "funding": -0.01},
            {"id": "F00012", "symbol": "LINK/USDT", "side": "LONG", "entry": 14.8, "exit_price": 14.1, "net_pnl": -1.05,
             "pnl": -1.05, "r_multiple": -1.0, "exit_reason": "stop", "opened_at": iso(now_ms - 30 * H1), "closed_at": iso(now_ms - 22 * H1), "fees": 0.04, "funding": 0.0},
            {"id": "F00013", "symbol": "AVAX/USDT", "side": "SHORT", "entry": 27.4, "exit_price": 26.1, "net_pnl": 0.94,
             "pnl": 0.94, "r_multiple": 0.62, "exit_reason": "kısmi", "opened_at": iso(now_ms - 20 * H1), "closed_at": iso(now_ms - 12 * H1), "fees": 0.03, "funding": 0.0}]
    w("futures_ledger.json", {"schema_version": "futures_v2", "starting_equity": "100", "wallet_balance": "101.71",
                              "equity": "101.71", "max_positions": 3, "updated_at": iso(now_ms),
                              "positions": main_pos, "history": hist})
    w("strategy_paper_index.json", {"generated_at": iso(now_ms), "books": [
        {"key": "strategy_paper", "name": "t2_trend_regime", "summary_file": "strategy_paper.json"},
        {"key": "strategy_paper_m2", "name": "m2_tsmom28", "summary_file": "strategy_paper_m2.json"}]})
    sp_pos = {"SOL/USDT": {"symbol": "SOL/USDT", "side": "LONG", "entry_avg": 196.2, "entry": 196.2, "qty": 0.12,
                           "stop": 181.0, "targets": [], "leverage": 1, "isolated_margin": 23.5,
                           "opened_at": iso(now_ms - 52 * H1), "last_price": 205.0, "fees_paid": 0.02, "funding_net": -0.02}}
    for key, name, eq, pos in (("strategy_paper", "t2_trend_regime", 104.31, sp_pos), ("strategy_paper_m2", "m2_tsmom28", 98.7, {})):
        w("%s/futures_ledger.json" % key, {"schema_version": "futures_v2", "starting_equity": "100",
                                           "wallet_balance": "%.2f" % (eq - 1.05 if pos else eq), "positions": pos,
                                           "history": [] if pos else [dict(hist[1], symbol="DOGE/USDT", id="F00021")]})
        w("%s.json" % key, {"schema_version": "strategy_paper_v1", "key": key, "name": name, "generated_at": iso(now_ms - 240_000),
                            "starting_equity": 100.0, "regime": "UP",
                            "summary": {"equity_mtm": eq, "wallet_balance": eq - 1.05 if pos else eq, "unrealized": 1.05 if pos else 0.0,
                                        "starting_equity": 100.0, "open": len(pos), "closed": 0 if pos else 1},
                            "positions": pos, "history_tail": [] if pos else [dict(hist[1], symbol="DOGE/USDT", id="F00021")],
                            "counters": {"opened": 1, "closed": 0 if pos else 1, "tours": 412, "data_rejected": 0},
                            "rejections": {}, "data_gaps": {}, "note_tr": "KÂĞIT İLERİ TEST — gerçek para yok."})
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
    w("heartbeat.json", {"at": iso(now_ms - 20_000), "run_id": "demo", "pid": 0})
    w("health.json", {"state": "HEALTHY", "at": iso(now_ms - 20_000), "summary": "demo"})
    w("mode.json", {"mode": "PAPER", "live_trading": False})
    w("risk.json", {"generated_at": iso(now_ms - 60_000), "last_decisions": []})
    print("demo state:", state)
    print("demo data :", data)
    print("UYARI: bu veriler SENTETİKTİR — gerçek piyasa sonucu değildir.")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo-panel").resolve())

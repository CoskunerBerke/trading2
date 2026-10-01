# -*- coding: utf-8 -*-
"""Teşhis: Part B replay'inde (gerçek arşiv) likidasyonla kapanan işlemlerin ayrıntısı. PnL raporu DEĞİL.

Kullanım: python diag_liq.py <kod_kökü> <kural> <OFF|ENFORCE> <çıktı.json>
"""
import datetime as _dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
name, mode, out = sys.argv[2], sys.argv[3], Path(sys.argv[4])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "docs" / "review" / "evidence-2026-09-22-shared"))
import real_archive_structures as R  # noqa: E402

from tradingbot.config import load_config  # noqa: E402
from tradingbot.history import HistoryStore  # noqa: E402
from tradingbot.paper_rules import build_params, replay_strategy  # noqa: E402
from tradingbot.replay import HistoricalReplay  # noqa: E402


def _ms(v):
    return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


syms = R._universe()
tmp = Path(tempfile.mkdtemp(prefix="diag_liq_"))
try:
    cfg = load_config(str(root / "config.yaml"))
    sp = cfg.v3.strategy_paper
    book = {"name": sp.name, "atr_mult": sp.atr_mult, "rule_params": sp.rule_params,
            "breakeven_at_mfe_r": sp.breakeven_at_mfe_r, "starting_equity_usdt": sp.starting_equity_usdt}
    if name != sp.name:
        book = next(dict(x) for x in (sp.extra or []) if x.get("name") == name)
    params = build_params(name, atr_mult=float(book.get("atr_mult") or 3.0), rule_params=dict(book.get("rule_params") or {}))
    cfg.v3.futures_v3.breakeven_at_mfe_r = float(book.get("breakeven_at_mfe_r") or 0.0)
    cfg.futures.starting_equity_usdt = float(book.get("starting_equity_usdt") or 100.0)
    cfg.exchange.cache_dir = str(R.ARCH.parent)          # arşivin DOĞRULANMIŞ borsa kuralları (varsayılan değil)
    rp = HistoricalReplay(cfg, run_id="diag_%s_%s" % (name, mode.lower()), store=HistoryStore(R.ARCH), symbols=syms,
                          market="futures", tf="4h", seed=0, state_root=tmp, pattern_engine=None, start_ms=_ms("2025-09-19"),
                          end_ms=_ms("2026-09-19"), economics_gate=False, spot_listed=set(syms),
                          strategy=replay_strategy(name, params=params, mode=mode), structures_mode=None)
    rp.load(require_all=True)
    assert rp.filters_source == "binance_api", rp.filters_source
    rp.run(windows=[])
    trades = rp.ledger2.history_dicts()
    keep = []
    for t in trades:
        f = t.get("features") or {}
        keep.append({k: t.get(k) for k in ("id", "symbol", "side", "opened_at", "closed_at", "exit_reason", "entry_avg", "exit_avg",
                                            "initial_stop", "stop", "leverage", "liquidation_price", "qty", "notional", "margin",
                                            "net_pnl", "r_multiple", "exit_fill", "targets")}
                    | {"structure": {k: (f.get("structure") or {}).get(k) for k in ("action", "pattern_id", "name", "timeframe", "stop",
                                                                                   "confirmed_at_ms", "reason_code")} if f.get("structure") else None,
                       "exit_structure": (f.get("exit_structure") or {}).get("reason_code") if f.get("exit_structure") else None})
    liq_full = [t for t in trades if t.get("exit_reason") == "likidasyon"]
    out.write_text(json.dumps({"code_root": str(root), "rule": name, "mode": mode, "trades": keep, "liq_full": liq_full,
                               "trade_keys": sorted(trades[0].keys()) if trades else []}, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    print("yazıldı", out, len(keep))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

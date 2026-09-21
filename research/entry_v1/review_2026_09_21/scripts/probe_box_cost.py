# -*- coding: utf-8 -*-
"""ÖLÇÜM (tahmin değil): tam replay'i 5m'de koşturmak ne kadar sürüyor?

Küçük bir pencerede koşup karar başına süreyi ölçer ve tam süpürmenin maliyetini ondan çıkarır.
Sonuç, süpürmenin ağır replay ile mi yoksa ayrı bir hızlı koşucuyla mı yapılacağını BELİRLER.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(r"C:/Users/berke/wt-entry")))

import box_rules  # noqa: E402
from tradingbot.box_theory import BoxParams  # noqa: E402

WT = Path(r"C:/Users/berke/wt-entry")
CACHE = r"C:/Users/berke/wt-ten/data"


def probe(symbols, date_from: str, date_to: str, tf: str = "5m") -> dict:
    import datetime as dt
    import os
    os.environ["TRADINGBOT_CACHE_DIR"] = CACHE
    from tradingbot.config import load_config
    from tradingbot.history import HistoryStore
    from tradingbot.replay import HistoricalReplay, walk_forward_windows
    from tradingbot.replay.research import resolve_replay_dir

    def _ms(v):
        return int(dt.datetime.fromisoformat(v).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    cfg = load_config(str(WT / "config.yaml"))
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    run_id = "probe_box_%s" % tf
    rdir = resolve_replay_dir(cfg.state_path, run_id, None)
    strat = box_rules.build(BoxParams(leverage=3))
    rp = HistoricalReplay(cfg, run_id=run_id, store=store, symbols=list(symbols), market="futures",
                          tf=tf, seed=0, state_root=rdir.parent, pattern_engine=None,
                          start_ms=_ms(date_from), end_ms=_ms(date_to), decision_stride=1,
                          economics_gate=False, spot_listed=set(symbols), entry_rule=None,
                          entry_trigger=False, legacy_veto=False, strategy=strat)
    t0 = time.time()
    rp.load()
    t_load = time.time() - t0
    ws = walk_forward_windows(rp.result.start_ms, rp.result.end_ms, train_days=180,
                              test_days=30, purge_bars=6, embargo_bars=6, tf=tf)
    t1 = time.time()
    res = rp.run(windows=ws)
    t_run = time.time() - t1
    n_dec = int(res.n_decisions or 0)
    return {"tf": tf, "symbols": len(symbols), "from": date_from, "to": date_to,
            "load_s": round(t_load, 1), "run_s": round(t_run, 1),
            "n_decisions": n_dec, "n_opened": int(res.n_opened or 0),
            "us_per_decision": round(t_run / max(n_dec, 1) * 1e6, 1),
            "history_root": str(store.root),
            "rejection_keys": sorted((res.rejections or {}))[:8]}


if __name__ == "__main__":
    syms = ["BTC/USDT", "ETH/USDT"]
    out = probe(syms, "2026-08-01", "2026-08-08")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    # Tam süpürme maliyeti: 10 sembol x 3 pencere (~2 yıl) x kol sayısı
    per_sym_year = 365 * 24 * 12                       # 5m bar / yıl
    dec_full = per_sym_year * 2 * 10                   # 2 yıllık pencere, 10 sembol
    s_per_arm = dec_full * (out["run_s"] / max(out["n_decisions"], 1))
    print("\nTAHMIN (olculen hizdan): bir kol / bir 2-yillik pencere ~ %.0f dk" % (s_per_arm / 60))
    print("3 pencere x 12 kol ~ %.1f saat" % (s_per_arm * 3 * 12 / 3600))

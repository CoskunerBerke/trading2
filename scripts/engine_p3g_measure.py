#!/usr/bin/env python3
"""P3g ölçümü (docs/ENGINE_BRAIN_V1.md §5.3 karar kuralı, §8.4 P3g notu): büyük sentetik dünyada (P2b dünyası + P3 yükü)
TAM gecenin süresi ve tepe belleği (`VmHWM`). Yalnız sentetik veri, ağ yok; PAPER araştırma ölçümüdür, kanıt değil.

Dünya geçici bir klasörde kurulur (`tests/research_engine_p3g_fixtures.big_p3_world`); her gece AYRI bir alt süreçte
(`night.run_night`, sahte S0 dış dünyası `FakeHost`) koşar ki `VmHWM` yalnız o gecenin süreci olsun. Gece birimiyle aynı
bellek sınırı ortamı verilir (`ENGINE_EXPECTED_MEMORY_MAX` = 768 MiB; MEMORY_GUARD 0,7 × sınır). Çıktı: gece başına aşama
süreleri, VmHWM, koruma özeti ve S4w kuyruğu; sonda × 3 VPS tahmini (§5.3, ölçüm notu 1'in çarpanı).

    PYTHONPATH=. python scripts/engine_p3g_measure.py --coins 40 --nights 2
    PYTHONPATH=. python scripts/engine_p3g_measure.py --box-5m-days 365   # + tek Box biriminin 5m bellek yoklaması
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

MEM_MAX = 805306368          # 768 MiB = gece birimi MemoryMax (deploy/tradingbot-engine-night.service)
VPS_FACTOR = 3.0

CHILD = r"""
import json, os, sys
sys.path.insert(0, os.environ["P3G_ROOT"]); sys.path.insert(0, os.path.join(os.environ["P3G_ROOT"], "tests"))
from pathlib import Path
from research_engine_fixtures import FakeHost, FakeVps
from research_engine_p3g_fixtures import config_text, night_after_seal
from tradingbot.research_engine import night as N
from tradingbot.research_engine.paths import EnginePaths
d = Path(os.environ["P3G_DIR"])
host = FakeHost(d / "host", config_text=config_text())
paths = EnginePaths.for_state(d / "w" / "data" / "state")
kw = host.kw()
kw["env"] = {"ALLOW_LIVE_TRADING": "false", "ENGINE_EXPECTED_MEMORY_MAX": os.environ["P3G_MEM"]}
if os.environ.get("P3G_S4W_MAX_S"):              # test: S4w (ve aynı paylı S4x) bu gecede en çok bu kadar sürer
    from datetime import timedelta
    N.S4W_RESERVE = N.S4W_RESERVE_GATE_A = N.DEADLINE_AFTER - timedelta(minutes=3) - timedelta(
        seconds=float(os.environ["P3G_S4W_MAX_S"]))
st = N.run_night(paths, now=night_after_seal(int(os.environ["P3G_DAY"])), **kw)
out = {"result": st["result"], "flags": st["flags"], "resources": st["resources"],
       "stages": {k: {"status": v.get("status"), "duration_s": v.get("duration_s"), "reason": (v.get("reason") or "")[:160]}
                  for k, v in st["stages"].items()},
       "s4w": (st["stages"].get("S4w") or {}).get("result"), "s4x": (st["stages"].get("S4x") or {}).get("result"),
       "s6": {k: v for k, v in ((st["stages"].get("S6") or {}).get("result") or {}).items() if k != "parity_b"},
       "guard": (st.get("p3") or {}).get("guard")}
print("@@" + json.dumps(out, default=str))
"""

BOX = r"""
import json, os, sys, time
sys.path.insert(0, os.environ["P3G_ROOT"])
from tradingbot.research_engine import synthetic as SY, wfqueue as WQ
from tradingbot.research_engine.nightp3 import proc_status_bytes
days = int(os.environ["P3G_BOX_DAYS"])
w = SY.explore_world(["BTC"], seed=5, seal_ms=SY.T0 + 3 * 365 * 86400000, fwd_days=0, pre_1d_days=60, pre_1h_days=days,
                     pre_5m_days=days)
bars = w["BTC"]["bars"]
h = WQ.History(coin="BTC", bars={k: list(v) for k, v in bars.items()}, funding=w["BTC"]["funding"], markpx=w["BTC"]["markpx_1h"],
               parts={})
del w, bars
u = next(u for u in WQ.build_units(list(__import__("tradingbot.research_engine.universe", fromlist=["x"]).ENTRY_UNIVERSE_FROZEN))
         if u.key == "FAM_BOX@BTC")
r0 = proc_status_bytes("VmRSS")
t = time.time()
end = max(int(r["timestamp"]) for r in h.bars["5m"]) + 300000
lines, meta = WQ.run_unit(u, h, h, tick=0.01, month="x", end_ms=end, spec_of=lambda v: None)
print("@@" + json.dumps({"bars_5m": len(h.bars["5m"]), "seconds": round(time.time() - t, 1), "trades": meta["trades"],
                         "rss_before": r0, "vm_hwm": proc_status_bytes("VmHWM"), "rss_after": proc_status_bytes("VmRSS")}))
"""


def _child(code: str, env: dict) -> dict:
    t = time.time()
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, **env}, timeout=7200)
    line = next((ln for ln in cp.stdout.splitlines() if ln.startswith("@@")), None)
    if cp.returncode != 0 or line is None:
        raise SystemExit(f"alt süreç düştü ({cp.returncode}):\n{cp.stdout[-3000:]}\n{cp.stderr[-3000:]}")
    d = json.loads(line[2:])
    d["wall_child_s"] = round(time.time() - t, 1)
    return d


def _mib(b) -> str:
    return "—" if not b else f"{b / 1048576:.0f} MiB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coins", type=int, default=40)
    ap.add_argument("--nights", type=int, default=2)
    ap.add_argument("--trades", type=int, default=600)
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--history-days", type=int, default=60)
    ap.add_argument("--fwd-days", type=int, default=7)
    ap.add_argument("--pre-1d", type=int, default=620)
    ap.add_argument("--pre-1h", type=int, default=130)
    ap.add_argument("--pre-5m", type=int, default=30)
    ap.add_argument("--box-5m-days", type=int, default=0)
    ap.add_argument("--keep", default=None, help="dünyanın kurulacağı klasör (verilmezse geçici, sonra silinir)")
    a = ap.parse_args(argv)
    from research_engine_fixtures import FakeVps
    from research_engine_p3g_fixtures import COINS40, big_p3_world
    from tradingbot.research_engine import selfcheck as SC
    from datetime import datetime, timezone
    base = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="p3g-measure-"))
    t = time.time()
    v = FakeVps(base / "w")
    info = big_p3_world(v, v.state, coins=COINS40[:a.coins], n_trades=a.trades, days=a.days, history_days=a.history_days,
                        fwd_days=a.fwd_days, pre_1d_days=a.pre_1d, pre_1h_days=a.pre_1h, pre_5m_days=a.pre_5m)
    SC.register_epoch(v.paths, "b" * 40, datetime(2020, 1, 1, tzinfo=timezone.utc), "t", code_hash=SC.engine_code_hash())
    print(f"dünya: {a.coins} coin · P2b işlem {sum(info['p2b'].values())} · kurulum {time.time() - t:.0f} sn · {base}")
    rows = []
    for k in range(a.nights):
        day = a.fwd_days + 1 + k
        v.save(__import__("research_engine_p3g_fixtures").night_after_seal(day) - __import__("datetime").timedelta(minutes=1))
        d = _child(CHILD, {"P3G_ROOT": str(ROOT), "P3G_DIR": str(base), "P3G_MEM": str(MEM_MAX), "P3G_DAY": str(day)})
        rows.append(d)
        r = d["resources"]
        st = d["stages"]
        print(f"\ngece {k + 1} (mühür + {day} gün): {d['result']} · duvar {r.get('wall_s')} sn · VmHWM "
              f"{_mib(r.get('vm_hwm_bytes'))} · ru_maxrss {_mib(r.get('maxrss_bytes'))} · bayraklar {', '.join(d['flags'])}")
        print("   " + " · ".join(f"{s} {x['status']} {x['duration_s'] or 0:.1f}s" for s, x in st.items()
                                 if x["status"] not in ("NOT_PLANNED", None)))
        for s, x in st.items():
            if x["status"] == "SKIPPED":
                print(f"   {s} atlandı: {x['reason']}")
        w = d.get("s4w") or {}
        print(f"   S4w: {w.get('stop')} · birim {w.get('units_done')} (+ veri yok {w.get('units_no_data')}) · kuyruk "
              f"{(w.get('backlog') or {}).get('done')}/{(w.get('backlog') or {}).get('total')} · işlem {w.get('trades')}")
        x = d.get("s4x") or {}
        print(f"   S4x: {x.get('status')} · yeni işlem {x.get('trades_new')} · {x.get('seconds')} sn")
        g = d.get("guard") or {}
        print(f"   koruma: sınır {_mib(g.get('limit_bytes'))} × {g.get('fraction')} · en çok RSS {_mib(g.get('max_rss_bytes'))} · "
              f"ihlal {g.get('trips')}")
    if a.box_5m_days:
        b = _child(BOX, {"P3G_ROOT": str(ROOT), "P3G_BOX_DAYS": str(a.box_5m_days)})
        print(f"\nBox birimi (FAM_BOX, 5m {b['bars_5m']} bar ≈ {a.box_5m_days} gün): {b['seconds']} sn · {b['trades']} işlem · RSS "
              f"{_mib(b['rss_before'])} → VmHWM {_mib(b['vm_hwm'])}")
    hwm = max((d["resources"].get("vm_hwm_bytes") or 0) for d in rows)
    print(f"\nen yüksek VmHWM {_mib(hwm)} · 0,7 × 768 MiB = {_mib(0.7 * MEM_MAX)} · 0,8 × 768 MiB = {_mib(0.8 * MEM_MAX)}")
    fixed = ("S0", "S1a", "S3", "S1b", "S2", "S4x", "S6", "S7", "S7b")
    for i, d in enumerate(rows, 1):
        sec = sum((d["stages"].get(s) or {}).get("duration_s") or 0 for s in fixed)
        s4w = (d["stages"].get("S4w") or {}).get("duration_s") or 0
        print(f"gece {i}: S4w dışı aşamalar {sec:.0f} sn (× {VPS_FACTOR:g} ≈ {sec * VPS_FACTOR / 60:.1f} dk VPS) · S4w {s4w:.0f} sn "
              f"(× {VPS_FACTOR:g} ≈ {s4w * VPS_FACTOR / 60:.1f} dk; gecede payla sınırlı)")
    if not a.keep:
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

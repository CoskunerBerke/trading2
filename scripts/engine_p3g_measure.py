#!/usr/bin/env python3
"""P3g ölçümü (docs/ENGINE_BRAIN_V1.md §5.3 karar kuralı, §8.4 P3g notu): büyük sentetik dünyada (P2b dünyası + P3 yükü)
TAM gecenin süresi ve tepe belleği (`VmHWM`). Yalnız sentetik veri, ağ yok; PAPER araştırma ölçümüdür, kanıt değil.

Dünya geçici bir klasörde kurulur (`tests/research_engine_p3g_fixtures.big_p3_world`); her gece AYRI bir alt süreçte
(`night.run_night`, sahte S0 dış dünyası `FakeHost`) koşar ki `VmHWM` yalnız o gecenin süreci olsun. Gece birimiyle aynı
bellek sınırı ortamı verilir (`ENGINE_EXPECTED_MEMORY_MAX` = 768 MiB; MEMORY_GUARD 0,7 × sınır). Çıktı: gece başına aşama
süreleri, VmHWM, koruma özeti ve S4w kuyruğu; sonda × 3 VPS tahmini (§5.3, ölçüm notu 1'in çarpanı).

    PYTHONPATH=. python scripts/engine_p3g_measure.py --coins 40 --nights 2
    PYTHONPATH=. python scripts/engine_p3g_measure.py --box-5m-days 365   # + tek Box biriminin 5m bellek yoklaması
    PYTHONPATH=. python scripts/engine_p3g_measure.py --ag-5m-days 365    # + bir sembolün 12 AG biriminin yoklaması

S1d `LIB_AG_V1` varsayılan olarak dahildir (`--no-ag`: yalnız LIB_V1): dünyaya 15m barları ve geç listelenmiş XAUUSDT
eklenir; AG keşfi, AG birimleri ve AG değerlendirme birimi gecenin S4x/S4w payını paylaşır. `--ag-5m-days N`: tek sembolün
12 AG biriminin N günlük gerçek uzunlukta 5m/15m geçmişte süresi ve tepe belleği; sonda sembol-yıl başına süreden 41
sembolün tam geçmişli ilk kurulumunun × 3 VPS gece tahmini (S4w nominal payı 70 dk).
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
       "guard": (st.get("p3") or {}).get("guard"), "ag": (st.get("p3") or {}).get("ag")}
print("@@" + json.dumps(out, default=str))
"""

AG = r"""
import json, os, sys, time
sys.path.insert(0, os.environ["P3G_ROOT"]); sys.path.insert(0, os.path.join(os.environ["P3G_ROOT"], "tests"))
from research_engine_p3g_fixtures import add_ag
from tradingbot.research_engine import synthetic as SY, wfqueue as WQ, lib_ag as LA
from tradingbot.research_engine.nightp3 import proc_status_bytes
days = int(os.environ["P3G_AG_DAYS"])
w = SY.explore_world(["BTC"], seed=5, seal_ms=SY.T0 + 3 * 365 * 86400000, fwd_days=0, pre_1d_days=420, pre_1h_days=days,
                     pre_5m_days=days)
add_ag(w)
bars = w["BTC"]["bars"]
h = WQ.History(coin="BTC", bars={k: list(v) for k, v in bars.items()}, funding=w["BTC"]["funding"], markpx=w["BTC"]["markpx_1h"],
               parts={})
del w, bars
end = max(int(r["timestamp"]) for r in h.bars["5m"]) + 300000
r0 = proc_status_bytes("VmRSS")
out = {"bars_5m": len(h.bars["5m"]), "bars_15m": len(h.bars["15m"]), "units": {}, "rss_before": r0}
for u in WQ.ag_units("BTC"):
    t = time.time()
    lines, meta = WQ.run_unit(u, h, h, tick=0.01, month="x", end_ms=end, spec_of=lambda v: None)
    out["units"][u.key] = {"seconds": round(time.time() - t, 1), "trades": meta["trades"], "status": meta["status"],
                           "variants": len(u.variants), "signal_tf": u.signal_tf, "bytes": sum(len(x) for x in lines)}
    del lines
out["vm_hwm"] = proc_status_bytes("VmHWM")
print("@@" + json.dumps(out))
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
    ap.add_argument("--ag-5m-days", type=int, default=0)
    ap.add_argument("--no-ag", action="store_true", help="S1d AG eklerini dünyaya koyma (yalnız LIB_V1 yükü)")
    ap.add_argument("--ag-history-years", type=float, default=6.7,
                    help="AG ilk kurulum tahmini: coin başına 5m geçmişi (yıl; 2020-01 → 2026-09 ≈ 6,7)")
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
                        fwd_days=a.fwd_days, pre_1d_days=a.pre_1d, pre_1h_days=a.pre_1h, pre_5m_days=a.pre_5m,
                        ag=not a.no_ag)
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
        if w.get("ag"):
            wa = w["ag"]
            print(f"   S4w AG: koştu {wa.get('run')} · birim {wa.get('units_built')} (verili {wa.get('units_done')}) / planda "
                  f"{wa.get('units_planned')} · işlem {wa.get('trades')} · değerlendirilen varyant {wa.get('evaluated', '—')}")
        x = d.get("s4x") or {}
        print(f"   S4x: {x.get('status')} · yeni işlem {x.get('trades_new')} · {x.get('seconds')} sn")
        xa = x.get("ag") or {}
        if xa:
            print(f"   S4x AG: {xa.get('status') or xa.get('skipped')} · yeni işlem {xa.get('trades_new')} · {xa.get('seconds')} sn")
        ag = d.get("ag") or {}
        if ag:
            print(f"   AG: mühür {ag.get('seal')} · deneme satırları {(ag.get('trials') or {}).get('status')} · koştu {ag.get('run')}")
        g = d.get("guard") or {}
        print(f"   koruma: sınır {_mib(g.get('limit_bytes'))} × {g.get('fraction')} · en çok RSS {_mib(g.get('max_rss_bytes'))} · "
              f"ihlal {g.get('trips')}")
    if a.box_5m_days:
        b = _child(BOX, {"P3G_ROOT": str(ROOT), "P3G_BOX_DAYS": str(a.box_5m_days)})
        print(f"\nBox birimi (FAM_BOX, 5m {b['bars_5m']} bar ≈ {a.box_5m_days} gün): {b['seconds']} sn · {b['trades']} işlem · RSS "
              f"{_mib(b['rss_before'])} → VmHWM {_mib(b['vm_hwm'])}")
    if a.ag_5m_days:
        g = _child(AG, {"P3G_ROOT": str(ROOT), "P3G_AG_DAYS": str(a.ag_5m_days)})
        tot = sum(x["seconds"] for x in g["units"].values())
        print(f"\nAG birimleri (BTC, 5m {g['bars_5m']} / 15m {g['bars_15m']} bar ≈ {a.ag_5m_days} gün): toplam {tot:.1f} sn · RSS "
              f"{_mib(g['rss_before'])} → VmHWM {_mib(g['vm_hwm'])}")
        for k, x in sorted(g["units"].items(), key=lambda kv: -kv[1]["seconds"]):
            print(f"   {k}: {x['seconds']} sn · {x['variants']} varyant · {x['trades']} işlem · {x['bytes'] / 1e6:.1f} MB önbellek")
        per_year = tot * 365.0 / a.ag_5m_days
        sym_years = 40 * a.ag_history_years + 0.8                  # XAUUSDT 2025-12-11'den (≈ 0,8 yıl)
        need = per_year * sym_years * VPS_FACTOR
        nominal = 70 * 60.0
        print(f"   tahmin: sembol-yıl başına {per_year:.0f} sn (bu makine) · ilk tam AG kurulumu {sym_years:.0f} sembol-yıl × "
              f"{VPS_FACTOR:g} ≈ {need / 3600:.1f} sa VPS ≈ {need / nominal:.1f} gecelik S4w nominal payı (70 dk; LIB_V1 "
              f"birimleri ayrıca); her ay yeniden kurulur")
    hwm = max(((d["resources"].get("vm_hwm_bytes") or 0) for d in rows), default=0)
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

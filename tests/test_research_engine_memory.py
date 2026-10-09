# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — gece biriminin bellek bütçesi (docs/SYSTEM_LEARNING_ENGINE_V1.md §2.5; VPS kabul 5:
motor `memory.peak` ≤ 0,8 × MemoryMax; birim P1a'da `MemoryHigh=400M`, `MemoryMax=512M`; P2'den (inceleme M6, 2026-10-08)
`MemoryHigh=640M`, `MemoryMax=768M`).

Rotasyon tavanlarında (her vadeli defter 5000 `history` / 2000 `entries`, 8 vadeli defter + ana botun spot defteri)
iki tam gece (`night.run_night`: S0 → S1a → S3 → S7 → S7b) AYRI bir süreçte, üretim import yoluyla (`import
tradingbot.cli`; pandas dahil) koşulur ve sürecin tepe RSS'i (`ru_maxrss`) ölçülür. Bütçe 0,6 × MemoryMax: cgroup
`memory.peak` sayfa önbelleğini de sayar (ledger okuma, arşiv yazımı); kalan 0,2 × MemoryMax o paydır. Ledger'lar
worker'ın kendi `FuturesLedgerV2`/`SpotLedger` kayıtlarından türetilir (kimlik ve zamanlar çoğaltılarak; hızlı).
Ölçülen değerler `closes` modülünün okuma 9'una yazılır.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeVps, at, iso, trade  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MEMORY_MAX = 805306368                       # deploy/tradingbot-engine-night.service MemoryMax=768M (P2, inceleme M6)
BUDGET = int(0.6 * MEMORY_MAX)
BOOKS = ("", "strategy_paper", "strategy_paper_m2", "strategy_paper_box", "pattern_trader", "strategy_paper_trend4h",
         "strategy_paper_candle4h", "strategy_paper_candle4h_strict")
N_HIST, N_ENT = 5000, 2000

CHILD = r'''
import json, resource, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/tests"]
import tradingbot.cli  # noqa: F401  üretim yolu (python -m tradingbot engine-night) bunu yükler
from datetime import datetime, timezone
from research_engine_fixtures import FakeHost, FREE_BYTES
from tradingbot.research_engine import night as N, selfcheck as SC
from tradingbot.research_engine.paths import EnginePaths
def _peak():
    # Bu sürecin kendi tepe RSS'i (VmHWM, exec ile sıfırlanır). ru_maxrss Linux'ta fork+exec'te ebeveynin tepesini
    # taşır: tam test koşusunda büyük pytest sürecinin ~650 MiB'ı çocuğa yazılıyordu (2026-10-06).
    try:
        for line in open("/proc/self/status", encoding="ascii"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
data = Path(sys.argv[2])
paths = EnginePaths(data=data, state=data / "state")
host = FakeHost(data.parent)
# A/B penceresi dışı (iki gece de tam plan: S0, S1a, S3, S7, S7b)
SC.register_epoch(paths, "b" * 40, datetime(2020, 1, 1, tzinfo=timezone.utc), "mem", code_hash=SC.engine_code_hash())
out = {"rss_after_import": _peak()}
for i, when in enumerate(("2026-10-01T01:40:00+00:00", "2026-10-02T01:40:00+00:00")):
    st = N.run_night(paths, now=datetime.fromisoformat(when), **host.kw())
    out[f"night{i + 1}"] = {"result": st["result"], "rss": _peak(),
                            "stages": {k: v.get("status") for k, v in st["stages"].items()},
                            "new_closes": sum(b.get("new_closes", 0) for b in
                                              ((st["stages"].get("S1a") or {}).get("result") or {}).get("books", {}).values())}
print(json.dumps(out))
'''


def _caps_state(root: Path) -> Path:
    """8 vadeli defter × (5000 kayıt, 2000 hareket) + spot (5000 / 2000), worker'ın kendi kayıtlarından çoğaltılarak."""
    v = FakeVps(root)
    led = v.fut()
    t0 = at("2026-09-20", 1)
    for i in range(6):
        trade(led, "ETH/USDT", t0 + timedelta(minutes=10 * i), 100.0, 101.0 + i * 0.1)
    sp = v.spot(cash="100000")
    sp.market_buy("ETH/USDT", qty=D("1"), ref_price=D("100"), now=t0)
    sp.market_sell("ETH/USDT", qty=D("0.5"), ref_price=D("101"), now=t0 + timedelta(minutes=5))
    v.save(at("2026-10-01", 1, 35))
    fut_doc = json.loads(v.fut_path().read_text(encoding="utf-8"))
    spot_doc = json.loads(v.spot_path().read_text(encoding="utf-8"))

    def grow(doc: dict, tag: str) -> dict:
        d = copy.deepcopy(doc)
        hs, es = doc["history"], doc["entries"]
        base = at("2026-09-24")
        d["history"] = []
        for i in range(N_HIST):
            r = copy.deepcopy(hs[i % len(hs)])
            t = base + timedelta(seconds=90 * i)
            r.update(id=f"{tag}{i:05d}", opened_at=iso(t), closed_at=iso(t + timedelta(seconds=40)))
            d["history"].append(r)
        d["entries"] = []
        for i in range(N_ENT):
            e = copy.deepcopy(es[i % len(es)])
            e.update(ts=iso(base + timedelta(seconds=200 * i)), ref_id=f"{tag}{i // 3:05d}")
            d["entries"].append(e)
        d["updated_at"] = iso(at("2026-10-01", 1, 35))
        return d
    for i, b in enumerate(BOOKS):
        p = (v.state / b / "futures_ledger.json") if b else (v.state / "futures_ledger.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(grow(fut_doc, f"F{i}")), encoding="utf-8")
    v.spot_path().write_text(json.dumps(grow(spot_doc, "S")), encoding="utf-8")
    return v.data


def test_night_peak_rss_at_rotation_caps_is_within_budget(tmp_path):
    if sys.platform != "linux":
        return                                           # ru_maxrss birimi ve gece birimi yalnız Linux
    data = _caps_state(tmp_path)
    sizes = [p.stat().st_size for p in (data / "state").rglob("*_ledger.json")]
    assert len(sizes) == 9 and min(sizes) > 5_000_000, sizes
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), "LANG": "C.UTF-8", "TZ": "UTC"}
    cp = subprocess.run([sys.executable, "-s", "-c", CHILD, str(ROOT), str(data)], capture_output=True, text=True,
                        env=env, cwd=str(tmp_path), timeout=1500)
    assert cp.returncode == 0, cp.stdout[-2000:] + cp.stderr[-4000:]
    out = json.loads(cp.stdout.strip().splitlines()[-1])
    n1, n2 = out["night1"], out["night2"]
    assert n1["result"] == "SUCCESS" and n2["result"] == "SUCCESS", out
    assert n1["new_closes"] == 9 * N_HIST and n2["new_closes"] == 0, "ilk gece hepsi arşivlendi; ikinci gece 0"
    peak = max(n1["rss"], n2["rss"])
    print(f"ledger'lar {sum(sizes) / 1e6:.1f} MB (en büyük {max(sizes) / 1e6:.1f} MB) · tepe RSS {peak / 2**20:.0f} MiB "
          f"(import sonrası {out['rss_after_import'] / 2**20:.0f} MiB; 1. gece {n1['rss'] / 2**20:.0f}, 2. gece "
          f"{n2['rss'] / 2**20:.0f}; bütçe {BUDGET / 2**20:.0f} MiB = 0,6 × MemoryMax)")
    assert peak <= BUDGET, f"tepe RSS {peak / 2**20:.0f} MiB > {BUDGET / 2**20:.0f} MiB (0,6 × MemoryMax)"

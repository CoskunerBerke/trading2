# -*- coding: utf-8 -*-
"""PROTOCOL_V14: 2 kural x 3 pencere = 6 kosu, 2020 evreni, AYRI onbellek (TRADINGBOT_CACHE_DIR). Baska kosu YOK.

Hazirlik (bir kez):  python run_v14.py --prepare   -> tar'i C:/Users/berke/wt-2020/data altina acar, filtreleri kopyalar
Kosu:                python run_v14.py
"""
import hashlib, io, json, os, shutil, subprocess, sys, tarfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:/Users/berke/research/entry_v1")
CACHE = Path(r"C:/Users/berke/wt-2020/data")
TAR = Path(r"C:/Users/berke/trading2-deploy/tb-2020-universe.tar.gz")
FILTERS_SRC = Path(r"C:/Users/berke/wt-ten/data/symbol_filters.json")
WINS = {"p1": ("2020-11-01", "2022-08-31"), "p2": ("2022-09-01", "2024-08-31"), "p3": ("2024-09-01", "2026-08-31")}
UNIVERSE = ["BTC/USDT", "ETH/USDT", "LINK/USDT", "YFI/USDT", "BCH/USDT", "UNI/USDT", "BNB/USDT", "LTC/USDT", "XRP/USDT", "DOT/USDT"]
JOBS = [("v14_%s_%s" % (k, p), s, WINS[p]) for k, s in (("t2", "t2_trend_regime"), ("m2", "m2_tsmom28")) for p in WINS]


def prepare():
    assert TAR.exists(), "paket yok: %s" % TAR
    CACHE.mkdir(parents=True, exist_ok=True)
    with tarfile.open(TAR, "r:gz") as t:
        names = t.getnames()
        bad = [n for n in names if n.startswith("/") or ".." in n]
        assert not bad, bad
        t.extractall(CACHE)
    shutil.copy2(FILTERS_SRC, CACHE / "symbol_filters.json")
    rank = json.load(io.open(CACHE / "ranking_oct2020.json", encoding="utf-8"))
    top = [r["symbol"] for r in rank["ranking"][:10]]
    print("paket acildi:", len(names), "uye; ranking ilk 10:", top)
    assert top == UNIVERSE, "VPS siralamasi protokoldeki evrenle AYNI DEGIL: %s" % top
    got = sorted(p.name.replace("_", "/") for p in (CACHE / "history" / "futures").iterdir())
    print("arsivdeki semboller:", got)
    missing = [s for s in UNIVERSE if s not in got]
    assert not missing, "eksik seri: %s" % missing
    for s in UNIVERSE:
        d = CACHE / "history" / "futures" / s.replace("/", "_")
        tfs = sorted(p.name for p in d.iterdir() if p.is_dir())
        n1d = len(list((d / "1d").glob("*/*.csv.gz"))) if (d / "1d").exists() else 0
        print("  %-9s tf=%s 1d_ay=%d" % (s, ",".join(tfs), n1d))
    print("PREPARE_OK")


def one(job):
    rid, strat, (f, t) = job
    env = dict(os.environ, TRADINGBOT_CACHE_DIR=str(CACHE))
    cmd = [sys.executable, "run_rule.py", "--rule", "none", "--strategy", strat, "--run-id", rid,
           "--from", f, "--to", t, "--no-breakeven", "--cache-dir", str(CACHE), "--symbols"] + UNIVERSE
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    mp = ROOT / "out" / ("meta_%s.json" % rid)
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {"run_id": rid}
    m.update({"rc": r.returncode, "wall_s": round(time.time() - t0, 1), "strategy_name": strat, "cache_dir": str(CACHE)})
    if r.returncode != 0:
        m["err"] = r.stderr[-1500:]
    elif not str(m.get("history_root", "")).replace("\\", "/").startswith(str(CACHE).replace("\\", "/")):
        m["rc"] = 99; m["err"] = "YANLIS ARSIV: %s" % m.get("history_root")     # 2026-09-14 olayi: run_rule env'i eziyordu
    print("%-12s rc=%s %5.0fs acilan=%s funding=%s" % (rid, m["rc"], m["wall_s"], m.get("n_opened"), m.get("funding_coverage")), flush=True)
    return m


if __name__ == "__main__":
    if "--prepare" in sys.argv:
        prepare(); sys.exit(0)
    assert (CACHE / "symbol_filters.json").exists(), "once --prepare"
    prov = {"momentum_rules_sha256": hashlib.sha256((ROOT / "momentum_rules.py").read_bytes()).hexdigest(),
            "strategy_rules_sha256": hashlib.sha256((ROOT / "strategy_rules.py").read_bytes()).hexdigest(),
            "tar_sha256": hashlib.sha256(TAR.read_bytes()).hexdigest(), "universe": UNIVERSE}
    print("PROTOCOL_V14: %d kosu - %s" % (len(JOBS), json.dumps(prov)[:160]), flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(one, JOBS))
    (ROOT / "out" / "v14_meta.json").write_text(json.dumps({"provenance": prov, "runs": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("V14 DONE", sum(1 for r in rows if r.get("rc") == 0), "/", len(rows))

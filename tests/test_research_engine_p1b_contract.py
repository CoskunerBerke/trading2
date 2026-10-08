# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b — yalıtım ve sözleşme (docs/SYSTEM_LEARNING_ENGINE_V1.md §2.1, §2.4, §2.8, §3.2, §3.3,
§9.2; P1a kabul 11'in P1b uzantısı).

* Ağ kullanan TEK motor modülü `datastore`'dur (`urllib` yalnız `urllib_http` içinde); paket dışından yalnız
  `history.store` (alt sınıf) ve `market.ratelimit` (`BudgetPool`) import edilir; gece biriminin ve ağsız modüllerin
  import grafiği `datastore`'u içermez.
* Worker/karar yolu `research_engine`'i hiç anmaz (history, market, storage, patterns ve P1a karar listesi).
* Worker'ın `HistoryStore`'u değişmez: motor import edildiğinde sınıf/modül yamalanmaz; worker deposunun davranışı ve
  dosya biçimi aynıdır (`_src` yok, `.sha256` yok, manifest alanları temel `Manifest`'tir).
* Veri birimi yalnız `data/research` altına yazar; state ve worker deposu yalnız okunur (açma kipi izlenir).
* U_R §3.3'e uyar; gece birimi S0'da `data_status.json`'un mührünü okur (`data_seal`, `DATA_STALE`).
"""
from __future__ import annotations

import ast
import builtins
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import CONFIG_ONE, DataEnv, ms, utc  # noqa: E402
from research_engine_fixtures import FakeHost, FakeVps, at, run_engine_night, trade  # noqa: E402

from tradingbot.research_engine import selfcheck as SC  # noqa: E402
from tradingbot.research_engine import universe as U  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradingbot" / "research_engine"
NET = {"urllib", "http", "socket", "requests", "ssl", "aiohttp", "httpx", "websocket", "websockets", "ccxt", "ftplib", "smtplib"}
#: paket DIŞI import izinleri (modül → izinli `tradingbot.*` modülleri)
OUTSIDE_OK = {"store.py": {"tradingbot.history.store"}, "seed.py": {"tradingbot.history.store"},
              "datastore.py": {"tradingbot.market.ratelimit"}}


def _imports(path: Path) -> list[tuple[ast.AST, str, int]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(node, a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node, node.module or "", node.level))
    return out


def _fn_lines(path: Path, name: str) -> range:
    fn = next(n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return range(fn.lineno, fn.end_lineno + 1)


def test_only_datastore_reaches_the_network_and_outside_imports_are_whitelisted():
    http_fn = _fn_lines(PKG / "datastore.py", "urllib_http")
    sock_fn = _fn_lines(PKG / "selfcheck.py", "probe_socket_denied")
    seen_outside: dict[str, set[str]] = {}
    for f in sorted(PKG.glob("*.py")):
        for node, mod, level in _imports(f):
            top = mod.split(".")[0]
            if level == 0:
                if top in NET:
                    ok = (f.name == "datastore.py" and top == "urllib" and node.lineno in http_fn) or (
                        f.name == "selfcheck.py" and top == "socket" and node.lineno in sock_fn)
                    assert ok, f"{f.name}:{node.lineno}: ağ modülü {mod}"
                assert top != "subprocess" or f.name in ("selfcheck.py", "datastore.py"), f"{f.name}: subprocess"
            elif level >= 2:
                full = "tradingbot." + mod
                seen_outside.setdefault(f.name, set()).add(full)
                assert full in OUTSIDE_OK.get(f.name, set()), f"{f.name}:{node.lineno}: paket dışı import {full}"
    assert seen_outside == OUTSIDE_OK, seen_outside


def _graph(start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        for node, mod, level in _imports(PKG / f"{m}.py"):
            if level == 1:
                if mod:
                    todo.append(mod.split(".")[0])
                else:
                    todo += [a.name if (PKG / f"{a.name}.py").exists() else "__init__" for a in node.names]
    return seen


def test_network_free_modules_never_import_the_data_unit():
    for m in ("night", "selfcheck", "summary", "backup", "closes", "daily_target", "store", "seed", "universe", "provider"):
        g = _graph(m)
        assert "datastore" not in g, (m, g)
    assert not _graph("night") & {"store", "seed", "provider", "universe", "datastore"}
    assert {"store", "universe", "seed", "night", "selfcheck"} <= _graph("datastore")


WORKER_GLOBS = ("history/*.py", "market/*.py", "storage/*.py", "patterns/*.py", "data.py", "engine_v3.py", "engine.py",
                "learning*.py", "entry_universe.py", "decision*.py", "strategy_paper*.py", "box_*.py", "protective_monitor.py",
                "paper_*.py", "pattern_trader/**/*.py", "coinhead/**/*.py", "learn/**/*.py", "execution/**/*.py",
                "accounting/**/*.py", "risk/**/*.py", "shared_experience/**/*.py", "dashboard/**/*.py", "ops/**/*.py",
                "core/**/*.py", "config*.py")


def test_worker_and_decision_path_never_reference_the_engine():
    tb = ROOT / "tradingbot"
    files = sorted({p for g in WORKER_GLOBS for p in tb.glob(g)})
    assert len(files) > 100
    for f in files:
        assert "research_engine" not in f.read_text(encoding="utf-8"), f"{f.relative_to(ROOT)} motoru anmamalı"
    cli = (tb / "cli.py").read_text(encoding="utf-8")
    assert "research_engine" not in cli
    tree = ast.parse((tb / "cli_v3.py").read_text(encoding="utf-8"))
    handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_engine_data")
    mods = {n.module for n in ast.walk(handler) if isinstance(n, ast.ImportFrom)}
    assert mods == {"research_engine.paths", "research_engine.datastore"}, mods


CHILD = r'''
import inspect, json, sys
sys.path.insert(0, sys.argv[1])
import tradingbot.history.store as HS
def snap():
    cls = {k: (getattr(v, "__qualname__", None), getattr(getattr(v, "__code__", None), "co_code", b"").hex())
           for k, v in vars(HS.HistoryStore).items()}
    mod = {"KIND_COLS": json.dumps(HS.KIND_COLS), "KLINE_COLS": list(HS.KLINE_COLS), "FUNDING_COLS": list(HS.FUNDING_COLS),
           "fields": list(HS.Manifest.__dataclass_fields__), "cols_for": HS.cols_for.__code__.co_code.hex(),
           "step": HS.step_ms_for.__code__.co_code.hex(), "canon": HS.canonical_checksum.__code__.co_code.hex(),
           "funding_step": HS.step_ms_for("funding"), "ids": [id(HS.cols_for), id(HS.step_ms_for), id(HS.Manifest)]}
    return {"cls": cls, "mod": mod}
before = snap()
import tradingbot.research_engine.store, tradingbot.research_engine.seed, tradingbot.research_engine.provider
import tradingbot.research_engine.universe, tradingbot.research_engine.datastore
after = snap()
print(json.dumps({"same": before == after, "funding_step": after["mod"]["funding_step"]}))
'''


def test_history_store_class_and_module_are_not_mutated_by_the_engine():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TRADINGBOT_", "ENGINE_"))}
    cp = subprocess.run([sys.executable, "-s", "-c", CHILD, str(ROOT)], capture_output=True, text=True, env=env, timeout=180)
    assert cp.returncode == 0, cp.stderr[-2000:]
    out = json.loads(cp.stdout.strip().splitlines()[-1])
    assert out == {"same": True, "funding_step": 8 * 3_600_000}


def test_worker_history_store_behaviour_and_file_format_are_unchanged(tmp_path):
    import tradingbot.research_engine.store  # noqa: F401  (motor yüklüyken bile)
    from tradingbot.history.store import KLINE_COLS, HistoryStore, Manifest, canonical_checksum
    w = HistoryStore(tmp_path / "market" / "history")
    t0 = ms(utc(2026, 9, 1))
    df = pd.DataFrame({"timestamp": [t0 + i * 3_600_000 for i in range(30)], "open": 1.0, "high": 2.0, "low": 0.5,
                       "close": 1.5, "volume": 3.0, "close_time": [t0 + (i + 1) * 3_600_000 - 1 for i in range(30)]})
    r = w.write("futures", "BTC/USDT", "1h", df, source="rest")
    r2 = w.write("futures", "BTC/USDT", "1h", df.assign(close=9.0), source="rest")     # temel davranış: son gelen kazanır
    assert r["rows_new"] == 30 and r2["rows_new"] == 0
    back = w.read("futures", "BTC/USDT", "1h")
    assert list(back.columns) == KLINE_COLS and set(back["close"]) == {9.0}
    files = sorted(p.relative_to(w.root).as_posix() for p in w.root.rglob("*") if p.is_file())
    assert files == ["futures/BTC_USDT/1h/2026/09.parquet", "futures/BTC_USDT/1h/manifest.json"]
    assert "_src" not in pd.read_parquet(w.root / files[0]).columns
    man = json.loads((w.root / files[1]).read_text(encoding="utf-8"))
    assert set(man) == set(Manifest.__dataclass_fields__)
    assert man["checksum"] == canonical_checksum(back, KLINE_COLS) and w.validate("futures", "BTC/USDT", "1h")["ok"]


def test_data_unit_writes_only_under_research_and_opens_state_and_worker_store_read_only(tmp_path, monkeypatch):
    from tradingbot.history.store import HistoryStore
    e = DataEnv(tmp_path, start=utc(2026, 10, 6, 0, 41), listing={"futures/BTCUSDT": ms(utc(2026, 8, 20))},
                only={"futures/BTCUSDT/4h", "futures/BTCUSDT/funding", "futures/BTCUSDT/metrics_5m"})
    trade(e.v.fut(), "SOL/USDT", at("2026-10-01", 3), 100.0, 101.0)
    e.v.save(utc(2026, 10, 6))
    HistoryStore(e.paths.worker_history).write("futures", "BTC/USDT", "4h", pd.DataFrame(
        {"timestamp": [ms(utc(2026, 9, 1)) + i * 14_400_000 for i in range(6)], "open": 1.0, "high": 2.0, "low": 0.5,
         "close": 1.0, "volume": 1.0, "close_time": [ms(utc(2026, 9, 1)) + (i + 1) * 14_400_000 - 1 for i in range(6)]}))
    research = os.path.abspath(e.paths.research)
    outside = [p for p in e.paths.data.rglob("*") if p.is_file()]
    before = {p: p.read_bytes() for p in outside}
    bad: list[str] = []
    ledger_modes: list[str] = []
    real = {"open": builtins.open, "replace": os.replace, "rename": os.rename, "unlink": os.unlink, "rmtree": shutil.rmtree,
            "mkdir": os.mkdir}

    def inside(p) -> bool:
        a = os.path.abspath(str(p))
        return a == research or a.startswith(research + os.sep) or not a.startswith(os.path.abspath(e.paths.data))

    def spy_open(file, mode="r", *a, **k):
        if str(file).endswith("ledger.json"):
            ledger_modes.append(str(mode))
        if any(c in str(mode) for c in "wax+") and not inside(file):
            bad.append(f"open {file} {mode}")
        return real["open"](file, mode, *a, **k)

    def guard(name):
        def f(*args, **k):
            target = args[1] if name in ("replace", "rename") else args[0]
            if not inside(target):
                bad.append(f"{name} {target}")
            return real[name](*args, **k)
        return f
    monkeypatch.setattr(builtins, "open", spy_open)
    for n in ("replace", "rename", "unlink", "mkdir"):
        monkeypatch.setattr(os, n, guard(n))
    monkeypatch.setattr(shutil, "rmtree", guard("rmtree"))
    for mode in ("backfill", "update"):
        st = e.run(mode)
        assert st["exit_code"] == 0, (mode, st.get("error"))
    monkeypatch.undo()
    assert not bad, bad
    assert ledger_modes and set(ledger_modes) == {"r"}, ledger_modes
    assert {p: p.read_bytes() for p in outside} == before
    assert sorted(p for p in e.paths.data.rglob("*") if p.is_file() and not inside(p)) == sorted(outside)
    for sub in ("store", "archive_cache", "summary", "universe", "runs", "locks"):
        assert (e.paths.research / sub).exists(), sub


# ============================================================================ U_R (§3.3)
def test_research_universe_follows_the_design_table(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path, config_text=CONFIG_ONE.replace("[BTC/USDT, SOL/USDT]", "[BTC/USDT, ZEC/USDT]"))
    now = utc(2026, 10, 6, 0, 41)
    trade(v.fut(), "SOL/USDT", at("2026-09-28", 3), 100.0, 101.0)                     # son 180 gün: girer
    trade(v.fut("strategy_paper_box"), "ADA/USDT", at("2026-03-01", 3), 1.0, 1.1)     # 180 günden eski: girmez
    trade(v.fut("strategy_paper_box"), "XAU/USDT", at("2026-09-20", 3), 4000.0, 4010.0)
    from decimal import Decimal as D
    v.spot().market_buy("DOGE/USDT", qty=D("50"), ref_price=D("0.2"), now=at("2026-09-30", 2))   # ana bot spot: açık lot
    v.save(at("2026-10-05"))
    cf = v.state / "strategy_paper_m2" / "counterfactual_trades.json"
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps({"trades": [{"symbol": "LINK/USDT", "market_type": "futures", "created_at": "2026-10-01T03:00:00+00:00"},
                                         {"symbol": "OLD/USDT", "created_at": "2025-01-01T00:00:00+00:00"}]}), encoding="utf-8")
    doc = U.build_universe(v.paths, now=now, app_dir=host.app)
    assert set(doc["futures"]) == {"BTCUSDT", "ETHUSDT", "ZECUSDT", "SOLUSDT", "LINKUSDT"}
    assert doc["futures"]["BTCUSDT"] == sorted({U.ROLE_ENTRY, U.ROLE_BTC_ETH}) and doc["futures"]["LINKUSDT"] == [U.ROLE_CF]
    assert set(doc["gold_futures"]) == {"XAUUSDT", "PAXGUSDT"} and U.ROLE_TRADED in doc["gold_futures"]["XAUUSDT"]
    assert doc["main_spot"] == {"DOGEUSDT": [U.ROLE_MAIN_SPOT]} and "ADAUSDT" not in json.dumps(doc["futures"])
    assert doc["entry_universe"]["source"] == "config.yaml entry_universe.symbols" and not doc["flags"]
    plan = {s.key: s for s in U.plan_series(doc, now=now)}
    for k in ("5m", "15m", "1h", "4h", "1d", "funding", "metrics_5m", "markpx_1h", "premium_1h"):
        assert f"futures/BTCUSDT/{k}" in plan and plan[f"futures/BTCUSDT/{k}"].from_listing
    assert plan["futures/BTCUSDT/metrics_5m"].start_ms == U.day_ms("2021-12-01")
    assert "futures/XAUUSDT/1m" in plan and "futures/XAUUSDT/metrics_5m" not in plan and "futures/BTCUSDT/1m" not in plan
    assert plan["futures/XAUUSDT/1h"].start_ms == U.day_ms("2025-12-01")
    assert plan["futures/XAUUSDT/1m"].start_ms == max(U.day_ms("2025-12-01"), ms(now) - 400 * 86_400_000)
    assert plan["futures/PAXGUSDT/funding"].start_ms == U.day_ms("2025-03-01")
    assert {k for k in plan if k.startswith("spot/PAXGUSDT/")} == {f"spot/PAXGUSDT/{t}" for t in ("5m", "1h", "4h", "1d")}
    assert plan["spot/PAXGUSDT/5m"].start_ms == U.day_ms("2020-08-01") and plan["spot/BTCUSDT/1d"].start_ms == U.day_ms("2020-01-01")
    d0 = ms(now) - 400 * 86_400_000
    assert plan["spot/DOGEUSDT/1h"].start_ms == d0 - d0 % 86_400_000 and "spot/DOGEUSDT/1m" not in plan
    # Değişiklik 2026-10-08: giriş evreni/BTC-ETH dışı vadelilerin (SOL: yalnız işlem, LINK: yalnız CF) ince serileri son 400 gün
    short = {f"futures/{s}/{k}" for s in ("SOLUSDT", "LINKUSDT") for k in ("5m", "15m", "metrics_5m")}
    for k in short:
        assert plan[k].start_ms == d0 - d0 % 86_400_000 and plan[k].from_listing, k
    for s in ("SOLUSDT", "LINKUSDT"):
        for k in ("1h", "4h", "1d", "funding", "markpx_1h", "premium_1h"):
            assert plan[f"futures/{s}/{k}"].start_ms == U.day_ms("2019-09-01"), (s, k)
    for s in ("BTCUSDT", "ETHUSDT", "ZECUSDT"):
        assert plan[f"futures/{s}/5m"].start_ms == U.day_ms("2019-09-01") and plan[f"futures/{s}/metrics_5m"].start_ms == U.day_ms("2021-12-01")
    snap = U.snapshot_doc(doc, list(plan.values()), U.universe_json_snapshot(v.paths))
    p = U.write_universe_snapshot(v.paths, snap)
    got = json.loads(p.read_text())
    assert p == v.paths.research / "universe" / "2026-10-06.json" and got["series_count"] == len(plan)
    assert set(got["series_short"]) == short and got["short_days"] == 400
    assert set(got["series_short"].values()) == {U.datetime.fromtimestamp((d0 - d0 % 86_400_000) / 1000, U.UTC).strftime("%Y-%m-%d")}


def test_entry_universe_falls_back_to_frame_provenance_then_flags_missing(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)                                    # config'te entry_universe yok
    syms, info = U.entry_universe(v.paths, host.app)
    assert syms == [] and info["source"] == "none"
    assert U.F_ENTRY_MISSING in U.build_universe(v.paths, now=utc(2026, 10, 6), app_dir=host.app)["flags"]
    (v.state / "frame_provenance.json").write_text(json.dumps({"generated_at": "x", "entry_universe": ["ARB/USDT", "BTC/USDT"]}),
                                                   encoding="utf-8")
    syms, info = U.entry_universe(v.paths, host.app)
    assert syms == ["ARBUSDT", "BTCUSDT"] and info["source"].startswith("state/frame_provenance.json")


def test_entry_universe_list_is_used_only_when_enabled(tmp_path):
    """Worker `entry_universe.symbols`'ı YALNIZ `enabled: true` iken giriş evreni sayar (`engine_v3`: `_eu.symbols if
    _eu.enabled`; varsayılan false) ve kapalıyken `frame_provenance.json`'a boş liste yazar. Motor da öyle yapar."""
    v = FakeVps(tmp_path / "v")
    for name, cfg in (("off", CONFIG_ONE.replace("enabled: true", "enabled: false")),
                      ("missing", CONFIG_ONE.replace("  enabled: true\n", ""))):
        host = FakeHost(tmp_path / name, config_text=cfg)
        syms, info = U.entry_universe(v.paths, host.app)
        assert syms == [] and info["source"] == "none" and "ignored" in info and info["enabled"] in (False, None), (name, info)
        doc = U.build_universe(v.paths, now=utc(2026, 10, 6), app_dir=host.app)
        assert U.F_ENTRY_MISSING in doc["flags"] and "SOLUSDT" not in doc["futures"] and "BTCUSDT" in doc["futures"]
    (v.state / "frame_provenance.json").write_text(json.dumps({"entry_universe": []}), encoding="utf-8")   # worker: kapalı
    assert U.entry_universe(v.paths, FakeHost(tmp_path / "off2", config_text=CONFIG_ONE.replace(
        "enabled: true", "enabled: false")).app)[0] == []
    syms, info = U.entry_universe(v.paths, FakeHost(tmp_path / "on", config_text=CONFIG_ONE).app)
    assert syms == ["BTCUSDT", "SOLUSDT"] and info["enabled"] is True


def test_rawconfig_reads_the_entry_universe_tolerantly(tmp_path):
    from tradingbot.research_engine import rawconfig as RC
    p = tmp_path / "c.yaml"
    p.write_text("entry_universe:\n  enabled: true\n  symbols: [BTC/USDT, 5, '', ETH/USDT]\n  yeni_anahtar: 1\n", encoding="utf-8")
    rc = RC.read_raw_config(p)
    assert rc.ok and rc.values["entry_universe.symbols"] == ["BTC/USDT", "ETH/USDT"] and rc.values["entry_universe.enabled"] is True
    assert {"entry_universe.enabled", "entry_universe.symbols"} <= set(RC.NEEDS)
    p.write_text("entry_universe: nope\n", encoding="utf-8")
    assert RC.read_raw_config(p).values["entry_universe.symbols"] is None


# ============================================================================ gece S0: veri tazeliği (§6.1 S0, P1b'den)
def test_night_records_the_data_seal_and_flags_stale_data(tmp_path):
    e = DataEnv(tmp_path, start=utc(2026, 10, 6, 0, 41), listing={"futures/BTCUSDT": ms(utc(2026, 9, 20))},
                only={"futures/BTCUSDT/1d"})
    SC.register_epoch(e.paths, "b" * 40, utc(2020, 1, 1), "test", code_hash=SC.engine_code_hash())
    rs = run_engine_night(e.v, e.host, utc(2026, 10, 5, 1, 40))
    assert rs["data_seal"] is None and rs["selfcheck"]["data"]["status"] == SC.DATA_NONE and "DATA_STALE" not in rs["flags"]
    e.run("backfill")
    seal = e.status()["data_seal"]
    rs = run_engine_night(e.v, e.host, utc(2026, 10, 6, 1, 40))
    assert rs["data_seal"] == seal and rs["selfcheck"]["data"]["status"] == SC.OK and "DATA_STALE" not in rs["flags"]
    assert rs["plan"] == ["S0", "S1a", "S3", "S7", "S7b"], "P1b'de depoyu okuyan aşama yok; plan değişmez"
    rs = run_engine_night(e.v, e.host, utc(2026, 10, 8, 1, 40))      # mühür 49 saat önce
    assert rs["data_seal"] == seal and rs["selfcheck"]["data"]["status"] == SC.DATA_STALE and "DATA_STALE" in rs["flags"]
    assert rs["result"] == "SUCCESS"
    from tradingbot.research_engine.summary import status_lines
    assert any(ln.startswith("Veri: DATA_STALE") for ln in status_lines(e.paths, now=utc(2026, 10, 8, 2)))

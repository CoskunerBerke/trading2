"""P3b-1 — `tradingbot/strategy_lib/` sözleşmesi (docs/ENGINE_BRAIN_V1.md §2.1, §8.1 P3b).

* Saflık (AST): G/Ç, saat, ağ, rastgelelik yok; motor paketini anmaz/import etmez (çalışma anında da yüklemez). Tek
  okuma istisnası `identity.py`'nin depo kaynaklarını okumasıdır (yazma/saat/ağ yok).
* Kimlik: `module_src_sha` import kapanışından bağımsız yeniden hesapla eşit; canlı kural modülleri kapanışta;
  `spec_sha` = `registry.spec_sha(registry.lib_spec(…))` = deneme defterinin `seal_entries` satırı (alias'lar dahil).
* Kopyalar mühürlü parametrelerle kurulur (eksik/fazla anahtar ValueError); canlı pencere sabitleri canlı koda eşit;
  C4 bağdaştırıcısının atladığı kapı mühürde sekiz varyasyonda da açık.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import trials as TR
from tradingbot.strategy_lib import catalog as SLC
from tradingbot.strategy_lib import identity as SLI
from tradingbot.strategy_lib import livebars as LB

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradingbot" / "strategy_lib"

FORBIDDEN_MODULES = {"time", "datetime", "random", "secrets", "uuid", "socket", "ssl", "urllib", "http", "requests",
                     "httpx", "aiohttp", "ccxt", "websocket", "websockets", "subprocess", "os", "sys", "io", "pathlib",
                     "shutil", "tempfile", "sqlite3", "asyncio", "threading", "multiprocessing", "logging", "pickle",
                     "glob", "fcntl", "signal", "zoneinfo", "calendar"}
FORBIDDEN_CALLS = {"open", "print", "input", "eval", "exec", "compile", "__import__", "breakpoint", "exit", "quit"}
FORBIDDEN_ATTRS = {"now", "utcnow", "today", "time", "time_ns", "perf_counter", "monotonic", "random", "randint", "uniform",
                   "gauss", "shuffle", "choice", "choices", "urandom", "read_text", "read_bytes", "write_text",
                   "write_bytes", "unlink", "mkdir", "rmdir", "rename", "replace", "system", "popen", "getenv", "environ",
                   "seed", "token_hex", "uuid4"}
#: dosya → izinli istisnalar (modül, öznitelik)
EXCEPTIONS = {"identity.py": ({"pathlib"}, {"read_bytes"}), "catalog.py": ({"importlib"}, set())}


def _py_files() -> list[Path]:
    files = sorted(PKG.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "contract.py", "catalog.py", "identity.py", "livebars.py", "k_trend.py",
                                       "k_box.py", "k_d4.py", "k_c4.py", "k_fm.py"}
    return files


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.name)
def test_strategy_lib_is_pure_by_ast(path):
    src = path.read_text(encoding="utf-8")
    assert "research_engine" not in src, f"{path.name}: motor paketi anılmaz (worker da bu paketi import edecek)"
    tree = ast.parse(src)
    ok_mods, ok_attrs = EXCEPTIONS.get(path.name, (set(), set()))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                assert top not in FORBIDDEN_MODULES - ok_mods, f"{path.name}:{node.lineno}: import {a.name}"
                assert top != "importlib" or "importlib" in ok_mods, f"{path.name}:{node.lineno}: importlib"
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                top = (node.module or "").split(".")[0]
                assert top not in FORBIDDEN_MODULES - ok_mods, f"{path.name}:{node.lineno}: from {node.module}"
                assert top != "importlib" or "importlib" in ok_mods
            else:
                assert node.level <= 2, f"{path.name}:{node.lineno}: paket dışına göreli import"
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                assert f.id not in FORBIDDEN_CALLS, f"{path.name}:{node.lineno}: {f.id}()"
            elif isinstance(f, ast.Attribute):
                assert f.attr not in FORBIDDEN_ATTRS - ok_attrs, f"{path.name}:{node.lineno}: .{f.attr}()"
        elif isinstance(node, ast.Global):
            raise AssertionError(f"{path.name}:{node.lineno}: global durum yazımı")


def test_importing_strategy_lib_does_not_load_the_engine():
    code = ("import sys, tradingbot.strategy_lib, tradingbot.strategy_lib.identity; "
            "import tradingbot.strategy_lib.k_trend, tradingbot.strategy_lib.k_box, tradingbot.strategy_lib.k_d4, "
            "tradingbot.strategy_lib.k_c4, tradingbot.strategy_lib.k_fm; "
            "print(sorted(m for m in sys.modules if m.startswith('tradingbot.research_engine')))")
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert cp.returncode == 0, cp.stderr[-2000:]
    assert cp.stdout.strip().splitlines()[-1] == "[]", cp.stdout


# ============================================================================ kimlik
def test_module_src_sha_is_the_import_closure_digest():
    for vid in SLC.IMPLEMENTED:
        sha = SLI.module_src_sha(vid)
        assert RG.is_sha256(sha)
        lines = sorted(f"{rel}\t{hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()}\n" for rel in SLI.module_files(vid))
        assert sha == hashlib.sha256((SLI.VERSION + "\n" + "".join(lines)).encode("utf-8")).hexdigest()
    files = {v: set(SLI.module_files(v)) for v in ("K_T2", "K_BOX", "K_D4", "K_C4_CV001", "K_FM")}
    assert {"tradingbot/strategy_lib/k_trend.py", "tradingbot/paper_rules.py", "tradingbot/ema200_trend.py",
            "tradingbot/regime_gate.py", "tradingbot/structures/bots.py", "tradingbot/indicators.py"} <= files["K_T2"]
    assert {"tradingbot/box_theory.py", "tradingbot/strategy_lib/k_box.py"} <= files["K_BOX"]
    assert {"tradingbot/donchian_trend.py", "tradingbot/signal_lab.py"} <= files["K_D4"]
    assert {"tradingbot/candle_book.py", "tradingbot/candle_dsl.py", "tradingbot/candle_variations.py"} <= files["K_C4_CV001"]
    assert {"tradingbot/pattern_trader/strategy_v3.py", "tradingbot/structures/analysis.py"} <= files["K_FM"]
    assert all("tradingbot/research_engine" not in f for fs in files.values() for f in fs)
    assert len({SLI.module_src_sha(v) for v in SLC.C4_VARIATIONS}) == 1, "aynı modül → aynı kaynak sha'sı"
    assert len({SLI.module_src_sha(v) for v in ("K_T2", "K_BOX", "K_D4", "K_C4_CV001", "K_FM")}) == 5


def test_spec_sha_equals_the_registry_and_the_trials_ledger():
    m = KN.module_src_shas()
    assert set(m) == set(SLC.IMPLEMENTED) and len(SLC.IMPLEMENTED) == 13
    for vid in SLC.IMPLEMENTED:
        assert KN.spec_sha_for(vid) == RG.spec_sha(RG.lib_spec(vid, SLI.module_src_sha(vid)))
    assert KN.spec_sha_for("DON_20_10_4h_LO") == KN.spec_sha_for("K_D4")
    assert KN.spec_sha_for("BOX_n10_mid_ms0.5") == KN.spec_sha_for("K_BOX")
    assert len({KN.spec_sha_for(v) for v in SLC.IMPLEMENTED}) == 13
    bases = {v["variant_id"] for v in RG.LIB_V1["variants"] if not v.get("alias_of")}
    full = {b: m.get(b, "f" * 64) for b in bases}
    rows = TR.seal_entries(full)
    by_unit = {}
    for r in rows:
        vid = r["params"]["variant_id"]
        by_unit.setdefault(vid, set()).add(r["spec_sha"])
    for vid in SLC.IMPLEMENTED:
        assert by_unit[vid] == {KN.spec_sha_for(vid)}, vid
    assert by_unit["DON_20_10_4h_LO"] == {KN.spec_sha_for("K_D4")}
    assert by_unit["BOX_n10_mid_ms0.5"] == {KN.spec_sha_for("K_BOX")}


# ============================================================================ kopyalar
def test_copies_are_built_from_the_sealed_params_only():
    vs = RG.variants_by_id()
    for vid in SLC.IMPLEMENTED:
        var = KN.make_variant(vid)
        assert var.variant_id == vid and var.family == "K"
        assert var.signal_tf == RG.LIB_V1["variants"][[v["variant_id"] for v in RG.LIB_V1["variants"]].index(vid)]["signal_tf"]
        assert tuple(var.timeframes) == tuple(vs[vid]["timeframes"])
        p = RG.thaw(vs[vid]["params"])
        with pytest.raises(ValueError):
            SLC.make(vid, {**p, "extra": 1})
        with pytest.raises(ValueError):
            SLC.make(vid, {k: v for k, v in list(p.items())[1:]})
    with pytest.raises(KeyError):
        SLC.make("TS_28_LS", {"lookback_days": 28})            # B…O P3b-2'de
    assert KN.make_variant("DON_20_10_4h_LO").variant_id == "K_D4"


def test_live_windows_equal_the_live_code():
    from tradingbot.engine import TradingEngine
    from tradingbot.pattern_trader.data import BARS_PER_TF
    from tradingbot.research_engine import rehydrate as RH
    assert dict(LB.LIVE_CLOSED_BARS) == {tf: n - 1 for tf, n in TradingEngine.PERP_FRAME_LIMITS.items()}
    assert dict(LB.LIVE_CLOSED_BARS) == RH.LIVE_CLOSED_BARS
    assert LB.FM_BARS_4H == BARS_PER_TF["4h"]


def test_c4_adapter_skips_only_a_gate_that_is_open_at_the_seal():
    from tradingbot import candle_variations as CV
    for vid in SLC.C4_VARIATIONS:
        cv = RG.variants_by_id()[vid]["params"]["variation"]
        var, why = CV.gate(cv, "standard")
        assert var is not None and why is None, (vid, why)
        assert KN.make_variant(vid).var.definition_sha == var.definition_sha


def test_trend_copy_reuses_the_live_used_structures_reader():
    from tradingbot.strategy_lib.k_trend import used_structures
    st = {"pattern_id": "P1", "action": "ENTER", "side": "LONG", "timeframe": "1d"}
    h = (SL.BookEntry("X", SL.LONG, 1, 2, {"structure": st}), SL.BookEntry("X", SL.LONG, 3, 4, {}),
         SL.BookEntry("X", SL.LONG, 5, 6, {"structure": dict(st, pattern_id="P2", shadow=True)}))
    used = used_structures(h)
    assert set(used) == {"P1"} and used.entries[0]["symbol"] == "X"

"""P3b-1 — `tradingbot/strategy_lib/` sözleşmesi (docs/ENGINE_BRAIN_V1.md §2.1, §8.1 P3b).

* Saflık (AST): G/Ç, saat, ağ, rastgelelik yok; motor paketini anmaz/import etmez (çalışma anında da yüklemez). Tek
  istisna `identity.py`'nin salt-okunur git komutları ve paket kaynaklarının AST'idir (yazma/saat/ağ yok). Çalışma anı
  yoklaması (G/Ç, saat) ve bildirilen modüllerin çalışma izini örtmesi `test_research_engine_parity_p3b.py`'dedir.
* Kimlik: `module_src_sha` = BİLDİRİLMİŞ dosya kümesinin (varyant modülü + paket içi bağımlılıklar + `LIVE_MODULES`)
  HEAD blob'larının özeti; dosya listesi testte sabitlenir ve özet git'ten bağımsız yeniden hesaplanır (döngüsel değil);
  ilgisiz bir modülün düzenlenmesi kimliği değiştirmez, bildirilmiş birininki değiştirir, kirli ağaç reddedilir (geçici
  depo); `spec_sha` = `registry.spec_sha(registry.lib_spec(…))` = deneme defterinin `seal_entries` satırı.
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
EXCEPTIONS = {"identity.py": ({"pathlib", "subprocess", "importlib"}, {"read_bytes"}), "catalog.py": ({"importlib"}, set())}


def _py_files() -> list[Path]:
    files = sorted(PKG.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "contract.py", "catalog.py", "identity.py", "livebars.py", "k_trend.py",
                                       "k_box.py", "k_d4.py", "k_c4.py", "k_fm.py", "indicators.py", "pure.py", "b_tsmom.py",
                                       "c_macross.py", "d_donchian.py", "e_volbreak.py", "f1_range.py", "f2_boxgrid.py",
                                       "h_funding.py"}
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
            "tradingbot.strategy_lib.k_c4, tradingbot.strategy_lib.k_fm, tradingbot.strategy_lib.b_tsmom, "
            "tradingbot.strategy_lib.c_macross, tradingbot.strategy_lib.d_donchian, tradingbot.strategy_lib.e_volbreak, "
            "tradingbot.strategy_lib.f1_range, tradingbot.strategy_lib.f2_boxgrid, tradingbot.strategy_lib.h_funding; "
            "print(sorted(m for m in sys.modules if m.startswith('tradingbot.research_engine')))")
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert cp.returncode == 0, cp.stderr[-2000:]
    assert cp.stdout.strip().splitlines()[-1] == "[]", cp.stdout


# ============================================================================ kimlik
_COMMON = ("tradingbot/strategy_lib/contract.py", "tradingbot/strategy_lib/livebars.py", "tradingbot/timeframes.py",
           "tradingbot/paper_rules.py", "tradingbot/candle_confirmation.py")
_STRUCT = ("tradingbot/structures/analysis.py", "tradingbot/structures/bots.py", "tradingbot/structures/catalog.py",
           "tradingbot/structures/policy.py", "tradingbot/chart_patterns.py", "tradingbot/learn/candle_context.py",
           "tradingbot/learn/multitimeframe_context.py", "tradingbot/learn/weekly_structure.py", "tradingbot/core/ids.py")
#: bildirilmiş dosya kümeleri, elle sabitlenmiş (kimlik fonksiyonunun kendi listesinden bağımsız)
EXPECTED_FILES = {
    "K_T2": _COMMON + _STRUCT + ("tradingbot/strategy_lib/k_trend.py", "tradingbot/ema200_trend.py",
                                 "tradingbot/regime_gate.py", "tradingbot/indicators.py"),
    "K_BOX": _COMMON + ("tradingbot/strategy_lib/k_box.py", "tradingbot/box_theory.py", "tradingbot/ema200_trend.py",
                        "tradingbot/indicators.py"),
    "K_D4": _COMMON + ("tradingbot/strategy_lib/k_d4.py", "tradingbot/donchian_trend.py", "tradingbot/signal_lab.py",
                       "tradingbot/box_theory.py"),
    "K_C4_CV001": _COMMON + ("tradingbot/strategy_lib/k_c4.py", "tradingbot/candle_book.py", "tradingbot/candle_dsl.py",
                             "tradingbot/candle_variations.py", "tradingbot/signal_lab.py"),
    "K_FM": tuple(f for f in _COMMON if f not in ("tradingbot/paper_rules.py", "tradingbot/candle_confirmation.py"))
    + _STRUCT + ("tradingbot/strategy_lib/k_fm.py", "tradingbot/strategy_lib/k_trend.py",
                 "tradingbot/pattern_trader/strategy_v3.py", "tradingbot/pattern_trader/strategy.py",
                 "tradingbot/pattern_trader/universe.py", "tradingbot/signal_lab.py"),
}


def _git_ok() -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True).returncode == 0


def _sha_or_skip(vid: str, **kw) -> str:
    try:
        return SLI.module_src_sha(vid, **kw)
    except SLI.DirtyTreeError as exc:
        if "HEAD'den farklı" in str(exc) and not kw:
            pytest.skip(f"çalışma ağacı kirli (kimlik bilerek hesaplanmaz): {exc}")
        raise


def test_module_src_sha_hashes_the_declared_files_at_head_non_circular():
    if not _git_ok():
        pytest.skip("git yok")
    for vid, files in EXPECTED_FILES.items():
        assert SLI.module_files(vid) == tuple(sorted(set(files))), vid
        assert all("research_engine" not in f for f in files)
    assert SLI.module_files("K_M2") == SLI.module_files("K_T2")
    assert {SLI.module_files(v) for v in SLC.C4_VARIATIONS} == {SLI.module_files("K_C4_CV001")}
    for vid, files in EXPECTED_FILES.items():
        sha = _sha_or_skip(vid)
        lines = []
        for rel in sorted(set(files)):
            blob = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True, check=True).stdout
            lines.append(f"{rel}\t{hashlib.sha256(blob).hexdigest()}\n")
        assert sha == hashlib.sha256(("strategy_lib.module_src_sha.v2\n" + "".join(lines)).encode("utf-8")).hexdigest(), vid
    assert len({SLI.module_src_sha(v) for v in SLC.C4_VARIATIONS}) == 1, "aynı modül → aynı kaynak sha'sı"
    assert len({SLI.module_src_sha(v) for v in ("K_T2", "K_BOX", "K_D4", "K_C4_CV001", "K_FM")}) == 5


def test_unrelated_edit_keeps_module_src_sha_relevant_edit_changes_it_dirty_tree_refused(tmp_path):
    """Geçici bir git deposu (K_D4'ün bildirilmiş dosyaları + ilgisiz bir modül, aynı yollar): ilgisiz modülün işlenmiş
    düzenlemesi kimliği DEĞİŞTİRMEZ; bildirilmiş modülünki değiştirir; bildirilmiş dosyada işlenmemiş düzenleme ya da
    HEAD'de olmayan bildirilmiş dosya `DirtyTreeError`; ilgisiz dosyadaki işlenmemiş düzenleme engel değildir."""
    if not _git_ok():
        pytest.skip("git yok")
    g = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-C", str(tmp_path)]
    unrelated = "tradingbot/ema200_trend.py"
    assert unrelated not in SLI.module_files("K_D4")
    for rel in SLI.module_files("K_D4") + (unrelated,):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((ROOT / rel).read_bytes())
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "a"], check=True)
    sha0 = SLI.module_src_sha("K_D4", root=tmp_path)
    (tmp_path / unrelated).write_text((tmp_path / unrelated).read_text(encoding="utf-8") + "\n# ilgisiz\n", encoding="utf-8")
    assert SLI.module_src_sha("K_D4", root=tmp_path) == sha0, "işlenmemiş ilgisiz düzenleme engel değil, kimlik aynı"
    subprocess.run(g + ["commit", "-q", "-am", "b"], check=True)
    assert SLI.module_src_sha("K_D4", root=tmp_path) == sha0, "ilgisiz modülün düzenlemesi kimliği değiştirmez"
    rel = tmp_path / "tradingbot/donchian_trend.py"
    rel.write_text(rel.read_text(encoding="utf-8") + "\n# kural düzenlemesi\n", encoding="utf-8")
    with pytest.raises(SLI.DirtyTreeError):
        SLI.module_src_sha("K_D4", root=tmp_path)
    subprocess.run(g + ["commit", "-q", "-am", "c"], check=True)
    sha1 = SLI.module_src_sha("K_D4", root=tmp_path)
    assert sha1 != sha0, "bildirilmiş modülün düzenlemesi kimliği değiştirir"
    subprocess.run(g + ["rm", "-q", "--cached", "tradingbot/signal_lab.py"], check=True)
    subprocess.run(g + ["commit", "-q", "-m", "d"], check=True)
    with pytest.raises(SLI.DirtyTreeError):
        SLI.module_src_sha("K_D4", root=tmp_path)         # izlenmeyen / HEAD'de yok


def test_spec_sha_equals_the_registry_and_the_trials_ledger():
    if not _git_ok():
        pytest.skip("git yok")
    try:
        m = KN.module_src_shas()
    except SLI.DirtyTreeError as exc:
        pytest.skip(f"çalışma ağacı kirli: {exc}")
    assert set(m) == set(SLC.IMPLEMENTED) and len(SLC.IMPLEMENTED) == 58            # P3b-2a: K + B…H
    for vid in SLC.IMPLEMENTED:
        assert KN.spec_sha_for(vid) == RG.spec_sha(RG.lib_spec(vid, SLI.module_src_sha(vid)))
    assert KN.spec_sha_for("DON_20_10_4h_LO") == KN.spec_sha_for("K_D4")
    assert KN.spec_sha_for("BOX_n10_mid_ms0.5") == KN.spec_sha_for("K_BOX")
    assert len({KN.spec_sha_for(v) for v in SLC.IMPLEMENTED}) == 58
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


def test_formation_fees_equal_the_live_config():
    import yaml

    from tradingbot.config_v3 import FeesSection
    from tradingbot.research_engine import parity as PA
    from tradingbot.strategy_lib import k_fm
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    f = FeesSection()
    for k, v in k_fm.LIVE_FEES.items():
        assert v == getattr(f, k) == cfg["fees"][k], k
    assert k_fm.COST_FRAC == PA.live_cost_frac() == pytest.approx(0.0016)


# ============================================================================ kopyalar
def test_copies_are_built_from_the_sealed_params_only():
    vs = RG.variants_by_id()
    for vid in [v for v in SLC.IMPLEMENTED if SLC.FAMILY_OF[v] == "K"]:
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
        SLC.make("O_T2_F_ER", {})                              # O örtüleri sonraki P3b-2 adımında
    with pytest.raises(ValueError):
        SLC.make("TS_28_LS", {"lookback_days": 28})            # saf aile mühürlü spec'siz kurulmaz (P3b-2a)
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

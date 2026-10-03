# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — KARAR DEĞİŞMEZ (2026-09-29; SPEC_V1 §1 / §13 T6, görev X3).

**Altın eşitlik.** GERÇEK `TradingEngineV3.tour` (ağsız harness: `test_engine_v3._engine` + `test_strategy_paper_engine_v1.
_install`; T2/M2/D4 kâğıt defterleri, Formasyon defteri, öğrenme modu AÇIK ve KAPALI) saat dondurulmuş ve kimlikler
deterministik olarak dört tur koşulur; `shared_experience` YOK / OFF / RECORD koşularında `state/shared_experience/**`
DIŞINDAKİ her dosya (defterler, karşı-olgusal ve gölge dosyaları, planlar, özetler, risk.json, huni, signals_seen,
karar günlüğü …) BAYT BAYT aynıdır; `health.json` yalnız `shared_experience` anahtarı kadar farklıdır. YOK == OFF ayrıca
dosya kümesi ve `health.json` dahil birebir aynıdır.

**Arıza enjeksiyonu** (hepsi RECORD, öğrenme AÇIK): anlık görüntü istisnası, depo G/Ç hatası, kilit meşgul, bozuk imleç,
kurulum hatası (üç denemeden sonra durur), adım istisnası — karar eşitliği korunur ve sayaçlar artar.

**AST koruması.** Paket defter/kitap/kayıtçı değiştiren hiçbir yöntemi çağırmaz, sahibi olmadığı nesnelerin
`features/meta/stop/plans/trades/positions/history/outcome` alanlarına yazmaz; paketi yalnız `engine_v3` (ve rapor aşaması
için `cli_v3`) içe aktarır; öğrenme modülleri ve replay paketi ona dokunmaz. **Replay** determinizm hash'i RECORD'la aynı.
"""
from __future__ import annotations

import ast
import json
import os
import random
import shutil
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradingbot" / "shared_experience"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

time_machine = pytest.importorskip("time_machine")

UTC = timezone.utc
#: Turlar aynı saatin içinde (10:03, 10:13, 10:23, 10:33): harness çerçeveleri bayatlamaz.
T0 = datetime(2026, 9, 29, 10, 3, tzinfo=UTC)
EXCLUDE_DIR = "shared_experience"
LM_BOOKS = {"main": {"enabled": True, "slots": 20},
            "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
            "m2_tsmom28": {"enabled": True, "slots": 40, "leverage_max": 4},
            "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
            "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3}}


def _overrides(*, lm: bool, xp: dict | None) -> dict:
    ov = {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                             "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"},
                                       {"name": "d4_donchian_20_10", "state_dir": "strategy_paper_trend4h",
                                        "rule_params": {"leverage": 1}}]},
          "pattern_trader": {"enabled": True, "protocol": "momentum_4h_v3", "scan_seconds": 3600.0,
                             "universe_refresh_minutes": 0.0},
          "chart_analysis": {"enabled": False}, "news": {"enabled": False},
          # tek işçi: coin head iş parçacığı havuzunun tamamlanma SIRASI davranış değildir (iki koşu aynı sırayı görsün)
          "coin_heads": {"max_workers": 1},
          "learning_mode": {"enabled": bool(lm), "books": dict(LM_BOOKS),
                            "strategy_overrides": {"regime_gate_shadow": True, "candle_veto_shadow": True,
                                                   "economics_exploration": True,
                                                   "leverage_confidence_fallback": True}}}
    if xp is not None:
        ov["shared_experience"] = dict(xp)
    return ov


class _DetOS:
    """`core.ids.new_id` için deterministik `os.urandom` (her koşu aynı tohumla)."""

    def __init__(self, seed: int = 12345) -> None:
        self._rng = random.Random(seed)

    def __getattr__(self, name):
        return getattr(os, name)

    def urandom(self, n: int) -> bytes:
        return bytes(self._rng.getrandbits(8) for _ in range(n))


def _run(tmp_path: Path, monkeypatch, *, lm: bool, xp: dict | None, prepare=None, between=None) -> dict:
    """Aynı dizinde (yollar eşit kalsın), donmuş saatle dört gerçek tur; dönüş: dosya baytları + motor."""
    import pandas as pd

    import test_engine_v3 as E
    import test_strategy_paper_engine_v1 as SP
    import tradingbot.core.ids as ids_mod
    root = tmp_path / "run"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    with monkeypatch.context() as mp:
        mp.setattr(ids_mod, "os", _DetOS())
        random.seed(7)
        mp.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
        mp.setattr("tradingbot.box_timer.BoxTimer.start", lambda self: None)
        with time_machine.travel(T0, tick=False) as clock:
            eng = E._engine(root, mp, _overrides(lm=lm, xp=xp), symbols=4, equity=200.0, p_win=0.64)
            for fr in eng._fake_live._frames.values():            # üretimdeki gibi oluşan bar dahil (yalnız zaman)
                for tf, ms in (("4h", 14_400_000), ("1h", 3_600_000)):
                    fr[tf]["timestamp"] = fr[tf]["timestamp"] + ms
                    fr[tf].index = fr[tf].index + pd.Timedelta(milliseconds=ms)
            SP._install(eng, mp, btc_up=True, coin_above=True)
            if prepare is not None:
                prepare(eng, mp)
            px = {s: float(fr["4h"]["close"].iloc[-1]) for s, fr in eng._fake_live._frames.items()}
            for i in range(4):
                if i == 0:
                    eng.killswitch.trip("MANUAL", "golden")           # adaylar bloklanır → karşı-olgusal
                if i == 1:
                    eng.killswitch.reset("golden", "reset")           # aynı sinyaller açılır → SUPERSEDED
                if i == 3:                                            # fiyat oynar: stop/hedef → kapanışlar
                    for k, s in enumerate(sorted(px)):
                        eng._fake_live.price[s] = px[s] * (0.85 if k % 2 == 0 else 1.2)
                eng._fake_live._now_s = __import__("time").time()     # canlı görüntü turun (donmuş) anında
                eng.tour(do_scan=False, obsidian=False, charts=False)
                if between is not None:
                    between(eng, i)
                clock.shift(timedelta(minutes=10))
    files = {}
    rb = str(root).encode("utf-8")
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and EXCLUDE_DIR not in rel.split("/"):
            files[rel] = p.read_bytes().replace(rb, b"<ROOT>")           # mutlak yol koşu dizinine bağlı; içerik değil
    health = json.loads((eng.cfg.state_path / "health.json").read_text(encoding="utf-8"))
    mem = (health.get("learning_mode") or {}).get("memory")
    if isinstance(mem, dict):                                         # süreç RSS'i ölçümdür, karar değil
        for k in ("rss_mb", "hwm_mb"):
            mem.pop(k, None)
    xp_dir = eng.cfg.state_path / EXCLUDE_DIR
    status = json.loads((xp_dir / "status.json").read_text(encoding="utf-8")) if (xp_dir / "status.json").exists() else None
    return {"files": files, "health": health, "eng": eng, "status": status, "xp_dir_exists": xp_dir.exists(),
            "rows": sum(1 for x in (xp_dir / "experience.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())
            if (xp_dir / "experience.jsonl").exists() else 0}


def _decision_view(run: dict) -> dict:
    files = dict(run["files"])
    h = dict(run["health"])
    h.pop("shared_experience", None)
    files.pop("state/health.json", None)                              # health yalnız ayrıştırılmış (anahtar düşülmüş) hâliyle
    return {"files": files, "health": h}


def _files_with_health(run: dict) -> dict:
    files = dict(run["files"])
    files.pop("state/health.json", None)
    return {"files": files, "health": run["health"]}


def _diff(a: dict, b: dict) -> list[str]:
    fa, fb = a["files"], b["files"]
    out = sorted(set(fa) ^ set(fb))
    out += [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]
    if a["health"] != b["health"]:
        out.append("health.json")
    return out


RECORD = {"enabled": True, "mode": "RECORD"}


@pytest.fixture(scope="module")
def _cache():
    return {}


def _baseline(tmp_path, monkeypatch, cache, lm: bool) -> dict:
    key = ("off", lm)
    if key not in cache:
        cache[key] = _run(tmp_path / "base", monkeypatch, lm=lm, xp={"enabled": False, "mode": "OFF"})
    return cache[key]


# ============================================================================ altın eşitlik
@pytest.mark.parametrize("lm", [True, False], ids=["learning_on", "learning_off"])
def test_golden_record_equals_off_equals_absent_byte_for_byte(tmp_path, monkeypatch, _cache, lm):
    off = _baseline(tmp_path, monkeypatch, _cache, lm)
    absent = _run(tmp_path / "absent", monkeypatch, lm=lm, xp=None)
    rec = _run(tmp_path / "rec", monkeypatch, lm=lm, xp=RECORD)
    # YOK == OFF: health.json ve dosya kümesi dahil birebir
    assert _diff(_files_with_health(absent), _files_with_health(off)) == []
    assert not off["xp_dir_exists"] and not absent["xp_dir_exists"], "kapalıyken dizin bile kurulmaz"
    assert "shared_experience" not in off["health"]
    # RECORD: karar eserleri bayt bayt aynı
    assert _diff(_decision_view(rec), _decision_view(off)) == []
    eng = rec["eng"]
    # anlamlı senaryo: gerçek etkinlik oldu ve katman satır yazdı
    books = {b.key: (len(b.ledger.positions), len(b.ledger.history)) for b in eng.strategy_books}
    assert sum(sum(v) for v in books.values()) > 0 and rec["rows"] > 0, books
    assert rec["health"]["shared_experience"]["state"] == "OK"
    assert set(rec["health"]["shared_experience"]) == {"mode", "state", "rows_total", "rows_last", "drafts", "step_ms",
                                                       "errors", "disk_mb"}
    st = rec["status"]
    c = st["counters"]
    assert c["errors_total"] == 0 and c["rows_total"] == rec["rows"] and st["drafts"] == 0
    assert c["rows_by_kind"].get("xp_entry", 0) > 0 and c["rows_by_kind"].get("xp_outcome", 0) > 0
    assert set(c["snapshot_status_mix"]) == {"OK"}
    if lm:                                   # kill switch turu → kâğıt defter karşı-olgusalları; sıfırlama → aynı sinyal açılır
        assert c["rows_by_kind"].get("xp_cf", 0) > 0 and c["vanished_by_status"].get("SUPERSEDED", 0) > 0


# ============================================================================ arıza enjeksiyonu
def _fault_snapshot(eng, mp):
    import tradingbot.shared_experience.situation as S

    def boom(*a, **k):
        raise RuntimeError("snapshot boom")
    mp.setattr(S, "snapshot", boom)


class _FailFsyncOS:
    def __getattr__(self, name):
        return getattr(os, name)

    @staticmethod
    def fsync(fd):
        raise OSError(28, "No space left on device")


def _fault_store_io(eng, mp):
    import tradingbot.shared_experience.store as store_mod
    mp.setattr(store_mod, "os", _FailFsyncOS())                     # yalnız deponun `os`u; motorun yazımları etkilenmez


def _fault_lock_busy(eng, mp):
    import tradingbot.shared_experience.collector as XC
    foreign = threading.Lock()
    held = threading.Event()

    def hold():
        foreign.acquire()
        held.set()
    th = threading.Thread(target=hold, daemon=True)
    th.start()
    held.wait(5)
    orig = XC.build_adapters

    def busy_adapters(e):
        ads, skipped = orig(e)
        for ad in ads:
            ad._lock = (lambda: foreign)
        return ads, skipped
    mp.setattr(XC, "build_adapters", busy_adapters)


def _fault_cursor_corrupt(eng, mp):
    d = eng.cfg.state_path / EXCLUDE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "cursor.json").write_text("{bozuk", encoding="utf-8")


def _fault_from_engine(eng, mp):
    import tradingbot.shared_experience.collector as XC

    def bad(cls, e):
        raise OSError("kurulum boom")
    mp.setattr(XC.SharedExperienceCollector, "from_engine", classmethod(bad))


def _fault_step(eng, mp):
    import tradingbot.shared_experience.collector as XC

    def boom(self, *a, **k):
        raise KeyError("step boom")
    mp.setattr(XC.SharedExperienceCollector, "_run", boom)


FAULTS = {
    "snapshot_raises": (_fault_snapshot, dict(RECORD)),
    "store_oserror": (_fault_store_io, dict(RECORD)),
    "lock_busy": (_fault_lock_busy, dict(RECORD, lock_timeout_s=0.0)),
    "cursor_corrupt": (_fault_cursor_corrupt, dict(RECORD)),
    "from_engine_raises": (_fault_from_engine, dict(RECORD)),
    "step_raises": (_fault_step, dict(RECORD)),
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_fault_injection_keeps_every_decision_artefact_identical_and_counts(tmp_path, monkeypatch, _cache, fault):
    off = _baseline(tmp_path, monkeypatch, _cache, True)
    prep, xp = FAULTS[fault]
    run = _run(tmp_path / fault, monkeypatch, lm=True, xp=xp, prepare=prep)
    assert _diff(_decision_view(run), _decision_view(off)) == [], fault
    eng, st = run["eng"], run["status"]
    if fault == "from_engine_raises":
        assert eng.__dict__["_shared_xp"] is False and eng.__dict__["_shared_xp_fail"] == 3
        assert "shared_experience" not in run["health"] and st is None
        return
    c = st["counters"]
    if fault == "snapshot_raises":
        assert c["snapshot_errors"] > 0 and c["snapshot_status_mix"].get("ERROR", 0) > 0
        assert set(c["snapshot_status_mix"]) == {"ERROR"}
    elif fault == "store_oserror":
        # fsync hatası: toplu yazım BAŞARISIZ sayılır → imleç ilerlemez, taslaklar kalır (diskteki olası kopya satırlar
        # okuma yolunda `row_id` ile tekilleşir — depo sözleşmesi)
        assert c["write_errors"] == 4 and c["rows_total"] == 0 and st["drafts"] > 0
    elif fault == "lock_busy":
        assert c["lock_busy_skips"] > 0 and c["rows_by_kind"].get("xp_entry", 0) == 0
    elif fault == "cursor_corrupt":
        assert c["cursor_corrupt"] == 1 and c["rows_total"] > 0
    elif fault == "step_raises":
        assert c["errors_total"] == 4 and st["breaker"]["consecutive_errors"] == 4 and st["last_error_code"]
    assert run["health"]["shared_experience"]["mode"] == "RECORD"


# ============================================================================ AST koruması
#: Defter / kitap / kayıtçı / gölge defteri / öğrenici DEĞİŞTİREN yöntem adları (SPEC §1.1, §13 T6).
MUTATORS = {"save", "close_manual", "close_partial", "tick", "guarded_tick", "record", "record_entry", "record_decision",
            "supersede", "label_pending", "label", "label_records", "relabel_net", "settle_late_funding",
            "reconcile_funding", "apply_action", "apply_closed_bars_to_ledger", "evaluate", "evaluate_kill_triggers",
            "open", "add", "step", "set_learning", "bump", "trip", "reset", "_shadow_add", "_lm_cf_supersede", "_lm_cf_cap",
            "_lm_cf_forget", "_enforce_cap", "sync_book_counters", "on_trade_closed", "learn", "observe", "train_challenger",
            "append_decision", "append_outcome", "_finalize", "_close_part", "import_legacy_ledger"}
#: Sahibi olunmayan nesnelerin yazılmaması gereken alanları.
FOREIGN_FIELDS = {"features", "meta", "stop", "initial_stop", "targets", "plans", "trades", "positions", "history",
                  "outcome", "labeled_at", "ledger", "cf", "sb", "shadow", "entries", "last_frames", "_frame_provenance",
                  "_seen_signals", "_lm_since_doc", "_code_sha_cache", "_config_hash_cache", "qty", "status"}
_MUTATING_METHODS = {"update", "pop", "popitem", "setdefault", "clear", "append", "extend", "insert", "remove",
                     "__setitem__", "__delitem__", "sort", "reverse", "discard"}


def _set_names(fn: ast.AST) -> set[str]:
    """Fonksiyon içinde `set()` / küme ifadesiyle kurulan ya da `set[...]` ile açıklanan yerel adlar."""
    out: set[str] = set()
    args = getattr(fn, "args", None)
    for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)) if args is not None else []:
        if a.annotation is not None and ast.unparse(a.annotation).startswith("set"):
            out.add(a.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            ann = ast.unparse(n.annotation)
            if ann.startswith("set"):
                out.add(n.target.id)
        if isinstance(n, ast.Assign):
            v = n.value
            is_set = isinstance(v, (ast.Set, ast.SetComp)) or (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                                                                and v.func.id == "set")
            if is_set:
                out.update(t.id for t in n.targets if isinstance(t, ast.Name))
    return out


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []
    fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [tree]
    for fn in fns:
        local_sets = _set_names(fn)
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr in MUTATORS:
                    recv = f.value
                    if f.attr == "add" and isinstance(recv, ast.Name) and recv.id in local_sets:
                        continue                                  # yerel küme
                    if f.attr == "add" and isinstance(recv, ast.Attribute) and recv.attr == "_seen":
                        continue                                  # deponun kendi kimlik kümesi
                    bad.append("%s:%d call .%s()" % (path.name, n.lineno, f.attr))
                if isinstance(f, ast.Name) and f.id == "open" and path.name != "store.py":
                    bad.append("%s:%d builtin open()" % (path.name, n.lineno))
                if isinstance(f, ast.Name) and f.id in ("setattr", "delattr", "RiskEngine"):
                    bad.append("%s:%d %s()" % (path.name, n.lineno, f.id))
                if (isinstance(f, ast.Attribute) and f.attr in _MUTATING_METHODS and isinstance(f.value, ast.Attribute)
                        and f.value.attr in FOREIGN_FIELDS):
                    bad.append("%s:%d mutates .%s.%s()" % (path.name, n.lineno, f.value.attr, f.attr))
            targets = []
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if isinstance(n, ast.Delete):
                targets = n.targets
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Attribute) and sub.attr in FOREIGN_FIELDS and isinstance(sub.ctx, (ast.Store, ast.Del)):
                        bad.append("%s:%d assigns .%s" % (path.name, n.lineno, sub.attr))
                    if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Attribute) \
                            and sub.value.attr in FOREIGN_FIELDS:
                        bad.append("%s:%d assigns .%s[...]" % (path.name, n.lineno, sub.value.attr))
            if isinstance(n, ast.Name) and n.id == "RiskEngine":
                bad.append("%s:%d references RiskEngine" % (path.name, n.lineno))
    return sorted(set(bad))


def test_ast_guard_the_package_never_mutates_books_ledgers_recorders_or_learners():
    files = sorted(PKG.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "situation.py", "cache.py", "rows.py", "store.py", "bars.py",
                                       "collector.py"}
    bad = [v for f in files for v in _violations(f)]
    assert bad == []


def test_ast_guard_detects_what_it_claims_to_detect(tmp_path):
    """Kendi kendini sınar: yasak kalıpların her biri yakalanır (boş koruma testi geçmesin)."""
    src = ("def f(book, pos, rec, s: set[str]):\n"
           "    book.ledger.close_manual('X', 1)\n    book.cf.supersede(signal_key='k')\n    pos.features['a'] = 1\n"
           "    pos.meta.update({})\n    rec.outcome = None\n    book.cf.sb.trades.append(1)\n    setattr(pos, 'stop', 1)\n"
           "    x = set()\n    x.add(1)\n    s.add(2)\n    book.shadow.add({}, [])\n    open('p', 'w')\n")
    p = tmp_path / "probe.py"
    p.write_text(src, encoding="utf-8")
    bad = _violations(p)
    for needle in (".close_manual()", ".supersede()", "assigns .features[...]", "mutates .meta.update()",
                   "assigns .outcome", "mutates .trades.append()", "setattr()", "builtin open()"):
        assert any(needle in b for b in bad), (needle, bad)
    assert sum(".add()" in b for b in bad) == 1, "yalnız gölge defterinin add'i; yerel kümeler serbest"


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = ("." * n.level) + (n.module or "")
            mods.add(base)
            mods.update(base + "." + a.name for a in n.names)
    return mods


def test_only_the_engine_and_the_cli_import_the_package_and_learning_or_replay_never_reference_it():
    importers = []
    for p in sorted((ROOT / "tradingbot").rglob("*.py")):
        if PKG in p.parents:
            continue
        txt = p.read_text(encoding="utf-8")
        if "shared_experience" not in txt:
            continue
        mods = _imports_of(p)
        if any("shared_experience" in m for m in mods):
            importers.append(p.relative_to(ROOT).as_posix())
    assert set(importers) <= {"tradingbot/engine_v3.py", "tradingbot/cli_v3.py"}, importers
    assert "tradingbot/engine_v3.py" in importers
    for p in sorted((ROOT / "tradingbot" / "learn").rglob("*.py")) + sorted((ROOT / "tradingbot" / "replay").rglob("*.py")) + \
            [ROOT / "tradingbot" / n for n in ("learning_mode.py", "learning_basis.py", "learning_cf.py", "strategy_paper.py",
                                               "box_timer.py")] + sorted((ROOT / "tradingbot" / "pattern_trader").rglob("*.py")):
        assert "shared_experience" not in p.read_text(encoding="utf-8"), p
    # paket başı hiçbir şey içe aktarmaz (kapalı yol yüklemesiz kalsın)
    init = ast.parse((PKG / "__init__.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(init) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_engine_hook_is_exactly_one_step_call_after_the_journal_and_one_health_line():
    src = (ROOT / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    assert src.count("self._shared_experience_step(risk_log, decisions, briefs, now)") == 1
    i_j = src.index("        self._journal_decisions(risk_log, decisions, now)\n")
    i_x = src.index("self._shared_experience_step(risk_log, decisions, briefs, now)")
    i_pm = src.index("self._write_position_management(marks, decisions, now)")
    assert i_j < i_x < i_pm, "kanca karar günlüğünden SONRA, pozisyon yönetimi gözleminden ÖNCE"
    assert src.count('health["shared_experience"] = _xp.health()') == 1
    assert src.count("from .shared_experience.collector import SharedExperienceCollector") == 1


# ============================================================================ replay
def test_replay_determinism_hash_is_unchanged_with_record_configured(tmp_path):
    from test_quant_replay_e2e import H4, T0, _cfg, _comparable, _store

    from tradingbot.config_v3 import SharedExperienceSection
    from tradingbot.replay import HistoricalReplay, walk_forward_windows
    store = _store(tmp_path)

    def run(cfg, rid):
        rep = HistoricalReplay(cfg, run_id=rid, store=store, symbols=["BTC/USDT", "AAA/USDT"], market="futures",
                               tf="4h", seed=7, decision_stride=6, min_bars=250)
        return _comparable(rep.run(windows=walk_forward_windows(T0 + 250 * H4, T0 + 400 * H4, train_days=10,
                                                                test_days=10, tf="4h")))
    a_cfg, b_cfg = _cfg(tmp_path / "a"), _cfg(tmp_path / "b")
    b_cfg.v3.shared_experience = SharedExperienceSection(enabled=True, mode="RECORD")
    a, b = run(a_cfg, "xa"), run(b_cfg, "xb")
    assert a == b and a["determinism_hash"] and a["n_opened"] > 0
    assert not list((tmp_path / "b").rglob(EXCLUDE_DIR)), "replay toplayıcıyı ASLA koşmaz"

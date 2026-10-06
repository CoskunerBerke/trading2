# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — birim sözleşmesi ve yalıtım (docs/SYSTEM_LEARNING_ENGINE_V1.md §2.2–§2.5, §2.8, §9.2;
P1a depo kabul testleri 9, 10 ve 11).

* 9: `deploy/tradingbot-engine-night.{service,timer}` — `OnCalendar` UTC ve 4h yayın pencereleri dışında, iç son tarih
  ve sert durma 04:00'dan önce; Nice/CPUWeight/IO/OOM; `ReadWritePaths` yalnız `data/research`, `PrivateNetwork`,
  `PrivateTmp`, `ProtectSystem=strict`; `EnvironmentFile` yok, ortam listesi AYNEN §2.3; `ENGINE_EXPECTED_MEMORY_MAX` =
  `MemoryMax` (bayt); `python -s -m`, `-I` yok, `WorkingDirectory=/opt/tradingbot/engine-app`.
* 10: `systemd-analyze verify` temiz (araç varsa; VPS yolu olmayan `ExecStart` yorumlayıcısı yerel Python'la
  değiştirilerek — gerçek yol sürüm betiğinin `--dry-run`'ında VPS'te doğrulanır). `shellcheck` bu aşamada kabuk
  betiği olmadığı için uygulanmaz (sürüm betiği aşaması).
* 11: AST yalıtımı — karar modülleri `research_engine`'i import etmez; `cli.py`/`cli_v3.py`'de ÜST DÜZEY
  `research_engine` import'u yoktur (gerçek bir alt süreçte `import tradingbot.cli` motoru yüklemez); `research_engine`
  `config_v3`/`load_config`/`sqlite3` ve ağ modüllerini import etmez (istisnalar: `selfcheck.probe_socket_denied`'in
  RET denemesi ve P1b veri biriminin `datastore.urllib_http`'si); gece biriminin import grafiği P1b'nin ağlı modüllerini
  içermez; engine-* komutları (P1b `engine-data` dahil) config yüklemez.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeVps  # noqa: E402

from tradingbot.research_engine import night as N  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "deploy" / "tradingbot-engine-night.service"
TIMER = ROOT / "deploy" / "tradingbot-engine-night.timer"
PKG = ROOT / "tradingbot" / "research_engine"

#: §2.3: birimdeki ortam satırlarının TAMAMI (fazlası ya da eksiği sözleşme ihlalidir).
ALLOWED_ENV = {"TRADINGBOT_DATA": "/opt/tradingbot/data", "TRADINGBOT_STATE_DIR": "/opt/tradingbot/data/state",
               "ALLOW_LIVE_TRADING": "false", "TZ": "UTC", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               "PYTHONDONTWRITEBYTECODE": "1", "ENGINE_EXPECTED_MEMORY_MAX": None}


def parse_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    cur = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = out.setdefault(line[1:-1], {})
            continue
        assert cur is not None and "=" in line, f"{path.name}: çözülemeyen satır {raw!r}"
        k, _, v = line.partition("=")
        cur.setdefault(k.strip(), []).append(v.strip())
    return out


def one(sec: dict[str, list[str]], key: str) -> str:
    v = sec.get(key)
    assert v is not None and len(v) == 1, f"{key}: tam bir kez olmalı ({v})"
    return v[0]


def size_bytes(s: str) -> int:
    m = re.fullmatch(r"(\d+)([KMGT]?)", s.strip())
    assert m, s
    return int(m.group(1)) * {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[m.group(2)]


def span_seconds(s: str) -> int:
    units = {"d": 86400, "h": 3600, "min": 60, "m": 60, "s": 1}
    total, pos = 0, 0
    for m in re.finditer(r"(\d+)\s*(min|d|h|m|s)", s):
        assert m.start() == pos, s
        total += int(m.group(1)) * units[m.group(2)]
        pos = m.end()
    assert pos == len(s.strip()) and total > 0, s
    return total


# ============================================================================ kabul 9: birim sözleşmesi
def test_night_timer_is_utc_and_outside_4h_publication_windows():
    t = parse_unit(TIMER)["Timer"]
    cals = t["OnCalendar"]
    assert cals, "OnCalendar var"
    for cal in cals:
        assert cal.endswith(" UTC"), f"OnCalendar açıkça UTC yazmalı: {cal}"
        m = re.fullmatch(r"\*-\*-\* (\d{2}):(\d{2}):(\d{2}) UTC", cal)
        assert m, cal
        hh, mm = int(m.group(1)), int(m.group(2))
        assert not (hh % 4 == 0 and mm <= 35), f"{cal}: hh:00–hh:35 4h yayın penceresi"
        acc = span_seconds(one(t, "AccuracySec")) if "AccuracySec" in t else 60
        end_min = mm + -(-acc // 60)
        assert not (hh % 4 == 0 and end_min <= 35) and (hh % 4 != 0 or mm > 35)
        assert "RandomizedDelaySec" not in t, "rastgele gecikme pencere denetimini bozar"
        svc = parse_unit(SERVICE)["Service"]
        hard = hh * 3600 + mm * 60 + span_seconds(one(svc, "TimeoutStartSec"))
        assert hard <= 4 * 3600, "sert durma 04:00 yayın penceresinden önce (03:52)"
        from datetime import datetime, timezone
        start = datetime(2026, 10, 5, hh, mm, tzinfo=timezone.utc)
        dl = N.compute_deadline(start)
        assert (dl.hour, dl.minute) == (3, 40) and dl < start.replace(hour=4, minute=0), "iç son tarih 03:40"
    assert one(t, "Persistent") == "false" and one(t, "Unit") == "tradingbot-engine-night.service"
    assert parse_unit(TIMER)["Install"]["WantedBy"] == ["timers.target"]


def test_night_service_contract():
    u = parse_unit(SERVICE)
    s = u["Service"]
    assert one(s, "Type") == "oneshot" and one(s, "User") == "tradingbot" and one(s, "Group") == "tradingbot"
    assert one(s, "Nice") == "19" and one(s, "CPUWeight") == "10" and one(s, "IOSchedulingClass") == "idle"
    assert one(s, "IOWeight") == "10" and one(s, "OOMScoreAdjust") == "1000" and one(s, "CPUQuota") == "100%"
    assert one(s, "ProtectSystem") == "strict" and one(s, "PrivateTmp") == "yes" and one(s, "PrivateNetwork") == "yes"
    assert s["ReadWritePaths"] == ["/opt/tradingbot/data/research"], "yazılabilir tek yol araştırma kökü"
    ro = " ".join(s["ReadOnlyPaths"]).split()
    assert set(ro) == {"/opt/tradingbot/data", "/opt/tradingbot/app"}
    assert "EnvironmentFile" not in s and "SupplementaryGroups" not in s, "sır yok; günlük okuma yalnız veri biriminde"
    env: dict[str, str] = {}
    for line in s["Environment"]:
        k, _, v = line.partition("=")
        assert k not in env, f"tekrar eden ortam {k}"
        env[k] = v
    assert set(env) == set(ALLOWED_ENV), set(env) ^ set(ALLOWED_ENV)
    for k, want in ALLOWED_ENV.items():
        if want is not None:
            assert env[k] == want, k
    mem_max = size_bytes(one(s, "MemoryMax"))
    assert int(env["ENGINE_EXPECTED_MEMORY_MAX"]) == mem_max, "ENGINE_EXPECTED_MEMORY_MAX = MemoryMax (bayt)"
    assert size_bytes(one(s, "MemoryHigh")) < mem_max <= 512 << 20, "P1a: MemoryHigh 0,4G / MemoryMax 0,5G"
    exe = one(s, "ExecStart").split()
    assert exe == ["/opt/tradingbot/venv/bin/python", "-s", "-m", "tradingbot", "engine-night"], exe
    assert "-I" not in exe and not any(a.startswith("-I") for a in exe)
    assert one(s, "WorkingDirectory") == "/opt/tradingbot/engine-app"
    assert "OnFailure" not in u.get("Unit", {}), "uyarı birimi yalnız VPS'te varsa sürüm betiğinin drop-in'iyle"
    assert one(s, "NoNewPrivileges") == "yes" and one(s, "CapabilityBoundingSet") == ""
    assert "Install" not in u, "servis zamanlayıcıyla tetiklenir; doğrudan etkinleştirilmez"


def test_night_deadline_constants_match_the_unit():
    s = parse_unit(SERVICE)["Service"]
    assert N.DEADLINE_AFTER.total_seconds() < span_seconds(one(s, "TimeoutStartSec")), "iç son tarih sert durmadan önce"
    assert span_seconds(one(s, "TimeoutStartSec")) - N.DEADLINE_AFTER.total_seconds() == 12 * 60


# ============================================================================ kabul 10: systemd-analyze verify
def test_systemd_analyze_verify_is_clean(tmp_path):
    exe = shutil.which("systemd-analyze")
    if not exe:
        pytest.skip("systemd-analyze yok (sürüm betiğinin --dry-run'ı VPS'te koşar)")
    d = tmp_path / "units"
    d.mkdir()
    svc = SERVICE.read_text(encoding="utf-8").replace("/opt/tradingbot/venv/bin/python", os.path.realpath(sys.executable))
    (d / SERVICE.name).write_text(svc, encoding="utf-8")
    shutil.copy(TIMER, d / TIMER.name)
    cp = subprocess.run([exe, "verify", str(d / SERVICE.name), str(d / TIMER.name)], capture_output=True, text=True,
                        timeout=120)
    ours = [ln for ln in (cp.stdout + cp.stderr).splitlines() if "tradingbot-engine-night" in ln]
    assert cp.returncode == 0 and not ours, cp.stdout + cp.stderr


# ============================================================================ kabul 11: AST yalıtımı
DECISION_FILES = ("engine_v3.py", "engine.py", "box_timer.py", "box_theory.py", "protective_monitor.py", "paper_rules.py",
                  "candle_book.py", "donchian_trend.py", "ema200_trend.py", "economics_gate.py")
DECISION_GLOBS = ("strategy_paper*.py", "pattern_trader/**/*.py", "coinhead/**/*.py", "learn/**/*.py", "execution/**/*.py",
                  "accounting/**/*.py", "risk/**/*.py", "shared_experience/**/*.py", "dashboard/**/*.py")


def _imports(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            out.append((node, mod))
            out += [(node, f"{mod}.{a.name}") for a in node.names]
    return out


def test_decision_modules_never_import_research_engine():
    tb = ROOT / "tradingbot"
    files = [tb / f for f in DECISION_FILES]
    for g in DECISION_GLOBS:
        files += sorted(tb.glob(g))
    assert len(files) > 60 and all(f.exists() for f in files)
    for f in files:
        src = f.read_text(encoding="utf-8")
        assert "research_engine" not in src, f"{f.relative_to(ROOT)}: karar modülü motoru anmamalı/import etmemeli"


def _top_level_imports(tree: ast.Module) -> list[str]:
    """Modül düzeyinde (fonksiyon/sınıf gövdeleri HARİÇ, if/try/with gövdeleri DAHİL) import edilen adlar."""
    names: list[str] = []

    def visit(stmts):
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                names.extend(n for _, n in _imports(st))
            for field in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(st, field, None)
                if isinstance(sub, list):
                    visit([x for x in sub if isinstance(x, ast.stmt)] + [h for h in sub if isinstance(h, ast.ExceptHandler)])
            if isinstance(st, ast.ExceptHandler):
                visit(st.body)
    visit(tree.body)
    return names


def test_cli_modules_have_no_top_level_research_engine_import_and_handlers_import_lazily():
    for name in ("cli.py", "cli_v3.py"):
        tree = ast.parse((ROOT / "tradingbot" / name).read_text(encoding="utf-8"))
        top = _top_level_imports(tree)
        assert top and not [n for n in top if "research_engine" in n], f"{name}: üst düzey research_engine import'u"
    tree = ast.parse((ROOT / "tradingbot" / "cli_v3.py").read_text(encoding="utf-8"))
    lazy = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
            and any("research_engine" in n for _, n in _imports(fn))}
    assert lazy == {"cmd_engine_night", "cmd_engine_status", "cmd_engine_restore", "cmd_engine_data", "cmd_engine_query"}, lazy


def test_importing_cli_does_not_load_research_engine_in_a_real_process():
    code = ("import sys, tradingbot.cli as c; c.build_parser(); "
            "print(sorted(m for m in sys.modules if m.startswith('tradingbot.research_engine')))")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TRADINGBOT_", "ENGINE_"))}
    env["PYTHONPATH"] = str(ROOT)
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), env=env,
                        timeout=180)
    assert cp.returncode == 0, cp.stderr[-2000:]
    assert cp.stdout.strip().splitlines()[-1] == "[]", cp.stdout


_FORBIDDEN_TOP = {"config_v3", "sqlite3", "requests", "socket", "urllib", "http", "ccxt", "aiohttp", "httpx", "websocket",
                  "websockets", "ssl", "ftplib", "smtplib"}


def _socket_probe_lines() -> range:
    tree = ast.parse((PKG / "selfcheck.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "probe_socket_denied")
    return range(fn.lineno, fn.end_lineno + 1)


def _data_unit_http_lines() -> range:
    """P1b: ağ kullanan TEK motor yolu `datastore.urllib_http` (veri birimi; §2.8)."""
    tree = ast.parse((PKG / "datastore.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "urllib_http")
    return range(fn.lineno, fn.end_lineno + 1)


def test_research_engine_import_whitelist_and_forbidden_modules():
    stdlib = set(sys.stdlib_module_names)
    probe = _socket_probe_lines()
    http_fn = _data_unit_http_lines()
    for f in sorted(PKG.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node, name in _imports(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "load_config" for a in node.names):
                raise AssertionError(f"{f.name}: load_config")
            if name.startswith("."):
                continue
            top = name.split(".")[0]
            parts = set(name.split("."))
            if f.name == "selfcheck.py" and name == "socket" and node.lineno in probe:
                continue                                       # istisna 1: soket RET denemesi (§2.8 madde 2)
            if f.name == "datastore.py" and name.split(".")[0] == "urllib" and node.lineno in http_fn:
                continue                                       # istisna 2 (P1b): veri biriminin HTTP GET'i
            assert not parts & _FORBIDDEN_TOP, f"{f.name}:{node.lineno}: yasaklı import {name}"
            assert top in stdlib or top in {"yaml", "pandas"}, f"{f.name}: izin dışı import {name}"
            assert top != "tradingbot", f"{f.name}: paket dışı tradingbot import'u {name} (motor kendi kendine yeter)"


def _graph(start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        tree = ast.parse((PKG / f"{m}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    todo.append(node.module.split(".")[0])
                else:                                   # `from . import x`: alt modül ya da __init__ adı
                    todo += [a.name if (PKG / f"{a.name}.py").exists() else "__init__" for a in node.names]
    return seen


def test_night_import_graph_has_no_network_modules():
    """§2.8: gece biriminin import grafiği ağ kullanan modülleri (`datastore`, `pit_universe`) İÇERMEZ. P2b'den itibaren
    S1b/S2/UTC günü mühürlü depoyu (`store`/`provider`) okur; §2.8 bunları dışlamaz (P2 uygulama notları madde 12)."""
    g = _graph("night")
    assert {"selfcheck", "closes", "daily_target", "summary", "backup", "rawconfig", "ledgers", "paths", "lock"} <= g
    assert {"journal", "analysis", "attribution", "cfgrid", "utc_day", "report"} <= g
    assert not g & {"datastore", "pit_universe"}, g
    for m in g:
        assert (PKG / f"{m}.py").exists()


def test_engine_night_cli_skips_load_config_and_stops_when_unsandboxed(tmp_path, monkeypatch, capsys):
    import tradingbot.cli as cli

    def no_config(*a, **k):
        raise AssertionError("engine-night load_config çağırmaz")
    monkeypatch.setattr(cli, "load_config", no_config)
    v = FakeVps(tmp_path)
    (v.data / "market").mkdir()
    (v.state / "mode.json").write_text('{"mode": "PAPER"}', encoding="utf-8")
    monkeypatch.setenv("TRADINGBOT_DATA", str(v.data))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(v.state))
    monkeypatch.delenv("ENGINE_EXPECTED_MEMORY_MAX", raising=False)
    import errno
    import socket

    class NoNet:                                     # testte gerçek ağ denemesi yapılmaz (CI ağ çağrısı yapmaz)
        def __init__(self, *a, **k):
            pass

        def settimeout(self, t):
            pass

        def connect(self, addr):
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

        def close(self):
            pass
    monkeypatch.setattr(socket, "socket", NoNet)
    import tradingbot.research_engine.night as N
    from tradingbot.research_engine import paths as P
    real_guard = P.disk_guard
    # disk: sunucunun gerçek boş alanından bağımsız (gerçek statvfs yolu test_research_engine_night'ta ayrıca sınanır)
    monkeypatch.setattr(N, "disk_guard", lambda paths, **kw: real_guard(paths, **{**kw, "free_bytes": 50 * P.GB}))
    rc = cli.main(["engine-night", "--app-dir", str(tmp_path / "app")])
    out = capsys.readouterr().out
    assert rc == 3 and "ISOLATION_BROKEN" in out, "birim dışında (yazılabilir state, ortam yok) çalıştırma durur"
    rs = json.loads((v.data / "research" / "summary" / "run_status.json").read_text(encoding="utf-8"))
    assert rs["selfcheck"]["isolation"]["broken"] == ["write_denied_state", "write_denied_market", "write_denied_app",
                                                      "memory_max"], "app ağacı YOK: yanlış yol bozuk sayılır"
    assert sorted(p.name for p in v.state.iterdir()) == ["mode.json"], "deneme dosyaları silindi; state'e başka yazım yok"
    assert not (v.data / "market").exists() or not list((v.data / "market").iterdir())


def test_engine_status_daily_cli_prints_the_scorecard_view_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """VPS kabul 6: `engine-status --daily` scorecard --daily ile aynı tanımlı tabloyu basar (salt-okunur; config yok)."""
    import tradingbot.cli as cli
    from research_engine_fixtures import at, night_of, trade
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: (_ for _ in ()).throw(AssertionError("config yüklenmez")))
    v = FakeVps(tmp_path)
    trade(v.fut(), "ETH/USDT", at("2026-10-01", 3), 100.0, 101.0)
    v.night(night_of("2026-10-01"))
    v.night(night_of("2026-10-02"))
    monkeypatch.setenv("TRADINGBOT_DATA", str(v.data))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(v.state))
    before = {p: p.read_bytes() for p in v.data.rglob("*") if p.is_file()}
    assert cli.main(["engine-status", "--daily", "--days", "400"]) == 0
    out = capsys.readouterr().out
    assert "scorecard --daily ile aynı tanım" in out and "2026-10-01" in out and "Ana bot · vadeli" in out
    assert cli.main(["engine-status", "--daily", "--json", "--days", "400"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["books"]["main_fut"]["days"]["2026-10-01"]["rec"]["n"] == 1
    assert cli.main(["engine-status", "--daily", "--days", "0"]) == 2
    assert {p: p.read_bytes() for p in v.data.rglob("*") if p.is_file()} == before, "salt-okunur"

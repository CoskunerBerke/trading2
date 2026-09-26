# -*- coding: utf-8 -*-
"""ÖLÇÜM BETİĞİ (scripts/measure_protective_monitor.py) — başlatma/durdurma, temizlik ve rapor yazımı (ağsız).

KUSUR (2026-09-24, PR #1 incelemesi): `RssSampler._stop` alanı `threading.Thread._stop` iç metodunu eziyordu; örnekleyici
başlatılıp `stop()` çağrılınca `join` → `self._stop()` → TypeError ('Event' object is not callable). `measure` bu yüzden
rapor yazmadan düşerdi. Motor burada SAHTEDİR (ağ yok); gerçek `ProtectiveMonitor` iş parçacığı çalışır. Üretim ölçümü
DEĞİLDİR.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _script():
    spec = importlib.util.spec_from_file_location("measure_protective_monitor", ROOT / "scripts" / "measure_protective_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alive(name: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_rss_sampler_starts_and_stops_cleanly():
    m = _script()
    s = m.RssSampler(every_s=0.01)
    s.start()
    s.stop()                                              # ÖNCE: TypeError ('Event' object is not callable)
    assert not s.is_alive() and s.samples >= 1 and s.max_mb > 0
    s.stop()                                              # ikinci durdurma da güvenli
    assert callable(threading.Thread._stop)


class _Stopper:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeEngine:
    """Ağsız motor: ana defterde bir açık pozisyon, M2 defteri boş. İzleyici GERÇEK `ProtectiveMonitor`."""
    tour_error: Exception | None = None
    index_error: Exception | None = None
    last = None

    def __init__(self, cfg):
        from types import SimpleNamespace
        self.cfg = cfg
        self.ledger2 = SimpleNamespace(positions={"ETH/USDT": SimpleNamespace(id="F1")})
        self.strategy_books = [SimpleNamespace(key="strategy_paper_m2", ledger=SimpleNamespace(positions={}))]
        self.pattern_book = None
        self.pattern_scanner, self.box_timer, self._refresher = _Stopper(), _Stopper(), _Stopper()
        self.protective_monitor = None
        self.drained = 0
        _FakeEngine.last = self

    def ensure_protective_monitor(self, interval_s):
        from tradingbot.accounting import TickData
        from tradingbot.core import iso
        from tradingbot.protective_monitor import ProtectiveMonitor
        eng = self

        class _Main:
            key = "main"

            def held(self):
                return {s: p.id for s, p in eng.ledger2.positions.items()}

            def protect(self, marks, marks_f, gaps, now, expect, apply_clock=None):
                eng.protective_monitor.observer.note("main", {s: {"id": expect[s]} for s in marks}, int(now.timestamp() * 1000),
                                                     source="monitor")
                return []

        class _M2:
            key = "strategy_paper_m2"

            def held(self):
                return {}

            def protect(self, *a, **k):
                return []

        def price_fn(syms, now_ms):
            from datetime import datetime, timezone
            at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
            return {s: TickData(last=100, mark=100, ts=iso(at)) for s in syms}, {s: 100.0 for s in syms}, {}
        self.protective_monitor = ProtectiveMonitor(handles_fn=lambda: [_Main(), _M2()], price_fn=price_fn,
                                                    state_path=self.cfg.state_path, interval_s=float(interval_s))
        self.protective_monitor.start()

    def tour(self, do_scan=True, obsidian=True, charts=False):
        if self.tour_error is not None:
            raise self.tour_error
        return {"run_id": "r1", "opened": [], "closed": []}

    def index_refresh_status(self):
        if self.index_error is not None:
            raise self.index_error
        return {"enabled": True, "index": {"version": 1, "events": 7, "series": 1}}

    def stop_protective_monitor(self):
        self.protective_monitor.stop(10)

    def drain_protective_closes(self):
        self.drained += 1
        return []


def _setup(tmp_path, monkeypatch, *, tour_error=None, index_error=None, config_text: str = "mode: PAPER\n"):
    src = tmp_path / "src"
    (src / "state").mkdir(parents=True)
    (src / "state" / "futures_ledger.json").write_text('{"x": 1}', encoding="utf-8")
    (src / "market").mkdir()
    (src / "market" / "part.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(config_text, encoding="utf-8")
    for k in ("TRADINGBOT_DATA", "TRADINGBOT_STATE_DIR", "TRADINGBOT_CACHE_DIR", "TRADINGBOT_VAULT_PATH", "TRADINGBOT_LOG_DIR",
              "TRADINGBOT_BACKUPS_DIR", "TRADINGBOT_VAULT_GIT_SYNC"):
        monkeypatch.delenv(k, raising=False)                # betik ortamı kendi içinde geri yükler; test de temiz başlar
    monkeypatch.setattr(_FakeEngine, "last", None)
    monkeypatch.setattr(_FakeEngine, "tour_error", tour_error)
    monkeypatch.setattr(_FakeEngine, "index_error", index_error)
    monkeypatch.setattr("tradingbot.engine_v3.TradingEngineV3", _FakeEngine)
    out = tmp_path / "olcum.json"
    args = argparse.Namespace(source=str(src), work=str(tmp_path / "work"), work_is_disposable=False, config=str(cfg),
                              interval=60.0, index_wait=5.0, no_scan=True, no_obsidian=True, out=str(out), verbose=False)
    digest = {p.relative_to(src).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}
    return src, args, out, digest


def _src_digest(src: Path) -> dict:
    return {p.relative_to(src).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}


def test_measure_writes_the_report_and_cleans_up_every_thread(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    assert m.cmd_measure(args) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["kind"] == "PRODUCTION_LIKE_MEASUREMENT" and rep["tour_error"] is None
    assert rep["positions_at_load"] == {"main": ["ETH/USDT"], "strategy_paper_m2": []}
    books = rep["monitor"]["books"]
    assert books["main"]["open_positions"] == 1 and books["main"]["verdict"] == "WITHIN_60S"
    assert books["strategy_paper_m2"]["verdict"] == "NOT_MEASURED_NO_OPEN_POSITION", "pozisyonsuz defter doğrulanmış SAYILMAZ"
    assert rep["monitor"]["runs"] >= 1 and rep["pattern_index"]["index"]["events"] == 7
    assert rep["memory"]["peak_rss_process_mb"] > 0 and rep["memory"]["samples"] >= 1
    eng = _FakeEngine.last
    assert eng.drained >= 1 and all(s.stopped for s in (eng.pattern_scanner, eng.box_timer, eng._refresher))
    assert not _alive("rss-sampler") and not _alive("protective-monitor")
    assert (Path(args.work) / "state" / "futures_ledger.json").exists()
    assert _src_digest(src) == digest, "kaynak kopya DEĞİŞMEDİ"


def test_a_failing_tour_still_writes_the_report_and_stops_the_threads(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, tour_error=RuntimeError("ağ yok"))
    assert m.cmd_measure(args) == 2
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["tour_error"] == "RuntimeError: ağ yok"
    assert not _alive("rss-sampler") and not _alive("protective-monitor")


def test_an_unexpected_error_after_the_tour_still_cleans_up(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, index_error=OSError("disk"))
    with pytest.raises(OSError):
        m.cmd_measure(args)
    eng = _FakeEngine.last
    assert all(s.stopped for s in (eng.pattern_scanner, eng.box_timer, eng._refresher))
    assert not _alive("rss-sampler") and not _alive("protective-monitor")
    assert _src_digest(src) == digest


def test_an_existing_work_directory_is_never_overwritten(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    Path(args.work).mkdir()
    (Path(args.work) / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        m.cmd_measure(args)
    assert (Path(args.work) / "keep.txt").read_text(encoding="utf-8") == "x" and not out.exists()


# ---------------------------------------------------------------------------------------------- Windows uyumu
def test_the_memory_probe_uses_the_native_method_of_this_platform():
    """Linux: /proc (VmRSS/VmHWM). Windows: Win32 GetProcessMemoryInfo (ctypes, ek paket yok). CI iki platformda da koşar."""
    m = _script()
    probe = m.MemoryProbe()
    expected = {"linux": "procfs", "win32": "psapi"}.get(sys.platform)
    if expected:
        assert probe.backend == expected, (probe.backend, probe.errors)
    vals = probe.read()
    assert vals["current_mb"] > 0 and vals["peak_mb"] >= vals["current_mb"] - 1.0
    if sys.platform == "win32":
        assert vals["peak_commit_mb"] > 0
    other = "psapi" if sys.platform != "win32" else "procfs"     # bu platformda olmayan yöntem → atlanır, hata vermez
    probe_other = m.MemoryProbe(order=(other,))
    assert probe_other.backend == "none" and other in probe_other.errors and probe_other.read() == {}


def test_the_script_runs_without_any_posix_only_module(tmp_path, monkeypatch):
    """ÖNCE: betik ilk satırda `import resource` yapıyordu (yalnız POSIX) → Windows'ta hiç açılmıyordu. Bellek ölçülemese bile
    ölçüm koşar, rapor yazılır ve bellek alanları dürüstçe boş kalır."""
    import builtins
    real_import = builtins.__import__

    def no_posix(name, *a, **k):
        if name in ("resource", "psutil"):
            raise ImportError("bu platformda yok: %s" % name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_posix)
    m = _script()                                         # modül yüklenirken POSIX modülü İSTENMEZ
    monkeypatch.setattr(m, "MEMORY_ORDER", ("psapi", "psutil", "getrusage") if sys.platform != "win32" else ("psutil", "getrusage"))
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    assert m.cmd_measure(args) == 0
    mem = json.loads(out.read_text(encoding="utf-8"))["memory"]
    assert mem["backend"] == "none" and mem["peak_rss_process_mb"] is None and mem["under_vps_limit"] is None
    assert not _alive("rss-sampler")


def test_path_containment_is_correct_for_windows_paths():
    import ntpath
    import posixpath
    m = _script()
    assert m._inside(r"C:\Data\Work\state", r"c:/data/work", pathmod=ntpath), "büyük/küçük harf ve ayırıcı duyarsız"
    assert m._inside(r"C:\Data\Work", r"C:\Data\Work", pathmod=ntpath)
    assert not m._inside(r"C:\Data\Work2\state", r"C:\Data\Work", pathmod=ntpath), "önek 'Work2' içeride SAYILMAZ"
    assert not m._inside(r"D:\Data\Work\state", r"C:\Data\Work", pathmod=ntpath), "farklı sürücü"
    assert not m._inside("/tmp/work2/state", "/tmp/work", pathmod=posixpath)


def test_measure_forces_a_private_vault_and_turns_off_notifications_and_git_sync(tmp_path, monkeypatch):
    """Gerçek kasa/ayarlar asla kullanılmaz: ortamdaki kasa yolu ve git senkronu, config'teki Telegram/izleme bildirimleri
    bu süreçte kapatılır ve rapora yazılır; betik bitince ortam eski hâline döner."""
    real_vault = tmp_path / "gercek_kasa"
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, config_text=(
        "mode: PAPER\nobsidian:\n  git_sync: true\ntelegram:\n  enabled: true\nmonitoring:\n  telegram_enabled: true\n"))
    monkeypatch.setenv("TRADINGBOT_VAULT_PATH", str(real_vault))
    monkeypatch.setenv("TRADINGBOT_VAULT_GIT_SYNC", "1")
    assert m.cmd_measure(args) == 0
    cfg = _FakeEngine.last.cfg
    work = Path(args.work).resolve()
    assert Path(cfg.obsidian.vault_path).resolve() == work / "vault" and cfg.obsidian.git_sync is False
    assert cfg.v3.telegram.enabled is False and cfg.v3.monitoring.telegram_enabled is False
    assert Path(cfg.state_path).resolve() == work / "state" and Path(cfg.cache_path).resolve() == work / "market"
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert set(rep["neutralized"]) == {"obsidian.git_sync", "telegram.enabled", "monitoring.telegram_enabled"}
    assert all(m._inside(p, work) for p in rep["paths"].values())
    assert not real_vault.exists(), "gerçek kasa klasörüne dokunulmadı"
    assert os.environ["TRADINGBOT_VAULT_PATH"] == str(real_vault) and "TRADINGBOT_DATA" not in os.environ, "ortam geri yüklendi"


def test_a_config_path_outside_the_work_copy_stops_before_anything_is_copied(tmp_path, monkeypatch):
    elsewhere = tmp_path / "canli_state"
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, config_text="mode: PAPER\nstate_dir: %s\n" % elsewhere.as_posix())
    with pytest.raises(SystemExit) as ei:
        m.cmd_measure(args)
    assert "DIŞINDA" in str(ei.value) and not Path(args.work).exists() and not elsewhere.exists() and not out.exists()


def test_a_non_paper_config_is_refused(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "_load_config", lambda path: _Cfg("LIVE"))
    with pytest.raises(SystemExit) as ei:
        m.cmd_measure(args)
    assert "PAPER" in str(ei.value) and not Path(args.work).exists()


class _Cfg:
    """Yalnız mod/yol denetimi için asgari config."""

    def __init__(self, mode):
        from types import SimpleNamespace
        root = Path(os.environ["TRADINGBOT_DATA"])
        self.mode = mode
        self.state_path, self.cache_path, self.logs_path, self.backups_path = root / "state", root / "market", root / "logs", root / "b"
        self.obsidian = SimpleNamespace(vault_path=str(root / "vault"), git_sync=False)
        self.v3 = None


def test_too_little_disk_space_stops_before_copying(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "_free_bytes", lambda p: 10)
    with pytest.raises(SystemExit) as ei:
        m.cmd_measure(args)
    assert "boş disk yetersiz" in str(ei.value) and not Path(args.work).exists()


def test_preflight_reports_readiness_without_copying_or_running(tmp_path, monkeypatch, capsys):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch)
    pre = argparse.Namespace(source=args.source, work=args.work, config=args.config, net=False, work_is_disposable=False)
    assert m.cmd_preflight(pre) == 0
    text = capsys.readouterr().out
    doc = json.loads(text[: text.rindex("}") + 1])
    assert doc["ready"] is True and doc["source"]["state"] and doc["memory"]["backend"] != "none"
    assert not Path(args.work).exists() and _FakeEngine.last is None, "preflight kopyalamaz, motor kurmaz"
    bad = argparse.Namespace(source=str(tmp_path / "yok"), work=args.work, config=args.config, net=False, work_is_disposable=False)
    assert m.cmd_preflight(bad) == 1
    assert "HAZIR DEĞİL" in capsys.readouterr().out


def test_ctrl_c_during_the_tour_cleans_up_and_writes_an_interrupted_report(tmp_path, monkeypatch):
    m = _script()
    src, args, out, digest = _setup(tmp_path, monkeypatch, tour_error=KeyboardInterrupt())
    assert m.cmd_measure(args) == 130
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["tour_error"] == "INTERRUPTED"
    eng = _FakeEngine.last
    assert all(s.stopped for s in (eng.pattern_scanner, eng.box_timer, eng._refresher))
    assert not _alive("rss-sampler") and not _alive("protective-monitor")


def test_atomic_write_retries_a_windows_sharing_violation(tmp_path, monkeypatch):
    """Windows'ta hedef başka tanıtıcıda açıkken `os.replace` PermissionError verebilir: kısa aralıkla yeniden denenir; hep
    başarısızsa hata SESSİZ geçmez (StorageError)."""
    from tradingbot.core import atomic
    from tradingbot.core.errors import StorageError
    monkeypatch.setattr(atomic, "_REPLACE_RETRIES", 3)
    monkeypatch.setattr(atomic, "_REPLACE_BACKOFF_S", 0.0)
    real = os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "sharing violation")
        return real(a, b)
    monkeypatch.setattr(atomic.os, "replace", flaky)
    target = tmp_path / "x.json"
    atomic.atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1} and calls["n"] == 3
    monkeypatch.setattr(atomic.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(13, "locked")))
    with pytest.raises(StorageError):
        atomic.atomic_write_json(target, {"a": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1} and not list(tmp_path.glob("x.json.tmp-*"))

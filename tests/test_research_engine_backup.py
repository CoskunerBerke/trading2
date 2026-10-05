# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — araştırma yedeği (S7b) ve `engine-restore` (docs/SYSTEM_LEARNING_ENGINE_V1.md §3.6;
P1a depo kabul testi 15): içerik (dahil/hariç listesi, son 30 çalıştırma), sha256 yan dosyası ve hemen-yeniden-okuma
doğrulaması, 7 günlük + 4 haftalık saklama, `engine-restore` kuru çalıştırma (hiçbir şey değişmez) ve `--yes` ile
uygulama (mevcut içerik `research.pre-restore-<ts>`'e taşınır, asla silinmez; `locks/` ve `backup/` yerinde kalır),
güvensiz arşivin reddi, kilit tutulurken reddedilmesi; CLI yolunun config yüklemediği.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeHost, FakeVps, at, night_of, run_engine_night, trade  # noqa: E402

from tradingbot.research_engine import backup as B  # noqa: E402
from tradingbot.research_engine import lock as LK  # noqa: E402
from tradingbot.research_engine import paths as P  # noqa: E402

UTC = timezone.utc


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _populate(pth: P.EnginePaths) -> None:
    r = pth.research
    for sub in B.INCLUDE:
        pth.write_text(r / sub / "a" / "x.json", f'{{"sub": "{sub}"}}\n')
    for sub in B.EXCLUDE + ("bilinmeyen_klasor",):
        pth.write_text(r / sub / "big.bin", "yeniden üretilebilir\n")
    pth.write_text(r / "locks" / "analysis.lock", "")
    pth.write_text(r / "closes" / "main_fut" / ".2026-09.jsonl.gz.abc.tmp", "yarım yazım")
    for i in range(35):
        pth.write_json(r / "runs" / f"202609{i + 1:02d}T013700Z" / "run_status.json", {"i": i})
    pth.append_jsonl(r / "runs" / "engine_epochs.jsonl", [{"schema": "engine_epoch_v1", "engine_sha": "b" * 40}])


def test_backup_content_include_exclude_sha_and_immediate_verification(tmp_path):
    pth = P.EnginePaths.for_state(tmp_path / "data" / "state")
    _populate(pth)
    r = B.make_backup(pth, now=at("2026-10-05", 1, 40))
    assert r["status"] == "OK" and r["verified"] is True and r["file"] == "research-small-2026-10-05.tar.gz"
    arch = pth.backup / r["file"]
    side = (pth.backup / (r["file"] + ".sha256")).read_text(encoding="utf-8")
    assert side == f"{r['sha256']}  {r['file']}\n", "sha256sum -c biçimi"
    assert hashlib.sha256(arch.read_bytes()).hexdigest() == r["sha256"]
    with tarfile.open(arch, "r:gz") as tar:
        names = tar.getnames()
    tops = {n.split("/", 1)[0] for n in names}
    assert tops == set(B.INCLUDE) | {"runs"}, tops
    assert not any(n.startswith(B.EXCLUDE + ("bilinmeyen_klasor", "locks", "backup")) for n in names)
    assert not any(n.endswith(".tmp") for n in names), "yarım yazım dosyası yedeğe girmez"
    run_dirs = sorted({n.split("/")[1] for n in names if n.startswith("runs/") and n.count("/") == 2})
    assert len(run_dirs) == B.RUNS_KEEP and run_dirs[0] == "20260906T013700Z", "yalnız son 30 çalıştırma"
    assert "runs/engine_epochs.jsonl" in names, "runs/ kökündeki kayıt dosyaları (A/B dönemleri) dahil"
    assert set(r["excluded_present"]) >= set(B.EXCLUDE) and r["members"] == len(names)
    ok = B.verify_archive(arch)
    assert ok["ok"] and ok["expected"] == r["sha256"]
    # bozulma: tek bayt
    raw = bytearray(arch.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(bytes(raw))
    (tmp_path / "bad.tar.gz.sha256").write_text(f"{r['sha256']}  bad.tar.gz\n")
    assert B.verify_archive(bad)["error"] == "sha256 uyuşmuyor"
    (tmp_path / "bad.tar.gz.sha256").write_text(f"{hashlib.sha256(bytes(raw)).hexdigest()}  bad.tar.gz\n")
    v = B.verify_archive(bad)
    assert not v["ok"] and "tar okunamadı" in v["error"], "sha tutsa bile gzip CRC'si akış sonuna kadar okunarak denetlenir"
    lonely = tmp_path / "lonely.tar.gz"
    lonely.write_bytes(arch.read_bytes())
    assert not B.verify_archive(lonely)["ok"] and B.verify_archive(lonely, expected_sha=r["sha256"])["ok"]


def test_retention_seven_daily_plus_four_weekly():
    days = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(60)]   # 08-01 … 09-29
    keep = B.retention_keep(days)
    daily = sorted(days)[-7:]
    assert set(daily) <= keep and len(keep) == 11
    weekly = sorted(keep - set(daily))
    assert len(weekly) == 4 and len({date.fromisoformat(d).isocalendar()[1] for d in weekly}) == 4
    for d in weekly:
        wk = date.fromisoformat(d).isocalendar()[:2]
        same = [x for x in days if date.fromisoformat(x).isocalendar()[:2] == wk and x not in daily]
        assert d == max(same), "haftanın en yeni yedeği"
    assert B.retention_keep(days[:5]) == set(days[:5])


def test_prune_deletes_only_matching_names(tmp_path):
    pth = P.EnginePaths.for_state(tmp_path / "data" / "state")
    pth.ensure_tree()
    for i in range(20):
        d = (date(2026, 9, 1) + timedelta(days=i)).isoformat()
        pth.write_bytes(pth.backup / B.archive_name(d), b"x")
        pth.write_text(pth.backup / (B.archive_name(d) + ".sha256"), "x\n")
    pth.write_text(pth.backup / "sahibin-notu.txt", "dokunma\n")
    gone = B.prune_backups(pth)
    left = sorted(p.name for p in pth.backup.iterdir())
    assert "sahibin-notu.txt" in left and len(gone) == 20 - len(B.retention_keep(
        [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(20)]))
    for g in gone:
        assert g not in left and g + ".sha256" not in left


def _night_with_backup(tmp_path):
    v = FakeVps(tmp_path)
    host = FakeHost(tmp_path)
    trade(v.fut(), "ETH/USDT", at("2026-09-01", 3), 100.0, 101.0)
    st = run_engine_night(v, host, night_of("2026-09-02"))
    assert st["result"] == "SUCCESS" and st["stages"]["S7b"]["result"]["verified"]
    return v, host, st


def test_engine_restore_dry_run_changes_nothing_and_apply_moves_aside(tmp_path):
    v, host, st = _night_with_backup(tmp_path)
    r = v.paths.research
    arch = v.paths.backup / st["stages"]["S7b"]["result"]["file"]
    archived = _tree(r)
    state0 = _tree(v.state)
    # gece sonrası değişiklik: bir kapanış segmenti kaybolur, çöp eklenir
    seg = next((r / "closes" / "main_fut").glob("*.jsonl.gz"))
    seg.unlink()
    v.paths.write_text(r / "summary" / "cop.txt", "çöp\n")
    before = _tree(v.data)
    rep = B.restore(v.paths, arch.name, now=datetime(2026, 9, 2, 12, tzinfo=UTC))
    assert rep["dry_run"] is True and rep["applied"] is False and rep["verify"]["ok"] is True
    assert set(rep["kept_in_place"]) == {"backup", "locks"} and "closes" in rep["will_move_aside"]
    assert rep["aside"].endswith("research.pre-restore-20260902T120000Z") and rep["top_dirs"]["closes"] >= 1
    assert _tree(v.data) == before, "kuru çalıştırma hiçbir şeyi değiştirmez"
    lk = LK.analysis_lock(v.paths)
    assert lk.try_acquire()
    with pytest.raises(B.RestoreRefused):
        B.restore(v.paths, arch, apply=True, now=datetime(2026, 9, 2, 12, tzinfo=UTC))
    lk.release()
    assert _tree(v.data) == before
    rep = B.restore(v.paths, arch, apply=True, now=datetime(2026, 9, 2, 12, 5, tzinfo=UTC))
    assert rep["applied"] is True
    aside = v.data / "research.pre-restore-20260902T120500Z"
    assert aside.is_dir() and (aside / "summary" / "cop.txt").exists(), "önceki içerik silinmez, kenara alınır"
    assert seg.exists(), "kaybolan segment geri geldi"
    now_tree = _tree(r)
    with tarfile.open(arch, "r:gz") as tar:
        in_arch = {m.name: hashlib.sha256(tar.extractfile(m).read()).hexdigest() for m in tar if m.isfile()}
    restorable = {k: h for k, h in now_tree.items() if k.split("/", 1)[0] in B.INCLUDE + ("runs",)}
    assert restorable == in_arch, "geri yüklenen içerik = arşivin içeriği (ne eksik ne fazla)"
    assert set(in_arch) <= set(archived) and any(k.startswith("closes/") for k in in_arch)
    assert (r / "locks").is_dir() and (r / "backup" / arch.name).exists(), "locks/ ve backup/ yerinde kalır"
    assert not list(r.glob(".restore-staging-*"))
    assert _tree(v.state) == state0, "state'e dokunulmaz"
    with pytest.raises(B.RestoreRefused):
        B.aside_dir(v.paths, "../../etc")


def test_engine_restore_apply_refuses_when_not_the_research_root_owner(tmp_path, monkeypatch):
    v, host, st = _night_with_backup(tmp_path)
    arch = v.paths.backup / st["stages"]["S7b"]["result"]["file"]
    before = _tree(v.data)
    me = v.paths.research.stat().st_uid
    monkeypatch.setattr(B.os, "geteuid", lambda: me + 4242)          # ör. sahibi tradingbot olan köke root ile --yes
    rep = B.restore(v.paths, arch)
    assert rep["dry_run"] is True and rep["verify"]["ok"], "kuru çalıştırma herkes için serbest (yalnız okur)"
    with pytest.raises(B.RestoreRefused, match="sahibiyle"):
        B.restore(v.paths, arch, apply=True, now=datetime(2026, 9, 2, 12, tzinfo=UTC))
    monkeypatch.undo()
    assert _tree(v.data) == before and not list(v.data.glob("research.pre-restore-*"))


def test_engine_restore_refuses_unsafe_or_unverified_archives(tmp_path):
    pth = P.EnginePaths.for_state(tmp_path / "data" / "state")
    pth.ensure_tree()
    for name, member in (("evil", "../state/futures_ledger.json"), ("abs", "/etc/passwd"), ("top", "state/x.json")):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"{}"
            ti = tarfile.TarInfo(member)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        a = tmp_path / f"{name}.tar.gz"
        a.write_bytes(buf.getvalue())
        (tmp_path / f"{name}.tar.gz.sha256").write_text(hashlib.sha256(buf.getvalue()).hexdigest() + f"  {a.name}\n")
        rep = B.restore(pth, a)
        assert not rep["verify"]["ok"] and "güvensiz üye" in rep["error"], name
        with pytest.raises(B.RestoreRefused):
            B.restore(pth, a, apply=True)
    assert not (tmp_path / "data" / "state").exists() or not any((tmp_path / "data" / "state").iterdir())
    assert not list((tmp_path / "data").glob("research.pre-restore-*"))


def test_engine_restore_cli_dry_run_does_not_load_config(tmp_path, monkeypatch, capsys):
    v, host, st = _night_with_backup(tmp_path)
    arch = v.paths.backup / st["stages"]["S7b"]["result"]["file"]
    import tradingbot.cli as cli

    def no_config(*a, **k):
        raise AssertionError("engine-* komutları load_config çağırmaz")
    monkeypatch.setattr(cli, "load_config", no_config)
    monkeypatch.setenv("TRADINGBOT_DATA", str(v.data))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(v.state))
    before = _tree(v.data)
    assert cli.main(["engine-restore", str(arch)]) == 0
    out = capsys.readouterr().out
    assert "KURU ÇALIŞTIRMA" in out and json.loads(out[:out.rindex("}") + 1])["dry_run"] is True
    assert _tree(v.data) == before
    assert cli.main(["engine-restore", str(tmp_path / "yok.tar.gz")]) == 2
    capsys.readouterr()
    assert cli.main(["engine-status", "--brief"]) == 0
    out = capsys.readouterr().out
    assert "GECE ÖĞRENME MOTORU" in out and len(out.strip().splitlines()) <= 60
    assert _tree(v.data) == before, "engine-status salt-okunurdur (kilit almaz, yazmaz)"

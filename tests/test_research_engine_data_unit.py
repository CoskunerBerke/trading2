# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1b — VERİ birimi sözleşmesi (docs/SYSTEM_LEARNING_ENGINE_V1.md §2.2–§2.5, §2.8, §3.4, §9.2;
P1b depo kabul testi 12) ve P1b'nin birim/yedek uçları.

* 12: `deploy/tradingbot-engine-data.{service,timer}` — `OnCalendar` UTC ve 4h yayın pencereleri dışında, sert durma ve
  iç son tarih gece biriminden (01:37) önce; `SupplementaryGroups=systemd-journal` YALNIZ veri biriminde (depodaki başka
  hiçbir birimde yok); `PrivateTmp`; `ReadWritePaths` yalnız `data/research`. P1a'nın gece birimi testlerinin (kabul 9,
  10) veri birimi eşleri: Nice/CPU/IO/OOM, `ProtectSystem=strict`, ortam listesi AYNEN §2.3, `ENGINE_EXPECTED_MEMORY_MAX`
  = `MemoryMax` (bayt), `python -s -m`, `-I` yok, `systemd-analyze verify` temiz; ayrıca iki birimin yalıtım ve kaynak
  satırlarının — yalnız ağ, günlük grubu, komut, süre ve bellek dışında — AYNI olduğu (parite).
* Motor kod özeti (A/B dönemi) veri birimi dosyalarını da kapsar (gece birimi gibi; P1b uygulama notları).
* Araştırma yedeği (S7b) P1b'nin küçük ve yeniden ÜRETİLEMEYEN anlık görüntülerini (`universe/`, `exchangeinfo/`) içerir;
  depo, zip aynası ve veri çalıştırma kayıtları girmez.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import DataEnv, ms, utc  # noqa: E402
from test_research_engine_contract import ALLOWED_ENV, one, parse_unit, size_bytes, span_seconds  # noqa: E402

from tradingbot.research_engine import datastore as DS  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DSERVICE = ROOT / "deploy" / "tradingbot-engine-data.service"
DTIMER = ROOT / "deploy" / "tradingbot-engine-data.timer"
NSERVICE = ROOT / "deploy" / "tradingbot-engine-night.service"
NTIMER = ROOT / "deploy" / "tradingbot-engine-night.timer"
#: P1b çekirdek ölçümü (devir notu): `engine-data` tepe belleği ≈ 227 MiB
MEASURED_PEAK = 227 << 20


def _start(timer: Path) -> tuple[int, int]:
    cal = one(parse_unit(timer)["Timer"], "OnCalendar")
    m = re.fullmatch(r"\*-\*-\* (\d{2}):(\d{2}):(\d{2}) UTC", cal)
    assert m, cal
    return int(m.group(1)), int(m.group(2))


# ============================================================================ P1b kabul 12: veri birimi sözleşmesi
def test_data_timer_is_utc_outside_4h_windows_and_ends_before_the_night_unit():
    u = parse_unit(DTIMER)
    t = u["Timer"]
    assert len(t["OnCalendar"]) == 1 and t["OnCalendar"][0].endswith(" UTC"), "OnCalendar açıkça UTC yazmalı"
    hh, mm = _start(DTIMER)
    assert (hh, mm) == (0, 41)
    acc = span_seconds(one(t, "AccuracySec"))
    assert not (hh % 4 == 0 and mm <= 35), "hh:00–hh:35 4h yayın penceresi"
    assert "RandomizedDelaySec" not in t, "rastgele gecikme pencere denetimini bozar"
    assert one(t, "Persistent") == "false" and one(t, "Unit") == "tradingbot-engine-data.service"
    assert u["Install"]["WantedBy"] == ["timers.target"]
    s = parse_unit(DSERVICE)["Service"]
    start = datetime(2026, 10, 6, hh, mm, tzinfo=timezone.utc)
    hard = start + timedelta(seconds=acc + span_seconds(one(s, "TimeoutStartSec")))
    nh, nm = _start(NTIMER)
    night = start.replace(hour=nh, minute=nm)
    assert hard <= night - timedelta(minutes=5), f"sert durma {hard:%H:%M} gece biriminden (01:37) önce: iki birim çakışmaz"
    assert (hard.hour, hard.minute) == (1, 32), "01:31 (+ AccuracySec 1 dk)"
    dl = DS.compute_data_deadline(start)
    assert (dl.hour, dl.minute) == (1, 26) and dl < start + timedelta(seconds=span_seconds(one(s, "TimeoutStartSec")))
    for late in (0, acc):                    # zamanlayıcı en geç 00:42'de başlasa bile iç son tarih 01:26'da kalır
        assert DS.compute_data_deadline(start + timedelta(seconds=late)) <= dl
    # 4h penceresi hiçbir an: 00:41 → 01:32 aralığında hh%4==0 && dk ≤ 35 yok
    t_ = start
    while t_ <= hard:
        assert not DS.in_publication_window(t_), t_
        t_ += timedelta(minutes=1)


def test_data_service_contract():
    u = parse_unit(DSERVICE)
    s = u["Service"]
    assert one(s, "Type") == "oneshot" and one(s, "User") == "tradingbot" and one(s, "Group") == "tradingbot"
    assert one(s, "Nice") == "19" and one(s, "CPUWeight") == "10" and one(s, "IOSchedulingClass") == "idle"
    assert one(s, "IOWeight") == "10" and one(s, "OOMScoreAdjust") == "1000" and one(s, "CPUQuota") == "100%"
    assert one(s, "ProtectSystem") == "strict" and one(s, "PrivateTmp") == "yes"
    assert "PrivateNetwork" not in s, "tek ağlı motor birimi (§2.2)"
    assert s["ReadWritePaths"] == ["/opt/tradingbot/data/research"], "yazılabilir tek yol araştırma kökü"
    assert set(" ".join(s["ReadOnlyPaths"]).split()) == {"/opt/tradingbot/data", "/opt/tradingbot/app"}
    assert s["SupplementaryGroups"] == ["systemd-journal"], "worker 429/418 günlük koruması (§3.4 madde 2)"
    assert "EnvironmentFile" not in s, "sır yok"
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
    assert int(env["ENGINE_EXPECTED_MEMORY_MAX"]) == mem_max, "ENGINE_EXPECTED_MEMORY_MAX = MemoryMax (bayt; yoksa çıkış 3)"
    assert size_bytes(one(s, "MemoryHigh")) < mem_max == 1 << 30, "MemoryHigh 0,8G / MemoryMax 1G (§2.2)"
    assert MEASURED_PEAK <= 0.8 * mem_max, "VPS kabul 6: memory.peak ≤ 0,8 × MemoryMax (ölçülen ≈ 227 MiB)"
    exe = one(s, "ExecStart").split()
    assert exe == ["/opt/tradingbot/venv/bin/python", "-s", "-m", "tradingbot", "engine-data", "--update"], exe
    assert not any(a.startswith("-I") for a in exe)
    assert one(s, "WorkingDirectory") == "/opt/tradingbot/engine-app"
    fams = one(s, "RestrictAddressFamilies").split()
    assert set(fams) == {"AF_UNIX", "AF_INET", "AF_INET6"}, "ağ (IPv4/IPv6) + DNS/yerel soket; başka aile yok"
    assert "OnFailure" not in u.get("Unit", {}), "uyarı birimi yalnız VPS'te varsa sürüm betiğinin drop-in'iyle"
    assert one(s, "NoNewPrivileges") == "yes" and one(s, "CapabilityBoundingSet") == "" and one(s, "AmbientCapabilities") == ""
    assert "Install" not in u, "servis zamanlayıcıyla tetiklenir; doğrudan etkinleştirilmez"


def test_journal_group_only_in_the_data_unit():
    """`SupplementaryGroups=systemd-journal` depodaki birimlerin YALNIZ veri biriminde vardır (gece birimi ağsız ve
    günlüksüzdür; worker/panel/yedek birimleri değişmez)."""
    units = sorted((ROOT / "deploy").glob("*.service"))
    assert NSERVICE in units and DSERVICE in units and len(units) >= 5
    for p in units:
        groups = parse_unit(p).get("Service", {}).get("SupplementaryGroups", [])
        has = any("systemd-journal" in g.split() for g in groups)
        assert has == (p == DSERVICE), f"{p.name}: SupplementaryGroups={groups}"


#: iki motor biriminin BİLEREK farklı olduğu satırlar; geri kalan her [Service] satırı bayt bayt aynıdır
DIFFER = {"ExecStart", "TimeoutStartSec", "MemoryHigh", "MemoryMax", "PrivateNetwork", "SupplementaryGroups"}


def test_data_and_night_units_share_every_isolation_and_resource_line_except_network():
    d, n = parse_unit(DSERVICE)["Service"], parse_unit(NSERVICE)["Service"]
    assert {k: v for k, v in d.items() if k not in DIFFER | {"Environment"}} == \
        {k: v for k, v in n.items() if k not in DIFFER | {"Environment"}}
    strip = lambda env: [e for e in env if not e.startswith("ENGINE_EXPECTED_MEMORY_MAX=")]  # noqa: E731
    assert strip(d["Environment"]) == strip(n["Environment"]), "ortam satırları aynı (yalnız bellek beklentisi farklı)"
    assert set(d) ^ set(n) == {"PrivateNetwork", "SupplementaryGroups"}
    assert n["PrivateNetwork"] == ["yes"] and "PrivateNetwork" not in d
    dt, nt = parse_unit(DTIMER), parse_unit(NTIMER)
    assert set(dt["Timer"]) == set(nt["Timer"]) and dt["Install"] == nt["Install"]
    for k in ("AccuracySec", "Persistent"):
        assert dt["Timer"][k] == nt["Timer"][k]


def test_data_deadline_constants_match_the_unit():
    s = parse_unit(DSERVICE)["Service"]
    hard = span_seconds(one(s, "TimeoutStartSec"))
    assert DS.DEADLINE_AFTER.total_seconds() < hard and hard - DS.DEADLINE_AFTER.total_seconds() == 5 * 60
    hh, mm = _start(DTIMER)
    assert DS.DATA_DEADLINE_HM == (1, 26) and (hh * 60 + mm) + hard // 60 == 1 * 60 + 31, "sert durma 01:31"


def test_systemd_analyze_verify_data_unit_is_clean(tmp_path):
    exe = shutil.which("systemd-analyze")
    if not exe:
        pytest.skip("systemd-analyze yok (sürüm betiğinin --dry-run'ı VPS'te koşar)")
    d = tmp_path / "units"
    d.mkdir()
    svc = DSERVICE.read_text(encoding="utf-8").replace("/opt/tradingbot/venv/bin/python", os.path.realpath(sys.executable))
    (d / DSERVICE.name).write_text(svc, encoding="utf-8")
    shutil.copy(DTIMER, d / DTIMER.name)
    cp = subprocess.run([exe, "verify", str(d / DSERVICE.name), str(d / DTIMER.name)], capture_output=True, text=True,
                        timeout=120)
    ours = [ln for ln in (cp.stdout + cp.stderr).splitlines() if "tradingbot-engine-data" in ln]
    assert cp.returncode == 0 and not ours, cp.stdout + cp.stderr


# ============================================================================ motor kod özeti (A/B dönemi)
def test_engine_code_hash_covers_the_data_unit_files(tmp_path):
    assert {"deploy/tradingbot-engine-data.service", "deploy/tradingbot-engine-data.timer"} <= set(SC.ENGINE_CODE_GLOBS)
    tree = tmp_path / "tree"
    for rel in ("tradingbot/research_engine/selfcheck.py", "deploy/tradingbot-engine-night.service",
                "deploy/tradingbot-engine-night.timer", "deploy/tradingbot-engine-data.service",
                "deploy/tradingbot-engine-data.timer"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tree / rel)
    h0 = SC.engine_code_hash(tree)
    p = tree / "deploy" / "tradingbot-engine-data.service"
    p.write_text(p.read_text(encoding="utf-8").replace("MemoryHigh=800M", "MemoryHigh=700M"), encoding="utf-8")
    assert h0 and SC.engine_code_hash(tree) != h0, "veri birimi değişirse yeni A/B dönemi"


# ============================================================================ araştırma yedeği: P1b eki
def test_research_backup_keeps_universe_and_exchangeinfo_but_not_the_store(tmp_path):
    from tradingbot.research_engine import backup as B
    e = DataEnv(tmp_path, start=utc(2026, 10, 6, 0, 41), listing={"futures/BTCUSDT": ms(utc(2026, 9, 20))},
                only={"futures/BTCUSDT/1d"})
    assert e.run("backfill")["exit_code"] == 0
    assert e.run("update")["exit_code"] == 0
    res = e.paths.research
    assert (res / "universe" / "2026-10-06.json").is_file() and (res / "exchangeinfo" / "2026-10-06.json.gz").is_file()
    r = B.make_backup(e.paths, now=utc(2026, 10, 6, 3, 30))
    assert r["status"] == "OK" and r["verified"] is True
    with tarfile.open(e.paths.backup / r["file"], "r:gz") as tar:
        names = tar.getnames()
    assert "universe/2026-10-06.json" in names and "exchangeinfo/2026-10-06.json.gz" in names
    assert "summary/data_status.json" in names
    assert not [n for n in names if n.startswith(("store/", "archive_cache/", "runs/data/", "dukascopy/", "locks/"))], names
    small = sum((res / n).stat().st_size for n in names if n.startswith(("universe/", "exchangeinfo/")))
    assert small < 512 * 1024, "günlük evren + exchangeInfo küçük (yedeği şişirmez)"
    assert B.verify_archive(e.paths.backup / r["file"])["ok"]
    assert hashlib.sha256((e.paths.backup / r["file"]).read_bytes()).hexdigest() == r["sha256"]

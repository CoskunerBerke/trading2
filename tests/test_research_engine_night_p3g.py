# -*- coding: utf-8 -*-
"""P3g — gece biriminin P3 aşamaları (docs/ENGINE_BRAIN_V1.md §5.2, §5.3, §8.1 P3g satırı): S4x (keşif), S4w
(walk-forward kuyruğu), S6 (deneme defteri zinciri + HWM kopyası, dersler, parite (b), Kapı A gecesi planı),
`MEMORY_GUARD`, config dönemlerinin kurulumu ve gerçek veri sayaçları. Yalnız sentetik veri, ağ yok.

Kabul (§8.1 P3g): büyük sentetik dünyada tam gece pencere içinde (× 3 VPS payıyla); `VmHWM` ölçümü ve §5.3 karar kuralı
(bugünkü 768M birimine uyarlanmış: VmHWM ≤ 0,7 × MemoryMax → birim değişmez); Kapı A gecesi planı; birim sözleşmesi
(`ENGINE_EXPECTED_MEMORY_MAX` = `MemoryMax`, koruma sınırı aynı sayı); atlama kuralları (A/B KAPALI, SKEW, DATA_STALE,
BACKFILL_ACTIVE, yeniden kurulum gecesi, evren farkı); MEMORY_GUARD (sahte RSS); kontrol noktasından sürdürme = tek seferde
kurulum (bayt-aynı); kesilen birim sayacı; haftalık doğrulama farkı.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeHost, FakeVps, run_engine_night  # noqa: E402
from research_engine_p3g_fixtures import COINS40, big_p3_world, config_text, night_after_seal, p3_world  # noqa: E402

from tradingbot.research_engine import epochs_night as EN  # noqa: E402
from tradingbot.research_engine import night as N  # noqa: E402
from tradingbot.research_engine import nightp3 as NP  # noqa: E402
from tradingbot.research_engine import registry as RG  # noqa: E402
from tradingbot.research_engine import rehydrate as RH  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402
from tradingbot.research_engine import summary as SM  # noqa: E402
from tradingbot.research_engine import trials as TR  # noqa: E402
from tradingbot.research_engine import wfqueue as WQ  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
MIB = 1 << 20
MEM_MAX = 805306368
_UNITS = WQ.build_units


def _small(monkeypatch, coin: str = "BTC", tfs=("1d",)):
    """S4w birim planı yalnız bir coin'in birkaç birimi (test süresi); gerçek plan ayrıca sınanır."""
    sub = [u for u in _UNITS(COINS40) if u.coin == coin and u.signal_tf in tfs]
    monkeypatch.setattr(WQ, "build_units", lambda syms, *a, **kw: sub)
    return sub


def _world(tmp_path: Path, *, coins=("BTC", "ETH"), cfg: str | None = None, **kw):
    v = FakeVps(tmp_path / "w")
    host = FakeHost(tmp_path / "host", config_text=config_text() if cfg is None else cfg)
    p3_world(v.paths, v.state, coins=list(coins), **{"fwd_days": 3, "pre_1d_days": 260, "pre_1h_days": 12,
                                                    "pre_5m_days": 1, **kw})
    SC.register_epoch(v.paths, "b" * 40, datetime(2020, 1, 1, tzinfo=UTC), "t", code_hash=SC.engine_code_hash())
    v.fut("strategy_paper_box")
    return v, host


def _st(st: dict, s: str) -> dict:
    return st["stages"].get(s) or {}


# ============================================================================ plan ve paylar (§5.2)
def test_full_plan_runs_forward_stages_first_and_reserves_leave_s6_s7_and_s7b_their_time():
    assert N.PLAN_FULL == ("S0", "S1a", "S3", "S1b", "S2", "S4x", "S4w", "S6", "S7", "S7b") == N.STAGES
    assert N.PLAN_SKEW == ("S0", "S1a", "S7") and N.PLAN_AB_OFF == ("S0", "S1s"), "SKEW / A/B KAPALI planları değişmez"
    assert (N.S1B_RESERVE, N.S2_RESERVE) == (timedelta(minutes=42), timedelta(minutes=12)), "P2b payları değişmez"
    assert N.S4X_BUDGET == timedelta(minutes=10) and N.S6_BUDGET == timedelta(minutes=10)
    assert N.S7_RESERVE == timedelta(minutes=8), "S6 son tarih − 8 dk'da durur (§5.2)"
    assert N.S4W_RESERVE == timedelta(minutes=25), "S4w son tarih − 25 dk'ya kadar (§5.2)"
    assert N.S4W_RESERVE >= N.S6_BUDGET + N.S7_RESERVE and N.S4W_RESERVE_GATE_A >= N.S6_BUDGET_GATE_A + N.S7_RESERVE
    assert N.S6_BUDGET_GATE_A == timedelta(minutes=40) and N.S4W_RESERVE_GATE_A - N.S4W_RESERVE == timedelta(minutes=30)
    # iç son tarih 03:40, sert durma 03:52: S7 + S7b her gece en az S7_RESERVE + 12 dk görür
    assert N.DEADLINE_AFTER == timedelta(hours=2, minutes=3) and N.NIGHT_DEADLINE_HM == (3, 40)


# ============================================================================ tam P3 gecesi
def test_p3_night_runs_s4x_s4w_s6_installs_config_epochs_and_keeps_the_summary_bounded(tmp_path, monkeypatch):
    sub = _small(monkeypatch)
    v, host = _world(tmp_path)
    st = run_engine_night(v, host, night_after_seal(4))
    assert st["result"] == N.R_SUCCESS, {k: (x.get("status"), x.get("reason"), x.get("error")) for k, x in st["stages"].items()}
    assert list(st["plan"]) == list(N.PLAN_FULL) and all(_st(st, s)["status"] == "OK" for s in N.PLAN_FULL)
    x = _st(st, "S4x")["result"]
    assert x["status"]["OK"] > 0 and x["status"].get("NO_TICK", 0) == 0 and x["plan_total"] == 3745
    assert set(x["real_data"]["trade_flags"]) == {"PATH_1H_GAP", "FUNDING_GAP", "F8_UNMEASURABLE", "FUNDING_MARK_MISMATCH"}
    w = _st(st, "S4w")["result"]
    assert w["stop"] == WQ.STOP_DONE and w["units_done"] == len(sub) and w["backlog"]["complete_month"] == "2026-09"
    s6 = _st(st, "S6")["result"]
    assert s6["trials"]["ok"] and s6["trials"]["hwm_copy"]["schema"] == TR.HWM_SCHEMA
    assert s6["lessons"]["status"] in ("OK", "NO_ATTRIBUTION", "S2_PENDING") and s6["gate_a"]["provisional"] is True
    assert st["config_epochs"]["status"] == EN.ST_INSTALLED and st["config_epochs"]["night_epochs"] > 0
    res = v.paths.research
    lib = json.loads((res / "library" / "config_epochs.json").read_text(encoding="utf-8"))
    n_v1 = len(RG.CONFIG_EPOCHS_V1["epochs"])
    v1 = {k: val for k, val in lib.items() if k != "night"} | {"epochs": lib["epochs"][:n_v1]}
    assert RG.sha256_canonical(v1) == RG.CONFIG_EPOCHS_V1_SHA, "kurulan dosyanın V1 kısmı mühürlü dosyayla aynı"
    assert all(e["from"] >= lib["covered_until"] for e in lib["epochs"][n_v1:]), "gece dönemleri mühürden sonra başlar"
    for rel in ("backtests/queue.json", "explore/LIB_v1/_run.json", "library/config_epochs_night.json"):
        assert (res / rel).is_file(), rel
    assert all((res / "backtests" / "cache" / "2026-09" / f"{u.key}.jsonl.gz").is_file() for u in sub)
    digest = (res / "summary" / "digest_tr.md").read_text(encoding="utf-8")
    assert len(digest.encode("utf-8")) <= SM.DIGEST_MAX_BYTES and not SM.claim_word_violations(digest.splitlines())
    assert "## Keşif, walk-forward ve dersler (P3" in digest and "WF_BACKLOG" in digest and "Bellek koruması" in digest
    assert "Keşif (ileri, mühür sonrası; kayıt-yalnız)" in digest, "keşif satırları gecenin p3 kaydından (özet keşfi import etmez)"
    summ = json.loads((res / "summary" / "engine_summary.json").read_text(encoding="utf-8"))
    assert len((res / "summary" / "engine_summary.json").read_bytes()) <= SM.SUMMARY_MAX_BYTES
    assert summ["p3"]["trials_hwm_copy"] == s6["trials"]["hwm_copy"], "HWM kopyası gece özetinde"
    lines = SM.status_lines(v.paths, brief=True, now=night_after_seal(4) + timedelta(hours=2))
    assert len(lines) <= SM.STATUS_MAX_LINES and any(ln.startswith("P3 S4w WF") for ln in lines)
    # ikinci gece: keşif artımlı, kuyruk tam (yalnız 1/7 doğrulama), defter zinciri önceki gecenin HWM kopyasıyla denetlenir
    st2 = run_engine_night(v, host, night_after_seal(4) + timedelta(days=1))
    assert st2["result"] == N.R_SUCCESS and _st(st2, "S6")["result"]["trials"]["vs_summary_copy"] in ("OK", "EMPTY")
    assert _st(st2, "S4w")["result"]["units_done"] == 0 and _st(st2, "S4x")["result"]["status"].get("UNCHANGED", 0) >= 0


def test_post_seal_trade_gets_a_night_epoch_and_the_journal_digest_only_changes_once_installed(tmp_path):
    from tradingbot.research_engine import journal as J
    v = FakeVps(tmp_path / "w")
    paths = v.paths
    paths.ensure_tree()
    late = (datetime.fromtimestamp(RG.T_SEAL_MS / 1000, tz=UTC) + timedelta(days=2)).isoformat()
    assert RH.config_epoch("strategy_paper_box", late, research=paths.research)[1] == RH.EPOCH_SRC_PROVISIONAL
    before = EN.update(paths, config_bytes=b"x: 1\n", config_sha256="a" * 64,
                       now=datetime.fromtimestamp(RG.T_SEAL_MS / 1000, tz=UTC) - timedelta(hours=1))
    assert before["status"] == EN.ST_BEFORE_SEAL and not (paths.research / "library").exists(), "mühürden önce kurulmaz"
    n1 = night_after_seal(1)
    r = EN.update(paths, config_bytes=config_text().encode(), config_sha256="a" * 64, now=n1)
    assert r["status"] == EN.ST_INSTALLED and r["installed_changed"]
    ep, src = RH.config_epoch("strategy_paper_box", late, research=paths.research)
    assert src == RH.EPOCH_SRC_SEALED and ep == "E" + EN.EP._compact(n1.isoformat()), "ilk gece gözlemi: yeni dönem"
    mid = (datetime.fromtimestamp(RG.T_SEAL_MS / 1000, tz=UTC) + timedelta(hours=3)).isoformat()
    assert RH.config_epoch("strategy_paper_box", mid, research=paths.research) == (None, RH.EPOCH_SRC_TRANSITION), \
        "görünüm mühürdekinden farklı: mühür → ilk gece arası TRANSITION (atanmaz)"
    # aynı görünüm: yalnız gözlem ilerler; görünüm değişince önceki dönem son görüldüğü anda kapanır
    n2, n3 = night_after_seal(2), night_after_seal(3)
    assert EN.update(paths, config_bytes=config_text().encode(), config_sha256="a" * 64, now=n2)["opened"] == []
    r3 = EN.update(paths, config_bytes=config_text().encode() + b"strategy_paper_box: {slots: 41}\n",
                   config_sha256="b" * 64, now=n3)
    assert any(o.endswith(":b1_box_fade") for o in r3["opened"]) and len(r3["opened"]) >= 2
    t = (n2 + timedelta(hours=5)).isoformat()
    assert RH.config_epoch("strategy_paper_box", t, research=paths.research) == (None, RH.EPOCH_SRC_TRANSITION)
    # günlük girdi özeti: geçici sınırlarda eski biçim (bayt-aynı), kurulu dosyada dönem ataması özete girer
    loc = J.ArchiveLoc("strategy_paper_box", "k", 0, "ACTIVE", "s", "seg", 1, {"opened_at": late}, None)
    base = J._input_digest(loc, {}, "p", "c", "e", "q")
    assert base == J._input_digest(loc, {}, "p", "c", "e", "q", epoch=None)
    assert base != J._input_digest(loc, {}, "p", "c", "e", "q", epoch=[ep, src])


# ============================================================================ atlama kuralları (§5.2)
def test_ab_off_and_skew_nights_do_not_plan_p3_stages(tmp_path):
    v = FakeVps(tmp_path / "a")
    host = FakeHost(tmp_path / "ha", config_text=config_text())
    day = night_after_seal(3)
    while SC.ab_parity_on(day.strftime("%Y-%m-%d")):
        day += timedelta(days=1)
    SC.register_epoch(v.paths, "b" * 40, day - timedelta(days=2), "t", code_hash=SC.engine_code_hash())
    st = run_engine_night(v, host, day)
    assert st["result"] == N.R_AB_OFF and all(_st(st, s)["status"] == N.ST_NOT_PLANNED for s in N.P3_STAGES)
    v2 = FakeVps(tmp_path / "s")
    sk = run_engine_night(v2, FakeHost(tmp_path / "hs", git_rc=1, config_text=config_text()), night_after_seal(3))
    assert sk["result"] == N.R_SKEW and all(_st(sk, s)["status"] == N.ST_NOT_PLANNED for s in N.P3_STAGES)
    assert not (v2.paths.research / "library").exists(), "SKEW gecesi config dönemlerini kurmaz"


def test_data_stale_skips_s4x_and_s4w_but_s6_still_checks_the_ledger(tmp_path, monkeypatch):
    _small(monkeypatch)
    v, host = _world(tmp_path)
    st = run_engine_night(v, host, night_after_seal(4) + timedelta(days=3))       # mühür 3 gün eski → DATA_STALE
    assert SC.DATA_STALE in st["flags"]
    for s in ("S4x", "S4w"):
        assert _st(st, s)["status"] == N.ST_SKIPPED and _st(st, s)["reason"].startswith("DATA_STALE"), _st(st, s)
    assert _st(st, "S6")["status"] == "OK" and _st(st, "S6")["result"]["trials"]["ok"]
    assert not (v.paths.research / "explore").exists() and not (v.paths.research / "backtests").exists()


def test_backfill_active_skips_only_s4w(tmp_path, monkeypatch):
    _small(monkeypatch)
    v, host = _world(tmp_path)
    ds = json.loads(v.paths.data_status.read_text(encoding="utf-8"))
    ds["running"] = {"mode": "backfill", "pid": os.getpid(), "started_at": "2026-10-16T08:00:00+00:00"}
    v.paths.write_json(v.paths.data_status, ds)
    st = run_engine_night(v, host, night_after_seal(4))
    assert _st(st, "S4x")["status"] == "OK"
    assert _st(st, "S4w")["status"] == N.ST_SKIPPED and _st(st, "S4w")["reason"].startswith("BACKFILL_ACTIVE")
    assert NP.F_BACKFILL_ACTIVE in st["flags"] and st["result"] == N.R_SUCCESS
    assert NP.backfill_active(v.paths)[0]
    ds["running"]["pid"] = 2 ** 22 + 12345                                         # ölü süreç: doldurma sürmüyor
    v.paths.write_json(v.paths.data_status, ds)
    assert NP.backfill_active(v.paths) == (False, None)
    ds["running"] = {"mode": "update", "pid": os.getpid()}
    v.paths.write_json(v.paths.data_status, ds)
    assert NP.backfill_active(v.paths) == (False, None), "00:41 güncellemesi doldurma değildir"


def test_rebuild_night_and_entry_universe_diff_skip_with_a_reason(tmp_path, monkeypatch):
    assert NP.is_rebuild_night({"journal": {"rows": 1000, "built_rows": 300, "pending": 0}})[0]
    assert NP.is_rebuild_night({"journal": {"rows": 1000, "built_rows": 10, "pending": 3}})[0]
    assert not NP.is_rebuild_night({"journal": {"rows": 1000, "built_rows": 199, "pending": 0}})[0]
    assert not NP.is_rebuild_night({"journal": {"rows": 100, "built_rows": 100, "pending": 0}})[0], "küçük günlük: eşik 200"
    _small(monkeypatch)
    v, host = _world(tmp_path)
    orig = N._Night._s1b

    def heavy(self, now):
        r = orig(self, now)
        r["journal"] = {**(r.get("journal") or {}), "rows": 5000, "built_rows": 4000, "pending": 0}
        return r
    monkeypatch.setattr(N._Night, "_s1b", heavy)
    st = run_engine_night(v, host, night_after_seal(4))
    assert _st(st, "S4x")["status"] == "OK" and _st(st, "S4w")["status"] == N.ST_SKIPPED
    assert _st(st, "S4w")["reason"].startswith("S1B_REBUILD") and NP.F_MEMORY_GUARD not in st["flags"]
    monkeypatch.undo()
    v2, host2 = _world(tmp_path / "u", cfg=config_text(COINS40[:39]))
    st2 = run_engine_night(v2, host2, night_after_seal(4))
    for s in ("S4x", "S4w"):
        assert _st(st2, s)["status"] == N.ST_SKIPPED and _st(st2, s)["reason"].startswith("ENTRY_UNIVERSE_DIFF")
    assert NP.F_ENTRY_UNIVERSE_DIFF in st2["flags"] and st2["p3"]["entry_universe"]["missing"] == [COINS40[39]]


def test_no_budget_left_after_s1b_s2_is_a_p3_backlog_not_a_deadline_and_s6_s7_s7b_still_run(tmp_path, monkeypatch):
    _small(monkeypatch)
    v, host = _world(tmp_path)
    monkeypatch.setattr(N, "S4W_RESERVE", N.DEADLINE_AFTER)                      # S4x/S4w'ye pay kalmadı
    monkeypatch.setattr(N, "S4W_RESERVE_GATE_A", N.DEADLINE_AFTER)               # (gece ayın 17'si: Kapı A bakış gecesi)
    st = run_engine_night(v, host, night_after_seal(4))
    for s in ("S4x", "S4w"):
        assert _st(st, s)["status"] == N.ST_SKIPPED and _st(st, s)["reason"].startswith("NO_BUDGET"), _st(st, s)
    assert {NP.F_S4X_BACKLOG, NP.F_WF_BACKLOG} <= set(st["flags"]) and N.F_DEADLINE not in st["flags"]
    assert st["result"] == N.R_SUCCESS and all(_st(st, s)["status"] == "OK" for s in ("S6", "S7", "S7b"))


# ============================================================================ MEMORY_GUARD (§5.3)
def test_memory_guard_uses_cgroup_then_expected_env_and_trips_above_seventy_percent():
    g = NP.MemoryGuard.for_night(None, {"ENGINE_EXPECTED_MEMORY_MAX": str(MEM_MAX)}, rss=lambda: 500 * MIB)
    assert (g.limit, g.source, g.threshold) == (MEM_MAX, "ENGINE_EXPECTED_MEMORY_MAX", int(0.7 * MEM_MAX))
    assert g.check("a")["ok"] and 500 * MIB < 0.7 * MEM_MAX < 540 * MIB
    g2 = NP.MemoryGuard.for_night(1 << 30, {"ENGINE_EXPECTED_MEMORY_MAX": str(MEM_MAX)}, rss=lambda: 540 * MIB)
    assert g2.limit == 1 << 30 and g2.check("b")["ok"], "cgroup memory.max önce gelir"
    g3 = NP.MemoryGuard.for_night(None, {"ENGINE_EXPECTED_MEMORY_MAX": str(MEM_MAX)}, rss=lambda: 540 * MIB)
    r = g3.check("S4w")
    assert not r["ok"] and r["ratio"] > 0.7 and g3.summary()["trips"] == ["S4w"]
    assert NP.MemoryGuard.for_night(None, {}, rss=lambda: 10 ** 12).check("x")["ok"], "sınır yoksa koruma pasif (ölçülür)"
    assert NP.proc_status_bytes("VmRSS") and NP.proc_status_bytes("VmHWM") >= NP.proc_status_bytes("VmRSS")


def test_memory_guard_stops_s4w_with_a_checkpoint_and_s6_lessons_never_s1a_s3_s7b(tmp_path, monkeypatch):
    sub = _small(monkeypatch)
    v, host = _world(tmp_path)
    st = run_engine_night(v, host, night_after_seal(4), memory_limit=MEM_MAX, rss_probe=lambda: 600 * MIB)
    assert NP.F_MEMORY_GUARD in st["flags"] and st["result"] == N.R_SUCCESS
    w = _st(st, "S4w")["result"]
    assert w["stop"] == WQ.STOP_GUARD and w["units_done"] == 0
    q = WQ.read_queue(WQ.root_of(v.paths))
    assert q["building"]["month"] == "2026-09" and q["building"]["units"] == {}, "kontrol noktası yazıldı (kurulum başladı)"
    assert _st(st, "S6")["result"]["lessons"] == {"status": "SKIPPED", "reason": "MEMORY_GUARD"}
    assert all(_st(st, s)["status"] == "OK" for s in ("S1a", "S3", "S7", "S7b")), "S1a/S3/S7b korunmaz"
    assert st["p3"]["guard"]["trips"][:1] == ["S4w"] and st["p3"]["guard"]["limit_bytes"] == MEM_MAX
    st2 = run_engine_night(v, host, night_after_seal(5), memory_limit=MEM_MAX, rss_probe=lambda: 200 * MIB)
    assert _st(st2, "S4w")["result"]["units_done"] == len(sub) and NP.F_MEMORY_GUARD not in st2["flags"]


# ============================================================================ S4w: sürdürme = tek sefer (bayt-aynı)
def _queue_run(paths, reader, units, *, now_ms, deadline, step=10.0, **kw):
    t = {"n": 0.0}
    orig = WQ.run_unit

    def run_unit(*a, **k):
        t["n"] += step
        return orig(*a, **k)
    WQ.run_unit = run_unit
    try:
        return WQ.run_s4w(paths, reader, entry_symbols=COINS40, ticks={"BTC": 1e-4, "ETH": 1e-4}, now_ms=now_ms,
                          deadline=deadline, clock=lambda: t["n"], units=units, **kw)
    finally:
        WQ.run_unit = orig


def _cache(paths, month: str) -> dict:
    d = WQ.root_of(paths) / "cache" / month
    return {p.name: gzip.decompress(p.read_bytes()) for p in sorted(d.glob("*.jsonl.gz"))}


def test_s4w_resumes_from_its_checkpoint_byte_identical_to_one_shot_and_counts_killed_units(tmp_path):
    from tradingbot.research_engine.paths import EnginePaths
    from tradingbot.research_engine.provider import SealedReader
    pa, pb = EnginePaths(data=tmp_path / "a", state=tmp_path / "sa"), EnginePaths(data=tmp_path / "b", state=tmp_path / "sb")
    for p in (pa, pb):
        p.ensure_tree()
        p3_world(p, p.state, coins=["BTC", "ETH"], fwd_days=3, pre_1d_days=260, pre_1h_days=12, pre_5m_days=1)
    units = [u for u in _UNITS(COINS40) if u.coin in ("BTC", "ETH") and u.signal_tf in ("1d", "4h")][:8]
    now = int(night_after_seal(4).timestamp() * 1000)
    one = _queue_run(pb, SealedReader.from_status(pb), units, now_ms=now, deadline=1e9)
    assert one["stop"] == WQ.STOP_DONE and one["backlog"] == {"complete_month": "2026-09", "building_month": None,
                                                              "done": 8, "total": 8}
    ra = SealedReader.from_status(pa)
    r1 = _queue_run(pa, ra, units, now_ms=now, deadline=25.0)
    assert r1["stop"] in (WQ.STOP_DEADLINE, WQ.STOP_ESTIMATE) and 0 < r1["backlog"]["done"] < 8
    q = WQ.read_queue(WQ.root_of(pa))
    assert len(q["building"]["units"]) == r1["backlog"]["done"] and q["in_progress"] is None
    r2 = _queue_run(pa, ra, units, now_ms=now, deadline=1e9)
    assert r2["stop"] == WQ.STOP_DONE and r2["backlog"]["complete_month"] == "2026-09"
    assert _cache(pa, "2026-09") == _cache(pb, "2026-09"), "kaldığı yerden = tek seferde (bayt-aynı)"
    qa, qb = WQ.read_queue(WQ.root_of(pa)), WQ.read_queue(WQ.root_of(pb))
    assert {k: x["sha256"] for k, x in qa["complete"]["units"].items()} == \
        {k: x["sha256"] for k, x in qb["complete"]["units"].items()}
    # bir birimin ORTASINDA ölen gece (OOM/sert durma): sayaç; MAX_KILLS'te birim KILLED_SKIPPED olur (kuyruk tıkanmaz)
    pc = EnginePaths(data=tmp_path / "c", state=tmp_path / "sc")
    pc.ensure_tree()
    p3_world(pc, pc.state, coins=["BTC"], fwd_days=3, pre_1d_days=260, pre_1h_days=12, pre_5m_days=1)
    rc_ = SealedReader.from_status(pc)
    two = units[:2]
    _queue_run(pc, rc_, two, now_ms=now, deadline=5.0)                    # kurulum başladı, birim yok/biri bitti
    for k in range(WQ.MAX_KILLS):
        q = WQ.read_queue(WQ.root_of(pc))
        left = [u.key for u in two if u.key not in q["building"]["units"]]
        q["in_progress"] = {"month": q["building"]["month"], "unit": left[0]}
        pc.write_json(WQ.root_of(pc) / "queue.json", q)
        r = WQ.run_s4w(pc, rc_, entry_symbols=COINS40, ticks={"BTC": 1e-4}, now_ms=now, deadline=-1.0, units=two)
        assert r["previous_night_killed_in"] == left[0]
    q = WQ.read_queue(WQ.root_of(pc))
    b = q["building"] or q["complete"]                                   # atlanan birim sayılır: kurulum tamamlanabilir
    assert b["units"][left[0]]["status"] == WQ.ST_KILLED and b["kills"][left[0]] == WQ.MAX_KILLS


def test_a_unit_longer_than_the_night_or_over_the_memory_guard_is_dropped_mid_unit_counted_and_then_skipped(
        tmp_path, monkeypatch):
    """Birim ortası bekçi (P3g ölçümü: gerçek uzunlukta 5m Box birimi tek başına bir gecenin S4w payı mertebesinde):
    son tarih ya da bellek koruması birimin ORTASINDA aşılınca birim bırakılır (S4w durur, S6/S7'nin payı korunur),
    kontrol noktası yazılır; MAX_ABORTS kezde birim atlanır ve kuyruk sürer. `SIGALRM` işleyicisi ve zamanlayıcı geri
    alınır.

    Belirlenimci: son tarih, birim süresi ve `ABORT_MIN_FRACTION` sayımı SAHTE monotonic saatle (`run_s4w(clock=…)`)
    ölçülür — yalnız sonsuz birim saati ilerletir (kuyruk okuma/yazma, geçmiş yükleme, makine yükü sayılmaz). Gerçek zamana
    bağlı tek şey bekçinin kendisidir: sonsuz birim gerçek zamanda döner ve yalnız `SIGALRM` işleyicisinin attığı
    `UnitAborted` onu durdurabilir (bekçi atmazsa birim 20 sn sonra hata verir → `aborted` yok, test düşer)."""
    import signal
    import time
    from tradingbot.research_engine.paths import EnginePaths
    from tradingbot.research_engine.provider import SealedReader
    monkeypatch.setattr(WQ, "WATCH_EVERY_S", 0.05)
    p = EnginePaths(data=tmp_path / "a", state=tmp_path / "s")
    p.ensure_tree()
    p3_world(p, p.state, coins=["BTC"], fwd_days=3, pre_1d_days=260, pre_1h_days=12, pre_5m_days=1)
    rd = SealedReader.from_status(p)
    units = [u for u in _UNITS(COINS40) if u.coin == "BTC" and u.signal_tf == "1d"][:3]
    slow = {units[0].key}
    orig = WQ.run_unit
    spins = {"n": 0}
    clk = {"t": 1000.0, "step": 0.0}

    def clock() -> float:
        return clk["t"]

    def run_unit(u, *a, **k):
        if u.key in slow:                                    # sonsuz birim: sahte saati payın ötesine taşır, sonra döner
            spins["n"] += 1
            clk["t"] += clk["step"]
            t0 = time.monotonic()
            while time.monotonic() - t0 < 20:                # gerçek zamanda yalnız bekçi (SIGALRM) durdurabilir
                pass
            raise AssertionError("bekçi birimi bırakmadı")
        return orig(u, *a, **k)
    monkeypatch.setattr(WQ, "run_unit", run_unit)
    now = int(night_after_seal(4).timestamp() * 1000)
    before = signal.getsignal(signal.SIGALRM)
    budget = 0.4

    def night(budget_s: float, **kw):
        t = clock()
        r = WQ.run_s4w(p, rd, entry_symbols=COINS40, ticks={"BTC": 1e-4}, now_ms=now, deadline=clock() + budget_s,
                       clock=clock, units=units, night_budget_s=budget, **kw)
        assert signal.getsignal(signal.SIGALRM) == before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        return r, clock() - t
    clk["step"] = budget + 0.1                               # birim gecenin payını aşar (≥ ABORT_MIN_FRACTION × pay)
    for k in range(1, WQ.MAX_ABORTS + 1):
        r, took = night(budget)
        assert r["stop"] == WQ.STOP_DEADLINE and r["aborted"]["unit"] == units[0].key and r["aborted"]["why"] == "DEADLINE"
        assert r["aborted"]["after_s"] == round(budget + 0.1, 1) and took == clk["step"], \
            "birim son tarihte, ORTASINDA bırakıldı (gece sürmedi: sahte saat yalnız birimin adımı kadar ilerledi)"
        q = WQ.read_queue(WQ.root_of(p))
        assert q["in_progress"] is None and q["building"]["aborts"][units[0].key] == k, "kontrol noktası yazıldı, sayıldı"
    assert q["building"]["units"][units[0].key]["status"] == WQ.ST_TOO_LONG and r["units_aborted"] == 1
    r, _ = night(600)
    assert r["stop"] == WQ.STOP_DONE and r["backlog"]["complete_month"] == "2026-09" and r["backlog"]["skipped"] == 1
    assert spins["n"] == WQ.MAX_ABORTS, "atlanan birim bir daha koşmaz"
    # bellek koruması birim ORTASINDA: ilk denetim (birimden önce) geçer, birim içindeki denetim aşar → bırakılır, sayılır
    # (son tarih uzak: sahte saat birimde en çok 1 sn ilerler; durduran yalnız korumadır — bekçinin ilk atışı birimin
    # veri yüklemesine ya da sonsuz döngüsüne denk gelebilir, ikisi de birimin içidir)
    p2 = EnginePaths(data=tmp_path / "b", state=tmp_path / "s2")
    p2.ensure_tree()
    p3_world(p2, p2.state, coins=["BTC"], fwd_days=3, pre_1d_days=260, pre_1h_days=12, pre_5m_days=1)
    rd2 = SealedReader.from_status(p2)
    monkeypatch.setattr(WQ, "GUARD_EVERY_S", 0.0)
    calls = []

    def guard():
        calls.append(1)
        return {"ok": len(calls) == 1, "rss_bytes": 600 * MIB}
    clk["step"] = 1.0
    t = clock()
    r = WQ.run_s4w(p2, rd2, entry_symbols=COINS40, ticks={"BTC": 1e-4}, now_ms=now, deadline=clock() + 600, clock=clock,
                   units=units, night_budget_s=3600.0, guard=guard)
    assert clock() - t <= 1.0 and r["stop"] == WQ.STOP_GUARD and r["aborted"]["why"] == WQ.STOP_GUARD
    assert r["aborted"]["unit"] == units[0].key and r["guard_aborts"] == [units[0].key] and len(calls) == 3, \
        "birimden önce bir (geçer), birim ORTASINDA bir (aşar; birim bırakılır, kuyruk sürer), sonraki birimden önce bir " \
        "(aşar → S4w durur; sonraki birime sayılmaz)"
    q = WQ.read_queue(WQ.root_of(p2))
    assert q["building"]["aborts"] == {units[0].key: 1} and q["in_progress"] is None and units[0].key not in q["building"]["units"]
    assert signal.getsignal(signal.SIGALRM) == before


def test_watchdog_fires_again_when_its_first_abort_lands_in_a_finalizer_and_is_swallowed(monkeypatch):
    """Yük altında bekçinin ilk atışı `gc.collect()`'in çağırdığı bir sonlandırıcıya (`GzipFile.__del__`) düştü: Python
    oradaki istisnayı yutar. Bekçi neden sürdükçe sonraki atışta yeniden atmalı (eskiden tek atışlıydı → birim 20 sn
    sürdü). Sonlandırıcının içinde dönen nesne bunu belirlenimci kurar; gövde bitince ve çıkışta bir daha atmaz."""
    import signal
    import time
    swallowed: list[type] = []
    monkeypatch.setattr(sys, "unraisablehook", lambda u: swallowed.append(u.exc_type))
    checks: list[int] = []

    class _Fin:
        def __del__(self):                                   # ilk atış burada: istisna yutulur
            while not checks:
                pass
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10:
                pass

    w = WQ._Watch(lambda: checks.append(1) or "DEADLINE", 0.02)
    before = signal.getsignal(signal.SIGALRM)
    t0 = time.monotonic()
    try:
        with w:
            _Fin()                                           # başvuru sayısı 0 → `__del__` burada, eşzamanlı
            while time.monotonic() - t0 < 10:                # gövde: yalnız ikinci atış durdurabilir
                pass
        raise AssertionError("bekçi yutulan atıştan sonra bir daha atmadı")
    except WQ.UnitAborted as ab:
        assert ab.why == "DEADLINE"
    assert swallowed == [WQ.UnitAborted], "ilk atış sonlandırıcıda yutuldu"
    assert w.fired >= 2 and checks == [1], "neden bir kez denetlenir, yeniden atışlar aynı nedenle"
    assert time.monotonic() - t0 < 10
    assert signal.getsignal(signal.SIGALRM) == before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    # gövde normal biterse (done) ve çıkıştan sonra hiç atmaz
    w2 = WQ._Watch(lambda: "DEADLINE", 0.02)
    with w2:
        w2.done()
        t1 = time.monotonic()
        while time.monotonic() - t1 < 0.1:
            pass
    assert w2.fired == 0


def test_weekly_one_seventh_verification_recomputes_and_reports_a_cache_mismatch(tmp_path):
    from tradingbot.research_engine.paths import EnginePaths
    from tradingbot.research_engine.provider import SealedReader
    p = EnginePaths(data=tmp_path / "a", state=tmp_path / "s")
    p.ensure_tree()
    p3_world(p, p.state, coins=["BTC"], fwd_days=3, pre_1d_days=260, pre_1h_days=12, pre_5m_days=1)
    units = [u for u in _UNITS(COINS40) if u.coin == "BTC" and u.signal_tf in ("1d", "4h")]
    rd = SealedReader.from_status(p)
    now = int(night_after_seal(4).timestamp() * 1000)
    _queue_run(p, rd, units, now_ms=now, deadline=1e9)
    q = WQ.read_queue(WQ.root_of(p))
    day0 = now // WQ.DAY_MS
    day = next(d for d in range(day0 + 1, day0 + 7) if any(WQ._bucket(u.key) == d % WQ.VERIFY_ROTATION for u in units))
    now = day * WQ.DAY_MS + 6_000_000                                      # aynı hedef ay (2026-09), başka doğrulama kovası
    assert WQ.target_month(now)[0] == q["complete"]["month"]
    k = next(u.key for u in units if WQ._bucket(u.key) == day % WQ.VERIFY_ROTATION)
    good = q["complete"]["units"][k]["sha256"]
    q["complete"]["units"][k]["sha256"] = "0" * 64
    q["verify"] = {}
    p.write_json(WQ.root_of(p) / "queue.json", q)
    r = _queue_run(p, rd, units, now_ms=now, deadline=1e9)
    assert r["verified"] >= 1 and r["mismatch"] == 1 and r["units_done"] == 0
    assert WQ.read_queue(WQ.root_of(p))["complete"]["units"][k]["sha256"] == good, "yeniden hesap kaydı düzeltir"
    r2 = _queue_run(p, rd, units, now_ms=now, deadline=1e9)
    assert r2["verified"] == 0, "aynı gün yeniden doğrulanmaz"


def test_unit_plan_covers_every_runnable_series_once_and_puts_5m_last_per_coin():
    from tradingbot.research_engine import explore as EX
    units = WQ.build_units(COINS40)
    runnable = set(EX.runnable_variants())
    per = {}
    for u in units:
        for vid in u.variants:
            per[(vid, u.coin)] = per.get((vid, u.coin), 0) + 1
    assert set(per) == {(v, c) for v in runnable for c in COINS40} and set(per.values()) == {1}
    assert len(units) == 17 * 40 and len({u.key for u in units}) == len(units)
    for c in COINS40:
        tfs = [u.signal_tf for u in units if u.coin == c]
        assert tfs == sorted(tfs, key=lambda t: WQ.TF_ORDER[t]), "5m birimleri coin'in sonunda"
    assert WQ.target_month(int(datetime(2026, 11, 3, 1, 40, tzinfo=UTC).timestamp() * 1000))[0] == "2026-09", "arşiv payı"
    assert WQ.target_month(int(datetime(2026, 11, 4, 1, 40, tzinfo=UTC).timestamp() * 1000))[0] == "2026-10"


# ============================================================================ Kapı A gecesi planı (§5.2, §3.7)
def test_gate_a_look_night_gives_s6_forty_minutes_shrinks_s4w_once_per_month_and_stays_provisional(tmp_path, monkeypatch):
    _small(monkeypatch)
    v, host = _world(tmp_path, fwd_days=6)
    look = night_after_seal(7)                                            # ayın 20'si: o ay için bakış yok
    assert look.day > 5 and NP.gate_a_night(v.paths, look.date())
    st = run_engine_night(v, host, look)
    p3 = st["p3"]
    assert p3["gate_a_night"] and p3["s4w_reserve_min"] == 55 and p3["s6_budget_min"] == 40
    assert _st(st, "S4w")["result"]["budget_s"] == (timedelta(hours=2) - N.S4W_RESERVE_GATE_A).total_seconds()
    ga = _st(st, "S6")["result"]["gate_a"]
    assert ga["status"] == "HESAPLANMADI" and ga["provisional"] and NP.F_GATE_A_NOT_COMPUTED in st["flags"]
    assert not [r for r in TR.iter_rows(v.paths.trials_file) if r.get("kind") in ("LOOK_A", "PROMOTE", "BIRTH")], \
        "bakış hükmü/terfi yazılmaz (S2 mühürlü değil; P4)"
    assert not (v.paths.research / "promotions").exists() and not list(v.paths.research.rglob("*auto_promo*")), \
        "terfi manifesti yok (P4)"
    nxt = run_engine_night(v, host, look + timedelta(days=1))
    assert not nxt["p3"]["gate_a_night"] and nxt["p3"]["s4w_reserve_min"] == 25, "ayın bakışı tüketildi"
    assert _st(nxt, "S4w")["result"]["budget_s"] == (timedelta(hours=2) - N.S4W_RESERVE).total_seconds()
    assert not NP.gate_a_night(v.paths, date(2026, 11, 5)) and NP.gate_a_night(v.paths, date(2026, 11, 6))


# ============================================================================ deneme defteri: zincir + HWM kopyası
def test_s6_detects_a_ledger_restored_together_with_its_hwm_from_the_summary_copy(tmp_path, monkeypatch):
    _small(monkeypatch)
    v, host = _world(tmp_path)
    led = TR.TrialsLedger(v.paths)
    rows = list(RG.PRIOR_TRIALS_V1["rows"])
    led.import_priors({"rows": rows[:3]}, at="2026-10-14T00:00:00+00:00")
    old = (v.paths.trials_file.read_bytes(), v.paths.trials_hwm_file.read_bytes())
    led.import_priors({"rows": rows[3:6]}, at="2026-10-15T00:00:00+00:00")
    st = run_engine_night(v, host, night_after_seal(4))
    tr = _st(st, "S6")["result"]["trials"]
    assert tr["ok"] and tr["hwm_copy"]["rows"] == tr["rows"] > 0
    v.paths.trials_file.write_bytes(old[0])                               # defter + HWM birlikte eski yedekten
    v.paths.trials_hwm_file.write_bytes(old[1])
    assert TR.verify_ledger(v.paths)["ok"], "yan dosya tek başına geri sarmayı göremez"
    st2 = run_engine_night(v, host, night_after_seal(5))
    tr2 = _st(st2, "S6")["result"]
    assert tr2["trials"]["code"] == "ROLLED_BACK_VS_SUMMARY" and NP.F_TRIALS_CHAIN_BROKEN in st2["flags"]
    assert tr2["lessons"]["status"] == "CLOSED" and tr2["gate_a"]["status"] == "CLOSED"


# ============================================================================ birim sözleşmesi (§5.3 kararı)
def _unit(name: str) -> dict:
    out: dict = {}
    for ln in (ROOT / "deploy" / name).read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#") and "=" in ln and not ln.startswith("["):
            k, val = ln.split("=", 1)
            out.setdefault(k, []).append(val)
    return out


def test_night_unit_contract_expected_memory_max_equals_memory_max_and_the_guard_reads_it():
    u = _unit("tradingbot-engine-night.service")
    mm = u["MemoryMax"][0]
    assert mm == "768M" and u["MemoryHigh"] == ["640M"], "§5.3 kararı (P3g): birim DEĞİŞMEZ (bugünkü 640M/768M)"
    want = int(mm[:-1]) << 20
    env = dict(e.split("=", 1) for e in u["Environment"])
    assert int(env["ENGINE_EXPECTED_MEMORY_MAX"]) == want == MEM_MAX
    g = NP.MemoryGuard.for_night(None, env, rss=lambda: 0)
    assert g.limit == want and g.threshold == int(0.7 * want), "koruma sınırı = MemoryMax (cgroup okunamazsa ortamdan)"
    assert u["OOMScoreAdjust"] == ["1000"] and u["PrivateNetwork"] == ["yes"]


# ============================================================================ gerçek veri sayaçları
def test_funding_interval_counter_and_trade_flag_counters():
    from tradingbot.research_engine import explore as EX
    H = 3_600_000
    t0 = RG.T_SEAL_MS
    rows = [(t0 + k * 8 * H, 1e-4, 1.0) for k in range(5)] + [(t0 + 32 * H + k * 4 * H, 1e-4, 1.0) for k in range(1, 4)]
    fi = EX.funding_intervals(rows)
    assert fi == {"intervals_h": [4, 8], "changes": 1, "settlements": 8}
    assert EX.funding_intervals([(t0 - 8 * H, 0.0, 1.0), (t0, 0.0, 1.0)])["settlements"] == 1, "yalnız mühürden sonrası"
    assert EX.funding_intervals(None) == {"intervals_h": [], "changes": 0, "settlements": 0}


def test_parity_b_night_wiring_is_resumable_and_reports_the_formasyon_data_source_key(tmp_path, monkeypatch):
    _small(monkeypatch)
    v, host = _world(tmp_path)
    st = run_engine_night(v, host, night_after_seal(4))
    pb = _st(st, "S6")["result"]["parity_b"]
    from tradingbot.research_engine import parity as PA
    assert set(pb["verdicts"]) == set(PA.BOOK_DIR) and len(PA.BOOK_DIR) == 13
    assert pb["formasyon_bars_4h"]["signal_tf"] == "4h" and "with_key" in pb["formasyon_bars_4h"]
    d = NP.parity_dir(v.paths)
    assert len(list(d.glob("K_*.json"))) == 13
    from tradingbot.research_engine.provider import SealedReader
    again = NP.run_parity_b(v.paths, SealedReader.from_status(v.paths), now_ms=int(night_after_seal(4).timestamp() * 1000),
                            deadline_mono=1e18)
    assert sorted(again["unchanged"]) == sorted(pb["verdicts"]) and again["computed"] == [], "değişmeyen girdi yeniden yok"
    stop = NP.run_parity_b(v.paths, SealedReader.from_status(v.paths), now_ms=0, deadline_mono=-1.0)
    assert stop["stop"] == "DEADLINE" and stop["computed"] == []


# ============================================================================ büyük sentetik dünya (kabul)
def _measure():
    spec = importlib.util.spec_from_file_location("engine_p3g_measure", ROOT / "scripts" / "engine_p3g_measure.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_big_synthetic_world_night_fits_the_window_and_vmhwm_keeps_the_768m_unit(tmp_path, capsys):
    """P2b dünyası + 40 coin'lik P3 yükü; gece AYRI süreçte (VmHWM yalnız onun). Kabul: SUCCESS, S7/S7b OK; × 3 VPS payıyla
    S4w dışı aşamalar S4w durduktan sonra kalan ≥ 25 dk'ya ve S4x 10 dk'ya sığar; VmHWM ≤ 0,7 × 768 MiB (MEMORY_GUARD
    eşiği; §5.3 kuralının bugünkü 768M birimine uyarlanması → birim değişmez). Ölçümler yazdırılır (§8.4 P3g notu)."""
    m = _measure()
    v = FakeVps(tmp_path / "w")
    big_p3_world(v, v.state, n_trades=120, days=10, history_days=30, fwd_days=3, pre_1d_days=260, pre_1h_days=20,
                 pre_5m_days=2)
    SC.register_epoch(v.paths, "b" * 40, datetime(2020, 1, 1, tzinfo=UTC), "t", code_hash=SC.engine_code_hash())
    v.save(night_after_seal(4) - timedelta(minutes=1))
    d = m._child(m.CHILD, {"P3G_ROOT": str(ROOT), "P3G_DIR": str(tmp_path), "P3G_MEM": str(MEM_MAX), "P3G_DAY": "4",
                           "P3G_S4W_MAX_S": "120"})
    sec = {s: (x.get("duration_s") or 0.0) for s, x in d["stages"].items()}
    hwm = d["resources"]["vm_hwm_bytes"]
    with capsys.disabled():
        print(f"\nP3g büyük dünya: {d['result']} · duvar {d['resources']['wall_s']} sn · VmHWM {hwm / MIB:.0f} MiB · "
              + " · ".join(f"{s} {x:.1f}s" for s, x in sec.items() if x))
    assert d["result"] == N.R_SUCCESS and all(d["stages"][s]["status"] == "OK" for s in ("S6", "S7", "S7b")), d["stages"]
    assert d["stages"]["S1b"]["status"] == "OK" and d["stages"]["S2"]["status"] == "OK"
    assert d["s4w"]["units_done"] > 0 and d["s4x"]["status"]["OK"] > 1000
    assert 3 * (sec["S6"] + sec["S7"] + sec["S7b"]) <= N.S4W_RESERVE.total_seconds(), "S4w durunca kalanlar sığar"
    assert sec["S4x"] <= 120 + 60, "S4x payla sınırlı (bu testte 120 sn; gecede ≤ 10 dk)"
    early = sum(sec[s] for s in ("S0", "S1a", "S3", "S1b", "S2"))
    assert 3 * early < (N.DEADLINE_AFTER - N.S4W_RESERVE - N.S4X_BUDGET).total_seconds()
    assert hwm and hwm <= 0.7 * MEM_MAX, f"VmHWM {hwm / MIB:.0f} MiB > 0,7 × 768 MiB → §5.3: birim kararı yeniden"
    assert d["guard"]["limit_bytes"] == MEM_MAX and d["guard"]["trips"] == []


def test_night_plan_has_p3_stages_after_s2_and_before_s7_in_the_status_order():
    st = {"stages": {s: {"status": "OK", "duration_s": 1.0} for s in N.PLAN_FULL}, "plan": list(N.PLAN_FULL)}
    line = SM._stage_line(st)
    assert line.index("S2 OK") < line.index("S4x OK") < line.index("S4w OK") < line.index("S6 OK") < line.index("S7 OK")
    assert SC.ENV_EXPECTED_MEMORY_MAX == NP.ENV_EXPECTED_MEMORY_MAX


# ============================================================================ P3g inceleme: uzun geçmişli coin, bellek
LONG = r"""
import json, os, sys, time
sys.path.insert(0, os.environ["P3G_ROOT"]); sys.path.insert(0, os.path.join(os.environ["P3G_ROOT"], "tests"))
from pathlib import Path
from research_engine_p3g_fixtures import COINS40, LongHistoryReader
from tradingbot.research_engine import lib_ag as LA, nightp3 as NP, wfqueue as WQ
from tradingbot.research_engine.paths import EnginePaths
d = Path(os.environ["P3G_DIR"])
p = EnginePaths(data=d / "data", state=d / "data" / "state")
p.ensure_tree()
now = int(os.environ["P3G_NOW"])
month, end = WQ.target_month(now)
rd = LongHistoryReader("BTC", end_ms=end, years=float(os.environ["P3G_YEARS"]))
by = {u.key: u for u in WQ.build_units(COINS40, LA.universe_symbols())}
units = [by[k] for k in os.environ["P3G_UNITS"].split(",")]
g = NP.MemoryGuard(int(os.environ["P3G_MEM"]), source="test")
nights = []
for _n in range(2):
    t = time.monotonic()
    r = WQ.run_s4w(p, rd, entry_symbols=COINS40, ticks={"BTC": 0.1}, now_ms=now, deadline=time.monotonic() + 3600,
                   guard=lambda: g.check("S4w"), night_budget_s=4200.0, units=units, rss=g.rss_now, rss_limit=g.threshold)
    nights.append({k: r.get(k) for k in ("stop", "units_done", "units_aborted", "guard_refused", "guard_aborts", "aborted",
                                         "backlog", "errors")} | {"seconds": round(time.monotonic() - t, 1)})
q = WQ.read_queue(WQ.root_of(p))
b = q.get("building") or q.get("complete") or {}
print("@@" + json.dumps({"nights": nights, "units": b.get("units"), "aborts": b.get("aborts") or {}, "month": month,
                         "trips": [x["where"] for x in g.trips], "max_rss": g.max_rss,
                         "vm_hwm": NP.proc_status_bytes("VmHWM"),
                         "bars": {tf: WQ.listed_bars(rd, "BTC", tf, end) for tf in ("1d", "4h", "1h", "15m", "5m")}}))
"""


def test_long_history_coin_builds_1d_4h_1h_15m_units_and_only_its_5m_units_end_too_big_under_the_guard(tmp_path, capsys):
    """İnceleme engeli (P3g): coin'in ilk birimi kalan bütün dizilerini (5m/15m dahil) yüklüyordu (≈ 7 yılda +375/+490 MiB,
    ≈ 3 sn) ve koruma bunu görmüyordu (birimden hemen önceki denetim 5 sn'lik zamanlayıcıyı sıfırlıyordu): gerçek gecede OOM
    ya da sağlıklı 1d/4h/1h birimlerinin iki gecede `TOO_BIG_SKIPPED` olması. Şimdi: birim YALNIZ kendi dizilerini yükler;
    yükleme öncesi tahmin + RSS eşiği aşan birim yüklenmeden bırakılır ve sayım YALNIZ ona yazılır; bekçi her atışta VmRSS
    okur. Taze süreçte gerçek boyutlu 6,5 yıllık tek coin (5m ≈ 690 bin, 15m ≈ 230 bin bar; sahte okuyucu), gerçek
    `MemoryGuard` (768 MiB) ve iki gece: 1d/4h/1h/15m birimleri kurulur, 5m birimleri (Box, AG_P2) iki gecede
    `TOO_BIG_SKIPPED`, ay kurulumu tamamlanır, VmHWM < 0,7 × 768 MiB ve hiçbir koruma ihlali yok. Ölçüm yazdırılır."""
    import subprocess
    keys = ["B.1d@BTC", "C.4h@BTC", "E.1h@BTC", "FAM_BOX@BTC", "AG_P3.15m@BTC", "AG_P2.5m@BTC"]
    now = int(night_after_seal(4).timestamp() * 1000)
    env = {**os.environ, "P3G_ROOT": str(ROOT), "P3G_DIR": str(tmp_path), "P3G_NOW": str(now), "P3G_YEARS": "6.5",
           "P3G_MEM": str(MEM_MAX), "P3G_UNITS": ",".join(keys)}
    cp = subprocess.run([sys.executable, "-c", LONG], capture_output=True, text=True, env=env, timeout=1800)
    line = next((ln for ln in cp.stdout.splitlines() if ln.startswith("@@")), None)
    assert cp.returncode == 0 and line, cp.stdout[-2000:] + cp.stderr[-3000:]
    d = json.loads(line[2:])
    n1, n2 = d["nights"]
    with capsys.disabled():
        print(f"\nP3g uzun geçmiş (BTC 6,5 yıl; 5m {d['bars']['5m']} / 15m {d['bars']['15m']} bar üst sınırı): VmHWM "
              f"{d['vm_hwm'] / MIB:.0f} MiB (eşik {0.7 * MEM_MAX / MIB:.0f}) · en çok RSS {d['max_rss'] / MIB:.0f} MiB · gece 1 "
              f"{n1['seconds']} sn ({n1['units_done']} birim, tahminle bırakılan {len(n1.get('guard_refused') or [])}) · "
              f"gece 2 {n2['seconds']} sn")
    assert d["bars"]["5m"] >= 6 * 105_000 and d["bars"]["15m"] >= 6 * 35_000, "gerçek boyutlu 5m/15m geçmişi"
    u = d["units"]
    for k in ("B.1d@BTC", "C.4h@BTC", "E.1h@BTC", "AG_P3.15m@BTC"):
        assert u[k]["status"] == WQ.ST_DONE, (k, u.get(k))
    for k in ("FAM_BOX@BTC", "AG_P2.5m@BTC"):
        assert u[k]["status"] == WQ.ST_TOO_BIG and u[k]["predicted_bytes"] > 0.7 * MEM_MAX, (k, u.get(k))
    assert set(d["aborts"]) == {"FAM_BOX@BTC", "AG_P2.5m@BTC"} and set(d["aborts"].values()) == {WQ.MAX_ABORTS}, \
        "sayım YALNIZ 5m birimlerine (başka dilimin birimine asla)"
    assert [x["unit"] for x in n1["guard_refused"]] == ["FAM_BOX@BTC", "AG_P2.5m@BTC"] and n1["units_done"] == 4, \
        "tahminle bırakılan 5m biriminden sonra kuyruk sürdü (15m birimi aynı gece kuruldu)"
    assert n1["stop"] == n2["stop"] == WQ.STOP_DONE and n2["backlog"]["complete_month"] == d["month"]
    assert n2["backlog"]["skipped"] == 2 and not n1.get("guard_aborts") and not n1.get("errors")
    assert d["trips"] == [] and d["vm_hwm"] < 0.7 * MEM_MAX, f"VmHWM {d['vm_hwm'] / MIB:.0f} MiB ≥ MEMORY_GUARD eşiği"


def test_cheap_rss_watch_trips_inside_a_units_own_load_and_counts_only_that_unit(tmp_path, monkeypatch):
    """İnceleme engeli (P3g): bekçi her atışta ucuz `VmRSS` okur (gc YOK); `guard()` (gc + RSS) yalnız eşik aşılınca aşımı
    doğrulamak için çağrılır. Birimin KENDİ yüklemesi sırasında eşik aşılırsa yalnız o birim bırakılır ve sayılır; geçmişi
    bırakılır, kuyruk sonraki birimle sürer (öndeki denetim belleği yeniden ölçer). Önceki ve sonraki birimler kurulur."""
    import signal
    import time
    from tradingbot.research_engine.paths import EnginePaths
    from tradingbot.research_engine.provider import SealedReader
    monkeypatch.setattr(WQ, "WATCH_EVERY_S", 0.05)
    p = EnginePaths(data=tmp_path / "a", state=tmp_path / "s")
    p.ensure_tree()
    p3_world(p, p.state, coins=["BTC"], fwd_days=3, pre_1d_days=260, pre_1h_days=12, pre_5m_days=1)
    rd = SealedReader.from_status(p)
    by = {u.key: u for u in _UNITS(COINS40)}
    units = [by["B.1d@BTC"], by["F2.5m@BTC"], by["C.1d@BTC"]]
    high = {"on": False}
    real_load = WQ.load_history

    def load(reader, coin, arrays, end, **kw):
        if "5m" in set(arrays):                               # 5m biriminin kendi yüklemesi RSS'i eşiğin üstüne taşır
            high["on"] = True
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10:                 # yalnız bekçi (SIGALRM) durdurabilir
                pass
            raise AssertionError("bekçi yüklemeyi bırakmadı")
        return real_load(reader, coin, arrays, end, **kw)
    monkeypatch.setattr(WQ, "load_history", load)
    calls: list[bool] = []
    reads = {"n": 0}

    def rss() -> int:
        reads["n"] += 1
        return (700 if high["on"] else 200) * MIB

    def guard():
        calls.append(high["on"])
        ok = not high["on"]
        high["on"] = False                                    # gc + bırakılan geçmiş: RSS eşiğin altına döner
        return {"ok": ok, "rss_bytes": (700 if not ok else 200) * MIB}
    before = signal.getsignal(signal.SIGALRM)
    now = int(night_after_seal(4).timestamp() * 1000)
    r = WQ.run_s4w(p, rd, entry_symbols=COINS40, ticks={"BTC": 1e-4}, now_ms=now, deadline=time.monotonic() + 600,
                   units=units, night_budget_s=3600.0, guard=guard, rss=rss, rss_limit=int(0.7 * MEM_MAX))
    assert r["stop"] == WQ.STOP_DONE and r["aborted"]["unit"] == "F2.5m@BTC" and r["aborted"]["why"] == WQ.STOP_GUARD
    assert r["guard_aborts"] == ["F2.5m@BTC"] and r["units_done"] + r["units_no_data"] == 2, r
    assert calls == [False, True, False] and reads["n"] >= 3, \
        "gc'li denetim: ilk birimden önce (yeni coin), yüklemedeki aşımı doğrulamak için, bırakmadan sonraki birimden önce"
    q = WQ.read_queue(WQ.root_of(p))
    b = q["building"]
    assert b["aborts"] == {"F2.5m@BTC": 1} and set(b["units"]) == {"B.1d@BTC", "C.1d@BTC"} and q["in_progress"] is None
    assert signal.getsignal(signal.SIGALRM) == before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_watchdog_never_raises_inside_its_exit_or_done_and_an_escaped_abort_still_reaches_s6_s7_s7b(tmp_path,
                                                                                                     monkeypatch):
    """Yarış: `_Watch.__exit__` başlarken bekleyen bir atış `on=False`'tan önce `UnitAborted` atardı (`with` bloğunun
    dışında; gece S7'siz düşerdi). İşleyici `__exit__`/`done` çerçevesinde atmaz; yine de kaçan bir `UnitAborted` S4w'yi
    FAILED yapar, gece S6/S7/S7b'ye sürer — `__exit__`'i hiç çağrılmamış, hâlâ kurulu ve atan bir bekçi bırakmış olsa bile
    (yedek onu kapatır)."""
    import signal
    import types
    w = WQ._Watch(lambda: WQ.STOP_DEADLINE, 0.0)
    w.on = True
    for code in (WQ._Watch.__exit__.__code__, WQ._Watch.done.__code__):
        w._handler(14, types.SimpleNamespace(f_code=code))
    assert w.fired == 0 and w.why is None
    try:
        w._handler(14, sys._getframe())
        raised = False
    except WQ.UnitAborted as ab:
        raised = ab.why == WQ.STOP_DEADLINE
    assert raised and w.fired == 1, "gövdenin içinde atış yine bırakır"
    _small(monkeypatch)
    v, host = _world(tmp_path)

    before = signal.getsignal(signal.SIGALRM)

    def escaped(*a, **k):
        left = WQ._Watch(lambda: WQ.STOP_DEADLINE, 0.02)       # `__exit__`'i atlanmış bekçi: her atışta yeniden atar
        left.__enter__()
        raise WQ.UnitAborted(WQ.STOP_DEADLINE)
    monkeypatch.setattr(NP, "run_s4w", escaped)
    st = run_engine_night(v, host, night_after_seal(4))
    assert _st(st, "S4w")["status"] == "FAILED" and "UnitAborted S4w dışına kaçtı" in _st(st, "S4w")["error"]
    assert all(_st(st, s)["status"] == "OK" for s in ("S6", "S7", "S7b")), st["stages"]
    assert WQ._Watch.current is None and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == before and not WQ.disarm_watch()


def test_a_daytime_smoke_on_a_look_day_does_not_consume_the_months_gate_a_look(tmp_path, monkeypatch):
    """Sürüm/elle smoke aynı servisi gündüz başlatır (`daytime`: başlangıç ≥ 03:40 UTC; zamanlayıcı 01:37 UTC): ayın tek
    Kapı A bakışını tüketmez (`gate_a/look_nights.json` yazılmaz); bakışı zamanlanmış gece yapar. `gate_a/` ve
    `parity/` yedektedir (yeniden üretilemez)."""
    from tradingbot.research_engine import backup as B
    _small(monkeypatch)
    v, host = _world(tmp_path, fwd_days=6)
    look = night_after_seal(7)
    rec = v.paths.research / "gate_a" / "look_nights.json"
    assert NP.gate_a_night(v.paths, look.date())
    sm = run_engine_night(v, host, look.replace(hour=10, minute=5))
    assert not sm["p3"]["gate_a_night"] and sm["p3"]["gate_a_look"].startswith("DEFERRED_DAYTIME_RUN")
    assert _st(sm, "S6")["result"]["gate_a"]["status"] == "NOT_LOOK_NIGHT" and NP.F_GATE_A_NOT_COMPUTED not in sm["flags"]
    assert not rec.exists() and NP.gate_a_night(v.paths, (look + timedelta(days=1)).date()), "ay bakışsız kaldı"
    st = run_engine_night(v, host, look + timedelta(days=1))
    assert st["p3"]["gate_a_night"] and _st(st, "S6")["result"]["gate_a"]["status"] == "HESAPLANMADI" and rec.exists()
    assert {"gate_a", "parity"} <= set(B.INCLUDE) and not {"gate_a", "parity"} & set(B.EXCLUDE)

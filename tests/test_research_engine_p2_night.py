# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2b — gece aşamaları S1b/S2, özet ve sorgu (docs/SYSTEM_LEARNING_ENGINE_V1.md §6.1, §7.7, §8,
§2.8; P2 kabul 8, 12, 13).

* Gece planı S0, S1a, S3, S1b, S2, S7, S7b; A/B KAPALI ve SKEW gecelerinde S1b/S2 planlanmaz; eski veri mührü S2'yi
  `DATA_STALE` ile atlatır (P1b sözleşme testinde).
* Kabul 8: aynı mühür → günlük, yollar ve atıf BAYT-ÖZDEŞ (iki bağımsız kopya); ikinci S2 hiçbir satırı yeniden kurmaz.
* Kaldığı yerden devam (son tarih): adım adım kurulan günlük/atıf tek seferde kurulanla bayt-özdeştir; gece payı
  bitmişse aşama `SKIPPED_DEADLINE`, ertesi gece tamamlanır.
* Kabul 12: `digest_tr.md` ≤ 8 KB (yoğun bir günde de), sorgular ≤ 150 satır / ≤ 4 KB; yalnız-gerçekleşmiş satırda hüküm
  kelimesi yok; tam gecede yazımlar yalnız `data/research` altında, ledger'lar "r" kipinde.
* Kabul 13 (zamanlama): ölçeklenmiş sentetik dünyada S1b + S2'nin işlem başına süresi 2.000 işleme taşındığında (VPS
  için ×3 pay) gece son tarihinin içinde kalır; tam 2.000 işlem ölçümü `ENGINE_P2_TIMING_FULL=1` ile (P2 uygulama
  notlarında kayıtlı). A/B tur etkisi (≤ %5) yalnız VPS'te ölçülür (`--ab-report`, §2.9).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeHost, run_engine_night  # noqa: E402
from research_engine_p2_fixtures import config_text, standard_world, utc  # noqa: E402

from tradingbot.research_engine import analysis as A  # noqa: E402
from tradingbot.research_engine import journal as J  # noqa: E402
from tradingbot.research_engine import night as N  # noqa: E402
from tradingbot.research_engine import report as RP  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402
from tradingbot.research_engine import summary as SM  # noqa: E402
from tradingbot.research_engine.query import run_query  # noqa: E402

NIGHT = utc(2026, 10, 6, 1, 40)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}


def _world_night(tmp_path: Path, name: str = "w"):
    w = standard_world(tmp_path / name)
    host = FakeHost(tmp_path / (name + "_host"), config_text=config_text())
    SC.register_epoch(w.paths, "b" * 40, utc(2020, 1, 1), "t", code_hash=SC.engine_code_hash())     # A/B penceresi dışı
    return w, host


# ============================================================================ gece planı ve çıktılar
def test_full_p2_night_runs_s1b_s2_and_writes_bounded_turkish_digest(tmp_path):
    w, host = _world_night(tmp_path)
    st = run_engine_night(w.v, host, NIGHT, save=False)
    assert st["result"] == N.R_SUCCESS, st["stages"]
    assert list(st["plan"]) == ["S0", "S1a", "S3", "S1b", "S2", "S7", "S7b"] == list(N.PLAN_FULL)
    assert all(st["stages"][s]["status"] == "OK" for s in N.PLAN_FULL), {k: v.get("status") for k, v in st["stages"].items()}
    s1b, s2 = st["stages"]["S1b"]["result"], st["stages"]["S2"]["result"]
    assert s1b["journal"]["rows"] == 40 and s1b["journal"]["pending"] == 0 and s1b["reconcile"]["status"] == "OK"
    assert s1b["fidelity"]["gate_pass"] and s2["rows"] == 40 and s2["pending"] == 0 and s2["grid"]["OK"] == 38
    assert (st["stages"]["S3"]["result"].get("utc_day") or {}).get("status") == "OK"
    res = w.paths.research
    for rel in ("journal/tj_v1/_build.json", "attribution/_build.json", "attribution/_index.json.gz", "paths/needs_1m.json"):
        assert (res / rel).exists(), rel
    digest = (res / "summary" / "digest_tr.md").read_text(encoding="utf-8")
    assert len(digest.encode("utf-8")) <= SM.DIGEST_MAX_BYTES
    for head in ("## Dün kapanan işlemler (2026-10-05 UTC)", "## Neden kaybetti — son 7 gün", "## Nasıl kâra dönebilirdi",
                 "## Veri ve doğruluk sağlığı", "## Günlük hedef"):
        assert head in digest, head
    assert "HINDSIGHT" in digest and "EX_ANTE" in digest and "Fidelity (§5.3): 38/38" in digest
    assert not SM.claim_word_violations(digest.splitlines())
    doc = json.loads((res / "summary" / "engine_summary.json").read_text(encoding="utf-8"))
    p2 = doc["p2"]
    assert p2["attribution"]["rows"] == 40 and p2["attribution"]["attribution_sha"] and p2["utc_day"]["headline"] == "tgt_v1"
    status = SM.status_lines(w.paths, now=NIGHT + timedelta(minutes=30))
    assert any("S1b OK" in ln and "S2 OK" in ln for ln in status) and len(status) <= SM.STATUS_MAX_LINES


def test_ab_off_and_skew_nights_do_not_plan_s1b_or_s2(tmp_path):
    w = standard_world(tmp_path / "w")
    host = FakeHost(tmp_path / "w_host", config_text=config_text())
    # motor dönemi 10-01 → 10-06 (gün-yıl 279, tek) A/B penceresinde KAPALI gece
    SC.register_epoch(w.paths, "b" * 40, utc(2026, 10, 1), "release", code_hash=SC.engine_code_hash())
    st = run_engine_night(w.v, host, NIGHT, save=False)
    assert st["result"] in (N.R_AB_OFF, N.R_AB_OFF_FORCED), st["result"]
    assert st["stages"]["S1b"]["status"] == st["stages"]["S2"]["status"] == "NOT_PLANNED"
    skew = FakeHost(tmp_path / "skew", git_rc=1, config_text=config_text())
    st = run_engine_night(w.v, skew, NIGHT + timedelta(days=1), save=False)
    assert tuple(st["plan"]) == N.PLAN_SKEW and st["stages"]["S1b"]["status"] == "NOT_PLANNED"
    assert st["stages"]["S2"]["status"] == "NOT_PLANNED"


# ============================================================================ kabul 8: aynı mühür → bayt-özdeş
def test_same_seal_gives_byte_identical_journal_paths_and_attribution(tmp_path):
    outs = []
    for name in ("a", "b"):
        w = standard_world(tmp_path / name)
        J.build_journal(w.paths, now=utc(2026, 10, 6, 2), src=w.source(), rawconfig=w.rc)
        r = A.run_s2(w.paths, now=utc(2026, 10, 6, 2))
        assert r["built_rows"] == 40
        outs.append({k: _tree(w.paths.research / k) for k in ("journal", "paths", "attribution")})
    for k in ("journal", "paths", "attribution"):
        assert outs[0][k] and outs[0][k] == outs[1][k], k
    # yeniden çalıştırma: hiçbir satır yeniden kurulmaz, dosyalar dokunulmaz
    wa = tmp_path / "a" / "data" / "research" / "attribution"
    before = {p: (wa / p).stat().st_mtime_ns for p in outs[0]["attribution"]}
    from tradingbot.research_engine.paths import EnginePaths
    paths = EnginePaths(data=tmp_path / "a" / "data", state=tmp_path / "a" / "data" / "state")
    r = A.run_s2(paths, now=utc(2026, 10, 7, 2))
    assert r["built_rows"] == 0 and r["months_written"] == []
    assert {p: (wa / p).stat().st_mtime_ns for p in outs[0]["attribution"]} == before


def test_resumed_builds_are_byte_identical_to_one_shot_builds(tmp_path):
    one = standard_world(tmp_path / "one")
    J.build_journal(one.paths, now=utc(2026, 10, 6, 2), src=one.source(), rawconfig=one.rc)
    A.run_s2(one.paths, now=utc(2026, 10, 6, 2))
    step = standard_world(tmp_path / "step")
    seen = []
    for _ in range(12):
        s = J.build_journal(step.paths, now=utc(2026, 10, 6, 2), src=step.source(), rawconfig=step.rc, max_rows=9)
        seen.append(s["pending"])
        if not s["pending"]:
            break
    assert seen[0] == 31 and seen[-1] == 0 and len(seen) == 5, seen
    zero = J.build_journal(step.paths, now=utc(2026, 10, 6, 2), src=step.source(), rawconfig=step.rc, budget_s=0.0)
    assert zero["pending"] == 0 and zero["built_rows"] == 0
    rs = []
    for _ in range(12):
        r = A.run_s2(step.paths, now=utc(2026, 10, 6, 2), max_rows=13)
        rs.append(r["pending"])
        if not r["pending"]:
            break
    assert rs[0] == 27 and rs[-1] == 0, rs
    for k in ("journal", "attribution"):
        assert _tree(one.paths.research / k) == _tree(step.paths.research / k), k
    # en yeni ay önce (dün kapanan işlemler ilk gece işlenir): ilk adımda kurulan 13 satırın hepsi en yeni aydadır;
    # günlükte ay içinde de en yeni kapanış önce kurulur
    fresh = standard_world(tmp_path / "fresh")
    J.build_journal(fresh.paths, now=utc(2026, 10, 6, 2), src=fresh.source(), rawconfig=fresh.rc, max_rows=13)
    allc = sorted(str(r["closed_at"]) for r in J.iter_journal(fresh.paths))
    full = sorted(str(r["closed_at"]) for r in J.iter_journal(one.paths))
    assert allc == full[-13:]
    J.build_journal(fresh.paths, now=utc(2026, 10, 6, 2), src=fresh.source(), rawconfig=fresh.rc)
    A.run_s2(fresh.paths, now=utc(2026, 10, 6, 2), max_rows=13)
    got = {str(r["closed_at"])[:7] for r in A.iter_attribution(fresh.paths)}
    assert got == {max(full)[:7]} and sum(1 for _ in A.iter_attribution(fresh.paths)) == 13


def test_no_stage_budget_left_skips_with_deadline_and_next_night_completes(tmp_path, monkeypatch):
    w, host = _world_night(tmp_path)
    monkeypatch.setattr(N, "S1B_RESERVE", timedelta(hours=2, minutes=2, seconds=30))
    st = run_engine_night(w.v, host, NIGHT, save=False)
    assert st["stages"]["S1b"]["status"] == "SKIPPED_DEADLINE" and N.F_DEADLINE in st["flags"]
    assert st["stages"]["S2"]["status"] == "SKIPPED" and st["result"] == N.R_DEADLINE
    assert st["stages"]["S3"]["status"] == "OK" and st["stages"]["S7b"]["status"] == "OK"     # ana ölçüm ve yedek sürer
    monkeypatch.undo()
    st = run_engine_night(w.v, host, NIGHT + timedelta(days=1), save=False)
    assert st["result"] == N.R_SUCCESS and st["stages"]["S2"]["result"]["rows"] == 40


# ============================================================================ kabul 12: sınırlar
def _heavy_day(paths, day: str, n: int = 260) -> None:
    """Bir güne yığılmış çok sayıda atıf satırı (sınır testi; gerçek satır biçimi)."""
    base = next(iter(A.iter_attribution(paths)))
    rows = []
    for i in range(n):
        r = json.loads(json.dumps(base))
        r["trade_key"] = f"{['strategy_paper_box', 'main_fut', 'strategy_paper'][i % 3]}|H{i:05d}|{day}T00:00:00+00:00"
        r["book"] = r["trade_key"].split("|")[0]
        r["closed_at"] = f"{day}T{i % 24:02d}:{i % 60:02d}:00+00:00"
        r["net_r"] = str(-1.0 + (i % 7) * 0.3)
        rows.append(r)
    p = A.attr_month_file(paths, day[:7])
    old = list(A.iter_attribution(paths, [day[:7]]))
    from tradingbot.research_engine.paths import gzip_bytes, json_line
    paths.write_bytes(p, gzip_bytes("".join(json_line(r) for r in old + rows).encode("utf-8")))


def test_digest_and_queries_stay_bounded_on_a_heavy_day(tmp_path):
    w, host = _world_night(tmp_path)
    st = run_engine_night(w.v, host, NIGHT, save=False)
    _heavy_day(w.paths, "2026-10-05")
    text = SM.digest_text(w.paths, st, now=NIGHT)
    assert len(text.encode("utf-8")) <= SM.DIGEST_MAX_BYTES and "## Dün kapanan işlemler" in text
    assert "engine-query day 2026-10-05" in text                                   # kısaltma notu
    assert not SM.claim_word_violations(text.splitlines())
    for kw in (dict(topic="day", arg="2026-10-05"), dict(topic="why-lost", days=30), dict(topic="why-lost", book="main_fut"),
               dict(topic="trade", arg="F00001")):
        lines, code = run_query(w.paths, now=NIGHT, **kw)
        assert code == 0 and 1 < len(lines) <= RP.QUERY_MAX_LINES, (kw, len(lines))
        assert len("\n".join(lines).encode("utf-8")) <= RP.QUERY_MAX_BYTES, kw
    assert run_query(w.paths, topic="day", arg="10-05")[1] == 2
    assert run_query(w.paths, topic="trade")[1] == 2


def test_trade_query_answers_why_and_how_with_labels(tmp_path):
    w, host = _world_night(tmp_path)
    run_engine_night(w.v, host, NIGHT, save=False)
    loser = next(r for r in A.iter_attribution(w.paths) if (r.get("attribution") or {}).get("outcome") == "LOSS"
                 and (r.get("grid") or {}).get("status") == "OK")
    lines, code = run_query(w.paths, topic="trade", arg=loser["trade_key"])
    text = "\n".join(lines)
    assert code == 0 and "NEDEN (LOSS; attribution_v1, gözlem)" in text and "IZGARA (cfgrid_v1" in text
    assert "HINDSIGHT" in text and "R AYRIŞTIRMASI" in text and "artık" in text
    for vid in ("E_ACTUAL", "S_ATR150", "X_T2R", "SKIP"):
        assert f"  {vid}" in text, vid


def test_engine_query_cli_is_read_only_and_bounded(tmp_path, monkeypatch, capsys):
    w, host = _world_night(tmp_path)
    run_engine_night(w.v, host, NIGHT, save=False)
    before = _tree(w.paths.research)
    monkeypatch.setenv("TRADINGBOT_DATA", str(w.v.data))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(w.v.state))
    import tradingbot.cli as cli

    def no_config(*a, **k):
        raise AssertionError("engine-query config yüklemez")
    monkeypatch.setattr(cli, "load_config", no_config)
    rc = cli.main(["engine-query", "why-lost", "--days", "30"])
    out = capsys.readouterr().out
    assert rc == 0 and "NEDEN KAYBETTİ" in out and len(out.strip().splitlines()) <= 150
    assert _tree(w.paths.research) == before, "sorgu hiçbir şey yazmaz"


# ============================================================================ kabul 13: zamanlama
def test_night_timing_scaled_synthetic_world_fits_the_deadline_for_2000_trades(tmp_path):
    """Ölçeklenmiş dünya (≈ 120 işlem; Box %50, ana bot, T2, M2, D4, C4; 1m yol, 125 günlük 5m geçmiş): S1b + S2 işlem
    başına süresi × 2.000 × 3 (VPS payı) < gece son tarihine kadarki S1b/S2 payı. Tam ölçüm: ENGINE_P2_TIMING_FULL=1."""
    from research_engine_fixtures import FakeVps
    from research_engine_p2b_fixtures import NOW, big_world, stage_seconds
    full = os.environ.get("ENGINE_P2_TIMING_FULL") == "1"
    n = 2000 if full else 120
    v = FakeVps(tmp_path / "big")
    counts = big_world(v, n_trades=n, days=90 if full else 10, history_days=125)
    host = FakeHost(tmp_path / "host", config_text=config_text())
    SC.register_epoch(v.paths, "b" * 40, utc(2020, 1, 1), "t", code_hash=SC.engine_code_hash())
    st = run_engine_night(v, host, NOW)
    sec = stage_seconds(st)
    rows = st["stages"]["S2"]["result"]["rows"]
    assert st["result"] == N.R_SUCCESS and rows == sum(counts.values()), (st["stages"], counts)
    per_trade = (sec["S1b"] + sec["S2"]) / rows
    window = (N.DEADLINE_AFTER - N.S2_RESERVE).total_seconds()
    assert per_trade * 2000 * (1 if full else 3) < window, (per_trade, window)
    assert st["stages"]["S2"]["result"]["grid"].get("OK", 0) >= 0.9 * rows


# ============================================================================ §2.8: P2 gecesinde yazım sınırı
def test_p2_night_writes_only_under_research_and_reads_state_read_only(tmp_path, monkeypatch):
    from test_research_engine_night import _spy_io, _write_violations
    w, host = _world_night(tmp_path)
    state_before = _tree(w.v.state)
    ev = _spy_io(monkeypatch)
    st = run_engine_night(w.v, host, NIGHT, save=False)
    lines, _code = run_query(w.paths, topic="why-lost", days=30, now=NIGHT)
    monkeypatch.undo()
    assert st["result"] == N.R_SUCCESS and lines
    assert not _write_violations(ev, w.paths.research), _write_violations(ev, w.paths.research)[:5]
    reads = [e for e in ev if e[0] == "open" and e[1] and "/state/" in e[1]]
    assert reads and all(e[2] in ("r", "rb", "rt") for e in reads), [e for e in reads if e[2] not in ("r", "rb", "rt")][:5]
    assert _tree(w.v.state) == state_before, "state baytları değişmedi"


def test_daytime_smoke_run_caps_s1b_and_s2_and_the_night_run_does_not(tmp_path, monkeypatch):
    """Gece penceresi dışındaki (elle/sürüm smoke) çalıştırmada S1b/S2 payı `DAYTIME_STAGE_BUDGET_S` ile sınırlıdır."""
    w, host = _world_night(tmp_path)
    monkeypatch.setattr(N, "DAYTIME_STAGE_BUDGET_S", 0.0)
    st = run_engine_night(w.v, host, utc(2026, 10, 6, 14, 5), save=False)
    assert st["stages"]["S1b"]["status"] == "SKIPPED_DEADLINE" and "payı" in st["stages"]["S1b"]["reason"]
    st = run_engine_night(w.v, host, utc(2026, 10, 7, 1, 40), save=False)
    assert st["stages"]["S1b"]["status"] == "OK" and st["stages"]["S2"]["status"] == "OK"


def test_one_failing_trade_does_not_stop_s2_and_is_recorded_not_guessed(tmp_path, monkeypatch):
    from tradingbot.research_engine import cfgrid as CG
    w = standard_world(tmp_path / "w")
    J.build_journal(w.paths, now=utc(2026, 10, 6, 2), src=w.source(), rawconfig=w.rc)
    real = CG.evaluate
    calls = {"n": 0}

    def flaky(env, rc):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("yapay ızgara hatası")
        return real(env, rc)
    monkeypatch.setattr(CG, "evaluate", flaky)
    r = A.run_s2(w.paths, now=utc(2026, 10, 6, 2), max_rows=4)
    assert r["errors"] == 1 and r["built_rows"] == 4
    bad = [x for x in A.iter_attribution(w.paths) if (x.get("grid") or {}).get("reason") == "ERROR:RuntimeError"]
    assert len(bad) == 1 and bad[0]["attribution"]["primary_code"] is None and "yapay" in bad[0]["attribution"]["error"]

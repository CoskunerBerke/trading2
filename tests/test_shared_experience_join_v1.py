# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — BİRLEŞTİRME (2026-09-29; SPEC_V1 §5.4 / §4.5, görev X3).

* **Sinyal anahtarı** gerçek işlemde karşı-olgusal kayıtçının anahtarıyla AYNI biçimde türetilir:
  ana bot `_signal_id` (aynı turun `risk_log` + `decisions` + `briefs`; `_seen_signals` ile çapraz denetim), kâğıt defterler
  `meta.signal.signal_ts` (D4/C4) ya da `features.data_source.bars[karar dilimi]` (T2/M2/Box; kural `_signal_key(act)` ile
  eşit — gerçek turda `apply_action` casusuyla denetlenir), Formasyon `plan_id`.
* **SUPERSEDED**: aynı sinyal gerçek işleme dönüşünce kaybolan karşı-olgusal, gerçek girişle AYNI `join_key`i taşır; anahtar
  defteri içerir (başka defterin aynı sinyali değiştirme SAYILMAZ).
* **Aynı durum**: aynı barları gören gerçek giriş ve karşı-olgusal (farklı defterler dahil) AYNI anlık görüntüyü alır
  (önbellek anahtarı; `input_sha` eşit, önbellek isabeti).
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_shared_experience_collector_v1 as C  # noqa: E402

from tradingbot.shared_experience import collector as XC  # noqa: E402
from tradingbot.shared_experience import rows as R  # noqa: E402

XP_ON = {"shared_experience": {"enabled": True, "mode": "RECORD"}}


def _complete_frames(eng) -> None:
    """`test_engine_v3._engine` çerçevelerinin son barı "bir bar önce kapanmış"tır (oluşan bar yok) → anlık görüntü
    tamamlanamaz. Üretimdeki gibi oluşan barı içerecek şekilde 4h/1h bir bar ileri alınır (yalnız zaman)."""
    for fr in eng._fake_live._frames.values():
        for tf, ms in (("4h", 14_400_000), ("1h", 3_600_000)):
            df = fr[tf]
            df["timestamp"] = df["timestamp"] + ms
            df.index = df.index + pd.Timedelta(milliseconds=ms)


def _perp_provenance(eng, syms) -> None:
    """`_execute` doğrudan çağrıldığında (tur dışı) provenans ve BTC çerçevesi elle kurulur (tur bunu kendisi yapar)."""
    first = eng.runner.last_frames[syms[0]]
    eng.runner.last_frames.setdefault("BTC/USDT", first)
    eng._frame_provenance = {s: {"market": "USDM_PERP", "source": "test"} for s in list(syms) + ["BTC/USDT"]}


def _xp_rows(eng) -> list[dict]:
    p = eng.cfg.state_path / "shared_experience" / "experience.jsonl"
    import json
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ============================================================================ ana bot: _signal_id + SUPERSEDED
def test_main_real_signal_key_is_the_engine_signal_id_and_supersedes_its_counterfactual(tmp_path, monkeypatch):
    import test_learning_mode_main as M
    from tradingbot.core import utc_now
    eng = M._eng(tmp_path, monkeypatch, M._lm(), extra=XP_ON)
    _complete_frames(eng)
    now = utc_now().replace(microsecond=0)
    eng._tour_now_ms = int(now.timestamp() * 1000)
    eng.killswitch.trip("MANUAL", "test")
    decisions, chief, briefs, marks = M._cands(eng, M.SYMS[:1])
    _perp_provenance(eng, M.SYMS[:1])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    assert opened == [] and len(M._main_cfs(eng)) == 1
    eng._shared_experience_step(risk_log, decisions, briefs, now)
    cf0 = [r for r in _xp_rows(eng) if r["kind"] == "xp_cf"]
    assert [(r["rev"], r["status"], r["snapshot_status"]) for r in cf0] == [(0, "PENDING", "OK")]
    eng.killswitch.reset("test", "reset")
    eng.run_id = "run_2"
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    assert len(opened) == 1 and M._main_cfs(eng) == []
    eng._shared_experience_step(risk_log, decisions, briefs, now + timedelta(seconds=5))
    rows = _xp_rows(eng)
    ent = [r for r in rows if r["kind"] == "xp_entry"]
    cf = [r for r in rows if r["kind"] == "xp_cf"]
    d, b = decisions[M.SYMS[0]], briefs[0]
    sig = eng._signal_id(M.SYMS[0], "USDM_PERP", d, d.active_plan, b)
    assert len(ent) == 1 and ent[0]["signal_key"] == sig and ent[0]["signal_key_src"] == "ENGINE_SIGNAL_ID"
    assert sig in eng._seen_signals and cf0[0]["signal_key"] == sig
    assert [(r["rev"], r["status"]) for r in cf] == [(0, "PENDING"), (1, "SUPERSEDED")]
    assert cf[1]["join_key"] == ent[0]["join_key"] == "main|%s|%s|%s|" % (sig, M.SYMS[0], "SHORT")
    assert ent[0]["snapshot"]["input_sha"] == cf0[0]["snapshot"]["input_sha"], "aynı barlar → aynı durum"


def test_main_signal_key_not_confirmed_by_seen_signals_is_left_null(tmp_path, monkeypatch):
    import test_learning_mode_main as M
    from tradingbot.core import utc_now
    eng = M._eng(tmp_path, monkeypatch, M._lm(), extra=XP_ON)
    _complete_frames(eng)
    now = utc_now().replace(microsecond=0)
    eng._tour_now_ms = int(now.timestamp() * 1000)
    decisions, chief, briefs, marks = M._cands(eng, M.SYMS[:1])
    _perp_provenance(eng, M.SYMS[:1])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    assert len(opened) == 1
    eng._seen_signals = []                                                   # çapraz denetim tutmaz → uydurma YOK
    eng._shared_experience_step(risk_log, decisions, briefs, now)
    ent = [r for r in _xp_rows(eng) if r["kind"] == "xp_entry"]
    assert len(ent) == 1 and ent[0]["signal_key"] is None and ent[0]["join_key"] is None
    assert eng._shared_xp.c["main_sigkey_unconfirmed"] == 1


# ============================================================================ kâğıt defterler: kural anahtarıyla eşitlik (gerçek tur)
def test_strategy_book_signal_key_equals_the_rule_signal_key_at_open(tmp_path, monkeypatch):
    import test_engine_v3 as E
    import test_strategy_paper_engine_v1 as SP
    import tradingbot.strategy_paper as sp
    ov = {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                             "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]}, **XP_ON}
    eng = E._engine(tmp_path, monkeypatch, ov, symbols=2)
    _complete_frames(eng)
    SP._install(eng, monkeypatch, btc_up=True, coin_above=True)
    acts: dict[tuple[str, str], dict] = {}
    orig = sp.apply_action

    def spy(act, *a, **k):
        res = orig(act, *a, **k)
        if act.get("action") == "OPEN":
            acts[(str(act.get("name")), str(k.get("symbol")))] = dict(act)
        return res
    monkeypatch.setattr(sp, "apply_action", spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    ent = [r for r in _xp_rows(eng) if r["kind"] == "xp_entry" and r["book"] != "main"]
    assert {r["book"] for r in ent} == {"strategy_paper", "strategy_paper_m2"} and acts
    for r in ent:
        act = acts[(r["book_name"], r["symbol"])]
        assert r["signal_key"] == sp._signal_key(act) and r["signal_key_src"] == "DATA_SOURCE_BAR", r["book"]
        assert r["snapshot_status"] == "OK" and r["origin"] == "LIVE"


def test_meta_signal_and_data_source_bar_give_the_same_key_format_and_pattern_uses_the_plan_id(tmp_path):
    d4 = XC.BookAdapter(key="strategy_paper_trend4h", book_type=R.STRATEGY, lock=lambda: None, ledger=lambda: None,
                        cf_trades=lambda: None, cf_under_lock=True, decision_tf="4h")
    open_facts = {"symbol": "SOL/USDT", "side": "LONG", "meta": {"signal": {"signal_ts": 1_759_132_800_000}},
                  "features": {"data_source": {"bars": {"4h": 1_759_132_800_000, "1d": 1}}}}
    closed_facts = {"symbol": "SOL/USDT", "side": "LONG", "features": {"data_source": {"bars": {"4h": 1_759_132_800_000}}}}
    assert XC.real_signal_key(d4, open_facts) == ("signal_ts:1759132800000", "META_SIGNAL_TS")
    assert XC.real_signal_key(d4, closed_facts) == ("signal_ts:1759132800000", "DATA_SOURCE_BAR")
    import tradingbot.strategy_paper as sp
    assert XC.real_signal_key(d4, open_facts)[0] == sp._signal_key({"signal_ts": 1_759_132_800_000})
    pat = XC.BookAdapter(key="pattern_trader", book_type=R.PATTERN, lock=lambda: None, ledger=lambda: None,
                         cf_trades=lambda: None, cf_under_lock=True)
    assert XC.real_signal_key(pat, {"features": {"plan_id": "pl_1"}}) == ("pl_1", "PLAN_ID")
    assert XC.real_signal_key(d4, {"features": {}}) == (None, None), "anahtar uydurulmaz"


def test_c4_variation_is_part_of_the_join_and_a_real_c4_open_supersedes_only_its_variation(tmp_path):
    c4 = C.FakeBook(tmp_path, "strategy_paper_candle4h", decision_tf="4h")
    eng = C.fake_engine(tmp_path, books=[c4])
    xp = C.collector(eng)
    C.step(xp, eng, C.T0)
    at = C.T0 + timedelta(minutes=1)
    a = C.record_cf(c4, "SOL/USDT", sig=C.T0_MS - C.H4, created=at, reason="ALSO_MATCHED", variation="CV001")
    b = C.record_cf(c4, "SOL/USDT", sig=C.T0_MS - C.H4, created=at, reason="ALSO_MATCHED", variation="CV002")
    assert a.id == R.cf_id_for("c4_candle_variations", "signal_ts:%d" % (C.T0_MS - C.H4), "SOL/USDT", "LONG", "CV001")
    C.set_tour(eng, C.T0_MS + 60_000)
    C.step(xp, eng, C.T0 + timedelta(minutes=2))
    C.open_pos(c4.ledger, "SOL/USDT", at=C.T0 + timedelta(minutes=3), meta={"signal": {"signal_ts": C.T0_MS - C.H4}},
               features={"candle_variation": {"id": "CV001"}})
    assert c4.cf.supersede(signal_key="signal_ts:%d" % (C.T0_MS - C.H4), symbol="SOL/USDT", direction="LONG",
                           variation="CV001") == 1
    C.step(xp, eng, C.T0 + timedelta(minutes=4))
    rows = C.rows_of(eng)
    last = {r["cf_id"]: r for r in rows if r["kind"] == "xp_cf"}
    ent = [r for r in rows if r["kind"] == "xp_entry"][0]
    assert last[a.id]["status"] == "SUPERSEDED" and last[a.id]["join_key"] == ent["join_key"]
    assert ent["variation"] == "CV001" and ent["join_key"].endswith("|LONG|CV001")
    assert last[b.id]["status"] == "PENDING" and b.id in xp._books["strategy_paper_candle4h"].cf_known


def test_the_same_signal_in_another_book_is_not_a_supersede(tmp_path):
    box = C.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    t2 = C.FakeBook(tmp_path, "strategy_paper", decision_tf="1d")
    eng = C.fake_engine(tmp_path, books=[box, t2])
    xp = C.collector(eng)
    C.step(xp, eng, C.T0)
    t = C.record_cf(box, "SOL/USDT", sig=C.T0_MS, created=C.T0 + timedelta(minutes=1))
    C.set_tour(eng, C.T0_MS + 60_000)
    C.step(xp, eng, C.T0 + timedelta(minutes=2))
    C.open_pos(t2.ledger, "SOL/USDT", at=C.T0 + timedelta(minutes=3), meta={"signal": {"signal_ts": C.T0_MS}})
    box.cf.sb.trades = []                                                   # Box kaydı başka nedenle düştü
    C.step(xp, eng, C.T0 + timedelta(minutes=4))
    last = {r["cf_id"]: r for r in C.rows_of(eng) if r["kind"] == "xp_cf"}
    assert last[t.id]["status"] == "DROPPED", "join_key defteri içerir: T2'nin işlemi Box karşı-olgusalını DEĞİŞTİRMEZ"


# ============================================================================ aynı durum (önbellek anahtarı)
def test_real_and_counterfactual_rows_on_the_same_bars_share_one_snapshot_across_books(tmp_path):
    box = C.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    pat = C.FakePattern(tmp_path)
    eng = C.fake_engine(tmp_path, books=[box], pattern=pat)
    xp = C.collector(eng)
    C.step(xp, eng, C.T0)
    at = C.T0 + timedelta(minutes=1)
    C.open_pos(eng.ledger2, "SOL/USDT", at=at)
    C.open_pos(pat.ledger, "SOL/USDT", at=at, features={"plan_id": "pl_9", "family": "M3_MOMENTUM_3WS_RSI70"})
    C.record_cf(box, "SOL/USDT", sig=C.T0_MS, created=at, side="SHORT")
    C.set_tour(eng, C.T0_MS + 60_000)
    C.step(xp, eng, C.T0 + timedelta(minutes=2))
    rows = C.rows_of(eng)
    snaps = {(r["kind"], r["book"]): r["snapshot"] for r in rows}
    assert set(snaps) == {("xp_entry", "main"), ("xp_entry", "pattern_trader"), ("xp_cf", "strategy_paper_box")}
    vals = list(snaps.values())
    assert all(v == vals[0] for v in vals) and vals[0]["status"] == "OK"
    st = xp.cache.stats()
    assert st["misses"] == 1 and st["hits"] == 2, "bir hesap, iki isabet: kitaplar arası 'aynı durum' birleşimi"
    pe = [r for r in rows if r["book"] == "pattern_trader"][0]
    assert pe["signal_key"] == "pl_9" and pe["signal_key_src"] == "PLAN_ID" and pe["family"] == "MOMENTUM"

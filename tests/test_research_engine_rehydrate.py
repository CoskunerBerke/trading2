# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2a — kural bağlamını yeniden kurma `rehydrate` (docs/SYSTEM_LEARNING_ENGINE_V1.md §5.2, §6.2;
§10 P2 kabul 6).

Strateji defterlerinin (T2, M2, Box, D4, C4) işlemleri canlı kuralın kendi kararıyla açılır
(`tests/research_engine_p2_fixtures.py`); kuralın o anki eylemi (hedefler, stop, sinyal bağlamı) dünya meta dosyasına
yazılır ve rehydrate'in mühürlü depodan geri kazandığıyla karşılaştırılır. Ağ yok.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_p2_fixtures import World, standard_world, utc  # noqa: E402

from tradingbot.research_engine import journal as J  # noqa: E402
from tradingbot.research_engine import rehydrate as RH  # noqa: E402

NOW = utc(2026, 10, 6, 2)
RULE_BOOKS = ("strategy_paper", "strategy_paper_m2", "strategy_paper_box", "strategy_paper_trend4h", "strategy_paper_candle4h")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    w = standard_world(tmp_path_factory.mktemp("rehydrate_world"))
    summary = J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    rows = {r["trade_key"]: r for r in J.iter_journal(w.paths)}
    return w, summary, rows


def _record(w: World, key: str) -> dict:
    book = key.split("|", 1)[0]
    loc = J.scan_archive(w.paths, book)[key]
    return json.loads(json.dumps(J.load_records(w.paths, book, {key: loc})[key]))


def _memory(w: World, book: str, tid: str) -> dict | None:
    m = J.SideSources(w.v.state, ids_by_book={book: {tid}}, keys=set()).memory(book, tid)
    return m[0] if m else None


def _rh(w: World, key: str, rec: dict | None = None, *, memory: dict | None | str = "auto", src="auto") -> RH.Rehydrated:
    book, tid, _o = key.split("|", 2)
    rec = rec if rec is not None else _record(w, key)
    mem = _memory(w, book, tid) if memory == "auto" else memory
    return RH.rehydrate(rec, book, src=w.source() if src == "auto" else src, rawconfig=w.rc, memory_row=mem)


def _close(a, b, rel=1e-9) -> bool:
    return a == b or (a is not None and b is not None and abs(float(a) - float(b)) <= rel * max(1.0, abs(float(b))))


# ============================================================================ kabul 6
def test_match_rate_per_book_and_config_epoch_is_at_least_95_percent(built):
    _w, summary, rows = built
    ms = summary["rehydrate"]
    assert ms == RH.match_stats(list(rows.values()))
    assert set(ms) == set(RULE_BOOKS)
    for book in RULE_BOOKS:
        n = sum(v["n"] for v in ms[book].values())
        assert n >= 4, book
        for ep, v in ms[book].items():
            assert v["rate"] >= 0.95 and v["meets_target"], (book, ep, v)
    assert set(ms["strategy_paper_box"]) == {"E0", "E2026-09-30", "E2026-10-03"}, "Box üç config döneminde ayrı raporlanır"
    assert set(ms["strategy_paper"]) == {"E0", "E2026-10-03"} and set(ms["strategy_paper_m2"]) == {"E0", "E2026-10-03"}
    for r in rows.values():
        if r["book"] in RULE_BOOKS:
            m = r["rehydrate"]["match"]
            assert m["ok"] and m["stop"]["ok"] and m["signal_bar"]["ok"] and m["entry_side_of_stop"], r["trade_key"]
            assert D(m["stop"]["abs_diff"]) <= D(m["tick"])
            assert m["entry_ref"]["basis"] == "SIGNAL_CLOSE_VS_TRADE_MEMORY" and m["entry_ref"]["ok"]


def test_rehydrated_targets_and_signal_context_equal_what_the_live_rule_used(built):
    w, _s, rows = built
    n = 0
    for book in RULE_BOOKS:
        for m in w.meta[book]:
            act = m["act"]
            r = rows[f"{book}|{m['id']}|{m['opened_at']}"]
            rh, ctx = r["rehydrate"], r["signal_ctx"]
            assert rh["status"] == RH.OK, (book, m["id"], rh["reason"])
            assert _close(D(r["initial_stop"]), act["stop"]), "stop ÖLÇÜLMÜŞ olandır ve kuralınkiyle aynı"
            assert len(r["targets"]) == len(act.get("targets") or [])
            for a, b in zip(r["targets"], act.get("targets") or []):
                assert _close(a, b), (book, m["id"], r["targets"], act["targets"])
            assert r["signal_ts"] == act["signal_ts"] and _close(ctx["signal_close"], act["signal_close"])
            if book in ("strategy_paper", "strategy_paper_m2"):
                assert _close(ctx["ema200"], act["ema200"]) and _close(ctx["atr14"], act["atr14"])
                assert ctx["regime"] == act["regime"]
                if book == "strategy_paper_m2":
                    assert _close(ctx["ref_close"], act["ref_close"]) and ctx["ref_ts"] == act["ref_ts"]
            elif book == "strategy_paper_box":
                for k in ("box_high", "box_low", "box_mid", "day_low", "day_high"):
                    assert _close(ctx[k], act[k]), k
                assert ctx["location"] == act["location"] and ctx["exit_kind"] == act["exit_kind"]
                # Box `min_stop_pct` bir FİLTREDİR (geometri değil; dönemle değişti): etikette nötrlenir
                assert ctx["params_label"] == act["params_label"].replace("_ms0.3", "")
                assert rh["diag"]["filter_params_ignored"] == ["min_stop_pct"]
            elif book == "strategy_paper_trend4h":
                assert _close(ctx["channel_high20"], act["channel_high20"]) and _close(ctx["atr14"], act["atr14"])
            else:
                assert ctx["variation_id"] == act["setup_type"].split(":", 1)[1] == r["variation_id"]
                assert ctx["definition_sha"] == act["variation"]["definition_sha"] == r["params_hash"]
            n += 1
    assert n >= 30


# ============================================================================ eşleşmeme → RECONSTRUCT_FAILED (tahmin yok)
def _key(rows, book: str, i: int = 0) -> str:
    return sorted(k for k in rows if k.startswith(book + "|"))[i]


def test_broken_records_are_reconstruct_failed_with_a_reason_never_guessed(built):
    w, _s, rows = built
    kt = _key(rows, "strategy_paper")
    rec = _record(w, kt)
    tick = RH.tick_of(rec)
    # 1) ölçülmüş stop kaydırılmış (1 tick'ten fazla)
    bad = json.loads(json.dumps(rec))
    bad["features"]["initial_stop"] = float(D(str(rec["features"]["initial_stop"])) + 3 * tick)
    r1 = _rh(w, kt, bad)
    assert r1.status == RH.FAILED and "STOP_MISMATCH" in r1.reason and r1.targets is None and r1.signal_ctx is None
    assert r1.match["stop"]["ok"] is False
    # 2) kaydın okuduğu sinyal barı bir gün kaydırılmış (yanlış bar): o bardan kurulan geometri ölçülmüşü tutmaz
    bad = json.loads(json.dumps(rec))
    bad["features"]["data_source"]["bars"]["1d"] -= 86_400_000
    r2 = _rh(w, kt, bad)
    assert r2.status == RH.FAILED and "STOP_MISMATCH" in r2.reason and "ENTRY_REF_MISMATCH" in r2.reason
    # 3) işlem hafızasındaki ölçülmüş sinyal kapanışı tutmuyor
    mem = json.loads(json.dumps(_memory(w, "strategy_paper", kt.split("|")[1])))
    mem["features"]["signal_close"] = float(mem["features"]["signal_close"]) + 0.05
    r3 = _rh(w, kt, memory=mem)
    assert r3.status == RH.FAILED and "ENTRY_REF_MISMATCH" in r3.reason
    # 4) C4: kayıttaki varyasyon tanımının özeti değişmiş
    kc = _key(rows, "strategy_paper_candle4h")
    bad = _record(w, kc)
    bad["features"]["candle_variation"]["definition_sha"] = "0" * 16
    r4 = _rh(w, kc, bad)
    assert r4.status == RH.FAILED and "DEFINITION_SHA_MISMATCH" in r4.reason
    # 5) okunan bar bilinmiyor / depo yok
    bad = _record(w, kt)
    bad["features"]["data_source"] = {}
    assert _rh(w, kt, bad).reason == "BARS_UNKNOWN_1D"
    assert _rh(w, kt, src=None).status == RH.NO_DATA
    # 6) stop ölçülmemiş (eski kayıt): tahmin edilmez
    bad = _record(w, kt)
    bad["features"].pop("initial_stop")
    assert _rh(w, kt, bad).reason == "INITIAL_STOP_MISSING"


def test_failed_rehydrate_leaves_targets_and_signal_ctx_missing_in_the_journal_and_counts_against_the_rate(built):
    w, _s, rows = built
    book = "strategy_paper_box"
    kb = sorted(k for k, r in rows.items() if r["book"] == book and r["exit_reason"] == "hedef1")[0]
    loc = J.scan_archive(w.paths, book)[kb]
    rec = _record(w, kb)
    rec["features"]["initial_stop"] = float(D(str(rec["features"]["initial_stop"])) + D("0.05"))
    ctx = J.BuildContext(paths=w.paths, now=NOW, src=w.source(), rawconfig=w.rc,
                         sides=J.SideSources(w.v.state, ids_by_book={book: {kb.split("|")[1]}}, keys={kb}),
                         exec_by_book=J._exec_params_by_book(w.paths), equity_points=[], write_paths=False)
    row = J.build_row(loc, rec, "futures", ctx)
    assert row["rehydrate"]["status"] == RH.FAILED
    for f in ("targets", "signal_ctx", "dist_level_atr"):
        assert row[f] is None and row["field_source"][f] == J.MISSING, f
        assert "RECONSTRUCT_FAILED" in row["field_notes"].get(f, "RECONSTRUCT_FAILED") or f == "dist_level_atr"
    assert row["field_source"]["initial_stop"] == J.MEASURED, "stop her durumda ölçülmüş olandır"
    # hedefle çıkmış ama hedef bilinmiyor: yeniden oynatılamaz, fidelity paydasına girmez
    assert row["fidelity"]["status"] == "NOT_ELIGIBLE" and row["fidelity"]["primary_reason"] == "TARGETS_MISSING"
    others = [r for k, r in rows.items() if r["book"] == book and k != kb]
    st = RH.match_stats(others + [row])[book]
    ep = row["config_epoch"]
    assert st[ep]["failed"][0]["trade_key"] == kb and "STOP_MISMATCH" in st[ep]["failed"][0]["reason"]
    assert st[ep]["rate"] < 1.0


def test_stop_must_match_within_one_tick(built):
    w, _s, rows = built
    k = _key(rows, "strategy_paper_box", 1)
    rec = _record(w, k)
    tick = RH.tick_of(rec)
    assert tick == D("0.01")
    for shift, ok in ((D("0.6") * tick, True), (tick, True), (D("1.5") * tick, False)):
        b = json.loads(json.dumps(rec))
        b["features"]["initial_stop"] = float(D(str(rec["features"]["initial_stop"])) + shift)
        assert (_rh(w, k, b).status == RH.OK) is ok, shift


def test_tick_is_the_written_precision_of_the_entry_fill():
    def rec(px):
        return {"fills": [{"kind": "entry", "price": px}]}
    assert RH.tick_of(rec("216.60")) == D("0.01"), "sondaki sıfır korunur (defter dolumu kuantize eder)"
    assert RH.tick_of(rec("17000")) == D("1")
    assert RH.tick_of(rec("0.000123")) == D("0.000001")
    assert RH.tick_of(rec("1.123456789012")) == D("1e-8")
    assert RH.tick_of({"entry": "x"}) == D("1e-8")


# ============================================================================ config dönemi ve parametreler
def test_config_epoch_uses_provisional_doc_boundaries_until_the_sealed_p3_file_exists(tmp_path):
    ep = RH.config_epoch
    assert ep("strategy_paper_box", "2026-09-29T23:59:59+00:00") == ("E0", RH.EPOCH_SRC_PROVISIONAL)
    assert ep("strategy_paper_box", "2026-10-01T06:00:00+00:00")[0] == "E2026-09-30"
    assert ep("strategy_paper_box", "2026-10-04T06:00:00+00:00")[0] == "E2026-10-03"
    assert ep("strategy_paper", "2026-10-01T00:00:00+00:00")[0] == "E0", "Box sınırı yalnız Box'a uygulanır"
    assert ep("strategy_paper", "2026-10-03T00:00:00+00:00")[0] == "E2026-10-03"
    assert ep("strategy_paper", None) == (None, "MISSING")
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "config_epochs.json").write_text(json.dumps({"epochs": [
        {"id": "LIB-A", "from": "2026-01-01T00:00:00+00:00", "to": "2026-10-02T00:00:00+00:00"},
        {"id": "LIB-B", "from": "2026-10-02T00:00:00+00:00", "books": ["strategy_paper"]}]}), encoding="utf-8")
    assert ep("strategy_paper", "2026-10-01T00:00:00+00:00", research=tmp_path) == ("LIB-A", RH.EPOCH_SRC_SEALED)
    assert ep("strategy_paper", "2026-10-05T00:00:00+00:00", research=tmp_path) == ("LIB-B", RH.EPOCH_SRC_SEALED)
    assert ep("strategy_paper_box", "2026-10-05T00:00:00+00:00", research=tmp_path)[1] == RH.EPOCH_SRC_PROVISIONAL


def test_rule_parameters_come_from_the_raw_config_or_the_rule_default(built):
    w, _s, rows = built
    rp, atr, src = RH.rule_params_for("t2_trend_regime", w.rc)
    assert src == "CONFIG_RAW" and atr == 3.0 and rp["leverage_max"] == 4
    rp, _atr, src = RH.rule_params_for("c4_candle_variations", w.rc)
    assert src == "CONFIG_RAW" and rp["variations"][0] == "CV001_BREAKOUT20_TREND_VOL_L"
    assert RH.rule_params_for("t2_trend_regime", None) == ({}, None, "DEFAULT")
    for r in rows.values():
        if r["book"] in RULE_BOOKS:
            assert r["rehydrate"]["params_source"] == "CONFIG_RAW" and r["params_hash"]
    a = rows[_key(rows, "strategy_paper")]["params_hash"]
    assert all(r["params_hash"] == a for r in rows.values() if r["book"] == "strategy_paper"), "aynı kural + parametre → aynı özet"

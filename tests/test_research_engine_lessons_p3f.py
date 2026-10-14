# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P3f — dersler (docs/ENGINE_BRAIN_V1.md §4.1–§4.4, §8.1 P3f kabulü; mühürler S1 + S1c).

Kabul: bakış yalnız eşiklerde; alfa bölünmesi; BH ailesi; ileri doğrulama yalnız T_k sonrası; geçişler; runner
APPLIED_BOUNDED yazamaz; dönüşüm → spec + O ile tekilleştirme; canlı kural dışlama; §5.10 dönem başına yeniden üretim.
Ek: bütün defterler (defter × kod), aylık LIB_v1.<AAAA-AA>, aynı gece yeniden çalışma, `engine-query lessons`. Yalnız
sentetik veri; ağ yok.
"""
from __future__ import annotations

import ast
import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tradingbot.research_engine import lessons as L
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import seal_s1c as S1C
from tradingbot.research_engine import stats as ST
from tradingbot.research_engine import trials as TR
from tradingbot.research_engine.paths import EnginePaths

ROOT = Path(__file__).resolve().parents[1]
VER = ("a" * 64, "b" * 64)
MS = {v["variant_id"]: hashlib.sha256(v["variant_id"].encode()).hexdigest() for v in RG.LIB_V1["variants"]}
UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
RAW = {"ok": True, "values": {"entry_universe.symbols": UNIVERSE},
       "books": {"b1_box_fade": {"min_stop_pct": 0.5, "rule_params": {"exit_kind": "box_mid", "exit_r": 2.0}},
                 "t2_trend_regime": {"atr_mult": 3.0}, "m2_tsmom28": {"atr_mult": 3.0},
                 "d4_donchian_20_10": {"symbols": ["BTC/USDT", "ETH/USDT"]}}}
T0 = datetime(2026, 11, 2, tzinfo=UTC)
BOX, T2, D4, MAIN = "strategy_paper_box", "strategy_paper", "strategy_paper_trend4h", "main_fut"


def _paths(tmp_path) -> EnginePaths:
    return EnginePaths(data=tmp_path / "data", state=tmp_path / "data" / "state")


def mk(i, book, day, r, *, ep="E1", sym="BTC/USDT", tactic="t", cohort="POLICY", bucket=("UP", "HIGH"), grid=None,
       codes=None, stop=0.8, hour=3, at=None):
    o = at or (T0 + timedelta(days=day, hours=hour))
    c = o + timedelta(hours=2)
    cells = [{"id": "E_ACTUAL", "status": "OK", "r_cons": r}] + [{"id": k, "status": "OK", "r_cons": r + d}
                                                                 for k, d in (grid or {}).items()]
    return {"trade_key": f"{book}:{cohort}:{i}", "book": book, "symbol": sym, "tactic": tactic, "config_epoch": ep,
            "cohort": cohort, "opened_at": o.isoformat(), "closed_at": c.isoformat(), "net_r": r, "stop_dist_pct": stop,
            "attribution": {"registry_sha": VER[0], "codes_loss": codes if codes is not None else
                            (["WRONG_DIRECTION"] if r <= 0 else []), "codes_win": [] if r <= 0 else ["CLEAN_ENTRY"],
                            "costs_r": {"cost_all": 0.05}},
            "grid": {"registry_sha": VER[1], "status": "OK", "rank_src": "cf_aux_v1", "cells": cells},
            "regime_fit": {"bucket": list(bucket)} if bucket else {"status": "NO_BUCKET"}}


def night(day: int) -> datetime:
    """Gün `day`'in işlemlerinden sonraki gece (ertesi gün 01:37 UTC)."""
    return T0 + timedelta(days=day + 1, hours=1, minutes=37)


def run(paths, now, rows, *, raw=RAW, versions=VER, **kw):
    return L.run_lessons(paths, now=now, rows=rows, raw=raw, module_src_sha=MS, versions=versions, **kw)


def looks(paths, kind="LESSON_LOOK"):
    return [r for r in TR.iter_rows(paths.trials_file) if r["kind"] == kind] if paths.trials_file.exists() else []


def noise_rows(book, days, per_day, *, mean=0.0, sd=1.0, seed=0, start=0, **kw):
    rng = random.Random(seed)
    out, i = [], start
    for d in days:
        for j in range(per_day(d) if callable(per_day) else per_day):
            out.append(mk(i, book, d, mean + rng.gauss(0, sd), hour=1 + (j % 20), **kw))
            i += 1
    return out


def store(paths):
    return L.LessonStore(paths).load()


# ============================================================================ mühür ve sabitler
def test_sealed_constants_and_readings_are_what_the_code_uses():
    assert S1C.check() == (True, "OK")
    th = RG.LESSON_REGISTRY_V1["thresholds"]
    assert L.N_THRESHOLDS == (30, 60, 120, 240) == tuple(th["looks"]["n_thresholds"])
    assert L.ALPHAS == (0.01, 0.01, 0.015, 0.015) and abs(sum(L.ALPHAS) - th["looks"]["alpha_total"]) < 1e-12
    assert (L.BH_Q, L.PAIR_CAP, L.MIN_N, L.MIN_DAYS, L.CI_B) == (0.10, 2000, 30, 10, 5000)
    assert L.MAX_SPECS_PER_MONTH == 20
    from tradingbot.research_engine import analysis as AN
    assert set(L.GRID_CELLS) == set(AN.RANKED_VARIANTS) - {"SKIP"} and len(L.PAIR_TYPES) == 16
    # D4'ün canlı stopu kod sabitidir (ham config'te yok): S1c okuması kodla aynı
    from tradingbot import donchian_trend as DT
    assert S1C.LESSON_READINGS_V1["live_rule"]["T2"]["D4"]["value"] == DT.STOP_ATR
    # taban eşlemesi LIB_v1 kopyalarıyla ve config dönemlerinin defter kurallarıyla tutarlı
    vs = RG.variants_by_id()
    for book, base in L.BASES.items():
        for v in S1C.LESSON_READINGS_V1["transforms"]["base_variants"][base]:
            assert vs[v]["book"]["book"] == book
        assert L._config_name(book) is not None


def test_s1c_mismatch_refuses_the_lesson_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(S1C, "PINNED", {**S1C.PINNED, "S1C": "0" * 64})
    with pytest.raises(L.LessonsRefused, match="S1C_SHA_MISMATCH"):
        run(_paths(tmp_path), night(30), [])
    assert not (tmp_path / "data").exists() or not _paths(tmp_path).trials_file.exists()


# ============================================================================ kabul: bakış yalnız eşiklerde
def test_looks_only_at_thresholds_one_per_pair_per_night_and_only_when_eligible(tmp_path):
    p = _paths(tmp_path)
    # gün 0–4: günde 8 işlem (n 40 ≥ 30 ama yalnız 5 gün → uygun değil); gün 5–9: günde 1 (gün 9'da 10 gün → bakış 1);
    # gün 10: 220 işlem birden (n ≥ 240: bekleyen 2, 3, 4 sonraki gecelere birer birer); gün 11+ işlem yok
    sched = {**{d: 8 for d in range(5)}, **{d: 1 for d in range(5, 10)}, 10: 220}
    rows = noise_rows(BOX, range(11), lambda d: sched[d], sd=1.0, seed=3)
    per_night = {}
    for d in range(0, 16):
        before = p.trials_file.read_bytes() if p.trials_file.exists() else b""
        s = run(p, night(d), rows)
        new = [r for r in looks(p) if r["at"] == L.iso(night(d))]
        per_night[d] = new
        if not new:                                          # ara gece: deftere hiçbir şey yazılmaz
            assert (p.trials_file.read_bytes() if p.trials_file.exists() else b"") == before
        assert s["looks"] == len(new)
    filt = {d: [r for r in rs if r["params"]["type"] == "FILTER"] for d, rs in per_night.items()}
    assert [d for d, rs in filt.items() if rs] == [9, 10, 11, 12]                 # 5 hücre (L0–L4) aynı gecelerde
    for d, k in ((9, 1), (10, 2), (11, 3), (12, 4)):
        assert len(filt[d]) == 5 and {r["look_no"] for r in filt[d]} == {k}
        for r in filt[d]:
            assert r["n"] >= L.N_THRESHOLDS[k - 1] and r["n_days"] >= 10 and r["params"]["alpha"] == L.ALPHAS[k - 1]
    # çift başına gece en çok bir bakış; bakış numaraları 1..4 artan
    by_pair = {}
    for r in looks(p):
        by_pair.setdefault(r["row_id"], []).append((r["at"], r["look_no"]))
    for rid, ls in by_pair.items():
        assert len({a for a, _ in ls}) == len(ls) and [k for _, k in ls] == list(range(1, len(ls) + 1)), rid
    # 4. bakıştan sonra bakış yok (uygun ve yeni işlem gelse bile)
    more = rows + noise_rows(BOX, range(13, 20), 30, seed=9, start=10_000)          # bakış 4'ten SONRA kapananlar
    for d in range(13, 20):
        run(p, night(d), more)
    assert not [r for r in looks(p) if r["params"]["type"] == "FILTER" and r["at"] > L.iso(night(12))]
    assert not looks(p, "RETIRE")
    assert TR.verify_chain(p.trials_file)["counters"]["N_lesson_cum"] == len(looks(p))


def test_random_arrivals_property_looks_only_on_threshold_crossings(tmp_path):
    for seed in range(3):
        p = _paths(tmp_path / str(seed))
        rng = random.Random(seed)
        counts = {d: rng.choice([0, 0, 1, 2, 5, 15, 40]) for d in range(40)}
        rows = noise_rows(BOX, range(40), lambda d: counts[d], seed=seed, bucket=None)
        last, prev_n = {}, {}
        for d in range(40):
            run(p, night(d), rows)
        for r in looks(p):
            rid, k = r["row_id"], r["look_no"]
            assert r["n"] >= L.N_THRESHOLDS[k - 1] and r["n_days"] >= L.MIN_DAYS
            assert last.get(rid, 0) == k - 1                                     # sırayla, atlamasız
            assert r["at"] > prev_n.get(rid, "")                                  # her gece en çok bir
            last[rid], prev_n[rid] = k, r["at"]
        # beklenen: n_k'yı geçmiş ve uygun her eşik ya bakılmış ya da hâlâ sırada (gece başına bir)
        n_all = len(rows)
        for rid, k in last.items():
            assert k <= sum(1 for t in L.N_THRESHOLDS if n_all >= t)


# ============================================================================ kabul: alfa bölünmesi
def test_alpha_split_per_look_and_validation_alpha():
    assert L.discovery_verdict(0.2, 0.012, 0.02, 1) == "NOT_SIGNIFICANT"          # α1 = 0,01
    assert L.discovery_verdict(0.2, 0.012, 0.02, 2) == "NOT_SIGNIFICANT"          # α2 = 0,01
    assert L.discovery_verdict(0.2, 0.012, 0.02, 3) == "SIGNIFICANT"              # α3 = 0,015
    assert L.discovery_verdict(0.2, 0.016, 0.02, 4) == "EXHAUSTED"                # α4 = 0,015; son bakış
    assert L.discovery_verdict(0.2, 0.001, 0.11, 1) == "NOT_SIGNIFICANT"          # q > 0,10 → değil (BH de şart)
    assert L.discovery_verdict(-0.1, 0.001, 0.001, 1) == "NOT_SIGNIFICANT"        # etki ≤ 0 → değil
    assert [L.validation_look_no(k) for k in (1, 2, 3, 4)] == [2, 3, 4, 4]
    assert L.validation_verdict(0.2, 0.0099, 0.01, True) == "FWD_PASS"
    assert L.validation_verdict(0.2, 0.0101, 0.01, True) == "FWD_FAIL"
    assert L.validation_verdict(0.2, 0.001, 0.01, False) == "FWD_FAIL"
    assert L.validation_verdict(-0.01, 0.001, 0.01, True) == "FWD_SIGN_FLIP"


# ============================================================================ kabul: BH ailesi
def _multi_book_rows():
    rows = []
    rows += noise_rows(BOX, range(15), 6, mean=-0.4, sd=0.8, seed=1, sym="BTC/USDT",
                       grid={"S_ATR150": 0.1, "X_T2R": -0.05})
    rows += noise_rows(BOX, range(15), 4, mean=0.0, sd=1.0, seed=2, sym="ETH/USDT", start=5000)
    rows += noise_rows(T2, range(15), 3, mean=0.05, sd=1.0, seed=4, start=9000, bucket=("DOWN", "LOW"))
    rows += noise_rows(MAIN, range(15), 3, mean=-0.2, sd=1.0, seed=5, start=12000)
    return rows


def test_bh_family_is_all_looks_of_the_night_across_books_and_types(tmp_path):
    p = _paths(tmp_path)
    run(p, night(14), _multi_book_rows())
    rows = looks(p)
    assert {r["params"]["book"] for r in rows} == {BOX, T2, MAIN}
    assert {r["params"]["type"] for r in rows} >= {"FILTER", "GRID:S_ATR150", "GRID:X_T2R"}
    q = ST.bh_qvalues({r["row_id"]: r["p_day"] for r in rows})
    for r in rows:
        assert r["q_bh"] == q[r["row_id"]]
        assert r["verdict"] == L.discovery_verdict(r["params"]["effect"], r["p_day"], r["q_bh"], r["look_no"])


def test_nightly_pair_cap_orders_by_n_then_cell_and_carries_the_rest(tmp_path, monkeypatch):
    rows = _multi_book_rows()
    full = _paths(tmp_path / "full")
    run(full, night(14), rows)
    allr = sorted(looks(full), key=lambda r: (-r["n"], r["params"]["cell_id"], r["params"]["pair_id"]))
    monkeypatch.setattr(L, "PAIR_CAP", 7)
    p = _paths(tmp_path / "cap")
    s = run(p, night(14), rows)
    first = looks(p)
    assert s["family_m"] == 7 and s["overflow_carried"] == len(allr) - 7
    assert [r["row_id"] for r in sorted(first, key=lambda r: (-r["n"], r["params"]["cell_id"]))] == \
        [r["row_id"] for r in allr[:7]]
    q = ST.bh_qvalues({r["row_id"]: r["p_day"] for r in first})                 # aile = yalnız o gecenin 7 bakışı
    assert all(r["q_bh"] == q[r["row_id"]] for r in first)
    for d in range(15, 30):                                                     # taşanlar sonraki gecelere (sayılır)
        s2 = run(p, night(d), rows)
        assert s2["family_m"] <= 7 and (s2["overflow_carried"] == 0 or s2["family_m"] == 7)
    first_looks = {r["row_id"] for r in looks(p) if r["look_no"] == 1}
    assert first_looks == {r["row_id"] for r in allr}                            # hiçbiri kaybolmadı
    assert TR.verify_chain(p.trials_file)["counters"]["N_lesson_cum"] == len(looks(p))


# ============================================================================ kabul: ileri doğrulama yalnız T_k sonrası
def _fwd_world(after_mean, *, after_sd=0.6, months_fn=None, per_day=3, extra=()):
    rows = noise_rows(BOX, range(10), per_day, mean=-0.7, sd=0.5, seed=11)
    days_after = range(11, 160)
    rng = random.Random(12)
    i = 100_000
    for d in days_after:
        day = T0 + timedelta(days=d)
        m = after_mean(day) if callable(after_mean) else after_mean
        for j in range(per_day if months_fn is None else months_fn(day)):
            rows.append(mk(i, BOX, d, m + rng.gauss(0, after_sd), hour=1 + j))
            i += 1
    return rows + list(extra)


T_K = night(9)                       # keşif gecesi (n 30, 10 gün)
FWD_DUE = datetime(2027, 3, 1, 1, 37, tzinfo=UTC)        # T_k ayı Kasım → Ara, Oca, Şub tam → 1 Mart'tan sonraki ilk gece


def test_forward_validation_uses_only_trades_after_t_k_and_waits_three_complete_months(tmp_path):
    p = _paths(tmp_path)
    rows = _fwd_world(-0.5)
    s = run(p, T_K, rows)
    disc = [r for r in looks(p) if r["params"]["type"] == "FILTER"]
    assert len(disc) == 5 and all(r["verdict"] == "SIGNIFICANT" and r["look_no"] == 1 for r in disc)
    assert s["states"] == {L.RESEARCH_HYPOTHESIS: 5}
    for d in (datetime(2026, 12, 20, 1, 37, tzinfo=UTC), datetime(2027, 2, 28, 1, 37, tzinfo=UTC)):
        run(p, d, rows)                                    # doğrulama vakti gelmedi; keşif bakışı da yok (hipotez evresi)
    assert len(looks(p)) == 5
    run(p, FWD_DUE, rows)
    val = [r for r in looks(p) if r["params"]["kind"] == "VALIDATION"]
    assert len(val) == 5
    t_k_ms = int(T_K.timestamp() * 1000)
    end_ms = int(datetime(2027, 3, 1, tzinfo=UTC).timestamp() * 1000)
    want = [r for r in rows if t_k_ms < L._ms(r["closed_at"]) < end_ms]
    for r in val:
        pa = r["params"]
        assert pa["t_k_ms"] == t_k_ms and pa["window_end_ms"] == end_ms and r["n"] == len(want)
        assert r["window"]["from"] > T_K.isoformat() and r["look_no"] == 2 and pa["alpha"] == L.ALPHAS[1]
        assert pa["folds"]["n_folds"] == 4 and pa["folds"]["ok"] and r["verdict"] == "FWD_PASS"
        assert r["row_id"].startswith(L.FWD_PREFIX)
    # BH ailesi doğrulama bakışlarını da içerir (o gecenin bütün bakışları)
    night_rows = [r for r in looks(p) if r["at"] == L.iso(FWD_DUE)]
    q = ST.bh_qvalues({r["row_id"]: r["p_day"] for r in night_rows})
    assert all(r["q_bh"] == q[r["row_id"]] for r in night_rows)
    # pencere sonrasındaki işlemler sonucu değiştirmez; T_k öncesi örneklem doğrulamaya girmez
    p2 = _paths(tmp_path / "late")
    late = [mk(900_000 + i, BOX, 0, 5.0, at=datetime(2027, 3, 1, 0, 30, tzinfo=UTC)) for i in range(50)]
    run(p2, T_K, rows)
    run(p2, FWD_DUE, rows + late)
    v2 = [r for r in looks(p2) if r["params"]["kind"] == "VALIDATION"]
    assert [(r["row_id"], r["n"], r["p_day"], r["params"]["effect"]) for r in v2] == \
        [(r["row_id"], r["n"], r["p_day"], r["params"]["effect"]) for r in val]


def test_forward_validation_outcomes_sign_flip_retired_inconsistent_rejected_and_deferral(tmp_path):
    # işaret döndü → RETIRED
    p = _paths(tmp_path / "flip")
    rows = _fwd_world(+0.5)
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    v = [r for r in looks(p) if r["params"]["kind"] == "VALIDATION"]
    assert v and all(r["verdict"] == "FWD_SIGN_FLIP" for r in v)
    assert set(d["policy_status"] for d in store(p).lessons.values()) == {L.RETIRED}
    # katman tutarlılığı yok (4 ayın 2'si kaybettiriyor, ortalama yine kaybettiriyor) → REJECTED
    p = _paths(tmp_path / "rej")
    rows = _fwd_world(lambda day: -1.2 if day.month in (11, 12) else 0.3, after_sd=0.3)
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    v = [r for r in looks(p) if r["params"]["kind"] == "VALIDATION"]
    assert v and all(r["verdict"] == "FWD_FAIL" and not r["params"]["folds"]["ok"] and r["params"]["effect"] > 0
                     for r in v)
    assert set(d["policy_status"] for d in store(p).lessons.values()) == {L.REJECTED}
    # seyrek ileri örneklem (< 30 işlem) → 1 Mart'ta bakış yok, pencere bir ay uzar (yalnız büyüklüğe bağlı)
    p = _paths(tmp_path / "defer")
    rows = _fwd_world(-0.5, months_fn=lambda day: 1 if day.day in (3, 13, 23) and day.month in (12, 1, 2) else
                      (4 if day.month == 3 else 0))
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    assert not [r for r in looks(p) if r["params"]["kind"] == "VALIDATION"]
    run(p, datetime(2027, 4, 1, 1, 37, tzinfo=UTC), rows)
    v = [r for r in looks(p) if r["params"]["kind"] == "VALIDATION"]
    assert v and all(r["params"]["window_end_ms"] == int(datetime(2027, 4, 1, tzinfo=UTC).timestamp() * 1000) for r in v)


# ============================================================================ kabul: geçişler (+ geçersizleşme)
def test_transitions_follow_the_lesson_store_and_invalidation_retires_and_relooks(tmp_path):
    p = _paths(tmp_path)
    rows = _fwd_world(-0.5)
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    st = store(p)
    for d in st.lessons.values():
        assert d["schema_version"] == "lesson_v2" and d["policy_status"] == L.RESEARCH_HYPOTHESIS
        assert [(h["from"], h["to"]) for h in d["status_history"]] == [(L.OBSERVATION, L.RESEARCH_HYPOTHESIS)]
        assert d["fwd_validation"] == "FWD_PASS" and d["hypothesis_sha"] and d["evidence"]["kind"] == "VALIDATION"
        assert d["keys"][0].startswith("B|") and any(k.startswith("E|") for k in d["keys"])
        assert d["mechanism_code"] == "WRONG_DIRECTION"
    doc = next(iter(st.lessons.values()))
    with pytest.raises(ValueError):                                     # atlamalı geçiş yok (learn.lesson_store)
        L.runner_transition({**doc, "policy_status": L.OBSERVATION, "evidence_level": L.OBSERVATION},
                            L.VALIDATED_POLICY_CANDIDATE, reason="x", at="t")
    # cfgrid_v1 sürümü değişti → bütün dersler RETIRED (INVALIDATED), yeni kuşak baştan bakar (yeni LESSON_LOOK)
    n0 = TR.verify_chain(p.trials_file)["counters"]["N_lesson_cum"]
    v2 = (VER[0], "c" * 64)
    rows2 = [{**r, "grid": {**r["grid"], "registry_sha": v2[1]}} for r in rows]
    s = run(p, datetime(2027, 3, 2, 1, 37, tzinfo=UTC), rows2, versions=v2)
    assert s["invalidated"] == 5 and len(looks(p, "RETIRE")) == 5
    st = store(p)
    old = [d for d in st.lessons.values() if d["gen"] == 0]
    assert len(old) == 5 and all(d["policy_status"] == L.RETIRED and "INVALIDATED" in d["status_history"][-1]["reason"]
                                 for d in old)
    new = [r for r in looks(p) if r["row_id"].endswith("|g1")]
    assert new and all(r["look_no"] == 1 for r in new)
    assert TR.verify_chain(p.trials_file)["counters"]["N_lesson_cum"] == n0 + len(new)
    # tek işlemin değeri değişti → yalnız o işlemi içeren çiftler geçersiz (kanıt özeti)
    p = _paths(tmp_path / "digest")
    rows = noise_rows(BOX, range(12), 3, mean=0.0, seed=5, sym="BTC/USDT") + \
        noise_rows(BOX, range(12), 3, mean=0.0, seed=6, sym="ETH/USDT", start=50_000)
    run(p, night(11), rows)
    k = next(i for i, r in enumerate(rows) if r["symbol"] == "ETH/USDT")
    rows[k] = {**rows[k], "net_r": rows[k]["net_r"] + 1.0}
    s = run(p, night(12), rows)
    ret = looks(p, "RETIRE")
    assert ret and all("I=BTC" not in r["params"]["pair_id"] for r in ret)
    assert any("I=ETH" in r["params"]["pair_id"] for r in ret) and s["invalidated"] == len(ret)


# ============================================================================ kabul: runner APPLIED_BOUNDED yazamaz
def test_runner_can_never_write_applied_bounded(tmp_path):
    from tradingbot.learn import lesson_store as LS
    p = _paths(tmp_path)
    rows = _d4_world()
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    st = store(p)
    vpc = [d for d in st.lessons.values() if d["policy_status"] == L.VALIDATED_POLICY_CANDIDATE]
    assert vpc
    # genel durum makinesi VPC → APPLIED_BOUNDED'a izin verir; gecenin tek geçiş yolu reddeder
    assert LS.transition(vpc[0], L.APPLIED_BOUNDED)["policy_status"] == L.APPLIED_BOUNDED
    with pytest.raises(L.RunnerForbidden):
        L.runner_transition(vpc[0], L.APPLIED_BOUNDED, reason="x", at="t")
    before = L.store_file(p).read_bytes()
    with pytest.raises(L.RunnerForbidden):
        st.append([{"event": "TRANSITION", "lesson_id": vpc[0]["lesson_id"], "to": L.APPLIED_BOUNDED, "lesson": vpc[0]}])
    with pytest.raises(L.RunnerForbidden):
        st.append([{"event": "UPDATE", "lesson_id": vpc[0]["lesson_id"],
                    "lesson": {**vpc[0], "policy_status": L.APPLIED_BOUNDED}}])
    assert L.store_file(p).read_bytes() == before
    assert L.APPLIED_BOUNDED not in L.RUNNER_TARGETS and L.APPLIED_BOUNDED in L.RUNNER_FORBIDDEN
    # kaynak: ad yalnız sabit açılımında; dize sabiti olarak hiçbir kod yolunda yok
    tree = ast.parse((ROOT / "tradingbot/research_engine/lessons.py").read_text(encoding="utf-8"))
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "APPLIED_BOUNDED"]
    assert len(uses) == 1 and isinstance(uses[0].ctx, ast.Store)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "APPLIED_BOUNDED"]
    for f in (p.trials_file, L.store_file(p), L.derived_file(p)):
        assert b"APPLIED_BOUNDED" not in f.read_bytes()


# ============================================================================ kabul: dönüşüm → spec + O ile tekilleştirme
def _meta(level, book=BOX, cohort="POLICY", coin="BTC", bucket="UP|HIGH", tactic="t"):
    dims = S1C.LESSON_READINGS_V1["cells"]["levels"][level]
    return {"level": level, "book": book, "epoch": "E1", "cohort": cohort, "tactic": tactic if "T" in dims else None,
            "coin": coin if "I" in dims else None, "cls": "COIN", "bucket": bucket if "X" in dims else None}


def test_transform_mapping_and_spec_identity():
    bp = L.build_proposal
    assert bp(_meta("L2"), "FILTER", module_src_sha=MS)["transform"] == "T1"
    assert bp(_meta("L3"), "FILTER", module_src_sha=MS)["transform"] == "T4"
    for lv in ("L0", "L1", "L4"):
        assert bp(_meta(lv), "FILTER", module_src_sha=MS)["status"] == "NO_TRANSFORM"
    for c, (t, a) in (("S_ATR075", ("T2", 0.75)), ("S_ATR200", ("T2", 2.0)), ("X_T1R", ("T3", "X_T1R")),
                      ("X_NONE_TIME", ("T3", "X_NONE_TIME"))):
        pr = bp(_meta("L2"), f"GRID:{c}", module_src_sha=MS)
        assert (pr["transform"], pr["arg"], pr["scope"]) == (t, a, "COIN:BTC")
        assert bp(_meta("L1"), f"GRID:{c}", module_src_sha=MS)["scope"] == "POOLED_OWN"
    for c in ("E_DELAY1", "E_LIMIT_025", "M_TP1_ON", "M_BE_OFF"):
        assert bp(_meta("L1"), f"GRID:{c}", module_src_sha=MS)["status"] == "NO_TRANSFORM"
    assert bp(_meta("L2", book=MAIN), "FILTER", module_src_sha=MS)["status"] == "NO_LIB_BASE"
    assert bp(_meta("L2", cohort="CAPACITY"), "FILTER", module_src_sha=MS)["status"] == "NO_TRANSFORM_COHORT"
    assert bp(_meta("L2"), "FILTER", module_src_sha={})["status"] == "NO_MODULE_SHA"
    a = bp(_meta("L2"), "FILTER", module_src_sha=MS)
    assert a == bp(_meta("L2"), "FILTER", module_src_sha=MS) and a["spec_sha"] == RG.spec_sha(a["spec"])
    assert a["spec"]["family"] == "O" and a["spec"]["params"]["base"] == "BOX" and a["scope"] == "POOLED_OWN"
    assert bp(_meta("L2", coin="ETH"), "FILTER", module_src_sha=MS)["spec_sha"] != a["spec_sha"]
    assert bp(_meta("L2", cohort="UNTAGGED"), "FILTER", module_src_sha=MS)["spec_sha"] == a["spec_sha"]   # kohort spec'te yok
    ms2 = {**MS, "K_BOX": "f" * 64}
    assert bp(_meta("L2"), "FILTER", module_src_sha=ms2)["spec_sha"] != a["spec_sha"]                   # taban kodu kimlikte
    # v1: hiçbir dönüşüm bir LIB_v1 örtüsü/kopyasıyla aynı tanımlı değil (eşdeğerlik tablosu boş, okunuşu S1c'de)
    lib = L.lib_spec_index(MS)
    assert len(lib) == sum(1 for r in RG.gate_a_rows() if r["row_type"] in ("COPY", "COVER")) == 452
    for book in L.BASES:
        for lv in L.LEVELS:
            for t in L.PAIR_TYPES:
                pr = bp(_meta(lv, book=book), t, module_src_sha=MS)
                if pr["status"] == "PROPOSAL":
                    assert (pr["spec_sha"], pr["scope"]) not in lib


def test_derived_specs_dedup_against_o_covers_existing_specs_and_the_ledger_family(tmp_path, monkeypatch):
    # (a) aynı spec iki dersten (POLICY ve UNTAGGED kohortu, aynı kayıp): bir PROPOSED, bir LINKED
    p = _paths(tmp_path / "two")
    rows = _fwd_world(-0.5) + [{**r, "trade_key": r["trade_key"] + "u", "cohort": "UNTAGGED"} for r in _fwd_world(-0.5)]
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    d = L.ChainFile(p, L.derived_file(p), L.DERIVED_SCHEMA).load().rows
    t1 = [x for x in d if x["transform"]["T"] == "T1"]
    assert sorted(x["status"] for x in t1) == ["LINKED", "PROPOSED"]
    assert len({x["spec_sha"] for x in t1}) == 1 and t1[1]["linked_to"] == t1[0]["row_id"]
    # (b) tanımı AYNI örtü (eşdeğerlik okuması verilmiş gibi) → LIB_v1 satırına bağlanır, yeni deneme yok
    rd = RG.thaw(L.RD)
    rd["transforms"]["cover_equivalence"] = {"T4:UP|HIGH": "F_VOL"}
    monkeypatch.setattr(L, "RD", RG.freeze(rd))
    p = _paths(tmp_path / "cover")
    rows = _fwd_world(-0.5)
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    d = L.ChainFile(p, L.derived_file(p), L.DERIVED_SCHEMA).load().rows
    t4 = [x for x in d if x["transform"]["T"] == "T4"]
    assert len(t4) == 1 and t4[0]["status"] == "LINKED" and t4[0]["linked_to"] == "LIB_v1:O_BOX_F_VOL@POOLED_OWN"
    assert t4[0]["spec_sha"] == RG.spec_sha(RG.lib_spec("O_BOX_F_VOL", MS["O_BOX_F_VOL"]))
    monkeypatch.undo()
    # (c) deneme defterinin LIB ailesinde aynı (spec_sha, kapsam) → bağlanır
    p = _paths(tmp_path / "ledger")
    want = L.build_proposal(_meta("L2"), "FILTER", module_src_sha=MS)
    TR.TrialsLedger(p).append([{"kind": "SPEC", "lib_version": "LIB_v1.2026-10", "spec_sha": want["spec_sha"],
                                "scope": want["scope"], "row_id": "earlier"}])
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    d = L.ChainFile(p, L.derived_file(p), L.DERIVED_SCHEMA).load().rows
    t1 = [x for x in d if x["transform"]["T"] == "T1"]
    assert len(t1) == 1 and t1[0]["status"] == "LINKED" and t1[0]["linked_to"] == "LIB_v1.2026-10:earlier"


def test_monthly_lib_seal_cap_order_overflow_and_idempotence(tmp_path):
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.load()
    dfile = L.ChainFile(p, L.derived_file(p), L.DERIVED_SCHEMA).load()
    rng = random.Random(4)
    items = []
    for i in range(25):
        spec = {"family": "O", "variant_id": f"D_BOX_T1_I-C{i}", "params": {}, "timeframes": ["1d"], "exits": {},
                "sizing": {}, "module_src_sha": "e" * 64}
        items.append({"at": "x", "lesson_id": f"LSN-{i}", "pair_id": f"p{i}", "look_row_id": f"r{i}", "look_seq": i,
                      "transform": {"T": "T1"}, "spec": spec, "spec_sha": RG.spec_sha(spec), "scope": "POOLED_OWN",
                      "row_id": f"{spec['variant_id']}@POOLED_OWN", "q": round(rng.random() / 10, 4),
                      "delta": round(rng.uniform(-1, 1), 3), "live": None, "status": "PROPOSED", "linked_to": None})
    dfile.append(items)
    assert not L.seal_month(p, dfile, led, now=datetime(2027, 3, 1, 1, 37, tzinfo=UTC))["sealed"]   # 1'i değil
    n0 = led.counters["N_raw_cum"]
    s = L.seal_month(p, dfile, led, now=datetime(2027, 3, 2, 1, 37, tzinfo=UTC))
    assert s == {"sealed": True, "lib_version": "LIB_v1.2027-03", "specs": 20, "carried": 5}
    order = sorted(items, key=lambda d: (d["q"], -abs(d["delta"]), d["spec_sha"]))
    sealed = [x for x in dfile.rows if x["status"] == "SEALED"]
    assert [x["spec_sha"] for x in sealed] == [x["spec_sha"] for x in order[:20]]
    assert led.counters["N_raw_cum"] == n0 + 20
    specs = [r for r in TR.iter_rows(p.trials_file) if r["kind"] == "SPEC"]
    assert {r["lib_version"] for r in specs} == {"LIB_v1.2027-03"} and len(specs) == 20
    mf = json.loads(L.manifest_file(p, "2027-03").read_text())
    assert len(mf["specs"]) == 20 and mf["carried"] == 5 and mf["sha"] == RG.sha256_canonical(
        {k: v for k, v in mf.items() if k != "sha"}) and all(x["t_seal_ms"] == mf["t_seal_ms"] for x in mf["specs"])
    raw = L.manifest_file(p, "2027-03").read_bytes()
    assert not L.seal_month(p, dfile, led, now=datetime(2027, 3, 3, 1, 37, tzinfo=UTC))["sealed"]   # ay mühürlü
    # çökme benzetimi: manifest yazılmadan önce kesilmiş gibi → yeniden çalışma aylık tavanı korur, yeni deneme yok
    L.manifest_file(p, "2027-03").unlink()
    s = L.seal_month(p, dfile, led, now=datetime(2027, 3, 2, 1, 50, tzinfo=UTC))
    assert s["specs"] == 20 and led.counters["N_raw_cum"] == n0 + 20
    assert json.loads(L.manifest_file(p, "2027-03").read_text())["specs"] == json.loads(raw)["specs"]
    s = L.seal_month(p, dfile, led, now=datetime(2027, 4, 2, 1, 37, tzinfo=UTC))                   # taşanlar sonraki ay
    assert (s["specs"], s["carried"]) == (5, 0) and led.counters["N_raw_cum"] == n0 + 25


# ============================================================================ kabul: canlı kural dışlama
def _d4_world():
    rows = []
    rng = random.Random(21)
    for d in range(160):
        for j in range(3):
            r = rng.gauss(-0.1, 0.8)
            rows.append(mk(len(rows), D4, d, r, sym="BTC/USDT", hour=1 + j,
                           grid={"S_ATR200": 0.4 + rng.gauss(0, 0.3), "S_ATR150": 0.4 + rng.gauss(0, 0.3)}))
    return rows


def test_live_rule_check_matrix():
    def prop(t, base, arg):
        return {"transform": t, "base": base, "arg": arg}
    raw = json.loads(json.dumps(RAW))
    assert L.live_rule_check(prop("T2", "D4", 2.0), D4, raw)["status"] == "LIVE"          # kod sabiti 2 × ATR14
    assert L.live_rule_check(prop("T2", "D4", 1.5), D4, raw)["status"] == "NOT_LIVE"
    assert L.live_rule_check(prop("T2", "T2", 2.0), T2, raw)["status"] == "NOT_LIVE"      # canlı 3 × ATR
    raw["books"]["t2_trend_regime"]["atr_mult"] = 2.0
    assert L.live_rule_check(prop("T2", "T2", 2.0), T2, raw)["status"] == "LIVE"
    assert L.live_rule_check(prop("T2", "BOX", 2.0), BOX, raw)["status"] == "NOT_LIVE"    # Box'ta ATR stopu yok
    assert L.live_rule_check(prop("T3", "BOX", "X_T2R"), BOX, raw)["status"] == "NOT_LIVE"  # box_mid
    raw["books"]["b1_box_fade"]["rule_params"] = {"exit_kind": "r_multiple", "exit_r": 2.0}
    assert L.live_rule_check(prop("T3", "BOX", "X_T2R"), BOX, raw)["status"] == "LIVE"
    assert L.live_rule_check(prop("T3", "BOX", "X_T3R"), BOX, raw)["status"] == "NOT_LIVE"
    assert L.live_rule_check(prop("T1", "D4", "SOL"), D4, raw)["status"] == "LIVE"        # SOL D4 listesinde yok
    assert L.live_rule_check(prop("T1", "D4", "BTC"), D4, raw)["status"] == "NOT_LIVE"
    assert L.live_rule_check(prop("T1", "BOX", "BTC"), BOX, raw)["status"] == "NOT_LIVE"  # Box: giriş evreni
    assert L.live_rule_check(prop("T1", "BOX", "DOGE"), BOX, raw)["status"] == "LIVE"
    assert L.live_rule_check(prop("T4", "BOX", "UP|HIGH"), BOX, raw)["status"] == "NOT_LIVE"
    assert L.live_rule_check(prop("T1", "BOX", "BTC"), BOX, {"ok": False})["status"] == "UNAVAILABLE"
    assert L.live_rule_check(prop("T1", "BOX", "BTC"), BOX, None)["status"] == "UNAVAILABLE"


def test_live_rule_is_never_a_candidate_and_validation_waits_for_a_readable_config(tmp_path):
    p = _paths(tmp_path)
    rows = _d4_world()
    run(p, T_K, rows)
    s = run(p, FWD_DUE, rows, raw=None)                            # config okunamıyor → doğrulama ertelenir
    assert s["validation_deferred_no_config"] > 0 and not [r for r in looks(p) if r["params"]["kind"] == "VALIDATION"]
    run(p, FWD_DUE + timedelta(days=1), rows)
    d = L.ChainFile(p, L.derived_file(p), L.DERIVED_SCHEMA).load().rows
    k2 = [x for x in d if x["transform"]["T"] == "T2" and x["transform"]["arg"] == 2.0]
    k15 = [x for x in d if x["transform"]["T"] == "T2" and x["transform"]["arg"] == 1.5]
    assert k2 and all(x["status"] == "EXCLUDED_LIVE" and x["live"]["key"] == "donchian_trend.STOP_ATR" for x in k2)
    assert k15 and all(x["status"] in ("PROPOSED", "LINKED", "SEALED") for x in k15)   # 2 Mart mühür gecesi
    st = store(p).lessons.values()
    live_l = [x for x in st if x["pair_type"] == "GRID:S_ATR200" and x.get("fwd_validation") == "FWD_PASS"]
    assert live_l and all(x["policy_status"] == L.VALIDATED_POLICY_CANDIDATE for x in live_l)
    assert all(x["policy_status"] == L.RESEARCH_HYPOTHESIS for x in st if x["pair_type"] == "GRID:S_ATR150")
    # mühür gecesi: canlı kural hiçbir zaman SPEC denemesi olmaz
    run(p, datetime(2027, 4, 2, 1, 37, tzinfo=UTC), rows)
    specs = [r for r in TR.iter_rows(p.trials_file) if r["kind"] == "SPEC"]
    assert specs and not {r["spec_sha"] for r in specs} & {x["spec_sha"] for x in k2}


# ============================================================================ kabul: §5.10 dönem başına yeniden üretim
def _box_510_rows():
    """Eski rejim (min_stop 0,32) mühürlü dosyada iki döneme bölünmüş: dar kova 40 + 32 = 72 işlem ort. −0,56R, %0,5–1
    kovası 100 + 76 = 176 işlem ort. +0,17R; dar kovanın kayıplarında TIGHT_STOP_COST_MULTIPLIER baskın. Yeni rejim
    (min_stop 0,5) yalnız izlenir."""
    out = []

    def add(ep, at0, n, mean, stop, code):
        for i in range(n):
            r = mean + (0.3 if i % 2 else -0.3) * (1 if n % 2 == 0 or i < n - 1 else 0)
            out.append(mk(len(out), BOX, 0, r, ep=ep, stop=stop, at=at0 + timedelta(minutes=37 * i),
                          codes=[code] if r <= 0 else []))
    add("E2026-09-28T2120Z", datetime(2026, 9, 28, 22, tzinfo=UTC), 40, -0.56, 0.4, "TIGHT_STOP_COST_MULTIPLIER")
    add("E2026-09-29T2243Z", datetime(2026, 9, 30, 0, tzinfo=UTC), 32, -0.56, 0.35, "TIGHT_STOP_COST_MULTIPLIER")
    add("E2026-09-28T2120Z", datetime(2026, 9, 29, 0, tzinfo=UTC), 100, 0.17, 0.7, "WRONG_DIRECTION")
    add("E2026-09-29T2243Z", datetime(2026, 10, 1, 0, tzinfo=UTC), 76, 0.17, 0.8, "WRONG_DIRECTION")
    add("E2026-10-05T0455Z", datetime(2026, 10, 6, 0, tzinfo=UTC), 50, 0.05, 0.6, "WRONG_DIRECTION")
    return out


def test_sanity_5_10_reproduces_per_epoch_and_across_the_split_regime_without_a_candidate(tmp_path):
    reg = L.epoch_regimes("strategy_paper_box", S1C.LESSON_READINGS_V1["sanity_5_10"]["param"])
    assert reg["E2026-09-28T2120Z"] == reg["E2026-09-29T2243Z"] == "E2026-09-28T2120Z"     # slot değişikliği bölmez
    assert reg["E2026-10-05T0455Z"] == "E2026-10-05T0455Z"                                  # min_stop değişti
    assert "X2026-10-04T114556Z" not in reg                                                 # TRANSITION'a işlem yok
    rows = _box_510_rows()
    s = L.sanity_5_10(rows, raw=RAW)
    assert s["status"] == "REPRODUCED" and s["candidate"] is None
    assert s["regime_epochs"] == ["E2026-09-28T2120Z", "E2026-09-29T2243Z"]
    pr = s["per_regime"]["E2026-09-28T2120Z"]
    assert (pr["<0.5"]["n"], pr["<0.5"]["mean_r"], pr["0.5-1"]["n"], pr["0.5-1"]["mean_r"]) == (72, -0.56, 176, 0.17)
    assert next(iter(pr["<0.5"]["codes"])) == "TIGHT_STOP_COST_MULTIPLIER"
    pe = s["per_epoch"]                                                        # dönem başına ayrı (sınır aşılmaz)
    assert (pe["E2026-09-28T2120Z"]["<0.5"]["n"], pe["E2026-09-29T2243Z"]["<0.5"]["n"]) == (40, 32)
    assert (pe["E2026-09-28T2120Z"]["0.5-1"]["n"], pe["E2026-09-29T2243Z"]["0.5-1"]["n"]) == (100, 76)
    assert all(pe[e]["<0.5"]["mean_r"] < 0 < pe[e]["0.5-1"]["mean_r"] for e in ("E2026-09-28T2120Z", "E2026-09-29T2243Z"))
    assert s["live_rule"]["status"] == "APPLIED_DECISION_MONITOR" and s["monitor_regimes"] == ["E2026-10-05T0455Z"]
    assert L.sanity_5_10(rows, raw={**RAW, "books": {"b1_box_fade": {"min_stop_pct": 0.32}}})["live_rule"]["status"] \
        == "NOT_APPLIED"
    flipped = [{**r, "net_r": -r["net_r"]} if r["config_epoch"] != "E2026-10-05T0455Z" else r for r in rows]
    assert L.sanity_5_10(flipped, raw=RAW)["status"] == "NOT_REPRODUCED"
    # gece adımında: aday/deneme yok; ders hücreleri dönem sınırını aşmaz
    p = _paths(tmp_path)
    out = run(p, datetime(2026, 10, 20, 1, 37, tzinfo=UTC), rows)
    assert out["sanity_5_10"]["status"] == "REPRODUCED"
    assert not L.derived_file(p).exists() or not L.ChainFile(p, L.derived_file(p), L.DERIVED_SCHEMA).load().rows
    cells, _meta2 = L.build_cells(L.Evidence(rows, attribution_sha=VER[0], cfgrid_sha=VER[1]))
    ep_of = {r["trade_key"]: r["config_epoch"] for r in rows}
    ev = L.Evidence(rows, attribution_sha=VER[0], cfgrid_sha=VER[1])
    for cid, idx in cells.items():
        assert len({ep_of[ev.keys[i]] for i in idx}) == 1 and f"E={ep_of[ev.keys[int(idx[0])]]}" in cid


# ============================================================================ bütün defterler, kod görünümü, kanıt kuralları
def test_all_books_get_lessons_and_a_book_by_code_view(tmp_path):
    p = _paths(tmp_path)
    rows = _multi_book_rows()
    rows += [{**mk(77_000 + i, BOX, 3, -1.0), "attribution": {"registry_sha": "z" * 64}} for i in range(5)]   # eski sürüm
    rows += [{**mk(78_000 + i, BOX, 3, -1.0), "config_epoch": None} for i in range(4)]                       # TRANSITION
    s = run(p, night(14), rows)
    assert s["evidence"]["excluded"] == {"NO_CONFIG_EPOCH": 4, "STALE_VERSION": 5}
    assert set(s["evidence"]["books"]) == {BOX, T2, MAIN}
    assert {d["scope"]["book"] for d in store(p).lessons.values()} == {BOX, T2, MAIN}
    cv = s["code_view"]
    assert {k.split("|")[0] for k in cv} == {BOX, T2, MAIN}
    box = cv[f"{BOX}|E1"]
    wd = next(c for c in box["codes"] if c["code"] == "WRONG_DIRECTION")
    assert wd["mean_r"] < 0 and wd["best_ex_ante"][0] == "S_ATR150" and 0 < wd["loss_share"] <= 1
    assert all(d["mechanism_code"] in (None, "WRONG_DIRECTION") for d in store(p).lessons.values())


def test_rerun_same_night_is_idempotent_and_store_is_a_function_of_the_ledger(tmp_path):
    p = _paths(tmp_path)
    rows = _fwd_world(-0.5)
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    snap = {f: f.read_bytes() for f in (p.trials_file, L.store_file(p), L.derived_file(p))}
    s = run(p, FWD_DUE, rows)
    assert s["looks"] == 0 and {f: f.read_bytes() for f in snap} == snap
    # depo silinirse defterden aynı baytlarla yeniden kurulur
    L.store_file(p).unlink()
    run(p, FWD_DUE, rows)
    assert L.store_file(p).read_bytes() == snap[L.store_file(p)]


def test_s2_backlog_blocks_looks(tmp_path):
    p = _paths(tmp_path)
    assert L.run_lessons(p, now=night(20), raw=RAW, module_src_sha=MS, versions=VER)["status"] == "NO_ATTRIBUTION"
    p.attribution.mkdir(parents=True)
    (p.attribution / "_build.json").write_text(json.dumps({"pending": 3}))
    assert L.run_lessons(p, now=night(20), raw=RAW, module_src_sha=MS, versions=VER)["status"] == "S2_PENDING"
    assert not p.trials_file.exists()


def test_a_spent_s6_budget_defers_the_nights_looks_whole_and_writes_nothing(tmp_path):
    """P3g inceleme: S6 dersleri payla çalışır; pay yazımdan önceki güvenli bir noktada bitmişse o gecenin bakışları BÜTÜN
    olarak ertelenir (yarım bakış ailesi/BH yok, deftere satır yok) ve ertesi çalıştırma aynı bakışları yapar."""
    p = _paths(tmp_path)
    rows = noise_rows(BOX, range(12), 8, sd=1.0, seed=3)
    s = run(p, night(11), rows, deadline=0.0, clock=lambda: 1.0)
    assert s["status"] == "DEFERRED_BUDGET" and s["looks"] == 0 and not looks(p) and "S6 payı" in s["reason"]
    s2 = run(p, night(11), rows, deadline=10.0, clock=lambda: 1.0)
    assert s2["status"] == "OK" and s2["looks"] > 0 and len(looks(p)) == s2["looks"]


def test_broken_store_chain_refuses_to_append(tmp_path):
    p = _paths(tmp_path)
    rows = _fwd_world(-0.5)
    run(p, T_K, rows)
    raw = L.store_file(p).read_bytes()
    L.store_file(p).write_bytes(raw.replace(b'"OBSERVATION"', b'"OBSERVATIOM"', 1))
    with pytest.raises(L.LessonStoreBroken):
        run(p, FWD_DUE, rows)


# ============================================================================ engine-query lessons
def test_engine_query_lessons_is_bounded_read_only_and_wired(tmp_path, monkeypatch, capsys):
    from tradingbot.research_engine.query import run_query
    p = _paths(tmp_path)
    rows = _multi_book_rows() + _fwd_world(-0.5)
    run(p, T_K, rows)
    run(p, FWD_DUE, rows)
    before = {f: f.read_bytes() for f in p.research.rglob("*") if f.is_file()}
    lines, code = run_query(p, topic="lessons")
    assert code == 0 and len(lines) <= 150 and len("\n".join(lines).encode()) <= 4096
    txt = "\n".join(lines)
    assert "DERSLER" in txt and "Defter × kod" in txt and "§5.10" in txt and "Türeyen spec" in txt
    lines_b, _ = run_query(p, topic="lessons", book=BOX)
    assert all(MAIN not in ln for ln in lines_b if "×" not in ln)
    assert {f: f.read_bytes() for f in p.research.rglob("*") if f.is_file()} == before
    import tradingbot.cli as cli

    def no_config(*a, **k):
        raise AssertionError("engine-query config yüklemez")
    monkeypatch.setattr(cli, "load_config", no_config)
    monkeypatch.setenv("TRADINGBOT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(tmp_path / "data" / "state"))
    assert cli.main(["engine-query", "lessons", "--book", BOX]) == 0
    out = capsys.readouterr().out
    assert "DERSLER" in out and BOX in out
    assert {f: f.read_bytes() for f in p.research.rglob("*") if f.is_file()} == before

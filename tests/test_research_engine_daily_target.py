# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — günlük hedef `tgt_v1` (docs/SYSTEM_LEARNING_ENGINE_V1.md §7; P1a depo kabul testleri
5, 6, 7, 14, 17'nin bu modüllere düşen kısmı).

* 5: GEÇİCİ/KESİN, revizyon yalnız eklenir ve REVİZE görünür, hüküm yalnız bakış gecesinde, yalnız-gerçekleşmiş satırda
  `TUTTU`/`HEDEF GÜNÜ` metni hiç geçmez (özet başlığı, durum tablosu ve karnenin --daily çıktısı üzerinde), iki payda
  etiketli, "en iyi enstrüman" hüküm paydasıyla.
* 6: `KANITLANDI` özellik testi (sabit tohumlu rastgele satırlar; hypothesis gerekmez).
* 7: `daily_target` ile `scripts/bot_scorecard.py --daily` aynı sayıları verir (kayıt, cüzdan, gerçekleşmemiş; spot dahil).
* 14: `rawconfig` bilinmeyen anahtarla kırılmaz, sha kaydedilir.
* 17: başlık ve durum tablosu boyut sınırları.
"""
from __future__ import annotations

import hashlib
import importlib.util
import random
import sys
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeVps, at, night_of, trade  # noqa: E402

from tradingbot.accounting import AmountType, SizeSpec  # noqa: E402
from tradingbot.research_engine import closes as C  # noqa: E402
from tradingbot.research_engine import daily_target as T  # noqa: E402
from tradingbot.research_engine import rawconfig as RC  # noqa: E402
from tradingbot.research_engine.ledgers import add_days  # noqa: E402
from tradingbot.research_engine.paths import read_jsonl  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bot_scorecard_daily", ROOT / "scripts" / "bot_scorecard.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def _no_forbidden(lines):
    for ln in lines:
        for w in T.FORBIDDEN_ON_REALIZED:
            assert w not in ln, f"yasak kelime {w!r}: {ln}"


# ============================================================================ kabul 5: kesinleşme, bakış, metin
def test_rows_stay_provisional_three_days_then_final_and_look_only_on_look_night(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    v.night(night_of("2026-10-01"))
    for d in range(1, 9):
        day = f"2026-10-{d:02d}"
        trade(led, "ETH/USDT", at(day, 3), 100.0, 101.5)
        _r1, r3 = v.night(night_of(f"2026-10-{d + 1:02d}"))
        rows = v.rows()
        for dd, row in rows.items():
            run_day = f"2026-10-{d + 1:02d}"
            if run_day < add_days(dd, 3):
                assert row["status"] == "GEÇİCİ" and "3_GÜN_DOLMADI" in row["status_reason"], (dd, run_day)
            else:
                assert row["status"] == "KESİN", (dd, run_day, row["status_reason"])
            assert row["hit"] is None or row["status"] == "KESİN", "isabet yalnız KESİN satırda değerlendirilir"
        assert r3["look"] == (run_day == "2026-10-04"), "bakış: ayın 3. UTC gününden sonraki İLK gece, ayda bir"
    looks = list(read_jsonl(v.paths.target_looks))
    assert len(looks) == 1 and looks[0]["month"] == "2026-10" and looks[0]["look_day"] == "2026-10-04"
    assert looks[0]["scopes"]["total"]["verdict"] == T.V_THIN
    assert T.look_verdicts(v.rows(), look_day="2026-10-09", look_night=False)["scopes"] == {}, "bakış dışı gecede hüküm yok"
    assert T.is_look_night("2026-11-04", ["2026-10"]) and not T.is_look_night("2026-11-03", ["2026-10"])
    assert not T.is_look_night("2026-11-20", ["2026-10", "2026-11"])


def _two_book_state(tmp_path):
    """İki defter: ana bot (büyük bakiye) ve M2 (küçük bakiye). Enstrüman A=SOL yalnız küçük defterde büyük yüzdeyle,
    B=BTC yalnız büyük defterde daha büyük USDT ile — "en iyi enstrüman" hüküm paydasıyla (E_total) B olmalı."""
    v = FakeVps(tmp_path)
    big, small = v.fut(equity="10000"), v.fut("strategy_paper_m2", equity="100")
    v.night(night_of("2026-10-01"))
    trade(big, "BTC/USDT", at("2026-10-01", 3), 100.0, 104.0, notional="1500", lev=3)   # ≈ +60 USDT / 10 100
    trade(small, "SOL/USDT", at("2026-10-01", 3), 100.0, 106.0, notional="100", lev=2)  # ≈ +6 USDT / 100 → kendi defterine göre büyük
    v.night(night_of("2026-10-02"))
    return v


def test_best_instrument_uses_verdict_denominator_and_both_denominators_are_labelled(tmp_path):
    v = _two_book_state(tmp_path)
    row = v.rows()["2026-10-01"]
    ins = row["instruments"]
    assert ins["SOLUSDT"]["r_book_inst"]["strategy_paper_m2"] > ins["BTCUSDT"]["r_book_inst"]["main_fut"], "kurgu"
    assert ins["BTCUSDT"]["r_inst"] > ins["SOLUSDT"]["r_inst"]
    bi = row["best_instrument"]
    assert bi["inst"] == "BTCUSDT" and bi["denominator"] == T.DENOM_TOTAL and bi["book_denominator"] == T.DENOM_BOOK
    head = T.render_brief(v.rows(), None, {})
    txt = "\n".join(head)
    assert "payda: toplam sermaye" in txt and "kendi defterine göre" in txt and "en iyi enstrüman BTCUSDT" in txt
    assert row["chance"]["headline_null"] == T.CHANCE_HEADLINE and row["chance"]["secondary_null"] == T.CHANCE_SECONDARY
    assert 0.0 <= row["chance"]["headline"] <= 1.0 and 0.0 <= row["chance"]["secondary"] <= 1.0


def test_no_claim_words_on_realized_only_rows_in_header_table_and_scorecard(tmp_path, capsys):
    v = FakeVps(tmp_path)
    led = v.fut()
    sp = v.spot(cash="500")
    # P1a öncesi işlemler (yalnız-gerçekleşmiş, EKSİK satırlar) + çok kârlı günler
    for d in range(20, 25):
        trade(led, "ETH/USDT", at(f"2026-09-{d}", 3), 100.0, 130.0, notional="900", lev=5)
    sp.market_buy("ETH/USDT", qty=D("1"), ref_price=D("100"), now=at("2026-09-24", 5))
    v.night(night_of("2026-09-25"))
    v.night(night_of("2026-09-26"))      # W(09-25): spot ETH için mark yok → MTM EKSİK (yalnız gerçekleşmiş)
    rows = v.rows()
    assert any(r["status"] == "EKSİK" and "P1A_ÖNCESİ" in r["status_reason"] for r in rows.values())
    assert rows["2026-09-25"]["total"]["r_mtm"] is None and "MARK_EKSİK" in rows["2026-09-25"]["status_reason"]
    head = T.render_brief(rows, None, {})
    _no_forbidden(head)
    assert "MTM yok, hedefle karşılaştırılmaz" in head[0]
    _no_forbidden(T.render_table(rows))
    assert S.main(["--state", str(v.state), "--daily", "--days", "10", "--now", night_of("2026-09-26").isoformat()]) == 0
    out = capsys.readouterr().out
    assert "GÜNLÜK HEDEF" in out and "Ana bot · spot" in out
    _no_forbidden(out.splitlines())
    # GEÇİCİ MTM satırı: karşılaştırma var, hüküm kelimesi yok
    fut2 = v.fut("strategy_paper_m2")
    assert fut2.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("50"), AmountType.NOTIONAL, 1), stop=D("90"),
                     now=at("2026-09-26", 3)) is not None
    fut2.tick({"ETH/USDT": D("101")}, now_utc=at("2026-09-26", 20))
    for d in (27, 28, 29, 30):
        v.night(night_of(f"2026-09-{d}"))
    rows = v.rows()
    head = T.render_brief(rows, None, {})
    assert rows[max(rows)]["status"] == "GEÇİCİ" and "%1'e göre:" in head[2] and "kesinleşme" in head[2]
    _no_forbidden(head[:3])
    kes = [ln for ln in head if "HEDEF GÜNÜ" in ln]
    assert len(kes) == 1 and kes[0].startswith("Son kesin gün"), "HEDEF GÜNÜ yalnız KESİN MTM gününün satırında"
    assert all("TUTTU" not in ln for ln in head)


def test_revision_is_append_only_and_status_change_alone_is_not_revize(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    v.night(night_of("2026-10-01"))
    trade(led, "ETH/USDT", at("2026-10-01", 3), 100.0, 101.0)
    snaps = []
    for d in range(2, 7):
        v.night(night_of(f"2026-10-{d:02d}"))
        snaps.append(v.paths.target_daily.read_bytes())
    for a, b in zip(snaps, snaps[1:]):
        assert b.startswith(a)
    revs = [r for r in read_jsonl(v.paths.target_daily) if r["day"] == "2026-10-01"]
    assert [r["rev"] for r in revs] == [0, 1] and [r["status"] for r in revs] == ["GEÇİCİ", "KESİN"]
    assert revs[1]["revised"] is False and revs[1]["label"] == "KESİN", "yalnız durum değişti: REVİZE değil"


# ============================================================================ kabul 6: KANITLANDI özellik testi
def _synthetic_rows(rng: random.Random, n_days: int, *, strong: bool) -> dict[str, dict]:
    rows = {}
    start = "2026-01-01"
    insts = ["BTCUSDT", "ETHUSDT"]
    for i in range(n_days):
        d = add_days(start, i)
        st = rng.choice(["KESİN"] * 6 + ["GEÇİCİ", "EKSİK"])
        stream = T.STREAM_LIVE if rng.random() < 0.9 else rng.choice(T.STREAMS[1:])
        base = rng.gauss(3.0, 0.4) if strong else rng.gauss(rng.choice([0.0, 1.0, 1.5]), 1.5)
        real = base - (rng.random() * 0.3 if rng.random() < 0.8 else rng.random() * 4)
        mtm = None if rng.random() < 0.05 else round(base, 4)          # yalnız-gerçekleşmiş satır (KESİN dese bile)
        n_tr = rng.randint(0, 4)
        e = 1000.0
        rows[d] = {"day": d, "stream": stream, "status": st,
                   "total": {"r_mtm": mtm, "r_realized": round(real, 4), "n_trades": n_tr, "e_start": e},
                   "groups": {"main": {"status": st, "r_mtm": mtm, "r_realized": round(real, 4), "n_trades": n_tr}},
                   "instruments": {k: {"r_inst": None if mtm is None else round(mtm / 2, 4), "pnl_wal": real / 2 * 10,
                                       "n": n_tr // 2} for k in insts},
                   "gold": {"r_inst": None if mtm is None else 0.0, "r_realized": 0.0, "n": 0}}
    return rows


def _check_invariants(rows, res, look_night, stream):
    for sc, out in res["scopes"].items():
        if out["verdict"] != T.V_PROVEN:
            continue
        assert look_night and stream == T.STREAM_LIVE and out["eligible"]
        assert out["n_kesin"] >= T.MIN_KESIN_DAYS and out["n_trades"] >= T.MIN_TRADES
        assert out["p_holm"] <= T.ALPHA_LOOK and out["p_realized_holm"] <= T.ALPHA_LOOK
        assert out["max_day_share"] is not None and out["max_day_share"] <= 0.25


def test_kanitlandi_only_on_look_night_with_kesin_days_eligible_scope_live_stream_property():
    seen_proven = 0
    for case in range(40):
        rng = random.Random(1000 + case)
        rows = _synthetic_rows(rng, rng.randint(30, 75), strong=case % 3 == 0)
        look_day = add_days(max(rows), 1)
        look_night = rng.random() < 0.8
        stream = T.STREAM_LIVE if rng.random() < 0.85 else rng.choice(T.STREAMS[1:])
        declared = rng.choice([None, None, "BTCUSDT"])
        res = T.look_verdicts(rows, look_day=look_day, look_night=look_night, stream=stream, declared_instrument=declared, b=2000)
        if not look_night:
            assert res["scopes"] == {}
            continue
        _check_invariants(rows, res, look_night, stream)
        seen_proven += sum(1 for o in res["scopes"].values() if o["verdict"] == T.V_PROVEN)
        # KESİN olmayan günlerin değeri ne olursa olsun hüküm değişmez
        mutated = {d: ({**r, "total": {**r["total"], "r_mtm": 99.0}} if r["status"] != "KESİN" else r) for d, r in rows.items()}
        res2 = T.look_verdicts(mutated, look_day=look_day, look_night=True, stream=stream, declared_instrument=declared, b=2000)
        assert res2["scopes"]["total"]["verdict"] == res["scopes"]["total"]["verdict"]
        assert res2["scopes"]["total"]["mean_pct"] == res["scopes"]["total"]["mean_pct"]
    assert seen_proven > 0, "pozitif kontrol: güçlü seride KANITLANDI erişilebilir (test boş değil)"


def test_kanitlandi_negative_controls():
    rng = random.Random(5)
    rows = _synthetic_rows(rng, 70, strong=True)
    for r in rows.values():
        r["status"], r["stream"] = "KESİN", T.STREAM_LIVE
        r["total"]["r_mtm"] = r["total"]["r_mtm"] if r["total"]["r_mtm"] is not None else 3.0
        r["total"]["n_trades"] = 2
    look = add_days(max(rows), 1)
    ok = T.look_verdicts(rows, look_day=look, look_night=True, b=2000)
    assert ok["scopes"]["total"]["verdict"] == T.V_PROVEN
    # 40 KESİN günden az → asla
    few = {d: ({**r, "status": "GEÇİCİ"} if i % 2 else r) for i, (d, r) in enumerate(sorted(rows.items()))}
    assert T.look_verdicts(few, look_day=look, look_night=True, b=2000)["scopes"]["total"]["verdict"] == T.V_THIN
    # yalnız-gerçekleşmiş seri zayıfsa → asla (cüzdan görünümüyle de koşul sağlanmalı)
    weak = {d: {**r, "total": {**r["total"], "r_realized": -0.5}} for d, r in rows.items()}
    assert T.look_verdicts(weak, look_day=look, look_night=True, b=2000)["scopes"]["total"]["verdict"] == T.V_ONTRACK
    # canlı olmayan akış → asla
    for s in T.STREAMS[1:]:
        other = {d: {**r, "stream": s} for d, r in rows.items()}
        assert T.look_verdicts(other, look_day=look, look_night=True, stream=s, b=2000)["scopes"]["total"]["verdict"] != T.V_PROVEN
    # tek gün pencerenin %25'inden fazlası → asla
    spike = dict(rows)
    d0 = sorted(spike)[-5]
    spike[d0] = {**spike[d0], "total": {**spike[d0]["total"], "r_mtm": 1000.0, "r_realized": 1000.0}}
    assert T.look_verdicts(spike, look_day=look, look_night=True, b=2000)["scopes"]["total"]["verdict"] != T.V_PROVEN
    # bildirilmiş enstrüman varken diğer kapsamlar yalnız tanımlayıcıdır
    dec = T.look_verdicts(rows, look_day=look, look_night=True, declared_instrument="BTCUSDT", b=2000)
    assert not dec["scopes"]["group:main"]["eligible"] and dec["scopes"]["group:main"]["verdict"] != T.V_PROVEN
    assert dec["family_size"] == 2


def test_holm_wilson_and_block_bootstrap_basics():
    assert T.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})
    lo, hi = T.wilson(5, 10)
    assert lo < 0.5 < hi
    assert T.p_tgt([0.0] * 50, b=500) > 0.5 and T.p_tgt([5.0 + (i % 3) * 0.1 for i in range(50)], b=500) < 0.01
    assert T.boot_ci([1.0] * 5) is None, "blok uzunluğu kadar günle aralık verilmez"


# ============================================================================ kabul 7: scorecard --daily == daily_target
def test_scorecard_daily_equals_engine_views_spot_and_all_mark_sources(tmp_path):
    v = FakeVps(tmp_path)
    main, m2 = v.fut(), v.fut("strategy_paper_m2", equity="300")
    sp = v.spot(cash="3000")
    now = night_of("2026-10-04")
    for d in (1, 2, 3):
        day = f"2026-10-0{d}"
        trade(main, "ETH/USDT", at(day, 3), 100.0, 101.0 + d)
        trade(m2, "BTC/USDT", at(day, 5), 100.0, 98.0)
    sp.market_buy("ETH/USDT", qty=D("2"), ref_price=D("100"), now=at("2026-10-02", 4))
    sp.market_buy("BTC/USDT", qty=D("1"), ref_price=D("200"), now=at("2026-10-02", 4))
    sp.market_buy("SOL/USDT", qty=D("3"), ref_price=D("50"), now=at("2026-10-02", 4))
    sp.market_sell("ETH/USDT", qty=D("0.5"), ref_price=D("104"), now=at("2026-10-03", 9))
    # ETH → PERP_PROXY (açık vadeli pozisyon), BTC → position_path (≤ 60 dk), SOL → HistoryStore son kapanmış spot barı
    assert m2.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("50"), AmountType.NOTIONAL, 1), stop=D("90"),
                   now=at("2026-10-03", 10)) is not None
    m2.tick({"ETH/USDT": D("103")}, now_utc=at("2026-10-03", 23))
    (v.state / "position_path.jsonl").write_text(
        '{"schema_version": "position_path_v1", "symbol": "BTC/USDT", "ts": "%s", "mark": 207.5}\n'
        % (now - timedelta(minutes=20)).isoformat(), encoding="utf-8")
    from tradingbot.history.store import HistoryStore
    hs = HistoryStore(v.data / "market" / "history")
    t_ms = int((now - timedelta(hours=2)).timestamp() * 1000)
    hs.write("spot", "SOL/USDT", "1h", pd.DataFrame([{"timestamp": t_ms - 3_600_000 * k, "open": 50.0, "high": 52.0,
                                                     "low": 49.0, "close": 51.0 + k * 0.1, "volume": 1.0}
                                                    for k in range(3)]))
    v.save(now - timedelta(minutes=1))
    r1 = v.s1a(now)
    spst = r1["books"]["main_spot"]["state"]
    assert {s: m["source"] for s, m in spst["marks"].items()} == {
        "ETH/USDT": C.MARK_PERP_PROXY, "BTC/USDT": C.MARK_POSITION_PATH, "SOL/USDT": C.MARK_HISTORY_BAR}
    assert spst["marks"]["SOL/USDT"]["price"] == "51.0", "son KAPANMIŞ bar (en yeni zaman damgası)"
    eng = T.ledger_day_views(v.paths, days=5, now=now)
    sc = S.daily_report(v.state, days=5, now=now)
    assert set(eng["books"]) == set(sc["books"]) == {"main_fut", "main_spot", "strategy_paper_m2"}
    for b, eb in eng["books"].items():
        sb = sc["books"][b]
        assert eb["unrealized_now"] == sb["unrealized_now"], b
        for d in eng["days"]:
            assert eb["days"][d]["rec"] == sb["days"][d]["rec"], (b, d)
            assert eb["days"][d]["wal_ts"] == sb["days"][d]["wal_ts"], (b, d)
    assert eng["books"]["main_spot"]["unrealized_now"] is not None and eng["books"]["main_spot"]["days"]["2026-10-03"]["rec"]["n"] == 1
    # mark eksikse ikisi de "yok" der (ertesi gece; aynı UTC gününün ilk anlık görüntüsü ölçümdür, yeniden yazılmaz)
    sp.market_buy("XRP/USDT", qty=D("10"), ref_price=D("1"), now=at("2026-10-04", 2))
    nxt = night_of("2026-10-05")
    v.save(nxt - timedelta(minutes=1))
    v.s1a(nxt)
    eng = T.ledger_day_views(v.paths, days=2, now=nxt)
    sc = S.daily_report(v.state, days=2, now=nxt)
    assert eng["books"]["main_spot"]["unrealized_now"] is None and sc["books"]["main_spot"]["unrealized_now"] is None


def test_scorecard_d4_label_and_monthly_block_includes_spot(tmp_path, capsys):
    assert S.BOOKS["strategy_paper_trend4h"] == "D4 Donchian 4h"
    v = FakeVps(tmp_path)
    trade(v.fut("strategy_paper_trend4h"), "ETH/USDT", at("2026-10-02", 3), 100.0, 101.0)
    sp = v.spot(cash="400")
    sp.market_buy("ETH/USDT", qty=D("1"), ref_price=D("100"), now=at("2026-10-02", 4))
    sp.market_sell("ETH/USDT", qty=D("1"), ref_price=D("103"), now=at("2026-10-02", 6))
    v.save(at("2026-10-03"))
    mt = S.monthly_target(v.state, now=at("2026-10-03"))
    assert mt["books"]["main_spot"]["month"]["n"] == 1 and mt["books"]["main_spot"]["name"] == "Ana bot · spot"
    assert S.main(["--state", str(v.state), "--now", at("2026-10-03").isoformat()]) == 0
    out = capsys.readouterr().out
    assert "D4 Donchian 4h" in out and "Trend 4h" not in out and "Ana bot · spot" in out


# ============================================================================ kabul 14: rawconfig
def test_rawconfig_tolerates_unknown_keys_and_records_sha(tmp_path):
    src = (ROOT / "config.yaml").read_text(encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(src.replace("learning_mode:\n  enabled: true", "learning_mode:\n  yeni_anahtar_gelecek_surum: 3\n  enabled: true")
                   .replace("shared_experience:\n  enabled: true", "shared_experience:\n  bilinmeyen: x\n  enabled: true"),
                   encoding="utf-8")
    assert "yeni_anahtar_gelecek_surum" in cfg.read_text(encoding="utf-8") and "bilinmeyen: x" in cfg.read_text(encoding="utf-8")
    from tradingbot.config_v3 import load_v3
    import yaml
    with pytest.raises(Exception):
        load_v3(yaml.safe_load(cfg.read_text(encoding="utf-8")))       # neden ham okuma: tam yükleyici kırılır
    rc = RC.read_raw_config(cfg)
    assert rc.ok and rc.sha256 == hashlib.sha256(cfg.read_bytes()).hexdigest() and rc.size == len(cfg.read_bytes())
    assert rc.values["learning_mode.extra_entries"] == "record_selectivity"
    assert rc.values["risk.starting_equity_usdt"] == 100.0
    box = rc.books["b1_box_fade"]
    assert box["min_stop_pct"] == 0.5 and box["state_dir"] == "strategy_paper_box" and box["learning_enabled"] is True
    assert rc.book_by_state_dir("strategy_paper_trend4h")["name"] == "d4_donchian_20_10"
    assert rc.books["c4s_candle_variations_strict"]["learning_enabled"] is False
    assert rc.book_by_state_dir("")["name"] == "main"
    bad = tmp_path / "bad.yaml"
    bad.write_text("risk: [unclosed\n", encoding="utf-8")
    rb = RC.read_raw_config(bad)
    assert not rb.ok and rb.sha256 and rb.error
    assert not RC.read_raw_config(tmp_path / "yok.yaml").ok
    odd = tmp_path / "odd.yaml"
    odd.write_text("risk: 5\nlearning_mode: [1, 2]\nstrategy_paper: {extra: nope}\n", encoding="utf-8")
    ro = RC.read_raw_config(odd)
    assert ro.ok and ro.values["risk.starting_equity_usdt"] is None and ro.values["learning_mode.extra_entries"] is None
    assert set(RC.NEEDS) >= {"risk.starting_equity_usdt", "learning_mode.extra_entries", "learning_mode.books.*.enabled",
                             "learning_mode.books.*.min_stop_pct"}


# ============================================================================ kabul 17: boyut sınırları
def test_header_and_status_table_are_bounded(tmp_path):
    v = FakeVps(tmp_path)
    books = ["", "strategy_paper", "strategy_paper_m2", "strategy_paper_box", "pattern_trader", "strategy_paper_trend4h",
             "strategy_paper_candle4h", "strategy_paper_candle4h_strict"]
    v.night(night_of("2026-10-01"))
    for d in range(1, 11):
        for i, b in enumerate(books):
            trade(v.fut(b), ["ETH/USDT", "BTC/USDT", "SOL/USDT"][i % 3], at(f"2026-10-{d:02d}", 3 + i), 100.0, 100.5 + i * 0.1)
        v.night(night_of(f"2026-10-{d + 1:02d}"))
    rows = v.rows()
    head = T.render_brief(rows, None, {n: T.rolling_stats(rows, end_day=max(rows), n=n) for n in T.ROLLING_WINDOWS})
    assert len(head) <= 6 and len("\n".join(head).encode("utf-8")) <= 2048
    tab = T.render_table(rows, days=7)
    assert len(tab) <= 1 + 7 + 1 + 8 and len("\n".join(tab).encode("utf-8")) <= 4096
    summ = (v.paths.summary / "daily_target.json").read_bytes()
    assert len(summ) <= 256 * 1024
    roll = T.rolling_stats(rows, end_day=max(rows), n=30)
    assert roll["n_kesin"] >= 7 and roll["required_compounded_pct"] > roll["compounded_pct"]
    assert roll["hit_rate_wilson95"] is not None and roll["max_drawdown_pct"] <= 0

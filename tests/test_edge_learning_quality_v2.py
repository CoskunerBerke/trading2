"""EDGE & LEARNING QUALITY V2 — zorunlu testler.

Kapsam (görev bölüm 16, 30 madde):
olasılık semantiği, edge↔execution ayrımı, kayıpsız ders saklama, çıkış/seçicilik
challenger'ları, terfi kapıları, risk/emir izolasyonu, dashboard dayanıklılığı, E2E zinciri.

Bu dosya HİÇBİR eski testi gevşetmez; yalnız yeni sözleşmeleri sabitler.
"""
from __future__ import annotations

import gzip
import json
import math
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingbot.dashboard.app import create_app
from tradingbot.dashboard.config import DashboardConfig
from tradingbot.learn.edge_execution import (APPLIED_BOUNDED, COST_DOMINATED,
                                             DATA_INSUFFICIENT, ENTRY_QUALITY_CANDIDATE,
                                             EXIT_POLICY_CANDIDATE, HIGH_MFE_REVERSAL,
                                             LOW_MFE_STOP, NO_POLICY_CHANGE,
                                             NORMAL_PLANNED_LOSS, OBSERVATION,
                                             PARTIAL_PROFIT_THEN_BE, RESEARCH_HYPOTHESIS,
                                             VALIDATED_POLICY_CANDIDATE, capture_ratio,
                                             classify_edge_execution, excursions_r,
                                             promote_evidence_level)
from tradingbot.learn.lesson_store import (ALLOWED_TRANSITIONS, LessonStore, SCOPE_AGGREGATE,
                                           SCOPE_HOT, SCOPE_INDEXED, build_lesson, transition)
from tradingbot.learn.prob_semantics import (CalibrationBook, EVIDENCE_ADDED,
                                             FORBIDDEN_VERDICTS, HIGH_SURPRISE, INSUFFICIENT,
                                             LOW_SURPRISE, REAL, SHADOW, brier_contribution,
                                             log_loss_contribution,
                                             outcome_probability_evidence, probability_note_tr,
                                             surprise_bits, wilson_interval)
from tradingbot.learning import Learner
from tradingbot.quant.champion import (KEEP_CHAMPION, PROMOTE_CANDIDATE, PromotionGates,
                                       evaluate_challenger)
from tradingbot.quant.exit_challenger import (CHAMPION_POLICY, DEFAULT_CHALLENGERS,
                                              EARLY_PARTIAL_BE, ExitPolicy,
                                              assert_same_cost_model, compare_exit_policies,
                                              cost_model_key, simulate_exit)
from tradingbot.quant.selectivity import (CHAMPION, MIN_TRADES_ABS, SelectivityRule,
                                          evaluate_selected, fit_on_train, net_edge_r,
                                          select_candidate)

_SAFE = dict(leakage_passed=True, data_quality_passed=True, isolation_verified=True,
             same_cost_model=True, fold_consistency=0.8)


def _ch_metrics(**kw):
    base = {"n": 200, "insufficient_sample": False, "expectancy_r": 0.25,
            "max_drawdown_r": -2.0, "tail_loss_r_cvar5": -1.0, "payoff_ratio": 1.6,
            "win_rate": 0.5, "calibration": {"brier": 0.2, "n": 200, "state": "ok"},
            "bootstrap_ci_mean_r": {"state": "ok", "low": 0.1, "high": 0.4},
            "concentration": {"top_symbol_share": 0.2, "top_trade_share": 0.1}}
    base.update(kw)
    return base


def _cm_metrics(**kw):
    base = {"expectancy_r": 0.05, "max_drawdown_r": -3.0, "tail_loss_r_cvar5": -1.5}
    base.update(kw)
    return _ch_metrics(**base)


# ============================================================ 1–7 olasılık semantiği

def test_01_low_probability_win_is_not_model_error():
    """1 · p=0.29 + KAZANÇ → model otomatik «yanıldı» sayılmaz."""
    ev = outcome_probability_evidence(0.29, True, trade_id="T1")
    assert ev["single_outcome_verdict"] is False and ev["causal_claim"] is False
    blob = json.dumps(ev, ensure_ascii=False)
    for bad in FORBIDDEN_VERDICTS:
        assert bad not in blob
    assert HIGH_SURPRISE in ev["codes"]           # sürpriz, HATA değil
    assert "yanıldı" not in probability_note_tr(ev)
    assert "doğrulamaz" in probability_note_tr(ev)


def test_02_mid_probability_loss_is_not_model_success():
    """2 · p=0.45 + KAYIP → model otomatik «isabetli» sayılmaz."""
    ev = outcome_probability_evidence(0.45, False, trade_id="T2")
    blob = json.dumps(ev, ensure_ascii=False)
    for bad in FORBIDDEN_VERDICTS:
        assert bad not in blob
    assert LOW_SURPRISE in ev["codes"]            # 0.55 gerçekleşti — sürpriz düşük
    assert "isabetli" not in probability_note_tr(ev)
    # ve hiçbiri diğerinden "daha doğru" ilan edilmez: ikisi de yalnız katkıdır
    assert ev["brier_contribution"] == pytest.approx(0.45 ** 2)


def test_03_brier_and_log_loss_are_numerically_correct():
    """3 · Brier ve log-loss sayısal doğruluk."""
    assert brier_contribution(0.29, 1.0) == pytest.approx(0.5041)
    assert brier_contribution(0.45, 0.0) == pytest.approx(0.2025)
    assert log_loss_contribution(0.29, 1.0) == pytest.approx(-math.log(0.29))
    assert log_loss_contribution(0.45, 0.0) == pytest.approx(-math.log(0.55))
    assert surprise_bits(0.25, 1.0) == pytest.approx(2.0)
    assert surprise_bits(0.5, 0.0) == pytest.approx(1.0)
    lo, hi = wilson_interval(5, 10)
    assert 0.0 < lo < 0.5 < hi < 1.0               # simetrik olmayan ama kapsayan aralık


def test_04_calibration_bucket_is_no_lookahead():
    """4 · Karar anından SONRA etiketlenen sonuç kalibrasyona GİREMEZ."""
    b = CalibrationBook()
    as_of = 1_000_000
    assert b.add(trade_id="past", p=0.3, won=True, label_ts=as_of - 1, as_of_ms=as_of) is True
    assert b.add(trade_id="future", p=0.3, won=True, label_ts=as_of + 1, as_of_ms=as_of) is False
    assert b.add(trade_id="unknown_ts", p=0.3, won=True, label_ts=None, as_of_ms=as_of) is False
    assert b.rejected_future == 2
    assert b.bucket_stats(0.3)["real_n"] == 1


def test_05_duplicate_outcome_counted_once():
    """5 · Aynı sonuç iki kez sayılmaz (gölge kopyası da gerçek olanı ikizlemez)."""
    b = CalibrationBook()
    assert b.add(trade_id="T", p=0.6, won=True) is True
    assert b.add(trade_id="T", p=0.6, won=True) is False
    assert b.add(trade_id="T", p=0.6, won=True, source=SHADOW) is False
    assert b.rejected_duplicate == 2
    assert b.bucket_stats(0.6)["real_n"] == 1


def test_06_real_paper_outweighs_shadow():
    """6 · Gerçek PAPER sonucu gölgeden AĞIRDIR ve gölge tek başına kova yeterli yapamaz."""
    b = CalibrationBook(min_bucket_sample=3, shadow_weight=0.25)
    for i in range(10):
        b.add(trade_id=f"S{i}", p=0.6, won=True, source=SHADOW)
    st = b.bucket_stats(0.6)
    assert st["real_n"] == 0 and st["sufficient"] is False and st["state"] == INSUFFICIENT
    assert st["n"] == pytest.approx(2.5)          # 10 × 0.25 — gerçek fille eşit DEĞİL
    with pytest.raises(ValueError):
        CalibrationBook(shadow_weight=1.0)        # gölge gerçeğe eşit sayılamaz
    for i in range(3):
        b.add(trade_id=f"R{i}", p=0.6, won=True, source=REAL)
    assert b.bucket_stats(0.6)["sufficient"] is True
    assert EVIDENCE_ADDED in outcome_probability_evidence(0.6, True, book=b)["codes"]


def test_07_single_outcome_cannot_become_policy_candidate():
    """7 · Tek sonuç `OBSERVATION`ı AŞAMAZ; atlamalı terfi de yok."""
    assert promote_evidence_level(OBSERVATION, n_supporting=1, oos_validated=True,
                                  applied_bounded=True) == OBSERVATION
    assert promote_evidence_level(OBSERVATION, n_supporting=20) == RESEARCH_HYPOTHESIS
    # OOS doğrulaması olmadan VALIDATED olamaz
    assert promote_evidence_level(RESEARCH_HYPOTHESIS, n_supporting=20) == RESEARCH_HYPOTHESIS
    assert promote_evidence_level(RESEARCH_HYPOTHESIS, n_supporting=20,
                                  oos_validated=True) == VALIDATED_POLICY_CANDIDATE
    assert promote_evidence_level(VALIDATED_POLICY_CANDIDATE, n_supporting=20, oos_validated=True,
                                  applied_bounded=True) == APPLIED_BOUNDED
    # ders yaşam döngüsü de atlamayı REDDEDER
    les = build_lesson(source_trade_id="T", symbol="ETH/USDT", direction="LONG",
                       setup="breakout", regime="trend_up")
    with pytest.raises(ValueError):
        transition(les, VALIDATED_POLICY_CANDIDATE)
    assert VALIDATED_POLICY_CANDIDATE not in ALLOWED_TRANSITIONS[OBSERVATION]


# ============================================================ 8–12 edge ↔ execution

def _trade(**kw):
    base = {"id": "T", "symbol": "ETH/USDT", "entry_price": 100.0, "initial_stop": 98.0,
            "direction": "LONG", "exit_reason": "stop", "r_multiple": -1.0,
            "mfe_pct": 0.2, "mae_pct": -2.0, "bars_held": 6}
    base.update(kw)
    return base


def test_08_low_mfe_stop_is_an_entry_quality_question():
    """8 · Neredeyse hiç lehe gitmeden stop → sinyal (edge) sorusu."""
    c = classify_edge_execution(_trade(mfe_pct=0.2))
    assert LOW_MFE_STOP in c["observation_codes"]
    assert HIGH_MFE_REVERSAL not in c["observation_codes"]
    assert ENTRY_QUALITY_CANDIDATE in c["hypothesis_codes"]
    assert EXIT_POLICY_CANDIDATE not in c["hypothesis_codes"]
    assert c["mfe_r"] == pytest.approx(0.1)       # %0.2 / %2 stop = 0.1R
    assert c["evidence_level"] == OBSERVATION and c["causal_claim"] is False


def test_09_high_mfe_reversal_is_an_exit_policy_question():
    """9 · Önce güçlü lehe gidip sonra stop → çıkış (execution) sorusu."""
    c = classify_edge_execution(_trade(mfe_pct=3.0, mae_pct=-2.0))
    assert HIGH_MFE_REVERSAL in c["observation_codes"]
    assert LOW_MFE_STOP not in c["observation_codes"]
    assert EXIT_POLICY_CANDIDATE in c["hypothesis_codes"]
    assert ENTRY_QUALITY_CANDIDATE not in c["hypothesis_codes"]
    assert c["mfe_r"] == pytest.approx(1.5)


def test_10_cost_dominated_is_separated_from_signal_error():
    """10 · Maliyet baskın kayıp, sinyal hatasıyla KARIŞTIRILMAZ."""
    c = classify_edge_execution(_trade(mfe_pct=3.0, r_multiple=-0.2, fee_drag_r=0.2,
                                       funding_drag_r=0.25, slippage_drag_r=0.05))
    assert COST_DOMINATED in c["observation_codes"]
    assert "COST_FILTER_CANDIDATE" in c["hypothesis_codes"]
    assert c["cost_drag_total_r"] == pytest.approx(0.5)
    # eksik maliyet alanı iyimser SIFIRA dönmez
    c2 = classify_edge_execution(_trade(mfe_pct=3.0))
    assert c2["cost_drag_total_r"] is None and COST_DOMINATED not in c2["observation_codes"]
    # stop mesafesi bilinmiyorsa R hesabı yapılmaz, veri yetersiz işaretlenir
    c3 = classify_edge_execution({"id": "X", "symbol": "A", "direction": "LONG",
                                  "exit_reason": "stop", "mfe_pct": 3.0})
    assert c3["mfe_r"] is None and DATA_INSUFFICIENT in c3["observation_codes"]


def test_11_normal_planned_loss_produces_no_policy_change():
    """11 · Plana uygun normal kayıp gereksiz politika üretmez."""
    c = classify_edge_execution(_trade(mfe_pct=1.2, mae_pct=-2.0, r_multiple=-1.0))
    assert NORMAL_PLANNED_LOSS in c["observation_codes"]
    assert c["hypothesis_codes"] == [NO_POLICY_CHANGE]


def test_12_partial_tp_then_breakeven_capture_ratio():
    """12 · Kısmi TP + başa-baş kapanışta capture ratio tanımı ve sınır durumları."""
    rec = _trade(exit_reason="başa-baş stop", tp1_done=True, r_multiple=0.76,
                 mfe_pct=4.0, mae_pct=-0.5)
    c = classify_edge_execution(rec, labels={"exit_quality": "TP1_THEN_BE"})
    assert PARTIAL_PROFIT_THEN_BE in c["observation_codes"]
    assert c["mfe_r"] == pytest.approx(2.0)
    # capture = gerçekleşen (kısmi çıkışların ağırlıklı neti) / erişilebilir en iyi R
    assert c["capture_ratio"] == pytest.approx(0.38)
    assert c["capture_ratio_state"] == "OK"
    # MFE ≤ 0 iken oran TANIMSIZDIR — sıfır DEĞİL (iyimser okuma yok)
    assert capture_ratio(-1.0, 0.0) is None
    # Çok küçük MFE'ye bölmek anlamsız katsayı üretir — değer raporlanır ama GÜRÜLTÜLÜ işaretlenir
    noisy = classify_edge_execution(_trade(mfe_pct=0.1, mae_pct=-2.0, r_multiple=-1.0))
    assert noisy["capture_ratio"] is not None
    assert noisy["capture_ratio_state"] == "NOISY_NEGLIGIBLE_EXCURSION"
    assert capture_ratio(None, 2.0) is None
    assert excursions_r({"entry_price": 100.0, "initial_stop": 100.0})["mfe_r"] is None


# ============================================================ 13–16 challenger disiplini

def _bars(seq):
    return [{"high": h, "low": lo, "close": c} for h, lo, c in seq]


def _path_trade(seq, **kw):
    base = {"id": "P", "symbol": "ETH/USDT", "entry_price": 100.0, "initial_stop": 98.0,
            "direction": "LONG", "targets": [104.0, 108.0], "price_path": _bars(seq)}
    base.update(kw)
    return base


def test_13_exit_challenger_uses_same_entry_and_cost_model():
    """13 · Challenger champion ile AYNI giriş, AYNI barlar, AYNI maliyet modelini kullanır."""
    t = _path_trade([(101.5, 99.5, 101.0), (103.0, 100.5, 102.5), (103.5, 97.0, 97.5)])
    rep = compare_exit_policies([t], cost_per_fill_r=0.02)
    assert rep["cost_model_key"] == cost_model_key(cost_per_fill_r=0.02)
    assert rep["active_policy_changed"] is False
    # her politika aynı giriş/stop/bar dizisinden aynı MFE'yi görür
    mfes = {rep["champion"]["policy"]: None}
    champ = simulate_exit(t, CHAMPION_POLICY, cost_per_fill_r=0.02)
    for p in DEFAULT_CHALLENGERS:
        sim = simulate_exit(t, p, cost_per_fill_r=0.02)
        assert sim["mfe_r"] == pytest.approx(champ["mfe_r"])
    assert mfes  # (yalnız yapı kontrolü)
    other = compare_exit_policies([t], cost_per_fill_r=0.05)
    with pytest.raises(ValueError):
        assert_same_cost_model(rep, other)        # farklı maliyet → karşılaştırma reddedilir


def test_14_worse_execution_can_never_improve_the_result():
    """14 · Maliyet arttıkça hiçbir politikanın net sonucu İYİLEŞEMEZ (monotonluk)."""
    t = _path_trade([(102.0, 99.0, 101.5), (105.0, 101.0, 104.5), (109.0, 103.0, 108.5)])
    for pol in (CHAMPION_POLICY, *DEFAULT_CHALLENGERS):
        cheap = simulate_exit(t, pol, cost_per_fill_r=0.0)
        dear = simulate_exit(t, pol, cost_per_fill_r=0.10)
        assert dear["net_r"] <= cheap["net_r"] + 1e-9, pol.name
        assert dear["gross_r"] == pytest.approx(cheap["gross_r"])   # brüt yol DEĞİŞMEZ


def _rows(n, *, start=0, expectancy=0.1, warn_hi=False):
    out = []
    for i in range(start, start + n):
        good = i % 3 != 0
        out.append({"id": f"R{i}", "symbol": f"S{i % 8}/USDT",
                    "r_multiple": (1.2 if good else -1.0) + expectancy,
                    "net_pnl": (12.0 if good else -10.0),
                    "p_win": 0.62 if good else 0.35, "rr": 2.5,
                    "conviction": 0.7 if good else 0.2,
                    "n_warnings": (7 if (warn_hi and not good) else 1),
                    "similar_expectancy_r": 0.2 if good else -0.3,
                    "outcome_class": "WIN" if good else "LOSS"})
    return out


def test_15_test_rows_cannot_change_challenger_selection():
    """15 · Test verisi seçimi DEĞİŞTİREMEZ — seçim imzası test satırı kabul etmez."""
    train, valid = _rows(60, warn_hi=True), _rows(60, start=100, warn_hi=True)
    sel = select_candidate(train, valid)
    assert sel["saw_test_rows"] is False and sel["saw_holdout_rows"] is False
    # test setini nasıl değiştirirsek değiştirelim seçim AYNI kalır
    for flip in (True, False):
        test_rows = _rows(60, start=200, expectancy=(2.0 if flip else -2.0))
        out = evaluate_selected(sel, test_rows)
        assert out["evaluated_rule"] == sel["selected"]
        assert out["selection_unchanged"] is True
    assert select_candidate(train, valid)["selected"] == sel["selected"]   # deterministik


def test_16_holdout_cannot_change_pre_holdout_result():
    """16 · Holdout hiçbir seçime girmez ve önceki sonucu DEĞİŞTİRMEZ."""
    train, valid, test = _rows(60), _rows(60, start=100), _rows(60, start=200)
    sel = select_candidate(train, valid)
    without = evaluate_selected(sel, test)
    with_ho = evaluate_selected(sel, test, holdout_rows=_rows(60, start=300, expectancy=5.0))
    assert with_ho["holdout_entered_selection"] is False
    assert with_ho["evaluated_rule"] == without["evaluated_rule"]
    assert with_ho["selected_test_metrics"] == without["selected_test_metrics"]
    assert with_ho["champion_test_metrics"] == without["champion_test_metrics"]


# ============================================================ 17–19 terfi kapıları

def test_17_high_win_rate_low_payoff_cannot_be_promoted():
    """17 · %75 kazanma + 0.40R kazanç / −1.00R kayıp → beklenti +0.05R: terfi EDEMEZ."""
    ch = _ch_metrics(win_rate=0.75, payoff_ratio=0.4, expectancy_r=0.05,
                     avg_win_r=0.40, avg_loss_r=-1.00)
    out = evaluate_challenger(_cm_metrics(expectancy_r=-0.05), ch, **_SAFE)
    assert out["decision"] == KEEP_CHAMPION
    assert any(c["code"] == "PAYOFF_RATIO" and not c["passed"] for c in out["checks"])
    # buna karşılık %50 kazanma + 1.50R / −1.00R → beklenti +0.25R kapıyı geçer
    ok = _ch_metrics(win_rate=0.5, payoff_ratio=1.5, expectancy_r=0.25)
    assert evaluate_challenger(_cm_metrics(), ok, **_SAFE)["decision"] == PROMOTE_CANDIDATE


def test_18_positive_expectancy_with_bad_tail_cannot_be_promoted():
    """18 · Pozitif beklenti ama kötü tail → terfi EDEMEZ (mutlak taban da var)."""
    bad_tail = _ch_metrics(expectancy_r=0.4, tail_loss_r_cvar5=-6.0)
    out = evaluate_challenger(_cm_metrics(tail_loss_r_cvar5=None), bad_tail, **_SAFE)
    assert out["decision"] == KEEP_CHAMPION
    codes = {c["code"]: c["passed"] for c in out["checks"]}
    assert codes["TAIL_LOSS_ABSOLUTE"] is False


def test_19_single_symbol_profit_concentration_cannot_be_promoted():
    """19 · Kâr tek sembolden geliyorsa terfi EDEMEZ."""
    conc = _ch_metrics(concentration={"top_symbol_share": 0.92, "top_trade_share": 0.1})
    out = evaluate_challenger(_cm_metrics(), conc, **_SAFE)
    assert out["decision"] == KEEP_CHAMPION
    assert any(c["code"] == "SYMBOL_CONCENTRATION" and not c["passed"] for c in out["checks"])
    assert PromotionGates().min_payoff_ratio >= 1.0      # eşikler yapılandırılabilir ama muhafazakâr


# ============================================================ 20–22 kayıpsız saklama

def _lesson(i: int, sym: str = "ETH/USDT"):
    return build_lesson(source_trade_id=f"T{i}", symbol=sym, direction="LONG",
                        setup="breakout", regime="trend_up",
                        observation={"observation_codes": [LOW_MFE_STOP], "realized_r": -1.0},
                        hypothesis=[ENTRY_QUALITY_CANDIDATE]) | {"r": -1.0, "won": False,
                                                                 "why": [f"ders {i}"]}


def test_20_old_lesson_survives_the_200_window(tmp_path: Path):
    """20 · 200 sınırı sonrasında eski ders hâlâ arşivde ve retrieval'da."""
    store = LessonStore(tmp_path / "lesson_archive", hot_window=200)
    hot = [_lesson(i) for i in range(260)]
    res = store.rotate(hot)
    assert res["archived"] == 60 and res["error"] is None
    hot = res["hot"]
    assert len(hot) == 200
    assert all(l["source_trade_id"] != "T0" for l in hot)          # sıcak pencerede yok
    archived_ids = [r["source_trade_id"] for r in store.archive.iter_rows()]
    assert "T0" in archived_ids and len(archived_ids) == 60        # ama arşivde TAM METİNLE var
    first = next(r for r in store.archive.iter_rows() if r["source_trade_id"] == "T0")
    assert first["why"] == ["ders 0"] and first["observation"]["observation_codes"] == [LOW_MFE_STOP]
    q = store.query(symbol="ETH/USDT", direction="LONG", setup="breakout", regime="trend_up",
                    hot=[], k=5)
    assert q["indexed"] and SCOPE_INDEXED in q["retrieval_scope"]
    assert q["aggregate"]["n"] == 60 and SCOPE_AGGREGATE in q["retrieval_scope"]
    st = store.stats(hot_count=len(hot))
    assert st["lifetime_lessons"] == 260 and st["deletes_detail_on_overflow"] is False
    assert st["retrieval_scopes"] == [SCOPE_HOT, SCOPE_INDEXED, SCOPE_AGGREGATE]


def test_21_corrupt_archive_or_index_is_fail_closed(tmp_path: Path):
    """21 · Bozuk arşiv/indeks → SESSİZ KAYIP YOK; budama yapılmaz, bozukluk raporlanır."""
    store = LessonStore(tmp_path / "arc", hot_window=5, min_rotate_block=1)
    hot = store.rotate([_lesson(i) for i in range(9)])["hot"]
    assert len(hot) == 5
    seg = store.archive.segments()[0]
    path = store.archive.segments_dir / seg["file"]
    path.write_bytes(gzip.compress(b'{"broken":'))                 # checksum artık tutmaz
    ver = store.verify()
    assert ver["corrupt"] and ver["ok"] == []
    assert list(store.archive.iter_rows()) == []                   # bozuk segment öğrenmeye GİRMEZ
    # arşiv yazımı imkânsız hale gelirse sıcak liste BUDANMAZ (arşivsiz silme yasak)
    broken = LessonStore(tmp_path / "arc2", hot_window=2, min_rotate_block=1)
    broken.archive.seal = lambda lines: (_ for _ in ()).throw(OSError("disk dolu"))
    rows = [_lesson(i) for i in range(10)]
    res = broken.rotate(rows)
    assert res["archived"] == 0 and res["error"] and len(res["hot"]) == 10
    # bozuk indeks dosyası → boş indeks (çökme yok), arşiv birincil kayıt olarak durur
    store.index_path.write_text("{ not json", encoding="utf-8")
    assert store.index()["aggregate"] == {}
    assert store.rebuild_index()["n_indexed"] == 0                 # bozuk segment sayılmaz


def test_22_retrieval_does_not_scan_the_whole_archive(tmp_path: Path):
    """22 · Aday başına O(toplam arşiv) tarama YASAK — okunan segment sayısı sınırlı."""
    store = LessonStore(tmp_path / "arc", hot_window=1, max_segments_scanned=2, min_rotate_block=1)
    hot: list = []
    for batch in range(12):                                        # 12 ayrı segment
        hot += [_lesson(batch * 10 + i, sym="ETH/USDT") for i in range(10)]
        hot = store.rotate(hot)["hot"]
    assert len(store.archive.segments()) == 12
    reads: list[str] = []
    orig = store.archive.read_segment
    store.archive.read_segment = lambda seg: (reads.append(str(seg.get("segment_id"))) or orig(seg))
    q = store.query(symbol="ETH/USDT", direction="LONG", setup="breakout", regime="trend_up",
                    hot=[], k=5)
    assert len(reads) <= 2 and q["segments_scanned"] <= 2
    assert q["scanned_whole_archive"] is False
    assert q["aggregate"]["n"] == 119        # 120 ders − 1 sıcak; toplam O(1) okunur


# ============================================================ 23–25 risk/emir izolasyonu

def test_23_paper_bounded_delta_stays_within_the_existing_cap():
    """23 · PAPER_BOUNDED etkisi mevcut `max_fraction` sınırını AŞAMAZ."""
    from tradingbot.learn.influence import InfluenceConfig, apply_influence, combine_components
    cfg = InfluenceConfig(mode="PAPER_BOUNDED")
    assert cfg.max_fraction == 0.05                                # sınır BÜYÜTÜLMEDİ
    out = apply_influence({"baseline": 0.50, "learned": 0.95}, cfg=cfg, mode_value="PAPER")
    assert out["applied"] is True and out["effective"] == pytest.approx(0.525)
    comb = combine_components(raw_model_p=0.5, hierarchical_p=0.5,
                              adjustment={"fraction": 5.0}, cfg=cfg)
    assert comb["final"] == pytest.approx(0.525)
    with pytest.raises(ValueError):
        InfluenceConfig(mode="PAPER_BOUNDED", max_fraction=0.5).validate()


def test_24_learning_cannot_emit_leverage_size_stop_tp_or_orders(tmp_path: Path):
    """24 · Öğrenme çıktısı kaldıraç/boyut/stop/TP/emir ÜRETEMEZ."""
    lr = Learner(tmp_path / "learning.json", min_trades=2)
    lesson = lr.learn({"id": "T1", "symbol": "ETH/USDT", "side": "LONG", "pnl": -10.0,
                       "r_multiple": -1.0, "exit_reason": "stop", "mae_pct": -2.0,
                       "mfe_pct": 0.2, "bars_held": 3, "closed_at": "2026-08-25T10:00:00+00:00",
                       "entry_price": 100.0, "initial_stop": 98.0,
                       "features": {"setup_type": "kırılım", "rr": 2.5, "p_win": 0.4}})
    forbidden = ("leverage", "qty", "quantity", "size", "notional", "stop_price", "take_profit",
                 "order", "client_order_id", "outbox")
    for key in forbidden:
        assert key not in lesson, key
    blob = json.dumps(lesson, ensure_ascii=False, default=str)
    for bad in FORBIDDEN_VERDICTS:
        assert bad not in blob
    # üretilen hipotezler yalnız ARAŞTIRMA seviyesindedir
    assert all(h["evidence_level"] == OBSERVATION for h in lesson["hypotheses"])
    assert lesson["policy_status"] == OBSERVATION and lesson["causal_claim"] is False


def test_25_active_risk_engine_result_is_unchanged_by_learning():
    """25 · Aktif RiskEngine sonucu öğrenme/challenger'lardan ETKİLENMEZ."""
    from tradingbot.risk import PROFILES, RiskEngine, build_state
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    plan = {"symbol": "ETH/USDT", "direction": "LONG", "entry": 100.0, "stop": 98.0,
            "market_type": "USDM_PERP", "leverage": 3}
    state = build_state(equity=200.0, starting_equity=200.0, available=150.0,
                        used_margin=0.0, positions=[], history=[])
    before = eng.evaluate(plan, state).to_dict()
    rep = compare_exit_policies([_path_trade([(103.0, 99.0, 102.0), (109.0, 101.0, 108.0)])],
                                cost_per_fill_r=0.02)
    sel = select_candidate(_rows(60), _rows(60, start=100))
    evaluate_selected(sel, _rows(60, start=200))
    after = eng.evaluate(plan, state).to_dict()
    assert after == before
    assert rep["active_policy_changed"] is False and sel["selected"] is not None


# ============================================================ 26–27 dashboard dayanıklılığı

def _state(tmp_path: Path, learning: dict) -> Path:
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    (d / "learning.json").write_text(json.dumps(learning, ensure_ascii=False, default=str),
                                     encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}),
                                 encoding="utf-8")
    return d


@pytest.mark.parametrize("ln", [
    {},                                                                    # boş
    {"lessons": [{"id": "L", "won": True, "r": 0.5}]},                      # ESKİ şema
    {"n_trades": 5, "n_wins": 2, "sum_r": float("nan"), "lessons": []},     # non-finite
    {"n_trades": 5, "lesson_retention": None, "calibration": None},         # null bloklar
    {"n_trades": 5, "lesson_retention": "bozuk", "calibration": [1, 2]},    # yanlış tip
    {"n_trades": 5, "calibration": {"buckets": [{"bucket": None, "real_n": None}]}},
    {"n_trades": 5, "quality_metrics": {"payoff_ratio": float("inf"), "avg_loss_r": 0}},
    {"n_trades": 5, "lessons": [{"id": "L", "observation": "yanlış tip", "hypotheses": 3}]},
])
def test_26_dashboard_never_500s_on_old_or_broken_schema(tmp_path: Path, ln):
    """26 · Eski/bozuk/stale/null/non-finite şemada `/learning` 500 ÜRETMEZ."""
    c = TestClient(create_app(_state(tmp_path, ln), tmp_path / "market", None, DashboardConfig()))
    for path in ("/learning", "/quant"):
        assert c.get(path).status_code == 200, (path, ln)


def test_27_mutating_http_methods_return_405(tmp_path: Path):
    """27 · POST/PUT/PATCH/DELETE → 405 (salt okunur panel)."""
    c = TestClient(create_app(_state(tmp_path, {"n_trades": 3}), tmp_path / "market", None,
                              DashboardConfig()))
    for path in ("/learning", "/quant"):
        for verb in ("post", "put", "patch", "delete"):
            assert getattr(c, verb)(path).status_code == 405, (path, verb)


# ============================================================ 28–30 E2E zinciri

def test_28_end_to_end_with_production_schema(tmp_path: Path):
    """28 · Gerçek üretim şemasıyla kapanış → ders → arşiv → dashboard zinciri."""
    store = LessonStore(tmp_path / "lesson_archive", hot_window=3, min_rotate_block=1)
    lr = Learner(tmp_path / "learning.json", min_trades=2, lesson_store=store, hot_window=3)
    for i in range(6):
        lr.learn({"id": f"T{i}", "symbol": "ETH/USDT", "side": "LONG", "pnl": -10.0,
                  "r_multiple": -1.0, "exit_reason": "stop", "mae_pct": -2.0, "mfe_pct": 3.0,
                  "bars_held": 5, "closed_at": f"2026-08-2{i}T10:00:00+00:00",
                  "entry_price": 100.0, "initial_stop": 98.0,
                  "features": {"setup_type": "kırılım", "rr": 2.5, "p_win": 0.42,
                               "regime": "trend_up"}})
    doc = json.loads((tmp_path / "learning.json").read_text(encoding="utf-8"))
    assert len(doc["lessons"]) == 3                      # sıcak pencere
    assert doc["n_lessons_lifetime"] == 6                # ömür boyu sayaç
    ret = doc["lesson_retention"]
    assert ret["archived_lessons"] == 3 and ret["lifetime_lessons"] == 6
    assert ret["deletes_detail_on_overflow"] is False
    assert doc["calibration"]["n_real"] == 6 and doc["calibration"]["ece"] is not None
    # ajan ağırlığı ÖNCE/DELTA/SONRA görünür
    contrib = doc["lessons"][-1]["agent_contributions"]
    assert contrib and all({"weight_before", "applied_delta", "weight_after", "sample_count",
                            "shrinkage_prior", "evidence_quality"} <= set(c) for c in contrib)
    assert all(abs(c["applied_delta"]) < 0.05 for c in contrib)   # tek sonuç sıçrama yapmaz
    # dashboard bu şemayı 200 ile render eder ve dürüst metni yazar
    d = _state(tmp_path, doc)
    c = TestClient(create_app(d, tmp_path / "market", None, DashboardConfig()))
    html = c.get("/learning").text
    assert c.get("/learning").status_code == 200
    assert "kayıpsız arşivleniyor" in html and "en fazla 200 ders tutar" not in html
    # Ders METNİ artık tek sonuçtan model hükmü İÇERMEZ (dashboard'un kendi statik
    # açıklama cümleleri bu denetimin dışındadır — denetlenen üretilen ders içeriğidir).
    lesson_text = json.dumps(doc["lessons"], ensure_ascii=False)
    assert "isabetli" not in lesson_text and "yanıldı" not in lesson_text
    assert "doğrulamaz" in lesson_text and "Sürpriz" in lesson_text


def test_29_close_to_lesson_to_archive_feeds_the_next_similar_decision(tmp_path: Path):
    """29 · Kapanış → ders → arşiv/indeks → SONRAKİ benzer karar için retrieval."""
    store = LessonStore(tmp_path / "arc", hot_window=2, min_rotate_block=1)
    lr = Learner(tmp_path / "learning.json", min_trades=2, lesson_store=store, hot_window=2)
    for i in range(8):
        lr.learn({"id": f"T{i}", "symbol": "ETH/USDT", "side": "LONG", "pnl": -10.0,
                  "r_multiple": -1.0, "exit_reason": "stop", "mae_pct": -2.0, "mfe_pct": 0.2,
                  "bars_held": 2, "closed_at": f"2026-08-1{i}T10:00:00+00:00",
                  "entry_price": 100.0, "initial_stop": 98.0,
                  "features": {"setup_type": "kırılım", "rr": 2.5, "p_win": 0.4,
                               "regime": "trend_up"}})
    q = store.query(symbol="ETH/USDT", direction="LONG", setup="kırılım", regime="trend_up",
                    hot=lr.state.lessons, k=6)
    assert SCOPE_HOT in q["retrieval_scope"] and SCOPE_INDEXED in q["retrieval_scope"]
    assert q["aggregate"]["n"] == 6 and q["aggregate"]["win_rate"] == 0.0
    assert q["aggregate"]["expectancy_r"] == pytest.approx(-1.0)
    codes = dict(q["aggregate"]["top_codes"])
    assert codes.get(LOW_MFE_STOP) == 6                  # gözlem kodu geçmişten geri okunur


def test_30_future_outcome_cannot_change_the_current_decision(tmp_path: Path):
    """30 · Sonradan gelen sonuç, ÖNCEKİ karar anındaki kanıtı DEĞİŞTİRMEZ."""
    book = CalibrationBook(min_bucket_sample=2)
    as_of = 2_000_000
    for i in range(4):
        book.add(trade_id=f"P{i}", p=0.35, won=(i % 2 == 0), label_ts=as_of - 10,
                 as_of_ms=as_of)
    before = book.bucket_stats(0.35)
    ev_before = outcome_probability_evidence(0.35, True, book=book, as_of=as_of)
    # gelecekteki sonuçlar reddedilir → karar anındaki kanıt BİREBİR aynı kalır
    for i in range(50):
        assert book.add(trade_id=f"F{i}", p=0.35, won=True, label_ts=as_of + 1,
                        as_of_ms=as_of) is False
    assert book.bucket_stats(0.35) == before
    assert outcome_probability_evidence(0.35, True, book=book, as_of=as_of) == ev_before
    # ders kaydı da geçmişi DEĞİŞTİRMEZ: promosyon yeni durum EKLER
    les = build_lesson(source_trade_id="T", symbol="ETH/USDT", direction="LONG",
                       setup="breakout", regime="trend_up")
    promoted = transition(les, RESEARCH_HYPOTHESIS, reason="8 benzer örnek")
    assert les["policy_status"] == OBSERVATION            # ORİJİNAL kayıt DOKUNULMADAN kalır
    assert promoted["policy_status"] == RESEARCH_HYPOTHESIS
    assert promoted["status_history"][-1]["from"] == OBSERVATION
    assert promoted["created_at"] == les["created_at"]


# ============================================================ ek: seçicilik kapısı

def test_selectivity_cannot_win_by_trading_almost_nothing():
    """İşlem sayısını düşürmek TEK BAŞINA başarı değildir — kapsam kapısı fail-closed."""
    train, valid = _rows(90), _rows(90, start=100)
    rules = fit_on_train(train)
    assert len(rules) <= 5 and rules[0].name == CHAMPION
    strict = SelectivityRule("QUALITY_PERCENTILE", threshold=1e9)      # neredeyse hiç işlem
    from tradingbot.quant.selectivity import coverage_gate
    gate = coverage_gate([r for r in valid if strict.accepts(r)], len(valid))
    assert gate["passed"] is False and gate["n_taken"] < MIN_TRADES_ABS
    sel = select_candidate(train, valid)
    chosen = next(c for c in sel["candidates"] if c["rule"] == sel["selected"])
    assert chosen["coverage_gate"]["passed"] is True
    assert net_edge_r({"p_win": 0.6, "rr": 2.0, "fee_drag_r": 0.1}) == pytest.approx(0.7)
    assert net_edge_r({"rr": 2.0}) is None                              # eksik alan → 0 DEĞİL


def test_exit_challenger_reports_both_rescue_and_truncation():
    """Challenger büyük kazananları kesiyorsa rapor bunu AÇIKÇA yazar."""
    rescue = _path_trade([(103.0, 99.5, 102.5), (104.5, 100.0, 101.0), (101.0, 97.5, 97.9)],
                         id="RESCUE")
    runner = _path_trade([(101.5, 99.5, 101.0), (105.0, 100.5, 104.5), (112.0, 104.0, 111.0)],
                         id="RUNNER", targets=[104.0, 120.0])
    rep = compare_exit_policies([rescue, runner], cost_per_fill_r=0.01)
    early = next(c for c in rep["challengers"] if c["policy"] == EARLY_PARTIAL_BE)
    assert early["high_mfe_stop_rescue"]["n"] >= 1
    assert "truncates_winners" in early["big_winner_truncation"]
    assert rep["label"].startswith("OFFLINE RESEARCH")
    with pytest.raises(ValueError):
        compare_exit_policies([rescue], challengers=[ExitPolicy(name=f"X{i}") for i in range(4)])


def test_portfolio_heat_challenger_is_advisory_only():
    """Portföy ısısı yalnız ADVISORY / karşı-olgusal üretir; aktif motoru DEĞİŞTİRMEZ."""
    from tradingbot.quant.risk_v2 import (ADVISORY, COUNTERFACTUAL_BLOCK, HEAT_VERDICTS,
                                          offline_risk_report, portfolio_heat_challenger)
    pos = [{"symbol": "ETH/USDT", "direction": "LONG", "risk_usdt": 9.0, "theme": "L1"},
           {"symbol": "SOL/USDT", "direction": "LONG", "risk_usdt": 1.0, "theme": "L1"}]
    rep = offline_risk_report(pos, {})
    heat = portfolio_heat_challenger(rep, pos)
    assert heat["verdict"] in HEAT_VERDICTS
    assert heat["applies_to_active_engine"] is False
    assert heat["changes_leverage_stop_tp_or_orders"] is False
    assert heat["independence_assumed"] is False           # korelasyon yoksa bağımsızlık VARSAYILMAZ
    assert heat["verdict"] == COUNTERFACTUAL_BLOCK         # tek yönde %100 küme payı
    assert heat["top_theme_share"] == pytest.approx(1.0)
    solo = [{"symbol": "ETH/USDT", "direction": "LONG", "risk_usdt": 1.0}]
    assert portfolio_heat_challenger(offline_risk_report(solo, {}), solo)["verdict"] in (
        ADVISORY, COUNTERFACTUAL_BLOCK)


def test_lesson_retention_performance_is_bounded(tmp_path: Path):
    """Sıcak döngü maliyeti arşiv toplamıyla DOĞRUSAL BÜYÜMEZ (rotate O(blok))."""
    store = LessonStore(tmp_path / "arc", hot_window=100, min_rotate_block=1)
    hot = [_lesson(i) for i in range(200)]
    hot = store.rotate(hot)["hot"]
    t0 = time.perf_counter()
    for batch in range(10):
        hot += [_lesson(1000 + batch * 100 + i) for i in range(100)]
        hot = store.rotate(hot)["hot"]
    elapsed = time.perf_counter() - t0
    assert len(store.archive.segments()) == 11
    assert store.stats(hot_count=len(hot))["archived_lessons"] == 1100
    assert elapsed < 20.0                                  # tur başına saniyeler değil, ms mertebesi
    t1 = time.perf_counter()
    q = store.query(symbol="ETH/USDT", direction="LONG", setup="breakout", regime="trend_up",
                    hot=hot, k=5)
    assert (time.perf_counter() - t1) < 2.0 and q["segments_scanned"] == 0   # sıcak yeterliydi


def test_deferred_sealing_never_deletes_a_lesson(tmp_path: Path):
    """Mühürleme ERTELENİRSE ders SİLİNMEZ — sıcak liste geçici olarak pencereyi aşar."""
    store = LessonStore(tmp_path / "arc", hot_window=10, min_rotate_block=50)
    hot = [_lesson(i) for i in range(40)]
    res = store.rotate(hot)
    assert res["archived"] == 0 and len(res["hot"]) == 40      # 30 taşma < 50 blok → beklet
    assert store.stats(hot_count=40)["lifetime_lessons"] == 40  # hiçbir ders kaybolmadı
    hot = res["hot"] + [_lesson(100 + i) for i in range(25)]
    res2 = store.rotate(hot)
    assert res2["archived"] == 55 and len(res2["hot"]) == 10    # eşiğe ulaşınca tek blokta mühürlendi
    assert len(store.archive.segments()) == 1                   # segment sayısı ŞİŞMEDİ
    assert store.stats(hot_count=10)["lifetime_lessons"] == 65
    ids = {r["source_trade_id"] for r in store.archive.iter_rows()}
    assert "T0" in ids and len(ids) == 55


def test_index_size_stays_bounded_as_the_archive_grows(tmp_path: Path):
    """İndeks arşivle DOĞRUSAL büyümez: anahtar başına en yeni `index_fanout` segment tutulur.

    v1'de segment başına anahtar listesi tutuluyordu; 100k derste indeks ~11 MB'a çıkıp sorgu
    p50'sini 129 ms'ye taşıyordu. Ters indeks boyutu `hücre × fanout` ile sınırlıdır.
    """
    store = LessonStore(tmp_path / "arc", hot_window=10, min_rotate_block=1, index_fanout=4)
    sizes = []
    hot: list = []
    for batch in range(30):
        hot += [_lesson(batch * 20 + i, sym=f"S{i % 5}/USDT") for i in range(20)]
        hot = store.rotate(hot)["hot"]
        sizes.append(store.index_path.stat().st_size)
    assert len(store.archive.segments()) == 30
    # ilk 10 segmentten sonra indeks boyutu PLATOYA oturur (±%20 bant)
    plateau = sizes[10:]
    assert max(plateau) <= min(plateau) * 1.2, sizes
    idx = store.index()
    assert all(len(v) <= 4 for v in idx["by_key"].values())
    assert idx["n_segments_indexed"] == 30
    assert idx["n_indexed"] == 590           # 600 ders − 10 sıcak; hiçbiri kaybolmadı
    assert store.stats(hot_count=len(hot))["lifetime_lessons"] == 600
    q = store.query(symbol="S0/USDT", direction="LONG", setup="breakout", regime="trend_up",
                    hot=[], k=3)
    assert q["segments_scanned"] <= store.max_segments_scanned
    # aggregate TÜM arşivlenmiş geçmişi sayar ve O(1) okunur — taranan segmentle SINIRLI değildir
    assert q["aggregate"]["n"] == 118


def test_exit_challenger_offline_bridge_slices_bars_correctly():
    """Bar yolu OFFLINE replay çerçevesinden kesilir — canlı worker'a tarama EKLENMEZ."""
    from tradingbot.quant.exit_challenger import bars_from_frame
    frame = [{"timestamp": t, "open": 100.0, "high": 100.0 + t / 10, "low": 99.0,
              "close": 100.0} for t in (10, 20, 30, 40, 50)]
    bars = bars_from_frame(frame, 15, 45)
    assert [b["high"] for b in bars] == [102.0, 103.0, 104.0]   # (15, 45] yarı açık aralık
    assert bars_from_frame(frame, 45, 15) == []                 # ters aralık → boş
    assert bars_from_frame(frame, None, 45) == []               # eksik zaman → boş (uydurma YOK)
    assert bars_from_frame([{"timestamp": 20, "high": None, "low": 1, "close": 1}], 10, 30) == []
    # yolu olmayan işlem karşılaştırmaya GİRMEZ, sessizce sıfır sayılmaz
    rep = compare_exit_policies([{"id": "NOPATH", "symbol": "A/USDT", "entry_price": 100.0,
                                  "initial_stop": 98.0, "direction": "LONG", "price_path": []}])
    assert rep["skipped_no_data"] == 1 and rep["champion"]["n"] == 0


def _quant_state(tmp_path: Path, doc: dict) -> Path:
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    (d / "quant_eval.json").write_text(json.dumps(doc, ensure_ascii=False, default=str),
                                       encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "live_order_path_enabled": False}),
                                 encoding="utf-8")
    return d


def test_quant_report_states_are_honest_when_data_is_missing():
    """Veri yokken challenger bölümleri UYDURMAZ; durumu AÇIKÇA yazar."""
    from tradingbot.quant.run import _exit_challenger_section, _selectivity_section
    ex = _exit_challenger_section([{"id": "A", "price_path": []}, {"id": "B"}])
    assert ex["state"] == "NO_BAR_PATH" and ex["active_policy_changed"] is False
    assert "bars_from_frame" in ex["note"]
    se = _selectivity_section(_rows(50), None)
    assert se["state"] == "NOT_RUN_NEEDS_FOLD_PLAN" and se["active_policy_changed"] is False
    # iki yollu (train/test) plan da YETMEZ — validation olmadan aday secilemez
    assert _selectivity_section(_rows(50), {"layout": "two_way", "folds": []})["state"] == \
        "NOT_RUN_NEEDS_FOLD_PLAN"


def test_quant_selectivity_uses_folds_and_never_sees_holdout():
    """Fold planı verilince seçim train+validation'dan yapılır; holdout ASLA girmez."""
    from tradingbot.quant.run import _selectivity_section
    from tradingbot.quant.walkforward import assign_rows, make_folds
    day = 86_400_000
    start = 1_700_000_000_000
    plan = make_folds(start, start + 400 * day, mode="anchored", train_days=120,
                      validation_days=60, test_days=60, holdout_days=60, tf="4h")
    rows = []
    for i in range(400):
        r = dict(_rows(1, start=i)[0])
        r["ts_ms"] = start + i * day + day // 2
        r["as_of_ms"] = r["ts_ms"] - 1000
        rows.append(r)
    out = _selectivity_section(rows, plan)
    assert out["state"] == "RUN" and out["active_policy_changed"] is False
    assert out["selection"]["saw_test_rows"] is False
    assert out["selection"]["saw_holdout_rows"] is False
    assert out["evaluation"]["selection_unchanged"] is True
    asg = assign_rows(rows, plan)
    assert asg["holdout"]                              # holdout satırı GERÇEKTEN vardı
    assert out["evaluation"].get("holdout_metrics") is None   # ve rapora GİRMEDİ


@pytest.mark.parametrize("q", [
    {},
    {"portfolio_heat": None, "exit_challenger": None, "selectivity_challenger": None},
    {"portfolio_heat": "bozuk", "exit_challenger": 7, "selectivity_challenger": []},
    {"exit_challenger": {"state": "NO_BAR_PATH", "note": "yok"}},
    {"exit_challenger": {"state": "RUN", "champion": {"policy": "C", "n": 2, "metrics": {}},
                         "challengers": [{"policy": "X", "n": 2, "metrics": {},
                                          "high_mfe_stop_rescue": {}, "big_winner_truncation": {}},
                                         "bozuk satır"]}},
    {"selectivity_challenger": {"state": "RUN", "selection": {"candidates": [None, {}]},
                                "evaluation": {}}},
    {"portfolio_heat": {"verdict": None, "reasons": [1, "ok"], "top_cluster_share": float("nan")}},
])
def test_quant_dashboard_survives_broken_challenger_sections(tmp_path: Path, q):
    """/quant eksik/bozuk/non-finite challenger bölümlerinde 500 ÜRETMEZ."""
    c = TestClient(create_app(_quant_state(tmp_path, q), tmp_path / "market", None,
                              DashboardConfig()))
    r = c.get("/quant")
    assert r.status_code == 200, q
    for verb in ("post", "put", "patch", "delete"):
        assert getattr(c, verb)("/quant").status_code == 405

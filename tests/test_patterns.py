"""Pattern zekâsı: causal feature future-mutation, triple-barrier worst-case + maliyet/funding, spot short red, no-look-ahead retrieval,
overlap purge, min sample / negative expectancy / cost-eroded / regime mismatch fail-closed, EvidencePacket exactness, noop açıklama,
rastgele veri güvenilir kenar üretmez, sentetik pattern yalnız out-of-sample doğrulanır, determinizm."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.patterns import (EvidencePacket, SimilarPatternEngine, build_feature_frame, compute_stats, explain_tr,  # noqa: E402
                                 feature_columns, packet_from_query, triple_barrier)
from tradingbot.patterns.outcomes import EXIT_STOP, EXIT_TP2, EXIT_TIME, Outcome, barriers_from_atr  # noqa: E402

H = 3_600_000
T0 = 1_700_000_000_000


def _candles(n=600, seed=1, drift=0.0, vol=0.01, start=T0, tf_ms=H):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, vol, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, vol, n))
    v = rng.uniform(50, 150, n)
    return pd.DataFrame({"timestamp": start + np.arange(n) * tf_ms, "open": o, "high": h, "low": l, "close": c, "volume": v,
                         "quote_volume": v * c, "trades": 10, "taker_buy_base": v * 0.5, "taker_buy_quote": v * c * 0.5, "close_time": start + np.arange(n) * tf_ms + tf_ms - 1})


def test_causal_features_do_not_change_when_future_changes():
    df = _candles(400)
    f1 = build_feature_frame(df, "1h")
    df2 = pd.concat([df, _candles(50, seed=9, start=T0 + 400 * H)], ignore_index=True)
    df2.loc[df2.index[-30:], "close"] *= 1.5                                    # geleceği bozarak değiştir
    f2 = build_feature_frame(df2, "1h")
    cols = feature_columns(f1)
    a, b = f1[cols].iloc[:400].reset_index(drop=True), f2[cols].iloc[:400].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    assert (f1["cutoff_ts"] == f1["event_ts"] + H - 1).all() and f1["schema_version"].iloc[0] == 1 and "miss_futures" in f1.columns
    assert f1["quality"].iloc[-1] > 0.6


def test_triple_barrier_worst_case_costs_funding_and_spot_short():
    o = np.array([100, 100, 100, 100, 100.0]); h = np.array([101, 106, 101, 101, 101.0]); l = np.array([99, 94, 99, 99, 99.0]); c = np.array([100, 100, 100, 100, 100.0])
    # aynı barda stop (95) ve tp (105) → önce stop (worst-case)
    oc = triple_barrier(o, h, l, c, 0, "LONG", stop=95, tp1=105, tp2=110, horizon=3, fee_pct=0.05, slippage_pct=0.03)
    assert oc.exit_reason == EXIT_STOP and oc.gross_r == -1.0 and oc.net_r < -1.0 and oc.fees_pct == 0.1 and oc.slippage_pct == 0.06
    # TP2 (uzun) — TP1 kısmi + kalan TP2
    h2 = np.array([101, 106, 111, 101, 101.0]); l2 = np.array([99, 99, 100.5, 99, 99.0])   # TP1 sonrası BE stop tetiklenmesin
    oc2 = triple_barrier(o, h2, l2, c, 0, "LONG", stop=95, tp1=105, tp2=110, horizon=3)
    assert oc2.exit_reason == EXIT_TP2 and oc2.tp1_hit and abs(oc2.gross_r - 1.5) < 1e-9
    # funding: long öder → net düşer; short alır → net artar
    oc_l = triple_barrier(o, h2, l2, c, 0, "LONG", stop=95, tp1=105, tp2=110, horizon=3, funding_pct_per_bar=0.05)
    assert oc_l.net_r < oc2.net_r and oc_l.funding_pct > 0
    oc_s = triple_barrier(o, np.full(5, 101.0), np.array([99, 99, 99, 99, 99.0]), c, 0, "SHORT", stop=105, tp1=95, tp2=90, horizon=3, funding_pct_per_bar=0.05)
    assert oc_s.exit_reason == EXIT_TIME and oc_s.funding_pct < 0                # short funding alır
    import pytest
    with pytest.raises(ValueError):
        triple_barrier(o, h, l, c, 0, "SHORT", stop=105, tp1=95, tp2=90, horizon=3, market="spot")
    assert barriers_from_atr(100, 2, "SHORT") == (105, 95, 90)


def _mk(o: float = 0.5, ts=T0, reason="TP2", n=1):
    return Outcome("LONG", "futures", 1, 5, 100, 101, 95, 105, 110, reason, 4, o * 5, 0.1, 0.06, 0.0, o * 5 - 0.16, o, o - 0.032, -1.0, 2.0, True)


def test_stats_fail_closed_codes():
    now = T0 + 300 * 86_400_000
    # az örnek
    st = compute_stats([(T0 + i * H, _mk(1.0), "A", "UP_MIDVOL", "futures") for i in range(5)], now_ts=now, min_sample=30)
    assert "INSUFFICIENT_SAMPLE" in st.codes and not st.ok
    # yüksek win-rate ama negatif beklenti (küçük kazançlar, büyük kayıplar)
    outs = [(T0 + i * H, _mk(0.2 if i % 10 else -5.0), "A", "UP_MIDVOL", "futures") for i in range(100)]
    st = compute_stats(outs, now_ts=now, min_sample=30)
    assert st.wins >= 85 and st.mean_net_r < 0 and "NEGATIVE_EXPECTANCY" in st.codes and not st.ok
    # brüt pozitif, maliyet siliyor
    outs = [(T0 + i * H, Outcome("LONG", "futures", 1, 5, 100, 100.5, 95, 105, 110, "TIME", 4, 0.1, 0.1, 0.06, 0.0, -0.06, 0.02, -0.012, -0.5, 0.5, False), "A", "UP_MIDVOL", "futures") for i in range(60)]
    st = compute_stats(outs, now_ts=now, min_sample=30)
    assert "COST_ERODED_EDGE" in st.codes
    # sağlam pozitif → ok; rejim uyumsuz → REGIME_MISMATCH
    rng = np.random.default_rng(0)
    outs = [(T0 + i * 86_400_000, _mk(float(rng.normal(0.6, 0.5))), "A", "UP_MIDVOL", "futures") for i in range(300)]
    st = compute_stats(outs, now_ts=now, min_sample=30, query_regime="UP_MIDVOL")
    assert st.ok and st.expectancy_ci[0] > 0 and st.p_win_ci[0] > 0.5 and st.windows["90d"]["n"] >= 80
    st2 = compute_stats(outs, now_ts=now, min_sample=30, query_regime="DOWN_HIGHVOL")
    assert "REGIME_MISMATCH" in st2.codes
    # edge decay: son 90 gün çok kötü
    outs2 = [(ts, (_mk(-0.5) if now - ts <= 90 * 86_400_000 else o), s, r, m) for ts, o, s, r, m in outs]
    st3 = compute_stats(outs2, now_ts=now, min_sample=30)
    assert "EDGE_DECAY" in st3.codes and st3.edge_decay < 0


def test_engine_no_lookahead_purge_and_determinism():
    df = _candles(900, seed=3, drift=0.0005)
    eng = SimilarPatternEngine(min_sample=10, embargo_bars=2)
    n = eng.add_series("A/USDT", "futures", "1h", df)
    assert n > 500
    qi = 700
    q_cut = int(eng.frames[("A/USDT", "futures", "1h")]["cutoff_ts"].iloc[qi])
    res = eng.query("A/USDT", "futures", "1h", "LONG", idx=qi, k=40)
    assert res["n"] > 0
    # her komşunun hem cutoff'u hem çıkışı sorgu anından önce
    for nb in res["neighbors"]:
        assert nb["event_ts"] < q_cut
    ev_by_ts = {e.event_ts: e for e in eng.events}
    for nb in res["neighbors"]:
        e = ev_by_ts[nb["event_ts"]]
        exit_ts = int(df["timestamp"].iloc[e.outcomes["LONG"].exit_idx])
        assert exit_ts < q_cut and e.cutoff_ts < q_cut - 2 * H
    # overlap purge: aynı sembolde komşular en az min_sep bar ayrık
    idxs = sorted(ev_by_ts[nb["event_ts"]].idx for nb in res["neighbors"])
    assert all(b - a >= 16 for a, b in zip(idxs, idxs[1:]))
    # determinizm: aynı sorgu aynı sonuç
    res2 = SimilarPatternEngine(min_sample=10, embargo_bars=2)
    res2.add_series("A/USDT", "futures", "1h", df)
    assert res2.query("A/USDT", "futures", "1h", "LONG", idx=qi, k=40) == res
    # spot: SHORT sonucu yok
    eng_s = SimilarPatternEngine(min_sample=10); eng_s.add_series("A/USDT", "spot", "1h", df)
    assert all("SHORT" not in e.outcomes for e in eng_s.events)
    assert eng_s.query("A/USDT", "spot", "1h", "SHORT", idx=qi)["n"] == 0


def test_random_data_yields_no_trusted_edge_and_synthetic_edge_only_out_of_sample():
    # rastgele yürüyüş: birçok sorguda "ok" oranı düşük (güvenilir kenar iddiası yok)
    oks = 0
    for seed in range(6):
        df = _candles(700, seed=100 + seed)
        eng = SimilarPatternEngine(min_sample=30)
        eng.add_series("R/USDT", "futures", "1h", df)
        for qi in (500, 600, 680):
            r = eng.query("R/USDT", "futures", "1h", "LONG", idx=qi, k=60)
            oks += int(r["ok"])
    assert oks <= 3
    # sentetik kenar: RSI düşük + trend yukarı sonrasında yükseliş enjekte edilmiş seri → geçmiş (in-sample) olaylar sorgu anından
    # önce; sonuçlar yalnız sorgu ANINDAN ÖNCEKİ olaylardan (out-of-sample doğrulama = geleceği içermez)
    rng = np.random.default_rng(7)
    n = 1500
    r = rng.normal(0.0004, 0.008, n)
    for i in range(200, n - 30, 60):
        r[i:i + 3] = -0.02                        # keskin düşüş
        r[i + 3:i + 15] = 0.006                   # ardından toparlanma (öğrenilebilir olay)
    c = 100 * np.exp(np.cumsum(r))
    df = pd.DataFrame({"timestamp": T0 + np.arange(n) * H, "open": np.r_[100, c[:-1]], "high": np.maximum(np.r_[100, c[:-1]], c) * 1.002,
                       "low": np.minimum(np.r_[100, c[:-1]], c) * 0.998, "close": c, "volume": 100.0})
    eng = SimilarPatternEngine(min_sample=10, horizon=12)
    eng.add_series("S/USDT", "futures", "1h", df)
    qi = 200 + 60 * 20 + 2                          # bir düşüş olayının 3. barı (toparlanma öncesi)
    res = eng.query("S/USDT", "futures", "1h", "LONG", idx=qi, k=40)
    assert res["n"] >= 10 and all(nb["event_ts"] < int(df["timestamp"].iloc[qi]) for nb in res["neighbors"])
    assert res["stats"]["mean_net_r"] > 0


def test_evidence_packet_exact_and_noop_explanation():
    df = _candles(800, seed=5, drift=0.0008)
    eng = SimilarPatternEngine(min_sample=10)
    eng.add_series("E/USDT", "futures", "1h", df)
    res = eng.query("E/USDT", "futures", "1h", "LONG", idx=650, k=30)
    p = packet_from_query(res, decision_id="dec1", timestamp="2026-01-01T00:00:00+00:00", timeframes=["1h"])
    assert isinstance(p, EvidencePacket) and p.decision_id == "dec1" and p.symbol == "E/USDT" and p.side == "LONG"
    st = res["stats"]
    assert p.independent_sample_count == res["n"] and p.win_rate == st["p_win_posterior"] and p.win_rate_ci == st["p_win_ci"]
    assert p.net_expectancy_r == st["mean_net_r"] and p.expectancy_ci == st["expectancy_ci"] and p.mae_pct == st["mae_pct_mean"]
    assert p.fee_slippage_funding_r == st["cost_drag_r"] and p.recency == st["windows"] and p.veto_reasons == res["codes"] and p.ok == res["ok"]
    assert len(p.neighbor_ids) == len(res["neighbors"]) and p.pattern_id
    txt = explain_tr(p)
    assert "bağımsız geçmiş olay" in txt and "garanti" in txt or "Kısıt" in txt
    assert "kesin" not in txt.lower().replace("kesinlik", "")
    assert explain_tr(p) == explain_tr(p)                                    # deterministik
    p0 = packet_from_query({"ok": False, "codes": ["INSUFFICIENT_SAMPLE"], "n": 0, "query": {"symbol": "X", "market": "spot", "tf": "1h", "side": "LONG"}}, timestamp="t")
    assert "kanıt yok" in explain_tr(p0)


def test_specialist_uses_evidence_and_is_unusable_without_it():
    import test_coinhead as T
    from tradingbot.coinhead import CoinHead, CoinHeadConfig
    fr = T.frames(seed=3, drift=0.0015)
    reports, brief = T.legacy(fr)
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    d0 = CoinHead("ETH/USDT", cfg).decide(T._inputs(fr, reports, brief))
    sp0 = next(r for r in d0.specialist_reports if r.agent_name == "similar_patterns")
    assert not sp0.usable and sp0.error == "PATTERN_EVIDENCE_MISSING"
    ev = {"LONG": {"ok": True, "n": 80, "codes": [], "stats": {"mean_net_r": 0.4, "expectancy_ci": [0.15, 0.65], "p_win_posterior": 0.58, "edge_decay": 0.0}},
          "SHORT": {"ok": False, "n": 12, "codes": ["INSUFFICIENT_SAMPLE"], "stats": {"mean_net_r": -0.1}}}
    d1 = CoinHead("ETH/USDT", cfg).decide(T._inputs(fr, reports, brief, pattern_evidence=ev))
    sp1 = next(r for r in d1.specialist_reports if r.agent_name == "similar_patterns")
    assert sp1.usable and sp1.bias > 0 and sp1.factor_group == "historical_edge" and sp1.metrics["independent_sample_count"] == 80
    ev2 = {"LONG": {"ok": False, "n": 90, "codes": ["NEGATIVE_EXPECTANCY"], "stats": {"mean_net_r": -0.3}}, "SHORT": {"ok": False, "n": 5, "codes": ["INSUFFICIENT_SAMPLE"], "stats": {}}}
    d2 = CoinHead("ETH/USDT", cfg).decide(T._inputs(fr, reports, brief, pattern_evidence=ev2))
    sp2 = next(r for r in d2.specialist_reports if r.agent_name == "similar_patterns")
    assert sp2.usable and sp2.bias == 0.0 and "NEGATIVE_EXPECTANCY" in sp2.warnings

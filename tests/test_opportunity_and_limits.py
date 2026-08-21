"""Sabit işlem sayısı kotasının kaldırıldığını ve kararın ekonomiyle verildiğini kanıtlayan testler.

Bot artık "belirli sayıda işlem" hedeflemez. Her geçerli fırsat maliyet sonrası, belirsizlik ayarlı
beklentiyle değerlendirilir; zayıf kanıtlar üst üste dizilmiş sert engeller yerine tek bir
`conservative_net_edge_r` içinde toplanır ve pozisyon boyutunu belirler.

"100 işlem/gün" bir kota değildir: 100 ayrı, benzersiz, maliyet sonrası olumlu fırsat oluşur ve her
işlem kapanarak risk bütçesini serbest bırakırsa sabit bir sayaç bunları engellememelidir.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.coinhead.chief import ChiefConfig, ChiefPortfolioManager  # noqa: E402
from tradingbot.decision_gates import (FORBIDDEN_QUOTA_CODES, GATES, GateLedger, HARD_SAFETY,  # noqa: E402
                                       SOFT_EVIDENCE, hard_codes, is_hard, soft_codes)
from tradingbot.opportunity import (GROSS_MINUS_COSTS, NET_OUTCOME, assess, cost_in_r,  # noqa: E402
                                    rank, uncertainty_penalty_r)
from tradingbot.risk.profiles import resolve_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tradingbot"


def _src(*parts: str) -> str:
    return (SRC.joinpath(*parts)).read_text(encoding="utf-8")


# =========================================================================== 1-2) sabit limitler kalktı
def test_no_daily_or_per_run_trade_count_limit_in_source():
    """Kaynakta günlük/tur başına işlem ADEDİ limiti bulunmamalı (başka adla da olmamalı)."""
    for rel in (("coinhead", "chief.py"), ("engine_v3.py",), ("risk", "engine.py")):
        text = _src(*rel)
        for bad in FORBIDDEN_QUOTA_CODES:
            assert bad not in text, f"{rel}: yasak kota kodu {bad}"
        assert "trades_per_day" not in text and "max_trades" not in text, rel
    chief = _src("coinhead", "chief.py")
    assert "granted >= cfg.max_new_positions_per_run" not in chief
    assert "tur başına yeni pozisyon limiti" not in chief


def test_max_new_positions_per_run_hard_cap_is_removed():
    c = ChiefConfig()
    assert c.max_new_positions_per_run is None and c.daily_trade_cap is None
    # Kalan alanlar YIĞILMA EŞİĞİ; veto değil ceza üretirler.
    assert c.crowded_same_direction_at >= 1 and c.crowding_penalty_r > 0
    assert not hasattr(c, "max_same_direction") and not hasattr(c, "max_same_cluster_same_direction")


# =========================================================================== 3-5) sıralama ve kapasite
def _opp(edge: float, risk_pct: float = 1.0) -> dict:
    return {"conservative_net_edge_r": edge, "opportunity_score": min(1.0, max(0.0, edge)),
            "risk_pct_requested": risk_pct, "size_multiplier": 1.0}


class _Dec:
    """Chief'in ihtiyaç duyduğu asgari karar yüzeyi (gerçek alan adlarıyla)."""

    def __init__(self, symbol, edge, *, direction="LONG", risk_pct=1.0, vetoes=()):
        from tradingbot.coinhead.schema import Verdict
        self.symbol, self.direction = symbol, direction
        self.verdict = Verdict.FUTURES_LONG if direction == "LONG" else Verdict.FUTURES_SHORT
        self.is_actionable, self.market_type = True, "futures"
        self.expected_r, self.confidence_calibrated, self.p_win = 2.0, 0.7, 0.55
        self.dissent, self.vetoes, self.no_trade_reason, self.regime = [], list(vetoes), "", "TREND_UP"
        self.opportunity = _opp(edge, risk_pct)


def test_three_strong_opportunities_in_one_run_all_get_permission():
    decs = [_Dec("ETH/USDT", 0.6), _Dec("SOL/USDT", 0.5), _Dec("AVAX/USDT", 0.4)]
    ch = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=6.0)).decide(
        decs, {"equity": 1000.0, "open_positions": [], "total_open_risk_usdt": 0.0})
    assert all(ch.permission[d.symbol]["allow"] for d in decs), ch.permission
    assert ch.exposure["ranked"] == 3 and ch.exposure["granted_this_run"] == 3
    assert ch.exposure["daily_trade_cap"] is None and ch.exposure["per_run_trade_cap"] is None


def test_all_candidates_are_ranked_before_processing():
    """Beş pozitif aday ÖNCEDEN sıralanır; daha güçlü üçüncü aday ilk ikisi yüzünden elenmez."""
    decs = [_Dec("A/USDT", 0.1), _Dec("B/USDT", 0.2), _Dec("C/USDT", 0.9),
            _Dec("D/USDT", 0.3), _Dec("E/USDT", 0.7)]
    ch = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=6.0)).decide(
        decs, {"equity": 1000.0, "open_positions": [], "total_open_risk_usdt": 0.0})
    order = [r["symbol"] for r in ch.ranking]
    assert order == ["C/USDT", "E/USDT", "D/USDT", "B/USDT", "A/USDT"], order
    assert ch.priority[0] == "C/USDT"
    assert all(ch.permission[s]["allow"] for s in order)          # bütçe yetiyor → hepsi
    # Kapasite projeksiyonu yalnız 2 işleme yetse bile CHIEF ENGELLEMEZ (rezervasyon yok):
    # sıra korunur, yetkili karar nihai boyutla RiskEngine'de verilir.
    ch2 = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=2.0)).decide(
        decs, {"equity": 1000.0, "open_positions": [], "total_open_risk_usdt": 0.0})
    assert [r["symbol"] for r in ch2.ranking] == order
    assert all(ch2.permission[s]["allow"] for s in order), ch2.permission
    fit = [s for s in order if ch2.permission[s]["capacity_projection"]["would_fit"]]
    assert fit == ["C/USDT", "E/USDT"], fit          # projeksiyon (ADVISORY) en güçlü ikisini işaretler
    assert ch2.exposure["advisory_capacity_fit"] == 2
    assert ch2.exposure["authoritative_risk_reservation"] is False


def test_capacity_projection_is_advisory_and_never_blocks_in_chief():
    """Kapasite dolu görünse bile CHIEF engellemez; kod hiçbir zaman kota kodu olamaz."""
    decs = [_Dec("A/USDT", 0.9, risk_pct=2.0), _Dec("B/USDT", 0.8, risk_pct=2.0)]
    ch = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=2.0)).decide(
        decs, {"equity": 1000.0, "open_positions": [], "total_open_risk_usdt": 0.0})
    assert ch.permission["A/USDT"]["allow"] and ch.permission["B/USDT"]["allow"]
    assert ch.permission["A/USDT"]["capacity_projection"]["would_fit"] is True
    assert ch.permission["B/USDT"]["capacity_projection"]["would_fit"] is False   # yalnız RAPOR
    for perm in ch.permission.values():
        assert perm.get("block_code") is None
        assert perm.get("block_code") not in FORBIDDEN_QUOTA_CODES
        assert perm["capacity_projection"]["advisory"] is True
    assert ch.exposure["risk_capacity_left_usdt"] >= 0
    assert ch.exposure["risk_used_usdt"] == 0.0        # GERÇEK açık risk; rezervasyon yok


def test_hundred_sequential_unique_opportunities_are_never_counter_blocked():
    """100 ardışık benzersiz fırsat: her biri kapanıp riski serbest bıraktığında hiçbiri sabit
    sayaç yüzünden reddedilmemeli. (Aynı barın tekrarı DEĞİL: her tur farklı sembol/fırsat.)"""
    mgr = ChiefPortfolioManager(ChiefConfig(max_total_open_risk_pct=6.0))
    granted, codes = 0, set()
    for i in range(100):
        d = _Dec(f"S{i:03d}/USDT", 0.5, risk_pct=2.0)
        ch = mgr.decide([d], {"equity": 1000.0, "open_positions": [],
                              "total_open_risk_usdt": 0.0})       # önceki işlem kapandı → risk serbest
        perm = ch.permission[d.symbol]
        granted += 1 if perm["allow"] else 0
        if perm.get("block_code"):
            codes.add(perm["block_code"])
    assert granted == 100, f"sabit sayaç engelledi: {granted}/100, kodlar={codes}"
    assert not (codes & set(FORBIDDEN_QUOTA_CODES))


# =========================================================================== 6-9) yumuşak kanıt davranışı
def _ledger(*pairs) -> GateLedger:
    g = GateLedger()
    for code, pen in pairs:
        g.penalise(code, pen)
    return g


def test_single_weak_soft_signal_does_not_reject_strong_composite_edge():
    # Orta güçlü (doyuma ulaşmamış) edge: cezanın boyuta etkisi görünür olsun.
    strong = dict(symbol="ETH/USDT", side="LONG", setup="pullback", p_win=0.55, avg_win_r=1.3,
                  avg_loss_r=1.0, sample_size=80, cost_pct_notional=0.2, stop_dist_pct=3.0)
    clean = assess(gates=GateLedger(), **strong)
    weak = assess(gates=_ledger(("RR_BELOW_PREFERRED", 0.05)), **strong)
    assert clean.tradeable and weak.tradeable, "tek zayıf gösterge işlemi ÖLDÜRMEMELİ"
    assert weak.size_multiplier < clean.size_multiplier, "boyut düşmeli"
    assert weak.hard_block_codes == []
    # Çok güçlü bir edge küçük bir cezayla tavandan düşmeyebilir — bu KASITLIDIR.
    huge = dict(strong, p_win=0.75, avg_win_r=2.5)
    assert assess(gates=_ledger(("RR_BELOW_PREFERRED", 0.05)), **huge).size_multiplier == 1.0


def test_many_moderate_weaknesses_shrink_size_and_do_not_become_ten_vetoes():
    strong = dict(symbol="ETH/USDT", side="LONG", setup="pullback", p_win=0.55, avg_win_r=1.3,
                  avg_loss_r=1.0, sample_size=120, cost_pct_notional=0.2, stop_dist_pct=3.0)
    many = _ledger(*[(c, 0.05) for c in ("LOW_CONSENSUS", "LOW_CONFIDENCE", "HIGH_DISSENT",
                                         "PATTERN_WEAK", "SPREAD_WIDE", "VOL_REGIME_HIGH",
                                         "FUNDING_ADVERSE", "MA_POSITION", "RSI_LEVEL", "VOLUME_WEAK")])
    a = assess(gates=many, **strong)
    assert a.hard_block_codes == [], "çok sayıda orta zayıflık otomatik VETO üretmemeli"
    assert len(a.soft_evidence) == 10
    assert many.soft_penalty_r() <= 0.60, "toplam yumuşak ceza üst sınırlı olmalı"
    base = assess(gates=GateLedger(), **strong)
    # On ayri veto YERINE: boyut kucultuldu (gerekirse arastirma boyutuna dustu).
    assert 0 < a.size_multiplier < base.size_multiplier
    assert a.research_only or a.tradeable


def test_trade_that_turns_negative_after_costs_is_rejected():
    a = assess(symbol="X/USDT", side="LONG", setup="breakout", gates=GateLedger(),
               p_win=0.5, avg_win_r=1.05, avg_loss_r=1.0, sample_size=200,
               cost_pct_notional=1.2, stop_dist_pct=2.0, expectancy_basis=GROSS_MINUS_COSTS)
    assert a.net_expectancy_r < 0 and not a.tradeable and not a.research_only
    assert a.size_multiplier == 0.0


def test_low_rr_with_high_p_win_can_beat_high_rr_with_low_p_win():
    """Kararı sabit R/R değil, maliyet sonrası muhafazakâr edge verir."""
    low_rr = assess(symbol="A/USDT", side="LONG", setup="s", gates=GateLedger(), p_win=0.75,
                    avg_win_r=1.2, avg_loss_r=1.0, sample_size=150, cost_pct_notional=0.1,
                    stop_dist_pct=3.0)
    high_rr = assess(symbol="B/USDT", side="LONG", setup="s", gates=GateLedger(), p_win=0.25,
                     avg_win_r=2.0, avg_loss_r=1.0, sample_size=150, cost_pct_notional=0.1,
                     stop_dist_pct=3.0)
    assert low_rr.tradeable and not high_rr.tradeable
    assert rank([high_rr, low_rr])[0].symbol == "A/USDT"


# =========================================================================== maliyet çift sayılmıyor
def test_cost_is_never_double_counted():
    common = dict(symbol="X/USDT", side="LONG", setup="s", p_win=0.55, avg_win_r=2.0, avg_loss_r=1.0,
                  sample_size=100, cost_pct_notional=0.6, stop_dist_pct=3.0)
    net_basis = assess(gates=GateLedger(), expectancy_basis=NET_OUTCOME, **common)
    gross_basis = assess(gates=GateLedger(), expectancy_basis=GROSS_MINUS_COSTS, **common)
    assert net_basis.cost_r == 0.0, "NET_OUTCOME tabanında maliyet TEKRAR düşülmez"
    assert gross_basis.cost_r == pytest.approx(cost_in_r(0.6, 3.0))
    assert net_basis.net_expectancy_r == net_basis.gross_expectancy_r
    assert gross_basis.net_expectancy_r == pytest.approx(
        gross_basis.gross_expectancy_r - gross_basis.cost_r)
    assert net_basis.expectancy_basis == NET_OUTCOME and gross_basis.expectancy_basis == GROSS_MINUS_COSTS
    with pytest.raises(ValueError):
        assess(gates=GateLedger(), expectancy_basis="BOGUS", **common)


def test_uncertainty_penalty_shrinks_with_sample_size():
    assert uncertainty_penalty_r(0) > uncertainty_penalty_r(20) > uncertainty_penalty_r(500) > 0
    small = assess(symbol="X/USDT", side="LONG", setup="s", gates=GateLedger(), p_win=0.6,
                   avg_win_r=1.5, avg_loss_r=1.0, sample_size=1, cost_pct_notional=0.1, stop_dist_pct=3.0)
    large = assess(symbol="X/USDT", side="LONG", setup="s", gates=GateLedger(), p_win=0.6,
                   avg_win_r=1.5, avg_loss_r=1.0, sample_size=400, cost_pct_notional=0.1, stop_dist_pct=3.0)
    assert small.conservative_net_edge_r < large.conservative_net_edge_r
    assert 0 < small.risk_pct_requested <= 2.0 and 0 < large.risk_pct_requested <= 2.0


def test_research_only_when_point_estimate_positive_but_uncertain():
    a = assess(symbol="X/USDT", side="LONG", setup="s", gates=GateLedger(), p_win=0.52,
               avg_win_r=1.1, avg_loss_r=1.0, sample_size=0, cost_pct_notional=0.05, stop_dist_pct=3.0)
    assert a.net_expectancy_r > 0 and a.conservative_net_edge_r <= 0
    assert a.research_only and not a.tradeable and 0 < a.size_multiplier < 0.5


# =========================================================================== kapı sözleşmesi
def test_gate_contract_classifies_every_control():
    assert set(hard_codes()) & set(soft_codes()) == set()
    for code in ("MA_POSITION", "RSI_LEVEL", "LOW_CONSENSUS", "PATTERN_WEAK", "RR_BELOW_PREFERRED",
                 "SAME_DIRECTION_CROWDED", "CLUSTER_CROWDED", "MARKET_REGIME_MISMATCH", "SPREAD_WIDE"):
        assert not is_hard(code), f"{code} tek başına REDDETMEMELİ"
        assert GATES[code].cls == SOFT_EVIDENCE
    for code in ("KILL_SWITCH_ACTIVE", "STOP_PRESENT", "ZERO_STOP_DISTANCE", "TIMESTAMP_LEAKAGE",
                 "DUPLICATE_SIGNAL", "ALREADY_OPEN_SAME_SYMBOL", "TOTAL_OPEN_RISK",
                 "NEGATIVE_NET_EDGE", "RISK_CAPACITY_BLOCKED", "DATA_INVALID"):
        assert is_hard(code) and GATES[code].cls == HARD_SAFETY
    with pytest.raises(ValueError):
        GateLedger().penalise("KILL_SWITCH_ACTIVE", 0.1)      # sert kapı yumuşak olamaz
    with pytest.raises(ValueError):
        GateLedger().block("MA_POSITION")                     # yumuşak kanıt sert engel olamaz


def test_zero_stop_distance_and_missing_stop_are_hard():
    g = GateLedger().block("ZERO_STOP_DISTANCE")
    a = assess(symbol="X/USDT", side="LONG", setup="s", gates=g, p_win=0.9, avg_win_r=5.0,
               avg_loss_r=1.0, sample_size=500, cost_pct_notional=0.0, stop_dist_pct=0.0)
    assert a.blocked and not a.tradeable and a.size_multiplier == 0.0
    assert cost_in_r(0.5, 0.0) == 0.0                          # sıfır stop → maliyet R'ye çevrilemez


# =========================================================================== risk profilleri
def test_paper_uses_risk_budget_not_position_counts():
    p = resolve_profile("PAPER_RESEARCH")
    assert p.max_open_positions is None and p.max_positions_per_market is None
    assert p.max_total_open_risk_pct == 6.0 and p.risk_per_trade_pct == 2.0
    text = _src("risk", "engine.py")
    assert "if p.max_open_positions is not None:" in text     # kapı yalnız tanımlıysa uygulanır


@pytest.mark.parametrize("name", ["TESTNET", "SHADOW_LIVE", "LIVE", "LIVE_LIMITED"])
def test_conservative_profiles_keep_their_count_limits(name):
    p = resolve_profile(name)
    assert p.max_open_positions is not None and p.max_positions_per_market is not None
    assert p.max_open_positions <= 3 and p.risk_per_trade_pct <= 0.5
    assert p.max_total_open_risk_pct <= 2.0


def test_llm_budget_never_reaches_execution():
    """`daily_usd_budget` yalnız LLM harcama bütçesidir; işlem/limit yolunda KULLANILMAZ."""
    from tradingbot.config_v3 import load_v3
    cfg = load_v3({"mode": "PAPER"})
    assert cfg.llm.daily_usd_budget == 2.0
    for rel in (("engine_v3.py",), ("coinhead", "chief.py"), ("risk", "engine.py"),
                ("coinhead", "head.py"), ("opportunity.py",)):
        text = _src(*rel)
        assert "daily_usd_budget" not in text, f"{rel}: LLM bütçesi karar yolunda kullanılamaz"
        assert "daily_token_budget" not in text, rel


# =========================================================================== starvation regresyonu
def test_mixed_quality_universe_does_not_starve(tmp_path, monkeypatch):
    """Karışık kaliteli fırsat setinde pipeline yalnız hard-gate çoğalması yüzünden SIFIR işlem üretmemeli."""
    eng = _engine(tmp_path, monkeypatch)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    funnel = json.loads((eng.cfg.state_path / "decision_funnel.json").read_text(encoding="utf-8"))
    assert funnel["run"]["actionable"] > 0, "hiç işlenebilir aday yok — senaryo geçersiz"
    assert s["opened"], f"starvation: sıfır işlem (funnel={funnel['run']})"
    assert funnel["run"]["opened"] == len(s["opened"])
    assert funnel["daily_trade_cap"] is None and funnel["per_run_trade_cap"] is None


def test_starvation_test_does_not_create_a_minimum_quota(tmp_path, monkeypatch):
    """Negatif edge'li veri setinde SIFIR işlem DOĞRU sonuçtur — asgari kota yaratılmamalı."""
    eng = _engine(tmp_path, monkeypatch)
    import tradingbot.engine_v3 as E

    def _all_negative(self, decisions, briefs):      # her adayı ekonomik olarak negatif yap
        for d in decisions.values():
            if getattr(d, "is_actionable", False):
                d.opportunity = {"conservative_net_edge_r": -0.5, "net_expectancy_r": -0.4,
                                 "opportunity_score": 0.0, "size_multiplier": 0.0,
                                 "risk_pct_requested": 0.0, "tradeable": False, "research_only": False,
                                 "hard_block_codes": []}
    monkeypatch.setattr(E.TradingEngineV3, "_assess_opportunities", _all_negative)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s["opened"] == [], "negatif edge'de işlem açılmamalı"
    funnel = json.loads((eng.cfg.state_path / "decision_funnel.json").read_text(encoding="utf-8"))
    assert funnel["run"]["negative_edge_blocked"] > 0
    assert funnel["run"]["opened"] == 0


# =========================================================================== huni + benzersiz sinyal
def test_decision_funnel_is_persisted_with_required_fields(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    f = json.loads((eng.cfg.state_path / "decision_funnel.json").read_text(encoding="utf-8"))
    for key in ("actionable", "risk_capacity_blocked", "positive_point_edge",
                "positive_conservative_edge", "research_small", "duplicate_blocked",
                "trigger_fired", "exchange_rejected", "opened", "closed"):
        assert key in f["run"], key
    for key in ("rolling_24h", "trades_opened_24h", "hard_block_rate", "no_trade_rate",
                "opportunity_cost_count", "daily_trade_cap", "per_run_trade_cap"):
        assert key in f, key
    assert f["daily_trade_cap"] is None and f["per_run_trade_cap"] is None


def test_duplicate_signal_is_blocked_but_a_new_bar_is_not(tmp_path, monkeypatch):
    import pandas as pd
    eng = _engine(tmp_path, monkeypatch)
    s1 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s1["opened"], "ilk turda giriş bekleniyor"
    for sym, pos in list(eng.ledger2.positions.items()):      # pozisyonları kapat → sinyal serbest
        eng._fake_live.price[sym] = float(pos.stop) * (0.98 if pos.side.value == "LONG" else 1.02)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    eng._fake_live.price.clear()
    s2 = eng.tour(do_scan=False, obsidian=False, charts=False)     # AYNI bar → duplicate
    f2 = json.loads((eng.cfg.state_path / "decision_funnel.json").read_text(encoding="utf-8"))["run"]
    assert s2["opened"] == [] and f2["duplicate_blocked"] > 0
    # YENİ kapanmış bar → yeni benzersiz sinyal → engel YOK
    for _sym, fr in eng.runner.last_frames.items():
        for tf, ms in (("1d", 86_400_000), ("4h", 14_400_000), ("1h", 3_600_000)):
            df = fr.get(tf)
            if df is None:
                continue
            df["timestamp"] = df["timestamp"] + ms
            df.index = df.index + pd.Timedelta(milliseconds=ms)
    eng._fake_live._now_s += 14_400_000 / 1000
    monkeypatch.setattr("tradingbot.engine_v3.utc_now",
                        lambda _t=eng._fake_live._now_s: __import__("datetime").datetime.fromtimestamp(
                            _t, tz=__import__("datetime").timezone.utc))
    s3 = eng.tour(do_scan=False, obsidian=False, charts=False)
    f3 = json.loads((eng.cfg.state_path / "decision_funnel.json").read_text(encoding="utf-8"))["run"]
    assert s3["opened"] or f3["duplicate_blocked"] == 0, "yeni bar duplicate sayılmamalı"


def test_signal_id_components_are_unique_per_bar_side_and_setup(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)

    class _P:
        entry_type = "pullback"

    class _D:
        direction = "LONG"

    class _B:
        last_bar_4h = "2026-01-01 00:00:00"
    a = eng._signal_id("ETH/USDT", "USDM_PERP", _D(), _P(), _B())
    _B.last_bar_4h = "2026-01-01 04:00:00"
    b = eng._signal_id("ETH/USDT", "USDM_PERP", _D(), _P(), _B())
    _D.direction = "SHORT"
    c = eng._signal_id("ETH/USDT", "USDM_PERP", _D(), _P(), _B())
    _P.entry_type = "breakout"
    d = eng._signal_id("ETH/USDT", "USDM_PERP", _D(), _P(), _B())
    assert len({a, b, c, d}) == 4, "bar/taraf/setup değişimi yeni sinyal kimliği üretmeli"


# =========================================================================== statik sözleşme
def test_engine_uses_opportunity_assessment_in_runtime_path():
    text = _src("engine_v3.py")
    assert "_assess_opportunities(" in text and "conservative_net_edge_r" in text
    assert "NEGATIVE_NET_EDGE" in text and "RISK_CAPACITY_BLOCKED" in text
    tree = ast.parse(text)
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_assess_opportunities" in calls and "_signal_id" in calls


def test_research_candidate_changes_at_most_one_parameter():
    from tradingbot.learn.policy import MAX_CHANGES_PER_CANDIDATE
    assert MAX_CHANGES_PER_CANDIDATE == 1

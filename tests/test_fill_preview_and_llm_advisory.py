"""Üretim eşdeğerliği: (a) fill önizlemesi = gerçek fill, (b) LLM advisory hard veto veremez.

Denetimde kalan iki gerçek açık:

* `engine_v3._execution_entry()` kendi YAKLAŞIK kayma formülünü üretiyordu. Futures defteri kendi
  `SlippageModel.fill_price()` + price-tick kuantizasyon yolunu, spot defteri ise `(ask|bid) or last`
  seçimini ve spot kayma modelini kullanıyor. Özellikle spotta ask-last farkı sabit kaymadan büyükse
  RiskEngine gerçekleşecek girişi YANLIŞ görüyordu. Artık her iki defter yan etkisiz ortak bir
  `market_fill_price()` sunuyor; hem gerçek açılış hem de risk önizlemesi AYNI fonksiyonu çağırıyor.
* `head.py` içindeki `llm_advice["veto"] → rep.veto = True` yolu merkezi `decision_gates.GATES`
  sınıflandırmasını ATLIYOR ve modelin kayıtlı/deterministik güvenlik kanıtı olmadan planı geçersiz
  kılmasına izin veriyordu. Artık LLM yalnız kayıtlı `RED_TEAM_SOFT_PENALTY` üretebilir.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_coinhead as T  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.accounting import (AmountType, FuturesLedgerV2, MarketType, Side, SizeSpec,  # noqa: E402
                                   SlippageModel, SpotLedger, SymbolFilters, TickData)
from tradingbot.coinhead import CoinHead, CoinHeadConfig  # noqa: E402
from tradingbot.decision_gates import GATES, HARD_SAFETY, is_known  # noqa: E402

D = Decimal


def _filters(symbol="ETH/USDT", *, market=MarketType.USDM_PERP, price_tick="0.01", qty_step="0.001") -> SymbolFilters:
    return SymbolFilters(symbol=symbol, market_type=market, price_tick=D(price_tick), qty_step=D(qty_step),
                         min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _fut(**kw) -> FuturesLedgerV2:
    kw.setdefault("slippage", SlippageModel(fixed_bps=D("3")))
    return FuturesLedgerV2(D("100000"), max_positions=None, **kw)


# ===================================================================== 1) FUTURES preview == fill
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_futures_preview_matches_real_fill(side):
    """Futures LONG/SHORT: önizleme fiyatı gerçek fill fiyatına BİREBİR eşit."""
    led = _fut()
    f = _filters()
    tick = TickData(last=D("2000"), mark=D("2000"))
    preview = led.market_fill_price("ETH/USDT", side, D("2000"), filters=f, tick=tick)
    pos = led.open("ETH/USDT", side, D("2000"), SizeSpec(D("4000"), AmountType.NOTIONAL, 2),
                   stop=D("1800") if side == "LONG" else D("2200"), targets=[D("2400") if side == "LONG" else D("1600")],
                   filters=f, tick=tick)
    assert pos is not None, led.last_reject_reason
    assert pos.entry_avg == preview, (side, float(pos.entry_avg), float(preview))
    # Kayma yönü aleyhte: LONG yukarı, SHORT aşağı.
    assert (preview > D("2000")) if side == "LONG" else (preview < D("2000"))


def test_futures_preview_matches_fill_with_bid_ask_spread_and_price_tick():
    """Yarım-spread bileşeni + price-tick kuantizasyonu önizlemede de birebir uygulanır."""
    led = _fut(slippage=SlippageModel(fixed_bps=D("3"), spread_half=True))
    f = _filters(price_tick="0.5")                      # kaba tick: yuvarlama görünür olsun
    tick = TickData(last=D("2000"), mark=D("2000"), bid=D("1996"), ask=D("2004"))   # 8 birim spread
    preview = led.market_fill_price("ETH/USDT", "LONG", D("2000"), filters=f, tick=tick)
    assert preview % D("0.5") == 0, f"price-tick'e yuvarlanmalı: {preview}"
    pos = led.open("ETH/USDT", "LONG", D("2000"), SizeSpec(D("8000"), AmountType.NOTIONAL, 2),
                   stop=D("1800"), targets=[D("2400")], filters=f, tick=tick)
    assert pos is not None, led.last_reject_reason
    assert pos.entry_avg == preview
    # Naif "ref × (1 + 3bps)" formülü bu tick'te YANLIŞ olurdu (yarım spread + tick yuvarlaması).
    assert preview != D("2000") * (D("1") + D("3") / D("10000"))


def test_futures_preview_matches_fill_with_coarse_qty_step():
    """Qty-step yuvarlaması AŞAĞI yapılır → gerçekleşen risk önizlenenden büyük OLAMAZ."""
    led = _fut()
    f = _filters(qty_step="0.1")
    tick = TickData(last=D("2000"), mark=D("2000"))
    preview = led.market_fill_price("ETH/USDT", "LONG", D("2000"), filters=f, tick=tick)
    notional = D("4000")
    pos = led.open("ETH/USDT", "LONG", D("2000"), SizeSpec(notional, AmountType.NOTIONAL, 2),
                   stop=D("1800"), targets=[D("2400")], filters=f, tick=tick)
    assert pos is not None, led.last_reject_reason
    assert pos.entry_avg == preview
    assert pos.qty % D("0.1") == 0 and pos.qty <= notional / preview
    predicted_risk = notional * abs(preview - D("1800")) / preview
    real_risk = abs(pos.entry_avg - D("1800")) * pos.qty
    assert real_risk <= predicted_risk + D("1e-9"), (float(real_risk), float(predicted_risk))


# ===================================================================== 2) SPOT preview == fill
def test_spot_preview_matches_real_fill_and_uses_ask_not_last():
    """SPOT BUY: önizleme `ask`+kayma kullanır ve gerçek fill'e eşittir (ask-last farkı 3 bps'i aşsa da)."""
    led = SpotLedger(D("100000"), slippage=SlippageModel(fixed_bps=D("3")))
    # ask, last'tan %1 yukarıda: sabit 3 bps kaymadan ÇOK büyük fark.
    tick = TickData(last=D("2000"), bid=D("1990"), ask=D("2020"))
    preview = led.market_fill_price("ETH/USDT", Side.BUY, tick=tick)
    naive = D("2000") * (D("1") + D("3") / D("10000"))
    assert preview > naive, "eski yaklaşık formül ask'ı görmezdi"
    order = led.market_buy("ETH/USDT", quote_amount=D("4000"), tick=tick, ref_price=D("2000"))
    assert str(order.status).upper().endswith("FILLED"), order.status
    assert order.avg_fill_price == preview, (float(order.avg_fill_price), float(preview))


def test_spot_preview_falls_back_to_ref_price_exactly_like_place_order():
    """Tick yoksa `ref_price` kullanılır — `place_order` ile AYNI öncelik."""
    led = SpotLedger(D("100000"), slippage=SlippageModel(fixed_bps=D("3")))
    preview = led.market_fill_price("ETH/USDT", Side.BUY, ref_price=D("2000"))
    order = led.market_buy("ETH/USDT", quote_amount=D("4000"), ref_price=D("2000"), tick=TickData(last=D("2000")))
    assert order.avg_fill_price == preview
    assert led.market_fill_price("ETH/USDT", Side.BUY) == 0, "fiyat yoksa 0 (yan etki yok)"


def test_preview_is_side_effect_free():
    """Önizleme defteri DEĞİŞTİRMEZ: bakiye, pozisyon, emir, seq aynı kalır."""
    led = _fut()
    before = (led.wallet_balance, len(led.positions), led.seq, len(led.entries))
    for _ in range(5):
        led.market_fill_price("ETH/USDT", "LONG", D("2000"), filters=_filters(), tick=TickData(last=D("2000")))
    assert (led.wallet_balance, len(led.positions), led.seq, len(led.entries)) == before
    spot = SpotLedger(D("10000"))
    s_before = (spot.cash, spot.seq, len(spot.open_orders))
    spot.market_fill_price("ETH/USDT", Side.BUY, tick=TickData(last=D("2000"), ask=D("2001")))
    assert (spot.cash, spot.seq, len(spot.open_orders)) == s_before


# ===================================================================== 3) motor: önizleme = gerçekleşen
def test_engine_risk_preview_entry_equals_the_filled_entry(tmp_path, monkeypatch):
    """Motorun RiskEngine'e verdiği entry, defterde gerçekleşen entry ile birebir eşleşir."""
    from test_risk_capacity_and_gates import EQUITY, _force_final_risk_pct, _force_triggers, _risk_log
    eng = _engine(tmp_path, monkeypatch, symbols=4, equity=EQUITY)
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 - i * 0.1 for i, s in enumerate(eng.cfg.coins)})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions, "senaryo geçersiz: hiç pozisyon açılmadı"
    log = {r["symbol"]: r for r in _risk_log(eng) if r.get("execution_entry") is not None}
    for sym, pos in eng.ledger2.positions.items():
        assert float(pos.entry_avg) == pytest.approx(log[sym]["execution_entry"], rel=1e-12), sym


def test_engine_real_open_risk_never_exceeds_the_previewed_risk(tmp_path, monkeypatch):
    """Fill sonrası GERÇEK açık risk, risk kontrolünde öngörülen değeri AŞMAZ (qty aşağı yuvarlanır)."""
    from test_risk_capacity_and_gates import EQUITY, _force_final_risk_pct, _force_triggers, _open_risk, _risk_log
    eng = _engine(tmp_path, monkeypatch, symbols=4, equity=EQUITY)
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 - i * 0.1 for i, s in enumerate(eng.cfg.coins)})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    log = {r["symbol"]: r for r in _risk_log(eng) if r.get("final_risk_usdt") is not None}
    total_predicted = 0.0
    for sym, pos in eng.ledger2.positions.items():
        real = abs(float(pos.entry_avg) - float(pos.stop)) * float(pos.qty)
        predicted = log[sym]["final_risk_usdt"]
        total_predicted += predicted
        assert real <= predicted + 1e-6, (sym, real, predicted)
        assert real == pytest.approx(predicted, rel=0.02), (sym, real, predicted)
    assert _open_risk(eng) <= total_predicted + 1e-6
    assert _open_risk(eng) <= EQUITY * eng.profile.max_total_open_risk_pct / 100.0 + 1e-6


def test_engine_does_not_reimplement_slippage():
    """Motor kendi yaklaşık kayma formülünü ÜRETMEZ — defterin fill API'sini çağırır."""
    src = (Path(__file__).resolve().parents[1] / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    body = src[src.index("def _execution_entry"):src.index("def _trigger_fired")]
    assert "market_fill_price" in body
    assert "slippage_pct" not in body and "1.0 +" not in body, "yaklaşık formül geri gelmiş"


# ===================================================================== 4) LLM advisory
def _decide(**advice_kw):
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = T.frames(seed=5, drift=0.0015)
    reports, brief = T.legacy(fr)
    inp = T._inputs(fr, reports, brief)
    inp.llm_advice = dict(advice_kw) if advice_kw else None
    return CoinHead("ETH/USDT", cfg).decide(inp)


def test_llm_veto_without_hard_evidence_keeps_the_plan_valid_and_only_shrinks_size():
    """LLM `{"veto": true}` dese fakat hard safety kanıtı yoksa plan GEÇERLİ kalır, yalnız soft ceza alır."""
    base = _decide()
    assert base.is_actionable, "senaryo geçersiz: taban karar işlenebilir olmalı"
    d = _decide(veto=True, veto_reasons=["model contradiction", "narrative risk"])
    assert d.is_actionable, "LLM advisory tek başına planı geçersiz YAPAMAZ"
    assert not d.vetoes, d.vetoes
    assert d.active_plan.valid and d.active_plan.invalid_reason == ""
    assert "RED_TEAM_SOFT_PENALTY" in d.active_plan.soft_flags
    rep = next(r for r in d.specialist_reports if r.agent_name.startswith("red_team_veto"))
    assert rep.veto is False
    meta = (rep.metrics or {}).get("llm_advisory") or {}
    assert meta.get("can_hard_veto") is False and meta.get("applied_as") == "RED_TEAM_SOFT_PENALTY"
    assert "model contradiction" in " ".join(rep.warnings)          # telemetri korunur


def test_llm_schema_invalid_stays_hard_fail_closed():
    """Deterministik şema hatası (`LLM_SCHEMA_INVALID`) SERT kalır — model metni değil, doğrulayıcı üretir."""
    d = _decide(schema_invalid=True)
    assert not d.is_actionable, d.verdict
    assert any("LLM_SCHEMA_INVALID" in v for v in d.vetoes), d.vetoes
    assert GATES["LLM_SCHEMA_INVALID"].cls == HARD_SAFETY


def test_unknown_llm_reason_never_becomes_a_gate_code():
    """LLM metnindeki serbest kod registry'ye SIZAMAZ ve soft_flags'e kod olarak YAZILMAZ."""
    bogus = "KILL_SWITCH_ACTIV"                       # kayıtlı DEĞİL (gerçekçi yazım hatası)
    assert not is_known(bogus)
    d = _decide(veto=True, veto_reasons=[bogus, "TOTAL_OPEN_RISK", "MADE_UP_CODE"])
    for code in d.active_plan.soft_flags:
        assert is_known(code), f"kayıtsız kod soft_flags'e sızdı: {code}"
    assert bogus not in d.active_plan.soft_flags and "MADE_UP_CODE" not in d.active_plan.soft_flags
    # LLM, kayıtlı bir SERT kodun adını yazsa bile o kapıyı TETİKLEYEMEZ.
    assert "TOTAL_OPEN_RISK" not in d.active_plan.soft_flags and not d.vetoes
    assert d.is_actionable
    assert not is_known(bogus), "registry LLM metninden büyümemeli"


def test_llm_cannot_change_mode_risk_limits_leverage_or_open_positions():
    """LLM advisory; mod, risk limiti, kaldıraç ya da açık pozisyonlara DOKUNAMAZ."""
    from tradingbot.risk.profiles import resolve_profile
    p = resolve_profile("PAPER_RESEARCH")
    plain = _decide()
    hostile = _decide(veto=True, veto_reasons=["x"], decision_support="STRONG_BUY",
                      recommended_action="PROCEED", suggested_leverage=50,
                      risk_per_trade_pct=99.0, mode="LIVE", size_multiplier=10.0)
    # Kaldıraç ve boyut plandan gelir; LLM alanları KULLANILMAZ.
    assert hostile.leverage == plain.leverage <= p.futures_max_leverage
    assert hostile.notional == pytest.approx(plain.notional)
    assert resolve_profile("PAPER_RESEARCH").risk_per_trade_pct == 2.0
    # LLM açık pozisyon yolunu da değiştiremez: açık pozisyon varken karar HOLD/EXIT/REDUCE kalır.
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = T.frames(seed=5, drift=0.0015)
    reports, brief = T.legacy(fr)
    inp = T._inputs(fr, reports, brief, portfolio={"open_position": {"side": "LONG", "qty": 1.0, "stop": 1.0}})
    inp.llm_advice = {"veto": True, "recommended_action": "PROCEED", "close_position": True}
    d = CoinHead("ETH/USDT", cfg).decide(inp)
    assert d.verdict.value in ("HOLD", "EXIT", "REDUCE") and d.notional == 0.0
    # Açık pozisyonun kendisi de dokunulmadan geri döner (entry/qty/stop LLM'den etkilenmez).
    assert d.stop == 0.0 and not d.targets


def test_llm_noop_provider_behaviour_is_unchanged():
    """Provider `noop` davranışı değişmedi: advisory yok → karar birebir aynı."""
    from tradingbot.llm.provider import NOOP_ADVICE_JSON, NoOpLLMProvider
    from tradingbot.llm.schema import LLMAdvice
    prov = NoOpLLMProvider()
    assert prov.name == "noop"
    assert NOOP_ADVICE_JSON.get("veto") is False, NOOP_ADVICE_JSON
    failed = LLMAdvice.failed_closed(provider="noop")
    assert failed.veto is False and failed.allows_open is False and failed.confidence == 0.0
    a, b = _decide(), _decide()
    assert a.verdict == b.verdict and a.notional == pytest.approx(b.notional)
    assert a.active_plan.soft_flags == b.active_plan.soft_flags

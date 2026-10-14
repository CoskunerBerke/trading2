# -*- coding: utf-8 -*-
"""BOT KARNESİ (2026-10-03, sahip kararı): «kayda alınan ekstra» ayrı sınıf ve aylık hedef raporu (yalnız rapor).

* Öğrenme modunun `record_selectivity` kipinde AÇILMAYAN seçicilik-ekstra adaylar (karşı-olgusal ilk nedeni
  LEARNING_RECORD_ONLY) defter başına ayrı sütundur (kayıt sayısı / etiketlenenlerin net ort. R); «karşı-olgusal» sütunu
  onları içermez.
* Aylık hedef: içinde bulunulan UTC ayı ve son 30 gün, kapanan işlemlerin ücret/kayma/funding SONRASI neti, başlangıç
  bakiyesinin %'si, işlem sayısı ve +%1 hedefe uzaklık. Kâr iddiası yok; yalnız komut satırı yazar.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec
from tradingbot.learning_mode import LEARNING_RECORD_ONLY as RO

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


# ============================================================================ karne
_spec = importlib.util.spec_from_file_location("bot_scorecard_roe", ROOT / "scripts" / "bot_scorecard.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _ledger(path: Path, trades: list[tuple[datetime, float]], equity: str = "1000") -> None:
    led = FuturesLedgerV2(D(equity))
    for t, px in trades:
        assert led.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=D("95"),
                        targets=[D("110")], now=t) is not None
        assert led.close_manual("ETH/USDT", D(str(px)), now=t + timedelta(minutes=30)) is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    led.save(path)


def _cf(i, *, reasons, r_net=None):
    o = None if r_net is None else {"r_multiple": r_net + 0.1, "r_net": r_net, "label_version": "cf_label_v3"}
    return {"id": "c%d" % i, "book": "t2_trend_regime", "reason_not_opened": list(reasons), "outcome": o}


def test_scorecard_shows_recorded_extras_as_their_own_class(tmp_path):
    state = tmp_path / "state"
    _ledger(state / "strategy_paper" / "futures_ledger.json", [(T0 + timedelta(hours=i), 104.0) for i in range(3)])
    (state / "strategy_paper" / "counterfactual_trades.json").write_text(json.dumps({"trades": [
        _cf(1, reasons=["TOTAL_OPEN_RISK"], r_net=1.0), _cf(2, reasons=["POSITION_OPEN"], r_net=-1.0),
        _cf(3, reasons=[RO, "BOOK_UNIVERSE"], r_net=-0.5), _cf(4, reasons=[RO, "STRUCTURE_WAIT_TRIGGER"], r_net=-0.3),
        _cf(5, reasons=[RO, "NEGATIVE_NET_EDGE"])]}), encoding="utf-8")
    (state / "learning_mode.json").write_text(json.dumps({"since": T0.isoformat()}), encoding="utf-8")
    card = S.scorecard(state)
    t2 = card["learning_mode"]["books"]["strategy_paper"]
    cf, rec = t2["counterfactual"], t2[S.RECORDED_EXTRA]
    assert (cf["recorded"], cf["n_net"], cf["mean_r"]) == (2, 2, 0.0), "karşı-olgusal sütunu yalnız-kayıtları İÇERMEZ"
    assert (rec["recorded"], rec["labeled"], rec["pending"], rec["n_net"]) == (3, 2, 1, 2)
    assert rec["mean_r"] == pytest.approx(-0.4) and rec["in_pnl"] is False
    txt = S.render(card)
    assert "kayda alınan ekstra" in txt and "3 / -0.40" in txt and "2/2 etiketli" in txt
    # yalnız-kayıt kaydı olmayan defter: sütun «0 / —», karşı-olgusal kartı eskisiyle aynı
    assert S.counterfactual_card(state / "strategy_paper" / "counterfactual_trades.json", record_only=None)["recorded"] == 5


def test_monthly_target_report_is_net_of_costs_per_book_and_makes_no_profit_claim(tmp_path, capsys):
    state = tmp_path / "state"
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    # T2: ekimde 2 kazanç (+4 brüt), eylülde (son 30 gün içinde) 1 kazanç; 30 günden eski 1 zarar
    _ledger(state / "strategy_paper" / "futures_ledger.json",
            [(datetime(2026, 9, 1, 1, tzinfo=UTC), 90.0), (datetime(2026, 9, 25, 1, tzinfo=UTC), 104.0),
             (datetime(2026, 10, 2, 1, tzinfo=UTC), 104.0), (datetime(2026, 10, 15, 1, tzinfo=UTC), 104.0)])
    # M2: bu ay işlem yok
    _ledger(state / "strategy_paper_m2" / "futures_ledger.json", [(datetime(2026, 8, 1, 1, tzinfo=UTC), 104.0)])
    mt = S.monthly_target(state, now=now)
    t2 = mt["books"]["strategy_paper"]
    led = FuturesLedgerV2.load(state / "strategy_paper" / "futures_ledger.json")
    pnl = {str(t.closed_at)[:10]: float(t.pnl) for t in led.history}
    assert t2["month"]["n"] == 2 and t2["last_30d"]["n"] == 3
    m_net = pnl["2026-10-02"] + pnl["2026-10-15"]
    assert t2["month"]["net_usdt"] == pytest.approx(m_net, abs=1e-4) and m_net < 8.0, "ücret/kayma SONRASI net"
    assert t2["month"]["net_pct"] == pytest.approx(m_net / 1000 * 100, abs=1e-3)
    assert t2["month"]["gap_to_target_pct"] == pytest.approx(t2["month"]["net_pct"] - 1.0, abs=1e-3)
    assert t2["last_30d"]["net_usdt"] == pytest.approx(m_net + pnl["2026-09-25"], abs=1e-4)
    assert t2["month"]["fees_usdt"] > 0 and mt["month"] == "2026-10" and mt["target_pct"] == 1.0
    m2 = mt["books"]["strategy_paper_m2"]
    assert (m2["month"]["n"], m2["month"]["net_pct"], m2["last_30d"]["n"]) == (0, 0.0, 0)
    assert m2["month"]["gap_to_target_pct"] == -1.0
    assert S.main(["--state", str(state), "--now", now.isoformat()]) == 0
    txt = capsys.readouterr().out
    assert "AYLIK HEDEF (+%1/ay, yalnız rapor)" in txt and "hedefe 1.00 puan var" in txt
    assert "kâr iddiası ya da garantisi değildir" in txt and "Açık pozisyonların gerçekleşmemiş sonucu dahil değildir" in txt
    assert "monthly_target" not in S.scorecard(state), "scorecard() sözlüğü değişmez (yalnız komut satırı yazar)"
    assert S.main(["--state", str(state), "--now", "dün"]) == 2

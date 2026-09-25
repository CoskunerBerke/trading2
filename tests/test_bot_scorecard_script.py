# -*- coding: utf-8 -*-
"""Bot karnesi betiği: salt okunur, maliyet sonrası R, dürüst hüküm (az işlemde hüküm yok)."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bot_scorecard", ROOT / "scripts" / "bot_scorecard.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _ledger(path: Path, exits: list[float]) -> None:
    led = FuturesLedgerV2(D("1000"))
    for i, px in enumerate(exits):
        t = T0 + timedelta(hours=i)
        assert led.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=D("95"),
                        targets=[D("110")], now=t) is not None, led.last_reject_reason
        assert led.close_manual("ETH/USDT", D(str(px)), now=t + timedelta(minutes=30)) is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    led.save(path)


def test_scorecard_verdicts_follow_the_evidence_and_never_write(tmp_path):
    state = tmp_path / "state"
    _ledger(state / "futures_ledger.json", [104.0] * 36 + [96.0] * 4)          # ana bot: net kâr, 40 işlem
    _ledger(state / "strategy_paper_m2" / "futures_ledger.json", [95.0] * 35)   # M2: istikrarlı zarar
    _ledger(state / "strategy_paper" / "futures_ledger.json", [104.0] * 10)     # T2: az işlem
    before = {p: p.read_bytes() for p in state.rglob("*.json")}
    card = S.scorecard(state)
    b = card["books"]
    assert b["main"]["verdict"] == S.V_WIN and b["main"]["r"]["n"] == 40
    assert b["strategy_paper_m2"]["verdict"] == S.V_LOSS and b["strategy_paper_m2"]["net_usdt"] < 0
    assert b["strategy_paper"]["verdict"] == S.V_THIN, "30 işlemden azsa hüküm verilmez"
    assert b["main"]["fees_usdt"] > 0 and b["main"]["r"]["mean_r"] < 0.8, "R maliyet SONRASI (brüt 0,8 R'den küçük)"
    assert {p: p.read_bytes() for p in state.rglob("*.json")} == before, "karne defterlere yazmaz"
    txt = S.render(card)
    assert "Ana bot" in txt and "M2 (TSMOM28)" in txt and "VERİ YETERSİZ" in txt


def test_since_filter_and_cli_json(tmp_path, capsys):
    state = tmp_path / "state"
    _ledger(state / "pattern_trader" / "futures_ledger.json", [104.0] * 5)
    out = tmp_path / "karne.json"
    assert S.main(["--state", str(state), "--since", "2026-09-01T03", "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["books"]["pattern_trader"]["r"]["n"] == 2 and doc["books"]["pattern_trader"]["closed_trades_total"] == 5
    assert "Formasyon" in capsys.readouterr().out
    assert S.main(["--state", str(tmp_path / "yok")]) == 2

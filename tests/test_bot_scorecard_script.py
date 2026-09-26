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


def _candle_ledger(path: Path, trades: list[tuple[str, float, dict]]) -> None:
    """C4 defteri: `candle:<id>` kurulumlu işlemler, girişteki varyasyon anlık görüntüsüyle (`features.candle_variation`)."""
    led = FuturesLedgerV2(D("1000"))
    for i, (vid, px, snap) in enumerate(trades):
        t = T0 + timedelta(hours=i)
        assert led.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=D("95"), targets=[D("110")],
                        setup_type=f"candle:{vid}", features={"candle_variation": dict(snap, id=vid)}, now=t) is not None
        assert led.close_manual("ETH/USDT", D(str(px)), now=t + timedelta(minutes=30)) is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    led.save(path)


def test_candle_variations_rows_observation_mark_and_lab_drift(tmp_path):
    state = tmp_path / "state"
    _ledger(state / "strategy_paper_m2" / "futures_ledger.json", [95.0] * 35)
    before_m2 = S.book_card(state / "strategy_paper_m2" / "futures_ledger.json")
    before_txt = S.render(S.scorecard(state))
    lab = {"definition_sha": "abcd" * 4, "lab_verdict": "GÜÇLÜ ADAY", "lab_oos_mean_r": 0.21, "lab_oos_ci95": [0.05, 0.37],
           "observation": False}
    weak = {"definition_sha": "ef01" * 4, "lab_verdict": "ZAYIF İZ", "lab_oos_mean_r": 0.08, "lab_oos_ci95": [-0.1, 0.25],
            "observation": True}
    _candle_ledger(state / "strategy_paper_candle4h" / "futures_ledger.json",
                   [("CV001_A", 96.0, lab)] * 32 + [("CV002_B", 104.0, weak)] * 6)
    card = S.scorecard(state)
    c4 = card["books"]["strategy_paper_candle4h"]
    assert c4["name"] == "C4 Mum varyasyonları (PAPER)" and c4["r"]["n"] == 38
    bv = c4["by_variation"]
    assert list(bv) == ["CV001_A", "CV002_B"] and set(c4["by_setup"]) == {"candle:CV001_A", "candle:CV002_B"}
    a, b = bv["CV001_A"], bv["CV002_B"]
    assert a["n"] == 32 and a["mean_r"] < 0 and a["verdict"] == S.V_LOSS and a["observation"] is False
    assert a["lab_oos_ci95"] == [0.05, 0.37] and a["lab_oos_mean_r"] == 0.21 and a["definition_sha"] == "abcd" * 4
    assert a["flags"] == ["LAB_DRIFT"], "30+ işlemde PAPER ortalaması laboratuvar OOS aralığının altında"
    assert b["n"] == 6 and b["mean_r"] > 0 and b["verdict"] == S.V_THIN and b["observation"] is True
    assert b["flags"] == [], "az işlemde sapma bayrağı yok"
    txt = S.render(card)
    assert "varyasyon CV001_A:" in txt and "LAB_DRIFT" in txt and "lab OOS 0.21 [0.05, 0.37]" in txt
    assert "varyasyon CV002_B (gözlem, kanıtlanmadı):" in txt
    # diğer defterlerin çıktısı değişmedi
    assert "by_variation" not in card["books"]["strategy_paper_m2"]
    assert S.book_card(state / "strategy_paper_m2" / "futures_ledger.json") == before_m2
    m2 = lambda t: [ln for ln in t.splitlines() if ln.startswith("M2 (TSMOM28)")]  # noqa: E731
    assert m2(txt) and m2(txt) == m2(before_txt), "M2 satırları aynı"
    assert "varyasyon" not in before_txt, "mum varyasyonu işlemi olmayan karnede yeni satır yok"

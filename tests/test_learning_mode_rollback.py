# -*- coding: utf-8 -*-
"""ÖĞRENME MODU — GERİ ALMA HAZIRLIĞI (2026-09-28, öğrenme modu; ikinci doğrulama turu).

Eski kod Formasyon'un bekleyen planlarını evren ayrımı yapmadan doldurur: öğrenmenin kurduğu protokol DIŞI plan (S9) ya da
likidite beklemesindeki plan (D16) geri alınan kodda sıradan protokol işlemi olurdu. `scripts/learning_mode_rollback_prep.py`
yalnız bu planları CANCELLED yapar; açık pozisyonun planına, terminal planlara ve protokol içi normal planlara dokunmaz.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.pattern_trader.strategy import (PL_AWAITING, PL_CANCELLED, PL_MANAGED, PL_RISK_CHECK,  # noqa: E402
                                                PL_TRIGGERED, TERMINAL)


def _load():
    spec = importlib.util.spec_from_file_location("lm_rollback_prep", ROOT / "scripts" / "learning_mode_rollback_prep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plans(tmp_path: Path) -> Path:
    plans = {
        "lab_trig": {"plan_id": "lab_trig", "symbol": "LNG/USDT", "status": PL_TRIGGERED, "in_lab_universe": False,
                     "liquidity_wait": {"pending": True, "codes": ["DEPTH_UNKNOWN"]}},
        "lab_await": {"plan_id": "lab_await", "symbol": "AVAX/USDT", "status": PL_AWAITING, "in_lab_universe": False},
        "liq_wait": {"plan_id": "liq_wait", "symbol": "BTC/USDT", "status": PL_TRIGGERED, "in_lab_universe": True,
                     "liquidity_wait": {"pending": True}},
        "risk_chk": {"plan_id": "risk_chk", "symbol": "XRP/USDT", "status": PL_RISK_CHECK, "in_lab_universe": False},
        "normal": {"plan_id": "normal", "symbol": "ETH/USDT", "status": PL_AWAITING, "in_lab_universe": True},
        "legacy": {"plan_id": "legacy", "symbol": "SOL/USDT", "status": PL_TRIGGERED},     # öğrenmesiz (alan yok)
        "open_pos": {"plan_id": "open_pos", "symbol": "DOT/USDT", "status": PL_MANAGED, "in_lab_universe": False},
        "done": {"plan_id": "done", "symbol": "ADA/USDT", "status": PL_CANCELLED, "in_lab_universe": False},
    }
    p = tmp_path / "pattern_trader" / "plans.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema_version": "pattern_plans_v1", "generated_at": "x", "plans": plans}), encoding="utf-8")
    return p


def test_only_learning_plans_the_old_code_would_fill_are_cancelled(tmp_path):
    mod = _load()
    p = _plans(tmp_path)
    before = json.loads(p.read_text(encoding="utf-8"))
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    res = mod.prepare(tmp_path, now=now)
    assert res["written"] is True and Path(res["backup"]).exists()
    assert json.loads(Path(res["backup"]).read_text(encoding="utf-8")) == before
    assert sorted(c["plan_id"] for c in res["cancelled"]) == ["lab_await", "lab_trig", "liq_wait", "risk_chk"]
    after = json.loads(p.read_text(encoding="utf-8"))["plans"]
    for pid in ("lab_await", "lab_trig", "liq_wait", "risk_chk"):
        pl = after[pid]
        assert pl["status"] == PL_CANCELLED and pl["status"] in TERMINAL
        assert pl["reasons"][-1].startswith("LEARNING_MODE_ROLLBACK:")
        assert pl["status_history"][-1]["at_ms"] == int(now.timestamp() * 1000)
    for pid in ("normal", "legacy", "open_pos", "done"):
        assert after[pid] == before["plans"][pid], pid
    # ikinci koşu: yapılacak iş yok, dosya ve yedek değişmez
    again = mod.prepare(tmp_path, now=now)
    assert again["cancelled"] == [] and again["written"] is False


def test_dry_run_missing_file_and_corrupt_file(tmp_path, capsys):
    mod = _load()
    assert mod.prepare(tmp_path / "nothing") == {"path": str(tmp_path / "nothing" / "pattern_trader" / "plans.json"),
                                                 "cancelled": [], "backup": None, "written": False}
    p = _plans(tmp_path)
    raw = p.read_text(encoding="utf-8")
    assert mod.main(["--state", str(tmp_path), "--dry-run"]) == 0
    assert p.read_text(encoding="utf-8") == raw and "kuru koşu" in capsys.readouterr().out
    p.write_text("{bozuk", encoding="utf-8")
    assert mod.main(["--state", str(tmp_path)]) == 2 and p.read_text(encoding="utf-8") == "{bozuk"

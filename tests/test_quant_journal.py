"""Quant Evaluation V1 — birleşik karar→sonuç günlüğü testleri.

Kapsam: kararlı kimlik, idempotency, duplicate koruması, geriye uyumlu şema (eksik alan → null +
availability), crash/yarım-yazma kurtarma (atomic write), non-finite JSON güvenliği ve
kabul/red/shadow → outcome bağlantısı. Sentetik küçük fixture'lar; ağ/gerçek state yok.
"""
from __future__ import annotations

import json
import math

import pytest

from tradingbot.core import read_json
from tradingbot.quant.journal import (SCHEMA_VERSION, export_journal, row_from_memory,
                                      row_from_shadow, rows_from_memory, unify)


def _entry(tid="t1", symbol="ETH/USDT", **over):
    d = {"kind": "entry", "trade_id": tid, "source": "LIVE_PAPER", "symbol": symbol,
         "market_type": "futures", "side": "LONG", "timeframe": "4h",
         "decision_ts": "2026-01-05T12:00:00+00:00", "last_bar_ts": "2026-01-05T08:00:00+00:00",
         "p_win": 0.61, "expected_r": 0.4, "regime": "trend_up",
         "plan": {"plan_id": "p1", "entry": 100.0, "stop": 95.0, "targets": [104.0, 108.0],
                  "leverage": 3, "notional": 300.0}}
    d.update(over)
    return d


def _exit(tid="t1", r=1.6, net=48.0):
    return {"kind": "exit", "trade_id": tid,
            "outcome": {"exit_reason": "hedef2", "net_pnl": net, "gross_pnl": net + 2.4,
                        "fees": 1.8, "funding": 0.6, "r_multiple": r, "mae_pct": -1.2,
                        "mfe_pct": 8.4, "bars_held": 7}}


def _shadow(plan_id="p9", variant="as_planned", outcome=None):
    return {"id": "shadow_1", "plan_id": plan_id, "symbol": "SOL/USDT", "market_type": "futures",
            "direction": "SHORT", "created_at": "2026-01-06T00:00:00+00:00", "entry": 200.0,
            "stop": 210.0, "targets": [190.0, 180.0], "horizon_bars": 12, "variant": variant,
            "reason_not_opened": ["RISK_BUDGET_EXCEEDED"], "label_ts": "2026-01-08T00:00:00+00:00",
            "tf_minutes": 240, "leverage": 2.0, "outcome": outcome}


def test_stable_identity_and_schema():
    a = row_from_memory(_entry(), _exit())
    b = row_from_memory(_entry(), _exit())
    assert a["decision_id"] == b["decision_id"]
    assert a["schema_version"] == SCHEMA_VERSION
    assert a["accepted"] is True and a["outcome_labeled"] is True
    assert a["outcome_class"] == "WIN" and a["r_multiple"] == pytest.approx(1.6)
    assert a["planned_leverage"] == pytest.approx(3.0)
    s = row_from_shadow(_shadow())
    assert s["accepted"] is False and s["is_counterfactual"] is True
    assert s["reject_reason"] == "RISK_BUDGET_EXCEEDED"
    assert s["decision_id"] != a["decision_id"]


def test_backward_compatible_missing_fields_become_null():
    bare = {"kind": "entry", "trade_id": "old1", "symbol": "BTC/USDT"}
    rec = row_from_memory(bare, None)
    assert rec["p_win"] is None and rec["planned_stop"] is None
    assert rec["availability"]["p_win"] is False
    assert rec["outcome_labeled"] is False and rec["outcome_class"] is None
    # eski kayıt okunabilir kalır; exception yok, uydurma 0 yok
    assert rec["net_pnl"] is None


def test_non_finite_values_are_null_and_flagged():
    rec = row_from_memory(_entry(p_win=float("nan"), plan={"plan_id": "p1", "entry": float("inf"),
                                                           "stop": 95.0, "targets": [float("nan"), 104.0]}), None)
    assert rec["p_win"] is None and rec["planned_entry"] is None
    assert rec["planned_targets"] == [104.0]
    assert any(f.startswith("NON_FINITE:p_win") for f in rec["quality_flags"])
    assert any(f.startswith("NON_FINITE:planned_entry") for f in rec["quality_flags"])
    dumped = json.dumps(export_doc := {"records": [rec]}, allow_nan=False)  # RFC-safe: exception atmamalı
    assert "NaN" not in dumped and "Infinity" not in dumped


def test_duplicate_prevention_and_idempotent_unify():
    mem = [_entry(), _exit(), _entry(), _exit()]          # aynı trade iki kez akmış olsun
    sh = [_shadow(), _shadow()]                            # aynı shadow iki kez
    rows = unify(mem, sh)
    assert len(rows) == 2                                  # 1 memory + 1 shadow
    rows2 = unify(mem, sh)
    assert [r["decision_id"] for r in rows] == [r["decision_id"] for r in rows2]


def test_labeled_record_preferred_over_unlabeled():
    sh_unlabeled = _shadow()
    sh_labeled = _shadow(outcome={"r_multiple": -1.0, "exit_reason": "stop", "bars": 5,
                                  "mae_pct": -5.0, "mfe_pct": 1.0, "won": False})
    rows = unify([], [sh_unlabeled, sh_labeled])
    assert len(rows) == 1 and rows[0]["outcome_labeled"] is True
    assert rows[0]["outcome_class"] == "LOSS"


def test_orphan_exit_does_not_crash_and_is_flagged():
    rows = rows_from_memory([_exit("ghost"), _entry("t1"), _exit("t1")])
    assert len(rows) == 1
    assert any(f.startswith("ORPHAN_EXITS:1") for f in rows[0]["quality_flags"])


def test_export_atomic_and_crash_safe(tmp_path):
    p = tmp_path / "quant_journal.json"
    rows = unify([_entry(), _exit()], [_shadow()])
    doc = export_journal(p, rows)
    assert doc["n_records"] == 2 and doc["n_labeled"] == 1 and doc["n_accepted"] == 1
    on_disk = read_json(p)
    assert on_disk["schema_version"] == SCHEMA_VERSION
    # crash/yarım-yazma simülasyonu: süreç tmp dosyasını yazarken ölmüş olsun —
    # atomic sözleşme gereği yarım tmp ASLA okunmaz, ana dosya bütün kalır.
    before = p.read_bytes()
    (tmp_path / f"{p.name}.tmp-99999").write_text('{"schema_version": "quant_jour', encoding="utf-8")
    assert p.read_bytes() == before
    assert read_json(p)["n_records"] == 2                  # okuma tmp'den etkilenmez
    # idempotent yeniden yazım aynı içeriği üretir (deterministik; zaman damgası yok)
    export_journal(p, rows)
    assert p.read_bytes() == before


def test_decision_outcome_linking_accept_reject_shadow():
    mem = [_entry("t1"), _exit("t1", r=-1.0, net=-30.0), _entry("t2")]   # t2 hâlâ açık
    sh = [_shadow(outcome={"r_multiple": 0.9, "exit_reason": "target", "bars": 9,
                           "mae_pct": -0.5, "mfe_pct": 4.0, "won": True})]
    rows = unify(mem, sh)
    by_ref = {r["outcome_ref"]: r for r in rows}
    assert by_ref["t1"]["outcome_class"] == "LOSS" and by_ref["t1"]["net_pnl"] == pytest.approx(-30.0)
    assert by_ref["t2"]["outcome_labeled"] is False
    shadow = next(r for r in rows if r["source_kind"] == "SHADOW")
    assert shadow["outcome_class"] == "WIN" and shadow["is_counterfactual"] is True
    assert math.isfinite(shadow["r_multiple"])

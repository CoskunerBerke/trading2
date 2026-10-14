# -*- coding: utf-8 -*-
"""Box CF gap audit (scripts/box_cf_gap_audit.py): read-only, standard library on the default path, H1-H10 statistics.

The synthetic state is written with the REAL repo classes: a `FuturesLedgerV2` whose trades come from open/tick/close
(a stop filled at the first observation beyond it, a target exit, a BOX_EOD_FLAT close, a wrong-side 'hedef1'), and a
`CounterfactualRecorder` whose rows are labelled by `label_pending` on synthetic 5m frames with `ExecModel.of_ledger`
(real cf_label_v3 rows, a POSITION_OPEN row with rule_version '..._ms2.22_eod', rows whose 00:00 funding is missing).
Trade memory, learning_mode.json, box_timer.json, protective_monitor.json and the other optional files are hand-written."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.accounting import AmountType, FeeSchedule, FuturesLedgerV2, SizeSpec, SlippageModel, TickData
from tradingbot.learn.shadow import label_with_candles
from tradingbot.learning_cf import CounterfactualRecorder, ExecModel, _decompose, net_outcome

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "box_cf_gap_audit.py"
_spec = importlib.util.spec_from_file_location("box_cf_gap_audit", SCRIPT)
GA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GA)

BOOK = "strategy_paper_box"
UTC = timezone.utc
M5 = 300_000
DAY = 86_400_000
D0 = datetime(2026, 9, 26, tzinfo=UTC)                 # previous day of the first trading day (box day)
DAYS = 4                                               # trading days 09-27 .. 09-30
SYMS = ("AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT", "EEE/USDT", "FFF/USDT")
NAMED = ("GAP/USDT", "TGT/USDT", "TGN/USDT", "EOD/USDT", "WRS/USDT", "BOS/USDT", "REE/USDT", "LAT/USDT")
NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
RULE_LEARN = "n0.1_break_prev_day_low_box_mid_LS_ms0.32_eod"
RULE_BASE = "n0.1_break_prev_day_low_box_mid_LS_ms2.22_eod"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _iso(ms: int) -> str:
    return _dt(ms).isoformat(timespec="seconds")


def _frames(seed: int = 7) -> dict:
    """Deterministic 5m random walks (6 days, open/high/low/close) per symbol."""
    rng = random.Random(seed)
    out = {}
    for k, sym in enumerate(SYMS + NAMED):
        px = 10.0 * (k + 1)
        rows = []
        t = _ms(D0)
        for _ in range((DAYS + 2) * 288):
            o = px
            c = o * (1.0 + rng.gauss(0.0, 0.0025))
            hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, 0.0012)))
            lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, 0.0012)))
            rows.append({"timestamp": t, "open": o, "high": hi, "low": lo, "close": c})
            px, t = c, t + M5
        out[sym] = pd.DataFrame(rows)
    return out


def _ledger() -> FuturesLedgerV2:
    return FuturesLedgerV2(D("1000000"), max_positions=500, enforce_position_cap=False,
                           fees=FeeSchedule(maker_pct=D("0.02"), taker_pct=D("0.05")), slippage=SlippageModel(fixed_bps=D("3")))


def _funding(sym, when):
    """Realized funding: 0.01 % everywhere; the 00:00 rate is 'not yet known' for the counterfactual labeller."""
    return D("0.0001")


def _cf_funding(sym, when):
    return None if when.hour == 0 else D("0.0001")


def _bar(frames, sym, ms):
    df = frames[sym]
    i = int((ms - _ms(D0)) // M5)
    return df.iloc[i]


def _box_mid(frames, sym, day_ms):
    df = frames[sym]
    prev = df[(df["timestamp"] >= day_ms - DAY) & (df["timestamp"] < day_ms)]
    return (float(prev["high"].max()) + float(prev["low"].min())) / 2.0


def _real_trade(led, mem, frames, sym, side, bar_ms, sp_pct, *, targets=None, learning=None, lag_s=7, path="bars"):
    """Open on the close of the signal bar `bar_ms` (+5 min + lag), then tick bar closes (price-only marks) until a stop /
    target or the 00:05 end-of-day flat."""
    sig = _bar(frames, sym, bar_ms)
    entry = float(sig["close"])
    s = 1 if side == "LONG" else -1
    stop = entry * (1 - s * sp_pct / 100.0)
    day0 = bar_ms - bar_ms % DAY
    if targets is None:
        mid = _box_mid(frames, sym, day0)
        targets = [mid] if (mid - entry) * s > 0 else []
    at = _dt(bar_ms + M5 + lag_s * 1000)
    lrn = {"learning_unlocked_by": ["BOX_MIN_STOP_PCT"] if sp_pct < 2.22 else [], "slots": 20, "size_rule": "SLOT"}
    if learning is not None:
        lrn.update(learning)
    pos = led.open(sym, side, D(str(entry)), SizeSpec(D("200"), AmountType.NOTIONAL, 3), stop=D(str(stop)),
                   targets=[D(str(t)) for t in targets], setup_type="box_fade", trigger_text="BOX_FADE",
                   features={"strategy": "b1_box_fade", "learning": lrn}, now=at)
    assert pos is not None, led.last_reject_reason
    mem.append({"kind": "entry", "trade_id": pos.id, "recorded_at": at.isoformat(), "symbol": sym,
                "features": {"strategy": "b1_box_fade", "signal_close": entry, "signal_ts": bar_ms, "stop_at_entry": stop}})
    if path == "none":
        return pos
    t = bar_ms + 2 * M5 + lag_s * 1000
    while t < day0 + DAY:
        px = float(_bar(frames, sym, t - lag_s * 1000 - M5)["close"])
        recs = led.tick({sym: TickData(last=D(str(round(px, 8))))}, now_utc=_dt(t), funding_rate_lookup=_funding)
        if recs:
            return recs[-1]
        t += M5
    close_px = D(str(round(float(_bar(frames, sym, day0 + DAY - M5)["close"]), 8)))
    recs = led.tick({sym: TickData(last=close_px)}, now_utc=_dt(day0 + DAY + 60_000), funding_rate_lookup=_funding)
    if recs:
        return recs[-1]
    return led.close_manual(sym, close_px, reason="BOX_EOD_FLAT", now=_dt(day0 + DAY + 5 * 60_000 + 7_000))


def build_state(tmp_path: Path, *, n_days: int = DAYS, optional: bool = True, archive: bool = True) -> Path:
    state = tmp_path / "state"
    book = state / BOOK
    book.mkdir(parents=True)
    frames = _frames()
    led = _ledger()
    mem: list = []
    rng = random.Random(11)
    sps = (0.35, 0.45, 0.7, 1.4, 2.6, 4.5)
    # --- named real trades (second trading day, inside the counterfactual window) ----------------------------------
    d1 = _ms(D0) + DAY
    d2 = d1 + DAY
    H = 3_600_000

    def tick(sym, at_ms, px, **kw):
        return led.tick({sym: TickData(last=D(str(round(px, 8))))}, now_utc=_dt(at_ms), **kw)
    # GAP: stop filled by GAP_FILL_AT_FIRST_OBSERVATION (one price-only tick far beyond the stop)
    _real_trade(led, mem, frames, "GAP/USDT", "LONG", d2 + 10 * H, 0.4, path="none")
    recs = tick("GAP/USDT", d2 + 10 * H + 2 * M5 + 30_000, float(led.positions["GAP/USDT"].initial_stop) * 0.996)
    assert recs and recs[0].features["exit_fill"]["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION"
    # TGT: target exit above the entry
    c = float(_bar(frames, "TGT/USDT", d2 + 11 * H)["close"])
    _real_trade(led, mem, frames, "TGT/USDT", "LONG", d2 + 11 * H, 1.4, path="none", targets=[c * 1.004])
    recs = tick("TGT/USDT", d2 + 11 * H + 2 * M5 + 9_000, c * 1.006)
    assert recs and recs[0].exit_reason == "hedef1" and float(recs[0].r_multiple) > 0
    # TGN: correct-side target whose net R is slightly negative (costs exceed the small gain) — NOT a wrong-side target
    c = float(_bar(frames, "TGN/USDT", d2 + 11 * H + 6 * M5)["close"])
    _real_trade(led, mem, frames, "TGN/USDT", "LONG", d2 + 11 * H + 6 * M5, 1.4, path="none", targets=[c * 1.0008])
    recs = tick("TGN/USDT", d2 + 11 * H + 8 * M5 + 9_000, c * 1.002)
    assert recs and recs[0].exit_reason == "hedef1" and float(recs[0].r_multiple) < 0
    assert float(recs[0].exit_price) > float(recs[0].entry)
    # EOD: BOX_EOD_FLAT close_manual five minutes after midnight (00:00 funding settled by a 00:01 tick)
    _real_trade(led, mem, frames, "EOD/USDT", "SHORT", d2 + 22 * H, 4.5, path="none", targets=[])
    tick("EOD/USDT", d2 + DAY + 60_000, float(led.positions["EOD/USDT"].entry_avg), funding_rate_lookup=_funding)
    rec = led.close_manual("EOD/USDT", led.positions["EOD/USDT"].entry_avg, reason="BOX_EOD_FLAT", now=_dt(d2 + DAY + 307_000))
    assert rec.exit_reason == "BOX_EOD_FLAT"
    # WRS: wrong-side 'hedef1' — a LONG whose target is BELOW the fill (box_mid from the signal close)
    c = float(_bar(frames, "WRS/USDT", d2 + 12 * H)["close"])
    _real_trade(led, mem, frames, "WRS/USDT", "LONG", d2 + 12 * H, 0.6, path="none", targets=[c * 0.9995])
    fill = float(led.positions["WRS/USDT"].entry_avg)
    recs = tick("WRS/USDT", d2 + 12 * H + 2 * M5 + 9_000, fill)
    assert recs and recs[0].exit_reason == "hedef1" and float(recs[0].exit_price) < fill
    # BOS: stopped inside the entry 5m bar (opened 13:05:07, stopped 13:07:30)
    _real_trade(led, mem, frames, "BOS/USDT", "LONG", d2 + 13 * H, 0.4, path="none", targets=[])
    recs = tick("BOS/USDT", d2 + 13 * H + M5 + 150_000, float(led.positions["BOS/USDT"].initial_stop) * 0.999)
    assert recs and recs[0].exit_reason == "stop"
    # REE: a stop, then a re-entry on the same symbol, side and day
    _real_trade(led, mem, frames, "REE/USDT", "LONG", d2 + 14 * H, 0.4, path="none", targets=[])
    recs = tick("REE/USDT", d2 + 14 * H + 3 * M5, float(led.positions["REE/USDT"].initial_stop) * 0.998)
    assert recs and recs[0].exit_reason == "stop"
    _real_trade(led, mem, frames, "REE/USDT", "LONG", d2 + 15 * H, 0.8)
    # LAT: a 23:50 signal (opened 23:55:07, flattened at 00:05)
    _real_trade(led, mem, frames, "LAT/USDT", "LONG", d2 + DAY - 2 * M5, 0.7, path="none", targets=[])
    tick("LAT/USDT", d2 + DAY + 60_000, float(led.positions["LAT/USDT"].entry_avg), funding_rate_lookup=_funding)
    assert led.close_manual("LAT/USDT", led.positions["LAT/USDT"].entry_avg, reason="BOX_EOD_FLAT",
                            now=_dt(d2 + DAY + 307_000)) is not None
    # --- generated real trades: n_days x 6 symbols x 2 ---------------------------------------------------------------
    for d in range(n_days):
        day0 = d1 + d * DAY
        for k, sym in enumerate(SYMS):
            for j in range(2):
                if sym in led.positions:
                    continue
                hour = 6 + 7 * j + (k % 3)
                bar_ms = day0 + hour * 3_600_000 + 5 * M5 * (k % 4)
                side = "LONG" if (k + j + d) % 2 == 0 else "SHORT"
                _real_trade(led, mem, frames, sym, side, bar_ms, sps[(k + j + d) % len(sps)])
    # one trade still open (occupancy, POSITION_OPEN join)
    last_day = d1 + (n_days - 1) * DAY
    _real_trade(led, mem, frames, "FFF/USDT", "LONG", last_day + 22 * 3_600_000, 0.5, path="none", targets=[])
    led.save(book / "futures_ledger.json")
    (book / "trade_memory.jsonl").write_text("\n".join(json.dumps(r) for r in mem) + "\n{broken\n\n", encoding="utf-8")
    # --- counterfactuals labelled by the real recorder ---------------------------------------------------------------
    cf = CounterfactualRecorder(book / "counterfactual_trades.json", book="b1_box_fade")
    reasons = ("INSUFFICIENT_MARGIN", "MIN_ORDER_CONFLICT", "INSUFFICIENT_MARGIN", "KILL_SWITCH_ACTIVE")
    n = 0
    for d in range(n_days):
        day0 = d1 + d * DAY
        for k, sym in enumerate(SYMS):
            for j in range(3):
                bar_ms = day0 + (15 + 2 * j + (k % 2)) * 3_600_000 + M5 * ((k + j) % 6)
                sig = _bar(frames, sym, bar_ms)
                entry = float(sig["close"])
                side = "LONG" if (k + j + d + 1) % 2 == 0 else "SHORT"
                s = 1 if side == "LONG" else -1
                sp = sps[(k + 2 * j + d) % len(sps)]
                created = _dt(bar_ms + M5 + 7_000)
                mid = _box_mid(frames, sym, day0)
                reason = reasons[n % len(reasons)]
                rule, feats = RULE_LEARN, {"learning": {"policy_grade": False, "why": "POLICY_RESERVE"}, "signal_close": entry}
                if n % 7 == 3:
                    reason, rule, sp = "POSITION_OPEN", RULE_BASE, max(sp, 2.3)
                    feats = {"baseline_blocked_by": ["TOTAL_OPEN_RISK"] if n % 2 else [], "signal_close": entry}
                stop = entry * (1 - s * sp / 100.0)
                h = GA.box_eod_bars(bar_ms, _ms(created))
                ok = cf.record(signal_key="signal_ts:%d" % bar_ms, symbol=sym, direction=side, entry=entry, stop=stop,
                               targets=[mid], reason=reason, created_at=created, tf_minutes=5, horizon_bars=h,
                               label_kind="TARGET_STOP_TIME", features=feats, rule_version=rule)
                assert ok
                n += 1
    # a capacity row on the trading day after the last one is still pending (excluded from CF_NET)
    pend = d1 + n_days * DAY + 2 * 3_600_000
    assert cf.record(signal_key="signal_ts:%d" % pend, symbol="AAA/USDT", direction="LONG",
                     entry=float(_bar(frames, "AAA/USDT", pend)["close"]), stop=float(_bar(frames, "AAA/USDT", pend)["close"]) * 0.99,
                     targets=[], reason="INSUFFICIENT_MARGIN", created_at=_dt(pend + M5 + 7_000), tf_minutes=5,
                     horizon_bars=GA.box_eod_bars(pend, pend + M5 + 7_000), label_kind="TARGET_STOP_TIME", rule_version=RULE_LEARN)
    model = ExecModel.of_ledger(led)
    label_now = _dt(d1 + n_days * DAY + 60_000)
    cf.label_pending({s_: {"5m": frames[s_][frames[s_]["timestamp"] < d1 + n_days * DAY]} for s_ in SYMS}, label_now,
                     exec_model=model, funding_lookup=_cf_funding)
    cf.save()
    (state / "learning_mode.json").write_text(json.dumps({"schema_version": "learning_mode_since_v1",
                                                          "since": (D0 + timedelta(days=1)).isoformat()}), encoding="utf-8")
    if optional:
        (state / "box_timer.json").write_text(json.dumps({
            "lags_s": [3.1, 3.4, 4.0, 9.5], "protect_gaps_s": [58.0, 61.0, 60.2, 75.0, 121.0], "tick_every_s": 60.0,
            "poll_s": 15.0, "missed_bars": 0, "last_eval": {"seconds": 1.4}}), encoding="utf-8")
        first_id = json.loads((book / "futures_ledger.json").read_text(encoding="utf-8"))["history"][0]["id"]
        (state / "protective_monitor.json").write_text(json.dumps({"observations": {"since": D0.isoformat(), "books": {
            BOOK: {"positions": {first_id: {"observations": 3, "sources": {"box_timer": 2, "protective_monitor": 1},
                                            "max_gap_s": 61.0}}}}}}), encoding="utf-8")
        (state / ("%s.json" % BOOK)).write_text(json.dumps({"learning": {"counters": {"counterfactual_recorded": n + 1,
                                                                                      "opened": 60}},
                                                            "rejections": {"INSUFFICIENT_MARGIN": 40, "DATA_FRAME_MISSING_5M": 3}}),
                                               encoding="utf-8")
        (state / "monitoring_gaps.jsonl").write_text(json.dumps({"kind": "MONITORING_GAP", "book": BOOK, "gap_s": 1800.0}) + "\n"
                                                     + json.dumps({"book": "other", "gap_s": 99.0}) + "\n", encoding="utf-8")
        xp = state / "shared_experience"
        xp.mkdir()
        doc = json.loads((book / "counterfactual_trades.json").read_text(encoding="utf-8"))
        h0 = json.loads((book / "futures_ledger.json").read_text(encoding="utf-8"))["history"][0]
        rows = [{"kind": "xp_cf", "book": BOOK, "cf_id": t["id"], "rev": 0,
                 "snapshot": {"h4_trend": "UP" if i % 2 else "DOWN", "h4_vol_regime": "NORMAL", "btc_h4_trend": "UP"}}
                for i, t in enumerate(doc["trades"][:10])]
        rows.append({"kind": "xp_entry", "book": BOOK, "trade_id": h0["id"], "opened_at": h0["opened_at"], "rev": 0,
                     "snapshot": {"h4_trend": "UP", "h4_vol_regime": "HIGH", "btc_h4_trend": "DOWN"}})
        rows.append({"kind": "xp_entry", "book": "strategy_paper", "trade_id": h0["id"], "opened_at": h0["opened_at"],
                     "snapshot": {"h4_trend": "X"}})
        (xp / "experience.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
        (state / "shadow_book.json").write_text(json.dumps({"trades": [
            {"id": "s1", "book": "main", "symbol": "BTC/USDT", "market_type": "USDM_PERP", "created_at": "2026-09-30T00:00:00+00:00",
             "reason_not_opened": ["CHIEF_BLOCKED"], "outcome": {"r_multiple": 0.5, "r_net": 0.4}},
            {"id": "s2", "book": "main", "symbol": "ETH/USDT", "market_type": "SPOT", "created_at": "2026-09-30T12:00:00+00:00",
             "reason_not_opened": ["NEGATIVE_NET_EDGE"]},
            {"id": "s3", "symbol": "ETH/USDT", "created_at": "2026-09-29T12:00:00+00:00"}],
            "meta": {"lm_expired": 2, "lm_dropped": 0, "lm_superseded": 1}}), encoding="utf-8")
    if archive:
        (book / "counterfactual_archive").mkdir()
        (book / "counterfactual_archive" / "seg_000001.jsonl").write_text("{}\n", encoding="utf-8")
    return state


@pytest.fixture(scope="module")
def base_state(tmp_path_factory):
    return build_state(tmp_path_factory.mktemp("gapaudit"))


def _copy(base: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "state"
    shutil.copytree(base, dst)
    return dst


def _run(state: Path, *args, now=NOW, capsys=None):
    rc = GA.main(["--state", str(state), "--boot", "200"] + list(args), now=now)
    out = capsys.readouterr().out if capsys is not None else ""
    return rc, out


def _audit(state: Path, **kw):
    a = GA.build_parser().parse_args(["--state", str(state), "--boot", str(kw.pop("boot", 200))] + kw.pop("argv", []))
    doc, summary, details = GA.Audit(a, now=NOW).run()
    return doc, summary, details


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir")
            for p in sorted(root.rglob("*"))}


def _ledger_doc(state: Path) -> dict:
    return json.loads((state / BOOK / "futures_ledger.json").read_text(encoding="utf-8"))


def _hist_by_symbol(state: Path) -> dict:
    out: dict = {}
    for h in _ledger_doc(state)["history"]:
        out.setdefault(h["symbol"], []).append(h)
    return out


# ----------------------------------------------------------------------------- 1. read-only contract
def test_read_only_and_json_out_contract(base_state, tmp_path, monkeypatch, capsys):
    state = _copy(base_state, tmp_path)
    before = _tree(tmp_path)
    out = tmp_path / "out" / "gap.json"
    out.parent.mkdir()
    rc, _ = _run(state, "--config", str(ROOT / "config.yaml"), "--json-out", str(out), capsys=capsys)
    assert rc == 0
    after = _tree(tmp_path)
    assert {k: v for k, v in after.items() if not k.startswith("out")} == {k: v for k, v in before.items()}
    assert sorted(k for k in after if k.startswith("out")) == ["out", str(Path("out") / "gap.json")]
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == GA.SCHEMA
    # --json-out inside the state (or equal to an input) → exit 2, nothing written
    for bad in (state / "gap.json", state / BOOK / "x.json", ROOT / "config.yaml"):
        rc, _ = _run(state, "--config", str(ROOT / "config.yaml"), "--json-out", str(bad), capsys=capsys)
        assert rc == 2
        assert not (state / "gap.json").exists() and not (state / BOOK / "x.json").exists()
    assert _tree(state) == {k[len("state") + 1:]: v for k, v in before.items() if k.startswith("state" + os.sep)}
    rc, _ = _run(state, "--json-out", str(out.parent), capsys=capsys)     # not writable (a directory) → 2, no traceback
    assert rc == 2
    # no --state and no environment → 2; TRADINGBOT_STATE_DIR is honoured
    monkeypatch.delenv(GA.ENV_STATE, raising=False)
    assert GA.main(["--boot", "20"], now=NOW) == 2
    monkeypatch.setenv(GA.ENV_STATE, str(state))
    assert GA.main(["--boot", "20"], now=NOW) == 0
    assert "OLSAYDI" in capsys.readouterr().out


def test_required_inputs_missing_or_malformed(base_state, tmp_path, capsys):
    state = _copy(base_state, tmp_path)
    (state / BOOK / "counterfactual_trades.json").unlink()
    assert _run(state, capsys=capsys)[0] == 2
    state2 = _copy(base_state, tmp_path / "b")
    (state2 / BOOK / "futures_ledger.json").write_text("{broken", encoding="utf-8")
    assert _run(state2, capsys=capsys)[0] == 3
    (state2 / BOOK / "futures_ledger.json").write_text("[]", encoding="utf-8")
    assert _run(state2, capsys=capsys)[0] == 3
    assert GA.main(["--state", str(tmp_path / "nope")]) == 2
    for bad in (["--since", "garbage"], ["--config", str(tmp_path / "none.yaml")], ["--replay-klines", str(tmp_path / "nodir")]):
        assert _run(base_state, *bad, capsys=capsys)[0] == 2


# ----------------------------------------------------------------------------- 2. decomposition parity
def test_decomposition_parity_with_learning_cf(base_state):
    led = FuturesLedgerV2.load(base_state / BOOK / "futures_ledger.json")
    from tradingbot.learn.shadow import ShadowTrade
    seen = set()
    for rec in led.history:
        stop = float(rec.features["initial_stop"])
        view = ShadowTrade(id="v", plan_id="v", symbol=rec.symbol, market_type="USDM_PERP", direction=rec.side,
                           created_at=rec.opened_at, entry=float(rec.entry), stop=stop, targets=[], horizon_bars=1,
                           variant="as_planned", reason_not_opened=["X"], label_ts=rec.closed_at, tf_minutes=5)
        ref = _decompose(rec, view, 0.0)
        mine = GA.decompose_real(rec.to_dict(), stop)
        for k, v in mine["parts"].items():
            assert round(v, 6) + 0.0 == pytest.approx(ref[k], abs=1e-9), (rec.exit_reason, k)
        assert round(0.0 - mine["a0"], 6) + 0.0 == pytest.approx(ref["path"], abs=1e-9)
        assert mine["a0"] - sum(mine["parts"].values()) == pytest.approx(float(rec.r_multiple), abs=1e-6)
        assert mine["e"] == pytest.approx(float(rec.r_multiple), abs=1e-6)
        seen.add(GA.exit_class(rec.exit_reason))
    assert {"STOP", "TARGET", "EOD"} <= seen
    doc, _s, _d = _audit(base_state)
    assert doc["s0"]["decompose_mismatch"] == 0


# ----------------------------------------------------------------------------- 3. overshoot
def test_overshoot_example_and_real_gap_fill():
    pct_, r_ = GA.overshoot(1, 100.0, 100.0, 99.6, 99.44)
    assert pct_ == pytest.approx(0.16) and r_ == pytest.approx(0.4)
    led = FuturesLedgerV2(D("10000"), fees=FeeSchedule(maker_pct=D("0.02"), taker_pct=D("0.05")),
                          slippage=SlippageModel(fixed_bps=D("0")))
    t0 = datetime(2026, 9, 28, 10, 5, 7, tzinfo=UTC)
    assert led.open("X/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=D("99.6"), now=t0) is not None
    rec = led.tick({"X/USDT": TickData(last=D("99.44"))}, now_utc=t0 + timedelta(minutes=3))[0]
    assert rec.features["exit_fill"]["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION"
    row = GA.real_row(rec.to_dict(), {})
    assert row["ov_pct"] == pytest.approx(0.16) and row["ov_r"] == pytest.approx(0.4)
    assert row["parts"]["exit_fill_model"] == pytest.approx(0.4)


# ----------------------------------------------------------------------------- 4. stratification identity
def test_post_stratification_identity():
    def rows(kind, n_a, n_b):
        return ([{"kind": kind, "r": 1.0, "cell": "A"}] * n_a) + ([{"kind": kind, "r": -0.5, "cell": "B"}] * n_b)
    cf, re_ = rows("cf", 6, 2), rows("re", 4, 12)
    per = GA.aggs({"cf": cf, "re": re_}, lambda r: "*", [GA.feed_mean("k", cell=lambda r: r["cell"])])
    ps = GA.poststrat(per["*"], "k", 3)
    raw = GA.plain_gap(per["*"], "k")
    assert raw == 0.75 and raw != 0.0
    assert ps["within"] == 0.0
    assert ps["mix"] == raw and ps["coverage"] == 1.0 and ps["reweighted_real"] == 0.625
    # a cell with fewer real rows than --min-cell is not covered
    ps2 = GA.poststrat(GA.aggs({"cf": cf, "re": rows("re", 4, 2)}, lambda r: "*",
                               [GA.feed_mean("k", cell=lambda r: r["cell"])])["*"], "k", 3)
    assert ps2["cells"] == 1 and ps2["coverage"] == 0.75 and ps2["within"] == 0.0


# ----------------------------------------------------------------------------- 5. episodes
def test_episodes_and_stop_clusters():
    base = {"kind": "cf", "sym": "X/USDT", "day": "2026-09-28", "side": 1, "stop": 99.5}
    rows = [dict(base, id="a", t0=1, r=1.0), dict(base, id="b", t0=2, r=0.0), dict(base, id="c", t0=3, r=-0.4),
            dict(base, id="d", t0=4, r=2.0, sym="Y/USDT", stop=10.0)]
    ep = GA.episodes(rows)
    assert len(ep) == 2 and max(len(v) for v in ep.values()) == 3
    means = [GA.mean(x["r"] for x in v) for v in ep.values()]
    assert GA.mean(means) == pytest.approx((0.2 + 2.0) / 2)
    assert [r["id"] for r in ep[("X/USDT", "2026-09-28", 1)]] == ["a", "b", "c"]
    cl = GA.stop_clusters(rows)
    assert len(cl) == 2 and max(cl.values()) == 3


# ----------------------------------------------------------------------------- 6. bootstrap
def test_bootstrap_reproducible_and_single_day(base_state):
    a, _s, _d = _audit(base_state)
    b, _s, _d = _audit(base_state)
    assert a == b, "same seed → identical document"
    c, _s, _d = _audit(base_state, argv=["--seed", "7"])
    assert c["verdicts"][0]["ci95"] != a["verdicts"][0]["ci95"]
    vals = list(range(10))
    assert GA.boot_iid(vals, 300, GA._rng(1, "x")) == GA.boot_iid(vals, 300, GA._rng(1, "x"))
    one, summary, _d = _audit(base_state, argv=["--since", "2026-09-28T00:00:00Z", "--until", "2026-09-28T23:59:59Z"])
    assert one["populations"]["days"] == 1
    assert all(v["verdict"] == GA.V_THIN and v["ci_flag"] == GA.SINGLE_DAY for v in one["verdicts"])
    assert any("[GA tek gün]" in line for line in summary)
    assert summary[-1].startswith("Sonuç: veri az")


# ----------------------------------------------------------------------------- 7. entry bar
def test_entry_bar_b1():
    opened = _ms(datetime(2026, 9, 28, 10, 5, 7, tzinfo=UTC))
    assert GA.b1_end(opened) == _ms(datetime(2026, 9, 28, 10, 10, tzinfo=UTC))
    assert GA.stopped_in_b1(opened, _ms(datetime(2026, 9, 28, 10, 7, 30, tzinfo=UTC)), True)
    assert not GA.stopped_in_b1(opened, _ms(datetime(2026, 9, 28, 10, 10, 30, tzinfo=UTC)), True)
    assert not GA.stopped_in_b1(opened, _ms(datetime(2026, 9, 28, 10, 7, 30, tzinfo=UTC)), False)


def test_entry_bar_in_the_audit(base_state):
    doc, _s, _d = _audit(base_state)
    b0 = next(x for x in doc["h5"]["by_bucket"] if x["bucket"] == "B0")
    assert b0["stopped_in_b1"] == 1, "the BOS trade is stopped 2 min 23 s after its open"
    assert doc["h5"]["upper_bound_bias"] is not None


# ----------------------------------------------------------------------------- 8. POSITION_OPEN join
def test_position_open_join_and_rule_ms(base_state):
    assert GA.rule_ms(RULE_BASE) == pytest.approx(2.22) and GA.rule_ms(RULE_LEARN) == pytest.approx(0.32)
    assert GA.rule_ms("n0.1_break_prev_day_low_box_mid_LS_eod") is None and GA.rule_ms(None) is None
    real = [{"id": "F1", "sym": "X/USDT", "side": 1, "t0": 100, "closed": 200, "xcls": "STOP", "r": -1.0},
            {"id": "F2", "sym": "X/USDT", "side": -1, "t0": 300, "closed": 400, "xcls": "TARGET", "r": 0.5}]
    idx = GA.held_index(real, [{"id": "F9", "sym": "Y/USDT", "side": 1, "t0": 50, "sp": 1.0}])
    assert GA.held_trade(idx, "X/USDT", 150)["id"] == "F1"
    assert GA.held_trade(idx, "X/USDT", 200) is None, "closed_at is exclusive"
    assert GA.held_trade(idx, "X/USDT", 350)["id"] == "F2"
    assert GA.held_trade(idx, "Y/USDT", 10_000)["xcls"] == "OPEN"
    doc, _s, _d = _audit(base_state)
    fam = {(f["family"], f["rule_ms"]) for f in doc["h3"]["by_family"]}
    assert ("POSITION_OPEN", "2.22") in fam and ("CAPACITY", "0.32") in fam
    j = doc["h3"]["join"]
    assert j["joined"] + j["unjoined"] == doc["h3"]["position_open_n"] > 0 and j["joined"] > 0
    assert sum(v["n"] for v in doc["h3"]["cf_mean_by_held_outcome"].values()) == doc["h3"]["position_open_n"]


# ----------------------------------------------------------------------------- 9. H7 flags
def test_h7_flags(base_state):
    D_ = _ms(datetime(2026, 9, 28, tzinfo=UTC))
    rows = [{"id": "a", "sym": "X", "side": 1, "day": "2026-09-28", "t0": D_ + 3_600_000, "closed": D_ + 2 * 3_600_000,
             "is_stop": True, "exit": "stop", "sig": D_ + 3_300_000},
            {"id": "b", "sym": "X", "side": 1, "day": "2026-09-28", "t0": D_ + 3 * 3_600_000, "closed": D_ + 4 * 3_600_000,
             "is_stop": False, "exit": "hedef1", "sig": D_ + 3 * 3_600_000 - 600_000},
            {"id": "c", "sym": "X", "side": -1, "day": "2026-09-28", "t0": D_ + 5 * 3_600_000, "closed": None,
             "is_stop": False, "exit": "stop", "sig": None},
            {"id": "d", "sym": "Y", "side": 1, "day": "2026-09-28", "t0": D_ + DAY - 293_000, "closed": D_ + DAY + 307_000,
             "is_stop": False, "exit": "BOX_EOD_FLAT", "sig": D_ + GA.SIG_2350_MS}]
    GA.h7_flags(rows)
    assert [r["reentry"] for r in rows] == [False, True, False, False]
    assert [r["late2350"] for r in rows] == [False, False, False, True]
    doc, _s, _d = _audit(base_state)
    assert doc["h7"]["reentry"]["n"] == 1 and doc["h7"]["signal_2350"]["n"] == 1
    assert doc["h7"]["real_kept"] == doc["populations"]["real_w"] - 2


# ----------------------------------------------------------------------------- 10. H9 wrong-side targets
def test_h9_wrong_side_targets(base_state):
    hist = _hist_by_symbol(base_state)
    wrs, tgn, tgt = hist["WRS/USDT"][0], hist["TGN/USDT"][0], hist["TGT/USDT"][0]
    assert GA.wrong_side(GA.real_row(wrs, {}))
    assert float(tgn["r_multiple"]) < 0 and not GA.wrong_side(GA.real_row(tgn, {})), "correct side, negative only by costs"
    assert not GA.wrong_side(GA.real_row(tgt, {}))
    doc, _s, _d = _audit(base_state)
    assert doc["h9"]["n"] == 1 and doc["h9"]["sum_r"] == pytest.approx(float(wrs["r_multiple"]), abs=1e-4)
    assert doc["h9"]["upper_bound_effect"] == pytest.approx(-float(wrs["r_multiple"]) / doc["populations"]["real_w"], abs=1e-4)


# ----------------------------------------------------------------------------- 11. H8 funding
def test_h8_funding_missing_counted_and_imputed(base_state):
    cf_doc = json.loads((base_state / BOOK / "counterfactual_trades.json").read_text(encoding="utf-8"))
    missing = [t for t in cf_doc["trades"] if (t.get("outcome") or {}).get("funding_complete") is False]
    assert missing, "the fixture has CF rows whose 00:00 funding is unknown"
    doc, _s, _d = _audit(base_state)
    h8 = doc["h8"]
    assert h8["cf_funding_incomplete"]["n"] == len(missing)
    assert sum(h8["imputation_levels"].values()) == len(missing) and h8["imputation_levels"].get("none", 0) == 0
    assert h8["adjusted_cf_mean"] != doc["populations"]["headline"]["cf"]["mean"]
    assert h8["real_eod_flat"]["seconds_after_midnight_p50"] == pytest.approx(307.0)
    # unit: symbol/side/midnight first, then side/midnight, then side; R = % of notional / CF stop %
    mid = _ms(datetime(2026, 9, 29, tzinfo=UTC))
    real = [{"sym": "X", "side": 1, "mid_fund": [(mid, -0.01)]}, {"sym": "Z", "side": 1, "mid_fund": [(mid, -0.03)]}]
    cf = [{"sym": "X", "side": 1, "day": "2026-09-28", "fund_ok": False, "fund_missing": 1, "sp": 0.5},
          {"sym": "Y", "side": 1, "day": "2026-09-28", "fund_ok": False, "fund_missing": 1, "sp": 0.5},
          {"sym": "Y", "side": 1, "day": "2026-09-20", "fund_ok": False, "fund_missing": 1, "sp": 2.0},
          {"sym": "Y", "side": -1, "day": "2026-09-28", "fund_ok": False, "fund_missing": 1, "sp": 2.0},
          {"sym": "Y", "side": 1, "day": "2026-09-28", "fund_ok": True, "sp": 0.5}]
    lv = GA.impute_funding(cf, real)
    assert [r["fund_imp"] for r in cf] == pytest.approx([-0.02, -0.04, -0.01, 0.0, 0.0])
    assert lv == {"symbol_side_midnight": 1, "side_midnight": 1, "side": 1, "none": 1}


# ----------------------------------------------------------------------------- 12. occupancy
def test_occupancy():
    trades = [(100, 400), (200, 500), (300, 600)]
    opens = sorted(o for o, _c in trades)
    closes = sorted(c for _o, c in trades)
    assert GA.occupancy_at(opens, closes, 300, own=True) == 2, "three overlapping trades → 2 others at the third open"
    assert GA.occupancy_at(opens, closes, 300) == 3
    assert GA.occupancy_at(opens, closes, 450) == 2 and GA.occupancy_at(opens, closes, 600) == 0
    assert GA.occupancy_at(opens + [350], closes, 700) == 1, "a still-open position counts"
    assert GA.occ_band(9) == "0-9" and GA.occ_band(40) == "40+" and GA.occ_band(None) == "?"


# ----------------------------------------------------------------------------- 13. streaming + size guard
def test_streaming_memory_peak_and_size_guard(base_state, tmp_path, monkeypatch, capsys):
    path = tmp_path / "trade_memory.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(200_000):
            tid = "F%06d" % (i // 2)
            if i % 2:
                fh.write('{"kind": "exit", "trade_id": "%s", "recorded_at": "2026-09-28T10:00:00+00:00", "outcome": {"r": 0.1}}\n' % tid)
            else:
                fh.write('{"kind": "entry", "trade_id": "%s", "recorded_at": "2026-09-28T10:00:00+00:00", "symbol": "X/USDT", '
                         '"features": {"strategy": "b1_box_fade", "signal_ts": 1790000000000, "signal_close": 1.2345, '
                         '"stop_at_entry": 1.2}}\n' % tid)
    wanted = {"F%06d" % i: _ms(datetime(2026, 9, 28, 10, tzinfo=UTC)) for i in range(0, 100_000, 160)}
    tracemalloc.start()
    try:
        mem = GA.load_memory(path, wanted)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(mem) == len(wanted) and mem["F000160"]["sig"] == 1790000000000
    assert peak < 20 * 1024 * 1024, peak
    # size guard: an optional JSON over the limit is skipped with a message; a required one stops the run (exit 2)
    monkeypatch.setattr(GA, "MAX_JSON_BYTES", 100)
    warn: list = []
    assert GA.load_json(base_state / "box_timer.json", required=False, warn=warn) is None
    assert warn and "çok büyük" in warn[0] and "okunmadı" in warn[0]
    with pytest.raises(GA.AuditError) as ei:
        GA.load_json(base_state / BOOK / "futures_ledger.json", required=True, warn=[])
    assert ei.value.code == 2
    assert GA.main(["--state", str(base_state), "--boot", "20"], now=NOW) == 2
    assert "atlandı" in capsys.readouterr().err


# ----------------------------------------------------------------------------- 14. missing optional files
def test_missing_optional_files_are_veri_yok(tmp_path, capsys):
    state = build_state(tmp_path, optional=False, archive=False)
    (state / BOOK / "trade_memory.jsonl").unlink()
    (state / "learning_mode.json").unlink()
    rc, out = _run(state, capsys=capsys)
    assert rc == 0
    assert out.count(GA.NO_DATA) >= 5
    assert "eksik isteğe bağlı dosya" in out and "OKUNMADI" not in out
    doc, _s, _d = _audit(state)
    assert doc["s_main"] == {"status": GA.NO_DATA} and doc["replay"]["status"] == GA.NO_DATA
    assert doc["h6"]["snapshot_strata"] == GA.NO_DATA and doc["h4"]["sampling_cadence"]["box_timer"] == GA.NO_DATA
    assert doc["populations"]["real_after"] > 0, "features.learning still marks the learning-period trades"


# ----------------------------------------------------------------------------- 15. replay
def _write_klines(frames, sym: str, d: Path) -> None:
    df = frames[sym]
    bsym = sym.replace("/", "")
    for day in range(DAYS + 2):
        start = _ms(D0) + day * DAY
        part = df[(df["timestamp"] >= start) & (df["timestamp"] < start + DAY)]
        micro = day == 1                                # one file in microseconds with a header row (newer public data)
        lines = ["open_time,open,high,low,close,volume,close_time"] if micro else []
        for t, o, h, lo, c in zip(part["timestamp"], part["open"], part["high"], part["low"], part["close"]):
            lines.append("%d,%r,%r,%r,%r,1.0,%d" % (t * 1000 if micro else t, o, h, lo, c, t + M5 - 1))
        (d / ("%s-5m-%s.csv" % (bsym, _dt(start).strftime("%Y-%m-%d")))).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_replay_matches_net_outcome_and_skips_without_csv(base_state, tmp_path, capsys):
    kl = tmp_path / "klines"
    kl.mkdir()
    doc, _s, _d = _audit(base_state, argv=["--replay-klines", str(kl)])
    assert doc["replay"]["status"] == GA.NO_DATA
    frames = _frames()
    _write_klines(frames, "AAA/USDT", kl)
    _write_klines(frames, "REE/USDT", kl)
    a = GA.build_parser().parse_args(["--state", str(base_state), "--boot", "50", "--replay-klines", str(kl)])
    aud = GA.Audit(a, now=NOW)
    aud.load()
    aud.populations()
    files = {(p.name.split("-5m-")[0], p.name.split("-5m-")[1][:10]): p for p in kl.iterdir()}
    res = GA.replay_rows(aud.re, files, aud.ledger_cfg)
    assert res["rows"] and res["skipped"].get("no_file", 0) > 0
    model = ExecModel.of_ledger(FuturesLedgerV2.load(base_state / BOOK / "futures_ledger.json"))
    by_id = {r["id"]: r for r in aud.re}
    for row in res["rows"]:
        r = by_id[row["id"]]
        sig = r["sig"]
        d0 = sig - sig % DAY
        prev = frames[r["sym"]][(frames[r["sym"]]["timestamp"] >= d0 - DAY) & (frames[r["sym"]]["timestamp"] < d0)]
        box_mid = (float(prev["high"].max()) + float(prev["low"].min())) / 2.0
        view = GA.replay_view(trade_id=r["id"], symbol=r["sym"], side=r["side"], opened_iso=_iso(r["t0"]), opened_ms=r["t0"],
                              entry=r["entry_ref"], stop=r["stop"], box_mid=box_mid, sig_ms=sig)
        cur = frames[r["sym"]]
        df = cur[(cur["timestamp"] >= (r["t0"] // M5) * M5) & (cur["timestamp"] < d0 + DAY)].reset_index(drop=True)
        g = label_with_candles(view, df)
        out = net_outcome(view, df, g, model=model, funding_lookup=None)
        assert row["r_net"] == pytest.approx(out["r_net"], abs=1e-9)
        assert row["diff"] == pytest.approx(out["r_net"] - (r["r"] + r["parts"]["funding"]), abs=1e-9)
        assert view.horizon_bars == GA.box_eod_bars(sig, r["t0"]) > 0
    doc2, _s, details = _audit(base_state, argv=["--replay-klines", str(kl)])
    assert doc2["replay"]["status"] == "OK" and doc2["replay"]["n"] == len(res["rows"])
    assert doc2["replay"]["transition"] and any("replayed" in line for line in details)


def test_box_eod_bars_matches_strategy_paper():
    from tradingbot.strategy_paper import _box_eod_bars
    day = _ms(datetime(2026, 9, 28, tzinfo=UTC))
    for sig_off, now_off in ((10 * 3_600_000, 10 * 3_600_000 + M5 + 7_000), (0, M5 + 3_000), (GA.SIG_2350_MS, GA.SIG_2350_MS + M5 + 7_000),
                             (DAY - M5, DAY + 7_000), (12 * 3_600_000, 15 * 3_600_000 + 299_999), (5 * 3_600_000, 5 * 3_600_000 + M5)):
        sig, now = day + sig_off, day + now_off
        assert GA.box_eod_bars(sig, now) == _box_eod_bars({"signal_ts": sig}, _dt(now)), (sig_off, now_off)
    assert GA.box_eod_bars(None, day) == 0


# ----------------------------------------------------------------------------- 16. output
def test_output_summary_and_json_keys(base_state, tmp_path, capsys):
    out = tmp_path / "gap.json"
    rc, text = _run(base_state, "--json-out", str(out), capsys=capsys)
    assert rc == 0
    lines = text.splitlines()
    end = lines.index(next(x for x in lines if x.startswith("Sonuç:")))
    summary = lines[:end + 1]
    assert summary[0] == "== BOX OLSAYDI–GERÇEK FARKI ==" and len(summary) <= 30
    assert "OLSAYDI" in text and "GA" in text and lines[-1].startswith("Sonuç:") and lines[-1] == summary[-1]
    for h, title in GA.H_TITLES.items():
        assert any(x.startswith("%s %s: etki ≈ " % (h, title)) and "[GA " in x and "→" in x for x in summary), h
    words = {GA.V_YES, GA.V_PART, GA.V_NO, GA.V_THIN}
    hlines = [x for x in summary if x.split(" ")[0] in GA.H_TITLES]
    assert len(hlines) == 10 and all(any(x.endswith("→ " + w) for w in words) for x in hlines)
    assert any(w in lines[-1] for w in ("açıklayan", "veri az"))
    assert "kâr iddiası değildir" in lines[-1]
    import re as _re
    assert not any(_re.search(r"[+-]\d+\.\d", x) for x in summary), "decimal comma in the summary"
    assert any(_re.search(r"[+-]\d+\.\d{3}", x) for x in lines[end + 1:]), "decimal point in the detail tables"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert {"schema", "generated_at", "args", "inputs", "populations", "s0", "h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8",
            "h9", "h10", "s_main", "replay", "verdicts"} <= set(doc)
    assert doc["schema"] == "box_cf_gap_audit_v1" and [v["h"] for v in doc["verdicts"]] == list(GA.H_TITLES)
    for v in doc["verdicts"]:
        assert {"h", "effect_r", "ci95", "verdict", "n_cf", "n_real", "n_days"} <= set(v) and v["verdict"] in words

    def floats(x):
        if isinstance(x, float):
            yield x
        elif isinstance(x, dict):
            for v in x.values():
                yield from floats(v)
        elif isinstance(x, list):
            for v in x:
                yield from floats(v)
    assert all(round(f, 4) == f for f in floats(doc))
    raw = out.read_text(encoding="utf-8")
    cf_ids = [t["id"] for t in json.loads((base_state / BOOK / "counterfactual_trades.json").read_text(encoding="utf-8"))["trades"]]
    assert sum(1 for i in cf_ids if i in raw) <= 2 * 10, "ids only in top-N lists"
    # the summary stays within 30 lines however many warnings there are
    a = GA.build_parser().parse_args(["--state", str(base_state), "--boot", "20"])
    aud = GA.Audit(a, now=NOW)
    aud.run()
    aud.warn += ["uyarı %d" % i for i in range(40)]
    s2 = aud.summary_lines()
    assert len(s2) <= 30 and s2[-1].startswith("Sonuç:") and any("uyarı daha" in x for x in s2)


def test_sanity_and_cohorts_match_bot_scorecard(base_state):
    spec = importlib.util.spec_from_file_location("bot_scorecard_for_gap", ROOT / "scripts" / "bot_scorecard.py")
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    card = bs.counterfactual_card(base_state / BOOK / "counterfactual_trades.json")
    doc, _s, _d = _audit(base_state)
    san = doc["s0"]["sanity"]
    assert san["match_1e3"] is True and san["scorecard_rule_mean"] == pytest.approx(card["mean_r"], abs=1e-4)
    assert san["cf_net_mean_unfiltered"] == pytest.approx(card["mean_r"], abs=1e-3) and san["scorecard_rule_n"] == card["n_net"]
    split = bs.learning_split(base_state / BOOK / "futures_ledger.json", learning_since_iso=(D0 + timedelta(days=1)).isoformat())
    coh = doc["s0"]["real_by_cohort"]
    assert coh.get("policy", 0) == split["policy"]["n"] and coh.get("extra", 0) == split["learning_extra"]["n"]
    assert doc["populations"]["real_after"] == split["after"]["n"]


# ----------------------------------------------------------------------------- invocation styles / standard library only
def _env_without_python_vars() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}


def test_runs_as_a_copy_with_pythonpath_and_in_place(base_state, tmp_path):
    before = _tree(base_state)
    copy_dir = tmp_path / "tmpdl"
    copy_dir.mkdir()
    copy = copy_dir / "box_cf_gap_audit.py"                        # like /tmp/box_cf_gap_audit.py on the VPS (pre-deploy)
    copy.write_bytes(SCRIPT.read_bytes())
    out = tmp_path / "gap.json"
    env = dict(_env_without_python_vars(), PYTHONPATH=str(ROOT))
    res = subprocess.run([sys.executable, str(copy), "--state", str(base_state), "--config", str(ROOT / "config.yaml"),
                          "--json-out", str(out), "--boot", "50"], cwd=str(copy_dir), env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=300)
    assert res.returncode == 0, res.stderr
    assert "içe aktarılamadı" not in res.stdout and json.loads(out.read_text(encoding="utf-8"))["schema"] == GA.SCHEMA
    assert sorted(p.name for p in copy_dir.iterdir()) == ["box_cf_gap_audit.py"], "no __pycache__ next to the copy"
    # post-deploy: in place under <app>/scripts, no PYTHONPATH (isolated mode) — the parent directory is the repo
    res = subprocess.run([sys.executable, "-I", str(SCRIPT), "--state", str(base_state), "--config", str(ROOT / "config.yaml"),
                          "--boot", "50"], cwd=str(tmp_path), env=_env_without_python_vars(), capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    assert res.returncode == 0, res.stderr
    assert "içe aktarılamadı" not in res.stdout and res.stdout.rstrip().splitlines()[-1].startswith("Sonuç:")
    # a copy without PYTHONPATH still runs on the standard library; --config then degrades to a warning
    res = subprocess.run([sys.executable, "-I", str(copy), "--state", str(base_state), "--boot", "50"], cwd=str(copy_dir),
                         env=_env_without_python_vars(), capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert res.returncode == 0, res.stderr
    res = subprocess.run([sys.executable, "-I", str(copy), "--state", str(base_state), "--config", str(ROOT / "config.yaml"),
                          "--boot", "50"], cwd=str(copy_dir), env=_env_without_python_vars(), capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    assert res.returncode == 0 and "içe aktarılamadı" in res.stdout
    assert _tree(base_state) == before and sorted(p.name for p in copy_dir.iterdir()) == ["box_cf_gap_audit.py"]


def test_default_path_is_standard_library_only(base_state):
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        nodes = [node] + (list(ast.walk(node)) if isinstance(node, ast.If) else [])
        for n in nodes:
            if isinstance(n, ast.Import):
                top |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                top.add(n.module.split(".")[0])
    assert top <= set(sys.stdlib_module_names) | {"__future__"}, top
    lazy = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for n in ast.walk(fn):
            mod = (n.module if isinstance(n, ast.ImportFrom) else n.names[0].name if isinstance(n, ast.Import) else None)
            if mod and mod.split(".")[0] not in sys.stdlib_module_names:
                lazy.setdefault(fn.name, set()).add(mod)
    assert set(lazy) == {"load_config_ranks", "replay_view", "replay_rows"}, lazy
    code = ("import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('ga', %r)\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "rc = m.main(['--state', %r, '--boot', '20'])\n"
            "bad = sorted(k for k in ('tradingbot', 'pandas', 'numpy', 'yaml') if k in sys.modules)\n"
            "print('MODS', bad, rc)\n") % (str(SCRIPT), str(base_state))
    res = subprocess.run([sys.executable, "-I", "-c", code], env=_env_without_python_vars(), capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().splitlines()[-1] == "MODS [] 0"


def test_empty_and_sparse_inputs_do_not_crash(tmp_path, capsys):
    state = tmp_path / "state"
    (state / BOOK).mkdir(parents=True)
    (state / BOOK / "counterfactual_trades.json").write_text('{"trades": []}', encoding="utf-8")
    (state / BOOK / "futures_ledger.json").write_text('{"schema_version": 2, "wallet_balance": "100", "history": [], "positions": {}}',
                                                      encoding="utf-8")
    rc, out = _run(state, capsys=capsys)
    assert rc == 0 and "[GA —]" in out and "Sonuç: veri az" in out
    (state / BOOK / "counterfactual_trades.json").write_text(json.dumps({"trades": [
        {"id": "x", "symbol": "A/USDT", "created_at": "garbage", "reason_not_opened": ["INSUFFICIENT_MARGIN"],
         "outcome": {"r_net": "abc"}}, 5]}), encoding="utf-8")
    (state / BOOK / "futures_ledger.json").write_text(json.dumps({"schema_version": 2, "wallet_balance": "100", "history": [
        {"id": "F1", "symbol": "A/USDT", "opened_at": None, "closed_at": "x", "exit_reason": None, "fills": "bad", "features": []},
        "not-a-dict"], "positions": {"A/USDT": {"opened_at": "2026-09-30T00:00:00Z"}}}), encoding="utf-8")
    rc, out = _run(state, capsys=capsys)
    assert rc == 0 and "zamanı okunamadı" in out and "nesne değil" in out

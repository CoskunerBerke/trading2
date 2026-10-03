# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — satır kurucuları (`rows.py`) + tek hafıza deposu (`store.py`) (2026-09-29).

Kapsam (SPEC_V1 §5, §8, §13 T3; KARARLAR.md — SPEC'e üstün): `xp_entry` / `xp_outcome` / `xp_cf` şeması, deterministik
`row_id`, birleştirme anahtarları (gerçek: defter|işlem|açılış; karşı-olgusal: defter|sinyal|sembol|yön|varyasyon),
revizyonlar (geç funding, v1 → v2+ net yeniden etiket), kohort (`scripts/bot_scorecard.py` ile BİREBİR), kurulum anahtarı
ve kaba aile, gerçek R (maliyet hepsi dahil), karşı-olgusal net sözleşmesi (yalnız cf_label_v2+; v1c ASLA net), depo
(toplu tek fsync, idempotent, NaN reddi, yarım satır, eşzamanlılık, kayıpsız döngü + çökme kurtarma, disk tavanı sırası).

Eski kodda (HEAD 7ec832c) `tradingbot.shared_experience.rows` / `.store` YOKTUR → her test ImportError ile düşer.
Fiyatlar/ücretler SENTETİKTİR (config ile aynı biçim: taker %0,05, maker %0,02, kayma 3 bps).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.accounting import AmountType, FeeSchedule, FuturesLedgerV2, SizeSpec, SlippageModel, TaxPolicy  # noqa: E402
from tradingbot.accounting.models import SymbolFilters  # noqa: E402
from tradingbot.learn import decision_journal as DJ  # noqa: E402
from tradingbot.learn.journal_archive import ArchiveError  # noqa: E402
from tradingbot.learn.shadow import ShadowTrade  # noqa: E402
from tradingbot import learning_cf as LCF  # noqa: E402
from tradingbot.shared_experience import rows as R  # noqa: E402
from tradingbot.shared_experience import store as S  # noqa: E402
from tradingbot.shared_experience.store import ExperienceStore  # noqa: E402

_spec = importlib.util.spec_from_file_location("bot_scorecard_xp", ROOT / "scripts" / "bot_scorecard.py")
SC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SC)

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 7, 30, tzinfo=UTC)
SINCE = "2026-09-28T21:20:00+00:00"
ENV = R.make_env(recorded_at=T0 + timedelta(hours=3), code_sha="deadbeef", config_hash="cafe", learning_since=SINCE)
SYM = "ETH/USDT"


# ============================================================================ yardımcılar
def _ledger() -> FuturesLedgerV2:
    return FuturesLedgerV2(D("1000"), fees=FeeSchedule(maker_pct=D("0.02"), taker_pct=D("0.05")),
                           slippage=SlippageModel(fixed_bps=D("3")), tax_policy=TaxPolicy.disabled())


def _open(led, *, sym=SYM, side="LONG", entry="100", stop=None, setup_type="box_fade", features=None, t=T0, meta=None):
    stop = stop or ("98" if side == "LONG" else "102")
    tg = [D("106")] if side == "LONG" else [D("94")]
    pos = led.open(sym, side, D(entry), SizeSpec(D("200"), AmountType.NOTIONAL, 2), stop=D(stop), targets=tg,
                   setup_type=setup_type, features=dict(features or {}), now=t, meta=dict(meta or {}))
    assert pos is not None, led.last_reject_reason
    return pos


def _trade(led=None, *, exit_px="103", hold=timedelta(hours=1), funding_rate=None, **kw):
    """Aç → (isteğe bağlı 08:00 funding) → kapat. Döner: (açıkken pozisyon kopyası bilgisi, TradeRecord)."""
    led = led or _ledger()
    pos = _open(led, **kw)
    snap = {"initial_stop": pos.initial_stop, "entry_avg": pos.entry_avg, "qty": pos.initial_qty}
    t = kw.get("t", T0)
    if funding_rate is not None:
        led.tick({pos.symbol: pos.entry_avg}, now_utc=t + timedelta(minutes=40),
                 funding_rate_lookup=lambda s, w: D(funding_rate))
    rec = led.close_manual(pos.symbol, D(exit_px), now=t + hold)
    assert rec is not None
    return snap, rec


def _shadow(book_name="b1_box_fade", *, key="signal_ts:1", sym=SYM, side="LONG", variation=None, features=None,
            outcome=None, created=T0, market="USDM_PERP", tf=5, reasons=("TOTAL_OPEN_RISK",)) -> ShadowTrade:
    entry, stop = 100.0, (99.68 if side == "LONG" else 100.32)
    return ShadowTrade(
        id=R.cf_id_for(book_name, key, sym, side, variation), plan_id=key, symbol=sym, market_type=market,
        direction=side, created_at=created.isoformat(), entry=entry, stop=stop,
        targets=[100.32 if side == "LONG" else 99.68], horizon_bars=5, variant="as_planned",
        reason_not_opened=list(reasons), label_ts=(created + timedelta(minutes=tf * 5)).isoformat(), tf_minutes=tf,
        outcome=outcome, book=book_name, signal_key=key, variation=variation, label_kind="TARGET_STOP_TIME",
        features=dict(features or {"setup_type": "box_fade"}), learning_unlocked=False, rule_version="r1", approx=False)


def _cf_rows(n, *, book="strategy_paper_box", book_name="b1_box_fade", status="PENDING", rev=0, tag="a", pad=0):
    out = []
    for i in range(n):
        feats = {"setup_type": "box_fade", "regime": "x" * pad} if pad else None
        sh = _shadow(book_name, key="signal_ts:%s%d" % (tag, i), features=feats)
        if status == "LABELLED":
            sh.outcome = {"r_multiple": 1.0, "exit_reason": "target", "bars": 1, "mfe_pct": 0.3, "mae_pct": 0.0}
        out.append(R.cf_row(sh, book=book, rev=rev, status=status, env=ENV,
                            snapshot={"status": "OK", "h4_trend": "UP"} if rev == 0 else None,
                            snapshot_status="OK" if rev == 0 else None))
    return out


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ============================================================================ 1) sabitler ve kayıt defteri
def test_constants_single_source_and_kinds_never_decision_journal_kinds():
    import tradingbot.shared_experience as P
    assert (R.ROW_SCHEMA, R.KINDS, R.LAYER_VERSION) == ("shared_experience_row_v1", ("xp_entry", "xp_outcome", "xp_cf"),
                                                        "1.0.0")
    assert (P.ROW_SCHEMA, P.KINDS, P.LAYER_VERSION) == (R.ROW_SCHEMA, R.KINDS, R.LAYER_VERSION), "paket başı = tek kaynak"
    assert set(R.KINDS).isdisjoint({DJ.KIND_DECISION, DJ.KIND_OUTCOME}), "DecisionJournal türleriyle ASLA çakışmaz"
    try:
        from tradingbot.shared_experience import situation
    except ImportError:          # durum modülü ayrı aşamada; varsa durum adları aynı olmalı
        situation = None
    if situation is not None and hasattr(situation, "STATUSES"):
        assert tuple(situation.STATUSES) == R.SNAPSHOT_STATUSES


def test_book_registry_matches_learning_mode_scorecard_and_learning_cf():
    from tradingbot.learning_mode import BASELINE_SIZE_KEY, BOOK_NAMES
    assert sorted(n for n, _t in R.BOOKS.values()) == sorted(BOOK_NAMES)
    assert set(R.BOOKS) == {sub or "main" for sub in SC.BOOKS}, "defter anahtarları = karnenin klasörleri"
    assert {k for k, (_n, t) in R.BOOKS.items() if t == R.MAIN} == {"main"}
    assert {k for k, (_n, t) in R.BOOKS.items() if t == R.PATTERN} == {"pattern_trader"}
    assert R.BASELINE_SIZE_KEY == BASELINE_SIZE_KEY
    assert R.STALE_GRACE_BARS == LCF.STALE_GRACE_BARS
    assert (R.CF_LABEL_V1, R.CF_LABEL_V1C) == (LCF.LABEL_VERSION_V1, LCF.LABEL_VERSION_V1C)
    for name in R.BOOKS.values():
        assert R.book_for_name(name[0]) in R.BOOKS


# ============================================================================ 2) kimlik ve birleştirme anahtarları
def test_row_id_is_deterministic_16_hex_and_distinct_per_kind_book_src_rev():
    a = R.row_id("xp_outcome", "main", "main|F00001|t", 0)
    assert a == R.row_id("xp_outcome", "main", "main|F00001|t", 0) and len(a) == 16 and int(a, 16) >= 0
    others = {R.row_id("xp_entry", "main", "main|F00001|t", 0), R.row_id("xp_outcome", "main", "main|F00001|t", 1),
              R.row_id("xp_outcome", "strategy_paper", "main|F00001|t", 0), R.row_id("xp_outcome", "main", "x", 0)}
    assert a not in others and len(others) == 4
    for bad in (("decision", "main", "s", 0), ("outcome_link", "main", "s", 0), ("xp_cf", "main", "", 0),
                ("xp_cf", "main", "s", -1)):
        with pytest.raises(R.RowError):
            R.row_id(*bad)
    _snap, rec = _trade()
    env2 = R.make_env(recorded_at=T0 + timedelta(days=5), learning_since=SINCE)
    r1 = R.outcome_row(rec, book="main", rev=0, final=True, env=ENV)
    r2 = R.outcome_row(rec, book="main", rev=0, final=True, env=env2)
    assert r1["row_id"] == r2["row_id"] == r1["decision_id"], "kayıt anı kimliği DEĞİŞTİRMEZ (idempotent)"
    assert r1["recorded_at"] != r2["recorded_at"]


def test_trade_key_is_book_trade_id_opened_at_so_a_ledger_reset_is_a_new_trade():
    _s1, a = _trade(t=T0)
    _s2, b = _trade(t=T0 + timedelta(days=2))                   # yeni defter: yine F00001
    assert a.id == b.id == "F00001"
    ka, kb = R.trade_key("strategy_paper_box", a.id, a.opened_at), R.trade_key("strategy_paper_box", b.id, b.opened_at)
    assert ka == "strategy_paper_box|F00001|%s" % a.opened_at and ka != kb
    ra = R.outcome_row(a, book="strategy_paper_box", rev=0, final=True, env=ENV)
    rb = R.outcome_row(b, book="strategy_paper_box", rev=0, final=True, env=ENV)
    assert (ra["trade_key"], rb["trade_key"]) == (ka, kb) and ra["row_id"] != rb["row_id"]
    with pytest.raises(R.RowError):
        R.trade_key("main", "F00001", "")


def test_cf_join_key_reproduces_recorder_identity_and_joins_the_real_entry(tmp_path):
    """Karşı-olgusal kimliği kayıtçının (defter, sinyal, sembol, yön, varyasyon) anahtarından; aynı sinyal gerçek açılınca
    gerçek giriş satırı AYNI `join_key`i taşır (SUPERSEDED tespiti buna dayanır)."""
    cf = LCF.CounterfactualRecorder(tmp_path / "cf.json", book="c4_candle_variations")
    key = "signal_ts:1759046400000"
    assert cf.record(signal_key=key, symbol=SYM, direction="LONG", entry=100.0, stop=98.0, targets=[104.0],
                     reason="ALSO_MATCHED", created_at=T0, tf_minutes=240, horizon_bars=6, label_kind="TARGET_STOP_TIME",
                     features={"setup_type": "candle:CV003", "data_source": {"bars": {"4h": 1}}}, variation="CV003")
    t = cf.sb.trades[-1]
    assert R.cf_id_for("c4_candle_variations", key, SYM, "long", "CV003") == t.id
    row = R.cf_row(t, book="strategy_paper_candle4h", rev=0, status="PENDING", env=ENV)
    jk = R.join_key(book="strategy_paper_candle4h", signal_key=key, symbol=SYM, side="LONG", variation="CV003")
    assert row["join_key"] == jk == "strategy_paper_candle4h|%s|%s|LONG|CV003" % (key, SYM)
    assert (row["cf_key"], row["cf_id"], row["signal_key_src"]) == ("strategy_paper_candle4h|" + t.id, t.id, "CF_RECORD")
    assert (row["setup_type"], row["variation"], row["family"]) == ("candle:CV003", "CV003", "CANDLE_PATTERN")
    led = _ledger()
    pos = _open(led, setup_type="candle:CV003", features={"candle_variation": {"id": "CV003", "lab_oos_ci95": [0, 1]}})
    ent = R.entry_row(pos, book="strategy_paper_candle4h", env=ENV, signal_key=key, signal_key_src="META_SIGNAL_TS")
    assert ent["join_key"] == jk and ent["variation"] == "CV003"
    assert R.join_key(book="strategy_paper_candle4h", signal_key=key, symbol=SYM, side="LONG", variation="CV004") != jk
    assert R.join_key(book="main", signal_key=None, symbol=SYM, side="LONG") is None
    with pytest.raises(R.RowError, match="CF_BOOK_MISMATCH"):
        R.cf_row(t, book="strategy_paper_box", rev=0, status="PENDING", env=ENV)
    legacy = copy.deepcopy(t)
    legacy.book = None
    with pytest.raises(R.RowError, match="LEGACY_SHADOW"):
        R.cf_row(legacy, book="main", rev=0, status="PENDING", env=ENV)


# ============================================================================ 3) kurulum anahtarı ve kaba aile
def test_family_mapping_covers_every_book_and_every_formasyon_family():
    from tradingbot.pattern_trader.strategy import FAMILIES as F1
    from tradingbot.pattern_trader.strategy_v2 import FAMILIES_V2 as F2
    from tradingbot.pattern_trader.strategy_v3 import FAMILY_V3 as F3
    want = {"main": ("breakout", "BREAKOUT"), "strategy_paper": ("trend", "TREND"),
            "strategy_paper_m2": ("trend", "MOMENTUM"), "strategy_paper_box": ("box_fade", "FADE"),
            "strategy_paper_trend4h": ("trend_donchian", "BREAKOUT"),
            "strategy_paper_candle4h": ("candle:CV001", "CANDLE_PATTERN"),
            "strategy_paper_candle4h_strict": ("candle:CV007", None),
            "pattern_trader": ("A_TREND_PULLBACK", "TREND")}
    assert set(want) == set(R.BOOKS), "her defter eşlenmiş"
    for book, (st, fam) in want.items():
        assert R.family_of(book, st) == fam and (fam is None or fam in R.FAMILIES)
    # C4S (2026-09-29, inceleme bulgusu): C4'ün aynı sinyalleri — aile havuzuna katılmaz (çift sayım yok)
    assert R.family_key(R.family_of("strategy_paper_candle4h_strict", "candle:CV007"), "LONG") is None
    for fam in list(F1) + list(F2) + [F3]:
        assert R.family_of("pattern_trader", fam) in R.FAMILIES, "Formasyon ailesi eşlenmemiş: %s" % fam
    assert R.family_of("pattern_trader", "B_LEVEL_REVERSAL") == "FADE" and R.family_of("pattern_trader", F3) == "MOMENTUM"
    assert R.family_of("pattern_trader", "C_COMPRESSION_BREAKOUT") == "BREAKOUT"
    assert [R.family_of("main", x) for x in ("pullback", "market", "kırılım", "geri çekilme", "-", None)] == \
        ["TREND", "TREND", "BREAKOUT", "TREND", None, None]
    assert R.family_of("pattern_trader", "UNKNOWN_FAMILY") is None, "eşlenmeyen kurulum havuza KATILMAZ"
    assert R.family_key("TREND", "LONG") != R.family_key("FADE", "LONG") != R.family_key("FADE", "SHORT")
    assert R.setup_key("strategy_paper_box", "box_fade", "short") == "strategy_paper_box|box_fade|SHORT"
    assert R.setup_key("main", "", "LONG") is None
    with pytest.raises(R.RowError):
        R.family_of("strategy_paper_t1", "trend")


def test_setup_type_source_per_book_on_real_and_counterfactual_rows():
    led = _ledger()
    pt = _open(led, setup_type="M3_MOMENTUM_3WS_RSI70", features={"family": "M3_MOMENTUM_3WS_RSI70", "plan_id": "p1"},
               meta={"protocol_version": "pattern_protocol_v3.0.0"})
    e = R.entry_row(pt, book="pattern_trader", env=ENV, signal_key="p1", signal_key_src="PLAN_ID")
    assert (e["setup_type"], e["family"], e["family_key"]) == ("M3_MOMENTUM_3WS_RSI70", "MOMENTUM", "MOMENTUM|LONG")
    assert e["setup_key"] == "pattern_trader|M3_MOMENTUM_3WS_RSI70|LONG"
    assert e["book_ctx"]["protocol_version"] == "pattern_protocol_v3.0.0", "açık pozisyonda meta'dan"
    cf = R.cf_row(_shadow("pattern_trader", key="p2", features={"family": "B_LEVEL_REVERSAL", "plan_id": "p2"}),
                  book="pattern_trader", rev=0, status="PENDING", env=ENV)
    assert (cf["setup_type"], cf["family"]) == ("B_LEVEL_REVERSAL", "FADE")
    mcf = R.cf_row(_shadow("main", key="sig", side="SHORT", features={"setup_type": "breakout", "p_win": 0.4}),
                   book="main", rev=0, status="PENDING", env=ENV)
    assert (mcf["setup_type"], mcf["family"], mcf["setup_key"], mcf["book_type"]) == \
        ("breakout", "BREAKOUT", "main|breakout|SHORT", "MAIN")


# ============================================================================ 4) kohort = bot karnesi
def test_cohort_is_exactly_the_bot_scorecard_rule(tmp_path):
    cut = datetime.fromisoformat(SINCE)
    before, after = cut - timedelta(days=2), cut + timedelta(hours=1)
    extra = {"learning": {"size_rule": "SLOT", "learning_unlocked_by": ["TOTAL_OPEN_RISK"]}}
    unknown = {"learning": {"size_rule": "SLOT", "learning_unlocked_by": ["BASELINE_UNKNOWN"]}}
    policy = {"learning": {"size_rule": "SLOT", "learning_unlocked_by": []}}
    policy_nolist = {"learning": {"size_rule": "SLOT"}}
    cases = [(before, None)] * 3 + [(after, None)] * 2 + [(before, policy)] * 2 + [(after, policy_nolist)] + \
            [(after, extra)] * 2 + [(before, unknown)] + [(after, {"learning": "not-a-dict"})] + [(cut, None)]
    led = _ledger()
    for i, (t, feats) in enumerate(cases):
        _open(led, sym="C%d/USDT" % i, features=feats, t=t)
        assert led.close_manual("C%d/USDT" % i, D("99"), now=t + timedelta(minutes=30)) is not None
    path = tmp_path / "strategy_paper_box" / "futures_ledger.json"
    path.parent.mkdir(parents=True)
    led.save(path)
    card = SC.learning_split(path, learning_since_iso=SINCE)
    mine = {"before": 0, "policy": 0, "learning_extra": 0}
    for rec in FuturesLedgerV2.load(path).history:
        row = R.outcome_row(rec, book="strategy_paper_box", rev=0, final=True, env=ENV)
        ent = R.entry_row(rec, book="strategy_paper_box", env=ENV)
        assert (row["cohort"], row["pre_learning"], row["scorecard_class"]) == \
            (ent["cohort"], ent["pre_learning"], ent["scorecard_class"])
        mine[row["scorecard_class"]] += 1
        if not row["pre_learning"]:                              # karnenin sınıflayıcısı yalnız SONRA dilimine uygulanır
            assert SC.learning_class(rec) == {"POLICY": SC.POLICY, "UNTAGGED": SC.POLICY,
                                              "LEARNING_EXTRA": SC.LEARNING_EXTRA}[row["cohort"]]
    assert (card["before"]["n"], card["after"]["n"], card["policy"]["n"], card["learning_extra"]["n"]) == \
        (mine["before"], mine["policy"] + mine["learning_extra"], mine["policy"], mine["learning_extra"])
    assert mine == {"before": 3, "policy": 7, "learning_extra": 3}, "etiketli işlem daima sonra; since anı dahil"
    assert [R.cohort(f or {}) for f in (None, policy, policy_nolist, extra, unknown, {"learning": "x"})] == \
        ["UNTAGGED", "POLICY", "POLICY", "LEARNING_EXTRA", "LEARNING_EXTRA", "UNTAGGED"]
    # since bilinmiyor → etiketsiz işlem öğrenme ÖNCESİDİR; açılış okunamazsa kapanış anı kullanılır (karnenin `_after`i)
    assert R.pre_learning({}, after.isoformat(), None) is True and R.pre_learning(policy, before.isoformat(), None) is False
    assert R.pre_learning({}, "okunamaz", SINCE, closed_at=after.isoformat()) is False
    assert R.pre_learning({}, "okunamaz", SINCE, closed_at=before.isoformat()) is True


# ============================================================================ 5) gerçek R (maliyet HEPSİ DAHİL)
@pytest.mark.parametrize("side,exit_px,rate", [("LONG", "103", "0.0005"), ("SHORT", "96.5", "0.0005"),
                                               ("LONG", "97.5", None)])
def test_real_r_all_in_cost_identity_and_path_ratios(side, exit_px, rate):
    snap, rec = _trade(side=side, exit_px=exit_px, funding_rate=rate)
    row = R.outcome_row(rec, book="strategy_paper_box", rev=0, final=True, env=ENV)
    risk = D(rec.features["risk_usdt"])
    assert row["r_net"] == pytest.approx(float(rec.r_multiple), abs=1e-6) and row["r_basis"] == "NET"
    all_in = (rec.entry_fee + rec.exit_fee + rec.slippage_cost + rec.funding_paid - rec.funding_received) / risk
    assert row["cost_r"] == pytest.approx(float(all_in), abs=1e-6), "KARARLAR 1: ücret + kayma + ödenen funding"
    assert row["r_gross"] == pytest.approx(row["r_net"] + row["cost_r"], abs=2e-6)
    assert row["r_gross"] == pytest.approx(float((rec.gross_pnl + rec.slippage_cost) / risk), abs=1e-6), \
        "referans fiyatlarda (kaymasız) maliyetsiz R"
    assert row["r_gross_fill"] == pytest.approx(float(rec.gross_pnl / risk), abs=1e-6)
    assert (row["fee_r"], row["slippage_r"]) > (0, 0)
    if rate is None:
        assert row["funding_r"] == 0.0
    else:                                          # pozitif oran: LONG öder (maliyet +), SHORT alır (maliyet −)
        assert (row["funding_r"] > 0) is (side == "LONG") and row["funding_r"] != 0
    rp = 100 * float(risk) / (float(rec.quantity) * float(rec.entry))
    assert row["risk_pct"] == pytest.approx(rp, abs=1e-6)
    assert row["mfe_r"] == pytest.approx(float(rec.mfe_pct) / rp, abs=1e-6)
    assert row["mae_r"] == pytest.approx(float(rec.mae_pct) / rp, abs=1e-6)
    assert (row["hold_hours"], row["hold_bars_4h"], row["label_version"]) == (1.0, 0.25, "ledger_v2")
    assert row["label2"] == ("WIN" if row["r_net"] > 0 else "LOSS")
    ent = R.entry_row(rec, book="strategy_paper_box", env=ENV)
    assert ent["initial_stop"] == pytest.approx(float(snap["initial_stop"]), abs=1e-9)
    assert (ent["initial_stop_src"], ent["entry_px"]) == ("RISK_USDT", float(snap["entry_avg"]))
    assert ent["risk_usdt"] == pytest.approx(float(risk), abs=1e-6)


def test_label2_is_r_net_above_zero_and_label3_uses_the_scratch_band():
    got = [(R.real_r({"r_multiple": r})["label2"], R.real_r({"r_multiple": r})["label3"])
           for r in (0.0, 0.1, 0.25, -0.25, -0.1, 2.0)]
    assert got == [("LOSS", "SCRATCH"), ("WIN", "SCRATCH"), ("WIN", "WIN"), ("LOSS", "LOSS"), ("LOSS", "SCRATCH"),
                   ("WIN", "WIN")]
    no_risk = R.real_r({"r_multiple": 0, "features": {}})
    assert no_risk["risk_known"] is False and no_risk["cost_r"] is None and no_risk["r_net"] == 0.0


def test_late_funding_produces_a_revision_and_the_highest_rev_wins():
    led = _ledger()
    led.funding.fallback_to_last_known = False
    pos = _open(led)
    led.tick({SYM: D("101")}, now_utc=T0 + timedelta(minutes=40))       # 08:00 oranı bilinmiyor → BEKLER
    rec = led.close_manual(SYM, D("103"), now=T0 + timedelta(hours=1))
    assert pos.symbol == SYM and rec.features["funding_coverage"]["complete"] is False
    now = T0 + timedelta(hours=2)
    fin0 = R.outcome_is_final(rec, now=now, in_window=True)
    fp0 = R.outcome_fp(rec)
    rev0 = R.outcome_row(rec, book="main", rev=R.next_rev(None, fp0), final=fin0, env=ENV)
    assert (rev0["rev"], rev0["final"], rev0["funding_complete"]) == (0, False, False)
    assert R.next_rev({"rev": 0, "fp": fp0}, R.outcome_fp(rec)) is None, "değişmeyen kapanış yeni satır üretmez"
    posted = led.settle_late_funding(lambda s, t: D("0.0005"), now=now, mark_for=lambda s, t: D("101"))
    assert len(posted) == 1 and rec.features["funding_coverage"]["complete"] is True
    fp1 = R.outcome_fp(rec)
    rv = R.next_rev({"rev": 0, "fp": fp0}, fp1)
    rev1 = R.outcome_row(rec, book="main", rev=rv, final=R.outcome_is_final(rec, now=now, in_window=True), env=ENV)
    assert (rev1["rev"], rev1["final"], rev1["funding_complete"]) == (1, True, True)
    assert rev1["trade_key"] == rev0["trade_key"] and rev1["row_id"] != rev0["row_id"]
    assert rev1["r_net"] < rev0["r_net"] and rev1["funding_r"] > 0 == rev0["funding_r"]
    assert rev1["r_gross"] == pytest.approx(rev0["r_gross"], abs=1e-6), "funding brüt R'yi değiştirmez"
    best = R.latest_rows([rev0, rev1, rev0])
    assert best[("xp_outcome", rev0["trade_key"])]["rev"] == 1
    assert R.outcome_is_final(rec, now=now, in_window=False) is True
    rec.features["funding_coverage"]["complete"] = False
    assert R.outcome_is_final(rec, now=now, in_window=True) is False
    assert R.outcome_is_final(rec, now=T0 + timedelta(hours=1 + 72), in_window=True) is True


# ============================================================================ 6) karşı-olgusal net sözleşmesi
_BASE = {"r_multiple": 1.2, "exit_reason": "target", "bars": 3, "mfe_pct": 0.5, "mae_pct": -0.1}


@pytest.mark.parametrize("extra,basis,r_net", [
    ({}, "GROSS_LEGACY", None),
    ({"label_version": "cf_label_v1", "net_status": "NET_BACKFILL_WINDOW_NOT_IN_FRAME"}, "GROSS_LEGACY", None),
    ({"label_version": "cf_label_v1c", "r_net_approx": 0.8, "r_net": 0.8}, "GROSS_LEGACY", None),
    ({"label_version": "cf_label_v2", "r_gross": 1.2, "r_net": 0.9, "cost_r": 0.3}, "NET", 0.9),
    ({"label_version": "cf_label_v3", "r_gross": 1.2, "r_net": 0.9, "cost_r": 0.3}, "NET", 0.9),
    ({"label_version": "cf_label_v12", "r_gross": 1.2, "r_net": 0.9}, "NET", 0.9),
    ({"label_version": "cf_label_v3", "r_gross": 1.2, "r_net": None, "net_status": "NO_BARS"}, "NET_UNAVAILABLE", None),
    ({"label_version": "cf_label_v0", "r_net": 0.9}, "UNKNOWN_LABEL_VERSION", None),
    ({"label_version": "cf_label_v2b", "r_net": 0.9}, "UNKNOWN_LABEL_VERSION", None),
    ({"label_version": "shadow_v9", "r_net": 0.9}, "UNKNOWN_LABEL_VERSION", None),
])
def test_cf_r_net_only_from_cf_label_v2_plus_and_v1c_never_in_net(extra, basis, r_net):
    out = R.cf_r({**_BASE, **extra}, entry=100.0, stop=99.0, tf_minutes=240)
    assert (out["r_basis"], out["r_net"], out["in_net_stats"]) == (basis, r_net, basis == "NET")
    assert out["r_gross"] == 1.2 and out["hold_hours"] == 12.0
    assert out["mfe_r"] == pytest.approx(0.5) and out["mae_r"] == pytest.approx(-0.1)
    if basis == "NET":
        assert out["cost_r"] == pytest.approx(0.3) and (out["label2"], out["label3"]) == ("WIN", "WIN")
    else:
        assert out["cost_r"] is None and out["label2"] is None, "net olmayan etiket hükme/kazanca GİRMEZ"
    if extra.get("label_version") == "cf_label_v1c":
        assert out["r_net_approx"] == 0.8, "v1c tahmini yalnız bilgi alanında"
    assert R.cf_r(None, entry=100, stop=99) == {}


def test_real_labeller_versions_classify_and_v1_to_net_relabel_is_a_new_revision(tmp_path):
    """Gerçek kayıtçı: bekleyen (rev 0) → v1 brüt etiket (rev 1, GROSS_LEGACY) → tembel net yeniden etiket (yerinde,
    `label_version` = kodun güncel sürümü) → parmak izi değişir → rev 2, NET."""
    assert R.cf_label_class(LCF.LABEL_VERSION) == "NET" and R.cf_label_class(LCF.LABEL_VERSION_V2) == "NET"
    assert R.cf_label_class(LCF.LABEL_VERSION_V1) == R.cf_label_class(LCF.LABEL_VERSION_V1C) == "GROSS_LEGACY"
    cf = LCF.CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t_rec = datetime(2026, 9, 28, 12, 0, 5, tzinfo=UTC)
    assert cf.record(signal_key="signal_ts:9", symbol="X/USDT", direction="LONG", entry=100.0, stop=99.68,
                     targets=[100.32], reason="POSITION_OPEN", created_at=t_rec, tf_minutes=5, horizon_bars=5,
                     label_kind="TARGET_STOP_TIME", features={"setup_type": "box_fade", "baseline_blocked_by": ["X"]})
    t = cf.sb.trades[-1]
    rows = [R.cf_row(t, book="strategy_paper_box", rev=0, status="PENDING", env=ENV, snapshot={"status": "OK"},
                     snapshot_status="OK")]
    prev = {"rev": 0, "fp": rows[0]["fp"]}
    m5 = 300_000
    ts = int(t_rec.timestamp() * 1000) // m5 * m5 + m5
    df = pd.DataFrame([{"timestamp": ts, "open": 100.0, "high": 100.336, "low": 100.0, "close": 100.32, "volume": 1.0}])
    now = t_rec + timedelta(milliseconds=12 * m5)
    assert cf.label_pending({"X/USDT": {"5m": df}}, now) == 1 and "label_version" not in t.outcome
    fp = R.cf_fp("LABELLED", t.outcome)
    rv = R.next_rev(prev, fp)
    rows.append(R.cf_row(t, book="strategy_paper_box", rev=rv, status="LABELLED", env=ENV))
    assert (rows[1]["rev"], rows[1]["r_basis"], rows[1]["r_net"], rows[1]["r_gross"]) == (1, "GROSS_LEGACY", None, 1.0)
    assert "snapshot" not in rows[1], "anlık görüntü yalnız rev 0'da"
    prev = {"rev": rv, "fp": fp}
    model = LCF.ExecModel.of_ledger(_ledger())
    filt = SymbolFilters(symbol="XUSDT", price_tick=D("0.001"), qty_step=D("0.001"), min_qty=D("0.001"),
                         min_notional=D("5"), source="exchange")
    cf.label_pending({"X/USDT": {"5m": df}}, now, exec_model=model, filters_for=lambda s: filt)
    assert t.outcome["label_version"] == LCF.LABEL_VERSION and t.outcome["r_net"] is not None
    fp2 = R.cf_fp("LABELLED", t.outcome)
    rv2 = R.next_rev(prev, fp2)
    rows.append(R.cf_row(t, book="strategy_paper_box", rev=rv2, status="LABELLED", env=ENV))
    last = rows[2]
    assert (last["rev"], last["r_basis"], last["in_net_stats"], last["label_version"]) == \
        (2, "NET", True, LCF.LABEL_VERSION)
    assert last["r_net"] == pytest.approx(t.outcome["r_net"], abs=1e-6) and last["r_net"] < last["r_gross"] == 1.0
    assert last["cost_r"] == pytest.approx(t.outcome["cost_r"], abs=1e-6)
    assert last["outcome"]["exit_reason"] == "target" and last["labeled_at"] == t.labeled_at and last["final"] is True
    assert last["baseline_blocked"] is True and last["baseline_blocked_by"] == ["X"], "A15 bayrakla KALIR (KARARLAR 7)"
    assert len({r["row_id"] for r in rows}) == 3 and len({r["cf_key"] for r in rows}) == 1
    assert R.latest_rows(rows)[("xp_cf", last["cf_key"])]["rev"] == 2


def test_cf_row_fields_reason_family_whitelist_and_vanish_row_from_minimal_facts():
    feats = {"strategy": "b1_box_fade", "setup_type": "box_fade", "regime": "RANGE", "atr14": 1.5,
             "data_source": {"bars": {"5m": 1}}, "structure": {"action": "SHADOW", "reason_code": "X", "big": [1] * 99},
             "learning": {"size_rule": "SLOT", "reason": "OK", "why": "w", "policy_grade": True, "notional": 5.0},
             "in_lab_universe": True}
    r0 = R.cf_row(_shadow(features=feats, reasons=("STRUCTURE:BOS_AGAINST", "TOTAL_OPEN_RISK")),
                  book="strategy_paper_box", rev=0, status="PENDING", env=ENV, origin="BACKFILL_CF",
                  snapshot={"status": "PARTIAL", "missing": ["h1_rsi14"]}, snapshot_status="PARTIAL",
                  snapshot_meta={"source": {"h4": "tour_frames"}, "lag_steps": 1})
    assert (r0["status"], r0["final"], r0["origin"], r0["reason"], r0["reason_family"]) == \
        ("PENDING", False, "BACKFILL_CF", "STRUCTURE:BOS_AGAINST", "GATE")
    assert r0["features"] == {"strategy": "b1_box_fade", "setup_type": "box_fade", "regime": "RANGE", "atr14": 1.5,
                              "structure": {"action": "SHADOW", "reason_code": "X"},
                              "learning_fit": {"size_rule": "SLOT", "reason": "OK", "why": "w", "policy_grade": True}}
    assert (r0["in_lab_universe"], r0["learning_unlocked"], r0["baseline_blocked"]) == (True, False, False)
    assert r0["snapshot_status"] == "PARTIAL" and r0["snapshot"]["missing"] == ["h1_rsi14"]
    assert r0["as_of_ms"] == int(T0.timestamp() * 1000) and r0["tf_minutes"] == 5 and r0["entry_ref"] == 100.0
    fams = {c: R.reason_family(c) for c in sorted(LCF.counterfactual_ok.__globals__["COUNTERFACTUAL_OK"])}
    assert "OTHER" not in fams.values(), "her karşı-olgusal nedeni bir aileye düşer: %s" % fams
    assert [R.reason_family(x) for x in ("ALREADY_OPEN_SAME_SYMBOL", "CANDLE_VETO:C1", "MIN_NOTIONAL", "WHATEVER")] == \
        ["OCCUPANCY", "GATE", "EXCHANGE", "OTHER"]
    main = R.cf_row(_shadow("main", key="s", features={"setup_type": "pullback", "p_win": 0.41, "net_expectancy_r": -0.1,
                                                       "regime_gate": {"verdict": "BLOCK", "mode": "SHADOW"},
                                                       "learning_penalties": {"a": 1}}),
                    book="main", rev=0, status="PENDING", env=ENV)
    assert main["features"] == {"setup_type": "pullback", "p_win": 0.41, "net_expectancy_r": -0.1,
                                "regime_gate_verdict": "BLOCK"}
    minimal = {"id": "cf_x", "book": "b1_box_fade", "symbol": SYM, "direction": "LONG", "signal_key": "signal_ts:1",
               "features": {"setup_type": "box_fade"}}
    v = R.cf_row(minimal, book="strategy_paper_box", rev=2, status="SUPERSEDED", env=ENV)
    assert (v["status"], v["final"], v["rev"], v["join_key"]) == \
        ("SUPERSEDED", True, 2, "strategy_paper_box|signal_ts:1|%s|LONG|" % SYM)
    assert "outcome" not in v and "r_net" not in v and "snapshot" not in v and v["fp"] != R.cf_fp("PENDING", None)
    with pytest.raises(R.RowError, match="LABELLED_WITHOUT_OUTCOME"):
        R.cf_row(minimal, book="strategy_paper_box", rev=1, status="LABELLED", env=ENV)


def test_classify_vanished_superseded_expired_dropped_and_labelled_archive_trim():
    lt = T0
    grace = timedelta(minutes=5 * (R.STALE_GRACE_BARS + 1))
    kw = dict(label_ts=lt.isoformat(), tf_minutes=5)
    assert R.classify_vanished("PENDING", superseded_by_real=True, now=lt, **kw) == "SUPERSEDED"
    assert R.classify_vanished("PENDING", superseded_by_real=False, now=lt + grace + timedelta(seconds=1), **kw) == "EXPIRED"
    assert R.classify_vanished("PENDING", superseded_by_real=False, now=lt + grace, **kw) == "DROPPED"
    assert R.classify_vanished("LABELLED", superseded_by_real=True, now=lt, **kw) is None, "etiketli budama: revizyon yok"
    assert R.classify_vanished("SUPERSEDED", superseded_by_real=False, now=lt, **kw) is None


def test_paper_only_usdm_only_and_invalid_inputs_raise_row_error():
    sh = _shadow(market="SPOT")
    with pytest.raises(R.RowError, match="SPOT_EXCLUDED"):
        R.cf_row(sh, book="strategy_paper_box", rev=0, status="PENDING", env=ENV)
    ok = _shadow()
    for mode in ("TESTNET", "LIVE", "", None):
        with pytest.raises(R.RowError):
            R.cf_row(ok, book="strategy_paper_box", rev=0, status="PENDING",
                     env={**ENV, "app_mode": mode})
    with pytest.raises(R.RowError, match="BOOK_UNKNOWN"):
        R.cf_row(ok, book="strategy_paper_t1", rev=0, status="PENDING", env=ENV)
    with pytest.raises(R.RowError, match="ORIGIN"):
        R.cf_row(ok, book="strategy_paper_box", rev=0, status="PENDING", env=ENV, origin="REPLAY")
    with pytest.raises(R.RowError, match="SNAPSHOT_STATUS"):
        R.cf_row(ok, book="strategy_paper_box", rev=0, status="PENDING", env=ENV, snapshot_status="PENDING")
    with pytest.raises(R.RowError, match="CF_STATUS"):
        R.cf_row(ok, book="strategy_paper_box", rev=0, status="OPEN", env=ENV)
    assert R.make_env(recorded_at=T0)["app_mode"] == "PAPER"


# ============================================================================ 7) projeksiyonlar ve saflık
def test_learning_projection_and_book_ctx_whitelists_per_book_type():
    lm = {"book": "main", "size_rule": "BUMP_MIN_NOTIONAL", "slots": 20, "risk_pct": 0.5, "leverage": 3,
          "leverage_max": 5, "risk_usdt": 1.25, "equity_basis": 1000.0, "risk_fraction_of_budget": 0.9,
          "learning_unlocked_by": ["NEGATIVE_NET_EDGE"], "exploration": "S5", "leverage_fallback": None,
          "size_multipliers_recorded": {"total": 1.0}, "penalties_recorded": {"x": 1}, "risk_usdt_fit": 1.3,
          "baseline_size": {"notional": 50.0},
          "policy_basis": {"policy_basis": "POLICY", "learning_codes": ["A"], "policy_codes": [], "p_win": 0.4,
                           "p_win_policy": 0.38, "conservative_net_edge_r_policy": -0.1, "size_multiplier_policy": 0.0,
                           "junk": 1}}
    feats = {"learning": lm, "exploration": "S5", "p_win": 0.42, "expected_r": 1.9, "regime": "TREND",
             "consensus_score": 0.6, "consensus_conf": 70, "n_vetoes": 1, "expected_cost_pct": 0.2, "spread_pct": 0.01,
             "structure": {"action": "ENTER", "reason_code": "BOS", "shadow": True, "levels": [1, 2, 3]},
             "candle_confirmation": {"verdict": "CONFIRM", "mode": "SHADOW"}, "regime_gate": {"verdict": "ALLOW"},
             "bias_trend": 0.5, "warnings": ["a"], "agent_stances": {"x": "y"}}
    led = _ledger()
    pos = _open(led, setup_type="pullback", features=feats)
    e = R.entry_row(pos, book="main", env=ENV, signal_key="abc", signal_key_src="ENGINE_SIGNAL_ID")
    assert e["learning"] == {"size_rule": "BUMP_MIN_NOTIONAL", "slots": 20, "risk_pct": 0.5, "leverage": 3,
                             "leverage_max": 5, "risk_usdt": 1.25, "risk_fraction_of_budget": 0.9, "equity_basis": 1000.0,
                             "learning_unlocked_by": ["NEGATIVE_NET_EDGE"], "exploration": "S5",
                             "leverage_fallback": None, "has_baseline_size": True,
                             "policy_basis": {"policy_basis": "POLICY", "learning_codes": ["A"], "policy_codes": [],
                                              "p_win": 0.4, "p_win_policy": 0.38,
                                              "conservative_net_edge_r_policy": -0.1, "size_multiplier_policy": 0.0}}
    assert (e["cohort"], e["exploration"], e["family"], e["signal_key_src"]) == \
        ("LEARNING_EXTRA", "S5", "TREND", "ENGINE_SIGNAL_ID")
    assert e["book_ctx"] == {"p_win": 0.42, "expected_r": 1.9, "regime": "TREND", "consensus_score": 0.6,
                             "consensus_conf": 70, "n_vetoes": 1, "expected_cost_pct": 0.2, "spread_pct": 0.01,
                             "structure": {"action": "ENTER", "reason_code": "BOS", "shadow": True},
                             "regime_gate_verdict": "ALLOW", "candle_confirmation_verdict": "CONFIRM"}
    st = R.book_ctx(R.STRATEGY, {"strategy": "c4", "regime": "UP", "expected_r": 1.0, "p_win": None,
                                 "candle_variation": {"id": "CV001", "definition_sha": "s", "target_r": 2.0,
                                                      "max_hold_bars": 6, "observation": True,
                                                      "lab_oos_ci95": [0.1, 0.3]}})
    assert st == {"strategy": "c4", "regime": "UP", "expected_r": 1.0,
                  "candle_variation": {"id": "CV001", "definition_sha": "s", "target_r": 2.0, "max_hold_bars": 6,
                                       "observation": True}}
    d4 = _open(_ledger(), setup_type="trend_donchian", features={"in_lab_universe": False, "learning": {}})
    ed4 = R.entry_row(d4, book="strategy_paper_trend4h", env=ENV)
    assert (ed4["in_lab_universe"], ed4["cohort"], ed4["learning"]) == (False, "POLICY", {"has_baseline_size": False})
    assert R.project_learning({}) is None and R.project_learning({"learning": "x"}) is None


def test_row_builders_are_pure_and_always_json_safe():
    nasty = {"learning": {"size_rule": "SLOT", "learning_unlocked_by": [], "risk_usdt": float("nan"),
                          "equity_basis": D("1000.5"), "slots": 20},
             "regime": float("inf"), "strategy": "b1_box_fade", "expected_r": D("1.5"),
             "structure": {"action": "ENTER", "reason_code": {"x", "y"}, "shadow": datetime(2026, 1, 1, tzinfo=UTC)},
             "in_lab_universe": True}
    led = _ledger()
    pos = _open(led, features=nasty)
    before = copy.deepcopy(pos.features)
    import numpy as np
    snap = {"h4_rsi14": float("nan"), "status": "OK", "nested": {"a": [float("-inf"), 1]},
            "n_h4": np.int64(200), "h4_atr_pct": np.float64(1.25), "is_btc": np.bool_(False), "x": np.float64("nan")}
    snap_before = copy.deepcopy(snap)
    e = R.entry_row(pos, book="strategy_paper_box", env=ENV, snapshot=snap, snapshot_status="OK",
                    snapshot_meta={"computed_at": T0})
    rec = led.close_manual(SYM, D("101"), now=T0 + timedelta(hours=2))
    o = R.outcome_row(rec, book="strategy_paper_box", rev=0, final=True, env=ENV)
    for row in (e, o):
        json.dumps(row, allow_nan=False)                      # NaN/sonsuz satıra ÇIKMAZ
    assert e["learning"]["risk_usdt"] is None and e["learning"]["equity_basis"] == 1000.5
    assert e["snapshot"]["h4_rsi14"] is None and e["snapshot"]["nested"]["a"] == [None, 1]
    sn = e["snapshot"]
    assert (sn["n_h4"], sn["h4_atr_pct"], sn["is_btc"], sn["x"]) == (200, 1.25, False, None)
    assert type(sn["n_h4"]) is int and type(sn["is_btc"]) is bool, "numpy sayıları metne dönüşmez"
    assert e["book_ctx"]["structure"]["reason_code"] == ["x", "y"] and e["book_ctx"]["expected_r"] == 1.5
    assert pos.features == before and snap == snap_before, "girdi DEĞİŞMEZ"


# ============================================================================ 8) depo: yazım, idempotency, doğrulama
def _mixed_rows():
    _snap, rec = _trade()
    ent = R.entry_row(rec, book="strategy_paper_box", env=ENV, snapshot={"status": "OK"}, snapshot_status="OK")
    out = R.outcome_row(rec, book="strategy_paper_box", rev=0, final=True, env=ENV)
    return [ent, out] + _cf_rows(2)


def test_store_round_trip_idempotent_by_row_id_nan_and_invalid_rejected(tmp_path):
    st = ExperienceStore(tmp_path / "xp")
    assert (st.path.name, st.max_lines, st.archive.stream_id, st.archive.record_schema_version) == \
        ("experience.jsonl", 5000, "shared_experience", "shared_experience_row_v1")
    rows = _mixed_rows()
    assert st.append_rows(rows) == (4, 0)
    assert [json.loads(ln) for ln in _lines(st.path)] == rows, "satırlar AYNEN geri okunur"
    assert st.append_rows(rows) == (0, 0) and st.last_append["duplicate"] == 4, "row_id ile idempotent"
    again = ExperienceStore(tmp_path / "xp")
    again.load_seen()
    assert again.append_rows(rows) == (0, 0) and len(_lines(st.path)) == 4, "yeniden başlatma sonrası da"
    nan = copy.deepcopy(rows[3])
    nan["row_id"] = nan["decision_id"] = R.row_id("xp_cf", nan["book"], nan["cf_key"], 7)
    nan["snapshot_meta"] = {"lag": float("nan")}
    assert st.append_rows([nan]) == (0, 1) and st.last_append["nan"] == 1 and st.counters["nan"] == 1
    fixed = dict(nan, snapshot_meta={"lag": None})
    assert st.append_rows([fixed]) == (1, 0), "reddedilen kimlik işaretlenmez; düzeltilmiş satır yazılır"
    base = rows[2]
    bad = [dict(base, kind="decision"), dict(base, kind="outcome_link"), dict(base, schema="decision_journal_v1"),
           dict(base, app_mode="TESTNET"), dict(base, decision_id="0" * 16), dict(base, book="nowhere"),
           dict(base, rev=-1), dict(base, row_id="XYZ"), "not-a-row"]
    assert st.append_rows(bad) == (0, len(bad))
    assert st.last_append["reasons"] == {"KIND": 2, "SCHEMA": 1, "NOT_PAPER": 1, "DECISION_ID": 1, "BOOK": 1, "REV": 1,
                                         "ROW_ID": 1, "NOT_A_MAPPING": 1}
    no_did = {k: v for k, v in _cf_rows(1, tag="z")[0].items() if k != "decision_id"}
    assert st.append_rows([no_did]) == (1, 0) and json.loads(_lines(st.path)[-1])["decision_id"] == no_did["row_id"]
    kinds = {json.loads(ln)["kind"] for ln in _lines(st.path)}
    assert kinds <= set(R.KINDS)
    link = DJ.build_outcome_link(trade_id="F1", outcome={"closed_at": "x"})
    assert st.append_outcome(link) is False and st.append_decision(_cf_rows(1, tag="q")[0]) is True
    assert len(_lines(st.path)) == 7


def test_store_one_fsync_per_batch(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    st = ExperienceStore(tmp_path / "xp")
    assert st.append_rows(_cf_rows(50)) == (50, 0) and len(calls) == 1
    assert st.append_rows(_cf_rows(30, tag="b")) == (30, 0) and len(calls) == 2
    assert st.append_rows(_cf_rows(50)) == (0, 0) and st.append_rows([]) == (0, 0) and len(calls) == 2, \
        "yazılacak satır yoksa açılış/fsync yok"


def test_store_io_error_leaves_rows_unmarked_and_reads_stay_unique(tmp_path, monkeypatch):
    st = ExperienceStore(tmp_path / "xp")
    rows = _cf_rows(5)

    def boom(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fsync", boom)
    assert st.append_rows(rows) == (0, 5) and st.last_append["io_error"] == 5 and st.errors == 1
    assert "No space" in st.last_append["error"] and not (st._seen & {r["row_id"] for r in rows})
    monkeypatch.undo()
    assert st.append_rows(rows) == (5, 0), "sonraki adım yeniden dener"
    assert len({r["row_id"] for r in st.iter_all_rows()}) == 5 and len(list(st.iter_all_rows())) == 5


def test_store_partial_last_line_is_tolerated_and_kept(tmp_path):
    st = ExperienceStore(tmp_path / "xp")
    assert st.append_rows(_cf_rows(2)) == (2, 0)
    with open(st.path, "ab") as fh:
        fh.write(b'{"schema":"shared_experience_row_v1","row_id":"abc')      # çökme: yarım satır, \n yok
    st2 = ExperienceStore(tmp_path / "xp")
    st2.load_seen()
    assert st2.append_rows(_cf_rows(2, tag="n")) == (2, 0) and st2.last_append["partial_line_repaired"] is True
    raw = _lines(st2.path)
    assert len(raw) == 5 and raw[2].endswith('"row_id":"abc'), "yarım satır KAYIPSIZ durur"
    assert len(list(st2.iter_rows())) == 4, "okuyucu yarım satırı atlar, yeni satırlar sağlam"
    assert st2.append_rows(_cf_rows(1, tag="m")) == (1, 0) and st2.last_append["partial_line_repaired"] is False


def test_store_two_threads_on_the_same_file_lose_no_lines(tmp_path):
    a, b = ExperienceStore(tmp_path / "xp"), ExperienceStore(tmp_path / "xp")
    assert a._lock is b._lock, "yol bazlı ortak kilit"
    batches = {name: [_cf_rows(10, tag="%s%d_" % (name, i)) for i in range(20)] for name in ("a", "b")}

    def run(st, name):
        for bt in batches[name]:
            st.append_rows(bt)
    th = [threading.Thread(target=run, args=(a, "a")), threading.Thread(target=run, args=(b, "b"))]
    for t in th:
        t.start()
    for t in th:
        t.join()
    rows = [json.loads(ln) for ln in _lines(a.path)]
    assert len(rows) == 400 and len({r["row_id"] for r in rows}) == 400


# ============================================================================ 9) depo: kayıpsız döngü ve kurtarma
def _filled(tmp_path, n=25, max_lines=10):
    st = ExperienceStore(tmp_path / "xp", hot_max_lines=max_lines)
    for i in range(0, n, 5):
        assert st.append_rows(_cf_rows(5, tag="r%d_" % i))[0] == 5
    return st


def test_store_rotation_is_lossless_and_disk_bytes_counts_hot_plus_archive(tmp_path):
    st = _filled(tmp_path)
    ids = [json.loads(ln)["row_id"] for ln in _lines(st.path)]
    res = st.rotate()
    # HİSTEREZİS (2026-09-29): sınır (10) aşılınca dosya keep_lines = 10 × 0,5 = 5 satıra budanır (eskiden TAM 10'a)
    assert (res["archived"], res["trimmed"], res["health"]) == (20, 20, "OK")
    assert len(_lines(st.path)) == 5 and st._line_count == 5 and st.keep_lines == 5
    man = st.archive.manifest()
    assert man["totals"]["records"] == 20 and man["stream_id"] == "shared_experience" and man["pending_trim"] is None
    assert [r["row_id"] for r in st.iter_all_rows()] == ids, "arşiv + sıcak: sıra korunur, kayıp yok"
    assert st.disk_bytes() == st.path.stat().st_size + man["totals"]["bytes_compressed"]
    assert st.rotate()["archived"] == 0, "sınırın altında döngü no-op"
    # bellek bütçesi (SPEC §12): idempotency kümesi sıcak pencereye iner; sıcaktaki satır yine tekrar YAZILMAZ,
    # arşive gitmiş satır yeniden gelirse zararsız kopya yazılır ve okuma yolu tekilleştirir
    assert st._seen == {json.loads(ln)["row_id"] for ln in _lines(st.path)} and len(st._seen) == 5
    assert st.append_rows(_cf_rows(5, tag="r20_")) == (0, 0)
    assert st.append_rows(_cf_rows(5, tag="r0_")) == (5, 0)
    assert [r["row_id"] for r in st.iter_all_rows()] == ids


def test_store_crash_with_pending_trim_recovers_without_duplication(tmp_path):
    st = _filled(tmp_path)
    ids = [json.loads(ln)["row_id"] for ln in _lines(st.path)]
    block = _lines(st.path)[:15]
    meta = st.archive.seal(block)                                  # çökme: mühür + manifest, budama YOK
    st.archive.commit(meta, pending_trim={"segment_id": meta["segment_id"], "n_lines": 15,
                                          "block_sha256": meta["block_sha256"]})
    assert [r["row_id"] for r in st.iter_all_rows()] == ids, "budama öncesi okuma da tekil"
    st2 = ExperienceStore(tmp_path / "xp", hot_max_lines=10)
    res = st2.rotate()
    assert res["trimmed"] == 15 and len(_lines(st2.path)) == 10 and st2.archive.pending_trim() is None
    assert [r["row_id"] for r in st2.iter_all_rows()] == ids and len(st2.archive.segments()) == 1
    # budama yapılmış ama bekleyen kayıt temizlenmemiş: ikinci kez SİLİNMEZ
    st2.archive.commit(meta, pending_trim={"segment_id": meta["segment_id"], "n_lines": 15,
                                           "block_sha256": meta["block_sha256"]})
    st3 = ExperienceStore(tmp_path / "xp", hot_max_lines=10)
    assert st3.rotate()["trimmed"] == 0 and len(_lines(st3.path)) == 10 and st3.archive.pending_trim() is None
    assert [r["row_id"] for r in st3.iter_all_rows()] == ids


def test_store_crash_after_seal_before_manifest_is_adopted_and_resealed_idempotently(tmp_path):
    st = _filled(tmp_path)
    ids = [json.loads(ln)["row_id"] for ln in _lines(st.path)]
    st.archive.seal(_lines(st.path)[:20])                          # segment diskte, manifest YOK (döngü kesimi 25 − 5)
    st2 = ExperienceStore(tmp_path / "xp", hot_max_lines=10)
    res = st2.rotate()
    assert res["recovered"]["adopted"] == 1 and res["archived"] == 20 and len(st2.archive.segments()) == 1, \
        "aynı blok aynı segment: çift kayıt yok"
    assert len(_lines(st2.path)) == 5 and [r["row_id"] for r in st2.iter_all_rows()] == ids


def test_store_archive_failure_means_no_trim(tmp_path, monkeypatch):
    st = _filled(tmp_path)
    before = _lines(st.path)

    def fail(lines):
        raise ArchiveError("disk dolu (test)")
    monkeypatch.setattr(st.archive, "seal", fail)
    res = st.rotate()
    assert res["health"] == "ARCHIVE_FAILED" and res["trimmed"] == 0 and _lines(st.path) == before
    assert st.archive_errors == 1 and "disk dolu" in st.summary()["last_archive_error"]


# ============================================================================ 10) depo: disk tavanı sırası
def test_store_disk_cap_order_warn_then_thinning_then_degraded_and_nothing_deleted(tmp_path):
    cap_kb = 150
    st = ExperienceStore(tmp_path / "xp", max_total_mb=cap_kb / 1024.0)
    st.seed_cf_labelled({"strategy_paper_box": 10_000, "nowhere": 5, "main": -1})
    assert st.cf_labelled_counts() == {"strategy_paper_box": 10_000}
    order = {"OK": 0, "WARN": 1, "THINNING": 2, "DEGRADED": 3}
    seen: list[str] = []
    written_ids: set[str] = set()
    lines_prev = 0
    thin_kept = thin_dropped = 0
    i = 0
    while "DEGRADED" not in seen and i < 200:
        box = _cf_rows(4, tag="box%d_" % i, pad=300)                                     # uygun defter, rev 0
        main = _cf_rows(4, book="main", book_name="main", tag="m%d_" % i, pad=300)      # etiketli sayısı 0
        lab = _cf_rows(1, tag="lab%d_" % i, status="LABELLED", rev=1)                    # rev 1: seyreltilmez
        size_before = st.path.stat().st_size if st.path.exists() else 0
        w, rej = st.append_rows(box + main + lab)
        state = st.last_append["state"]
        seen.append(state)
        on_disk = [json.loads(ln) for ln in _lines(st.path)]
        new = on_disk[lines_prev:]
        assert len(on_disk) >= lines_prev, "HİÇBİR ŞEY SİLİNMEZ"
        lines_prev = len(on_disk)
        written_ids |= {r["row_id"] for r in new}
        if state in ("OK", "WARN"):
            assert (w, rej) == (9, 0) and not any(r.get("thinned") for r in new)
        elif state == "THINNING":
            got_box = [r for r in new if r["book"] == "strategy_paper_box" and r["rev"] == 0]
            assert all(r["thinned"] is True for r in got_box) and len(got_box) + st.last_append["thinned"] == 4
            assert sum(1 for r in new if r["book"] == "main") == 4 and not any(r.get("thinned") for r in new
                                                                               if r["book"] == "main")
            assert sum(1 for r in new if r["rev"] == 1) == 1, "etiket revizyonu seyreltilmez"
            assert {r["row_id"] for r in got_box} == {r["row_id"] for r in box if S._thin_keep(r)}, "deterministik"
            thin_kept += len(got_box)
            thin_dropped += st.last_append["thinned"]
        else:
            assert (w, rej) == (0, 9) and st.last_append["blocked_degraded"] == 9
            assert st.path.stat().st_size == size_before, "DEGRADED: bütün yazımlar durur"
        i += 1
    assert [s for j, s in enumerate(seen) if j == 0 or s != seen[j - 1]] == ["OK", "WARN", "THINNING", "DEGRADED"]
    assert all(order[a] <= order[b] for a, b in zip(seen, seen[1:])), seen
    assert thin_dropped > 0 and thin_kept > 0
    assert written_ids == {r["row_id"] for r in st.iter_all_rows()}
    assert st.pressure()["state"] == "DEGRADED" and st.summary()["counters"]["blocked_degraded"] == 9
    assert st.cf_labelled_counts()["strategy_paper_box"] > 10_000, "yazılan etiketli satırlar sayılır"
    # tavan yükseltilince (yeniden başlatma) yazım sürer — durum ÖLÇÜLÜR, hiçbir satır silinmemiştir
    st2 = ExperienceStore(tmp_path / "xp", max_total_mb=64)
    st2.load_seen()
    assert st2.append_rows(_cf_rows(2, tag="after")) == (2, 0) and st2.last_append["state"] == "OK"

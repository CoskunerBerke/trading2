# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2a — işlem günlüğü `tj_v1` (docs/SYSTEM_LEARNING_ENGINE_V1.md §4, §10 P2 kabul 1, 2, 3, 4, 8, 9).

Dünya (`tests/research_engine_p2_fixtures.py`): sentetik piyasa → canlı kuralın kararı (`paper_rules.decide_for`) →
worker'ın kendi muhasebe sınıfları (`FuturesLedgerV2`, `SpotLedger`) → P1a gecesi (kapanış arşivi) → mühürlü araştırma
deposu. Günlük yalnız arşivden + mühürlü depodan kurulur; ağ yok.
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import night_of  # noqa: E402
from research_engine_p2_fixtures import STEP, World, standard_world, utc, world_markets  # noqa: E402

from tradingbot.accounting.funding import static_rates  # noqa: E402
from tradingbot.research_engine import closes as C  # noqa: E402
from tradingbot.research_engine import journal as J  # noqa: E402
from tradingbot.research_engine.ledgers import parse_ts  # noqa: E402

NOW = utc(2026, 10, 6, 2)
TOL = D("1e-6")
STRATEGY_BOOKS = ("strategy_paper", "strategy_paper_m2", "strategy_paper_box", "strategy_paper_trend4h", "strategy_paper_candle4h")


def _ledger_docs(w: World) -> dict[str, tuple[str, dict]]:
    out = {}
    for p in sorted(w.v.state.rglob("futures_ledger.json")):
        book = "main_fut" if p.parent == w.v.state else p.parent.name
        out[book] = ("futures", json.loads(p.read_text(encoding="utf-8")))
    sp = w.v.state / "spot_ledger.json"
    if sp.exists():
        out["main_spot"] = ("spot", json.loads(sp.read_text(encoding="utf-8")))
    return out


def _build(w: World, now=NOW) -> dict:
    return J.build_journal(w.paths, now=now, src=w.source(), rawconfig=w.rc)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    w = standard_world(tmp_path_factory.mktemp("journal_world"))
    summary = _build(w)
    rows = list(J.iter_journal(w.paths))
    return w, summary, rows


# ============================================================================ kabul 1
def test_every_record_of_every_book_is_in_the_journal_exactly_once_and_sums_match_1e6(built):
    w, summary, rows = built
    docs = _ledger_docs(w)
    assert set(docs) == {"main_fut", "main_spot", *STRATEGY_BOOKS}
    keys = [r["trade_key"] for r in rows]
    assert len(keys) == len(set(keys)), "her kayıt günlükte TAM BİR KEZ"
    total = 0
    for book, (kind, doc) in docs.items():
        hist = doc["history"]
        assert hist, book
        total += len(hist)
        want = {C.trade_key(book, h) for h in hist}
        jr = {r["trade_key"]: r for r in rows if r["book"] == book}
        assert set(jr) == want, book
        assert {k for k, st in C.latest_closes(w.paths, book).items() if st.status == C.ST_ACTIVE} == want
        # Σ net_pnl / ücret / fonlama / kayma: ledger (worker'ın kendi alanları) ↔ günlük, 1e-6
        for lf, jf in (("pnl", "net_pnl"), ("fees", "fees_total"), ("funding", "funding_net"), ("slippage_cost", "slippage_cost")):
            ls = sum((D(str(h.get(lf) or 0)) for h in hist), D(0))
            js = sum((D(str(r.get(jf) or 0)) for r in jr.values()), D(0))
            assert abs(ls - js) <= TOL, (book, lf, ls, js)
            if kind == "spot" and lf == "funding":
                assert all(r["field_source"]["funding_net"] == J.NOT_APPLICABLE for r in jr.values())
    assert summary["rows"] == len(rows) == total
    rec = J.reconcile_journal(w.paths)
    assert rec["status"] == "OK", rec
    for b, body in rec["books"].items():
        assert body["ok"] and body["duplicates"] == 0 and body["ledger"]["n_missing"] == 0, b
        assert all(s["ok"] for s in body["sums"].values()) and all(s["ok"] for s in body["ledger"]["sums"].values()), b


def test_spot_partial_sales_are_separate_rows_of_one_position_group_with_provenance_stop(built):
    _w, _s, rows = built
    sp = [r for r in rows if r["book"] == "main_spot"]
    assert len(sp) == 2 and len({r["trade_key"] for r in sp}) == 2
    assert len({r["position_group"] for r in sp}) == 1 and sp[0]["position_group"].startswith("main_spot|ETH/USDT|")
    for r in sp:
        assert r["kind"] == "spot" and r["venue"] == "spot" and r["leverage"] == 1
        assert r["field_source"]["leverage"] == J.MEASURED
        for f in ("funding_paid", "funding_received", "funding_net", "margin", "liquidation_price"):
            assert r["field_source"][f] == J.NOT_APPLICABLE, f
        # stop ve R paydası ana botun provenance'ından (alış emri = FIFO lotunun dolum kimliğinin emir kısmı)
        assert r["field_source"]["initial_stop"] == J.MEASURED and "entry_provenance" in r["field_notes"]["initial_stop"]
        assert r["risk_usdt"] is not None and r["net_r"] is not None
        assert any(s["what"] == "entry_provenance" for s in r["sources"])
        assert r["fidelity"]["status"] == "NOT_ELIGIBLE" and r["fidelity"]["primary_reason"] == "SPOT"


def test_rotated_records_stay_in_the_journal_and_are_not_rebuilt(built, tmp_path):
    w0, _s, rows0 = built
    shutil.copytree(w0.root, tmp_path, dirs_exist_ok=True)
    w = World.attach(tmp_path)
    led = w.v.futs["strategy_paper"]
    gone = [C.trade_key("strategy_paper", h.to_dict()) for h in led.history[:3]]
    led.history = led.history[3:]                             # ledger rotasyonu: en eski 3 kayıt ledger'dan düştü
    r1, _ = w.v.night(night_of("2026-10-07"))
    assert r1["books"]["strategy_paper"]["vanished"] == 0
    s = _build(w, now=utc(2026, 10, 7, 2))
    rows = {r["trade_key"]: r for r in J.iter_journal(w.paths)}
    old = {r["trade_key"]: r for r in rows0}
    assert set(rows) == set(old), "ledger'dan düşen kayıtlar günlükte kalır (arşiv)"
    for k in gone:
        assert rows[k] == old[k]
    assert s["built_rows"] == 0, "girdisi değişmeyen satır yeniden kurulmaz"
    rec = J.reconcile_journal(w.paths)
    assert rec["status"] == "OK" and rec["books"]["strategy_paper"]["ledger"]["held"] == len(led.history)


# ============================================================================ kabul 2
def test_every_owner_field_is_filled_or_labelled_and_every_field_has_a_label(built):
    _w, summary, rows = built
    assert summary["owner_fields_ok"] is True
    owner = set(J.OWNER_FIELDS) | {f for g in J.OWNER_GROUPS for f in J.FIELD_GROUPS[g]}
    for r in rows:
        assert J.owner_fields_ok(r), r["trade_key"]
        assert set(r["field_source"]) == set(J.ALL_FIELDS)
        for f in J.ALL_FIELDS:
            lab = r["field_source"][f]
            assert lab in J.LABELS, (f, lab)
            if lab in (J.MEASURED, J.RECONSTRUCTED):
                assert r[f] is not None, (r["trade_key"], f)
            if f in owner and r[f] is None:
                assert lab in (J.MISSING, J.MODELED, J.NOT_APPLICABLE), (r["trade_key"], f, lab)
        assert r["missing_fields"] == sorted(f for f in J.ALL_FIELDS if r["field_source"][f] == J.MISSING)
        assert r["field_source"]["slippage_cost"] == J.MODELED and r["field_source"]["spread_cost"] == J.MODELED
        assert r["field_source"]["entry_fill"] == J.MEASURED and r["field_source"]["net_pnl"] == J.MEASURED
    by_book = {}
    for r in rows:
        by_book.setdefault(r["book"], []).append(r)
    for b in STRATEGY_BOOKS:
        for r in by_book[b]:
            assert r["tactic"] == J.BOOK_TACTIC[b][0] and r["field_source"]["tactic"] == J.MEASURED
            assert r["variation_id"] and r["field_source"]["variation_id"] in (J.MEASURED, J.RECONSTRUCTED)
            assert r["field_source"]["targets"] == J.RECONSTRUCTED and r["signal_ctx"]
            assert r["config_epoch"] and r["field_source"]["config_epoch"] == J.RECONSTRUCTED
    for r in by_book["main_fut"]:
        assert r["decision_id"].startswith("dec-") and r["field_source"]["targets"] == J.MEASURED
        assert r["agents_ctx"]["regime"] == "TREND_UP" and r["field_source"]["signal_ctx"] == J.NOT_APPLICABLE


def test_owner_field_checker_rejects_an_unlabelled_or_falsely_measured_field(built):
    _w, _s, rows = built
    r = json.loads(json.dumps(rows[0]))
    r["entry_fill"] = None
    assert not J.owner_fields_ok(r), "MEASURED etiketli boş alan reddedilir"
    r = json.loads(json.dumps(rows[0]))
    del r["field_source"]["leverage"]
    assert not J.owner_fields_ok(r)


# ============================================================================ kabul 3
def test_fee_identity_holds_and_costs_are_not_double_counted(built):
    w, summary, rows = built
    assert summary["fee_identity_violations"] == 0
    for r in rows:
        assert r["fee_identity_ok"] is True
        assert abs(D(r["fees_total"]) - (D(r["entry_fee"]) + D(r["exit_fee"]))) <= TOL
        if r["risk_usdt"] is None:
            continue
        risk = D(r["risk_usdt"])
        cr = r["cost_r"]
        eps = D("1e-18")                                      # Decimal bağlamının (28 hane) son hanesi
        assert abs(D(cr["fee"]) - D(r["fees_total"]) / risk) <= eps, "ücret R'ı = fees / risk (bir kez)"
        assert abs(D(cr["total"]) - (D(cr["fee"]) + D(cr["funding"]))) <= eps
        assert abs(D(r["gross_r"]) - D(cr["total"]) - D(r["net_r"])) <= eps, "brüt − maliyet = net (kayma brütün içinde)"
    # regresyon: `learn/labels.label_outcome` entry_fee varken ücreti İKİ KEZ sayar; günlük onu kullanmaz
    from tradingbot.learn.labels import label_outcome
    docs = _ledger_docs(w)
    h = docs["strategy_paper"][1]["history"][0]
    row = next(r for r in rows if r["trade_key"] == C.trade_key("strategy_paper", h))
    risk = D(str(h["features"]["risk_usdt"]))
    lo = label_outcome(h)
    once = float(D(str(h["fees"])) / risk)
    assert lo["fee_drag_r"] == pytest.approx(2 * once, abs=1e-4), "labels.py:45 çift sayımı hâlâ orada (belgelenmiş)"
    assert abs(lo["fee_drag_r"] - once) > 1e-3, "çift sayım ölçülebilir büyüklükte"
    assert float(D(row["cost_r"]["fee"])) == pytest.approx(once, rel=1e-12), "günlük ücreti bir kez sayar"


def test_fee_identity_violation_is_flagged_not_hidden(built):
    w, _s, rows = built
    key = rows[0]["trade_key"]
    book = rows[0]["book"]
    loc = J.scan_archive(w.paths, book)[key]
    rec = J.load_records(w.paths, book, {key: loc})[key]
    rec = json.loads(json.dumps(rec))
    rec["fees"] = str(D(str(rec["fees"])) + D("0.5"))
    ctx = J.BuildContext(paths=w.paths, now=NOW, src=None, rawconfig=w.rc,
                         sides=J.SideSources(w.v.state, ids_by_book={}, keys=set()), exec_by_book={}, equity_points=[],
                         write_paths=False)
    row = J.build_row(loc, rec, "futures", ctx)
    assert row["fee_identity_ok"] is False


# ============================================================================ kabul 4
def test_net_r_uses_risk_usdt_and_equals_ledger_r_multiple_1e9(built):
    w, summary, rows = built
    assert summary["r_check_failures"] == 0
    docs = _ledger_docs(w)
    n = 0
    for book, (kind, doc) in docs.items():
        if kind != "futures":
            continue
        for h in doc["history"]:
            r = next(x for x in rows if x["trade_key"] == C.trade_key(book, h))
            assert r["risk_usdt"] == h["features"]["risk_usdt"] and r["field_source"]["risk_usdt"] == J.MEASURED
            assert abs(D(r["net_r"]) - D(str(h["r_multiple"]))) <= D("1e-9"), r["trade_key"]
            assert r["r_check"]["ok"] is True
            n += 1
    assert n >= 30


def test_net_r_still_equals_r_multiple_after_late_funding_rev(built, tmp_path):
    w0, _s, rows0 = built
    shutil.copytree(w0.root, tmp_path, dirs_exist_ok=True)
    w = World.attach(tmp_path)
    led = w.v.futs[""]
    pend = [h for h in led.history if not (h.features.get("funding_coverage") or {}).get("complete", True)]
    assert len(pend) == 1, "standart dünyada tam bir ana bot işlemi 08:00 fonlamasını bekliyor"
    key = C.trade_key("main_fut", pend[0].to_dict())
    old = next(r for r in rows0 if r["trade_key"] == key)
    assert old["funding_complete"] is False and old["rev"] == 0
    posted = led.settle_late_funding(static_rates({"ETH/USDT": "0.0003"}), now=utc(2026, 10, 6, 10),
                                     mark_for=lambda s, t: D("19000"))
    assert posted
    w.v.night(night_of("2026-10-07"))
    s = _build(w, now=utc(2026, 10, 7, 2))
    assert s["built_rows"] == 1, "yalnız revize kayıt yeniden kurulur"
    new = next(r for r in J.iter_journal(w.paths) if r["trade_key"] == key)
    h = next(x for x in led.history if x.id == pend[0].id).to_dict()
    assert new["rev"] == 1 and new["funding_complete"] is True and new["net_pnl"] != old["net_pnl"]
    assert abs(D(new["net_r"]) - D(str(h["r_multiple"]))) <= D("1e-9") and new["r_check"]["ok"]
    assert new["risk_usdt"] == old["risk_usdt"], "geç fonlama R paydasını değiştirmez"
    assert D(new["net_r"]) != D(old["net_r"])
    assert J.reconcile_journal(w.paths)["status"] == "OK"


# ============================================================================ kabul 8
def test_same_seal_gives_byte_identical_journal_and_paths(built, tmp_path):
    w0, _s, _rows = built
    a, b = tmp_path / "a", tmp_path / "b"
    wa, wb = standard_world(a), standard_world(b)
    _build(wa)
    _build(wb)
    ta, tb = _tree(wa.paths.journal), _tree(wb.paths.journal)
    assert ta and ta == tb, "iki bağımsız kurulum, aynı mühür → bayt-özdeş günlük"
    assert _tree(wa.paths.paths_root) == _tree(wb.paths.paths_root) and _tree(wa.paths.paths_root)
    assert ta == _tree(w0.paths.journal)
    # aynı mühürle yeniden çalıştırma: hiçbir satır yeniden kurulmaz, hiçbir dosya değişmez
    s2 = _build(wa)
    assert s2["built_rows"] == 0 and s2["months_written"] == [] and _tree(wa.paths.journal) == ta
    # günlüğü silip baştan kurmak aynı baytları verir
    shutil.rmtree(wa.paths.journal)
    _build(wa)
    assert _tree(wa.paths.journal) == ta
    for p in wa.paths.journal_tj.glob("????-??.jsonl.gz"):
        with open(p, "rb") as fh:
            assert fh.read(10)[4:8] == b"\x00\x00\x00\x00", "gzip mtime=0"
        lines = gzip.decompress(p.read_bytes()).decode("utf-8").splitlines()
        keys = [(json.loads(x)["closed_at"], json.loads(x)["trade_key"]) for x in lines]
        assert keys == sorted(keys), "satırlar (closed_at, trade_key) sırasında"


# ============================================================================ kabul 9
CTX_FIELDS = ("range_pos_20", "dist_20d_high_atr", "dist_20d_low_atr", "dist_ema200_atr", "dist_level_atr", "situation_entry",
              "btc_ctx_entry", "signal_ctx", "targets", "signal_ts", "stop_dist_atr", "funding_rate_at_entry", "variation_id")


def _perturb(frames: dict[str, dict[str, pd.DataFrame]], t_ms: int, k: float) -> dict[str, dict[str, pd.DataFrame]]:
    """`t`'de henüz KAPANMAMIŞ (kapanışı t'den sonra) her barı bozar (oluşan bar dahil)."""
    out = {}
    for sym, fr in frames.items():
        out[sym] = {}
        for tf, df in fr.items():
            df = df.copy()
            fut = (df["timestamp"] + STEP[tf]) > t_ms
            for c in ("open", "high", "low", "close"):
                df.loc[fut, c] = (df.loc[fut, c] * k).round(2)
            out[sym][tf] = df
    return out


def test_no_lookahead_context_is_identical_when_every_bar_after_entry_changes(built, tmp_path):
    w0, _s, rows0 = built
    for r in rows0:                                            # as_of < opened_at, satır satır
        o = int(parse_ts(r["opened_at"]).timestamp() * 1000)
        if r["field_source"]["situation_entry"] == J.RECONSTRUCTED:
            assert r["situation_entry"]["as_of_ms"] < o
        if r["field_source"]["btc_ctx_entry"] in (J.RECONSTRUCTED, J.MEASURED):
            assert r["btc_ctx_entry"]["as_of_ms"] < o
        if r["signal_ts"] is not None:
            assert r["signal_ts"] < o
    pivot = next(r for r in rows0 if r["book"] == "strategy_paper_m2" and r["trade_id"] == "F00005")
    t = int(parse_ts(pivot["opened_at"]).timestamp() * 1000)
    shutil.copytree(w0.root, tmp_path, dirs_exist_ok=True)
    w = World.attach(tmp_path)
    shutil.rmtree(w.paths.research / "store")
    shutil.rmtree(w.paths.journal)
    shutil.rmtree(w.paths.paths_root)
    w.seal(frames=_perturb(world_markets(), t, 1.07))
    _build(w)
    new = {r["trade_key"]: r for r in J.iter_journal(w.paths)}
    checked = changed_path = 0
    for r in rows0:
        if "ADA" not in r["symbol"] or int(parse_ts(r["opened_at"]).timestamp() * 1000) > t:
            continue
        n = new[r["trade_key"]]
        for f in CTX_FIELDS:
            assert n[f] == r[f], (r["trade_key"], f)
        assert n["rehydrate"]["status"] == r["rehydrate"]["status"] == "OK"
        checked += 1
        if int(parse_ts(r["closed_at"]).timestamp() * 1000) > t:
            assert n["mfe_r"] != r["mfe_r"] or n["mae_r"] != r["mae_r"], "bozma yolda görünür (deney geçerli)"
            changed_path += 1
    assert checked >= 8 and changed_path >= 1
    later = [k for k, r in new.items() if "ADA" in r["symbol"] and int(parse_ts(r["opened_at"]).timestamp() * 1000) > t + 86_400_000]
    assert later and any(new[k]["range_pos_20"] != next(x for x in rows0 if x["trade_key"] == k)["range_pos_20"] for k in later)


def test_entry_context_reads_only_daily_bars_closed_before_entry(built):
    w, _s, _rows = built
    src = w.source()
    o = utc(2026, 10, 2, 0, 20, 47)
    as_of = int(o.timestamp() * 1000) - 1
    ec, _used = J.entry_context(src, "futures", "ADA/USDT", as_of, D("30"))
    assert ec["daily_last_open_ms"] + 86_400_000 <= as_of
    assert ec["daily_last_open_ms"] == int((o - timedelta(days=1)).replace(hour=0, minute=0, second=0).timestamp() * 1000)

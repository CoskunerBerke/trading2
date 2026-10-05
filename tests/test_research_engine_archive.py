# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P1a — kapanış/hareket arşivi, gecelik anlık görüntü, uzlaştırma, geri yükleme, rotasyon payı
(docs/SYSTEM_LEARNING_ENGINE_V1.md §4.2, §3.6; P1a depo kabul testleri 1–4, 8, 16 ve yalıtımın depo tarafı).

Ledger'lar worker'ın kendi muhasebe sınıflarıyla üretilir (`tests/research_engine_fixtures.py`); motor onları yalnız
ham JSON olarak okur.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sys
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeVps, at, night_of, trade  # noqa: E402

from tradingbot.accounting import AmountType, SizeSpec  # noqa: E402
from tradingbot.accounting.funding import static_rates  # noqa: E402
from tradingbot.research_engine import closes as C  # noqa: E402
from tradingbot.research_engine import ledgers as L  # noqa: E402
from tradingbot.research_engine import lock as LK  # noqa: E402
from tradingbot.research_engine import paths as P  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOL = D("1e-6")


def _keys(v: FakeVps, book: str) -> dict:
    return C.latest_closes(v.paths, book)


def _entry_rows(v: FakeVps, book: str) -> list[dict]:
    return list(C.iter_rows(v.paths.book_entries_dir(book)))


# ============================================================================ kabul 1: kapanış arşivi
def test_closes_archive_survives_5000_history_rotation_and_reconciles(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut("strategy_paper_box", equity="100000")
    t0 = at("2026-09-01", 0, 5)
    for i in range(3000):
        trade(led, "ETH/USDT", t0 + timedelta(seconds=20 * i), 100.0, 100.5 if i % 3 else 99.5, hold=timedelta(seconds=10))
    r1, _ = v.night(night_of("2026-09-02"))
    b1 = r1["books"]["strategy_paper_box"]
    assert b1["new_closes"] == 3000 and b1["recon_record"]["status"] == "OK" and b1["align"] == C.ALIGN_BASELINE
    t1 = at("2026-09-02", 2, 0)
    for i in range(2600):
        trade(led, "BTC/USDT", t1 + timedelta(seconds=20 * i), 100.0, 101.0 if i % 2 else 99.0, hold=timedelta(seconds=10))
    assert len(led.history) == 5000, "ledger 5000'de döndü: ilk 600 kayıt artık ledger'da yok"
    r2, _ = v.night(night_of("2026-09-03"))
    b2 = r2["books"]["strategy_paper_box"]
    arch = _keys(v, "strategy_paper_box")
    assert len(arch) == 5600 and b2["new_closes"] == 2600 and b2["vanished"] == 0
    assert all(st.status == C.ST_ACTIVE for st in arch.values()), "rotasyonla düşen kayıtlar arşivde AKTİF kalır"
    rotated = [st for st in arch.values() if st.proj["symbol"] == "ETH/USDT"]
    assert len(rotated) == 3000, "ledger'dan düşen 600 kayıt dahil bütün ETH kapanışları arşivde"
    rec = b2["recon_record"]
    assert rec["status"] == "OK" and rec["missing"] == 0 and rec["tolerance"] == "0.000001"
    assert C.F_INCONSISTENT not in b2["flags"]
    # 2600 işlem × 3 hareket > 2000: hareket listesi bir gecede döndü → dürüst boşluk işareti, pencere EKSİK
    assert b2["align"] == C.ALIGN_GAP and C.F_ENTRIES_GAP in b2["flags"] and b2["recon_wallet"]["status"] == C.RECON_GAP
    row = v.rows()["2026-09-02"]
    assert row["status"] == "EKSİK" and "ENTRIES_GAP" in row["status_reason"]
    assert b2["rotation"]["history"]["full"] is True and b2["rotation"]["entries"]["full"] is True
    # yeniden çalıştırma 0 satır ekler
    r3 = v.s1a(night_of("2026-09-03") + timedelta(minutes=5))
    b3 = r3["books"]["strategy_paper_box"]
    assert (b3["new_closes"], b3["new_entries"], b3["revised"]) == (0, 0, 0) and b3["align"] == C.ALIGN_OK


def _pending_funding_trade(led, day: str):
    """Kapanışta fonlaması BEKLEYEN işlem (08:00 uzlaşması oran yokken geçti)."""
    t0 = at(day, 7)
    assert led.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 2), stop=D("95"), now=t0) is not None
    led.tick({"ETH/USDT": D("100.5")}, now_utc=t0 + timedelta(minutes=30), funding_rate_lookup=None)
    rec = led.close_manual("ETH/USDT", D("101"), now=t0 + timedelta(hours=2))
    assert rec.features["funding_coverage"]["complete"] is False
    return rec


def test_late_funding_appends_rev_and_marks_day_revized_append_only(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    v.night(night_of("2026-09-10"))
    rec = _pending_funding_trade(led, "2026-09-10")
    v.night(night_of("2026-09-11"))
    row = v.rows()["2026-09-10"]
    assert row["status"] == "GEÇİCİ" and "FONLAMA_BEKLİYOR" in row["status_reason"] and row["rev"] == 0
    pnl0 = row["total"]["pnl_rec"]
    v.night(night_of("2026-09-12"))
    before = v.paths.target_daily.read_bytes()
    posted = led.settle_late_funding(static_rates({"ETH/USDT": "0.0001"}), now=at("2026-09-12", 10),
                                     mark_for=lambda s, t: D("100"))
    assert posted and posted[0]["amount"] == "-0.0100000"
    r1, r3 = v.night(night_of("2026-09-13"))
    b = r1["books"]["main_fut"]
    assert b["revised"] == 1 and b["new_closes"] == 0
    k = C.trade_key("main_fut", rec.to_dict())
    rows = [r for r in C.iter_rows(v.paths.book_closes_dir("main_fut")) if r.get("trade_key") == k and r.get("row") == "close"]
    assert [r["rev"] for r in rows] == [0, 1] and "pnl" in rows[1]["changed"] and "funding" in rows[1]["changed"]
    assert rows[0]["record"]["pnl"] != rows[1]["record"]["pnl"], "eski revizyon satırı AYNEN durur"
    after = v.paths.target_daily.read_bytes()
    assert after.startswith(before), "günlük hedef dosyası yalnız eklenir"
    row = v.rows()["2026-09-10"]
    assert row["rev"] >= 1 and row["revised"] is True and "REVİZE" in row["label"] and "GEÇ_FONLAMA" in row["revision_reason"]
    assert row["total"]["pnl_rec"] == pytest.approx(pnl0 - 0.01, abs=1e-9), "geç fonlama kayıt görünümünde closed_at gününe"
    late = [r for r in _entry_rows(v, "main_fut") if "late funding" in r["entry"]["note"]]
    assert len(late) == 1 and late[0]["observed_at"].startswith("2026-09-13")
    w12 = v.rows()["2026-09-12"]["books"]["main_fut"]
    assert w12["pnl_wal"] == pytest.approx(-0.01, abs=1e-12), "cüzdan görünümünde gözlendiği pencereye (W 09-12)"
    assert v.rows()["2026-09-10"]["books"]["main_fut"]["pnl_wal"] != w12["pnl_wal"]
    v.night(night_of("2026-09-14"))
    row = v.rows()["2026-09-10"]
    assert row["status"] == "KESİN" and row["label"].startswith("KESİN · REVİZE"), "3 gün + kapsama tamam → KESİN, REVİZE görünür"


def test_unsettled_funding_day_becomes_eksik_after_seven_days(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    v.night(night_of("2026-09-10"))
    _pending_funding_trade(led, "2026-09-10")
    for d in range(11, 19):
        v.night(night_of(f"2026-09-{d:02d}"))
        st = v.rows()["2026-09-10"]
        if d < 17:
            assert st["status"] == "GEÇİCİ", d
    assert st["status"] == "EKSİK" and "EKSİK_FONLAMA" in st["status_reason"]


def test_backdated_close_revises_previous_day_row(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    v.night(night_of("2026-09-20"))
    assert led.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 2), stop=D("95"),
                    now=at("2026-09-20", 10)) is not None
    led.tick({"ETH/USDT": D("101")}, now_utc=at("2026-09-20", 22))
    v.night(night_of("2026-09-21"))
    row = v.rows()["2026-09-20"]
    assert row["total"]["n_trades"] == 0 and row["rev"] == 0
    # koruyucu izleyici kapanışı bar kapanışına geri tarihler: closed_at 09-20 23:00, S(09-21)'den sonra yazıldı
    led.close_manual("ETH/USDT", D("102"), now=at("2026-09-20", 23))
    r1, _ = v.night(night_of("2026-09-22"))
    new = [r for r in C.iter_rows(v.paths.book_closes_dir("main_fut")) if r.get("row") == "close"]
    assert len(new) == 1 and new[0]["late"] is True and r1["books"]["main_fut"]["late_appearances"] == 1
    row = v.rows()["2026-09-20"]
    assert row["total"]["n_trades"] == 1 and row["revised"] and "GEÇ_GELEN_KAPANIŞ" in row["revision_reason"]
    w21 = v.rows()["2026-09-21"]["books"]["main_fut"]
    assert w21["pnl_wal"] != 0 and w21["status"] != "EKSİK", "hareketler gözlendikleri pencerede; MTM eşitliği tutar"


def test_record_view_reconciliation_detects_a_tampered_archive(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    for i in range(3):
        trade(led, "ETH/USDT", at("2026-09-05", 3 + i), 100.0, 103.0)
    r1, _ = v.night(night_of("2026-09-06"))
    assert r1["books"]["main_fut"]["recon_record"]["status"] == "OK"
    seg = C.segment_files(v.paths.book_closes_dir("main_fut"))[0]
    rows = list(P.read_jsonl_gz(seg))
    rows[0]["proj"]["pnl"] = str(D(rows[0]["proj"]["pnl"]) + D("0.00001"))
    seg.write_bytes(P.gzip_bytes("".join(P.json_line(r) for r in rows).encode("utf-8")))
    res = C.reconcile_records(v.paths, "main_fut", "futures", [h.to_dict() for h in led.history])
    assert res["status"] == C.RECON_INCONSISTENT and res["mismatches"][0]["field"] == "pnl"


# ============================================================================ kabul 2: spot
def test_spot_partial_sales_are_separate_rows_with_one_position_group(tmp_path):
    v = FakeVps(tmp_path)
    fut = v.fut()
    sp = v.spot(cash="1000")
    v.night(night_of("2026-09-01"))
    assert sp.market_buy("ETH/USDT", qty=D("1"), ref_price=D("100"), now=at("2026-09-01", 4)).status.value == "FILLED"
    assert sp.market_sell("ETH/USDT", qty=D("0.3"), ref_price=D("104"), now=at("2026-09-01", 6)).status.value == "FILLED"
    assert sp.market_sell("ETH/USDT", qty=D("0.2"), ref_price=D("106"), now=at("2026-09-01", 8)).status.value == "FILLED"
    assert fut.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("50"), AmountType.NOTIONAL, 1), stop=D("90"),
                    now=at("2026-09-01", 9)) is not None
    fut.tick({"ETH/USDT": D("105")}, now_utc=at("2026-09-01", 23))
    r1, _ = v.night(night_of("2026-09-02"))
    sb = r1["books"]["main_spot"]
    assert sb["new_closes"] == 2 and sb["recon_wallet"]["status"] == "OK"
    rows = [r for r in C.iter_rows(v.paths.book_closes_dir("main_spot")) if r.get("row") == "close"]
    assert len({r["trade_key"] for r in rows}) == 2 and len({r["position_group"] for r in rows}) == 1
    assert rows[0]["position_group"].startswith("main_spot|ETH/USDT|")
    for r in rows:
        assert r["proj"]["leverage"] == 1 and r["proj"]["funding"] is None and r["proj"]["funding_complete"] == "NOT_APPLICABLE"
        assert r["proj"]["venue"] == "spot" and r["record"]["market_type"] == "SPOT"
    der = [r for r in C.iter_rows(v.paths.book_closes_dir("main_spot")) if r.get("row") == "derived"]
    assert {d["min_to_next_funding_source"] for d in der} == {"NOT_APPLICABLE"}
    st = r1["books"]["main_spot"]["state"]
    assert st["marks"]["ETH/USDT"]["source"] == C.MARK_PERP_PROXY and st["mtm_complete"] is True
    row = v.rows()["2026-09-01"]
    sp_row = row["books"]["main_spot"]
    exp = float(sum((e.amount for e in sp.entries if e.kind.value in ("PNL", "FEE")), D(0)))
    assert sp_row["pnl_wal"] == pytest.approx(exp, abs=1e-9) and sp_row["n_trades"] == 2 and sp_row["n_spot_no_r"] == 2
    assert row["groups"]["main"]["pnl_wal"] == pytest.approx(sp_row["pnl_wal"] + row["books"]["main_fut"]["pnl_wal"], abs=1e-9)
    assert row["total"]["r_mtm"] is not None
    e_tot = row["books"]["main_spot"]["e_start"] + row["books"]["main_fut"]["e_start"]
    assert row["total"]["r_mtm"] == pytest.approx(row["total"]["pnl_mtm"] / e_tot * 100, rel=1e-5), "r_total spot dahil"


# ============================================================================ kabul 3: hareket arşivi
def test_entries_archive_survives_2000_rotation_rerun_adds_zero_and_keeps_every_kind(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut(equity="100000")
    sp = v.spot(cash="1000")
    v.night(night_of("2026-09-01"))
    total = 0
    for night in range(2, 5):
        day = f"2026-09-0{night - 1}"
        t0 = at(day, 2)
        for i in range(500):                       # 500 işlem × 3 hareket = 1500 < 2000 (gece başına)
            trade(led, "ETH/USDT", t0 + timedelta(seconds=30 * i), 100.0, 100.4, hold=timedelta(seconds=10))
        total += 1500
        r1, _ = v.night(night_of(f"2026-09-0{night}"))
        assert r1["books"]["main_fut"]["align"] == C.ALIGN_OK and r1["books"]["main_fut"]["new_entries"] == 1500
        assert r1["books"]["main_fut"]["recon_wallet"]["status"] == "OK"
    assert len(led.entries) == 2000 and total == 4500
    rows = [r for r in _entry_rows(v, "main_fut") if not r["pre_archive"]]
    assert len(rows) == 4500 and len({r["key"] for r in rows}) == 4500, "rotasyona rağmen bütün hareketler, tekil anahtarlı"
    r_again = v.s1a(night_of("2026-09-04") + timedelta(minutes=3))
    assert r_again["books"]["main_fut"]["new_entries"] == 0, "yeniden çalıştırma 0 satır ekler"
    # her tür: FEE/PNL/FUNDING (açık, late funding, funding reversal)/TRANSFER (spot alış)/LIQ_FEE
    t = at("2026-09-04", 7)
    rec_pending = _pending_funding_trade(led, "2026-09-04")
    lk = type("L", (), {"__call__": lambda self, s, t: D("0.0002"), "settlement_mark": lambda self, s, t: D("100")})()
    assert led.open("BTC/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 2), stop=D("95"), now=t) is not None
    led.tick({"BTC/USDT": D("100.2")}, now_utc=t + timedelta(hours=2), funding_rate_lookup=lk)
    led.close_manual("BTC/USDT", D("100.1"), now=t + timedelta(minutes=50))           # 08:00'den önce: reversal
    led.settle_late_funding(static_rates({"ETH/USDT": "0.0001"}), now=t + timedelta(hours=5), mark_for=lambda s, tt: D("100"))
    assert led.open("SOL/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 10), now=t + timedelta(hours=6)) is not None
    assert [c.exit_reason for c in led.tick({"SOL/USDT": D("80")}, now_utc=t + timedelta(hours=7))] == ["likidasyon"]
    sp.market_buy("ETH/USDT", qty=D("0.5"), ref_price=D("100"), now=t)
    r1, _ = v.night(night_of("2026-09-05"))
    assert r1["books"]["main_fut"]["recon_wallet"]["status"] == "OK" and rec_pending is not None
    kinds = {r["entry"]["kind"] for r in _entry_rows(v, "main_fut")} | {r["entry"]["kind"] for r in _entry_rows(v, "main_spot")}
    assert {"FEE", "PNL", "FUNDING", "TRANSFER", "LIQ_FEE"} <= kinds
    notes = " | ".join(r["entry"]["note"] for r in _entry_rows(v, "main_fut"))
    assert "late funding" in notes and "funding reversal" in notes
    syms = {r["symbol"] for r in _entry_rows(v, "main_fut") if r["entry"]["kind"] == "FUNDING"}
    assert syms <= {"ETH/USDT", "BTC/USDT"} and None not in syms, "hareket ref_id üzerinden sembole bağlanır"
    # hizalama kaybı: bir gecede > 2000 yeni hareket → ENTRIES_GAP ve o pencere EKSİK
    t2 = at("2026-09-05", 2)
    for i in range(800):
        trade(led, "ETH/USDT", t2 + timedelta(seconds=30 * i), 100.0, 100.4, hold=timedelta(seconds=10))
    r1, _ = v.night(night_of("2026-09-06"))
    assert r1["books"]["main_fut"]["align"] == C.ALIGN_GAP and r1["books"]["main_fut"]["recon_wallet"]["status"] == C.RECON_GAP
    gap_rows = [r for r in _entry_rows(v, "main_fut") if r["gap_before"]]
    assert len(gap_rows) == 1
    row = v.rows()["2026-09-05"]
    assert row["status"] == "EKSİK" and "ENTRIES_GAP" in row["books"]["main_fut"]["status_reason"]


# ============================================================================ kabul 4: anlık görüntü eşitliği
def test_snapshot_wallet_identity_futures_and_spot_cash(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    sp = v.spot(cash="2000")
    v.night(night_of("2026-09-01"))
    lk = type("L", (), {"__call__": lambda self, s, t: D("0.0001"), "settlement_mark": lambda self, s, t: D("100")})()
    for d in range(1, 5):
        day = f"2026-09-0{d}"
        trade(led, "ETH/USDT", at(day, 3), 100.0, 101.0 + d)
        assert led.open("BTC/USDT", "SHORT", D("100"), SizeSpec(D("80"), AmountType.NOTIONAL, 3), stop=D("110"),
                        targets=[D("98"), D("95")], now=at(day, 6)) is not None
        led.tick({"BTC/USDT": {"last": "97.5", "high": "100.5", "low": "97.5"}}, now_utc=at(day, 9), funding_rate_lookup=lk)
        led.close_manual("BTC/USDT", D("99"), now=at(day, 20))
        sp.market_buy("ETH/USDT", qty=D("1"), ref_price=D(str(100 + d)), now=at(day, 4))
        sp.market_sell("ETH/USDT", qty=D("0.4"), ref_price=D(str(103 + d)), now=at(day, 15))
        r1, _ = v.night(night_of(f"2026-09-0{d + 1}"))
        fw, sw = r1["books"]["main_fut"]["recon_wallet"], r1["books"]["main_spot"]["recon_wallet"]
        assert fw["status"] == "OK" and abs(D(fw["diff"])) <= TOL
        assert sw["status"] == "OK" and abs(D(sw["diff_cash"])) <= TOL and abs(D(sw["diff_book_value"])) <= TOL
        assert D(sw["released_cost"]) > 0, "satışta nakde dönen maliyet hareket olarak yazılmaz; eşitlik bunu ekler"
    tp1 = [f for h in led.history for f in h.fills if f.kind == "hedef1"]
    assert tp1, "TP1 kısmi çıkışı sınandı"
    # bozulma: hareketsiz cüzdan farkı → INCONSISTENT
    led.wallet_balance += D("0.01")
    r1, _ = v.night(night_of("2026-09-06"))
    assert r1["books"]["main_fut"]["recon_wallet"]["status"] == C.RECON_INCONSISTENT and r1["inconsistent"] is True
    sp.cash += D("0.5")
    r1, _ = v.night(night_of("2026-09-07"))
    assert r1["books"]["main_spot"]["recon_wallet"]["status"] == C.RECON_INCONSISTENT
    assert v.rows()["2026-09-06"]["books"]["main_spot"]["status"] == "EKSİK"


# ============================================================================ kabul 8: geri yükleme
def test_restore_marks_restored_away_not_inconsistent(tmp_path):
    v = FakeVps(tmp_path)
    led = v.fut()
    v.night(night_of("2026-09-10"))
    for i in range(3):
        trade(led, "ETH/USDT", at("2026-09-10", 3 + i), 100.0, 102.0)
    v.night(night_of("2026-09-11"))
    backup = json.loads(v.fut_path().read_text(encoding="utf-8"))
    for i in range(2):
        trade(led, "BTC/USDT", at("2026-09-11", 5 + i), 100.0, 97.0)
    v.night(night_of("2026-09-12"))
    # geri yükleme: state eski yedekten; restore.sh mevcut state'i state.pre-restore-<ts> olarak kenara alır
    (v.data / "state.pre-restore-20260912T120000").mkdir()
    v.fut_path().write_text(json.dumps(backup), encoding="utf-8")
    r1 = v.s1a(night_of("2026-09-13"))
    b = r1["books"]["main_fut"]
    assert b["restore"] and "SEQ_DECREASED" in b["restore"]["evidence"]
    assert any(e.startswith("PRE_RESTORE_DIR:state.pre-restore-") for e in b["restore"]["evidence"])
    assert b["restored_away"] == 2 and b["vanished"] == 0 and C.F_INCONSISTENT not in b["flags"]
    assert b["recon_wallet"]["status"] == C.RECON_RESTORED and r1["restore_events"] == ["main_fut"]
    assert r1["inconsistent"] is False
    arch = _keys(v, "main_fut")
    away = [st for st in arch.values() if st.status == C.ST_RESTORED_AWAY]
    assert len(away) == 2 and all(st.proj["symbol"] == "BTC/USDT" for st in away), "silinmez, işaretlenir"
    v.s3(night_of("2026-09-13") + timedelta(minutes=1))
    rows = v.rows()
    assert rows["2026-09-12"]["status"] == "EKSİK" and "RESTORE" in rows["2026-09-12"]["books"]["main_fut"]["status_reason"]
    r11 = rows["2026-09-11"]
    assert r11["revised"] and "RESTORE" in r11["revision_reason"] and "(RESTORE)" in r11["label"]
    assert r11["total"]["restored_away"]["n"] == 2 and r11["total"]["n_trades"] == 0
    # kanıt yokken kaybolan kayıt INCONSISTENT'tir
    v2 = FakeVps(tmp_path / "b")
    led2 = v2.fut()
    for i in range(4):
        trade(led2, "ETH/USDT", at("2026-09-10", 3 + i), 100.0, 101.0)
    v2.night(night_of("2026-09-11"))
    led2.history.pop(2)
    v2.save(night_of("2026-09-12"))
    r = v2.s1a(night_of("2026-09-12"))
    assert r["books"]["main_fut"]["vanished"] == 1 and C.F_INCONSISTENT in r["books"]["main_fut"]["flags"]
    # ledger sıfırlandı (dolu değil, en eski kayıt yeni): eski kayıtların yokluğunu rotasyon açıklamaz
    led2.history.clear()
    trade(led2, "SOL/USDT", at("2026-09-12", 5), 100.0, 101.0)
    v2.save(night_of("2026-09-13"))
    r = v2.s1a(night_of("2026-09-13"))
    # 3 elde tutulan + dün kaybolan 1 (kaybolan kayıt arşivde AKTİF kalır ve açıklanana kadar her gece yeniden sayılır)
    assert r["books"]["main_fut"]["vanished"] == 4 and C.F_INCONSISTENT in r["books"]["main_fut"]["flags"]
    assert C.F_HISTORY_GAP not in r["books"]["main_fut"]["flags"]


# ============================================================================ kabul 16: rotasyon payı
def test_rotation_margin_and_three_day_warning(tmp_path):
    ref = at("2026-09-10", 1, 40)
    fast = [ref - timedelta(minutes=10 * i) for i in range(2000)]       # 144/gün değil: dolu liste, 2000 / 13,9 gün
    m = C.rotation_margin(fast, keep=2000, new_since_last=0, ref=ref)
    assert m["full"] and m["rate_per_day"] == pytest.approx(2000 / (19990 / 1440), rel=1e-3) and not m["warn"]
    busy = [ref - timedelta(minutes=2 * i) for i in range(2000)]        # 720/gün → 2000/720 = 2,8 gün < 3
    m = C.rotation_margin(busy, keep=2000, new_since_last=0, ref=ref)
    assert m["days"] < 3 and m["warn"] is True
    m = C.rotation_margin(busy[:300], keep=5000, new_since_last=300, ref=ref)
    assert m["remaining_items"] == 4700 and not m["full"]
    assert C.rotation_margin([], keep=5000, new_since_last=0, ref=ref)["days"] is None
    # S1a yolunda bayrak
    v = FakeVps(tmp_path)
    led = v.fut(equity="100000")
    t0 = ref - timedelta(hours=20)
    for i in range(700):
        trade(led, "ETH/USDT", t0 + timedelta(seconds=90 * i), 100.0, 100.3, hold=timedelta(seconds=20))
    v.save(ref)
    rs = C.rotation_status(v.paths, now=ref)
    assert rs["warn"] is True and rs["min_days"] < 3, "salt-okunur pay (A/B kararı ve --check için)"
    r1, _ = v.night(ref)
    ent = r1["books"]["main_fut"]["rotation"]["entries"]
    assert ent["warn"] is True and C.F_ROTATION_WARN in r1["books"]["main_fut"]["flags"]


# ============================================================================ şema, okuma kipi, yalıtım
def test_unknown_schema_is_skipped_fail_closed_and_others_continue(tmp_path):
    v = FakeVps(tmp_path)
    trade(v.fut(), "ETH/USDT", at("2026-09-01", 3), 100.0, 101.0)
    trade(v.fut("strategy_paper_m2"), "BTC/USDT", at("2026-09-01", 3), 100.0, 101.0)
    v.save(night_of("2026-09-02"))
    d = json.loads(v.fut_path("strategy_paper_m2").read_text(encoding="utf-8"))
    d["schema_version"] = 3
    v.fut_path("strategy_paper_m2").write_text(json.dumps(d), encoding="utf-8")
    r1 = v.s1a(night_of("2026-09-02"))
    assert r1["books"]["strategy_paper_m2"]["status"] == L.LEDGER_SCHEMA_UNKNOWN
    assert r1["books"]["main_fut"]["new_closes"] == 1
    assert not v.paths.book_closes_dir("strategy_paper_m2").exists()
    snap = C.load_snapshot(v.paths, "2026-09-02")
    assert snap["books"]["strategy_paper_m2"]["status"] == L.LEDGER_SCHEMA_UNKNOWN
    v.night(night_of("2026-09-03"), save=False)
    row = v.rows()["2026-09-02"]
    assert row["status"] == "EKSİK" and "DEFTER_SCHEMA_UNKNOWN" in row["status_reason"]
    assert row["total"]["r_mtm"] is None, "okunamayan defter varken toplam yüzdesi kısmi olurdu: yazılmaz"
    assert row["books"]["main_fut"]["r_mtm"] is not None


def test_engine_reads_ledgers_in_r_mode_and_writes_only_under_research(tmp_path, monkeypatch):
    v = FakeVps(tmp_path)
    trade(v.fut(), "ETH/USDT", at("2026-09-01", 3), 100.0, 101.0)
    trade(v.fut("strategy_paper_box"), "ETH/USDT", at("2026-09-01", 3), 100.0, 99.0)
    v.spot().market_buy("ETH/USDT", qty=D("1"), ref_price=D("100"), now=at("2026-09-01", 4))
    v.save(night_of("2026-09-02"))
    before = {p: p.read_bytes() for p in v.data.rglob("*") if p.is_file()}
    modes: list[tuple[str, str]] = []
    real_open = open

    def spy(file, mode="r", *a, **k):
        modes.append((str(file), mode))
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(L, "open", spy, raising=False)
    v.s1a(night_of("2026-09-02"))
    v.s3(night_of("2026-09-02") + timedelta(minutes=1))
    ledger_opens = [m for f, m in modes if f.endswith(("futures_ledger.json", "spot_ledger.json"))]
    assert ledger_opens and set(ledger_opens) == {"r"}, ledger_opens
    after = {p: p.read_bytes() for p in v.data.rglob("*") if p.is_file()}
    research = (v.data / "research").resolve()
    for p, b in before.items():
        assert after[p] == b, f"state dosyası değişti: {p}"
    for p in after:
        if p not in before:
            assert research in p.resolve().parents, f"araştırma kökü dışına yazıldı: {p}"


def test_find_ledgers_covers_scorecard_find_books_plus_spot(tmp_path):
    spec = importlib.util.spec_from_file_location("bot_scorecard_re", ROOT / "scripts" / "bot_scorecard.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    import random
    rng = random.Random(7)
    names = list(S.BOOKS) + ["strategy_paper_m2x", "yeni_defter", "x"]
    for trial in range(25):
        st = tmp_path / f"s{trial}" / "state"
        st.mkdir(parents=True)
        for n in rng.sample(names, rng.randint(0, len(names))):
            p = st / n / "futures_ledger.json" if n else st / "futures_ledger.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        spot = rng.random() < 0.5
        if spot:
            (st / "spot_ledger.json").write_text("{}", encoding="utf-8")
        mine = L.find_ledgers(st)
        theirs = {("main_fut" if not k else k): p for k, p in S.find_books(st).items()}
        assert {k: p for k, (kind, p) in mine.items() if kind == "futures"} == theirs
        assert ("main_spot" in mine) == spot


def test_paths_guard_lock_and_disk_guard(tmp_path):
    pth = P.EnginePaths.for_state(tmp_path / "data" / "state")
    with pytest.raises(P.OutsideResearchRoot):
        pth.write_text(tmp_path / "data" / "state" / "x.json", "{}")
    with pytest.raises(P.OutsideResearchRoot):
        pth.write_text(pth.research / ".." / "state" / "y.json", "{}")
    pth.write_text(pth.research / "summary" / "ok.json", "{}")
    a, b = LK.analysis_lock(pth), LK.analysis_lock(pth)
    assert a.try_acquire() and not b.try_acquire(), "ikinci süreç beklemez: SKIPPED_LOCKED"
    d = LK.data_lock(pth)
    assert d.try_acquire(), "data.lock ve analysis.lock ayrı kilitlerdir"
    a.release()
    d.release()
    assert b.try_acquire()
    b.release()
    assert LK.SKIPPED_LOCKED == "SKIPPED_LOCKED"
    g = P.disk_guard(pth, research_bytes=16 * P.GB, free_bytes=50 * P.GB)
    assert g["status"] == P.DISK_WARN
    assert P.disk_guard(pth, research_bytes=20 * P.GB, free_bytes=50 * P.GB)["status"] == P.DISK_REFUSE
    assert P.disk_guard(pth, research_bytes=1, free_bytes=9 * P.GB)["status"] == P.DISK_REFUSE
    assert P.disk_guard(pth, research_bytes=1, free_bytes=11 * P.GB)["status"] == P.DISK_OK


_FORBIDDEN_IMPORTS = ("config_v3", "sqlite3", "requests", "socket", "urllib", "http", "ccxt", "aiohttp", "httpx")


def test_research_engine_imports_no_config_loader_no_sqlite_no_network():
    pkg = ROOT / "tradingbot" / "research_engine"
    # Tek istisna: S0'ın soket RET denemesi (§2.8 madde 2) `socket`'i yalnız `selfcheck.probe_socket_denied` içinde
    # import eder; ayrıntılı beyaz liste testi tests/test_research_engine_contract.py'dedir.
    sc = ast.parse((pkg / "selfcheck.py").read_text(encoding="utf-8"))
    probe = next(n for n in sc.body if isinstance(n, ast.FunctionDef) and n.name == "probe_socket_denied")
    for f in sorted(pkg.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (f.name == "selfcheck.py" and isinstance(node, ast.Import) and [a.name for a in node.names] == ["socket"]
                    and probe.lineno <= node.lineno <= probe.end_lineno):
                continue
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
                assert not any(a.name == "load_config" for a in node.names), f"{f.name}: load_config"
            for n in names:
                parts = set(n.split("."))
                assert not parts & set(_FORBIDDEN_IMPORTS), f"{f.name}: yasaklı import {n}"


def test_segments_carry_sha256_sidecars_that_match(tmp_path):
    v = FakeVps(tmp_path)
    trade(v.fut(), "ETH/USDT", at("2026-09-01", 3), 100.0, 101.0)
    v.night(night_of("2026-09-02"))
    segs = C.segment_files(v.paths.book_closes_dir("main_fut")) + C.segment_files(v.paths.book_entries_dir("main_fut"))
    assert segs
    for s in segs:
        assert s.with_name(s.name + ".sha256").read_text().strip() == P.sha256_file(s)
    shutil.rmtree(v.data / "research")
    v.night(night_of("2026-09-03"))          # kök silinse bile yeniden kurulur (yeni taban)
    assert C.segment_files(v.paths.book_closes_dir("main_fut"))

# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2a — UTC günü MTM (`tgt_v2_utc_day`; docs/SYSTEM_LEARNING_ENGINE_V1.md §7.1, §10 P2 kabul 10).

Worker'ın kendi muhasebe sınıflarıyla dakika dakika ilerleyen bir benzetim: vadeli işlemler (biri gece yarısını ve gece
çalıştırmasını aşar, biri gece yarısından önce kısmi kapanır, biri gece yarısı ile gece çalıştırması arasında kısmi
kapanır) + spot alış/kısmi satış, her gece 01:40'ta P1a gecesi (S1a + S3) ve UTC günü birimi. Her 00:00'da defterin
GERÇEK cüzdanı ve açık pozisyonları kaydedilir; motorun arşivlenmiş hareketlerden kurduğu 00:00 cüzdanı ve depo
kapanışıyla `LAST_PRICE_PROXY` gerçekleşmemiş kârı bunlarla karşılaştırılır. Ağ yok.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import night_of  # noqa: E402
from research_engine_p2_fixtures import (M1, SettlementRates, World, close_at, ms, new_ledger, price_at,  # noqa: E402
                                         utc, walk_ledger, world_markets)

from tradingbot.accounting import AmountType, SizeSpec  # noqa: E402
from tradingbot.research_engine import daily_target as T  # noqa: E402
from tradingbot.research_engine import utc_day as U  # noqa: E402
from tradingbot.research_engine.closes import load_snapshots  # noqa: E402
from tradingbot.research_engine.ledgers import iso  # noqa: E402

TOL = D("1e-6")
SYM = "ETH/USDT"
#: UTC_SCHEMA tanımının mühürü (§7.1 "geçişte tanım mühürlenir"); tanım değişirse bu test bilerek kırılır
TGT_V2_SHA_PINNED = "f9e73cafe7ac12e977f3e666c0dcac134c2623f5e15b7ebe26c84d64787ac659"
NIGHTS = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]


class Driver:
    """Tek sembollü (ETH) vadeli defter + spot defter; zaman yalnız ileri akar (`advance`)."""

    def __init__(self, w: World):
        self.w = w
        self.eth = w.m1(SYM)
        self.led = new_ledger("10000")
        w.v.futs[""] = self.led
        self.sp = w.v.spot(cash="20000")
        self.fund = SettlementRates("0.0001", {SYM: self.eth})
        self.cur: int | None = None
        self.truth: dict[str, dict] = {}
        self.utc_runs: dict[str, dict] = {}

    def advance(self, to_ms: int) -> None:
        if self.led.positions.get(SYM) is not None and self.cur is not None and to_ms - M1 > self.cur:
            walk_ledger(self.led, SYM, self.eth, opened_ms=self.cur, until_ms=to_ms - M1, funding=self.fund)
        self.cur = to_ms - M1

    def open(self, t: datetime, side: str) -> None:
        self.advance(ms(t) // M1 * M1)
        e = price_at(self.eth, ms(t))
        stop = round(e * (0.95 if side == "LONG" else 1.05), 2)
        pos = self.led.open(SYM, side, D(str(e)), SizeSpec(D("600"), AmountType.NOTIONAL, 3), stop=stop, now=t)
        assert pos is not None, self.led.last_reject_reason
        self.cur = ms(t)

    def partial(self, t: datetime, frac: str = "0.5") -> None:
        self.advance(ms(t))
        px = float(self.eth[self.eth["timestamp"] == ms(t) - M1]["close"].iloc[0])
        assert self.led.close_partial(SYM, D(str(px)), D(frac), now=t) is None

    def close(self, t: datetime) -> None:
        self.advance(ms(t))
        assert close_at(self.led, SYM, self.eth, ms(t)) is not None

    def midnight(self, day: str) -> None:
        t = utc(*(int(x) for x in day.split("-")))
        self.advance(ms(t))
        lots = sum((lot.qty * lot.cost_basis for ls in self.sp.lots.values() for lot in ls), D(0))
        self.truth[day] = {"wallet": self.led.wallet_balance,
                           "positions": [(p.side.value, p.qty, p.entry_avg, p.id) for p in self.led.positions.values()],
                           "spot_b": self.sp.cash + self.sp.locked_cash + lots,
                           "spot_qty": sum((lot.qty for ls in self.sp.lots.values() for lot in ls), D(0))}

    def night(self, day: str) -> None:
        n = night_of(day)
        self.advance(ms(n))
        self.w.v.night(n)
        self.utc_runs[day] = U.run_utc_day(self.w.paths, now=n + timedelta(minutes=3), run_id=f"n{day}", src=self.w.source())


def _px(eth, t: datetime) -> D:
    return D(str(float(eth[eth["timestamp"] == ms(t) - M1]["close"].iloc[0])))


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    w = World(tmp_path_factory.mktemp("utc_world"))
    w.seal(frames={"ETHUSDT": world_markets()["ETHUSDT"]}, spot=("ETHUSDT",))
    d = Driver(w)
    d.midnight("2026-09-26")
    d.open(utc(2026, 9, 26, 0, 30, 13), "LONG")                 # temel gözlemden ÖNCE kapanan işlem
    d.close(utc(2026, 9, 26, 1, 0))
    d.night("2026-09-26")
    d.open(utc(2026, 9, 26, 5, 0, 13), "LONG")                  # 08:00 fonlamasını geçer
    d.close(utc(2026, 9, 26, 9, 0))
    o = d.sp.market_buy(SYM, qty=D("0.02"), ref_price=D(str(price_at(d.eth, ms(utc(2026, 9, 26, 10, 0, 31))))),
                        now=utc(2026, 9, 26, 10, 0, 31))
    assert o.status.value == "FILLED"
    d.open(utc(2026, 9, 26, 20, 0, 13), "SHORT")                # gece yarısını ve gece çalıştırmasını aşar
    d.midnight("2026-09-27")
    d.night("2026-09-27")
    d.close(utc(2026, 9, 27, 6, 0))
    so = d.sp.market_sell(SYM, qty=D("0.005"), ref_price=D(str(price_at(d.eth, ms(utc(2026, 9, 27, 12, 0, 9))))),
                          now=utc(2026, 9, 27, 12, 0, 9))
    assert so.status.value == "FILLED"
    d.open(utc(2026, 9, 27, 21, 0, 13), "LONG")
    d.partial(utc(2026, 9, 27, 23, 30))                          # gece yarısından ÖNCE kısmi çıkış
    d.midnight("2026-09-28")
    d.close(utc(2026, 9, 28, 1, 0))
    d.night("2026-09-28")
    d.open(utc(2026, 9, 28, 22, 0, 13), "LONG")
    d.midnight("2026-09-29")
    d.partial(utc(2026, 9, 29, 1, 0))                            # 00:00 ile çapa (01:40) ARASINDA kısmi çıkış
    d.night("2026-09-29")
    d.close(utc(2026, 9, 29, 5, 0))
    d.midnight("2026-09-30")
    d.night("2026-09-30")
    d.midnight("2026-10-01")
    d.night("2026-10-01")
    rows = U.latest_utc_rows(w.paths)
    return d, rows


# ============================================================================ kabul 10
def test_midnight_wallet_from_archived_movements_equals_the_true_ledger_wallet_1e6(sim):
    d, rows = sim
    assert sorted(rows) == NIGHTS[:-1], "ilk anlık görüntü gününden son anlık görüntünün önceki gününe"
    n = 0
    for day, row in rows.items():
        nxt = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
        for side, t in (("start", day), ("end", nxt)):
            fut, spot = row["books"]["main_fut"][side], row["books"]["main_spot"][side]
            assert abs(D(fut["wallet"]) - d.truth[t]["wallet"]) <= TOL, (day, side)
            assert abs(D(spot["wallet"]) - d.truth[t]["spot_b"]) <= TOL, (day, side, "spot defter değeri B")
            n += 1
    assert n == 10
    assert d.truth["2026-09-26"]["wallet"] == D("10000") and d.truth["2026-09-27"]["wallet"] != D("10000")


def test_midnight_unrealized_is_the_store_close_last_price_proxy(sim):
    d, rows = sim
    eth = d.eth
    for day in ("2026-09-27", "2026-09-28", "2026-09-29"):
        t = utc(*(int(x) for x in day.split("-")))
        px = _px(eth, t)
        want = sum(((px - entry) * qty * (1 if side == "LONG" else -1) for side, qty, entry, _id in d.truth[day]["positions"]), D(0))
        assert d.truth[day]["positions"], day
        prev = (t - timedelta(days=1)).date().isoformat()
        st = rows[prev]["books"]["main_fut"]["end"]
        assert abs(D(st["unrealized"]) - want) <= TOL, day
        assert st["mark_source"] == U.MARK_LAST_PRICE_PROXY and all(m["tf"] == "1m" for m in st["marks"].values())
        assert {m["price"] for m in st["marks"].values()} == {str(px)}
        # spot: varlık × 00:00 kapanışı − lot maliyeti
        sp = rows[prev]["books"]["main_spot"]["end"]
        assert abs(D(sp["equity"]) - D(sp["wallet"]) - D(sp["unrealized"])) <= TOL
        assert sp["marks"]["ETHUSDT"]["price"] == str(px) and D(sp["marks"]["ETHUSDT"]["qty"]) == d.truth[day]["spot_qty"]
    # kısmi çıkıştan sonraki miktar: 09-28 00:00'da yarım pozisyon (arşiv kaydının dolumlarından)
    side, qty, _e, _id = d.truth["2026-09-28"]["positions"][0]
    m = next(iter(rows["2026-09-27"]["books"]["main_fut"]["end"]["marks"].values()))
    assert D(m["qty"]) == qty and m["src"] == "archive"


def test_anchor_and_entries_identities_hold_and_mtm_is_the_equity_difference(sim):
    _d, rows = sim
    for day, row in rows.items():
        for book in ("main_fut", "main_spot"):
            b = row["books"][book]
            for side in ("start", "end"):
                ac = b[side]["anchor_check"]
                assert ac["ok"] in (True, None) and (ac["ok"] is True or ac["prev_anchor"] is None), (day, book, side)
            assert b["entries_check"]["ok"] is True, (day, book)
            assert D(b["pnl_mtm_utc"]) == D(b["e_end"]) - D(b["e_start"]) - D(b["other"]) - (D(b["transfer"]) if book == "main_fut" else 0)
        tot = row["total"]
        assert D(tot["pnl_mtm_utc"]) == sum((D(row["books"][b]["pnl_mtm_utc"]) for b in ("main_fut", "main_spot")), D(0))
        assert tot["mark_source"] == U.MARK_LAST_PRICE_PROXY and row["tgt_sha"] == U.TGT_V2_SHA
    first = rows["2026-09-26"]["books"]["main_fut"]["start"]
    assert first["anchor_check"]["prev_anchor"] is None, "temel gözlemden önceki 00:00 yalnız sonraki çapadan"


def test_partial_exit_between_midnight_and_anchor_is_honest_eksik_then_revised_from_the_archive(sim):
    d, _rows = sim
    from tradingbot.research_engine.paths import read_jsonl
    hist = [r for r in read_jsonl(d.w.paths.target_daily_utc) if r["day"] == "2026-09-28"]
    assert [r["rev"] for r in hist][:2] == [0, 1]
    r0 = hist[0]
    assert r0["status"] == "EKSİK" and any(x.startswith("main_fut:U_QTY_UNKNOWN") for x in r0["total"]["status_reason"])
    assert r0["books"]["main_fut"]["pnl_mtm_utc"] is None, "00:00 miktarı bilinmiyor: tahmin yok"
    last = hist[-1]
    assert not any("U_QTY_UNKNOWN" in x for x in last["total"]["status_reason"])
    assert last["books"]["main_fut"]["pnl_mtm_utc"] is not None


def test_utc_rows_stand_beside_the_w_day_ledger_mark_rows_which_stay_unchanged(sim):
    d, rows = sim
    w_rows = T.latest_rows(d.w.paths)
    for day, row in rows.items():
        w = w_rows[day]
        v = row["w_day"]
        assert v["mark_source"] == "LEDGER_MARK" and v["window"] == w["window"] and v["rev"] == w.get("rev")
        assert v["r_mtm"] == (w["total"] or {}).get("r_mtm") and row["side_by_side"]["w_r_mtm"] == v["r_mtm"]
        assert row["side_by_side"]["utc_r_mtm"] == row["total"]["r_mtm_utc"]
        assert row["window"] == {"from": iso(utc(*(int(x) for x in day.split("-")))),
                                 "to": iso(utc(*(int(x) for x in day.split("-"))) + timedelta(days=1)), "kind": "UTC"}
    # UTC birimi W-günü dosyasına dokunmaz; yeniden çalıştırma yalnız değişeni ekler
    before_w, before_u = d.w.paths.target_daily.read_bytes(), d.w.paths.target_daily_utc.read_bytes()
    out = U.run_utc_day(d.w.paths, now=night_of("2026-10-01") + timedelta(minutes=9), src=d.w.source())
    assert out["appended"] == 0 and d.w.paths.target_daily.read_bytes() == before_w
    assert d.w.paths.target_daily_utc.read_bytes() == before_u


def test_status_follows_tgt_v1_finality_rules(sim):
    _d, rows = sim
    st = {day: r["status"] for day, r in rows.items()}
    assert st["2026-09-26"] == "KESİN" and st["2026-09-27"] == "KESİN", "≥ 3 gün + fonlama kapsaması tam"
    assert st["2026-09-30"] == "GEÇİCİ" and "3_GÜN_DOLMADI" in " ".join(rows["2026-09-30"]["total"]["status_reason"])


def test_tgt_v2_headline_switches_only_after_14_side_by_side_days(sim):
    d, rows = sim
    s = U.tgt_v2_status(d.w.paths, today="2026-10-01")
    assert s["headline"] == "tgt_v1" and s["active"] is False and s["side_by_side_days"] == len(rows) == 5
    assert s["activates_on"] == "2026-10-10" and s["tgt_v2_sha"] == TGT_V2_SHA_PINNED
    fake = {(datetime(2026, 9, 1) + timedelta(days=k)).date().isoformat(): {"w_day": {"r_mtm": 0.1}} for k in range(14)}
    assert U.tgt_v2_status(d.w.paths, today="2026-09-14", rows=fake)["active"] is False, "bugünün satırı sayılmaz"
    s = U.tgt_v2_status(d.w.paths, today="2026-09-15", rows=fake)
    assert s["headline"] == U.TGT_V2 and s["side_by_side_days"] == 14 and s["first_side_by_side_day"] == "2026-09-01"
    fake["2026-09-05"]["w_day"] = None
    assert U.tgt_v2_status(d.w.paths, today="2026-09-15", rows=fake)["active"] is False, "yalnız W-günüyle YAN YANA günler"
    assert U.TGT_V2_SHA == TGT_V2_SHA_PINNED, "tgt_v2 tanımı mühürlü"


def test_midnight_before_an_archive_whose_movements_already_rotated_is_eksik(sim, monkeypatch):
    d, _rows = sim
    rows = {r["day"]: r for r in U.build_utc_rows(d.w.paths, now=night_of("2026-10-01"), src=d.w.source())}
    assert "HAREKETLER_ARŞİVDEN_ÖNCE_DÖNDÜ" not in rows["2026-09-26"]["books"]["main_fut"]["status_reason"]
    monkeypatch.setattr(U, "ENTRIES_KEEP", 3)                    # temel gözlemde liste "dolu": eskiler düşmüş olabilir
    rows = {r["day"]: r for r in U.build_utc_rows(d.w.paths, now=night_of("2026-10-01"), src=d.w.source())}
    b = rows["2026-09-26"]["books"]["main_fut"]
    assert b["status"] == "EKSİK" and "HAREKETLER_ARŞİVDEN_ÖNCE_DÖNDÜ" in b["status_reason"]
    assert rows["2026-09-27"]["books"]["main_fut"]["status"] != "EKSİK" or \
        "HAREKETLER_ARŞİVDEN_ÖNCE_DÖNDÜ" not in rows["2026-09-27"]["books"]["main_fut"]["status_reason"]


def test_open_position_without_a_store_close_is_eksik_not_guessed(sim):
    d, _rows = sim
    tl = U.load_timeline(d.w.paths, "main_fut", load_snapshots(d.w.paths, light=True))
    s = U.state_at(tl, utc(2026, 9, 27), None)
    assert s["ok"] is False and any(r.startswith("MARK_EKSİK") for r in s["reasons"]) and s["equity"] is None
    assert s["wallet"] is not None, "cüzdan yine ölçülür; yalnız gerçekleşmemiş kısım eksik"

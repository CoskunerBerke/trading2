# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — TOPLAYICI + BAR KAYNAĞI (2026-09-29; SPEC_V1 §6-§8, görev X3).

Motor yerine GERÇEK defter (`FuturesLedgerV2`), GERÇEK karşı-olgusal kayıtçı (`CounterfactualRecorder`) ve GERÇEK gölge
defteri (`ShadowBook`) taşıyan ince bir sahte motor kullanılır; toplayıcının okuduğu her şey üretimdekiyle aynı tiptedir.

Kapsam: giriş/sonuç (iki adım arasında açılıp kapanan dahil), geç funding revizyonu, karşı-olgusal rev 0 → etiket → kaybolma,
erteleme (PENDING → OK, geç anlık görüntü = tam çerçeveden anlık görüntü), yaş sınırında GAP, kilit meşgul (kayıpsız),
bütçe/satır tavanı taşıması, depo G/Ç hatası (imleç ilerlemez, tekrar yok), devre kesici, PAPER dışı ASKI, DEGRADED,
geri doldurma kökenleri, imleç kalıcılığı/bozulması, bellek sınırları, bar kaynağı sırası (provenans kapısı, CSV, tembel
çekim varsayılan 0), durum/sağlık dosyaları.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec  # noqa: E402
from tradingbot.config_v3 import SharedExperienceSection  # noqa: E402
from tradingbot.core import iso, stable_id  # noqa: E402
from tradingbot.learn.shadow import ShadowBook, ShadowTrade  # noqa: E402
from tradingbot.learning_cf import CounterfactualRecorder  # noqa: E402
from tradingbot.shared_experience import collector as C  # noqa: E402
from tradingbot.shared_experience import rows as R  # noqa: E402
from tradingbot.shared_experience import situation as S  # noqa: E402
from tradingbot.shared_experience.bars import BarSource  # noqa: E402

UTC = timezone.utc
H4, H1 = 14_400_000, 3_600_000
#: Sabit an: 2026-09-29 10:05 UTC (4h barı 08:00'de açıldı → as_of anında kapanmış son 4h barı 04:00).
T0 = datetime(2026, 9, 29, 10, 5, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)
SYMS = ("SOL/USDT", "ETH/USDT", "AVAX/USDT")
BTC = "BTC/USDT"


# ============================================================================ sentetik dünya (zamana bağlı, deterministik)
class World:
    """Bar değerleri ZAMAN DAMGASININ fonksiyonudur: hangi anda alınırsa alınsın aynı kapanmış bar aynı değeri taşır.
    Alınma anında oluşan (kapanmamış) son bar BOZULUR — yanlışlıkla kullanılırsa anlık görüntü değişir."""

    def __init__(self, start_ms: int = T0_MS - 400 * H4, n4: int = 460, n1: int = 1900) -> None:
        self._s: dict[tuple[str, str], pd.DataFrame] = {}
        self.start = (start_ms // H4) * H4
        self.n = {"4h": n4, "1h": n1}

    def series(self, sym: str, tf: str) -> pd.DataFrame:
        key = (sym, tf)
        if key not in self._s:
            step = H4 if tf == "4h" else H1
            n = self.n[tf]
            rng = np.random.default_rng(sum(map(ord, sym + tf)))
            ts = self.start + step * np.arange(n, dtype=np.int64)
            # borsa biçimi ondalıklar (CSV gidiş-dönüşü BİREBİR; rastgele ikili kayan nokta değil)
            c = np.round(100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n))), 4)
            o = np.r_[c[0], c[:-1]]
            h = np.round(np.maximum(o, c) * (1 + rng.uniform(0, 0.004, n)), 4)
            lo = np.round(np.minimum(o, c) * (1 - rng.uniform(0, 0.004, n)), 4)
            v = np.round(rng.uniform(100, 1000, n), 3)
            self._s[key] = pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v})
        return self._s[key]

    def frame(self, sym: str, tf: str, fetch_ms: int) -> pd.DataFrame:
        step = H4 if tf == "4h" else H1
        df = self.series(sym, tf)
        out = df[df["timestamp"] <= (fetch_ms // step) * step].copy().reset_index(drop=True)
        last = len(out) - 1
        if last >= 0 and int(out.loc[last, "timestamp"]) + step > fetch_ms:       # oluşan bar: kısmi değerler
            out.loc[last, "close"] = out.loc[last, "close"] * 1.07
            out.loc[last, "high"] = max(out.loc[last, "high"], out.loc[last, "close"])
            out.loc[last, "volume"] = out.loc[last, "volume"] * 0.1
        return out

    def frames(self, fetch_ms: int, syms=SYMS + (BTC,)) -> dict:
        return {s: {"4h": self.frame(s, "4h", fetch_ms), "1h": self.frame(s, "1h", fetch_ms)} for s in syms}


PERP = {"market": "USDM_PERP", "source": "test", "entry_ok": True}


# ============================================================================ ince sahte motor (gerçek defter / kayıtçı)
class FakeBook:
    """`StrategyBook` salt-okur yüzeyi: key/name/lock/ledger/cf + `_decision_tf()` (gerçek defter ve kayıtçı)."""

    def __init__(self, tmp: Path, key: str, *, decision_tf: str = "4h", cf: bool = True) -> None:
        self.key, self.name = key, R.BOOKS[key][0]
        self.lock = threading.RLock()
        self.ledger = FuturesLedgerV2(1000, max_positions=None)
        self.cf = CounterfactualRecorder(tmp / key / "counterfactual_trades.json", book=self.name) if cf else None
        self._dtf = decision_tf

    def _decision_tf(self) -> str:
        return self._dtf


class FakePattern(FakeBook):
    def __init__(self, tmp: Path) -> None:
        super().__init__(tmp, "pattern_trader")


def fake_engine(tmp: Path, *, world: World | None = None, fetch_ms: int = T0_MS - 60_000, books=(), pattern=None,
                mode: str = "PAPER", **xp) -> SimpleNamespace:
    world = world or World()
    st = tmp / "state"
    st.mkdir(parents=True, exist_ok=True)
    sec = SharedExperienceSection(enabled=True, mode="RECORD", **xp)
    lk = threading.RLock()
    eng = SimpleNamespace(
        cfg=SimpleNamespace(state_path=st, cache_path=tmp / "data", v3=SimpleNamespace(shared_experience=sec)),
        mode_state=SimpleNamespace(mode=SimpleNamespace(value=mode), is_live_order_path_enabled=lambda: False),
        ledger2=FuturesLedgerV2(1000, max_positions=None), _exit_lock=lk, _ledger_lock=lk,
        shadow=ShadowBook(st / "shadow_book.json"), strategy_books=list(books), pattern_book=pattern,
        runner=SimpleNamespace(last_frames={}), _frame_provenance={}, _tour_now_ms=fetch_ms, _seen_signals=[])
    eng._signal_id = lambda sym, market, d, plan, b: stable_id("signal", sym, market, "4h", b.last_bar_4h, d.direction,
                                                               plan.entry_type or "-")
    eng.world = world
    set_tour(eng, fetch_ms)
    return eng


def set_tour(eng, fetch_ms: int, syms=SYMS + (BTC,)) -> None:
    """Yeni tur: çerçeveler `fetch_ms` anında alınmış (oluşan son bar kısmi), provenans USDⓈ-M perp."""
    eng._tour_now_ms = int(fetch_ms)
    eng.runner.last_frames = eng.world.frames(fetch_ms, syms)
    eng._frame_provenance = {s: dict(PERP) for s in syms}


def collector(eng) -> C.SharedExperienceCollector:
    return C.SharedExperienceCollector.from_engine(eng)


def step(xp, eng, now: datetime, **kw):
    return xp.step(eng, risk_log=kw.get("risk_log", []), decisions=kw.get("decisions", {}), briefs=kw.get("briefs", []),
                   now=now)


def rows_of(eng) -> list[dict]:
    p = eng.cfg.state_path / "shared_experience" / "experience.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def status_of(eng) -> dict:
    return json.loads((eng.cfg.state_path / "shared_experience" / "status.json").read_text(encoding="utf-8"))


def open_pos(ledger, sym: str, *, side: str = "LONG", px: str = "100", stop: str = "95", at: datetime = T0, **kw):
    sgn = 1 if side == "LONG" else -1
    tg = [D(px) * (1 + D("0.1") * sgn)]
    pos = ledger.open(sym, side, D(px), SizeSpec(D("50"), AmountType.NOTIONAL, 2), stop=D(stop), targets=tg, now=at,
                      setup_type=kw.pop("setup_type", "trend"), **kw)
    assert pos is not None, ledger.last_reject_reason
    return pos


def record_cf(book: FakeBook, sym: str, *, sig: int, created: datetime, side: str = "LONG", reason: str = "POSITION_OPEN",
              variation: str | None = None, px: float = 100.0) -> ShadowTrade:
    stop = px * (0.95 if side == "LONG" else 1.05)
    ok = book.cf.record(signal_key="signal_ts:%d" % sig, symbol=sym, direction=side, entry=px, stop=stop,
                        targets=[px * (1.1 if side == "LONG" else 0.9)], reason=reason, created_at=created, tf_minutes=240,
                        horizon_bars=6, label_kind="TARGET_STOP_TIME", features={"setup_type": "trend", "strategy": "x"},
                        variation=variation)
    assert ok
    return book.cf.sb.trades[-1]


def by(rows, **kw) -> list[dict]:
    return [r for r in rows if all(r.get(k) == v for k, v in kw.items())]


# ============================================================================ gerçek işlem: giriş + sonuç
def test_open_then_close_writes_one_entry_then_one_final_outcome(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)                                                      # doğum: boş defter
    pos = open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1),
                   features={"learning": {"size_rule": "SLOT", "learning_unlocked_by": []}})
    set_tour(eng, T0_MS + 60_000)
    step(xp, eng, T0 + timedelta(minutes=2))
    rows = rows_of(eng)
    assert [r["kind"] for r in rows] == ["xp_entry"]
    e = rows[0]
    assert e["trade_id"] == pos.id and e["origin"] == "LIVE" and e["snapshot_status"] == "OK"
    assert e["book"] == "main" and e["cohort"] == "POLICY" and e["initial_stop"] == 95.0
    assert e["snapshot"]["as_of_ms"] == int((T0 + timedelta(minutes=1)).timestamp() * 1000)
    assert e["snapshot_meta"]["source"] == {"h4": "tour_frames", "h1": "tour_frames", "btc": "tour_frames"}
    rec = eng.ledger2.close_manual("SOL/USDT", D("104"), "hedef", T0 + timedelta(hours=3))
    step(xp, eng, T0 + timedelta(hours=3, minutes=1))
    step(xp, eng, T0 + timedelta(hours=3, minutes=2))                        # tekrar yok
    rows = rows_of(eng)
    outs = by(rows, kind="xp_outcome")
    assert len(outs) == 1 and len(by(rows, kind="xp_entry")) == 1
    o = outs[0]
    assert o["trade_key"] == e["trade_key"] and o["rev"] == 0 and o["final"] is True
    assert o["r_net"] == pytest.approx(float(rec.r_multiple), abs=1e-6)
    assert o["r_gross"] == pytest.approx(o["r_net"] + o["cost_r"], abs=1e-5)


def test_trade_opened_and_closed_between_steps_gets_entry_from_the_trade_record(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(eng.ledger2, "ETH/USDT", side="SHORT", px="200", stop="210", at=T0 + timedelta(minutes=1))
    eng.ledger2.close_manual("ETH/USDT", D("205"), "stop", T0 + timedelta(minutes=3))
    set_tour(eng, T0_MS + 5 * 60_000)
    step(xp, eng, T0 + timedelta(minutes=6))
    rows = rows_of(eng)
    ent, out = by(rows, kind="xp_entry"), by(rows, kind="xp_outcome")
    assert len(ent) == 1 and len(out) == 1 and ent[0]["trade_key"] == out[0]["trade_key"]
    # TradeRecord stop taşımaz → giriş ∓ risk_usdt / miktar (defterin kendi tanımı)
    assert ent[0]["initial_stop_src"] == "RISK_USDT" and ent[0]["initial_stop"] == pytest.approx(210.0, rel=1e-6)
    assert ent[0]["origin"] == out[0]["origin"] == "LIVE" and ent[0]["snapshot_status"] == "OK"


def test_late_funding_change_writes_a_new_revision_and_the_latest_is_final(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1))
    rec = eng.ledger2.close_manual("SOL/USDT", D("103"), "hedef", T0 + timedelta(hours=1))
    rec.features["funding_coverage"] = dict(rec.features["funding_coverage"], complete=False)   # bekleyen funding
    step(xp, eng, T0 + timedelta(hours=1, minutes=1))
    o = by(rows_of(eng), kind="xp_outcome")
    assert [(r["rev"], r["final"]) for r in o] == [(0, False)]
    step(xp, eng, T0 + timedelta(hours=1, minutes=2))
    assert len(by(rows_of(eng), kind="xp_outcome")) == 1, "değişiklik yok → revizyon yok"
    # geç uzlaştırma (defterin settle_late_funding'i gibi): funding ve kapsama değişti
    rec.funding, rec.r_multiple = D("-0.05"), rec.r_multiple - D("0.02")
    rec.features["funding_coverage"] = dict(rec.features["funding_coverage"], complete=True)
    step(xp, eng, T0 + timedelta(hours=2))
    o = by(rows_of(eng), kind="xp_outcome")
    assert [(r["rev"], r["final"]) for r in o] == [(0, False), (1, True)]
    assert R.latest_rows(rows_of(eng))[("xp_outcome", o[0]["trade_key"])]["rev"] == 1
    assert xp._books["main"].unfinal == {}


# ============================================================================ karşı-olgusal
def test_counterfactual_rev0_carries_snapshot_label_is_rev1_and_legacy_gross_is_flagged(tmp_path):
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box])
    xp = collector(eng)
    step(xp, eng, T0)
    t = record_cf(box, "SOL/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    set_tour(eng, T0_MS + 60_000)
    step(xp, eng, T0 + timedelta(minutes=2))
    cf = by(rows_of(eng), kind="xp_cf")
    assert [(r["rev"], r["status"], r["snapshot_status"]) for r in cf] == [(0, "PENDING", "OK")]
    assert cf[0]["cf_id"] == t.id == R.cf_id_for("b1_box_fade", "signal_ts:%d" % T0_MS, "SOL/USDT", "LONG")
    # eski (sürümsüz, brüt) etiket: r_net null, GROSS_LEGACY — net istatistiğe girmez, sayılır
    t.outcome = {"r_multiple": 1.2, "exit_reason": "target", "bars": 5, "mfe_pct": 3.0, "mae_pct": -1.0}
    step(xp, eng, T0 + timedelta(minutes=3))
    cf = by(rows_of(eng), kind="xp_cf")
    assert [(r["rev"], r["status"]) for r in cf] == [(0, "PENDING"), (1, "LABELLED")]
    assert cf[1]["r_basis"] == "GROSS_LEGACY" and cf[1]["r_net"] is None and "snapshot" not in cf[1]
    # yerinde net yeniden etiketleme (v1 → v3): yeni revizyon, NET
    t.outcome.update(label_version="cf_label_v3", r_gross=1.2, r_net=0.9, cost_r=0.3)
    step(xp, eng, T0 + timedelta(minutes=4))
    cf = by(rows_of(eng), kind="xp_cf")
    assert cf[-1]["rev"] == 2 and cf[-1]["r_basis"] == "NET" and cf[-1]["r_net"] == 0.9
    st = status_of(eng)["counters"]
    assert st["legacy_gross_cf"] == 1 and st["rows_by_kind"]["xp_cf"] == 3


def test_vanished_counterfactuals_are_classified_superseded_expired_dropped_and_labelled_trim_is_silent(tmp_path):
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box])
    xp = collector(eng)
    step(xp, eng, T0)
    a = record_cf(box, "SOL/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    b = record_cf(box, "ETH/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    c = record_cf(box, "AVAX/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    d = record_cf(box, "SOL/USDT", sig=T0_MS + 300_000, created=T0 + timedelta(minutes=1), side="SHORT")
    set_tour(eng, T0_MS + 60_000)
    step(xp, eng, T0 + timedelta(minutes=2))
    d.outcome = {"r_multiple": 0.5, "label_version": "cf_label_v3", "r_gross": 0.5, "r_net": 0.3, "cost_r": 0.2,
                 "exit_reason": "target"}
    step(xp, eng, T0 + timedelta(minutes=3))
    assert len(by(rows_of(eng), kind="xp_cf")) == 5
    # a: aynı sinyal GERÇEK işlem oldu (defterin supersede'i + açık pozisyon aynı birleştirme anahtarıyla)
    open_pos(box.ledger, "SOL/USDT", at=T0 + timedelta(minutes=4), meta={"signal": {"signal_ts": T0_MS}})
    assert box.cf.supersede(signal_key="signal_ts:%d" % T0_MS, symbol="SOL/USDT", direction="LONG") == 1
    # b: bekleyen tavanı düşürdü (ufuk dolmadı) → DROPPED; c: ufuk + tampon geçti → EXPIRED; d: etiketli arşiv budaması
    box.cf.sb.trades = [t for t in box.cf.sb.trades if t.id not in (b.id,)]
    step(xp, eng, T0 + timedelta(minutes=5))
    box.cf.sb.trades = [t for t in box.cf.sb.trades if t.id not in (c.id, d.id)]
    step(xp, eng, T0 + timedelta(days=4))
    rows = rows_of(eng)
    last = {r["cf_id"]: r for r in by(rows, kind="xp_cf")}
    assert last[a.id]["status"] == "SUPERSEDED" and last[a.id]["rev"] == 1 and last[a.id]["final"] is True
    assert last[b.id]["status"] == "DROPPED" and last[c.id]["status"] == "EXPIRED"
    assert last[d.id]["status"] == "LABELLED", "etiketli kaydın budanması revizyon DEĞİL"
    ent = by(rows, kind="xp_entry", book="strategy_paper_box")[0]
    assert ent["join_key"] == last[a.id]["join_key"] and ent["signal_key_src"] == "META_SIGNAL_TS"
    assert xp._books["strategy_paper_box"].cf_known == {}, "kaybolanlar imleçten budanır (bellek sınırı)"
    assert status_of(eng)["counters"]["vanished_by_status"] == {"SUPERSEDED": 1, "DROPPED": 1, "EXPIRED": 1}


def test_main_counterfactual_only_learning_rows_legacy_and_spot_are_not_recorded(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)
    eng.shadow.add({"plan_id": "p1", "symbol": "SOL/USDT", "market_type": "USDM_PERP", "direction": "LONG", "entry": 100,
                    "stop": 95, "targets": [110]}, ["CHIEF:x"], now=T0 + timedelta(minutes=1))          # eski gölge (book None)
    for sym, mkt in (("ETH/USDT", "USDM_PERP"), ("AVAX/USDT", "SPOT")):
        sh = eng.shadow.add({"plan_id": "sig_" + sym, "symbol": sym, "market_type": mkt, "direction": "LONG", "entry": 100,
                             "stop": 95, "targets": [110]}, ["RISK_CAPACITY_BLOCKED"], now=T0 + timedelta(minutes=1))[0]
        sh.book, sh.signal_key, sh.label_kind, sh.features = "main", "sig_" + sym, "TARGET_STOP_TIME", {"setup_type": "pullback"}
    set_tour(eng, T0_MS + 60_000)
    step(xp, eng, T0 + timedelta(minutes=2))
    cf = by(rows_of(eng), kind="xp_cf")
    assert [(r["symbol"], r["book"], r["setup_type"], r["family"]) for r in cf] == [("ETH/USDT", "main", "pullback", "TREND")]
    c = status_of(eng)["counters"]
    assert c["spot_excluded"] == 1 and c["row_errors"] == {"SPOT_EXCLUDED": 1}
    step(xp, eng, T0 + timedelta(minutes=3))
    assert len(by(rows_of(eng), kind="xp_cf")) == 1 and status_of(eng)["counters"]["spot_excluded"] == 1, "SPOT bir kez sayılır"


# ============================================================================ erteleme (nedensellik)
def test_event_after_a_bar_boundary_is_deferred_then_equals_the_snapshot_from_complete_frames(tmp_path):
    """Box karşı-olgusalı 12:05'te; çerçeveler 11:58'de başlayan turdan (08:00 4h ve 11:00 1h barları KISMİ) → PENDING;
    12:10 turunun çerçeveleriyle OK ve tam çerçevelerden hesaplanan anlık görüntüyle BİREBİR aynı."""
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    t_1158 = datetime(2026, 9, 29, 11, 58, tzinfo=UTC)
    eng = fake_engine(tmp_path, books=[box], fetch_ms=int(t_1158.timestamp() * 1000))
    xp = collector(eng)
    step(xp, eng, t_1158)
    ev = datetime(2026, 9, 29, 12, 5, tzinfo=UTC)
    t = record_cf(box, "SOL/USDT", sig=int(ev.timestamp() * 1000), created=ev)
    step(xp, eng, ev + timedelta(seconds=30))                               # aynı turun (11:58) çerçeveleri
    assert by(rows_of(eng), kind="xp_cf") == [] and len(xp._drafts) == 1
    assert status_of(eng)["drafts_by"] == {"C:LIVE": 1}
    t_1210 = datetime(2026, 9, 29, 12, 10, tzinfo=UTC)
    set_tour(eng, int(t_1210.timestamp() * 1000))
    step(xp, eng, t_1210)
    cf = by(rows_of(eng), kind="xp_cf")
    assert len(cf) == 1 and cf[0]["snapshot_status"] == "OK" and cf[0]["snapshot_meta"]["lag_steps"] == 1
    as_of = int(ev.timestamp() * 1000)
    full = eng.world.frames(int(t_1210.timestamp() * 1000) + 3 * H4)        # üç tur SONRA alınmış çerçeveler
    wins = [S.normalize_rows(full[s][tf], tf, as_of, W=w, source="x") for s, tf, w in
            (("SOL/USDT", "4h", S.W4H), ("SOL/USDT", "1h", S.W1H), (BTC, "4h", S.WBTC))]
    ref = S.snapshot("SOL/USDT", *wins, as_of)
    assert json.loads(json.dumps(ref)) == cf[0]["snapshot"] and cf[0]["snapshot"]["input_sha"] == ref["input_sha"]
    assert t.id == cf[0]["cf_id"]


def test_live_draft_waits_at_most_pending_max_age_then_is_written_as_gap(tmp_path):
    eng = fake_engine(tmp_path, pending_max_age_h=1.0)
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1))
    eng.runner.last_frames["SOL/USDT"]["1h"] = eng.runner.last_frames["SOL/USDT"]["1h"].iloc[:-3]   # 1h kaynağı geride
    step(xp, eng, T0 + timedelta(minutes=2))
    step(xp, eng, T0 + timedelta(minutes=30))
    assert rows_of(eng) == [] and len(xp._drafts) == 1
    step(xp, eng, T0 + timedelta(hours=1, minutes=2))
    e = by(rows_of(eng), kind="xp_entry")
    assert len(e) == 1 and e[0]["snapshot_status"] == "GAP" and "h1_window" in e[0]["snapshot"]["missing"]
    assert e[0]["snapshot"]["h1_rsi14"] is None, "bayat barlardan alan HESAPLANMAZ"
    assert status_of(eng)["counters"]["drafts_expired"] == 1


# ============================================================================ kilit / bütçe / G/Ç / devre kesici
def test_busy_book_lock_is_skipped_and_nothing_is_lost_on_the_next_step(tmp_path):
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box], lock_timeout_s=0.0)
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(box.ledger, "SOL/USDT", at=T0 + timedelta(minutes=1), meta={"signal": {"signal_ts": T0_MS}})
    record_cf(box, "ETH/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    set_tour(eng, T0_MS + 60_000)
    held, release = threading.Event(), threading.Event()

    def holder():
        with box.lock:                                                      # Box zamanlayıcısı geçişi gibi
            held.set()
            release.wait(10)
    th = threading.Thread(target=holder)
    th.start()
    held.wait(5)
    try:
        step(xp, eng, T0 + timedelta(minutes=2))
    finally:
        release.set()
        th.join(5)
    assert by(rows_of(eng), book="strategy_paper_box") == []
    c = status_of(eng)["counters"]
    assert c["lock_busy_skips"] == 1 and c["lock_busy_by_book"] == {"strategy_paper_box": 1}
    step(xp, eng, T0 + timedelta(minutes=3))
    assert sorted(r["kind"] for r in by(rows_of(eng), book="strategy_paper_box")) == ["xp_cf", "xp_entry"]


def test_row_cap_carries_work_over_and_outcomes_come_before_new_snapshot_rows(tmp_path):
    eng = fake_engine(tmp_path, max_rows_per_tour=10)
    xp = collector(eng)
    step(xp, eng, T0)
    for i, sym in enumerate(("SOL/USDT", "ETH/USDT", "AVAX/USDT")):
        open_pos(eng.ledger2, sym, at=T0 + timedelta(minutes=1))
    set_tour(eng, T0_MS + 60_000)
    step(xp, eng, T0 + timedelta(minutes=2))                                # 3 giriş
    for sym in ("SOL/USDT", "ETH/USDT", "AVAX/USDT"):
        eng.ledger2.close_manual(sym, D("101"), "hedef", T0 + timedelta(minutes=10))
    # 12 yeni pozisyon (aynı sembol tekrar açılabilir: öncekiler kapandı) + 3 kapanış → tavan 10
    for k in range(4):
        for sym in ("SOL/USDT", "ETH/USDT", "AVAX/USDT"):
            open_pos(eng.ledger2, sym, at=T0 + timedelta(minutes=11 + k))
            if k < 3:
                eng.ledger2.close_manual(sym, D("100.5"), "manual", T0 + timedelta(minutes=11 + k, seconds=30))
    step(xp, eng, T0 + timedelta(minutes=20))
    first = rows_of(eng)[3:]
    assert len(first) == 10 and status_of(eng)["counters"]["row_cap_hits"] >= 1
    assert [r["kind"] for r in first[:10]].count("xp_outcome") == 10, "kapanışlar önce (anlık görüntü gerekmez)"
    for i in range(4):
        step(xp, eng, T0 + timedelta(minutes=21 + i))
    rows = rows_of(eng)
    ids = [r["row_id"] for r in rows]
    assert len(ids) == len(set(ids)), "taşımada tekrar yok"
    assert len(by(rows, kind="xp_outcome")) == 12 and len(by(rows, kind="xp_entry")) == 15
    tks = {r["trade_key"] for r in by(rows, kind="xp_outcome")}
    assert tks <= {r["trade_key"] for r in by(rows, kind="xp_entry")}, "her kapanışın girişi var"


def test_store_io_error_does_not_advance_cursors_and_the_retry_writes_each_row_once(tmp_path, monkeypatch):
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box])
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1))
    record_cf(box, "ETH/USDT", sig=T0_MS, created=T0 + timedelta(minutes=1))
    set_tour(eng, T0_MS + 60_000)
    import tradingbot.shared_experience.store as store_mod

    def boom(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store_mod.os, "fsync", boom)
    step(xp, eng, T0 + timedelta(minutes=2))
    monkeypatch.undo()
    c = status_of(eng)["counters"]
    assert c["write_errors"] == 1 and c["rows_total"] == 0 and len(xp._drafts) == 2
    assert xp._books["main"].entry_done == {} and xp._books["strategy_paper_box"].cf_known == {}
    step(xp, eng, T0 + timedelta(minutes=3))
    rows = [r for r in xp.store.iter_all_rows()]
    assert sorted(r["kind"] for r in rows) == ["xp_cf", "xp_entry"]


def test_io_error_commits_nothing_so_a_duplicate_later_in_the_batch_cannot_skip_an_unwritten_close(tmp_path, monkeypatch):
    """(2026-09-29) Aynı defterde r1 (yazılmamış) ve r2 (kapanış satırı önceki bir süreçten sıcak dosyada) birlikte gelir;
    toplu yazım G/Ç hatası verir. Eskiden r2'nin (tekrar) imleç işlemi çalışıp `last_key`i r2'ye taşıyordu → r1'in kapanışı
    bir daha HİÇ yazılmıyordu. Şimdi hata adımında hiçbir imleç işlemi çalışmaz; sonraki adım ikisini de işler."""
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)                                                      # doğum
    open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1))
    eng.ledger2.close_manual("SOL/USDT", D("101"), "hedef", T0 + timedelta(minutes=2))
    open_pos(eng.ledger2, "ETH/USDT", at=T0 + timedelta(minutes=3))
    r2 = eng.ledger2.close_manual("ETH/USDT", D("101"), "hedef", T0 + timedelta(minutes=4))
    env = R.make_env(recorded_at=T0, code_sha=None)
    prior = R.outcome_row(r2, book="main", rev=0, final=True, env=env)     # önceki süreçten kalan satır (imleç kayıp)
    assert xp.store.append_rows([prior]) == (1, 0)
    set_tour(eng, T0_MS + 4 * 60_000)
    import tradingbot.shared_experience.store as store_mod

    def boom(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store_mod.os, "fsync", boom)
    step(xp, eng, T0 + timedelta(minutes=5))
    monkeypatch.undo()
    assert xp._books["main"].last_key is None, "hata adımında imleç İLERLEMEZ (tekrar satırı olsa bile)"
    step(xp, eng, T0 + timedelta(minutes=6))
    rows = list(xp.store.iter_all_rows())
    outs = {r["trade_id"] for r in by(rows, kind="xp_outcome")}
    ents = {r["trade_id"] for r in by(rows, kind="xp_entry")}
    assert outs == ents == {"F00001", "F00002"}, (outs, ents)
    c = status_of(eng)["counters"]
    # yazılan = r1 giriş + r1 kapanış + r2 giriş; r2 kapanışı zaten vardı → tekrar olarak sayılır, toplamı şişirmez
    assert c["rows_total"] == 3 and c["duplicate_rows"] == 1 and c["write_errors"] == 1


def test_live_boundary_is_the_layer_birth_even_when_the_first_read_of_a_book_is_late(tmp_path):
    """(2026-09-29) Geri doldurma KAPALI. İlk adımda Box kilidi meşgul (Box zamanlayıcısı), ikinci adımda ilk okuma yapılır.
    Doğumdan SONRA açılıp kapanan Box işlemi ve ilk turda (doğumdan sonra, adımdan önce) açılıp kapanan ana bot işlemi
    canlı kayıttır; doğumdan ÖNCE kapanan işlem kayıt dışıdır. Eskiden ilk okumadaki "son kayıt" sınır alınıyordu → iki
    canlı işlem de kayboluyordu."""
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box], backfill=False, lock_timeout_s=0.0)
    open_pos(box.ledger, "AVAX/USDT", at=T0 - timedelta(hours=3), meta={"signal": {"signal_ts": 1}})
    box.ledger.close_manual("AVAX/USDT", D("101"), "hedef", T0 - timedelta(hours=2))           # doğumdan önce
    born = T0 - timedelta(minutes=1)                                        # doğum = ilk turun başlangıcı (fetch_ms)
    open_pos(eng.ledger2, "ETH/USDT", at=born + timedelta(seconds=10))
    eng.ledger2.close_manual("ETH/USDT", D("101"), "hedef", born + timedelta(seconds=40))      # ilk turun içinde
    held, release = threading.Event(), threading.Event()

    def holder():
        with box.lock:
            open_pos(box.ledger, "SOL/USDT", at=T0 + timedelta(seconds=5), meta={"signal": {"signal_ts": T0_MS}})
            box.ledger.close_manual("SOL/USDT", D("99"), "stop", T0 + timedelta(seconds=30))
            held.set()
            release.wait(10)
    th = threading.Thread(target=holder)
    th.start()
    held.wait(5)
    xp = collector(eng)
    try:
        step(xp, eng, T0 + timedelta(minutes=1))
    finally:
        release.set()
        th.join(5)
    assert status_of(eng)["counters"]["lock_busy_by_book"] == {"strategy_paper_box": 1}
    set_tour(eng, T0_MS + 5 * 60_000)
    step(xp, eng, T0 + timedelta(minutes=6))
    rows = rows_of(eng)
    got = sorted((r["kind"], r["book"], r["symbol"], r["origin"]) for r in rows)
    assert got == [("xp_entry", "main", "ETH/USDT", "LIVE"), ("xp_entry", "strategy_paper_box", "SOL/USDT", "LIVE"),
                   ("xp_outcome", "main", "ETH/USDT", "LIVE"), ("xp_outcome", "strategy_paper_box", "SOL/USDT", "LIVE")], got
    assert xp._books["strategy_paper_box"].bf_done is True
    step(xp, eng, T0 + timedelta(minutes=7))
    assert len(rows_of(eng)) == 4, "tekrar yok"


def test_five_consecutive_exceptions_trip_the_breaker_for_the_process(tmp_path, monkeypatch):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    monkeypatch.setattr(xp, "_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    for i in range(4):
        assert step(xp, eng, T0 + timedelta(minutes=i))["state"] == "OK"
    assert step(xp, eng, T0 + timedelta(minutes=5))["state"] == C.STATE_BREAKER
    monkeypatch.undo()
    assert step(xp, eng, T0 + timedelta(minutes=6)) == {"state": C.STATE_BREAKER, "rows": 0}
    st = status_of(eng)
    assert st["state"] == C.STATE_BREAKER and st["counters"]["errors_total"] == 5
    assert st["last_error_code"] == "RuntimeError: boom" and xp.health()["state"] == C.STATE_BREAKER


def test_three_consecutive_budget_overruns_trip_the_breaker(tmp_path, monkeypatch):
    eng = fake_engine(tmp_path, tour_budget_s=0.1)
    xp = collector(eng)
    clock = {"t": 0.0}

    def fake_perf():
        clock["t"] += 1.0                                                   # her okuma 1 sn: adım > 1,5 × bütçe
        return clock["t"]
    monkeypatch.setattr(C.time, "perf_counter", fake_perf)
    for i in range(2):
        step(xp, eng, T0 + timedelta(minutes=i))
        assert xp.state == "OK"
    step(xp, eng, T0 + timedelta(minutes=3))
    assert xp.state == C.STATE_BREAKER and status_of(eng)["counters"]["budget_overruns"] == 3


def test_work_stops_early_enough_that_the_write_tail_does_not_trip_the_breaker_during_a_backlog(tmp_path, monkeypatch):
    """(2026-09-29) İş döngüleri bütçenin %75'inde durur; bütçesiz kuyruk (toplu yazım + döngü + imleç) kalan payı kullanır.
    Sahte saat: her bütçe denetimi 0,01 sn, toplu yazım 0,6 × bütçe. Eskiden iş tam bütçeye kadar sürüyordu → adım 1,6 ×
    bütçe → üç ardışık adımda DEVRE KESİCİ (ilk açılış geri doldurmasında katman süreç boyunca kapanıyordu)."""
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box], tour_budget_s=1.0, max_rows_per_tour=5000)
    xp = collector(eng)
    step(xp, eng, T0)
    for i in range(400):                                                     # canlı birikim: 400 karşı-olgusal
        record_cf(box, SYMS[i % 3], sig=T0_MS + i * 300_000, created=T0 + timedelta(minutes=1), side="LONG")
    set_tour(eng, T0_MS + 60_000)
    clock = {"t": 0.0}

    def fake_perf():
        clock["t"] += 0.01
        return clock["t"]
    orig_append = xp.store.append_rows

    def slow_append(rows):
        clock["t"] += 0.6                                                    # kuyruk: 0,6 × bütçe
        return orig_append(rows)
    monkeypatch.setattr(C.time, "perf_counter", fake_perf)
    monkeypatch.setattr(xp.store, "append_rows", slow_append)
    for k in range(3):
        step(xp, eng, T0 + timedelta(minutes=2 + k))
        assert xp.state == "OK", (k, status_of(eng)["breaker"])
    c = status_of(eng)["counters"]
    assert c["budget_overruns"] == 0 and c["budget_hits"] == 3 and 0 < c["rows_total"] < 400, c
    assert max(xp._step_ms) <= C.OVERRUN_FACTOR * 1000.0


def test_non_paper_mode_suspends_and_writes_no_rows(tmp_path):
    eng = fake_engine(tmp_path, mode="TESTNET")
    xp = collector(eng)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 - timedelta(minutes=5))
    res = step(xp, eng, T0)
    assert res["state"] == "SUSPENDED:TESTNET" and rows_of(eng) == []
    assert status_of(eng)["state"] == "SUSPENDED:TESTNET"
    eng.mode_state.mode.value = "PAPER"
    eng.mode_state.is_live_order_path_enabled = lambda: True
    assert step(xp, eng, T0)["state"] == "SUSPENDED:LIVE_ORDER_PATH_ENABLED" and rows_of(eng) == []


def test_degraded_store_reads_and_writes_nothing(tmp_path, monkeypatch):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 - timedelta(minutes=5))
    monkeypatch.setattr(xp.store, "disk_bytes", lambda: int(xp.store.max_total_bytes * 1.2))
    step(xp, eng, T0)
    assert xp.state == "DEGRADED" and rows_of(eng) == [] and xp._books == {}
    assert status_of(eng)["counters"]["degraded_steps"] == 1


# ============================================================================ geri doldurma / imleç
def test_first_start_backfills_history_open_positions_and_counterfactuals_with_origins(tmp_path):
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box])
    old = T0 - timedelta(hours=6)
    open_pos(eng.ledger2, "SOL/USDT", at=old)
    eng.ledger2.close_manual("SOL/USDT", D("102"), "hedef", old + timedelta(hours=1))
    open_pos(eng.ledger2, "ETH/USDT", at=old + timedelta(hours=2))
    record_cf(box, "AVAX/USDT", sig=1, created=old)
    xp = collector(eng)
    step(xp, eng, T0)
    rows = rows_of(eng)
    got = sorted((r["kind"], r["symbol"], r["origin"]) for r in rows)
    assert got == [("xp_cf", "AVAX/USDT", "BACKFILL_CF"), ("xp_entry", "ETH/USDT", "BACKFILL_OPEN"),
                   ("xp_entry", "SOL/USDT", "BACKFILL_HISTORY"), ("xp_outcome", "SOL/USDT", "BACKFILL_HISTORY")]
    assert all(r["snapshot_status"] == "OK" for r in rows if "snapshot_status" in r)
    assert xp._books["main"].bf_done is True
    # yeni süreç (imleç kalıcı): hiçbir şey yeniden yazılmaz
    xp2 = collector(eng)
    step(xp2, eng, T0 + timedelta(minutes=1))
    assert len(rows_of(eng)) == len(rows)


def test_backfill_disabled_ignores_everything_before_the_first_start(tmp_path):
    eng = fake_engine(tmp_path, backfill=False)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 - timedelta(hours=2))
    open_pos(eng.ledger2, "ETH/USDT", at=T0 - timedelta(hours=3))
    eng.ledger2.close_manual("ETH/USDT", D("101"), "hedef", T0 - timedelta(hours=1))
    xp = collector(eng)
    step(xp, eng, T0)
    eng.ledger2.close_manual("SOL/USDT", D("101"), "hedef", T0 + timedelta(minutes=5))
    open_pos(eng.ledger2, "AVAX/USDT", at=T0 + timedelta(minutes=6))
    set_tour(eng, T0_MS + 6 * 60_000)
    step(xp, eng, T0 + timedelta(minutes=7))
    assert [(r["kind"], r["symbol"]) for r in rows_of(eng)] == [("xp_entry", "AVAX/USDT")]
    assert status_of(eng)["counters"]["ignored_backfill"] == 1


def test_corrupt_cursor_is_set_aside_resynced_and_reading_has_no_duplicate_row_ids(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1))
    set_tour(eng, T0_MS + 60_000)
    step(xp, eng, T0 + timedelta(minutes=2))
    cur = eng.cfg.state_path / "shared_experience" / "cursor.json"
    cur.write_text("{not json", encoding="utf-8")
    xp2 = collector(eng)
    assert xp2.c["cursor_corrupt"] == 1 and (cur.parent / "cursor.json.corrupt").exists()
    step(xp2, eng, T0 + timedelta(minutes=3))
    ids = [r["row_id"] for r in xp2.store.iter_all_rows()]
    assert len(ids) == len(set(ids)) == 1, "deterministik kimlik: yeniden eşitleme tekrar YAZMAZ (sıcak dosya tekilliği)"
    assert json.loads(cur.read_text(encoding="utf-8"))["schema"] == C.CURSOR_SCHEMA


def test_ledger_reset_with_the_same_trade_id_is_a_new_trade_and_counts_a_resync(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)
    open_pos(eng.ledger2, "SOL/USDT", at=T0 + timedelta(minutes=1))
    eng.ledger2.close_manual("SOL/USDT", D("101"), "hedef", T0 + timedelta(minutes=2))
    set_tour(eng, T0_MS + 2 * 60_000)
    step(xp, eng, T0 + timedelta(minutes=3))
    eng.ledger2 = FuturesLedgerV2(1000, max_positions=None)                   # sıfırlama: F00001 yeniden
    open_pos(eng.ledger2, "ETH/USDT", at=T0 + timedelta(minutes=4))
    eng.ledger2.close_manual("ETH/USDT", D("101"), "hedef", T0 + timedelta(minutes=5))
    set_tour(eng, T0_MS + 5 * 60_000)
    step(xp, eng, T0 + timedelta(minutes=6))
    outs = by(rows_of(eng), kind="xp_outcome")
    assert [o["trade_id"] for o in outs] == ["F00001", "F00001"] and len({o["trade_key"] for o in outs}) == 2
    assert status_of(eng)["counters"]["resyncs"] == 1


# ============================================================================ bellek sınırı
def test_draft_cap_forces_the_oldest_drafts_out_with_their_current_status(tmp_path):
    eng = fake_engine(tmp_path, max_rows_per_tour=10)                       # taslak tavanı max(100, 10) = 100
    xp = collector(eng)
    xp.max_drafts = 3
    step(xp, eng, T0)
    for sym in SYMS:
        open_pos(eng.ledger2, sym, at=T0 + timedelta(minutes=1))
    for sym in SYMS + ("DOT/USDT",):
        eng.runner.last_frames.pop(sym, None)                               # hiç kaynak yok → hepsi bekler
    step(xp, eng, T0 + timedelta(minutes=2))
    assert rows_of(eng) == [] and len(xp._drafts) == 3
    open_pos(eng.ledger2, "DOT/USDT", at=T0 + timedelta(minutes=3))
    step(xp, eng, T0 + timedelta(minutes=4))
    e = by(rows_of(eng), kind="xp_entry")
    assert len(e) == 1 and e[0]["snapshot_status"] == "NO_BARS" and len(xp._drafts) == 3
    assert status_of(eng)["counters"]["drafts_forced"] == 1


def test_status_and_health_are_small_and_complete(tmp_path):
    eng = fake_engine(tmp_path)
    xp = collector(eng)
    step(xp, eng, T0)
    h = xp.health()
    assert set(h) == {"mode", "state", "rows_total", "rows_last", "drafts", "step_ms", "errors", "disk_mb"}
    st = status_of(eng)
    for k in ("state", "last_step_at", "step_ms_p50", "step_ms_p95", "drafts", "breaker", "counters", "cache", "store",
              "budget", "backfill"):
        assert k in st, k
    for k in ("rows_by_kind", "snapshot_status_mix", "lock_busy_skips", "vanished_by_status", "legacy_gross_cf",
              "write_errors", "errors_total", "budget_overruns", "resyncs"):
        assert k in st["counters"], k
    assert st["store"]["state"] == "OK" and st["budget"]["lazy_fetch_max_per_tour"] == 0


# ============================================================================ bar kaynağı
def test_bar_source_uses_perp_tour_frames_only_then_the_closed_bar_csv_cache(tmp_path):
    w = World()
    fetch = T0_MS - 60_000
    frames = w.frames(fetch)
    csv_dir = tmp_path / "data"
    from tradingbot.pattern_trader.data import CsvCandleCache
    cc = CsvCandleCache(csv_dir)
    full = w.series("SOL/USDT", "4h")
    cc.write("SOL/USDT", "4h", full[full["timestamp"] + H4 <= T0_MS].copy())   # CSV: yalnız kapanmış barlar
    bs = BarSource(frames=frames, provenance={"SOL/USDT": {"market": "SPOT"}}, tour_now_ms=fetch, csv_dir=csv_dir)
    win = bs.window("SOL/USDT", "4h", as_of_ms=T0_MS, W=S.W4H)
    assert win.source == "csv_cache" and S.is_complete(win, T0_MS), "SPOT ikamesi ASLA; CSV önbelleği devreye girer"
    bs2 = BarSource(frames=frames, provenance={"SOL/USDT": dict(PERP)}, tour_now_ms=fetch, csv_dir=csv_dir)
    w2 = bs2.window("SOL/USDT", "4h", as_of_ms=T0_MS, W=S.W4H)
    assert w2.source == "tour_frames" and S.input_sha(w2, w2, w2) == S.input_sha(win, win, win)
    none = bs2.window("DOT/USDT", "4h", as_of_ms=T0_MS, W=S.W4H)
    assert none.n == 0 and none.source == "none"
    assert bs2.window("SOL/USDT", "4h", as_of_ms=T0_MS, W=S.W4H) is w2 and bs2.counters["memo_hits"] == 1


def test_bar_source_fetch_rule_excludes_a_bar_that_closed_after_the_tour_fetched_its_frames(tmp_path):
    w = World()
    fetch = int(datetime(2026, 9, 29, 11, 58, tzinfo=UTC).timestamp() * 1000)
    as_of = int(datetime(2026, 9, 29, 12, 5, tzinfo=UTC).timestamp() * 1000)
    bs = BarSource(frames=w.frames(fetch), provenance={"SOL/USDT": dict(PERP)}, tour_now_ms=fetch, csv_dir=None)
    win = bs.window("SOL/USDT", "4h", as_of_ms=as_of, W=S.W4H)
    assert not S.is_complete(win, as_of) and win.last_open_ms == (as_of // H4) * H4 - 2 * H4, "08:00 barı kısmiydi"


def test_lazy_fetch_is_off_by_default_and_bounded_by_its_budget(tmp_path):
    calls = []

    def fetcher(sym, tf, limit):
        calls.append((sym, tf, limit))
        return World().frame(sym, tf, T0_MS).values.tolist()
    bs = BarSource(frames={}, provenance={}, tour_now_ms=T0_MS, csv_dir=None, fetcher=fetcher)
    assert bs.window("SOL/USDT", "4h", as_of_ms=T0_MS, W=S.W4H).n == 0 and calls == [], "varsayılan 0: ağ YOK"
    bs = BarSource(frames={}, provenance={}, tour_now_ms=T0_MS, csv_dir=None, fetcher=fetcher, fetch_budget=1,
                   wall_ms=lambda: T0_MS)
    a = bs.window("SOL/USDT", "4h", as_of_ms=T0_MS, W=S.W4H)
    b = bs.window("ETH/USDT", "4h", as_of_ms=T0_MS, W=S.W4H)
    assert a.source == "lazy_fetch" and S.is_complete(a, T0_MS) and b.n == 0 and len(calls) == 1
    assert C.XpSettings().lazy_fetch_max_per_tour == 0 == SharedExperienceSection().lazy_fetch_max_per_tour


def test_collector_never_mutates_the_books_it_reads(tmp_path):
    """Adım öncesi/sonrası defter, kayıtçı ve gölge defteri JSON'u bit-aynı (salt okur)."""
    box = FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = fake_engine(tmp_path, books=[box])
    open_pos(eng.ledger2, "SOL/USDT", at=T0 - timedelta(minutes=5), features={"learning": {"learning_unlocked_by": ["X"]}})
    open_pos(box.ledger, "ETH/USDT", at=T0 - timedelta(minutes=5), meta={"signal": {"signal_ts": 1}})
    t = record_cf(box, "AVAX/USDT", sig=1, created=T0 - timedelta(minutes=5))
    t.outcome = {"r_multiple": 1.0, "label_version": "cf_label_v3", "r_net": 0.8, "r_gross": 1.0, "cost_r": 0.2}

    def snap():
        return (json.dumps(eng.ledger2.to_dict(), sort_keys=True, default=str),
                json.dumps(box.ledger.to_dict(), sort_keys=True, default=str),
                json.dumps([x.to_dict() for x in box.cf.sb.trades], sort_keys=True, default=str),
                json.dumps([x.to_dict() for x in eng.shadow.trades], sort_keys=True, default=str))
    before = snap()
    xp = collector(eng)
    for i in range(3):
        step(xp, eng, T0 + timedelta(minutes=i))
    assert snap() == before and len(rows_of(eng)) == 3

# -*- coding: utf-8 -*-
"""T2/M2 PAPER — CANLI FİYAT VE ZAMAN DOĞRULAMASI (2026-09-16, inceleme tabanı 6042fcf).

Üç bulgu 6042fcf'de gerçek motor turuyla yeniden üretildi (`docs/review/evidence-2026-09-16/repro_pt.py`, `pt_before.txt`):
F1 60 sn çıkış izleyicisi turun bağlı (`perp_mark`) fiyatını `tour_id == run_id` olduğu sürece yeniden kullanıyor, canlı
sağlayıcıyı hiç sormuyordu; F2 giriş ÖNCESİ kapanmış 1h mumunun uçları her tur/izleyici tick'ine ekleniyor, yanlış stop ve
MFE/MAE üretiyordu (tur içi tick + izleyici); F3 haftalarca eski günlük seri yeni tur kimliğiyle bağlanınca veri kapısını
geçiyor, kural giriş açıyordu. Bu dosya kabul matrisini gerçek motor turu + gerçek StrategyBook/FuturesLedgerV2 ile sınar
(ağ yerine deterministik sağlayıcı adaptörleri; doğrulama fonksiyonları mock'lanmaz).

Zaman sözleşmeleri (kod: `strategy_paper.py` sabitleri): canlı fiyat = `funding.mark` + kaynak zamanı (`funding.ts` yoksa
snapshot `ts`), kontrol anına göre yaş <= PRICE_MAX_AGE_S, gelecekte değil; bar uçları = kapanmış 1h USDM_PERP barı,
pozisyon açılışından SONRA açılmış, bir kez (`meta.ohlc_cursor`); günlük sinyal = `as_of` anında kapanmış son bar,
beklenen son kapanış (as_of - tolerans) kadar güncel; replay `as_of` = simülasyon anı.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_strategy_paper_data_source_v1 import _books, _eng, _positions, _summary, _tour, BOOKS  # noqa: E402
from test_strategy_paper_engine_v1 import BTC, SYMS  # noqa: E402
from tradingbot.accounting import TickData  # noqa: E402
from tradingbot.candle_confirmation import closed_bars  # noqa: E402
from tradingbot.core import iso, utc_now  # noqa: E402
from tradingbot.ema200_trend import daily_rows_from_frame  # noqa: E402
from tradingbot.strategy_paper import (BAR_LAG_TOLERANCE_MS, PRICE_MAX_AGE_S, DataVerdict, StrategyBook,  # noqa: E402
                                       book_specs, frame_freshness, parse_ts_ms, verified_price, verify_paper_data)

DAY = 86_400_000
H1 = 3_600_000


def _t2(eng) -> StrategyBook:
    return _books(eng)["strategy_paper"]


def _stop(book, s) -> float:
    return float(book.ledger.positions[s].stop)


def _patch_snapshot(eng, monkeypatch, fn):
    """Canlı sağlayıcı taklidi: `fn(symbol, orijinal_snapshot) -> snapshot`; çağrılar sayılır."""
    base = getattr(eng, "_base_snapshot", None) or eng.runner.live.snapshot     # hep ORİJİNAL sağlayıcı sarılır (zincir yok)
    eng._base_snapshot = base
    calls: list[str] = []

    def snap(sym):
        calls.append(sym)
        return fn(sym, dict(base(sym)))
    monkeypatch.setattr(eng.runner.live, "snapshot", snap)
    return calls


def _raise(sym, d):
    raise RuntimeError("feed down")


def _mark(price: float, *, age_s: float = 0.0, funding_ts: bool = False):
    def fn(sym, d):
        d["funding"] = {"rate": 0.0, "mark": price if not callable(price) else price(sym)}
        now = time.time()
        if funding_ts:
            d["funding"]["ts"] = int((now - age_s) * 1000)     # borsa mark zaman damgası (ms)
            d["ts"] = now
        else:
            d["ts"] = now - age_s                               # yalnız alınma zamanı (epoch sn)
        return d
    return fn


# ====================================================================== 1) izleyici: aynı run_id, pozitif bağlı mark, fiyat 105→95
def test_1_exit_monitor_asks_the_live_provider_each_check_and_stops_on_a_fresh_price_below_stop(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)                                                       # sağlıklı tur: güncel snapshot, pozitif bağlı perp_mark
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    t2 = _t2(eng)
    run_id = eng.run_id
    stop = {s: _stop(t2, s) for s in SYMS}
    entry = {s: float(t2.ledger.positions[s].entry_avg) for s in SYMS}
    bound = {s: eng._frame_provenance[s]["perp_mark"] for s in SYMS}
    assert all(b and b["price"] > stop[s] and b["fresh"] is True and b["price_ts_ms"] and b["fetched_at_ms"] for s, b in bound.items()), \
        "bağlı perp_mark pozitif ve stop üstü (KAYIT: zamanlarıyla birlikte), fiyat kaynağı değil"
    # 10 dakika geçmiş olsun: bağlı kayıt ve sahte sağlayıcının kendi görüntüsü yaşlanır; bağlı mark TEMİZLENMEZ, yeni tur AÇILMAZ
    eng._fake_live._now_s = time.time() - 600.0
    for s in SYMS:
        pm = eng._frame_provenance[s]["perp_mark"]
        pm["ts"] -= 600.0; pm["price_ts_ms"] -= 600_000; pm["fetched_at_ms"] -= 600_000
    # aynı run_id ile 60 sn kontrolleri; sağlayıcı güncel ve stop altı fiyat veriyor
    calls = _patch_snapshot(eng, monkeypatch, _mark(lambda s: stop[s] * 0.95))
    eng._strategy_paper_exit_check()
    assert eng.run_id == run_id and len(calls) >= len(SYMS), "her kontrolde canlı sağlayıcı soruldu (tur kimliği eşitliği fiyat güncelliği sayılmadı)"
    assert _positions(eng) == {k: [] for k in BOOKS}, "105→95 geçişinde stop ilk kontrolde çalıştı; yeni strateji turu beklenmedi"
    for b in _books(eng).values():
        assert b.counters["closed"] == len(SYMS) and b.data_gaps == {}
        for h in b.ledger.history_dicts():
            assert h["exit_reason"] == "stop" and float(h["exit_price"]) < entry[h["symbol"]]
            assert float(h["exit_price"]) <= stop[h["symbol"]] * 1.001, "gerçekleşme güncel doğrulanmış markla (stop altı gap-through)"
    # ikinci ve üçüncü kontrol: pozisyon yok → sağlayıcı gereksiz sorulmaz, kapanış yinelenmez
    n = len(calls)
    eng._strategy_paper_exit_check(); eng._strategy_paper_exit_check()
    assert len(calls) == n and all(b.counters["closed"] == len(SYMS) for b in _books(eng).values())


# ====================================================================== 2) izleyici: bayat / geçersiz / kesik sağlayıcı → boşluk, uydurma fill yok; toparlanma
def test_2_stale_invalid_or_missing_price_is_a_visible_gap_never_a_fill_and_recovers(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)
    t2 = _t2(eng)
    stop = {s: _stop(t2, s) for s in SYMS}
    last0 = {s: float(t2.ledger.positions[s].last_price) for s in SYMS}
    # a) yalnız yaşlı fiyat (kaynak zamanı 10 dk önce) ve stop altı → BAYAT: tick yok, son bilinen fiyat korunur
    _patch_snapshot(eng, monkeypatch, _mark(lambda s: stop[s] * 0.9, age_s=PRICE_MAX_AGE_S + 420))
    eng._strategy_paper_exit_check()
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    for key, b in _books(eng).items():
        assert {g["reason"] for g in b.data_gaps.values()} == {"STALE_FUTURES_PRICE"} and set(b.data_gaps) == set(SYMS)
        assert all(g["age_s"] > PRICE_MAX_AGE_S and g["price_ts"] and g["last_seen_mark"] < stop[s] for s, g in b.data_gaps.items()), "yaşlı fiyat kayıtta, fill'de değil"
        assert [e["reason"] for e in b.data_events if e["kind"] == "PRICE_GAP"] == ["STALE_FUTURES_PRICE"] * len(SYMS)
        assert all(float(b.ledger.positions[s].last_price) == last0[s] for s in SYMS), "eski fiyata yeni zaman damgası basılmadı; son bilinen fiyat dürüstçe kaldı"
        doc = _summary(eng, key)
        assert set(doc["data_gaps"]) == set(SYMS) and doc["data_policy"]["price"]["max_age_s"] == PRICE_MAX_AGE_S
    # b) borsa mark zamanı gelecekte → geçersiz zaman (gerekçe değişti: yeni olay, bir kez)
    _patch_snapshot(eng, monkeypatch, _mark(lambda s: stop[s] * 0.9, age_s=-900, funding_ts=True))
    eng._strategy_paper_exit_check(); eng._strategy_paper_exit_check()
    for b in _books(eng).values():
        assert {g["reason"] for g in b.data_gaps.values()} == {"INVALID_FUTURES_PRICE_TIME"}
        assert [e["reason"] for e in b.data_events if e["kind"] == "PRICE_GAP"] == ["STALE_FUTURES_PRICE"] * len(SYMS) + ["INVALID_FUTURES_PRICE_TIME"] * len(SYMS)
    # c) sağlayıcı kesik (istisna) → fiyat yok
    _patch_snapshot(eng, monkeypatch, _raise)
    eng._strategy_paper_exit_check()
    for b in _books(eng).values():
        assert {g["reason"] for g in b.data_gaps.values()} == {"NO_VERIFIED_FUTURES_PRICE"} and "feed down" in next(iter(b.data_gaps.values()))["detail"]
        assert b.counters["closed"] == 0
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    # d) geçerli, güncel fiyat (borsa mark zamanı 5 sn önce) stop üstünde → boşluk kapanır, pozisyon açık; sonra stop altı → kapanır
    _patch_snapshot(eng, monkeypatch, _mark(lambda s: stop[s] * 1.02, age_s=5, funding_ts=True))
    eng._strategy_paper_exit_check()
    for b in _books(eng).values():
        assert b.data_gaps == {} and [e for e in b.data_events if e["kind"] == "PRICE_RESTORED"]
        assert all(abs(float(b.ledger.positions[s].last_price) / (stop[s] * 1.02) - 1) < 1e-9 for s in SYMS)
    _patch_snapshot(eng, monkeypatch, _mark(lambda s: stop[s] * 0.99, age_s=5, funding_ts=True))
    eng._strategy_paper_exit_check()
    assert _positions(eng) == {k: [] for k in BOOKS} and all(b.counters["closed"] == len(SYMS) for b in _books(eng).values())
    # saf sözleşme: verified_price
    now_ms = int(time.time() * 1000)
    assert verified_price({"funding": {"mark": 100.0}, "ts": time.time()}, now_ms=now_ms)["ok"]
    assert verified_price({"funding": {"mark": 100.0, "ts": now_ms - 30_000}, "ts": time.time()}, now_ms=now_ms)["age_s"] == pytest.approx(30.0, abs=0.01)
    assert verified_price({"funding": {"mark": 0.0}, "ts": time.time()}, now_ms=now_ms)["reason"] == "NO_VERIFIED_FUTURES_PRICE"
    assert verified_price({"funding": {"mark": float("inf")}, "ts": time.time()}, now_ms=now_ms)["reason"] == "NO_VERIFIED_FUTURES_PRICE"
    assert verified_price({"funding": {"mark": 100.0}}, now_ms=now_ms)["reason"] == "INVALID_FUTURES_PRICE_TIME"
    assert verified_price({"funding": {"mark": 100.0}, "ts": time.time() - PRICE_MAX_AGE_S - 1}, now_ms=now_ms)["reason"] == "STALE_FUTURES_PRICE"
    assert verified_price(None, now_ms=now_ms)["reason"] == "NO_VERIFIED_FUTURES_PRICE"
    assert parse_ts_ms("2026-09-16T10:31:00Z") == parse_ts_ms(1789554660) == parse_ts_ms(1789554660000) == parse_ts_ms("1789554660.0") == 1789554660000
    assert parse_ts_ms(None) is None and parse_ts_ms("x") is None and parse_ts_ms(-5) is None


# ====================================================================== 3) giriş öncesi 1h fitili: tur tick'i + izleyici; girişten sonraki geçerli fitil stop'lar
def test_3_pre_entry_wick_never_fills_but_a_post_entry_bar_through_the_stop_does(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    orig = eng.runner.run_symbol

    def wick(symbol, analysis=None, prefetched=None):          # son KAPANMIŞ 1h bar (pozisyondan önce): low stop altında
        b = orig(symbol, analysis, prefetched)
        h1 = eng.runner.last_frames[symbol]["1h"]
        px = float(b.price)                                        # sentetik 1h serisi bağımsız seviyede: OHLC fiyata hizalanır
        h1.loc[h1.index[-1], ["open", "high", "low", "close"]] = [px * 1.0, px * 1.01, px * 0.88, px * 0.95]
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", wick)
    _tour(eng)                                                     # tur içi yol: pozisyon açılır ve aynı turda tick'lenir
    t2 = _t2(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS} and all(b.counters["closed"] == 0 for b in _books(eng).values()), \
        "giriş ÖNCESİ bar fitili yeni pozisyonu stop'lamadı (tur tick'i fiyat-yalnız; bar uçları girişten sonraki barlara ait)"
    for s in SYMS:
        p = t2.ledger.positions[s]
        assert float(p.mae_pct) > -1.0 and float(p.mfe_pct) < 1.5, "giriş öncesi uçlar MFE/MAE'ye girmedi"
        assert "ohlc_cursor" not in p.meta, "hiçbir bar tüketilmedi (hepsi girişten önce açılmış)"
    eng._strategy_paper_exit_check()                               # izleyici yolu: aynı eski fitil hâlâ çerçevede
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS} and all(b.counters["closed"] == 0 for b in _books(eng).values())
    marks, marks_f, gaps = eng._paper_marks(list(SYMS), now=utc_now())
    assert gaps == {} and all(marks[s].high is None and marks[s].low is None for s in SYMS), "izleyici tick'i fiyat-yalnız"
    # pozisyon 4 saat önce açılmış olsun (yeniden başlatma/uzun pozisyon): aynı bar artık GİRİŞTEN SONRA açılmış → uçları geçerli
    bar_open = int(eng.runner.last_frames[SYMS[0]]["1h"]["timestamp"].iloc[-1])
    for b in _books(eng).values():
        for s in SYMS:
            b.ledger.positions[s].opened_at = iso(datetime.fromtimestamp(bar_open / 1000, tz=timezone.utc) - timedelta(hours=1))
    _tour(eng)
    assert _positions(eng) == {k: [] for k in BOOKS}, "girişten SONRA açılmış kapanmış barın stop altı low'u koruyucu stop'u çalıştırdı"
    for b in _books(eng).values():
        assert b.counters["closed"] == len(SYMS)
        for h in b.ledger.history_dicts():
            assert h["exit_reason"] == "stop" and h["closed_at"] == iso(datetime.fromtimestamp((bar_open + H1) / 1000, tz=timezone.utc)), \
                "kapanış zamanı barın kapanışı (gerçek olay zamanı), tur saati değil"
            assert float(h["exit_price"]) <= float(h["entry"]) * 0.92


# ====================================================================== 4) aynı bar iki kez tüketilmez: tur tekrarı, izleyici, yeniden başlatma
def test_4_a_closed_bar_is_consumed_once_across_tours_monitor_and_restart(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)
    t2 = _t2(eng)
    entry = {s: float(t2.ledger.positions[s].entry_avg) for s in SYMS}
    bar_open = int(eng.runner.last_frames[SYMS[0]]["1h"]["timestamp"].iloc[-1])
    for b in _books(eng).values():
        for s in SYMS:                                              # pozisyon bardan 1 saat önce açılmış olsun
            b.ledger.positions[s].opened_at = iso(datetime.fromtimestamp(bar_open / 1000, tz=timezone.utc) - timedelta(hours=1))
    for s in SYMS:                                                  # girişten sonraki bar: MAE'yi -3%'e çeker, stop'a (~-9%) değmez
        h1 = eng.runner.last_frames[s]["1h"]
        h1.loc[h1.index[-1], ["open", "high", "low", "close"]] = [entry[s], entry[s] * 1.01, entry[s] * 0.97, entry[s] * 0.99]
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    mae = {s: float(t2.ledger.positions[s].mae_pct) for s in SYMS}
    assert all(-3.2 < v <= -2.8 for v in mae.values()), mae
    assert all(t2.ledger.positions[s].meta["ohlc_cursor"]["1h"] == bar_open for s in SYMS), "imleç barın açılışında"
    events = len(t2.data_events)
    _tour(eng); eng._strategy_paper_exit_check(); _tour(eng)      # tekrar tur + izleyici + tur: aynı bar yeniden tüketilmez
    assert {s: float(t2.ledger.positions[s].mae_pct) for s in SYMS} == mae and all(b.counters["closed"] == 0 for b in _books(eng).values())
    assert len(t2.data_events) == events
    # yeniden başlatma: imleç defter dosyasından (meta) gelir; aynı bar (şimdi stop altı low ile bile) yeniden uygulanmaz
    eng2 = _eng(tmp_path / "e", monkeypatch)
    for s in SYMS:                                                  # yeni motorun çerçevesi: aynı bar, stop altı low
        h1 = eng2._fake_live._frames[s]["1h"]
        h1.loc[h1.index[-1], ["open", "high", "low", "close"]] = [entry[s], entry[s] * 1.01, entry[s] * 0.85, entry[s] * 0.99]
    b2 = _t2(eng2)
    assert all(b2.ledger.positions[s].meta["ohlc_cursor"]["1h"] == bar_open for s in SYMS), "imleç yeniden başlatmada korunur"
    _tour(eng2)
    assert _positions(eng2) == {k: sorted(SYMS) for k in BOOKS} and b2.counters["closed"] == 0, "tüketilmiş bar yeniden stop üretmedi"
    assert {s: float(b2.ledger.positions[s].mae_pct) for s in SYMS} == pytest.approx(mae)
    # doğrudan sözleşme: kapanmamış bar ve girişten önceki bar hiç uygulanmaz; imleç ilerlemez
    now = utc_now()
    unclosed = {SYMS[0]: {"tf": "1h", "mark": entry[SYMS[0]], "rows": [{"timestamp": int(now.timestamp() * 1000) - 600_000, "high": entry[SYMS[0]] * 1.01, "low": entry[SYMS[0]] * 0.5, "close": entry[SYMS[0]]}]}}
    assert b2.apply_closed_bars(unclosed, now=now) == [] and SYMS[0] in b2.ledger.positions and b2.ledger.positions[SYMS[0]].meta["ohlc_cursor"]["1h"] == bar_open
    pre = {SYMS[0]: {"tf": "1h", "mark": entry[SYMS[0]], "rows": [{"timestamp": bar_open - 5 * H1, "high": entry[SYMS[0]] * 1.01, "low": entry[SYMS[0]] * 0.5, "close": entry[SYMS[0]]}]}}
    assert b2.apply_closed_bars(pre, now=now) == [] and SYMS[0] in b2.ledger.positions
    # ölçek dışı bar (canlı mark'ın %20'sinden uzak) uydurulmaz: atlanır, olay kaydedilir, imleç ilerler
    b2.ledger.positions[SYMS[0]].opened_at = iso(datetime.fromtimestamp(bar_open / 1000, tz=timezone.utc) - timedelta(hours=9))
    off = {SYMS[0]: {"tf": "1h", "mark": entry[SYMS[0]], "rows": [{"timestamp": bar_open - 6 * H1, "high": entry[SYMS[0]] * 3, "low": entry[SYMS[0]] * 2.5, "close": entry[SYMS[0]] * 2.8}]}}
    b2.ledger.positions[SYMS[0]].meta["ohlc_cursor"]["1h"] = bar_open - 7 * H1
    assert b2.apply_closed_bars(off, now=now) == [] and SYMS[0] in b2.ledger.positions
    assert b2.data_events[-1]["kind"] == "BAR_SKIPPED" and b2.data_events[-1]["reason"] == "BAR_OUT_OF_RANGE" and b2.ledger.positions[SYMS[0]].meta["ohlc_cursor"]["1h"] == bar_open - 6 * H1


# ====================================================================== 5) haftalarca eski günlük seri (yeni tour_id) → giriş yok; güncel seri → giriş, kural aynı
@pytest.mark.parametrize("stale_btc_only", [False, True])
def test_5_stale_daily_series_bound_to_a_new_tour_id_never_authorises_an_entry(tmp_path: Path, monkeypatch, stale_btc_only):
    eng = _eng(tmp_path / "e", monkeypatch)
    orig = eng.runner.run_symbol

    def old_daily(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        for s in ((BTC,) if stale_btc_only else (symbol, BTC)):
            fr = dict(eng.runner.last_frames[s]); d1 = fr["1d"].copy()
            d1["timestamp"] = d1["timestamp"] - 46 * DAY
            d1.index = d1.index - pd.Timedelta(days=46)
            fr["1d"] = d1; eng.runner.last_frames[s] = fr
        eng._bind_provenance(BTC)                                   # BTC bağı: yüklü (eski) çerçeveye, yeni tur kimliğiyle
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", old_daily)
    _tour(eng)
    assert _positions(eng) == {k: [] for k in BOOKS}
    reason = "DATA_BTC_FRAME_STALE_1D" if stale_btc_only else "DATA_FRAME_STALE_1D"
    for key, b in _books(eng).items():
        assert b.rejections == {reason: len(SYMS)} and b.counters["data_rejected"] == len(SYMS) and b.counters["opened"] == 0
        for s in SYMS:
            c = b.data_checks[s]
            assert c["reason"] == reason and c["tour_id"] == eng.run_id and c["as_of_ms"] == eng._tour_now_ms
            d = c["btc"]["detail"]["1d"] if stale_btc_only else c["detail"]["1d"]
            assert d["age_s"] > 40 * 86_400 and d["used_open_ms"] < d["expected_open_ms"] and d["tolerance_ms"] == BAR_LAG_TOLERANCE_MS["1d"]
            assert b.last_actions[s]["action"] == "DATA_REJECTED" and b.last_actions[s]["stage"] == ("ENTRY" if stale_btc_only else "SIGNAL")
            if stale_btc_only:
                assert c["ok"] is True and c["entry_ok"] is False, "sembol verisi güncel: kural kapanışı için yeterli, yeni giriş için BTC de güncel olmalı"
            else:
                assert c["ok"] is False
        ev = [e for e in b.data_events if e["kind"] == "REJECT"]
        assert len(ev) == len(SYMS) and all(e["would_act"] == "OPEN" for e in ev), "eski ama 'doğru' veriyle kural OPEN derdi; UYGULANMADI"
        assert _summary(eng, key)["data_checks"][SYMS[0]]["reason"] == reason
    # aynı motor, güncel kapalı seri → veri kapısı geçer, kural değişmeden giriş açar; kayda yazılan bar kuralın okuduğu bar
    monkeypatch.setattr(eng.runner, "run_symbol", orig)
    eng.runner.last_frames.pop(BTC, None)                          # BTC çerçevesi harness'ta yeniden (güncel) kurulur
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    for b in _books(eng).values():
        assert b.rejections == {reason: len(SYMS)} and b.counters["opened"] == len(SYMS)
        for s in SYMS:
            used = closed_bars(daily_rows_from_frame(eng.runner.last_frames[s]["1d"]), now_ms=eng._tour_now_ms, tf="1d")[-1]["timestamp"]
            ds = b.ledger.positions[s].features["data_source"]
            assert ds["bars"]["1d"] == used == b.data_checks[s]["bars"]["1d"], "data_source.bars == kuralın kullandığı son kapanmış bar"
            assert ds["bars"]["1d"] + DAY <= eng._tour_now_ms, "kapanmamış/gelecek bar sinyalin parçası değil"
            row = json.loads((eng.cfg.state_path / b.key / "trade_memory.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            assert row["features"]["signal_ts"] == used


# ====================================================================== 6) saf sözleşme: gece yarısı sınırı, kapanmamış son bar, gelecek damgası, tolerans
def test_6_daily_freshness_contract_is_exact_at_the_utc_close_boundary_and_records_the_used_bar():
    day0 = (int(time.time() * 1000) // DAY) * DAY                    # bugünün UTC 00:00'ı
    tol = BAR_LAG_TOLERANCE_MS["1d"]

    def frame(last_open, n=260):
        ts = [last_open - (n - 1 - i) * DAY for i in range(n)]
        return pd.DataFrame({"timestamp": ts, "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n})

    prov = lambda fr: {"market": "USDM_PERP", "source": "t", "entry_ok": True, "tour_id": "R", "frames": {"1d": {"last_ts": int(fr["timestamp"].iloc[-1]), "n": len(fr)}}}  # noqa: E731
    # dünkü bar (açılış day0-1g, kapanış day0) — bugün her saatte güncel; kayıt = o bar
    fr = frame(day0 - DAY)
    for as_of in (day0, day0 + 1, day0 + tol, day0 + 12 * 3_600_000, day0 + DAY - 1):
        v = verify_paper_data(symbol="X", frames={"1d": fr}, provenance=prov(fr), run_id="R", need_btc=False, as_of_ms=as_of)
        assert v.ok and v.bars["1d"] == day0 - DAY and v.detail["1d"]["unclosed_last"] is False, as_of
    # gece yarısından 1 ms önce: bugünkü bar henüz KAPANMAMIŞ → dünkü bar beklenir ve kullanılır; kapanmamış son satır dışlanır
    fr2 = frame(day0)                                                # son satır bugünün barı (kapanış day0+1g)
    v = verify_paper_data(symbol="X", frames={"1d": fr2}, provenance=prov(fr2), run_id="R", need_btc=False, as_of_ms=day0 + 6 * 3_600_000)
    assert v.ok and v.bars["1d"] == day0 - DAY and v.detail["1d"]["unclosed_last"] is True, "kapanmamış son bar sinyalin/kaydın parçası değil"
    # gece yarısından hemen sonra, sağlayıcı yeni barı henüz vermemiş: dünkü bar tolerans içinde güncel; tolerans bitince BAYAT
    fr3 = frame(day0 - 2 * DAY)                                      # son bar evvelsi gün (kapanış day0-1g)
    ok_edge = verify_paper_data(symbol="X", frames={"1d": fr3}, provenance=prov(fr3), run_id="R", need_btc=False, as_of_ms=day0 + tol - 1)
    stale_edge = verify_paper_data(symbol="X", frames={"1d": fr3}, provenance=prov(fr3), run_id="R", need_btc=False, as_of_ms=day0 + tol)
    assert ok_edge.ok and ok_edge.bars["1d"] == day0 - 2 * DAY
    assert not stale_edge.ok and stale_edge.reason == "DATA_FRAME_STALE_1D" and stale_edge.bars == {"1d": day0 - 2 * DAY} and stale_edge.detail["1d"]["expected_open_ms"] == day0 - DAY
    # dünden önceki gün için de aynı: as_of = day0 - 1 ms (dün 23:59:59.999) → evvelsi günün barı güncel
    assert verify_paper_data(symbol="X", frames={"1d": fr3}, provenance=prov(fr3), run_id="R", need_btc=False, as_of_ms=day0 - 1).ok
    # gelecek zaman damgası (saat sorunu) → ret; as_of yok → ret; hiç kapanmış bar yok → eksik
    fr4 = frame(day0 + 2 * DAY)
    assert verify_paper_data(symbol="X", frames={"1d": fr4}, provenance=prov(fr4), run_id="R", need_btc=False, as_of_ms=day0 + 1000).reason == "DATA_FRAME_FUTURE_1D"
    assert verify_paper_data(symbol="X", frames={"1d": fr}, provenance=prov(fr), run_id="R", need_btc=False).reason == "DATA_AS_OF_MISSING"
    fr5 = frame(day0, n=1)
    assert verify_paper_data(symbol="X", frames={"1d": fr5}, provenance=prov(fr5), run_id="R", need_btc=False, as_of_ms=day0 + 1000).reason == "DATA_FRAME_MISSING_1D"
    assert frame_freshness(fr, "1d", None)[0] == "AS_OF_MISSING" and frame_freshness(None, "1d", day0)[0] == "FRAME_MISSING_1D"
    # BTC referansı aynı sözleşmeyle; açık pozisyonun kapanışında (need_btc=False) BTC bayatlığı sembolü engellemez
    old_btc = frame(day0 - 30 * DAY)
    v = verify_paper_data(symbol="X", frames={"1d": fr}, provenance=prov(fr), run_id="R", btc_frames={"1d": old_btc}, btc_provenance=prov(old_btc), need_btc=True, as_of_ms=day0 + 1000)
    assert v.ok and not v.entry_ok and v.reason == "DATA_BTC_FRAME_STALE_1D" and v.btc["detail"]["1d"]["age_s"] > 25 * 86_400
    assert verify_paper_data(symbol="X", frames={"1d": fr}, provenance=prov(fr), run_id="R", btc_frames={"1d": old_btc}, btc_provenance=prov(old_btc), need_btc=False, as_of_ms=day0 + 1000).entry_ok
    d = DataVerdict(ok=True, entry_ok=True, as_of_ms=5, detail={"1d": {"x": 1}}).to_dict()
    assert d["as_of_ms"] == 5 and d["detail"] == {"1d": {"x": 1}}


# ====================================================================== 7) önceki DATA_* engelleri korunur; bayat mum yeni girişi engellerken açık pozisyon güncel fiyatla korunur
def test_7_previous_data_blocks_stay_and_open_positions_keep_price_protection_under_stale_candles(tmp_path: Path, monkeypatch):
    spot = _eng(tmp_path / "spot", monkeypatch, market=None, btc_market=None)
    _tour(spot)
    assert _positions(spot) == {k: [] for k in BOOKS} and all(b.rejections == {"DATA_MARKET_SPOT": len(SYMS)} for b in _books(spot).values())
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)                                                       # güncel veriyle pozisyonlar açık
    t2 = _t2(eng)
    stop = {s: _stop(t2, s) for s in SYMS}
    orig = eng.runner.run_symbol

    def old_daily(symbol, analysis=None, prefetched=None):          # sonraki turlarda günlük seri bayat (yeni giriş yok, kural CLOSE yok)
        b = orig(symbol, analysis, prefetched)
        fr = dict(eng.runner.last_frames[symbol]); d1 = fr["1d"].copy()
        d1["timestamp"] = d1["timestamp"] - 20 * DAY; d1.index = d1.index - pd.Timedelta(days=20)
        d1["ema200"] = d1["close"] * 1.05                            # bayat veriyle kural CLOSE derdi → UYGULANMAZ
        fr["1d"] = d1; eng.runner.last_frames[symbol] = fr
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", old_daily)
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    for b in _books(eng).values():
        assert b.rejections == {"DATA_FRAME_STALE_1D": len(SYMS)}
    assert all(e["would_act"] == "CLOSE" for e in _t2(eng).data_events if e["kind"] == "REJECT"), "T2 bayat veriyle CLOSE derdi; UYGULANMADI (M2 kuralı 28g momentum: ayrı)"
    # bu arada güncel doğrulanmış perp mark stop altına iner → izleyici korur (mum bayatlığı fiyat korumasını durdurmaz)
    _patch_snapshot(eng, monkeypatch, _mark(lambda s: stop[s] * 0.99, age_s=3, funding_ts=True))
    eng._strategy_paper_exit_check()
    assert _positions(eng) == {k: [] for k in BOOKS} and all(b.counters["closed"] == len(SYMS) for b in _books(eng).values())
    # provenans yok → önceki engel
    monkeypatch.setattr(eng.runner, "run_symbol", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    _tour(eng)
    assert all(b.rejections.get("DATA_PROVENANCE_MISSING") == len(SYMS) for b in _books(eng).values()) and _positions(eng) == {k: [] for k in BOOKS}


# ====================================================================== 8) replay: as_of = simülasyon anı; eski tarih tek başına ret değil; arşiv boşluğu ret
def test_8_replay_freshness_uses_the_simulated_decision_time_not_the_wall_clock(tmp_path: Path):
    from test_replay_strategy_mode_v1 import H4, SYM, _marks, _rep

    def strat(sym, t, fr, pos, rp):
        if pos is None:
            return {"action": "OPEN", "direction": "LONG", "stop": 1850.0, "targets": [], "leverage": 1, "name": "T", "reason": "probe"}
        return {"action": "CLOSE", "reason": "PROBE_EXIT"}

    def daily(last_open, n=260, px=2000.0):
        ts = [last_open - (n - 1 - i) * DAY for i in range(n)]
        return pd.DataFrame({"timestamp": ts, "open": [px] * n, "high": [px * 1.01] * n, "low": [px * 0.99] * n, "close": [px] * n, "volume": [1.0] * n})

    rep = _rep(tmp_path / "fut", strat)                                 # 2023-11 tarihli 4h arşivi: eski tarih tek başına ret DEĞİL
    t = int(rep.frames[SYM]["4h"]["timestamp"].iloc[-1])
    as_of = t + H4
    day_last = (as_of // DAY) * DAY - DAY                               # simülasyon anında kapanmış son günlük bar
    rep.frames[SYM]["1d"] = daily(day_last + DAY)                       # seri bugünün (simülasyon günü) KAPANMAMIŞ barını da içerir
    now = datetime.fromtimestamp(as_of / 1000, tz=timezone.utc)
    rep._strategy_step(t, now, *_marks(2000.0))
    assert SYM in rep.ledger2.positions and rep.result.n_opened == 1
    ds = rep.ledger2.positions[SYM].features["data_source"]
    assert ds["bars"] == {"4h": t, "1d": day_last} and ds["market"] == "USDM_PERP", "kapanmamış günlük bar kayda girmez; kullanılan bar simülasyon anına göre"
    v = rep._paper_data_verdict(SYM, t, rep._slice(SYM, t))
    assert v.ok and v.as_of_ms == as_of and v.detail["1d"]["used_open_ms"] == day_last
    rep._strategy_step(t + H4, datetime.fromtimestamp((t + 2 * H4) / 1000, tz=timezone.utc), *_marks(2040.0))
    assert SYM not in rep.ledger2.positions and rep.result.trades[0]["exit_reason"] == "PROBE_EXIT"
    # arşivde günlük boşluk (son günlük bar 5 gün önce) → simülasyon anına göre BAYAT → giriş yok (duvar saati DEĞİL)
    hole = _rep(tmp_path / "hole", strat)
    hole.frames[SYM]["1d"] = daily(day_last - 5 * DAY)
    hole._strategy_step(t, now, *_marks(2000.0))
    assert SYM not in hole.ledger2.positions and hole.result.rejections["by_reason"].get("DATA_FRAME_STALE_1D") == 1
    # yalnız 4h dilimi (kural çerçevesi yok): önceki davranış aynen (test_replay_strategy_mode_v1 ile parite)
    bare = _rep(tmp_path / "bare", strat)
    bare._strategy_step(t, now, *_marks(2000.0))
    assert SYM in bare.ledger2.positions and bare.ledger2.positions[SYM].features["data_source"]["bars"] == {"4h": t}
    spotr = _rep(tmp_path / "spot", strat, market="spot")
    spotr.frames[SYM]["1d"] = daily(day_last + DAY)
    spotr._strategy_step(t, now, *_marks(2000.0))
    assert spotr.result.rejections["by_reason"].get("DATA_MARKET_SPOT") == 1, "önceki replay engelleri korunur"


# ====================================================================== 9) tur tick'i fiyat-yalnız; bağlı perp_mark KAYIT; TickData zamanı fiyatın kaynak zamanı (ISO)
def test_9_tour_price_ticks_carry_no_bar_extremes_and_timestamps_follow_one_contract(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    px = {s: round(float(eng._fake_live._frames[s]["4h"]["close"].iloc[-1]) * 1.001, 4) for s in SYMS}   # çerçeveyle tutarlı perp mark
    _patch_snapshot(eng, monkeypatch, _mark(lambda s: px[s], age_s=7, funding_ts=True))
    _tour(eng)
    t2 = _t2(eng)
    marks, marks_f, gaps = eng._paper_marks(list(SYMS), now=utc_now())
    assert gaps == {} and marks_f == px
    for s in SYMS:
        td = marks[s]
        assert isinstance(td, TickData) and td.high is None and td.low is None and td.mark == Decimal(str(px[s]))
        assert abs(parse_ts_ms(td.ts) - (time.time() - 7) * 1000) < 5000, "tick zamanı = fiyatın kaynak zamanı (ISO, UTC), yeni damga değil"
        pm = eng._frame_provenance[s]["perp_mark"]
        assert pm["price"] == px[s] and pm["fresh"] is True and pm["price_ts_ms"] and pm["fetched_at_ms"] and pm["age_s"] >= 6
    for s in SYMS:
        assert float(t2.ledger.positions[s].entry_avg) == pytest.approx(px[s], rel=2e-3), "giriş güncel doğrulanmış markla"
    doc = _summary(eng, "strategy_paper")
    assert doc["data_policy"]["bars"]["tf"] == "1h" and doc["data_policy"]["freshness"]["tolerance_ms"] == dict(BAR_LAG_TOLERANCE_MS)
    prov = json.loads((eng.cfg.state_path / "frame_provenance.json").read_text(encoding="utf-8"))["by_symbol"][SYMS[0]]
    assert prov["as_of_ms"] == eng._tour_now_ms and prov["perp_mark"]["fresh"] is True
    nb = StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters, run_id="", spec=book_specs(eng.cfg.v3)[0])
    assert nb.counters == {"opened": 2, "closed": 0, "rejected": 0, "tours": 1, "data_rejected": 0}, "sayaç sözlüğü değişmedi"

# -*- coding: utf-8 -*-
"""ÇALIŞMA ONARIMLARI V1 (2026-09-23) — OOM, ana botun giriş öncesi 1h uçları, Box zamanlaması, izleme kesintisi.

ÖLÇÜLEN KUSURLAR (üretim VPS'i 3b0ae8e, salt okunur ölçüm; trading2-deploy/oom-olcum-2026-09-23):
  * OOM: worker 2026-09-19'dan beri her turda 4 GiB cgroup sınırında öldürülüyordu (~250 kez). Tur, aday snapshot
    kaydında `EntrySnapshotStore.known_ids()` → `by_candidate()` ile 655 MB'lık sıcak dosyanın TAMAMINI ayrıştırıp
    memoya alıyordu (+1,86 GB kalıcı; `rotate()` ayrıca +2,5 GB geçici). Tüketiciler yalnız işleme BAĞLI adayları kullanır.
  * Ana bot: tur tiki son kapanmış 1h barın uçlarını taşıyordu; girişten önce açılmış bar (girişi içeren kısmi bar
    dahil) yeni pozisyonun stopunu tetikleyebiliyor, aynı bar birkaç turda yeniden uygulanıyordu.
  * Box: 5m mumları yalnız tur anında görülüyordu (tur 1414 sn); teyitler kaçıyordu.
  * Kesinti: defterler 09-20'den beri izlenmedi; yeniden başlamada 80+ saatlik bar birikimi geriye dönük uygulanırdı.

ETİKET: gerçek `EntrySnapshotStore`, `apply_closed_bars_to_ledger`, `StrategyBook`, `TradingEngineV3` ve `BoxTimer`
çalışır; fiyatlar SENTETİKTİR. Gerçek piyasa sonucu ya da kârlılık kanıtı DEĞİLDİR.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_box_theory_book_v15 import SYM as BOX_SYM, _book, _daily_frame, _m5_frame, _now_ms, _ov, _short_bars  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_entry_snapshot_rotation import _fill, _snap, _store  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers  # noqa: E402
from tradingbot.accounting import AmountType, MarketType, SizeSpec, SymbolFilters, TickData  # noqa: E402
from tradingbot.box_timer import M5_MS, BoxTimer  # noqa: E402
from tradingbot.strategy_paper import (BAR_LAG_TOLERANCE_MS, MONITORING_GAPS_FILE, BookSpec, StrategyBook,  # noqa: E402
                                       apply_closed_bars_to_ledger)
from tradingbot.structures import catalog as K  # noqa: E402

UTC = timezone.utc
H1 = 3_600_000


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# ================================================================== 1) OOM — aday snapshot deposu
def test_known_ids_streams_ids_without_building_the_full_snapshot_graph(tmp_path):
    st = _store(tmp_path, max_lines=1000)
    ids = _fill(st, 12)
    fresh = _store(tmp_path, max_lines=1000)               # yeni süreç: kimlik kümesi diskteki dosyadan kurulur
    assert fresh.known_ids() == set(ids)
    assert fresh._bc_cache is None, "ÖNCE: known_ids() bütün snapshot grafiğini by_candidate memosuna alıyordu (OOM)"


def test_by_candidate_only_keeps_just_the_requested_candidates_with_identical_content(tmp_path):
    st = _store(tmp_path, max_lines=1000)
    ids = _fill(st, 10)
    full = _store(tmp_path, max_lines=1000).by_candidate()
    reader = _store(tmp_path, max_lines=1000)
    got = reader.by_candidate(only={ids[2], ids[7], "yok"})
    assert set(got) == {ids[2], ids[7]}
    assert got[ids[2]] == full[ids[2]] and got[ids[7]] == full[ids[7]]
    assert reader._bc_cache is None, "seçili okuma bütün pencereyi memoya ALMAMALI"
    assert reader.by_candidate(only=set()) == {}


def test_stats_counts_snapshots_without_the_full_graph(tmp_path):
    st = _store(tmp_path, max_lines=1000)
    _fill(st, 7)
    st.link_trade(st.by_candidate(only={_snap(3)["candidate_id"]}).popitem()[0], "T3")
    reader = _store(tmp_path, max_lines=1000)
    s = reader.stats()
    assert s["snapshots"] == 7 and s["links"] == 1
    assert reader._bc_cache is None


def test_rotation_streams_and_leaves_exactly_the_same_hot_file_and_segment(tmp_path, monkeypatch):
    """Akışlı rotasyon, eski (bütün dosyayı belleğe alan) algoritmayla BİREBİR aynı sıcak dosyayı ve segmenti üretir."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    sa = _store(a, max_lines=5)
    _fill(sa, 12)
    (b / "entry_snapshot.jsonl").write_bytes((a / "entry_snapshot.jsonl").read_bytes())
    old_lines = _store(b, max_lines=5)._hot_lines()      # eski algoritmanın gördüğü satırlar
    expected_rest = old_lines[7:]
    rot = _store(a, max_lines=5)
    monkeypatch.setattr(type(rot), "_hot_lines", lambda self: (_ for _ in ()).throw(AssertionError("rotate belleğe aldı")))
    res = rot.rotate()
    monkeypatch.undo()
    assert res["health"] == "OK" and res["archived"] == 7 and res["trimmed"] == 7 and res["hot_lines"] == 5
    assert (a / "entry_snapshot.jsonl").read_text(encoding="utf-8") == "\n".join(expected_rest) + "\n"
    import hashlib
    seg = rot.archive.segments()[0]
    assert seg["block_sha256"] == hashlib.sha256(("\n".join(old_lines[:7]) + "\n").encode("utf-8")).hexdigest()
    assert len(rot.by_candidate(include_archive=True)) == 12, "kayıpsız: hiçbir snapshot kaybolmadı"


def test_the_engine_reads_only_linked_candidates_in_the_hot_loop(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY)
    store = eng.entry_snapshot_store
    assert store is not None
    ids = _fill(store, 6)
    store.link_trade(ids[4], "F9")
    seen: list = []
    orig = type(store).by_candidate

    def spy(self, **kw):
        seen.append(kw.get("only"))
        return orig(self, **kw)
    monkeypatch.setattr(type(store), "by_candidate", spy)
    eng._write_entry_eval(datetime.now(UTC))
    assert seen and all(o is not None for o in seen), "ÖNCE: _write_entry_eval bütün pencereyi by_candidate() ile okuyordu"
    assert set().union(*[set(o) for o in seen]) == {ids[4]}


# ================================================================== 2) ana bot — giriş öncesi 1h uçları
DAY = datetime(2026, 9, 21, tzinfo=UTC)
T_OPEN = DAY.replace(hour=10, minute=20)


def _filters(sym: str) -> SymbolFilters:
    return SymbolFilters(symbol=sym, market_type=MarketType.USDM_PERP, price_tick=D("0.01"), qty_step=D("0.001"),
                         min_qty=D("0.001"), min_notional=D("5"), max_leverage=20)


def _h1(rows: list[tuple[datetime, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"timestamp": _ms(t), "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
                         for t, o, h, lo, c in rows])


def _main(tmp_path, monkeypatch, *, started: datetime, opened: datetime = T_OPEN):
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY)
    sym = eng.cfg.coins[0]
    eng._started_ms = _ms(started)
    pos = eng.ledger2.open(sym, "LONG", 100, SizeSpec(50, AmountType.NOTIONAL, leverage=1), stop=95, targets=[130],
                           filters=_filters(sym), now=opened)
    assert pos is not None, eng.ledger2.last_reject_reason
    eng.run_id = "rid-main"
    eng._frame_provenance = {sym: {"market": "USDM_PERP", "source": "test", "tour_id": "rid-main"}}
    return eng, sym


def _apply_main(eng, sym, frame: pd.DataFrame, now: datetime):
    eng.runner.last_frames[sym] = {"1h": frame}
    marks = {sym: TickData(last=D("100"), mark=D("100"), ts=now.isoformat())}
    return apply_closed_bars_to_ledger(eng.ledger2, eng._main_closed_bars(marks, now), now=now)


def test_main_tour_marks_carry_only_the_current_price(tmp_path, monkeypatch):
    eng, sym = _main(tmp_path, monkeypatch, started=DAY)
    eng.runner.last_frames[sym] = {"1h": _h1([(DAY.replace(hour=9), 100, 101, 80, 100)])}
    brief = SimpleNamespace(symbol=sym, price=100.0, reports=[])
    tk = eng._marks([brief])[sym]
    assert tk.high is None and tk.low is None, "ÖNCE: son 1h barın uçları (low 80) her tur tikine ekleniyordu"


def test_pre_entry_and_partial_entry_bars_never_touch_a_new_main_position_and_each_bar_applies_once(tmp_path, monkeypatch):
    eng, sym = _main(tmp_path, monkeypatch, started=DAY)            # pozisyon BU süreçte açıldı (10:20)
    pre = (DAY.replace(hour=9), 100, 101, 80, 100)                   # girişten ÖNCE: low 80 < stop 95
    part = (DAY.replace(hour=10), 100, 101, 80, 100)                 # girişi İÇEREN kısmi bar: low 80 < stop 95
    assert _apply_main(eng, sym, _h1([pre]), DAY.replace(hour=10, minute=25)) == []
    assert _apply_main(eng, sym, _h1([pre, part]), DAY.replace(hour=11, minute=5)) == []
    assert sym in eng.ledger2.positions, "ÖNCE: girişten önceki/kısmi barın fitili yeni pozisyonu stop'luyordu"
    post = (DAY.replace(hour=11), 100, 101, 90, 96)                  # girişten SONRA açılmış bar: stop gerçek olay
    recs = _apply_main(eng, sym, _h1([pre, part, post]), DAY.replace(hour=12, minute=5))
    assert len(recs) == 1 and sym not in eng.ledger2.positions
    assert recs[0].exit_reason.upper().startswith("STOP")


def test_the_same_post_entry_bar_is_not_applied_twice(tmp_path, monkeypatch):
    eng, sym = _main(tmp_path, monkeypatch, started=DAY)
    post = (DAY.replace(hour=11), 100, 104, 99, 103)                 # stop/TP'ye değmeyen bar → MFE günceller
    _apply_main(eng, sym, _h1([post]), DAY.replace(hour=12, minute=5))
    cur = eng.ledger2.positions[sym].meta["ohlc_cursor"]["1h"]
    assert cur == _ms(DAY.replace(hour=11))
    mfe = eng.ledger2.positions[sym].mfe_pct
    _apply_main(eng, sym, _h1([post]), DAY.replace(hour=12, minute=40))
    assert eng.ledger2.positions[sym].meta["ohlc_cursor"]["1h"] == cur and eng.ledger2.positions[sym].mfe_pct == mfe


def test_a_legacy_position_is_migrated_without_replaying_history(tmp_path, monkeypatch):
    """Süreçten ÖNCE açılmış, imleçsiz pozisyon (eski yol): geçmiş barlar geriye dönük UYGULANMAZ, sonrakiler uygulanır."""
    eng, sym = _main(tmp_path, monkeypatch, started=DAY.replace(hour=15), opened=DAY.replace(hour=3))
    hist = [(DAY.replace(hour=h), 100, 101, 80, 100) for h in range(4, 14)]      # hepsi stopun altına iniyor
    assert _apply_main(eng, sym, _h1(hist), DAY.replace(hour=15, minute=5)) == []
    assert sym in eng.ledger2.positions
    assert eng.ledger2.positions[sym].meta["ohlc_cursor"]["1h"] == _ms(DAY.replace(hour=13))
    new = (DAY.replace(hour=14), 100, 101, 90, 96)
    assert len(_apply_main(eng, sym, _h1(hist + [new]), DAY.replace(hour=15, minute=10))) == 1


def test_bars_opened_before_a_structure_tightened_stop_do_not_hit_the_new_stop(tmp_path, monkeypatch):
    eng, sym = _main(tmp_path, monkeypatch, started=DAY)
    p = eng.ledger2.positions[sym]
    p.stop = D("98")
    p.meta["structure_stop"] = {"at": DAY.replace(hour=11, minute=30).isoformat(), "stop": 98}
    before = (DAY.replace(hour=11), 100, 101, 97, 100)               # sıkılaştırmadan önce açılmış: low 97 < yeni stop 98
    assert _apply_main(eng, sym, _h1([before]), DAY.replace(hour=12, minute=5)) == []
    after = (DAY.replace(hour=12), 100, 101, 97, 99)
    assert len(_apply_main(eng, sym, _h1([before, after]), DAY.replace(hour=13, minute=5))) == 1


# ================================================================== 3) izleme kesintisi
def _t2(eng) -> StrategyBook:
    spec = BookSpec(name="t2_trend_regime", state_dir="strategy_paper_gap", starting_equity_usdt=100.0)
    return StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters,
                        run_id="rid", spec=spec)


def _book_with_position(tmp_path, monkeypatch, *, last_saved: datetime, opened: datetime):
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY)
    sym = eng.cfg.coins[0]
    b = _t2(eng)
    assert b.ledger.open(sym, "LONG", 100, SizeSpec(50, AmountType.NOTIONAL, leverage=1), stop=95, targets=[130],
                         filters=_filters(sym), now=opened) is not None
    b.ledger.save(b.ledger_path)
    d = json.loads(b.ledger_path.read_text(encoding="utf-8"))
    d["updated_at"] = last_saved.isoformat()
    b.ledger_path.write_text(json.dumps(d), encoding="utf-8")
    return eng, sym, _t2(eng)                                        # YENİ süreç: defter diskten yüklenir


def _spec(sym, rows):
    return {sym: {"tf": "1h", "rows": [{"timestamp": _ms(t), "open": o, "high": h, "low": lo, "close": c}
                                       for t, o, h, lo, c in rows], "mark": 100.0, "market": "USDM_PERP"}}


def test_resume_after_an_outage_records_the_gap_and_does_not_backfill_trades(tmp_path, monkeypatch):
    last = DAY.replace(hour=4)
    eng, sym, book = _book_with_position(tmp_path, monkeypatch, last_saved=last, opened=DAY.replace(hour=2))
    resume = DAY + timedelta(days=3, hours=10, minutes=5)
    gap_bars = [(last + timedelta(hours=h), 100, 101, 80, 100) for h in range(1, 70)]   # kesintide stop'u delen barlar
    assert book.apply_closed_bars(_spec(sym, gap_bars), now=resume) == []
    assert sym in book.ledger.positions, "ÖNCE: 70 saatlik birikim geriye dönük uygulanıp pozisyon stop'la kapanırdı"
    rec = [json.loads(x) for x in (Path(eng.cfg.state_path) / MONITORING_GAPS_FILE).read_text(encoding="utf-8").splitlines()]
    assert len(rec) == 1 and rec[0]["book"] == book.key and rec[0]["positions"] == [sym]
    assert rec[0]["from"].startswith("2026-09-21T04:00") and book.monitoring_gap is not None
    nxt = (resume.replace(minute=0), 100, 101, 90, 96)                # kesintiden SONRA kapanan bar: gerçek olay
    recs = book.apply_closed_bars(_spec(sym, gap_bars + [nxt]), now=resume.replace(minute=0) + timedelta(hours=1, minutes=5))
    assert len(recs) == 1 and sym not in book.ledger.positions


def _gap_records(state_path) -> list[dict]:
    p = Path(state_path) / MONITORING_GAPS_FILE
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_the_outage_is_recorded_with_the_positions_held_at_load_even_if_the_first_step_closes_them(tmp_path, monkeypatch):
    # 2026-09-23 doğrulama koşusunda Box zamanlayıcısı `step` → `apply_closed_bars` sırasıyla çalıştı: 3,5 gün izlenmeyen
    # AVAX/ENA `step` içinde kapandı ve kesinti HİÇ kaydedilmedi (denetim yalnız ilk bar uygulamasındaydı).
    last = DAY.replace(hour=4)
    eng, sym, book = _book_with_position(tmp_path, monkeypatch, last_saved=last, opened=DAY.replace(hour=2))
    resume = DAY + timedelta(days=3, hours=10, minutes=5)
    stop = book.ledger.positions[sym].stop
    closed = book.tick({sym: TickData(last=stop * D("0.95"), mark=stop * D("0.95"), ts=resume.isoformat())}, now=resume,
                       bar_advance=False)                               # İLK etkinlik güncel fiyatla kapatır (gerçek olay)
    assert len(closed) == 1 and sym not in book.ledger.positions
    rec = _gap_records(eng.cfg.state_path)
    assert len(rec) == 1 and rec[0]["positions"] == [sym], "ÖNCE: kayıt yoktu (pozisyon ilk bar uygulamasından önce kapandı)"
    assert rec[0]["from"].startswith("2026-09-21T04:00") and rec[0]["to"].startswith(resume.isoformat()[:16])
    book.apply_closed_bars({}, now=resume + timedelta(minutes=1))
    book.step(symbols=[], frames_by_symbol={}, marks={}, marks_f={}, now=resume + timedelta(minutes=2))
    assert len(_gap_records(eng.cfg.state_path)) == 1, "kesinti süreç başına BİR kez yazılır"
    assert book.monitoring_gap["positions"] == [sym]


def test_the_first_step_triggers_the_outage_check_before_it_can_change_positions(tmp_path, monkeypatch):
    last = DAY.replace(hour=4)
    eng, sym, book = _book_with_position(tmp_path, monkeypatch, last_saved=last, opened=DAY.replace(hour=2))
    resume = DAY + timedelta(days=3, hours=10, minutes=5)
    book.step(symbols=[], frames_by_symbol={}, marks={}, marks_f={}, now=resume)       # Box zamanlayıcısının sırası
    rec = _gap_records(eng.cfg.state_path)
    assert len(rec) == 1 and rec[0]["positions"] == [sym] and rec[0]["policy"] == "bars_closed_in_gap_not_applied"


def test_an_outage_without_open_positions_is_still_recorded(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY)
    b = _t2(eng)
    b.ledger.save(b.ledger_path)
    d = json.loads(b.ledger_path.read_text(encoding="utf-8"))
    d["updated_at"] = DAY.replace(hour=4).isoformat()
    b.ledger_path.write_text(json.dumps(d), encoding="utf-8")
    book = _t2(eng)
    resume = DAY + timedelta(days=3, hours=10, minutes=5)
    book.step(symbols=[], frames_by_symbol={}, marks={}, marks_f={}, now=resume)
    rec = _gap_records(eng.cfg.state_path)
    assert len(rec) == 1 and rec[0]["positions"] == [], "kural bu aralıkta hiç değerlendirilmedi: ileri test boşluğu görünür"


def test_the_pattern_book_records_the_outage_with_its_load_time_positions(tmp_path):
    from test_pattern_trader_v1 import _book_obj, _cfg
    cfg = _cfg(tmp_path)
    sym = "SOL/USDT"
    b = _book_obj(cfg)
    assert b.ledger.open(sym, "LONG", 100, SizeSpec(50, AmountType.NOTIONAL, leverage=1), stop=95, targets=[130],
                         filters=_filters(sym), now=DAY.replace(hour=2)) is not None
    b.ledger.save(b.ledger_path)
    d = json.loads(b.ledger_path.read_text(encoding="utf-8"))
    d["updated_at"] = DAY.replace(hour=4).isoformat()
    b.ledger_path.write_text(json.dumps(d), encoding="utf-8")
    book = _book_obj(cfg)                                            # YENİ süreç
    resume = DAY + timedelta(days=3, hours=10, minutes=5)
    closed = book.tick({sym: TickData(last=D("90"), mark=D("90"), ts=resume.isoformat())}, now=resume)
    assert len(closed) == 1 and not book.ledger.positions            # ilk etkinlik (TIME_STOP/tik) kapattı
    rec = _gap_records(cfg.state_path)
    assert len(rec) == 1 and rec[0]["book"] == "pattern_trader" and rec[0]["positions"] == [sym]
    ev = [e for e in book.events if e["kind"] == "MONITORING_GAP"]
    assert len(ev) == 1 and ev[0]["positions"] == [sym]
    book.apply_closed_bars({}, now=resume + timedelta(minutes=1))
    assert len(_gap_records(cfg.state_path)) == 1


def test_a_short_restart_is_not_a_monitoring_gap(tmp_path, monkeypatch):
    now = DAY.replace(hour=12, minute=5)
    eng, sym, book = _book_with_position(tmp_path, monkeypatch, last_saved=now - timedelta(minutes=30),
                                         opened=DAY.replace(hour=9, minute=30))
    recs = book.apply_closed_bars(_spec(sym, [(DAY.replace(hour=11), 100, 101, 90, 96)]), now=now)
    assert len(recs) == 1, "olağan yeniden başlatmada barlar sözleşmeye göre uygulanır"
    assert not (Path(eng.cfg.state_path) / MONITORING_GAPS_FILE).exists()


# ================================================================== 4) Box zamanlayıcısı
class _Prov:
    """Ağsız sağlayıcı: BoxTimer'ın kullandığı yüzey (klines / premiumIndex / mark_price)."""
    prefix = "/fapi/v1"

    def __init__(self, d1: pd.DataFrame, m5: pd.DataFrame, mark: float, mark_ts: int):
        self.frames = {"1d": d1, "5m": m5, "1h": m5.iloc[0:0]}
        self.mark, self.mark_ts, self.calls = mark, mark_ts, []
        self.http = SimpleNamespace(get=self._get)

    def _get(self, path, params=None, weight=1):
        assert path.endswith("/premiumIndex")
        return [{"symbol": BOX_SYM.replace("/", ""), "markPrice": str(self.mark), "time": self.mark_ts}]

    def mark_price(self, sym):
        return {"mark": self.mark, "ts": self.mark_ts}

    def klines(self, sym, tf, limit=500, start_ms=None, end_ms=None):
        self.calls.append((sym, tf))
        return self.frames[tf].copy()


def _timer(eng, book, prov, now_ms):
    return BoxTimer(book=book, provider_factory=lambda: prov, state_path=eng.cfg.state_path,
                    symbols_fn=lambda: [BOX_SYM], clock_ms=lambda: now_ms)


def test_the_box_timer_evaluates_each_closed_five_minute_bar_once_and_on_time(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()                                               # son 5m kapanışının 30 sn sonrası
    prov = _Prov(_daily_frame(now_ms, 110.0, 90.0), _m5_frame(now_ms, _short_bars()), 107.0, now_ms - 1_000)
    book = _book(eng)
    t = _timer(eng, book, prov, now_ms)
    r1 = t.run_once(now_ms)
    assert BOX_SYM in book.ledger.positions and book.ledger.positions[BOX_SYM].side.value == "SHORT"
    assert r1["lag_s"] == 30.0 and t.evaluations == 1
    n_calls = len(prov.calls)
    assert t.run_once(now_ms + 10_000).get("idle") and t.evaluations == 1, "aynı mum ikinci kez değerlendirilmez"
    assert len(prov.calls) == n_calls
    status = json.loads((Path(eng.cfg.state_path) / "box_timer.json").read_text(encoding="utf-8"))
    assert status["last_bar_open"] == ((now_ms - 3_000) // M5_MS) * M5_MS - M5_MS and status["lags_s"] == [30.0]


def test_the_box_timer_never_opens_twice_from_the_same_signal_bar(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    prov = _Prov(_daily_frame(now_ms, 110.0, 90.0), _m5_frame(now_ms, _short_bars()), 107.0, now_ms - 1_000)
    book = _book(eng)
    _timer(eng, book, prov, now_ms).run_once(now_ms)
    pos = book.ledger.positions[BOX_SYM]
    now = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    book.tick({BOX_SYM: TickData(last=pos.stop * D("1.01"), mark=pos.stop * D("1.01"), ts=now.isoformat())}, now=now, bar_advance=False)
    assert BOX_SYM not in book.ledger.positions, "önkoşul: pozisyon aynı mum içinde stop'la kapandı"
    (Path(eng.cfg.state_path) / "box_timer.json").unlink()           # yeniden başlatma: zamanlayıcı durumu yok
    t2 = _timer(eng, book, prov, now_ms)
    t2.run_once(now_ms)
    assert BOX_SYM not in book.ledger.positions, "aynı sinyal mumundan İKİNCİ giriş açılmamalı"
    assert t2.duplicates_blocked == 1


def test_the_tour_leaves_the_box_book_to_the_timer_but_keeps_it_in_the_index(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    box = next(b for b in eng.strategy_books if b.name == "b1_box_fade")
    other = [b for b in eng.strategy_books if b is not box]
    eng.box_timer = SimpleNamespace(book=box, alive=True)
    called: list[str] = []
    monkeypatch.setattr(box, "step", lambda **kw: (_ for _ in ()).throw(AssertionError("Box turda değerlendirildi")))
    for b in other:
        monkeypatch.setattr(b, "step", lambda _n=b.name, **kw: called.append(_n))
    eng.run_id = "rid"
    eng._strategy_paper_tour([eng.cfg.coins[0]], {}, {}, {}, False, datetime.now(UTC))
    assert called == [b.name for b in other]
    idx = json.loads((Path(eng.cfg.state_path) / "strategy_paper_index.json").read_text(encoding="utf-8"))
    assert {"key": box.key, "name": box.name, "summary_file": box.summary_file, "evaluated_by": "box_timer"} in idx["books"]
    eng.box_timer = SimpleNamespace(book=box, alive=False)           # zamanlayıcı ölürse Box tur yoluna döner
    assert eng._box_timer_owns(box) is False


def test_the_five_minute_freshness_thresholds_were_not_relaxed():
    assert BAR_LAG_TOLERANCE_MS["5m"] == 900_000
    assert K.DEFAULT_CONFIG.fresh_bars == 2

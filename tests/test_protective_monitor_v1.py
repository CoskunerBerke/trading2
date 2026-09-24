# -*- coding: utf-8 -*-
"""KORUYUCU İZLEYİCİ V1 (2026-09-24) — ana botun kesinti politikası + beş defterin turdan bağımsız koruyucu izlemesi.

ÖLÇÜLEN KUSURLAR (2026-09-23 VPS kopyası, d8b0c8c raporu):
  * Ana bot kesintiyi geçmiş 1m mumlarla (GapReconciler) ve kesinti aralığında kapanmış 1h barlarla dolduruyor, canlı
    PAPER defterine GEÇMİŞ zamanlı kapanış yazabiliyordu.
  * `watch` tek iş parçacıklıdır: 60 sn'lik çıkış kontrolü yalnız turlar arasında koşar; ağır turda T2/M2 ~370 sn,
    formasyon defteri 527 sn izlenmedi; bloke turda hiç izlenmez.
  * Ana defterin 60 sn izleyicisi spot ticker `last` ile futures pozisyonunu değerlendiriyordu.

ETİKET: gerçek `TradingEngineV3`, `StrategyBook` (T2/M2/Box), `PatternBook`, `FuturesLedgerV2`, `ProtectiveMonitor`
çalışır; fiyatlar SENTETİKTİR. Saat enjekte edilir, eşzamanlılık `threading.Event` ile sürülür (uyku süresi yok).
Gerçek piyasa sonucu ya da kârlılık kanıtı DEĞİLDİR.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_coinhead as T  # noqa: E402
from test_engine_v3 import _UNIVERSE, _engine  # noqa: E402
from test_funding_five_ledgers_v2 import SynthFundingProvider, _ov as _five  # noqa: E402
from test_risk_capacity_and_gates import EQUITY  # noqa: E402
from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, TickData  # noqa: E402
from tradingbot.core import from_iso, iso, utc_now  # noqa: E402
from tradingbot.ops.gap import SIMULATION_LABEL, read_watermark, simulate_outage, write_watermark  # noqa: E402
from tradingbot.protective_monitor import FIRST_OBS_FILE, guarded_tick, perp_marks  # noqa: E402
from tradingbot.strategy_paper import PRICE_MAX_AGE_S  # noqa: E402
from tradingbot.strategy_paper import MONITORING_GAPS_FILE  # noqa: E402

UTC = timezone.utc
H1 = 3_600_000
SYM = "ETH/USDT"
QTY = SizeSpec(D("0.1"), AmountType.QUANTITY, 1)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _tick(px: float, at: datetime) -> TickData:
    return TickData(last=D(str(px)), mark=D(str(px)), ts=iso(at))


class SpyProvider(SynthFundingProvider):
    """Kline isteği SAYILIR: yeni politika kesinti için geçmiş mum İSTEMEZ. İstenirse stop'u delen 1m mumlar verir."""

    def __init__(self, low: float = 80.0):
        super().__init__()
        self.low, self.kline_calls = low, 0
        self.max_kline_limit = 1000

    def klines(self, symbol, tf, limit=1000, start_ms=None, end_ms=None, **_):
        self.kline_calls += 1
        step = 60_000
        start = int(start_ms or 0) - int(start_ms or 0) % step
        rows = [{"timestamp": t, "open": 100.0, "high": 100.5, "low": self.low, "close": 99.0, "close_time": t + step - 1}
                for t in range(start, min(int(end_ms), start + limit * step), step) if t + step - 1 <= int(end_ms)]
        return pd.DataFrame(rows)


# ====================================================================== 1) ANA BOT — kesinti politikası
def _seed_main(*, entry: float, stop: float, opened: datetime, last_saved: datetime, cursor_ms: int | None = None):
    """Önceki süreç: ana defterde açık LONG + son koruyucu gözlem `last_saved` (defter + watermark)."""
    def build(cfg):
        led = FuturesLedgerV2(D(str(EQUITY)))
        pos = led.open(SYM, "LONG", D(str(entry)), QTY, stop=D(str(stop)), targets=[D(str(entry * 1.5))], now=opened)
        assert pos is not None, led.last_reject_reason
        if cursor_ms is not None:
            pos.meta["ohlc_cursor"] = {"1h": int(cursor_ms)}
        led.save(cfg.state_path / "futures_ledger.json")
        p = cfg.state_path / "futures_ledger.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["updated_at"] = iso(last_saved)
        p.write_text(json.dumps(d), encoding="utf-8")
        write_watermark(cfg.state_path, last_saved, "previous-process")
    return build


def _fixed_marks(price: float | None, *, at: datetime | None = None):
    """`_paper_marks` yerine: doğrulanmış perp fiyatı (ya da yokluğu). Zaman: gözlem anı."""
    def fn(syms, now=None):
        t = at or now or utc_now()
        if price is None:
            return {}, {}, {s: {"reason": "NO_VERIFIED_FUTURES_PRICE", "detail": "feed down"} for s in syms}
        return {s: _tick(price, t) for s in syms}, {s: float(price) for s in syms}, {}
    return fn


def test_main_first_activity_after_outage_records_the_gap_and_observes_the_current_price_without_backfill(tmp_path, monkeypatch):
    now = utc_now()
    last_saved = now - timedelta(days=3, hours=2)
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY,
                  before_build=_seed_main(entry=100.0, stop=95.0, opened=last_saved - timedelta(hours=5), last_saved=last_saved))
    spy = SpyProvider(low=80.0)                                       # kesintide stop'u delen geçmiş mumlar HAZIR
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: spy)
    wallet0, hist0 = eng.ledger2.wallet_balance, len(eng.ledger2.history)
    pid = eng.ledger2.positions[SYM].id
    monkeypatch.setattr(eng, "_paper_marks", _fixed_marks(99.0))
    assert eng.exit_check() == []                                     # İLK etkinlik
    assert spy.kline_calls == 0, "ÖNCE: GapReconciler kesintiyi geçmiş 1m mumlarla oynatıyordu"
    assert SYM in eng.ledger2.positions and eng.ledger2.positions[SYM].id == pid
    assert len(eng.ledger2.history) == hist0 and eng.ledger2.wallet_balance == wallet0, "geçmiş/bakiye SIFIRLANMADI, doldurulmadı"
    gaps = [r for r in _jsonl(eng.cfg.state_path / MONITORING_GAPS_FILE) if r.get("book") == "main"]
    assert len(gaps) == 1 and gaps[0]["positions"] == [SYM] and from_iso(gaps[0]["from"]) == last_saved.replace(microsecond=0)
    assert gaps[0]["policy"] == "bars_closed_in_gap_not_applied" and gaps[0]["simulation_input"]
    obs = [r for r in _jsonl(eng.cfg.state_path / FIRST_OBS_FILE) if r["book"] == "main"]
    assert len(obs) == 1 and obs[0]["position_id"] == pid and obs[0]["price"] == 99.0
    assert abs((from_iso(obs[0]["observed_at"]) - now).total_seconds()) < 30, "gözlemin GERÇEK zamanı kaydedildi"
    assert eng.ledger2.positions[SYM].last_price == D("99.0")
    assert read_watermark(eng.cfg.state_path) > last_saved
    eng.exit_check()                                                  # ikinci etkinlik: kesinti yeniden kaydedilmez
    assert len([r for r in _jsonl(eng.cfg.state_path / MONITORING_GAPS_FILE) if r.get("book") == "main"]) == 1


def test_main_outage_price_beyond_stop_closes_at_the_observed_price_and_time_not_in_the_past(tmp_path, monkeypatch):
    now = utc_now()
    last_saved = now - timedelta(days=2)
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY,
                  before_build=_seed_main(entry=100.0, stop=95.0, opened=last_saved - timedelta(hours=5), last_saved=last_saved))
    spy = SpyProvider(low=80.0)
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: spy)
    monkeypatch.setattr(eng, "_paper_marks", _fixed_marks(90.0))
    out = eng.exit_check()
    assert [c["symbol"] for c in out] == [SYM] and SYM not in eng.ledger2.positions
    rec = eng.ledger2.history[-1]
    closed_at = from_iso(rec.closed_at)
    assert closed_at >= now - timedelta(seconds=5), "ÖNCE: kapanış kesinti içindeki geçmiş 1m barın zamanıyla yazılıyordu"
    assert float(rec.exit_price) < 95.0, "dolum gözlenen güncel fiyattan (stop seviyesi değil: fiyat stopun ötesinde gözlendi)"
    assert rec.features["exit_fill"]["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION"
    assert spy.kline_calls == 0
    obs = [r for r in _jsonl(eng.cfg.state_path / FIRST_OBS_FILE) if r["book"] == "main"]
    assert obs and obs[0]["closed_by_this_observation"] is True


def test_main_outage_without_any_verified_price_fabricates_no_price_and_no_fill(tmp_path, monkeypatch):
    now = utc_now()
    last_saved = now - timedelta(days=1)
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY,
                  before_build=_seed_main(entry=100.0, stop=95.0, opened=last_saved - timedelta(hours=5), last_saved=last_saved))
    spy = SpyProvider(low=80.0)
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: spy)
    monkeypatch.setattr(eng, "_paper_marks", _fixed_marks(None))
    last0 = eng.ledger2.positions[SYM].last_price
    assert eng.exit_check() == []
    assert SYM in eng.ledger2.positions and eng.ledger2.positions[SYM].last_price == last0 and not eng.ledger2.history
    assert spy.kline_calls == 0 and not eng._gap_blocked
    assert eng._main_price_gaps[SYM]["reason"] == "NO_VERIFIED_FUTURES_PRICE"
    assert read_watermark(eng.cfg.state_path) == last_saved.replace(microsecond=0), "gözlem yoksa izleme damgası ilerlemez"
    assert not _jsonl(eng.cfg.state_path / FIRST_OBS_FILE), "ilk gözlem henüz yok: uydurulmadı"
    assert [r["book"] for r in _jsonl(eng.cfg.state_path / MONITORING_GAPS_FILE)] == ["main"]


def test_a_process_that_kept_running_without_positions_is_not_reported_as_an_outage(tmp_path, monkeypatch):
    """Eski izleme damgası (son pozisyonlu gözlem 3 gün önce) + taze defter kaydı (tur her 15 dk kaydetti): süreç
    CANLIYDI → yeniden başlatmada kesinti KAYDEDİLMEZ (kesinti başlangıcı iki izin SONRAKİSİDİR)."""
    def build(cfg):
        led = FuturesLedgerV2(D(str(EQUITY)))
        led.save(cfg.state_path / "futures_ledger.json")              # updated_at = şimdi
        write_watermark(cfg.state_path, utc_now() - timedelta(days=3), "old")
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY, before_build=build)
    eng.exit_check()
    assert not [r for r in _jsonl(eng.cfg.state_path / MONITORING_GAPS_FILE) if r.get("book") == "main"]


def _crafted_1h(px: float, *, n: int, low_from: int, low: float) -> pd.DataFrame:
    """Şimdiye hizalı 1h çerçevesi: son bar bir saat önce kapandı; `low_from`dan itibaren barların fitili `low`a iner."""
    now_ms = _ms(utc_now())
    last_open = now_ms - now_ms % H1 - 2 * H1
    rows = [{"timestamp": last_open - (n - 1 - i) * H1, "open": px, "high": px * 1.002, "low": (low if i >= low_from else px * 0.998),
             "close": px, "volume": 10.0} for i in range(n)]
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return T.ind.add_snapshot_indicators(df)


def test_main_tour_after_an_outage_does_not_apply_the_gap_bars_to_the_live_ledger(tmp_path, monkeypatch):
    """Tur yolu (DAVRANIŞ): kesinti aralığında kapanmış 1h barlar stop'u delse de canlı deftere UYGULANMAZ; imleç geçer."""
    seed, drift = next((sd, dr) for s, sd, dr in _UNIVERSE if s == SYM)
    px = float(T.frames(seed=seed, drift=drift)["4h"]["close"].iloc[-1]) * 1.0002    # sahte canlı perp mark ile aynı seviye
    now = utc_now()
    last_saved = now - timedelta(hours=40)
    cursor = _ms(last_saved) - _ms(last_saved) % H1 - H1                  # önceki süreçte uygulanmış son bar
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY,
                  before_build=_seed_main(entry=px, stop=px * 0.9, opened=last_saved - timedelta(hours=10), last_saved=last_saved,
                                          cursor_ms=cursor))
    eng._fake_live._frames[SYM]["1h"] = _crafted_1h(px, n=60, low_from=30, low=px * 0.85)   # kesintideki barlar stop'u deler
    run = eng.runner.run_symbol

    def run_perp(symbol, analysis=None, prefetched=None):          # çerçeve USDⓈ-M perp kaynaklı (provenans)
        eng._frame_provenance[symbol] = {"market": "USDM_PERP", "source": "test", "entry_ok": True}
        return run(symbol, analysis, prefetched)
    monkeypatch.setattr(eng.runner, "run_symbol", run_perp)
    pid = eng.ledger2.positions[SYM].id
    eng.tour(do_scan=False, obsidian=False, charts=False)
    closed_hist = [h for h in eng.ledger2.history_dicts() if h["id"] == pid]
    assert not closed_hist, "ÖNCE: kesintide kapanmış bar pozisyonu geçmiş zamanlı stop ile kapatıyordu: %s" % closed_hist
    assert SYM in eng.ledger2.positions and eng.ledger2.positions[SYM].id == pid
    assert eng.ledger2.positions[SYM].meta["ohlc_cursor"]["1h"] > cursor, "kesinti barları tüketildi (bir daha denenmez)"
    assert [r["book"] for r in _jsonl(eng.cfg.state_path / MONITORING_GAPS_FILE)].count("main") == 1


def test_outage_history_reconciliation_is_a_labelled_simulation_that_never_touches_the_ledger(tmp_path, monkeypatch):
    now = utc_now()
    last_saved = now - timedelta(hours=6)
    eng = _engine(tmp_path, monkeypatch, symbols=1, equity=EQUITY,
                  before_build=_seed_main(entry=100.0, stop=95.0, opened=last_saved - timedelta(hours=5), last_saved=last_saved))
    monkeypatch.setattr(eng, "_paper_marks", _fixed_marks(99.0))
    eng.exit_check()
    gap = [r for r in _jsonl(eng.cfg.state_path / MONITORING_GAPS_FILE) if r["book"] == "main"][0]
    snap = FuturesLedgerV2.from_dict(json.loads((eng.cfg.state_path / gap["simulation_input"]).read_text(encoding="utf-8")))
    live_before = (eng.ledger_path.read_bytes(), read_watermark(eng.cfg.state_path))
    doc = simulate_outage(snap, start=from_iso(gap["from"]), end=from_iso(gap["to"]), provider_factory=lambda: SpyProvider(low=80.0),
                          state_dir=eng.cfg.state_path, book_key="main")
    assert doc["label"] == SIMULATION_LABEL and doc["applied_to_ledger"] is False
    assert len(doc["simulated_closes"]) == 1 and doc["simulated_closes"][0]["exit_reason"] == "stop"
    assert (eng.ledger_path.read_bytes(), read_watermark(eng.cfg.state_path)) == live_before, "canlı defter/damga DEĞİŞMEDİ"
    assert SYM in eng.ledger2.positions and not eng.ledger2.history
    assert Path(doc["path"]).exists() and "SİMÜLASYON" in json.loads(Path(doc["path"]).read_text(encoding="utf-8"))["note_tr"]


# ====================================================================== 2) BEŞ DEFTER — turdan bağımsız izleme
class Clock:
    def __init__(self, start: datetime):
        self.ms = _ms(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, s: float) -> None:
        self.ms += int(s * 1000)


def _five_books(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _five(), symbols=2, equity=EQUITY)
    monkeypatch.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
    monkeypatch.setattr("tradingbot.box_timer.BoxTimer.start", lambda self: None)
    monkeypatch.setattr(eng, "_gap_provider_factory", lambda: SynthFundingProvider())
    eng.ensure_pattern_scanner()
    books = {"main": eng.ledger2}
    books.update({b.key: b.ledger for b in eng.strategy_books})
    books[eng.pattern_book.key] = eng.pattern_book.ledger
    assert set(books) == {"main", "strategy_paper", "strategy_paper_m2", "strategy_paper_box", "pattern_trader"}
    opened = utc_now() - timedelta(hours=1)
    for key, led in books.items():
        assert led.open(SYM, "LONG", D("100"), QTY, stop=D("95"), targets=[D("130")], now=opened) is not None, key
    return eng, books


def _price_fn(prices: dict[str, float] | float | None, calls: list | None = None, hook=None):
    def fn(syms, now_ms):
        if calls is not None:
            calls.append(list(syms))
        if hook is not None:
            hook()
        at = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
        pr = {s: prices for s in syms} if isinstance(prices, (int, float)) else dict(prices or {})
        return ({s: _tick(p, at) for s, p in pr.items() if s in syms}, {s: float(p) for s, p in pr.items() if s in syms},
                {s: {"reason": "NO_VERIFIED_FUTURES_PRICE"} for s in syms if s not in pr})
    return fn


def test_monitor_protects_all_five_books_while_a_real_tour_is_blocked(tmp_path, monkeypatch):
    """Ağır/bloke tur: `tour()` ajan adımında bekletilir; izleyici BU ARADA beş defterin stop'unu güncel fiyatla işler."""
    eng, books = _five_books(tmp_path, monkeypatch)
    in_tour, release = threading.Event(), threading.Event()
    run = eng.runner.run_symbol

    def blocking_run(symbol, analysis=None, prefetched=None):
        in_tour.set()
        assert release.wait(30), "test tur kilidini bırakmadı"
        return run(symbol, analysis, prefetched)
    monkeypatch.setattr(eng.runner, "run_symbol", blocking_run)
    clock = Clock(utc_now())
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(90.0), clock_ms=clock)
    errors: list = []

    def tour():
        try:
            eng.tour(do_scan=False, obsidian=False, charts=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    th = threading.Thread(target=tour, daemon=True)
    th.start()
    try:
        assert in_tour.wait(30), "tur ajan adımına ulaşmadı"
        res = eng.protective_monitor.run_once()                    # tur BLOKE iken
        assert th.is_alive(), "tur hâlâ bloke olmalıydı"
        assert all(SYM not in led.positions for led in books.values()), {k: list(v.positions) for k, v in books.items()}
        assert set(res["closed"]) == set(books), res
        assert all(h.exit_reason in ("stop", "STOP") or str(h.exit_reason).lower().startswith("stop")
                   for led in books.values() for h in led.history[-1:])
        # ana defter kapanışı kuyrukta: öğrenme ana iş parçacığında (tur), izleyici öğrenicilere dokunmaz
        assert eng._pending_protective_ids() == {str(books["main"].history[-1].id)}
    finally:
        release.set()
        th.join(60)
    assert not errors, errors
    assert not eng._pending_protective_ids(), "tur kuyruğu öğrendi"
    assert eng.learner2.n_closed >= 1


def test_the_threaded_monitor_keeps_every_open_position_within_sixty_seconds_during_a_blocked_tour(tmp_path, monkeypatch):
    """Gerçek izleyici İŞ PARÇACIĞI: geçişler olayla sürülür, saat 60 sn ilerletilir; tur boyunca bloke. Açık pozisyon
    başına en uzun gözlem aralığı ölçülür (hedef 60 sn)."""
    eng, books = _five_books(tmp_path, monkeypatch)
    clock = Clock(utc_now())
    passes, go = threading.Semaphore(0), threading.Semaphore(0)

    def waiter(_s):
        passes.release()                                          # bir geçiş bitti
        go.acquire(timeout=30)                                    # test izin verene kadar bekle (uyku yok)
        clock.advance(60)
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(101.0), clock_ms=clock, waiter=waiter)
    mon = eng.protective_monitor
    for b in eng.strategy_books:
        assert b.observer is mon.observer
    in_tour, release = threading.Event(), threading.Event()
    run = eng.runner.run_symbol

    def blocking_run(symbol, analysis=None, prefetched=None):
        in_tour.set()
        release.wait(60)
        return run(symbol, analysis, prefetched)
    monkeypatch.setattr(eng.runner, "run_symbol", blocking_run)
    th = threading.Thread(target=lambda: eng.tour(do_scan=False, obsidian=False, charts=False), daemon=True)
    th.start()
    assert in_tour.wait(30)
    mon.start()
    try:
        for _ in range(5):                                        # 5 geçiş ≈ 5 dk tur blokesi
            assert passes.acquire(timeout=30), "izleyici geçişi tamamlanmadı"
            assert th.is_alive()
            go.release()
        assert passes.acquire(timeout=30)
        st = mon.status()["observations"]
        for key in books:
            b = st["books"][key]
            assert b["open_positions"] == 1 and b["measured"], (key, b)
            assert b["max_gap_s"] is not None and b["max_gap_s"] <= 60.0, (key, b)
            (row,) = b["positions"].values()
            assert row["observations"] >= 5 and row["sources"].get("monitor", 0) >= 5, (key, row)
        assert st["over_target"] is False and st["worst_gap_s"] <= 60.0
        doc = json.loads((eng.cfg.state_path / "protective_monitor.json").read_text(encoding="utf-8"))
        assert doc["runs"] >= 5 and doc["pass_gap_max_s"] <= 60.0
    finally:
        mon.stop(0.1)
        go.release()
        release.set()
        th.join(60)
        mon.stop(10)


def test_a_book_without_open_positions_is_not_reported_as_verified(tmp_path, monkeypatch):
    eng, books = _five_books(tmp_path, monkeypatch)
    for b in eng.strategy_books:
        if b.key == "strategy_paper_m2":
            b.ledger.close_manual(SYM, D("100"), now=utc_now())
    clock = Clock(utc_now())
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(101.0), clock_ms=clock)
    eng.protective_monitor.run_once()
    st = eng.protective_monitor.status()["observations"]["books"]
    assert st["strategy_paper_m2"]["open_positions"] == 0 and st["strategy_paper_m2"]["max_gap_s"] is None
    assert st["main"]["open_positions"] == 1


def test_a_position_closed_and_reopened_by_the_tour_during_the_price_fetch_is_neither_closed_twice_nor_hit_by_the_old_price(tmp_path, monkeypatch):
    """Eşzamanlı kapanış: izleyici kimlikleri alır, fiyatı beklerken (kilitsiz) tur pozisyonu kapatır ve aynı sembolde
    yenisini açar. İzleyicinin eski gözlemi (stop altı) yeni pozisyona UYGULANMAZ; eski pozisyon ikinci kez kapanmaz."""
    eng, books = _five_books(tmp_path, monkeypatch)
    t2 = next(b for b in eng.strategy_books if b.key == "strategy_paper")
    old_main, old_t2 = books["main"].positions[SYM].id, t2.ledger.positions[SYM].id

    def tour_interleaves():
        now = utc_now()
        with eng._exit_lock:
            eng.ledger2.close_manual(SYM, D("100"), now=now)
            assert eng.ledger2.open(SYM, "LONG", D("100"), QTY, stop=D("80"), targets=[D("130")], now=now) is not None
        with t2.lock:
            t2.ledger.close_manual(SYM, D("100"), now=now)
            assert t2.ledger.open(SYM, "LONG", D("100"), QTY, stop=D("80"), targets=[D("130")], now=now) is not None
    clock = Clock(utc_now())
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(90.0, hook=tour_interleaves), clock_ms=clock)
    res = eng.protective_monitor.run_once()
    for led, old in ((eng.ledger2, old_main), (t2.ledger, old_t2)):
        assert [h.id for h in led.history].count(old) == 1, "eski pozisyon TEK kez kapandı (turun kapanışı)"
        assert SYM in led.positions and led.positions[SYM].id != old, "yeni pozisyon eski fiyatla kapatılmadı"
        assert led.positions[SYM].last_price != D("90")
    assert "main" not in res["closed"] and "strategy_paper" not in res["closed"]
    assert {"strategy_paper_m2", "strategy_paper_box", "pattern_trader"} <= set(res["closed"])


def test_a_tp1_partial_between_snapshot_and_protect_closes_only_the_remaining_quantity(tmp_path, monkeypatch):
    eng, books = _five_books(tmp_path, monkeypatch)
    led = eng.ledger2
    pos = led.positions[SYM]
    pos.targets = [D("110"), D("130")]
    q0 = pos.qty

    def partial():                                              # tur TP1'i işler (kısmi kapanış, stop başabaşa)
        with eng._exit_lock:
            led.tick({SYM: _tick(111.0, utc_now())}, now_utc=utc_now())
        assert led.positions[SYM].tp1_done and led.positions[SYM].qty < q0
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(90.0, hook=partial), clock_ms=Clock(utc_now()))
    eng.protective_monitor.run_once()
    assert SYM not in led.positions
    rec = led.history[-1]
    exits = sum((f.qty for f in rec.fills if f.kind != "entry"), D("0"))
    assert exits == q0, "toplam çıkış miktarı = giriş miktarı (kısmi yarı ikinci kez kapanmadı)"


def _live(px: float, *, src: datetime | None, got: datetime | None) -> TickData:
    """Canlı fiyat tiki: borsa (kaynak) ve alınma zamanı AYRI (`protective_monitor.live_tick` biçimi)."""
    best = src or got
    return TickData(last=D(str(px)), mark=D(str(px)), ts=iso(best) if best else "",
                    src_ts=iso(src, timespec="milliseconds") if src else "", fetched_ts=iso(got, timespec="milliseconds") if got else "")


def test_an_older_exchange_timed_price_is_rejected_without_any_tolerance(tmp_path, monkeypatch):
    """Karşılaştırılabilir borsa zamanlarında, uygulanmış fiyattan YALNIZ 5 sn eski ve stop altı fiyat bile kapatmaz.
    ÖNCE (09840eb): 10 sn'lik sıra payı bu fiyatla stop kapatıyordu ve eski test bunu BEKLİYORDU."""
    eng, books = _five_books(tmp_path, monkeypatch)
    led = eng.ledger2
    t0 = utc_now()
    clock = Clock(t0 + timedelta(seconds=1))
    with eng._exit_lock:
        recs, info = guarded_tick(led, {SYM: _live(101.0, src=t0, got=t0)}, now=t0, apply_clock=clock)
    assert recs == [] and SYM in info["applied"]
    meta = led.positions[SYM].meta
    assert meta["mark_src_ts_ms"] == _ms(t0) and meta["mark_fetched_ts_ms"] == _ms(t0), "kaynak ve alınma zamanı AYRI kalıcı"
    older = _live(94.0, src=t0 - timedelta(seconds=5), got=t0 + timedelta(seconds=1))      # yeni ALINDI ama borsa zamanı eski
    with eng._exit_lock:
        recs, info = guarded_tick(led, {SYM: older}, now=t0 + timedelta(seconds=1), apply_clock=clock)
    assert recs == [] and info["skipped"] == {SYM: "OLDER_THAN_APPLIED"} and SYM in led.positions
    probe = FuturesLedgerV2.from_dict(led.to_dict())                     # korumasız defter tiki bu fiyatla KAPATIRDI
    assert len(probe.tick({SYM: older}, now_utc=t0)) == 1
    same = _live(94.0, src=t0, got=t0 + timedelta(seconds=2))            # AYNI borsa anı: daha eski değil
    with eng._exit_lock:
        recs, _ = guarded_tick(led, {SYM: same}, now=t0 + timedelta(seconds=2), apply_clock=clock)
    assert len(recs) == 1 and SYM not in led.positions


def test_without_a_comparable_exchange_time_the_fetch_times_decide(tmp_path, monkeypatch):
    """Borsa zamanı olmayan fiyat (yalnız alınma zamanı): önbellekten gelen daha ESKİ alınma reddedilir, yenisi uygulanır."""
    eng, books = _five_books(tmp_path, monkeypatch)
    led = eng.ledger2
    t0 = utc_now()
    clock = Clock(t0 + timedelta(seconds=2))
    with eng._exit_lock:
        guarded_tick(led, {SYM: _live(101.0, src=t0, got=t0)}, now=t0, apply_clock=clock)
        recs, info = guarded_tick(led, {SYM: _live(94.0, src=None, got=t0 - timedelta(seconds=50))}, now=t0, apply_clock=clock)
    assert recs == [] and info["skipped"] == {SYM: "OLDER_THAN_APPLIED"}
    with eng._exit_lock:
        recs, _ = guarded_tick(led, {SYM: _live(94.0, src=None, got=t0 + timedelta(seconds=1))}, now=t0, apply_clock=clock)
    assert len(recs) == 1


def test_freshness_is_checked_again_when_the_price_is_applied(tmp_path, monkeypatch):
    """Alınırken taze (yaş 170 sn < 180) fiyat, kilit beklemesi/uzun tur yüzünden UYGULANIRKEN bayatsa tick YOK."""
    eng, books = _five_books(tmp_path, monkeypatch)
    led = eng.ledger2
    t0 = utc_now()
    px = _live(90.0, src=t0 - timedelta(seconds=170), got=t0)
    late = Clock(t0 + timedelta(seconds=20))                             # uygulama anı: 190 sn > PRICE_MAX_AGE_S
    with eng._exit_lock:
        recs, info = guarded_tick(led, {SYM: px}, now=t0, apply_clock=late)
    assert recs == [] and info["skipped"] == {SYM: "STALE_AT_APPLY"} and SYM in led.positions
    assert 170 < PRICE_MAX_AGE_S < 190
    future = _live(90.0, src=t0 + timedelta(minutes=10), got=t0)
    with eng._exit_lock:
        recs, info = guarded_tick(led, {SYM: future}, now=t0, apply_clock=Clock(t0))
    assert recs == [] and info["skipped"] == {SYM: "PRICE_TIME_IN_FUTURE"}
    # izleyici yolu da aynı saati kilit altında okur: saat 20 sn ilerletilince aynı fiyat uygulanmaz
    clock = Clock(t0)
    stale_at_apply = Clock(t0 + timedelta(seconds=200))
    eng.ensure_protective_monitor(interval_s=60, start=False, clock_ms=clock,
                                  price_fn=lambda syms, now_ms: ({s: _live(90.0, src=t0, got=t0) for s in syms}, {s: 90.0 for s in syms}, {}))
    eng.protective_monitor.clock_ms = stale_at_apply
    eng.protective_monitor.run_once(now_ms=_ms(t0))
    assert all(SYM in led_.positions for led_ in books.values()), "uygulama anında bayat fiyat hiçbir defterde kapanış üretmedi"
    eng.protective_monitor.clock_ms = clock
    eng.protective_monitor.run_once(now_ms=_ms(t0))
    assert all(SYM not in led_.positions for led_ in books.values())


def test_network_waits_never_hold_a_ledger_lock(tmp_path, monkeypatch):
    """Fiyat isteği ve funding ağ adımı sürerken defter kilitleri BOŞTA: tur (başka iş parçacığı) deftere yazabilir."""
    eng, books = _five_books(tmp_path, monkeypatch)
    in_fetch, done = threading.Event(), threading.Event()
    got: dict = {}

    def slow_prices():
        in_fetch.set()
        assert done.wait(30)

    def other_thread():
        assert in_fetch.wait(30)
        locks = {"main": eng._exit_lock, **{b.key: b.lock for b in eng.strategy_books}, "pattern_trader": eng.pattern_book.lock}
        for k, lk in locks.items():
            got[k] = lk.acquire(timeout=2)
            if got[k]:
                lk.release()
        done.set()
    th = threading.Thread(target=other_thread, daemon=True)
    th.start()
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(101.0, hook=slow_prices), clock_ms=Clock(utc_now()))
    eng.protective_monitor.run_once()
    th.join(30)
    assert got and all(got.values()), got


def test_funding_refresh_blocked_on_the_network_does_not_delay_the_stop(tmp_path, monkeypatch):
    """Funding ağ adımı (tur) bloke iken izleyici stop'u işler; bilinmeyen funding dönemi BEKLER (tahmin yok)."""
    eng, books = _five_books(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    class Blocking(SynthFundingProvider):
        def funding_history(self, *a, **k):
            entered.set()
            release.wait(30)
            return super().funding_history(*a, **k)
    monkeypatch.setattr(eng.funding_rates, "provider", Blocking())
    th = threading.Thread(target=lambda: eng._funding_step(utc_now()), daemon=True)
    th.start()
    try:
        assert entered.wait(30), "funding ağ adımı başlamadı"
        eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(90.0), clock_ms=Clock(utc_now()))
        res = eng.protective_monitor.run_once()
        assert set(res["closed"]) == set(books), res
        assert th.is_alive(), "funding ağ adımı hâlâ bekliyor"
    finally:
        release.set()
        th.join(30)
    # gerçekleşmiş funding sözleşmesi korunur: yazılan her dönem settlement satırının KENDİ oranı/mark'ıyla (tahmin yok),
    # ağ adımı bitince aynı dönem İKİNCİ kez yazılmaz (idempotent)
    rec = eng.ledger2.history[-1]
    rows = [r for r in (rec.features.get("funding_settlements") or []) if not r.get("reversed_at")]
    assert all(r["mark_basis"] == "SETTLEMENT_ROW" and not r["estimated"] for r in rows), rows
    n = len(rows)
    eng._funding_step(utc_now())
    eng._funding_step(utc_now())
    rec = eng.ledger2.history[-1]
    assert len([r for r in (rec.features.get("funding_settlements") or []) if not r.get("reversed_at")]) >= n
    assert len({r["settlement"] for r in rec.features.get("funding_settlements") or []}) == len(rec.features.get("funding_settlements") or [])


def test_pattern_book_releases_its_lock_during_the_liquidity_request(tmp_path, monkeypatch):
    """Formasyon defteri: tetik anındaki likidite (ağ) isteği sürerken kilit bırakılır; sonra aynı derinlikte geri alınır."""
    eng, books = _five_books(tmp_path, monkeypatch)
    pb = eng.pattern_book
    waiting, done = threading.Event(), threading.Event()
    seen: dict = {}

    def liquidity():
        waiting.set()
        assert done.wait(30)
        return {"spread_pct": 0.01}

    def monitor():
        assert waiting.wait(30)
        seen["acquired"] = pb.lock.acquire(timeout=2)
        if seen["acquired"]:
            pb.protect({SYM: _tick(90.0, utc_now())}, {SYM: 90.0}, {}, now=utc_now(), expect=pb.held_ids())
            pb.lock.release()
        done.set()
    th = threading.Thread(target=monitor, daemon=True)
    th.start()
    with pb.lock:
        with pb.lock:                                             # iç içe (RLock) — derinlik korunur
            out = pb._call_unlocked(liquidity)
            assert pb.lock._is_owned()
        assert pb.lock._is_owned()
    th.join(30)
    assert out == {"spread_pct": 0.01} and seen.get("acquired") is True
    assert SYM not in pb.ledger.positions, "izleyici likidite beklenirken formasyon defterinin stop'unu işledi"


def test_restart_learns_a_monitor_close_exactly_once_and_keeps_the_price_order(tmp_path, monkeypatch):
    eng, books = _five_books(tmp_path, monkeypatch)
    t_obs = utc_now()
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn({SYM: 101.0}), clock_ms=Clock(t_obs))
    eng.protective_monitor.run_once()                                 # gözlem: fiyat zamanı kalıcı
    t2 = next(b for b in eng.strategy_books if b.key == "strategy_paper")
    assert t2.ledger.positions[SYM].meta["mark_src_ts_ms"] == _ms(t_obs.replace(microsecond=0))
    for key, led in books.items():
        if key != "main":
            led.positions[SYM].stop = D("1")                         # yalnız ana defter kapansın
    eng.protective_monitor.price_fn = _price_fn(90.0)
    eng.protective_monitor.run_once()                                 # ana defter kapanır → kuyruk; süreç ÖĞRENMEDEN "ölür"
    tid = str(eng.ledger2.history[-1].id)
    assert eng._pending_protective_ids() == {tid}
    n0 = eng.learner2.n_closed
    eng2 = _engine(tmp_path, monkeypatch, _five(), symbols=2, equity=EQUITY)   # yeniden başlatma (aynı state)
    assert eng2._pending_protective_ids() == {tid}
    assert eng2._complete_close_chain().get("lessons_added", 0) == 0, "zincir onarımı kuyruktakini öğrenmez (çift yol yok)"
    out = eng2.drain_protective_closes()
    assert [c["id"] for c in out] == [tid] and not eng2._pending_protective_ids()
    assert eng2.drain_protective_closes() == []
    from tradingbot.learn.close_chain import close_event_id
    from tradingbot.learn.reconcile import LearnedIndex
    idx = LearnedIndex(eng2.cfg.state_path / "learned_closes.jsonl").load()
    h = eng2.ledger2.history[-1]
    assert idx[close_event_id(h.id, h.closed_at, h.exit_reason)]["source"] == "EXIT_MONITOR"
    assert eng2.learner2.n_closed == n0 + 1
    # sıra yeniden başlatmada da korunur: diskten yüklenen T2 pozisyonuna eski fiyat uygulanmaz
    t2b = next(b for b in eng2.strategy_books if b.key == "strategy_paper")
    recs = t2b.tick({SYM: _tick(80.0, t_obs - timedelta(minutes=5))}, now=utc_now(), bar_advance=False)
    assert recs == [] and SYM in t2b.ledger.positions


def test_perp_marks_use_the_exchange_mark_time_and_never_a_spot_price(tmp_path):
    now_ms = _ms(utc_now())

    class Http:
        def __init__(self, rows):
            self.rows = rows

        def get(self, path, weight=1):
            assert path.endswith("/premiumIndex")
            return self.rows

    class Prov:
        prefix = "/fapi/v1"

        def __init__(self, rows):
            self.http = Http(rows)

        def mark_price(self, sym):
            raise RuntimeError("perp mark yok")

    rows = [{"symbol": "ETHUSDT", "markPrice": "101.5", "time": now_ms - 2_000},
            {"symbol": "SOLUSDT", "markPrice": "20.0", "time": now_ms - 600_000}]
    marks, mf, gaps = perp_marks(Prov(rows), ["ETH/USDT", "SOL/USDT", "AVAX/USDT"], now_ms)
    assert mf == {"ETH/USDT": 101.5} and marks["ETH/USDT"].ts == iso(datetime.fromtimestamp((now_ms - 2_000) / 1000, tz=UTC))
    assert gaps["SOL/USDT"]["reason"] == "STALE_FUTURES_PRICE" and gaps["AVAX/USDT"]["reason"] == "NO_VERIFIED_FUTURES_PRICE"


def test_main_exit_monitor_ignores_a_spot_ticker_without_a_verified_perp_mark(tmp_path, monkeypatch):
    """ÖNCE: ana defterin 60 sn izleyicisi `ticker.last` (spot) ile futures pozisyonunun stop'unu işliyordu."""
    eng, books = _five_books(tmp_path, monkeypatch)
    monkeypatch.setattr(eng.runner.live, "snapshot", lambda s: {"ticker": {"last": 50.0}, "ts": utc_now().timestamp()})
    monkeypatch.setattr(eng, "_strategy_paper_exit_check", lambda: None)
    monkeypatch.setattr(eng, "_pattern_exit_check", lambda: None)
    assert eng.exit_check() == [] and SYM in eng.ledger2.positions
    assert eng._main_price_gaps[SYM]["reason"] == "NO_VERIFIED_FUTURES_PRICE"


def test_the_monitor_survives_a_failing_book_and_still_protects_the_others(tmp_path, monkeypatch):
    eng, books = _five_books(tmp_path, monkeypatch)
    box = next(b for b in eng.strategy_books if b.key == "strategy_paper_box")
    monkeypatch.setattr(box, "protect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk dolu")))
    mon_state = {}
    eng.ensure_protective_monitor(interval_s=60, start=False, price_fn=_price_fn(90.0), clock_ms=Clock(utc_now()))
    res = eng.protective_monitor.run_once()
    mon_state.update(res)
    assert "strategy_paper_box" in res["errors"] and SYM in box.ledger.positions
    assert set(res["closed"]) == set(books) - {"strategy_paper_box"}


def test_stop_during_the_tour_tick_path_uses_the_fresh_perp_mark_under_the_ledger_lock(tmp_path, monkeypatch):
    """Tur adım 5: ana defter tiki fiyatı BU AN için doğrulanmış perp mark'tan alır ve kilit altında uygular."""
    eng, books = _five_books(tmp_path, monkeypatch)
    for key in list(books):
        if key != "main":
            books[key].positions[SYM].stop = D("1")                  # yalnız ana defteri sına
    held_lock: list = []
    orig = eng._paper_marks

    def spy(syms, now=None):
        held_lock.append(eng._exit_lock._is_owned())
        m, mf, g = orig(syms, now=now)
        if SYM in syms:
            fm, fmf, _ = _fixed_marks(90.0)([SYM], now)
            m, mf = {**m, **fm}, {**mf, **fmf}
            g.pop(SYM, None)
        return m, mf, g
    monkeypatch.setattr(eng, "_paper_marks", spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    closed = [h for h in eng.ledger2.history if h.symbol == SYM]
    assert SYM not in eng.ledger2.positions and closed and closed[-1].exit_reason in ("stop", "be_stop"), closed
    assert held_lock and not any(held_lock), "fiyat isteği sırasında ana defter kilidi TUTULMADI"


def test_real_clock_cadence_the_monitor_thread_keeps_observing_while_the_tour_is_blocked(tmp_path, monkeypatch):
    """KONTROLLÜ SENARYO (gerçek saat, kısa): izleyici 1 sn aralıkla kendi iş parçacığında koşar; tur ajan adımında bloke.
    Bekleme uyku değil olay: test, izleyicinin N geçişini `price_fn` çağrılarıyla sayar. Ölçülen en uzun aralık ~1 sn."""
    eng, books = _five_books(tmp_path, monkeypatch)
    calls: list = []
    enough = threading.Event()

    def counted(syms, now_ms):
        calls.append(now_ms)
        if len(calls) >= 4:
            enough.set()
        return _price_fn(101.0)(syms, now_ms)
    in_tour, release = threading.Event(), threading.Event()
    run = eng.runner.run_symbol

    def blocking_run(symbol, analysis=None, prefetched=None):
        in_tour.set()
        release.wait(60)
        return run(symbol, analysis, prefetched)
    monkeypatch.setattr(eng.runner, "run_symbol", blocking_run)
    th = threading.Thread(target=lambda: eng.tour(do_scan=False, obsidian=False, charts=False), daemon=True)
    th.start()
    assert in_tour.wait(30)
    eng.ensure_protective_monitor(interval_s=1.0, price_fn=counted)
    try:
        assert enough.wait(30), "izleyici tur bloke iken geçiş yapmadı"
        assert th.is_alive()
        st = eng.protective_monitor.status()["observations"]
        assert all(st["books"][k]["open_positions"] == 1 for k in books)
        gaps = [(calls[i + 1] - calls[i]) / 1000.0 for i in range(len(calls) - 1)]
        assert max(gaps) < 10.0, gaps                             # hedef 1 sn; tur blokesinden bağımsız
    finally:
        eng.stop_protective_monitor(10)
        release.set()
        th.join(60)


class _WatchEngine:
    """`watch` döngüsünün bağlantısı için sahte motor (ağsız; tur ikinci çağrıda döngüyü bitirir)."""

    def __init__(self, monitor_ok: bool):
        self.calls: list = []
        self.monitor_ok = monitor_ok
        self.protective_monitor = None
        self.tours = 0

    def set_stop_check(self, fn):
        pass

    def ensure_protective_monitor(self, interval_s):
        self.calls.append(("monitor", interval_s))
        if not self.monitor_ok:
            raise RuntimeError("izleyici kurulamadı")
        self.protective_monitor = type("M", (), {"alive": True})()

    def tour(self, do_scan=True, obsidian=True):
        self.tours += 1
        self.calls.append("tour")
        if self.tours >= 2:
            raise KeyboardInterrupt
        return {"run_id": "r", "opened": [], "closed": []}

    def drain_protective_closes(self):
        self.calls.append("drain")
        return []

    def record_monitor_path(self):
        return 0

    def exit_check(self):
        self.calls.append("exit_check")
        return []

    def stop_protective_monitor(self):
        self.calls.append("stop_monitor")


def _run_watch(tmp_path, monkeypatch, monitor_ok: bool) -> list:
    import argparse
    from tradingbot import cli
    from tradingbot.config import BotConfig
    cfg = BotConfig()
    cfg.project_root = tmp_path
    fake = _WatchEngine(monitor_ok)
    monkeypatch.setattr(cli, "_make_engine", lambda c, legacy=False: fake)
    monkeypatch.setattr(cli, "_print_tour", lambda s: None)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)            # bekleme olay/sayaçla ilerler, gerçek uyku yok
    args = argparse.Namespace(interval=1, scan_every=1, no_obsidian=True, no_wfo=True, exit_every=60, legacy=False,
                              families=None, no_paper=True)
    assert cli.cmd_watch(cfg, args) == 0
    return fake.calls


def test_watch_starts_the_monitor_and_only_drains_learning_in_the_main_thread(tmp_path, monkeypatch):
    calls = _run_watch(tmp_path, monkeypatch, monitor_ok=True)
    assert calls[0] == ("monitor", 60.0) and calls.index("tour") > 0, "izleyici ilk turdan ÖNCE başlar"
    assert "exit_check" not in calls, "izleyici canlıyken eşzamanlı exit_check çağrılmaz"
    between = calls[calls.index("tour") + 1: len(calls) - 1 - calls[::-1].index("tour")]
    assert between.count("drain") >= 10, "bekleme sırasında izleyici kapanışları ana iş parçacığında öğrenilir"
    assert calls[-2:] == ["stop_monitor", "drain"]


def test_watch_falls_back_to_the_synchronous_exit_check_if_the_monitor_cannot_start(tmp_path, monkeypatch):
    calls = _run_watch(tmp_path, monkeypatch, monitor_ok=False)
    assert "exit_check" in calls and calls.index("exit_check") > calls.index("tour")

"""OTOMATIK ARSIV + INDEKS YENILEMESI — tek hedefli senaryo, AYNI SUREC, RESTART YOK.

Senaryo (talebin dort adimi):

1. Yeni mum geliyor.
2. Arsiv ve indeks ilerliyor.
3. Yeni indeks karar yolunda kullaniliyor.
4. Baglanti kesilip geri geldiginde ayni surec KENDILIGINDEN toparlaniyor.

Kararin degismesi BEKLENMEZ; yeni verinin gercekten kullanildigi kanitlanir.

Gercek olanlar: `HistoryStore` (arsiv diske yaziliyor), `IncrementalUpdater` (artimli
cekim mantigi), `IndexRefresher` (atomik yayim ve toparlanma). Indeks kurucusu testte
hafif tutulur — 200+ barlik gercek `SimilarPatternEngine` kurulumu dakikalar surer ve bu
testin oznesi indeks matematigi degil, YENILEME ZINCIRIDIR. Uretimin gercek kurucuyu
gecirdigi ayrica kaynak sozlesmesiyle dogrulanir (`test_production_wires_the_real_builder`).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.history.incremental import IncrementalUpdater, closed_only  # noqa: E402
from tradingbot.history.store import HistoryStore, cols_for  # noqa: E402
from tradingbot.patterns.refresher import IndexRefresher  # noqa: E402

BAR = 14_400_000                      # 4h
T0 = 1_700_000_000_000 - (1_700_000_000_000 % BAR)
SYM = "SOL/USDT"


def _bars(start_ms: int, n: int) -> pd.DataFrame:
    if n <= 0:                                  # bos cerceve de SUTUNLU olmali
        return pd.DataFrame(columns=cols_for("4h"))
    rows = []
    for i in range(n):
        t = start_ms + i * BAR
        c = 100.0 + i * 0.5
        rows.append({"timestamp": t, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                     "volume": 10.0, "close_time": t + BAR - 1, "quote_volume": 1000.0,
                     "trades": 5, "taker_buy_base": 5.0, "taker_buy_quote": 500.0})
    return pd.DataFrame(rows)[cols_for("4h")]


class _Venue:
    """Sahte borsa. `online=False` iken her istek patlar (baglanti kesilmesi)."""

    def __init__(self, all_bars: pd.DataFrame):
        self.all = all_bars
        self.online = True
        self.calls = 0

    def klines(self, symbol, interval, limit=1000, start_ms=None, end_ms=None):
        self.calls += 1
        if not self.online:
            raise ConnectionError("venue erişilemiyor")
        df = self.all
        if start_ms is not None:
            df = df[df["timestamp"] >= int(start_ms)]
        return df.head(int(limit)).reset_index(drop=True)


@pytest.fixture()
def rig(tmp_path: Path):
    """Arsivde 20 bar var; borsada 22 bar VAR (ikisi henuz cekilmemis)."""
    store = HistoryStore(tmp_path / "history")
    store.write("futures", SYM, "4h", _bars(T0, 20), source="test_seed")
    venue = _Venue(_bars(T0, 22))
    now = T0 + 22 * BAR                         # 22 barin hepsi KAPANMIS
    return store, venue, now


def _last(store) -> int:
    return int(store.manifest("futures", SYM, "4h").last_ts_ms)


# --------------------------------------------------------------------------- artimli cekim
def test_only_closed_bars_are_written(rig):
    """Acik bar arsive GIRMEZ: `t` barinin kapanisi `t + step`tir."""
    store, venue, _now = rig
    half_open = T0 + 21 * BAR + 1               # 22. bar henuz kapanmadi
    upd = IncrementalUpdater(store, venue)
    r = upd.update_series(SYM, "4h", now_ms=half_open)
    assert r.status == "advanced"
    assert _last(store) == T0 + 20 * BAR, "kapanmamis bar yazildi"


def test_closed_only_filter_drops_the_unclosed_bar():
    df = _bars(T0, 3)
    kept = closed_only(df, "4h", T0 + 3 * BAR - 1)
    assert list(kept["timestamp"]) == [T0, T0 + BAR]


def test_a_series_without_a_baseline_is_skipped_not_backfilled(tmp_path: Path):
    """Tur icinde yillarca gecmis SESSIZCE indirilmez."""
    store = HistoryStore(tmp_path / "h")
    upd = IncrementalUpdater(store, _Venue(_bars(T0, 5)))
    r = upd.update_series("BTC/USDT", "4h", now_ms=T0 + 9 * BAR)
    assert r.status == "skipped_no_baseline"
    assert r.rows_new == 0


def test_an_empty_answer_does_not_advance_the_timestamp(rig):
    """"Guncel gorunsun" diye damga ILERLETILMEZ."""
    store, _venue, now = rig

    class _Empty:
        def klines(self, *a, **k):
            return _bars(T0, 0)

    before = _last(store)
    r = IncrementalUpdater(store, _Empty()).update_series(SYM, "4h", now_ms=now)
    assert r.status == "unchanged"
    assert _last(store) == before


def test_a_network_error_leaves_the_archive_untouched(rig):
    store, venue, now = rig
    before = _last(store)
    venue.online = False
    r = IncrementalUpdater(store, venue).update_series(SYM, "4h", now_ms=now)
    assert r.status == "error" and "ConnectionError" in r.error
    assert _last(store) == before, "hata arşivi bozdu"


def test_update_is_idempotent(rig):
    """Ikinci calistirma yeni satir EKLEMEZ."""
    store, venue, now = rig
    upd = IncrementalUpdater(store, venue)
    first = upd.update_series(SYM, "4h", now_ms=now)
    second = upd.update_series(SYM, "4h", now_ms=now)
    assert first.rows_new == 2
    assert second.status == "unchanged" and second.rows_new == 0


def test_request_budget_stops_the_cycle_and_says_so(rig):
    store, venue, now = rig
    rep = IncrementalUpdater(store, venue, max_requests=1).update(
        [SYM, "ETH/USDT", "BTC/USDT"], ["4h"], now_ms=now)
    assert rep.truncated is True
    assert rep.requests <= 1


# --------------------------------------------------------------------------- SENARYO
def test_scenario_new_bar_advances_archive_and_index_and_survives_a_disconnect(rig):
    """DORT ADIM, tek senaryo, tek surec.

    Not: yenileyicinin arka plan is parcacigi BASLATILMAZ; `refresh_once` dogrudan
    cagrilir. Zamanlamaya bagli test yazmamak icin bilincli — is parcacigi yalnizca bu
    fonksiyonu periyodik cagirir ve ayri testi vardir.
    """
    store, venue, now = rig
    built: list[int] = []

    def _build(symbols):
        last = _last(store)
        built.append(last)
        eng = type("Idx", (), {})()
        eng.events = list(range(3))
        eng.candles = {(SYM, "futures", "4h"): pd.DataFrame({"timestamp": [last - BAR, last]})}
        return eng, {f"{SYM}|futures|4h": last}

    def _update(symbols, now_ms):
        return IncrementalUpdater(store, venue).update(symbols, ["4h"], now_ms=now_ms)

    r = IndexRefresher(build_fn=_build, symbols_fn=lambda: [SYM], update_fn=_update,
                       interval_s=10_000)

    # --- 1) ilk yayim: arsiv 20 -> 22 bar, indeks kuruldu
    out = r.refresh_once(now_ms=now)
    assert out.get("published") is True
    assert _last(store) == T0 + 21 * BAR, "arşiv yeni barlara ilerlemedi"
    v1 = r.bundle.version
    assert r.bundle.newest_ts() == _last(store)
    assert r.last_success_at is not None and r.last_error == ""

    # --- 2) arsiv durduysa bosuna yeniden kurulmaz (CPU ve 2x bellek israfi)
    out = r.refresh_once(now_ms=now)
    assert out.get("skipped") == "archive_unchanged"
    assert r.bundle.version == v1

    # --- 3) YENI MUM: arsiv ve indeks birlikte ilerliyor
    venue.all = _bars(T0, 24)
    now2 = T0 + 24 * BAR
    out = r.refresh_once(now_ms=now2)
    assert out.get("published") is True
    assert _last(store) == T0 + 23 * BAR, "yeni mumlar arşive girmedi"
    assert r.bundle.version == v1 + 1, "indeks sürümü ilerlemedi"
    assert r.bundle.newest_ts() == _last(store), "indeks yeni barı görmüyor"
    assert built[-1] == _last(store), "indeks ESKİ arşivden kuruldu"

    # --- 4) BAGLANTI KESILDI: eski indeks KORUNUR, bayatlik gorunur
    venue.online = False
    venue.all = _bars(T0, 26)
    stale_version = r.bundle.version
    stale_newest = r.bundle.newest_ts()
    out = r.refresh_once(now_ms=T0 + 26 * BAR)
    assert r.bundle.version == stale_version, "hata sırasında indeks değişti"
    assert r.bundle.newest_ts() == stale_newest
    assert "ConnectionError" in r.last_error, f"bayatlık görünür değil: {r.last_error!r}"
    assert r.status()["seconds_since_success"] is not None

    # --- 4b) BAGLANTI GERI GELDI: ELLE MUDAHALE YOK, ayni surec toparlaniyor
    venue.online = True
    out = r.refresh_once(now_ms=T0 + 26 * BAR)
    assert out.get("published") is True, "bağlantı dönünce yayım olmadı"
    assert r.bundle.version == stale_version + 1
    assert r.bundle.newest_ts() == T0 + 25 * BAR
    assert r.last_error == "", "toparlandıktan sonra hata hâlâ gösteriliyor"


# --------------------------------------------------------------------------- yayim guvenligi
def test_a_failed_build_keeps_the_previous_index(rig):
    """Yarim kurulmus indeks karar yoluna SIZAMAZ."""
    store, venue, now = rig
    state = {"fail": False}

    def _build(symbols):
        if state["fail"]:
            raise RuntimeError("kurulum patladı")
        eng = type("Idx", (), {})()
        eng.events, eng.candles = [1], {}
        return eng, {"k": _last(store)}

    r = IndexRefresher(build_fn=_build, symbols_fn=lambda: [SYM],
                       update_fn=lambda s, n: IncrementalUpdater(store, venue).update(s, ["4h"], now_ms=n),
                       interval_s=10_000)
    r.refresh_once(now_ms=now)
    good = r.bundle
    state["fail"] = True
    venue.all = _bars(T0, 24)
    out = r.refresh_once(now_ms=T0 + 24 * BAR)
    assert "error" in out
    assert r.bundle is good, "kurulum patladığında eski paket kaybedildi"
    assert "kurulum patladı" in r.last_error


def test_symbol_set_is_capped_for_memory(rig):
    """Bellek tavani: yeniden kurulumda eski ve yeni indeks birlikte yasar."""
    store, venue, _now = rig
    many = [f"C{i}/USDT" for i in range(50)]
    r = IndexRefresher(build_fn=lambda s: (None, {}), symbols_fn=lambda: many,
                       interval_s=10_000, max_symbols=16)
    assert len(r.symbols()) == 16


def test_open_positions_are_always_in_the_index_set(tmp_path: Path, monkeypatch):
    """Evrenden cikmis acik pozisyon da tarihsel baglam ister."""
    import test_engine_v3 as TE
    eng = TE._engine(tmp_path, monkeypatch,
                     {"entry_universe": {"enabled": True, "symbols": ["ETH/USDT"]}},
                     symbols=["ETH/USDT", "SOL/USDT"])

    class _P:
        pass

    eng.ledger2.positions["ADA/USDT"] = _P()
    syms = eng._index_symbols()
    assert "ETH/USDT" in syms and "ADA/USDT" in syms


def test_a_second_refresh_cannot_run_concurrently(rig):
    """Ayni anda iki kurulum = iki kat bellek. Kilit bunu engeller."""
    store, venue, now = rig
    r = IndexRefresher(build_fn=lambda s: (None, {}), symbols_fn=lambda: [SYM], interval_s=10_000)
    r._lock.acquire()
    try:
        assert r.refresh_once(now_ms=now).get("skipped") == "already_running"
    finally:
        r._lock.release()


# --------------------------------------------------------------------------- uretim baglantisi
def test_production_wires_the_real_builder_and_updater():
    """Testte hafif kurucu kullanildi; URETIM gercegini gecirmeli."""
    from tradingbot.engine_v3 import TradingEngineV3
    src = inspect.getsource(TradingEngineV3._make_refresher)
    assert "self._build_pattern_index" in src
    assert "IncrementalUpdater" in src
    assert "IndexRefresher" in src
    build = inspect.getsource(TradingEngineV3._build_pattern_index)
    assert "SimilarPatternEngine" in build


def test_the_tour_starts_the_refresher_and_the_exit_path_does_not():
    """Yenileme stop/TP yonetimini BEKLETMEZ: cikis yolu yenileyiciye hic dokunmaz."""
    from tradingbot.engine_v3 import TradingEngineV3
    assert "ensure_index_refresher" in inspect.getsource(TradingEngineV3.tour)
    exit_src = inspect.getsource(TradingEngineV3.exit_check)
    for bad in ("ensure_index_refresher", "_refresher", "IncrementalUpdater", "_build_pattern_index"):
        assert bad not in exit_src, f"çıkış yolu yenilemeye bağlandı: {bad}"


def test_the_refresher_runs_off_the_tour_thread():
    """`start()` arka plan is parcacigi kurar ve HEMEN doner."""
    src = inspect.getsource(IndexRefresher.start)
    assert "threading.Thread" in src and "daemon=True" in src


# --------------------------------------------------------------------------- yeniden kurulum tetigi
def test_only_the_timeframes_the_index_reads_trigger_a_rebuild(rig):
    """1h bari kapandi diye 4h indeksi yeniden KURULMAZ.

    Ölçüldü: yeniden kurulum 40 sn CPU, ardından soğuk önbellekle ~250 sn'lik bir tur
    demektir. İndekste tek bir olay değiştirmeyecek bir tetik bunu saatte bir öderdi.
    """
    store, venue, now = rig
    store.write("futures", SYM, "1h", _bars(T0, 20).assign(timestamp=lambda d: d["timestamp"]),
                source="seed_1h")
    builds = {"n": 0}

    def _build(symbols):
        builds["n"] += 1
        eng = type("Idx", (), {})()
        eng.events, eng.candles = [1], {}
        return eng, {"k": _last(store)}

    r = IndexRefresher(build_fn=_build, symbols_fn=lambda: [SYM], interval_s=10_000,
                       update_fn=lambda s, n: IncrementalUpdater(store, venue).update(s, ["4h"], now_ms=n),
                       index_timeframes=("4h",))
    r.refresh_once(now_ms=now)
    assert builds["n"] == 1                       # ilk kurulum

    # YALNIZ 1h ilerledi -> indeks yeniden kurulmamali
    r._update_fn = lambda s, n: type("R", (), {"to_dict": lambda self: {
        "advanced": True, "results": [{"symbol": SYM, "timeframe": "1h",
                                       "status": "advanced", "rows_new": 3}]}})()
    out = r.refresh_once(now_ms=now)
    assert out.get("skipped") == "archive_unchanged"
    assert builds["n"] == 1, "1h ilerlemesi 4h indeksini yeniden kurdu"

    # 4h ilerledi -> kurulmali
    r._update_fn = lambda s, n: type("R", (), {"to_dict": lambda self: {
        "advanced": True, "results": [{"symbol": SYM, "timeframe": "4h",
                                       "status": "advanced", "rows_new": 1}]}})()
    out = r.refresh_once(now_ms=now)
    assert out.get("published") is True
    assert builds["n"] == 2


def test_an_unknown_report_shape_falls_back_to_the_general_flag(rig):
    """Fail-safe: rapor bicimi taninmiyorsa yenileme ATLANMAZ."""
    store, venue, now = rig
    r = IndexRefresher(build_fn=lambda s: (type("I", (), {"events": [1], "candles": {}})(), {}),
                       symbols_fn=lambda: [SYM], interval_s=10_000, index_timeframes=("4h",))
    assert r._index_inputs_advanced({"advanced": True}) is True
    assert r._index_inputs_advanced({"advanced": False}) is False


def test_production_only_rebuilds_on_the_index_timeframe():
    from tradingbot.engine_v3 import TradingEngineV3
    src = inspect.getsource(TradingEngineV3._make_refresher)
    assert 'index_timeframes=("4h",)' in src

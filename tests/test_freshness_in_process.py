"""SÜREKLİ GÜNCELLİK — aynı worker sürecinde, RESTART YOK.

Soru (2026-09-11, dağıtım öncesi): yeni bir 1H/4H mum geldiğinde aynı süreçte ne ilerler,
ne ilerlemez, ve pattern önbelleği değişen girdiye doğru cevap verir mi?

Kodda İKİ AYRI veri yolu vardır ve bunları karıştırmak yanlış sonuç üretir:

* **A — GÜNCEL ADAY.** `tour()` her turda `perp_frames()` çağırır; mumlar borsadan YENİDEN
  çekilir, `drop_unclosed_last_bar` açık barı atar, göstergeler yeniden hesaplanır. Bayatlık
  kapısı `_quality_for` → `check_klines` (`max_candle_age_bars=2`, STALE_CANDLE = HIGH →
  DATA_INVALID). Bu yol her turda yeniden çektiği için bayatlıktan KENDİLİĞİNDEN çıkar.

* **B — TARİHSEL REFERANS.** `_load_pattern_engine` `HistoryStore`'dan `SimilarPatternEngine`
  kurar. `_pattern_loaded` bir kez True olur ve bir daha SIFIRLANMAZ: indeks süreç boyunca
  STATİKTİR. Bayatlık kapısı ayrıdır (`now_ms - last_ts > 3 bar`) ve kanıtı susturur.

Pattern önbelleği YALNIZ B yolundadır. A yolunun hiçbir hesabına dokunmaz.

Bu testler dört şeyi kanıtlar; işlem kararının DEĞİŞMESİNİ beklemezler, doğru girdinin
gerçekten değerlendirildiğini gösterirler.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.engine_v3 import TradingEngineV3  # noqa: E402

BAR_4H = 14_400_000
BAR_1H = 3_600_000
SYMS = ("ETH/USDT", "SOL/USDT")


class _Index:
    """Sahte tarihsel indeks. Gerçek `SimilarPatternEngine` gibi `candles` STATİKTİR;
    yalnız `advance()` ile (yani yeniden kurulmayı taklit ederek) ilerler."""

    def __init__(self, last_ts: int):
        self.calls = 0
        self.last = int(last_ts)
        self._build()

    def _build(self) -> None:
        self.candles = {(s, "futures", "4h"): pd.DataFrame({"timestamp": [self.last - BAR_4H, self.last]})
                        for s in SYMS}

    def advance(self, bars: int = 1) -> None:
        self.last += bars * BAR_4H
        self._build()

    def query(self, symbol, market, tf, side, k=60):
        self.calls += 1
        return {"side": side, "index_last": self.last, "n": 42, "neighbors": []}


def _engine(tmp_path, monkeypatch):
    eng = TE._engine(tmp_path, monkeypatch,
                     {"entry_universe": {"enabled": True, "symbols": list(SYMS)}},
                     symbols=list(SYMS), p_win=0.62)
    frames = eng._fake_live._frames
    monkeypatch.setattr(eng, "perp_frames", lambda s: dict(frames[s]))
    idx = _Index(int(frames["ETH/USDT"]["4h"]["timestamp"].iloc[-1]))
    monkeypatch.setattr(eng, "_load_pattern_engine", lambda: idx)
    return eng, idx, frames


def _append_bar(frames: dict, tf: str, step_ms: int) -> None:
    """Her sembolün `tf` çerçevesine BİR yeni kapalı bar ekler (borsadan yeni mum gelmesi)."""
    for fr in frames.values():
        df = fr[tf]
        nxt = df.iloc[[-1]].copy()
        nxt["timestamp"] = int(df["timestamp"].iloc[-1]) + step_ms
        nxt.index = [df.index[-1] + pd.Timedelta(milliseconds=step_ms)]
        fr[tf] = pd.concat([df, nxt])


def _decision_bar(eng, sym: str = "ETH/USDT", tf: str = "4h") -> int:
    return int(eng.runner.last_frames[sym][tf]["timestamp"].iloc[-1])


def _now_ms(eng) -> int:
    return int(eng._fake_live._now_s * 1000)


# ------------------------------------------------------------------ 1) güncel aday ilerliyor
def test_a_new_4h_bar_advances_the_decision_input_without_restart(tmp_path: Path, monkeypatch):
    """A YOLU: yeni mum geldiğinde kararın baktığı bar İLERLER (aynı süreç, restart yok)."""
    eng, _idx, frames = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    before = _decision_bar(eng)

    _append_bar(frames, "4h", BAR_4H)
    eng._fake_live._now_s += BAR_4H / 1000
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert _decision_bar(eng) == before + BAR_4H, "karar çerçevesi yeni barı GÖRMEDİ"


def test_a_new_1h_bar_advances_the_1h_decision_input(tmp_path: Path, monkeypatch):
    eng, _idx, frames = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    before = _decision_bar(eng, tf="1h")

    _append_bar(frames, "1h", BAR_1H)
    eng._fake_live._now_s += BAR_1H / 1000
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert _decision_bar(eng, tf="1h") == before + BAR_1H


def test_bar_advance_fires_once_on_the_new_bar_and_not_again(tmp_path: Path, monkeypatch):
    """`bar_advance` defteri besler (funding tahakkuku, bar sonu kontrolleri).

    Aynı bar içinde tekrar tetiklenmemeli: tetiklenirse funding iki kez işlenirdi.
    """
    eng, _idx, frames = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    seen0 = eng.last_bar_seen

    _append_bar(frames, "4h", BAR_4H)
    eng._fake_live._now_s += BAR_4H / 1000
    eng.tour(do_scan=False, obsidian=False, charts=False)
    seen1 = eng.last_bar_seen
    assert seen1 != seen0, "yeni bar `last_bar_seen`'i ilerletmedi → bar_advance tetiklenmezdi"

    eng.tour(do_scan=False, obsidian=False, charts=False)     # aynı bar, ikinci tur
    assert eng.last_bar_seen == seen1, "aynı bar ikinci kez ilerleme saymamalı"


# ------------------------------------------------------------------ 2) önbellek doğru cevap veriyor
def test_cache_holds_while_the_historical_index_has_not_moved(tmp_path: Path, monkeypatch):
    """B YOLU: karar mumu ilerlese bile İNDEKS ilerlemediyse tarihsel cevap AYNIDIR.

    Bu bir kusur değil, iki yolun ayrılığıdır: pattern kanıtı arşivlenmiş seriden gelir,
    o turun canlı mumundan değil. Önbellek burada YANLIŞ bir tazelik iddia ETMEZ.
    """
    eng, idx, frames = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    calls_after_first = idx.calls
    assert calls_after_first == len(SYMS) * 2, "sembol başına LONG+SHORT sorgusu beklenir"

    _append_bar(frames, "4h", BAR_4H)
    eng._fake_live._now_s += BAR_4H / 1000
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert idx.calls == calls_after_first, "indeks durduğu hâlde yeniden hesaplandı"
    # Sorgu zamanı indekse YAKIN sabitlenir: bu test önbelleği ölçer, bayatlık kapısını değil
    # (kapının kendi testi aşağıdadır).
    ev = eng._pattern_evidence("ETH/USDT", idx.last + BAR_4H)
    assert ev["LONG"]["index_last"] == idx.last, "cevap indeksin GERÇEK son barını yansıtmalı"


def test_cache_recomputes_when_the_historical_index_moves(tmp_path: Path, monkeypatch):
    """EN ÖNEMLİSİ: girdi değiştiğinde önbellek ESKİ cevabı vermez."""
    eng, idx, _frames = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    old = eng._pattern_evidence("ETH/USDT", _now_ms(eng))["LONG"]["index_last"]
    calls = idx.calls

    idx.advance(1)                                   # indeks yeni veriyle kuruldu
    eng._fake_live._now_s += BAR_4H / 1000
    eng.tour(do_scan=False, obsidian=False, charts=False)

    new = eng._pattern_evidence("ETH/USDT", _now_ms(eng))["LONG"]["index_last"]
    assert new == old + BAR_4H, "önbellek DEĞİŞEN girdiye eski cevabı verdi"
    assert idx.calls > calls, "yeni girdide yeniden hesaplama yapılmadı"


# ------------------------------------------------------------------ 3) yenileme mekanizması
def test_the_historical_index_is_built_once_per_process_and_never_refreshed():
    """KAYNAK SÖZLEŞMESİ — dürüstlük kaydı, iddia değil.

    `_pattern_loaded` bir kez True olur ve hiçbir yerde sıfırlanmaz; `_load_pattern_engine`
    süreç ömrü boyunca tek bir indeks kurar. Yani B yolunun ÇALIŞMA ZAMANINDA yenilenme
    mekanizması YOKTUR: arşiv `history-collect` ile güncellenir ve indeks ancak süreç
    yeniden başlatıldığında yeni veriyi görür.

    Bu test bir gerileme dedektörü değil, sınırın kaynakta DURDUĞUNUN kanıtıdır. Bir gün
    yenileme eklenirse bu test düşer ve belge güncellenmek zorunda kalır.
    """
    src = inspect.getsource(TradingEngineV3._load_pattern_engine)
    assert "if self._pattern_loaded:" in src
    assert "self._pattern_loaded = True" in src
    # `= False` YALNIZ `__init__` ilklendirmesinde olmalı. Başka bir yerde geçiyorsa indeks
    # çalışma zamanında yeniden kuruluyor demektir ve bu dosyadaki güncellik belgesi yanlış olur.
    whole = inspect.getsource(TradingEngineV3)
    assert whole.count("self._pattern_loaded = False") == 1
    assert "self._pattern_loaded = False" in inspect.getsource(TradingEngineV3.__init__)


# ------------------------------------------------------------------ 4) bayatlıktan dönüş
def test_stale_index_silences_evidence_and_a_refreshed_index_restores_it(tmp_path: Path, monkeypatch):
    """Bayat → kanıt YOK. İndeks tazelenirse → kanıt GERİ GELİR; önbellek engel DEĞİLDİR.

    Önbelleğin bu zincirdeki rolü kritiktir: bayatlık kapısı önbellekten ÖNCE çalıştığı için
    bayat pencerede ESKİ bir cevap DÖNMEZ, ve tazelenince yeni cevap hesaplanır.
    """
    eng, idx, _frames = _engine(tmp_path, monkeypatch)
    now = _now_ms(eng)
    assert eng._pattern_evidence("ETH/USDT", now) is not None, "taban: kanıt olmalı"

    # Kapı `now - last_ts > 3 bar`. 4h seride bu, arşivin son barından ~12 saat sonra demektir:
    # arşiv güncellenmezse worker çalışmaya devam ederken pattern kanıtı KENDİLİĞİNDEN susar.
    assert eng._pattern_evidence("ETH/USDT", idx.last + 3 * BAR_4H) is not None, "tam 3 bar hâlâ taze"
    stale_now = idx.last + 4 * BAR_4H
    assert eng._pattern_evidence("ETH/USDT", stale_now) is None, "bayat indeks kanıt vermemeli"

    idx.advance(4)                                   # arşiv güncellendi + süreç indeksi yeniledi
    back = eng._pattern_evidence("ETH/USDT", idx.last + BAR_4H)
    assert back is not None, "tazelenen indekste kanıt GERİ GELMEDİ"
    assert back["LONG"]["index_last"] == idx.last, "geri gelen cevap eski indeksi yansıtıyor"


def test_the_live_decision_path_recovers_from_staleness_on_its_own(tmp_path: Path, monkeypatch):
    """A YOLU kendiliğinden döner: her tur mumları YENİDEN çeker.

    Bayat bir çerçeveyle bir tur geçirilir (kalite kapısı DATA_INVALID verir), sonra taze
    mum gelir ve sonraki tur bunu görür — hiçbir müdahale ya da restart olmadan.
    """
    from tradingbot.market.quality import DataQualityConfig, DataQualityGate
    eng, _idx, frames = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)

    gate = DataQualityGate(DataQualityConfig())
    h4 = eng.runner.last_frames["ETH/USDT"]["4h"]
    fresh_ms = int(h4["timestamp"].iloc[-1]) + BAR_4H
    stale_ms = int(h4["timestamp"].iloc[-1]) + 6 * BAR_4H

    assert not gate.check_klines(h4.reset_index(drop=True), "4h", stale_ms).ok, "bayat sayılmalıydı"
    assert gate.check_klines(h4.reset_index(drop=True), "4h", fresh_ms).ok, "taze sayılmalıydı"

    before = _decision_bar(eng)
    _append_bar(frames, "4h", BAR_4H)
    eng._fake_live._now_s += BAR_4H / 1000
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert _decision_bar(eng) == before + BAR_4H, "taze mum geldiğinde tur onu görmeli"

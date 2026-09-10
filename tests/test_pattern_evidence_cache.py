"""PATTERN KANITI ONBELLEGI — ayni cevabi 20 kez hesaplamayi birakmak, cevabi degistirmeden.

Olcum (2026-09-11, 138.891 olayli indeks, on sembol): tek `SimilarPatternEngine.query`
12,6 sn suruyordu. `_pattern_evidence` sembol basina IKI sorgu yapar (LONG + SHORT), on
sembolde tur basina 251 sn eder — turun %88'i. Ustelik `SimilarPatternEngine` mum tablosunu
`_load_pattern_engine` icinde BIR KEZ kurar ve tur icinde guncellemez, yani ayni sorgu
surec boyunca AYNI cevabi verir. Iki ardisik sorgu birebir ayni sonucu dondurdu.

Bu testler onbellegin HIZLI degil DOGRU oldugunu kilitler:

1. Ayni indekste ayni cevap doner ve motor yeniden hesaplamaz.
2. Anahtar indeksin SON BARIDIR — indeks yeni veriyle kurulursa kanit yeniden hesaplanir.
3. Bayatlik kapisi onbellekten ONCE calisir: eski bir cevap, veri bayatladiktan sonra
   DONDURULMEZ.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

BAR_4H = 14_400_000


class _FakeEngine:
    """Sorgu sayan sahte pattern motoru. `candles` gercek motordaki gibi statiktir."""

    def __init__(self, last_ts: int, symbols=("SOL/USDT",)):
        self.calls = 0
        self.candles = {(s, "futures", "4h"): pd.DataFrame({"timestamp": [last_ts - BAR_4H, last_ts]})
                        for s in symbols}

    def query(self, symbol, market, tf, side, k=60):
        self.calls += 1
        return {"side": side, "n": 42, "mean_r": 0.1, "neighbors": []}


def _engine(tmp_path, monkeypatch, *, last_ts: int):
    eng = TE._engine(tmp_path, monkeypatch, symbols=["SOL/USDT", "ETH/USDT"])
    fake = _FakeEngine(last_ts, symbols=("SOL/USDT", "ETH/USDT"))
    monkeypatch.setattr(eng, "_load_pattern_engine", lambda: fake)
    return eng, fake


def _now_ms(last_ts: int, *, bars_after: float = 1.0) -> int:
    return int(last_ts + bars_after * BAR_4H)


def test_second_call_returns_the_same_answer_without_recomputing(tmp_path: Path, monkeypatch):
    last = 1_789_000_000_000
    eng, fake = _engine(tmp_path, monkeypatch, last_ts=last)
    now = _now_ms(last)
    a = eng._pattern_evidence("SOL/USDT", now)
    after_first = fake.calls
    b = eng._pattern_evidence("SOL/USDT", now)
    assert a == b
    assert after_first == 2, "ilk cagri LONG+SHORT sorgusu yapmali"
    assert fake.calls == 2, "ikinci cagri YENIDEN HESAPLAMAMALI"


def test_many_tours_cost_one_computation_per_symbol(tmp_path: Path, monkeypatch):
    last = 1_789_000_000_000
    eng, fake = _engine(tmp_path, monkeypatch, last_ts=last)
    now = _now_ms(last)
    for _ in range(16):                       # 4 saatlik bar boyunca 15 dk'lik turlar
        for sym in ("SOL/USDT", "ETH/USDT"):
            eng._pattern_evidence(sym, now)
    assert fake.calls == 4, f"iki sembol x iki yon = 4 sorgu beklenir, {fake.calls} yapildi"


def test_a_rebuilt_index_with_newer_data_recomputes(tmp_path: Path, monkeypatch):
    """Onbellek YANLIS TAZE olamaz: anahtar indeksin son baridir."""
    last = 1_789_000_000_000
    eng, fake = _engine(tmp_path, monkeypatch, last_ts=last)
    eng._pattern_evidence("SOL/USDT", _now_ms(last))
    assert fake.calls == 2

    newer = last + BAR_4H
    fake.candles[("SOL/USDT", "futures", "4h")] = pd.DataFrame({"timestamp": [newer - BAR_4H, newer]})
    eng._pattern_evidence("SOL/USDT", _now_ms(newer))
    assert fake.calls == 4, "yeni barli indekste kanit YENIDEN hesaplanmali"


def test_stale_data_is_refused_even_when_a_cached_answer_exists(tmp_path: Path, monkeypatch):
    """Bayatlik kapisi onbellekten ONCE calisir."""
    last = 1_789_000_000_000
    eng, fake = _engine(tmp_path, monkeypatch, last_ts=last)
    assert eng._pattern_evidence("SOL/USDT", _now_ms(last)) is not None
    assert fake.calls == 2
    stale = _now_ms(last, bars_after=4)        # 3 bardan eski -> kanit YOK
    assert eng._pattern_evidence("SOL/USDT", stale) is None
    assert fake.calls == 2, "bayat cagri yeniden hesaplamamali (ama cevap da vermemeli)"


def test_symbols_do_not_share_a_cache_entry(tmp_path: Path, monkeypatch):
    last = 1_789_000_000_000
    eng, fake = _engine(tmp_path, monkeypatch, last_ts=last)
    now = _now_ms(last)
    a = eng._pattern_evidence("SOL/USDT", now)
    b = eng._pattern_evidence("ETH/USDT", now)
    assert a is not b
    assert fake.calls == 4


def test_a_symbol_outside_the_index_is_still_none(tmp_path: Path, monkeypatch):
    last = 1_789_000_000_000
    eng, fake = _engine(tmp_path, monkeypatch, last_ts=last)
    assert eng._pattern_evidence("DOGE/USDT", _now_ms(last)) is None
    assert fake.calls == 0


def test_evidence_file_is_written_on_the_computing_call(tmp_path: Path, monkeypatch):
    """Panel dosyasi hesaplanan cagrida yazilir; onbellek isabetinde yeniden yazilmaz."""
    last = 1_789_000_000_000
    eng, _fake = _engine(tmp_path, monkeypatch, last_ts=last)
    now = _now_ms(last)
    eng._pattern_evidence("SOL/USDT", now)
    f = eng.cfg.state_path / "evidence" / "SOL_USDT.json"
    assert f.exists()
    stamp = f.stat().st_mtime_ns
    eng._pattern_evidence("SOL/USDT", now)
    assert f.stat().st_mtime_ns == stamp, "onbellek isabetinde dosya yeniden yazilmamali"

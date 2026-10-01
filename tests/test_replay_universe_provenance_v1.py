# -*- coding: utf-8 -*-
"""REPLAY EVRENI SESSIZCE DUSURULMEZ (2026-09-19).

OLCULEN KUSUR
-------------
`HistoricalReplay.load()` yeterli serisi olmayan sembolu hicbir kayit birakmadan atliyordu:
else dali, sayac ya da log YOKTU. Tek fail-closed nokta "hicbiri yuklenemedi" idi, yani
40 sembolun 39'u dusse bile kosu rc=0 ile biterdi. `ReplayResult.symbols` ise KURUCUYA
VERILEN (istenen) listedir ve arastirma metasi onu yaziyordu.

Sonuc: 10 coinlik arsivde 40 coinlik bir kosu baslatilabilir, "hatasiz" biter ve meta
"40 sembol" raporlar — yani 40 coinde olculmus gibi gorunen bir hukum, aslinda 10 coinde
kurulur. Bu projede "hatasiz bitti" defalarca veri kaniti sanildi (V14 arsiv provenansi).

SOZLESME
--------
1. Dusen her sembol SEBEBIYLE `result.skipped_symbols` icinde kayda gecer (NO_SERIES |
   TOO_FEW_BARS:<n>) ve `result.loaded_symbols` GERCEKTEN yuklenen evrendir.
2. `load(require_all=True)` eksik evrende ValueError atar — "N coinde olctuk" hukmu ancak
   bu bayrakla kurulabilir. Arastirma kosucusu (`run_rule.py`) varsayilan olarak boyle kosar.
3. `symbols` alani ISTENEN liste olarak kalir; rapor ikisini AYRI yazmalidir.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_quant_replay_e2e import _candles, _cfg  # noqa: E402
from tradingbot.history import HistoryStore  # noqa: E402
from tradingbot.replay import HistoricalReplay  # noqa: E402

HAVE = ("BTC/USDT", "AAA/USDT")
MISSING = "ZZZ/USDT"


def _store(tmp_path: Path, *, thin: str | None = None) -> HistoryStore:
    st = HistoryStore(tmp_path / "hist")
    for i, sym in enumerate(HAVE):
        st.write("futures", sym, "4h", _candles(seed=11 + i))
    if thin:                                   # seri VAR ama min_bars'in altinda
        st.write("futures", thin, "4h", _candles(n=120, seed=99))
    return st


def _replay(cfg, store, symbols):
    return HistoricalReplay(cfg, run_id="uni_prov", store=store, symbols=list(symbols),
                            market="futures", tf="4h", seed=7, min_bars=250)


def test_a_missing_symbol_is_recorded_with_a_reason_not_silently_dropped(tmp_path):
    cfg = _cfg(tmp_path)
    rp = _replay(cfg, _store(tmp_path), list(HAVE) + [MISSING])
    rp.load()
    res = rp.result
    assert sorted(res.loaded_symbols) == sorted(HAVE), "yuklenen evren gercek olmali"
    assert MISSING in res.skipped_symbols, "dusen sembol SESSIZ gecemez"
    assert res.skipped_symbols[MISSING] == "NO_SERIES"
    # `symbols` ISTENEN liste olarak kalir — rapor ikisini ayri yazabilsin diye.
    assert MISSING in res.symbols and len(res.symbols) == 3
    assert res.to_dict()["skipped_symbols"] == res.skipped_symbols, "provenans serilestirilmeli"


def test_a_series_that_is_too_short_reports_its_bar_count(tmp_path):
    cfg = _cfg(tmp_path)
    rp = _replay(cfg, _store(tmp_path, thin=MISSING), list(HAVE) + [MISSING])
    rp.load()
    reason = rp.result.skipped_symbols[MISSING]
    assert reason.startswith("TOO_FEW_BARS:"), reason
    assert reason.endswith(":120"), "kac bar bulundugu yazilmali — 'yok' ile 'az' ayni sey degil"


def test_require_all_refuses_to_run_on_an_incomplete_universe(tmp_path):
    """Eksik sembolle kosmak AYRI bir deneydir: acikca istenmedikce hata."""
    cfg = _cfg(tmp_path)
    rp = _replay(cfg, _store(tmp_path), list(HAVE) + [MISSING])
    with pytest.raises(ValueError) as e:
        rp.load(require_all=True)
    assert MISSING in str(e.value), "hata hangi sembolun eksik oldugunu SOYLEMELI"


def test_a_complete_universe_passes_require_all_and_reports_it(tmp_path):
    """ON KOSUL: bayrak her kosuyu reddetmiyor — tam evrende sessizce gecer."""
    cfg = _cfg(tmp_path)
    rp = _replay(cfg, _store(tmp_path), list(HAVE))
    rp.load(require_all=True)
    assert sorted(rp.result.loaded_symbols) == sorted(HAVE)
    assert rp.result.skipped_symbols == {}


def test_an_entirely_empty_archive_still_fails_closed(tmp_path):
    """Eski fail-closed nokta korundu: hicbiri yuklenemezse yine hata."""
    cfg = _cfg(tmp_path)
    st = HistoryStore(tmp_path / "bos")
    rp = _replay(cfg, st, list(HAVE))
    with pytest.raises(ValueError):
        rp.load()

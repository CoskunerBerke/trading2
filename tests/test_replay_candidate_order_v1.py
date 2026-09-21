# -*- coding: utf-8 -*-
"""ADAY SIRALAMA KANCASI (2026-09-19).

NEDEN
-----
Risk butcesi (`max_total_open_risk_pct / risk_per_trade_pct`) ayni anda yalniz birkac
pozisyona izin verir. Varsayilan yolda semboller ARSIV SIRASIYLA gezilir ve slotlar
"ilk uyan"a gider — canli kagit defterin davranisinin aynisi. On coinde makul, kirk
coinde DEGIL: ayni uc slota dort kat aday dusunce sonuc sinyal KALITESINI degil
`config.yaml` liste SIRASINI olcer.

OLCULDU (bn_archive, 6 kontrollu karsilastirma, funding kapsami TAM):
  40 coin LEHTE 2 / ALEYHTE 4; ortalama R 6'nin 5'inde DUSUK;
  M2 uc pencere bilesik +216% (10 coin) -> +100% (40 coin).

SOZLESME
--------
1. `candidate_order=None` -> davranis BIT BIT eskisi gibi (tek gecis, arsiv sirasi).
   Determinizm hash'i degismez.
2. Anahtar verilince ayni turdaki YENI GIRIS adaylari buyukten kucuge uygulanir.
3. CIKISLAR siralamaya GIRMEZ ve ONCE uygulanir — cikis butce acar, geciktirmek risk artirir.
4. Anahtar None dondurursa aday en sona duser; esitlikte arsiv sirasi korunur (kararli).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_quant_replay_e2e import _candles, _cfg  # noqa: E402
from tradingbot.history import HistoryStore  # noqa: E402
from tradingbot.replay import HistoricalReplay, walk_forward_windows  # noqa: E402

H4 = 14_400_000
T0 = 1_700_000_000_000
# KITLIK SART: risk butcesi (%6 / %2) ayni anda UC pozisyona izin verir. Adaydan cok slot
# varsa herkes girer ve SIRALAMA OLCULEMEZ — ilk yazimda test tam bu yuzden ayirt etmedi.
# Alti aday, uc slot: sira artik sonucu BELIRLER.
SYMS = ["AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT", "EEE/USDT", "FFF/USDT"]
SLOT = 3


def _store(tmp_path: Path) -> HistoryStore:
    st = HistoryStore(tmp_path / "hist")
    for i, sym in enumerate(SYMS):
        st.write("futures", sym, "4h", _candles(seed=21 + i, drift=0.0004 + i * 0.0002))
    return st


def _always_open(sym, t, fr, pos, rp):
    """Her sembolde ayni anda giris iste — slot yarisini GORUNUR kilar."""
    if pos is not None:
        return None
    df = fr.get("4h")
    if df is None or not len(df):
        return None
    close = float(df["close"].iloc[-1])
    return {"action": "OPEN", "direction": "LONG", "stop": close * 0.9, "targets": [],
            "leverage": 1, "reason": "TEST", "name": "test", "setup_type": "strategy",
            "signal_close": close}


def _replay(cfg, store, **kw):
    return HistoricalReplay(cfg, run_id=kw.pop("run_id", "cand"), store=store, symbols=list(SYMS),
                            market="futures", tf="4h", seed=5, min_bars=250,
                            strategy=_always_open, **kw)


def _run(rp):
    ws = walk_forward_windows(rp.result.start_ms, rp.result.end_ms, train_days=60, test_days=20, tf="4h")
    return rp.run(windows=ws)


def _acilan_sirasi(rp) -> list:
    """UYGULAMA sirasi — `_entry_meta`nin EKLEME sirasi.

    `result.trades` yalniz KAPANANLARI tasir; kural kendi kapanisini uretmediginde bos kalir.
    Zaman damgasina gore siralamak da ise yaramaz: ayni bardaki butun acilislar AYNI `t`
    degerini tasir ve siralama alfabetik sekmeye duser — ilk yazimda test tam bu yuzden
    yanlis sonuc verdi. Python sozlugu ekleme sirasini korur, `apply_action` da adaylari
    siralanmis sirada cagirir; aranan bilgi BURADADIR."""
    return [m.get("symbol") for m in rp._entry_meta.values()]


def _acilan_kume(rp) -> set:
    """Pozisyon ACMIS butun semboller (kapanmis olanlar dahil)."""
    return {m.get("symbol") for m in rp._entry_meta.values()} | {t.get("symbol") for t in rp.result.trades}


def test_default_path_is_byte_for_byte_unchanged(tmp_path):
    """ON KOSUL: kanca kapaliyken determinizm hash'i, siralamali yoldan BAGIMSIZ olarak sabit."""
    cfg = _cfg(tmp_path / "a")
    rp = _replay(cfg, _store(tmp_path), run_id="d1")
    rp.load()
    a = _run(rp)
    cfg2 = _cfg(tmp_path / "b")
    rp2 = _replay(cfg2, _store(tmp_path), run_id="d2")
    rp2.load()
    b = _run(rp2)
    assert a.determinism_hash == b.determinism_hash, "varsayilan yol deterministik olmali"
    assert rp.candidate_order is None


def test_the_ranked_path_changes_which_candidate_wins_the_slot(tmp_path):
    """Ayni adaylar, ZIT siralama -> farkli semboller slotu kazanir.

    Bu testin ASIL isi: siralamanin GERCEKTEN baglandigini gostermek. Iki kol da IKI FAZLI
    yoldan gecer, yani tek degisken SIRALAMADIR ("iki fazli olmak" degil).
    """
    cfg_a = _cfg(tmp_path / "ra")
    rp_a = _replay(cfg_a, _store(tmp_path), run_id="r_asc",
                   candidate_order=lambda sym, act: float(SYMS.index(sym)))
    rp_a.load()
    _run(rp_a)
    ilk_a = _acilan_sirasi(rp_a)

    cfg_b = _cfg(tmp_path / "rb")
    rp_b = _replay(cfg_b, _store(tmp_path), run_id="r_desc",
                   candidate_order=lambda sym, act: -float(SYMS.index(sym)))
    rp_b.load()
    _run(rp_b)
    ilk_b = _acilan_sirasi(rp_b)

    kume_a, kume_b = _acilan_kume(rp_a), _acilan_kume(rp_b)
    assert ilk_a and ilk_b, "on kosul: iki kol da pozisyon acmali"
    # ON KOSUL: kitlik gercekten olustu mu (herkes girdiyse test bir sey kanitlamaz)
    assert len(kume_a) < len(SYMS), "on kosul: butun adaylar girdiyse siralama olculemez"
    assert ilk_a[0] != ilk_b[0], (
        "zit siralama ayni sembolu once acti — anahtar BAGLANMIYOR (%s vs %s)" % (ilk_a[0], ilk_b[0]))
    # Zit anahtar -> zit uctan secim: kazananlar AYRISMALI.
    assert kume_a != kume_b, "zit siralama ayni sembol kumesini acti (%s vs %s)" % (
        sorted(kume_a), sorted(kume_b))


def test_an_unrankable_candidate_falls_to_the_back_not_the_front(tmp_path):
    """Anahtar None/hata dondurursa aday ONE GECEMEZ; kosu da durmaz."""
    cfg = _cfg(tmp_path / "u")

    def _kotu(sym, act):
        if sym == "AAA/USDT":
            raise ValueError("siralanamaz")
        if sym == "CCC/USDT":
            return 1.0                              # yalniz CCC siralanabilir
        return None

    rp = _replay(cfg, _store(tmp_path), run_id="unrank", candidate_order=_kotu)
    rp.load()
    _run(rp)
    acilan = _acilan_sirasi(rp)
    assert acilan, "siralama arizasi kosuyu durdurmamali"
    assert acilan[0] == "CCC/USDT", (
        "siralanabilen tek aday ONCE acilmaliydi; acilan sira: %s" % acilan[:4])

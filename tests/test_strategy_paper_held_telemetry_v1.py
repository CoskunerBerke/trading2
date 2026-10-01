# -*- coding: utf-8 -*-
"""TUTULAN POZISYONUN HUKMU BAYATLAMAZ (2026-09-19).

OLCULEN KUSUR
-------------
`last_actions[sym]` sinyalsiz turda yalniz BIR KEZ yaziliyordu (`sym not in self.last_actions`)
ve `last_actions` yeniden baslatmada geri YUKLENMIYOR. Boylece acik bir pozisyonun son hukmu,
yeniden baslatmadan sonraki ILK turda donup kaliyordu.

Uretimde gorunumu (VPS, 2026-09-19 okumasi): 3b0ae8e dagitimi 2026-09-18 21:13Z'de yapildi,
ilk tur 21:16:01'de kostu; T2'nin SOL/BNB/ZEN ve M2'nin SOL/BNB pozisyonlarinin hukmu saatler
sonra hala `{"action": "NONE", "reason": "NO_SIGNAL", "at": "2026-09-18T21:16:01+00:00"}`
gorunuyordu — defterler o sirada 396. turdaydi. Cikis yolu SAGLAMDI (her tur `decide_for`
cagriliyor), bayatlayan yalniz TELEMETRIYDI; ama bu, "pozisyon hapsoldu" suphesini dogurdu ve
ayirt edilemedi. Sessiz kusurlarin tekrar tekrar cikti bir projede, "degerlendirildi mi" sorusu
makineyle olculebilir olmalidir.

SOZLESME
--------
Sinyalsiz her turda kayit TAZELENIR ve `tour` (defterin tur sayaci) + `held` (pozisyon acik mi)
alanlarini tasir. `reason` degismez: flat sembolde "NO_SIGNAL" mevcut sozlesmedir
(bkz. test_box_theory_book_v15.py: flat sembol NO_SIGNAL bekler).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from test_strategy_paper_engine_v1 import SYMS, _install  # noqa: E402

BOOK = "strategy_paper"


def _ov() -> dict:
    return _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime"},
                            "chart_analysis": {"enabled": False},
                            "news": {"enabled": False}}


def _eng(root: Path, mp):
    eng = _engine(root, mp, _ov(), symbols=2, equity=EQUITY)
    _force_triggers(mp, False)
    _install(eng, mp, btc_up=True, coin_above=True, market="USDM_PERP", btc_market="USDM_PERP")
    return eng


def _tour(eng) -> None:
    eng.tour(do_scan=False, obsidian=False, charts=False)


def _book(eng):
    return {b.key: b for b in eng.strategy_books}[BOOK]


def test_held_position_verdict_refreshes_every_tour_after_a_restart(tmp_path, monkeypatch):
    """Yeniden baslatmadan sonra tutulan pozisyonun hukmu her turda tazelenir."""
    eng = _eng(tmp_path, monkeypatch)
    _tour(eng)
    book = _book(eng)
    held = sorted(book.ledger.positions)
    assert held, "on kosul: ilk tur pozisyon acmali (aksi halde test bir sey kanitlamaz)"

    # YENIDEN BASLATMA: `last_actions` ozet dosyasindan geri YUKLENMEZ, bos baslar.
    book.last_actions.clear()

    _tour(eng)
    tour_a = book.counters["tours"]
    snap_a = {s: dict(book.last_actions[s]) for s in held}
    for s in held:
        assert snap_a[s]["action"] == "NONE" and snap_a[s]["reason"] == "NO_SIGNAL"
        assert snap_a[s]["held"] is True, "pozisyon acik: kayit bunu ilan etmeli"
        assert snap_a[s]["tour"] == tour_a

    _tour(eng)
    tour_b = book.counters["tours"]
    assert tour_b == tour_a + 1, "on kosul: ikinci tur gercekten kosmali"
    assert sorted(book.ledger.positions) == held, "on kosul: pozisyonlar hala acik (cikis sinyali yok)"

    for s in held:
        rec = book.last_actions[s]
        # ASIL ISPAT: kayit donmadi, yeni turu gosteriyor.
        assert rec["tour"] == tour_b, (
            "tutulan %s icin hukum %d. turda dondu (beklenen %d) — panelde 'degerlendirildi, "
            "cikis sinyali yok' ile 'artik degerlendirilmiyor' ayirt edilemez" % (s, rec["tour"], tour_b))
        assert rec["held"] is True


def test_flat_symbol_keeps_the_no_signal_contract_and_declares_not_held(tmp_path, monkeypatch):
    """Pozisyonu olmayan sembolde sozlesme degismedi: NO_SIGNAL, held=False."""
    eng = _engine(tmp_path, monkeypatch, _ov() | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime"}},
                  symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    # coin_above=False -> giris sinyali yok, defter flat kalir
    _install(eng, monkeypatch, btc_up=True, coin_above=False, market="USDM_PERP", btc_market="USDM_PERP")
    _tour(eng)
    book = _book(eng)
    assert not book.ledger.positions, "on kosul: defter flat olmali"
    for s in SYMS:
        rec = book.last_actions.get(s)
        if rec is None or rec.get("action") != "NONE":
            continue
        assert rec["reason"] == "NO_SIGNAL", "flat sembol sozlesmesi korunmali"
        assert rec["held"] is False
        assert rec["tour"] == book.counters["tours"]


def test_book_scope_field_is_enforced_and_never_orphans_an_open_position(tmp_path, monkeypatch):
    """Defter basina `symbols` BAGLAR; kapsam disi acik pozisyon yine de yonetilir.

    REGRESYON KORUMASI — mevcut davranisi cakiler, yeni davranis getirmez.

    Uygulama noktasi `engine_v3.py:2316` (`syms = book.symbols or universe`) ve kapsam birlesimi
    `:2303`; `strategy_paper.StrategyBook` filtrelemez, kendisine verilen listeyi gezer. Bu ayrim
    kayda degerdir: 2026-09-19'da bu sozlesme, paket ici arama `head -20` ile KESILDIGI icin
    "ilan edilip uygulanmayan olu alan" sanildi ve gereksiz bir ikinci filtre yazildi. Geri alma
    sondasi bunu yakaladi: sonda kaldirildiginda test DUSMEDI, yani ilk teshis yanlisti.
    Ders — kesilmis bir arama sonucu, tam sonuc gibi okunmaz.

    Kapsam daralmasinin bir pozisyonu YONETIMSIZ birakmamasi V13 dersidir (ZEN/USDT, 2026-09-13):
    defterin acik pozisyonlari evren disinda kalsa bile kural cikisi ve stop icin gezilir.
    """
    ov = _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                             "symbols": ["ETH/USDT"]},
                          "chart_analysis": {"enabled": False}, "news": {"enabled": False}}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=2, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    _install(eng, monkeypatch, btc_up=True, coin_above=True, market="USDM_PERP", btc_market="USDM_PERP")
    _tour(eng)
    book = _book(eng)
    assert book.symbols, "on kosul: defter kapsami config'ten okunmali"
    assert sorted(book.ledger.positions) == ["ETH/USDT"], (
        "kapsam disi SOL/USDT acilmamaliydi — alan baglamiyorsa bu liste iki sembol olur")

    # Kapsam disi bir pozisyon ELLE defterin icine konursa, tur onu yine de gezmeli (cikis + stop).
    book.symbols = ["SOL/USDT"]                     # kapsam degisti: ETH artik kapsam disi ama ACIK
    _tour(eng)
    assert "ETH/USDT" in book.last_actions, "kapsam disi kalan acik pozisyon yonetimsiz birakilamaz"
    assert book.last_actions["ETH/USDT"].get("held") is True


def test_a_write_without_a_rule_pass_does_not_look_fresh(tmp_path, monkeypatch):
    """`generated_at` tazelenir ama kural dongusunun kendi zamani TAZELENMEZ.

    60 sn'lik cikis izleyicisi (`engine_v3._strategy_paper_exit_check`) her dakika `book.save()`
    cagirir ve `generated_at`i tazeler; kural dongusunu (`step`) KOSTURMAZ. Ana bot boru hattindaki
    bir istisna `_strategy_paper_tour`a hic ulasilmamasina yol acarsa (engine_v3.py:1301 korumasiz,
    onunde 66 korumasiz ifade var) defter disaridan CANLI gorunurdu: taze damga + onceki turun
    hepsi-yesil `data_checks`. Bayatligi olcen hicbir kod yoktu.
    """
    import json
    from datetime import timedelta

    from tradingbot.core import utc_now

    eng = _eng(tmp_path, monkeypatch)
    _tour(eng)
    book = _book(eng)
    tour_no = book.rule_tour
    assert book.rule_evaluated_at, "kural kostu: zamani kaydedilmeli"
    assert tour_no == book.counters["tours"]

    # 60 sn izleyicisinin yaptigi: step YOK, yalniz save.
    later = utc_now() + timedelta(seconds=900)
    book.save({}, later)
    doc = json.loads((book.state_dir / book.summary_file).read_text(encoding="utf-8")) \
        if (book.state_dir / book.summary_file).exists() else None
    if doc is None:                                    # ozet motorun state kokune yazilir
        doc = json.loads((Path(eng.cfg.state_path) / book.summary_file).read_text(encoding="utf-8"))

    assert doc["rule_tour"] == tour_no, "kural kosmadi: tur numarasi ILERLEMEMELI"
    assert doc["rule_evaluated_this_write"] is False, "bu yazmada kural kosmadi, oyle ilan edilmeli"
    assert doc["rule_stale_s"] is not None and doc["rule_stale_s"] >= 800, (
        "kural dongusunun bayatligi olculebilir olmali; olculen=%s" % doc["rule_stale_s"])
    assert doc["generated_at"] != doc["rule_evaluated_at"], (
        "yazma ani ile kural ani AYNI alan degildir — karisirsa arizali defter canli gorunur")


def test_a_real_rule_pass_declares_itself_fresh(tmp_path, monkeypatch):
    """ON KOSUL: bayrak her yazmada False degil — gercek tur taze ilan eder."""
    import json

    eng = _eng(tmp_path, monkeypatch)
    _tour(eng)
    book = _book(eng)
    p = Path(eng.cfg.state_path) / book.summary_file
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["rule_evaluated_this_write"] is True
    # `rule_stale_s` bir OLCUMDUR (yazma ani - kuralin son kostugu an); bayrak ondan TURETILMEZ.
    # Once bayrak `< 1 sn` ile turetiliyordu ve tam paket kosusunda, yuk altinda step ile save
    # arasi bir saniyeyi asinca bu test dustu — duvar saatine bagli iddia, ayni dosyada onarilan
    # panel kusurunun kardesi. Burada yalniz olculebilir oldugu ve negatif olmadigi aranir.
    assert doc["rule_stale_s"] is not None and doc["rule_stale_s"] >= 0.0

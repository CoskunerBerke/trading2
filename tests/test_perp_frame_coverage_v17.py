# -*- coding: utf-8 -*-
"""V17 — PERPETUAL ÇERÇEVE KAPSAMI.

Üretimde görülen kusur (2026-09-18): `perp_frames` yalnız 1d/4h/1h çekiyordu ve ilan edilen demet
(`_need`) de oydu. Box defterinin okuduğu **5m TradingView SPOT** akışına düşüyordu; provenans yine
`USDM_PERP` diyordu çünkü YALNIZ ilan edilen dilimler denetlenir. Sonuç: defter, spot mumundan
hesaplanmış tetik/giriş/stop ile PERPETUAL pozisyon açıyor ve bunu göremiyordu. Ayrıca spot
listelemesi olmayan semboller (1000PEPE, 1000SHIB, XMR) 5m'i hiç alamıyordu.

Bu dosya kuralı çiviler: **bir defter hangi dilimi okuyorsa, o dilim perpetual kaynaktan gelmeli;
gelemiyorsa hüküm USDM_PERP OLMAMALI.**
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _profile  # noqa: E402
from tradingbot import paper_rules  # noqa: E402
from tradingbot.engine import TradingEngine  # noqa: E402

BOX = {"name": "b1_box_fade", "state_dir": "strategy_paper_box",
       "rule_params": {"min_stop_pct": 2.22, "exit_kind": "box_mid", "leverage": 3}}


#: Giris evreni ACIK olmali: perpetual cerceve yolu (`futures_required`) YALNIZ evren sembolleri
#: icin isler. Kapaliyken `perp_frames` hic cagrilmaz ve test hicbir sey olcmez.
UNIVERSE = ["ETH/USDT", "SOL/USDT"]


def _ov(box: bool) -> dict:
    return _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                               "extra": [dict(BOX)] if box else []},
                            "entry_universe": {"enabled": True, "symbols": list(UNIVERSE)},
                            "chart_analysis": {"enabled": False}, "news": {"enabled": False}}


def test_the_frame_limit_table_covers_every_timeframe_a_book_can_ask_for():
    """Kayıttaki HER dilimin bar tavanı tanımlı olmalı; eksikse `perp_frames` KeyError verir."""
    need = {tf for v in paper_rules.VARIANTS for tf in paper_rules.rule_timeframes(v)}
    missing = sorted(need - set(TradingEngine.PERP_FRAME_LIMITS))
    assert not missing, "bu dilimler için bar tavanı YOK: %s" % missing


def test_perp_frames_fetches_exactly_the_timeframes_it_is_asked_for(monkeypatch):
    asked: list[str] = []

    class _Ex:
        def fetch_ohlcv(self, sym, tf, limit):
            asked.append(tf)
            base = 1_700_000_000_000
            return [[base + i * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(60)]

    eng = TradingEngine.__new__(TradingEngine)
    eng._fu = _Ex()
    monkeypatch.setattr(TradingEngine, "_fut", lambda self: self._fu)
    out = eng.perp_frames("BTC/USDT", timeframes=("1d", "5m"))
    assert asked == ["1d", "5m"], asked
    assert set(out) == {"1d", "5m"}
    asked.clear()
    eng.perp_frames("BTC/USDT")
    assert asked == list(TradingEngine.PERP_BASE_TIMEFRAMES), "demet verilmezse TABAN kullanılır"


def test_an_unknown_timeframe_is_refused_instead_of_silently_skipped(monkeypatch):
    class _Ex:
        def fetch_ohlcv(self, sym, tf, limit):
            return []

    eng = TradingEngine.__new__(TradingEngine)
    eng._fu = _Ex()
    monkeypatch.setattr(TradingEngine, "_fut", lambda self: self._fu)
    with pytest.raises(KeyError):
        eng.perp_frames("BTC/USDT", timeframes=("1d", "3m"))


def test_the_engine_asks_the_perpetual_source_for_the_books_timeframes(tmp_path, monkeypatch):
    """AYIRT EDİCİ: box defteri AÇIKKEN 5m perpetual kaynaktan istenmeli, KAPALIYKEN istenmemeli."""
    seen: dict[str, tuple] = {}

    def _spy(self, symbol, timeframes=None):
        seen[symbol] = tuple(timeframes or self.PERP_BASE_TIMEFRAMES)
        raise RuntimeError("perp yok")            # akışı kes: yalnız İSTENEN demeti ölçüyoruz

    monkeypatch.setattr(TradingEngine, "perp_frames", _spy)
    for box, want_5m in ((True, True), (False, False)):
        seen.clear()
        eng = _engine(tmp_path / ("box" if box else "nobox"), monkeypatch, _ov(box), symbols=1, equity=EQUITY)
        eng.tour(do_scan=False, obsidian=False, charts=False)
        assert seen, "perp_frames hiç çağrılmadı"
        tfs = next(iter(seen.values()))
        assert ("5m" in tfs) is want_5m, (box, tfs)
        assert {"1d", "4h", "1h"} <= set(tfs), "taban dilimler HER ZAMAN istenmeli: %s" % (tfs,)


def test_a_missing_required_timeframe_must_not_be_declared_as_perpetual(tmp_path, monkeypatch):
    """KUSURUN TA KENDİSİ: perpetual kaynak 5m veremiyorsa hüküm USDM_PERP OLMAMALI ve giriş kapanmalı.

    Eskiden `_need` yalnız 1d/4h/1h olduğu için 5m eksikken bile USDM_PERP deniyordu; defter o zaman
    SPOT 5m barlarıyla perpetual pozisyon açıyordu.
    """
    def _partial(self, symbol, timeframes=None):
        import pandas as pd
        base = 1_700_000_000_000
        df = pd.DataFrame([[base + i * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(60)],
                          columns=["timestamp", "open", "high", "low", "close", "volume"])
        return {tf: df.copy() for tf in ("1d", "4h", "1h")}          # 5m BİLEREK EKSİK

    monkeypatch.setattr(TradingEngine, "perp_frames", _partial)
    eng = _engine(tmp_path, monkeypatch, _ov(True), symbols=1, equity=EQUITY)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    prov = getattr(eng, "_frame_provenance", {}) or {}
    assert prov, "provenans yazılmadı"
    for sym, p in prov.items():
        assert p.get("market") != "USDM_PERP", (
            "%s: 5m perpetual kaynaktan GELMEDİĞİ halde USDM_PERP ilan edildi -> defter SPOT mumuyla "
            "perpetual pozisyon açabilir" % sym)


def test_a_code_error_in_the_perp_path_is_not_reported_as_missing_market_data(tmp_path, monkeypatch):
    """KOD HATASI ile VERİ YOKLUĞU ayrı gerekçelerle kaydedilmeli.

    İkisi aynı sepete girerse bir imza hatası bütün evreni sessizce SPOT'a düşürür ve bu, panelde
    ve kayıtta "borsa veri vermedi" gibi görünür. 2026-09-18'de tam bu oldu: `perp_frames`e yeni bir
    parametre eklendi, test taklitleri eski imzadaydı, TypeError `FUTURES_FRAMES_UNAVAILABLE` diye
    maskelendi."""
    def _boom(self, symbol, timeframes=None):
        raise TypeError("eski imza")

    monkeypatch.setattr(TradingEngine, "perp_frames", _boom)
    eng = _engine(tmp_path / "bug", monkeypatch, _ov(True), symbols=1, equity=EQUITY)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    reasons = {s: p.get("reason", "") for s, p in (getattr(eng, "_frame_provenance", {}) or {}).items()}
    assert reasons, "provenans yazılmadı"
    assert all(r.startswith("PERP_FRAMES_CODE_ERROR:") for r in reasons.values()), reasons
    assert all(p.get("market") == "SPOT" and p.get("entry_ok") is False
               for p in eng._frame_provenance.values()), "kod hatasında da giriş KAPALI kalmalı"


def test_a_provider_outage_is_still_reported_as_missing_market_data(tmp_path, monkeypatch):
    """AYIRT EDİCİ ikiz: sağlayıcı arızasında gerekçe DEĞİŞMEMELİ (kod hatasıyla karışmasın)."""
    def _down(self, symbol, timeframes=None):
        raise RuntimeError("perp feed down")

    monkeypatch.setattr(TradingEngine, "perp_frames", _down)
    eng = _engine(tmp_path / "down", monkeypatch, _ov(True), symbols=1, equity=EQUITY)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    reasons = {s: p.get("reason", "") for s, p in (getattr(eng, "_frame_provenance", {}) or {}).items()}
    assert reasons and all(r == "FUTURES_FRAMES_UNAVAILABLE" for r in reasons.values()), reasons

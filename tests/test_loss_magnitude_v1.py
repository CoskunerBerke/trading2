"""KAYIP BÜYÜKLÜĞÜ SABİTİ ONARIMI — regresyon testleri.

KUSUR: `opportunity.hierarchical_expectancy` `avg_loss_r` olarak `default_loss_r`'yi (sabit 1.0)
döndürüyordu. Donmuş üretim verisinde 2797/2797 adayın tamamı `avg_loss_r = -1.0` taşıyordu.
Oysa 29 kapanışın 22 kaybının GERÇEKLEŞMİŞ NET büyüklüğü ortalama 1.0774R'dir (ortancası 1.0649,
pop. s.s. 0.0831, %95 GA 1.042..1.113 — yani 1.0 aralığın DIŞINDA). Fark saf uygulama açığıdır:
stop'un ötesine geçen doldurma + fee/funding sürüklemesi.

Bu dosya dört değişmezi kilitler:

1. **Cebirsel sadeleşme** — `w = 1` iken kayıp büyüklüğü `gross`tan TAMAMEN sadeleşir ve
   `gross == exp_r` olur; sabit yalnız soğuk başlangıç harmanının `(1 − w)` payından ısırır.
   Etkinin büyüklüğüne dair her iddia bunu hesaba katmak ZORUNDADIR.
2. **Soğuk başlangıç** — kayıp verisi YOKKEN davranış bugünküyle BİREBİR aynı (tam 1.0).
3. **Daralma** — kestirim örneklem büyüdükçe tekdüze veriye, belirsizlik büyüdükçe 1.0'a gider.
4. **Geriye uyumluluk** — `loss_r` düğümü olmayan ESKİ `learn_v2.json` yüklenir ve learner'ın
   kendi ders kaydından KAYIPSIZ geri doldurulur; tekrar yükleme çift saymaz.

Ayrıca sabitin geri yapıştırılmasını (hem `1.0`'a dönüş hem de `1.0774` gibi bir literalin
kopyalanması) yakalayan bir test vardır.
"""
from __future__ import annotations

import inspect
import json
import math
import re
from pathlib import Path

import pytest

from tradingbot import opportunity as O
from tradingbot.decision_gates import GateLedger
from tradingbot.learn.learner_v2 import LearnerV2
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.model import (LOSS_PRIOR_R, MAX_LOSS_R, MIN_LOSS_SD_R, HierarchicalRate,
                                    loss_magnitude_estimate)
from tradingbot.learn.registry import ModelRegistry


# ------------------------------------------------------------------ yardımcılar
class _Stub:
    """`hierarchical_expectancy`nin gördüğü asgari learner yüzeyi."""

    def __init__(self, *, p_win: float, n_win: float, exp_r: float, n_exp: float,
                 losses: list[float] | None = None) -> None:
        self.win = _Node(p_win, n_win)
        self.exp_r = _Node(exp_r, n_exp)
        self._losses = list(losses or [])

    def loss_magnitude_r(self):
        if not self._losses:
            return loss_magnitude_estimate(0.0, LOSS_PRIOR_R, 0.0)
        n = float(len(self._losses))
        m = sum(self._losses) / n
        var = sum((x - m) ** 2 for x in self._losses) / n
        return loss_magnitude_estimate(n, m, math.sqrt(var))


class _Node:
    def __init__(self, value: float, n: float) -> None:
        self._v, self._n = float(value), float(n)

    def estimate(self, **_kw):
        return self._v, self._n


class _NoLossSurface:
    """`loss_magnitude_r` HİÇ olmayan learner — eski (sürüm öncesi) yüzey."""

    def __init__(self, stub: _Stub) -> None:
        self.win, self.exp_r = stub.win, stub.exp_r


def _learner(tmp: Path) -> LearnerV2:
    return LearnerV2(TradeMemory(tmp / "trade_memory.jsonl"),
                     ModelRegistry(tmp / "models.json"), state_path=tmp / "learn_v2.json")


def _close(tid="T1", symbol="AAA/USDT", r=-1.0, regime="TREND_UP", setup="pullback", side="LONG"):
    return {"id": tid, "trade_id": tid, "symbol": symbol, "side": side, "setup_type": setup,
            "r_multiple": r, "net_pnl": r, "exit_reason": "stop",
            "closed_at": "2026-09-03T00:00:00+00:00", "mae_pct": -1.0, "mfe_pct": 0.5,
            "features": {"regime": regime, "setup_type": setup, "direction": side}}


def _gross(stats: dict, p: float | None = None) -> float:
    """`assess`in brüt formülü — AYNI kayıp büyüklüğüyle."""
    a = O.assess(symbol="X/USDT", side="LONG", setup="s", gates=GateLedger(),
                 p_win=stats["p_win"] if p is None else p, avg_win_r=stats["avg_win_r"],
                 avg_loss_r=stats["avg_loss_r"], sample_size=stats["sample_size"],
                 cost_pct_notional=0.2, stop_dist_pct=3.0,
                 expectancy_basis=stats["expectancy_basis"])
    return a.gross_expectancy_r, a


# ============================================================ 1) cebirsel sadeleşme (w = 1)
@pytest.mark.parametrize("exp_r", [-0.4, 0.0, 0.35, 1.2])
@pytest.mark.parametrize("loss_hist", [None, [1.05, 1.10, 1.30, 1.02, 0.95, 1.12]])
def test_01_loss_constant_cancels_algebraically_at_blend_weight_one(exp_r, loss_hist):
    """`w = 1` iken `gross == exp_r`; kayıp büyüklüğü NE OLURSA OLSUN sonuç DEĞİŞMEZ.

    `blend_n → 0` yapıldığında `w = n / (n + 1e-9) ≈ 1` olur. İki farklı kayıp büyüklüğüyle
    üretilen brüt beklenti hem birbirine hem de `exp_r`ye eşit çıkmalıdır. Bu, etkinin YALNIZ
    soğuk başlangıç payından geldiğini kanıtlar.
    """
    kw = dict(symbol="AAA/USDT", side="LONG", setup="pullback", regime="TREND_UP", blend_n=0.0)
    s_flat = O.hierarchical_expectancy(learner=_NoLossSurface(_Stub(p_win=0.45, n_win=30, exp_r=exp_r, n_exp=30)), **kw)
    s_est = O.hierarchical_expectancy(learner=_Stub(p_win=0.45, n_win=30, exp_r=exp_r, n_exp=30, losses=loss_hist), **kw)
    if loss_hist:
        assert s_est["avg_loss_r"] > s_flat["avg_loss_r"], "kestirim gerçekten farklı olmalı"
    g_flat, _ = _gross(s_flat)
    g_est, _ = _gross(s_est)
    assert g_flat == pytest.approx(exp_r, abs=1e-7)
    assert g_est == pytest.approx(exp_r, abs=1e-7)
    assert g_est == pytest.approx(g_flat, abs=1e-7)


def test_01b_effect_enters_only_through_the_cold_start_share():
    """`Δgross = −(1 − w)·(1 − p)·ΔL` — kapalı form, aynı p her iki yerde kullanıldığında."""
    p, exp_r = 0.45, 0.2
    for blend_n, n in ((20.0, 4.0), (20.0, 24.0), (5.0, 10.0)):
        stub = _Stub(p_win=p, n_win=n, exp_r=exp_r, n_exp=n, losses=[1.08] * 40)
        kw = dict(symbol="A/USDT", side="LONG", setup="s", regime=None, blend_n=blend_n)
        s0 = O.hierarchical_expectancy(learner=_NoLossSurface(stub), **kw)
        s1 = O.hierarchical_expectancy(learner=stub, **kw)
        w = n / (n + blend_n)
        predicted = -(1.0 - w) * (1.0 - p) * (s1["avg_loss_r"] - s0["avg_loss_r"])
        assert _gross(s1)[0] - _gross(s0)[0] == pytest.approx(predicted, abs=1e-6)
        assert predicted < 0, "kayıp büyüklüğü artınca edge KÜÇÜLMELİ"


# ============================================================ 2) soğuk başlangıç
def test_02_cold_start_is_exactly_todays_behaviour(tmp_path):
    """Kayıp verisi yoksa `avg_loss_r` TAM 1.0 — ne 1.0774 ne başka bir şey."""
    assert loss_magnitude_estimate(0.0, 1.4, 0.3)[0] == 1.0
    fresh = _learner(tmp_path)
    assert fresh.loss_magnitude_r()[0] == 1.0
    s = O.hierarchical_expectancy(learner=fresh, symbol="A/USDT", side="LONG", setup="s", regime=None)
    assert s["avg_loss_r"] == 1.0
    # `loss_magnitude_r` yüzeyi HİÇ yoksa da (eski learner nesnesi) 1.0'a düşülür.
    s2 = O.hierarchical_expectancy(learner=_NoLossSurface(_Stub(p_win=0.5, n_win=3, exp_r=0.1, n_exp=3)),
                                   symbol="A/USDT", side="LONG", setup="s", regime=None)
    assert s2["avg_loss_r"] == 1.0
    assert s2["provenance"]["loss_magnitude"] == {"source": "default"}


def test_02b_broken_loss_surface_falls_back_and_does_not_raise():
    """Kayıp istatistiği PATLARSA motor durmaz; sabit varsayılana dönülür (fail-safe)."""
    class _Boom(_NoLossSurface):
        def loss_magnitude_r(self):
            raise RuntimeError("bozuk durum")

    s = O.hierarchical_expectancy(learner=_Boom(_Stub(p_win=0.5, n_win=3, exp_r=0.1, n_exp=3)),
                                  symbol="A/USDT", side="LONG", setup="s", regime=None)
    assert s["avg_loss_r"] == 1.0
    assert s["provenance"]["loss_magnitude"]["source"] == "default_fallback"


# ============================================================ 3) daralma
def test_03_shrinkage_is_monotone_in_sample_size():
    """Aynı dağılım, artan n → kestirim 1.0'dan örneklem ortalamasına TEKDÜZE yaklaşır."""
    mean, sd = 1.0774, 0.0831
    prev = LOSS_PRIOR_R
    for n in (0, 1, 2, 5, 10, 22, 50, 200, 5000):
        est, meta = loss_magnitude_estimate(n, mean, sd)
        assert est >= prev, f"n={n} kestirimi düştü"
        assert LOSS_PRIOR_R <= est <= mean + 1e-9
        assert 0.0 <= meta["shrink_w"] <= 1.0
        prev = est
    assert loss_magnitude_estimate(5000, mean, sd)[0] == pytest.approx(mean, abs=2e-3)


def test_03b_more_dispersion_shrinks_harder_toward_one():
    """Belirsizlik kestirimi ŞİŞİRMEZ: aynı n ve ortalama, daha büyük s.s. → 1.0'a daha yakın."""
    prev = None
    for sd in (MIN_LOSS_SD_R, 0.20, 0.40, 0.80):
        est = loss_magnitude_estimate(22, 1.0774, sd)[0]
        if prev is not None:
            assert est < prev, f"sd={sd} daha çok daralmalıydı"
        prev = est
    # TABAN: ölçülen s.s. tabanın altındaysa taban kullanılır — tek bir sıkı örneklem
    # (n=1'de s.s. tanımı gereği 0'dır) kestirimi ele geçiremez.
    pinned = loss_magnitude_estimate(22, 1.0774, MIN_LOSS_SD_R)[0]
    assert loss_magnitude_estimate(22, 1.0774, 0.0)[0] == pinned
    assert loss_magnitude_estimate(22, 1.0774, MIN_LOSS_SD_R / 2)[0] == pinned


def test_03c_estimate_is_one_sided_and_bounded():
    """1.0'ın ALTINA inilmez (NET R zaten fee taşır) ve üst sınır aşılmaz (fail-safe)."""
    assert loss_magnitude_estimate(500, 0.80, 0.05)[0] == LOSS_PRIOR_R
    assert loss_magnitude_estimate(500, 0.999, 0.01)[0] == LOSS_PRIOR_R
    est, meta = loss_magnitude_estimate(10_000, 9.0, 0.01)
    assert est == MAX_LOSS_R and meta["clamped"] is True


def test_03d_matches_the_measured_production_sample_without_pasting_it():
    """22 gerçek kaybın (ort. 1.0774, s.s. 0.0831) kestirimi istenen aralıkta ve 1.0774 DEĞİL."""
    est, meta = loss_magnitude_estimate(22, 1.077432, 0.083071)
    assert 1.06 <= est <= 1.08
    assert est != pytest.approx(1.077432, abs=1e-4), "ham ortalama daraltılmadan kullanılmamalı"
    assert 0.0 < meta["shrink_w"] < 1.0


# ============================================================ 4) geriye uyumluluk
def _old_state(losses: list[float], wins: list[float]) -> dict:
    """`loss_r` düğümü OLMAYAN eski biçim durum dosyası (schema_version 2)."""
    win = HierarchicalRate(10.0, 0.5)
    exp_r = HierarchicalRate(10.0, 0.0)
    lessons = []
    for i, r in enumerate([-abs(x) for x in losses] + [abs(x) for x in wins]):
        sym, setup, side, reg = "AAA/USDT", "pullback", "LONG", "TREND_UP"
        win.add(1.0 if r > 0 else 0.0, regime=reg, leaves=(f"{sym}|{setup}", sym))
        exp_r.add(r, regime=reg, leaf=f"{setup}|{side}")
        lessons.append({"id": f"T{i}", "symbol": sym, "side": side, "r": r, "won": r > 0,
                        "exit": "stop", "at": "2026-09-03T00:00:00+00:00", "codes": [],
                        "setup": setup, "regime": reg})
    return {"schema_version": 2, "updated_at": "2026-09-03T00:00:00+00:00", "win": win.to_dict(),
            "exp_r": exp_r.to_dict(), "agent_hit": HierarchicalRate(10.0, 0.5).to_dict(),
            "n_closed": len(lessons), "lessons": lessons, "calibrator": {}, "last_metrics": {},
            "baseline_metrics": {}}


def test_04_old_state_file_loads_and_backfills_losslessly(tmp_path):
    """`loss_r` içermeyen ESKİ dosya yüklenir; kayıplar learner'ın kendi derslerinden kurulur."""
    losses = [1.05, 1.10, 1.30, 1.02, 1.20]
    p = tmp_path / "learn_v2.json"
    old = _old_state(losses, [2.0, 1.5])
    assert "loss_r" not in old
    p.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    lv = _learner(tmp_path)
    assert lv.n_closed == 7 and len(lv.lessons) == 7        # mevcut alanlar KORUNDU
    assert lv.loss_r.stats[""].n == len(losses)             # yalnız KAYIPLAR sayıldı
    assert lv.loss_r.stats[""].mean == pytest.approx(sum(losses) / len(losses))
    assert lv.loss_magnitude_r()[0] > 1.0
    # hiyerarşi de dolar (ileride kırılım için) — ata düğümler bir kez sayılır
    assert lv.loss_r.stats["regime:TREND_UP"].n == len(losses)
    assert lv.loss_r.stats["leaf:AAA/USDT|pullback"].n == len(losses)


def test_04b_reload_after_save_does_not_double_count(tmp_path):
    """Göç TEK SEFERLİKtir: kaydedip yeniden yüklemek kayıpları TEKRAR saymaz."""
    p = tmp_path / "learn_v2.json"
    p.write_text(json.dumps(_old_state([1.05, 1.10, 1.30], [2.0])), encoding="utf-8")
    lv = _learner(tmp_path)
    n1, est1 = lv.loss_r.stats[""].n, lv.loss_magnitude_r()[0]
    lv.save()
    assert "loss_r" in json.loads(p.read_text(encoding="utf-8"))
    lv2 = _learner(tmp_path)
    assert lv2.loss_r.stats[""].n == n1
    assert lv2.loss_magnitude_r()[0] == est1
    lv2.save()
    assert _learner(tmp_path).loss_r.stats[""].n == n1


def test_04c_old_state_without_lessons_keeps_todays_behaviour(tmp_path):
    """Ders kaydı da yoksa geri doldurulacak bir şey yoktur → kestirim TAM 1.0 kalır."""
    p = tmp_path / "learn_v2.json"
    st = _old_state([], [])
    st["n_closed"] = 12                                    # sayaç var, ders yok (budanmış dosya)
    p.write_text(json.dumps(st), encoding="utf-8")
    lv = _learner(tmp_path)
    assert lv.n_closed == 12 and lv.loss_r.stats.get("") is None
    assert lv.loss_magnitude_r()[0] == 1.0


def test_04d_live_close_records_only_losses(tmp_path):
    """Canlı yol: kaybeden kapanış `loss_r`ye yazılır, kazanan YAZILMAZ; R zaten NET'tir."""
    lv = _learner(tmp_path)
    lv.on_trade_closed(_close("T1", r=-1.24), {"regime": "TREND_UP"})
    lv.on_trade_closed(_close("T2", r=+2.10), {"regime": "TREND_UP"})
    lv.on_trade_closed(_close("T3", r=-1.06), {"regime": "TREND_UP"})
    assert lv.n_closed == 3
    assert lv.loss_r.stats[""].n == 2
    assert lv.loss_r.stats[""].mean == pytest.approx((1.24 + 1.06) / 2)
    assert lv.snapshot()["loss_magnitude_r"] == lv.loss_magnitude_r()[0]


# ============================================================ 5) sabitin geri gelmesine karşı
def test_05_avg_loss_r_must_track_the_learner_not_a_literal():
    """ÜÇ farklı kayıp geçmişi → ÜÇ farklı `avg_loss_r`, her biri learner'ın kestirimine EŞİT.

    Biri `default_loss_r`'yi geri koyarsa (ya da 1.0774 gibi bir literal yapıştırırsa) değerler
    aynılaşır ve bu test DÜŞER.
    """
    seen = []
    for hist in ([1.02] * 40, [1.20] * 40, [1.45] * 40):
        stub = _Stub(p_win=0.5, n_win=10, exp_r=0.1, n_exp=10, losses=hist)
        s = O.hierarchical_expectancy(learner=stub, symbol="A/USDT", side="LONG", setup="s", regime=None)
        assert s["avg_loss_r"] == stub.loss_magnitude_r()[0]
        assert s["provenance"]["loss_magnitude"]["source"] == "learner"
        seen.append(s["avg_loss_r"])
    assert len(set(seen)) == 3 and seen == sorted(seen)


def test_05b_source_carries_no_pasted_loss_literal():
    """Kaynak sözleşmesi: ölçülen ortalamanın (~1.02–1.20) bir literal olarak yapıştırılmaması."""
    src = inspect.getsource(O.hierarchical_expectancy)
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for lit in re.findall(r"(?<![\w.])\d+\.\d+", body):
        assert not (1.005 <= float(lit) <= 1.50), (
            f"`hierarchical_expectancy` içinde {lit} literali var — kayıp büyüklüğü learner'dan "
            "gelmeli, elle yapıştırılmamalı")
    assert '"loss_magnitude_r"' in src, "kestirim learner'a devredilmeli"


def test_05c_same_loss_magnitude_is_used_in_inversion_and_in_gross():
    """Ters çözüm ile brüt formül AYNI L'yi kullanmalı — aksi halde cebir tutmaz."""
    stub = _Stub(p_win=0.42, n_win=24, exp_r=0.18, n_exp=24, losses=[1.05, 1.12, 1.30, 1.01])
    s = O.hierarchical_expectancy(learner=stub, symbol="A/USDT", side="LONG", setup="s", regime=None)
    L = s["avg_loss_r"]
    assert L == stub.loss_magnitude_r()[0] > 1.0
    g, a = _gross(s)
    assert g == pytest.approx(s["p_win"] * s["avg_win_r"] - (1 - s["p_win"]) * L, abs=1e-6)
    assert a.avg_loss_r == pytest.approx(-L, abs=1e-6)      # `assess` işareti ters çevirir
    # ters çözümün kendisi de AYNI L'yi kullandı: w = 1'de gross tam exp_r'ye oturur
    s1 = O.hierarchical_expectancy(learner=stub, symbol="A/USDT", side="LONG", setup="s",
                                   regime=None, blend_n=0.0)
    assert _gross(s1)[0] == pytest.approx(0.18, abs=1e-7)


def test_05d_cost_is_not_double_counted():
    """`r_multiple` zaten NET olduğundan taban `NET_OUTCOME` kalmalı ve `cost_r = 0` olmalı."""
    stub = _Stub(p_win=0.5, n_win=10, exp_r=0.3, n_exp=10, losses=[1.08] * 30)
    s = O.hierarchical_expectancy(learner=stub, symbol="A/USDT", side="LONG", setup="s", regime=None)
    assert s["expectancy_basis"] == O.NET_OUTCOME
    _, a = _gross(s)
    assert a.cost_r == 0.0 and a.net_expectancy_r == a.gross_expectancy_r

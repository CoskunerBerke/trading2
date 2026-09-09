"""EKONOMİK KAPI, KALİBRE p_win KULLANMALI — 2026-09-09 üretim kusurunun regresyon testi.

KUSUR: `engine_v3.tour()` içinde `_assess_opportunities` (ekonomik kapı) öğrenici döngüsünden
48 satır ÖNCE çağrılıyordu. Kapının içindeki `if d.p_win:` dalı o anda `head.py:354`'ün yazdığı
HEAD ÖNSELİNİ okuyordu: `0.5 + 0.25*confidence`, daima >= 0.5, yani hiçbir zaman falsy. Sonuç:
`opportunity.hierarchical_expectancy`'nin ürettiği kalibre olasılık HER adayda eziliyordu.

Üretim verisiyle ölçüldü (2797 aday, `entry_snapshot.jsonl`): kapının kullandığı olasılık
2797/2797'de (%100) HEAD önseliydi; kayıtlı istatistiksel `p_win` hiçbir adayda kullanılmamıştı.
Ortalama beklenti şişmesi +0.988R/aday. NATGAS (2026-09-08T13:51Z, KABUL EDİLDİ): kapı 0.625
kullandı, istatistiksel tahmin 0.342'ydi, gerçek olasılıkla brüt beklenti -0.136R — açılmamalıydı.

Bu testler sıra değişmezini kilitler. Kapı yeniden öğreniciden öne alınırsa BUNLAR DÜŞER.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.coinhead.head import CoinHeadConfig  # noqa: E402


def _head_prior(confidence: float, score: float, threshold: float = 0.22) -> float:
    """`head.py:354` — kapının ESKİDEN kullandığı değer."""
    return round(0.5 + 0.25 * confidence * (1 if abs(score) >= threshold else 0), 3)


def test_01_head_prior_is_never_falsy_so_it_always_won_the_override():
    """Kusurun neden %100 vurduğunun aritmetik kanıtı: önsel asla 0/None olamaz.

    NOT (dürüstlük): bu bir GERİ ALMA DEDEKTÖRÜ DEĞİLDİR — sıra geri alınsa da geçer.
    Amacı, `if d.p_win:` korumasının neden hiçbir zaman devreye girmediğini belgelemek ve
    üretimdeki formülün bu özelliği kaybetmesini (ör. taban 0.0'a çekilirse) yakalamaktır.
    Sıra değişmezini test_02/test_03/test_05b/test_06 kilitler.
    """
    for conf in (0.0, 0.05, 0.5, 1.0):
        for score in (0.0, 0.21, 0.22, 1.0):
            assert _head_prior(conf, score) >= 0.5
    assert CoinHeadConfig().consensus_threshold == 0.22
    # Üretim formülü hâlâ aynı tabanla mı yazılıyor? (kaynak sözleşmesi)
    import inspect

    from tradingbot.coinhead import head as H
    src = inspect.getsource(H.CoinHead)
    assert "d.p_win = round(0.5 + 0.25 * conf" in src, (
        "head.py önsel formülü değişmiş — bu testin dayandığı taban artık geçerli olmayabilir")


def test_02_gate_sees_the_learner_pwin_not_the_head_prior(tmp_path, monkeypatch):
    """SIRA DEĞİŞMEZİ: `_assess_opportunities` çağrıldığı anda `d.p_win` öğrenici değeri olmalı."""
    eng = TE._engine(tmp_path, monkeypatch)

    SENTINEL = 0.137                      # hiçbir HEAD önselinin üretemeyeceği değer (< 0.5)
    seen: dict[str, float] = {}

    class _Pred:
        ready = True
        p_win_calibrated = SENTINEL
        prior_used = SENTINEL

    monkeypatch.setattr(eng.learner2, "predict", lambda *a, **k: _Pred())
    monkeypatch.setattr(eng.learner2, "prior_only", lambda *a, **k: _Pred())

    orig = eng._assess_opportunities

    def spy(decisions, briefs):
        for sym, d in decisions.items():
            if getattr(d, "p_win", None) is not None:
                seen[sym] = float(d.p_win)
        return orig(decisions, briefs)

    monkeypatch.setattr(eng, "_assess_opportunities", spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert seen, "kapı hiç çağrılmadı — test kurulumu bozuk"
    for sym, p in seen.items():
        assert p == pytest.approx(SENTINEL), (
            "%s: kapı p_win=%.3f gördü; öğrenici değeri %.3f olmalıydı "
            "(HEAD önseli sızdı → sıra kusuru geri geldi)" % (sym, p, SENTINEL))
        assert p < 0.5, "değer HEAD önseli aralığında (>=0.5) — kalibre değil"


def test_03_gate_output_inverts_to_the_learner_pwin(tmp_path, monkeypatch):
    """Kapının KENDİ kimliğinden geri çözülen olasılık öğrenici değerine eşit olmalı.

    `gross = p*W - (1-p)*L`  →  `p = (gross + |L|) / (W + |L|)`
    Üretim denetimi bu tersine çözümü kullandı; burada da aynısını kullanıyoruz.
    """
    eng = TE._engine(tmp_path, monkeypatch)
    SENTINEL = 0.211

    class _Pred:
        ready = True
        p_win_calibrated = SENTINEL
        prior_used = SENTINEL

    monkeypatch.setattr(eng.learner2, "predict", lambda *a, **k: _Pred())
    monkeypatch.setattr(eng.learner2, "prior_only", lambda *a, **k: _Pred())

    captured: dict[str, dict] = {}
    orig = eng._assess_opportunities

    def capture(decisions, briefs):
        res = orig(decisions, briefs)
        for sym, d in decisions.items():
            opp = getattr(d, "opportunity", None)
            if opp:
                captured[sym] = opp
        return res

    monkeypatch.setattr(eng, "_assess_opportunities", capture)
    eng.tour(do_scan=False, obsidian=False, charts=False)

    assert captured, "hiçbir adayda `opportunity` üretilmedi — test kurulumu bozuk"
    for sym, opp in captured.items():
        W = float(opp["avg_win_r"]); L = abs(float(opp["avg_loss_r"])); g = float(opp["gross_expectancy_r"])
        assert W + L > 0
        p_implied = (g + L) / (W + L)
        assert p_implied == pytest.approx(SENTINEL, abs=5e-4), (
            "%s: kapının kullandığı olasılık %.4f, öğrenici %.4f" % (sym, p_implied, SENTINEL))


def test_04_chief_ranking_still_sees_the_opportunity(tmp_path, monkeypatch):
    """Yeniden sıralama chief'i bozmamalı: yetkili chief kararı `d.opportunity` DOLU iken kurulmalı."""
    eng = TE._engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    chief = eng.registry.chief
    assert chief and chief.get("ranking"), "chief sıralaması üretilmedi"
    scored = [r for r in chief["ranking"] if r.get("conservative_net_edge_r") is not None]
    assert scored, ("chief `conservative_net_edge_r` göremedi — kapı chief'ten SONRA çalışıyor "
                    "olabilir; sıralama eski legacy edge'e düşmüş")


def test_05_risk_mode_reaches_the_learner_features(tmp_path, monkeypatch):
    """`market_risk_mode` kapıdan ÖNCE alınıp ÖĞRENİCİ özelliklerine ulaşmalı.

    DİKKAT — bu testin ilk hâli BOŞTU (bağımsız doğrulamada yakalandı): `features_from_brief`
    tur içinde İKİ AYRI yerden çağrılıyor; öğrenici döngüsünden (`engine_v3.py:871`, v3 chief ile)
    ve `_execute` içinden (`engine_v3.py:~1172`, LEGACY ajan chief'i ile). Spy son çağrıyı
    kaydedince ölçüm yanlış çağrıya kayıyor ve `risk_mode` sabit yanlış bir değere kodlansa
    bile test geçiyordu. Artık İLK gözlem esas alınır ve çağrı sayısı da kontrol edilir.
    """
    eng = TE._engine(tmp_path, monkeypatch)
    seen: list[str] = []
    import tradingbot.engine_v3 as E
    orig = E.features_from_brief

    def spy(brief, chief=None, scan_score=None):
        seen.append(str(getattr(chief, "risk_mode", None)) if chief is not None else "<chief-yok>")
        return orig(brief, chief, scan_score)

    monkeypatch.setattr(E, "features_from_brief", spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert seen, "features_from_brief hiç çağrılmadı — test kurulumu bozuk"
    first = seen[0]                                   # ÖĞRENİCİ döngüsündeki çağrı
    assert first in ("RISK-ON", "RISK-OFF", "NÖTR"), (
        "öğrenme özelliklerine giden risk_mode geçersiz: %r (tüm gözlemler: %r)" % (first, seen[:6]))
    assert first == eng.registry.chief["market_risk_mode"], (
        "erken alınan risk_mode (%r) yetkili chief kararından (%r) farklı"
        % (first, eng.registry.chief["market_risk_mode"]))


def test_05b_gate_runs_before_execution(tmp_path, monkeypatch):
    """Kapı, EMİR YOLUNDAN (`_execute`) önce çalışmalı.

    Bağımsız doğrulama, kapının ve chief'in `_execute`'tan SONRAYA taşındığı bir "onarım"ın
    diğer testlerin hepsini geçeceğini gösterdi — o hâlde `_execute` `d.opportunity is None`
    ile çalışır ve ekonomik kapı TAMAMEN devre dışı kalır. Bu test o sessiz bozulmayı kilitler.
    """
    eng = TE._engine(tmp_path, monkeypatch)
    order: list[str] = []
    g_orig, x_orig = eng._assess_opportunities, eng._execute

    def g(decisions, briefs):
        order.append("gate")
        return g_orig(decisions, briefs)

    def x(decisions, chief, briefs, state, marks, now):
        order.append("execute")
        opp_seen = [s for s, d in decisions.items() if getattr(d, "opportunity", None)]
        order.append("opportunity_var" if opp_seen else "OPPORTUNITY_YOK")
        return x_orig(decisions, chief, briefs, state, marks, now)

    monkeypatch.setattr(eng, "_assess_opportunities", g)
    monkeypatch.setattr(eng, "_execute", x)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert "gate" in order and "execute" in order, "tur kapı/emir yolunu çalıştırmadı: %r" % order
    assert order.index("gate") < order.index("execute"), (
        "EKONOMİK KAPI EMİR YOLUNDAN SONRA çalışıyor — kapı devre dışı: %r" % order)
    assert "OPPORTUNITY_YOK" not in order, (
        "`_execute` `d.opportunity` boşken çalıştı — kapı sonucu emir yoluna ULAŞMIYOR")


def test_06_influence_log_is_populated_before_the_gate(tmp_path, monkeypatch):
    """Yan kazanç: etki günlüğü artık kapıdan ÖNCE dolar; kapı bayat/önceki tur kaydını okumaz."""
    eng = TE._engine(tmp_path, monkeypatch)
    at_gate: dict[str, object] = {}
    orig = eng._assess_opportunities

    def spy(decisions, briefs):
        at_gate["log_is_list"] = isinstance(getattr(eng, "_influence_log", None), list)
        at_gate["snapshots"] = dict(getattr(eng, "_pred_snapshots", {}) or {})
        return orig(decisions, briefs)

    monkeypatch.setattr(eng, "_assess_opportunities", spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert at_gate.get("log_is_list") is True, "`_influence_log` kapı anında kurulmamış"
    assert at_gate.get("snapshots"), "`_pred_snapshots` kapı anında boş — öğrenici hâlâ kapıdan sonra"

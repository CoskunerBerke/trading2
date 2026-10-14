# -*- coding: utf-8 -*-
"""KARŞI-OLGUSAL NET ETİKET — BAR İÇİ NEDENSEL YOL (`cf_label_v3` / `cf_net_ledger_replay_v2`, 2026-09-29).

İnceleme bulguları (`cf_label_v2`, 3cca8a8):
* YÜKSEK — ana bot 4h yeniden oynatması: barın YÜKSEĞİ stopu başa-başa taşıyor, aynı tikte barın (daha ÖNCEKİ) AÇILIŞI yeni
  stopun ötesinde görünüp pozisyon açılıştan satılıyordu (brüt +2,25R hedef → net −0,571 "başa-baş stop").
* ORTA — 4h/1d barda stop dolumu kapanıştan (STOP_CLOSE_BEYOND_LEVEL_PRUDENT) yapılıyordu; canlı 60 sn izleyici seviyenin
  yanında doldurur (C4: −1,80R yerine ≈ −1,06R).
* DÜŞÜK — v1c "üst sınır" değil ve kesin değil (mumla yeniden denenmeli); Formasyon `qmark` kaydı iki kez kaydırılıyordu.
Her test 7ec832c'de düşer (sürüm dizesi, yol sırası, dolum kuralı, qmark, v1c adaylığı yok)."""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

from test_learning_cf_net import (BOX, FILT, H4, SYM, T0, _cost_floor_paths, _ledger, _rows,  # noqa: E402
                                  _scratch_ledger_r, _v1_row)
from tradingbot.learning_cf import (LABEL_VERSION, LABEL_VERSION_V1C, LABEL_VERSION_V2, NET_CONTRACT,  # noqa: E402
                                    CounterfactualRecorder, ExecModel, relabel_net)

UTC = timezone.utc
D1 = 86_400_000
MAIN = ExecModel.of_ledger(_ledger(be="1.0"))                 # ana defter: TP1 %50 + MFE başa-baş 1R (config.yaml)
C4 = ExecModel.of_ledger(_ledger())                           # tek hedefli 4h defter (C4/C4s/D4), başa-baş kapalı


def _one(tmp_path, bars, *, model=MAIN, side="LONG", entry=100.0, stop=98.0, targets=(103.0, 106.0), tf=240, horizon=6,
         kind="TARGET_STOP_TIME", features=None, key="k"):
    cf = CounterfactualRecorder(tmp_path / f"{key}.json", book="main")
    assert cf.record(signal_key=key, symbol=SYM, direction=side, entry=entry, stop=stop, targets=list(targets),
                     reason="TOTAL_OPEN_RISK", created_at=T0, tf_minutes=tf, horizon_bars=horizon, label_kind=kind,
                     features=features)
    t = cf.sb.trades[-1]
    tf_ms = tf * 60_000
    df = _rows(bars, tf_ms=tf_ms)
    key_tf = {240: "4h", 1440: "1d", 5: "5m"}[tf]
    n = cf.label_pending({SYM: {key_tf: df}}, T0 + timedelta(milliseconds=(horizon + 3) * tf_ms), exec_model=model,
                         filters_for=lambda s: FILT)
    assert n == 1 and t.outcome is not None
    return t, t.outcome


# ============================================================================ 1) YÜKSEK: bar içi sıra
def test_main_break_even_bar_that_opens_below_entry_is_not_sold_at_its_own_open(tmp_path):
    """Bulgunun kendisi: LONG 100, stop 98, hedefler 103/106, ana defter. İlk 4h bar (99,0 · 102,4 · 98,8 · 101,5): yüksek
    +1,2R → stop başa-başa; açılış 99,0 o yüksekten ÖNCEDİR, yeni stopa sınanamaz. Sonraki iki bar 106'ya gider → hedef."""
    _t, o = _one(tmp_path, [(99.0, 102.4, 98.8, 101.5), (101.5, 104.0, 101.0, 103.8), (103.8, 106.2, 103.5, 106.0)])
    assert o["r_multiple"] == pytest.approx(2.25) and o["exit_reason"] == "target"
    assert o["net_exit_reason"] == "hedef2" and o["r_net"] == pytest.approx(2.1517, abs=2e-3), \
        "ÖNCE: r_net −0,571, 'başa-baş stop', GAP_FILL_AT_FIRST_OBSERVATION @ 98,97 (barın 16:00 açılışı)"
    assert o["label_version"] == LABEL_VERSION == "cf_label_v3" and o["exec_model"]["contract"] == NET_CONTRACT


@pytest.mark.parametrize("close,want_exit,want_r,want_basis", [
    # yüksek başa-başı taşır, kapanış başa-başın ALTINA iner → çıkış SEVİYEDEN, aynı barın KAPANIŞ noktasında
    (99.2, "başa-baş stop", 0.0, "STOP_AT_LEVEL"),
    # düşük (99,0) yüksekten ÖNCE sayılır (ihtiyatlı sıra) → başa-baş henüz yok; kapanış üstünde → pozisyon sürer, hedef
    (104.0, "hedef2", 2.2097, None),
])
def test_a_stop_moved_by_the_bar_high_is_checked_only_against_later_path_points(tmp_path, close, want_exit, want_r,
                                                                              want_basis):
    """Yol: açılış → ters uç → lehte uç → kapanış. v2 barı tek tikte işliyordu: yüksek başa-başı taşıyor, aynı barın
    DÜŞÜĞÜ (yüksekten önce olmuş olabilir) yeni stopu tetikliyor, dolum kapanıştan ya da seviyeden yapılıyordu."""
    e, r = 100.0, 5.0
    bars = [(100.5, 106.5, 99.0, close), (close, 116.0, close - 0.5, 115.5)]
    _t, o = _one(tmp_path, bars, stop=e - r, targets=(e + 1.5 * r, e + 3 * r))
    assert o["net_exit_reason"] == want_exit and o["r_net"] == pytest.approx(want_r, abs=5e-3), o
    if want_basis:
        assert o["net_exit_basis"] == want_basis and o["cost_parts_r"]["exit_fill_model"] == 0.0


def test_random_paths_equal_an_independent_causal_walk_and_never_fill_before_the_event(tmp_path):
    """Rastgele 4h yollar (LONG/SHORT, ana model: TP1 + MFE başa-baş): net R bağımsız nedensel yürüyüşle (aynı sözleşme)
    BİREBİR; stop çıkışı ya seviyeden (kayma hariç) ya da ilk OLMAYAN barın açılışındaki gerçek boşluktandır."""
    rnd = random.Random(20260929)
    seen = {"STOP_AT_LEVEL": 0, "be": 0}
    for k in range(60):
        side = rnd.choice(("LONG", "SHORT"))
        sg = 1 if side == "LONG" else -1
        e, sp = 100.0, rnd.choice((1.0, 2.0, 3.0))
        stop = e * (1 - sg * sp / 100)
        risk = abs(e - stop)
        px, bars = e, []
        for _ in range(8):
            o = px
            c = o * (1 + rnd.gauss(0, 0.012))
            bars.append((o, max(o, c) * (1 + abs(rnd.gauss(0, 0.006))), min(o, c) * (1 - abs(rnd.gauss(0, 0.006))), c))
            px = c if rnd.random() > 0.15 else c * (1 + rnd.gauss(0, 0.01))
        t, o = _one(tmp_path, bars, side=side, stop=stop, targets=(e + sg * 1.5 * risk, e + sg * 3 * risk), horizon=8,
                    key=f"r{k}")
        assert o["r_net"] == pytest.approx(_scratch_ledger_r(t, _rows(bars, tf_ms=H4), MAIN), abs=1e-6), (k, o)
        if o["net_exit_reason"] in ("stop", "başa-baş stop"):
            assert o["net_exit_basis"] in ("STOP_AT_LEVEL", "GAP_FILL_AT_BAR_OPEN"), o
            seen["STOP_AT_LEVEL"] += o["net_exit_basis"] == "STOP_AT_LEVEL"
            seen["be"] += o["net_exit_reason"] == "başa-baş stop"
            if o["net_exit_basis"] == "GAP_FILL_AT_BAR_OPEN":
                assert o["cost_parts_r"]["exit_fill_model"] > 0
    assert seen["STOP_AT_LEVEL"] >= 10 and seen["be"] >= 3, seen


# ============================================================================ 2) ORTA: seviyeden dolum
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("tf,kind", [(240, "TARGET_STOP_TIME"), (1440, "HORIZON")])
def test_a_touched_stop_on_a_coarse_bar_fills_at_the_level_plus_exit_slippage(tmp_path, side, tf, kind):
    """C4/C4s/D4 (4h) ve T2/M2 (1d, HORIZON): bar stopu geçip ötesinde kapanıyor. v2 kapanıştan dolduruyordu
    (−1,80R); canlı 60 sn izleyicinin ulaştığı dolum seviye + çıkış kaymasıdır (≈ −1,06R)."""
    sg = 1 if side == "LONG" else -1
    bar = (100.0, 100.0 + sg * 0.5, 100.0 - sg * 4.0, 100.0 - sg * 3.5)
    bar = (bar[0], max(bar[1], bar[2]), min(bar[1], bar[2]), bar[3])
    _t, o = _one(tmp_path, [bar], model=C4, side=side, stop=100.0 - sg * 2.0, targets=(100.0 + sg * 3.0,), tf=tf, kind=kind)
    assert o["r_multiple"] == pytest.approx(-1.0) and o["net_exit_basis"] == "STOP_AT_LEVEL"
    assert o["net_exit_price"] == pytest.approx((100.0 - sg * 2.0) * (1 - sg * 0.0003), abs=1e-6)
    assert o["r_net"] == pytest.approx(-1.0633, abs=2e-3) and o["cost_parts_r"]["exit_fill_model"] == 0.0, \
        "ÖNCE: STOP_CLOSE_BEYOND_LEVEL_PRUDENT, r_net −1,80"


def test_only_a_later_bar_opening_beyond_the_stop_is_a_gap_the_first_bar_is_not(tmp_path):
    """Gerçek boşluk: İLK OLMAYAN bar stopun ötesinde açılır → dolum açılıştan (+ kayma). İlk bar ise girişten açılışına
    kadarki (karşı-olgusalın görmediği, canlı izleyicinin izlediği) aralığın ardından gelir → seviyeden."""
    _t, gap = _one(tmp_path, [(100.0, 100.5, 99.5, 100.2), (97.0, 97.5, 96.0, 96.5)], model=C4, targets=(103.0,), key="gap")
    assert gap["net_exit_basis"] == "GAP_FILL_AT_BAR_OPEN" and gap["net_exit_price"] == pytest.approx(97.0 * 0.9997, abs=1e-6)
    assert gap["cost_parts_r"]["exit_fill_model"] == pytest.approx(0.5, abs=1e-3)
    _t, first = _one(tmp_path, [(97.0, 97.5, 96.0, 96.5)], model=C4, targets=(103.0,), key="first")
    assert first["net_exit_basis"] == "STOP_AT_LEVEL" and first["r_net"] == pytest.approx(-1.0633, abs=2e-3), \
        "ÖNCE: ilk barın açılışı boşluk sayılıyordu (r_net −1,56)"


# ============================================================================ 3) DÜŞÜK: Formasyon qmark
def test_an_old_quantized_entry_record_is_not_slipped_a_second_time(tmp_path):
    """Eski Formasyon kaydı (öğrenme açılış reddi): giriş = `qmark` (defterin dolumu, 100,03). v2 onu yeniden kaydırıp
    100,06'dan açıyordu; v3 dolumu kayıt girişi kabul eder."""
    _t, o = _one(tmp_path, [(100.0, 104.5, 99.5, 104.0)], model=C4, entry=100.03, targets=(104.0,),
                 features={"entry_ref": "quantized_entry"}, key="q")
    assert o["entry_fill"] == pytest.approx(100.03, abs=1e-9) and o["cost_parts_r"]["entry_fill"] == 0.0
    _t, m = _one(tmp_path, [(100.0, 104.5, 99.5, 104.0)], model=C4, entry=100.0, targets=(104.0,),
                 features={"entry_ref": "mark"}, key="m")
    assert m["entry_fill"] == pytest.approx(100.03, abs=1e-9), "mark kaydı yine defterin dolumundan açılır"


def test_pattern_learning_open_reject_records_the_mark_not_the_quantized_fill(tmp_path):
    from test_learning_mode_pattern import _direct, _open, _plan, _ue
    _, book = _direct(tmp_path, bl_over={"min_notional_bump": False})
    pl = _plan()
    assert _open(book, pl, ue=_ue(min_notional="50")) == "REJECTED"
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened == ["MIN_ORDER_CONFLICT"]
    assert t.entry == pytest.approx(100.0) and t.features["entry_ref"] == "mark", \
        "ÖNCE: giriş qmark (100,03) ve entry_ref 'quantized_entry' — net yeniden oynatma ikinci kez kaydırıyordu"
    assert t.features["rr_at_entry"]["quantized_entry"] == pytest.approx(100.03)


# ============================================================================ 4) DÜŞÜK: v1c kesin değil, sürümler korunur
def test_v1c_rows_are_retried_with_klines_and_kept_untouched_when_still_impossible(tmp_path):
    import cf_backfill_net as B
    rows = [_v1_row(tmp_path, "a")]
    v2 = dict(rows[0], id="v2row", signal_key="v2row",
              outcome=dict(rows[0]["outcome"], label_version=LABEL_VERSION_V2, r_net=-1.2, net_status="OK"))
    doc = {"trades": rows + [v2]}
    df = _rows(_cost_floor_paths(100.0, 0.32)["stop"])
    df = pd.concat([_rows([(100.0, 100.01, 99.99, 100.0)], start=T0 - timedelta(minutes=5)), df]).reset_index(drop=True)
    now = T0 + timedelta(hours=3)
    # 1. koşu: mum yok → v1c
    s1 = B.backfill_rows(doc["trades"], model=BOX, now=now, frames_fn=None, filters_for=lambda s: FILT)
    o1 = doc["trades"][0]["outcome"]
    assert (s1["candidates"], s1["v1c"]) == (1, 1) and o1["label_version"] == LABEL_VERSION_V1C and "r_net_approx" in o1
    snap = json.dumps(doc, sort_keys=True)
    # 2. koşu: yine mum yok → v1c AYNEN kalır (yeniden yazım yok)
    s2 = B.backfill_rows(doc["trades"], model=BOX, now=now + timedelta(hours=1), frames_fn=None, filters_for=lambda s: FILT)
    assert (s2["candidates"], s2["v1c"], s2["v1c_kept"], s2["net"]) == (1, 0, 1, 0) and json.dumps(doc, sort_keys=True) == snap
    # 3. koşu: mumlar geldi → v1c ADAY, net yeniden oynatmayla DEĞİŞTİRİLİR (ÖNCE: aday 0, kayıt kalıcı v1c)
    s3 = B.backfill_rows(doc["trades"], model=BOX, now=now + timedelta(hours=2), frames_fn=lambda *a: df,
                         filters_for=lambda s: FILT)
    o3 = doc["trades"][0]["outcome"]
    assert (s3["candidates"], s3["net"], s3["v1c_upgraded"]) == (1, 1, 1)
    assert o3["label_version"] == LABEL_VERSION and o3["r_net"] == pytest.approx(-1.371, abs=2e-3)
    assert "r_net_approx" not in o3 and o3["net_status"] == "OK" and o3["net_backfilled"]["source"] == "offline_klines"
    assert o3["r_multiple"] == -1.0, "brüt alanlar değişmez"
    # eski v2 kaydı sürümünü KORUR: ne betik adayı ne tembel yol
    assert doc["trades"][1]["outcome"]["label_version"] == LABEL_VERSION_V2 and doc["trades"][1]["outcome"]["r_net"] == -1.2
    rep = {"now": "x", "apply": False, "fetch": True, "books": {"b": dict(s1, write="DRY_RUN", file="f")}}
    txt = B.render(rep)
    assert "yaklaşık" in txt and "üst sınır" not in txt


def test_lazy_relabel_keeps_v2_and_v1c_versions_untouched(tmp_path):
    from test_learning_cf_net import _rec
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t2, t1c = _rec(cf, key="v2"), _rec(cf, key="v1c")
    t2.outcome = {"r_multiple": -1.0, "exit_reason": "stop", "bars": 2, "label_version": LABEL_VERSION_V2, "r_net": -1.3}
    t1c.outcome = {"r_multiple": -1.0, "exit_reason": "stop", "bars": 2, "label_version": LABEL_VERSION_V1C,
                   "r_net_approx": -1.37}
    before = (dict(t2.outcome), dict(t1c.outcome))
    rc = relabel_net(cf.sb.trades, {SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)["stop"])}}, T0 + timedelta(minutes=20),
                     exec_model=BOX, filters_for=lambda s: FILT)
    assert rc == {"relabeled": 0, "unavailable": 0, "skipped": 0} and (t2.outcome, t1c.outcome) == before
    assert LABEL_VERSION != LABEL_VERSION_V2, "sürüm yükseltildi: v2 (bar tek tick) ile v3 (bar içi yol) ayırt edilir"


def test_v1c_is_documented_as_an_approximation_not_an_upper_bound():
    import cf_backfill_net as B
    from tradingbot import learning_cf as L
    doc = L.approx_net_r.__doc__ or ""
    assert "ne alt ne üst sınırdır" in doc and "NET'in ÜST SINIRI" not in doc
    assert "ÜST SINIR" not in (B.__doc__ or "") and "ne alt ne üst sınır" in (B.__doc__ or "")


# ============================================================================ 4) DÜŞÜK (inceleme, 2026-09-29): belirsiz bar sırası
def test_a_bar_whose_favourable_extreme_moves_the_stop_past_its_adverse_extreme_is_flagged_ambiguous(tmp_path):
    """LONG 100, stop 98, hedefler 103/106, ana defter (başa-baş 1R). İlk 4h bar (100,2 · 102,4 · 99,0 · 101,48): yüksek
    başa-başı taşır ve aynı barın düşüğü (99,0) yeni stopun ALTINDA — gerçek yol önce yükselip sonra başa-başı delebilir
    (gerçek defter: 'başa-baş stop' −0,013). "Ters uç önce" yolu bu barda HEP "sürer" dalını alır; bu İLK stop için
    ihtiyatlıdır, taşınan stop için DEĞİL. Kötü dal karalama Monte Carlo'da daha kötü tahminci çıktığı için yol korunur ve
    etiket işaretlenir (7ec832c'de alan yok; `net_stats` sayar)."""
    from tradingbot.learning_cf import net_stats
    bars = [(100.2, 102.4, 99.0, 101.48), (101.48, 101.6, 101.0, 101.2), (101.2, 101.5, 100.9, 101.3)]
    _t, amb = _one(tmp_path, bars, horizon=3, key="amb")
    assert amb["net_exit_basis"] == "HORIZON_CLOSE" and amb["r_net"] > 0.4, "yol değişmedi (sürer dalı)"
    assert amb["intrabar_ambiguous_bars"] == 1
    # aynı bar ama düşük başa-başın ÜSTÜNDE → hiçbir sırada stop yok, belirsizlik yok
    _t, clean = _one(tmp_path, [(100.2, 102.4, 100.6, 101.48)] + bars[1:], horizon=3, key="clean")
    assert clean["intrabar_ambiguous_bars"] == 0
    # stop taşınmayan bar (yüksek 1R'ye ulaşmıyor) belirsiz sayılmaz
    _t, flat = _one(tmp_path, [(100.2, 101.5, 99.0, 101.0)] + bars[1:], horizon=3, key="flat")
    assert flat["intrabar_ambiguous_bars"] == 0
    st = net_stats([amb, clean, flat])
    assert st["n_net_intrabar_ambiguous"] == 1 and st["n_net"] == 3


def test_the_conservative_wording_names_the_moved_stop_exception():
    from tradingbot import learning_cf as L
    assert "intrabar_ambiguous_bars" in (L.__doc__ or "") and "DEĞİLDİR" in (L._bar_path.__doc__ or "")
    assert L.INTRABAR_PATH == "open>adverse>favourable>close"

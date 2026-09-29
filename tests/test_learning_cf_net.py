# -*- coding: utf-8 -*-
"""KARŞI-OLGUSAL NET ETİKET (2026-09-29, maliyet sapması) — `cf_label_v2`.

"Olsaydı" R'si BRÜT ölçülüyordu (referans fiyat, seviyeden dolum, ücret/kayma/funding yok); gerçek işlemlerin R'si NET.
Kesinleşen etikete defterin KENDİ yürütme modeliyle aynı barlarda yeniden oynatılan NET R eklenir; eski alanlar brüt kalır;
eski (v1) etiketler tembel (çerçevede) ya da çevrimdışı (betik) doldurulur; karne/panel net ortalamayı raporlar.
Her test eski kodda (c0b8c94) düşer: `exec_model`/`ExecModel`/`net_*`/`peek` yok, karne brüt ortalama raporlar."""
from __future__ import annotations

import dataclasses
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

from tradingbot.accounting import (AmountType, FeeSchedule, FuturesLedgerV2, LiquidationParams, SizeSpec,  # noqa: E402
                                   SlippageModel, TaxPolicy, TickData, default_brackets)
from tradingbot.accounting.models import MarketType, SymbolFilters  # noqa: E402
from tradingbot.core import iso  # noqa: E402
from tradingbot.learn.shadow import ShadowBook, ShadowTrade, label_with_candles  # noqa: E402
from tradingbot.learning_cf import (LABEL_VERSION, LABEL_VERSION_V1, LABEL_VERSION_V1C,  # noqa: E402
                                    CounterfactualRecorder, ExecModel, _replay_notional, approx_net_r, label_records,
                                    outcome_r)

UTC = timezone.utc
T0 = datetime(2026, 9, 28, 12, 0, 5, tzinfo=UTC)
M5, H4 = 300_000, 14_400_000
SYM = "X/USDT"
FILT = SymbolFilters(symbol="XUSDT", price_tick=Decimal("0.001"), qty_step=Decimal("0.001"), min_qty=Decimal("0.001"),
                     min_notional=Decimal("5"), source="exchange")
V1_KEYS = {"r_multiple", "exit_reason", "exit_price", "bars", "mae_pct", "mfe_pct", "won", "veto_was_right",
           "is_counterfactual", "label_kind", "approx", "label_method"}


def _ledger(*, tp1="0.5", be="0") -> FuturesLedgerV2:
    """Defterlerin kurucusuyla aynı biçim (config: taker %0,05, maker %0,02, kayma 3 bps)."""
    return FuturesLedgerV2(Decimal("200"), fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05")),
                           slippage=SlippageModel(fixed_bps=Decimal("3")), brackets=default_brackets(),
                           liq_params=LiquidationParams(liq_fee_pct=Decimal("0.5")), tp1_fraction=Decimal(tp1),
                           breakeven_at_mfe_r=Decimal(be), tax_policy=TaxPolicy.disabled())


BOX = ExecModel.of_ledger(_ledger())


def _rows(bars, *, tf_ms=M5, start=T0):
    t = int(start.timestamp() * 1000) // tf_ms * tf_ms + tf_ms            # girişten SONRAKİ ilk bar
    out = []
    for o, h, lo, c in bars:
        out.append({"timestamp": t, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0})
        t += tf_ms
    return pd.DataFrame(out)


def _rec(cf: CounterfactualRecorder, *, entry=100.0, stop_pct=0.32, targets_r=(2.0,), side="LONG", horizon=5, tf=5,
         key="sig-1", created=T0, kind="TARGET_STOP_TIME") -> ShadowTrade:
    sg = 1 if side == "LONG" else -1
    stop = entry * (1 - sg * stop_pct / 100)
    risk = abs(entry - stop)
    assert cf.record(signal_key=key, symbol=SYM, direction=side, entry=entry, stop=stop,
                     targets=[entry + sg * k * risk for k in targets_r], reason="TOTAL_OPEN_RISK", created_at=created,
                     tf_minutes=tf, horizon_bars=horizon, label_kind=kind)
    return cf.sb.trades[-1]


def _label(cf, df, *, tf_ms=M5, n=10, **kw):
    return cf.label_pending({SYM: {("5m" if tf_ms == M5 else "4h"): df}}, T0 + timedelta(milliseconds=(n + 2) * tf_ms), **kw)


def _cost_floor_paths(e, r):
    return {"stop": [(e, e + 0.1 * r, e, e), (e, e, e - 1.05 * r, e - 0.5 * r)],
            "target": [(e, e + 1.05 * r, e, e + r)],
            "eod": [(e, e + 0.01 * r, e - 0.01 * r, e)] * 3}


# ============================================================================ 1) maliyet tabanı (araştırma tablosu)
@pytest.mark.parametrize("case,targets_r,horizon,gross,net", [
    ("stop", (2.0,), 5, -1.0, -1.371), ("target", (1.0,), 5, 1.0, 0.542), ("eod", (5.0,), 3, 0.0, -0.457)])
def test_cost_floor_long_032pct_stop_matches_the_ledger(tmp_path, case, targets_r, horizon, gross, net):
    """Config ücretleri, LONG, %0,32 stop: seviyede stop −1,0 → −1,371; 1R hedef +1,0 → +0,542; gün sonu 0R → −0,457.
    Eski alanlar brüt (v1 anlamı) kalır, yanına net ve maliyet parçaları (toplamı `cost_r`) yazılır."""
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t = _rec(cf, targets_r=targets_r, horizon=horizon)
    df = _rows(_cost_floor_paths(100.0, 0.32)[case])
    assert _label(cf, df, exec_model=BOX, filters_for=lambda s: FILT) == 1
    o = t.outcome
    assert o["r_multiple"] == pytest.approx(gross) and o["r_gross"] == o["r_multiple"]
    assert o["r_net"] == pytest.approx(net, abs=1e-3) and o["label_version"] == LABEL_VERSION == "cf_label_v2"
    assert o["cost_r"] == pytest.approx(o["r_gross"] - o["r_net"], abs=1e-6)
    assert sum(o["cost_parts_r"].values()) == pytest.approx(o["cost_r"], abs=1e-5)
    assert set(o["cost_parts_r"]) == {"fees", "entry_fill", "exit_slippage", "exit_fill_model", "path", "funding"}
    assert o["won_net"] is (o["r_net"] >= 0.25) and o["veto_was_right_net"] is (o["r_net"] <= 0)
    assert o["won"] is (gross > 0.25), "brüt `won` v1 anlamıyla kalır"
    assert o["net_status"] == "OK" and o["funding_complete"] is True and o["entry_fill"] > 100.0
    xm = o["exec_model"]
    assert (xm["taker_pct"], xm["maker_pct"], xm["slippage_bps"], xm["tp1_fraction"], xm["breakeven_at_mfe_r"]) == \
        (0.05, 0.02, 3.0, 0.5, 0.0)
    assert (xm["filters_source"], xm["price_tick"], xm["contract"]) == ("exchange", 0.001, "cf_net_ledger_replay_v1")
    assert V1_KEYS <= set(o)


# ============================================================================ 2) kimlik: net = atılık defterin R'si
def _scratch_ledger_r(t: ShadowTrade, df: pd.DataFrame, model: ExecModel) -> float:
    """Bağımsız yeniden oynatma (araştırmanın `analyze.replay` yolu): defteri AÇ, barları tick'le, açık kalırsa kapat."""
    led = model.new_ledger()
    led.open(t.symbol, t.direction, Decimal(str(t.entry)), SizeSpec(_replay_notional(FILT, Decimal("100")), AmountType.NOTIONAL, 3),
             stop=Decimal(str(t.stop)), targets=[Decimal(str(x)) for x in t.targets], filters=FILT,
             now=datetime.fromisoformat(t.created_at))
    tf_ms = int(t.tf_minutes) * 60_000
    c_ms = int(datetime.fromisoformat(t.created_at).timestamp() * 1000)
    l_ms = int(datetime.fromisoformat(t.label_ts).timestamp() * 1000)
    rows = df[(df["timestamp"] > c_ms) & (df["timestamp"] <= l_ms)]
    last = None
    for r in rows.itertuples():
        cdt = datetime.fromtimestamp((int(r.timestamp) + tf_ms) / 1000, tz=UTC)
        recs = led.tick({t.symbol: TickData(last=Decimal(str(r.close)), mark=Decimal(str(r.close)), high=Decimal(str(r.high)),
                                            low=Decimal(str(r.low)), open=Decimal(str(r.open)), ts=iso(cdt))}, now_utc=cdt)
        last = (r.close, cdt)
        if recs:
            return float(recs[-1].r_multiple)
    return float(led.close_manual(t.symbol, Decimal(str(last[0])), now=last[1]).r_multiple)


def test_r_net_equals_the_scratch_ledger_r_on_the_same_bars(tmp_path):
    rnd = random.Random(20260929)
    n_checked = 0
    for k in range(40):
        cf = CounterfactualRecorder(tmp_path / f"cf{k}.json", book="b1_box_fade")
        side = rnd.choice(("LONG", "SHORT"))
        t = _rec(cf, side=side, stop_pct=rnd.choice((0.32, 0.5, 1.0, 2.5)), targets_r=rnd.choice(((1.0,), (1.5, 3.0), (2.0,))),
                 horizon=12)
        px, bars = 100.0, []
        for _ in range(12):
            o = px
            c = o * (1 + rnd.gauss(0, 0.004))
            bars.append((o, max(o, c) * (1 + abs(rnd.gauss(0, 0.002))), min(o, c) * (1 - abs(rnd.gauss(0, 0.002))), c))
            px = c if rnd.random() > 0.2 else c * (1 + rnd.gauss(0, 0.004))       # ara sıra açılış boşluğu
        df = _rows(bars)
        assert _label(cf, df, n=12, exec_model=BOX, filters_for=lambda s: FILT) == 1
        assert t.outcome["r_net"] == pytest.approx(_scratch_ledger_r(t, df, BOX), abs=1e-6), t.outcome
        assert sum(t.outcome["cost_parts_r"].values()) == pytest.approx(t.outcome["cost_r"], abs=1e-4)
        n_checked += 1
    assert n_checked == 40


# ============================================================================ 3) exec_model=None → v1 bit-aynı
def test_without_exec_model_the_outcome_is_the_v1_outcome_and_net_only_adds_fields(tmp_path):
    df = _rows(_cost_floor_paths(100.0, 0.32)["stop"])
    a = CounterfactualRecorder(tmp_path / "a.json", book="b1_box_fade")
    b = CounterfactualRecorder(tmp_path / "b.json", book="b1_box_fade")
    ta, tb = _rec(a), _rec(b)
    assert _label(a, df) == 1 and _label(b, df, exec_model=BOX, filters_for=lambda s: FILT) == 1
    assert set(ta.outcome) == V1_KEYS, "exec_model yok → yalnız v1 alanları (label_version YOK)"
    ref = label_with_candles(ta, df)
    assert {k: ta.outcome[k] for k in ref} == ref
    assert {k: tb.outcome[k] for k in V1_KEYS} == ta.outcome, "net eklemek v1 alanlarını DEĞİŞTİRMEZ"
    n, _ = label_records([], {}, T0, exec_model=None)
    assert n == 0


# ============================================================================ 4) ana defter: MFE başa-baş
def test_main_mfe_breakeven_turns_a_gross_full_loss_into_a_near_scratch(tmp_path):
    """Ana defter (TP1 %50, `breakeven_at_mfe_r` 1,0): +1,2R MFE sonra tam stop — brüt −1,0; defter stopu başa-başa
    çekmişti → net ≈ −0,03 (etiketleyici bu kuralı bilmez; fark `path` parçasındadır)."""
    main = ExecModel.of_ledger(_ledger(be="1.0"))
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="main")
    t = _rec(cf, stop_pct=5.0, targets_r=(1.5, 3.0), horizon=6, tf=240)
    e, r = 100.0, 5.0
    bars = [(e + a * r, e + b * r, e + c * r, e + d * r) for a, b, c, d in
            [(0, 1.2, -0.1, 1.0), (1.0, 1.05, 0.5, 0.6), (0.6, 0.7, -1.1, -1.0)]]
    df = _rows(bars, tf_ms=H4)
    assert _label(cf, df, tf_ms=H4, n=6, exec_model=main, filters_for=lambda s: FILT) == 1
    o = t.outcome
    assert o["r_multiple"] == pytest.approx(-1.0) and o["exit_reason"] == "stop"
    assert o["r_net"] == pytest.approx(-0.0318, abs=2e-3) and o["net_exit_reason"] == "başa-baş stop"
    assert o["cost_parts_r"]["path"] < -0.9, "başa-baş kuralı maliyet değil KAZANÇ (negatif maliyet) olarak görünür"


# ============================================================================ 5) araştırma eşleşmesi net tabanda
def test_outcome_r_prefers_net_and_falls_back_to_gross():
    assert outcome_r({"r_multiple": 1.0, "r_net": 0.6}) == 0.6
    assert outcome_r({"r_multiple": 1.0}) == 1.0 and outcome_r({"r_multiple": 1.0, "r_net": None}) == 1.0
    assert outcome_r(None) is None


# ============================================================================ 6) tembel dolgu (v1 → v2)
def test_old_v1_labels_are_relabelled_lazily_when_the_frame_covers_the_window(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t_ok = _rec(cf, key="ok")
    t_gone = _rec(cf, key="gone", created=T0 - timedelta(hours=2))       # penceresi çerçeveden düşecek
    t_miss = _rec(cf, key="miss")
    t_miss.symbol = "Y/USDT"                                               # çerçevesi hiç yok
    df = _rows(_cost_floor_paths(100.0, 0.32)["stop"])
    early = _rows([(100.0, 100.1, 99.9, 100.0)] * 30, start=T0 - timedelta(hours=2))
    both = pd.concat([early[early["timestamp"] < int(df["timestamp"].iloc[0])], df]).reset_index(drop=True)
    frames = {SYM: {"5m": both}, "Y/USDT": {}}
    now = T0 + timedelta(minutes=20)
    assert cf.label_pending(frames, now) >= 1                              # eski kod: yalnız brüt (v1)
    assert "label_version" not in t_ok.outcome
    assert t_gone.outcome is not None and t_miss.outcome is None
    gone_v1 = dict(t_gone.outcome)
    cf.save()
    # yeni kod: bar penceresi yalnız son 3 bar (t_gone'un başlangıcı düştü); t_ok'un penceresi tam
    cf2 = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    frames2 = {SYM: {"5m": df}}
    cf2.label_pending(frames2, now, exec_model=BOX, filters_for=lambda s: FILT)
    by = {t.signal_key: t for t in cf2.sb.trades}
    o = by["ok"].outcome
    assert o["label_version"] == LABEL_VERSION and o["r_net"] == pytest.approx(-1.371, abs=1e-3)
    assert o["net_backfilled"]["source"] == "lazy" and o["r_multiple"] == -1.0
    g = by["gone"].outcome
    assert g["label_version"] == LABEL_VERSION_V1 and g["net_status"] == "NET_BACKFILL_WINDOW_NOT_IN_FRAME"
    assert {k: g[k] for k in gone_v1} == gone_v1 and "r_net" not in g, "brüt alanlar değişmez"
    assert by["miss"].outcome is None, "çerçevesi olmayan bekleyen kayda dokunulmaz"
    assert cf2.stats()["net_backfilled"] == 1 and cf2.stats()["n_net"] == 1
    cf2.save()
    assert json.loads((tmp_path / "cf.json").read_text(encoding="utf-8"))["meta"]["net_backfilled"] == 1


def test_lazy_relabel_marks_a_gross_mismatch_and_never_retries(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t = _rec(cf)
    df = _rows(_cost_floor_paths(100.0, 0.32)["stop"])
    now = T0 + timedelta(minutes=20)
    cf.label_pending({SYM: {"5m": df}}, now)
    bad = df.copy()
    bad.loc[1, "low"] = 100.0                                              # veri değişti: brüt artık üretilemez
    cf.label_pending({SYM: {"5m": bad}}, now, exec_model=BOX, filters_for=lambda s: FILT)
    assert t.outcome["label_version"] == LABEL_VERSION_V1 and t.outcome["net_status"] == "NET_BACKFILL_GROSS_MISMATCH"
    cf.label_pending({SYM: {"5m": df}}, now, exec_model=BOX, filters_for=lambda s: FILT)
    assert "r_net" not in t.outcome, "işaretli kayıt tembel yolda yeniden denenmez (çevrimdışı betiğe kalır)"


# ============================================================================ 7) funding: yan etkisiz okuma ve kapsama
def _fr():
    from tradingbot.pattern_trader.funding import FundingRates
    fr = FundingRates(provider=object())
    fr._intervals_known = True
    return fr


def test_funding_is_charged_from_the_realized_source_without_side_effects(tmp_path):
    """Pencere 16:00 UTC settlement'ını kapsar: oran bellekte → funding parçası dolu, kapsama tam. `FundingRates`e
    `peek` ile bakılır: istenen an kaydedilmez, sayaç artmaz (karşı-olgusal ağ adımını tetiklemez)."""
    created = datetime(2026, 9, 28, 15, 50, 5, tzinfo=UTC)
    st_ms = int(datetime(2026, 9, 28, 16, 0, tzinfo=UTC).timestamp() * 1000)
    fr = _fr()
    fr._rates[SYM] = {st_ms: Decimal("0.001")}
    fr._marks[SYM] = {st_ms: Decimal("100")}
    before = dict(fr.stats)
    model = dataclasses.replace(BOX, funding_hours_for=fr.hours_for, funding_fallback=False)
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t = _rec(cf, created=created, targets_r=(9.0,), horizon=6)
    df = _rows([(100.0, 100.05, 99.95, 100.0)] * 6, start=created)
    cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=45), exec_model=model, filters_for=lambda s: FILT,
                     funding_lookup=fr)
    o = t.outcome
    assert o["exit_reason"] == "horizon" and o["funding_complete"] is True
    stop = 100.0 * (1 - 0.0032)
    assert o["cost_parts_r"]["funding"] == pytest.approx(100.0 * 0.001 / (o["entry_fill"] - stop), rel=1e-3), \
        "LONG pozitif oranı öder: miktar × settlement mark'ı × oran, R paydası dolumdan"
    assert fr.pending() == {} and fr.stats == before, "yan etki yok"
    # oran bilinmiyor → funding ölçülmedi (sıfır sayılmaz): kapsama eksik, yine yan etki yok
    fr2 = _fr()
    cf2 = CounterfactualRecorder(tmp_path / "cf2.json", book="b1_box_fade")
    t2 = _rec(cf2, created=created, targets_r=(9.0,), horizon=6)
    cf2.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=45), exec_model=model, filters_for=lambda s: FILT,
                      funding_lookup=fr2)
    assert t2.outcome["funding_complete"] is False and t2.outcome["cost_parts_r"]["funding"] == 0.0
    assert fr2.pending() == {} and cf2.stats()["n_net_funding_incomplete"] == 1
    assert fr.peek(SYM, datetime.fromtimestamp(st_ms / 1000, tz=UTC)) == Decimal("0.001")


# ============================================================================ 8) doğrulanmamış filtre → nötr
def test_unverified_default_filters_do_not_invent_a_tick_rounded_fill(tmp_path):
    """Varsayılan filtre (0,01 tick) 0,12'lik coinde dolumu %8 kaydırırdı; gerçek defter doğrulanmamış hassasiyetle
    girmez → yeniden oynatma tick yuvarlamasız (kayıtta `UNVERIFIED_NEUTRAL`)."""
    res = {}
    for name, ff in (("default", None), ("exchange", lambda s: SymbolFilters(symbol="DOGEUSDT", price_tick=Decimal("0.00001"),
                                                                          qty_step=Decimal("1"), min_qty=Decimal("1"),
                                                                          min_notional=Decimal("5"), source="exchange"))):
        cf = CounterfactualRecorder(tmp_path / f"{name}.json", book="b1_box_fade")
        t = _rec(cf, entry=0.1234)
        e, r = 0.1234, 0.1234 * 0.0032
        cf.label_pending({SYM: {"5m": _rows([(e, e + 1.05 * r * 2, e, e + 2 * r)])}}, T0 + timedelta(minutes=20),
                         exec_model=BOX, filters_for=ff)
        res[name] = t.outcome
    assert res["default"]["exec_model"]["filters_source"] == "UNVERIFIED_NEUTRAL"
    assert res["default"]["r_net"] == pytest.approx(res["exchange"]["r_net"], abs=0.05)
    assert -1.0 < res["default"]["r_net"] < 2.0


# ============================================================================ 9) istatistik, gölge defter, geri alma
def test_stats_report_net_first_and_the_shadow_book_keeps_gross_avg_r(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    for i, case in enumerate(("stop", "target")):
        _rec(cf, key=f"k{i}", targets_r=(1.0,))
        cf.label_pending({SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)[case])}}, T0 + timedelta(minutes=20),
                         exec_model=BOX, filters_for=lambda s: FILT)
    st = cf.stats()
    assert st["n_net"] == 2 and st["r_basis"] == "net" and st["n_gross_only"] == 0
    assert st["mean_r_gross"] == pytest.approx(0.0) and st["mean_r_net"] == pytest.approx((-1.3707 + 0.5424) / 2, abs=1e-3)
    sb = cf.sb.stats()
    assert sb["avg_r"] == pytest.approx(0.0) and sb["avg_r_net"] == pytest.approx(st["mean_r_net"], abs=1e-3) and sb["n_net"] == 2


def test_v2_outcome_survives_an_old_code_rewrite_unchanged(tmp_path):
    """Geri alma: eski kod `outcome`u olduğu gibi taşır (isteğe bağlı alanları düşürse de) — net alanları kaybolmaz."""
    p = tmp_path / "cf.json"
    cf = CounterfactualRecorder(p, book="b1_box_fade")
    _rec(cf)
    cf.label_pending({SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)["stop"])}}, T0 + timedelta(minutes=20),
                     exec_model=BOX, filters_for=lambda s: FILT)
    cf.save()
    want = json.loads(p.read_text(encoding="utf-8"))["trades"][0]["outcome"]
    old_fields = ("id", "plan_id", "symbol", "market_type", "direction", "created_at", "entry", "stop", "targets",
                  "horizon_bars", "variant", "reason_not_opened", "label_ts", "tf_minutes", "leverage", "outcome",
                  "labeled_at", "is_counterfactual")
    raw = json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps({"trades": [{k: r[k] for k in old_fields} for r in raw["trades"]]}), encoding="utf-8")
    ShadowBook(p).save()                                                   # eski kodun yükle/yaz döngüsü
    back = CounterfactualRecorder(p, book="b1_box_fade")
    assert back.sb.trades[0].outcome == want and want["label_version"] == LABEL_VERSION
    back.label_pending({SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)["stop"])}}, T0 + timedelta(minutes=20),
                       exec_model=BOX, filters_for=lambda s: FILT)
    assert back.sb.trades[0].outcome == want, "v2 kayıt yeniden etiketlenmez"


# ============================================================================ 10) kapalı-biçim tahmin (v1c)
def test_closed_form_estimate_is_an_upper_bound_close_to_the_replay(tmp_path):
    for case, targets_r, horizon in (("stop", (2.0,), 5), ("target", (1.0,), 5), ("eod", (5.0,), 3)):
        cf = CounterfactualRecorder(tmp_path / f"{case}.json", book="b1_box_fade")
        t = _rec(cf, targets_r=targets_r, horizon=horizon)
        cf.label_pending({SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)[case])}}, T0 + timedelta(minutes=30),
                         exec_model=BOX, filters_for=lambda s: FILT)
        est = approx_net_r(t, t.outcome, model=BOX, filters=FILT)
        assert est == pytest.approx(t.outcome["r_net"], abs=0.02), case


# ============================================================================ 11) karne: net taban
def test_scorecard_averages_only_net_rows_and_shows_gross_as_info(tmp_path):
    import bot_scorecard as S
    p = tmp_path / "cf.json"
    p.write_text(json.dumps({"trades": [
        {"id": "a", "outcome": {"r_multiple": 2.0}},                                           # v1: yalnız brüt
        {"id": "b", "outcome": {"r_multiple": 1.0, "r_net": 0.6, "cost_r": 0.4, "label_version": "cf_label_v2",
                                "funding_complete": True}},
        {"id": "c", "outcome": {"r_multiple": -1.0, "r_net": -1.3, "cost_r": 0.3, "label_version": "cf_label_v2",
                                "funding_complete": False}},
        {"id": "d", "outcome": {"r_multiple": 0.5, "r_net_approx": 0.1, "label_version": "cf_label_v1c"}},
        {"id": "e", "outcome": None}]}), encoding="utf-8")
    c = S.counterfactual_card(p)
    assert (c["recorded"], c["labeled"], c["pending"], c["n_net"]) == (5, 4, 1, 2)
    assert c["mean_r"] == pytest.approx(-0.35) and c["r_basis"] == "net" and c["win_rate"] == 0.5
    assert c["mean_r_gross"] == pytest.approx(0.625) and c["mean_r_gross_net_rows"] == pytest.approx(0.0)
    assert (c["n_gross_only_v1"], c["n_approx_v1c"], c["mean_r_net_approx_v1c"]) == (1, 1, 0.1)
    assert c["mean_cost_r"] == pytest.approx(0.35) and c["n_net_funding_incomplete"] == 1 and c["in_pnl"] is False
    card = {"learning_mode": {"since": "x", "source": "y", "note_tr": "n", "books": {"b": {
        "name": "Box", "before": {"n": 0, "mean_r": None}, "after": {"n": 0, "mean_r": None}, "policy": {"n": 0},
        "learning_extra": {"n": 0}, "counterfactual": c}}}}
    txt = "\n".join(S.render_learning(card))
    assert "4/5 etiketli · net 2 · ort.R -0.35 (brüt 0.62)" in txt and "net R" in txt


# ============================================================================ 12) panel
def test_dashboard_table_and_badge_show_net_counterfactual_r():
    from tradingbot.dashboard import learning_view as LV
    row = {"book_id": "strategy_paper_box", "label": "Box", "active": True, "slots_k": 20, "slots_used": 3,
           "margin_frac": 0.2, "margin_sum": 40.0, "open_learning": 3, "open_extra": 2, "counters": {},
           "counterfactual": {"recorded_total": 487, "labeled": 313, "pending": 174, "n_net": 313, "mean_r_net": -0.12,
                              "mean_r_gross": 0.4}}
    html = LV.books_table_html([row])
    assert "KO net ort. R" in html and "KO brüt ort. R" in html and "-0.12" in html and "+0.40" in html
    badge = LV.book_badge_html(row)
    assert "karşı-olgusal net ort. R -0.12" in badge
    old = dict(row, counterfactual={"recorded_total": 2, "labeled": 1, "pending": 1})     # eski kodun özeti
    assert "KO net ort. R" in LV.books_table_html([old]) and "net ort. R —" in LV.book_badge_html(old)


def test_main_cf_summary_on_the_dashboard_counts_net_rows_only():
    from tradingbot.dashboard import learning_view as LV

    class _S:
        def get(self, k):
            return {"trades": [{"book": "main", "outcome": {"r_multiple": 1.0, "r_net": 0.7}},
                               {"book": "main", "outcome": {"r_multiple": -1.0}}, {"book": "main", "outcome": None},
                               {"outcome": {"r_multiple": 5.0, "r_net": 5.0}}]} if k == "shadow_book" else None
    d = LV._cf_main(_S())
    assert d == {"pending": 1, "labeled": 2, "dropped": None, "recorded_total": 3, "n_net": 1, "mean_r_net": 0.7,
                 "mean_r_gross": 0.0}


# ============================================================================ 13) çağıranlar: defterin kendi modeli
def test_strategy_book_labels_with_its_own_ledger_model(tmp_path):
    from test_learning_mode_books import _bl, _book
    book = _book(tmp_path, "b1_box_fade", filters=[SymbolFilters(symbol=SYM, market_type=MarketType.USDM_PERP,
                                                                  price_tick=Decimal("0.001"), qty_step=Decimal("0.001"),
                                                                  min_qty=Decimal("0.001"), source="exchange")])
    book._cf_recorder(_bl("b1_box_fade"))
    t = _rec(book.cf)
    book._learning_label({SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)["stop"])}}, T0 + timedelta(minutes=20))
    o = t.outcome
    assert o["label_version"] == LABEL_VERSION and o["exec_model"] == ExecModel.of_ledger(book.ledger).to_dict(
        book.filters_cache.get(SYM, MarketType.USDM_PERP))
    assert o["exec_model"]["filters_source"] == "exchange" and o["r_net"] == pytest.approx(-1.371, abs=2e-3)


def test_pattern_book_labels_with_its_own_ledger_model_tp1_closes_all(tmp_path):
    """Formasyon defteri TP1'de TAMAMINI kapatır (`tp1_fraction` 1): brüt etiketleyici 2 hedefte yarıyı tutup ikinciye
    giderdi — net yeniden oynatma defterin kuralını uygular (fark `path` parçası)."""
    from test_learning_mode_pattern import _direct
    _cfg, book = _direct(tmp_path)
    book.cf = CounterfactualRecorder(book.state_dir / "counterfactual_trades.json", book="pattern_trader")
    t = _rec(book.cf, targets_r=(1.0, 3.0), horizon=6, tf=60, stop_pct=2.0)
    e, r = 100.0, 2.0
    bars = [(e, e + 1.1 * r, e - 0.1 * r, e + r), (e + r, e + 3.1 * r, e + 0.9 * r, e + 3 * r)]
    assert book.label_counterfactuals(SYM, {"1h": _rows(bars, tf_ms=3_600_000).to_dict("records")},
                                      T0 + timedelta(hours=8)) == 1
    o = t.outcome
    assert o["r_multiple"] == pytest.approx(2.0) and o["exec_model"]["tp1_fraction"] == 1.0
    assert o["net_exit_reason"] == "hedef1" and o["r_net"] < 1.0 and o["cost_parts_r"]["path"] == pytest.approx(1.0, abs=0.01)


def test_main_engine_labels_net_relabels_old_rows_and_pairs_research_on_net(tmp_path, monkeypatch):
    import tradingbot.engine_v3 as M
    from test_learning_mode_main import _eng, _h4_closed, _lm
    eng = _eng(tmp_path, monkeypatch, _lm())
    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    created = t0 + timedelta(hours=2, minutes=7)
    plan = {"symbol": "S/USDT", "direction": "LONG", "entry": 100.0, "stop": 90.0, "targets": [130.0], "horizon_bars": 6}
    (lm_row,) = eng.shadow.add(dict(plan, plan_id="sig-lm"), ["TOTAL_OPEN_RISK"], now=created)
    lm_row.book, lm_row.signal_key, lm_row.label_kind = "main", "sig-lm", "TARGET_STOP_TIME"
    (old,) = eng.shadow.add(dict(plan, plan_id="sig-old"), ["TOTAL_OPEN_RISK"], now=created)
    old.book, old.signal_key, old.label_kind = "main", "sig-old", "TARGET_STOP_TIME"
    (legacy,) = eng.shadow.add(dict(plan, plan_id="plan-legacy"), ["RISK_CAPACITY_BLOCKED"], now=created)
    now = created + timedelta(hours=4 * 6 + 4)
    frame = _h4_closed(now, t0, 20, crash_bar=6)
    label_records([old], {"S/USDT": {"4h": frame}}, now)                   # eski kodun etiketi (v1, brüt)
    assert "label_version" not in old.outcome
    eng.runner.last_frames["S/USDT"] = {"4h": frame}
    seen = []
    monkeypatch.setattr(eng.research, "pop_pending_for_trade",
                        lambda tid: [{"policy_id": "pol", "decision": {"reasons": []}}] if tid == lm_row.id else [])
    monkeypatch.setattr(eng.research, "observe", lambda pid, **kw: seen.append(kw))
    monkeypatch.setattr(M, "utc_now", lambda: now)
    eng._label_shadows()
    o = lm_row.outcome
    assert o["label_version"] == LABEL_VERSION and o["r_multiple"] == -1.0 and o["r_net"] < -1.0
    assert o["exec_model"]["breakeven_at_mfe_r"] == float(eng.ledger2.breakeven_at_mfe_r)
    assert seen and seen[0]["baseline_r"] == pytest.approx(o["r_net"]), "BLOCKED eşleşmesi NET R ile"
    assert old.outcome["label_version"] == LABEL_VERSION and old.outcome["net_backfilled"]["source"] == "lazy"
    assert legacy.outcome is not None and "r_net" not in legacy.outcome, "eski gölge yolu (öğrenmesiz) aynen brüt"
    disk = json.loads((Path(eng.cfg.state_path) / "shadow_book.json").read_text(encoding="utf-8"))
    got = {r["plan_id"]: ("r_net" in (r.get("outcome") or {})) for r in disk["trades"]
           if r["plan_id"] in ("sig-lm", "sig-old", "plan-legacy")}
    assert got == {"sig-lm": True, "sig-old": True, "plan-legacy": False}


# ============================================================================ 14) çevrimdışı dolgu betiği
def test_backfill_script_exec_models_equal_the_real_books(tmp_path, monkeypatch):
    import cf_backfill_net as B
    from test_learning_mode_books import _book
    from test_learning_mode_main import _eng, _lm
    from test_learning_mode_pattern import _direct
    book = _book(tmp_path / "b", "b1_box_fade")
    book.ledger.funding.fallback_to_last_known = False                     # motorun kaynaksız kurulumu
    assert B.book_exec_model(book.cfg.v3, kind="book", spec=book.spec) == ExecModel.of_ledger(book.ledger)
    eng = _eng(tmp_path / "e", monkeypatch, _lm())
    assert B.book_exec_model(eng.cfg.v3, kind="main", rates=eng.funding_rates) == ExecModel.of_ledger(eng.ledger2)
    _c, pbook = _direct(tmp_path / "p")
    assert B.book_exec_model(pbook.cfg.v3, kind="pattern") == ExecModel.of_ledger(pbook.ledger)


def _v1_row(tmp_path, key, *, created=T0, case="stop"):
    cf = CounterfactualRecorder(tmp_path / f"tmp_{key}.json", book="b1_box_fade")
    t = _rec(cf, key=key, created=created)
    cf.label_pending({SYM: {"5m": _rows(_cost_floor_paths(100.0, 0.32)[case], start=created)}}, created + timedelta(minutes=20))
    return t.to_dict()


def test_backfill_rows_write_v2_from_klines_else_the_v1c_estimate(tmp_path):
    import cf_backfill_net as B
    rows = [_v1_row(tmp_path, "ok"), _v1_row(tmp_path, "nok", created=T0 + timedelta(hours=1))]
    pend = dict(rows[0], id="p", signal_key="p", outcome=None)
    done = dict(rows[0], id="v2", outcome=dict(rows[0]["outcome"], label_version="cf_label_v2", r_net=-1.2))
    doc = {"trades": rows + [pend, done], "meta": {"book": "b1_box_fade"}}
    df_ok = _rows(_cost_floor_paths(100.0, 0.32)["stop"])
    df_ok = pd.concat([_rows([(100.0, 100.01, 99.99, 100.0)], start=T0 - timedelta(minutes=5)), df_ok]).reset_index(drop=True)

    def frames_fn(sym, tfm, a, b):
        return df_ok if a <= int(T0.timestamp() * 1000) else None           # "nok" için mum yok
    out = B.backfill_rows(doc["trades"], model=BOX, now=T0 + timedelta(hours=3), frames_fn=frames_fn,
                          filters_for=lambda s: FILT)
    assert (out["candidates"], out["v2"], out["v1c"], out["none"]) == (2, 1, 1, 0)
    ok, nok = doc["trades"][0]["outcome"], doc["trades"][1]["outcome"]
    assert ok["label_version"] == LABEL_VERSION and ok["net_backfilled"]["source"] == "offline_klines"
    assert ok["r_net"] == pytest.approx(-1.371, abs=2e-3) and ok["r_multiple"] == -1.0
    assert nok["label_version"] == LABEL_VERSION_V1C and "r_net" not in nok and nok["r_net_approx"] < -1.0
    assert nok["net_status"].startswith("APPROX_CLOSED_FORM") and doc["trades"][2]["outcome"] is None
    assert doc["trades"][3]["outcome"]["r_net"] == -1.2, "v2 kayda dokunulmaz"


def test_backfill_script_run_is_dry_by_default_and_apply_backs_up(tmp_path, monkeypatch):
    """Kuru çalışma yazmaz; `--apply` yedekler ve yazar; ikinci koşuda aday yok; diskte değişen dosya atlanır."""
    import cf_backfill_net as B
    import tradingbot.strategy_paper as SPM
    from types import SimpleNamespace

    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    monkeypatch.setattr(SPM, "book_specs", lambda v3: [SimpleNamespace(state_dir="strategy_paper_box", breakeven_at_mfe_r=0.0)])
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.v3 = load_v3({})
    state = tmp_path / "state"
    (state / "strategy_paper_box").mkdir(parents=True)
    f = state / "strategy_paper_box" / "counterfactual_trades.json"
    f.write_text(json.dumps({"trades": [_v1_row(tmp_path, "ok")], "meta": {"book": "b1_box_fade"}}), encoding="utf-8")
    raw0 = f.read_bytes()
    df_ok = _rows(_cost_floor_paths(100.0, 0.32)["stop"])
    df_ok = pd.concat([_rows([(100.0, 100.01, 99.99, 100.0)], start=T0 - timedelta(minutes=5)), df_ok]).reset_index(drop=True)
    now = T0 + timedelta(hours=3)
    rep = B.run(state, cfg, apply=False, fetch=True, frames_fn=lambda *a: df_ok, now=now)
    assert rep["books"]["strategy_paper_box"]["write"] == "DRY_RUN" and f.read_bytes() == raw0
    assert rep["books"]["strategy_paper_box"]["v2"] == 1
    rep = B.run(state, cfg, apply=True, fetch=True, frames_fn=lambda *a: df_ok, now=now)
    assert rep["books"]["strategy_paper_box"]["write"] == "WRITTEN" and "WRITTEN" in B.render(rep)
    o = json.loads(f.read_text(encoding="utf-8"))["trades"][0]["outcome"]
    assert o["label_version"] == LABEL_VERSION and o["r_multiple"] == -1.0 and o["r_net"] < -1.0
    assert [p.read_bytes() for p in f.parent.glob("counterfactual_trades.json.pre-cf-net-*")] == [raw0]
    rep = B.run(state, cfg, apply=True, fetch=False, now=now)                  # ikinci koşu: aday yok → yazım yok
    assert rep["books"]["strategy_paper_box"]["candidates"] == 0 and rep["books"]["strategy_paper_box"]["write"] == "NO_CHANGE"
    stale = f.stat()
    f.write_text(f.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert B._write(f, {"trades": []}, stale, "x").startswith("SKIPPED_CHANGED_ON_DISK")

# -*- coding: utf-8 -*-
"""C4 · MUM VARYASYONLARI (4h) — kâğıt defter.

Kural modülü (`candle_book`), kural kaydı (`paper_rules`), çalışma kapısı (`candle_variations.gate`), defter yolu (gerçek
`StrategyBook` + `apply_action` + `FuturesLedgerV2` + `RiskEngine`) ve laboratuvarla PARİTE: defterin açtığı barlar,
stop'ları, ATR'si, hedefi, risk sınırları ve zaman çıkışı `candle_lab.variation_events` + `signal_lab.simulate`in test
ettikleriyle birebir aynıdır. Diğer defterler (T2, M2, Box, D4) değişmez.

Test varyasyonları kayda YALNIZ test süresince eklenir (`registry`); gerçek kayıt ve kayıt klasörü değişmez.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import logging
import sys
import time
import types
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from candle_variation_examples import EXAMPLES, pad  # noqa: E402
from test_donchian_trend_book import _lab_frame  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from tradingbot import box_theory, donchian_trend, ema200_trend, paper_rules  # noqa: E402
from tradingbot import candle_book as B  # noqa: E402
from tradingbot import candle_dsl as D  # noqa: E402
from tradingbot import candle_lab as CL  # noqa: E402
from tradingbot import candle_variations as V  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402
from tradingbot.accounting import MarketType, TickData  # noqa: E402
from tradingbot.candle_confirmation import closed_bars  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3  # noqa: E402
from tradingbot.strategy_paper import (BookSpec, DataVerdict, StrategyBook, apply_action, book_specs,  # noqa: E402
                                       validate_settings, verify_paper_data)

NAME = "c4_candle_variations"
SYM = "ETH/USDT"
H4 = 14_400_000
W = D.WINDOW
CV0 = "CV000_EXAMPLE_BULL3"
READBACK = {"confirmed_by": "user", "date": "2026-09-26"}
#: Onay, sonucu görülen laboratuvar çalıştırmasına bağlıdır (`run_id` = kaydın `run.github_run_id`, `_record`: 4242).
APPROVAL = {"by": "user", "date": "2026-09-28", "observation": False, "run_id": "4242"}
LAB_AT = "2026-09-27T10:00:00Z"


# ================================================================== yardımcılar
def _entry(vid: str, bars: list, *, side: str = "LONG", relations=(), context=None, confirm=None, stop=None, exit_=None,
           risk=None) -> dict:
    d = {"side": side, "timeframe": "4h", "bars": bars, "relations": list(relations), "context": context or {},
         "confirm": confirm or {"kind": "pattern_close"}, "stop": stop or {"anchor": "pattern", "atr_buffer": 0.25},
         "exit": exit_ or {"target_r": 2.0, "max_hold_bars": 24}}
    if risk is not None:
        d["risk_atr_bounds"] = risk
    return {"id": vid, "definition": d}


#: Parite varyasyonları: (a) gevşek 2 mum LONG (RSI + mum hacim filtresi), (b) önceki barın içinde kalan TEK mum, 3 bar
#: içinde yukarı kırılım (ana mum toplamla: max_high/min_low(-1..-1)), (c) 2 mum SHORT, trendle ya da karışık bağlamda.
VA = _entry("CV901_PAR_LOOSE", [{"color": "bear"}, {"color": "bull", "body_range": [0.5, None], "volume_ratio": [0.8, None]}],
            context={"rsi14": [None, 60]})
VB = _entry("CV902_PAR_INSIDE", [{}], relations=["c0.high <= max_high(-1..-1)", "c0.low >= min_low(-1..-1)"],
            confirm={"kind": "break", "within_bars": 3})
VC = _entry("CV903_PAR_SHORT", [{"color": "bull"}, {"color": "bear", "body_range": [0.5, None]}], side="SHORT",
            context={"trend": ["with", "mixed"]})
#: Zaman çıkışı sık olsun: hedef 3R, en çok 6 bar.
VT = _entry("CV904_PAR_TIME", VA["definition"]["bars"], context={"rsi14": [None, 60]},
            exit_={"target_r": 3.0, "max_hold_bars": 6})
#: Örnek kaydın AYNISI, örnek olmayan kimlikle (defter zinciri testleri; eşleşen dizi `EXAMPLES[CV0]["match"]`).
VX = dict(copy.deepcopy(V.CV000_EXAMPLE_BULL3), id="CV905_BULL3_COPY", example=False)
#: Aynı pencerenin son mumunu (hacimli uzun yeşil) tek başına yakalayan ikinci varyasyon.
VY = _entry("CV906_BIG_GREEN", [{"color": "bull", "body_range": [0.6, None], "volume_ratio": [1.5, None]}])
#: Kırılım bekleyen küçük mum (panel: bekleyen kırılım).
VK = _entry("CV907_SMALL_BREAK", [{"range_atr": [None, 0.5]}], confirm={"kind": "break", "within_bars": 3})


def _record(entry: dict, **over) -> dict:
    sha = over["definition_sha"] if "definition_sha" in over else D.definition_sha(entry)
    rec = {"record_schema": CL.RECORD_SCHEMA, "id": entry["id"], "definition_sha": sha,
           "dsl_version": D.DSL_VERSION, "window": D.WINDOW, "golden_sha": "0" * 16, "verdict": L.V_STRONG,
           "run": {"github_run_id": "4242", "run_url": "https://github.com/o/r/actions/runs/4242", "commit": "abc",
                   "completed_at": LAB_AT, "mode": {"catalog": False, "algos": False, "extras": False, "only_variations": True}},
           "readback": copy.deepcopy(entry.get("readback")),
           "primary_tf": "4h", "by_tf": {"4h": {"OOS": {"n": 120, "mean_r": 0.21, "ci95": [0.05, 0.37]}}}}
    rec.update(over)
    return rec


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """`add(*kayıtlar, readback=..., approval=..., record=True, **kayıt_alanları)`: kayda ekler, (geçen) laboratuvar kaydını
    geçici klasöre yazar. Döner: kimlikler."""
    rec_dir = tmp_path / "lab_records"
    rec_dir.mkdir()
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", rec_dir)

    def add(*entries, readback=READBACK, approval=APPROVAL, record=True, **rec_over):
        full = []
        for e in entries:
            e = copy.deepcopy(e)
            e["readback"], e["approval"] = copy.deepcopy(readback), copy.deepcopy(approval)
            full.append(e)
            if record:
                (rec_dir / ("%s.json" % e["id"])).write_text(json.dumps(_record(e, **rec_over)), encoding="utf-8")
        monkeypatch.setattr(V, "VARIATIONS", tuple(V.VARIATIONS) + tuple(full))
        V.reset_cache()
        return [e["id"] for e in full]
    add.dir = rec_dir
    yield add
    V.reset_cache()


def _params(*ids: str, **kw) -> B.CandleParams:
    return paper_rules.build_params(NAME, rule_params={"variations": list(ids), **kw})


def _now_ms() -> int:
    return (int(time.time() * 1000) // H4) * H4 + 120_000


def _rows_at(seq: list, now_ms: int, total: int = W) -> list[dict]:
    """Dizi, son barı `now_ms`ten önce kapanmış olacak şekilde (500 bar, ardışık 4h)."""
    return pad(seq, total=total, t_last=(now_ms // H4) * H4 - H4)


def _frames(seq: list, now_ms: int, total: int = W) -> dict:
    return {"4h": pd.DataFrame(_rows_at(seq, now_ms, total))}


def _ov(extra: list | None = None) -> dict:
    return _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                               "extra": extra if extra is not None else
                                               [{"name": NAME, "state_dir": "strategy_paper_candle4h"}]},
                            "chart_analysis": {"enabled": False}, "news": {"enabled": False}}


def _book(eng, variations: list[str]) -> StrategyBook:
    spec = BookSpec(name=NAME, state_dir="strategy_paper_candle4h", starting_equity_usdt=200.0,
                    rule_params={"leverage": 1, "entry_window_min": 60, "variations": list(variations)})
    return StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters,
                        run_id="rid1", spec=spec)


def _prov(book: StrategyBook, frames: dict) -> dict:
    return {"market": "USDM_PERP", "source": "test:USDM_PERP", "entry_ok": True, "tour_id": book.run_id,
            "frames": {tf: {"last_ts": int(df["timestamp"].iloc[-1])} for tf, df in frames.items()}}


def _tick(price: float, now: datetime) -> TickData:
    return TickData(last=Decimal(str(price)), mark=Decimal(str(price)), ts=now.isoformat())


def _step(book: StrategyBook, frames: dict, *, now_ms: int, price: float) -> None:
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    book.step(symbols=[SYM], frames_by_symbol={SYM: frames}, marks={SYM: _tick(price, now)}, marks_f={SYM: price}, now=now,
              provenance_by_symbol={SYM: _prov(book, frames)})


def _pos(opened_ms: int, vid: str | None, hold: int | None, setup: str | None = None):
    feats = {"candle_variation": {"id": vid, "max_hold_bars": hold}} if hold is not None else {}
    return types.SimpleNamespace(opened_at=int(opened_ms), setup_type=setup if setup is not None else "candle:%s" % vid,
                                 features=feats)


# ================================================================== 1) kural kaydı ve ayar
def test_rule_registry(registry):
    from tradingbot.structures.bots import StructureContext
    assert paper_rules.rule_timeframes(NAME) == ("4h",) and paper_rules.CANDLE_TIMEFRAMES == ("4h",)
    assert paper_rules.needs_btc(NAME) is False
    assert paper_rules.spec_for(NAME).family == "candle" and NAME in paper_rules.CANDLE_VARIANTS == B.VARIANTS
    assert paper_rules.build_params(NAME, rule_params={}) == B.DEFAULT_PARAMS and B.DEFAULT_PARAMS.variations == ()
    with pytest.raises(TypeError):                       # varyasyonun tanımı ayardan DEĞİŞTİRİLEMEZ
        paper_rules.build_params(NAME, rule_params={"target_r": 3.0})
    for bad in ({"leverage": 2}, {"leverage_max": 4}, {"entry_window_min": 0}, {"entry_window_min": 241},
                {"variations": ["CV999_NOPE"]}, {"variations": ["cv000_example_bull3"]}, {"variations": [CV0, CV0]},
                {"variations": CV0}, {"variations": [5]}):
        with pytest.raises(ValueError):
            paper_rules.build_params(NAME, rule_params=bad)
    assert _params(CV0).variations == (CV0,), "YAML listesi demete çevrilir; örnek kimlik ayarda durabilir (kapı reddeder)"
    # ENFORCE yapı bağlamı bile C4'e yapı katmanı uygulamaz (laboratuvar yapı katmanı olmadan ölçtü)
    ids = registry(VX)
    now_ms = _now_ms()
    frames = _frames(EXAMPLES[CV0]["match"], now_ms)
    ctx = StructureContext(mode="ENFORCE", symbol=SYM, as_of_ms=now_ms, price=97.0)
    act, sdec, an = paper_rules.decide_with_structures(NAME, frames=frames, btc_rows=None, now_ms=now_ms, params=_params(*ids),
                                                       ctx=ctx)
    assert sdec is None and an == {}
    assert act and act["action"] == "OPEN" and act == paper_rules.decide_for(NAME, frames=frames, btc_rows=None, now_ms=now_ms,
                                                                              params=_params(*ids))


def test_repository_config_idle_book():
    v3 = load_v3(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))
    specs = book_specs(v3)
    spec = next(b for b in specs if b.name == NAME)
    assert spec.state_dir == "strategy_paper_candle4h" and spec.enabled and spec.starting_equity_usdt == 200.0
    assert spec.symbols and set(spec.symbols) <= set(L.WIDE_SYMBOLS), "yalnız laboratuvarda test edilen coinler"
    assert set(spec.symbols) <= set(v3.entry_universe.symbols), "turun çerçeve çektiği evrenin dışında sembol yok"
    assert spec.symbols == next(b for b in specs if b.name == "d4_donchian_20_10").symbols, "D4'ün 23 coini"
    # defter BOŞ başladı; etkinleştirilen her kimlik kapıdan geçer (sonraki test) — bu test etkinleştirmede de geçer
    vs = spec.rule_params.get("variations")
    assert int(spec.rule_params.get("leverage", 1)) == 1 and isinstance(vs, list) and len(vs) == len(set(vs))
    assert spec.max_entry_drift_pct == 0.0 and v3.structures.mode_for(NAME) == "OFF"
    assert [b.name for b in specs][-1] == NAME and len(specs) == 5
    with pytest.raises(ValueError, match="PAPER_ONLY"):
        validate_settings(enabled=True, name=NAME, app_mode="LIVE", starting_equity=200.0, atr_mult=3.0)

    def cfg(rp):
        return load_v3({"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                           "extra": [{"name": NAME, "state_dir": "c4x", "rule_params": rp}]}})
    cfg({"leverage": 1, "variations": []})
    for rp, why in (({"variations": ["CV999_NOPE"]}, "kayıtta olmayan"), ({"leverage": 2}, "leverage"),
                    ({"exit_r": 3.0}, "rule_params")):
        with pytest.raises(ConfigError, match=why):
            cfg(rp)


def test_repository_config_variations_pass_gate():
    """CI kapısı: config'te etkin her varyasyon kapıdan GEÇER (çeviri onayı + CI laboratuvar kaydı + kullanıcı onayı).
    Liste boşken boş geçer; bozuk bir etkinleştirme birleşmeden önce burada düşer."""
    v3 = load_v3(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))
    for b in book_specs(v3):
        if b.name != NAME:
            continue
        for vid in b.rule_params.get("variations") or []:
            var, why = V.gate(vid)
            assert why is None and var is not None and var.id == vid, (vid, why)
            assert not var.example


# ================================================================== 4) kapı
def test_gate_reasons(tmp_path, monkeypatch):
    base = copy.deepcopy(VX)
    base.update(id="CV910_GATE", readback=dict(READBACK), approval=dict(APPROVAL))
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", tmp_path)

    def case(entry_over=None, rec_over=None, *, write=True, vid="CV910_GATE"):
        e = copy.deepcopy(base)
        e.update(entry_over or {})
        path = tmp_path / "CV910_GATE.json"
        if path.exists():
            path.unlink()
        if write:
            path.write_text(json.dumps(_record(e, **(rec_over or {}))), encoding="utf-8")
        monkeypatch.setattr(V, "VARIATIONS", (V.CV000_EXAMPLE_BULL3, e))
        V.reset_cache()
        return V.gate(vid)

    bad_def = copy.deepcopy(base["definition"])
    bad_def["bars"][0]["wick"] = [0, 1]
    run = _record(base)["run"]
    rb_late = {"confirmed_by": "user", "date": "2026-09-28"}
    cases = {
        "UNKNOWN_ID": case(vid="CV999_NOPE"),
        "DEFINITION_INVALID": case({"definition": bad_def}, {"definition_sha": "0" * 16}),
        "EXAMPLE": case(vid=CV0),
        "RETIRED": case({"retired": {"date": "2026-09-30", "reason_tr": "deneme"}}),
        "NO_READBACK": case({"readback": None}),
        "NO_LAB_RECORD": case(write=False),
        "DSL_VERSION_MISMATCH": case(rec_over={"dsl_version": "cv_dsl_0"}),
        "SHA_MISMATCH": case(rec_over={"definition_sha": "0" * 16}),
        "LAB_NOT_FROM_CI": case(rec_over={"run": dict(run, github_run_id=None)}),
        "LAB_RECORD_INVALID": case(rec_over={"record_schema": "candle_lab/0"}),
        "LAB_NOT_4H": case(rec_over={"primary_tf": "1h"}),
        "READBACK_AFTER_LAB": case({"readback": rb_late}, {"readback": rb_late}),
        "VERDICT_LOSS": case(rec_over={"verdict": L.V_LOSS}),
        "NO_APPROVAL": case({"approval": None}),
        "APPROVAL_BEFORE_LAB": case({"approval": {"by": "user", "date": "2026-09-26", "observation": True, "run_id": "4242"}}),
        "OBSERVATION_NOT_ACKED": case(rec_over={"verdict": L.V_WEAK}),
    }
    for code, (var, why) in cases.items():
        assert var is None and why == code, (code, why)
    assert set(cases) == set(V.GATE_REASONS) - {"GATE_ERROR"}, "her ret kodu sınandı"
    assert V.RECORD_SCHEMA == CL.RECORD_SCHEMA, "kapı laboratuvarın kayıt şemasını bekler"
    assert case(rec_over={"window": 400}) == (None, "DSL_VERSION_MISMATCH")
    # geçersiz kayıt: bilinmeyen/boş hüküm, katalogla birlikte (yalnız varyasyon OLMAYAN) koşu, kip bilgisi yok
    for over in ({"verdict": None}, {"verdict": "ÇOK İYİ"}, {"run": dict(run, mode={"only_variations": False})},
                 {"run": {k: v for k, v in run.items() if k != "mode"}}):
        assert case(rec_over=over) == (None, "LAB_RECORD_INVALID"), over
    # 4h ölçülmemiş (ör. --tfs 1h,1d): gözlem onayı olsa bile geçmez
    obs = {"approval": {"by": "user", "date": "2026-09-28", "observation": True, "run_id": "4242"}}
    for over in ({"by_tf": {"1h": {}, "1d": {}}}, {"primary_tf": None}, {"by_tf": None}):
        assert case(obs, dict(over, verdict=L.V_THIN)) == (None, "LAB_NOT_4H"), over
    # taslak koşu: kayıt onaysız koştu, onay aynı gün sonradan yazıldı → gün düzeyinde "önce" görünse de geçmez
    same_rb = {"confirmed_by": "user", "date": "2026-09-27"}
    assert case({"readback": same_rb}, {"readback": None}) == (None, "READBACK_AFTER_LAB")
    # koşudan sonra değiştirilen çeviri onayı da eşleşmez
    assert case({"readback": {"confirmed_by": "user", "date": "2026-09-20"}}, {"readback": READBACK}) == (None, "READBACK_AFTER_LAB")
    # onay çalıştırmaya bağlı: kimliği yok ya da başka çalıştırma → APPROVAL_BEFORE_LAB (aynı gün önceden verilen onay)
    for ap in ({"by": "user", "date": "2026-09-28", "observation": False},
               {"by": "user", "date": "2026-09-28", "observation": False, "run_id": "4241"}):
        assert case({"approval": ap}) == (None, "APPROVAL_BEFORE_LAB"), ap
    # geçenler: GÜÇLÜ ADAY (gözlem gerekmez); diğer hükümler yalnız açık gözlem onayıyla (KAYBETTİRİR asla)
    assert case()[1] is None
    for v in (L.V_WEAK, L.V_NONE, L.V_THIN, L.V_STRONG):
        var, why = case(obs, {"verdict": v})
        assert why is None and var.approval["observation"] is True, v
    assert case(obs, {"verdict": L.V_LOSS}) == (None, "VERDICT_LOSS")
    # aynı gün: çeviri onayı koşudan önce (kayıtta var), onay koşunun kimliğiyle aynı gün → geçer
    same = {"readback": same_rb, "approval": {"by": "user", "date": "2026-09-27", "observation": False, "run_id": 4242}}
    assert case(same, {"readback": same_rb})[1] is None


# ================================================================== 5-8) laboratuvarla parite
@pytest.mark.parametrize("seed", [3, 11])
def test_parity_entries_stops_atr_match_the_lab(registry, seed):
    ids = registry(VA, VB, VC)
    params = _params(*ids)
    df = _lab_frame(1400, seed)
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    lab: dict[tuple[int, str], tuple] = {}
    for vid in ids:
        evs, atr_sig = CL.variation_events(df, "PAR/USDT", "4h", V.get(vid))
        for e in evs:
            if e.family == "candle_var":
                lab[(e.i, vid)] = (e, float(atr_sig[e.i]))
    live: dict[tuple[int, str], dict | None] = {}
    for end in range(W, len(df) + 1):
        # motor yolu: çerçeveden okuma (hacimli 4h okuyucu + kapanmış bar), sinyal kapanışından 1 dk sonra
        act = paper_rules.decide_for(NAME, frames={"4h": df.iloc[:end]}, btc_rows=None, now_ms=int(ts[end - 1]) + H4 + 60_000,
                                     params=params)
        if act is None:
            continue
        i = end - 1
        assert act["action"] == "OPEN"
        live[(i, act["lab_algo"])] = act
        for other in act["also_matched"]:
            assert other != act["lab_algo"] and ids.index(other) > ids.index(act["lab_algo"]), "öncelik = config sırası"
            live[(i, other)] = None
    assert set(live) == set(lab), sorted(set(live) ^ set(lab))[:10]
    per = {vid: sum(1 for (_i, v) in lab if v == vid) for vid in ids}
    assert per[VA["id"]] >= 20 and per[VB["id"]] >= 5 and per[VC["id"]] >= 5, per
    for (i, vid), act in live.items():
        if act is None:
            continue
        ev, atr_i = lab[(i, vid)]
        var = V.get(vid)
        assert act["stop"] == ev.stop and act["atr14"] == atr_i, (i, vid)
        assert act["setup_type"] == "candle:" + vid and act["reason"] == "CANDLE_" + vid and act["direction"] == var.side
        assert act["signal_close_ms"] == ev.t_ms and act["signal_ts"] == int(ts[i]) and act["signal_close"] == float(df["close"].iloc[i])
        assert act["target_r_from_entry"] == var.target_r and act["risk_atr_bounds"] == list(var.risk_atr_bounds)
        assert act["targets"] == [] and act["leverage"] == 1 and act["leverage_max"] == 0
        assert act["variation"]["definition_sha"] == var.definition_sha and act["variation"]["max_hold_bars"] == var.max_hold_bars


def test_parity_time_stop(registry, monkeypatch):
    (vid,) = registry(VT)
    var = V.get(vid)
    hold = var.max_hold_bars
    df = _lab_frame(1400, 3)
    ts = df["timestamp"].to_numpy(dtype=np.int64)
    evs, atr_sig = CL.variation_events(df, "PAR/USDT", "4h", var)
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    vc = CL.vcfg(L.LabConfig(), var)
    timed = [e for e in evs if e.family == "candle_var" and not L.simulate(e, arr, atr_sig, vc) and e.exit_reason == "TIME"]
    assert len(timed) >= 5
    params = _params(vid)

    def at(k, pos, p=params):
        return paper_rules.decide_for(NAME, frames={"4h": df.iloc[:k + 1]}, btc_rows=None, now_ms=int(ts[k]) + H4 + 60_000,
                                      position=pos, params=p)
    for e in timed[:8]:
        j = e.i + 1                                           # giriş barı (laboratuvar: açılışında girer)
        assert e.hold == hold, "laboratuvar zaman çıkışı close[j+H-1]"
        pos = _pos(int(ts[j]) + 60_000, vid, hold)
        assert at(j + hold - 2, pos) is None
        act = at(j + hold - 1, pos)
        assert act == {"action": "CLOSE", "reason": "TIME_STOP_%d_BARS" % hold, "name": NAME, "late_bars": 0}
    # kesinti: iki bar kaçtı → çıkış yine gelir, gecikme yazılır
    e = timed[0]
    j = e.i + 1
    assert at(j + hold + 1, _pos(int(ts[j]) + 60_000, vid, hold))["late_bars"] == 2
    # varyasyon config'ten VE kayıttan çıkarılsa da açık pozisyon girişteki anlık görüntüyle biter
    monkeypatch.setattr(V, "VARIATIONS", tuple(x for x in V.VARIATIONS if x.get("id") != vid))
    V.reset_cache()
    idle = paper_rules.build_params(NAME, rule_params={})
    assert at(j + hold - 1, _pos(int(ts[j]) + 60_000, vid, hold), idle)["reason"] == "TIME_STOP_%d_BARS" % hold
    # anlık görüntü yok: kayda (setup_type) düşer; ikisi de yoksa ölçülemez → NONE (ret sayacında görünür)
    bare = _pos(int(ts[j]) + 60_000, vid, None)
    assert at(j + hold - 1, bare, idle) == {"action": "NONE", "reason": "TIME_STOP_UNKNOWN_VARIATION", "name": NAME}
    registry(VT)
    assert at(j + hold - 1, bare, idle)["reason"] == "TIME_STOP_%d_BARS" % hold
    assert at(j + hold - 2, bare, idle) is None
    # açılış anı okunamıyorsa hüküm yok (stop/hedef defterde sürer)
    assert at(j + hold - 1, types.SimpleNamespace(opened_at="?", setup_type="candle:" + vid, features={}), idle) is None


def test_parity_target_and_risk_bounds(registry, tmp_path, monkeypatch):
    (vid,) = registry(VA)
    var = V.get(vid)
    df = _lab_frame(1400, 11)
    evs, atr_sig = CL.variation_events(df, "PAR/USDT", "4h", var)
    arr = {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}
    vc = CL.vcfg(L.LabConfig(), var)
    ev = next(e for e in evs if e.family == "candle_var" and not L.simulate(dataclasses.replace(e), arr, atr_sig, vc))
    i, j, a_i, s = ev.i, ev.i + 1, float(atr_sig[ev.i]), 1.0
    # canlı zincir: sinyal barı duvar saatine hizalanır (yalnız zaman damgası kayar; dizin ve fiyatlar aynı)
    now_ms = _now_ms()
    off = (now_ms // H4) * H4 - H4 - int(df["timestamp"].iloc[i])
    live = df.copy()
    live["timestamp"] += off
    live["close_time"] += off
    frames = {"4h": live.iloc[:i + 1].reset_index(drop=True)}
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    book = _book(eng, [vid])

    def lab_skip(price):
        a2 = {k: v.copy() for k, v in arr.items()}
        a2["open"][j] = price
        return L.simulate(dataclasses.replace(ev), a2, atr_sig, vc)
    # stop'a 0,05 ATR mesafe (< 0,1 ATR): defter RISK_OUTSIDE_TESTED_RANGE, laboratuvar STOP_TOO_CLOSE
    close_px = ev.stop + s * 0.05 * a_i
    _step(book, frames, now_ms=now_ms, price=close_px)
    assert SYM not in book.ledger.positions and book.rejections.get("RISK_OUTSIDE_TESTED_RANGE") == 1, dict(book.rejections)
    assert lab_skip(close_px) == "STOP_TOO_CLOSE"
    # 5,5 ATR (> 5 ATR): defter reddeder, laboratuvar STOP_TOO_FAR
    far_px = ev.stop + s * 5.5 * a_i
    _step(book, frames, now_ms=now_ms + 30_000, price=far_px)
    assert SYM not in book.ledger.positions and book.rejections.get("RISK_OUTSIDE_TESTED_RANGE") == 2
    assert lab_skip(far_px) == "STOP_TOO_FAR"
    # laboratuvarın girişi (sonraki açılış): hedef = giriş + R × risk, laboratuvarın `simulate` formülüyle birebir
    price = float(arr["open"][j])
    assert lab_skip(price) == ""
    _step(book, frames, now_ms=now_ms + 60_000, price=price)
    pos = book.ledger.positions.get(SYM)
    assert pos is not None, dict(book.rejections)
    risk = s * (price - ev.stop)
    lab_target = price + s * vc.default_rr * risk
    assert len(pos.targets) == 1 and float(pos.targets[0]) == pytest.approx(lab_target, rel=1e-12, abs=1e-12)
    assert float(pos.stop) == pytest.approx(ev.stop, rel=1e-12) and pos.features["candle_variation"]["target_r"] == 2.0


def test_parity_placebo_is_matched(registry):
    ids = registry(VA, VC)
    cfg = L.LabConfig()
    evs_all: list[dict] = []
    for k, seed in enumerate((3, 11)):
        df = _lab_frame(1400, seed)
        a = CL._arrays(df)
        for vid in ids:
            var = V.get(vid)
            evs, atr_sig = CL.variation_events(df, "S%d/USDT" % k, "4h", var)
            plc = [e for e in evs if e.family == "placebo"]
            assert len(plc) >= 20 and all(e.side == var.side and e.name == "PLACEBO_" + vid for e in plc)
            # aynı bağlam (şekil dışı koşullar, pencere ATR'si) ve AYNI RİSK GEOMETRİSİ: stop = c[j] ∓ q × ATR14(j), q =
            # j'den önceki gerçek isabetlerden birinin risk/ATR'si (zaman damgasıyla seçilir) — stop kuralı son n bara
            # uygulanmaz (şekil stop mesafesini de belirler; öyle olsa vs_placebo şekli değil stop geometrisini ölçerdi)
            s = 1.0 if var.side == "LONG" else -1.0
            c = df["close"].to_numpy(dtype=float)
            real = sorted((e for e in evs if e.family == "candle_var"), key=lambda e: e.i)
            for e in plc:
                ok, a_j = D.placebo_context(CL.window_at(a, e.i, H4), var)
                assert ok and a_j == atr_sig[e.i], "aynı bağlam, pencere ATR'si"
                before = [r for r in real if r.i < e.i]
                r = before[int(L._h("S%d/USDT" % k, "4h", "PLACEBO_" + vid, "q", int(df["timestamp"].iloc[e.i])) * len(before))]
                q = s * (c[r.i] - r.stop) / atr_sig[r.i]
                assert q > 0 and e.stop == c[e.i] - s * q * a_j, "stop = gerçek isabetin risk/ATR'si ile"
        done, _meta = L.process_series(df, "S%d/USDT" % k, "4h", cfg, catalog=False, algos=False, extras=True,
                                       variations=tuple(ids))
        evs_all += [dataclasses.asdict(x) for x in done]
    agg = L.aggregate(evs_all, cfg)
    for vid in ids:
        g = next(x for x in agg["groups"] if x["family"] == "candle_var" and x["name"] == vid and x["context"] == "HEPSİ")
        pg = next(x for x in agg["groups"] if x["name"] == "PLACEBO_" + vid and x["context"] == "HEPSİ")
        assert g["vs_placebo"] is not None and g["vs_placebo"]["placebo_mean_r"] == [pg["IS"]["mean_r"], pg["OOS"]["mean_r"]]
    # eşi yoksa genel rastgele girişe (PLACEBO_RANDOM) DÜŞMEZ
    no_own = [e for e in evs_all if not str(e["name"]).startswith("PLACEBO_CV")]
    assert any(e["name"] == "PLACEBO_RANDOM" for e in no_own)
    assert all(g["vs_placebo"] is None for g in L.aggregate(no_own, cfg)["groups"] if g["family"] == "candle_var")


# ================================================================== 9-14) defter yolu (gerçek zincir)
def test_book_opens_labels_and_scales(registry, tmp_path, monkeypatch):
    (vid,) = registry(VX)
    var = V.get(vid)
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = _frames(EXAMPLES[CV0]["match"], now_ms)
    hit = D.detect_last(D.window_from_rows(_rows_at(EXAMPLES[CV0]["match"], now_ms), H4)[0], var)
    book = _book(eng, [vid])
    price = hit.close + 0.05
    _step(book, frames, now_ms=now_ms, price=price)
    assert book.data_checks[SYM]["ok"] is True, book.data_checks[SYM]
    pos = book.ledger.positions.get(SYM)
    assert pos is not None, dict(book.rejections)
    assert pos.side.value == "LONG" and pos.leverage == 1 and len(pos.targets) == 1 and pos.setup_type == "candle:" + vid
    assert float(pos.stop) == pytest.approx(hit.stop) and float(pos.targets[0]) == pytest.approx(price + 2.0 * (price - hit.stop))
    cv = pos.features["candle_variation"]
    assert cv["id"] == vid and cv["definition_sha"] == var.definition_sha and cv["dsl_version"] == D.DSL_VERSION
    assert cv["observation"] is False and cv["lab_verdict"] == L.V_STRONG and cv["lab_oos_ci95"] == [0.05, 0.37]
    assert cv["max_hold_bars"] == 24 and cv["target_r"] == 2.0 and cv["pattern_low"] == pytest.approx(hit.pattern_low)
    assert cv["lab_run_url"].endswith("/4242")
    sig = pos.meta["signal"]
    assert sig["lab_algo"] == vid and sig["signal_close_ms"] == hit.signal_close_ms and sig["atr14"] == hit.atr_i
    scaled = pos.meta.get("size_scaled_to_cap")
    assert scaled and 0 < scaled["risk_fraction_of_budget"] < 1, "tavanı aşan işlem küçültülür, reddedilmez"
    assert book.last_actions[SYM]["reason"] == "CANDLE_" + vid
    # gözlem onayı (ZAYIF İZ) işlem kaydına geçer
    (vid2,) = registry(dict(copy.deepcopy(VX), id="CV908_OBS_COPY"),
                       approval={"by": "user", "date": "2026-09-28", "observation": True, "run_id": "4242"}, verdict=L.V_WEAK)
    act = paper_rules.decide_for(NAME, frames=frames, btc_rows=None, now_ms=now_ms, params=_params(vid2))
    assert act["variation"]["observation"] is True and act["variation"]["lab_verdict"] == L.V_WEAK


def test_one_entry_per_signal_after_same_bar_stop(registry, tmp_path, monkeypatch):
    (vid,) = registry(VX)
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    frames = _frames(EXAMPLES[CV0]["match"], now_ms)
    book = _book(eng, [vid])
    _step(book, frames, now_ms=now_ms, price=96.6)
    pos = book.ledger.positions.get(SYM)
    assert pos is not None, dict(book.rejections)
    # stop aynı barda geldi (defter kapattı) → sonraki tur aynı sinyali yeniden görür ama GİRMEZ
    book.ledger.close_manual(SYM, float(pos.stop), reason="STOP", now=datetime.fromtimestamp(now_ms / 1000 + 300, tz=timezone.utc))
    _step(book, frames, now_ms=now_ms + 900_000, price=96.6)
    assert SYM not in book.ledger.positions
    assert book.last_actions[SYM]["reason"] == "SIGNAL_ALREADY_USED"


def test_no_entry_after_window(registry):
    (vid,) = registry(VX)
    now_ms = _now_ms()
    rows = _rows_at(EXAMPLES[CV0]["match"], now_ms)
    close_ms = int(rows[-1]["timestamp"]) + H4

    def dec(at_ms, **kw):
        return paper_rules.decide_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, now_ms=at_ms, params=_params(vid, **kw))
    assert dec(close_ms + 60 * 60_000)["action"] == "OPEN", "tam 60 dk hâlâ geçerli"
    assert dec(close_ms + 61 * 60_000) is None, "kesinti sonrası geç giriş yok"
    assert dec(close_ms + 61 * 60_000, entry_window_min=90)["action"] == "OPEN"
    assert dec(None)["action"] == "OPEN", "now_ms verilmezse (araştırma) pencere denetlenmez"


def test_target_tick_closes_fully_tp1(registry, tmp_path, monkeypatch):
    (vid,) = registry(VX)
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    now_ms = _now_ms()
    book = _book(eng, [vid])
    _step(book, _frames(EXAMPLES[CV0]["match"], now_ms), now_ms=now_ms, price=96.6)
    pos = book.ledger.positions.get(SYM)
    assert pos is not None and len(pos.targets) == 1, dict(book.rejections)
    target = float(pos.targets[0])
    at = datetime.fromtimestamp(now_ms / 1000 + 600, tz=timezone.utc)
    book.tick({SYM: _tick(target * 0.999, at)}, now=at, bar_advance=False)
    assert SYM in book.ledger.positions, "hedefe varmadan kapanmaz"
    at2 = datetime.fromtimestamp(now_ms / 1000 + 700, tz=timezone.utc)
    recs = book.tick({SYM: _tick(target + 0.01, at2)}, now=at2, bar_advance=False)
    assert SYM not in book.ledger.positions and len(recs) == 1, "tek hedef: TAMAMI kapanır (kısmi TP yok)"
    rec = book.ledger.history[-1]
    assert rec.exit_reason == "hedef1" and float(rec.r_multiple) > 1.5 and rec.setup_type == "candle:" + vid
    assert rec.features["candle_variation"]["id"] == vid, "anlık görüntü işlem kaydına geçer"


def test_two_variations_same_bar(registry, monkeypatch, caplog):
    ids = registry(VX, VY)
    now_ms = _now_ms()
    rows = _rows_at(EXAMPLES[CV0]["match"], now_ms)
    at = int(rows[-1]["timestamp"]) + H4 + 60_000
    win = D.window_from_rows(rows, H4)[0]
    for order in (ids, ids[::-1]):
        act = paper_rules.decide_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, now_ms=at, params=_params(*order))
        assert act["lab_algo"] == order[0] and act["also_matched"] == [order[1]], "config sırası = öncelik"
        assert act["stop"] == D.detect_last(win, V.get(order[0])).stop
    # öncelikli varyasyon kapıdan geçmezse sıradaki açar; ret sunucuda sessiz kalmaz (süreç başına bir kez UYARI)
    (vz,) = registry(dict(copy.deepcopy(VY), id="CV909_NO_APPROVAL"), approval=None)
    monkeypatch.setattr(B, "_GATE_LOGGED", set())
    with caplog.at_level(logging.WARNING, logger="tradingbot.candle_book"):
        for _ in range(2):
            act = paper_rules.decide_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, now_ms=at,
                                               params=_params(vz, CV0, ids[0]))
            assert act["lab_algo"] == ids[0] and act["also_matched"] == []
    logged = sorted(r.getMessage() for r in caplog.records if "kapıdan geçmedi" in r.getMessage())
    assert len(logged) == 2 and "%s kapıdan geçmedi: NO_APPROVAL" % vz in logged[1] and "%s kapıdan geçmedi: EXAMPLE" % CV0 in logged[0]


def test_signal_ignored_while_position_open(registry, tmp_path, monkeypatch):
    (vid,) = registry(VX)
    now_ms = _now_ms()
    rows = _rows_at(EXAMPLES[CV0]["match"], now_ms)
    opened = int(rows[-3]["timestamp"]) + 60_000
    act = paper_rules.decide_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, now_ms=now_ms, position=_pos(opened, vid, 24),
                                       params=_params(vid))
    assert act is None, "açık pozisyonda yeni sinyal yok (yalnız zaman sınırı)"
    assert B.decide(NAME, rows=rows, position={"opened_ts": opened, "setup_type": "candle:" + vid, "features": {}},
                    now_ms=now_ms, params=_params(vid)) is None
    # gerçek zincir: ikinci tur aynı sinyali görür, pozisyon tek kalır
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    book = _book(eng, [vid])
    frames = _frames(EXAMPLES[CV0]["match"], now_ms)
    _step(book, frames, now_ms=now_ms, price=96.6)
    first = book.ledger.positions[SYM].id
    _step(book, frames, now_ms=now_ms + 900_000, price=96.7)
    assert list(book.ledger.positions) == [SYM] and book.ledger.positions[SYM].id == first
    assert book.last_actions[SYM]["action"] == "NONE" and book.last_actions[SYM]["held"] is True


# ================================================================== 15-16) boş defter ve panel
def test_idle_book(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    assert "strategy_paper_candle4h" in [b.key for b in eng.strategy_books] and "4h" in eng._book_timeframes
    now_ms = _now_ms()
    frames = _frames(EXAMPLES[CV0]["match"], now_ms)
    book = _book(eng, [])
    _step(book, frames, now_ms=now_ms, price=96.6)
    assert book.data_checks[SYM]["ok"] is True, book.data_checks[SYM]
    assert not book.ledger.positions and book.rejections == {} and book.counters["rejected"] == 0
    assert book.last_actions[SYM]["action"] == "NONE" and book.last_actions[SYM]["reason"] == "NO_SIGNAL"
    rs = paper_rules.state_for(NAME, frames=frames, btc_rows=None, now_ms=now_ms, params=book.rule_params)
    assert rs["reason"] == "NO_VARIATIONS" and rs["status_tr"] == "varyasyon bekliyor" and rs["variations"] == []
    assert rs["ok"] is True and rs["volume_present"] is True and rs["n_bars"] == W and rs["window"] == W
    json.dumps(rs, allow_nan=False)
    # veri eksikse boş defter yine hata vermez (gerekçe görünür)
    short = paper_rules.state_for(NAME, frames=_frames(EXAMPLES[CV0]["match"], now_ms, total=120), btc_rows=None, now_ms=now_ms,
                                  params=book.rule_params)
    assert short["ok"] is False and short["window_reason"] == "NOT_ENOUGH_4H_BARS" and short["reason"] == "NO_VARIATIONS"


def test_rule_state_for_panel(registry):
    ids = registry(VX, VK)
    (noap,) = registry(dict(copy.deepcopy(VY), id="CV909_NO_APPROVAL"), approval=None)
    params = _params(CV0, *ids, noap)
    now_ms = _now_ms()
    # 1) c1'in gövdesi fazla büyük: aktif varyasyonun ilk başarısız koşulu
    miss = _rows_at(EXAMPLES[CV0]["near_miss"]["bars[1].body_range"], now_ms)
    rs = paper_rules.state_from_rows(NAME, daily=[], intraday=miss, btc_rows=[], params=params)
    assert rs["ok"] and rs["reason"] is None and rs["signal_ts"] == miss[-1]["timestamp"] and rs["volume_present"]
    rows = {r["id"]: r for r in rs["variations"]}
    assert [r["id"] for r in rs["variations"]] == [CV0, *ids, noap], "config sırası"
    assert rows[CV0]["active"] is False and rows[CV0]["gate_reason"] == "EXAMPLE" and rows[CV0]["matched"] is None
    assert rows[noap]["active"] is False and rows[noap]["gate_reason"] == "NO_APPROVAL" and rows[noap]["lab_verdict"] == L.V_STRONG
    vx = rows[ids[0]]
    assert vx["active"] and vx["gate_reason"] is None and vx["matched"] is False and vx["observation"] is False
    assert vx["first_fail_clause"] == "bars[1].body_range" and vx["first_fail"].startswith("bars[1].body_range ")
    assert vx["lab_verdict"] == L.V_STRONG and rs["status_tr"] == "2/4 varyasyon etkin"
    json.dumps(rs, allow_nan=False)
    # 2) eşleşen pencere: eşleşme ve açılsaydı stop
    rs = paper_rules.state_from_rows(NAME, daily=[], intraday=_rows_at(EXAMPLES[CV0]["match"], now_ms), btc_rows=[], params=params)
    vx = next(r for r in rs["variations"] if r["id"] == ids[0])
    assert vx["matched"] is True and vx["first_fail"] is None and vx["stop_if_open"] is not None
    # 3) küçük mum oluştu, kırılım bekleniyor: seviye, geçersizlik seviyesi, kalan bar
    small = (0.0, 0.2, -0.1, 0.1, 1.0)
    rs = paper_rules.state_from_rows(NAME, daily=[], intraday=_rows_at([small], now_ms), btc_rows=[], params=params)
    vk = next(r for r in rs["variations"] if r["id"] == ids[1])
    assert vk["matched"] is False and vk["pending_break"]["p"] == W - 1 and vk["pending_break"]["bars_left"] == 3
    assert vk["pending_break"]["level"] == pytest.approx(100.2) and vk["pending_break"]["invalid_level"] == pytest.approx(99.9)
    # 4) okunamayan pencere: gerekçe görünür, satırlar kapı durumunu yine gösterir
    rs = paper_rules.state_from_rows(NAME, daily=[], intraday=miss[1:], btc_rows=[], params=params)
    assert rs["ok"] is False and rs["reason"] == "NOT_ENOUGH_4H_BARS" and all(r["matched"] is None for r in rs["variations"])


# ================================================================== 17) yalıtım: diğer defterler bit-bit aynı
def _mixed_frames(now_ms: int) -> dict:
    rnd = np.random.default_rng(5)

    def frame(tf_ms_: int, n: int) -> pd.DataFrame:
        last = (now_ms // tf_ms_) * tf_ms_                  # son satır OLUŞAN bar (kapanmamış)
        c = 100 + np.cumsum(rnd.normal(0, 1, n))
        return pd.DataFrame({"timestamp": last - (n - 1 - np.arange(n)) * tf_ms_, "open": c, "high": c + 1, "low": c - 1, "close": c,
                             "volume": rnd.uniform(50, 150, n)})
    return {"1d": frame(86_400_000, 600), "4h": frame(H4, 600), "5m": frame(300_000, 600)}


def test_isolation(tmp_path, monkeypatch):
    specs = {"t2_trend_regime": ("trend", ("1d",), True), "m2_tsmom28": ("trend", ("1d",), True),
             "b1_box_fade": ("box", ("1d", "5m"), False), "d4_donchian_20_10": ("donchian", ("4h",), False)}
    now_ms = _now_ms()
    frames = _mixed_frames(now_ms)
    for name, (fam, tfs, btc) in specs.items():
        sp = paper_rules.spec_for(name)
        assert (sp.family, sp.timeframes, sp.needs_btc) == (fam, tfs, btc)
        assert paper_rules.rule_timeframes(name) == tfs and paper_rules.needs_btc(name) is btc
        d1, intra = paper_rules._frames_rows(name, frames, now_ms)
        assert d1 == closed_bars(ema200_trend.daily_rows_from_frame(frames["1d"], tail=320), now_ms=now_ms, tf="1d")
        tf = next((t for t in tfs if t != "1d"), None)
        want = None if tf is None else closed_bars(box_theory.rows_from_frame(frames[tf], tail=400), now_ms=now_ms, tf=tf)
        assert intra == want and paper_rules.intraday_for(name, frames, now_ms) == want, name
        if want:
            assert len(want) == 399 and "volume" not in want[-1], "400 bar, hacimsiz, oluşan bar düşer"
    # D4 eylemi: apply_action yolu değişmedi (hedef yok, özellik anahtarları aynı, ret kodları aynı, meta.signal aynı)
    d4rows = []
    closes = [100.0 + (0.5 if k % 2 else -0.5) for k in range(79)] + [104.0]
    prev = closes[0]
    for k, c in enumerate(closes):
        d4rows.append({"timestamp": (now_ms // H4) * H4 - (80 - k) * H4, "open": prev, "high": max(prev, c) + 1.0,
                       "low": min(prev, c) - 1.0, "close": c, "volume": 1.0})
        prev = c
    d4act = donchian_trend.decide("d4_donchian_20_10", rows=d4rows, now_ms=now_ms)
    assert d4act and d4act["action"] == "OPEN" and "target_r_from_entry" not in d4act and "variation" not in d4act
    eng = _engine(tmp_path, monkeypatch, _ov([{"name": "d4_donchian_20_10", "state_dir": "strategy_paper_trend4h"}]), symbols=1,
                  equity=EQUITY)
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    data = DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="test", tour_id="rid1", bars={"4h": d4rows[-1]["timestamp"]})

    def run(act: dict, price: float, sd: str):
        spec = BookSpec(name="d4_donchian_20_10", state_dir=sd, starting_equity_usdt=200.0, rule_params={"leverage": 1})
        bk = StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters, run_id="rid1", spec=spec)
        res = apply_action(dict(act), symbol=SYM, price=price, tick=_tick(price, now), now=now, ledger=bk.ledger, risk=bk.risk,
                           profile=bk.profile, state=bk._state({SYM: price}), filters=bk.filters_cache.get(SYM, MarketType.USDM_PERP),
                           run_id="rid1", reject=bk._reject, on_closed=bk._on_closed, on_opened=bk._on_opened, data=data)
        return res, bk
    res, bk = run(d4act, 104.1, "iso_a")
    pos = bk.ledger.positions[SYM]
    assert res == "OPENED" and pos.targets == [] and pos.setup_type == "trend_donchian"
    assert set(pos.features) == {"regime", "market_type", "strategy", "expected_r", "p_win", "data_source", "structure",
                                 "initial_stop", "initial_units", "liquidation_price"}, sorted(pos.features)
    assert set(pos.meta["signal"]) == {"signal_ts", "signal_close_ms", "signal_close", "atr14", "lab_algo"}
    assert pos.meta["signal"]["lab_algo"] == "TREND_DONCHIAN_20_10"
    # boş/yanlış-değerli yeni anahtarlar hiçbir şeyi değiştirmez (yalnız isteyen eylem)
    res2, bk2 = run(dict(d4act, target_r_from_entry=None, variation=None), 104.1, "iso_b")
    pos2 = bk2.ledger.positions[SYM]
    assert res2 == "OPENED" and pos2.targets == pos.targets and pos2.qty == pos.qty and pos2.stop == pos.stop
    assert set(pos2.features) == set(pos.features) and pos2.meta["size_scaled_to_cap"] == pos.meta["size_scaled_to_cap"]
    # açıkça verilen hedefler (T2/Box biçimi) yeniden yazılmaz
    res3, bk3 = run(dict(d4act, targets=[110.0]), 104.1, "iso_c")
    assert res3 == "OPENED" and [float(t) for t in bk3.ledger.positions[SYM].targets] == [110.0]
    # hedef girişten: boyut değişmez, yalnız hedef eklenir
    res4, bk4 = run(dict(d4act, target_r_from_entry=2.0), 104.1, "iso_d")
    pos4 = bk4.ledger.positions[SYM]
    assert res4 == "OPENED" and pos4.qty == pos.qty
    assert float(pos4.targets[0]) == pytest.approx(104.1 + 2.0 * (104.1 - d4act["stop"]))
    # ret kodları aynı
    for act, px, code in ((d4act, d4act["stop"] - 0.5, "STRATEGY_BAD_STOP"),
                          (d4act, d4act["stop"] + 0.05 * d4act["atr14"], "RISK_OUTSIDE_TESTED_RANGE")):
        res5, bk5 = run(act, px, "iso_" + code.lower())
        assert res5 == "REJECTED" and bk5.rejections == {code: 1}
    # D4 kararı çerçeveden hâlâ 400 barlık hacimsiz okumayla
    assert paper_rules.decide_for("d4_donchian_20_10", frames={"4h": pd.DataFrame(d4rows)}, btc_rows=None, now_ms=now_ms) == d4act


# ================================================================== 18) pencere okuyucu
def test_window_reader_carries_volume_and_drops_forming_bar(tmp_path, monkeypatch):
    now_ms = _now_ms()
    rows = pad(EXAMPLES[CV0]["match"], total=W + 10, t_last=(now_ms // H4) * H4)      # son satır OLUŞAN bar
    frames = {"4h": pd.DataFrame(rows)}
    got = paper_rules.intraday_for(NAME, frames, now_ms)
    assert got == B.window_rows(frames, now_ms) == paper_rules._frames_rows(NAME, frames, now_ms)[1]
    assert len(got) == W and got[-1]["timestamp"] == rows[-2]["timestamp"], "oluşan bar düşer, son 500 kapanmış bar"
    assert all("volume" in r for r in got) and got == rows[-W - 1:-1]
    assert D.window_from_rows(got, H4)[1] is None
    # hacim sütunu yoksa pencere reddedilir (fail-closed, gerekçe görünür)
    novol = {"4h": pd.DataFrame(rows).drop(columns=["volume"])}
    assert D.window_from_rows(paper_rules.intraday_for(NAME, novol, now_ms), H4) == (None, "VOLUME_MISSING")
    assert B.rows_from_frame(pd.DataFrame(rows).drop(columns=["low"])) == [] and B.rows_from_frame(None) == []
    # defterin veri hükmü ile kuralın okuduğu son bar aynı (_bar_binding)
    eng = _engine(tmp_path, monkeypatch, _ov(), symbols=1, equity=EQUITY)
    book = _book(eng, [])
    verdict = verify_paper_data(symbol=SYM, frames=frames, provenance=_prov(book, frames), run_id=book.run_id, need_btc=False,
                                as_of_ms=now_ms, tfs=book.rule.timeframes)
    assert verdict.ok and verdict.bars == {"4h": rows[-2]["timestamp"]}
    assert book._bar_binding(frames, verdict, now_ms) == (None, None)
    _step(book, frames, now_ms=now_ms, price=96.6)
    assert book.data_checks[SYM]["ok"] is True and book.data_checks[SYM]["bars"] == {"4h": rows[-2]["timestamp"]}


# ================================================================== 19) grafik analizi: motor kaydı = panel analizi
def test_chart_analysis_engine_record_matches_panel(registry, tmp_path, monkeypatch):
    """Motorun grafik analizi kaydı, panelin o anki analiziyle AYNI kimliği (analysis_id) taşır: iki taraf da kuralın KENDİ
    penceresini (son 500 kapanmış 4h bar, hacim dahil) ve defterin `rule_params`ını okur. Önceden motor C4 için gün içi
    satır ve ayar vermiyordu (NOT_ENOUGH_4H_BARS + NO_VARIATIONS) → panel her C4 grafiğinde motor kaydını 'defter/karar
    durumu farklı' gösteriyordu. D4, T2 ve ana defterin girdileri DEĞİŞMEDİ."""
    from tradingbot.chart_analysis import bars_from_frame, build_snapshot, closed_bars_at
    from tradingbot.chart_analysis_store import ChartAnalysisStore
    from tradingbot.engine_v3 import chart_rule_inputs
    ids = registry(VX)
    rp = {"leverage": 1, "entry_window_min": 60, "variations": list(ids)}
    now_ms = _now_ms()
    df = pd.DataFrame(_rows_at(EXAMPLES[CV0]["match"], now_ms, total=700))   # motor çerçevesi 700 bar; formasyon son kapanmış barda
    ov = _ov([{"name": NAME, "state_dir": "strategy_paper_candle4h", "rule_params": rp}]) | {"chart_analysis": {"enabled": True}}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=1, equity=EQUITY)
    eng.runner.last_frames[SYM] = {"4h": df}
    eng._frame_provenance = {SYM: {"market": "USDM_PERP", "source": "test"}}
    eng._chart_analysis_tour([SYM], {}, datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc))
    rec = ChartAnalysisStore(eng.cfg.state_path).latest("strategy_paper_candle4h", "USDM_PERP", SYM, "4h")
    assert rec is not None
    rs = rec["rule_state"]
    assert rs["ok"] is True and rs["n_bars"] == W and rs["volume_present"] is True and rs["window_reason"] is None
    assert [r["id"] for r in rs["variations"]] == ids and rs["variations"][0]["matched"] is True
    # panel yolu (dashboard/app.py): candles.load(n=600) → paper_rules.intraday_for; rule_params defterin özetinden
    bars = closed_bars_at(bars_from_frame(df, tail=400), as_of_ms=now_ms, tf="4h")
    pan = build_snapshot(symbol=SYM, market_type="USDM_PERP", timeframe="4h", tf_ms=H4,
                         book={"book_id": "strategy_paper_candle4h", "name": NAME, "atr_mult": 3.0, "rule_params": rp},
                         bars=bars, as_of_ms=now_ms, daily_rows=[], btc_daily_rows=[], gates={}, decision=None, plan=None,
                         intraday_rows=paper_rules.intraday_for(NAME, {"4h": df.tail(600)}, now_ms) or [],
                         position=None, history=[], entry_features=None, mark_price=None, cfg={},
                         code=rec["identity"]["code_sha"], cfg_hash=rec["identity"]["config_hash"])
    assert pan["rule_state"]["signal_ts"] == rs["signal_ts"]
    assert pan["analysis_id"] == rec["analysis_id"] and pan["decision_fingerprint"] == rec["decision_fingerprint"]
    # diğer defterler: motorun grafik girdileri eskisiyle aynı (rule_params geçmez; D4 grafik barları, diğerleri hiç)
    for name, want in (("d4_donchian_20_10", bars), ("t2_trend_regime", None), ("b1_box_fade", None), ("main", None)):
        bk, intra = chart_rule_inputs({"book_id": "x", "name": name, "atr_mult": 3.0, "rule_params": {"leverage": 1}},
                                      tf="4h", bars=bars, frames={"4h": df}, as_of_ms=now_ms)
        assert bk == {"book_id": "x", "name": name, "atr_mult": 3.0} and intra is want, name
    assert chart_rule_inputs({"book_id": "x", "name": "d4_donchian_20_10", "atr_mult": 3.0}, tf="1h", bars=bars, frames={},
                             as_of_ms=now_ms)[1] is None


# ================================================================== 20) replay: tam pencere şart
def test_replay_refuses_short_lookback():
    """Replay dilimi 500 kapanmış 4h bardan kısaysa C4 hiç sinyal üretmezdi (sessizce boş sonuç): açıkça hata verir."""
    strat = paper_rules.replay_strategy(NAME)
    with pytest.raises(ValueError, match="lookback_bars >= %d" % (W + 2)):
        strat(SYM, 0, {}, None, types.SimpleNamespace(lookback_bars=400, tf="4h"))

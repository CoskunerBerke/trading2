# -*- coding: utf-8 -*-
"""C4S · MUM VARYASYONLARI (4h, SIKI) — C4'ün sıkı eşi.

Kapının hüküm kipleri (`candle_variations.gate(vid, verdict_mode)`): standart kip C4'ün bugünkü davranışıdır; sıkı kip
kaydın `verdict_strict` alanını okur, gözlem onayını kabul etmez (`STRICT_NOT_STRONG`) ve sıkı hükmü olmayan kaydı
reddeder (`LAB_NO_STRICT_VERDICT`). Sıkı kapıdan geçen her varyasyon standart kapıdan da geçer (C4S ⊆ C4). Defter,
dedektör, giriş, stop, hedef ve zaman sınırı C4 ile AYNIDIR; iki defterin ret günlükleri birbirini bastırmaz.

Test varyasyonları kayda YALNIZ test süresince eklenir (`registry`, tests/test_candle_variations_book.py).
"""
from __future__ import annotations

import copy
import itertools
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from candle_variation_examples import EXAMPLES  # noqa: E402
from test_candle_variations_book import (APPROVAL, CV0, H4, NAME, READBACK, SYM, VX, VY, W, _book, _frames,  # noqa: E402
                                         _now_ms, _ov, _record, _rows_at, _step, registry)
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers  # noqa: E402
from tradingbot import candle_book as B  # noqa: E402
from tradingbot import candle_dsl as D  # noqa: E402
from tradingbot import candle_variations as V  # noqa: E402
from tradingbot import paper_rules  # noqa: E402
from tradingbot import signal_lab as L  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3  # noqa: E402
from tradingbot.engine_v3 import chart_rule_inputs  # noqa: E402
from tradingbot.strategy_paper import BookSpec, StrategyBook, book_specs  # noqa: E402

assert registry  # fikstür (içe aktarılarak bu modülde de kullanılır)

STRICT = "c4s_candle_variations_strict"
STRICT_DIR = "strategy_paper_candle4h_strict"
OBS = {"by": "user", "date": "2026-09-28", "observation": True, "run_id": "4242"}
VERDICTS = (L.V_STRONG, L.V_WEAK, L.V_NONE, L.V_THIN, L.V_LOSS)


def _gate_case(tmp_path, monkeypatch, *, approval=APPROVAL, vid="CV920_STRICT", **rec_over):
    """Tek varyasyon + tek kayıt; döner: (standart kapı, sıkı kapı)."""
    e = copy.deepcopy(VX)
    e.update(id=vid, readback=dict(READBACK), approval=copy.deepcopy(approval))
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", tmp_path)
    rec = _record(e, **rec_over)
    for k in [k for k, v in rec_over.items() if v is _DROP]:
        rec.pop(k)
    (tmp_path / ("%s.json" % vid)).write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(V, "VARIATIONS", (V.CV000_EXAMPLE_BULL3, e))
    V.reset_cache()
    return V.gate(vid), V.gate(vid, "strict")


_DROP = object()


def _sp(name: str, **kw):
    return paper_rules.build_params(name, rule_params=kw)


# ================================================================== 1) kapı kipleri
def test_strict_refuses_weak_strict_verdict_even_with_observation(tmp_path, monkeypatch):
    (sv, swhy), (tv, twhy) = _gate_case(tmp_path, monkeypatch, verdict=L.V_STRONG, verdict_strict=L.V_WEAK)
    assert swhy is None and sv is not None, "standart kip bugünkü davranış: GÜÇLÜ ADAY geçer"
    assert tv is None and twhy == "STRICT_NOT_STRONG"
    (sv, swhy), (tv, twhy) = _gate_case(tmp_path, monkeypatch, approval=OBS, verdict=L.V_STRONG, verdict_strict=L.V_WEAK)
    assert swhy is None and sv.approval["observation"] is True
    assert tv is None and twhy == "STRICT_NOT_STRONG", "gözlem onayı sıkı deftere varyasyon SOKMAZ"


def test_both_strong_pass_both(tmp_path, monkeypatch):
    (sv, swhy), (tv, twhy) = _gate_case(tmp_path, monkeypatch, verdict=L.V_STRONG, verdict_strict=L.V_STRONG)
    assert swhy is None and twhy is None and sv.id == tv.id == "CV920_STRICT"
    assert V.verdict_used("CV920_STRICT", "strict") == V.verdict_used("CV920_STRICT") == L.V_STRONG


def test_missing_or_unknown_strict_verdict(tmp_path, monkeypatch):
    for over in ({"verdict_strict": _DROP}, {"verdict_strict": None}, {"verdict_strict": "ÇOK İYİ"}):
        (sv, swhy), (tv, twhy) = _gate_case(tmp_path, monkeypatch, verdict=L.V_STRONG, **over)
        assert swhy is None and sv is not None, ("standart kip etkilenmez", over)
        assert tv is None and twhy == "LAB_NO_STRICT_VERDICT", over
        if over["verdict_strict"] in (_DROP, None):
            assert V.verdict_used("CV920_STRICT", "strict") is None


def test_unknown_mode_never_raises(tmp_path, monkeypatch):
    _gate_case(tmp_path, monkeypatch, verdict=L.V_STRONG, verdict_strict=L.V_STRONG)
    for mode in ("STRICT", "", None, 5, ["strict"], {"a": 1}, "loose"):
        assert V.gate("CV920_STRICT", mode) == (None, "UNKNOWN_VERDICT_MODE"), mode
    assert V.gate(None, "strict") == (None, "UNKNOWN_ID") and V.gate("CV999_NOPE", "strict") == (None, "UNKNOWN_ID")
    assert set(V.GATE_REASONS) <= set(V.GATE_REASONS_TR) and all(V.GATE_REASONS_TR[c] for c in V.GATE_REASONS)


def test_strict_order_and_unchanged_rules(tmp_path, monkeypatch):
    # KAYBETTİRİR iki kipte de VERDICT_LOSS; onay kuralları sıkı kipte de aynı sırada
    for v in (L.V_LOSS,):
        _s, (tv, twhy) = _gate_case(tmp_path, monkeypatch, verdict=v, verdict_strict=v)
        assert twhy == "VERDICT_LOSS" and _s == (None, "VERDICT_LOSS")
    _s, (tv, twhy) = _gate_case(tmp_path, monkeypatch, approval=None, verdict=L.V_WEAK, verdict_strict=L.V_WEAK)
    assert _s == (tv, twhy) == (None, "NO_APPROVAL")
    early = {"by": "user", "date": "2026-09-26", "observation": False, "run_id": "4242"}
    _s, (tv, twhy) = _gate_case(tmp_path, monkeypatch, approval=early, verdict=L.V_STRONG, verdict_strict=L.V_STRONG)
    assert _s == (tv, twhy) == (None, "APPROVAL_BEFORE_LAB")
    # geçersiz kayıt sıkı hükümden ÖNCE (sıra aynı); 4h dışı ölçüm sıkı hükümden SONRA
    _s, t = _gate_case(tmp_path, monkeypatch, verdict=L.V_STRONG, record_schema="candle_lab/1", verdict_strict=_DROP)
    assert _s == t == (None, "LAB_RECORD_INVALID")
    _s, t = _gate_case(tmp_path, monkeypatch, verdict=L.V_STRONG, verdict_strict=L.V_STRONG, primary_tf="1h")
    assert _s == t == (None, "LAB_NOT_4H")
    # tutarsız kayıt (sıkı GÜÇLÜ, standart değil) sıkı kapıdan geçmez → C4S ⊆ C4 yapı gereği
    _s, t = _gate_case(tmp_path, monkeypatch, verdict=L.V_WEAK, verdict_strict=L.V_STRONG)
    assert _s == (None, "OBSERVATION_NOT_ACKED") and t == (None, "STRICT_NOT_STRONG")


def test_strict_subset_of_standard(tmp_path, monkeypatch):
    """Her (hüküm, sıkı hüküm, gözlem) birleşiminde: sıkı kapıdan geçen standart kapıdan da geçer."""
    passed_strict = 0
    for v, vs, ap in itertools.product(VERDICTS, VERDICTS + (_DROP,), (APPROVAL, OBS)):
        (sv, swhy), (tv, twhy) = _gate_case(tmp_path, monkeypatch, approval=ap, verdict=v, verdict_strict=vs)
        if tv is not None:
            passed_strict += 1
            assert sv is not None and swhy is None, (v, vs, ap)
            assert v == vs == L.V_STRONG
    assert passed_strict == 2, "yalnız iki hüküm de GÜÇLÜ ADAY (gözlem bayrağından bağımsız)"


# ================================================================== 2) ayar ve config
def test_params_verdict_mode():
    assert B.DEFAULT_PARAMS.verdict_mode == "standard" and _sp(NAME).verdict_mode == "standard"
    assert _sp(NAME, verdict_mode="strict").verdict_mode == "strict", "C4'te sıkı kip isteğe bağlı (yalnız yeni yol)"
    assert _sp(STRICT).verdict_mode == "strict", "C4S kip yazılmazsa sıkı"
    assert _sp(STRICT, verdict_mode="strict").verdict_mode == "strict"
    assert paper_rules.spec_for(STRICT).family == "candle" and paper_rules.rule_timeframes(STRICT) == ("4h",)
    assert paper_rules.needs_btc(STRICT) is False and STRICT in paper_rules.CANDLE_VARIANTS
    for name, bad in ((NAME, "loose"), (NAME, None), (NAME, 1), (STRICT, "standard"), (STRICT, "loose")):
        with pytest.raises(ValueError):
            _sp(name, verdict_mode=bad)
    assert B.mode_for(STRICT, B.DEFAULT_PARAMS) == "strict", "C4S varsayılan ayarla bile sıkı kapıdan geçirir"
    assert B.mode_for(NAME, B.DEFAULT_PARAMS) == "standard"

    def cfg(name, rp):
        return load_v3({"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                                           "extra": [{"name": name, "state_dir": "c4x", "rule_params": rp}]}})
    cfg(STRICT, {"variations": [], "verdict_mode": "strict"})
    for name, rp in ((STRICT, {"verdict_mode": "standard"}), (NAME, {"verdict_mode": "loose"})):
        with pytest.raises(ConfigError, match="verdict_mode"):
            cfg(name, rp)


def test_repository_config_has_both_books():
    v3 = load_v3(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))
    specs = {b.name: b for b in book_specs(v3)}
    c4, c4s = specs[NAME], specs[STRICT]
    assert c4s.enabled and c4s.state_dir == STRICT_DIR and c4s.state_dir != c4.state_dir
    assert c4s.symbols == c4.symbols and len(c4s.symbols) == 23, "C4 ile AYNI 23 coin"
    assert c4s.starting_equity_usdt == c4.starting_equity_usdt == 200.0 and c4s.max_entry_drift_pct == 0.0
    assert c4s.breakeven_at_mfe_r == c4.breakeven_at_mfe_r == 0.0
    # liste boş başladı; etkinleştirme (belge §7 adım 8) bu testi kırmaz — kapı ve C4S ⊆ C4 ayrı testte
    vs = c4s.rule_params.get("variations")
    assert isinstance(vs, list) and len(vs) == len(set(vs))
    assert {k: v for k, v in c4s.rule_params.items() if k != "variations"} == {"leverage": 1, "entry_window_min": 60,
                                                                               "verdict_mode": "strict"}
    assert "verdict_mode" not in c4.rule_params, "C4'ün ayarı değişmedi"
    assert v3.structures.mode_for(STRICT) == "OFF"
    names = [b.name for b in book_specs(v3)]
    assert names.index(STRICT) == names.index(NAME) + 1


# ================================================================== 3) defter: aynı dedektör, aynı giriş
def test_strict_book_parity_with_c4(registry):
    (vid,) = registry(VX, verdict_strict=L.V_STRONG)
    now_ms = _now_ms()
    rows = _rows_at(EXAMPLES[CV0]["match"], now_ms)
    at = int(rows[-1]["timestamp"]) + H4 + 60_000
    a = paper_rules.decide_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, now_ms=at, params=_sp(NAME, variations=[vid]))
    b = paper_rules.decide_from_rows(STRICT, daily=[], intraday=rows, btc_rows=None, now_ms=at,
                                     params=_sp(STRICT, variations=[vid]))
    assert a and b and a["action"] == b["action"] == "OPEN"
    hit = D.detect_last(D.window_from_rows(rows, H4)[0], V.get(vid))
    assert a["stop"] == b["stop"] == hit.stop and a["target_r_from_entry"] == b["target_r_from_entry"] == 2.0
    assert a["variation"]["verdict_mode"] == "standard" and b["variation"]["verdict_mode"] == "strict"
    assert a["variation"]["verdict_used"] == b["variation"]["verdict_used"] == L.V_STRONG
    strip = lambda act: {k: v for k, v in act.items() if k not in ("name", "variation")}  # noqa: E731
    assert strip(a) == strip(b), "tek fark defter adı ve kip"
    va, vb = dict(a["variation"]), dict(b["variation"])
    for k in ("verdict_mode",):
        va.pop(k), vb.pop(k)
    assert va == vb
    # zaman sınırı da aynı yol (girişteki anlık görüntü)
    pos = {"opened_ts": at, "setup_type": "candle:" + vid, "features": {"candle_variation": {"max_hold_bars": 1}}}
    later = rows + [dict(rows[-1], timestamp=int(rows[-1]["timestamp"]) + H4)]
    assert B.decide(NAME, rows=later, position=pos) == dict(B.decide(STRICT, rows=later, position=pos), name=NAME)


def test_strict_book_refuses_what_c4_takes(registry):
    """Aynı sinyal: C4 gözlem onaylı ZAYIF İZ'i açar, C4S açmaz; rule_state nedeni gösterir."""
    (weak,) = registry(dict(copy.deepcopy(VX), id="CV921_WEAK"), approval=OBS, verdict=L.V_WEAK, verdict_strict=L.V_WEAK)
    (half,) = registry(dict(copy.deepcopy(VY), id="CV922_HALF"), verdict=L.V_STRONG, verdict_strict=L.V_WEAK)
    now_ms = _now_ms()
    rows = _rows_at(EXAMPLES[CV0]["match"], now_ms)
    at = int(rows[-1]["timestamp"]) + H4 + 60_000
    a = paper_rules.decide_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, now_ms=at,
                                     params=_sp(NAME, variations=[weak, half]))
    b = paper_rules.decide_from_rows(STRICT, daily=[], intraday=rows, btc_rows=None, now_ms=at,
                                     params=_sp(STRICT, variations=[weak, half]))
    assert a and a["lab_algo"] == weak and a["also_matched"] == [half] and b is None
    rs = paper_rules.state_from_rows(STRICT, daily=[], intraday=rows, btc_rows=None, params=_sp(STRICT, variations=[weak, half]))
    assert rs["verdict_mode"] == "strict" and rs["status_tr"] == "0/2 varyasyon etkin"
    rows_ = {r["id"]: r for r in rs["variations"]}
    assert rows_[weak]["gate_reason"] == rows_[half]["gate_reason"] == "STRICT_NOT_STRONG"
    assert rows_[half]["verdict_used"] == L.V_WEAK and rows_[half]["gate_reason_tr"]
    rs4 = paper_rules.state_from_rows(NAME, daily=[], intraday=rows, btc_rows=None, params=_sp(NAME, variations=[weak, half]))
    assert rs4["verdict_mode"] == "standard" and rs4["status_tr"] == "2/2 varyasyon etkin"


def test_strict_book_opens_through_real_chain(registry, tmp_path, monkeypatch):
    (vid,) = registry(VX, verdict_strict=L.V_STRONG)
    eng = _engine(tmp_path, monkeypatch, _ov([{"name": NAME, "state_dir": "strategy_paper_candle4h"},
                                              {"name": STRICT, "state_dir": STRICT_DIR}]), symbols=1, equity=EQUITY)
    _force_triggers(monkeypatch, False)
    assert {"strategy_paper_candle4h", STRICT_DIR} <= {b.key for b in eng.strategy_books}
    now_ms = _now_ms()
    frames = _frames(EXAMPLES[CV0]["match"], now_ms)
    hit = D.detect_last(D.window_from_rows(_rows_at(EXAMPLES[CV0]["match"], now_ms), H4)[0], V.get(vid))
    price = hit.close + 0.05
    c4 = _book(eng, [vid])
    spec = BookSpec(name=STRICT, state_dir=STRICT_DIR, starting_equity_usdt=200.0,
                    rule_params={"leverage": 1, "entry_window_min": 60, "variations": [vid], "verdict_mode": "strict"})
    c4s = StrategyBook(eng.cfg, profile=eng.profile, killswitch=eng.killswitch, filters_cache=eng.filters, run_id="rid1", spec=spec)
    for bk in (c4, c4s):
        _step(bk, frames, now_ms=now_ms, price=price)
    p4, p4s = c4.ledger.positions.get(SYM), c4s.ledger.positions.get(SYM)
    assert p4 is not None and p4s is not None, (dict(c4.rejections), dict(c4s.rejections))
    assert float(p4.stop) == float(p4s.stop) == pytest.approx(hit.stop) and p4.targets == p4s.targets and p4.qty == p4s.qty
    assert p4.setup_type == p4s.setup_type == "candle:" + vid
    cv, cvs = p4.features["candle_variation"], p4s.features["candle_variation"]
    assert cv["verdict_mode"] == "standard" and cvs["verdict_mode"] == "strict"
    assert cv["verdict_used"] == cvs["verdict_used"] == L.V_STRONG
    assert cvs["id"] == vid and cvs["lab_verdict"] == L.V_STRONG and cvs["definition_sha"] == cv["definition_sha"]


# ================================================================== 4) günlük: defter başına
def test_refusal_logs_are_per_book(registry, monkeypatch, caplog):
    (half,) = registry(dict(copy.deepcopy(VY), id="CV922_HALF"), verdict=L.V_STRONG, verdict_strict=L.V_WEAK)
    (noap,) = registry(dict(copy.deepcopy(VY), id="CV923_NOAP"), approval=None, verdict_strict=L.V_STRONG)
    now_ms = _now_ms()
    rows = _rows_at(EXAMPLES[CV0]["match"], now_ms)
    at = int(rows[-1]["timestamp"]) + H4 + 60_000
    monkeypatch.setattr(B, "_GATE_LOGGED", set())
    with caplog.at_level(logging.WARNING, logger="tradingbot.candle_book"):
        for _ in range(3):
            for name in (NAME, STRICT):
                paper_rules.decide_from_rows(name, daily=[], intraday=rows, btc_rows=None, now_ms=at,
                                             params=_sp(name, variations=[noap, half]))
    logged = sorted(r.getMessage() for r in caplog.records if "kapıdan geçmedi" in r.getMessage())
    assert len(logged) == 3, logged
    assert any(m.startswith("C4 ") and "%s kapıdan geçmedi: NO_APPROVAL" % noap in m for m in logged)
    assert any(m.startswith("C4S (sıkı) ") and "%s kapıdan geçmedi: NO_APPROVAL" % noap in m for m in logged), \
        "aynı (kimlik, neden) iki defterde ayrı ayrı günlüğe yazılır"
    assert any(m.startswith("C4S (sıkı) ") and "%s kapıdan geçmedi: STRICT_NOT_STRONG" % half in m for m in logged)


# ================================================================== 5) grafik analizi, panel, karne
def test_chart_inputs_and_labels(registry, tmp_path):
    (vid,) = registry(VX, verdict_strict=L.V_STRONG)
    now_ms = _now_ms()
    frames = {"4h": pd.DataFrame(_rows_at(EXAMPLES[CV0]["match"], now_ms, total=W + 5))}
    rp = {"leverage": 1, "variations": [vid], "verdict_mode": "strict"}
    book, intra = chart_rule_inputs({"book_id": STRICT_DIR, "name": STRICT, "atr_mult": 3.0, "rule_params": rp}, tf="4h",
                                    bars=[], frames=frames, as_of_ms=now_ms)
    book4, intra4 = chart_rule_inputs({"book_id": "strategy_paper_candle4h", "name": NAME, "atr_mult": 3.0, "rule_params": rp},
                                      tf="4h", bars=[], frames=frames, as_of_ms=now_ms)
    assert book["rule_params"] == rp and len(intra) == W and intra == intra4, "C4 ile aynı 500 barlık 4h pencere"
    from tradingbot.dashboard.state import StateReader
    st = tmp_path / "state"
    st.mkdir()
    (st / "strategy_paper_index.json").write_text(json.dumps({"books": [
        {"key": "strategy_paper_candle4h", "name": NAME}, {"key": STRICT_DIR, "name": STRICT}]}), encoding="utf-8")
    labels = {b["book_id"]: b["label"] for b in StateReader(st).books()}
    assert labels["strategy_paper_candle4h"] == "C4 · Mum varyasyonları (4h, PAPER)"
    assert labels[STRICT_DIR] == "C4S · Mum varyasyonları 4h (sıkı, PAPER)"
    sys.path.insert(0, str(ROOT / "scripts"))
    import bot_scorecard as S
    assert S.BOOKS[STRICT_DIR] == "C4S Mum varyasyonları 4h (sıkı, PAPER)"
    assert S.BOOKS["strategy_paper_candle4h"] == "C4 Mum varyasyonları (PAPER)"

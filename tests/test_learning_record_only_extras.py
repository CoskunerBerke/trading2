# -*- coding: utf-8 -*-
"""ÖĞRENME-EKSTRA GİRİŞLER NEDENE GÖRE (2026-10-03, sahip kararı): seçicilik-ekstra YALNIZ KAYIT.

* SINIF TABLOSU: `learning_mode.classify_unlock_codes` tek kaynaktır; tablo burada sabitlenir ve kodu üreten her yer (ana bot
  `_execute` / `_lm_baseline_blockers`, taban `RiskEngine.evaluate`, `strategy_paper._learning_tags` / `_baseline_blocks` /
  `_ledger_preview`, `ledger.can_open`, Formasyon `_try_open` / `_open_learning` / likidite bekleme kodları) AST ile taranır:
  sınıfsız yeni kod ya da sınıflandırılmamış yeni dinamik kaynak testi DÜŞÜRÜR.
* ÜÇ GİRİŞ YOLU (ana bot, strateji defterleri, Formasyon): `record_selectivity` kipinde seçicilik kodlu aday AÇILMAZ ve
  nedeni LEARNING_RECORD_ONLY olan karşı-olgusal olur (ayrılan kodlar + kullanacağı öğrenme boyutu); kapasite kodlu ve
  politika adayları `open` kipindekiyle BİREBİR aynı açılır. Box: öğrenme stop tabanı 0,5 (config.yaml) → %0,4 stop sinyal
  üretmez, %0,6 stop BOX_MIN_STOP_PCT ile GERÇEK açılır.
* AYNI SİNYALİN ÖNCEKİ KAYDI: başka nedenli BEKLEYEN kayıt yalnız-kayda dönüşür (`open`da açılış onu düşürürdü); sayaç
  KAYIT sayar, `rejections` tur başına olay, ana huni tur başına aşama.
* AŞAĞI AKIŞ: ayrılan kayıtlar araştırma politikasına (BLOCKED gözlemi) ve deneyim havuzuna GİRMEZ; mühürlü ortak deneyim
  toplayıcısı onları kod değişmeden `xp_cf` satırı olarak taşır (neden ailesi GATE: `rows.py`, mühürsüz) ve motorun
  `config_hash`ını taşır; karar günlüğünde SHADOW / `learning_record_only`.
* CONFIG: kip doğrulaması (kod varsayılanı `open`; `record_selectivity` karşı-olgusal kaydı ister). Karne:
  `test_bot_scorecard_record_only_monthly.py`; `open` kipinin çok turlu ve doğrudan yol bit-aynılığı (943345c'ye karşı):
  `test_learning_record_only_tours.py`.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import sys
from datetime import timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_learning_mode_books as LB  # noqa: E402
import test_learning_mode_main as LMM  # noqa: E402
import test_learning_mode_pattern as LP  # noqa: E402

import tradingbot.learning_mode as LM  # noqa: E402
from tradingbot.config import load_config  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import ConfigError, utc_now  # noqa: E402
from tradingbot.learn.shadow import ShadowTrade  # noqa: E402
from tradingbot.pattern_trader.strategy import PL_REJECTED  # noqa: E402

UTC = timezone.utc
REC = LM.EXTRA_RECORD_SELECTIVITY
RO = LM.LEARNING_RECORD_ONLY

# ============================================================================ 1) sınıf tablosu (sabit)
#: TAM tablo — kod → sınıf ("capacity" | "box_exception" | "selectivity"). Değiştirmek bir SAHİP kararıdır.
TABLE = {
    # (A) kapasite / emir boyutu
    "TOTAL_OPEN_RISK": "capacity", "MAX_POSITIONS": "capacity", "MAX_POSITIONS_MARKET": "capacity",
    "MAX_POSITION_PCT": "capacity", "MIN_ORDER_CONFLICT": "capacity", "NO_TRADE_MIN_ORDER_CONFLICT": "capacity",
    "INSUFFICIENT_MARGIN": "capacity", "STEP_ZERO_QTY": "capacity", "MIN_QTY": "capacity", "MAX_QTY": "capacity",
    "MIN_NOTIONAL": "capacity", "LEVERAGE_TOO_HIGH": "capacity", "RISK_ABOVE_CAP_AFTER_ROUNDING": "capacity",
    "MARGIN_UTILIZATION": "capacity", "SPOT_ALLOCATION": "capacity", "CLUSTER_CAP": "capacity",
    "ALTCOIN_EXPOSURE": "capacity", "RISK_PER_TRADE": "capacity", "LEVERAGE_CAP": "capacity",
    "ALREADY_OPEN_SAME_SYMBOL": "capacity", "ALREADY_OPEN": "capacity", "OPPOSITE_EXPOSURE_CONFLICT": "capacity",
    # (C) Box istisnası (A gibi davranır)
    "BOX_MIN_STOP_PCT": "box_exception",
    # (B) seçicilik
    "NEGATIVE_NET_EDGE": "selectivity", "RESEARCH_SIZE_ONLY": "selectivity", "SIZE_MULTIPLIER_ZERO": "selectivity",
    "BOOK_UNIVERSE": "selectivity", "NOT_IN_PROTOCOL_UNIVERSE": "selectivity", "RR_BELOW_MIN_AT_ENTRY": "selectivity",
    "RR_BELOW_MIN_AFTER_ROUNDING": "selectivity", "COOLDOWN_AFTER_LOSS": "selectivity", "THIN_DEPTH": "selectivity",
    "LIQUIDITY_UNKNOWN": "selectivity", "DEPTH_UNKNOWN": "selectivity", "KILL_SWITCH_ACTIVE": "selectivity",
    "DAILY_LOSS": "selectivity", "WEEKLY_LOSS": "selectivity", "MAX_DRAWDOWN": "selectivity",
    "CONSEC_LOSS_COOLDOWN": "selectivity", "SYMBOL_COOLDOWN": "selectivity", "STOP_PRESENT": "selectivity",
    "SPOT_NO_SHORT": "selectivity", "SPREAD": "selectivity", "MIN_EXPECTED_R": "selectivity", "LIQ_BUFFER": "selectivity",
    "BAD_PRICE": "selectivity", "RISK_DENIED": "selectivity", "BASELINE_UNKNOWN": "selectivity",
}
#: (B) önek kuralları (ana bot `CANDLE_VETO:`, `REGIME_VETO:`, `STRUCTURE:`, `LEVERAGE_GATE_BLOCKED:`; defterler `STRUCTURE_`)
PREFIXES = ("CANDLE_VETO", "REGIME_VETO", "STRUCTURE", "LEVERAGE_GATE_BLOCKED")


def test_classification_table_is_pinned_and_the_sets_are_disjoint():
    cap, box, sel = LM.UNLOCK_CAPACITY, LM.UNLOCK_BOX_EXCEPTION, LM.UNLOCK_SELECTIVITY
    assert not (cap & box) and not (cap & sel) and not (box & sel)
    got = {**{c: "capacity" for c in cap}, **{c: "box_exception" for c in box}, **{c: "selectivity" for c in sel}}
    assert got == TABLE
    assert LM.UNLOCK_SELECTIVITY_PREFIXES == PREFIXES
    for code, cls in TABLE.items():
        assert LM.classify_unlock_code(code) == ("selectivity" if cls == "selectivity" else "capacity"), code
    # öneksiz kapasite kodu YALNIZ tam eşleşmedir; bilinmeyen kod seçiciliktir (fail-closed)
    for code in ("CANDLE_VETO:C1_AGAINST", "REGIME_VETO:R1_BTC_DOWN", "STRUCTURE:OPPOSING_CONFIRMED",
                 "STRUCTURE_WAIT_TRIGGER", "LEVERAGE_GATE_BLOCKED:STOP_TOO_FAR_FOR_LEVERAGE", "TOTAL_OPEN_RISK:x",
                 "SOMETHING_NEW", "", "BOX_MIN_STOP_PCT_X"):
        assert LM.classify_unlock_code(code) == LM.CLASS_SELECTIVITY, code


@pytest.mark.parametrize("codes,cls", [
    ([], "policy"), (None, "policy"), (["", "  "], "policy"),
    (["TOTAL_OPEN_RISK"], "capacity"), (["MAX_POSITIONS", "INSUFFICIENT_MARGIN", "MIN_NOTIONAL"], "capacity"),
    (["BOX_MIN_STOP_PCT"], "capacity"), (["BOX_MIN_STOP_PCT", "TOTAL_OPEN_RISK"], "capacity"),
    (["NO_TRADE_MIN_ORDER_CONFLICT", "MIN_ORDER_CONFLICT"], "capacity"),
    (["NEGATIVE_NET_EDGE"], "selectivity"), (["TOTAL_OPEN_RISK", "STRUCTURE:OPPOSING_CONFIRMED"], "selectivity"),
    (["BOX_MIN_STOP_PCT", "BOOK_UNIVERSE"], "selectivity"), (["BASELINE_UNKNOWN"], "selectivity"),
    (["INSUFFICIENT_MARGIN", "WHATEVER_NEW"], "selectivity"), (["RR_BELOW_MIN_AT_ENTRY", "MAX_POSITIONS"], "selectivity")])
def test_classify_unlock_codes(codes, cls):
    assert LM.classify_unlock_codes(codes) == cls


# ---------------------------------------------------------------------------- kod tabanı taraması
def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _fn(tree: ast.Module, name: str) -> ast.FunctionDef:
    hits = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(hits) == 1, (name, len(hits))
    return hits[0]


def _appends(fn, names: set[str], module=None) -> tuple[set[str], set[str], list[str]]:
    """Listeye eklenen kodlar: (sabitler, önekler, dinamik kaynaklar). Bilinmeyen biçim dinamik sayılır (test sabitler)."""
    codes, prefixes, dynamic = set(), set(), []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in names:
            if node.func.attr == "append":
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    codes.add(arg.value)
                elif isinstance(arg, ast.BinOp) and isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str):
                    prefixes.add(arg.left.value.split("%")[0])
                elif isinstance(arg, ast.Name) and module is not None and isinstance(getattr(module, arg.id, None), str):
                    codes.add(getattr(module, arg.id))
                else:
                    dynamic.append("append:" + ast.unparse(arg))
            elif node.func.attr in ("extend", "insert", "__iadd__"):
                dynamic.append(node.func.attr + ":" + ast.unparse(node.args[-1]))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            dynamic.append("+=:" + ast.unparse(node.value))
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            dynamic.append("=:" + ast.unparse(node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            dynamic.append("=:" + ast.unparse(node.value))
    return codes, prefixes, sorted(dynamic)


def _return_strings(fn) -> set[str]:
    """`return` ifadelerindeki ret kodları: doğrudan dönen dize ya da dönen ifadedeki liste sabitlerinin dize öğeleri."""
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                out.add(node.value.value)
            for lst in ast.walk(node.value):
                if isinstance(lst, ast.List):
                    out |= {c.value for c in lst.elts if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return out


def _risk_engine_codes() -> set[str]:
    fn = _fn(_tree("tradingbot/risk/engine.py"), "evaluate")
    return {n.args[0].value for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "add" and n.args and isinstance(n.args[0], ast.Constant)}


def _can_open_codes() -> set[str]:
    import tradingbot.accounting.futures_ledger as FL
    fn = _fn(_tree("tradingbot/accounting/futures_ledger.py"), "can_open")
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and len(node.value.elts) == 2:
            ok, why = node.value.elts
            if isinstance(ok, ast.Constant) and ok.value is False:
                out.add(getattr(FL, why.id) if isinstance(why, ast.Name) else why.value)
    return out


def _scan() -> tuple[set[str], set[str]]:
    """Kod tabanının `learning_unlocked_by`a yazabileceği bütün kodlar ve önekler. Dinamik kaynaklar SABİTLENİR."""
    import tradingbot.learning_basis as LBASIS
    import tradingbot.pattern_trader.book as PB
    codes, prefixes = set(), set()
    risk = _risk_engine_codes()
    assert {"TOTAL_OPEN_RISK", "MIN_ORDER_CONFLICT", "KILL_SWITCH_ACTIVE"} <= risk
    # ---- ana bot
    eng = _tree("tradingbot/engine_v3.py")
    c, p, dyn = _appends(_fn(eng, "_execute_locked"), {"lm_unlocked"})
    assert dyn == ["+=:_bcodes", "=:[]", "=:[c for c in lm_unlocked if c not in _LM_ECON_CODES] + _pcodes"], dyn
    codes |= c
    prefixes |= p
    codes |= set(LBASIS.ECONOMICS_CODES) | (_return_strings(_fn(_tree("tradingbot/learning_basis.py"), "economics_codes")))
    bb = _return_strings(_fn(eng, "_lm_baseline_blockers"))       # + list(rdb.reasons) → taban RiskEngine
    assert bb == {"INSUFFICIENT_MARGIN", "BASELINE_UNKNOWN"}, bb
    codes |= bb | risk
    # ---- strateji defterleri
    sp = _tree("tradingbot/strategy_paper.py")
    c, p, dyn = _appends(_fn(sp, "_learning_tags"), {"unlocked"})
    assert dyn == ["=:[]"], dyn
    codes |= c
    prefixes |= p
    c, p, dyn = _appends(_fn(sp, "_open_learning"), {"unlocked"})
    assert (c, p) == (set(), set()) and dyn == ["+=:[x for x in bcodes if x not in unlocked]",
                                                "=:[str(x) for x in tags.get('unlocked_by') or []]"], dyn
    blk = _return_strings(_fn(sp, "_baseline_blocks"))           # + RiskEngine nedenleri + `_ledger_preview` kodu
    assert blk == {"RISK_DENIED", "BASELINE_UNKNOWN"}, blk
    codes |= blk | _return_strings(_fn(sp, "_ledger_preview")) | _can_open_codes()
    # ---- Formasyon
    pb = _tree("tradingbot/pattern_trader/book.py")
    c, p, dyn = _appends(_fn(pb, "_try_open"), {"unlocked"})
    assert p == set() and dyn == ["=:[]"], dyn
    codes |= c
    c, p, dyn = _appends(_fn(pb, "_open_learning"), {"tags"}, module=PB)
    assert p == set() and dyn == ["=:list(unlocked)", "extend:(c for c in bcodes if c not in tags)",
                                  "extend:(pl.get('liquidity_wait') or {}).get('codes') or []"], dyn
    codes |= c
    waits = {n.args[1].value for n in ast.walk(pb) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_liquidity_wait" and len(n.args) > 1 and isinstance(n.args[1], ast.Constant)}
    assert waits == {"LIQUIDITY_UNKNOWN", "DEPTH_UNKNOWN"}, waits
    codes |= waits
    return codes, prefixes


def test_every_code_the_code_base_can_write_into_learning_unlocked_by_has_a_class():
    codes, prefixes = _scan()
    explicit = LM.UNLOCK_CAPACITY | LM.UNLOCK_BOX_EXCEPTION | LM.UNLOCK_SELECTIVITY
    assert codes - explicit == set(), "sınıfsız yeni kod: learning_mode.UNLOCK_* tablosuna ekle (sahip kararı)"
    assert explicit - codes == set(), "tabloda artık üretilmeyen kod"
    assert prefixes == {"CANDLE_VETO:", "REGIME_VETO:", "STRUCTURE:", "STRUCTURE_", "LEVERAGE_GATE_BLOCKED:"}
    assert all(p.startswith(LM.UNLOCK_SELECTIVITY_PREFIXES) for p in prefixes)


def test_learning_unlocked_by_is_written_only_by_the_three_entry_paths():
    """Etiketi (sözlük anahtarı olarak) KURAN dosyalar yalnız üç giriş yoludur; yenisi gelirse kodları sınıflanmalı."""
    writers = set()
    for p in sorted((ROOT / "tradingbot").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "learning_unlocked_by"
                                               for k in n.keys):
                writers.add(p.relative_to(ROOT).as_posix())
    assert writers == {"tradingbot/engine_v3.py", "tradingbot/strategy_paper.py", "tradingbot/pattern_trader/learning.py"}


# ============================================================================ 2) anahtar ve config
def test_record_only_needs_learning_on_the_record_mode_and_a_selectivity_code():
    bl = LB._bl("d4_donchian_20_10", extra_entries=REC)
    assert LM.record_only(["BOOK_UNIVERSE"], LB._bl("d4_donchian_20_10")) is None, "kod varsayılanı open"
    assert LM.record_only(["BOOK_UNIVERSE"], dataclasses.replace(bl, extra_entries=LM.EXTRA_OPEN)) is None
    assert LM.record_only(["BOOK_UNIVERSE"], dataclasses.replace(bl, on=False)) is None
    assert LM.record_only(["BOOK_UNIVERSE"], None) is None
    assert LM.record_only([], bl) is None and LM.record_only(["TOTAL_OPEN_RISK", "BOX_MIN_STOP_PCT"], bl) is None
    d = LM.record_only(["TOTAL_OPEN_RISK", "BOOK_UNIVERSE", "BOOK_UNIVERSE", "BASELINE_UNKNOWN"], bl)
    assert d == {"reason": RO, "codes": ["TOTAL_OPEN_RISK", "BOOK_UNIVERSE", "BASELINE_UNKNOWN"],
                 "selectivity_codes": ["BOOK_UNIVERSE", "BASELINE_UNKNOWN"], "class": "selectivity", "mode": REC}
    assert LM.counterfactual_ok(RO) and LM.is_record_only_cf([RO, "X"]) and not LM.is_record_only_cf(["X", RO])


def test_config_switch_defaults_to_open_validates_and_reaches_every_book_view():
    assert load_v3({}).learning_mode.extra_entries == "open"
    for v in ("open", "record_selectivity"):
        assert load_v3({"learning_mode": {"extra_entries": v}}).learning_mode.extra_entries == v
    for bad in ("record", "OPEN", "", None, True, 1):
        with pytest.raises(ConfigError, match="extra_entries"):
            load_v3({"learning_mode": {"extra_entries": bad}})
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.v3.learning_mode.extra_entries == REC
    assert cfg.v3.learning_mode.books["b1_box_fade"].min_stop_pct == 0.5
    lm = LM.LearningMode.from_config(cfg, mode_gate=lambda: (True, "OK"))
    lm.refresh()
    for name in ("main", "t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10", "c4_candle_variations",
                 "pattern_trader"):
        bl = lm.book(name)
        assert bl is not None and bl.extra_entries == REC and bl.to_dict()["extra_entries"] == REC, name
    assert lm.status()["extra_entries"] == REC
    # kod varsayılanında görünüm/özet/sağlık alanı YOK (open kipinde dosyalar bit-aynı)
    off = LM.LearningMode(LB._section(main={"enabled": True}), mode_gate=lambda: (True, "OK"))
    off.refresh()
    assert off.extra_entries == "open" and "extra_entries" not in off.book("main").to_dict()
    assert "extra_entries" not in off.status()
    # motorun karar kimliği (`config_hash`, karar günlüğü satırları): varsayılan `open` özete girmez, `record_selectivity` girer
    from types import SimpleNamespace

    import tradingbot.engine_v3 as EV

    def h(raw):
        return EV.TradingEngineV3.config_hash(SimpleNamespace(cfg=SimpleNamespace(v3=load_v3(raw))))
    assert h({}) == h({"learning_mode": {"extra_entries": "open"}}) != h({"learning_mode": {"extra_entries": REC}})


def test_record_selectivity_requires_counterfactual_recording():
    """Kayıt kipi karşı-olgusal kapalıyken adayı sessizce kaybederdi → açık ConfigError; `open` kipi etkilenmez."""
    with pytest.raises(ConfigError, match="counterfactual: true"):
        load_v3({"learning_mode": {"extra_entries": REC, "counterfactual": False}})
    assert load_v3({"learning_mode": {"extra_entries": "open", "counterfactual": False}}).learning_mode.counterfactual is False
    assert load_v3({"learning_mode": {"counterfactual": False}}).learning_mode.extra_entries == "open"


def test_decision_journal_classifies_record_only_as_a_learning_shadow():
    from tradingbot.learn.decision_journal import SHADOW, classify_outcome
    got = classify_outcome({"block_code": RO, "learning_record_only": {"codes": ["NEGATIVE_NET_EDGE"]}},
                           is_actionable=True, has_valid_plan=True, shadowed=False)
    assert got == (SHADOW, "learning_record_only", RO)


def test_shared_experience_collector_carries_the_engines_config_hash_in_every_mode(tmp_path, monkeypatch):
    """Mühürlü toplayıcı motorun önbelleğini okur, yoksa kendi formülüne düşer (o formül `open`ı düşmez). Motor özeti
    toplayıcıdan ÖNCE hesaplar → xp satırlarının `config_hash`ı `open`/yok kipinde 943345c'ninkiyle aynı kalır."""
    from dataclasses import asdict
    from types import SimpleNamespace

    import tradingbot.engine_v3 as EV
    from tradingbot.core import payload_hash
    from tradingbot.shared_experience.collector import SharedExperienceCollector
    monkeypatch.setattr(SharedExperienceCollector, "step", lambda self, *a, **k: None)

    class _Eng:
        config_hash = EV.TradingEngineV3.config_hash
        _shared_experience_step = EV.TradingEngineV3._shared_experience_step

    hashes = {}
    for name, sec in (("absent", {}), ("open", {"extra_entries": "open"}), ("rec", {"extra_entries": REC})):
        eng = _Eng()
        eng.cfg = SimpleNamespace(v3=load_v3({"shared_experience": {"enabled": True, "mode": "RECORD"}, "learning_mode": sec}),
                                  state_path=tmp_path / name / "state", cache_path=None, code_sha="test")
        eng._shared_experience_step([], {}, [], utc_now())        # ilk tur: karar listesi boş
        xp = eng.__dict__["_shared_xp"]
        assert isinstance(xp, SharedExperienceCollector) and xp.config_hash == eng.config_hash(), name
        hashes[name] = xp.config_hash
    head = asdict(load_v3({}))
    head.pop("shared_experience")
    head.pop("m2x_aggressive")                                   # M2X (2026-10-05): karar kimliğine GİRMEZ
    head["learning_mode"].pop("extra_entries")                   # 943345c'nin V3Config alanları
    assert hashes["absent"] == hashes["open"] == payload_hash(head) != hashes["rec"]


# ============================================================================ 3) ANA BOT
def _lm_rec(**kw) -> dict:
    d = LMM._lm(**kw)
    d["learning_mode"]["extra_entries"] = REC
    return d


_IDS = {"id", "trade_id", "position_id"}


def _strip(x):
    """Rastgele kimlikler (ULID: zaman + rastgele) iki koşuda farklıdır; karar alanları değil → düşer."""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in _IDS}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _pos_view(eng) -> str:
    return json.dumps(_strip({s: p.to_dict() for s, p in eng.ledger2.positions.items()}), sort_keys=True, default=str)


def test_main_selectivity_extra_is_recorded_not_opened_with_codes_and_learning_params(tmp_path, monkeypatch):
    now = utc_now().replace(microsecond=0)
    runs = {}
    for name, lm in (("open", LMM._lm()), ("rec", _lm_rec())):
        eng = LMM._eng(tmp_path / name, monkeypatch, lm)
        decisions, chief, briefs, marks = LMM._cands(eng, LMM.SYMS[:1], opp=LMM.NEG)
        opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
        runs[name] = (eng, opened, LMM._log(risk_log, LMM.SYMS[0]))
    eng, opened, e = runs["open"]
    assert len(opened) == 1 and "NEGATIVE_NET_EDGE" in eng.ledger2.positions[LMM.SYMS[0]].meta["learning"]["learning_unlocked_by"]
    assert "learning_record_only" not in eng._funnel, "open kipinde huni anahtarı yok"
    eng, opened, e = runs["rec"]
    assert opened == [] and eng.ledger2.positions == {}
    assert e["block_code"] == RO and e["learning_fit"]["ok"] is True and e["risk_allowed"] is True, e
    assert "NEGATIVE_NET_EDGE" in e["learning_record_only"]["selectivity_codes"]
    (cf,) = LMM._main_cfs(eng)
    assert cf.reason_not_opened[0] == RO and "NEGATIVE_NET_EDGE" in cf.reason_not_opened[1:]
    ro = cf.features[LM.RECORD_ONLY_FEATURE]
    assert ro["class"] == "selectivity" and ro["mode"] == REC and "NEGATIVE_NET_EDGE" in ro["codes"]
    assert ro["book"] == "main" and ro["size_rule"] == LMM.SIZE_SLOT and ro["notional"] > 0 and ro["leverage"] >= 1
    assert ro["exploration"] == "NEG_EDGE" and cf.features["learning_fit"]["ok"] is True
    f = eng._funnel
    assert f["learning_record_only"] == 1 and f["counterfactual_recorded"] == 1 and f["opened"] == 0
    assert f["capacity_approved"] == f["opened"] + f["exchange_rejected"], "huni değişmezi korunur"


def test_main_capacity_extra_and_policy_candidates_open_exactly_as_with_open(tmp_path, monkeypatch):
    now = utc_now().replace(microsecond=0)
    out = {}
    for name, lm in (("open", LMM._lm()), ("rec", _lm_rec())):
        for case in ("capacity", "policy"):
            eng = LMM._eng(tmp_path / name / case, monkeypatch, lm)
            if case == "capacity":
                LMM._prefill(eng, notional=40.0, lev=2, stop_pct=5.0, n=3, now=now)     # %6 taban bütçesi dolu
            decisions, chief, briefs, marks = LMM._cands(eng, LMM.SYMS[:2])
            opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
            tags = [eng.ledger2.positions[s].meta["learning"]["learning_unlocked_by"] for s in LMM.SYMS[:2]
                    if s in eng.ledger2.positions]
            out[(name, case)] = (opened, _pos_view(eng), json.dumps(_strip(risk_log), sort_keys=True, default=str), tags,
                                 [t.reason_not_opened for t in LMM._main_cfs(eng)])
    for case in ("capacity", "policy"):
        assert out[("rec", case)] == out[("open", case)], case
    opened, _pv, _rl, tags, cfs = out[("rec", "capacity")]
    assert len(opened) == 2 and all(t and LM.classify_unlock_codes(t) == "capacity" for t in tags) and cfs == []
    opened, _pv, _rl, tags, cfs = out[("rec", "policy")]
    assert len(opened) == 2 and tags == [[], []] and cfs == []


def test_main_mixed_capacity_and_selectivity_codes_are_record_only(tmp_path, monkeypatch):
    now = utc_now().replace(microsecond=0)
    eng = LMM._eng(tmp_path, monkeypatch, _lm_rec())
    LMM._prefill(eng, notional=40.0, lev=2, stop_pct=5.0, n=3, now=now)
    # RESEARCH_SIZE_ONLY (çarpan 0,25 → taban boyutu > 0, taban kapıları ölçülür) + %6 bütçe dolu → TOTAL_OPEN_RISK
    decisions, chief, briefs, marks = LMM._cands(eng, LMM.SYMS[:1], opp=LMM.RES)
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    e = LMM._log(risk_log, LMM.SYMS[0])
    assert opened == [] and e["block_code"] == RO
    assert {"TOTAL_OPEN_RISK", "RESEARCH_SIZE_ONLY"} <= set(e["learning_record_only"]["codes"])
    assert e["learning_record_only"]["selectivity_codes"] == ["RESEARCH_SIZE_ONLY"]


def test_main_record_only_counterfactual_is_superseded_when_the_signal_later_opens(tmp_path, monkeypatch):
    """Aynı sinyal sonraki turda politika adayı olarak açılırsa yalnız-kayıt kaydı düşer (çift sayım yok, mevcut kural)."""
    now = utc_now().replace(microsecond=0)
    eng = LMM._eng(tmp_path, monkeypatch, _lm_rec())
    decisions, chief, briefs, marks = LMM._cands(eng, LMM.SYMS[:1], opp=LMM.NEG)
    assert eng._execute(decisions, chief, briefs, None, marks, now)[0] == [] and len(LMM._main_cfs(eng)) == 1
    decisions[LMM.SYMS[0]].opportunity = None
    eng.run_id = "run_2"
    opened, _ = eng._execute(decisions, chief, briefs, None, marks, now)
    assert len(opened) == 1 and LMM._main_cfs(eng) == [] and eng._funnel["counterfactual_superseded"] == 1


def test_main_pending_counterfactual_of_the_same_signal_becomes_record_only(tmp_path, monkeypatch):
    """Tur 1 kill switch → KILL_SWITCH_ACTIVE karşı-olgusalı; tur 2 (aynı sinyal) seçicilik-ekstra. `open`: açılır, eski kayıt
    düşer (supersede). `record_selectivity`: açılmaz; tekillik yeni kaydı engeller → ESKİ kayıt yalnız-kayda dönüşür (normal
    karşı-olgusal gibi etiketlenip araştırma/deneyim/karneye girmesin). Huni anahtarı her turdaki olayı sayar."""
    now = utc_now().replace(microsecond=0)
    out = {}
    for name, lm in (("open", LMM._lm()), ("rec", _lm_rec())):
        eng = LMM._eng(tmp_path / name, monkeypatch, lm)
        eng.killswitch.trip("MANUAL", "test")
        decisions, chief, briefs, marks = LMM._cands(eng, LMM.SYMS[:1], opp=LMM.NEG)
        assert eng._execute(decisions, chief, briefs, None, marks, now)[0] == []
        assert [t.reason_not_opened[0] for t in LMM._main_cfs(eng)] == ["KILL_SWITCH_ACTIVE"], name
        eng.killswitch.reset("test", "reset")
        funnels = []
        for k in (2, 3):
            eng.run_id = "run_%d" % k
            opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
            funnels.append(dict(eng._funnel))
        out[name] = (eng, opened, funnels, LMM._log(risk_log, LMM.SYMS[0]))
    eng, _opened, _f, _e = out["open"]
    assert LMM.SYMS[0] in eng.ledger2.positions and LMM._main_cfs(eng) == [] and eng.shadow.meta.get("lm_superseded") == 1
    eng, opened, funnels, e = out["rec"]
    assert opened == [] and eng.ledger2.positions == {} and e["block_code"] == RO
    (cf,) = LMM._main_cfs(eng)
    assert cf.reason_not_opened[0] == RO and "NEGATIVE_NET_EDGE" in cf.reason_not_opened
    assert "KILL_SWITCH_ACTIVE" in cf.reason_not_opened and cf.outcome is None
    ro = cf.features[LM.RECORD_ONLY_FEATURE]
    assert ro["retagged_from"] == "KILL_SWITCH_ACTIVE" and ro["book"] == "main" and ro["notional"] > 0
    assert eng.shadow.meta.get("lm_retagged") == 1 and not eng.shadow.meta.get("lm_superseded")
    assert [f["learning_record_only"] for f in funnels] == [1, 1], "huni: her turdaki olay (diğer aşamalar gibi)"
    assert [f["counterfactual_recorded"] for f in funnels] == [0, 0], "yeni kayıt yazılmadı"
    from tradingbot.learn.shadow import ShadowBook
    (disk,) = [t for t in ShadowBook(eng.shadow.path).trades if t.book == "main"]
    assert disk.reason_not_opened == cf.reason_not_opened


def test_record_only_counterfactuals_never_feed_the_research_policy(tmp_path, monkeypatch):
    """Etiketlenen yalnız-kayıt kaydı için BLOCKED gözlemi YAZILMAZ (bekleyen eşleşme olsa bile); normal ana karşı-olgusal
    eskisi gibi gözlenir. Deneyim havuzu da yalnız-kayıt kaydını almaz."""
    eng = LMM._eng(tmp_path, monkeypatch, _lm_rec())
    now = utc_now().replace(microsecond=0)

    def _sh(i, reasons):
        return ShadowTrade(id="cf_%d" % i, plan_id="sig%d" % i, symbol=LMM.SYMS[i], market_type="USDM_PERP",
                           direction="LONG", created_at=(now - timedelta(days=2)).isoformat(), entry=100.0, stop=95.0,
                           targets=[110.0], horizon_bars=6, variant="as_planned", reason_not_opened=list(reasons),
                           label_ts=(now - timedelta(days=1)).isoformat(), tf_minutes=240, book="main",
                           signal_key="sig%d" % i, label_kind="TARGET_STOP_TIME", features={})
    rec_only, normal = _sh(0, [RO, "NEGATIVE_NET_EDGE"]), _sh(1, ["TOTAL_OPEN_RISK"])
    eng.shadow.trades += [rec_only, normal]
    for t in (rec_only, normal):
        eng.research.pending["p_" + t.id] = {"policy_id": "pol", "trade_id": t.id, "decision": {"reasons": ["x"]}}
    seen = []
    monkeypatch.setattr(eng.research, "observe", lambda pid, **kw: seen.append(kw["trade_id"]))

    def _label(rows, *a, **k):
        for t in rows:
            t.outcome = {"r_multiple": 1.0, "r_net": 0.8, "label_version": "cf_label_v3", "exit_reason": "target"}
            t.labeled_at = now.isoformat()
        return len(rows), []
    monkeypatch.setattr("tradingbot.learning_cf.label_records", _label)
    assert eng._lm_label_main_cf(now) == 2
    assert seen == [normal.id], "yalnız-kayıt kaydı araştırma politikasına GİRMEZ"
    assert "p_" + rec_only.id in eng.research.pending, "yalnız-kayıt kaydına dokunulmaz (bekleyen yoktur zaten)"
    from tradingbot.learn.experience import RECORD_ONLY_REASON, shadow_experiences
    assert RECORD_ONLY_REASON == RO
    exps = shadow_experiences([t.to_dict() for t in (rec_only, normal)], as_of_ms=None)
    assert [e.symbol for e in exps] == [normal.symbol]


# ============================================================================ 4) STRATEJİ DEFTERLERİ (+ Box)
def _book_view(book) -> str:
    led = json.loads(book.ledger_path.read_text(encoding="utf-8"))
    return json.dumps(_strip(LB._norm({"positions": led.get("positions"), "history": led.get("history")})), sort_keys=True)


def test_book_universe_extra_is_recorded_not_opened_and_the_in_universe_entry_opens_as_before(tmp_path):
    fbs = LB._d4_fbs((LB.SYM, "SOL/USDT"))
    out = {}
    for name, mode in (("open", LM.EXTRA_OPEN), ("rec", REC)):
        book = LB._book(tmp_path / name, "d4_donchian_20_10", symbols=[LB.SYM])
        LB._step(book, fbs, now_ms=LB.NOW_H4, px=104.1, symbols=[LB.SYM, "SOL/USDT"],
                 learning=LB._bl("d4_donchian_20_10", extra_entries=mode))
        out[name] = book
    o, r = out["open"], out["rec"]
    assert set(o.ledger.positions) == {LB.SYM, "SOL/USDT"}
    assert set(r.ledger.positions) == {LB.SYM}
    pi, pr = o.ledger.positions[LB.SYM], r.ledger.positions[LB.SYM]
    assert (pi.qty, pi.entry_avg, pi.stop, pi.leverage, pi.meta["learning"]) == \
        (pr.qty, pr.entry_avg, pr.stop, pr.leverage, pr.meta["learning"]), "evren içi giriş bit-aynı"
    assert r.last_actions["SOL/USDT"]["reason"] == RO and r.rejections[RO] == 1
    (t,) = [x for x in r.cf.sb.trades if x.symbol == "SOL/USDT"]
    assert t.reason_not_opened[0] == RO and "BOOK_UNIVERSE" in t.reason_not_opened
    ro = t.features[LM.RECORD_ONLY_FEATURE]
    assert ro["book"] == "d4_donchian_20_10" and "BOOK_UNIVERSE" in ro["selectivity_codes"] and ro["notional"] > 0
    assert t.features["learning"]["ok"] is True and t.features["in_lab_universe"] is False, "fit (öğrenme boyutu) kayıtta"
    summ = json.loads((Path(r.cfg.state_path) / r.summary_file).read_text(encoding="utf-8"))
    assert summ["learning"]["counters"]["learning_record_only"] == 1
    assert summ["learning"]["book"]["extra_entries"] == REC
    so = json.loads((Path(o.cfg.state_path) / o.summary_file).read_text(encoding="utf-8"))
    assert "extra_entries" not in so["learning"]["book"] and "learning_record_only" not in so["learning"]["counters"]


@pytest.mark.parametrize("name", ["t2_trend_regime", "d4_donchian_20_10"])
def test_capacity_extras_open_identically_under_record_selectivity(name, tmp_path):
    syms = ["S%02d/USDT" % i for i in range(12)]
    if name == "t2_trend_regime":
        _flag, brk = LB._trend_rows()
        fbs, now_ms, px = LB._trend_fbs(brk, syms), LB._asof(brk), float(brk[-1]["close"])
    else:
        fbs, now_ms, px = LB._d4_fbs(syms), LB.NOW_H4, 104.1
    views = {}
    for mode in (LM.EXTRA_OPEN, REC):
        book = LB._book(tmp_path / mode, name, symbols=(syms if name == "d4_donchian_20_10" else None))
        LB._step(book, fbs, now_ms=now_ms, px=px, symbols=syms, learning=LB._bl(name, extra_entries=mode))
        assert set(book.ledger.positions) == set(syms), (mode, book.rejections)
        views[mode] = _book_view(book)
        unlocked = [p.meta["learning"]["learning_unlocked_by"] for p in book.ledger.positions.values()]
        assert any(unlocked) and all(LM.classify_unlock_codes(u) in ("policy", "capacity") for u in unlocked)
    assert views[REC] == views[LM.EXTRA_OPEN]


def test_t2_structure_shadow_extra_is_recorded_with_the_rules_own_geometry(tmp_path):
    flag, _brk = LB._trend_rows()
    fbs, now_ms, px = LB._trend_fbs(flag), LB._asof(flag), float(flag[-1]["close"])
    book = LB._book(tmp_path, "t2_trend_regime", mode="ENFORCE")
    LB._step(book, fbs, now_ms=now_ms, px=px, learning=LB._bl("t2_trend_regime", extra_entries=REC),
             structures_entry_shadow=True)
    assert LB.SYM not in book.ledger.positions
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened[:2] == [RO, "STRUCTURE_WAIT_TRIGGER"]
    assert t.features["structure"]["action"] == "WAIT_TRIGGER" and t.stop > 0
    assert book.structure_decisions[LB.SYM]["applied"] == "REJECTED"


def test_book_record_only_counter_counts_records_and_rejections_count_tours(tmp_path):
    """Aynı günlük barda dört tur: TEK kayıt, sayaç 1 (kayıt sayar); `rejections` diğer ret nedenleri gibi tur başına olay."""
    flag, _brk = LB._trend_rows()
    fbs, now_ms, px = LB._trend_fbs(flag), LB._asof(flag), float(flag[-1]["close"])
    book = LB._book(tmp_path, "t2_trend_regime", mode="ENFORCE")
    for k in range(4):
        book.run_id = "RUN-%d" % k
        LB._step(book, fbs, now_ms=now_ms + k * 900_000, px=px, learning=LB._bl("t2_trend_regime", extra_entries=REC),
                 structures_entry_shadow=True)
    assert len(book.cf.sb.trades) == 1 and book.rejections[RO] == 4
    assert book.learning_counters["learning_record_only"] == 1 and book.learning_counters["counterfactual_recorded"] == 1


def test_book_pending_counterfactual_of_the_same_signal_becomes_record_only(tmp_path):
    """D4: tur 1 kill switch → iki sembolde KILL_SWITCH_ACTIVE kaydı; tur 2: evren içi sembol açılır (kaydı düşer, iki kipte
    aynı); evren dışı (BOOK_UNIVERSE) `open`da açılır, `record_selectivity`de açılmaz ve ESKİ kaydı yalnız-kayda dönüşür."""
    syms = [LB.SYM, "SOL/USDT"]
    fbs = LB._d4_fbs(tuple(syms))
    out = {}
    for mode in (LM.EXTRA_OPEN, REC):
        lrn = LB._bl("d4_donchian_20_10", extra_entries=mode)
        book = LB._book(tmp_path / mode, "d4_donchian_20_10", symbols=[LB.SYM])
        book.risk.ks.trip("TEST", "manual trip")
        LB._step(book, fbs, now_ms=LB.NOW_H4, px=104.1, symbols=syms, learning=lrn)
        assert sorted((t.symbol, t.reason_not_opened) for t in book.cf.sb.trades) == \
            [("ETH/USDT", ["KILL_SWITCH_ACTIVE"]), ("SOL/USDT", ["KILL_SWITCH_ACTIVE"])], mode
        book.risk.ks.reset("test", "reset")
        for k in (1, 2):
            LB._step(book, fbs, now_ms=LB.NOW_H4 + k * 15 * 60_000, px=104.1, symbols=syms, learning=lrn)
        out[mode] = book
    o, r = out[LM.EXTRA_OPEN], out[REC]
    assert set(o.ledger.positions) == set(syms) and o.cf.sb.trades == [] and o.cf.stats()["superseded"] == 2
    assert set(r.ledger.positions) == {LB.SYM} and r.cf.stats()["superseded"] == 1
    (t,) = r.cf.sb.trades
    assert t.symbol == "SOL/USDT" and t.reason_not_opened[0] == RO and t.outcome is None
    assert "BOOK_UNIVERSE" in t.reason_not_opened and "KILL_SWITCH_ACTIVE" in t.reason_not_opened
    assert t.features[LM.RECORD_ONLY_FEATURE]["retagged_from"] == "KILL_SWITCH_ACTIVE"
    assert r.cf.recorded_total == 2, "dönüşüm yeni kayıt değildir"
    assert r.learning_counters["learning_record_only"] == 1 and r.rejections[RO] == 2
    disk = json.loads((r.state_dir / "counterfactual_trades.json").read_text(encoding="utf-8"))
    (d,) = disk["trades"]
    assert d["reason_not_opened"] == t.reason_not_opened


def test_retag_leaves_labelled_and_already_record_only_records_alone():
    t = ShadowTrade(id="x", plan_id="k", symbol="ETH/USDT", market_type="USDM_PERP", direction="LONG", created_at="2026-10-01",
                    entry=100.0, stop=95.0, targets=[], horizon_bars=6, variant="as_planned",
                    reason_not_opened=["INSUFFICIENT_MARGIN"], label_ts="2026-10-02", features=None)
    info = {"reason": RO, "codes": ["BOOK_UNIVERSE", "TOTAL_OPEN_RISK"]}
    assert LM.retag_as_record_only(t, info) is True
    assert t.reason_not_opened == [RO, "BOOK_UNIVERSE", "TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN"]
    assert t.features[LM.RECORD_ONLY_FEATURE] == dict(info, retagged_from="INSUFFICIENT_MARGIN")
    assert LM.retag_as_record_only(t, info) is False, "zaten yalnız-kayıt"
    t2 = dataclasses.replace(t, reason_not_opened=["MIN_NOTIONAL"], outcome={"r_multiple": 1.0}, features=None)
    assert LM.retag_as_record_only(t2, info) is False and t2.reason_not_opened == ["MIN_NOTIONAL"], "etiketli kayda dokunmaz"


# Box: 1d kutu 110/90; son 5m mumu kırmızı ve önceki mumun dibini kırar → SHORT, stop = önceki mumun tepesi.
def _box_bars(stop_pct: float) -> list[tuple]:
    hi = round(108.0 * (1 + stop_pct / 100.0), 6)
    return [(100, 101, 99, 100), (100, 101, 99, 100), (108.1, hi, 108.05, 108.3), (108.3, 108.35, 107.9, 108.0)]


def test_box_learning_floor_is_half_a_percent_and_box_min_stop_extras_stay_real(tmp_path):
    floor = load_config(ROOT / "config.yaml").v3.learning_mode.books["b1_box_fade"].min_stop_pct
    assert floor == 0.5
    res = {}
    for pct, msp in ((0.4, floor), (0.6, floor), (0.4, 0.32)):
        book = LB._book(tmp_path / ("%s_%s" % (pct, msp)), "b1_box_fade", mode="SHADOW")
        LB._step(book, LB._box_fbs(_box_bars(pct)), now_ms=LB.NOW_M5, px=108.0,
                 learning=LB._bl("b1_box_fade", min_stop_pct=msp, extra_entries=REC))
        res[(pct, msp)] = book
    b = res[(0.4, floor)]
    assert b.ledger.positions == {} and b.last_actions[LB.SYM]["reason"] == "NO_SIGNAL", "%0,4 < %0,5 taban: sinyal yok"
    assert not (b.cf.sb.trades if b.cf is not None else [])
    b = res[(0.6, floor)]
    pos = b.ledger.positions[LB.SYM]
    assert pos.side.value == "SHORT" and float(pos.stop) == pytest.approx(108.648)
    tags = pos.meta["learning"]["learning_unlocked_by"]
    assert "BOX_MIN_STOP_PCT" in tags and LM.classify_unlock_codes(tags) == "capacity", tags
    assert RO not in b.rejections
    assert LB.SYM in res[(0.4, 0.32)].ledger.positions, "eski taban (0,32) %0,4 stopu açardı"


# ============================================================================ 5) FORMASYON
def _pattern_direct(tmp_path, mode: str, **bl_over):
    return LP._direct(tmp_path, bl_over=dict(bl_over, extra_entries=mode))


def test_pattern_rr_floor_extra_is_recorded_not_opened(tmp_path):
    out = {}
    for mode in (LM.EXTRA_OPEN, REC):
        _, book = _pattern_direct(tmp_path / mode, mode)
        pl = LP._plan(min_rr=5.0)
        out[mode] = (book, pl, LP._open(book, pl))
    book, pl, res = out[LM.EXTRA_OPEN]
    assert res == "OPENED" and "RR_BELOW_MIN_AT_ENTRY" in book.ledger.positions["BTC/USDT"].meta["learning"]["learning_unlocked_by"]
    book, pl, res = out[REC]
    assert res == "REJECTED" and book.ledger.positions == {}
    assert pl["status"] == PL_REJECTED and book.rejections.get(RO) == 1
    rr = ["RR_BELOW_MIN_AT_ENTRY", "RR_BELOW_MIN_AFTER_ROUNDING"]
    assert pl["learning"]["record_only"]["selectivity_codes"] == rr
    (t,) = book.cf.sb.trades
    assert t.reason_not_opened == [RO] + rr and t.signal_key == pl["plan_id"]
    ro = t.features[LM.RECORD_ONLY_FEATURE]
    assert ro["book"] == "pattern_trader" and ro["notional"] > 0 and ro["size_rule"] == "SLOT"
    assert t.features["entry_ref"] == "mark" and book.learning_counters["learning_record_only"] == 1


def test_pattern_capacity_and_policy_entries_open_exactly_as_with_open(tmp_path):
    out = {}
    for mode in (LM.EXTRA_OPEN, REC):
        for case, kw in (("policy", {}), ("rounding", {"ue": LP._ue(tick="0.5")})):
            _, book = _pattern_direct(tmp_path / mode / case, mode, **({"slots": 1} if case == "rounding" else {}))
            pl = LP._plan()
            ue = kw.get("ue")
            res = LP._open(book, pl, ue=ue) if ue is not None else LP._open(book, pl)
            pos = book.ledger.positions.get("BTC/USDT")
            out[(mode, case)] = (res, None if pos is None else (pos.qty, pos.entry_avg, pos.leverage, pos.meta["learning"]))
    for case in ("policy", "rounding"):
        assert out[(REC, case)] == out[(LM.EXTRA_OPEN, case)] and out[(REC, case)][0] == "OPENED", case
    assert out[(REC, "policy")][1][3]["learning_unlocked_by"] == []
    tags = out[(REC, "rounding")][1][3]["learning_unlocked_by"]
    assert "RISK_ABOVE_CAP_AFTER_ROUNDING" in tags and LM.classify_unlock_codes(tags) == "capacity"


def test_pattern_count_cap_extras_still_open_under_record_selectivity(tmp_path):
    LP.P.CLOCK[0] = LP.P.T0
    try:
        sc, book, _prov, _ = LP._multi(tmp_path, learning=LP.LEARN)
        book.set_learning(LP._bl(book.cfg, extra_entries=REC), universe_symbols=[], gate=lambda: (True, "OK"))
        sc.scan_cycle(now_ms=LP.P.CLOCK[0])
        assert set(book.ledger.positions) == set(LP.LAB5) and RO not in book.rejections
        caps = [p.meta["learning"]["learning_unlocked_by"] for p in book.ledger.positions.values()]
        assert sum(1 for u in caps if "MAX_POSITIONS" in u) == 2 and all(LM.classify_unlock_codes(u) != "selectivity"
                                                                       for u in caps)
    finally:
        LP.P.CLOCK[0] = LP.P.T0


# ============================================================================ 6) ortak deneyim (mühürlü toplayıcı, kod değişmeden)
# Mühürlü dosyalar (advisor*, advice_store, situation, collector, store) değişmedi; neden ailesi `rows.py`de (mühürsüz).
def test_sealed_collector_carries_record_only_counterfactuals_as_xp_cf_rows(tmp_path):
    import test_shared_experience_collector_v1 as XC
    box = XC.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")
    eng = XC.fake_engine(tmp_path, books=[box])
    xp = XC.collector(eng)
    XC.step(xp, eng, XC.T0)
    ok = box.cf.record(signal_key="signal_ts:%d" % XC.T0_MS, symbol="SOL/USDT", direction="LONG", entry=100.0, stop=95.0,
                       targets=[110.0], reason=RO, created_at=XC.T0 + timedelta(minutes=1), tf_minutes=240, horizon_bars=6,
                       label_kind="TARGET_STOP_TIME", features={"setup_type": "trend", "strategy": "x"},
                       extra_reasons=["BOOK_UNIVERSE", "TOTAL_OPEN_RISK"])
    assert ok and box.cf.sb.trades[-1].reason_not_opened == [RO, "BOOK_UNIVERSE", "TOTAL_OPEN_RISK"]
    XC.set_tour(eng, XC.T0_MS + 60_000)
    XC.step(xp, eng, XC.T0 + timedelta(minutes=2))
    (row,) = XC.by(XC.rows_of(eng), kind="xp_cf")
    assert row["reason"] == RO and row["reasons"] == [RO, "BOOK_UNIVERSE", "TOTAL_OPEN_RISK"]
    assert row["reason_family"] == "GATE" and row["status"] == "PENDING"

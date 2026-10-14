"""MOTOR duzeyinde provenans regresyonlari — `learning_keys` GERCEKTEN indekse yaziliyor mu.

Kusur: `engine_v3` uc yerde `learner2.on_trade_closed(...)` DONUSUNU atiyor ve `note_learned`a
LEGACY ogrenicinin dersini geciyordu; `learning_keys` yalnizca v2 dersinde uretildigi icin
indekse HIC yazilmiyordu. Uretimde F00019/MSF T (close_event_id 1eb8ce44ef0f5687) boyle kaydedildi.

Buradaki testler motor orkestrasyonunu CAGIRIR (birim `note_learned` cagrisiyla YETINMEZ) ve
uc cagiranin (GAP_RECONCILE / EXIT_MONITOR / LIVE_TOUR) provenansi tasidigini yapisal olarak
kanitlar. Metin sayimi KULLANILMAZ; AST uzerinde veri akisi dogrulanir.
"""
from __future__ import annotations

import ast
import json
import pathlib
import threading
from decimal import Decimal

import pytest

ENGINE_SRC = pathlib.Path(__file__).resolve().parents[1] / "tradingbot" / "engine_v3.py"

#: Uretimden alinan GERCEK kapanis (F00019/MSFT, 2026-09-08T13:46:29Z). Bu satir uretimde
#: `learning_keys` OLMADAN yazildi; tarihsel yokluk TARIHSELDIR, geriye donuk doldurulmaz.
F00019 = {
    "id": "F00019", "symbol": "MSFT/USDT", "side": "LONG", "setup_type": "pullback",
    "r_multiple": -1.0936022535345444, "net_pnl": -0.4862216468, "pnl": -0.4862216468,
    "exit_reason": "stop", "closed_at": "2026-09-08T13:46:29+00:00",
    "opened_at": "2026-08-27T23:26:08+00:00", "entry": 503.71,
    "mae_pct": -2.285044966349685, "mfe_pct": 2.8230529471322785,
    "features": {"regime": "SQUEEZE", "setup_type": "pullback", "direction": "LONG"},
}
F00019_EVENT = "1eb8ce44ef0f5687"


# --------------------------------------------------------------------- AST sozlesmesi
def _note_learned_calls() -> list[tuple[str, ast.Call]]:
    tree = ast.parse(ENGINE_SRC.read_text(encoding="utf-8"))
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    out = []
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "note_learned":
            enc = sorted((f for f in funcs if f.lineno <= call.lineno <= (f.end_lineno or f.lineno)),
                         key=lambda f: (f.end_lineno or 0) - f.lineno)
            out.append((enc[0].name if enc else "?", call))
    return out


def _v2_result_names(fn_name: str) -> set[str]:
    """`learner2.on_trade_closed(...)` DONUSUNUN atandigi isimler (atilmiyorsa dolu)."""
    tree = ast.parse(ENGINE_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        v = node.value
        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                and v.func.attr == "on_trade_closed"
                and isinstance(v.func.value, ast.Attribute) and v.func.value.attr == "learner2"):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def test_every_engine_caller_routes_provenance_from_the_v2_lesson():
    """UC cagiranin HEPSI v2 dersinin donusunu yakalar ve `learning_keys` olarak gecer."""
    calls = _note_learned_calls()
    # 2026-09-24: kesinti artik gecmis mumlarla KAPANIS URETMEZ (`ensure_gap_reconciled` ogrenecek kapanis bilmez);
    # 60 sn izleyicinin kapanislari (esanli `exit_check` ve izleyici is parcacigi kuyrugu) TEK yoldan ogrenilir.
    assert {fn for fn, _ in calls} == {"tour", "_learn_protective_closes"}, calls
    for fn_name, call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "learning_keys" in kw, f"{fn_name}: learning_keys GECILMIYOR"
        v2_names = _v2_result_names(fn_name)
        assert v2_names, f"{fn_name}: on_trade_closed donusu ATILIYOR"
        # deger v2 dersinden turemeli: `(v2 or {}).get("learning_keys")` gibi
        used = {n.id for n in ast.walk(kw["learning_keys"]) if isinstance(n, ast.Name)}
        assert used & v2_names, f"{fn_name}: learning_keys v2 dersinden TUREMIYOR ({used})"


def test_legacy_lesson_is_still_the_lesson_argument():
    """Legacy alanlar (lesson_id vb.) BOZULMADI: `lesson` konumsal argumani korunur."""
    for fn_name, call in _note_learned_calls():
        assert len(call.args) == 3, f"{fn_name}: note_learned(index, close, lesson) imzasi degisti"


# --------------------------------------------------------------------- motor harness
class _Rec:
    def __init__(self, d: dict):
        self._d = dict(d)
        self.symbol = d["symbol"]
        self.side = d["side"]
        self.exit_reason = d["exit_reason"]
        self.net_pnl = Decimal(str(d["net_pnl"]))

    def to_legacy_dict(self) -> dict:
        return dict(self._d)


class _Pos:
    id = "F00019"

    def __init__(self):
        self.meta: dict = {}


class _Ledger:
    def __init__(self, records):
        self.positions = {"MSFT/USDT": _Pos()}
        self.history: list = []
        self._records = records
        self.ticks = 0

    def tick(self, marks, **kw):
        self.ticks += 1
        out, self._records = self._records, []      # ayni kapanis IKINCI kez DONMEZ
        return out

    def save(self, path):
        pathlib.Path(path).write_text("{}", encoding="utf-8")


def _engine(tmp_path, records):
    """GERCEK `EngineV3.exit_check` icin izole bagimliliklarla kismi nesne."""
    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.learn.learner_v2 import LearnerV2
    from tradingbot.learn.memory import TradeMemory
    from tradingbot.learn.reconcile import LearnedIndex
    from tradingbot.learn.registry import ModelRegistry

    e = object.__new__(TradingEngineV3)
    e._exit_lock = threading.RLock()
    # `__init__` atlandigi icin motorun kosulsuz alanlari ELLE kurulur (gercek nesnede daima vardir:
    # engine_v3.__init__ `self.pattern_book = None` yazar). 60746c3 formasyon defterini cikis yoluna
    # eklediginde bu kismi nesne eksik kalmisti — testler AttributeError ile dusuyordu (kod DEGIL, kurgu).
    e.pattern_book = None
    e.pattern_scanner = None
    e.ledger2 = _Ledger(records)
    e.ledger_path = tmp_path / "ledger.json"
    e.run_id = "run_test"
    e.last_decisions = {"MSFT/USDT": {"regime": "SQUEEZE", "consensus_score": 0.4,
                                      "dissent": [], "vetoes": []}}
    e.learner2 = LearnerV2(TradeMemory(tmp_path / "mem.jsonl"),
                           ModelRegistry(tmp_path / "models.json"),
                           state_path=tmp_path / "learn_v2.json")
    e.learned_index = LearnedIndex(tmp_path / "learned_closes.jsonl")
    e.journaled: list = []

    class _Cfg:
        state_path = tmp_path

    e.cfg = _Cfg()

    class _Live:
        @staticmethod
        def snapshot(sym):
            # 2026-09-24: ana defter izleyicisi dogrulanmis USDS-M perp mark'i kullanir (spot ticker DEGIL)
            import time
            return {"ticker": {"last": 490.0}, "funding": {"mark": 490.0}, "ts": time.time()}

    class _Runner:
        live = _Live()

    e.runner = _Runner()
    e.ensure_gap_reconciled = lambda: None
    e._record_position_path = lambda *a, **k: None
    e._portfolio_state = lambda *a, **k: {}
    e._persist_risk_state = lambda *a, **k: None
    e._journal_outcome = lambda legacy, lesson: e.journaled.append((legacy, lesson))
    # LEGACY ogrenici: `learning_keys` ICERMEZ (uretimdeki davranis)
    e.learner = type("L", (), {"learn": staticmethod(
        lambda legacy: {"id": legacy.get("id"), "symbol": legacy.get("symbol"),
                        "r": legacy.get("r_multiple"), "won": False})})()
    return e


def _index_rows(tmp_path) -> list[dict]:
    p = tmp_path / "learned_closes.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_exit_monitor_writes_learning_keys_to_the_existing_index(tmp_path):
    """EXIT_MONITOR yolu (uretimde F00019'u yazan yol) provenansi indekse GECIRIR."""
    e = _engine(tmp_path, [_Rec(F00019)])
    out = e.exit_check()
    assert [x["id"] for x in out] == ["F00019"]

    rows = _index_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["close_event_id"] == F00019_EVENT          # idempotency anahtari DEGISMEDI
    assert row["source"] == "EXIT_MONITOR"
    assert row["lesson_id"] == "F00019"                   # LEGACY alan korundu
    assert row["r_multiple"] == pytest.approx(F00019["r_multiple"])
    keys = row["learning_keys"]                           # <-- kusurun duzeltildigi yer
    assert keys["semantics"] == "v2-multileaf"
    assert keys["regime_at_close"] == "SQUEEZE"           # KAPANIS ani rejimi
    assert keys["win_leaves"] == ["MSFT/USDT|pullback", "MSFT/USDT"]
    assert keys["exp_r_leaf"] == "pullback|LONG"
    assert keys["weight"] == 1.0
    # IKINCI defter YOK: yalniz ogrenici state'i + mevcut indeks + hafiza
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["learned_closes.jsonl", "mem.jsonl"]


def test_one_close_increments_each_node_exactly_once(tmp_path):
    """Global/rejim ATALARI BIR KEZ, iki yaprak granulerligi birer kez."""
    e = _engine(tmp_path, [_Rec(F00019)])
    e.exit_check()
    st = e.learner2.win.stats
    assert e.learner2.n_closed == 1
    for key in ("", "regime:SQUEEZE", "leaf:MSFT/USDT|pullback", "leaf:MSFT/USDT",
                "regime:SQUEEZE|leaf:MSFT/USDT|pullback", "regime:SQUEEZE|leaf:MSFT/USDT"):
        assert st[key].n == 1, (key, st[key].n)
    assert st[""].s == 0.0                                # r=-1.09 -> KAZANC DEGIL
    assert e.learner2.exp_r.stats["leaf:pullback|LONG"].n == 1


def test_repeated_delivery_does_not_double_count(tmp_path):
    """Ayni kapanis ikinci kez teslim edilirse indeks BUYUMEZ ve sayaclar artmaz."""
    e = _engine(tmp_path, [_Rec(F00019)])
    e.exit_check()
    rows_1, n_1 = _index_rows(tmp_path), e.learner2.win.stats[""].n
    e.ledger2._records = [_Rec(F00019)]                   # defter ayni kapanisi TEKRAR dondurur
    e.exit_check()
    rows_2 = _index_rows(tmp_path)
    assert len(rows_2) == len(rows_1) == 1                # LearnedIndex dedup calisiyor
    assert rows_2[0]["close_event_id"] == F00019_EVENT
    # ogrenici IKINCI kez cagrildi (motor sozlesmesi degismedi) ama INDEKS tek satir kalir
    assert e.learner2.win.stats[""].n == n_1 + 1
    assert rows_2[0]["learning_keys"]["semantics"] == "v2-multileaf"


def test_distinct_closes_stay_distinct(tmp_path):
    """Farkli kapanislar farkli yapraklara yazar ve iki AYRI indeks satiri uretir."""
    other = dict(F00019, id="F00099", symbol="ONDO/USDT", setup_type="breakout",
                 r_multiple=1.9, net_pnl=1.9, closed_at="2026-09-08T14:00:00+00:00",
                 features={"regime": "TREND_UP", "setup_type": "breakout", "direction": "LONG"})
    e = _engine(tmp_path, [_Rec(F00019), _Rec(other)])
    e.last_decisions["ONDO/USDT"] = {"regime": "TREND_UP"}
    e.ledger2.positions["ONDO/USDT"] = _Pos()
    e.exit_check()
    rows = _index_rows(tmp_path)
    assert len(rows) == 2
    keys = {r["trade_id"]: r["learning_keys"] for r in rows}
    assert keys["F00019"]["win_leaves"] == ["MSFT/USDT|pullback", "MSFT/USDT"]
    assert keys["F00099"]["win_leaves"] == ["ONDO/USDT|breakout", "ONDO/USDT"]
    assert keys["F00099"]["regime_at_close"] == "TREND_UP"
    st = e.learner2.win.stats
    assert st[""].n == 2 and st[""].s == 1.0              # yalniz ONDO (r=1.9 > 0.25) KAZANC
    assert st["regime:SQUEEZE"].n == 1 and st["regime:TREND_UP"].n == 1


def test_failed_v2_update_is_not_marked_learned(tmp_path):
    """v2 guncellemesi PATLARSA indekse 'ogrenildi' YAZILMAZ."""
    e = _engine(tmp_path, [_Rec(F00019)])

    def _boom(*a, **k):
        raise RuntimeError("v2 guncellemesi basarisiz")

    e.learner2.on_trade_closed = _boom
    e.exit_check()                                        # motor kapanisi yine dondurur
    assert not (tmp_path / "learned_closes.jsonl").exists() or _index_rows(tmp_path) == []


def test_note_learned_explicit_keys_override_and_fallback(tmp_path):
    """`learning_keys` acik gecilir; verilmezse ESKI davranis (ders icinden) korunur."""
    from tradingbot.learn.reconcile import LearnedIndex, note_learned
    idx = LearnedIndex(tmp_path / "a.jsonl")
    assert note_learned(idx, F00019, {"id": "F00019"}, source="T",
                        learning_keys={"semantics": "v2-multileaf"}) is True
    idx2 = LearnedIndex(tmp_path / "b.jsonl")
    assert note_learned(idx2, F00019, {"id": "F00019", "learning_keys": {"semantics": "x"}},
                        source="T") is True
    a = json.loads((tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()[0])
    b = json.loads((tmp_path / "b.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert a["learning_keys"]["semantics"] == "v2-multileaf"
    assert b["learning_keys"]["semantics"] == "x"          # geriye uyum
    idx3 = LearnedIndex(tmp_path / "c.jsonl")
    assert note_learned(idx3, F00019, {"id": "F00019"}, source="T") is True
    c = json.loads((tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "learning_keys" not in c                        # alan hala OPSIYONEL

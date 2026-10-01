"""Ops düzeltmeleri: (A) aynı tur içinde fill sonrası risk durumu yenilenir (sonraki aday önceki fill'i görür; max pozisyon aynı turda
aşılamaz; persist hatası → yeni giriş yok, çıkışlar sürer; retry duplicate üretmez; spot+futures birleşik exposure)."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_coinhead as T  # noqa: E402
import test_engine_v3 as E  # noqa: E402

from tradingbot.coinhead.schema import Verdict  # noqa: E402
from tradingbot.risk.engine import RiskDecision  # noqa: E402

SYMS = ["ETH/USDT", "SOL/USDT", "ADA/USDT", "AVAX/USDT"]


def _engine4(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch)
    fl = eng._fake_live
    for i, s in enumerate(SYMS[2:]):                        # ekstra iki sembol için sentetik çerçeve
        fl._frames[s] = T.frames(seed=21 + i, drift=0.002)
        for tf, tf_ms in (("1d", 86_400_000), ("4h", 14_400_000), ("1h", 3_600_000)):
            import pandas as pd
            df = fl._frames[s][tf]
            now_ms = int(fl._now_s * 1000)
            shift = (now_ms - now_ms % tf_ms) - 2 * tf_ms - int(df["timestamp"].iloc[-1])
            df["timestamp"] = df["timestamp"] + shift
            df.index = df.index + pd.Timedelta(milliseconds=shift)
    eng.ledger2.max_positions = 10                          # defter limiti maskelemesin: risk seviyesi limiti test ediliyor
    # profil limiti sahte risk fonksiyonunda (3) uygulanır
    return eng


def _candidates(eng, syms):
    briefs = [eng.runner.run_symbol(s) for s in syms]
    decisions, marks = {}, {}
    from tradingbot.accounting import TickData
    for b in briefs:
        px = float(b.price)
        plan = SimpleNamespace(valid=True, entry=px, stop=px * 1.05, targets=[px * 0.93, px * 0.90], notional=10.0, margin=10.0,
                               size=SimpleNamespace(leverage=1), expected_r=1.5, entry_type="pullback", entry_trigger="t", time_horizon_bars=20)
        d = SimpleNamespace(is_actionable=True, active_plan=plan, verdict=Verdict.FUTURES_SHORT, direction="SHORT", specialist_reports=[],
                            coin_head_id="ch", regime="TREND_DOWN", consensus_score=-0.5, consensus_confidence=0.6, dissent=[], vetoes=[],
                            expected_r=1.5, expected_cost=0.1, model_versions={}, p_win=0.5, to_dict=lambda include_reports=False: {})
        decisions[b.symbol] = d
        marks[b.symbol] = TickData(last=Decimal(str(px)), mark=Decimal(str(px)))
    chief = SimpleNamespace(priority=list(syms), permission={s: {"allow": True, "reason": None} for s in syms}, to_dict=lambda: {})
    return decisions, chief, briefs, marks


def _fake_risk(seen: list[int]):
    def evaluate(plan, state, ctx=None):
        seen.append(len(state.open_positions))
        ok = len(state.open_positions) < 3
        return RiskDecision(allowed=ok, reasons=[] if ok else ["MAX_POSITIONS"], adjusted_notional=None, adjusted_leverage=1)
    return evaluate


def test_intratour_refresh_blocks_fourth_position_and_second_sees_first(tmp_path: Path, monkeypatch):
    eng = _engine4(tmp_path, monkeypatch)
    decisions, chief, briefs, marks = _candidates(eng, SYMS)
    seen: list[int] = []
    monkeypatch.setattr(eng.risk, "evaluate", _fake_risk(seen))
    monkeypatch.setattr(eng, "_trigger_fired", lambda *a, **k: True)
    from tradingbot.core import utc_now
    state0 = eng._portfolio_state({k: float(v.last) for k, v in marks.items()})
    opened, risk_log = eng._execute(decisions, chief, briefs, state0, marks, utc_now())
    assert len(opened) == 3 and len(eng.ledger2.positions) == 3
    assert seen == [0, 1, 2, 3]                                          # her aday önceki fill'leri gördü
    rej = [r for r in risk_log if not r["risk_allowed"]]
    # Adaylar artık maliyet-sonrası muhafazakâr edge'e göre SIRALI işlenir; hangi sembolün dördüncü
    # sırada kaldığı sıralamaya bağlıdır. Sabit kota yoktur — reddin sebebi gerçek risk kapısıdır.
    assert len(rej) == 1 and rej[0]["risk_reasons"] == ["MAX_POSITIONS"] and rej[0]["symbol"] in SYMS
    assert rej[0]["symbol"] not in [o.split(" ")[0] for o in opened]
    fills = [r for r in risk_log if r.get("state_after_fill")]
    assert [f["state_after_fill"]["open_positions"] for f in fills] == [1, 2, 3] and all(f["state_after_fill"]["persisted"] for f in fills)
    assert fills[1]["state_after_fill"]["used_margin"] > fills[0]["state_after_fill"]["used_margin"] > 0
    rj = json.loads((eng.cfg.state_path / "risk.json").read_text(encoding="utf-8"))
    assert rj["exposure"]["open_positions"] == 3 and rj["exposure"]["used_margin"] > 0     # diskteki risk durumu anında güncel
    # retry: aynı adaylar tekrar → duplicate pozisyon/fill yok
    seen.clear()
    opened2, _ = eng._execute(decisions, chief, briefs, state0, marks, utc_now())
    assert opened2 == [] and len(eng.ledger2.positions) == 3 and all(len(p.fills) == 1 for p in eng.ledger2.positions.values())


def test_persist_failure_blocks_new_entries_but_not_exits(tmp_path: Path, monkeypatch):
    eng = _engine4(tmp_path, monkeypatch)
    decisions, chief, briefs, marks = _candidates(eng, SYMS[:3])
    monkeypatch.setattr(eng.risk, "evaluate", _fake_risk([]))
    monkeypatch.setattr(eng, "_trigger_fired", lambda *a, **k: True)
    import tradingbot.engine_v3 as M
    real = M.atomic_write_json

    def failing(path, *a, **k):
        if str(path).endswith("risk.json"):
            raise OSError("disk full (test)")
        return real(path, *a, **k)
    monkeypatch.setattr(M, "atomic_write_json", failing)
    from tradingbot.core import utc_now
    state0 = eng._portfolio_state({k: float(v.last) for k, v in marks.items()})
    opened, risk_log = eng._execute(decisions, chief, briefs, state0, marks, utc_now())
    assert len(opened) == 1 and len(eng.ledger2.positions) == 1                       # ilk fill sonrası persist hatası → diğerleri kapalı
    assert [r["risk_reasons"] for r in risk_log if not r["risk_allowed"]] == [["RISK_STATE_PERSIST_FAILED"]] * 2
    # çıkış etkilenmez: fiyat TP2 altına → tick kapatır
    sym, pos = next(iter(eng.ledger2.positions.items()))
    recs = eng.ledger2.tick({sym: Decimal(str(float(pos.targets[-1]) * 0.98))}, bar_advance=True)
    assert len(recs) == 1 and sym not in eng.ledger2.positions


def test_combined_spot_and_futures_exposure_counts_toward_limit(tmp_path: Path, monkeypatch):
    eng = _engine4(tmp_path, monkeypatch)
    decisions, chief, briefs, marks = _candidates(eng, SYMS[:2])
    # önce spot pozisyon (yetkili spot defterinden), sonra futures adayları
    b0 = briefs[0]
    order = eng.spot2.market_buy(b0.symbol, quote_amount=Decimal("10"), ref_price=Decimal(str(b0.price)))
    assert str(order.status.value if hasattr(order.status, "value") else order.status).upper() == "FILLED"
    st = eng._portfolio_state({k: float(v.last) for k, v in marks.items()})
    assert len(st.open_positions) == 1 and st.open_positions[0].market_type == "SPOT"
    seen: list[int] = []
    monkeypatch.setattr(eng.risk, "evaluate", _fake_risk(seen))
    monkeypatch.setattr(eng, "_trigger_fired", lambda *a, **k: True)
    from tradingbot.core import utc_now
    opened, _ = eng._execute(decisions, chief, briefs, st, marks, utc_now())
    assert seen[0] == 1                                    # ilk futures adayı spot pozisyonu birleşik exposure'da gördü
    st2 = eng._portfolio_state({k: float(v.last) for k, v in marks.items()})
    kinds = sorted(p.market_type for p in st2.open_positions)
    assert kinds.count("SPOT") == 1 and kinds.count("USDM_PERP") == len(opened) >= 1


# ---------------------------------------------------------------- (B) kooperatif durdurma + lock sahipliği
import hashlib  # noqa: E402
import os  # noqa: E402

from tradingbot.ops.lock import SingletonLock  # noqa: E402
from tradingbot.ops.shutdown import (STOP_REQUEST, InstanceRecord, StopWatcher, instance_status, request_stop,  # noqa: E402
                                     wait_stopped)


def test_cooperative_stop_token_and_idempotency(tmp_path: Path):
    inst = InstanceRecord(tmp_path, "worker"); inst.register()
    mine = StopWatcher(tmp_path, inst.token, min_interval_s=0)
    other = StopWatcher(tmp_path, "not-my-token", min_interval_s=0)
    assert not mine.requested()
    r1 = request_stop(tmp_path, ("worker",))
    assert r1["requested"] == [inst.token] and not r1["already_pending"] and (tmp_path / STOP_REQUEST).exists()
    r2 = request_stop(tmp_path, ("worker",))                       # ikinci istek idempotent
    assert r2["already_pending"] and (tmp_path / STOP_REQUEST).exists()
    assert mine.requested() and not other.requested()              # yalnız doğru token'lı instance durur
    mine.consume()
    assert not (tmp_path / STOP_REQUEST).exists()
    assert inst.unregister() and not inst.path.exists()
    # instance yokken: istek yazılmaz, 'absent'
    r3 = request_stop(tmp_path, ("worker",))
    assert r3["requested"] == [] and not (tmp_path / STOP_REQUEST).exists()
    assert wait_stopped(tmp_path, ("worker",), timeout_s=0.2) == {"worker": "absent"}


def test_stale_instance_and_foreign_lock_not_touched(tmp_path: Path):
    from tradingbot.core import atomic_write_json
    atomic_write_json(tmp_path / ".worker_instance.json", {"kind": "worker", "pid": 999_999_9, "token": "t", "started_at": "x"}, indent=None)
    st = instance_status(tmp_path, "worker")
    assert st["present"] and st["stale"] and not st["alive"]
    r = request_stop(tmp_path, ("worker",))
    assert r["requested"] == [] and not (tmp_path / STOP_REQUEST).exists()          # ölü instance'a istek yazılmaz
    assert (tmp_path / ".worker_instance.json").exists()                              # bayat kayıt silinmez (yalnız raporlanır)
    # lock: başka PID'nin dosyası silinmez; kendi kilidi remove_file ile kalkar
    lp = tmp_path / ".lock"
    lp.write_text("424242\n", encoding="ascii")
    lk = SingletonLock(lp)
    assert not lk.is_locked_by_other() and not lp.exists() # bayat pid → OS kilidi serbest → bayat dosya güvenle kaldırıldı
    lk.acquire()                                            # kendi kilidimiz: dosyaya kendi pid'imiz yazılır
    assert lk.read_pid() == os.getpid()
    lk.release(remove_file=True)
    assert not lp.exists()
    lp.write_text("424242\n", encoding="ascii")
    lk2 = SingletonLock(lp); lk2._fh = open(lp, "a+b")      # tanıtıcı var ama dosyadaki pid bizim değil → silinmez
    lk2.release(remove_file=True)
    assert lp.exists() and lp.read_text().strip() == "424242"


def test_stop_request_blocks_new_entries_keeps_positions_and_exits(tmp_path: Path, monkeypatch):
    eng = _engine4(tmp_path, monkeypatch)
    decisions, chief, briefs, marks = _candidates(eng, SYMS[:2])
    monkeypatch.setattr(eng.risk, "evaluate", _fake_risk([]))
    monkeypatch.setattr(eng, "_trigger_fired", lambda *a, **k: True)
    from tradingbot.core import utc_now
    # önce bir pozisyon aç, defteri kaydet, hash al
    d1 = {SYMS[0]: decisions[SYMS[0]]}
    opened, _ = eng._execute(d1, chief, briefs, None, marks, utc_now())
    assert len(opened) == 1
    eng.ledger2.save(eng.ledger_path)
    h0 = hashlib.sha256(eng.ledger_path.read_bytes()).hexdigest()
    # durdurma isteği → yeni giriş yok, açık pozisyon aynen durur, defter dosyası değişmez
    eng.set_stop_check(lambda: True)
    opened2, log2 = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert opened2 == [] and [r["risk_reasons"] for r in log2 if not r["risk_allowed"]] == [["SHUTDOWN_REQUESTED"]] * 2
    assert list(eng.ledger2.positions) == [SYMS[0]] and hashlib.sha256(eng.ledger_path.read_bytes()).hexdigest() == h0
    # çıkış yolu durdurma sırasında da çalışır ve tam yazılır
    pos = eng.ledger2.positions[SYMS[0]]
    recs = eng.ledger2.tick({SYMS[0]: Decimal(str(float(pos.targets[-1]) * 0.98))}, bar_advance=True)
    assert len(recs) == 1
    eng.ledger2.save(eng.ledger_path)
    d = json.loads(eng.ledger_path.read_text(encoding="utf-8"))
    assert d["positions"] == {} and len(d["history"]) == 1 and d["history"][0]["exit_reason"]


def test_dashboard_stop_poller_sets_should_exit(tmp_path: Path):
    from tradingbot.dashboard.app import poll_stop_request
    inst = InstanceRecord(tmp_path, "dashboard"); inst.register()
    srv = SimpleNamespace(should_exit=False)
    request_stop(tmp_path, ("dashboard",))
    poll_stop_request(srv, StopWatcher(tmp_path, inst.token, min_interval_s=0), interval_s=0.01)
    assert srv.should_exit is True


# ---------------------------------------------------------------- (C) runtime: hızlı exit monitörü + kanıt persist/Obsidian/dashboard
def test_exit_check_closes_positions_between_tours_and_writes_state(tmp_path: Path, monkeypatch):
    eng = _engine4(tmp_path, monkeypatch)
    decisions, chief, briefs, marks = _candidates(eng, SYMS[:1])
    monkeypatch.setattr(eng.risk, "evaluate", _fake_risk([]))
    monkeypatch.setattr(eng, "_trigger_fired", lambda *a, **k: True)
    from tradingbot.core import utc_now
    opened, _ = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert len(opened) == 1
    sym, pos = next(iter(eng.ledger2.positions.items()))
    assert eng.exit_check() == []                                              # fiyat giriş civarı → açık kalır, yeni giriş yok
    eng._fake_live.price[sym] = float(pos.targets[-1]) * 0.98                  # canlı fiyat TP2 ötesine
    closed = eng.exit_check()
    assert len(closed) == 1 and sym not in eng.ledger2.positions and closed[0]["exit_reason"]
    d = json.loads(eng.ledger_path.read_text(encoding="utf-8"))
    assert d["positions"] == {} and len(d["history"]) == 1                     # defter tur beklemeden kaydedildi
    assert eng.learner2.n_closed >= 1
    rj = json.loads((eng.cfg.state_path / "risk.json").read_text(encoding="utf-8"))
    assert rj["exposure"]["open_positions"] == 0


def test_pattern_evidence_persisted_and_exposed(tmp_path: Path, monkeypatch):
    from test_patterns import _candles
    from tradingbot.history import HistoryStore
    eng = _engine4(tmp_path, monkeypatch)
    st = HistoryStore(eng.cfg.cache_path / eng.cfg.v3.history.root_dir)
    import time as _t
    now_ms = int(_t.time() * 1000)
    start = now_ms - now_ms % 14_400_000 - 700 * 14_400_000
    st.write("futures", "ETH/USDT", "4h", _candles(700, seed=4, drift=0.0006, tf_ms=14_400_000, start=start))
    eng._pattern_loaded = False
    ev = eng._pattern_evidence("ETH/USDT", now_ms)
    assert ev is not None and set(ev) == {"LONG", "SHORT"} and "stats" in ev["LONG"]
    p = eng.cfg.state_path / "evidence" / "ETH_USDT.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["packets"]["LONG"]["independent_sample_count"] == ev["LONG"]["n"] and d["explanation_tr"]["LONG"]
    # eski veri (3 bardan bayat) → kanıt yok; olmayan sembol → None; history yoksa motor None (fail-safe)
    assert eng._pattern_evidence("ETH/USDT", now_ms + 5 * 14_400_000) is None and eng._pattern_evidence("ZZZ/USDT", now_ms) is None
    # Obsidian bölümü
    from tradingbot.obsidian_coinheads import ObsidianCoinHeadWriter
    w = ObsidianCoinHeadWriter(tmp_path / "vault"); w.evidence_dir = eng.cfg.state_path / "evidence"
    sec = w._evidence_section("ETH/USDT")
    assert sec and any("Benzer Geçmiş Olaylar" in x for x in sec) and any("LONG" in x for x in sec)
    # dashboard state reader
    from tradingbot.dashboard.state import StateReader
    assert StateReader(eng.cfg.state_path).evidence("ETH")["symbol"] == "ETH/USDT"

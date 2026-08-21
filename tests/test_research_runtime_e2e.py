"""GERÇEK ÇALIŞMA YOLU uçtan uca testi — manuel `ResearchPolicyBook` geçişi YOK.

Bu dosya, araştırma döngüsünün motor turlarıyla kendiliğinden yürüdüğünü kanıtlar. Test kodunda
`book.propose` / `record_offline` / `start_shadow` / `observe` / `maybe_activate` / `_set_state`
çağrısı bulunmaz — bir statik test bunu ayrıca zorlar. Tek girdi: canlı `trade_memory.jsonl` ve
`TradingEngineV3.tour()`.

Kanıtlanan zincir:
  boş `research_policy.json` → aday üretimi → offline walk-forward → SHADOW → gerçek girişleri
  DEĞİŞTİRMEDEN eşleşmiş gözlem → istatistik kapıları → PAPER_RESEARCH_ACTIVE → yalnız YENİ girişin
  elenmesi → kötüleşme → RETIRED + baseline → cooldown sonrası yeni aday.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_end_to_end_learning import HL_NORMAL, _decision, _replay_snapshot  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.core import iso  # noqa: E402
from tradingbot.learn import TradeMemory  # noqa: E402
from tradingbot.learn.research_policy import ACTIVE, RETIRED, SHADOW  # noqa: E402

H4 = 14_400_000
BASE = datetime(2026, 3, 1, tzinfo=timezone.utc)
SYMS = ("ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT")

# Araştırma kapıları testte küçültülür (değerler config'ten gelir; kapıların KENDİSİ atlanmaz).
RESEARCH_CFG = {"learning_v3": {
    "min_samples_train": 5, "research_enabled": True,
    "research_min_new_closed": 0, "research_run_cooldown_hours": 0.0, "research_min_rows": 40,
    "research_min_shadow_obs": 2, "research_min_active_obs": 2, "research_min_review_obs": 999,
    "research_cooldown_hours": 0.0, "research_retire_delta_r": -0.10,
    "research_min_fold_consistency": 0.6}}


# =========================================================================== canlı hafıza tohumlaması
def seed_live_memory(cfg, *, n: int = 120, bad_side: str = "LONG", sparse: bool = False) -> None:
    """Canlı LIVE_PAPER hafızasına kapanmış işlem geçmişi yazar (gerçek `TradeMemory` API'si).

    Bağlam BÜTÜN işlemlerde aynıdır; sonucu yalnız TARAF belirler → veriden çıkan tek ayırt edici
    bulgu `side`/`side_x_regime` olur. Böylece üretilen aday motorun gerçek girişlerini ayırt eder.
    """
    mem = TradeMemory(cfg.state_path / "trade_memory.jsonl", source="LIVE_PAPER")
    for i in range(n):
        sym, side = SYMS[i % len(SYMS)], ("LONG" if i % 2 else "SHORT")
        row = {"trade_id": f"S{i:03d}", "symbol": sym, "direction": side, "market_type": "USDM_PERP",
               "setup_type": "pullback", "regime": "TREND_UP",
               "recorded_at": (BASE + timedelta(days=i)).isoformat()}
        if sparse:
            row["features"] = {"expected_r": 1.9, "p_win": 0.5}       # eski sparse hafıza (snapshot YOK)
        else:
            snap = _replay_snapshot(*_decision(sym, side, good=True), symbol=sym, hl=HL_NORMAL, seed=i)
            row |= {"features": snap.vector(), "snapshot": snap.to_dict()}
        mem.record_entry(row)
        o = BASE + timedelta(days=i)
        r = (0.6 if i % 9 == 0 else -1.2) if side == bad_side else (-0.5 if i % 7 == 0 else 0.9)
        mem.record_exit(f"S{i:03d}", {"symbol": sym, "side": side, "r_multiple": r,
                                      "mae_pct": -0.4 if r > 0 else -2.6, "mfe_pct": 3.1 if r > 0 else 0.3,
                                      "exit_reason": "hedef1" if r > 0 else "stop", "bars_held": 6,
                                      "opened_at": o.isoformat(),
                                      "closed_at": (o + timedelta(days=1)).isoformat()})


class _Clock:
    """Motor saati — barlarla BİRLİKTE ilerler ki veri ne bayat ne de geleceğe dönük olsun."""

    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance_ms(self, ms: float) -> None:
        self.t = self.t + timedelta(milliseconds=ms)


def build(tmp_path, monkeypatch, *, overrides: dict | None = None, **seed_kw):
    from tradingbot.core import utc_now
    ov = {k: dict(v) for k, v in RESEARCH_CFG.items()}
    for sec, vals in (overrides or {}).items():
        ov[sec] = {**ov.get(sec, {}), **vals}
    eng = _engine(tmp_path, monkeypatch, ov, before_build=lambda cfg: seed_live_memory(cfg, **seed_kw))
    eng._clock = _Clock(utc_now())
    monkeypatch.setattr("tradingbot.engine_v3.utc_now", eng._clock)
    return eng


def advance_bars(eng, n: int = 1) -> None:
    """Sentetik piyasayı `n` bar ileri alır → YENİ kapanmış bar → YENİ benzersiz sinyal.

    Aynı bar üzerinde tekrar giriş `DUPLICATE_SIGNAL` ile engellenir (doğru davranış); gerçek botta da
    aynı 4h barı içinde iki tur aynı sinyali üretir. Yeni fırsat için barın ilerlemesi gerekir.
    """
    moved = False
    for _sym, fr in eng.runner.last_frames.items():
        for tf, ms in (("1d", 86_400_000), ("4h", H4), ("1h", 3_600_000)):
            df = fr.get(tf)
            if df is None:
                continue
            df["timestamp"] = df["timestamp"] + n * ms
            df.index = df.index + pd.Timedelta(milliseconds=n * ms)
            moved = True
    if not moved:                          # ilk tur: henüz frame yok → saati de ilerletme (senkron korunur)
        return
    eng._fake_live._now_s += n * H4 / 1000
    eng._clock.advance_ms(n * H4)          # motorun saati barlarla BİRLİKTE ilerler


def tour(eng, *, close_symbols: tuple[str, ...] = (), advance: int = 1):
    """Bir motor turu. `close_symbols` verilen sembollerin fiyatını stop'un ötesine taşır (ZARAR)."""
    if advance:
        advance_bars(eng, advance)
    for sym in close_symbols:
        pos = eng.ledger2.positions.get(sym)
        if pos is not None:
            eng._fake_live.price[sym] = float(pos.stop) * (0.98 if pos.side.value == "LONG" else 1.02)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    eng._fake_live.price.clear()
    return s


def current(eng):
    return eng.research.shadow() or eng.research.active()


def label_pending_shadows(eng) -> int:
    """Karşı-olgusal gölge işlemlerin SAATİNİ ileri alır (research state'ine DOKUNMAZ).

    Gerçek etiketleme bir sonraki turda `_label_shadows` tarafından gerçek mumlarla yapılır.
    """
    h4 = (eng.runner.last_frames.get("ETH/USDT") or {}).get("4h")
    last_ts = int(h4["timestamp"].iloc[-1])
    n = 0
    for t in eng.shadow.trades:
        if t.outcome is None:
            t.created_at = iso(pd.Timestamp(last_ts - 30 * H4, unit="ms", tz="UTC").to_pydatetime())
            t.label_ts = iso(pd.Timestamp(last_ts, unit="ms", tz="UTC").to_pydatetime())
            n += 1
    eng.shadow.save()
    return n


def _rally_frames(eng, on: bool, saved: dict) -> None:
    """Karşı-olgusal piyasayı çevirir: son 40 bar güçlü yükselişe alınır (LONG karşı-olgusalları kazanır).

    Bu, "piyasa değişti, politika artık kazananları eliyor" senaryosudur — gölge defteri fixture'ıdır,
    araştırma durum makinesine DOKUNULMAZ.
    """
    for sym, fr in list(eng.runner.last_frames.items()):
        h4 = fr.get("4h")
        if h4 is None or len(h4) < 60:
            continue
        if on:
            saved[sym] = {c: h4[c].to_numpy().copy() for c in ("open", "high", "low", "close")}
            px = h4["close"].to_numpy().astype(float).copy()
            base = float(px[-41])
            for k in range(len(px) - 40, len(px)):
                base *= 1.03
                px[k] = base
            h4["close"], h4["open"] = px, px * 0.999
            h4["high"], h4["low"] = px * 1.04, px * 0.999
        elif sym in saved:
            for c, arr in saved[sym].items():
                h4[c] = arr


def drive_to_retirement(eng, pid: str) -> None:
    """Aktif aday, elediği girişlerin KAZANÇLI olduğunu öğrenince kendiliğinden emekli olmalı."""
    for _ in range(4):
        tour(eng)                                    # aktif aday girişleri eliyor → gölge birikiyor
    saved: dict = {}
    _rally_frames(eng, True, saved)                  # piyasa çevirir: elenen LONG'lar kazanırdı
    try:
        for _ in range(6):
            label_pending_shadows(eng)
            tour(eng)
            if eng.research.get(pid).state == RETIRED:
                return
    finally:
        _rally_frames(eng, False, saved)
    raise AssertionError(f"emeklilik olmadı: {eng.research.get(pid).stats()}")


def drive_to_active(eng) -> str:
    """Motor turlarıyla boş defterden PAPER_RESEARCH_ACTIVE'e kadar sürer. Manuel geçiş YOK."""
    tour(eng)                                        # TUR-1: aday üretilir → SHADOW
    assert eng.research.shadow() is not None, "boş defterden aday üretilmedi"
    for _ in range(6):
        tour(eng, close_symbols=("ETH/USDT",))       # ETH kapanır → eşleşmiş gözlem
        if eng.research.active() is not None:
            return eng.research.active().policy_id
        tour(eng)                                    # ETH yeniden açılır (SHADOW pending kaydeder)
        if eng.research.active() is not None:
            return eng.research.active().policy_id
    raise AssertionError(f"aktifleşme olmadı: {current(eng).stats() if current(eng) else None}")


# =========================================================================== 1-7) ana yaşam döngüsü
def test_lifecycle_from_empty_book_with_real_engine(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch)
    st = eng.cfg.state_path
    # (0) başlangıçta defter GERÇEKTEN boş ve baseline davranışı bozulmamış
    assert eng.research.records == [] and eng.research.active_policy() is None
    assert eng.research.shadow_policy() is None

    # (1) boş defterden aday KENDİLİĞİNDEN üretiliyor + (2) offline sonucu SHADOW
    s1 = tour(eng, advance=0)
    rec = eng.research.shadow()
    assert rec is not None and rec.state == SHADOW
    assert rec.offline["verdict"] == "SHADOW_CANDIDATE" and not rec.offline["failed_gates"]
    assert rec.policy.get("changed_params") and rec.policy.get("rationale")
    assert len(rec.policy["changed_params"]) == 1, "aday tek parametre değiştirmeli"
    assert s1["opened"], "baseline girişleri SHADOW'dan ÖNCE normal açılmalı"
    coord = json.loads((st / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["ran"] and coord["last_result"]["status"] == "OK"

    # (3) SHADOW gerçek girişleri DEĞİŞTİRMEDEN eşleşmiş gözlem topluyor
    tour(eng, close_symbols=("ETH/USDT",))
    s3 = tour(eng)
    assert s3["opened"], "SHADOW gerçek girişi engellememeli"
    risk = json.loads((st / "risk.json").read_text(encoding="utf-8"))["last_decisions"]
    applied = [e for e in risk if e.get("research_policy_id")]
    assert applied == [], "SHADOW aşamasında hiçbir giriş UYGULANAN politikadan etkilenmemeli"
    assert any(e.get("shadow_policy_id") == rec.policy_id for e in risk), "SHADOW kararı kaydedilmeli"

    # (4) istatistik kapıları geçilince PAPER_RESEARCH_ACTIVE
    pid = drive_to_active(eng)
    act = eng.research.active()
    assert act.policy_id == pid and act.state == ACTIVE
    stats = act.stats()
    assert stats["n_obs"] >= 2 and stats["delta_r"] > 0 and stats["delta_ci95_low"] > 0
    assert stats["metric"] == "risk_budget_contribution_r"
    assert any(h["to"] == ACTIVE for h in act.history)

    # (6) aktifleşme anında AÇIK olan pozisyonlar birebir aynı kalmalı
    before = {s: (p.entry_avg, p.qty, p.stop, tuple(p.targets), p.leverage)
              for s, p in eng.ledger2.positions.items()}
    s5 = tour(eng)
    # (5) aktif politika YALNIZ yeni girişi eliyor
    assert s5["opened"] == [], "aktif aday yeni girişleri elemeli"
    after = {s: (p.entry_avg, p.qty, p.stop, tuple(p.targets), p.leverage)
             for s, p in eng.ledger2.positions.items()}
    for sym, vals in before.items():
        if sym in after:
            assert after[sym] == vals, f"{sym}: açık pozisyon araştırma politikasından etkilendi"
    blocked = [t for t in eng.shadow.trades
               if any("RESEARCH_POLICY_BLOCK" in r for r in t.reason_not_opened)]
    assert blocked, "elenen giriş karşı-olgusal gölge ile izlenmeli"

    # (7) elenen girişler aslında KAZANÇLI çıkarsa aday otomatik emekli olup baseline'a dönülür
    drive_to_retirement(eng, pid)
    assert eng.research.get(pid).state == RETIRED
    assert "kötüleşme" in eng.research.get(pid).retired_reason
    assert eng.research.active() is None and eng.research.active_policy() is None
    s7 = tour(eng)
    assert s7["opened"], "emeklilik sonrası baseline girişleri yeniden açılmalı"


# =========================================================================== 8) cooldown sonrası yeni aday
def test_after_retirement_a_different_candidate_is_tried(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch)
    pid = drive_to_active(eng)
    tried_before = {r.policy_id for r in eng.research.records}      # emeklilikten ÖNCE denenmiş olanlar
    drive_to_retirement(eng, pid)
    for _ in range(4):                                # cooldown dolunca sonraki turlarda yeni aday denenir
        tour(eng)
        if eng.research.shadow() is not None:
            break
    new = eng.research.shadow()
    assert new is not None, "emeklilik sonrası yeni aday denenmedi"
    assert new.policy_id not in tried_before, "aynı aday tekrar önerilmemeli"
    assert new.policy.get("changed_params") != eng.research.get(pid).policy.get("changed_params")
    assert eng.research.get(pid).state == RETIRED and eng.research.active() is None


# =========================================================================== 9) restart dayanıklılığı
def test_state_pending_and_dedupe_survive_restart(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch)
    tour(eng)
    tour(eng, close_symbols=("ETH/USDT",))
    tour(eng)                                          # SHADOW pending oluşur
    st = eng.cfg.state_path
    pid = eng.research.shadow().policy_id
    pending_before = dict(eng.research.pending)
    assert pending_before, "SHADOW pending kaydı oluşmalı"

    from tradingbot.learn.research_policy import ResearchPolicyBook
    reloaded = ResearchPolicyBook(st / "research_policy.json")
    assert reloaded.get(pid).state == SHADOW
    assert dict(reloaded.pending) == pending_before     # pending restart'a dayanıklı
    obs_before = len(reloaded.get(pid).observations)

    eng2 = _engine(tmp_path / "reuse", monkeypatch, RESEARCH_CFG)     # yeni motor örneği
    eng2.cfg.state_path.mkdir(parents=True, exist_ok=True)
    for name in ("research_policy.json", "research_coordinator.json"):
        (eng2.cfg.state_path / name).write_bytes((st / name).read_bytes())
    from tradingbot.learn.research_policy import ResearchPolicyBook as B2
    again = B2(eng2.cfg.state_path / "research_policy.json")
    assert again.get(pid).state == SHADOW and len(again.get(pid).observations) == obs_before
    assert dict(again.pending) == pending_before


# =========================================================================== 10) PAPER dışı modlar
@pytest.mark.parametrize("mode", ["TESTNET", "SHADOW_LIVE", "LIVE"])
def test_non_paper_modes_never_apply_or_progress_research(tmp_path, monkeypatch, mode):
    eng = build(tmp_path / mode, monkeypatch)
    tour(eng)
    pid = eng.research.shadow().policy_id
    before = json.loads((eng.cfg.state_path / "research_policy.json").read_text(encoding="utf-8"))

    class _Mode:
        value = mode
    monkeypatch.setattr(eng.mode_state, "mode", _Mode())
    monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: False)
    for _ in range(3):
        tour(eng, close_symbols=("ETH/USDT",))
        tour(eng)
    assert eng.research.get(pid).state == SHADOW, "PAPER dışında durum İLERLEMEMELİ"
    assert eng.research.active() is None and eng.research.active_policy() is None
    after = json.loads((eng.cfg.state_path / "research_policy.json").read_text(encoding="utf-8"))
    assert [r["state"] for r in after["records"]] == [r["state"] for r in before["records"]]
    coord = json.loads((eng.cfg.state_path / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["read_only"] and not coord["last_result"]["mode_allowed"]
    risk = json.loads((eng.cfg.state_path / "risk.json").read_text(encoding="utf-8"))["last_decisions"]
    assert all(e.get("research_policy_id") is None for e in risk)


def test_live_order_path_blocks_research_even_in_paper(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch)
    monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: True)
    tour(eng)
    assert eng.research.records == [], "gerçek emir yolu açıkken aday ÜRETİLMEMELİ"
    coord = json.loads((eng.cfg.state_path / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["reason"] == "LIVE_ORDER_PATH_ENABLED"


def test_non_paper_gateway_blocks_research(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch,
                overrides={"execution": {"gateway": "binance_futures_testnet", "testnet_enabled": True}})
    tour(eng)
    assert eng.research.records == []
    coord = json.loads((eng.cfg.state_path / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["reason"].startswith("GATEWAY_NOT_PAPER")


# =========================================================================== 11) yetersiz/sparse/leakage
def test_sparse_memory_keeps_baseline_and_produces_no_candidate(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch, sparse=True)
    s = tour(eng)
    assert eng.research.records == [], "sparse hafızadan aday üretilmemeli"
    coord = json.loads((eng.cfg.state_path / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["code"] == "FEATURE_COVERAGE_INVALID"
    assert s["opened"], "baseline davranışı sürmeli"


def test_insufficient_history_keeps_baseline(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch, n=12)
    tour(eng)
    assert eng.research.records == []
    coord = json.loads((eng.cfg.state_path / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["code"] == "INSUFFICIENT_CLOSED_TRADES"


def test_cadence_gate_prevents_work_on_every_tour(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch,
                overrides={"learning_v3": {"research_min_new_closed": 999}})
    tour(eng)
    assert eng.research.records == [], "asgari yeni kapanış kapısı geçilmeden araştırma turu koşmamalı"
    coord = json.loads((eng.cfg.state_path / "research_coordinator.json").read_text(encoding="utf-8"))
    assert coord["last_result"]["ran"] is False and coord["last_result"]["mode_allowed"] is True


# =========================================================================== 12) çift sayım yok
def test_same_trade_id_is_never_counted_twice(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch)
    drive_to_active(eng)
    rec = eng.research.active()
    ids = [o["trade_id"] for o in rec.observations]
    assert ids and len(ids) == len(set(ids)), "aynı işlem iki kez sayılmış"
    for _ in range(3):                                 # ek turlar mevcut gözlemleri çoğaltmamalı
        tour(eng)
    ids2 = [o["trade_id"] for o in eng.research.get(rec.policy_id).observations]
    assert len(ids2) == len(set(ids2))


# =========================================================================== karantina
def test_corrupt_persisted_policy_is_quarantined_and_never_applied(tmp_path, monkeypatch):
    eng = build(tmp_path, monkeypatch)
    tour(eng)
    st = eng.cfg.state_path
    doc = json.loads((st / "research_policy.json").read_text(encoding="utf-8"))
    doc["records"][0]["state"] = ACTIVE                      # diskte kurcalanmış: aktif + risk artıran
    doc["records"][0]["policy"]["size_multiplier"] = 3.0
    (st / "research_policy.json").write_text(json.dumps(doc), encoding="utf-8")

    from tradingbot.learn.research_policy import ResearchPolicyBook
    book = ResearchPolicyBook(st / "research_policy.json")
    assert book.active() is None and book.active_policy() is None
    assert book.quarantined and "QUARANTINED" in book.records[0].retired_reason
    assert book.records[0].state == RETIRED


# =========================================================================== statik / AST kapıları
def _calls_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.add(node.func.attr)
    return out


def test_engine_runtime_path_actually_calls_the_coordinator():
    """Motorun çalışma yolunda koordinatör çağrısı GERÇEKTEN bulunmalı (eksik halka kapandı)."""
    src = Path("tradingbot/engine_v3.py")
    calls = _calls_in(src)
    assert "tick" in calls, "motor turunda ResearchCoordinator.tick çağrısı yok"
    text = src.read_text(encoding="utf-8")
    assert "research_coordinator.tick(" in text
    assert "ResearchCoordinator(" in text
    # eski, kilitlenen doğrudan çağrılar motor yolunda kalmamalı
    assert "self.research.maybe_activate(" not in text
    assert "self.research.evaluate_active(" not in text


def test_this_file_contains_no_manual_research_transitions():
    """Bu dosya durum makinesini ELLE sürmemeli — kanıt yalnız gerçek motor turlarından gelmeli."""
    forbidden = {"propose", "record_offline", "start_shadow", "observe", "maybe_activate", "_set_state"}
    # AST kullanılır: yalnız GERÇEK çağrılar sayılır, string sabitleri değil.
    found = _calls_in(Path(__file__)) & forbidden
    assert not found, f"manuel ResearchPolicyBook geçiş çağrısı bulundu: {sorted(found)}"
    # Kapının kendisi de anlamlı olmalı: en az bir gerçek motor turu çağrısı bulunmalı.
    assert "tour" in _calls_in(Path(__file__))


def test_engine_never_calls_maybe_promote():
    text = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    assert "maybe_promote" not in text, "motor otomatik model terfisi çağıramaz"
    from tradingbot.config_v3 import load_v3
    assert load_v3({"mode": "PAPER"}).learning_v3.auto_promote_in_paper is False

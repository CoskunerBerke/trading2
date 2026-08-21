"""Uçtan uca (ağsız) V3 tur: sahte veri → coin heads → chief → risk → paper futures v2 → tick → öğrenme → state dosyaları."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_coinhead as T  # noqa: E402  (sentetik çerçeveler + legacy ajanlar)

from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.engine_v3 import TradingEngineV3  # noqa: E402


class FakeLive:
    def __init__(self):
        self.price: dict[str, float] = {}

    def snapshot(self, symbol):
        fr = self._frames.get(symbol)
        lv = T.live_for(fr) if fr else dict(T.LIVE)
        if symbol in self.price:
            px = self.price[symbol]
            lv["ticker"].update({"last": px, "high": px * 1.03, "low": px * 0.97})
        lv["ts"] = self._now_s
        return lv


# Sentetik evren: her sembol farkli seed/drift alir -> bagimsiz, benzersiz firsatlar.
# 16 sembol: "12 firsat %6 kapasiteyi doldurur, 13.'su RISK yuzunden reddedilir" senaryosu icin yeterli.
_UNIVERSE: tuple[tuple[str, int, float], ...] = tuple(
    (f"{base}/USDT", seed, drift) for base, seed, drift in (
        ("ETH", 5, 0.0015), ("SOL", 13, 0.003), ("AVAX", 21, 0.0022), ("LINK", 29, 0.0026),
        ("ADA", 37, 0.0019), ("DOT", 43, 0.0024), ("ATOM", 51, 0.0021), ("NEAR", 57, 0.0027),
        ("APT", 63, 0.0018), ("ARB", 71, 0.0025), ("OP", 79, 0.0020), ("INJ", 83, 0.0028),
        ("SUI", 91, 0.0023), ("SEI", 97, 0.0017), ("TIA", 103, 0.0029), ("RUNE", 109, 0.0016),
    ))


def _engine(tmp_path: Path, monkeypatch, v3_overrides: dict | None = None,
            *, before_build=None, symbols: int | list[str] | None = None,
            equity: float | None = None) -> TradingEngineV3:
    """Agsiz V3 motoru. `v3_overrides` v3 config bolumlerini derinlemesine gunceller;
    `before_build(cfg)` motor kurulmadan ONCE state dizinine dokunmak icin cagrilir.

    `symbols` bir sayi ise `_UNIVERSE`'in ilk N sembolu kullanilir (varsayilan 2). `equity` senaryo
    kurulumu icindir ve bir ADET kotasi degildir.

    DEFTER ADET TAVANI OVERRIDE EDILEMEZ (bilincli): `cfg.futures.max_positions` uretim
    varsayilaninda (3) birakilir. Motor bu tavani runtime risk profilinden turetir; PAPER_RESEARCH
    adet limiti tanimlamadigi icin defter `None` ile kurulur. Eskiden testler bunu `4`/`8` ile
    eziyordu ve "4/4 acildi" sonucu uretimi KANITLAMIYORDU."""
    if symbols is None:
        picks = list(_UNIVERSE[:2])
    elif isinstance(symbols, int):
        picks = list(_UNIVERSE[:symbols])
    else:
        picks = [u for u in _UNIVERSE if u[0] in symbols]
    syms = [s for s, _, _ in picks]
    cfg = BotConfig()
    cfg.coins = list(syms)
    cfg.scanner.enabled = False
    cfg.scanner.core_coins = list(syms)
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    if equity is not None:
        cfg.futures.starting_equity_usdt = float(equity)
        cfg.risk.starting_equity_usdt = float(equity)
    raw = {"coin_heads": {"consensus_threshold": 0.05, "min_confidence": 0.05},
           "learning_v3": {"min_samples_train": 5}}
    for sec, vals in (v3_overrides or {}).items():
        raw[sec] = {**raw.get(sec, {}), **vals}
    cfg.v3 = load_v3(raw)
    if before_build is not None:
        cfg.state_path.mkdir(parents=True, exist_ok=True)
        before_build(cfg)
    eng = TradingEngineV3(cfg)
    frames = {s: T.frames(seed=sd, drift=dr) for s, sd, dr in picks}
    # sentetik mumları "şimdi"ye kaydır (veri kalitesi kapısı bayat mum görmesin): son 4h bar bir bar önce kapandı
    import pandas as pd
    from tradingbot.core import utc_now
    now_ms = int(utc_now().timestamp() * 1000)
    for fr in frames.values():
        for tf, tf_ms in (("1d", 86_400_000), ("4h", 14_400_000), ("1h", 3_600_000)):
            df = fr[tf]
            shift = (now_ms - now_ms % tf_ms) - 2 * tf_ms - int(df["timestamp"].iloc[-1])
            df["timestamp"] = df["timestamp"] + shift
            df.index = df.index + pd.Timedelta(milliseconds=shift)
    fake_live = FakeLive()
    fake_live._frames = frames
    fake_live._now_s = now_ms / 1000
    eng.runner.live = fake_live
    briefs = {}

    def run_symbol(symbol, analysis=None, prefetched=None):
        fr = frames[symbol]
        live = fake_live.snapshot(symbol)
        from tradingbot.agents.base import CoinContext
        from tradingbot.agents.manager import CoinManagerAgent
        from tradingbot.agents.market import MarketDataAgent
        from tradingbot.agents.technical import TECHNICAL_AGENTS
        # legacy plan boyutu motor equity'siyle ayni tabani kullansin (varsayilan 50 -> davranis degismez)
        ctx = CoinContext(symbol=symbol, frames=fr, live=live, equity_usdt=cfg.futures.starting_equity_usdt,
                          risk_pct=2.0, atr_stop_mult=2.5)
        reports = [a.run(ctx) for a in TECHNICAL_AGENTS + [MarketDataAgent()]]
        b = CoinManagerAgent().decide(ctx, reports)
        b.generated_at = "2026-08-18T00:00:00+00:00"
        h4 = fr["4h"]
        b.last_close_4h, b.last_bar_4h = float(h4["close"].iloc[-1]), str(h4.index[-1])
        eng.runner.last_frames[symbol] = fr
        briefs[symbol] = b
        return b

    monkeypatch.setattr(eng.runner, "run_symbol", run_symbol)
    monkeypatch.setattr(eng, "_chart", lambda b: "")
    eng._fake_live = fake_live
    return eng


def test_v3_tour_end_to_end_opens_ticks_learns_and_persists(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    st = eng.cfg.state_path
    s1 = eng.tour(do_scan=False, obsidian=True, charts=False)
    assert s1["run_id"] and s1["risk"]["killswitch"] == "ARMED" and "chief" in s1
    for f in ("coin_heads.json", "risk.json", "health.json", "heartbeat.json", "futures_ledger.json", "mode.json", "spot_ledger.json", "agents.json"):
        assert (st / f).exists(), f
    ch = json.loads((st / "coin_heads.json").read_text(encoding="utf-8"))
    assert len(ch["heads"]) == 2 and ch["chief"]["approval_flags_required"] == ["coin_head_valid", "no_red_team_veto", "risk_engine_allowed"]
    led = json.loads((st / "futures_ledger.json").read_text(encoding="utf-8"))
    assert led.get("schema_version") == 2
    assert json.loads((st / "mode.json").read_text(encoding="utf-8"))["mode"] == "PAPER"
    # geri çekilme tetiği: ATR planı girişi = fiyat → ilk turda açılabilir (risk+chief izin verdiyse)
    opened_total = len(s1["opened"])
    risk_log = json.loads((st / "risk.json").read_text(encoding="utf-8"))["last_decisions"]
    assert isinstance(risk_log, list)
    if opened_total:
        assert eng.ledger2.positions and (st / "trade_memory.jsonl").exists()
        pos = next(iter(eng.ledger2.positions.values()))
        assert pos.stop is not None and pos.targets and pos.leverage <= eng.profile.futures_max_leverage
        # 2) fiyatı hedefin ötesine taşı → tick kapatır → öğrenme (v1 + v2) çalışır, defter ÖNCE kaydedilir
        sym = pos.symbol
        tgt = float(pos.targets[-1])
        eng._fake_live.price[sym] = tgt * (1.01 if pos.side.value == "LONG" else 0.99)
        s2 = eng.tour(do_scan=False, obsidian=True, charts=False)
        assert sym in s2["closed"] and sym not in eng.ledger2.positions
        assert eng.learner.state.n_trades >= 1 and eng.learner2.n_closed >= 1
        led2 = json.loads((st / "futures_ledger.json").read_text(encoding="utf-8"))
        assert led2["history"] and led2["history"][0]["exit_reason"]
        mem = [json.loads(l) for l in (st / "trade_memory.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(m["kind"] == "exit" for m in mem) and any(m["kind"] == "entry" and m.get("decision") for m in mem)
    # kill switch → yeni giriş yok, çıkışlar çalışır
    eng.killswitch.trip("MANUAL", "test")
    s3 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s3["opened"] == [] and s3["risk"]["killswitch"] == "HALT_ALL"
    hb = json.loads((st / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["run_id"] == s3["run_id"]
    # Obsidian: legacy dosyalar + (varsa) Coin Heads
    vault = Path(eng.cfg.obsidian.vault_path)
    assert (vault / "Paper Futures.md").exists() and "v2" in (vault / "Paper Futures.md").read_text(encoding="utf-8")


def test_v3_engine_refuses_when_config_invalid(tmp_path: Path):
    from tradingbot.core import ConfigError
    with pytest.raises(ConfigError):
        load_v3({"mode": "LIVE"})
    with pytest.raises(ConfigError):
        load_v3({"risk_profiles": {"profile": "TESTNET", "overrides": {"risk_per_trade_pct": 50}}})
    with pytest.raises(ConfigError):
        load_v3({"tax_policy": {"enabled": True}})
    cfg = load_v3({"mode": "PAPER", "unknown_section": {}, "llm": {"mode": "advisory", "foo": 1}})
    assert cfg.llm.mode == "ADVISORY" and any("foo" in w for w in cfg.warnings)


def test_v3_closed_trade_writes_frozen_obsidian_trade_note_once(tmp_path: Path, monkeypatch):
    """Kapanan işlem `Trades/<id>.md` notunu post-mortem + Ders/Model zinciriyle TAM BİR KEZ üretir."""
    eng = _engine(tmp_path, monkeypatch)
    s1 = eng.tour(do_scan=False, obsidian=True, charts=False)
    if not s1["opened"]:
        pytest.skip("bu turda pozisyon açılmadı")
    pos = next(iter(eng.ledger2.positions.values()))
    sym, tid = pos.symbol, pos.id
    vault = Path(eng.cfg.obsidian.vault_path)
    assert not (vault / "Trades" / f"{tid}.md").exists()  # açıkken not yok
    eng._fake_live.price[sym] = float(pos.targets[-1]) * (1.01 if pos.side.value == "LONG" else 0.99)
    eng.tour(do_scan=False, obsidian=True, charts=False)
    note = vault / "Trades" / f"{tid}.md"
    assert note.exists(), "kapanış sonrası işlem notu yazılmadı"
    txt = note.read_text(encoding="utf-8")
    assert "status: CLOSED" in txt and "[[Learning/Dersler]]" in txt and "[[Models/Registry]]" in txt
    assert "## Post-mortem" in txt and "(post-mortem yok)" not in txt  # learner v2 post-mortem'i iliştirildi
    assert eng.ch_writer.trade_note_frozen(tid)
    # ikinci tur (retry/restart benzeri) notu yeniden yazmaz → tek kez öğrenilir/dondurulur
    mtime = note.stat().st_mtime_ns
    eng.tour(do_scan=False, obsidian=True, charts=False)
    assert note.stat().st_mtime_ns == mtime
    assert eng.learner2.n_closed == 1


def test_active_research_policy_blocks_new_entries_without_touching_open_positions(tmp_path: Path, monkeypatch):
    """Aktif araştırma adayı YENİ girişi eler; açık pozisyonun stop/TP'sine ve çıkış yönetimine DOKUNMAZ."""
    from datetime import timedelta

    from tradingbot.core import utc_now
    from tradingbot.learn.policy import CandidatePolicy
    from tradingbot.learn.research_policy import BLOCKED, ResearchGates, ResearchPolicyBook

    eng = _engine(tmp_path, monkeypatch)
    s0 = eng.tour(do_scan=False, obsidian=False, charts=False)     # baseline tur: aktif aday YOK
    assert eng.research.active_policy() is None
    assert s0["opened"] and eng.ledger2.positions, "test boş geçmemeli: baseline turda pozisyon açılmalı"
    risk0 = json.loads((eng.cfg.state_path / "risk.json").read_text(encoding="utf-8"))["last_decisions"]
    assert all(e.get("research_policy_id") is None and e.get("research_size_multiplier") == 1.0
               for e in risk0), "aktif aday yokken davranış birebir aynı kalmalı"
    before = {s: (p.stop, tuple(p.targets), p.qty, p.entry_avg) for s, p in eng.ledger2.positions.items()}
    n_shadow_before = len(eng.shadow.trades)

    # her girişi eleyen bir aday aktifleştir (yalnız FİLTRE; risk/kaldıraç/stop değişmez)
    now = utc_now()
    block_all = CandidatePolicy(policy_id="block_all", seed=7, min_p_win=0.90,
                                rationale="test: bütün girişleri ele")
    gates = ResearchGates(min_shadow_obs=3, min_active_obs=999, cooldown_hours=0.0,
                          min_fold_consistency=0.6)
    book = ResearchPolicyBook(eng.cfg.state_path / "research_policy.json", gates)
    book.propose(block_all, now=now)
    book.record_offline("block_all", {"verdict": "SHADOW_CANDIDATE", "fold_consistency": 1.0}, now=now)
    for i in range(3):          # aktivasyon kapisi CI95 alt siniri > 0 arar -> birden fazla gozlem
        book.observe("block_all", trade_id=f"seed{i}", baseline_r=-1.0,
                     risk_budget_contribution_r=0.0, kind=BLOCKED, size_multiplier=0.0, at=now)
    assert book.maybe_activate(now=now + timedelta(hours=1)) == "block_all"
    eng.research = book                                            # motor bu defteri kullansın

    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s["opened"] == [], "aktif araştırma adayı varken yeni pozisyon açılmamalı"
    # AÇIK POZİSYONLAR AYNEN DURUYOR: stop, TP, miktar, giriş fiyatı değişmedi
    after = {sym: (p.stop, tuple(p.targets), p.qty, p.entry_avg) for sym, p in eng.ledger2.positions.items()}
    for sym, vals in before.items():
        if sym in after:
            assert after[sym] == vals, f"{sym}: açık pozisyon araştırma politikasından etkilendi"
    # elenen girişler karşı-olgusal gölge olarak izleniyor
    blocked = [t for t in eng.shadow.trades[n_shadow_before:]
               if any("RESEARCH_POLICY_BLOCK" in r for r in t.reason_not_opened)]
    if blocked:
        assert all(t.id in book.pending for t in blocked)           # eşleşmiş gözlem beklemede
        assert all(book.pending[t.id]["policy_id"] == "block_all" for t in blocked)
    # risk günlüğüne aday kararı yazıldı; mod ve kill switch değişmedi
    assert s["risk"]["killswitch"] == "ARMED"
    assert eng.mode_state.mode.value == "PAPER"

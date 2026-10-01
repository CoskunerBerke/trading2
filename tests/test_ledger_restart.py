"""v1/v2 defter dosya sahipliği ve restart/resume: v1 yükleyici v2 dosyasını ne parse eder ne üzerine yazar; v3 motoru açık v2
pozisyonla başlar, pozisyon bir kez ve aynı değerlerle yüklenir; art arda restart duplicate/değişiklik üretmez; bilinmeyen/bozuk şema fail-closed."""
from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as E  # noqa: E402

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec  # noqa: E402
from tradingbot.core import StorageError  # noqa: E402
from tradingbot.paper_futures import FuturesLedger, LedgerSchemaError, read_ledger_schema  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _v2_with_open_short(path: Path) -> dict:
    """Gerçek phase-3 durumuna benzer v2 defter: SHORT SUI, notional 15, 1x, stop/TP; kaydet ve semantik değerleri döndür."""
    led = FuturesLedgerV2(50)
    pos = led.open("SUI/USDT", "SHORT", Decimal("0.65"), SizeSpec(Decimal("15"), AmountType.NOTIONAL, 1),
                    stop=Decimal("0.6777"), targets=[Decimal("0.6053"), Decimal("0.5812")], setup_type="pullback")
    assert pos is not None and led.positions
    led.save(path)
    p = json.loads(path.read_text(encoding="utf-8"))["positions"]["SUI/USDT"]
    return {"id": p["id"], "qty": p["qty"], "entry_avg": p["entry_avg"], "isolated_margin": p["isolated_margin"], "leverage": p["leverage"],
            "stop": p["stop"], "targets": p["targets"], "entry_fee": p["entry_fee"], "fills": len(p["fills"]), "realized_pnl": p["realized_pnl"]}


def _v1_file(path: Path, *, with_position: bool = True) -> None:
    led = FuturesLedger(equity=50.0, starting_equity=50.0)
    if with_position:
        led.open("ETH/USDT", "LONG", 100.0, 20.0, 2, 95.0, 110.0, 120.0)
    led.save(path)
    d = json.loads(path.read_text(encoding="utf-8"))
    assert "schema_version" not in d and "wallet_balance" not in d      # gerçek legacy dosya: sürüm/hash alanı yok


# 1 + 8) v1 dosya v1 yükleyici ile (sürüm alanı olmayan legacy) — geriye uyumlu
def test_v1_loader_opens_legacy_v1_file(tmp_path: Path):
    p = tmp_path / "futures_ledger.json"
    _v1_file(p)
    assert read_ledger_schema(p)[0] == 1
    led = FuturesLedger.load(p, 50)
    assert "ETH/USDT" in led.positions and led.equity > 0
    _v1_file(tmp_path / "empty.json", with_position=False)
    assert FuturesLedger.load(tmp_path / "empty.json", 50).positions == {}


# 2) v2 dosya v2 yükleyici ile
def test_v2_loader_opens_v2_file(tmp_path: Path):
    p = tmp_path / "futures_ledger.json"
    sem = _v2_with_open_short(p)
    led = FuturesLedgerV2.load(p, starting_equity=50)
    pos = led.positions["SUI/USDT"]
    assert pos.id == sem["id"] and str(pos.qty) == sem["qty"] and str(pos.entry_avg) == sem["entry_avg"] and len(pos.fills) == sem["fills"]


# 3) v1 yükleyici v2 dosyasını parse ETMEZ, boş deftere DÖNÜŞTÜRMEZ, üzerine YAZMAZ
def test_v1_loader_refuses_v2_file_and_never_overwrites(tmp_path: Path):
    p = tmp_path / "futures_ledger.json"
    _v2_with_open_short(p)
    h = _sha(p)
    with pytest.raises(LedgerSchemaError, match="FuturesLedgerV2"):
        FuturesLedger.load(p, 50)
    assert _sha(p) == h
    v1 = FuturesLedger(equity=50.0, starting_equity=50.0)
    with pytest.raises(LedgerSchemaError):
        v1.save(p)
    assert _sha(p) == h and not (tmp_path / "futures_ledger.json.bak").exists()
    # legacy motor (v1) aynı dosyayla fail-closed: dosya korunur
    from tradingbot.config import BotConfig
    from tradingbot.engine import TradingEngine
    cfg = BotConfig(); cfg.project_root = tmp_path; cfg.scanner.enabled = False
    (cfg.state_path).mkdir(parents=True, exist_ok=True)
    (cfg.state_path / "futures_ledger.json").write_bytes(p.read_bytes())
    with pytest.raises(LedgerSchemaError):
        TradingEngine(cfg)
    assert _sha(cfg.state_path / "futures_ledger.json") == h


# 9 + 10) bilinmeyen şema fail-closed, bozuk dosya sessizce boş defter olmaz (v1 ve v2 yükleyiciler)
def test_unknown_and_corrupt_schema_fail_closed(tmp_path: Path):
    unk = tmp_path / "unk.json"
    unk.write_text(json.dumps({"schema_version": 99, "kind": "futures", "wallet_balance": "1", "positions": {}}), encoding="utf-8")
    h = _sha(unk)
    with pytest.raises(LedgerSchemaError):
        FuturesLedger.load(unk, 50)
    with pytest.raises(StorageError):
        FuturesLedgerV2.load(unk, starting_equity=50)
    assert _sha(unk) == h
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"schema_version": "x", "positions": {}}), encoding="utf-8")
    with pytest.raises(LedgerSchemaError):
        FuturesLedger.load(weird, 50)
    with pytest.raises(StorageError):
        FuturesLedgerV2.load(weird, starting_equity=50)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(LedgerSchemaError):
        FuturesLedger.load(bad, 50)
    with pytest.raises(StorageError):
        FuturesLedgerV2.load(bad, starting_equity=50)
    assert bad.read_text(encoding="utf-8") == "{not json"
    # v1 kaydı bozuk/bilinmeyen dosyanın üzerine de yazmaz
    with pytest.raises(LedgerSchemaError):
        FuturesLedger(equity=1.0, starting_equity=1.0).save(bad)


# 4 + 5 + 6 + 7 + 11) v3 motoru açık v2 pozisyonla başlar; art arda restart: pozisyon bir kez, değerler ve dosya değişmez (migration yok → idempotent)
def test_v3_engine_restarts_with_open_v2_position_idempotent(tmp_path: Path, monkeypatch):
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    from tradingbot.engine_v3 import TradingEngineV3
    cfg = BotConfig(); cfg.project_root = tmp_path; cfg.scanner.enabled = False
    cfg.coins = ["ETH/USDT", "SOL/USDT"]; cfg.scanner.core_coins = ["ETH/USDT", "SOL/USDT"]
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    p = cfg.state_path / "futures_ledger.json"
    sem = _v2_with_open_short(p)
    h0 = _sha(p)
    for _ in range(2):                                   # iki ardışık restart
        eng = TradingEngineV3(cfg)
        assert eng.ledger is eng.ledger2                 # dosyanın sahibi v2; legacy defter nesnesi yok
        assert list(eng.ledger2.positions) == ["SUI/USDT"]
        pos = eng.ledger2.positions["SUI/USDT"]
        assert (pos.id, str(pos.qty), str(pos.entry_avg), str(pos.isolated_margin), pos.leverage, str(pos.stop), [str(t) for t in pos.targets],
                str(pos.entry_fee), len(pos.fills), str(pos.realized_pnl)) == \
               (sem["id"], sem["qty"], sem["entry_avg"], sem["isolated_margin"], sem["leverage"], sem["stop"], sem["targets"],
                sem["entry_fee"], sem["fills"], sem["realized_pnl"])
        assert _sha(p) == h0                             # yalnız load: bakiye/fee/P&L/marj ve dosya değişmedi
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["schema_version"] == 2 and len(d["positions"]) == 1 and len(d["positions"]["SUI/USDT"]["fills"]) == 1 and d["history"] == []


# 12) motor seviyesi resume → tick → hold/exit: açık SUI, fiyat TP2 ötesine → tek kapanış, tek exit fill, defter tutarlı, restart sonrası yeniden açılmaz
def test_v3_resume_tick_hold_then_exit_once(tmp_path: Path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch)
    st = eng.cfg.state_path
    p = st / "futures_ledger.json"
    sem = _v2_with_open_short(p)
    from tradingbot.engine_v3 import TradingEngineV3
    eng2 = E._engine(tmp_path, monkeypatch)              # restart: aynı state dizini
    assert isinstance(eng2, TradingEngineV3) and list(eng2.ledger2.positions) == ["SUI/USDT"]
    pos = eng2.ledger2.positions["SUI/USDT"]
    assert pos.id == sem["id"] and len(pos.fills) == 1
    # HOLD: fiyat giriş civarı → pozisyon açık kalır
    recs = eng2.ledger2.tick({"SUI/USDT": Decimal("0.652")}, bar_advance=True)
    assert recs == [] and "SUI/USDT" in eng2.ledger2.positions and pos.bars_held >= 1
    # EXIT: fiyat TP2'nin altına → tek kapanış kaydı
    recs = eng2.ledger2.tick({"SUI/USDT": Decimal("0.57")}, bar_advance=True)
    assert len(recs) == 1 and "SUI/USDT" not in eng2.ledger2.positions
    rec = recs[0]
    assert rec.exit_reason and rec.quantity == Decimal(sem["qty"]) and rec.exit_price is not None
    assert rec.fees == rec.entry_fee + rec.exit_fee and rec.net_pnl == rec.gross_pnl - rec.fees + rec.funding and rec.pnl == rec.net_pnl
    assert rec.gross_pnl > 0 and rec.r_multiple > 0    # SHORT, fiyat düştü
    eng2.ledger2.save(p)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["positions"] == {} and len(d["history"]) == 1
    exit_fills = [f for f in d["history"][0].get("fills", []) if f.get("kind") != "entry"]
    assert 1 <= len(exit_fills) <= 2                    # TP1 kısmi + TP2 kalan ya da tek TP2 kapanışı; duplicate yok
    # restart: kapalı pozisyon yeniden açılmaz, history değişmez
    eng3 = E._engine(tmp_path, monkeypatch)
    assert eng3.ledger2.positions == {} and len(eng3.ledger2.history) == 1

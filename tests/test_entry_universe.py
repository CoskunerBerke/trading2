"""SABIT GIRIS EVRENI — kapinin yalniz GIRISI bagladiginin ve cikisi bagLAMADIGININ kaniti.

Talimat (2026-09-11): yeni futures girisi yalniz on USDⓈ-M perpetual sembolde acilabilir;
liste disinda kalan ACIK pozisyonlarin fiyat takibi ve cikis yonetimi AYNEN surer, "liste
degisti" diye pozisyon kapatilmaz.

Buradaki testler dort seyi kilitler:

1. Uyelik kurali (`entry_universe`) — normalize etme, bos evren, yon izinleri.
2. Config fail-closed: `enabled=true` + bos liste program BASLATMAZ.
3. Motor: evren disi sembolde YENI giris acilmaz ve gerekce `SYMBOL_NOT_IN_ENTRY_UNIVERSE`
   olarak risk gunlugune yazilir.
4. Motor: evren disindaki ACIK pozisyon tur kapsaminda KALIR ve kapanabilir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.config_v3 import ConfigError, load_v3, validate_v3  # noqa: E402
from tradingbot.decision_gates import GATES, HARD_SAFETY  # noqa: E402
from tradingbot.entry_universe import (GATE_CODE, entry_block_reason, normalize,  # noqa: E402
                                       normalize_all, tour_symbols)

TEN = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
       "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]


# --------------------------------------------------------------------------- 1) uyelik kurali
@pytest.mark.parametrize("raw,want", [
    ("BTCUSDT", "BTC/USDT"), ("btc/usdt", "BTC/USDT"), ("BTC-USDT", "BTC/USDT"),
    ("BTC_USDT", "BTC/USDT"), ("  eth/usdt ", "ETH/USDT"), ("AAVEUSDT", "AAVE/USDT"),
    ("ETHBTC", "ETH/BTC"), ("", ""),
])
def test_normalize_accepts_both_exchange_and_bot_spelling(raw, want):
    assert normalize(raw) == want


def test_normalize_prefers_usdt_over_usd_suffix():
    """`BTCUSDT` `BTC/USDT` olmali; `USD` once eslenirse `BTCUSD/T` gibi bir sonuc cikardi."""
    assert normalize("BTCUSDT") == "BTC/USDT"
    assert normalize("BTCUSD") == "BTC/USD"


def test_normalize_all_dedupes_and_keeps_order():
    assert normalize_all(["BTCUSDT", "BTC/USDT", "ethusdt", ""]) == ["BTC/USDT", "ETH/USDT"]


def test_gate_code_is_registered_as_hard_safety():
    """Kayitsiz kod `UnknownGateCode` uretir — kapinin sinifi kaynakta kayitli olmali."""
    assert GATE_CODE in GATES
    assert GATES[GATE_CODE].cls == HARD_SAFETY
    assert GATES[GATE_CODE].stage == "risk"


def test_member_symbol_is_not_blocked():
    for s in TEN:
        assert entry_block_reason(s, market="USDM_PERP", universe=TEN, direction="LONG") is None
        assert entry_block_reason(s, market="USDM_PERP", universe=TEN, direction="SHORT") is None


def test_non_member_symbol_is_blocked_in_both_directions():
    for d in ("LONG", "SHORT"):
        assert entry_block_reason("ADA/USDT", market="USDM_PERP", universe=TEN, direction=d) == "NOT_IN_UNIVERSE"
        assert entry_block_reason("DOT/USDT", market="USDM_PERP", universe=TEN, direction=d) == "NOT_IN_UNIVERSE"


def test_exchange_spelling_is_accepted_by_the_gate():
    """Evren `BTC/USDT` yazilmis, aday `BTCUSDT` gelmis — bu bir RET sebebi olmamali."""
    assert entry_block_reason("BTCUSDT", market="USDM_PERP", universe=TEN) is None
    assert entry_block_reason("BTC/USDT", market="USDM_PERP", universe=["BTCUSDT"]) is None


def test_empty_universe_is_fail_closed_not_fail_open():
    """Bos evren 'her sembol serbest' DEGILDIR."""
    assert entry_block_reason("BTC/USDT", market="USDM_PERP", universe=[]) == "UNIVERSE_EMPTY"


def test_disabled_universe_blocks_nothing():
    assert entry_block_reason("ADA/USDT", market="USDM_PERP", universe=TEN, enabled=False) is None
    assert entry_block_reason("ADA/USDT", market="USDM_PERP", universe=[], enabled=False) is None


def test_gate_binds_futures_only_not_spot():
    """Talimat vadeli giris evrenini sabitler; SPOT yolu bu kapiyla baglanmaz."""
    assert entry_block_reason("ADA/USDT", market="SPOT", universe=TEN) is None
    assert entry_block_reason("ADA/USDT", market="USDM_PERP", universe=TEN) == "NOT_IN_UNIVERSE"


def test_direction_permissions_apply_only_to_members():
    assert entry_block_reason("BTC/USDT", market="USDM_PERP", universe=TEN,
                              direction="SHORT", allow_short=False) == "SHORT_DISABLED"
    assert entry_block_reason("BTC/USDT", market="USDM_PERP", universe=TEN,
                              direction="LONG", allow_short=False) is None
    assert entry_block_reason("BTC/USDT", market="USDM_PERP", universe=TEN,
                              direction="LONG", allow_long=False) == "LONG_DISABLED"


def test_tour_scope_always_contains_open_positions_outside_the_universe():
    """Evren disi ACIK pozisyon analiz kapsaminda KALIR — cikis yonetimi fiyat gerektirir."""
    got = tour_symbols(universe=TEN, open_positions=["ADAUSDT", "DOT/USDT"])
    assert "ADA/USDT" in got and "DOT/USDT" in got
    assert set(TEN).issubset(set(got))
    assert len(got) == len(TEN) + 2                     # tekillestirme calisiyor


def test_tour_scope_does_not_duplicate_an_open_member():
    got = tour_symbols(universe=TEN, open_positions=["BTC/USDT"])
    assert got.count("BTC/USDT") == 1
    assert len(got) == len(TEN)


# --------------------------------------------------------------------------- 2) config fail-closed
def _v3(raw: dict):
    cfg = load_v3(raw)
    validate_v3(cfg)
    return cfg


def test_config_rejects_enabled_universe_with_no_symbols():
    with pytest.raises(ConfigError, match="ENTRY_UNIVERSE_EMPTY"):
        _v3({"entry_universe": {"enabled": True, "symbols": []}})


def test_config_rejects_universe_with_both_directions_disabled():
    with pytest.raises(ConfigError, match="allow_long ve allow_short"):
        _v3({"entry_universe": {"enabled": True, "symbols": TEN,
                                "allow_long": False, "allow_short": False}})


def test_config_rejects_unresolvable_symbol():
    with pytest.raises(ConfigError, match="çözümlenemedi"):
        _v3({"entry_universe": {"enabled": True, "symbols": ["NOTASYMBOL"]}})


def test_config_normalizes_symbols_so_gate_and_report_see_one_spelling():
    cfg = _v3({"entry_universe": {"enabled": True, "symbols": ["btcusdt", "ETH-USDT"]}})
    assert cfg.entry_universe.symbols == ["BTC/USDT", "ETH/USDT"]


def test_disabled_universe_needs_no_symbols():
    cfg = _v3({"entry_universe": {"enabled": False, "symbols": []}})
    assert cfg.entry_universe.enabled is False


# --------------------------------------------------------------------------- 3) motor: giris kapali
def _risk_log(eng) -> list[dict]:
    """Kapinin gerekcesi UYGULAMA ciktisindan okunur (`state/risk.json`), test icinden degil."""
    return json.loads((eng.cfg.state_path / "risk.json").read_text(encoding="utf-8"))["last_decisions"]


def _universe_engine(tmp_path, monkeypatch, universe: list[str], symbols: list[str]):
    """Evren acik motor. `perp_frames` fixture cerceveleriyle degistirilir.

    GEREKLI: evren acikken motor, evren sembollerinin karar cercevelerini PERPETUAL
    kaynaktan ister (spot ikamesi fail-closed). Test agdan mum cekmez; ayni sentetik
    cerceveler perpetual kaynagi taklit eder.
    """
    eng = TE._engine(tmp_path, monkeypatch, {"entry_universe": {"enabled": True, "symbols": universe}},
                     symbols=symbols, p_win=0.62)
    monkeypatch.setattr(eng, "perp_frames", lambda sym, timeframes=None: dict(eng._fake_live._frames[sym]))
    return eng


def test_engine_opens_nothing_outside_the_universe(tmp_path: Path, monkeypatch):
    """Kapsam ZORLA evren disina cikarildi (`symbols_override`) → aday uretildi ama ACILMADI.

    `symbols_override` burada kapiyi yalitmak icindir: tur kapsami evrene esit olsaydi evren
    disi aday hic uretilmezdi ve test kapiyi degil kapsam daraltmasini olcerdi.
    """
    eng = _universe_engine(tmp_path, monkeypatch, universe=["BTC/USDT"], symbols=["ETH/USDT", "SOL/USDT"])
    s = eng.tour(do_scan=False, obsidian=False, charts=False, symbols_override=["ETH/USDT", "SOL/USDT"])
    assert s["opened"] == []
    risk_log = _risk_log(eng)
    blocked = [r for r in risk_log if r.get("block_code") == GATE_CODE]
    assert blocked, f"evren disi aday reddedilmeli, risk gunlugu: {risk_log}"
    assert {r["symbol"] for r in blocked} <= {"ETH/USDT", "SOL/USDT"}
    assert all(r["risk_allowed"] is False and r["hard_veto"] is True for r in blocked)
    assert all(r["block_detail"] == "NOT_IN_UNIVERSE" for r in blocked)


def test_engine_still_opens_inside_the_universe(tmp_path: Path, monkeypatch):
    """Kapi ayrim yapmali: AYNI kurulum evrenin icindeyken evren kapisi devreye girmemeli.

    Ayni fixture, ayni semboller, tek fark evren uyeligi — boylece "hic aday yoktu" ile
    "kapi engelledi" karisamaz.
    """
    eng = _universe_engine(tmp_path, monkeypatch, universe=["ETH/USDT", "SOL/USDT"], symbols=["ETH/USDT", "SOL/USDT"])
    eng.tour(do_scan=False, obsidian=False, charts=False, symbols_override=["ETH/USDT", "SOL/USDT"])
    blocked = [r for r in _risk_log(eng) if r.get("block_code") == GATE_CODE]
    assert blocked == [], f"evren ICI aday evren kapisiyla reddedilmemeli: {blocked}"


def test_engine_tour_scope_is_the_universe_when_scanner_is_off(tmp_path: Path, monkeypatch):
    eng = _universe_engine(tmp_path, monkeypatch, universe=["ETH/USDT", "SOL/USDT"], symbols=["ETH/USDT", "SOL/USDT"])
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert set(s["symbols"]) == {"ETH/USDT", "SOL/USDT"}


def test_open_position_outside_the_universe_stays_in_scope_and_is_not_force_closed(tmp_path: Path, monkeypatch):
    """EN ONEMLI DAVRANIS: evren daraldi diye acik pozisyon kapsamdan DUSMEZ ve KAPATILMAZ.

    Once ETH/SOL evreninde bir pozisyon acilir, sonra evren yalniz BTC olacak sekilde
    daraltilir. Pozisyon: (a) hala tur kapsaminda, (b) hala acik, (c) YENI giris kapali.
    """
    eng = _universe_engine(tmp_path, monkeypatch, universe=["ETH/USDT", "SOL/USDT"], symbols=["ETH/USDT", "SOL/USDT"])
    for _ in range(3):
        eng.tour(do_scan=False, obsidian=False, charts=False)
        if eng.ledger2.positions:
            break
    opened = set(eng.ledger2.positions)
    if not opened:
        pytest.skip("fixture bu turda pozisyon acmadi — evren daralma senaryosu kurulamiyor")

    eng.cfg.v3.entry_universe.symbols = ["BTC/USDT"]          # evren daraldi
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert opened <= set(s["symbols"]), "acik pozisyon tur kapsamindan dusmemeli"
    assert opened <= set(eng.ledger2.positions), "evren degisikligi pozisyon KAPATMAMALI"
    assert s["opened"] == [], "daraltilmis evrende yeni giris olmamali"


# --------------------------------------------------------------------------- 4) veri kimligi
def test_universe_symbols_are_analysed_on_perpetual_frames(tmp_path: Path, monkeypatch):
    """Evren USDⓈ-M perpetual'dir; karar cerceveleri de perpetual kaynaktan ISTENMELI.

    Onceki davranis: `cfg.coins` icindeki semboller `core_set` muafiyetiyle TradingView
    `BINANCE:<SYM>` (SPOT) akisindan besleniyordu. Yani on coin SPOT mumlariyla analiz
    edilip PERP sozlesmede islem goruyordu. Bu test o muafiyeti kilitler.
    """
    asked: list[str] = []
    eng = TE._engine(tmp_path, monkeypatch,
                     {"entry_universe": {"enabled": True, "symbols": ["ETH/USDT", "SOL/USDT"]}},
                     symbols=["ETH/USDT", "SOL/USDT"], p_win=0.62)

    def _perp(sym, timeframes=None):
        asked.append(sym)
        return dict(eng._fake_live._frames[sym])

    monkeypatch.setattr(eng, "perp_frames", _perp)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert set(asked) == {"ETH/USDT", "SOL/USDT"}, "evren sembolleri icin perpetual cerceve istenmedi"
    prov = json.loads((eng.cfg.state_path / "frame_provenance.json").read_text(encoding="utf-8"))
    assert prov["universe_enabled"] is True
    for sym in ("ETH/USDT", "SOL/USDT"):
        assert prov["by_symbol"][sym]["market"] == "USDM_PERP"
        assert prov["by_symbol"][sym]["entry_ok"] is True
    assert prov["entry_blocked_on_data"] == []


def test_missing_perpetual_frames_block_entry_but_not_analysis(tmp_path: Path, monkeypatch):
    """Perpetual cerceve yoksa: SPOT ile SESSIZCE tamamlanmaz → giris kapali, analiz surer.

    Analizin surmesi bilinclidir: acik pozisyonun baglami, panel ve cikis degerlendirmesi
    fiyat gorunurlugu ister. Kapatilan tek sey YENI girise duyulan guvendir.
    """
    eng = TE._engine(tmp_path, monkeypatch,
                     {"entry_universe": {"enabled": True, "symbols": ["ETH/USDT", "SOL/USDT"]}},
                     symbols=["ETH/USDT", "SOL/USDT"], p_win=0.62)

    def _perp(sym, timeframes=None):
        if sym == "ETH/USDT":
            raise RuntimeError("perp feed down")     # SAGLAYICI arizasi (kod hatasi DEGIL)
        return dict(eng._fake_live._frames[sym])

    monkeypatch.setattr(eng, "perp_frames", _perp)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)

    assert "ETH/USDT" in s["symbols"], "analiz susturulmamali"
    prov = json.loads((eng.cfg.state_path / "frame_provenance.json").read_text(encoding="utf-8"))
    assert prov["by_symbol"]["ETH/USDT"]["market"] == "SPOT"
    assert prov["by_symbol"]["ETH/USDT"]["entry_ok"] is False
    assert prov["by_symbol"]["ETH/USDT"]["reason"] == "FUTURES_FRAMES_UNAVAILABLE"
    assert prov["entry_blocked_on_data"] == ["ETH/USDT"]
    assert not any(o.startswith("ETH/USDT") for o in s["opened"]), "spot cerceveyle giris acilmamali"
    blocked = [r for r in _risk_log(eng)
               if r.get("symbol") == "ETH/USDT" and r.get("block_detail") == "FUTURES_FRAMES_UNAVAILABLE"]
    assert blocked, "gerekce risk gunlugunde gorunmeli"
    assert blocked[0]["block_code"] == "DATA_INVALID"

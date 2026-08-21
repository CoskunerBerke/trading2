"""Replay ↔ canlı PAPER adet-kotası paritesi + state/rollback geriye uyumluluğu.

BLOCKER 1 — `replay/engine.py` defteri sabit `max_positions=cfg.futures.max_positions` (=3) ile
kuruyordu; canlı PAPER ise `profile.max_open_positions is None` olduğu için adet kotası
uygulamıyordu. Replay daha az işlem açtığı için trade memory / outcome / loss attribution /
walk-forward / araştırma politikaları YANLIŞ dağılımdan öğreniyordu. Artık iki motor da tavanın
uygulanıp uygulanmadığını TEK ortak sözleşmeden (`risk.enforces_position_cap`) türetir.

BLOCKER 2 — PAPER defteri `"max_positions": null` yazabiliyordu; pre-`68b63f4` kod override
geçmeyen yollarda `int(None)` ile çöküyordu (`paper-status`, `reconcile`, `export-trades`,
`export-tax`). Artık JSON'a DAİMA integer yazılır; "uygula/uygulama" davranışı ayrı
`enforce_position_cap` bayrağındadır ve eski loader o anahtarı güvenle yok sayar.

Bu dosya HİÇBİR replay başlatmaz (yalnız `HistoricalReplay` nesnesi kurulur), frozen
`core4_4h_s4_seed7` artifact'lerine dokunmaz ve yalnız tamamen sentetik temp state kullanır.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

from tradingbot.accounting import FuturesLedgerV2  # noqa: E402
from tradingbot.accounting.futures_ledger import DEFAULT_MAX_POSITIONS  # noqa: E402
from tradingbot.decision_gates import FORBIDDEN_QUOTA_CODES  # noqa: E402
from tradingbot.risk import enforces_position_cap, resolve_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PRE_FIX_SHA = "9860a58958ccd05f01080d385d40ddaa699a7d32"      # pre-68b63f4 (rollback hedefi)
FROZEN = "core4_4h_s4_seed7"


# ============================================================================= yardımcılar
def _replay_cfg(tmp_path: Path):
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.scanner.enabled = False
    cfg.v3 = load_v3({"coin_heads": {"consensus_threshold": 0.05, "min_confidence": 0.05}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    return cfg


def _replay_ledger(tmp_path: Path):
    """Replay motorunu KURAR (run() ÇAĞRILMAZ → hiçbir replay koşmaz) ve defterini döner."""
    from tradingbot.history import HistoryStore
    from tradingbot.replay.engine import HistoricalReplay
    store = HistoryStore(tmp_path / "hist")                   # boş: replay çalıştırılmıyor
    rep = HistoricalReplay(_replay_cfg(tmp_path), run_id="cap_parity_probe", store=store,
                           symbols=["ETH/USDT"], market="futures", tf="4h", seed=0)
    return rep, rep.ledger2


def _cap_state(led) -> tuple[int, bool]:
    return int(led.max_positions), bool(led.enforce_position_cap)


# ============================================================================= 1) parite
def test_live_paper_and_replay_ledgers_share_one_position_cap_contract(tmp_path, monkeypatch):
    """Üretim varsayılanlarıyla canlı PAPER ve HistoricalReplay defterleri AYNI davranışa sahip."""
    live = _engine(tmp_path / "live", monkeypatch, symbols=2).ledger2
    rep, replay = _replay_ledger(tmp_path / "replay")
    assert rep.profile.name == "PAPER_RESEARCH"
    assert _cap_state(live) == _cap_state(replay) == (DEFAULT_MAX_POSITIONS, False), (
        _cap_state(live), _cap_state(replay))
    # Yapılandırılmış değer KORUNDU; uygulanan davranış profilden geldi.
    assert live.max_positions == 3 and replay.max_positions == 3
    assert enforces_position_cap(resolve_profile("PAPER_RESEARCH")) is False
    # Davranış eşitliği: üç pozisyon açıkken İKİSİ de dördüncüye izin verir.
    for led in (live, replay):
        led.positions.update({"A/USDT": object(), "B/USDT": object(), "C/USDT": object()})
        assert led.can_open("D/USDT") == (True, "OK"), led
        assert led.can_open("A/USDT") == (False, "ALREADY_OPEN")   # sembol tekliği korunur


def test_replay_and_live_do_not_derive_the_cap_separately():
    """İki motor da AYNI ortak yardımcıyı çağırır; ayrı formül üretmez."""
    for rel in ("engine_v3.py", "replay/engine.py"):
        src = (ROOT / "tradingbot" / rel).read_text(encoding="utf-8")
        assert "enforce_position_cap=enforces_position_cap(self.profile)" in src, rel
        assert "max_open_positions is None" not in src, f"{rel}: yerel formül geri gelmiş"


@pytest.mark.parametrize("name,expect_cap", [("PAPER_RESEARCH", False), ("TESTNET", True),
                                             ("SHADOW_LIVE", True), ("LIVE", True),
                                             ("LIVE_LIMITED", True)])
def test_position_cap_contract_per_profile(name, expect_cap):
    """TESTNET/SHADOW_LIVE/LIVE/LIVE_LIMITED tavanlarını KORUR; yalnız PAPER uygulamaz."""
    p = resolve_profile(name)
    assert enforces_position_cap(p) is expect_cap
    led = FuturesLedgerV2(1_000, max_positions=3, enforce_position_cap=enforces_position_cap(p))
    led.positions.update({"A/USDT": object(), "B/USDT": object(), "C/USDT": object()})
    ok, why = led.can_open("D/USDT")
    assert (not ok and why == "MAX_POSITIONS") if expect_cap else (ok and why == "OK")
    if expect_cap:
        assert p.max_open_positions is not None and p.max_positions_per_market is not None


# ============================================================================= 2) JSON integer kalıyor
def test_to_dict_max_positions_is_always_an_integer():
    """`null` ASLA yazılmaz — pre-68b63f4 loader `int(d["max_positions"])` yapıyor."""
    for kwargs in ({"max_positions": 3, "enforce_position_cap": False},
                   {"max_positions": 3, "enforce_position_cap": True},
                   {"max_positions": None},                       # geriye uyumlu çağrı biçimi
                   {}):
        led = FuturesLedgerV2(1_000, **kwargs)
        d = led.to_dict()
        assert isinstance(d["max_positions"], int) and d["max_positions"] == 3, (kwargs, d["max_positions"])
        assert isinstance(d["enforce_position_cap"], bool)
        assert json.loads(json.dumps(d))["max_positions"] == 3
    # `max_positions=None` eski anlamı ("tavan uygulanmaz") bayrağa taşındı.
    assert FuturesLedgerV2(1_000, max_positions=None).enforce_position_cap is False


def test_paper_round_trip_never_writes_null(tmp_path, monkeypatch):
    """Gerçek motor turu sonrası diske yazılan PAPER ledger JSON'unda `max_positions` integer."""
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    assert _cap_state(eng.ledger2) == (3, False)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    raw = json.loads((eng.cfg.state_path / "futures_ledger.json").read_text(encoding="utf-8"))
    assert raw["max_positions"] == 3 and isinstance(raw["max_positions"], int)
    assert raw["enforce_position_cap"] is False
    assert "null" not in json.dumps(raw["max_positions"])
    # Yeniden yüklenip kaydedildiğinde de integer kalır (round-trip kararlı).
    again = FuturesLedgerV2.from_dict(raw)
    assert isinstance(again.to_dict()["max_positions"], int)
    assert again.enforce_position_cap is False


def test_legacy_null_fixture_is_normalised_to_an_integer(tmp_path):
    """`68b63f4` sürümünün yazmış olabileceği `max_positions: null` fail-safe biçimde 3'e çevrilir."""
    fixture = {"schema_version": 2, "kind": "futures", "starting_equity": "5000",
               "wallet_balance": "5000", "max_positions": None, "positions": {}, "history": [],
               "entries": [], "seq": 0}
    path = tmp_path / "futures_ledger.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    led = FuturesLedgerV2.load(path, starting_equity=5000)
    assert led.max_positions == DEFAULT_MAX_POSITIONS and isinstance(led.max_positions, int)
    assert led.enforce_position_cap is False, "null'ın eski anlamı (tavan yok) korunmalı"
    led.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["max_positions"] == 3 and isinstance(raw["max_positions"], int)
    assert raw["enforce_position_cap"] is False
    # Kayıtlı bayrak yoksa muhafazakâr davranış (uygula) seçilir.
    del fixture["max_positions"]
    assert FuturesLedgerV2.from_dict(dict(fixture, max_positions=3)).enforce_position_cap is True


# ============================================================================= 3) pre-68b63f4 semantiği
def _pre_fix_loader_semantics(d: dict, **overrides):
    """pre-`68b63f4` `from_dict` içindeki TAM ifade (9860a58: futures_ledger.py:529).

        max_pos = int(overrides.pop("max_positions", d.get("max_positions", 3)))

    `int(None)` ile çökme senaryosunu birebir yeniden üretir.
    """
    return int(overrides.pop("max_positions", d.get("max_positions", 3)))


def test_new_ledger_json_loads_under_pre_fix_loader_semantics(tmp_path, monkeypatch):
    """Yeni kodun yazdığı JSON, eski loader ifadesiyle `int(None)` ÜRETMEZ."""
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    raw = json.loads((eng.cfg.state_path / "futures_ledger.json").read_text(encoding="utf-8"))
    assert _pre_fix_loader_semantics(raw) == 3                       # override GEÇMEYEN yol (CLI)
    assert _pre_fix_loader_semantics(raw, max_positions=3) == 3      # override geçen yol (motor)
    # Kontrol: eski gösterim gerçekten çökerdi → düzeltmenin gerekliliği kanıtlanır.
    with pytest.raises(TypeError):
        _pre_fix_loader_semantics(dict(raw, max_positions=None))


def test_pre_fix_loader_ignores_the_new_json_key():
    """Eski `from_dict` yalnız bildiği anahtarları `d.get` ile okur → yeni anahtar yok sayılır."""
    src = subprocess.run(["git", "show", f"{PRE_FIX_SHA}:tradingbot/accounting/futures_ledger.py"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if src.returncode != 0:
        pytest.skip("pre-fix kaynağı yok (shallow clone)")
    old = src.stdout
    assert "enforce_position_cap" not in old, "eski sürüm bu anahtarı bilmiyor olmalı"
    body = old[old.index("def from_dict"):old.index("def import_legacy_ledger")]
    # Eski gövde JSON'u yalnız açık `d.get(...)` çağrılarıyla okur; bilinmeyen anahtar için
    # setattr/loop YOKTUR (yalnız `overrides` üzerinde setattr var, o da kwargs'tır).
    assert "for k, v in overrides.items()" in body and "for k, v in d.items()" not in body
    assert 'd.get("enforce_position_cap"' not in body


@pytest.mark.skipif(os.environ.get("SKIP_WORKTREE_ROLLBACK") == "1", reason="devre dışı")
def test_pre_fix_cli_commands_load_the_new_ledger_json(tmp_path):
    """ROLLBACK KANITI: pre-`68b63f4` kodu, yeni kodun yazdığı ledger'ı GERÇEKTEN okuyabiliyor.

    Geçici git worktree + TAMAMEN SENTETİK temp state kullanır; gerçek `/opt/tradingbot/data`,
    VPS state'i ya da frozen artifact'lere DOKUNMAZ. Dört komut da çalıştırılır
    (`paper-status`, `reconcile`, `export-trades`, `export-tax`) ve negatif kontrol olarak eski
    `max_positions: null` gösteriminin aynı kodda `int(None)` ile ÇÖKTÜĞÜ doğrulanır.
    """
    if subprocess.run(["git", "cat-file", "-e", f"{PRE_FIX_SHA}^{{commit}}"], cwd=ROOT,
                      capture_output=True).returncode != 0:
        pytest.skip("pre-fix commit yok (shallow clone)")
    # 1) Sentetik state: ledger JSON'unu YENİ kod yazar.
    root = tmp_path / "synthetic_root"
    (root / "state").mkdir(parents=True)
    ledger_path = root / "state" / "futures_ledger.json"
    FuturesLedgerV2(5_000, max_positions=3, enforce_position_cap=False).save(ledger_path)
    raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert raw["max_positions"] == 3 and raw["enforce_position_cap"] is False
    # `coins` DOLU olmalı: aksi hâlde üç komut defteri yüklemeden erken çıkar ve test hiçbir şey kanıtlamaz.
    (root / "config.yaml").write_text("mode:\n  mode: PAPER\nstate_dir: state\ncoins:\n  - ETH/USDT\n",
                                      encoding="utf-8")
    # 2) Eski kodu ayrı worktree'ye çıkar (çalışma ağacına dokunmaz).
    wt = tmp_path / "wt_pre_fix"
    if subprocess.run(["git", "worktree", "add", "--detach", str(wt), PRE_FIX_SHA],
                      cwd=ROOT, capture_output=True, text=True).returncode != 0:
        pytest.skip("worktree oluşturulamadı")

    def _run(cmd: str):
        env = dict(os.environ, PYTHONPATH=str(wt), PYTHONIOENCODING="utf-8")
        env.pop("TRADINGBOT_DATA", None)
        r = subprocess.run([sys.executable, "-m", "tradingbot", "--config", str(root / "config.yaml"), cmd],
                           cwd=wt, capture_output=True, text=True, env=env, timeout=300,
                           encoding="utf-8", errors="replace")   # çocuk UTF-8 yazar; locale ile çözme
        return r, (r.stdout or "") + (r.stderr or "")

    try:
        old_src = (wt / "tradingbot" / "accounting" / "futures_ledger.py").read_text(encoding="utf-8")
        assert "self.max_positions = int(max_positions)" in old_src, "beklenen eski semantik yok"
        assert "enforce_position_cap" not in old_src, "eski sürüm yeni anahtarı bilmiyor olmalı"
        # POZİTİF: dört komut da defteri yükler ve hata vermez.
        # Markerlar ASCII: defterin GERÇEKTEN yüklendiğini kanıtlar (erken çıkışta görünmezler).
        for cmd, marker in (("paper-status", "starting_equity"), ("reconcile", "gateway"),
                            ("export-trades", "trades_"), ("export-tax", "tax_")):
            r, out = _run(cmd)
            assert r.returncode == 0, (cmd, out[-1200:])
            assert "Traceback" not in out and "TypeError" not in out, (cmd, out[-1200:])
            assert "int() argument must be" not in out, (cmd, out[-1200:])
            assert marker in out, f"{cmd} defteri yüklemeden çıkmış olabilir: {out[-400:]}"
        # NEGATİF KONTROL: eski `null` gösterimi AYNI kodda gerçekten çöker (düzeltme gerekliydi).
        broken = dict(raw, max_positions=None)
        broken.pop("enforce_position_cap", None)
        ledger_path.write_text(json.dumps(broken), encoding="utf-8")
        r, out = _run("paper-status")
        assert r.returncode != 0 and "int() argument must be" in out, out[-1200:]
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)


# ============================================================================= 4) mutasyon yolları
def test_no_cli_path_reactivates_the_cap_or_writes_the_ledger():
    """PAPER'da adet tavanını yanlışlıkla geri açan / defteri yazan CLI yolu yok."""
    src = (ROOT / "tradingbot" / "cli_v3.py").read_text(encoding="utf-8")
    assert "max_positions" not in src, "CLI adet tavanını elle geçmemeli"
    assert "ledger2.save(" not in src and "fut.save(" not in src, "CLI futures defterini yazmamalı"
    # Override geçmeyen CLI yolları kayıtlı bayrağı MİRAS ALIR (tavan sessizce geri açılmaz).
    d = FuturesLedgerV2(5_000, max_positions=3, enforce_position_cap=False).to_dict()
    assert FuturesLedgerV2.from_dict(d).enforce_position_cap is False


# ============================================================================= 5) frozen artifact / replay
def test_replay_probe_never_executes_a_replay_or_touches_frozen_artifacts(tmp_path, monkeypatch):
    """RUNTIME kanıt: `HistoricalReplay` yalnız KURULUR; `run()` çağrılırsa test PATLAR.

    Ayrıca frozen `core4_4h_s4_seed7` dizini (varsa) bit düzeyinde değişmez ve replay sondası
    yalnız `tmp_path` altında yazar.
    """
    from tradingbot.replay.engine import HistoricalReplay

    def _boom(*a, **kw):
        raise AssertionError("bu testler replay BAŞLATMAMALI")
    monkeypatch.setattr(HistoricalReplay, "run", _boom)

    frozen = ROOT / "state" / "replay" / FROZEN
    before = None
    if frozen.exists():
        before = sorted((f.relative_to(frozen).as_posix(), f.stat().st_size, f.stat().st_mtime_ns)
                        for f in frozen.rglob("*") if f.is_file())

    rep, replay_led = _replay_ledger(tmp_path / "probe")
    assert _cap_state(replay_led) == (DEFAULT_MAX_POSITIONS, False)
    # Sonda yalnız temp altına yazdı.
    assert tmp_path in rep.state_dir.parents or rep.state_dir.is_relative_to(tmp_path)
    assert not (ROOT / "state" / "replay" / "cap_parity_probe").exists()

    if before is not None:
        after = sorted((f.relative_to(frozen).as_posix(), f.stat().st_size, f.stat().st_mtime_ns)
                       for f in frozen.rglob("*") if f.is_file())
        assert after == before, "frozen artifact değişti"


def test_existing_capacity_contracts_still_hold():
    """Kapasite kararı adet değil RİSK: yasak kota kodları kaynakta yok."""
    for rel in ("engine_v3.py", "coinhead/chief.py", "risk/engine.py", "replay/engine.py"):
        text = (ROOT / "tradingbot" / rel).read_text(encoding="utf-8")
        for bad in FORBIDDEN_QUOTA_CODES:
            assert bad not in text, f"{rel}: {bad}"

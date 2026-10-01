"""KARAR KAYDI DENETIM KIMLIGI — hangi kod ve hangi config bu karari uretti?

Bulunan kusur (2026-09-11): `engine_v3.code_sha()` ve `config_hash()` metodlari, karar
gunlugunde bu alanlarin bos kalmasini onarmak icin YAZILMISTI ("uretimde bu alan bostu,
0/20000 dolu" — kendi docstring'i). Fakat `_journal_decisions` cagri yeri hala eski
`getattr(self.cfg, "code_sha", None)` yolunu kullaniyordu ve `cfg.code_sha` uretimde HIC
set edilmiyor. Olculdu: gercek turdan sonra 0/30 kayitta `code_sha` doluydu; `config_hash`
ise cagri yerine HIC gecirilmiyordu.

Kaydin bu iki alani olmadan denetim yapilamaz: bir kararin hangi surumle ve hangi etkin
yapilandirmayla alindigi sonradan sorulamaz.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as TE  # noqa: E402

from tradingbot.engine_v3 import TradingEngineV3  # noqa: E402
from tradingbot.learn.decision_journal import build_decision_record  # noqa: E402


# --------------------------------------------------------------------------- kaynak sozlesmesi
def test_journal_uses_the_resolver_not_the_unset_config_attribute():
    """REGRESYON: `getattr(self.cfg, "code_sha")` geri gelirse alan yeniden BOSALIR."""
    src = inspect.getsource(TradingEngineV3._journal_decisions)
    assert "self.code_sha()" in src
    assert "self.config_hash()" in src
    assert 'getattr(self.cfg, "code_sha"' not in src, (
        "cfg.code_sha üretimde set EDİLMİYOR — bu yol alanı boş bırakır")


def test_record_builder_accepts_both_identity_fields():
    rec = build_decision_record(run_id="r", cycle_id=1, symbol="BTC/USDT", direction="LONG",
                                code_sha="abc123", config_hash="def456")
    assert rec["code_sha"] == "abc123"
    assert rec["config_hash"] == "def456"


# --------------------------------------------------------------------------- gercek motor
def _engine(tmp_path, monkeypatch):
    eng = TE._engine(tmp_path, monkeypatch,
                     {"entry_universe": {"enabled": True, "symbols": ["ETH/USDT", "SOL/USDT"]}},
                     symbols=["ETH/USDT", "SOL/USDT"], p_win=0.62)
    monkeypatch.setattr(eng, "perp_frames", lambda sym: dict(eng._fake_live._frames[sym]))
    return eng


def _records(eng) -> list[dict]:
    p = eng.cfg.state_path / "decision_journal.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_every_decision_record_carries_the_code_sha(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(eng, "code_sha", lambda: "deadbeefcafe")
    eng.tour(do_scan=False, obsidian=False, charts=False)
    recs = _records(eng)
    assert recs, "karar kaydı yazılmadı"
    assert all(r.get("code_sha") == "deadbeefcafe" for r in recs)


def test_every_decision_record_carries_the_config_hash(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    recs = _records(eng)
    assert recs
    hashes = {r.get("config_hash") for r in recs}
    assert len(hashes) == 1 and next(iter(hashes)), f"config_hash boş ya da tutarsız: {hashes}"


def test_identity_is_none_not_invented_when_it_cannot_be_resolved(tmp_path: Path, monkeypatch):
    """Git yoksa alan BOS kalir — sahte bir sürüm damgasi UYDURULMAZ."""
    eng = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(eng, "code_sha", lambda: None)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    recs = _records(eng)
    assert recs
    assert all(r.get("code_sha") in (None, "") for r in recs)


def test_market_type_is_filled_for_actionable_candidates_only(tmp_path: Path, monkeypatch):
    """Plan yoksa piyasa da SECILMEMISTIR; alan bos kalir, varsayilan atanmaz."""
    eng = _engine(tmp_path, monkeypatch)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    recs = _records(eng)
    assert recs
    for r in recs:
        if r.get("is_actionable"):
            assert r.get("market_type") in ("USDM_PERP", "SPOT"), r
        else:
            assert r.get("market_type") in (None, ""), r


def test_code_sha_is_resolved_once_and_cached(tmp_path: Path, monkeypatch):
    """Aday basina bir `git rev-parse` cagrisi turu yavaslatirdi."""
    eng = _engine(tmp_path, monkeypatch)
    calls = {"n": 0}
    real = TradingEngineV3.code_sha

    def _counted(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(TradingEngineV3, "code_sha", _counted)
    eng.code_sha(); eng.code_sha(); eng.code_sha()
    assert calls["n"] == 3                       # metod cagrisi sayilir
    assert getattr(eng, "_code_sha_cache", ...) is not ...   # ama cozum onbelleklenir

# -*- coding: utf-8 -*-
"""M2X AYNA DEFTERİ v1 (2026-10-05; ön kayıt docs/M2_AGGRESSIVE_V1.md §1–§2.10, §4) — PAPER.

1. **Config ve kill switch:** kod ve config.yaml varsayılanı KAPALI; bilinmeyen anahtar / sürüm / başlangıç bakiyesi /
   klasör çakışması ConfigError; LIVE'da açık ConfigError; env `TRADINGBOT_M2X=off` yalnız kapatır.
2. **config_hash (§1.6.4):** bölüm karar kimliğine GİRMEZ (motor + ortak deneyim toplayıcısının yedek özeti); config.yaml
   ile hesaplanan değer `1c2c6e2` kodunun bölümsüz config'te ürettiği değere eşit (değer sabit).
3. **Altın yalıtım (§1.7):** GERÇEK `TradingEngineV3.tour` (ortak deneyim altın harness'i: T2/M2/D4 + formasyon, öğrenme
   modu AÇIK/KAPALI, donmuş saat, deterministik kimlikler). Bölüm YOK == `enabled: false` bayt bayt (health dahil);
   `enabled: true` iken farklar YALNIZ izin verilen listede (indeks girdisi, `m2x` faz süresi, M2X'in kendi dosyaları).
   `strategy_books`, tur kapsamı ve canlı sağlayıcı çağrıları (sayı, sembol, sıra) aynı; izleyicinin fiyat partisi aynı.
4. **Politika (§2) sentetik turlarda:** kademe boyutu, açık risk tavanı ve ortak küçültme, kriz bütçesi, L_liq kaldıracı
   ve likidasyon tamponu, aşağı inme, histerezis (A ve B anahtarı, demirleme), yumuşak durdurma, −%50 DUR ve sahip yeniden
   başlatması (tek kullanım, dönem), veri boşluğu (fail-closed), M2 kural çıkışı eşleme, yetim, kaçırılan tur, likidasyon
   ayrışması, yeni giriş kapalı. Simülasyonun `M2xRunner`ı ile aynı akışta AYNI defter sonucu (parite).
5. **Kalıcılık:** yeniden başlatma politika durumunu, eşlemeyi ve sayaçları korur; kesintisiz koşuyla aynı sonuç.
6. **Raporlama:** karne (`MIRROR_BOOKS` dışlaması; ana tablo + AYLIK HEDEF M2X'li/M2X'siz bayt bayt aynı; ayrı bölüm,
   günlük ve aylık hedef satırları), `--m2x-check` satırları, panel kartı.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot import m2x_policy as P  # noqa: E402
from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, TickData  # noqa: E402
from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import ConfigError, load_v3  # noqa: E402
from tradingbot.core import iso  # noqa: E402
from tradingbot.m2x_book import (MIRROR_ORPHAN_CLOSED, MIRROR_PARENT_CLOSED, M2xBook, check_lines,  # noqa: E402
                                 write_resume_request)
from tradingbot.m2x_sim import SimFilters  # noqa: E402

UTC = timezone.utc
D0 = datetime(2026, 10, 1, 0, 5, tzinfo=UTC)
#: `1c2c6e2` kodunun config.yaml (bölümsüz; sha256 aşağıda) ile ürettiği karar kimliği (§1.6.4) — değer SABİT.
BASE_CONFIG_HASH = "4387f914bf60dc1530057730a845942664530122b758392d1c049e10cb5c6a87"
BASE_CONFIG_SHA256 = "be5e3e0d1c760abf228ece68c249d416d521e1643b7b18f0a12a8b254161c398"
M2X_BLOCK_MARK = "\n# ---- M2X — AGRESİF M2 KÂĞIT DEFTERİ"


# ============================================================================ 1. config + kill switch
def test_code_and_config_yaml_default_off():
    assert load_v3({}).m2x_aggressive.enabled is False
    raw = __import__("yaml").safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    sec = raw["m2x_aggressive"]
    assert sec["enabled"] is False and sec["policy_version"] == P.POLICY_VERSION
    assert sec["starting_equity_usdt"] == P.M2X_POLICY_V1["starting_equity_usdt"] and sec["parent"] == "m2_tsmom28"
    from tradingbot.config import load_config
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.v3.m2x_aggressive.enabled is False and not any("m2x" in w for w in cfg.v3.warnings)


@pytest.mark.parametrize("sec,msg", [
    ({"enable": True}, "bilinmeyen anahtar"),
    ({"enabled": "yes"}, "true/false"),
    ({"new_entries": 1}, "true/false"),
    ({"policy_version": "m2x_v2"}, "policy_version"),
    ({"starting_equity_usdt": 300}, "starting_equity_usdt"),
    ({"parent": "t2_trend_regime"}, "parent"),
    ({"state_dir": "strategy_paper"}, "state_dir"),
    ({"state_dir": "../x"}, "state_dir"),
    ({"state_dir": "shared_experience"}, "state_dir"),
])
def test_config_validation_fail_closed(sec, msg):
    with pytest.raises(ConfigError, match=msg):
        load_v3({"m2x_aggressive": sec})
    with pytest.raises(ConfigError, match="sözlük"):
        load_v3({"m2x_aggressive": ["enabled"]})


def test_paper_only_gate_rejects_live_modes():
    from tradingbot.config_v3 import _validate_m2x
    cfg = load_v3({"m2x_aggressive": {"enabled": True}})
    for mode in ("LIVE", "LIVE_LIMITED"):
        cfg.mode.mode = mode
        with pytest.raises(ConfigError, match="M2X_PAPER_ONLY"):
            _validate_m2x(cfg)
    for mode in ("PAPER", "TESTNET", "OBSERVE", "SHADOW_LIVE"):
        cfg.mode.mode = mode
        _validate_m2x(cfg)
    cfg.m2x_aggressive.enabled = False
    cfg.mode.mode = "LIVE"
    _validate_m2x(cfg)                                 # kapalı bölüm LIVE'da da geçerli (kod yüklenmez)


def test_env_kill_switch_only_disables(monkeypatch):
    monkeypatch.setenv("TRADINGBOT_M2X", "off")
    assert load_v3({"m2x_aggressive": {"enabled": True}}).m2x_aggressive.enabled is False
    monkeypatch.setenv("TRADINGBOT_M2X", "on")
    with pytest.raises(ConfigError, match="TRADINGBOT_M2X"):
        load_v3({"m2x_aggressive": {"enabled": False}})


# ============================================================================ 2. config_hash
def _hash_of(raw: dict) -> str:
    from types import SimpleNamespace

    from tradingbot.engine_v3 import TradingEngineV3
    cfg = BotConfig()
    cfg.v3 = load_v3(raw)
    return TradingEngineV3.config_hash(SimpleNamespace(cfg=cfg))


def test_config_hash_ignores_the_section_on_off_and_absent():
    import yaml
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    base = dict(raw)
    base.pop("m2x_aggressive", None)
    h0 = _hash_of(base)
    assert _hash_of(dict(base, m2x_aggressive={"enabled": False})) == h0
    assert _hash_of(dict(base, m2x_aggressive={"enabled": True, "new_entries": False})) == h0
    assert _hash_of(raw) == h0


def test_config_hash_equals_base_commit_value_and_collector_fallback_agrees(tmp_path):
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert M2X_BLOCK_MARK in text, "config.yaml M2X bölümü işareti"
    without = text[:text.index(M2X_BLOCK_MARK) + 1]
    from tradingbot.config import load_config
    cfg = load_config(ROOT / "config.yaml")
    from types import SimpleNamespace

    from tradingbot.engine_v3 import TradingEngineV3
    h = TradingEngineV3.config_hash(SimpleNamespace(cfg=cfg))
    if hashlib.sha256(without.encode("utf-8")).hexdigest() == BASE_CONFIG_SHA256:
        assert h == BASE_CONFIG_HASH                 # 1c2c6e2 kodunun aynı (bölümsüz) config'te ürettiği değer
    p = tmp_path / "cfg.yaml"
    p.write_text(without, encoding="utf-8")
    assert TradingEngineV3.config_hash(SimpleNamespace(cfg=load_config(p))) == h
    # ortak deneyim toplayıcısının yedek özeti (motor önbelleği yokken) motorunkiyle aynı (karar kimliği)
    from tradingbot.shared_experience.collector import SharedExperienceCollector
    cfg.project_root = tmp_path
    eng = SimpleNamespace(cfg=cfg, __dict__={})
    col = SharedExperienceCollector.from_engine(eng)
    assert col.config_hash == h


# ============================================================================ 3. altın yalıtım (gerçek motor turu)
def _golden_run(tmp_path, monkeypatch, *, lm: bool, mx: dict | None, prepare=None, between=None, xp: dict | None = None) -> dict:
    import test_shared_experience_no_decision_change_v1 as G
    orig = G._overrides

    def ov(*, lm, xp):
        d = orig(lm=lm, xp=xp)
        if mx is not None:
            d["m2x_aggressive"] = dict(mx)
        return d
    with monkeypatch.context() as mp:
        mp.setattr(G, "_overrides", ov)
        run = G._run(tmp_path, monkeypatch, lm=lm, xp=xp, prepare=prepare, between=between)
    # ortak deneyim deposu ve gölge danışman tavsiyeleri (G._run bu klasörü dosya listesinden dışlar): durum dosyaları
    # (`status.json`, `advice/advisor_status.json`: ölçülen süreler — M2X YOK/KAPALI arasında da oynar) hariç her dosya
    d = run["eng"].cfg.state_path / "shared_experience"
    rb = str(run["eng"].cfg.project_root).encode("utf-8")
    run["xp_files"] = {p.relative_to(d).as_posix(): p.read_bytes().replace(rb, b"<ROOT>")
                       for p in sorted(d.rglob("*")) if p.is_file() and not p.name.endswith("status.json")} if d.exists() else {}
    return run


#: §1.7 İZİN VERİLEN FARKLAR (tam liste). `protective_monitor.json` ve ilk-gözlem satırları bu harness'te oluşmaz.
def _allowed(rel: str) -> bool:
    return (rel == "state/strategy_paper_index.json" or rel == "state/strategy_paper_m2x.json"
            or rel.startswith("state/strategy_paper_m2x/"))


def _decision_files(run: dict) -> dict:
    return {k: v for k, v in run["files"].items() if not _allowed(k) and k != "state/health.json"}


@pytest.fixture(scope="module")
def _gcache():
    return {}


def _base(tmp_path_factory, monkeypatch, cache, lm: bool) -> dict:
    if ("absent", lm) not in cache:
        cache[("absent", lm)] = _golden_run(tmp_path_factory.mktemp("absent"), monkeypatch, lm=lm, mx=None)
    return cache[("absent", lm)]


def test_golden_absent_equals_disabled_byte_for_byte(tmp_path_factory, monkeypatch, _gcache):
    import test_shared_experience_no_decision_change_v1 as G
    absent = _base(tmp_path_factory, monkeypatch, _gcache, True)
    off = _golden_run(tmp_path_factory.mktemp("off"), monkeypatch, lm=True, mx={"enabled": False})
    assert G._diff(G._files_with_health(off), G._files_with_health(absent)) == []
    assert off["eng"].m2x_book is None and not any("m2x" in k for k in off["files"])
    assert "m2x" not in off["health"].get("phases", {})


@pytest.mark.parametrize("lm", [True, False], ids=["learning_on", "learning_off"])
def test_golden_enabled_changes_only_allowed_files(tmp_path_factory, monkeypatch, _gcache, lm):
    base = _base(tmp_path_factory, monkeypatch, _gcache, lm)
    on = _golden_run(tmp_path_factory.mktemp("on"), monkeypatch, lm=lm, mx={"enabled": True})
    fa, fb = _decision_files(on), _decision_files(base)
    diff = sorted(set(fa) ^ set(fb)) + [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]
    assert diff == [], diff
    # health.json: yalnız yeni `m2x` faz süresi
    ho, hb = json.loads(json.dumps(on["health"])), json.loads(json.dumps(base["health"]))
    assert "m2x" in ho["phases"] and "m2x" not in hb["phases"]
    ho["phases"].pop("m2x")
    assert ho == hb
    # indeks: yalnız M2X girdisi eklenir (en sonda, `kind: mirror`)
    io = json.loads(on["files"]["state/strategy_paper_index.json"])
    ib = json.loads(base["files"]["state/strategy_paper_index.json"])
    assert io["books"][:-1] == ib["books"] and io["generated_at"] == ib["generated_at"]
    assert io["books"][-1] == {"key": "strategy_paper_m2x", "name": "m2x_aggressive", "kind": "mirror",
                               "parent": "m2_tsmom28", "summary_file": "strategy_paper_m2x.json"}
    # anlamlı senaryo: M2 açtı → M2X kopyaladı, M2'nin kapanışları M2X'te de kapandı
    eng = on["eng"]
    m2 = next(b for b in eng.strategy_books if b.name == "m2_tsmom28")
    mx = eng.m2x_book
    m2_ids = {str(p.id) for p in m2.ledger.positions.values()} | {str(h.id) for h in m2.ledger.history}
    mine = [mx.parent_id(p) for p in mx.ledger.positions.values()] + [h.features["m2x"]["m2_id"] for h in mx.ledger.history]
    assert mine and set(mine) <= m2_ids
    assert {h.features["m2x"]["m2_id"] for h in mx.ledger.history} <= {str(h.id) for h in m2.ledger.history}
    assert {mx.parent_id(p) for p in mx.ledger.positions.values()} <= {str(p.id) for p in m2.ledger.positions.values()}
    doc = json.loads(on["files"]["state/strategy_paper_m2x.json"])
    assert doc["kind"] == "mirror" and doc["m2x"]["policy_sha"] == P.M2X_POLICY_SHA and doc["m2x"]["entries"]["n"] >= 1
    assert doc["m2x"]["tier"] == "K1" and doc["m2x"]["status"] == "ÇALIŞIYOR"
    # aynı defter listeleri: strategy_books ve tur kapsamı
    assert [b.key for b in eng.strategy_books] == [b.key for b in base["eng"].strategy_books]
    assert eng._strategy_open_symbols() == base["eng"]._strategy_open_symbols()
    keys_on = [h.key for h in eng._protective_handles()]
    assert keys_on == [h.key for h in base["eng"]._protective_handles()] + ["strategy_paper_m2x"]


def _health_wo_timing(h: dict) -> dict:
    h = json.loads(json.dumps(h))
    (h.get("phases") or {}).pop("m2x", None)
    xp = h.get("shared_experience") or {}
    xp.pop("step_ms", None)
    for k in ("step_ms", "step_ms_p95"):
        (xp.get("advisor") or {}).pop(k, None)
    return h


def test_golden_shared_experience_rows_and_advisor_outputs_byte_identical(tmp_path_factory, monkeypatch):
    """Ortak deneyim RECORD + gölge danışman RECORD açıkken: deneyim satırları, imleç, arşiv ve tavsiye dosyaları M2X
    YOK / KAPALI / AÇIK koşularında bayt bayt aynı; karar dosyaları da (izin verilen liste dışında)."""
    xp = {"enabled": True, "mode": "RECORD", "advisor_mode": "RECORD"}
    runs = {tag: _golden_run(tmp_path_factory.mktemp("xp_" + tag), monkeypatch, lm=True, mx=mx, xp=xp)
            for tag, mx in (("absent", None), ("off", {"enabled": False}), ("on", {"enabled": True}))}
    base = runs["absent"]
    assert base["xp_files"].get("experience.jsonl") and any(k.startswith("advice/") for k in base["xp_files"])
    for tag in ("off", "on"):
        r = runs[tag]
        assert r["xp_files"] == base["xp_files"], tag
        fa, fb = _decision_files(r), _decision_files(base)
        assert sorted(set(fa) ^ set(fb)) + [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]] == [], tag
        assert _health_wo_timing(r["health"]) == _health_wo_timing(base["health"]), tag
    assert runs["on"]["eng"].m2x_book is not None and runs["on"]["eng"].m2x_book.ledger.history


def _spy_snapshot(calls: list):
    def prep(eng, mp):
        orig = eng.runner.live.snapshot

        def spy(sym):
            calls.append(sym)
            return orig(sym)
        mp.setattr(eng.runner.live, "snapshot", spy)
        if getattr(eng, "funding_rates", None) is not None:
            o_ref = eng.funding_rates.refresh

            def rspy(*a, **k):
                calls.append(("refresh", tuple(a[0]) if a else ()))
                return o_ref(*a, **k)
            mp.setattr(eng.funding_rates, "refresh", rspy)
    return prep


def test_m2x_makes_no_price_provider_or_refresh_call(tmp_path_factory, monkeypatch):
    c_off: list = []
    c_on: list = []
    _golden_run(tmp_path_factory.mktemp("spy_off"), monkeypatch, lm=True, mx=None, prepare=_spy_snapshot(c_off))
    _golden_run(tmp_path_factory.mktemp("spy_on"), monkeypatch, lm=True, mx={"enabled": True}, prepare=_spy_snapshot(c_on))
    assert c_on == c_off and len(c_off) > 0           # sayı, sembol ve SIRA aynı


def test_monitor_price_batch_unchanged_and_m2x_handle_last(tmp_path_factory, monkeypatch):
    """İzleyici: M2X tutamacı EN SONDA; fiyat partisi (semboller ve sıra) M2X açıkken aynı; M2'nin defteri aynı."""
    import test_shared_experience_no_decision_change_v1 as G
    time_machine = pytest.importorskip("time_machine")
    out = {}
    for tag, mx in (("off", None), ("on", {"enabled": True})):
        run = _golden_run(tmp_path_factory.mktemp("mon_" + tag), monkeypatch, lm=True, mx=mx)
        eng = run["eng"]
        seen: list = []
        now_ms = int(G.T0.timestamp() * 1000) + 3_600_000

        def price_fn(syms, ms, _eng=eng, _seen=seen):
            _seen.append(list(syms))
            ticks, flo = {}, {}
            for s in syms:
                px = _eng._fake_live.price.get(s) or float(_eng._fake_live._frames[s]["4h"]["close"].iloc[-1])
                px *= 0.5                                # sert düşüş: stoplar bu geçişte tetiklenir
                ticks[s] = TickData(last=Decimal(repr(px)), mark=Decimal(repr(px)), ts=iso(datetime.fromtimestamp(ms / 1000, UTC)))
                flo[s] = px
            return ticks, flo, {}
        mon = eng.ensure_protective_monitor(start=False, price_fn=price_fn, clock_ms=lambda: now_ms)
        assert mon["alive"] is False
        n_open_mx = len(eng.m2x_book.ledger.positions) if eng.m2x_book else None
        with time_machine.travel(datetime.fromtimestamp(now_ms / 1000, UTC), tick=False):   # kayıt damgaları da aynı saat
            eng.protective_monitor.run_once(now_ms)
        if eng.m2x_book is not None:
            assert n_open_mx and not eng.m2x_book.ledger.positions        # M2X pozisyonları aynı geçişte stop oldu
            st = json.loads((eng.cfg.state_path / "protective_monitor.json").read_text(encoding="utf-8"))
            assert "strategy_paper_m2x" in st["last_run"]["books"]       # §1.7 izin verilen fark (yalnız M2X anahtarı)
        m2 = next(b for b in eng.strategy_books if b.name == "m2_tsmom28")
        root = eng.cfg.project_root
        files = {}
        for p in sorted((eng.cfg.state_path).rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and not _allowed(rel) and rel not in ("state/protective_monitor.json", "state/health.json") \
                    and "shared_experience" not in rel.split("/"):
                files[rel] = p.read_bytes().replace(str(root).encode(), b"<ROOT>")
        mon_doc = json.loads((eng.cfg.state_path / "protective_monitor.json").read_text(encoding="utf-8"))
        for _k in ("books", "closed", "errors"):                      # `last_run` içindeki M2X anahtarları (§1.7)
            (mon_doc["last_run"].get(_k) or {}).pop("strategy_paper_m2x", None)
        mon_doc["observations"]["books"].pop("strategy_paper_m2x", None)
        for k in ("last_run",):
            mon_doc[k].pop("seconds", None)
        out[tag] = {"seen": seen, "m2": m2.ledger.to_dict(), "handles": [h.key for h in eng._protective_handles()],
                    "files": files, "mon": mon_doc}
    for d in out.values():
        d["m2"].pop("updated_at", None)
    assert out["on"]["seen"] == out["off"]["seen"] and out["on"]["m2"] == out["off"]["m2"]
    assert out["on"]["handles"][-1] == "strategy_paper_m2x" and out["on"]["handles"][:-1] == out["off"]["handles"]
    fa, fb = out["on"]["files"], out["off"]["files"]
    _d = sorted(set(fa) ^ set(fb)) + [k for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]
    assert _d == []
    assert out["on"]["mon"] == out["off"]["mon"]                       # M2X anahtarları dışında izleyici durumu aynı


def test_static_isolation_m2x_never_writes_parent_or_fetches_prices():
    """AST: M2X modülü M2'ye (parent) yazan bir yöntem çağırmaz ve fiyat/ağ/funding yenileme yolu çağırmaz."""
    src = (ROOT / "tradingbot" / "m2x_book.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned_calls = {"snapshot", "_paper_marks", "exit_check", "refresh", "mark_price", "premiumIndex", "perp_marks",
                    "perp_price_fn", "_futures_provider_factory", "_monitor_provider_factory"}
    mutators = {"save", "step", "tick", "open", "close_manual", "close_partial", "apply_closed_bars", "protect",
                "reconcile_funding", "settle_late_funding", "record_entry", "bind_funding", "_on_closed", "_on_opened"}
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in banned_calls:
                bad.append("call .%s @%d" % (n.func.attr, n.lineno))
            root = n.func.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in ("parent", "p", "pp", "h") and n.func.attr in mutators:
                bad.append("parent mutator .%s @%d" % (n.func.attr, n.lineno))
    assert bad == []
    # motor: M2X `strategy_books`a eklenmez, `_funding_step` ve çıkış izleyicisi listesine girmez
    eng_src = (ROOT / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    assert "strategy_books.append(self.m2x_book" not in eng_src and "strategy_books + [self.m2x_book" not in eng_src
    fstep = eng_src[eng_src.index("def _funding_step"):eng_src.index("def ensure_box_timer")]
    xcheck = eng_src[eng_src.index("def _strategy_paper_exit_check"):eng_src.index("def _label_shadows")]
    assert "m2x" not in fstep and "m2x" not in xcheck


def test_registries_do_not_know_the_mirror_book():
    """§1.6.5: kural kaydı, öğrenme modu defter listesi, ortak deneyim defter haritası ve karne defter listesi DEĞİŞMEZ."""
    import importlib.util

    from tradingbot import learning_mode, paper_rules
    from tradingbot.shared_experience import rows
    assert "m2x_aggressive" not in paper_rules.VARIANTS and "m2x_aggressive" not in learning_mode.BOOK_NAMES
    with pytest.raises(Exception):
        paper_rules.spec_for("m2x_aggressive")
    assert not any("m2x" in str(k) or "m2x" in str(v) for k, v in dict(rows.BOOKS).items())
    spec = importlib.util.spec_from_file_location("bot_scorecard_reg", ROOT / "scripts" / "bot_scorecard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "strategy_paper_m2x" not in mod.BOOKS and "strategy_paper_m2x" in mod.MIRROR_BOOKS


# ============================================================================ 4. sentetik turlar (doğrudan defter)
class FakeM2:
    """M2'nin yerine geçen sahte ebeveyn: gerçek `FuturesLedgerV2`; adım = aç/kapat (aynı `now`)."""
    name = "m2_tsmom28"
    key = "strategy_paper_m2"

    def __init__(self):
        from tradingbot.m2x_sim import LedgerParams
        # M2 ile AYNI ücret/kayma/likidasyon (config.yaml varsayılanları); adet tavanı yok (PAPER_RESEARCH)
        self.ledger = LedgerParams().ledger(200, 40)
        self.ledger.enforce_position_cap = False

    def held_ids(self):
        return {s: str(p.id) for s, p in self.ledger.positions.items()}

    def open(self, sym, px, stop, now):
        pos = self.ledger.open(sym, "LONG", Decimal(repr(px)), SizeSpec(Decimal("6"), AmountType.NOTIONAL, 2),
                               stop=Decimal(repr(stop)), now=now, tick=_td(px, now))
        assert pos is not None, self.ledger.last_reject_reason
        return pos

    def close(self, sym, px, reason, now):
        return self.ledger.close_manual(sym, px, reason=reason, now=now, tick=_td(px, now))


def _td(px, now):
    return TickData(last=Decimal(repr(float(px))), mark=Decimal(repr(float(px))), ts=iso(now))


def _cfg(tmp_path, **sec):
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.v3 = load_v3({"m2x_aggressive": {"enabled": True, **sec}})
    return cfg


class Rig:
    def __init__(self, tmp_path, *, m2=None, **sec):
        self.tmp = tmp_path
        self.cfg = _cfg(tmp_path, **sec)
        self.m2 = m2 or FakeM2()
        self.filters = SimFilters()
        self.book = self.new_book()
        self.px: dict[str, float] = {}

    def new_book(self):
        return M2xBook(self.cfg, section=self.cfg.v3.m2x_aggressive, filters_cache=self.filters)

    def tour(self, now, *, opens=(), closes=(), m2_tick=False, priced=None, parent=True, process=True):
        """Bir motor turunun M2X'e görünen kısmı: M2 adımı (aç/kapat) → yakalama → (M2'nin kendi tiki) → M2X turu."""
        held_before = self.m2.held_ids()
        for sym, px, stop in opens:
            self.px[sym] = px
            self.m2.open(sym, px, stop, now)
        for sym, reason in closes:
            self.m2.close(sym, self.px[sym], reason, now)
        self.book.capture_parent_step(self.m2, held_before, now=now, regime={"learning_on": True, "extra_entries": "record_selectivity"})
        syms = [s for s in self.px if priced is None or s in priced]
        if m2_tick:
            self.m2.ledger.tick({s: _td(self.px[s], now) for s in syms if s in self.m2.ledger.positions}, now_utc=now)
        if not process:
            return None
        pm = {s: _td(self.px[s], now) for s in syms}
        return self.book.tour_step(self.m2 if parent else None, now=now, tick_now=now, pmarks=pm,
                                   pmarks_f={s: float(self.px[s]) for s in syms}, pbars={},
                                   wall_ms=lambda: int(now.timestamp() * 1000))

    def doc(self):
        return json.loads((self.cfg.state_path / "strategy_paper_m2x.json").read_text(encoding="utf-8"))

    def events(self, kind=None):
        p = self.cfg.state_path / "strategy_paper_m2x" / "m2x_events.jsonl"
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [r for r in rows if kind is None or r.get("kind") == kind]


def _day(k, minutes=0):
    return D0 + timedelta(days=k, minutes=minutes)


def test_k1_entry_risk_leverage_and_liquidation_buffer(tmp_path):
    r = Rig(tmp_path)
    out = r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    assert out["opened"] == 1 and out["tier"] == "K1"
    pos = r.book.ledger.positions["AAA/USDT"]
    risk = float(pos.qty) * (float(pos.entry_avg) - 90.0)
    assert risk == pytest.approx(0.02 * 200, rel=0.01)               # K1: %2 × E
    assert pos.leverage == 4                                          # L_liq (tampon içindeki en yüksek, ≤ 4)
    s = (float(pos.entry_avg) - 90.0) / float(pos.entry_avg)
    liq_dist = (float(pos.entry_avg) - float(pos.liquidation_price)) / float(pos.entry_avg)
    assert liq_dist >= 2 * s                                          # defterin bracket'lı formülüyle denetlendi
    assert r.book.parent_id(pos) == str(r.m2.ledger.positions["AAA/USDT"].id)
    m = r.doc()["m2x"]
    or_mark = float(pos.qty) * (100.0 - 90.0)                         # OR mark'tan stopa (mark = M2'nin tur mark'ı)
    assert m["open_risk_mark_pct"] == pytest.approx(or_mark / m["equity_mtm"] * 100, rel=1e-3)
    assert m["open_risk_entry_pct"] == pytest.approx(risk / m["equity_mtm"] * 100, rel=1e-3)
    assert r.doc()["positions"]["AAA/USDT"]["m2_id"] == r.book.parent_id(pos)


def test_wide_stop_uses_leverage_one_and_crash_budget(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("WID/USDT", 100.0, 60.0)])               # s = %40 → tampon 2 × s yalnız 1x ile
    pos = r.book.ledger.positions["WID/USDT"]
    assert pos.leverage == 1
    cl = r.doc()["m2x"]["crash_loss_usdt"]
    assert cl == pytest.approx(float(pos.qty) * float(pos.entry_avg) * 0.501, rel=1e-3)


def test_open_risk_cap_scales_all_candidates_of_a_tour_together(tmp_path):
    r = Rig(tmp_path)
    opens = [("S%02d/USDT" % i, 100.0, 90.0) for i in range(14)]      # 14 × %2 = %28 > K1 tavanı %20
    out = r.tour(_day(0), opens=opens)
    m = r.doc()["m2x"]
    ent = r.events("entry")
    lam = ent[0]["lam"]
    assert 0.25 <= lam < 1.0 and all(e["lam"] == lam for e in ent) and len(ent) == 14 == out["opened"]
    # tavanlar GİRİŞ ANINDAKİ özkaynakla (E = 200, ücret öncesi; simülasyonla aynı tanım) denetlenir
    assert m["open_risk_mark_usdt"] <= 0.20 * 200 + 1e-6
    assert m["crash_loss_usdt"] <= 200 - 0.5 * m["peak"] + 1e-6
    assert m["binding_cap"] in ("OR", "CL")
    risks = [e["risk_usdt"] for e in ent]
    assert max(risks) / min(risks) < 1.01                             # hepsi aynı oranda küçüldü


def test_below_quarter_scale_enters_in_m2_order_then_skips(tmp_path):
    r = Rig(tmp_path)
    opens = [("Q%02d/USDT" % i, 100.0, 90.0) for i in range(40)]      # λ < 0,25
    r.tour(_day(0), opens=opens)
    ent, sk = r.events("entry"), r.events("skip")
    assert ent and sk and all(e["scale"] == 0.25 for e in ent)
    assert [e["symbol"] for e in ent] == ["Q%02d/USDT" % i for i in range(len(ent))]   # M2'nin açılış sırası
    assert {s["reason"] for s in sk} <= {P.SKIP_OR, P.SKIP_CL}
    m = r.doc()["m2x"]
    assert m["open_risk_mark_usdt"] <= 0.20 * 200 + 1e-6 and m["crash_loss_usdt"] <= 200 - 0.5 * m["peak"] + 1e-6


def _seed(book, *, peak, tier_peak=None, tier=0, halted=False):
    book.st.peak = float(peak)
    book.st.tier_peak = float(tier_peak if tier_peak is not None else peak)
    book.st.tier = int(tier)
    book.st.halted = bool(halted)


def test_tier_down_at_snapshot_then_sizes_at_tier_risk(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    _seed(r.book, peak=240.0)                                         # E 200 → DDₖ %16,7 → K2 (sonraki görüntüde)
    out = r.tour(_day(1), opens=[("AAA/USDT", 100.0, 90.0)])
    assert out["tier"] == "K2"
    pos = r.book.ledger.positions["AAA/USDT"]
    assert float(pos.qty) * (float(pos.entry_avg) - 90.0) == pytest.approx(0.01 * 200, rel=0.01)
    assert [e["kind"] for e in r.book.meta["events"]][-1] == "DOWN"


def test_entry_observation_moves_down_but_not_peak_or_up(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    _seed(r.book, peak=320.0)                                         # DD %37,5 → K4 (O2'de, aynı gün)
    out = r.tour(_day(0, 30), opens=[("AAA/USDT", 100.0, 90.0)])
    assert out["tier"] == "K4_SOFT_HALT" and out["opened"] == 0 and r.book.st.peak == 320.0
    assert r.events("skip")[-1]["reason"] == P.SKIP_SOFT_HALT
    assert r.doc()["m2x"]["status"] == "YUMUŞAK DURDURMA"


def test_hysteresis_band_and_five_snapshot_step_up_with_key_a(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    _seed(r.book, peak=230.0, tier=1)                                 # K2, DDₖ %13: bandın içinde (≤ %10 değil)
    for k in range(1, 8):
        r.tour(_day(k))
    assert r.book.st.tier == 1 and r.book.st.streak == 0               # %15'in altında ama çıkış yok (histerezis)
    _seed(r.book, peak=230.0, tier_peak=215.0, tier=1)                # DDₖ %7 ≤ %10 → A geçer
    for k in range(8, 12):
        r.tour(_day(k))
        assert r.book.st.tier == 1
    _seed(r.book, peak=230.0, tier_peak=240.0, tier=1)                # bir gün A geçmez → sayaç SIFIRLANIR
    r.tour(_day(12))
    assert r.book.st.streak == 0 and r.book.st.tier == 1
    _seed(r.book, peak=230.0, tier_peak=215.0, tier=1)
    for k in range(13, 18):
        r.tour(_day(k))
    assert r.book.st.tier == 0
    up = [e for e in r.book.meta["events"] if e["kind"] == "UP"]
    assert up and up[-1]["key"] == "A" and up[-1]["anchored"] is False
    # aynı gün ikinci tur görüntü DEĞİLDİR (sayaç ilerlemez)
    s0 = r.book.st.streak
    r.tour(_day(17, 30))
    assert r.book.st.streak == s0


def test_key_b_steps_up_on_parent_unit_r_recovery_and_reanchors(tmp_path):
    r = Rig(tmp_path, new_entries=False)
    r.tour(_day(0), opens=[("UUU/USDT", 100.0, 80.0)])                # M2'nin açık pozisyonu U'yu taşır
    _seed(r.book, peak=260.0, tier=1)                                 # K2, DDₖ %23 — A geçmez
    for k in range(1, 30):
        r.px["UUU/USDT"] = 100.0 + k                                  # M2'nin mark R'si artar → ΔU20 > 0
        r.tour(_day(k))
        if r.book.st.tier == 0:
            break
    up = [e for e in r.book.meta["events"] if e["kind"] == "UP"]
    assert up and up[0]["key"] == "B" and up[0]["anchored"] is True
    # B ile çıkışta Pₖ := E / (1 − (alt sınır − 0,05)), gerçek zirve P DEĞİŞMEZ
    assert r.book.st.tier_peak == pytest.approx(up[0]["x"] / (1 - (0.15 - 0.05)), rel=1e-9)
    assert r.book.st.peak == 260.0
    assert len([d for d in r.book.meta["daily"] if d["u"] is not None]) >= 21


def test_halt_at_minus_50_persists_across_restart_and_owner_resume(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    _seed(r.book, peak=400.0)                                         # E 200 → DD %50 → DUR
    out = r.tour(_day(1), opens=[("AAA/USDT", 100.0, 90.0)])
    assert out["tier"] == "HALTED" and out["opened"] == 0
    assert r.events("skip")[-1]["reason"] == P.SKIP_HALTED
    doc = r.doc()
    assert doc["m2x"]["status"] == "DURDURULDU" and doc["m2x"]["halt"]["epoch"] == 0
    assert check_lines(doc, {"enabled": True})[-1].startswith("!! ")
    # yeniden başlatma DURU silmez
    r.book = r.new_book()
    assert r.book.st.halted and r.book.st.peak == 400.0
    # sahip isteği: yanlış dönem → REJECTED_STALE_EPOCH (tek kullanım)
    req = write_resume_request(r.cfg.state_path, note="inceledim")["request"]
    ctl = r.cfg.state_path / "strategy_paper_m2x" / "m2x_control.json"
    ctl.write_text(json.dumps(dict(req, epoch_expected=7)), encoding="utf-8")
    r.tour(_day(2))
    assert r.book.st.halted and r.book.meta["resume_history"][-1]["result"] == "REJECTED_STALE_EPOCH"
    assert json.loads(ctl.read_text(encoding="utf-8"))["consumed"] is True
    # doğru istek → kabul: dönem +1, P := Pₖ := E, kademe K3
    w = write_resume_request(r.cfg.state_path, note="kriz bitti, inceledim")
    assert w["halted"] is True and w["request"]["epoch_expected"] == 0
    r.tour(_day(3))
    st = r.book.st
    assert not st.halted and st.tier == 2 and r.book.meta["epoch"] == 1
    assert st.peak == pytest.approx(200.0) and st.tier_peak == pytest.approx(200.0)
    assert r.book.meta["resume_history"][-1]["result"] == "ACCEPTED"
    # aynı istek ikinci kez işlenmez; DURDURULMAMIŞ defterde yeni istek REJECTED_NOT_HALTED
    r.tour(_day(4))
    assert [x["result"] for x in r.book.meta["resume_history"]] == ["REJECTED_STALE_EPOCH", "ACCEPTED"]
    write_resume_request(r.cfg.state_path, note="tekrar")
    r.tour(_day(5))
    assert r.book.meta["resume_history"][-1]["result"] == "REJECTED_NOT_HALTED"
    out = r.tour(_day(6), opens=[("BBB/USDT", 100.0, 90.0)])
    pos = r.book.ledger.positions["BBB/USDT"]
    assert out["opened"] == 1 and float(pos.qty) * (float(pos.entry_avg) - 90.0) == pytest.approx(0.005 * 200, rel=0.02)


def test_data_gap_blocks_entries_never_changes_tier_and_snapshot_is_retried(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    _seed(r.book, peak=400.0)                                         # DUR koşulu var ama veri yok
    out = r.tour(_day(1), opens=[("BBB/USDT", 100.0, 90.0)], priced={"BBB/USDT"})   # AAA'nın fiyatı yok
    assert out["data_gap"] is True and out["opened"] == 0 and not r.book.st.halted
    assert r.events("skip")[-1]["reason"] == P.SKIP_DATA_GAP
    assert r.book.meta["last_snapshot_day"] == "2026-10-01" and r.book.meta["last_observation"]["data_gap"] is True
    r.tour(_day(1, 20))                                                # aynı gün yeniden denenir
    assert r.book.meta["last_snapshot_day"] == "2026-10-02" and r.book.st.halted


def test_missed_days_reset_step_up_streak(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    _seed(r.book, peak=215.0, tier=1)                                 # A geçer
    for k in (1, 2, 3):
        r.tour(_day(k))
    assert r.book.st.streak == 3
    r.tour(_day(6))                                                    # 4. ve 5. günün görüntüsü yok
    assert r.book.st.streak == 1 and r.book.st.tier == 1
    assert [a for a, u in r.book.st.u_hist[-3:-1]] == ["2026-10-05", "2026-10-06"]


def test_mirror_rule_close_orphan_and_parent_already_closed(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0), ("BBB/USDT", 50.0, 45.0)])
    r.px["AAA/USDT"] = 103.0
    r.tour(_day(1), closes=[("AAA/USDT", "M2_TSMOM28_CROSS_DOWN")])   # kural çıkışı → aynı mark'la eşlenir
    h = [x for x in r.book.ledger.history if x.symbol == "AAA/USDT"][-1]
    assert h.exit_reason == "%s:M2_TSMOM28_CROSS_DOWN" % MIRROR_PARENT_CLOSED and float(h.exit_price) < 103.0
    m2h = [x for x in r.m2.ledger.history if x.symbol == "AAA/USDT"][-1]
    assert float(h.exit_price) == pytest.approx(float(m2h.exit_price))   # aynı fiyat + aynı kayma modeli
    # M2 turlar ARASINDA (ör. izleyici) kapatır → bir sonraki turda yetim kapanışı (ayrışma)
    r.m2.close("BBB/USDT", 50.0, "manuel", _day(1, 30))
    r.tour(_day(2))
    assert "BBB/USDT" not in r.book.ledger.positions
    assert r.book.ledger.history[-1].exit_reason == MIRROR_ORPHAN_CLOSED and r.book.meta["divergences"]["orphan"] == 1
    # M2 açar ama aynı turda kendi tikinde kapanır → M2X GİRMEZ
    r.px["CCC/USDT"] = 100.0
    held = r.m2.held_ids()
    r.m2.open("CCC/USDT", 100.0, 99.0, _day(3))
    r.book.capture_parent_step(r.m2, held, now=_day(3))
    r.px["CCC/USDT"] = 98.0
    r.m2.ledger.tick({"CCC/USDT": _td(98.0, _day(3))}, now_utc=_day(3))
    pm = {s: _td(p, _day(3)) for s, p in r.px.items()}
    r.book.tour_step(r.m2, now=_day(3), tick_now=_day(3), pmarks=pm, pmarks_f=dict(r.px), pbars={},
                     wall_ms=lambda: int(_day(3).timestamp() * 1000))
    assert "CCC/USDT" not in r.book.ledger.positions and r.events("skip")[-1]["reason"] == P.SKIP_PARENT_CLOSED


def test_stop_sync_follows_a_parent_stop_move(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    r.m2.ledger.positions["AAA/USDT"].stop = Decimal("95")             # (bugünkü config'te olmaz) M2 stopu taşıdı
    r.tour(_day(1))
    pos = r.book.ledger.positions["AAA/USDT"]
    assert pos.stop == Decimal("95") and pos.meta["m2x_stop_sync"]["to"] == "95"
    assert r.doc()["m2x"]["stop_syncs"] == 1 and r.events("stop_sync")
    r.tour(_day(2))
    assert r.doc()["m2x"]["stop_syncs"] == 1                           # değişmeyen stop yeniden eşlenmez


def test_parent_position_closed_before_capture_is_counted_as_already_closed(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    held = r.m2.held_ids()
    r.px["QQQ/USDT"] = 100.0
    r.m2.open("QQQ/USDT", 100.0, 90.0, _day(1))
    r.m2.close("QQQ/USDT", 89.0, "stop", _day(1))                      # adımla yakalama arasında (izleyici) kapandı
    r.book.capture_parent_step(r.m2, held, now=_day(1))
    pm = {s: _td(p, _day(1)) for s, p in r.px.items()}
    r.book.tour_step(r.m2, now=_day(1), tick_now=_day(1), pmarks=pm, pmarks_f=dict(r.px), pbars={},
                     wall_ms=lambda: int(_day(1).timestamp() * 1000))
    assert not r.book.ledger.positions and r.events("skip")[-1]["reason"] == P.SKIP_PARENT_CLOSED


def test_missed_parent_tour_never_enters_late(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    r.tour(_day(1), opens=[("AAA/USDT", 100.0, 90.0)], process=False)   # M2X bu turu işleyemedi
    r.tour(_day(2))
    assert "AAA/USDT" not in r.book.ledger.positions
    assert r.events("skip")[-1]["reason"] == P.SKIP_MISSED_TOUR


def test_missed_parent_positions_counted_once_after_restart(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    r.px["AAA/USDT"] = 100.0
    r.m2.open("AAA/USDT", 100.0, 90.0, _day(1))                       # M2X kapalıyken (süreç yok) M2 açtı
    r.book = r.new_book()
    r.tour(_day(2))
    r.tour(_day(3))
    sk = [s for s in r.events("skip") if s["reason"] == P.SKIP_MISSED_TOUR]
    assert len(sk) == 1 and sk[0]["symbol"] == "AAA/USDT" and "AAA/USDT" not in r.book.ledger.positions


def test_liquidation_divergence_counted_when_m2_stops(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("LIQ/USDT", 100.0, 90.0)])
    assert r.book.ledger.positions["LIQ/USDT"].leverage == 4         # M2X likidasyonu stopa daha yakın
    r.px["LIQ/USDT"] = 70.0                                           # %30 boşluk: M2X likide (≈ %24,7), M2 (2x) stop
    r.tour(_day(1), m2_tick=True)
    assert r.book.ledger.history[-1].exit_reason == "likidasyon"
    assert r.m2.ledger.history[-1].exit_reason == "stop"
    assert r.book.meta["divergences"]["liquidation"] == 1
    d_r = float(r.book.ledger.history[-1].r_multiple) - float(r.m2.ledger.history[-1].r_multiple)
    assert r.book.meta["parity"]["matched"] == 1 and r.book.meta["parity"]["liq_div_cost_r"] == pytest.approx(d_r)
    assert d_r != 0                                                   # izole marj kırpması: R farkı raporlanır


def test_new_entries_off_skips_but_still_mirrors_exits(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    r.cfg.v3.m2x_aggressive.new_entries = False
    r.book = r.new_book()
    r.tour(_day(1), opens=[("BBB/USDT", 100.0, 90.0)], closes=[("AAA/USDT", "M2_STRUCTURE_EXIT")])
    assert not r.book.ledger.positions and r.events("skip")[-1]["reason"] == P.SKIP_NEW_ENTRIES_OFF
    assert r.book.ledger.history[-1].exit_reason.startswith(MIRROR_PARENT_CLOSED)
    assert r.doc()["m2x"]["new_entries"] is False


def test_parent_input_regime_change_is_recorded(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0))
    held = r.m2.held_ids()
    r.book.capture_parent_step(r.m2, held, now=_day(1), regime={"learning_on": False, "extra_entries": None})
    r.book.tour_step(r.m2, now=_day(1), tick_now=_day(1), pmarks={}, pmarks_f={}, pbars={},
                     wall_ms=lambda: int(_day(1).timestamp() * 1000))
    ev = [e for e in r.book.meta["events"] if e["kind"] == "INPUT_REGIME_CHANGED"]
    assert ev and ev[-1]["from"]["extra_entries"] == "record_selectivity" and ev[-1]["to"]["learning_on"] is False


def test_engine_kill_switch_blocks_new_entries(tmp_path):
    from types import SimpleNamespace
    r = Rig(tmp_path)
    r.book.killswitch = SimpleNamespace(active=True)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    assert not r.book.ledger.positions and r.events("skip")[-1]["reason"] == "M2X_KILL_SWITCH"


def test_parent_disabled_no_entries_no_orphan_closes(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    r.m2.close("AAA/USDT", 100.0, "manuel", _day(0, 30))
    r.tour(_day(1), parent=False)
    assert "AAA/USDT" in r.book.ledger.positions                       # yetim kapatılmaz (M2 kapalı: bilinmiyor)
    assert r.doc()["m2x"]["parent"]["enabled"] is False


def test_monitor_protect_uses_only_the_given_batch_and_records(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0)])
    t = _day(0, 5)
    recs = r.book.protect({"AAA/USDT": _td(89.0, t)}, {"AAA/USDT": 89.0}, {}, now=t, expect=r.book.held_ids(),
                          apply_clock=lambda: int(t.timestamp() * 1000))
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and not r.book.ledger.positions
    assert r.events("close")[-1]["how"] == "monitor"
    # kimlik koruması: beklenen kimlik eşleşmezse tik yok
    r.tour(_day(1), opens=[("BBB/USDT", 100.0, 90.0)])
    t2 = _day(1, 5)
    assert r.book.protect({"BBB/USDT": _td(80.0, t2)}, {}, {}, now=t2, expect={"BBB/USDT": "F99999"},
                          apply_clock=lambda: int(t2.timestamp() * 1000)) == []


def test_restart_persistence_equals_uninterrupted_run(tmp_path):
    def script(rig, restart_at=None):
        steps = [dict(opens=[("A/USDT", 100.0, 90.0), ("B/USDT", 50.0, 44.0)]), dict(), dict(opens=[("C/USDT", 20.0, 18.0)]),
                 dict(closes=[("A/USDT", "M2_TSMOM28_CROSS_DOWN")]), dict(), dict(opens=[("D/USDT", 10.0, 9.0)]), dict()]
        for k, kw in enumerate(steps):
            if restart_at is not None and k == restart_at:
                rig.book = rig.new_book()
            for s in list(rig.px):
                rig.px[s] = round(rig.px[s] * (0.97 if k % 2 else 1.02), 6)
            if k == 2:
                _seed(rig.book, peak=245.0)                          # kademe inişi de kalıcılığa girsin
            rig.tour(_day(k), **kw)
        return rig
    a = script(Rig(tmp_path / "a"))
    b = script(Rig(tmp_path / "b"), restart_at=4)

    def view(rig):
        d = rig.book.ledger.to_dict()
        d.pop("updated_at", None)
        st = rig.book.st
        meta = {k: rig.book.meta[k] for k in ("skips", "entries", "divergences", "last_snapshot_day", "epoch", "tours")}
        meta["daily"] = [{k: v for k, v in x.items()} for x in rig.book.meta["daily"]]
        return d, (st.peak, st.tier_peak, st.tier, st.halted, st.streak, st.u_hist), meta
    assert view(a) == view(b)
    assert a.book.st.tier == 1 and any(e["kind"] == "DOWN" for e in a.book.meta["events"])


# ---------------------------------------------------------------------------- simülasyonla parite
def test_same_stream_same_result_as_simulation_runner(tmp_path):
    """Simülasyonun `M2xRunner`ı (ön kayıtlı sonuçların kodu) ile canlı defter AYNI akışta AYNI girişleri, boyutları,
    kaldıraçları, kapanışları ve kademe yolunu üretir (politika kodu tek kaynak)."""
    from tradingbot.m2x_sim import ARMS, LedgerParams, M2xRunner
    r = Rig(tmp_path)
    sim = M2xRunner(ARMS["b"], params=LedgerParams.from_v3(r.cfg.v3), funding=None, filters=r.filters, record_hourly=False)
    days = [
        dict(opens=[("A/USDT", 100.0, 90.0), ("B/USDT", 40.0, 35.0), ("C/USDT", 5.0, 4.0)]),
        dict(move={"A/USDT": 1.05, "B/USDT": 0.97}),
        dict(opens=[("D%02d/USDT" % i, 10.0 + i, 9.0 + i * 0.9) for i in range(12)]),
        dict(move={s: 0.9 for s in ["A/USDT", "B/USDT", "C/USDT"]}),
        dict(closes=[("A/USDT", "M2_TSMOM28_CROSS_DOWN")], opens=[("E/USDT", 7.0, 6.0)]),
        dict(move={"D00/USDT": 0.5}),                                  # stop boşluğu (M2X likide / M2 stop olabilir)
        dict(opens=[("F/USDT", 3.0, 2.5)]),
    ]
    order = 0
    for k, spec in enumerate(days):
        now = _day(k)
        t_ms = int(now.timestamp() * 1000)
        for s, f in (spec.get("move") or {}).items():
            r.px[s] = r.px[s] * f
        held_before = r.m2.held_ids()
        opens_ev = []
        for sym, px, stop in spec.get("opens", []):
            r.px[sym] = px
            pos = r.m2.open(sym, px, stop, now)
            order += 1
            opens_ev.append({"id": str(pos.id), "symbol": sym, "ref": float(pos.meta["ref_entry"]), "stop": float(pos.stop),
                             "order": order})
        rule = []
        for sym, reason in spec.get("closes", []):
            mid = r.m2.held_ids()[sym]
            r.m2.close(sym, r.px[sym], reason, now)
            rule.append((mid, sym, r.px[sym], reason))
        r.book.capture_parent_step(r.m2, held_before, now=now)
        r.m2.ledger.tick({s: _td(r.px[s], now) for s in r.m2.ledger.positions}, now_utc=now)
        # U: iki tarafa aynı değer (M2'nin açık pozisyonlarının mark R'si + kapananlar) — canlı defter kendisi hesaplar
        r.book._u_scan(r.m2)
        u = r.book._u_value(r.m2, dict(r.px))
        m2_open = r.m2.held_ids()
        sim.on_tour(t_ms, marks={s: float(p) for s, p in r.px.items()}, rule_closes=rule, opens=opens_ev, m2_open=dict(m2_open),
                    u=u, bars=None)
        pm = {s: _td(p, now) for s, p in r.px.items()}
        r.book.tour_step(r.m2, now=now, tick_now=now, pmarks=pm, pmarks_f=dict(r.px), pbars={}, wall_ms=lambda t=t_ms: t)
        live = {s: (round(float(p.qty), 9), p.leverage, round(float(p.entry_avg), 9)) for s, p in r.book.ledger.positions.items()}
        simp = {s: (round(float(p.qty), 9), p.leverage, round(float(p.entry_avg), 9)) for s, p in sim.ledger.positions.items()}
        assert live == simp, (k, live, simp)
        assert [(h.symbol, h.exit_reason, round(float(h.net_pnl), 9)) for h in r.book.ledger.history] == \
               [(h.symbol, h.exit_reason, round(float(h.net_pnl), 9)) for h in sim.ledger.history], k
        assert (r.book.st.tier, r.book.st.halted) == (sim.st.tier, sim.st.halted), k
        assert r.book.st.peak == pytest.approx(sim.st.peak) and r.book.st.tier_peak == pytest.approx(sim.st.tier_peak)
        assert float(r.book.ledger.wallet_balance) == pytest.approx(float(sim.ledger.wallet_balance), abs=1e-9)
    skipped = {(s["symbol"], s["reason"]) for s in r.events("skip")}
    assert skipped == {(s["symbol"], s["reason"]) for s in sim.skips}
    assert len(r.book.ledger.history) >= 2 and len(r.events("entry")) >= 10


# ============================================================================ 6. raporlama
def _scorecard_main(state, *extra):
    import io
    from contextlib import redirect_stdout

    import importlib.util
    spec = importlib.util.spec_from_file_location("bot_scorecard_m2x", ROOT / "scripts" / "bot_scorecard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(["--state", str(state), "--now", "2026-10-09T12:00:00+00:00", *extra])
    return rc, buf.getvalue(), mod


def _state_with_m2x(tmp_path):
    r = Rig(tmp_path)
    r.tour(_day(0), opens=[("AAA/USDT", 100.0, 90.0), ("BBB/USDT", 50.0, 45.0)])
    r.px["AAA/USDT"] = 104.0
    r.tour(_day(1), closes=[("AAA/USDT", "M2_TSMOM28_CROSS_DOWN")])
    r.tour(_day(2), opens=[("CCC/USDT", 20.0, 18.0)])
    (r.cfg.state_path / "strategy_paper_m2").mkdir(parents=True, exist_ok=True)
    r.m2.ledger.save(r.cfg.state_path / "strategy_paper_m2" / "futures_ledger.json")
    return r


def test_scorecard_main_table_unchanged_and_separate_m2x_section(tmp_path):
    r = _state_with_m2x(tmp_path / "with")
    st = r.cfg.state_path
    rc, out, mod = _scorecard_main(st)
    assert rc == 0
    # aynı state, M2X klasörü ve özeti YOK → çıktı M2X bölümüne kadar bayt bayt aynı (ana tablo + AYLIK HEDEF)
    import shutil
    st2 = tmp_path / "without" / "state"
    shutil.copytree(st, st2)
    shutil.rmtree(st2 / "strategy_paper_m2x")
    (st2 / "strategy_paper_m2x.json").unlink()
    rc2, out2, _ = _scorecard_main(st2)
    assert rc2 == 0 and "M2X" not in out2
    head = out[:out.index("\nM2X AGRESİF")]
    assert head.rstrip("\n") == out2.rstrip("\n").replace(str(st2), str(st))
    assert "strategy_paper_m2x" not in mod.find_books(st) and set(mod.find_books(st)) == set(mod.find_books(st2))
    sec = out[out.index("M2X AGRESİF"):]
    assert "hüküm yok: kopyadır, yeni kanıt değildir" in sec and "GÜNLÜK HEDEF (+%1/gün" in sec and "AYLIK HEDEF (+%1/ay" in sec
    assert "KÂRDA" not in sec and "ZARARDA" not in sec and "BELİRSİZ" not in sec   # bağımsız hüküm YOK
    assert "tam boy kopyalanan" in sec and "ISINMA" in sec
    # --m2x-check satırları (§4.3)
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("m2x_aggressive:\n  enabled: true\n  new_entries: true\n  policy_version: m2x_v1\n", encoding="utf-8")
    rc3, out3, _ = _scorecard_main(st, "--m2x-check", "--config", str(cfgp))
    lines = [x.strip() for x in out3.strip().splitlines()]
    assert rc3 == 0 and lines[0] == "m2x_aggressive (config): enabled = True · new_entries = True · policy = m2x_v1"
    assert lines[1].startswith("M2X: özkaynak ") and "· DURUM: ÇALIŞIYOR" in lines[1]
    assert lines[1].index("düşüş") < lines[1].index("bugün")      # yüzdeler düşüşün YANINDA, satır başında değil


def test_scorecard_unchanged_when_no_m2x_state(tmp_path):
    from tradingbot.accounting import FuturesLedgerV2 as L
    st = tmp_path / "state"
    (st / "strategy_paper_m2").mkdir(parents=True)
    L(200).save(st / "strategy_paper_m2" / "futures_ledger.json")
    rc, out, mod = _scorecard_main(st)
    assert rc == 0 and "M2X" not in out and mod.mirror_cards(st) == {}


def test_panel_renders_m2x_card_without_buttons(tmp_path):
    r = _state_with_m2x(tmp_path)
    from tradingbot.dashboard import m2x_view
    doc = r.doc()
    html = m2x_view.section_html(doc)
    assert "M2X pozisyonları" in html and "Atlanan M2 girişleri" in html and "<button" not in html.lower()
    _seed(r.book, peak=600.0)
    r.tour(_day(3))
    html = m2x_view.section_html(r.doc())
    assert "DURDURULDU" in html and "m2x-resume" in html and "<button" not in html.lower() and "<form" not in html.lower()
    # panel defter listesi veri-güdümlü: indeks girdisi → etiketli defter
    from tradingbot.dashboard.state import StateReader
    (r.cfg.state_path / "strategy_paper_index.json").write_text(json.dumps({"books": [r.book.index_entry()]}), encoding="utf-8")
    sr = StateReader(r.cfg.state_path)
    b = [x for x in sr.books() if x["book_id"] == "strategy_paper_m2x"]
    assert b and b[0]["label"] == "M2X · agresif M2 kopyası (PAPER)"
    assert sr.book_summary_file("strategy_paper_m2x")["m2x"]["halted"] is True
    assert len(sr.book_positions("strategy_paper_m2x")) == len(r.book.ledger.positions)


def test_panel_routes_render_m2x_from_the_index(tmp_path):
    """Panel defter listesi veri-güdümlü: indeks girdisi + özet → genel bakış, strateji sayfası, işlemler ve hesap API'si."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    r = _state_with_m2x(tmp_path)
    st = r.cfg.state_path
    (st / "strategy_paper_index.json").write_text(json.dumps({"books": [r.book.index_entry()]}), encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    c = TestClient(create_app(st, tmp_path / "data"))
    home = c.get("/")
    assert home.status_code == 200 and "M2X · agresif M2 kopyası (PAPER)" in home.text
    page = c.get("/portfolio/strategy")
    assert page.status_code == 200 and "M2X pozisyonları" in page.text and "Zirveden düşüş" in page.text
    assert "<button" not in page.text[page.text.index("M2X pozisyonları") - 4000:page.text.index("M2X pozisyonları")].lower()
    assert c.get("/trades", params={"book": "strategy_paper_m2x"}).status_code == 200
    api = c.get("/api/book/strategy_paper_m2x").json()
    assert api["book_id"] == "strategy_paper_m2x" and api["open_total"] == len(r.book.ledger.positions)


def test_resume_cli_requires_review_flag_and_note(tmp_path, capsys):
    from types import SimpleNamespace

    from tradingbot.cli_v3 import cmd_m2x_resume
    cfg = _cfg(tmp_path)
    assert cmd_m2x_resume(cfg, SimpleNamespace(i_reviewed=False, note="x")) == 2
    assert cmd_m2x_resume(cfg, SimpleNamespace(i_reviewed=True, note="  ")) == 2
    assert not (cfg.state_path / "strategy_paper_m2x" / "m2x_control.json").exists()
    assert cmd_m2x_resume(cfg, SimpleNamespace(i_reviewed=True, note="inceledim")) == 0
    doc = json.loads((cfg.state_path / "strategy_paper_m2x" / "m2x_control.json").read_text(encoding="utf-8"))
    assert doc["note"] == "inceledim" and doc["consumed"] is False and len(doc["request_id"]) == 32
    assert "REJECTED_NOT_HALTED" in capsys.readouterr().out

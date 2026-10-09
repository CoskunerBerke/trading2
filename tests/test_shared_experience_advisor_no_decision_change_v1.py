# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — KARAR DEĞİŞMEZ (2026-09-29; SPEC_ADVISOR_V1 §1, §7 T-A6 + T-A8; DANISMAN_V1.md §1).

**Altın eşitlik.** GERÇEK `TradingEngineV3.tour` harness'i (ortak deneyim T6 ile aynı: T2/M2/D4 kâğıt defterleri,
Formasyon defteri, öğrenme modu AÇIK ve KAPALI, donmuş saat, deterministik kimlikler, dört tur): toplayıcı RECORD ×
danışman YOK / OFF / RECORD. Bütün karar eserleri (defterler, karşı-olgusal/gölge dosyaları, planlar, özetler, risk.json,
huni, signals_seen, karar günlüğü), ANA DEPO (`experience.jsonl`, `archive/**`) ve `cursor.json` BAYT BAYT aynı;
`health.json` yalnız `["shared_experience"]["advisor"]` kadar farklı. YOK == OFF (dosya kümesi dahil; `advice/` yok).

**Arıza enjeksiyonu** (katlama istisnası, tavsiye deposu G/Ç hatası, disk dolu, bozuk anlık görüntü, bulunamayan
filigran, bellek tavanı): eşitlik korunur; toplayıcının sayaçları ve kesicisi ETKİLENMEZ; danışmanın sayaçları artar.
Her tavsiye satırı `effect == "NONE"`.

**AST koruması (T-A8).** Karar yolundaki hiçbir modül (motor, `learn/*`, öğrenme modu/tabanı/karşı-olgusal,
strategy_paper, pattern_trader, box_timer, replay) danışmana/tavsiyeye ATIF YAPMAZ; paket dışında yalnız `cli_v3`,
panel (yalnız dosya adları), `config_v3` (ayar alanları) ve `ops/backup` (yol sabitleri) anar; panel paketi içe aktarmaz;
danışman modülleri `advice/` dışına yazmaz.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "tradingbot" / "shared_experience"
sys.path.insert(0, str(HERE))

import test_shared_experience_no_decision_change_v1 as G  # noqa: E402

RECORD = {"enabled": True, "mode": "RECORD"}
ADV = dict(RECORD, advisor_mode="RECORD")


def _xp_bytes(run: dict) -> dict[str, bytes]:
    d = run["eng"].cfg.state_path / "shared_experience"
    out: dict[str, bytes] = {}
    if not d.exists():
        return out
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        if p.is_file() and (rel in ("experience.jsonl", "cursor.json") or rel.startswith("archive/")):
            out[rel] = p.read_bytes()
    return out


def _advice_dir(run: dict) -> Path:
    return run["eng"].cfg.state_path / "shared_experience" / "advice"


@pytest.fixture(scope="module")
def _cache():
    return {}


def _rec(tmp_path, monkeypatch, cache, lm: bool) -> dict:
    key = ("rec", lm)
    if key not in cache:
        cache[key] = G._run(tmp_path / "rec_base", monkeypatch, lm=lm, xp=dict(RECORD))
    return cache[key]


# ============================================================================ altın eşitlik
@pytest.mark.parametrize("lm", [True, False], ids=["learning_on", "learning_off"])
def test_golden_advisor_absent_off_record_change_no_decision_and_no_main_store_byte(tmp_path, monkeypatch, _cache, lm):
    base = _rec(tmp_path, monkeypatch, _cache, lm)                       # danışman YOK (alan yazılmamış)
    off = G._run(tmp_path / "adv_off", monkeypatch, lm=lm, xp=dict(RECORD, advisor_mode="OFF"))
    rec = G._run(tmp_path / "adv_rec", monkeypatch, lm=lm, xp=dict(ADV))
    # karar eserleri (shared_experience/ dışı her dosya + health.json anahtar düşülmüş)
    assert G._diff(G._decision_view(off), G._decision_view(base)) == []
    assert G._diff(G._decision_view(rec), G._decision_view(base)) == []
    # YOK == OFF: health.json (ölçülen adım süresi hariç) ve ortak deneyim dosya KÜMESİ birebir; tavsiye klasörü YOK
    ho, hb0 = dict(off["health"]["shared_experience"]), dict(base["health"]["shared_experience"])
    ho.pop("step_ms"), hb0.pop("step_ms")
    assert ho == hb0 and "advisor" not in ho
    xd = lambda r: sorted(p.relative_to(r["eng"].cfg.state_path).as_posix()  # noqa: E731
                          for p in (r["eng"].cfg.state_path / "shared_experience").rglob("*"))
    assert xd(off) == xd(base)
    assert not _advice_dir(base).exists() and not _advice_dir(off).exists()
    # ana depo ve imleç BAYT BAYT aynı
    xb, xo, xr = _xp_bytes(base), _xp_bytes(off), _xp_bytes(rec)
    assert xb and xb == xo == xr
    # health: yalnız ["shared_experience"]["advisor"] eklenir
    hb, hr = dict(base["health"]["shared_experience"]), dict(rec["health"]["shared_experience"])
    adv = hr.pop("advisor")
    assert set(hr) == set(hb) and {k: hr[k] for k in hr if k != "step_ms"} == {k: hb[k] for k in hb if k != "step_ms"}
    assert adv["state"] == "OK" and adv["advisor_sha"] and adv["errors"] == 0 and adv["breaker"] is False
    assert set(adv) == {"state", "advisor_sha", "mode", "lag_rows", "index_mb", "advised_last", "late_last", "errors",
                        "breaker", "step_ms", "step_ms_p95", "record_blocked", "advice_emitted", "advice_written",
                        "waiting_segment", "segments_bad", "lines_bad"}
    assert adv["record_blocked"] is None and adv["waiting_segment"] is None and adv["segments_bad"] == 0
    assert adv["advice_emitted"] == adv["advice_written"] > 0
    # tavsiye yazıldı ve HİÇBİR ŞEY değiştirmez
    rows = [json.loads(x) for x in (_advice_dir(rec) / "advice.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and all(r["effect"] == "NONE" and r["app_mode"] == "PAPER" for r in rows)
    n_targets = sum(1 for x in (rec["eng"].cfg.state_path / "shared_experience" / "experience.jsonl").read_text(
        encoding="utf-8").splitlines() if '"kind":"xp_entry"' in x or ('"kind":"xp_cf"' in x and '"rev":0' in x))
    assert len(rows) == n_targets
    st = rec["status"]
    assert st["counters"]["errors_total"] == 0 and st["breaker"]["tripped"] is False


# ============================================================================ arıza enjeksiyonu
def _fault_fold(eng, mp):
    from tradingbot.shared_experience import advisor as A

    def boom(self, *a, **k):
        raise RuntimeError("fold boom")
    mp.setattr(A.AdvisorFold, "fold_batch", boom)


def _fault_store_raises(eng, mp):
    from tradingbot.shared_experience.advice_store import AdviceStore

    def boom(self, rows):
        raise OSError(28, "No space left on device")
    mp.setattr(AdviceStore, "append_rows", boom)


def _fault_store_io(eng, mp):
    from tradingbot.shared_experience.advice_store import AdviceStore

    def io_err(self, rows):
        rows = list(rows)
        self.last_append = {"io_error": len(rows), "blocked_degraded": 0, "duplicate": 0}
        return 0, len(rows)
    mp.setattr(AdviceStore, "append_rows", io_err)


def _fault_disk_full(eng, mp):
    from tradingbot.shared_experience import store as ST
    from tradingbot.shared_experience.advice_store import AdviceStore
    mp.setattr(AdviceStore, "_state_for", lambda self, used: ST.STATE_DEGRADED)


def _fault_snapshot_corrupt(eng, mp):
    d = eng.cfg.state_path / "shared_experience" / "advice"
    d.mkdir(parents=True, exist_ok=True)
    (d / "advisor_state.v1.gz").write_bytes(b"\x1f\x8bcorrupt")


def _fault_watermark_missing(eng, mp):
    from tradingbot.shared_experience import advisor as A
    from tradingbot.shared_experience import advisor_live as AL
    d = eng.cfg.state_path / "shared_experience" / "advice"
    d.mkdir(parents=True, exist_ok=True)
    body = A.AdvisorFold().state_bytes()
    hdr = {"schema": AL.SNAPSHOT_SCHEMA, "advisor_sha": A.ADVISOR_SHA, "situation_schema_sha": A.S.SCHEMA_SHA,
           "last_row_id": "ffffffffffffffff", "watermark_seq": None, "archive_max_seq": 0,
           "body_sha256": hashlib.sha256(body).hexdigest()}
    (d / "advisor_state.v1.gz").write_bytes(gzip.compress(json.dumps(hdr).encode() + b"\n" + body))


def _fault_memory_cap(eng, mp):
    from tradingbot.shared_experience import advisor as A
    mp.setattr(A.AdvisorFold, "memory_estimate", lambda self: 10 ** 12)


FAULTS = {"fold_raises": _fault_fold, "advice_store_raises": _fault_store_raises, "advice_store_io_error": _fault_store_io,
          "disk_full": _fault_disk_full, "snapshot_corrupt": _fault_snapshot_corrupt,
          "watermark_missing": _fault_watermark_missing, "memory_cap": _fault_memory_cap}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_advisor_faults_change_no_decision_no_main_store_byte_and_not_the_collector(tmp_path, monkeypatch, _cache,
                                                                                      fault):
    base = _rec(tmp_path, monkeypatch, _cache, True)
    run = G._run(tmp_path / fault, monkeypatch, lm=True, xp=dict(ADV), prepare=FAULTS[fault])
    assert G._diff(G._decision_view(run), G._decision_view(base)) == [], fault
    assert _xp_bytes(run) == _xp_bytes(base), fault
    st, bst = run["status"], base["status"]
    assert st["counters"] == bst["counters"] and st["state"] == "OK" and st["breaker"] == bst["breaker"]
    adv = run["health"]["shared_experience"]["advisor"]
    ast_ = json.loads((_advice_dir(run) / "advisor_status.json").read_text(encoding="utf-8"))
    c = ast_["counters"]
    if fault == "fold_raises":
        assert adv["errors"] == 4 and ast_["breaker"]["consecutive_errors"] == 4 and adv["state"] == "OK"
    elif fault == "advice_store_raises":
        assert adv["errors"] == 4 and not (_advice_dir(run) / "advice.jsonl").exists()
    elif fault in ("advice_store_io_error", "disk_full"):
        assert c["advice_io_errors"] == 4 and c["advice_written"] == 0 and c["advice_retry"] > 0
        # (2026-09-30, inceleme bulgusu) kayıt engeli GÖRÜNÜR: eskiden sağlık / durum "OK" diyordu, hiçbir şey yazılmazken
        want = "STORE_DEGRADED" if fault == "disk_full" else "WRITE_ERROR"
        assert adv["errors"] == 0 and adv["state"] == "RECORD_BLOCKED" and adv["record_blocked"] == want
        assert ast_["state"] == "RECORD_BLOCKED" and ast_["internal_state"] == "OK"
        assert adv["advice_emitted"] > 0 and adv["advice_written"] == 0 and ast_["record"]["retry_pending"] > 0
    elif fault == "snapshot_corrupt":
        assert c["snapshot_rejected"] == 1 and c["rebuilds"] == 1 and adv["errors"] == 0
    elif fault == "watermark_missing":
        assert c["watermark_lost"] == 1 and c["rebuilds"] >= 1 and adv["errors"] == 0
    elif fault == "memory_cap":
        assert adv["state"] == "DEGRADED" and c["degraded_steps"] == 3


# ============================================================================ AST koruması (T-A8)
_TOKENS = re.compile(r"advisor_live|advisor_eval|advice_store|AdvisorFold|LiveAdvisor|AdviceStore|ADVICE_SCHEMA|xp_advice"
                     r"|shared_experience_advice|advice/|advisor_state|advisor_meta|advisor_status|walkforward_summary")
_DECISION_TOKENS = re.compile(r"advisor(?!y)|advice")
ALLOWED = {"tradingbot/cli_v3.py", "tradingbot/config_v3.py", "tradingbot/ops/backup.py"}


def _decision_modules() -> list[Path]:
    tb = ROOT / "tradingbot"
    mods = [tb / n for n in ("engine_v3.py", "learning_mode.py", "learning_basis.py", "learning_cf.py", "strategy_paper.py",
                             "box_timer.py")]
    for sub in ("learn", "pattern_trader", "replay"):
        mods += sorted((tb / sub).rglob("*.py"))
    return mods


def test_no_decision_path_module_references_the_advisor():
    for p in _decision_modules():
        txt = p.read_text(encoding="utf-8")
        assert not _DECISION_TOKENS.search(txt), (p, _DECISION_TOKENS.search(txt))
    eng = (ROOT / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    i = eng.index("def _prepared_experience_pool")
    assert "advis" not in eng[i:i + 6000].lower().replace("advisory", "")


def test_only_allowed_modules_mention_advisor_files_or_schemas():
    bad = []
    for p in sorted((ROOT / "tradingbot").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if PKG in p.parents or rel in ALLOWED or rel.startswith("tradingbot/dashboard/"):
            continue
        if _TOKENS.search(p.read_text(encoding="utf-8")):
            bad.append(rel)
    assert bad == []
    for rel in ("tradingbot/config_v3.py", "tradingbot/ops/backup.py") + tuple(
            p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "tradingbot" / "dashboard").glob("*.py"))):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                assert "shared_experience" not in (n.module or ""), rel
            if isinstance(n, ast.Import):
                assert not any("shared_experience" in a.name for a in n.names), rel


def test_advisor_modules_pass_the_package_mutator_guard_and_write_only_under_advice():
    files = [PKG / n for n in ("advisor.py", "advisor_live.py", "advisor_eval.py", "advice_store.py")]
    assert all(p.exists() for p in files)
    assert [v for p in files for v in G._violations(p)] == []
    writes = {"atomic_write_text", "atomic_write_bytes", "atomic_write_json", "write_text", "write_bytes", "unlink",
              "replace", "rename", "mkdir", "rmtree"}
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            name = n.func.attr if isinstance(n.func, ast.Attribute) else (n.func.id if isinstance(n.func, ast.Name)
                                                                           else None)
            if name not in writes:
                continue
            if name == "replace" and len(n.args) != 1:
                continue                                        # str.replace(a, b) — dosya işlemi değil
            if name.startswith("atomic_write"):
                target = ast.unparse(n.args[0])                  # atomic_write_*(yol, …)
            else:
                target = ast.unparse(n.func.value)               # yol.write_text(…) / yol.unlink() …
            if p.name == "advisor_live.py":
                assert ("self.dir" in target or target in ("self._meta_path()", "self._state_path()")), (p.name, target)
            elif p.name == "advisor_eval.py":
                assert target == "out_path", (p.name, target)       # yalnız --replay-out (kullanıcının verdiği dosya)
            else:
                pytest.fail("%s yazmamalı: %s" % (p.name, ast.unparse(n)))
    live = (PKG / "advisor_live.py").read_text(encoding="utf-8")
    assert "return self.dir / META_FILE" in live and "return self.dir / STATE_FILE" in live
    assert 'self.dir = self.root / ADVICE_DIR' in live


def test_package_init_and_row_contract_are_untouched():
    init = ast.parse((PKG / "__init__.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(init) if isinstance(n, (ast.Import, ast.ImportFrom))]
    from tradingbot.shared_experience import KINDS, ROW_SCHEMA
    assert KINDS == ("xp_entry", "xp_outcome", "xp_cf") and ROW_SCHEMA == "shared_experience_row_v1"
    src = (ROOT / "tradingbot" / "engine_v3.py").read_text(encoding="utf-8")
    assert "advisor" not in src.replace("ADVISORY", "")

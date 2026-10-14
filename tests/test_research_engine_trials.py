# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P3a — deneme defteri `trials/trials.jsonl` (docs/ENGINE_BRAIN_V1.md §3.5, §2.6; §8.1 P3a kabulü).

* Yalnız ekleme: her eklemeden önceki baytlar sonraki dosyanın öneki; tek yazım yolu atomik değiştirme (geçici dosya +
  fsync + geri okuma + önek denetimi + os.replace + klasör fsync), başka yeniden yazma yolu yok (AST); her çökme noktası
  hedefi bozmadan bırakır; yazarlar flock altında.
* Hash zinciri + kurcalama tespiti: alan değişikliği, satır silme/yer değiştirme/ekleme, yarım satır, boşluk oynaması,
  sayaç oynaması (zincir yeniden hesaplansa bile), tekil anahtar tekrarı, NaN/Infinity/1e999/yalnız vekil, bool/float
  zincir alanları; hiçbiri istisna fırlatmaz; kırık zincire eklenmez.
* Sayaçlar hiç azalmaz: dosya içinde (özellik testi) ve dosyalar arasında (HWM: satır sınırında kesme = ROLLED_BACK);
  DSR N = N_raw + N_lesson + N_explore.
* Öncül = 2.775 (`kind=PRIOR`, bir kez); `spec_sha` tekilleştirme (aynı LIB ailesi/spec/kapsam bir kez — aylık
  `LIB_v1.<AAAA-AA>` dahil; farklı kapsam ve yeni ana sürüm ayrı satır).
* `engine-query trials [--like]`: salt-okunur, ≤ 150 satır / ≤ 4 KB, config yüklemez, kilit almaz.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import random
import threading
from pathlib import Path

import pytest

from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import trials as TR
from tradingbot.research_engine.paths import EnginePaths

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-10-20T01:40:00+00:00"


def _paths(tmp_path) -> EnginePaths:
    return EnginePaths(data=tmp_path / "data", state=tmp_path / "data" / "state")


def _sha(i: int) -> str:
    return hashlib.sha256(f"spec-{i}".encode()).hexdigest()


def _spec(i: int, scope: str = "POOLED_P23", kind: str = "SPEC") -> dict:
    return {"kind": kind, "spec_sha": _sha(i), "scope": scope, "row_id": f"V{i}@{scope}", "at": AT,
            "params": {"i": i}, "code_sha": "c" * 40, "data_seal": "seal-1"}


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}


# ============================================================================ temel
def test_empty_ledger_and_prior_import_is_2775_once(tmp_path):
    p = _paths(tmp_path)
    assert TR.verify_chain(p.trials_file)["code"] == TR.EMPTY
    led = TR.TrialsLedger(p)
    assert led.counters == {"N_raw_cum": 0, "N_lesson_cum": 0, "N_explore_cum": 0}
    res = led.import_priors(at=AT)
    assert [r["status"] for r in res] == ["APPENDED"] * 10
    st = TR.verify_chain(p.trials_file)
    assert st["ok"] and st["rows"] == 10 and st["counters"]["N_raw_cum"] == 2775 == RG.prior_total()
    assert st["kinds"] == {"PRIOR": 10} and led.dsr_n() == 2775
    before = p.trials_file.read_bytes()
    again = TR.TrialsLedger(p).import_priors(at=AT)
    assert [r["status"] for r in again] == ["DUPLICATE"] * 10 and p.trials_file.read_bytes() == before
    rows = list(TR.iter_rows(p.trials_file))
    assert rows[0]["prev_sha"] == TR.GENESIS and rows[0]["seq"] == 1
    assert all(r["registry_sha"] == RG.REGISTRY_SHA and r["params"]["prior_trials_sha"] == RG.PRIOR_TRIALS_V1_SHA for r in rows)
    assert {r["row_id"] for r in rows if r["params"]["count_lower_bound"]} == {"fut_v1", "fut_v2"}


def test_spec_sha_dedup_scopes_and_counter_effects(tmp_path):
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    res = led.append([_spec(1), _spec(1), _spec(1, scope="CL_MAJ"), _spec(2), _spec(2, kind="OPTION"),
                      _spec(3, scope="COIN_FIT:SOL", kind="OPTION")])
    assert [r["status"] for r in res] == ["APPENDED", "DUPLICATE", "APPENDED", "APPENDED", "DUPLICATE", "APPENDED"]
    assert res[1]["seq"] == res[0]["seq"], "tekrar ilk satırı gösterir"
    assert led.counters["N_raw_cum"] == 2775 + 4
    assert led.append([_spec(1)])[0] == {"status": "DUPLICATE", "seq": res[0]["seq"], "row_sha": None,
                                         "key": ["TRIAL", "LIB_v1"]}
    assert TR.TrialsLedger(p).append([_spec(1)])[0]["seq"] == res[0]["seq"], "yeniden yüklemeden sonra da tekil"
    monthly = dict(_spec(1), lib_version="LIB_v1.2026-11")
    assert TR.TrialsLedger(p).append([monthly])[0]["status"] == "DUPLICATE", "aylık türeyen mühür aynı LIB ailesi (T3/T4)"
    other_lib = dict(_spec(1), lib_version="LIB_v2")
    assert TR.TrialsLedger(p).append([other_lib])[0]["status"] == "APPENDED", "yeni ana LIB sürümü yeni deneme"
    for bad in ("LIB_v1.2026-13", "LIB_v1-x", "lib_v1"):
        with pytest.raises(TR.TrialRowError):
            TR.TrialsLedger(p).append([dict(_spec(5), lib_version=bad)])
    led = TR.TrialsLedger(p)
    led.append([{"kind": "LOOK_A", "row_id": "V1@POOLED_P23", "look_no": 1, "at": AT, "p_day": 0.2, "q_bh": 0.5,
                 "verdict": "GEÇMEDİ"},
                {"kind": "LESSON_LOOK", "row_id": "cell-1|GRID", "look_no": 1, "at": AT},
                {"kind": "LESSON_LOOK", "row_id": "cell-1|GRID", "look_no": 1, "at": AT},
                {"kind": "EXPLORE_SEEN", "row_id": "LIB_v1", "n": 3745, "at": AT},
                {"kind": "ELIG", "row_id": "TS_7_LO", "scope": "COIN:SOL", "n": 140, "at": AT}])
    c = led.counters
    assert c == {"N_raw_cum": 2780, "N_lesson_cum": 1, "N_explore_cum": 3745}, c
    assert led.dsr_n() == 2780 + 1 + 3745
    assert TR.verify_chain(p.trials_file)["ok"]
    with pytest.raises(TR.TrialRowError):
        led.append([{"kind": "ELIG", "row_id": "TS_7_LO", "scope": "COIN:SOL", "n": 1, "at": AT, "seq": 9}])


def test_seal_entries_register_exactly_the_budget_and_n_at_seal_is_4513(tmp_path):
    """§2.5 + §3.5: mühür satırları (657 SPEC + 1.081 OPTION) defterde N_raw'ı tam 1.738 artırır; öncülle 4.513. Aynı
    spec_sha farklı kapsamda ayrı satırdır (DON_20_10_4h_LO ≡ K_D4: COIN_FIT seçeneği ve COIN satırı ayrı)."""
    mod = {v["variant_id"]: hashlib.sha256(v["variant_id"].encode()).hexdigest() for v in RG.LIB_V1["variants"]}
    entries = TR.seal_entries(mod)
    assert len(entries) == 1738 and sum(e["kind"] == "SPEC" for e in entries) == 657
    assert sum(e["kind"] == "OPTION" for e in entries) == 1081
    assert len({(e["spec_sha"], e["scope"]) for e in entries}) == 1738
    d4 = RG.spec_sha(RG.lib_spec("K_D4", mod["K_D4"]))
    assert {e["scope"] for e in entries if e["spec_sha"] == d4} == {"POOLED_OWN", *(f"COIN:{s}" for s in RG.P23),
                                                                    *(f"COIN_FIT:{s}" for s in RG.P23)}
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    res = led.append([dict(e, at=AT) for e in entries])
    assert all(r["status"] == "APPENDED" for r in res)
    assert led.counters["N_raw_cum"] == 4513 == RG.n_at_seal() and led.dsr_n() == 4513
    again = TR.TrialsLedger(p).append([dict(e, at=AT) for e in entries])
    assert all(r["status"] == "DUPLICATE" for r in again)
    assert TR.verify_chain(p.trials_file)["counters"]["N_raw_cum"] == 4513


@pytest.mark.parametrize("bad, msg", [
    ({"kind": "NOPE", "row_id": "x"}, "kind"),
    ({"kind": "SPEC", "row_id": "x", "scope": "s", "spec_sha": "ABC"}, "spec_sha"),
    ({"kind": "SPEC", "row_id": "x", "scope": "s"}, "spec_sha zorunlu"),
    ({"kind": "PRIOR", "row_id": "x", "n": -1}, "negatif"),
    ({"kind": "LOOK_A", "row_id": "x", "look_no": True}, "tam sayı"),
    ({"kind": "LOOK_A", "row_id": "x", "look_no": 0}, "look_no"),
    ({"kind": "LOOK_A", "row_id": "x", "look_no": 1, "p_day": float("nan")}, "sonlu"),
    ({"kind": "BIRTH", "row_id": "x", "N_raw_cum": 5}, "bilinmeyen"),
    ({"kind": "BIRTH", "row_id": "x", "params": {"o": object()}}, ""),
    ({"kind": "BIRTH", "row_id": "x", "params": {"s": "\ud800"}}, "UTF-8"),
    ({"kind": "BIRTH", "row_id": "x", "params": {"f": float("inf")}}, ""),
])
def test_row_validation(tmp_path, bad, msg):
    led = TR.TrialsLedger(_paths(tmp_path))
    with pytest.raises((TR.TrialRowError, TypeError, ValueError)) as ei:
        led.append([dict(bad, at=AT)])
    assert msg in str(ei.value)
    assert not _paths(tmp_path).trials_file.exists(), "geçersiz satır hiçbir şey yazmaz"


# ============================================================================ yalnız ekleme + sayaçlar (özellik)
def _random_entry(rng: random.Random, i: int) -> dict:
    k = rng.choice(["SPEC", "OPTION", "LOOK_A", "LESSON_LOOK", "EXPLORE_SEEN", "SELECT", "BIRTH", "HOLM", "FWD_READ"])
    if k in ("SPEC", "OPTION"):
        return _spec(rng.randrange(40), scope=rng.choice(["POOLED_P23", "CL_L1", "COIN_FIT:BTC"]), kind=k)
    if k in ("LOOK_A", "LESSON_LOOK", "FWD_READ"):
        return {"kind": k, "row_id": f"r{rng.randrange(10)}", "look_no": rng.randrange(1, 4), "at": AT}
    if k == "EXPLORE_SEEN":
        return {"kind": k, "row_id": f"LIB_v1.{rng.randrange(5)}", "n": rng.randrange(0, 50), "at": AT}
    if k == "SELECT":
        return {"kind": k, "row_id": "B@COIN_FIT:SOL", "scope": "COIN_FIT:SOL", "window": {"fold": rng.randrange(8)}, "at": AT}
    if k == "HOLM":
        return {"kind": k, "row_id": f"cand{rng.randrange(4)}", "window": {"month": f"2027-0{rng.randrange(1, 4)}"}, "at": AT}
    return {"kind": k, "row_id": f"cand{i}", "at": AT}


def test_append_only_prefix_and_counters_never_decrease_property(tmp_path):
    rng = random.Random(20261011)
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    prev_bytes = p.trials_file.read_bytes()
    for batch in range(25):
        led.append([_random_entry(rng, batch * 10 + j) for j in range(rng.randrange(1, 9))])
        now = p.trials_file.read_bytes()
        assert now.startswith(prev_bytes), "var olan baytlar asla değişmez"
        prev_bytes = now
    rows = list(TR.iter_rows(p.trials_file))
    last = (0, 0, 0)
    for r in rows:
        cur = tuple(r[c] for c in TR.COUNTERS)
        assert all(a <= b for a, b in zip(last, cur)), (r["seq"], last, cur)
        last = cur
    st = TR.verify_chain(p.trials_file)
    assert st["ok"] and tuple(st["counters"][c] for c in TR.COUNTERS) == last
    fresh = TR.TrialsLedger(p)
    assert fresh.counters == st["counters"]


def test_no_rewrite_path_in_the_trials_module():
    """Var olan baytları değiştiren tek yol `_atomic_replace`'tir: modülde TEK `os.replace` (orada), silme yalnız kendi
    geçici dosyası (`tmp`), O_TRUNC/O_APPEND yok, yerleşik `open` yalnız okuma kipinde, `os.fdopen` yalnız orada ve
    önek denetimi (`startswith`) orada."""
    tree = ast.parse((ROOT / "tradingbot" / "research_engine" / "trials.py").read_text(encoding="utf-8"))
    forbidden = {"rename", "renames", "remove", "truncate", "ftruncate", "write_bytes", "write_text", "write_json",
                 "write_json_gz", "write_jsonl_gz", "append_jsonl", "write_gzip_stream", "rmtree", "move", "copyfile"}
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    atomic = funcs["_atomic_replace"]
    in_atomic = {id(n) for n in ast.walk(atomic)}
    replaces = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("shutil",):
            raise AssertionError("shutil kullanımı")
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
        assert name not in forbidden, f"yasak çağrı {owner}.{name} satır {node.lineno}"
        if name == "replace" and owner == "os":
            replaces += 1
            assert id(node) in in_atomic, f"os.replace yalnız _atomic_replace içinde (satır {node.lineno})"
        if name == "unlink":
            assert owner == "os" and [ast.unparse(x) for x in node.args] == ["tmp"], f"silme yalnız tmp (satır {node.lineno})"
        if name == "open" and owner == "":
            mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
            assert mode in ("r", "rb"), f"open kipi {mode!r} satır {node.lineno}"
        if name == "fdopen":
            assert id(node) in in_atomic, f"fdopen yalnız _atomic_replace içinde (satır {node.lineno})"
        if name == "open" and owner == "os":
            flags = ast.unparse(node.args[1])
            assert "O_TRUNC" not in flags and "O_APPEND" not in flags and "O_WRONLY" not in flags, flags
    assert replaces == 1, "tek yeniden adlandırma yolu"
    assert any(isinstance(n, ast.Attribute) and n.attr == "startswith" for n in ast.walk(atomic)), "önek denetimi"
    assert any(isinstance(n, ast.Attribute) and n.attr == "fsync" for n in ast.walk(atomic)), "fsync"


def _snapshot(p: EnginePaths) -> tuple[bytes, bytes]:
    return p.trials_file.read_bytes(), p.trials_hwm_file.read_bytes()


def _no_tmp_left(p: EnginePaths) -> bool:
    return not [x for x in p.trials_dir.iterdir() if x.name.endswith(".tmp")]


def test_crash_at_every_write_point_leaves_the_ledger_intact(tmp_path, monkeypatch):
    """OOM/SIGKILL/ENOSPC/elektrik kesintisinin karşılığı: geçici dosya yazımı, fsync, geri okuma ya da os.replace'te
    hata → hedef bayt bayt aynı, zincir sağlam, geçici dosya kalmaz; sonraki ekleme normal çalışır."""
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    before = _snapshot(p)
    real_replace, real_fsync, real_read = os.replace, os.fsync, TR.read_raw

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    for name, patch in (("replace", lambda: monkeypatch.setattr(TR.os, "replace", boom)),
                        ("fsync", lambda: monkeypatch.setattr(TR.os, "fsync", boom)),
                        ("readback", lambda: monkeypatch.setattr(TR, "read_raw", lambda path: (
                            real_read(path)[:-7] if str(path).endswith(".tmp") else real_read(path))))):
        patch()
        with pytest.raises((OSError, TR.TrialsWriteError)):
            TR.TrialsLedger(p).append([_spec(1)])
        monkeypatch.setattr(TR.os, "replace", real_replace)
        monkeypatch.setattr(TR.os, "fsync", real_fsync)
        monkeypatch.setattr(TR, "read_raw", real_read)
        assert _snapshot(p) == before, name
        assert TR.verify_ledger(p)["ok"] and _no_tmp_left(p), name
    # defter değiştirildi ama HWM yazılamadı (ikinci os.replace'te çöküş): dosya HWM'nin ÖNÜNDE → sağlam
    calls = {"n": 0}

    def second_fails(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "I/O error")
        return real_replace(src, dst)
    monkeypatch.setattr(TR.os, "replace", second_fails)
    with pytest.raises(OSError):
        TR.TrialsLedger(p).append([_spec(1)])
    monkeypatch.setattr(TR.os, "replace", real_replace)
    assert p.trials_hwm_file.read_bytes() == before[1] and p.trials_file.read_bytes().startswith(before[0])
    st = TR.verify_ledger(p)
    assert st["ok"] and st["rows"] == 11 and _no_tmp_left(p)
    assert TR.TrialsLedger(p).append([_spec(2)])[0]["status"] == "APPENDED"
    hwm, err = TR.read_hwm(p.trials_hwm_file)
    assert err is None and hwm["rows"] == 12 and hwm["counters"]["N_raw_cum"] == 2775 + 2


def test_rollback_to_a_shorter_or_different_ledger_is_detected(tmp_path):
    """Satır sınırında kesme (eski yedeğin geri yüklenmesi, disk hatası, elle düzenleme) zincir olarak geçerlidir ama
    sayaçları düşürür: HWM ile ROLLED_BACK; eklenmez; sorgu gösterir. Aynı uzunlukta başka bir zincir de ROLLED_BACK."""
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    full = p.trials_file.read_bytes()
    lines = full.split(b"\n")[:-1]
    p.trials_file.write_bytes(b"\n".join(lines[:4]) + b"\n")
    assert TR.verify_chain(p.trials_file)["ok"], "HWM'siz bakışta önek geçerli bir zincirdir"
    st = TR.verify_ledger(p)
    assert st["code"] == "ROLLED_BACK" and not st["ok"] and st["counters"]["N_raw_cum"] < 2775, st
    with pytest.raises(TR.TrialsChainBroken, match="ROLLED_BACK"):
        TR.TrialsLedger(p).append([_spec(1)])
    from tradingbot.research_engine.query import run_query
    text = "\n".join(run_query(p, topic="trials")[0])
    assert "KIRIK (ROLLED_BACK" in text and "HWM (son yazım): 10 satır" in text and "DSR N 2.775" in text
    p.trials_file.write_bytes(b"")
    assert TR.verify_ledger(p)["code"] == "ROLLED_BACK", "boşaltılmış defter"
    # aynı uzunlukta başka bir geçerli zincir (farklı 'at')
    other = _paths(tmp_path / "o")
    TR.TrialsLedger(other).import_priors(at="2026-10-21T01:40:00+00:00")
    p.trials_file.write_bytes(other.trials_file.read_bytes())
    assert TR.verify_ledger(p)["code"] == "ROLLED_BACK"
    p.trials_file.write_bytes(full)
    assert TR.verify_ledger(p)["ok"]
    p.trials_hwm_file.write_bytes(b'{"schema":"trials_hwm_v1","rows":true}\n')
    st = TR.verify_ledger(p)
    assert st["code"] == "BAD_HWM" and not st["ok"]
    with pytest.raises(TR.TrialsChainBroken, match="BAD_HWM"):
        TR.TrialsLedger(p).append([_spec(1)])


def test_writers_serialise_on_the_flock(tmp_path):
    p = _paths(tmp_path)
    a = TR.TrialsLedger(p)
    a.import_priors(at=AT)
    done = threading.Event()
    res = {}

    def writer():
        res["r"] = TR.TrialsLedger(p).append([_spec(9)])
        done.set()
    with a._locked():
        t = threading.Thread(target=writer)
        t.start()
        assert not done.wait(0.4), "kilit tutulurken ikinci yazar beklemeli"
    t.join(30)
    assert done.is_set() and res["r"][0]["status"] == "APPENDED" and TR.verify_ledger(p)["rows"] == 11


# ============================================================================ kurcalama tespiti
def _built(tmp_path) -> tuple[EnginePaths, list[bytes]]:
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    led.append([_spec(i) for i in range(6)] + [{"kind": "LESSON_LOOK", "row_id": "c", "look_no": 1, "at": AT}])
    return p, p.trials_file.read_bytes().split(b"\n")[:-1]


def _write(p: EnginePaths, lines: list[bytes], tail: bytes = b"\n") -> None:
    p.trials_file.write_bytes(b"\n".join(lines) + tail)


def _rechain(rows: list[dict]) -> list[bytes]:
    prev = TR.GENESIS
    out = []
    for i, r in enumerate(rows, start=1):
        r = dict(r, seq=i, prev_sha=prev)
        r["row_sha"] = TR.row_sha(r)
        prev = r["row_sha"]
        out.append(TR.line_bytes(r)[:-1])
    return out


def test_tamper_detection_matrix(tmp_path):
    p, lines = _built(tmp_path)
    assert TR.verify_chain(p.trials_file)["ok"]
    rows = [json.loads(x) for x in lines]
    cases = {}
    r = dict(rows[11], params={"i": 99})
    cases["BAD_ROW_SHA"] = lines[:11] + [TR.line_bytes(r)[:-1]] + lines[12:]
    r = dict(rows[11], params={"i": 99})
    r["row_sha"] = TR.row_sha(r)
    cases["BAD_PREV"] = lines[:11] + [TR.line_bytes(r)[:-1]] + lines[12:]
    cases["BAD_SEQ"] = lines[:11] + lines[12:]                                   # satır silindi
    cases["BAD_SEQ_SWAP"] = lines[:11] + [lines[12], lines[11]] + lines[13:]
    cases["NOT_CANONICAL"] = lines[:5] + [lines[5].replace(b'","', b'", "', 1)] + lines[6:]
    cases["BAD_JSON"] = lines[:5] + [lines[5][:-3]] + lines[6:]
    bad = [dict(x) for x in rows]
    bad[12]["N_raw_cum"] -= 1                                                    # sayaç azaltıldı, zincir yeniden kuruldu
    cases["BAD_COUNTER"] = _rechain(bad)
    dup = [dict(x) for x in rows] + [dict(rows[11])]                            # tekil anahtar tekrarı, zincir yeniden kuruldu
    dup[-1]["N_raw_cum"] = dup[-2]["N_raw_cum"] + 1
    cases["DUPLICATE"] = _rechain(dup)
    for code, ls in cases.items():
        _write(p, ls)
        st = TR.verify_chain(p.trials_file)
        assert not st["ok"] and st["code"] == code.replace("_SWAP", ""), (code, st)
        with pytest.raises(TR.TrialsChainBroken):
            TR.TrialsLedger(p).append([_spec(77)])
    _write(p, lines, tail=b"\n" + lines[0][:40])                                 # yarım son satır
    st = TR.verify_chain(p.trials_file)
    full = TR.verify_bytes(b"\n".join(lines) + b"\n")
    assert st["code"] == "TORN_TAIL" and st["bad_seq"] == len(lines) + 1
    assert st["counters"] == full["counters"] and st["head_sha"] == full["head_sha"] and st["rows"] == len(lines), \
        "yarım satırda sayaçlar/baş doğrulanmış tam satırlarındır"
    with pytest.raises(TR.TrialsChainBroken):
        TR.TrialsLedger(p).append([_spec(77)])
    _write(p, lines[:5] + [lines[5][:-3]], tail=b"\n" + b'{"x')               # önekte de kırık: ilk kırık bildirilir
    assert TR.verify_chain(p.trials_file)["code"] == "BAD_JSON"


@pytest.mark.parametrize("label, mutate, code", [
    ("nan", lambda b: b.replace(b'"p_day":null', b'"p_day":NaN'), "BAD_JSON"),
    ("inf", lambda b: b.replace(b'"p_day":null', b'"p_day":Infinity'), "BAD_JSON"),
    ("ninf", lambda b: b.replace(b'"p_day":null', b'"p_day":-Infinity'), "BAD_JSON"),
    ("1e999", lambda b: b.replace(b'"p_day":null', b'"p_day":1e999'), "BAD_JSON"),
    ("surrogate", lambda b: b.replace(b'"verdict":null', b'"verdict":"\\ud800"'), "NOT_CANONICAL"),
    ("bad_utf8", lambda b: b.replace(b'"verdict":null', b'"verdict":"\xff"'), "BAD_JSON"),
    ("deep", lambda b: b.replace(b'"params":{', b'"params":{"d":' + b"[" * 100000 + b"]" * 100000 + b',', 1), "BAD_JSON"),
])
def test_non_json_values_are_codes_not_exceptions(tmp_path, label, mutate, code):
    p, lines = _built(tmp_path)
    i = 11
    bad = mutate(lines[i])
    assert bad != lines[i], label
    _write(p, lines[:i] + [bad] + lines[i + 1:])
    st = TR.verify_chain(p.trials_file)
    assert not st["ok"] and st["code"] == code and st["bad_seq"] == i + 1, (label, st)
    with pytest.raises(TR.TrialsChainBroken):
        TR.TrialsLedger(p).append([_spec(77)])
    from tradingbot.research_engine.query import run_query
    lines_q, rc = run_query(p, topic="trials", like="V1")
    assert rc == 0 and any("KIRIK" in x for x in lines_q)
    "\n".join(lines_q).encode("utf-8")


@pytest.mark.parametrize("field, value", [("seq", True), ("N_raw_cum", "float"), ("N_lesson_cum", -0.0),
                                          ("prev_sha", "upper"), ("row_sha", "short")])
def test_chain_fields_must_be_plain_ints_and_hex(tmp_path, field, value):
    """`seq=true`, `N_raw_cum=2775.0` gibi değerler Python'da eşit görünür; zincir yeniden hesaplansa bile BAD_SCHEMA."""
    p, lines = _built(tmp_path)
    rows = [json.loads(x) for x in lines]
    i = 0 if field == "seq" else 3
    r = dict(rows[i])
    if value == "float":
        r[field] = float(r[field])
    elif value == "upper":
        r[field] = r[field].upper() if r[field] != TR.GENESIS else "A" * 64
    elif value == "short":
        r[field] = r[field][:63]
    else:
        r[field] = value
    if field != "row_sha":
        r["row_sha"] = TR.row_sha(r)
    raw = TR.RG.canonical_bytes(r)
    _write(p, lines[:i] + [raw] + lines[i + 1:])
    st = TR.verify_chain(p.trials_file)
    assert not st["ok"] and st["code"] == "BAD_SCHEMA" and st["bad_seq"] == i + 1, (field, st)


def test_concurrent_change_between_load_and_append_is_refused(tmp_path):
    p = _paths(tmp_path)
    a, b = TR.TrialsLedger(p), TR.TrialsLedger(p)
    a.load()
    b.load()
    a.append([_spec(1)])
    with pytest.raises(TR.TrialsChanged):
        b.append([_spec(2)])
    b.load()
    assert b.append([_spec(2)])[0]["status"] == "APPENDED" and TR.verify_chain(p.trials_file)["rows"] == 2


def test_writes_stay_under_the_research_root(tmp_path):
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    files = sorted(x for x in (tmp_path / "data").rglob("*") if x.is_file())
    assert files == sorted([p.trials_file, p.trials_hwm_file, p.trials_lock_file])
    assert all(p.is_under_research(f) for f in files)
    hwm, err = TR.read_hwm(p.trials_hwm_file)
    st = TR.verify_chain(p.trials_file)
    assert err is None and (hwm["rows"], hwm["head_sha"], hwm["counters"]) == (st["rows"], st["head_sha"], st["counters"])


# ============================================================================ engine-query trials
def test_engine_query_trials_is_read_only_bounded_and_finds_variants_rows_and_priors(tmp_path, monkeypatch, capsys):
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.import_priors(at=AT)
    m = "e" * 64
    led.append([{"kind": "SPEC", "spec_sha": RG.spec_sha(RG.lib_spec(v["variant_id"], m)), "scope": "POOLED_P23",
                 "row_id": f"{v['variant_id']}@POOLED_P23", "at": AT, "params": {"variant_id": v["variant_id"]}}
                for v in RG.LIB_V1["variants"] if v["family"] in ("B", "C", "D") and not v["alias_of"]])
    before = _tree(tmp_path)
    from tradingbot.research_engine.query import run_query
    lines, code = run_query(p, topic="trials")
    assert code == 0 and len(lines) <= 150 and len("\n".join(lines).encode()) <= 4096
    text = "\n".join(lines)
    assert "Zincir: SAĞLAM" in text and "N_raw 2.800" in text and "Kapı A satırı 818" in text and "1.738" in text
    lines, _ = run_query(p, topic="trials", like="TS_")
    text = "\n".join(lines)
    assert "TS_7_LO" in text and "Defter satırı eşleşen: 10" in text
    lines, _ = run_query(p, topic="trials", like="paxg")
    text = "\n".join(lines)
    assert "ÖNCÜL — KANIT YOK" in text and "PAXG hafta sonu" in text and "gold_v2" in text
    sp = RG.spec_sha(RG.lib_spec("DON_20_10_4h_LO", m))
    lines, _ = run_query(p, topic="trials", like=sp[:10])
    assert sum(1 for x in lines if x.startswith("  #")) == 0, "K_D4 POOLED_OWN'da; D ailesinin alias'ı havuzda satır açmaz"
    lines, _ = run_query(p, topic="trials", like=RG.spec_sha(RG.lib_spec("DON_55_20_1d_LS", m))[:10])
    assert sum(1 for x in lines if x.startswith("  #")) == 1
    big, _ = run_query(p, topic="trials", like="_")
    assert len(big) <= 150 and len("\n".join(big).encode()) <= 4096 and big[-1].startswith("…")
    assert _tree(tmp_path) == before, "sorgu hiçbir şey yazmaz"
    lines = p.trials_file.read_bytes().split(b"\n")
    p.trials_file.write_bytes(b"\n".join(lines[:3] + lines[4:]))
    text = "\n".join(run_query(p, topic="trials")[0])
    assert "Zincir: KIRIK (BAD_SEQ" in text and "TRIALS_CHAIN_BROKEN" in text
    p.trials_file.write_bytes(p.trials_file.read_bytes() + b'{"yarim\n')
    lines, code = run_query(p, topic="trials", like="TS_")
    assert code == 0 and any("KIRIK" in x for x in lines), "bozuk satırda sorgu çökmez"
    monkeypatch.setenv("TRADINGBOT_DATA", str(p.data))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(p.state))
    import tradingbot.cli as cli

    def no_config(*a, **k):
        raise AssertionError("engine-query config yüklemez")
    monkeypatch.setattr(cli, "load_config", no_config)
    rc = cli.main(["engine-query", "trials", "--like", "BOX_n20"])
    out = capsys.readouterr().out
    assert rc == 0 and "BOX_n20_mid_ms0.5" in out and len(out.strip().splitlines()) <= 150


def test_engine_query_trials_without_a_ledger(tmp_path):
    from tradingbot.research_engine.query import run_query
    lines, code = run_query(_paths(tmp_path), topic="trials", like="fonlama")
    text = "\n".join(lines)
    assert code == 0 and "Defter: YOK/BOŞ" in text and "FUND_CARRY" in text and "UNSPECIFIED" in text
    assert not (tmp_path / "data").exists()
    assert TR.QUERY_MAX_LINES == 150 and TR.QUERY_MAX_BYTES == 4096
    from tradingbot.research_engine import report as RP
    assert (RP.QUERY_MAX_LINES, RP.QUERY_MAX_BYTES) == (TR.QUERY_MAX_LINES, TR.QUERY_MAX_BYTES)


def test_engine_query_counts_every_match_and_shows_the_newest(tmp_path):
    """2.000'lik kesme yok: bütün eşleşmeler sayılır, en yeni 40 satır en yeniden geriye gösterilir."""
    p = _paths(tmp_path)
    led = TR.TrialsLedger(p)
    led.append([{"kind": "LOOK_A", "row_id": f"R{i}@POOLED_P23", "look_no": 1, "at": AT} for i in range(2100)])
    from tradingbot.research_engine.query import run_query
    lines, rc = run_query(p, topic="trials", like="look_a")
    shown = [x for x in lines if x.startswith("  #")]
    assert rc == 0 and "Defter satırı eşleşen: 2100 (en yeni 40)" in lines, lines[:12]
    assert shown[0].startswith("  #2100 LOOK_A R2099@") and len(shown) <= 40

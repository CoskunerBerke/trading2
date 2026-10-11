# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P3a — deneme defteri `trials/trials.jsonl` (docs/ENGINE_BRAIN_V1.md §3.5, §2.6; §8.1 P3a kabulü).

* Yalnız ekleme: her eklemeden önceki baytlar sonraki dosyanın öneki; modülde yeniden yazma yolu yok (AST).
* Hash zinciri + kurcalama tespiti: alan değişikliği, satır silme/yer değiştirme/ekleme, yarım satır, boşluk oynaması,
  sayaç oynaması (zincir yeniden hesaplansa bile), tekil anahtar tekrarı; kırık zincire eklenmez.
* Sayaçlar hiç azalmaz (özellik testi) ve yeniden hesapla eşittir; DSR N = N_raw + N_lesson + N_explore.
* Öncül = 2.775 (`kind=PRIOR`, bir kez); `spec_sha` tekilleştirme (aynı lib/spec/kapsam bir kez, farklı kapsam ayrı satır).
* `engine-query trials [--like]`: salt-okunur, ≤ 150 satır / ≤ 4 KB, config yüklemez, kilit almaz.
"""
from __future__ import annotations

import ast
import hashlib
import json
import random
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
    other_lib = dict(_spec(1), lib_version="LIB_v2")
    assert TR.TrialsLedger(p).append([other_lib])[0]["status"] == "APPENDED", "farklı LIB sürümü yeni deneme"
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
])
def test_row_validation(tmp_path, bad, msg):
    led = TR.TrialsLedger(_paths(tmp_path))
    with pytest.raises((TR.TrialRowError, TypeError)) as ei:
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
    """Modülde var olan baytları yeniden yazan/kesen/silen yol yoktur: tek yazım `os.open(... O_APPEND ...)`."""
    tree = ast.parse((ROOT / "tradingbot" / "research_engine" / "trials.py").read_text(encoding="utf-8"))
    forbidden_attrs = {"replace", "rename", "remove", "unlink", "truncate", "ftruncate", "write_bytes", "write_text",
                       "write_json", "write_json_gz", "write_jsonl_gz", "append_jsonl", "write_gzip_stream", "rmtree"}
    opens = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
            if name in forbidden_attrs and owner in ("os", "shutil", "self", "paths", "Path", "") and name != "replace":
                raise AssertionError(f"yasak çağrı {owner}.{name} satır {node.lineno}")
            if name == "replace" and owner in ("os", "shutil"):
                raise AssertionError(f"os.replace satır {node.lineno}")
            if name == "open" and owner == "":
                mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
                assert mode in ("r", "rb"), f"open kipi {mode!r} satır {node.lineno}"
            if name == "open" and owner == "os":
                opens += 1
                flags = ast.unparse(node.args[1])
                assert "O_APPEND" in flags and "O_TRUNC" not in flags, flags
    assert opens == 1, "tek yazım yolu"


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
    assert st["code"] == "TORN_TAIL" and st["bad_seq"] == len(lines) + 1
    with pytest.raises(TR.TrialsChainBroken):
        TR.TrialsLedger(p).append([_spec(77)])


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
    files = [x for x in (tmp_path / "data").rglob("*") if x.is_file()]
    assert files == [p.trials_file] and p.is_under_research(p.trials_file)


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

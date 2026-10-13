# -*- coding: utf-8 -*-
"""Mühür S1b (sahip kararı 2026-10-13; `research_engine/seal_s1b.py`): fonlama mark kaynağı (`FUNDING_MARK_V1`) ve
dondurulmuş 40 coin'lik giriş evreni (`ENTRY_UNIVERSE_V1`) S1'e dokunulmadan ayrı adlı mühürle sabitlenir.

Kabul: sha'lar sabit; her alan değişikliği sha'yı değiştirir (özellik testi); `S1B_SEAL_MS` mühür commit'inden önce
değil (git); S1 (registry.py, REGISTRY_SHA, T_SEAL_MS, sekiz sha) 43a0bf0'daki değerlerle bayt-özdeş; asla-gevşetme
yönleri (`fixed`) her tanım alanını kapsar; S1b eksik ya da tutmuyorsa çekirdek, keşif ve WF açık nedenle çalışmaz.
"""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from tradingbot.research_engine import explore as EX
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import seal_s1b as S
from tradingbot.research_engine import synthetic as SY
from tradingbot.research_engine import universe as U
from tradingbot.research_engine import wf as WF

ROOT = Path(__file__).resolve().parents[1]
GIT = ("git", "-c", "log.showSignature=false")

#: S1 — 43a0bf0'daki (düzeltme mührü 9) değerler; S1b bunlara DOKUNMAZ
S1_AT_43A0BF0 = {
    "LIB_V1": "ae237b55c6df7014a52649915342eeb0bd67d23a18edcee5cf3e4b8c773df2e3",
    "WF_PROTOCOL_V1": "085ca94c3a8abc007327a15c217eea7d56a5b9af7b5075ec42714d5424af46b4",
    "STATS_V1": "fda79c7565db098f76379d5037443e119723367fdba535d8a8ed9897c57bf60e",
    "LESSON_REGISTRY_V1": "8fbed7f5e33e21a02ec9547bfd84ab7ad0a798d9cc25908aa99911244ad58836",
    "PIT_RULES_V1": "59d0628c975e42aaa7810e231ce0704e5113b6a2bde4fd59fb29e65d8fef48b7",
    "PRIOR_TRIALS_V1": "ad6d8903e66034ce96cafe006f0a071e254968308627ab9998b0464f96984921",
    "CONFIG_EPOCHS_V1": "38126e55acda08dc414a3aec209f24933ea025a2f98843f4f8ad9f02f2172e65",
    "THRESHOLD_DIRECTIONS_V1": "6ccbdfcecd55c240ff445b7ab39abedd6696587d54b04712ab0c28fc1e9fb56f",
}
REGISTRY_SHA_AT_43A0BF0 = "3e700fa03e455237c5ef45e29b85e5a810a9f8f7bd9dfe88465e543a5620beaf"
T_SEAL_MS_AT_43A0BF0 = 1791885600000
S1_FILES = ("tradingbot/research_engine/registry.py", "tradingbot/research_engine/prior_trials.json",
            "tradingbot/research_engine/library/config_epochs.json")

#: S1b sabitleri (mühür commit'i)
S1B_SHAS_PINNED = {
    "FUNDING_MARK_V1": "165057d63cf6818464d773916761a6b4a143883a20da1221c15b12de5a80a5dd",
    "ENTRY_UNIVERSE_V1": "f1d5744789fa585e6bc5a059168c9bdd1bb5138902dbacbc90d81139362ee3f6",
    "S1B_DIRECTIONS_V1": "106f15397c60e5f1ebb31d64b0bcea503fbf9c19349cd23e286374dd3f221c29",
}
S1B_SEAL_MS_PINNED = 1791908100000
S1B_SHA_PINNED = "b3254fe69f72c48dda1ec372e6230d8b4a8106bac61043c4f5b88b4db9d08c31"

COINS40 = ["BTC", "ETH", "ZEC", "SOL", "NEAR", "UNI", "XRP", "ONE", "BNB", "ARB", "DOGE", "LSK", "SUI", "ENA", "G", "WLD",
           "ADA", "ONDO", "1000PEPE", "COTI", "LINK", "REZ", "DASH", "AAVE", "TAO", "BCH", "AVAX", "DOT", "FIL", "APT", "SYN",
           "LTC", "XLM", "INJ", "FET", "TRX", "XMR", "1000SHIB", "OP", "KSM"]
#: tanım olmayan (betimsel) alanlar: yön tablosu kapsamaz, ama sha'ya girer
META_KEYS = {"id", "decision", "reading_tr", "applies_to", "source"}


def _git(*args: str) -> str:
    return subprocess.run([*GIT, "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120, check=True).stdout


def _git_ok() -> bool:
    try:
        _git("rev-parse", "HEAD")
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


# ============================================================================ S1 dokunulmadı
def test_s1_is_byte_identical_to_fixup_9():
    assert dict(RG.SHAS) == S1_AT_43A0BF0
    assert (RG.REGISTRY_SHA, RG.T_SEAL_MS) == (REGISTRY_SHA_AT_43A0BF0, T_SEAL_MS_AT_43A0BF0)
    assert S.S1B_SEAL["extends_s1_registry_sha"] == REGISTRY_SHA_AT_43A0BF0
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    try:
        _git("cat-file", "-e", "43a0bf0^{commit}")
    except subprocess.CalledProcessError:
        pytest.skip("43a0bf0 bu klonda yok")
    for f in S1_FILES:                                  # çalışma ağacı ve HEAD, 43a0bf0 ile bayt-aynı
        assert _git("diff", "--no-textconv", "43a0bf0", "--", f) == "", f
        assert _git("diff", "--no-textconv", "43a0bf0", "HEAD", "--", f) == "", f


# ============================================================================ S1b sabitleri ve zaman
def test_s1b_shas_are_pinned_and_the_seal_checks():
    assert dict(S.SHAS) == S1B_SHAS_PINNED == {k: v for k, v in S.PINNED.items() if k != "S1B"}
    assert (S.FUNDING_MARK_V1_SHA, S.ENTRY_UNIVERSE_V1_SHA, S.S1B_DIRECTIONS_V1_SHA) == tuple(S1B_SHAS_PINNED.values())
    assert S.S1B_SEAL_MS == S1B_SEAL_MS_PINNED and S.S1B_SHA == S1B_SHA_PINNED == S.PINNED["S1B"]
    assert RG.thaw(S.S1B_SEAL) == {"seal_id": "S1b", "shas": S1B_SHAS_PINNED, "s1b_seal_ms": S1B_SEAL_MS_PINNED,
                                   "extends_s1_registry_sha": REGISTRY_SHA_AT_43A0BF0}
    assert S.check() == (True, "OK") and KN.s1b_status() == (True, "OK")
    # mühürlü değerler: çekirdeğin uyguladığı kural ve keşfin evreni bunlardır
    fm = S.FUNDING_MARK_V1
    assert {k: fm[k] for k in KN.FUNDING_MARK_IMPL} == KN.FUNDING_MARK_IMPL
    assert (fm["rest_mark_check"]["max_rel_diff"], fm["rest_mark_check"]["flag"]) == (KN.REST_MARK_MAX_REL_DIFF,
                                                                                       KN.FLAG_FUNDING_MARK_MISMATCH)
    assert list(S.ENTRY_UNIVERSE_V1["coins"]) == COINS40 == list(U.ENTRY_UNIVERSE_FROZEN)
    assert S.ENTRY_UNIVERSE_V1["size"] == len(COINS40) == RG.LIB_V1["explore"]["entry_universe_size"]
    assert set(RG.LIB_V1["p23"]["symbols"]) <= set(COINS40)
    with pytest.raises(TypeError):
        S.FUNDING_MARK_V1["mark_source"] = "x"                              # derin donuk


def test_s1b_seal_ms_is_not_before_its_commit():
    """`S1B_SEAL_MS` ≥ seal_s1b.py'yi değiştiren en son commit'in işleyen zamanı (dosyayı değiştiren her commit yeniden
    mühürdür) ve en geç 24 saat sonra; tam dakika; S1'in T_SEAL_MS'inden sonra."""
    t = S.S1B_SEAL_MS
    assert t % 60_000 == 0 and t > RG.T_SEAL_MS
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    out = _git("log", "-1", "--no-textconv", "--format=%ct %H", "--", "tradingbot/research_engine/seal_s1b.py").split()
    if not out:
        pytest.skip("seal_s1b.py henüz commit edilmedi")
    ct, sha = int(out[0]), out[1]
    assert ct * 1000 <= t, f"S1B_SEAL_MS {t} seal_s1b.py'yi değiştiren {sha[:12]} commit'inden ({ct}) önce: yeniden mühürle"
    assert t - ct * 1000 <= 24 * 3600 * 1000, (t, ct, sha[:12])


# ============================================================================ özellik: her alan sha'yı değiştirir
def _leaves(x, path=()):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _leaves(v, path + (k,))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _leaves(v, path + (i,))
    else:
        yield path, x


def _set(doc, path, value):
    cur = doc
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def _mutate(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return v + 1 if isinstance(v, int) else v * 1.0001 + 1e-9
    if isinstance(v, str):
        return v + "·"
    return "x"


def test_any_field_change_changes_the_sha():
    for name, reg in S.REGISTRIES.items():
        base = RG.thaw(reg)
        sha0 = RG.sha256_canonical(base)
        n = 0
        for path, v in _leaves(base):
            doc = copy.deepcopy(base)
            _set(doc, path, _mutate(v))
            assert RG.sha256_canonical(doc) != sha0, (name, path)
            n += 1
        doc = copy.deepcopy(base)
        doc["extra"] = 1
        assert RG.sha256_canonical(doc) != sha0
        for k in base:
            doc = copy.deepcopy(base)
            del doc[k]
            assert RG.sha256_canonical(doc) != sha0, (name, k)
        assert n >= 4
    # coin sırası da kimliktir (config sırası mühürlü)
    eu = RG.thaw(S.ENTRY_UNIVERSE_V1)
    eu["coins"] = list(reversed(eu["coins"]))
    assert RG.sha256_canonical(eu) != S.ENTRY_UNIVERSE_V1_SHA
    # S1b sha'sı mühür anına ve genişlettiği S1'e bağlı
    assert RG.sha256_canonical(S.seal_doc(seal_ms=S.S1B_SEAL_MS + 60_000)) != S.S1B_SHA
    assert RG.sha256_canonical(S.seal_doc(s1_registry_sha="0" * 64)) != S.S1B_SHA


# ============================================================================ asla gevşetme (yönler fixed)
def test_never_loosen_directions_cover_every_definition_field_and_catch_changes():
    assert set(S.S1B_DIRECTIONS_V1) == set(S.S1B_VERSIONS)
    for fam, versions in S.S1B_VERSIONS.items():
        v1 = versions[0]
        dirs = S.S1B_DIRECTIONS_V1[fam]
        assert set(dirs.values()) == {RG.FIXED}                                 # sahip: ikisi de fixed
        defs = set()
        for path, _v in _leaves(RG.thaw(v1)):
            if path[0] not in META_KEYS:
                defs.add(".".join(str(p) for p in path if not isinstance(p, int)))
        assert defs <= set(dirs), (fam, defs - set(dirs))
        for path in dirs:
            RG.get_path(v1, path)
        assert RG.looser_than(v1, v1, dirs) == []
        for path in dirs:
            old = RG.thaw(RG.get_path(v1, path))
            changed = RG.thaw(v1)
            par, last = _parent(changed, path)
            par[last] = (old + ["NEW"]) if isinstance(old, list) else _mutate(old)
            bad = RG.looser_than(v1, changed, dirs)
            assert len(bad) == 1 and bad[0].startswith(path + ":"), (fam, path, bad)
            gone = RG.thaw(v1)
            par, last = _parent(gone, path)
            del par[last]
            assert RG.looser_than(v1, gone, dirs) == [f"{path}: eşik kaldırıldı"]
        for later in versions[1:]:
            assert RG.looser_than(v1, later, dirs) == [], fam


def _parent(doc, path):
    *head, last = path.split(".")
    cur = doc
    for p in head:
        cur = cur[p]
    return cur, last


# ============================================================================ eksik/tutmayan mühür → çalışma yok
def test_mismatched_or_missing_s1b_refuses_kernel_explore_and_wf(tmp_path, monkeypatch):
    data = SY.d4_world(3, 600)
    var = KN.make_variant("DON_20_10_4h_LS")
    assert KN.run(var, data).trades                                          # mühür tutarken çalışır
    w = SY.explore_world(["BTC"], seed=2, seal_ms=RG.T_SEAL_MS, fwd_days=2)
    cd = {"BTC": EX.inputs_from_rows("BTC", w["BTC"]["bars"], w["BTC"]["funding"], 0.001,
                                     now_ms=RG.T_SEAL_MS + 3 * 86_400_000, markpx=w["BTC"]["markpx_1h"])}
    cases = [("PINNED", dict(S.PINNED, FUNDING_MARK_V1="f" * 64), "S1B_SHA_MISMATCH"),
             ("PINNED", dict(S.PINNED, S1B="e" * 64), "S1B_SHA_MISMATCH"),
             ("ENTRY_UNIVERSE_V1", RG.freeze(dict(RG.thaw(S.ENTRY_UNIVERSE_V1), coins=COINS40[:-1] + ["NEW"])),
              "S1B_SHA_MISMATCH")]
    for attr, value, code in cases:
        with monkeypatch.context() as m:
            m.setattr(S, attr, value)
            if attr != "PINNED":
                m.setattr(S, "REGISTRIES", {**S.REGISTRIES, attr: value})
            ok, why = KN.s1b_status()
            assert not ok and why.startswith(code), why
            with pytest.raises(KN.SealError, match=code):
                KN.run(var, data)
            rep = EX.explore_step(tmp_path / f"r_{code}_{attr}", cd, entry_symbols=COINS40, only=["KEL_20_2_1h"])
            assert rep["status"] == {"REFUSED": 1} and rep["refused"]["reason"] == code
            with pytest.raises(WF.EntryUniverseRefused, match=code):
                WF.require_entry_universe(COINS40)
    # mühürlü okuma çekirdeğin uyguladığından farklı → çekirdek çalışmaz
    with monkeypatch.context() as m:
        m.setattr(KN, "FUNDING_MARK_IMPL", dict(KN.FUNDING_MARK_IMPL, mark_fallback="NONE"))
        assert KN.s1b_status()[1].startswith("S1B_READING_NOT_IMPLEMENTED")
        with pytest.raises(KN.SealError):
            KN.run(var, data)
    # dosya yok → çekirdek, keşif ve WF açık nedenle durur
    with monkeypatch.context() as m:
        m.setattr(KN, "S1B", None)
        m.setattr(U, "_S1B", None)
        assert KN.s1b_status() == (False, "S1B_MISSING: research_engine/seal_s1b.py yok")
        with pytest.raises(KN.SealError, match="S1B_MISSING"):
            KN.run(var, data)
        rep = EX.explore_step(tmp_path / "missing", cd, entry_symbols=COINS40, only=["KEL_20_2_1h"])
        assert rep["refused"]["reason"] == "S1B_MISSING" and not list((tmp_path / "missing").glob("*/*"))
        lb = "\n".join(EX.leaderboard_lines({}, EX.read_run(tmp_path / "missing")))
        assert "S1B_MISSING" in lb and EX.verdict_word_violations(lb.splitlines()) == []
        with pytest.raises(WF.EntryUniverseRefused, match="S1B_MISSING"):
            WF.require_entry_universe(COINS40)
    assert KN.s1b_status() == (True, "OK")

# -*- coding: utf-8 -*-
"""Mühür S1c (P3f ders okumaları; docs/ENGINE_BRAIN_V1.md §8.4 P3f notu).

Kabul: sha'lar sabit; her alan değişikliği sha'yı değiştirir (özellik testi); `S1C_SEAL_MS` mühür commit'inden önce değil
(git); S1 (registry.py) ve S1b (seal_s1b.py) dokunulmadı ve S1c ikisini de genişletir; asla-gevşetme yönleri her tanım
alanını kapsar ve her değişikliği yakalar; tutmayan mühürde ders katmanı çalışmaz (lessons testinde).
"""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import seal_s1b as S1B
from tradingbot.research_engine import seal_s1c as S

ROOT = Path(__file__).resolve().parents[1]
GIT = ("git", "-c", "log.showSignature=false")
REGISTRY_SHA_S1 = "3e700fa03e455237c5ef45e29b85e5a810a9f8f7bd9dfe88465e543a5620beaf"
S1B_SHA_PINNED = "b3254fe69f72c48dda1ec372e6230d8b4a8106bac61043c4f5b88b4db9d08c31"
S1C_SHAS_PINNED = {
    "LESSON_READINGS_V1": "b1d069233fe71472a45912e05f8a1bb2844a2603846b29b2c75d9cb7e01a008f",
    "S1C_DIRECTIONS_V1": "01ef6b0f737b27598e5756a86258ba128042c33db3924a15e3a42d285d03ef3f",
}
S1C_SEAL_MS_PINNED = 1791914160000
S1C_SHA_PINNED = "277922ca4c84b29017e93b05e5e3947bce57b5e890ef8413828f03917a52de95"
META = ("id", "extends", "decision")
BASE_COMMIT = "9132798"              # S1b mühür commit'i (P3f'nin dalı buradan)


def _git(*args: str) -> str:
    return subprocess.run([*GIT, "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120, check=True).stdout


def _git_ok() -> bool:
    try:
        _git("rev-parse", "HEAD")
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _leaves(x, path=()):
    if isinstance(x, dict):
        for k in sorted(x):
            yield from _leaves(x[k], path + (k,))
    else:
        yield path, x


def _set(doc, path, v):
    cur = doc
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = v


def _mutate(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return v + 1
    if isinstance(v, str):
        return v + "x"
    if isinstance(v, list):
        return v + ["NEW"]
    if isinstance(v, dict):
        return {**v, "NEW": 1}
    return "x"


def test_s1c_shas_are_pinned_and_extend_untouched_s1_and_s1b():
    assert dict(S.SHAS) == S1C_SHAS_PINNED == {k: v for k, v in S.PINNED.items() if k != "S1C"}
    assert S.S1C_SEAL_MS == S1C_SEAL_MS_PINNED and S.S1C_SHA == S1C_SHA_PINNED == S.PINNED["S1C"]
    assert RG.thaw(S.S1C_SEAL) == {"seal_id": "S1c", "shas": S1C_SHAS_PINNED, "s1c_seal_ms": S1C_SEAL_MS_PINNED,
                                   "extends_s1_registry_sha": REGISTRY_SHA_S1, "extends_s1b_sha": S1B_SHA_PINNED}
    assert (RG.REGISTRY_SHA, S1B.S1B_SHA) == (REGISTRY_SHA_S1, S1B_SHA_PINNED)
    assert S.check() == (True, "OK") and S1B.check() == (True, "OK")
    with pytest.raises(TypeError):
        S.LESSON_READINGS_V1["looks"]["order"] = "x"                              # derin donuk
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    try:
        _git("cat-file", "-e", f"{BASE_COMMIT}^{{commit}}")
    except subprocess.CalledProcessError:
        pytest.skip(f"{BASE_COMMIT} bu klonda yok")
    for f in ("tradingbot/research_engine/registry.py", "tradingbot/research_engine/seal_s1b.py",
              "tradingbot/research_engine/prior_trials.json", "tradingbot/research_engine/library/config_epochs.json"):
        assert _git("diff", "--no-textconv", BASE_COMMIT, "--", f) == "", f        # mühürlü dosyalar bayt-aynı


def test_s1c_seal_ms_is_not_before_its_commit():
    t = S.S1C_SEAL_MS
    assert t % 60_000 == 0 and t > S1B.S1B_SEAL_MS > RG.T_SEAL_MS
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    out = _git("log", "-1", "--no-textconv", "--format=%ct %H", "--", "tradingbot/research_engine/seal_s1c.py").split()
    if not out:
        pytest.skip("seal_s1c.py henüz işlenmedi")
    ct, sha = int(out[0]), out[1]
    assert ct * 1000 <= t, f"S1C_SEAL_MS {t} seal_s1c.py'yi değiştiren {sha[:12]} commit'inden ({ct}) önce: yeniden mühürle"
    assert t - ct * 1000 <= 24 * 3600 * 1000


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
        for k in base:
            doc = copy.deepcopy(base)
            del doc[k]
            assert RG.sha256_canonical(doc) != sha0, (name, k)
        assert n >= 10
    assert RG.sha256_canonical(S.seal_doc(seal_ms=S.S1C_SEAL_MS + 60_000)) != S.S1C_SHA
    assert RG.sha256_canonical(S.seal_doc(s1_registry_sha="0" * 64)) != S.S1C_SHA
    assert RG.sha256_canonical(S.seal_doc(s1b_sha="0" * 64)) != S.S1C_SHA


def test_never_loosen_directions_cover_every_definition_field_and_catch_changes():
    v1 = S.LESSON_READINGS_V1
    dirs = S.S1C_DIRECTIONS_V1["LESSON_READINGS"]
    assert set(S.S1C_DIRECTIONS_V1) == set(S.S1C_VERSIONS)
    for path, _v in _leaves(RG.thaw(v1)):
        if path[0] in META or path[-1] == "reading_tr":
            continue
        dotted = ".".join(path)
        assert any(dotted == d or dotted.startswith(d + ".") for d in dirs), dotted
    assert RG.looser_than(v1, v1, dirs) == []
    for path, d in dirs.items():
        changed = RG.thaw(v1)
        _set(changed, tuple(path.split(".")), _mutate(RG.thaw(RG.get_path(v1, path))))
        bad = RG.looser_than(v1, changed, dirs)
        if d == RG.MIN:                                     # yükseltmek sıkılaştırmadır (kabul), düşürmek gevşetme
            assert bad == []
            low = RG.thaw(v1)
            _set(low, tuple(path.split(".")), RG.get_path(v1, path) - 1)
            bad = RG.looser_than(v1, low, dirs)
        assert len(bad) == 1 and bad[0].startswith(path + ":"), (path, bad)
        gone = RG.thaw(v1)
        *head, last = path.split(".")
        par = gone
        for p in head:
            par = par[p]
        del par[last]
        assert RG.looser_than(v1, gone, dirs) == [f"{path}: eşik kaldırıldı"]
    assert dirs["forward_validation.first_complete_months"] == RG.MIN
    assert set(dirs.values()) == {RG.FIXED, RG.MIN}

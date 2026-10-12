# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P3a — mühür S1 (docs/ENGINE_BRAIN_V1.md §2–§4, §8.1 P3a kabulü, §8.2).

* Sha'lar sabittir (bu dosyada ve belgenin §8.2 tablosunda; ikisi aynı commit'te). DEĞİŞİRSE CI kırılır: mühürden sonra
  her değişiklik `_v2` + yeni `SPEC`/`OPTION` satırları + belgeye tarihli "Değişiklik" notudur.
* Özellik: HER alan değişikliği (yaprak, anahtar silme, liste öğesi silme) sha'yı değiştirir; kayıtlar donuktur.
* Bütçe tablosu (818 / 1.738 ve satır satır), öncül 2.775, mühürde N 4.513, 106 varyant, ızgara 47, keşif 3.745 ve S5
  öncesi "Oto" sütunu 64 KAYITTAN yeniden üretilir ve belgenin sayılarına eşittir (sayılar bu testte bağımsız yazılıdır).
* `T_SEAL_MS` mühürlü içeriği değiştiren en son commit'in işleyen zamanından önce olamaz (git'ten; tarih elle ayarlanmaz).
* §8.5 boşluklarına sahip kararı (2026-10-11, "Önerilenleri kabul et"): önerisi olan her boşluk dolu (`decided`,
  `reading_tr`); kalan boşluklar yalnız B9 (spread, XSEC) ve altın stopları → 13 varyant / 25 Kapı A satırı çalıştırılmaz.
* `spec_sha` tekilleştirme (alias'lı ızgara üyesi tabanıyla aynı sha; örtü faktör parametreleri spec'te), "asla gevşetme"
  iskeleti (yön tablosu S1'de mühürlü, her eşiği ve kapı-dışı eşik benzerlerini kapsar; gevşetilmiş bir kopya
  yakalanır), UNSPECIFIED boşluklar ve bağımlılıkları (varyant → ER20 dilimi, örtü faktörü, kopya tabanı) sabit, config
  dönemleri üreticisi belirlenimci ve git geçmişinden yeniden üretilebilir, P2 okuyucusuyla (`rehydrate.config_epoch`)
  uyumlu (TRANSITION ve kapsam dışı → dönem yok); yalnız standart kütüphane.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradingbot.research_engine import epochs as EP
from tradingbot.research_engine import registry as RG

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradingbot" / "research_engine"
DOC = ROOT / "docs" / "ENGINE_BRAIN_V1.md"

#: §8.2 S1 mühürü (P3a commit'i). DEĞİŞİRSE CI kırılır.
SHAS_PINNED = {
    "LIB_V1": "5f05c27d474e458a391d488ae2eff09d5e2067420f901346eb1c8130164b1903",
    "WF_PROTOCOL_V1": "a88abf4bc3dec78807d06b1ee39ebc0cf78883b1cbfd01e36365624ebe9e4a8c",
    "STATS_V1": "4a75ce951e1eddb8024537c24ba3d200c823d761ec6b292e7c3875f5874d60e8",
    "LESSON_REGISTRY_V1": "8fbed7f5e33e21a02ec9547bfd84ab7ad0a798d9cc25908aa99911244ad58836",
    "PIT_RULES_V1": "59d0628c975e42aaa7810e231ce0704e5113b6a2bde4fd59fb29e65d8fef48b7",
    "PRIOR_TRIALS_V1": "ad6d8903e66034ce96cafe006f0a071e254968308627ab9998b0464f96984921",
    "CONFIG_EPOCHS_V1": "35f2d18c6fae8d7e0006886deca68f0b3af64bbfd7d0546cf4a00e51643568c0",
    "THRESHOLD_DIRECTIONS_V1": "93016e4a468ea3a99cbedd2e6cb56780f569f1b713358b39fa7eb4aa2f1b0451",
}
T_SEAL_MS_PINNED = 1791773880000
REGISTRY_SHA_PINNED = "4b0e47cd82323191930660d1e527cc0a036a0fe7ab0c87512e71378a2ca1c095"

#: belgedeki sayılar (§2.2, §2.5, §2.6, §5.1, §7.5) — kayıttan BAĞIMSIZ yazılır
DOC_BUDGET = {"POOLED": (45, 45), "CLUSTER": (135, 135), "COIN_FIT": (161, 1081), "COPY": (312, 312), "COVER": (140, 140),
              "OTHER": (25, 25), "TOTAL": (818, 1738)}
DOC_FAMILIES = {"K": 13, "B": 10, "C": 8, "D": 7, "E": 4, "F1": 4, "F2": 7, "H": 5, "I": 2, "J": 5, "G": 6, "O": 35}
DOC_GRID = {"B": 10, "C": 8, "D": 8, "E": 4, "F1": 4, "F2": 8, "H": 5}

#: UNSPECIFIED boşlukları (belge söylemiyor; uydurulmadı). Yeni boşluk sessizce eklenemez. Sahibin 2026-10-11 kararından
#: (§8.5 "Önerilenleri kabul et") sonra yalnız önerisi olmayanlar kalır: B9 (spread, XSEC) ve B10'un altın stopları.
GAPS_PINNED = {
    *(f"LIB_V1.variants[BTCETH_Z_{z}].{f}" for z in ("2.0", "2.5") for f in ("exits.stop", "params.leg_sizing")),
    *(f"LIB_V1.variants[XS_MOM_{lb}_k{k}].{f}" for lb in (7, 28) for k in (3, 5)
      for f in ("exits.stop", "params.beta_window", "params.rebalance_time")),
    *(f"LIB_V1.variants[XS_REV_1D_k5].{f}" for f in ("exits.stop", "params.beta_neutral", "params.rebalance")),
    *(f"LIB_V1.variants[{g}].exits.stop" for g in ("G_LDN_ORB", "G_NY_ORB", "G_ASIA_DRIFT", "G_MONTH_END", "G_XAU_BTC_28",
                                                   "G_XAU_BTC_56")),
}
#: varyant → boşlukları. 13 varyant (I 2, J 5, G 6) gerçek veride çalıştırılmaz; kayıt düzeyinde boşluk kalmadı.
BLOCKED_PINNED = {
    v: {f"variants[{v}].{p}" for p in ps} for v, ps in {
        **{f"BTCETH_Z_{z}": ("exits.stop", "params.leg_sizing") for z in ("2.0", "2.5")},
        **{f"XS_MOM_{lb}_k{k}": ("exits.stop", "params.beta_window", "params.rebalance_time") for lb in (7, 28) for k in (3, 5)},
        "XS_REV_1D_k5": ("exits.stop", "params.beta_neutral", "params.rebalance"),
        **{g: ("exits.stop",) for g in ("G_LDN_ORB", "G_NY_ORB", "G_ASIA_DRIFT", "G_MONTH_END", "G_XAU_BTC_28",
                                        "G_XAU_BTC_56")}}.items()
}
PROTOCOL_GAPS_PINNED: set[str] = set()
#: §8.5 sahip kararı 2026-10-11 ile dolan eski boşluk yolları → §8.5 maddesi (değerler `reading_tr` ile; aşağıdaki test).
DECIDED_PINNED = {
    "WF_PROTOCOL_V1.purge_embargo.make_folds_split": "B1 (b)",
    "LIB_V1.copy_books.T2.max_hold": "B2 (a)", "LIB_V1.copy_books.M2.max_hold": "B2 (a)",
    "LIB_V1.defaults.er20_timeframe": "B3",
    "LIB_V1.covers.factors.F_BTC.regime": "B4",
    **{f"LIB_V1.variants[O_{b}_F_BTC].params.factor_params.regime": "B4" for b in ("T2", "M2", "BOX", "D4", "C4")},
    "LIB_V1.variants[RSI2_1h].exits.exit_rule": "B5", "LIB_V1.variants[RSI2_4h].exits.exit_rule": "B5",
    "STATS_V1.p_day.rng": "B6", "STATS_V1.placebo.seed_int": "B6", "LESSON_REGISTRY_V1.ci.seed": "B6",
    "LIB_V1.variants[KEL_20_2_1h].params.center_line": "B7", "LIB_V1.variants[KEL_20_2_4h].params.center_line": "B7",
    "LIB_V1.variants[BBSQ_20_1h].params.breakout_ref": "B7", "LIB_V1.variants[BBSQ_20_4h].params.breakout_ref": "B7",
    **{f"LIB_V1.variants[{v}].exits.exit_rule": "B8" for v in ("FUND_FADE_24h", "FUND_FADE_72h", "FUND_FADE_LOW_24h",
                                                                "FUND_FADE_LOW_72h")},
    "LIB_V1.variants[FUND_CARRY].params.pct_window": "B8",
    "LIB_V1.variants[G_NY_ORB].params.entry_until_utc": "B10", "LIB_V1.variants[G_ASIA_DRIFT].params.direction": "B10",
    "LIB_V1.specialisation_rules.4_profit_share_def": "B11",
    "PIT_RULES_V1.membership.volume_window_includes_d": "B11",
}


def _git_ok() -> bool:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", "db5db96^{commit}"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ============================================================================ mühür sabitleri
def test_s1_shas_are_pinned():
    assert dict(RG.SHAS) == SHAS_PINNED, dict(RG.SHAS)
    for name, sha in SHAS_PINNED.items():
        assert getattr(RG, f"{name}_SHA") == sha
        assert RG.sha256_canonical(RG.REGISTRIES[name]) == sha
    assert RG.T_SEAL_MS == T_SEAL_MS_PINNED
    assert RG.REGISTRY_SHA == REGISTRY_SHA_PINNED, RG.REGISTRY_SHA
    assert RG.S1_SEAL["shas"] == SHAS_PINNED and RG.S1_SEAL["t_seal_ms"] == T_SEAL_MS_PINNED


def test_doc_section_8_2_carries_the_same_seal_values():
    text = DOC.read_text(encoding="utf-8")
    for name, sha in SHAS_PINNED.items():
        assert f"`{name}` `{sha}`" in text, name
    assert f"`REGISTRY_SHA` `{REGISTRY_SHA_PINNED}`" in text
    assert f"`T_SEAL_MS` `{T_SEAL_MS_PINNED}`" in text


#: mühürlü içerik: bu veri dosyalarına dokunan ve sabitlenmiş sha/T_SEAL satırlarını değiştiren her commit
SEALED_DATA_FILES = ("tradingbot/research_engine/prior_trials.json", "tradingbot/research_engine/library/config_epochs.json")
PIN_FILES = ("tests/test_research_engine_registry.py", "tradingbot/research_engine/registry.py")
PIN_LINE = re.compile(r'^[+-]\s*("[A-Z_]+_V1": "[0-9a-f]{64}"|T_SEAL_MS_PINNED = |REGISTRY_SHA_PINNED = |T_SEAL_MS = )')
GIT = ("git", "-c", "log.showSignature=false")


def _git_out(*args: str) -> str:
    return subprocess.run([*GIT, "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120, check=True).stdout


def newest_sealed_commit_ct() -> tuple[int, str] | None:
    """Mühürlü içeriği değiştiren EN SON commit'in (işleyen zamanı, sha'sı): veri dosyalarına dokunan ya da sabitlenmiş
    sha/T_SEAL satırlarından birini değiştiren commit'ler (içerik değişikliği sha'yı değiştirir → sabit değişir)."""
    best = None
    out = _git_out("log", "--no-textconv", "--format=%ct %H", "--", *SEALED_DATA_FILES).split()
    if out:
        best = (int(out[0]), out[1])
    for line in _git_out("log", "--no-textconv", "--format=%ct %H", "--", *PIN_FILES).splitlines():
        ct, sha = line.split()
        if best is not None and int(ct) <= best[0]:
            break
        diff = _git_out("show", "--no-textconv", "--no-ext-diff", "--format=", "-U0", sha, "--", *PIN_FILES)
        if any(PIN_LINE.match(x) for x in diff.splitlines()):
            best = (int(ct), sha)
            break
    return best


def test_t_seal_ms_is_not_before_the_last_sealed_change():
    """`T_SEAL_MS` ≥ mühürlü içeriği değiştiren en son commit'in işleyen zamanı (fix-up commit'i T_SEAL'ı yenilemeden
    içeriği değiştiremez; rebase/squash/cherry-pick işleyen zamanı yeniler → test kırılır → yeniden mühür). Üst sınır:
    en geç 24 saat sonra (ileri pencere boşa harcanmaz)."""
    t = RG.T_SEAL_MS
    assert t % 60_000 == 0 and datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp() * 1000 <= t
    assert t <= datetime(2026, 10, 25, 23, 59, tzinfo=timezone.utc).timestamp() * 1000, "hedef ≤ 2026-10-25 (§8.2)"
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    last = newest_sealed_commit_ct()
    assert last is not None
    ct, sha = last
    assert ct * 1000 <= t, f"T_SEAL_MS {t} mühürlü içeriği değiştiren {sha[:12]} commit'inden ({ct}) önce: yeniden mühürle"
    assert t - ct * 1000 <= 24 * 3600 * 1000, (t, ct, sha[:12])
    doc = RG.CONFIG_EPOCHS_V1
    assert doc["covered_until"] == datetime.fromtimestamp(t / 1000, tz=timezone.utc).isoformat()


# ============================================================================ donma ve kanonik biçim
def test_registries_are_deeply_frozen():
    with pytest.raises(TypeError):
        RG.LIB_V1["id"] = "x"
    with pytest.raises(TypeError):
        RG.STATS_V1["thresholds"]["bh_q"] = 0.2
    with pytest.raises((TypeError, AttributeError)):
        RG.LIB_V1["variants"].append({})
    with pytest.raises(TypeError):
        RG.LIB_V1["variants"][0]["params"]["x"] = 1
    with pytest.raises(TypeError):
        RG.REGISTRIES["LIB_V1"] = {}


def test_canonical_bytes_are_sorted_compact_utf8_and_reject_nan():
    a = {"b": 1, "a": {"ç": "ğ", "z": [1, 2.5]}}
    b = {"a": {"z": [1, 2.5], "ç": "ğ"}, "b": 1}
    assert RG.canonical_bytes(a) == RG.canonical_bytes(b) == '{"a":{"z":[1,2.5],"ç":"ğ"},"b":1}'.encode("utf-8")
    assert RG.canonical_bytes(RG.freeze(a)) == RG.canonical_bytes(a)
    with pytest.raises(ValueError):
        RG.canonical_bytes({"x": float("nan")})
    with pytest.raises(ValueError):
        RG.freeze({"x": float("inf")})
    with pytest.raises(TypeError):
        RG.freeze({1: "int anahtar"})


def _leaf_paths(x, path=()):
    if isinstance(x, dict):
        if not x:
            yield path, "empty_dict"
        for k in x:
            yield from _leaf_paths(x[k], path + (k,))
    elif isinstance(x, list):
        if not x:
            yield path, "empty_list"
        for i, v in enumerate(x):
            yield from _leaf_paths(v, path + (i,))
    else:
        yield path, "leaf"


def _containers(x, path=()):
    if isinstance(x, dict):
        for k in x:
            yield path, k
            yield from _containers(x[k], path + (k,))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield path, i
            yield from _containers(v, path + (i,))


def _at(doc, path):
    for p in path:
        doc = doc[p]
    return doc


def _mutated(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return v + 1
    if isinstance(v, float):
        return v + 0.5 if v < 1e6 else v * 2
    if isinstance(v, str):
        return v + "·"
    if v is None:
        return 0
    raise AssertionError(type(v))


@pytest.mark.parametrize("name", sorted(RG.REGISTRIES))
def test_every_field_change_changes_the_sha(name):
    """Özellik: her yaprağın değişmesi, her anahtarın ve her liste öğesinin silinmesi sha'yı değiştirir."""
    doc = RG.thaw(RG.REGISTRIES[name])
    base = RG.sha256_canonical(doc)
    n = 0
    for path, kind in list(_leaf_paths(doc)):
        if not path:
            continue
        parent, key = _at(doc, path[:-1]), path[-1]
        old = parent[key]
        parent[key] = ({"x": 1} if kind == "empty_dict" else [1] if kind == "empty_list" else _mutated(old))
        assert RG.sha256_canonical(doc) != base, (name, path)
        parent[key] = old
        n += 1
    for path, key in list(_containers(doc)):
        parent = _at(doc, path)
        old = parent.pop(key)
        assert RG.sha256_canonical(doc) != base, (name, path, key)
        if isinstance(parent, dict):
            parent[key] = old
        else:
            parent.insert(key, old)
    assert RG.sha256_canonical(doc) == base and n > 20


# ============================================================================ bütçe ve sayılar (§2.2, §2.5, §2.6, §5.1, §7.5)
def test_budget_table_is_regenerated_from_the_registry():
    bt = RG.budget_table()
    assert bt == DOC_BUDGET, bt
    rows = RG.gate_a_rows()
    assert len(rows) == 818 and sum(r["raw_trials"] for r in rows) == 1738
    assert len({r["row_id"] for r in rows}) == 818, "satır kimlikleri tekil"
    decl = RG.LIB_V1["budget_declared"]
    assert {k: tuple(decl[k]) for k in DOC_BUDGET} == DOC_BUDGET
    cf = [r for r in rows if r["row_type"] == "COIN_FIT"]
    assert {r["unit"] for r in cf} == set(DOC_GRID) and all(r["raw_trials"] == DOC_GRID[r["unit"]] for r in cf)
    assert len({r["scope"] for r in cf}) == 23
    copies = [r for r in rows if r["row_type"] == "COPY"]
    assert sum(1 for r in copies if r["scope"] == "POOLED_OWN") == 13 and sum(1 for r in copies if r["scope"].startswith("COIN:")) == 299
    assert not any(r["unit"] in ("DON_20_10_4h_LO", "BOX_n10_mid_ms0.5") for r in rows), "alias satır açmaz"


def test_priors_cumulative_n_and_counts_match_the_design():
    assert RG.prior_total() == 2775
    assert RG.n_at_seal() == 4513 == 1738 + 2775
    assert RG.unique_variant_count() == 106 == sum(DOC_FAMILIES.values())
    assert RG.family_counts() == DOC_FAMILIES
    assert RG.coin_fit_grid() == DOC_GRID and sum(DOC_GRID.values()) == 47
    assert RG.explore_series() == 3745 == 45 * 40 + 13 * 40 + 35 * 40 + 5 + 2 + 18
    assert RG.oto_column_pre_s5_count() == 64
    assert sum(1 for v in RG.LIB_V1["variants"] if v["family"] == "O" and v["oto_column_pre_s5"]) == 24
    assert not any("auto_v1_table" in v for v in RG.LIB_V1["variants"]), "S5 öncesi sütun yönlendirme alanı değildir"
    s5 = next(d for d in RG.LIB_V1["owner_decisions"] if d["id"] == "S5")["text_tr"]
    assert "GEÇERSİZ KILAR" in s5 and "AUTO_CATALOG_V1" in s5
    ids = [v["variant_id"] for v in RG.LIB_V1["variants"]]
    assert len(ids) == len(set(ids)) == 108, "106 benzersiz + 2 alias (ızgara üyesi)"
    assert {v["variant_id"]: v["alias_of"] for v in RG.LIB_V1["variants"] if v["alias_of"]} == {
        "DON_20_10_4h_LO": "K_D4", "BOX_n10_mid_ms0.5": "K_BOX"}


def test_p23_and_clusters_partition_and_scopes():
    p23 = list(RG.LIB_V1["p23"]["symbols"])
    assert len(p23) == len(set(p23)) == 23
    mem = RG.LIB_V1["clusters"]["members"]
    assert [len(mem[c]) for c in ("CL_MAJ", "CL_L1", "CL_ALT")] == [2, 11, 10]
    flat = [s for c in mem.values() for s in c]
    assert sorted(flat) == sorted(p23)
    sc = RG.LIB_V1["scopes"]
    assert set(sc) == {"POOLED_P23", "POOLED_OWN", "CL_MAJ", "CL_L1", "CL_ALT", "COIN_FIT", "COIN", "PORT_XSEC", "PAIR_BTCETH",
                       "XAU", "PAXG", "GOLD_POOLED"}
    wf = set(RG.WF_PROTOCOL_V1["classes"])
    for v in RG.LIB_V1["variants"]:
        assert v["wf_class"] in wf and v["family"] in RG.LIB_V1["families"]
        assert set(v["exits"]) == {"stop", "exit_rule", "target_r", "max_hold_bars"}


def test_variant_dependencies_are_declared_by_rule():
    """Kayıt düzeyindeki boşluklar onlara dayanan varyantlara bağlıdır: ER20 kullanan → `defaults.er20_timeframe`;
    örtü → `covers.factors.<F>` + `copy_books.<taban>` (F_ER'de ER20 de); kopya ve F2 ızgarası → `copy_books.<taban>`."""
    for v in RG.LIB_V1["variants"]:
        req = list(v["requires"])
        assert req == sorted(set(req)) and all(RG.get_path(RG.LIB_V1, r) is not None for r in req), v["variant_id"]
        uses_er20 = "er20_max" in v["params"] or (v["cover"] and v["cover"]["factor"] == "F_ER")
        assert (RG.ER20_TF in req) == bool(uses_er20), v["variant_id"]
        if v["family"] == "O":
            base, fac = v["cover"]["base"], v["cover"]["factor"]
            assert {f"copy_books.{base}", f"covers.factors.{fac}"} <= set(req)
            assert dict(v["params"]["factor_params"]) == {k: x for k, x in RG.LIB_V1["covers"]["factors"][fac].items()
                                                          if k != "rule_tr"}
        if v["family"] == "K":
            base = next(k for k, b in RG.COPY_BOOKS.items() if b["book"] == v["book"]["book"])
            assert req == [f"copy_books.{base}"]
        if v["family"] == "F2":
            assert req == ["copy_books.BOX"]


def test_k_copy_values_equal_the_live_config_at_the_values_release():
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    import yaml
    cfg = yaml.safe_load(subprocess.run(["git", "-C", str(ROOT), "show", "db5db96:config.yaml"], capture_output=True,
                                        text=True, check=True).stdout)
    sp = cfg["strategy_paper"]
    ex = {e["name"]: e for e in sp["extra"]}
    v = RG.variants_by_id()
    assert v["K_T2"]["params"]["atr_mult"] == sp["atr_mult"] and v["K_M2"]["params"]["atr_mult"] == ex["m2_tsmom28"]["atr_mult"]
    box = v["K_BOX"]["params"]
    for k in ("near_frac", "trigger", "long_stop", "exit_kind", "exit_r", "eod_close", "allow_long", "allow_short"):
        assert box[k] == ex["b1_box_fade"]["rule_params"][k], k
    assert box["min_stop_pct"] == cfg["learning_mode"]["books"]["b1_box_fade"]["min_stop_pct"] == 0.5
    assert box["structures"] == cfg["structures"]["b1_box_fade"]
    assert [v[f"K_C4_CV{i:03d}"]["params"]["variation"] for i in range(1, 9)] == \
        ex["c4_candle_variations"]["rule_params"]["variations"]
    assert [s.split("/")[0] for s in ex["d4_donchian_20_10"]["symbols"]] == list(RG.LIB_V1["p23"]["symbols"])
    assert v["K_FM"]["params"]["protocol"] == cfg["pattern_trader"]["protocol"]
    assert len(cfg["entry_universe"]["symbols"]) == RG.LIB_V1["explore"]["entry_universe_size"]


def test_copy_max_hold_equals_the_live_rule_code():
    """Kopyaların zaman stopu canlı koddan okunur (WF arındırması ve X_TIME buna dayanır); T2/M2'de yok → yalnız WF için §2.2 varsayılanı (B2)"""
    from tradingbot import candle_variations as CV
    from tradingbot import donchian_trend as DT
    from tradingbot import ema200_trend as ET
    from tradingbot.pattern_trader import strategy_v3 as S3
    mh = {k: RG.LIB_V1["copy_books"][k]["max_hold"] for k in RG.COPY_BOOKS}
    assert (mh["D4"]["bars"], mh["D4"]["tf"]) == (DT.MAX_BARS, DT.TIMEFRAME) == (300, "4h")
    cvs = {c["id"]: c for c in CV.VARIATIONS}
    assert {cvs[x]["definition"]["exit"]["max_hold_bars"] for x in RG.C4_VARIATIONS} == {mh["C4"]["bars"]} == {24}
    assert {cvs[x]["definition"]["timeframe"] for x in RG.C4_VARIATIONS} == {mh["C4"]["tf"]}
    assert (mh["FM"]["bars"], mh["FM"]["tf"]) == (S3.V3_SIGNAL["hold_bars"], S3.V3_TF) == (24, "4h")
    # T2/M2: canlı kuralda zaman stopu yok → sahip kararı B2 (a): §2.2 varsayılanı 300 × 1d YALNIZ WF için (kural değişmez)
    for b in ("T2", "M2"):
        assert (mh[b]["bars"], mh[b]["tf"], mh[b]["wf_only"], mh[b]["x_time_bars"]) == (RG.DEFAULT_MAX_HOLD, "1d", True, 150)
        assert list(RG.LIB_V1["copy_books"][b]["timeframes"]) == ["1d"]
    src = Path(ET.__file__).read_text(encoding="utf-8")
    assert "max_hold" not in src and "hold_bars" not in src, "canlı T2/M2 kuralına zaman stopu eklendiyse wf_only değeri değişir"


# ============================================================================ kimlik (§2.1)
def test_spec_sha_dedups_aliases_and_every_spec_field_counts():
    m = "a" * 64
    assert RG.spec_sha(RG.lib_spec("DON_20_10_4h_LO", m)) == RG.spec_sha(RG.lib_spec("K_D4", m))
    assert RG.spec_sha(RG.lib_spec("BOX_n10_mid_ms0.5", m)) == RG.spec_sha(RG.lib_spec("K_BOX", m))
    shas = {RG.spec_sha(RG.lib_spec(v["variant_id"], m)) for v in RG.LIB_V1["variants"]}
    assert len(shas) == 106
    base = RG.lib_spec("TS_28_LS", m)
    for k in RG.SPEC_KEYS:
        s = copy.deepcopy(base)
        s[k] = "b" * 64 if k == "module_src_sha" else (s[k] + "x" if isinstance(s[k], str) else {"x": s[k]})
        assert RG.spec_sha(s) != RG.spec_sha(base), k
    with pytest.raises(ValueError):
        RG.spec_sha({k: base[k] for k in RG.SPEC_KEYS if k != "sizing"})
    with pytest.raises(ValueError):
        RG.spec_sha({**base, "extra": 1})
    with pytest.raises(ValueError):
        RG.spec_sha({**base, "module_src_sha": "XYZ"})
    cov = RG.lib_spec("O_D4_F_VOL", m)
    tweaked = copy.deepcopy(cov)
    tweaked["params"]["factor_params"]["atr_pct_max_pctile"] = 75
    assert RG.spec_sha(tweaked) != RG.spec_sha(cov), "örtü faktör eşiği spec_sha'ya girer (T3/T4 tekilleştirmesi)"


# ============================================================================ asla gevşetme (§8.2, sahip kararı 2)
def test_never_loosen_direction_table_covers_every_threshold_and_is_sealed():
    assert RG.THRESHOLD_DIRECTIONS is RG.THRESHOLD_DIRECTIONS_V1 and "THRESHOLD_DIRECTIONS_V1" in RG.S1_SEAL["shas"]
    assert set(RG.THRESHOLD_DIRECTIONS) == set(RG.REGISTRY_VERSIONS)
    for fam, versions in RG.REGISTRY_VERSIONS.items():
        dirs = RG.THRESHOLD_DIRECTIONS[fam]
        leaves = RG.threshold_leaves(versions[0])
        assert set(leaves) <= set(dirs), (fam, set(leaves) - set(dirs))
        for path in dirs:
            RG.get_path(versions[0], path)                     # her yön yolu v1'de var
        assert set(dirs.values()) <= {RG.MIN, RG.MAX, RG.FIXED}
    d = RG.THRESHOLD_DIRECTIONS
    assert d["STATS"]["thresholds.bh_q"] == RG.MAX and d["LESSON_REGISTRY"]["thresholds.looks.alphas"] == RG.MAX
    assert d["LESSON_REGISTRY"]["thresholds.eligibility.min_n"] == RG.MIN
    assert d["WF_PROTOCOL"]["thresholds.costs.ADVERSE.slippage_bps"] == RG.MIN
    assert d["LIB"]["thresholds.single_instrument.max_oos_profit_share"] == RG.MAX
    # tanımlar (iki yönde de sonucu oynatabilir) FIXED
    for fam, path in (("STATS", "thresholds.placebo.k_per_trade"), ("STATS", "thresholds.p_day.resamples"),
                      ("WF_PROTOCOL", "thresholds.positive_fold_min_trades"), ("WF_PROTOCOL", "thresholds.candidates_top_k"),
                      ("WF_PROTOCOL", "thresholds.warmup_bars"), ("PIT_RULES", "thresholds.listed_min_days"),
                      ("PIT_RULES", "thresholds.min_median_daily_quote_volume_usdt"), ("PIT_RULES", "xsec_requires_pit"),
                      ("WF_PROTOCOL", "step_equals_test"), ("STATS", "dsr.sr_var_floor"),
                      ("STATS", "null_world.pass_rate_max"), ("STATS", "bh_fdr.family_tr"),
                      ("WF_PROTOCOL", "kernel.stop_fill.policy"), ("WF_PROTOCOL", "kernel.entry_hour.policy")):
        assert d[fam][path] == RG.FIXED, (fam, path)
    assert all(d["LIB"][f"budget_declared.{k}"] == RG.MIN for k in ("TOTAL", "COIN_FIT", "PRIOR_MIN", "N_AT_SEAL_MIN"))


def _loosen(v, direction):
    if isinstance(v, list):
        return [_loosen(x, direction) for x in v[:1]] + v[1:]
    if isinstance(v, bool):
        return not v
    if isinstance(v, str):
        return v + "·"
    if direction == RG.MAX:
        return v * 2 if v else 1
    if direction == RG.MIN:
        return v / 2 if v else -1
    return v + 1


def _tighten(v, direction):
    if isinstance(v, list):
        return [_tighten(x, direction) for x in v]
    return (v / 2 if v else -1) if direction == RG.MAX else (v * 2 if v else 1)


def _parent(doc, path):
    *head, last = path.split(".")
    cur = doc
    for p in head:
        cur = cur[p]
    return cur, last


def test_never_loosen_catches_each_loosened_threshold_and_accepts_tighter_ones():
    for fam, versions in RG.REGISTRY_VERSIONS.items():
        v1 = versions[0]
        dirs = RG.THRESHOLD_DIRECTIONS[fam]
        assert RG.looser_than(v1, v1, dirs) == []
        for path, direction in dirs.items():
            old = RG.thaw(RG.get_path(v1, path))
            loose = RG.thaw(v1)
            par, last = _parent(loose, path)
            par[last] = _loosen(old, direction)
            bad = RG.looser_than(v1, loose, dirs)
            assert len(bad) == 1 and bad[0].startswith(path + ":"), (fam, path, bad)
            if direction != RG.FIXED:
                tight = RG.thaw(v1)
                par, last = _parent(tight, path)
                par[last] = _tighten(old, direction)
                assert RG.looser_than(v1, tight, dirs) == [], (fam, path)
            gone = RG.thaw(v1)
            par, last = _parent(gone, path)
            del par[last]
            assert RG.looser_than(v1, gone, dirs) == [f"{path}: eşik kaldırıldı"]


def test_every_later_registry_version_is_not_looser_than_v1():
    """Sonraki her sürüm (`REGISTRY_VERSIONS`'a eklenir) v1'den gevşek bir eşik taşıyamaz."""
    for fam, versions in RG.REGISTRY_VERSIONS.items():
        for later in versions[1:]:
            assert RG.looser_than(versions[0], later, RG.THRESHOLD_DIRECTIONS[fam]) == [], fam
    names = {n for n in dir(RG) if n.endswith(("_V2", "_V3", "_V4")) and not n.endswith("_SHA")}
    listed = {id(v) for vs in RG.REGISTRY_VERSIONS.values() for v in vs}
    assert all(id(getattr(RG, n)) in listed for n in names), f"sürüm REGISTRY_VERSIONS'a eklenmeli: {names}"


def test_threshold_values_match_the_design_text():
    t = RG.STATS_V1["thresholds"]
    assert (t["bh_q"], t["p_day"]["resamples"], t["pbo"]["blocks"], t["placebo"]["k_per_trade"], t["null_world"]["worlds"]) == \
        (0.10, 10000, 16, 5, 200)
    assert "bh_family_min_oos_trades" not in t, "BH ailesi işlem sayısıyla süzülmez (ana belge §6.4 daha sıkı)"
    assert "≥ 30" not in RG.STATS_V1["bh_fdr"]["family_tr"] and "p = 1" in RG.STATS_V1["bh_fdr"]["no_p_tr"]
    from math import comb
    assert comb(t["pbo"]["blocks"], t["pbo"]["blocks"] // 2) == 12870
    lt = RG.LESSON_REGISTRY_V1["thresholds"]
    assert list(lt["looks"]["n_thresholds"]) == [30, 60, 120, 240]
    assert abs(sum(lt["looks"]["alphas"]) - lt["looks"]["alpha_total"]) < 1e-12 and lt["looks"]["alpha_total"] == 0.05
    w = RG.WF_PROTOCOL_V1["thresholds"]
    assert w["folds"]["H1_PLUS"] == {"min_train_days": 365, "validation_days": 90, "test_days": 90, "step_days": 90}
    assert w["folds"]["INTRADAY"] == {"train_days": 120, "validation_days": 30, "test_days": 14, "step_days": 14}
    assert w["folds"]["GOLD_FUT"] == {"min_train_days": 180, "validation_days": 30, "test_days": 30, "step_days": 30}
    assert {k: dict(v) for k, v in w["costs"].items()} == {
        "base": {"taker_fee_pct": 0.05, "fee_sides": 2, "slippage_bps": 3, "spread_ticks": 0},
        "ADVERSE": {"taker_fee_pct": 0.05, "fee_sides": 2, "slippage_bps": 6, "spread_ticks": 1},
        "STRESS": {"taker_fee_pct": 0.07, "fee_sides": 2, "slippage_bps": 9, "spread_ticks": 1}}
    assert RG.PIT_RULES_V1["thresholds"]["min_median_daily_quote_volume_usdt"] == 20_000_000
    e = RG.LIB_V1["thresholds"]["eligibility"]
    assert (e["min_history_days_1h_plus"], e["min_history_days_5m"], e["min_pre_seal_trades"]) == (730, 240, 100)


# ============================================================================ UNSPECIFIED
def test_unspecified_gaps_are_explicit_and_pinned():
    gaps = RG.unspecified_gaps()
    assert {p for p, _ in gaps} == GAPS_PINNED, {p for p, _ in gaps} ^ GAPS_PINNED
    assert all(t.strip() for _, t in gaps)


def test_gaps_follow_variant_dependencies():
    """Kayıt düzeyindeki boşluk ona dayanan her varyantı durdurur (`requires`); sahip kararı 2026-10-11 sonrası kayıt
    düzeyinde boşluk kalmadı: kalan 13 varyantın boşlukları kendi alanlarındadır (B9, altın stopları)."""
    blocked = {k: set(v) for k, v in RG.blocked_variants().items()}
    assert blocked == BLOCKED_PINNED, set(blocked) ^ set(BLOCKED_PINNED)
    assert len(blocked) == 13 and not any(RG.variants_by_id()[v]["alias_of"] for v in blocked)
    assert {RG.variants_by_id()[v]["family"] for v in blocked} == {"I", "J", "G"}
    auto = {v["variant_id"] for v in RG.LIB_V1["variants"] if v["oto_column_pre_s5"] and not v["alias_of"]}
    assert not auto & set(blocked)
    # kayıt düzeyi bağımlılıklar artık dolu: onlara dayanan hiçbir varyant durmaz
    lib = RG.thaw(RG.LIB_V1)
    for path in ("defaults.er20_timeframe", "covers.factors.F_BTC.regime", "copy_books.T2.max_hold", "copy_books.M2.max_hold"):
        assert not RG.is_gap(RG.get_path(RG.LIB_V1, path)), path
        assert not any(path in g for g in blocked.values()), path
        par, last = _parent(lib, path)
        par[last] = RG.gap("test")                               # yeniden boşluk olursa bağımlılar yine durur
        assert any(path in g for g in RG.blocked_variants(lib).values()), path
        par[last] = RG.thaw(RG.get_path(RG.LIB_V1, path))
    assert {p for p, _ in RG.protocol_gaps()} == PROTOCOL_GAPS_PINNED
    # bütçede sayılan ama çalıştırılmayan pay (koddan; §8.4 notu): yalnız OTHER satırları
    assert RG.blocked_budget() == {"variants": 13, "gate_a_rows": 25, "raw_trials": 25}
    assert RG.blocked_budget()["gate_a_rows"] == RG.budget_table()["OTHER"][0], "I + J + G satırlarının hepsi"


def test_owner_decisions_2026_10_11_fill_the_recommended_gaps():
    """§8.5 'Önerilenleri kabul et' (2026-10-11): önerisi olan her boşluk değerle dolu, gerekçe `reading_tr`'de; öneri
    olmayanlar (B9, altın stopları) boşluk kalır."""
    at = {f"{name}.{p}": x for name, reg in RG.REGISTRIES.items() for p, x in RG._walk(reg)}   # unspecified_gaps yol biçimi
    for path, item in DECIDED_PINNED.items():
        v = at[path]
        assert not RG.is_gap(v), path
        assert v["decision"] == item and v["reading_tr"].startswith(f"sahip kararı 2026-10-11, §8.5 önerilen ({item}): "), path
    assert not set(DECIDED_PINNED) & GAPS_PINNED
    dec = {d["id"]: d for d in RG.LIB_V1["owner_decisions"]}["P3A_GAPS"]
    assert dec["date"] == "2026-10-11" and "B9" in dec["text_tr"] and "altın stopları" in dec["text_tr"]
    # değerler (§8.5 tablosu aynen)
    split = RG.WF_PROTOCOL_V1["purge_embargo"]["make_folds_split"]
    ex = split["example"]
    assert (ex["max_hold"], ex["purge_bars"], ex["embargo_bars"]) == (300, 150, 151)
    for mh in (300, 48, 24, 72, 6, 18):
        purge = (1 + mh) // 2                                    # split["purge_bars"] = floor((1 + max_hold) / 2)
        assert purge + ((1 + mh) - purge) == 1 + mh and (1 + mh) - purge - purge in (0, 1)
    assert split["sum_bars"] == "1 + max_hold" and split["purge_bars"] == "floor((1 + max_hold) / 2)"
    v = RG.variants_by_id()
    for h, bars in (("24h", 6), ("72h", 18)):
        for vid in (f"FUND_FADE_{h}", f"FUND_FADE_LOW_{h}"):
            assert v[vid]["exits"]["max_hold_bars"] == v[vid]["exits"]["exit_rule"]["max_hold_bars"] == bars, vid
    assert v["FUND_CARRY"]["params"]["pct_window"]["days"] == v["FUND_FADE_24h"]["params"]["pct_window_days"] == 20
    assert v["G_NY_ORB"]["params"]["entry_until_utc"]["utc"] == "18:00" and v["G_LDN_ORB"]["params"]["entry_until_utc"] == "12:00"
    assert v["G_ASIA_DRIFT"]["params"]["direction"]["zero_return"] == "NO_ENTRY"
    for tf in ("1h", "4h"):
        assert (v[f"KEL_20_2_{tf}"]["params"]["center_line"]["ma"], v[f"KEL_20_2_{tf}"]["params"]["center_line"]["period"]) == \
            ("EMA", 20)
        assert v[f"BBSQ_20_{tf}"]["params"]["breakout_ref"]["rule"] == "CLOSE_OUTSIDE_BB"
        rx = v[f"RSI2_{tf}"]["exits"]["exit_rule"]
        assert (rx["bb_period"], rx["bb_std"]) == (v[f"BBRE_{tf}"]["params"]["bb_period"], v[f"BBRE_{tf}"]["params"]["bb_std"])
    er = RG.LIB_V1["defaults"]["er20_timeframe"]
    assert (er["rule"], er["covers"], er["box"]) == ("SIGNAL_TF", "BASE_SIGNAL_TF", "1d")
    fb = RG.LIB_V1["covers"]["factors"]["F_BTC"]["regime"]
    assert (fb["definition"], fb["timeframe"], fb["ema_len"]) == ("regime_gate.btc_regime", "4h", 200)
    from tradingbot import ema200_trend as ET
    from tradingbot import regime_gate as RGT
    assert RGT.EMA_LEN == 200 and ET.btc_regime is RGT.btc_regime, "T2'nin kullandığı tanım"
    assert RG.STATS_V1["p_day"]["rng"]["algorithm"] == "random.Random"
    assert "tj_v1" in RG.STATS_V1["placebo"]["seed_int"]["trade_key"]
    assert RG.LESSON_REGISTRY_V1["ci"]["seed"]["seed"] == "sha256(cell_id|look_no)"
    assert RG.LIB_V1["specialisation_rules"]["4_profit_share_def"]["denominator"] == "SUM_POSITIVE_INSTRUMENT_NET_R"
    pit = RG.PIT_RULES_V1["membership"]["volume_window_includes_d"]
    assert pit["includes_d"] is False and pit["window"] == "d-30 … d-1"
    # yeni tanımlar asla-gevşetme tablosunda (FIXED)
    d = RG.THRESHOLD_DIRECTIONS
    assert d["WF_PROTOCOL"]["purge_embargo.make_folds_split.sum_bars"] == RG.FIXED
    assert d["LIB"]["specialisation_rules.4_profit_share_def.denominator"] == RG.FIXED
    assert d["PIT_RULES"]["membership.volume_window_includes_d.includes_d"] == RG.FIXED


# ============================================================================ öncül dosyası (§2.6)
def test_prior_trials_file_is_canonical_and_complete():
    raw = RG.PRIOR_TRIALS_FILE.read_text(encoding="utf-8")
    assert raw == RG.pretty_json(json.loads(raw)), "dosya sıralı-anahtar/indent=1 biçiminde"
    rows = RG.PRIOR_TRIALS_V1["rows"]
    assert len({r["prior_id"] for r in rows}) == len(rows) == 10
    assert {r["prior_id"]: r["count"] for r in rows} == {
        "gold_v1_primary": 32, "gold_v1_discovery": 1133, "gold_v2": 24, "fut_v1": 8, "fut_v2": 8, "book_v1": 54,
        "signal_lab_run_36150821072": 38, "signal_lab_run_36277302747": 24, "signal_lab_run_36123072720": 1412,
        "box_deney_v15": 42}
    assert {r["prior_id"] for r in rows if r["count_lower_bound"]} == {"fut_v1", "fut_v2"}
    assert len(RG.PRIOR_TRIALS_V1["no_reentry"]) == 7


# ============================================================================ config dönemleri (CONFIG_EPOCHS_V1)
def test_config_epochs_file_is_regenerated_from_git_history():
    if not _git_ok():
        pytest.skip("git geçmişi yok")
    cp = subprocess.run([sys.executable, "-s", str(ROOT / "scripts" / "gen_config_epochs.py"), "--check"],
                        capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def _cfg(**over) -> str:
    import yaml
    doc = {"risk": {"x": 1}, "strategy_paper": {"name": "t2_trend_regime", "atr_mult": 3.0,
                                                "extra": [{"name": "b1_box_fade", "rule_params": {"near_frac": 0.1}},
                                                          {"name": "m2_tsmom28", "atr_mult": 3.0}]},
           "learning_mode": {"extra_entries": "open", "books": {"b1_box_fade": {"slots": 20}}},
           "shared_experience": {"mode": "OFF"}}
    for path, val in over.items():
        cur = doc
        *head, last = path.split("__")
        for p in head:
            cur = cur.setdefault(p, {})
        cur[last] = val
    return yaml.safe_dump(doc)


def test_config_epochs_builder_is_deterministic_and_splits_only_the_affected_books(monkeypatch):
    base = _cfg()
    texts = {d["release"]: base for d in EP.DEPLOYS}
    texts["c0b8c94"] = texts["f9a61dd"] = _cfg(learning_mode__books={"b1_box_fade": {"slots": 20}})
    texts["f6e6119"] = texts["188cf22"] = texts["fc63481"] = _cfg(learning_mode__books={"b1_box_fade": {"slots": 40}})
    texts["f8b05fb"] = texts["8db1faf"] = texts["db5db96"] = _cfg(learning_mode__books={"b1_box_fade": {"slots": 40}},
                                                                   learning_mode__extra_entries="record_selectivity",
                                                                   shared_experience={"mode": "RECORD"})
    a, b = EP.render(EP.build(texts)), EP.render(EP.build(dict(reversed(list(texts.items())))))
    assert a == b, "aynı girdi → aynı bayt"
    doc = json.loads(a)
    by = {}
    for e in doc["epochs"]:
        by.setdefault(e["rule"], []).append(e)
    box = [(e["id"], e["kind"]) for e in by["b1_box_fade"]]
    assert box == [("E0", "PRE_HISTORY"), ("E2026-09-29T2243Z", "CONFIG"), ("X2026-10-04T114556Z", "TRANSITION"),
                   ("E2026-10-05T0455Z", "CONFIG")], box
    t2 = [(e["id"], e["kind"]) for e in by["t2_trend_regime"]]
    assert t2 == [("E0", "PRE_HISTORY"), ("X2026-10-04T114556Z", "TRANSITION"), ("E2026-10-05T0455Z", "CONFIG")], t2
    for eps in by.values():
        assert eps[0]["from"] == EP.UNBOUNDED_FROM and eps[-1]["to"] is None
        assert all(x["to"] == y["from"] for x, y in zip(eps, eps[1:]))
    assert EP.owner("learning_mode.books.b1_box_fade.slots") == "b1_box_fade" and EP.owner("risk.x") is None
    assert EP.owner("learning_mode.books.unknown_book.slots") is None, "tanınmayan defter paylaşılan sayılır"
    assert EP.neutral("shared_experience.mode") and not EP.neutral("shared_experiences.mode")


def test_config_epochs_adjacent_windows_merge_into_one_transition():
    import yaml
    base = yaml.safe_load(_cfg())
    flat0 = EP.flatten(base)
    f1 = dict(flat0, **{"risk.x": 2})
    f2 = dict(f1, **{"risk.x": 3})
    evs = [{"id": "r1", "lo": "2026-01-01T00:00:00+00:00", "hi": "2026-01-02T00:00:00+00:00", "flat": f1, "decision_tr": "a"},
           {"id": "r2", "lo": "2026-01-02T00:00:00+00:00", "hi": "2026-01-03T00:00:00+00:00", "flat": f2, "decision_tr": "b"}]
    eps = EP.book_epochs("main", ("main_fut", "main_spot"), flat0, evs)
    assert [(e["id"], e["kind"], e["from"], e["to"]) for e in eps] == [
        ("E0", "PRE_HISTORY", EP.UNBOUNDED_FROM, "2026-01-01T00:00:00+00:00"),
        ("X2026-01-01T0000Z", "TRANSITION", "2026-01-01T00:00:00+00:00", "2026-01-03T00:00:00+00:00"),
        ("E2026-01-03T0000Z", "CONFIG", "2026-01-03T00:00:00+00:00", None)]
    assert eps[1]["events"] == ["r1", "r2"] and eps[2]["release"] == "r2"


def test_f8b05fb_window_starts_at_its_release_script_commit():
    """Pencere alt sınırı = sürüm betiğinin ilk commit'i (f94ef5a, 2026-10-04 11:45:56 UTC); daha erken bir alt sınır
    fc63481 config'iyle açılmış işlemleri TRANSITION'a atardı."""
    d = {x["release"]: x for x in EP.DEPLOYS}["f8b05fb"]
    assert d["window"][0] == "2026-10-04T11:45:56+00:00" and d["lo_commit"] == "f94ef5a"
    for rel, bound, commit in EP.bound_commits():
        assert rel != "f8b05fb" or (bound, commit) in (("2026-10-04T11:45:56+00:00", "f94ef5a"),)


def test_sealed_config_epochs_work_with_the_p2_reader(tmp_path):
    from tradingbot.research_engine import rehydrate as RH
    lib = tmp_path / "research" / "library"
    lib.mkdir(parents=True)
    (lib / "config_epochs.json").write_text(RG.CONFIG_EPOCHS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    r = tmp_path / "research"
    cases = {("strategy_paper_box", "2026-09-29T12:00:00+00:00"): "E2026-09-28T2120Z",
             ("strategy_paper_box", "2026-10-01T00:00:00+00:00"): "E2026-09-29T2243Z",
             ("strategy_paper_box", "2026-10-04T11:00:00+00:00"): "E2026-09-29T2243Z",
             ("strategy_paper_box", "2026-10-09T00:00:00+00:00"): "E2026-10-05T0455Z",
             ("strategy_paper", "2026-10-01T00:00:00+00:00"): "E2026-09-28T2120Z",
             ("main_fut", "2026-09-20T00:00:00+00:00"): "E0", ("main_spot", "2026-10-10T00:00:00+00:00"): "E2026-10-05T0455Z"}
    for (book, at), want in cases.items():
        assert RH.config_epoch(book, at, research=r) == (want, RH.EPOCH_SRC_SEALED), (book, at)
    # TRANSITION penceresindeki işlem hiçbir config dönemine atanmaz
    for book in ("strategy_paper_box", "strategy_paper", "main_fut"):
        assert RH.config_epoch(book, "2026-10-04T12:00:00+00:00", research=r) == (None, RH.EPOCH_SRC_TRANSITION), book
    # mühürden (covered_until) sonra: son döneme yazılmaz; o andan sonra BAŞLAYAN bir dönem örterse o
    late = datetime.fromtimestamp(RG.T_SEAL_MS / 1000 + 3600, tz=timezone.utc).isoformat()
    assert RH.config_epoch("strategy_paper_box", late, research=r) == (None, RH.EPOCH_SRC_UNCOVERED)
    doc = json.loads(RG.CONFIG_EPOCHS_FILE.read_text(encoding="utf-8"))
    cov = doc["covered_until"]
    doc["epochs"].append({"id": "N1", "from": cov, "to": None, "books": ["strategy_paper_box"], "kind": "CONFIG"})
    (lib / "config_epochs.json").write_text(json.dumps(doc), encoding="utf-8")
    assert RH.config_epoch("strategy_paper_box", late, research=r) == ("N1", RH.EPOCH_SRC_SEALED)
    assert RH.config_epoch("strategy_paper", late, research=r) == (None, RH.EPOCH_SRC_UNCOVERED)
    (lib / "config_epochs.json").write_text(RG.CONFIG_EPOCHS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    doc = RG.CONFIG_EPOCHS_V1
    assert doc["schema"] == EP.SCHEMA and {r["release"] for r in doc["releases"]} == {d["release"] for d in EP.DEPLOYS}
    books = {b for e in doc["epochs"] for b in e["books"]}
    assert {"main_fut", "main_spot", "strategy_paper", "strategy_paper_m2", "strategy_paper_box", "pattern_trader",
            "strategy_paper_trend4h", "strategy_paper_candle4h", "strategy_paper_candle4h_strict"} <= books


# ============================================================================ yalıtım
def _imports(path: Path) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out += [(node.module, node.level)] if node.module else [(a.name, node.level) for a in node.names]
    return out


def test_p3a_modules_import_only_the_standard_library():
    stdlib = set(sys.stdlib_module_names)
    allowed_rel = {"registry.py": set(), "trials.py": {"registry", "ledgers", "paths"}, "epochs.py": set()}
    for name, rel_ok in allowed_rel.items():
        for mod, level in _imports(PKG / name):
            if level:
                assert mod.split(".")[0] in rel_ok, (name, mod)
                continue
            top = mod.split(".")[0]
            assert top in stdlib or (name == "epochs.py" and top == "yaml"), (name, mod)
            assert top not in {"socket", "urllib", "http", "ssl", "requests", "subprocess", "sqlite3", "pandas"}, (name, mod)
    code = ("import sys; import tradingbot.research_engine.registry, tradingbot.research_engine.trials, "
            "tradingbot.research_engine.epochs; print(sorted(m for m in ('pandas','numpy','yaml','socket','urllib.request') "
            "if m in sys.modules))")
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert cp.returncode == 0 and cp.stdout.strip().splitlines()[-1] == "[]", cp.stdout + cp.stderr

"""P3b-2a — saf aileler B, C, D, E, F1, F2, H (docs/ENGINE_BRAIN_V1.md §2.1, §2.2, §8.4 P3b-2a notu).

* Katalog kayıtla uzlaşır: uygulanmış 58 (K 13, B 10, C 8, D 7, E 4, F1 4, F2 7, H 5) + O 35 (sonraki adım) = kaydın
  çalıştırılabilir 93'ü (106 − 13 boşluklu); I, J, G (13) `BLOCKED` — çalışan kod yok, kurulamaz; iki alias tabanın
  kopyasını çalıştırır.
* Varyant YALNIZ mühürlü satırdan kurulur (params + spec; eksik/fazla/yanlış yapı ValueError); pencere canlı kural
  çerçevesinin penceresidir.
* Kimlik: bildirilmiş dosya kümeleri elle sabitlenir; alias'ın `module_src_sha`'sı ve `spec_sha`'sı tabanınkidir —
  deneme defterinde aynı kapsamda ikinci kez sayılmaz (DUPLICATE). `DON_20_10_4h_LO`'nun saf modülle kurulumu K_D4 ile
  bayt-aynı işlemler verir (ısınmadan sonra); F2 ekseni alias'ta K_BOX'un kural parametrelerini birebir verir.
* Elle kurulmuş yollar: her ailenin girişi/yönü/stopu ve çıkışları (işaret dönüşü, ters kesişim, karşı kanal, 2R hedef,
  max_hold, orta bant, fonlama yüzdeliği); altın sentetik yollar (varyant başına özet sha'sı sabit); LS'de iki yön.
* Saflık: çalışma anında G/Ç, saat, rastgelelik yok; çalışan `tradingbot` dosyaları bildirilmiş kümede.
* Parite (a) saf ailelerde (varyantın kendi kuralı → `apply_action` → `FuturesLedgerV2`), kısmen eksik 1h verisinde
  çekirdekle aynı tek-tik yedeği ve `PATH_1H_GAP` (m-C); H dünyasında 4h damgaları, ms kaymaları ve boşluk.
* P3b-2a inceleme düzeltmesi (2026-10-12): f8 aralık kuralı (eksik uzlaşma / aralık değişimi → NaN; 8h aynen; kör nokta
  sabit; yerel kural = çekirdeğinki), FUND_CARRY işaret çıkışı ve pozisyonda `F8_UNMEASURABLE`, BBSQ sıkışması t − 1'de
  (t'den ayırt edilir), RSI2 orta bandın doğru yanı; Box alias eşitliği işlem üreten dünyada.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tradingbot import strategy_lib as SL
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import parity as PA
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import synthetic as S
from tradingbot.research_engine import trials as TR
from tradingbot.research_engine.paths import EnginePaths
from tradingbot.strategy_lib import catalog as SLC
from tradingbot.strategy_lib import identity as SLI
from tradingbot.strategy_lib import indicators as G
from tradingbot.strategy_lib import pure as PU

ROOT = Path(__file__).resolve().parents[1]
T0 = S.T0
NEW_FAMILIES = ("B", "C", "D", "E", "F1", "F2", "H")
PURE = [v for v in SLC.IMPLEMENTED if SLC.FAMILY_OF[v] != "K"]


# ============================================================================ katalog ↔ kayıt
def test_catalog_reconciles_with_the_registry_runnable_count():
    vs = RG.variants_by_id()
    st = {vid: SLC.status(vid) for vid in vs}
    blocked = RG.blocked_variants()
    assert {k for k, x in st.items() if x == SLC.BLOCKED_S} == set(blocked) == set(SLC.BLOCKED)
    assert {vs[k]["family"] for k in SLC.BLOCKED} == set(SLC.BLOCKED_FAMILIES) == {"I", "J", "G"}
    assert {k: v["alias_of"] for k, v in vs.items() if v.get("alias_of")} == dict(SLC.ALIASES)
    assert {k for k, x in st.items() if x == SLC.ALIAS_S} == set(SLC.ALIASES)
    impl = {k for k, x in st.items() if x == SLC.IMPLEMENTED_S}
    pending = {k for k, x in st.items() if x == SLC.PENDING_S}
    assert impl == set(SLC.IMPLEMENTED) and {vs[k]["family"] for k in pending} == {"O"}
    per = Counter(SLC.FAMILY_OF[v] for v in SLC.IMPLEMENTED)
    assert per == {"K": 13, "B": 10, "C": 8, "D": 7, "E": 4, "F1": 4, "F2": 7, "H": 5}
    fc = RG.family_counts()
    assert all(fc[f] == n for f, n in per.items()), "uygulanan = kaydın (alias'sız) aile sayısı"
    assert all(SLC.FAMILY_OF[v] == vs[v]["family"] for v in SLC.IMPLEMENTED)
    runnable = RG.unique_variant_count() - RG.blocked_budget()["variants"]
    assert (len(impl), len(pending), runnable) == (58, 35, 93) and len(impl) + len(pending) == runnable
    with pytest.raises(KeyError):
        SLC.status("NOPE")


def test_blocked_families_have_no_runnable_code():
    for vid in SLC.BLOCKED:
        with pytest.raises(SLC.BlockedVariant):
            SLC.make(vid, RG.thaw(RG.variants_by_id()[vid]["params"]))
        with pytest.raises(SLC.BlockedVariant):
            KN.make_variant(vid)
        with pytest.raises(SLC.BlockedVariant):
            SLI.module_src_sha(vid)
    for f in sorted((ROOT / "tradingbot" / "strategy_lib").glob("*.py")):
        if f.name == "catalog.py":
            continue
        src = f.read_text(encoding="utf-8")
        assert not any(k in src for k in ("BTCETH", "XS_MOM", "XS_REV", "G_LDN", "G_NY", "G_ASIA", "G_MONTH", "G_XAU")), f
    with pytest.raises(KeyError):
        KN.make_variant("O_T2_F_ER")                         # bekleyen (sonraki adım)


# ============================================================================ mühürlü satırdan kurulum
def test_pure_variants_are_built_from_the_sealed_row_only():
    from tradingbot import paper_rules
    import inspect
    from tradingbot.strategy_lib import livebars as LB
    assert PU.WINDOW["1d"] == LB.LIVE_CLOSED_BARS["1d"]
    tail = inspect.signature(paper_rules.intraday_rows).parameters["tail"].default
    assert PU.WINDOW["1h"] == PU.WINDOW["4h"] == tail == 400, "gün içi pencere = canlı kural çerçevesi (D4 ile aynı)"
    vs = RG.variants_by_id()
    for vid in PURE:
        v = vs[vid]
        var = KN.make_variant(vid)
        assert (var.variant_id, var.family, var.signal_tf) == (vid, v["family"], v["signal_tf"])
        assert list(var.timeframes) == list(v["timeframes"])
        p = RG.thaw(v["params"])
        spec = KN.variant_spec(vid)
        with pytest.raises(ValueError):
            SLC.make(vid, {**p, "extra": 1}, spec)
        with pytest.raises(ValueError):
            SLC.make(vid, {k: x for k, x in list(p.items())[1:]}, spec)
        with pytest.raises(ValueError):
            SLC.make(vid, p, None)
        if v["family"] == "F2":
            assert var.rule_params.near_frac == p["near_frac"] and var.rule_params.min_stop_pct == p["min_stop_pct"]
            assert var.rule_params.exit_kind == {"mid": "box_mid", "2r": "r_multiple"}[vid.split("_")[2]]
            continue
        ex = v["exits"]
        assert var.stop_atr_mult == ex["stop"]["atr_mult"] and var.max_hold == ex["max_hold_bars"]
        assert var.target_r == ex["target_r"] and var.lookback == {var.signal_tf: PU.WINDOW[var.signal_tf]}
        bad = dict(spec, exits=dict(spec["exits"], stop={"kind": "PCT", "atr_mult": 1.0, "atr": "ATR14 (x)"}))
        with pytest.raises(ValueError):
            SLC.make(vid, p, bad)
        with pytest.raises(ValueError):
            SLC.make(vid, p, dict(spec, family="Z"))
    assert KN.variant_spec("K_T2") is None and KN.variant_spec("BOX_n20_2r_ms1.0")["base_params"] == KN.lib_params("K_BOX")


# ============================================================================ kimlik ve tekilleştirme
_LIB = ("tradingbot/strategy_lib/contract.py", "tradingbot/strategy_lib/indicators.py", "tradingbot/strategy_lib/pure.py")
_BOX = ("tradingbot/strategy_lib/contract.py", "tradingbot/strategy_lib/livebars.py", "tradingbot/strategy_lib/k_box.py",
        "tradingbot/paper_rules.py", "tradingbot/box_theory.py", "tradingbot/ema200_trend.py", "tradingbot/indicators.py",
        "tradingbot/candle_confirmation.py", "tradingbot/timeframes.py")
EXPECTED_FILES = {
    "TS_28_LS": _LIB + ("tradingbot/strategy_lib/b_tsmom.py",),
    "MAX_50_200_1d_LO": _LIB + ("tradingbot/strategy_lib/c_macross.py",),
    "DON_55_20_4h_LS": _LIB + ("tradingbot/strategy_lib/d_donchian.py",),
    "KEL_20_2_1h": _LIB + ("tradingbot/strategy_lib/e_volbreak.py",),
    "RSI2_4h": _LIB + ("tradingbot/strategy_lib/f1_range.py",),
    "BOX_n20_2r_ms1.0": _BOX + ("tradingbot/strategy_lib/f2_boxgrid.py",),
    "FUND_CARRY": _LIB + ("tradingbot/strategy_lib/h_funding.py", "tradingbot/futures_features.py",
                          "tradingbot/timeframes.py"),
}


def _git_ok() -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True).returncode == 0


def _shas_or_skip() -> dict[str, str]:
    if not _git_ok():
        pytest.skip("git yok")
    try:
        return KN.module_src_shas()
    except SLI.DirtyTreeError as exc:
        pytest.skip(f"çalışma ağacı kirli (kimlik bilerek hesaplanmaz): {exc}")


def test_declared_identity_files_per_family_and_aliases_share_the_base_files():
    for vid, files in EXPECTED_FILES.items():
        assert SLI.module_files(vid) == tuple(sorted(set(files))), vid
    for vid in PURE:
        fam = SLC.FAMILY_OF[vid]
        rep = {"B": "TS_28_LS", "C": "MAX_50_200_1d_LO", "D": "DON_55_20_4h_LS", "E": "KEL_20_2_1h", "F1": "RSI2_4h",
               "F2": "BOX_n20_2r_ms1.0", "H": "FUND_CARRY"}[fam]
        assert SLI.module_files(vid) == SLI.module_files(rep), vid
    assert SLI.module_files("DON_20_10_4h_LO") == SLI.module_files("K_D4")
    assert SLI.module_files("BOX_n10_mid_ms0.5") == SLI.module_files("K_BOX")
    assert all("research_engine" not in f for v in PURE for f in SLI.module_files(v))


def test_module_src_sha_from_head_and_alias_dedupe_in_the_trials_ledger(tmp_path):
    """Tekilleştirme sahte değil: alias kataloğun aynı modülüne çözülür → aynı `module_src_sha` → `registry.lib_spec`
    tabanın spec'i → aynı `spec_sha`; saf D modülü (başka dosya kümesi) farklı kimlik verirdi. Deneme defteri aynı
    (LIB ailesi, spec_sha, kapsam) ikinci satırı DUPLICATE sayar."""
    m = _shas_or_skip()
    assert set(m) == set(SLC.IMPLEMENTED) and len(m) == 58
    for vid, files in EXPECTED_FILES.items():
        lines = []
        for rel in sorted(set(files)):
            blob = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True, check=True).stdout
            lines.append(f"{rel}\t{hashlib.sha256(blob).hexdigest()}\n")
        assert m[vid] == hashlib.sha256(("strategy_lib.module_src_sha.v2\n" + "".join(lines)).encode()).hexdigest(), vid
    for alias, base in SLC.ALIASES.items():
        assert SLI.module_src_sha(alias) == m[base]
        assert KN.spec_sha_for(alias) == KN.spec_sha_for(base) == RG.spec_sha(RG.lib_spec(alias, m[base]))
    assert SLI.module_src_sha("DON_20_10_1d_LO") != m["K_D4"], "saf modül başka kimlik → tekilleşme katalogdan gelir"
    shas = {v: KN.spec_sha_for(v) for v in SLC.IMPLEMENTED}
    assert len(set(shas.values())) == 58
    full = {v["variant_id"]: m.get(v["variant_id"], hashlib.sha256(v["variant_id"].encode()).hexdigest())
            for v in RG.LIB_V1["variants"] if not v.get("alias_of")}
    rows = TR.seal_entries(full)
    by_vid: dict[str, set] = {}
    for r in rows:
        by_vid.setdefault(r["params"]["variant_id"], set()).add(r["spec_sha"])
    for vid in SLC.IMPLEMENTED:
        assert by_vid[vid] == {shas[vid]}, vid
    assert by_vid["DON_20_10_4h_LO"] == {shas["K_D4"]} and by_vid["BOX_n10_mid_ms0.5"] == {shas["K_BOX"]}
    p = EnginePaths(data=tmp_path / "data", state=tmp_path / "data" / "state")
    led = TR.TrialsLedger(p)
    at = "2026-10-20T01:40:00+00:00"
    assert all(r["status"] == "APPENDED" for r in led.append([dict(r, at=at) for r in rows]))
    n0 = led.counters["N_raw_cum"]
    assert n0 == 1738
    for alias, base in SLC.ALIASES.items():
        again = {"kind": "SPEC", "lib_version": "LIB_v1", "spec_sha": KN.spec_sha_for(alias), "scope": "POOLED_OWN",
                 "row_id": f"{alias}@POOLED_OWN", "params": {"variant_id": alias}, "at": at}
        assert led.append([again])[0]["status"] == "DUPLICATE", f"{alias} ≡ {base}: ikinci kez deneme sayılmaz"
    assert TR.TrialsLedger(p).counters["N_raw_cum"] == n0


def test_don_20_10_4h_lo_pure_module_equals_the_k_d4_copy():
    """§2.2 `DON_20_10_4h_LO ≡ K_D4`: D ailesinin saf modülü 20/10/4h/LO ile kurulduğunda canlı D4 kopyasıyla AYNI
    işlemleri verir (K_D4'ün 60 barlık ısınmasından sonra; bayt-aynı `to_dict`, varyant adı hariç)."""
    from tradingbot.strategy_lib import d_donchian as DD
    spec = dict(KN.variant_spec("DON_20_10_1d_LO"), timeframes=["4h"], signal_tf="4h")
    don = DD.DonchianBreakout("DON_20_10_4h_LO", {"entry_channel_bars": 20, "exit_channel_bars": 10}, spec)
    k = KN.make_variant("DON_20_10_4h_LO")
    assert k.variant_id == "K_D4" and SLC.status("DON_20_10_4h_LO") == SLC.ALIAS_S
    n = 0
    for seed in (11, 13):
        d = S.d4_world(seed, n=900)
        start = int(d.bars["4h"][59]["timestamp"]) + S.H4
        a = [dict(t.to_dict(), variant_id=None) for t in KN.run(don, d, start_ms=start).trades]
        b = [dict(t.to_dict(), variant_id=None) for t in KN.run(k, d, start_ms=start).trades]
        assert a == b and a
        n += len(a)
    assert n >= 15


def test_box_grid_alias_axes_equal_the_k_box_rule_params():
    from tradingbot.strategy_lib import f2_boxgrid as F2
    p = RG.thaw(RG.variants_by_id()["BOX_n10_mid_ms0.5"]["params"])
    spec = dict(KN.variant_spec("BOX_n20_mid_ms0.5"))
    g = F2.BoxGrid("BOX_n10_mid_ms0.5", p, spec)
    k = KN.make_variant("BOX_n10_mid_ms0.5")
    assert k.variant_id == "K_BOX" and g.rule_params == k.rule_params
    d = S.box_world(22, days=3)                                # bu dünya işlem üretir (eşitlik boş kümede sınanmaz)
    a = [dict(t.to_dict(), variant_id=None) for t in KN.run(g, d).trades]
    b = [dict(t.to_dict(), variant_id=None) for t in KN.run(k, d).trades]
    assert len(a) >= 1 and a == b, (len(a), len(b))
    with pytest.raises(ValueError):
        F2.BoxGrid("BOX_n10_mid_ms0.5", dict(p, near_frac=0.2), spec)
    with pytest.raises(ValueError):
        F2.BoxGrid("BOX_n10_2r_ms0.5", dict(p, exit="2R"), dict(spec, exits=dict(spec["exits"], target_r=3.0)))


# ============================================================================ elle kurulmuş yollar
def mk(closes: list[float], tf: str, *, wick: float = 0.001, t0: int = T0) -> list[dict]:
    """Kapanışlardan barlar: açılış = önceki kapanış (boşluk yok), uçlar gövdeden `wick` kadar."""
    step = SL.TF_MS[tf]
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"timestamp": t0 + i * step, "open": prev, "high": max(prev, c) * (1 + wick),
                    "low": min(prev, c) * (1 - wick), "close": c, "volume": 1.0})
        prev = c
    return out


def run_on(vid: str, rows: list[dict], *, funding=None) -> KN.KernelResult:
    var = KN.make_variant(vid)
    return KN.run(var, KN.SeriesData(symbol="TST", bars={var.signal_tf: rows}, funding=funding))


def at(rows: list[dict], i: int) -> int:
    return int(rows[i]["timestamp"])


def test_b_tsmom_sign_state_entry_flip_exit_and_ls_reversal():
    closes = [100.0 + i for i in range(21)] + [120.0 - 2 * (i - 20) for i in range(21, 36)]
    rows = mk(closes, "1d")
    lo = run_on("TS_7_LO", rows)
    (t,) = lo.trades
    # ATR14 ilk kez 13. barda ölçülür → ilk karar orada; 7 günlük getiri 23. barda ≤ 0 → 24. barın açılışında çık
    assert (t.signal_ts, t.side, t.exit_kind, t.exit_reason, t.exit_ts) == (at(rows, 13), "LONG", KN.EXIT_RULE,
                                                                            "TS_SIGN_FLIP_DOWN", at(rows, 24))
    a = G.atr([r["high"] for r in rows[:14]], [r["low"] for r in rows[:14]], closes[:14], 14)[-1]
    assert t.initial_stop == pytest.approx(closes[13] - 3.0 * a, abs=1e-12)
    ls = run_on("TS_7_LS", rows)
    assert [(x.signal_ts, x.side) for x in ls.trades] == [(at(rows, 13), "LONG"), (at(rows, 24), "SHORT")]
    assert ls.trades[1].entry_ts == at(rows, 25) and ls.trades[1].exit_kind == KN.EXIT_END


def test_c_ma_cross_state_reverse_cross_exit_and_reversal():
    closes = [200.0 - 0.5 * i for i in range(80)] + [160.5 + 0.5 * k for k in range(80)]
    rows = mk(closes, "4h")
    res = run_on("MAX_20_50_4h_LS", rows)
    k = next(i for i in range(80, len(closes)) if G.ema(closes[:i + 1], 20)[-1] >= G.ema(closes[:i + 1], 50)[-1])
    s, lng = res.trades[0], res.trades[1]
    assert (s.signal_ts, s.side, s.exit_kind, s.exit_reason, s.exit_ts) == (at(rows, 49), "SHORT", KN.EXIT_RULE,
                                                                            "MA_CROSS_UP", at(rows, k + 1))
    assert (lng.signal_ts, lng.side, lng.entry_ts) == (at(rows, k + 1), "LONG", at(rows, k + 2))
    lo = run_on("MAX_20_50_4h_LO", rows)                      # LO pozisyonsuzdur: long sinyali kesişim barında
    kg = next(i for i in range(80, len(closes)) if G.ema(closes[:i + 1], 20)[-1] > G.ema(closes[:i + 1], 50)[-1])
    assert [(x.signal_ts, x.side) for x in lo.trades] == [(at(rows, kg), "LONG")] and kg == k


def test_d_donchian_fresh_breakout_opposite_channel_exit_and_short():
    closes = [100.0 + (0.05 if i % 2 else -0.05) for i in range(30)] + [103.0, 104.0] + [104.0 + i for i in range(1, 8)]
    closes += [110.0 - 1.5 * i for i in range(1, 30)]
    rows = mk(closes, "1d")
    var = KN.make_variant("DON_20_10_1d_LS")
    data = KN.SeriesData(symbol="TST", bars={"1d": rows})
    (it,) = KN.signals_at(var, data, at(rows, 30) + S.D1)
    assert (it.signal_ts, it.side) == (at(rows, 30), "LONG")
    assert KN.signals_at(var, data, at(rows, 31) + S.D1) == [], "kırılım İLK kapanıştır (31. bar yeni değil)"
    res = KN.run(var, data)
    x = res.trades[0]
    lows = [r["low"] for r in rows]
    k = next(i for i in range(31, len(rows)) if closes[i] < min(lows[i - 10:i]))
    assert (x.side, x.exit_kind, x.exit_reason, x.exit_ts) == ("LONG", KN.EXIT_RULE, "DONCHIAN_EXIT_LOW10", at(rows, k + 1))
    shorts = [t for t in res.trades if t.side == "SHORT"]
    j = next(i for i in range(k + 1, len(rows)) if closes[i] < min(lows[i - 20:i]) and not closes[i - 1] < min(lows[i - 21:i - 1]))
    assert shorts and shorts[0].signal_ts == at(rows, j)


def _flat_then(n: int, base: float = 100.0, amp: float = 0.2) -> list[float]:
    return [base + (amp if i % 2 else 0.0) for i in range(n)]


def test_e_keltner_fresh_break_2r_target_from_the_real_entry_and_max_hold():
    closes = _flat_then(60) + [103.0] + [103.0 + (0.1 if i % 2 else 0.0) for i in range(60)]
    rows = mk(closes, "1h")
    (t,) = run_on("KEL_20_2_1h", rows).trades
    assert (t.signal_ts, t.side) == (at(rows, 60), "LONG")
    assert t.targets == (t.entry_ref + 2.0 * (t.entry_ref - t.initial_stop),), "2R GERÇEK girişten (açılış)"
    assert (t.exit_kind, t.exit_reason, t.exit_ts) == (KN.EXIT_TIME, "TIME_STOP_48_BARS", at(rows, 61 + 48))
    up = closes[:61] + [103.2, 103.5, 104.0, 105.0, 106.0] + [106.0] * 10
    rows = mk(up, "1h")
    (t,) = run_on("KEL_20_2_1h", rows).trades
    assert t.exit_kind == KN.EXIT_TARGET and t.legs[0].ref == pytest.approx(t.targets[0]) and not t.legs[0].market
    down = _flat_then(60) + [97.0] + [97.0] * 5
    (t,) = run_on("KEL_20_2_1h", mk(down, "1h")).trades
    assert t.side == "SHORT"


def _bb_rank(c: list[float], s: int, lb: int = 120) -> float:
    """s barının BB(20,2) genişliğinin, s'de biten (kendisi dahil) son `lb` genişlik içindeki ortalama sırası / lb."""
    w = [G.bb_width_at(c, j, 20, 2.0) for j in range(s - lb + 1, s + 1)]
    return G.pct_rank(w, w[-1])


def test_e_bbsq_needs_the_squeeze_rank_on_the_bar_before_the_breakout():
    import random as _r
    rnd = _r.Random(4)
    wild = [100.0 * (1 + rnd.gauss(0, 0.02)) for _ in range(160)]
    calm = [100.0 + (0.02 if i % 2 else 0.0) for i in range(22)]
    var = KN.make_variant("BBSQ_20_1h")
    rows = mk(wild + calm + [100.6], "1h")
    data = KN.SeriesData(symbol="TST", bars={"1h": rows})
    (it,) = KN.signals_at(var, data, at(rows, 182) + S.H1)
    assert it.side == "LONG" and it.evidence["squeeze_pct_rank"] <= 0.20 and it.evidence["squeeze_bar_ts"] == at(rows, 181)
    assert it.targets == (SL.Target(SL.TARGET_R_FROM_ENTRY, 2.0),) and it.max_hold_bars == 48
    calm2 = [100.0 + (0.02 if i % 2 else 0.0) for i in range(182)]
    rows2 = mk(calm2 + [100.6], "1h")                       # hep sakin: genişlikler eşit → sıra ≈ 0,5 (sıkışma yok)
    c2 = [r["close"] for r in rows2]
    assert c2[-1] > G.bollinger_at(c2, 182)[2] and _bb_rank(c2, 181) > 0.20, "kırılım var ama sıkışma yok"
    assert KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows2}), at(rows2, 182) + S.H1) == []
    rows3 = mk(wild + calm + [99.4], "1h")
    (it,) = KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows3}), at(rows3, 182) + S.H1)
    assert it.side == "SHORT"


def test_e_bbsq_squeeze_is_measured_on_t_minus_1_not_on_the_breakout_bar():
    """Teknik okuma (P3b-2a inceleme bulgu 2): sıkışma t − 1'de, kırılım t'de. (A) t − 1 sıkışık, kırılım barı bandı
    genişletir (t'nin sırası en üstte) → GİRİŞ (t okunuşu reddederdi); (B) t − 1'in penceresinde eski bir sıçrama var
    (t − 1 geniş), t sıkışık → giriş YOK (t okunuşu girerdi). Kırılım ikisinde de taze."""
    import random as _r
    var = KN.make_variant("BBSQ_20_1h")
    rnd = _r.Random(7)
    a = [100.0 + rnd.uniform(-0.15, 0.15) for _ in range(150)] + [100.0 + (0.01 if k % 2 else 0.0) for k in range(25)]
    a += [101.0]
    rnd = _r.Random(8)
    b = [100.0 + rnd.uniform(-0.6, 0.6) for _ in range(150)] + [104.0] + [100.0 + (0.02 if k % 2 else 0.0) for k in range(19)]
    b += [100.6]
    for c, want in ((a, "LONG"), (b, None)):
        i = len(c) - 1
        assert c[i] > G.bollinger_at(c, i)[2] and c[i - 1] <= G.bollinger_at(c, i - 1)[2], "taze kırılım"
        r_t, r_prev = _bb_rank(c, i), _bb_rank(c, i - 1)
        assert (r_t > 0.20, r_prev <= 0.20) == ((True, True) if want else (False, False)), (r_t, r_prev)
        rows = mk(c, "1h")
        got = KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows}), at(rows, i) + S.H1)
        assert [x.side for x in got] == ([want] if want else []), (want, r_t, r_prev)
        if want:
            assert got[0].evidence["squeeze_pct_rank"] == pytest.approx(r_prev, abs=1e-12)
    p = RG.thaw(RG.variants_by_id()["BBSQ_20_1h"]["params"])
    with pytest.raises(ValueError):
        SLC.make("BBSQ_20_1h", dict(p, squeeze_bar=dict(p["squeeze_bar"], squeeze_at="SIGNAL_BAR")), KN.variant_spec("BBSQ_20_1h"))


def _range_series(n: int, base: float = 100.0) -> list[float]:
    return [base + 1.0 * math.sin(i * 0.9) for i in range(n)]


def test_f1_rsi2_er_filter_middle_band_exit_and_max_hold():
    var = KN.make_variant("RSI2_1h")
    closes = _range_series(60) + [98.0, 96.5]
    rows = mk(closes, "1h")
    data = KN.SeriesData(symbol="TST", bars={"1h": rows})
    (it,) = KN.signals_at(var, data, at(rows, 61) + S.H1)
    assert it.side == "LONG" and it.evidence["rsi"] < 10 and it.evidence["er20"] < 0.3
    trend = [100.0 + 0.8 * i for i in range(60)]
    trend += [trend[-1] - 3.0, trend[-1] - 6.0]
    rows_t = mk(trend, "1h")
    assert G.rsi(trend, 2)[-1] < 10 and G.er(trend, 20, 61) >= 0.3
    assert KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows_t}), at(rows_t, 61) + S.H1) == [], "ER20 süzgeci"
    back = closes + [97.0, 98.5, 100.0, 101.0, 101.0]
    rows = mk(back, "1h")
    res = run_on("RSI2_1h", rows)                              # aralık serisi kendi işlemlerini de üretir (iki yön)
    assert {t.side for t in res.trades} == {"LONG", "SHORT"}
    (t,) = [x for x in res.trades if x.signal_ts == at(rows, 61)]
    k = next(i for i in range(62, len(back)) if back[i] >= G.bollinger_at(back, i)[1])
    assert (t.side, t.exit_kind, t.exit_reason, t.exit_ts) == ("LONG", KN.EXIT_RULE, "MIDDLE_BAND", at(rows, k + 1))
    drift = closes + [96.5 - 0.01 * i for i in range(1, 40)]
    rows = mk(drift, "1h")
    (t,) = [x for x in run_on("RSI2_1h", rows).trades if x.signal_ts == at(rows, 61)]
    assert (t.exit_kind, t.exit_reason, t.exit_ts) == (KN.EXIT_TIME, "TIME_STOP_24_BARS", at(rows, 62 + 24))


def test_f1_rsi2_entry_needs_the_close_on_the_right_side_of_the_middle_band():
    """Teknik okuma (P3b-2a inceleme bulgu 4): RSI2 < 10 ve ER20 < 0,3 olsa da kapanış BB(20,2) orta çizgisinin üstündeyse
    long YOK (orta bant çıkışı ilk kararda tetiklenirdi); aynası short için. Doğru yanda giriş var."""
    var = KN.make_variant("RSI2_1h")
    base = _range_series(60)
    plat = [base[-1] + 6.0 + (0.5 if k % 2 else 0.0) for k in range(6)]
    up = base + plat + [base[-1] + 6.5 - 2.0, base[-1] + 6.5 - 4.0]
    for c, rsi_ok in ((up, lambda r: r < 10), ([200.0 - x for x in up], lambda r: r > 90)):
        i = len(c) - 1
        r, e, mid = G.rsi(c, 2)[-1], G.er(c, 20, i), G.bollinger_at(c, i)[1]
        assert rsi_ok(r) and e < 0.3 and (c[i] > mid if c is up else c[i] < mid), (r, e, mid, c[i])
        rows = mk(c, "1h")
        assert KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows}), at(rows, i) + S.H1) == []
    rows = mk(base + [98.0, 96.5], "1h")                       # doğru yan: kapanış < orta
    (it,) = KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows}), at(rows, 61) + S.H1)
    assert it.side == "LONG" and rows[-1]["close"] < it.evidence["bb_mid"]
    p = RG.thaw(RG.variants_by_id()["RSI2_1h"]["params"])
    with pytest.raises(ValueError):
        SLC.make("RSI2_1h", dict(p, entry_mid_side=dict(p["entry_mid_side"], bb_period=14)), KN.variant_spec("RSI2_1h"))


def test_f1_bbre_outside_then_close_back_inside_both_sides():
    var = KN.make_variant("BBRE_1h")
    base = _range_series(60)
    rows = mk(base + [97.0, 99.3], "1h")
    lo_p = G.bollinger_at(base + [97.0], 60)[0]
    assert 97.0 < lo_p
    (it,) = KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows}), at(rows, 61) + S.H1)
    assert it.side == "LONG" and it.exit_rule == "MIDDLE_BAND" and it.max_hold_bars == 24
    rows = mk(base + [103.0, 100.7], "1h")
    (it,) = KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows}), at(rows, 61) + S.H1)
    assert it.side == "SHORT"
    rows = mk(base + [97.0, 96.0], "1h")                       # hâlâ dışarıda: giriş yok
    assert KN.signals_at(var, KN.SeriesData(symbol="TST", bars={"1h": rows}), at(rows, 61) + S.H1) == []


H8 = 8 * 3_600_000


def _funding(days: int, spike_at: int | None, spike: float, base: float = 0.0001) -> list[tuple[int, float, float]]:
    return [(T0 + k * H8 + 3, spike if k == spike_at else base, 100.0) for k in range(days * 3)]


def test_h_funding_fade_extreme_short_time_exit_and_no_data_no_trade():
    closes = _flat_then(160, amp=0.1)
    rows = mk(closes, "4h")
    fr = _funding(30, 48, 0.002)                               # 16. gün 00:00 uzlaşması 20 günün en yükseği
    (t,) = run_on("FUND_FADE_24h", rows, funding=fr).trades
    seen = next(i for i, r in enumerate(rows) if r["timestamp"] + S.H4 - 60_000 >= fr[48][0])
    assert (t.signal_ts, t.side) == (at(rows, seen), "SHORT")
    assert t.evidence["f8_hi"] == 1.0 and t.evidence["f8"] == pytest.approx(0.002)
    assert (t.exit_kind, t.exit_reason, t.exit_ts) == (KN.EXIT_TIME, "TIME_STOP_6_BARS", at(rows, seen + 1 + 6))
    (t,) = run_on("FUND_FADE_72h", rows, funding=_funding(30, 48, -0.002)).trades
    assert t.side == "LONG" and t.exit_reason == "TIME_STOP_18_BARS"
    assert run_on("FUND_FADE_24h", rows, funding=None).trades == [], "fonlama verisi yok → giriş yok"
    assert run_on("FUND_FADE_24h", rows, funding=_funding(30, None, 0.0)).trades == [], "sabit oran uç değildir"
    assert run_on("FUND_FADE_24h", rows, funding=_funding(30, 20, 0.002)).trades == [], "< 30 değer: yüzdelik yok"
    trend = [100.0 * (1.01 ** i) for i in range(160)]
    assert run_on("FUND_FADE_LOW_24h", mk(trend, "4h"), funding=fr).trades == [], "ER20 ≥ 0,3: giriş yok"
    (t,) = run_on("FUND_FADE_LOW_24h", rows, funding=fr).trades
    assert t.side == "SHORT" and t.evidence["er20"] < 0.3


def test_h_funding_carry_receiving_side_and_exit_below_the_median():
    rows = mk(_flat_then(160, amp=0.1), "4h")
    fr = [(T0 + k * H8, (0.0001 + 0.00001 * (k % 7)) if k != 48 else 0.003, 100.0) for k in range(90)]
    (t,) = run_on("FUND_CARRY", rows, funding=fr).trades
    seen = next(i for i, r in enumerate(rows) if r["timestamp"] + S.H4 - 60_000 >= fr[48][0])
    assert (t.signal_ts, t.side) == (at(rows, seen), "SHORT"), "f8 > 0: fonlamayı short alır"
    assert t.exit_kind == KN.EXIT_RULE and t.exit_reason == "ABS_F8_BELOW_P50"
    nxt = next(i for i, r in enumerate(rows) if r["timestamp"] + S.H4 - 60_000 >= fr[49][0])
    assert t.exit_ts == at(rows, nxt + 1)
    neg = [(a, -r if k == 48 else r, m) for k, (a, r, m) in enumerate(fr)]
    (t,) = run_on("FUND_CARRY", rows, funding=neg).trades
    assert t.side == "LONG"


def _carry_rows() -> list[tuple[int, float, float]]:
    return [(T0 + k * H8, (0.0001 + 0.00001 * (k % 7)) if k != 48 else 0.003, 100.0) for k in range(90)]


def test_h_funding_carry_exits_when_the_sign_turns_against_the_position():
    """Teknik okuma (P3b-2a inceleme bulgu 3): f8'in işareti pozisyona ters dönünce (short'ta f8 < 0, long'da f8 > 0)
    çıkılır — |f8| hâlâ yüksek olsa da (|f8| < %50'lik çıkışı tetiklenmez)."""
    from tradingbot.strategy_lib import h_funding as HF
    rows = mk(_flat_then(160, amp=0.1), "4h")
    for sign, side in ((1.0, "SHORT"), (-1.0, "LONG")):
        fr = [(a, sign * r if k == 48 else (-sign * 0.0025 if k == 49 else r), m) for k, (a, r, m) in enumerate(_carry_rows())]
        trades = run_on("FUND_CARRY", rows, funding=fr).trades
        t = trades[0]
        nxt = next(i for i, r in enumerate(rows) if r["timestamp"] + S.H4 - 60_000 >= fr[49][0])
        _a, ahi, _l = HF.f8_state(fr, at(rows, nxt) + S.H4, absolute=True)
        f8, _h, _lo = HF.f8_state(fr, at(rows, nxt) + S.H4)
        assert ahi >= 0.5 and f8 * sign < 0, "|f8| çıkışı tetiklenmez; işaret ters"
        assert (t.side, t.exit_kind, t.exit_reason, t.exit_ts) == (side, KN.EXIT_RULE, "F8_SIGN_AGAINST_POSITION",
                                                                   at(rows, nxt + 1))
        assert trades[1].side != side and trades[1].signal_ts == at(rows, nxt + 1), "şimdi alan taraf karşı yön"
    spec = KN.variant_spec("FUND_CARRY")
    p = RG.thaw(RG.variants_by_id()["FUND_CARRY"]["params"])
    bad = dict(spec, exits=dict(spec["exits"], exit_rule=dict(spec["exits"]["exit_rule"], sign_against=False)))
    with pytest.raises(ValueError):
        SLC.make("FUND_CARRY", p, bad)


def _fund_every(n: int, every_h: int, *, drop: int | None = None, off: int = 3) -> list[tuple[int, float, float]]:
    """n uzlaşma her `every_h` saatte (ms kaymalı); oran tabanın küçük döngüsü (uç yok: en büyük/en küçük payı 6/7)."""
    return [(T0 + k * every_h * 3_600_000 + off, 0.0001 + 0.00001 * (k % 7), 100.0) for k in range(n) if k != drop]


@pytest.mark.parametrize("every_h, bar", [(4, 151), (1, 45)])
def test_h_f8_is_unmeasurable_when_the_settlement_before_the_current_is_missing(every_h, bar):
    """Bulgu 1 (2026-10-12): 200 uzlaşmalık 4h/1h dizisinde güncelden ÖNCEKİ uzlaşma eksikse fut_v1 aralığı 8h/2h okur, f8
    yarıya iner ve 20 günün en düşüğü olur (`f8_lo` = 1 → yanlış FUND_FADE LONG; açık başarısızlık). Aralık kuralı: güncel
    aralık öncekinden farklı → f8 NaN → giriş yok (bütün koşuda da)."""
    import numpy as np
    from tradingbot import futures_features as FF
    from tradingbot.strategy_lib import h_funding as HF
    rows = mk(_flat_then(220, amp=0.1), "4h")
    T = at(rows, bar) + S.H4                                    # karar anı (bar kapanışı)
    cur = (T - T0 - FF.FUND_GUARD_MS - 3) // (every_h * 3_600_000)   # görünür güncel uzlaşma
    fr = _fund_every(200, every_h, drop=cur - 1)
    old = FF.align_funding(np.asarray([T]), [r[0] for r in fr], [r[1] for r in fr])
    assert old["f8"][0] == pytest.approx(fr[cur - 1][1] * 8 / (2 * every_h)) and old["f8_lo"][0] == 1.0, "eski okunuş: uç"
    assert all(x != x for x in HF.f8_state(fr, T)) and not HF.interval_steady([r[0] for r in fr], T)
    var = KN.make_variant("FUND_FADE_24h")
    data = KN.SeriesData(symbol="TST", bars={"4h": rows}, funding=fr)
    assert KN.signals_at(var, data, T) == []
    assert run_on("FUND_FADE_24h", rows, funding=fr).trades == []
    assert run_on("FUND_FADE_24h", rows, funding=_fund_every(200, every_h)).trades == [], "eksiksiz dizide de uç yok"


def test_h_f8_on_a_steady_8h_series_is_exactly_fut_v1():
    """8h davranışı değişmedi: kararlı 8h dizisinde (ms kaymalı) her karar anında f8 / yüzdelikler fut_v1
    `align_funding`'in kendisidir; 8h'te güncelden önceki uzlaşmanın yokluğu (16h) fut_v1'de zaten NaN'dır."""
    import random as _r
    import numpy as np
    from tradingbot import futures_features as FF
    from tradingbot.strategy_lib import h_funding as HF
    rnd = _r.Random(3)
    fr = [(T0 + k * H8 + rnd.randint(0, 999), rnd.gauss(0.0001, 0.0002), 100.0) for k in range(120)]
    ts, rs = [r[0] for r in fr], [r[1] for r in fr]
    n_fin = 0
    assert HF.f8_state(fr, T0 + 4 * S.H4)[0] != HF.f8_state(fr, T0 + 4 * S.H4)[0], "iki görünür uzlaşma: aralık doğrulanamaz"
    for b in range(5, 240):                                      # ≥ 3 görünür uzlaşma (iki aralık)
        T = T0 + b * S.H4
        for ab in (False, True):
            ref = FF.align_funding(np.asarray([T]), ts, [abs(x) for x in rs] if ab else rs)
            got = HF.f8_state(fr, T, absolute=ab)
            want = (float(ref["f8"][0]), float(ref["f8_hi"][0]), float(ref["f8_lo"][0]))
            assert all((x != x and y != y) or x == y for x, y in zip(got, want)), (b, ab, got, want)
            n_fin += got[1] == got[1]
    assert n_fin > 300
    T = T0 + 61 * H8 + S.H4                                      # güncel k = 61; k = 60 eksik
    holed = [r for k, r in enumerate(fr) if k != 60]
    ref = FF.align_funding(np.asarray([T]), [r[0] for r in holed], [r[1] for r in holed])
    assert ref["f8"][0] != ref["f8"][0] and all(x != x for x in HF.f8_state(holed, T))


def test_h_f8_interval_switch_and_the_pinned_blind_spot():
    """8h → 4h geçişi: güncelin aralığı öncekinden farklı olduğu tek karar penceresinde f8 NaN, sonra fut_v1'in kendisi.
    Kör nokta (m-D ile aynı; belgelendi ve sabitlendi): geçişte İLK 4h uzlaşması eksikse 8h'lik aralık değişimin bir
    uzlaşma sonra olmasından ayırt edilemez → o tek pencerede f8 = 4h oranı × 8 / 8 (gerçeğin yarısı); sonraki pencere
    NaN (aralık değişti)."""
    import numpy as np
    from tradingbot import futures_features as FF
    from tradingbot.strategy_lib import h_funding as HF
    sw = T0 + 40 * H8                                           # 8h: k = 0…40 (k = 40 = sw, 16:00 → 00:00); 4h: sw + j × 4h
    r8 = [(T0 + k * H8 + 5, 0.0001 + 0.00001 * (k % 7), 100.0) for k in range(41)]
    r4 = {j: (sw + j * S.H4 + 7, (0.0001 + 0.00001 * (j % 5)) / 2, 100.0) for j in range(1, 30)}   # 4 saatlik oran

    def st(fr, j):                                              # sw + j × 4h uzlaşmasının görünür olduğu ilk karar anı
        return HF.f8_state(fr, sw + j * S.H4 + S.H4)

    def ref(fr, j):
        out = FF.align_funding(np.asarray([sw + j * S.H4 + S.H4]), [r[0] for r in fr], [r[1] for r in fr])
        return float(out["f8"][0])

    fr = r8 + [r4[j] for j in sorted(r4)]
    assert all(x == x for x in st(fr, 0)), "j = 0: güncel 8h (16:00 → 00:00), değişim henüz görünmez"
    assert st(fr, 1)[0] != st(fr, 1)[0], "j = 1: güncel 4h, önceki 8h → NaN"
    assert st(fr, 2)[0] == ref(fr, 2) == pytest.approx(r4[2][1] * 2) and st(fr, 2)[1] == st(fr, 2)[1]
    blind = r8 + [r4[j] for j in sorted(r4) if j != 1]          # ilk 4h uzlaşması (j = 1) eksik
    assert st(blind, 1) == st(blind, 0), "j = 1 yok: güncel hâlâ sw"
    f8 = st(blind, 2)[0]
    assert f8 == ref(blind, 2) == pytest.approx(r4[2][1]), "kör nokta: 8h okunur, f8 yarı (gerçek r4 × 2)"
    assert st(blind, 3)[0] != st(blind, 3)[0] and st(blind, 4)[0] == ref(blind, 4) == pytest.approx(r4[4][1] * 2)


def test_h_local_gap_rule_equals_the_kernel_rule():
    """f8 aralık kuralının yerel boşluk ölçüsü çekirdeğin `FundingSeries` kuralıdır (sabitler ve sonuç aynı)."""
    import random as _r
    from tradingbot.strategy_lib import h_funding as HF
    assert (HF.GAP_FACTOR, HF.LOCAL_SPAN, HF.SNAP_MS) == (KN.FUNDING_GAP_FACTOR, KN.FUNDING_LOCAL_SPAN, KN.FUNDING_SNAP_MS)
    rnd = _r.Random(9)
    for _ in range(300):
        t, out = T0, []
        for _k in range(rnd.randint(2, 30)):
            t += rnd.choice((1, 2, 4, 4, 8, 8, 8, 12, 16)) * 3_600_000
            out.append((t + rnd.randint(0, 999), 0.0001, 100.0))
        ts = sorted({KN.snap_ms(x[0]) for x in out})
        assert HF.local_gaps(ts) == KN.FundingSeries(out).gap_after
        T = out[-1][0] + 3_600_000
        vis = sorted({KN.snap_ms(x[0]) for x in out})
        if len(vis) >= 3:
            g = KN.FundingSeries(out).gap_after
            d = [b - a for a, b in zip(vis, vis[1:])]
            steady = round(d[-1] / 3.6e6) == round(d[-2] / 3.6e6) and round(d[-1] / 3.6e6) in (1, 2, 4, 8) and not (g[-1] or g[-2])
            assert HF.interval_steady([x[0] for x in out], T) == steady


def test_h_funding_carry_holds_and_flags_when_f8_is_unmeasurable_in_position():
    """Bulgu 1: pozisyondayken f8 ölçülemezse (eksik uzlaşma: bayat, sonra 16h aralık, sonra aralık değişimi) durum
    DEĞİŞMEZ (çıkış yok) ve işlem `F8_UNMEASURABLE` (bilgi) işaretlenir; ölçülebilir olunca çıkış kuralı yine çalışır."""
    from tradingbot.strategy_lib import h_funding as HF
    rows = mk(_flat_then(160, amp=0.1), "4h")
    fr = _carry_rows()
    (t0,) = run_on("FUND_CARRY", rows, funding=fr).trades
    assert HF.FLAG_F8_UNMEASURABLE not in t0.flags and t0.exit_reason == "ABS_F8_BELOW_P50"
    holed = [r for k, r in enumerate(fr) if k != 49]
    (t,) = run_on("FUND_CARRY", rows, funding=holed).trades
    assert (t.signal_ts, t.side) == (t0.signal_ts, t0.side)
    assert t.flags.count(HF.FLAG_F8_UNMEASURABLE) == 1 and t.exit_reason == "ABS_F8_BELOW_P50" and t.exit_ts > t0.exit_ts
    first_ok = next(i for i, r in enumerate(rows) if all(x == x for x in HF.f8_state(holed, r["timestamp"] + S.H4, absolute=True))
                    and r["timestamp"] + S.H4 - 60_000 >= fr[50][0])
    assert t.exit_ts == at(rows, first_ok + 1)
    assert SL.hold() is SL.NONE and SL.hold("X").flags == ("X",) and SL.hold("X").kind == SL.ACT_NONE
    with pytest.raises(ValueError):
        SL.Action(SL.ACT_NONE, flags=("",))


def test_h_funding_fade_flags_but_keeps_its_exit_when_f8_is_unmeasurable_in_position():
    """FUND_FADE pozisyonda f8 okumaz (zaman/stop çıkışı): eksik uzlaşma tutuşta f8'i ölçülemez yapınca yalnız
    `F8_UNMEASURABLE` (bilgi) yazılır; giriş, çıkış ve R aynıdır."""
    from tradingbot.strategy_lib import h_funding as HF
    rows = mk(_flat_then(160, amp=0.1), "4h")
    fr = _funding(30, 48, 0.002)
    (t0,) = run_on("FUND_FADE_72h", rows, funding=fr).trades
    (t,) = run_on("FUND_FADE_72h", rows, funding=[r for k, r in enumerate(fr) if k != 50]).trades
    assert HF.FLAG_F8_UNMEASURABLE not in t0.flags and t.flags.count(HF.FLAG_F8_UNMEASURABLE) == 1
    assert (t.signal_ts, t.side, t.exit_reason, t.exit_ts) == (t0.signal_ts, t0.side, t0.exit_reason, t0.exit_ts)
    assert t.legs == t0.legs


def test_h_parity_world_has_4h_stamps_ms_offsets_and_a_gap_and_parity_holds():
    """İnceleme düzeltmesi (P3b-2a, bulgu 6): H parite dünyası 8h → 4h geçişi (4h damgaları), ms kaymaları ve iki eksik
    uzlaşma içerir; çekirdek ↔ defter (`live_1h`) beş H varyantında birlikte geçer; boşluğu örten işlemler çekirdekte
    `FUNDING_GAP` (kapıdan çıkar), FUND_CARRY'de ölçülemeyen karar `F8_UNMEASURABLE`."""
    d = S.world_for("FUND_CARRY", "4h", "H")(11)
    ts = [int(r[0]) for r in d.funding]
    hours = {(t % 86_400_000) // 3_600_000 for t in ts}
    assert {4, 12, 20} <= hours and {0, 8, 16} <= hours, "4h damgaları (geçişten sonra)"
    assert sum(1 for t in ts if t % 1000) > len(ts) // 2, "ms kaymaları"
    fs = KN.FundingSeries(d.funding)
    assert sum(fs.gap_after) >= 2, "çekirdeğin yerel kuralıyla en az iki boşluk"
    pairs, gap_trades, flags = [], 0, set()
    for vid in ("FUND_FADE_24h", "FUND_FADE_72h", "FUND_FADE_LOW_24h", "FUND_FADE_LOW_72h", "FUND_CARRY"):
        kr = KN.run(KN.make_variant(vid), d)
        refs, _rej = PA.reference_run(vid, d)
        pairs.append((vid, kr, refs))
        gap_trades += sum(1 for t in kr.trades if KN.FLAG_FUNDING_GAP in t.flags)
        flags |= {f for t in kr.trades for f in t.flags}
    a = PA.compare_a(pairs, min_trades=100)
    assert a.identity_share == 1.0 and a.passed, a.to_dict()
    assert gap_trades >= 2 and "F8_UNMEASURABLE" in flags


# ============================================================================ altın sentetik yollar
def golden_world(vid: str) -> tuple[Any, KN.SeriesData]:
    v = KN.make_variant(vid)
    fn = S.world_for(vid, v.signal_tf, v.family)
    if v.family == "F2":
        return v, fn(22, days=5)
    kw = {"1d": {"n": 700}, "4h": {"n": 1400}, "1h": {"n": 2400}}[v.signal_tf]
    return v, fn(31, **kw)


_WORLD_CACHE: dict[tuple, KN.SeriesData] = {}


def golden_summary(vid: str) -> tuple:
    v = KN.make_variant(vid)
    key = (v.family == "F2", v.family == "H", v.signal_tf)
    if key not in _WORLD_CACHE:
        _WORLD_CACHE[key] = golden_world(vid)[1]
    kr = KN.run(v, _WORLD_CACHE[key])
    sm = [(t.signal_ts, t.side, t.exit_kind, t.exit_reason, round(t.r["base"], 6), round(t.r["ADVERSE"], 6),
           round(t.r["STRESS"], 6)) for t in kr.trades]
    kinds = dict(sorted(Counter(x[2] for x in sm).items()))
    sides = Counter(x[1] for x in sm)
    return (len(sm), sides.get("LONG", 0), sides.get("SHORT", 0), kinds, round(sum(x[4] for x in sm), 6),
            hashlib.sha256(json.dumps(sm).encode()).hexdigest()[:16])


#: varyant → (işlem, long, short, çıkış türleri, Σ base R, özet sha'sı[:16]); özet = her işlemin (sinyal barı, yön,
#: çıkış türü, nedeni, base/ADVERSE/STRESS R 6 hane). Dünya: seed 31 (1d 700, 4h 1.400, 1h 2.400 bar; H fonlama
#: dünyası), F2: Box seed 22, 5 gün. Kural/çekirdek değişirse kırılır; değer bilerek yeniden sabitlenir (P3b-2a; inceleme
#: düzeltmesi 2026-10-12: BBSQ sıkışması t − 1'de, RSI2 orta bandın doğru yanı, FUND_CARRY işaret çıkışı, f8 aralık kuralı
#: ve H dünyası 8h → 4h geçişi + ms kaymaları + iki eksik uzlaşma).
GOLDEN_ALL: dict[str, tuple] = {
    "TS_7_LO": (61, 61, 0, {"RULE": 61}, -4.227389, "db227f06d3d91be8"),
    "TS_7_LS": (93, 43, 50, {"END": 1, "RULE": 91, "STOP": 1}, 2.316665, "1831a14e5b529539"),
    "TS_14_LO": (33, 33, 0, {"RULE": 33}, -1.450662, "44226b2426a88181"),
    "TS_14_LS": (54, 26, 28, {"END": 1, "RULE": 53}, 4.594484, "c411d298ad09d6f6"),
    "TS_28_LO": (27, 27, 0, {"RULE": 27}, -1.859421, "9171466cdf376721"),
    "TS_28_LS": (45, 20, 25, {"END": 1, "RULE": 44}, 4.528761, "080417f1d7b32f13"),
    "TS_56_LO": (14, 14, 0, {"RULE": 13, "STOP": 1}, -0.159799, "c4a95930481e0769"),
    "TS_56_LS": (21, 10, 11, {"END": 1, "RULE": 19, "STOP": 1}, 5.650379, "d0e740fef16ca962"),
    "TS_84_LO": (11, 11, 0, {"RULE": 8, "STOP": 3}, -3.145765, "aec23a04beceb3cf"),
    "TS_84_LS": (17, 8, 9, {"END": 1, "RULE": 14, "STOP": 2}, -0.219013, "5a916c3a8cb47a17"),
    "MAX_20_50_4h_LO": (11, 11, 0, {"END": 1, "RULE": 8, "STOP": 2}, 12.741909, "9cab9fa3da7bd26d"),
    "MAX_20_50_4h_LS": (23, 11, 12, {"END": 1, "RULE": 17, "STOP": 5}, 22.88307, "3a43dcbf69ecd577"),
    "MAX_20_50_1d_LO": (7, 7, 0, {"RULE": 4, "STOP": 3}, -2.130763, "cd9e11bfb3579a3e"),
    "MAX_20_50_1d_LS": (15, 8, 7, {"END": 1, "RULE": 10, "STOP": 4}, 2.063883, "fd311e1f5f6ff15a"),
    "MAX_50_200_4h_LO": (10, 10, 0, {"END": 1, "RULE": 3, "STOP": 6}, -2.387435, "7e921d2479c2e84b"),
    "MAX_50_200_4h_LS": (19, 9, 10, {"END": 1, "RULE": 6, "STOP": 11, "TIME": 1}, -2.004455, "eafe43e713d89427"),
    "MAX_50_200_1d_LO": (4, 4, 0, {"RULE": 2, "STOP": 2}, -2.597662, "f17a67831add58ca"),
    "MAX_50_200_1d_LS": (8, 4, 4, {"END": 1, "RULE": 4, "STOP": 3}, -0.605817, "1d7a05fc60ab1dba"),
    "DON_20_10_4h_LS": (35, 18, 17, {"RULE": 22, "STOP": 13}, 35.479625, "31d1db71f9317c46"),
    "DON_20_10_1d_LO": (10, 10, 0, {"RULE": 5, "STOP": 5}, -4.297926, "e34a3ba182ce290d"),
    "DON_20_10_1d_LS": (19, 10, 9, {"END": 1, "RULE": 11, "STOP": 7}, 3.780981, "5d16638cc93a5481"),
    "DON_55_20_4h_LO": (9, 9, 0, {"END": 1, "RULE": 3, "STOP": 5}, 19.911358, "0cc081828157d3fe"),
    "DON_55_20_4h_LS": (16, 9, 7, {"END": 1, "RULE": 8, "STOP": 7}, 38.561751, "fa09bb9d31bc20c2"),
    "DON_55_20_1d_LO": (3, 3, 0, {"RULE": 1, "STOP": 2}, -0.700745, "53b7e8f85c146a89"),
    "DON_55_20_1d_LS": (8, 3, 5, {"END": 1, "RULE": 5, "STOP": 2}, 6.961566, "5b7c70f91983f892"),
    "KEL_20_2_1h": (52, 34, 18, {"STOP": 25, "TARGET": 27}, 23.990145, "617a04f0c1b09a25"),
    "KEL_20_2_4h": (46, 22, 24, {"STOP": 24, "TARGET": 22}, 16.13307, "ae446861f1c9af4f"),
    "BBSQ_20_1h": (35, 17, 18, {"END": 1, "STOP": 13, "TARGET": 19, "TIME": 2}, 22.654288, "965c71b8299666ce"),
    "BBSQ_20_4h": (26, 12, 14, {"END": 1, "STOP": 6, "TARGET": 19}, 30.026968, "045aa8033a650888"),
    "RSI2_1h": (165, 90, 75, {"RULE": 80, "STOP": 85}, -46.93407, "d6f29eb3f5420230"),
    "RSI2_4h": (84, 46, 38, {"END": 1, "RULE": 36, "STOP": 47}, -23.971553, "5443915396abd60e"),
    "BBRE_1h": (65, 29, 36, {"END": 1, "RULE": 35, "STOP": 29}, -14.60941, "be5e6948f2cab37e"),
    "BBRE_4h": (32, 17, 15, {"END": 1, "RULE": 13, "STOP": 18}, -8.897132, "cb37678cedc71f8a"),
    "BOX_n10_mid_ms1.0": (8, 7, 1, {"RULE": 1, "STOP": 4, "TARGET": 3}, 7.124974, "6954d66ce668e4d1"),
    "BOX_n10_2r_ms0.5": (15, 14, 1, {"RULE": 1, "STOP": 8, "TARGET": 6}, 0.215183, "f0737ae6cd9981e1"),
    "BOX_n10_2r_ms1.0": (10, 9, 1, {"RULE": 1, "STOP": 4, "TARGET": 5}, 4.188711, "2d9015885f8e74b2"),
    "BOX_n20_mid_ms0.5": (14, 12, 2, {"RULE": 1, "STOP": 9, "TARGET": 4}, 5.601887, "da53a09d58d2b603"),
    "BOX_n20_mid_ms1.0": (9, 7, 2, {"RULE": 1, "STOP": 5, "TARGET": 3}, 5.527846, "e4361fe38164bb1c"),
    "BOX_n20_2r_ms0.5": (17, 15, 2, {"RULE": 1, "STOP": 8, "TARGET": 8}, 3.738433, "3d2292dfe42029d5"),
    "BOX_n20_2r_ms1.0": (10, 9, 1, {"RULE": 1, "STOP": 4, "TARGET": 5}, 4.229327, "12f8f7244a043c49"),
    "FUND_FADE_24h": (54, 29, 25, {"STOP": 2, "TIME": 52}, 3.428623, "416c0389557e02c5"),
    "FUND_FADE_72h": (34, 18, 16, {"STOP": 8, "TIME": 26}, -0.129705, "0318bdd7bcebac4a"),
    "FUND_FADE_LOW_24h": (36, 22, 14, {"STOP": 2, "TIME": 34}, -1.635491, "e05ea761c8cc533c"),
    "FUND_FADE_LOW_72h": (24, 13, 11, {"STOP": 8, "TIME": 16}, -0.599038, "a7d6edd1e20570a4"),
    "FUND_CARRY": (26, 11, 15, {"RULE": 24, "STOP": 2}, 3.561266, "19d78cf81d5eb0eb"),
}


@pytest.mark.parametrize("vid", PURE)
def test_golden_synthetic_path_per_variant(vid):
    got = golden_summary(vid)
    assert got == GOLDEN_ALL[vid], (vid, got)
    n, n_long, n_short, kinds, _sum, _sha = got
    assert n >= 1
    sides = RG.variants_by_id()[vid]["sides"]
    if sides != "LO":
        assert n_long >= 1 and n_short >= 1, f"{vid}: iki yön de yolda olmalı"
    else:
        assert n_short == 0


#: aile → altın yollarda görülmesi gereken çıkış türleri (stop, hedef/2R, ters sinyal/kural, max_hold, orta bant)
FAMILY_EXITS = {"B": {"STOP", "RULE"}, "C": {"STOP", "RULE", "TIME"}, "D": {"STOP", "RULE"}, "E": {"STOP", "TARGET", "TIME"},
                "F1": {"STOP", "RULE"}, "F2": {"STOP", "TARGET", "RULE"}, "H": {"STOP", "TIME", "RULE"}}


def test_golden_paths_cover_each_family_exits():
    seen: dict[str, set] = {}
    for vid in PURE:
        seen.setdefault(SLC.FAMILY_OF[vid], set()).update(GOLDEN_ALL[vid][3])
    for fam, need in FAMILY_EXITS.items():
        assert need <= seen[fam], (fam, seen[fam])


def test_kernel_runs_of_pure_families_are_deterministic():
    for vid in ("MAX_20_50_4h_LS", "FUND_CARRY", "BBSQ_20_1h"):
        v, d = golden_world(vid)
        a = json.dumps(KN.run(v, d).to_dict(), sort_keys=True)
        b = json.dumps(KN.run(KN.make_variant(vid), golden_world(vid)[1]).to_dict(), sort_keys=True)
        assert a == b


# ============================================================================ çalışma anı saflık yoklaması
_PROBE: dict[str, Any] = {"on": False, "hits": [], "files": set()}
_TB = str(ROOT / "tradingbot") + os.sep


def _audit(event: str, args: tuple) -> None:
    if not _PROBE["on"]:
        return
    if event == "open":
        path = str(args[0]) if args else ""
        if not path.endswith((".py", ".pyc", ".pyi", ".so", ".pth")):
            _PROBE["hits"].append((event, path))
    elif event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "urllib.", "http.", "os.remove",
                           "os.rename", "os.mkdir", "shutil.")):
        _PROBE["hits"].append((event, str(args)[:120]))


sys.addaudithook(_audit)


def _profile(frame, event, arg):
    if event == "call":
        _PROBE["files"].add(frame.f_code.co_filename)
    elif event == "c_call":
        name = getattr(arg, "__name__", "")
        if name in {"time", "time_ns", "monotonic", "perf_counter", "now", "utcnow", "today", "random", "urandom",
                    "getrandbits"}:
            _PROBE["hits"].append(("call:" + name, f"{frame.f_code.co_filename}:{frame.f_lineno}"))


@pytest.mark.parametrize("vid", ["TS_14_LS", "MAX_20_50_4h_LO", "DON_55_20_1d_LS", "KEL_20_2_4h", "BBSQ_20_1h", "RSI2_4h",
                                 "BBRE_1h", "BOX_n20_2r_ms0.5", "FUND_FADE_LOW_24h", "FUND_CARRY"])
def test_pure_families_do_no_io_clock_or_randomness_and_run_only_declared_files(vid):
    import importlib
    for m in SLI.declared_modules(vid):
        importlib.import_module(m)
    var = KN.make_variant(vid)
    fn = S.world_for(vid, var.signal_tf, var.family)
    data = fn(41, days=1) if var.family == "F2" else fn(41, n=500)
    osig, oman = var.signals, var.manage
    calls = Counter()

    def probed(f, kind):
        def wrapper(*a):
            calls[kind] += 1
            _PROBE["on"] = True
            sys.setprofile(_profile)
            try:
                return f(*a)
            finally:
                sys.setprofile(None)
                _PROBE["on"] = False
        return wrapper
    var.signals, var.manage = probed(osig, "signals"), probed(oman, "manage")
    _PROBE["hits"], _PROBE["files"] = [], set()
    res = KN.run(var, data)
    assert _PROBE["hits"] == [], _PROBE["hits"][:5]
    ran = {Path(f).relative_to(ROOT).as_posix() for f in _PROBE["files"] if f.startswith(_TB)}
    assert ran and ran <= set(SLI.module_files(vid)), sorted(ran - set(SLI.module_files(vid)))
    assert calls["signals"] > 50 and (calls["manage"] > 0 or not res.trades)


# ============================================================================ parite (a) saf ailelerde + 1h yedeği (m-C)
@pytest.mark.parametrize("vid", ["TS_28_LS", "MAX_20_50_4h_LS", "DON_55_20_4h_LS", "KEL_20_2_1h", "BBSQ_20_4h", "RSI2_1h",
                                 "BBRE_4h", "FUND_FADE_72h", "FUND_FADE_LOW_24h", "FUND_CARRY"])
def test_parity_a_pure_family_kernel_vs_ledger(vid):
    var = KN.make_variant(vid)
    data = S.world_for(vid, var.signal_tf, var.family)(11)
    kr = KN.run(var, data)
    refs, _rej = PA.reference_run(vid, data)
    a = PA.compare_a([(vid, kr, refs)], min_trades=1)
    th = RG.LIB_V1["thresholds"]["parity_kernel"]
    assert a.n_matched >= 10 and a.identity_share == 1.0, (a.only_kernel[:3], a.only_ref[:3])
    assert a.median_abs_dr <= th["max_median_abs_dr"] and a.p95_abs_dr <= th["max_p95_abs_dr"] and a.passed, a.to_dict()


def _holed(data: KN.SeriesData, seed: int, share: float) -> KN.SeriesData:
    """1h barlarının bir kısmını sil (kısmen eksik sinyal barları)."""
    import random as _r
    rnd = _r.Random(seed)
    hs = [r for r in data.bars["1h"] if rnd.random() > share]
    return KN.SeriesData(symbol=data.symbol, bars=dict(data.bars, **{"1h": hs}), btc_1d=data.btc_1d, funding=data.funding,
                         price_tick=data.price_tick)


@pytest.mark.parametrize("vid, world, share", [("K_FM", lambda: S.fm_world(21, n=900), 0.1),
                                               ("DON_55_20_4h_LS", lambda: S.d4_world(12, n=1400), 0.06),
                                               ("KEL_20_2_4h", lambda: S.d4_world(13, n=1400), 0.06)])
def test_reference_takes_the_kernel_one_tick_fallback_on_partial_1h_data(vid, world, share):
    """m-C: sinyal barının 1h barları KISMEN eksikse başvuru da barı tek OHLC tiki olarak işler ve çıkış kararı gereken
    barda `PATH_1H_GAP` işaretler; işaretli işlem kümesi çekirdeğinkiyle aynı, ΔR eşikte."""
    data = _holed(world(), 5, share)
    kr = KN.run(KN.make_variant(vid), data)
    refs, _rej = PA.reference_run(vid, data)
    a = PA.compare_a([(vid, kr, refs)], min_trades=1)
    assert a.identity_share == 1.0 and a.passed, a.to_dict()
    kf = {t.key() for t in kr.trades if KN.FLAG_PATH_GAP in t.flags}
    rf = {(r.symbol, int(r.signal_ts), r.side) for r in refs if KN.FLAG_PATH_GAP in r.flags}
    assert kf == rf and kf, (len(kf), len(rf))

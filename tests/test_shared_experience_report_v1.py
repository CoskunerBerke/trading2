# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — RAPOR (`report.py`), CLI (`shared-experience-report` / `-status`) ve panel kartı (2026-09-29).

Kapsam (SPEC_V1 §11 / §13 T7; KARARLAR.md 4, 5, 7, 10 — SPEC'e üstün):

* Soru: "bu durumda, bu kurulumda daha önce kazandık mı kaybettik mi?" — (kurulum|aile, yön, durum kovası) hücresi,
  COİNLER ARASI havuz; gerçek ve karşı-olgusal AYRI sütun; karşı-olgusal net YALNIZ cf_label_v2+; eski brüt ayrı satır.
* En yüksek revizyon; `row_id`e denk tekilleştirme (arşiv → sıcak); A15 bayrağı + süzülmüş görünüm; kohort dilimi
  (politika / öğrenme-ekstra / öğrenme öncesi); hüküm kademeleri (n<10 sayı yok, n<30 tanımlayıcı, n≥30 hüküm; karnenin
  etiketleri, iid VE küme aralığı); geri çekilme yapı → hacim → BTC → oynaklık → trend; coin başına kısmi havuz (k=20).
* "Bu durumu daha önce gördük mü?": `--for-symbol` (depodaki son OK anlık görüntü), `--snapshot-json`, `--live` (CSV).
* Salt okur: CLI yalnız `--out` / `--summary-out` yazar; motor yüklenmez; ağ yok. Panel kartı paketi İÇE AKTARMAZ,
  yalnız `status.json` + `report_summary.json` okur; katman yoksa kart boş (sayfa aynı).
* Akış belleği: 200 bin satırlık depoda tracemalloc tepe < 60 MB.

Eski kodda (HEAD 7ec832c) `tradingbot.shared_experience.report`, CLI komutları ve `StateReader.shared_experience` YOKTUR →
her test düşer. Bütün sayılar SENTETİKTİR.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import textwrap
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.shared_experience import report as X  # noqa: E402
from tradingbot.shared_experience import rows as R  # noqa: E402
from tradingbot.shared_experience import situation as S  # noqa: E402
from tradingbot.shared_experience.store import ExperienceStore  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 9, 20, tzinfo=UTC)
SINCE = "2026-09-28T21:20:00+00:00"
NOW = datetime(2026, 9, 29, 12, 0, 30, tzinfo=UTC)
ENV = R.make_env(recorded_at=NOW - timedelta(hours=1), learning_since=SINCE)
BOX = "strategy_paper_box"
FULL = {"trend": "UP", "vol": "NORMAL", "btc": "UP", "volume": "NORMAL", "structure": "HH_HL"}


# ============================================================================ satır kurucuları (gerçek rows.py yoluyla)
def _snap(sym, t, *, status="OK", sha=None, **dims):
    d = {**FULL, **dims}
    return {"schema_id": S.SCHEMA_ID, "schema_sha": sha or S.SCHEMA_SHA, "symbol": sym,
            "as_of_ms": int(t.timestamp() * 1000), "status": status, "missing": [], "h4_trend": d["trend"],
            "h4_vol_regime": d["vol"], "btc_h4_trend": d["btc"], "h4_vol_bucket": d["volume"], "h4_structure": d["structure"]}


def _cf_out(r, lv="cf_label_v3", *, gross=None):
    g = (r + 0.1) if gross is None else gross
    o = {"label_version": lv, "r_gross": g, "r_multiple": g, "exit_reason": "TARGET" if (r or 0) > 0 else "STOP",
         "mfe_pct": 1.0, "mae_pct": 0.5, "bars": 3}
    if lv not in (None, "cf_label_v1", "cf_label_v1c"):
        o.update({"r_net": r, "cost_r": g - r})
    if lv == "cf_label_v1c":
        o["r_net_approx"] = r
    if lv is None:
        o.pop("label_version")
    return o


def _cf_src(book, cid, sym, side, st, t, *, bb=None):
    f = {"setup_type": st}
    if R.BOOKS[book][1] == R.PATTERN:
        f = {"family": st}
    if bb:
        f["baseline_blocked_by"] = list(bb)
    return {"id": cid, "book": R.BOOKS[book][0], "symbol": sym, "direction": side, "signal_key": "signal_ts:%s" % cid,
            "features": f, "created_at": t.isoformat(), "entry": 100.0, "stop": 98.0 if side == "LONG" else 102.0,
            "tf_minutes": 240, "reason_not_opened": ["TOTAL_OPEN_RISK"], "label_ts": t.isoformat()}


def cf_rows(book, cid, sym, side, st, t, r, *, snap=True, lv="cf_label_v3", bb=None, env=ENV, final_status="LABELLED",
            origin=R.LIVE, dims=None, snap_status="OK"):
    """rev 0 PENDING (anlık görüntülü) + rev 1 son durum (LABELLED ise sonuçlu)."""
    src = _cf_src(book, cid, sym, side, st, t, bb=bb)
    sn = _snap(sym, t, status=snap_status, **(dims or {})) if snap else None
    out = [R.cf_row(src, book=book, rev=0, status="PENDING", env=env, origin=origin, snapshot=sn,
                    snapshot_status=sn["status"] if sn else None)]
    if final_status == "LABELLED":
        out.append(R.cf_row({**src, "outcome": _cf_out(r, lv)}, book=book, rev=1, status="LABELLED", env=env, origin=origin))
    elif final_status != "PENDING":
        out.append(R.cf_row(src, book=book, rev=1, status=final_status, env=env, origin=origin))
    return out


def real_rows(book, tid, sym, side, st, t, r, *, snap=True, learning=None, env=ENV, final=True, dims=None,
              snap_status="OK", entry=True, origin=R.LIVE, open_only=False):
    f = {"risk_usdt": 2.0, "funding_coverage": {"complete": bool(final)}}
    if learning is not None:
        f["learning"] = learning
    if R.BOOKS[book][1] == R.PATTERN:
        f["family"] = st
    stop = 98.0 if side == "LONG" else 102.0
    pos = {"id": tid, "opened_at": t.isoformat(), "symbol": sym, "side": side, "setup_type": st, "entry_avg": 100.0,
           "initial_stop": stop, "initial_qty": 1.0, "features": f}
    sn = _snap(sym, t, status=snap_status, **(dims or {})) if snap else None
    rows = [R.entry_row(pos, book=book, env=env, origin=origin, snapshot=sn, snapshot_status=sn["status"] if sn else None)] \
        if entry else []
    if open_only:
        return rows
    rec = {"id": tid, "opened_at": t.isoformat(), "closed_at": (t + timedelta(hours=5)).isoformat(), "symbol": sym,
           "side": side, "setup_type": st, "r_multiple": r, "entry": 100.0, "quantity": 1.0, "entry_fee": 0.05,
           "exit_fee": 0.05, "slippage_cost": 0.06, "funding_paid": 0.0, "funding_received": 0.0, "features": f,
           "mfe_pct": 1.5, "mae_pct": 0.7, "exit_reason": "TARGET" if r > 0 else "STOP"}
    rows.append(R.outcome_row(rec, book=book, rev=0, final=final, env=env, origin=origin))
    return rows


def write(root, rows, *, rotate_every=None, hot_max_lines=100_000):
    st = ExperienceStore(root, hot_max_lines=hot_max_lines)
    if rotate_every:
        for i in range(0, len(rows), rotate_every):
            w, rej = st.append_rows(rows[i:i + rotate_every])
            assert rej == 0, st.last_append
            st.rotate()
    else:
        w, rej = st.append_rows(rows)
        assert rej == 0, st.last_append
    return st


def run(root, **kw):
    q = X.Query(**{k: v for k, v in kw.items() if k in X.Query.__dataclass_fields__})
    rest = {k: v for k, v in kw.items() if k not in X.Query.__dataclass_fields__}
    return X.run(root, q, now=NOW, **rest)


def group(doc, name, gb="setup"):
    hit = [g for g in doc["groups"] if g["group"] == name and g["group_by"] == gb]
    assert len(hit) == 1, [g["group"] for g in doc["groups"]]
    return hit[0]


def _tree(root: Path) -> dict[str, tuple]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            out[str(p.relative_to(root)) + "/"] = ()
    return out


def _scorecard():
    spec = importlib.util.spec_from_file_location("bot_scorecard_xp_report", ROOT / "scripts" / "bot_scorecard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rs(n, mean, sd, seed):
    rnd = random.Random(seed)
    return [rnd.gauss(mean, sd) for _ in range(n)]


# ============================================================================ sabitler tek kaynağa bağlı
def test_constants_bind_to_scorecard_store_collector_pattern_report_wilson_and_dashboard():
    sc = _scorecard()
    assert (X.V_THIN, X.V_LOSS, X.V_WIN, X.V_OPEN) == (sc.V_THIN, sc.V_LOSS, sc.V_WIN, sc.V_OPEN)
    from tradingbot.pattern_trader.report import MIN_TRADES_FOR_VERDICT
    assert X.MIN_N_VERDICT == MIN_TRADES_FOR_VERDICT == 30 and X.Query().min_n == 30
    assert X.MIN_N_DESCRIPTIVE == 10 and X.SHRINK_K == 20 and X.Query().shrink_k == 20
    assert X.BACKOFF_ORDER == ("structure", "volume", "btc", "vol", "trend")
    assert X.BANNER_TR == "tanımlayıcı; karar yok; kanıt değil" and X.BANNER_TR in X.BANNER_LINE_TR
    from tradingbot.learn.journal_archive import MANIFEST_NAME, SEGMENTS_DIRNAME
    from tradingbot.shared_experience import collector as C
    from tradingbot.shared_experience import store as ST
    assert (X.HOT_FILE, X.ARCHIVE_DIR, X.MANIFEST_FILE, X.SEGMENTS_DIR) == (ST.HOT_FILE, ST.ARCHIVE_DIR, MANIFEST_NAME,
                                                                           SEGMENTS_DIRNAME)
    assert (X.STATUS_FILE, X.CURSOR_FILE) == (C.STATUS_FILE, C.CURSOR_FILE)
    from tradingbot.patterns.engine import wilson
    for k, n in ((0, 1), (3, 10), (17, 40), (40, 40), (123, 1000)):
        assert X.wilson(k, n) == pytest.approx(wilson(k, n), abs=1e-12)
    from tradingbot.dashboard import state as DS
    assert (DS.XP_DIR, DS.XP_STATUS_FILE, DS.XP_SUMMARY_FILE, DS.XP_SUMMARY_SCHEMA, DS.XP_BANNER_TR) == \
        (C.XpSettings().state_dir, X.STATUS_FILE, X.SUMMARY_FILE, X.SUMMARY_SCHEMA, X.BANNER_TR)
    # rapor boyut değerleri situation.bucket'ın ürettiği değerleri kapsar
    for trend in ("UP", "DOWN", "RANGE", None):
        b = S.bucket({"h4_trend": trend, "h4_vol_regime": None}, "LONG")
        assert b["trend"] in X.DIM_VALUES["trend"] and b["vol"] in X.DIM_VALUES["vol"]


# ============================================================================ revizyon + tekilleştirme
def test_highest_revision_wins_duplicates_are_idempotent_and_archive_is_read_before_hot(tmp_path):
    root = tmp_path / "xp"
    t = T0 + timedelta(days=1)
    src = _cf_src(BOX, "cf_a", "SOL/USDT", "LONG", "box_fade", t)
    rev0 = R.cf_row(src, book=BOX, rev=0, status="PENDING", env=ENV, snapshot=_snap("SOL/USDT", t), snapshot_status="OK")
    rev1 = R.cf_row({**src, "outcome": _cf_out(-1.0, "cf_label_v2")}, book=BOX, rev=1, status="LABELLED", env=ENV)
    rev2 = R.cf_row({**src, "outcome": _cf_out(2.0, "cf_label_v3")}, book=BOX, rev=2, status="LABELLED", env=ENV)
    # gerçek: giriş + kapanış rev 0 (kesin değil) + rev 1 (geç funding sonrası kesin)
    ent, out0 = real_rows(BOX, "F00001", "SOL/USDT", "LONG", "box_fade", t, -0.5, final=False)
    rec1 = dict(out0)
    out1 = R.outcome_row({"id": "F00001", "opened_at": t.isoformat(), "closed_at": (t + timedelta(hours=5)).isoformat(),
                          "symbol": "SOL/USDT", "side": "LONG", "setup_type": "box_fade", "r_multiple": -0.4, "entry": 100.0,
                          "quantity": 1.0, "entry_fee": 0.05, "exit_fee": 0.05, "slippage_cost": 0.06, "funding_paid": 0.0,
                          "funding_received": 0.0, "features": {"risk_usdt": 2.0, "funding_coverage": {"complete": True}}},
                         book=BOX, rev=1, final=True, env=ENV)
    assert rec1["rev"] == 0 and out1["rev"] == 1
    st = write(root, [rev0, rev1, ent, out0], rotate_every=2, hot_max_lines=2)
    assert list((root / "archive" / "segments").glob("*.jsonl.gz")), "arşiv segmenti oluşmalı"
    st.append_rows([out1])
    # yeni revizyon (rev2) iki kez (aynı row_id), ardından çökme kopyaları: arşivdeki rev0/rev1 (artık ESKİ) ve giriş
    hot = root / "experience.jsonl"
    with hot.open("ab") as fh:
        for r in (rev2, rev2, rev0, rev1, ent):
            fh.write((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))
    o = X.scan(root)
    assert o.read.segments_read >= 1 and o.n == 2
    doc = X.build(o, X.Query(kind="both", snapshot="ok"), now=NOW)
    g = group(doc, "strategy_paper_box|box_fade|LONG")
    lv = g["levels"][0]
    assert lv["cf"]["n"] == 1 and lv["real"]["n"] == 1
    cf_i = int(np.flatnonzero(o.src == 1)[0])
    re_i = int(np.flatnonzero(o.src == 0)[0])
    assert o.r_net[cf_i] == 2.0, "en yüksek revizyon (rev 2) kullanılır"
    assert o.r_net[re_i] == -0.4 and bool(o.final[re_i]), "gerçek: en yüksek kapanış revizyonu (kesin)"
    assert o.counts["dup_rows"] == 2 and o.counts["stale_revs"] == 2    # (rev2 + giriş) aynı row_id; rev0/rev1 eski
    assert o.counts["xp_cf"] == 6 and o.counts["keys"] == 2


# ============================================================================ net sözleşmesi + sütun ayrımı
def test_net_contract_real_and_cf_are_separate_columns_and_legacy_gross_never_enters_net(tmp_path):
    root = tmp_path / "xp"
    t = T0 + timedelta(days=1)
    rows = []
    rows += cf_rows(BOX, "c_v3", "SOL/USDT", "LONG", "box_fade", t, 1.0, lv="cf_label_v3")
    rows += cf_rows(BOX, "c_v2", "ETH/USDT", "LONG", "box_fade", t, -0.5, lv="cf_label_v2")
    rows += cf_rows(BOX, "c_v1", "XRP/USDT", "LONG", "box_fade", t, 5.0, lv="cf_label_v1")
    rows += cf_rows(BOX, "c_none", "ADA/USDT", "LONG", "box_fade", t, 7.0, lv=None)
    rows += cf_rows(BOX, "c_v1c", "DOT/USDT", "LONG", "box_fade", t, 9.0, lv="cf_label_v1c")
    rows += cf_rows(BOX, "c_unk", "BNB/USDT", "LONG", "box_fade", t, 3.0, lv="cf_label_vX")
    # sahte satır: r_basis NET beyan ediyor ama etiket sürümü v1 — çifte kilit net saymaz
    forged = cf_rows(BOX, "c_forged", "LTC/USDT", "LONG", "box_fade", t, 4.0, lv="cf_label_v3")
    forged[1] = {**forged[1], "label_version": "cf_label_v1"}
    rows += forged
    for i in range(3):
        rows += real_rows(BOX, "F%05d" % i, "SOL/USDT", "LONG", "box_fade", t + timedelta(hours=i), [-1.0, 0.5, 2.0][i])
    write(root, rows)
    doc = run(root)
    lv = group(doc, "strategy_paper_box|box_fade|LONG")["levels"][0]
    assert lv["cf"]["n"] == 2 and lv["real"]["n"] == 3
    assert lv["cf_gross_legacy"]["n"] == 3                  # yok + v1 + v1c (v1c tahmini ASLA net değil)
    assert lv["coverage"]["cf_unknown_label"] == 1 and lv["coverage"]["cf_net_unavailable"] == 1
    o = X.scan(root)
    cf_net = np.sort(o.r_net[(o.src == 1) & np.isfinite(o.r_net)])
    assert cf_net.tolist() == [-0.5, 1.0], "yalnız v2 ve v3 net değerleri"
    assert sorted(o.r_net[o.src == 0].tolist()) == [-1.0, 0.5, 2.0]
    assert doc["coverage"]["cf_net"] == 2 and doc["coverage"]["cf_gross_legacy"] == 3


# ============================================================================ hüküm kademeleri
def test_verdict_matrix_tiers_and_iid_cluster_disagreement():
    v = X.verdict
    assert v(9, 9, [0.1, 0.2], [0.1, 0.2]) == (X.TIER_INSUFFICIENT, X.INSUFFICIENT_SAMPLE, "N_LT_10")
    assert v(10, 10, [0.1, 0.2], [0.1, 0.2])[:2] == (X.TIER_DESCRIPTIVE, X.INSUFFICIENT_SAMPLE)
    assert v(29, 20, [0.1, 0.2], [0.1, 0.2])[:2] == (X.TIER_DESCRIPTIVE, X.INSUFFICIENT_SAMPLE)
    assert v(30, 10, [-0.5, -0.1], [-0.6, -0.05])[:2] == (X.TIER_VERDICT, X.LOSS)
    assert v(30, 10, [0.1, 0.5], [0.05, 0.6])[:2] == (X.TIER_VERDICT, X.WIN)
    assert v(30, 10, [0.1, 0.5], [-0.1, 0.6])[:2] == (X.TIER_VERDICT, X.UNDECIDED), "iid ↔ küme çelişkisi → BELİRSİZ"
    assert v(30, 10, [-0.5, -0.1], [-0.6, 0.02])[:2] == (X.TIER_VERDICT, X.UNDECIDED)
    assert v(30, 4, [0.1, 0.5], [0.1, 0.5])[:2] == (X.TIER_VERDICT, X.INSUFFICIENT_SAMPLE), "küme < 5"
    assert v(30, 10, None, [0.1, 0.5])[:2] == (X.TIER_VERDICT, X.INSUFFICIENT_SAMPLE)
    assert v(12, 12, [0.1, 0.5], [0.1, 0.5], min_n=12)[:2] == (X.TIER_VERDICT, X.WIN)
    assert X.VERDICT_TR[X.LOSS] == "ZARARDA (kanıtlı)" and X.VERDICT_TR[X.WIN] == "KÂRDA (kanıtlı, PAPER)"
    with pytest.raises(X.ReportError):
        X.Query(min_n=9).validate()


def _obs_from(tmp_path, rs, *, coins=None, days=None, name="xp"):
    root = tmp_path / name
    rows = []
    for i, r in enumerate(rs):
        sym = (coins[i] if coins else "SOL/USDT")
        t = T0 + timedelta(days=(days[i] if days else i % 9), hours=i % 7)
        rows += real_rows(BOX, "F%05d" % i, sym, "LONG", "box_fade", t, r)
    write(root, rows)
    return X.scan(root)


def test_column_stats_are_deterministic_bounded_and_hide_numbers_below_ten(tmp_path):
    rs = _rs(40, 0.3, 1.0, 1)
    coins = ["C%d/USDT" % (i % 8) for i in range(40)]
    o = _obs_from(tmp_path, rs, coins=coins)
    idx = np.arange(o.n)
    a, b = X.column_stats(o, idx), X.column_stats(o, idx)
    assert a == b, "deterministik tohum"
    assert a["n"] == 40 and a["n_final"] == 40 and a["tier"] == X.TIER_VERDICT
    wins = sum(1 for r in rs if r > 0)
    assert a["wins"] == wins and a["win_rate_ci95"] == [round(x, 4) for x in X.wilson(wins, 40)]
    assert a["mean_r"] == round(float(np.mean(rs)), 4) and a["median_r"] == round(float(np.median(rs)), 4)
    for key in ("mean_r_ci95", "mean_r_ci95_cluster"):
        lo, hi = a[key]
        assert lo <= a["mean_r"] <= hi and hi - lo < 1.5
    lo, hi = a["median_r_ci95"]
    assert lo <= a["median_r"] <= hi
    assert a["ci_method"] == {"iid": "bootstrap", "cluster": "bootstrap"}
    assert a["mean_cost_r"] == pytest.approx(0.08, abs=1e-6), "gerçek maliyet: (ücret 0.10 + kayma 0.06) / risk 2"
    assert a["mean_hold_h"] == 5.0
    small = X.column_stats(o, idx[:9])
    assert small["tier"] == X.TIER_INSUFFICIENT and small["verdict_tr"] == X.V_THIN
    assert small["mean_r"] is None and small["win_rate"] is None and small["coins"] is None
    ten = X.column_stats(o, idx[:10])
    assert ten["tier"] == X.TIER_DESCRIPTIVE and ten["mean_r"] is not None and ten["verdict_tr"] == X.V_THIN
    # büyük n: normal yaklaşım (süre sınırlı), küme bootstrap'ı küme sayısıyla
    ci, m = X._boot_ci(np.linspace(-1, 1, X.BOOT_MAX_N + 1))
    assert m == "normal" and ci[0] < 0 < ci[1]


# ============================================================================ geri çekilme
def test_backoff_order_levels_and_answer_level_is_first_reaching_min_n(tmp_path):
    lv = X.backoff_levels(FULL)
    assert [d for _, d in lv] == [[], ["structure"], ["structure", "volume"], ["structure", "volume", "btc"],
                                  ["structure", "volume", "btc", "vol"], ["structure", "volume", "btc", "vol", "trend"]]
    assert lv[-1][0] == {}
    assert [sorted(d) for d, _ in X.backoff_levels({"vol": "HIGH", "trend": "UP"})] == [["trend", "vol"], ["trend"], []]
    assert len(X.backoff_levels(FULL, backoff=False)) == 1
    root = tmp_path / "xp"
    rows, i = [], 0
    # tam hücre 5, yapı farklı 7 (L1'de 12), hacim farklı 25 (L2'de 37 → cevap), BTC farklı 3
    for n, dims in ((5, {}), (7, {"structure": "MIXED"}), (25, {"structure": "LH_LL", "volume": "HIGH"}),
                    (3, {"btc": "DOWN", "structure": "MIXED"})):
        for _ in range(n):
            rows += real_rows(BOX, "F%05d" % i, "C%d/USDT" % (i % 6), "LONG", "box_fade", T0 + timedelta(days=i % 8, hours=i),
                              0.1 * ((i % 5) - 2), dims=dims)
            i += 1
    write(root, rows)
    doc = run(root, dims=dict(FULL), kind="real")
    g = group(doc, "strategy_paper_box|box_fade|LONG")
    ns = [lv["real"]["n"] for lv in g["levels"]]
    assert ns == [5, 12, 37, 40, 40, 40] and ns == sorted(ns)
    assert g["answer"]["real"]["level"] == 2 and g["answer"]["real"]["n"] == 37 and g["answer"]["cf"] is None
    assert g["levels"][2]["dropped"] == ["structure", "volume"]
    nb = run(root, dims=dict(FULL), kind="real", backoff=False)
    assert len(group(nb, "strategy_paper_box|box_fade|LONG")["levels"]) == 1
    txt = X.render_tr(doc)
    assert "L2 − yapı, hacim" in txt and "CEVAP: gerçek → L2 (n=37" in txt


# ============================================================================ havuz + kısmi havuz
def test_cell_is_pooled_across_coins_and_partial_pooling_is_shown_next_to_pooled(tmp_path):
    rs = [1.0] * 10 + [-1.0] * 30
    coins = ["AAA/USDT"] * 10 + ["BBB/USDT"] * 30
    o = _obs_from(tmp_path, rs, coins=coins, days=[i % 10 for i in range(40)])
    st = X.column_stats(o, np.arange(o.n), focus_coin="AAA/USDT")
    assert st["mean_r"] == -0.5, "coinler arası havuz"
    by = {c["coin"]: c for c in st["coins"]}
    assert by["AAA/USDT"]["shrunk_mean_r"] == pytest.approx((10 * 1 + 20 * -0.5) / 30, abs=1e-4)
    assert by["BBB/USDT"]["shrunk_mean_r"] == pytest.approx((30 * -1 + 20 * -0.5) / 50, abs=1e-4)
    assert st["coin"]["coin"] == "AAA/USDT" and st["coin"]["n"] == 10
    new = X.column_stats(o, np.arange(o.n), focus_coin="NEW/USDT")
    assert new["coin"] == {"coin": "NEW/USDT", "n": 0, "mean_r": None, "shrunk_mean_r": -0.5}, "yeni coin havuzdan yararlanır"
    k0 = X.column_stats(o, np.arange(o.n), k=0.0)
    assert {c["coin"]: c["shrunk_mean_r"] for c in k0["coins"]} == {"AAA/USDT": 1.0, "BBB/USDT": -1.0}
    assert X.shrunk_mean(4, 2.0, 0.0, 20) == pytest.approx(8 / 24)
    assert X.shrunk_mean(0, None, 0.3) == 0.3
    assert X.coin_of("sol") == X.coin_of("SOLUSDT") == X.coin_of("SOL/USDT:USDT") == X.coin_of("sol-usdt") == "SOL/USDT"


# ============================================================================ kohort + A15
def test_real_cohort_split_matches_scorecard_classes_and_cohort_filter(tmp_path):
    root = tmp_path / "xp"
    after, before = datetime(2026, 9, 29, 1, tzinfo=UTC), datetime(2026, 9, 27, tzinfo=UTC)
    rows, i = [], 0
    for n, lr, t in ((11, {"learning_unlocked_by": []}, after), (12, {"learning_unlocked_by": ["RISK_CAP"]}, after),
                     (3, None, after), (13, None, before)):
        for _ in range(n):
            rows += real_rows(BOX, "F%05d" % i, "C%d/USDT" % (i % 5), "LONG", "box_fade", t + timedelta(minutes=i), 0.2,
                              learning=lr)
            i += 1
    write(root, rows)
    doc = run(root, kind="real")
    lv = group(doc, "strategy_paper_box|box_fade|LONG")["levels"][0]
    assert lv["real"]["n"] == 39
    assert {c: lv["real_by_cohort"][c]["n"] for c in X.COHORT_ORDER} == {"policy": 14, "learning_extra": 12, "before": 13}
    ex = run(root, kind="real", cohort="extra")
    assert group(ex, "strategy_paper_box|box_fade|LONG")["levels"][0]["real"]["n"] == 12
    pre = run(root, kind="real", cohort="pre")
    assert group(pre, "strategy_paper_box|box_fade|LONG")["levels"][0]["real"]["n"] == 13


def test_a15_records_stay_in_cf_stats_with_flag_and_a_filtered_view_excludes_them(tmp_path):
    root = tmp_path / "xp"
    rows = []
    for i in range(24):
        rows += cf_rows(BOX, "c%d" % i, "C%d/USDT" % (i % 6), "LONG", "box_fade", T0 + timedelta(days=i % 6, hours=i),
                        -1.0 if i < 12 else 0.5, bb=["POSITION_OPEN"] if i < 12 else None)
    write(root, rows)
    doc = run(root, kind="cf")
    lv = group(doc, "strategy_paper_box|box_fade|LONG")["levels"][0]
    assert lv["cf"]["n"] == 24 and lv["cf"]["n_a15"] == 12 and lv["cf"]["mean_r"] == -0.25
    assert lv["cf_excl_a15"]["n"] == 12 and lv["cf_excl_a15"]["mean_r"] == 0.5
    assert doc["coverage"]["cf_net_a15"] == 12
    assert "[A15 bayraklı 12]" in X.render_tr(doc)


# ============================================================================ anlık görüntü süzgeci
def test_snapshot_filter_default_ok_only_and_other_schema_is_never_mixed(tmp_path):
    root = tmp_path / "xp"
    t = T0 + timedelta(days=1)
    rows = []
    rows += real_rows(BOX, "F1", "SOL/USDT", "LONG", "box_fade", t, 1.0)
    rows += real_rows(BOX, "F2", "SOL/USDT", "LONG", "box_fade", t, 1.0, snap_status="PARTIAL")
    rows += real_rows(BOX, "F3", "SOL/USDT", "LONG", "box_fade", t, 1.0, snap_status="GAP")
    rows += real_rows(BOX, "F4", "SOL/USDT", "LONG", "box_fade", t, 1.0, entry=False)       # girişi kayıp
    other = real_rows(BOX, "F5", "SOL/USDT", "LONG", "box_fade", t, 1.0)
    other[0] = {**other[0], "snapshot": {**other[0]["snapshot"], "schema_sha": "0000000000000000"}}
    rows += other
    write(root, rows)
    n = {f: group(run(root, snapshot=f, kind="real"), "strategy_paper_box|box_fade|LONG")["levels"][0]["real"]["n"]
         for f in ("ok", "ok+partial")}
    assert n == {"ok": 1, "ok+partial": 2}
    doc = run(root, snapshot="any", kind="real")
    lv = group(doc, "strategy_paper_box|box_fade|LONG")["levels"][0]
    assert lv["real"]["n"] == 5 and lv["coverage"]["real_entry_missing"] == 1
    assert doc["source"]["snapshot_schema_other"] == 1
    mix = doc["coverage"]["snapshot_status_mix_all_rows"]["real"]
    assert mix == {"GAP": 1, "NONE": 1, "OK": 1, "PARTIAL": 1, "SCHEMA_OTHER": 1}
    # başka şema sürümünün kovası UNKNOWN'a düşer (tam hücrede OK satırıyla karışmaz)
    full = run(root, snapshot="any", kind="real", dims=dict(FULL), backoff=False)
    assert group(full, "strategy_paper_box|box_fade|LONG")["levels"][0]["real"]["n"] == 3


# ============================================================================ aile havuzu
def test_family_pools_across_books_but_never_opposite_logics_or_sides(tmp_path):
    root = tmp_path / "xp"
    rows, i = [], 0
    for book, st, side in (("strategy_paper", "trend", "LONG"), ("main", "pullback", "LONG"), (BOX, "box_fade", "LONG"),
                           ("strategy_paper", "trend", "SHORT")):
        for _ in range(4):
            rows += real_rows(book, "F%05d" % i, "SOL/USDT", side, st, T0 + timedelta(hours=i), 0.1)
            i += 1
    write(root, rows)
    doc = run(root, kind="real")
    fam = {g["group"]: g["levels"][0]["real"]["n"] for g in doc["groups"] if g["group_by"] == "family"}
    assert fam == {"TREND|LONG": 8, "FADE|LONG": 4, "TREND|SHORT": 4}
    setups = {g["group"] for g in doc["groups"] if g["group_by"] == "setup"}
    assert setups == {"strategy_paper|trend|LONG", "main|pullback|LONG", "strategy_paper_box|box_fade|LONG",
                      "strategy_paper|trend|SHORT"}
    only = run(root, kind="real", group_by="family", family="TREND", side="LONG")
    assert [g["group"] for g in only["groups"]] == ["TREND|LONG"]
    assert [g["group"] for g in run(root, kind="real", book="b1_box_fade", group_by="setup")["groups"]] == \
        ["strategy_paper_box|box_fade|LONG"]
    assert [g["group"] for g in run(root, kind="real", setup="PULLBACK", group_by="setup")["groups"]] == \
        ["main|pullback|LONG"]


# ============================================================================ "bu durumu daha önce gördük mü?"
def test_for_symbol_uses_latest_ok_snapshot_and_selects_matching_cells(tmp_path):
    root = tmp_path / "xp"
    rows = []
    rows += cf_rows(BOX, "s1", "SOL/USDT", "LONG", "box_fade", T0, 0.1, dims={"trend": "UP"})
    rows += cf_rows(BOX, "s2", "SOL/USDT", "LONG", "box_fade", T0 + timedelta(days=2), 0.1, dims={"trend": "DOWN", "vol": "HIGH"})
    rows += cf_rows(BOX, "s3", "SOL/USDT", "LONG", "box_fade", T0 + timedelta(days=3), 0.1, dims={"trend": "RANGE"},
                    snap_status="PARTIAL")
    for i in range(15):                                        # aynı durum, BAŞKA coinler (havuz)
        rows += cf_rows(BOX, "o%d" % i, "C%d/USDT" % (i % 5), "LONG", "box_fade", T0 + timedelta(days=i % 5, hours=i),
                        0.4, dims={"trend": "DOWN", "vol": "HIGH"})
    write(root, rows)
    for alias in ("SOL/USDT", "sol", "SOLUSDT", "SOL/USDT:USDT"):
        doc = run(root, for_symbol=alias)
        assert doc["situation"]["mode"] == "symbol" and doc["situation"]["snapshot_status"] == "OK"
        assert doc["situation"]["dims"] == {**FULL, "trend": "DOWN", "vol": "HIGH"}
        assert doc["situation"]["as_of"].startswith("2026-09-22")
    g = group(doc, "strategy_paper_box|box_fade|LONG")
    lv0 = g["levels"][0]
    assert lv0["dims"] == doc["situation"]["dims"] and lv0["cf"]["n"] == 16
    assert lv0["cf"]["coin"]["coin"] == "SOL/USDT" and lv0["cf"]["coin"]["n"] == 1
    assert "SOL/USDT: coin n=1" in X.render_tr(doc) and "DURUM  trend=DOWN" in X.render_tr(doc)
    part = run(root, for_symbol="SOL", snapshot="ok+partial")
    assert part["situation"]["dims"]["trend"] == "RANGE" and part["situation"]["snapshot_status"] == "PARTIAL"
    over = run(root, for_symbol="SOL", dims={"vol": "LOW"})
    assert over["situation"]["dims"]["vol"] == "LOW" and over["situation"]["dims"]["trend"] == "DOWN"
    none = run(root, for_symbol="DOGE/USDT")
    assert none["situation"] is None and any("DOGE/USDT" in n for n in none["notes_tr"])


def test_snapshot_json_explicit_dims_and_invalid_queries(tmp_path):
    root = tmp_path / "xp"
    write(root, cf_rows(BOX, "a", "SOL/USDT", "LONG", "box_fade", T0, 0.1))
    snap = _snap("XRP/USDT", T0, trend="DOWN", structure="LH_LL")
    doc = run(root, snapshot_doc={"snapshot": snap})
    assert doc["situation"]["mode"] == "snapshot" and doc["situation"]["dims"]["structure"] == "LH_LL"
    assert X.parse_situation("trend=up, vol=HIGH;yapı=mixed") == {"trend": "UP", "vol": "HIGH", "structure": "MIXED"}
    for bad in ("trend=SIDEWAYS", "color=RED", "trend"):
        with pytest.raises(X.ReportError):
            X.parse_situation(bad)
    with pytest.raises(X.ReportError):
        X.situation_from_snapshot({"schema_id": "situation_v2"})
    with pytest.raises(X.ReportError):
        X.parse_when("dün")
    with pytest.raises(X.ReportError):
        X.run(root, X.Query(), live=True)                  # canlı anlık görüntü yalnız --for-symbol ile
    with pytest.raises(X.ReportError):
        X.Query(book="box").validate()


def test_live_snapshot_is_built_offline_from_the_csv_cache(tmp_path):
    pd = pytest.importorskip("pandas")
    from tradingbot.pattern_trader.data import CsvCandleCache
    cache = CsvCandleCache(tmp_path / "market")
    now_ms = int(NOW.timestamp() * 1000)
    rnd = random.Random(3)
    for sym, tf, step in (("SOL/USDT", "4h", 4 * 3600_000), ("SOL/USDT", "1h", 3600_000), ("BTC/USDT", "4h", 4 * 3600_000)):
        last_open = S.expected_last_closed_open(now_ms, tf)
        px, bars = 100.0, []
        for k in range(240):
            o = px
            px = max(1.0, px * (1 + rnd.gauss(0.001, 0.01)))
            bars.append({"timestamp": last_open - (239 - k) * step, "open": o, "high": max(o, px) * 1.004,
                         "low": min(o, px) * 0.996, "close": px, "volume": 1000 + rnd.random() * 100})
        cache.write(sym, tf, pd.DataFrame(bars))
    root = tmp_path / "xp"
    write(root, cf_rows(BOX, "a", "SOL/USDT", "LONG", "box_fade", T0, 0.1))
    before = _tree(tmp_path / "market")
    doc = run(root, for_symbol="SOL/USDT", live=True, csv_dir=tmp_path / "market")
    assert doc["situation"]["mode"] == "live_csv" and doc["situation"]["snapshot_status"] in ("OK", "PARTIAL")
    assert set(doc["situation"]["dims"]) == set(X.DIMS)
    assert _tree(tmp_path / "market") == before, "CSV önbelleği SALT okunur"
    stale = run(root, for_symbol="SOL/USDT", live=True, csv_dir=tmp_path / "empty")
    assert stale["situation"]["mode"] == "symbol" and any("canlı" in n for n in stale["notes_tr"])


# ============================================================================ pencere + segment atlama + bozuk segment
def test_since_until_filter_on_decision_time_skips_sealed_segments_and_corrupt_segment_is_excluded(tmp_path):
    root = tmp_path / "xp"
    old = []
    for i in range(12):
        old += cf_rows(BOX, "old%d" % i, "SOL/USDT", "LONG", "box_fade", T0 + timedelta(hours=i), -1.0)
    write(root, old, rotate_every=6, hot_max_lines=4)
    segs = list((root / "archive" / "segments").glob("*.jsonl.gz"))
    assert len(segs) >= 2
    future = datetime(2031, 1, 1, tzinfo=UTC)
    st = ExperienceStore(root, hot_max_lines=100_000)
    new = []
    for i in range(5):
        new += cf_rows(BOX, "new%d" % i, "SOL/USDT", "LONG", "box_fade", future + timedelta(hours=i), 1.0)
    st.append_rows(new)
    full = X.scan(root)
    win = X.scan(root, since_ms=X.parse_when("2030-12-31"))
    assert win.read.segments_skipped == len(segs) and win.read.segments_read == 0
    assert int(((win.src == 1) & np.isfinite(win.r_net)).sum()) == 5
    assert int((full.src == 1).sum()) == 17
    # until: karar anı < until (hariç)
    up = X.scan(root, until_ms=int((T0 + timedelta(hours=6)).timestamp() * 1000))
    assert int((up.src == 1).sum()) == 6
    # bozuk segment (sha256 tutmaz) okunmaz, sayılır; kalan satırlar yine okunur
    victim = sorted(segs)[0]
    victim.write_bytes(gzip.compress(b'{"schema":"x"}\n'))
    bad = X.scan(root)
    assert bad.read.segments_bad == 1 and int((bad.src == 1).sum()) < 17


def test_foreign_invalid_and_partial_lines_are_counted_not_crashing(tmp_path):
    root = tmp_path / "xp"
    write(root, cf_rows(BOX, "a", "SOL/USDT", "LONG", "box_fade", T0, 0.3))
    good = cf_rows(BOX, "b", "ETH/USDT", "LONG", "box_fade", T0, 0.3)
    with (root / "experience.jsonl").open("ab") as fh:
        for r in ({"schema": "other", "kind": "decision"}, {**good[0], "app_mode": "LIVE"}, {**good[0], "book": "nope"},
                  {**good[0], "rev": -1}, {**good[0], "kind": "outcome_link"}):
            fh.write((json.dumps(r) + "\n").encode())
        fh.write(b'{"schema": "shared_experience_row_v1", "kind": "xp_cf", "re')     # yarım son satır
    doc = run(root)
    assert doc["source"]["rows_foreign"] == 2 and doc["source"]["rows_invalid"] == 3 and doc["source"]["lines_bad"] == 1
    assert doc["coverage"]["cf_net"] == 1


# ============================================================================ hücre haritası + özet
def test_cells_map_and_summary_rank_by_sample_size_not_performance(tmp_path):
    root = tmp_path / "xp"
    rows, i = [], 0
    for n, dims, r in ((50, {}, -1.0), (20, {"trend": "DOWN"}, 2.0), (4, {"trend": "RANGE"}, 5.0)):
        for _ in range(n):
            rows += cf_rows(BOX, "c%d" % i, "C%d/USDT" % (i % 7), "LONG", "box_fade", T0 + timedelta(days=i % 9, hours=i),
                            r + 0.01 * (i % 3), dims=dims)
            i += 1
    write(root, rows)
    doc = run(root, cells=True, group_by="setup")
    cells = doc["cells"]
    assert [c["dims"]["trend"] for c in cells] == ["UP", "DOWN"], "n ≥ 10 olan tam hücreler, n'e göre"
    assert cells[0]["cf"]["verdict"] == X.LOSS and cells[1]["answer"]["cf"]["level"] == 5
    assert cells[1]["answer"]["cf"]["n"] == 74 and cells[1]["cf"]["tier"] == X.TIER_DESCRIPTIVE
    sm = X.summary(X.scan(root), now=NOW)
    assert sm["schema"] == X.SUMMARY_SCHEMA and sm["banner"] == X.BANNER_TR
    assert [c["dims"]["trend"] for c in sm["top_cells"]] == ["UP", "DOWN", "RANGE"]
    assert sm["top_cells"][2]["cf"]["mean_r"] is None and sm["top_cells"][2]["cf"]["tier"] == X.TIER_INSUFFICIENT
    assert "en iyiler değil" in sm["params"]["rank_by"]


def test_json_is_stable_and_text_starts_with_the_fixed_banner(tmp_path):
    root = tmp_path / "xp"
    rows = []
    for i in range(14):
        rows += cf_rows(BOX, "c%d" % i, "C%d/USDT" % (i % 4), "LONG", "box_fade", T0 + timedelta(days=i % 5), 0.2 * (i % 3) - 0.1)
    rows += real_rows(BOX, "F1", "SOL/USDT", "LONG", "box_fade", T0, 1.0)
    write(root, rows)
    a = json.dumps(run(root, for_symbol="SOL", with_summary=True), sort_keys=True, ensure_ascii=False)
    b = json.dumps(run(root, for_symbol="SOL", with_summary=True), sort_keys=True, ensure_ascii=False)
    assert a == b
    doc = json.loads(a)
    assert doc["banner"] == X.BANNER_TR and doc["summary"]["banner"] == X.BANNER_TR
    txt = X.render_tr(doc)
    assert txt.splitlines()[0] == X.BANNER_LINE_TR
    assert "sayı gösterilmez" in txt, "n<10 sütunları sayı göstermez"
    missing = X.run(tmp_path / "yok", X.Query(), now=NOW)
    assert missing["available"] is False and X.render_tr(missing).splitlines()[0] == X.BANNER_LINE_TR
    assert not (tmp_path / "yok").exists()


# ============================================================================ durum belgesi
def test_status_doc_coverage_by_recorded_time_no_data_flags_and_rollback_triggers(tmp_path):
    root = tmp_path / "xp"
    env_old = R.make_env(recorded_at=NOW - timedelta(days=3), learning_since=SINCE)
    rows = cf_rows(BOX, "a", "SOL/USDT", "LONG", "box_fade", T0, 0.3)                              # son 24 sa
    rows += cf_rows("strategy_paper", "b", "SOL/USDT", "LONG", "trend", T0, 0.3, env=env_old)       # yalnız 7 gün
    rows += real_rows("main", "F1", "SOL/USDT", "LONG", "pullback", T0, 0.3, env=env_old)
    write(root, rows)
    (root / "status.json").write_text(json.dumps({"schema": "shared_experience_status_v1", "state": "DISABLED_BY_BREAKER",
                                                  "last_step_at": (NOW - timedelta(minutes=5)).isoformat(),
                                                  "step_ms_p95": 6000.0, "counters": {"rows_total": 7}}), encoding="utf-8")
    before = _tree(root)
    doc = X.status_doc(root, now=NOW)
    assert _tree(root) == before, "durum komutu SALT okur"
    assert doc["rollback"]["breaker"] and doc["rollback"]["p95_over_5000"] and doc["status_age_s"] == 300.0
    c24, c7 = doc["coverage"]["24h"], doc["coverage"]["7d"]
    assert c24[BOX] == {"rows": 2, "entries": 0, "outcomes": 0, "cf_new": 1, "cf_labelled": 1}
    assert c24["strategy_paper"]["rows"] == 0 and c7["strategy_paper"]["rows"] == 2
    assert c7["main"] == {"rows": 2, "entries": 1, "outcomes": 1, "cf_new": 0, "cf_labelled": 0}
    assert "strategy_paper" in doc["no_data_24h"] and BOX not in doc["no_data_24h"]
    txt = X.render_status_tr(doc)
    assert "GERİ ALMA TETİĞİ" in txt and "VERİ YOK" in txt and X.BANNER_TR in txt
    assert X.status_doc(tmp_path / "none", now=NOW)["available"] is False and not (tmp_path / "none").exists()


# ============================================================================ CLI: salt okur, motor yok, ağ yok
def test_cli_report_and_status_are_read_only_own_process_no_engine_no_network(tmp_path):
    root = tmp_path / "state" / "shared_experience"
    rows = []
    for i in range(12):
        rows += cf_rows(BOX, "c%d" % i, "C%d/USDT" % (i % 4), "LONG", "box_fade", T0 + timedelta(days=i % 4), 0.2)
    rows += real_rows(BOX, "F1", "SOL/USDT", "LONG", "box_fade", T0, 1.0)
    write(root, rows, rotate_every=10, hot_max_lines=8)
    (root / "status.json").write_text(json.dumps({"state": "OK", "counters": {}}), encoding="utf-8")
    before = _tree(tmp_path / "state")
    out, summ = tmp_path / "out" / "rapor.json", tmp_path / "out" / "ozet.json"
    out.parent.mkdir()
    script = textwrap.dedent("""
        import json, socket, sys
        def _no_net(*a, **k):
            raise RuntimeError("AĞ YASAK")
        socket.socket.connect = _no_net
        socket.create_connection = _no_net
        sys.path.insert(0, %(root)r)
        from tradingbot.cli import main
        codes = [main(["--config", %(cfg)r, "shared-experience-report", "--root", %(xp)r, "--for-symbol", "SOL/USDT",
                       "--json", "--out", %(out)r]),
                 main(["--config", %(cfg)r, "shared-experience-report", "--root", %(xp)r, "--cells",
                       "--summary-out", %(summ)r]),
                 main(["--config", %(cfg)r, "shared-experience-status", "--root", %(xp)r]),
                 main(["--config", %(cfg)r, "shared-experience-report", "--root", %(xp)r, "--min-n", "5"]),
                 main(["--config", %(cfg)r, "shared-experience-report", "--root", %(missing)r])]
        # yapılandırma doğrulaması (önceden de) strategy_paper/pattern_trader modüllerini yükler; MOTOR kurulmaz/yüklenmez,
        # ağ çağrısı yukarıdaki soket kapanıyla yasaktır (her bağlantı RuntimeError)
        heavy = sorted(m for m in sys.modules if m in ("tradingbot.engine_v3", "tradingbot.engine", "ccxt",
                                                        "tradingbot.box_timer"))
        print("RESULT" + json.dumps({"codes": codes, "heavy": heavy}))
    """) % {"root": str(ROOT), "cfg": str(ROOT / "config.yaml"), "xp": str(root), "out": str(out), "summ": str(summ),
            "missing": str(tmp_path / "state" / "yok")}
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRADINGBOT_")}
    p = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=300)
    assert p.returncode == 0, p.stderr[-3000:]
    res = json.loads(p.stdout.split("RESULT", 1)[1])
    assert res == {"codes": [0, 0, 0, 2, 0], "heavy": []}, res
    assert _tree(tmp_path / "state") == before, "CLI depoya/state'e HİÇBİR ŞEY yazmaz"
    assert not (tmp_path / "state" / "yok").exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["banner"] == X.BANNER_TR and doc["situation"]["coin"] == "SOL/USDT" and "summary" not in doc
    sm = json.loads(summ.read_text(encoding="utf-8"))
    assert sm["schema"] == X.SUMMARY_SCHEMA and sm["top_cells"]
    assert sorted(x.name for x in out.parent.iterdir()) == ["ozet.json", "rapor.json"]
    assert X.BANNER_LINE_TR in p.stdout and "ORTAK DENEYİM DURUMU" in p.stdout and "min_n en az 10" in p.stdout


# ============================================================================ akış belleği (T7)
def test_streaming_memory_peak_below_60mb_on_a_200k_row_store(tmp_path):
    from tradingbot.learn.journal_archive import SegmentArchive
    root = tmp_path / "xp"
    root.mkdir()
    t = T0
    src = _cf_src(BOX, "tmpl", "SOL/USDT", "LONG", "box_fade", t)
    snap = {k: 0.123456 for k in S.SNAPSHOT_FIELDS}
    snap.update(_snap("SOL/USDT", t))
    r0 = R.cf_row(src, book=BOX, rev=0, status="PENDING", env=ENV, snapshot=snap, snapshot_status="OK")
    r1 = R.cf_row({**src, "outcome": _cf_out(0.5)}, book=BOX, rev=1, status="LABELLED", env=ENV)
    arch = SegmentArchive(root / "archive", stream_id="shared_experience")
    trends, coins = ("UP", "DOWN", "RANGE"), ["C%02d/USDT" % k for k in range(40)]
    # gerçekçi boyutta satırlar (rev 0 tam anlık görüntülü ~2,5 KB, etiket revizyonu ~1 KB); hız için metin şablonu
    ph = {"cf_key": "@CK@", "cf_id": "@CI@", "row_id": "@RI@", "symbol": "@SY@", "as_of_ms": 1111111111111}
    ta = json.dumps({**r0, **ph, "snapshot": {**r0["snapshot"], "h4_trend": "@TR@"}}, separators=(",", ":"))
    tb = json.dumps({**r1, **ph, "r_net": 2222.5}, separators=(",", ":"))

    def fill(tpl, i, rid, extra):
        out = (tpl.replace("@CK@", "%s|cf_%07d" % (BOX, i)).replace("@CI@", "cf_%07d" % i).replace("@RI@", rid)
               .replace("@SY@", coins[i % 40])
               .replace("1111111111111", str(int((t + timedelta(minutes=5 * i)).timestamp() * 1000))))
        for k, v in extra:
            out = out.replace(k, v)
        return out

    n_keys, block, lines = 100_000, 5000, []
    for i in range(n_keys):
        lines.append(fill(ta, i, "%016x" % (2 * i), (("@TR@", trends[i % 3]),)))
        lines.append(fill(tb, i, "%016x" % (2 * i + 1), (("2222.5", repr(round(((i * 7919) % 200) / 100.0 - 0.9, 4))),)))
        if len(lines) >= block:
            arch.commit(arch.seal(lines))
            lines = []
    (root / "experience.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    assert len(list((root / "archive" / "segments").glob("*.gz"))) == 2 * n_keys // block
    tracemalloc.start()
    try:
        doc = X.run(root, X.Query(), now=NOW, with_summary=True)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert doc["source"]["rows_read"] == 2 * n_keys and doc["coverage"]["cf_net"] == n_keys
    assert peak < 60 * 1024 * 1024, "tepe %.1f MB" % (peak / 1048576)
    assert sum(g["levels"][0]["cf"]["n"] for g in doc["groups"] if g["group_by"] == "setup") == n_keys


# ============================================================================ panel kartı
def test_dashboard_card_reads_only_status_and_summary_is_empty_when_off_and_never_imports_the_package(tmp_path):
    from tradingbot.dashboard.state import StateReader
    from tradingbot.dashboard.templates import experience_layer_card
    state = tmp_path / "state"
    state.mkdir()
    rd = StateReader(state)
    assert rd.shared_experience() is None and experience_layer_card(None) == ""
    xp = state / "shared_experience"
    rows = []
    for i in range(12):
        rows += cf_rows(BOX, "c%d" % i, "C%d/USDT" % (i % 4), "LONG", "box_fade", T0 + timedelta(days=i % 4), -0.3)
    write(xp, rows)
    (xp / "status.json").write_text(json.dumps({
        "schema": "shared_experience_status_v1", "state": "OK", "last_step_at": NOW.isoformat(), "steps": 9,
        "step_ms_p95": 41.5, "drafts": 0, "breaker": {"tripped": False},
        "counters": {"rows_total": 24, "rows_by_kind": {"xp_cf": 24}, "snapshot_status_mix": {"OK": 12},
                     "errors_total": 0},
        "store": {"disk_bytes": 2 * 1048576, "hot_lines": 24}}), encoding="utf-8")
    d = rd.shared_experience()
    assert d["banner"] == X.BANNER_TR and d["rows_total"] == 24 and d["last_sweep_at"] is None and d["top_cells"] == []
    html = experience_layer_card(d)
    assert X.BANNER_TR in html and "rapor taraması henüz yok" in html and "%100 OK" in html
    sm = X.summary(X.scan(xp), now=NOW)
    (xp / X.SUMMARY_FILE).write_text(json.dumps(sm, ensure_ascii=False), encoding="utf-8")
    d = rd.shared_experience()
    assert d["last_sweep_at"] == sm["generated_at"] and d["top_cells"][0]["group"] == "strategy_paper_box|box_fade|LONG"
    html = experience_layer_card(d)
    assert "strategy_paper_box|box_fade|LONG" in html and "UP · NORMAL · UP · NORMAL · HH_HL" in html
    assert "en iyiler DEĞİL" in html and X.V_THIN in html
    # büyük / bozuk özet yok sayılır ve KOPYALANMAZ (read_json'un aksine)
    (xp / X.SUMMARY_FILE).write_text("{bozuk", encoding="utf-8")
    assert rd.shared_experience()["top_cells"] == [] and not list(xp.glob("*.corrupt*"))
    (xp / X.SUMMARY_FILE).write_text(json.dumps({**sm, "pad": "x" * (1 << 20)}), encoding="utf-8")
    assert rd.shared_experience()["last_sweep_at"] is None
    for name in ("state.py", "templates.py", "app.py"):
        tree = ast.parse((ROOT / "tradingbot" / "dashboard" / name).read_text(encoding="utf-8"))
        mods = [(n.module or "") + "." + a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names] + \
               [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in mods if "shared_experience" in m], name


def test_dashboard_overview_shows_card_only_when_the_layer_ran(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import create_app
    from tradingbot.dashboard.config import DashboardConfig
    state = tmp_path / "state"
    state.mkdir()
    c = TestClient(create_app(state, tmp_path / "market", None, DashboardConfig()))
    off = c.get("/").text
    assert "Ortak deneyim" not in off and c.get("/api/shared-experience").json() == {"available": False}
    (state / "shared_experience").mkdir()
    (state / "shared_experience" / "status.json").write_text(json.dumps({"state": "OK", "counters": {"rows_total": 3}}),
                                                            encoding="utf-8")
    on = c.get("/").text
    assert "Ortak deneyim — yalnız KAYIT" in on and X.BANNER_TR in on
    api = c.get("/api/shared-experience").json()
    assert api["available"] is True and api["rows_total"] == 3
    before = _tree(state)
    c.get("/")
    assert _tree(state) == before

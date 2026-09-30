# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — BELLEK / CPU / DEVRE KESİCİ BÜTÇESİ (2026-09-30; SPEC_ADVISOR_V1 §6.3, §6.8, §7 T-A7).

**Bellek.**
* Canlı katlama durumu, 1 yıllık sentetik hacimde (≈ 70 bin gerçek + 450 bin v3 olsaydı kanıtı, 620 bin çözülmüş hedef
  anahtarı, gerçekçi hücre/coin/gün dağılımı, 72 sa kuyruk ve bekleyen bağlamlar) ≤ 48 MB tutulur. Ölçüm: durum anlık
  görüntüye paketlenir ve tracemalloc altında geri yüklenir (yeniden başlatmadaki süreç belleği); canlı dizilerin büyüme
  payı için %10 eklenir. `memory_estimate()` (DEGRADED tavanının girdisi) ölçülenin ±%35'i içinde.
* Çevrimdışı CLI (walk-forward, bootstrap dahil) taze bir alt süreçte: 200 bin satırlık depoda tepe RSS artışı < 80 MB;
  100 bin → 200 bin doğrusal (400 bine doğrusal uzatma < 120 MB). RSS, tracemalloc'tan KATIDIR (parçalanma dahil).
* Panel okuyucusunun tavanları (büyük dosya → None) `test_dashboard_advisor_card_v1`de; burada yalnız sabitler.

**CPU.** 15 hedef / 20 kanıtlık toplu yazımlarda `on_flush` p95 < 50 ms; 600 satırlık toplu yazım (300 durumlu hedef +
300 kanıt) < 400 ms; yetişme adımı kendi bütçesine (`advisor_catch_up_budget_ms`, 2026-09-30) uyar (±%20; tavsiye deposu
döngü adımı hariç); anlık görüntü kalan bütçeye bağlıdır (yetmezse ertelenir, sayılır; art arda ertelemeden sonra ucuz
adımda zorla yazılır). Toplayıcının `step_ms`i danışmanın süresini İÇERMEZ.

**Devre kesici.** Art arda 5 istisna ya da art arda 3 adım > 3 × bütçe → danışman `DISABLED_BY_BREAKER`; toplayıcı
ETKİLENMEZ (satır yazmaya devam eder, sayaçları/kesicisi aynı). Toplayıcının aşım denetimi danışmanın süresini DIŞLAR.
"""
from __future__ import annotations

import gc
import json
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
from datetime import timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import advisor_synth as X  # noqa: E402
from advisor_synth import A, R, S  # noqa: E402

from tradingbot.shared_experience import advisor_live as AL  # noqa: E402
from tradingbot.shared_experience.collector import XpSettings  # noqa: E402
from tradingbot.shared_experience.store import ExperienceStore  # noqa: E402

MB = 1024 * 1024
TR3, VO3 = ("UP", "DOWN", "RANGE"), ("LOW", "NORMAL", "HIGH")
COINS = ["C%02d/USDT:USDT" % k for k in range(40)]


# ============================================================================ 1 yıllık sentetik katlama durumu
def _year_fold(n_real: int = 70_000, n_cf: int = 450_000, n_targets: int = 620_000, seed: int = 1) -> A.AdvisorFold:
    """Kompakt üretim: kanıt doğrudan hücrelere (üretimdeki `_ingest` ile); son 3 gün kuyrukta; 20 bin bekleyen
    olsaydı + 400 açık gerçek bağlam; 620 bin çözülmüş hedef anahtarı."""
    rng = random.Random(seed)
    f = A.AdvisorFold()
    setups: list[tuple[str, str | None]] = []
    for b, st, fam in (("main", "breakout", "BREAKOUT"), ("main", "pullback", "TREND"), ("main", "market", "TREND"),
                       ("strategy_paper", "trend", "TREND"), ("strategy_paper_m2", "trend", "MOMENTUM"),
                       ("strategy_paper_trend4h", "donchian", "BREAKOUT"), ("strategy_paper_box", "box_fade", "FADE")):
        for sd in ("LONG", "SHORT"):
            setups.append(("%s|%s|%s" % (b, st, sd), "%s|%s" % (fam, sd)))
    for v in range(20):
        for sd in ("LONG", "SHORT"):
            setups.append(("strategy_paper_candle4h|candle:CV%03d|%s" % (v, sd), "CANDLE_PATTERN|%s" % sd))
            setups.append(("strategy_paper_candle4h_strict|candle:CV%03d|%s" % (v, sd), None))
    for fam, ff in (("A_TREND_PULLBACK", "TREND"), ("B_LEVEL_REVERSAL", "FADE"), ("C_COMPRESSION_BREAKOUT", "BREAKOUT"),
                    ("M3_MOMENTUM_3WS_RSI70", "MOMENTUM"), ("E2_SWEEP_RECLAIM", "FADE")):
        for sd in ("LONG", "SHORT"):
            setups.append(("pattern_trader|%s|%s" % (fam, sd), "%s|%s" % (ff, sd)))
    box = [i for i, s in enumerate(setups) if s[0].startswith("strategy_paper_box")]
    main = [i for i, s in enumerate(setups) if s[0].startswith("main|")]
    strat = [i for i, s in enumerate(setups) if s[0].split("|")[0] in ("strategy_paper", "strategy_paper_m2",
                                                                       "strategy_paper_trend4h")]
    patt = [i for i, s in enumerate(setups) if s[0].startswith("pattern_trader")]
    c4 = [i for i, s in enumerate(setups) if "candle" in s[0]]
    ids = [(f._gk_id(s), f._gk_id(fm) if fm else -1) for s, fm in setups]
    coins = [f._coin_id("C%03d/USDT" % i) for i in range(80)]
    dv = [[f._dim_code(i, v) for v in vals] for i, vals in enumerate((TR3, VO3, TR3, VO3, ("HH_HL", "LH_LL", "MIXED",
                                                                                           "UNKNOWN")))]
    day0 = X.T0_MS // X.DAY - 365
    per_day = (n_real // 365, n_cf // 365)
    for d in range(365):
        btc, vol = rng.randrange(3), rng.randrange(3)
        coin_trend = [rng.randrange(3) for _ in coins]
        tail = d >= 362                                          # son 72 sa: kuyrukta (avail gün içinde)
        for ch in (0, 1):
            for k in range(per_day[ch]):
                u = rng.random()
                if ch == 1:
                    si = rng.choice(box) if u < 0.95 else rng.randrange(len(setups))
                else:
                    si = (rng.choice(box) if u < 0.4 else rng.choice(main) if u < 0.7 else rng.choice(strat) if u < 0.85
                          else rng.choice(patt) if u < 0.95 else rng.choice(c4))
                c = rng.randrange(len(coins) if si not in box else 40)
                dc = (dv[0][coin_trend[c]], dv[1][vol if rng.random() < 0.7 else rng.randrange(3)], dv[2][btc],
                      dv[3][1 if rng.random() < 0.6 else rng.randrange(3)], dv[4][rng.randrange(4)])
                s_id, f_id = ids[si]
                avail = (day0 + d) * X.DAY + rng.randrange(X.DAY)
                f._ingest(ch, avail, rng.getrandbits(64), f._evidence_cells(ch, s_id, f_id, dc), day0 + d, coins[c],
                          rng.choice((-1_000_000, 500_000, 1_200_000, -300_000)), 1, tail=tail)
    for j in range(n_targets):
        f._mark_done(j * 0x9E3779B97F4A7C15 % (1 << 64))
    f._merge_done()
    # bekleyen bağlamlar: 20 bin olsaydı (etiket bekliyor) + 400 açık gerçek işlem
    rec = X.iso_us(X.T0_MS, 5)
    rec_ms = A.ms_exact(rec)
    for i in range(20_400):
        if i < 20_000:
            row = X.cf("strategy_paper_box", "p%06d" % i, X.T0_MS - rng.randrange(3 * X.DAY), rec=rec, setup="box_fade",
                       symbol=COINS[i % 40])
            kind = R.KIND_CF
        else:
            row = X.entry("main", "o%05d" % i, X.T0_MS - rng.randrange(3 * X.DAY), rec=rec, setup="pullback",
                          symbol=COINS[i % 40])
            kind = R.KIND_ENTRY
        fl, _dims = f._make_ctx(row, kind, rec_ms)
        f._ctx[A.key_hash(kind, row["cf_key"] if kind == R.KIND_CF else row["trade_key"])] = A._CTX.pack(*fl)
    f.batch_clock = X.T0_MS
    return f


@pytest.fixture(scope="module")
def year_fold() -> A.AdvisorFold:
    return _year_fold()


def test_live_fold_retained_memory_at_one_year_is_within_48mb(year_fold):
    f = year_fold
    st = f.stats()
    assert st["evidence_total"][0] >= 69_000 and st["evidence_total"][1] >= 449_000
    assert st["done_keys"] == 620_000 and st["pending_contexts"] == 20_400 and sum(st["tail"]) > 3_000
    body = f.state_bytes()
    gc.collect()
    tracemalloc.start()
    try:
        f2 = A.AdvisorFold.from_state(body)
        del body
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert f2.stats()["cells"] == st["cells"] and f2.stats()["tail"] == st["tail"]
    assert retained * 1.10 <= 48 * MB, "tutulan %.1f MB (+%%10 büyüme payı)" % (retained / MB)
    est = f.memory_estimate()
    assert 0.65 * retained <= est <= 1.35 * retained, (est / MB, retained / MB)
    assert est / MB < 64, "alarm eşiği (64 MB) 1 yılda aşılmamalı"


def test_snapshot_of_one_year_state_is_bounded_and_round_trips(year_fold, tmp_path):
    f = year_fold
    adv = AL.LiveAdvisor(tmp_path / "xp", settings=XpSettings(advisor_mode="RECORD"))
    adv.fold, adv.mode = f, AL.MODE_LIVE
    t = time.perf_counter()
    adv._write_snapshot()
    ms = (time.perf_counter() - t) * 1000.0
    p = tmp_path / "xp" / "advice" / AL.STATE_FILE
    assert p.stat().st_size < 16 * MB, p.stat().st_size
    assert ms < 1500, "anlık görüntü %.0f ms" % ms                     # tasarım ≈ 0.5 s (yavaş CI payı)
    hdr, g = AL.LiveAdvisor(tmp_path / "xp", settings=XpSettings(advisor_mode="RECORD"))._read_snapshot()
    assert hdr["advisor_sha"] == A.ADVISOR_SHA and g.state_bytes() == f.state_bytes()


# ============================================================================ çevrimdışı tepe (taze alt süreç)
def _gen_store(root: Path, n_rows: int, block: int = 5000) -> int:
    """Box olsaydı rev 0 + 3 tur sonra etiket (tur başına ~20 satır) + tur başına bir gerçek işlem; arşiv segmentleri
    + sıcak dosya. Dönüş: satır sayısı."""
    from tradingbot.learn.journal_archive import SegmentArchive
    arch = SegmentArchive(root / "archive", stream_id="shared_experience")
    lines: list[str] = []
    pend: list[tuple] = []
    b = i = n = 0
    while n < n_rows:
        tk = X.T0_MS + b * X.STEP
        rec = X.iso_us(tk, 7)
        batch: list[dict] = []
        for j in range(9):
            as_of = tk - 5 * X.MIN + j * 1000
            sn = X.snap(TR3[i % 3], VO3[(i // 3) % 3], TR3[(i // 7) % 2], "NORMAL", ("HH_HL", "LH_LL", None)[i % 3])
            batch.append(X.cf("strategy_paper_box", "c%07d" % i, as_of, rec=rec, setup="box_fade",
                              side="LONG" if i % 5 else "SHORT", symbol=COINS[i % 40], sn=sn))
            pend.append((i, as_of, b))
            i += 1
        while pend and pend[0][2] <= b - 3:
            k, as_of, _b = pend.pop(0)
            batch.append(X.cf("strategy_paper_box", "c%07d" % k, as_of, rec=rec, rev=1, status=R.LABELLED,
                              r=(-1.0, 0.5, 1.2, -0.3, -1.0)[k % 5], labeled_ms=tk - X.MIN, setup="box_fade",
                              side="LONG" if k % 5 else "SHORT", symbol=COINS[k % 40]))
        batch += X.ev_real("main", "T%06d" % b, tk - 4 * X.MIN, (-1.0, 0.8, -0.4)[b % 3], rec=rec, setup="pullback",
                           symbol=COINS[b % 40], close_after=2 * X.MIN)
        lines += [json.dumps(r, separators=(",", ":")) for r in batch]
        n += len(batch)
        b += 1
        if len(lines) >= block:
            arch.commit(arch.seal(lines))
            lines = []
    (root / "experience.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return n


#: Tepe = bu sürecin bellek haritasının yüksek su işareti (`VmHWM`, exec'te sıfırlanır). `ru_maxrss` KULLANILMAZ: Linux
#: onu exec boyunca taşır (çatallanan pytest sürecinin RSS'i sayılırdı).
_MEASURE = r"""
import gc, json, sys
sys.path.insert(0, %r)
from tradingbot.shared_experience import advisor_eval as AE
import numpy  # noqa: F401 — taban çizgisine dahil


def vm(key):
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith(key + ":"):
                return int(ln.split()[1]) * 1024
    raise KeyError(key)


gc.collect()
r0 = vm("VmRSS")
doc = AE.run_walkforward(sys.argv[1], eligibility="all", boot=True)
print(json.dumps({"delta": vm("VmHWM") - r0, "resolved": doc["resolved_targets"], "rows": doc["source"]["rows_read"]}))
"""


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="Linux /proc gerekir")
def test_offline_walkforward_peak_under_80mb_at_200k_rows_and_linear(tmp_path):
    roots = {}
    for n in (100_000, 200_000):
        root = tmp_path / ("s%d" % n)
        root.mkdir()
        assert _gen_store(root, n) >= n
        roots[n] = root
    procs = {n: subprocess.Popen([sys.executable, "-c", _MEASURE % str(ROOT), str(r)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for n, r in roots.items()}
    out = {}
    for n, p in procs.items():
        so, se = p.communicate(timeout=900)
        assert p.returncode == 0, se[-3000:]
        out[n] = json.loads(so.strip().splitlines()[-1])
    d1, d2 = out[100_000]["delta"], out[200_000]["delta"]
    assert out[200_000]["rows"] >= 200_000 and out[200_000]["resolved"] > 90_000
    assert d2 < 80 * MB, "200 bin satır: tepe artışı %.1f MB" % (d2 / MB)
    assert d2 <= 2 * d1 + 4 * MB, "doğrusal değil: %.1f → %.1f MB" % (d1 / MB, d2 / MB)
    assert d2 + 2 * max(0, d2 - d1) < 120 * MB, "400 bine doğrusal uzatma"


# ============================================================================ CPU
def _snap_rnd(rng: random.Random) -> dict:
    return X.snap(rng.choice(TR3), rng.choice(VO3), rng.choice(TR3[:2]), rng.choice(VO3),
                  rng.choice(("HH_HL", "LH_LL", None)))


def _prepopulated(days: int = 60, per_day: int = 1400, seed: int = 3) -> A.AdvisorFold:
    """60 günlük Box + ana defter kanıtı (hedeflerin hücreleriyle AYNI kurulum/boyut uzayı)."""
    rng = random.Random(seed)
    f = A.AdvisorFold()
    day0 = X.T0_MS // X.DAY - days
    box = (f._gk_id("strategy_paper_box|box_fade|LONG"), f._gk_id("FADE|LONG"))
    main = (f._gk_id("main|pullback|LONG"), f._gk_id("TREND|LONG"))
    for d in range(days):
        for k in range(per_day):
            ch = 0 if k % 8 == 0 else 1
            s, fm = main if (ch == 0 and k % 16 == 0) else box
            b = S.bucket(_snap_rnd(rng), "LONG")
            dc = tuple(f._dim_code(i, b[dn]) for i, dn in enumerate(A.DIMS))
            f._ingest(ch, 0, 0, f._evidence_cells(ch, s, fm, dc), day0 + d, f._coin_id(rng.choice(COINS)),
                      rng.choice((-1_000_000, 500_000, 1_200_000, -300_000)), 1, tail=False)
    return f


class _Feed:
    """Canlı adım akışı: tur başına n_t hedef (3'ü gerçek giriş) + n_v kanıt (önceki turların etiket/kapanışları)."""

    def __init__(self, seed: int = 5) -> None:
        self.rng = random.Random(seed)
        self.pend: list[tuple] = []

    def batch(self, k: int, n_t: int = 15, n_v: int = 20) -> list[dict]:
        rng = self.rng
        tk = X.T0_MS + k * X.STEP
        rec = X.iso_us(tk, 11)
        out: list[dict] = []
        for j in range(n_t):
            as_of = tk - rng.randrange(X.MIN, 9 * X.MIN)
            if j < 3:
                key = "E%05d_%d" % (k, j)
                out.append(X.entry("main", key, as_of, rec=rec, setup="pullback", symbol=rng.choice(COINS),
                                   sn=_snap_rnd(rng)))
                self.pend.append(("R", key, as_of))
            else:
                key = "c%05d_%d" % (k, j)
                out.append(X.cf("strategy_paper_box", key, as_of, rec=rec, setup="box_fade", symbol=rng.choice(COINS),
                                sn=_snap_rnd(rng)))
                self.pend.append(("C", key, as_of))
        for _ in range(min(n_v, max(0, len(self.pend) - 40))):
            kind, key, as_of = self.pend.pop(0)
            r = rng.choice((-1.0, 0.5, 1.2, -0.3))
            if kind == "R":
                out.append(X.outcome("main", key, as_of, tk - 30_000, r, rec=rec, setup="pullback"))
            else:
                out.append(X.cf("strategy_paper_box", key, as_of, rec=rec, rev=1, status=R.LABELLED, r=r,
                                labeled_ms=tk - 20_000, setup="box_fade"))
        return out


def _live_advisor(root: Path, fold: A.AdvisorFold | None = None, **kw) -> tuple[AL.LiveAdvisor, ExperienceStore]:
    main = ExperienceStore(root, max_total_mb=None, hot_max_lines=kw.pop("hot_max_lines", 5000))
    adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD", **kw), main_store=main)
    if fold is not None:
        adv.fold, adv.mode = fold, AL.MODE_LIVE
        adv._set_born(X.T0_MS)
    return adv, main


def test_on_flush_p95_below_50ms_for_15_target_20_evidence_steps(tmp_path):
    adv, main = _live_advisor(tmp_path / "xp", _prepopulated())
    feed = _Feed()
    ms: list[float] = []
    for k in range(70):
        rows = feed.batch(k)
        w, _rej = main.append_rows(rows)
        assert w == len(rows)
        res = adv.on_flush(rows, budget_ms=250)
        main.rotate()
        if k >= 5:
            ms.append(res["ms"])
    assert adv.state == "OK" and adv.mode == "live" and adv.c["live_batches"] == 70 and adv.c["gaps"] == 0
    p95 = sorted(ms)[int(0.95 * (len(ms) - 1))]
    assert p95 < 50, "p95 %.1f ms (medyan %.1f)" % (p95, statistics.median(ms))
    assert adv.store.counters["written"] == 70 * 15


def test_600_row_batch_folds_within_400ms():
    f = _prepopulated()
    feed = _Feed(seed=9)
    for k in range(6):
        f.fold_batch(feed.batch(k, n_t=60, n_v=0))                    # bekleyen bağlam havuzu
    best = None
    for k in range(6, 9):
        rows = feed.batch(k, n_t=300, n_v=300)
        assert len(rows) == 600
        t = time.perf_counter()
        res = f.fold_batch(rows)
        dt = (time.perf_counter() - t) * 1000.0
        assert len(res.advice_rows) == 300 and all(r["state"] == "OK" for r in res.advice_rows)
        best = dt if best is None else min(best, dt)
    assert best < 400, "600 satır %.0f ms" % best


def test_catch_up_respects_the_advisor_budget(tmp_path):
    root = tmp_path / "xp"
    main = ExperienceStore(root, max_total_mb=None, hot_max_lines=50_000)
    feed = _Feed(seed=4)
    for k in range(400):
        main.append_rows(feed.batch(k))
    budget = 100
    adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD", advisor_budget_ms=budget,
                                                   advisor_catch_up_budget_ms=budget,
                                                   advisor_rebuild_rows_per_step=50_000), main_store=main)
    steps: list[float] = []
    rot0 = adv.store.rotations
    # önceki testlerin (1 yıllık katlama fikstürü) bıraktığı büyük yığın, bu testin ölçmediği GC taramalarına yol açar:
    # mevcut nesneler dondurulur (yalnız danışmanın kendi nesneleri taranır)
    gc.collect()
    gc.freeze()
    try:
        while adv.mode != AL.MODE_LIVE and len(steps) < 200:
            rot = adv.store.rotations
            res = adv.on_flush([], budget_ms=budget)
            if adv.mode != AL.MODE_LIVE and adv.store.rotations == rot:
                steps.append(res["ms"])                              # tavsiye deposu döngüsü olan adım hariç
    finally:
        gc.unfreeze()
    assert adv.mode == AL.MODE_LIVE and adv.c["catch_up_rows"] == main._line_count
    body = steps[1:]                                                 # ilk adım: kurulum (anlık görüntü arama vb.)
    assert len(body) >= 5 and adv.store.rotations >= rot0
    med = statistics.median(body)
    assert 0.8 * budget <= med <= 1.2 * budget, [round(x) for x in steps]
    # tek bir aykırı adım (GC duraklaması / soğuk sayfa önbelleği) bütçe kuralının ihlali değildir; ama hiçbir adım
    # aşım eşiğine (3 × bütçe) varmaz ve 1.5 × bütçeyi aşan adım en çok bir tanedir
    assert max(body) < AL.OVERRUN_FACTOR * budget, [round(x) for x in steps]
    assert sum(1 for x in body if x > 1.5 * budget) <= 1, [round(x) for x in steps]
    assert adv.state == "OK" and adv._consec_overruns == 0


def test_rebuild_rows_per_step_caps_the_catch_up_chunk(tmp_path):
    root = tmp_path / "xp"
    main = ExperienceStore(root, max_total_mb=None, hot_max_lines=50_000)
    feed = _Feed(seed=6)
    for k in range(60):
        main.append_rows(feed.batch(k))
    adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD", advisor_budget_ms=2000,
                                                   advisor_rebuild_rows_per_step=500), main_store=main)
    adv.on_flush([], budget_ms=2000)
    assert 500 <= adv.c["catch_up_rows"] < 500 + 35 and adv.mode == AL.MODE_CATCH_UP   # bir toplu yazım ≤ 35 satır


def test_snapshot_is_gated_by_the_remaining_budget(tmp_path):
    adv, main = _live_advisor(tmp_path / "xp", _prepopulated(days=5), advisor_snapshot_every_steps=5)
    feed = _Feed(seed=2)

    def run(n: int) -> None:
        for _ in range(n):
            rows = feed.batch(adv.steps)
            main.append_rows(rows)
            adv.on_flush(rows, budget_ms=250)
    run(3)
    adv._snap_ms_est = 1.0                                           # sığar → ilk uygun adımda (5.) yazılır
    run(2)
    assert adv.c["snapshots"] == 1 and (tmp_path / "xp" / "advice" / AL.STATE_FILE).exists()
    run(4)
    assert adv.c["snapshots"] == 1 and adv.c["snapshot_deferred"] == 0, "sıklık: en çok 5 adımda bir"
    adv._snap_ms_est = 1e9                                           # "anlık görüntü bütçeye sığmaz"
    run(1 + AL.SNAP_FORCE_AFTER - 1)
    assert adv.c["snapshots"] == 1 and adv.c["snapshot_deferred"] == AL.SNAP_FORCE_AFTER
    assert adv.c["snapshot_forced"] == 0


def test_deferred_snapshots_do_not_latch_forever(tmp_path):
    """İnceleme bulgusu (2026-09-30): tahmin yalnız yazımdan sonra güncellendiği için ertelemeler başlayınca süreç boyunca
    anlık görüntü yazılmıyordu (canlı adımlar birkaç ms iken). Art arda `SNAP_FORCE_AFTER` erteleme sonrası ucuz adımda
    ZORLA yazılır, bayat tahmin gerçek ölçümle değişir ve sonraki anlık görüntüler normal kapıdan geçer."""
    adv, main = _live_advisor(tmp_path / "xp", _prepopulated(days=5), advisor_snapshot_every_steps=5)
    feed = _Feed(seed=2)

    def run(n: int) -> None:
        for _ in range(n):
            rows = feed.batch(adv.steps)
            main.append_rows(rows)
            adv.on_flush(rows, budget_ms=250)
    adv._snap_ms_est = 1e9
    run(4 + AL.SNAP_FORCE_AFTER)                                      # 5. adımdan itibaren SNAP_FORCE_AFTER erteleme
    assert adv.c["snapshots"] == 0 and adv.c["snapshot_deferred"] == AL.SNAP_FORCE_AFTER
    st = json.loads((tmp_path / "xp" / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))
    assert st["snapshot"]["deferred_consecutive"] == AL.SNAP_FORCE_AFTER
    assert "SNAPSHOT_DEFERRED_%d_STEPS" % AL.SNAP_FORCE_AFTER in st["warnings"]
    run(1)
    assert adv.c["snapshots"] == 1 and adv.c["snapshot_forced"] == 1 and adv._snap_ms_est < 1e4
    run(4)
    assert adv.c["snapshots"] == 1
    run(1)
    assert adv.c["snapshots"] == 2 and adv.c["snapshot_forced"] == 1 and adv.c["snapshot_deferred"] == AL.SNAP_FORCE_AFTER
    st = json.loads((tmp_path / "xp" / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))
    assert st["snapshot"]["deferred_consecutive"] == 0 and not st["warnings"]


# ============================================================================ devre kesici (toplayıcıdan AYRI)
def _collector_world(tmp_path: Path, **xp):
    import test_shared_experience_collector_v1 as T
    books = [T.FakeBook(tmp_path, "strategy_paper_box", decision_tf="5m")]
    eng = T.fake_engine(tmp_path, books=books, advisor_mode="RECORD", **xp)
    xpc = T.collector(eng)

    def tour(k: int):
        at = T.T0 + timedelta(minutes=10 * k + 1)
        T.record_cf(books[0], T.SYMS[k % 3], sig=T.T0_MS + k * 1000, created=at)
        T.set_tour(eng, T.T0_MS + (10 * k + 1) * X.MIN + 30_000)
        return T.step(xpc, eng, T.T0 + timedelta(minutes=10 * k + 2))
    return eng, xpc, tour


def test_five_consecutive_advisor_exceptions_trip_only_the_advisor(tmp_path, monkeypatch):
    eng, xp, tour = _collector_world(tmp_path)

    def boom(self, *a, **k):
        raise RuntimeError("fold boom")
    monkeypatch.setattr(A.AdvisorFold, "fold_batch", boom)
    for k in range(7):
        res = tour(k)
        assert res["state"] == "OK" and res["rows"] >= 1, res
    adv = xp._advisor
    assert adv.state == AL.A_BREAKER and adv.errors == 5, (adv.state, adv.errors)
    h = xp.health()
    assert h["state"] == "OK" and h["errors"] == 0 and h["advisor"]["breaker"] is True
    assert h["advisor"]["state"] == "DISABLED_BY_BREAKER"
    assert xp.c["errors_total"] == 0 and xp.c["budget_overruns"] == 0 and xp.state == "OK"


def test_three_consecutive_advisor_overruns_trip_the_advisor_not_the_collector(tmp_path, monkeypatch):
    eng, xp, tour = _collector_world(tmp_path, tour_budget_s=0.1, advisor_budget_ms=20)
    orig = AL.LiveAdvisor._flush_advice

    def slow(self):
        time.sleep(0.2)                                              # > 3 × 20 ms ve > 1.5 × 100 ms
        return orig(self)
    monkeypatch.setattr(AL.LiveAdvisor, "_flush_advice", slow)
    for k in range(3):
        res = tour(k)
        assert res["state"] == "OK", res
        # (2026-09-30) toplayıcının `step_ms`i danışmanın süresini İÇERMEZ; danışmanınki ayrı (`advisor_ms`)
        assert res["advisor_ms"] > 150 and res["step_ms"] < 150, res
    for k in range(3, 5):
        assert tour(k)["state"] == "OK"
    adv = xp._advisor
    assert adv.state == AL.A_BREAKER and adv._consec_overruns >= 3
    assert xp.c["budget_overruns"] == 0 and xp._consec_overruns == 0 and xp.state == "OK", \
        "toplayıcının aşım denetimi danışmanın süresini dışlar"
    # kesici açıkken danışman bedava: toplayıcı adımları hızlı kalır
    t = time.perf_counter()
    tour(5)
    assert (time.perf_counter() - t) < 0.2 and xp._advisor_ms < 5


def test_collector_step_metrics_exclude_the_advisor_time(tmp_path, monkeypatch):
    """İnceleme bulgusu (2026-09-30): toplayıcının `step_ms` / `step_ms_p50` / `step_ms_p95` (sağlık ve status.json)
    danışmanın süresini İÇERİYORDU; katmanın dağıtım denetimi (p95 ≥ 1000 ms uyarı, > 1500 ms geri alma) yetişme ve
    tavsiye döngüsü adımlarıyla tetiklenebiliyordu. Şimdi yalnız toplayıcının kendi süresi; danışmanınki
    `health.advisor.step_ms` / `advisor_status.json`da."""
    eng, xp, tour = _collector_world(tmp_path, advisor_budget_ms=2000, advisor_catch_up_budget_ms=2000)
    orig = AL.LiveAdvisor._flush_advice

    def slow(self):
        time.sleep(0.12)
        return orig(self)
    monkeypatch.setattr(AL.LiveAdvisor, "_flush_advice", slow)
    for k in range(6):
        res = tour(k)
        assert res["advisor_ms"] >= 120 and res["step_ms"] < 100, res
    st = json.loads((xp.root / "status.json").read_text(encoding="utf-8"))
    h = xp.health()
    assert st["step_ms_p95"] < 100 and st["step_ms_p50"] < 100 and h["step_ms"] < 100, (st["step_ms_p95"], h)
    assert h["advisor"]["step_ms"] >= 120 and h["advisor"]["step_ms_p95"] >= 120
    adv_st = json.loads((xp.root / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))
    assert adv_st["step_ms_p95"] >= 120


def test_collector_overrun_still_counts_its_own_time(tmp_path, monkeypatch):
    """Dışlama yalnız danışmanın payıdır: toplayıcı kendisi yavaşsa aşım sayılır (denetim kör değil)."""
    from tradingbot.shared_experience import collector as C
    eng, xp, tour = _collector_world(tmp_path, tour_budget_s=0.1)
    orig = C.SharedExperienceCollector._prune

    def slow_prune(self, now_ms):
        time.sleep(0.2)
        return orig(self, now_ms)
    monkeypatch.setattr(C.SharedExperienceCollector, "_prune", slow_prune)
    tour(0)
    assert xp.c["budget_overruns"] == 1 and xp._advisor.state == "OK"

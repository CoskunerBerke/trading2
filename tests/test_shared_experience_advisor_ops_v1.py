# -*- coding: utf-8 -*-
"""GÖLGE DANIŞMAN — İŞLETİM İNCELEMESİ DÜZELTMELERİ (2026-09-30; inceleme "ops-parity" bulguları).

* **Okunamayan ana depo segmenti** (saatlik yedekten geri yükleme: segment yok; bozuk / sha256 tutmayan segment):
  okuyucu ATLAMAZ, bekler. Görünür durum `WAITING_SEGMENTS` (sağlık, durum dosyası); konum ve doğum ilerlemez; yalnız sıcak
  dosyadan "yeniden kurulum" YAPILMAZ. Segmentler gelince kaldığı yerden devam eder, sonuç canlıyla birebir.
  `restore_backup` listelenen eksik segmentleri önceki state'ten sha256 ile geri kopyalar.
* **Yüksek su işareti.** Anlık görüntü kaybından sonraki yeniden kurulum zaten yazılmış tavsiyeyi YENİDEN YAZMAZ (tavsiye
  arşivinde kopya yok; disk ikiye katlanmaz).
* **Kayıt engeli.** Tavsiye deposu disk tavanında (DEGRADED) → `RECORD_BLOCKED` (sağlık, durum dosyası, panel rozeti);
  yayımlanan / yazılan ayrı sayılır; tavan kalkınca yazım sürer.
* **Kurulum yolu** tavsiye deposunu TARAMAZ (meta yokken doğum, deponun SON satırından sınırlı okumayla).
* **Yetişme bütçesi** (`advisor_catch_up_budget_ms`) ve tavsiye deposu döngüsü payı; canlıya geçişte okuyucu bırakılır.
* **Bellek**: anlık görüntüden yeniden başlatmanın RSS artışı taze alt süreçte ölçülür (tahminin katı ile sınırlı).
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import advisor_synth as X  # noqa: E402
from test_shared_experience_advisor_fold_v1 import run_world  # noqa: E402

from tradingbot.shared_experience import advisor_eval as AE  # noqa: E402
from tradingbot.shared_experience import advisor_live as AL  # noqa: E402
from tradingbot.shared_experience import report as XR  # noqa: E402

MB = 1024 * 1024


def _status(root: Path) -> dict:
    return json.loads((root / "advice" / AL.STATUS_FILE).read_text(encoding="utf-8"))


def _advice_ids(root: Path) -> list[str]:
    return [r["row_id"] for r in AE.iter_advice(root)]


# ============================================================================ okunamayan segment
@pytest.mark.parametrize("damage", ["missing_all", "corrupt_first"])
def test_unreadable_main_segment_blocks_the_rebuild_visibly_and_resumes_when_restored(tmp_path, damage):
    w = run_world(tmp_path / "w", 24, hot_max_lines=60, advisor_snapshot_every_steps=5)
    a0 = w.xp._advisor
    born = a0.born_ms
    seg_dir = w.root / "archive" / "segments"
    segs = [s for s in XR._segments(XR.manifest(w.root))]
    assert len(segs) >= 3 and born is not None
    keep = tmp_path / "keep"
    shutil.copytree(seg_dir, keep)
    n_adv0 = len(_advice_ids(w.root))
    # saatlik yedekten geri yükleme benzetimi: segmentler (ya da biri bozuk) + anlık görüntü yok, meta VAR
    (w.root / "advice" / AL.STATE_FILE).unlink()
    if damage == "missing_all":
        for p in seg_dir.iterdir():
            p.unlink()
    else:
        first = seg_dir / min(segs, key=lambda s: s["seq"])["file"]
        first.write_bytes(first.read_bytes()[:-7] + b"garbage")
    xp2 = w.collector()
    for _ in range(3):
        res = w.tour(xp2)
        assert res["state"] == "OK" and res["rows"] >= 1, "toplayıcı ETKİLENMEZ"
    a = xp2._advisor
    assert a.mode == AL.MODE_CATCH_UP and a.state == AL.A_OK and a.public_state() == AL.S_WAITING_SEGMENTS
    assert a.c["catch_up_rows"] == 0 and a.c["waiting_segment_steps"] == 3, "sıcak dosyaya ATLAMA yok"
    assert a.born_ms == born, "doğum meta'dan; ilerlemez"
    h = xp2.health()
    assert h["state"] == "OK" and h["advisor"]["state"] == AL.S_WAITING_SEGMENTS
    assert h["advisor"]["segments_bad"] >= 3 and h["advisor"]["waiting_segment"]["seq"] == 1
    st = _status(w.root)
    assert st["state"] == AL.S_WAITING_SEGMENTS and st["internal_state"] == "OK" and st["mode"] == "catch_up"
    assert st["segments"]["waiting"]["reason"] == ("MISSING" if damage == "missing_all" else "SHA256_MISMATCH")
    assert st["segments"]["segments_bad"] >= 3 and "WAITING_SEGMENT_SEQ_1" in st["warnings"]
    assert len(_advice_ids(w.root)) == n_adv0, "yarım geçmişten tavsiye YAZILMAZ"
    # segmentler geri geldi (en yeni UTC-00 yedeğinden elle kopya) → kaldığı yerden
    for p in keep.iterdir():
        shutil.copy2(p, seg_dir / p.name)
    for _ in range(4):
        w.tour(xp2)
    assert a.mode == AL.MODE_LIVE and a.public_state() == "OK" and a.waiting_segment is None
    assert a.born_ms == born and _status(w.root)["segments"]["waiting"] is None
    v = AE.verify(w.root)
    assert v["agreement"] == 1.0 and v["missing_in_store"] == 0 and v["duplicates_ignored"] == 0


def test_store_reader_never_skips_a_listed_segment(tmp_path):
    """Okuyucu düzeyi: ortadaki segment eksik → o segmentin başında DURUR, yarım toplu yazımı vermez; geri gelince tam
    olarak kaldığı yerden devam eder (satırlar ve toplu yazımlar eksiksiz, sırası aynı)."""
    root = tmp_path / "xp"
    from test_shared_experience_advisor_budget_v1 import _gen_store
    _gen_store(root, 6_000, block=1_000)
    ref = [b for b in AE.iter_batches(root)]
    segs = sorted(XR._segments(XR.manifest(root)), key=lambda s: s["seq"])
    victim = root / "archive" / "segments" / segs[2]["file"]
    saved = victim.read_bytes()
    victim.unlink()
    rd = AL.StoreReader(root)
    got = [b for b in rd.batches()]
    assert rd.blocked == {"seq": segs[2]["seq"], "file": segs[2]["file"], "reason": "MISSING"} and not rd.at_end
    n_before = sum(len(b) for b in got)
    assert 0 < n_before < 2_100 and got == ref[:len(got)]
    assert list(rd.batches()) == [] and rd.blocked is not None and rd.segments_bad == 2      # yeniden dener, ilerlemez
    victim.write_bytes(saved)
    got += [b for b in rd.batches()]
    assert rd.blocked is None and rd.at_end and got == ref


# ============================================================================ geri yükleme: segment geri kopyası
def test_restore_copies_listed_segments_back_from_the_previous_state(tmp_path, monkeypatch):
    from tradingbot.ops import backup as B
    from tradingbot.ops.backup import restore_backup, run_backup
    stamps = iter("20261001T05%04dZ" % i for i in range(100))
    monkeypatch.setattr(B, "_ts", lambda: next(stamps))                   # aynı saniyede iki geri yükleme
    state = tmp_path / "state"
    xp = state / "shared_experience"
    from test_shared_experience_advisor_budget_v1 import _gen_store
    _gen_store(xp, 4_000, block=1_000)
    (state / "x.json").write_text("{}", encoding="utf-8")
    segs = sorted((xp / "archive" / "segments").iterdir())
    assert len(segs) >= 3
    res = run_backup(state, tmp_path / "bk", "hourly", now=datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc))
    assert res.skipped_xp_segments == len(segs)                          # saatlik@05: segment YOK
    out = restore_backup(res.archive, state)
    assert out["xp_segments"] == {"needed": len(segs), "present": 0, "copied": len(segs), "missing": []}
    assert sorted(p.name for p in (xp / "archive" / "segments").iterdir()) == [p.name for p in segs]
    assert sum(len(b) for b in AE.iter_batches(xp)) >= 4_000
    # önceki state'te de yoksa (ör. elle silinmiş): eksik RAPORLANIR (sessiz değil)
    for p in (xp / "archive" / "segments").iterdir():
        p.unlink()
    out2 = restore_backup(res.archive, state)
    assert out2["xp_segments"]["copied"] == 0 and len(out2["xp_segments"]["missing"]) == len(segs)


# ============================================================================ yüksek su işareti
def test_rebuild_after_a_lost_snapshot_rewrites_no_advice(tmp_path):
    """İnceleme bulgusu: tavsiye deposu yalnız sıcak dosyanın kimliklerine karşı tekilleştiriyordu; anlık görüntü
    kaybında bütün ARŞİVLENMİŞ tavsiye yeniden yazılıyordu (disk ikiye katlanır, her yeniden kurulumda büyür)."""
    def drop(w, xp):
        (w.root / "advice" / AL.STATE_FILE).unlink()
    w = run_world(tmp_path / "w", 30, restart_at=(20,), before_restart=drop, advice_hot_max_lines=12,
                  advisor_snapshot_every_steps=5)
    a = w.xp._advisor
    assert a.c["rebuilds"] == 1 and a.mode == AL.MODE_LIVE and a.c["advice_skipped_hwm"] > 20
    man = XR.manifest(w.root / "advice")
    assert len(XR._segments(man)) >= 3, "tavsiye arşivi sınandı"
    ids = _advice_ids(w.root)
    assert len(ids) == len(set(ids)), "arşivde kopya tavsiye YOK"
    v = AE.verify(w.root)
    assert v["agreement"] == 1.0 and v["missing_in_store"] == 0 and v["duplicates_ignored"] == 0
    seqs = [r["meta"]["target_seq"] for r in AE.iter_advice(w.root)]
    assert seqs == list(range(1, len(seqs) + 1)), "hedef sıra numaraları ardışık ve tekil"


# ============================================================================ kayıt engeli
def test_advice_store_at_its_cap_is_visible_as_record_blocked(tmp_path):
    from tradingbot.dashboard.state import StateReader
    from tradingbot.dashboard.templates import advisor_card
    w = run_world(tmp_path / "w", 8)
    a = w.xp._advisor
    assert a.public_state() == "OK" and a.c["advice_written"] == a.c["advice_emitted"] > 0
    a.store.max_total_bytes = 1024                                      # tavan: tavsiye deposu DEGRADED
    for _ in range(4):
        res = w.tour(w.xp)
        assert res["state"] == "OK" and res["rows"] >= 1, "toplayıcı ETKİLENMEZ"
    h = w.xp.health()["advisor"]
    assert h["state"] == AL.S_RECORD_BLOCKED and h["record_blocked"] == "STORE_DEGRADED"
    assert h["advice_emitted"] > h["advice_written"]
    st = _status(w.root)
    assert st["state"] == AL.S_RECORD_BLOCKED and st["record"]["blocked"] == "STORE_DEGRADED"
    assert st["record"]["retry_pending"] > 0 and "RECORD_BLOCKED_STORE_DEGRADED" in st["warnings"]
    html = advisor_card(StateReader(w.eng.cfg.state_path).shared_experience_advisor())
    assert AL.S_RECORD_BLOCKED in html and "b-bad" in html and "yazılan" in html
    a.store.max_total_bytes = None                                      # tavan kalktı (yapılandırma) → yazım sürer
    w.tour(w.xp)
    assert a.public_state() == "OK" and a.c["advice_written"] == a.c["advice_emitted"] and not a._retry
    assert AE.verify(w.root)["missing_in_store"] == 0


# ============================================================================ kurulum yolu taramaz
def test_construction_never_scans_the_advice_store(tmp_path, monkeypatch):
    w = run_world(tmp_path / "w", 26, advice_hot_max_lines=12, advisor_snapshot_every_steps=5)
    born, hwm = w.xp._advisor.born_ms, w.xp._advisor._adv_hwm
    assert hwm == len(_advice_ids(w.root)) > 0
    assert len(XR._segments(XR.manifest(w.root / "advice"))) >= 4
    (w.root / "advice" / AL.META_FILE).unlink()
    (w.root / "advice" / AL.STATE_FILE).unlink()
    calls = {"decompress": 0}
    real = gzip.decompress

    def counting(data, *a, **k):
        calls["decompress"] += 1
        return real(data, *a, **k)

    def forbidden(*a, **k):
        raise AssertionError("kurulum yolunda tam tarama")
    monkeypatch.setattr(gzip, "decompress", counting)
    monkeypatch.setattr(AE, "iter_advice", forbidden)
    monkeypatch.setattr(XR, "_parse_lines", forbidden)
    adv = AL.LiveAdvisor(w.root, settings=w.eng.cfg.v3.shared_experience)
    assert adv.born_ms == born and calls["decompress"] <= 1
    assert json.loads((w.root / "advice" / AL.META_FILE).read_text())["advisors"][AL.ADVISOR_SHA]["advisor_born_ms"] \
        == born
    assert adv._adv_hwm == hwm, "yüksek su işareti deponun SON satırından"


# ============================================================================ yetişme bütçesi / döngü payı / okuyucu
def _catch_up_world(tmp_path: Path, n: int = 240):
    from test_shared_experience_advisor_budget_v1 import _Feed
    from tradingbot.shared_experience.store import ExperienceStore
    root = tmp_path / "xp"
    main = ExperienceStore(root, max_total_mb=None, hot_max_lines=50_000)
    feed = _Feed(seed=4)
    for k in range(n):
        main.append_rows(feed.batch(k))
    return root, main


def test_catch_up_uses_its_own_budget_and_reserves_the_advice_rotation(tmp_path):
    from tradingbot.shared_experience.collector import XpSettings
    root, main = _catch_up_world(tmp_path)
    adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD", advisor_budget_ms=20,
                                                   advisor_catch_up_budget_ms=400, advice_hot_max_lines=600),
                         main_store=main)
    res = adv.on_flush([], budget_ms=20)
    assert adv.mode == AL.MODE_CATCH_UP and adv.c["catch_up_rows"] > 400, "yetişme payı canlı payından büyük"
    assert res["ms"] < AL.OVERRUN_FACTOR * 400 and adv._consec_overruns == 0
    st = _status(root)
    assert st["budget"]["advisor_catch_up_budget_ms"] == 400 and st["budget"]["advisor_budget_ms"] == 20
    # döngü öngörülürse süresi katlama payından düşülür: tahmin çok büyükse adım tek toplu yazımda durur
    adv._rot_ms_est = 1e9
    adv.store._line_count = adv.store.max_lines
    n0 = adv.c["catch_up_rows"]
    adv.on_flush([], budget_ms=20)
    assert 0 < adv.c["catch_up_rows"] - n0 <= 40
    while adv.mode != AL.MODE_LIVE:
        adv._rot_ms_est = 50.0
        adv.on_flush([], budget_ms=20)
    assert adv.reader is None, "canlıda okuyucu (sıcak dosya baytları) bırakılır"
    assert adv.c["catch_up_rows"] == main._line_count and adv.public_state() == "OK"
    assert adv._snap_deferred_consec == 0


# ============================================================================ bellek (taze alt süreç, RSS)
_RSS = r"""
import gc, json, sys, time
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(tests)r)
import numpy  # noqa: F401
from tradingbot.shared_experience import advisor_live as AL
from tradingbot.shared_experience.collector import XpSettings
from tradingbot.shared_experience.store import ExperienceStore


def vm(key):
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith(key + ":"):
                return int(ln.split()[1]) * 1024
    raise KeyError(key)


root = sys.argv[1]
main = ExperienceStore(root, max_total_mb=None, hot_max_lines=50_000)
gc.collect()
r0 = vm("VmRSS")
adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD", advisor_catch_up_budget_ms=5000),
                     main_store=main)
for _ in range(400):
    adv.on_flush([], budget_ms=250)
    if adv.mode == AL.MODE_LIVE:
        break
gc.collect()
print(json.dumps({"mode": adv.mode, "loaded": adv.c["snapshot_loaded"], "est": adv.fold.memory_estimate(),
                  "rss_delta": vm("VmRSS") - r0, "hwm_delta": vm("VmHWM") - r0, "reader": adv.reader is None,
                  "trims": adv.c["memory_trims"]}))
"""


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="Linux /proc gerekir")
def test_restart_from_snapshot_rss_is_bounded_by_the_estimate(tmp_path):
    """İnceleme bulgusu: RSS artışı tahminin ~4,5 katıydı (okuyucunun sıcak dosya kopyası + anlık görüntü açılımının
    geçici tepesi + ayırıcı parçalanması). Taze alt süreçte: yetişme → anlık görüntü → YENİ süreç anlık görüntüden canlıya;
    kalıcı RSS artışı ≤ 2 × tahmin + 12 MB (tahmin DEGRADED tavanının girdisi). Ölçüm (2026-09-30): bu depoda tahmin
    ~4 MB, artış ~7 MB (okuyucu tutulsaydı ~30 MB+); 200 bin satırlık üretim boyutlu depoda tahmin 20 MB, artış 26,5 MB
    (öncesi 41–93 MB)."""
    root, main = _catch_up_world(tmp_path, n=2_400)
    from tradingbot.shared_experience.collector import XpSettings
    adv = AL.LiveAdvisor(root, settings=XpSettings(advisor_mode="RECORD", advisor_catch_up_budget_ms=5000),
                         main_store=main)
    while adv.mode != AL.MODE_LIVE:
        adv.on_flush([], budget_ms=250)
    adv._write_snapshot()
    del adv
    p = subprocess.run([sys.executable, "-c", _RSS % {"root": str(X.ROOT), "tests": str(HERE)}, str(root)],
                       capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr[-3000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["mode"] == "live" and out["loaded"] == 1 and out["reader"] is True
    est = out["est"]
    assert out["rss_delta"] <= 2.0 * est + 12 * MB, (out["rss_delta"] / MB, est / MB)

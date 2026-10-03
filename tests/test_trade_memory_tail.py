"""`trade_memory.jsonl` ARTIMLI / SINIRLI OKUMA (2026-09-28, öğrenme modu; üçüncü doğrulama turu).

Öğrenmede ana bot günde ~45 giriş satırı (~73 KB: ajan + şef raporları) yazar; motor dosyayı her turda baştan birkaç kez
ayrıştırıyordu (157 MB'da okuma başına ~4–5 sn CPU, +520–590 MB tepe RSS). Değişen her okuyucunun çıktısı eski okuyucuyla
BİREBİR aynı olmalı:

* `MemoryTail.closed_rows / last_entries / exit_ids` (deneyim havuzu, giriş değerlendirmesi, replay denetimi, kapanış zinciri)
  — ekleme, kopya giriş, önce-çıkış, başka kaynak, boş/bozuk satır, CRLF, döndürme/kesme/yerinde yeniden yazma, sözlük
  olmayan JSON, UTF-8 hatası, satır içi `\\r`, tamamlanmamış son satır;
* `TradeMemory.trades(project=…)` (eğitim, araştırma) — tam satırların izdüşümüyle aynı; eğitim ve araştırma hattı çıktısı aynı;
* panel `tail_lines` — `read_text().splitlines()[-n:]` ile aynı.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tradingbot.learn.experience import experience_row  # noqa: E402
from tradingbot.learn.memory import MemoryTail, TradeMemory  # noqa: E402

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _legacy_entries(mem: TradeMemory, k: int) -> list[dict]:
    return [r for r in mem.iter_rows() if isinstance(r, dict) and r.get("kind") == "entry"][-k:]


def _legacy_exit_ids(mem: TradeMemory) -> set[str]:
    return {str(r["trade_id"]) for r in mem.iter_rows() if r.get("kind") == "exit" and r.get("trade_id")}


def _check(tail: MemoryTail, mem: TradeMemory, ks=(1, 3, 5), *, clean: bool = True) -> None:
    assert tail.closed_rows() == [experience_row(r) for r in mem.trades(closed_only=True)]
    for k in ks:
        assert tail.last_entries(k) == _legacy_entries(mem, k)
    ids = tail.exit_ids()                          # None → `reconcile` eski akışlı okumayı yapar
    assert (ids is not None) is clean and (ids if ids is not None else _legacy_exit_ids(mem)) == _legacy_exit_ids(mem)


def _append(path: Path, rows: list, *, crlf: bool = False) -> None:
    with open(path, "ab") as fh:
        for r in rows:
            line = r if isinstance(r, (bytes, str)) else json.dumps(r, ensure_ascii=False, default=str)
            if isinstance(line, str):
                line = line.encode("utf-8")
            fh.write(line + (b"\r\n" if crlf else b"\n"))


def _entry(i: int, *, sym: str = "ETH/USDT", heavy: int = 0, src: str | None = None, **kw) -> dict:
    row = {"kind": "entry", "trade_id": "T%d" % i, "symbol": sym, "direction": "LONG" if i % 2 else "SHORT",
           "setup_type": "pullback", "regime": "TREND_UP", "recorded_at": (BASE + timedelta(hours=i)).isoformat(),
           "features": {"rr": 1.0 + i / 10, "atr_pct": 0.2, "ş": "ü"}, "decision": {"reports": ["x" * heavy]},
           "chief": {"r": "y" * heavy}, "snapshot": {"values": {"a": i}}, "risk_decision": {"ok": True}}
    if src is not None:
        row["source"] = src
    row.update(kw)
    return row


def _exit(i: int, r: float = 0.5, pm=None, **kw) -> dict:
    row = {"kind": "exit", "trade_id": "T%d" % i, "recorded_at": (BASE + timedelta(hours=i, minutes=30)).isoformat(),
           "outcome": {"r_multiple": r, "opened_at": (BASE + timedelta(hours=i)).isoformat(),
                       "closed_at": (BASE + timedelta(hours=i + 1)).isoformat()},
           "price_path": [{"p": 1}], "postmortem": {"lesson_codes": ["L%d" % i], "text": "t"} if pm is None else pm}
    row.update(kw)
    return row


def test_incremental_reads_equal_the_full_readers_over_many_appends(tmp_path):
    rng = random.Random(7)
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    tail = MemoryTail(mem, project=experience_row, keep_entries=10)
    _check(tail, mem)                                                      # dosya yok
    i = 0
    for stage in range(12):
        rows: list = []
        for _ in range(rng.randint(1, 6)):
            i += 1
            kind = rng.random()
            if kind < 0.45:
                rows.append(_entry(i, sym=rng.choice(["ETH/USDT", "SOL/USDT"])))
            elif kind < 0.75 and i > 2:
                j = rng.randint(1, i - 1)
                rows.append(_exit(j, r=rng.choice([-1.0, 0.4, 1.3]),
                                  pm=rng.choice([None, {}, {"lesson_codes": None}, {"text": "only"}])))
            elif kind < 0.8:
                rows.append(_entry(rng.randint(1, i)))                    # kopya giriş: eski birleşim sonucu düşürür
            elif kind < 0.85:
                rows.append(_exit(i + 1000))                               # girişsiz çıkış
            elif kind < 0.9:
                rows.append(_entry(i, src="SHADOW"))                       # başka kaynak
            elif kind < 0.93:
                rows.append("")                                            # boş satır
            elif kind < 0.96:
                rows.append('{"bozuk json')                                # bozuk satır (atlanır)
            else:
                rows.append(_entry(i, recorded_at=None) if rng.random() < 0.0 else _entry(i, features=None))
        _append(path, rows, crlf=(stage % 5 == 3))
        _check(tail, mem, ks=(1, 3, 7, 10))
    assert tail.full_loads == 1 and tail.incremental_loads >= 11
    assert tail.stats()["clean"]


def test_rotation_truncation_and_in_place_rewrite_reload_fully(tmp_path):
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    tail = MemoryTail(mem, project=experience_row, keep_entries=5)
    _append(path, [_entry(1), _exit(1), _entry(2), _exit(2, r=-1.0)])
    _check(tail, mem)
    # döndürme: yeni dosya (yeni inode)
    new = tmp_path / "new.jsonl"
    _append(new, [_entry(7), _exit(7, r=2.0)])
    os.replace(new, path)
    _check(tail, mem)
    assert tail.full_loads == 2
    # kesme (daha kısa)
    path.write_text(json.dumps(_entry(9)) + "\n", encoding="utf-8")
    _check(tail, mem)
    # yerinde yeniden yazma, AYNI boy, farklı içerik (aynı inode) → bekçi yakalar
    before = path.read_bytes()
    alt = before.replace(b'"T9"', b'"T8"')
    assert len(alt) == len(before)
    with open(path, "r+b") as fh:
        fh.write(alt)
    _append(path, [_exit(8, r=0.9)])
    _check(tail, mem)
    assert tail.full_loads >= 4


@pytest.mark.parametrize("bad", [b"[1, 2]", b'"str"', b"\xff\xfe{}", b'{"kind": "entry"}\r{"kind": "exit"}'])
def test_rows_the_old_reader_treats_differently_fall_back_to_the_old_reader(tmp_path, bad):
    """Sözlük olmayan JSON (eski okuyucu `row.get` ile düşer), UTF-8 hatası, satır içi `\\r` (metin kipi satırı böler):
    okuyucu "temiz değil" der, çağıran ESKİ yolu çalıştırır — sonuç ya da istisna birebir aynı."""
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    tail = MemoryTail(mem, project=experience_row, keep_entries=5)
    _append(path, [_entry(1), _exit(1)])
    _check(tail, mem)
    _append(path, [bad, _entry(2), _exit(2)])
    for fn_new, fn_old in ((tail.closed_rows, lambda: [experience_row(r) for r in mem.trades(closed_only=True)]),
                           (lambda: tail.last_entries(3), lambda: _legacy_entries(mem, 3))):
        try:
            want = ("ok", fn_old())
        except Exception as exc:  # noqa: BLE001
            want = ("raise", type(exc))
        try:
            got = ("ok", fn_new())
        except Exception as exc:  # noqa: BLE001
            got = ("raise", type(exc))
        assert got == want
    assert tail.exit_ids() is None and not tail.stats()["clean"]
    from tradingbot.learn.reconcile import _memory_exit_ids
    mem.tail = tail
    got = _memory_exit_ids(mem)
    mem.tail = None
    assert got == _memory_exit_ids(mem)


def test_unterminated_last_line_uses_the_old_reader_until_it_is_completed(tmp_path):
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    tail = MemoryTail(mem, project=experience_row, keep_entries=5)
    _append(path, [_entry(1), _exit(1)])
    with open(path, "ab") as fh:                                           # çöküşte yarım kalmış ama geçerli JSON
        fh.write(json.dumps(_entry(2)).encode("utf-8"))
    _check(tail, mem, clean=False)
    assert [r["trade_id"] for r in tail.last_entries(5)] == ["T1", "T2"], "eski okuyucu geçerli son satırı okur"
    _append(path, [_exit(2)])                                              # birleşen satır bozuk → ikisi de atlar
    _check(tail, mem)
    with open(path, "ab") as fh:
        fh.write(b"   ")                                                   # yalnız boşluk: eski okuyucu atlar
    _check(tail, mem)
    _append(path, [_entry(3), _exit(3)])
    _check(tail, mem)
    assert tail.stats()["clean"]


def test_incremental_sync_holds_one_row_not_the_whole_file(tmp_path):
    """Tepe bellek: eski birleşim bütün tam satırları aynı anda tutar; artımlı okuyucu satır satır izdüşümle okur ve bir
    ekten sonra yalnız yeni satırı ayrıştırır."""
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    rows = []
    for i in range(1, 121):
        rows += [_entry(i, heavy=40_000), _exit(i)]
    _append(path, rows)
    tracemalloc.start()
    tracemalloc.reset_peak()
    old = [experience_row(r) for r in mem.trades(closed_only=True)]
    _, peak_old = tracemalloc.get_traced_memory()
    del old
    tail = MemoryTail(mem, project=experience_row, keep_entries=500)
    tracemalloc.reset_peak()
    cur0, _ = tracemalloc.get_traced_memory()
    first = tail.closed_rows()
    _, peak_first = tracemalloc.get_traced_memory()
    _append(path, [_entry(500, heavy=40_000), _exit(500)])
    tracemalloc.reset_peak()
    cur1, _ = tracemalloc.get_traced_memory()
    second = tail.closed_rows()
    _, peak_inc = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(first) == 120 and len(second) == 121
    assert peak_first - cur0 < peak_old / 5, (peak_first - cur0, peak_old)
    assert peak_inc - cur1 < 2_000_000, peak_inc - cur1
    assert tail.full_loads == 1 and tail.incremental_loads == 1


# ============================================================================ izdüşümlü birleşim (eğitim / araştırma)
def test_projected_trades_equal_the_projection_of_the_full_rows(tmp_path):
    from tradingbot.learn.learner_v2 import train_row
    from tradingbot.learn.research_coordinator import research_row
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    _append(path, [_entry(1), _exit(1), _entry(2), _entry(3, outcome={"pre": 1}), _exit(3, pm={}), _entry(1),
                   _exit(1, r=-0.3), _exit(99), _entry(4, recorded_at="2026-08-01T00:00:00+00:00"), _exit(4),
                   _entry(5, src="SHADOW"), _exit(5)])
    for proj in (experience_row, train_row, research_row):
        for closed in (True, False):
            for limit in (None, 2):
                assert mem.trades(closed_only=closed, limit=limit, project=proj) == \
                    [proj(r) for r in mem.trades(closed_only=closed, limit=limit)]
    assert not any("decision" in r or "chief" in r for r in mem.trades(closed_only=True, project=research_row))


def _seeded(tmp_path, n: int = 60) -> TradeMemory:
    """Gerçek v3 snapshot'lı kapanışlar (+ ağır `decision` / `chief` yükleri: okunsalar sonuç değişirdi)."""
    from test_research_runtime_e2e import seed_live_memory
    from tradingbot.config import BotConfig
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    seed_live_memory(cfg, n=n)
    path = cfg.state_path / "trade_memory.jsonl"
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r.get("kind") == "entry":
            r["decision"] = {"p_win": 0.99, "opportunity": {"tradeable": True}, "reports": ["z" * 500]}
            r["chief"] = {"priority": [r.get("symbol")], "risk_mode": "AGRESIF"}
        out.append(json.dumps(r, ensure_ascii=False, default=str))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return TradeMemory(path, source="LIVE_PAPER")


class _FullRows:
    """TradeMemory OLMAYAN hafıza: `train_challenger` eski yoldan (tam satırlar) okur."""

    def __init__(self, mem):
        self.mem = mem

    def trades(self, **kw):
        return self.mem.trades(**kw)


def test_train_challenger_is_identical_with_projected_rows(tmp_path):
    from tradingbot.learn.learner_v2 import LearnConfig, LearnerV2
    from tradingbot.learn.registry import ModelRegistry
    mem = _seeded(tmp_path / "m")
    now = BASE + timedelta(days=400)
    out = {}
    for name, m in (("projected", mem), ("full", _FullRows(mem))):
        d = tmp_path / name
        d.mkdir()
        lr = LearnerV2(m, ModelRegistry(d / "models.json"), LearnConfig(min_samples_train=20), state_path=d / "l.json")
        res = lr.train_challenger(now=now)
        assert res is not None
        reg = json.loads((d / "models.json").read_text(encoding="utf-8"))
        models = [{k: v for k, v in x.items() if k not in ("id", "created_at", "trained_at", "at")} for x in reg["models"]]
        assert len(models) == 1 and models[0]["params"]["feature_names"], "eğitim gerçekten koştu"
        out[name] = (res["metrics"], res["n_train"], res["n_holdout"], json.dumps(models, sort_keys=True, default=str))
    assert out["projected"] == out["full"]


def test_research_pipeline_output_is_identical_without_the_heavy_payloads(tmp_path, monkeypatch):
    """Araştırma hattı (kapsam, atıf, aday üretimi, walk-forward politika değerlendirmesi) `decision` / `chief` okumaz:
    satırlar izdüşümle okununca aynı çıktı."""
    import tradingbot.learn.research_coordinator as RC
    from tradingbot.config import BotConfig
    from tradingbot.learn.attribution import attribution_report
    from tradingbot.learn.coverage import coverage_report
    from tradingbot.learn.policy import candidates_from_attribution
    from tradingbot.replay.policy_eval import evaluate_policies
    mem = _seeded(tmp_path / "m", n=90)
    proj = RC.join_live_rows(mem.path)
    monkeypatch.setattr(RC, "research_row", lambda r: r)
    full = RC.join_live_rows(mem.path)
    assert proj == [RC.RESEARCH_DROP_KEYS.isdisjoint(r) and r for r in proj]
    assert [{k: v for k, v in r.items() if k not in RC.RESEARCH_DROP_KEYS} for r in full] == proj
    assert all("decision" in r and "chief" in r for r in full)

    def pipeline(rows, d):
        cov = coverage_report(rows, source="LIVE_PAPER")
        bounds = RC.anchored_bounds(rows, n_folds=4)
        att = attribution_report(rows, min_bucket=8)
        cands = candidates_from_attribution(att, seed=7, max_candidates=12, risk_profile_max_leverage=1.0)
        d.mkdir(parents=True, exist_ok=True)
        rep = evaluate_policies(BotConfig(), d, rows, bounds, seed=7, min_test_trades=8, candidates=list(cands),
                                point_in_time=True, survivorship_present=False)
        files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))}
        return json.dumps([cov, bounds, att, [c.to_dict() if hasattr(c, "to_dict") else repr(c) for c in cands], rep,
                           files], sort_keys=True, default=str)
    a, b = pipeline(proj, tmp_path / "eval"), pipeline(full, tmp_path / "eval")      # aynı dizin (`run_dir`)
    strip = lambda s: json.loads(s)  # noqa: E731
    sa, sb = strip(a), strip(b)
    for doc in (sa, sb):                                               # yazım anı damgaları karşılaştırmadan düşer
        _drop_times(doc)
    assert sa == sb


def _drop_times(x):
    if isinstance(x, dict):
        for k in list(x):
            if k in ("generated_at", "created_at", "updated_at", "at"):
                x.pop(k)
            else:
                _drop_times(x[k])
    elif isinstance(x, list):
        for v in x:
            _drop_times(v)


# ============================================================================ motor bağlantısı
def test_engine_readers_use_the_tail_and_match_the_old_expressions(tmp_path, monkeypatch):
    from test_engine_v3 import _engine
    from tradingbot.learn.reconcile import _memory_exit_ids
    eng = _engine(tmp_path, monkeypatch, {}, symbols=2)
    assert isinstance(eng.memory.tail, MemoryTail) and eng.memory.tail.project is experience_row
    rows = []
    for i in range(1, 30):
        rows += [_entry(i, heavy=100), _exit(i, r=(-1.0) ** i)]
    _append(eng.memory.path, rows)
    assert eng._memory_last_entries(400) == _legacy_entries(eng.memory, 400)
    assert eng._memory_last_entries(500) == _legacy_entries(eng.memory, 500)
    tail, eng.memory.tail = eng.memory.tail, None
    old_ids = _memory_exit_ids(eng.memory)
    eng.memory.tail = tail
    assert _memory_exit_ids(eng.memory) == old_ids and len(old_ids) == 29
    from types import SimpleNamespace
    cfg = SimpleNamespace(shadow_weight=0.25, shadow_fidelity=0.5)
    pool = eng._prepared_experience_pool(cfg)
    assert eng._exp_index._rows["memory"] == [experience_row(r) for r in eng.memory.trades(closed_only=True)]
    assert len(pool.experiences) >= 29
    loads = tail.full_loads
    _append(eng.memory.path, [_entry(77), _exit(77)])
    eng._prepared_experience_pool(cfg)
    assert eng._exp_index._rows["memory"] == [experience_row(r) for r in eng.memory.trades(closed_only=True)]
    assert tail.full_loads == loads, "ekten sonra yalnız yeni satırlar okunur"


# ============================================================================ panel
def test_dashboard_tail_lines_equal_reading_the_whole_file(tmp_path):
    from tradingbot.dashboard.state import tail_lines
    rng = random.Random(3)
    parts = [b'{"a": 1}', b"", b"  ", b'{"b": "\xc5\x9f\xc3\xbc"}', b"\xff\xfebroken", b'{"c": "x\xe2\x80\xa8y"}',
             b'{"d": "\xc2\x85"}', b"\x0cff", b'{"e": 2}\r', b"plain"]
    for trial in range(40):
        seps = [b"\n", b"\r\n", b"\r", b"\n\n"]
        body = b"".join(rng.choice(parts) + rng.choice(seps) for _ in range(rng.randint(0, 60)))
        if rng.random() < 0.3:
            body = body.rstrip(b"\r\n")                                    # son satır sonsuz
        if rng.random() < 0.2:
            body = b"\xef\xbb\xbf" + body                                   # BOM
        p = tmp_path / ("f%d.jsonl" % trial)
        p.write_bytes(body)
        whole = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for n in (1, 2, 5, 17, 200, 0, -3):
            for block in (1, 7, 64, 1 << 16):
                assert tail_lines(p, n, block=block) == whole[-n:], (trial, n, block)


def test_dashboard_streamed_lines_equal_the_whole_file_split(tmp_path):
    """Panelin işlem ayrıntısı (`book_entry_features`) hafızayı `read_text().splitlines()` ile TAMAMEN belleğe alıyordu
    (MemoryMax 512M); akışlı satırlar aynı, aynı sırada; sonuç aynı."""
    from tradingbot.dashboard.state import iter_text_lines
    rng = random.Random(9)
    parts = [b'{"trade_id": "T1", "features": {"a": 1}}', b"", b"  ", b"\xff\xfebroken", b'{"x": "\xe2\x80\xa8"}',
             b'{"y": "\xc2\x85"}', b"\x0cff", b'{"trade_id": "T2", "entry": {"features": {"b": 2}}}', b"plain\x1c2"]
    for trial in range(40):
        body = b"".join(rng.choice(parts) + rng.choice([b"\n", b"\r\n", b"\r", b"\x0b", b"\n\n"])
                        for _ in range(rng.randint(0, 40)))
        if rng.random() < 0.3:
            body = body.rstrip(b"\r\n")
        p = tmp_path / ("m%d.jsonl" % trial)
        p.write_bytes(body)
        assert list(iter_text_lines(p)) == p.read_text(encoding="utf-8", errors="replace").splitlines(), trial


# ============================================================================ panel — dördüncü doğrulama turu
def _heavy_memory(path: Path, n_trades: int = 70) -> int:
    """~74 KB'lık Türkçe giriş satırları + çıkış satırları (üretim biçimi); dosya boyu döner."""
    txt = "Şef değerlendirmesi: trend güçlü, hacim artıyor, ığüşöç ÇĞİÖŞÜ. " * 20
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(n_trades):
            sym = ("ETH/USDT", "BTC/USDT", "SOL/USDT")[i % 3]
            fh.write(json.dumps({"kind": "entry", "trade_id": "F%05d" % i, "symbol": sym, "recorded_at": "2026-09-%02d" % (1 + i % 28),
                                 "decision": {"reports": [{"text": txt, "i": k} for k in range(50)]}},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"kind": "exit", "trade_id": "F%05d" % i, "recorded_at": "2026-09-%02d" % (1 + i % 28),
                                 "symbol": sym, "outcome": {"r_multiple": (i % 5) - 2, "mae_pct": 1.0, "exit_reason": "stop",
                                                            "path": list(range(300))}}, ensure_ascii=False) + "\n")
    return path.stat().st_size


def _old_tail_jsonl(path: Path, n: int) -> list[dict]:
    """Öğrenme modundan önceki `StateReader.tail_jsonl` (birebir)."""
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
            if isinstance(d, dict):
                out.append(d)
        except json.JSONDecodeError:
            continue
    return out


def test_dashboard_tail_peak_is_the_returned_lines_not_a_multiple_of_the_file(tmp_path):
    """(bulgu R4a-1) Üçüncü turun `tail_lines`i soneki büyüttükçe tamponu yeniden çözüp bölüyordu: tampon + kopya + metin +
    iki satır listesi → tepe ≈ 6–7× dosya (eski `read_text` yolu ≈ 4×). Artık tepe ≈ dönen satırlar + bir blok."""
    from tradingbot.dashboard.state import tail_lines
    p = tmp_path / "trade_memory.jsonl"
    size = _heavy_memory(p, 40)
    whole = p.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = sum(sys.getsizeof(s) for s in whole)
    tracemalloc.start()
    try:
        got = tail_lines(p, 4000)                         # dosyadaki satırdan fazla: dosyanın tamamı
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert got == whole
    assert peak < 1.25 * kept + (1 << 20) + 4 * max(len(s) for s in whole), (peak, kept, size)


def test_dashboard_coin_memory_and_trade_rows_stream_with_bounded_memory(tmp_path):
    """(bulgu R4a-1) `/api/coin-memory/{base}` (4000 satır) ve `/trades/{id}` (2000 satır) trade_memory kuyruğunu TAM
    ayrıştırıp tutuyordu (512M panel ~52 MB dosyada ölüyordu). Kuyruk artık akışla okunur, satır yalnız gerekiyorsa ayrıştırılır
    ve yalnız okunan alanlar tutulur. Sonuç eski yolla AYNI; tepe dosya boyundan bağımsız (bir blok + bir satır)."""
    from tradingbot.dashboard.state import StateReader
    st = tmp_path / "state"
    st.mkdir()
    size = _heavy_memory(st / "trade_memory.jsonl", 160)
    reader = StateReader(st)

    class Old(StateReader):
        def tail_jsonl(self, name, n=200, **_kw):             # eski okuyucu: needle/project yok sayılır
            p = self.state_dir / {"trade_memory": "trade_memory.jsonl", "decision_journal": "decision_journal.jsonl"}[name]
            return _old_tail_jsonl(p, n) if p.exists() else []

    tracemalloc.start()
    try:
        cm = reader.coin_memory("ETH")
        tid = "F00007"
        mem = reader.tail_jsonl("trade_memory", 2000, needle=tid,
                                project=lambda m: m if str(m.get("trade_id") or m.get("id")) == tid else None)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert cm == Old(st).coin_memory("ETH") and cm["real"]["n"] > 0
    assert mem == [m for m in _old_tail_jsonl(st / "trade_memory.jsonl", 2000)
                   if str(m.get("trade_id") or m.get("id")) == tid] and len(mem) == 2
    assert peak < min(size / 3, 3 << 20), (peak, size)


def test_dashboard_needle_prefilter_never_drops_a_row_the_caller_keeps(tmp_path):
    """`tail_jsonl(needle=…)` ön süzgeci: kaçışlı dizeler (\\u0045, \\/), dize olmayan değerler (int/float/bool/None/liste),
    bozuk satırlar, `\\n` dışı satır sınırları — çağıranın süzgecinden sonra sonuç eski okuyucuyla AYNI."""
    from tradingbot.dashboard.state import StateReader
    rng = random.Random(5)
    st = tmp_path / "st"
    st.mkdir()
    vals = ["ETH/USDT", "BTC/USDT", 5, 5.0, 1e1, None, True, False, 0, "", ["ETH/USDT"], "T1", "12", 12, "ESC_E", "ESC_SL"]
    odd = [b"", b"  ", b"\xff\xfe{", b'{"c": "x\xe2\x80\xa8y"}', b'{"d": "\xc2\x85"}', b"\x0cff", b"[1]", b'"s"']

    def row() -> bytes:
        if rng.random() < 0.2:
            return rng.choice(odd)
        d = {"kind": rng.choice(["entry", "exit"])}
        for key in ("symbol", "trade_id", "id"):
            if rng.random() < 0.6:
                d[key] = rng.choice(vals)
        s = json.dumps(d, ensure_ascii=rng.random() < 0.5)
        s = s.replace('"ESC_E"', '"\\u0045TH/USDT"').replace('"ESC_SL"', '"ETH\\/USDT"')
        return s.encode("utf-8")

    for trial in range(60):
        body = b"".join(row() + rng.choice([b"\n", b"\n", b"\r\n", b"\r", b"\x0b"]) for _ in range(rng.randint(0, 50)))
        (st / "trade_memory.jsonl").write_bytes(body)
        for n in (1, 3, 20, 400):
            old = _old_tail_jsonl(st / "trade_memory.jsonl", n)
            reader = StateReader(st)
            assert reader.tail_jsonl("trade_memory", n) == old
            for sym in ("ETH/USDT", "5", "10.0", "None", "True"):
                got = reader.tail_jsonl("trade_memory", n, needle=sym,
                                        project=lambda r: r if str(r.get("symbol") or "") == sym else None)
                assert got == [r for r in old if str(r.get("symbol") or "") == sym], (trial, n, sym)
            for tid in ("T1", "12", "0", "False", ""):
                got = reader.tail_jsonl("trade_memory", n, needle=tid,
                                        project=lambda m: m if str(m.get("trade_id") or m.get("id")) == tid else None)
                assert got == [m for m in old if str(m.get("trade_id") or m.get("id")) == tid], (trial, n, tid)


# ============================================================================ bekçiler ve istisna — dördüncü doğrulama turu
def test_same_size_rewrite_in_the_first_4kb_is_detected_far_from_the_end(tmp_path):
    """(bulgu R4a-2) Aynı inode + aynı boy yerinde değişiklik, son 4 KB bekçisinin ÇOK gerisinde (dosya başında): üçüncü
    turda yakalanmıyordu (eski içerik, `clean=True`). Baş bekçisi (ilk 4 KB) tam yeniden okumaya düşürür. Ortada (iki bekçinin
    dışında) aynı boy değişiklik hâlâ yakalanmaz — sözleşmede yazılı sınır."""
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    tail = MemoryTail(mem, project=experience_row, keep_entries=50)
    _append(path, [x for i in range(40) for x in (_entry(i, heavy=150), _exit(i))])
    assert path.stat().st_size > 5 * MemoryTail.GUARD_BYTES
    _check(tail, mem)
    before = path.read_bytes()
    i = before.index(b'"trade_id": "T0"', before.index(b'"kind": "exit"'))
    assert i < MemoryTail.GUARD_BYTES
    alt = before[:i] + b'"trade_id": "T9"' + before[i + 16:]
    st0 = os.stat(path)
    with open(path, "r+b") as fh:
        fh.write(alt)
    assert (os.stat(path).st_ino, os.stat(path).st_size) == (st0.st_ino, st0.st_size)
    loads = tail.full_loads
    _check(tail, mem)
    assert tail.full_loads == loads + 1 and "T0" not in tail.exit_ids()


def test_an_error_midway_through_a_sync_does_not_apply_lines_twice(tmp_path):
    """(bulgu R4a-3) Satırlar tek tek uygulanıyor, ofset döngü sonunda işleniyordu: okuma ortasında istisna (G/Ç, bellek)
    yarım durum bırakır, yeniden deneme aynı satırları İKİNCİ kez uygulardı (`last_entries` kopyalar, `clean=True`). Artık
    durum sıfırlanır, istisna yükselir; sonraki çağrı tam okur ve eski okuyucuyla aynıdır."""
    path = tmp_path / "trade_memory.jsonl"
    mem = TradeMemory(path)
    boom = {"on": False}

    def proj(r: dict) -> dict:
        if boom["on"] and r.get("trade_id") == "T12":
            boom["on"] = False
            raise MemoryError("geçici")
        return experience_row(r)

    tail = MemoryTail(mem, project=proj, keep_entries=50)
    _append(path, [x for i in range(5) for x in (_entry(i), _exit(i))])
    _check(tail, mem, ks=(1, 3, 5, 10))
    _append(path, [x for i in range(10, 15) for x in (_entry(i), _exit(i))])
    boom["on"] = True
    with pytest.raises(MemoryError):
        tail.last_entries(10)
    _check(tail, mem, ks=(1, 3, 5, 10))
    assert tail.stats()["entries_kept"] == 10 and tail.stats()["clean"]

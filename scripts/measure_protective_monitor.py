#!/usr/bin/env python3
"""KORUYUCU İZLEYİCİ + BELLEK ÖLÇÜMÜ (2026-09-24) — tekrar çalıştırılabilir ölçüm betiği.

İki kip:

1) `index-check` (ağsız, her yerde): pyarrow ile Parquet `HistoryStore`a SENTETİK 4h futures mumları yazar, motorun GERÇEK
   `_build_pattern_index` yolunu (formasyon/benzerlik indeksi) kurar ve sorgular. Kanıtladığı: pyarrow kurulu ortamda
   indeks Parquet'ten gerçekten oluşur ve çalışır. Üretim verisi DEĞİLDİR; performans/bellek iddiası taşımaz.

2) `measure` (üretime benzer girdiler ister): VPS state kopyasının bir KOPYASI üzerinde PAPER motoru kurar, koruyucu
   izleyiciyi (ayrı iş parçacığı, gerçek USDⓈ-M perp mark) başlatır ve TEK ağır tur koşar. Raporlar:
     * süreç bellek tepesi (VmHWM) ve tur boyunca örneklenen RSS tepesi (MB),
     * tur süresi, formasyon indeksinin durumu (olay/seri sayısı),
     * beş defterin (ana bot, T2, M2, Box, formasyon) AÇIK POZİSYON BAŞINA en uzun gözlem aralığı; 60 sn aşımları ayrıca.
   Açık pozisyonu olmayan defter "ÖLÇÜLMEDİ" diye raporlanır (doğrulanmış SAYILMAZ); o defterler için kontrollü senaryo:
   `tests/test_protective_monitor_v1.py::test_the_threaded_monitor_keeps_every_open_position_within_sixty_seconds_during_a_blocked_tour`.

KAYNAK KOPYA KORUNUR: `--source` yalnız okunur ve `--work` altına kopyalanır (varsayılan). Ölçüm kopyası üzerinde
çalışmak için `--work` zaten atılabilir bir kopya olmalı ve `--work-is-disposable` verilmelidir. Canlı VPS'e bağlanmaz;
yalnız Binance public uçlarını (botun kendi kullandıkları) okur. Gerçek emir YOK (mod PAPER değilse durur).

Örnek (Linux, VPS kopyası `~/tb-olcum/data` altında: state/, market/, vault/):
    python scripts/measure_protective_monitor.py index-check
    python scripts/measure_protective_monitor.py measure --source ~/tb-olcum/data --work /tmp/tb-work \
        --config ~/tb-olcum/config.yaml --out olcum.json
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _proc_status_mb(key: str) -> float | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(key + ":"):
                return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        return None
    return None


def _peak_rss_mb() -> float:
    hwm = _proc_status_mb("VmHWM")
    if hwm is not None:
        return hwm
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(ru / (1024.0 * 1024.0) if sys.platform == "darwin" else ru / 1024.0, 1)


class RssSampler(threading.Thread):
    def __init__(self, every_s: float = 0.25):
        super().__init__(daemon=True, name="rss-sampler")
        self.every_s, self.max_mb, self.samples = every_s, 0.0, 0
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            v = _proc_status_mb("VmRSS")
            if v is not None:
                self.max_mb, self.samples = max(self.max_mb, v), self.samples + 1
            self._stop.wait(self.every_s)

    def stop(self) -> None:
        self._stop.set()
        self.join(5)


# ---------------------------------------------------------------------------------------------- 1) index-check
def _synthetic_4h(n: int, seed: int, start_ms: int):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    ts = start_ms + np.arange(n, dtype="int64") * 14_400_000
    vol = rng.uniform(1e3, 5e3, n)
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": hi, "low": lo, "close": close, "volume": vol,
                         "close_time": ts + 14_400_000 - 1, "quote_volume": vol * close, "trades": 100,
                         "taker_buy_base": vol / 2, "taker_buy_quote": vol * close / 2})


def cmd_index_check(args) -> int:
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    from tradingbot.history import store as hs
    out: dict = {"kind": "INDEX_CHECK_SYNTHETIC", "note": "SENTETİK mumlar — üretim ölçümü DEĞİL",
                 "has_parquet": bool(hs.HAS_PARQUET)}
    try:
        import pyarrow
        out["pyarrow"] = pyarrow.__version__
    except ImportError:
        out["pyarrow"] = None
    tmp = Path(tempfile.mkdtemp(prefix="tb-index-check-"))
    try:
        cfg = BotConfig()
        syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT"][: max(2, int(args.symbols))]
        cfg.coins = list(syms)
        cfg.project_root = tmp
        cfg.obsidian.vault_path = str(tmp / "vault")
        cfg.scanner.enabled = False
        cfg.v3 = load_v3({"history": {"enabled": True}})
        store = hs.HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
        now_ms = int(time.time() * 1000)
        start = now_ms - now_ms % 14_400_000 - int(args.bars) * 14_400_000
        for i, s in enumerate(syms):
            store.write("futures", s, "4h", _synthetic_4h(int(args.bars), 11 + i, start), source="synthetic")
        parts = sorted(p.suffix for p in (cfg.cache_path / cfg.v3.history.root_dir).rglob("*.*") if p.suffix in (".parquet", ".gz"))
        out["part_suffixes"] = sorted(set(parts))
        from tradingbot.engine_v3 import TradingEngineV3
        eng = TradingEngineV3(cfg)
        rss0 = _proc_status_mb("VmRSS")
        t0 = time.time()
        pe, last = eng._build_pattern_index(syms)
        out["build_s"] = round(time.time() - t0, 2)
        out["rss_delta_mb"] = round((_proc_status_mb("VmRSS") or 0) - (rss0 or 0), 1)
        out["events"] = len(pe.events) if pe is not None else 0
        out["series"] = len(pe.candles) if pe is not None else 0
        q = pe.query(syms[1], "futures", "4h", "LONG", k=60) if pe is not None else {}
        out["query_neighbors"] = len((q or {}).get("neighbors") or [])
        out["query_keys"] = sorted((q or {}).keys())[:12]
        ok = bool(out["has_parquet"]) and out["part_suffixes"] == [".parquet"] and out["events"] > 0 and out["query_neighbors"] > 0
        out["verdict"] = "OK" if ok else "FAIL"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out.get("verdict") == "OK" else 1


# ---------------------------------------------------------------------------------------------- 2) measure
def _prepare_work(args) -> Path:
    src = Path(args.source).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    if not (src / "state").is_dir():
        raise SystemExit(f"--source altında state/ yok: {src}")
    if args.work_is_disposable:
        if work == src:
            raise SystemExit("--work kaynakla aynı olamaz (kaynak kopya korunur)")
        if not (work / "state").is_dir():
            raise SystemExit(f"--work-is-disposable verildi ama {work}/state yok")
        return work
    if work.exists():
        raise SystemExit(f"--work zaten var: {work} (silmem; boş bir yol verin ya da --work-is-disposable)")
    print(f"kaynak kopyalanıyor (salt okunur): {src} → {work} ...", flush=True)
    shutil.copytree(src, work, symlinks=True)
    return work


def cmd_measure(args) -> int:
    work = _prepare_work(args)
    os.environ["TRADINGBOT_DATA"] = str(work)
    os.environ.pop("TRADINGBOT_STATE_DIR", None)
    os.environ.pop("TRADINGBOT_CACHE_DIR", None)
    os.environ.setdefault("TRADINGBOT_VAULT_PATH", str(work / "vault"))
    import logging
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from tradingbot.config import load_config
    cfg = load_config(args.config)
    if str(cfg.mode).upper() != "PAPER":
        raise SystemExit(f"mod PAPER değil ({cfg.mode}) — ölçüm yapılmadı")
    if not str(cfg.state_path.resolve()).startswith(str(work)):
        raise SystemExit(f"state yolu çalışma kopyasının dışında: {cfg.state_path} (config mutlak yol mu?)")
    report: dict = {"kind": "PRODUCTION_LIKE_MEASUREMENT", "work": str(work), "source": str(Path(args.source).resolve()),
                    "config": str(Path(args.config).resolve()), "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    sampler = RssSampler()
    sampler.start()
    t_init = time.time()
    from tradingbot.engine_v3 import TradingEngineV3
    eng = TradingEngineV3(cfg)
    report["engine_init_s"] = round(time.time() - t_init, 2)
    report["rss_after_init_mb"] = _proc_status_mb("VmRSS")
    report["positions_at_load"] = {"main": sorted(eng.ledger2.positions)}
    for b in eng.strategy_books:
        report["positions_at_load"][b.key] = sorted(b.ledger.positions)
    if eng.pattern_book is not None:
        report["positions_at_load"][eng.pattern_book.key] = sorted(eng.pattern_book.ledger.positions)
    eng.ensure_protective_monitor(interval_s=float(args.interval))
    t0 = time.time()
    tour_err = None
    try:
        summ = eng.tour(do_scan=not args.no_scan, obsidian=not args.no_obsidian, charts=False)
        report["tour"] = {k: summ.get(k) for k in ("run_id", "opened", "closed") if isinstance(summ, dict)}
    except Exception as exc:  # noqa: BLE001
        tour_err = f"{type(exc).__name__}: {exc}"
    report["tour_s"] = round(time.time() - t0, 1)
    report["tour_error"] = tour_err
    # formasyon indeksi (arka planda kurulur): en fazla --index-wait sn beklenir
    deadline = time.time() + float(args.index_wait)
    while time.time() < deadline:
        st = eng.index_refresh_status()
        if (st.get("index") or {}).get("events"):
            break
        time.sleep(2)
    report["pattern_index"] = eng.index_refresh_status()
    # tur bittikten sonra bir izleyici geçişi daha (tur sonu aralığı da ölçülsün)
    mon = eng.protective_monitor
    mon.run_once()
    status = mon.status()
    eng.stop_protective_monitor()
    eng.drain_protective_closes()
    sampler.stop()
    for stopper in (getattr(eng, "pattern_scanner", None), getattr(eng, "box_timer", None), getattr(eng, "_refresher", None)):
        try:
            if stopper is not None and hasattr(stopper, "stop"):
                stopper.stop()
        except Exception:  # noqa: BLE001
            pass
    obs = status["observations"]
    books = {}
    for key, b in (obs.get("books") or {}).items():
        rows = b.get("positions") or {}
        books[key] = {"open_positions": b.get("open_positions"),
                      "verdict": ("NOT_MEASURED_NO_OPEN_POSITION" if not rows else
                                  ("OVER_60S" if (b.get("max_gap_s") or 0) > 60.0 else "WITHIN_60S")),
                      "max_gap_s": b.get("max_gap_s"),
                      "positions": {pid: {k: r.get(k) for k in ("symbol", "open", "observations", "max_gap_s", "current_gap_s", "sources")}
                                    for pid, r in rows.items()}}
    for key in report["positions_at_load"]:
        books.setdefault(key, {"open_positions": 0, "verdict": "NOT_MEASURED_NO_OPEN_POSITION", "max_gap_s": None, "positions": {}})
    report["monitor"] = {"runs": status["runs"], "errors": status["errors"], "last_error": status["last_error"],
                         "duration_max_s": status["duration_max_s"], "pass_gap_max_s": status["pass_gap_max_s"],
                         "worst_gap_s": obs.get("worst_gap_s"), "over_60s": obs.get("exceeded"), "books": books}
    report["memory"] = {"peak_rss_process_mb": _peak_rss_mb(), "peak_rss_sampled_mb": sampler.max_mb, "samples": sampler.samples,
                        "limit_note": "systemd MemoryMax=4G (deploy/tradingbot-worker.service)"}
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"tur {report['tour_s']} sn · bellek tepesi {report['memory']['peak_rss_process_mb']} MB · "
          f"izleyici en kötü aralık {obs.get('worst_gap_s')} sn · rapor {args.out}")
    for key, b in books.items():
        print(f"  {key:22s} açık {b['open_positions']!s:>3} · {b['verdict']:32s} · en uzun {b['max_gap_s']}")
    return 0 if tour_err is None else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index-check", help="ağsız: pyarrow + Parquet ile formasyon indeksini kur ve sorgula (sentetik)")
    s.add_argument("--symbols", type=int, default=3)
    s.add_argument("--bars", type=int, default=900)
    s.set_defaults(fn=cmd_index_check)
    s = sub.add_parser("measure", help="üretim kopyası üzerinde tek ağır tur: bellek + beş defter izleme aralığı")
    s.add_argument("--source", required=True, help="VPS veri kopyası kökü (state/, market/, vault/) — salt okunur")
    s.add_argument("--work", required=True, help="çalışma kopyası yolu (yoksa oluşturulur)")
    s.add_argument("--work-is-disposable", action="store_true", help="--work zaten atılabilir bir kopya; yeniden kopyalama")
    s.add_argument("--config", required=True)
    s.add_argument("--interval", type=float, default=60.0)
    s.add_argument("--index-wait", type=float, default=180.0)
    s.add_argument("--no-scan", action="store_true")
    s.add_argument("--no-obsidian", action="store_true")
    s.add_argument("--out", default="protective_monitor_measurement.json")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_measure)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""KORUYUCU İZLEYİCİ + BELLEK ÖLÇÜMÜ (2026-09-24) — tekrar çalıştırılabilir ölçüm betiği (Linux VE Windows).

Üç kip:

1) `preflight` (kopyalamaz, çalıştırmaz): ortam ve girdiler ölçüme hazır mı — Python, bellek ölçüm yöntemi, pyarrow,
   kaynak düzeni ve boyutu, boş disk, config modu (PAPER), config yollarının çalışma kopyasında kalıp kalmadığı,
   isteğe bağlı Binance erişimi (`--net`). Engel varsa çıkış kodu 1.

2) `index-check` (ağsız): pyarrow ile Parquet `HistoryStore`a SENTETİK 4h mumları yazar, motorun GERÇEK
   `_build_pattern_index` yolunu (formasyon/benzerlik indeksi) kurar ve sorgular. Üretim verisi DEĞİLDİR.

3) `measure` (üretime benzer girdiler ister): VPS state kopyasının bir KOPYASI üzerinde PAPER motoru kurar, koruyucu
   izleyiciyi başlatır ve TEK ağır tur koşar. Raporlar: süreç bellek tepesi ve tur boyunca örneklenen tepe, tur süresi,
   formasyon indeksi durumu, beş defterin (ana bot, T2, M2, Box, formasyon) AÇIK POZİSYON BAŞINA en uzun gözlem aralığı ve
   60 sn aşımları. Açık pozisyonu olmayan defter "ÖLÇÜLMEDİ" diye raporlanır (doğrulanmış SAYILMAZ).

BELLEK ÖLÇÜMÜ (platforma göre, ek paket gerekmez):
  * Linux: `/proc/self/status` VmRSS (anlık) ve VmHWM (süreç tepesi) — VPS'teki 4G sınırına en yakın ölçüt.
  * Windows: Win32 `GetProcessMemoryInfo` — çalışma kümesi (WorkingSet / PeakWorkingSet) ve işlenmiş bellek (Pagefile
    tepe). Linux RSS'e YAKINDIR ama AYNI DEĞİLDİR; VPS cgroup sınırıyla birebir karşılaştırılmaz (raporda yazar).
  * Diğer: `psutil` kuruluysa o, yoksa `resource.getrusage` (yalnız tepe); hiçbiri yoksa bellek alanları boş kalır,
    ölçüm yine koşar.

GÜVENLİK (ölçüm senin gerçek dosyalarına ve hesaplarına dokunmaz):
  * `--source` yalnız okunur; `--work` altına kopyalanır (varsa DURUR, silmez). Kopyadan önce boş disk denetlenir.
  * state / piyasa önbelleği / Obsidian kasası / log / yedek yolları çalışma kopyasına zorlanır; config'te bunların dışına
    çıkan mutlak yol varsa ölçüm başlamaz. Obsidian git senkronu ve Telegram/Discord bildirimleri bu süreçte KAPATILIR.
  * Mod PAPER değilse durur; gerçek emir yok. Yalnız Binance public uçları okunur (botun kendi kullandıkları).
  * Ctrl+C: temizlik yapılır ve o ana kadarki rapor `INTERRUPTED` olarak yazılır.

Örnekler:
  Linux:   python scripts/measure_protective_monitor.py preflight --source ~/tb-olcum/data --work /tmp/tb-work --config ~/tb-olcum/config.yaml --net
           python scripts/measure_protective_monitor.py measure --source ~/tb-olcum/data --work /tmp/tb-work --config ~/tb-olcum/config.yaml
  Windows: python scripts\\measure_protective_monitor.py preflight --source D:\\tb-olcum\\data --work D:\\tb-work --config D:\\tb-olcum\\config.yaml --net
           python scripts\\measure_protective_monitor.py measure --source D:\\tb-olcum\\data --work D:\\tb-work --config D:\\tb-olcum\\config.yaml
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VPS_LIMIT_MB = 4096.0          # deploy/tradingbot-worker.service MemoryMax=4G
TARGET_GAP_S = 60.0


def _configure_console() -> None:
    """Windows konsolu/borusu (cp1252 vb.) Türkçe karakterde UnicodeEncodeError vermesin: yazılamayan karakter değiştirilir."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


# ================================================================================================= bellek ölçümü
def _procfs_reader() -> Callable[[], dict[str, float]]:
    status = Path("/proc/self/status")
    if not status.exists():
        raise OSError("/proc/self/status yok")

    def read() -> dict[str, float]:
        vals: dict[str, float] = {}
        for line in status.read_text().splitlines():
            key = line.split(":", 1)[0]
            if key in ("VmRSS", "VmHWM"):
                vals[key] = int(line.split()[1]) / 1024.0
        if "VmRSS" not in vals:
            raise OSError("VmRSS okunamadı")
        return {"current_mb": vals["VmRSS"], "peak_mb": vals.get("VmHWM", vals["VmRSS"])}
    read()
    return read


def _psapi_reader() -> Callable[[], dict[str, float]]:
    """Windows: kernel32/psapi `GetProcessMemoryInfo` (ctypes; ek paket yok)."""
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)   # Windows dışında AttributeError → sonraki yönteme geçilir
    try:
        fn = k32.K32GetProcessMemoryInfo
    except AttributeError:
        fn = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    k32.GetCurrentProcess.restype = wintypes.HANDLE

    def read() -> dict[str, float]:
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if not fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        mb = 1024.0 * 1024.0
        return {"current_mb": c.WorkingSetSize / mb, "peak_mb": c.PeakWorkingSetSize / mb,
                "commit_mb": c.PagefileUsage / mb, "peak_commit_mb": c.PeakPagefileUsage / mb}
    read()
    return read


def _psutil_reader() -> Callable[[], dict[str, float]]:
    import psutil  # type: ignore
    proc = psutil.Process()

    def read() -> dict[str, float]:
        mi = proc.memory_info()
        peak = getattr(mi, "peak_wset", None)
        return {"current_mb": mi.rss / 1048576.0, "peak_mb": (peak if peak is not None else mi.rss) / 1048576.0}
    read()
    return read


def _getrusage_reader() -> Callable[[], dict[str, float]]:
    import resource   # yalnız POSIX; Windows'ta ImportError → sonraki yönteme geçilir

    def read() -> dict[str, float]:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = ru / (1024.0 * 1024.0) if sys.platform == "darwin" else ru / 1024.0
        return {"peak_mb": peak}
    read()
    return read


_METRIC_NOTE = {
    "procfs": "Linux RSS (/proc VmRSS; tepe VmHWM) — VPS 4G sınırına en yakın ölçüt",
    "psapi": "Windows çalışma kümesi (WorkingSet; tepe PeakWorkingSet) + işlenmiş bellek (Pagefile) — Linux RSS'e YAKIN ama AYNI "
             "DEĞİL; VPS cgroup sınırıyla birebir karşılaştırılmaz",
    "psutil": "psutil RSS (Windows'ta tepe peak_wset) — VPS 4G sınırıyla birebir karşılaştırılmaz",
    "getrusage": "getrusage ru_maxrss (yalnız süreç tepesi; anlık değer yok)",
    "none": "bellek ölçülemedi (bu platformda desteklenen yöntem yok)",
}
#: Deneme sırası (testler değiştirebilir). Platforma özgü yol önce; olmayan atlanır.
MEMORY_ORDER: tuple[str, ...] = ("procfs", "psapi", "psutil", "getrusage")
_READERS = {"procfs": _procfs_reader, "psapi": _psapi_reader, "psutil": _psutil_reader, "getrusage": _getrusage_reader}


class MemoryProbe:
    """Platformdan bağımsız süreç belleği. `backend`: procfs | psapi | psutil | getrusage | none."""

    def __init__(self, order: tuple[str, ...] | None = None) -> None:
        self.backend, self._read, self.errors = "none", None, {}
        for name in (order or MEMORY_ORDER):
            try:
                self._read = _READERS[name]()
                self.backend = name
                break
            except Exception as exc:  # noqa: BLE001 — yöntem yoksa sıradaki denenir
                self.errors[name] = f"{type(exc).__name__}: {exc}"[:160]

    @property
    def note(self) -> str:
        return _METRIC_NOTE[self.backend]

    def read(self) -> dict[str, float]:
        if self._read is None:
            return {}
        try:
            return {k: round(v, 1) for k, v in self._read().items()}
        except Exception:  # noqa: BLE001
            return {}

    def current_mb(self) -> float | None:
        return self.read().get("current_mb")

    def peak_mb(self) -> float | None:
        return self.read().get("peak_mb")


class RssSampler(threading.Thread):
    """Bellek örnekleyici (anlık değer). Durdurma olayı `_halt`: `threading.Thread._stop` iç metodunu EZMEMELİ (ezilirse
    `join` → `self._stop()` çağrısı TypeError verir)."""

    def __init__(self, every_s: float = 0.25, probe: MemoryProbe | None = None):
        super().__init__(daemon=True, name="rss-sampler")
        self.every_s, self.max_mb, self.samples = every_s, 0.0, 0
        self.probe = probe or MemoryProbe()
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            v = self.probe.current_mb()
            if v is not None:
                self.max_mb, self.samples = max(self.max_mb, v), self.samples + 1
            self._halt.wait(self.every_s)

    def stop(self, timeout: float = 5.0) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout)


# ================================================================================================= yollar / disk / ortam
def _inside(child: Any, parent: Any, pathmod: Any = os.path) -> bool:
    """`child`, `parent`in içinde mi (eşitlik dahil)? Büyük/küçük harf ve ayırıcı Windows'ta duyarsız; 'work2', 'work'ün
    içinde SAYILMAZ (önek hatası yok); farklı sürücü → False."""
    c = pathmod.normcase(pathmod.normpath(pathmod.abspath(str(child))))
    p = pathmod.normcase(pathmod.normpath(pathmod.abspath(str(parent))))
    try:
        return pathmod.commonpath([c, p]) == p
    except ValueError:
        return False


def _real(p: Any) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def _tree_size(root: Path) -> tuple[int, int]:
    """(bayt, dosya sayısı) — sembolik bağlar izlenmez."""
    total = files = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            total += st.st_size
            files += 1
    return total, files


def _free_bytes(target: Path) -> int:
    p = target
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


_ENV_KEYS = ("TRADINGBOT_DATA", "TRADINGBOT_STATE_DIR", "TRADINGBOT_CACHE_DIR", "TRADINGBOT_LOG_DIR", "TRADINGBOT_BACKUPS_DIR",
             "TRADINGBOT_VAULT_PATH", "TRADINGBOT_VAULT_GIT_SYNC")


@contextlib.contextmanager
def measure_env(work: Path) -> Iterator[None]:
    """Ölçüm süresince bütün veri yolları çalışma kopyasına: state/, market/, vault/, logs/, backups/. Önceki ortam geri
    yüklenir. Kasa git senkronu kapalı."""
    old = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["TRADINGBOT_DATA"] = str(work)
        os.environ["TRADINGBOT_VAULT_PATH"] = str(work / "vault")       # setdefault DEĞİL: gerçek kasa asla kullanılmaz
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_config(path: str) -> Any:
    from tradingbot.config import load_config
    return load_config(path)


def _neutralize(cfg: Any) -> dict[str, Any]:
    """Dış etkili yan yolları kapatır; ne kapatıldığını döner (rapora yazılır)."""
    done: dict[str, Any] = {}
    if getattr(cfg.obsidian, "git_sync", False):
        done["obsidian.git_sync"] = "true → false"
    cfg.obsidian.git_sync = False
    v3 = getattr(cfg, "v3", None)
    tg = getattr(v3, "telegram", None)
    if tg is not None:
        if getattr(tg, "enabled", False):
            done["telegram.enabled"] = "true → false"
        tg.enabled = False
    mon = getattr(v3, "monitoring", None)
    for attr in ("telegram_enabled", "discord_enabled"):
        if mon is not None and hasattr(mon, attr):
            if getattr(mon, attr):
                done["monitoring.%s" % attr] = "true → false"
            setattr(mon, attr, False)
    return done


def _config_problems(cfg: Any, work: Path) -> tuple[list[str], dict[str, str]]:
    """Ölçümü başlatmaya engel config sorunları + yolların son hâli."""
    paths = {"state": str(cfg.state_path), "cache": str(cfg.cache_path), "vault": str(cfg.obsidian.vault_path),
             "logs": str(cfg.logs_path), "backups": str(cfg.backups_path)}
    problems = []
    if str(getattr(cfg, "mode", "")).upper() != "PAPER":
        problems.append(f"mod PAPER değil ({cfg.mode})")
    for name, p in paths.items():
        if not _inside(_real(p), work):
            problems.append(f"{name} yolu çalışma kopyasının DIŞINDA: {p} (config'te mutlak yol olabilir; göreli yol kullanın)")
    return problems, paths


# ================================================================================================= 1) preflight
def cmd_preflight(args) -> int:
    src = _real(args.source)
    work = _real(args.work)
    out: dict[str, Any] = {"kind": "PREFLIGHT", "python": platform.python_version(), "platform": platform.platform(),
                           "blockers": [], "warnings": []}
    probe = MemoryProbe()
    out["memory"] = {"backend": probe.backend, "note": probe.note, **probe.read(), "unavailable": probe.errors}
    if probe.backend == "none":
        out["warnings"].append("bellek ölçülemeyecek (rapor alanları boş kalır)")
    if sys.version_info < (3, 11):
        out["blockers"].append("Python >= 3.11 gerekli")
    try:
        import pyarrow
        out["pyarrow"] = pyarrow.__version__
    except ImportError:
        out["pyarrow"] = None
        out["warnings"].append("pyarrow yok: geçmiş veri deposu CSV'ye düşer (üretimle aynı değil) — pip install -r requirements.txt")
    out["source"] = {"path": str(src), "state": (src / "state").is_dir(), "market": (src / "market").is_dir(),
                     "vault": (src / "vault").is_dir()}
    if not (src / "state").is_dir():
        out["blockers"].append(f"--source altında state/ yok: {src}")
    else:
        size, files = _tree_size(src)
        out["source"].update({"size_mb": round(size / 1048576.0, 1), "files": files})
        if not (src / "market").is_dir():
            out["warnings"].append("market/ yok: tur geçmiş veriyi ağdan çeker, ölçüm üretime benzemez")
        need = int(size * 1.05) + 512 * 1048576
        free = _free_bytes(work)
        out["disk"] = {"work": str(work), "free_mb": round(free / 1048576.0, 1), "need_mb": round(need / 1048576.0, 1)}
        if args.work_is_disposable:
            if not (work / "state").is_dir():
                out["blockers"].append(f"--work-is-disposable verildi ama {work / 'state'} yok")
        elif work.exists():
            out["blockers"].append(f"--work zaten var: {work} (boş bir yol verin)")
        elif free < need:
            out["blockers"].append(f"boş disk yetersiz: {free // 1048576} MB var, ~{need // 1048576} MB gerekli")
    if _inside(work, src) or _inside(src, work):
        out["blockers"].append("--work ile --source iç içe olamaz")
    try:
        with measure_env(work):
            cfg = _load_config(args.config)
            out["neutralized"] = _neutralize(cfg)
            problems, paths = _config_problems(cfg, work)
        out["config"] = {"path": str(_real(args.config)), "mode": str(cfg.mode), "paths": paths}
        out["blockers"] += problems
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        out["blockers"].append(f"config yüklenemedi: {type(exc).__name__}: {exc}"[:300])
    if args.net:
        try:
            from tradingbot.market.http import HttpClient
            from tradingbot.market.providers import BinanceFuturesProvider
            from tradingbot.market.ratelimit import BudgetPool
            prov = BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, BudgetPool().get("fapi.binance.com"),
                                                     timeout=5.0, max_retries=0))
            rows = prov.http.get(f"{prov.prefix}/premiumIndex", weight=10)
            out["network"] = {"ok": True, "symbols": len(rows) if isinstance(rows, list) else 1}
        except Exception as exc:  # noqa: BLE001
            out["network"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
            out["blockers"].append("Binance public uçlarına erişilemiyor (ölçüm gerçek fiyat ister)")
    out["ready"] = not out["blockers"]
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    print("HAZIR" if out["ready"] else "HAZIR DEĞİL: " + " | ".join(out["blockers"]))
    return 0 if out["ready"] else 1


# ================================================================================================= 2) index-check
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
    probe = MemoryProbe()
    out: dict = {"kind": "INDEX_CHECK_SYNTHETIC", "note": "SENTETİK mumlar — üretim ölçümü DEĞİL",
                 "platform": platform.platform(), "has_parquet": bool(hs.HAS_PARQUET), "memory_backend": probe.backend}
    try:
        import pyarrow
        out["pyarrow"] = pyarrow.__version__
    except ImportError:
        out["pyarrow"] = None
    tmp = Path(tempfile.mkdtemp(prefix="tb-index-check-"))
    try:
        with measure_env(tmp):                              # kasa/log/yedek geçici dizinde
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
            rss0 = probe.current_mb()
            t0 = time.time()
            pe, _last = eng._build_pattern_index(syms)
            out["build_s"] = round(time.time() - t0, 2)
            rss1 = probe.current_mb()
            out["rss_delta_mb"] = round(rss1 - rss0, 1) if rss0 is not None and rss1 is not None else None
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


# ================================================================================================= 3) measure
def _plan_work(args) -> tuple[Path, Path, dict[str, Any]]:
    """Kopyalamadan ÖNCE bütün engeller (yan etkisiz): kaynak düzeni, iç içe yollar, var olan çalışma dizini, boş disk."""
    src = _real(args.source)
    work = _real(args.work)
    info: dict[str, Any] = {"layout": {"state": (src / "state").is_dir(), "market": (src / "market").is_dir(),
                                       "vault": (src / "vault").is_dir()}}
    if not (src / "state").is_dir():
        raise SystemExit(f"--source altında state/ yok: {src}")
    if _inside(work, src) or _inside(src, work):
        raise SystemExit("--work ile --source iç içe olamaz (kaynak kopya korunur)")
    if args.work_is_disposable:
        if not (work / "state").is_dir():
            raise SystemExit(f"--work-is-disposable verildi ama {work / 'state'} yok")
        info["copied"] = False
        return src, work, info
    if work.exists():
        raise SystemExit(f"--work zaten var: {work} (silmem; boş bir yol verin ya da --work-is-disposable)")
    size, files = _tree_size(src)
    need = int(size * 1.05) + 512 * 1048576
    free = _free_bytes(work)
    if free < need:
        raise SystemExit(f"boş disk yetersiz: {free // 1048576} MB var, ~{need // 1048576} MB gerekli ({work})")
    info.update({"copied": True, "size_mb": round(size / 1048576.0, 1), "files": files})
    return src, work, info


def _copy_source(src: Path, work: Path, info: dict[str, Any]) -> None:
    print(f"kaynak kopyalanıyor (salt okunur): {src} → {work} ({info['size_mb']:.0f} MB, {info['files']} dosya) ...", flush=True)
    t0 = time.time()
    # Windows'ta sembolik bağ oluşturmak yetki ister: bağın hedefi kopyalanır; kırık bağlar atlanır
    shutil.copytree(src, work, symlinks=(os.name != "nt"), ignore_dangling_symlinks=True)
    info["copy_s"] = round(time.time() - t0, 1)
    if not info["layout"]["market"]:
        print("UYARI: market/ yok — tur geçmiş veriyi ağdan çeker, ölçüm üretime benzemez", flush=True)


def _book_rows(obs: dict, positions_at_load: dict) -> dict:
    books = {}
    for key, b in (obs.get("books") or {}).items():
        rows = b.get("positions") or {}
        books[key] = {"open_positions": b.get("open_positions"),
                      "verdict": ("NOT_MEASURED_NO_OPEN_POSITION" if not rows else
                                  ("OVER_60S" if (b.get("max_gap_s") or 0) > TARGET_GAP_S else "WITHIN_60S")),
                      "max_gap_s": b.get("max_gap_s"),
                      "positions": {pid: {k: r.get(k) for k in ("symbol", "open", "observations", "max_gap_s", "current_gap_s", "sources")}
                                    for pid, r in rows.items()}}
    for key in positions_at_load or {}:
        books.setdefault(key, {"open_positions": 0, "verdict": "NOT_MEASURED_NO_OPEN_POSITION", "max_gap_s": None, "positions": {}})
    return books


def cmd_measure(args) -> int:
    src, work, prep = _plan_work(args)
    import logging
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with measure_env(work):
        cfg = _load_config(args.config)                  # config KOPYADAN ÖNCE doğrulanır: yanlış config'te kopya yapılmaz
        neutralized = _neutralize(cfg)
        problems, paths = _config_problems(cfg, work)
        if problems:
            raise SystemExit("ölçüm başlamadı: " + " | ".join(problems))
        if prep.get("copied"):
            _copy_source(src, work, prep)
        return _run_measure(args, cfg, work, prep, neutralized, paths)


def _run_measure(args, cfg, work: Path, prep: dict, neutralized: dict, paths: dict) -> int:
    probe = MemoryProbe()
    report: dict = {"kind": "PRODUCTION_LIKE_MEASUREMENT", "platform": platform.platform(), "python": platform.python_version(),
                    "work": str(work), "source": str(_real(args.source)), "config": str(_real(args.config)),
                    "prepare": prep, "paths": paths, "neutralized": neutralized,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    sampler = RssSampler(probe=probe)
    sampler.start()
    eng = None
    tour_err: str | None = None
    status: dict = {}
    try:
        t_init = time.time()
        from tradingbot.engine_v3 import TradingEngineV3
        eng = TradingEngineV3(cfg)
        report["engine_init_s"] = round(time.time() - t_init, 2)
        report["rss_after_init_mb"] = probe.current_mb()
        report["positions_at_load"] = {"main": sorted(eng.ledger2.positions)}
        for b in eng.strategy_books:
            report["positions_at_load"][b.key] = sorted(b.ledger.positions)
        if eng.pattern_book is not None:
            report["positions_at_load"][eng.pattern_book.key] = sorted(eng.pattern_book.ledger.positions)
        eng.ensure_protective_monitor(interval_s=float(args.interval))
        t0 = time.time()
        try:
            summ = eng.tour(do_scan=not args.no_scan, obsidian=not args.no_obsidian, charts=False)
            report["tour"] = {k: summ.get(k) for k in ("run_id", "opened", "closed") if isinstance(summ, dict)}
        except KeyboardInterrupt:
            tour_err = "INTERRUPTED"
        except Exception as exc:  # noqa: BLE001
            tour_err = f"{type(exc).__name__}: {exc}"
        report["tour_s"] = round(time.time() - t0, 1)
        report["tour_error"] = tour_err
        if tour_err != "INTERRUPTED":
            # formasyon indeksi (arka planda kurulur): en fazla --index-wait sn beklenir
            try:
                deadline = time.time() + float(args.index_wait)
                while time.time() < deadline:
                    st = eng.index_refresh_status()
                    if (st.get("index") or {}).get("events"):
                        break
                    time.sleep(2)
                report["pattern_index"] = eng.index_refresh_status()
                # tur bittikten sonra bir izleyici geçişi daha (tur sonu aralığı da ölçülsün)
                eng.protective_monitor.run_once()
            except KeyboardInterrupt:
                tour_err = report["tour_error"] = "INTERRUPTED"
        status = eng.protective_monitor.status() if eng.protective_monitor is not None else {}
    finally:
        # TEMİZLİK her koşulda: izleyici, arka plan iş parçacıkları, örnekleyici (ölçüm yarıda kalsa da süreç asılı kalmaz)
        if eng is not None:
            try:
                eng.stop_protective_monitor()
                eng.drain_protective_closes()
            except Exception as exc:  # noqa: BLE001
                report["cleanup_error"] = f"{type(exc).__name__}: {exc}"[:300]
            for stopper in (getattr(eng, "pattern_scanner", None), getattr(eng, "box_timer", None), getattr(eng, "_refresher", None)):
                try:
                    if stopper is not None and hasattr(stopper, "stop"):
                        stopper.stop()
                except Exception:  # noqa: BLE001
                    pass
        sampler.stop()
    obs = status.get("observations") or {}
    books = _book_rows(obs, report.get("positions_at_load") or {})
    report["monitor"] = {"runs": status.get("runs"), "errors": status.get("errors"), "last_error": status.get("last_error"),
                         "duration_max_s": status.get("duration_max_s"), "pass_gap_max_s": status.get("pass_gap_max_s"),
                         "worst_gap_s": obs.get("worst_gap_s"), "over_60s": obs.get("exceeded"), "books": books}
    final = probe.read()
    peak = final.get("peak_mb")
    report["memory"] = {"backend": probe.backend, "metric": probe.note,
                        "peak_rss_process_mb": peak, "peak_rss_sampled_mb": sampler.max_mb if sampler.samples else None,
                        "samples": sampler.samples, "peak_commit_mb": final.get("peak_commit_mb"),
                        "under_vps_limit": (peak < VPS_LIMIT_MB) if peak is not None else None,
                        "comparable_to_vps_limit": probe.backend == "procfs",
                        "limit_note": "systemd MemoryMax=4G (deploy/tradingbot-worker.service)"}
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = Path(args.out)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    mem = report["memory"]
    print(f"tur {report.get('tour_s')} sn · bellek tepesi {mem['peak_rss_process_mb']} MB ({probe.backend}) · "
          f"izleyici en kötü aralık {obs.get('worst_gap_s')} sn · rapor {out}")
    if tour_err:
        print(f"TUR HATASI: {tour_err}")
    for key, b in books.items():
        print(f"  {key:22s} açık {b['open_positions']!s:>3} · {b['verdict']:32s} · en uzun {b['max_gap_s']}")
    return 0 if tour_err is None else (130 if tour_err == "INTERRUPTED" else 2)


def main(argv=None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("preflight", help="kopyalamadan/çalıştırmadan: ortam, girdiler, disk, config güvenliği, (isteğe bağlı) ağ")
    s.add_argument("--source", required=True)
    s.add_argument("--work", required=True)
    s.add_argument("--config", required=True)
    s.add_argument("--work-is-disposable", action="store_true")
    s.add_argument("--net", action="store_true", help="Binance public premiumIndex'e bir istek at")
    s.set_defaults(fn=cmd_preflight)
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

"""Veri birimi (`engine-data`; §2.2, §2.4, §3.2–§3.4; P1b) — motorun AĞ KULLANAN TEK modülü.

Yalnız veri biriminin giriş noktasından (`cli_v3.cmd_engine_data`, tembel) import edilir; gece biriminin import grafiği
bu modülü içermez (test). Bütün yazımlar `data/research` altındadır; worker'ın state'ine, `data/market`'e ve
HistoryStore'una yazılmaz. Karar-nötr ve kayıt-yalnızdır.

Komutlar (`run_data`): `--update` (00:41 UTC birimi), `--backfill` (ilk doldurma; kaldığı yerden devam eder),
`--import-dukascopy <dizin>` (hazır aynayı içe alma), `--status` (salt-okunur; `status_lines`).

**Akış (update / backfill):** root reddi → PAPER → `data.lock` (alınamazsa beklemeden `SKIPPED_LOCKED`, çıkış 0) →
disk koruması → çalışma zamanı yalıtım öz-denetimi (state/market/app'e yazma reddi, cgroup `memory.max`, `/tmp` ve
araştırma kökü yazılabilir; soket denemesi YOK — tek ağlı birim budur) → kurtarma (`ResearchStore.recover_series`;
haftalık 1/7 derin örneklem) → U_R ve günlük evren anlık görüntüsü → (backfill) listelenme + tohum → ARŞİV adımı →
worker REST koruması → REST kuyruğu → `exchangeInfo` anlık görüntüsü → `data_status.json` + `data_seal`.

1. **Önce arşiv** (data.binance.vision, CDN ağırlık harcamaz). Biten aylar aylık zip'ten; içinde bulunulan ay ve
   aylık zip'i henüz yayımlanmamış önceki ay gün zip'lerinden (`metrics` yalnız gün zip'idir; `fundingRate`'in gün
   zip'i yoktur, içinde bulunulan ay REST'ten gelir). Her zip `.CHECKSUM` ile doğrulanır; `.CHECKSUM` yoksa satırlar
   `archive_unverified` yazılır ve sonraki gecelerde checksum yeniden aranır (bulunursa aynı satırlar `archive`a
   yükselir). Checksum tutmazsa zip önbelleğe alınmaz, parça fail-closed işaretlenir (`mark_bad_chunk`). Önbellek
   düzeni `archive_cache/archive/<sunucu>/<url yolu>` (+ `.CHECKSUM`, `.missing`): `gold_lab.ArchiveCache` /
   `book_lab.ZipCache` ile bayt-özdeştir; YALNIZ 404 "dosya yok"tur ve yalnız dönemi 10 günden eskiyse kalıcı
   (`.missing`) sayılır. 403/451 (CDN/WAF erişim engeli) "yok" DEĞİLDİR: arşiv adımı o çalıştırmada durur
   (`ARCHIVE_HALTED_<kod>`, kalan seriler `STALE`), kalıcı işaret yazılmaz; 5xx/408/429 geçicidir (yeniden denenir).
   1m zip'leri doğrulanmış alımdan sonra silinir; sha256'ları seri manifestinin dosya defterinde (`files`) kalır. Bir
   ayın gün zip'leri o ayın yazımı tamamlanınca "bitti" sayılır (yarıda kalan geçici hata bütün ayı yeniden dener;
   önbellekteki zip'ler yeniden indirilmez). `--update` 62 günlük pencerenin DIŞINDAKİ açık dönemleri (eksik ya da
   doğrulanmamış ay/gün) de onarır (çalıştırma başına en çok `REPAIR_MAX_TASKS` görev); kalan boşluk `holes` ve
   `ARCHIVE_HOLES` olarak dürüstçe yazılır (sonuç `PARTIAL`).
2. **Worker REST koruması** (`check_worker_journal`): REST'e dokunmadan önce `journalctl -u tradingbot-worker
   --since=-60min` okunur ve `market/http.py`'nin 429/418 satırları sayılır (`WORKER_RATE_LINE_RE`, metin kalıbı
   testte http.py'nin biçim dizgelerine karşı sabitlenir). Sayı > 0 → `REST_GUARD_SKIP`; günlük okunamıyorsa (çıkış
   kodu ≠ 0, yetki ipucu, ya da 60 dakikada HİÇ satır yok — worker ~20 dakikada bir tur yazar, boş çıktı "okuyamadım"
   demektir) → `REST_GUARD_UNKNOWN`. İkisinde de REST adımı TAMAMEN atlanır. Koruma HER REST kullanımından hemen önce
   yeniden okunur (`exchangeInfo`, REST kuyruğunun başı) ve REST sürerken sonucu `GUARD_MAX_AGE_S`'den eski olmaz;
   bayatlayınca yeniden okunur, geçmezse kalan REST tamamen atlanır (ilk doldurmada arşiv adımı saatler sürer).
3. **REST kuyruğu.** Yalnız arşiv tabanı olan serilerde, son satırdan sonraki KAPANMIŞ barlar (en çok son 2 gün;
   fonlama 7 gün) `_src=rest` olarak yazılır. "Kapanmış" isteğin GÖNDERİLDİĞİ ana göredir (bar kapanışı ≤ gönderim;
   yanıt geç gelse bile oluşmakta olan bar yazılmaz). Geçmiş aylar için REST yoktur. Delist olmuş sembollerde REST
   yoktur (`DELISTED`: `exchangeInfo` durumu TRADING değil / listede yok, ya da REST `-1121 Invalid symbol`); veri
   delist anına kadar kalır, seri bayat ya da `PARTIAL` sayılmaz, ayrı raporlanır.
4. **Bütçe** (`EngineBudget`): tek `BudgetPool(safety=0,1)`; ayrıca motorun kendi ağırlığı KAYAN 60 sn penceresinde
   ≤ 0,1 × IP limiti (fapi 2400 → 240; spot için `ratelimit`in muhafazakâr 1200'ü → 120) — jeton kovası tek başına bir
   dakikada iki kapasiteye izin verirdi. Her yanıtta `X-MBX-USED-WEIGHT-1M` okunur; IP limitinin %50'sine ulaşınca bir
   sonraki dakika başına kadar beklenir; bütçe bekledikten sonra saat penceresi YENİDEN denetlenir (bekleme isteği 4h
   penceresine taşıyamaz). 418/429 (ve erişim engeli 403/451) gelince REST adımı DURUR, kalan seriler REST'siz kalır ve
   `STALE` olur (§3.4 tablosu), `REST_HALTED_<kod>` yazılır. Saatlik REST kuyruğu yoktur.
5. **Arşiv uzlaştırma.** Gün/ay zip'i yayımlanınca REST satırları doğrulanmış arşiv satırlarıyla değişir (öncelik
   kuralı) ve fark satır satır `runs/data/<run_id>/diffs.jsonl.gz`'a yazılır (beklenen 0).
6. **Hata yalıtımı.** Her seri 3 denemeli, titreşimli yeniden denemeye sarılır ve istisnası yakalanır; hata o seriyi
   `STALE` yapar, çalıştırma sürer (`history-collect`'in ilk hatada durma açığı kapalı).
7. **Mühür.** `summary/data_status.json` (şema `engine_data_status_v1`): seri başına `last_ts`, `rows`, `gaps`
   (fonlamada veriden aralıkla), `quality`, `unverified_rows`, `stale`, kaynak sayıları; `data_seal` ve parça listesi
   `store/_seal/<seal>.json.gz`. Mühür yalnız TAMAMLANAN bir çalıştırmada yenilenir; uzun bir ilk doldurma sürerken
   dosya son tamamlanmış mührü ve `running` altında ilerlemeyi (`done/total`, son 1 saatin hızı, tahmini bitiş) taşır.
8. **Zaman pencereleri.** Ağ isteği hh∈{00,04,08,12,16,20} için hh:00–hh:35 UTC arasında YAPILMAZ: istekten önce saat
   denetlenir, pencere içindeyse hh:36'ya kadar beklenir (`PAUSED_4H_WINDOW` ilerlemeye yazılır). Zamanlayıcı birimi
   00:41'de başlar ve 50 dk sert sınırı olduğundan pencereye hiç girmez; kural ilk doldurma ve elle çalıştırmalar
   içindir. `--update`'in iç son tarihi başlangıç + 45 dk ve 00:00–01:26 arasında başladıysa o günün 01:26'sıdır
   (01:31 sert durma, 01:37 gece birimi); son tarihten sonra yeni seri başlatılmaz (`DEADLINE`, çıkış 0). Ağsız ağır
   adımlar da (kurtarma, tohum) pencerede bekler: tohum worker deposunu okur, IndexRefresher o pencerede yazar.
9. **Disk (2026-10-06).** P1a kapanış arşivi (gece S1a, yeniden üretilemez) depo ya da zip aynası yüzünden ASLA aç
   kalmaz: veri birimi gece biriminin reddinden (boş < 10 GB, araştırma ≥ 20 GB) `DATA_DISK_MARGIN` (3 GB) ÖNCE durur.
   Denetim başlangıçta ve DÖNGÜNÜN İÇİNDE (her ağ isteğinden ve her tohum serisinden önce; `statvfs` ucuzdur,
   araştırma boyutu boş alan düşüşüyle tahmin edilir ve yarım saatte bir yeniden ölçülür) yapılır; sınır aşılırsa
   çalıştırma `DISK_REFUSE` (çıkış 5) ile durur, yazılmış veri mühürlenir. Boş/kullanılan alan ilerlemede görünür.
10. **Durdurma.** SIGTERM (`systemctl stop`) `STOPPED` (çıkış 1) olarak kayda geçer: `data_status.json`'da bayat
   `running` kalmaz; mühür yenilenmez. SIGKILL sonrası kalan `running` bloğu okuyucularda süreç yoksa "DURDU" yazılır.

Dukascopy (`import_dukascopy`): v1'de indirici YOKTUR. Hazır ayna (`<kök>/XAUUSD/<YYYY>/<MM0>/BID_candles_hour_1.bi5`
aylık saatlik; `<kök>/XAUUSD/<YYYY>/BID_candles_day_1.bi5` yıllık günlük; isteğe bağlı
`<kök>/XAUUSD/<YYYY>/<MM0>/<DD>/BID_candles_min_1.bi5`) önce `.part` ve kayıt denetiminden geçer, bayt-özdeş olarak
`research/dukascopy/` altına kopyalanır, kaynak baştan sona aynı kalmalıdır (sha256 iki kez); bir adım tutmazsa
"yapılamadı" yazılır ve depoya HİÇBİR satır yazılmaz (sahte veri yok). bi5 = LZMA "alone" akışı, 24 baytlık big-endian
kayıt `>5if` (dönem başından saniye, açılış, kapanış, düşük, yüksek ÷1000, float32 hacim). Hacmi 0 ve düz bar kapalı
piyasadır (atılır); hacmi 0 hareketli barlar tutulur. Dukascopy kaynağının yayımlanmış checksum'ı olmadığından satırlar
`archive_unverified` işaretlenir (doğrulanmış sayımlara ve pariteye girmez).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import lzma
import os
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from ..market.ratelimit import DEFAULT_WEIGHTS_PER_MINUTE, USED_WEIGHT_HEADER, BudgetPool
from . import ENGINE_VERSION
from . import selfcheck as SC
from .lock import SKIPPED_LOCKED, data_lock
from .night import root_refusal
from .paths import (DISK_OK, DISK_REFUSE, DISK_WARN, GB, MIN_FREE_BYTES, REFUSE_RESEARCH_BYTES, WARN_RESEARCH_BYTES, EnginePaths,
                    dir_size_bytes, disk_guard, gzip_bytes, json_line)
from .seed import ST_ERROR, ST_REDOWNLOAD, ST_SEEDED, seed_series
from .store import (DUKA_1H, DUKA_1M, FUNDING, MARKPX_1H, METRICS_5M, METRICS_COLS, PREMIUM_1H, PX_COLS, SERIES_HALTED,
                    SRC_ARCHIVE, SRC_REST, SRC_UNVERIFIED, ArchiveUnitError, ResearchStore, iso_ms,
                    month_bounds_ym, normalize_archive_ts, step_ms)
from .universe import (SeriesSpec, build_universe, compact_exchange_info, latest_exchangeinfo, latest_onboard_dates, plan_series,
                       snapshot_doc, universe_json_snapshot, write_exchangeinfo_snapshot, write_universe_snapshot)

UTC = timezone.utc
DATA_STATUS_SCHEMA = "engine_data_status_v1"
DATA_RUN_SCHEMA = "engine_data_run_v1"
DUKA_STATUS_SCHEMA = "engine_dukascopy_import_v1"
USER_AGENT = "tradingbot-research-engine/1 (paper; read-only)"

ARCHIVE_HOST = "data.binance.vision"
ARCHIVE_BASE = f"https://{ARCHIVE_HOST}/data"
FAPI_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"
#: §3.4 bütçe tablosu
ENGINE_SAFETY = 0.1
HEADER_PAUSE_SHARE = 0.5
IP_LIMITS: dict[str, int] = dict(DEFAULT_WEIGHTS_PER_MINUTE)
WINDOW_S = 60.0
HALT_CODES = frozenset({418, 429, 403, 451})
#: arşiv (CDN) erişim engeli: "dosya yok" (404) DEĞİLDİR; arşiv adımı durur, kalıcı `.missing` yazılmaz
ARCHIVE_HALT_CODES = frozenset({403, 451})
#: REST `{"code": -1121, "msg": "Invalid symbol."}`: sembol borsada yok (delist)
INVALID_SYMBOL_CODE = -1121

#: §3.4 madde 2: worker koruması
WORKER_UNIT = "tradingbot-worker"
JOURNAL_SINCE = "-60min"
#: `market/http.py`: `log.warning("429 %s — %.0fs soğuma", url, ra)` ve `log.error("418 %s — IP yasağı %.0fs", url, ra)`
WORKER_RATE_LINE_RE = re.compile(r"\b(?P<code>429|418) (?P<url>https?://\S+) — (?:\d+s soğuma|IP yasağı \d+s)")
JOURNAL_UNREADABLE_RE = re.compile(r"(No journal files were (found|opened)|Permission denied|not seeing messages from "
                                   r"other users|Failed to )", re.IGNORECASE)
GUARD_OK, GUARD_SKIP, GUARD_UNKNOWN, GUARD_NOT_RUN = "REST_GUARD_OK", "REST_GUARD_SKIP", "REST_GUARD_UNKNOWN", "NOT_RUN"
#: koruma sonucunun REST sırasında en çok yaşı (sn); bayatlayınca günlük yeniden okunur (§3.4 madde 2)
GUARD_MAX_AGE_S = 300.0

RETRIES = 3
RETRY_BASE_S = 2.0
KEEP_404_DAYS = 10
UPDATE_LOOKBACK_DAYS = 62
#: `--update`: pencere DIŞINDAKİ açık dönemlerin (eksik/doğrulanmamış ay ya da gün) çalıştırma başına onarım sınırı
REPAIR_MAX_TASKS = 200
REST_LOOKBACK_MS = 2 * 86_400_000
STALE_AFTER_H = 26
DEADLINE_AFTER = timedelta(minutes=45)
DATA_DEADLINE_HM = (1, 26)
PUBLICATION_HOURS = frozenset({0, 4, 8, 12, 16, 20})
WINDOW_END_MINUTE = 35
PROGRESS_EVERY_S = 60.0
KEEP_DATA_RUNS = 60
KEEP_SEALS = 5
DEEP_SAMPLE_WEEKDAY = 6                          # Pazar: serilerin 1/7'si derin denetlenir (§3.2)
DAY_MS = 86_400_000
#: disk (madde 9): veri birimi gece biriminin `DISK_REFUSE` sınırlarından bu kadar ÖNCE durur
DATA_DISK_MARGIN = 3 * GB
DATA_MIN_FREE_BYTES = MIN_FREE_BYTES + DATA_DISK_MARGIN                  # boş < 13 GB → dur (gece: 10 GB)
DATA_REFUSE_RESEARCH_BYTES = REFUSE_RESEARCH_BYTES - DATA_DISK_MARGIN    # araştırma ≥ 17 GB → dur (gece: 20 GB)
DATA_WARN_RESEARCH_BYTES = WARN_RESEARCH_BYTES
DISK_REMEASURE_S = 1800.0
LISTING_KIND = "1d"
#: arşivdeki sürekli sözleşme başlangıcı (vadeli listelenme yoklamasının alt sınırı)
FUTURES_ARCHIVE_FLOOR_MS = 1_567_296_000_000     # 2019-09-01

MODE_UPDATE, MODE_BACKFILL, MODE_DUKA = "update", "backfill", "import_dukascopy"
R_SUCCESS, R_PARTIAL, R_DEADLINE, R_FAILED = "SUCCESS", "PARTIAL", "DEADLINE", "FAILED"
R_SKIPPED_LOCKED, R_ISOLATION_BROKEN, R_NOT_PAPER, R_DISK_REFUSE, R_ROOT_REFUSED = (
    SKIPPED_LOCKED, "ISOLATION_BROKEN", "NOT_PAPER", "DISK_REFUSE", "ROOT_REFUSED")
R_DUKA_DONE, R_DUKA_FAILED = "HAZIR", "YAPILAMADI"
R_STOPPED = "STOPPED"
EXIT_CODES = {R_SUCCESS: 0, R_PARTIAL: 0, R_DEADLINE: 0, R_SKIPPED_LOCKED: 0, R_FAILED: 1, R_STOPPED: 1, R_DUKA_FAILED: 2,
              R_DUKA_DONE: 0, R_ISOLATION_BROKEN: 3, R_NOT_PAPER: 4, R_DISK_REFUSE: 5, R_ROOT_REFUSED: 6}
S_OK, S_STALE, S_NO_BASELINE, S_HALTED, S_SKIPPED_DEADLINE = "OK", "STALE", "NO_BASELINE", "HALTED", "SKIPPED_DEADLINE"
#: delist (terminal, bayat sayılmaz) ve disk durması
S_DELISTED, S_SKIPPED_DISK = "DELISTED", "SKIPPED_DISK"
F_REST_GUARD_SKIP, F_REST_GUARD_UNKNOWN = GUARD_SKIP, GUARD_UNKNOWN
F_ARCHIVE_HOLES, F_DELISTED = "ARCHIVE_HOLES", "DELISTED"
#: dosya defteri durumları: doğrulandı / doğrulanamadı (CHECKSUM yok) / kalıcı eksik (404) / checksum tutmadı / kısmi ay
FS_VERIFIED, FS_UNVERIFIED, FS_MISSING, FS_BAD, FS_PARTIAL = "v", "u", "m", "x", "p"

#: işleme sırası: kaba türler önce (ilk doldurmada işe yarar veri erken görünür)
KIND_ORDER = {"1d": 0, "4h": 1, "1h": 2, FUNDING: 3, MARKPX_1H: 4, PREMIUM_1H: 5, "15m": 6, "5m": 7, METRICS_5M: 8,
              "1m": 9}


class TransientError(RuntimeError):
    """Geçici ağ hatası (zaman aşımı, bağlantı, 5xx): seri düzeyinde yeniden denenir."""


class RestHalted(RuntimeError):
    """418/429/403/451: REST adımı bu çalıştırmada TAMAMEN durur (§3.4)."""

    def __init__(self, status: int, url: str = ""):
        super().__init__(f"REST durdu: HTTP {status} {url[:120]}")
        self.status = int(status)


class RestError(RuntimeError):
    """Kalıcı REST hatası (4xx): yalnız o seri. `code` Binance hata kodudur (`-1121` = sembol yok/delist)."""

    def __init__(self, msg: str, status: int | None = None, code: int | None = None):
        super().__init__(msg)
        self.status, self.code = status, code


class RestGuardStop(RuntimeError):
    """Madde 2: göndermeden hemen önce yeniden okunan worker günlüğü koruması geçmedi; kalan REST bu çalıştırmada
    TAMAMEN atlanır (STALE değil: o gece yalnız arşiv)."""

    def __init__(self, status: str):
        super().__init__(f"REST koruması: {status}")
        self.status = status


class ArchiveHalted(RuntimeError):
    """Arşiv (CDN) 403/451: arşiv adımı bu çalıştırmada durur; kalıcı "eksik" işareti YAZILMAZ."""

    def __init__(self, status: int, url: str = ""):
        super().__init__(f"arşiv erişimi engellendi: HTTP {status} {url[:120]}")
        self.status = int(status)


class DeadlineReached(RuntimeError):
    """İç son tarih geçti: yeni seri başlatılmaz."""


class DiskRefused(RuntimeError):
    """Madde 9: boş alan ya da araştırma kökü veri biriminin sınırında; çalıştırma `DISK_REFUSE` ile durur."""

    def __init__(self, disk: dict):
        super().__init__("; ".join(disk.get("reasons") or []) or "disk sınırı")
        self.disk = disk


class StopRequested(BaseException):
    """SIGTERM (`systemctl stop`): `STOPPED` olarak kayda geçer (madde 10). BaseException: seri düzeyindeki
    `except Exception` blokları onu yutmaz."""


# ============================================================================ zaman
def compute_data_deadline(start: datetime) -> datetime:
    """`--update` iç son tarihi (madde 8): başlangıç + 45 dk; 00:00–01:26 arasında başladıysa en geç o günün 01:26'sı."""
    dl = start + DEADLINE_AFTER
    hh, mm = DATA_DEADLINE_HM
    cap = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start < cap:
        dl = min(dl, cap)
    return dl


def in_publication_window(dt: datetime) -> bool:
    """hh∈{00,04,08,12,16,20} için hh:00–hh:35 (UTC) — 4h endeks yayın penceresi (§2.2)."""
    d = dt.astimezone(UTC)
    return d.hour in PUBLICATION_HOURS and d.minute <= WINDOW_END_MINUTE


def window_resume_at(dt: datetime) -> datetime:
    d = dt.astimezone(UTC)
    return d.replace(minute=WINDOW_END_MINUTE + 1, second=0, microsecond=0)


def ms_of(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def dt_of(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def day_floor_ms(ms: int) -> int:
    return int(ms) - int(ms) % DAY_MS


def month_start_ms(ms: int) -> int:
    d = dt_of(ms)
    return ms_of(datetime(d.year, d.month, 1, tzinfo=UTC))


def next_month_ms(ms: int) -> int:
    d = dt_of(ms)
    return ms_of(datetime(d.year + (d.month == 12), (d.month % 12) + 1, 1, tzinfo=UTC))


def months_between(a_ms: int, b_ms: int) -> list[int]:
    """[a, b) aralığına değen ay başları."""
    out, cur = [], month_start_ms(a_ms)
    while cur < b_ms:
        out.append(cur)
        cur = next_month_ms(cur)
    return out


def ym_key(ms: int) -> str:
    d = dt_of(ms)
    return f"{d.year:04d}/{d.month:02d}"


def mstamp(ms: int) -> str:
    return dt_of(ms).strftime("%Y-%m")


def dstamp(ms: int) -> str:
    return dt_of(ms).strftime("%Y-%m-%d")


# ============================================================================ HTTP
@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


Http = Callable[[str, "Mapping[str, Any] | None", float], HttpResponse]


def urllib_http(url: str, params: Mapping[str, Any] | None = None, timeout: float = 30.0) -> HttpResponse:
    """Varsayılan HTTP GET (yalnız public uç noktalar; anahtar yok). Ağ/zaman aşımı → `TransientError`."""
    import urllib.error
    import urllib.parse
    import urllib.request
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return HttpResponse(int(r.status), {k: v for k, v in r.headers.items()}, r.read())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return HttpResponse(int(exc.code), {k: v for k, v in (exc.headers or {}).items()}, body)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise TransientError(f"GET {url[:160]}: {type(exc).__name__}: {exc}"[:300]) from exc


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    for k, v in (headers or {}).items():
        if str(k).lower() == name.lower():
            return v
    return None


# ============================================================================ arşiv aynası
@dataclass
class ArchiveFile:
    url: str
    status: str                       # ok | missing | mismatch
    data: bytes | None = None
    verified: bool = False
    sha: str | None = None
    from_cache: bool = False


class ArchiveMirror:
    """data.binance.vision dosyaları için `archive_cache/archive/<sunucu>/<yol>` önbelleği (madde 1)."""

    def __init__(self, paths: EnginePaths, http: Http, *, now_ms: Callable[[], int], gate: Callable[[], None],
                 keep_404_days: int = KEEP_404_DAYS, timeout: float = 60.0) -> None:
        self.paths = paths
        self.root = paths.archive_cache / "archive"
        self.http = http
        self.now_ms = now_ms
        self.gate = gate
        self.keep_404_days = int(keep_404_days)
        self.timeout = timeout
        self.stats = {"requests": 0, "bytes": 0, "ok": 0, "verified": 0, "unverified": 0, "missing": 0, "mismatch": 0,
                      "cache_hits": 0, "deleted_1m": 0}
        #: 403/451 sonrası bu çalıştırmada başka arşiv isteği YAPILMAZ (RestClient.halted gibi)
        self.halted: ArchiveHalted | None = None

    def path(self, url: str) -> Path:
        return self.root / url.split("://", 1)[-1].split("?", 1)[0]

    _STAMP = re.compile(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip$")

    def _period_end_ms(self, url: str) -> int | None:
        m = self._STAMP.search(url)
        if not m:
            return None
        s = m.group(1)
        if len(s) == 7:
            y, mo = (int(x) for x in s.split("-"))
            return ms_of(datetime(y + (mo == 12), (mo % 12) + 1, 1, tzinfo=UTC))
        y, mo, d = (int(x) for x in s.split("-"))
        return ms_of(datetime(y, mo, d, tzinfo=UTC)) + DAY_MS

    def _get(self, url: str) -> bytes | None:
        """200 → baytlar; YALNIZ 404 → None ("dosya yok"; laboratuvarlarla aynı: `signal_lab._http_get`,
        `gold_lab.ArchiveCache`). 403/451 → `ArchiveHalted` (erişim engeli; "yok" sayılmaz, kalıcı işaret yazılmaz);
        5xx/408/429 ve diğerleri → `TransientError`."""
        if self.halted is not None:
            raise self.halted
        self.gate()
        self.stats["requests"] += 1
        r = self.http(url, None, self.timeout)
        if r.status == 200:
            self.stats["bytes"] += len(r.body)
            return r.body
        if r.status == 404:
            return None
        if r.status in ARCHIVE_HALT_CODES:
            self.halted = ArchiveHalted(r.status, url)
            raise self.halted
        if r.status >= 500 or r.status in (408, 429):
            raise TransientError(f"arşiv HTTP {r.status}: {url}")
        raise TransientError(f"arşiv beklenmeyen HTTP {r.status}: {url}")

    def _write(self, p: Path, data: bytes) -> None:
        self.paths.write_bytes(p, data)

    @staticmethod
    def _expected(chk: bytes | None) -> str | None:
        if not chk:
            return None
        t = chk.decode("utf-8", "ignore").split()
        return t[0].strip().lower() if t and re.fullmatch(r"[0-9a-fA-F]{64}", t[0].strip()) else None

    def fetch(self, url: str) -> ArchiveFile:
        p = self.path(url)
        cp = p.with_name(p.name + ".CHECKSUM")
        miss = p.with_name(p.name + ".missing")
        if p.exists():
            with open(p, "rb") as fh:
                data = fh.read()
            self.stats["cache_hits"] += 1
            sha = hashlib.sha256(data).hexdigest()
            exp = None
            if cp.exists():
                with open(cp, "rb") as fh:
                    exp = self._expected(fh.read())
            if exp is None:
                raw = self._get(url + ".CHECKSUM")
                exp = self._expected(raw)
                if exp is not None:
                    self._write(cp, raw or b"")
            if exp is not None and exp != sha:
                # önbellekteki zip yayımlanan checksum'la tutmuyor: zip atılır, yeniden indirilir
                for x in (p, cp):
                    try:
                        os.unlink(self.paths.require_research(x))
                    except FileNotFoundError:
                        pass
                return self.fetch(url)
            ok = exp is not None
            self.stats["ok"] += 1
            self.stats["verified" if ok else "unverified"] += 1
            return ArchiveFile(url, "ok", data, ok, sha, True)
        if miss.exists():
            self.stats["missing"] += 1
            return ArchiveFile(url, "missing")
        data = self._get(url)
        if data is None:
            end = self._period_end_ms(url)
            if end is not None and end < self.now_ms() - self.keep_404_days * DAY_MS:
                self._write(miss, b"404\n")
            self.stats["missing"] += 1
            return ArchiveFile(url, "missing")
        raw = self._get(url + ".CHECKSUM")
        exp = self._expected(raw)
        sha = hashlib.sha256(data).hexdigest()
        if exp is not None and exp != sha:
            self.stats["mismatch"] += 1
            return ArchiveFile(url, "mismatch", None, False, sha)
        self._write(p, data)
        if exp is not None:
            self._write(cp, raw or b"")
        self.stats["ok"] += 1
        self.stats["verified" if exp else "unverified"] += 1
        return ArchiveFile(url, "ok", data, exp is not None, sha, False)

    def delete(self, url: str) -> None:
        """Doğrulanmış 1m zip'ini sil (checksum manifestte kalır)."""
        p = self.path(url)
        for x in (p, p.with_name(p.name + ".CHECKSUM")):
            try:
                os.unlink(self.paths.require_research(x))
            except FileNotFoundError:
                pass
        self.stats["deleted_1m"] += 1


def archive_url(spec: SeriesSpec, period: str) -> str:
    """`period` = 'YYYY-MM' (aylık) ya da 'YYYY-MM-DD' (günlük)."""
    kind = "monthly" if len(period) == 7 else "daily"
    seg = "spot" if spec.market == "spot" else "futures/um"
    s, ds = spec.symbol, spec.dataset
    if ds == "fundingRate":
        return f"{ARCHIVE_BASE}/{seg}/{kind}/fundingRate/{s}/{s}-fundingRate-{period}.zip"
    if ds == "metrics":
        return f"{ARCHIVE_BASE}/{seg}/{kind}/metrics/{s}/{s}-metrics-{period}.zip"
    iv = spec.interval
    return f"{ARCHIVE_BASE}/{seg}/{kind}/{ds}/{s}/{iv}/{s}-{iv}-{period}.zip"


# ============================================================================ ayrıştırma
def _zip_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise zipfile.BadZipFile("zip içinde dosya yok")
        return zf.read(names[0]).decode("utf-8-sig")


def _rows(text: str) -> tuple[list[str] | None, list[list[str]]]:
    rows = [r for r in csv.reader(io.StringIO(text)) if r and any(x.strip() for x in r)]
    if rows and not rows[0][0].strip().lstrip("-").isdigit() and not _looks_time(rows[0][0]):
        return [x.strip().lower() for x in rows[0]], rows[1:]
    return None, rows


def _looks_time(s: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", s.strip()))


def _f(x: str | None) -> float:
    if x is None:
        return float("nan")
    x = x.strip()
    if not x:
        return float("nan")
    return float(x)


def parse_kline_zip(data: bytes, market: str, period_start: int, period_end: int) -> pd.DataFrame:
    """Kline zip → `KLINE_COLS` (ms). Sayılar dizgeden `float()` ile (doğru yuvarlanmış; `signal_lab`'ın
    `astype(float)`'ıyla bayt-özdeş). µs→ms yoldan ve dönemden (`normalize_archive_ts`)."""
    _h, rows = _rows(_zip_text(data))
    rows = [r for r in rows if r[0].strip().isdigit()]
    ot, ct = normalize_archive_ts(market, period_start, period_end, [int(r[0]) for r in rows],
                                  [int(r[6]) if len(r) > 6 and r[6].strip() else 0 for r in rows])
    n = len(rows)

    def col(i: int) -> list[float]:
        return [_f(r[i]) if len(r) > i else float("nan") for r in rows]
    df = pd.DataFrame({"timestamp": pd.Series(ot, dtype="int64"), "open": col(1), "high": col(2), "low": col(3),
                       "close": col(4), "volume": col(5), "close_time": pd.Series(ct or [0] * n, dtype="int64"),
                       "quote_volume": col(7),
                       "trades": pd.Series([int(r[8]) if len(r) > 8 and r[8].strip() else 0 for r in rows], dtype="int64"),
                       "taker_buy_base": col(9), "taker_buy_quote": col(10)})
    return df.sort_values("timestamp", kind="mergesort").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def parse_px_zip(data: bytes, period_start: int, period_end: int) -> pd.DataFrame:
    """markPriceKlines / premiumIndexKlines zip → `PX_COLS`."""
    df = parse_kline_zip(data, "futures", period_start, period_end)
    return df[PX_COLS]


def parse_funding_zip(data: bytes, period_start: int, period_end: int) -> pd.DataFrame:
    """fundingRate zip → [timestamp (gerçek uzlaşma zamanı), rate, mark(NaN)]."""
    header, rows = _rows(_zip_text(data))
    if header is not None:
        ti = next((header.index(n) for n in ("calc_time", "funding_time", "fundingtime") if n in header), None)
        ri = next((header.index(n) for n in ("last_funding_rate", "funding_rate", "fundingrate") if n in header), None)
        if ti is None or ri is None:
            raise ValueError(f"fundingRate başlığı tanınmadı: {header}")
    else:
        ti, ri = 0, (len(rows[0]) - 1 if rows else 1)
    ts = [int(float(r[ti])) for r in rows]
    ts, _ = normalize_archive_ts("futures", period_start, period_end, ts, tolerance_ms=60_000)
    df = pd.DataFrame({"timestamp": pd.Series(ts, dtype="int64"), "rate": [_f(r[ri]) for r in rows],
                       "mark": [float("nan")] * len(rows)})
    return df.sort_values("timestamp", kind="mergesort").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


_METRICS_MAP = {"oi": "sum_open_interest", "oi_usdt": "sum_open_interest_value",
                "top_acct_ls": "count_toptrader_long_short_ratio", "top_pos_ls": "sum_toptrader_long_short_ratio",
                "global_ls": "count_long_short_ratio", "taker_ls_vol": "sum_taker_long_short_vol_ratio"}
_METRICS_POS = {"oi": 2, "oi_usdt": 3, "top_acct_ls": 4, "top_pos_ls": 5, "global_ls": 6, "taker_ls_vol": 7}


def _time_ms(s: str) -> int:
    s = s.strip().strip('"')
    if s.isdigit():
        v = int(s)
        return v // 1000 if v >= 10 ** 14 else (v * 1000 if v < 10 ** 11 else v)
    s = s.replace("T", " ").replace("Z", "")
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in s else "%Y-%m-%d %H:%M:%S"
    return ms_of(datetime.strptime(s, fmt).replace(tzinfo=UTC))


def parse_metrics_zip(data: bytes, day_start: int) -> pd.DataFrame:
    """daily/metrics zip → `METRICS_COLS` (5 dk; `create_time` UTC dizgesi). Gün dışı satır dosyayı bozuk sayar."""
    header, rows = _rows(_zip_text(data))
    if header is not None:
        if "create_time" not in header or "sum_open_interest" not in header:
            raise ValueError(f"metrics başlığı tanınmadı: {header}")
        ti = header.index("create_time")
        idx = {k: (header.index(v) if v in header else None) for k, v in _METRICS_MAP.items()}
    else:
        ti, idx = 0, dict(_METRICS_POS)
    ts = [_time_ms(r[ti]) for r in rows]
    if ts and (min(ts) < day_start or max(ts) > day_start + DAY_MS):
        raise ArchiveUnitError(f"metrics satırları gün dışında ({min(ts)}..{max(ts)})")
    out: dict[str, Any] = {"timestamp": pd.Series(ts, dtype="int64")}
    for k in METRICS_COLS[1:]:
        i = idx.get(k)
        out[k] = [(_f(r[i]) if i is not None and len(r) > i else float("nan")) for r in rows]
    df = pd.DataFrame(out)
    return df.sort_values("timestamp", kind="mergesort").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def parse_archive(spec: SeriesSpec, data: bytes, period_start: int, period_end: int) -> pd.DataFrame:
    if spec.dataset == "fundingRate":
        return parse_funding_zip(data, period_start, period_end)
    if spec.dataset == "metrics":
        return parse_metrics_zip(data, period_start)
    if spec.kind in (MARKPX_1H, PREMIUM_1H):
        return parse_px_zip(data, period_start, period_end)
    return parse_kline_zip(data, spec.market, period_start, period_end)


# ============================================================================ REST bütçesi ve istemci
def kline_weight(market: str, limit: int) -> int:
    if market == "spot":
        return 1 if limit <= 100 else 2 if limit <= 500 else 5 if limit <= 1000 else 10
    return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10


class EngineBudget:
    """Madde 4: motorun REST payı. `clock_s()` saniye, `sleep(s)` uyku (testler sahte saat verir)."""

    def __init__(self, *, clock_s: Callable[[], float], sleep: Callable[[float], None], safety: float = ENGINE_SAFETY,
                 limits: Mapping[str, int] | None = None) -> None:
        self.clock_s, self.sleep = clock_s, sleep
        self.safety = float(safety)
        self.limits = dict(limits or IP_LIMITS)
        self.pool = BudgetPool(safety=self.safety, clock=clock_s, sleeper=sleep)
        self._win: dict[str, deque] = {}
        self._hdr: dict[str, tuple[float, int]] = {}
        self.sent: list[tuple[str, float, int]] = []
        self.stats = {"weight": 0, "requests": 0, "window_waits": 0, "header_waits": 0, "max_header_used": 0}

    def limit(self, host: str) -> int:
        return int(self.limits.get(host.lower(), 1200))

    def cap(self, host: str) -> float:
        return self.limit(host) * self.safety

    def acquire(self, host: str, weight: int, gate: Callable[[], None] | None = None) -> None:
        """Payı ayır. `gate` (saat penceresi + son tarih + disk) her beklemeden SONRA yeniden çağrılır: bütçe beklemesi
        isteği 4h penceresine taşıyamaz. Kayan pencereye yazılan an GÖNDERİM anıdır (son kapı denetiminden sonra)."""
        host = host.lower()
        weight = max(1, int(weight))
        cap = self.cap(host)
        if weight > cap:
            raise ValueError(f"tek istek ağırlığı {weight} motor payını ({cap:.0f}) aşıyor")
        dq = self._win.setdefault(host, deque())
        while True:
            if gate is not None:
                gate()
            t = self.clock_s()
            hu = self._hdr.get(host)
            if hu and hu[1] >= HEADER_PAUSE_SHARE * self.limit(host):
                nxt = (int(hu[0] // 60) + 1) * 60.0
                if t < nxt:
                    self.stats["header_waits"] += 1
                    self.sleep(nxt - t + 1.0)
                    continue
                self._hdr.pop(host, None)
            while dq and dq[0][0] <= t - WINDOW_S:
                dq.popleft()
            used = sum(w for _, w in dq)
            if used + weight > cap:
                self.stats["window_waits"] += 1
                self.sleep(max(0.01, dq[0][0] + WINDOW_S - t + 0.01))
                continue
            break
        slept = self.pool.get(host, self.limit(host)).acquire(weight)
        if gate is not None and slept:
            gate()                                   # jeton kovası uyuttuysa pencere/son tarih yeniden
        t = self.clock_s()
        dq.append((t, weight))
        self.sent.append((host, t, weight))
        self.stats["weight"] += weight
        self.stats["requests"] += 1

    def on_response(self, host: str, headers: Mapping[str, str] | None) -> None:
        host = host.lower()
        self.pool.get(host, self.limit(host)).on_response(dict(headers or {}))
        raw = _header(headers, USED_WEIGHT_HEADER)
        try:
            used = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            used = None
        if used is not None:
            self._hdr[host] = (self.clock_s(), used)
            self.stats["max_header_used"] = max(self.stats["max_header_used"], used)

    def max_window(self, host: str) -> int:
        """Gönderilmiş isteklerin herhangi bir 60 sn penceresindeki en büyük toplam ağırlık (test ve durum için)."""
        xs = [(t, w) for h, t, w in self.sent if h == host.lower()]
        best, j, s = 0, 0, 0
        for i in range(len(xs)):
            s += xs[i][1]
            while xs[i][0] - xs[j][0] >= WINDOW_S:
                s -= xs[j][1]
                j += 1
            best = max(best, s)
        return best

    def to_dict(self) -> dict[str, Any]:
        hosts = sorted({h for h, _, _ in self.sent})
        return {**self.stats, "safety": self.safety, "caps": {h: self.cap(h) for h in hosts},
                "max_window_weight": {h: self.max_window(h) for h in hosts}}


class RestClient:
    def __init__(self, http: Http, budget: EngineBudget, *, gate: Callable[[], None], timeout: float = 15.0) -> None:
        self.http, self.budget, self.gate, self.timeout = http, budget, gate, timeout
        self.halted: RestHalted | None = None
        self.stats = {"requests": 0, "errors": 0}
        #: son isteğin GÖNDERİLDİĞİ an (ms): "yalnız kapanmış bar" bu ana göre uygulanır (madde 3)
        self.last_sent_ms: int | None = None
        #: göndermeden HEMEN önce (bütçe ve kapıdan sonra) çağrılır: worker günlüğü korumasının tazeliği (madde 2)
        self.pre_send: Callable[[], None] | None = None

    def get(self, base: str, path: str, params: Mapping[str, Any], weight: int) -> Any:
        if self.halted is not None:
            raise self.halted
        host = base.split("://", 1)[-1].split("/", 1)[0]
        self.budget.acquire(host, weight, gate=self.gate)
        if self.pre_send is not None:
            self.pre_send()
        url = base + path
        self.stats["requests"] += 1
        self.last_sent_ms = int(self.budget.clock_s() * 1000)
        r = self.http(url, {k: v for k, v in params.items() if v is not None}, self.timeout)
        self.budget.on_response(host, r.headers)
        if r.status == 200:
            try:
                return json.loads(r.body.decode("utf-8"))
            except ValueError as exc:
                raise TransientError(f"REST yanıtı JSON değil: {url}") from exc
        self.stats["errors"] += 1
        if r.status in HALT_CODES:
            self.halted = RestHalted(r.status, url)
            raise self.halted
        if r.status >= 500:
            raise TransientError(f"REST HTTP {r.status}: {url}")
        code = None
        try:
            j = json.loads((r.body or b"").decode("utf-8", "replace"))
            code = int(j.get("code")) if isinstance(j, dict) and j.get("code") is not None else None
        except (ValueError, TypeError):
            code = None
        raise RestError(f"REST HTTP {r.status}: {url}: {r.body[:160]!r}", status=r.status, code=code)


# ============================================================================ worker REST koruması
def check_worker_journal(runner: Callable[..., Any] = subprocess.run, *, unit: str = WORKER_UNIT,
                         since: str = JOURNAL_SINCE) -> dict[str, Any]:
    """Madde 2. Dönen `status`: REST_GUARD_OK | REST_GUARD_SKIP | REST_GUARD_UNKNOWN."""
    cmd = ["journalctl", "-u", unit, f"--since={since}", "--no-pager", "-o", "cat"]
    out: dict[str, Any] = {"cmd": " ".join(cmd), "status": GUARD_UNKNOWN, "hits": 0, "lines": 0, "codes": {}}
    try:
        cp = runner(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=60, check=False,
                    env={"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "SYSTEMD_PAGER": ""})
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        out["error"] = f"journalctl çalıştırılamadı: {type(exc).__name__}: {exc}"[:200]
        return out
    rc = getattr(cp, "returncode", None)
    text = getattr(cp, "stdout", "") or ""
    err = getattr(cp, "stderr", "") or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out["lines"] = len(lines)
    if rc != 0:
        out["error"] = f"journalctl çıkış {rc}: {err.strip()[:160]}"
        return out
    if JOURNAL_UNREADABLE_RE.search(err) or any(JOURNAL_UNREADABLE_RE.search(ln) for ln in lines[:3]):
        out["error"] = "günlük okunamıyor (yetki/ipucu satırı)"
        return out
    if not lines:
        out["error"] = "son 60 dakikada worker günlüğünde hiç satır yok (okuma doğrulanamadı)"
        return out
    codes: dict[str, int] = {}
    for ln in lines:
        m = WORKER_RATE_LINE_RE.search(ln)
        if m:
            codes[m.group("code")] = codes.get(m.group("code"), 0) + 1
    out["codes"] = codes
    out["hits"] = sum(codes.values())
    out["status"] = GUARD_SKIP if out["hits"] else GUARD_OK
    return out


# ============================================================================ Dukascopy içe alma
DUKA_SYMBOL = "XAUUSD"
DUKA_H_FILE, DUKA_D_FILE, DUKA_M_FILE = "BID_candles_hour_1.bi5", "BID_candles_day_1.bi5", "BID_candles_min_1.bi5"
DUKA_REC = struct.Struct(">5if")
DUKA_DIV = 1000.0
DUKA_PLAUSIBLE = (100.0, 20_000.0)


class DukaError(RuntimeError):
    """İçe alma dürüstçe tamamlanamaz ("yapılamadı")."""


def decode_bi5(data: bytes, start_ms: int, span_s: int, unit_s: int) -> list[tuple[int, float, float, float, float, float]]:
    """bi5 (LZMA alone) → [(ts_ms, açılış, yüksek, düşük, kapanış, hacim)]. Saniyeler [0, span) içinde, kesin artan ve
    `unit_s`'in katı olmalı (değilse ValueError). Boş dosya → boş liste."""
    if not data:
        return []
    raw = lzma.decompress(data, format=lzma.FORMAT_ALONE)
    if len(raw) % DUKA_REC.size:
        raise ValueError(f"bi5 uzunluğu {len(raw)} {DUKA_REC.size}'ün katı değil")
    out = []
    prev = -1
    for t, o, c, lo, h, v in DUKA_REC.iter_unpack(raw):
        if t < 0 or t >= span_s or t <= prev or t % unit_s:
            raise ValueError(f"bi5 kayıt saniyesi {t} geçersiz (aralık [0, {span_s}), kesin artan, {unit_s}'in katı)")
        prev = t
        out.append((int(start_ms) + t * 1000, o / DUKA_DIV, h / DUKA_DIV, lo / DUKA_DIV, c / DUKA_DIV, float(v)))
    return out


def clean_duka(rows: list[tuple]) -> tuple[list[tuple], dict[str, int]]:
    """Kapalı piyasa (hacim 0 VE düz) atılır; makul olmayan/tutarsız satır atılır ve sayılır; hacmi 0 hareketli tutulur."""
    keep, st = [], {"flat_closed": 0, "invalid": 0, "vol0_moving": 0}
    lo_ok, hi_ok = DUKA_PLAUSIBLE
    for r in rows:
        _ts, o, h, lo, c, v = r
        flat = o == h == lo == c
        if flat and v == 0:
            st["flat_closed"] += 1
            continue
        if not (lo <= min(o, c) and h >= max(o, c) and lo >= lo_ok and h <= hi_ok):
            st["invalid"] += 1
            continue
        if v == 0:
            st["vol0_moving"] += 1
        keep.append(r)
    return keep, st


def _days_in_month(y: int, m: int) -> int:
    a = datetime(y, m, 1, tzinfo=UTC)
    b = datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=UTC)
    return (b - a).days


def duka_files(root: Path) -> dict[str, list[tuple[Path, str, int, int, int]]]:
    """Aynadaki dosyalar: tür → [(yol, anahtar, başlangıç ms, aralık sn, birim sn)]."""
    base = root / DUKA_SYMBOL
    out: dict[str, list] = {"hour": [], "day": [], "min": []}
    if not base.is_dir():
        return out
    for yd in sorted(x for x in base.iterdir() if x.is_dir() and x.name.isdigit()):
        y = int(yd.name)
        df = yd / DUKA_D_FILE
        if df.is_file():
            out["day"].append((df, f"{y:04d}", ms_of(datetime(y, 1, 1, tzinfo=UTC)),
                               (366 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 365) * 86_400, 86_400))
        for md in sorted(x for x in yd.iterdir() if x.is_dir() and x.name.isdigit()):
            m0 = int(md.name)
            if not 0 <= m0 <= 11:
                continue
            hf = md / DUKA_H_FILE
            if hf.is_file():
                out["hour"].append((hf, f"{y:04d}-{m0 + 1:02d}", ms_of(datetime(y, m0 + 1, 1, tzinfo=UTC)),
                                    _days_in_month(y, m0 + 1) * 86_400, 3600))
            for dd in sorted(x for x in md.iterdir() if x.is_dir() and x.name.isdigit()):
                mf = dd / DUKA_M_FILE
                d = int(dd.name)
                if mf.is_file() and 1 <= d <= _days_in_month(y, m0 + 1):
                    out["min"].append((mf, f"{y:04d}-{m0 + 1:02d}-{d:02d}", ms_of(datetime(y, m0 + 1, d, tzinfo=UTC)),
                                       86_400, 60))
    return out


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_duka_manifest(root: Path) -> dict[str, dict[str, bool]] | None:
    p = root / "manifest.jsonl"
    if not p.is_file():
        return None
    ok: dict[str, dict[str, bool]] = {"hour": {}, "day": {}}
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("kind") in ok:
                good = r.get("status") == 200 and _positive(r.get("bytes"))
                k = str(r.get("key"))
                ok[r["kind"]][k] = ok[r["kind"]].get(k, False) or good
    return ok


def _positive(x: Any) -> bool:
    try:
        return float(x) > 0
    except (TypeError, ValueError):
        return False


def duka_status_path(paths: EnginePaths) -> Path:
    return paths.dukascopy / "import_status.json"


def read_duka_status(paths: EnginePaths) -> dict[str, Any]:
    d = _read_json(duka_status_path(paths))
    if isinstance(d, dict) and d.get("status") in (R_DUKA_DONE, R_DUKA_FAILED):
        return d
    return {"schema": DUKA_STATUS_SCHEMA, "status": R_DUKA_FAILED,
            "reason": "hazır ayna içe alınmadı (engine-data --import-dukascopy <dizin>); v1'de indirici yok (§3.3)"}


def import_dukascopy(paths: EnginePaths, src: Path | str, *, store: ResearchStore, now: datetime) -> dict[str, Any]:
    """Hazır Dukascopy aynasını içe al. Dönen sözlük `dukascopy/import_status.json`'a yazılır; başarısızlıkta önceki
    başarılı içe alma (varsa) yerinde kalır ve `last_attempt` olarak not edilir."""
    root = Path(src)
    prev = read_duka_status(paths)
    att: dict[str, Any] = {"schema": DUKA_STATUS_SCHEMA, "source_dir": str(root), "at": now.isoformat()}
    stage = paths.dukascopy / f".staging-{now.strftime('%Y%m%dT%H%M%SZ')}"
    try:
        doc = _duka_import(paths, root, stage, store=store, now=now)
        doc.update(att, status=R_DUKA_DONE)
        paths.write_json(duka_status_path(paths), doc)
        return doc
    except DukaError as exc:
        att.update(status=R_DUKA_FAILED, reason=str(exc)[:500])
    except Exception as exc:  # noqa: BLE001 — beklenmeyen hata da "yapılamadı"dır (sahte veri yok)
        att.update(status=R_DUKA_FAILED, reason=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        if stage.exists():
            shutil.rmtree(paths.require_research(stage), ignore_errors=True)
    out = dict(prev) if prev.get("status") == R_DUKA_DONE else dict(att)
    out["last_attempt"] = att
    paths.write_json(duka_status_path(paths), out)
    return {**out, "status": att["status"], "reason": att.get("reason")}


def _duka_import(paths: EnginePaths, root: Path, stage: Path, *, store: ResearchStore, now: datetime) -> dict[str, Any]:
    if not root.is_dir():
        raise DukaError(f"ayna klasörü yok: {root}")
    if not (root / DUKA_SYMBOL).is_dir():
        raise DukaError(f"aynada {DUKA_SYMBOL}/ yok: {root}")
    parts_left = sorted(str(p.relative_to(root)) for p in (root / DUKA_SYMBOL).rglob("*.part"))
    if parts_left:
        raise DukaError(f"ayna işi bitmemiş: {len(parts_left)} .part dosyası var (ör. {parts_left[0]})")
    files = duka_files(root)
    if not files["hour"]:
        raise DukaError("aynada saatlik dosya (BID_candles_hour_1.bi5) yok")
    man = _read_duka_manifest(root)
    allf = [x for k in ("hour", "day", "min") for x in files[k]]
    sha1 = {str(p): _sha_file(p) for p, *_ in allf}
    # 1) bayt-özdeş kopya + 2) kayıt denetimi (depoya henüz hiçbir şey yazılmaz)
    stats: dict[str, Any] = {"hour": {"files": 0, "records": 0, "kept": 0, "flat_closed": 0, "invalid": 0,
                                      "vol0_moving": 0, "manifest_missing": 0},
                             "day": {"files": 0, "records": 0, "manifest_missing": 0},
                             "min": {"files": 0, "records": 0, "kept": 0, "flat_closed": 0, "invalid": 0,
                                     "vol0_moving": 0}}
    lines: list[dict[str, Any]] = []
    for kind in ("hour", "day", "min"):
        for p, key, start, span, unit in files[kind]:
            rel = p.relative_to(root)
            with open(p, "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != sha1[str(p)]:
                raise DukaError(f"ayna içe alma sırasında değişti: {rel}")
            try:
                recs = decode_bi5(data, start, span, unit)
            except (lzma.LZMAError, EOFError, ValueError) as exc:
                raise DukaError(f"{rel} kayıt denetimini geçmedi: {type(exc).__name__}: {exc}") from exc
            paths.write_bytes(stage / rel, data)
            st = stats[kind]
            st["files"] += 1
            st["records"] += len(recs)
            if kind in ("hour", "day") and man is not None and not (man.get(kind) or {}).get(key):
                st["manifest_missing"] += 1
            line = {"kind": kind, "key": key, "rel": rel.as_posix(), "sha256": sha1[str(p)], "bytes": len(data),
                    "records": len(recs)}
            if kind in ("hour", "min"):
                kept, cst = clean_duka(recs)
                for k2, v2 in cst.items():
                    st[k2] += v2
                st["kept"] += len(kept)
                line.update(kept=len(kept), **cst)
            lines.append(line)
    # 3) kaynak baştan sona aynı mı (ikinci sha256)
    changed = [str(Path(k).relative_to(root)) for k, v in sha1.items() if _sha_file(Path(k)) != v]
    if changed:
        raise DukaError(f"ayna içe alma sırasında değişti: {len(changed)} dosya (ör. {changed[0]})")
    # 4) depo satırları ÖNCE geçici bir depoda kurulur (mevcut seri kopyalanır, aynı öncelik/birleştirme kurallarıyla
    #    yazılır): bir yazım hatası asıl depoya HİÇBİR satır bırakmaz (2026-10-06 düzeltmesi; önceden yerine koyma
    #    sonrası yazım yarıda kalırsa önceki HAZIR içe almanın yanında yarım satırlar kalabiliyordu)
    sroot = stage / ".store"
    sst = ResearchStore(sroot, provider=store.provider, clock_ms=store.clock_ms, guard=paths.require_research)
    tfs = (DUKA_1H, DUKA_1M)
    for tf in tfs:
        cur = store.series_dir("dukascopy", DUKA_SYMBOL, tf)
        if cur.is_dir():
            shutil.copytree(cur, paths.require_research(sst.series_dir("dukascopy", DUKA_SYMBOL, tf)))
    rows_written = {DUKA_1H: 0, DUKA_1M: 0}
    staged = duka_files(stage)
    now_ms = ms_of(now)
    for kind, tf, unit_files in (("hour", DUKA_1H, staged["hour"]), ("min", DUKA_1M, staged["min"])):
        by_month: dict[str, list[tuple]] = {}
        for p, key, start, span, unit in unit_files:
            with open(p, "rb") as fh:
                kept, _ = clean_duka(decode_bi5(fh.read(), start, span, unit))
            by_month.setdefault(key[:7], []).extend(kept)
            if kind == "hour" or len(by_month) > 1:
                for mk in sorted(by_month)[:-1] if kind == "min" else sorted(by_month):
                    rows_written[tf] += _duka_write(sst, tf, by_month.pop(mk), now_ms)
        for mk in sorted(by_month):
            rows_written[tf] += _duka_write(sst, tf, by_month.pop(mk), now_ms)
    # 5) yerine koy (yalnız yeniden adlandırma; eski kopya ancak yenisi yerindeyken kaldırılır)
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    dest = paths.dukascopy / DUKA_SYMBOL
    old = paths.dukascopy / f".old-{stamp}"
    if dest.exists():
        os.replace(paths.require_research(dest), paths.require_research(old))
    os.replace(paths.require_research(stage / DUKA_SYMBOL), paths.require_research(dest))
    if old.exists():
        shutil.rmtree(paths.require_research(old), ignore_errors=True)
    for tf in tfs:
        new_dir = sst.series_dir("dukascopy", DUKA_SYMBOL, tf)
        if not new_dir.is_dir():
            continue
        cur = store.series_dir("dukascopy", DUKA_SYMBOL, tf)
        cur.parent.mkdir(parents=True, exist_ok=True)
        prev = cur.with_name(f".{tf}.old-{stamp}")
        if cur.exists():
            os.replace(paths.require_research(cur), paths.require_research(prev))
        os.replace(paths.require_research(new_dir), paths.require_research(cur))
        if prev.exists():
            shutil.rmtree(paths.require_research(prev), ignore_errors=True)
    paths.write_bytes(paths.dukascopy / "manifest.jsonl", "".join(json_line({**ln, "imported_at": now.isoformat()})
                                                                  for ln in lines).encode("utf-8"))
    years: dict[str, dict[str, Any]] = {}
    for ln in lines:
        if ln["kind"] == "hour":
            y = ln["key"][:4]
            yr = years.setdefault(y, {"months": 0, "kept_hours": 0})
            yr["months"] += 1
            yr["kept_hours"] += int(ln.get("kept") or 0)
    return {"imported_at": now.isoformat(), "files": {k: stats[k]["files"] for k in stats}, "stats": stats,
            "manifest_present": man is not None, "rows_new": rows_written, "years": years,
            "store_series": [f"dukascopy/{DUKA_SYMBOL}/{DUKA_1H}"] + ([f"dukascopy/{DUKA_SYMBOL}/{DUKA_1M}"]
                                                                      if files["min"] else []),
            "src": SRC_UNVERIFIED, "files_digest": hashlib.sha256("".join(f"{ln['rel']}|{ln['sha256']}\n" for ln in lines)
                                                                  .encode("utf-8")).hexdigest(),
            "note_tr": "Dukascopy kaynağının yayımlanmış checksum'ı yok: satırlar archive_unverified (doğrulanmış "
                       "sayımlara ve pariteye girmez); kayıt denetimi ve iki geçişli sha256 ile bayt-özdeş kopya."}


def _duka_write(store: ResearchStore, tf: str, rows: list[tuple], now_ms: int) -> int:
    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume_proxy"])
    df["timestamp"] = df["timestamp"].astype("int64")
    return int(store.write("dukascopy", DUKA_SYMBOL, tf, df, src=SRC_UNVERIFIED, now_ms=now_ms,
                           chunk_id="dukascopy-mirror")["rows_new"])


# ============================================================================ yardımcılar
def _read_json(p: Path) -> Any:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def stable_bucket(key: str, n: int = 7) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % n


# ============================================================================ veri çalıştırması
@dataclass
class Task:
    period: str                        # YYYY-MM | YYYY-MM-DD
    start: int
    end: int
    refetch: bool = False
    recheck: bool = False
    done: bool = False
    failed: bool = False               # kalıcı hata (checksum / bozuk / denemeler tükendi): `holes`'a sayılır
    at: str = ""                       # doğrulanmamış dosyanın son deneme anı (onarım sırası)


class DataRun:
    """`--update` / `--backfill` / `--import-dukascopy` çalıştırması. Bütün dış dünya enjekte edilebilir: `clock`
    (aware datetime), `sleep`, `http`, `runner` (journalctl), `probes` (öz-denetim), `rng` (titreşim)."""

    def __init__(self, paths: EnginePaths, *, mode: str, clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] | None = None, http: Http | None = None,
                 runner: Callable[..., Any] | None = None, probes: SC.Probes | None = None,
                 env: Mapping[str, str] | None = None, app_dir: Path | str = SC.APP_DIR,
                 seed_root: Path | str | None = None, free_bytes: int | Callable[[], int] | None = None,
                 rng: random.Random | None = None,
                 dukascopy_dir: Path | str | None = None, seed_hook: Callable[[Path, int], None] | None = None,
                 progress_every_s: float = PROGRESS_EVERY_S,
                 plan_filter: Callable[[SeriesSpec], bool] | None = None) -> None:
        self.paths, self.mode = paths, mode
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep or time.sleep
        self.http = http or urllib_http
        self.runner = runner or subprocess.run
        self.probes = probes
        self.env = os.environ if env is None else env
        self.app_dir = Path(app_dir)
        self.seed_root = Path(seed_root) if seed_root is not None else paths.worker_history
        #: boş alan: None → `statvfs`; sayı ya da çağrılabilir (yalnız test/sahte VPS)
        self.free_bytes = free_bytes
        self.rng = rng or random.Random()
        self.dukascopy_dir = dukascopy_dir
        self.seed_hook = seed_hook
        self.progress_every_s = float(progress_every_s)
        #: yalnız test/sahte VPS: planı süzer (üretim yolu `engine-data` bunu hiç vermez)
        self.plan_filter = plan_filter
        self.start = self.clock()
        self.mono0 = time.monotonic()
        self.deadline = compute_data_deadline(self.start) if mode == MODE_UPDATE else None
        self.store = ResearchStore.for_paths(paths, clock_ms=lambda: ms_of(self.clock()))
        self.budget = EngineBudget(clock_s=lambda: self.clock().timestamp(), sleep=self.sleep)
        self.rest = RestClient(self.http, self.budget, gate=self.gate)
        self.rest.pre_send = self._rest_guard_gate
        self.mirror = ArchiveMirror(paths, self.http, now_ms=lambda: ms_of(self.clock()), gate=self.gate)
        self.run_id: str | None = None
        self.flags: set[str] = set()
        self.series: dict[str, dict[str, Any]] = {}
        self.night = self.start.strftime("%Y-%m-%d")
        self.progress: dict[str, Any] = {"phase": "start", "done": 0, "total": 0, "paused": False}
        self._done_times: deque = deque()
        self._last_progress = 0.0
        self.prev = _read_json(paths.data_status) if paths.data_status.exists() else None
        cache = _read_json(self._listings_path())
        self.listings: dict[str, int | None] = ({k: (v or {}).get("listing_ms") for k, v in cache.items()}
                                                if isinstance(cache, dict) else {})
        self.plan_keys: set[str] = set()
        #: REST koruması (madde 2): son okumanın sonucu ve anı (sn)
        self._guard_status: str = GUARD_NOT_RUN
        self._guard_at: float | None = None
        #: delist (terminal) semboller: "market/SEMBOL" → {since_ms, source, at}
        self.delisted: dict[str, dict[str, Any]] = self._load_delisted()
        #: seri → kalan açık dönem sayısı (onarılamadı ya da sınır)
        self.holes: dict[str, int] = {}
        #: disk (madde 9): son ölçüm (araştırma baytı, boş bayt, an) ve son durum
        self._disk_base: tuple[int, int | None, float] | None = None
        self._in_run = False
        self.status: dict[str, Any] = {"schema": DATA_RUN_SCHEMA, "engine": ENGINE_VERSION, "mode": mode,
                                       "run_id": None, "started_at": self.start.isoformat(), "finished_at": None,
                                       "deadline": self.deadline.isoformat() if self.deadline else None,
                                       "result": "RUNNING", "exit_code": None, "pid": os.getpid(), "flags": [],
                                       "selfcheck": {}, "rest": {"guard": GUARD_NOT_RUN}, "archive": {}, "seed": {},
                                       "recovery": {}, "diffs": {"count": 0}, "universe": {}, "exchangeinfo": {}}

    # ---------------------------------------------------------------- saat, pencere, son tarih, disk
    def gate(self) -> None:
        """Her ağ isteğinden önce (madde 8–9): son tarih geçtiyse `DeadlineReached`; disk veri biriminin sınırındaysa
        `DiskRefused`; 4h penceresindeyse beklenir (beklemeden sonra hepsi yeniden denetlenir)."""
        while True:
            now = self.clock()
            if self.deadline is not None and now > self.deadline:
                raise DeadlineReached(f"iç son tarih {self.deadline.isoformat()} geçti")
            self.disk_check()
            if not self._pause_if_window(now):
                return

    def pause_window(self) -> None:
        """Ağsız ağır adımlar (kurtarma, tohum) için: 4h penceresindeyse bitene kadar bekle (son tarih denetimi yok)."""
        while self._pause_if_window(self.clock()):
            pass

    def _pause_if_window(self, now: datetime) -> bool:
        if not in_publication_window(now):
            if self.progress.get("paused"):
                self.progress.update(paused=False, pause_reason=None, resume_at=None)
            return False
        resume = window_resume_at(now)
        self.progress.update(paused=True, pause_reason="PAUSED_4H_WINDOW", resume_at=resume.isoformat())
        self.flags.add("PAUSED_4H_WINDOW")
        self.write_status(final=False)
        self.sleep(max(1.0, (resume - now).total_seconds()))
        return True

    def _free_now(self) -> int | None:
        fb = self.free_bytes
        if callable(fb):
            return int(fb())
        if fb is not None:
            return int(fb)
        probe = self.paths.research if self.paths.research.exists() else self.paths.data
        try:
            st = os.statvfs(probe)
            return int(st.f_bavail * st.f_frsize)
        except (OSError, AttributeError):
            return None

    def disk_view(self, *, remeasure: bool = False) -> dict[str, Any]:
        """Madde 9: boş alan (ucuz `statvfs`) + araştırma kökü tahmini = son ölçüm + o andan beri boş alandaki düşüş
        (başkasının tükettiği alan da araştırmaya yazılır: muhafazakâr); `DISK_REMEASURE_S`'de bir yeniden ölçülür."""
        t = self.clock().timestamp()
        free = self._free_now()
        if remeasure or self._disk_base is None or t - self._disk_base[2] >= DISK_REMEASURE_S:
            self._disk_base = (dir_size_bytes(self.paths.research), free, t)
        used0, free0, _ = self._disk_base
        used = used0 + (max(0, int(free0) - int(free)) if (free0 is not None and free is not None) else 0)
        status, reasons = DISK_OK, []
        if used >= DATA_REFUSE_RESEARCH_BYTES:
            status = DISK_REFUSE
            reasons.append(f"araştırma kökü ≈ {used / GB:.2f} GB ≥ {DATA_REFUSE_RESEARCH_BYTES / GB:.0f} GB (veri birimi "
                           f"sınırı; gece birimi {REFUSE_RESEARCH_BYTES / GB:.0f} GB'ta reddeder)")
        elif used >= DATA_WARN_RESEARCH_BYTES:
            status = DISK_WARN
            reasons.append(f"araştırma kökü ≈ {used / GB:.2f} GB ≥ {DATA_WARN_RESEARCH_BYTES / GB:.0f} GB (uyarı)")
        if free is not None and free < DATA_MIN_FREE_BYTES:
            status = DISK_REFUSE
            reasons.append(f"boş disk {free / GB:.2f} GB < {DATA_MIN_FREE_BYTES / GB:.0f} GB (veri birimi sınırı; gece "
                           f"birimi {MIN_FREE_BYTES / GB:.0f} GB'ta reddeder)")
        return {"status": status, "research_bytes": int(used), "free_bytes": free,
                "refuse_research_bytes": DATA_REFUSE_RESEARCH_BYTES, "min_free_bytes": DATA_MIN_FREE_BYTES,
                "night_refuse_research_bytes": REFUSE_RESEARCH_BYTES, "night_min_free_bytes": MIN_FREE_BYTES,
                "reasons": reasons, "at": self.clock().isoformat()}

    def disk_check(self) -> None:
        d = self.disk_view()
        self.progress["disk"] = {k: d[k] for k in ("status", "research_bytes", "free_bytes", "at")}
        if d["status"] == DISK_WARN:
            self.flags.add("DISK_WARN")
        if d["status"] == DISK_REFUSE:
            raise DiskRefused(d)

    def tick(self, n: int = 1) -> None:
        """İlerleme: bir görev bitti (madde 7, `done/total`, son 1 saatin hızı, tahmini bitiş)."""
        t = self.clock().timestamp()
        self.progress["done"] = int(self.progress.get("done", 0)) + n
        for _ in range(n):
            self._done_times.append(t)
        while self._done_times and self._done_times[0] < t - 3600:
            self._done_times.popleft()
        if t - self._last_progress >= self.progress_every_s:
            self._last_progress = t
            self.write_status(final=False)

    def _progress_view(self) -> dict[str, Any]:
        p = dict(self.progress)
        t = self.clock().timestamp()
        recent = [x for x in self._done_times if x >= t - 3600]
        span = (t - recent[0]) if recent else 0.0
        rate = (len(recent) / span * 3600.0) if span > 0 else None
        left = max(0, int(p.get("total", 0)) - int(p.get("done", 0)))
        p["rate_per_h_1h"] = round(rate, 1) if rate else None
        p["remaining"] = left
        p["eta"] = (self.clock() + timedelta(hours=left / rate)).isoformat() if rate and left else (
            self.clock().isoformat() if not left and p.get("total") else None)
        return p

    # ---------------------------------------------------------------- durum dosyası
    def _series_table(self, plan_keys: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        now_ms = ms_of(self.clock())
        table: dict[str, Any] = {}
        tot = {"series": 0, "planned": 0, "rows": 0, "unverified_rows": 0, "rest_rows": 0, "seed_rows": 0,
               "archive_rows": 0, "stale": 0, "halted": 0, "no_baseline": 0, "delisted": 0, "holes": 0}
        keys = sorted(set(plan_keys) | {self.store.series_key(m, s, t) for m, s, t in self.store.series()})
        for key in keys:
            market, sym, tf = key.split("/", 2)
            m = self.store.manifest(market, sym, tf)
            run = self.series.get(key, {})
            step = step_ms(tf)
            last_close = None
            if m.last_ts_ms is not None:
                last_close = m.last_ts_ms + (step if (step and tf != METRICS_5M) else 0)
            age_h = round((now_ms - last_close) / 3_600_000, 2) if last_close is not None else None
            historical = market == "dukascopy"
            dl = self.delisted.get(f"{market}/{sym}")
            st = run.get("status")
            if m.status == SERIES_HALTED:
                st = S_HALTED
            elif dl is not None and st in (None, S_OK, S_DELISTED, S_NO_BASELINE):
                st = S_DELISTED                      # terminal: veri delist anına kadar; bayat sayılmaz (madde 3)
            elif not m.row_count:
                st = st or S_NO_BASELINE
            elif st in (None, S_OK):
                st = S_STALE if (not historical and age_h is not None and age_h > STALE_AFTER_H) else (st or S_OK)
            stale = st in (S_STALE, S_HALTED, S_NO_BASELINE, S_SKIPPED_DEADLINE, S_SKIPPED_DISK)
            src = dict(m.src_rows or {})
            row = {"planned": key in plan_keys, "status": st, "stale": bool(stale and not historical),
                   "last_ts": iso_ms(m.last_ts_ms), "last_ts_ms": m.last_ts_ms, "age_h": age_h, "rows": m.row_count,
                   "first_ts": iso_ms(m.first_ts_ms), "gaps": m.gap_count, "quality": m.quality_score,
                   "unverified_rows": int(src.get(SRC_UNVERIFIED, 0)), "src": src, "bad_chunks": len(m.bad_chunks),
                   "refetch": list(m.refetch), "series_seal": m.series_seal}
            if tf == FUNDING:
                row["interval_ms"] = m.interval_ms
            if m.halted_reason:
                row["halted_reason"] = m.halted_reason
            if dl is not None:
                row["delisted"] = dl
            if self.holes.get(key):
                row["holes"] = int(self.holes[key])
            for k in ("error", "rows_new", "rest_rows", "archive_files", "rest_skipped"):
                if run.get(k):
                    row[k] = run[k]
            table[key] = row
            tot["series"] += 1
            tot["planned"] += int(row["planned"])
            tot["rows"] += int(m.row_count or 0)
            tot["unverified_rows"] += row["unverified_rows"]
            tot["rest_rows"] += int(src.get(SRC_REST, 0))
            tot["seed_rows"] += int(src.get("seed", 0))
            tot["archive_rows"] += int(src.get(SRC_ARCHIVE, 0))
            tot["stale"] += int(row["stale"])
            tot["halted"] += int(st == S_HALTED)
            tot["no_baseline"] += int(st == S_NO_BASELINE)
            tot["delisted"] += int(st == S_DELISTED)
            tot["holes"] += int(row.get("holes") or 0)
        return table, tot

    def write_status(self, *, final: bool, seal: tuple[str, str] | None = None) -> None:
        """`summary/data_status.json`. Mühür alanları yalnız TAMAMLANAN çalıştırmada değişir (madde 7)."""
        self.status["flags"] = sorted(self.flags)
        if self.run_id is None:
            return
        prev = self.prev if isinstance(self.prev, dict) else {}
        plan_keys = set(self.plan_keys)
        table, tot = self._series_table(plan_keys)
        doc: dict[str, Any] = {"schema": DATA_STATUS_SCHEMA, "engine": ENGINE_VERSION, "updated_at": self.clock().isoformat(),
                               "data_seal": prev.get("data_seal"), "seal_file": prev.get("seal_file"),
                               "sealed_at": prev.get("sealed_at"), "sealed_run_id": prev.get("sealed_run_id"),
                               "sealed_parts": prev.get("sealed_parts")}
        if seal is not None:
            doc.update(data_seal=seal[0], seal_file=seal[1], sealed_at=self.clock().isoformat(), sealed_run_id=self.run_id,
                       sealed_parts=self.status.get("sealed_parts"))
        last = {k: self.status.get(k) for k in ("run_id", "mode", "started_at", "finished_at", "result", "exit_code",
                                                "deadline", "flags", "rest", "archive", "seed", "recovery", "diffs",
                                                "universe", "exchangeinfo", "selfcheck_status", "disk", "budget")}
        if final:
            doc["last_run"] = last
            doc["running"] = None
        else:
            doc["last_run"] = prev.get("last_run")
            doc["running"] = {**last, "pid": os.getpid(), "progress": self._progress_view()}
        doc["series"] = table
        doc["totals"] = tot
        doc["delisted"] = dict(sorted(self.delisted.items()))
        doc["dukascopy"] = read_duka_status(self.paths)
        self.paths.write_json(self.paths.data_status, doc)
        self.prev = doc if final else prev
        self.paths.write_json(self.paths.data_runs / self.run_id / "data_run.json",
                              {**self.status, "progress": self._progress_view()})

    def _new_run_id(self) -> str:
        base = self.start.strftime("%Y%m%dT%H%M%SZ")
        rid, n = base, 1
        root = self.paths.data_runs
        root.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.paths.require_research(root / rid).mkdir(parents=False, exist_ok=False)
                return rid
            except FileExistsError:
                n += 1
                rid = f"{base}-{n}"

    def _prune_runs(self) -> None:
        root = self.paths.data_runs
        if not root.is_dir():
            return
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and re.match(r"^\d{8}T\d{6}Z(-\d+)?$", p.name))
        for p in dirs[:-KEEP_DATA_RUNS]:
            shutil.rmtree(self.paths.require_research(p), ignore_errors=True)

    def finish(self, result: str) -> dict[str, Any]:
        self.status["result"] = result
        self.status["exit_code"] = EXIT_CODES.get(result, 1)
        self.status["finished_at"] = self.clock().isoformat()
        if self.run_id is None:                       # root reddi / araştırma kökü yazılamıyor: HİÇBİR şey yazılmaz
            return self.status
        self.status["budget"] = self.budget.to_dict()
        self.status["wall_s"] = round(time.monotonic() - self.mono0, 3)
        try:
            from .night import resources
            self.status["resources"] = resources({"self": 0.0, "children": 0.0, "maxrss_bytes": 0.0}, self.mono0)
        except Exception:  # noqa: BLE001
            pass
        seal = None
        sealable = (R_SUCCESS, R_PARTIAL, R_DEADLINE, R_DUKA_DONE, R_DUKA_FAILED) + ((R_DISK_REFUSE,) if self._in_run else ())
        if result in sealable and self.run_id is not None:   # disk durması: yazılmış veri tutarlıdır, mühürlenir
            try:
                seal = self._seal()
            except Exception as exc:  # noqa: BLE001 — mühür yazılamazsa önceki mühür kalır
                self.flags.add("SEAL_FAILED")
                self.status["seal_error"] = _err(exc)
        if result == R_SKIPPED_LOCKED:
            if self.run_id is not None:
                self.paths.write_json(self.paths.data_runs / self.run_id / "data_run.json", self.status)
            return self.status
        try:
            self.write_status(final=True, seal=seal)
        except OSError as exc:
            print(f"engine-data: data_status yazılamadı: {exc}", file=sys.stderr)
        self._prune_runs()
        return self.status

    def _seal(self) -> tuple[str, str]:
        seal, listing = self.store.data_seal()
        rel = f"store/_seal/{seal}.json.gz"
        p = self.paths.research / rel
        if not p.exists():
            self.paths.write_json_gz(p, {"schema": "engine_data_seal_v1", "data_seal": seal, "run_id": self.run_id,
                                         "at": self.clock().isoformat(), "parts": [list(x) for x in listing]})
        self.status["sealed_parts"] = len(listing)
        sd = p.parent
        olds = sorted((x for x in sd.glob("*.json.gz") if x != p), key=lambda x: x.stat().st_mtime)
        for x in olds[:-(KEEP_SEALS - 1)] if KEEP_SEALS > 1 else olds:
            try:
                os.unlink(self.paths.require_research(x))
            except OSError:
                pass
        return seal, rel

    # ---------------------------------------------------------------- S0 benzeri ön kontrol
    def preflight(self) -> str | None:
        why = root_refusal(self.paths)
        if why:
            print(f"engine-data: {why.replace('engine-night', 'engine-data')}", file=sys.stderr)
            self.status.update(error=why)
            return R_ROOT_REFUSED
        paper = SC.check_paper(self.paths.state, self.env)
        self.status["selfcheck"]["paper"] = paper
        try:
            self.run_id = self._new_run_id()
        except OSError as exc:
            self.status["selfcheck"]["isolation"] = {"status": SC.ISOLATION_BROKEN, "broken": ["writable_research"],
                                                     "error": str(exc)[:200]}
            return R_ISOLATION_BROKEN
        self.status["run_id"] = self.run_id
        if paper["status"] != SC.OK:
            return R_NOT_PAPER
        return None

    def checks(self) -> str | None:
        # madde 9: veri biriminin KENDİ (daha dar) sınırları: gece biriminin reddinden 3 GB önce
        disk = disk_guard(self.paths, warn_bytes=DATA_WARN_RESEARCH_BYTES, refuse_bytes=DATA_REFUSE_RESEARCH_BYTES,
                          min_free_bytes=DATA_MIN_FREE_BYTES, free_bytes=self._free_now())
        self._disk_base = (int(disk["research_bytes"]), disk.get("free_bytes"), self.clock().timestamp())
        disk["night_refuse_research_bytes"], disk["night_min_free_bytes"] = REFUSE_RESEARCH_BYTES, MIN_FREE_BYTES
        self.status["disk"] = disk
        if disk["status"] == DISK_REFUSE:
            return R_DISK_REFUSE
        if disk["status"] == DISK_WARN:
            self.flags.add("DISK_WARN")
        iso_r = SC.run_isolation(self.paths, app_dir=self.app_dir, env=self.env, probes=self.probes,
                                 expect_no_network=False)
        self.status["selfcheck"]["isolation"] = iso_r
        self.status["selfcheck_status"] = iso_r["status"]
        if iso_r["status"] != SC.OK:
            return R_ISOLATION_BROKEN
        return None

    # ---------------------------------------------------------------- ana akış
    def run(self) -> dict[str, Any]:
        stop = self.preflight()
        if stop:
            return self.finish(stop)
        lk = data_lock(self.paths)
        try:
            got = lk.try_acquire()
        except OSError as exc:
            self.status["selfcheck"]["isolation"] = {"status": SC.ISOLATION_BROKEN, "broken": ["writable_research"],
                                                     "error": str(exc)[:200]}
            return self.finish(R_ISOLATION_BROKEN)
        if not got:
            self.status["reason"] = f"data.lock tutuluyor (pid {lk.read_pid() or '?'})"
            return self.finish(R_SKIPPED_LOCKED)
        prev_term = self._install_sigterm()
        try:
            stop = self.checks()
            if stop:
                return self.finish(stop)
            try:
                if self.mode == MODE_DUKA:
                    return self.finish(self._run_duka())
                return self.finish(self._run_data())
            except StopRequested:
                self.flags.add(R_STOPPED)
                self.status["reason"] = "SIGTERM (systemctl stop): durduruldu; kaldığı yerden sürdürülür"
                return self.finish(R_STOPPED)
            except Exception as exc:  # noqa: BLE001 — beklenmeyen hata: kayda geçer, sıfırdan farklı çıkış
                self.status.update(error=_err(exc), traceback_tail="".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)[-3:])[-1500:])
                return self.finish(R_FAILED)
        finally:
            self._restore_sigterm(prev_term)
            lk.release()

    # ---------------------------------------------------------------- SIGTERM (madde 10)
    def _on_sigterm(self, signum: int, frame: Any) -> None:
        raise StopRequested()

    def _install_sigterm(self) -> Any:
        if threading.current_thread() is not threading.main_thread():
            return None
        try:
            return signal.signal(signal.SIGTERM, self._on_sigterm)
        except (ValueError, OSError):
            return None

    @staticmethod
    def _restore_sigterm(prev: Any) -> None:
        if prev is None:
            return
        try:
            signal.signal(signal.SIGTERM, prev)
        except (ValueError, OSError, TypeError):
            pass

    def _run_duka(self) -> str:
        if not self.dukascopy_dir:
            self.status["dukascopy"] = {"status": R_DUKA_FAILED, "reason": "ayna klasörü verilmedi"}
            return R_DUKA_FAILED
        r = import_dukascopy(self.paths, self.dukascopy_dir, store=self.store, now=self.clock())
        self.status["dukascopy"] = {k: r.get(k) for k in ("status", "reason", "files", "rows_new", "files_digest")}
        return R_DUKA_DONE if r.get("status") == R_DUKA_DONE else R_DUKA_FAILED

    def _run_data(self) -> str:
        self._in_run = True
        plan: list[SeriesSpec] = []
        stop: str | None = None
        try:
            # 1) kurtarma (bütün mevcut seriler; ucuz) + haftalık derin örneklem
            self.progress["phase"] = "recovery"
            self._recovery_pass()
            # 2) evren
            self.progress["phase"] = "universe"
            doc = build_universe(self.paths, now=self.clock(), app_dir=self.app_dir)
            plan = plan_series(doc, now=self.clock())
            if self.plan_filter is not None:
                plan = [s for s in plan if self.plan_filter(s)]
            self.plan_keys = {s.key for s in plan}
            uj = universe_json_snapshot(self.paths)
            up = write_universe_snapshot(self.paths, snapshot_doc(doc, plan, uj))
            self.flags.update(doc.get("flags") or [])
            self.status["universe"] = {"file": str(up.relative_to(self.paths.research)), "series": len(plan),
                                       "futures": len(doc.get("futures") or {}),
                                       "gold_futures": len(doc.get("gold_futures") or {}),
                                       "main_spot": len(doc.get("main_spot") or {}),
                                       "entry_source": (doc.get("entry_universe") or {}).get("source")}
            plan.sort(key=lambda s: (KIND_ORDER.get(s.kind, 99), s.market, s.symbol))
            # 3) REST koruması → exchangeInfo (günlük, REST'e dokunmadan HEMEN önce okunur) → delist durumu
            if self._guard_ok("exchangeinfo", force=True):
                self._exchangeinfo()
            self._update_delisted(plan)
            # 4) backfill: listelenme + tohum
            if self.mode == MODE_BACKFILL:
                self.progress["phase"] = "listing"
                self._resolve_listings(plan, latest_onboard_dates(self.paths))
                self.progress["phase"] = "seed"
                self._seed(plan)
            # 5) arşiv görevleri ve işleme
            self.progress["phase"] = "archive"
            work = []
            for spec in plan:
                tasks = self._series_tasks(spec)
                if tasks is not None:
                    work.append((spec, tasks))
            self.progress["total"] = sum(len(t) for _, t in work)
            self.write_status(final=False)
            self._archive_pass(work)
            # 6) REST kuyruğu: koruma burada YENİDEN okunur (ilk doldurmada arşiv adımı saatler sürer)
            self.progress["phase"] = "rest"
            self._rest_step(plan)
            # 7) --update: 62 günlük pencerenin dışındaki açık dönemler (sınırlı onarım)
            if self.mode == MODE_UPDATE:
                self.progress["phase"] = "repair"
                self._repair_pass(plan)
        except DeadlineReached as exc:
            stop = R_DEADLINE
            self.flags.add("DEADLINE")
            self.status["deadline_reason"] = str(exc)
            for spec in plan:
                self.series.setdefault(spec.key, {"status": S_SKIPPED_DEADLINE})
        except DiskRefused as exc:
            stop = R_DISK_REFUSE
            self.flags.add(R_DISK_REFUSE)
            self.status["disk_stop"] = exc.disk
            self.status["reason"] = f"disk sınırı (kapanış arşivi önce gelir): {exc}"[:300]
            for spec in plan:
                self.series.setdefault(spec.key, {"status": S_SKIPPED_DISK, "error": f"disk: {exc}"[:300]})
        # 8) farklar
        diffs, n = self.store.drain_diffs()
        self.status["diffs"] = {"count": n, "file": None, "kept": len(diffs)}
        if n:
            rel = self.paths.data_runs / str(self.run_id) / "diffs.jsonl.gz"
            self.paths.write_bytes(rel, gzip_bytes("".join(json_line(d) for d in diffs).encode("utf-8")))
            self.status["diffs"]["file"] = str(rel.relative_to(self.paths.research))
            self.flags.add("DATA_DIFF")
        self.status["archive"] = dict(self.mirror.stats)
        if self.mirror.halted is not None:
            self.status["archive"]["halted"] = self.mirror.halted.status
        self.status["rest"].update(requests=self.rest.stats["requests"], errors=self.rest.stats["errors"],
                                   halted=self.rest.halted.status if self.rest.halted else None)
        self.progress["phase"] = "done"
        if any(v.get("status") == S_HALTED for v in self.series.values()) or any(
                self.store.manifest(*k.split("/", 2)).status == SERIES_HALTED for k in self.plan_keys):
            self.flags.add("SERIES_HALTED")
        dl = sorted({k.rsplit("/", 1)[0] for k in self.plan_keys} & set(self.delisted))
        if dl:
            self.flags.add(F_DELISTED)
            self.status["delisted"] = dl
        holes = {k: v for k, v in sorted(self.holes.items()) if v}
        if holes:
            self.flags.add(F_ARCHIVE_HOLES)
            self.status["holes"] = {"series": len(holes), "periods": sum(holes.values()),
                                    "top": dict(sorted(holes.items(), key=lambda kv: -kv[1])[:20])}
        if stop:
            return stop
        bad = [k for k, v in self.series.items() if v.get("status") in (S_STALE, S_HALTED) and v.get("error")]
        return R_PARTIAL if (bad or self.rest.halted or self.mirror.halted or "SERIES_HALTED" in self.flags
                             or holes) else R_SUCCESS

    # ---------------------------------------------------------------- REST koruması (madde 2)
    def _guard_ok(self, why: str, *, force: bool = False) -> bool:
        """Worker günlüğü koruması: `force` ya da sonuç `GUARD_MAX_AGE_S`'den eskiyse günlük YENİDEN okunur. Her okuma
        `rest.checks`'e yazılır; son sonuç `rest.guard`'dır. REST yalnız son okuma `REST_GUARD_OK` ise kullanılır."""
        t = self.clock().timestamp()
        if force or self._guard_at is None or t - self._guard_at > GUARD_MAX_AGE_S:
            g = check_worker_journal(self.runner)
            self._guard_at = self.clock().timestamp()
            self._guard_status = g["status"]
            rest = self.status["rest"]
            rest["guard"], rest["journal"] = g["status"], g
            checks = rest.setdefault("checks", [])
            checks.append({"at": self.clock().isoformat(), "why": why, "status": g["status"], "hits": g["hits"]})
            del checks[:-50]
            if g["status"] != GUARD_OK:
                self.flags.add(g["status"])
        return self._guard_status == GUARD_OK

    def _rest_guard_gate(self) -> None:
        """Her REST gönderiminden hemen önce: koruma sonucu `GUARD_MAX_AGE_S`'den eskiyse günlük yeniden okunur; geçmezse
        `RestGuardStop` (kalan REST atlanır). Tek bir seri birçok istek yapabildiği için seri başına denetim yetmez."""
        if not self._guard_ok("rest"):
            raise RestGuardStop(self._guard_status)

    # ---------------------------------------------------------------- kurtarma
    def _recovery_pass(self) -> None:
        rec = {"checked": 0, "manifest_lag": [], "corrupt": [], "missing": [], "deep": [], "halted": []}
        now = self.clock()
        isoweek = now.isocalendar()[1]
        deep_day = self.mode == MODE_UPDATE and now.weekday() == DEEP_SAMPLE_WEEKDAY
        for market, sym, tf in self.store.series():
            self.pause_window()                       # ağsız ama diske dokunur: 4h penceresinde bekler (madde 8)
            key = self.store.series_key(market, sym, tf)
            rec["checked"] += 1
            try:
                r = self.store.recover_series(market, sym, tf, night=self.night)
            except Exception as exc:  # noqa: BLE001 — bir serinin denetim hatası diğerlerini durdurmaz
                self.series[key] = {"status": S_STALE, "error": f"kurtarma: {_err(exc)}"}
                continue
            for k in ("manifest_lag", "corrupt", "missing"):
                rec[k] += [f"{key}@{ym}" for ym in r.get(k) or []]
            if r.get("halted"):
                rec["halted"].append(key)
            if deep_day and stable_bucket(key) == isoweek % 7:
                try:
                    d = self.store.deep_validate(market, sym, tf)
                    rec["deep"].append({"series": key, "ok": d["ok"], "rows": d["rows"]})
                    if not d["ok"]:                   # derin denetim bozukluk buldu: aynı kurallarla düzelt/karantina
                        r2 = self.store.recover_series(market, sym, tf, night=self.night, deep=True)
                        for k in ("manifest_lag", "corrupt", "missing"):
                            rec[k] += [f"{key}@{ym}" for ym in r2.get(k) or []]
                except Exception as exc:  # noqa: BLE001
                    rec["deep"].append({"series": key, "ok": False, "error": _err(exc)})
        if rec["manifest_lag"]:
            self.flags.add("MANIFEST_LAG")
        if rec["corrupt"] or rec["missing"]:
            self.flags.add("CORRUPT_PART")
        if rec["halted"]:
            self.flags.add("SERIES_HALTED")
        self.status["recovery"] = rec

    # ---------------------------------------------------------------- exchangeInfo (REST, korumalı)
    def _exchangeinfo(self) -> None:
        day = self.night
        p = self.paths.exchangeinfo_dir / f"{day}.json.gz"
        if p.exists():
            self.status["exchangeinfo"] = {"status": "OK", "file": str(p.relative_to(self.paths.research)), "cached": True}
            return
        try:
            fut = self.rest.get(FAPI_BASE, "/fapi/v1/exchangeInfo", {}, 1)
            spot_syms = sorted(self._spot_symbols())
            spot = self._spot_exchangeinfo(spot_syms) if spot_syms else None
        except RestHalted as exc:
            self.status["exchangeinfo"] = {"status": f"REST_HALTED_{exc.status}"}
            self.flags.add(f"REST_HALTED_{exc.status}")
            return
        except RestGuardStop as exc:
            self.status["exchangeinfo"] = {"status": exc.status}
            return
        except (RestError, TransientError) as exc:
            self.status["exchangeinfo"] = {"status": "ERROR", "error": _err(exc)}
            return
        doc = compact_exchange_info((fut or {}).get("symbols"), spot)
        write_exchangeinfo_snapshot(self.paths, day, doc)
        self.status["exchangeinfo"] = {"status": "OK", "file": str(p.relative_to(self.paths.research)),
                                       "futures": len(doc.get("futures") or []), "spot": len(doc.get("spot") or [])}

    def _spot_exchangeinfo(self, syms: list[str]) -> list[dict]:
        """Spot `exchangeInfo?symbols=[…]`; listedeki tek bir sembol borsada yoksa Binance İSTEĞİN TAMAMINI `-1121` ile
        reddeder: o zaman semboller tek tek sorulur ve yok olanlar `INVALID_SYMBOL` durumuyla kaydedilir (delist)."""
        try:
            d = self.rest.get(SPOT_BASE, "/api/v3/exchangeInfo", {"symbols": json.dumps(syms, separators=(",", ":"))}, 20)
            return list((d or {}).get("symbols") or [])
        except RestError as exc:
            if exc.code != INVALID_SYMBOL_CODE:
                raise
        out: list[dict] = []
        for s in syms:
            try:
                d = self.rest.get(SPOT_BASE, "/api/v3/exchangeInfo", {"symbol": s}, 20)
                out += list((d or {}).get("symbols") or [])
            except RestError as exc:
                if exc.code != INVALID_SYMBOL_CODE:
                    raise
                out.append({"symbol": s, "status": "INVALID_SYMBOL"})
        return out

    def _spot_symbols(self) -> set[str]:
        return {k.split("/")[1] for k in self.plan_keys if k.startswith("spot/")}

    # ---------------------------------------------------------------- delist (terminal durum; madde 3)
    def _delisted_path(self) -> Path:
        return self.paths.store / "_meta" / "delisted.json"

    def _load_delisted(self) -> dict[str, dict[str, Any]]:
        d = _read_json(self._delisted_path())
        return {str(k): dict(v) for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}

    def _save_delisted(self) -> None:
        self.paths.write_json(self._delisted_path(), dict(sorted(self.delisted.items())))

    def _mark_delisted(self, market: str, symbol: str, source: str, detail: str = "") -> None:
        key = f"{market}/{symbol}"
        if key in self.delisted:
            return
        m_last = [self.store.manifest(market, symbol, s.kind).last_ts_ms for s in self._plan_specs(market, symbol)]
        last = max((x for x in m_last if x is not None), default=None)
        self.delisted[key] = {"since_ms": None, "since": None, "last_ts": iso_ms(last), "source": source,
                              "detail": detail[:160], "at": self.clock().isoformat()}
        self._save_delisted()

    def _plan_specs(self, market: str, symbol: str) -> list[SeriesSpec]:
        return [SeriesSpec(*k.split("/", 2), 0) for k in sorted(self.plan_keys) if k.startswith(f"{market}/{symbol}/")]

    def _update_delisted(self, plan: list[SeriesSpec]) -> None:
        """En son `exchangeinfo/*.json.gz`'ye göre: vadeli tam listede YOK ya da durumu TRADING değil → delist; spot
        sorgulanan sembolün durumu TRADING değil (ya da `INVALID_SYMBOL`) → delist. TRADING görünen sembolün delist kaydı
        kalkar (yeniden listeleme). Vadeli "listede yok" kararı yalnız liste sağlamsa (BTCUSDT içeriyorsa) verilir."""
        doc = latest_exchangeinfo(self.paths)
        if not isinstance(doc, dict):
            return
        now = ms_of(self.clock())
        maps: dict[str, dict[str, dict] | None] = {}
        for market in ("futures", "spot"):
            rows = doc.get(market)
            maps[market] = ({str(r["symbol"]): r for r in rows if isinstance(r, dict) and r.get("symbol")}
                            if isinstance(rows, list) else None)
        fut_full = bool(maps["futures"]) and "BTCUSDT" in (maps["futures"] or {})
        changed = False
        for key in sorted({f"{s.market}/{s.symbol}" for s in plan}):
            market, sym = key.split("/", 1)
            mp = maps.get(market)
            if mp is None:
                continue
            r = mp.get(sym)
            if r is None and (market == "spot" or not fut_full):
                continue                               # spot: yalnız sorulan semboller listededir; sorulmadı ≠ delist
            st = (r or {}).get("status")
            if st == "TRADING":
                if key in self.delisted:
                    self.delisted.pop(key)
                    changed = True
                continue
            if key in self.delisted:
                continue
            since = None
            try:
                dd = int((r or {}).get("deliveryDate") or 0)
                since = dd if 0 < dd <= now else None
            except (TypeError, ValueError):
                since = None
            self.delisted[key] = {"since_ms": since, "since": iso_ms(since), "source": f"exchangeInfo:{st or 'LISTEDE_YOK'}",
                                  "snapshot": doc.get("day"), "at": self.clock().isoformat()}
            changed = True
        if changed:
            self._save_delisted()

    # ---------------------------------------------------------------- listelenme (backfill)
    def _listings_path(self) -> Path:
        return self.paths.store / "_meta" / "listings.json"

    def _resolve_listings(self, plan: list[SeriesSpec], onboard: Mapping[str, int]) -> None:
        cache = _read_json(self._listings_path()) or {}
        changed = False
        seen: set[str] = set()
        for spec in plan:
            if not spec.from_listing or spec.market != "futures":
                continue
            k = f"{spec.market}/{spec.symbol}"
            if k in seen or k in cache:
                seen.add(k)
                continue
            seen.add(k)
            if spec.symbol in onboard:
                cache[k] = {"listing_ms": int(onboard[spec.symbol]), "source": "exchangeInfo.onboardDate",
                            "at": self.clock().isoformat()}
                changed = True
                continue
            try:
                ms = self._probe_listing(spec)
            except ArchiveHalted as exc:
                self.flags.add("LISTING_PROBE_FAILED")
                self.series.setdefault(f"{k}/*", {"error": _err(exc)})
                break                                 # arşiv engelli: başka yoklama isteği yapılmaz
            except (TransientError, ArchiveUnitError, ValueError, zipfile.BadZipFile) as exc:
                self.flags.add("LISTING_PROBE_FAILED")
                self.series.setdefault(f"{k}/*", {"error": _err(exc)})
                continue
            cache[k] = {"listing_ms": ms, "source": "archive_probe" if ms is not None else "archive_probe_none",
                        "at": self.clock().isoformat()}
            changed = True
        if changed:
            self.paths.write_json(self._listings_path(), cache)
        self.listings = {k: v.get("listing_ms") for k, v in cache.items()}

    def _probe_listing(self, spec: SeriesSpec) -> int | None:
        """Aylık 1d zip'lerinde ikili arama: ilk var olan ay (sürekli yayın varsayımı). Bulunan zip'in ilk satırı."""
        probe = SeriesSpec(spec.market, spec.symbol, LISTING_KIND, FUTURES_ARCHIVE_FLOOR_MS)
        now = ms_of(self.clock())
        months = [m for m in months_between(FUTURES_ARCHIVE_FLOOR_MS, now) if next_month_ms(m) <= day_floor_ms(now)]
        lo, hi, found = 0, len(months) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            f = self.mirror.fetch(archive_url(probe, mstamp(months[mid])))
            if f.status == "ok":
                found, hi = mid, mid - 1
            else:
                lo = mid + 1
        if found is None:
            return None
        a = months[found]
        f = self.mirror.fetch(archive_url(probe, mstamp(a)))
        if f.data:
            df = parse_kline_zip(f.data, spec.market, a, next_month_ms(a))
            if len(df):
                return int(df["timestamp"].iloc[0])
        return a

    def _effective_start(self, spec: SeriesSpec) -> int:
        start = spec.start_ms
        if spec.from_listing and spec.market == "futures":
            lst = self.listings.get(f"{spec.market}/{spec.symbol}")
            if lst:
                start = max(start, int(lst))
        return start

    # ---------------------------------------------------------------- tohum (backfill)
    def _seed(self, plan: list[SeriesSpec]) -> None:
        """Tohumlama hiçbir koşulda bütün işi başarısız saymaz (§3.2): bir serinin BEKLENMEYEN hatası da (ör. worker
        parçasında NaN zaman damgası) o seriyi `ERROR` yapar ve seri arşivden indirilir."""
        out = {"seeded": 0, "redownload": 0, "absent": 0, "error": 0, "other": 0, "rows": 0, "series": {}}
        if not self.seed_root.is_dir():
            out["note"] = f"worker deposu yok: {self.seed_root}"
            self.status["seed"] = out
            return
        for spec in plan:
            self.pause_window()                       # worker deposunu okur: IndexRefresher 4h penceresinde yazar
            self.disk_check()
            try:
                r = seed_series(self.store, self.seed_root, spec.market, spec.symbol, spec.kind,
                                now_ms=ms_of(self.clock()), hook=self.seed_hook)
            except Exception as exc:  # noqa: BLE001 — tohum çalıştırmayı durdurmaz; seri arşivden gelir
                r = {"series": spec.key, "status": ST_ERROR, "reason": f"tohum: {_err(exc)}"}
            s = r.get("status")
            if s == ST_SEEDED:
                out["seeded"] += 1
                out["rows"] += int(r.get("rows") or 0)
            elif s == ST_REDOWNLOAD:
                out["redownload"] += 1
                self.flags.add("SEED_REDOWNLOAD")
            elif s == "ABSENT":
                out["absent"] += 1
                continue
            elif s == ST_ERROR:
                out["error"] += 1
                self.flags.add("SEED_ERROR")
            else:
                out["other"] += 1
            out["series"][spec.key] = {k: v for k, v in r.items() if k in ("status", "attempt", "rows", "reasons", "reason")}
        self.status["seed"] = out

    # ---------------------------------------------------------------- arşiv görevleri
    def _expected_rows(self, spec: SeriesSpec, a: int, b: int) -> int | None:
        st = step_ms(spec.kind)
        return (b - a) // st if st and spec.kind != METRICS_5M else None

    def _series_tasks(self, spec: SeriesSpec, *, repair: bool = False) -> list[Task] | None:
        """Bu seri için gereken arşiv dosyaları. None: seri bu çalıştırmada atlanır (durumu `self.series`'te).
        `repair=True` (yalnız `--update`): 62 günlük pencereden ÖNCEKİ, defteri kapanmamış dönemler — eksik ay/gün ya da
        doğrulanmamış (`u`) dosya; pencere içi ve yeniden çekim kuyruğundaki aylar ana geçiştedir."""
        m = self.store.manifest(spec.market, spec.symbol, spec.kind)
        key = spec.key
        if repair and (self.mode != MODE_UPDATE or not m.row_count or m.status == SERIES_HALTED):
            return []
        if not repair and m.status == SERIES_HALTED and not m.refetch:
            self.series[key] = {"status": S_HALTED}
            return None
        if not repair and self.mode == MODE_UPDATE and not m.row_count and not m.refetch:
            self.series[key] = {"status": S_NO_BASELINE}
            return None
        now = ms_of(self.clock())
        today = day_floor_ms(now)
        start = self._effective_start(spec)
        lo = start
        if self.mode == MODE_UPDATE:
            lo = max(start, month_start_ms(now - UPDATE_LOOKBACK_DAYS * DAY_MS))
        refetch = set(m.refetch)
        if repair:
            months = [a for a in months_between(start, lo) if ym_key(a) not in refetch]
            refetch = set()
        else:
            months = sorted(set(months_between(lo, today)) | {month_bounds_ym(ym)[0] for ym in refetch})
        tasks: list[Task] = []
        halted = m.status == SERIES_HALTED
        for a in months:
            b = next_month_ms(a)
            if b <= start:
                continue
            ym = ym_key(a)
            rf = ym in refetch
            if halted and not rf:
                continue
            ms_ = mstamp(a)
            complete = b <= today
            if spec.dataset != "metrics" and complete:
                fe = m.files.get(ms_) or {}
                st = fe.get("st")
                if st in (FS_VERIFIED, FS_MISSING) and not rf:
                    continue
                if st == FS_UNVERIFIED and not rf:
                    tasks.append(Task(ms_, a, b, recheck=True, at=str(fe.get("at") or "")))
                    continue
                if not rf and self._seed_complete(m, ym, spec, a, b):
                    continue
                tasks.append(Task(ms_, a, b, refetch=rf))
                continue
            if spec.dataset == "fundingRate":
                continue                                  # gün zip'i yok; içinde bulunulan ay REST'ten
            mst = (m.files.get(ms_) or {}).get("st")
            if mst in (FS_VERIFIED, FS_PARTIAL) and not rf:
                continue
            d = max(a, day_floor_ms(start))
            while d < min(b, today):
                ds = dstamp(d)
                fe = m.files.get(ds) or {}
                st = fe.get("st")
                if rf or st not in (FS_VERIFIED, FS_MISSING):
                    tasks.append(Task(ds, d, d + DAY_MS, refetch=rf, recheck=st == FS_UNVERIFIED,
                                      at=str(fe.get("at") or "")))
                d += DAY_MS
        return tasks

    @staticmethod
    def _seed_complete(m: Any, ym: str, spec: SeriesSpec, a: int, b: int) -> bool:
        pm = m.part_meta.get(ym) or {}
        st = step_ms(spec.kind)
        if not st or spec.kind == METRICS_5M or spec.kind == FUNDING:
            return False
        src = pm.get("src") or {}
        return int(pm.get("rows") or 0) == (b - a) // st and set(src) == {"seed"}

    def _repair_pass(self, plan: list[SeriesSpec]) -> None:
        """`--update` (BLOCKER düzeltmesi, 2026-10-06): pencere DIŞINDAKİ açık dönemler. Önce hiç kapsanmamış dönemler,
        sonra doğrulanmamış dosyaların checksum'ı (en uzun süredir denenmeyen önce); en çok `REPAIR_MAX_TASKS` görev.
        Sığmayanlar seri başına `holes`'a yazılır (dürüst durum: `ARCHIVE_HOLES`, sonuç `PARTIAL`)."""
        cand: list[tuple[tuple, SeriesSpec, Task]] = []
        for i, spec in enumerate(plan):
            for t in self._series_tasks(spec, repair=True) or []:
                cand.append(((1 if t.recheck else 0, t.at if t.recheck else "", i, t.start), spec, t))
        if not cand:
            return
        cand.sort(key=lambda x: x[0])
        take, over = cand[:REPAIR_MAX_TASKS], cand[REPAIR_MAX_TASKS:]
        for _k, spec, _t in over:
            self.holes[spec.key] = self.holes.get(spec.key, 0) + 1
        by: dict[str, tuple[SeriesSpec, list[Task]]] = {}
        for _k, spec, t in take:
            by.setdefault(spec.key, (spec, []))[1].append(t)
        work = [(spec, sorted(ts, key=lambda t: t.start)) for spec, ts in by.values()]
        self.status["repair"] = {"tasks": len(take), "series": len(work), "left": len(over)}
        self.progress["total"] = int(self.progress.get("total", 0)) + len(take)
        self._archive_pass(work)

    # ---------------------------------------------------------------- arşiv işleme
    def _retry(self, fn: Callable[[], Any]) -> Any:
        last: BaseException | None = None
        for attempt in range(RETRIES):
            try:
                return fn()
            except TransientError as exc:
                last = exc
                if attempt + 1 < RETRIES:
                    self.sleep(RETRY_BASE_S * (2 ** attempt) * (0.5 + self.rng.random()))
        assert last is not None
        raise last

    def _archive_pass(self, work: list[tuple[SeriesSpec, list[Task]]]) -> None:
        for i, (spec, tasks) in enumerate(work):
            try:
                self._archive_series(spec, tasks)
            except ArchiveHalted as exc:
                self._archive_halted(exc, work[i:])
                return

    def _archive_halted(self, exc: ArchiveHalted, rest: list[tuple[SeriesSpec, list[Task]]]) -> None:
        """403/451: arşiv adımı bu çalıştırmada durur; kalan seriler `STALE` (dönemleri "eksik" SAYILMAZ, kalıcı işaret
        yazılmaz — sonraki çalıştırma aynı dosyaları yeniden dener)."""
        self.flags.add(f"ARCHIVE_HALTED_{exc.status}")
        self.status["archive_halted"] = {"status": exc.status, "error": str(exc)[:200], "at": self.clock().isoformat()}
        for spec, tasks in rest:
            rec = self.series.setdefault(spec.key, {"status": S_OK})
            if not tasks and rec.get("status") in (None, S_OK):
                continue
            left = [t for t in tasks if not t.done]
            if left or rec.get("status") in (None, S_OK):
                rec.update(status=S_STALE, error=f"arşiv erişimi engellendi (HTTP {exc.status}); dönemler eksik "
                                                  f"sayılmadı, sonraki çalıştırmada yeniden denenir")
            for t in left:
                t.done = True
            self.tick(len(left))

    def _archive_series(self, spec: SeriesSpec, tasks: list[Task]) -> None:
        key = spec.key
        rec = self.series.setdefault(key, {"status": S_OK})
        if not tasks:
            return
        try:
            self._retry(lambda: self._archive_tasks(spec, tasks, rec))
        except (DeadlineReached, RestHalted, DiskRefused, ArchiveHalted):
            raise
        except Exception as exc:  # noqa: BLE001 — bir serinin hatası diğerlerini durdurmaz (madde 6)
            rec.update(status=S_STALE, error=_err(exc))
            left = [t for t in tasks if not t.done]
            self.tick(len(left))
            failed_months = sorted({ym_key(t.start) for t in left if t.refetch})
            for t in left:
                t.done, t.failed = True, True
            for ym in failed_months:                  # ağ hatasıyla yeniden çekilemeyen ay da başarısız gece sayılır
                try:
                    self.store.refetch_result(spec.market, spec.symbol, spec.kind, ym, ok=False, night=self.night,
                                              reason=_err(exc))
                except Exception:  # noqa: BLE001
                    pass
        n_failed = sum(1 for t in tasks if t.failed)
        if n_failed:
            self.holes[key] = self.holes.get(key, 0) + n_failed

    def _archive_tasks(self, spec: SeriesSpec, tasks: list[Task], rec: dict[str, Any]) -> None:
        """Görevleri ay ay işle: bir ayın gün dosyaları tek yazımda (yetkili aralık listesiyle) birleşir. Bir ay ancak
        YAZIMI tamamlanınca "bitti" sayılır: yeniden deneme yarım kalan ayı baştan alır (önbellekten)."""
        by_month: dict[int, list[Task]] = {}
        for t in tasks:
            if t.done:
                continue
            by_month.setdefault(month_start_ms(t.start), []).append(t)
        for a in sorted(by_month):
            ts = by_month[a]
            if len(ts[0].period) == 7:
                self._monthly(spec, ts[0], rec)
            else:
                self._daily_batch(spec, a, ts, rec)

    def _done(self, t: Task) -> None:
        t.done = True
        self.tick()

    def _monthly(self, spec: SeriesSpec, t: Task, rec: dict[str, Any]) -> None:
        url = archive_url(spec, t.period)
        f = self.mirror.fetch(url)
        ym = ym_key(t.start)
        if f.status == "missing":
            recent = t.end > ms_of(self.clock()) - KEEP_404_DAYS * DAY_MS
            if recent and spec.dataset != "fundingRate":
                # aylık zip henüz yayımlanmadı: o ayın gün zip'leri (madde 1). Yeniden çekimde ayın BÜTÜN günleri
                # (defterden bağımsız) istenir ve sonucu gün yığını kaydeder (önceden burada her durumda "başarısız"
                # yazılıyordu: gün zip'lerinden kurulan ay iki gece sonra sahte HALTED olurdu)
                start = max(t.start, day_floor_ms(self._effective_start(spec)))
                days = [Task(dstamp(d), d, d + DAY_MS, refetch=t.refetch) for d in range(start, t.end, DAY_MS)]
                if not t.refetch:
                    m = self.store.manifest(spec.market, spec.symbol, spec.kind)
                    days = [x for x in days if (m.files.get(x.period) or {}).get("st") not in (FS_VERIFIED, FS_MISSING)]
                t.failed = bool(self._daily_batch(spec, t.start, days, rec, count=False))
            elif not recent:
                self.store.note_files(spec.market, spec.symbol, spec.kind,
                                      {t.period: {"st": FS_MISSING, "at": self.clock().isoformat()}})
                if t.refetch:
                    self.store.refetch_result(spec.market, spec.symbol, spec.kind, ym, ok=False, night=self.night,
                                              reason="arşivde yok")
            # yakın dönem fonlama: aylık zip henüz yok, gün zip'i hiç yok; içinde bulunulan + önceki ay REST'ten gelir.
            # Yeniden çekim kuyruğunda bekler (başarısız gece SAYILMAZ); aylık zip yayımlanınca tamamlanır.
            self._done(t)
            return
        if f.status == "mismatch":
            self.store.mark_bad_chunk(spec.market, spec.symbol, spec.kind, f"archive:{t.period}", "checksum_mismatch")
            rec.update(status=S_STALE, error=f"checksum tutmadı: {t.period}")
            if t.refetch:
                self.store.refetch_result(spec.market, spec.symbol, spec.kind, ym, ok=False, night=self.night,
                                          reason="checksum tutmadı")
            t.failed = True
            self._done(t)
            return
        try:
            df = parse_archive(spec, f.data or b"", t.start, t.end)
        except (ArchiveUnitError, ValueError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
            self.store.mark_bad_chunk(spec.market, spec.symbol, spec.kind, f"archive:{t.period}", f"corrupt: {exc}"[:180])
            rec.update(status=S_STALE, error=f"bozuk arşiv {t.period}: {_err(exc)}")
            if t.refetch:
                self.store.refetch_result(spec.market, spec.symbol, spec.kind, ym, ok=False, night=self.night,
                                          reason=_err(exc))
            t.failed = True
            self._done(t)
            return
        src = SRC_ARCHIVE if f.verified else SRC_UNVERIFIED
        m = self.store.manifest(spec.market, spec.symbol, spec.kind)
        files = {t.period: {"st": FS_VERIFIED if f.verified else FS_UNVERIFIED, "sha": f.sha, "rows": int(len(df)),
                            "at": self.clock().isoformat()}}
        stamp = t.period
        drop_days = {k: None for k in m.files if k.startswith(stamp + "-")} if f.verified else {}
        res = self.store.write(spec.market, spec.symbol, spec.kind, df, src=src, chunk_id=f"archive:{t.period}",
                               authoritative=(t.start, t.end) if f.verified else None, files=files)
        if drop_days:
            self._drop_file_keys(spec, list(drop_days))
        if f.verified:
            self.store.clear_bad_chunk(spec.market, spec.symbol, spec.kind, f"archive:{t.period}")
            if spec.kind == "1m":
                self.mirror.delete(url)
        rec["rows_new"] = int(rec.get("rows_new", 0)) + int(res["rows_new"])
        rec["archive_files"] = int(rec.get("archive_files", 0)) + 1
        if t.refetch:
            self.store.refetch_result(spec.market, spec.symbol, spec.kind, ym, ok=True, night=self.night)
        self._done(t)

    def _drop_file_keys(self, spec: SeriesSpec, keys: list[str]) -> None:
        with self.store.series_lock(spec.market, spec.symbol, spec.kind):
            m = self.store.manifest(spec.market, spec.symbol, spec.kind)
            for k in keys:
                m.files.pop(k, None)
            self.store._recompute(m)
            self.store._save_manifest(m)

    def _daily_batch(self, spec: SeriesSpec, month_a: int, tasks: list[Task], rec: dict[str, Any], *,
                     count: bool = True) -> int:
        """Bir ayın gün zip'leri tek yazımda. BLOCKER düzeltmesi (2026-10-06): görevler YAZIM TAMAMLANDIKTAN SONRA
        "bitti" işaretlenir; araya giren geçici hata (`TransientError`) bütün ayı yeniden denetir (zip'ler önbellekte)
        — önceden yazılmamış günler "bitti" sayılıp kayboluyor ve çalıştırma yine SUCCESS diyordu. Dönen: kalıcı hatalı
        (checksum tutmadı / bozuk) gün sayısı."""
        verified: list[pd.DataFrame] = []
        unverified: list[pd.DataFrame] = []
        ranges: list[tuple[int, int]] = []
        files: dict[str, dict] = {}
        files_u: dict[str, dict] = {}
        now = ms_of(self.clock())
        refetch_any = any(t.refetch for t in tasks)
        failed = 0
        urls_1m: list[str] = []
        for t in tasks:
            url = archive_url(spec, t.period)
            f = self.mirror.fetch(url)
            if f.status == "missing":
                if t.end < now - KEEP_404_DAYS * DAY_MS:
                    files[t.period] = {"st": FS_MISSING, "at": self.clock().isoformat()}
                continue
            if f.status == "mismatch":
                self.store.mark_bad_chunk(spec.market, spec.symbol, spec.kind, f"archive:{t.period}", "checksum_mismatch")
                rec.update(status=S_STALE, error=f"checksum tutmadı: {t.period}")
                failed += 1
                t.failed = True
                continue
            try:
                df = parse_archive(spec, f.data or b"", t.start, t.end)
            except (ArchiveUnitError, ValueError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
                self.store.mark_bad_chunk(spec.market, spec.symbol, spec.kind, f"archive:{t.period}",
                                          f"corrupt: {exc}"[:180])
                rec.update(status=S_STALE, error=f"bozuk arşiv {t.period}: {_err(exc)}")
                failed += 1
                t.failed = True
                continue
            info = {"st": FS_VERIFIED if f.verified else FS_UNVERIFIED, "sha": f.sha, "rows": int(len(df)),
                    "at": self.clock().isoformat()}
            if f.verified:
                verified.append(df)
                ranges.append((t.start, t.end))
                files[t.period] = info
                if spec.kind == "1m":
                    urls_1m.append(url)
            else:
                unverified.append(df)
                files_u[t.period] = info
        rows_new = 0
        if unverified:
            rows_new += self.store.write(spec.market, spec.symbol, spec.kind, pd.concat(unverified, ignore_index=True),
                                         src=SRC_UNVERIFIED, chunk_id=f"archive:{mstamp(month_a)}:daily",
                                         files=files_u)["rows_new"]
        if verified or files:
            df = pd.concat(verified, ignore_index=True) if verified else None
            rows_new += self.store.write(spec.market, spec.symbol, spec.kind, df, src=SRC_ARCHIVE,
                                         chunk_id=f"archive:{mstamp(month_a)}:daily", authoritative=ranges or None,
                                         files=files)["rows_new"]
            for k in files:
                self.store.clear_bad_chunk(spec.market, spec.symbol, spec.kind, f"archive:{k}")
            for u in urls_1m:
                self.mirror.delete(u)
        self._maybe_compact_month(spec, month_a)
        rec["rows_new"] = int(rec.get("rows_new", 0)) + int(rows_new)
        rec["archive_files"] = int(rec.get("archive_files", 0)) + len(files) + len(files_u)
        if refetch_any:
            ym = ym_key(month_a)
            self.store.refetch_result(spec.market, spec.symbol, spec.kind, ym, ok=not failed and bool(verified),
                                      night=self.night, reason=None if verified else "gün zip'leri doğrulanamadı")
        for t in tasks:                               # ancak şimdi: ayın yazımı tamamlandı
            t.done = True
            if count:
                self.tick()
        return failed

    def _maybe_compact_month(self, spec: SeriesSpec, a: int) -> None:
        """Biten bir ayın bütün günleri doğrulandı/kalıcı eksikse gün damgaları tek ay damgasına sıkıştırılır (manifest
        küçük kalır). Aylık zip'i olan türlerde bu yapılmaz (aylık zip yayımlanınca o gelir)."""
        if spec.dataset != "metrics":
            return
        b = next_month_ms(a)
        if b > day_floor_ms(ms_of(self.clock())):
            return
        m = self.store.manifest(spec.market, spec.symbol, spec.kind)
        start = max(a, day_floor_ms(self._effective_start(spec)))
        days = [dstamp(d) for d in range(start, b, DAY_MS)]
        sts = [(m.files.get(d) or {}).get("st") for d in days]
        if not days or any(s not in (FS_VERIFIED, FS_MISSING) for s in sts):
            return
        missing = [d for d, s in zip(days, sts) if s == FS_MISSING]
        digest = hashlib.sha256("".join(f"{d}|{(m.files.get(d) or {}).get('sha')}\n" for d in days).encode()).hexdigest()
        with self.store.series_lock(spec.market, spec.symbol, spec.kind):
            m = self.store.manifest(spec.market, spec.symbol, spec.kind)
            for d in days:
                m.files.pop(d, None)
            m.files[mstamp(a)] = {"st": FS_PARTIAL if missing else FS_VERIFIED, "daily": True, "days": len(days),
                                  "missing_days": missing, "sha": digest, "at": self.clock().isoformat()}
            self.store._recompute(m)
            self.store._save_manifest(m)

    # ---------------------------------------------------------------- REST kuyruğu (madde 3)
    def _rest_halted_series(self, rec: dict[str, Any]) -> None:
        """§3.4 tablosu: 418/429 (403/451) → REST adımı durur, ilgili seriler `STALE` olur, kayıt düşülür."""
        code = self.rest.halted.status if self.rest.halted is not None else "?"
        rec["rest_skipped"] = f"REST_HALTED_{code}"
        if rec.get("status") in (None, S_OK):
            rec.update(status=S_STALE, error=f"REST_HALTED_{code}: REST adımı durdu, kuyruk alınmadı (§3.4)")

    def _rest_step(self, plan: list[SeriesSpec]) -> None:
        todo: list[tuple[SeriesSpec, Any]] = []
        for spec in plan:
            m = self.store.manifest(spec.market, spec.symbol, spec.kind)
            if not m.row_count or m.status == SERIES_HALTED:
                continue
            if f"{spec.market}/{spec.symbol}" in self.delisted:
                self.series.setdefault(spec.key, {"status": S_OK})["rest_skipped"] = S_DELISTED
                continue
            todo.append((spec, m))
        if not todo:
            return
        if not self._guard_ok("rest", force=True):    # §3.4 madde 2: REST'e dokunmadan HEMEN önce
            for spec, _m in todo:
                self.series.setdefault(spec.key, {"status": S_OK})["rest_skipped"] = self._guard_status
            return
        for i, (spec, m) in enumerate(todo):
            rec = self.series.setdefault(spec.key, {"status": S_OK})
            if self.rest.halted is not None:
                self._rest_halted_series(rec)
                continue
            if f"{spec.market}/{spec.symbol}" in self.delisted:      # bu adımda -1121 ile delist bulundu
                rec["rest_skipped"] = S_DELISTED
                continue
            try:
                n = self._retry(lambda: self._rest_series(spec, m.last_ts_ms))
                if n:
                    rec["rest_rows"] = int(rec.get("rest_rows", 0)) + int(n)
            except RestHalted as exc:
                self.flags.add(f"REST_HALTED_{exc.status}")
                rec.update(rest_error=_err(exc))
                self._rest_halted_series(rec)
            except RestGuardStop:                     # sonuç bayatladı, yeniden okundu, geçmedi: kalan REST atlanır
                for spec2, _m2 in todo[i:]:
                    self.series.setdefault(spec2.key, {"status": S_OK})["rest_skipped"] = self._guard_status
                return
            except (DeadlineReached, DiskRefused):
                raise
            except RestError as exc:
                if exc.code == INVALID_SYMBOL_CODE:   # sembol borsada yok: delist (terminal; veri o ana kadar kalır)
                    self._mark_delisted(spec.market, spec.symbol, f"rest:{INVALID_SYMBOL_CODE}", str(exc))
                    rec["rest_skipped"] = S_DELISTED
                    continue
                rec.update(status=S_STALE, error=f"REST: {_err(exc)}")
            except Exception as exc:  # noqa: BLE001 — bir serinin REST hatası diğerlerini durdurmaz
                rec.update(status=S_STALE, error=f"REST: {_err(exc)}")

    def _rest_series(self, spec: SeriesSpec, last_ts: int | None) -> int:
        now = ms_of(self.clock())
        if last_ts is None:
            return 0
        if spec.kind == FUNDING:
            # fonlamanın gün zip'i yok: içinde bulunulan ay ve aylık zip'i henüz yayımlanmamış önceki ay REST'ten
            # (≤ ~62 gün ≈ 190 satır, tek istek, ağırlık 1); daha eski aylar için REST yok
            return self._rest_funding(spec, max(last_ts + 1, month_start_ms(month_start_ms(now) - 1)), now)
        if spec.kind == METRICS_5M:
            return self._rest_metrics(spec, max(last_ts + 300_000, now - REST_LOOKBACK_MS), now)
        step = step_ms(spec.kind)
        assert step
        start = max(last_ts + step, now - REST_LOOKBACK_MS)
        start -= start % step
        if start + step > now:
            return 0
        if spec.market == "spot":
            base, path, maxlim = SPOT_BASE, "/api/v3/klines", 1000
        else:
            base, maxlim = FAPI_BASE, 1500
            path = {MARKPX_1H: "/fapi/v1/markPriceKlines", PREMIUM_1H: "/fapi/v1/premiumIndexKlines"}.get(spec.kind,
                                                                                                         "/fapi/v1/klines")
        interval = spec.interval
        rows_new, cur, guard = 0, start, 0
        while cur + step <= now and guard < 50:
            guard += 1
            need = (now - cur) // step + 1
            lim = int(max(1, min(maxlim, need)))
            data = self.rest.get(base, path, {"symbol": spec.symbol, "interval": interval, "startTime": cur,
                                              "endTime": now - 1, "limit": lim}, kline_weight(spec.market, lim))
            sent = int(self.rest.last_sent_ms or now)
            if not data:
                break
            df = _rest_klines_frame(data, spec.kind)
            # yalnız İSTEK GÖNDERİLDİĞİNDE kapanmış barlar (yanıt gecikse bile oluşmakta olan bar yazılmaz)
            df = df[(df["timestamp"] >= cur) & (df["timestamp"] + step <= sent)]
            if not len(df):
                break
            rows_new += int(self.store.write(spec.market, spec.symbol, spec.kind, df, src=SRC_REST,
                                             chunk_id=f"rest:{cur}", now_ms=sent)["rows_new"])
            last = int(df["timestamp"].max())
            if last + step <= cur:
                break
            cur = last + step
            if len(data) < lim:
                break
        return rows_new

    def _rest_funding(self, spec: SeriesSpec, start: int, now: int) -> int:
        data = self.rest.get(FAPI_BASE, "/fapi/v1/fundingRate", {"symbol": spec.symbol, "startTime": start,
                                                                 "endTime": now, "limit": 1000}, 1)
        sent = int(self.rest.last_sent_ms or now)
        rows = [r for r in (data or []) if isinstance(r, dict) and r.get("fundingTime")]
        if not rows:
            return 0
        df = pd.DataFrame({"timestamp": pd.Series([int(r["fundingTime"]) for r in rows], dtype="int64"),
                           "rate": [_f(str(r.get("fundingRate"))) for r in rows],
                           "mark": [_f(str(r["markPrice"])) if r.get("markPrice") not in (None, "") else float("nan")
                                    for r in rows]})
        return int(self.store.write("futures", spec.symbol, FUNDING, df, src=SRC_REST, chunk_id=f"rest:funding:{start}",
                                    now_ms=sent)["rows_new"])

    #: metrics_5m REST eşlemesi (`crowd_data.COLUMN_MAP` ile aynı; VPS'te arşiv uzlaştırmasıyla doğrulanır: fark → runs/)
    METRICS_REST = (("/futures/data/openInterestHist", {"oi": "sumOpenInterest", "oi_usdt": "sumOpenInterestValue"}),
                    ("/futures/data/topLongShortAccountRatio", {"top_acct_ls": "longShortRatio"}),
                    ("/futures/data/topLongShortPositionRatio", {"top_pos_ls": "longShortRatio"}),
                    ("/futures/data/globalLongShortAccountRatio", {"global_ls": "longShortRatio"}),
                    ("/futures/data/takerlongshortRatio", {"taker_ls_vol": "buySellRatio"}))

    def _rest_metrics(self, spec: SeriesSpec, start: int, now: int) -> int:
        start -= start % 300_000
        if start > now - 300_000:
            return 0
        acc: dict[int, dict[str, float]] = {}
        first_sent: int | None = None
        for path, cmap in self.METRICS_REST:
            cur, guard = start, 0
            while cur <= now and guard < 20:
                guard += 1
                data = self.rest.get(FAPI_BASE, path, {"symbol": spec.symbol, "period": "5m", "startTime": cur,
                                                       "endTime": now, "limit": 500}, 1)
                if first_sent is None:
                    first_sent = int(self.rest.last_sent_ms or now)
                rows = [r for r in (data or []) if isinstance(r, dict) and r.get("timestamp") is not None]
                if not rows:
                    break
                for r in rows:
                    t = int(r["timestamp"])
                    d = acc.setdefault(t, {})
                    for col, fld in cmap.items():
                        d[col] = _f(str(r.get(fld))) if r.get(fld) not in (None, "") else float("nan")
                last = max(int(r["timestamp"]) for r in rows)
                if last < cur or len(rows) < 500:
                    break
                cur = last + 300_000
        if not acc:
            return 0
        ts = sorted(acc)
        df = pd.DataFrame({"timestamp": pd.Series(ts, dtype="int64"),
                           **{c: [acc[t].get(c, float("nan")) for t in ts] for c in METRICS_COLS[1:]}})
        # beş uç noktanın İLKİ gönderildiğinde var olan noktalar: sonradan gelen bir uç noktanın tek başına getirdiği
        # (öteki sütunları boş) en yeni nokta yazılmaz
        return int(self.store.write("futures", spec.symbol, METRICS_5M, df, src=SRC_REST,
                                    chunk_id=f"rest:metrics:{start}", now_ms=first_sent or now)["rows_new"])


def _rest_klines_frame(data: list, kind: str) -> pd.DataFrame:
    rows = [r for r in data if isinstance(r, (list, tuple)) and len(r) >= 6]

    def col(i: int) -> list[float]:
        return [_f(str(r[i])) if len(r) > i and r[i] is not None else float("nan") for r in rows]
    df = pd.DataFrame({"timestamp": pd.Series([int(r[0]) for r in rows], dtype="int64"), "open": col(1), "high": col(2),
                       "low": col(3), "close": col(4), "volume": col(5),
                       "close_time": pd.Series([int(r[6]) if len(r) > 6 else 0 for r in rows], dtype="int64"),
                       "quote_volume": col(7),
                       "trades": pd.Series([int(r[8]) if len(r) > 8 and r[8] is not None else 0 for r in rows],
                                           dtype="int64"),
                       "taker_buy_base": col(9), "taker_buy_quote": col(10)})
    if kind in (MARKPX_1H, PREMIUM_1H):
        return df[PX_COLS]
    return df


# ============================================================================ giriş noktaları
def run_data(paths: EnginePaths | None = None, *, mode: str, env: Mapping[str, str] | None = None,
             **kw: Any) -> dict[str, Any]:
    """`engine-data --update|--backfill|--import-dukascopy`. Dönen: çalıştırma kaydı (`exit_code` dahil)."""
    if mode not in (MODE_UPDATE, MODE_BACKFILL, MODE_DUKA):
        raise ValueError(f"bilinmeyen kip: {mode}")
    env = os.environ if env is None else env
    paths = paths or EnginePaths.from_env(env)
    return DataRun(paths, mode=mode, env=env, **kw).run()


def _pid_alive(pid: Any) -> bool | None:
    """True/False; bilinemiyorsa None (başka kullanıcının süreci → canlı sayılır)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _gb(x: Any) -> str:
    try:
        return f"{int(x) / GB:.1f} GB".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def read_data_status(paths: EnginePaths) -> dict[str, Any] | None:
    d = _read_json(paths.data_status)
    return d if isinstance(d, dict) else None


def status_lines(paths: EnginePaths, *, now: datetime | None = None, limit: int = 40) -> list[str]:
    """`engine-data --status` (salt-okunur; kilit almaz). ≤ `limit` satır."""
    now = now or datetime.now(UTC)
    d = read_data_status(paths)
    out = [f"VERİ BİRİMİ ({ENGINE_VERSION}) — kayıt-yalnız, PAPER"]
    if not d:
        return out + ["data_status.json yok (engine-data henüz çalışmadı)"]
    lr = d.get("last_run") or {}
    out.append(f"Son çalıştırma: {lr.get('run_id')} {lr.get('mode')} → {lr.get('result')} (çıkış {lr.get('exit_code')}) "
               f"· bitiş {lr.get('finished_at')}")
    out.append(f"Mühür: data_seal {str(d.get('data_seal'))[:16]}… · {d.get('sealed_parts')} parça · {d.get('sealed_at')}")
    run = d.get("running")
    if run:
        p = run.get("progress") or {}
        alive = _pid_alive(run.get("pid"))
        head = "SÜRÜYOR" if alive is not False else (f"DURDU (süreç {run.get('pid')} yok; son güncelleme "
                                                     f"{str(d.get('updated_at'))[:16]}; kaldığı yerden sürdürülür)")
        out.append(f"{head}: {run.get('run_id')} {run.get('mode')} · aşama {p.get('phase')} · {p.get('done')}/{p.get('total')} "
                   f"· hız {p.get('rate_per_h_1h')}/sa · tahmini bitiş {p.get('eta')}"
                   + (f" · DURAKLADI ({p.get('pause_reason')}, {p.get('resume_at')}'e kadar)" if p.get("paused") and alive
                      is not False else ""))
        dk = p.get("disk") or {}
        if dk:
            out.append(f"Disk (çalışırken): boş {_gb(dk.get('free_bytes'))} · araştırma ≈ {_gb(dk.get('research_bytes'))} · "
                       f"veri birimi durur: boş < {_gb(DATA_MIN_FREE_BYTES)} ya da araştırma ≥ {_gb(DATA_REFUSE_RESEARCH_BYTES)} "
                       f"(gece birimi {_gb(MIN_FREE_BYTES)} / {_gb(REFUSE_RESEARCH_BYTES)})")
    t = d.get("totals") or {}
    out.append(f"Seriler: {t.get('series')} (planlı {t.get('planned')}) · bayat {t.get('stale')} · durmuş {t.get('halted')} "
               f"· tabansız {t.get('no_baseline')} · delist {t.get('delisted', 0)} · açık dönem {t.get('holes', 0)} · satır "
               f"{t.get('rows')} (doğrulanmamış {t.get('unverified_rows')}, REST {t.get('rest_rows')}, tohum {t.get('seed_rows')})")
    dls = d.get("delisted") or {}
    if dls:
        out.append("Delist (terminal; veri delist anına kadar, bayat sayılmaz): " + ", ".join(
            f"{k} ({(v or {}).get('source')})" for k, v in list(dls.items())[:8]) + (" …" if len(dls) > 8 else ""))
    rest = lr.get("rest") or {}
    out.append(f"REST: koruma {rest.get('guard')} · istek {rest.get('requests')} · durdu {rest.get('halted')} · "
               f"fark {((lr.get('diffs') or {}).get('count'))}")
    du = d.get("dukascopy") or {}
    out.append(f"Dukascopy: {du.get('status')}" + (f" — {du.get('reason')}" if du.get("status") != R_DUKA_DONE else
                                                  f" · {du.get('imported_at')}"))
    if lr.get("flags"):
        out.append("Bayraklar: " + ", ".join(lr.get("flags") or []))
    bad = [(k, v) for k, v in (d.get("series") or {}).items() if v.get("stale") or v.get("status") == S_HALTED]
    bad.sort(key=lambda kv: -(kv[1].get("age_h") or 1e9))
    if bad:
        out.append("Bayat/durmuş seriler (en eski önce):")
        for k, v in bad[: max(0, limit - len(out) - 1)]:
            out.append(f"  {k}: {v.get('status')} · son {v.get('last_ts')} · yaş {v.get('age_h')} sa"
                       + (f" · {v.get('error') or v.get('halted_reason')}" if (v.get('error') or v.get('halted_reason')) else ""))
    return out[:limit]


__all__ = ["ArchiveFile", "ArchiveMirror", "DATA_STATUS_SCHEMA", "DataRun", "DeadlineReached", "EXIT_CODES",
           "EngineBudget", "FAPI_BASE", "GUARD_OK", "GUARD_SKIP", "GUARD_UNKNOWN", "HttpResponse", "MODE_BACKFILL",
           "MODE_DUKA", "MODE_UPDATE", "R_DUKA_DONE", "R_DUKA_FAILED", "RestClient", "RestError", "RestHalted",
           "SPOT_BASE", "TransientError", "WORKER_RATE_LINE_RE", "archive_url", "check_worker_journal",
           "compute_data_deadline", "decode_bi5", "import_dukascopy", "in_publication_window", "parse_archive",
           "parse_funding_zip", "parse_kline_zip", "parse_metrics_zip", "read_data_status", "read_duka_status",
           "run_data", "status_lines", "urllib_http"]

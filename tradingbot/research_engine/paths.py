"""Motor kök düzeni, disk koruması ve YALNIZ `data/research` altına atomik yazım (§2.1, §2.5, §3.1).

Kök: `$TRADINGBOT_DATA/research` (VPS'te `/opt/tradingbot/data/research`). State `$TRADINGBOT_STATE_DIR`
(yoksa `$TRADINGBOT_DATA/state`). Motor state'e ve `data/market`'e HİÇ yazmaz: bu modüldeki bütün yazım yardımcıları
hedef yolun araştırma kökünün içinde olduğunu her çağrıda denetler (`OutsideResearchRoot`). Çekirdekteki
`ReadOnlyPaths` ve S0 öz-denetimi bunun üstüne gelen ayrı savunma hatlarıdır.

Disk koruması (§2.6, §3.3, §9.4): araştırma kökü 15 GB'ta UYARI, 20 GB'ta çalışmayı REDDET; dosya sisteminde boş alan
10 GB'ın altındaysa REDDET. GB = 10⁹ bayt (belgedeki sınırlar ondalık GB'dır).

Yazım sözleşmesi: geçici dosya hedefle AYNI klasörde yazılır, `fsync` edilir, `os.replace` ile yerine konur; yarım
yazılmış dosya asla hedef adla görünmez. `.jsonl.gz` segmentleri her yazımda bütün olarak yeniden yazılır (ekleme
gzip üyesi olarak yerinde yapılmaz: yarıda kesilen ekleme segmenti bozardı).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

#: Varsayılan veri kökü (VPS). Testler ve sahte VPS `TRADINGBOT_DATA` ile değiştirir.
DEFAULT_DATA_ROOT = Path("/opt/tradingbot/data")
RESEARCH_DIRNAME = "research"

GB = 1_000_000_000
#: §3.3: 15 GB'ta uyarı, 20 GB'ta çalışmayı reddet; §2.6: boş disk ≥ 10 GB, yoksa reddet.
WARN_RESEARCH_BYTES = 15 * GB
REFUSE_RESEARCH_BYTES = 20 * GB
MIN_FREE_BYTES = 10 * GB

DISK_OK, DISK_WARN, DISK_REFUSE = "OK", "WARN", "REFUSE"

#: Araştırma kökünün P1a alt ağaçları (§3.1). P1b+ ağaçları (store, archive_cache, ...) burada oluşturulmaz.
P1A_SUBDIRS = ("closes", "entries", "snapshots", "target", "summary", "runs", "backup", "locks")


class OutsideResearchRoot(RuntimeError):
    """Araştırma kökü dışına yazma girişimi — motorun bir hatasıdır; asla sessizce geçilmez."""


@dataclass(frozen=True)
class EnginePaths:
    """Motorun bütün yolları. `data` veri kökü, `state` worker state'i (SALT OKUNUR)."""

    data: Path
    state: Path

    # ------------------------------------------------------------------ kurucular
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EnginePaths":
        env = os.environ if env is None else env
        data = Path(env.get("TRADINGBOT_DATA") or DEFAULT_DATA_ROOT)
        state = Path(env.get("TRADINGBOT_STATE_DIR") or (data / "state"))
        return cls(data=data, state=state)

    @classmethod
    def for_state(cls, state: Path | str, data: Path | str | None = None) -> "EnginePaths":
        """Test/sahte VPS kolaylığı: veri kökü verilmezse state'in üst klasörü."""
        st = Path(state)
        return cls(data=Path(data) if data is not None else st.parent, state=st)

    # ------------------------------------------------------------------ kök düzeni (§3.1)
    @property
    def research(self) -> Path:
        return self.data / RESEARCH_DIRNAME

    @property
    def market(self) -> Path:
        """Worker'ın piyasa verisi (SALT OKUNUR; HistoryStore `market/history`)."""
        return self.data / "market"

    @property
    def closes(self) -> Path:
        return self.research / "closes"

    @property
    def entries(self) -> Path:
        return self.research / "entries"

    @property
    def snapshots(self) -> Path:
        return self.research / "snapshots"

    @property
    def target(self) -> Path:
        return self.research / "target"

    @property
    def target_daily(self) -> Path:
        return self.target / "daily.jsonl"

    @property
    def target_looks(self) -> Path:
        """Aylık kayıtlı bakış hükümleri (yalnız eklenir; §7.3)."""
        return self.target / "looks.jsonl"

    @property
    def summary(self) -> Path:
        return self.research / "summary"

    @property
    def runs(self) -> Path:
        return self.research / "runs"

    @property
    def backup(self) -> Path:
        return self.research / "backup"

    @property
    def locks(self) -> Path:
        return self.research / "locks"

    @property
    def data_lock(self) -> Path:
        return self.locks / "data.lock"

    @property
    def analysis_lock(self) -> Path:
        return self.locks / "analysis.lock"

    # ------------------------------------------------------------------ P1b veri deposu (§3.1; ağaçlar tembel oluşur)
    @property
    def store(self) -> Path:
        """`ResearchStore` kökü: `store/<market>/<SYM>/<tf>/<YYYY>/<MM>.parquet` (+ `.sha256`, `manifest.json`)."""
        return self.research / "store"

    @property
    def archive_cache(self) -> Path:
        """data.binance.vision aynası. Dosyalar `archive_cache/archive/<sunucu>/<url yolu>` altındadır: laboratuvarların
        `--cache <kök>` düzeni (`gold_lab.ArchiveCache` / `book_lab.ZipCache` kökün altına `archive/` ekler), böylece
        `--cache …/research/archive_cache --offline` bayt-özdeş dosyaları bulur (§3.5)."""
        return self.research / "archive_cache"

    @property
    def dukascopy(self) -> Path:
        return self.research / "dukascopy"

    @property
    def universe_dir(self) -> Path:
        return self.research / "universe"

    @property
    def exchangeinfo_dir(self) -> Path:
        return self.research / "exchangeinfo"

    @property
    def data_status(self) -> Path:
        """`engine-data` mührü ve seri durumu (şema `engine_data_status_v1`)."""
        return self.summary / "data_status.json"

    @property
    def data_runs(self) -> Path:
        """Veri çalıştırmalarının kaydı ve fark dosyaları: `runs/data/<run_id>/`. Gece biriminin `runs/<run_id>/`
        desenine uymaz; `night.prune_runs` ve S7b yedeği onlara dokunmaz (veri birimi kendi saklamasını yapar)."""
        return self.runs / "data"

    @property
    def worker_history(self) -> Path:
        """Worker'ın HistoryStore kökü (`data/market/history`; SALT OKUNUR, yalnız tohumlamada okunur)."""
        return self.market / "history"

    def book_closes_dir(self, book: str) -> Path:
        return self.closes / _safe_name(book)

    def book_entries_dir(self, book: str) -> Path:
        return self.entries / _safe_name(book)

    def snapshot_file(self, day: str) -> Path:
        return self.snapshots / f"{day}.json.gz"

    # ------------------------------------------------------------------ yazım denetimi
    def is_under_research(self, path: Path | str) -> bool:
        try:
            Path(os.path.abspath(path)).relative_to(os.path.abspath(self.research))
        except ValueError:
            return False
        return True

    def require_research(self, path: Path | str) -> Path:
        p = Path(path)
        if not self.is_under_research(p):
            raise OutsideResearchRoot(f"araştırma kökü dışına yazma reddedildi: {p} (kök {self.research})")
        return p

    def ensure_tree(self) -> None:
        """P1a alt ağaçlarını oluştur (yalnız araştırma kökü altında)."""
        for sub in P1A_SUBDIRS:
            (self.require_research(self.research / sub)).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ atomik yazım
    def write_bytes(self, path: Path | str, data: bytes) -> Path:
        p = self.require_research(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return p

    def write_text(self, path: Path | str, text: str) -> Path:
        return self.write_bytes(path, text.encode("utf-8"))

    def write_json(self, path: Path | str, obj: Any) -> Path:
        return self.write_text(path, json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n")

    def write_json_gz(self, path: Path | str, obj: Any) -> Path:
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
        return self.write_bytes(path, gzip_bytes(raw))

    def write_jsonl_gz(self, path: Path | str, rows: Iterable[Mapping[str, Any]]) -> Path:
        buf = io.StringIO()
        for r in rows:
            buf.write(json_line(r))
        return self.write_bytes(path, gzip_bytes(buf.getvalue().encode("utf-8")))

    def write_gzip_stream(self, path: Path | str, chunks: Iterable[bytes]) -> str:
        """`chunks`ı (ham baytlar) deterministik gzip olarak (mtime=0, ad alanı yok, düzey 6) AKIŞLA yaz; bellekte bütün
        dosya kurulmaz. Atomik (aynı klasörde geçici dosya + `fsync` + `os.replace`). Dönen: SIKIŞTIRILMIŞ baytların
        sha256'sı. Aynı ham içerik `gzip_bytes` ile aynı baytları verir (deflate çıktısı parçalamadan bağımsızdır)."""
        p = self.require_research(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        h = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as fh:
                sink = _HashingSink(fh, h)
                with gzip.GzipFile(filename="", fileobj=sink, mode="wb", mtime=0, compresslevel=6) as gz:
                    for c in chunks:
                        if c:
                            gz.write(c)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return h.hexdigest()

    def append_jsonl(self, path: Path | str, rows: Iterable[Mapping[str, Any]]) -> int:
        """Düz `.jsonl` dosyasına satır ekle (hedef günlük satırları gibi küçük, yalnız-eklenen dosyalar). Bütün dosya
        yeniden yazılır (atomik); var olan satırlar AYNEN korunur. Dönen: eklenen satır sayısı."""
        p = self.require_research(path)
        new = "".join(json_line(r) for r in rows)
        if not new:
            return 0
        old = p.read_bytes() if p.exists() else b""
        if old and not old.endswith(b"\n"):
            old += b"\n"
        self.write_bytes(p, old + new.encode("utf-8"))
        return new.count("\n")


class _HashingSink:
    """Yazılan baytları hem dosyaya yazan hem sha256'ya ekleyen en küçük dosya benzeri (GzipFile için; `name` yok)."""

    def __init__(self, fh, h) -> None:
        self._fh, self._h = fh, h

    def write(self, b) -> int:
        self._h.update(b)
        return self._fh.write(b)

    def flush(self) -> None:
        self._fh.flush()


def _safe_name(book: str) -> str:
    s = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(book))
    return s or "_"


def json_line(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n"


def gzip_bytes(raw: bytes) -> bytes:
    """Deterministik gzip (mtime=0): aynı içerik aynı baytları verir (yedek/sha karşılaştırmaları için)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=6) as gz:
        gz.write(raw)
    return buf.getvalue()


def read_jsonl_gz(path: Path | str) -> Iterator[dict]:
    """`.jsonl.gz` satırlarını sırayla verir. Bozuk satır `ValueError` ile durur (sessiz atlama yok)."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}: satır {n} çözülemedi: {exc}") from exc


def read_jsonl(path: Path | str) -> Iterator[dict]:
    p = Path(path)
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{p}: satır {n} çözülemedi: {exc}") from exc


def read_json_gz(path: Path | str) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_size_bytes(root: Path | str) -> int:
    """Klasör ağacının toplam dosya boyutu (sembolik bağlar izlenmez)."""
    total = 0
    stack = [Path(root)]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def disk_guard(paths: EnginePaths, *, warn_bytes: int = WARN_RESEARCH_BYTES, refuse_bytes: int = REFUSE_RESEARCH_BYTES,
               min_free_bytes: int = MIN_FREE_BYTES, research_bytes: int | None = None,
               free_bytes: int | None = None) -> dict[str, Any]:
    """Disk koruması: `REFUSE` dönerse çalıştırma o gece hiçbir şey yazmadan durur (S0). `research_bytes` /
    `free_bytes` testler için verilebilir; verilmezse ölçülür."""
    used = dir_size_bytes(paths.research) if research_bytes is None else int(research_bytes)
    if free_bytes is None:
        probe = paths.research if paths.research.exists() else paths.data
        try:
            st = os.statvfs(probe)
            free = st.f_bavail * st.f_frsize
        except (OSError, AttributeError):
            free = None
    else:
        free = int(free_bytes)
    status, reasons = DISK_OK, []
    if used >= refuse_bytes:
        status = DISK_REFUSE
        reasons.append(f"araştırma kökü {used / GB:.2f} GB ≥ {refuse_bytes / GB:.0f} GB")
    elif used >= warn_bytes:
        status = DISK_WARN
        reasons.append(f"araştırma kökü {used / GB:.2f} GB ≥ {warn_bytes / GB:.0f} GB (uyarı)")
    if free is not None and free < min_free_bytes:
        status = DISK_REFUSE
        reasons.append(f"boş disk {free / GB:.2f} GB < {min_free_bytes / GB:.0f} GB")
    return {"status": status, "research_bytes": used, "free_bytes": free, "warn_bytes": warn_bytes,
            "refuse_bytes": refuse_bytes, "min_free_bytes": min_free_bytes, "reasons": reasons}


__all__ = ["DEFAULT_DATA_ROOT", "DISK_OK", "DISK_REFUSE", "DISK_WARN", "EnginePaths", "GB", "MIN_FREE_BYTES",
           "OutsideResearchRoot", "P1A_SUBDIRS", "REFUSE_RESEARCH_BYTES", "WARN_RESEARCH_BYTES", "dir_size_bytes",
           "disk_guard", "gzip_bytes", "json_line", "read_json_gz", "read_jsonl", "read_jsonl_gz", "sha256_file"]

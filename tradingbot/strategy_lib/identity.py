"""P3b — varyant kimliğinin kod parçası: `module_src_sha` (docs/ENGINE_BRAIN_V1.md §2.1 `spec_sha` alanı).

`module_src_sha(variant_id)` = sha256( "strategy_lib.module_src_sha.v2\\n" + sıralı "<depo-göreli yol>\\t<sha256(blob
baytları)>\\n" satırları ). Dosya kümesi BİLDİRİLMİŞTİR (import kapanışı DEĞİL; v1'in 37–43 dosyalık kapanışı ilgisiz
düzenlemelerde yeni `spec_sha` üretiyor ve deneme sayısını şişiriyordu — inceleme bulgusu m3):

* varyant modülü ve onun `strategy_lib` İÇİNDEKİ bağımlılıkları (AST, yalnız `tradingbot.strategy_lib.*`: `contract`,
  `livebars`, Formasyonda `k_trend`);
* varyant modülünün `LIVE_MODULES` listesi (sınıfın `live_modules`'u) — kuralın karar yolunda ÇALIŞAN ya da kararı
  belirleyen tanım/sabitleri taşıyan canlı modüller; her modülün gerekçesi listenin yanındadır. Liste, kopyaların
  `signals`/`manage` çalışırken yürüyen `tradingbot` modüllerinin (çalışma izi testi) ÜST kümesidir;
* O örtüsünde (P3b-2b) ayrıca TABAN kopyanın modülü, onun paket içi bağımlılıkları ve `LIVE_MODULES`'u
  (`catalog.extra_modules`): örtünün kümesi = faktör modülü (+ `o_cover`, `stream`, …) ∪ taban modülü.

Baytlar çalışma ağacından değil **git'in HEAD blob'larından** okunur (`git ls-tree` + `git cat-file --batch`); bu
dosyalardan biri HEAD'e göre değişmiş, izlenmeyen ya da HEAD'de yoksa `DirtyTreeError` (kimlik hesaplanmaz: işlenmemiş
düzenleme deneme sayılmaz). Böylece bildirilmemiş bir modülün düzenlenmesi kimliği değiştirmez, bildirilmiş birininki
değiştirir (test). Git yoksa da hata (kimlik doğrulanamaz).

`spec_sha`'nın kendisi bu pakette HESAPLANMAZ: kanonik biçim mühürlü kaydın fonksiyonudur ve paket motoru import etmez;
motor tarafı `kernel.spec_sha_for` aynı fonksiyonla (`registry.spec_sha(registry.lib_spec(…))`) hesaplar.

Bu modül paketin TEK dışarıyla konuşan yeridir: salt-okunur git komutları (`status`, `ls-tree`, `cat-file`); saat, ağ,
rastgelelik ve yazma yok. Kural fonksiyonları bu modülü çağırmaz.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
from functools import lru_cache
from importlib import import_module
from pathlib import Path

from .catalog import extra_modules, module_name

VERSION = "strategy_lib.module_src_sha.v2"
TB_DIR = Path(__file__).resolve().parents[1]          # tradingbot/
REPO_ROOT = TB_DIR.parent
ROOT_PKG = TB_DIR.name                                # "tradingbot"
LIB_PKG = f"{ROOT_PKG}.strategy_lib"
_GIT = ("git", "-c", "core.quotepath=off", "-c", "log.showSignature=false")


class DirtyTreeError(RuntimeError):
    """Kimliğe giren bir dosya HEAD'den farklı / izlenmiyor / HEAD'de yok, ya da git okunamıyor."""


def _file_of(mod: str) -> Path | None:
    parts = mod.split(".")
    if not parts or parts[0] != ROOT_PKG:
        return None
    base = TB_DIR.joinpath(*parts[1:])
    for p in (base.with_suffix(".py"), base / "__init__.py"):
        if p.is_file():
            return p
    return None


def _lib_imports(mod: str) -> set[str]:
    """`mod`'un modül ve fonksiyon düzeyindeki import'larından yalnız `strategy_lib` içindekiler (paket `__init__` hariç)."""
    f = _file_of(mod)
    if f is None:
        return set()
    tree = ast.parse(f.read_bytes())
    pkg = mod.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = pkg.split(".")
                if node.level > 1:
                    anchor = anchor[:-(node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            names = [f"{base}.{a.name}" if _file_of(f"{base}.{a.name}") is not None else base for a in node.names]
        out.update(n for n in names if n.startswith(LIB_PKG + ".") and _file_of(n) is not None)
    return out


@lru_cache(maxsize=None)
def lib_closure(start: str) -> tuple[str, ...]:
    """`start` modülünden ulaşılan `strategy_lib` modülleri (paket `__init__`'i hariç; sıralı)."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(sorted(_lib_imports(m) - seen))
    return tuple(sorted(seen))


def declared_modules(variant_id: str) -> tuple[str, ...]:
    """Kimliğe giren modüller: varyant modülü + paket içi bağımlılıkları + modülün `LIVE_MODULES` listesi (örtüde ayrıca
    taban modülünün aynıları)."""
    mods: set[str] = set()
    for start in (module_name(variant_id),) + tuple(extra_modules(variant_id)):
        mods |= set(lib_closure(start)) | set(getattr(import_module(start), "LIVE_MODULES"))
    missing = sorted(m for m in mods if _file_of(m) is None)
    if missing:
        raise ValueError(f"{variant_id}: bildirilen modülün dosyası yok: {missing}")
    return tuple(sorted(mods))


def module_files(variant_id: str) -> tuple[str, ...]:
    """Kimliğe giren dosyalar (depo-göreli, sıralı)."""
    return tuple(sorted(_file_of(m).relative_to(REPO_ROOT).as_posix() for m in declared_modules(variant_id)))


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        cp = subprocess.run([*_GIT, "-C", str(root), *args], input=stdin, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DirtyTreeError(f"git çalıştırılamadı ({exc}); kimlik doğrulanamaz") from None
    if cp.returncode != 0:
        raise DirtyTreeError(f"git {' '.join(args[:2])} başarısız: {cp.stderr.decode('utf-8', 'replace')[-300:]}")
    return cp.stdout


_BLOBS: dict[str, bytes] = {}


def _blobs(root: Path, ids: list[str]) -> dict[str, bytes]:
    """Blob kimliği → baytlar (`git cat-file --batch`; blob içeriği kimliğiyle sabittir, süreç içi önbellek güvenli)."""
    need = [i for i in dict.fromkeys(ids) if i not in _BLOBS]
    if need:
        out = _git(root, "cat-file", "--batch", stdin=("\n".join(need) + "\n").encode("ascii"))
        pos = 0
        for i in need:
            nl = out.index(b"\n", pos)
            head = out[pos:nl].split()
            if len(head) != 3 or head[0].decode("ascii") != i or head[1] != b"blob":
                raise DirtyTreeError(f"git cat-file beklenmeyen çıktı: {out[pos:nl][:120]!r}")
            size = int(head[2])
            _BLOBS[i] = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
    return {i: _BLOBS[i] for i in ids}


def module_src_sha(variant_id: str, *, root: Path | str | None = None) -> str:
    """Bildirilmiş dosyaların HEAD blob'larının sha256 özeti; 64 küçük harf hex. `root`: depo kökü (varsayılan bu
    paketin deposu; test başka bir depo verir). Dosyalardan biri kirli / izlenmiyor / HEAD'de yoksa `DirtyTreeError`."""
    rootp = Path(root) if root is not None else REPO_ROOT
    files = list(module_files(variant_id))
    dirty = _git(rootp, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *files)
    if dirty.strip(b"\0"):
        bad = sorted({e.decode("utf-8", "replace")[3:] for e in dirty.split(b"\0") if len(e) > 3})
        raise DirtyTreeError(f"{variant_id}: kimliğe giren dosyalar HEAD'den farklı ya da izlenmiyor: {bad}")
    blob_of: dict[str, str] = {}
    for ent in _git(rootp, "ls-tree", "-z", "HEAD", "--", *files).split(b"\0"):
        if not ent:
            continue
        meta, path = ent.split(b"\t", 1)
        _mode, typ, oid = meta.decode("ascii").split()
        if typ == "blob":
            blob_of[path.decode("utf-8")] = oid
    missing = sorted(set(files) - set(blob_of))
    if missing:
        raise DirtyTreeError(f"{variant_id}: kimliğe giren dosyalar HEAD'de yok: {missing}")
    data = _blobs(rootp, [blob_of[f] for f in files])
    lines = sorted(f"{f}\t{hashlib.sha256(data[blob_of[f]]).hexdigest()}\n" for f in files)
    return hashlib.sha256((VERSION + "\n" + "".join(lines)).encode("utf-8")).hexdigest()


__all__ = ["DirtyTreeError", "REPO_ROOT", "VERSION", "declared_modules", "lib_closure", "module_files", "module_src_sha"]

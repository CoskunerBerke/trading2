"""P3b — varyant kimliğinin kod parçası: `module_src_sha` (docs/ENGINE_BRAIN_V1.md §2.1 `spec_sha` alanı).

`module_src_sha(variant_id)` = sha256( "strategy_lib.module_src_sha.v1\\n" + sıralı "<depo-göreli yol>\\t<sha256(dosya
baytları)>\\n" satırları ). Dosya kümesi, varyantın modülünden başlayan `tradingbot.*` import KAPANIŞIDIR (modül ve
fonksiyon düzeyindeki bütün `import`/`from … import` satırları, AST ile; paket `__init__`'i yalnız doğrudan import
edildiğinde). Böylece kopyanın çağırdığı canlı kural fonksiyonlarının (ve onların yardımcılarının) kaynağı da kimliğe
girer: canlı kural değişirse `spec_sha` değişir (yeni deneme), değişmezse aynı kalır (tekilleşir). Kapanış bilerek geniştir
(fazla örtmek, davranış değişikliğini kaçırmaktan iyidir).

`spec_sha`'nın kendisi bu pakette HESAPLANMAZ: kanonik biçim mühürlü kaydın fonksiyonudur ve paket motoru import etmez;
motor tarafı `kernel.spec_sha_for` aynı fonksiyonla (`registry.spec_sha(registry.lib_spec(…))`) hesaplar (testte deneme
defterinin `seal_entries`'iyle eşitlik).

Bu modül paketin TEK okuma yapan yeridir: yalnız depo içindeki `.py` kaynaklarını okur (saat, ağ, rastgelelik, yazma
yok; AST testi bunu sınar). Kural fonksiyonları bu modülü çağırmaz.
"""
from __future__ import annotations

import ast
import hashlib
from functools import lru_cache
from pathlib import Path

from .catalog import module_name

VERSION = "strategy_lib.module_src_sha.v1"
TB_DIR = Path(__file__).resolve().parents[1]          # tradingbot/
ROOT_PKG = TB_DIR.name                                # "tradingbot"


def _file_of(mod: str) -> Path | None:
    parts = mod.split(".")
    if not parts or parts[0] != ROOT_PKG:
        return None
    base = TB_DIR.joinpath(*parts[1:])
    for p in (base.with_suffix(".py"), base / "__init__.py"):
        if p.is_file():
            return p
    return None


def _is_pkg(mod: str) -> bool:
    f = _file_of(mod)
    return f is not None and f.name == "__init__.py"


def _imports_of(mod: str) -> set[str]:
    f = _file_of(mod)
    if f is None:
        return set()
    tree = ast.parse(f.read_bytes())
    pkg = mod if _is_pkg(mod) else mod.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.split(".")[0] == ROOT_PKG)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = pkg.split(".")
                if node.level > 1:
                    anchor = anchor[:-(node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base.split(".")[0] != ROOT_PKG:
                continue
            for a in node.names:
                sub = f"{base}.{a.name}"
                out.add(sub if _file_of(sub) is not None else base)
    return {m for m in out if _file_of(m) is not None}


@lru_cache(maxsize=None)
def module_closure(start: str) -> tuple[str, ...]:
    """`start` modülünden ulaşılan `tradingbot.*` modülleri (sıralı)."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        m = todo.pop()
        if m in seen or _file_of(m) is None:
            continue
        seen.add(m)
        todo.extend(sorted(_imports_of(m) - seen))
    return tuple(sorted(seen))


@lru_cache(maxsize=None)
def _sha_of_module(start: str) -> str:
    lines = []
    for m in module_closure(start):
        f = _file_of(m)
        rel = f.relative_to(TB_DIR.parent).as_posix()
        lines.append(f"{rel}\t{hashlib.sha256(f.read_bytes()).hexdigest()}\n")
    return hashlib.sha256((VERSION + "\n" + "".join(sorted(lines))).encode("utf-8")).hexdigest()


def module_src_sha(variant_id: str) -> str:
    """Varyantın modül kaynağının (import kapanışıyla) sha256'sı; 64 küçük harf hex."""
    return _sha_of_module(module_name(variant_id))


def module_files(variant_id: str) -> tuple[str, ...]:
    """Kimliğe giren dosyalar (depo-göreli), gösterim/test için."""
    return tuple(sorted(_file_of(m).relative_to(TB_DIR.parent).as_posix()
                        for m in module_closure(module_name(variant_id))))


__all__ = ["VERSION", "module_closure", "module_files", "module_src_sha"]

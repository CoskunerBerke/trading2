"""P3a — deneme defteri `trials/trials.jsonl` (docs/ENGINE_BRAIN_V1.md §3.5, §2.6, §4.4; ana belge §6.4).

* **Yalnız eklenir, atomik.** Ekleme = eski baytlar + yeni satırlar AYNI klasörde geçici dosyaya yazılır, `fsync` edilir,
  geri okunur; eski baytların TAM önek olduğu ve hedefin bu arada değişmediği doğrulanır; sonra `os.replace` + klasör
  `fsync`. Yarıda kalan yazım (OOM/SIGKILL/ENOSPC/elektrik) hedefi hiç değiştirmez (paths.py atomik yazım sözleşmesi).
  Modülde var olan baytları değiştiren başka HİÇBİR yol yoktur (AST testi: tek `os.replace`, `_atomic_replace` içinde;
  silme yalnız kendi geçici dosyası): düzeltme bile yeni bir satırdır.
* **Hash zinciri.** `row_sha` = sha256(kanonik satır − `row_sha`), `prev_sha` = önceki satırın `row_sha`'sı (ilk satırda
  `GENESIS`); `seq` 1'den ardışık. Satırın baytları kanonik JSON'un KENDİSİDİR (boşluk/sıra oynaması da kırıktır); `seq`
  ve sayaçlar düz tam sayı, `prev_sha`/`row_sha` 64 hex; NaN/sonsuz/yalnız vekil (surrogate) değer kırıktır.
* **Sayaçlar hiç azalmaz** — dosya içinde zincirle, dosyalar arasında yüksek su işaretiyle (HWM): her eklemeden sonra
  `trials/trials.hwm.json` (satır, baş sha, sayaçlar) atomik yazılır; `verify_chain(expect=HWM)` dosya kısaldıysa ya da
  HWM'nin başı zincirde değilse `ROLLED_BACK` döndürür (yedekten eski dosya, disk hatası, elle kesme). HWM'nin gece
  özetine kopyası P3g'dedir (defterle birlikte geri yüklenen HWM'yi de yakalar). `N_raw_cum` = benzersiz (LIB ailesi,
  `spec_sha`, kapsam) `SPEC`/`OPTION` + `PRIOR` sayıları; `N_lesson_cum` = `LESSON_LOOK` satırları; `N_explore_cum` =
  `EXPLORE_SEEN` seri sayıları. DSR'nin N'i bu üçünün toplamıdır (o anki değer). Bakışlar N'i büyütmez.
* **Tekilleştirme.** Aynı (LIB AİLESİ, `spec_sha`, kapsam) ikinci kez deneme sayılmaz (`DUPLICATE`, satır yazılmaz): LIB
  ailesi = `lib_version`'ın ana sürümü (`LIB_v1` ve aylık `LIB_v1.<AAAA-AA>` aynı aile; §4.3 T3/T4 "spec_sha aynıysa o
  satıra bağlanır"); yeni ana sürüm (`LIB_v2`) ayrı ailedir. Farklı kapsam farklı satırdır. `PRIOR` `row_id` başına bir
  kez; bakışlar (`row_id`, `look_no`) başına bir kez.
* **Doğrulama.** `verify_chain` dosyayı baştan sınar ve her kurcalama türünü bir kodla adlandırır (`BROKEN_CODES`); hiçbir
  girdi istisna fırlatmaz. Yarım son satırda kodu `TORN_TAIL`, sayaçlar/baş tam satırların doğrulanmış önekinindir. Kırık
  zincire EKLENMEZ (`TrialsChainBroken`); gece S6'da `TRIALS_CHAIN_BROKEN` → kapı ve terfi o gece kapalı.
* **Kilit.** Yazarlar `trials/trials.lock` üzerinde `flock(LOCK_EX)` alır; yeniden doğrulama ve yazım kilit içindedir.
  Yükleme ile ekleme arasında dosya değiştiyse ekleme reddedilir (`TrialsChanged`). `engine-query trials` salt-okunurdur,
  kilit almaz.

Satır alanları (§3.5, tam liste `ROW_KEYS`): `PRIOR` ve `EXPLORE_SEEN` satırlarında `n` sayılan deneme/seri sayısıdır;
`ELIG`'de §2.4-1'in giriş sayısıdır. Yalnız standart kütüphane; ağ yok.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from . import registry as RG
from .ledgers import iso, utc_now
from .paths import EnginePaths

SCHEMA = "trials_v1"
HWM_SCHEMA = "trials_hwm_v1"
GENESIS = "0" * 64
KINDS: tuple[str, ...] = ("PRIOR", "SPEC", "OPTION", "ELIG", "SELECT", "LOOK_A", "FWD_READ", "BIRTH", "LOOK_B", "STOP_B",
                          "HOLM", "PROMOTE", "RETIRE", "LESSON_LOOK", "EXPLORE_SEEN")
ROW_KEYS: tuple[str, ...] = (
    "seq", "prev_sha", "row_sha", "kind", "at", "lib_version", "registry_sha", "data_seal", "code_sha", "spec_sha", "row_id",
    "scope", "params", "window", "n", "n_days", "mean_r_base", "mean_r_adverse", "mean_r_stress", "p_day", "q_bh", "dsr", "pbo",
    "placebo_diff_ci", "verdict", "look_no", "N_raw_cum", "N_lesson_cum", "N_explore_cum", "fwd_read")
CHAIN_KEYS = ("seq", "prev_sha", "row_sha", "N_raw_cum", "N_lesson_cum", "N_explore_cum")
INPUT_KEYS: tuple[str, ...] = tuple(k for k in ROW_KEYS if k not in CHAIN_KEYS)
COUNTERS = ("N_raw_cum", "N_lesson_cum", "N_explore_cum")
FLOAT_KEYS = ("mean_r_base", "mean_r_adverse", "mean_r_stress", "p_day", "q_bh", "dsr", "pbo")
#: `LIB_v<n>` ya da aylık türeyen mühür `LIB_v<n>.<AAAA-AA>` (§4.3)
LIB_VERSION_RE = re.compile(r"^LIB_v[1-9][0-9]*(\.[0-9]{4}-(0[1-9]|1[0-2]))?$")
#: tür → zorunlu alanlar
REQUIRED: Mapping[str, tuple[str, ...]] = {
    "PRIOR": ("row_id", "n"), "SPEC": ("lib_version", "spec_sha", "scope", "row_id"),
    "OPTION": ("lib_version", "spec_sha", "scope", "row_id"), "ELIG": ("lib_version", "row_id", "scope", "n"),
    "SELECT": ("lib_version", "row_id", "scope", "window"), "LOOK_A": ("row_id", "look_no"), "FWD_READ": ("row_id", "look_no"),
    "BIRTH": ("row_id",), "LOOK_B": ("row_id", "look_no"), "STOP_B": ("row_id",), "HOLM": ("row_id", "window"),
    "PROMOTE": ("row_id",), "RETIRE": ("row_id",), "LESSON_LOOK": ("row_id", "look_no"), "EXPLORE_SEEN": ("lib_version", "row_id", "n"),
}
#: `report.bound` ile aynı sözleşme (§8: ≤ 150 satır, ≤ 4 KB); report pandas'lı grafiği çektiği için burada yinelenir
QUERY_MAX_LINES, QUERY_MAX_BYTES = 150, 4096
QUERY_SHOW_ROWS = 40
OK, EMPTY = "OK", "EMPTY"
BROKEN_CODES = ("TORN_TAIL", "BAD_JSON", "NOT_CANONICAL", "BAD_SCHEMA", "BAD_SEQ", "BAD_PREV", "BAD_ROW_SHA", "BAD_COUNTER",
                "DUPLICATE", "ROLLED_BACK", "BAD_HWM")


class TrialsChainBroken(RuntimeError):
    """Zincir doğrulanmadı: eklenmez (gece: `TRIALS_CHAIN_BROKEN`, kapı ve terfi kapalı)."""


class TrialsChanged(RuntimeError):
    """Yüklemeden sonra dosya değişti (başka yazar): eklenmez; yeniden yükleyip tekrar denenmeli."""


class TrialRowError(ValueError):
    """Satır şeması/zorunlu alanı tutmuyor."""


class TrialsWriteError(RuntimeError):
    """Atomik yazımın denetimi tutmadı (geri okuma / önek): hedef değişmedi."""


# ============================================================================ satır kuralları
def line_bytes(row: Mapping[str, Any]) -> bytes:
    return RG.canonical_bytes(row) + b"\n"


def row_sha(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(RG.canonical_bytes({k: v for k, v in row.items() if k != "row_sha"})).hexdigest()


def lib_family(lib_version: str) -> str:
    """`LIB_v1.2026-11` → `LIB_v1` (aylık türeyen mühürler ana sürümün ailesidir)."""
    return lib_version.split(".", 1)[0]


def dedup_key(row: Mapping[str, Any]) -> tuple | None:
    k = row["kind"]
    if k in ("SPEC", "OPTION"):
        return ("TRIAL", lib_family(row["lib_version"]), row["spec_sha"], row["scope"])
    if k == "PRIOR":
        return ("PRIOR", row["row_id"])
    if k == "ELIG":
        return ("ELIG", row["lib_version"], row["row_id"], row["scope"])
    if k == "EXPLORE_SEEN":
        return ("EXPLORE_SEEN", row["lib_version"], row["row_id"])
    if k in ("LOOK_A", "FWD_READ", "LOOK_B", "LESSON_LOOK"):
        return (k, row["row_id"], row["look_no"])
    if k in ("BIRTH", "STOP_B", "PROMOTE", "RETIRE"):
        return (k, row["row_id"])
    if k == "HOLM":
        return (k, row["row_id"], RG.canonical_bytes(row["window"]))
    if k == "SELECT":
        return (k, row["lib_version"], row["row_id"], row["scope"], RG.canonical_bytes(row["window"]))
    return None


def increments(row: Mapping[str, Any]) -> tuple[int, int, int]:
    """(N_raw, N_lesson, N_explore) artışı. Bakış satırları N'i büyütmez."""
    k = row["kind"]
    if k == "PRIOR":
        return int(row["n"]), 0, 0
    if k in ("SPEC", "OPTION"):
        return 1, 0, 0
    if k == "LESSON_LOOK":
        return 0, 1, 0
    if k == "EXPLORE_SEEN":
        return 0, 0, int(row["n"])
    return 0, 0, 0


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def validate_input(entry: Mapping[str, Any]) -> None:
    unknown = set(entry) - set(INPUT_KEYS)
    if unknown:
        raise TrialRowError(f"bilinmeyen alan(lar): {sorted(unknown)} (zincir alanlarını defter yazar)")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise TrialRowError(f"kind {kind!r} ∉ {KINDS}")
    for f in REQUIRED[kind]:
        if entry.get(f) is None:
            raise TrialRowError(f"{kind}: {f} zorunlu")
    if entry.get("spec_sha") is not None and not RG.is_sha256(entry["spec_sha"]):
        raise TrialRowError("spec_sha 64 karakterlik küçük harf hex olmalı")
    for f in ("n", "n_days", "look_no"):
        v = entry.get(f)
        if v is not None and (not _is_int(v) or v < 0):
            raise TrialRowError(f"{f} negatif olmayan tam sayı olmalı")
    if entry.get("look_no") is not None and entry["look_no"] < 1:
        raise TrialRowError("look_no ≥ 1")
    for f in FLOAT_KEYS:
        v = entry.get(f)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v))):
            raise TrialRowError(f"{f} sonlu sayı olmalı")
    for f in ("lib_version", "registry_sha", "data_seal", "code_sha", "row_id", "scope", "verdict", "at"):
        v = entry.get(f)
        if v is not None and not isinstance(v, str):
            raise TrialRowError(f"{f} metin olmalı")
    if entry.get("lib_version") is not None and not LIB_VERSION_RE.match(entry["lib_version"]):
        raise TrialRowError("lib_version 'LIB_v<n>' ya da 'LIB_v<n>.<AAAA-AA>' olmalı")
    try:
        RG.canonical_bytes(dict(entry))                   # JSON'a sığmayan değer (NaN, nesne, yalnız vekil) burada düşer
    except UnicodeEncodeError as exc:
        raise TrialRowError(f"UTF-8'e kodlanamayan metin: {exc.reason}") from None


def _chain_types_ok(row: Mapping[str, Any]) -> str | None:
    """Zincir alanlarının türleri: `seq` ve sayaçlar düz tam sayı (bool/float değil), sha'lar 64 hex."""
    if not _is_int(row["seq"]) or row["seq"] < 1:
        return "seq düz pozitif tam sayı değil"
    for c in COUNTERS:
        if not _is_int(row[c]) or row[c] < 0:
            return f"{c} düz negatif olmayan tam sayı değil"
    for c in ("prev_sha", "row_sha"):
        if not RG.is_sha256(row[c]):
            return f"{c} 64 karakterlik küçük harf hex değil"
    return None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"JSON dışı sabit {name}")


def _finite_float(text: str) -> float:
    f = float(text)
    if not math.isfinite(f):
        raise ValueError(f"sonlu olmayan sayı {text[:24]}")
    return f


def strict_loads(line: bytes) -> Any:
    """Katı JSON: UTF-8, NaN/Infinity/sonsuza taşan sayı yok (ValueError)."""
    return json.loads(line.decode("utf-8"), parse_constant=_reject_constant, parse_float=_finite_float)


# ============================================================================ doğrulama
def _result(ok: bool, code: str, rows: int, head: str, counters: tuple[int, int, int], *, bad_seq: int | None = None,
            detail: str = "", kinds: dict | None = None, size: int = 0, keys: dict | None = None) -> dict:
    return {"ok": ok, "code": code, "rows": rows, "head_sha": head, "bad_seq": bad_seq, "detail": detail,
            "counters": dict(zip(COUNTERS, counters)), "kinds": kinds or {}, "size": size, "_keys": keys or {}}


def _verify_lines(body: bytes, size: int, mark_seq: int) -> tuple[dict, tuple[str, tuple] | None]:
    """Tam satırların (her biri `\n` ile biten) zinciri. `mark_seq`: o satırın (row_sha, sayaçlar)'ı da döner (HWM)."""
    prev, cnt, keys, kinds = GENESIS, (0, 0, 0), {}, {}
    mark = None

    def broken(code: str, i: int, detail: str) -> tuple[dict, None]:
        return _result(False, code, i - 1, prev, cnt, bad_seq=i, detail=detail[:160], kinds=kinds, size=size), None

    for i, line in enumerate(body.split(b"\n")[:-1], start=1):
        try:
            row = strict_loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            return broken("BAD_JSON", i, str(exc))
        if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(ROW_KEYS)) or row.get("kind") not in KINDS:
            return broken("BAD_SCHEMA", i, "alan kümesi/kind")
        bad_type = _chain_types_ok(row)
        if bad_type:
            return broken("BAD_SCHEMA", i, bad_type)
        try:
            canon = line_bytes(row)
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            return broken("NOT_CANONICAL", i, f"kanonik kodlanamıyor: {type(exc).__name__}")
        if line + b"\n" != canon:
            return broken("NOT_CANONICAL", i, "bayt kanonik değil")
        if row["seq"] != i:
            return broken("BAD_SEQ", i, f"seq {row['seq']} ≠ {i}")
        if row["prev_sha"] != prev:
            return broken("BAD_PREV", i, "prev_sha önceki row_sha değil")
        if row["row_sha"] != row_sha(row):
            return broken("BAD_ROW_SHA", i, "row_sha içerikle tutmuyor")
        try:
            validate_input({k: row[k] for k in INPUT_KEYS})
            inc = increments(row)
            key = dedup_key(row)
        except (TrialRowError, KeyError, TypeError, ValueError) as exc:
            return broken("BAD_SCHEMA", i, str(exc))
        if key is not None and key in keys:
            return broken("DUPLICATE", i, f"tekil anahtar tekrarı {key[:2]}")
        new = tuple(a + b for a, b in zip(cnt, inc))
        if tuple(row[c] for c in COUNTERS) != new:
            return broken("BAD_COUNTER", i, f"sayaçlar {tuple(row[c] for c in COUNTERS)} ≠ yeniden hesap {new}")
        if key is not None:
            keys[key] = i
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        prev, cnt = row["row_sha"], new
        if i == mark_seq:
            mark = (prev, cnt)
    rows = body.count(b"\n")
    return _result(True, OK if rows else EMPTY, rows, prev, cnt, kinds=kinds, size=size, keys=keys), mark


def hwm_problem(hwm: Any) -> str | None:
    """HWM kaydının biçim hatası (yoksa None)."""
    if not isinstance(hwm, dict) or hwm.get("schema") != HWM_SCHEMA:
        return "HWM şeması tanınmıyor"
    if not _is_int(hwm.get("rows")) or hwm["rows"] < 0 or not RG.is_sha256(hwm.get("head_sha")):
        return "HWM rows/head_sha geçersiz"
    c = hwm.get("counters")
    if not isinstance(c, dict) or set(c) != set(COUNTERS) or not all(_is_int(c[k]) and c[k] >= 0 for k in COUNTERS):
        return "HWM sayaçları geçersiz"
    return None


def verify_bytes(raw: bytes, *, expect: Mapping[str, Any] | None = None) -> dict:
    """Bütün zinciri sına; hiçbir girdi istisna fırlatmaz. Dönen: `ok`, `code` (OK/EMPTY ya da `BROKEN_CODES`), `rows`,
    `head_sha`, `bad_seq`, sayaçlar (kırıkta: ilk kırık satırdan önceki doğrulanmış önekin). `expect` = HWM
    (`{"rows", "head_sha", "counters"}`): dosya kısaldıysa ya da HWM'nin başı zincirde değilse `ROLLED_BACK`."""
    size = len(raw)
    cut = raw.rfind(b"\n") + 1
    body, torn = raw[:cut], raw[cut:]
    if expect is not None:
        bad = hwm_problem(expect)
        if bad:
            return _result(False, "BAD_HWM", 0, GENESIS, (0, 0, 0), detail=bad, size=size)
    mark_seq = int(expect["rows"]) if expect is not None else 0
    st, mark = _verify_lines(body, size, mark_seq)
    if not st["ok"] and st["code"] != EMPTY:
        return st
    if torn:
        return _result(False, "TORN_TAIL", st["rows"], st["head_sha"], tuple(st["counters"][c] for c in COUNTERS),
                       bad_seq=st["rows"] + 1, detail="son satır yarım (yeni satır yok); sayaçlar tam satırlarındır",
                       kinds=st["kinds"], size=size)
    if expect is not None and expect["rows"] > 0:
        want = (expect["head_sha"], tuple(expect["counters"][c] for c in COUNTERS))
        if st["rows"] < expect["rows"]:
            return dict(st, ok=False, code="ROLLED_BACK", bad_seq=st["rows"] + 1, _keys={},
                        detail=f"defter kısaldı: {st['rows']} satır < HWM {expect['rows']} (sayaçlar azalmış olur)")
        if mark != want:
            return dict(st, ok=False, code="ROLLED_BACK", bad_seq=expect["rows"], _keys={},
                        detail=f"HWM'nin başı (satır {expect['rows']}) bu zincirde değil")
    return st


def read_raw(path: os.PathLike | str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return b""


def read_hwm(path: os.PathLike | str) -> tuple[dict | None, str | None]:
    """(HWM, hata). Dosya yoksa (None, None); okunamaz/geçersizse (None, neden)."""
    raw = read_raw(path)
    if not raw:
        return (None, None) if not os.path.exists(path) else (None, "HWM dosyası boş")
    try:
        hwm = strict_loads(raw.strip())
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        return None, f"HWM okunamadı: {str(exc)[:80]}"
    bad = hwm_problem(hwm)
    return (None, bad) if bad else (hwm, None)


def verify_chain(path: os.PathLike | str, *, expect: Mapping[str, Any] | None = None) -> dict:
    r = verify_bytes(read_raw(path), expect=expect)
    r.pop("_keys", None)
    return r


def verify_ledger(paths: EnginePaths) -> dict:
    """Defter + HWM yan dosyası birlikte (gece S6 ve sorgu bunu kullanır)."""
    hwm, err = read_hwm(paths.trials_hwm_file)
    if err:
        r = _result(False, "BAD_HWM", 0, GENESIS, (0, 0, 0), detail=err, size=len(read_raw(paths.trials_file)))
        r.pop("_keys", None)
        return r
    r = verify_chain(paths.trials_file, expect=hwm)
    r["hwm"] = hwm
    return r


def iter_rows(path: os.PathLike | str, *, lenient: bool = False) -> Iterator[dict]:
    """Satırlar (doğrulamasız). `lenient`: ayrıştırılamayan satır atlanır (kırık defterde salt-okunur sorgu için)."""
    for line in read_raw(path).split(b"\n"):
        if not line:
            continue
        try:
            row = strict_loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError):
            if lenient:
                continue
            raise
        if isinstance(row, dict) or not lenient:
            yield row


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ============================================================================ defter
class TrialsLedger:
    """`trials/trials.jsonl` yazarı. `load()` zinciri (ve HWM'yi) doğrular; `append()` yalnız sona ekler (atomik)."""

    def __init__(self, paths: EnginePaths) -> None:
        self.paths = paths
        self.path = paths.require_research(paths.trials_file)
        self.hwm_path = paths.require_research(paths.trials_hwm_file)
        self.lock_path = paths.require_research(paths.trials_lock_file)
        self._st: dict | None = None

    def _verify_now(self) -> dict:
        hwm, err = read_hwm(self.hwm_path)
        if err:
            raise TrialsChainBroken(f"TRIALS_CHAIN_BROKEN BAD_HWM: {err}")
        st = verify_bytes(read_raw(self.path), expect=hwm)
        if not st["ok"] and st["code"] != EMPTY:
            raise TrialsChainBroken(f"TRIALS_CHAIN_BROKEN {st['code']} satır {st['bad_seq']}: {st['detail']}")
        return st

    def load(self) -> dict:
        self._st = self._verify_now()
        return {k: v for k, v in self._st.items() if k != "_keys"}

    @property
    def counters(self) -> dict:
        if self._st is None:
            self.load()
        return dict(self._st["counters"])

    def dsr_n(self) -> int:
        """DSR'nin N'i (§3.5): N_raw_cum + N_lesson_cum + N_explore_cum."""
        return sum(self.counters.values())

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Yazarlar arası `flock(LOCK_EX)` (`trials.lock`; kapanınca bırakılır)."""
        lp = self.lock_path
        created = not lp.parent.exists()
        lp.parent.mkdir(parents=True, exist_ok=True)
        if created:
            _fsync_dir(lp.parent.parent)
        fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def append(self, entries: Iterable[Mapping[str, Any]]) -> list[dict]:
        """Satırları ekle. Her giriş için {"status": "APPENDED"|"DUPLICATE", "seq", "row_sha"} döner (sıra korunur).
        Tekrar (tekil anahtar) yazılmaz; sayaçlar o satırın kümülatif değerini taşır. Girişler kilitten ÖNCE doğrulanır
        (geçersiz giriş hiçbir şey yazmaz)."""
        now = iso(utc_now())
        prepared = []
        for e in entries:
            entry = dict(e)
            entry.setdefault("at", now)
            entry.setdefault("registry_sha", RG.REGISTRY_SHA)
            if entry.get("kind") in ("SPEC", "OPTION", "ELIG", "SELECT", "EXPLORE_SEEN"):
                entry.setdefault("lib_version", RG.LIB_VERSION)
            validate_input(entry)
            prepared.append({k: entry.get(k) for k in INPUT_KEYS})
        with self._locked():
            cur = self._verify_now()
            if self._st is None:
                self._st = cur
            elif (cur["size"], cur["head_sha"], cur["rows"]) != (self._st["size"], self._st["head_sha"], self._st["rows"]):
                raise TrialsChanged(f"trials.jsonl değişti ({self._st['rows']} → {cur['rows']} satır); yeniden yükle")
            st = self._st
            seq, prev, cnt = st["rows"], st["head_sha"], tuple(st["counters"][c] for c in COUNTERS)
            keys, kinds = dict(st["_keys"]), dict(st["kinds"])
            out, blob = [], []
            for row in prepared:
                key = dedup_key(row)
                if key is not None and key in keys:
                    out.append({"status": "DUPLICATE", "seq": keys[key], "row_sha": None, "key": list(key[:2])})
                    continue
                cnt = tuple(a + b for a, b in zip(cnt, increments(row)))
                seq += 1
                row = dict(row, seq=seq, prev_sha=prev, **dict(zip(COUNTERS, cnt)))
                row["row_sha"] = row_sha(row)
                blob.append(line_bytes(row))
                prev = row["row_sha"]
                if key is not None:
                    keys[key] = seq
                kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
                out.append({"status": "APPENDED", "seq": seq, "row_sha": row["row_sha"]})
            data = b"".join(blob)
            if data:
                old = read_raw(self.path)
                self._atomic_replace(self.path, old + data, old=old)
                self._atomic_replace(self.hwm_path, self._hwm_bytes(seq, prev, cnt, len(old) + len(data)), old=None)
                st.update({"rows": seq, "head_sha": prev, "counters": dict(zip(COUNTERS, cnt)), "_keys": keys,
                           "kinds": kinds, "size": len(old) + len(data), "code": OK, "ok": True})
        return out

    @staticmethod
    def _hwm_bytes(rows: int, head: str, cnt: tuple[int, int, int], size: int) -> bytes:
        return RG.canonical_bytes({"schema": HWM_SCHEMA, "rows": rows, "head_sha": head, "size": size,
                                   "counters": dict(zip(COUNTERS, cnt))}) + b"\n"

    def _atomic_replace(self, target: Path, data: bytes, *, old: bytes | None) -> None:
        """TEK yeniden adlandırma yolu: aynı klasörde geçici dosya → `fsync` → geri okuma (= `data`) → `old` verildiyse
        `data`'nın TAM öneki olduğu ve hedefin hâlâ `old` olduğu denetimi → `os.replace` → klasör `fsync`. Hata = hedef
        değişmez (geçici dosya silinir)."""
        p = self.paths.require_research(target)
        if old is not None and not data.startswith(old):
            raise TrialsWriteError("yalnız ekleme ihlali: eski baytlar yeni içeriğin öneki değil")
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            back = read_raw(tmp)
            if back != data or (old is not None and not back.startswith(old)):
                raise TrialsWriteError("geçici dosya geri okumada tutmadı")
            if old is not None and read_raw(p) != old:
                raise TrialsChanged("trials.jsonl yazım sırasında değişti")
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _fsync_dir(p.parent)

    def import_priors(self, prior: Mapping[str, Any] = RG.PRIOR_TRIALS_V1, *, at: str | None = None) -> list[dict]:
        """§2.6: `prior_trials.json` satırlarını `kind=PRIOR` olarak (bir kez) yaz. Tekrar çağrı `DUPLICATE` döner."""
        entries = []
        for r in prior["rows"]:
            entries.append({"kind": "PRIOR", "row_id": r["prior_id"], "n": int(r["count"]), "at": at,
                            "params": {"lab": r["lab"], "count_kind": r["count_kind"],
                                       "count_lower_bound": bool(r["count_lower_bound"]), "source": r["source"],
                                       "prior_trials_sha": RG.PRIOR_TRIALS_V1_SHA},
                            "verdict": r["result_tr"]})
        if at is None:
            for e in entries:
                e.pop("at")
        return self.append(entries)


# ============================================================================ mühür satırları (§2.5, §3.5)
def seal_entries(module_src_sha: Mapping[str, str], *, lib: Mapping[str, Any] = RG.LIB_V1) -> list[dict]:
    """LIB sürümünün mühür satırları, `registry.gate_a_rows`'tan (sonuçlara bakılmadan): COIN_FIT dışındaki her Kapı A
    satırı için bir `SPEC`; her `COIN_FIT:<SYM>` satırı için ailenin her ızgara seçeneğine bir `OPTION` (§2.4-5). Toplam
    ham deneme = §2.5 tablosu (LIB_v1: 657 SPEC + 1.081 OPTION = 1.738). `module_src_sha`: varyant kimliği (alias'lar
    tabanına çözülmüş) → `strategy_lib` modül kaynağının sha256'sı (P3b)."""
    vs = RG.variants_by_id(lib)

    def sha_of(vid: str) -> str:
        base = vs[vid].get("alias_of") or vid
        return RG.spec_sha(RG.lib_spec(vid, module_src_sha[base], lib))

    out: list[dict] = []
    for r in RG.gate_a_rows(lib):
        if r["row_type"] == "COIN_FIT":
            for v in lib["variants"]:
                if v["family"] == r["unit"]:
                    out.append({"kind": "OPTION", "lib_version": lib["id"], "spec_sha": sha_of(v["variant_id"]),
                                "scope": r["scope"], "row_id": f"{v['variant_id']}@{r['scope']}",
                                "params": {"family": r["unit"], "variant_id": v["variant_id"], "gate_row": r["row_id"]}})
        else:
            out.append({"kind": "SPEC", "lib_version": lib["id"], "spec_sha": sha_of(r["unit"]), "scope": r["scope"],
                        "row_id": r["row_id"], "params": {"row_type": r["row_type"], "variant_id": r["unit"]}})
    return out


# ============================================================================ engine-query trials (salt-okunur)
def _n(x: int) -> str:
    return f"{int(x):,}".replace(",", ".")


def bound(lines: list[str], *, max_lines: int = QUERY_MAX_LINES, max_bytes: int = QUERY_MAX_BYTES) -> list[str]:
    note = f"… (sınır: {max_lines} satır / {max_bytes // 1024} KB; ayrıntı için daha dar bir --like)"
    out = list(lines)
    if len(out) > max_lines:
        out = out[:max_lines - 1] + [note]
    while len("\n".join(out).encode("utf-8")) > max_bytes and len(out) > 2:
        out = out[:-2] + [note]
    return out


def _hay(*parts: Any) -> str:
    return " ".join(json.dumps(RG.thaw(p), ensure_ascii=False, sort_keys=True) if not isinstance(p, str) else p
                    for p in parts).casefold()


def _safe(x: Any) -> str:
    """Defterden gelen metin (kırık defterde yalnız vekil vb. olabilir) yazdırılabilir UTF-8'e."""
    return str(x).encode("utf-8", "backslashreplace").decode("utf-8")


def query_lines(paths: EnginePaths, *, like: str | None = None) -> list[str]:
    """`engine-query trials [--like <metin>]` — kilit almaz, hiçbir şey yazmaz; ≤ 150 satır / ≤ 4 KB."""
    from datetime import datetime, timezone

    st = verify_ledger(paths)
    bt = RG.budget_table()
    seal_at = datetime.fromtimestamp(RG.T_SEAL_MS / 1000, tz=timezone.utc).isoformat() if RG.T_SEAL_MS else "—"
    lines = ["DENEME DEFTERİ — trials/trials.jsonl (salt-okunur)",
             f"Mühür S1: registry {RG.REGISTRY_SHA[:12]} · LIB_v1 {RG.LIB_V1_SHA[:12]} · T_seal {seal_at}"]
    if st["code"] == EMPTY:
        lines.append("Defter: YOK/BOŞ (P3 gecesi henüz satır yazmadı)")
    elif st["ok"]:
        lines.append(f"Zincir: SAĞLAM · {_n(st['rows'])} satır · baş {st['head_sha'][:12]}")
    else:
        lines.append(f"Zincir: KIRIK ({st['code']}, satır {st['bad_seq']}: {_safe(st['detail'])}) — TRIALS_CHAIN_BROKEN: "
                     "kapı ve terfi kapalı; sayaçlar doğrulanmış önekindir")
    c = st["counters"]
    lines.append(f"Sayaçlar: N_raw {_n(c['N_raw_cum'])} · N_lesson {_n(c['N_lesson_cum'])} · N_explore "
                 f"{_n(c['N_explore_cum'])} · DSR N {_n(sum(c.values()))}")
    hwm = st.get("hwm")
    if hwm and st["code"] == "ROLLED_BACK":
        hc = hwm["counters"]
        lines.append(f"HWM (son yazım): {_n(hwm['rows'])} satır · baş {hwm['head_sha'][:12]} · DSR N {_n(sum(hc.values()))}")
    if st["kinds"]:
        lines.append("Satır türleri: " + " · ".join(f"{k} {_n(v)}" for k, v in sorted(st["kinds"].items())))
    lines.append(f"Bütçe (LIB_v1, koddan): Kapı A satırı {_n(bt['TOTAL'][0])} · ham deneme {_n(bt['TOTAL'][1])} · öncül ≥ "
                 f"{_n(RG.prior_total())} · mühürde N ≥ {_n(RG.n_at_seal())}")
    blocked = RG.blocked_variants()
    lines.append(f"UNSPECIFIED boşluk: {len(RG.unspecified_gaps())} · çalıştırılmayan varyant {len(blocked)} (bütçede "
                 f"sayılır) · protokol boşluğu {len(RG.protocol_gaps())} (dolmadan gerçek veri WF/kapı yok)")
    if not like:
        lines.append("Arama: engine-query trials --like <metin> (varyant, aile, parametre, spec_sha öneki, öncül)")
        return bound(lines)
    q = like.casefold().strip()
    lines.append(f"Arama: '{like}'")
    fam = RG.LIB_V1["families"]
    hits = [v for v in RG.LIB_V1["variants"]
            if q in _hay(v["variant_id"], v["family"], fam[v["family"]]["name_tr"], v["params"], v["rule_tr"])]
    lines.append(f"Kütüphane (LIB_v1) eşleşen varyant: {len(hits)}")
    for v in hits[:QUERY_SHOW_ROWS]:
        alias = f" ≡ {v['alias_of']} (tekilleşir)" if v.get("alias_of") else ""
        gaps = " · UNSPECIFIED" if v["variant_id"] in blocked else ""
        lines.append(f"  {v['variant_id']} · {v['family']} {fam[v['family']]['name_tr']} · {','.join(v['timeframes'])}"
                     f"{alias}{gaps}")
    n_match, last = 0, deque(maxlen=QUERY_SHOW_ROWS)
    if st["rows"] or not st["ok"]:
        for r in iter_rows(paths.trials_file, lenient=True):
            sp = r.get("spec_sha") if isinstance(r.get("spec_sha"), str) else ""
            if (len(q) >= 6 and sp.startswith(q)) or q in _hay(r.get("kind"), r.get("row_id"), r.get("scope"),
                                                               r.get("params"), r.get("verdict") or ""):
                n_match += 1
                last.append(r)
    lines.append(f"Defter satırı eşleşen: {n_match}" + (f" (en yeni {len(last)})" if n_match > len(last) else ""))
    for r in reversed(last):
        lines.append(_safe(f"  #{r.get('seq')} {r.get('kind')} {r.get('row_id')} {r.get('scope') or ''} "
                           f"{sp_short(r)} {r.get('verdict') or ''}".rstrip()))
    pri = [r for r in RG.PRIOR_TRIALS_V1["rows"]
           if q in _hay(r["prior_id"], r["lab"], r["result_tr"], r["keywords"])]
    lines.append(f"Öncül deneme eşleşen: {len(pri)}")
    for r in pri:
        lb = " (alt sınır)" if r["count_lower_bound"] else ""
        lines.append(f"  {r['prior_id']} · {r['lab']} · {_n(r['count'])}{lb} · {r['result_tr']}")
    nr = [r for r in RG.PRIOR_TRIALS_V1["no_reentry"] if q in _hay(r["id"], r["lab"], r["text_tr"], r["keywords"])]
    if nr:
        lines.append("ÖNCÜL — KANIT YOK (yeni LIB sürümü ve Kapı A olmadan yeniden girmez):")
        lines += [f"  {r['text_tr']}" for r in nr]
    return bound(lines)


def sp_short(r: Mapping[str, Any]) -> str:
    sp = r.get("spec_sha")
    return sp[:12] if isinstance(sp, str) else ""


__all__ = ["BROKEN_CODES", "COUNTERS", "GENESIS", "HWM_SCHEMA", "INPUT_KEYS", "KINDS", "QUERY_MAX_BYTES", "QUERY_MAX_LINES",
           "ROW_KEYS", "SCHEMA", "TrialRowError", "TrialsChainBroken", "TrialsChanged", "TrialsLedger", "TrialsWriteError",
           "bound", "dedup_key", "hwm_problem", "increments", "iter_rows", "lib_family", "line_bytes", "query_lines",
           "read_hwm", "row_sha", "seal_entries", "strict_loads", "validate_input", "verify_bytes", "verify_chain",
           "verify_ledger"]

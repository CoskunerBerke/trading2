"""P3a — deneme defteri `trials/trials.jsonl` (docs/ENGINE_BRAIN_V1.md §3.5, §2.6, §4.4; ana belge §6.4).

* **Yalnız eklenir.** Satırlar `O_APPEND` ile tek seferde yazılır ve `fsync` edilir. Bu modülde var olan baytları yeniden
  yazan, kesen, silen ya da yeniden adlandıran HİÇBİR yol yoktur (AST testi): düzeltme bile yeni bir satırdır.
* **Hash zinciri.** `row_sha` = sha256(kanonik satır − `row_sha`), `prev_sha` = önceki satırın `row_sha`'sı (ilk satırda
  `GENESIS`); `seq` 1'den ardışık. Satırın baytları kanonik JSON'un KENDİSİDİR (boşluk/sıra oynaması da kırıktır).
* **Sayaçlar hiç azalmaz.** `N_raw_cum` = benzersiz (`lib_version`, `spec_sha`, kapsam) `SPEC`/`OPTION` + `PRIOR`
  sayıları; `N_lesson_cum` = `LESSON_LOOK` satırları; `N_explore_cum` = `EXPLORE_SEEN` seri sayıları. DSR'nin N'i bu
  üçünün toplamıdır (o anki değer). Bakışlar N'i büyütmez.
* **Tekilleştirme.** Aynı (`lib_version`, `spec_sha`, kapsam) ikinci kez deneme sayılmaz (`DUPLICATE`, satır yazılmaz);
  farklı kapsam farklı satırdır. `PRIOR` `row_id` (prior_id) başına bir kez; bakışlar (`row_id`, `look_no`) başına bir kez.
* **Doğrulama.** `verify_chain` dosyayı baştan sınar (sıra, zincir, satır sha'sı, kanonik bayt, sayaçlar, tekillik, yarım
  satır). Kırık zincire EKLENMEZ (`TrialsChainBroken`); gece S6'da `TRIALS_CHAIN_BROKEN` → kapı ve terfi o gece kapalı.
* **Kilit.** Ekleme yalnız `analysis.lock`'u tutan gece birimindendir (P3g); `engine-query trials` salt-okunurdur, kilit
  almaz. Yükleme ile ekleme arasında dosya boyu değişirse ekleme reddedilir (`TrialsChanged`).

Satır alanları (§3.5, tam liste `ROW_KEYS`): `PRIOR` ve `EXPLORE_SEEN` satırlarında `n` sayılan deneme/seri sayısıdır;
`ELIG`'de §2.4-1'in giriş sayısıdır. Yalnız standart kütüphane; ağ yok.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any, Iterable, Mapping

from . import registry as RG
from .ledgers import iso, utc_now
from .paths import EnginePaths

SCHEMA = "trials_v1"
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
OK, EMPTY = "OK", "EMPTY"
BROKEN_CODES = ("TORN_TAIL", "BAD_JSON", "NOT_CANONICAL", "BAD_SCHEMA", "BAD_SEQ", "BAD_PREV", "BAD_ROW_SHA", "BAD_COUNTER",
                "DUPLICATE")


class TrialsChainBroken(RuntimeError):
    """Zincir doğrulanmadı: eklenmez (gece: `TRIALS_CHAIN_BROKEN`, kapı ve terfi kapalı)."""


class TrialsChanged(RuntimeError):
    """Yüklemeden sonra dosya boyu değişti (başka yazar): eklenmez; yeniden yükleyip tekrar denenmeli."""


class TrialRowError(ValueError):
    """Satır şeması/zorunlu alanı tutmuyor."""


# ============================================================================ satır kuralları
def line_bytes(row: Mapping[str, Any]) -> bytes:
    return RG.canonical_bytes(row) + b"\n"


def row_sha(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(RG.canonical_bytes({k: v for k, v in row.items() if k != "row_sha"})).hexdigest()


def dedup_key(row: Mapping[str, Any]) -> tuple | None:
    k = row["kind"]
    if k in ("SPEC", "OPTION"):
        return ("TRIAL", row["lib_version"], row["spec_sha"], row["scope"])
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
    RG.canonical_bytes(dict(entry))                       # JSON'a sığmayan değer (NaN, nesne) burada düşer


# ============================================================================ doğrulama
def _result(ok: bool, code: str, rows: int, head: str, counters: tuple[int, int, int], *, bad_seq: int | None = None,
            detail: str = "", kinds: dict | None = None, size: int = 0, keys: dict | None = None) -> dict:
    return {"ok": ok, "code": code, "rows": rows, "head_sha": head, "bad_seq": bad_seq, "detail": detail,
            "counters": dict(zip(COUNTERS, counters)), "kinds": kinds or {}, "size": size, "_keys": keys or {}}


def verify_bytes(raw: bytes) -> dict:
    """Bütün zinciri sına. Dönen: `ok`, `code` (OK/EMPTY ya da `BROKEN_CODES`), `rows`, `head_sha`, `bad_seq`, sayaçlar."""
    if not raw:
        return _result(True, EMPTY, 0, GENESIS, (0, 0, 0))
    if not raw.endswith(b"\n"):
        n = raw.count(b"\n")
        return _result(False, "TORN_TAIL", n, GENESIS, (0, 0, 0), bad_seq=n + 1, detail="son satır yarım (yeni satır yok)",
                       size=len(raw))
    prev, cnt, keys, kinds = GENESIS, (0, 0, 0), {}, {}
    for i, line in enumerate(raw.split(b"\n")[:-1], start=1):
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return _result(False, "BAD_JSON", i - 1, prev, cnt, bad_seq=i, detail=str(exc)[:120], size=len(raw))
        if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(ROW_KEYS)) or row.get("kind") not in KINDS:
            return _result(False, "BAD_SCHEMA", i - 1, prev, cnt, bad_seq=i, detail="alan kümesi/kind", size=len(raw))
        if line + b"\n" != line_bytes(row):
            return _result(False, "NOT_CANONICAL", i - 1, prev, cnt, bad_seq=i, detail="bayt kanonik değil", size=len(raw))
        if row["seq"] != i:
            return _result(False, "BAD_SEQ", i - 1, prev, cnt, bad_seq=i, detail=f"seq {row['seq']} ≠ {i}", size=len(raw))
        if row["prev_sha"] != prev:
            return _result(False, "BAD_PREV", i - 1, prev, cnt, bad_seq=i, detail="prev_sha önceki row_sha değil",
                           size=len(raw))
        if row["row_sha"] != row_sha(row):
            return _result(False, "BAD_ROW_SHA", i - 1, prev, cnt, bad_seq=i, detail="row_sha içerikle tutmuyor", size=len(raw))
        try:
            validate_input({k: row[k] for k in INPUT_KEYS})
            inc = increments(row)
            key = dedup_key(row)
        except (TrialRowError, KeyError, TypeError, ValueError) as exc:
            return _result(False, "BAD_SCHEMA", i - 1, prev, cnt, bad_seq=i, detail=str(exc)[:120], size=len(raw))
        if key is not None and key in keys:
            return _result(False, "DUPLICATE", i - 1, prev, cnt, bad_seq=i, detail=f"tekil anahtar tekrarı {key[:2]}",
                           size=len(raw))
        new = tuple(a + b for a, b in zip(cnt, inc))
        if tuple(row[c] for c in COUNTERS) != new:
            return _result(False, "BAD_COUNTER", i - 1, prev, cnt, bad_seq=i,
                           detail=f"sayaçlar {tuple(row[c] for c in COUNTERS)} ≠ yeniden hesap {new}", size=len(raw))
        if key is not None:
            keys[key] = i
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        prev, cnt = row["row_sha"], new
    return _result(True, OK, raw.count(b"\n"), prev, cnt, kinds=kinds, size=len(raw), keys=keys)


def read_raw(path: os.PathLike | str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return b""


def verify_chain(path: os.PathLike | str) -> dict:
    r = verify_bytes(read_raw(path))
    r.pop("_keys", None)
    return r


def iter_rows(path: os.PathLike | str, *, lenient: bool = False) -> Iterable[dict]:
    """Satırlar (doğrulamasız). `lenient`: ayrıştırılamayan satır atlanır (kırık defterde salt-okunur sorgu için)."""
    for line in read_raw(path).split(b"\n"):
        if not line:
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            if lenient:
                continue
            raise
        if isinstance(row, dict) or not lenient:
            yield row


# ============================================================================ defter
class TrialsLedger:
    """`trials/trials.jsonl` yazarı. `load()` zinciri doğrular; `append()` yalnız sona ekler."""

    def __init__(self, paths: EnginePaths) -> None:
        self.paths = paths
        self.path = paths.require_research(paths.trials_file)
        self._st: dict | None = None

    def load(self) -> dict:
        st = verify_bytes(read_raw(self.path))
        if not st["ok"]:
            raise TrialsChainBroken(f"TRIALS_CHAIN_BROKEN {st['code']} satır {st['bad_seq']}: {st['detail']}")
        self._st = st
        return {k: v for k, v in st.items() if k != "_keys"}

    @property
    def counters(self) -> dict:
        if self._st is None:
            self.load()
        return dict(self._st["counters"])

    def dsr_n(self) -> int:
        """DSR'nin N'i (§3.5): N_raw_cum + N_lesson_cum + N_explore_cum."""
        return sum(self.counters.values())

    def append(self, entries: Iterable[Mapping[str, Any]]) -> list[dict]:
        """Satırları ekle. Her giriş için {"status": "APPENDED"|"DUPLICATE", "seq", "row_sha"} döner (sıra korunur).
        Tekrar (tekil anahtar) yazılmaz; sayaçlar o satırın kümülatif değerini taşır."""
        if self._st is None:
            self.load()
        st = self._st
        size_now = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        if size_now != st["size"]:
            raise TrialsChanged(f"trials.jsonl boyu değişti ({st['size']} → {size_now}); yeniden yükle")
        seq, prev, cnt = st["rows"], st["head_sha"], tuple(st["counters"][c] for c in COUNTERS)
        keys, kinds = dict(st["_keys"]), dict(st["kinds"])
        out, blob = [], []
        now = iso(utc_now())
        for e in entries:
            entry = dict(e)
            entry.setdefault("at", now)
            entry.setdefault("registry_sha", RG.REGISTRY_SHA)
            if entry.get("kind") in ("SPEC", "OPTION", "ELIG", "SELECT", "EXPLORE_SEEN"):
                entry.setdefault("lib_version", RG.LIB_VERSION)
            validate_input(entry)
            row = {k: entry.get(k) for k in INPUT_KEYS}
            key = dedup_key(row)
            if key is not None and key in keys:
                out.append({"status": "DUPLICATE", "seq": keys[key], "row_sha": None, "key": list(key[:2])})
                continue
            inc = increments(row)
            cnt = tuple(a + b for a, b in zip(cnt, inc))
            seq += 1
            row.update({"seq": seq, "prev_sha": prev, **dict(zip(COUNTERS, cnt))})
            row["row_sha"] = row_sha(row)
            blob.append(line_bytes(row))
            prev = row["row_sha"]
            if key is not None:
                keys[key] = seq
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
            out.append({"status": "APPENDED", "seq": seq, "row_sha": row["row_sha"]})
        data = b"".join(blob)
        if data:
            self._append_bytes(data)
        st.update({"rows": seq, "head_sha": prev, "counters": dict(zip(COUNTERS, cnt)), "_keys": keys, "kinds": kinds,
                   "size": st["size"] + len(data), "code": OK if seq else EMPTY})
        return out

    def _append_bytes(self, data: bytes) -> None:
        """TEK yazım yolu: `O_APPEND` (var olan bayta dokunmaz) + tam yazım döngüsü + `fsync`."""
        p = self.paths.require_research(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)

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


def query_lines(paths: EnginePaths, *, like: str | None = None) -> list[str]:
    """`engine-query trials [--like <metin>]` — kilit almaz, hiçbir şey yazmaz; ≤ 150 satır / ≤ 4 KB."""
    from datetime import datetime, timezone

    st = verify_chain(paths.trials_file)
    bt = RG.budget_table()
    seal_at = datetime.fromtimestamp(RG.T_SEAL_MS / 1000, tz=timezone.utc).isoformat() if RG.T_SEAL_MS else "—"
    lines = ["DENEME DEFTERİ — trials/trials.jsonl (salt-okunur)",
             f"Mühür S1: registry {RG.REGISTRY_SHA[:12]} · LIB_v1 {RG.LIB_V1_SHA[:12]} · T_seal {seal_at}"]
    if st["code"] == EMPTY:
        lines.append("Defter: YOK/BOŞ (P3 gecesi henüz satır yazmadı)")
    elif st["ok"]:
        lines.append(f"Zincir: SAĞLAM · {_n(st['rows'])} satır · baş {st['head_sha'][:12]}")
    else:
        lines.append(f"Zincir: KIRIK ({st['code']}, satır {st['bad_seq']}: {st['detail']}) — TRIALS_CHAIN_BROKEN: kapı ve "
                     "terfi kapalı; sayaçlar ilk kırık satırdan öncesidir")
    c = st["counters"]
    lines.append(f"Sayaçlar: N_raw {_n(c['N_raw_cum'])} · N_lesson {_n(c['N_lesson_cum'])} · N_explore "
                 f"{_n(c['N_explore_cum'])} · DSR N {_n(sum(c.values()))}")
    if st["kinds"]:
        lines.append("Satır türleri: " + " · ".join(f"{k} {_n(v)}" for k, v in sorted(st["kinds"].items())))
    lines.append(f"Bütçe (LIB_v1, koddan): Kapı A satırı {_n(bt['TOTAL'][0])} · ham deneme {_n(bt['TOTAL'][1])} · öncül ≥ "
                 f"{_n(RG.prior_total())} · mühürde N ≥ {_n(RG.n_at_seal())}")
    lines.append(f"UNSPECIFIED boşluk: {len(RG.unspecified_gaps())} (o alanı olan varyant çalıştırılmaz, bütçede sayılır)")
    if not like:
        lines.append("Arama: engine-query trials --like <metin> (varyant, aile, parametre, spec_sha öneki, öncül)")
        return bound(lines)
    q = like.casefold().strip()
    lines.append(f"Arama: '{like}'")
    fam = RG.LIB_V1["families"]
    hits = [v for v in RG.LIB_V1["variants"]
            if q in _hay(v["variant_id"], v["family"], fam[v["family"]]["name_tr"], v["params"], v["rule_tr"])]
    lines.append(f"Kütüphane (LIB_v1) eşleşen varyant: {len(hits)}")
    for v in hits[:40]:
        alias = f" ≡ {v['alias_of']} (tekilleşir)" if v.get("alias_of") else ""
        gaps = " · UNSPECIFIED" if RG.variant_gaps(v) else ""
        lines.append(f"  {v['variant_id']} · {v['family']} {fam[v['family']]['name_tr']} · {','.join(v['timeframes'])}"
                     f"{alias}{gaps}")
    rows = []
    if st["rows"] or not st["ok"]:
        for r in iter_rows(paths.trials_file, lenient=True):
            if len(rows) >= 2000:
                break
            sp = r.get("spec_sha") or ""
            if (len(q) >= 6 and sp.startswith(q)) or q in _hay(r.get("kind"), r.get("row_id"), r.get("scope"),
                                                               r.get("params"), r.get("verdict") or ""):
                rows.append(r)
    lines.append(f"Defter satırı eşleşen: {len(rows)}")
    for r in rows[-40:][::-1]:
        lines.append(f"  #{r.get('seq')} {r.get('kind')} {r.get('row_id')} {r.get('scope') or ''} "
                     f"{(r.get('spec_sha') or '')[:12]} {r.get('verdict') or ''}".rstrip())
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


__all__ = ["COUNTERS", "GENESIS", "INPUT_KEYS", "KINDS", "QUERY_MAX_BYTES", "QUERY_MAX_LINES", "ROW_KEYS", "SCHEMA",
           "TrialRowError", "TrialsChainBroken", "TrialsChanged", "TrialsLedger", "bound", "dedup_key", "increments",
           "iter_rows", "line_bytes", "query_lines", "row_sha", "seal_entries", "validate_input", "verify_bytes",
           "verify_chain"]

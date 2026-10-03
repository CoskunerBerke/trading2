# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — RAPOR (2026-09-29). SALT OKUR: depoya, imlece, deftere, ağa DOKUNMAZ.

Soru (kullanıcı): **"bu durumda, bu kurulumda daha önce kazandık mı kaybettik mi?"** Cevap her zaman şu şeritle gelir:
`tanımlayıcı; karar yok; kanıt değil` — bu rapor hiçbir kararı DEĞİŞTİRMEZ, öğreniciye/karara GİRDİ DEĞİLDİR.

Kaynak ve okuma (2026-09-29; SPEC_V1 §11, KARARLAR.md — SPEC'e üstün):

* Depo `state/<state_dir>/` (varsayılan `shared_experience`): önce arşiv segmentleri (manifest `seq` sırası; dosya
  sha256'sı manifestle tutmayan segment ATLANIR ve sayılır), sonra sıcak dosya `experience.jsonl`. Dosya tanıtıcısı
  açılmaz; her segment/sıcak dosya bayt olarak okunur ve satır satır ayrıştırılır (segment ≤ `hot_max_lines` satır).
  `core.read_json` KULLANILMAZ: bozuk dosyayı `.corrupt-N` olarak kopyalar (yazma) — burada salt-okur okuyucu var.
* Tekilleştirme `(tür, kaynak anahtarı, rev)` ile — `row_id = f(tür, defter, kaynak, rev)` olduğundan `row_id`
  tekilleştirmesine DENKTİR (eşit revizyonda İLK görülen kalır: arşiv sıcak dosyadan önce okunur).
* Anahtar başına durum KOMPAKTTIR (64-bit özet anahtar + paketlenmiş 6 float): 200 bin satırlık depoda tepe bellek
  < 60 MB (T7). Gözlem tablosu numpy dizileridir; hücre istatistikleri maske/indeksle hesaplanır.

Gözlem ve sütunlar:

* **Gerçek** (`xp_entry` + en yüksek revizyonlu `xp_outcome`, `trade_key`): durum = girişin rev-0 anlık görüntüsü;
  `r_net` defterin net R'si, `cost_r` hepsi dahil maliyet (KARARLAR 1). Açık işlem yalnız kapsamda (`real_open`).
  Kohort dilimi karnenin (`scorecard_class`): politika / öğrenme-ekstra / öğrenme öncesi.
* **Olsaydı** (karşı-olgusal, `cf_key`): durum = rev-0 anlık görüntüsü; net istatistiğe YALNIZ en son revizyonu
  LABELLED, `r_basis == NET`, `in_net_stats` ve etiket sürümü `cf_label_v2+` olanlar girer. Eski brüt etiketler
  (yok / v1 / v1c) AYRI satırda yalnız brüt ortalama olarak gösterilir — hükme ve karşılaştırmaya GİRMEZ.
  Bekleyen / değiştirildi / süresi doldu / düşürüldü / kayboldu yalnız kapsamda sayılır.
* **A15** (KARARLAR 7): tabanın da bloke ettiği (`baseline_blocked_by` dolu) karşı-olgusallar istatistikte KALIR,
  bayrakla sayılır (`n_a15`) ve ayrıca onlar HARİÇ süzülmüş görünüm (`cf_excl_a15`) verilir.
* Gerçek ve karşı-olgusal ASLA tek ortalamada birleşmez (ayrı sütunlar).

Hücre, geri çekilme ve hüküm (önceden kayıtlı, KARARLAR 4-5):

* Hücre = (kurulum `setup_key` = defter|setup_type|yön YA DA aile `family_key` = aile|yön) × durum kovası
  (`situation.bucket`: trend, oynaklık, BTC, hacim, yapı), COİNLER ARASINDA havuzlanmış. Zıt mantıklar aynı aileye
  düşmez (rows.py). Kurulumlar ve yönler ASLA birleştirilmez.
* Geri çekilme sırası: yapı → hacim → BTC → oynaklık → trend; her seviye raporlanır, sütun başına "cevap seviyesi" =
  n ≥ `min_n` olan ilk seviye.
* n < 10 → `INSUFFICIENT_SAMPLE` (sayı GÖSTERİLMEZ); 10 ≤ n < `min_n` (30) → `DESCRIPTIVE` (aralıklarla tanımlayıcı,
  hüküm `VERİ YETERSİZ`); n ≥ 30 → hüküm (etiketler `scripts/bot_scorecard.py` ile BİREBİR): küme < 5 ya da aralık yok →
  `VERİ YETERSİZ`; iid VE küme aralığının üst ucu < 0 → `ZARARDA (kanıtlı)`; ikisinin alt ucu > 0 →
  `KÂRDA (kanıtlı, PAPER)`; aksi `BELİRSİZ`.
* Kazanç = `r_net > 0` (karne); oran için Wilson %95. Ortalama için iid bootstrap (2000, tohum 20260916) ve sembol×gün
  küme bootstrap'ı (2000, tohum 20260929); n (ya da küme sayısı) > 5.000 ise normal yaklaşım (`ci_method`).
  KARNEYLE SAYISAL EŞİTLİK YOK (2026-09-29, inceleme bulgusu): iid aralık numpy `default_rng` (PCG64) ile üretilir; karnenin
  `pattern_trader.report._bootstrap_ci`si `random.Random` (Mersenne Twister) ve saf Python döngüsüdür. Yöntem aynıdır
  (ortalamanın 2000 yeniden örneklemli yüzdelik aralığı) ama aynı R listesi için sınırlar örnekleme gürültüsü kadar farklıdır
  (ölçüm: 40 değerde [−0,0797, 0,4046] ↔ [−0,0763, 0,4157]); ayrıca bootstrap sırası-bağımlıdır ve rapor gözlemleri karnenin
  işlem sırasıyla gelmez (geri doldurma yeniden eskiye yazar). Karne işlevi bilinçli olarak YENİDEN KULLANILMAZ: n = 5000'de
  hücre başına ~10 milyon Python çağrısı (saniyeler) ve çok seviyeli sorguda dakikalar. Sınırdaki bir hücrenin hükmü iki
  araçta farklı çıkabilir; belgede `params.ci_iid_note` bunu söyler.
  Medyan için dağılımdan bağımsız sıra-istatistiği aralığı.
* Coin başına kısmi havuz (KARARLAR 4): `shrunk = (n_coin·ort_coin + k·ort_havuz) / (n_coin + k)`, k = 20
  (ayarlanabilir); havuz değerinin yanında gösterilir. Yeni/az işlem gören coin havuzdan hemen yararlanır.

Sorgu kipi "bu durumu daha önce gördük mü?": açık boyutlar (`--trend` …), bir sembolün depodaki son anlık görüntüsü
(`--for-symbol`), dışarıdan verilen anlık görüntü (`--snapshot-json`) ya da Formasyon CSV önbelleğinden AĞSIZ canlı
anlık görüntü (`--live`). Hücreler bu duruma göre seçilir; odak coin'in büzülmüş ortalaması yanında verilir.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import struct
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from ..core import from_iso, iso
from . import KINDS, LAYER_VERSION, ROW_SCHEMA
from . import rows as R
from . import situation as S

# ============================================================================ sabitler (2026-09-29)
REPORT_SCHEMA = "shared_experience_report_v1"
SUMMARY_SCHEMA = "shared_experience_summary_v1"
STATUS_REPORT_SCHEMA = "shared_experience_status_report_v1"
#: Sabit şerit (KARARLAR; görev X4) — rapor metni, JSON ve panel kartında AYNEN.
BANNER_TR = "tanımlayıcı; karar yok; kanıt değil"
BANNER_LINE_TR = ("ORTAK DENEYİM — " + BANNER_TR
                  + " (PAPER; seçilim yanlılığı olabilir; gerçek ve karşı-olgusal tek ortalamada BİRLEŞMEZ)")
#: Depo dosya adları (store.py / collector.py ile AYNI — test korur; rapor ağır `learn` paketini yüklemesin diye yerel).
HOT_FILE = "experience.jsonl"
ARCHIVE_DIR = "archive"
MANIFEST_FILE = "manifest.json"
SEGMENTS_DIR = "segments"
STATUS_FILE = "status.json"
CURSOR_FILE = "cursor.json"
#: Panel kartının okuduğu rapor özeti (CLI `--summary-out` ile yazılır; panel paketi İÇE AKTARMAZ).
SUMMARY_FILE = "report_summary.json"

#: Hüküm eşiği (= `pattern_trader.report.MIN_TRADES_FOR_VERDICT`, karne; test korur) ve tanımlayıcı alt sınır.
MIN_N_VERDICT = 30
MIN_N_DESCRIPTIVE = 10
MIN_CLUSTERS = 5
#: Coin başına kısmi havuz sabiti k (KARARLAR 4).
SHRINK_K = 20.0
BOOT_ITERS = 2000
SEED_IID = 20260916
SEED_CLUSTER = 20260929
#: iid aralığın karneyle ilişkisi (2026-09-29, inceleme bulgusu) — rapor belgesinde `params.ci_iid_note`.
CI_IID_NOTE_TR = ("iid bootstrap numpy PCG64 (tohum 20260916) ile; karnenin _bootstrap_ci'si (random.Random) ile AYNI yöntem "
                  "ama sayısal olarak AYNI DEĞİL — sınırlar örnekleme gürültüsü kadar farklı olabilir")
#: Bu boyun (gözlem ya da küme) üstünde bootstrap yerine normal yaklaşım: n > 5000'de ortalamanın bootstrap yüzdelik
#: aralığı normal aralıkla pratikte aynıdır; çok seviyeli sorguda süre sınırlı kalır (`ci_method` hangisi olduğunu yazar).
BOOT_MAX_N = 5_000
#: Bootstrap parça boyu (öğe): indeks + toplanan değer matrisi en çok ~4 MB (512M panel/CLI bellek bütçesi).
_BOOT_CHUNK = 262_144
Z95 = 1.96
TOP_COINS = 10
TOP_CELLS = 10
TOP_EXITS = 5

#: Durum boyutları (situation.bucket anahtarları) ve ÖNCEDEN KAYITLI geri çekilme sırası (KARARLAR 5).
DIMS = ("trend", "vol", "btc", "volume", "structure")
BACKOFF_ORDER = ("structure", "volume", "btc", "vol", "trend")
DIM_TR = {"trend": "trend", "vol": "oynaklık", "btc": "BTC", "volume": "hacim", "structure": "yapı"}
UNKNOWN = S.UNKNOWN
DIM_VALUES: dict[str, tuple[str, ...]] = {
    "trend": ("UP", "DOWN", "RANGE", UNKNOWN), "vol": ("LOW", "NORMAL", "HIGH", UNKNOWN),
    "btc": ("UP", "DOWN", "RANGE", UNKNOWN), "volume": ("LOW", "NORMAL", "HIGH", UNKNOWN),
    "structure": ("HH_HL", "LH_LL", "MIXED", UNKNOWN)}

#: Hüküm etiketleri — `scripts/bot_scorecard.py` V_THIN / V_LOSS / V_WIN / V_OPEN ile BİREBİR (test korur).
V_THIN, V_LOSS, V_WIN, V_OPEN = "VERİ YETERSİZ", "ZARARDA (kanıtlı)", "KÂRDA (kanıtlı, PAPER)", "BELİRSİZ"
INSUFFICIENT_SAMPLE, LOSS, WIN, UNDECIDED = "INSUFFICIENT_SAMPLE", "LOSS", "WIN", "UNDECIDED"
VERDICT_TR = {INSUFFICIENT_SAMPLE: V_THIN, LOSS: V_LOSS, WIN: V_WIN, UNDECIDED: V_OPEN}
TIER_INSUFFICIENT, TIER_DESCRIPTIVE, TIER_VERDICT = "INSUFFICIENT_SAMPLE", "DESCRIPTIVE", "VERDICT"

#: Anlık görüntü süzgeci (varsayılan yalnız OK — SPEC §11.3). "any" = anlık görüntüsüz ve eksik olanlar dahil.
SNAPSHOT_FILTERS: dict[str, tuple[str, ...] | None] = {"ok": ("OK",), "ok+partial": ("OK", "PARTIAL"), "any": None}
SNAP_NONE, SNAP_SCHEMA_OTHER = "NONE", "SCHEMA_OTHER"
#: Gerçek işlem kohort süzgeci → karnenin dilimi (`rows.scorecard_class`).
COHORT_FILTERS = {"all": None, "policy": R.SC_POLICY, "extra": R.SC_LEARNING_EXTRA, "pre": R.SC_BEFORE}
COHORT_ORDER = (R.SC_POLICY, R.SC_LEARNING_EXTRA, R.SC_BEFORE)
COHORT_TR = {R.SC_POLICY: "politika", R.SC_LEARNING_EXTRA: "öğrenme-ekstra", R.SC_BEFORE: "öğrenme öncesi"}
GROUP_BY = ("setup", "family")

# anahtar başına kompakt durum kodları
_REAL, _CF = 0, 1
_CLOSED, _OTHER = "CLOSED", "OTHER"
_ST = (_CLOSED, R.PENDING, R.LABELLED, R.SUPERSEDED, R.EXPIRED, R.DROPPED, R.VANISHED, _OTHER)
_ST_I = {s: i for i, s in enumerate(_ST)}
_B_NONE = "NONE"
_BASIS = (_B_NONE, R.NET, R.GROSS_LEGACY, R.UNKNOWN_LABEL_VERSION, R.NET_UNAVAILABLE)
_BASIS_I = {b: i for i, b in enumerate(_BASIS)}
_NO_CTX, _EXCLUDED = -1, -2
_NAN6 = (math.nan,) * 6
_DAY_MS = 86_400_000
#: Tekilleştirilmiş bağlam demeti sütunları (`_Scan._context`; gün BURADA DEĞİL → bağlam sayısı küçük kalır).
_CTX = ("src", "book", "setup", "family", "side", "coin", "snap", "trend", "vol", "btc", "volume", "structure",
        "origin", "sc", "a15", "rf", "rank")
_CI = {k: i for i, k in enumerate(_CTX)}
#: Anahtar başına TEK paketli kayıt (67 bayt): bağlam sırası, bağlam id, gün, sonuç rev, kod, çıkış nedeni, 6 float
#: (r_net, r_gross, cost_r, mfe_r, mae_r, hold_h). Liste + float nesnelerine göre anahtar başına ~%60 daha az bellek.
_REC = struct.Struct("<BiiiHi6d")
_REC_DT = np.dtype([("rank", "u1"), ("ctx", "<i4"), ("day", "<i4"), ("rev", "<i4"), ("code", "<u2"), ("exit", "<i4"),
                    ("f", "<f8", (6,))])
assert _REC_DT.itemsize == _REC.size
_REC_EMPTY = (0, _NO_CTX, -1, -1, 0, 0) + _NAN6
_RES_FLOATS = ("r_gross", "cost_r", "mfe_r", "mae_r", "hold_hours")


class ReportError(ValueError):
    """Geçersiz sorgu (boyut/değer/eşik). CLI çıkış kodu 2 ile bildirir."""


# ============================================================================ küçük yardımcılar
def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ms(x: Any) -> int | None:
    """ISO metin / datetime / ms tamsayı → epoch ms; okunamaz → None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return int(x)
    if isinstance(x, datetime):
        d = x if x.tzinfo is not None else x.replace(tzinfo=timezone.utc)
        return int(d.timestamp() * 1000)
    try:
        return int(from_iso(str(x)).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def parse_when(x: Any) -> int | None:
    """CLI zamanı (ISO tarih ya da tarih-saat; saat dilimsiz → UTC) → ms. Geçersiz → ReportError."""
    if x in (None, ""):
        return None
    v = _ms(x)
    if v is None:
        raise ReportError("zaman okunamadı: %r (ISO bekleniyor, ör. 2026-09-29 ya da 2026-09-29T08:00:00+00:00)" % (x,))
    return v


def _iso_ms(ms: int | None) -> str | None:
    return None if ms is None else iso(datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc))


def _h64(s: str) -> int:
    """Anahtar metni → deterministik 64-bit tamsayı (süreçten bağımsız; `hash()` rastgeledir)."""
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "little")


def coin_of(symbol: Any) -> str | None:
    """Sembol → coin kimliği `BASE/QUOTE` (defterler arası havuz): `SOL/USDT:USDT`, `SOL/USDT`, `SOLUSDT`, `sol-usdt`
    ve yalın `SOL` hepsi `SOL/USDT`."""
    s = str(symbol or "").strip().upper().split(":")[0].replace("-", "/").replace("_", "/")
    if not s:
        return None
    if "/" in s:
        base, quote = s.split("/", 1)
    else:
        base, quote = s, "USDT"
        for q in ("USDT", "USDC", "BUSD"):
            if s.endswith(q) and len(s) > len(q):
                base, quote = s[: -len(q)], q
                break
    return "%s/%s" % (base, quote or "USDT") if base else None


def _read_json_ro(path: Path, *, max_bytes: int | None = None) -> Any:
    """SALT-OKUR JSON: yok / okunamaz / bozuk / tavandan büyük → None (dosyaya ASLA dokunmaz)."""
    try:
        if max_bytes is not None and path.stat().st_size > max_bytes:
            return None
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson aralığı — `patterns.engine.wilson` ile AYNI formül (test eşitliği korur; o modül pandas yükler)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, cen - half), min(1.0, cen + half)


def _r4(x: Any) -> float | None:
    v = _f(x)
    return None if v is None else round(v, 4)


# ============================================================================ okuma (akış, salt okur)
@dataclass
class ReadStats:
    """Okuma sayaçları (rapor `source` bölümü)."""
    segments_total: int = 0
    segments_read: int = 0
    segments_skipped: int = 0
    segments_bad: int = 0
    hot_present: bool = False
    hot_bytes: int = 0
    lines_bad: int = 0
    rows: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"segments_total": self.segments_total, "segments_read": self.segments_read,
                "segments_skipped": self.segments_skipped, "segments_bad": self.segments_bad,
                "hot_present": self.hot_present, "hot_bytes": self.hot_bytes, "lines_bad": self.lines_bad,
                "rows_read": self.rows}


def manifest(root: Path | str) -> dict[str, Any]:
    doc = _read_json_ro(Path(root) / ARCHIVE_DIR / MANIFEST_FILE)
    return doc if isinstance(doc, dict) else {}


def _segments(man: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Manifest segmentleri ARŞİVLEME sırasıyla (`SegmentArchive._ordered` ile aynı anahtar)."""
    segs = [s for s in (man.get("segments") or []) if isinstance(s, dict)]
    return sorted(segs, key=lambda s: (int(s["seq"]) if isinstance(s.get("seq"), (int, float)) else 1 << 30,
                                       str(s.get("first_ts") or ""), str(s.get("segment_id") or "")))


def _parse_lines(data: bytes, st: ReadStats) -> Iterator[dict[str, Any]]:
    for raw in io.BytesIO(data):
        s = raw.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except ValueError:                           # yarım/bozuk satır (çökme) — atlanır ve sayılır
            st.lines_bad += 1
            continue
        if isinstance(row, dict):
            st.rows += 1
            yield row


def iter_rows(root: Path | str, *, sealed_after_ms: int | None = None,
              stats: ReadStats | None = None) -> Iterator[dict[str, Any]]:
    """Ham satır akışı: arşiv segmentleri (manifest sırası) sonra sıcak dosya. TEKİLLEŞTİRME YAPMAZ (tarayıcı yapar).

    `sealed_after_ms`: bu andan ÖNCE mühürlenmiş segment atlanır — segmentin bütün satırları mühürden önce YAZILMIŞTIR
    ve karar anı (`as_of`) yazım anından sonra olamaz; bu yüzden `since` süzgeci ve "son 7 gün" kapsamı için kayıpsızdır.
    Dosya sha256'sı manifestle tutmayan / eksik / açılamayan segment atlanır (`segments_bad`)."""
    root = Path(root)
    st = stats if stats is not None else ReadStats()
    seg_dir = root / ARCHIVE_DIR / SEGMENTS_DIR
    for seg in _segments(manifest(root)):
        st.segments_total += 1
        if sealed_after_ms is not None:
            sealed = _ms(seg.get("created_at"))
            if sealed is not None and sealed < sealed_after_ms:
                st.segments_skipped += 1
                continue
        name = str(seg.get("file") or "")
        if not name or "/" in name or "\\" in name or name.startswith("."):
            st.segments_bad += 1
            continue
        try:
            blob = (seg_dir / name).read_bytes()
        except OSError:
            st.segments_bad += 1
            continue
        if hashlib.sha256(blob).hexdigest() != str(seg.get("sha256") or ""):
            st.segments_bad += 1                        # checksum tutmayan segment istatistiğe KATILMAZ
            continue
        try:
            data = gzip.decompress(blob)
        except (OSError, EOFError, ValueError):
            st.segments_bad += 1
            continue
        del blob
        st.segments_read += 1
        yield from _parse_lines(data, st)
        del data
    hot = root / HOT_FILE
    try:
        data = hot.read_bytes()
    except OSError:
        return
    st.hot_present = True
    st.hot_bytes = len(data)
    yield from _parse_lines(data, st)


# ============================================================================ tarama (anahtar başına kompakt durum)
class _Scan:
    """Akıştan anahtar başına EN SON revizyon + rev-0 bağlamı. Anahtar başına tek paketli kayıt (`_REC`); bağlam
    demetleri tekilleştirilir (interning)."""

    def __init__(self, *, since_ms: int | None, until_ms: int | None) -> None:
        self.since_ms, self.until_ms = since_ms, until_ms
        self.words: list[str | None] = [None]
        self._word: dict[str | None, int] = {None: 0}
        self.ctxs: list[tuple] = []
        self._ctx_id: dict[tuple, int] = {}
        #: 64-bit anahtar özeti → paketli kayıt (`_REC`)
        self.keys: dict[int, bytes] = {}
        #: (coin, anlık görüntü durumu) → (as_of_ms, boyut demeti, sembol) — `--for-symbol` için en yeni anlık görüntü
        self.snaps: dict[tuple[str, str], tuple[int, tuple, str]] = {}
        self.n: dict[str, int] = {k: 0 for k in (
            "rows_used", "rows_foreign", "rows_invalid", "dup_rows", "dup_rows_replaced", "stale_revs", "excluded_window",
            "snapshot_schema_other", "xp_entry", "xp_outcome", "xp_cf")}

    def _w(self, s: Any) -> int:
        k = None if s is None else str(s)
        c = self._word.get(k)
        if c is None:
            c = len(self.words)
            self.words.append(k)
            self._word[k] = c
        return c

    def ingest(self, row: Mapping[str, Any]) -> None:
        cnt = self.n
        kind = row.get("kind")
        if row.get("schema") != ROW_SCHEMA or kind not in KINDS:
            cnt["rows_foreign"] += 1
            return
        rev = row.get("rev")
        if (not isinstance(rev, int) or isinstance(rev, bool) or rev < 0 or row.get("book") not in R.BOOKS
                or str(row.get("app_mode") or "").upper() != R.APP_MODE_PAPER):
            cnt["rows_invalid"] += 1
            return
        skey = row.get("cf_key") if kind == R.KIND_CF else row.get("trade_key")
        if not skey:
            cnt["rows_invalid"] += 1
            return
        cnt["rows_used"] += 1
        cnt[kind] += 1
        hk = _h64(("C|" if kind == R.KIND_CF else "R|") + str(skey))
        old = self.keys.get(hk)
        rec = list(_REC.unpack(old)) if old is not None else list(_REC_EMPTY)
        changed = old is None
        rank = 2 if (kind == R.KIND_ENTRY or (kind == R.KIND_CF and rev == 0)) else 1
        if rank > rec[0]:
            rec[0] = rank
            rec[1], rec[2] = self._context(row, kind, rank)
            changed = True
        elif kind == R.KIND_ENTRY:
            cnt["dup_rows"] += 1
        if kind != R.KIND_ENTRY:
            if rev > rec[3]:
                vals, code, ex = self._result(row, kind)
                rec[3], rec[4], rec[5] = rev, code, ex
                rec[6:12] = vals
                changed = True
            elif rev == rec[3]:
                # aynı (tür, anahtar, rev) = aynı row_id → SONUNCU kalır (2026-09-29, inceleme bulgusu): akış yazım
                # sırasıdır (arşiv manifest sırası, sonra sıcak dosya); tekrar ancak yeniden yayınla (G/Ç hatası sonrası
                # geri kesilemeyen bayt, imleç kaybı) oluşur ve imlecin işlediği EN SON yayındır. Eskiden ilk (bayat)
                # kopya kalıyor, net etiket istatistiğe hiç girmiyordu.
                cnt["dup_rows"] += 1
                vals, code, ex = self._result(row, kind)
                if [rec[4], rec[5]] != [code, ex] or any(not (a == b or (a != a and b != b))
                                                         for a, b in zip(rec[6:12], vals)):
                    cnt["dup_rows_replaced"] += 1
                    rec[4], rec[5] = code, ex
                    rec[6:12] = vals
                    changed = True
            else:
                cnt["stale_revs"] += 1
        if changed:
            self.keys[hk] = _REC.pack(*rec)

    def _context(self, row: Mapping[str, Any], kind: str, rank: int) -> tuple[int, int]:
        """(bağlam id, gün). Pencere dışı → (_EXCLUDED, -1)."""
        as_of = row.get("as_of_ms")
        if not isinstance(as_of, int) or isinstance(as_of, bool):
            as_of = _ms(row.get("opened_at") if kind != R.KIND_CF else row.get("created_at"))
        if (self.since_ms is not None and (as_of is None or as_of < self.since_ms)) or \
                (self.until_ms is not None and (as_of is None or as_of >= self.until_ms)):
            self.n["excluded_window"] += 1
            return _EXCLUDED, -1
        side = str(row.get("side") or "").upper()
        coin = coin_of(row.get("symbol"))
        snap = row.get("snapshot") if rank == 2 else None
        dims: tuple = (UNKNOWN,) * len(DIMS)
        if isinstance(snap, Mapping):
            if snap.get("schema_id") != S.SCHEMA_ID or snap.get("schema_sha") != S.SCHEMA_SHA:
                sst = SNAP_SCHEMA_OTHER                   # başka şema sürümü: kovası bu sürümle KARIŞTIRILMAZ
                self.n["snapshot_schema_other"] += 1
            else:
                sst = str(row.get("snapshot_status") or snap.get("status") or SNAP_NONE)
                b = S.bucket(snap, side)
                dims = tuple(b[d] for d in DIMS)
                if coin and as_of is not None:
                    k = (coin, sst)
                    prev = self.snaps.get(k)
                    if prev is None or as_of > prev[0]:
                        self.snaps[k] = (int(as_of), dims, str(row.get("symbol") or coin))
        else:
            sst = SNAP_NONE
        w = self._w
        t = (_CF if kind == R.KIND_CF else _REAL, w(row.get("book")),
             w(row.get("setup_key") or "%s|?|%s" % (row.get("book"), side)), w(row.get("family_key")), w(side), w(coin),
             w(sst), *[w(d) for d in dims], w(row.get("origin")), w(row.get("scorecard_class") if kind != R.KIND_CF else None),
             1 if (kind == R.KIND_CF and row.get("baseline_blocked")) else 0,
             w(row.get("reason_family") if kind == R.KIND_CF else None), int(rank))
        c = self._ctx_id.get(t)
        if c is None:
            c = len(self.ctxs)
            self.ctxs.append(t)
            self._ctx_id[t] = c
        return c, (int(as_of) // _DAY_MS) if as_of is not None else -1

    def _result(self, row: Mapping[str, Any], kind: str) -> tuple[list, int, int]:
        if kind == R.KIND_OUTCOME:
            st = _CLOSED
            r_net = _f(row.get("r_net"))
            basis = R.NET if (r_net is not None and row.get("in_net_stats") is not False) else _B_NONE
            ex = row.get("exit_reason")
        else:
            st = str(row.get("status") or _OTHER)
            st = st if st in _ST_I else _OTHER
            basis, r_net = _B_NONE, None
            if st == R.LABELLED:
                basis = str(row.get("r_basis") or R.GROSS_LEGACY)
                if basis == R.NET:
                    r_net = _f(row.get("r_net"))
                    # çifte kilit: satırın beyanı + etiket sürümü sınıfı (yalnız cf_label_v2+ net sayılır)
                    if (r_net is None or row.get("in_net_stats") is not True
                            or R.cf_label_class(row.get("label_version")) != R.NET):
                        basis, r_net = R.NET_UNAVAILABLE, None
                elif basis not in _BASIS_I:
                    basis = R.UNKNOWN_LABEL_VERSION
            out = row.get("outcome")
            ex = out.get("exit_reason") if isinstance(out, Mapping) else None
        g = row.get
        vals = [math.nan if r_net is None else r_net]
        for key in _RES_FLOATS:
            v = g(key)
            if v.__class__ is float:                     # hızlı yol (satır kurucusu sonlu float ya da None yazar)
                vals.append(v if v - v == 0.0 else math.nan)
            else:
                v = _f(v)
                vals.append(math.nan if v is None else v)
        code = (_ST_I[st] << 4) | (_BASIS_I[basis] << 1) | (1 if row.get("final") is True else 0)
        return vals, code, self._w(str(ex) if ex else None)


class Observations:
    """Gözlem tablosu (numpy; bağlamı olan anahtar başına bir satır, (gün, anahtar özeti) sırasıyla deterministik)."""

    def __init__(self, scan: _Scan, read: ReadStats) -> None:
        self.words = scan.words
        self._code = dict(scan._word)
        self.read = read
        self.counts = dict(scan.n)
        self.snaps = dict(scan.snaps)
        n_keys = len(scan.keys)
        if n_keys:
            recs = np.frombuffer(b"".join(scan.keys.values()), dtype=_REC_DT)
            hks = np.fromiter(scan.keys.keys(), dtype=np.uint64, count=n_keys)
        else:
            recs, hks = np.zeros(0, dtype=_REC_DT), np.zeros(0, dtype=np.uint64)
        scan.keys.clear()                                 # anahtar sözlüğü burada bırakılır (tepe bellek)
        cx = recs["ctx"]
        self.counts.update({"keys": n_keys, "keys_no_context": int((cx == _NO_CTX).sum()),
                            "orphan_results": int(((cx == _NO_CTX) & (recs["rev"] >= 0)).sum())})
        keep = cx >= 0
        recs, hks = recs[keep], hks[keep]
        order = np.lexsort((hks, recs["day"]))
        recs = recs[order]
        del hks, order, keep
        self.n = n = int(recs.shape[0])
        ctx = np.array(scan.ctxs, dtype=np.int32).reshape(-1, len(_CTX)) if scan.ctxs \
            else np.zeros((0, len(_CTX)), np.int32)
        ci = recs["ctx"].astype(np.intp)

        def col(name: str) -> np.ndarray:
            return ctx[ci, _CI[name]] if n else np.zeros(0, np.int32)

        self.src = col("src").astype(np.int8)
        self.book, self.setup, self.family, self.side = col("book"), col("setup"), col("family"), col("side")
        self.coin, self.snap, self.origin, self.sc, self.rf = col("coin"), col("snap"), col("origin"), col("sc"), col("rf")
        self.dims = np.ascontiguousarray(np.stack([col(d) for d in DIMS], axis=1)) if n else np.zeros((0, len(DIMS)), np.int32)
        self.a15 = col("a15").astype(bool)
        self.rank = col("rank").astype(np.int8)
        self.day = recs["day"].astype(np.int64)
        self.has_res = recs["rev"] >= 0
        cd = recs["code"].astype(np.int64)
        self.stc = ((cd >> 4) & 0xF).astype(np.int8)
        self.basis = ((cd >> 1) & 0x7).astype(np.int8)
        self.final = (cd & 1).astype(bool) & self.has_res
        self.exit = recs["exit"].astype(np.int32)
        fl = recs["f"]
        self.r_net, self.r_gross, self.cost_r = fl[:, 0].copy(), fl[:, 1].copy(), fl[:, 2].copy()
        self.mfe_r, self.mae_r, self.hold_h = fl[:, 3].copy(), fl[:, 4].copy(), fl[:, 5].copy()
        del recs, fl, cd
        #: sembol×gün kümesi (küme bootstrap'ı)
        self.cluster = self.coin.astype(np.int64) * 1_000_000 + (self.day + 1)

    def code(self, word: Any) -> int:
        """Metin → kod; tabloda hiç geçmiyorsa −1 (hiçbir satırla eşleşmez)."""
        return self._code.get(None if word is None else str(word), -1)

    def word(self, code: int) -> str | None:
        return self.words[int(code)] if 0 <= int(code) < len(self.words) else None


def scan(root: Path | str, *, since_ms: int | None = None, until_ms: int | None = None) -> Observations:
    """Depoyu TEK geçişte akışla okur (salt okur) ve gözlem tablosunu kurar. `since`/`until` karar anına (`as_of`)
    uygulanır; `since`ten önce mühürlenmiş segmentler hiç açılmaz."""
    rs = ReadStats()
    sc = _Scan(since_ms=since_ms, until_ms=until_ms)
    for row in iter_rows(root, sealed_after_ms=since_ms, stats=rs):
        sc.ingest(row)
    obs = Observations(sc, rs)
    return obs


# ============================================================================ istatistik
def _boot_ci(r: np.ndarray, *, iters: int = BOOT_ITERS, seed: int = SEED_IID) -> tuple[list[float] | None, str]:
    """Ortalama için %95 iid bootstrap (deterministik tohum; n < 5 → None; n > BOOT_MAX_N → normal yaklaşım). Karnenin
    `_bootstrap_ci`siyle aynı yöntem, sayısal olarak aynı DEĞİL (bkz. modül notu ve `CI_IID_NOTE_TR`; 2026-09-29)."""
    n = int(r.size)
    if n < 5:
        return None, "none"
    if n > BOOT_MAX_N:
        m, sd = float(r.mean()), float(r.std(ddof=1))
        h = Z95 * sd / math.sqrt(n)
        return [round(m - h, 4), round(m + h, 4)], "normal"
    rng = np.random.default_rng(seed)
    chunk = max(1, min(iters, _BOOT_CHUNK // n))
    means = np.empty(iters, dtype=np.float64)
    i = 0
    while i < iters:
        m = min(chunk, iters - i)
        means[i:i + m] = r[rng.integers(0, n, size=(m, n))].mean(axis=1)
        i += m
    means.sort()
    return [round(float(means[int(0.025 * iters)]), 4), round(float(means[int(0.975 * iters)]), 4)], "bootstrap"


def _cluster_ci(r: np.ndarray, cl: np.ndarray, *, iters: int = BOOT_ITERS,
                seed: int = SEED_CLUSTER) -> tuple[list[float] | None, str]:
    """Sembol×gün küme bootstrap'ı (küme sayısı < 5 → None; > BOOT_MAX_N → küme-sağlam normal yaklaşım)."""
    if r.size == 0:
        return None, "none"
    _u, inv = np.unique(cl, return_inverse=True)
    C = int(_u.size)
    if C < MIN_CLUSTERS:
        return None, "none"
    sums = np.bincount(inv, weights=r)
    cnts = np.bincount(inv).astype(np.float64)
    if C > BOOT_MAX_N:
        m = float(sums.sum() / cnts.sum())
        var = float(((sums - m * cnts) ** 2).sum()) / (float(cnts.sum()) ** 2) * C / (C - 1)
        h = Z95 * math.sqrt(var)
        return [round(m - h, 4), round(m + h, 4)], "normal"
    rng = np.random.default_rng(seed)
    chunk = max(1, min(iters, _BOOT_CHUNK // C))
    means = np.empty(iters, dtype=np.float64)
    i = 0
    while i < iters:
        k = min(chunk, iters - i)
        idx = rng.integers(0, C, size=(k, C))
        means[i:i + k] = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
        i += k
    means.sort()
    return [round(float(means[int(0.025 * iters)]), 4), round(float(means[int(0.975 * iters)]), 4)], "bootstrap"


def _median_ci(sr: np.ndarray) -> list[float] | None:
    """Medyan için dağılımdan bağımsız %95 sıra-istatistiği aralığı (Conover); `sr` sıralı. n < 10 → None."""
    n = int(sr.size)
    if n < MIN_N_DESCRIPTIVE:
        return None
    h = Z95 * math.sqrt(n)
    lo = max(1, int(math.floor((n - h) / 2.0)))
    hi = min(n, int(math.ceil(1 + (n + h) / 2.0)))
    return [round(float(sr[lo - 1]), 4), round(float(sr[hi - 1]), 4)]


def verdict(n: int, n_clusters: int, ci_iid: Any, ci_cluster: Any, *, min_n: int = MIN_N_VERDICT) -> tuple[str, str, str]:
    """(kademe, hüküm kodu, gerekçe). Kademeler: n < 10 INSUFFICIENT_SAMPLE; n < min_n DESCRIPTIVE (hüküm VERİ
    YETERSİZ); aksi VERDICT. Hüküm karnenin kuralıyla, İKİ aralık (iid VE küme) birlikte."""
    if n < MIN_N_DESCRIPTIVE:
        return TIER_INSUFFICIENT, INSUFFICIENT_SAMPLE, "N_LT_%d" % MIN_N_DESCRIPTIVE
    if n < min_n:
        return TIER_DESCRIPTIVE, INSUFFICIENT_SAMPLE, "N_LT_MIN_N_%d" % min_n
    if n_clusters < MIN_CLUSTERS:
        return TIER_VERDICT, INSUFFICIENT_SAMPLE, "CLUSTERS_LT_%d" % MIN_CLUSTERS
    if not ci_iid or not ci_cluster:
        return TIER_VERDICT, INSUFFICIENT_SAMPLE, "CI_MISSING"
    if ci_iid[1] < 0 and ci_cluster[1] < 0:
        return TIER_VERDICT, LOSS, "BOTH_CI_BELOW_0"
    if ci_iid[0] > 0 and ci_cluster[0] > 0:
        return TIER_VERDICT, WIN, "BOTH_CI_ABOVE_0"
    return TIER_VERDICT, UNDECIDED, "CI_SPANS_0_OR_DISAGREE"


def shrunk_mean(n_coin: int, mean_coin: float | None, mean_pooled: float, k: float = SHRINK_K) -> float:
    """Kısmi havuz (KARARLAR 4): (n_coin·ort_coin + k·ort_havuz) / (n_coin + k); coin verisi yoksa havuz ortalaması."""
    if n_coin <= 0 or mean_coin is None:
        return float(mean_pooled)
    return (n_coin * float(mean_coin) + float(k) * float(mean_pooled)) / (n_coin + float(k))


def _nanmean(x: np.ndarray) -> float | None:
    x = x[np.isfinite(x)]
    return round(float(x.mean()), 4) if x.size else None


_NUMERIC_KEYS = ("wins", "win_rate", "win_rate_ci95", "mean_r", "mean_r_ci95", "mean_r_ci95_cluster", "median_r",
                 "median_r_ci95", "sum_r", "mean_cost_r", "mean_mfe_r", "mean_mae_r", "mean_hold_h", "exit_mix", "coins",
                 "coin", "ci_method")


def column_stats(o: Observations, idx: np.ndarray, *, min_n: int = MIN_N_VERDICT, ci: bool = True,
                 k: float = SHRINK_K, focus_coin: str | None = None) -> dict[str, Any]:
    """Bir sütunun (gerçek ya da net karşı-olgusal) istatistiği — `idx` gözlem satırları. n < 10 → sayı YOK."""
    n = int(idx.size)
    cl = o.cluster[idx]
    n_cl = int(np.unique(cl).size) if n else 0
    out: dict[str, Any] = {"n": n, "n_final": int(o.final[idx].sum()) if n else 0, "n_clusters": n_cl}
    if n < MIN_N_DESCRIPTIVE:
        tier, code, why = verdict(n, n_cl, None, None, min_n=min_n)
        out.update({key: None for key in _NUMERIC_KEYS})
        out.update({"tier": tier, "verdict": code, "verdict_tr": VERDICT_TR[code], "verdict_reason": why})
        return out
    r = o.r_net[idx]
    wins = int((r > 0).sum())
    wlo, whi = wilson(wins, n)
    mean = float(r.mean())
    sr = np.sort(r)
    ci_iid, m1 = _boot_ci(r) if ci else (None, "off")
    ci_cl, m2 = _cluster_ci(r, cl) if ci else (None, "off")
    tier, code, why = verdict(n, n_cl, ci_iid, ci_cl, min_n=min_n)
    ex = o.exit[idx]
    ue, ce = np.unique(ex, return_counts=True)
    mix = sorted(((o.word(u) or "?", int(c)) for u, c in zip(ue, ce)), key=lambda t: (-t[1], t[0]))[:TOP_EXITS]
    # coin başına kısmi havuz (havuz ortalamasının YANINDA; havuzun yerine GEÇMEZ)
    cc = o.coin[idx]
    uc, inv = np.unique(cc, return_inverse=True)
    cnt = np.bincount(inv)
    sm = np.bincount(inv, weights=r)
    rows = []
    for j, u in enumerate(uc):
        mc = float(sm[j] / cnt[j])
        rows.append({"coin": o.word(u), "n": int(cnt[j]), "mean_r": round(mc, 4),
                     "shrunk_mean_r": round(shrunk_mean(int(cnt[j]), mc, mean, k), 4)})
    rows.sort(key=lambda d: (-d["n"], str(d["coin"])))
    focus = None
    if focus_coin is not None:                          # odak coin: verisi yoksa büzülmüş = havuz (yeni coin)
        hit = [d for d in rows if d["coin"] == focus_coin]
        focus = dict(hit[0]) if hit else {"coin": focus_coin, "n": 0, "mean_r": None, "shrunk_mean_r": round(mean, 4)}
    out.update({
        "wins": wins, "win_rate": round(wins / n, 4), "win_rate_ci95": [round(wlo, 4), round(whi, 4)],
        "mean_r": round(mean, 4), "mean_r_ci95": ci_iid, "mean_r_ci95_cluster": ci_cl,
        "median_r": round(float(np.median(r)), 4), "median_r_ci95": _median_ci(sr), "sum_r": round(float(r.sum()), 4),
        "mean_cost_r": _nanmean(o.cost_r[idx]), "mean_mfe_r": _nanmean(o.mfe_r[idx]), "mean_mae_r": _nanmean(o.mae_r[idx]),
        "mean_hold_h": _nanmean(o.hold_h[idx]), "exit_mix": {a: b for a, b in mix},
        "coins": rows[:TOP_COINS], "coin": focus, "ci_method": {"iid": m1, "cluster": m2},
        "tier": tier, "verdict": code, "verdict_tr": VERDICT_TR[code], "verdict_reason": why})
    return out


def _gross_stats(o: Observations, idx: np.ndarray) -> dict[str, Any]:
    """Eski BRÜT karşı-olgusal etiketler: yalnız bilgi — net değil, hükme ve karşılaştırmaya GİRMEZ."""
    n = int(idx.size)
    g = o.r_gross[idx]
    return {"n": n, "mean_r_gross": round(float(g.mean()), 4) if n >= MIN_N_DESCRIPTIVE else None,
            "note_tr": "net değil; hükme ve karşılaştırmaya girmez"}


# ============================================================================ sorgu
@dataclass
class Query:
    """Rapor sorgusu (CLI bayraklarının karşılığı). Boyut süzgeçleri `dims` (boyut → değer)."""
    book: str | None = None
    setup: str | None = None
    side: str | None = None
    family: str | None = None
    group_by: str = "both"
    dims: dict[str, str] = field(default_factory=dict)
    kind: str = "both"
    cohort: str = "all"
    cf_reason_family: str | None = None
    origin: str = "all"
    snapshot: str = "ok"
    min_n: int = MIN_N_VERDICT
    shrink_k: float = SHRINK_K
    backoff: bool = True
    cells: bool = False
    cells_min: int = MIN_N_DESCRIPTIVE
    focus_coin: str | None = None
    ci: bool = True

    def validate(self) -> "Query":
        if self.group_by not in ("setup", "family", "both"):
            raise ReportError("group_by: setup | family | both")
        if self.kind not in ("real", "cf", "both"):
            raise ReportError("kind: real | cf | both")
        if self.cohort not in COHORT_FILTERS:
            raise ReportError("cohort: %s" % " | ".join(COHORT_FILTERS))
        if self.origin not in ("all", "live"):
            raise ReportError("origin: all | live")
        if self.snapshot not in SNAPSHOT_FILTERS:
            raise ReportError("snapshot: %s" % " | ".join(SNAPSHOT_FILTERS))
        if int(self.min_n) < MIN_N_DESCRIPTIVE:
            raise ReportError("min_n en az %d olmalı (tanımlayıcı eşik)" % MIN_N_DESCRIPTIVE)
        if float(self.shrink_k) < 0:
            raise ReportError("shrink_k negatif olamaz")
        if int(self.cells_min) < 1:
            raise ReportError("cells_min en az 1")
        if self.side is not None and str(self.side).upper() not in (R.LONG, R.SHORT):
            raise ReportError("side: LONG | SHORT")
        if self.book and str(self.book).lower() != "all" and self.book not in R.BOOKS and R.book_for_name(self.book) is None:
            raise ReportError("bilinmeyen defter: %r (geçerli: %s ya da adları %s)"
                              % (self.book, ", ".join(R.BOOKS), ", ".join(v[0] for v in R.BOOKS.values())))
        self.dims = normalize_dims(self.dims)
        return self


def normalize_dims(d: Mapping[str, Any] | None) -> dict[str, str]:
    """Boyut sözlüğünü doğrular ve büyük harfe çevirir; bilinmeyen boyut/değer → ReportError."""
    out: dict[str, str] = {}
    for k, v in (d or {}).items():
        kk = str(k).strip().lower()
        kk = {"oynaklik": "vol", "oynaklık": "vol", "hacim": "volume", "yapi": "structure", "yapı": "structure",
              "volatility": "vol"}.get(kk, kk)
        if kk not in DIMS:
            raise ReportError("bilinmeyen boyut: %r (geçerli: %s)" % (k, ", ".join(DIMS)))
        vv = str(v).strip().upper()
        if vv not in DIM_VALUES[kk]:
            raise ReportError("%s için geçersiz değer: %r (geçerli: %s)" % (kk, v, ", ".join(DIM_VALUES[kk])))
        out[kk] = vv
    return {k: out[k] for k in DIMS if k in out}


def parse_situation(text: str | None) -> dict[str, str]:
    """`trend=UP,vol=HIGH,btc=UP,volume=NORMAL,structure=HH_HL` → sözlük (alt küme serbest)."""
    out: dict[str, str] = {}
    for part in str(text or "").replace(";", ",").split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ReportError("durum parçası 'boyut=değer' olmalı: %r" % part)
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return normalize_dims(out)


def backoff_levels(dims: Mapping[str, str], *, backoff: bool = True) -> list[tuple[dict[str, str], list[str]]]:
    """[(seviye boyutları, düşürülenler)] — L0 verilen boyutlar; ardından önceden kayıtlı sırayla birer birer düşer."""
    cur = {d: dims[d] for d in DIMS if d in dims}
    levels: list[tuple[dict[str, str], list[str]]] = [(dict(cur), [])]
    if not backoff:
        return levels
    dropped: list[str] = []
    for d in BACKOFF_ORDER:
        if d in cur:
            cur = {k: v for k, v in cur.items() if k != d}
            dropped = dropped + [d]
            levels.append((dict(cur), list(dropped)))
    return levels


@dataclass
class _Cols:
    real: np.ndarray
    real_open: np.ndarray
    real_no_net: np.ndarray
    real_entry_missing: np.ndarray
    cf_net: np.ndarray
    cf_gross: np.ndarray
    cf_other: dict[str, np.ndarray]


def _cols(o: Observations) -> _Cols:
    is_real, is_cf = o.src == _REAL, o.src == _CF
    fin_n, fin_g = np.isfinite(o.r_net), np.isfinite(o.r_gross)
    lab = o.stc == _ST_I[R.LABELLED]
    real = is_real & o.has_res & (o.basis == _BASIS_I[R.NET]) & fin_n
    other = {"cf_pending": is_cf & (o.stc == _ST_I[R.PENDING]),
             "cf_superseded": is_cf & (o.stc == _ST_I[R.SUPERSEDED]),
             "cf_expired": is_cf & (o.stc == _ST_I[R.EXPIRED]),
             "cf_dropped": is_cf & (o.stc == _ST_I[R.DROPPED]),
             "cf_vanished": is_cf & (o.stc == _ST_I[R.VANISHED]),
             "cf_unknown_label": is_cf & lab & (o.basis == _BASIS_I[R.UNKNOWN_LABEL_VERSION]),
             "cf_net_unavailable": is_cf & lab & (o.basis == _BASIS_I[R.NET_UNAVAILABLE])}
    return _Cols(real=real, real_open=is_real & ~o.has_res, real_no_net=is_real & o.has_res & ~real,
                 real_entry_missing=is_real & (o.rank < 2),
                 cf_net=is_cf & lab & (o.basis == _BASIS_I[R.NET]) & fin_n,
                 cf_gross=is_cf & lab & (o.basis == _BASIS_I[R.GROSS_LEGACY]) & fin_g, cf_other=other)


def _codes_where(o: Observations, pred) -> list[int]:
    return [i for i, w in enumerate(o.words) if w is not None and pred(w)]


def filter_mask(o: Observations, q: Query) -> np.ndarray:
    """Sorgunun satır süzgeci (kurulum/defter/yön/aile/köken/anlık görüntü/tür/kohort/neden ailesi)."""
    m = np.ones(o.n, dtype=bool)
    if q.book and str(q.book).lower() != "all":
        key = q.book if q.book in R.BOOKS else R.book_for_name(q.book)
        m &= o.book == o.code(key)
    if q.setup:
        want = str(q.setup).strip().lower()
        m &= np.isin(o.setup, _codes_where(o, lambda w: w.count("|") >= 2 and w.split("|", 1)[1].rsplit("|", 1)[0].lower() == want))
    if q.side:
        m &= o.side == o.code(str(q.side).upper())
    if q.family:
        fam = str(q.family).strip().upper()
        m &= np.isin(o.family, _codes_where(o, lambda w: w.split("|", 1)[0] == fam and "|" in w))
    if q.origin == "live":
        m &= o.origin == o.code(R.LIVE)
    allowed = SNAPSHOT_FILTERS[q.snapshot]
    if allowed is not None:
        m &= np.isin(o.snap, [o.code(s) for s in allowed])
    if q.kind == "real":
        m &= o.src == _REAL
    elif q.kind == "cf":
        m &= o.src == _CF
    cls = COHORT_FILTERS[q.cohort]
    if cls is not None:
        m &= (o.src == _CF) | (o.sc == o.code(cls))
    if q.cf_reason_family:
        m &= (o.src == _REAL) | (o.rf == o.code(str(q.cf_reason_family).upper()))
    return m


def _cell_mask(o: Observations, base: np.ndarray, gb: str, gcode: int, dims: Mapping[str, str]) -> np.ndarray:
    m = base & ((o.setup if gb == "setup" else o.family) == gcode)
    for d, v in dims.items():
        m = m & (o.dims[:, DIMS.index(d)] == o.code(v))
    return m


def _level_doc(o: Observations, M: np.ndarray, cols: _Cols, q: Query, focus: str | None) -> dict[str, Any]:
    kw = {"min_n": q.min_n, "ci": q.ci, "k": q.shrink_k, "focus_coin": focus}
    real_idx = np.flatnonzero(M & cols.real)
    cf_m = M & cols.cf_net
    doc: dict[str, Any] = {
        "real": column_stats(o, real_idx, **kw),
        "real_by_cohort": {c: column_stats(o, np.flatnonzero(M & cols.real & (o.sc == o.code(c))), min_n=q.min_n,
                                           ci=q.ci, k=q.shrink_k) for c in COHORT_ORDER},
        "cf": column_stats(o, np.flatnonzero(cf_m), **kw),
        "cf_excl_a15": column_stats(o, np.flatnonzero(cf_m & ~o.a15), **kw),
        "cf_gross_legacy": _gross_stats(o, np.flatnonzero(M & cols.cf_gross)),
    }
    doc["cf"]["n_a15"] = int((cf_m & o.a15).sum())
    cov = {"real_open": int((M & cols.real_open).sum()), "real_no_net": int((M & cols.real_no_net).sum()),
           "real_entry_missing": int((M & cols.real_entry_missing).sum())}
    cov.update({k: int((M & v).sum()) for k, v in cols.cf_other.items()})
    doc["coverage"] = cov
    return doc


def _answer(levels: list[dict[str, Any]], col: str, min_n: int) -> dict[str, Any] | None:
    for lv in levels:
        st = lv[col]
        if st["n"] >= min_n:
            return {"level": lv["level"], "n": st["n"], "verdict": st["verdict"], "verdict_tr": st["verdict_tr"],
                    "mean_r": st["mean_r"], "dims": lv["dims"]}
    return None


def _group_info(o: Observations, gb: str, g: int) -> dict[str, Any]:
    name = o.word(g) or "?"
    info: dict[str, Any] = {"group_by": gb, "group": name}
    if gb == "setup":
        parts = name.split("|")
        book = parts[0] if parts else None
        info.update({"book": book, "book_name": (R.BOOKS.get(book) or (None,))[0],
                     "setup_type": "|".join(parts[1:-1]) if len(parts) >= 3 else None,
                     "side": parts[-1] if len(parts) >= 3 else None})
    else:
        fam, _, side = name.partition("|")
        info.update({"family": fam, "side": side or None})
    return info


def _groups(o: Observations, base: np.ndarray, q: Query) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for gb in (GROUP_BY if q.group_by == "both" else (q.group_by,)):
        arr = o.setup if gb == "setup" else o.family
        codes = [int(c) for c in np.unique(arr[base]) if o.word(int(c)) is not None]
        out.extend((gb, c) for c in sorted(codes, key=lambda c: str(o.word(c))))
    return out


def _coverage_totals(o: Observations, base: np.ndarray, cols: _Cols, base_any: np.ndarray | None = None) -> dict[str, Any]:
    """Kapsam sayımları. `real_entry_missing` anlık görüntü süzgecinden BAĞIMSIZ sayılır (`base_any`, 2026-09-29): giriş
    satırı olmayan işlemin anlık görüntüsü yoktur; varsayılan süzgeç (yalnız OK) onu gizliyor, kayıp görünmüyordu."""
    miss = base_any if base_any is not None else base
    tot = {"real_closed_net": int((base & cols.real).sum()), "real_open": int((base & cols.real_open).sum()),
           "real_no_net": int((base & cols.real_no_net).sum()),
           "real_entry_missing": int((miss & cols.real_entry_missing).sum()),
           "cf_net": int((base & cols.cf_net).sum()), "cf_net_a15": int((base & cols.cf_net & o.a15).sum()),
           "cf_gross_legacy": int((base & cols.cf_gross).sum())}
    tot.update({k: int((base & v).sum()) for k, v in cols.cf_other.items()})
    mix: dict[str, dict[str, int]] = {}
    for src, name in ((_REAL, "real"), (_CF, "cf")):
        sel = o.snap[o.src == src]
        u, c = np.unique(sel, return_counts=True)
        mix[name] = {str(o.word(a)): int(b) for a, b in sorted(zip(u, c), key=lambda t: str(o.word(t[0])))}
    tot["snapshot_status_mix_all_rows"] = mix
    return tot


def _full_cells(o: Observations, base: np.ndarray, cols: _Cols, gb: str) -> list[tuple[int, tuple, int, int]]:
    """(grup, tam boyut kodları, n_gerçek, n_olsaydı) — en az bir net gözlemi olan her tam hücre."""
    arr = o.setup if gb == "setup" else o.family
    sel = base & (cols.real | cols.cf_net) & (arr != 0)
    if not sel.any():
        return []
    stack = np.column_stack([arr[sel], o.dims[sel], cols.real[sel].astype(np.int32), cols.cf_net[sel].astype(np.int32)])
    keys, inv = np.unique(stack[:, :6], axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    n_real = np.bincount(inv, weights=stack[:, 6]).astype(int)
    n_cf = np.bincount(inv, weights=stack[:, 7]).astype(int)
    return [(int(keys[i, 0]), tuple(int(x) for x in keys[i, 1:6]), int(n_real[i]), int(n_cf[i])) for i in range(keys.shape[0])]


def _cells_doc(o: Observations, base: np.ndarray, cols: _Cols, q: Query, focus: str | None) -> list[dict[str, Any]]:
    """`--cells`: her tam hücre (n ≥ cells_min) + sütun başına geri çekilme cevabı ("nerede kazandık" haritası)."""
    out: list[dict[str, Any]] = []
    for gb in (GROUP_BY if q.group_by == "both" else (q.group_by,)):
        cells = [c for c in _full_cells(o, base, cols, gb) if max(c[2], c[3]) >= q.cells_min]
        cells.sort(key=lambda c: (str(o.word(c[0])), -(c[2] + c[3]), tuple(str(o.word(x)) for x in c[1])))
        for g, dcodes, _nr, _nc in cells:
            dims = {DIMS[i]: str(o.word(dcodes[i])) for i in range(len(DIMS))}
            levels = []
            for i, (dd, dropped) in enumerate(backoff_levels(dims, backoff=q.backoff)):
                M = _cell_mask(o, base, gb, g, dd)
                if i == 0:
                    lv = {"level": 0, "dims": dd, "dropped": dropped, **_level_doc(o, M, cols, q, focus)}
                else:                               # geri çekilme seviyeleri: önce yalnız sayım (ucuz)
                    lv = {"level": i, "dims": dd, "dropped": dropped,
                          "real": {"n": int((M & cols.real).sum())}, "cf": {"n": int((M & cols.cf_net).sum())}, "_M": M}
                levels.append(lv)
            ans: dict[str, Any] = {}
            for col, cm in (("real", cols.real), ("cf", cols.cf_net)):
                hit = next((lv for lv in levels if lv[col]["n"] >= q.min_n), None)
                if hit is None:
                    ans[col] = None
                    continue
                st = hit[col] if hit["level"] == 0 else column_stats(o, np.flatnonzero(hit["_M"] & cm), min_n=q.min_n,
                                                                        ci=q.ci, k=q.shrink_k, focus_coin=focus)
                ans[col] = {"level": hit["level"], "dims": hit["dims"], "dropped": hit["dropped"], **st}
            full = levels[0]
            out.append({**_group_info(o, gb, g), "dims": dims, "real": full["real"], "cf": full["cf"],
                        "cf_excl_a15": full["cf_excl_a15"], "cf_gross_legacy": full["cf_gross_legacy"],
                        "coverage": full["coverage"], "answer": ans})
    return out


def build(o: Observations, q: Query, *, situation: Mapping[str, Any] | None = None, now: datetime | None = None,
          window: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rapor belgesi (JSON ile aynı içerik; metin `render_tr`). Karar DEĞİLDİR."""
    q.validate()
    base = filter_mask(o, q)
    cols = _cols(o)
    focus = coin_of(q.focus_coin) if q.focus_coin else None
    dims = dict(q.dims)
    doc: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "layer_version": LAYER_VERSION, "banner": BANNER_TR, "banner_line": BANNER_LINE_TR,
        "generated_at": iso(now or datetime.now(timezone.utc)),
        "params": {"min_n": int(q.min_n), "min_n_descriptive": MIN_N_DESCRIPTIVE, "min_clusters": MIN_CLUSTERS,
                   "shrink_k": float(q.shrink_k), "snapshot": q.snapshot, "origin": q.origin, "kind": q.kind,
                   "cohort": q.cohort, "cf_reason_family": q.cf_reason_family, "book": q.book, "setup": q.setup,
                   "side": q.side, "family": q.family, "group_by": q.group_by, "backoff": bool(q.backoff),
                   "backoff_order": list(BACKOFF_ORDER), "dims_order": list(DIMS), "cells": bool(q.cells),
                   "cells_min": int(q.cells_min), "focus_coin": coin_of(q.focus_coin) if q.focus_coin else None,
                   "ci": bool(q.ci), "boot_iters": BOOT_ITERS, "seeds": {"iid": SEED_IID, "cluster": SEED_CLUSTER},
                   "ci_iid_note": CI_IID_NOTE_TR,
                   "window": dict(window or {})},
        "source": {**o.read.as_dict(), **o.counts, "observations": int(o.n)},
        "situation": dict(situation) if situation else ({"mode": "explicit", "dims": dims} if dims else None),
        "coverage": _coverage_totals(o, base, cols, filter_mask(o, replace(q, snapshot="any"))),
        "definitions": {
            "n": "net R'si olan gözlem (gerçek: kapanmış işlem; olsaydı: LABELLED + cf_label_v2+ net)",
            "n_final": "n içinde en son revizyonu kesin (final) olanlar",
            "win": "r_net > 0 (karne kuralı)", "tiers": "n<10 INSUFFICIENT_SAMPLE · 10≤n<min_n DESCRIPTIVE · n≥min_n VERDICT",
            "shrunk_mean_r": "(n_coin·ort_coin + k·ort_havuz)/(n_coin + k)",
            "a15": "baseline_blocked_by dolu karşı-olgusal: 'cf' içinde sayılır (n_a15), 'cf_excl_a15' onlarsız"},
    }
    if q.cells:
        doc["cells"] = _cells_doc(o, base, cols, q, focus)
        return doc
    groups = []
    for gb, g in _groups(o, base, q):
        levels = []
        for i, (dd, dropped) in enumerate(backoff_levels(dims, backoff=q.backoff)):
            M = _cell_mask(o, base, gb, g, dd)
            levels.append({"level": i, "dims": dd, "dropped": dropped, **_level_doc(o, M, cols, q, focus)})
        groups.append({**_group_info(o, gb, g), "levels": levels,
                       "answer": {"real": _answer(levels, "real", q.min_n), "cf": _answer(levels, "cf", q.min_n),
                                  "cf_excl_a15": _answer(levels, "cf_excl_a15", q.min_n)}})
    doc["groups"] = groups
    return doc


def summary(o: Observations, *, now: datetime | None = None, min_n: int = MIN_N_VERDICT, shrink_k: float = SHRINK_K,
            window: Mapping[str, Any] | None = None, top: int = TOP_CELLS) -> dict[str, Any]:
    """Panel kartı özeti: sayımlar + EN ÇOK GÖZLEMLİ tam hücreler (örneklem büyüklüğüne göre; "en iyiler" DEĞİL).
    Varsayılan süzgeç (yalnız OK anlık görüntü, tüm köken). Küçük ve sınırlı (≤ `top` hücre)."""
    q = Query(min_n=min_n, shrink_k=shrink_k).validate()
    base = filter_mask(o, q)
    cols = _cols(o)
    cells = _full_cells(o, base, cols, "setup")
    cells.sort(key=lambda c: (-(c[2] + c[3]), str(o.word(c[0])), tuple(str(o.word(x)) for x in c[1])))
    top_cells = []
    for g, dcodes, _nr, _nc in cells[:max(0, int(top))]:
        dims = {DIMS[i]: str(o.word(dcodes[i])) for i in range(len(DIMS))}
        M = _cell_mask(o, base, "setup", g, dims)
        ent = {**_group_info(o, "setup", g), "dims": dims}
        for col, cm in (("real", cols.real), ("cf", cols.cf_net)):
            st = column_stats(o, np.flatnonzero(M & cm), min_n=min_n, k=shrink_k)
            ent[col] = {k: st[k] for k in ("n", "n_final", "win_rate", "mean_r", "mean_r_ci95", "mean_r_ci95_cluster",
                                          "tier", "verdict", "verdict_tr")}
        top_cells.append(ent)
    return {"schema": SUMMARY_SCHEMA, "layer_version": LAYER_VERSION, "banner": BANNER_TR,
            "generated_at": iso(now or datetime.now(timezone.utc)),
            "params": {"min_n": int(min_n), "shrink_k": float(shrink_k), "snapshot": q.snapshot, "top": int(top),
                       "rank_by": "n_real + n_cf (örneklem büyüklüğü; en iyiler değil)", "window": dict(window or {})},
            "counts": {**_coverage_totals(o, base, cols, filter_mask(o, replace(q, snapshot="any"))),
                       "observations": int(o.n), "rows_read": int(o.read.rows),
                       "keys": int(o.counts.get("keys", 0))},
            "top_cells": top_cells}


# ============================================================================ "bu durumu daha önce gördük mü?"
def situation_for_symbol(o: Observations, symbol: str, *, snapshot: str = "ok") -> dict[str, Any] | None:
    """Sembolün depodaki EN YENİ anlık görüntüsü → boyutlar. Varsayılan yalnız OK; `ok+partial` (ve `any`) ile OK ya da
    PARTIAL arasından en yenisi (eşit anda OK öncelikli). GAP/NO_BARS/ERROR durumu "bu durum" olarak ASLA seçilmez."""
    coin = coin_of(symbol)
    allowed = SNAPSHOT_FILTERS.get(snapshot) or ("OK", "PARTIAL")
    best = None
    for rank, st in enumerate(a for a in allowed if a in ("OK", "PARTIAL")):
        hit = o.snaps.get((coin, st)) if coin else None
        if hit is not None and (best is None or (hit[0], -rank) > (best[1][0], -best[0])):
            best = (rank, hit, st)
    if best is None:
        return None
    _rank, hit, st = best
    return {"mode": "symbol", "symbol": hit[2], "coin": coin, "as_of_ms": hit[0], "as_of": _iso_ms(hit[0]),
            "snapshot_status": st, "dims": {DIMS[i]: hit[1][i] for i in range(len(DIMS))}}


def situation_from_snapshot(snap: Mapping[str, Any], *, source: str = "snapshot") -> dict[str, Any]:
    """Dışarıdan verilen situation_v1 anlık görüntüsü (ya da `snapshot` alanı olan satır) → boyutlar."""
    if isinstance(snap.get("snapshot"), Mapping):
        snap = snap["snapshot"]
    if snap.get("schema_id") != S.SCHEMA_ID:
        raise ReportError("anlık görüntü şeması %r değil: %r" % (S.SCHEMA_ID, snap.get("schema_id")))
    b = S.bucket(snap, "LONG")
    return {"mode": source, "symbol": snap.get("symbol"), "coin": coin_of(snap.get("symbol")),
            "as_of_ms": snap.get("as_of_ms"), "as_of": _iso_ms(snap.get("as_of_ms")) if isinstance(snap.get("as_of_ms"), int) else None,
            "snapshot_status": snap.get("status"), "schema_sha_match": snap.get("schema_sha") == S.SCHEMA_SHA,
            "dims": {d: b[d] for d in DIMS}}


def live_snapshot(symbol: str, *, csv_dir: Path | str, as_of_ms: int) -> dict[str, Any] | None:
    """AĞSIZ canlı anlık görüntü: Formasyon'un kapanmış-bar CSV önbelleğinden (salt okur; `CsvCandleCache.read`).
    Pencere yoksa None; eksikse durum GAP/PARTIAL olarak döner (çağıran gösterir)."""
    from .bars import BarSource                        # (2026-09-29) yalnız bu kipte: pandas + CSV okuyucu
    src = BarSource(frames=None, provenance=None, tour_now_ms=None, csv_dir=csv_dir)
    h4 = src.window(symbol, S.TF_H4, as_of_ms=as_of_ms, W=S.W4H)
    if h4.n == 0:
        return None
    h1 = src.window(symbol, S.TF_H1, as_of_ms=as_of_ms, W=S.W1H)
    btc = h4 if S.is_btc_symbol(symbol) else src.window(S.BTC_SYMBOL, S.TF_H4, as_of_ms=as_of_ms, W=S.WBTC)
    try:
        return S.snapshot(symbol, h4, h1, btc, as_of_ms)
    except ValueError:
        return None


def run(root: Path | str, q: Query, *, since: Any = None, until: Any = None, for_symbol: str | None = None,
        snapshot_doc: Mapping[str, Any] | None = None, live: bool = False, csv_dir: Path | str | None = None,
        now: datetime | None = None, with_summary: bool = False) -> dict[str, Any]:
    """CLI giriş noktası: tara → durumu çöz → belge (+ isteğe bağlı panel özeti). SALT OKUR."""
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    since_ms, until_ms = parse_when(since), parse_when(until)
    window = {"since": _iso_ms(since_ms), "until": _iso_ms(until_ms)}
    q = replace(q, dims=dict(q.dims)).validate()        # çağıranın sorgu nesnesi DEĞİŞMEZ (durum birleştirmesi kopyada)
    if live and not for_symbol:
        raise ReportError("--live yalnız --for-symbol ile kullanılır")
    if not root.is_dir():
        return {"schema": REPORT_SCHEMA, "banner": BANNER_TR, "banner_line": BANNER_LINE_TR, "available": False,
                "root": str(root), "generated_at": iso(now),
                "note_tr": "ortak deneyim deposu yok (katman kapalı ya da hiç çalışmadı) — VERİ YOK"}
    o = scan(root, since_ms=since_ms, until_ms=until_ms)
    sit: dict[str, Any] | None = None
    notes: list[str] = []
    if snapshot_doc is not None:
        sit = situation_from_snapshot(snapshot_doc)
    elif for_symbol and live:
        snap = live_snapshot(for_symbol, csv_dir=csv_dir, as_of_ms=int(now.timestamp() * 1000)) if csv_dir else None
        if snap is not None and snap.get("status") in ("OK", "PARTIAL"):
            sit = situation_from_snapshot(snap, source="live_csv")
        else:
            notes.append("canlı anlık görüntü kurulamadı (CSV önbelleği eksik/bayat: %s) — depodaki son anlık görüntü "
                         "kullanıldı" % ((snap or {}).get("status") or "YOK"))
    if sit is None and for_symbol:
        sit = situation_for_symbol(o, for_symbol, snapshot=q.snapshot)
        if sit is None:
            notes.append("%s için depoda uygun anlık görüntü yok — durum boyutsuz (tüm durumlar)" % for_symbol)
    if sit is not None:
        merged = dict(sit.get("dims") or {})
        merged.update(q.dims)                            # açık bayraklar sembolün durumunu EZER
        q.dims = normalize_dims(merged)
        sit = {**sit, "dims": dict(q.dims)}
        if for_symbol and not q.focus_coin:
            q.focus_coin = for_symbol
    doc = build(o, q, situation=sit, now=now, window=window)
    doc["available"] = True
    doc["root"] = str(root)
    if notes:
        doc["notes_tr"] = notes
    if with_summary:
        doc["summary"] = summary(o, now=now, min_n=q.min_n, shrink_k=q.shrink_k, window=window)
    return doc


# ============================================================================ metin (Türkçe)
def _num(x: Any, nd: int = 2, sign: bool = True) -> str:
    v = _f(x)
    if v is None:
        return "—"
    return ("%+.*f" if sign else "%.*f") % (nd, v)


def _ci(c: Any) -> str:
    return "[%s, %s]" % (_num(c[0]), _num(c[1])) if c else "[—]"


def _dims_tr(d: Mapping[str, str]) -> str:
    return " · ".join("%s=%s" % (DIM_TR[k], d[k]) for k in DIMS if k in d) or "tüm durumlar"


def _col_line(label: str, st: Mapping[str, Any], *, min_n: int = MIN_N_VERDICT, indent: str = "     ",
              width: int = 30) -> str:
    head = "%s%-*s n=%d (kesin %d, küme %d)" % (indent, width, label, st["n"], st["n_final"], st["n_clusters"])
    if st.get("tier") == TIER_INSUFFICIENT:
        return head + " · %s (n<%d: sayı gösterilmez)" % (V_THIN, MIN_N_DESCRIPTIVE)
    wr = st["win_rate_ci95"]
    body = (" · kazanç %%%.1f [%.1f, %.1f] · ort.net R %s iid %s küme %s · medyan %s %s · maliyet %sR · MFE %sR / MAE %sR"
            " · tutma %s sa" % (100 * st["win_rate"], 100 * wr[0], 100 * wr[1], _num(st["mean_r"]), _ci(st["mean_r_ci95"]),
                               _ci(st["mean_r_ci95_cluster"]), _num(st["median_r"]), _ci(st["median_r_ci95"]),
                               _num(st["mean_cost_r"], sign=False), _num(st["mean_mfe_r"]), _num(st["mean_mae_r"]),
                               _num(st["mean_hold_h"], 1, sign=False)))
    tail = " · %s" % st["verdict_tr"] + (" (tanımlayıcı, n<%d)" % min_n if st.get("tier") == TIER_DESCRIPTIVE else "")
    return head + body + tail


def _coin_line(label: str, st: Mapping[str, Any], k: float) -> str | None:
    c = st.get("coin")
    if not c or st.get("mean_r") is None:
        return None
    return ("%s %s: coin n=%d ort %s → büzülmüş %s (havuz %s, k=%g)"
            % (label, c.get("coin"), c["n"], _num(c.get("mean_r")), _num(c["shrunk_mean_r"]), _num(st["mean_r"]), k))


def _render_level(lines: list[str], lv: Mapping[str, Any], *, kind: str, k: float, min_n: int) -> None:
    tag = "L%d" % lv["level"]
    drop = (" − " + ", ".join(DIM_TR[d] for d in lv["dropped"])) if lv["dropped"] else ""
    lines.append("  %s%s: %s" % (tag, drop, _dims_tr(lv["dims"])))
    if kind in ("real", "both"):
        lines.append(_col_line("Gerçek (hepsi)", lv["real"], min_n=min_n))
        for c in COHORT_ORDER:                          # boş kohort satırı basılmaz (JSON'da hepsi var)
            if lv["real_by_cohort"][c]["n"]:
                lines.append(_col_line("  " + COHORT_TR[c], lv["real_by_cohort"][c], min_n=min_n))
    if kind in ("cf", "both"):
        cf = lv["cf"]
        lines.append(_col_line("Olsaydı (karşı-olgusal, net)", cf, min_n=min_n) + ("  [A15 bayraklı %d]" % cf.get("n_a15", 0)))
        if cf.get("n_a15"):
            lines.append(_col_line("  A15 hariç", lv["cf_excl_a15"], min_n=min_n))
        g = lv["cf_gross_legacy"]
        if g["n"]:
            lines.append("     %-30s n=%d · ort. brüt R %s (%s)" % ("Olsaydı — brüt, eski etiket", g["n"],
                                                                     _num(g["mean_r_gross"]), g["note_tr"]))
    for label, st in (("Gerçek", lv["real"]), ("Olsaydı", lv["cf"])):
        cl = _coin_line("     kısmi havuz · " + label, st, k)
        if cl:
            lines.append(cl)
    cv = lv["coverage"]
    parts = ["%s %d" % (tr, cv[k]) for k, tr in _COVERAGE_TR if cv.get(k)]
    lines.append("     Kapsam: " + (" · ".join(parts) if parts else "—"))


_COVERAGE_TR = (("real_open", "açık"), ("real_no_net", "net yok"), ("real_entry_missing", "girişi eksik"),
                ("cf_pending", "bekleyen"), ("cf_superseded", "değiştirildi"), ("cf_expired", "süresi doldu"),
                ("cf_dropped", "düşürüldü"), ("cf_vanished", "kayboldu"), ("cf_unknown_label", "bilinmeyen etiket"),
                ("cf_net_unavailable", "net üretilemedi"))


def _level_sig(lv: Mapping[str, Any]) -> tuple:
    return (lv["real"]["n"], lv["cf"]["n"], lv["cf_excl_a15"]["n"], lv["cf_gross_legacy"]["n"],
            tuple(sorted(lv["coverage"].items())), tuple(lv["real_by_cohort"][c]["n"] for c in COHORT_ORDER))


def _ans_tr(a: Mapping[str, Any] | None, min_n: int) -> str:
    if not a:
        return "hiçbir seviye n≥%d değil" % min_n
    return "L%d (n=%d, ort %s, %s)" % (a["level"], a["n"], _num(a.get("mean_r")), a["verdict_tr"])


def render_tr(doc: Mapping[str, Any]) -> str:
    """Belge → Türkçe metin (şerit HER ZAMAN ilk satır)."""
    lines = [BANNER_LINE_TR]
    if not doc.get("available", True):
        lines.append(str(doc.get("note_tr") or "VERİ YOK"))
        return "\n".join(lines)
    p, src = doc["params"], doc["source"]
    win = p.get("window") or {}
    lines.append("Kaynak: %d satır · %d anahtar · %d gözlem · arşiv %d/%d segment okundu (%d atlandı, %d bozuk) · "
                 "pencere: %s → %s · anlık görüntü: %s · köken: %s · min_n=%d · k=%g"
                 % (src["rows_read"], src.get("keys", 0), src["observations"], src["segments_read"], src["segments_total"],
                    src["segments_skipped"], src["segments_bad"], win.get("since") or "başlangıç", win.get("until") or "şimdi",
                    p["snapshot"], p["origin"], p["min_n"], p["shrink_k"]))
    sit = doc.get("situation")
    if sit:
        extra = ""
        if sit.get("mode") in ("symbol", "live_csv", "snapshot"):
            extra = "   [%s %s @ %s, %s]" % ({"symbol": "depodaki son anlık görüntü", "live_csv": "canlı (CSV önbelleği)",
                                              "snapshot": "verilen anlık görüntü"}[sit["mode"]], sit.get("symbol") or "?",
                                             sit.get("as_of") or "?", sit.get("snapshot_status") or "?")
        lines.append("DURUM  %s%s" % (_dims_tr(sit.get("dims") or {}), extra))
    for n in doc.get("notes_tr") or []:
        lines.append("NOT: " + n)
    cov = doc["coverage"]
    lines.append("Toplam: gerçek kapanmış(net) %d · açık %d · olsaydı net %d (A15 %d) · brüt eski %d · bekleyen %d · "
                 "değiştirildi %d · süresi doldu %d · düşürüldü %d"
                 % (cov["real_closed_net"], cov["real_open"], cov["cf_net"], cov["cf_net_a15"], cov["cf_gross_legacy"],
                    cov["cf_pending"], cov["cf_superseded"], cov["cf_expired"], cov["cf_dropped"]))
    k, min_n, kind = p["shrink_k"], p["min_n"], p["kind"]
    if "cells" in doc:
        lines.append("HÜCRE HARİTASI (tam hücre, n ≥ %d; cevap = geri çekilmede n ≥ %d olan ilk seviye)" % (p["cells_min"], min_n))
        cur = None
        for c in doc["cells"]:
            if c["group"] != cur:
                cur = c["group"]
                lines.append("")
                lines.append("■ %s %s" % ("KURULUM" if c["group_by"] == "setup" else "AİLE", cur))
            r, f = c["real"], c["cf"]
            lines.append("  %s | gerçek n=%d %s %s | olsaydı n=%d %s %s | cevap: gerçek %s · olsaydı %s"
                         % (_dims_tr(c["dims"]), r["n"], _num(r["mean_r"]), r["verdict_tr"], f["n"], _num(f["mean_r"]),
                            f["verdict_tr"], _ans_tr(c["answer"]["real"], min_n), _ans_tr(c["answer"]["cf"], min_n)))
        if not doc["cells"]:
            lines.append("  (n ≥ %d olan hücre yok — VERİ YETERSİZ)" % p["cells_min"])
        return "\n".join(lines)
    if not doc.get("groups"):
        lines.append("")
        lines.append("Süzgece uyan gözlem yok — VERİ YOK.")
    for g in doc.get("groups") or []:
        lines.append("")
        if g["group_by"] == "setup":
            lines.append("■ KURULUM %s — %s" % (g["group"], g.get("book_name") or g.get("book")))
        else:
            lines.append("■ AİLE %s (defterler arası havuz; zıt mantıklar ayrı)" % g["group"])
        a = g["answer"]
        # metin kısaltması: cevap seviyelerinden SONRAKİ seviyeler yalnız JSON'da (hepsi raporlanır; SPEC §11.3)
        cols = [a[c] for c in (("real", "cf") if kind == "both" else (kind,))]
        last = len(g["levels"]) - 1 if any(x is None for x in cols) else max(x["level"] for x in cols)
        prev_sig, same = None, []
        for lv in g["levels"][:last + 1]:
            sig = _level_sig(lv)
            if sig == prev_sig:                          # boyut düşürmek gözlem eklemedi → tekrar basılmaz
                same.append(lv["level"])
                continue
            if same:
                lines.append("  L%d–L%d: önceki seviyeyle AYNI gözlemler (boyut düşürmek örneklem eklemedi)" % (same[0], same[-1]))
                same = []
            _render_level(lines, lv, kind=kind, k=k, min_n=min_n)
            prev_sig = sig
        if same:
            lines.append("  L%d–L%d: önceki seviyeyle AYNI gözlemler (boyut düşürmek örneklem eklemedi)" % (same[0], same[-1]))
        if last + 1 < len(g["levels"]):
            lines.append("  (L%d–L%d: cevaptan sonraki geri çekilme seviyeleri --json çıktısında)" % (last + 1, len(g["levels"]) - 1))
        ans = [("gerçek", a["real"]), ("olsaydı", a["cf"])]
        ans = [x for x in ans if kind == "both" or (kind == "real") == (x[0] == "gerçek")]
        lines.append("  CEVAP: " + " · ".join("%s → %s" % (lbl, _ans_tr(v, min_n)) for lbl, v in ans))
    return "\n".join(lines)


# ============================================================================ durum (shared-experience-status)
def status_doc(root: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """`status.json` + imleç özeti + arşiv manifesti + defter başına son 24 sa / 7 gün kapsamı (yazım anına göre) +
    VERİ YOK bayrakları + geri alma tetikleyicileri. SALT OKUR; son 7 günden önce mühürlenmiş segmentler açılmaz."""
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    now_ms = int(now.timestamp() * 1000)
    doc: dict[str, Any] = {"schema": STATUS_REPORT_SCHEMA, "banner": BANNER_TR, "root": str(root),
                           "generated_at": iso(now), "available": root.is_dir()}
    if not root.is_dir():
        doc["note_tr"] = "ortak deneyim deposu yok (katman kapalı ya da hiç çalışmadı)"
        return doc
    st = _read_json_ro(root / STATUS_FILE)
    st = st if isinstance(st, dict) else None
    doc["status"] = st
    if st:
        age = _ms(st.get("last_step_at"))
        doc["status_age_s"] = round((now_ms - age) / 1000.0, 1) if age is not None else None
        p95 = _f(st.get("step_ms_p95"))
        doc["rollback"] = {"breaker": st.get("state") == "DISABLED_BY_BREAKER", "p95_over_5000": bool(p95 and p95 > 5000),
                           "advice_tr": "tetik varsa: shared_experience.mode OFF + worker restart (öğrenme modu GERİ ALINMAZ)"}
    cur = _read_json_ro(root / CURSOR_FILE)
    if isinstance(cur, dict):
        books = cur.get("books") if isinstance(cur.get("books"), dict) else {}
        doc["cursor"] = {"schema": cur.get("schema"), "born_ms": cur.get("born_ms"),
                         "born_at": _iso_ms(cur["born_ms"]) if isinstance(cur.get("born_ms"), int) else None,
                         "backfill": cur.get("backfill"), "created_at": cur.get("created_at"),
                         "cf_labelled": cur.get("cf_labelled"),
                         "books": {k: {"backfill_done": bool((v.get("backfill") or {}).get("done")),
                                       "entries": len(v.get("entries") or {}), "unfinal": len(v.get("unfinal") or {}),
                                       "cf_known": len(v.get("cf") or {})}
                                   for k, v in sorted(books.items()) if isinstance(v, dict)}}
    else:
        doc["cursor"] = None
    man = manifest(root)
    tot = man.get("totals") if isinstance(man.get("totals"), dict) else {}
    doc["archive"] = {"health": man.get("health"), "segments": len(man.get("segments") or []),
                      "records": tot.get("records"), "bytes_compressed": tot.get("bytes_compressed"),
                      "last_rotation_at": man.get("last_rotation_at"), "pending_trim": bool(man.get("pending_trim"))}
    sm = _read_json_ro(root / SUMMARY_FILE, max_bytes=4 << 20)
    doc["last_sweep_at"] = sm.get("generated_at") if isinstance(sm, dict) else None
    # kapsam: yazım anına (recorded_at) göre son 24 sa / 7 gün, defter başına (row_id tekil)
    cut7, cut1 = now_ms - 7 * _DAY_MS, now_ms - _DAY_MS
    zero = {"rows": 0, "entries": 0, "outcomes": 0, "cf_new": 0, "cf_labelled": 0}
    cov = {w: {b: dict(zero) for b in R.BOOKS} for w in ("24h", "7d")}
    rs = ReadStats()
    seen = set()
    for row in iter_rows(root, sealed_after_ms=cut7, stats=rs):
        if row.get("schema") != ROW_SCHEMA or row.get("book") not in R.BOOKS:
            continue
        rid = str(row.get("row_id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        rec = _ms(row.get("recorded_at"))
        if rec is None or rec < cut7:
            continue
        kind = row.get("kind")
        for w, cut in (("7d", cut7), ("24h", cut1)):
            if rec < cut:
                continue
            c = cov[w][row["book"]]
            c["rows"] += 1
            if kind == R.KIND_ENTRY:
                c["entries"] += 1
            elif kind == R.KIND_OUTCOME:
                c["outcomes"] += 1
            elif kind == R.KIND_CF:
                if row.get("rev") == 0:
                    c["cf_new"] += 1
                if row.get("status") == R.LABELLED:
                    c["cf_labelled"] += 1
    doc["hot"] = {"present": rs.hot_present, "bytes": rs.hot_bytes}
    doc["coverage"] = cov
    doc["coverage_read"] = rs.as_dict()
    doc["no_data_24h"] = [b for b in R.BOOKS if cov["24h"][b]["rows"] == 0]
    return doc


def render_status_tr(doc: Mapping[str, Any]) -> str:
    lines = ["ORTAK DENEYİM DURUMU — " + BANNER_TR + " (yalnız KAYIT; karar değişmez)"]
    if not doc.get("available"):
        lines.append(str(doc.get("note_tr") or "VERİ YOK"))
        return "\n".join(lines)
    st = doc.get("status") or {}
    if st:
        c = st.get("counters") or {}
        store = st.get("store") or {}
        lines.append("Durum %s · son adım %s (%s sn önce) · adım %s · p50 %s ms · p95 %s ms · taslak %s · son hata %s"
                     % (st.get("state"), st.get("last_step_at"), doc.get("status_age_s"), st.get("steps"),
                        st.get("step_ms_p50"), st.get("step_ms_p95"), st.get("drafts"), st.get("last_error_code") or "—"))
        lines.append("Satır: toplam %s · tür %s · anlık görüntü %s · kilit atlama %s · kaybolan %s · brüt eski %s · hata %s"
                     % (c.get("rows_total"), json.dumps(c.get("rows_by_kind") or {}, ensure_ascii=False),
                        json.dumps(c.get("snapshot_status_mix") or {}, ensure_ascii=False), c.get("lock_busy_skips"),
                        json.dumps(c.get("vanished_by_status") or {}, ensure_ascii=False), c.get("legacy_gross_cf"),
                        c.get("errors_total")))
        br = st.get("breaker") or {}
        lines.append("Devre kesici: %s (art arda hata %s/%s, aşım %s/%s) · depo %s · sıcak %s satır · disk %.1f MB"
                     % ("AÇILDI" if br.get("tripped") else "kapalı", br.get("consecutive_errors"), br.get("errors_limit"),
                        br.get("consecutive_overruns"), br.get("overruns_limit"), store.get("state"),
                        store.get("hot_lines"), (float(store.get("disk_bytes") or 0) / 1048576.0)))
        rb = doc.get("rollback") or {}
        if rb.get("breaker") or rb.get("p95_over_5000"):
            lines.append("⚠ GERİ ALMA TETİĞİ: " + str(rb.get("advice_tr")))
    else:
        lines.append("status.json yok ya da okunamadı")
    cur = doc.get("cursor") or {}
    if cur:
        lines.append("İmleç: doğum %s · geri doldurma %s" % (cur.get("born_at"), cur.get("backfill")))
    a = doc.get("archive") or {}
    lines.append("Arşiv: %s segment · %s kayıt · sağlık %s · son döngü %s · son rapor taraması %s"
                 % (a.get("segments") or 0, a.get("records") or 0, a.get("health") or "yok",
                    a.get("last_rotation_at") or "yok", doc.get("last_sweep_at") or "yok"))
    lines.append("Kapsam (yazım anı)          24 sa: satır/giriş/kapanış/olsaydı yeni/olsaydı etiket | 7 gün: aynı")
    for b in R.BOOKS:
        c1, c7 = doc["coverage"]["24h"][b], doc["coverage"]["7d"][b]
        flag = "  VERİ YOK" if b in doc.get("no_data_24h", []) else ""
        lines.append("  %-32s %5d/%4d/%4d/%5d/%5d | %6d/%5d/%5d/%6d/%6d%s"
                     % (b, c1["rows"], c1["entries"], c1["outcomes"], c1["cf_new"], c1["cf_labelled"], c7["rows"],
                        c7["entries"], c7["outcomes"], c7["cf_new"], c7["cf_labelled"], flag))
    return "\n".join(lines)


__all__ = [
    "BACKOFF_ORDER", "BANNER_LINE_TR", "BANNER_TR", "BOOT_ITERS", "BOOT_MAX_N", "COHORT_FILTERS", "CURSOR_FILE", "DIMS",
    "DIM_VALUES", "HOT_FILE", "INSUFFICIENT_SAMPLE", "LOSS", "MIN_CLUSTERS", "MIN_N_DESCRIPTIVE", "MIN_N_VERDICT",
    "Observations", "Query", "REPORT_SCHEMA", "ReadStats", "ReportError", "SEED_CLUSTER", "SEED_IID", "SHRINK_K",
    "SNAPSHOT_FILTERS", "STATUS_FILE", "STATUS_REPORT_SCHEMA", "SUMMARY_FILE", "SUMMARY_SCHEMA", "TIER_DESCRIPTIVE",
    "TIER_INSUFFICIENT", "TIER_VERDICT", "UNDECIDED", "VERDICT_TR", "V_LOSS", "V_OPEN", "V_THIN", "V_WIN", "WIN",
    "backoff_levels", "build", "coin_of", "column_stats", "filter_mask", "iter_rows", "live_snapshot", "manifest",
    "normalize_dims", "parse_situation", "parse_when", "render_status_tr", "render_tr", "run", "scan", "shrunk_mean",
    "situation_for_symbol", "situation_from_snapshot", "status_doc", "summary", "verdict", "wilson",
]

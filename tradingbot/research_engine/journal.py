"""P2 — işlem günlüğü `tj_v1` (§4; P2 teslimatı) — vadeli + spot, P1a kapanış arşivinden.

Kaynaklar (hepsi SALT-OKUNUR; §4.1): kapanış arşivi `closes/<defter>/YYYY-MM.jsonl.gz` (her kaydın birebir kopyası, son
`rev`) ve `closes_derived` satırları; ledger JSON'u (yalnız yürütme modeli ayarları: ücret, kayma, TP1, MFE başa-baş);
`state/entry_provenance.jsonl` (ana bot: karar kimliği, kod/config özeti, giriş stop/hedefleri, ajan skorları);
defterlerin `trade_memory.jsonl` giriş satırları (strateji defterlerinde ölçülmüş `signal_close`/`atr14`/`ema200`);
ortak deneyim `xp_entry` satırları (`state/shared_experience/experience.jsonl` + arşiv segmentleri: girişteki hedefler
ve `situation_v1` anlık görüntüsü); gecelik anlık görüntüler (giriş anı özsermayesi); araştırma deposu (mühürlü; yol,
bağlam, rehydrate). SQLite okunmaz.

Satır kimliği `trade_key = defter|trade_id|opened_at` (§4.2); ana bot iki alt defterdir (`main_fut`, `main_spot`); spot
kısmi satışları kendi anahtarıyla ayrı satırdır, `position_group` ile gruplanır. Günlük ARŞİVİN türetilmiş görünümüdür:
`journal/tj_v1/YYYY-MM.jsonl.gz` (ay = kaydın `closed_at` UTC ayı; satırlar `(closed_at, trade_key)` sırasında),
kaydın içeriği (geç fonlama → `rev+1`), yan kaynaklar ya da okunan depo parçaları değişince satır yeniden kurulur. Her
çıktı deterministiktir (gzip `mtime=0`, sabit anahtar sırası): **aynı mühür ve aynı girdiler bayt-özdeş çıktı verir**.
`RESTORED_AWAY` kayıtlar günlüğe girmez (ayrı sayılır).

Her alanın yanında `field_source[alan]` ∈ {MEASURED, RECONSTRUCTED, MODELED, MISSING, NOT_APPLICABLE} tutulur (§4.3);
`missing_fields[]` MISSING olanların listesidir. Ölçülmüş alanlardan saf aritmetikle türetilen değer (ör. `stop_dist_pct`,
`net_r`) MEASURED sayılır; depo/kural/model girdisi olan RECONSTRUCTED/MODELED'dır.

**Para ve R (kabul 1, 3, 4):** para alanları Decimal'ın kayıpsız dizgesidir (ledger'daki gibi). R paydası her yerde
`features.risk_usdt`'tir: `net_r = net_pnl / risk_usdt` (Decimal; ledger `r_multiple`'ı ile aynı bölme → eşitlik 1e-9,
geç fonlamadan sonra da, çünkü `settle_late_funding` R'yi aynı payda ile yeniden yazar). Payda yoksa (eski kayıt) R
alanları `MISSING`'dir, tahminle doldurulmaz. Ücret özdeşliği `fees == entry_fee + exit_fee` her satırda denetlenir
(`fee_identity_ok`); ücret yükü `cost_r.fee = fees / risk_usdt`'tir — `learn/labels.label_outcome` `entry_fee` varken
`fees + entry_fee + exit_fee` toplayıp ücreti İKİ KEZ sayar (labels.py:45); bu modül onu kullanmaz (regresyon testi).
Kayma (`slippage_cost`, MODELED) dolum fiyatlarının İÇİNDEDİR: `gross_pnl` onu zaten taşır; toplam maliyet
`cost_r.total = fee + funding` ve `gross_r − cost_r.total = net_r` (Decimal; bağlamın 28 hanesinde).

Spot (§4.1): `features` yoktur; stop ve risk varsa ana botun provenance'ından (alış emri kimliği = FIFO lotunun dolum
kimliğinin emir kısmı; MEASURED, kaynak dosyasıyla), yoksa MISSING. Kaldıraç 1 (MEASURED); fonlama, marj ve likidasyon
NOT_APPLICABLE. Spot satırları USDT toplamlarına her durumda tam girer.

Hedefler ve sinyal bağlamı (§4.3 son madde): 1) `xp_entry` / provenance (MEASURED); 2) kural bağlamının yeniden
kurulması (`rehydrate`, RECONSTRUCTED); 3) MISSING. Bağlam alanları (giriş yeri, `situation_*`, BTC, OI, taker) depo
barlarından GİRİŞTEN ÖNCE kapanmış barlarla hesaplanır (`as_of = opened_at − 1 ms`; ileriye bakma yok, kabul 9);
çıkış bağlamı `as_of = closed_at`.

Bu modül ağ kullanmaz; yalnız `data/research/journal` ve `data/research/paths` altına yazar.
"""
from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import fidelity as FD
from . import pathrec as PR
from . import rehydrate as RH
from .closes import CLOSES_SCHEMA, ST_ACTIVE, ST_RESTORED_AWAY, latest_closes, load_snapshots, segment_files
from .ledgers import (
    BOOK_MAIN_FUT,
    BOOK_MAIN_SPOT,
    KIND_FUTURES,
    KIND_SPOT,
    book_name,
    dec,
    dec_or_none,
    dstr,
    find_ledgers,
    is_gold,
    is_mirror,
    iso,
    parse_ts,
    read_ledger,
    symbol_key,
    utc_now,
)
from .paths import EnginePaths, gzip_bytes, json_line, sha256_file
from .store import ResearchStore

UTC = timezone.utc
#: ay dosyasına akışla birleştirmeden önce bellekte tutulan en çok yeni satır (bellek bütçesi, §2.5)
FLUSH_ROWS = 1000
#: arşivden aynı anda okunan kayıt sayısı (`load_records`; büyük parça = segmentin daha az taranması)
LOAD_CHUNK = 1000
#: gece S1b'nin depo parça önbelleği (1m ay parçası ~2–3 MB; varsayılan 96 bellekte ~250 MB tutabilirdi); 2026-10-08
#: (inceleme M6) 32 → 16
S1B_MAX_PARTS = 16
JOURNAL_SCHEMA = "tj_v1"
JOURNAL_VERSION = "tj_v1/p2c.1"   # 2026-10-08 inceleme düzeltmeleri (B1, M2, M5, küçükler)
INDEX_SCHEMA = "tj_v1_index"
BUILD_SCHEMA = "tj_v1_build"
SOURCE_LIVE = "LIVE_PAPER"
MEASURED, RECONSTRUCTED, MODELED, MISSING, NOT_APPLICABLE = "MEASURED", "RECONSTRUCTED", "MODELED", "MISSING", "NOT_APPLICABLE"
LABELS = (MEASURED, RECONSTRUCTED, MODELED, MISSING, NOT_APPLICABLE)
TOL = Decimal("1e-6")
R_TOL = Decimal("1e-9")

#: §4.3 alan grupları (sıra = satırdaki sıra)
FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "kimlik": ("schema", "trade_key", "rev", "source", "book", "book_name", "trade_id", "decision_id", "code_sha",
               "config_hash", "config_epoch"),
    "enstruman": ("symbol", "instrument_class", "venue", "side"),
    "taktik": ("tactic", "family", "variation_id", "params_hash", "cohort", "record_only", "learning_unlocked_by"),
    "zaman": ("signal_ts", "opened_at", "closed_at", "closed_at_backdated", "hold_hours", "bars_held", "decision_delay_s"),
    "giris": ("ref_price", "entry_fill", "entry_slip_bps", "range_pos_20", "dist_20d_high_atr", "dist_20d_low_atr",
              "dist_ema200_atr", "dist_level_atr", "session", "utc_hour", "weekday", "funding_rate_at_entry",
              "min_to_next_funding", "situation_entry"),
    "buyukluk": ("qty", "notional", "margin", "leverage", "liquidation_price", "liq_distance_in_stops", "risk_usdt",
                 "risk_pct_of_equity", "equity_at_entry", "size_rule"),
    "plan": ("initial_stop", "stop_dist_pct", "stop_dist_atr", "targets", "tp1_fraction", "breakeven_at_mfe_r", "max_hold",
             "planned_rr_after_cost"),
    "cikis": ("exit_price", "exit_reason", "exit_basis", "exit_overshoot_r", "tp1_done", "fills"),
    "maliyet": ("gross_pnl", "entry_fee", "exit_fee", "slippage_cost", "spread_cost", "spread_est", "funding_paid",
                "funding_received", "funding_net", "funding_complete", "net_pnl", "net_r", "gross_r", "cost_r",
                "pnl_pct_book_equity", "pnl_pct_total_equity"),
    "yol": ("mfe_r", "mae_r", "t_mfe", "t_mae", "order", "time_to_1r", "giveback_r", "capture_ratio", "path_source",
            "ambiguous_bars"),
    "baglam": ("situation_exit", "btc_ctx_entry", "btc_ctx_exit", "oi_change_24h", "taker_ratio", "agents_ctx", "signal_ctx",
               "gold_ctx"),
    "atif": ("fidelity",),
    "koken": ("sources", "data_seal", "missing_fields", "journal_version"),
}
ALL_FIELDS: tuple[str, ...] = tuple(f for g in FIELD_GROUPS.values() for f in g)
#: Sahibin listesi (§4.3 tablosunda kalın): bu alanlar ya doludur ya MISSING/MODELED/NOT_APPLICABLE etiketlidir (kabul 2).
OWNER_FIELDS: tuple[str, ...] = ("symbol", "tactic", "variation_id", "entry_fill", "qty", "notional", "leverage", "gross_pnl",
                                 "entry_fee", "exit_fee", "slippage_cost", "spread_cost", "funding_paid", "funding_received",
                                 "net_pnl")
#: Sahibin kalın GRUPLARI (giriş yeri, büyüklük ve kaldıraç, gelir ve maliyet, taktik ve varyasyon)
OWNER_GROUPS: tuple[str, ...] = ("taktik", "giris", "buyukluk", "maliyet")

#: defter → (taktik, aile) — §4.3 "Box/D4/C4 için düzeltilmiş"; ana bot ve Formasyon kaydın `setup_type`'ı
BOOK_TACTIC: dict[str, tuple[str | None, str]] = {
    "strategy_paper": ("t2_trend_regime", "TREND"), "strategy_paper_m2": ("m2_tsmom28", "MOMENTUM"),
    "strategy_paper_box": ("b1_box_fade", "FADE"), "strategy_paper_trend4h": ("d4_donchian_20_10", "BREAKOUT"),
    "strategy_paper_candle4h": ("c4_candle_variations", "CANDLE_PATTERN"),
    "strategy_paper_candle4h_strict": ("c4s_candle_variations_strict", "CANDLE_PATTERN"),
    "strategy_paper_m2x": ("m2x_mirror_of_m2", "MOMENTUM"), "pattern_trader": (None, "CANDLE_PATTERN"),
    BOOK_MAIN_FUT: (None, "MAIN_ENSEMBLE"), BOOK_MAIN_SPOT: (None, "MAIN_ENSEMBLE"),
}
_TARGET_EXITS = ("hedef1", "hedef2")
_STOP_EXITS = ("stop", "başa-baş stop")
DAY_MS, H4_MS, H1_MS = 86_400_000, 14_400_000, 3_600_000
XP_DIR = "shared_experience"
#: `scan_archive`'in tuttuğu izdüşüm alanları (sıra, ay, özet; bütün izdüşüm 45 bin kayıtta ~100 MB tutardı)
LOC_PROJ_FIELDS = ("id", "symbol", "opened_at", "closed_at")
#: ana botun giriş kararından (provenance `entry_features`, karar anı vektörü) günlüğe taşınan alanlar (P2b atıf kodları
#: `DISSENT_WAS_RIGHT` / `TOO_MANY_WARNINGS` / `LOW_RR` kapanıştaki `last_decisions`'ı DEĞİL bunu kullanır, §5.4)
ENTRY_DECISION_FEATURES = ("n_dissent", "n_vetoes", "rr", "consensus_score", "risk_allowed")
XP_BOOK_TO_ENGINE = {"main": BOOK_MAIN_FUT}


# ============================================================================ küçük yardımcılar
def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def _r6(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return round(v, 6) + 0.0


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt is not None else None


def iter_gz_lines(path: Path) -> Iterator[tuple[int, dict]]:
    """`.jsonl.gz` satırları (1 tabanlı satır numarasıyla; boş satırlar sayılır, atlanır)."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if line:
                yield n, json.loads(line)


def session_of(t: datetime) -> str:
    """Seans (§4.3 `session`; sınırlar belgede açık bırakılmış, P2 notu): hafta sonu (Cmt/Paz UTC) WEEKEND; diğer günler
    ASIA [21:00, 07:00), LONDON [07:00, 13:00), NY [13:00, 21:00) UTC."""
    if t.weekday() >= 5:
        return "WEEKEND"
    h = t.hour
    if 7 <= h < 13:
        return "LONDON"
    if 13 <= h < 21:
        return "NY"
    return "ASIA"


class _Row:
    """Alan + etiket biriktirici (her alanın tam bir etiketi olur)."""

    def __init__(self) -> None:
        self.v: dict[str, Any] = {}
        self.src: dict[str, str] = {}
        self.notes: dict[str, str] = {}

    def set(self, name: str, value: Any, label: str, note: str | None = None) -> None:
        if label not in LABELS:
            raise ValueError(f"bilinmeyen etiket {label!r} ({name})")
        if label in (MISSING, NOT_APPLICABLE) and value is not None and not isinstance(value, (list, dict)):
            value = None
        self.v[name] = value
        self.src[name] = label
        if note:
            self.notes[name] = note

    def miss(self, name: str, note: str | None = None) -> None:
        self.set(name, None, MISSING, note)

    def na(self, name: str, note: str | None = None) -> None:
        self.set(name, None, NOT_APPLICABLE, note)


# ============================================================================ arşiv taraması
@dataclass
class ArchiveLoc:
    book: str
    key: str
    rev: int
    status: str
    sha: str
    seg: str
    line: int
    proj: dict
    position_group: str | None
    derived: dict | None = None


def scan_archive(paths: EnginePaths, book: str) -> dict[str, ArchiveLoc]:
    """Defterin arşivinden anahtar başına SON kapanış satırının yeri ve küçük izdüşümü (yalnız `LOC_PROJ_FIELDS`; bellek)
    (+ `closes_derived` satırı)."""
    out: dict[str, ArchiveLoc] = {}
    derived: dict[str, dict] = {}
    for seg in segment_files(paths.book_closes_dir(book)):
        for n, row in iter_gz_lines(seg):
            k = str(row.get("trade_key"))
            if row.get("row") == "derived":
                derived.setdefault(k, {kk: row.get(kk) for kk in ("min_to_next_funding", "min_to_next_funding_source",
                                                                  "decision_delay_s", "decision_delay_source")})
                continue
            if row.get("schema") != CLOSES_SCHEMA:
                continue
            prev = out.get(k)
            rp = row.get("proj")
            proj = {f: rp.get(f) for f in LOC_PROJ_FIELDS} if isinstance(rp, dict) and rp else (prev.proj if prev else {})
            out[k] = ArchiveLoc(book, k, int(row.get("rev", 0)), str(row.get("status", ST_ACTIVE)), str(row.get("content_sha", "")),
                                seg.name, n, proj, row.get("position_group") or (prev.position_group if prev else None))
    for k, d in derived.items():
        if k in out:
            out[k].derived = d
    return out


def load_records(paths: EnginePaths, book: str, locs: dict[str, ArchiveLoc]) -> dict[str, dict]:
    """Verilen yerlerdeki BİREBİR kayıtlar (`record`); yalnız istenen satırlar tutulur."""
    want: dict[str, dict[int, str]] = {}
    for k, loc in locs.items():
        want.setdefault(loc.seg, {})[loc.line] = k
    out: dict[str, dict] = {}
    d = paths.book_closes_dir(book)
    for seg_name, lines in want.items():
        last = max(lines)
        with gzip.open(d / seg_name, "rt", encoding="utf-8") as fh:      # yalnız istenen satırlar ayrıştırılır
            for n, line in enumerate(fh, 1):
                k = lines.get(n)
                if k is not None and line.strip():
                    row = json.loads(line)
                    if isinstance(row.get("record"), dict):
                        out[k] = row["record"]
                if n >= last:
                    break
    return out


# ============================================================================ yan kaynaklar (salt-okunur)
def _jsonl_rows(path: Path, needle_any: set[str] | None = None) -> Iterator[tuple[int, dict]]:
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if needle_any is not None and not any(x in line for x in needle_any):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield n, row
    except OSError:
        return


class SideSources:
    """Provenance, işlem hafızası ve `xp_entry` satırları — yalnız gereken kimlikler için, satır numarasıyla."""

    def __init__(self, state: Path, *, ids_by_book: dict[str, set[str]], keys: set[str]) -> None:
        self.state = Path(state)
        self.prov: dict[str, tuple[dict, str, int]] = {}
        self.mem: dict[tuple[str, str], tuple[dict, str, int]] = {}
        self.xp: dict[str, tuple[dict, str, int]] = {}
        main_ids = set(ids_by_book.get(BOOK_MAIN_FUT, set())) | set(ids_by_book.get(BOOK_MAIN_SPOT, set()))
        if main_ids:
            p = self.state / "entry_provenance.jsonl"
            for n, row in _jsonl_rows(p, main_ids):
                tid = str(row.get("trade_id") or "")
                if tid in main_ids and tid not in self.prov:                     # ilk kayıt otoritedir
                    self.prov[tid] = (row, "state/entry_provenance.jsonl", n)
        for book, ids in sorted(ids_by_book.items()):
            if not ids or book == BOOK_MAIN_SPOT:
                continue
            sub = "" if book == BOOK_MAIN_FUT else book
            p = (self.state / sub / "trade_memory.jsonl") if sub else (self.state / "trade_memory.jsonl")
            rel = f"state/{sub + '/' if sub else ''}trade_memory.jsonl"
            for n, row in _jsonl_rows(p, ids):
                tid = str(row.get("trade_id") or "")
                if row.get("kind") == "entry" and tid in ids and (book, tid) not in self.mem:
                    self.mem[(book, tid)] = (row, rel, n)
        if keys:
            xp_root = self.state / XP_DIR
            files = [(xp_root / "experience.jsonl", f"state/{XP_DIR}/experience.jsonl")]
            seg_dir = xp_root / "archive" / "segments"
            if seg_dir.is_dir():
                files = [(s, f"state/{XP_DIR}/archive/segments/{s.name}") for s in sorted(seg_dir.glob("seg-*.jsonl.gz"))] + files
            for p, rel in files:
                it = iter_gz_lines(p) if p.suffix == ".gz" else _jsonl_rows(p, {"xp_entry"})
                try:
                    for n, row in it:
                        if row.get("kind") != "xp_entry":
                            continue
                        tk = str(row.get("trade_key") or "")
                        b, _, rest = tk.partition("|")
                        tk = f"{XP_BOOK_TO_ENGINE.get(b, b)}|{rest}"
                        if tk in keys and tk not in self.xp:
                            self.xp[tk] = (row, rel, n)
                except (OSError, ValueError, EOFError):
                    continue

    def provenance(self, tid: str) -> tuple[dict, str, int] | None:
        return self.prov.get(tid)

    def memory(self, book: str, tid: str) -> tuple[dict, str, int] | None:
        return self.mem.get((book, tid))

    def xp_entry(self, key: str) -> tuple[dict, str, int] | None:
        return self.xp.get(key)


def spot_buy_order(rec: dict) -> str | None:
    """Spot satış kaydının alış emri kimliği: ilk FIFO lotunun dolum kimliğinin emir kısmı (`S000123-1` → `S000123`)."""
    lots = (rec.get("costs") or {}).get("fifo_lots") if isinstance(rec.get("costs"), dict) else None
    for lot in lots or []:
        fid = str((lot or {}).get("fill_id") or "")
        if "-" in fid:
            return fid.rsplit("-", 1)[0]
    return None


# ============================================================================ depo bağlamı
def _ind_frame(df):
    from .. import indicators as ind
    out = df.copy()
    out["ema200"] = ind.ema(out["close"], 200)
    out["atr14"] = ind.atr(out["high"], out["low"], out["close"], 14)
    return out


def entry_context(src: PR.BarSource | None, market: str, symbol: str, as_of_ms: int, entry: Decimal | None
                  ) -> tuple[dict[str, Any], list]:
    """Giriş yeri bağlamı — yalnız `as_of`'tan önce KAPANMIŞ günlük barlar (açılış + 1g ≤ as_of): 20 günlük aralıkta
    konum, 20g tepe/dip ve EMA200'e ATR14(1d) cinsinden uzaklık. Depo yoksa boş."""
    out: dict[str, Any] = {}
    if src is None or entry is None or entry <= 0:
        return out, []
    last_open = (as_of_ms // DAY_MS) * DAY_MS - DAY_MS
    while last_open + DAY_MS > as_of_ms:
        last_open -= DAY_MS
    df, used, st = src.last_closed(market, symbol, "1d", last_open, 399, lookback_ms=420 * DAY_MS)
    if st is not None or len(df) < 20:
        return out, used
    df = _ind_frame(df)
    last = df.iloc[-1]
    atr = float(last["atr14"]) if last["atr14"] == last["atr14"] else None
    ema = float(last["ema200"]) if len(df) >= 200 and last["ema200"] == last["ema200"] else None
    w = df.tail(20)
    hi, lo = float(w["high"].max()), float(w["low"].min())
    e = float(entry)
    out["daily_last_open_ms"] = int(last["timestamp"])
    out["range_pos_20"] = _r6((e - lo) / (hi - lo)) if hi > lo else None
    out["atr14_1d"] = _r6(atr)
    if atr and atr > 0:
        out["dist_20d_high_atr"] = _r6((hi - e) / atr)
        out["dist_20d_low_atr"] = _r6((e - lo) / atr)
        out["dist_ema200_atr"] = _r6((e - ema) / atr) if ema is not None else None
    return out, used


def point_value(src: PR.BarSource | None, market: str, symbol: str, kind: str, col: str, as_of_ms: int, *,
                lookback_ms: int) -> tuple[float | None, int | None, list]:
    """Nokta türünde (`funding`, `metrics_5m`) `as_of`'tan ÖNCEKİ son değer ve zamanı."""
    if src is None:
        return None, None, []
    df, used, st = src.frame(market, symbol, kind, as_of_ms - lookback_ms, as_of_ms - 1)
    if st is not None or not len(df) or col not in df.columns:
        return None, None, used
    df = df[df[col] == df[col]]
    if not len(df):
        return None, None, used
    r = df.iloc[-1]
    return float(r[col]), int(r["timestamp"]), used


def situation_at(src: PR.BarSource | None, market: str, symbol: str, as_of_ms: int) -> tuple[dict | None, list]:
    """`situation_v1` (ortak deneyim katmanının SAF fonksiyonu, aynı `SCHEMA_SHA`) — depodaki 4h/1h + BTC 4h barlarından."""
    if src is None:
        return None, []
    from ..shared_experience import situation as SIT
    used: list = []
    wins = []
    for sym, tf, W, step in ((symbol, "4h", SIT.W4H, H4_MS), (symbol, "1h", SIT.W1H, H1_MS), ("BTCUSDT", "4h", SIT.WBTC, H4_MS)):
        df, u, st = src.frame(market if sym == symbol else "futures", sym, tf, as_of_ms - (W + 6) * step, as_of_ms)
        used += u
        if st is not None:
            return None, used
        wins.append(SIT.normalize_rows(df, tf, as_of_ms, W=W, source="research_store"))
    try:
        snap = SIT.snapshot(symbol, wins[0], wins[1], wins[2], as_of_ms)
    except Exception:  # noqa: BLE001 — hesaplanamayan durum: dürüstçe ERROR anlık görüntüsü
        snap = SIT.error_snapshot(symbol, as_of_ms)
    return snap, used


def _btc_ctx(snap: dict | None) -> dict | None:
    if not isinstance(snap, dict) or snap.get("status") in (None, "NO_BARS", "ERROR"):
        return None
    return {"trend": snap.get("btc_h4_trend"), "ret_6_pct": snap.get("btc_h4_ret_6_pct"), "vol_regime": snap.get("btc_h4_vol_regime"),
            "corr_50": snap.get("corr_btc_h4_50"), "as_of_ms": snap.get("as_of_ms"), "status": snap.get("status")}


# ============================================================================ satır kurma
@dataclass
class BuildContext:
    paths: EnginePaths
    now: datetime
    src: PR.BarSource | None
    rawconfig: Any
    sides: SideSources
    exec_by_book: dict[str, dict | None]
    equity_points: list[tuple[datetime, dict[str, Decimal | None], Decimal | None]]
    write_paths: bool = True
    stats: dict[str, int] = field(default_factory=dict)
    wd: PR.WindowDigest | None = None


def _equity_at(ctx: BuildContext, book: str, t: datetime) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Son ölçülmüş gece görüntüsü (okuma anı ≤ t): (defter özsermayesi, toplam, görüntü anı)."""
    best = None
    for at, books, total in ctx.equity_points:
        if at <= t:
            best = (at, books, total)
        else:
            break
    if best is None:
        return None, None, None
    return best[1].get(book), best[2], iso(best[0])


def _side_sign(side: str) -> int:
    return -1 if str(side).upper() in ("SHORT", "SELL") else 1


def build_row(loc: ArchiveLoc, rec: dict, kind: str, ctx: BuildContext) -> dict[str, Any]:
    """Tek kaydın `tj_v1` satırı (saf: aynı girdiler aynı satırı verir)."""
    R = _Row()
    book = loc.book
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    tid = str(rec.get("id") or "")
    opened, closed = parse_ts(rec.get("opened_at")), parse_ts(rec.get("closed_at"))
    sym = str(rec.get("symbol") or "")
    inst = symbol_key(sym)
    side = str(rec.get("side") or "")
    sgn = _side_sign(side)
    spot = kind == KIND_SPOT
    market = "spot" if spot else "futures"
    sources: list[dict[str, Any]] = [{"file": f"closes/{book}/{loc.seg}", "row": loc.line, "what": "record", "rev": loc.rev}]
    parts: set[tuple[str, str, str]] = set()
    lrn = feats.get("learning") if isinstance(feats.get("learning"), dict) else None

    # ---- yan kaynaklar
    prov = None
    if book == BOOK_MAIN_FUT:
        prov = ctx.sides.provenance(tid)
    elif book == BOOK_MAIN_SPOT:
        bo = spot_buy_order(rec)
        prov = ctx.sides.provenance(bo) if bo else None
    if prov:
        sources.append({"file": prov[1], "row": prov[2], "what": "entry_provenance"})
    prow = prov[0] if prov else {}
    mem = ctx.sides.memory(book, tid) if not spot else None
    if mem:
        sources.append({"file": mem[1], "row": mem[2], "what": "trade_memory_entry"})
    mrow = mem[0] if mem else None
    xp = ctx.sides.xp_entry(loc.key)
    if xp:
        sources.append({"file": xp[1], "row": xp[2], "what": "xp_entry"})
    xrow = xp[0] if xp else {}
    if loc.derived is not None:
        sources.append({"file": f"closes/{book}", "what": "closes_derived"})

    # ---- kimlik
    R.set("schema", JOURNAL_SCHEMA, MEASURED)
    R.set("trade_key", loc.key, MEASURED)
    R.set("rev", loc.rev, MEASURED)
    R.set("source", SOURCE_LIVE, MEASURED)
    R.set("book", book, MEASURED)
    R.set("book_name", book_name(book), MEASURED)
    R.set("trade_id", tid, MEASURED)
    for fld, pk in (("decision_id", "entry_decision_id"), ("code_sha", "entry_code_sha"), ("config_hash", "entry_config_hash")):
        if prow.get(pk):
            R.set(fld, prow[pk], MEASURED)
        else:
            R.miss(fld, "yalnız ana bot provenance'ı taşır (P6: strateji defterleri)" if book not in (BOOK_MAIN_FUT, BOOK_MAIN_SPOT)
                   else "provenance satırı/alanı yok")
    ep, ep_src = RH.config_epoch(book, rec.get("opened_at"), research=ctx.paths.research)
    if ep is None:
        R.miss("config_epoch")
    else:
        R.set("config_epoch", ep, RECONSTRUCTED if ep_src == RH.EPOCH_SRC_PROVISIONAL else MEASURED, ep_src)

    # ---- enstrüman
    R.set("symbol", sym, MEASURED)
    R.set("instrument_class", "GOLD" if is_gold(inst) else "COIN", MEASURED)
    R.set("venue", "spot" if spot else "UM_futures", MEASURED)
    R.set("side", side, MEASURED)

    # ---- yol (rehydrate ve fidelity bunu kullanır)
    risk = None if spot else dec_or_none(feats.get("risk_usdt"))
    stop = None if spot else dec_or_none(feats.get("initial_stop"))
    stop_src_note = None
    entry = dec_or_none(rec.get("entry"))
    qty = dec_or_none(rec.get("quantity"))
    if spot:
        ps = dec_or_none(prow.get("entry_stop"))
        if ps is not None and entry is not None and qty:
            stop, risk = ps, abs(entry - ps) * qty
            stop_src_note = f"{prov[1]}#{prov[2]} entry_stop" if prov else None
    path = PR.reconstruct(rec, trade_key=loc.key, src=ctx.src, market=market, risk_usdt=risk, state=ctx.paths.state,
                          use_position_path=book in (BOOK_MAIN_FUT, BOOK_MAIN_SPOT))
    parts.update(path.parts)
    path_rel = PR.write_path(ctx.paths, path, rec.get("closed_at")) if ctx.write_paths else None
    if path_rel:
        sources.append({"file": path_rel, "what": "path"})

    # ---- rehydrate (strateji defterleri)
    rh = RH.rehydrate(rec, book, src=ctx.src, rawconfig=ctx.rawconfig, memory_row=mrow) if not spot else \
        RH.Rehydrated(status=RH.NOT_APPLICABLE, reason="SPOT")
    parts.update(rh.parts)

    # ---- taktik
    tac, fam = BOOK_TACTIC.get(book, (None, "MAIN_ENSEMBLE"))
    if book in RH.RULE_BOOKS and feats.get("strategy"):
        R.set("tactic", str(feats.get("strategy")), MEASURED)
    elif tac is not None:
        R.set("tactic", tac, RECONSTRUCTED, "defter → kural adı")
    elif rec.get("setup_type"):
        R.set("tactic", str(rec.get("setup_type")), MEASURED)
    else:
        R.miss("tactic")
    R.set("family", fam, RECONSTRUCTED, "defter/kural ailesi sınıflaması")
    cv = feats.get("candle_variation") if isinstance(feats.get("candle_variation"), dict) else None
    if cv and cv.get("id"):
        R.set("variation_id", str(cv["id"]), MEASURED)
    elif rh.variation_id and rh.status == RH.OK:
        R.set("variation_id", rh.variation_id, RECONSTRUCTED)
    elif book in RH.RULE_BOOKS:
        R.miss("variation_id", "rehydrate başarısız: " + str(rh.reason))
    elif rec.get("setup_type"):
        R.set("variation_id", str(rec.get("setup_type")), MEASURED, "setup_type")
    else:
        R.miss("variation_id")
    if cv and cv.get("definition_sha"):
        R.set("params_hash", str(cv["definition_sha"]), MEASURED, "definition_sha")
    elif rh.params_hash and rh.status == RH.OK:
        R.set("params_hash", rh.params_hash, RECONSTRUCTED, "kural modülü kaynağı + parametreler (P6'ya kadar)")
    else:
        R.miss("params_hash")
    if lrn is not None:
        from ..learning_mode import classify_unlock_codes
        codes = [str(c) for c in (lrn.get("learning_unlocked_by") or [])]
        R.set("cohort", classify_unlock_codes(codes).upper(), MEASURED)
        R.set("learning_unlocked_by", codes, MEASURED)
    else:
        R.set("cohort", "UNTAGGED", MEASURED, "öğrenme etiketi yok")
        R.set("learning_unlocked_by", [], MEASURED)
    R.set("record_only", False, MEASURED, "canlı kâğıt işlem")

    # ---- zaman
    ds = feats.get("data_source") if isinstance(feats.get("data_source"), dict) else {}
    dbars = ds.get("bars") if isinstance(ds.get("bars"), dict) else {}
    dec_tf = {"trend": "1d", "box": "5m", "donchian": "4h", "candle": "4h"}.get(rh.family or "", None)
    msig = ((mrow or {}).get("features") or {}).get("signal_ts") if mrow else None
    if msig is not None:
        R.set("signal_ts", int(msig), MEASURED, "trade_memory")
    elif dec_tf and dbars.get(dec_tf) is not None:
        R.set("signal_ts", int(dbars[dec_tf]), MEASURED, f"data_source.bars[{dec_tf}]")
    elif rh.status == RH.OK and (rh.signal_ctx or {}).get("signal_ts") is not None:
        R.set("signal_ts", int(rh.signal_ctx["signal_ts"]), RECONSTRUCTED)
    else:
        R.miss("signal_ts")
    R.set("opened_at", rec.get("opened_at"), MEASURED)
    R.set("closed_at", rec.get("closed_at"), MEASURED)
    ef = feats.get("exit_fill") if isinstance(feats.get("exit_fill"), dict) else None
    if ef and ef.get("first_source") in ("BAR_OPEN", "UNKNOWN"):
        backdated, lab = True, MEASURED
    elif ef and ef.get("first_source") == "PRICE":
        backdated, lab = False, MEASURED
    else:
        backdated = bool(closed is not None and closed.second == 0 and closed.microsecond == 0 and closed.minute % 5 == 0)
        lab = RECONSTRUCTED
    R.set("closed_at_backdated", backdated, lab, None if lab == MEASURED else "kapanış anı bar sınırında (sezgisel)")
    R.set("hold_hours", _r6((closed - opened).total_seconds() / 3600.0) if opened and closed else None,
          MEASURED if opened and closed else MISSING)
    R.set("bars_held", int(rec.get("bars_held") or 0), MEASURED)
    dd = loc.derived or {}
    if spot:
        R.na("decision_delay_s")
    elif dd.get("decision_delay_source") == MEASURED:
        R.set("decision_delay_s", dd.get("decision_delay_s"), MEASURED, "closes_derived")
    else:
        R.miss("decision_delay_s", "provenance karar zamanı yok")

    # ---- giriş yeri
    fills = [f for f in (rec.get("fills") or []) if isinstance(f, dict)]
    ef_fill = next((f for f in fills if f.get("kind") == "entry"), None)
    ref = dec_or_none(ef_fill.get("ref_price")) if (ef_fill and not spot) else None
    R.set("entry_fill", dstr(entry), MEASURED if entry is not None else MISSING, "kayıt `entry` (dolum VWAP)")
    if ref is not None:
        R.set("ref_price", dstr(ref), MEASURED)
        R.set("entry_slip_bps", _r6((entry - ref) / ref * 10000 * sgn) if entry is not None and ref > 0 else None, MEASURED)
    else:
        why = "spot satış kaydı alış dolumunun ref fiyatını taşımaz" if spot else "giriş dolumu yok"
        R.miss("ref_price", why)
        R.miss("entry_slip_bps", why)
    as_of = (_ms(opened) or 0) - 1
    cmarket = "futures" if PR.store_has_series(ctx.src, "futures", sym, "1d") else market
    ec, used = entry_context(ctx.src, cmarket, sym, as_of, entry) if opened is not None else ({}, [])
    parts.update(used)
    for fld in ("range_pos_20", "dist_20d_high_atr", "dist_20d_low_atr", "dist_ema200_atr"):
        if ec.get(fld) is not None:
            R.set(fld, ec[fld], RECONSTRUCTED, "depo 1d (giriş öncesi kapanmış)" if cmarket == market else "vadeli 1d vekil")
        else:
            R.miss(fld, "depoda yeterli 1d bar yok")
    sctx = rh.signal_ctx if rh.status == RH.OK else None
    level = None
    if sctx:
        if rh.family == "box":
            level = sctx.get("box_high") if side.upper() == "SHORT" else sctx.get("box_low")
        elif rh.family == "donchian":
            level = sctx.get("channel_high20")
        elif rh.family == "candle":
            level = sctx.get("pattern_high") if side.upper() == "LONG" else sctx.get("pattern_low")
        elif rh.family == "trend":
            level = sctx.get("ema200")
    atr_rule = (sctx or {}).get("atr14") or ec.get("atr14_1d")
    if level is not None and atr_rule and entry is not None:
        R.set("dist_level_atr", _r6((float(entry) - float(level)) / float(atr_rule)), RECONSTRUCTED,
              "kural seviyesi (kutu kenarı / kanal / formasyon ucu / EMA200), kuralın ATR'si")
    else:
        R.miss("dist_level_atr", "kural seviyesi yeniden kurulamadı" if book in RH.RULE_BOOKS else "kural seviyesi yok (P6)")
    if opened is not None:
        R.set("session", session_of(opened), MEASURED)
        R.set("utc_hour", opened.hour, MEASURED)
        R.set("weekday", opened.weekday(), MEASURED)
    else:
        for fld in ("session", "utc_hour", "weekday"):
            R.miss(fld)
    if spot:
        R.na("funding_rate_at_entry")
        R.na("min_to_next_funding")
    else:
        fr, frt, used = point_value(ctx.src, "futures", sym, "funding", "rate", (_ms(opened) or 0), lookback_ms=10 * DAY_MS)
        parts.update(used)
        if fr is not None:
            R.set("funding_rate_at_entry", _r6(fr), RECONSTRUCTED, "depo fonlama: girişten önceki son uzlaşma")
        else:
            R.miss("funding_rate_at_entry", "depoda fonlama yok")
        if dd.get("min_to_next_funding_source") == MEASURED:
            R.set("min_to_next_funding", dd.get("min_to_next_funding"), MEASURED, "closes_derived")
        else:
            R.miss("min_to_next_funding", "kayıtta funding_hours_utc yok")
    from ..shared_experience import situation as SIT
    xsnap = xrow.get("snapshot") if isinstance(xrow.get("snapshot"), dict) else None
    if xsnap and xsnap.get("schema_sha") == SIT.SCHEMA_SHA:
        R.set("situation_entry", xsnap, MEASURED, "xp_entry")
        snap_e = xsnap
    else:
        snap_e, used = situation_at(ctx.src, "futures", sym, as_of) if opened is not None else (None, [])
        parts.update(used)
        if snap_e is not None and snap_e.get("status") not in ("NO_BARS", "ERROR"):
            R.set("situation_entry", snap_e, RECONSTRUCTED, "aynı saf fonksiyon, depo barları, as_of = opened_at − 1 ms")
        else:
            snap_e = None
            R.miss("situation_entry", "depoda 4h/1h/BTC barı yok")

    # ---- büyüklük ve kaldıraç
    R.set("qty", dstr(qty), MEASURED if qty is not None else MISSING)
    R.set("notional", dstr(dec_or_none(rec.get("effective_notional"))), MEASURED)
    lev = 1 if spot else int(rec.get("leverage") or 1)
    R.set("leverage", lev, MEASURED, "spot: 1" if spot else None)
    liq = None
    if spot:
        R.na("margin")
        R.na("liquidation_price")
        R.na("liq_distance_in_stops")
    else:
        R.set("margin", dstr(dec_or_none(rec.get("effective_margin"))), MEASURED)
        liq = dec_or_none(rec.get("liquidation_price"))
        R.set("liquidation_price", dstr(liq), MEASURED if liq is not None else MISSING)
        if liq is not None and entry is not None and stop is not None and entry != stop:
            R.set("liq_distance_in_stops", _r6(abs(entry - liq) / abs(entry - stop)), MEASURED)
        else:
            R.miss("liq_distance_in_stops")
    if risk is not None and risk > 0:
        R.set("risk_usdt", dstr(risk), MEASURED, "features.risk_usdt" if not spot else "provenance entry_stop × miktar")
    else:
        risk = None
        R.miss("risk_usdt", "eski kayıt (features.risk_usdt yok)" if not spot else "spot: provenance stop'u yok")
    eq_book, eq_total, eq_at = _equity_at(ctx, book, opened) if opened is not None else (None, None, None)
    eq_lab, eq_note = RECONSTRUCTED, f"son ölçülmüş gece görüntüsü {eq_at}"
    if lrn is not None and lrn.get("equity_basis") is not None:
        eq_book, eq_lab, eq_note = dec_or_none(lrn.get("equity_basis")), MEASURED, "features.learning.equity_basis"
    if eq_book is not None and eq_book > 0:
        R.set("equity_at_entry", dstr(eq_book), eq_lab, eq_note)
    else:
        eq_book = None
        R.miss("equity_at_entry", "girişten önce ölçülmüş anlık görüntü yok")
    if risk is not None and eq_book is not None:
        R.set("risk_pct_of_equity", _r6(risk / eq_book * 100), eq_lab)
    else:
        R.miss("risk_pct_of_equity")
    if lrn is not None and lrn.get("size_rule"):
        R.set("size_rule", str(lrn.get("size_rule")), MEASURED)
    else:
        R.na("size_rule", "öğrenme modu dışı boyut (taban kuralı)")

    # ---- plan
    if stop is not None:
        R.set("initial_stop", dstr(stop), MEASURED, stop_src_note or "features.initial_stop")
        R.set("stop_dist_pct", _r6(abs(entry - stop) / entry * 100) if entry else None, MEASURED)
        if atr_rule:
            R.set("stop_dist_atr", _r6(abs(float(entry) - float(stop)) / float(atr_rule)), RECONSTRUCTED)
        else:
            R.miss("stop_dist_atr")
    else:
        R.miss("initial_stop", "eski kayıt" if not spot else "spot: stop yok (P6)")
        R.miss("stop_dist_pct")
        R.miss("stop_dist_atr")
    targets, tsrc = None, None
    if isinstance(xrow.get("targets"), list):
        targets, tsrc = [float(t) for t in xrow["targets"] if t is not None], (MEASURED, "xp_entry")
    elif isinstance(prow.get("entry_targets"), list):
        targets, tsrc = [float(t) for t in prow["entry_targets"] if t is not None], (MEASURED, "entry_provenance")
    elif rh.status == RH.OK and rh.targets is not None:
        targets, tsrc = list(rh.targets), (RECONSTRUCTED, "rehydrate")
    if targets is not None:
        R.set("targets", targets, tsrc[0], tsrc[1])
    else:
        R.miss("targets", "RECONSTRUCT_FAILED: " + str(rh.reason) if book in RH.RULE_BOOKS else "hedef kaynağı yok")
    xp_par = ctx.exec_by_book.get(book)
    if spot:
        R.na("tp1_fraction")
        R.na("breakeven_at_mfe_r")
    elif xp_par:
        R.set("tp1_fraction", str(xp_par.get("tp1_fraction")), MEASURED, "ledger ayarı (güncel)")
        R.set("breakeven_at_mfe_r", str(xp_par.get("breakeven_at_mfe_r")), MEASURED, "ledger ayarı (güncel)")
    else:
        R.miss("tp1_fraction", "ledger okunamadı")
        R.miss("breakeven_at_mfe_r", "ledger okunamadı")
    if cv and cv.get("max_hold_bars"):
        R.set("max_hold", {"bars": int(cv["max_hold_bars"]), "tf": "4h"}, MEASURED, "features.candle_variation")
    elif rh.family == "donchian":
        R.set("max_hold", {"bars": 300, "tf": "4h"}, RECONSTRUCTED, "donchian_trend.MAX_BARS")
    elif rh.family == "box":
        R.set("max_hold", {"rule": "EOD_UTC"}, RECONSTRUCTED, "Box gün sonu düzleşmesi")
    elif rh.family == "trend":
        R.na("max_hold", "trend kuralının zaman stopu yok")
    else:
        R.miss("max_hold")
    if targets and risk is not None and entry is not None and qty and not spot:
        t1 = Decimal(repr(float(targets[0])))
        fees_d = (xp_par or {}).get("fees") or {}
        rate = dec(fees_d.get("maker_pct" if (xp_par or {}).get("tp_maker") else "taker_pct", "0.05")) / 100
        unit = sgn * (t1 - entry) - dec(rec.get("entry_fee")) / qty - rate * t1
        R.set("planned_rr_after_cost", _r6(unit / (risk / qty)), MODELED, "hedef1, giriş ücreti + çıkış ücreti oranı")
    elif targets == []:
        R.na("planned_rr_after_cost", "hedef yok")
    else:
        R.miss("planned_rr_after_cost")

    # ---- çıkış
    R.set("exit_price", dstr(dec_or_none(rec.get("exit_price"))), MEASURED)
    xr = str(rec.get("exit_reason") or "")
    R.set("exit_reason", xr, MEASURED)
    rpu = (risk / qty) if (risk is not None and qty) else None
    xd = exit_fill_detail(ef, exit_reason=xr, side=side, rpu=rpu, path=path)
    R.set("exit_basis", xd["exit_basis"], MEASURED if xd["bar_open_gap"] is None else RECONSTRUCTED,
          "exit_fill.basis + yol (attribution.classify_exit_basis)" if ef else "çıkış nedeni")
    if xr in _STOP_EXITS and xd.get("overshoot_r") is not None:
        R.set("exit_overshoot_r", xd["overshoot_r"], MEASURED, "yön·(stop − gözlem fiyatı) / birim risk (kayma öncesi)")
    elif xr in _STOP_EXITS:
        R.miss("exit_overshoot_r", "R paydası ya da exit_fill yok")
    else:
        R.na("exit_overshoot_r", "stop çıkışı değil")
    R.set("tp1_done", bool(rec.get("tp1_done")), MEASURED)
    R.set("fills", [{"kind": f.get("kind"), "ts": f.get("ts"), "side": f.get("side"), "qty": f.get("qty"), "price": f.get("price"),
                     "ref_price": f.get("ref_price"), "fee": f.get("fee"), "slippage": f.get("slippage"),
                     "is_maker": bool(f.get("is_maker"))} for f in fills], MEASURED)

    # ---- gelir ve maliyet (2026-10-08, inceleme küçüğü: kayıtta OLMAYAN para alanı 0 diye UYDURULMAZ → MISSING)
    gross, ef_, xf = dec_or_none(rec.get("gross_pnl")), dec_or_none(rec.get("entry_fee")), dec_or_none(rec.get("exit_fee"))
    fees, slip = dec_or_none(rec.get("fees")), dec_or_none(rec.get("slippage_cost"))
    net = dec_or_none(rec.get("net_pnl")) if rec.get("net_pnl") is not None else dec_or_none(rec.get("pnl"))

    def _money(fld: str, v: Decimal | None, label: str = MEASURED, note: str | None = None) -> None:
        if v is None:
            R.miss(fld, "eski kayıt: alan yok (0 diye doldurulmaz)")
        else:
            R.set(fld, dstr(v), label, note)
    _money("gross_pnl", gross)
    _money("entry_fee", ef_)
    _money("exit_fee", xf)
    _money("slippage_cost", slip, MODELED, "defterin kayma modeli (sabit bps), dolum fiyatlarının içinde")
    R.set("spread_cost", dstr(dec(rec.get("spread_cost"))), MODELED, "MODELED_ZERO: defter spread yazmaz")
    R.miss("spread_est", "kaydedilmemiş spread tahmini (dolumda canlı spread yok, P6)")
    if spot:
        for fld in ("funding_paid", "funding_received", "funding_net", "funding_complete"):
            R.na(fld, "spot")
        fund = Decimal(0)
    else:
        fund = dec_or_none(rec.get("funding"))
        _money("funding_paid", dec_or_none(rec.get("funding_paid")))
        _money("funding_received", dec_or_none(rec.get("funding_received")))
        _money("funding_net", fund)
        cov = feats.get("funding_coverage") if isinstance(feats.get("funding_coverage"), dict) else {}
        if isinstance(cov.get("complete"), bool):
            R.set("funding_complete", cov["complete"], MEASURED)
        else:
            R.miss("funding_complete", "kayıtta funding_coverage yok")
    _money("net_pnl", net)
    fee_ok = (abs(fees - (ef_ + xf)) <= TOL) if None not in (fees, ef_, xf) else None
    rmult = dec_or_none(rec.get("r_multiple")) if not spot else None
    if risk is not None and net is not None:
        R.set("net_r", format(net / risk, "f"), MEASURED, "net_pnl / risk_usdt")
    else:
        R.miss("net_r", "R paydası yok" if risk is None else "net_pnl yok")
    if risk is not None and gross is not None:
        R.set("gross_r", format(gross / risk, "f"), MEASURED)
    else:
        R.miss("gross_r", "R paydası yok" if risk is None else "gross_pnl yok (eski kayıt)")
    if risk is not None and None not in (fees, fund, slip):
        fee_r, fund_r, slip_r = fees / risk, -fund / risk, slip / risk
        R.set("cost_r", {"fee": format(fee_r, "f"), "funding": format(fund_r, "f"), "slippage_in_fills": format(slip_r, "f"),
                         "total": format(fee_r + fund_r, "f")}, MEASURED, "pozitif = maliyet; kayma brüt içinde")
    else:
        R.miss("cost_r", "R paydası yok" if risk is None else "ücret/fonlama/kayma alanı yok (eski kayıt)")
    if eq_book is not None and net is not None:
        R.set("pnl_pct_book_equity", _r6(net / eq_book * 100), eq_lab)
    else:
        R.miss("pnl_pct_book_equity")
    if eq_total is not None and eq_total > 0 and net is not None:
        R.set("pnl_pct_total_equity", _r6(net / eq_total * 100), RECONSTRUCTED, f"toplam özsermaye {eq_at}")
    else:
        R.miss("pnl_pct_total_equity")

    # ---- yol
    m = path.metrics or {}
    for fld in ("mfe_r", "mae_r", "t_mfe", "t_mae", "order", "time_to_1r", "giveback_r", "capture_ratio"):
        val = m.get(fld)
        if val is None:
            R.miss(fld, f"yol: {path.source}" if path.source != PR.SRC_MISSING else "yol yok")
        else:
            R.set(fld, val, MEASURED if path.source == PR.SRC_EXTREMES else RECONSTRUCTED,
                  "defter uçları (mfe_pct/mae_pct)" if path.source == PR.SRC_EXTREMES else None)
    if path.source == PR.SRC_MISSING:
        R.miss("path_source")
        R.miss("ambiguous_bars")
    else:
        R.set("path_source", path.source, RECONSTRUCTED)
        R.set("ambiguous_bars", int(m.get("ambiguous_bars") or 0), RECONSTRUCTED)

    # ---- bağlam
    snap_x, used = situation_at(ctx.src, "futures", sym, _ms(closed)) if closed is not None else (None, [])
    parts.update(used)
    if snap_x is not None and snap_x.get("status") not in ("NO_BARS", "ERROR"):
        R.set("situation_exit", snap_x, RECONSTRUCTED, "aynı saf fonksiyon, depo barları, as_of = closed_at")
    else:
        snap_x = None
        R.miss("situation_exit", "depoda 4h/1h/BTC barı yok")
    for fld, sn in (("btc_ctx_entry", snap_e), ("btc_ctx_exit", snap_x)):
        bc = _btc_ctx(sn)
        if bc is not None:
            R.set(fld, bc, R.src.get("situation_entry" if fld.endswith("entry") else "situation_exit", RECONSTRUCTED))
        else:
            R.miss(fld)
    if spot:
        R.na("oi_change_24h")
        R.na("taker_ratio")
    else:
        o_now, _t1, u1 = point_value(ctx.src, "futures", sym, "metrics_5m", "oi", _ms(opened) or 0, lookback_ms=2 * H1_MS)
        o_prev, _t2, u2 = point_value(ctx.src, "futures", sym, "metrics_5m", "oi", (_ms(opened) or 0) - DAY_MS, lookback_ms=2 * H1_MS)
        tk, _t3, u3 = point_value(ctx.src, "futures", sym, "metrics_5m", "taker_ls_vol", _ms(opened) or 0, lookback_ms=2 * H1_MS)
        parts.update(u1 + u2 + u3)
        if o_now is not None and o_prev:
            R.set("oi_change_24h", _r6(o_now / o_prev - 1.0), RECONSTRUCTED, "metrics_5m oi (giriş öncesi)")
        else:
            R.miss("oi_change_24h", "depoda metrics_5m yok")
        if tk is not None:
            R.set("taker_ratio", _r6(tk), RECONSTRUCTED, "metrics_5m taker_ls_vol (giriş öncesi)")
        else:
            R.miss("taker_ratio", "depoda metrics_5m yok")
    if book in (BOOK_MAIN_FUT, BOOK_MAIN_SPOT):
        if prow:
            efe = prow.get("entry_features") if isinstance(prow.get("entry_features"), dict) else {}
            R.set("agents_ctx", {"p_win": prow.get("entry_p_win"), "expected_r": prow.get("entry_expected_r"),
                                 "regime": prow.get("entry_regime"), "specialist_scores": prow.get("entry_specialist_scores"),
                                 "risk_decision": prow.get("entry_risk_decision"), "policy_id": prow.get("entry_policy_id"),
                                 "entry_features": {k: efe.get(k) for k in ENTRY_DECISION_FEATURES if k in efe}},
                  MEASURED, "entry_provenance (giriş kararı)")
        else:
            R.miss("agents_ctx", "provenance satırı yok")
    else:
        R.na("agents_ctx", "ana bot değil")
    if book in RH.RULE_BOOKS:
        if sctx is not None:
            R.set("signal_ctx", sctx, RECONSTRUCTED, "rehydrate")
        else:
            R.miss("signal_ctx", "RECONSTRUCT_FAILED: " + str(rh.reason))
    elif book in (BOOK_MAIN_FUT, BOOK_MAIN_SPOT):
        R.na("signal_ctx", "ana bot topluluğu: kural bağlamı yok (agents_ctx)")
    else:
        R.miss("signal_ctx", "bu defter için yeniden kurma yok (§5.2)")
    if is_gold(inst):
        gctx: dict[str, Any] = {"session": session_of(opened) if opened else None}
        paxg, _, u1 = point_value(ctx.src, "futures", "PAXGUSDT", "1h", "close", (_ms(opened) or 0) - H1_MS + 1, lookback_ms=6 * H1_MS)
        xau, _, u2 = point_value(ctx.src, "futures", "XAUUSDT", "1h", "close", (_ms(opened) or 0) - H1_MS + 1, lookback_ms=6 * H1_MS)
        parts.update(u1 + u2)
        gctx["paxg_xau_basis_pct"] = _r6((paxg / xau - 1.0) * 100) if (paxg and xau) else None
        R.set("gold_ctx", gctx, RECONSTRUCTED)
    else:
        R.na("gold_ctx", "altın değil")

    # ---- atıf (P2a: fidelity)
    from .attribution import DEFAULT_ENTRY_TF, ENTRY_TF, TF_MS
    fd = FD.replay(rec, kind=kind, bars=path.bars, tf=path.tf, path_gaps=path.gaps, targets=targets,
                   targets_known=targets is not None, exec_par=xp_par, backdated=backdated, tick=RH.tick_of(rec),
                   rule_tf_ms=TF_MS[ENTRY_TF.get(book, DEFAULT_ENTRY_TF)])
    R.set("fidelity", fd, RECONSTRUCTED if fd.get("status") != FD.ST_NOT_ELIGIBLE else NOT_APPLICABLE,
          "learning_cf._net_replay, kaydın R paydası")

    # ---- köken
    R.set("sources", sources, MEASURED)
    plist = sorted(parts)
    if plist and ctx.wd is not None and opened is not None and closed is not None:
        plist = ctx.wd.row_tokens(plist, PR.s1b_windows(sym, market, _ms(opened), _ms(closed), gold=is_gold(inst)))
    R.set("data_seal", ResearchStore.seal_of(plist) if plist else None, RECONSTRUCTED if plist else MISSING,
          "satırın okuma penceresindeki depo içeriğinin mührü (pathrec.WindowDigest belirteçleri; ResearchStore.seal_of)")
    R.set("journal_version", JOURNAL_VERSION, MEASURED)
    missing = sorted(f for f, lab in R.src.items() if lab == MISSING)
    R.set("missing_fields", missing, MEASURED)

    row: dict[str, Any] = {}
    for fname in ALL_FIELDS:
        row[fname] = R.v.get(fname)
    row["kind"] = kind
    row["fees_total"] = dstr(fees) if fees is not None else None
    row["position_group"] = loc.position_group if spot else loc.key
    row["fee_identity_ok"] = fee_ok
    row["r_check"] = ({"ledger_r_multiple": dstr(rmult), "abs_diff": format(abs(net / risk - rmult), "f"),
                       "ok": abs(net / risk - rmult) <= R_TOL} if (risk is not None and rmult is not None and net is not None)
                      else None)
    row["path"] = {**path.summary(), "file": path_rel}
    row["rehydrate"] = rh.to_dict()
    row["store_parts"] = [list(p) for p in plist]                  # pencere belirteçleri (M5): (seri, ay, sha | d:gün özeti)
    row["field_source"] = {f: R.src[f] for f in ALL_FIELDS}
    row["field_notes"] = dict(sorted(R.notes.items()))
    row["cf_inputs"] = cf_inputs(rec, kind)
    row["exit_fill_detail"] = xd if xd.get("basis") else None
    return row


def exit_fill_detail(ef: dict | None, *, exit_reason: str, side: str, rpu: Decimal | None, path: PR.PathResult
                     ) -> dict[str, Any]:
    """Çıkış dolumunun ölçülmüş olguları ve sınıfı (2026-10-08, inceleme B1). Canlı defter stopu seviyenin ötesindeki İLK
    gözlemden doldurur (`exit_fill.basis`: 60 sn örnek `GAP_FILL_AT_FIRST_OBSERVATION`, kural barı açılışı, ihtiyatlı
    kapanış); bu bir piyasa boşluğu DEĞİLDİR, yolda seviye sürekli işlem gördüyse örnekleme aşmasıdır. Olgular: gözlem
    fiyatı, seviyenin ötesinde mi, aşma (R), bar yolunda (1m/5m) stopa ilk ulaşan bar ve o barın stopun ötesinde açılıp
    açılmadığı (`pathrec.stop_crossing`). Sınıf `attribution.classify_exit_basis`'tedir (mühürlü kural)."""
    from .attribution import EXIT_OBSERVED_BASES, classify_exit_basis
    b = str((ef or {}).get("basis") or "")
    sg = Decimal(1) if str(side).upper() in ("LONG", "BUY") else Decimal(-1)
    stop = dec_or_none((ef or {}).get("stop"))
    ref = dec_or_none((ef or {}).get("close_price") if b == "STOP_CLOSE_BEYOND_LEVEL_PRUDENT" else (ef or {}).get("first_price"))
    beyond = bool(b in EXIT_OBSERVED_BASES and stop is not None and ref is not None and sg * (stop - ref) > 0)
    ov: float | None = None
    if exit_reason in _STOP_EXITS and b and stop is not None and rpu is not None and rpu > 0:
        ov = _r6(sg * (stop - ref) / rpu) if (beyond and ref is not None) else 0.0
    t_cross, gap = None, None
    if beyond and path.source in (PR.SRC_1M, PR.SRC_5M):
        t_cross, gap = PR.stop_crossing(path.bars, side=side, stop=stop)
    eb, mech = classify_exit_basis(b or None, beyond=beyond, overshoot_r=ov, bar_open_gap=gap,
                                   target_exit=exit_reason in _TARGET_EXITS)
    return {"basis": b or None, "first_source": (ef or {}).get("first_source"), "stop": dstr(stop), "fill_ref": dstr(ref),
            "beyond_level": beyond, "overshoot_r": ov, "crossing_bar": t_cross, "bar_open_gap": gap, "mechanism": mech,
            "exit_basis": eb}


def cf_inputs(rec: dict, kind: str) -> dict[str, Any] | None:
    """P2b karşı-olgusal ızgaranın kayıttan ihtiyaç duyduğu küçük, ÖLÇÜLMÜŞ girdiler (S2 arşiv kaydını yeniden okumasın):
    kaydın KENDİ fonlama uzlaşmaları (`fidelity.RecordedFunding` girdisi: an, oran, settlement mark'ı; ters çevrilenler
    hariç), watermark, sembolün fonlama saatleri ve gerçek stop çıkışının aşma yüzdesi (`learning_cf_aux` `cf_aux_v1`
    tanımı: yalnız `GAP_FILL_AT_FIRST_OBSERVATION` + `first_source == PRICE`). Spot: None."""
    if kind != KIND_FUTURES:
        return None
    f = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    sett = []
    for r in f.get("funding_settlements") or []:
        if not isinstance(r, dict) or r.get("reversed_at") or r.get("path") == "REVERSAL":
            continue
        t = parse_ts(r.get("settlement"))
        if t is None or r.get("rate") is None:
            continue
        sett.append([iso(t), str(r.get("rate")), None if r.get("mark") is None else str(r.get("mark"))])
    hours = f.get("funding_hours_utc") if isinstance(f.get("funding_hours_utc"), list) else None
    ov = None
    ef = f.get("exit_fill") if isinstance(f.get("exit_fill"), dict) else None
    if (ef and ef.get("basis") == "GAP_FILL_AT_FIRST_OBSERVATION" and ef.get("first_source") == "PRICE"
            and str(rec.get("exit_reason") or "") in ("stop", "başa-baş stop")):
        first, stp, ent = dec_or_none(ef.get("first_price")), dec_or_none(ef.get("stop")), dec_or_none(rec.get("entry"))
        if first is not None and stp is not None and ent is not None and ent > 0:
            sg = 1 if str(rec.get("side") or "").upper() == "LONG" else -1
            ov = _r6(float(sg * (stp - first) / ent * 100))
    return {"funding_settlements": sorted(sett), "funding_settled_until": f.get("funding_settled_until"),
            "funding_hours_utc": [int(h) for h in hours] if hours else None, "exit_overshoot_pct": ov,
            "entry_ref": f.get("entry_ref")}


# ============================================================================ derleme
def _exec_params_by_book(paths: EnginePaths) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for b, (kind, p) in sorted(find_ledgers(paths.state).items()):
        if kind != KIND_FUTURES:
            continue
        rd = read_ledger(b, kind, p)
        out[b] = FD.exec_params(rd.doc) if rd.ok else None
        rd.doc = None
    return out


def _equity_points(paths: EnginePaths) -> list[tuple[datetime, dict[str, Decimal | None], Decimal | None]]:
    pts = []
    for s in load_snapshots(paths, light=True):
        books = s.get("books") or {}
        eq: dict[str, Decimal | None] = {}
        tot: Decimal | None = Decimal(0)
        at = parse_ts(s.get("taken_at"))
        for b, body in sorted(books.items()):
            if not isinstance(body, dict) or body.get("status") != "OK":
                if not is_mirror(b):
                    tot = None
                continue
            e = dec_or_none(body.get("equity"))
            eq[b] = e
            if not is_mirror(b):
                tot = (tot + e) if (tot is not None and e is not None) else None
        if at is not None:
            pts.append((at, eq, tot))
    pts.sort(key=lambda x: x[0])
    return pts


def _month_of(closed_at: Any) -> str:
    t = parse_ts(closed_at)
    return t.strftime("%Y-%m") if t is not None else "unknown"


def _closed_iso(proj: dict) -> str:
    t = parse_ts((proj or {}).get("closed_at"))
    return iso(t) if t is not None else ""


def _sort_key(row: dict) -> tuple[str, str]:
    t = parse_ts(row.get("closed_at"))
    return (iso(t) if t is not None else "", str(row.get("trade_key")))


def month_file(paths: EnginePaths, month: str) -> Path:
    return paths.journal_tj / f"{month}.jsonl.gz"


def _index_path(paths: EnginePaths) -> Path:
    return paths.journal_tj / "_index.json.gz"


def _read_index(paths: EnginePaths) -> dict[str, dict]:
    p = _index_path(paths)
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError, EOFError):
        return {}
    if not isinstance(d, dict) or d.get("schema") != INDEX_SCHEMA or d.get("journal_version") != JOURNAL_VERSION:
        return {}
    return dict(d.get("keys") or {})


def iter_journal(paths: EnginePaths, months: Iterable[str] | None = None) -> Iterator[dict]:
    """Günlük satırları (ay dosyası sırasıyla). P2b'nin S2 ve `engine-query` girdisi."""
    root = paths.journal_tj
    if not root.is_dir():
        return
    want = set(months) if months is not None else None
    for p in sorted(root.glob("????-??.jsonl.gz")):
        if want is not None and p.name[:7] not in want:
            continue
        for _n, row in iter_gz_lines(p):
            yield row


def _input_digest(loc: ArchiveLoc, side: dict[str, Any], seal_listing_digest: str, cfg_digest: str, exec_digest: str,
                  eq_digest: str) -> str:
    return _sha({"v": JOURNAL_VERSION, "sha": loc.sha, "rev": loc.rev, "status": loc.status, "derived": loc.derived,
                 "pg": loc.position_group, "side": side, "seal": seal_listing_digest, "cfg": cfg_digest,
                 "exec": exec_digest, "eq": eq_digest})


def _src_digest(src: PR.BarSource | None) -> str:
    """Depo içerik özeti: mühürlü okuyucuda mühür; değilse manifestlerin parça listesinin özeti."""
    if src is None:
        return "no-store"
    if src.seal:
        return str(src.seal)
    st = src._store()
    return ResearchStore.seal_of(st.seal_listing())


def build_journal(paths: EnginePaths, *, now: datetime | None = None, src: PR.BarSource | None = None, rawconfig: Any = None,
                  books: Iterable[str] | None = None, write_paths: bool = True,
                  progress: Callable[[str], None] | None = None, budget_s: float | None = None,
                  max_rows: int | None = None) -> dict[str, Any]:
    """Günlüğü arşivden kur (artımlı): girdileri değişen satırlar yeniden hesaplanır, etkilenen ay dosyaları yeniden
    yazılır. Dönen özet (`_build.json`): satır/ay sayıları, fidelity kapısı, rehydrate eşleşme oranları, depo mührü.

    **Kaldığı yerden devam (P2b, gece son tarihi):** `budget_s` (duvar saati saniyesi) ya da `max_rows` verilirse
    değişen satırlar EN YENİ kapanıştan geriye kurulur ve bütçe bitince durulur. Kurulamayan satırın eski hâli (varsa)
    eski özetiyle AYNEN kalır (ertesi gece yeniden kurulur); hiç kurulmamış yeni kayıt dizine girmez (ertesi gece "yeni"
    sayılır). Bekleyen sayısı dönen özette `pending`'dir (`_build.json` içerik özetine girmez)."""
    now = now or utc_now()
    all_books = sorted(set(books) if books is not None else
                       {p.name for p in paths.closes.iterdir() if p.is_dir()} if paths.closes.exists() else set())
    kinds = {b: (KIND_SPOT if b == BOOK_MAIN_SPOT else KIND_FUTURES) for b in all_books}
    locs: dict[str, dict[str, ArchiveLoc]] = {b: scan_archive(paths, b) for b in all_books}
    active = {b: {k: v for k, v in ls.items() if v.status == ST_ACTIVE} for b, ls in locs.items()}
    restored = sum(1 for ls in locs.values() for v in ls.values() if v.status == ST_RESTORED_AWAY)
    ids_by_book: dict[str, set[str]] = {}
    keys: set[str] = set()
    spot_buy_ids: set[str] = set()
    for b, ls in active.items():
        for k in ls:
            keys.add(k)
            ids_by_book.setdefault(b, set()).add(k.split("|", 2)[1])
    exec_by_book = _exec_params_by_book(paths)
    seal_d = _src_digest(src)
    cfg_by_book: dict[str, str] = {}
    for b in all_books:
        name = RH.RULE_BOOKS.get(b)
        cfg_by_book[b] = _sha(RH.rule_params_for(name, rawconfig)) if name else "-"
    eq_pts = _equity_points(paths)
    old_index = _read_index(paths)
    # yan kaynaklar: spot alış emirleri için kayıtların tamamı gerekir (FIFO lotu) → spot kayıtları her gece okunur (küçük)
    spot_bo: dict[str, str | None] = {}                  # spot anahtarı → alış emri (kayıtlar bellekte tutulmaz)
    if BOOK_MAIN_SPOT in active:
        for k, r in load_records(paths, BOOK_MAIN_SPOT, active[BOOK_MAIN_SPOT]).items():
            bo = spot_buy_order(r)
            spot_bo[k] = bo
            if bo:
                spot_buy_ids.add(bo)
    ids_for_sides = {b: set(v) for b, v in ids_by_book.items()}
    ids_for_sides[BOOK_MAIN_SPOT] = spot_buy_ids
    sides = SideSources(paths.state, ids_by_book=ids_for_sides, keys=keys)
    wd = PR.WindowDigest(src, cache_file=paths.journal_tj / "_window_days.json.gz", paths=paths) if src is not None else None
    ctx = BuildContext(paths=paths, now=now, src=src, rawconfig=rawconfig, sides=sides, exec_by_book=exec_by_book,
                       equity_points=eq_pts, write_paths=write_paths, wd=wd)
    new_index: dict[str, dict] = {}
    changed: dict[str, list[str]] = {}
    for b in all_books:
        for k, loc in sorted(active[b].items()):
            tid = k.split("|", 2)[1]
            side = {"prov": _sha(sides.provenance(tid)[0]) if sides.provenance(tid) else None,
                    "mem": _sha(sides.memory(b, tid)[0]) if sides.memory(b, tid) else None,
                    "xp": _sha(sides.xp_entry(k)[0]) if sides.xp_entry(k) else None}
            if b == BOOK_MAIN_SPOT:
                bo = spot_bo.get(k)
                side["prov"] = _sha(sides.provenance(bo)[0]) if bo and sides.provenance(bo) else None
            opened, closed = parse_ts(loc.proj.get("opened_at")), parse_ts(loc.proj.get("closed_at"))
            eq_at = _equity_at(ctx, b, opened)[2] if opened is not None else None
            sym_p = str(loc.proj.get("symbol") or "")
            pdg = (wd.digest(PR.s1b_windows(sym_p, "spot" if kinds[b] == KIND_SPOT else "futures", _ms(opened), _ms(closed),
                                            gold=is_gold(symbol_key(sym_p))))
                   if (src is not None and opened is not None and closed is not None) else "no-store")
            dg = _input_digest(loc, side, pdg, cfg_by_book[b], _sha(exec_by_book.get(b)), str(eq_at))
            month = _month_of(loc.proj.get("closed_at"))
            new_index[k] = {"digest": dg, "month": month, "book": b}
            old = old_index.get(k)
            if old is None or old.get("digest") != dg or not month_file(paths, month).exists():
                changed.setdefault(b, []).append(k)
    # ---- değişen satırları kur: EN YENİ ay önce, ay içinde en yeni kapanış önce; her ay bitince o ayın dosyası yazılır
    # (bellekte en çok bir ayın yeni satırları durur). Bütçe/sınır bitince kalan satırlar bekler (ertesi gece).
    n_built = 0
    t_start = time.monotonic()
    order = sorted(((_closed_iso(active[b][k].proj), k, b) for b, ks in changed.items() for k in ks), reverse=True)
    by_month: dict[str, list[tuple[str, str]]] = {}
    for _c, k, b in order:
        by_month.setdefault(new_index[k]["month"], []).append((b, k))
    pending: list[tuple[str, str]] = []
    written: list[str] = []

    def _over() -> bool:
        return (budget_s is not None and time.monotonic() - t_start > budget_s) or (max_rows is not None and n_built >= max_rows)

    clean_spills(paths, paths.journal_tj)
    if paths.paths_root.is_dir():
        for d in sorted(paths.paths_root.glob("????-??")):
            clean_spills(paths, d)
    for month in sorted(by_month, reverse=True):
        items = by_month[month]
        writer = MonthWriter(paths, month_file(paths, month),
                             lambda k, _m=month: k in new_index and new_index[k]["month"] == _m, _sort_key,
                             flush_rows=FLUSH_ROWS)
        n_month = 0
        for i in range(0, len(items), LOAD_CHUNK):
            part = items[i:i + LOAD_CHUNK]
            if _over():
                pending += part
                continue
            want: dict[str, list[str]] = {}
            for b, k in part:
                want.setdefault(b, []).append(k)
            recs_by_book = {b: load_records(paths, b, {k: active[b][k] for k in ks}) for b, ks in want.items()}
            for b, k in part:
                if _over():
                    pending.append((b, k))
                    continue
                rec = recs_by_book[b].get(k)
                if rec is None:
                    continue
                writer.add(k, build_row(active[b][k], rec, kinds[b], ctx))
                n_built += 1
                n_month += 1
                if progress is not None and n_built % 500 == 0:
                    progress(f"journal {n_built}")
            recs_by_book = {}
        if n_month and writer.close():
            written.append(month)
        elif not n_month:
            writer.buf = []
    for b, k in pending:                                   # kurulamayan: eski satır ve eski özeti kalır (yoksa dizine girmez)
        old = old_index.get(k)
        if old is not None and month_file(paths, str(old.get("month"))).exists():
            new_index[k] = old
        else:
            new_index.pop(k, None)
    removed = {k: v for k, v in old_index.items() if k not in new_index}
    # ---- satır düşen aylar (kaldırılan ya da ayı değişen anahtarların ESKİ ayı) süzülür; yazılan aylar zaten tamdır
    months_drop = ({v.get("month") for v in removed.values()}
                   | {v.get("month") for k, v in old_index.items() if k in new_index and new_index[k]["month"] != v.get("month")})
    months_drop.discard(None)
    for month in sorted(months_drop):
        if _write_month(paths, month, {}, new_index) and month not in written:
            written.append(month)
    if wd is not None:
        wd.save()
    idx = {"schema": INDEX_SCHEMA, "journal_version": JOURNAL_VERSION, "keys": dict(sorted(new_index.items()))}
    paths.write_bytes(_index_path(paths), gzip_bytes(json.dumps(idx, ensure_ascii=False, separators=(",", ":"),
                                                                sort_keys=False).encode("utf-8")))
    # özet iki akışla (bütün satırlar ya da izdüşümleri bellekte tutulmaz; kabul: 45 bin satırda bellek, §2.5)
    acc = {"rows": 0, "owner_ok": True, "fee_bad": 0, "r_bad": 0, "paths": {}}

    def _fd_rows() -> Iterator[dict]:
        for r in iter_journal(paths):
            acc["rows"] += 1
            acc["owner_ok"] = acc["owner_ok"] and owner_fields_ok(r)
            acc["fee_bad"] += 1 if r.get("fee_identity_ok") is False else 0      # None = alan yok (eski kayıt), ihlal değil
            acc["r_bad"] += 1 if (r.get("r_check") and not r["r_check"]["ok"]) else 0
            ps = r.get("path_source") or "MISSING"
            acc["paths"][ps] = acc["paths"].get(ps, 0) + 1
            yield _summary_proj(r)
    fd_sum = FD.summarize(_fd_rows())
    rh_sum = RH.match_stats(_summary_proj(r) for r in iter_journal(paths))
    # `_build.json` yalnız İÇERİĞİ özetler (aynı mühür + aynı girdiler → bayt-özdeş); çalıştırmaya özgü sayılar
    # (yeniden kurulan satır, yazılan ay, an) yalnız dönen özette
    content = {"schema": BUILD_SCHEMA, "journal_version": JOURNAL_VERSION, "store_seal": seal_d,
               "rows": acc["rows"], "books": {b: len(active[b]) for b in all_books},
               "restored_away_excluded": restored,
               "fidelity": fd_sum, "rehydrate": rh_sum,
               "owner_fields_ok": acc["owner_ok"],
               "fee_identity_violations": acc["fee_bad"],
               "r_check_failures": acc["r_bad"],
               "path_sources": dict(sorted(acc["paths"].items())),
               "data_moving": dict(sorted((src.moving if src is not None else {}).items()))}
    p = paths.journal_tj / "_build.json"
    data = (json.dumps(content, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if not (p.exists() and p.read_bytes() == data):
        paths.write_bytes(p, data)
    return {**content, "built_at": iso(now), "built_rows": n_built, "months_written": sorted(written),
            "removed": len(removed), "pending": len(pending)}


def _summary_proj(r: dict) -> dict:
    rh = r.get("rehydrate") or {}
    fd = r.get("fidelity") or {}
    return {"book": r.get("book"), "config_epoch": r.get("config_epoch"), "trade_key": r.get("trade_key"),
            "fidelity": {k: fd.get(k) for k in ("status", "reasons", "primary_reason", "delta_r")},
            "rehydrate": {"status": rh.get("status"), "reason": rh.get("reason")},
            "_owner_ok": owner_fields_ok(r), "fee_identity_ok": r.get("fee_identity_ok"), "r_check": r.get("r_check"),
            "path_source": r.get("path_source")}


class MonthWriter:
    """Ay dosyasının AKIŞLA yeniden yazımı, doğrusal maliyetle: yeni satırlar `flush_rows`'ta bir sıralı geçici parçaya
    (`.spill-*`, aynı klasör) dökülür; kapanışta eski dosyanın tutulan ve yeniden kurulmamış satırları + parçalar tek
    k-yollu birleşimle (`heapq.merge`, `sort_key`) yazılır. Bellekte en çok `flush_rows` yeni satır ve yeni anahtarlar
    kümesi durur. Çıktı deterministik gzip'tir (`write_gzip_stream` = `gzip_bytes` baytları); içerik aynıysa dosyaya
    dokunulmaz. Yarıda öldürülürse kalan `.spill-*` dosyaları bir sonraki çalıştırmada silinir (`clean_spills`)."""

    def __init__(self, paths: EnginePaths, path: Path, keep: Callable[[str], bool], sort_key: Callable[[dict], Any], *,
                 flush_rows: int = 1000) -> None:
        self.paths, self.path, self.keep, self.sort_key, self.flush_rows = paths, path, keep, sort_key, flush_rows
        self.buf: list[dict] = []
        self.keys: set[str] = set()
        self.spills: list[Path] = []

    def add(self, key: str, row: dict) -> None:
        if not self.keep(key):
            return
        self.keys.add(key)
        self.buf.append(row)
        if len(self.buf) >= self.flush_rows:
            self._spill()

    def _spill(self) -> None:
        if not self.buf:
            return
        rows = sorted(self.buf, key=self.sort_key)
        p = self.path.with_name(f".spill-{self.path.name[:-len('.jsonl.gz')]}-{len(self.spills):04d}.jsonl.gz")
        self.paths.write_gzip_stream(p, (json_line(r).encode("utf-8") for r in rows))
        self.spills.append(p)
        self.buf = []

    def close(self) -> bool:
        """Birleştir ve yaz. Dönen: dosya değişti mi."""
        mem = sorted(self.buf, key=self.sort_key)
        self.buf = []
        n = 0

        def old_rows() -> Iterator[dict]:
            if self.path.exists():
                for _ln, row in iter_gz_lines(self.path):
                    k = str(row.get("trade_key"))
                    if k not in self.keys and self.keep(k):
                        yield row

        def spill_rows(p: Path) -> Iterator[dict]:
            for _ln, row in iter_gz_lines(p):
                yield row

        def gen() -> Iterator[bytes]:
            nonlocal n
            for row in heapq.merge(old_rows(), *(spill_rows(p) for p in self.spills), iter(mem), key=self.sort_key):
                n += 1
                yield json_line(row).encode("utf-8")
        tmp = self.path.with_name("." + self.path.name + ".merge")
        try:
            digest = self.paths.write_gzip_stream(tmp, gen())
        finally:
            for p in self.spills:
                p.unlink(missing_ok=True)
            self.spills = []
        if n == 0:
            tmp.unlink()
            if self.path.exists():
                self.path.unlink()
                return True
            return False
        if self.path.exists() and sha256_file(self.path) == digest:
            tmp.unlink()
            return False
        os.replace(self.paths.require_research(tmp), self.paths.require_research(self.path))
        return True


def clean_spills(paths: EnginePaths, root: Path) -> None:
    """Yarıda kalmış birleşimlerin ve atomik yazımların geçici dosyaları (`.spill-*`, `.*.merge`, `.*.tmp` —
    `write_bytes`/`write_gzip_stream`'in `os.replace`'ten önce öldürülen geçici dosyası; 2026-10-08 inceleme küçüğü)."""
    if root.is_dir():
        for p in list(root.glob(".spill-*")) + list(root.glob(".*.merge")) + list(root.glob(".*.tmp")):
            paths.require_research(p).unlink(missing_ok=True)


def merge_sorted_month(paths: EnginePaths, path: Path, built: dict[str, dict], keep: Callable[[str], bool],
                       sort_key: Callable[[dict], Any]) -> bool:
    """Tek seferlik birleşim (`MonthWriter` ile): eski dosyanın tutulan satırları + `built`."""
    w = MonthWriter(paths, path, keep, sort_key, flush_rows=10**9)
    for k, r in built.items():
        w.add(k, r)
    return w.close()


def _write_month(paths: EnginePaths, month: str, built: dict[str, dict], new_index: dict[str, dict]) -> bool:
    """Günlük ay dosyası: hâlâ bu aya ait eski satırlar + yeni kurulanlar (`(closed_at, anahtar)` sırasında)."""
    return merge_sorted_month(paths, month_file(paths, month), built,
                              lambda k: k in new_index and new_index[k]["month"] == month, _sort_key)


def _count(xs: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def owner_fields_ok(row: dict) -> bool:
    """Kabul 2: sahibin her alanı (ve kalın gruplarının her alanı) ya doludur ya MISSING/MODELED/NOT_APPLICABLE etiketli."""
    fs = row.get("field_source") or {}
    names = set(OWNER_FIELDS) | {f for g in OWNER_GROUPS for f in FIELD_GROUPS[g]}
    for f in names:
        lab = fs.get(f)
        if lab not in LABELS:
            return False
        if lab in (MEASURED, RECONSTRUCTED) and row.get(f) is None:
            return False
    return True


# ============================================================================ uzlaştırma (kabul 1)
def _s1a_read_at(paths: EnginePaths, book: str) -> datetime | None:
    """Defterin SON S1a arşiv okumasının anı (`closes` çapası `observed_at`; P1a). Yoksa None."""
    from .closes import _read_anchor
    a = _read_anchor(paths, book)
    return parse_ts((a or {}).get("observed_at")) if a else None


def reconcile_journal(paths: EnginePaths, rows: Iterable[dict] | None = None) -> dict[str, Any]:
    """Günlük ↔ arşiv (son rev, ACTIVE) ↔ ledger (elde tutulan pencere): her kayıt günlükte TAM BİR KEZ; Σ net_pnl,
    ücret, fonlama ve kayma 1e-6 ile eşit. Dönen: {"status": OK|INCONSISTENT, books: {...}}.

    **Ledger kolu S1a OKUMASINA göredir (2026-10-08, inceleme M4).** Arşiv, S1a'nın ledger okumasının kopyasıdır
    (`closes.reconcile_records` o okumada arşiv = ledger'ı denetler); S1b'nin sonunda ledger yeniden okununca S1a'dan
    SONRA kapanan kayıt (henüz arşivde değil, `closed_at` > S1a okuması) ve S1a'dan sonra içeriği değişen kayıt (geç
    fonlama / geriye tarihli kapanış: içerik sha'sı arşivinkinden farklı ve ledger `updated_at` > S1a okuması) tutarsızlık
    DEĞİLDİR: sayılır (`after_s1a`, `revised_after_s1a`) ve ertesi gecenin S1a'sı arşive alır. Geri kalan kayıtlar birebir
    denetlenir; S1a okumasından önce kapanmış ama arşivde/günlükte olmayan kayıt hâlâ `INCONSISTENT`'tir."""
    from .closes import canonical_sha
    keep = ("trade_key", "book", "net_pnl", "fees_total", "funding_net", "slippage_cost")
    rows = list(rows) if rows is not None else [{k: r.get(k) for k in keep} for r in iter_journal(paths)]
    by_book: dict[str, list[dict]] = {}
    for r in rows:
        by_book.setdefault(str(r.get("book")), []).append(r)
    out: dict[str, Any] = {"books": {}, "status": "OK"}
    books = sorted(set(by_book) | ({p.name for p in paths.closes.iterdir() if p.is_dir()} if paths.closes.exists() else set()))
    for b in books:
        jr = by_book.get(b, [])
        keys = [str(r["trade_key"]) for r in jr]
        dup = len(keys) - len(set(keys))
        jmap = {str(r["trade_key"]): r for r in jr}
        arch = {k: v for k, v in latest_closes(paths, b).items() if v.status == ST_ACTIVE}
        sums = {}
        for name, jf, af in (("net_pnl", "net_pnl", "pnl"), ("fees", None, "fees"), ("funding", "funding_net", "funding"),
                             ("slippage", "slippage_cost", "slippage")):
            js = Decimal(0)
            for r in jr:
                js += dec(r.get("fees_total" if name == "fees" else jf))
            asum = sum((dec((v.proj or {}).get(af)) for v in arch.values()), Decimal(0))
            sums[name] = {"journal": dstr(js), "archive": dstr(asum), "ok": abs(js - asum) <= TOL}
        missing_in_journal = sorted(set(arch) - set(jmap))
        extra_in_journal = sorted(set(jmap) - set(arch))
        led: dict[str, Any] = {"held": None, "missing": None, "sums": None}
        found = find_ledgers(paths.state)
        if b in found:
            kind, p = found[b]
            rd = read_ledger(b, kind, p)
            if rd.ok:
                as_of = _s1a_read_at(paths, b)
                upd = parse_ts((rd.doc or {}).get("updated_at"))
                changed_after = bool(as_of is not None and upd is not None and upd > as_of)
                hist = [h for h in (rd.doc or {}).get("history") or [] if isinstance(h, dict)]
                lk, after, revised, miss = [], 0, 0, []
                for h in hist:
                    k = f"{b}|{h.get('id', '')}|{h.get('opened_at', '')}"
                    a = arch.get(k)
                    if a is None:
                        c = parse_ts(h.get("closed_at"))
                        if as_of is not None and c is not None and c > as_of:
                            after += 1                       # S1a okumasından SONRA kapandı: ertesi gece arşive girer
                            continue
                        miss.append(k)
                        continue
                    if changed_after and canonical_sha(h) != a.sha:
                        revised += 1                         # S1a'dan sonra içeriği değişti (geç fonlama vb.)
                        continue
                    lk.append((k, h))
                miss += [k for k, _h in lk if k not in jmap]
                lsum = {"net_pnl": sum((dec(h.get("pnl")) for _k, h in lk), Decimal(0)),
                        "fees": sum((dec(h.get("fees")) for _k, h in lk), Decimal(0)),
                        "funding": sum((dec(h.get("funding")) for _k, h in lk), Decimal(0)),
                        "slippage": sum((dec(h.get("slippage_cost")) for _k, h in lk), Decimal(0))}
                jsum = {"net_pnl": sum((dec(jmap[k].get("net_pnl")) for k, _h in lk if k in jmap), Decimal(0)),
                        "fees": sum((dec(jmap[k].get("fees_total")) for k, _h in lk if k in jmap), Decimal(0)),
                        "funding": sum((dec(jmap[k].get("funding_net")) for k, _h in lk if k in jmap), Decimal(0)),
                        "slippage": sum((dec(jmap[k].get("slippage_cost")) for k, _h in lk if k in jmap), Decimal(0))}
                led = {"held": len(hist), "compared": len(lk), "as_of": iso(as_of) if as_of else None, "after_s1a": after,
                       "revised_after_s1a": revised, "missing": miss[:20], "n_missing": len(miss),
                       "sums": {k: {"ledger": dstr(lsum[k]), "journal": dstr(jsum[k]), "ok": abs(lsum[k] - jsum[k]) <= TOL}
                                for k in lsum}}
            rd.doc = None
        ok = (not dup and not missing_in_journal and not extra_in_journal and all(s["ok"] for s in sums.values())
              and (led["sums"] is None or (not led["n_missing"] and all(s["ok"] for s in led["sums"].values()))))
        out["books"][b] = {"rows": len(jr), "archive_active": len(arch), "duplicates": dup,
                           "missing_in_journal": missing_in_journal[:20], "extra_in_journal": extra_in_journal[:20],
                           "sums": sums, "ledger": led, "ok": ok}
        if not ok:
            out["status"] = "INCONSISTENT"
    return out


# ============================================================================ S1b girişi (P2b gece aşamasına bağlar)
def open_store(paths: EnginePaths) -> tuple[PR.BarSource | None, dict[str, Any]]:
    """Mühürlü depo görünümü (`data_status.json` → `store/_seal/<mühür>.json.gz`); yoksa (None, neden)."""
    from .provider import DataMoving, SealedReader
    try:
        rd = SealedReader.from_status(paths)
    except DataMoving as exc:
        return None, {"status": "NO_SEAL", "reason": exc.reason}
    return PR.BarSource(rd), {"status": "OK", "data_seal": rd.seal}


def run_s1b(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None,
            app_dir: Path | str | None = None, budget_s: float | None = None) -> dict[str, Any]:
    """S1b (§6.1): günlük + yol + rehydrate + fidelity, ardından tembel 1m ihtiyaç dosyası (gece aşaması `night._s1b`).
    `budget_s`: gece son tarihine kalan süre payı (kaldığı yerden devam, `build_journal`)."""
    from .rawconfig import read_raw_config
    now = now or utc_now()
    src, sinfo = open_store(paths)
    if src is not None:
        src.max_parts = min(int(src.max_parts), S1B_MAX_PARTS)        # bellek bütçesi (§2.5); sonuç değişmez
    rc = read_raw_config(Path(app_dir) / "config.yaml") if app_dir is not None else read_raw_config()
    summary = build_journal(paths, now=now, src=src, rawconfig=rc, budget_s=budget_s)
    items = []
    for r in iter_journal(paths):
        if (r.get("path") or {}).get("tf") == "1m" and (r.get("path") or {}).get("complete"):
            continue
        o, c = parse_ts(r.get("opened_at")), parse_ts(r.get("closed_at"))
        if o is None or c is None:
            continue
        items.append({"market": "spot" if r.get("kind") == KIND_SPOT else "futures", "symbol": r.get("symbol"),
                      "opened_ms": _ms(o), "closed_ms": _ms(c)})
    needs = PR.plan_needs(items, now=now, seal=(sinfo or {}).get("data_seal"))
    PR.write_needs(paths, needs)
    if summary.get("pending"):
        recon: dict[str, Any] = {"status": "PARTIAL_BUILD", "pending": summary.get("pending")}
    else:
        rc_full = reconcile_journal(paths)
        recon = {"status": rc_full["status"], "books_not_ok": sorted(b for b, x in rc_full["books"].items() if not x["ok"])}
    return {"stage": "S1b", "run_id": run_id, "store": sinfo, "reconcile": recon, "journal": {k: summary.get(k) for k in (
        "rows", "built_rows", "pending", "months_written", "restored_away_excluded", "owner_fields_ok",
        "fee_identity_violations", "r_check_failures", "path_sources", "data_moving")}, "fidelity": {k: summary["fidelity"].get(k) for k in (
            "eligible", "ok", "rate", "gate_pass", "failures_by_reason", "not_eligible")},
        "needs_1m": {"days_total": needs["days_total"], "capped": needs["capped"], "series": len(needs["series"])}}


__all__ = ["ALL_FIELDS", "BOOK_TACTIC", "BuildContext", "FIELD_GROUPS", "JOURNAL_SCHEMA", "JOURNAL_VERSION", "LABELS", "OWNER_FIELDS",
           "OWNER_GROUPS", "SideSources", "build_journal", "build_row", "entry_context", "iter_journal", "month_file",
           "open_store", "owner_fields_ok", "reconcile_journal", "run_s1b", "scan_archive", "session_of", "situation_at",
           "spot_buy_order"]

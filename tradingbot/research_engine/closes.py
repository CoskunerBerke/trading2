"""S1a — kapanış arşivi, cüzdan hareketi arşivi, gecelik ölçülmüş anlık görüntü ve uzlaştırma (§4.2, §3.6, §10 P1a).

Kaynaklar (hepsi `ledgers.read_ledger` ile salt-okunur): her vadeli defterin `futures_ledger.json`'ı ve ana botun
`state/spot_ledger.json`'ı (`main_spot`). Çıktılar yalnız `data/research` altındadır:

* `closes/<defter>/YYYY-MM.jsonl.gz` — her kapanmış kaydın BİREBİR kopyası (`row="close"`, alan `record`) + küçük bir
  para izdüşümü (`proj`; okuyucular tam kaydı yeniden çözmeden toplar). Satırlar yalnız eklenir. Kayıt anahtarı
  `trade_key = defter|trade_id|opened_at`. Bir kaydın içeriği değişirse (geç fonlama, geriye tarihli kapanış) aynı
  anahtarla `rev+1` satırı eklenir. Ayrıca kayıt başına bir kez `row="derived"` satırı (`closes_derived`:
  `min_to_next_funding`, `decision_delay_s`); ham kayıt birebir kalır, türetilmiş alanlar ayrı satırdadır.
* `entries/<defter>/YYYY-MM.jsonl.gz` — `entries[]` cüzdan hareketleri (FEE, PNL, FUNDING — "late funding" ve
  "funding reversal" dahil —, TRANSFER, LIQ_FEE, TAX, SLIPPAGE_INFO), her biri ilk görüldüğü okumanın `observed_at`
  damgasıyla. Anahtar `sha256(defter|ts|kind|ref_id|amount|note)#tekrar`; yeniden çalıştırma 0 satır ekler.
* `snapshots/YYYY-MM-DD.json.gz` — gecelik ÖLÇÜLMÜŞ anlık görüntü (`MEASURED`): vadeli `wallet_balance` + açık
  pozisyonlar (miktar, giriş, `last_price`, gerçekleşmemiş), spot `cash`/`locked_cash`/`assets`/lotlar + mark kaynağı ve
  yaşı, ledger `seq`/`updated_at`, okuma zamanı; ayrıca o pencerenin uzlaştırması, rotasyon payı ve geri yükleme izi.

Okumalar ve belge ile kodun karşılaştırılması (belgenin niyetine göre; ayrıntı ilgili fonksiyonlarda):

1. **Segment ayı = satırın arşive yazıldığı (gözlendiği) UTC ayıdır**, kaydın kapanış ayı değil. Böylece geçmiş aylar
   gerçekten mühürlü kalır; geç gelen revizyon yeni aya eklenir. Kapanış günü her satırda `record.closed_at`'tedir.
2. **"Son 14 günde kapananlar her gece yeniden eşitlenir"** — motor, ledger'ın ELİNDE TUTTUĞU bütün kayıtları her gece
   karşılaştırır (14 günü kapsayan bir üst küme; 5000 kayıtta milisaniyeler). 14 günden eski bir kayıtta değişiklik
   görülürse ayrıca sayılır (`revised_older_than_resync`).
3. **Spot cüzdan eşitliği.** `SpotLedger` alışta `TRANSFER(−notional)` + `FEE`, satışta `PNL(hasılat − maliyet)` + `FEE`
   yazar; satışta nakde dönen MALİYET bir hareket olarak yazılmaz. Bu yüzden belgedeki "spot `cash` farkı = hareketler"
   eşitliği kodda şu biçimde doğrudur ve öyle denetlenir: Δ(`cash` + `locked_cash`) = Σ gözlenen hareketler +
   Σ o pencerede ilk gözlenen satış kayıtlarının maliyeti (`effective_notional`). Ayrıca defter değeri
   B = `cash` + `locked_cash` + Σ lot × maliyet için ΔB = Σ (TRANSFER dışı) hareketler denetlenir.
4. **Spot TRANSFER bir para yatırma değil, alış dönüşümüdür** (nakit → varlık); vadeli defterler bugün hiç TRANSFER
   yazmaz. P&L'e hiçbiri girmez; vadeli TRANSFER (olursa) özsermaye düzeltmesidir.
5. **Rotasyon payı** (§4.2, --check): `history_keep=5000`, `entries_keep=2000` (accounting varsayılanları; JSON'a
   yazılmaz). Gecelik arşivde ilk arşivlenmemiş öğe, ledger'a `keep − (son arşivden beri yeni öğe)` öğe daha eklenince
   düşer; "rotasyona kalan kayıt" bu sayıdır, gün karşılığı son 7 günün ortalama hızına bölümüdür. 3 günün altı UYARI.
6. **Geri yükleme** (§3.6): ledger `seq`'inin bir önceki anlık görüntüye göre azalması VEYA veri kökünde o anlık
   görüntüden yeni bir `state.pre-restore-*` klasörü; hangisinin görüldüğü kayda yazılır. O zaman ledger'da artık
   bulunmayan (ve rotasyonla açıklanamayan) arşiv kayıtları `RESTORED_AWAY` satırı alır (silinmez), pencere
   `RESTORED` olur ve `INCONSISTENT` verilmez. Kanıt yokken kaybolan kayıt `INCONSISTENT`tir.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import ENGINE_VERSION
from .ledgers import (
    BOOK_MAIN_FUT,
    KIND_FUTURES,
    KIND_SPOT,
    LedgerRead,
    dec,
    dec_or_none,
    dstr,
    find_ledgers,
    iso,
    parse_ts,
    read_ledger,
    symbol_key,
    utc_now,
)
from .paths import EnginePaths, gzip_bytes, json_line, read_json_gz, read_jsonl_gz

CLOSES_SCHEMA = "closes_v1"
DERIVED_SCHEMA = "closes_derived_v1"
ENTRIES_SCHEMA = "entries_v1"
SNAPSHOT_SCHEMA = "snapshot_v1"

#: accounting varsayılanları (futures_ledger.py / spot_ledger.py `history_keep` / `entries_keep`).
HISTORY_KEEP = 5000
ENTRIES_KEEP = 2000
#: hizalama penceresi (§4.2): arşivin son 50 hareketi ledger listesinde aranır.
ALIGN_TAIL = 50
RESYNC_DAYS = 14
MARGIN_WARN_DAYS = 3.0
RATE_WINDOW_DAYS = 7
#: uzlaştırma toleransı (USDT).
TOL = Decimal("1e-6")
#: spot mark kaynakları (§7.1): position_path ≤ 60 dk; HistoryStore son kapanmış bar (≤ 24 saat, yaşı yazılır).
POSITION_PATH_MAX_AGE = timedelta(minutes=60)
POSITION_PATH_FUTURE_TOL = timedelta(minutes=2)
HISTORY_BAR_MAX_AGE = timedelta(hours=24)
POSITION_PATH_TAIL_BYTES = 4 << 20

ST_ACTIVE, ST_RESTORED_AWAY = "ACTIVE", "RESTORED_AWAY"
#: hareket hizalama sonuçları
ALIGN_OK, ALIGN_BASELINE, ALIGN_GAP, ALIGN_REWIND = "OK", "BASELINE", "ENTRIES_GAP", "ENTRIES_REWIND"
#: cüzdan uzlaştırma sonuçları
RECON_OK, RECON_INCONSISTENT, RECON_GAP, RECON_RESTORED, RECON_NO_PREV = "OK", "INCONSISTENT", "ENTRIES_GAP", "RESTORED", "NO_PREV"
#: bayraklar
F_INCONSISTENT, F_RESTORE_EVENT, F_ENTRIES_GAP, F_ROTATION_WARN = "INCONSISTENT", "RESTORE_EVENT", "ENTRIES_GAP", "ROTATION_MARGIN_LOW"
#: ledger'ın en eski kaydı bu gece ilk kez görüldü ve arşivde önceki kayıtlar vardı: iki gece arasında > 5000 kapanış;
#: aradaki kayıtlar rotasyonla arşive hiç girmeden düşmüş olabilir.
F_HISTORY_GAP = "HISTORY_GAP"
#: mark kaynakları
MARK_LEDGER, MARK_LEDGER_ENTRY = "LEDGER_MARK", "LEDGER_ENTRY_NO_MARK"
MARK_POSITION_PATH, MARK_HISTORY_BAR, MARK_PERP_PROXY = "POSITION_PATH", "HISTORY_SPOT_BAR", "PERP_PROXY"
#: P&L sayılan hareket türleri (§7.1 cüzdan görünümü); TRANSFER ayrı tutulur, SLIPPAGE_INFO bilgi amaçlıdır.
PNL_KINDS = ("PNL", "FEE", "FUNDING", "LIQ_FEE", "TAX")
PRE_RESTORE_PREFIX = "state.pre-restore-"


# ============================================================================ kimlikler ve izdüşüm
def canonical_sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def trade_key(book: str, rec: dict) -> str:
    return f"{book}|{rec.get('id', '')}|{rec.get('opened_at', '')}"


def position_group(book: str, rec: dict, kind: str) -> str:
    """Spot kısmi satışları aynı gruba düşer: `main_spot|sembol|opened_at` (§4.2). Vadeli: kaydın kendi anahtarı."""
    if kind == KIND_SPOT:
        return f"{book}|{rec.get('symbol', '')}|{rec.get('opened_at', '')}"
    return trade_key(book, rec)


def entry_base(book: str, e: dict) -> str:
    raw = "|".join(str(x) for x in (book, e.get("ts", ""), e.get("kind", ""), e.get("ref_id", ""), e.get("amount", ""),
                                    e.get("note", "")))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def project(rec: dict, kind: str) -> dict[str, Any]:
    """Kaydın para izdüşümü (okuyucular için). Değerler kayıpsız string; R yalnız `features.risk_usdt` > 0 ise."""
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    risk = dec_or_none(feats.get("risk_usdt")) if kind == KIND_FUTURES else None
    r = dec_or_none(rec.get("r_multiple")) if (risk is not None and risk > 0) else None
    if kind == KIND_SPOT:
        cov: Any = "NOT_APPLICABLE"
    else:
        c = feats.get("funding_coverage")
        cov = c.get("complete") if isinstance(c, dict) and isinstance(c.get("complete"), bool) else None
    fees, ef, xf = dec(rec.get("fees")), dec(rec.get("entry_fee")), dec(rec.get("exit_fee"))
    return {"id": str(rec.get("id", "")), "symbol": str(rec.get("symbol", "")), "inst": symbol_key(rec.get("symbol")),
            "side": str(rec.get("side", "")), "opened_at": str(rec.get("opened_at", "")), "closed_at": str(rec.get("closed_at", "")),
            "venue": "spot" if kind == KIND_SPOT else "UM_futures",
            "pnl": dstr(dec(rec.get("pnl"))), "net_pnl": dstr(dec(rec.get("net_pnl"))), "gross": dstr(dec(rec.get("gross_pnl"))),
            "fees": dstr(fees), "entry_fee": dstr(ef), "exit_fee": dstr(xf),
            "funding": dstr(dec(rec.get("funding"))) if kind == KIND_FUTURES else None,
            "funding_paid": dstr(dec(rec.get("funding_paid"))) if kind == KIND_FUTURES else None,
            "funding_received": dstr(dec(rec.get("funding_received"))) if kind == KIND_FUTURES else None,
            "slippage": dstr(dec(rec.get("slippage_cost"))), "r": dstr(r), "risk_usdt": dstr(risk),
            "leverage": 1 if kind == KIND_SPOT else rec.get("leverage"),
            "cost": dstr(dec(rec.get("effective_notional"))) if kind == KIND_SPOT else None,
            "funding_complete": cov, "fee_identity_ok": abs(fees - (ef + xf)) <= TOL,
            "setup_type": str(rec.get("setup_type", "")), "exit_reason": str(rec.get("exit_reason", ""))}


def min_to_next_funding(rec: dict, kind: str) -> tuple[int | None, str]:
    """Açılış anından sonraki ilk fonlama uzlaşmasına dakika (kayıttaki `funding_hours_utc`'den)."""
    if kind == KIND_SPOT:
        return None, "NOT_APPLICABLE"
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    hours = feats.get("funding_hours_utc")
    opened = parse_ts(rec.get("opened_at"))
    try:
        hs = sorted({int(h) for h in hours}) if isinstance(hours, list) else []
    except (TypeError, ValueError):
        hs = []
    if not hs or opened is None:
        return None, "MISSING"
    base = opened.replace(hour=0, minute=0, second=0, microsecond=0)
    cands = [base + timedelta(days=d, hours=h) for d in (0, 1) for h in hs]
    nxt = min(c for c in cands if c > opened)
    return int((nxt - opened).total_seconds() // 60), "MEASURED"


_DECISION_TS_KEYS = ("decision_ts", "entry_decision_ts", "decided_at")


def _provenance_decisions(state: Path, ids: set[str]) -> dict[str, datetime]:
    """`entry_provenance.jsonl`'dan karar zamanı (yalnız alan varsa). Bugünkü şema (`entry_provenance_v1`) karar
    zamanı taşımaz (yalnız `recorded_at`, açılıştan SONRA) → çoğunlukla boş döner ve `decision_delay_s` MISSING kalır."""
    out: dict[str, datetime] = {}
    p = state / "entry_provenance.jsonl"
    if not ids or not p.exists():
        return out
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                tid = str(row.get("trade_id", "")) if isinstance(row, dict) else ""
                if tid not in ids:
                    continue
                for k in _DECISION_TS_KEYS:
                    t = parse_ts(row.get(k))
                    if t is not None:
                        out[tid] = t
                        break
    except OSError:
        return out
    return out


# ============================================================================ arşiv okuma
@dataclass
class CloseState:
    rev: int
    sha: str
    status: str
    ord: int
    proj: dict
    first_observed_at: str
    observed_at: str


@dataclass
class BookArchive:
    """Bir defterin arşivinin bellekteki özeti (segmentlerden akışla kurulur; tam kayıtlar tutulmaz)."""
    book: str
    closes: dict[str, CloseState] = field(default_factory=dict)
    max_ord: int = 0
    close_rows: int = 0
    derived_keys: set[str] = field(default_factory=set)
    entries_count: int = 0
    entries_tail: list[str] = field(default_factory=list)
    base_counts: Counter = field(default_factory=Counter)     # yalnız ts filtresindeki tabanlar
    first_entry_ts: datetime | None = None
    window_entry_sum: Decimal = Decimal(0)                     # (window_from, ∞) içinde gözlenen hareketler
    window_entry_nontransfer_sum: Decimal = Decimal(0)
    window_new_close_cost: Decimal = Decimal(0)                # spot: penceredeki rev-0 satışların maliyeti
    refmap: dict[str, str] = field(default_factory=dict)        # ref_id → sembol


def segment_files(d: Path) -> list[Path]:
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.jsonl.gz") if len(p.name) == len("YYYY-MM.jsonl.gz"))


def iter_rows(d: Path) -> Iterator[dict]:
    for seg in segment_files(d):
        yield from read_jsonl_gz(seg)


def load_book_archive(paths: EnginePaths, book: str, *, ts_filter: set[str] | None = None,
                      window_from: datetime | None = None, include_entries: bool = True) -> BookArchive:
    """Arşivi akışla oku. `ts_filter`: tekrar sayımı yalnız bu `ts` değerlerindeki tabanlar için tutulur (hafıza).
    `window_from`: bu andan SONRA gözlenen hareket/kapanış satırlarının toplamı (cüzdan uzlaştırması için)."""
    a = BookArchive(book=book)
    for row in iter_rows(paths.book_closes_dir(book)):
        if row.get("row") == "derived":
            a.derived_keys.add(str(row.get("trade_key")))
            continue
        k = str(row.get("trade_key"))
        a.close_rows += 1
        prev = a.closes.get(k)
        st = CloseState(rev=int(row.get("rev", 0)), sha=str(row.get("content_sha", "")), status=str(row.get("status", ST_ACTIVE)),
                        ord=int(row.get("ord", 0)), proj=row.get("proj") or (prev.proj if prev else {}),
                        first_observed_at=prev.first_observed_at if prev else str(row.get("observed_at", "")),
                        observed_at=str(row.get("observed_at", "")))
        a.closes[k] = st
        a.max_ord = max(a.max_ord, st.ord)
        if window_from is not None and prev is None:
            obs = parse_ts(row.get("observed_at"))
            if obs is not None and obs > window_from and st.proj.get("cost") is not None:
                a.window_new_close_cost += dec(st.proj.get("cost"))
    for row in (iter_rows(paths.book_entries_dir(book)) if include_entries else ()):
        e = row.get("entry") if isinstance(row.get("entry"), dict) else {}
        base = str(row.get("key", "")).split("#", 1)[0]
        a.entries_count += 1
        a.entries_tail.append(base)
        if len(a.entries_tail) > ALIGN_TAIL:
            del a.entries_tail[0]
        if ts_filter is None or str(e.get("ts", "")) in ts_filter:
            a.base_counts[base] += 1
        t = parse_ts(e.get("ts"))
        if t is not None and (a.first_entry_ts is None or t < a.first_entry_ts):
            a.first_entry_ts = t
        if row.get("symbol"):
            a.refmap[str(e.get("ref_id", ""))] = str(row.get("symbol"))
        if window_from is not None:
            obs = parse_ts(row.get("observed_at"))
            if obs is not None and obs > window_from and not row.get("pre_archive"):
                amt = dec(e.get("amount"))
                a.window_entry_sum += amt
                if str(e.get("kind")) != "TRANSFER":
                    a.window_entry_nontransfer_sum += amt
    return a


def latest_closes(paths: EnginePaths, book: str) -> dict[str, CloseState]:
    return load_book_archive(paths, book, include_entries=False).closes


def archived_books(paths: EnginePaths) -> list[str]:
    books = set()
    for root in (paths.closes, paths.entries):
        if root.exists():
            books.update(p.name for p in root.iterdir() if p.is_dir())
    return sorted(books)


# ============================================================================ anlık görüntüler
def snapshot_days(paths: EnginePaths) -> list[str]:
    if not paths.snapshots.exists():
        return []
    return sorted(p.name[:10] for p in paths.snapshots.glob("????-??-??.json.gz"))


def load_snapshot(paths: EnginePaths, day: str) -> dict | None:
    p = paths.snapshot_file(day)
    if not p.exists():
        return None
    return read_json_gz(p)


def load_snapshots(paths: EnginePaths) -> list[dict]:
    out = []
    for d in snapshot_days(paths):
        s = load_snapshot(paths, d)
        if isinstance(s, dict):
            out.append(s)
    return out


# ============================================================================ spot mark kaynakları (§7.1)
def _position_path_marks(state: Path, ref: datetime) -> dict[str, tuple[Decimal, datetime]]:
    """`position_path.jsonl` kuyruğundan sembol başına en son mark (ref − 60 dk … ref + 2 dk). Dosya salt-okunur."""
    p = state / "position_path.jsonl"
    out: dict[str, tuple[Decimal, datetime]] = {}
    if not p.exists():
        return out
    try:
        with open(p, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - POSITION_PATH_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return out
    lines = tail.splitlines()
    if size > POSITION_PATH_TAIL_BYTES and lines:
        lines = lines[1:]                      # ilk satır yarım olabilir
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        t, m = parse_ts(row.get("ts")), dec_or_none(row.get("mark"))
        sym = symbol_key(row.get("symbol"))
        if t is None or m is None or m <= 0 or not sym:
            continue
        if not (ref - POSITION_PATH_MAX_AGE <= t <= ref + POSITION_PATH_FUTURE_TOL):
            continue
        if sym not in out or t >= out[sym][1]:
            out[sym] = (m, t)
    return out


_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def history_series_names(symbol: str) -> list[str]:
    """HistoryStore klasör adı adayları, sabit sırayla: `symbol_safe` ('ETH/USDT' → 'ETH_USDT'), sonra 'ETHUSDT'."""
    out = []
    for n in (str(symbol).replace("/", "_").replace(":", "-"), symbol_key(symbol)):
        if n and n not in out:
            out.append(n)
    return out


def _history_spot_bar(market: Path, symbol: str, ref: datetime) -> tuple[Decimal, datetime] | None:
    """Worker HistoryStore'unda (`market/history/spot/<ad>/<tf>/`, salt-okunur) son KAPANMIŞ spot barı (§7.1 kaynak 2).
    Mühürlü kural (betik bağımsız olarak aynısını uygular): ad adayları `history_series_names` sırasıyla, tf
    1m→1d sırasıyla; her seride `timestamp + tf ≤ ref` ve `ref − (timestamp + tf) ≤ 24 saat` olan SON bar; adaylar
    arasında kapanışı en yeni olan (eşitlikte ilk aday). pandas yalnız seri klasörü varsa import edilir."""
    root = market / "history" / "spot"
    if not root.exists():
        return None
    ref_ms = int(ref.timestamp() * 1000)
    max_age = int(HISTORY_BAR_MAX_AGE.total_seconds() * 1000)
    best: tuple[int, Decimal] | None = None
    for name in history_series_names(symbol):
        for tf, step in _TF_MS.items():
            d = root / name / tf
            if not d.is_dir():
                continue
            parts = sorted(p for p in d.glob("*/*") if p.suffix in (".parquet", ".gz"))[-2:]
            if not parts:
                continue
            try:
                import pandas as pd  # noqa: PLC0415 — yalnız gerektiğinde
                frames = [pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p) for p in parts]
                df = pd.concat(frames, ignore_index=True)
                ts = df["timestamp"].astype("int64")
                ct = ts + step
                df = df[(ct <= ref_ms) & (ref_ms - ct <= max_age)]
                if df.empty:
                    continue
                df = df.assign(_ct=df["timestamp"].astype("int64") + step).sort_values("_ct")
                row = df.iloc[-1]
                price = dec_or_none(str(row["close"]))
            except Exception:  # noqa: BLE001 — okunamayan seri atlanır, bir sonraki kaynağa geçilir
                continue
            if price is None or price <= 0:
                continue
            if best is None or int(row["_ct"]) > best[0]:
                best = (int(row["_ct"]), price)
    if best is None:
        return None
    return best[1], datetime.fromtimestamp(best[0] / 1000, tz=timezone.utc)


def spot_marks(paths: EnginePaths, symbols: Iterable[str], ref: datetime,
               futures_docs: dict[str, dict]) -> dict[str, dict[str, Any]]:
    """Spot varlıkların mark'ı, mühürlü sırayla (§7.1): (1) `position_path.jsonl` ≤ 60 dk, (2) worker HistoryStore'unda
    son kapanmış spot barı, (3) aynı sembolün herhangi bir vadeli ledger'daki `last_price`'ı (`PERP_PROXY`, en yeni
    `updated_at`). Hiçbiri yoksa mark yoktur (o defterin MTM'i EKSİK)."""
    syms = sorted({str(s) for s in symbols if s})
    out: dict[str, dict[str, Any]] = {}
    if not syms:
        return out
    pp = _position_path_marks(paths.state, ref)
    proxies: dict[str, tuple[Decimal, datetime | None]] = {}
    for _b, doc in sorted(futures_docs.items()):
        upd = parse_ts(doc.get("updated_at"))
        for p in (doc.get("positions") or {}).values():
            if not isinstance(p, dict):
                continue
            lp = dec_or_none(p.get("last_price"))
            k = symbol_key(p.get("symbol"))
            if lp is None or lp <= 0 or not k:
                continue
            cur = proxies.get(k)
            if cur is None or (upd is not None and (cur[1] is None or upd > cur[1])):
                proxies[k] = (lp, upd)
    for s in syms:
        k = symbol_key(s)
        if k in pp:
            m, t = pp[k]
            out[s] = {"price": dstr(m), "source": MARK_POSITION_PATH, "age_s": round((ref - t).total_seconds(), 1)}
            continue
        hb = _history_spot_bar(paths.market, s, ref)
        if hb is not None:
            out[s] = {"price": dstr(hb[0]), "source": MARK_HISTORY_BAR, "age_s": round((ref - hb[1]).total_seconds(), 1)}
            continue
        if k in proxies:
            m, t = proxies[k]
            out[s] = {"price": dstr(m), "source": MARK_PERP_PROXY,
                      "age_s": round((ref - t).total_seconds(), 1) if t is not None else None}
            continue
        out[s] = {"price": None, "source": "MISSING", "age_s": None}
    return out


# ============================================================================ defter durumu (anlık görüntü gövdesi)
def futures_state(doc: dict, read_at: datetime) -> dict[str, Any]:
    """Vadeli defterin ölçülmüş durumu; gerçekleşmemiş = ledger'ın KENDİ `last_price`'ı (`LEDGER_MARK`), yoksa
    ledger'ın kendi kuralıyla giriş fiyatı (0; kaynak `LEDGER_ENTRY_NO_MARK` olarak yazılır)."""
    wallet = dec(doc.get("wallet_balance"))
    positions, unreal, by_sym = [], Decimal(0), {}
    for sym, p in sorted((doc.get("positions") or {}).items()):
        if not isinstance(p, dict):
            continue
        qty, entry = dec(p.get("qty")), dec(p.get("entry_avg"))
        lp = dec_or_none(p.get("last_price"))
        src = MARK_LEDGER if lp is not None and lp > 0 else MARK_LEDGER_ENTRY
        px = lp if src == MARK_LEDGER else entry
        sign = -1 if str(p.get("side", "LONG")).upper() in ("SHORT", "SELL") else 1
        u = (px - entry) * qty * sign
        unreal += u
        k = symbol_key(p.get("symbol") or sym)
        by_sym[k] = by_sym.get(k, Decimal(0)) + u
        positions.append({"symbol": str(p.get("symbol") or sym), "id": str(p.get("id", "")), "side": str(p.get("side", "")),
                          "qty": dstr(qty), "entry": dstr(entry), "last_price": dstr(lp), "unrealized": dstr(u),
                          "opened_at": str(p.get("opened_at", "")), "mark_source": src})
    upd = parse_ts(doc.get("updated_at"))
    return {"kind": KIND_FUTURES, "seq": doc.get("seq"), "updated_at": doc.get("updated_at"),
            "starting_equity": dstr(dec(doc.get("starting_equity"))), "wallet_balance": dstr(wallet),
            "n_history": len(doc.get("history") or []), "n_entries": len(doc.get("entries") or []),
            "positions": positions, "unrealized": dstr(unreal), "unrealized_by_inst": {k: dstr(v) for k, v in sorted(by_sym.items())},
            "equity": dstr(wallet + unreal), "mark_source": MARK_LEDGER,
            "mark_age_s": round((read_at - upd).total_seconds(), 1) if upd else None, "mtm_complete": True}


def spot_base(symbol: str, quote: str = "USDT") -> str:
    """'ETH/USDT' → 'ETH'; 'ETHUSDT' → 'ETH' (spot_ledger.split_symbol ile aynı kural, yalnız taban)."""
    s = str(symbol).upper()
    if "/" in s:
        return s.split("/", 1)[0]
    k = symbol_key(s)
    return k[: -len(quote)] if k.endswith(quote) and len(k) > len(quote) else k


def spot_state(doc: dict, marks: dict[str, dict[str, Any]], read_at: datetime) -> dict[str, Any]:
    """Spot defterin ölçülmüş durumu. B = cash + locked_cash + Σ lot × maliyet; E = cash + locked_cash + Σ varlık × mark;
    U = E − B (brüt, tahmini çıkış ücreti düşülmeden — vadeli `LEDGER_MARK` ile aynı tanım)."""
    quote = str(doc.get("quote_asset") or "USDT").upper()
    cash, locked = dec(doc.get("cash")), dec(doc.get("locked_cash"))
    lots = doc.get("lots") or {}
    lot_sum: dict[str, dict[str, Decimal]] = {}
    for sym, ls in sorted(lots.items()):
        q = sum((dec(x.get("qty")) for x in (ls or []) if isinstance(x, dict)), Decimal(0))
        c = sum((dec(x.get("qty")) * dec(x.get("cost_basis")) for x in (ls or []) if isinstance(x, dict)), Decimal(0))
        lot_sum[sym] = {"qty": q, "cost": c}
    assets: dict[str, Decimal] = {}
    for src in (doc.get("assets") or {}, doc.get("locked_assets") or {}):
        for a, v in src.items():
            assets[str(a).upper()] = assets.get(str(a).upper(), Decimal(0)) + dec(v)
    base_to_sym = {spot_base(sym, quote): sym for sym in lot_sum}
    held: dict[str, Decimal] = {}
    for a, q in assets.items():
        if q == 0:
            continue
        held[base_to_sym.get(a, f"{a}/{quote}")] = q
    book_value = cash + locked + sum((v["cost"] for v in lot_sum.values()), Decimal(0))
    equity, complete, by_sym = cash + locked, True, {}
    mark_rows = {}
    for sym, q in sorted(held.items()):
        m = marks.get(sym) or {"price": None, "source": "MISSING", "age_s": None}
        mark_rows[sym] = m
        px = dec_or_none(m.get("price"))
        if px is None:
            complete = False
            continue
        equity += q * px
        by_sym[symbol_key(sym)] = q * px - lot_sum.get(sym, {}).get("cost", Decimal(0))
    unreal = (equity - book_value) if complete else None
    return {"kind": KIND_SPOT, "seq": doc.get("seq"), "updated_at": doc.get("updated_at"),
            "starting_equity": dstr(dec(doc.get("starting_equity"))), "cash": dstr(cash), "locked_cash": dstr(locked),
            "assets": {a: dstr(v) for a, v in sorted(assets.items()) if v != 0},
            "lots": {s: {"qty": dstr(v["qty"]), "cost": dstr(v["cost"])} for s, v in lot_sum.items()},
            "held": {s: dstr(q) for s, q in held.items()}, "marks": mark_rows,
            "n_history": len(doc.get("history") or []), "n_entries": len(doc.get("entries") or []),
            "book_value": dstr(book_value), "unrealized": dstr(unreal),
            "unrealized_by_inst": {k: dstr(v) for k, v in sorted(by_sym.items())} if complete else {},
            "equity": dstr(equity) if complete else None, "mark_source": "SPOT_MARK_CHAIN", "mtm_complete": complete,
            "read_at": iso(read_at)}


# ============================================================================ rotasyon payı
def rotation_margin(times: list[datetime | None], *, keep: int, new_since_last: int, ref: datetime) -> dict[str, Any]:
    """Rotasyon payı (okuma 5, modül başı): kalan öğe = keep − son arşivden beri yeni öğe; gün = kalan / günlük hız
    (son 7 gün; ledger 7 günü kapsamıyorsa ve doluysa elde tutulan aralığın hızı)."""
    ts = [t for t in times if t is not None]
    held = len(times)
    since = ref - timedelta(days=RATE_WINDOW_DAYS)
    n7 = sum(1 for t in ts if since < t <= ref)
    oldest = min(ts) if ts else None
    if oldest is not None and oldest > since and held >= keep:
        span = max((ref - oldest).total_seconds() / 86400.0, 1e-6)
        rate = held / span
    else:
        rate = n7 / RATE_WINDOW_DAYS
    remaining = keep - int(new_since_last)
    days = (remaining / rate) if rate > 0 else None
    return {"keep": keep, "held": held, "full": held >= keep, "new_since_last_archive": int(new_since_last),
            "remaining_items": remaining, "rate_per_day": round(rate, 3), "days": round(days, 2) if days is not None else None,
            "warn": bool(days is not None and days < MARGIN_WARN_DAYS) or remaining <= 0}


def rotation_status(paths: EnginePaths, *, now: datetime | None = None) -> dict[str, Any]:
    """Arşivlemeden, salt-okunur rotasyon payı (§4.2 --check; §2.9 A/B: herhangi bir defterin payı 3 günden azsa KAPALI
    gecede de S1a çalışır → `AB_OFF_ZORUNLU_ARŞİV`). "Son arşivden beri yeni öğe" son anlık görüntünün okuma anından
    sonraki kapanış/hareket sayısıyla yaklaşıklanır (zaman damgası; arşiv okunmaz)."""
    now = now or utc_now()
    days = snapshot_days(paths)
    last = (load_snapshot(paths, days[-1]) or {}).get("books", {}) if days else {}
    books: dict[str, Any] = {}
    min_days: float | None = None
    for b, (kind, p) in find_ledgers(paths.state).items():
        rd = read_ledger(b, kind, p, clock=lambda: now)
        if not rd.ok:
            books[b] = {"status": rd.status}
            continue
        doc = rd.doc or {}
        since = parse_ts((last.get(b) or {}).get("read_at"))
        h_t = [parse_ts(h.get("closed_at")) for h in doc.get("history") or [] if isinstance(h, dict)]
        e_t = [parse_ts(e.get("ts")) for e in doc.get("entries") or [] if isinstance(e, dict)]
        new_h = sum(1 for t in h_t if t is not None and since is not None and t > since) if since else len(h_t)
        new_e = sum(1 for t in e_t if t is not None and since is not None and t > since) if since else 0
        m = {"history": rotation_margin(h_t, keep=HISTORY_KEEP, new_since_last=new_h, ref=now),
             "entries": rotation_margin(e_t, keep=ENTRIES_KEEP, new_since_last=new_e, ref=now)}
        books[b] = {"status": "OK", **m}
        for x in m.values():
            if x["days"] is not None:
                min_days = x["days"] if min_days is None else min(min_days, x["days"])
    warn = any(v.get("history", {}).get("warn") or v.get("entries", {}).get("warn") for v in books.values())
    return {"books": books, "min_days": min_days, "warn": warn, "warn_below_days": MARGIN_WARN_DAYS}


# ============================================================================ yazım
def _append_segment(paths: EnginePaths, d: Path, month: str, rows: list[dict]) -> Path | None:
    if not rows:
        return None
    seg = d / f"{month}.jsonl.gz"
    old = b""
    if seg.exists():
        with gzip.open(seg, "rb") as fh:
            old = fh.read()
        if old and not old.endswith(b"\n"):
            old += b"\n"
    blob = gzip_bytes(old + "".join(json_line(r) for r in rows).encode("utf-8"))
    paths.write_bytes(seg, blob)
    paths.write_text(seg.with_name(seg.name + ".sha256"), hashlib.sha256(blob).hexdigest() + "\n")
    return seg


def _pre_restore_dirs(data: Path, after: datetime | None) -> list[str]:
    """Veri kökünde `after`dan sonra beliren `state.pre-restore-*` klasörleri (mtime/ctime ile)."""
    out = []
    if not data.exists():
        return out
    for p in sorted(data.glob(PRE_RESTORE_PREFIX + "*")):
        if not p.is_dir():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        t = datetime.fromtimestamp(max(st.st_mtime, st.st_ctime), tz=timezone.utc)
        if after is None or t > after:
            out.append(p.name)
    return out


def _find_tail(lst: list[str], tail: list[str]) -> int | None:
    """`tail` dizisinin `lst` içindeki SON geçtiği yerin bitiş indeksi; yoksa None."""
    k = len(tail)
    if k == 0:
        return None
    last = tail[-1]
    for p in range(len(lst) - 1, k - 2, -1):
        if lst[p] == last and lst[p - k + 1:p + 1] == tail:
            return p
    return None


def _ref_symbols(doc: dict, kind: str) -> dict[str, str]:
    """ref_id → sembol (hareketleri enstrümana bağlamak için). Vadeli: pozisyon kimliği; spot: emir kimliği."""
    m: dict[str, str] = {}
    for h in doc.get("history") or []:
        if not isinstance(h, dict):
            continue
        rid = str(h.get("id", ""))
        if kind == KIND_SPOT and "-" in rid:
            rid = rid.rsplit("-", 1)[0]
        if rid:
            m[rid] = str(h.get("symbol", ""))
    if kind == KIND_FUTURES:
        for sym, p in (doc.get("positions") or {}).items():
            if isinstance(p, dict) and p.get("id"):
                m[str(p["id"])] = str(p.get("symbol") or sym)
    else:
        for o in list((doc.get("open_orders") or {}).values()) + list(doc.get("closed_orders") or []):
            if isinstance(o, dict) and o.get("id"):
                m[str(o["id"])] = str(o.get("symbol", ""))
        for sym, ls in (doc.get("lots") or {}).items():
            for x in ls or []:
                fid = str((x or {}).get("fill_id", "")) if isinstance(x, dict) else ""
                if "-" in fid:
                    m.setdefault(fid.rsplit("-", 1)[0], sym)
    return m


def _entry_symbol(e: dict, refmap: dict[str, str]) -> str | None:
    rid = str(e.get("ref_id", ""))
    if rid in refmap and refmap[rid]:
        return refmap[rid]
    note = str(e.get("note", ""))
    parts = note.split()
    if len(parts) >= 2 and parts[0] in ("buy", "sell"):
        return parts[1]
    return None


@dataclass
class BookResult:
    book: str
    kind: str
    status: str
    read: dict
    state: dict | None = None
    new_closes: int = 0
    revised: int = 0
    revised_older_than_resync: int = 0
    late_appearances: int = 0
    restored_away: int = 0
    vanished: int = 0
    new_entries: int = 0
    align: str | None = None
    restore: dict | None = None
    recon_wallet: dict | None = None
    recon_record: dict | None = None
    rotation: dict = field(default_factory=dict)
    fee_identity_violations: int = 0
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def archive_book(paths: EnginePaths, rd: LedgerRead, prev: dict | None, *, run_id: str,
                 futures_docs: dict[str, dict]) -> BookResult:
    """Tek defterin S1a işi: kapanış + hareket arşivi, durum, uzlaştırma, rotasyon payı, geri yükleme tespiti.
    `prev`: bir önceki anlık görüntüdeki bu defterin gövdesi (yoksa None)."""
    res = BookResult(book=rd.book, kind=rd.kind, status=rd.status, read=rd.meta())
    if not rd.ok:
        res.flags.append(rd.status)
        return res
    doc, kind, book, now = rd.doc or {}, rd.kind, rd.book, rd.read_at
    hist = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    ents = [e for e in (doc.get("entries") or []) if isinstance(e, dict)]
    prev_ok = isinstance(prev, dict) and prev.get("status") == "OK"
    prev_at = parse_ts(prev.get("read_at")) if prev_ok else None
    ts_set = {str(e.get("ts", "")) for e in ents}
    arch = load_book_archive(paths, book, ts_filter=ts_set, window_from=prev_at)
    month = now.strftime("%Y-%m")
    obs = iso(now)

    # ---- geri yükleme kanıtı
    evidence = []
    if prev_ok:
        try:
            if int(doc.get("seq") or 0) < int(prev.get("seq") or 0):
                evidence.append("SEQ_DECREASED")
        except (TypeError, ValueError):
            pass
        dirs = _pre_restore_dirs(paths.data, prev_at)
        if dirs:
            evidence.append("PRE_RESTORE_DIR:" + ",".join(dirs))
    restore = bool(evidence)
    if restore:
        res.restore = {"evidence": evidence, "prev_seq": prev.get("seq"), "seq": doc.get("seq"), "at": obs}
        res.flags.append(F_RESTORE_EVENT)

    # ---- kapanışlar
    rows: list[dict] = []
    derived: list[dict] = []
    held_keys: list[str] = []
    resync_from = now - timedelta(days=RESYNC_DAYS)
    new_main_ids: set[str] = set()
    next_ord = arch.max_ord
    had_closes = bool(arch.closes)
    new_keys: set[str] = set()
    for rec in hist:
        k = trade_key(book, rec)
        held_keys.append(k)
        sha = canonical_sha(rec)
        cur = arch.closes.get(k)
        proj = project(rec, kind)
        if not proj["fee_identity_ok"]:
            res.fee_identity_violations += 1
        if cur is None:
            next_ord += 1
            closed = parse_ts(rec.get("closed_at"))
            late = bool(prev_at is not None and closed is not None and closed <= prev_at)
            res.late_appearances += int(late)
            res.new_closes += 1
            rows.append({"schema": CLOSES_SCHEMA, "row": "close", "book": book, "trade_key": k, "rev": 0, "status": ST_ACTIVE,
                         "observed_at": obs, "run_id": run_id, "ord": next_ord, "content_sha": sha, "changed": [],
                         "late": late, "position_group": position_group(book, rec, kind), "proj": proj, "record": rec})
            arch.closes[k] = CloseState(0, sha, ST_ACTIVE, next_ord, proj, obs, obs)
            new_keys.add(k)
            if k not in arch.derived_keys:
                mtnf, src = min_to_next_funding(rec, kind)
                derived.append({"schema": DERIVED_SCHEMA, "row": "derived", "book": book, "trade_key": k, "observed_at": obs,
                                "min_to_next_funding": mtnf, "min_to_next_funding_source": src,
                                "decision_delay_s": None, "decision_delay_source": "NOT_APPLICABLE" if kind == KIND_SPOT else "MISSING"})
                if book == BOOK_MAIN_FUT:
                    new_main_ids.add(str(rec.get("id", "")))
        elif cur.sha != sha or cur.status != ST_ACTIVE:
            changed = sorted(f for f in proj if proj.get(f) != cur.proj.get(f)) or ["record"]
            if cur.status != ST_ACTIVE:
                changed.append("status")
            closed = parse_ts(rec.get("closed_at"))
            if closed is not None and closed < resync_from:
                res.revised_older_than_resync += 1
            res.revised += 1
            rows.append({"schema": CLOSES_SCHEMA, "row": "close", "book": book, "trade_key": k, "rev": cur.rev + 1,
                         "status": ST_ACTIVE, "observed_at": obs, "run_id": run_id, "ord": cur.ord, "content_sha": sha,
                         "changed": changed, "late": False, "position_group": position_group(book, rec, kind), "proj": proj,
                         "record": rec})
            arch.closes[k] = CloseState(cur.rev + 1, sha, ST_ACTIVE, cur.ord, proj, cur.first_observed_at, obs)
    if new_main_ids:
        dec_ts = _provenance_decisions(paths.state, new_main_ids)
        for d in derived:
            tid = d["trade_key"].split("|", 2)[1]
            if tid in dec_ts:
                opened = parse_ts(d["trade_key"].split("|", 2)[2])
                if opened is not None:
                    d["decision_delay_s"] = round((opened - dec_ts[tid]).total_seconds(), 3)
                    d["decision_delay_source"] = "MEASURED"
    # kaybolanlar: elde tutulan pencerenin içinde olup ledger'da artık bulunmayan aktif kayıtlar. Ledger'ın en eski
    # kaydından ÖNCE arşivlenmiş kayıtlar rotasyonla düşmüştür. En eski kayıt bu gece YENİ ise: ledger doluysa iki gece
    # arasında > 5000 kapanış olmuştur (HISTORY_GAP; eskiler rotasyonla düşmüş sayılır), dolu değilse eskilerin yokluğunu
    # rotasyon açıklamaz (hepsi denetlenir).
    held = set(held_keys)
    first_new = bool(held_keys) and held_keys[0] in new_keys
    if had_closes and first_new and len(hist) >= HISTORY_KEEP:
        res.flags.append(F_HISTORY_GAP)
    if not held_keys or (first_new and len(hist) < HISTORY_KEEP):
        first_ord = None
    else:
        first_ord = arch.closes[held_keys[0]].ord
    for k, st in sorted(arch.closes.items(), key=lambda kv: kv[1].ord):
        if k in held or st.status != ST_ACTIVE:
            continue
        if first_ord is not None and st.ord <= first_ord:
            continue                                    # rotasyonla düşmüş (ledger'ın en eski kaydından önce)
        if restore:
            res.restored_away += 1
            rows.append({"schema": CLOSES_SCHEMA, "row": "close", "book": book, "trade_key": k, "rev": st.rev + 1,
                         "status": ST_RESTORED_AWAY, "observed_at": obs, "run_id": run_id, "ord": st.ord,
                         "content_sha": st.sha, "changed": ["status"], "late": False, "position_group": None,
                         "proj": st.proj, "record": None})
            arch.closes[k] = CloseState(st.rev + 1, st.sha, ST_RESTORED_AWAY, st.ord, st.proj, st.first_observed_at, obs)
        else:
            res.vanished += 1
    if res.vanished:
        res.flags.append(F_INCONSISTENT)

    # ---- hareketler (hizalama)
    bases = [entry_base(book, e) for e in ents]
    refmap = dict(arch.refmap)
    refmap.update(_ref_symbols(doc, kind))
    new_idx: list[int]
    if arch.entries_count == 0 and not prev_ok:
        # ilk gözlem: ledger'daki hareketler ilk anlık görüntüden ÖNCEdir (hiçbir pencereye girmez)
        res.align, new_idx = ALIGN_BASELINE, list(range(len(ents)))
    elif arch.entries_count == 0:
        # önceki anlık görüntü var ama arşivde hareket yok: önceki okumada ledger'da hareket yoksa hepsi yenidir;
        # varsa arşiv o hareketleri hiç almamıştır → boşluk
        res.align = ALIGN_OK if int(prev.get("n_entries") or 0) == 0 else ALIGN_GAP
        new_idx = list(range(len(ents)))
        if res.align == ALIGN_GAP:
            res.flags.append(F_ENTRIES_GAP)
    else:
        tail = arch.entries_tail[-min(ALIGN_TAIL, len(arch.entries_tail)):]
        p = _find_tail(bases, tail)
        if p is not None:
            res.align, new_idx = ALIGN_OK, list(range(p + 1, len(ents)))
        else:
            seen: Counter = Counter()
            new_idx = []
            known_any = False
            for i, b in enumerate(bases):
                if arch.base_counts.get(b, 0) > seen[b]:
                    known_any = True
                else:
                    new_idx.append(i)
                seen[b] += 1
            res.align = ALIGN_REWIND if known_any else ALIGN_GAP
            res.flags.append(F_ENTRIES_GAP)
    erows = []
    counts = Counter(arch.base_counts)
    for j, i in enumerate(new_idx):
        e, b = ents[i], bases[i]
        occ = counts[b]
        counts[b] += 1
        erows.append({"schema": ENTRIES_SCHEMA, "book": book, "key": f"{b}#{occ}", "observed_at": obs, "run_id": run_id,
                      "pre_archive": res.align == ALIGN_BASELINE, "gap_before": bool(j == 0 and res.align in (ALIGN_GAP, ALIGN_REWIND)),
                      "symbol": _entry_symbol(e, refmap), "entry": e})
    res.new_entries = len(erows)

    # ---- yaz
    _append_segment(paths, paths.book_closes_dir(book), month, rows + derived)
    _append_segment(paths, paths.book_entries_dir(book), month, erows)

    # ---- durum (anlık görüntü gövdesi)
    if kind == KIND_FUTURES:
        state = futures_state(doc, now)
    else:
        quote = str(doc.get("quote_asset") or "USDT").upper()
        held_syms = [s for s in (doc.get("lots") or {})] + [f"{a}/{quote}" for src in (doc.get("assets") or {}, doc.get("locked_assets") or {})
                                                           for a, v in src.items() if dec(v) != 0]
        state = spot_state(doc, spot_marks(paths, held_syms, now, futures_docs), now)
    state.update({"status": "OK", "read_at": obs, "path": rd.path, "sha256": rd.sha256, "schema_version": rd.schema_version})
    res.state = state

    # ---- kayıt görünümü uzlaştırması (diskten yeniden okunan arşiv ↔ ledger; elde tutulan pencerede, ay başına)
    res.recon_record = reconcile_records(paths, book, kind, hist)
    if res.recon_record["status"] != RECON_OK:
        res.flags.append(F_INCONSISTENT)

    # ---- cüzdan görünümü uzlaştırması (S_prev, S_now]
    res.recon_wallet = reconcile_wallet(kind, prev if prev_ok else None, state, arch, erows, rows, align=res.align,
                                        restore=restore)
    if res.recon_wallet["status"] == RECON_INCONSISTENT:
        res.flags.append(F_INCONSISTENT)

    # ---- rotasyon payı
    res.rotation = {
        "history": rotation_margin([parse_ts(h.get("closed_at")) for h in hist], keep=HISTORY_KEEP,
                                   new_since_last=res.new_closes, ref=now),
        "entries": rotation_margin([parse_ts(e.get("ts")) for e in ents], keep=ENTRIES_KEEP,
                                   new_since_last=res.new_entries if res.align != ALIGN_BASELINE else 0, ref=now)}
    if res.rotation["history"]["warn"] or res.rotation["entries"]["warn"]:
        res.flags.append(F_ROTATION_WARN)
    state.update({"recon": res.recon_wallet, "align": res.align, "restore": res.restore, "rotation": res.rotation,
                  "flags": sorted(set(res.flags))})
    res.flags = sorted(set(res.flags))
    return res


_RECORD_SUMS = ("pnl", "fees", "funding", "slippage")


def reconcile_records(paths: EnginePaths, book: str, kind: str, hist: list[dict]) -> dict[str, Any]:
    """Kayıt görünümü değişmezi (§4.2): ledger'ın ELİNDE TUTTUĞU pencerede, ay başına Σ arşiv (son rev, diskten yeniden
    okunmuş) = Σ ledger `TradeRecord` (pnl, ücret, fonlama, kayma; tolerans 1e-6) ve kayıt sayısı."""
    arch = latest_closes(paths, book)
    led: dict[str, dict[str, Decimal]] = {}
    arc: dict[str, dict[str, Decimal]] = {}
    missing = 0
    for rec in hist:
        m = str(rec.get("closed_at", ""))[:7]
        p = project(rec, kind)
        a = arch.get(trade_key(book, rec))
        for tgt, src in ((led, p), (arc, a.proj if a and a.status == ST_ACTIVE else None)):
            if src is None:
                missing += 1
                continue
            row = tgt.setdefault(m, {k: Decimal(0) for k in _RECORD_SUMS} | {"n": Decimal(0)})
            for k in _RECORD_SUMS:
                row[k] += dec(src.get(k))
            row["n"] += 1
    bad = []
    for m in sorted(set(led) | set(arc)):
        lr, ar = led.get(m, {}), arc.get(m, {})
        for k in _RECORD_SUMS + ("n",):
            if abs(lr.get(k, Decimal(0)) - ar.get(k, Decimal(0))) > TOL:
                bad.append({"month": m, "field": k, "ledger": dstr(lr.get(k)), "archive": dstr(ar.get(k))})
    return {"status": RECON_OK if not bad and not missing else RECON_INCONSISTENT, "months": len(led), "missing": missing,
            "mismatches": bad[:20], "tolerance": dstr(TOL)}


def reconcile_wallet(kind: str, prev: dict | None, state: dict, arch: BookArchive, erows: list[dict], crows: list[dict], *,
                     align: str | None, restore: bool) -> dict[str, Any]:
    """Cüzdan görünümü değişmezi (§4.2) penceresi (S_prev, S_now]: vadeli Δwallet_balance = Σ gözlenen hareketler;
    spot Δ(cash+locked) = Σ hareketler + Σ yeni satışların maliyeti ve ΔB = Σ TRANSFER dışı hareketler (okuma 3)."""
    if prev is None:
        return {"status": RECON_NO_PREV}
    win = {"from": prev.get("read_at"), "to": state.get("read_at")}
    if restore or align == ALIGN_REWIND:
        return {**win, "status": RECON_RESTORED}
    if align == ALIGN_GAP:
        return {**win, "status": RECON_GAP}
    obs = arch.window_entry_sum + sum((dec(r["entry"].get("amount")) for r in erows), Decimal(0))
    obs_nt = arch.window_entry_nontransfer_sum + sum((dec(r["entry"].get("amount")) for r in erows
                                                      if str(r["entry"].get("kind")) != "TRANSFER"), Decimal(0))
    if kind == KIND_FUTURES:
        delta = dec(state.get("wallet_balance")) - dec(prev.get("wallet_balance"))
        diff = delta - obs
        ok = abs(diff) <= TOL
        return {**win, "status": RECON_OK if ok else RECON_INCONSISTENT, "delta": dstr(delta), "observed": dstr(obs),
                "diff": dstr(diff), "tolerance": dstr(TOL)}
    cost = arch.window_new_close_cost + sum((dec((r.get("proj") or {}).get("cost")) for r in crows
                                             if r.get("rev") == 0 and r.get("status") == ST_ACTIVE), Decimal(0))
    dcash = (dec(state.get("cash")) + dec(state.get("locked_cash"))) - (dec(prev.get("cash")) + dec(prev.get("locked_cash")))
    dbook = dec(state.get("book_value")) - dec(prev.get("book_value"))
    diff_cash = dcash - (obs + cost)
    diff_book = dbook - obs_nt
    ok = abs(diff_cash) <= TOL and abs(diff_book) <= TOL
    return {**win, "status": RECON_OK if ok else RECON_INCONSISTENT, "delta_cash": dstr(dcash), "observed": dstr(obs),
            "released_cost": dstr(cost), "diff_cash": dstr(diff_cash), "delta_book_value": dstr(dbook),
            "observed_non_transfer": dstr(obs_nt), "diff_book_value": dstr(diff_book), "tolerance": dstr(TOL)}


# ============================================================================ S1a orkestrasyonu
def run_s1a(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None,
            clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """S1a: bütün defterler için arşiv + anlık görüntü + uzlaştırma. Anlık görüntü dosyası o UTC günü için YOKSA
    yazılır (günün ilk başarılı okuması ölçümdür; aynı gün yeniden çalıştırma onu değiştirmez, yeni hareketleri ise
    gözlem damgasıyla sonraki pencereye ekler). Dönen sözlük `run_status.json`'a girer."""
    if clock is None:
        # Üretimde okuma anı GERÇEK saattir (`observed_at` = dosyanın okunduğu an); `now` verilirse (test/sahte VPS)
        # bütün okumalar o ana sabitlenir.
        clock = (lambda: now) if now is not None else utc_now
    now = now or utc_now()
    run_id = run_id or now.strftime("%Y%m%dT%H%M%SZ")
    paths.ensure_tree()
    day = now.strftime("%Y-%m-%d")
    days = snapshot_days(paths)
    prev_snap = load_snapshot(paths, days[-1]) if days else None
    reads = {b: read_ledger(b, kind, p, clock=clock) for b, (kind, p) in find_ledgers(paths.state).items()}
    fut_docs = {b: r.doc for b, r in reads.items() if r.ok and r.kind == KIND_FUTURES and r.doc is not None}
    books: dict[str, BookResult] = {}
    prev_books = (prev_snap or {}).get("books") or {}
    for b, rd in reads.items():
        books[b] = archive_book(paths, rd, prev_books.get(b), run_id=run_id, futures_docs=fut_docs)
    snap_written = False
    if not paths.snapshot_file(day).exists():
        snap = {"schema": SNAPSHOT_SCHEMA, "engine": ENGINE_VERSION, "day": day, "taken_at": iso(now), "run_id": run_id,
                "label": "MEASURED", "prev_day": days[-1] if days else None,
                "books": {b: (r.state if r.state is not None else {**r.read, "status": r.status}) for b, r in books.items()}}
        paths.write_json_gz(paths.snapshot_file(day), snap)
        snap_written = True
    flags = sorted({f for r in books.values() for f in r.flags})
    return {"stage": "S1a", "day": day, "run_id": run_id, "snapshot_written": snap_written,
            "prev_snapshot_day": days[-1] if days else None, "flags": flags,
            "inconsistent": F_INCONSISTENT in flags, "restore_events": [b for b, r in books.items() if r.restore],
            "books": {b: r.to_dict() for b, r in books.items()}}


__all__ = ["ALIGN_BASELINE", "ALIGN_GAP", "ALIGN_OK", "ALIGN_REWIND", "BookArchive", "BookResult", "CLOSES_SCHEMA",
           "CloseState", "DERIVED_SCHEMA", "ENTRIES_KEEP", "ENTRIES_SCHEMA", "F_ENTRIES_GAP", "F_INCONSISTENT",
           "F_HISTORY_GAP", "F_RESTORE_EVENT", "F_ROTATION_WARN", "HISTORY_KEEP", "MARGIN_WARN_DAYS", "MARK_HISTORY_BAR", "MARK_LEDGER",
           "MARK_PERP_PROXY", "MARK_POSITION_PATH", "PNL_KINDS", "RECON_GAP", "RECON_INCONSISTENT", "RECON_NO_PREV",
           "RECON_OK", "RECON_RESTORED", "SNAPSHOT_SCHEMA", "ST_ACTIVE", "ST_RESTORED_AWAY", "TOL", "archive_book",
           "archived_books", "canonical_sha", "entry_base", "futures_state", "iter_rows", "latest_closes",
           "load_book_archive", "load_snapshot", "load_snapshots", "min_to_next_funding", "position_group", "project",
           "reconcile_records", "reconcile_wallet", "rotation_margin", "rotation_status", "run_s1a", "segment_files", "snapshot_days",
           "spot_marks", "spot_state", "trade_key"]

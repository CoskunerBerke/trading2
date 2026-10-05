"""S1a — kapanış arşivi, cüzdan hareketi arşivi, gecelik ölçülmüş anlık görüntü ve uzlaştırma (§4.2, §3.6, §10 P1a);
S1s — A/B KAPALI gecelerinin YALNIZ anlık görüntüsü (§2.9 ↔ §7.1 okuması, aşağıda madde 7).

Kaynaklar (hepsi `ledgers.read_ledger` ile salt-okunur): her vadeli defterin `futures_ledger.json`'ı ve ana botun
`state/spot_ledger.json`'ı (`main_spot`). Çıktılar yalnız `data/research` altındadır:

* `closes/<defter>/YYYY-MM.jsonl.gz` — her kapanmış kaydın BİREBİR kopyası (`row="close"`, alan `record`) + küçük bir
  para izdüşümü (`proj`; okuyucular tam kaydı yeniden çözmeden toplar). Satırlar yalnız eklenir. Kayıt anahtarı
  `trade_key = defter|trade_id|opened_at`. Bir kaydın içeriği değişirse (geç fonlama, geriye tarihli kapanış) aynı
  anahtarla `rev+1` satırı eklenir. Ayrıca kayıt başına bir kez `row="derived"` satırı (`closes_derived`:
  `min_to_next_funding`, `decision_delay_s`); ham kayıt birebir kalır, türetilmiş alanlar ayrı satırdadır.
* `entries/<defter>/YYYY-MM.jsonl.gz` — `entries[]` cüzdan hareketleri (FEE, PNL, FUNDING — "late funding" ve
  "funding reversal" dahil —, TRANSFER, LIQ_FEE, TAX, SLIPPAGE_INFO), her biri ilk GÖRÜLDÜĞÜ okumanın `observed_at`
  damgasıyla (ve arşive yazıldığı anın `archived_at`'iyle). Anahtar `sha256(defter|ts|kind|ref_id|amount|note)#tekrar`;
  yeniden çalıştırma 0 satır ekler. `entries/<defter>/anchor.json` — hizalama çapası (madde 8).
* `snapshots/YYYY-MM-DD.json.gz` — gecelik ÖLÇÜLMÜŞ anlık görüntü (`MEASURED`): vadeli `wallet_balance` + açık
  pozisyonlar (miktar, giriş, `last_price`, gerçekleşmemiş), spot `cash`/`locked_cash`/`assets`/lotlar + mark kaynağı ve
  yaşı, ledger `seq`/`updated_at`, okuma zamanı, ledger kuyrukları (`tails`; madde 7); ayrıca o pencerenin
  uzlaştırması, rotasyon payı ve geri yükleme izi.

Okumalar ve belge ile kodun karşılaştırılması (belgenin niyetine göre; ayrıntı ilgili fonksiyonlarda):

1. **Segment ayı = satırın arşive yazıldığı UTC ayıdır**, kaydın kapanış ayı değil. Böylece geçmiş aylar gerçekten
   mühürlü kalır; geç gelen revizyon yeni aya eklenir. Kapanış günü her satırda `record.closed_at`'tedir.
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
   `RESTORED` olur ve `INCONSISTENT` verilmez. Kanıt yokken kaybolan kayıt `INCONSISTENT`tir. S1s gecesinde görülen
   kanıt anlık görüntüye `restore_pending` olarak yazılır ve ertesi arşiv gecesi onu da sayar.
7. **A/B KAPALI geceleri ve W(D) (§2.9 ↔ §7.1).** §2.9 KAPALI gecede "S0'dan sonra AB_OFF yazıp çıkar" der; ama §7.1'in
   MTM günü W(D) = (S(D), S(D+1)] her takvim gününün ölçülmüş anlık görüntüsünü ister. Harfiyen uygulansa her motor
   sürümünden sonraki ~14 gün EKSİK olurdu (sahibin ana ölçümü). Okuma: KAPALI gecede yalnız **S1s** çalışır —
   ledger'lar salt-okunur okunur, `snapshots/` altına TEK küçük dosya yazılır (arşiv, S3, S7, S7b YOK). Anlık görüntü
   her defterin son `SNAP_TAIL` hareket tabanını ve kayıt anahtarını (`tails`) ve `archived: false` taşır. Ertesi arşiv
   gecesi yeni hareketlerin/kapanışların her birini, o kuyrukların ledger listesindeki konumuna göre İLK GÖRÜLDÜĞÜ
   okumaya atar (`observed_at` = S1s okuma anı; `archived_at` = arşive yazıldığı an). Böylece pencere eşitlikleri
   (Δcüzdan = gözlenen hareketler; spot nakit eşitliği) KAPALI gecelerin iki yanında da tutar ve günler KESİN
   olabilir. Kuyruk bulunamazsa (araya > 2000 hareket girdiyse) bölünme kaybolur (`OBSERVATION_SPLIT_LOST`) ve o
   pencerelerin MTM eşitliği tutmaz → EKSİK (dürüst). KAPALI gecenin kendi penceresinin spot NAKİT eşitliği yazılmaz;
   vadeli cüzdan ve spot defter-değeri eşitliği S3'ün MTM eşitliğiyle her pencerede denetlenir.
8. **Hizalama çapası** (§4.2 "arşivin son 50 hareketi ledger'da aranır"). Her arşiv çalıştırması sonunda ledger'ın SON
   50 hareket tabanı `entries/<defter>/anchor.json`'a yazılır (arşivdeki satır sayısıyla birlikte; sayı tutmuyorsa çapa
   eskidir ve arşivin kendi kuyruğu kullanılır). Normal gecelerde ikisi aynıdır; geri yüklemeden sonra arşivin son
   50'si ledger'dan silinen (`RESTORED_AWAY`) hareketleri içerir ve ≥ 50 yeni hareket gelene kadar her gece
   `ENTRIES_REWIND` olurdu. Çapa yeniden yüklenmiş ledger'ın kuyruğudur: geri yükleme gecesinden sonraki gece hizalama
   yine `OK`'dir.
9. **Bellek** (§2.5: gece birimi `MemoryHigh=400M`, `MemoryMax=512M`; VPS kabul 5: tepe ≤ 0,8 × MemoryMax). Defterler
   TEK TEK işlenir: oku → arşivle → anlık görüntü gövdesini kur → belgeyi bırak (vadeli defterlerden yalnız spot
   `PERP_PROXY` mark'ları için küçük bir sembol → `last_price` haritası tutulur; spot en son işlenir). Arşiv okunurken
   tam izdüşüm yalnız ledger'ın elinde tuttuğu anahtarlar için tutulur; segmentler akışla yeniden yazılır.
   Ölçüm (`tests/test_research_engine_memory.py`: ayrı süreç, üretim yolu `import tradingbot.cli` ≈ 100 MiB pandas
   dahil, 8 vadeli defter + spot, her biri 5000 kayıt / 2000 hareket, ledger'lar toplam ≈ 98 MB; iki tam gece): tepe
   RSS önceki sürümde ≈ 450 MiB idi (bütün belgeler aynı anda bellekte; MemoryHigh 400M'in üstü, 0,8 × MemoryMax'ın
   üstü), bu sürümde ≈ 175–180 MiB'dir; test bütçesi 0,6 × MemoryMax = 307 MiB (kalan pay cgroup `memory.peak`'in
   saydığı sayfa önbelleği içindir). Birim sınırları (`MemoryHigh=400M`, `MemoryMax=512M`) bu yüzden değişmez.
10. **Defter okuma güvenceleri.** Daha önce arşivlenmiş (veya önceki anlık görüntüde olan) bir defterin ledger'ı bu
   gece yoksa `LEDGER_MISSING` (anlık görüntüde `status: MISSING`; o geçiş penceresi EKSİK); hiç ledger bulunamazsa
   `NO_LEDGERS`; okunan ledger'ların EN YENİ `updated_at`'i 6 saatten eskiyse `LEDGER_STALE` (worker duruk ya da yanlış
   state klasörü). `--check` bunları `[DİKKAT]` gösterir.
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
    LEDGER_MISSING,
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
from .paths import EnginePaths, json_line, read_json_gz, read_jsonl_gz

CLOSES_SCHEMA = "closes_v1"
DERIVED_SCHEMA = "closes_derived_v1"
ENTRIES_SCHEMA = "entries_v1"
SNAPSHOT_SCHEMA = "snapshot_v1"
ANCHOR_SCHEMA = "entries_anchor_v1"
ANCHOR_FILE = "anchor.json"

#: accounting varsayılanları (futures_ledger.py / spot_ledger.py `history_keep` / `entries_keep`).
HISTORY_KEEP = 5000
ENTRIES_KEEP = 2000
#: hizalama penceresi (§4.2): arşivin son 50 hareketi ledger listesinde aranır.
ALIGN_TAIL = 50
#: anlık görüntüde defter başına tutulan ledger kuyruğu (ilk gözlem anı atfı; okuma 7).
SNAP_TAIL = 16
#: S1a'nın geriye bakıp arşivlenmemiş (S1s) anlık görüntüleri aradığı en çok gün.
PENDING_LOOKBACK = 14
RESYNC_DAYS = 14
MARGIN_WARN_DAYS = 3.0
RATE_WINDOW_DAYS = 7
#: okunan ledger'ların en yenisi bundan eskiyse `LEDGER_STALE` (okuma 10).
STALE_AFTER = timedelta(hours=6)
#: uzlaştırma toleransı (USDT).
TOL = Decimal("1e-6")
#: spot mark kaynakları (§7.1): position_path ≤ 60 dk; HistoryStore son kapanmış bar (≤ 24 saat, yaşı yazılır).
POSITION_PATH_MAX_AGE = timedelta(minutes=60)
POSITION_PATH_FUTURE_TOL = timedelta(minutes=2)
HISTORY_BAR_MAX_AGE = timedelta(hours=24)
POSITION_PATH_TAIL_BYTES = 4 << 20

MODE_ARCHIVE, MODE_SNAPSHOT = "archive", "snapshot_only"
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
#: okuma 10 ve 7
F_LEDGER_MISSING, F_NO_LEDGERS, F_LEDGER_STALE = "LEDGER_MISSING", "NO_LEDGERS", "LEDGER_STALE"
F_SPLIT_LOST = "OBSERVATION_SPLIT_LOST"
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
@dataclass(slots=True)
class CloseState:
    """Bir kayıt anahtarının arşivdeki son durumu. `proj` yalnız istenen anahtarlar için tutulur (bellek; okuma 9);
    diğerlerinde `None`'dır ve gerekirse `latest_rows_for` ile diskten okunur."""
    rev: int
    sha: str
    status: str
    ord: int
    proj: dict | None
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
                      window_from: datetime | None = None, include_entries: bool = True,
                      keep_proj: set[str] | None = None) -> BookArchive:
    """Arşivi akışla oku. `ts_filter`: tekrar sayımı yalnız bu `ts` değerlerindeki tabanlar için tutulur (hafıza).
    `window_from`: bu andan SONRA gözlenen hareket/kapanış satırlarının toplamı (cüzdan uzlaştırması için).
    `keep_proj`: verilirse tam izdüşüm yalnız bu anahtarlar için tutulur (diğerleri `proj=None`; okuma 9)."""
    a = BookArchive(book=book)
    for row in iter_rows(paths.book_closes_dir(book)):
        if row.get("row") == "derived":
            a.derived_keys.add(str(row.get("trade_key")))
            continue
        k = str(row.get("trade_key"))
        a.close_rows += 1
        prev = a.closes.get(k)
        rp = row.get("proj") or None
        if window_from is not None and prev is None and rp:
            obs = parse_ts(row.get("observed_at"))
            if obs is not None and obs > window_from and rp.get("cost") is not None:
                a.window_new_close_cost += dec(rp.get("cost"))
        if keep_proj is not None and k not in keep_proj:
            proj = None
        else:
            proj = rp or (prev.proj if prev else {})
        st = CloseState(int(row.get("rev", 0)), str(row.get("content_sha", "")), str(row.get("status", ST_ACTIVE)),
                        int(row.get("ord", 0)), proj, prev.first_observed_at if prev else str(row.get("observed_at", "")),
                        str(row.get("observed_at", "")))
        a.closes[k] = st
        a.max_ord = max(a.max_ord, st.ord)
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


def latest_closes(paths: EnginePaths, book: str, keys: set[str] | None = None) -> dict[str, CloseState]:
    """Anahtar → son durum. `keys` verilirse yalnız o anahtarların izdüşümü tutulur (diğerleri `proj=None`)."""
    return load_book_archive(paths, book, include_entries=False, keep_proj=keys).closes


def latest_rows_for(paths: EnginePaths, book: str, keys: set[str]) -> dict[str, dict]:
    """Verilen anahtarların SON kapanış satırı (izdüşüm ve içerik sha'sı için; yalnız seyrek yollar, ör. RESTORED_AWAY)."""
    out: dict[str, dict] = {}
    if not keys:
        return out
    for row in iter_rows(paths.book_closes_dir(book)):
        k = str(row.get("trade_key"))
        if row.get("row") == "derived" or k not in keys:
            continue
        prev = out.get(k)
        out[k] = {"proj": row.get("proj") or (prev or {}).get("proj") or {}, "content_sha": row.get("content_sha")}
    return out


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


#: `load_snapshots(light=True)`: okuyucuların (S3, durum) gerekmeyen ağır alanları (kuyruklar) bellekte tutulmaz.
_HEAVY_BODY_KEYS = ("tails",)


def load_snapshots(paths: EnginePaths, *, light: bool = False) -> list[dict]:
    out = []
    for d in snapshot_days(paths):
        s = load_snapshot(paths, d)
        if isinstance(s, dict):
            if light:
                for body in (s.get("books") or {}).values():
                    if isinstance(body, dict):
                        for k in _HEAVY_BODY_KEYS:
                            body.pop(k, None)
            out.append(s)
    return out


def body_archived(body: dict) -> bool:
    """Anlık görüntü gövdesi bir arşiv çalıştırmasınca mı yazıldı? (Eski gövdelerde alan yoktur → arşiv.)"""
    return bool(body.get("archived", True))


def trailing_bodies(paths: EnginePaths, days: list[str], books: Iterable[str], *,
                    lookback: int = PENDING_LOOKBACK) -> tuple[dict[str, list[dict]], dict[str, dict | None]]:
    """Defter başına (son arşiv çalıştırmasından SONRAKİ arşivlenmemiş [S1s] OK gövdeler [eskiden yeniye], son
    ARŞİVLENMİŞ OK gövde). En yeni anlık görüntüden geriye, en çok `lookback` gün; OK olmayan gövdeler atlanır."""
    pending: dict[str, list[dict]] = {b: [] for b in books}
    base: dict[str, dict | None] = {b: None for b in books}
    open_ = set(pending)
    for d in reversed(days[-lookback:] if lookback > 0 else []):
        if not open_:
            break
        snap = load_snapshot(paths, d) or {}
        for b in list(open_):
            body = (snap.get("books") or {}).get(b)
            if not isinstance(body, dict) or body.get("status") != "OK":
                continue
            if body_archived(body):
                base[b] = body
                open_.discard(b)
            else:
                pending[b].insert(0, body)
    return pending, base


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


PerpProxies = dict[str, tuple[Decimal, "datetime | None"]]


def collect_perp_proxies(doc: dict, proxies: PerpProxies) -> PerpProxies:
    """Bir vadeli ledger'ın açık pozisyonlarının `last_price`'ını (sembol anahtarıyla) haritaya ekle; aynı sembolde
    `updated_at`'i daha yeni olan defter kazanır (§7.1 kaynak 3). Yalnız bu küçük harita tutulur (okuma 9)."""
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
    return proxies


def spot_marks(paths: EnginePaths, symbols: Iterable[str], ref: datetime,
               proxies: PerpProxies | None = None) -> dict[str, dict[str, Any]]:
    """Spot varlıkların mark'ı, mühürlü sırayla (§7.1): (1) `position_path.jsonl` ≤ 60 dk, (2) worker HistoryStore'unda
    son kapanmış spot barı, (3) aynı sembolün herhangi bir vadeli ledger'daki `last_price`'ı (`PERP_PROXY`, en yeni
    `updated_at`; `collect_perp_proxies`). Hiçbiri yoksa mark yoktur (o defterin MTM'i EKSİK)."""
    syms = sorted({str(s) for s in symbols if s})
    out: dict[str, dict[str, Any]] = {}
    if not syms:
        return out
    pp = _position_path_marks(paths.state, ref)
    proxies = proxies or {}
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


def ledger_tails(book: str, hist: list[dict], ents: list[dict], bases: list[str] | None = None) -> dict[str, Any]:
    """Anlık görüntüye yazılan ledger kuyrukları (okuma 7): son `SNAP_TAIL` hareket tabanı ve kayıt anahtarı."""
    eb = bases[-SNAP_TAIL:] if bases is not None else [entry_base(book, e) for e in ents[-SNAP_TAIL:]]
    return {"entries": list(eb), "n_entries": len(ents),
            "history": [trade_key(book, h) for h in hist[-SNAP_TAIL:]], "n_history": len(hist)}


def _tail_pos(seq: list[str], tail: list[str] | None, n_at: Any) -> int | None:
    """Bir okumadaki kuyruğun bugünkü listede bittiği konum (o okumada liste boşsa −1; bulunamazsa None)."""
    try:
        if int(n_at or 0) == 0:
            return -1
    except (TypeError, ValueError):
        return None
    if not tail:
        return None
    return _find_tail(seq, list(tail))


def _attribute(seq: list[str], idxs: list[int], points: list[tuple[list[str] | None, Any]]) -> tuple[list[int], bool]:
    """Yeni öğelerin (artan `idxs`) her biri için ilk göründüğü okuma: `points` (eskiden yeniye; her biri o okumadaki
    kuyruk ve uzunluk) içindeki sıra, ya da −1 (bu okuma). Dönen: (sıralar, bölünme kayboldu mu)."""
    pos: list[int | None] = []
    last = -1
    lost = False
    for tail, n in points:
        q = _tail_pos(seq, tail, n)
        if q is None:
            lost = True
            pos.append(None)
            continue
        q = max(q, last)
        last = q
        pos.append(q)
    out = []
    for i in idxs:
        k = -1
        for j, q in enumerate(pos):
            if q is not None and i <= q:
                k = j
                break
        out.append(k)
    return out, bool(lost and idxs and points)


def _read_anchor(paths: EnginePaths, book: str) -> dict | None:
    p = paths.book_entries_dir(book) / ANCHOR_FILE
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == ANCHOR_SCHEMA else None


def _new_since_base(book: str, hist: list[dict], ents: list[dict], bases: list[str], anchor: dict | None,
                    base: dict | None) -> tuple[int, int, str]:
    """Son ARŞİV çalıştırmasından beri ledger'a eklenen (henüz arşivlenmemiş) kapanış ve hareket sayısı (rotasyon payı
    için; S1s ve `rotation_status`). Hareket: çapa → son arşivlenmiş gövdenin kuyruğu → zaman damgası; kapanış: son
    arşivlenmiş gövdenin kuyruğu → `closed_at`. Defter hiç arşivlenmemişse ledger'daki her şey arşivlenmemiştir
    (muhafazakâr: pay en küçük)."""
    tails = (base or {}).get("tails") or {}
    base_at = parse_ts((base or {}).get("read_at")) if base else None
    q = _find_tail(bases, list(anchor.get("bases") or [])) if anchor and anchor.get("bases") else None
    src = "anchor"
    if q is None and base:
        q, src = _tail_pos(bases, tails.get("entries"), tails.get("n_entries")), "base_tail"
    if q is not None:
        new_e = len(ents) - 1 - q
    elif base_at is not None:
        new_e, src = sum(1 for e in ents if (parse_ts(e.get("ts")) or base_at) > base_at), "ts"
    else:
        new_e, src = len(ents), "never_archived"
    keys = [trade_key(book, h) for h in hist]
    qh = _tail_pos(keys, tails.get("history"), tails.get("n_history")) if base else None
    if qh is not None:
        new_h = len(hist) - 1 - qh
    elif base_at is not None:
        new_h = sum(1 for h in hist if (parse_ts(h.get("closed_at")) or base_at) > base_at)
    else:
        new_h = len(hist)
    return max(0, new_h), max(0, new_e), src


def book_rotation(hist: list[dict], ents: list[dict], *, new_h: int, new_e: int, ref: datetime) -> dict[str, Any]:
    return {"history": rotation_margin([parse_ts(h.get("closed_at")) for h in hist], keep=HISTORY_KEEP,
                                       new_since_last=new_h, ref=ref),
            "entries": rotation_margin([parse_ts(e.get("ts")) for e in ents], keep=ENTRIES_KEEP,
                                       new_since_last=new_e, ref=ref)}


def rotation_status(paths: EnginePaths, *, now: datetime | None = None) -> dict[str, Any]:
    """Arşivlemeden, salt-okunur rotasyon payı (§4.2 --check; §2.9 A/B istisnası). "Son arşivden beri yeni öğe"
    `_new_since_base` ile (çapa / son arşivlenmiş anlık görüntünün kuyruğu) bulunur. Defterler tek tek okunur."""
    now = now or utc_now()
    found = find_ledgers(paths.state)
    _pending, base = trailing_bodies(paths, snapshot_days(paths), list(found))
    books: dict[str, Any] = {}
    min_days: float | None = None
    for b, (kind, p) in found.items():
        rd = read_ledger(b, kind, p, clock=lambda: now)
        if not rd.ok:
            books[b] = {"status": rd.status}
            continue
        doc = rd.doc or {}
        hist = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
        ents = [e for e in (doc.get("entries") or []) if isinstance(e, dict)]
        bases = [entry_base(b, e) for e in ents]
        new_h, new_e, _src = _new_since_base(b, hist, ents, bases, _read_anchor(paths, b), base.get(b))
        m = book_rotation(hist, ents, new_h=new_h, new_e=new_e, ref=now)
        del rd, doc
        books[b] = {"status": "OK", **m}
        for x in m.values():
            if x["days"] is not None:
                min_days = x["days"] if min_days is None else min(min_days, x["days"])
    warn = any(v.get("history", {}).get("warn") or v.get("entries", {}).get("warn") for v in books.values())
    return {"books": books, "min_days": min_days, "warn": warn, "warn_below_days": MARGIN_WARN_DAYS}


# ============================================================================ yazım
def _append_segment(paths: EnginePaths, d: Path, month: str, rows: list[dict]) -> Path | None:
    """Ay segmentine satır ekle: eski segment AÇILIP akışla kopyalanır, yeni satırlar eklenir, bütün dosya atomik ve
    deterministik gzip olarak yeniden yazılır (yarım ekleme segmenti bozamaz); `.sha256` yan dosyası güncellenir."""
    if not rows:
        return None
    seg = d / f"{month}.jsonl.gz"

    def _chunks() -> Iterator[bytes]:
        last = b"\n"
        if seg.exists():
            with gzip.open(seg, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    last = chunk[-1:]
                    yield chunk
        if last != b"\n":
            yield b"\n"
        for r in rows:
            yield json_line(r).encode("utf-8")
    sha = paths.write_gzip_stream(seg, _chunks())
    paths.write_text(seg.with_name(seg.name + ".sha256"), sha + "\n")
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
    attributed_earlier: int = 0
    align: str | None = None
    restore: dict | None = None
    recon_wallet: dict | None = None
    recon_record: dict | None = None
    rotation: dict = field(default_factory=dict)
    fee_identity_violations: int = 0
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _restore_evidence(paths: EnginePaths, doc: dict, prev: dict | None, pending: Iterable[dict]) -> list[str]:
    """Geri yükleme kanıtı (okuma 6): arşivlenmemiş S1s gövdelerinin `restore_pending`'i + bir önceki anlık görüntüye
    göre `seq` azalması + o okumadan sonra beliren `state.pre-restore-*` klasörü."""
    evidence: list[str] = []
    for p in pending:
        evidence.extend(str(x) for x in (p.get("restore_pending") or []))
    if isinstance(prev, dict) and prev.get("status") == "OK":
        try:
            if int(doc.get("seq") or 0) < int(prev.get("seq") or 0):
                evidence.append("SEQ_DECREASED")
        except (TypeError, ValueError):
            pass
        dirs = _pre_restore_dirs(paths.data, parse_ts(prev.get("read_at")))
        if dirs:
            evidence.append("PRE_RESTORE_DIR:" + ",".join(dirs))
    return list(dict.fromkeys(evidence))


def _spot_held_symbols(doc: dict) -> list[str]:
    quote = str(doc.get("quote_asset") or "USDT").upper()
    return [s for s in (doc.get("lots") or {})] + [f"{a}/{quote}" for src in (doc.get("assets") or {}, doc.get("locked_assets") or {})
                                                   for a, v in src.items() if dec(v) != 0]


def _book_state(paths: EnginePaths, rd: LedgerRead, proxies: PerpProxies | None) -> dict[str, Any]:
    doc, now = rd.doc or {}, rd.read_at
    if rd.kind == KIND_FUTURES:
        state = futures_state(doc, now)
    else:
        state = spot_state(doc, spot_marks(paths, _spot_held_symbols(doc), now, proxies), now)
    state.update({"status": "OK", "read_at": iso(now), "path": rd.path, "sha256": rd.sha256,
                  "schema_version": rd.schema_version})
    return state


def archive_book(paths: EnginePaths, rd: LedgerRead, prev: dict | None, *, run_id: str,
                 perp_proxies: PerpProxies | None = None, pending: Iterable[dict] = (),
                 base: dict | None = None) -> BookResult:
    """Tek defterin S1a işi: kapanış + hareket arşivi, durum, uzlaştırma, rotasyon payı, geri yükleme tespiti.
    `prev`: en son anlık görüntüdeki bu defterin gövdesi; `pending`: son arşiv çalıştırmasından sonraki arşivlenmemiş
    (S1s) OK gövdeler (eskiden yeniye; okuma 7); `base`: son arşivlenmiş OK gövde (yoksa None)."""
    res = BookResult(book=rd.book, kind=rd.kind, status=rd.status, read=rd.meta())
    if not rd.ok:
        res.flags.append(rd.status)
        return res
    doc, kind, book, now = rd.doc or {}, rd.kind, rd.book, rd.read_at
    hist = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    ents = [e for e in (doc.get("entries") or []) if isinstance(e, dict)]
    pending = [p for p in pending if isinstance(p, dict) and p.get("status") == "OK"]
    pts_t = [parse_ts(p.get("read_at")) for p in pending]
    base_ok = isinstance(base, dict) and base.get("status") == "OK"
    base_at = parse_ts(base.get("read_at")) if base_ok else None
    prev_ok = isinstance(prev, dict) and prev.get("status") == "OK"
    prev_at = parse_ts(prev.get("read_at")) if prev_ok else None
    last_obs = pts_t[-1] if pts_t else (prev_at or base_at)
    held_keys = [trade_key(book, h) for h in hist]
    ts_set = {str(e.get("ts", "")) for e in ents}
    arch = load_book_archive(paths, book, ts_filter=ts_set, window_from=prev_at, keep_proj=set(held_keys))
    month = now.strftime("%Y-%m")
    obs = iso(now)

    # ---- geri yükleme kanıtı
    evidence = _restore_evidence(paths, doc, prev, pending)
    restore = bool(evidence)
    if restore:
        res.restore = {"evidence": evidence, "prev_seq": (prev or {}).get("seq"), "seq": doc.get("seq"), "at": obs}
        res.flags.append(F_RESTORE_EVENT)

    # ---- kapanışlar
    rows: list[dict] = []
    derived: list[dict] = []
    resync_from = now - timedelta(days=RESYNC_DAYS)
    new_main_ids: set[str] = set()
    next_ord = arch.max_ord
    had_closes = bool(arch.closes)
    new_keys: set[str] = set()
    new_idx: list[int] = []
    for i, rec in enumerate(hist):
        if abs(dec(rec.get("fees")) - (dec(rec.get("entry_fee")) + dec(rec.get("exit_fee")))) > TOL:
            res.fee_identity_violations += 1        # ücret özdeşliği her işlemde (§4.2), değişmeyenler dahil
        k = held_keys[i]
        cur = arch.closes.get(k)
        if cur is None:
            new_idx.append(i)
            continue
        sha = canonical_sha(rec)
        if cur.sha == sha and cur.status == ST_ACTIVE:
            continue
        proj = project(rec, kind)
        changed = sorted(f for f in proj if proj.get(f) != (cur.proj or {}).get(f)) or ["record"]
        if cur.status != ST_ACTIVE:
            changed.append("status")
        closed = parse_ts(rec.get("closed_at"))
        if closed is not None and closed < resync_from:
            res.revised_older_than_resync += 1
        res.revised += 1
        rows.append({"schema": CLOSES_SCHEMA, "row": "close", "book": book, "trade_key": k, "rev": cur.rev + 1,
                     "status": ST_ACTIVE, "observed_at": obs, "archived_at": obs, "run_id": run_id, "ord": cur.ord,
                     "content_sha": sha, "changed": changed, "late": False,
                     "position_group": position_group(book, rec, kind), "proj": proj, "record": rec})
        arch.closes[k] = CloseState(cur.rev + 1, sha, ST_ACTIVE, cur.ord, proj, cur.first_observed_at, obs)
    h_att, lost_h = _attribute(held_keys, new_idx, [((p.get("tails") or {}).get("history"),
                                                     (p.get("tails") or {}).get("n_history")) for p in pending])
    for i, katt in zip(new_idx, h_att):
        rec, k = hist[i], held_keys[i]
        sha, proj = canonical_sha(rec), project(rec, kind)
        next_ord += 1
        closed = parse_ts(rec.get("closed_at"))
        seen_at = pts_t[katt] if katt >= 0 else now
        prior = (pts_t[katt - 1] if katt >= 1 else base_at) if katt >= 0 else last_obs
        late = bool(prior is not None and closed is not None and closed <= prior)
        res.late_appearances += int(late)
        res.new_closes += 1
        res.attributed_earlier += int(katt >= 0)
        o = iso(seen_at) if seen_at is not None else obs
        rows.append({"schema": CLOSES_SCHEMA, "row": "close", "book": book, "trade_key": k, "rev": 0, "status": ST_ACTIVE,
                     "observed_at": o, "archived_at": obs, "run_id": run_id, "ord": next_ord, "content_sha": sha,
                     "changed": [], "late": late, "position_group": position_group(book, rec, kind), "proj": proj,
                     "record": rec})
        arch.closes[k] = CloseState(0, sha, ST_ACTIVE, next_ord, proj, o, o)
        new_keys.add(k)
        if k not in arch.derived_keys:
            mtnf, src = min_to_next_funding(rec, kind)
            derived.append({"schema": DERIVED_SCHEMA, "row": "derived", "book": book, "trade_key": k, "observed_at": obs,
                            "min_to_next_funding": mtnf, "min_to_next_funding_source": src,
                            "decision_delay_s": None, "decision_delay_source": "NOT_APPLICABLE" if kind == KIND_SPOT else "MISSING"})
            if book == BOOK_MAIN_FUT:
                new_main_ids.add(str(rec.get("id", "")))
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
    away: list[tuple[str, CloseState]] = []
    for k, st in sorted(arch.closes.items(), key=lambda kv: kv[1].ord):
        if k in held or st.status != ST_ACTIVE:
            continue
        if first_ord is not None and st.ord <= first_ord:
            continue                                    # rotasyonla düşmüş (ledger'ın en eski kaydından önce)
        away.append((k, st))
    if away and restore:
        latest = latest_rows_for(paths, book, {k for k, _st in away})
        for k, st in away:
            lr = latest.get(k) or {}
            proj = lr.get("proj") or st.proj or {}
            res.restored_away += 1
            rows.append({"schema": CLOSES_SCHEMA, "row": "close", "book": book, "trade_key": k, "rev": st.rev + 1,
                         "status": ST_RESTORED_AWAY, "observed_at": obs, "archived_at": obs, "run_id": run_id,
                         "ord": st.ord, "content_sha": lr.get("content_sha") or st.sha, "changed": ["status"],
                         "late": False, "position_group": None, "proj": proj, "record": None})
            arch.closes[k] = CloseState(st.rev + 1, st.sha, ST_RESTORED_AWAY, st.ord, None, st.first_observed_at, obs)
    elif away:
        res.vanished += len(away)
    if res.vanished:
        res.flags.append(F_INCONSISTENT)

    # ---- hareketler (hizalama: çapa → arşiv kuyruğu → taban sayımı; okuma 8)
    bases = [entry_base(book, e) for e in ents]
    refmap = dict(arch.refmap)
    refmap.update(_ref_symbols(doc, kind))
    e_points = [((p.get("tails") or {}).get("entries"), (p.get("tails") or {}).get("n_entries")) for p in pending]
    e_idx: list[int]
    pre_upto = -1                                   # bu konuma kadarki hareketler ilk gözlemden ÖNCEdir (pre_archive)
    if arch.entries_count == 0 and not base_ok:
        # ilk gözlem: ledger'daki hareketler ilk anlık görüntüden ÖNCEdir (hiçbir pencereye girmez). İlk gözlem bir S1s
        # anlık görüntüsü idiyse onun kuyruğuna kadarkiler öncedir, sonrakiler sonraki pencerelere atanır.
        res.align, e_idx = ALIGN_BASELINE, list(range(len(ents)))
        if e_points:
            q0 = _tail_pos(bases, e_points[0][0], e_points[0][1])
            pre_upto = len(ents) - 1 if q0 is None else q0
            if q0 is None:
                res.flags.append(F_SPLIT_LOST)
        else:
            pre_upto = len(ents) - 1
    elif arch.entries_count == 0:
        # son arşivlenmiş gövde var ama arşivde hareket yok: o okumada ledger'da hareket yoksa hepsi yenidir; varsa arşiv
        # o hareketleri hiç almamıştır → boşluk
        # (o okumada 0 hareket vardı ama ledger şimdi DOLU ise aradaki en eskiler rotasyonla düşmüş olabilir → boşluk)
        res.align = ALIGN_OK if int(base.get("n_entries") or 0) == 0 and len(ents) < ENTRIES_KEEP else ALIGN_GAP
        e_idx = list(range(len(ents)))
        if res.align == ALIGN_GAP:
            res.flags.append(F_ENTRIES_GAP)
    else:
        anchor = _read_anchor(paths, book)
        tails = []
        if anchor and anchor.get("bases") and int(anchor.get("archive_count") or -1) == arch.entries_count:
            tails.append(list(anchor["bases"]))
        tails.append(arch.entries_tail[-min(ALIGN_TAIL, len(arch.entries_tail)):])
        p = None
        for t in tails:
            p = _find_tail(bases, t)
            if p is not None:
                break
        if p is not None:
            res.align, e_idx = ALIGN_OK, list(range(p + 1, len(ents)))
        else:
            seen: Counter = Counter()
            e_idx = []
            known_any = False
            for i, b in enumerate(bases):
                if arch.base_counts.get(b, 0) > seen[b]:
                    known_any = True
                else:
                    e_idx.append(i)
                seen[b] += 1
            res.align = ALIGN_REWIND if known_any else ALIGN_GAP
            res.flags.append(F_ENTRIES_GAP)
    e_att, lost_e = _attribute(bases, e_idx, e_points)
    if lost_h or lost_e:
        res.flags.append(F_SPLIT_LOST)
    erows = []
    counts = Counter(arch.base_counts)
    for j, (i, katt) in enumerate(zip(e_idx, e_att)):
        e, b = ents[i], bases[i]
        occ = counts[b]
        counts[b] += 1
        seen_at = pts_t[katt] if katt >= 0 else now
        res.attributed_earlier += int(katt >= 0)
        erows.append({"schema": ENTRIES_SCHEMA, "book": book, "key": f"{b}#{occ}",
                      "observed_at": iso(seen_at) if seen_at is not None else obs, "archived_at": obs, "run_id": run_id,
                      "pre_archive": i <= pre_upto, "gap_before": bool(j == 0 and res.align in (ALIGN_GAP, ALIGN_REWIND)),
                      "symbol": _entry_symbol(e, refmap), "entry": e})
    res.new_entries = sum(1 for r in erows if not r["pre_archive"]) if res.align == ALIGN_BASELINE else len(erows)

    # ---- yaz (segmentler, sonra çapa: çapa yalnız arşivdeki satır sayısıyla tutarlıysa kullanılır)
    _append_segment(paths, paths.book_closes_dir(book), month, rows + derived)
    _append_segment(paths, paths.book_entries_dir(book), month, erows)
    paths.write_json(paths.book_entries_dir(book) / ANCHOR_FILE,
                     {"schema": ANCHOR_SCHEMA, "book": book, "bases": bases[-ALIGN_TAIL:], "n_entries": len(ents),
                      "archive_count": arch.entries_count + len(erows), "observed_at": obs, "run_id": run_id})

    # ---- durum (anlık görüntü gövdesi)
    state = _book_state(paths, rd, perp_proxies)
    state.update({"archived": True, "tails": ledger_tails(book, hist, ents, bases)})
    res.state = state

    # ---- cüzdan görünümü uzlaştırması (S_prev, S_now]; sonra arşiv özeti bırakılır (bellek)
    res.recon_wallet = reconcile_wallet(kind, prev if prev_ok else None, state, arch, erows, rows, align=res.align,
                                        restore=restore)
    if res.recon_wallet["status"] == RECON_INCONSISTENT:
        res.flags.append(F_INCONSISTENT)
    arch.closes.clear()
    del arch

    # ---- kayıt görünümü uzlaştırması (diskten yeniden okunan arşiv ↔ ledger; elde tutulan pencerede, ay başına)
    res.recon_record = reconcile_records(paths, book, kind, hist)
    if res.recon_record["status"] != RECON_OK:
        res.flags.append(F_INCONSISTENT)

    # ---- rotasyon payı
    res.rotation = book_rotation(hist, ents, new_h=res.new_closes,
                                 new_e=res.new_entries if res.align != ALIGN_BASELINE else 0, ref=now)
    if res.rotation["history"]["warn"] or res.rotation["entries"]["warn"]:
        res.flags.append(F_ROTATION_WARN)
    state.update({"recon": res.recon_wallet, "align": res.align, "restore": res.restore, "rotation": res.rotation,
                  "flags": sorted(set(res.flags))})
    res.flags = sorted(set(res.flags))
    return res


def snapshot_book(paths: EnginePaths, rd: LedgerRead, prev: dict | None, *, perp_proxies: PerpProxies | None = None,
                  pending: Iterable[dict] = (), base: dict | None = None) -> BookResult:
    """S1s (okuma 7): arşivlemeden yalnız ölçülmüş anlık görüntü gövdesi + kuyruklar + rotasyon payı + geri yükleme
    kanıtı (`restore_pending`). Arşive, çapaya ve hiçbir başka dosyaya dokunmaz."""
    res = BookResult(book=rd.book, kind=rd.kind, status=rd.status, read=rd.meta())
    if not rd.ok:
        res.flags.append(rd.status)
        return res
    doc, book, now = rd.doc or {}, rd.book, rd.read_at
    hist = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    ents = [e for e in (doc.get("entries") or []) if isinstance(e, dict)]
    bases = [entry_base(book, e) for e in ents]
    evidence = _restore_evidence(paths, doc, prev, ())
    if evidence:
        res.restore = {"evidence": evidence, "prev_seq": (prev or {}).get("seq"), "seq": doc.get("seq"), "at": iso(now),
                       "pending": True}
        res.flags.append(F_RESTORE_EVENT)
    base_ok = isinstance(base, dict) and base.get("status") == "OK"
    new_h, new_e, src = _new_since_base(book, hist, ents, bases, _read_anchor(paths, book), base if base_ok else None)
    res.rotation = book_rotation(hist, ents, new_h=new_h, new_e=new_e, ref=now)
    res.rotation["since_source"] = src
    if res.rotation["history"]["warn"] or res.rotation["entries"]["warn"]:
        res.flags.append(F_ROTATION_WARN)
    state = _book_state(paths, rd, perp_proxies)
    state.update({"archived": False, "tails": ledger_tails(book, hist, ents, bases), "recon": None, "align": None,
                  "restore": None, "restore_pending": evidence or None, "rotation": res.rotation,
                  "flags": sorted(set(res.flags))})
    res.state = state
    res.flags = sorted(set(res.flags))
    return res


_RECORD_SUMS = ("pnl", "fees", "funding", "slippage")


def reconcile_records(paths: EnginePaths, book: str, kind: str, hist: list[dict]) -> dict[str, Any]:
    """Kayıt görünümü değişmezi (§4.2): ledger'ın ELİNDE TUTTUĞU pencerede, ay başına Σ arşiv (son rev, diskten yeniden
    okunmuş) = Σ ledger `TradeRecord` (pnl, ücret, fonlama, kayma; tolerans 1e-6) ve kayıt sayısı."""
    keys = {trade_key(book, rec) for rec in hist}
    arch = latest_closes(paths, book, keys)
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
    spot Δ(cash+locked) = Σ hareketler + Σ yeni satışların maliyeti ve ΔB = Σ TRANSFER dışı hareketler (okuma 3).
    Bu gece arşive yazılan ama ilk gözlemi S_prev ya da daha önce olan satırlar (okuma 7) pencereye girmez."""
    if prev is None:
        return {"status": RECON_NO_PREV}
    win = {"from": prev.get("read_at"), "to": state.get("read_at")}
    if restore or align == ALIGN_REWIND:
        return {**win, "status": RECON_RESTORED}
    if align == ALIGN_GAP:
        return {**win, "status": RECON_GAP}
    prev_at = parse_ts(prev.get("read_at"))

    def _inside(r: dict) -> bool:
        if r.get("pre_archive"):
            return False
        t = parse_ts(r.get("observed_at"))
        return prev_at is None or (t is not None and t > prev_at)
    new_e = [r for r in erows if _inside(r)]
    obs = arch.window_entry_sum + sum((dec(r["entry"].get("amount")) for r in new_e), Decimal(0))
    obs_nt = arch.window_entry_nontransfer_sum + sum((dec(r["entry"].get("amount")) for r in new_e
                                                      if str(r["entry"].get("kind")) != "TRANSFER"), Decimal(0))
    if kind == KIND_FUTURES:
        delta = dec(state.get("wallet_balance")) - dec(prev.get("wallet_balance"))
        diff = delta - obs
        ok = abs(diff) <= TOL
        return {**win, "status": RECON_OK if ok else RECON_INCONSISTENT, "delta": dstr(delta), "observed": dstr(obs),
                "diff": dstr(diff), "tolerance": dstr(TOL)}
    cost = arch.window_new_close_cost + sum((dec((r.get("proj") or {}).get("cost")) for r in crows
                                             if r.get("rev") == 0 and r.get("status") == ST_ACTIVE and _inside(r)),
                                            Decimal(0))
    dcash = (dec(state.get("cash")) + dec(state.get("locked_cash"))) - (dec(prev.get("cash")) + dec(prev.get("locked_cash")))
    dbook = dec(state.get("book_value")) - dec(prev.get("book_value"))
    diff_cash = dcash - (obs + cost)
    diff_book = dbook - obs_nt
    ok = abs(diff_cash) <= TOL and abs(diff_book) <= TOL
    return {**win, "status": RECON_OK if ok else RECON_INCONSISTENT, "delta_cash": dstr(dcash), "observed": dstr(obs),
            "released_cost": dstr(cost), "diff_cash": dstr(diff_cash), "delta_book_value": dstr(dbook),
            "observed_non_transfer": dstr(obs_nt), "diff_book_value": dstr(diff_book), "tolerance": dstr(TOL)}


# ============================================================================ S1a / S1s orkestrasyonu
def _ordered(found: dict[str, tuple[str, Path]]) -> list[tuple[str, tuple[str, Path]]]:
    """Vadeli defterler önce (bulunduğu sırayla), spot EN SON (PERP_PROXY mark'ları vadeli defterlerden toplanır)."""
    return sorted(found.items(), key=lambda kv: kv[1][0] == KIND_SPOT)


def _drive(paths: EnginePaths, *, now: datetime | None, run_id: str | None, clock: Callable[[], datetime] | None,
           snapshot_only: bool) -> dict[str, Any]:
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
    prev_books = (prev_snap or {}).get("books") or {}
    found = find_ledgers(paths.state)
    pending, base = trailing_bodies(paths, days, list(found))
    proxies: PerpProxies = {}
    books: dict[str, BookResult] = {}
    newest: datetime | None = None
    for b, (kind, p) in _ordered(found):
        rd = read_ledger(b, kind, p, clock=clock)
        if rd.ok:
            upd = parse_ts((rd.doc or {}).get("updated_at"))
            if upd is not None and (newest is None or upd > newest):
                newest = upd
            if kind == KIND_FUTURES:
                collect_perp_proxies(rd.doc or {}, proxies)
        if snapshot_only:
            books[b] = snapshot_book(paths, rd, prev_books.get(b), perp_proxies=proxies, pending=pending[b], base=base[b])
        else:
            books[b] = archive_book(paths, rd, prev_books.get(b), run_id=run_id, perp_proxies=proxies, pending=pending[b],
                                    base=base[b])
        rd.doc = None                       # okuma 9: belge bırakılır, bir sonraki defter okunur
        del rd
    # okuma 10: daha önce görülen (arşivlenmiş ya da önceki anlık görüntüde OK/MISSING) ama bu gece ledger'ı olmayan defter
    known = set(archived_books(paths)) | {b for b, x in prev_books.items()
                                         if isinstance(x, dict) and x.get("status") in ("OK", LEDGER_MISSING)}
    for b in sorted(known - set(found)):
        meta = {"book": b, "kind": (prev_books.get(b) or {}).get("kind"), "status": LEDGER_MISSING, "read_at": iso(now),
                "error": "ledger dosyası yok (daha önce görülmüştü)"}
        books[b] = BookResult(book=b, kind=str(meta["kind"] or "?"), status=LEDGER_MISSING, read=meta,
                              flags=[F_LEDGER_MISSING])
    run_flags = {f for r in books.values() for f in r.flags}
    if not found:
        run_flags.add(F_NO_LEDGERS)
    stale = None
    if newest is not None:
        stale = {"newest_updated_at": iso(newest), "age_s": round((now - newest).total_seconds(), 1),
                 "limit_s": STALE_AFTER.total_seconds()}
        if now - newest > STALE_AFTER:
            run_flags.add(F_LEDGER_STALE)
    snap_written = False
    if not paths.snapshot_file(day).exists():
        snap = {"schema": SNAPSHOT_SCHEMA, "engine": ENGINE_VERSION, "day": day, "taken_at": iso(now), "run_id": run_id,
                "label": "MEASURED", "mode": MODE_SNAPSHOT if snapshot_only else MODE_ARCHIVE,
                "prev_day": days[-1] if days else None, "flags": sorted(run_flags), "ledger_freshness": stale,
                "books": {b: (r.state if r.state is not None else {**r.read, "status": r.status}) for b, r in books.items()}}
        paths.write_json_gz(paths.snapshot_file(day), snap)
        snap_written = True
    flags = sorted(run_flags)
    rot = [x["days"] for r in books.values() for k, x in (r.rotation or {}).items()
           if k in ("history", "entries") and isinstance(x, dict) and x.get("days") is not None]
    return {"stage": "S1s" if snapshot_only else "S1a", "mode": MODE_SNAPSHOT if snapshot_only else MODE_ARCHIVE,
            "day": day, "run_id": run_id, "snapshot_written": snap_written,
            "prev_snapshot_day": days[-1] if days else None, "flags": flags,
            "inconsistent": F_INCONSISTENT in flags, "restore_events": [b for b, r in books.items() if r.restore],
            "ledger_freshness": stale, "rotation_min_days": min(rot) if rot else None,
            "books": {b: r.to_dict() for b, r in books.items()}}


def run_s1a(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None,
            clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """S1a: bütün defterler için arşiv + anlık görüntü + uzlaştırma. Anlık görüntü dosyası o UTC günü için YOKSA
    yazılır (günün ilk başarılı okuması ölçümdür; aynı gün yeniden çalıştırma onu değiştirmez, yeni hareketleri ise
    gözlem damgasıyla sonraki pencereye ekler). Dönen sözlük `run_status.json`'a girer."""
    return _drive(paths, now=now, run_id=run_id, clock=clock, snapshot_only=False)


def run_s1s(paths: EnginePaths, *, now: datetime | None = None, run_id: str | None = None,
            clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """S1s (A/B KAPALI gece; okuma 7): yalnız ölçülmüş anlık görüntü (o UTC günü için yoksa) + rotasyon payı; arşiv
    YAZILMAZ. `rotation_min_days` 3'ün altındaysa gece birimi aynı gece S1a'yı da çalıştırır (`AB_OFF_ZORUNLU_ARŞİV`)."""
    return _drive(paths, now=now, run_id=run_id, clock=clock, snapshot_only=True)


__all__ = ["ALIGN_BASELINE", "ALIGN_GAP", "ALIGN_OK", "ALIGN_REWIND", "ANCHOR_FILE", "BookArchive", "BookResult",
           "CLOSES_SCHEMA", "CloseState", "DERIVED_SCHEMA", "ENTRIES_KEEP", "ENTRIES_SCHEMA", "F_ENTRIES_GAP",
           "F_HISTORY_GAP", "F_INCONSISTENT", "F_LEDGER_MISSING", "F_LEDGER_STALE", "F_NO_LEDGERS", "F_RESTORE_EVENT",
           "F_ROTATION_WARN", "F_SPLIT_LOST", "HISTORY_KEEP", "MARGIN_WARN_DAYS", "MARK_HISTORY_BAR", "MARK_LEDGER",
           "MARK_PERP_PROXY", "MARK_POSITION_PATH", "MODE_ARCHIVE", "MODE_SNAPSHOT", "PNL_KINDS", "RECON_GAP",
           "RECON_INCONSISTENT", "RECON_NO_PREV", "RECON_OK", "RECON_RESTORED", "SNAPSHOT_SCHEMA", "SNAP_TAIL",
           "STALE_AFTER", "ST_ACTIVE", "ST_RESTORED_AWAY", "TOL", "archive_book", "archived_books", "body_archived",
           "book_rotation", "canonical_sha", "collect_perp_proxies", "entry_base", "futures_state", "iter_rows",
           "latest_closes", "latest_rows_for", "ledger_tails", "load_book_archive", "load_snapshot", "load_snapshots",
           "min_to_next_funding", "position_group", "project", "reconcile_records", "reconcile_wallet",
           "rotation_margin", "rotation_status", "run_s1a", "run_s1s", "segment_files", "snapshot_book", "snapshot_days",
           "spot_marks", "spot_state", "trade_key", "trailing_bodies"]

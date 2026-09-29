# -*- coding: utf-8 -*-
"""ORTAK DENEYİM KATMANI v1 — TOPLAYICI ve SALT-OKUR ADAPTÖRLER (2026-09-29). Yalnız KAYIT: karar DEĞİŞMEZ.

Motor her turun sonunda (`engine_v3._shared_experience_step`, `_journal_decisions`tan SONRA) `step()` çağırır. Bu
modül sekiz defterin (ana bot, T2, M2, Box, D4, C4, C4S, Formasyon) gerçek işlemlerini ve karşı-olgusallarını KOPYALAR,
satırları `rows.py` ile kurar ve `store.ExperienceStore`a (yalnız `state/<state_dir>/`) yazar.

Sözleşme (2026-09-29; SPEC_V1 §6-§8, KARARLAR.md):

* **Salt okur.** Defter / pozisyon / plan / karşı-olgusal kayıtçı / gölge defter / öğrenici / günlük / özet / `features`
  / `meta` üzerinde hiçbir yazma ya da değiştiren çağrı YOK (AST koruma testi). Kilitler yalnız DENEMELİ alınır
  (`lock_timeout_s`); meşgulse defter bu adımda atlanır (`lock_busy_skips`), imleçler artımlı olduğu için kayıp olmaz.
* **Kilit altında yalnız KOPYA.** G/Ç, anlık görüntü hesabı ve satır kurulumu kilit DIŞINDADIR. Kilit altında yalnız
  yeni/değişen kayıtlar beyaz listeli, derinliği sınırlı sözlüklere kopyalanır.
* **Karşı-olgusal kaynaklar BELLEKTEN** okunur (`book.cf.sb.trades`, ana `eng.shadow.trades` içinde `book == "main"`);
  JSON dosyaları ayrıştırılmaz. Eski (öğrenmesiz, `book` None) gölgeler kayıt dışıdır.
* **Nedensellik.** Anlık görüntü yalnız `as_of` anında kapanmış barlardan; pencereler tam değilse satır TASLAK
  (PENDING) kalır ve sonraki adımlarda yeniden denenir; `pending_max_age_h` sonra GAP / NO_BARS ile yazılır.
* **Bütçe.** Adım başına süre (`tour_budget_s`) ve satır (`max_rows_per_tour`) tavanı; kalan iş sonraki adıma kalır.
  Devre kesici: art arda 5 istisna ya da art arda 3 bütçe aşımı (süre > 1,5 × bütçe) → `DISABLED_BY_BREAKER` (süreç
  boyunca; yeniden başlatma açar).
* **Yalnız PAPER.** `mode_state.mode != PAPER` → `SUSPENDED:<mod>`, satır YAZILMAZ.
* **Bellek sınırlı.** LRU anlık görüntü önbelleği; karşı-olgusal `known` kümesi her adım aktif listeye budanır;
  gerçek birleştirme anahtarları sınırlı kuyruk; taslak tavanı (`max_rows_per_tour`, en az 100).
"""
from __future__ import annotations

import hashlib
import heapq
import json
import logging
import numbers
import time
from collections import deque
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from ..core import atomic_write_text, iso, read_json
from . import LAYER_VERSION
from . import rows as R
from . import situation as S
from .bars import BarSource
from .cache import SituationCache
from .store import STATE_DEGRADED, ExperienceStore

log = logging.getLogger(__name__)

# ============================================================================ sabitler (2026-09-29)
CURSOR_SCHEMA = "shared_experience_cursor_v1"
STATUS_SCHEMA = "shared_experience_status_v1"
CURSOR_FILE = "cursor.json"
STATUS_FILE = "status.json"
MODE_RECORD = "RECORD"
STATE_OK = "OK"
STATE_BREAKER = "DISABLED_BY_BREAKER"
STATE_SUSPENDED = "SUSPENDED"
STATE_DEGRADED_STORE = STATE_DEGRADED
#: Devre kesici eşikleri (SPEC §7.9). Aşım = adım süresi > OVERRUN_FACTOR × bütçe (bütçeye DEĞMEK aşım değildir:
#: iş sonraki adıma kalır ve `budget_hits` sayılır).
BREAKER_ERRORS = 5
BREAKER_OVERRUNS = 3
OVERRUN_FACTOR = 1.5
#: İş döngüleri (kapanış/karşı-olgusal/taslak) bütçenin bu kesrinde durur (2026-09-29): kalan pay bütçesiz kuyruğa
#: (toplu yazım + döngü + imleç + durum dosyası) kalır. Ölçüm: 8 defter × 5000 geri doldurmada kuyruk tracemalloc
#: altında ~1 sn'ye çıkıyor ve tam bütçeyle adım 1,5 × bütçeyi aşıp ilk açılışta devre kesiciyi tetikliyordu.
WORK_BUDGET_FRACTION = 0.75
#: Son N adımın süresi (p50/p95).
STEP_MS_KEEP = 50
#: Defter başına saklanan son gerçek giriş birleştirme anahtarı (SUPERSEDED tespiti; değişim aynı turda olur).
REAL_JK_KEEP = 500
#: Geç funding penceresi (`FuturesLedgerV2.settle_late_funding(window=500)`).
LEDGER_WINDOW = R.LATE_FUNDING_WINDOW
#: Taslak tavanının alt sınırı; geri doldurma taslakları tavanın en çok yarısını kullanır (canlı iş önce).
MIN_DRAFTS = 100
MAIN_KEY = "main"
BTC = S.BTC_SYMBOL
#: İmleç yazım seyreltmesi (2026-09-29, inceleme bulgusu): bu boydan büyük imleç her adım DEĞİL en çok
#: `CURSOR_SAVE_EVERY_STEPS` adımda bir yazılır (tam listelerde imleç ~3,3 MB; her tur yazımı günde ~0,8 GB). İçerik
#: yeniden türetilebilir: satır kimlikleri deterministiktir ve tekrarlar depoda/okuyucuda tekilleşir; kayıtlı imleç tutarlı
#: bir anlık görüntüdür (ör. `last_key` ile bekleyen kapanmış işlem girişleri birlikte).
CURSOR_THROTTLE_BYTES = 262_144
CURSOR_SAVE_EVERY_STEPS = 5
#: Geri doldurmada kilit ALTINDA defter başına kopyalanan en çok kayıt (2026-09-29, inceleme bulgusu: 300 kayıt + 5000
#: karşı-olgusal taraması defter kilidini ~430 ms tutuyordu; koruyucu izleyici ve Box zamanlayıcısı bekliyordu).
BF_COPY_PER_BOOK = 100
#: Ana bot açılışının sinyal kimliği (`trade_id → _signal_id`) açılış adımında ana defter atlanırsa kaybolmasın diye imleçte
#: bu süre / adet kadar tutulur (2026-09-29, inceleme bulgusu).
SIG_TID_TTL_MS = 2 * 86_400_000
SIG_TID_KEEP = 500
#: Kaybolmuş karşı-olgusal kimliklerinin son revizyonu (mezar taşı): deterministik kimlik yeniden kaydedilirse revizyon
#: numarası kaldığı yerden sürer (2026-09-29, inceleme bulgusu: yeni rev 0/1 eski satırlarla çakışıp tekrar sayılıyordu).
CF_GONE_TTL_MS = 3 * 86_400_000
CF_GONE_KEEP = 2000
#: Karşı-olgusal imleç durum kodları (kompakt).
_ST_CODE = {R.PENDING: "P", R.LABELLED: "L"}
_CODE_ST = {v: k for k, v in _ST_CODE.items()}
IGNORED = "X"                       # kayıt dışı (SPOT / geri doldurma kapalı / kurulamaz satır)
ORIG_LIVE, ORIG_BF = "L", "B"
_MISSING = object()


# ============================================================================ ayarlar (2026-09-29)
@dataclass
class XpSettings:
    """`config_v3.SharedExperienceSection`in toplayıcıya giden kısmı (testler motorsuz kurabilsin diye ayrı)."""
    state_dir: str = "shared_experience"
    hot_max_lines: int = 5000
    archive_max_segments: int = 0
    max_total_mb: float = 1024.0
    tour_budget_s: float = 2.0
    max_rows_per_tour: int = 600
    backfill: bool = True
    cache_entries: int = 2048
    pending_max_age_h: float = 48.0
    lock_timeout_s: float = 0.2
    lazy_fetch_max_per_tour: int = 0

    @classmethod
    def from_section(cls, sec: Any) -> "XpSettings":
        return cls(**{f.name: getattr(sec, f.name) for f in fields(cls) if sec is not None and hasattr(sec, f.name)})


# ============================================================================ kopyalama (kilit ALTINDA; yalnız bellek)
_COPY_DEPTH = 6
_COPY_LIST = 64


def _ev(x: Any) -> Any:
    return x.value if isinstance(x, Enum) else x


def _copy(x: Any, depth: int = 0) -> Any:
    """Sınırlı derin kopya (JSON benzeri). Kaynak nesne DEĞİŞMEZ; paylaşılan değişken iç yapı dışarı taşınmaz."""
    if depth > _COPY_DEPTH:
        return None
    if x is None or isinstance(x, (bool, str, int, float, Decimal, datetime)):
        return x
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, Mapping):
        return {str(k): _copy(v, depth + 1) for k, v in list(x.items())}
    if isinstance(x, (list, tuple)):
        return [_copy(v, depth + 1) for v in list(x)[:_COPY_LIST]]
    if isinstance(x, (set, frozenset)):
        return [_copy(v, depth + 1) for v in sorted(list(x), key=str)[:_COPY_LIST]]
    if isinstance(x, numbers.Number):
        return x
    return str(x)


#: Gerçek işlemin `features` beyaz listesi (rows.py'nin okuduğu anahtarlar + sinyal kaynağı).
_REAL_FEATURE_KEYS = (
    "learning", "exploration", "in_lab_universe", "candle_variation", "risk_usdt", "initial_stop", "funding_coverage",
    "exit_fill", "path_unverified", "p_win", "expected_r", "regime", "consensus_score", "consensus_conf", "n_vetoes",
    "expected_cost_pct", "spread_pct", "structure", "strategy", "family", "cohort", "protocol_version", "rr_after_cost",
    "target_source", "side_rule", "age_h_at_entry", "plan_id", "market_type", "setup_type")
_GATE_KEYS = ("regime_gate", "candle_confirmation", "chart_confirmation")
_META_KEYS = ("signal", "plan_id", "family", "protocol_version", "cohort", "rr_after_cost", "target_source", "side_rule",
              "age_h_at_entry")
_CF_FEATURE_KEYS = (
    "strategy", "setup_type", "regime", "signal_close", "atr14", "lab_algo", "plan_id", "family", "protocol_version",
    "cohort", "entry_tf", "structure", "learning", "learning_fit", "candle_variation", "p_win", "expected_r",
    "conservative_net_edge_r", "net_expectancy_r", "in_lab_universe", "baseline_blocked_by")
_REC_FIELDS = ("id", "symbol", "side", "entry", "exit_reason", "closed_at", "opened_at", "pnl", "fees", "funding",
               "r_multiple", "mae_pct", "mfe_pct", "bars_held", "leverage", "setup_type", "quantity", "entry_fee",
               "exit_fee", "funding_paid", "funding_received", "slippage_cost", "gross_pnl", "net_pnl", "exit_price",
               "market_type")
_CF_FIELDS = ("id", "plan_id", "symbol", "market_type", "direction", "created_at", "entry", "stop", "horizon_bars",
              "variant", "label_ts", "tf_minutes", "leverage", "labeled_at", "book", "signal_key", "variation",
              "label_kind", "learning_unlocked", "rule_version", "approx")


def _proj_features(f: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(f, Mapping):
        return {}
    out: dict[str, Any] = {}
    for k in keys:
        if k in f and f[k] is not None:
            v = f[k]
            if k == "learning" and isinstance(v, Mapping):
                # `baseline_size` sözlüğü kopyalanmaz; satır yalnız VARLIĞINI yazar (rows.project_learning)
                v = {kk: ({} if kk == R.BASELINE_SIZE_KEY and isinstance(vv, Mapping) else vv) for kk, vv in list(v.items())}
            out[k] = _copy(v, 1)
    for g in _GATE_KEYS:
        gv = f.get(g)
        if isinstance(gv, Mapping):
            out[g] = {"verdict": _copy(gv.get("verdict"), 2)}
    ds = f.get("data_source")
    if isinstance(ds, Mapping) and isinstance(ds.get("bars"), Mapping):
        out["data_source"] = {"bars": _copy(ds.get("bars"), 2)}
    return out


def _pos_facts(pos: Any) -> dict[str, Any]:
    """Açık `Position` → düz kopya (kilit altında)."""
    meta = getattr(pos, "meta", None)
    return {"id": _ev(getattr(pos, "id", None)), "symbol": getattr(pos, "symbol", None),
            "side": _ev(getattr(pos, "side", None)), "market_type": _ev(getattr(pos, "market_type", None)),
            "entry_avg": getattr(pos, "entry_avg", None), "initial_qty": getattr(pos, "initial_qty", None),
            "qty": getattr(pos, "qty", None), "initial_stop": getattr(pos, "initial_stop", None),
            "targets": _copy(list(getattr(pos, "targets", None) or [])[:16], 1), "leverage": getattr(pos, "leverage", None),
            "opened_at": getattr(pos, "opened_at", None), "setup_type": getattr(pos, "setup_type", None),
            "features": _proj_features(getattr(pos, "features", None), _REAL_FEATURE_KEYS),
            "meta": ({k: _copy(meta[k], 1) for k in _META_KEYS if k in meta} if isinstance(meta, Mapping) else {})}


def _rec_facts(rec: Any) -> dict[str, Any]:
    """Kapanmış `TradeRecord` → düz kopya (kilit altında)."""
    out = {k: _ev(getattr(rec, k, None)) for k in _REC_FIELDS}
    out["features"] = _proj_features(getattr(rec, "features", None), _REAL_FEATURE_KEYS)
    return out


def _cf_facts(t: Any) -> dict[str, Any]:
    """`ShadowTrade` → düz kopya (kilit altında). Sonuç sözlüğü DERİN kopyalanır (yerinde yeniden etiketleme yarışı yok)."""
    out = {k: _ev(getattr(t, k, None)) for k in _CF_FIELDS}
    out["targets"] = _copy(list(getattr(t, "targets", None) or [])[:16], 1)
    out["reason_not_opened"] = [str(x) for x in list(getattr(t, "reason_not_opened", None) or [])[:16]]
    o = getattr(t, "outcome", None)
    out["outcome"] = _copy(o, 1) if isinstance(o, Mapping) else None
    f = getattr(t, "features", None)
    out["features"] = _proj_features(f, _CF_FEATURE_KEYS) if isinstance(f, Mapping) else None
    return out


def _cf_q(t: Any) -> Any:
    """Ucuz değişim parmak izi (bellek içi): sonuç nesnesinin kimliği + sürüm/net/brüt/çıkış."""
    o = getattr(t, "outcome", None)
    if o is None or (o.__class__ is not dict and not isinstance(o, Mapping)):   # hızlı yol: ABC denetimi yalnız gerekirse
        return None
    return (id(o), o.get("label_version"), o.get("r_net"), o.get("r_multiple"), o.get("r_gross"), o.get("exit_reason"))


def _iso_ms(x: Any) -> int | None:
    if not x:
        return None
    try:
        s = str(x)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _tkey(book: str, tid: Any, opened_at: Any) -> str | None:
    try:
        return R.trade_key(book, tid, opened_at)
    except R.RowError:
        return None


# ============================================================================ adaptörler (salt okur)
class BookAdapter:
    """Bir defterin salt-okur görünümü: kilit, defter, karşı-olgusal liste ve sinyal anahtarı türetimi (2026-09-29).

    Hiçbir yöntem kaynağa yazmaz; `ledger()` / `cf_trades()` yalnız referans döner (kopyayı toplayıcı kilit altında alır)."""

    def __init__(self, *, key: str, book_type: str, lock: Callable[[], Any], ledger: Callable[[], Any],
                 cf_trades: Callable[[], Any], cf_under_lock: bool, cf_filter: Callable[[Any], bool] | None = None,
                 decision_tf: str | None = None) -> None:
        self.key = key
        self.book_name = R.BOOKS[key][0]
        self.book_type = book_type
        self._lock = lock
        self._ledger = ledger
        self._cf = cf_trades
        self.cf_under_lock = bool(cf_under_lock)
        self.cf_filter = cf_filter
        self.decision_tf = decision_tf

    def lock(self) -> Any:
        return self._lock()

    def ledger(self) -> Any:
        return self._ledger()

    def cf_trades(self) -> Any:
        return self._cf()


def _main_cf_list(eng: Any) -> Any:
    sh = getattr(eng, "shadow", None)
    return getattr(sh, "trades", None) if sh is not None else None


def _book_cf_list(book: Any) -> Any:
    cf = getattr(book, "cf", None)                         # tembel kurulur; None → bu süreçte karşı-olgusal yok
    sb = getattr(cf, "sb", None) if cf is not None else None
    return getattr(sb, "trades", None) if sb is not None else None


def build_adapters(eng: Any) -> tuple[list[BookAdapter], dict[str, int]]:
    """Motorun sekiz defteri için adaptörler. Bilinmeyen / adı uyuşmayan defter atlanır ve sayılır (2026-09-29)."""
    out: list[BookAdapter] = []
    skipped: dict[str, int] = {}
    if getattr(eng, "ledger2", None) is not None:
        out.append(BookAdapter(key=MAIN_KEY, book_type=R.MAIN, lock=lambda: eng._ledger_lock,
                               ledger=lambda: eng.ledger2, cf_trades=lambda: _main_cf_list(eng), cf_under_lock=False,
                               cf_filter=lambda t: getattr(t, "book", None) == MAIN_KEY))
    for b in list(getattr(eng, "strategy_books", None) or []):
        key = str(getattr(b, "key", "") or "")
        info = R.BOOKS.get(key)
        if info is None or info[1] != R.STRATEGY or info[0] != str(getattr(b, "name", "")):
            skipped[key or "?"] = skipped.get(key or "?", 0) + 1
            continue
        try:
            dtf = str(b._decision_tf())                   # SAF: kuralın dilimlerinden okur
        except Exception:  # noqa: BLE001
            dtf = None
        out.append(BookAdapter(key=key, book_type=R.STRATEGY, lock=(lambda _b=b: _b.lock),
                               ledger=(lambda _b=b: _b.ledger), cf_trades=(lambda _b=b: _book_cf_list(_b)),
                               cf_under_lock=True, decision_tf=dtf))
    pb = getattr(eng, "pattern_book", None)
    if pb is not None:
        key = str(getattr(pb, "key", "") or "")
        info = R.BOOKS.get(key)
        if info is None or info[1] != R.PATTERN:
            skipped[key or "?"] = skipped.get(key or "?", 0) + 1
        else:
            out.append(BookAdapter(key=key, book_type=R.PATTERN, lock=lambda: pb.lock, ledger=lambda: pb.ledger,
                                   cf_trades=lambda: _book_cf_list(pb), cf_under_lock=True))
    return out, skipped


def main_signal_keys(eng: Any, risk_log: Any, decisions: Any, briefs: Any,
                     stats: dict[str, int] | None = None) -> tuple[dict[str, str], int]:
    """Bu turda açılan ana bot işlemleri için `trade_id → _signal_id` (SPEC §5.4; SAF). Açılışın `_seen_signals`e
    eklediği kimlikle çapraz denetlenir; tutmayan (ya da SPOT) eşleme YAZILMAZ. Döner: (eşlem, doğrulanamayan sayısı).
    `stats` verilirse (2026-09-29, KARARLAR 3) kayıt dışı SPOT açılışları `stats["spot"]`a sayılır."""
    out: dict[str, str] = {}
    bad = 0
    try:
        bmap = {getattr(b, "symbol", None): b for b in list(briefs or [])}
        seen = set(getattr(eng, "_seen_signals", None) or [])
        for e in list(risk_log or []):
            if not isinstance(e, Mapping) or not e.get("trade_id"):
                continue
            if str(_ev(e.get("verdict")) or "").upper() == "SPOT_LONG":
                if stats is not None:                     # SPOT v1'de kapsam dışı — sessiz değil, SAYILIR (KARARLAR 3)
                    stats["spot"] = int(stats.get("spot", 0)) + 1
                continue
            sym = e.get("symbol")
            d = (decisions or {}).get(sym) if isinstance(decisions, Mapping) else None
            b = bmap.get(sym)
            plan = getattr(d, "active_plan", None) if d is not None else None
            if d is None or b is None or plan is None:
                bad += 1
                continue
            if str(_ev(getattr(d, "verdict", None))) == "SPOT_LONG":
                continue                                  # SPOT v1'de kapsam dışı
            sig = eng._signal_id(sym, R.MARKET, d, plan, b)
            if sig not in seen:
                bad += 1
                continue
            out[str(e["trade_id"])] = str(sig)
    except Exception:  # noqa: BLE001 — (2026-09-29) sinyal eşlemesi yoksa giriş anahtarsız yazılır (uydurulmaz)
        bad += 1
    return out, bad


def real_signal_key(ad: BookAdapter, facts: Mapping[str, Any], *, main_sig: str | None = None) -> tuple[str | None, str | None]:
    """Gerçek işlemin sinyal anahtarı ve kaynağı (SPEC §5.4). Biçim karşı-olgusal kayıtçının anahtarıyla AYNI
    (`strategy_paper._signal_key` → `"signal_ts:<ms>"`; Formasyon `plan_id`; ana bot `_signal_id`)."""
    if ad.book_type == R.MAIN:
        return (str(main_sig), "ENGINE_SIGNAL_ID") if main_sig else (None, None)
    f = facts.get("features") if isinstance(facts.get("features"), Mapping) else {}
    m = facts.get("meta") if isinstance(facts.get("meta"), Mapping) else {}
    if ad.book_type == R.PATTERN:
        pid = f.get("plan_id") or m.get("plan_id")
        return (str(pid), "PLAN_ID") if pid else (None, None)
    sig = m.get("signal")
    if isinstance(sig, Mapping) and sig.get("signal_ts") is not None and not isinstance(sig.get("signal_ts"), bool):
        try:
            return "signal_ts:%d" % int(sig["signal_ts"]), "META_SIGNAL_TS"
        except (TypeError, ValueError):
            pass
    ds = f.get("data_source")
    bars = ds.get("bars") if isinstance(ds, Mapping) else None
    if isinstance(bars, Mapping) and ad.decision_tf and bars.get(ad.decision_tf) is not None:
        try:
            return "signal_ts:%d" % int(bars[ad.decision_tf]), "DATA_SOURCE_BAR"
        except (TypeError, ValueError):
            pass
    return None, None


def _real_join_key(ad: BookAdapter, facts: Mapping[str, Any], sk: str | None) -> str | None:
    if not sk:
        return None
    f = facts.get("features") if isinstance(facts.get("features"), Mapping) else {}
    cv = f.get("candle_variation")
    var = cv.get("id") if isinstance(cv, Mapping) else None
    try:
        return R.join_key(book=ad.key, signal_key=sk, symbol=facts.get("symbol"), side=facts.get("side"), variation=var)
    except R.RowError:
        return None


# ============================================================================ imleç (2026-09-29)
class BookCursor:
    """Defter başına O(yeni) imleç (2026-09-29). `cf_known[tid]` kompakt liste: [rev, fp, durum, köken] (+ bekleyende
    kaybolma satırı için: sembol, yön, sinyal, varyasyon, kurulum, label_ts, tf). Diskte `entries` / `cf` adlarıyla."""

    def __init__(self, d: Mapping[str, Any] | None = None) -> None:
        d = d if isinstance(d, Mapping) else {}
        led = d.get("ledger") if isinstance(d.get("ledger"), Mapping) else {}
        self.last_key: str | None = led.get("last_key") or None
        self.n_seen = int(led.get("n") or 0)
        bf = d.get("backfill") if isinstance(d.get("backfill"), Mapping) else {}
        self.bf_next: str | None = bf.get("next_key") or None
        self.bf_done = bool(bf.get("done", False))
        self.first_seen = bool(d)
        #: Girişi yazılmış (ya da kayıt dışı "X") işlem anahtarları — açık ya da kapanışı henüz işlenmemiş olanlar.
        self.entry_done: dict[str, Any] = {str(k): v for k, v in (d.get("entries") or {}).items()}
        self.unfinal: dict[str, list] = {str(k): list(v) for k, v in (d.get("unfinal") or {}).items()
                                         if isinstance(v, (list, tuple)) and len(v) >= 2}
        #: Satırı yazılmış karşı-olgusal kimlikleri (aktif listeye budanır).
        self.cf_known: dict[str, list] = {str(k): list(v) for k, v in (d.get("cf") or {}).items()
                                    if isinstance(v, (list, tuple)) and len(v) >= 3}
        self.real_jk: deque = deque([str(x) for x in (d.get("real_jk") or [])], maxlen=REAL_JK_KEEP)
        self.sig: dict[str, str] = {str(k): str(v) for k, v in (d.get("sig") or {}).items()}
        #: (2026-09-29) ana bot: tüketilmemiş `trade_id → [sinyal, eklenme ms]` (açılış adımında defter atlandıysa).
        self.sig_tid: dict[str, list] = {str(k): list(v) for k, v in (d.get("sig_tid") or {}).items()
                                         if isinstance(v, (list, tuple)) and len(v) >= 2}
        #: (2026-09-29) kaybolmuş karşı-olgusal mezar taşları: `cf_id → [son yazılan rev, kaybolma ms]`.
        self.cf_gone: dict[str, list] = {str(k): list(v) for k, v in (d.get("cf_gone") or {}).items()
                                         if isinstance(v, (list, tuple)) and len(v) >= 2}
        #: (2026-09-29) yüklemede okunan, kapanmış işlemlerin bekleyen giriş taslakları (toplayıcı yeniden kaydeder).
        self.pend_e_loaded: dict[str, Any] = {str(k): v for k, v in (d.get("pend_e") or {}).items() if isinstance(v, Mapping)}

    def to_dict(self, pend_e: Mapping[str, Any] | None = None) -> dict[str, Any]:
        out = {"ledger": {"last_key": self.last_key, "n": self.n_seen},
               "backfill": {"next_key": self.bf_next, "done": self.bf_done},
               "entries": dict(self.entry_done), "unfinal": dict(self.unfinal), "cf": dict(self.cf_known),
               "real_jk": list(self.real_jk), "sig": dict(self.sig)}
        # yalnız doluysa (2026-09-29): boş defter imleci eski biçimle aynı kalır
        if self.sig_tid:
            out["sig_tid"] = dict(self.sig_tid)
        if self.cf_gone:
            out["cf_gone"] = dict(self.cf_gone)
        if pend_e:
            out["pend_e"] = dict(pend_e)
        return out


@dataclass
class _Delta:
    """Bir defterin bu adımdaki kopyaları (kilit altında toplanır; sonra kilit DIŞINDA işlenir)."""
    busy: bool = False
    ledger_ok: bool = False
    resync: bool = False
    open_keys: set = field(default_factory=set)
    open_new: list = field(default_factory=list)             # (tk, facts)
    closed_new: list = field(default_factory=list)           # (tk, facts, in_window)
    closed_keys: set = field(default_factory=set)            # imleçten sonraki TÜM kapanış anahtarları
    live_more: bool = False
    revised: list = field(default_factory=list)              # (tk, facts, in_window)
    unfinal_missing: list = field(default_factory=list)
    bf_records: list = field(default_factory=list)           # (tk, facts, in_window, next_key_after)
    bf_exhausted: bool = False
    end_key: str | None = None
    cf_ok: bool = False
    cf_present: set = field(default_factory=set)
    cf_changed: list = field(default_factory=list)           # (tid, q, status, fp, facts)
    cf_new: list = field(default_factory=list)               # (tid, q, facts, created_ms)
    cf_q_only: list = field(default_factory=list)            # (tid, q)
    cf_bf_left: int = 0


@dataclass
class _Draft:
    """Anlık görüntü bekleyen satır (yalnız bellek; SPEC §4.1 erteleme)."""
    kind: str                     # "E" giriş | "C" karşı-olgusal rev 0
    book: str
    src_key: str
    symbol: str
    as_of_ms: int | None
    origin: str
    facts: dict
    sig: str | None = None
    sig_src: str | None = None
    q: Any = None
    lag: int = 0
    born_step: int = 0
    #: (2026-09-29) kapanmış işlemin (TradeRecord'dan) giriş taslağı: imleç `last_key` onu geçti → imleçte saklanır.
    closed: bool = False


# ============================================================================ toplayıcı
class SharedExperienceCollector:
    """Tur sonu toplayıcısı (ana iş parçacığı). Arızası turu ve kararı ETKİLEMEZ (2026-09-29)."""

    def __init__(self, root: Path | str, *, settings: XpSettings | None = None, state_path: Path | str | None = None,
                 csv_dir: Path | str | None = None, code_sha: str | None = None, config_hash: str | None = None) -> None:
        self.s = settings or XpSettings()
        self.root = Path(root)
        self.state_path = Path(state_path) if state_path else None
        self.csv_dir = Path(csv_dir) if csv_dir else None
        self.code_sha = code_sha
        self.config_hash = config_hash
        self.store = ExperienceStore(self.root, hot_max_lines=int(self.s.hot_max_lines),
                                     archive_max_segments=int(self.s.archive_max_segments), code_sha=code_sha,
                                     max_total_mb=float(self.s.max_total_mb))
        self.store.load_seen()                                # yeniden başlatma: sıcak dosyanın kimlikleri (≤ 5000)
        self.cache = SituationCache(max_entries=int(self.s.cache_entries))
        self.max_drafts = max(MIN_DRAFTS, int(self.s.max_rows_per_tour))
        self._drafts: dict[tuple[str, str, str], _Draft] = {}
        self._memq: dict[str, dict[str, Any]] = {}
        self._learning_since: str | None = None
        self.state = STATE_OK
        self.steps = 0
        self._consec_errors = 0
        self._consec_overruns = 0
        self._step_ms: deque = deque(maxlen=STEP_MS_KEEP)
        self.last_step_at: str | None = None
        self.last_error_code: str | None = None
        self.rows_last = 0
        self.disk_bytes = 0
        self.c: dict[str, Any] = {
            "rows_total": 0, "rows_by_kind": {}, "rows_by_book": {}, "snapshot_status_mix": {},
            "lock_busy_skips": 0, "lock_busy_by_book": {}, "budget_hits": 0, "row_cap_hits": 0, "budget_overruns": 0,
            "resyncs": 0, "vanished_by_status": {}, "legacy_gross_cf": 0, "unknown_label_version": 0,
            "spot_excluded": 0, "row_errors": {}, "write_errors": 0, "io_retry_steps": 0, "rejected_rows": 0, "duplicate_rows": 0,
            "errors_total": 0, "snapshot_errors": 0, "drafts_forced": 0, "drafts_expired": 0, "unfinal_lost": 0,
            "main_sigkey_unconfirmed": 0, "books_skipped": {}, "cursor_corrupt": 0, "cursor_write_errors": 0,
            "status_write_errors": 0, "suspended_steps": 0, "degraded_steps": 0, "ignored_backfill": 0,
            "spot_real_excluded": 0, "spot_real_closed_seen": None, "main_entry_no_sigkey": 0, "cf_reappeared": 0,
            "cursor_deferred": 0, "pending_entries_restored": 0, "rotate_errors": 0}
        self._cursor_sha: str | None = None
        self._shape_saved: tuple | None = None
        self._dirty = False
        self._cursor_bytes = 0
        self._cursor_saved_step = 0
        self._load_cursor()
        self.store.seed_cf_labelled(self._cur_meta.get("cf_labelled") or {})
        try:
            # (2026-09-29) çökme kurtarması + gerekiyorsa döngü, HİÇBİR ekleme yapılmadan önce (kesim aynı blok → aynı segment)
            self.store.rotate()
        except Exception as exc:  # noqa: BLE001 — kurtarma sonraki döngü çağrısında yeniden denenir
            self.c["rotate_errors"] += 1
            self.last_error_code = ("ROTATE: %s" % exc)[:200]

    # ------------------------------------------------------------------ kurulum
    @classmethod
    def from_engine(cls, eng: Any) -> "SharedExperienceCollector":
        """Motorun yapılandırmasından kurar (motora YAZMAZ). Kök = `state/<state_dir>`."""
        sec = getattr(getattr(eng.cfg, "v3", None), "shared_experience", None)
        st = XpSettings.from_section(sec)
        state_path = Path(eng.cfg.state_path)
        code_sha = eng.__dict__.get("_code_sha_cache") or getattr(eng.cfg, "code_sha", None)
        cfg_hash = eng.__dict__.get("_config_hash_cache")
        if not cfg_hash:
            try:
                from dataclasses import asdict

                from ..core import payload_hash
                if getattr(eng.cfg, "v3", None) is not None:
                    _d = asdict(eng.cfg.v3)
                    _d.pop("shared_experience", None)         # motorun `config_hash()` kuralıyla AYNI (karar kimliği)
                    cfg_hash = payload_hash(_d)
            except Exception:  # noqa: BLE001 — (2026-09-29) etiket yalnız; hesaplanamazsa boş kalır
                cfg_hash = None
        return cls(state_path / st.state_dir, settings=st, state_path=state_path,
                   csv_dir=getattr(eng.cfg, "cache_path", None), code_sha=code_sha, config_hash=cfg_hash)

    # ------------------------------------------------------------------ imleç dosyası
    def _load_cursor(self) -> None:
        p = self.root / CURSOR_FILE
        doc = None
        if p.exists():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or doc.get("schema") != CURSOR_SCHEMA:
                    raise ValueError("cursor schema")
            except (OSError, ValueError) as exc:
                self.c["cursor_corrupt"] += 1
                self.last_error_code = ("CURSOR_CORRUPT: %s" % exc)[:200]
                try:                                          # bozuk imleç kenara alınır (silinmez); yeniden eşitlenir
                    p.replace(p.with_name(p.name + ".corrupt"))
                except OSError:
                    pass
                doc = None
        doc = doc or {}
        self._cur_meta: dict[str, Any] = {"born_ms": doc.get("born_ms"), "backfill": doc.get("backfill"),
                                          "cf_labelled": doc.get("cf_labelled") or {}, "created_at": doc.get("created_at")}
        self._books: dict[str, BookCursor] = {str(k): BookCursor(v) for k, v in (doc.get("books") or {}).items()
                                              if str(k) in R.BOOKS}
        self._cursor_sha = None
        # (2026-09-29, inceleme bulgusu) kapanmış işlemlerin bekleyen giriş taslakları: imleç `last_key` onları geçmişti;
        # eskiden yalnız bellekteydiler ve yeniden başlatmada giriş satırı (anlık görüntü) kalıcı olarak kayboluyordu.
        for key, bc in self._books.items():
            for tk, dd in list(bc.pend_e_loaded.items()):
                dr = _draft_from_doc(key, tk, dd)
                if dr is not None and tk not in bc.entry_done:
                    self._drafts[("E", key, tk)] = dr
                    self.c["pending_entries_restored"] += 1
            bc.pend_e_loaded = {}

    def _pend_entries(self) -> dict[str, dict[str, Any]]:
        """Kapanmış işlemlerin bekleyen giriş taslakları, defter başına (imleçte saklanır; 2026-09-29)."""
        out: dict[str, dict[str, Any]] = {}
        for (kind, book, tk), dr in self._drafts.items():
            if kind == "E" and dr.closed:
                out.setdefault(book, {})[tk] = _draft_to_doc(dr)
        return out

    def _cursor_doc(self) -> dict[str, Any]:
        pend = self._pend_entries()
        return {"schema": CURSOR_SCHEMA, "layer_version": LAYER_VERSION, "born_ms": self._cur_meta.get("born_ms"),
                "backfill": self._cur_meta.get("backfill"), "created_at": self._cur_meta.get("created_at"),
                "cf_labelled": self.store.cf_labelled_counts(),
                "books": {k: b.to_dict(pend.get(k)) for k, b in sorted(self._books.items())}}

    def _cursor_shape(self) -> tuple:
        """Ucuz yapısal parmak izi (2026-09-29): imleç değerleri yalnız commit'lerle (sayılır) ya da YENİ anahtar
        eklenerek (uzunluk değişir) değişir; tek istisna `_dirty` ile işaretlenir. Kararlı adımda 40 bin kimlikli
        imleci her adım yeniden JSON'a çevirmemek için."""
        n_pend = sum(1 for (kind, _b, _t), dr in self._drafts.items() if kind == "E" and dr.closed)
        return (self._cur_meta.get("born_ms"), self._cur_meta.get("backfill"),
                tuple(sorted(self.store.cf_labelled_counts().items())), n_pend,
                tuple((k, b.last_key, b.n_seen, b.bf_next, b.bf_done, len(b.entry_done), len(b.unfinal), len(b.cf_known),
                       len(b.real_jk), (b.real_jk[-1] if b.real_jk else None), len(b.sig), len(b.sig_tid), len(b.cf_gone))
                      for k, b in sorted(self._books.items())))

    def _save_cursor(self, *, commits: int = 0, force: bool = False) -> None:
        shape = self._cursor_shape()
        if not force and not commits and not self._dirty and shape == self._shape_saved:
            return                                            # değişmediyse serileştirilmez/yazılmaz
        if (not force and self._cursor_bytes >= CURSOR_THROTTLE_BYTES and self._shape_saved is not None
                and self.steps - self._cursor_saved_step < CURSOR_SAVE_EVERY_STEPS):
            self.c["cursor_deferred"] += 1                    # (2026-09-29) büyük imleç: en çok N adımda bir yazılır
            return
        text = json.dumps(self._cursor_doc(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if sha == self._cursor_sha:
            self._dirty, self._shape_saved = False, shape
            return
        try:
            atomic_write_text(self.root / CURSOR_FILE, text)
            self._cursor_sha = sha
            self._dirty, self._shape_saved = False, shape
            self._cursor_bytes, self._cursor_saved_step = len(text), self.steps
        except OSError as exc:
            self.c["cursor_write_errors"] += 1
            self.last_error_code = ("CURSOR_WRITE: %s" % exc)[:200]

    def _book(self, key: str) -> BookCursor:
        b = self._books.get(key)
        if b is None:
            b = self._books[key] = BookCursor(None)
        return b

    # ------------------------------------------------------------------ yardımcılar
    def _learning_since_value(self) -> str | None:
        """`state/learning_mode.json` "since" (motorun önbelleğine DOKUNMADAN; bulununca sabit kalır)."""
        if self._learning_since is None and self.state_path is not None:
            try:
                doc = read_json(self.state_path / "learning_mode.json", default=None)
            except Exception:  # noqa: BLE001
                doc = None
            v = doc.get("since") if isinstance(doc, dict) else None
            self._learning_since = str(v) if v else None
        return self._learning_since

    def _bump(self, name: str, key: str, n: int = 1) -> None:
        d = self.c[name]
        d[key] = int(d.get(key, 0)) + int(n)

    def _row_error(self, exc: Exception) -> None:
        code = str(exc).split(":", 1)[0][:60] or type(exc).__name__
        if code == "SPOT_EXCLUDED":
            self.c["spot_excluded"] += 1
        self._bump("row_errors", code)

    # ------------------------------------------------------------------ genel arayüz
    def health(self) -> dict[str, Any]:
        """`health.json["shared_experience"]` — O(1), ASLA istisna atmaz."""
        try:
            return {"mode": MODE_RECORD, "state": self.state, "rows_total": int(self.c["rows_total"]),
                    "rows_last": int(self.rows_last), "drafts": len(self._drafts),
                    "step_ms": (self._step_ms[-1] if self._step_ms else None), "errors": int(self.c["errors_total"]),
                    "disk_mb": round(self.disk_bytes / 1048576.0, 3)}
        except Exception:  # noqa: BLE001
            return {"mode": MODE_RECORD, "state": "UNKNOWN"}

    def _pct(self, p: float) -> float | None:
        xs = sorted(self._step_ms)
        if not xs:
            return None
        k = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
        return xs[k]

    def status(self) -> dict[str, Any]:
        drafts_by: dict[str, int] = {}
        for dr in self._drafts.values():
            k = "%s:%s" % (dr.kind, dr.origin)
            drafts_by[k] = drafts_by.get(k, 0) + 1
        try:
            store = self.store.summary()
        except Exception as exc:  # noqa: BLE001
            store = {"error": str(exc)[:200]}
        return {"schema": STATUS_SCHEMA, "layer_version": LAYER_VERSION, "mode": MODE_RECORD, "state": self.state,
                "last_step_at": self.last_step_at, "steps": self.steps,
                "step_ms_last": (self._step_ms[-1] if self._step_ms else None),
                "step_ms_p50": self._pct(0.5), "step_ms_p95": self._pct(0.95), "rows_last": self.rows_last,
                "drafts": len(self._drafts), "drafts_by": drafts_by,
                "breaker": {"consecutive_errors": self._consec_errors, "consecutive_overruns": self._consec_overruns,
                            "tripped": self.state == STATE_BREAKER, "errors_limit": BREAKER_ERRORS,
                            "overruns_limit": BREAKER_OVERRUNS, "overrun_factor": OVERRUN_FACTOR},
                "last_error_code": self.last_error_code, "born_ms": self._cur_meta.get("born_ms"),
                "backfill": {k: {"done": b.bf_done} for k, b in sorted(self._books.items())},
                "counters": json.loads(json.dumps(self.c)), "cache": self.cache.stats(), "store": store,
                "budget": {"tour_budget_s": self.s.tour_budget_s, "max_rows_per_tour": self.s.max_rows_per_tour,
                           "max_drafts": self.max_drafts, "lock_timeout_s": self.s.lock_timeout_s,
                           "lazy_fetch_max_per_tour": self.s.lazy_fetch_max_per_tour}}

    def _write_status(self) -> None:
        try:
            atomic_write_text(self.root / STATUS_FILE, json.dumps(self.status(), ensure_ascii=False, indent=1, default=str))
        except Exception as exc:  # noqa: BLE001
            self.c["status_write_errors"] += 1
            self.last_error_code = ("STATUS_WRITE: %s" % exc)[:200]

    def step(self, eng: Any, *, risk_log: Any = None, decisions: Any = None, briefs: Any = None,
             now: datetime | None = None) -> dict[str, Any]:
        """Bir tur sonu adımı. İstisna DIŞARI ÇIKMAZ (devre kesici sayar); dönen özet yalnız bilgi."""
        if self.state == STATE_BREAKER:
            return {"state": self.state, "rows": 0}
        t0 = time.perf_counter()
        now = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.steps += 1
        self.last_step_at = iso(now)
        self.rows_last = 0
        try:
            mstate = getattr(eng, "mode_state", None)
            m = getattr(mstate, "mode", None)
            mode = str(getattr(m, "value", m) or "?").upper()
            if mode == "PAPER" and callable(getattr(mstate, "is_live_order_path_enabled", None)) \
                    and mstate.is_live_order_path_enabled():
                mode = "LIVE_ORDER_PATH_ENABLED"             # canlı emir yolu açık derlemede kayıt da durur
        except Exception:  # noqa: BLE001
            mode = "?"
        if mode != "PAPER":
            self.state = "%s:%s" % (STATE_SUSPENDED, mode)   # YALNIZ PAPER — satır yazılmaz
            self.c["suspended_steps"] += 1
            self._write_status()
            return {"state": self.state, "rows": 0}
        res: dict[str, Any] = {"rows": 0}
        try:
            res = self._run(eng, risk_log=risk_log, decisions=decisions, briefs=briefs, now=now, t0=t0)
            self._consec_errors = 0
        except Exception as exc:  # noqa: BLE001 — kayıt katmanı ASLA turu durdurmaz
            self._consec_errors += 1
            self.c["errors_total"] += 1
            try:
                from ..learn.telemetry import sanitize_code
                self.last_error_code = sanitize_code(exc)
            except Exception:  # noqa: BLE001
                self.last_error_code = type(exc).__name__
            log.warning("ortak deneyim adımı başarısız (%d/%d; karar ETKİLENMEZ): %s", self._consec_errors,
                        BREAKER_ERRORS, exc)
            if self._consec_errors >= BREAKER_ERRORS:
                self.state = STATE_BREAKER
        ms = round((time.perf_counter() - t0) * 1000.0, 3)
        self._step_ms.append(ms)
        if ms > OVERRUN_FACTOR * float(self.s.tour_budget_s) * 1000.0:
            self.c["budget_overruns"] += 1
            self._consec_overruns += 1
            if self._consec_overruns >= BREAKER_OVERRUNS:
                self.state = STATE_BREAKER
        else:
            self._consec_overruns = 0
        if self.state == STATE_BREAKER:
            log.error("ortak deneyim katmanı DEVRE KESİCİ ile kapandı (süreç boyunca; karar ETKİLENMEZ): %s",
                      self.last_error_code)
        self._write_status()
        res["state"] = self.state
        res["step_ms"] = ms
        return res

    # ------------------------------------------------------------------ adım
    def _run(self, eng: Any, *, risk_log: Any, decisions: Any, briefs: Any, now: datetime, t0: float) -> dict[str, Any]:
        s = self.s
        now_ms = int(now.timestamp() * 1000)
        pressure = self.store.pressure()
        self.disk_bytes = int(pressure.get("disk_bytes") or 0)
        if pressure.get("state") == STATE_DEGRADED:
            self.state = STATE_DEGRADED_STORE                 # disk tavanı %110: hiçbir şey okunmaz/yazılmaz (sayılır)
            self.c["degraded_steps"] += 1
            return {"rows": 0}
        self.state = STATE_OK
        tour_ms = int(getattr(eng, "_tour_now_ms", 0) or 0) or now_ms
        if self._cur_meta.get("born_ms") is None:
            # ilk açılış: bu turun başlangıcı (saniyeye yuvarlı — `opened_at` saniye hassasiyetinde); öncesi GERİ DOLDURMA
            self._cur_meta["born_ms"] = (tour_ms // 1000) * 1000
            self._cur_meta["backfill"] = bool(s.backfill)
            self._cur_meta["created_at"] = iso(now)
        env = R.make_env(recorded_at=now, app_mode=R.APP_MODE_PAPER, code_sha=self.code_sha,
                         config_hash=self.config_hash, learning_since=self._learning_since_value())
        ctx = _StepCtx(self, env=env, now=now, now_ms=now_ms, born=int(self._cur_meta["born_ms"]),
                       bf_on=bool(self._cur_meta.get("backfill")),
                       deadline=t0 + WORK_BUDGET_FRACTION * max(0.0, float(s.tour_budget_s)),
                       cap=max(1, int(s.max_rows_per_tour)))
        sstats: dict[str, int] = {}
        sigmap, n_bad = main_signal_keys(eng, risk_log, decisions, briefs, stats=sstats)
        self.c["main_sigkey_unconfirmed"] += n_bad
        self.c["spot_real_excluded"] += int(sstats.get("spot", 0))
        hist = getattr(getattr(eng, "spot2", None), "history", None)
        if isinstance(hist, list):                            # salt okur gösterge: spot defterinin kapanmış işlemleri
            self.c["spot_real_closed_seen"] = len(hist)
        if sigmap and getattr(eng, "ledger2", None) is not None:
            # (2026-09-29) ana defter bu adımda atlanırsa (kilit meşgul / arıza / DEGRADED) açılışın sinyal kimliği
            # kaybolmasın: giriş taslağı kurulana kadar imleçte (TTL'li) kalır
            mc = self._book(MAIN_KEY)
            for tid, sig in sigmap.items():
                mc.sig_tid.setdefault(str(tid), [str(sig), now_ms])
        nfetch = int(s.lazy_fetch_max_per_tour or 0)
        ctx.bars = BarSource(frames=getattr(getattr(eng, "runner", None), "last_frames", None),
                             provenance=getattr(eng, "_frame_provenance", None), tour_now_ms=tour_ms,
                             csv_dir=self.csv_dir, fetcher=(_lazy_fetcher(eng) if nfetch > 0 else None),
                             fetch_budget=nfetch, wall_ms=lambda: int(time.time() * 1000))
        adapters, skipped = build_adapters(eng)
        for k, n in skipped.items():
            self._bump("books_skipped", k, n)
        deltas: list[tuple[BookAdapter, BookCursor, _Delta]] = []
        for ad in adapters:
            cur = self._book(ad.key)
            d = self._read(ad, cur, ctx)
            if d.busy:
                self.c["lock_busy_skips"] += 1                # imleç ilerlemez → sonraki adımda kayıpsız
                self._bump("lock_busy_by_book", ad.key)
                continue
            deltas.append((ad, cur, d))
        # 1) kapanışlar (anlık görüntü gerekmez) — iki adım arasında açılıp kapananın girişi taslağa
        for ad, cur, d in deltas:
            ctx.phase_outcomes(ad, cur, d, sigmap)
        # 2) yeni açık pozisyonlar ve yeni karşı-olgusallar → taslak (gerçek birleştirme anahtarları kaybolmadan ÖNCE)
        for ad, cur, d in deltas:
            ctx.register_drafts(ad, cur, d, sigmap)
        # 3) karşı-olgusal revizyonları ve kaybolma (SUPERSEDED / EXPIRED / DROPPED)
        for ad, cur, d in deltas:
            ctx.phase_cf(ad, cur, d)
        # 4) geri doldurma kapanışları (yeni → eski)
        for ad, cur, d in deltas:
            ctx.phase_backfill(ad, cur, d, sigmap)
        # 5) taslaklar: bu adımın canlıları → eski canlılar → geri doldurma (yeni as_of önce)
        ctx.materialize_drafts()
        return self._flush(ctx)

    def _flush(self, ctx: "_StepCtx") -> dict[str, Any]:
        """Tek toplu yazım (bir fsync) → yazılan/tekrar satırların imleç işlemleri → döngü → imleç → durum.
        G/Ç hatasında (ya da DEGRADED blokajında) hiçbir imleç ilerlemez: iş sonraki adımda aynen yeniden denenir."""
        rows = [r for r, _c in ctx.out if r is not None]
        written = rejected = 0
        io_fail = blocked = False
        seen = self.store._seen                               # (2026-09-29) aynı paketin deposu: yazılan + tekrar kimlikler
        pre = {str(r.get("row_id")) for r in rows if str(r.get("row_id")) in seen}   # önceden yazılmış (tekrar) satırlar
        if rows:
            written, rejected = self.store.append_rows(rows)
            la = self.store.last_append or {}
            io_fail = int(la.get("io_error") or 0) > 0
            blocked = int(la.get("blocked_degraded") or 0) > 0
            if io_fail:
                self.c["write_errors"] += 1
                self.c["io_retry_steps"] += 1
                self.last_error_code = ("STORE_IO: %s" % (la.get("error") or "?"))[:200]
            self.c["rejected_rows"] += int(rejected)
        committed = n_commits = 0
        counted: set[str] = set()
        if not (io_fail or blocked):
            # (2026-09-29) G/Ç hatası / DEGRADED blokajında HİÇBİR imleç işlemi çalışmaz: eskiden daha önce yazılmış (tekrar)
            # bir satırın işlemi çalışıyordu — aynı defterde ondan ÖNCEKİ yazılamamış kaydın üzerinden `last_key`
            # ilerletip onu kaybettirebiliyordu. Sonraki adım hepsini aynen yeniden dener; tekrarlar orada işlenir.
            for row, commit in ctx.out:
                commit()
                n_commits += 1
                if row is None:
                    continue
                rid = str(row.get("row_id"))
                if rid in pre:
                    self.c["duplicate_rows"] += 1             # yeniden eşitleme / imleç kaybı: zararsız, sayılır
                elif rid in seen and rid not in counted:
                    counted.add(rid)
                    committed += 1
                    self._count_row(row)
        self.rows_last = int(written)
        try:
            self.store.rotate()
        except Exception as exc:  # noqa: BLE001
            self.c["errors_total"] += 1
            self.last_error_code = ("ROTATE: %s" % exc)[:200]
        self._prune(ctx.now_ms)
        self._save_cursor(commits=n_commits)
        try:
            self.disk_bytes = int(self.store.disk_bytes())
        except Exception:  # noqa: BLE001
            pass
        return {"rows": int(written), "rejected": int(rejected), "committed": committed, "drafts": len(self._drafts),
                "io_error": io_fail}

    def _count_row(self, row: Mapping[str, Any]) -> None:
        self.c["rows_total"] += 1
        self._bump("rows_by_kind", str(row.get("kind")))
        self._bump("rows_by_book", str(row.get("book")))
        ss = row.get("snapshot_status")
        if ss is not None:
            self._bump("snapshot_status_mix", str(ss))
        if row.get("kind") == R.KIND_CF and row.get("status") == R.LABELLED:
            rb = row.get("r_basis")
            if rb == R.GROSS_LEGACY:
                self.c["legacy_gross_cf"] += 1
            elif rb == R.UNKNOWN_LABEL_VERSION:
                self.c["unknown_label_version"] += 1

    def _prune(self, now_ms: int | None = None) -> None:
        """Bellek sınırı: bellek içi parmak izleri imleçteki bilinen kimliklere indirgenir; mezar taşları ve ana bot
        sinyal eşlemleri süre + adet tavanıyla budanır (2026-09-29)."""
        for key, memq in list(self._memq.items()):
            cur = self._books.get(key)
            known = cur.cf_known if cur is not None else {}
            if len(memq) > len(known):
                for tid in [t for t in memq if t not in known]:
                    memq.pop(tid, None)
        if now_ms is None:
            return
        for cur in self._books.values():
            for dct, ttl, keep in ((cur.cf_gone, CF_GONE_TTL_MS, CF_GONE_KEEP), (cur.sig_tid, SIG_TID_TTL_MS, SIG_TID_KEEP)):
                if not dct:
                    continue
                for k in [k for k, v in dct.items() if not isinstance(v[1], (int, float)) or now_ms - int(v[1]) > ttl]:
                    dct.pop(k, None)
                if len(dct) > keep:
                    for k in sorted(dct, key=lambda x: dct[x][1])[:len(dct) - keep]:
                        dct.pop(k, None)

    # ------------------------------------------------------------------ okuma (kilit altında YALNIZ kopya; G/Ç yok)
    def _read(self, ad: BookAdapter, cur: BookCursor, ctx: "_StepCtx") -> _Delta:
        d = _Delta()
        try:
            lk = ad.lock()
        except Exception:  # noqa: BLE001
            lk = None
        if lk is not None:
            t = float(self.s.lock_timeout_s)
            got = lk.acquire(blocking=False) if t <= 0 else lk.acquire(timeout=t)
            if not got:
                d.busy = True
                return d
        try:
            self._read_ledger(ad, cur, d, ctx)
            if ad.cf_under_lock:
                self._read_cf(ad, cur, d, ctx)
        finally:
            if lk is not None:
                lk.release()
        if not ad.cf_under_lock:
            self._read_cf(ad, cur, d, ctx)                    # ana gölge defteri: yalnız ana iş parçacığı yazar
        return d

    def _read_ledger(self, ad: BookAdapter, cur: BookCursor, d: _Delta, ctx: "_StepCtx") -> None:
        led = ad.ledger()
        positions = getattr(led, "positions", None) if led is not None else None
        hist = getattr(led, "history", None) if led is not None else None
        if not isinstance(positions, Mapping) or not isinstance(hist, list):
            return
        d.ledger_ok = True
        key = ad.key

        def hk(r: Any) -> str | None:
            return _tkey(key, getattr(r, "id", None), getattr(r, "opened_at", None))
        open_keys: set[str] = set()
        for _sym, pos in list(positions.items()):
            tk = _tkey(key, getattr(pos, "id", None), getattr(pos, "opened_at", None))
            if tk is None:
                continue
            open_keys |= {tk}
            if tk in cur.entry_done or ("E", key, tk) in self._drafts:
                continue
            d.open_new.append((tk, _pos_facts(pos)))
        d.open_keys = open_keys
        n = len(hist)
        d.end_key = hk(hist[-1]) if n else None
        if not cur.first_seen:
            # ilk görüş: canlı sınır = katmanın DOĞUMU (2026-09-29). İlk okuma gecikse de (ilk adımda kilit meşgul, defter
            # sonradan eklendi, ilk turda kapanan işlem) doğumdan SONRA kapananlar canlı yoldan işlenir; öncesi (geri
            # doldurma açıksa) sondan başa. Eskiden sınır "şu anki son kayıt"tı: geri doldurma kapalıyken aradakiler
            # KAYBOLUYOR, açıkken geri doldurmaya düşüyordu.
            cur.first_seen = True
            start = n
            while start > 0:
                cm = _iso_ms(getattr(hist[start - 1], "closed_at", None))
                if cm is None or cm < ctx.born:
                    break
                start -= 1
            cur.last_key = hk(hist[start - 1]) if start > 0 else None
            cur.bf_next = cur.last_key if (ctx.bf_on and start) else None
            cur.bf_done = not (ctx.bf_on and start)
        elif cur.last_key is None:
            start = 0
        else:
            start = None
            for i in range(n - 1, -1, -1):
                if hk(hist[i]) == cur.last_key:
                    start = i + 1
                    break
            if start is None:
                # defter sıfırlandı / geri yüklendi: geçmiş yeniden eşitlenir (deterministik kimlik → tekrar zararsız)
                d.resync = True
                self.c["resyncs"] += 1
                cur.last_key = d.end_key
                cur.bf_next = d.end_key if n else None
                cur.bf_done = not n
                start = n
        tail0 = max(0, n - LEDGER_WINDOW)
        take = max(0, min(n - start, ctx.cap))
        for i in range(start, n):
            tk = hk(hist[i])
            if tk is None:
                continue
            d.closed_keys |= {tk}
            if i < start + take:
                d.closed_new.append((tk, _rec_facts(hist[i]), i >= tail0))
        d.live_more = (n - start) > take
        if cur.unfinal:
            idx: dict[str, int] = {}
            for i in range(tail0, n):
                tk = hk(hist[i])
                if tk is not None:
                    idx[tk] = i
            for tk in list(cur.unfinal):
                i = idx.get(tk)
                if i is None:
                    for j in range(tail0 - 1, -1, -1):
                        if hk(hist[j]) == tk:
                            i = j
                            break
                if i is None:
                    d.unfinal_missing.append(tk)
                elif i < start:
                    d.revised.append((tk, _rec_facts(hist[i]), i >= tail0))
        if not cur.bf_done and cur.bf_next is not None:
            j = None
            for i in range(n - 1, -1, -1):
                if hk(hist[i]) == cur.bf_next:
                    j = i
                    break
            if j is None:
                d.bf_exhausted = True
            else:
                # yalnız gerçekten okunacak kadar yer; kilit altında defter başına en çok BF_COPY_PER_BOOK (2026-09-29)
                k = ctx.bf_take(min(j + 1, max(1, ctx.cap // 2), BF_COPY_PER_BOOK))
                for i in range(j, max(-1, j - k), -1):
                    tk = hk(hist[i])
                    if tk is not None:
                        d.bf_records.append((tk, _rec_facts(hist[i]), i >= tail0, hk(hist[i - 1]) if i > 0 else None))

    def _read_cf(self, ad: BookAdapter, cur: BookCursor, d: _Delta, ctx: "_StepCtx") -> None:
        trades = ad.cf_trades()
        if not isinstance(trades, list):
            return                                            # kayıtçı yok (bu süreçte öğrenme açılmadı) → kaybolma YOK
        d.cf_ok = True
        memq = self._memq.setdefault(ad.key, {})
        flt = ad.cf_filter
        present: set[str] = set()
        bf_cand: list[tuple[int, int, Any]] = []
        for pos_i, t in enumerate(list(trades)):
            if flt is not None and not flt(t):
                continue
            tid = str(getattr(t, "id", "") or "")
            if not tid:
                continue
            present |= {tid}
            q = _cf_q(t)
            prev = cur.cf_known.get(tid)
            if prev is not None:
                if memq.get(tid, _MISSING) == q:
                    continue
                if prev[2] == IGNORED:
                    d.cf_q_only.append((tid, q))
                    continue
                o = getattr(t, "outcome", None)
                status = R.LABELLED if isinstance(o, Mapping) else R.PENDING
                fp = R.cf_fp(status, o if status == R.LABELLED else None)
                if fp == prev[1] and _CODE_ST.get(prev[2]) == status:
                    d.cf_q_only.append((tid, q))
                    continue
                d.cf_changed.append((tid, q, status, fp, _cf_facts(t)))
                continue
            created = _iso_ms(getattr(t, "created_at", None))
            if ("C", ad.key, tid) in self._drafts or (created is not None and created >= ctx.born):
                d.cf_new.append((tid, q, _cf_facts(t), created))
            else:
                bf_cand.append((created if created is not None else -1, pos_i, t))
        d.cf_present = present
        if not bf_cand:
            return
        if not ctx.bf_on:
            for _c, _i, t in bf_cand:                         # geri doldurma kapalı: kuruluştan önceki kayıtlar kayıt dışı
                tid = str(getattr(t, "id", ""))
                cur.cf_known[tid] = [-1, "", IGNORED, ORIG_BF]
                memq[tid] = _cf_q(t)
                self.c["ignored_backfill"] += 1
            return
        room = ctx.bf_take(min(len(bf_cand), BF_COPY_PER_BOOK))   # kilit altında kopya tavanı (2026-09-29)
        pick = heapq.nlargest(room, bf_cand, key=lambda x: (x[0], x[1])) if room > 0 else []
        for created, _i, t in pick:
            d.cf_new.append((str(getattr(t, "id", "")), _cf_q(t), _cf_facts(t), created if created >= 0 else None))
        d.cf_bf_left = len(bf_cand) - len(pick)


def _lazy_fetcher(eng: Any) -> Callable[[str, str, int], Any] | None:
    """İsteğe bağlı tembel çekim (varsayılan KAPALI: `lazy_fetch_max_per_tour: 0`). Motorun perp istemcisiyle salt okuma
    isteği (`fetch_ohlcv`); motorun karar durumuna yazmaz."""
    fut = getattr(eng, "_fut", None)
    if not callable(fut):
        return None

    def _f(symbol: str, tf: str, limit: int) -> Any:
        return fut().fetch_ohlcv("%s:USDT" % str(symbol).split(":")[0], tf, limit=int(limit))
    return _f


# ============================================================================ adım bağlamı (satır kurulumu, kilit DIŞINDA)
class _StepCtx:
    """Bir adımın iş kuyruğu: (satır | None, imleç işlemi) çiftleri (`out`), bütçe ve taslak kaydı (2026-09-29).

    İmleç yalnız `out`taki işlemlerle değişir; `_flush` bunları ancak satır depoya ulaştıysa (yazıldı ya da zaten vardı)
    çalıştırır. Böylece G/Ç hatası, DEGRADED ya da bütçe kesintisi hiçbir kaydı kaybettirmez."""

    def __init__(self, col: SharedExperienceCollector, *, env: dict, now: datetime, now_ms: int, born: int, bf_on: bool,
                 deadline: float, cap: int) -> None:
        self.col = col
        self.env = env
        self.now = now
        self.now_ms = now_ms
        self.born = born
        self.bf_on = bf_on
        self.deadline = deadline
        self.cap = cap
        self.bars: BarSource | None = None
        self.out: list[tuple[dict | None, Callable[[], None]]] = []
        self.n_rows = 0
        self.stopped = False
        self._bf_reserved = 0

    # -------------------------------------------------------------- bütçe
    def room(self) -> bool:
        if self.stopped:
            return False
        if self.n_rows >= self.cap:
            self.stopped = True
            self.col.c["row_cap_hits"] += 1
            return False
        if time.perf_counter() >= self.deadline:
            self.stopped = True
            self.col.c["budget_hits"] += 1
            return False
        return True

    def bf_take(self, want: int) -> int:
        """Geri doldurma taslağı için yer ayırır (taslak tavanının yarısı canlı işe kalır). Döner: verilen adet."""
        n_bf = sum(1 for dr in self.col._drafts.values() if dr.origin != R.LIVE)
        free = max(0, self.col.max_drafts // 2 - n_bf - self._bf_reserved)
        got = max(0, min(int(want), free))
        self._bf_reserved += got
        return got

    def emit(self, row: dict | None, commit: Callable[[], None]) -> None:
        self.out.append((row, commit))
        if row is not None:
            self.n_rows += 1

    def _entry_origin(self, opened_ms: int | None, closed_ms: int | None) -> str:
        if closed_ms is not None and closed_ms < self.born:
            return R.BACKFILL_HISTORY
        if opened_ms is not None and opened_ms < self.born:
            return R.BACKFILL_OPEN
        return R.LIVE

    def _outcome_origin(self, closed_ms: int | None) -> str:
        return R.BACKFILL_HISTORY if (closed_ms is not None and closed_ms < self.born) else R.LIVE

    # -------------------------------------------------------------- 1) kapanışlar
    def phase_outcomes(self, ad: BookAdapter, cur: BookCursor, d: _Delta, sigmap: Mapping[str, str]) -> None:
        col = self.col
        for tk in d.unfinal_missing:
            col.c["unfinal_lost"] += 1
            self.emit(None, _pop_unfinal(cur, tk))
        for tk, f, in_window in d.revised:
            if not self.room():
                return
            prev = cur.unfinal.get(tk)
            if prev is not None:
                origin = R.BACKFILL_HISTORY if (len(prev) > 2 and prev[2] == ORIG_BF) else R.LIVE
                self._outcome(ad, cur, tk, f, in_window, prev=prev, origin=origin, advance=False, sigmap=sigmap)
        for tk, f, in_window in d.closed_new:
            if not self.room():
                return
            if cur.entry_done.get(tk) == IGNORED:                # kuruluştan önce açılmış + geri doldurma kapalı
                self.emit(None, _advance(cur, tk))
                continue
            opened_ms, closed_ms = _iso_ms(f.get("opened_at")), _iso_ms(f.get("closed_at"))
            if tk not in cur.entry_done and ("E", ad.key, tk) not in col._drafts:
                eo = self._entry_origin(opened_ms, closed_ms)
                if not self.bf_on and eo != R.LIVE:          # kuruluştan önce açılmış (hiç açık görülmedi) + geri doldurma kapalı
                    col.c["ignored_backfill"] += 1
                    self.emit(None, _advance(cur, tk))
                    continue
                # iki adım arasında açılıp kapandı (Box, 60 sn izleyici): giriş TradeRecord'dan, kapanıştan hemen önce
                self._make_entry_draft(ad, cur, tk, dict(f), eo, sigmap)
            self._outcome(ad, cur, tk, f, in_window, prev=cur.unfinal.get(tk), origin=self._outcome_origin(closed_ms),
                          advance=True, sigmap=sigmap)

    def _main_sig(self, cur: BookCursor, tk: str, f: Mapping[str, Any], sigmap: Mapping[str, str]) -> str | None:
        tid = str(f.get("id"))
        pend = cur.sig_tid.get(tid)
        return cur.sig.get(tk) or sigmap.get(tid) or (str(pend[0]) if pend else None)

    def _outcome(self, ad: BookAdapter, cur: BookCursor, tk: str, f: dict, in_window: bool, *, prev: list | None,
                 origin: str, advance: bool, sigmap: Mapping[str, str]) -> None:
        final = R.outcome_is_final(f, now=self.now, in_window=bool(in_window))
        fp = "%s|%s" % (R.outcome_fp(f), "F" if final else "U")
        rev = R.next_rev({"rev": prev[0], "fp": prev[1]} if prev else None, fp)
        ocode = prev[2] if (prev and len(prev) > 2) else (ORIG_BF if origin != R.LIVE else ORIG_LIVE)
        adv = _advance(cur, tk) if advance else None
        if rev is None:
            if adv is not None:
                self.emit(None, adv)
            return
        if ad.book_type == R.MAIN:
            sk = self._main_sig(cur, tk, f, sigmap)
            src = "ENGINE_SIGNAL_ID" if sk else None
        else:
            sk, src = real_signal_key(ad, f)
        try:
            row = R.outcome_row(f, book=ad.key, rev=int(rev), final=bool(final), env=self.env, origin=origin,
                                signal_key=sk, signal_key_src=src)
        except R.RowError as exc:
            self.col._row_error(exc)
            self.emit(None, _chain(_pop_unfinal(cur, tk), adv))
            return
        self.emit(row, _chain(_set_unfinal(cur, tk, int(rev), fp, ocode, final), adv))

    # -------------------------------------------------------------- 2) taslak kaydı
    def _make_entry_draft(self, ad: BookAdapter, cur: BookCursor, tk: str, facts: dict, origin: str,
                          sigmap: Mapping[str, str]) -> None:
        col = self.col
        if not self.bf_on and origin != R.LIVE:
            cur.entry_done[tk] = IGNORED                         # geri doldurma kapalı: kuruluştan önceki işlem kayıt dışı
            col.c["ignored_backfill"] += 1
            return
        if ad.book_type == R.MAIN:
            sk = self._main_sig(cur, tk, facts, sigmap)
            src = "ENGINE_SIGNAL_ID" if sk else None
            if sk:
                cur.sig[tk] = sk
                cur.sig_tid.pop(str(facts.get("id")), None)   # tüketildi: artık `sig[tk]` taşır
            else:
                col.c["main_entry_no_sigkey"] += 1            # (2026-09-29) anahtarsız ana giriş SAYILIR
        else:
            sk, src = real_signal_key(ad, facts)
        jk = _real_join_key(ad, facts, sk)
        if jk and jk not in cur.real_jk:
            cur.real_jk.append(jk)                            # aynı adımdaki kaybolma SUPERSEDED olarak sınıflansın
        col._drafts[("E", ad.key, tk)] = _Draft(kind="E", book=ad.key, src_key=tk, symbol=str(facts.get("symbol") or ""),
                                                 as_of_ms=_iso_ms(facts.get("opened_at")), origin=origin, facts=facts,
                                                 sig=sk, sig_src=src, born_step=col.steps,
                                                 closed=bool(facts.get("closed_at")))

    def register_drafts(self, ad: BookAdapter, cur: BookCursor, d: _Delta, sigmap: Mapping[str, str]) -> None:
        col = self.col
        for tk, facts in d.open_new:
            if ("E", ad.key, tk) in col._drafts or tk in cur.entry_done:
                continue
            self._make_entry_draft(ad, cur, tk, facts, self._entry_origin(_iso_ms(facts.get("opened_at")), None), sigmap)
        if d.ledger_ok:
            # giriş işaretleri: açık ya da kapanışı henüz işlenmemiş olanlar KALIR, diğerleri budanır
            keep = d.open_keys | d.closed_keys
            for tk in [t for t in cur.entry_done if t not in keep]:
                cur.entry_done.pop(tk, None)
            if cur.sig:
                pend = {dr.src_key for dr in col._drafts.values() if dr.book == ad.key and dr.kind == "E"}
                for tk in [t for t in cur.sig if t not in keep and t not in pend]:
                    cur.sig.pop(tk, None)
        if not d.cf_ok:
            return
        for tid, q, facts, created in d.cf_new:
            key = ("C", ad.key, tid)
            dr = col._drafts.get(key)
            if dr is not None:
                dr.facts, dr.q = facts, q                     # en güncel kopya (etiketlendiyse rev 0 sonucu taşır)
                continue
            origin = R.LIVE if (created is not None and created >= self.born) else R.BACKFILL_CF
            col._drafts[key] = _Draft(kind="C", book=ad.key, src_key=tid, symbol=str(facts.get("symbol") or ""),
                                      as_of_ms=created, origin=origin, facts=facts, q=q, born_step=col.steps)

    # -------------------------------------------------------------- 3) karşı-olgusal revizyonları ve kaybolma
    def phase_cf(self, ad: BookAdapter, cur: BookCursor, d: _Delta) -> None:
        col = self.col
        memq = col._memq.setdefault(ad.key, {})
        for tid, q in d.cf_q_only:
            memq[tid] = q
        for tid, q, status, fp, facts in d.cf_changed:
            if not self.room():
                return
            prev = cur.cf_known.get(tid)
            if prev is None:
                continue
            ocode = prev[3] if len(prev) > 3 else ORIG_LIVE
            rev = R.next_rev({"rev": prev[0], "fp": prev[1]}, fp)
            if rev is None:
                memq[tid] = q
                continue
            try:
                row = R.cf_row(facts, book=ad.key, rev=int(rev), status=status, env=self.env,
                               origin=(R.BACKFILL_CF if ocode == ORIG_BF else R.LIVE))
            except R.RowError as exc:
                col._row_error(exc)
                cur.cf_known[tid] = [int(prev[0]), prev[1], IGNORED, ocode]
                col._dirty = True                             # mevcut anahtarın değeri değişti (uzunluk aynı)
                memq[tid] = q
                continue
            self.emit(row, _set_cf(cur, memq, tid, _cf_entry(int(rev), row["fp"], status, ocode, facts, ad.book_type), q))
        if not d.cf_ok:
            return
        real = set(cur.real_jk)
        for tid in [t for t in cur.cf_known if t not in d.cf_present]:
            if not self.room():
                return
            prev = cur.cf_known[tid]
            st = _CODE_ST.get(prev[2])
            ext = prev[4:11] if len(prev) >= 11 else None
            vst = None
            if st is not None:
                jk = None
                if ext is not None:
                    try:
                        jk = R.join_key(book=ad.key, signal_key=ext[2], symbol=ext[0], side=ext[1], variation=ext[3])
                    except R.RowError:
                        jk = None
                vst = R.classify_vanished(st, superseded_by_real=bool(jk and jk in real), now=self.now,
                                          label_ts=(ext[5] if ext is not None else None),
                                          tf_minutes=(ext[6] if ext is not None else None))
            if vst is None or ext is None:
                # etiketli kaydın arşiv budaması ya da kayıt dışı işaret: revizyon YOK, yalnız unutulur
                self.emit(None, _forget_cf(cur, memq, tid, int(prev[0]), self.now_ms))
                continue
            src = {"id": tid, "book": ad.book_name, "symbol": ext[0], "direction": ext[1], "signal_key": ext[2],
                   "variation": ext[3], "market_type": R.MARKET, "label_ts": ext[5], "tf_minutes": ext[6],
                   "features": {("family" if ad.book_type == R.PATTERN else "setup_type"): ext[4]}}
            ocode = prev[3] if len(prev) > 3 else ORIG_LIVE
            try:
                row = R.cf_row(src, book=ad.key, rev=int(prev[0]) + 1, status=vst, env=self.env,
                               origin=(R.BACKFILL_CF if ocode == ORIG_BF else R.LIVE))
            except R.RowError as exc:
                col._row_error(exc)
                self.emit(None, _forget_cf(cur, memq, tid, int(prev[0]), self.now_ms))
                continue
            self.emit(row, _chain(_forget_cf(cur, memq, tid, int(prev[0]) + 1, self.now_ms), _count_vanished(col, vst)))

    # -------------------------------------------------------------- 4) geri doldurma
    def phase_backfill(self, ad: BookAdapter, cur: BookCursor, d: _Delta, sigmap: Mapping[str, str]) -> None:
        """Geri doldurma (yeni → eski): kapanış satırı hemen, giriş taslağa (en düşük öncelik)."""
        if d.bf_exhausted:
            cur.bf_done, cur.bf_next = True, None             # imleç anahtarı defterin başından budanmış
            return
        for tk, f, in_window, nxt in d.bf_records:
            if not self.room():
                return
            opened_ms, closed_ms = _iso_ms(f.get("opened_at")), _iso_ms(f.get("closed_at"))
            origin = self._outcome_origin(closed_ms)
            if not self.bf_on and origin != R.LIVE:
                self.emit(None, _bf_advance(cur, nxt))        # yeniden eşitleme + geri doldurma kapalı: eski kayıt atlanır
                continue
            if tk not in cur.entry_done and ("E", ad.key, tk) not in self.col._drafts:
                self._make_entry_draft(ad, cur, tk, dict(f), self._entry_origin(opened_ms, closed_ms), sigmap)
            self._outcome(ad, cur, tk, f, in_window, prev=cur.unfinal.get(tk), origin=origin, advance=False,
                          sigmap=sigmap)
            self.emit(None, _bf_advance(cur, nxt))

    # -------------------------------------------------------------- 5) anlık görüntü
    def materialize_drafts(self) -> None:
        col = self.col
        if not col._drafts:
            return
        items = list(col._drafts.items())

        def _prio(kv: tuple) -> tuple:
            dr = kv[1]
            if dr.origin == R.LIVE:
                return (0 if dr.born_step == col.steps else 1, dr.as_of_ms or 0)
            return (2, -(dr.as_of_ms or 0))
        items.sort(key=_prio)
        over = len(items) - col.max_drafts
        force: set = set()
        if over > 0:                                          # tavan aşıldı: en eski taslaklar mevcut durumla yazılır
            force = {k for k, _dr in sorted(items, key=lambda kv: (kv[1].as_of_ms or 0))[:over]}
            items = [kv for kv in items if kv[0] in force] + [kv for kv in items if kv[0] not in force]
        for key, dr in items:
            if not self.room():
                break
            forced = key in force
            try:
                res = self._snapshot(dr, force=forced)
            except Exception as exc:  # noqa: BLE001 — beklenmeyen hata: taslak kayıt dışı kalır (sonsuz deneme yok)
                col.c["errors_total"] += 1
                col.last_error_code = ("DRAFT: %s" % exc)[:200]
                self._drop_draft(key, dr)
                continue
            if res is None:
                dr.lag += 1
                continue
            if forced:
                col.c["drafts_forced"] += 1
            self._emit_draft(key, dr, *res)

    def _snapshot(self, dr: _Draft, *, force: bool) -> tuple | None:
        """(anlık görüntü, durum, meta) ya da None (TASLAK kalır). Tam değilse canlı satır yaş sınırına kadar bekler;
        geri doldurma/zorlanan satır mevcut durumla (GAP / NO_BARS / PARTIAL) hemen yazılır."""
        col = self.col
        meta: dict[str, Any] = {"source": None, "lag_steps": int(dr.lag), "computed_at": iso(self.now)}
        if dr.as_of_ms is None or not dr.symbol:
            return None, "ERROR", dict(meta, error="AS_OF_MISSING")
        h4 = h1 = btc = None
        try:
            bars = self.bars
            h4 = bars.window(dr.symbol, S.TF_H4, as_of_ms=dr.as_of_ms, W=S.W4H)
            h1 = bars.window(dr.symbol, S.TF_H1, as_of_ms=dr.as_of_ms, W=S.W1H)
            btc = h4 if S.is_btc_symbol(dr.symbol) else bars.window(BTC, S.TF_H4, as_of_ms=dr.as_of_ms, W=S.WBTC)
            complete = all(S.is_complete(w, dr.as_of_ms) for w in (h4, h1, btc))
            if not complete and not force and dr.origin == R.LIVE:
                if (self.now_ms - dr.as_of_ms) / 3_600_000.0 < float(col.s.pending_max_age_h):
                    return None                               # PENDING: sonraki adımda yeniden
                col.c["drafts_expired"] += 1
            core = col.cache.get_or_compute(dr.symbol, h4, h1, btc) if complete else None
            snap = S.snapshot(dr.symbol, h4, h1, btc, dr.as_of_ms, core=core)
            meta["source"] = {"h4": h4.source, "h1": h1.source, "btc": btc.source}
            return snap, snap.get("status"), meta
        except Exception as exc:  # noqa: BLE001 — hesap hatası: ERROR anlık görüntüsü (sıfır yok), sayılır
            col.c["snapshot_errors"] += 1
            col.last_error_code = ("SNAPSHOT: %s" % exc)[:200]
            try:
                snap = S.error_snapshot(dr.symbol, dr.as_of_ms, h4=h4, h1=h1, btc=btc)
            except Exception:  # noqa: BLE001
                snap = None
            return snap, "ERROR", dict(meta, error=str(exc)[:200])

    def _drop_draft(self, key: tuple, dr: _Draft) -> None:
        col = self.col
        col._drafts.pop(key, None)
        cur = col._book(dr.book)
        if dr.kind == "E":
            cur.entry_done[dr.src_key] = IGNORED
        else:
            cur.cf_known[dr.src_key] = [-1, "", IGNORED, ORIG_LIVE if dr.origin == R.LIVE else ORIG_BF]

    def _emit_draft(self, key: tuple, dr: _Draft, snap: Any, sst: str, meta: dict) -> None:
        col = self.col
        cur = col._book(dr.book)
        try:
            if dr.kind == "E":
                row = R.entry_row(dr.facts, book=dr.book, env=self.env, origin=dr.origin, signal_key=dr.sig,
                                  signal_key_src=dr.sig_src, snapshot=snap, snapshot_status=sst, snapshot_meta=meta)
            else:
                status = R.LABELLED if isinstance(dr.facts.get("outcome"), Mapping) else R.PENDING
                # MEZAR TAŞI (2026-09-29): aynı deterministik kimlik kaybolduktan sonra yeniden kaydedildiyse revizyon
                # kaldığı yerden sürer (yeni rev 0/1 eski satırların `row_id`siyle çakışıp tekrar sayılıyordu)
                gone = cur.cf_gone.get(dr.src_key)
                rev0 = int(gone[0]) + 1 if gone and int(gone[0]) >= 0 else 0
                row = R.cf_row(dr.facts, book=dr.book, rev=rev0, status=status, env=self.env, origin=dr.origin,
                               snapshot=snap, snapshot_status=sst, snapshot_meta=meta)
        except R.RowError as exc:
            col._row_error(exc)
            self._drop_draft(key, dr)
            return
        if dr.kind == "E":
            self.emit(row, _commit_entry_draft(col, cur, key, dr.src_key))
        else:
            ent = _cf_entry(rev0, row["fp"], status, ORIG_LIVE if dr.origin == R.LIVE else ORIG_BF, dr.facts,
                            R.BOOKS[dr.book][1])
            self.emit(row, _chain(_pop_draft(col, key), _set_cf(cur, col._memq.setdefault(dr.book, {}), dr.src_key,
                                                                 ent, dr.q), _reappeared(col, cur, dr.src_key) if rev0 else None))


# ============================================================================ imleç işlemleri (yalnız commit'te)
def _chain(*fns: Callable[[], None] | None) -> Callable[[], None]:
    def _run() -> None:
        for fn in fns:
            if fn is not None:
                fn()
    return _run


def _advance(cur: BookCursor, tk: str) -> Callable[[], None]:
    def _f() -> None:
        cur.entry_done.pop(tk, None)
        cur.last_key = tk
        cur.n_seen += 1
    return _f


def _bf_advance(cur: BookCursor, nxt: str | None) -> Callable[[], None]:
    def _f() -> None:
        cur.bf_next = nxt
        if nxt is None:
            cur.bf_done = True
    return _f


def _pop_unfinal(cur: BookCursor, tk: str) -> Callable[[], None]:
    def _f() -> None:
        cur.unfinal.pop(tk, None)
    return _f


def _set_unfinal(cur: BookCursor, tk: str, rev: int, fp: str, ocode: str, final: bool) -> Callable[[], None]:
    def _f() -> None:
        if final:
            cur.unfinal.pop(tk, None)
        else:
            cur.unfinal[tk] = [int(rev), fp, ocode]
    return _f


def _set_cf(cur: BookCursor, memq: dict, tid: str, ent: list, q: Any) -> Callable[[], None]:
    def _f() -> None:
        cur.cf_known[tid] = ent
        memq[tid] = q
    return _f


def _forget_cf(cur: BookCursor, memq: dict, tid: str, last_rev: int = -1, now_ms: int = 0) -> Callable[[], None]:
    def _f() -> None:
        cur.cf_known.pop(tid, None)
        memq.pop(tid, None)
        if last_rev >= 0:                                 # (2026-09-29) mezar taşı: yeniden kayıtta revizyon sürer
            cur.cf_gone[tid] = [int(last_rev), int(now_ms)]
    return _f


def _reappeared(col: SharedExperienceCollector, cur: BookCursor, tid: str) -> Callable[[], None]:
    def _f() -> None:
        cur.cf_gone.pop(tid, None)
        col.c["cf_reappeared"] += 1
    return _f


def _count_vanished(col: SharedExperienceCollector, status: str) -> Callable[[], None]:
    def _f() -> None:
        col._bump("vanished_by_status", status)
    return _f


def _pop_draft(col: SharedExperienceCollector, key: tuple) -> Callable[[], None]:
    def _f() -> None:
        col._drafts.pop(key, None)
    return _f


def _commit_entry_draft(col: SharedExperienceCollector, cur: BookCursor, key: tuple, tk: str) -> Callable[[], None]:
    def _f() -> None:
        col._drafts.pop(key, None)
        cur.entry_done[tk] = 1
    return _f


def _cf_entry(rev: int, fp: str, status: str, ocode: str, facts: Mapping[str, Any], book_type: str | None) -> list:
    """İmleç kaydı: etiketli → [rev, fp, durum, köken]; bekleyen → + kaybolma satırı için asgari alanlar
    [sembol, yön, sinyal, varyasyon, kurulum, label_ts, tf_minutes]."""
    ent: list = [int(rev), str(fp), _ST_CODE.get(status, IGNORED), ocode]
    if status == R.PENDING:
        f = facts.get("features") if isinstance(facts.get("features"), Mapping) else {}
        setup = f.get("family") if book_type == R.PATTERN else f.get("setup_type")
        ent += [facts.get("symbol"), _ev(facts.get("direction")), facts.get("signal_key") or facts.get("plan_id"),
                facts.get("variation"), setup, facts.get("label_ts"), facts.get("tf_minutes")]
    return ent


# ============================================================================ bekleyen giriş taslağı (imleçte; 2026-09-29)
def _enc(x: Any) -> Any:
    """JSON'a kayıpsız: Decimal → {"$d": metin} (satır kurulumu kesin aritmetik kullanır), diğerleri aynen/metin."""
    if isinstance(x, Decimal):
        return {"$d": str(x)}
    if isinstance(x, Mapping):
        return {str(k): _enc(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_enc(v) for v in x]
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    return str(x)


def _dec_doc(x: Any) -> Any:
    if isinstance(x, Mapping):
        if set(x) == {"$d"}:
            try:
                return Decimal(str(x["$d"]))
            except (ArithmeticError, ValueError):
                return None
        return {str(k): _dec_doc(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_dec_doc(v) for v in x]
    return x


def _draft_to_doc(dr: _Draft) -> dict[str, Any]:
    return {"symbol": dr.symbol, "as_of_ms": dr.as_of_ms, "origin": dr.origin, "sig": dr.sig, "sig_src": dr.sig_src,
            "lag": int(dr.lag), "facts": _enc(dr.facts)}


def _draft_from_doc(book: str, tk: str, d: Mapping[str, Any]) -> _Draft | None:
    facts = _dec_doc(d.get("facts"))
    if not isinstance(facts, dict) or not facts.get("closed_at") or d.get("origin") not in R.ORIGINS:
        return None
    as_of = d.get("as_of_ms")
    return _Draft(kind="E", book=book, src_key=str(tk), symbol=str(d.get("symbol") or ""),
                  as_of_ms=int(as_of) if isinstance(as_of, int) and not isinstance(as_of, bool) else None,
                  origin=str(d.get("origin")), facts=facts, sig=d.get("sig"), sig_src=d.get("sig_src"),
                  lag=int(d.get("lag") or 0), born_step=0, closed=True)


__all__ = ["BookAdapter", "BookCursor", "CURSOR_FILE", "CURSOR_SCHEMA", "MODE_RECORD", "STATE_BREAKER", "STATE_OK",
           "STATUS_FILE", "STATUS_SCHEMA", "SharedExperienceCollector", "XpSettings", "build_adapters",
           "main_signal_keys", "real_signal_key"]

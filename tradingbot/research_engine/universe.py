"""Araştırma evreni U_R ve günlük evren / `exchangeInfo` anlık görüntüsü (§3.3; P1b). Ağsızdır: `exchangeInfo`'yu
veri birimi (`datastore`) REST korumasıyla çeker ve buraya yalnız sözlük olarak verir.

U_R (belge §3.3 tablosu, AYNEN; her sembolün neden evrende olduğu `roles` alanında yazılır):

* USDⓈ-M vadeli: 40 giriş evreni ∪ BTC/ETH ∪ son 180 günde herhangi bir defterin veya CF'nin işlem yaptığı semboller;
  5m, 15m, 1h, 4h, 1d, fonlama, `metrics_5m`, mark/premium 1h; listelenmeden (metrics 2021-12-01'den).
* Altın vadeli: XAUUSDT (2025-12-01'den), PAXGUSDT (2025-03-01'den); 1m–1d + fonlama.
* Altın spot: PAXGUSDT; 5m, 1h, 4h, 1d; 2020-08-01'den.
* Spot bağlam: BTCUSDT, ETHUSDT; 1h, 1d; 2020-01-01'den.
* Spot (ana bot): ana botun son 180 günde spot işlem yaptığı semboller; 1h (1m tembel, P2); son 400 gün.

**Okumalar:**
1. *Giriş evreni (40).* Belge "(40, `universe.json`)" der; ama `state/universe.json` borsadaki bütün uygun sembolleri
   (≈ 200) taşır, 40'lık giriş evreni ise `config.yaml → entry_universe.symbols`'tır (worker `frame_provenance.json`'a da
   yazar). Motor bu listeyi ham YAML'dan, YALNIZ `entry_universe.enabled: true` iken okur (worker da yalnız o zaman
   kullanır; 2026-10-06 inceleme düzeltmesi) (`rawconfig`, ihtiyaç listesine eklendi); okunamazsa
   `state/frame_provenance.json`'daki `entry_universe`'e düşer; ikisi de yoksa bileşen boş kalır ve `ENTRY_UNIVERSE_YOK`
   bayrağı yazılır (sessizce başka liste kullanılmaz). `state/universe.json` salt-okunur okunur ve günlük anlık
   görüntüye (uygunluk, eleme nedeni, yaş) kopyalanır — "günlük evren" budur.
2. *Altın vadeli 1m.* Tablo altın vadeliler için "1m–1d" der, 1m satırı ise "son 400 gün, tembel (P2)". İkisi birlikte:
   altın vadelilerin 1m serisi P1b'de `max(başlangıç, şimdi − 400 gün)`'den doldurulur; diğer sembollerin 1m'i P2'de
   tembeldir (U_R'ye girmez).
3. *İşlem görenler.* Defterler: P1a kapanış arşivi (`closes/<defter>/AAAA-AA.jsonl.gz`, rotasyondan bağımsız) +
   ledger `history[]` (kapanış zamanı) + açık pozisyonlar/lotlar; CF: `state/**/counterfactual_trades.json` ve ana
   botun `state/shadow_book.json`'u (`created_at`). Vadeli defterler ve CF'ler vadeli listeye, `main_spot` spot listeye
   gider. Hepsi `open(..., "r")` ile okunur.

Hayatta kalma yanlılığı (§3.3): U_R bugünün listesidir; kesitsel taktikler P3'teki zaman noktasında evrene kadar Kapı
A'ya girmez. Bu modül `config_v3`'ü / `load_config`'i import ETMEZ.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .ledgers import BOOK_MAIN_SPOT, KIND_SPOT, parse_ts, read_all, symbol_key
from .paths import EnginePaths, read_json_gz, read_jsonl_gz
from .rawconfig import read_raw_config
from .store import FUNDING, MARKPX_1H, METRICS_5M, PREMIUM_1H

UTC = timezone.utc
UNIVERSE_SCHEMA = "engine_universe_v1"
EXCHANGEINFO_SCHEMA = "engine_exchangeinfo_v1"

BTC_ETH: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
GOLD_FUTURES: dict[str, str] = {"XAUUSDT": "2025-12-01", "PAXGUSDT": "2025-03-01"}
GOLD_SPOT: dict[str, str] = {"PAXGUSDT": "2020-08-01"}
SPOT_CONTEXT: dict[str, str] = {"BTCUSDT": "2020-01-01", "ETHUSDT": "2020-01-01"}
FUT_TFS: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d")
GOLD_FUT_TFS: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
GOLD_SPOT_TFS: tuple[str, ...] = ("5m", "1h", "4h", "1d")
SPOT_CTX_TFS: tuple[str, ...] = ("1h", "1d")
MAIN_SPOT_TFS: tuple[str, ...] = ("1h",)
FUTURES_FLOOR = "2019-09-01"                 # Binance USDⓈ-M sürekli sözleşmelerinin arşiv başlangıcı
METRICS_FLOOR = "2021-12-01"                 # `daily/metrics` arşivinin başlangıcı (§3.3)
TRADED_DAYS = 180
MAIN_SPOT_DAYS = 400
GOLD_1M_DAYS = 400

ROLE_ENTRY, ROLE_BTC_ETH, ROLE_TRADED, ROLE_CF = "giris_evreni", "btc_eth", "islem_180g", "cf_180g"
ROLE_GOLD, ROLE_SPOT_CTX, ROLE_MAIN_SPOT = "altin", "spot_baglam", "ana_bot_spot"
F_ENTRY_MISSING = "ENTRY_UNIVERSE_YOK"

#: kind → (arşiv veri kümesi, aralık)
DATASETS: dict[str, tuple[str, str | None]] = {FUNDING: ("fundingRate", None), METRICS_5M: ("metrics", None),
                                               MARKPX_1H: ("markPriceKlines", "1h"),
                                               PREMIUM_1H: ("premiumIndexKlines", "1h")}


def day_ms(day: str) -> int:
    y, m, d = (int(x) for x in day[:10].split("-"))
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


@dataclass(frozen=True)
class SeriesSpec:
    """Planlanan bir seri. `start_ms` tabandır; `from_listing` doğruysa veri birimi başlangıcı sembolün listelenme anına
    (`exchangeInfo.onboardDate` ya da arşiv yoklaması) çeker: etkin başlangıç = max(taban, listelenme)."""

    market: str
    symbol: str
    kind: str
    start_ms: int
    from_listing: bool = False
    roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.market}/{self.symbol}/{self.kind}"

    @property
    def dataset(self) -> str:
        return DATASETS.get(self.kind, ("klines", None))[0]

    @property
    def interval(self) -> str | None:
        ds = DATASETS.get(self.kind)
        return ds[1] if ds else self.kind


# ============================================================================ kaynaklar
def _read_json_r(p: Path) -> Any:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def entry_universe(paths: EnginePaths, app_dir: Path | str) -> tuple[list[str], dict[str, Any]]:
    """40'lık giriş evreni (okuma 1). Dönen: (ham semboller, kaynak bilgisi). Liste YALNIZ `entry_universe.enabled`
    true iken giriş evrenidir (worker da öyle kullanır: `engine_v3` `_eu.symbols if _eu.enabled`; varsayılan false).
    Kapalıyken (ya da anahtar yoksa) config listesi KULLANILMAZ; worker'ın `frame_provenance.json`'u da o durumda boş
    liste yazar → bileşen boş ve `ENTRY_UNIVERSE_YOK` (işlem görenler yine U_R'ye girer)."""
    rc = read_raw_config(Path(app_dir) / "config.yaml")
    enabled = rc.values.get("entry_universe.enabled") if rc.ok else None
    syms = rc.values.get("entry_universe.symbols") if rc.ok else None
    info: dict[str, Any] = {"config_path": rc.path, "config_ok": rc.ok, "config_sha256": rc.sha256, "enabled": enabled}
    if syms and enabled is True:
        out = list(dict.fromkeys(symbol_key(s) for s in syms))
        info.update(source="config.yaml entry_universe.symbols", count=len(out))
        return out, info
    if syms:
        info["ignored"] = "config.yaml entry_universe.enabled true değil: liste giriş evreni değil (worker da kullanmaz)"
    fp = _read_json_r(paths.state / "frame_provenance.json")
    eu = fp.get("entry_universe") if isinstance(fp, dict) else None
    if isinstance(eu, list) and eu:
        out = list(dict.fromkeys(symbol_key(s) for s in eu if isinstance(s, str)))
        info.update(source="state/frame_provenance.json entry_universe", count=len(out),
                    generated_at=fp.get("generated_at"))
        return out, info
    info.update(source="none", count=0)
    return [], info


def universe_json_snapshot(paths: EnginePaths) -> dict[str, Any]:
    """`state/universe.json` → küçük anlık görüntü (salt-okunur). Yoksa `present=False`."""
    p = paths.state / "universe.json"
    try:
        with open(p, "rb") as fh:
            raw = fh.read()
    except OSError:
        return {"path": str(p), "present": False}
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        return {"path": str(p), "present": True, "sha256": hashlib.sha256(raw).hexdigest(), "error": str(exc)[:200]}
    ents = (d.get("merged") or d.get("entries") or d.get("symbols") or []) if isinstance(d, dict) else []
    rows = []
    for e in ents:
        if not isinstance(e, dict) or not e.get("symbol"):
            continue
        rows.append({"symbol": symbol_key(e.get("symbol")), "market_type": e.get("market_type"),
                     "eligible": bool(e.get("eligible", e.get("excluded_reason") is None)),
                     "excluded_reason": e.get("excluded_reason"), "status": e.get("status"),
                     "listing_age_days": e.get("listing_age_days")})
    return {"path": str(p), "present": True, "sha256": hashlib.sha256(raw).hexdigest(),
            "generated_at": d.get("generated_at") if isinstance(d, dict) else None, "entries": len(rows),
            "eligible": sum(1 for r in rows if r["eligible"]), "rows": rows}


def _add(out: dict[str, set[str]], sym: Any, why: str) -> None:
    k = symbol_key(sym)
    if k:
        out.setdefault(k, set()).add(why)


def traded_symbols(paths: EnginePaths, *, now: datetime, days: int = TRADED_DAYS) -> dict[str, dict[str, list[str]]]:
    """Son `days` günde işlem yapılan semboller (okuma 3). Dönen: {"futures": {sembol: [neden]}, "spot": {...}}."""
    since = now - timedelta(days=days)
    fut: dict[str, set[str]] = {}
    spot: dict[str, set[str]] = {}
    # (a) P1a kapanış arşivi — rotasyondan bağımsız
    months = set()
    d = since.replace(day=1)
    while d <= now:
        months.add(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    if paths.closes.is_dir():
        for bd in sorted(x for x in paths.closes.iterdir() if x.is_dir()):
            dest = spot if bd.name == BOOK_MAIN_SPOT else fut
            for seg in sorted(bd.glob("*.jsonl.gz")):
                if seg.name[:7] not in months:
                    continue
                try:
                    for row in read_jsonl_gz(seg):
                        proj = row.get("proj") or {}
                        ca = parse_ts(proj.get("closed_at"))
                        if row.get("row") == "close" and ca is not None and ca >= since:
                            _add(dest, proj.get("inst") or proj.get("symbol"), f"{ROLE_TRADED}:{bd.name}")
                except (OSError, ValueError):
                    continue
    # (b) ledger'lar (salt-okunur): kapanmış kayıtlar + açık pozisyon/lotlar
    for book, lr in read_all(paths.state).items():
        if not lr.ok or not isinstance(lr.doc, dict):
            continue
        dest = spot if lr.kind == KIND_SPOT else fut
        for h in lr.doc.get("history") or []:
            ca = parse_ts((h or {}).get("closed_at")) if isinstance(h, dict) else None
            if ca is not None and ca >= since:
                _add(dest, h.get("symbol"), f"{ROLE_TRADED}:{book}")
        held = lr.doc.get("lots") if lr.kind == KIND_SPOT else lr.doc.get("positions")
        if isinstance(held, dict):
            for sym, v in held.items():
                if v:
                    _add(dest, (v.get("symbol") if isinstance(v, dict) else None) or sym, f"{ROLE_TRADED}:{book}:acik")
    # (c) karşı-olgusallar (CF) ve ana botun gölge defteri
    cf_files = sorted(set(paths.state.glob("*/counterfactual_trades.json")) |
                      {p for p in (paths.state / "counterfactual_trades.json", paths.state / "shadow_book.json")
                       if p.exists()})
    for p in cf_files:
        doc = _read_json_r(p)
        for t in (doc.get("trades") or []) if isinstance(doc, dict) else []:
            if not isinstance(t, dict):
                continue
            ca = parse_ts(t.get("created_at"))
            if ca is None or ca < since:
                continue
            dest = spot if str(t.get("market_type") or "").lower() == "spot" else fut
            _add(dest, t.get("symbol"), f"{ROLE_CF}:{p.parent.name if p.parent != paths.state else 'main'}")
    return {"futures": {k: sorted(v) for k, v in sorted(fut.items())},
            "spot": {k: sorted(v) for k, v in sorted(spot.items())}}


# ============================================================================ evren ve plan
def build_universe(paths: EnginePaths, *, now: datetime, app_dir: Path | str) -> dict[str, Any]:
    """U_R belgesi (rol bazlı). Plan `plan_series` ile seriye açılır."""
    entry, entry_info = entry_universe(paths, app_dir)
    traded = traded_symbols(paths, now=now)
    fut: dict[str, set[str]] = {}
    for s in entry:
        fut.setdefault(s, set()).add(ROLE_ENTRY)
    for s in BTC_ETH:
        fut.setdefault(s, set()).add(ROLE_BTC_ETH)
    for s, why in traded["futures"].items():
        fut.setdefault(s, set()).update(w.split(":", 1)[0] for w in why)
    gold_f = {s: {ROLE_GOLD} for s in GOLD_FUTURES}
    for s in list(fut):
        if s in GOLD_FUTURES:                        # altın vadeliler kendi satırıyla (1m–1d + fonlama) planlanır
            gold_f[s] |= fut.pop(s)
    main_spot = {s: {ROLE_MAIN_SPOT} for s in traded["spot"]}
    flags = [] if entry else [F_ENTRY_MISSING]
    return {"schema": UNIVERSE_SCHEMA, "day": now.strftime("%Y-%m-%d"), "generated_at": now.astimezone(UTC).isoformat(),
            "entry_universe": entry_info, "entry_symbols": entry,
            "futures": {s: sorted(r) for s, r in sorted(fut.items())},
            "gold_futures": {s: sorted(r) for s, r in sorted(gold_f.items())},
            "gold_spot": {s: [ROLE_GOLD] for s in GOLD_SPOT}, "spot_context": {s: [ROLE_SPOT_CTX] for s in SPOT_CONTEXT},
            "main_spot": {s: sorted(r) for s, r in sorted(main_spot.items())},
            "traded": traded, "flags": flags,
            "survivorship_tr": "U_R bugünün listesidir (delist olmuşlar yok); kesitsel taktikler P3 zaman noktasında "
                               "evrene kadar Kapı A'ya giremez (§3.3)."}


def plan_series(doc: dict[str, Any], *, now: datetime) -> list[SeriesSpec]:
    """U_R belgesinden seri planı (§3.3 tablosu). Sıra: vadeli → altın vadeli → spot; aynı anahtar bir kez."""
    now_ms = int(now.timestamp() * 1000)
    fl = day_ms(FUTURES_FLOOR)
    out: dict[str, SeriesSpec] = {}

    def add(spec: SeriesSpec) -> None:
        out.setdefault(spec.key, spec)

    for s, roles in (doc.get("futures") or {}).items():
        r = tuple(roles)
        for tf in FUT_TFS:
            add(SeriesSpec("futures", s, tf, fl, True, r))
        add(SeriesSpec("futures", s, FUNDING, fl, True, r))
        add(SeriesSpec("futures", s, METRICS_5M, day_ms(METRICS_FLOOR), True, r))
        add(SeriesSpec("futures", s, MARKPX_1H, fl, True, r))
        add(SeriesSpec("futures", s, PREMIUM_1H, fl, True, r))
    for s, roles in (doc.get("gold_futures") or {}).items():
        st = day_ms(GOLD_FUTURES.get(s, FUTURES_FLOOR))
        r = tuple(roles)
        for tf in GOLD_FUT_TFS:
            a = max(st, now_ms - GOLD_1M_DAYS * 86_400_000) if tf == "1m" else st
            add(SeriesSpec("futures", s, tf, a, True, r))
        add(SeriesSpec("futures", s, FUNDING, st, True, r))
    for s, roles in (doc.get("gold_spot") or {}).items():
        for tf in GOLD_SPOT_TFS:
            add(SeriesSpec("spot", s, tf, day_ms(GOLD_SPOT.get(s, "2020-08-01")), False, tuple(roles)))
    for s, roles in (doc.get("spot_context") or {}).items():
        for tf in SPOT_CTX_TFS:
            add(SeriesSpec("spot", s, tf, day_ms(SPOT_CONTEXT.get(s, "2020-01-01")), False, tuple(roles)))
    ms0 = now_ms - MAIN_SPOT_DAYS * 86_400_000
    ms0 -= ms0 % 86_400_000
    for s, roles in (doc.get("main_spot") or {}).items():
        for tf in MAIN_SPOT_TFS:
            k = f"spot/{s}/{tf}"
            if k in out:                                # bağlam/altın serisi zaten daha eskiden başlıyor
                out[k] = SeriesSpec("spot", s, tf, out[k].start_ms, False, tuple(sorted(set(out[k].roles) | set(roles))))
            else:
                add(SeriesSpec("spot", s, tf, ms0, False, tuple(roles)))
    return list(out.values())


def snapshot_doc(doc: dict[str, Any], plan: Iterable[SeriesSpec], uj: dict[str, Any]) -> dict[str, Any]:
    """`universe/AAAA-AA-GG.json` içeriği: rol bazlı U_R, seri anahtarları ve `state/universe.json`'un günlük kopyası."""
    keys = [s.key for s in plan]
    return {**doc, "series_count": len(keys), "series": keys, "universe_json": uj}


def write_universe_snapshot(paths: EnginePaths, doc: dict[str, Any]) -> Path:
    p = paths.universe_dir / f"{doc['day']}.json"
    paths.write_json(p, doc)
    return p


def compact_exchange_info(futures: list[dict] | None, spot: list[dict] | None) -> dict[str, Any]:
    """REST `exchangeInfo` → küçük listeleme/delist görüntüsü (sembol, durum, sözleşme türü, onboard/delivery)."""
    def _f(r: dict) -> dict:
        return {"symbol": r.get("symbol"), "status": r.get("status"), "contractType": r.get("contractType"),
                "onboardDate": r.get("onboardDate"), "deliveryDate": r.get("deliveryDate"),
                "quoteAsset": r.get("quoteAsset"), "underlyingType": r.get("underlyingType")}

    def _s(r: dict) -> dict:
        return {"symbol": r.get("symbol"), "status": r.get("status"), "baseAsset": r.get("baseAsset"),
                "quoteAsset": r.get("quoteAsset")}
    return {"schema": EXCHANGEINFO_SCHEMA,
            "futures": [_f(r) for r in (futures or []) if isinstance(r, dict)] if futures is not None else None,
            "spot": [_s(r) for r in (spot or []) if isinstance(r, dict)] if spot is not None else None}


def write_exchangeinfo_snapshot(paths: EnginePaths, day: str, doc: dict[str, Any]) -> Path:
    p = paths.exchangeinfo_dir / f"{day}.json.gz"
    paths.write_json_gz(p, {**doc, "day": day})
    return p


def latest_exchangeinfo(paths: EnginePaths) -> dict[str, Any] | None:
    """En son okunabilen `exchangeinfo/*.json.gz` belgesi (delist kararı için); yoksa None."""
    d = paths.exchangeinfo_dir
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.json.gz"), reverse=True):
        try:
            doc = read_json_gz(p)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return doc
    return None


def latest_onboard_dates(paths: EnginePaths) -> dict[str, int]:
    """En son `exchangeinfo/*.json.gz`'den vadeli sembol → onboardDate (ms). Yoksa boş."""
    d = paths.exchangeinfo_dir
    if not d.is_dir():
        return {}
    for p in sorted(d.glob("*.json.gz"), reverse=True):
        try:
            doc = read_json_gz(p)
        except (OSError, ValueError):
            continue
        out = {}
        for r in doc.get("futures") or []:
            try:
                if r.get("symbol") and r.get("onboardDate"):
                    out[str(r["symbol"])] = int(r["onboardDate"])
            except (TypeError, ValueError):
                continue
        if out:
            return out
    return {}


__all__ = ["BTC_ETH", "DATASETS", "EXCHANGEINFO_SCHEMA", "FUTURES_FLOOR", "FUT_TFS", "F_ENTRY_MISSING", "GOLD_1M_DAYS",
           "GOLD_FUTURES", "GOLD_FUT_TFS", "GOLD_SPOT", "GOLD_SPOT_TFS", "MAIN_SPOT_DAYS", "METRICS_FLOOR", "SPOT_CONTEXT",
           "SeriesSpec", "TRADED_DAYS", "UNIVERSE_SCHEMA", "build_universe", "compact_exchange_info", "day_ms",
           "entry_universe", "latest_exchangeinfo", "latest_onboard_dates", "plan_series", "snapshot_doc", "traded_symbols",
           "universe_json_snapshot", "write_exchangeinfo_snapshot", "write_universe_snapshot"]

"""SABIT GIRIS EVRENI — teslim raporu. Tek komutla olculebilir durum.

Rapor OLCER, iddia ETMEZ. Her bolum diskteki bir dosyadan ya da bir manifest'ten turetilir;
bir sey yoksa alan `null` kalir ve neden yok oldugu yazilir. "Kapsam tam" gibi bir cumle,
ancak sayilar oyle diyorsa uretilir.

Bolumler:

* ``contracts``  — on sembolun BORSADAN dogrulanmis sozlesme kimligi ve emir kurallari.
* ``history``    — zaman dilimi bazinda kapsama: ilk/son bar (UTC), satir, bosluk, checksum.
* ``decisions``  — son turun karar dagilimi ve acilmama gerekceleri.
* ``learning``   — kapanislarin kacinin ogrenmeye girdigi ve DISLANANLARIN sayisi.
* ``resources``  — tur suresi, istek/gun tahmini, disk, LLM tuketimi.
* ``provenance`` — karar cercevelerinin hangi piyasadan geldigi.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIMEFRAMES = ("1d", "4h", "1h", "15m", "1m")


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _iso(ms: Any) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _epoch(v: Any) -> str:
    """Unix saniyesi -> UTC ISO. Cozulemezse '-' (uydurma YOK)."""
    if not v:
        return "-"
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError, TypeError):
        return "-"


def _dir_bytes(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def contracts_section(filters_cache: dict | None, universe: list[str]) -> dict:
    """`symbol_filters.json`dan on sembolun DOGRULANMIS kurallari.

    Onbellekte olmayan sembol "varsayilan kural" ile DOLDURULMAZ: eksik olarak raporlanir,
    cunku dogrulanmamis bir adim/min-notional ile boyut hesaplamak sessizce yanlis emir uretir.
    """
    cache = (filters_cache or {}).get("futures") or {}
    rows, missing = [], []
    for sym in universe:
        f = cache.get(sym)
        # DOGRULANMIS sayilmak icin kaynak da PERPETUAL sozlesme tipi de gerekir: onbellekte
        # bir satirin BULUNMASI, o satirin borsadan gelmis olmasi anlamina gelmez.
        if (not isinstance(f, dict) or str(f.get("source")) != "binance_api"
                or str(f.get("contract_type")) != "PERPETUAL"):
            missing.append(sym)
            continue
        rows.append({"symbol": sym, "contract_type": f.get("contract_type"),
                     "market_type": f.get("market_type"),
                     "tick_size": f.get("price_tick"), "step_size": f.get("qty_step"),
                     "min_qty": f.get("min_qty"), "min_notional": f.get("min_notional"),
                     "max_leverage": f.get("max_leverage"), "verified_at": f.get("verified_at")})
    return {"verified": len(rows), "expected": len(universe), "missing": missing,
            "verified_at": (filters_cache or {}).get("verified_at"), "rows": rows}


def history_section(store: Any, universe: list[str], *, market: str = "futures",
                    timeframes: tuple[str, ...] = TIMEFRAMES) -> dict:
    """Zaman dilimi bazinda kapsama tablosu. Seri yoksa satir `present=False` olarak gecer."""
    rows, absent = [], []
    for sym in universe:
        for tf in timeframes:
            m = store.manifest(market, sym, tf)
            if not m.row_count:
                absent.append(f"{sym} {tf}")
                rows.append({"symbol": sym, "timeframe": tf, "present": False})
                continue
            rows.append({"symbol": sym, "timeframe": tf, "present": True,
                         "rows": m.row_count, "gaps": m.gap_count,
                         "duplicates_removed": m.duplicate_count,
                         "first_utc": _iso(m.first_ts_ms), "last_utc": _iso(m.last_ts_ms),
                         "quality": m.quality_score, "bad_chunks": len(m.bad_chunks),
                         "downloaded_at": m.downloaded_at, "checksum": (m.checksum or "")[:12]})
    present = [r for r in rows if r.get("present")]
    return {"market": market, "series_expected": len(universe) * len(timeframes),
            "series_present": len(present), "series_absent": absent,
            "total_rows": sum(int(r.get("rows") or 0) for r in present),
            "total_gaps": sum(int(r.get("gaps") or 0) for r in present),
            "source": "binance public archive (data.binance.vision) + fapi REST",
            "rows": rows}


def decisions_section(risk: dict | None, heads: dict | None, universe: list[str]) -> dict:
    """Son turun karar dagilimi + acilmama gerekceleri (aday basina SON kayit)."""
    last = (risk or {}).get("last_decisions") or []
    by_sym: dict[str, dict] = {}
    for e in last:
        if isinstance(e, dict) and e.get("symbol"):
            by_sym[str(e["symbol"])] = e
    head_rows = (heads or {}).get("heads") or []
    verdicts: dict[str, int] = {}
    for h in head_rows:
        v = str(h.get("verdict") or "UNKNOWN")
        verdicts[v] = verdicts.get(v, 0) + 1
    reasons: dict[str, int] = {}
    for sym in universe:
        e = by_sym.get(sym)
        h = next((x for x in head_rows if str(x.get("symbol")) == sym), None)
        code = str((e or {}).get("block_code") or (h or {}).get("no_trade_reason") or "")
        if code:
            reasons[code] = reasons.get(code, 0) + 1
    opened = [s for s, e in by_sym.items() if e.get("risk_allowed") is True]
    return {"generated_at": (heads or {}).get("generated_at"),
            "universe_with_decision": sum(1 for s in universe
                                          if any(str(h.get("symbol")) == s for h in head_rows)),
            "universe_total": len(universe), "verdicts": verdicts,
            "ranked": len(by_sym), "opened": opened, "block_reasons": reasons}


def learning_section(chain: dict | None, ledger: dict | None) -> dict:
    """Kapanis → ogrenme zinciri. DISLANAN kayit sayisi gizlenmez."""
    led = ledger or {}
    closed = len(led.get("history") or [])
    ch = chain or {}
    return {"closed_trades": closed, "open_positions": len(led.get("positions") or {}),
            "chain": {k: ch.get(k) for k in ("complete", "total", "missing", "excluded",
                                             "skipped_funding_incomplete")
                      if k in ch} or None,
            "note": "eksik funding taşıyan kapanış öğrenmeye GİRMEZ ve burada sayılır; "
                    "hesap raporundan gizlenmez"}


def resources_section(state_dir: Path, data_dir: Path, *, health: dict | None,
                      llm: dict | None, universe_size: int) -> dict:
    """Tur suresi, disk ve istek tahmini.

    Istek tahmini OLCUM DEGIL HESAPTIR ve oyle etiketlenir: sembol basina uc mum cercevesi
    (1d/4h/1h) + bir canli anlik goruntu, arti tur basina iki venue istegi.
    """
    per_tour = universe_size * 4 + 2
    return {"last_tour_seconds": (health or {}).get("seconds"),
            "last_tour_symbols": (health or {}).get("symbols"),
            "state_bytes": _dir_bytes(state_dir), "history_bytes": _dir_bytes(data_dir),
            "requests_per_tour_estimate": per_tour,
            "requests_per_day_estimate_15m_cadence": per_tour * 96,
            "estimate_basis": "sembol başına 3 mum çerçevesi + 1 canlı anlık görüntü, "
                              "tur başına 2 venue isteği; ÖLÇÜM DEĞİL hesaptır",
            "llm": {"spent_usd": (llm or {}).get("spent_usd"),
                    "spent_tokens": (llm or {}).get("spent_tokens"),
                    "calls": (llm or {}).get("calls"), "day": (llm or {}).get("day")}}


#: Bar boyutu (sikistirilmis, olculdu: 137 MB / 2.942.914 satir ~= 46 bayt/satir).
_BYTES_PER_ROW = 46
_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}


def storage_section(state_dir: Path, cache_dir: Path, *, history_root: str = "history",
                    universe: list[str] | None = None,
                    timeframes: tuple[str, ...] = ("1d", "4h", "1h", "15m")) -> dict:
    """Disk kullanimi ve BUYUME HIZI; ham arsiv ile yeniden uretilebilir onbellek AYRI.

    Ayrim onemlidir cunku kurtarma yollari farklidir:

    * ``raw_archive`` — `data/<history_root>`. Binance arsivinden indirilmis ham mumlar,
      manifest'te kaynak/tarih/checksum ile. Silinirse yeniden indirmek saatler surer;
      yedege girmelidir.
    * ``regenerable_cache`` — `data/` altindaki diger dosyalar (TradingView/ccxt mum
      onbellegi, sembol filtreleri). Kaybi veri kaybi DEGILDIR; bir sonraki turda yeniden
      uretilir ve guvenle silinebilir.
    * ``state`` — `state/`. Defter, ogrenme, karar kayitlari. YETKILI veri; asla
      "onbellek" muamelesi gormez.

    Benzerlik indeksi diske YAZILMAZ (yalniz bellekte), bu yuzden yer tutmaz; ham arsivden
    yeniden kurulur. Onbellegi silmek veri kaybettirmez.
    """
    raw = cache_dir / history_root
    raw_b = _dir_bytes(raw)
    all_cache_b = _dir_bytes(cache_dir)
    rows_per_day = sum(_BARS_PER_DAY.get(tf, 0) for tf in timeframes) * max(1, len(universe or []))
    return {
        "raw_archive": {"path": str(raw), "bytes": raw_b,
                        "note": "ham mumlar + manifest (kaynak/tarih/checksum); yeniden indirmek saatler surer"},
        "regenerable_cache": {"path": str(cache_dir), "bytes": max(0, all_cache_b - raw_b),
                              "note": "TradingView/ccxt mum onbellegi ve sembol filtreleri; kaybi veri kaybi DEGILDIR"},
        "state": {"path": str(state_dir), "bytes": _dir_bytes(state_dir),
                  "note": "defter/ogrenme/karar kayitlari - YETKILI veri, onbellek degil"},
        "growth": {"rows_per_day": rows_per_day,
                   "bytes_per_day_estimate": rows_per_day * _BYTES_PER_ROW,
                   "bytes_per_year_estimate": rows_per_day * _BYTES_PER_ROW * 365,
                   "basis": f"{len(universe or [])} sembol x {list(timeframes)}; "
                            f"{_BYTES_PER_ROW} bayt/satir olculen sikistirilmis ortalama - OLCUM DEGIL hesaptir"},
        "index_on_disk": False,
        "git": {"tracked": False,
                "note": "data/ .gitignore'da; kod, config ve manifestler ayri yonetilir"},
    }


def _news_sources() -> dict:
    """ETKIN kaynaklar ve UYGULANMAYANLAR.

    "Haberler takip ediliyor" cumlesi ancak `active` listesi icin kurulabilir; `not_implemented`
    gizlenmez ve neye bagli oldugu yazilir.
    """
    out: dict = {"active": ["venue_contract_and_funding (binance exchangeInfo + fundingInfo)"],
                 "not_implemented": []}
    try:
        from .market.project_news import MACRO_CALENDAR_STATUS, PROJECT_REPOS
        out["active"].append(f"project_releases (github, {len(PROJECT_REPOS)} depo, anahtarsiz)")
        if not MACRO_CALENDAR_STATUS.get("implemented"):
            out["not_implemented"].append(f"macro_calendar (bagli: {MACRO_CALENDAR_STATUS.get('blocked_on')})")
        out["not_implemented"] += ["borsa listeleme duyurulari", "yonetisim oylamalari",
                                   "genel basin akisi (RSS/JSON)"]
    except Exception:  # noqa: BLE001
        pass
    return out


def build(state_dir: Path | str, data_dir: Path | str, *, universe: list[str],
          history_root: str = "history", market: str = "futures",
          timeframes: tuple[str, ...] = TIMEFRAMES) -> dict:
    """Butun bolumleri topla. Eksik dosya raporu DUSURMEZ; alan `null` kalir."""
    st, dd = Path(state_dir), Path(data_dir)
    prov = _read(st / "frame_provenance.json", {}) or {}
    try:
        from .history import HistoryStore
        hist = history_section(HistoryStore(dd / history_root), universe,
                               market=market, timeframes=timeframes)
    except Exception as exc:  # noqa: BLE001 — veri golu yoksa rapor yine uretilir
        hist = {"error": f"{type(exc).__name__}: {exc}", "series_present": 0}
    news_cov = None
    try:
        from .market.news import NewsStore
        news_cov = NewsStore(st / "news.jsonl").coverage()
    except Exception:  # noqa: BLE001
        news_cov = None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": list(universe), "universe_size": len(universe),
        "universe_enabled": bool(prov.get("universe_enabled")),
        "contracts": contracts_section(_read(dd / "symbol_filters.json", {}), universe),
        "history": hist,
        "decisions": decisions_section(_read(st / "risk.json", {}), _read(st / "coin_heads.json", {}), universe),
        "learning": learning_section(_read(st / "learning_chain.json", {}), _read(st / "futures_ledger.json", {})),
        "resources": resources_section(st, dd / history_root, health=_read(st / "health.json", {}),
                                       llm=_read(st / "llm_budget.json", {}), universe_size=len(universe)),
        "storage": storage_section(st, dd, history_root=history_root, universe=universe,
                                   timeframes=tuple(t for t in timeframes if t != "1m")),
        "refresh": _read(st / "history_refresh.json", None),
        "provenance": {"entry_universe": prov.get("entry_universe"),
                       "entry_blocked_on_data": prov.get("entry_blocked_on_data"),
                       "by_symbol": prov.get("by_symbol")},
        "news": news_cov,
        "news_sources": _news_sources(),
    }


def render_text(rep: dict) -> str:
    """Insan okunur ozet. Sayilar `rep` icindekilerle AYNI — ikinci bir hesap YOK."""
    out: list[str] = []
    add = out.append
    add(f"SABIT GIRIS EVRENI RAPORU — {rep['generated_at']}")
    add(f"Evren: {rep['universe_size']} sembol · kapı {'AÇIK' if rep['universe_enabled'] else 'KAPALI'}")
    c = rep["contracts"]
    add(f"\nSÖZLEŞMELER: {c['verified']}/{c['expected']} doğrulandı"
        + (f" · EKSİK: {', '.join(c['missing'])}" if c["missing"] else "")
        + (f" · doğrulama: {c['verified_at']}" if c.get("verified_at") else ""))
    for r in c["rows"]:
        add(f"  {r['symbol']:10} tick={r['tick_size']:<10} step={r['step_size']:<8} "
            f"minQty={r['min_qty']:<8} minNotional={r['min_notional']}")
    h = rep["history"]
    if h.get("error"):
        add(f"\nTARİHSEL VERİ: okunamadı — {h['error']}")
    else:
        add(f"\nTARİHSEL VERİ ({h['market']}): {h['series_present']}/{h['series_expected']} seri · "
            f"{h['total_rows']:,} satır · {h['total_gaps']} boşluk")
        add(f"  kaynak: {h['source']}")
        for r in h["rows"]:
            if not r.get("present"):
                continue
            add(f"  {r['symbol']:10} {r['timeframe']:4} {r['rows']:>8,} satır  "
                f"{r['first_utc']} → {r['last_utc']}  boşluk={r['gaps']} kalite={r['quality']}")
        if h["series_absent"]:
            add(f"  YOK: {', '.join(h['series_absent'])}")
    d = rep["decisions"]
    add(f"\nSON TUR: {d['universe_with_decision']}/{d['universe_total']} sembolde karar · "
        f"sıralamaya giren {d['ranked']} · açılan {len(d['opened'])}")
    if d["verdicts"]:
        add("  kararlar: " + ", ".join(f"{k}={v}" for k, v in sorted(d["verdicts"].items())))
    if d["block_reasons"]:
        add("  açılmama nedenleri: " + ", ".join(f"{k}={v}" for k, v in sorted(d["block_reasons"].items())))
    lr = rep["learning"]
    add(f"\nÖĞRENME: {lr['closed_trades']} kapanmış işlem · {lr['open_positions']} açık pozisyon")
    if lr.get("chain"):
        add("  zincir: " + ", ".join(f"{k}={v}" for k, v in lr["chain"].items()))
    p = rep["provenance"]
    by = p.get("by_symbol") or {}
    perp = sum(1 for v in by.values() if v.get("market") == "USDM_PERP")
    add(f"\nVERİ KİMLİĞİ: {perp}/{len(by)} sembol PERPETUAL çerçeveyle analiz edildi")
    if p.get("entry_blocked_on_data"):
        add(f"  veri nedeniyle giriş kapalı: {', '.join(p['entry_blocked_on_data'])}")
    n = rep.get("news")
    if n:
        add(f"\nOLAY KAYDI: {n['total']} kayıt · doğrulanmış={n['by_status'].get('CONFIRMED', 0)} "
            f"söylenti={n['by_status'].get('RUMOR', 0)} · yayın zamanı bilinmeyen={n['unknown_publish_time']}")
        bc = n.get("by_category") or {}
        add(f"  sinif: venue={bc.get('venue', 0)} proje={bc.get('project', 0)} makro={bc.get('macro', 0)}")
        src = rep.get("news_sources") or {}
        if src:
            add(f"  ETKIN kaynaklar: {', '.join(src.get('active') or []) or 'yok'}")
            add(f"  UYGULANMADI: {', '.join(src.get('not_implemented') or []) or 'yok'}")
    rf = rep.get("refresh") or {}
    if rf.get("enabled"):
        ix = rf.get("index") or {}
        add("")
        add(f"OTOMATIK YENILEME: her {rf.get('interval_s', 0) / 60:.0f} dk - "
            f"{rf.get('refreshes', 0)} yenileme - en fazla {rf.get('max_symbols')} sembol")
        _age = rf.get("seconds_since_success")
        add("  son basarili yenileme: " + _epoch(rf.get("last_success_at"))
            + (f" ({_age:.0f} sn once)" if _age is not None else " (HIC)"))
        add(f"  hata: {rf.get('last_error') or 'yok'} - indeks serileri: {rf.get('index_timeframes')}")
        if ix:
            add(f"  indeks surumu {ix.get('version')} - {ix.get('events')} olay - "
                f"{ix.get('series')} seri - en yeni bar {_iso(ix.get('newest_bar_ms'))}")
    elif rf is not None and rf != {}:
        add("")
        add(f"OTOMATIK YENILEME: KAPALI ({rf.get('reason')})")
    sg = rep.get("storage") or {}
    if sg:
        add("")
        add(f"DEPOLAMA: ham arsiv {sg['raw_archive']['bytes'] / 1e6:.1f} MB - "
            f"yeniden uretilebilir onbellek {sg['regenerable_cache']['bytes'] / 1e6:.1f} MB - "
            f"state {sg['state']['bytes'] / 1e6:.1f} MB")
        g = sg["growth"]
        add(f"  buyume: {g['rows_per_day']:,} satir/gun ~ {g['bytes_per_day_estimate'] / 1e6:.1f} MB/gun "
            f"~ {g['bytes_per_year_estimate'] / 1e9:.2f} GB/yil - {g['basis']}")
    r = rep["resources"]
    add(f"\nKAYNAK: son tur {r['last_tour_seconds']}s / {r['last_tour_symbols']} sembol · "
        f"state {r['state_bytes'] / 1e6:.1f} MB · geçmiş {r['history_bytes'] / 1e6:.1f} MB")
    add(f"  istek tahmini: {r['requests_per_tour_estimate']}/tur, "
        f"{r['requests_per_day_estimate_15m_cadence']}/gün (15 dk kadans) — {r['estimate_basis']}")
    add(f"  LLM: {r['llm']['calls']} çağrı · {r['llm']['spent_usd']} USD · {r['llm']['spent_tokens']} token")
    return "\n".join(out)


__all__ = ["TIMEFRAMES", "build", "render_text", "contracts_section", "history_section",
           "decisions_section", "learning_section", "resources_section", "storage_section"]

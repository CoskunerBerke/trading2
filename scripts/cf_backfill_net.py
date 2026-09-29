# -*- coding: utf-8 -*-
"""KARŞI-OLGUSAL NET DOLGU (2026-09-29, maliyet sapması) — ESKİ (v1, yalnız brüt) etiketli "olsaydı" kayıtlarına NET R.

Neden: `cf_label_v2` öncesi etiketler yalnız BRÜT R taşır (referans fiyat, seviyeden dolum, ücret/kayma/funding yok); gerçek
işlemlerin R'si NET. Çalışan kod v1 kayıtları TEMBEL doldurur (`learning_cf.relabel_net`), ama yalnız pencere defterin
eldeki kapanmış barlarındaysa (Box 5m ≈ 25 saat). Daha eski kayıtlar için bu betik:

1. Kapanmış mumları borsadan (resmi USDⓈ-M, salt okunur) çeker ve AYNI `relabel_net` / `net_outcome` yolunu koşar →
   `label_version = cf_label_v2`, `net_backfilled = {at, source: "offline_klines"}` (brüt yeniden üretilemezse yazılmaz).
2. Mum alınamazsa (ağ yok / `--no-fetch` / pencere tutmuyor) KAPALI-BİÇİM TAHMİN yazar: `label_version = cf_label_v1c`,
   `r_net_approx` (ücret + kayma + giriş dolumu; çıkış dolum modeli ve funding YOK → ÜST SINIR). Tahmin v2 net
   ortalamasına ASLA karışmaz (karne `n_approx_v1c` ayrı sayar).

Brüt alanlar (`r_multiple`, `won`, `veto_was_right`, `exit_reason`, `exit_price`) DEĞİŞMEZ. Yürütme modeli her defterin
kurucusuyla AYNI parametrelerle kurulan defterden `ExecModel.of_ledger` ile alınır (test: gerçek defterlerle eşit).

Ne zaman: `--apply` yalnız worker DURDURULMUŞKEN (dağıtım betiğinde kod güncellendikten sonra, başlatmadan önce); çalışan
worker dosyayı kendi bellek kopyasıyla yeniden yazar. Hizmet kullanıcısıyla koşulması önerilir; root ile koşulursa özgün
sahiplik korunur. `--apply` olmadan hiçbir dosyaya YAZILMAZ (kuru çalışma). Özgün dosya yanına yedeklenir
(`<ad>.pre-cf-net-<UTC>`). Dosya okuma ile yazma arasında değiştiyse o dosya ATLANIR (yarış yok).

Kullanım:
    python scripts/cf_backfill_net.py --state /opt/tradingbot/data/state --config /opt/tradingbot/app/config.yaml [--apply]
                                      [--no-fetch] [--json rapor.json]
Çıkış kodu: 0 = tamam (değişiklik olsun olmasın); 2 = state/config okunamadı.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from tradingbot.accounting import (FeeSchedule, FuturesLedgerV2, LiquidationParams, MarketType, SlippageModel,  # noqa: E402
                                   TaxPolicy, default_brackets)
from tradingbot.accounting.funding import FundingSchedule  # noqa: E402
from tradingbot.core import atomic_write_json, from_iso, iso  # noqa: E402
from tradingbot.learn.shadow import ShadowTrade  # noqa: E402
from tradingbot.learning_cf import (LABEL_VERSION_V1, LABEL_VERSION_V1C, ExecModel, _replay_filters,  # noqa: E402
                                    approx_net_r, relabel_net)
from tradingbot.timeframes import TF_MS  # noqa: E402

CF_FILE = "counterfactual_trades.json"
MAIN_SHADOW_FILE = "shadow_book.json"
PATTERN_DIR = "pattern_trader"
KLINE_PAGE = 1500
_TF_BY_MIN = {v // 60_000: k for k, v in TF_MS.items()}


def _configure_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# ----------------------------------------------------------------------------- yürütme modelleri (kurucularla AYNI)
def _ledger_kwargs(v3: Any, *, tp1_fraction, breakeven_at_mfe_r, funding: FundingSchedule | None = None) -> dict[str, Any]:
    fees = FeeSchedule(maker_pct=Decimal(str(v3.fees.futures_maker_pct)), taker_pct=Decimal(str(v3.fees.futures_taker_pct)),
                       source=v3.fees.source)
    kw = dict(fees=fees, slippage=SlippageModel(fixed_bps=Decimal(str(v3.fees.slippage_bps))), brackets=default_brackets(),
              liq_params=LiquidationParams(liq_fee_pct=Decimal(str(v3.futures_v3.liq_fee_pct))),
              tp1_fraction=Decimal(str(tp1_fraction)), breakeven_at_mfe_r=Decimal(str(breakeven_at_mfe_r)),
              tax_policy=TaxPolicy.disabled())
    if funding is not None:
        kw["funding"] = funding
    return kw


def book_exec_model(v3: Any, *, kind: str, spec: Any = None, rates: Any = None) -> ExecModel:
    """Tek defterin yürütme modeli. `kind`: "main" (`TradingEngineV3.__init__` → `ledger2`), "book" (`StrategyBook.__init__`,
    `spec` = BookSpec) ya da "pattern" (`PatternBook.__init__`). Parametreler kurucularla AYNI; funding motorun bağladığı
    gibi (`bind_source(rates)`, kaynak yoksa tahminsiz) — model `ExecModel.of_ledger` ile defter nesnesinden alınır."""
    if kind == "main":
        led = FuturesLedgerV2(1, **_ledger_kwargs(v3, tp1_fraction=v3.futures_v3.tp1_fraction,
                                                  breakeven_at_mfe_r=getattr(v3.futures_v3, "breakeven_at_mfe_r", 0.0)))
    elif kind == "book":
        led = FuturesLedgerV2(1, **_ledger_kwargs(v3, tp1_fraction=v3.futures_v3.tp1_fraction,
                                                  breakeven_at_mfe_r=spec.breakeven_at_mfe_r))
    elif kind == "pattern":
        led = FuturesLedgerV2(1, **_ledger_kwargs(v3, tp1_fraction="1", breakeven_at_mfe_r="0",
                                                  funding=FundingSchedule(fallback_to_last_known=False)))
    else:
        raise ValueError(f"bilinmeyen defter türü: {kind}")
    if rates is not None:
        led.funding.bind_source(rates)
    else:
        led.funding.fallback_to_last_known = False
    return ExecModel.of_ledger(led)


def exec_models(cfg: Any, rates: Any = None) -> dict[str, ExecModel]:
    """Defter anahtarı → yürütme modeli (`book_exec_model`)."""
    from tradingbot.strategy_paper import book_specs
    v3 = cfg.v3
    out: dict[str, ExecModel] = {"main": book_exec_model(v3, kind="main", rates=rates)}
    for sp in book_specs(v3):
        out[str(sp.state_dir)] = book_exec_model(v3, kind="book", spec=sp, rates=rates)
    out[PATTERN_DIR] = book_exec_model(v3, kind="pattern", rates=rates)
    return out


def cf_files(state: Path, cfg: Any) -> list[tuple[str, Path, str | None]]:
    """(defter anahtarı, dosya, yalnız bu `book` satırları). Ana bot: `shadow_book.json` içindeki `book == "main"`."""
    from tradingbot.strategy_paper import book_specs
    out = [("main", state / MAIN_SHADOW_FILE, "main")]
    out += [(str(sp.state_dir), state / str(sp.state_dir) / CF_FILE, None) for sp in book_specs(cfg.v3)]
    out.append((PATTERN_DIR, state / PATTERN_DIR / CF_FILE, None))
    return [x for x in out if x[1].exists()]


def _candidate(row: dict[str, Any]) -> bool:
    o = row.get("outcome")
    return (isinstance(o, dict) and o.get("r_net") is None and o.get("label_version") in (None, LABEL_VERSION_V1))


def _shadow(row: dict[str, Any]) -> ShadowTrade:
    st = ShadowTrade(**{k: v for k, v in row.items() if k in ShadowTrade.__dataclass_fields__})
    o = dict(st.outcome or {})
    o.pop("label_version", None)                     # tembel yolun "bu yoldan yok" işareti: burada mumlarla yeniden denenir
    o.pop("net_status", None)
    st.outcome = o
    return st


# ----------------------------------------------------------------------------- mumlar
def provider_frames(provider: Any) -> Callable[[str, int, int, int], pd.DataFrame | None]:
    """(sembol, dilim dk, başlangıç ms, bitiş ms) → kapanmış mumlar (sayfalı; `KLINE_PAGE`). Arıza → None."""
    def _get(symbol: str, tf_minutes: int, start_ms: int, end_ms: int) -> pd.DataFrame | None:
        tf = _TF_BY_MIN.get(int(tf_minutes))
        if tf is None:
            return None
        step = int(tf_minutes) * 60_000
        parts, cur = [], int(start_ms)
        try:
            while cur <= end_ms:
                df = provider.klines(symbol, tf, limit=KLINE_PAGE, start_ms=cur, end_ms=int(end_ms))
                if df is None or df.empty:
                    break
                if "is_closed" in df.columns:
                    df = df[df["is_closed"].astype(bool)]
                if df.empty:
                    break
                parts.append(df[["timestamp", "open", "high", "low", "close"]])
                nxt = int(df["timestamp"].iloc[-1]) + step
                if nxt <= cur:
                    break
                cur = nxt
        except Exception as exc:  # noqa: BLE001 — mum alınamazsa kapalı-biçim tahmine düşülür
            print(f"   {symbol} {tf} mumları alınamadı: {type(exc).__name__}: {exc}"[:200])
            return None
        if not parts:
            return None
        return pd.concat(parts).drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    return _get


# ----------------------------------------------------------------------------- dosya başına dolgu
def backfill_rows(rows: list[dict[str, Any]], *, model: ExecModel, now: datetime,
                  frames_fn: Callable[[str, int, int, int], pd.DataFrame | None] | None,
                  filters_for: Callable[[str], Any] | None = None, funding_lookup: Any = None) -> dict[str, Any]:
    """Aday satırların `outcome`ını YERİNDE günceller. Döner: özet (sayılar, brüt/net ortalamaları)."""
    cands = [r for r in rows if _candidate(r)]
    summ: dict[str, Any] = {"candidates": len(cands), "v2": 0, "v1c": 0, "none": 0}
    if not cands:
        return summ
    shadows = {id(r): _shadow(r) for r in cands}
    frames: dict[str, dict[str, Any]] = {}
    if frames_fn is not None:
        want: dict[tuple[str, int], list[int]] = {}
        for st in shadows.values():
            tf_ms = int(st.tf_minutes) * 60_000
            c_ms = int(from_iso(st.created_at).timestamp() * 1000)
            l_ms = int(from_iso(st.label_ts).timestamp() * 1000)
            w = want.setdefault((st.symbol, int(st.tf_minutes)), [c_ms - tf_ms, l_ms])
            w[0], w[1] = min(w[0], c_ms - tf_ms), max(w[1], l_ms)
        now_ms = int(now.timestamp() * 1000)
        for (sym, tfm), (a, b) in want.items():
            df = frames_fn(sym, tfm, a - a % (tfm * 60_000), min(b, now_ms))
            if df is not None and not df.empty:
                key = _TF_BY_MIN.get(tfm, str(tfm))
                frames.setdefault(sym, {})[key] = df
        rc = relabel_net(list(shadows.values()), frames, now, exec_model=model, filters_for=filters_for,
                         funding_lookup=funding_lookup, limit=10 ** 9, source="offline_klines")
        summ["relabel"] = rc
    gross, net, approx = [], [], []
    for r in cands:
        st = shadows[id(r)]
        o = dict(st.outcome or {})
        if o.get("r_net") is not None:
            r["outcome"] = o
            summ["v2"] += 1
            gross.append(float(o["r_multiple"]))
            net.append(float(o["r_net"]))
            continue
        why = str(o.get("net_status") or ("NO_KLINES" if frames_fn is not None else "NO_FETCH"))
        filt = filters_for(st.symbol) if filters_for is not None else None
        x = approx_net_r(st, o, model=model, filters=filt)
        if x is None:
            summ["none"] += 1
            continue
        o.update({"label_version": LABEL_VERSION_V1C, "r_net_approx": x, "net_status": "APPROX_CLOSED_FORM:" + why,
                  "exec_model": model.to_dict(_replay_filters(st.symbol, filt)),
                  "net_backfilled": {"at": iso(now), "source": "offline_closed_form"}})
        r["outcome"] = o
        summ["v1c"] += 1
        approx.append((float(o["r_multiple"]), x))

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None
    summ.update({"v2_mean_r_gross": mean(gross), "v2_mean_r_net": mean(net),
                 "v1c_mean_r_gross": mean([g for g, _ in approx]), "v1c_mean_r_net_approx": mean([x for _, x in approx])})
    return summ


def _write(path: Path, doc: Any, stat0: os.stat_result, stamp: str) -> str:
    st = path.stat()
    if (st.st_mtime_ns, st.st_size) != (stat0.st_mtime_ns, stat0.st_size):
        return "SKIPPED_CHANGED_ON_DISK (worker çalışıyor olabilir — durdurup yeniden koşun)"
    shutil.copy2(path, path.with_name(f"{path.name}.pre-cf-net-{stamp}"))
    atomic_write_json(path, doc)
    try:
        if os.geteuid() == 0:
            os.chown(path, stat0.st_uid, stat0.st_gid)
    except (AttributeError, OSError):
        pass
    return "WRITTEN"


def run(state: Path, cfg: Any, *, apply: bool, fetch: bool, now: datetime | None = None, provider: Any = None,
        frames_fn: Callable | None = None, rates: Any = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    from tradingbot.accounting.filters import FiltersCache
    fpath = Path(cfg.cache_path) / "symbol_filters.json"
    fc = FiltersCache(fpath) if fpath.exists() else None
    filters_for = (lambda s: fc.get(s, MarketType.USDM_PERP)) if fc is not None else None
    files = cf_files(state, cfg)
    if fetch and frames_fn is None:
        if provider is None:
            from tradingbot.market.http import HttpClient
            from tradingbot.market.providers import BinanceFuturesProvider
            from tradingbot.market.ratelimit import BudgetPool
            pool = BudgetPool(safety=cfg.v3.data.rate_budget_safety)
            provider = BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, pool.get("fapi.binance.com")))
        frames_fn = provider_frames(provider)
        if rates is None and bool(getattr(cfg.v3.futures_v3, "realized_funding_source", False)):
            rates = _funding_rates(provider, files, now)
    models = exec_models(cfg, rates)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {"now": iso(now), "apply": bool(apply), "fetch": bool(fetch), "books": {}}
    for key, path, only in files:
        stat0 = path.stat()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report["books"][key] = {"file": str(path), "error": f"{type(exc).__name__}: {exc}"}
            continue
        rows = [t for t in (doc.get("trades") or []) if isinstance(t, dict) and (only is None or t.get("book") == only)]
        summ = backfill_rows(rows, model=models.get(key) or models["main"], now=now, frames_fn=frames_fn if fetch else None,
                             filters_for=filters_for, funding_lookup=rates)
        summ["file"] = str(path)
        if apply and (summ["v2"] or summ["v1c"]):
            summ["write"] = _write(path, doc, stat0, stamp)
        else:
            summ["write"] = "DRY_RUN" if not apply else "NO_CHANGE"
        report["books"][key] = summ
    return report


def _funding_rates(provider: Any, files: list, now: datetime) -> Any:
    """Gerçekleşmiş funding satırları (settlement oranı + kendi mark'ı): adayların en erken anından bugüne. Arıza → None."""
    try:
        from tradingbot.pattern_trader.funding import FundingRates
        earliest, syms = None, set()
        for _k, path, only in files:
            doc = json.loads(path.read_text(encoding="utf-8"))
            for r in doc.get("trades") or []:
                if isinstance(r, dict) and (only is None or r.get("book") == only) and _candidate(r):
                    syms.add(str(r.get("symbol")))
                    c = from_iso(str(r.get("created_at")))
                    earliest = c if earliest is None or c < earliest else earliest
        if not syms or earliest is None:
            return None
        now_ms = int(now.timestamp() * 1000)
        fr = FundingRates(provider, lookback_ms=now_ms - int(earliest.timestamp() * 1000) + 2 * 86_400_000)
        fr.refresh(sorted(syms), now_ms=now_ms)
        return fr
    except Exception as exc:  # noqa: BLE001 — funding alınamazsa net funding'siz (`funding_complete` False)
        print(f"funding verisi alınamadı: {type(exc).__name__}: {exc}"[:200])
        return None


def render(rep: dict[str, Any]) -> str:
    lines = [f"KARŞI-OLGUSAL NET DOLGU · {rep['now']} · {'UYGULA' if rep['apply'] else 'KURU ÇALIŞMA (yazılmaz)'}"
             f" · mumlar {'borsadan' if rep['fetch'] else 'YOK (--no-fetch)'}"]
    for key, b in rep["books"].items():
        if "error" in b:
            lines.append(f"   {key:<32} OKUNAMADI: {b['error']}")
            continue
        lines.append(f"   {key:<32} aday {b['candidates']:>4} · v2 net {b['v2']:>4} (brüt {b.get('v2_mean_r_gross')} → net "
                     f"{b.get('v2_mean_r_net')}) · v1c tahmin {b['v1c']:>4} (brüt {b.get('v1c_mean_r_gross')} → üst sınır "
                     f"{b.get('v1c_mean_r_net_approx')}) · yok {b['none']} · {b['write']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    ap = argparse.ArgumentParser(description="Eski (brüt) karşı-olgusal etiketlerine net R (salt kuru çalışma, --apply ile yazar).")
    ap.add_argument("--state", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true", help="dosyalara yaz (yalnız worker durdurulmuşken)")
    ap.add_argument("--no-fetch", action="store_true", help="borsadan mum çekme; yalnız kapalı-biçim tahmin (v1c)")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    state = Path(a.state)
    if not state.is_dir():
        print(f"state klasörü yok: {state}", file=sys.stderr)
        return 2
    try:
        from tradingbot.config import load_config
        cfg = load_config(a.config)
    except Exception as exc:  # noqa: BLE001
        print(f"config okunamadı: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    rep = run(state, cfg, apply=bool(a.apply), fetch=not a.no_fetch)
    print(render(rep))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

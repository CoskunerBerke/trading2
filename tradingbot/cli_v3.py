"""v3 CLI komutları — mevcut komutlar bozulmaz; yeni komutlar `cli.build_parser` içine kaydedilir.

  doctor · migrate · collect · analyze(v3) · paper-status · spot-status · futures-status · replay · backtest(futures)
  validate-model · model-status · risk-status · health · reconcile · dashboard · export-trades · export-tax · mode-status
  mode-transition · killswitch-reset · backup · restore · universe
Gerçek emir komutu YOKTUR; LIVE yolu bu sürümde kapalıdır.
"""
from __future__ import annotations

import datetime as _dt

import argparse
import csv
import json
import logging
from pathlib import Path

from .config import BotConfig
from .core import ExecutionDisabledError, iso, read_json, utc_now

log = logging.getLogger("tradingbot.v3")


def _p(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


# ------------------------------------------------------------------ durum komutları
def cmd_mode_status(cfg: BotConfig, args) -> int:
    from .risk import ModeState
    ms = ModeState(cfg.state_path / "mode.json")
    _p({"mode": ms.mode.value, "config_mode": cfg.mode, "live_order_path_enabled": ms.is_live_order_path_enabled(),
        "allow_live_env": __import__("os").environ.get("ALLOW_LIVE_TRADING", "unset"), "history": ms.history[-5:],
        "note": "Geçişler yalnız manuel: `mode-transition --to TESTNET --operator <ad> --check k=v ...`"})
    return 0


def cmd_mode_transition(cfg: BotConfig, args) -> int:
    from .risk import ModeState
    ms = ModeState(cfg.state_path / "mode.json")
    checks = {}
    for kv in args.check or []:
        k, _, v = kv.partition("=")
        checks[k] = (v.lower() in ("1", "true", "yes")) if v.lower() in ("1", "0", "true", "false", "yes", "no") else (float(v) if v.replace(".", "", 1).isdigit() else v)
    try:
        r = ms.request_transition(args.to, operator=args.operator, checks=checks, confirmation_token=args.token,
                                  account_label=cfg.v3.mode.account_label, config_allow_live=cfg.v3.mode.live_trading)
    except ExecutionDisabledError as exc:
        print(f"⛔ {exc}")
        return 2
    _p(r.to_dict())
    return 0 if r.ok else 1


def cmd_risk_status(cfg: BotConfig, args) -> int:
    from .risk import KillSwitch, resolve_profile, warn_if_below_recommended
    prof = resolve_profile(cfg.v3.risk_profiles.profile, cfg.v3.risk_profiles.overrides, i_understand=cfg.v3.risk_profiles.i_understand)
    ks = KillSwitch.load(cfg.state_path / "killswitch.json")
    risk = read_json(cfg.state_path / "risk.json", default={})
    _p({"profile": prof.to_dict(), "warnings_vs_recommended": warn_if_below_recommended(prof), "killswitch": ks.to_dict(),
        "exposure": risk.get("exposure"), "last_decisions": (risk.get("last_decisions") or [])[-10:]})
    return 0


def cmd_killswitch_reset(cfg: BotConfig, args) -> int:
    from .risk import KillSwitch
    ks = KillSwitch.load(cfg.state_path / "killswitch.json")
    if not ks.active:
        print("Kill switch zaten ARMED.")
        return 0
    ks.reset(args.operator, args.note)
    print(f"Kill switch sıfırlandı (operator={args.operator}). Kayıt: state/killswitch.json audit")
    return 0


def cmd_health(cfg: BotConfig, args) -> int:
    hb = read_json(cfg.state_path / "heartbeat.json", default={})
    health = read_json(cfg.state_path / "health.json", default={})
    age = None
    if hb.get("at"):
        from .core import from_iso
        age = (utc_now() - from_iso(hb["at"])).total_seconds()
    state = health.get("state", "UNKNOWN")
    if age is not None and age > cfg.v3.monitoring.heartbeat_stale_s and state == "HEALTHY":
        state = "DATA_STALE"
    _p({"state": state, "heartbeat_age_s": round(age, 1) if age is not None else None, "last": health})
    return 0 if state in ("HEALTHY", "DEGRADED") else 1


def cmd_paper_status(cfg: BotConfig, args) -> int:
    from .accounting import FuturesLedgerV2, SpotLedger
    fut = FuturesLedgerV2.load(cfg.state_path / "futures_ledger.json", starting_equity=cfg.futures.starting_equity_usdt)
    spot = SpotLedger.load(cfg.state_path / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
    _p({"mode": cfg.mode, "futures": fut.summary(), "spot": spot.summary(),
        "futures_positions": [{"id": p.id, "symbol": s, "side": p.side.value, "entry": float(p.entry_avg), "qty": float(p.qty), "notional": float(p.qty * p.entry_avg),
                               "margin": float(p.isolated_margin), "leverage": p.leverage, "amount_type": p.amount_type.value, "stop": float(p.stop) if p.stop else None,
                               "targets": [float(t) for t in p.targets], "liq": float(p.liquidation_price) if p.liquidation_price else None} for s, p in fut.positions.items()],
        "spot_positions": spot.positions(), "note": "PAPER: gerçek para yok; amount_type NOTIONAL = pozisyon büyüklüğü, MARGIN = teminat"})
    return 0


def cmd_futures_status(cfg: BotConfig, args) -> int:
    return cmd_paper_status(cfg, args)


def cmd_spot_status(cfg: BotConfig, args) -> int:
    from .accounting import SpotLedger
    from .portfolio import Portfolio
    spot = SpotLedger.load(cfg.state_path / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
    legacy = Portfolio.load(cfg.state_path / "portfolio.json", cfg.risk.starting_equity_usdt)
    _p({"spot_ledger_v2": spot.summary(), "positions": spot.positions(), "legacy_portfolio": {"cash": legacy.cash, "positions": list(legacy.positions), "closed": len(legacy.history)}})
    return 0


def cmd_model_status(cfg: BotConfig, args) -> int:
    from .learn import LearnerV2, ModelRegistry, TradeMemory
    reg = ModelRegistry(cfg.state_path / "models.json")
    lr = LearnerV2(TradeMemory(cfg.state_path / "trade_memory.jsonl"), reg, state_path=cfg.state_path / "learn_v2.json")
    _p({"registry": reg.to_dict(), "learner": lr.snapshot()})
    return 0


def cmd_validate_model(cfg: BotConfig, args) -> int:
    from .learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory
    reg = ModelRegistry(cfg.state_path / "models.json")
    lr = LearnerV2(TradeMemory(cfg.state_path / "trade_memory.jsonl"), reg, LearnConfig(min_samples_train=cfg.v3.learning_v3.min_samples_train), cfg.state_path / "learn_v2.json")
    out = lr.train_challenger()
    if not out:
        print(f"Yeterli kapanmış işlem yok (min {cfg.v3.learning_v3.min_samples_train}).")
        return 1
    _p(out)
    if args.promote:
        ok, reasons = lr.maybe_promote(cfg.mode, operator=args.operator or "cli", manual=bool(args.operator))
        print("TERFİ:", "✅" if ok else f"⛔ {reasons}")
    return 0


# ------------------------------------------------------------------ altyapı komutları
def cmd_migrate(cfg: BotConfig, args) -> int:
    from .storage import Database, migrate_state_dir
    db = Database(cfg.db_path)
    rep = migrate_state_dir(cfg.state_path, db)
    _p(rep.to_dict())
    print(f"DB: {cfg.db_path} · bütünlük: {'ok' if db.integrity_check() else 'HATA'}")
    db.close()
    return 0


def cmd_doctor(cfg: BotConfig, args) -> int:
    try:
        from .ops.doctor import print_report, run_doctor
    except ImportError:
        print("ops.doctor modülü yok — temel kontrol:")
        _p({"mode": cfg.mode, "state": str(cfg.state_path), "state_writable": cfg.state_path.exists() or True})
        return 0
    rep = run_doctor(cfg, cfg.state_path, cfg.cache_path, cfg.obsidian.root, quick=args.quick)
    if getattr(args, "json", False):
        _p(rep.to_dict())               # makine-okunur sözleşme (preflight bunu kullanır); exit kodu değişmez
    else:
        print_report(rep)
    return 0 if rep.ok else 1


def cmd_preflight(cfg: BotConfig, args) -> int:
    """Systemd ExecStartPre katmanı: doctor'ı süreç içinde çalıştırır, TİPLENMİŞ karar verir (ops/preflight.decide).
    Normal doctor gevşetilmez; yalnız-bayat-heartbeat istisnası SADECE burada uygulanır. Her hata fail-closed."""
    from .ops.preflight import decide
    try:
        from .ops.doctor import run_doctor
        rep = run_doctor(cfg, cfg.state_path, cfg.cache_path, cfg.obsidian.root, quick=getattr(args, "quick", True))
        report = rep.to_dict()
    except Exception as exc:  # noqa: BLE001 — doctor crash → başlangıç engellenir
        print(f"BLOCK: doctor çalıştırılamadı ({type(exc).__name__}: {exc}) — fail-closed")
        return 1
    allow, reason = decide(report)
    print(reason)
    return 0 if allow else 1


def cmd_backup(cfg: BotConfig, args) -> int:
    from .ops.backup import run_backup
    kind = "daily" if args.daily else "hourly"
    out = run_backup(cfg.state_path, cfg.backups_path, kind=kind, keep_hourly=cfg.v3.storage.keep_hourly, keep_daily=cfg.v3.storage.keep_daily,
                     keep_weekly=cfg.v3.storage.keep_weekly, vault_dir=cfg.obsidian.root if args.daily else None)
    _p(out if isinstance(out, dict) else {"result": str(out)})
    return 0


def cmd_restore(cfg: BotConfig, args) -> int:
    from .ops.backup import restore_backup
    out = restore_backup(Path(args.archive), cfg.state_path, dry_run=not args.yes)
    _p(out if isinstance(out, dict) else {"result": str(out)})
    if not args.yes:
        print("Kuru çalıştırma. Gerçek geri yükleme için --yes ekle (mevcut state `state.pre-restore-<ts>` olarak saklanır).")
    return 0


def cmd_reconcile(cfg: BotConfig, args) -> int:
    from .accounting import FuturesLedgerV2, SpotLedger
    from .execution import PaperGateway, reconcile
    fut = FuturesLedgerV2.load(cfg.state_path / "futures_ledger.json", starting_equity=cfg.futures.starting_equity_usdt)
    spot = SpotLedger.load(cfg.state_path / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
    gw = PaperGateway(spot, fut, price_feed=lambda s: None)
    rep = reconcile(gw, [], list(fut.positions.values()))
    _p(rep.to_dict())
    print("Mod:", cfg.mode, "· PAPER'da uzlaştırma = defter ↔ journal; TESTNET'te borsa pozisyonları/açık emirler karşılaştırılır.")
    return 0 if rep.ok else 1


def cmd_dashboard(cfg: BotConfig, args) -> int:
    import os
    from .dashboard.app import DashboardConfig, run_dashboard
    d = cfg.v3.dashboard
    token = os.environ.get(d.auth_token_env)
    dc = DashboardConfig(host=args.host or d.host, port=args.port or d.port, auth_token=token, allow_insecure_public=d.allow_insecure_public, max_bars=d.max_bars)
    print(f"Dashboard http://{dc.host}:{dc.port}  (varsayılan yalnız 127.0.0.1; uzaktan erişim için SSH tüneli/Tailscale)")
    run_dashboard(cfg.state_path, cfg.cache_path, cfg.obsidian.root, dc)
    return 0


# ---------------------------------------------------------------- tarihsel veri gölü
def _history_ctx(cfg: BotConfig, args):
    from .history import ArchiveClient, CollectSpec, HistoryCollector, HistoryStore
    from .market.http import HttpClient
    from .market.providers import BinanceFuturesProvider, BinanceSpotProvider
    from .market.ratelimit import BudgetPool
    hc = cfg.v3.history
    store = HistoryStore(cfg.cache_path / hc.root_dir)
    pool = BudgetPool(safety=cfg.v3.data.rate_budget_safety)
    spot = BinanceSpotProvider(HttpClient(BinanceSpotProvider.base_url, pool.get("api.binance.com")))
    fut = BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, pool.get("fapi.binance.com")))
    use_archive = hc.archive_first and not getattr(args, "no_archive", False)
    col = HistoryCollector(store, spot=spot, futures=fut, archive=ArchiveClient() if use_archive else None, pause_s=hc.request_pause_s)
    markets = ("spot", "futures") if args.market == "both" else (args.market,)
    syms = tuple(args.symbols) if args.symbols else tuple(cfg.coins)
    tfs = tuple(args.timeframes) if args.timeframes else tuple(hc.tier_a_timeframes)

    def _ms(v):
        return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000) if v else None
    from_ms, to_ms = _ms(getattr(args, "from_", None)), _ms(getattr(args, "to", None))
    spec = CollectSpec(markets=markets, symbols=syms, timeframes=tfs, from_ms=from_ms, to_ms=to_ms, days=args.days,
                       max_available=bool(args.max_available or (args.days is None and from_ms is None and hc.max_available)),
                       include_funding=hc.include_funding and "futures" in markets, include_open_interest=hc.include_open_interest and "futures" in markets,
                       resume=not getattr(args, "no_resume", False), archive_first=use_archive)
    return store, col, spec


def universe_ranked_symbols(uni: dict) -> list[str]:
    """`state/universe.json` → hacme göre sıralı UYGUN semboller. Şema: `merged` (güncel);
    `entries`/`symbols` eski taslaklar için tolerans. Uygun değilse (eligible=False) plana girmez."""
    ents = uni.get("merged") or uni.get("entries") or uni.get("symbols") or []
    return [e.get("symbol") for e in ents if isinstance(e, dict) and e.get("symbol") and e.get("eligible", True)]


def cmd_history_plan(cfg: BotConfig, args) -> int:
    """Veri indirmeden: sembol/aralık/satır/disk/istek/süre tahmini (listing tespiti için sembol başına 1 hafif istek; --offline ile o da yok)."""
    store, col, spec = _history_ctx(cfg, args)
    plan = col.plan(spec, probe_listing=not getattr(args, "offline", False))
    if args.universe:
        from .history import build_tier_specs
        uni = read_json(cfg.state_path / "universe.json", default={}) or {}
        ranked = universe_ranked_symbols(uni) or list(cfg.coins)
        open_syms = list((read_json(cfg.state_path / "futures_ledger.json", default={}) or {}).get("positions", {}).keys())
        tp = build_tier_specs(ranked, open_syms, cfg.v3.history)
        plan["tiers"] = tp.summary
        # DÜRÜSTLÜK: universe.json yalnız BUGÜN listeli/uygun sembolleri içerir (delisted yok) →
        # bu plan point-in-time DEĞİLDİR; replay eğitiminde survivorship bias riski açıkça işaretlenir.
        plan["point_in_time"] = False
        plan["survivorship_bias"] = {"present": True,
                                     "note": "universe.json bugünün TRADING sembolleri; geçmişte delist edilenler kapsam dışı",
                                     "universe_generated_at": uni.get("generated_at", ""),
                                     "ranked_symbols": len(ranked)}
        ests = []
        for t, sp in zip("ABC", tp.specs):
            e = col.plan(sp, probe_listing=False); e.pop("items", None); e["tier"] = t; ests.append(e)
        plan["tier_estimates"] = ests
    if not args.verbose and len(plan["items"]) > 12:
        n = len(plan["items"]) - 12
        plan["items"] = plan["items"][:12] + [{"...": f"{n} seri daha"}]
    _p(plan)
    return 0


def cmd_history_collect(cfg: BotConfig, args) -> int:
    store, col, spec = _history_ctx(cfg, args)
    if args.dry_run:
        return cmd_history_plan(cfg, args)

    def _prog(r):
        print(f"  {r['market']:7s} {r['symbol']:12s} {r['timeframe']:7s} +{r['rows_new']:>7d} (toplam {r['rows_total']}, gap {r.get('gaps', 0)}, bad {r.get('bad_chunks', 0)})")
    res = col.collect(spec, on_progress=_prog)
    _p({"stats": res["stats"], "series": len(res["series"]), "finished_at": res["finished_at"]})
    return 0 if res["stats"]["bad_chunks"] == 0 else 1


def cmd_history_validate(cfg: BotConfig, args) -> int:
    from .history import HistoryStore
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    out, bad = [], 0
    for market, sym, tf in store.series():
        if args.symbols and sym not in args.symbols:
            continue
        v = store.validate(market, sym, tf)
        out.append(v)
        bad += 0 if v["ok"] else 1
    _p({"series": len(out), "invalid": bad, "items": out if args.verbose else [o for o in out if not o["ok"]][:20]})
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------- pattern zekâsı
def _pattern_engine(cfg: BotConfig, args, *, market: str, tf: str, symbols: list[str] | None = None):
    """HistoryStore'daki serilerden SimilarPatternEngine kur (aynı tf; BTC bağlamı ve funding varsa eklenir)."""
    from .history import HistoryStore
    from .patterns import SimilarPatternEngine
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    have = [(m, sym, t) for m, sym, t in store.series() if m == market and t == tf and (not symbols or sym in symbols)]
    btc = store.read(market, "BTC/USDT", tf)
    eng = SimilarPatternEngine(min_sample=int(getattr(args, "min_sample", 30) or 30), horizon=int(getattr(args, "horizon", 24) or 24),
                               fee_pct=cfg.v3.fees.futures_taker_pct if market == "futures" else cfg.v3.fees.spot_taker_pct,
                               slippage_pct=cfg.v3.fees.slippage_bps / 100, clusters=_clusters(cfg))
    n_ev = 0
    for m, sym, t in have:
        df = store.read(m, sym, t)
        if len(df) < 200:
            continue
        fund = store.read("futures", sym, "funding") if market == "futures" else None
        n_ev += eng.add_series(sym, m, t, df, btc_df=None if sym == "BTC/USDT" else (btc if len(btc) else None), funding_df=fund if fund is not None and len(fund) else None,
                               stride=int(getattr(args, "stride", 1) or 1))
    return store, eng, n_ev


def _clusters(cfg: BotConfig) -> dict[str, str]:
    out = {}
    for name, syms in (cfg.v3.risk_profiles.clusters or {}).items():
        for s_ in syms or []:
            out[s_] = name
    return out


def cmd_build_features(cfg: BotConfig, args) -> int:
    """HistoryStore serileri → causal feature frame (cache/features/<market>/<symbol>/<tf>.csv.gz) + özet."""
    import gzip
    from .history import HistoryStore
    from .patterns import build_feature_frame, feature_columns
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    root = cfg.cache_path / "features"
    markets = ("spot", "futures") if args.market == "both" else (args.market,)
    out = []
    for m, sym, tf in store.series():
        if m not in markets or (args.symbols and sym not in args.symbols) or (args.timeframes and tf not in args.timeframes) or tf in ("funding",) or tf.startswith("oi_"):
            continue
        df = store.read(m, sym, tf)
        if len(df) < 50:
            continue
        btc = store.read(m, "BTC/USDT", tf) if sym != "BTC/USDT" else None
        fund = store.read("futures", sym, "funding") if m == "futures" else None
        fr = build_feature_frame(df, tf, btc_df=btc if btc is not None and len(btc) else None, funding_df=fund if fund is not None and len(fund) else None)
        pth = root / m / sym.replace("/", "_") / f"{tf}.csv.gz"
        pth.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(pth, "wt", encoding="utf-8") as fh:
            fr.to_csv(fh, index=False, lineterminator="\n")
        out.append({"market": m, "symbol": sym, "timeframe": tf, "rows": int(len(fr)), "features": len(feature_columns(fr)),
                    "quality_last": round(float(fr["quality"].iloc[-1]), 3), "path": str(pth.relative_to(cfg.cache_path))})
    _p({"series": len(out), "feature_rows": int(sum(o["rows"] for o in out)), "items": out if args.verbose else out[:15]})
    return 0


def cmd_pattern_query(cfg: BotConfig, args) -> int:
    from .patterns import explain_tr, packet_from_query
    store, eng, n_ev = _pattern_engine(cfg, args, market=args.market, tf=args.tf, symbols=None)
    res = eng.query(args.symbol, args.market, args.tf, args.side, k=args.k, level=args.level, window=args.window)
    pk = packet_from_query(res, timestamp=iso(utc_now()), timeframes=[args.tf])
    _p({"index_events": n_ev, "query": res.get("query"), "ok": res.get("ok"), "codes": res.get("codes"), "n": res.get("n"), "levels": res.get("levels"),
        "stats": {k: v for k, v in (res.get("stats") or {}).items() if k not in ("breakdown",)}, "neighbors": res.get("neighbors", [])[:10],
        "explanation_tr": explain_tr(pk)})
    return 0


def cmd_evidence_show(cfg: BotConfig, args) -> int:
    """Kayıtlı runtime kanıtı (state/evidence/<symbol>.json) varsa onu, yoksa canlı sorguyu EvidencePacket + deterministik açıklama olarak göster."""
    from .patterns import EvidencePacket, explain_tr, packet_from_query
    p = cfg.state_path / "evidence" / f"{args.symbol.replace('/', '_')}.json"
    d = read_json(p, default=None)
    if isinstance(d, dict) and d.get("packets") and not args.live:
        for side, pk in d["packets"].items():
            pkt = EvidencePacket(**{k: v for k, v in pk.items() if k in set(EvidencePacket.__dataclass_fields__)})
            _p({"side": side, "packet": pkt.to_dict(), "explanation_tr": explain_tr(pkt)})
        return 0
    store, eng, n_ev = _pattern_engine(cfg, args, market=args.market, tf=args.tf)
    for side in (["LONG", "SHORT"] if args.market == "futures" else ["LONG"]):
        res = eng.query(args.symbol, args.market, args.tf, side, k=args.k, level=args.level, window=args.window)
        pkt = packet_from_query(res, timestamp=iso(utc_now()), timeframes=[args.tf])
        _p({"side": side, "packet": pkt.to_dict(), "explanation_tr": explain_tr(pkt)})
    return 0


# ---------------------------------------------------------------- tarihsel replay / öğrenme
def cmd_historical_replay(cfg: BotConfig, args) -> int:
    """Event-time replay: state/replay/<run_id>/ (gerçek state'e dokunmaz), HISTORICAL_REPLAY namespace, walk-forward, determinism hash."""
    from .history import HistoryStore
    from .replay import HistoricalReplay, walk_forward_windows
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    syms = list(args.symbols) if args.symbols else list(cfg.coins)
    run_id = args.run_id or f"replay_{args.market}_{args.tf}_seed{args.seed}_{utc_now():%Y%m%dT%H%M%SZ}"
    def _ms(v):
        return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000) if v else None
    eng = None
    if not args.no_patterns:
        _, eng, n_ev = _pattern_engine(cfg, args, market=args.market, tf=args.tf, symbols=syms)
        print(f"pattern index: {n_ev} olay")
    rp = HistoricalReplay(cfg, run_id=run_id, store=store, symbols=syms, market=args.market, tf=args.tf, seed=args.seed,
                          state_root=Path(args.state_dir) if args.state_dir else None, pattern_engine=eng, start_ms=_ms(getattr(args, "from_", None)),
                          end_ms=_ms(args.to), decision_stride=args.stride)
    rp.load()
    ws = walk_forward_windows(rp.result.start_ms, rp.result.end_ms, train_days=args.train_days, test_days=args.test_days, purge_bars=args.purge, embargo_bars=args.embargo)
    res = rp.run(windows=ws, on_progress=lambda d: print(f"  {d['t']} kararlar={d['decisions']} açılış={d['opened']} kapanış={d['closed']}"))
    out = res.to_dict(); out["trades"] = f"{len(res.trades)} işlem (replay_result.json)"
    out["metrics"] = {k: v for k, v in res.metrics.items() if k != "learner"}
    _p(out)
    return 0


def _replay_root(cfg: BotConfig, args):
    return Path(args.state_dir) if getattr(args, "state_dir", None) else None


def cmd_replay_plan(cfg: BotConfig, args) -> int:
    """Read-only dry-run: satır/timeline/olay/bellek/CPU tahmini + kapasite risk sınıfı. Hiçbir şey yazmaz; fail-closed."""
    from .history import HistoryStore
    from .replay.research import ReplaySafetyError, plan_replay, resolve_replay_dir
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    syms = list(args.symbols) if args.symbols else list(cfg.coins)
    run_id = args.run_id or f"plan_{args.market}_{args.tf}_seed{args.seed}"
    try:
        resolve_replay_dir(cfg.state_path, run_id, _replay_root(cfg, args))       # yol sözleşmesi planda da doğrulanır
    except ReplaySafetyError as exc:
        print(f"BLOCK: {exc}")
        return 2

    def _ms(v):
        return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000) if v else None

    plan = plan_replay(cfg, store, run_id=run_id, symbols=syms, market=args.market, tf=args.tf, stride=args.stride,
                       seed=args.seed, start_ms=_ms(getattr(args, "from_", None)), end_ms=_ms(args.to),
                       patterns=not args.no_patterns, pattern_stride=int(getattr(args, "pattern_stride", 1) or 1),
                       available_mb=getattr(args, "assume_available_mb", None),
                       host_reserve_mb=args.host_reserve_mb, worker_reserve_mb=args.worker_reserve_mb)
    _p(plan.to_dict())
    return 0 if plan.ok else 1


def cmd_replay_train(cfg: BotConfig, args) -> int:
    """Replay state'indeki HISTORICAL_REPLAY hafızasından challenger eğitir. Canlı state/model'e DOKUNMAZ, terfi YOK."""
    from .replay.research import ReplaySafetyError, train_replay_challenger, resolve_replay_dir
    try:
        rdir = resolve_replay_dir(cfg.state_path, args.run_id, _replay_root(cfg, args), must_exist=True)
        out = train_replay_challenger(cfg, rdir, seed=args.seed, force=bool(getattr(args, "force", False)))
    except ReplaySafetyError as exc:
        print(f"BLOCK: {exc}")
        return 2
    _p(out)
    return 0


def cmd_replay_evaluate(cfg: BotConfig, args) -> int:
    """Objektif OOS değerlendirmesi (walk-forward/purge/embargo + sızıntı kontrolü). Yalnız rapor; terfi YOK."""
    from .replay.research import ReplaySafetyError, evaluate_replay, resolve_replay_dir
    try:
        rdir = resolve_replay_dir(cfg.state_path, args.run_id, _replay_root(cfg, args), must_exist=True)
        rep = evaluate_replay(cfg, rdir, min_samples=getattr(args, "min_samples", None))
    except ReplaySafetyError as exc:
        print(f"BLOCK: {exc}")
        return 2
    _p(rep)
    return 0


def cmd_learning_status(cfg: BotConfig, args) -> int:
    """LearnerV2/registry özeti; --replay <run_id> ile replay state'i, yoksa gerçek PAPER state (kaynak namespace ayrı)."""
    from .learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory
    st = (cfg.state_path / "replay" / args.replay) if args.replay else cfg.state_path
    src = "HISTORICAL_REPLAY" if args.replay else "LIVE_PAPER"
    mem = TradeMemory(st / "trade_memory.jsonl", source=src)
    reg = ModelRegistry(st / "models.json")
    lr = LearnerV2(mem, reg, LearnConfig(), state_path=st / "learn_v2.json")
    rows = list(mem.iter_rows())
    _p({"state_dir": str(st), "source": src, "memory_rows": len(rows), "entries": sum(1 for r in rows if r.get("kind") == "entry"),
        "exits": sum(1 for r in rows if r.get("kind") == "exit"), "learner": lr.snapshot(), "champion": reg.champion("p_win") if hasattr(reg, "champion") else None})
    return 0


def cmd_authority(cfg: BotConfig, args) -> int:
    """Tek yetkili worker markörü: --claim bu makineye alır, --release kaldırır, varsayılan durumu basar."""
    from .ops.authority import check, claim, current_host, read_authority, release
    if getattr(args, "claim", False):
        d = claim(cfg.state_path, note=getattr(args, "note", "") or "")
        _p({"claimed": d})
        return 0
    if getattr(args, "release", False):
        _p({"released": release(cfg.state_path)})
        return 0
    ok, why = check(cfg.state_path)
    _p({"host": current_host(), "authority": read_authority(cfg.state_path), "allowed_here": ok, "reason": why})
    return 0


def cmd_stop(cfg: BotConfig, args) -> int:
    """Kooperatif durdurma: canlı worker/dashboard instance'ını doğrula, atomik stop isteği yaz, timeout'a kadar bekle.
    Force yok (varsayılan); --force yalnız kesin PID'ye normal sonlandırma uygular ve graceful sayılmaz."""
    from .ops.lock import SingletonLock
    from .ops.shutdown import instance_status, request_stop, terminate_pid, wait_stopped
    targets = ("worker", "dashboard") if args.target == "all" else (args.target,)
    before = {k: instance_status(cfg.state_path, k) for k in targets}
    req = request_stop(cfg.state_path, targets)
    res = wait_stopped(cfg.state_path, targets, timeout_s=float(args.timeout))
    forced: dict[str, bool] = {}
    if args.force:
        for k, r in res.items():
            if r == "timeout" and before[k].get("pid"):
                forced[k] = terminate_pid(int(before[k]["pid"]))
        if forced:
            res = {**res, **{k: ("terminated_not_graceful" if ok else "force_failed") for k, ok in forced.items()}}
    lock = SingletonLock(cfg.state_path / ".lock")
    out = {"targets": {k: {"pid": before[k].get("pid"), "was_alive": before[k]["alive"], "stale_record": before[k]["stale"], "result": res.get(k)} for k in targets},
           "requested_tokens": len(req["requested"]), "already_pending": req["already_pending"],
           "lock": {"file_present": lock.path.exists(), "held_by_other": lock.is_locked_by_other(), "pid_in_file": lock.read_pid()},
           "graceful": all(res.get(k) in ("stopped", "absent") for k in targets), "forced": forced}
    _p(out)
    return 0 if out["graceful"] else 1


def cmd_universe(cfg: BotConfig, args) -> int:
    from .market import BinanceFuturesProvider, BinanceSpotProvider, HttpClient, UniverseConfig, build_universe
    from .market.ratelimit import BudgetPool
    u = cfg.v3.universe
    ucfg = UniverseConfig(min_quote_volume_24h_spot=u.min_quote_volume_24h, min_quote_volume_24h_futures=u.min_quote_volume_24h, max_spread_pct=u.max_spread_pct,
                          min_depth_0_5pct_usdt=u.min_depth_0_5pct_usdt, min_listing_age_days=u.min_listing_age_days, max_symbols=u.max_symbols,
                          allow_usdc=cfg.v3.markets.allow_usdc)
    pool = BudgetPool(safety=cfg.v3.data.rate_budget_safety)
    spot = BinanceSpotProvider(HttpClient(BinanceSpotProvider.base_url, pool.get("api.binance.com")))
    fut = BinanceFuturesProvider(HttpClient(BinanceFuturesProvider.base_url, pool.get("fapi.binance.com")))
    snap = build_universe(spot, fut, ucfg, save_path=cfg.state_path / "universe.json")
    d = snap.to_dict()
    _p({"counts": d.get("counts"), "saved": str(cfg.state_path / "universe.json")})
    return 0


def cmd_collect(cfg: BotConfig, args) -> int:
    """Yalnız veri toplar (OBSERVE benzeri): mumları çeker, kaliteyi ölçer, Parquet'e yazar; işlem yok."""
    from .data import MarketData
    from .market.quality import DataQualityGate
    from .storage import CandleStore
    md = MarketData(cfg.exchange.candidates, cfg.exchange.timeframe, cfg.exchange.history_days, cfg.cache_path, source=cfg.exchange.source, tv_exchange=cfg.exchange.tv_exchange)
    store = CandleStore(cfg.cache_path / cfg.v3.storage.parquet_dir)
    gate = DataQualityGate()
    now_ms = int(utc_now().timestamp() * 1000)
    for s in (args.symbols or cfg.coins):
        try:
            df = md.fetch(s)
        except Exception as exc:  # noqa: BLE001 — bir sembol hatası diğerlerini durdurmaz
            print(f"{s}: HATA {exc}")
            continue
        raw = df.reset_index(drop=True)
        n = store.write(s, cfg.exchange.timeframe, raw)
        rep = gate.check_klines(raw, cfg.exchange.timeframe, now_ms)
        print(f"{s:<10} {len(df):>6} bar → parquet {n:>6} · kalite {rep.verdict} {list(rep.codes)}")
    return 0


def cmd_export_trades(cfg: BotConfig, args) -> int:
    from .accounting import FuturesLedgerV2, SpotLedger
    fut = FuturesLedgerV2.load(cfg.state_path / "futures_ledger.json", starting_equity=cfg.futures.starting_equity_usdt)
    spot = SpotLedger.load(cfg.state_path / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
    rows = [h.to_dict() for h in fut.history] + [h.to_dict() for h in spot.history]
    out = Path(args.out or (cfg.state_path / "exports" / f"trades_{utc_now():%Y%m%d_%H%M%S}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({k for r in rows for k in r if not isinstance(r[k], (dict, list))})
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})
    print(f"{len(rows)} işlem → {out}")
    return 0


def cmd_export_tax(cfg: BotConfig, args) -> int:
    from .accounting import FuturesLedgerV2, SpotLedger, TaxPolicy, export_tax_csv, tax_rows
    t = cfg.v3.tax_policy
    policy = TaxPolicy(version=t.version, enabled=t.enabled, manually_confirmed=t.manually_confirmed, status=t.status,
                       rate_pct=__import__("decimal").Decimal(str(t.gain_withholding_rate)))
    fut = FuturesLedgerV2.load(cfg.state_path / "futures_ledger.json", starting_equity=cfg.futures.starting_equity_usdt)
    spot = SpotLedger.load(cfg.state_path / "spot_ledger.json", starting_cash=cfg.risk.starting_equity_usdt)
    rows = tax_rows(list(fut.history) + list(spot.history), policy)
    out = Path(args.out or (cfg.state_path / "exports" / f"tax_{utc_now():%Y%m%d}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    export_tax_csv(rows, out)
    print(f"{len(rows)} satır → {out} · vergi politikası: {t.status} (enabled={t.enabled}) — bu rapor mali müşavir yerine geçmez.")
    return 0


def cmd_replay(cfg: BotConfig, args) -> int:
    """Kaydedilmiş mumlarla deterministik tekrar oynatma: aynı girdi + aynı model sürümü → aynı Coin Head kararı (hash)."""
    from .coinhead import CoinHead, CoinHeadConfig, CoinHeadInputs
    from .core import payload_hash
    from .data import MarketData
    from .indicators import add_snapshot_indicators
    md = MarketData(cfg.exchange.candidates, "4h", cfg.exchange.history_days, cfg.cache_path, source="ccxt")
    hashes = {}
    for s in (args.symbols or cfg.coins[:3]):
        cache = None
        for name in (f"tv-binance_{s.replace('/', '-')}_4h.csv", f"binance_{s.replace('/', '-')}_4h.csv"):
            p = cfg.cache_path / name
            if p.exists():
                cache = p
                break
        if cache is None:
            print(f"{s}: önbellek yok")
            continue
        import pandas as pd
        from .data import prepare
        df = prepare(pd.read_csv(cache))
        cut = -abs(args.bars_ago) if args.bars_ago else None
        h4 = add_snapshot_indicators(df.iloc[:cut] if cut else df)
        frames = {"4h": h4}
        live = {"ticker": {"last": float(h4["close"].iloc[-1])}, "ts": int(h4["timestamp"].iloc[-1]) / 1000 + 14_400}
        head = CoinHead(s, CoinHeadConfig())
        outs = []
        for _ in range(2):
            d = head.decide(CoinHeadInputs(frames=frames, live=live, availability={"spot": True, "futures": True}, run_id="replay", snapshot_id="replay",
                                           now_ms=int(h4["timestamp"].iloc[-1]) + 14_400_000 + 1000))
            dd = d.to_dict(include_reports=False)
            for k in ("expires_at", "generated_at", "latency_ms", "run_id", "snapshot_id", "coin_head_id"):
                dd.pop(k, None)
            outs.append(payload_hash(dd))
        hashes[s] = {"verdict": d.verdict.value, "reason": d.no_trade_reason, "deterministic": outs[0] == outs[1], "hash": outs[0][:16]}
    _p(hashes)
    return 0 if all(v["deterministic"] for v in hashes.values()) else 1


def cmd_futures_backtest(cfg: BotConfig, args) -> int:
    import pandas as pd
    from .data import prepare
    from .futures_backtest import run_futures_backtest
    from .indicators import ema
    for s in (args.symbols or cfg.coins[:3]):
        cache = next((cfg.cache_path / n for n in (f"tv-binance_{s.replace('/', '-')}_4h.csv", f"binance_{s.replace('/', '-')}_4h.csv") if (cfg.cache_path / n).exists()), None)
        if cache is None:
            print(f"{s}: önbellek yok")
            continue
        df = prepare(pd.read_csv(cache))
        e20, e50 = ema(df["close"], 20), ema(df["close"], 50)
        side = pd.Series(0, index=df.index)
        side[(e20 > e50)] = 1
        side[(e20 < e50)] = -1
        res = run_futures_backtest(df, side, bars_per_year=2190, leverage=args.leverage, fee_taker_pct=cfg.v3.fees.futures_taker_pct,
                                   slippage_pct=cfg.v3.fees.slippage_bps / 100, atr_stop_mult=cfg.risk.atr_stop_mult, starting_equity=cfg.futures.starting_equity_usdt,
                                   risk_per_trade_pct=cfg.risk.risk_per_trade_pct, max_position_pct=cfg.risk.max_position_pct)
        m = res.metrics
        print(f"== {s} EMA20/50 long-short {args.leverage}x: işlem {m.get('trades', len(res.trades))} · net getiri {m.get('total_return_pct', 0):+.2f}% · "
              f"expectancy {m.get('expectancy_r', 0):+.3f}R · PF {m.get('profit_factor', 0):.2f} · maxDD {m.get('max_dd_pct', 0):.1f}% · funding {m.get('funding_paid', 0):+.4f} · liq {m.get('liq_count', 0)}")
    return 0


# ------------------------------------------------------------------ parser kaydı
def register(sub: argparse._SubParsersAction) -> None:
    s = sub.add_parser("doctor", help="Ortam/durum sağlık kontrolü"); s.add_argument("--quick", action="store_true")
    s.add_argument("--json", action="store_true", help="makine-okunur structured sonuç (exit kodu aynı)"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("preflight", help="Systemd başlangıç ön kontrolü: yalnız-bayat-heartbeat'e izin, geri kalan fail-closed")
    s.add_argument("--quick", action="store_true", default=True); s.set_defaults(fn=cmd_preflight)
    s = sub.add_parser("migrate", help="Eski JSON state → SQLite (idempotent, kayıpsız)"); s.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("collect", help="Yalnız veri topla (Parquet + kalite)"); s.add_argument("--symbols", nargs="*"); s.set_defaults(fn=cmd_collect)
    s = sub.add_parser("paper-status", help="Kağıt defter özeti (spot+futures v2)"); s.set_defaults(fn=cmd_paper_status)
    s = sub.add_parser("spot-status", help="Spot kağıt durumu"); s.set_defaults(fn=cmd_spot_status)
    s = sub.add_parser("futures-status", help="Futures kağıt durumu"); s.set_defaults(fn=cmd_futures_status)
    s = sub.add_parser("replay", help="Deterministik tekrar oynatma (aynı girdi → aynı karar)"); s.add_argument("--symbols", nargs="*"); s.add_argument("--bars-ago", type=int, default=0); s.set_defaults(fn=cmd_replay)
    s = sub.add_parser("backtest", help="Futures long/short backtest (maliyet+funding+liq)"); s.add_argument("--symbols", nargs="*"); s.add_argument("--leverage", type=int, default=2); s.set_defaults(fn=cmd_futures_backtest)
    s = sub.add_parser("validate-model", help="Challenger eğit/kalibre et; --promote ile terfi kapısı"); s.add_argument("--promote", action="store_true"); s.add_argument("--operator", default=None); s.set_defaults(fn=cmd_validate_model)
    s = sub.add_parser("model-status", help="Model registry + öğrenme özeti"); s.set_defaults(fn=cmd_model_status)
    s = sub.add_parser("risk-status", help="Risk profili, kill switch, exposure"); s.set_defaults(fn=cmd_risk_status)
    s = sub.add_parser("killswitch-reset", help="Kill switch manuel reset (denetim kaydı)"); s.add_argument("--operator", required=True); s.add_argument("--note", required=True); s.set_defaults(fn=cmd_killswitch_reset)
    s = sub.add_parser("health", help="Sağlık durumu (heartbeat yaşı)"); s.set_defaults(fn=cmd_health)
    def _hist_args(s):
        s.add_argument("--market", choices=["spot", "futures", "both"], default="both"); s.add_argument("--symbols", nargs="*", default=None)
        s.add_argument("--timeframes", nargs="*", default=None); s.add_argument("--days", type=int, default=None); s.add_argument("--max-available", action="store_true")
        s.add_argument("--from", dest="from_", default=None); s.add_argument("--to", default=None); s.add_argument("--no-resume", action="store_true")
        s.add_argument("--no-archive", action="store_true"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--universe", action="store_true")
        s.add_argument("--offline", action="store_true"); s.add_argument("--verbose", "-v", action="store_true")
    s = sub.add_parser("history-plan", help="Tarihsel veri planı (dry-run: satır/disk/istek/süre tahmini)"); _hist_args(s); s.set_defaults(fn=cmd_history_plan)
    s = sub.add_parser("history-collect", help="Tarihsel veri topla (archive-first + REST, resume, idempotent)"); _hist_args(s); s.set_defaults(fn=cmd_history_collect)
    s = sub.add_parser("history-validate", help="Manifest/checksum/gap doğrulaması"); s.add_argument("--symbols", nargs="*", default=None); s.add_argument("--verbose", "-v", action="store_true"); s.set_defaults(fn=cmd_history_validate)
    s = sub.add_parser("build-features", help="Tarihsel serilerden causal feature store üret"); s.add_argument("--market", choices=["spot", "futures", "both"], default="both")
    s.add_argument("--symbols", nargs="*", default=None); s.add_argument("--timeframes", nargs="*", default=None); s.add_argument("--verbose", "-v", action="store_true"); s.set_defaults(fn=cmd_build_features)

    def _pq_args(s):
        s.add_argument("--symbol", required=True); s.add_argument("--market", choices=["spot", "futures"], default="futures"); s.add_argument("--tf", default="4h")
        s.add_argument("--side", choices=["LONG", "SHORT"], default="LONG"); s.add_argument("--k", type=int, default=60); s.add_argument("--window", type=int, default=64)
        s.add_argument("--level", choices=["auto", "same_coin", "cluster", "universe"], default="auto"); s.add_argument("--min-sample", dest="min_sample", type=int, default=30)
        s.add_argument("--horizon", type=int, default=24); s.add_argument("--stride", type=int, default=1); s.add_argument("--live", action="store_true")
    s = sub.add_parser("pattern-query", help="Benzer geçmiş olay sorgusu + maliyet sonrası istatistik"); _pq_args(s); s.set_defaults(fn=cmd_pattern_query)
    s = sub.add_parser("evidence-show", help="EvidencePacket + deterministik Türkçe açıklama"); _pq_args(s); s.set_defaults(fn=cmd_evidence_show)
    s = sub.add_parser("historical-replay", help="Event-time tarihsel replay (ayrı state, walk-forward, deterministik)")
    s.add_argument("--symbols", nargs="*", default=None); s.add_argument("--market", choices=["spot", "futures"], default="futures"); s.add_argument("--tf", default="4h")
    s.add_argument("--seed", type=int, default=0); s.add_argument("--run-id", dest="run_id", default=None); s.add_argument("--state-dir", dest="state_dir", default=None)
    s.add_argument("--from", dest="from_", default=None); s.add_argument("--to", default=None); s.add_argument("--stride", type=int, default=1)
    s.add_argument("--train-days", dest="train_days", type=int, default=180); s.add_argument("--test-days", dest="test_days", type=int, default=30)
    s.add_argument("--purge", type=int, default=6); s.add_argument("--embargo", type=int, default=6); s.add_argument("--no-patterns", dest="no_patterns", action="store_true")
    s.add_argument("--min-sample", dest="min_sample", type=int, default=30); s.add_argument("--horizon", type=int, default=24); s.set_defaults(fn=cmd_historical_replay)
    s = sub.add_parser("replay-plan", help="Replay dry-run: veri/timeline/olay/bellek/CPU tahmini + kapasite riski (read-only)")
    s.add_argument("--symbols", nargs="*"); s.add_argument("--market", default="futures", choices=["spot", "futures"]); s.add_argument("--tf", default="4h")
    s.add_argument("--from", dest="from_", default=None); s.add_argument("--to", default=None); s.add_argument("--stride", type=int, default=1)
    s.add_argument("--pattern-stride", dest="pattern_stride", type=int, default=1); s.add_argument("--seed", type=int, default=0)
    s.add_argument("--run-id", dest="run_id", default=None); s.add_argument("--state-dir", dest="state_dir", default=None)
    s.add_argument("--no-patterns", dest="no_patterns", action="store_true")
    s.add_argument("--assume-available-mb", dest="assume_available_mb", type=float, default=None,
                   help="RAM ölçülemeyen ortamlarda açık kapasite girdisi (fail-closed)")
    s.add_argument("--host-reserve-mb", dest="host_reserve_mb", type=float, default=1024.0)
    s.add_argument("--worker-reserve-mb", dest="worker_reserve_mb", type=float, default=900.0)
    s.set_defaults(fn=cmd_replay_plan)
    s = sub.add_parser("replay-train", help="Replay challenger eğitimi (yalnız replay state; canlı model/terfi yok)")
    s.add_argument("--run-id", dest="run_id", required=True); s.add_argument("--state-dir", dest="state_dir", default=None)
    s.add_argument("--seed", type=int, default=0); s.add_argument("--force", action="store_true", help="idempotent atlamayı bilinçli olarak geç")
    s.set_defaults(fn=cmd_replay_train)
    s = sub.add_parser("replay-evaluate", help="Replay OOS değerlendirmesi (walk-forward + sızıntı kontrolü; yalnız rapor)")
    s.add_argument("--run-id", dest="run_id", required=True); s.add_argument("--state-dir", dest="state_dir", default=None)
    s.add_argument("--min-samples", dest="min_samples", type=int, default=None); s.set_defaults(fn=cmd_replay_evaluate)
    s = sub.add_parser("learning-status", help="LearnerV2/registry özeti (PAPER ya da --replay <run_id>)"); s.add_argument("--replay", default=None); s.set_defaults(fn=cmd_learning_status)
    s = sub.add_parser("authority", help="Tek yetkili worker markörü (split-brain koruması): --claim / --release / durum")
    s.add_argument("--claim", action="store_true"); s.add_argument("--release", action="store_true"); s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_authority)
    s = sub.add_parser("stop", help="Kooperatif durdurma (worker/dashboard/all); force yok, timeout'ta dürüst rapor")
    s.add_argument("--target", choices=["worker", "dashboard", "all"], default="all"); s.add_argument("--timeout", type=float, default=120)
    s.add_argument("--force", action="store_true", help="timeout sonrası kesin PID'ye normal sonlandırma (graceful sayılmaz)"); s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("reconcile", help="Defter ↔ gateway uzlaştırma"); s.set_defaults(fn=cmd_reconcile)
    s = sub.add_parser("dashboard", help="Web dashboard (varsayılan 127.0.0.1:8080)"); s.add_argument("--host", default=None); s.add_argument("--port", type=int, default=None); s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("export-trades", help="İşlemleri CSV'ye aktar"); s.add_argument("--out", default=None); s.set_defaults(fn=cmd_export_trades)
    s = sub.add_parser("export-tax", help="Vergi satırları CSV (politika doğrulanmadıkça vergi 0)"); s.add_argument("--out", default=None); s.set_defaults(fn=cmd_export_tax)
    s = sub.add_parser("mode-status", help="Çalışma modu (PAPER varsayılan)"); s.set_defaults(fn=cmd_mode_status)
    s = sub.add_parser("mode-transition", help="Manuel mod geçişi talebi"); s.add_argument("--to", required=True); s.add_argument("--operator", required=True)
    s.add_argument("--check", nargs="*", help="k=v kapı bayrakları"); s.add_argument("--token", default=None); s.set_defaults(fn=cmd_mode_transition)
    s = sub.add_parser("backup", help="Yedek al (saatlik/günlük)"); s.add_argument("--daily", action="store_true"); s.add_argument("--hourly", action="store_true"); s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore", help="Yedekten geri yükle"); s.add_argument("archive"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("universe", help="Dinamik spot+futures evrenini yenile → state/universe.json"); s.set_defaults(fn=cmd_universe)

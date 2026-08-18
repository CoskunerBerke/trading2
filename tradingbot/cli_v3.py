"""v3 CLI komutları — mevcut komutlar bozulmaz; yeni komutlar `cli.build_parser` içine kaydedilir.

  doctor · migrate · collect · analyze(v3) · paper-status · spot-status · futures-status · replay · backtest(futures)
  validate-model · model-status · risk-status · health · reconcile · dashboard · export-trades · export-tax · mode-status
  mode-transition · killswitch-reset · backup · restore · universe
Gerçek emir komutu YOKTUR; LIVE yolu bu sürümde kapalıdır.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from .config import BotConfig
from .core import ExecutionDisabledError, read_json, utc_now

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
    print_report(rep)
    return 0 if rep.ok else 1


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
    s = sub.add_parser("doctor", help="Ortam/durum sağlık kontrolü"); s.add_argument("--quick", action="store_true"); s.set_defaults(fn=cmd_doctor)
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

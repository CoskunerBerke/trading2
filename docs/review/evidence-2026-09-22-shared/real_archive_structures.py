# -*- coding: utf-8 -*-
"""GERÇEK ARŞİV ÖRNEKLEMİ — ortak yapı analizi (structures_v1) ve T2/M2 kararına etkisi.

ETİKET: veri GERÇEKTİR (yerel `bn_archive` kopyası: Binance USDⓈ-M perpetual 1d/4h + funding, son bar 2026-09-19);
kod bu dalın kodudur. SALT OKUMA: arşive yazılmaz; replay durumu geçici dizine yazılır ve silinir.
KÂRLILIK İDDİASI YOKTUR ve PnL RAPORLANMAZ; parametre taraması YOKTUR (config.yaml değerleri aynen).

A) Analiz bütünlüğü (40 coin, 1d son 365 bar + 4h son 720 bar, her kapanmış barda yeniden analiz — ileri yürüyüş):
   yalnız kapanmış bar, dayanak/teyit zamanı karar anından sonra değil, AYNI kimlik → AYNI dayanak/tetik/geçersizlik,
   teyit zamanı sabit, durum geçişi tek yönlü (FORMING → CONFIRMED → BROKEN/EXPIRED; geri dönüş yok).
B) Karar etkisi (T2 ve M2, 40 coin, 2025-09-19 → 2026-09-19, replay strateji modu = canlı defterle aynı
   `paper_rules.decide_with_structures` + `strategy_paper.apply_action`): yapı OFF ve ENFORCE; karar sayımları,
   açılan işlem sayısı, örnek işlem/iptal kimlikleri. Box (5m) ve Formasyon (15m/1h) için arşivde veri YOK.

Çalıştırma (depo kökünden):  python docs/review/evidence-2026-09-22-shared/real_archive_structures.py
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT))
OUT = Path(__file__).resolve().parent
ARCH = Path(r"C:\Users\berke\research\bn_archive\history")
TF_MS = {"1d": 86_400_000, "4h": 14_400_000}


def _rows(df):
    return [{"timestamp": int(r.timestamp), "open": float(r.open), "high": float(r.high), "low": float(r.low),
             "close": float(r.close), "volume": float(r.volume)} for r in df.itertuples()]


def _universe() -> list[str]:
    from tradingbot.config import load_config
    cfg = load_config(str(WT / "config.yaml"))
    return list(cfg.v3.entry_universe.symbols)


def part_a(symbols: list[str]) -> dict:
    """İleri yürüyüş bütünlük denetimi — denetim mantığı `tradingbot.structures.audit` (birim testiyle AYNI kod)."""
    from tradingbot.history import HistoryStore
    from tradingbot.structures.audit import walk_forward_audit
    hs = HistoryStore(ARCH)
    out: dict = {"symbols": len(symbols), "by_tf": {}}
    for tf, n_eval in (("1d", 365), ("4h", 720)):
        agg = {"analyses": 0, "violations": Counter(), "info": Counter(), "examples": [], "distinct_records": 0,
               "records_by_name": Counter(), "confirmations_by_name": Counter(), "rejects": Counter(), "symbols_without_rows": []}
        for sym in symbols:
            rows = _rows(hs.read("futures", sym, tf))
            if len(rows) < 30:
                agg["symbols_without_rows"].append(sym)
                continue
            r = walk_forward_audit(rows, market="USDM_PERP", symbol=sym, timeframe=tf, step_ms=TF_MS[tf], n_eval=n_eval)
            agg["analyses"] += r["analyses"]
            agg["distinct_records"] += r["distinct_records"]
            for k in ("violations", "info", "records_by_name", "confirmations_by_name", "rejects"):
                agg[k].update(r[k])
            agg["examples"] += r["examples"][: max(0, 12 - len(agg["examples"]))]
        out["by_tf"][tf] = {k: (dict(v.most_common()) if isinstance(v, Counter) else v) for k, v in agg.items()}
    return out


def _flatten_counts(obj, top: int = 12) -> dict:
    """`rejections` iç içe olabilir ({gerekçe: {sembol: n}} ya da {sembol: {gerekçe: n}}): sayısal yaprakları gerekçe
    anahtarıyla toplar (sembol adları anahtar sayılmaz)."""
    c: Counter = Counter()

    def walk(o, key=None):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, key if ("/" in str(k)) else k)
        elif isinstance(o, (int, float)) and key is not None:
            c[str(key)] += o
    walk(obj)
    return dict(c.most_common(top))


def _hold_hours(trades: list[dict]) -> dict:
    """Kapanmış işlemlerin tutma süresi (saat): medyan/ortalama/en az/en çok — PnL DEĞİL, yalnız süre."""
    hs = []
    for t in trades:
        try:
            a = _dt.datetime.fromisoformat(str(t.get("opened_at")).replace("Z", "+00:00"))
            b = _dt.datetime.fromisoformat(str(t.get("closed_at")).replace("Z", "+00:00"))
            hs.append((b - a).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            continue
    if not hs:
        return {"n": 0}
    hs.sort()
    return {"n": len(hs), "median": round(hs[len(hs) // 2], 1), "mean": round(sum(hs) / len(hs), 1),
            "min": round(hs[0], 1), "max": round(hs[-1], 1)}


def part_b(symbols: list[str], names=("t2_trend_regime", "m2_tsmom28"), modes=("OFF", "ENFORCE")) -> dict:
    from tradingbot.config import load_config
    from tradingbot.history import HistoryStore
    from tradingbot.paper_rules import build_params, replay_strategy
    from tradingbot.replay import HistoricalReplay

    def _ms(v: str) -> int:
        return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)

    res_all: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="structures_evidence_"))
    try:
        for name in names:
            for mode in modes:
                cfg = load_config(str(WT / "config.yaml"))
                sp = cfg.v3.strategy_paper
                book = {"name": sp.name, "atr_mult": sp.atr_mult, "rule_params": sp.rule_params,
                        "breakeven_at_mfe_r": sp.breakeven_at_mfe_r, "starting_equity_usdt": sp.starting_equity_usdt}
                if name != sp.name:
                    book = next(dict(x) for x in (sp.extra or []) if x.get("name") == name)
                # canlı defterin KENDİ ayarları (config.yaml) — tarama/ayar YOK
                params = build_params(name, atr_mult=float(book.get("atr_mult") or 3.0), rule_params=dict(book.get("rule_params") or {}))
                cfg.v3.futures_v3.breakeven_at_mfe_r = float(book.get("breakeven_at_mfe_r") or 0.0)
                cfg.futures.starting_equity_usdt = float(book.get("starting_equity_usdt") or 100.0)
                # GERÇEK BORSA KURALLARI (2026-09-23): replay filtreleri `cfg.cache_path/symbol_filters.json`dan okur;
                # ölçüm ağacında `data/` yoktur → VARSAYILAN kurallar (tick 0,01) ve düşük fiyatlı coinde bozuk giriş
                # fiyatı (G/USDT 0,0043 → 0,01, ilk gözlemde "likidasyon"). Arşivin doğrulanmış filtre dosyası kullanılır.
                cfg.exchange.cache_dir = str(ARCH.parent)
                run_id = "structures_evidence_%s_%s" % (name, mode.lower())
                t0 = time.time()
                rp = HistoricalReplay(cfg, run_id=run_id, store=HistoryStore(ARCH), symbols=symbols, market="futures", tf="4h",
                                      seed=0, state_root=tmp, pattern_engine=None, start_ms=_ms("2025-09-19"),
                                      end_ms=_ms("2026-09-19"), economics_gate=False, spot_listed=set(symbols),
                                      strategy=replay_strategy(name, params=params, mode=mode), structures_mode=None)
                rp.load(require_all=True)
                if getattr(rp, "filters_source", None) != "binance_api":
                    raise RuntimeError("replay VARSAYILAN borsa kurallarıyla koşacaktı (filters_source=%r)" % getattr(rp, "filters_source", None))
                from tradingbot.accounting.models import MarketType as _MT
                missing = [s_ for s_ in symbols if not rp.filters.has(s_, _MT.USDM_PERP)]
                if missing:
                    raise RuntimeError("doğrulanmış filtresi olmayan semboller: %s" % missing)
                res = rp.run(windows=[])
                log = list(getattr(rp, "_structure_log", []) or [])
                acts = Counter(x["action"] for x in log)
                reasons = Counter("%s:%s" % (x["action"], x["reason_code"]) for x in log)
                applied = Counter("%s→%s" % (x["action"], x.get("applied")) for x in log)
                trades = rp.ledger2.history_dicts()            # defterin KENDİ kapanış kayıtları (features dahil)
                with_struct = [t for t in trades if isinstance(t.get("features"), dict) and (t["features"].get("structure") or {}).get("pattern_id")]
                struct_exit = [t for t in trades if isinstance(t.get("features"), dict) and t["features"].get("exit_structure")]
                open_struct = [p for p in rp.ledger2.positions.values() if (p.features.get("structure") or {}).get("pattern_id")]
                ex_trades = [{"trade_id": t.get("id"), "symbol": t.get("symbol"), "side": t.get("side"), "opened_at": t.get("opened_at"),
                              "structure": {k: (t["features"]["structure"] or {}).get(k) for k in ("pattern_id", "name", "timeframe", "status", "action")}}
                             for t in with_struct[:5]]
                ex_exits = [{"trade_id": t.get("id"), "symbol": t.get("symbol"), "closed_at": t.get("closed_at"), "exit_reason": t.get("exit_reason"),
                             "exit_structure": {k: (t["features"]["exit_structure"] or {}).get(k) for k in ("pattern_id", "name", "timeframe", "reason_code")}}
                            for t in struct_exit[:5]]
                ex_waits = [x for x in log if x["action"] in ("WAIT", "CANCEL", "WAIT_TRIGGER")][:8]
                res_all["%s|%s" % (name, mode)] = {
                    "rule": name, "mode": mode, "symbols_loaded": len(res.loaded_symbols), "symbols_skipped": dict(res.skipped_symbols),
                    "filters_source": rp.filters_source, "filters_verified_at": getattr(rp.filters, "verified_at", None),
                    "elapsed_s": round(time.time() - t0, 1), "n_decisions": res.n_decisions, "n_opened": res.n_opened,
                    "n_closed_trades": len(trades), "determinism_hash": res.determinism_hash,
                    "structure_decisions_by_action": dict(acts), "structure_decisions_by_reason": dict(reasons.most_common(20)),
                    "structure_decision_to_applied_action": dict(applied.most_common(20)),
                    "closed_trades_with_entry_structure": len(with_struct), "closed_trades_exited_by_structure": len(struct_exit),
                    "open_positions_at_end": len(rp.ledger2.positions), "open_positions_with_entry_structure": len(open_struct),
                    "exit_reasons": dict(Counter(str(t.get("exit_reason")) for t in trades).most_common()),
                    "hold_hours": _hold_hours(trades),
                    "example_trades": ex_trades, "example_structure_exits": ex_exits,
                    "example_wait_or_cancel_decisions": ex_waits,
                    "rejections_top": _flatten_counts(res.rejections)}
                print("  %s %s: opened=%s closed=%s decisions=%s (%.0fs)" % (name, mode, res.n_opened, len(trades), dict(acts), time.time() - t0),
                      flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res_all


def main() -> int:
    t0 = time.time()
    syms = _universe()
    print("evren:", len(syms), "coin (config.yaml entry_universe)", flush=True)
    doc = {"label": "GERÇEK ARŞİV (bn_archive yerel kopya, Binance USDⓈ-M perpetual) — salt okuma; PnL raporlanmaz",
           "archive_root": str(ARCH), "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    doc["A_analysis_integrity"] = part_a(syms)
    print("A tamam (%.0fs)" % (time.time() - t0), flush=True)
    doc["B_decision_effect"] = part_b(syms)
    doc["elapsed_s"] = round(time.time() - t0, 1)
    (OUT / "real_archive_structures.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("yazıldı:", OUT / "real_archive_structures.json", "(%.0fs)" % doc["elapsed_s"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

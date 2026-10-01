"""Bounded public-data smoke — TAM `HistoricalReplay` zinciri (Quant Evaluation V1).

AYRI KOMUTTUR: normal test suite'in parçası DEĞİLDİR (suite ağsız çalışır). Bu betik gerçek
public Binance verisiyle şu zinciri uçtan uca doğrular:

    public downloader/parser
    → DataQualityGate
    → HistoryStore (üretim şeması)
    → HistoricalReplay (üretim CoinHead/Chief/RiskEngine karar yolu)
    → FuturesLedgerV2 (fee/funding/slippage)
    → TradeMemory
    → quant.run (journal → coverage → attribution → senaryolar → kanıt köprüsü)
    → quant raporu + manifest

Sınırlar (kasıtlı):
* TEK doğrulanmış futures sembolü (varsayılan BTCUSDT) + küçük tarih aralığı
* API anahtarı YOK, yalnız public endpoint, toplam birkaç istek
* Bütün çıktı geçici/ignored çalışma dizinine yazılır; repo, vault ve canlı state'e DOKUNULMAZ
* Kârlılık ölçümü DEĞİLDİR — yalnız zincir ve determinizm kanıtıdır

Kullanım:
    python scripts/quant_public_smoke.py --workdir <gecici_dizin> [--symbol BTCUSDT] [--bars 1000]

Çıkış kodları: 0 OK · 2 ağ/veri erişilemedi (BLOCKED) · 3 veri kalitesi INVALID · 4 determinizm bozuk
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:                                    # Windows konsolu cp1254 olabilir; çıktı UTF-8'e sabitlenir
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):       # pragma: no cover
    pass

import pandas as pd  # noqa: E402

from tradingbot.core import read_json  # noqa: E402
from tradingbot.history.store import HistoryStore  # noqa: E402
from tradingbot.market.quality import DataQualityGate  # noqa: E402
from tradingbot.quant.eligibility import build_artifact, from_exchange_info, write_artifact  # noqa: E402
from tradingbot.quant.run import main as quant_main  # noqa: E402
from tradingbot.replay.engine import HistoricalReplay, walk_forward_windows  # noqa: E402

FAPI = "https://fapi.binance.com"
TF_MS = {"4h": 14_400_000, "1d": 86_400_000}


def fetch_json(url: str, dest: Path | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "quant-eval-smoke/2.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
    if dest is not None:
        dest.write_bytes(raw)
    return json.loads(raw)


def klines_df(rows: list) -> pd.DataFrame:
    ts = [int(r[0]) for r in rows]
    return pd.DataFrame({
        "timestamp": ts,
        "open": [float(r[1]) for r in rows], "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows], "close": [float(r[4]) for r in rows],
        "volume": [float(r[5]) for r in rows], "close_time": [int(r[6]) for r in rows],
        "quote_volume": [float(r[7]) for r in rows], "trades": [int(r[8]) for r in rows],
        "taker_buy_base": [float(r[9]) for r in rows],
        "taker_buy_quote": [float(r[10]) for r in rows]})


def build_cfg(work: Path, *, equity_usdt: float):
    """TEST POLICY config'i — üretim `config.yaml` DEĞİŞTİRİLMEZ, yalnız bellekte kurulur.

    `equity_usdt`: üretim varsayılanı 50 USDT'dir; BTC gibi ~100k fiyatlı bir sembolde bu miktar
    borsa `stepSize` filtresine takılıp `STEP_ZERO_QTY` verir (ledger minimumları GEVŞETMEZ ve
    sahte fill üretmez — doğru davranış). Smoke'un maliyet/attribution halkasını da kanıtlaması
    için sermaye açıkça yükseltilir; bu bir TEST varsayımıdır, kârlılık iddiası değildir.
    """
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    cfg = BotConfig()
    cfg.project_root = work
    cfg.scanner.enabled = False
    cfg.futures.starting_equity_usdt = float(equity_usdt)
    cfg.risk.starting_equity_usdt = float(equity_usdt)
    # Eşikler düşürülür ki sınırlı veride üretim karar yolu gerçekten aday üretsin.
    cfg.v3 = load_v3({"coin_heads": {"consensus_threshold": 0.05, "min_confidence": 0.05}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    return cfg


def comparable(result) -> dict:
    """Wall-clock/telemetri ve dizin yolu HARİÇ karşılaştırma görünümü."""
    d = result.to_dict()
    return {"trades": d["trades"],
            "metrics": {k: v for k, v in d["metrics"].items() if k not in ("learner", "ledger")},
            "n_decisions": d["n_decisions"], "n_actionable": d["n_actionable"],
            "n_opened": d["n_opened"], "windows": d["windows"],
            "determinism_hash": d["determinism_hash"], "rejections": d["rejections"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bounded public-data HistoricalReplay smoke")
    ap.add_argument("--workdir", default=None, help="geçici çalışma dizini (varsayılan: mkdtemp)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--bars", type=int, default=1000, help="4h bar sayısı (tek istek sınırı içinde)")
    ap.add_argument("--equity", type=float, default=200_000.0,
                    help="TEST POLICY başlangıç sermayesi (üretim varsayılanı 50 USDT; yüksek "
                         "fiyatlı sembolde stepSize filtresi nedeniyle yükseltilir)")
    args = ap.parse_args(argv)

    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="quant_smoke_"))
    work.mkdir(parents=True, exist_ok=True)
    raw_dir = work / "raw"
    raw_dir.mkdir(exist_ok=True)
    pair = f"{args.symbol[:-4]}/{args.symbol[-4:]}" if args.symbol.endswith("USDT") else args.symbol
    print(f"workdir: {work}")

    # ---------------------------------------------------------------- 1) indirme (birkaç istek)
    try:
        k4 = fetch_json(f"{FAPI}/fapi/v1/klines?symbol={args.symbol}&interval=4h&limit={args.bars}",
                        raw_dir / "klines_4h.json")
        k1d = fetch_json(f"{FAPI}/fapi/v1/klines?symbol={args.symbol}&interval=1d&limit=400",
                         raw_dir / "klines_1d.json")
        funding = fetch_json(f"{FAPI}/fapi/v1/fundingRate?symbol={args.symbol}&limit=1000",
                             raw_dir / "funding.json")
        exinfo = fetch_json(f"{FAPI}/fapi/v1/exchangeInfo", raw_dir / "exchange_info.json")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as ex:
        print(f"SMOKE_BLOCKED: public-data erişimi başarısız: {type(ex).__name__}: {ex}")
        return 2
    df4, df1d = klines_df(k4), klines_df(k1d)
    print(f"downloaded: 4h={len(df4)} 1d={len(df1d)} funding={len(funding)} "
          f"exchangeInfo_symbols={len(exinfo.get('symbols', []))}")

    # ---------------------------------------------------------------- 2) veri kalitesi kapısı
    gate = DataQualityGate()
    rep4 = gate.check_klines(df4, "4h", int(df4["timestamp"].iloc[-1]) + TF_MS["4h"])
    rep1d = gate.check_klines(df1d, "1d", int(df1d["timestamp"].iloc[-1]) + TF_MS["1d"])
    print(f"quality: 4h={rep4.verdict}{rep4.codes} 1d={rep1d.verdict}{rep1d.codes}")
    if "DATA_INVALID" in (rep4.verdict, rep1d.verdict):
        print("SMOKE_BLOCKED: veri kalitesi INVALID — geçerli backtest sayılmaz")
        return 3

    # ---------------------------------------------------------------- 3) eligibility artifact
    as_of = int(df4["timestamp"].iloc[0])
    snaps = [s for s in from_exchange_info(exinfo, as_of_ms=as_of, source="binance_fapi_exchangeInfo",
                                           source_timestamp_ms=int(exinfo.get("serverTime") or as_of))
             if s.symbol == pair]
    elig_path = write_artifact(work / "eligibility.json",
                               build_artifact(snaps, as_of_ms=as_of,
                                              source="binance_fapi_exchangeInfo",
                                              source_timestamp_ms=int(exinfo.get("serverTime") or as_of)))
    print(f"eligibility: {len(snaps)} snapshot → {elig_path.name} "
          f"(NOT: bugünün metadata'sı, as_of'tan İTİBAREN geçerli)")

    # ---------------------------------------------------------------- 4) HistoryStore (üretim şeması)
    store = HistoryStore(work / "hist")
    store.write("futures", pair, "4h", df4, source="binance_public")
    store.write("futures", pair, "1d", df1d, source="binance_public")

    # ---------------------------------------------------------------- 5) TAM HistoricalReplay ×2
    cfg = build_cfg(work, equity_usdt=args.equity)
    start_ms, end_ms = int(df4["timestamp"].iloc[0]), int(df4["timestamp"].iloc[-1])
    windows = walk_forward_windows(start_ms, end_ms, train_days=45, test_days=15, tf="4h")

    def run(run_id: str):
        rp = HistoricalReplay(cfg, run_id=run_id, store=store, symbols=[pair], market="futures",
                              tf="4h", seed=7, decision_stride=1, min_bars=250)
        return rp.run(windows=windows)

    try:
        r1 = run("public_smoke_a")
        r2 = run("public_smoke_b")
    except ValueError as ex:
        print(f"SMOKE_BLOCKED: replay çalıştırılamadı: {ex}")
        return 2
    print(f"replay: decisions={r1.n_decisions} actionable={r1.n_actionable} "
          f"opened={r1.n_opened} closed={len(r1.trades)} folds={len(r1.windows)}")
    if r1.rejections.get("total"):
        print(f"replay rejections: {r1.rejections['by_reason']}")
    if r1.n_opened == 0:
        print("UYARI: hiç pozisyon açılmadı — zincir çalıştı fakat maliyet/attribution halkası "
              "boş kaldı (yukarıdaki red nedenlerine bakın; --equity ayarı gerekebilir).")
    same_hash = r1.determinism_hash == r2.determinism_hash
    same_all = comparable(r1) == comparable(r2)
    print(f"DETERMINISM: hash1={r1.determinism_hash[:16]} hash2={r2.determinism_hash[:16]} "
          f"equal={same_hash} full_result_equal={same_all}")
    if not (same_hash and same_all):
        print("SMOKE_FAILED: iki tam replay aynı sonucu vermedi")
        return 4

    # ---------------------------------------------------------------- 6) quant.run (üretim biçimleri)
    shadow = work / "shadow_book.json"
    shadow.write_text(json.dumps({"trades": []}), encoding="utf-8")
    reports = []
    for tag, res in (("a", r1), ("b", r2)):
        out = work / f"quant_eval_{tag}.json"
        rc = quant_main(["--memory", str(Path(res.state_dir) / "trade_memory.jsonl"),
                         "--shadow", str(shadow), "--out", str(out),
                         "--ledger", str(Path(res.state_dir) / "futures_ledger.json"),
                         "--eligibility", str(elig_path),
                         "--run-id", "public_smoke", "--code-sha", "public_smoke",
                         "--seed", "7", "--min-sample", "1"])
        if rc != 0:
            print(f"SMOKE_FAILED: quant.run rc={rc}")
            return 4
        d = read_json(out)
        reports.append(d)
    a, b = reports

    def stripped(doc: dict) -> dict:
        """Wall-clock türevli ve çalıştırmaya özgü alanları karşılaştırmadan ÇIKARIR.

        Determinizm iddiası ölçüm sonuçları içindir; rapor üretim anına bağlı alanlar (veri yaşı,
        çalışma dizini, manifest run kimlikleri) doğal olarak değişir.
        """
        d = json.loads(json.dumps(doc))
        d.pop("manifest", None)
        for path in (("coverage",), ("evidence", "journal_coverage")):
            node = d
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, dict):
                node.pop("data_age_days", None)
                node.pop("span_days", None)
        if isinstance(d.get("risk_v2"), dict):
            d["risk_v2"].pop("data_age_ms", None)
        return d

    reports_equal = stripped(a) == stripped(b)
    print(f"quant: records={a['journal']['n_records']} labeled={a['journal']['n_labeled']} "
          f"expectancy_r={(a['overall'] or {}).get('expectancy_r')} "
          f"decision={a['champion_challenger']['decision']} backtest={a['backtest_status']}")
    sc = (a.get("execution_scenarios") or {}).get("expectancy_r_by_scenario")
    print(f"scenarios(expectancy_r): {sc}")
    print(f"manifest: hash={a['manifest']['manifest_hash'][:16]} "
          f"valid_backtest={a['manifest']['valid_backtest']}")
    print(f"ATTRIBUTION_DETERMINISM: reports_equal={reports_equal}")
    if not reports_equal:
        print("SMOKE_FAILED: aynı girdiden iki farklı quant raporu çıktı")
        return 4
    print("PUBLIC_REPLAY_SMOKE_OK (TEST DATA — kârlılık ölçümü DEĞİLDİR)")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())

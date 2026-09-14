#!/usr/bin/env bash
# =============================================================================
#  HAYATTA KALMA KONTROLU ICIN VERI (PROTOCOL_V7 Q3) — VPS'te, ubuntu olarak, AYRI dizine.
#  Bu makineden Binance'e erisim yok (fapi + data.binance.vision baglanti sifirlaniyor);
#  VPS erisebiliyor. Uretim verisine ve worker'a DOKUNMAZ: ayri cache dizini, archive-first
#  (API agirligi tuketmez), servis kullanicisi degil ubuntu.
#
#  KULLANIM:  bash ~/vps-collect-2020-universe.sh
#  CIKTI  :   ~/tb-2020-universe.tar.gz  (sonra: scp ile bilgisayara)
# =============================================================================
set -uo pipefail
APP="/opt/tradingbot/app"
VENV="/opt/tradingbot/venv"
CACHE="$HOME/research-data"                 # AYRI dizin; uretim /opt/tradingbot/data'ya dokunulmaz
STATE="$CACHE/state"
mkdir -p "$CACHE" "$STATE"
say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! ABORTED: %s\n' "$*"; exit 1; }
[ -x "$VENV/bin/python" ] || fail "venv yok"
cd "$APP" || fail "cd $APP"
PY="env HOME=$HOME TRADINGBOT_CACHE_DIR=$CACHE TRADINGBOT_STATE_DIR=$STATE $VENV/bin/python"

CANDS="BTC/USDT ETH/USDT LINK/USDT BNB/USDT LTC/USDT XRP/USDT BCH/USDT TRX/USDT ETC/USDT XLM/USDT ADA/USDT DOT/USDT ATOM/USDT XMR/USDT DASH/USDT ZEC/USDT XTZ/USDT NEO/USDT VET/USDT THETA/USDT UNI/USDT YFI/USDT SUSHI/USDT ALGO/USDT IOTA/USDT BAT/USDT ONT/USDT QTUM/USDT FIL/USDT KSM/USDT EGLD/USDT ZIL/USDT COMP/USDT SNX/USDT CRV/USDT KAVA/USDT BAND/USDT RUNE/USDT NEAR/USDT"

say "1) Ekim-2020 siralamasi icin gunluk barlar (2019-09 -> 2020-12), archive-first"
$PY -m tradingbot history-collect --market futures --timeframes 1d --from 2019-09-01 --to 2020-12-31 --symbols $CANDS 2>&1 | tail -12

say "2) Ekim-2020 hacmine gore ilk 10"
TOP="$($PY - <<'PYR'
import os, json, sys, datetime as dt
sys.path.insert(0, "/opt/tradingbot/app")
from tradingbot.history import HistoryStore
cache = os.environ["TRADINGBOT_CACHE_DIR"]
store = HistoryStore(os.path.join(cache, "history"))
root = os.path.join(cache, "history", "futures")
t0 = int(dt.datetime(2020,10,1,tzinfo=dt.timezone.utc).timestamp()*1000); t1 = int(dt.datetime(2020,10,31,23,tzinfo=dt.timezone.utc).timestamp()*1000)
rank = []
for safe in sorted(os.listdir(root)) if os.path.isdir(root) else []:
    sym = safe.replace("_", "/")
    try:
        df = store.read("futures", sym, "1d", t0, t1)
    except Exception as exc:  # noqa: BLE001
        print("  atlandi", sym, exc, file=sys.stderr); continue
    if df is None or len(df) < 20 or "quote_volume" not in df.columns:
        continue
    rank.append((float(df["quote_volume"].sum()), sym, int(len(df))))
rank.sort(reverse=True)
json.dump({"ranking": [{"symbol": s, "oct2020_quote_volume": q, "days": n} for q, s, n in rank]},
          open(os.path.join(cache, "ranking_oct2020.json"), "w"), indent=1)
print(" ".join(s for _, s, _ in rank[:10]))
PYR
)"
echo "   ILK 10: $TOP"
[ -n "$TOP" ] || fail "siralama bos — 1. adim veri getirmedi"

say "3) Ilk 10 icin tam seri (1h 4h 1d + funding), 2019-09 -> 2026-09-10"
$PY -m tradingbot history-collect --market futures --timeframes 1h 4h 1d --from 2019-09-01 --to 2026-09-10 --symbols $TOP 2>&1 | tail -14

say "4) Dogrulama + paket"
$PY -m tradingbot history-validate --symbols $TOP 2>&1 | tail -3
$PY - <<'PYC'
import glob, os
import pandas as pd
n = 0
for p in glob.glob(os.path.join(os.environ["TRADINGBOT_CACHE_DIR"], "history", "futures", "*", "*", "*", "*.parquet")):
    out = p[:-len(".parquet")] + ".csv.gz"
    if not os.path.exists(out):
        pd.read_parquet(p).to_csv(out, index=False, compression="gzip"); n += 1
print("   parquet -> csv.gz:", n, "dosya (bilgisayarda pyarrow yok)")
PYC
cd "$CACHE" && tar --exclude='*.parquet' -czf "$HOME/tb-2020-universe.tar.gz" ranking_oct2020.json history/futures && ls -la "$HOME/tb-2020-universe.tar.gz" && sha256sum "$HOME/tb-2020-universe.tar.gz"
echo
echo "COLLECT_OK  ->  bilgisayarda: scp -i ~/.ssh/trading2_ovh ubuntu@<VPS_HOST>:~/tb-2020-universe.tar.gz C:/Users/berke/trading2-deploy/"

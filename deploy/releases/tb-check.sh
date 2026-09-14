#!/usr/bin/env bash
# Canli kontrol: rejim kapisi / mum vetosu / grafik golge + huni sayaclari + son kararlar. Salt okunur.
set -uo pipefail
PY=/opt/tradingbot/venv/bin/python
echo "== journal (baslangic satirlari + hata)"
journalctl -u tradingbot-worker -n 4000 --no-pager | grep -E "REJIM KAPISI|MUM ONAYI|GRAFIK ONAYI" | tail -3
echo "-- son 24 saatte hata satirlari (bos = temiz):"
journalctl -u tradingbot-worker --since "-24h" --no-pager | grep -E "Traceback|ERROR|CRITICAL" | tail -6
echo
echo "== huni (bu tur / kayan 24 saat)"
$PY - <<'PYEOF'
import json, io
d = json.load(io.open("/opt/tradingbot/data/state/decision_funnel.json", encoding="utf-8"))
keys = ("actionable", "ranked", "no_trigger", "trigger_fired", "candle_blocked", "chart_blocked", "regime_blocked",
        "negative_edge_blocked", "risk_capacity_blocked", "opened")
for name in ("run", "rolling_24h", "rolling"):
    r = d.get(name)
    if isinstance(r, dict):
        print("  %-12s" % name, {k: r.get(k) for k in keys if k in r})
PYEOF
echo
echo "== son kararlar (sembol, engel, rejim, mum hukmu)"
$PY - <<'PYEOF'
import json, io
d = json.load(io.open("/opt/tradingbot/data/state/risk.json", encoding="utf-8"))
rows = d.get("last_decisions", [])
print("  kayit:", len(rows))
for e in rows[-14:]:
    rg = e.get("regime_gate") or {}
    cc = e.get("candle_confirmation") or {}
    print("  %-10s %-34s rejim=%-5s mum=%s" % (e.get("symbol"), (e.get("block_code") or "ACILDI/GECTI")[:34],
                                              rg.get("regime"), (cc.get("verdict") or {}).get("reason") or ("ok" if cc else "-")))
PYEOF
echo
echo "== defter"
$PY - <<'PYEOF'
import json, io
d = json.load(io.open("/opt/tradingbot/data/state/futures_ledger.json", encoding="utf-8"))
print("  acik:", {s: p.get("side") for s, p in d.get("positions", {}).items()}, "| kapanis:", len(d.get("history", [])))
PYEOF
echo
echo "== strateji kagit defterleri (V10/V12)"
journalctl -u tradingbot-worker -n 800 --no-pager | grep -E "STRATEJI KAGIT DEFTERI|strateji kagit" | tail -4
$PY - <<'PYEOF'
import json, io, os, sys
sys.path.insert(0, "/opt/tradingbot/app")
try:
    from tradingbot.config import load_config
    _eu = load_config("/opt/tradingbot/app/config.yaml").v3.entry_universe
    UNI = set(_eu.symbols) if _eu.enabled else set()
except Exception as _exc:  # noqa: BLE001
    UNI = set(); print("  (evren okunamadi: %s)" % _exc)
st = "/opt/tradingbot/data/state/"
idx = st + "strategy_paper_index.json"
files = []
if os.path.exists(idx):
    files = [b.get("summary_file") for b in (json.load(io.open(idx, encoding="utf-8")).get("books") or []) if b.get("summary_file")]
if not files:
    files = [f for f in ("strategy_paper.json", "strategy_paper_m2.json") if os.path.exists(st + f)]
if not files:
    print("  ozet dosyasi henuz yok (ilk tur bekleniyor)")
for f in files:
    d = json.load(io.open(st + f, encoding="utf-8")); sm = d.get("summary") or {}
    print("  [%s] kural=%s rejim=%s ozkaynak=%s baslangic=%s" % (f, d.get("name"), d.get("regime"), sm.get("equity_mtm"), d.get("starting_equity")))
    print("      sayaclar=%s retler=%s" % (d.get("counters"), d.get("rejections")))
    for s_, p_ in (d.get("positions") or {}).items():
        tag = "" if (not UNI or s_ in UNI) else "  <- EVREN DISI (yalniz yonetilir)"
        print("      acik: %-10s %s giris=%s stop=%s son=%s%s" % (s_, p_.get("side"), p_.get("entry"), p_.get("stop"), p_.get("last_price"), tag))
    for s_, a in list((d.get("last_actions") or {}).items())[:10]:
        print("      karar: %-10s %s %s" % (s_, a.get("action"), a.get("reason")))
PYEOF

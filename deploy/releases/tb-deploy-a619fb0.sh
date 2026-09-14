#!/usr/bin/env bash
# =============================================================================
#  TRADING BOT — TREND STRATEJISI KAGIT ILERI TEST (V10) + replay strateji modu
#  Taban : 0796c914a1a5d8f02308755418518194bb84a778 (VPS'te 2026-09-13 11:03Z dogrulandi); baskasinda betik DURUR
#  Hedef : a619fb07e62b500101f9c9a16d8bde40aa7843a9
#  Bundle: tb-a619fb0.bundle   sha256 2166b01b15339f581b325b30fa678b1db0874517f17c7e9996e0c46cb316bd64
#
#  Ne degisiyor:
#   * strategy_paper.enabled = true: tek kuralli EMA200 trend (T2: BTC 1d close > EMA200 iken, 1d close
#     > EMA200 olan coinde LONG; close < EMA200 -> kapat; stop close - 3xATR14(1d); kaldirac 1) AYRI 100 USDT
#     sanal defterde, ana botun YANINDA calisir. Ana botun defteri/ogrenicisi/kararlari DEGISMEZ.
#   * Dashboard: ana sayfada "Trend stratejisi - kagit ileri test" kartlari; /portfolio/strategy.
#  Olcum: DENEY_V9 (T2 +107% / +34% / +10%, maksDD %22). Gercek para YOK; LIVE'da config reddedilir.
#
#  ROOT ile calisir; depo islemleri servis kullanicisi adina. State'e DOKUNMAZ (yeni dizin acilir).
#  KULLANIM (VPS'te):  sudo bash ~/tb-deploy-a619fb0.sh
# =============================================================================
set -uo pipefail

BASE_SHA="0796c914a1a5d8f02308755418518194bb84a778"
TARGET_SHA="a619fb07e62b500101f9c9a16d8bde40aa7843a9"
BUNDLE_SHA="2166b01b15339f581b325b30fa678b1db0874517f17c7e9996e0c46cb316bd64"
BUNDLE_NAME="tb-a619fb0.bundle"

BASE="/opt/tradingbot"
APP="$BASE/app"
VENV="$BASE/venv"
STATE="$BASE/data/state"
SVC_USER="tradingbot"

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! DEPLOY_ABORTED: %s\n' "$*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "root gerekli: sudo bash ~/tb-deploy-a619fb0.sh"
RUN="$BASE/rollback/deploy-${TARGET_SHA}-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN" || fail "rollback dizini olusturulamadi: $RUN"
LOG="$RUN/deploy.log"
exec > >(tee -a "$LOG") 2>&1

git_svc() { sudo -u "$SVC_USER" git -C "$APP" "$@"; }
py_svc()  { sudo -u "$SVC_USER" env HOME="$BASE" "$VENV/bin/python" "$@"; }

say "0) ORTAM"
command -v git >/dev/null || fail "git yok"
id -u "$SVC_USER" >/dev/null 2>&1 || fail "servis kullanicisi yok: $SVC_USER"
[ -d "$APP/.git" ] || fail "$APP bir git deposu degil"
[ -x "$VENV/bin/python" ] || fail "venv python yok"
[ "$(stat -c '%U' "$APP")" = "$SVC_USER" ] || fail "$APP sahibi $(stat -c '%U' "$APP"), beklenen $SVC_USER"
sudo -u "$SVC_USER" test -w "$STATE" || fail "$STATE servis kullanicisi tarafindan yazilabilir degil"
echo "   root OK, depo sahibi $SVC_USER, state yazilabilir"

say "1) KAYNAK — bundle BUTUNLUK"
SRC_HOME="$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)"
SRC="$SRC_HOME/$BUNDLE_NAME"
[ -f "$SRC" ] || SRC="/home/ubuntu/$BUNDLE_NAME"
[ -f "$SRC" ] || fail "bundle yok: $SRC_HOME/$BUNDLE_NAME (scp ile kopyalayin)"
GOT="$(sha256sum "$SRC" | awk '{print $1}')"
[ "$GOT" = "$BUNDLE_SHA" ] || fail "bundle sha256 uyusmuyor: beklenen $BUNDLE_SHA, bulunan $GOT"
BUNDLE="$BASE/$BUNDLE_NAME"
install -o "$SVC_USER" -g "$SVC_USER" -m 0644 "$SRC" "$BUNDLE" || fail "bundle kopyalanamadi"
echo "   bundle sha256 OK -> $BUNDLE"

say "2) MEVCUT DURUM"
CUR="$(git_svc rev-parse HEAD)" || fail "HEAD okunamadi"
CUR_SHORT="${CUR:0:7}"
echo "   HEAD=$CUR_SHORT  dal=$(git_svc rev-parse --abbrev-ref HEAD)"
[ -z "$(git_svc status --porcelain)" ] || fail "calisma agaci KIRLI: $(git_svc status --porcelain | head -3 | tr '\n' ' ')"
case "$CUR" in
  "$TARGET_SHA"*) echo "   ZATEN DAGITILMIS."; echo "DEPLOY_ALREADY_DONE"; exit 0 ;;
  "$BASE_SHA"*)   echo "   taban dogru (${BASE_SHA:0:7})" ;;
  *) fail "beklenmeyen HEAD ($CUR_SHORT). Beklenen taban ${BASE_SHA:0:7}. Hicbir seye dokunulmadi. Bu HEAD'i bildirin." ;;
esac
LEDGER="$STATE/futures_ledger.json"
[ -r "$LEDGER" ] || fail "defter okunamadi: $LEDGER"
BEFORE_LEDGER="$("$VENV/bin/python" - "$LEDGER" <<'PY'
import json,io,sys,hashlib
raw=io.open(sys.argv[1],encoding='utf-8').read(); d=json.loads(raw)
print(json.dumps({"open":len(d.get("positions",{})),"history":len(d.get("history",[])),"seq":d.get("seq"),
                  "sha256":hashlib.sha256(raw.encode()).hexdigest()[:16]},sort_keys=True))
PY
)"
echo "   ana defter (once): $BEFORE_LEDGER"
[ -e "$STATE/strategy_paper" ] && echo "   NOT: $STATE/strategy_paper zaten var (onceki deneme?) — dokunulmaz" || echo "   strateji defteri dizini yok; ilk turda 100 USDT ile acilacak"

say "3) DOGRULANMIS YEDEK"
( cd "$APP" && sudo -u "$SVC_USER" env HOME="$BASE" bash "$APP/deploy/backup.sh" manual ) \
  || fail "yedek alinamadi — hicbir seye dokunulmadi"
echo "$CUR" > "$BASE/.last_good_commit"
git_svc tag -f "backup/vps-pre-strategy-${CUR_SHORT}" "$CUR" >/dev/null 2>&1 || true
echo "   .last_good_commit = $CUR_SHORT"

say "4) FETCH + FF-ONLY"
git_svc bundle verify "$BUNDLE" >/dev/null || fail "git bundle verify basarisiz"
git_svc fetch "$BUNDLE" '+refs/heads/*:refs/remotes/bundle/*' || fail "bundle fetch basarisiz"
git_svc cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || fail "hedef commit getirilemedi"
git_svc merge-base --is-ancestor "$CUR" "$TARGET_SHA" || fail "ff-only degil"
git_svc merge --ff-only "$TARGET_SHA" || fail "ff-only merge basarisiz"
NEW="$(git_svc rev-parse HEAD)"
[ "$NEW" = "$TARGET_SHA" ] || fail "HEAD hedefe esit degil: $NEW"
echo "   HEAD = ${NEW:0:7}"

say "5) DEGISMEZLER (kural tek kaynak + kagit defter + config)"
py_svc - <<'PY' || { echo "!! degismezler DUSTU -> sudo -u tradingbot git -C $APP checkout -q $CUR_SHORT"; exit 90; }
import inspect, sys
sys.path.insert(0, "/opt/tradingbot/app")
ok = True
def chk(c, m):
    global ok
    print(("   OK  " if c else "   FAIL") + " " + m); ok = ok and bool(c)
from tradingbot.ema200_trend import decide, VARIANTS
from tradingbot.strategy_paper import apply_action, validate_settings, StrategyBook
D = 24*3600*1000
rows = [{"timestamp": i*D, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ema200": 95.0, "atr14": 2.0} for i in range(260)]
a = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=[{"timestamp": 0, "close": 100.0, "ema200": 90.0}], position_open=False)
chk(a and a["action"] == "OPEN" and a["direction"] == "LONG" and abs(a["stop"] - 94.0) < 1e-9 and a["leverage"] == 1, "kural: UP + close>EMA200 -> LONG, stop close-3*ATR")
chk(decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=[{"timestamp": 0, "close": 100.0, "ema200": 110.0}], position_open=False) is None, "kural: BTC DOWN -> giris yok")
rows2 = [dict(r, ema200=105.0) for r in rows]
chk(decide("t2_trend_regime", daily_rows=rows2, btc_daily_rows=None, position_open=True) == {"action": "CLOSE", "reason": "EMA200_CROSS_DOWN", "name": "t2_trend_regime"}, "kural: close<EMA200 -> kapat")
chk(decide("t2_trend_regime", daily_rows=rows[:100], btc_daily_rows=None, position_open=False) is None, "kural: az veri -> fail-closed")
try:
    validate_settings(enabled=True, name="t2_trend_regime", app_mode="LIVE", starting_equity=100, atr_mult=3.0); chk(False, "LIVE'da enabled reddedilmeli")
except ValueError:
    chk(True, "LIVE'da enabled reddedilir (fail-closed)")
from tradingbot import engine_v3
src = inspect.getsource(engine_v3)
chk("_strategy_paper_tour(" in src and "_strategy_paper_exit_check(" in src and "StrategyBook(" in src, "canli motor kancalari mevcut")
chk("_regime_gate(" in src and "_candle_confirmation(" in src, "rejim + mum kancalari korunuyor")
from tradingbot.replay import engine as rep
chk("apply_action(" in inspect.getsource(rep), "replay ayni uygulayiciyi cagiriyor")
from tradingbot.learn.memory import SOURCES
chk("STRATEGY_PAPER" in SOURCES, "trade memory kaynagi kayitli")
from tradingbot.dashboard.state import STATE_FILES
chk(STATE_FILES.get("strategy_paper") == "strategy_paper.json", "dashboard ozet dosyasi kayitli")
from tradingbot.config import load_config
c = load_config("/opt/tradingbot/app/config.yaml"); sp = c.v3.strategy_paper; e = c.v3.entry_selectivity
chk(sp.enabled and sp.name == "t2_trend_regime" and float(sp.starting_equity_usdt) == 100.0 and float(sp.atr_mult) == 3.0 and float(sp.breakeven_at_mfe_r) == 0.0, "config: strategy_paper t2, 100 USDT, ATR 3, basa-bas KAPALI")
chk(e.regime_gate_mode == "ENFORCE" and e.candle_confirmation_mode == "ENFORCE" and e.chart_confirmation_mode == "SHADOW", "config: ana bot kapilari degismedi")
chk(str(c.v3.mode.mode).upper() == "PAPER", "config: mode PAPER")
sys.exit(0 if ok else 1)
PY

say "6) CONFIG SOZLESMESI - PAPER degismedi"
py_svc - <<'PYCFG' || { echo "!! config sozlesmesi DUSTU -> sudo -u tradingbot git -C $APP checkout -q $CUR_SHORT"; exit 91; }
import io, sys, yaml
d = yaml.safe_load(io.open("/opt/tradingbot/app/config.yaml", encoding="utf-8")) or {}
ok = True
def chk(c, m):
    global ok
    print(("   OK  " if c else "   FAIL") + " " + m); ok = ok and bool(c)
for sec, key in (("mode","mode"),("mode","live_trading"),("execution","gateway"),("execution","testnet_enabled"),
                 ("risk","risk_per_trade_pct"),("risk","starting_equity_usdt"),("leverage","max_leverage"),
                 ("leverage","paper_only"),("risk_profiles","profile"),("entry_selectivity","regime_gate_mode"),
                 ("strategy_paper","enabled"),("strategy_paper","name"),("strategy_paper","starting_equity_usdt")):
    chk(isinstance(d.get(sec), dict) and key in d[sec], "anahtar var: %s.%s" % (sec, key))
if not ok: sys.exit(1)
chk(str(d["mode"]["mode"]).upper() == "PAPER", "mode = PAPER")
chk(d["mode"]["live_trading"] is False, "live_trading = False")
chk(str(d["execution"]["gateway"]).lower() == "paper", "gateway = paper")
chk(d["execution"]["testnet_enabled"] is False, "testnet_enabled = False")
chk(float(d["risk"]["risk_per_trade_pct"]) <= 2.0, "risk_per_trade_pct <= 2.0")
chk(float(d["risk"]["starting_equity_usdt"]) <= 100.0, "starting_equity_usdt <= 100")
chk(int(d["leverage"]["max_leverage"]) <= 5, "max_leverage <= 5")
chk(d["leverage"]["paper_only"] is True, "leverage.paper_only = True")
chk(str(d["risk_profiles"]["profile"]) == "PAPER_RESEARCH", "profil = PAPER_RESEARCH")
chk(d["strategy_paper"]["enabled"] is True and d["strategy_paper"]["name"] == "t2_trend_regime", "strategy_paper: enabled, t2_trend_regime")
chk(float(d["strategy_paper"]["starting_equity_usdt"]) <= 100.0, "strategy_paper.starting_equity_usdt <= 100 (sanal)")
sys.exit(0 if ok else 1)
PYCFG

say "7) BAGIMLILIKLAR + DOCTOR"
if git_svc diff --quiet "$CUR" "$NEW" -- requirements.txt; then
  echo "   requirements.txt degismedi -> pip atlandi"
else
  sudo -u "$SVC_USER" env HOME="$BASE" "$VENV/bin/pip" install -q -r "$APP/requirements.txt" \
    || "$VENV/bin/pip" install -q -r "$APP/requirements.txt" || fail "pip install basarisiz"
fi
( cd "$APP" && py_svc -m tradingbot doctor --quick ) || echo "   (doktor uyari verdi — saglik kontrolu belirleyici)"

say "8) YENIDEN BASLATMA"
R0="$(systemctl show -p NRestarts --value tradingbot-worker.service)"
systemctl restart tradingbot-worker.service tradingbot-dashboard.service
code=""
for i in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health/live || true)"
  [ "$code" = "200" ] && { echo "   /health/live 200 ($i sn)"; break; }
  sleep 1
done
[ "$code" = "200" ] || fail "servis ayaga kalkmadi (/health/live=$code) -> bash $APP/deploy/rollback.sh"
systemctl is-active --quiet tradingbot-worker.service || fail "worker aktif degil -> bash $APP/deploy/rollback.sh"
echo "   NRestarts $R0 -> $(systemctl show -p NRestarts --value tradingbot-worker.service)"
code2="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/portfolio/strategy || true)"
echo "   /portfolio/strategy HTTP $code2 (ilk turdan once 'kapali ya da henuz yazilmadi' gorunmesi normal)"

say "9) DAGITIM SONRASI"
AFTER_LEDGER="$("$VENV/bin/python" - "$LEDGER" <<'PY'
import json,io,sys
d=json.loads(io.open(sys.argv[1],encoding='utf-8').read())
print(json.dumps({"open":len(d.get("positions",{})),"history":len(d.get("history",[])),"seq":d.get("seq")},sort_keys=True))
PY
)"
echo "   ana defter (sonra): $AFTER_LEDGER   (worker calistigi icin dogal degisim MESRUDUR)"
echo "   Ilk tur ~10-15 dk sonra: journal'da 'STRATEJI KAGIT DEFTERI: name=t2_trend_regime' ve $STATE/strategy_paper.json"

say "10) IZLEME (15-30 dk sonra)"
cat <<'EOS'
   sudo journalctl -u tradingbot-worker -n 800 --no-pager | grep -E "STRATEJI KAGIT|REJIM KAPISI|strateji kagit|traceback|error" | head -12
   sudo /opt/tradingbot/venv/bin/python -c "import json,io;d=json.load(io.open('/opt/tradingbot/data/state/strategy_paper.json',encoding='utf-8'));print({k:d.get(k) for k in ('regime','counters','positions','rejections')}, d.get('summary',{}).get('equity_mtm'))"
EOS

say "GERI ALMA (gerekirse)"
echo "   sudo -u $SVC_USER git -C $APP checkout -q $CUR_SHORT && sudo systemctl restart tradingbot-worker tradingbot-dashboard"
echo
echo "DEPLOY_OK $TARGET_SHA"
echo "log: $LOG"

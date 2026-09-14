#!/usr/bin/env bash
# =============================================================================
#  TRADING BOT — PIYASA REJIMI KAPISI (V7): yalniz LONG, yalniz BTC yukselisinde
#  Taban : 5fc4507cd4dee8e7c88ef8e686f419b787985dff (VPS'te 2026-09-12 20:17Z dogrulandi); baskasinda betik DURUR
#  Hedef : 0796c914a1a5d8f02308755418518194bb84a778
#  Bundle: tb-0796c91.bundle   sha256 ffc961d7e28ac22ceb0bcffd8817fb43d941c0389eb1c6af6c21561d356f7c80
#
#  Ne degisiyor:
#   * entry_selectivity.regime_gate_mode = ENFORCE, variant = r1_long_only_uptrend:
#     BTC gunluk kapanis EMA200 ustundeyse (UP) yalniz LONG acilir; DOWN'da giris yok; SHORT hic.
#   * Mum vetosu (ENFORCE) ve grafik dedektoru (SHADOW) aynen kalir.
#  Olcum (DENEY_V7): uc pencerede de temeli her olcutte gecti; 2022 sonrasi hala negatif.
#  Bu bir KAYIP AZALTICIDIR, dogrulanmis karli kural DEGIL; operator karari.
#
#  ROOT ile calisir; depo islemleri servis kullanicisi adina. State'e DOKUNMAZ.
#  KULLANIM (VPS'te):  sudo bash ~/tb-deploy-0796c91.sh
# =============================================================================
set -uo pipefail

BASE_SHA="5fc4507cd4dee8e7c88ef8e686f419b787985dff"
TARGET_SHA="0796c914a1a5d8f02308755418518194bb84a778"
BUNDLE_SHA="ffc961d7e28ac22ceb0bcffd8817fb43d941c0389eb1c6af6c21561d356f7c80"
BUNDLE_NAME="tb-0796c91.bundle"

BASE="/opt/tradingbot"
APP="$BASE/app"
VENV="$BASE/venv"
STATE="$BASE/data/state"
SVC_USER="tradingbot"

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! DEPLOY_ABORTED: %s\n' "$*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "root gerekli: sudo bash ~/tb-deploy-0796c91.sh"
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
echo "   defter (once): $BEFORE_LEDGER"
echo "   NOT: acik SHORT pozisyonlar varsa KAPATILMAZ; kapi yalniz YENI girisleri etkiler (cikis yolu dokunulmadi)."

say "3) DOGRULANMIS YEDEK"
( cd "$APP" && sudo -u "$SVC_USER" env HOME="$BASE" bash "$APP/deploy/backup.sh" manual ) \
  || fail "yedek alinamadi — hicbir seye dokunulmadi"
echo "$CUR" > "$BASE/.last_good_commit"
git_svc tag -f "backup/vps-pre-regime-${CUR_SHORT}" "$CUR" >/dev/null 2>&1 || true
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

say "5) DEGISMEZLER (rejim kapisi tek kaynak + config + BTC karesi)"
py_svc - <<'PY' || { echo "!! degismezler DUSTU -> sudo -u tradingbot git -C $APP checkout -q $CUR_SHORT"; exit 90; }
import inspect, sys
sys.path.insert(0, "/opt/tradingbot/app")
ok = True
def chk(c, m):
    global ok
    print(("   OK  " if c else "   FAIL") + " " + m); ok = ok and bool(c)
from tradingbot.regime_gate import VARIANTS, btc_regime, evaluate_variant, regime_confirmation, validate_settings
chk(validate_settings(mode="ENFORCE", variant="r1_long_only_uptrend", app_mode="PAPER") == "ENFORCE", "PAPER'da ENFORCE kabul")
try:
    validate_settings(mode="ENFORCE", variant="r1_long_only_uptrend", app_mode="LIVE"); chk(False, "LIVE'da ENFORCE reddedilmeli")
except ValueError:
    chk(True, "LIVE'da ENFORCE reddedilir (fail-closed)")
chk(btc_regime([{"close": 101.0, "ema200": 100.0}]) == "UP" and btc_regime([{"close": 99.0, "ema200": 100.0}]) == "DOWN", "rejim: close vs EMA200")
chk(evaluate_variant("r1_long_only_uptrend", direction="LONG", regime="UP") == (True, ""), "UP + LONG -> gecer")
chk(evaluate_variant("r1_long_only_uptrend", direction="LONG", regime="DOWN") == (False, "R1_NOT_UPTREND"), "DOWN + LONG -> veto")
chk(evaluate_variant("r1_long_only_uptrend", direction="SHORT", regime="UP") == (False, "R1_SHORT_BLOCKED"), "SHORT -> her zaman veto")
r = regime_confirmation(mode="ENFORCE", variant="r1_long_only_uptrend", direction="LONG", btc_daily_bars=[])
chk(r["blocks"] and r["verdict"]["reason"] == "R1_NO_REGIME", "BTC karesi yoksa fail-closed")
from tradingbot import engine_v3
src = inspect.getsource(engine_v3)
chk("_regime_gate(" in src and '"regime_blocked"' in src and "REGIME_VETO:" in src, "canli motor kancasi mevcut")
chk("_candle_confirmation(" in src and "_chart_confirmation(" in src, "mum ve grafik kancalari korunuyor")
from tradingbot.replay import engine as rep
chk("regime_evaluate_variant(" in inspect.getsource(rep), "replay ayni fonksiyonu cagiriyor")
from tradingbot.config import load_config
c = load_config("/opt/tradingbot/app/config.yaml"); e = c.v3.entry_selectivity
chk(e.regime_gate_mode == "ENFORCE" and e.regime_gate_variant == "r1_long_only_uptrend", "config: regime ENFORCE / r1")
chk(e.candle_confirmation_mode == "ENFORCE" and e.chart_confirmation_mode == "SHADOW", "config: candle ENFORCE, chart SHADOW (degismedi)")
chk(str(c.v3.mode.mode).upper() == "PAPER", "config: mode PAPER")
chk("BTC/USDT" in list(c.coins), "BTC/USDT evrende (gunluk kare her turda cekiliyor)")
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
                 ("leverage","paper_only"),("risk_profiles","profile"),
                 ("entry_selectivity","candle_confirmation_mode"),("entry_selectivity","chart_confirmation_mode"),
                 ("entry_selectivity","regime_gate_mode"),("entry_selectivity","regime_gate_variant")):
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
chk(str(d["entry_selectivity"]["regime_gate_mode"]).upper() == "ENFORCE", "regime_gate_mode = ENFORCE")
chk(d["entry_selectivity"]["regime_gate_variant"] == "r1_long_only_uptrend", "regime_gate_variant = r1_long_only_uptrend")
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

say "9) DAGITIM SONRASI"
AFTER_LEDGER="$("$VENV/bin/python" - "$LEDGER" <<'PY'
import json,io,sys
d=json.loads(io.open(sys.argv[1],encoding='utf-8').read())
print(json.dumps({"open":len(d.get("positions",{})),"history":len(d.get("history",[])),"seq":d.get("seq")},sort_keys=True))
PY
)"
echo "   defter (sonra): $AFTER_LEDGER   (worker calistigi icin dogal degisim MESRUDUR)"
echo "   Ilk tur ~10-15 dk sonra: journal'da 'REJIM KAPISI: mode=ENFORCE variant=r1_long_only_uptrend'."

say "10) IZLEME (15-30 dk sonra)"
cat <<'EOS'
   sudo journalctl -u tradingbot-worker -n 600 --no-pager | grep -E "REJIM KAPISI|MUM ONAYI|GRAFIK ONAYI|REGIME|traceback|error" | head -20
   sudo /opt/tradingbot/venv/bin/python -c "import json,io;d=json.load(io.open('/opt/tradingbot/data/state/decision_funnel.json',encoding='utf-8'));r=d.get('run',{});print({k:r.get(k) for k in ('actionable','trigger_fired','candle_blocked','chart_blocked','regime_blocked','opened')})"
   sudo /opt/tradingbot/venv/bin/python -c "import json,io;d=json.load(io.open('/opt/tradingbot/data/state/risk.json',encoding='utf-8'));print([(e.get('symbol'),e.get('block_code'),(e.get('regime_gate') or {}).get('regime')) for e in d.get('last_decisions',[]) if 'regime_gate' in e][:12])"
EOS

say "GERI ALMA (gerekirse)"
echo "   sudo -u $SVC_USER git -C $APP checkout -q $CUR_SHORT && sudo systemctl restart tradingbot-worker tradingbot-dashboard"
echo
echo "DEPLOY_OK $TARGET_SHA"
echo "log: $LOG"

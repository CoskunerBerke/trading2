# -*- coding: utf-8 -*-
"""Grafik formasyonu (SHADOW) dagitim paketi: bundle + betik. ROOT ile calisir, depo islemleri
`tradingbot` adina. Taban: VPS'te dogrulanan 0cfe3f3."""
import hashlib
import io
import subprocess
from pathlib import Path

WT = r"C:/Users/berke/wt-entry"
OUT = Path(r"C:/Users/berke/trading2-deploy")
BASE = "0cfe3f3de51d3e37681f87972d672f9fcfdfe91a"
BRANCH = "work/entry-research-v1"

target = subprocess.run(["git", "-C", WT, "rev-parse", BRANCH], capture_output=True, text=True, check=True).stdout.strip()
short = target[:7]
dirty = subprocess.run(["git", "-C", WT, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
assert not dirty, "wt-entry agaci kirli:\n" + dirty
assert target != BASE, "hedef tabanla ayni"
bundle = OUT / ("tb-%s.bundle" % short)
if not bundle.exists():
    subprocess.run(["git", "-C", WT, "bundle", "create", str(bundle), "%s..%s" % (BASE, BRANCH)], check=True)
subprocess.run(["git", "-C", WT, "bundle", "verify", str(bundle)], check=True)
bsha = hashlib.sha256(bundle.read_bytes()).hexdigest()

script = r'''#!/usr/bin/env bash
# =============================================================================
#  TRADING BOT — GRAFIK FORMASYONU DEDEKTORU (V5, SHADOW)
#  Taban : @BASE@ (VPS'te 2026-09-12 15:23Z dogrulandi); baskasinda betik DURUR
#  Hedef : @TARGET@
#  Bundle: tb-@SHORT@.bundle   sha256 @BSHA@
#
#  Ne degisiyor:
#   * Cift dip/tepe, uclu, omuz-bas-omuz, ucgen, bayrak: KIRILISLA teyitli dedektor.
#   * entry_selectivity.chart_confirmation_mode = SHADOW: her adayin karar kaydina dort
#     varyantin hukmu ve taze formasyon listesi yazilir. KARAR DEGISMEZ.
#   * Mum vetosu (C3, ENFORCE) oldugu gibi kalir.
#  Olcum: DENEY_V5 dort varyanti da reddetti; kirilis sarti olculebilir zararli. ENFORCE YOK.
#
#  ROOT ile calisir; depo islemleri servis kullanicisi adina. State'e DOKUNMAZ.
#  KULLANIM (VPS'te):  sudo bash ~/tb-deploy-@SHORT@.sh
# =============================================================================
set -uo pipefail

BASE_SHA="@BASE@"
TARGET_SHA="@TARGET@"
BUNDLE_SHA="@BSHA@"
BUNDLE_NAME="tb-@SHORT@.bundle"

BASE="/opt/tradingbot"
APP="$BASE/app"
VENV="$BASE/venv"
STATE="$BASE/data/state"
SVC_USER="tradingbot"

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! DEPLOY_ABORTED: %s\n' "$*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "root gerekli: sudo bash ~/tb-deploy-@SHORT@.sh"
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

say "3) DOGRULANMIS YEDEK"
( cd "$APP" && sudo -u "$SVC_USER" env HOME="$BASE" bash "$APP/deploy/backup.sh" manual ) \
  || fail "yedek alinamadi — hicbir seye dokunulmadi"
echo "$CUR" > "$BASE/.last_good_commit"
git_svc tag -f "backup/vps-pre-chart-${CUR_SHORT}" "$CUR" >/dev/null 2>&1 || true
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

say "5) DEGISMEZLER (grafik dedektoru + tek kaynak + config)"
py_svc - <<'PY' || { echo "!! degismezler DUSTU -> sudo -u tradingbot git -C $APP checkout -q $CUR_SHORT"; exit 90; }
import inspect, sys
sys.path.insert(0, "/opt/tradingbot/app")
ok = True
def chk(c, m):
    global ok
    print(("   OK  " if c else "   FAIL") + " " + m); ok = ok and bool(c)
from tradingbot.chart_confirmation import VARIANTS, evaluate_variant, validate_settings, chart_confirmation
from tradingbot.chart_patterns import ChartPatternConfig, detect_chart_patterns, DOUBLE_BOTTOM
chk(validate_settings(mode="SHADOW", variant="p2_4h_veto", app_mode="PAPER") == "SHADOW", "SHADOW kabul")
try:
    validate_settings(mode="ENFORCE", variant="p2_4h_veto", app_mode="LIVE"); chk(False, "LIVE'da ENFORCE reddedilmeli")
except ValueError:
    chk(True, "LIVE'da ENFORCE reddedilir (fail-closed)")
H = 14_400_000
def ramp(a, b, n): return [a + (b - a) * i / (n - 1) for i in range(n)]
closes = [100 * (1 + 0.004 * ((i % 4) - 1.5)) for i in range(30)] + ramp(112, 100, 10) + ramp(100.6, 110, 9) + ramp(109.4, 100.4, 9) + ramp(101, 109.5, 8) + [110.8, 111.5]
bars, prev = [], closes[0]
for i, c in enumerate(closes):
    bars.append({"timestamp": i * H, "open": prev, "high": max(prev, c + 0.3), "low": min(prev, c - 0.3), "close": c}); prev = c
det = detect_chart_patterns(bars, ChartPatternConfig())
chk(any(p["pattern"] == DOUBLE_BOTTOM and p["side"] == "BULL" for p in det["patterns"]), "sentetik cift dip teyitli tespit")
chk(evaluate_variant("p2_4h_veto", direction="SHORT", bars_4h=bars, bars_1d=None) == (False, "P2_OPPOSITE_PATTERN"), "SHORT adaya karsi boga kirilisi -> veto hukmu")
r = chart_confirmation(mode="SHADOW", variant="p2_4h_veto", direction="SHORT", bars_4h=bars, bars_1d=None)
chk(r["blocks"] is False and set(r["shadow"]) == set(VARIANTS) and r["fresh_4h"], "SHADOW: engel yok, 4 golge hukum, taze liste")
from tradingbot import engine_v3
src = inspect.getsource(engine_v3)
chk("_chart_confirmation(" in src and '"chart_blocked"' in src and "CHART_VETO:" in src, "canli motor kancasi mevcut")
chk("_candle_confirmation(" in src, "mum kancasi korunuyor")
from tradingbot.replay import engine as rep
chk("chart_evaluate_variant(" in inspect.getsource(rep), "replay ayni fonksiyonu cagiriyor")
from tradingbot.config import load_config
c = load_config("/opt/tradingbot/app/config.yaml"); e = c.v3.entry_selectivity
chk(e.chart_confirmation_mode == "SHADOW" and e.chart_confirmation_variant == "p2_4h_veto", "config: chart SHADOW / p2_4h_veto")
chk(e.candle_confirmation_mode == "ENFORCE" and e.candle_confirmation_variant == "c3_4h_veto", "config: candle ENFORCE / c3_4h_veto (degismedi)")
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
                 ("leverage","paper_only"),("risk_profiles","profile"),
                 ("entry_selectivity","candle_confirmation_mode"),("entry_selectivity","chart_confirmation_mode"),
                 ("entry_selectivity","chart_confirmation_variant")):
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
chk(str(d["entry_selectivity"]["candle_confirmation_mode"]).upper() == "ENFORCE", "candle_confirmation_mode = ENFORCE")
chk(str(d["entry_selectivity"]["chart_confirmation_mode"]).upper() == "SHADOW", "chart_confirmation_mode = SHADOW (ENFORCE DEGIL)")
chk(d["entry_selectivity"]["chart_confirmation_variant"] == "p2_4h_veto", "chart_confirmation_variant = p2_4h_veto")
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
echo "   Ilk tur ~10-15 dk sonra: journal'da 'MUM ONAYI: mode=ENFORCE' ve 'GRAFIK ONAYI: mode=SHADOW' satirlari."

say "10) IZLEME (15-30 dk sonra)"
cat <<'EOS'
   sudo journalctl -u tradingbot-worker -n 600 --no-pager | grep -E "MUM ONAYI|GRAFIK ONAYI|CANDLE|CHART|traceback|error" | head -20
   sudo /opt/tradingbot/venv/bin/python -c "import json,io;d=json.load(io.open('/opt/tradingbot/data/state/decision_funnel.json',encoding='utf-8'));r=d.get('run',{});print({k:r.get(k) for k in ('actionable','trigger_fired','candle_blocked','chart_blocked','opened')})"
EOS

say "GERI ALMA (gerekirse)"
echo "   sudo -u $SVC_USER git -C $APP checkout -q $CUR_SHORT && sudo systemctl restart tradingbot-worker tradingbot-dashboard"
echo
echo "DEPLOY_OK $TARGET_SHA"
echo "log: $LOG"
'''
script = (script.replace("@BASE@", BASE).replace("@TARGET@", target)
                .replace("@SHORT@", short).replace("@BSHA@", bsha))
sp = OUT / ("tb-deploy-%s.sh" % short)
io.open(sp, "w", encoding="utf-8", newline="\n").write(script)
print("bundle  ", bundle, bsha)
print("script  ", sp, hashlib.sha256(sp.read_bytes()).hexdigest())
print("target  ", target)

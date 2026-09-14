# -*- coding: utf-8 -*-
"""V12 dagitim paketi: ikinci kagit defter (m2_tsmom28) — v10 uretecinden turetilir, taban a619fb0."""
import io
import subprocess
from pathlib import Path

SRC = Path(r"C:/Users/berke/AppData/Local/Temp/claude/C--Users-berke-Trading-bot/d6d38598-6bd1-4029-b448-bb4c46b4e039/scratchpad/gen_deploy_v10.py")
SRC = Path(__file__).with_name("gen_deploy_v10.py")
src = io.open(SRC, encoding="utf-8").read()


def rep(old, new):
    global src
    assert src.count(old) == 1, old[:70]
    src = src.replace(old, new)


rep('BASE = "0796c914a1a5d8f02308755418518194bb84a778"', 'BASE = "a619fb07e62b500101f9c9a16d8bde40aa7843a9"')
rep("#  TRADING BOT — TREND STRATEJISI KAGIT ILERI TEST (V10) + replay strateji modu",
    "#  TRADING BOT — IKINCI KAGIT DEFTER: 28 GUNLUK MOMENTUM (V12), T2 ile yan yana")
rep("#  Taban : @BASE@ (VPS'te 2026-09-13 11:03Z dogrulandi); baskasinda betik DURUR",
    "#  Taban : @BASE@ (VPS'te 2026-09-13 18:33Z dogrulandi); baskasinda betik DURUR")
rep("""#  Ne degisiyor:
#   * strategy_paper.enabled = true: tek kuralli EMA200 trend (T2: BTC 1d close > EMA200 iken, 1d close
#     > EMA200 olan coinde LONG; close < EMA200 -> kapat; stop close - 3xATR14(1d); kaldirac 1) AYRI 100 USDT
#     sanal defterde, ana botun YANINDA calisir. Ana botun defteri/ogrenicisi/kararlari DEGISMEZ.
#   * Dashboard: ana sayfada "Trend stratejisi - kagit ileri test" kartlari; /portfolio/strategy.
#  Olcum: DENEY_V9 (T2 +107% / +34% / +10%, maksDD %22). Gercek para YOK; LIVE'da config reddedilir.""",
    """#  Ne degisiyor:
#   * strategy_paper.extra: IKINCI kagit defter m2_tsmom28 (1d close > 28 gun onceki close -> LONG; altina
#     inince kapat; BTC rejim kapisi ve 3xATR stop T2 ile ayni) AYRI 100 USDT sanal defterde (state/strategy_paper_m2).
#     T2 defteri ve ana bot AYNEN kalir. Dashboard: her defter icin kart + /portfolio/strategy.
#  Olcum: DENEY_V11 (M2 +158% / +46% / +35%, maksDD %16; T2 +107/+34/+10, %22). Gercek para YOK.""")
rep("""chk(sp.enabled and sp.name == "t2_trend_regime" and float(sp.starting_equity_usdt) == 100.0 and float(sp.atr_mult) == 3.0 and float(sp.breakeven_at_mfe_r) == 0.0, "config: strategy_paper t2, 100 USDT, ATR 3, basa-bas KAPALI")""",
    """chk(sp.enabled and sp.name == "t2_trend_regime" and float(sp.starting_equity_usdt) == 100.0 and float(sp.atr_mult) == 3.0 and float(sp.breakeven_at_mfe_r) == 0.0, "config: strategy_paper t2, 100 USDT, ATR 3, basa-bas KAPALI")
from tradingbot.strategy_paper import book_specs
bs = book_specs(c.v3)
chk([(b.name, b.state_dir) for b in bs] == [("t2_trend_regime", "strategy_paper"), ("m2_tsmom28", "strategy_paper_m2")], "config: iki defter (t2 + m2), ayri dizinler")
rows_m = [{"timestamp": i*D, "open": 100.0, "high": 101.0, "low": 99.0, "close": (90.0 if i == 259 - 28 else 100.0), "ema200": 105.0, "atr14": 2.0} for i in range(260)]
am = decide("m2_tsmom28", daily_rows=rows_m, btc_daily_rows=[{"timestamp": 0, "close": 100.0, "ema200": 90.0}], position_open=False)
chk(am and am["action"] == "OPEN" and am["reason"] == "M2_TSMOM28", "kural m2: close > 28 gun onceki close -> LONG (EMA'dan bagimsiz)")
chk(decide("t2_trend_regime", daily_rows=rows_m, btc_daily_rows=[{"timestamp": 0, "close": 100.0, "ema200": 90.0}], position_open=False) is None, "kural t2 degismedi (close < EMA200 -> giris yok)")
from tradingbot import strategy_paper as _spm
chk("strategy_books" in src and "_strategy_paper_tour(" in src and "INDEX_FILE" in src and _spm.INDEX_FILE == "strategy_paper_index.json", "canli motor coklu defter + indeks")""")
# ---- yeniden calistirma: kod zaten hedefteyse fetch/merge atlanir, degismezler + restart yapilir
rep("""  "$TARGET_SHA"*) echo "   ZATEN DAGITILMIS."; echo "DEPLOY_ALREADY_DONE"; exit 0 ;;""",
    """  "$TARGET_SHA"*) echo "   kod ZATEN hedefte (onceki calistirma 5. adimda durmus): fetch/merge ATLANIR, degismezler + restart yapilir"; ALREADY=1 ;;""")
rep("""say "3) DOGRULANMIS YEDEK"
( cd "$APP" && sudo -u "$SVC_USER" env HOME="$BASE" bash "$APP/deploy/backup.sh" manual ) \\
  || fail "yedek alinamadi — hicbir seye dokunulmadi"
echo "$CUR" > "$BASE/.last_good_commit"
git_svc tag -f "backup/vps-pre-strategy-${CUR_SHORT}" "$CUR" >/dev/null 2>&1 || true
echo "   .last_good_commit = $CUR_SHORT\"""",
    """ALREADY="${ALREADY:-0}"
ROLLBACK_SHA="$(cat "$BASE/.last_good_commit" 2>/dev/null || echo "$CUR")"
[ "$ALREADY" = "1" ] || ROLLBACK_SHA="$CUR"
ROLLBACK_SHORT="${ROLLBACK_SHA:0:7}"
say "3) DOGRULANMIS YEDEK"
( cd "$APP" && sudo -u "$SVC_USER" env HOME="$BASE" bash "$APP/deploy/backup.sh" manual ) \\
  || fail "yedek alinamadi — hicbir seye dokunulmadi"
if [ "$ALREADY" != "1" ]; then
  echo "$CUR" > "$BASE/.last_good_commit"
  git_svc tag -f "backup/vps-pre-m2-${CUR_SHORT}" "$CUR" >/dev/null 2>&1 || true
fi
echo "   geri alma hedefi = $ROLLBACK_SHORT\"""")
rep("""say "4) FETCH + FF-ONLY"
git_svc bundle verify "$BUNDLE" >/dev/null || fail "git bundle verify basarisiz\"""",
    """say "4) FETCH + FF-ONLY"
if [ "$ALREADY" = "1" ]; then
  NEW="$CUR"; echo "   atlandi (HEAD = ${NEW:0:7})"
else
git_svc bundle verify "$BUNDLE" >/dev/null || fail "git bundle verify basarisiz\"""")
rep("""[ "$NEW" = "$TARGET_SHA" ] || fail "HEAD hedefe esit degil: $NEW"
echo "   HEAD = ${NEW:0:7}\"""",
    """[ "$NEW" = "$TARGET_SHA" ] || fail "HEAD hedefe esit degil: $NEW"
echo "   HEAD = ${NEW:0:7}"
fi""")
rep("""py_svc - <<'PY' || { echo "!! degismezler DUSTU -> sudo -u tradingbot git -C $APP checkout -q $CUR_SHORT"; exit 90; }""",
    """py_svc - <<'PY' || { echo "!! degismezler DUSTU -> sudo -u tradingbot git -C $APP checkout -q $ROLLBACK_SHORT"; exit 90; }""")
rep("""py_svc - <<'PYCFG' || { echo "!! config sozlesmesi DUSTU -> sudo -u tradingbot git -C $APP checkout -q $CUR_SHORT"; exit 91; }""",
    """py_svc - <<'PYCFG' || { echo "!! config sozlesmesi DUSTU -> sudo -u tradingbot git -C $APP checkout -q $ROLLBACK_SHORT"; exit 91; }""")
rep("""if git_svc diff --quiet "$CUR" "$NEW" -- requirements.txt; then""",
    """if git_svc diff --quiet "$ROLLBACK_SHA" "$NEW" -- requirements.txt; then""")
rep("""echo "   sudo -u $SVC_USER git -C $APP checkout -q $CUR_SHORT && sudo systemctl restart tradingbot-worker tradingbot-dashboard\"""",
    """echo "   sudo -u $SVC_USER git -C $APP checkout -q $ROLLBACK_SHORT && sudo systemctl restart tradingbot-worker tradingbot-dashboard\"""")
rep("""chk(d["strategy_paper"]["enabled"] is True and d["strategy_paper"]["name"] == "t2_trend_regime", "strategy_paper: enabled, t2_trend_regime")""",
    """chk(d["strategy_paper"]["enabled"] is True and d["strategy_paper"]["name"] == "t2_trend_regime", "strategy_paper: enabled, t2_trend_regime")
ex = d["strategy_paper"].get("extra") or []
chk(len(ex) == 1 and ex[0].get("name") == "m2_tsmom28" and ex[0].get("state_dir") == "strategy_paper_m2" and float(ex[0].get("starting_equity_usdt", 0)) <= 100.0, "strategy_paper.extra: m2_tsmom28, strategy_paper_m2, <= 100 USDT")""")
rep("""[ -e "$STATE/strategy_paper" ] && echo "   NOT: $STATE/strategy_paper zaten var (onceki deneme?) — dokunulmaz" || echo "   strateji defteri dizini yok; ilk turda 100 USDT ile acilacak\"""",
    """[ -e "$STATE/strategy_paper" ] && echo "   T2 defteri var: $STATE/strategy_paper (dokunulmaz)" || echo "   T2 defteri dizini yok"
[ -e "$STATE/strategy_paper_m2" ] && echo "   NOT: $STATE/strategy_paper_m2 zaten var — dokunulmaz" || echo "   M2 defteri dizini yok; ilk turda 100 USDT ile acilacak\"""")
rep("""   Ilk tur ~10-15 dk sonra: journal'da 'STRATEJI KAGIT DEFTERI: name=t2_trend_regime' ve $STATE/strategy_paper.json\"""",
    """   Ilk tur ~10-15 dk sonra: journal'da 'STRATEJI KAGIT DEFTERI: name=m2_tsmom28' ve $STATE/strategy_paper_m2.json + strategy_paper_index.json\"""")
rep("""   sudo /opt/tradingbot/venv/bin/python -c "import json,io;d=json.load(io.open('/opt/tradingbot/data/state/strategy_paper.json',encoding='utf-8'));print({k:d.get(k) for k in ('regime','counters','positions','rejections')}, d.get('summary',{}).get('equity_mtm'))\"""",
    """   sudo /opt/tradingbot/venv/bin/python -c "import json,io
for f in ('strategy_paper.json','strategy_paper_m2.json'):
    d=json.load(io.open('/opt/tradingbot/data/state/'+f,encoding='utf-8')); print(f, {k:d.get(k) for k in ('name','regime','counters','positions')}, d.get('summary',{}).get('equity_mtm'))\"""")
exec(compile(src, str(SRC), "exec"))

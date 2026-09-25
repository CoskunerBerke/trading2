#!/usr/bin/env bash
# trading2 — VPS dağıtımı (PAPER; gerçek para YOK). Hedef: 4931c73 (dal claude/gifted-knuth-0ehpcs).
#
# Kod kaynağı: betikle aynı klasörde tb-4931c73.bundle varsa o (sha256 doğrulanır); yoksa herkese açık GitHub deposu
# (commit TAM SHA ile istenir ve doğrulanır — içerik commit kimliğine bağlıdır). VPS'te:
#   sudo bash tb-deploy-4931c73.sh --dry-run   # yalnız kontroller; kod/state/servis DEĞİŞMEZ
#   sudo bash tb-deploy-4931c73.sh             # dağıt
#   sudo bash tb-deploy-4931c73.sh --check     # dağıtım sonrası durum (salt okunur; tekrar tekrar koşulabilir)
#
# Sıra: kaynak → HEAD + temiz ağaç → hedef commit'in getirilmesi → HEAD hedefin atası mı → disk → [dry-run burada biter]
#       → doğrulanmış yedek → hızlı ileri sarma (ff-only) → preflight (yeni kod) → config değişmezleri → yeniden başlatma
#       → servis/sağlık → defter özeti. Yedek alınamazsa git'e ve servislere DOKUNULMAZ. Preflight ya da değişmezler
#       düşerse eski commit'e dönülür ve servisler yeniden başlatılmaz. Servis ayağa kalkmazsa eski commit'e dönülüp
#       yeniden başlatılır. State'e (defterlere) hiçbir adımda dokunulmaz.
set -Eeuo pipefail

TIP="4931c73c2978b6161180650b19f0ebab789f722c"
BUNDLE_SHA256="162db58786642c7ce33871b88126e3590a3524f26f77ca17aa5b9ce29882c9da"
BUNDLE_REF="refs/heads/claude/gifted-knuth-0ehpcs"
PREREQ="3b0ae8e"                         # bundle tabanı: VPS'te bulunması gereken son bilinen commit
REPO_URL="https://github.com/CoskunerBerke/trading2.git"   # herkese açık depo (bundle yoksa)

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"; DATA="$BASE/data"; STATE="$DATA/state"
SVC_USER="${TRADINGBOT_USER:-tradingbot}"
WORKER="tradingbot-worker.service"; DASH="tradingbot-dashboard.service"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_SRC="$HERE/tb-${TIP:0:7}.bundle"
LOGDIR="$BASE/deploy-logs"
MODE="${1:-deploy}"

say() { printf '\n== %s\n' "$*"; }
ok()  { printf '   OK  %s\n' "$*"; }
die() { printf '\nDUR: %s\n' "$*" >&2; exit 1; }
as_svc() { sudo -u "$SVC_USER" "$@"; }
gitc() { as_svc git -C "$APP" "$@"; }

# Servisin ortamı: unit'in Environment= satırları + EnvironmentFile (değerler YAZDIRILMAZ, yalnız aktarılır).
ENV_ARGS=()
load_env() {
  local e line
  for e in $(systemctl show "$WORKER" -p Environment --value 2>/dev/null); do ENV_ARGS+=("$e"); done
  if [[ -r "$BASE/env" ]]; then
    local k v
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
      k="${line%%=*}"; v="${line#*=}"
      if [[ ${#v} -ge 2 && ( ( "${v:0:1}" == '"' && "${v: -1}" == '"' ) || ( "${v:0:1}" == "'" && "${v: -1}" == "'" ) ) ]]; then
        v="${v:1:${#v}-2}"                # systemd gibi: dış tırnaklar değerin parçası değil
      fi
      ENV_ARGS+=("$k=$v")
    done < "$BASE/env"
  fi
  ENV_ARGS+=("TRADINGBOT_BASE=$BASE" "ALLOW_LIVE_TRADING=false" "MPLCONFIGDIR=$BASE/.cache-deploy-mpl")
}
py() { as_svc env -C "$APP" "${ENV_ARGS[@]}" "$VENV/bin/python" "$@"; }

book_snapshot() {   # defterlerin bakiye ve açık pozisyonları (salt okunur)
  py - "$STATE" <<'PY'
import json, sys, pathlib
st = pathlib.Path(sys.argv[1])
for p in [st / "futures_ledger.json"] + sorted(st.glob("*/futures_ledger.json")):
    if not p.exists():
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("  %-24s OKUNAMADI: %s" % (p.parent.name if p.parent != st else "ana", type(e).__name__)); continue
    pos = d.get("positions") or {}
    name = "ana" if p.parent == st else p.parent.name
    print("  %-24s bakiye=%-10s acik=%d %s" % (name, str(d.get("wallet_balance", d.get("equity")))[:10], len(pos),
                                            ",".join(sorted(pos))[:80]))
    for r in (d.get("history") or [])[-3:]:
        try:
            pnl = round(float(r.get("pnl") or 0), 3)
        except (TypeError, ValueError):
            pnl = r.get("pnl")
        print("      son kapanış: %-12s açılış %s  kapanış %s  %-22s pnl=%s" % (
            r.get("symbol"), str(r.get("opened_at"))[:16], str(r.get("closed_at"))[:16], r.get("exit_reason"), pnl))
PY
}

memory_report() {
  local cg="/sys/fs/cgroup/system.slice/$WORKER"
  _mem() { local v; v="$(cat "$cg/$1" 2>/dev/null || true)"
           if [[ "$v" =~ ^[0-9]+$ ]]; then numfmt --to=iec "$v" 2>/dev/null || echo "$v"; else echo "${v:-?}"; fi; }
  printf '   bellek (şu an / tepe / sınır): %s / %s / %s\n' "$(_mem memory.current)" "$(_mem memory.peak)" "$(_mem memory.max)"
  printf '   OOM kill sayısı (bu servis): %s\n' "$(awk '$1=="oom_kill"{print $2}' "$cg/memory.events" 2>/dev/null || echo ?)"
}

[[ $EUID -eq 0 ]] || die "sudo ile çalıştırın: sudo bash $0 ${MODE}"
[[ -d "$APP/.git" ]] || die "$APP bir git kopyası değil"
load_env

# ------------------------------------------------------------------ --check: dağıtım sonrası durum (salt okunur)
if [[ "$MODE" == "--check" ]]; then
  say "kod"
  echo "   HEAD $(gitc rev-parse --short HEAD) ($(gitc symbolic-ref --quiet --short HEAD || echo detached)) — hedef ${TIP:0:7}"
  say "servisler"
  for u in "$WORKER" "$DASH"; do
    echo "   $u: $(systemctl is-active "$u" || true), yeniden başlama sayısı $(systemctl show "$u" -p NRestarts --value)"
  done
  memory_report
  echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'yanıt yok')"
  say "defterler (şimdi)"
  book_snapshot
  if [[ -f "$LOGDIR/${TIP:0:7}-before.txt" ]]; then say "defterler (dağıtımdan hemen önce)"; cat "$LOGDIR/${TIP:0:7}-before.txt"; fi
  say "yeni defter (4h trend gözlem) ve formasyon protokolü"
  if [[ -f "$STATE/strategy_paper_trend4h.json" ]]; then
    py - "$STATE/strategy_paper_trend4h.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
s, c = d.get("summary") or {}, d.get("counters") or {}
print("   D4 (4h trend, gözlem): özkaynak=%s açılan=%s kapanan=%s tur=%s son kural turu=%s"
      % (s.get("equity"), c.get("opened"), c.get("closed"), c.get("tours"), d.get("rule_evaluated_at")))
print("   ret gerekçeleri:", dict(list((d.get("rejections") or {}).items())[:8]))
PY
  else
    echo "   D4 özeti henüz yok (ilk tur ~10-15 dk sürer)"
  fi
  py - <<'PY' || true
import yaml
print("   formasyon protokolü (config):", (yaml.safe_load(open("config.yaml", encoding="utf-8")).get("pattern_trader") or {}).get("protocol"))
PY
  say "karne (maliyet sonrası; salt okunur)"
  py scripts/bot_scorecard.py --state "$STATE" 2>&1 | tail -n 30 || echo "   karne okunamadı"
  say "son 30 dk uyarı/hata satırları (worker)"
  journalctl -u "$WORKER" --since "-30min" -p warning --no-pager 2>/dev/null | tail -n 25 || true
  exit 0
fi

[[ "$MODE" == "deploy" || "$MODE" == "--dry-run" ]] || die "bilinmeyen seçenek: $MODE (--dry-run | --check | seçeneksiz)"

# ------------------------------------------------------------------ 1) kontroller (hiçbir şeye dokunmaz)
say "1/9 kod kaynağı"
if [[ -f "$BUNDLE_SRC" ]]; then
  SOURCE="bundle"
  got="$(sha256sum "$BUNDLE_SRC" | awk '{print $1}')"
  [[ "$got" == "$BUNDLE_SHA256" ]] || die "bundle sha256 uyuşmuyor: $got (beklenen $BUNDLE_SHA256) — dosya bozuk/eksik indirilmiş"
  ok "bundle, sha256 $got"
else
  SOURCE="github"
  ok "GitHub ($REPO_URL), commit ${TIP}"
fi

say "2/9 mevcut kod"
PREV="$(gitc rev-parse HEAD)"
BRANCH_NOW="$(gitc symbolic-ref --quiet --short HEAD || echo detached)"
echo "   HEAD ${PREV:0:7} ($BRANCH_NOW)"
if [[ "$PREV" == "$TIP" ]]; then echo "   zaten hedefte (${TIP:0:7}). Durum için: sudo bash $0 --check"; exit 0; fi
dirty="$(gitc status --porcelain --untracked-files=no)"
[[ -z "$dirty" ]] || die "izlenen dosyalarda yerel değişiklik var (HİÇBİR ŞEYE DOKUNULMADI):
$dirty"
ok "izlenen dosyalar temiz"

say "3/9 hedef commit'in getirilmesi (salt ekleme; çalışma ağacı değişmez)"
if [[ "$SOURCE" == "bundle" ]]; then
  install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$BASE/deploy-bundles"
  BUNDLE="$BASE/deploy-bundles/$(basename "$BUNDLE_SRC")"
  install -o "$SVC_USER" -g "$SVC_USER" -m 0640 "$BUNDLE_SRC" "$BUNDLE"
  gitc bundle verify -q "$BUNDLE" || die "bundle bu kopyaya uygulanamıyor (gereken taban ${PREREQ} yok). HEAD ${PREV:0:7}. HİÇBİR ŞEYE DOKUNULMADI"
  gitc fetch -q "$BUNDLE" "$BUNDLE_REF:refs/deploy/${TIP:0:7}"
  [[ "$(gitc rev-parse "refs/deploy/${TIP:0:7}")" == "$TIP" ]] || die "bundle'daki dal ucu beklenen commit değil"
else
  # önce tam SHA ile (GitHub destekler); olmazsa dalı getirip commit'i onun içinde ara
  if ! as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$TIP" 2>/dev/null; then
    as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$BUNDLE_REF" \
      || die "GitHub'a erişilemedi ($REPO_URL). HİÇBİR ŞEYE DOKUNULMADI — bundle ile deneyin (tb-4931c73.bundle bu betiğin yanına)"
  fi
  gitc cat-file -e "${TIP}^{commit}" 2>/dev/null || die "hedef commit ${TIP:0:7} getirilemedi. HİÇBİR ŞEYE DOKUNULMADI"
  gitc update-ref "refs/deploy/${TIP:0:7}" "$TIP"
fi
ok "hedef commit ${TIP:0:7} hazır ($SOURCE)"

say "4/9 ileri sarma mümkün mü"
gitc merge-base --is-ancestor "$PREV" "$TIP" || die "mevcut HEAD ${PREV:0:7} hedefin atası değil (ayrışmış kod). HİÇBİR ŞEYE DOKUNULMADI; bana HEAD'i iletin"
echo "   ${PREV:0:7} → ${TIP:0:7}: $(gitc rev-list --count "$PREV..$TIP") commit"
ok "hızlı ileri sarma mümkün"

say "5/9 disk ve servisler"
free_kb="$(df -Pk "$DATA" | awk 'NR==2{print $4}')"
[[ "$free_kb" -gt 2097152 ]] || die "diskte 2 GB'tan az boş yer var ($((free_kb/1024)) MB) — yedek için yetersiz"
ok "boş disk $((free_kb/1024)) MB"
for u in "$WORKER" "$DASH"; do echo "   $u: $(systemctl is-active "$u" || true)"; done
memory_report
say "defterler (şimdi)"
book_snapshot

if [[ "$MODE" == "--dry-run" ]]; then
  say "KURU ÇALIŞMA BİTTİ — kod, state ve servisler DEĞİŞMEDİ (yalnız hedef kod git nesnelerine eklendi)."
  echo "   Dağıtmak için: sudo bash $0"
  exit 0
fi

# ------------------------------------------------------------------ 2) değişiklik
mkdir -p "$LOGDIR"
# SSH bağlantısı koparsa (HUP) dağıtım yarıda KALMAZ: sona kadar sürer, çıktı ayrıca log dosyasına yazılır.
trap '' HUP
# tee de INT/TERM/HUP'u yok sayar: Ctrl+C önce tee'yi öldürürse betiğin sonraki yazımı SIGPIPE ile onu da öldürür ve
# geri alma YARIM kalır (sahte VPS'te ölçüldü).
exec > >(trap '' INT TERM HUP; exec tee -a "$LOGDIR/${TIP:0:7}-deploy.log") 2>&1
echo "   (çıktı ayrıca: $LOGDIR/${TIP:0:7}-deploy.log)"
book_snapshot > "$LOGDIR/${TIP:0:7}-before.txt" 2>&1 || true
echo "$PREV" > "$LOGDIR/${TIP:0:7}-prev-commit.txt"

say "6/9 doğrulanmış yedek (git ve servislere dokunmadan ÖNCE)"
as_svc env -C "$APP" "${ENV_ARGS[@]}" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" \
  TRADINGBOT_BACKUPS_DIR="$DATA/backups" bash "$APP/deploy/backup.sh" manual \
  || die "YEDEK BAŞARISIZ ya da DOĞRULANAMADI → dağıtım DURDURULDU (git/servis dokunulmadı)"
echo "$PREV" > "$BASE/.last_good_commit"       # deploy/rollback.sh bunu kullanır
chown "$SVC_USER:$SVC_USER" "$BASE/.last_good_commit" 2>/dev/null || true
ok "yedek alındı; geri dönüş işaretçisi ${PREV:0:7}"

revert_code() {                 # kodu dağıtım ÖNCESİ duruma döndürür: aynı dal adı, aynı commit
  trap - ERR
  trap '' INT TERM              # geri alma yarıda kesilmez (ikinci Ctrl+C dahil); ÖNCE iş, SONRA ekrana yazı
  if [[ "$BRANCH_NOW" != "detached" ]]; then
    # dalı yalnız bu betiğin az önce yaptığı ileri sarmadan geri alır (dal önceden tam olarak PREV'deydi)
    gitc checkout -q -B "$BRANCH_NOW" "$PREV" || gitc checkout -q "$PREV" \
      || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  else
    gitc checkout -q "$PREV" || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  fi
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt" >/dev/null 2>&1 || true
  echo "   GERİ ALINDI: kod ${PREV:0:7}" || true
}

say "7/9 kod ${PREV:0:7} → ${TIP:0:7} (ff-only)"
gitc merge -q --ff-only "$TIP"
# Bu noktadan sonra beklenmeyen her hata kodu geri alır (servisler henüz yeniden başlatılmadı → eski sürüm çalışıyor).
trap 'revert_code; echo "DUR: beklenmeyen hata (satır $LINENO) → kod ${PREV:0:7}e geri alındı" >&2' ERR
RESTARTED=""
on_abort() {
  trap '' INT TERM
  if [[ -z "$RESTARTED" ]]; then
    revert_code
    echo "DUR: durduruldu → kod ${PREV:0:7}'e geri alındı; servisler YENİDEN BAŞLATILMADI (eski sürüm çalışıyor)" >&2 || true
  else
    echo "DUR: durduruldu — servisler yeni kodla başlatılmıştı; durum için: sudo bash $0 --check" >&2 || true
  fi
  exit 130
}
trap on_abort INT TERM
[[ "$(gitc rev-parse HEAD)" == "$TIP" ]] || die "ileri sarma sonrası HEAD hedef değil"
"$VENV/bin/pip" install -q -r "$APP/requirements.txt"
chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
ok "kod hedefte; bağımlılıklar kuruldu"

say "8/9 preflight (yeni kod, servisin ortamıyla) + config değişmezleri"
if ! py -m tradingbot preflight --quick; then
  revert_code; die "preflight başarısız → kod geri alındı, servisler YENİDEN BAŞLATILMADI (eski sürüm çalışmaya devam ediyor)"
fi
if ! py - <<'PY'
import sys
from tradingbot.config_v3 import load_v3
import yaml
raw = yaml.safe_load(open("config.yaml", encoding="utf-8"))
v3 = load_v3(raw)
from tradingbot.strategy_paper import book_specs
books = {b.name: b for b in book_specs(v3)}
checks = [
    ("mod PAPER", v3.mode.mode == "PAPER"),
    ("gerçek işlem kapalı", not bool(getattr(v3.mode, "live_trading", False))),
    ("T2/M2/Box/D4 defterleri", set(books) == {"t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10"}),
    ("D4 kaldıraç 1", int(books["d4_donchian_20_10"].rule_params.get("leverage", 1)) == 1),
    ("D4 ayrı defter", books["d4_donchian_20_10"].state_dir == "strategy_paper_trend4h"),
    ("formasyon protokolü v3", v3.pattern_trader.protocol == "momentum_4h_v3"),
    ("tek coin tavanı %30", float(raw["risk"]["max_position_pct"]) == 30.0),
    ("işlem başına risk %2", float(raw["risk"]["risk_per_trade_pct"]) == 2.0),
]
bad = [n for n, okk in checks if not okk]
for n, okk in checks:
    print("   %s  %s" % ("OK " if okk else "HATA", n))
sys.exit(1 if bad else 0)
PY
then
  revert_code; die "config değişmezleri düştü → kod geri alındı, servisler YENİDEN BAŞLATILMADI"
fi
ok "preflight ve değişmezler geçti"

say "9/9 yeniden başlatma (mevcut tur biter, state yazılır; en çok ~90 sn) + 60 sn kararlılık"
RESTART_AT="$(date '+%Y-%m-%d %H:%M:%S')"
key_log() {    # yalnız karar verdiren satırlar (analiz gürültüsü değil)
  journalctl -u "$WORKER" --since "$RESTART_AT" --no-pager 2>/dev/null \
    | grep -E 'Started|Stopping|Stopped|Main process exited|Scheduled restart|Failed|Traceback|ERROR|CRITICAL|Killed|oom|BLOCK|ALLOW' \
    | tail -n 40 || true
}
# Worker SIGTERM'de MEVCUT TURU bitirip çıkar; tur ~10 dk sürer, TimeoutStopSec 90 sn → tur ortasında durdurmak SIGKILL
# demektir (2026-09-25 ilk denemede iki kez oldu). Turlar arası beklemede ise 2 sn içinde temiz kapanır ve state yazılır.
# Bekleme aralığı: heartbeat.json yalnız turlar arasında "source": "watch" ile ~30 sn'de bir yazılır (tur başı yazımı
# bu alanı taşımaz).
idle_now() {
  py - "$STATE/heartbeat.json" <<'PY'
import datetime as dt, json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    t = dt.datetime.fromisoformat(str(d.get("ts") or d.get("at")).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("source") == "watch" and age < 45 else 1)
PY
}
waited=0
until idle_now; do
  if (( waited >= 1800 )); then
    revert_code
    die "worker 30 dk içinde turlar arası beklemeye girmedi → kod ${PREV:0:7}'e geri alındı, servisler YENİDEN BAŞLATILMADI"
  fi
  (( waited % 60 == 0 )) && echo "   worker turda; tur bitince yeniden başlatılacak (bekleniyor: $((waited / 60)) dk)"
  sleep 10; waited=$((waited + 10))
done
ok "worker turlar arası beklemede → şimdi yeniden başlatılıyor"
RESTARTED=1
systemctl restart "$WORKER" "$DASH" || true      # preflight düşerse restart hata döner; aşağıda yakalanır
# NRestarts yalnız OTOMATİK yeniden başlamaları sayar ve elle restart'ta SIFIRLANIR: dağıtım öncesi değerle
# KARŞILAŞTIRILMAZ (2026-09-25 VPS: sağlıklı worker bu yüzden "kalkmadı" sayılıp geri alınmıştı). Taban = restart'tan
# hemen sonraki değer; kararlılık = 60 sn boyunca aynı süreç (MainPID) ve taban üstünde yeni otomatik başlama yok.
n0="$(systemctl show "$WORKER" -p NRestarts --value)"
up=""
for _ in $(seq 1 36); do                         # en çok 3 dk: preflight + başlatma
  sleep 5
  if [[ "$(systemctl is-active "$WORKER" || true)" == "active" ]]; then up=1; break; fi
done
pid1=""
if [[ -n "$up" ]]; then
  pid1="$(systemctl show "$WORKER" -p MainPID --value)"
  sleep 60
  if [[ "$(systemctl is-active "$WORKER" || true)" != "active" || -z "$pid1" || "$pid1" == "0" \
        || "$(systemctl show "$WORKER" -p MainPID --value)" != "$pid1" \
        || "$(systemctl show "$WORKER" -p NRestarts --value)" != "$n0" ]]; then
    up=""
  fi
fi
if [[ -z "$up" ]]; then
  key_log
  revert_code; systemctl restart "$WORKER" "$DASH" || true
  die "worker kararlı çalışmadı (aktif olmadı ya da 60 sn içinde yeniden başladı) → kod ${PREV:0:7}'e geri alındı ve servisler yeniden başlatıldı. Yukarıdaki satırları bana iletin"
fi
trap - ERR INT TERM
ok "worker çalışıyor (PID $pid1, 60 sn kararlı)"
echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'henüz yanıt yok (birkaç sn sonra --check)')"
memory_report

cat <<EOF

DAĞITIM TAMAM: ${PREV:0:7} → ${TIP:0:7}. Defterler sıfırlanmadı (önceki durum: $LOGDIR/${TIP:0:7}-before.txt).

İzleme:
  sudo bash $0 --check                         # durum, bellek, defterler, karne, son uyarılar
  journalctl -u $WORKER -f                     # canlı log (Ctrl+C ile çık)
İlk tur ~10-15 dk sürer; 4h trend defteri (state/strategy_paper_trend4h) ilk turdan sonra görünür.
Bellek: ilk 2-3 turda tepe değeri 4G sınırına yaklaşırsa (ör. > 3,6G) bana --check çıktısını iletin.

Geri alma (gerekirse): sudo bash $APP/deploy/rollback.sh   → ${PREV:0:7}
  d8b0c8c'ye dönmek OOM onarımını korur (sudo bash $APP/deploy/rollback.sh d8b0c8c) ama ana botun geçmiş mumlarla
  kapanış yazmasını ve izleme gecikmesini geri getirir. Tercih: sorunu bana iletin, ileri düzeltme yapalım.
EOF
if gitc merge-base --is-ancestor "$PREV" d8b0c8c 2>/dev/null && [[ "$(gitc rev-parse d8b0c8c 2>/dev/null)" != "$PREV" ]]; then
  echo "  UYARI: ${PREV:0:7} bellek (OOM) onarımından ÖNCEKİ koddur — ona dönmek OOM'u GERİ GETİRİR; sağlıklı bir dönüş değildir."
fi

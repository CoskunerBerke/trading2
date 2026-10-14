#!/usr/bin/env bash
# trading2 — VPS dağıtımı (PAPER; gerçek para YOK). Hedef: TIP değişkeni (dal claude/gifted-knuth-0ehpcs).
# Ön koşul: VPS'te çalışan kod c0b8c94 (öğrenme modu L1) ya da onun soyu (hedefin atası). Daha eskiyse HİÇBİR ŞEYE
# dokunmadan durur.
#
# BU SÜRÜM (2026-09-29) üç değişikliği BİRLİKTE getirir; öğrenme modu (L1) AYNEN kalır:
#
# 1) ORTAK DENEYİM KATMANI — 1. AŞAMA: YALNIZ KAYIT, KARAR DEĞİŞTİRMEZ.
#    Tek hafıza: bütün botların (ana bot, T2, M2, Box, D4, C4, C4S, Formasyon) gerçek işlemleri (giriş + sonuç) ve
#    açılmayan geçerli sinyallerin "olsaydı" kayıtları, girişteki PİYASA DURUMU anlık görüntüsüyle (4h trend/oynaklık/
#    yapı/hacim, 1h, BTC) tek bir kayda yazılır: /opt/tradingbot/data/state/shared_experience/. Soru: "bu durumda bu
#    kurulumla daha önce kazandık mı?" → python -m tradingbot shared-experience-report (elle; tanımlayıcıdır).
#    Katman hiçbir deftere/pozisyona/plana/öğreniciye YAZMAZ, hiçbir kararı DEĞİŞTİRMEZ; arızası turu durdurmaz (devre
#    kesici kendini kapatır). Yalnız PAPER: mod PAPER değilse ya da canlı emir yolu açıksa kendini askıya alır
#    (SUSPENDED:<mod>) ve satır yazmaz. İlk ~180 tur (~18 sa) mevcut geçmişi de geriye doğru kaydeder (geri doldurma).
#
# 2) "OLSAYDI" (KARŞI-OLGUSAL) R'Sİ ARTIK MALİYET DÜŞÜLMÜŞ (NET) — cf_label_v3.
#    Eskiden "olsaydı" R'si brüttü (ücret/kayma/funding yok); gerçek işlemlerin R'si ise net. Artık her defterin KENDİ
#    ücret/kayma/funding modeliyle aynı kapanmış barlarda, bar içi nedensel yolla (açılış → ters uç → lehte uç →
#    kapanış) yeniden oynatılan net R yazılır. Eski brüt etiketler dağıtım sırasında (worker DURMUŞKEN, yeni kod
#    yerindeyken, yeniden başlatmadan önce) scripts/cf_backfill_net.py ile net'e çevrilir: önce kuru çalışma, sonra
#    --apply (her değişen dosyanın yanına <dosya>.pre-cf-net-<UTC> yedeği). Mum alınamazsa (ağ) --no-fetch ile kapalı-
#    biçim YAKLAŞIK net (v1c) yazılır; o net ortalamasına ASLA karışmaz ve sonraki mumlu bir koşuda v3'e yükseltilir.
#    Brüt alanlar (r_multiple, won, …) DEĞİŞMEZ; P&L'e hiçbir "olsaydı" kaydı girmez.
#
# 3) CANLI MUHASEBE DÜZELTMESİ (stop taşıma): fiyat izlemesiyle stop taşındıktan (MFE başa-baş / TP1) sonra gelen,
#    taşımadan ÖNCE açılmış kapanmış 1h bar pozisyonu artık o barın eski açılışından kapatmaz; bar kendi süresinde
#    geçerli stoplarla sınanır (olay BAR_PRE_MOVE_STOP; sıra belirsizse BAR_SKIPPED / OPENED_BEFORE_STOP_MOVE; kapanışta
#    exit_fill.stop_in_force = PRE_MOVE). Bu, öğrenme modu kapalıyken de PAPER muhasebesini değiştirir (tasarım gereği).
#
# KATMANI KAPATMAK (eski koda dönmeden; öğrenme modunu ETKİLEMEZ): /opt/tradingbot/app/config.yaml içinde
#   shared_experience:
#     enabled: false
#     mode: RECORD
# sonra: sudo systemctl restart tradingbot-worker   (ya da drop-in: sudo systemctl edit tradingbot-worker →
#   [Service] Environment=TRADINGBOT_SHARED_EXPERIENCE=off, sonra restart). state/shared_experience/ kalır, zararsızdır.
#   (config.yaml düzenlemesi bir sonraki dağıtımda "yerel değişiklik" olarak görünür; o zaman bana haber verin.)
# ÖĞRENME MODUNU KAPATMAK önceki sürümdeki gibidir: config.yaml → learning_mode: enabled: false + worker/panel restart.
#
# Kod kaynağı: herkese açık GitHub deposu (bu sürüm için bundle YOK; betiğin yanında bundle varsa durur)
# (commit TAM SHA ile istenir ve doğrulanır — içerik commit kimliğine bağlıdır). VPS'te (dosya adı tb-deploy-<hedefin
# ilk 7 hanesi>.sh):
#   sudo bash tb-deploy-f9a61dd.sh --dry-run   # kontroller + yeni kodun AYRI kopyada sınanması (preflight, değişmezler);
#                                              # çalışan kod/state/servis DEĞİŞMEZ
#   sudo bash tb-deploy-f9a61dd.sh             # dağıt (bu terminalde)
#   sudo bash tb-deploy-f9a61dd.sh --detach    # dağıt, SSH'tan BAĞIMSIZ ayrı systemd görevi olarak; çıktıyı izler.
#                                              # Pencere/PC kapansa da sürer. Ctrl+C yalnız İZLEMEYİ bırakır.
#   sudo bash tb-deploy-f9a61dd.sh --check     # dağıtım sonrası durum + geri alma tetikleri (state'e dokunmaz; yalnız
#                                              # deploy-logs'a Box ölçüm örneği ekler; tekrar tekrar koşulabilir)
#
# Sıra: kaynak → HEAD + temiz ağaç → hedef commit'in getirilmesi → ileri sarma + çalışan sürüm (c0b8c94 ya da soyu) →
#       disk, bellek sınırı, ortam (TRADINGBOT_LEARNING_MODE yok; TRADINGBOT_SHARED_EXPERIENCE yok ya da yalnız off),
#       state/mode.json PAPER, state/shared_experience yazılabilir → yeni kod AYRI kopyada: preflight + 34 config
#       değişmezi (servis ortamıyla) → [dry-run burada biter] → doğrulanmış yedek → worker'ın turlar arası beklemesi →
#       ortamın son denetimi → worker DURUR → hızlı ileri sarma (ff-only) → state/shared_experience açılır →
#       doğrulanmış yedek (worker durmuşken, yeni kodla) → karşı-olgusal net dolgu (kuru çalışma + --apply; servis
#       kullanıcısıyla) → worker başlar, panel yeniden başlar → 60 sn kararlılık.
#       Yedek alınamazsa git'e ve servislere DOKUNULMAZ. Preflight ya da değişmezler düşerse çalışan kod hiç değişmez.
#       İleri sarmadan sonra herhangi bir adım (yedek, dolgu) düşerse ya da servis kararlı kalkmazsa geri alınır:
#       worker durur → eski commit → worker başlar (eski kod yeni state'i tolere eder; aşağıda "ESKİ KODA dönmek").
#       Yedeklere ve defterlere hiçbir adımda dokunulmaz.
set -Eeuo pipefail

TIP="f9a61ddff64ff6bfe8a17315eb1e62efcf8f8c22"
BUNDLE_SHA256=""                          # bu sürüm için bundle yok (GitHub kaynağı)
BUNDLE_REF="refs/heads/claude/gifted-knuth-0ehpcs"
# VPS'te çalışan sürüm (2026-09-28 21:20 UTC dağıtımı): c0b8c94 — öğrenme modu L1. Çalışan HEAD bunun kendisi ya da
# soyundan olmalı (ve hedefin atası); daha eski kod reddedilir (2026-09-29).
PREREQ=("c0b8c941be50b4f421174b575820f9395472f956")
OOM_FIX="d8b0c8c"                         # bellek (OOM) onarımı; bundan eskiye dönmek OOM'u geri getirir
REPO_URL="https://github.com/CoskunerBerke/trading2.git"   # herkese açık depo (bundle yoksa)

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"; DATA="$BASE/data"; STATE="$DATA/state"
SVC_USER="${TRADINGBOT_USER:-tradingbot}"
WORKER="tradingbot-worker.service"; DASH="tradingbot-dashboard.service"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_SRC="$HERE/tb-${TIP:0:7}.bundle"
LOGDIR="$BASE/deploy-logs"
T7="${TIP:0:7}"
MARK_RESTART="$LOGDIR/$T7-restart-at.txt"      # yeniden başlatma anı (epoch + yerel saat) — --check pencereleri
BOX_SAMPLES="$LOGDIR/$T7-box-samples.txt"      # --check Box ölçüm örnekleri (saatlik kaçan bar oranı için)
MARK_PRE_HWM="$LOGDIR/$T7-pre-hwm.txt"         # (2026-09-29) dağıtım öncesi worker tepe belleği (hwm_mb) — --check farkı
MEM_EXPECT=6442450944                          # worker MemoryMax beklenen 6G (bayt)
MEM_OLD=4294967296                             # depodaki eski unit değeri 4G (bayt)
# ORTAK DENEYİM (2026-09-29): katmanın state klasörü (config varsayılanı `shared_experience.state_dir`) ve karşı-olgusal
# net dolgunun raporları. Mum çekimi (ağ) en çok CF_FETCH_TIMEOUT sn sürebilir (worker bu sırada DURMUŞ); aşılırsa
# --no-fetch ile sürdürülür (yaklaşık v1c; sonraki mumlu koşu v3'e yükseltir).
XP_DIR="$STATE/shared_experience"
CF_DRY_JSON="$DATA/cf_backfill_dry.json"
CF_APPLY_JSON="$DATA/cf_backfill_apply.json"
CF_FETCH_TIMEOUT=300
MODE="${1:-deploy}"

say() { printf '\n== %s\n' "$*"; }
ok()  { printf '   OK  %s\n' "$*"; }
die() { printf '\nDUR: %s\n' "$*" >&2; exit 1; }
as_svc() { sudo -u "$SVC_USER" "$@"; }
gitc() { as_svc git -C "$APP" "$@"; }
shorts() { local s="" p; for p in "$@"; do s+="${s:+ / }${p:0:7}"; done; printf '%s' "$s"; }

# (2026-09-29) hedef TAM SHA olmalı: yer tutucu doldurulmadan betik HİÇBİR modda çalışmaz.
[[ "$TIP" =~ ^[0-9a-f]{40}$ ]] || die "TIP tam bir commit SHA'sı değil ('$TIP'): betik yayımlanmadan önce doldurulmalı. HİÇBİR ŞEYE DOKUNULMADI"

# Servisin ortamı: unit'in Environment= satırları + EnvironmentFile. Değerler KOMUT SATIRINA KONMAZ: sudo tam komut
# satırını sistem log'una yazar (2026-09-25 VPS'te görüldü; o gün değerler boştu). Geçici dosyaya (0600, servis
# kullanıcısı) yazılır, alt süreç oradan okur; çıkışta silinir.
ENV_ARGS=()
ENVFILE=""
CF_TMP=""                                 # (2026-09-29) karşı-olgusal dolgu çıktısının geçici kopyası
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
  ENVFILE="$(mktemp /tmp/tb-deploy-env.XXXXXX)"
  chmod 600 "$ENVFILE"; printf '%s\n' "${ENV_ARGS[@]}" > "$ENVFILE"; chown "$SVC_USER" "$ENVFILE"
}
trap 'rm -f "$ENVFILE" "$CF_TMP"' EXIT
# svc_run DİZİN KOMUT...: servis kullanıcısı + servis ortamı, verilen dizinde (değerler dosyadan; komut satırında yok)
svc_run() {
  local dir="$1"; shift
  as_svc bash -c 'd="$1"; f="$2"; shift 2; while IFS= read -r l; do export "$l"; done < "$f"; cd "$d" && exec "$@"' \
    _ "$dir" "$ENVFILE" "$@"
}
py() { svc_run "$APP" "$VENV/bin/python" "$@"; }

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

memory_report() {   # worker ve panel cgroup belleği (şu an / tepe / sınır) + OOM sayısı
  local u cg
  _mem() { local v; v="$(cat "$1" 2>/dev/null || true)"
           if [[ "$v" =~ ^[0-9]+$ ]]; then numfmt --to=iec "$v" 2>/dev/null || echo "$v"; else echo "${v:-?}"; fi; }
  for u in "$WORKER" "$DASH"; do
    cg="/sys/fs/cgroup/system.slice/$u"
    printf '   %-30s bellek (şu an / tepe / sınır): %s / %s / %s   OOM kill: %s\n' "$u" "$(_mem "$cg/memory.current")" \
      "$(_mem "$cg/memory.peak")" "$(_mem "$cg/memory.max")" "$(awk '$1=="oom_kill"{print $2}' "$cg/memory.events" 2>/dev/null || echo ?)"
  done
}

MEMMAX=""
memmax_check() {    # worker MemoryMax: 6G beklenir. 4G ise YÜKSEK SESLE uyarır (dağıtımı durdurmaz)
  local mm h
  mm="$(systemctl show "$WORKER" -p MemoryMax --value 2>/dev/null || true)"
  MEMMAX="$mm"
  if [[ "$mm" =~ ^[0-9]+$ ]]; then
    h="$(numfmt --to=iec "$mm" 2>/dev/null || echo "$mm")"
    if (( mm == MEM_EXPECT )); then
      ok "worker MemoryMax = $h ($mm bayt; beklenen 6G)"
    elif (( mm <= MEM_OLD )); then
      cat <<EOF
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
   !!! UYARI: worker MemoryMax = $h ($mm bayt) — BEKLENEN 6G. Worker bugün ~3,4G kullanıyor, tepesi ~4,5G:
   !!! bu sınırda OOM (bellek) ile öldürülme riski YÜKSEK; öğrenme modu kayıt dosyalarını daha hızlı büyütür.
   !!! Dağıtımdan ÖNCE sınırı 6G yapmanız önerilir (kalıcı; worker'ı yeniden başlatmaz):
   !!!     sudo systemctl set-property $WORKER MemoryMax=6G
   !!! Dağıtım bu yüzden DURDURULMADI; geri alma tetiği (tepe > %90 × MemoryMax) bu sınıra göre hesaplanır.
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
    elif (( mm < MEM_EXPECT )); then
      echo "   UYARI: worker MemoryMax = $h ($mm bayt) — beklenen 6G (tepe ~4,5G; %90 eşiği $((mm * 9 / 10 / 1048576)) MB)"
    else
      echo "   worker MemoryMax = $h ($mm bayt; 6G'den büyük)"
    fi
  else
    echo "   UYARI: worker MemoryMax okunamadı ya da sınırsız ('${mm:-?}') — beklenen 6G"
  fi
}

# TRADINGBOT_LEARNING_MODE servis ortamında OLMAMALI: off/false/0/disabled öğrenmeyi SESSİZCE kapatır (dağıtım boşa
# gider), başka her değer ConfigError'dur (worker hiç başlamaz). Unit Environment= (drop-in dahil), EnvironmentFile'lar
# ve $BASE/env taranır.
lm_env_scan() {
  local hits=() e f
  for e in "${ENV_ARGS[@]}"; do
    if [[ "$e" == TRADINGBOT_LEARNING_MODE=* ]]; then hits+=("servis ortamı: $e"); fi
  done
  while IFS= read -r f; do
    [[ -n "$f" && -r "$f" ]] || continue
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?TRADINGBOT_LEARNING_MODE[[:space:]]*=' "$f" 2>/dev/null; then
      hits+=("ortam dosyası: $f")
    fi
  done < <( { systemctl show "$WORKER" -p EnvironmentFiles --value 2>/dev/null | grep -oE '/[^ ]+' || true; echo "$BASE/env"; } | sort -u)
  if (( ${#hits[@]} )); then
    printf '   HATA  TRADINGBOT_LEARNING_MODE tanımlı:\n'; printf '           %s\n' "${hits[@]}"
    return 1
  fi
  ok "TRADINGBOT_LEARNING_MODE servis ortamında yok (config.yaml geçerli)"
}
lm_env_hint="off/false/0/disabled öğrenme modunu SESSİZCE kapatır, başka değer ConfigError (worker başlamaz). Satırı
  silin (unit için: sudo systemctl edit $WORKER; dosya için: $BASE/env) ve betiği yeniden çalıştırın"

# ORTAK DENEYİM ENV (2026-09-29): TRADINGBOT_SHARED_EXPERIENCE unit'te (drop-in dahil), EnvironmentFile'larda ve
# $BASE/env'de OLMAMALI. Varsa YÜKSEK SESLE basılır: yalnız off/false/0/disabled kabul edilir (katman KAPALI kalır —
# operatörün bilinçli kapatması; dağıtımın geri kalanı sürer); başka her değer yeni kodda ConfigError'dur (worker
# BAŞLAMAZ) → dur. Boş değer yükleyicide etkisizdir (yok sayılır).
xp_env_norm() { local v="$1"; v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
                if [[ ${#v} -ge 2 && ( ( "${v:0:1}" == '"' && "${v: -1}" == '"' ) || ( "${v:0:1}" == "'" && "${v: -1}" == "'" ) ) ]]; then
                  v="${v:1:${#v}-2}"; fi
                v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"; printf '%s' "${v,,}"; }
XP_ENV_OFF=""
xp_env_scan() {
  local hits=() vals=() e f v line bad=0
  XP_ENV_OFF=""
  for e in "${ENV_ARGS[@]}"; do
    if [[ "$e" == TRADINGBOT_SHARED_EXPERIENCE=* ]]; then hits+=("servis ortamı: $e"); vals+=("${e#*=}"); fi
  done
  while IFS= read -r f; do
    [[ -n "$f" && -r "$f" ]] || continue
    while IFS= read -r line; do
      hits+=("ortam dosyası $f: TRADINGBOT_SHARED_EXPERIENCE=$line"); vals+=("$line")
    done < <(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?TRADINGBOT_SHARED_EXPERIENCE[[:space:]]*=(.*)$/\2/p' "$f" 2>/dev/null || true)
  done < <( { systemctl show "$WORKER" -p EnvironmentFiles --value 2>/dev/null | grep -oE '/[^ ]+' || true; echo "$BASE/env"; } | sort -u)
  if (( ${#hits[@]} == 0 )); then
    ok "TRADINGBOT_SHARED_EXPERIENCE servis ortamında yok (config.yaml geçerli: katman RECORD)"
    return 0
  fi
  for v in "${vals[@]}"; do
    v="$(xp_env_norm "$v")"
    if [[ -z "$v" ]]; then continue; fi
    if [[ "$v" =~ ^(off|false|0|disabled)$ ]]; then XP_ENV_OFF=1; else bad=1; fi
  done
  printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  printf '   !!! TRADINGBOT_SHARED_EXPERIENCE TANIMLI (servis ortamı / drop-in / ortam dosyası):\n'
  printf '   !!!     %s\n' "${hits[@]}"
  if (( bad )); then
    printf '   !!! HATA: yalnız off/false/0/disabled kabul edilir; başka değer yeni kodda ConfigError (worker BAŞLAMAZ).\n'
    printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
    return 1
  fi
  if [[ -n "$XP_ENV_OFF" ]]; then
    printf '   !!! Ortak deneyim katmanı bu ortamla KAPALI kalacak (operatör kapatması kabul edildi). Net etiket ve canlı\n'
    printf '   !!! muhasebe düzeltmesi yine dağıtılır. Katmanı açmak için satırı silin ve worker'"'"'ı yeniden başlatın.\n'
  else
    printf '   !!! Değer boş: yükleyici yok sayar (katman config.yaml ile RECORD). Satırı silmeniz önerilir.\n'
  fi
  printf '   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
  return 0
}
xp_env_hint="yalnız off/false/0/disabled kabul edilir (katmanı kapatır); başka değer ConfigError (worker başlamaz). Satırı
  silin ya da düzeltin (unit için: sudo systemctl edit $WORKER; dosya için: $BASE/env) ve betiği yeniden çalıştırın"

# ORTAK DENEYİM KLASÖRÜ (2026-09-29): <data>/state/shared_experience servis kullanıcısı için açılabilir ve yazılabilir
# olmalı. `check`: HİÇBİR ŞEY yazmaz (yoksa state klasörünün yazılabilirliğine bakar). `ensure`: yoksa
# `install -d -o $SVC_USER -g $SVC_USER` ile açar (worker'ın UMask=0027'siyle aynı: 0750) ve servis kullanıcısıyla
# gerçek bir deneme dosyası yazıp siler.
xp_dir_check() {
  local how="$1" own
  if [[ -e "$XP_DIR" && ! -d "$XP_DIR" ]]; then
    echo "   HATA  $XP_DIR var ama klasör değil"; return 1
  fi
  if [[ -d "$XP_DIR" ]]; then
    own="$(stat -c '%U:%G %a' "$XP_DIR" 2>/dev/null || echo '?')"
    if ! as_svc test -w "$XP_DIR" -a -x "$XP_DIR"; then
      echo "   HATA  $XP_DIR servis kullanıcısı ($SVC_USER) için yazılabilir değil ($own)"; return 1
    fi
  else
    if ! as_svc test -w "$STATE" -a -x "$STATE"; then
      echo "   HATA  $XP_DIR yok ve $STATE servis kullanıcısı ($SVC_USER) için yazılabilir değil — açılamaz"; return 1
    fi
    if [[ "$how" != ensure ]]; then
      ok "state/shared_experience yok; servis kullanıcısı açabilir (dağıtımda install -d ile açılacak)"; return 0
    fi
    install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$XP_DIR" || { echo "   HATA  install -d $XP_DIR başarısız"; return 1; }
    own="$(stat -c '%U:%G %a' "$XP_DIR" 2>/dev/null || echo '?')"
    echo "   state/shared_experience açıldı ($own)"
  fi
  if [[ "$how" == ensure ]]; then
    # shellcheck disable=SC2016
    as_svc bash -c 'f="$(mktemp "$1/.tb-deploy-probe.XXXXXX")" && rm -f -- "$f"' _ "$XP_DIR" \
      || { echo "   HATA  $XP_DIR içine servis kullanıcısıyla yazılamadı"; return 1; }
  fi
  ok "state/shared_experience servis kullanıcısı ($SVC_USER) için yazılabilir ($own)"
}

mode_check() {      # state/mode.json: yok ya da mode=PAPER (öğrenme yalnız PAPER'da açılır)
  py - "$STATE/mode.json" <<'PY'
import json, os, sys
p = sys.argv[1]
if not os.path.exists(p):
    print("   OK  state/mode.json yok (varsayılan PAPER)")
    sys.exit(0)
try:
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
except Exception as exc:  # noqa: BLE001
    print("   HATA  state/mode.json okunamadı (%s) — elle bakın" % type(exc).__name__)
    sys.exit(1)
m = d.get("mode") if isinstance(d, dict) else None
lop = d.get("live_order_path_enabled") if isinstance(d, dict) else None
if m == "PAPER" and lop is not True:
    print("   OK  state/mode.json: mode=PAPER")
    sys.exit(0)
print("   HATA  state/mode.json: mode=%r live_order_path_enabled=%r — öğrenme modu yalnız PAPER'da açılır" % (m, lop))
sys.exit(1)
PY
}

# --check: öğrenme modu durumu, defter başına slot/marj/karşı-olgusal, dosya boyları ve GERİ ALMA TETİKLERİ (salt okunur;
# son satır "BOXSAMPLE ..." çağırana ölçüm örneğidir, ekrana basılmaz).
learning_report() {
  local rs_epoch="" rs_human="" tours=""
  if [[ -r "$MARK_RESTART" ]]; then
    rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; rs_human="$(sed -n 2p "$MARK_RESTART" || true)"
  fi
  # tur süreleri: worker her turun sonunda "🎓 ÖĞRENME: … · <saniye>s" basar (yeniden başlatmadan beri; yoksa son 6 saat)
  tours="$(journalctl -u "$WORKER" --since "${rs_human:--6h}" -o cat --no-pager 2>/dev/null \
            | grep -aE 'ÖĞRENME: .* [0-9]+(\.[0-9]+)?s$' | sed -nE 's/.* ([0-9]+(\.[0-9]+)?)s$/\1/p' | tail -n 40 | tr '\n' ' ' || true)"
  local out
  out="$(py - "$STATE" "$APP" "${MEMMAX:-}" "$rs_epoch" "$tours" "$BOX_SAMPLES" <<'PY'
import datetime as dt
import json
import os
import sys
import time

st, app, memmax, rs_epoch, tours_s, boxs = sys.argv[1:7]
now = time.time()


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def ago(e):
    return "?" if e is None else "%.0f dk önce" % ((now - e) / 60.0)


def unreadable(d):
    """rj() okunamayan dosya için {"__err__": tür} döner: boş sayılmaz, OKUNAMADI yazılır."""
    return isinstance(d, dict) and "__err__" in d


rs = fl(rs_epoch)
warns = []
print("   (yeniden başlatma: %s)" % (dt.datetime.fromtimestamp(rs).strftime("%Y-%m-%d %H:%M:%S") if rs else "kayıt yok"))

# ---------------------------------------------------------------- öğrenme modu (health.json)
print("\n-- öğrenme modu (health.json)")
h = rj(os.path.join(st, "health.json"))
if unreadable(h):
    print("   health.json OKUNAMADI (%s)" % h["__err__"])
    warns.append("health.json okunamadı")
h = h if isinstance(h, dict) and not unreadable(h) else {}
h_at = epoch(h.get("at"))
if h:
    print("   health: durum=%s  yazıldı=%s (%s)  son tur=%ss" % (h.get("state"), h.get("at"), ago(h_at), h.get("seconds")))
lm = h.get("learning_mode")
if not isinstance(lm, dict):
    print("   learning_mode: health.json'da YOK — yeni worker ilk turunu henüz bitirmedi (WFO + tur 1 saati aşabilir)"
          " ya da öğrenme kapalı")
    warns.append("health.json'da learning_mode yok")
    lm = {}
else:
    print("   aktif=%s  neden=%s  durum değişimi=%s  öğrenme başlangıcı=%s"
          % (lm.get("active"), lm.get("reason"), lm.get("since"), lm.get("learning_mode_since")))
    if lm.get("active") is not True:
        warns.append("öğrenme modu AKTİF DEĞİL (neden %s)" % lm.get("reason"))
    mem = lm.get("memory") if isinstance(lm.get("memory"), dict) else {}
    print("   bellek: rss_mb=%s  hwm_mb=%s  deneyim önbelleği satırları=%s"
          % (mem.get("rss_mb"), mem.get("hwm_mb"), mem.get("exp_cache_rows")))
    tm = mem.get("trade_memory") if isinstance(mem.get("trade_memory"), dict) else {}
    print("   trade_memory okuyucusu: mb=%s  tam okuma=%s  artımlı okuma=%s  temiz=%s"
          % (tm.get("mb"), tm.get("full_loads"), tm.get("incremental_loads"), tm.get("clean")))
    if tm and tm.get("clean") is not True:
        warns.append("trade_memory okuyucusu temiz değil (clean=%s; eski okuyucuya düşüyor)" % tm.get("clean"))
    pb = lm.get("policy_basis") if isinstance(lm.get("policy_basis"), dict) else {}
    if pb.get("status") == "LOST":
        # görünüm öğrenme başladıktan sonra kayboldu; motor kirli öğreniciden YENİDEN KURMAZ (dördüncü doğrulama turu)
        print("   politika tabanı: KAYIP (%s; dosya durumu %s; öğrenme başlangıcı %s)"
              % (pb.get("code"), pb.get("file"), pb.get("learning_mode_since")))
        warns.append("politika tabanı KAYIP (POLICY_BASIS_LOST, dosya %s): state yedeğinden "
                     "learning_policy_basis.json (ya da .bak) geri yüklenip worker yeniden başlatılmalı; o zamana kadar "
                     "politika etiketi LEARNING_LEARNER" % pb.get("file"))
    else:
        print("   politika tabanı: seeded_at=%s  n_policy=%s  n_extra=%s  (updated_at=%s, v1_n_trades=%s)"
              % (pb.get("seeded_at"), pb.get("n_policy"), pb.get("n_extra"), pb.get("updated_at"), pb.get("v1_n_trades")))
        if not pb:
            warns.append("politika tabanı (policy_basis) yok")

# ---------------------------------------------------------------- defterler
print("\n-- defterler: açık pozisyon · slot (açık/K) · Σmarj/E · öğrenmede açılan (ekstra) · karşı-olgusal")
cfg = {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception as exc:  # noqa: BLE001
    warns.append("config.yaml okunamadı (%s): K bilinmiyor" % type(exc).__name__)
lmc = cfg.get("learning_mode") if isinstance(cfg.get("learning_mode"), dict) else {}
lmb = lmc.get("books") if isinstance(lmc.get("books"), dict) else {}


def slots_k(name):
    b = lmb.get(name) if isinstance(lmb.get(name), dict) else {}
    return b.get("slots", 20) if (lmc.get("enabled") is True and b.get("enabled") is True) else None


books = [("main", "ana bot", st, None)]
idx = rj(os.path.join(st, "strategy_paper_index.json"))
if unreadable(idx):
    warns.append("strategy_paper_index.json okunamadı (%s): kâğıt defterler listelenemedi" % idx["__err__"])
for b in ((idx or {}).get("books") or []) if isinstance(idx, dict) else []:
    key = str((b or {}).get("key") or "") if isinstance(b, dict) else ""
    if key and all(c.isalnum() or c == "_" for c in key):
        books.append((str(b.get("name") or key), key, os.path.join(st, key), str(b.get("summary_file") or key + ".json")))
if os.path.isdir(os.path.join(st, "pattern_trader")):
    books.append(("pattern_trader", "pattern_trader", os.path.join(st, "pattern_trader"), "pattern_trader.json"))

for name, label, bdir, summ in books:
    led = rj(os.path.join(bdir, "futures_ledger.json"))
    if not isinstance(led, dict) or "__err__" in led:
        print("   %-30s defter %s" % (name, "yok" if led is None else "OKUNAMADI (%s)" % led.get("__err__")))
        continue
    pos = led.get("positions") or {}
    pos = list(pos.values()) if isinstance(pos, dict) else [p for p in pos if isinstance(p, dict)]
    margins = [fl(p.get("isolated_margin", p.get("margin"))) for p in pos if isinstance(p, dict)]
    msum = sum(m for m in margins if m is not None)
    if name == "main":
        rk = rj(os.path.join(st, "risk.json"))
        eq = fl(((rk or {}).get("learning_mode") or {}).get("equity_basis") if isinstance(rk, dict) else None)
    else:
        sd = rj(os.path.join(st, summ)) if summ else None
        sd = sd if isinstance(sd, dict) else {}
        eq = fl((sd.get("summary") or {}).get("starting_equity") if isinstance(sd.get("summary"), dict) else None) \
            or fl(sd.get("starting_equity"))
    eq = eq or fl(led.get("starting_equity"))
    lt = [((p.get("meta") or {}).get("learning") if isinstance(p.get("meta"), dict) else None) for p in pos]
    n_learn = sum(1 for t in lt if isinstance(t, dict))
    n_extra = sum(1 for t in lt if isinstance(t, dict) and t.get("learning_unlocked_by"))
    if name == "main":
        cf = rj(os.path.join(st, "shadow_book.json"))
        rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict) and t.get("book") == "main"] \
            if isinstance(cf, dict) and not unreadable(cf) else ([] if cf is None else None)
        cf_src = "shadow_book.json (book=main)"
        dropped = None
    else:
        cf = rj(os.path.join(bdir, "counterfactual_trades.json"))
        rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict)] \
            if isinstance(cf, dict) and not unreadable(cf) else None
        cf_src = "counterfactual_trades.json"
        dropped = (cf.get("meta") or {}).get("dropped") if isinstance(cf, dict) and not unreadable(cf) else None
    if unreadable(cf):
        cf_txt = "karşı-olgusal: %s OKUNAMADI (%s)" % (cf_src.split(" ")[0], cf["__err__"])
        warns.append("%s: %s okunamadı" % (name, cf_src.split(" ")[0]))
    elif rows is None:
        cf_txt = "karşı-olgusal: dosya yok"
    else:
        lab = sum(1 for t in rows if isinstance(t.get("outcome"), dict))
        cf_txt = "karşı-olgusal: bekleyen %d · etiketli %d%s  [%s]" % (
            len(rows) - lab, lab, (" · düşürülen %s" % dropped) if dropped else "", cf_src)
    k = slots_k(name)
    frac = (msum / eq) if eq else None
    extra = ""
    if name == "main":
        sp = rj(os.path.join(st, "spot_ledger.json"))
        lots = (sp or {}).get("lots") if isinstance(sp, dict) else None
        n_spot = sum(1 for v in (lots or {}).values() if v) if isinstance(lots, dict) else 0
        extra = " (+%d spot)" % n_spot if n_spot else ""
    print("   %-30s açık %3d%s  slot %3d/%-4s  Σmarj/E %6s  öğrenmede %3d (ekstra %d)  %s"
          % (name, len(pos), extra, len(pos), k if k is not None else "—",
             ("%.0f%%" % (frac * 100)) if frac is not None else "?", n_learn, n_extra, cf_txt))
    if frac is not None and frac > 0.95:
        warns.append("%s: Σmarj/E %.0f%% > %%95 (öğrenme sınırı aşılmış görünüyor)" % (name, frac * 100))
    if k is not None and len(pos) > k:
        warns.append("%s: açık pozisyon %d > slot K=%d" % (name, len(pos), k))

# ---------------------------------------------------------------- dosya boyları
print("\n-- büyüyen kayıt dosyaları (öğrenme açıkken daha hızlı büyür)")
for fn in ("trade_memory.jsonl", "position_path.jsonl", "profitability_experiment_v1_2_events.jsonl"):
    try:
        print("   %-46s %9.2f MB" % (fn, os.path.getsize(os.path.join(st, fn)) / 1048576.0))
    except OSError:
        print("   %-46s yok" % fn)

# ---------------------------------------------------------------- geri alma tetikleri
print("\n-- GERİ ALMA TETİKLERİ: (1) hwm_mb > %90 × MemoryMax  (2) tur > 35 dk  (3) Box kaçan bar > 0/saat")
res = []
mm = int(memmax) if str(memmax).isdigit() else None
hwm = fl((lm.get("memory") or {}).get("hwm_mb")) if isinstance(lm.get("memory"), dict) else None
if mm and hwm is not None:
    thr = 0.9 * mm / 1048576.0
    res.append(("bellek", hwm > thr, "hwm_mb %.0f MB / eşik %.0f MB (MemoryMax %.0f MB)" % (hwm, thr, mm / 1048576.0)))
else:
    res.append(("bellek", None, "ölçülemedi (hwm_mb=%s, MemoryMax=%s)" % (hwm, memmax or "?")))
tours = [fl(x) for x in tours_s.split()]
tours = [x for x in tours if x is not None]
if not tours and fl(h.get("seconds")) is not None and h_at is not None and (rs is None or h_at >= rs):
    tours.append(fl(h.get("seconds")))          # günlükte tur satırı yoksa: health.json'daki son tur
if tours:
    worst = max(tours)
    res.append(("tur süresi", worst > 2100, "en uzun tur %.1f dk (%d tur ölçüldü; son %.1f dk)"
                % (worst / 60.0, len(tours), tours[-1] / 60.0)))
else:
    res.append(("tur süresi", None, "yeniden başlatmadan beri biten tur yok"))
bt = rj(os.path.join(st, "box_timer.json"))
if unreadable(bt):
    warns.append("box_timer.json okunamadı (%s)" % bt["__err__"])
bt = bt if isinstance(bt, dict) and not unreadable(bt) else {}
le = bt.get("last_eval") if isinstance(bt.get("last_eval"), dict) else {}
le_at = epoch(le.get("at"))
missed = bt.get("missed_bars")
print("   Box zamanlayıcısı: toplam kaçan bar=%s  son değerlendirme %s (%s; önceki kaçan=%s)  hata=%s"
      % (missed, le.get("bar_open"), ago(le_at), le.get("missed_before"), bt.get("errors")))
sample = None
if isinstance(missed, int) and le_at is not None and (rs is None or le_at >= rs):
    samples = []
    try:
        with open(boxs, encoding="utf-8") as fh:
            for ln in fh:
                p = ln.split()
                if len(p) == 3 and all(x.lstrip("-").isdigit() for x in p):
                    samples.append(tuple(int(x) for x in p))
    except OSError:
        pass
    ref = None
    for e, m, la in samples:          # en yeni, ≥ 50 dk önceki, yeniden başlatma sonrası örnek
        if e <= now - 3000 and (rs is None or la >= rs):
            ref = (e, m, la)
    stalled = (now - le_at) > 900
    if ref is not None:
        d = missed - ref[1]
        hrs = (now - ref[0]) / 3600.0
        res.append(("Box kaçan bar", d > 0 or stalled, "%+d kaçan bar / %.1f saat = %.2f/saat%s"
                    % (d, hrs, d / hrs, "; SON DEĞERLENDİRME %.0f dk ÖNCE (zamanlayıcı durmuş olabilir)"
                       % ((now - le_at) / 60) if stalled else "")))
    elif stalled:
        res.append(("Box kaçan bar", True, "son değerlendirme %.0f dk önce — zamanlayıcı durmuş olabilir"
                    % ((now - le_at) / 60)))
    else:
        res.append(("Box kaçan bar", None, "ölçüm örneği kaydedildi; saatlik oran için --check'i ≥ 1 saat sonra "
                                           "yeniden çalıştırın"))
    sample = "%d %d %d" % (int(now), missed, int(le_at))
else:
    res.append(("Box kaçan bar", None, "yeniden başlatmadan sonra henüz değerlendirme yok (zamanlayıcı ilk turla "
                                       "başlar; WFO + tur 1 saati aşabilir)"))
fired = [r for r in res if r[1] is True]
for name, f, txt in res:
    print("   %-13s %-14s %s" % ("[TETİKLENDİ]" if f is True else ("[tamam]" if f is False else "[ölçülemedi]"), name, txt))
for w in warns:
    print("   UYARI: %s" % w)
if fired:
    print("\n   >>> GERİ ALMA TETİĞİ: %s. Bu --check çıktısını bana iletin. Acil durumda öğrenme modunu kapatın:"
          % ", ".join(r[0] for r in fired))
    print("       config.yaml → learning_mode.enabled: false ; sudo systemctl restart tradingbot-worker tradingbot-dashboard")
else:
    print("   tetiklenen yok")
if sample:
    print("BOXSAMPLE " + sample)
PY
)" || { printf '%s\n' "$out"; echo "   öğrenme raporu okunamadı"; return 0; }
  printf '%s\n' "$out" | grep -v '^BOXSAMPLE ' || true
  local s; s="$(printf '%s\n' "$out" | sed -n 's/^BOXSAMPLE //p' | tail -n 1 || true)"
  if [[ -n "$s" && -f "$MARK_RESTART" ]]; then echo "$s" >> "$BOX_SAMPLES" 2>/dev/null || true; fi
}

# --check (2026-09-29): ORTAK DENEYİM KATMANI durumu (status.json + dosya boyları + defter başına 24 sa kapsam), son
# rapor taraması, karşı-olgusal NET / BRÜT (defter başına, etiket sürümleri), Box tek seferlik gecikme, canlı muhasebe
# düzeltmesinin izleri, yedek notu ve YALNIZ KATMAN geri alma tetikleri. Salt okunur (yalnız `shared-experience-status`
# alt süreci; o da salt okur).
xp_report() {
  local rs_epoch="" rs_human="" pre_hwm="" interval="" main_pre="" main_skip="" bk=""
  if [[ -r "$MARK_RESTART" ]]; then
    rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; rs_human="$(sed -n 2p "$MARK_RESTART" || true)"
  fi
  [[ -r "$MARK_PRE_HWM" ]] && pre_hwm="$(head -n 1 "$MARK_PRE_HWM" || true)"
  interval="$(systemctl show "$WORKER" -p ExecStart --value 2>/dev/null | grep -oE -- '--interval[ =][0-9]+' | grep -oE '[0-9]+' | head -n 1 || true)"
  # ana defterin olayları yalnız günlüğe yazılır ("ana defter <sembol> <tür> <neden> {…}"); yeniden başlatmadan beri
  main_pre="$(journalctl -u "$WORKER" --since "${rs_human:--24h}" -o cat --no-pager 2>/dev/null | grep -acE 'ana defter [^ ]+ BAR_PRE_MOVE_STOP' || true)"
  main_skip="$(journalctl -u "$WORKER" --since "${rs_human:--24h}" -o cat --no-pager 2>/dev/null | grep -acE 'ana defter [^ ]+ BAR_SKIPPED OPENED_BEFORE_STOP_MOVE' || true)"
  bk="$(journalctl -u tradingbot-backup.service --since "-26h" -o cat --no-pager 2>/dev/null | grep -aoE '"skipped_xp_segments": *[0-9]+' | tail -n 1 | grep -oE '[0-9]+$' || true)"
  if ! py - "$STATE" "$APP" "$rs_epoch" "$pre_hwm" "${interval:-15}" "${main_pre:-0}" "${main_skip:-0}" "${bk:-}" <<'PY'
import datetime as dt
import json
import os
import subprocess
import sys
import time

st, app, rs_epoch, pre_hwm, interval_s, main_pre, main_skip, bk_skip = sys.argv[1:9]
now = time.time()
XP = os.path.join(st, "shared_experience")


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def unreadable(d):
    return isinstance(d, dict) and "__err__" in d


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def mb(n):
    return "%.2f MB" % (n / 1048576.0) if isinstance(n, (int, float)) else "?"


def size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return None


rs = fl(rs_epoch)
alarms, warns, trig = [], [], []
h = rj(os.path.join(st, "health.json"))
h = h if isinstance(h, dict) and not unreadable(h) else {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
except Exception:  # noqa: BLE001
    raw = {}
xp_cfg = raw.get("shared_experience")
xp_env = os.environ.get("TRADINGBOT_SHARED_EXPERIENCE")
xp_on = isinstance(xp_cfg, dict) and xp_cfg.get("enabled") is True \
    and str(xp_cfg.get("mode") or "").upper() == "RECORD" and not (xp_env or "").strip()
period = max(1.0, fl(interval_s) or 15.0) * 60.0 + max(0.0, fl(h.get("seconds")) or 0.0)   # tur aralığı + son tur

# ---------------------------------------------------------------- ortak deneyim: status.json
print("-- ortak deneyim katmanı (yalnız KAYIT; karar DEĞİŞMEZ): config=%s%s" % (
    json.dumps(xp_cfg, ensure_ascii=False), ("  !!! servis ortamı TRADINGBOT_SHARED_EXPERIENCE=%s" % xp_env) if xp_env else ""))
hx = h.get("shared_experience")
print("   health.json: %s" % (json.dumps(hx, ensure_ascii=False) if hx is not None else "anahtar yok (katman kapalı ya da "
                                                                                     "yeni worker ilk turunu bitirmedi)"))
s = rj(os.path.join(XP, "status.json"))
bf_done = None
p95 = None
store = {}
if unreadable(s):
    alarms.append("status.json OKUNAMADI (%s)" % s["__err__"])
    s = None
if not isinstance(s, dict):
    print("   status.json YOK — ilk RECORD turu henüz bitmedi (yeni worker önce WFO + tur koşar) ya da katman kapalı")
    if xp_on and rs is not None and now - rs > 2 * period + 3600:
        warns.append("katman açık ama yeniden başlatmadan %.0f dk sonra status.json yok (health.json'da da %s)"
                     % ((now - rs) / 60.0, "yok" if hx is None else "var"))
else:
    state = str(s.get("state"))
    c = s.get("counters") if isinstance(s.get("counters"), dict) else {}
    store = s.get("store") if isinstance(s.get("store"), dict) else {}
    br = s.get("breaker") if isinstance(s.get("breaker"), dict) else {}
    ls = epoch(s.get("last_step_at"))
    age = (now - ls) if ls is not None else None
    tag = "OK" if state == "OK" else "!!! ALARM"
    print("   durum: %s [%s]  son adım %s (%s önce; eşik 2 tur ≈ %.0f dk)  adım %s  son hata %s"
          % (state, tag, s.get("last_step_at"), ("%.0f dk" % (age / 60.0)) if age is not None else "?",
             2 * period / 60.0, s.get("steps"), s.get("last_error_code") or "—"))
    if state.startswith("SUSPENDED"):
        alarms.append("katman ASKIDA (%s): mod PAPER değil ya da canlı emir yolu açık — satır yazılmıyor. "
                      "state/mode.json ve worker günlüğüne bakın" % state)
    elif state == "DISABLED_BY_BREAKER":
        alarms.append("katman DEVRE KESİCİ ile kapandı (son hata %s)" % s.get("last_error_code"))
    elif state == "DEGRADED":
        alarms.append("katman DEGRADED (disk tavanı %110: yazım durdu)")
    elif state != "OK":
        alarms.append("katman durumu beklenmeyen: %s" % state)
    if age is not None and age > 2 * period:
        hat = epoch(h.get("at"))
        warns.append("son katman adımı %.0f dk önce (> 2 tur ≈ %.0f dk)%s" % (
            age / 60.0, 2 * period / 60.0, "; health.json daha yeni → worker turluyor ama katman adımı yok"
            if hat is not None and ls is not None and hat - ls > period else "; worker da turlamıyor olabilir"))
    print("   satırlar: toplam %s · tür %s · son adım %s" % (
        c.get("rows_total"), json.dumps(c.get("rows_by_kind") or {}, ensure_ascii=False), s.get("rows_last")))
    print("   defter başına satır: %s" % json.dumps(c.get("rows_by_book") or {}, ensure_ascii=False, sort_keys=True))
    p95 = fl(s.get("step_ms_p95"))
    print("   adım süresi ms: p50 %s · p95 %s · son %s   taslak %s %s" % (
        s.get("step_ms_p50"), s.get("step_ms_p95"), s.get("step_ms_last"), s.get("drafts"),
        json.dumps(s.get("drafts_by") or {}, ensure_ascii=False)))
    print("   devre kesici: %s (art arda hata %s/%s · art arda aşım %s/%s)" % (
        "AÇILDI" if br.get("tripped") else "kapalı", br.get("consecutive_errors"), br.get("errors_limit"),
        br.get("consecutive_overruns"), br.get("overruns_limit")))
    names = ("errors_total", "lock_busy_skips", "budget_hits", "row_cap_hits", "budget_overruns", "write_errors",
             "io_retry_steps", "rejected_rows", "duplicate_rows", "cursor_write_errors", "cursor_deferred", "rotate_errors",
             "spot_excluded", "spot_real_excluded", "spot_real_closed_seen", "main_entry_no_sigkey", "cf_reappeared",
             "pending_entries_restored", "legacy_gross_cf", "unknown_label_version")
    line = "   sayaçlar:"
    for n in names:
        item = " %s=%s" % (n, c.get(n))
        if len(line) + len(item) > 118:
            print(line)
            line = "            "
        line += item
    print(line)
    print("   kaybolan (durum başına): %s   anlık görüntü karışımı: %s" % (
        json.dumps(c.get("vanished_by_status") or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(c.get("snapshot_status_mix") or {}, ensure_ascii=False, sort_keys=True)))
    sc = store.get("counters") if isinstance(store.get("counters"), dict) else {}
    print("   depo: durum %s · disk %s / tavan %s (oran %s) · sıcak %s satır (döngü sonrası %s) · döngü %s · arşiv hatası %s%s"
          " · rollback_failed %s · io_error %s" % (
              store.get("state"), mb(store.get("disk_bytes")), mb(store.get("cap_bytes")), store.get("fraction"),
              store.get("hot_lines"), store.get("hot_keep_lines"), store.get("rotations"), store.get("archive_errors"),
              (" (son: %s)" % store.get("last_archive_error")) if store.get("last_archive_error") else "",
              sc.get("rollback_failed"), sc.get("io_error")))
    if store.get("state") == "DEGRADED":
        alarms.append("depo DEGRADED (disk tavanı %110)")
    elif store.get("state") in ("WARN", "THINNING"):
        warns.append("depo disk baskısı %s (oran %s)" % (store.get("state"), store.get("fraction")))
    for n in ("errors_total", "write_errors", "rotate_errors", "cursor_write_errors", "unknown_label_version"):
        if isinstance(c.get(n), int) and c.get(n) > 0:
            warns.append("katman sayacı %s = %s" % (n, c.get(n)))
    if isinstance(sc.get("rollback_failed"), int) and sc.get("rollback_failed") > 0:
        warns.append("depo rollback_failed = %s (yarım yazım geri kesilemedi)" % sc.get("rollback_failed"))
    bf = s.get("backfill") if isinstance(s.get("backfill"), dict) else {}
    bf_done = bool(bf) and all(isinstance(v, dict) and v.get("done") is True for v in bf.values())
    print("   geri doldurma: %s (%s)" % ("BİTTİ" if bf_done else "sürüyor — ilk ~180 tur ≈ 18 sa, adım p95 ≈ 1 sn beklenir",
                                       ", ".join("%s=%s" % (k, "✓" if (v or {}).get("done") else "…")
                                                 for k, v in sorted(bf.items())) or "defter yok"))

# ---------------------------------------------------------------- dosya boyları
print("\n-- ortak deneyim dosya boyları (beklenen)")
for fn, exp, lim in (("experience.jsonl", "≤ ~15 MB", 15 * 1048576), ("cursor.json", "≤ ~3,5 MB (tam listelerde)", 3.5 * 1048576),
                     ("status.json", "~3 KB", 30 * 1024)):
    n = size(os.path.join(XP, fn))
    print("   %-26s %12s   (%s)" % (fn, mb(n) if n is not None else "yok", exp))
    if n is not None and n > 1.3 * lim:
        warns.append("%s %s — beklenenden büyük (%s)" % (fn, mb(n), exp))
man_p = os.path.join(XP, "archive", "manifest.json")
man = rj(man_p)
seg_dir = os.path.join(XP, "archive", "segments")
seg_n, seg_b = 0, 0
try:
    for e in os.scandir(seg_dir):
        if e.is_file():
            seg_n += 1
            seg_b += e.stat().st_size
except OSError:
    pass
born = None
cur = rj(os.path.join(XP, "cursor.json"))
if isinstance(cur, dict) and not unreadable(cur) and isinstance(cur.get("born_ms"), int):
    born = cur["born_ms"] / 1000.0
days = ((now - born) / 86400.0) if born else None
print("   %-26s %12s   segment %d dosya / %s%s (beklenen: sıcak dosya dolunca ≈ 1 segment/gün)" % (
    "archive/manifest.json", mb(size(man_p)) if size(man_p) is not None else "yok", seg_n, mb(seg_b),
    ("; manifest %s segment" % len(man.get("segments") or [])) if isinstance(man, dict) and not unreadable(man) else ""))
if days is not None and days >= 1.0:
    per_day = seg_n / days
    print("   doğumdan beri %.1f gün → %.1f segment/gün (ilk gün geri doldurma fazladan üretebilir)" % (days, per_day))
    if per_day > 24:
        warns.append("arşiv %.1f segment/gün (≈ 1 beklenir; tur başına döngü?)" % per_day)

# ---------------------------------------------------------------- defter başına 24 sa kapsam (CLI, salt okur)
print("\n-- defter başına kapsam (python -m tradingbot shared-experience-status --json; yazım anına göre)")
doc = None
try:
    r = subprocess.run(["nice", "-n", "10", sys.executable, "-m", "tradingbot", "shared-experience-status", "--json"],
                       capture_output=True, text=True, timeout=300, cwd=app)
    txt = r.stdout
    i = txt.find("{")
    doc = json.loads(txt[i:]) if r.returncode == 0 and i >= 0 else None
    if doc is None:
        print("   komut çalışmadı (çıkış %s): %s" % (r.returncode, (r.stderr or r.stdout).strip()[-300:]))
except Exception as exc:  # noqa: BLE001
    print("   komut çalışmadı: %s: %s" % (type(exc).__name__, exc))
if isinstance(doc, dict):
    if not doc.get("available"):
        print("   %s" % (doc.get("note_tr") or "depo yok"))
    cov = doc.get("coverage") if isinstance(doc.get("coverage"), dict) else {}
    c24 = cov.get("24h") if isinstance(cov.get("24h"), dict) else {}
    c7 = cov.get("7d") if isinstance(cov.get("7d"), dict) else {}
    nod = set(doc.get("no_data_24h") or [])
    if c24:
        print("   %-32s 24 sa: satır/giriş/kapanış/olsaydı yeni/olsaydı etiket | 7 gün satır" % "defter")
    for b in c24:
        a, w = c24.get(b) or {}, c7.get(b) or {}
        print("   %-32s %6s/%5s/%5s/%6s/%6s | %7s%s" % (b, a.get("rows"), a.get("entries"), a.get("outcomes"), a.get("cf_new"),
                                                    a.get("cf_labelled"), w.get("rows"), "   VERİ YOK" if b in nod else ""))
    if nod:
        print("   VERİ YOK (24 sa): %s — C4/D4 gibi seyrek sinyalli defterlerde beklenebilir" % ", ".join(sorted(nod)))

# ---------------------------------------------------------------- son rapor taraması
sm = rj(os.path.join(XP, "report_summary.json"))
gen = sm.get("generated_at") if isinstance(sm, dict) and not unreadable(sm) else None
print("\n-- son rapor taraması (report_summary.json): %s" % (gen or "yok (elle)"))
if not gen:
    import pwd
    print("   Otomatik yazan YOK. Elle (≈ 5 sn, ≈ 140 MB; salt okur):")
    print("   sudo -u %s bash -c 'cd %s && TRADINGBOT_STATE_DIR=%s nice -n 19 %s -m tradingbot shared-experience-report"
          " --summary-out %s'" % (pwd.getpwuid(os.getuid()).pw_name, app, st, sys.executable,
                                  os.path.join(XP, "report_summary.json")))

# ---------------------------------------------------------------- karşı-olgusal net / brüt (defter başına)
print("\n-- karşı-olgusal NET / BRÜT (defter başına; P&L'e GİRMEZ) — net ortalama yalnız net etiketlilerden")
try:
    from tradingbot.learning_cf import net_stats as _net_stats
except Exception:  # noqa: BLE001 — eski kod: aynı formül
    _net_stats = None


def net_stats(outs):
    if _net_stats is not None:
        return _net_stats(outs)
    g_, n_, c_, fin, amb, lab = [], [], [], 0, 0, 0
    for o in outs:
        if not isinstance(o, dict):
            continue
        lab += 1
        g, n = fl(o.get("r_multiple")), fl(o.get("r_net"))
        if g is not None:
            g_.append(g)
        if n is not None:
            n_.append(n)
            cc = fl(o.get("cost_r"))
            if cc is not None:
                c_.append(cc)
            fin += o.get("funding_complete") is False
            amb += (o.get("intrabar_ambiguous_bars") or 0) > 0

    def m(xs):
        return round(sum(xs) / len(xs), 4) if xs else None
    return {"n_net": len(n_), "mean_r_net": m(n_), "mean_r_gross": m(g_), "mean_cost_r": m(c_), "n_gross_only": lab - len(n_),
            "n_net_funding_incomplete": fin, "n_net_intrabar_ambiguous": amb}


LV = {"cf_label_v3": "v3", "cf_label_v2": "v2", "cf_label_v1": "v1", "cf_label_v1c": "v1c", None: "none"}
srcs = [("main", os.path.join(st, "shadow_book.json"), "main", os.path.join(st, "futures_ledger.json"), None, None)]
idx = rj(os.path.join(st, "strategy_paper_index.json"))
for b in ((idx or {}).get("books") or []) if isinstance(idx, dict) and not unreadable(idx) else []:
    key = str((b or {}).get("key") or "") if isinstance(b, dict) else ""
    if key and all(ch.isalnum() or ch == "_" for ch in key):
        srcs.append((key, os.path.join(st, key, "counterfactual_trades.json"), None, os.path.join(st, key, "futures_ledger.json"),
                     os.path.join(st, str(b.get("summary_file") or key + ".json")), "data_events_recent"))
if os.path.isdir(os.path.join(st, "pattern_trader")):
    srcs.append(("pattern_trader", os.path.join(st, "pattern_trader", "counterfactual_trades.json"), None,
                 os.path.join(st, "pattern_trader", "futures_ledger.json"), os.path.join(st, "pattern_trader.json"),
                 "events_recent"))
print("   %-32s %5s %8s %8s %7s %6s %5s %5s  etiket v3/v2/v1/v1c/none" % (
    "defter", "n_net", "ort.net", "ort.brüt", "maliyet", "brüt*", "fund-", "belir"))
for key, cfp, only, _led, _sum, _evk in srcs:
    d = rj(cfp)
    if d is None:
        continue
    if unreadable(d):
        print("   %-32s OKUNAMADI (%s)" % (key, d["__err__"]))
        warns.append("%s: karşı-olgusal dosyası okunamadı" % key)
        continue
    rows = [t for t in (d.get("trades") or []) if isinstance(t, dict) and (only is None or t.get("book") == only)]
    outs = [t.get("outcome") for t in rows if isinstance(t.get("outcome"), dict)]
    ns = net_stats(outs)
    cnt = {"v3": 0, "v2": 0, "v1": 0, "v1c": 0, "none": 0, "diğer": 0}
    new_unlabelled = 0
    for t in rows:
        o = t.get("outcome")
        if not isinstance(o, dict):
            continue
        lv = LV.get(o.get("label_version"), "diğer")
        cnt[lv] += 1
        la = epoch(t.get("labeled_at"))
        if lv == "none" and rs is not None and la is not None and la >= rs:
            new_unlabelled += 1
    print("   %-32s %5s %8s %8s %7s %6s %5s %5s  %d/%d/%d/%d/%d%s" % (
        key, ns.get("n_net"), ns.get("mean_r_net"), ns.get("mean_r_gross"), ns.get("mean_cost_r"), ns.get("n_gross_only"),
        ns.get("n_net_funding_incomplete"), ns.get("n_net_intrabar_ambiguous"), cnt["v3"], cnt["v2"], cnt["v1"], cnt["v1c"],
        cnt["none"], (" (+%d diğer)" % cnt["diğer"]) if cnt["diğer"] else ""))
    if new_unlabelled:
        warns.append("%s: yeniden başlatmadan sonra %d yeni etiket label_version TAŞIMIYOR (net etiket yolu çalışmıyor?)"
                     % (key, new_unlabelled))
print("   (ort.brüt = bütün etiketlilerin brüt ort.; brüt* = net'i olmayan etiketli (v1/v1c/hata); fund- = funding eksik net;"
      " belir = bar içi sırası belirsiz net. v1c yaklaşık net ortalamaya GİRMEZ; karnede n_approx_v1c)")

# ---------------------------------------------------------------- Box: beklenen tek seferlik gecikme
bt = rj(os.path.join(st, "box_timer.json"))
le = bt.get("last_eval") if isinstance(bt, dict) and not unreadable(bt) and isinstance(bt.get("last_eval"), dict) else {}
print("\n-- Box son değerlendirme süresi: %s sn (%s) — ilk ~3 Box geçişinde ≈ +1,5 sn BEKLENİR (tembel yeniden etiketleme,"
      " 400 v1 kayıt: 1,1 → 1,5 sn Box kilidi altında); sonra eski düzeyine döner" % (le.get("seconds"), le.get("at")))

# ---------------------------------------------------------------- canlı muhasebe düzeltmesi (stop taşıma)
print("\n-- canlı muhasebe düzeltmesi (stop taşıma; öğrenme kapalıyken de PAPER muhasebesini değiştirir — tasarım gereği)")
print("   %-32s %9s %9s %11s" % ("defter", "PRE_MOVE", "ATLANAN", "kapanış PRE"))
for key, _cfp, _only, ledp, sump, evk in srcs:
    n_pre = n_skip = 0
    if key == "main":
        n_pre, n_skip = int(main_pre or 0), int(main_skip or 0)       # ana defter olayları günlükte
    elif sump:
        sd = rj(sump)
        for e in ((sd or {}).get(evk) or []) if isinstance(sd, dict) and not unreadable(sd) else []:
            if not isinstance(e, dict):
                continue
            at = epoch(e.get("at"))
            if rs is not None and (at is None or at < rs):
                continue
            if e.get("kind") == "BAR_PRE_MOVE_STOP":
                n_pre += 1
            elif e.get("kind") == "BAR_SKIPPED" and e.get("reason") == "OPENED_BEFORE_STOP_MOVE":
                n_skip += 1
    led = rj(ledp)
    n_close = 0
    for r in ((led or {}).get("history") or []) if isinstance(led, dict) and not unreadable(led) else []:
        ef = ((r.get("features") or {}).get("exit_fill") or {}) if isinstance(r, dict) and isinstance(r.get("features"), dict) else {}
        if isinstance(ef, dict) and ef.get("stop_in_force") == "PRE_MOVE":
            ca = epoch(r.get("closed_at"))
            if rs is None or (ca is not None and ca >= rs):
                n_close += 1
    print("   %-32s %9d %9d %11d" % (key, n_pre, n_skip, n_close))
print("   (PRE_MOVE = BAR_PRE_MOVE_STOP olayı; ATLANAN = BAR_SKIPPED/OPENED_BEFORE_STOP_MOVE; kapanış PRE = exit_fill."
      "stop_in_force=PRE_MOVE; yeniden başlatmadan beri. Kâğıt defterlerde son 30 olay tutulur; ana defter günlükten)")

# ---------------------------------------------------------------- yedekler
print("\n-- yedekler: saatlik yedek shared_experience/archive/segments'ı YALNIZ UTC 00'da taşır (son saatlik yedek "
      "skipped_xp_segments=%s). Başka saatin yedeğinden geri yüklerken segmentleri en yeni UTC-00 (ya da elle) yedekten "
      "kopyalayın; segment dosyaları değişmez." % (bk_skip or "?"))

# ---------------------------------------------------------------- bellek (katman ≈ 25 MB kalıcı)
lm = h.get("learning_mode") if isinstance(h.get("learning_mode"), dict) else {}
hwm = fl((lm.get("memory") or {}).get("hwm_mb")) if isinstance(lm.get("memory"), dict) else None
ph = fl(pre_hwm)
if hwm is not None and ph is not None:
    print("\n-- bellek: hwm_mb %.0f (dağıtım öncesi %.0f → %+.0f MB; katman ≈ 25 MB kalıcı, eşik +50 MB; düzenekte +2,5 MB)"
          % (hwm, ph, hwm - ph))
    if hwm - ph >= 50:
        warns.append("hwm_mb dağıtım öncesinden %+.0f MB (≥ +50 MB)" % (hwm - ph))
else:
    print("\n-- bellek: hwm_mb %s · dağıtım öncesi %s (karşılaştırılamadı)" % (hwm, ph))

# ---------------------------------------------------------------- YALNIZ KATMAN geri alma tetikleri
print("\n-- KATMAN GERİ ALMA TETİKLERİ (yalnız ortak deneyim; öğrenme modu GERİ ALINMAZ): (1) devre kesici  "
      "(2) adım p95 > 1500 ms (geri doldurma bittikten sonra)  (3) depo DEGRADED")
res = []
if not isinstance(s, dict):
    res = [("devre kesici", None, "status.json yok"), ("adım p95", None, "status.json yok"), ("depo", None, "status.json yok")]
else:
    tripped = str(s.get("state")) == "DISABLED_BY_BREAKER" or bool((s.get("breaker") or {}).get("tripped"))
    res.append(("devre kesici", tripped, "durum %s" % s.get("state")))
    if p95 is None:
        res.append(("adım p95", None, "ölçüm yok"))
    elif not bf_done:
        res.append(("adım p95", None, "p95 %.0f ms — geri doldurma sürüyor (≈ 1000 ms beklenir; tetik geri doldurma bitince)" % p95))
    else:
        res.append(("adım p95", p95 > 1500, "p95 %.0f ms (kararlı < 1000 ms beklenir; tetik > 1500 ms)" % p95))
        if 1000 <= p95 <= 1500:
            warns.append("katman adım p95 %.0f ms ≥ 1000 ms (kararlı düzeyin üstünde)" % p95)
    deg = str(s.get("state")) == "DEGRADED" or store.get("state") == "DEGRADED"
    res.append(("depo", deg, "depo %s · katman %s" % (store.get("state"), s.get("state"))))
for name, f, txt in res:
    print("   %-13s %-14s %s" % ("[TETİKLENDİ]" if f is True else ("[tamam]" if f is False else "[ölçülemedi]"), name, txt))
for a in alarms:
    print("   ALARM: %s" % a)
for w in warns:
    print("   UYARI: %s" % w)
fired = [r for r in res if r[1] is True]
if fired:
    print("\n   >>> KATMAN GERİ ALMA TETİĞİ: %s. Katmanı kapatın (öğrenme modu ve net etiket AYNEN sürer):"
          % ", ".join(r[0] for r in fired))
    print("       config.yaml → shared_experience: {enabled: false, mode: RECORD}  (ya da drop-in "
          "Environment=TRADINGBOT_SHARED_EXPERIENCE=off) ; sudo systemctl restart tradingbot-worker")
    print("       state/shared_experience/ kalır ve zararsızdır. Bu --check çıktısını bana iletin.")
else:
    print("   tetiklenen yok")
PY
  then
    echo "   ortak deneyim raporu okunamadı"
  fi
}

# Kodu dağıtım ÖNCESİ duruma döndürür: worker DURUR → (yalnız eski kod öğrenme modunu TANIMIYORSA: Formasyon öğrenme
# planları temizlenir, YENİ kod hâlâ yerindeyken) → aynı dal adı + önceki commit → requirements değiştiyse pip → worker
# başlar, panel yeniden başlar. Yedeklere ve defterlere dokunmaz. Bu sürümün ön koşulu c0b8c94 öğrenme modunu tanıdığı için
# plan temizliği ÇALIŞMAZ (2026-09-29): eski kod öğrenme planlarını kendisi yönetir; temizlik onları gereksiz yere iptal
# ederdi. Temizlik gerekip başarısızsa (çıkış 2: plans.json okunamadı/bozuk) DURUR: worker kapalı, yeni kod yerinde kalır.
REQ_CHANGED=""
PREP_NEEDED=""
revert_code() {
  trap - ERR
  trap '' INT TERM              # geri alma yarıda kesilmez (ikinci Ctrl+C dahil); ÖNCE iş, SONRA ekrana yazı
  systemctl stop "$WORKER" || true
  if [[ -n "$PREP_NEEDED" && -f "$APP/scripts/learning_mode_rollback_prep.py" ]]; then
    local rc=0
    svc_run "$APP" "$VENV/bin/python" scripts/learning_mode_rollback_prep.py --state "$STATE" || rc=$?
    if (( rc != 0 )); then
      cat >&2 <<EOF

DUR: GERİ ALMA DURDURULDU — öğrenme planı temizliği başarısız (çıkış $rc; 2 = pattern_trader/plans.json okunamadı/bozuk).
  Durum: worker DURDURULDU; kod hâlâ ${T7} (yeni); eski kod BAŞLATILMADI; yedek ve defterlere dokunulmadı.
  Yapılacak (plans.json'a elle bakıldıktan sonra):
    sudo -u $SVC_USER $VENV/bin/python $APP/scripts/learning_mode_rollback_prep.py --state $STATE
    sudo -u $SVC_USER git -C $APP checkout -q -B $BRANCH_NOW ${PREV:0:7}     # (dal yoksa: checkout ${PREV:0:7})
    sudo systemctl start $WORKER; sudo systemctl restart $DASH
  Ya da yeni kodla sürdürmek için: sudo systemctl start $WORKER. Bu çıktıyı bana iletin.
EOF
      exit 3
    fi
  else
    echo "   (öğrenme planı temizliği gerekmiyor: ${PREV:0:7} öğrenme modunu tanıyor)" || true
  fi
  if [[ "$BRANCH_NOW" != "detached" ]]; then
    # dalı yalnız bu betiğin az önce yaptığı ileri sarmadan geri alır (dal önceden tam olarak PREV'deydi)
    gitc checkout -q -B "$BRANCH_NOW" "$PREV" || gitc checkout -q "$PREV" \
      || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  else
    gitc checkout -q "$PREV" || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  fi
  if [[ -n "$REQ_CHANGED" ]]; then
    "$VENV/bin/pip" install -q -r "$APP/requirements.txt" >/dev/null 2>&1 || true
    chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
  fi
  systemctl start "$WORKER" || true
  systemctl restart "$DASH" || true
  echo "   GERİ ALINDI: kod ${PREV:0:7}; worker başlatıldı, panel yeniden başlatıldı (açıldıysa state/shared_experience ve" \
       "varsa net'e çevrilmiş karşı-olgusal dosyalar kalır — eski kod onları tolere eder; .pre-cf-net-<UTC> yedekleri" \
       "yanlarında)" || true
}

# KARŞI-OLGUSAL NET DOLGU (2026-09-29): worker DURMUŞKEN, yeni kod yerindeyken, yeniden başlatmadan ÖNCE; servis
# kullanıcısı + servis ortamıyla (TRADINGBOT_DATA/…: filtre önbelleği ve yollar worker'la aynı). Önce kuru çalışma, sonra
# --apply. Çıkış 2 (state/config okunamadı) ya da başka hata → 1 döner (çağıran kodu geri alır). Mum çekimi zaman aşımına
# düşerse ya da mumlar alınamazsa (ağ) → --no-fetch --apply (kapalı-biçim yaklaşık v1c; net ortalamasına girmez, sonraki
# mumlu koşu v3'e yükseltir).
cf_table() {        # $1: betiğin --json raporu → defter başına tablo
  py - "$1" <<'PY'
import json
import sys
try:
    rep = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print("   rapor okunamadı: %s" % exc)
    sys.exit(0)
print("   %-30s %5s %5s %5s %6s %6s %5s %9s %9s %9s  %s" % ("defter", "aday", "net", "v1c", "v1c→v3", "v1c=", "yok",
                                                         "brüt ort", "net ort", "v1c≈net", "yazım"))
bad = []
for key, b in (rep.get("books") or {}).items():
    if "error" in b:
        print("   %-30s OKUNAMADI: %s" % (key, b["error"]))
        bad.append(key)
        continue
    print("   %-30s %5s %5s %5s %6s %6s %5s %9s %9s %9s  %s" % (
        key, b.get("candidates"), b.get("net"), b.get("v1c"), b.get("v1c_upgraded"), b.get("v1c_kept"), b.get("none"),
        b.get("net_mean_r_gross"), b.get("net_mean_r_net"), b.get("v1c_mean_r_net_approx"), b.get("write")))
    if str(b.get("write") or "").startswith("SKIPPED"):
        bad.append(key)
if bad:
    print("   UYARI: %s — dosya okunamadı ya da diskte değişti (yazılmadı; eski etiketler aynen kaldı, yeni kod tembel "
          "doldurur). Worker durmuşken değişiyorsa bana haber verin." % ", ".join(bad))
PY
}
CF_OUT=""
cf_run() {          # $1: json yolu, $2...: ek argümanlar. Çıktı canlı basılır ve CF_OUT'a alınır; dönüş = betiğin çıkış kodu
  local js="$1" rc=0; shift
  install -o "$SVC_USER" -g "$SVC_USER" -m 0640 /dev/null "$js"
  CF_TMP="$(mktemp /tmp/tb-deploy-cf.XXXXXX)"
  # (2026-09-29, sahte VPS) ALT KABUKTA + boru: işlev çağrısına bağlı `> dosya` yönlendirmesi, bu sırada gelen SIGTERM'in
  # tuzağını (on_abort → geri alma) da o dosyaya yazdırıyordu (geri alma yapılıyor ama ekranda/günlükte görünmüyordu).
  # pipefail: dönüş kodu dolgu betiğinin kodudur (tee/sed başarılıysa).
  ( trap - ERR; svc_run "$APP" timeout -k 30 "$CF_FETCH_TIMEOUT" "$VENV/bin/python" scripts/cf_backfill_net.py \
      --state "$STATE" --config "$APP/config.yaml" --json "$js" "$@" ) 2>&1 | tee "$CF_TMP" | sed -u 's/^/   | /' || rc=$?
  CF_OUT="$(cat "$CF_TMP")"; rm -f "$CF_TMP"; CF_TMP=""
  return "$rc"
}
cf_rc_why() { if [[ "$1" == 2 ]]; then printf ': state/config okunamadı'; fi; }
cf_backfill() {
  local rc=0 nofetch="" nfail=0
  echo "   (kuru çalışma: hiçbir dosyaya yazılmaz; mumlar borsadan, en çok ${CF_FETCH_TIMEOUT} sn)"
  cf_run "$CF_DRY_JSON" || rc=$?
  if (( rc == 124 || rc == 137 )); then
    echo "   !!! mum çekimi ${CF_FETCH_TIMEOUT} sn'de bitmedi (ağ?) → --no-fetch ile sürdürülüyor (yaklaşık v1c)"
    nofetch=1; rc=0
    cf_run "$CF_DRY_JSON" --no-fetch || rc=$?
  fi
  if (( rc != 0 )); then
    echo "   HATA  karşı-olgusal net dolgu kuru çalışması başarısız (çıkış $rc$(cf_rc_why "$rc"))"
    return 1
  fi
  cf_table "$CF_DRY_JSON"
  nfail="$(printf '%s\n' "$CF_OUT" | grep -c 'mumları alınamadı' || true)"
  if (( nfail > 0 )); then
    echo "   !!! kuru çalışmada $nfail sembol/dilim için mum alınamadı: o kayıtlar yaklaşık (v1c) yazılır (sonra mumla v3'e yükselir)"
  fi
  echo "   (uygula: her değişen dosya <dosya>.pre-cf-net-<UTC> olarak yedeklenir; diskte değişen dosya ATLANIR)"
  rc=0
  if [[ -n "$nofetch" ]]; then
    cf_run "$CF_APPLY_JSON" --no-fetch --apply || rc=$?
  else
    cf_run "$CF_APPLY_JSON" --apply || rc=$?
    nfail="$(printf '%s\n' "$CF_OUT" | grep -c 'mumları alınamadı' || true)"
    if (( rc == 124 || rc == 137 || (rc == 0 && nfail > 0) )); then
      echo "   !!! mum çekimi başarısız (çıkış $rc; alınamayan $nfail) → --no-fetch --apply (kalan eski etiketler yaklaşık v1c)"
      rc=0
      cf_run "$CF_APPLY_JSON" --no-fetch --apply || rc=$?
    fi
  fi
  if (( rc != 0 )); then
    echo "   HATA  karşı-olgusal net dolgu (--apply) başarısız (çıkış $rc$(cf_rc_why "$rc"))"
    return 1
  fi
  cf_table "$CF_APPLY_JSON"
  cat <<EOF
   Başka göç GEREKMEZ:
     * state/shared_experience/ ilk RECORD turunda dolar; imleç doğumu (born_ms) o turun başlangıcıdır. İlk ~180 tur
       (~18 sa) geri doldurmadır (adım p95 ≈ 1 sn).
     * Canlı stop taşıma düzeltmesi pozisyonların mevcut meta'sını (be_by_mfe, dolumlar) kullanır.
     * Süreç içi v1 → v3 yeniden etiketleme tembeldir: çağrı başına ≤ 400 kayıt, Box penceresi ~25 sa.
EOF
}

[[ $EUID -eq 0 ]] || die "sudo ile çalıştırın: sudo bash $0 ${MODE}"
[[ -d "$APP/.git" ]] || die "$APP bir git kopyası değil"
load_env

# ------------------------------------------------------------------ --detach: SSH'tan bağımsız ayrı görev + izleme
if [[ "$MODE" == "--detach" ]]; then
  UNIT="tb-deploy-$T7"
  if systemctl is-active --quiet "$UNIT"; then
    echo "dağıtım zaten çalışıyor ($UNIT). İzlemek için: sudo journalctl -u $UNIT -f -o cat"; exit 0
  fi
  START="$(date '+%Y-%m-%d %H:%M:%S')"
  systemd-run --unit="$UNIT" --collect --quiet --setenv=TRADINGBOT_BASE="$BASE" --setenv=TRADINGBOT_USER="$SVC_USER" \
    bash "$HERE/$(basename "${BASH_SOURCE[0]}")" deploy || die "ayrı görev başlatılamadı (systemd-run)"
  cat <<EOF
Dağıtım AYRI bir görev olarak başladı ($UNIT). Bu pencereyi ya da PC'yi kapatsanız da sürer.
  Ctrl+C yalnız İZLEMEYİ bırakır, dağıtımı ETKİLEMEZ.
  Sonra tekrar izlemek:  sudo journalctl -u $UNIT -f -o cat     (görev bitince: sudo tail -60 $LOGDIR/$T7-deploy.log)
  Dağıtımı durdurmak:    sudo systemctl stop $UNIT   (yeniden başlatmadan önceyse kod geri alınır)
EOF
  journalctl -u "$UNIT" -f -o cat --no-pager --since "$START" \
    > >(grep --line-buffered -vE '^ +root : |pam_unix\(sudo:session\)') & jp=$!
  sleep 3
  while systemctl is-active --quiet "$UNIT"; do sleep 3; done
  sleep 2; kill "$jp" 2>/dev/null || true
  echo; echo "görev bitti. Durum için: sudo bash $0 --check"
  exit 0
fi

# ------------------------------------------------------------------ --check: dağıtım sonrası durum (state'e dokunmaz)
if [[ "$MODE" == "--check" ]]; then
  say "kod"
  echo "   HEAD $(gitc rev-parse --short HEAD) ($(gitc symbolic-ref --quiet --short HEAD || echo detached)) — hedef $T7" \
       "$([[ -r "$LOGDIR/$T7-prev-commit.txt" ]] && echo "— dağıtım öncesi $(head -c 7 "$LOGDIR/$T7-prev-commit.txt")")"
  say "servisler"
  for u in "$WORKER" "$DASH"; do
    echo "   $u: $(systemctl is-active "$u" || true), yeniden başlama sayısı $(systemctl show "$u" -p NRestarts --value)"
  done
  memory_report
  memmax_check
  echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'yanıt yok')"
  say "ÖĞRENME MODU (yalnız PAPER)"
  learning_report
  say "ORTAK DENEYİM KATMANI (yalnız KAYIT) + KARŞI-OLGUSAL NET + CANLI MUHASEBE DÜZELTMESİ"
  xp_report
  say "defterler (şimdi)"
  book_snapshot
  if [[ -f "$LOGDIR/$T7-before.txt" ]]; then say "defterler (dağıtımdan hemen önce)"; cat "$LOGDIR/$T7-before.txt"; fi
  say "defterler: D4 (4h trend), C4 / C4S (mum varyasyonları), Box yapı modu, formasyon protokolü"
  py - "$STATE" <<'PY'
import json, os, sys
st = sys.argv[1]
for key, label in (("strategy_paper_trend4h", "D4 (4h trend, gözlem)"), ("strategy_paper_candle4h", "C4 (mum varyasyonları)"),
                   ("strategy_paper_candle4h_strict", "C4S (mum varyasyonları, sıkı)")):
    f = os.path.join(st, key + ".json")
    if not os.path.exists(f):
        print("   %s: özet henüz yok (ilk turdan sonra görünür)" % label)
        continue
    d = json.load(open(f, encoding="utf-8"))
    s, c = d.get("summary") or {}, d.get("counters") or {}
    print("   %s: özkaynak=%s açılan=%s kapanan=%s tur=%s son kural turu=%s"
          % (label, s.get("equity"), c.get("opened"), c.get("closed"), c.get("tours"), d.get("rule_evaluated_at")))
PY
  py - <<'PY' || true
import yaml
raw = yaml.safe_load(open("config.yaml", encoding="utf-8"))
print("   Box yapı modu (config):", (raw.get("structures") or {}).get("b1_box_fade"))
_ex = {b.get("name"): b for b in ((raw.get("strategy_paper") or {}).get("extra") or []) if isinstance(b, dict)}
for _n in ("c4_candle_variations", "c4s_candle_variations_strict"):
    print("   %s etkin varyasyon (config): %d" % (_n, len(((_ex.get(_n) or {}).get("rule_params") or {}).get("variations") or [])))
print("   formasyon protokolü (config):", (raw.get("pattern_trader") or {}).get("protocol"))
print("   öğrenme modu (config): enabled =", (raw.get("learning_mode") or {}).get("enabled"))
print("   ortak deneyim (config):", raw.get("shared_experience"))
PY
  say "karne (maliyet sonrası; öğrenme öncesi/sonrası ayrı; karşı-olgusal NET; salt okunur)"
  py scripts/bot_scorecard.py --state "$STATE" 2>&1 | tail -n 40 || echo "   karne okunamadı"
  say "son 30 dk uyarı/hata satırları (worker)"
  journalctl -u "$WORKER" --since "-30min" -p warning --no-pager 2>/dev/null | tail -n 25 || true
  exit 0
fi

[[ "$MODE" == "deploy" || "$MODE" == "--dry-run" ]] || die "bilinmeyen seçenek: $MODE (--dry-run | --detach | --check | seçeneksiz)"

# ------------------------------------------------------------------ 1) kontroller (hiçbir şeye dokunmaz)
say "1/10 kod kaynağı"
if [[ -f "$BUNDLE_SRC" ]]; then
  SOURCE="bundle"
  [[ -n "$BUNDLE_SHA256" ]] || die "bu sürüm için bundle tanımlı değil: $BUNDLE_SRC dosyasını kaldırın (kod GitHub'dan alınır). HİÇBİR ŞEYE DOKUNULMADI"
  got="$(sha256sum "$BUNDLE_SRC" | awk '{print $1}')"
  [[ "$got" == "$BUNDLE_SHA256" ]] || die "bundle sha256 uyuşmuyor: $got (beklenen $BUNDLE_SHA256) — dosya bozuk/eksik indirilmiş"
  ok "bundle, sha256 $got"
else
  SOURCE="github"
  ok "GitHub ($REPO_URL), commit ${TIP}"
fi

say "2/10 mevcut kod"
PREV="$(gitc rev-parse HEAD)"
BRANCH_NOW="$(gitc symbolic-ref --quiet --short HEAD || echo detached)"
echo "   HEAD ${PREV:0:7} ($BRANCH_NOW)"
if [[ "$PREV" == "$TIP" ]]; then echo "   zaten hedefte ($T7). Durum için: sudo bash $0 --check"; exit 0; fi
dirty="$(gitc status --porcelain --untracked-files=no)"
[[ -z "$dirty" ]] || die "izlenen dosyalarda yerel değişiklik var (HİÇBİR ŞEYE DOKUNULMADI):
$dirty"
ok "izlenen dosyalar temiz"

say "3/10 hedef commit'in getirilmesi (salt ekleme; çalışma ağacı değişmez)"
if [[ "$SOURCE" == "bundle" ]]; then
  install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$BASE/deploy-bundles"
  BUNDLE="$BASE/deploy-bundles/$(basename "$BUNDLE_SRC")"
  install -o "$SVC_USER" -g "$SVC_USER" -m 0640 "$BUNDLE_SRC" "$BUNDLE"
  gitc bundle verify -q "$BUNDLE" || die "bundle bu kopyaya uygulanamıyor (gereken taban $(shorts "${PREREQ[@]}") yok). HEAD ${PREV:0:7}. HİÇBİR ŞEYE DOKUNULMADI"
  gitc fetch -q "$BUNDLE" "$BUNDLE_REF:refs/deploy/$T7"
  [[ "$(gitc rev-parse "refs/deploy/$T7")" == "$TIP" ]] || die "bundle'daki dal ucu beklenen commit değil"
else
  # önce tam SHA ile (GitHub destekler); olmazsa dalı getirip commit'i onun içinde ara
  if ! as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$TIP" 2>/dev/null; then
    as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$BUNDLE_REF" \
      || die "GitHub'a erişilemedi ($REPO_URL). HİÇBİR ŞEYE DOKUNULMADI — VPS'in GitHub erişimini kontrol edin"
  fi
  gitc cat-file -e "${TIP}^{commit}" 2>/dev/null || die "hedef commit $T7 getirilemedi. HİÇBİR ŞEYE DOKUNULMADI"
  gitc update-ref "refs/deploy/$T7" "$TIP"
fi
ok "hedef commit $T7 hazır ($SOURCE)"

say "4/10 ileri sarma mümkün mü + çalışan sürüm"
gitc merge-base --is-ancestor "$PREV" "$TIP" || die "mevcut HEAD ${PREV:0:7} hedefin atası değil (ayrışmış kod). HİÇBİR ŞEYE DOKUNULMADI; bana HEAD'i iletin"
echo "   ${PREV:0:7} → $T7: $(gitc rev-list --count "$PREV..$TIP") commit"
FOUND=""
for p in "${PREREQ[@]}"; do             # en yeni eşleşen kazanır (liste eskiden yeniye)
  if gitc merge-base --is-ancestor "$p" "$PREV" 2>/dev/null; then FOUND="$p"; fi
done
[[ -n "$FOUND" ]] || die "çalışan sürüm ${PREV:0:7}, beklenenlerden ($(shorts "${PREREQ[@]}")) hiçbiri ya da soyu değil (daha ESKİ kod).
  Önce o sürümlerin betiklerini sırayla çalıştırın ya da bana HEAD'i iletin. HİÇBİR ŞEYE DOKUNULMADI"
if [[ "$PREV" == "$FOUND" ]]; then
  ok "çalışan sürüm ${FOUND:0:7} (kabul edilenler: $(shorts "${PREREQ[@]}"))"
else
  ok "çalışan sürüm ${PREV:0:7}: ${FOUND:0:7} soyundan ve hedefin atası (kabul edilenler: $(shorts "${PREREQ[@]}"))"
fi
for p in "${PREREQ[@]}"; do
  if [[ "$p" != "$FOUND" ]] && gitc merge-base --is-ancestor "$FOUND" "$p" 2>/dev/null \
     && ! gitc merge-base --is-ancestor "$p" "$PREV" 2>/dev/null; then
    echo "   ${p:0:7} sürümünün değişiklikleri de bu dağıtıma DAHİL (o sürümün betiğini ayrıca çalıştırmayın)"
  fi
done
if gitc diff --quiet "$PREV" "$TIP" -- requirements.txt; then REQ_CHANGED=""; else REQ_CHANGED=1; fi
echo "   requirements.txt: $([[ -n "$REQ_CHANGED" ]] && echo "DEĞİŞTİ (pip install yapılacak)" || echo "değişmedi (pip gerekmez)")"
# (2026-09-29) geri dönülecek kod öğrenme modunu tanıyorsa Formasyon öğrenme planları geri almada temizlenmez
if gitc cat-file -e "$PREV:tradingbot/learning_mode.py" 2>/dev/null; then PREP_NEEDED=""; else PREP_NEEDED=1; fi
ok "hızlı ileri sarma mümkün"

say "5/10 disk, servisler, bellek sınırı, ortam, mod, ortak deneyim klasörü"
free_kb="$(df -Pk "$DATA" | awk 'NR==2{print $4}')"
[[ "$free_kb" -gt 2097152 ]] || die "diskte 2 GB'tan az boş yer var ($((free_kb/1024)) MB) — yedek için yetersiz"
ok "boş disk $((free_kb/1024)) MB"
for u in "$WORKER" "$DASH"; do echo "   $u: $(systemctl is-active "$u" || true)"; done
memory_report
memmax_check
lm_env_scan || die "TRADINGBOT_LEARNING_MODE servis ortamında tanımlı: $lm_env_hint. HİÇBİR ŞEYE DOKUNULMADI"
xp_env_scan || die "TRADINGBOT_SHARED_EXPERIENCE geçersiz değerle tanımlı: $xp_env_hint. HİÇBİR ŞEYE DOKUNULMADI"
mode_check || die "state/mode.json PAPER değil ya da okunamıyor — öğrenme modu yalnız PAPER'da açılır. HİÇBİR ŞEYE DOKUNULMADI"
xp_dir_check check || die "state/shared_experience servis kullanıcısı için açılamaz/yazılamaz. HİÇBİR ŞEYE DOKUNULMADI"
say "defterler (şimdi)"
book_snapshot

# ------------------------------------------------------------------ 2) yeni kodun sınanması + değişiklik
if [[ "$MODE" == "deploy" ]]; then
  mkdir -p "$LOGDIR"
  # SSH bağlantısı koparsa (HUP) dağıtım yarıda KALMAZ: sona kadar sürer, çıktı ayrıca log dosyasına yazılır.
  trap '' HUP
  # tee de INT/TERM/HUP'u yok sayar: Ctrl+C önce tee'yi öldürürse betiğin sonraki yazımı SIGPIPE ile onu da öldürür ve
  # geri alma YARIM kalır (sahte VPS'te ölçüldü).
  exec > >(trap '' INT TERM HUP; exec tee -a "$LOGDIR/$T7-deploy.log") 2>&1
  echo "   (çıktı ayrıca: $LOGDIR/$T7-deploy.log)"
  ( book_snapshot ) > "$LOGDIR/$T7-before.txt" 2>&1 || true    # alt kabuk: tuzak çıktısı dosyaya gitmesin
  echo "$PREV" > "$LOGDIR/$T7-prev-commit.txt"
fi

# Ctrl+C / systemctl stop: kod henüz değişmediyse yalnız çıkar (worker durdurulduysa eski kodla yeniden başlatır);
# değiştiyse geri alır; servis yeni kodla başladıysa bildirir.
MERGED=""; RESTARTED=""; STOPPED=""
WT="$BASE/deploy-wt-$T7"
cleanup_wt() { gitc worktree remove --force "$WT" >/dev/null 2>&1 || true; }
on_abort() {
  trap '' INT TERM
  if [[ -n "$RESTARTED" ]]; then
    echo "DUR: durduruldu — servisler yeni kodla başlatılmıştı; durum için: sudo bash $0 --check" >&2 || true
  elif [[ -n "$MERGED" ]]; then
    revert_code
    echo "DUR: durduruldu → kod ${PREV:0:7}'e geri alındı; worker eski kodla yeniden başlatıldı" >&2 || true
  elif [[ -n "$STOPPED" ]]; then
    systemctl start "$WORKER" || true
    echo "DUR: durduruldu — kod DEĞİŞMEDİ; durdurulan worker eski kodla yeniden başlatıldı" >&2 || true
  else
    cleanup_wt
    echo "DUR: durduruldu — kod DEĞİŞMEDİ, servisler yeniden başlatılmadı" >&2 || true
  fi
  exit 130
}
trap on_abort INT TERM
trap 'cleanup_wt' ERR

say "6/10 yeni kodun sınanması — AYRI çalışma kopyasında, servis ortamıyla (çalışan bot ve kodu DEĞİŞMEZ)"
# Kod, ancak worker boşa çıktığı ve DURDURULDUĞU anda değiştirilir. Önceki bir sürümde kod beklemeden ÖNCE değişiyordu:
# bekleme sırasında worker kendiliğinden yeniden başlarsa (OOM) yeni kodla açılıyor, betik sonra diski geri alsa da
# bellekte yeni kod çalışmaya devam ediyordu (2026-09-25/26 VPS'te oldu).
cleanup_wt
[[ -e "$WT" ]] && rm -rf -- "${WT:?}"
gitc worktree add -q --detach "$WT" "$TIP"
if ! svc_run "$WT" "$VENV/bin/python" -m tradingbot preflight --quick; then
  cleanup_wt; die "preflight (yeni kod) başarısız → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
fi
# Değişmezler yeni kodun KENDİ yükleyicisiyle (`load_config`: yinelenen anahtar reddi + servis ortamı, ör.
# TRADINGBOT_LEARNING_MODE / TRADINGBOT_SHARED_EXPERIENCE) okunur; yükleme hatası (ConfigError) da düşme sayılır.
if ! svc_run "$WT" "$VENV/bin/python" - "$STATE" <<'PY'
import json
import os
import sys
from pathlib import Path

st = Path(sys.argv[1])
try:
    from tradingbot.config import load_config, load_yaml_strict
    cfg = load_config(Path("config.yaml"))
    raw = load_yaml_strict(Path("config.yaml").read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 — ConfigError dahil (ör. geçersiz TRADINGBOT_LEARNING_MODE) → fail-closed
    print("   HATA  config yeni kodla ve servis ortamıyla yüklenemedi: %s: %s" % (type(exc).__name__, exc))
    sys.exit(1)
v3 = cfg.v3
from tradingbot import candle_variations as V
from tradingbot import learning_mode as LMM
from tradingbot.learn.research_coordinator import mode_gate
from tradingbot.risk.modes import ModeState
from tradingbot.risk.profiles import PROFILES
from tradingbot.strategy_paper import book_specs

books = {b.name: b for b in book_specs(v3)}
C4_IDS = ["CV001_BREAKOUT20_TREND_VOL_L", "CV002_BREAKOUT20_TREND_VOL_S", "CV003_PULLBACK_ENGULF_L", "CV004_PULLBACK_ENGULF_S",
          "CV005_SUPPORT_HARAMI_L", "CV006_RESIST_HARAMI_S", "CV007_SWEEP_REJECT_ENGULF_L", "CV008_SWEEP_REJECT_ENGULF_S"]
# docs/ogrenme_modu/CONTRACT.md "config.yaml values for L1" (+ 6fb39cd: ana bot leverage_max 5) — birebir
LM_SCALARS = {"enabled": True, "risk_per_trade_pct": 0.5, "max_total_open_risk_pct": 100, "margin_reserve_pct": 5,
              "liq_buffer_mult": 2.0, "min_notional_bump": True, "counterfactual": True, "counterfactual_max_pending": 2000}
LM_BOOKS = {
    "main": {"enabled": True, "slots": 20, "leverage_max": 5},
    "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
    "m2_tsmom28": {"enabled": True, "slots": 40, "leverage_max": 4},
    "b1_box_fade": {"enabled": True, "slots": 20, "leverage_max": 4, "min_stop_pct": 0.32},
    "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
    "c4_candle_variations": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
    "c4s_candle_variations_strict": {"enabled": False},
    "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3},
}
LM_OVR = {"regime_gate_shadow": True, "candle_veto_shadow": True,
          "structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"],
          "economics_exploration": True, "leverage_confidence_fallback": True}
lmraw = raw.get("learning_mode") if isinstance(raw.get("learning_mode"), dict) else {}
lm = v3.learning_mode


def same(a, b):
    """Birebir eşitlik; bool yalnız bool'a eşit (1 == True sayılmaz), sayılar değerce."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def lm_scalars():
    typed = {k: getattr(lm, k) for k in LM_SCALARS}
    rawv = {k: lmraw.get(k) for k in LM_SCALARS}
    return same(typed, LM_SCALARS) and same(rawv, LM_SCALARS) \
        and set(lmraw) == set(LM_SCALARS) | {"books", "strategy_overrides"}


def lm_books():
    exp_norm = {n: LMM.BookLearningCfg(**c).to_dict() for n, c in LM_BOOKS.items()}
    return same(lmraw.get("books"), LM_BOOKS) and same({n: b.to_dict() for n, b in lm.books.items()}, exp_norm)


def lm_runtime():
    """Yeni kodun `LearningMode`'u config'i defterlere birebir dağıtıyor mu (kapı açık varsayımıyla)."""
    m = LMM.LearningMode.from_config(v3, lambda: (True, "OK"))
    m.refresh()
    for n in LMM.BOOK_NAMES:
        c, b = LM_BOOKS.get(n) or {}, m.book(n)
        if not c.get("enabled"):
            if b is not None:
                return False
            continue
        if b is None or (b.slots, b.leverage_max, b.risk_pct, b.reserve_pct, b.liq_buffer_mult, b.hard_cap_pct,
                         b.min_stop_pct, b.symbols) != (c["slots"], c["leverage_max"], 0.5, 5.0, 2.0, 2.0,
                                                        c.get("min_stop_pct"), c.get("symbols")):
            return False
    return (m.structures_entry_shadow("main") and m.structures_entry_shadow("m2_tsmom28")
            and not m.structures_entry_shadow("b1_box_fade") and m.override("economics_exploration", False) is True)


def mode_file_ok():
    p = st / "mode.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        if not (isinstance(d, dict) and d.get("mode") == "PAPER" and d.get("live_order_path_enabled") is not True):
            return False
    ms = ModeState(p)
    return mode_gate(ms.mode.value, v3.execution.gateway, ms.is_live_order_path_enabled()) == (True, "OK")


# --- ORTAK DENEYİM KATMANI v1 + NET ETİKET (2026-09-29): docs/ortak_deneyim/SPEC_V1.md §14/§18, KARARLAR.md
XP_RAW = {"enabled": True, "mode": "RECORD"}
XP_DEFAULTS = {"state_dir": "shared_experience", "hot_max_lines": 5000, "archive_max_segments": 0, "max_total_mb": 1024,
               "tour_budget_s": 2.0, "max_rows_per_tour": 600, "backfill": True, "cache_entries": 2048,
               "pending_max_age_h": 48.0, "lock_timeout_s": 0.2, "lazy_fetch_max_per_tour": 0}
XP_ENV = os.environ.get("TRADINGBOT_SHARED_EXPERIENCE", "").strip().lower()
XP_IMPORTERS = {"tradingbot/engine_v3.py", "tradingbot/cli_v3.py"}
xp = getattr(v3, "shared_experience", None)      # bölümü tanımayan kod: her ortak deneyim değişmezi ayrı ayrı düşer


def xp_active():
    """Katman ETKİN (enabled + RECORD). Servis ortamında TRADINGBOT_SHARED_EXPERIENCE=off/false/0/disabled ise operatör
    kapatmasıdır (dağıtım öncesi yüksek sesle basıldı): o zaman yeni kod katmanı gerçekten KAPATIYOR olmalı."""
    if XP_ENV:
        return XP_ENV in ("off", "false", "0", "disabled") and xp.enabled is False and xp.mode == "OFF" \
            and xp.active is False
    return xp.enabled is True and xp.mode == "RECORD" and xp.active is True


def xp_section():
    """config.yaml bölümü BİREBİR {enabled: true, mode: RECORD}; geri kalan her alan kod varsayılanında."""
    from dataclasses import fields
    typed = {f.name: getattr(xp, f.name) for f in fields(xp)}
    exp = dict(XP_DEFAULTS, **(dict(enabled=False, mode="OFF") if XP_ENV else XP_RAW))
    return same(raw.get("shared_experience"), XP_RAW) and same(typed, exp)


def xp_paper_only():
    """Toplayıcı (yeni kod) mod PAPER değilken ya da canlı emir yolu açıkken SUSPENDED:<mod> olur ve satır YAZMAZ.
    Geçici klasörde gerçek `SharedExperienceCollector` + gerçek `ModeState` ile sınanır (state'e dokunmaz)."""
    import tempfile
    import types
    from tradingbot.risk.modes import OperatingMode
    from tradingbot.shared_experience.collector import SharedExperienceCollector, XpSettings

    class _LiveOrderPath(ModeState):
        is_live_order_path_enabled = staticmethod(lambda: True)

    live, testnet = ModeState(None), ModeState(None)
    live.mode, testnet.mode = OperatingMode.LIVE, OperatingMode.TESTNET
    cases = (("SUSPENDED:LIVE", live), ("SUSPENDED:LIVE_ORDER_PATH_ENABLED", _LiveOrderPath(None)),
             ("SUSPENDED:TESTNET", testnet))
    with tempfile.TemporaryDirectory(prefix="tb-deploy-xp-") as d:
        root = Path(d) / "xp"
        c = SharedExperienceCollector(root, settings=XpSettings.from_section(xp))
        for want, ms in cases:
            r = c.step(types.SimpleNamespace(mode_state=ms))
            if r.get("state") != want or c.state != want or r.get("rows") != 0:
                return False
        return c.c["rows_total"] == 0 and not (root / "experience.jsonl").exists()


def xp_situation_consts():
    from tradingbot.pattern_trader.data import BARS_PER_TF
    from tradingbot.shared_experience import situation as XS
    return (XS.SCHEMA_ID == "situation_v1" and XS.SCHEMA_SHA == "640fd10e5d6f727c"
            and XS.W4H == XS.W1H == XS.WBTC == 200 <= BARS_PER_TF["4h"] == BARS_PER_TF["1h"] == 240)


def xp_row_consts():
    import tradingbot.shared_experience as XP
    from tradingbot.shared_experience import rows as XR
    from tradingbot.shared_experience import store as XST
    return (XP.ROW_SCHEMA == XR.ROW_SCHEMA == "shared_experience_row_v1" and XP.LAYER_VERSION == "1.0.0"
            and XST.ROTATE_KEEP_FRACTION == 0.5)


def cf_consts():
    from tradingbot import learning_cf as CF
    return (CF.LABEL_VERSION, CF.NET_CONTRACT, CF.INTRABAR_PATH) \
        == ("cf_label_v3", "cf_net_ledger_replay_v2", "open>adverse>favourable>close")


def _xp_imported(path):
    import ast
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = "." * n.level + (n.module or "")
            mods = [base] + [base + "." + a.name for a in n.names]
        elif isinstance(n, ast.Call):            # importlib.import_module("…") / __import__("…")
            f = n.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in ("import_module", "__import__"):
                mods = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if any("shared_experience" in m for m in mods):
            return True
    return False


def xp_importers():
    """Dağıtılan ağaçta (tradingbot/ + scripts/) paketi YALNIZ engine_v3 ve cli_v3 içe aktarır; panel aktarmaz."""
    pkg = Path("tradingbot") / "shared_experience"
    found = set()
    for base in ("tradingbot", "scripts"):
        for p in sorted(Path(base).rglob("*.py")):
            if p.parent == pkg or pkg in p.parents:
                continue
            if "shared_experience" in p.read_text(encoding="utf-8") and _xp_imported(p):
                found.add(p.as_posix())
    okk = found <= XP_IMPORTERS and "tradingbot/engine_v3.py" in found \
        and not any(f.startswith("tradingbot/dashboard/") for f in found)
    if not okk:
        print("         içe aktaranlar: %s (izinli: %s)" % (sorted(found), sorted(XP_IMPORTERS)))
    return okk


def xp_config_hash():
    """Motorun config_hash'i shared_experience bölümüyle ve bölümsüz AYNI (motor bölümü düşürür: karar kimliği değişmez)."""
    import copy
    import types
    from tradingbot.config_v3 import load_v3
    from tradingbot.engine_v3 import TradingEngineV3

    def h(v):
        return TradingEngineV3.config_hash(types.SimpleNamespace(cfg=types.SimpleNamespace(v3=v)))
    with_sec = h(load_v3(copy.deepcopy(raw)))
    without = h(load_v3({k: copy.deepcopy(v) for k, v in raw.items() if k != "shared_experience"}))
    return with_sec is not None and with_sec == without == h(v3)


rp = raw.get("risk") or {}
checks = [
    # --- önceki sürümün 15 değişmezi (üçü TABAN olarak yeniden adlandırıldı: öğrenme kendi kaldıraç/risk
    #     tavanını defter başına ayrı taşır; config'teki TABAN değerler AYNEN kalır)
    ("mod PAPER", lambda: v3.mode.mode == "PAPER"),
    ("gerçek işlem kapalı", lambda: not bool(getattr(v3.mode, "live_trading", False))),
    ("T2/M2/Box/D4/C4/C4S defterleri", lambda: set(books) == {"t2_trend_regime", "m2_tsmom28", "b1_box_fade",
                                                             "d4_donchian_20_10", "c4_candle_variations",
                                                             "c4s_candle_variations_strict"}),
    ("D4 TABAN kaldıraç 1 (öğrenme kaldıracı ayrı: learning_mode.books)",
     lambda: int(books["d4_donchian_20_10"].rule_params.get("leverage", 1)) == 1),
    ("D4 ayrı defter", lambda: books["d4_donchian_20_10"].state_dir == "strategy_paper_trend4h"),
    ("C4/C4S TABAN kaldıraç 1", lambda: all(int(books[n].rule_params.get("leverage", 1)) == 1
                                            for n in ("c4_candle_variations", "c4s_candle_variations_strict"))),
    ("C4: CV001–CV008 gözlem olarak etkin",
     lambda: list(books["c4_candle_variations"].rule_params.get("variations") or []) == C4_IDS),
    ("C4 varyasyonları kapıdan geçiyor (standart)", lambda: all(V.gate(v)[1] is None for v in C4_IDS)),
    ("C4S boş (sıkı hükümle GÜÇLÜ varyasyon yok)",
     lambda: not list(books["c4s_candle_variations_strict"].rule_params.get("variations") or [])),
    ("C4S sıkı hüküm", lambda: books["c4s_candle_variations_strict"].rule_params.get("verdict_mode") == "strict"),
    ("C4/C4S ayrı defterler", lambda: (books["c4_candle_variations"].state_dir,
                                       books["c4s_candle_variations_strict"].state_dir)
                                      == ("strategy_paper_candle4h", "strategy_paper_candle4h_strict")),
    ("Box yapı katmanı SHADOW (karar B)", lambda: (raw.get("structures") or {}).get("b1_box_fade") == "SHADOW"),
    ("formasyon protokolü v3", lambda: v3.pattern_trader.protocol == "momentum_4h_v3"),
    ("tek coin tavanı %30", lambda: float(rp["max_position_pct"]) == 30.0),
    ("TABAN işlem riski %2 = öğrenme sert tavanı",
     lambda: float(rp["risk_per_trade_pct"]) == 2.0 and float(LMM.HARD_CAP_PCT) == 2.0),
    # --- öğrenme modu (CONTRACT.md "Additions" dahil)
    ("öğrenme modu ETKİN (servis ortamı dahil)", lambda: lm.enabled is True),
    ("öğrenme değerleri birebir: risk %0,5 · toplam açık risk 100 · rezerv %5 · likidasyon 2,0 × stop · "
     "min-notional yükseltme · karşı-olgusal (en çok 2000 bekleyen)", lm_scalars),
    ("öğrenme defterleri birebir: main K20 L5 · T2/M2 K40 L4 · Box K20 L4 min stop 0,32 · D4/C4 K20 L3 evren · "
     "C4S kapalı · Formasyon K30 L3", lm_books),
    ("öğrenme strateji ezmeleri birebir: rejim/mum gölge · yapı girişi gölge [main, T2, M2] · keşif · kaldıraç tabanı",
     lambda: same(lmraw.get("strategy_overrides"), LM_OVR) and same(dict(lm.strategy_overrides), LM_OVR)),
    ("öğrenme anahtarı defterlere doğru dağılıyor (yeni kod LearningMode)", lm_runtime),
    ("risk profili PAPER_RESEARCH, overrides {}",
     lambda: str(v3.risk_profiles.profile).upper() == "PAPER_RESEARCH"
     and (raw.get("risk_profiles") or {}).get("overrides") == {} and not (v3.risk_profiles.overrides or {})),
    ("TABAN profil sabitleri değişmedi (PAPER_RESEARCH: işlem %2, toplam açık risk %6; öğrenme profili kayıtsız)",
     lambda: (PROFILES["PAPER_RESEARCH"].risk_per_trade_pct, PROFILES["PAPER_RESEARCH"].max_total_open_risk_pct)
     == (2.0, 6.0) and set(PROFILES) == {"PAPER_RESEARCH", "TESTNET", "SHADOW_LIVE", "LIVE_LIMITED", "LIVE"}),
    ("yürütme kapısı paper", lambda: str(v3.execution.gateway).lower() == "paper"),
    ("testnet kapalı", lambda: v3.execution.testnet_enabled is False),
    ("TABAN adet tavanları 3 değişmedi (risk.max_open_positions, futures.max_positions, pattern_trader)",
     lambda: int(rp["max_open_positions"]) == 3 and int((raw.get("futures") or {})["max_positions"]) == 3
     and int(v3.pattern_trader.max_open_positions) == 3),
    ("state/mode.json yok ya da PAPER; mod kapısı açık (öğrenme AKTİF olur)", mode_file_ok),
    # --- ortak deneyim katmanı v1 (yalnız KAYIT) + net etiket (2026-09-29)
    ("ortak deneyim ETKİN: enabled + mode RECORD (servis ortamı dahil; yalnız off env'i kapatabilir)", xp_active),
    ("ortak deneyim config.yaml bölümü birebir {enabled: true, mode: RECORD}; varsayılanlar: state_dir shared_experience · "
     "sıcak 5000 · arşiv 0 · 1024 MB · bütçe 2,0 sn · 600 satır · geri doldurma · önbellek 2048 · 48 sa · kilit 0,2 sn · "
     "ağ 0", xp_section),
    ("ortak deneyim YALNIZ PAPER: toplayıcı PAPER dışı modda / canlı emir yolu açıkken SUSPENDED:<mod>, satır yok",
     xp_paper_only),
    ("durum şeması situation_v1 / SCHEMA_SHA 640fd10e5d6f727c; W4H = W1H = WBTC = 200 ≤ BARS_PER_TF 4h = 1h = 240",
     xp_situation_consts),
    ("satır şeması shared_experience_row_v1 · katman 1.0.0 · döngü histerezisi 0,5", xp_row_consts),
    ("net etiket cf_label_v3 · cf_net_ledger_replay_v2 · bar içi yol open>adverse>favourable>close", cf_consts),
    ("paketi yalnız engine_v3 ve cli_v3 içe aktarır; panel aktarmaz (AST, dağıtılan ağaç)", xp_importers),
    ("config_hash shared_experience bölümüyle ve bölümsüz AYNI (karar kimliği değişmez)", xp_config_hash),
]
bad = []
for n, fn in checks:
    try:
        okk = bool(fn())
        why = ""
    except Exception as exc:  # noqa: BLE001 — değerlendirilemeyen değişmez düşmüş sayılır
        okk, why = False, " (değerlendirilemedi: %s: %s)" % (type(exc).__name__, exc)
    if not okk:
        bad.append(n)
    print("   %s  %s%s" % ("OK  " if okk else "HATA", n, why))
if lm.enabled is not True and os.environ.get("TRADINGBOT_LEARNING_MODE"):
    print("   (servis ortamında TRADINGBOT_LEARNING_MODE=%s öğrenmeyi kapatıyor)" % os.environ["TRADINGBOT_LEARNING_MODE"])
if XP_ENV:
    print("   !!! servis ortamında TRADINGBOT_SHARED_EXPERIENCE=%s — ortak deneyim katmanı KAPALI kalacak (operatör kapatması)"
          % os.environ["TRADINGBOT_SHARED_EXPERIENCE"])
print("   %d/%d değişmez geçti" % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
PY
then
  cleanup_wt; die "config değişmezleri (yeni kod) düştü → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
fi
cleanup_wt
ok "yeni kod ayrı kopyada preflight ve değişmezlerden geçti"

if [[ "$MODE" == "--dry-run" ]]; then
  say "KURU ÇALIŞMA BİTTİ — çalışan kod, state ve servisler DEĞİŞMEDİ (hedef kod git nesnelerine eklendi, ayrı kopya silindi)."
  echo "   Dağıtımda ayrıca (worker durmuşken): doğrulanmış yedek + scripts/cf_backfill_net.py kuru çalışma ve --apply."
  echo "   Dağıtmak için: sudo bash $0   (ya da SSH'tan bağımsız: sudo bash $0 --detach)"
  exit 0
fi

say "7/10 doğrulanmış yedek (git ve servislere dokunmadan ÖNCE)"
svc_run "$APP" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" \
  TRADINGBOT_BACKUPS_DIR="$DATA/backups" bash "$APP/deploy/backup.sh" manual \
  || die "YEDEK BAŞARISIZ ya da DOĞRULANAMADI → dağıtım DURDURULDU (git/servis dokunulmadı)"
echo "$PREV" > "$BASE/.last_good_commit"       # deploy/rollback.sh bunu kullanır
chown "$SVC_USER:$SVC_USER" "$BASE/.last_good_commit" 2>/dev/null || true
ok "yedek alındı; geri dönüş işaretçisi ${PREV:0:7}"

say "8/10 worker'ın turlar arası beklemeye girmesi bekleniyor (kod hâlâ ${PREV:0:7})"
RESTART_AT="$(date '+%Y-%m-%d %H:%M:%S')"
key_log() {    # yalnız karar verdiren satırlar (analiz gürültüsü değil)
  journalctl -u "$WORKER" --since "$RESTART_AT" --no-pager 2>/dev/null \
    | grep -E 'Started|Stopping|Stopped|Main process exited|Scheduled restart|Failed|Traceback|ERROR|CRITICAL|Killed|oom|BLOCK|ALLOW|ConfigError|LEARNING|ortak deneyim|DEVRE KESİCİ' \
    | tail -n 40 || true
}
# Worker SIGTERM'de MEVCUT TURU bitirip çıkar; tur ~10-25 dk sürer, TimeoutStopSec 90 sn → tur ortasında durdurmak SIGKILL
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
# Worker her yeniden başlatmadan sonra ve her yeni 4h mumda TURDAN ÖNCE tam spot WFO döngüsü koşar (bekleme döngüsü
# bunu kontrol etmez); WFO + tur 1 saati aşabiliyor (2026-09-25 VPS: 30 dk içinde hiç bekleme görülmedi) → 3 saat.
phase_now() {
  journalctl -u "$WORKER" --since "-6h" -o cat --no-pager 2>/dev/null \
    | grep -E '^===== .* — (SPOT WFO|TUR #)' | tail -n 1 | tr -d '=' | sed 's/^ *//;s/ *$//' || true
}
# Dağıtım öncesi tepe bellek (health.json learning_mode.memory.hwm_mb) → --check farkı (2026-09-29; salt okur)
pre_hwm() {
  py - "$STATE/health.json" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(((d.get("learning_mode") or {}).get("memory") or {}).get("hwm_mb") or "")
except Exception:  # noqa: BLE001
    print("")
PY
}
waited=0
wait_idle() {
  until idle_now; do
    if (( waited >= 10800 )); then
      die "worker 3 saat içinde turlar arası beklemeye girmedi → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
    fi
    if (( waited % 300 == 0 )); then
      echo "   $(date '+%H:%M') worker meşgul [$(phase_now)] — bitince durdurulacak (bekleniyor: $((waited / 60)) dk)"
    fi
    sleep 10; waited=$((waited + 10))
  done
}
while :; do
  wait_idle
  ok "worker turlar arası beklemede"
  say "9/10 son ortam denetimi → worker DURUR → kod ${PREV:0:7} → $T7 (ff-only) → yedek → karşı-olgusal net dolgu"
  # bekleme saatler sürebilir: ortam ve mod bu arada değişmiş olabilir → kodu değiştirmeden hemen önce yeniden bak
  ENV_ARGS=(); rm -f "$ENVFILE"; load_env
  lm_env_scan || die "TRADINGBOT_LEARNING_MODE beklerken tanımlanmış: $lm_env_hint. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  xp_env_scan || die "TRADINGBOT_SHARED_EXPERIENCE beklerken geçersiz değerle tanımlanmış: $xp_env_hint. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  mode_check || die "state/mode.json beklerken PAPER dışına çıkmış. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  xp_dir_check check || die "state/shared_experience beklerken yazılamaz olmuş. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  ( pre_hwm ) > "$MARK_PRE_HWM" 2>/dev/null || true             # alt kabuk: tuzak çıktısı dosyaya gitmesin
  # denetimler birkaç saniye sürdü: worker bu arada yeni tura başladıysa (tur ortasında durdurma = SIGKILL) yeniden bekle
  if idle_now; then break; fi
  echo "   worker bu arada yeni tura başladı — yeniden bekleniyor"
done
# (2026-09-29) Göç (yedek + net dolgu) worker DURMUŞKEN yapılır: çalışan worker karşı-olgusal dosyalarını kendi bellek
# kopyasıyla yeniden yazardı. Worker durduktan sonraki her hata worker'ı ESKİ kodla yeniden başlatır.
STOPPED=1
trap 'systemctl start "$WORKER" || true; echo "DUR: beklenmeyen hata (satır $LINENO) — kod DEĞİŞMEDİ; worker eski kodla yeniden başlatıldı" >&2' ERR
systemctl stop "$WORKER" || true
if [[ "$(systemctl is-active "$WORKER" || true)" == "active" ]]; then
  trap - ERR
  die "worker durdurulamadı (hâlâ aktif) → kod DEĞİŞMEDİ; worker eski kodla çalışıyor"
fi
ok "worker durduruldu (turlar arası beklemedeydi; state yazıldı)"
MERGED=1
# Bu noktadan sonra beklenmeyen her hata kodu geri alır (worker durur → eski kod → worker başlar).
trap 'revert_code; echo "DUR: beklenmeyen hata (satır $LINENO) → kod ${PREV:0:7}e geri alındı" >&2' ERR
gitc merge -q --ff-only "$TIP"
[[ "$(gitc rev-parse HEAD)" == "$TIP" ]] || { revert_code; die "ileri sarma sonrası HEAD hedef değil → kod ${PREV:0:7}'e geri alındı"; }
ok "kod ${PREV:0:7} → $T7 (ff-only)"
if [[ -n "$REQ_CHANGED" ]]; then
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt"
  chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
fi
if ! xp_dir_check ensure; then
  revert_code; die "state/shared_experience açılamadı/yazılamadı → kod ${PREV:0:7}'e geri alındı, worker eski kodla başlatıldı"
fi
echo "   doğrulanmış yedek (worker durmuşken, yeni kodla; göçten hemen önceki state):"
if ! svc_run "$APP" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" TRADINGBOT_BACKUPS_DIR="$DATA/backups" \
     "$VENV/bin/python" -m tradingbot backup --manual; then
  revert_code; die "göç öncesi YEDEK BAŞARISIZ ya da DOĞRULANAMADI → kod ${PREV:0:7}'e geri alındı, worker eski kodla başlatıldı (state'e dokunulmadı)"
fi
ok "göç öncesi yedek alındı ve doğrulandı"
say "karşı-olgusal net dolgu (scripts/cf_backfill_net.py; servis kullanıcısı; worker DURMUŞ)"
if ! cf_backfill; then
  revert_code; die "karşı-olgusal net dolgu başarısız → kod ${PREV:0:7}'e geri alındı, worker eski kodla başlatıldı. Yukarıdaki satırları bana iletin"
fi
ok "karşı-olgusal net dolgu tamam (rapor: $CF_DRY_JSON, $CF_APPLY_JSON)"

say "10/10 worker başlatma + panel yeniden başlatma + 60 sn kararlılık"
RESTART_EPOCH="$(date +%s)"
RESTART_AT="$(date -d "@$RESTART_EPOCH" '+%Y-%m-%d %H:%M:%S')"
printf '%s\n%s\n' "$RESTART_EPOCH" "$RESTART_AT" > "$MARK_RESTART"
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
  revert_code
  die "worker kararlı çalışmadı (aktif olmadı ya da 60 sn içinde yeniden başladı) → worker durduruldu, kod ${PREV:0:7}'e geri alındı ve servisler yeniden başlatıldı (net'e çevrilmiş karşı-olgusal dosyalar ve state/shared_experience kalır; eski kod tolere eder). Yukarıdaki satırları bana iletin"
fi
trap - ERR INT TERM
ok "worker çalışıyor (PID $pid1, 60 sn kararlı)"
echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'henüz yanıt yok (birkaç sn sonra --check)')"
memory_report
memmax_check

cat <<EOF

DAĞITIM TAMAM: ${PREV:0:7} → $T7 — ORTAK DENEYİM KATMANI (yalnız KAYIT) + NET "olsaydı" R'si + canlı stop taşıma
düzeltmesi; ÖĞRENME MODU aynen (yalnız PAPER). Defterler sıfırlanmadı (önceki durum: $LOGDIR/$T7-before.txt).
Karşı-olgusal net dolgu raporları: $CF_DRY_JSON · $CF_APPLY_JSON (değişen dosyaların yanında .pre-cf-net-<UTC> yedekleri).

İzleme (ilk 2-3 turda birkaç kez; sonra günde bir):
  sudo bash $0 --check                         # öğrenme + ORTAK DENEYİM durumu, net/brüt, bellek, GERİ ALMA TETİKLERİ
  journalctl -u $WORKER -f                     # canlı log (Ctrl+C ile çık)
Yeni worker önce tam spot WFO döngüsünü, sonra ilk turu çalıştırır (toplam 1 saati aşabilir). Katman ilk RECORD turunda
state/shared_experience/ altına yazmaya başlar; ilk ~180 tur (~18 sa) geri doldurmadır (adım p95 ≈ 1 sn). İlk ~3 Box
geçişinde değerlendirme ≈ +1,5 sn sürebilir (tembel yeniden etiketleme; beklenir). "Olsaydı" kayıtları P&L'e girmez.
Geri alma tetikleri (--check işaretler):
  genel: tepe bellek (hwm_mb) > %90 × MemoryMax · tur > 35 dk · Box kaçan bar > 0/saat → bana iletin;
  YALNIZ KATMAN: devre kesici · adım p95 > 1500 ms (geri doldurma bitince) · depo DEGRADED → katmanı kapatın (aşağıda).
İsteğe bağlı panel özeti (otomatik yazan YOK; ≈ 5 sn, ≈ 140 MB; saatlik bir zamanlayıcıyla da kurulabilir: Nice=19, ayrı
dilim, servis kullanıcısı, worker ortamı):
  sudo -u $SVC_USER bash -c 'cd $APP && TRADINGBOT_STATE_DIR=$STATE nice -n 19 $VENV/bin/python -m tradingbot shared-experience-report --summary-out $XP_DIR/report_summary.json'

KATMANI KAPATMAK (yalnız katman; öğrenme modu ve net etiket AYNEN sürer — öğrenme geri alması DEĞİLDİR):
  config.yaml → shared_experience: {enabled: false, mode: RECORD}   (ya da: sudo systemctl edit $WORKER →
  [Service] Environment=TRADINGBOT_SHARED_EXPERIENCE=off)  ;  sudo systemctl restart $WORKER
  state/shared_experience/ kalır ve zararsızdır.
ÖĞRENME MODUNU KAPATMAK (kod aynı kalır): config.yaml → learning_mode: enabled: false ; sudo systemctl restart $WORKER $DASH
ESKİ KODA (${PREV:0:7}) dönmek (yalnız gerekirse; betiğin kendi geri alma adımlarıyla aynı sıra):
  sudo systemctl stop $WORKER
  sudo -u $SVC_USER git -C $APP checkout -q -B $BRANCH_NOW ${PREV:0:7}     # (dal yoksa: checkout ${PREV:0:7})
  sudo systemctl start $WORKER; sudo systemctl restart $DASH
  Eski kod yeni state'i tolere eder: bilinmeyen shared_experience bölümünü sessizce yok sayar; state/shared_experience/'ı
  hiç okumaz; net anahtarlı (r_net, cost_parts_r, exec_model, intrabar_ambiguous_bars) karşı-olgusal sonuçları aynen
  taşır ve brüt r_multiple raporlar; ek exit_fill anahtarları (stop_in_force …) ve BAR_PRE_MOVE_STOP olayları ek
  alanlardır; Formasyon'un entry_ref 'mark' karşı-olgusalları yalnız veridir. .pre-cf-net-<UTC> dosyalarını geri koymak
  isteğe bağlıdır, gerekmez. Geri dönüşten sonra yeni barlarda eski stop taşıma davranışı (önceki açılıştan kapanış) döner.
Yedekler: saatlik yedek shared_experience/archive/segments'ı yalnız UTC 00'da taşır (çıktıda skipped_xp_segments). Başka
  saatin yedeğinden geri yüklerken segmentleri en yeni UTC-00 (ya da elle) yedekten kopyalayın; segment dosyaları değişmez.
EOF
if gitc merge-base --is-ancestor "$PREV" "$OOM_FIX" 2>/dev/null && [[ "$(gitc rev-parse "$OOM_FIX" 2>/dev/null)" != "$PREV" ]]; then
  echo "  UYARI: ${PREV:0:7} bellek (OOM) onarımından ÖNCEKİ koddur — ona dönmek OOM'u GERİ GETİRİR; sağlıklı bir dönüş değildir."
fi

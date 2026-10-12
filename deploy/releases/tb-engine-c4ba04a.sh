#!/usr/bin/env bash
# trading2 — SÜREKLİ ÖĞRENME MOTORU P2.1 sürüm betiği (PAPER; gerçek para YOK). docs/SYSTEM_LEARNING_ENGINE_V1.md §2.2–§2.9,
# §9.3, §10 P2 (+ P2 uygulama notları 40–44). KÜÇÜK ve AYRILMIŞ: yalnız engine-app; tb-engine-44d5353.sh temel alındı.
# c4ba04a (2026-10-12): P2.1 — 44d5353'ün torunu, YALNIZ KOD: fidelity_v3 (kural/zaman çıkışı, ör. Box gün sonu kapanışı
#   BOX_EOD_FLAT, karar anına kadar kapanmış barlarla ve kaydın gözlem mark'ından yeniden oynatılır; strategy_paper
#   defterlerinde gözlemden dolum penceresi en az 1h), veri biriminde ABSENT_UPSTREAM (arşivin hiç yayımlamadığı
#   mark/premium serisi kanıtla etiketlenir: bayat sayılmaz, ayrı listelenir). PR dalının P3a/P3b araştırma kodu da
#   ağaçtadır; gece/veri birimi onu çalıştırmaz. attribution_v1 / cfgrid_v1 mühürleri ve birim dosyaları 44d5353'le AYNI.
#   Yalıtım (yalıtım-AST): config_v3/load_config/sqlite3 yasağı birimlerin yüklediği import grafiğine uygulanır; grafik dışı
#   P3b kabul kodu (parity.py'nin canlı FeesSection okuması; depo sözleşme testinde izinli) ayrı yazılır, KALDI sayılmaz.
#
# ÖN KOŞUL — P2 (44d5353) KURULU: gece + veri birimi dosyaları hedefle (= 44d5353) bayt bayt aynı ve yüklü
#   (NeedDaemonReload=no, gece MemoryMax 768M), veri + gece zamanlayıcısı açık ve aktif, engine-app 44d5353'te ya da onu
#   içeren bir torununda (hedefin torunu DEĞİL). Değilse HİÇBİR ŞEYE DOKUNMADAN durur.
# NE YAPAR: engine-app AYRI klonunu TIP'e sabitler + derler; veri ve gece birimini birer kez ELLE çalıştırır (gece
#   smoke'u S1b/S2'yi, journal/tj_v1/_build.json'da fidelity_v3'ü ve mühürleri kanıtlar). Birim dosyalarına, drop-in'lere
#   ve zamanlayıcılara DOKUNMAZ: daemon-reload YOK, enable/disable YOK. Smoke geçmezse (ya da sabitlemeden sonra
#   hata/sinyal) engine-app önceki sabitine (44d5353) döner; birimler zaten değişmemiştir. --backfill P1b'deki gibidir.
# NE YAPMAZ: worker/dashboard'ı DURDURMAZ, YENİDEN BAŞLATMAZ; app, config.yaml, state, defterler, data/market DEĞİŞMEZ.
#   Motor yalnız data/research'e, betik yalnız engine-app'e ve deploy-logs/engine-*'e yazar.
# İLK GECE: fidelity sürümü günlük girdi özetine girer → işlem günlüğü satırları İLK TAM gecede (A/B AÇIK ya da sürüm
#   günü) BİR KEZ yeniden kurulur (S1B_BACKLOG olabilir, kaldığı yerden sürer); beklenen fidelity ≈ %93–95 (ölçüt ≥ %90).
#   Motor kodu değiştiği için yeni bir 14 gecelik A/B dönemi açılır (§2.9; KAPALI gecelerde S1b/S2 çalışmaz).
# EN ERKEN DAĞITIM: ilk doldurma (tb-engine-backfill) BİTTİKTEN sonra (motor-boşta kapısı sürerken reddeder); başka bir
#   sürümün yeniden başlatmasından (deploy-logs/*-restart-at.txt) en az 1 GÜN sonra (sahip kararı 2026-10-07 "öğrenmeyi
#   şimdi kur"; betik zorlar). 44d5353'ün 14 gecelik A/B'si bitmeden dağıtılırsa UYARI basılır (yeni A/B dönemi, §2.9).
# SAHİBİN ADIMLARI (VPS'te, sırayla; dosya adı tb-engine-<hedefin ilk 7 hanesi>.sh):
#   1) sudo bash tb-engine-c4ba04a.sh --dry-run    # yalnız denetim; hedef kod GEÇİCİ klonda; hiçbir şey değişmez
#   2) sudo bash tb-engine-c4ba04a.sh              # dağıt: engine-app sabiti + iki smoke (birim/reload YOK)
#   3) sudo bash tb-engine-c4ba04a.sh --check      # 14 gün her gün (gece + veri + P2 kabul bölümü, fidelity_v3)
#   4) sudo bash tb-engine-c4ba04a.sh --ab-report  # 14. geceden sonra bir kez (A/B tur etkisi ≤ %5)
#   Geri alma: --rollback (44d5353 durumuna: engine-app 44d5353 + compileall; birim dosyaları aynı, reload YOK; veri
#   birimleri, ilk doldurma ve data/research KALIR). Zincir: sudo bash tb-engine-44d5353.sh --rollback (gece birimi P1a
#   baytları → 79b27cf), sudo bash tb-engine-79b27cf.sh --rollback (veri birimi + ilk doldurma kalkar → P1a), tam kaldırma
#   ardından: sudo bash tb-engine-4962209.sh --rollback
# Çıkış: 0 tamam · 1 durdu (ön denetimde: hiçbir şey değişmedi) · 2 smoke geçmedi ya da saati değil (engine-app önceki
#   sabitine döndü) · 4 dağıtıldı ama bir değişmez KALDI / --rollback YARIM KALDI (neden yazılır; aynı komutla yeniden).
# DİSK: P2.1 yeni klasör eklemez (journal/ satırları yerinde yeniden yazılır); gece birimi boş < 10 GB ya da data/research
#   ≥ 20 GB'ta, veri birimi 13 / 17 GB'ta kendiliğinden durur. BELLEK (§2.5): gece 768M (MemoryHigh 640M; birim 44d5353'le
#   aynı); smoke ve --check memory.peak ≤ 0,8 × 768M (614 MiB) ister. MemoryMax toplamı 7,7 GiB RAM'i zaten aşar (KAYITLI
#   KARAR 2026-10-06; uyarı basılır); OOMScoreAdjust=1000 motoru önce öldürür.
# DAEMON-RELOAD (§9.3): bu sürüm daemon-reload YAPMAZ (birim dosyası değişmez; depodaki worker birimi MemoryMax=4G der, VPS'te
#   6G override vardır). setup_vps_v3.sh ASLA çalıştırılmaz; systemctl set-property / edit / revert / enable / disable YOK.
#   systemd-run (yalnız --backfill) reload yapmaz.
# SAAT: dağıtım, smoke ve kuru çalışma UTC 00:00–04:00 (veri + gece birimi) ve 4h yayın pencerelerinde (hh:00–hh:35, hh
#   4'ün katı; +5 dk pay) BAŞLAMAZ; dağıtım bir sonraki 4h penceresine en az 75 dk kala başlar (koşucu ≈ 15–25 dk + iki
#   smoke); engine-app'e dokunmadan hemen önce ≥ 50 dk, gece smoke'undan önce ≥ 30 dk yeniden denetlenir (S1b/S2 gündüz en
#   çok 8'er dk, günlüğün yeniden kurulumu gece biriminde kaldığı yerden sürer). ÇIKTI dosyaya: sudo bash … > ~/engine-deploy.txt 2>&1
# KAPATMA (anında, veri yerinde kalır): sudo systemctl disable --now tradingbot-engine-night.timer tradingbot-engine-data.timer
#   (önce NeedDaemonReload=no; değilse yalnız stop yeter) · ilk doldurma: sudo systemctl stop tb-engine-backfill.service.
set -Eeuo pipefail

TIP="c4ba04a11fdbbcdce0842fd0bcf36db1eb18e78c"    # P2.1: fidelity_v3 + ABSENT_UPSTREAM (2026-10-12; 44d5353'ün torunu, PR dalında)
T7="${TIP:0:7}"
PREV_TIP="44d535388778163bc687803a73554249920b1e76" # P2 (tb-engine-44d5353.sh): ön koşul ve --rollback tabanı
P1B_TIP="79b27cfb72d8483e8e909cfb26d71d5049398cf2"  # P1b (tb-engine-79b27cf.sh): geri alma zinciri
P1A_TIP="49622093a3d3bc334b454b4df2dcdb4e53eb5144" # P1a (tb-engine-4962209.sh): geri alma zincirinin sonu
BRANCH_REF="refs/heads/claude/gifted-knuth-0ehpcs"
REPO_URL="${TB_ENGINE_REPO_URL:-https://github.com/CoskunerBerke/trading2.git}"   # içerik TAM SHA'ya bağlıdır
SVC_SHA256="573ef9720b4648af2bf6307c5f4953518942dfca18297fe35151a11744ede2da"    # gece .service @TIP = @44d5353 (P2: 640M/768M)
TMR_SHA256="59fa82a34182f1e8c50377d6633191881a2250e640098cf8c369a5cff46b6ed6"    # gece .timer @TIP = @44d5353 (= P1a)
P1A_SVC_SHA256="0db7a26a2a2c955ef3d9cdb83834f816c0556752233a8f312b3ffbbd7ca89b2e" # gece .service P1a (400M/512M; yalnız --check etiketi)
DSVC_SHA256="d6bf310855443cfc383b3bdda7268f3d110c8df353c3799938ba465f1ce56c1b"   # deploy/tradingbot-engine-data.service @TIP = @44d5353
DTMR_SHA256="7522cc40270e5cecf2a65039ba67302329a07866f01fde943adb81c495441c65"   # deploy/tradingbot-engine-data.timer @TIP = @44d5353
ATTR_SHA="914875a1f6631822585ffd5e67e7ffa9a721069e47b6df7a9a624a8a789a6d7f"      # attribution_v1 mührü (P2 not 27; DEĞİŞMEDİ)
CFG_SHA="7c299ae001aee643a76c697ee191026ba6cdb842e21c377d48ed86e0842adf68"       # cfgrid_v1 mührü (P2 not 27; DEĞİŞMEDİ)
FD_VER="fidelity_v3"                                 # fidelity.FIDELITY_VERSION @TIP (P2 not 40)
INV_TESTS=90                                     # bağımsız koşucu: P1a kabul 1–8, 13, 14 + P1b 101–115 + P2 201–213
ENGINE_VER="research_engine_v1_p2"

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; ENG="$BASE/engine-app"; VENV="$BASE/venv"; DATA="$BASE/data"; STATE="$DATA/state"; RES="$DATA/research"
SD="${TRADINGBOT_SYSTEMD_DIR:-/etc/systemd/system}"
SVC_USER="${TRADINGBOT_USER:-tradingbot}"; SVC_GROUP="${TRADINGBOT_GROUP:-$SVC_USER}"
SVC="tradingbot-engine-night.service"; TMR="tradingbot-engine-night.timer"
DSVC="tradingbot-engine-data.service"; DTMR="tradingbot-engine-data.timer"; BF="tb-engine-backfill.service"
DDROPIN="$SD/$DSVC.d/50-tb-engine.conf"; NDROPIN="$SD/$SVC.d/50-tb-engine.conf"   # §2.5 drop-in'leri (44d5353'ten; dokunulmaz)
WORKER="tradingbot-worker.service"; DASH="tradingbot-dashboard.service"
MEM_EXPECT=6442450944; ENGINE_MEM=805306368; DATA_MEM=1073741824   # worker 6G (override) · gece 768M (P2) · veri 1G
DISK_EST=6000000000            # yedek tahmin (universe/<gün>.json yoksa): ~45 sembol parquet ≈ 3,1 + zip ≈ 2,3 + blok ≈ 0,5 GB
DATA_FREE_MIN=13000000000; DATA_RES_MAX=17000000000  # = datastore.DATA_MIN_FREE_BYTES / DATA_REFUSE_RESEARCH_BYTES
LOGDIR="$BASE/deploy-logs"; BASELINE="$LOGDIR/engine-$T7-deploy.json"; SAMPLES="$LOGDIR/engine-$T7-samples.jsonl"
MODE="${1:-deploy}"; SMOKE_TIMEOUT=1800; DSMOKE_TIMEOUT=3300
TMP=""; WHY=""; RB_HINT=""; CHANGED=(); INV_N=0; INV_FAIL=(); ENG_PREV=""; UNDO_NOTE=""; DONE=0; TEE_PID=""
UNITS=("$SVC:$SVC_SHA256" "$TMR:$TMR_SHA256" "$DSVC:$DSVC_SHA256" "$DTMR:$DTMR_SHA256")   # bu sürümde DEĞİŞMEYEN birim dosyaları

say()  { printf '\n== %s\n' "$*"; };          ok()  { printf '   OK  %s\n' "$*"; }
warn() { printf '   UYARI  %s\n' "$*"; };       die() { printf '\nDUR: %s\n' "$1" >&2; exit "${2:-1}"; }
as_svc() { sudo -u "$SVC_USER" "$@"; }
# svc DİZİN KOMUT…: servis kullanıcısı, BOŞ ortam (servis ortamı aktarılmaz), verilen dizinde
svc() { local d="$1"; shift; as_svc env -i -C "$d" PATH=/usr/local/bin:/usr/bin:/bin HOME="$BASE" LANG=C.UTF-8 TZ=UTC \
          PYTHONDONTWRITEBYTECODE=1 "$@"; }
gitx() { local d="$1"; shift; svc "$d" GIT_TERMINAL_PROMPT=0 git "$@"; }
LOW=(nice -n 19); if command -v ionice >/dev/null 2>&1; then LOW+=(ionice -c3 -t); fi   # ağır adımlar: CPU/IO en düşük
if command -v choom >/dev/null 2>&1; then LOW+=(choom -n 1000 --); fi   # ve bellek darlığında önce onlar ölür (§2.5)
sc_show() { systemctl show "$1" -p "$2" --value 2>/dev/null || true; }
sc_state() { systemctl "$1" "$2" 2>/dev/null || true; }
fsha() { if [[ -f "$1" ]]; then sha256sum < "$1" | cut -d' ' -f1; fi; }
cleanup() { if [[ -n "$TMP" && "$TMP" == /tmp/tb-engine-* && -d "$TMP" ]]; then rm -rf -- "$TMP"; fi; }
on_err() { (( BASH_SUBSHELL == 0 )) || exit 1; trap - ERR; set +e   # $(…) içindeki hata: yalnız üst kabuk bir kez işler
           if [[ -n "$TEE_PID" ]] && ! kill -0 "$TEE_PID" 2>/dev/null; then exec >>"$LOG" 2>&1; fi   # kayıt borusu öldüyse: doğrudan kayda
           printf '\nDUR: beklenmeyen hata (satır %s). Bu çalıştırmada değişen: %s\n' "$1" "${CHANGED[*]:-hiçbir şey}" >&2
           if [[ "$MODE" == deploy ]] && (( ! DONE )) && [[ " ${CHANGED[*]} " == *" engine-app="* ]]; then
             back_out >&2; printf '   %s\n' "$(state_msg)" >&2
           elif [[ "$MODE" == --rollback ]]; then printf '   birim dosyalarına dokunulmadı · nedeni giderip AYNI komutla yeniden: sudo bash %s --rollback\n' \
             "$0" >&2
           elif (( ${#CHANGED[@]} )); then printf '   %s\n' "${RB_HINT:-Geri alma (44d5353 durumu): sudo bash $0 --rollback (data/research kalır)}" >&2; fi
           exit 1; }
trap cleanup EXIT
trap 'on_err $LINENO' ERR
if [[ "$MODE" == deploy ]]; then trap 'on_err "$LINENO (sinyal)"' HUP INT TERM; trap '' PIPE; fi   # bağlantı kopması / Ctrl-C: sabitleme
# smoke'suz kalmaz; kopmuş boru (| tail) betiği SIGPIPE ile öldürmez, kayıt (tee -p, HUP/INT'e kapalı) sürer
# inv AD 0|1 AÇIKLAMA — adlandırılmış değişmez. gate: aynı, ama dağıtımda ilk KALDI'da durur (hiçbir şey değişmeden).
inv() {
  INV_N=$((INV_N + 1))
  if [[ "$2" == 0 ]]; then printf '   [tamam] #%02d %-24s %s\n' "$INV_N" "$1" "$3"
  else printf '   [KALDI] #%02d %-24s %s\n' "$INV_N" "$1" "$3"; INV_FAIL+=("$1"); fi
}
gate() { inv "$@"; if [[ "$2" != 0 && "$MODE" =~ ^(deploy|--backfill)$ ]]; then die "$1: $3. HİÇBİR ŞEYE DOKUNULMADI"; fi; }
[[ "$TIP" =~ ^[0-9a-f]{40}$ && "$PREV_TIP" =~ ^[0-9a-f]{40}$ && "$P1B_TIP" =~ ^[0-9a-f]{40}$ && "$P1A_TIP" =~ ^[0-9a-f]{40}$ ]] \
  || die "TIP / PREV_TIP / P1B_TIP / P1A_TIP tam bir commit SHA'sı değil"
[[ "$MODE" =~ ^(deploy|--dry-run|--check|--ab-report|--rollback|--backfill)$ ]] \
  || die "bilinmeyen seçenek: $MODE (--dry-run | --check | --ab-report | --backfill | --rollback | seçeneksiz = dağıt)"
if [[ "$(id -u)" != 0 && -z "${TB_ENGINE_ALLOW_NON_ROOT:-}" ]]; then die "root olarak çalıştırın: sudo bash $0 $*"; fi
# time_ok [EN_AZ_DK] — 0: dağıtım/smoke için uygun saat (UTC); değilse 1 ve WHY. Veri + gece birimi penceresi 00:00–04:00;
# 4h yayın pencereleri hh:00–hh:35 (+5 dk pay); EN_AZ_DK: bir sonraki 4h penceresine en az bu kadar dakika kalmalı.
time_ok() {
  local hm h m left
  hm="$(date -u +%H:%M)"; h=$((10#${hm%%:*})); m=$((10#${hm##*:}))
  if (( h < 4 )); then WHY="UTC $hm — veri + gece birimi penceresi (00:00–04:00 UTC)"; return 1; fi
  if (( h % 4 == 0 && m < 40 )); then WHY="UTC $hm — 4h yayın penceresi (hh:00–hh:35, +5 dk pay)"; return 1; fi
  left=$(( (4 - h % 4) * 60 - m ))
  if (( left < ${1:-0} )); then WHY="UTC $hm — sonraki 4h penceresine $left dk (en az ${1} dk gerekir)"; return 1; fi
  WHY="UTC $hm"; return 0
}
# units_ok — bu sürümün birim değişmezi (§9.3): birim dosyası YAZILMAZ, daemon-reload YAPILMAZ. 0: dört birim dosyası
# sabitlenmiş baytlarda (= 44d5353 = hedef), dördü de yüklü (NeedDaemonReload=no), yüklü MemoryMax gece 768M / veri 1G;
# değilse 1 ve WHY.
units_ok() {
  local p u v; WHY=""
  for p in "${UNITS[@]}"; do
    u="${p%%:*}"; v="$(fsha "$SD/$u")"
    if [[ "$v" != "${p##*:}" ]]; then
      if [[ -n "$v" ]]; then v="sha256 ${v:0:12}…"; else v="dosya yok"; fi
      WHY="$u baytları 44d5353'ün (= hedefin) değil ($v)"; return 1; fi
  done
  for u in "$SVC" "$TMR" "$DSVC" "$DTMR"; do
    v="$(sc_show "$u" NeedDaemonReload)"
    if [[ "$v" != no || "$(sc_show "$u" LoadState)" != loaded ]]; then
      WHY="$u yüklü değil ya da NeedDaemonReload=${v:-?} (bu sürüm daemon-reload YAPMAZ; bana iletin)"; return 1; fi
  done
  if [[ "$(sc_show "$SVC" MemoryMax)" != "$ENGINE_MEM" || "$(sc_show "$DSVC" MemoryMax)" != "$DATA_MEM" ]]; then
    WHY="yüklü MemoryMax gece $(sc_show "$SVC" MemoryMax) / veri $(sc_show "$DSVC" MemoryMax) (beklenen $ENGINE_MEM / $DATA_MEM)"; return 1; fi
  return 0
}
# Gömülü Python araçları (yalnız standart kütüphane; root olarak `python -I`, depo kodu İÇE ALINMAZ).
read -r -d '' PYTOOL <<'PY' || true
import ast, hashlib, json, math, os, pwd, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
STAGES = ("S0", "S1s", "S1a", "S3", "S1b", "S2", "S7", "S7b")     # P2 tam plan: S0 S1a S3 S1b S2 S7 S7b (not 19)
ACCEPT = [1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 113, 114, 115,
          201, 205, 207, 208, 211, 212, 213]
DIGEST_MAX, QUERY_MAX = 8192, 150                                 # §10 P2 kabul 12
RUN_RE = r"^\d{8}T\d{6}Z(-\d+)?$"
def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
def ep(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp()
def iv(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
def f(x, nd=1):
    return "—" if x is None else ("%.*f" % (nd, x)).replace(".", ",")
def pct(v, q):
    v = sorted(v)
    return v[max(0, math.ceil(q * len(v)) - 1)] if v else None
def mark(flag):
    return {None: "[ölçülemedi]", True: "[DİKKAT]", False: "[tamam]", "bilgi": "[bilgi]"}[flag]
def line(flag, txt):
    print("   %-13s %s" % (mark(flag), txt))
def mb(rr):     # tepe bellek; veri/doldurmada + MemoryHigh kısması (memory.events high)
    hi = "" if (rr or {}).get("memory_high_events") is None else " · high %d" % rr["memory_high_events"]
    return ("%s MB (%s × MemoryMax)%s" % (f(rr["memory_peak_bytes"] / 1048576, 0), f(rr.get("memory_peak_ratio"), 2), hi)
            if (rr or {}).get("memory_peak_bytes") else "—")
def runs(res):
    d = os.path.join(res, "runs")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(RUN_RE, n))
    except OSError:
        return []
    return [r for r in (rj(os.path.join(d, n, "run_status.json")) for n in names) if isinstance(r, dict)]
def druns(res, since=0.0):
    """Veri birimi / ilk doldurma çalıştırmaları (runs/data/<id>/data_run.json); `_end` = bitiş ya da son yazım."""
    d, out = os.path.join(res, "runs", "data"), []
    try:
        names = sorted(n for n in os.listdir(d) if re.match(RUN_RE, n))
    except OSError:
        return []
    for n in names:
        p = os.path.join(d, n, "data_run.json")
        r = rj(p)
        if isinstance(r, dict) and (ep(r.get("started_at")) or 0) >= float(since):
            r["_end"] = ep(r.get("finished_at")) or os.path.getmtime(p)
            out.append(r)
    return out
def epoch(res, tip):
    """Hedef SHA'nın A/B dönem satırı (dönem = motor kod özeti; aynı kodla yeniden sabitlenen SHA eski dönemdedir)."""
    try:
        with open(os.path.join(res, "runs", "engine_epochs.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
    except (OSError, ValueError):
        return None
    return next((r for r in rows if r.get("engine_sha") == tip), None)
def release_day(res, tip):
    r = epoch(res, tip) or {}
    return r.get("epoch_day") or r.get("first_seen_day")
def mine(res, tip):
    """Bu sürümün (aynı motor kodunun) çalıştırmaları."""
    code = (epoch(res, tip) or {}).get("code_hash")
    return [r for r in runs(res) if (r.get("shas") or {}).get("engine") == tip
            or (code and (r.get("shas") or {}).get("engine_code") == code)]
def jscan(since, until=None, unit="tradingbot-worker.service"):
    """Worker günlüğü (short-unix, akışla): tur süreleri + olay sayıları; okunamazsa None. METİN/JSON ayrı sayılır, büyüğü alınır."""
    cmd = ["journalctl", "-u", unit, "--since", "@%d" % since, "-o", "short-unix", "--no-pager", "-q"]
    if until is not None:
        cmd[5:5] = ["--until", "@%d" % until]
    try:
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                              errors="replace")
    except OSError:
        return None
    tours, cnt = [], {}
    for ln in pr.stdout:
        p = ln.rstrip("\n").split(" ", 2)
        if len(p) < 3 or ": " not in p[2]:
            continue
        msg = p[2].split(": ", 1)[1]
        m = re.search(r"ÖĞRENME: .* ([0-9]+(?:\.[0-9]+)?)s$", msg)
        if m:
            tours.append(float(m.group(1)))
        k = "j" if msg.startswith('{"ts"') else "t"
        for name, rx in (("rest418429", r"(^| )(429|418) https?://"), ("research_active", r"PAPER_RESEARCH_ACTIVE"),
                         ("tour_error", r"İzleme turu hatası")):
            if re.search(rx, msg):
                cnt[(name, k)] = cnt.get((name, k), 0) + 1
    out = {n: max(cnt.get((n, "t"), 0), cnt.get((n, "j"), 0)) for n in ("rest418429", "research_active", "tour_error")}
    out["tours"] = tours
    return out if pr.wait() == 0 else None
def dwin(rs, lo=0.0, hi=None):
    """Veri birimi / ilk doldurma pencerelerinde worker 418/429 (§2.9; VPS kabul 5). Dönen (sayı, pencere, okunamayan)."""
    hits = n = bad = 0
    for r in rs:
        s, e = max(ep(r.get("started_at")) or 0, lo), min(r["_end"] + 60, hi or 1e18)
        if e > s:
            js = jscan(s, e)
            n, bad = n + 1, bad + (js is None)
            hits += (js or {}).get("rest418429", 0)
    return hits, n, bad
def c_paper(state):
    p = os.path.join(state, "mode.json")
    if not os.path.exists(p):
        return print("state/mode.json yok → worker varsayılanı PAPER") or 0
    d = rj(p)
    if not isinstance(d, dict):
        return print("state/mode.json OKUNAMADI") or 1
    print("mode=%s live_order_path_enabled=%s" % (d.get("mode"), d.get("live_order_path_enabled")))
    return 0 if d.get("mode") == "PAPER" and d.get("live_order_path_enabled") is not True else 1
def c_runner(path, n):
    d = rj(path) or {}
    print("%s geçti · %s kaldı · %s atlandı · kabul %s" % (d.get("passed"), d.get("failed"), d.get("skipped"),
                                                          d.get("acceptance_passed")))
    return 0 if (d.get("failed"), d.get("skipped"), d.get("passed"), d.get("acceptance_passed")) == (0, 0, int(n), ACCEPT) else 1
def _imports(node, top_only):
    for ch in ast.iter_child_nodes(node):
        if top_only and isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(ch, ast.Import):
            yield from (a.name for a in ch.names)
        elif isinstance(ch, ast.ImportFrom):
            mod = "." * ch.level + (ch.module or "")
            yield mod
            yield from (mod + "." + a.name for a in ch.names)
        yield from _imports(ch, top_only)
#: birimlerin ve durum komutlarının yüklediği motor modülleri: cli_v3 işleyicilerinin tembel import'ları (+ paket __init__'i)
UNIT_ENTRY = ("__init__", "night", "datastore", "summary", "daily_target", "query", "backup", "paths")
def _graph(red, starts):
    """Paket içi (göreli, seviye 1) import grafiği; fonksiyon içi import'lar dahil (üst küme;
    test_research_engine_p1b_contract._graph ile aynı kural)."""
    seen, todo = set(), list(starts)
    while todo:
        m = todo.pop()
        p = os.path.join(red, m + ".py")
        if m in seen or not os.path.isfile(p):
            continue
        seen.add(m)
        with open(p, encoding="utf-8") as fh:
            t = ast.parse(fh.read())
        for n in ast.walk(t):
            if isinstance(n, ast.ImportFrom) and n.level == 1:
                todo += [n.module.split(".")[0]] if n.module else [
                    a.name if os.path.isfile(os.path.join(red, a.name + ".py")) else "__init__" for a in n.names]
    return seen
def c_ast(tree):
    """§2.8 yalıtımı: birimlerin yüklediği grafikte config_v3/load_config/sqlite3 YOK; ağ modülü yalnız datastore/selfcheck
    (bütün modüllerde); cli üst düzeyde motoru import etmez. P2.1: PR dalının P3 araştırma/kabul kodu (ör. parity.py'nin
    canlı FeesSection okuması; depo sözleşme testinde izinli) birim grafiğinin DIŞINDAYSA KALDI sayılmaz, ayrı yazılır."""
    bad, aside = [], []
    red = os.path.join(tree, "tradingbot", "research_engine")
    unit = _graph(red, UNIT_ENTRY)
    for fn in sorted(os.listdir(red)):
        if fn.endswith(".py"):
            with open(os.path.join(red, fn), encoding="utf-8") as fh:
                t = ast.parse(fh.read())
            cfg = ["%s: %s" % (fn, n) for n in _imports(t, False)
                   if n.split(".")[0] == "sqlite3" or "config_v3" in n or n.rsplit(".", 1)[-1] in ("load_config", "load_v3")]
            (bad if fn[:-3] in unit else aside).extend(cfg)
            bad += ["%s: ağ modülü %s" % (fn, n) for n in _imports(t, False) if fn not in ("datastore.py", "selfcheck.py")
                    and n.split(".")[0] in ("urllib", "http", "socket", "requests", "ssl", "aiohttp", "httpx")]
    for fn in ("cli.py", "cli_v3.py"):
        with open(os.path.join(tree, "tradingbot", fn), encoding="utf-8") as fh:
            t = ast.parse(fh.read())
        bad += ["%s (üst düzey): %s" % (fn, n) for n in _imports(t, True) if "research_engine" in n]
    out = "birim grafiğinde (%d modül) yasak import yok" % len(unit) if not bad else "YASAK: " + "; ".join(bad[:5])
    out += ("; grafik dışı araştırma kodu, birimler yüklemez: " + ", ".join(aside[:3]) + (" …" if len(aside) > 3 else "")) if aside else ""
    print(out)
    return 1 if bad else 0
def c_lastrun(res):
    print((rj(os.path.join(res, "summary", "run_status.json")) or {}).get("run_id") or "")
    return 0
def c_dlast(res):
    print(((rj(os.path.join(res, "summary", "data_status.json")) or {}).get("last_run") or {}).get("run_id") or "")
    return 0
def owned(res, user, bad):
    uid, foreign = pwd.getpwnam(user).pw_uid, []
    for dp, dn, fn in os.walk(res):
        foreign += [os.path.join(dp, x) for x in dn + fn if os.lstat(os.path.join(dp, x)).st_uid != uid]
    if foreign:
        bad.append("%d dosya/klasör %s'a ait değil (ör. %s)" % (len(foreign), user, foreign[0]))
def c_smoke(res, tip, pre, t0, user, ver, attr, cfg, emem, fdv):
    st = rj(os.path.join(res, "summary", "run_status.json")) or {}
    rid, sc, stg, rr, bad = st.get("run_id"), st.get("selfcheck") or {}, st.get("stages") or {}, st.get("resources") or {}, []
    print("   gece smoke: %s · sonuç %s (çıkış %s) · plan %s · data_seal %s · tepe bellek %s · bayraklar %s" % (
        rid, st.get("result"), st.get("exit_code"), " ".join(st.get("plan") or []), str(st.get("data_seal"))[:12], mb(rr),
        ", ".join(st.get("flags") or []) or "—"))
    for s in ("S1b", "S2"):
        x = stg.get(s) or {}
        j = (x.get("result") or {}).get("journal") or x.get("result") or {}
        print("     %-3s %s · %s sn · %s satır · bu çalıştırmada %s kuruldu · bekleyen %s" % (
            s, x.get("status"), f(x.get("duration_s"), 0), j.get("rows"), j.get("built_rows"), j.get("pending")))
    print("     (gündüz smoke'unda S1b/S2 en çok 8'er dk; ilk tam kurulum gece biriminde kaldığı yerden sürer — beklenir)") \
        if {"S1B_BACKLOG", "S2_BACKLOG"} & set(st.get("flags") or []) else None
    for k, v in sorted(((sc.get("isolation") or {}).get("checks") or {}).items()):
        print("     öz-denetim %-26s %s %s" % (k, v.get("status"), v.get("errno") or ""))
    bad += ["smoke'un yeni run_status.json'u yok"] if (not rid or rid == pre or (ep(st.get("started_at")) or 0) < float(t0) - 5) else []
    bad += ["öz-denetim %s" % sc.get("status")] if sc.get("status") != "OK" else []
    bad += ["motor sürümü %s ≠ %s" % (st.get("engine"), ver)] if st.get("engine") != ver else []
    # Sürüm günü tam plan (SUCCESS) ve S1b + S2 planda. Aynı kodun A/B penceresinde YENİDEN dağıtımda KAPALI gün kuralı
    # smoke'ta da uygulanır (S0 + S1s): sonuç AB_OFF kabul, ama P2 dosyaları + mühürler bu kodun önceki çalıştırmasından şart.
    day, plan, ab = str(st.get("started_at"))[:10], st.get("plan") or [], (sc.get("ab") or {}).get("status")
    okres = st.get("result") == "SUCCESS" or (st.get("result") == ab and ab in ("AB_OFF", "AB_OFF_ZORUNLU_ARŞİV"))
    if not okres or st.get("exit_code") != 0:
        bad.append("sonuç %s" % st.get("result"))
    if st.get("result") == "SUCCESS":
        bad += ["planda %s yok (P2 aşaması çalışmadı)" % s for s in ("S1b", "S2") if s not in plan]
    for s in plan:
        if (stg.get(s) or {}).get("status") != "OK":
            bad.append("aşama %s: %s" % (s, (stg.get(s) or {}).get("status")))
    bad += ["engine SHA %s ≠ hedef" % (st.get("shas") or {}).get("engine")] if (st.get("shas") or {}).get("engine") != tip else []
    seal = (rj(os.path.join(res, "summary", "data_status.json")) or {}).get("data_seal")
    bad += ["S0 veri mührü %s ≠ data_status %s" % (st.get("data_seal"), seal)] if st.get("data_seal") != seal else []
    want = {"S0": ["summary/run_status.json", "runs/%s/run_status.json" % rid, "runs/engine_epochs.jsonl"],
            "S1s": ["snapshots/%s.json.gz" % day], "S1a": ["snapshots/%s.json.gz" % day], "S3": ["summary/daily_target.json"],
            "S1b": ["journal/tj_v1/_build.json", "journal/tj_v1/_index.json.gz"],
            "S2": ["attribution/_build.json", "attribution/_index.json.gz"],
            "S7": ["summary/digest_tr.md", "summary/engine_summary.json"],
            "S7b": ["backup/research-small-%s.tar.gz" % day, "backup/research-small-%s.tar.gz.sha256" % day]}
    need = [n for s in STAGES if s in plan or s in ("S0", "S1b", "S2", "S7") for n in want[s]]
    bad += ["beklenen dosya yok: " + n for n in need if not os.path.isfile(os.path.join(res, n))]
    dg = os.path.join(res, "summary", "digest_tr.md")
    bad += ["digest_tr.md %d bayt > 8 KB" % os.path.getsize(dg)] if os.path.isfile(dg) and os.path.getsize(dg) > DIGEST_MAX else []
    ab_, s2r = rj(os.path.join(res, "attribution", "_build.json")) or {}, (stg.get("S2") or {}).get("result") or {}
    bad += ["%s %s mührü %s ≠ sabit %s" % (src, name, str(d.get(key))[:12], pin[:12]) for src, d in (
        ("attribution/_build.json", ab_), ("S2 sonucu", s2r)) for name, key, pin in (
        ("attribution_v1", "attribution_sha", attr), ("cfgrid_v1", "cfgrid_sha", cfg)) if d and d.get(key) != pin]
    bad += ["attribution/_build.json okunamadı (mühürler denetlenemedi)"] if not ab_ else []
    # P2.1: S1b bu çalıştırmada koştuysa `_build.json`'un fidelity özeti hedefin sürümünü taşır (girdi özetine giren sürüm:
    # satırlar yeniden kurulur; bekleyen kısım gece biriminde sürer). S1b yoksa (A/B KAPALI gün) ölçülemez, KALDI sayılmaz.
    fb, s1 = (rj(os.path.join(res, "journal", "tj_v1", "_build.json")) or {}).get("fidelity") or {}, stg.get("S1b") or {}
    s1j = (s1.get("result") or {}).get("journal") or {}
    if "S1b" in plan and s1.get("status") == "OK":
        print("     fidelity: journal/tj_v1/_build.json %s · %s/%s = %%%s · bu çalıştırmada %s satır yeniden kuruldu, bekleyen %s%s" % (
            fb.get("version"), fb.get("ok", "—"), fb.get("eligible", "—"),
            f(fb["rate"] * 100, 1) if isinstance(fb.get("rate"), (int, float)) else "—", s1j.get("built_rows"), s1j.get("pending"),
            " (gece biriminde kaldığı yerden sürer)" if s1j.get("pending") else ""))
        bad += ["fidelity sürümü %s ≠ %s (journal/tj_v1/_build.json)" % (fb.get("version"), fdv)] if fb.get("version") != fdv else []
    else:
        print("     fidelity: bu çalıştırmada S1b yok (%s) — %s ilk tam gecede görünür (ölçülemedi, KALDI sayılmaz)" % (
            st.get("result"), fdv))
    mx, pk = rr.get("memory_max_bytes"), rr.get("memory_peak_ratio")
    bad += ["gece cgroup memory.max %s ≠ %s (P2 birimi yüklenmedi mi?)" % (mx, emem)] if mx is not None and iv(mx) != iv(emem) else []
    bad += ["memory.peak %s × MemoryMax > 0,8" % f(pk, 2)] if isinstance(pk, (int, float)) and pk > 0.8 else []
    bad += ["runs/engine_epochs.jsonl hedef SHA'yı içermiyor"] if release_day(res, tip) is None else []
    owned(res, user, bad)
    print("".join("     KALDI: %s\n" % b for b in bad), end="")
    return 1 if bad else 0
def c_dsmoke(res, pre, t0, user, ver):
    """Veri birimi smoke'u (P1b): yeni çalıştırma SUCCESS/0, öz-denetim OK, worker günlüğü OKUNDU (UNKNOWN değil), mühür, evren."""
    ds = rj(os.path.join(res, "summary", "data_status.json")) or {}
    lr, bad = ds.get("last_run") or {}, []
    rid, rest = lr.get("run_id"), lr.get("rest") or {}
    dr = rj(os.path.join(res, "runs", "data", str(rid), "data_run.json")) or {}
    iso = (dr.get("selfcheck") or {}).get("isolation") or {}
    print("   veri smoke: %s · %s · sonuç %s (çıkış %s) · REST koruması %s · istek %s · tepe bellek %s · bayraklar %s" % (
        rid, lr.get("mode"), lr.get("result"), lr.get("exit_code"), rest.get("guard"), rest.get("requests"),
        mb(dr.get("resources")), ", ".join(lr.get("flags") or []) or "—"))
    for k, v in sorted((iso.get("checks") or {}).items()):
        print("     öz-denetim %-26s %s %s" % (k, v.get("status"), v.get("errno") or ""))
    bad += ["smoke'un yeni data_status kaydı yok"] if (not rid or rid == pre or (ep(lr.get("started_at")) or 0) < float(t0) - 5) else []
    if (lr.get("mode"), lr.get("result"), lr.get("exit_code")) != ("update", "SUCCESS", 0):
        bad.append("sonuç %s %s (çıkış %s)" % (lr.get("mode"), lr.get("result"), lr.get("exit_code")))
    bad += ["motor sürümü %s ≠ %s" % (dr.get("engine"), ver)] if dr.get("engine") != ver else []
    bad += ["öz-denetim %s %s" % (iso.get("status"), iso.get("broken") or "")] if iso.get("status") != "OK" else []
    if ((iso.get("checks") or {}).get("socket_denied") or {}).get("status") != "NOT_APPLICABLE":
        bad.append("veri biriminde soket denemesi NOT_APPLICABLE olmalı")
    if rest.get("guard") not in ("REST_GUARD_OK", "REST_GUARD_SKIP"):
        bad.append("worker günlüğü okunamadı (%s: %s) — SupplementaryGroups=systemd-journal?" % (
            rest.get("guard"), ((rest.get("journal") or {}).get("error") or "")[:120]))
    bad += ["mühür bu çalıştırmanın değil"] if (ds.get("sealed_run_id") != rid or not ds.get("data_seal")) else []
    day = str(lr.get("started_at"))[:10]
    bad += ["universe/%s.json yok" % day] if not os.path.isfile(os.path.join(res, "universe", day + ".json")) else []
    if rest.get("guard") == "REST_GUARD_OK" and not os.path.isfile(os.path.join(res, "exchangeinfo", day + ".json.gz")):
        bad.append("exchangeinfo/%s.json.gz yok" % day)
    owned(res, user, bad)
    print("".join("     KALDI: %s\n" % b for b in bad), end="")
    return 1 if bad else 0
def c_sample(state, samples, nr, pid, memmax):
    bt = rj(os.path.join(state, "box_timer.json")) or {}
    pm = rj(os.path.join(state, "protective_monitor.json")) or {}
    tours = drej = dvm = 0
    for n in (sorted(os.listdir(state)) if os.path.isdir(state) else []):
        p = os.path.join(state, n)
        if not n.endswith(".json") or not os.path.isfile(p) or os.path.getsize(p) > 20_000_000:
            continue
        d = rj(p)
        if isinstance(d, dict) and isinstance(d.get("counters"), dict) and isinstance(d.get("rejections"), dict):
            tours += iv(d["counters"].get("tours")) or 0
            drej += iv(d["counters"].get("data_rejected")) or 0
            dvm += iv(d["rejections"].get("DATA_VERDICT_MISSING")) or 0
    s = {"t": int(time.time()), "box_missed": iv(bt.get("missed_bars")), "box_evals": iv(bt.get("evaluations")),
         "box_lag_p50": bt.get("lag_p50_s"), "box_lag_max": bt.get("lag_max_s"), "pm_runs": iv(pm.get("runs")),
         "pm_p50": pm.get("duration_p50_s"), "pm_max": pm.get("duration_max_s"),
         "pm_last_at": (pm.get("last_run") or {}).get("at"), "tours": tours, "data_rejected": drej, "dvm": dvm,
         "nrestarts": iv(nr), "worker_pid": iv(pid), "memmax": iv(memmax)}
    try:
        with open(samples, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    except OSError as exc:
        print("   ölçüm örneği yazılamadı: %s" % exc)
    return s
def c_baseline(path, tip, app_sha, nr, pid, memmax, rid, drid, t_start, state, samples):
    b = {"schema": "tb_engine_deploy_v1", "tip": tip, "app_sha": app_sha, "deployed_at": int(time.time()), "started_at": iv(t_start),
         "worker": {"nrestarts": iv(nr), "pid": iv(pid), "memmax": iv(memmax)}, "smoke_run_id": rid, "data_smoke_run_id": drid}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(b, indent=1) + "\n")
    c_sample(state, samples, nr, pid, memmax)
    return 0
def last_backup(res):
    bdir = os.path.join(res, "backup")
    arcs = sorted(x for x in (os.listdir(bdir) if os.path.isdir(bdir) else []) if re.match(r"^research-small-\d{4}-\d\d-\d\d\.tar\.gz$", x))
    if not arcs:
        return None, None, None
    try:
        want = open(os.path.join(bdir, arcs[-1] + ".sha256"), encoding="utf-8").read().split()[0]
        h = hashlib.sha256()
        with open(os.path.join(bdir, arcs[-1]), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        good = h.hexdigest() == want
    except (OSError, IndexError):
        good = False
    return arcs[-1], good, (time.time() - datetime.strptime(arcs[-1][15:25], "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) / 86400
def c_bkok(res):
    name, good, age = last_backup(res)
    print("son araştırma yedeği %s · sha256 %s · %s gün önce" % (name or "YOK", {None: "—", True: "doğrulandı", False: "TUTMADI"}[good],
                                                              f(age, 1)))
    return 0 if good else 1
def c_abwin(res, tip, label):
    rel = release_day(res, tip)
    if rel is None:
        return print("%s sürüm günü kaydı yok (runs/engine_epochs.jsonl)" % label) or 3
    x = (time.time() - datetime.strptime(rel, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) / 86400 - 220 / 1440
    n = max(0, min(14, math.floor(x)))     # gece i (1–14) 03:40'ta biter
    print("%s A/B penceresi (sürüm günü %s): %d/14 gece geçti" % (label, rel, n))
    return 0 if n >= 14 else 3
def c_check(res, state, tip, baseline, samples, memmax, nr, pid):
    b = rj(baseline) or {}
    allr = mine(res, tip)
    rs = allr[-15:]
    print("-- son gece çalıştırmaları (bu sürüm; en çok 15)")
    for r in rs:
        print("   %-18s %-22s plan %-17s A/B %-15s tepe bellek %s · veri mührü %s" % (
            r.get("run_id"), r.get("result"), " ".join(r.get("plan") or []),
            ((r.get("selfcheck") or {}).get("ab") or {}).get("status"), mb(r.get("resources")), str(r.get("data_seal"))[:12]))
    print("   (bu sürümün çalıştırması yok)") if not rs else None
    print("-- P1a VPS kabul ölçütleri, bu sürümle (§10 P1a; P1b kabul 5; [DİKKAT] = bana iletin)")
    sm = next((r for r in rs if r.get("run_id") == b.get("smoke_run_id")), None)
    line(None if sm is None else sm.get("result") != "SUCCESS", "K1 elle smoke (gece): %s · veri: %s" % (
        "%s %s" % (b.get("smoke_run_id"), sm.get("result")) if sm else "kaydı yok", b.get("data_smoke_run_id") or "kaydı yok"))
    inc = sum(1 for r in rs if ((((r.get("stages") or {}).get("S1a") or {}).get("result") or {}).get("inconsistent")))
    iso_ = sum(1 for r in rs if r.get("result") == "ISOLATION_BROKEN")
    skew = sum(1 for r in rs if "SKEW" in (r.get("flags") or []) or r.get("result") == "SKEW")
    fail = sum(1 for r in rs if r.get("result") in ("FAILED", "NOT_PAPER", "DISK_REFUSE"))
    stale = sum(1 for r in rs if "DATA_STALE" in (r.get("flags") or []))
    line(bool(inc or iso_ or skew or fail) if rs else None, "K3 INCONSISTENT %d · ISOLATION_BROKEN %d · SKEW %d · "
         "FAILED/NOT_PAPER/DISK %d · DATA_STALE %d (bilgi; ilk doldurma bitene dek beklenir)" % (inc, iso_, skew, fail, stale))
    pk = [x for x in ((r.get("resources") or {}).get("memory_peak_ratio") for r in rs) if isinstance(x, (int, float))]
    line(max(pk) > 0.8 if pk else None, "K5 gece memory.peak en çok %s × MemoryMax (ölçüt ≤ 0,8; P2 MemoryMax 768M)" % f(max(pk) if pk else None, 2))
    name, good, age = last_backup(res)
    n7b = sum(1 for r in rs if ((r.get("stages") or {}).get("S7b") or {}).get("status") == "OK")
    line(None if good is None else not good, "K7 araştırma yedeği: son %s sha256 %s · S7b OK çalıştırma %d" % (
        name or "—", {None: "—", True: "doğrulandı", False: "TUTMADI"}[good], n7b))
    s1 = lambda r: ((r.get("stages") or {}).get("S1a") or {}).get("result") or ((r.get("stages") or {}).get("S1s") or {}).get("result")
    last = next((r for r in reversed(rs) if s1(r)), None)
    lr = s1(last) if last else {}
    fl = set(lr.get("flags") or []) & {"LEDGER_STALE", "NO_LEDGERS", "LEDGER_MISSING"}
    miss = [k for k, v in (lr.get("books") or {}).items() if v.get("status") != "OK"]
    age_ = (lr.get("ledger_freshness") or {}).get("age_s")
    line(bool(miss or fl) if lr else None, "defter okuma: %s · en yeni updated_at %s sa önce · %s" % (
        ", ".join(sorted(fl)) or "bayrak yok", f(age_ / 3600.0 if age_ is not None else None, 1),
        ("sorunlu: " + ", ".join(miss)) if miss else "hepsi OK"))
    rot = []
    for k, v in ((lr or {}).get("books") or {}).items():
        rot += [(d, "%s/%s" % (k, kk)) for kk, d in (v.get("rotation_days") or {}).items() if isinstance(d, (int, float))]
    rmin = min(rot) if rot else (None, "—")
    line(rmin[0] < 3 if rot else None, "K8 rotasyon payı en az %s gün (%s; ölçüt ≥ 3)" % (f(rmin[0], 1), rmin[1]))
    js = jscan(max(b.get("deployed_at") or 0, int(time.time()) - 86400)) or {}
    line(js["rest418429"] > 0 if js else None, "418/429 (worker günlüğü, son 24 sa) %s · PAPER_RESEARCH_ACTIVE %s (raporlanır) · "
         "tur hatası %s" % (js.get("rest418429", "—"), js.get("research_active", "—"), js.get("tour_error", "—")))
    w = b.get("worker") or {}
    line((iv(memmax) != 6442450944) or (iv(nr) != w.get("nrestarts") if w else None),
         "worker: NRestarts dağıtımda %s → şimdi %s · MemoryMax %s (6G = 6442450944) · PID %s → %s" % (
             w.get("nrestarts"), nr, memmax, w.get("pid"), pid))
    c_sample(state, samples, nr, pid, memmax)
    return 0
gb = lambda x: f(iv(x) / 1e9, 1) if iv(x) is not None else "—"  # noqa: E731
def bf_view(res, bf_state, me="tb-engine-<sha7>.sh"):
    """İlk doldurma durumu: (bayrak, metin, tamamlandı mı)."""
    ds = rj(os.path.join(res, "summary", "data_status.json")) or {}
    run = ds.get("running") or {}
    bfs = [r for r in druns(res) if r.get("mode") == "backfill"]
    if bf_state in ("active", "activating", "reloading", "deactivating"):
        p = (run.get("progress") or {}) if run.get("mode") == "backfill" else {}
        dk = p.get("disk") or {}
        return None, "SÜRÜYOR (%s) · aşama %s · %s/%s görev · son 1 sa hızı %s/sa · tahmini bitiş %s%s%s" % (
            run.get("run_id") or "başlıyor", p.get("phase") or "—", p.get("done", "—"), p.get("total", "—"),
            p.get("rate_per_h_1h") or "—", str(p.get("eta") or "—")[:16],
            " · DURAKLADI (4h penceresi, %s'e kadar)" % str(p.get("resume_at"))[11:16] if p.get("paused") else "",
            " · disk (doldurmanın ölçümü %s): boş %s GB, araştırma ≈ %s GB" % (str(dk.get("at"))[11:16], gb(dk.get("free_bytes")),
                                                                         gb(dk.get("research_bytes"))) if dk else ""), False
    if not bfs:
        return None, "başlatılmadı (sudo bash %s --backfill)" % me, False
    z = bfs[-1]
    if z.get("result") == "SUCCESS":
        return False, "TAMAMLANDI %s (%s → %s)" % (z.get("run_id"), str(z.get("started_at"))[:16],
                                                 str(z.get("finished_at"))[:16]), True
    return True, "%s %s — bitmedi; kaldığı yerden: sudo bash %s --backfill%s" % (
        z.get("run_id"), z.get("result") if z.get("result") != "RUNNING" else "DURDU (süreç yok)", me,
        (" · " + str(z.get("error") or z.get("reason") or "")[:100]) if (z.get("error") or z.get("reason")) else ""), False
EST_B = {"5m": 40e6, "15m": 14e6, "1h": 5e6, "4h": 1.5e6, "1d": 0.5e6, "funding": 1e6, "metrics_5m": 60e6,
         "markpx_1h": 5e6, "premium_1h": 5e6, "1m": 30e6}
#: `series_short` (son 400 gün) ince serileri: 5m 40e6 × 400/2594 ≈ 6,2e6 · 15m ≈ 2,2e6 · metrics 60e6 × 400/1772 ≈ 13,5e6; yukarı
EST_SHORT = {"5m": 8e6, "15m": 3e6, "metrics_5m": 15e6}
def c_est(res, fallback):
    """--backfill disk kapısı ve V7: smoke'un en son universe/<gün>.json seri listesinden (seri başına parquet + zip + blok)."""
    d = os.path.join(res, "universe")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(r"^\d{4}-\d\d-\d\d\.json$", n))
    except OSError:
        names = []
    for n in reversed(names):
        doc = rj(os.path.join(d, n)) or {}
        keys, short = doc.get("series"), doc.get("series_short")
        short = short if isinstance(short, dict) else {}
        if isinstance(keys, list) and keys:
            def b(k):
                kind = str(k).rsplit("/", 1)[-1]
                return EST_SHORT.get(kind, EST_B.get(kind, 5e6)) if k in short else EST_B.get(kind, 5e6)
            print("%d %d universe/%s, %d kısa" % (int(sum(b(k) for k in keys)), len(keys), n, sum(1 for k in keys if k in short)))
            return 0
    print("%d 0 sabit-tahmin" % int(fallback))
    return 0
def c_bf(res, bf_state, peak, dmem, me="tb-engine-<sha7>.sh", high=""):
    flag, txt, _done = bf_view(res, bf_state, me)
    line(flag, "ilk doldurma: " + txt)
    if iv(peak):
        line(iv(peak) > 0.8 * int(dmem), "tb-engine-backfill cgroup memory.peak %s MB (≤ 0,8 × %s MB) · MemoryHigh kısması "
             "(memory.events high) %s" % (f(iv(peak) / 1048576, 0), f(int(dmem) / 1048576, 0), iv(high) if iv(high) is not None else "—"))
    return 0
def c_dcheck(res, tip, baseline, res_b, dmem, bf_state, peak, est, free_b="", me="tb-engine-<sha7>.sh", bf_high=""):
    b = rj(baseline) or {}
    t0 = (b.get("started_at") or b.get("deployed_at") or 0) - 60
    ds = rj(os.path.join(res, "summary", "data_status.json")) or {}
    rs = druns(res, t0)
    print("-- veri birimi çalıştırmaları (bu sürüm; en çok 10)")
    for r in rs[-10:]:
        rest = r.get("rest") or {}
        print("   %-18s %-8s %-15s REST %-18s istek %-5s fark %-4s tepe bellek %s" % (
            r.get("run_id"), r.get("mode"), r.get("result"), rest.get("guard"), rest.get("requests"),
            (r.get("diffs") or {}).get("count"), mb(r.get("resources"))))
    print("   (bu sürümün veri çalıştırması yok)") if not rs else None
    print("-- P1b VPS kabul ölçütleri (§10 P1b; [DİKKAT] = bana iletin)")
    bflag, btxt, done = bf_view(res, bf_state, me)
    line(bflag, "V1 ilk doldurma: " + btxt)
    # ÜST KAYNAKTA YOK (P2.1, datastore.S_ABSENT_UPSTREAM): arşiv bu mark/premium kümesini o sembol için hiç yayımlamıyor
    # (kanıtlı, her gece yeniden yoklanır). Bayat SAYILMAZ, taze de sayılmaz: planlı sayımın dışında, AYRI listelenir.
    au = sorted(k for k, v in (ds.get("series") or {}).items() if v.get("status") == "ABSENT_UPSTREAM")
    ser = {k: v for k, v in (ds.get("series") or {}).items() if v.get("planned") and k not in au}
    dl = sorted(k for k, v in ser.items() if v.get("status") == "DELISTED")     # terminal: tazelikten ayrı raporlanır
    old = sorted(((k, v) for k, v in ser.items() if k not in dl and (v.get("stale") or v.get("age_h") is None
                                                                   or v["age_h"] > 26)),
                 key=lambda kv: -(kv[1].get("age_h") or 1e9))
    miss = [g for g in ("futures/XAUUSDT", "futures/PAXGUSDT", "spot/PAXGUSDT") if not any(k.startswith(g + "/") for k in ser)]
    du = ds.get("dukascopy") or {}
    line(None if not (done and ser) else bool(old or miss), "V2 tazelik: planlı %d seri, last_ts ≤ 26 sa %d%s%s%s · altın %s · "
         "Dukascopy %s" % (len(ser) - len(dl), len(ser) - len(dl) - len(old),
                           " · delist %d (terminal, ayrı: %s)" % (len(dl), ", ".join(sorted({k.rsplit("/", 1)[0] for k in dl})[:4]))
                           if dl else "",
                           " · üst kaynakta yok %d (arşiv yayımlamıyor, kanıtlı; bayat da taze de sayılmaz, ayrı: %s%s)" % (
                               len(au), ", ".join(au[:4]), " …" if len(au) > 4 else "") if au else "", (" — eski: " + ", ".join(
             "%s %s" % (k, "%s sa" % f(v["age_h"], 0) if v.get("age_h") is not None else v.get("status")) for k, v in old[:4]))
             if old else "", "tamam" if not miss else "EKSİK " + ", ".join(miss),
             "HAZIR" if du.get("status") == "HAZIR" else "YAPILAMADI (kabul edilir; §3.3: hazır ayna yok)"))
    tot = ds.get("totals") or {}
    gaps = sum(int(v.get("gaps") or 0) for v in ser.values())
    line("bilgi" if done else None, "V3 boşluk %d (%d seride; fonlama yerel gerçek aralıkla) · açık dönem %s · "
         "archive_unverified satır %s · REST %s · tohum %s" % (gaps, sum(1 for v in ser.values() if v.get("gaps")),
                                                             tot.get("holes", 0), tot.get("unverified_rows"),
                                                             tot.get("rest_rows"), tot.get("seed_rows")))
    up = [r for r in rs if r.get("mode") == "update" and r.get("result") in ("SUCCESS", "PARTIAL", "DEADLINE")
          and r.get("run_id") != b.get("data_smoke_run_id")][:3]
    nd = sum(int((r.get("diffs") or {}).get("count") or 0) for r in up)
    line(None if not up else nd > 0, "V4 arşiv uzlaştırma farkı ilk %d/3 gecede %d (beklenen 0; fark: runs/data/<id>/diffs.jsonl.gz)"
         % (len(up), nd))
    hits, nw, unread = dwin(rs, lo=time.time() - 15 * 86400)
    halted = [r.get("run_id") for r in rs if (r.get("rest") or {}).get("halted")]
    line(None if not nw or unread == nw else bool(hits or halted), "V5 worker 418/429 veri/doldurma pencerelerinde %d (%d pencere"
         "%s) · REST durdu: %s" % (hits, nw, ", %d okunamadı" % unread if unread else "", ", ".join(halted) or "hiç"))
    pks = [x for x in ((r.get("resources") or {}).get("memory_peak_ratio") for r in rs) if isinstance(x, (int, float))]
    if iv(peak):
        pks.append(iv(peak) / float(dmem))
    sk = [r.get("run_id") for r in runs(res) if r.get("result") == "SKIPPED_LOCKED" and (ep(r.get("started_at")) or 0) >= t0]
    hi = [x for x in [(r.get("resources") or {}).get("memory_high_events") for r in rs] + [iv(bf_high)] if isinstance(x, int)]
    line(None if not pks and not sk else (bool(sk) or max(pks or [0]) > 0.8), "V6 veri/doldurma memory.peak en çok %s × "
         "MemoryMax (≤ 0,8) · gece SKIPPED_LOCKED %d (0 olmalı) · MemoryHigh kısması (memory.events high) en çok %s (bilgi; "
         "sayfa önbelleği dahil, A/B tur p95 ile okunur)" % (f(max(pks) if pks else None, 2), len(sk), max(hi) if hi else "—"))
    rb, e = iv(res_b) or 0, float(est)
    line(None if not done else not (0.7 * e <= rb <= 1.3 * e), "V7 disk: data/research %s GB · tahmin %s GB ±%%30%s" % (
        f(rb / 1e9, 2), f(e / 1e9, 1), "" if done else " (ilk doldurma bitince karşılaştırılır)"))
    fb = iv(free_b)
    line(None if fb is None else (fb < 15e9 or rb >= 16e9), "disk şimdi: boş %s GB · data/research %s GB (blok) — veri birimi/"
         "doldurma kendiliğinden durur: boş < 13 GB ya da ≥ 17 GB; gece birimi 10 / 20 GB'ta (kapanış arşivi önce gelir)" % (
             gb(fb), gb(rb)))
    z = next((r for r in reversed(rs) if r.get("mode") == "update"), None)
    if z:
        line(z.get("result") not in ("SUCCESS", "DEADLINE", "SKIPPED_LOCKED"), "son veri çalıştırması %s %s (çıkış %s) · REST %s · "
             "bayraklar %s" % (z.get("run_id"), z.get("result"), z.get("exit_code"), (z.get("rest") or {}).get("guard"),
                               ", ".join(z.get("flags") or []) or "—"))
    return 0
def _badchk(x):
    """UTC günü satırında tutmayan `anchor_check` / `entries_check` (ok = False) var mı (iç içe)."""
    if isinstance(x, dict):
        return any((k in ("anchor_check", "entries_check") and isinstance(v, dict) and v.get("ok") is False) or _badchk(v)
                   for k, v in x.items())
    return any(_badchk(v) for v in x) if isinstance(x, list) else False
def kv(d):
    return ", ".join("%s %s" % (k, v) for k, v in (d or {}).items()) or "—"
def c_p2(res, tip, attr, cfg, fdv, q1="", q2=""):
    """P2 VPS kabul ölçütleri (§10 P2) motorun yazdıklarından; ölçülemeyen [ölçülemedi] kalır, geçti SAYILMAZ."""
    rs = mine(res, tip)
    full = [r for r in rs if "S1b" in (r.get("plan") or [])]
    last = full[-1] if full else {}
    stg = last.get("stages") or {}
    s1, s2 = stg.get("S1b") or {}, stg.get("S2") or {}
    r1, r2 = s1.get("result") or {}, s2.get("result") or {}
    er = r2.get("errors")       # motor: işlem hatası SAYISI (int); eski biçim liste
    ne = len(er) if isinstance(er, list) else (iv(er) or 0)
    print("-- P2 VPS kabul ölçütleri (§10 P2; [DİKKAT] = bana iletin; 3, 7, 8 bayt-özdeşlik, 9 depo testleridir: "
          "bağımsız koşucu 201–213, --dry-run; 11 gerçek veride [bilgi])")
    line(None if not full else ((s1.get("status"), s2.get("status")) != ("OK", "OK") or ne > 0),
         "S1b/S2 son tam gece %s: S1b %s %s sn (bekleyen %s) · S2 %s %s sn (bekleyen %s, işlem hatası %s) · bayraklar %s" % (
             last.get("run_id") or "yok", s1.get("status"), f(s1.get("duration_s"), 0), (r1.get("journal") or {}).get("pending"),
             s2.get("status"), f(s2.get("duration_s"), 0), r2.get("pending"), ne,
             ", ".join(f_ for f_ in (last.get("flags") or []) if f_ != "DATA_STALE") or "—"))
    rec, jb = r1.get("reconcile") or {}, rj(os.path.join(res, "journal", "tj_v1", "_build.json")) or {}
    line({"OK": False, "INCONSISTENT": True}.get(rec.get("status")), "1 günlük = arşiv = ledger (her kayıt tam bir kez; Σ net_pnl, "
         "ücret, fonlama, kayma 1e-6): %s · %s satır%s%s" % (rec.get("status") or "—", jb.get("rows", "—"),
                                                          " (kurulum sürüyor; bekleyen %s)" % rec.get("pending") if rec.get("pending") else "",
                                                          " · sorunlu: " + ", ".join(rec.get("books_not_ok")[:4]) if rec.get("books_not_ok") else ""))
    own, fee, rb = jb.get("owner_fields_ok"), jb.get("fee_identity_violations"), jb.get("r_check_failures")
    line(None if not jb else bool(own is not True or fee or rb), "2 sahip alanları %s · 3 ücret özdeşliği ihlali %s · 4 net_r ≠ "
         "ledger r_multiple %s (1e-9)" % ({True: "dolu ya da etiketli", False: "EKSİK"}.get(own, "—"), fee, rb))
    # 5 (P2.1): oran yalnız `fdv` (fidelity_v3) özetiyle ve günlüğün yeniden kurulumu bitmişken ölçülmüş sayılır; eski
    # sürümün özeti ya da bekleyen satırlar varken (v2/v3 karışık) değerler basılır ama satır [ölçülemedi] kalır
    fd, jp = jb.get("fidelity") or {}, (r1.get("journal") or {}).get("pending")
    fnote = (" — özet %s: %s satırları henüz kurulmadı (ilk tam gecede)" % (fd.get("version") or "sürümsüz", fdv)
             if fd and fd.get("version") != fdv else " — yeniden kurulum sürüyor (bekleyen %s satır; oran v2/v3 karışık, ölçüt "
             "kurulum bitince)" % jp if fd and jp else "")
    line(None if not fd.get("eligible") or fnote else not fd.get("gate_pass"), "5 fidelity %s %s/%s = %%%s (ölçüt ≥ %%90; "
         "beklenen ≈ %%93–95) · nedenler %s · uygun değil %s%s" % (
             fd.get("version") or "—", fd.get("ok", "—"), fd.get("eligible", "—"),
             f(fd["rate"] * 100, 1) if fd.get("rate") is not None else "—", kv(fd.get("failures_by_reason")),
             kv(fd.get("not_eligible")), fnote))
    cells = [(b, e, v) for b, eps in (jb.get("rehydrate") or {}).items() for e, v in (eps or {}).items() if v.get("n")]
    low = [c for c in cells if not c[2].get("meets_target")]
    line(None if not cells else bool(low), "6 rehydrate eşleşmesi (giriş + ölçülmüş stop 1 tick; defter × config dönemi, ölçüt ≥ "
         "%%95): %d hücre, altında %d%s" % (len(cells), len(low), " — " + "; ".join(
             "%s %s %%%s (%d)" % (b[:24], e, f((v.get("rate") or 0) * 100, 0), v["n"]) for b, e, v in (low or cells)[:5])))
    ab = rj(os.path.join(res, "attribution", "_build.json")) or {}
    line(None if not ab else (ab.get("attribution_sha"), ab.get("cfgrid_sha")) != (attr, cfg), "8 mühürler attribution_v1 %s · "
         "cfgrid_v1 %s (sabit %s / %s) · S2 %s satır · ızgara %s · sıralama ölçüsü %s" % (
             str(ab.get("attribution_sha"))[:12], str(ab.get("cfgrid_sha"))[:12], attr[:12], cfg[:12], ab.get("rows", "—"),
             kv(ab.get("grid")), kv(ab.get("rank_src"))))
    sn = ab.get("sanity_5_10_box_stop_width") or {}
    bk = lambda v: "%s işlem ort. %sR (%s)" % (v.get("n"), f(v.get("mean_r"), 2), kv(dict(list((v.get("codes") or {}).items())[:2])))
    line("bilgi" if sn else None, "11 Box stop genişliği (§5.10; dönem başına; yeniden üretim, aday değil): %s" % ("; ".join(
        "%s: <0,5 %s · 0,5–1 %s" % (e, bk(b["<0.5"]) if b.get("<0.5") else "—", bk(b["0.5-1"]) if b.get("0.5-1") else "—")
        for e, b in list(sn.items())[-3:]) or "attribution/_build.json'da yok"))
    utc = {}
    try:
        with open(os.path.join(res, "target", "daily_utc.jsonl"), encoding="utf-8") as fh:
            for d in (json.loads(ln) for ln in fh if ln.strip()):
                if d.get("day") and int(d.get("rev") or 0) >= int((utc.get(d["day"]) or {}).get("rev") or 0):
                    utc[d["day"]] = d
    except (OSError, ValueError):
        pass
    bad10 = sorted(d for d, r in utc.items() if _badchk(r))
    tg = ((((stg.get("S3") or {}).get("result") or {}).get("utc_day") or {}).get("tgt_v2")) or {}
    line(None if not utc else bool(bad10), "10 UTC günü MTM: %d gün · çapa/hareket denetimi tutmayan %d%s · W-günüyle yan yana "
         "%s/14 gün (sonra tgt_v2)" % (len(utc), len(bad10), " (%s)" % ", ".join(bad10[-3:]) if bad10 else "",
                                       tg.get("side_by_side_days", "—")))
    dg = os.path.join(res, "summary", "digest_tr.md")
    sz, q = (os.path.getsize(dg) if os.path.isfile(dg) else None), [iv(x) for x in (q1, q2)]
    line(None if sz is None else (sz > DIGEST_MAX or any(x is None or x > QUERY_MAX for x in q)), "12 digest_tr.md %s KB (≤ 8 KB) "
         "· engine-query why-lost --days 7: %s satır · day <dün>: %s satır (≤ 150)" % (
             f(sz / 1024.0 if sz is not None else None, 1), q1 or "HATA", q2 or "HATA"))
    # 13: takvim gecesi (sürüm günü + 1'den, 03:40'ı geçmiş); A/B penceresinde tek gün-yıl sırası KAPALI (sayılmaz),
    # kaydı olmayan beklenen gece başarısız sayılır (birim hiç başlamadı / zamanlayıcı tetiklemedi)
    byday, nights, rel = {}, [], release_day(res, tip)
    for r in rs:
        t = ep(r.get("started_at"))
        d = datetime.fromtimestamp(t, UTC) if t is not None else None
        if d and (1, 30) <= (d.hour, d.minute) < (3, 40):
            byday.setdefault(d.strftime("%Y-%m-%d"), r)
    r0 = datetime.strptime(rel, "%Y-%m-%d").replace(tzinfo=UTC) if rel else None
    d0 = r0 + timedelta(days=1) if r0 else None
    while d0 is not None and d0.timestamp() + 220 * 60 < time.time():
        r = byday.get(d0.strftime("%Y-%m-%d"))
        if not ((d0 <= r0 + timedelta(days=14) and d0.timetuple().tm_yday % 2 == 1)
                or (r or {}).get("result") in ("AB_OFF", "AB_OFF_ZORUNLU_ARŞİV")):
            nights.append((d0.strftime("%Y-%m-%d"), r, d0.timestamp() + 220 * 60))
        d0 += timedelta(days=1)
    last3 = nights[-3:]
    line(None if len(last3) < 3 else not all(r and r.get("result") == "SUCCESS" and (ep(r.get("finished_at")) or 1e18) < lim
                                           for _, r, lim in last3),
         "13 son 3 ardışık gece (takvimden; A/B KAPALI geceler sayılmaz, kaydı olmayan gece başarısız): %s · ölçüt SUCCESS ve "
         "bitiş < 03:40 UTC; tur etkisi ≤ %%5: --ab-report" % ("; ".join(
             "%s %s %s" % (day, r.get("result"), str(r.get("finished_at"))[11:16]) if r else "%s kayıt yok" % day
             for day, r, _ in last3) or "henüz yok"))
    return 0
def c_k6(eng, sc):
    """VPS kabul 6 (P1a): kayıt (closed_at günü) ve cüzdan (ts günü) görünümleri arşivin son günü − 2'ye kadar eşit olmalı."""
    e, s_ = rj(eng) or {}, (rj(sc) or {}).get("daily_target") or {}
    upto = e.get("archived_through")
    if not e.get("books") or not s_.get("books") or not upto:
        line(None, "K6 scorecard --daily = engine-status --daily: karşılaştırılamadı")
        return 0
    cut = (datetime.strptime(upto, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    days = [d for d in e.get("days") or [] if d <= cut and d in (s_.get("days") or [])]
    bad = ["%s: betikte yok" % b for b in e["books"] if b not in s_["books"] or "error" in s_["books"][b]]
    bad += ["%s: motorda yok" % b for b, x in s_["books"].items() if b not in e["books"] and "error" not in x]
    for b, eb in e["books"].items():
        sd = (s_["books"].get(b) or {}).get("days") or {}
        for d in days:
            er, sr = eb["days"][d]["rec"], (sd.get(d) or {}).get("rec") or {}
            bad += ["%s %s kayıt.%s %s≠%s" % (b, d, k, er.get(k), sr.get(k)) for k in ("n", "net", "fees", "funding", "slippage")
                    if abs((er.get(k) or 0) - (sr.get(k) or 0)) > 1e-6]
            ew, sw = eb["days"][d]["wal_ts"], (sd.get(d) or {}).get("wal_ts") or {}
            if ew.get("complete") and sw.get("complete") and abs((ew.get("net") or 0) - (sw.get("net") or 0)) > 1e-6:
                bad.append("%s %s cüzdan %s≠%s" % (b, d, ew.get("net"), sw.get("net")))
    line(bool(bad) if days else None, "K6 scorecard --daily = engine-status --daily: %d defter × %d gün (≤ %s; arşiv %s'e kadar)%s" % (
        len(e["books"]), len(days), cut, upto, (" — FARK: " + "; ".join(bad[:4])) if bad else ""))
    return 0
def c_ab(res, tip, baseline, samples, memmax, nr, emem, dmem):
    b = rj(baseline) or {}
    rel = release_day(res, tip)
    print("-- A/B geceleri (§2.9; gece = 01:37–03:40 UTC, AÇIK ve KAPALI aynı pencere)")
    if rel is None:
        return print("   sürüm günü kaydı yok (runs/engine_epochs.jsonl) — A/B kurulamaz") or 1
    r0 = datetime.strptime(rel, "%Y-%m-%d").replace(tzinfo=UTC)
    byday = {}
    for r in mine(res, tip):
        t = ep(r.get("started_at"))
        if t is not None:
            d = datetime.fromtimestamp(t, UTC)
            if (d.hour, d.minute) >= (1, 30) and (d.hour, d.minute) < (3, 40):
                byday.setdefault(d.strftime("%Y-%m-%d"), r)
    g = {k: {"tours": [], "rest": 0, "ra": 0, "err": 0, "n": 0, "peak": []} for k in ("ON", "OFF")}
    nights, gone = [], 0
    for i in range(1, 15):
        d0 = r0 + timedelta(days=i)
        day, s, e = d0.strftime("%Y-%m-%d"), d0.timestamp() + 97 * 60, d0.timestamp() + 220 * 60
        if time.time() <= e:
            break
        r = byday.get(day)
        ab = ((r or {}).get("selfcheck") or {}).get("ab") or {}
        st = ab.get("status") or ("AB_ON" if d0.timetuple().tm_yday % 2 == 0 else "AB_OFF")
        js = jscan(s, e)
        plan = tuple((r or {}).get("plan") or [])   # AÇIK = tam plan; KAPALI = S0 + S1s (SKEW/kilit/yalıtım dışarıda)
        key = {("AB_ON", STAGES[:1] + STAGES[2:]): "ON", ("AB_OFF", ("S0", "S1s")): "OFF"}.get((st, plan)) if r is not None else None
        nights.append((day, st, (r or {}).get("result") or ("çalıştırma kaydı yok" if r is None else "?"), js, key))
        if key and js is not None:
            gg = g[key]
            gg["n"], gg["tours"], gg["rest"] = gg["n"] + 1, gg["tours"] + js["tours"], gg["rest"] + js["rest418429"]
            gg["ra"], gg["err"] = gg["ra"] + js["research_active"], gg["err"] + js["tour_error"]
            pk = ((r or {}).get("resources") or {}).get("memory_peak_ratio")
            if isinstance(pk, (int, float)):
                gg["peak"].append(pk)
        elif key:
            gone += 1
    print("   sürüm günü %s · pencere %s … %s · %d/14 gece geçti%s" % (
        rel, (r0 + timedelta(days=1)).strftime("%Y-%m-%d"), (r0 + timedelta(days=14)).strftime("%Y-%m-%d"), len(nights),
        "" if len(nights) >= 14 else " — ARA GÖRÜNÜM (14 gece dolmadı)"))
    for day, st, rr, js, key in nights:
        print("   %s %-22s %-24s %s" % (day, st, rr, "günlük okunamadı" if js is None else "tur %d (p95 %s sn) · 418/429 %d · araştırma %d · tur hatası %d" % (
            len(js["tours"]), f(pct(js["tours"], 0.95), 0), js["rest418429"], js["research_active"], js["tour_error"])))
    on, off = g["ON"], g["OFF"]
    p95on, p95off = pct(on["tours"], 0.95), pct(off["tours"], 0.95)
    print("-- karşılaştırma (AÇIK %d gece · KAPALI %d gece; AB_OFF_ZORUNLU_ARŞİV hariç%s)" % (
        on["n"], off["n"], (" · günlüğü okunamayan %d gece" % gone) if gone else ""))
    res4 = [("tur p50/p95 (sn)", None if p95on is None or p95off is None else p95on > 1.05 * p95off,
             "AÇIK %s/%s (%d tur) · KAPALI %s/%s (%d tur) · ölçüt AÇIK p95 ≤ KAPALI p95 × 1,05" % (
                 f(pct(on["tours"], .5), 0), f(p95on, 0), len(on["tours"]), f(pct(off["tours"], .5), 0), f(p95off, 0),
                 len(off["tours"])))]
    res4.append(("418/429", on["rest"] > 0 if on["n"] else None,
                 "AÇIK %d · KAPALI %d (ölçüt: AÇIK gecelerde 0)" % (on["rest"], off["rest"])))
    res4.append(("PAPER_RESEARCH_ACTIVE", "bilgi" if (on["n"] and off["n"]) else None,
                 "AÇIK %d · KAPALI %d (raporlanır; fark beklenmez)" % (on["ra"], off["ra"])))
    res4.append(("tur hatası", None if not (on["n"] and off["n"]) else (on["err"] > off["err"]),
                 "AÇIK %d · KAPALI %d" % (on["err"], off["err"])))
    pk = max(on["peak"]) if on["peak"] else None
    res4.append(("motor memory.peak", None if pk is None else pk > 0.8,
                 "AÇIK gecelerde en çok %s × MemoryMax (ölçüt ≤ 0,8; MemoryMax %s bayt)" % (f(pk, 2), emem)))
    w = b.get("worker") or {}
    res4.append(("worker NRestarts", iv(nr) != w.get("nrestarts") if w else None,
                 "dağıtımda %s → şimdi %s (değişmemeli)" % (w.get("nrestarts"), nr)))
    res4.append(("worker MemoryMax", iv(memmax) != 6442450944, "%s (6G = 6442450944 olmalı)" % memmax))
    # P1b kabul 5–6: veri birimi ve ilk doldurma pencereleri (A/B'den bağımsız; her gece 00:41 + doldurma süresi)
    lo, hi = (r0 + timedelta(days=1)).timestamp(), (r0 + timedelta(days=15)).timestamp()
    dr = [r for r in druns(res, lo - 30 * 86400) if r["_end"] > lo and (ep(r.get("started_at")) or 0) < hi]
    hits, nw, unread = dwin(dr, lo, hi)
    res4.append(("veri/doldurma 418/429", None if not nw or unread == nw else hits > 0,
                 "%d pencerede %d (ölçüt 0%s)" % (nw, hits, "; %d pencerenin günlüğü okunamadı" % unread if unread else "")))
    dp = [x for x in ((r.get("resources") or {}).get("memory_peak_ratio") for r in dr) if isinstance(x, (int, float))]
    res4.append(("veri memory.peak", None if not dp else max(dp) > 0.8,
                 "en çok %s × MemoryMax (ölçüt ≤ 0,8; MemoryMax %s bayt)" % (f(max(dp) if dp else None, 2), dmem)))
    sk = sum(1 for r in runs(res) if r.get("result") == "SKIPPED_LOCKED" and lo <= (ep(r.get("started_at")) or 0) < hi)
    res4.append(("gece SKIPPED_LOCKED", sk > 0, "%d (ölçüt 0: doldurma gece birimini kilitlemez)" % sk))
    sm = []
    try:
        with open(samples, encoding="utf-8") as fh:
            sm = [json.loads(x) for x in fh if x.strip()]
    except (OSError, ValueError):
        pass
    kinds = {day: key for day, _st, _rr, _js, key in nights}
    agg = {"ON": {"box": [], "drej": [], "pm": []}, "OFF": {"box": [], "drej": [], "pm": []}}
    bfw = [(ep(r.get("started_at")) or 0, r["_end"]) for r in dr if r.get("mode") == "backfill"]
    dj = {True: [], False: []}
    for a, z in zip(sm, sm[1:]):
        if z.get("worker_pid") != a.get("worker_pid"):
            continue
        if (z.get("tours") or 0) > (a.get("tours") or 0):
            dj[any(s_ < z["t"] and e_ > a["t"] for s_, e_ in bfw)].append(
                (z["data_rejected"] - a["data_rejected"]) / (z["tours"] - a["tours"]))
        inside = [dd for dd in kinds if a["t"] < datetime.strptime(dd, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 97 * 60
                  and datetime.strptime(dd, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 220 * 60 <= z["t"]]
        if len(inside) != 1 or kinds[inside[0]] is None:
            continue
        k, hrs = kinds[inside[0]], (z["t"] - a["t"]) / 3600.0
        if None not in (a.get("box_missed"), z.get("box_missed")) and hrs > 0:
            agg[k]["box"].append((z["box_missed"] - a["box_missed"]) / hrs)
        if (z.get("tours") or 0) > (a.get("tours") or 0):
            agg[k]["drej"].append((z["data_rejected"] - a["data_rejected"]) / (z["tours"] - a["tours"]))
        if z.get("pm_p50") is not None:
            agg[k]["pm"].append(z["pm_p50"])
    for name, key, nd, rule in (("Box kaçan bar/saat", "box", 3, "AÇIK ≤ KAPALI"),
                                ("veri reddi/tur", "drej", 3, "AÇIK ≤ KAPALI × 1,05"),
                                ("koruyucu izleyici süre p50 (sn)", "pm", 2, "AÇIK ≤ KAPALI × 1,05")):
        a_, z_ = agg["ON"][key], agg["OFF"][key]
        ma, mz = pct(a_, .5), pct(z_, .5)
        flag = None if ma is None or mz is None else (ma > mz if key == "box" else ma > 1.05 * mz + 1e-9)
        res4.append((name, flag, "KABA (iki --check örneği arası, tek A/B gecesi + gündüz): AÇIK medyan %s (%d aralık) · "
                                 "KAPALI medyan %s (%d aralık) · ölçüt %s" % (f(ma, nd), len(a_), f(mz, nd), len(z_), rule)))
    ma, mz = pct(dj[True], .5), pct(dj[False], .5)
    res4.append(("veri reddi/tur (doldurma)", None if ma is None or mz is None else ma > 1.05 * mz + 1e-9,
                 "KABA: doldurmalı aralık medyanı %s (%d) · doldurmasız %s (%d) · ölçüt ≤ × 1,05" % (
                     f(ma, 3), len(dj[True]), f(mz, 3), len(dj[False]))))
    for name, flag, txt in res4:
        print("   %-13s %-26s %s" % ({None: "[ölçülemedi]", True: "[KALDI]", False: "[GEÇTİ]", "bilgi": "[bilgi]"}[flag],
                                     name, txt))
    if any(x[1] is True for x in res4):
        print("   >>> en az bir ölçüt KALDI: bu çıktıyı bana iletin (kapatma: sudo systemctl disable --now "
              "tradingbot-engine-data.timer tradingbot-engine-night.timer)")
    return 0
def c_bfprops(unit, dropin):
    """systemd-run özellikleri = veri biriminin (+ drop-in) [Service] satırları AYNEN; Type=exec, ExecStart ve TimeoutStartSec hariç."""
    props = []
    for path in (unit, dropin):
        sec = None
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        for ln in (x.strip() for x in lines):
            if not ln or ln[0] in "#;":
                continue
            if ln.startswith("["):
                sec = ln
            elif sec == "[Service]" and "=" in ln:
                k, v = ln.split("=", 1)
                if k in ("Type", "ExecStart", "TimeoutStartSec"):
                    continue
                props = [p for p in props if p[0] != k or k == "Environment"] + [(k, v)]
    if not any(k == "MemoryMax" for k, _ in props):
        return 1
    print("\n".join("--property=%s=%s" % kv for kv in [("Type", "exec")] + props))
    return 0
CMDS = {"paper": c_paper, "runner": c_runner, "ast": c_ast, "lastrun": c_lastrun, "dlast": c_dlast, "smoke": c_smoke,
        "dsmoke": c_dsmoke, "check": c_check, "dcheck": c_dcheck, "bf": c_bf, "bfprops": c_bfprops, "bkok": c_bkok, "est": c_est,
        "abwin": c_abwin, "p2": c_p2, "ab": c_ab, "baseline": c_baseline, "k6": c_k6}
r = CMDS[sys.argv[1]](*sys.argv[2:])
sys.exit(r if isinstance(r, int) else 0)
PY
rpy() { env -i PATH="$PATH" LANG=C.UTF-8 TZ=UTC "$VENV/bin/python" -I -c "$PYTOOL" "$@"; }
# ------------------------------------------------------------------ klon yardımcıları (git servis kullanıcısıyla)
fetch_tip() {   # $1: klon [$2: SHA] [$3: önce denenecek yerel depo]. SHA yalnız EKLENİR (çalışma ağacı değişmez)
  local d="$1" s="${2:-$TIP}" src="${3:-}"
  if [[ -n "$src" ]] && ! gitx "$d" cat-file -e "$s^{commit}" 2>/dev/null; then gitx "$d" fetch -q "$src" "$s" 2>/dev/null || true; fi
  if ! gitx "$d" cat-file -e "$s^{commit}" 2>/dev/null; then
    gitx "$d" fetch -q "$REPO_URL" "$s" 2>/dev/null || gitx "$d" fetch -q "$REPO_URL" "$BRANCH_REF" 2>/dev/null || true
  fi
  gitx "$d" cat-file -e "$s^{commit}" 2>/dev/null \
    || die "commit ${s:0:7} getirilemedi ($REPO_URL): GitHub erişimini ve commit'in yayımlandığını denetleyin"
}
make_clone() {  # $1: VAR OLAN boş klasör (servis kullanıcısına ait). Yerel app deposundan klon + TIP + checkout
  gitx "$1" clone -q --no-checkout "$APP" .
  fetch_tip "$1"
  gitx "$1" -c advice.detachedHead=false checkout -q --detach "$TIP"
}
pin_eng() {     # $1: SHA — engine-app'i sabitle + önceden derle (gece/veri birimi çalışırken çağrılmaz); çıkış = gerçek sonuç
  fetch_tip "$ENG" "$1" && gitx "$ENG" -c advice.detachedHead=false checkout -q --detach "$1" \
    && svc "$ENG" "${LOW[@]}" "$VENV/bin/python" -s -m compileall -q tradingbot scripts >/dev/null
}
revert_eng() {  # smoke geçmezse / hata: gece birimi bilinen-iyi (önceki) kodla sürsün; çıkış GERÇEK sonuçtur (on_err'in set +e'sinde de)
  if [[ -n "$ENG_PREV" && "$ENG_PREV" != "$TIP" && "$(gitx "$ENG" rev-parse HEAD 2>/dev/null)" != "$ENG_PREV" ]]; then
    pin_eng "$ENG_PREV" || return 1; echo "   engine-app önceki sabitine döndü (${ENG_PREV:0:7})" || true; fi
  return 0     # yazılamayan çıktı (kopmuş bağlantı) sonucu değiştirmez
}
unit_contract() {   # $1: ağaç. sha sabitlemesinin üstüne ikinci savunma: §2.2–§2.5 anahtar satırları (gece + veri)
  local s="$1/deploy/$SVC" t="$1/deploy/$TMR" ds="$1/deploy/$DSVC" dt="$1/deploy/$DTMR" u
  grep -qx 'OnCalendar=\*-\*-\* 01:37:00 UTC' "$t" && grep -qx 'PrivateNetwork=yes' "$s" && grep -qx 'MemoryMax=768M' "$s" \
    && grep -qx 'MemoryHigh=640M' "$s" && grep -qx "Environment=ENGINE_EXPECTED_MEMORY_MAX=$ENGINE_MEM" "$s" \
    && ! grep -q '^SupplementaryGroups' "$s" \
    && grep -qx 'ExecStart=/opt/tradingbot/venv/bin/python -s -m tradingbot engine-night' "$s" || return 1
  grep -qx 'OnCalendar=\*-\*-\* 00:41:00 UTC' "$dt" && ! grep -q '^PrivateNetwork' "$ds" \
    && grep -qx 'SupplementaryGroups=systemd-journal' "$ds" && grep -qx 'MemoryMax=1G' "$ds" && grep -qx 'MemoryHigh=512M' "$ds" \
    && grep -q '^InaccessiblePaths=.*-/opt/tradingbot/env -/opt/tradingbot/env\.d .*-/opt/tradingbot/data/vault' "$ds" \
    && grep -qx "Environment=ENGINE_EXPECTED_MEMORY_MAX=$DATA_MEM" "$ds" && grep -qx 'TimeoutStartSec=50min' "$ds" \
    && grep -qx 'ExecStart=/opt/tradingbot/venv/bin/python -s -m tradingbot engine-data --update' "$ds" || return 1
  for u in "$s" "$ds"; do
    grep -qx 'PrivateTmp=yes' "$u" && grep -qx 'ProtectSystem=strict' "$u" && grep -qx 'User=tradingbot' "$u" \
      && grep -qx 'ReadWritePaths=/opt/tradingbot/data/research' "$u" && ! grep -q '^EnvironmentFile' "$u" \
      && grep -qx 'WorkingDirectory=/opt/tradingbot/engine-app' "$u" || return 1
  done
  grep -qx 'Persistent=false' "$t" && grep -qx 'Persistent=false' "$dt"
}
dropin_text() {     # 44d5353'ün kuracağı drop-in içeriği (uyarı birimi VARSA OnFailure; nproc < 4 ise CPUQuota=60%; §2.5) — YALNIZ karşılaştırma
  local t=""
  if systemctl cat tradingbot-alert@.service >/dev/null 2>&1; then t+=$'[Unit]\nOnFailure=tradingbot-alert@%n.service\n'; fi
  if (( $(nproc) < 4 )); then t+=$'[Service]\nCPUQuota=60%\n'; fi
  printf '%s' "$t"
}
nocomment() { sed '1{/^# tb-engine-[0-9a-f]\{7\}:/d;}'; }   # drop-in'in 1. satırı kuran sürümü yazar: içerik karşılaştırılır
dropin_note() {    # $1 drop-in yolu: 1. satır (kuran sürüm) ve içeriğin bu sunucu için beklenenle (1. satır hariç) aynılığı
  local first; first="$(head -n 1 "$1" 2>/dev/null || true)"
  if [[ ! -f "$1" && -z "$DT_WANT" ]]; then printf 'yok (beklenen de yok)'; return 0; fi
  if [[ "$DT_WANT" == "$({ cat "$1" 2>/dev/null || true; } | nocomment)" ]]; then printf '%s (içerik beklenenle aynı)' "${first:-yok}"
  else printf '%s — içerik bu sunucu için beklenenden FARKLI (uyarı birimi/nproc değişti mi?)' "${first:-yok}"; return 1; fi
}
back_out() {       # geri dönüş: engine-app bilinen-iyi (önceki) koda; birim dosyalarına bu sürüm hiç dokunmaz
  if revert_eng; then return 0; fi
  UNDO_NOTE="engine-app önceki sabitine (${ENG_PREV:0:7}) döndürülemedi"; warn "$UNDO_NOTE"; return 1
}
state_msg() {      # geri dönüşten sonra GERÇEK durum (--rollback önerisi yalnız gerekiyorsa)
  if [[ -n "$UNDO_NOTE" ]]; then printf '%s. Bana iletin; 44d5353 durumuna dönmek için uygun saatte: sudo bash %s --rollback' "$UNDO_NOTE" "$0"
  elif [[ -n "$RB_HINT" ]]; then printf '%s' "$RB_HINT"
  else printf 'önceki kurulum geri geldi (engine-app %s; birim dosyalarına ve zamanlayıcılara dokunulmadı) — --rollback GEREKMEZ; uygun saatte aynı komutla yeniden' "${ENG_PREV:0:7}"; fi
}
worker_now() { printf '%s %s' "$(sc_show "$WORKER" MainPID)" "$(sc_show "$WORKER" NRestarts)"; }
bf_peak() { cat "/sys/fs/cgroup/system.slice/$BF/memory.peak" 2>/dev/null || echo 0; }
bf_high() { awk '$1 == "high" {print $2}' "/sys/fs/cgroup/system.slice/$BF/memory.events" 2>/dev/null || true; }
# ================================================================== --check / --ab-report (salt-okunur; yalnız örnek eklenir)
if [[ "$MODE" == --check ]]; then
  say "ÖĞRENME MOTORU (gece + veri) — tb-engine-$T7 --check (salt-okunur; yalnız $SAMPLES'a ölçüm örneği eklenir)"
  [[ -d "$ENG/.git" ]] || die "motor kurulu değil ($ENG yok). Dağıtım: sudo bash $0"
  eng_sha="$(gitx "$ENG" rev-parse HEAD)"; app_sha="$(gitx "$APP" rev-parse HEAD)"
  if gitx "$ENG" merge-base --is-ancestor "$app_sha" "$eng_sha" 2>/dev/null; then rel="app ⊑ engine-app (SKEW yok)"
  else rel="SKEW — app sürümü engine-app'te yok: engine-app yeniden sabitlenmeli (o geceler yalnız S0/S1a/S7)"; fi
  echo "   iki SHA: app ${app_sha:0:7} · engine-app ${eng_sha:0:7} (bu betiğin hedefi $T7) → $rel"
  for t in "$TMR:$SVC" "$DTMR:$DSVC"; do
    echo "   ${t%%:*}: $(sc_state is-enabled "${t%%:*}") / $(sc_state is-active "${t%%:*}") · sonraki $(sc_show "${t%%:*}" NextElapseUSecRealtime)" \
         "· son servis sonucu $(sc_show "${t##*:}" Result) (çıkış $(sc_show "${t##*:}" ExecMainStatus); 226 = NAMESPACE)"
  done
  case "$(fsha "$SD/$SVC")" in "$SVC_SHA256") nu="P2 baytları (640M/768M; bu sürümde değişmedi)";; "$P1A_SVC_SHA256") nu="P1a baytları (400M/512M) — P2 birimi KURULU DEĞİL";; *) nu="BİLİNMEYEN baytlar";; esac
  echo "   gece birimi dosyası: $nu · yüklü MemoryMax $(sc_show "$SVC" MemoryMax) · NeedDaemonReload $(sc_show "$SVC" NeedDaemonReload)"
  [[ "$(fsha "$SD/$DSVC")" == "$DSVC_SHA256" && "$(fsha "$SD/$DTMR")" == "$DTMR_SHA256" ]] && nu="hedefle (= 44d5353) aynı" || nu="hedeften FARKLI"
  echo "   veri birimi dosyaları: $nu · yüklü MemoryMax $(sc_show "$DSVC" MemoryMax) · NeedDaemonReload $(sc_show "$DSVC" NeedDaemonReload)"
  echo "   ilk doldurma ($BF): $(sc_state is-active "$BF") · worker: MemoryMax $(sc_show "$WORKER" MemoryMax)" \
       "· NeedDaemonReload $(sc_show "$WORKER" NeedDaemonReload) · NRestarts $(sc_show "$WORKER" NRestarts) — betik worker'a dokunmaz"
  sch=""; for f in /sys/block/*/queue/scheduler; do d="${f#/sys/block/}"; d="${d%%/*}"; [[ -r "$f" && ! "$d" =~ ^(loop|ram) ]] && sch+="$d=$(sed -n 's/.*\[\(.*\)\].*/\1/p' "$f") "; done
  echo "   IO zamanlayıcı: ${sch:-bilinmiyor}(IOSchedulingClass=idle/IOWeight yalnız bfq'da etkili; none/mq-deadline'de A/B tur p95'i ölçer)"
  say "engine-status --brief (≤ 60 satır; GECE ÖĞRENME MOTORU › GÜNLÜK HEDEF)"
  svc "$ENG" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" "$VENV/bin/python" -s -m tradingbot engine-status --brief \
    || echo "   (engine-status hata verdi)"
  say "engine-data --status (≤ 40 satır; salt-okunur)"
  svc "$ENG" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" "$VENV/bin/python" -s -m tradingbot engine-data --status \
    || echo "   (engine-data --status hata verdi)"
  say "kabul ölçütleri — gece birimi (P1a; bu sürümle yeni A/B dönemi)"
  rpy check "$RES" "$STATE" "$TIP" "$BASELINE" "$SAMPLES" "$(sc_show "$WORKER" MemoryMax)" "$(sc_show "$WORKER" NRestarts)" \
    "$(sc_show "$WORKER" MainPID)" || echo "   (ölçüt raporu hata verdi)"
  TMP="$(mktemp -d /tmp/tb-engine-k6.XXXXXX)"; chown "$SVC_USER:$SVC_GROUP" "$TMP"   # K6 (salt-okunur; geçici, silinir)
  svc "$ENG" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" "${LOW[@]}" "$VENV/bin/python" -s -m tradingbot \
    engine-status --daily --json --days 10 > "$TMP/e.json" 2>/dev/null || true
  svc "$ENG" "${LOW[@]}" "$VENV/bin/python" -s scripts/bot_scorecard.py --state "$STATE" --daily --days 10 --out "$TMP/s.json" \
    >/dev/null 2>&1 || true
  rpy k6 "$TMP/e.json" "$TMP/s.json" || echo "   (K6 hata verdi)"
  say "kabul ölçütleri — veri birimi ve ilk doldurma (P1b)"
  res_b="$({ du -sB1 "$RES" 2>/dev/null || true; } | awk 'NR==1{print $1+0}')"
  free_b="$({ df -B1 --output=avail "$BASE" 2>/dev/null || true; } | awk 'NR==2{print $1+0}')"
  read -r est_b _ <<<"$(rpy est "$RES" "$DISK_EST" 2>/dev/null || echo "$DISK_EST")"
  rpy dcheck "$RES" "$TIP" "$BASELINE" "${res_b:-0}" "$DATA_MEM" "$(sc_state is-active "$BF")" "$(bf_peak)" "${est_b:-$DISK_EST}" \
    "${free_b:-}" "$(basename "$0")" "$(bf_high)" || echo "   (veri ölçüt raporu hata verdi)"
  say "kabul ölçütleri — P2 (işlem günlüğü, rehydrate, fidelity ($FD_VER), atıf, ızgara, özet, sorgu; salt-okunur)"
  qx() { svc "$ENG" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" "$VENV/bin/python" -s -m tradingbot engine-query "$@"; }
  q1="$(qx why-lost --days 7 2>/dev/null | wc -l)" || q1=""
  q2="$(qx day "$(date -u -d yesterday +%F)" 2>/dev/null | wc -l)" || q2=""
  rpy p2 "$RES" "$TIP" "$ATTR_SHA" "$CFG_SHA" "$FD_VER" "${q1:-}" "${q2:-}" || echo "   (P2 ölçüt raporu hata verdi)"
  echo "   14. geceden sonra  sudo bash $0 --ab-report · ilk doldurma bitti mi: yukarıdaki V1 satırı (TAMAMLANDI); --backfill" \
       "YALNIZ V1 'bitmedi' derse (bittikten sonra YENİ tur başlatır) · sorgu: engine-query trade|why-lost|day"
  echo "   kapatma: sudo systemctl disable --now $TMR $DTMR (önce NeedDaemonReload=no); geri alma (44d5353 durumu): sudo bash $0 --rollback"
  exit 0
fi
if [[ "$MODE" == --ab-report ]]; then
  say "ÖĞRENME MOTORU — tb-engine-$T7 --ab-report (salt-okunur)"
  rpy ab "$RES" "$TIP" "$BASELINE" "$SAMPLES" "$(sc_show "$WORKER" MemoryMax)" "$(sc_show "$WORKER" NRestarts)" "$ENGINE_MEM" "$DATA_MEM"
  exit 0
fi
# ================================================================== değiştiren kipler: tek örnek + kayıt
[[ -d "$LOGDIR" ]] || install -d -m 0750 "$LOGDIR"
exec 9>"$LOGDIR/.tb-engine.lock"
flock -n 9 || die "başka bir tb-engine betiği çalışıyor ($LOGDIR/.tb-engine.lock)"
LOG="$LOGDIR/engine-$T7-${MODE#--}-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(trap '' HUP INT TERM; exec tee -p -a "$LOG") 2>&1; TEE_PID=$!   # bağlantı koparsa da kayıt sürer
echo "tb-engine-$T7 $MODE · $(date -u '+%F %T') UTC · kayıt: $LOG"
read -r W_PID0 W_NR0 <<<"$(worker_now)"
win1() {   # 1 gün kuralı: son başka-sürüm yeniden başlatması (sahip kararı 2026-10-07 "öğrenmeyi şimdi kur"; önceden 3, ondan önce 7)
  local fm e last=0 lastf="" age
  for fm in "$LOGDIR"/*-restart-at.txt; do
    [[ -f "$fm" ]] || continue; e="$(sed -n 1p "$fm" 2>/dev/null || true)"
    if [[ "$e" =~ ^[0-9]+$ ]] && (( e > last )); then last="$e"; lastf="$fm"; fi
  done
  age=$(( $(date +%s) - last )); (( age >= 1 * 86400 )) && rc=0 || rc=1
  if [[ -n "$lastf" ]]; then out="son sürüm yeniden başlatması $(basename "$lastf") $((age / 86400)) gün önce (≥ 1; §2.9; sahip kararı 2026-10-07; ilk iki --check temiz olmalı)"
    (( rc == 0 )) || out+="; en erken $(date -u -d "@$((last + 1 * 86400))" '+%F %H:%M') UTC"
  else out="deploy-logs'ta sürüm kaydı yok"; fi
  gate "1g-pencere-dışı" "$rc" "$out"
}
disk_gate() {   # $1: ek gereksinim (bayt) · $2: en az boş (gece birimi 10G) · $3: araştırma en çok (20G) · $4: not
  local mf="${2:-10000000000}" mr="${3:-20000000000}"
  free_b="$({ df -B1 --output=avail "$BASE" 2>/dev/null || true; } | awk 'NR==2{print $1+0}')"
  res_b="$({ du -sB1 "$RES" 2>/dev/null || true; } | awk 'NR==1{print $1+0}')"
  (( ${free_b:-0} >= mf + ${1:-0} && ${res_b:-0} + ${1:-0} <= mr )) && rc=0 || rc=1
  gate "disk" "$rc" "boş $(numfmt --to=si "${free_b:-0}") (≥ $(numfmt --to=si "$mf") + $(numfmt --to=si "${1:-0}")) · data/research $(numfmt --to=si "${res_b:-0}") (+ ihtiyaç ≤ $(numfmt --to=si "$mr"))${4:+ · $4}"
}
if [[ "$MODE" == --rollback ]]; then
  say "GERİ ALMA (44d5353 durumuna) — engine-app ${PREV_TIP:0:7} + compileall; birim dosyaları bu sürümde DEĞİŞMEDİ (yazılmaz, daemon-reload YOK); veri birimleri, ilk doldurma ve data/research DOKUNULMAZ"
  PARTIAL=(); time_ok 0 || true
  st="$(sc_state is-active "$SVC") $(sc_state is-active "$DSVC") $(sc_state is-active "$BF")"
  if [[ "$st" =~ (^| )(active|activating)( |$) ]]; then PARTIAL+=("bir motor çalıştırması sürüyor (gece/veri/doldurma: $st): hiçbir şey değişmedi")
  elif [[ "$WHY" == *"00:00–04:00"* ]]; then PARTIAL+=("$WHY: hiçbir şey değişmedi")
  elif [[ ! -d "$ENG/.git" || -L "$ENG" ]]; then PARTIAL+=("$ENG yok ya da bağlantı: hiçbir şey değişmedi")
  else
    cur="$(gitx "$ENG" rev-parse HEAD)"; app_sha="$(gitx "$APP" rev-parse HEAD 2>/dev/null || echo ?)"
    if [[ "$cur" == "$PREV_TIP" ]]; then ok "engine-app zaten ${PREV_TIP:0:7}'de"
    elif ! gitx "$ENG" merge-base --is-ancestor "$app_sha" "$PREV_TIP" 2>/dev/null; then
      PARTIAL+=("engine-app ${cur:0:7}'de BIRAKILDI: app ${app_sha:0:7}, ${PREV_TIP:0:7}'nin atası değil (dönülse her gece SKEW olurdu)")
    elif [[ -n "$(gitx "$ENG" status --porcelain)" ]]; then PARTIAL+=("engine-app'te yerel değişiklik var: yeniden sabitlenmedi")
    else CHANGED+=("engine-app=${PREV_TIP:0:7}"); pin_eng "$PREV_TIP"; ok "engine-app ${cur:0:7} → ${PREV_TIP:0:7} + compileall"; fi
    if (( ! ${#PARTIAL[@]} )); then
      if [[ -f "$BASELINE" ]]; then mv -- "$BASELINE" "$BASELINE.rolledback-$(date -u +%Y%m%dT%H%M%SZ)"; fi   # yeniden dağıtım smoke'suz "zaten" demesin
      # 44d5353 durumunun birim yarısı: dosyalar 44d5353'ün baytları ve yüklü (bu betik onlara hiç dokunmaz; yalnız doğrular)
      if units_ok; then ok "birim dosyaları 44d5353'ün baytları ve yüklü (gece MemoryMax $(sc_show "$SVC" MemoryMax); reload gerekmez, yapılmadı)"
      else PARTIAL+=("$WHY — birimlere dokunulmadı; 44d5353 durumu TAM değil (bana iletin)"); fi
    fi
  fi
  echo "   zamanlayıcılar: gece $(sc_state is-enabled "$TMR")/$(sc_state is-active "$TMR") · veri $(sc_state is-enabled "$DTMR")/$(sc_state is-active "$DTMR")" \
       "· birim dosyaları, drop-in'ler ve data/research $({ du -sh "$RES" 2>/dev/null || true; } | awk 'NR==1{print $1}') DOKUNULMADI"
  read -r W_PID1 W_NR1 <<<"$(worker_now)"
  if [[ "$W_PID1 $W_NR1" == "$W_PID0 $W_NR0" ]]; then ok "worker dokunulmadı (PID $W_PID1, NRestarts $W_NR1)"
  else warn "worker PID/NRestarts değişti ($W_PID0/$W_NR0 → $W_PID1/$W_NR1) — betik worker'a dokunmaz; nedeni günlükte"; fi
  if (( ${#PARTIAL[@]} )); then printf '   YARIM: %s\n' "${PARTIAL[@]}"   # yarım geri alma BAŞARI diye bildirilmez
    echo "GERİ ALMA YARIM KALDI — 44d5353 durumuna TAM dönülmedi. Nedeni giderip ya da uygun saatte AYNI komutla yeniden: sudo bash $0 --rollback"
    exit 4; fi
  echo "GERİ ALINDI (44d5353): engine-app ${PREV_TIP:0:7}, birim dosyaları P2 baytları (değişmedi). Yeniden kurmak için: sudo bash $0"
  echo "Daha geri (zincir): sudo bash tb-engine-${PREV_TIP:0:7}.sh --rollback (gece birimi P1a baytları → 79b27cf), sudo bash tb-engine-${P1B_TIP:0:7}.sh --rollback" \
       "(veri birimi + ilk doldurma kalkar → P1a), ardından tam kaldırma: sudo bash tb-engine-${P1A_TIP:0:7}.sh --rollback"
  exit 0
fi
if [[ "$MODE" == --backfill ]]; then
  say "İLK DOLDURMA — $BF (veri biriminin [Service] özellikleri AYNEN; süre sınırı yok; kaldığı yerden devam eder)"
  bst="$(sc_state is-active "$BF")"
  if [[ "$bst" =~ ^(active|activating)$ ]]; then
    rpy bf "$RES" "$bst" "$(bf_peak)" "$DATA_MEM" "$(basename "$0")" "$(bf_high)"
    echo "   durdurma (sonra --backfill ile devam): sudo systemctl stop $BF · ayrıntı: sudo bash $0 --check"; exit 0
  fi
  eng="$(gitx "$ENG" rev-parse HEAD 2>/dev/null || echo yok)"
  { [[ "$eng" == "$TIP" ]] || gitx "$ENG" merge-base --is-ancestor "$TIP" "$eng" 2>/dev/null; } && rc=0 || rc=1
  gate "P2.1-kurulu" "$rc" "engine-app ${eng:0:7} (hedef $T7 ya da torunu; değilse önce dağıtın)"
  [[ "$(fsha "$SD/$DSVC")" == "$DSVC_SHA256" && "$(sc_state is-enabled "$DTMR")" == enabled ]] && rc=0 || rc=1
  gate "veri-birimi" "$rc" "$DSVC sha256 ${DSVC_SHA256:0:12}… · $DTMR $(sc_state is-enabled "$DTMR") (dağıtım smoke'u geçmiş olmalı)"
  if time_ok 5; then rc=0; else rc=1; fi
  gate "saat" "$rc" "$WHY (uygun: 04:40–07:55, 08:40–11:55, 12:40–15:55, 16:40–19:55, 20:40–23:55 UTC)"
  out="$(rpy paper "$STATE" 2>&1)" && rc=0 || rc=1
  gate "PAPER" "$rc" "$out"
  mm="$(sc_show "$WORKER" MemoryMax)"; [[ "$mm" == "$MEM_EXPECT" ]] && rc=0 || rc=1
  gate "worker-MemoryMax=6G" "$rc" "MemoryMax=${mm:-?}"
  st="$(sc_state is-active "$DSVC")"; [[ ! "$st" =~ ^(active|activating)$ ]] && rc=0 || rc=1
  gate "veri-birimi-boşta" "$rc" "$DSVC ${st:-?} (00:41 çalıştırması bitsin; data.lock)"
  win1
  # disk: tahmin smoke'un universe/<gün>.json'undan × 1,3 − mevcut; VERİ biriminin sınırlarıyla (13G/17G; gece 10G/20G)
  read -r est_b est_n est_src <<<"$(rpy est "$RES" "$DISK_EST")"
  res_b="$({ du -sB1 "$RES/store" "$RES/archive_cache" 2>/dev/null || true; } | awk '$1 ~ /^[0-9]+$/ {s+=$1} END {print s+0}')"
  need=$(( ${est_b:-$DISK_EST} * 13 / 10 - ${res_b:-0} )); (( need > 0 )) || need=0
  disk_gate "$need" "$DATA_FREE_MIN" "$DATA_RES_MAX" "tahmin $(numfmt --to=si "${est_b:-$DISK_EST}") (${est_n:-0} seri, ${est_src:-?}) × 1,3 − mevcut"
  if [[ "$bst" == failed ]]; then
    echo "   önceki doldurma: Result=$(sc_show "$BF" Result) ExecMainStatus=$(sc_show "$BF" ExecMainStatus) — sıfırlanıp sürdürülür"
    systemctl reset-failed "$BF" || true
  fi
  mapfile -t PROPS < <(rpy bfprops "$SD/$DSVC" "$DDROPIN")
  (( ${#PROPS[@]} >= 30 )) || die "veri biriminden systemd-run özellikleri çıkarılamadı (${#PROPS[@]})"
  printf '   %s\n' "${PROPS[@]}"
  systemd-run --unit="$BF" --description="Trading Bot research engine — initial backfill (P1b, tb-engine-$T7)" \
    "${PROPS[@]}" "$VENV/bin/python" -s -m tradingbot engine-data --backfill || die "systemd-run $BF'yi başlatamadı (yukarıdaki hata)"
  CHANGED+=("$BF başlatıldı"); sleep 5
  bst="$(sc_state is-active "$BF")"
  rpy bf "$RES" "$bst" "$(bf_peak)" "$DATA_MEM" "$(basename "$0")" "$(bf_high)"
  if [[ "$bst" == failed ]]; then
    journalctl -u "$BF" -n 30 --no-pager 2>/dev/null | sed 's/^/   | /' || true
    die "ilk doldurma hemen düştü (Result=$(sc_show "$BF" Result), çıkış $(sc_show "$BF" ExecMainStatus)). Çıktıyı bana iletin"
  fi
  if [[ "$bst" =~ ^(active|activating)$ ]]; then echo "İLK DOLDURMA BAŞLADI. İlerleme/ETA: sudo bash $0 --backfill · günlük: sudo journalctl -u $BF -f"
  else echo "İLK DOLDURMA çalıştı ve bitti ($bst; sonuç yukarıda). Ayrıntı: sudo bash $0 --check"; exit 0; fi
  echo "Durdurma: sudo systemctl stop $BF (kaldığı yerden: sudo bash $0 --backfill). Doldurma sürerken 00:41 veri birimi SKIPPED_LOCKED (çıkış 0) olur; gece birimi etkilenmez."
  exit 0
fi
# ------------------------------------------------------------------ ön denetimler (dağıtımda ilk KALDI → hiçbir şey değişmez)
say "1/10 sunucu ve P2 (44d5353) ön koşulu (salt-okunur)"
ENG_NOW=""; if [[ -d "$ENG/.git" ]]; then ENG_NOW="$(gitx "$ENG" rev-parse HEAD 2>/dev/null || echo ?)"; fi; ENG_PREV="$ENG_NOW"
T_DEPLOY="$(date +%s)"; UOK=0; UWHY=""; if units_ok; then UOK=1; else UWHY="$WHY"; fi
if [[ "$MODE" == deploy && "$ENG_NOW" == "$TIP" && "$UOK" == 1 && "$(sc_state is-enabled "$DTMR")" == enabled && -f "$BASELINE" ]]; then
  # taban (engine-<T7>-deploy.json) yalnız bu sürümün iki smoke'u geçince yazılır; --rollback onu kaldırır
  echo "   zaten dağıtılmış ($T7; birim dosyaları 44d5353'ün baytları, veri zamanlayıcısı açık). Durum: sudo bash $0 --check"; exit 0
fi
if [[ "$ENG_NOW" == "$TIP" ]]; then   # smoke'lar bitmeden kesilmiş önceki deneme: "önceki sabit" de hedeftir
  RB_HINT="engine-app başta da $T7'deydi (önceki deneme yarım kalmış); 44d5353 durumuna dönmek için uygun saatte: sudo bash $0 --rollback"
fi
SLOTS="Uygun aralıklar (UTC): 04:40–06:45, 08:40–10:45, 12:40–14:45, 16:40–18:45, 20:40–22:45"
if time_ok 75; then ok "saat uygun ($WHY)"
elif [[ "$MODE" == deploy ]]; then die "şimdi dağıtılmaz: $WHY. $SLOTS. HİÇBİR ŞEYE DOKUNULMADI"
elif time_ok 5; then warn "dağıtım şu an BAŞLAMAZ: $WHY (kuru çalışma sürer, düşük öncelikle)"
else die "kuru çalışma da şimdi yapılmaz: $WHY (derleme ve koşucu worker turlarıyla çekişmesin; §2.9). $SLOTS. HİÇBİR ŞEYE DOKUNULMADI"; fi
why="$UWHY"   # birim dosyaları 44d5353'ün (= hedefin) baytları ve yüklü; P1a gece birimi = P2 kurulu değil
[[ -n "$why" || ( "$(sc_state is-enabled "$DTMR")" == enabled && "$(sc_state is-active "$DTMR")" == active ) ]] \
  || why="veri zamanlayıcısı açık/aktif değil ($(sc_state is-enabled "$DTMR")/$(sc_state is-active "$DTMR"))"
[[ -n "$why" || ( "$(sc_state is-enabled "$TMR")" == enabled && "$(sc_state is-active "$TMR")" == active ) ]] \
  || why="gece zamanlayıcısı açık/aktif değil ($(sc_state is-enabled "$TMR")/$(sc_state is-active "$TMR"))"
[[ -n "$why" || -n "$ENG_NOW" ]] || why="$ENG yok"
[[ -n "$why" || "$ENG_NOW" == "$PREV_TIP" ]] || gitx "$ENG" merge-base --is-ancestor "$PREV_TIP" "$ENG_NOW" 2>/dev/null \
  || why="engine-app ${ENG_NOW:0:7}, 44d5353'ü (${PREV_TIP:0:7}) içermiyor"
[[ -n "$why" || "$ENG_NOW" == "$TIP" ]] || ! gitx "$ENG" merge-base --is-ancestor "$TIP" "$ENG_NOW" 2>/dev/null \
  || why="engine-app ${ENG_NOW:0:7} hedefin ($T7) torunu: daha yeni bir motor sürümü kurulu, bu betik onu geri sarmaz"
[[ -n "$why" || -z "$(gitx "$ENG" status --porcelain 2>/dev/null)" ]] || why="engine-app'te yerel değişiklik var"
[[ -z "$why" ]] && rc=0 || rc=1
[[ -n "$why" ]] || why="birim dosyaları 44d5353'ün baytları ve yüklü (gece 768M), veri + gece zamanlayıcısı açık, engine-app ${ENG_NOW:0:7} ⊒ ${PREV_TIP:0:7}"
gate "P2-kurulu" "$rc" "$why (önce: tb-engine-${PREV_TIP:0:7}.sh)"
id -u "$SVC_USER" >/dev/null 2>&1 && [[ -x "$VENV/bin/python" ]] && rc=0 || rc=1
gate "servis-kullanıcısı" "$rc" "$SVC_USER var, $VENV/bin/python çalıştırılabilir"
getent group systemd-journal >/dev/null 2>&1 && rc=0 || rc=1
gate "günlük-grubu" "$rc" "systemd-journal grubu var (veri biriminin worker 429/418 koruması; §3.4)"
out="$(rpy paper "$STATE" 2>&1)" && rc=0 || rc=1
gate "PAPER" "$rc" "$out"
mm="$(sc_show "$WORKER" MemoryMax)"; [[ "$mm" == "$MEM_EXPECT" ]] && rc=0 || rc=1
gate "worker-MemoryMax=6G" "$rc" "MemoryMax=${mm:-?} (beklenen $MEM_EXPECT)"
st="$(sc_state is-active "$SVC") $(sc_state is-active "$DSVC") $(sc_state is-active "$BF")"
[[ ! "$st" =~ (^| )(active|activating)( |$) ]] && rc=0 || rc=1
gate "motor-boşta" "$rc" "gece/veri/doldurma: $st (bir motor çalıştırması sürerken dağıtılmaz)"
disk_gate 0
win1
out="$(rpy bkok "$RES" 2>&1)" && rc=0 || rc=1
gate "yedek-doğrulandı" "$rc" "$out"
if ! out="$(rpy abwin "$RES" "$PREV_TIP" "44d5353" 2>&1)"; then warn "$out — P2.1 motor kodunu değiştirir: yeni bir A/B dönemi açılır; 44d5353 A/B raporu yarım kalır"
else ok "$out"; fi
ram_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"; dm="$(sc_show "$DASH" MemoryMax)"; [[ "$dm" =~ ^[0-9]+$ ]] || dm=536870912
need=$(( MEM_EXPECT + dm + ENGINE_MEM + DATA_MEM + 1073741824 ))
if (( ram_kb * 1024 >= need )); then ok "bellek: worker 6G + panel $(numfmt --to=iec "$dm") + gece 768M + veri/doldurma 1G + 1G ≤ RAM $(numfmt --to=iec $((ram_kb * 1024)))"
else warn "bellek (§2.5): MemoryMax toplamı + 1G = $(numfmt --to=iec "$need") > RAM $(numfmt --to=iec $((ram_kb * 1024))). KAYITLI KARAR (2026-10-06): veri/doldurma MemoryHigh 800M → 512M" \
  "(ölçülen tepe ≈ 227 MiB), MemoryMax aynı; P2 gece birimi MemoryHigh 640M / MemoryMax 768M (2026-10-08, not 34; sentetik ölçülen tepe 217–310 MiB," \
  "≤ 0,8 × 768M = 614 MiB; bu sürümde değişmez); OOMScoreAdjust=1000 motoru önce öldürür; etki K5/V6 + A/B'de ölçülür (bilgi)"; fi
echo "   mevcut kurulum: engine-app ${ENG_NOW:-yok} · gece/veri birimi 44d5353 baytları $( ((UOK)) && echo "(yüklü)" || echo "DEĞİL")" \
     "· zamanlayıcılar gece $(sc_state is-enabled "$TMR") / veri $(sc_state is-enabled "$DTMR") · worker/panel NeedDaemonReload" \
     "$(sc_show "$WORKER" NeedDaemonReload)/$(sc_show "$DASH" NeedDaemonReload) (bu sürüm reload yapmaz) · data/research $(stat -c '%U %a' "$RES" 2>/dev/null || echo yok)"
DT_WANT="$(dropin_text)"   # drop-in'ler 44d5353'ten: bu sürüm DOKUNMAZ, yalnız içerik bu sunucu için beklenenle karşılaştırılır
for d in "$NDROPIN" "$DDROPIN"; do
  if out="$(dropin_note "$d")"; then ok "drop-in ${d#"$SD"/}: $out (dokunulmaz)"
  else warn "drop-in ${d#"$SD"/}: $out — bu sürüm birimlere dokunmaz (bilgi; bana iletin)"; fi
done
APP_SHA="$(gitx "$APP" rev-parse HEAD 2>/dev/null)" || die "çalışan app SHA'sı okunamadı ($APP, $SVC_USER ile git)"
CFG_SHA0="$(sha256sum < "$APP/config.yaml" 2>/dev/null | cut -d' ' -f1)"

say "2/10 hedef kod $T7 GEÇİCİ klonda (çalışan hiçbir şey değişmez)"
TMP="$(mktemp -d /tmp/tb-engine-"$T7".XXXXXX)"; install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 0750 "$TMP/engine-app" "$TMP/data"
chown "$SVC_USER:$SVC_GROUP" "$TMP"
make_clone "$TMP/engine-app"; W="$TMP/engine-app"
[[ "$(gitx "$W" rev-parse HEAD)" == "$TIP" ]] && rc=0 || rc=1
inv "kaynak-TIP" "$rc" "geçici klon HEAD = $T7 ($REPO_URL)"
gitx "$W" merge-base --is-ancestor "$APP_SHA" "$TIP" && rc=0 || rc=1
gate "app-SHA-ata" "$rc" "çalışan app ${APP_SHA:0:7} hedefin atası (değilse her gece SKEW)"
gitx "$W" merge-base --is-ancestor "$PREV_TIP" "$TIP" && gitx "$W" merge-base --is-ancestor "$P1B_TIP" "$PREV_TIP" \
  && gitx "$W" merge-base --is-ancestor "$P1A_TIP" "$P1B_TIP" && rc=0 || rc=1
inv "44d5353-ata" "$rc" "P2 ${PREV_TIP:0:7} (ve P1b ${P1B_TIP:0:7}, P1a ${P1A_TIP:0:7}) hedefin atası"
rc=0; for p in "${UNITS[@]}"; do
  [[ "$(fsha "$W/deploy/${p%%:*}")" == "${p##*:}" && "$(gitx "$W" show "$PREV_TIP:deploy/${p%%:*}" | sha256sum | cut -d' ' -f1)" == "${p##*:}" ]] || rc=1
done
inv "birim-sha256" "$rc" "4 birim dosyası hedefte = 44d5353'te = sabit (gece ${SVC_SHA256:0:12}…, veri ${DSVC_SHA256:0:12}…): bu sürüm birim kurmaz"
unit_contract "$W" && rc=0 || rc=1
inv "birim-sözleşmesi" "$rc" "01:37/00:41 UTC, gece 640M/768M = ENGINE_EXPECTED_MEMORY_MAX, PrivateTmp, ReadWritePaths=data/research, yalnız gece PrivateNetwork"
install -d -m 0755 "$TMP/units"
for u in "$SVC" "$DSVC"; do   # verify yorumlayıcının VARLIĞINI da denetler (sahte kökte: $VENV)
  sed "s#^ExecStart=/opt/tradingbot/venv/bin/python #ExecStart=\"$VENV/bin/python\" #" "$W/deploy/$u" > "$TMP/units/$u"
done
cp -- "$W/deploy/$TMR" "$W/deploy/$DTMR" "$TMP/units/"
if command -v systemd-analyze >/dev/null 2>&1; then
  out="$(systemd-analyze verify "$TMP/units/$SVC" "$TMP/units/$TMR" "$TMP/units/$DSVC" "$TMP/units/$DTMR" 2>&1)" && rc=0 || rc=1
  out="$(grep "tradingbot-engine" <<<"$out" | head -n 3 || true)"
else rc=1; out="systemd-analyze yok"; fi
inv "systemd-analyze-verify" "$rc" "${out:-temiz}"
v="$({ systemd-run --version 2>/dev/null || true; } | awk 'NR==1{print $2}')"; [[ "$v" =~ ^[0-9]+$ ]] && (( v >= 245 )) && rc=0 || rc=1
inv "systemd-run" "$rc" "systemd ${v:-yok} (≥ 245: ilk doldurma geçici birimi ProtectClock/ProtectKernelLogs dahil)"
out="$(svc "$W" "${LOW[@]}" "$VENV/bin/python" -s -m compileall -q tradingbot scripts 2>&1 | tail -n 3)" && rc=0 || rc=1
inv "compileall" "$rc" "${out:-tradingbot/ + scripts/ önceden derlendi}"

say "3/10 bağımsız değişmezler (pytest YOK; ayrı süreç, servis ortamı aktarılmadan, ağ kapalı)"
svc "$W" "${LOW[@]}" "$VENV/bin/python" -s tests/standalone/run_engine_invariants.py --tree "$W" --forbid-pytest --json "$TMP/inv.json" \
  | sed 's/^/   /' || true
out="$(rpy runner "$TMP/inv.json" "$INV_TESTS" 2>&1)" && rc=0 || rc=1
inv "bağımsız-koşucu" "$rc" "$out (beklenen $INV_TESTS/$INV_TESTS)"
out="$(rpy ast "$W" 2>&1)" && rc=0 || rc=1
if o2="$(svc "$W" "${LOW[@]}" "$VENV/bin/python" -s -c 'import resource as r, sys, tradingbot.cli
print("cli import RSS %d MB" % (r.getrusage(r.RUSAGE_SELF).ru_maxrss // 1024))
sys.exit(any(m.startswith("tradingbot.research_engine") for m in sys.modules))' 2>&1)"; then out="$out; $o2"
else rc=1; out="$out; tradingbot.cli import'u motoru yükledi ya da düştü: ${o2:0:200}"; fi
inv "yalıtım-AST" "$rc" "$out (birim grafiğinde config_v3/load_config/sqlite3 yok; ağ yalnız datastore; cli tembel)"
out="$(svc "$W" TRADINGBOT_DATA="$TMP/data" "$VENV/bin/python" -s -m tradingbot engine-status --brief 2>&1)" && rc=0 || rc=1
o2="$(svc "$W" TRADINGBOT_DATA="$TMP/data" "$VENV/bin/python" -s -m tradingbot engine-data --status 2>&1)" || rc=1
o3="$(svc "$W" TRADINGBOT_DATA="$TMP/data" "$VENV/bin/python" -s -m tradingbot engine-query why-lost --days 7 2>&1)" || rc=1
n_out="$(wc -l <<<"$out")"; n3="$(wc -l <<<"$o3")"; leaked="$(find "$TMP/data" -mindepth 1 | head -n 1)"
[[ "$rc" == 0 && "$n_out" -le 60 && "$n3" -le 150 && -z "$leaked" && "$o2" == *"$ENGINE_VER"* ]] && rc=0 || rc=1
inv "status-kuru" "$rc" "boş veri kökünde engine-status ($n_out satır ≤ 60), engine-data --status, engine-query ($n3 satır ≤ 150) çıkış 0, hiçbir şey yazılmadı"

say "4/10 betik"
if ! command -v shellcheck >/dev/null 2>&1; then echo "   shellcheck yok (VPS'te kurulu değil; depoda sandbox testi denetler)"
elif shellcheck -S warning "$0" >/dev/null 2>&1; then ok "shellcheck temiz"; else warn "shellcheck bulgusu (shellcheck -S warning $0)"; fi
if (( ${#INV_FAIL[@]} )); then
  [[ "$MODE" == deploy ]] && die "${#INV_FAIL[@]} değişmez KALDI: ${INV_FAIL[*]}. HİÇBİR ŞEYE DOKUNULMADI"
  echo; echo "KURU ÇALIŞMA: $((INV_N - ${#INV_FAIL[@]}))/$INV_N değişmez geçti — KALAN: ${INV_FAIL[*]}. HİÇBİR ŞEY DEĞİŞMEDİ."; exit 1
fi
if [[ "$MODE" == --dry-run ]]; then
  echo; echo "KURU ÇALIŞMA: $INV_N/$INV_N değişmez geçti. HİÇBİR ŞEY DEĞİŞMEDİ. Dağıtım: sudo bash $0  (UTC uygun aralıkta)"; exit 0
fi

# ================================================================== DAĞITIM (yalnız engine-app; birimler ve worker olduğu gibi)
time_ok 50 || die "koşucudan sonra dağıtıma yetecek süre kalmadı: $WHY (sabitleme + iki smoke). $SLOTS. HİÇBİR ŞEYE DOKUNULMADI"
# hedef commit engine-app'e ÖNCE eklenir (geçici klondan; çalışma ağacı değişmez): düşerse hiçbir şey değişmemiş olur
fetch_tip "$ENG" "$TIP" "$W"
inv "TIP-engine-app'te" 0 "hedef $T7 engine-app nesnelerinde (geçici klondan; HEAD henüz ${ENG_PREV:0:7})"
say "5/10 data/research (P1a'dan; sahiplik ve 0750)"
install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 0750 "$RES" "$RES/locks" "$RES/backup"
foreign="$(find "$RES" ! -user "$SVC_USER" -print -quit)"
[[ "$(stat -c '%U %a' "$RES")" == "$SVC_USER 750" && -z "$foreign" ]] && rc=0 || rc=1
inv "araştırma-kökü" "$rc" "$RES ($SVC_USER, 0750; locks/ backup/)${foreign:+ — YABANCI SAHİPLİ: $foreign}"
(( rc == 0 )) || die "data/research sahipliği uygun değil: sudo chown -R $SVC_USER:$SVC_GROUP $RES (sonra yeniden çalıştırın)"

say "6/10 birimler DEĞİŞMEZ: dosya yazılmaz, daemon-reload YOK, enable/disable YOK (44d5353'ün P2 birimleri = hedefinkiler)"
rc=0; for p in "${UNITS[@]}"; do cmp -s "$W/deploy/${p%%:*}" "$SD/${p%%:*}" || rc=1; done
if (( rc == 0 )) && ! units_ok; then rc=1; fi
inv "birimler-aynı" "$rc" "${WHY:-kurulu gece + veri birimi dosyaları = hedefteki baytlar, yüklü (NeedDaemonReload=no), gece MemoryMax $(sc_show "$SVC" MemoryMax), veri $(sc_show "$DSVC" MemoryMax)}"
(( rc == 0 )) || die "birim dosyaları ya da yüklü durumları ön denetimden sonra değişti (${WHY:-bayt farkı}). HİÇBİR ŞEYE DOKUNULMADI"

say "7/10 engine-app ayrı klonu ${ENG_PREV:0:7} → $T7 + compileall"
[[ "$(stat -c %U "$ENG")" == "$SVC_USER" ]] || die "$ENG $SVC_USER'a ait değil. HİÇBİR ŞEYE DOKUNULMADI"
CHANGED+=("engine-app=$T7"); pin_eng "$TIP"   # ÖNCE kaydet: yarım kalan sabitleme (checkout oldu, derleme düştü) de geri alınır
[[ "$(gitx "$ENG" rev-parse HEAD)" == "$TIP" && -z "$(gitx "$ENG" status --porcelain)" ]] && rc=0 || rc=1
inv "engine-app-sabit" "$rc" "$ENG HEAD = $T7, temiz, önceden derlendi"
(( rc == 0 )) || { back_out; die "engine-app sabitlenemedi. $(state_msg)" 2; }

say "8/10 ELLE SMOKE (veri, ABSENT_UPSTREAM yoklaması dahil): systemctl start $DSVC (geçmezse engine-app önceki sabitine döner)"
time_ok 5 || { back_out; die "smoke şimdi çalıştırılmaz: $WHY. $(state_msg)" 2; }
PRE_D="$(rpy dlast "$RES")"; T0="$(date +%s)"
src=0; timeout "$DSMOKE_TIMEOUT" systemctl start "$DSVC" || src=$?
if (( src == 124 )); then systemctl stop "$DSVC" || true; fi
ems="$(sc_show "$DSVC" ExecMainStatus)"; sres="$(sc_show "$DSVC" Result)"
echo "   smoke (start) çıkışı $src · Result=$sres · ExecMainStatus=$ems"
out=0; rpy dsmoke "$RES" "$PRE_D" "$T0" "$SVC_USER" "$ENGINE_VER" || out=1
[[ "$src" == 0 && "$sres" == success && "$ems" == 0 && "$out" == 0 ]] && rc=0 || rc=1
inv "veri-smoke" "$rc" "çıkış 0, öz-denetim OK, worker günlüğü okundu, mühür + evren, 226/NAMESPACE yok, sahiplik $SVC_USER"
if (( rc )); then
  [[ "$ems" == 226 ]] && echo "   226/NAMESPACE: ReadWritePaths/ReadOnlyPaths bağlanamadı (data/research ya da data yok?)"
  [[ "$ems" == 216 ]] && echo "   216/GROUP: SupplementaryGroups=systemd-journal uygulanamadı"
  journalctl -u "$DSVC" -n 40 --no-pager 2>/dev/null | sed 's/^/   | /' || true
  back_out
  die "veri smoke'u GEÇMEDİ (data/research kaldı). Çıktıyı bana iletin; $(state_msg)" 2
fi

say "9/10 ELLE SMOKE (gece, P2.1 kodu + değişmeyen P2 birimi): systemctl start $SVC"
time_ok 30 || { back_out; die "gece smoke'u şimdi çalıştırılmaz (S1b/S2 ile ≈ 20–25 dk): $WHY. $(state_msg)" 2; }
PRE_RID="$(rpy lastrun "$RES")"; T0="$(date +%s)"
src=0; timeout "$SMOKE_TIMEOUT" systemctl start "$SVC" || src=$?
if (( src == 124 )); then systemctl stop "$SVC" || true; fi
ems="$(sc_show "$SVC" ExecMainStatus)"; sres="$(sc_show "$SVC" Result)"
echo "   smoke (start) çıkışı $src · Result=$sres · ExecMainStatus=$ems"
out=0; rpy smoke "$RES" "$TIP" "$PRE_RID" "$T0" "$SVC_USER" "$ENGINE_VER" "$ATTR_SHA" "$CFG_SHA" "$ENGINE_MEM" "$FD_VER" || out=1
[[ "$src" == 0 && "$sres" == success && "$ems" == 0 && "$out" == 0 ]] && rc=0 || rc=1
inv "gece-smoke" "$rc" "çıkış 0, öz-denetim OK, SUCCESS + plan S1b S2, günlük _build.json $FD_VER, atıf/özet ≤ 8 KB, P2 mühürleri = sabit (değişmedi), memory.peak ≤ 0,8, sahiplik $SVC_USER"
if (( rc )); then
  journalctl -u "$SVC" -n 40 --no-pager 2>/dev/null | sed 's/^/   | /' || true
  back_out
  die "gece smoke'u GEÇMEDİ. Çıktıyı bana iletin; $(state_msg)" 2
fi
DONE=1; RB_HINT="iki smoke geçti, P2.1 kurulu (engine-app $T7, birimler 44d5353'ünki); taban kaydı yazılamadıysa aynı komutla yeniden çalıştırın"

say "10/10 son denetim (birimlere ve zamanlayıcılara DOKUNULMADI; worker; taban kaydı)"
[[ "$(sc_state is-enabled "$DTMR")" == enabled && "$(sc_state is-active "$DTMR")" == active \
   && "$(sc_state is-enabled "$TMR")" == enabled && "$(sc_state is-active "$TMR")" == active ]] && units_ok && rc=0 || rc=1
inv "birimler-zamanlayıcılar" "$rc" "${WHY:-birim dosyaları 44d5353 baytları, yüklü} · veri + gece enabled/active (değiştirilmedi) · gece sonraki $(sc_show "$TMR" NextElapseUSecRealtime)"
read -r W_PID1 W_NR1 <<<"$(worker_now)"
[[ "$W_PID1 $W_NR1" == "$W_PID0 $W_NR0" && "$(gitx "$APP" rev-parse HEAD)" == "$APP_SHA" && "$(sc_show "$WORKER" MemoryMax)" == "$MEM_EXPECT" \
   && "$(sha256sum < "$APP/config.yaml" 2>/dev/null | cut -d' ' -f1)" == "$CFG_SHA0" ]] && rc=0 || rc=1
inv "worker-dokunulmadı" "$rc" "worker PID $W_PID0→$W_PID1, NRestarts $W_NR0→$W_NR1, MemoryMax 6G; app HEAD ve config.yaml baytları aynı"
rpy baseline "$BASELINE" "$TIP" "$APP_SHA" "$W_NR1" "$W_PID1" "$(sc_show "$WORKER" MemoryMax)" "$(rpy lastrun "$RES")" \
  "$(rpy dlast "$RES")" "$T_DEPLOY" "$STATE" "$SAMPLES" || warn "taban kaydı yazılamadı ($BASELINE)"
echo
if (( ${#INV_FAIL[@]} )); then
  echo "DAĞITILDI ama $((${#INV_FAIL[@]})) değişmez KALDI: ${INV_FAIL[*]} — bu çıktıyı bana iletin."; exit 4
fi
echo "DAĞITILDI: $INV_N/$INV_N değişmez geçti. Veri birimi her gece 00:41 UTC, gece birimi 01:37 UTC (P2.1: $FD_VER + ABSENT_UPSTREAM; $T7; kayıt-yalnız; birimler 44d5353'ünki)."
echo "İşlem günlüğü satırları ilk TAM gecede ($FD_VER ile) bir kez yeniden kurulur (S1B_BACKLOG olabilir; beklenen fidelity ≈ %93–95). 14 gün her gün: sudo bash $0 --check · sonra --ab-report"
echo "Kapatma: sudo systemctl disable --now $TMR $DTMR (önce NeedDaemonReload=no) · geri alma (44d5353 durumu): sudo bash $0 --rollback"

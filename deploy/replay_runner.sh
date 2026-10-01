#!/usr/bin/env bash
# Trading Bot v3 — replay ARAŞTIRMA koşucusu (kaynak-kontrollü, durable, fail-closed).
#   sudo bash deploy/replay_runner.sh plan|replay|train|evaluate|full|status|verify <RUN_ID> [ek argümanlar...]
#
# Sözleşme:
#   * Service user (`tradingbot`) + APP cwd + açık DATA/replay state; `env -i` (env dosyası YÜKLENMEZ,
#     secret okunmaz/yazdırılmaz).
#   * PAPER + live_order_path=false zorunlu (runner'da bir kez, pipeline içinde HER AŞAMADAN ÖNCE tekrar).
#   * Kapasite `replay-plan` ile doğrulanır; plan bloklarsa hiçbir iş BAŞLAMAZ.
#   * İş TEK transient systemd SERVICE içinde `replay-pipeline` olarak çalışır: aşama sırasını systemd'nin
#     yönettiği süreç kendi yürütür → SSH/çağıran shell ölse bile pipeline DEVAM EDER. Kaynak sınırları
#     (MemoryMax/CPUQuota/Nice/IOWeight) cgroup'ta uygulanır. `systemd-run` yoksa BLOCK (sınırsız fallback yok).
#   * Her aşama geçişi `state/replay/<RUN_ID>/run_status.json` dosyasına ATOMİK yazılır; `status` gerçek
#     sonucu unit silinmiş olsa bile buradan okur.
#   * Worker/dashboard DURDURULMAZ, yeniden başlatılmaz.
#   * Aynı run-id için eşzamanlı pipeline engellenir. `--resume` UNSUPPORTED (sahte resume yok);
#     `--force` mevcut çıktıları SİLMEZ/üzerine yazmaz → yeni --run-id ister.
set -Eeuo pipefail

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="${TRADINGBOT_APP:-$BASE/app}"
VENV_PY="${TRADINGBOT_VENV_PY:-$BASE/venv/bin/python}"
DATA="${TRADINGBOT_DATA:-$BASE/data}"
STATE="${TRADINGBOT_STATE_DIR:-$DATA/state}"
SVC_USER="${TRADINGBOT_SVC_USER:-tradingbot}"
MPLDIR="${MPLCONFIGDIR:-/var/cache/tradingbot/matplotlib}"
REPLAY_MEM_MAX="${REPLAY_MEM_MAX:-2G}"
REPLAY_CPU_QUOTA="${REPLAY_CPU_QUOTA:-60%}"
REPLAY_NICE="${REPLAY_NICE:-15}"
REPLAY_IO_WEIGHT="${REPLAY_IO_WEIGHT:-20}"
REPLAY_SAFE_PCT="${REPLAY_SAFE_PCT:-80}"

usage() { echo "kullanım: $0 plan|replay|train|evaluate|full|status|verify <RUN_ID> [ek argümanlar...]" >&2; exit 2; }

ACTION="${1:-}"
RUN_ID="${2:-}"
shift 2 2>/dev/null || true
case "$ACTION" in plan|replay|train|evaluate|full|status|verify) ;; *) usage ;; esac
[[ -n "$RUN_ID" ]] || usage

# --- girdi doğrulama: unit adı / property injection engeli ---------------------------------------
[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || {
  echo "BLOCK: geçersiz RUN_ID (yalnız harf/rakam/._- , en fazla 64, harf-rakamla başlar): $RUN_ID" >&2; exit 1; }
[[ "$REPLAY_MEM_MAX" =~ ^[0-9]+[KMGT]?$ ]] || { echo "BLOCK: geçersiz REPLAY_MEM_MAX: $REPLAY_MEM_MAX" >&2; exit 1; }
[[ "$REPLAY_CPU_QUOTA" =~ ^[0-9]+%$ ]] || { echo "BLOCK: geçersiz REPLAY_CPU_QUOTA: $REPLAY_CPU_QUOTA" >&2; exit 1; }
[[ "$REPLAY_NICE" =~ ^-?[0-9]+$ ]] || { echo "BLOCK: geçersiz REPLAY_NICE: $REPLAY_NICE" >&2; exit 1; }
[[ "$REPLAY_IO_WEIGHT" =~ ^[0-9]+$ ]] || { echo "BLOCK: geçersiz REPLAY_IO_WEIGHT: $REPLAY_IO_WEIGHT" >&2; exit 1; }
[[ "$REPLAY_SAFE_PCT" =~ ^[0-9]+$ ]] || { echo "BLOCK: geçersiz REPLAY_SAFE_PCT: $REPLAY_SAFE_PCT" >&2; exit 1; }

RESUME=0; FORCE=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --resume) RESUME=1 ;;
    --force) FORCE=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}

[[ -x "$VENV_PY" ]] || { echo "BLOCK: venv python yok: $VENV_PY" >&2; exit 1; }
[[ -d "$APP" ]] || { echo "BLOCK: APP yok: $APP" >&2; exit 1; }

UNIT="tradingbot-replay-$RUN_ID"
RUN_DIR="$STATE/replay/$RUN_ID"

mem_mb() {
  local v="$1" num unit
  num="${v//[!0-9]/}"; unit="${v//[0-9]/}"
  case "$unit" in
    K) echo $(( num / 1024 )) ;;
    ""|M) echo "$num" ;;
    G) echo $(( num * 1024 )) ;;
    T) echo $(( num * 1024 * 1024 )) ;;
  esac
}
MEM_MB="$(mem_mb "$REPLAY_MEM_MAX")"

# service user olarak, APP cwd'sinden, temiz ortamla (env dosyası yok)
tb() {
  ( cd "$APP" && sudo -u "$SVC_USER" env -i \
      PATH=/usr/bin:/bin HOME="$BASE" PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=Europe/Istanbul \
      MPLCONFIGDIR="$MPLDIR" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" \
      "$VENV_PY" -m tradingbot "$@" )
}

unit_active() { systemctl is-active --quiet "$UNIT.service" 2>/dev/null; }

# --- status: kalıcı manifest + unit durumu -------------------------------------------------------
if [[ "$ACTION" == "status" ]]; then
  ACT_FLAG=()
  if unit_active; then ACT_FLAG=(--unit-active); fi
  echo "== pipeline unit"
  st="$(systemctl show "$UNIT.service" -p ActiveState --value 2>/dev/null || true)"
  rs="$(systemctl show "$UNIT.service" -p Result --value 2>/dev/null || true)"
  ec="$(systemctl show "$UNIT.service" -p ExecMainStatus --value 2>/dev/null || true)"
  printf '  %-44s state=%s result=%s exit=%s\n' "$UNIT.service" "${st:-YOK(silinmiş/collect)}" "${rs:--}" "${ec:--}"
  journalctl -u "$UNIT.service" -n 8 --no-pager 2>/dev/null | sed 's/^/      /' || true
  echo "== kalıcı durum (run_status.json + artifact'ler)"
  tb replay-status --run-id "$RUN_ID" ${ACT_FLAG[@]+"${ACT_FLAG[@]}"}
  exit $?
fi

if [[ "$ACTION" == "verify" ]]; then
  echo "== tamamlanmış replay doğrulaması (artifact'ler DEĞİŞTİRİLMEZ)"
  tb replay-verify --run-id "$RUN_ID" "$@"
  exit $?
fi

# --- resume/force sözleşmesi (fail-closed) -------------------------------------------------------
if [[ "$RESUME" -eq 1 ]]; then
  echo "BLOCK: --resume UNSUPPORTED — güvenli gerçek devam (input/config hash + atomik event checkpoint +" >&2
  echo "       ledger/learner/memory/RNG durumu + karar zaman çizelgesi) uygulanmadı; sahte resume yapılmaz." >&2
  echo "       Yeni bir --run-id ile baştan koşun ya da yalnız train/evaluate aşamalarını çalıştırın." >&2
  exit 2
fi
if [[ "$FORCE" -eq 1 ]]; then
  echo "BLOCK: --force mevcut run klasörünü SİLMEZ/üzerine yazmaz (artifact bozulmasın)." >&2
  echo "       Yeniden koşmak için YENİ bir RUN_ID kullanın; mevcut çıktılar korunur." >&2
  exit 2
fi

echo "== mod güvenliği (PAPER zorunlu)"
MODE_JSON="$(tb mode-status)"
"$VENV_PY" - "$MODE_JSON" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
if str(d.get("mode", "")).upper() != "PAPER" or d.get("live_order_path_enabled"):
    print(f"BLOCK: mod {d.get('mode')} / live_order_path={d.get('live_order_path_enabled')} — replay yalnız PAPER'da", file=sys.stderr)
    raise SystemExit(1)
print("  mod PAPER · live_order_path_enabled=false")
PY

echo "== kapasite planı (fail-closed; runner MemoryMax ${REPLAY_MEM_MAX} · güvenli pay %${REPLAY_SAFE_PCT})"
tb replay-plan --run-id "$RUN_ID" --runner-memory-max-mb "$MEM_MB" --runner-safe-pct "$REPLAY_SAFE_PCT" "$@" \
  || { echo "BLOCK: kapasite/veri planı geçmedi — iş başlatılmadı" >&2; exit 1; }

if [[ "$ACTION" == "plan" ]]; then
  echo "PLAN_OK (yalnız plan istendi; replay/eğitim/değerlendirme çalıştırılmadı)"
  exit 0
fi

# --- aşama seçimi --------------------------------------------------------------------------------
case "$ACTION" in
  replay)   STAGES="replay" ;;
  train)    STAGES="train" ;;
  evaluate) STAGES="evaluate" ;;
  full)     STAGES="replay,train,evaluate" ;;
esac

# Tamamlanmış replay'i ASLA yeniden koşturma: train/evaluate için önce artifact doğrula.
if [[ "$STAGES" == *replay* && -f "$RUN_DIR/replay_result.json" ]]; then
  echo "BLOCK: $RUN_ID zaten tamamlanmış (replay_result.json var) — mevcut replay yeniden KOŞULMAZ." >&2
  echo "       Yalnız eğitim/değerlendirme için: $0 train $RUN_ID   ve   $0 evaluate $RUN_ID" >&2
  exit 1
fi
if [[ "$STAGES" != *replay* ]]; then
  echo "== mevcut replay artifact doğrulaması (değiştirilmez)"
  tb replay-verify --run-id "$RUN_ID" || { echo "BLOCK: replay artifact'leri doğrulanamadı" >&2; exit 1; }
fi

command -v systemd-run >/dev/null 2>&1 || {
  echo "BLOCK: systemd-run yok — kaynak sınırı olmadan replay çalıştırılmaz (fail-closed)" >&2; exit 1; }
if unit_active; then
  echo "BLOCK: $UNIT.service zaten çalışıyor — aynı run-id için ikinci pipeline başlatılamaz" >&2; exit 1
fi
systemctl reset-failed "$UNIT.service" >/dev/null 2>&1 || true

echo "== pipeline (TEK transient service; aşamalar: $STAGES)"
echo "  unit: $UNIT.service · takip: journalctl -u $UNIT.service -f · durum: $0 status $RUN_ID"
echo "  not: SSH kopsa da pipeline systemd altında devam eder; ilerleme run_status.json'a yazılır."
systemd-run --quiet --unit="$UNIT" --service-type=exec --collect \
  --property="MemoryMax=$REPLAY_MEM_MAX" --property="CPUQuota=$REPLAY_CPU_QUOTA" \
  --property="Nice=$REPLAY_NICE" --property="IOWeight=$REPLAY_IO_WEIGHT" \
  --property="WorkingDirectory=$APP" --property="User=$SVC_USER" \
  --setenv=PYTHONUNBUFFERED=1 --setenv=PYTHONIOENCODING=utf-8 --setenv=TZ=Europe/Istanbul \
  --setenv=MPLCONFIGDIR="$MPLDIR" --setenv=TRADINGBOT_DATA="$DATA" --setenv=TRADINGBOT_STATE_DIR="$STATE" \
  -- "$VENV_PY" -m tradingbot replay-pipeline --run-id "$RUN_ID" --stages "$STAGES" --unit "$UNIT" \
     --limit-memory "$REPLAY_MEM_MAX" --limit-cpu "$REPLAY_CPU_QUOTA" --limit-nice "$REPLAY_NICE" \
     --limit-io "$REPLAY_IO_WEIGHT" "$@"

echo "REPLAY_PIPELINE_STARTED action=$ACTION run_id=$RUN_ID unit=$UNIT.service"
echo "  (başlatıldı; SONUÇ için: $0 status $RUN_ID — 'başarılı' demeden önce state=SUCCESS görün)"

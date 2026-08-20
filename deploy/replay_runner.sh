#!/usr/bin/env bash
# Trading Bot v3 — düşük öncelikli replay ARAŞTIRMA koşucusu (kaynak-kontrollü, fail-closed).
#   sudo bash deploy/replay_runner.sh plan|replay|train|evaluate|full|status <RUN_ID> [ek argümanlar...]
#
# Sözleşme:
#   * Service user (`tradingbot`) + APP cwd + açık DATA/replay state; `env -i` (env dosyası YÜKLENMEZ,
#     secret okunmaz/yazdırılmaz).
#   * PAPER + live_order_path=false zorunlu; değilse fail-closed.
#   * Kapasite `replay-plan` ile doğrulanır (host + worker rezervi ve runner MemoryMax'ı düşülerek);
#     plan bloklarsa hiçbir iş BAŞLAMAZ.
#   * EN AĞIR iş dahil (historical-replay) her şey transient systemd SERVICE içinde, kaynak sınırlı
#     cgroup'ta çalışır (MemoryMax/CPUQuota/Nice/IOWeight). `systemd-run` yoksa BLOCK — sınırsız
#     fallback YOKTUR.
#   * Transient service SSH oturumundan bağımsızdır (scope değil, service tipi): bağlantı kopsa da sürer.
#   * Worker/dashboard DURDURULMAZ, yeniden başlatılmaz.
#   * Aynı run-id için eşzamanlı ikinci koşu engellenir; tamamlanmış replay yalnız --resume/--force ile.
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
REPLAY_SAFE_PCT="${REPLAY_SAFE_PCT:-80}"          # plan tahmini, MemoryMax'ın en fazla %X'i olabilir

usage() { echo "kullanım: $0 plan|replay|train|evaluate|full|status <RUN_ID> [ek argümanlar...]" >&2; exit 2; }

ACTION="${1:-}"
RUN_ID="${2:-}"
shift 2 2>/dev/null || true
case "$ACTION" in plan|replay|train|evaluate|full|status) ;; *) usage ;; esac
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

# MemoryMax'ı MB'ye çevir (plan uyumu için)
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

# Kaynak sınırlı TRANSIENT SERVICE (SSH'den bağımsız; scope DEĞİL, service). systemd-run yoksa fail-closed.
tb_unit() {
  local suffix="$1"; shift
  command -v systemd-run >/dev/null 2>&1 || {
    echo "BLOCK: systemd-run yok — kaynak sınırı olmadan replay çalıştırılmaz (fail-closed)" >&2; exit 1; }
  local unit="$UNIT-$suffix"
  if systemctl is-active --quiet "$unit.service" 2>/dev/null; then
    echo "BLOCK: $unit.service zaten çalışıyor — aynı run-id ikinci kez başlatılamaz" >&2; exit 1
  fi
  systemctl reset-failed "$unit.service" >/dev/null 2>&1 || true
  echo "  unit: $unit.service · takip: journalctl -u $unit.service -f · durum: $0 status $RUN_ID"
  systemd-run --quiet --unit="$unit" --service-type=exec --wait --collect \
    --property="MemoryMax=$REPLAY_MEM_MAX" --property="CPUQuota=$REPLAY_CPU_QUOTA" \
    --property="Nice=$REPLAY_NICE" --property="IOWeight=$REPLAY_IO_WEIGHT" \
    --property="WorkingDirectory=$APP" --property="User=$SVC_USER" \
    --setenv=PYTHONUNBUFFERED=1 --setenv=PYTHONIOENCODING=utf-8 --setenv=TZ=Europe/Istanbul \
    --setenv=MPLCONFIGDIR="$MPLDIR" --setenv=TRADINGBOT_DATA="$DATA" --setenv=TRADINGBOT_STATE_DIR="$STATE" \
    -- "$VENV_PY" -m tradingbot "$@"
}

# --- status --------------------------------------------------------------------------------------
if [[ "$ACTION" == "status" ]]; then
  echo "== transient unit durumları"
  for suffix in replay train evaluate; do
    u="$UNIT-$suffix.service"
    st="$(systemctl show "$u" -p ActiveState --value 2>/dev/null || echo "yok")"
    rs="$(systemctl show "$u" -p Result --value 2>/dev/null || echo "-")"
    ec="$(systemctl show "$u" -p ExecMainStatus --value 2>/dev/null || echo "-")"
    printf '  %-42s state=%s result=%s exit=%s\n' "$u" "${st:-yok}" "${rs:--}" "${ec:--}"
    journalctl -u "$u" -n 5 --no-pager 2>/dev/null | sed 's/^/      /' || true
  done
  echo "== replay artifact'leri ($RUN_DIR)"
  for f in replay_result.json train_manifest.json evaluation.json trade_memory.jsonl models.json; do
    if [[ -f "$RUN_DIR/$f" ]]; then printf '  %-22s %s bayt\n' "$f" "$(stat -c %s "$RUN_DIR/$f")"; else printf '  %-22s YOK\n' "$f"; fi
  done
  exit 0
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

if [[ "$ACTION" == "replay" || "$ACTION" == "full" ]]; then
  if [[ -f "$RUN_DIR/replay_result.json" && "$RESUME" -eq 0 && "$FORCE" -eq 0 ]]; then
    echo "BLOCK: $RUN_ID zaten tamamlanmış (replay_result.json var) — yeniden koşmak için --resume ya da --force verin" >&2
    exit 1
  fi
  echo "== historical-replay (kaynak sınırlı transient service; en ağır iş)"
  tb_unit replay historical-replay --run-id "$RUN_ID" "$@"
fi
if [[ "$ACTION" == "train" || "$ACTION" == "full" ]]; then
  echo "== challenger eğitimi (yalnız replay state; canlı model/terfi yok)"
  tb_unit train replay-train --run-id "$RUN_ID"
fi
if [[ "$ACTION" == "evaluate" || "$ACTION" == "full" ]]; then
  echo "== walk-forward OOS değerlendirmesi (yalnız rapor)"
  tb_unit evaluate replay-evaluate --run-id "$RUN_ID"
fi

echo "REPLAY_RUNNER_OK action=$ACTION run_id=$RUN_ID"

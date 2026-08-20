#!/usr/bin/env bash
# Trading Bot v3 — düşük öncelikli replay ARAŞTIRMA koşucusu (kaynak-kontrollü).
#   sudo bash deploy/replay_runner.sh plan|train|evaluate|full <RUN_ID> [ek argümanlar...]
#
# Sözleşme:
#   * Service user (`tradingbot`) + APP cwd + açık DATA/replay state kullanılır.
#   * Worker/dashboard DURDURULMAZ; iş `systemd-run --scope` ile ayrı, sınırlı bir cgroup'ta çalışır
#     (MemoryMax/CPUQuota/Nice/IOWeight) → worker'ın 4G'lik cgroup'u etkilenmez.
#   * PAPER + live_order_path=false zorunlu; değilse fail-closed çıkar.
#   * Kapasite kontrolü `replay-plan` ile yapılır (host + worker rezervi düşülür); plan bloklarsa iş BAŞLAMAZ.
#   * Env/API anahtarı okunmaz, yazdırılmaz; EnvironmentFile yüklenmez.
set -Eeuo pipefail

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="${TRADINGBOT_APP:-$BASE/app}"
VENV_PY="${TRADINGBOT_VENV_PY:-$BASE/venv/bin/python}"
DATA="${TRADINGBOT_DATA:-$BASE/data}"
STATE="${TRADINGBOT_STATE_DIR:-$DATA/state}"
SVC_USER="${TRADINGBOT_SVC_USER:-tradingbot}"
REPLAY_MEM_MAX="${REPLAY_MEM_MAX:-2G}"
REPLAY_CPU_QUOTA="${REPLAY_CPU_QUOTA:-60%}"
REPLAY_NICE="${REPLAY_NICE:-15}"
REPLAY_IO_WEIGHT="${REPLAY_IO_WEIGHT:-20}"

ACTION="${1:-}"
RUN_ID="${2:-}"
shift 2 2>/dev/null || true

usage() { echo "kullanım: $0 plan|train|evaluate|full <RUN_ID> [ek argümanlar...]" >&2; exit 2; }
case "$ACTION" in plan|train|evaluate|full) ;; *) usage ;; esac
[[ -n "$RUN_ID" ]] || usage
[[ -x "$VENV_PY" ]] || { echo "BLOCK: venv python yok: $VENV_PY" >&2; exit 1; }
[[ -d "$APP" ]] || { echo "BLOCK: APP yok: $APP" >&2; exit 1; }

# service user olarak, APP cwd'sinden, açık DATA/STATE ile (env dosyası YÜKLENMEZ)
tb() {
  ( cd "$APP" && sudo -u "$SVC_USER" env -i \
      PATH=/usr/bin:/bin HOME="$BASE" \
      PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=Europe/Istanbul \
      MPLCONFIGDIR="${MPLCONFIGDIR:-/var/cache/tradingbot/matplotlib}" \
      TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" \
      "$VENV_PY" -m tradingbot "$@" )
}

# sınırlı cgroup içinde (worker'ı etkilemez); systemd-run yoksa nice/ionice ile devam
tb_scoped() {
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --quiet --scope --collect \
      --unit="tradingbot-replay-$RUN_ID-$$" \
      -p "MemoryMax=$REPLAY_MEM_MAX" -p "CPUQuota=$REPLAY_CPU_QUOTA" \
      -p "Nice=$REPLAY_NICE" -p "IOWeight=$REPLAY_IO_WEIGHT" \
      -- bash -c 'cd "$1" && shift && sudo -u "$1" env -i PATH=/usr/bin:/bin HOME="$2" PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=Europe/Istanbul MPLCONFIGDIR="$3" TRADINGBOT_DATA="$4" TRADINGBOT_STATE_DIR="$5" "$6" -m tradingbot "${@:7}"' \
      _ "$APP" "$SVC_USER" "$BASE" "${MPLCONFIGDIR:-/var/cache/tradingbot/matplotlib}" "$DATA" "$STATE" "$VENV_PY" "$@"
  else
    echo "  (systemd-run yok — nice/ionice ile çalıştırılıyor)"
    nice -n "$REPLAY_NICE" ionice -c3 bash -c 'true'
    tb "$@"
  fi
}

echo "== mod güvenliği (PAPER zorunlu)"
MODE_JSON="$(tb mode-status)"
"$VENV_PY" - "$MODE_JSON" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
if str(d.get("mode", "")).upper() != "PAPER" or d.get("live_order_path_enabled"):
    print(f"BLOCK: mod {d.get('mode')} / live_order_path={d.get('live_order_path_enabled')} — replay yalnız PAPER'da", file=sys.stderr)
    raise SystemExit(1)
print(f"  mod PAPER · live_order_path_enabled=false")
PY

echo "== kapasite planı (fail-closed)"
tb replay-plan --run-id "$RUN_ID" "$@" || { echo "BLOCK: kapasite/veri planı geçmedi — iş başlatılmadı" >&2; exit 1; }

if [[ "$ACTION" == "plan" ]]; then
  echo "PLAN_OK (yalnız plan istendi; eğitim/değerlendirme çalıştırılmadı)"
  exit 0
fi

if [[ "$ACTION" == "train" || "$ACTION" == "full" ]]; then
  echo "== challenger eğitimi (yalnız replay state; canlı model/terfi yok)"
  tb_scoped replay-train --run-id "$RUN_ID"
fi
if [[ "$ACTION" == "evaluate" || "$ACTION" == "full" ]]; then
  echo "== OOS değerlendirmesi (yalnız rapor)"
  tb_scoped replay-evaluate --run-id "$RUN_ID"
fi

echo "REPLAY_RUNNER_OK action=$ACTION run_id=$RUN_ID"

#!/usr/bin/env bash
# Bellek kanıtı toplayıcı — TEK ÖRNEK alır, bir JSONL satırı ekler, çıkar.
#
# NEDEN: worker 2026-09-09 15:57:25Z'de 4 GiB cgroup sınırında OOM ile öldürüldü. Akış/memo
# onarımından sonra tek turda tepe 4,15 GB → 3,78 GB ölçüldü. TEK TUR OOM İŞİNİ KAPATMAZ:
# sınıra ne kadar yaklaşıldığı, sınırın gerçekten aşılıp aşılmadığı ve yeniden başlatma olup
# olmadığı SAATLER boyunca, aynı birimlerle, kesintisiz kaydedilmelidir. Bu betik onu yapar ve
# systemd timer'ı sayesinde herhangi bir oturuma bağlı DEĞİLDİR.
#
# Kurulum (VPS'te bir kez):
#   sudo install -m 0755 /opt/tradingbot/app/deploy/memory_probe.sh /opt/tradingbot/memory_probe.sh
#   sudo cp /opt/tradingbot/app/deploy/tradingbot-memprobe.{service,timer} /etc/systemd/system/
#   sudo systemctl daemon-reload && sudo systemctl enable --now tradingbot-memprobe.timer
# Okuma:
#   python -m tradingbot memory-report          (ya da scripts/memory_probe_report.py)
#
# Bütün bellek alanları BAYT'tır. Tek birim, tek yorum.
set -uo pipefail

UNIT="${MEMPROBE_UNIT:-tradingbot-worker.service}"
OUT="${MEMPROBE_OUT:-${TRADINGBOT_LOG_DIR:-/opt/tradingbot/data/logs}/memory_probe.jsonl}"
STATE_DIR="${TRADINGBOT_STATE_DIR:-/opt/tradingbot/data/state}"

read_first() { [ -r "$1" ] && head -n1 "$1" 2>/dev/null || echo ""; }

# cgroup v2 yolu: systemd'nin kendi bildirdiği yol tek doğru kaynaktır (slice adı varsayılmaz).
CG_REL="$(systemctl show -p ControlGroup --value "$UNIT" 2>/dev/null || echo "")"
CG="/sys/fs/cgroup${CG_REL}"

mem_max="$(read_first "$CG/memory.max")"
mem_cur="$(read_first "$CG/memory.current")"
mem_peak="$(read_first "$CG/memory.peak")"
swap_cur="$(read_first "$CG/memory.swap.current")"

# memory.events: low/high/max/oom/oom_kill — "max" sınıra dayanma, "oom_kill" gerçek öldürme sayısıdır.
ev_low=0; ev_high=0; ev_max=0; ev_oom=0; ev_oomkill=0
if [ -r "$CG/memory.events" ]; then
  while read -r k v; do
    case "$k" in
      low) ev_low="$v" ;; high) ev_high="$v" ;; max) ev_max="$v" ;;
      oom) ev_oom="$v" ;; oom_kill) ev_oomkill="$v" ;;
    esac
  done < "$CG/memory.events"
fi

pid="$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null || echo 0)"
rss_bytes=0; vsz_bytes=0
if [ "${pid:-0}" -gt 0 ] && [ -r "/proc/$pid/status" ]; then
  rss_kb="$(awk '/^VmRSS:/{print $2}' "/proc/$pid/status" 2>/dev/null || echo 0)"
  vsz_kb="$(awk '/^VmSize:/{print $2}' "/proc/$pid/status" 2>/dev/null || echo 0)"
  rss_bytes=$(( ${rss_kb:-0} * 1024 ))
  vsz_bytes=$(( ${vsz_kb:-0} * 1024 ))
fi

active="$(systemctl show -p ActiveState --value "$UNIT" 2>/dev/null || echo unknown)"
substate="$(systemctl show -p SubState --value "$UNIT" 2>/dev/null || echo unknown)"
nrestarts="$(systemctl show -p NRestarts --value "$UNIT" 2>/dev/null || echo 0)"
since="$(systemctl show -p ActiveEnterTimestamp --value "$UNIT" 2>/dev/null || echo "")"

# Tur kanıtı: heartbeat + health, worker'ın KENDİ yazdığı dosyalardan (tahmin yok).
hb="$(read_first "$STATE_DIR/heartbeat.json")"
health_secs=""
if [ -r "$STATE_DIR/health.json" ]; then
  health_secs="$(python3 -c "
import json,sys
try:
    d=json.load(open('$STATE_DIR/health.json',encoding='utf-8'))
    print(d.get('last_tour_seconds') or d.get('tour_seconds') or '')
except Exception:
    print('')
" 2>/dev/null || echo "")"
fi

snap_bytes=0
[ -r "$STATE_DIR/entry_snapshot.jsonl" ] && snap_bytes="$(stat -c%s "$STATE_DIR/entry_snapshot.jsonl" 2>/dev/null || echo 0)"
path_bytes=0
[ -r "$STATE_DIR/position_path.jsonl" ] && path_bytes="$(stat -c%s "$STATE_DIR/position_path.jsonl" 2>/dev/null || echo 0)"

mkdir -p "$(dirname "$OUT")"
printf '{"ts":"%s","unit":"%s","cgroup":"%s","active":"%s","substate":"%s","nrestarts":%s,"active_since":"%s","pid":%s,"memory_max_bytes":"%s","memory_current_bytes":"%s","memory_peak_bytes":"%s","memory_swap_current_bytes":"%s","events":{"low":%s,"high":%s,"max":%s,"oom":%s,"oom_kill":%s},"rss_bytes":%s,"vsz_bytes":%s,"entry_snapshot_bytes":%s,"position_path_bytes":%s,"last_tour_seconds":"%s","heartbeat":%s}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$UNIT" "$CG_REL" "$active" "$substate" "${nrestarts:-0}" "$since" "${pid:-0}" \
  "${mem_max:-}" "${mem_cur:-}" "${mem_peak:-}" "${swap_cur:-}" \
  "${ev_low:-0}" "${ev_high:-0}" "${ev_max:-0}" "${ev_oom:-0}" "${ev_oomkill:-0}" \
  "${rss_bytes:-0}" "${vsz_bytes:-0}" "${snap_bytes:-0}" "${path_bytes:-0}" "${health_secs:-}" \
  "${hb:-null}" >> "$OUT"

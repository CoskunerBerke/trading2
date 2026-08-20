#!/usr/bin/env bash
# Trading Bot v3 — systemd worker ExecStartPre preflight'i (kaynak-kontrollü; setup bunu
# /opt/tradingbot/preflight.sh hedefine atomik kurar; elle oluşturulan kopya source of truth DEĞİLDİR).
#
# Karar Python katmanındadır (`tradingbot preflight` → ops/preflight.decide, TİPLENMİŞ doctor sonucu):
#   * doctor tamamen başarılı                → exit 0 (başla)
#   * yalnız HEARTBEAT_STALE hatası          → exit 0 + WARNING (worker kapalıyken beklenen durum)
#   * diğer her hata / crash / geçersiz çıktı → exit ≠0 (fail-closed; worker başlamaz)
# Bu script hiçbir state/heartbeat/ledger/config dosyası YAZMAZ; secret/env değeri LOGLAMAZ.
set -Eeuo pipefail

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="${TRADINGBOT_APP:-$BASE/app}"
VENV_PY="${TRADINGBOT_VENV_PY:-$BASE/venv/bin/python}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "BLOCK: venv python yok/çalıştırılamaz: $VENV_PY (fail-closed)" >&2
  exit 1
fi
if [[ ! -d "$APP" ]]; then
  echo "BLOCK: APP dizini yok: $APP (fail-closed)" >&2
  exit 1
fi

# `python -m tradingbot` modül çözümlemesi çağıranın dizinine bağlı kalmasın diye APP'ten çalıştırılır.
cd "$APP"
exec "$VENV_PY" -m tradingbot preflight --quick

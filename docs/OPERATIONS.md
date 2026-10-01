# OPERATIONS

- Sağlık durumları: HEALTHY, DEGRADED, PAUSED, KILL_SWITCH, DATA_STALE, RECONCILIATION_REQUIRED (`ops/health.py`). `state/heartbeat.json` her turda; `python -m tradingbot health` (heartbeat yaşı > `monitoring.heartbeat_stale_s` → DATA_STALE, exit 1). Dashboard `/health/live`, `/health/ready` (503 sağlıksızsa), `/metrics` (Prometheus).
- Log: JSON satır (`ts, level, logger, run_id, msg`), `logs/` altında rotasyon 20MB×5, sır redaksiyonu (`monitoring.json_logs`).
- Doctor: `python -m tradingbot doctor [--quick]` — config, state yazılabilir, kilit, JSON şema, vault, disk, saat, bağımlılıklar, DB bütünlüğü, yedek tazeliği, heartbeat, mod PAPER, `ALLOW_LIVE_TRADING` yok. Docker/systemd healthcheck bunu kullanır.
- Kill switch: `risk-status` ile gör, `killswitch-reset --operator <ad> --note "<neden>"` ile sıfırla (denetim kaydı).
- Mod: `mode-status`, `mode-transition` (manuel).
- Günlük rutin: `health`, `paper-status`, `risk-status`, `model-status`; haftalık `validate-model`, `backup --daily`, `doctor`.
- Bildirim: `ops/notify.py` Log/Telegram/Discord — env yoksa sessiz (credential olmadan çalışmaz).
- Incident'ler: `Trading_bot/Operations/Incidents.md` (cap 200, aylık arşiv), `state/health.json`, log, dashboard `/health`.

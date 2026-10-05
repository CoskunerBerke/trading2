# OPERATIONS

- Sağlık durumları: HEALTHY, DEGRADED, PAUSED, KILL_SWITCH, DATA_STALE, RECONCILIATION_REQUIRED (`ops/health.py`). `state/heartbeat.json` her turda; `python -m tradingbot health` (heartbeat yaşı > `monitoring.heartbeat_stale_s` → DATA_STALE, exit 1). Dashboard `/health/live`, `/health/ready` (503 sağlıksızsa), `/metrics` (Prometheus).
- Log: JSON satır (`ts, level, logger, run_id, msg`), `logs/` altında rotasyon 20MB×5, sır redaksiyonu (`monitoring.json_logs`).
- Doctor: `python -m tradingbot doctor [--quick]` — config, state yazılabilir, kilit, JSON şema, vault, disk, saat, bağımlılıklar, DB bütünlüğü, yedek tazeliği, heartbeat, mod PAPER, `ALLOW_LIVE_TRADING` yok. Docker/systemd healthcheck bunu kullanır.
- Kill switch: `risk-status` ile gör, `killswitch-reset --operator <ad> --note "<neden>"` ile sıfırla (denetim kaydı).
- Mod: `mode-status`, `mode-transition` (manuel).
- Günlük rutin: `health`, `paper-status`, `risk-status`, `model-status`; haftalık `validate-model`, `backup --daily`, `doctor`.
- Bildirim: `ops/notify.py` Log/Telegram/Discord — env yoksa sessiz (credential olmadan çalışmaz).
- Incident'ler: `Trading_bot/Operations/Incidents.md` (cap 200, aylık arşiv), `state/health.json`, log, dashboard `/health`.
- Tur süresi ve pattern kanıtı alt süreci (2026-10-05; dağıtım `deploy/releases/tb-deploy-8db1faf.sh`): normal tur ~5,5 dk; 4h kapanışından sonraki indeks yayımı bir turun içine düşerse (yayım çakışması) düzeltmeden önce ~43 dk sürüyordu. Kanıt sorguları artık fork'lu tek bir alt süreçte koşar (`history.evidence_subprocess`, kod varsayılanı açık; kararlar aynı). `--check` "PATTERN KANITI ALT SÜRECİ + YAYIM ÇAKIŞMASI" bölümünde alt sürecin en yüksek özel belleğini, canlı alt süreci, worker cgroup `memory.peak` / `MemoryMax`'ı ve çakışma turlarını basar; 35 dk geri alma tetiği aynıdır. Kapatma: `config.yaml` → `history:` altına `evidence_subprocess: false` + worker restart. Ayrıntı: [TOUR_CONTENTION_V1.md](TOUR_CONTENTION_V1.md) §8–§9.

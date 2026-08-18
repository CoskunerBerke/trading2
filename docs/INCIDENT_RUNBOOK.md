# INCIDENT RUNBOOK

| Belirti | Kontrol | Aksiyon |
|---|---|---|
| `health` DATA_STALE / heartbeat yaşlı | `journalctl`/`logs/*.jsonl`, `doctor` | ağ/Binance/TradingView erişimi; `watch` yeniden başlat; kilit dosyası (`state/.lock`) sahipsiz kaldıysa sil |
| Kill switch tetiklendi | `risk-status` (reasons/audit) | nedeni gider; `killswitch-reset --operator <ad> --note "<neden>"`; asla otomatik reset yok |
| `RECONCILIATION_MISMATCH` | `reconcile` | defter ↔ journal farkı; yedekten `restore` veya manuel düzeltme; sonra reset |
| Bozuk state JSON | `state/*.corrupt-N`, `.bak` | `.bak` otomatik kullanılır; kalıcı bozulmada `restore` |
| Disk dolu / DB kilitli | `doctor`, `df -h` | yer aç, `backup` retention; kill switch HALT_ALL'dan reset |
| Rate-limit ban (418) | log `BannedError` | cooldown süresi kadar bekle; tur aralığını artır |
| LLM bütçe/şema hataları | `llm` sayfası, `state/llm_calls.jsonl` | bot paper devam eder; provider'ı `noop` yap ya da bütçe artır |
| Model drift | `model-status` (`drift`) | challenger eğit (`validate-model`), gerekirse manuel terfi; PAPER dışı modda terfi manuel |
| Dashboard 503 | `/health/ready` checks | heartbeat/health dosyalarını kontrol et |
Her incident: `Operations/Incidents.md`, `state/health.json`, log. Kritik incident sonrası 30 gün sayacı (graduation kapısı) sıfırlanır.

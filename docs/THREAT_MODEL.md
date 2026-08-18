# THREAT MODEL

| Tehdit | Etki | Önlem |
|---|---|---|
| API anahtarı sızıntısı (log/vault/dashboard/LLM/git) | hesap ele geçirme | env-only, redaksiyon, dashboard whitelist, `.gitignore`, testler |
| LLM prompt enjeksiyonu / şema dışı yanıt | yanlış işlem | şema doğrulama, fail-closed, LLM yalnız veto, deterministik kapılar |
| Bayat/çelişkili veri | yanlış fiyatla fill/stop | DataQualityGate, ticker yaşı, mark/last/REST-WS sapması → DATA_INVALID / kill switch |
| Crash sırasında yarım yazım | state/öğrenme kaybı | atomik yazım + .bak, kayıt sırası defter→öğrenme→tetik, `.corrupt-N` saklama |
| Çift yazar (PC + VPS) | defter bozulması | singleton kilit (`state/.lock`) |
| Duplicate order (timeout) | çift pozisyon | clientOrderId registry, UNKNOWN→RECONCILING, asla kör retry |
| Rate-limit ban / clock drift | veri kesintisi | ağırlık bütçesi, backoff, drift kontrolü → kill switch |
| Yanlış config (risk) | aşırı risk | typed doğrulama, ConfigError → başlamaz; profil uyarıları |
| Overfitting / yanlış edge | zarar | WFO + purge/embargo, bootstrap CI, DSR, MC DD, champion/challenger kapısı, shadow trades |
| Sessiz LIVE geçişi | gerçek para kaybı | manuel geçiş, üç kilit, bu sürümde kod yolu kapalı |
| Public dashboard | bilgi sızıntısı | 127.0.0.1 varsayılan, token, salt okunur |
| Vault/repo bloat | kullanılamaz Obsidian/git | Signals retention, skip-if-unchanged, prune, PNG'leri git_sync dışında tutma önerisi |

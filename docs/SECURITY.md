# SECURITY

- Varsayılan PAPER; API trade anahtarı yok. Anahtar sohbet içinde istenmez; yalnız env / `.env` / secret store (`deploy/env.example`), `.env` git dışı.
- Sırlar loglara (`ops/logging_setup.py` redaksiyon filtresi), Obsidian'a, dashboard yanıtlarına, LLM istemine (`llm/prompts.py::redact`) yazılmaz — `tests/test_security_chaos.py` doğrular. Kodda withdrawal endpointi yok.
- LLM süreci execution secret'ı görmez; servis defter/gateway referansı taşımaz.
- LIVE üç kilit + typed token; bu sürümde emir yolu kapalı. Testnet gateway'leri opt-in.
- Dashboard `127.0.0.1`; public bind için `TRADINGBOT_DASHBOARD_TOKEN` (Bearer) zorunlu, HTTPS/tünel (SSH, Tailscale) önerilir; salt okunur.
- Docker: non-root uid 10001, `no-new-privileges`, cap_drop ALL, `.dockerignore` (state/data/vault/.git imaja girmez), sırlar imajda değil `.env`'de. systemd: `User=tradingbot`, `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths` yalnız data.
- İleride API bağlanırsa: ayrı sub-account, spot/futures ayrı anahtar, withdrawal/transfer izni kapalı, IP whitelist, testnet/live anahtarları ayrı, analyzer ↔ execution süreç ayrımı.
- Public repo: runtime state/DB/log/backup/vergi raporu/hesap snapshot'ı commit edilmez (`.gitignore`); Obsidian vault senkronu ayrı **private** repo olmalı.

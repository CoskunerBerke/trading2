# BACKUP / RESTORE

- `python -m tradingbot backup --hourly` → `backups/hourly/tradingbot-hourly-<ts>.tar.gz` (+ `.sha256`), SQLite `.backup` (WAL dahil), state JSON'ların atomik kopyası; `--daily` → state + vault. Retention: 24 saatlik / 7 günlük / 4 haftalık (config `storage.keep_*`).
- Doğrulama: `ops/backup.py::verify_backup` (checksum). Geri yükleme: `python -m tradingbot restore <arşiv>` kuru çalıştırma; `--yes` ile uygular, mevcut state `state.pre-restore-<ts>` olarak saklanır; sonra `doctor`.
- VPS: `deploy/backup.sh` (`tradingbot-backup.timer`), compose `backup` servisi saatte bir çalışır. Yedekleri VPS dışına (private object storage / rsync, şifreli) taşıyın; public repoya asla.
- Restore testi: ayda bir tmp dizine `restore` + `doctor` (`tests/test_ops.py` roundtrip'i doğrular).
- Öğrenme arşivi: `state/decision_archive/` ve `state/shadow_archive/` (manifest + `.jsonl.gz` segmentler) `_copy_tree_state` özyinelemeli kopyaladığı için yedeğe **otomatik** dahildir. `tests/test_outcome_learning_retention.py::test_15_backup_restore_preserves_lifetime_records_and_checksums` yedeğin aktif günlük + segmentler + manifesti birlikte taşıdığını, restore sonrası ömür boyu kayıt sayısının, segment sha256'larının ve outcome bağlantılarının birebir korunduğunu doğrular.

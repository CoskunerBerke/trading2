"""Sürekli öğrenme motoru v1 (`research_engine`) — P1a: kapanış/hareket arşivi, gecelik ölçülmüş anlık görüntü ve
günlük hedef ölçümü (`closes`, `daily_target`) ile gece birimi çalışma zamanı (`selfcheck` S0, `night` orkestrasyon,
`summary` S7, `backup` S7b). P1b: araştırma veri deposu (`store.ResearchStore`, `HistoryStore` alt sınıfı), araştırma
evreni U_R (`universe`), worker deposundan tutarlı tohum (`seed`), ağlı veri birimi (`datastore`: arşiv-önce ekleme,
korumalı REST kuyruğu, `data_status.json` + `data_seal`, Dukascopy aynası içe alma) ve ağsız okuyucu
(`provider.StoreProvider`, mühürlü okuma). P2: işlem günlüğü `tj_v1` (`journal`, `pathrec`, `rehydrate`, `fidelity`,
`utc_day`, `lazy1m`) ve "neden kaybetti / nasıl kâra dönebilirdi" (`attribution` `attribution_v1`, `cfgrid` `cfgrid_v1`,
`analysis` S2, `report`/`query` özet ve `engine-query`). Tasarım: `docs/SYSTEM_LEARNING_ENGINE_V1.md` (§2, §3, §4, §5,
§7, §9, §10 P1a/P1b/P2). Giriş noktaları yalnız `cli_v3`'ün `engine-night` / `engine-status [--brief]` /
`engine-restore` / `engine-data` / `engine-query` işleyicileridir (tembel import; config yüklenmez); birimler
`deploy/tradingbot-engine-night.*` ve (P1b) `deploy/tradingbot-engine-data.*`.

İlkeler (belgeden, kodda zorlanır):

* **Karar-nötr ve kayıt-yalnız.** Motor hiçbir karar dosyasına yazmaz; `data/state` ve `data/market` altını yalnız
  OKUR (ledger'lar `"r"` kipinde açılır, bkz. `ledgers.py`). Bütün yazımlar `data/research` altındadır
  (`paths.EnginePaths.write_*` bunu her yazımda denetler).
* **Ağ yalnız veri biriminde.** Ağ kullanan TEK modül `datastore`'dur (P1b); gece birimi `PrivateNetwork=yes` ile
  çalışır ve import grafiği `datastore`/`pit_universe`'i içermez (test; P2'den itibaren mühürlü depo okuyucusunu içerir).
* **Yasaklı import'lar.** `config_v3`, `config.load_config` ve `sqlite3` bu paket tarafından import edilmez (AST testi);
  canlı config yalnız ham YAML olarak okunur (`rawconfig.py`). Karar modülleri bu paketi import etmez; `cli.py` /
  `cli_v3.py` paketi yalnız kendi işleyicileri içinde (tembel) import eder.
* **Dürüst etiket.** Günlük +%1 hedefi ÖLÇÜLÜR, iddia edilmez: yalnız-gerçekleşmiş bir satırda `TUTTU` / `HEDEF GÜNÜ`
  kelimesi hiç geçmez; gün satırı kesinleşmeden (`KESİN`) hüküm verilmez; eksik gün `EKSİK`tir, sıfır sayılmaz.

Gece birimi yolu bilerek hafiftir: yalnız standart kütüphane ve (ham config için) PyYAML kullanır. `pandas` yalnız
işçinin HistoryStore'undan spot mark okunurken ve yalnız gerektiğinde, fonksiyon içinde import edilir. Dış süreç olarak
yalnız S0'da `git merge-base --is-ancestor` (SKEW) ve `systemctl is-active tradingbot-backup.service` çağrılır; `socket`
yalnız S0'ın ağ RET denemesinde kullanılır (bağlantı KURULAMAMALIDIR). P1b veri birimi ek olarak pandas/pyarrow,
paket dışından yalnız `history.store` (alt sınıf için) ve `market.ratelimit` (`BudgetPool`) kullanır; dış süreç olarak
yalnız `journalctl -u tradingbot-worker` (salt-okunur, REST koruması) çağrılır.
"""
from __future__ import annotations

#: Motor şema/sürüm etiketleri (çıktılara yazılır). P2 (2026-10-08): `research_engine_v1_p2` — P2 sürüm betiği
#: `ENGINE_VER`'i bununla sabitler; motor kod özeti değiştiği için yeni A/B dönemi açılır (§2.9, beklenen).
ENGINE_VERSION = "research_engine_v1_p2"

__all__ = ["ENGINE_VERSION"]

"""Sürekli öğrenme motoru v1 (`research_engine`) — P1a çekirdeği: kapanış/hareket arşivi, gecelik ölçülmüş anlık
görüntü ve günlük hedef ölçümü. Tasarım: `docs/SYSTEM_LEARNING_ENGINE_V1.md` (§2, §4, §7, §9, §10 P1a).

İlkeler (belgeden, kodda zorlanır):

* **Karar-nötr ve kayıt-yalnız.** Motor hiçbir karar dosyasına yazmaz; `data/state` ve `data/market` altını yalnız
  OKUR (ledger'lar `"r"` kipinde açılır, bkz. `ledgers.py`). Bütün yazımlar `data/research` altındadır
  (`paths.EnginePaths.write_*` bunu her yazımda denetler).
* **Ağsız.** P1a'da hiçbir modül ağ kullanmaz; gece birimi `PrivateNetwork=yes` ile çalışır.
* **Yasaklı import'lar.** `config_v3`, `config.load_config` ve `sqlite3` bu paket tarafından import edilmez (AST testi);
  canlı config yalnız ham YAML olarak okunur (`rawconfig.py`). Karar modülleri bu paketi import etmez; `cli.py` /
  `cli_v3.py` paketi yalnız kendi işleyicileri içinde (tembel) import eder.
* **Dürüst etiket.** Günlük +%1 hedefi ÖLÇÜLÜR, iddia edilmez: yalnız-gerçekleşmiş bir satırda `TUTTU` / `HEDEF GÜNÜ`
  kelimesi hiç geçmez; gün satırı kesinleşmeden (`KESİN`) hüküm verilmez; eksik gün `EKSİK`tir, sıfır sayılmaz.

Bu paket bilerek hafiftir: yalnız standart kütüphane ve (ham config için) PyYAML kullanır. `pandas` yalnız işçinin
HistoryStore'undan spot mark okunurken ve yalnız gerektiğinde, fonksiyon içinde import edilir.
"""
from __future__ import annotations

#: Motor şema/sürüm etiketleri (çıktılara yazılır).
ENGINE_VERSION = "research_engine_v1_p1a"

__all__ = ["ENGINE_VERSION"]

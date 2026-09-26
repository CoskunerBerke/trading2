# Mum varyasyonu laboratuvar kayıtları

Bu klasörde her varyasyon için tek bir `<ID>.json` durur (ör. `CV001_X.json`).

* Dosya, GitHub `signal-lab` çalıştırmasının `signal-lab-report` çıktısındaki `variation_records/<ID>.json`
  dosyasından **bayt bayt** kopyalanır. Elle düzenlenmez.
* Çalışma kapısı (`tradingbot/candle_variations.gate`) yalnız buradaki kaydı okur: DSL sürümü, pencere,
  `definition_sha`, CI çalıştırma bilgisi ve hüküm denetlenir.
* Klasör başta boştur. Örnek kayıt (`CV000_EXAMPLE_BULL3`) hiçbir zaman işlem açmaz.

Ayrıntı: `docs/CANDLE_VARIATIONS_4H.md`.

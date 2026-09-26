# Mum varyasyonu laboratuvar kayıtları

Bu klasörde her varyasyon için tek bir `<ID>.json` durur (ör. `CV001_X.json`).

* Dosya, GitHub `signal-lab` çalıştırmasının `signal-lab-report` çıktısındaki `variation_records/<ID>.json`
  dosyasından **bayt bayt** kopyalanır. Elle düzenlenmez.
* Çıktı indirilemiyorsa kayıt iş günlüğünden alınır: "Print lab records" adımı her kaydı base64 olarak basar.
  Günlük bir dosyaya kaydedilir ve `python scripts/extract_lab_records.py <günlük>` çalıştırılır. Betik sha256'yı ve
  bayt sayısını denetler, dosyayı buraya bayt bayt yazar. Önce `--dry-run` ile denenebilir. Günlük ham indirme
  (web), API `/actions/jobs/<id>/logs` ya da `gh run view <id> --log` çıktısı olabilir.
* Şema `candle_lab/2`. Kapı yalnız güncel şemayı kabul eder; eski şemalı kayıt `LAB_RECORD_INVALID` olur ve
  laboratuvar yeniden koşulur.
* Hüküm alanları: `verdict` standart hükümdür, `verdict_strict` sıkı hükümdür. İkisi de üstte (birincil dilim, 4h) ve
  `by_tf.<dilim>` içinde durur. `strict_rule_tr` önceden kayıtlı sıkı kuralın metnidir.
* `by_tf.<dilim>.vs_placebo.ci95`: `{"IS": [alt, üst] | null, "OOS": [alt, üst] | null}`. Eşine göre farkın gün kümeli
  %95 aralığıdır. Yalnız standart hükmü GÜÇLÜ ADAY olan dilimde hesaplanır. Sıkı kural OOS alt ucunun 0'ın üstünde
  olmasını ister; IS aralığı bilgi amaçlıdır.
* Çalışma kapısı (`tradingbot/candle_variations.gate`) yalnız buradaki kaydı okur: DSL sürümü, pencere,
  `definition_sha`, CI çalıştırma bilgisi, şema ve hüküm denetlenir.
* Klasör başta boştur. Örnek kayıt (`CV000_EXAMPLE_BULL3`) hiçbir zaman işlem açmaz.

Ayrıntı: `docs/CANDLE_VARIATIONS_4H.md`.

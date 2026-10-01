# PROTOKOL V15 — Box Theory (önceki gün aralığını 5m'de soluklama)

**Bu belge süpürme sonuçları GÖRÜLMEDEN yazıldı.** Karar kuralı sonradan değiştirilmez; değiştirilirse
değişiklik tarihiyle birlikte buraya yazılır ve eski kural ayakta kalır.

## Kural nereden geldi

Kaynak: Instagram "The box theory" videosu (2026-09-17, 93 sn). Altyazılardan birebir çıkarıldı:

1. Günlük grafikte **önceki günün en yükseği ile en düşüğü** arasına kutu çiz.
2. Kutuyu **5 dakikalık** grafiğe taşı.
3. Fiyat kutunun **üstünde/yakınında → SAT**, **altında/yakınında → AL**, **ortada → hiçbir şey yapma**.
4. Short tetiği: kırmızı mum önceki mumun altına insin; stop = **önceki mumun tepesi**.
   Long tetiği: yeşil mum görünsün; stop = **günün en düşüğünün hemen altı**.

## Videoda OLMAYAN, bu yüzden ölçülen şeyler

| Ne | Neden parametre | Kolları |
|---|---|---|
| **Çıkış** | Video 93 saniyede kâr alma noktasını bir kez bile söylemiyor | `box_opposite`, `box_mid`, `r1`, `r1.5`, `r2`, `r3`, `none` |
| **"Yakın" ne kadar** | Sayı verilmiyor | `near_frac` = 0.05 / 0.10 / 0.20 |
| **Long stop asimetrisi** | Short'ta bir mum, long'da günün dibi — biri yanlış ifade olabilir | `long_stop` = `day_low` / `prev_candle` |
| **Tetik sıkılığı** | "Bir kırmızı mum" mu, "önceki mumu kıran kırmızı mum" mu | `trigger` = `break_prev` / `color_only` |
| **Kutu dışı fiyat** | "at or near the top" kutuyu AŞMIŞ fiyatı kapsıyor mu | `allow_outside` |
| **Asgari stop** | **Bizim eklediğimiz eşik** — aşağıdaki maliyet bulgusu yüzünden | `min_stop_pct` = 0 / 0.3 / 0.6 / 1.0 |
| **Kaldıraç** | Video söylemiyor; tek-coin notional tavanını açmak için gerekli | 3 (aritmetiği `config.yaml`da) |

## Ön ölçüm (BTC, 2026-06→08, tek pencere — hüküm DEĞİL)

| çıkış | n | BRÜT ortR | BRÜT %95 aralık | NET ortR | maliyet/R |
|---|---|---|---|---|---|
| box_opposite | 446 | −0,221 | [−0,404, −0,038] | −1,038 | 0,820 |
| box_mid | 476 | −0,137 | [−0,318, +0,043] | −0,963 | 0,828 |
| r2 | 614 | −0,021 | [−0,127, +0,085] | −0,916 | 0,897 |
| none | 388 | −0,250 | [−0,447, −0,053] | −1,048 | 0,800 |

**İki ayrı sonuç, karıştırılmamalı:**

* **Sinyalin kendisi** (maliyetsiz) sıfır civarı — r2'de güven aralığı sıfırı içeriyor, box_opposite'te negatif.
* **Maliyet öldürücü.** Medyan stop fiyatın **%0,25'i**; gidiş-dönüş komisyon+kayma **%0,16**. Yani
  riskin **~%82'si** daha işlem açılırken gidiyor. Bu, çıkış seçimiyle kapatılabilecek bir açık değil.

Bu yüzden Aşama A'nın belirleyici ekseni `min_stop_pct`tir: stop maliyetin yanında anlamlı olacak kadar
genişletildiğinde sinyal ayakta kalıyor mu?

## Ölçüm kurulumu

* **Evren:** giriş evrenindeki on coin (BTC, ETH, SOL, BNB, XRP, LINK, DOGE, AVAX, LTC, AAVE), USDⓈ-M perpetual.
* **Pencereler:** P1 2020-11-01→2022-08-31 · P2 2022-09-01→2024-08-31 · P3 2024-09-01→2026-08-31 (V9'dan beri aynı).
* **Veri:** 5m + 1d, `wt-ten/data/history`, Binance arşivi. Koşu `history_root`u rapora yazar.
  **Doğrulandı** (`history-validate`, 2026-09-18): on sembolün 70 serisinde `invalid: 0`; BTC 5m
  618.048 satır, **0 boşluk / 0 kopya / 0 bozuk parça, kalite 1,0**, 2020-11-01 → 2026-09-17.
  İndirme `bad_chunks: 0`, 700 arşiv ayı. "İndirme bitti" veri kanıtı değildir; bu satır kanıttır.
* **Maliyet:** taker %0,05 + kayma 3 bps, **iki bacakta**. Geçilen her funding settlement'ı arşivden sorulur;
  bilinmiyorsa sıfır sayılmaz, kol `funding_unknown` ile işaretlenir.
* **Belirsizlik:** aynı barda hem stop hem hedef değerse **STOP** kazanır (`ambiguity_policy: worst_case`).
* **Doluluk:** sembolde pozisyon açıkken yeni giriş yok — her kolda AYRI kurulur (havuzlanmış kısayol yok).
* **Karar mantığı** üretim modülünden gelir (`tradingbot.box_theory`); araştırma kendi kopyasını tutmaz.

## Karar kuralı (önceden yazıldı)

Bir kol **"ölçülmüş beklentisi pozitif"** sayılır ancak ve ancak:

1. **Üç pencerede de** NET ortalama R'nin %95 güven aralığının **alt sınırı sıfırın üstünde**, ve
2. **her pencerede en az 30 işlem** varsa.

Tek pencerede pozitif olmak yeterli DEĞİLDİR (bkz. *bölme deney değildir*). Hiçbir kol geçmezse sonuç
"bu kural bu evrende ve bu maliyet yapısında ölçülmüş bir avantaj göstermedi"dir — "biraz daha ayar" değil.

## Üretimde kaçınılmaz iki ayrışma (ölçülür, gizlenmez)

1. **Tur temposu.** Bot `watch --interval 15` ile çalışır: 5m kuralı barların **1/3'ünü** görür.
   Stride=3 kolu bunu doğrudan ölçer; defterin `data_policy.intraday` alanı bunu ilan eder.
2. **Tek-coin notional tavanı.** Dar stop → `notional = risk%/stop%` büyür → `MAX_POSITION_PCT`.
   Kaldıraç 3 tavanı açar ve işlem başına riski değiştirmez; aritmetik `config.yaml`da, testi
   `test_a_tight_intraday_stop_needs_leverage_to_fit_the_position_cap`.

## Kâğıt defter

`strategy_paper.extra` içinde `b1_box_fade` / `strategy_paper_box` olarak tanımlı ve **KAPALI**. Süpürme
bir çıkış seçmeden açılmaz: ölçülmemiş bir çıkışla başlatılan ileri test yorumlanamaz.

## Hızlı koşucunun üretimden AYRILDIĞI yerler (ölçüldü, saklanmadı)

Tam `HistoricalReplay` 5m'de karar başına **81 ms** sürüyor (ölçüm: `probe_box_cost.py`, BTC+ETH, 1 hafta,
4034 karar / 327 sn). Bu hızla tek kol × tek 2-yıllık pencere ≈ **47 saat**, tam süpürme ≈ **1706 saat**.
Bu yüzden süpürme `box_sweep.py` ile koşuyor. Karar mantığı AYNI üretim modülünden geliyor; ayrıldığı
yerler şunlar ve hiçbiri gizlenmiyor:

| Konu | Üretim | Hızlı koşucu | Etki |
|---|---|---|---|
| Boyutlandırma | risk tabanlı + `MAX_POSITION_PCT` | R çokluğu ölçülür, tavan AYRICA sayılır (`cap_ok`) | R'yi değiştirmez; hangi işlemin gerçekten açılabileceğini `cap_ok` söyler |
| Eşzamanlı pozisyon | defter tavanı (PAPER'da uygulanmıyor) | sembol başına 1, semboller arası sınırsız | üretimde 3 tavanı varsa işlem sayısı düşer |
| Tick/adım/asgari tutar | borsa filtreleri uygulanır | uygulanmaz | fiyat yuvarlama R'de ihmal edilebilir; asgari tutar `cap_ok` ile birlikte okunmalı |
| Gün sonu düzleşme | turun kararı | ertesi günün İLK 5m barında kapatılır | ~5 dakikalık fark |
| Stop/hedef dolumu | tick + bar uçları | yalnız bar uçları | 5m'de fark küçük; kayma maliyete dahil |

**Doğrudan ölçülmüş üretim kanıtı:** aynı kuralı tam replay ile koşturduğumuzda (BTC, 2026-08-01→04)
**865 karar → 143 sinyal → 0 pozisyon**, 143 retin tamamı `MAX_POSITION_PCT`. Yani bu kural mevcut risk
çerçevesinde boyutlandırılamıyor; `cap_ok` sayacı bunu her kol için niceliyor.

# 4 saatlik trend takibi — gözlem defteri (D4, 2026-09-25)

Kullanıcı onayıyla açılan dördüncü kâğıt defteri. Mod PAPER; gerçek para yok. Kaldıraç 1.

## Durum: KANITLANMADI

Kural, sinyal laboratuvarının algoritma çalıştırmasında sıkı testi **geçemedi**. Kapsam:
[GitHub run 36150821072](https://github.com/CoskunerBerke/trading2/actions/runs/36150821072), 30 coin, 4h için 4 yıl.
Test edilen algoritmalar arasında en yakın aday oldu:

| | Keşif dönemi | Doğrulama dönemi |
|---|---|---|
| İşlem sayısı | 5.728 | 2.882 |
| İşlem başına ortalama (maliyet sonrası) | +0,42R | +0,05R |
| %95 aralık (gün kümeli) | +0,18 … +0,70 | −0,21 … +0,34 (sıfırı kapsıyor) |
| Aynı çıkışlı rastgele girişe göre fark | +0,32 | +0,23 |

Trend ve momentum long 2021'den 2025 başına kadar işledi, son dönemde zayıfladı. Short tarafında avantaj görülmedi,
bu yüzden kullanılmıyor.

Defter bu kuralın canlıda nasıl davrandığını ölçmek için açıldı. Kâr beklentisi değildir.
Sonuçlar `scripts/bot_scorecard.py` ile ayrıca izlenir; defterin adı "Trend 4h (gözlem, kanıtlanmadı)".

## Kural (`tradingbot/donchian_trend.py`)

Tanım laboratuvardaki `TREND_DONCHIAN_20_10` ile birebir aynıdır ve config'ten değiştirilemez. Göstergeler
laboratuvarın kendi fonksiyonlarıyla hesaplanır (`signal_lab.indicators`, `signal_lab.aux_series`).

| Parça | Değer | Laboratuvardaki karşılığı |
|---|---|---|
| Giriş | Kapanmış 4h barın kapanışı önceki 20 barın tepesini **ilk kez** aşar → LONG | aynı koşul |
| Giriş fiyatı | Sinyal kapanışından sonraki ilk doğrulanmış perp fiyatı; en geç 60 dk | sonraki barın açılışı |
| İlk stop | Sinyal kapanışı − 2 × ATR14(4h) | aynı |
| Risk aralığı | Girişten stop'a mesafe 0,1–10 ATR dışında ise girilmez | aynı |
| Çıkış | 4h kapanış önceki 10 barın dibinin altına inince, sonraki fiyattan | sonraki barın açılışı |
| Zaman sınırı | 300 bar (50 gün) | aynı |
| Hedef | Yok | yok |
| Aynı sinyal | Tek işlem (stop aynı barda gelse bile ikinci giriş yok) | her sinyal tek işlem |

**Eşleşme testi:** `tests/test_donchian_trend_book.py::test_parity_entries_stops_and_channel_exits_match_the_lab`.
Sentetik iki seride defterin girişleri, stop'ları ve kanal çıkış barları laboratuvarınkilerle birebir aynıdır.

## Laboratuvardan farklı olanlar

Bunlar ölçümü etkiler; sonuç yorumlanırken akılda tutulmalı.

- **Evren 23 coin.** Laboratuvarın 30 coininden turun zaten 4h verisi çektiği 23'ü kullanılır. Kalan 7'si
  (ALGO, ATOM, ETC, HBAR, ICP, SAND, VET) giriş evreninde değildir.
- **Portföy sınırları.**
  - Laboratuvar her sinyali bağımsız saydı.
  - Defterde diğer defterlerle aynı sınırlar geçerlidir: aynı anda en çok 3 pozisyon ve toplam risk tavanı.
  - Bu yüzden defter sinyallerin yalnız bir alt kümesini alır.
- **Boyut.**
  - İşlem riski profilin işlem başına riskidir.
  - Tek pozisyon tavanını (%30) aşan işlem reddedilmez, tavana sığacak kadar küçültülür. Risk bütçenin altında kalır,
    asla üstüne çıkmaz.
  - Kayıtta `size_scaled_to_cap` alanıyla görünür.
- **Stop takibi.**
  - Laboratuvar stop'u 4h bar uçlarıyla denetler.
  - Defter iki yoldan denetler: canlı fiyat (60 sn izleyici) ve kapanmış 1h bar uçları. Aynı ya da daha sık bir
    denetimdir.
- **Kesinti.**
  - Sinyal kapanışından 60 dakika içinde girilemezse sinyal kaçmış sayılır; geç giriş yapılmaz.
  - Kaçan bir çıkış sinyali ise kaybolmaz: girişten sonra kapanan her bar denetlenir, çıkış ilk fırsatta yapılır.
  - Gecikme `late_bars` alanında yazılır.
  - İki saatten kısa kesintilerde de, stop'un kesinti sırasında geçmiş bir barda tetiklenmiş olması hâlâ mümkündür.
    Bu durumu defterin geçmiş bar denetimi yakalar; kayıtta ayrıca görünür.
- **Yapı katmanı yok.** Ortak yapı kataloğu (structures_v1) bu deftere uygulanmaz, çünkü laboratuvar kuralı onsuz test
  etti.

## Değişmeyenler

- Diğer defterler: ana bot, T2, M2, Box ve Formasyon aynen çalışır.
- Strateji, sermaye, kaldıraç, risk eşikleri, T2/M2 zaman ufukları.
- Uygulayıcıdaki yeni iki kontrol yalnız onları isteyen eylemde çalışır:
  - test edilmiş risk aralığı (`risk_atr_bounds`);
  - aynı sinyalle tek giriş (`one_entry_per_signal`).
- 4h için eklenen bir turluk (15 dk) veri gecikme toleransı yalnız 4h okuyan deftere etki eder.

## Ayar ve kapatma

`config.yaml` → `strategy_paper.extra` içinde `d4_donchian_20_10`. Durum klasörü `state/strategy_paper_trend4h`.
Kapatmak için tek satır yeter: `enabled: false`. Kapatılınca açık pozisyon kalmışsa o defterin kaydında durur.
Kapatmadan önce pozisyonların kapanmasını beklemek daha temizdir.

Bu değişiklik VPS'e ancak birleştirilip dağıtıldığında gider. O zamana kadar canlı botlarda hiçbir şey değişmez.

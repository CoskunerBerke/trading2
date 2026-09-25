# Formasyon botu — protokol v3: 4 saatlik momentum (2026-09-25)

Kullanıcı onayıyla Formasyon botunun 15 dakikalık formasyon girişleri kapatıldı. Bot artık yalnız sinyal
laboratuvarında sıkı testi geçen tek sinyalle işlem açıyor. Mod PAPER; gerçek para yok.

## Neden

Sinyal laboratuvarı (`tradingbot/signal_lab.py`) üç kez çalıştırıldı. Son çalıştırma
[GitHub run 36123072720](https://github.com/CoskunerBerke/trading2/actions/runs/36123072720) kapsamı: 30 coin, 4h için
4 yıl (2022 düşüşü dahil), 1h için 2 yıl, 1,1 milyon simüle işlem. Sonuçlar:

- Klasik formasyonlar maliyet sonrası güvenilir avantaj göstermedi: çekiç, kayan yıldız, sabah/akşam yıldızı, yutan,
  üçgen, bayrak, OBO, çift tepe/dip.
- Kısa dilimde maliyet belirleyici. İşlem başı maliyet 15m'de 0,43R, 5m'de 0,83R. Maliyet öncesi sinyal sonucu ≈ 0.
- 1.412 kombinasyondan sıkı testi geçen tek sonuç: **4h, üç beyaz asker + RSI14 > 70 → LONG**.
  - Keşif dönemi: +0,15R (n=1412, %95 aralık +0,02…+0,28).
  - Doğrulama dönemi: +0,26R (n=736, %95 aralık +0,03…+0,48).
  - Aynı bağlamdaki rastgele girişe göre fark: +0,19 / +0,07. 30 coinin hepsinde.

Bu bir geçmiş test bulgusudur. 1.412 denemeden çıkan tek sonuç şans olabilir; kâr garantisi değildir. PAPER'da ayrıca
ölçülür (`scripts/bot_scorecard.py`).

## Kural (`tradingbot/pattern_trader/strategy_v3.py`)

| Parça | Değer | Laboratuvardaki karşılığı |
|---|---|---|
| Sinyal | Ortak katalogda 4h `THREE_WHITE_SOLDIERS` LONG kaydının en son kapanan barda teyidi | aynı analiz (`structures.analyze`) |
| Filtre | Teyit kapanışında RSI14 > 70 | aynı fonksiyon: `signal_lab.indicators` |
| Giriş | Teyit kapanışından sonraki ilk doğrulanmış perp fiyatı; en geç 60 dk içinde | sonraki barın açılışı |
| Kovalama | Tetikten 1 ATR'den uzak fiyattan girilmez | aynı |
| Risk aralığı | Giriş-stop mesafesi 0,1–5 ATR dışında ise girilmez | aynı |
| Stop | Kayıt stop'u: formasyon dibi − 0,25×ATR | aynı |
| Hedef | Gerçek giriş fiyatından 2R | aynı |
| Zaman stopu | 24 × 4h = 96 saat (defterde 15m biriminde 384) | 24 bar |
| Evren | Laboratuvarda test edilen 30 coin (`V3_SYMBOLS`) | aynı liste |
| Yön | Yalnız LONG (short tarafı testi geçemedi) | — |

**Eşleşme testi:** `tests/test_pattern_trader_momentum_v3.py::test_parity...`. Aynı pencereyle botun her kapanışta
kurduğu planlar, laboratuvarın seçtiği sinyallerle birebir aynı.

## Değişmeyenler

- Veri kimliği ve güncellik kapıları, likidite (spread/derinlik), resmî emir filtreleri, risk motoru, maliyet ve
  kayma, funding uzlaştırması, koruyucu izleyici.
- Kaldıraç 1x.
- Risk bütçesi: işlem başına %1. **Farkı:** geniş 4h stop, tek pozisyon tavanını (%30) aştığında işlem reddedilmez;
  tavana sığacak kadar küçültülerek açılır. Risk bütçenin altında kalır, üstüne asla çıkmaz. Bu davranış yalnız v3
  planlarında geçerlidir; diğer defterlerde değişiklik yok. Kayıtta `size_scaled_to_cap` alanıyla görünür.
- Açık pozisyonlar ve eski protokolden kalan pozisyonlar kendi stop, hedef ve zaman stopuyla yönetilmeye devam eder.
  Eski protokolün bekleyen planları `PROTOCOL_MOMENTUM_4H_V3` gerekçesiyle iptal edilir.

## Ayar ve geri alma

`config.yaml`:

```yaml
pattern_trader:
  protocol: momentum_4h_v3   # geri almak için: classic
  symbols: []                # boş → laboratuvarın 30 coini
```

`classic` eski davranışa bit bit döner: yapı moduna göre v1/v2, 15m.

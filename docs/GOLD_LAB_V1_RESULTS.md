# Altın laboratuvarı — gold_v1 sonuçları (2026-10-04)

Ön kayıt: `docs/GOLD_LAB_V1.md` (`d9a3a9e`, fiyat verisinden önce). Kod `4e101a0`, mühür `GOLD_REGISTRY_SHA =
ee32a9db510f41cd`. Koşu 2026-10-04 22:21–22:32 UTC, bulutta, yalnız `data.binance.vision` arşivinden.

Geçmiş testtir; PAPER ya da canlı sonuç değildir. Kâr garantisi değildir. Kurallar sonuçtan sonra değiştirilmedi.

## Sonuç

**Hiçbir taktik hedefi karşılamadı.** 32 birincil hücrenin hiçbiri GÜÇLÜ ADAY çıkmadı (standart ve sıkı hüküm aynı):

- 21 hücre KAYBETTİRİR, 11 hücre KANIT YOK.
- Her hücrede keşif döneminin ortalama R'si eksi.
- Aday oranı gerçek hücrelerde %0, plasebolarda %0.

Aylık ölçü: 23 tam doğrulama ayı (2024-10 → 2026-08), işlem başına risk %0,5.

| Ne | En iyi sonuç | Not |
|---|---|---|
| Tek yön | 1h `EMA_X_SWING` LONG: +%0,21/ay, aralık [−0,38, +0,79] | Hedef %1 |
| İki yön birlikte | 1h `EMA_X_SWING_HTF`: +%0,24/ay, aralık [−0,43, +0,99] | Hedef %1 |

## Ne gördük

**15 dakikalık 20/50 EMA sistemi (Instagram):**
- Bütün 15m hücreler KAYBETTİRİR: doğrulamada ortalama −0,54 ile −1,55 R arası, ayda −%2,6 ile −%21 arası.
- Kazanma oranı %20–34. Gönderinin iddia ettiği "%45–50" görülmedi.
- Asıl neden maliyet. 15m'de stop dar olduğu için gidiş-dönüş maliyet (%0,16) R'nin 0,59–1,36'sını yiyor. Maliyetten önce
  sonuç sıfır civarı (örnek: 15m `EMA_X_SWING` LONG doğrulamada net −0,646 R, maliyet 0,682 R, yani brüt ≈ +0,04 R).
- Gönderinin "1:2,5'te %29 kazanma yeter" hesabı maliyeti yok saydığı için bu piyasada geçerli değil.
- 15m hücrelerde "plaseboya göre" sütunu artı görünüyor. Bunun nedeni aynı stoplu rastgele girişin daha da kötü olması
  (plasebonun doğrulama ortalaması örneğin −2,12 R); bir avantaj işareti değildir.
- 1h'te aynı sistem sıfır civarında (−0,19 ile +0,06 R) ve kanıt yok. 1,5 ATR stoplu hâli 1h'te de KAYBETTİRİR.

**Order block / FVG / likidite süpürmesi / CHoCH (birinci görsel):**
- 1h'te `SMC_BOS_OB`, `SMC_SWEEP` ve `SMC_FVG` belirgin zararda: doğrulamada −0,80 ile −1,11 R, ayda −%6 ile −%14.
- 4h'te sıfır civarında ya da zararda. En iyisi 4h `SMC_CHOCH` LONG (+0,066 R, n = 30) ve 4h `SMC_FVG` LONG (+0,005 R);
  ikisinin de aralığı sıfırı içeriyor.

**Binance vadeli denetimi (bilgi):**
- XAUUSDT 2025-12 → 2026-09, PAXGUSDT 2025-03 → 2026-09; 64 satır, 9.587 işlem.
- 7 satırın ortalaması artı; hiçbirinin aralığı sıfırın üstünde değil. Ana seride GÜÇLÜ ADAY olmadığı için "mekânda
  tutmadı" notu düşülecek hücre yok.

**Mevcut laboratuvar setleri (keşif):**
- Katalog + ek sinyaller + algoritmalar: 1.133 grup. Tek standart GÜÇLÜ ADAY 4h `ASCENDING_TRIANGLE` LONG, trend yönünde
  bağlam (doğrulama +0,59 R); sıkı hükümde ZAYIF İZ.
- Bağlam "HEPSİ"de ZAYIF İZ veren üç grup: 1d `TREND_DONCHIAN_20_10` LONG (+0,50 R) ve 1d `TSMOM_28` LONG (+0,43 R) bunların
  arasında.
- Bunların hepsi altının 2024–2026 boyunca güçlü yükselişiyle (yıllık medyan kapanış 2.383 → 4.513) uyumlu long sinyaller.
  Tek sembolde piyasa yönünden ayırmak zordur.
- C4 mum varyasyonları altında hiç aday vermedi (1h ortalama −0,44 R, 4h −0,22 R).

**Dukascopy (20 yıllık geçmiş):** yapılamadı. Kaynak bulut IP'sini hız sınırına takıyor; 2 saatte 155 gün indi (hafta içi
kapsaması %2,4; eşik %95).

## Veri notları

- PAXGUSDT spot arşivi 2020-08-28'de başlıyor (pencerenin 27 gün sonrası). Keşif/doğrulama kesimi buna göre 2024-09-17/18
  oldu; belgedeki "≈ 2024-07" kaba tahmindi.
- 15m: 213.459 bar, eksik %0,04, en uzun boşluk 4,75 saat. 24 saatten uzun boşluk yok. 2025-01-01'den itibaren mikrosaniye
  zaman damgaları doğru çözüldü. Eylül 2026 günlük dosyalardan geldi.
- 2021Q4–2025Q1 arasında PAXG spot fiyatları 1 USDT'lik adımlarla işlem görmüş. 2024-04-13 20:30 UTC'de gerçek bir sıçrama
  var (keşif döneminde). 2020'de 1.235 hacimsiz 15m bar var.

## Denetim

- **İki bağımsız denetçi:** biri ön kayda uygunluğa, diğeri geleceği görme, hesap ve güvenlik hatalarına baktı.
- **Birinci tur:** iki "major" bulgu çıktı (ayrı koşulan bölümlerin raporu ezmesi ve notların atlanması) ve birkaç küçük
  bulgu. Hepsi `4e101a0`'da düzeltildi; tek istisna, kozmetik olan spot/vadeli PAXGUSDT plasebo anahtarı.
- **İkinci tur:** iki denetçi de geçti. Kalanlar:
  - Bir küçük bulgu: kayıtlı bir vadeli sembolün arşivi boş olursa venue bölümünden sessizce düşer. Bu koşuda iki sembolün de
    verisi vardı.
  - Birkaç kozmetik not.
- Koşu sırasında kod ve belge değiştirilmedi.

## Sonra ne olur

- Bu sonuca göre altın için bir defter **önerilmiyor**. Sonuçtan sonra kural ayarlanmaz; ayarlama yeni sürüm (gold_v2), yeni
  ön kayıt ve yeni deneme sayısı demektir.
- Ayrı bir soru olarak, yalnız sahibin isteğiyle sorulabilecek olan: günlük trend takibinin (TSMOM_28 / Donchian 20-10 long)
  altında ayrı bir ön kayıtla sınanması. Kripto'da kanıtlı tek defter M2 (TSMOM28) olduğu için bu soru tutarlıdır. Ancak
  tek sembol ve güçlü yükseliş dönemi yüzünden bugünkü iz yetersizdir.
- Bot bugün altın işlemiyor; bu çalışma hiçbir defteri, stratejiyi ya da ayarı değiştirmedi.

## Deneme sayısı

gold_v1: 16 varyant (32 birincil hücre), aday 0. Mevcut setlerin altına uygulanması keşif amaçlıydı.

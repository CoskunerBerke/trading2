# M2X — agresif M2 kâğıt defteri: simülasyon sonuçları v1 (2026-10-05)

Ön kayıt: `docs/M2_AGGRESSIVE_V1.md` (`b580af1`, M2X için hiçbir sonuç hesaplanmadan önce). Politika ve simülasyon kodu
`cb44d6a` (mühür `M2X_POLICY_SHA = dfc2f68d2cdf4035`), raporlama eki `5995da4`, koşu sırasında bulunan bir simülasyon
hatasının düzeltmesi `0548073` (aşağıda "Düzeltme"). Ön kaydın §5 adım 5'te andığı sonuç belgesi budur. Koşu 2026-10-05
16:38–18:19 UTC, bulutta, yalnız `data.binance.vision` arşivinden.

Geçmiş simülasyonudur; PAPER ya da canlı sonuç değildir. Getiri, düşüş sınırı ya da durdurma için söz vermez. Kurallar
ve sayılar sonuçtan sonra değiştirilmedi. Sayılar `report.json` ve `meta.json`dan bir betikle tabloya döküldü; elle
yazılmadı. Fiyat verisi ve çıktılar depoya girmez.

## İlk satır (§3.7)

- **Kol (b)'de durdurma tetiklenmedi.** Tarihsel en büyük düşüş %32,86 (günlük görüntü), %34,29 (1h mark); −%50'ye
  ulaşılmadı. §3.7'nin "ilk satıra yazılır" koşulu oluşmadı.
- **Parite:** (b)–(d) ayrışmaları %0 (yetim kapanış 0, "M2 likide / M2X değil" 0). Likidasyon ayrışması 0. Eşleşen 1.124
  işlemin hepsinde M2X R'si M2 R'sine eşit. Bu yüzden "parite zayıf" başlığı GEREKMEZ.
- **Canlı paritesi kanıtsız:** E2 yapılamadı. VPS'teki M2 defterinin salt okunur kopyası bu çalışma alanında yok (§3.3).
- **Filtre: varsayım.** VPS'teki `data/symbol_filters.json` kopyası yok. Her sembolde en küçük emir 5 USDT, BTC 100,
  ETH 20; miktar adımı yok; varsayılan bracket'lar (§3.1). BTC/ETH ve bracket denetimi sonuçları kesin değildir.

## Sahibe düz dille

- 2023-01 → 2026-09 (45 ay). M2'nin kendi kodu bugünkü ayarıyla (kol a: öğrenme modu, işlem başı %0,5) 200 →
  317,15 USDT, **+%58,57**. Aynı işlemleri kopyalayan **M2X v1 (kol b): 200 → 342,71, +%71,36**. Ön kayıtlı diğer
  boyut, **orantılı kopya (kol p): 200 → 385,35, +%92,67**.
- **İşlem başı 4 kat risk, getiriyi 4 katına çıkarmadı.** (b)'nin açık riski ortalamada M2'ninkinin 2,30 katıydı.
  Tavanlar dolunca M2 işlemlerinin %31,5'i atlandı, %30,0'ı küçültüldü. Atlanan işlemlerin M2'deki ortalaması
  (+0,089 R) tam boy kopyalananlardan (+0,052 R) yüksekti. Zamana göre seçim bu örnekte daha iyi işlemleri seçmedi
  (aralıklar geniş ve örtüşüyor; ön kayıt bu riski yazmıştı). Bu örnekte (p), (b)'den daha çok kazandı ve daha az düştü.
- **Getiri birkaç güçlü aydan geliyor.** (b)'de aylık ortalama +%1,58, medyan −%0,54. Ayların %20'si ≥ +%1, %44,4'ü
  ≥ 0. En iyi ay +%28,07 (2023-10), en kötü ay −%8,30 (2024-04). 2026-01 → 04 arasında M2 hiç pozisyon tutmadı: o aylar
  %0'dır.
- **Günlük:** (b)'de günlerin %14,3'ü ≥ +%1, %12,5'i ≤ −%1, medyan gün %0. Günlerin %29'unda açık pozisyon yoktu.
- **Düşüş:** (b)'nin en büyük düşüşü %32,86: zirve 2024-03-09 (397,01 USDT), dip 2026-08-31 (266,55). Dönem sonunda
  defter hâlâ o zirvenin altında (936 gün). (p)'de en büyük düşüş %23,82, M2'nin kendisinde %23,30.
- **Merdiven:** (b) 928 gün K1'de, 412 gün K2'de, 29 gün K3'te kaldı. K4 (yumuşak durdurma) ve DUR hiç olmadı. 11 aşağı
  inme ve 11 yukarı çıkış oldu; çıkışların 9'u B anahtarıyla (M2'nin son 20 günü artıda). B anahtarı kademe zirvesini
  yeniden demirler, gerçek zirve (P) ise yerinde kalır. Bu yüzden B ile yapılan 9 çıkışın 7'sinde gerçek zirveden düşüş
  %20'nin üstündeydi (en çok %28,80). K1'de geçen 928 günün 490'ında gerçek zirveden düşüş %20'nin, 351'inde %25'in
  üstündeydi. Kötü dönemdeki temkin çoğunlukla K2 (%1 risk) oldu ve kısa sürdü. Yalnız A anahtarlı kol (j) ise 761 gün
  K3'te kaldı.
- **Yeniden örnekleme (bootstrap, 365 gün, 2.000 yol):** (b)'de durdurma sıklığı %0,0 (0 / 2.000 yol), en büyük düşüşün
  ≥ %50 olma sıklığı %0,0, ≥ %35 %5,6; 12 ay getirisinin medyanı +%25,9, %5 dilimi −%25,9, %95 dilimi +%144,0; yolların
  %27,1'i eksi bitti. (p)'de durdurma %0,0, ≥ %50 düşüş %0,0, ≥ %35 %0,1; medyan +%18,3 [−%19,5, +%89,3]; eksi biten yol
  %26,1. Bunlar yeniden örnekleme sıklıklarıdır, olasılık değildir. Bootstrap funding içermez. Tarihte funding (b)'nin
  toplamını +%86,96'dan +%71,36'ya indirdi; bu yüzden bootstrap sayıları tarihten iyimserdir.
- Bu bir getiri sözü değildir. Örnekte piyasa çapında tek bir çöküş var (2025-10-10). Evren bugünün hayatta kalan
  sembollerinden seçildi. Canlıda M2 25–29 pozisyon tutuyor, geçmişte medyanı 3'tü.

### İstenen ana sayılar

| Ölçü | M2X v1 (b) | Orantılı kopya (p) | M2 (a), bilgi |
|---|---|---|---|
| Toplam getiri (45 ay) | +%71,36 | +%92,67 | +%58,57 |
| Aylık ortalama / medyan | +%1,58 / −%0,54 | +%1,75 / −%0,51 | +%1,18 / −%0,46 |
| En kötü ay | −%8,30 (2024-04) | −%5,44 (2025-06) | −%8,15 (2024-12) |
| En büyük düşüş: günlük görüntü / 1h mark | %32,86 / %34,29 | %23,82 / %24,22 | %23,30 / %23,50 |
| Gün ≥ +%1 payı (≤ −%1 payı) | %14,3 (%12,5) | %11,0 (%9,1) | %9,0 (%6,8) |
| Durdurma (tarihsel) | yok | yok | — (kural yok) |
| Durdurma sıklığı, bootstrap 365 gün (blok 20) | %0,0 (0 / 2.000 yol) | %0,0 (0 / 2.000 yol) | — |
| En büyük düşüş ≥ %50 sıklığı, bootstrap 365 gün | %0,0 | %0,0 | — |
| En büyük düşüş ≥ %35 sıklığı, bootstrap 365 gün | %5,6 | %0,1 | — |
| Gün ≥ +%1 payı, bootstrap 365 gün | %16,8 | %11,0 | — |
| 12 ay getirisi medyan [%5, %95], bootstrap | +%25,9 [−%25,9, +%144,0] | +%18,3 [−%19,5, +%89,3] | — |

## Koşu

- **Kod:** simüle edilen M2 yolu `1c2c6e2` ile aynıdır (`tradingbot/` farkı yalnız `m2x_policy.py` ve `m2x_sim.py`). VPS
  `f8b05fb` çalıştırıyor; ön kayda göre M2 yolunda ikisi aynıdır. Kol (a) `5995da4` ile, M2X kolları ve bootstrap
  düzeltmeden sonra `0548073` ile koştu. Düzeltme kol (a)'nın yolunu değiştirmez.
- **Evren:** config `coins` = `entry_universe`, 40 sembol (BTC dahil). 12 sembol dönem içinde listelendi;
  listelendikten ve kuralın günlük ısınmasından sonra girer: FET (2023-01-17), ARB (2023-03-23), SUI (2023-05-03),
  1000PEPE (2023-05-05), WLD (2023-07-24), ONDO (2024-01-20), LSK (2024-01-25), ENA (2024-04-02), TAO (2024-04-11),
  REZ (2024-04-30), G (2024-08-15), SYN (2024-08-16).
- **Dönem:** kararlar 2023-01-01 → 2026-09-30 (UTC), 1.369 günlük tur. Günlük barlar 2021-11-01'den, 1h barlar
  2022-12-25'ten. Canlı sağlayıcının günlük pencere uzunluğu 400 bar.
- **Veri kalitesi:** dönem içinde hiçbir sembolde eksik 1h bar ya da 24 saatten uzun boşluk yok. Funding: kol (a)'da
  26.626 oran satırı kullanıldı; 32 sorguda oran bulunamadı (dönem bekletildi, uydurulmadı); 17 settlement'ta mark kline
  yoktu ve perp 1h açılışı kullanıldı (işaretli).
- **Aşırı fitiller (1h barda low/open − 1 ≤ −%25):** 16 gün. 2025-10-10'da 40 sembolün 35'inde; diğer 15 günde 1–5
  sembolde. **Örnekte 2025-10-10 ölçeğinde (piyasa çapında) tek olay var.** Fitilli günler: 2023-06-10, 2023-08-17,
  2023-11-09, 2023-12-11, 2024-01-03, 2024-03-05, 2024-03-06, 2024-04-12, 2025-10-10, 2026-07-23, 2026-08-12, 2026-08-22,
  2026-09-13, 2026-09-14, 2026-09-16, 2026-09-20.
- **Rejim sayısı:** BTC günlük kapanışı karar döneminde EMA200'ün 932 gün üstünde, 438 gün altında kaldı; 52 ayrı dönem
  (kesişmelerin çoğu kısa salınım).
- **Karşı-olgusal denetimi (§3.2):** 2025-09-01 → 2025-10-31, karşı-olgusal açık ve kapalı: 120 / 120 işlem, listeler
  bire bir aynı. Ana koşu karşı-olgusal kapalı yapıldı.
- **E3 (bilgi):** laboratuvarın `TSMOM_28` LONG olayı 1.988, M2 girişi 1.651, aynı gün 534 (laboratuvarın %26,9'u,
  M2'nin %32,3'ü). M2'nin üreteci laboratuvarınkinden belirgin biçimde farklıdır; laboratuvar sonucu M2 kanıtı yerine
  geçmez.
- **Süre:** kol (a) 1.339,5 sn; her M2X kolu ~21–31 sn; bootstrap 2.544 sn (8 anahtar).

## Kol (a): M2'nin kendisi (geçmiş, bugünkü config)

- 1.651 açılış, 1.635 kapanış, dönem sonunda 16 açık. Ortalama **+0,0467 R**, giriş gününe göre kümelenmiş %95 aralık
  **[−0,0024, +0,1048]** (606 giriş günü). Kazanma oranı %39,2, kâr çarpanı 1,272, toplam +76,33 R, R cinsinden en büyük
  düşüş −37,12 R.
- Yıl başına: 2023 n = 452, +0,090 R · 2024 n = 604, +0,092 R · 2025 n = 490, −0,060 R · 2026 n = 89, +0,104 R.
- Çıkış nedenleri: `M2_STRUCTURE_EXIT` 825, `M2_TSMOM28_CROSS_DOWN` 612, `ENTRY_STRUCTURE_FAILED` 150, stop 47,
  likidasyon 1.
- Girişlerin 341'i politika girişi, 1.310'u öğrenme modunun kapasite ekstrası (`TOTAL_OPEN_RISK`). 790 giriş en küçük
  emre çıkarıldı. Reddedilenler: yalnız-kayıt seçicilik ekstrası 7.493, en küçük emir çatışması 642, likidasyon
  tamponu 56, en küçük emir 5. BTC dönem boyunca hiç açılmadı (varsayılan en küçük emri 100 USDT; ret nedeni sembol
  başına ayrıştırılmadı).
- Açık pozisyon sayısı: ortalama 6,06, medyan 3, en çok 32. **Canlıdaki 25–29 pozisyon geçmişte tipik değildi.**
- Ücret 10,66 USDT, funding −20,48 USDT. Ortalama açık risk (mark'tan stopa) özkaynağın %4,49'u.
- Canlı karnenin "+0,12 R, [+0,05, +0,22]" satırı (Bağlam) ~3 haftalık bir pencereden gelir. Geçmişte aynı kodun
  ortalaması daha düşük ve aralığı sıfırı içeriyor. İyimser etiketi korunur.

## Kollar (a), (b), (p)

| Ölçü | (a) | (b) | (p) |
|---|---|---|---|
| Başlangıç → son özkaynak (USDT) | 200,00 → 317,15 | 200,00 → 342,71 | 200,00 → 385,35 |
| Toplam getiri | +%58,57 | +%71,36 | +%92,67 |
| Yıllık bileşik | +%13,09 | +%15,45 | +%19,12 |
| Aylık ortalama | +%1,18 | +%1,58 | +%1,75 |
| Aylık medyan | −%0,46 | −%0,54 | −%0,51 |
| En kötü ay | −%8,15 (2024-12) | −%8,30 (2024-04) | −%5,44 (2025-06) |
| En iyi ay | +%19,84 (2024-11) | +%28,07 (2023-10) | +%29,91 (2024-11) |
| Ay ≥ +%1 / ≥ 0 / ≤ −%10 payı | %24,4 / %40,0 / %0,0 | %20,0 / %44,4 / %0,0 | %26,7 / %42,2 / %0,0 |
| En büyük düşüş: günlük görüntü / 1h mark | %23,30 / %23,50 | %32,86 / %34,29 | %23,82 / %24,22 |
| Gün ≥ +%1 / gün ≤ −%1 payı | %9,0 / %6,8 | %14,3 / %12,5 | %11,0 / %9,1 |
| Medyan gün | %0,000 | %0,000 | %0,000 |
| En kötü gün | −%10,76 (2024-12-10) | −%11,41 (2025-10-11) | −%7,11 (2025-10-11) |
| Su altı: en uzun / toplam gün; ort. toparlanma | 663 / 1312; 39,2 gün | 936 / 1327; 25,4 gün | 663 / 1312; 33,5 gün |

Alt dönemler (yalnız bilgi; getiri / en büyük düşüş / aylık ortalama):

| Dönem | (a) | (b) | (p) |
|---|---|---|---|
| 2023 | +%23,45 / %12,45 / +%1,86 | +%29,31 / %27,85 / +%2,58 | +%27,62 / %15,30 / +%2,19 |
| 2024 | +%22,46 / %17,04 / +%2,04 | +%28,06 / %31,73 / +%2,66 | +%38,56 / %23,59 / +%3,31 |
| 2025 | −%6,94 / %13,01 / −%0,57 | −%14,30 / %18,66 / −%1,20 | −%9,48 / %16,70 / −%0,75 |
| 2026-01..09 | +%13,17 / %4,04 / +%1,49 | +%21,81 / %6,87 / +%2,53 | +%20,96 / %7,15 / +%2,39 |

### Bütün aylar (mark'a göre, UTC takvim ayı)

| Ay | (a) | (b) | (p) |
|---|---|---|---|
| 2023-01 | +%2,14 | +%5,44 | +%2,88 |
| 2023-02 | −%1,30 | −%3,96 | −%1,91 |
| 2023-03 | −%0,83 | −%2,33 | −%1,30 |
| 2023-04 | −%0,66 | +%0,39 | −%1,37 |
| 2023-05 | −%2,57 | −%6,35 | −%2,84 |
| 2023-06 | −%1,71 | −%6,53 | −%2,03 |
| 2023-07 | +%2,42 | +%0,33 | +%2,97 |
| 2023-08 | −%1,97 | −%4,37 | −%2,69 |
| 2023-09 | +%0,25 | +%0,04 | +%0,41 |
| 2023-10 | +%7,82 | +%28,07 | +%6,64 |
| 2023-11 | +%4,19 | +%0,94 | +%7,75 |
| 2023-12 | +%14,51 | +%19,33 | +%17,76 |
| 2024-01 | −%2,87 | −%2,74 | −%2,58 |
| 2024-02 | +%13,45 | +%24,85 | +%19,70 |
| 2024-03 | +%16,59 | +%17,90 | +%17,45 |
| 2024-04 | −%3,46 | −%8,30 | −%3,58 |
| 2024-05 | −%0,46 | −%2,38 | −%1,02 |
| 2024-06 | −%2,76 | −%4,21 | −%5,02 |
| 2024-07 | −%0,97 | −%2,58 | −%1,63 |
| 2024-08 | −%2,64 | −%7,09 | −%3,17 |
| 2024-09 | −%1,96 | −%1,12 | −%2,41 |
| 2024-10 | −%2,13 | −%1,59 | −%3,29 |
| 2024-11 | +%19,84 | +%24,35 | +%29,91 |
| 2024-12 | −%8,15 | −%5,13 | −%4,67 |
| 2025-01 | −%3,05 | −%5,66 | −%4,72 |
| 2025-02 | −%0,82 | −%2,49 | −%1,79 |
| 2025-03 | −%3,37 | −%6,97 | −%3,23 |
| 2025-04 | −%0,15 | −%0,27 | −%0,22 |
| 2025-05 | +%5,28 | +%7,09 | +%9,74 |
| 2025-06 | −%3,08 | −%5,42 | −%5,44 |
| 2025-07 | −%0,34 | +%4,06 | +%2,48 |
| 2025-08 | +%2,15 | +%0,61 | +%2,09 |
| 2025-09 | −%2,15 | −%3,92 | −%4,10 |
| 2025-10 | −%1,00 | −%0,96 | −%3,50 |
| 2025-11 | +%0,23 | +%0,19 | +%0,20 |
| 2025-12 | −%0,55 | −%0,67 | −%0,49 |
| 2026-01 | %0,00 | %0,00 | %0,00 |
| 2026-02 | %0,00 | %0,00 | %0,00 |
| 2026-03 | %0,00 | %0,00 | %0,00 |
| 2026-04 | %0,00 | %0,00 | %0,00 |
| 2026-05 | −%0,97 | −%2,67 | −%1,77 |
| 2026-06 | −%0,14 | −%0,45 | −%0,26 |
| 2026-07 | %0,00 | %0,00 | %0,00 |
| 2026-08 | −%0,33 | −%0,54 | −%0,51 |
| 2026-09 | +%14,82 | +%26,41 | +%24,09 |

### Çeyrekler

| Çeyrek | (a) | (b) | (p) |
|---|---|---|---|
| 2023-Q1 | −%0,02 | −%1,10 | −%0,40 |
| 2023-Q2 | −%4,87 | −%12,13 | −%6,12 |
| 2023-Q3 | +%0,66 | −%4,00 | +%0,60 |
| 2023-Q4 | +%28,64 | +%54,27 | +%35,31 |
| 2024-Q1 | +%28,48 | +%43,16 | +%36,96 |
| 2024-Q2 | −%6,56 | −%14,25 | −%9,35 |
| 2024-Q3 | −%5,47 | −%10,51 | −%7,04 |
| 2024-Q4 | +%7,73 | +%16,09 | +%19,78 |
| 2025-Q1 | −%7,08 | −%14,42 | −%9,45 |
| 2025-Q2 | +%1,88 | +%1,01 | +%3,54 |
| 2025-Q3 | −%0,38 | +%0,58 | +%0,33 |
| 2025-Q4 | −%1,32 | −%1,43 | −%3,77 |
| 2026-Q1 | %0,00 | %0,00 | %0,00 |
| 2026-Q2 | −%1,11 | −%3,12 | −%2,03 |
| 2026-Q3 | +%14,44 | +%25,72 | +%23,46 |

İlk 28 gün (2023-01-01 → 2023-01-29) ısınmadır: M2X boş başlar.

### M2X'e özgü çıktılar (§3.5)

| Ölçü | (b) | (p) |
|---|---|---|
| Kademede geçen gün | K1 928, K2 412, K3 29 | K1 1141, K2 228 |
| En uzun kalış (gün) | K1 300, K2 83, K3 29 | K1 331, K2 97 |
| Aşağı inme / yukarı çıkma (anahtar) | 11 / A 2, B 9 | 4 / A 1, B 3 |
| Durdurma | yok | yok |
| En büyük OR/E · CL/E | %38,26 · %48,56 | %32,71 · %40,03 |
| Saatlik kriz sonrası düşüş 1 − (E − CL)/P: en büyük · %50'yi aştığı saat payı | %50,65 · %2,16 | %50,38 · %0,12 |
| Bağlayan tavan (aday gelen tur sayısı) | yok 337, CL 52, OR 218 | yok 524, CL 3, OR 78 |
| M2 adayı · tam boy / küçültülmüş / atlanmış | 1651 · %38,5 / %30,0 / %31,5 | 1651 · %83,7 / %4,8 / %11,4 |
| Atlanma nedeni (kademe: neden sayısı) | K1: M2X_CRASH_BUDGET 63, M2X_OPEN_RISK_CAP 333; K2: M2X_CRASH_BUDGET 1, M2X_OPEN_RISK_CAP 123 | K1: M2X_CRASH_BUDGET 1, M2X_MIN_NOTIONAL 2, M2X_OPEN_RISK_CAP 184; K2: M2X_MIN_NOTIONAL 2 |
| Açılan girişler kademeye göre | K1 723, K2 396, K3 12 | K1 1234, K2 228 |
| M2'deki R: tam boy kopyalanan | n = 636, +0,052 R [−0,017, +0,134] | n = 1382, +0,077 R [+0,023, +0,141] |
| M2'deki R: küçültülerek kopyalanan | n = 495, +0,038 R [−0,042, +0,131] | n = 80, −0,004 R [−0,185, +0,192] |
| M2'deki R: atlanan | n = 520, +0,089 R [−0,024, +0,214] | n = 189, −0,041 R [−0,200, +0,146] |
| Ortalama OR/E · M2'ye göre gerçekleşen çarpan | %7,76 · 2,30 | %5,73 · 1,39 |
| Min-notional çıkarması · bracket kaldıraç indirimi · defterin marja küçültmesi | 43 · 0 · 0 | 110 · 0 · 0 |
| Likidasyon · likidasyon ayrışması (R maliyeti) | 0 · 0 (0,00 R) | 1 · 0 (0,00 R) |
| Diğer ayrışmalar: yetim · M2 likide/M2X değil · (b)–(d) payı | 0 · 0 · %0,00 | 0 · 0 · %0,00 |
| Eşleşen işlem başına M2X R − M2 R (n; ort.; %5–%95) | 1124; +0,0000; +0,0000 – +0,0000 | 1450; +0,0008; +0,0000 – +0,0000 |
| M2X'te kapanan işlem · ortalama R | 1124 · +0,0352 | 1450 · +0,0598 |
| STOP_AT_LEVEL duyarlılığı (n; toplam R kazancı) | 0; +0,00 | 1; +1,19 |
| Ücret · funding (USDT) | 21,50 · −27,78 | 15,88 · −24,88 |

Okuma notları:

- "Bağlayan tavan" aday gelen tur başınadır. "Atlanma nedeni" M2 adayı başınadır.
- Gerçekleşen çarpan: aynı günlük görüntüde (M2X açık riski / M2X özkaynağı) ÷ (M2 açık riski / M2 özkaynağı)
  oranlarının ortalaması (M2'nin açık riski olan günler).
- En büyük CL/E (%48,56) kriz bütçesinin (giriş anında E − 0,5 P) sınırındadır. Saatlik kriz sonrası düşüş serisi
  1 − (E − CL)/P en çok %50,65'e çıktı ve saatlerin %2,16'sında %50'nin üstündeydi. Bu girişten SONRA fiyat
  hareketiyle olur; ön kayıt (§2.3) bunu öngörüyordu. Gerçekleşen düşüş değil, "o saatte hepsi aynı anda %50 düşseydi"
  senaryosudur.
- Likidasyon: (b)'de hiç yok. (p)'deki tek likidasyon M2'nin de likide olduğu işlemdir (APT/USDT, 2025-10-10 22:00
  UTC, ihtiyatlı kural `INTRABAR_ORDER_UNOBSERVED`): M2'de −3,20 R, (p)'de −2,11 R (L_liq ile marj küçük, kayıp marjla
  sınırlı). (p)'nin eşleşen işlemlerinde R farkı olan tek işlem budur.
- STOP_AT_LEVEL duyarlılığı: bu likidasyon stop seviyesinden dolsaydı (p)'de +1,19 R, M2'de +2,28 R daha iyi
  olurdu. (b)'de ihtiyatlı likidasyon kaydı yok.

### Kol (b)'nin kademe yolu (tek gerçekleşme)

| Tarih (00:05 UTC) | Olay | Kademe | Anahtar | Gerçek zirveden DD | Kademe zirvesinden DDₖ | E (USDT) |
|---|---|---|---|---|---|---|
| 2023-05-09 | aşağı | K1 → K2 | — | %16,18 | %16,18 | 185,59 |
| 2023-06-02 | yukarı | K2 → K1 | B | %15,91 | %15,91 | 186,18 |
| 2023-06-11 | aşağı | K1 → K2 | — | %22,18 | %16,71 | 172,30 |
| 2023-07-18 | yukarı | K2 → K1 | B | %20,25 | %14,64 | 176,58 |
| 2023-09-21 | aşağı | K1 → K2 | — | %25,08 | %15,45 | 165,88 |
| 2023-10-04 | yukarı | K2 → K1 | B | %24,16 | %14,42 | 167,91 |
| 2024-05-08 | aşağı | K1 → K2 | — | %15,07 | %15,07 | 337,18 |
| 2024-07-13 | yukarı | K2 → K1 | B | %19,75 | %19,75 | 318,59 |
| 2024-08-03 | aşağı | K1 → K2 | — | %26,04 | %17,05 | 293,64 |
| 2024-10-25 | yukarı | K2 → K1 | B | %27,16 | %18,31 | 289,16 |
| 2024-11-04 | aşağı | K1 → K2 | — | %31,50 | %15,35 | 271,96 |
| 2024-11-11 | yukarı | K2 → K1 | A | %21,63 | %3,16 | 311,15 |
| 2025-01-09 | aşağı | K1 → K2 | — | %19,47 | %15,30 | 319,70 |
| 2025-03-29 | aşağı | K2 → K3 | — | %29,23 | %25,57 | 280,95 |
| 2025-04-27 | yukarı | K3 → K2 | B | %28,80 | %25,11 | 282,67 |
| 2025-05-13 | yukarı | K2 → K1 | B | %21,23 | %11,50 | 312,71 |
| 2025-06-06 | aşağı | K1 → K2 | — | %28,85 | %18,71 | 282,46 |
| 2025-07-16 | yukarı | K2 → K1 | B | %27,38 | %17,02 | 288,30 |
| 2025-09-26 | aşağı | K1 → K2 | — | %29,43 | %17,34 | 280,19 |
| 2025-11-04 | yukarı | K2 → K1 | B | %28,75 | %16,56 | 282,85 |
| 2026-08-31 | aşağı | K1 → K2 | — | %32,86 | %15,19 | 266,55 |
| 2026-09-08 | yukarı | K2 → K1 | A | %26,36 | %6,98 | 292,35 |

## Bilgi kolları (v1'i DEĞİŞTİRMEZ)

| Kol | Tanım | Toplam | Aylık ort. / medyan | En kötü ay | Düşüş günlük / 1h | Gün ≥ +%1 / ≤ −%1 | Durdurma | Kriz sonrası düşüş en büyük | Kademe günleri |
|---|---|---|---|---|---|---|---|---|---|
| (b) | M2X v1: §2'nin tamamı | +%71,4 | +%1,58 / −%0,54 | −%8,30 (2024-04) | %32,86 / %34,29 | %14,3 / %12,5 | yok | %50,65 | K1 928, K2 412, K3 29 |
| (c) | K1 sabit: %2, OR %20, kriz bütçesi, L_liq; kademe ve durdurma yok | +%76,9 | +%1,74 / −%0,78 | −%11,27 (2025-03) | %35,15 / %37,35 | %16,1 / %15,1 | yok | %51,92 | K1 1369 |
| (d) | %2; OR tavanı, kriz bütçesi, kademe, durdurma yok; pozisyon başı marj tavanı yok | +%185,8 | +%4,05 / −%1,37 | −%14,22 (2024-06) | %58,09 / %58,64 | %20,5 / %18,7 | yok | %96,83 | K1 1369 |
| (e) | v1, K1 OR tavanı %25 | +%128,0 | +%2,34 / −%0,49 | −%8,47 (2024-04) | %33,46 / %34,93 | %15,1 / %12,7 | yok | %51,55 | K1 848, K2 492, K3 29 |
| (f) | v1, OR girişteki riskle | +%147,3 | +%2,69 / −%0,67 | −%9,10 (2024-04) | %34,84 / %36,10 | %15,3 / %13,3 | yok | %56,20 | K1 873, K2 412, K3 84 |
| (g) | v1, kaldıraç L_need | +%78,4 | +%1,68 / −%0,67 | −%8,32 (2024-04) | %34,38 / %38,45 | %14,0 / %12,5 | yok | %51,61 | K1 950, K2 390, K3 29 |
| (h1) | v1 + DUR'da bütün pozisyonlar kapanır | +%71,4 | +%1,58 / −%0,54 | −%8,30 (2024-04) | %32,86 / %34,29 | %14,3 / %12,5 | yok | %50,65 | K1 928, K2 412, K3 29 |
| (h2) | v1 + kademe düşüşünde orantılı küçültme | +%71,4 | +%1,58 / −%0,54 | −%8,30 (2024-04) | %32,86 / %34,29 | %14,3 / %12,5 | yok | %50,65 | K1 928, K2 412, K3 29 |
| (i1) | v1, P ve Pₖ gerçekleşmiş özkaynaktan | +%77,5 | +%1,77 / −%0,55 | −%10,82 (2025-03) | %36,10 / %37,87 | %15,3 / %13,7 | yok | %52,46 | K1 1064, K2 305 |
| (i2) | v1, P ve Pₖ stop değerli özkaynaktan | +%56,7 | +%1,42 / −%0,47 | −%10,36 (2025-03) | %42,82 / %44,02 | %14,1 / %12,9 | yok | %52,26 | K1 986, K2 367, K3 16 |
| (j) | v1, yalnız A anahtarı | +%65,8 | +%1,35 / −%0,18 | −%8,24 (2024-04) | %30,21 / %32,05 | %9,4 / %8,7 | yok | %50,25 | K1 325, K2 283, K3 761 |

- (d) neden tavan var sorusunun cevabıdır: tavansız %2 riskle +%185,8, ama en büyük düşüş %58,09, ayların %17,8'i
  ≤ −%10 ve saatlik kriz sonrası düşüş %96,83'e çıktı. (d) "4 kat" değildir, marjın izin verdiği kadardır.
- (h1) ve (h2) (b) ile aynıdır. (h1): durdurma hiç olmadı. (h2): 11 aşağı inmenin her birinde açık risk yeni tavanın
  altındaydı ve kriz bütçesi artıdaydı; küçültme gerekmedi.
- (e) (K1 tavanı %25) ve (f) (OR girişteki riskle) bu geçmişte daha çok kazandı, düşüşleri (b)'ninkine yakın. (j)
  (yalnız A anahtarı) 761 gün K3'te kaldı. (i2) (stop değerli zirve) en çok düşen v1 türevi.
- Bunlar yalnız bilgidir. Bir bilgi kolunun bu tek geçmişte daha iyi görünmesi v1'i değiştirmez. Sonuç görüldükten sonra
  seçilen bir değişiklik bu veride aşırı uyum riski taşır; değişiklik ayrı ön kayıtla `m2x_v2` olur.

## Politika-tekrar bootstrap'ı (§3.6)

Takvim günleri üzerinde dairesel blok bootstrap. Girişi blokta olan her M2 işlemi tam yoluyla taşınır. Her yol uygulanan
`m2x_policy` kodu ve `FuturesLedgerV2` ile koşar. 2.000 yol, tohum 20261005; kollar aynı yolları kullanır. Girdi kol
(a)'nın 1.651 işlemi ve 1.369 gün. Basitleştirmeler (işaretli): her işlem kendi sentetik sembolüdür (fiyat girişe
normalize), en küçük emir 5 USDT, miktar adımı yok, funding yok. Aylık sütun takvim ayı değil, ardışık 30 günlük
dilimdir.

**Bu sayılar yeniden örnekleme SIKLIKLARIDIR, olasılık DEĞİLDİR.** Her birinin yanında şunlar geçerlidir: örnekte
piyasa çapında tek çöküş (2025-10-10) var; evren hayatta kalanlardan seçildi; örnekte 52 BTC-EMA200 rejim dönemi var
ve bootstrap örnekte olmayan bir rejimi üretemez.

| Anahtar (kol · ufuk · blok) | DD ≥ %15 / %25 / %35 / %50 | Durdurma | Toplam getiri medyan [%5, %95] · eksi yol payı | En büyük düşüş medyan / %95 | 30 günlük dilim ≥ +%1 · dilim ort. medyanı | Gün ≥ +%1 / ≤ −%1 · medyan gün |
|---|---|---|---|---|---|---|
| b · h365 · b20 | %92,4 / %37,4 / %5,6 / %0,0 | %0,0 | +%25,9 [−%25,9, +%144,0] · %27,1 | %22,7 / %35,5 | %42,3 · +%2,40 | %16,8 / %14,7 · %0,000 |
| p · h365 · b20 | %58,8 / %8,9 / %0,1 / %0,0 | %0,0 | +%18,3 [−%19,5, +%89,3] · %26,1 | %16,3 / %26,6 | %37,9 · +%1,62 | %11,0 / %8,8 · %0,000 |
| c · h365 · b20 | %92,4 / %47,4 / %13,6 / %0,0 | %0,0 | +%28,8 [−%28,4, +%151,9] · %24,9 | %24,6 / %39,9 | %44,2 · +%2,69 | %18,2 / %16,2 · %0,000 |
| b · h1369 · b20 | %100,0 / %89,3 / %44,6 / %0,0 | %0,0 | +%146,5 [−%19,3, +%728,9] · %10,3 | %33,8 / %47,1 | %42,1 · +%2,49 | %16,2 / %14,0 · %0,000 |
| p · h1369 · b20 | %97,9 / %51,0 / %13,9 / %0,0 | %0,0 | +%92,3 [−%10,7, +%364,5] · %8,6 | %25,2 / %39,6 | %38,0 · +%1,70 | %10,9 / %8,6 · %0,000 |
| c · h1369 · b20 | %100,0 / %93,7 / %57,5 / %0,0 | %0,0 | +%163,4 [−%22,9, +%835,6] · %10,8 | %36,6 / %48,6 | %43,6 · +%2,70 | %17,4 / %15,3 · %0,000 |
| b · h365 · b5 | %86,7 / %27,7 / %2,9 / %0,0 | %0,0 | +%40,9 [−%17,8, +%168,7] · %16,1 | %21,0 / %32,5 | %51,7 · +%3,31 | %19,7 / %16,2 · %0,000 |
| b · h365 · b60 | %94,1 / %40,0 / %4,5 / %0,0 | %0,0 | +%20,8 [−%26,7, +%141,6] · %30,8 | %23,3 / %34,9 | %35,1 · +%2,08 | %15,6 / %13,7 · %0,000 |

| Anahtar | Yol sonundaki kademe (2.000 yol) |
|---|---|
| b · h365 · b20 | K1 1445, K2 488, K3 67 |
| p · h365 · b20 | K1 1710, K2 279, K3 11 |
| c · h365 · b20 | K1 2000 |
| b · h1369 · b20 | K1 1474, K2 465, K3 61 |
| p · h1369 · b20 | K1 1714, K2 282, K3 4 |
| c · h1369 · b20 | K1 2000 |
| b · h365 · b5 | K1 1577, K2 396, K3 27 |
| b · h365 · b60 | K1 1310, K2 610, K3 80 |

**Kimlik denetimi (bilgi, ön kayıtta yok):** blokları örnekteki sırayla dizen tek yol (başlangıçlar 0, 20, 40, …;
1.369 gün) aynı makineyle koşuldu. (b): +%87,07, en büyük düşüş %31,00; funding'siz tarihsel kol (b): +%86,96, %31,21.
(p): +%96,29, %24,54; funding'siz tarihsel kol (p): +%104,72, %23,61. Yeniden örnekleme makinesi (b)'nin tarihini
funding dışında yeniden üretiyor. (p)'deki fark incelenmedi; bilinen ayrımlar en küçük emir (bootstrap'ta her sembolde
5 USDT, tarihte ETH 20) ve sentetik sembollerdir. Funding'li tarihsel sonuç (b) için +%71,36'dır.

Okuma notları:

- **Durdurma 2.000 yolun hiçbirinde olmadı** (her anahtarda %0,0). En büyük düşüşün ≥ %50 olduğu yol da yok; kademesiz ve
  durdurmasız (c) dahil. Örnekte piyasa çapında tek çöküş olduğu için bu sayı "durdurma olmaz" demek değildir.
- **Bootstrap tarihten iyimserdir.** Kimlik yolu (b) yılda +%18,19 bileşik verir (funding'siz); funding'li tarih
  +%15,45'tir. 365 günlük yollarda (b)'nin medyanı +%25,9. Getiri blok uzunluğuna duyarlıdır (medyan: 5 gün +%40,9,
  20 gün +%25,9, 60 gün +%20,8). Kısa bloklar M2'nin aynı trende düşen girişlerini dağıtır; tavanlar daha az bağlar ve
  daha çok işlem tam boy alınır. Funding de yoktur.
- **Aylık sütun takvim ayı değildir.** Kimlik yolunda 30 günlük dilimlerin %35,6'sı ≥ +%1 (funding'siz); tarihte takvim
  aylarının %20,0'si (funding'li). Bootstrap'taki %42,3 bu iki ölçüyle karşılaştırılmalıdır, tarihsel ayla değil.

## Düzeltme (koşu sırasında bulunan hata)

- İlk kol koşusunda (b)'nin 64, (p)'nin 123 adayı `M2X_LEDGER_MIN_NOTIONAL` ile atlanmıştı. Neden: koşum aracı
  `plan_entry`e sembolün miktar adımını ve en küçük miktarını vermiyordu (M2'nin kendi `fit_size` çağrısı ikisini de
  verir). En küçük emre çıkarılan giriş tam 5,00 USDT notional taşıyordu. Defter miktarı yeniden türetip adıma aşağı
  yuvarlayınca 4,999… < 5 oluyor ve her çıkarma reddediliyordu. §2.6 bu girişlerin en küçük emirle açılmasını ister.
- Düzeltme (`0548073`): filtreler M2'deki gibi verilir ve çıkarılmış giriş planın adıma yukarı yuvarlanmış miktarıyla
  açılır. Politika ve mühür değişmedi. Bir regresyon testi eklendi.
- Düzeltmeden önceki sonuçlar (yalnız şeffaflık için; geçerli sonuç yukarıdakilerdir): (b) 200 → 336,56 (+%68,28),
  aylık ortalama +%1,54, medyan −%0,43, en büyük düşüş %33,86; (p) 200 → 337,87 (+%68,94), aylık ortalama +%1,42, medyan
  −%0,49, en büyük düşüş %29,70. Bootstrap ilk üç anahtardan sonra durduruldu ve düzeltilmiş kodla baştan koştu.
- Düzeltmeden sonra (b)'de 43, (p)'de 110 giriş en küçük emre çıkarıldı. `M2X_LEDGER_*` reddi kalmadı.

## Uyarılar

- **Geçmiş veridir.** Gelecekteki getiri ya da düşüşün göstergesi değildir. Kâğıt sonucudur: gerçek emir defteri, kısmi
  dolum ve gecikme yoktur.
- **Hayatta kalan yanlılığı:** evren 2026-09'da hacme ve yaşa göre seçildi ve 2023'e uygulandı. Bu dönemde sönen ya da
  listeden çıkan semboller örnekte yok.
- **Rejim:** sonucun büyük kısmı birkaç trend döneminden gelir (2023-Q4, 2024-Q1, 2024-11, 2026-09). 2025, (j)
  dışındaki bütün kollarda eksidir ((j) +%0,07). 2026-01 → 04 boyunca M2 pozisyon açmadı. Örnekte piyasa çapında tek
  çöküş var. Daha uzun bir ayı piyasası ya da arka arkaya çöküşler örnekte yok; bootstrap da onları üretemez.
- **Canlıdan farklar:** stop canlıda dakikalık mark'la, burada 1h barla denetlenir (ihtiyatlı). Uçlar last price'tır.
  Giriş tek günlük turda (00:05 UTC) yapılır; canlıda 00:00–00:20 arasıdır ve gün içi girişler olabilir. Canlıda M2
  25–29 pozisyon tutuyor; geçmişte medyanı 3, en çok 32 idi. Tavanlar canlıdaki gibi dolu bir M2'de daha sık bağlar.
- **Funding:** (b)'de ödenen funding (−27,78 USDT) ücretten (21,50 USDT) büyük. Funding arşiv oranıyla hesaplandı.
- **E2 ve adım 0 yapılmadı:** VPS'teki M2 defteri olmadan canlı paritesi ve M2 kanıtının dönem kırılımı (§5 adım 0) bu
  çalışma alanında hesaplanamaz. İkisi de VPS'ten salt okunur kopyayla yapılacak; v1 sayılarını değiştirmez.
- Bu belge M2X için "kârlı", "kanıtlı" ya da "hedefi tuttu" demez. Aylık +%1 ve günlük +%1 hedefleri yalnız rapor
  edilir: (b)'de ayların %20,0'si ≥ +%1, günlerin %14,3'ü ≥ +%1 (≤ −%1 olan günler %12,5, medyan gün %0).

## Yeniden üretim

```
python scripts/m2x_sim.py --stage fetch --cache <önbellek> --out <çıktı>
python scripts/m2x_sim.py --stage cfcheck,m2 --cache <önbellek> --out <çıktı>
python scripts/m2x_sim.py --stage arms --jobs 3 --cache <önbellek> --out <çıktı>
python scripts/m2x_sim.py --stage boot --jobs 3 --cache <önbellek> --out <çıktı>
python scripts/m2x_sim.py --stage report --cache <önbellek> --out <çıktı>
```

Testler: `tests/test_m2x_policy.py`, `tests/test_m2x_sim.py` (ağsız, sentetik barlarla; üreteç = M2 kural kodu, merdiven,
durdurma, histerezis, açık risk tavanı, kriz bütçesi, en küçük emre çıkarma, bootstrap belirlenimciliği).

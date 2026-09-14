# ARAŞTIRMA KAYDI V2 — ne, hangi sırayla, hangi veride

Denetlenebilirlik için kronolojik. Hiçbir satır sonradan silinmedi.

| # | iş | veri | sonuç |
|---|---|---|---|
| 1 | Kaynak karşılaştırması: üretim vs replay karar yolu | kod | 7 aşama YALNIZ üretimde (`out/path_parity.json`) |
| 2 | `economics_gate.py` — tek kaynak kapı, iki motora bağlandı | kod | 12 sözleşme testi |
| 3 | Kapı etkisi ölçümü | 2026-07, 1 ay | kapısız 13 açılış → kapılı 5 |
| 4 | `p_win` etiketi ile kazanç tanımının uzlaştırılması | eski koşular | P(R>0) ≠ P(R>0,25); fark başa-baş çıkışları |
| 5 | Maliyet stresi: sabit işlem vs dinamik portföy | eski koşular | sabit kümede gider artışı DAİMA kötüleştiriyor |
| 6 | Estimand ayrımı (`observed_diff` ≠ bootstrap ortalaması) | eski koşular | +0,0074 vs +0,0087 |
| 7 | **PROTOCOL_V2.md yazıldı** | — | **hiçbir hipotez sonucu görülmeden** |
| 8 | `entry_rules.py` — 3 hipotez × 6 = 18 yapılandırma | — | ızgara sabitlendi |
| 9 | İlk ızgara koşusu başlatıldı (varsayılan filtrelerle) | geliştirme | **İPTAL EDİLDİ, aşağı bak** |
| 10 | **Kusur bulundu:** replay varsayılan borsa kurallarıyla çalışıyor | kod + arşiv | DOGE tick 0,01 vs gerçek 0,00001 |
| 11 | Sıfır fill fiyatı `DivisionByZero` fırlatıyordu | 2021 penceresi | üretim kodunda da vardı; RET'e çevrildi |
| 12 | Gerçek `symbol_filters.json` replay'e bağlandı | kod | 3 yeni test |
| 13 | **Bütün koşular SIFIRDAN tekrarlandı** (22 koşu) | 4 referans + 18 yapılandırma | gerçek kurallarla |
| 14 | Aday seçimi: `h1_r40_c0.2` | yalnız geliştirme | kural §4'e göre |
| 15 | Adayın değerlendirme penceresi koşusu | 2020-11 → 2022-08 | BİR KEZ |
| 16 | Yıl ve yön kırılımı (betimleyici) | iki temel koşu | asıl bulgu |

## 9. satır neden iptal edildi

İlk ızgaranın altı H1 yapılandırması varsayılan borsa kurallarıyla koştu. Varsayılan filtre
her sembole `price_tick = 0,01` ve `qty_step = 0,001` verir. Gerçek kurallar
(`symbol_filters.json`, `source = binance_api`, doğrulama 2026-09-10):

| sembol | varsayılan tick / step / min | gerçek |
|---|---|---|
| DOGE | 0,01 / 0,001 / 5 | **0,00001 / 1 / 5** |
| XRP | 0,01 / 0,001 / 5 | **0,0001 / 0,1 / 5** |
| BTC | 0,01 / 0,001 / 5 | **0,10 / 0,001 / 50** |

Sonuçları: DOGE fiyatı bir kuruşa yuvarlanıyordu (işlem başına iki fill, ~%5 hata),
borsanın kabul etmeyeceği kesirli miktarlar açılıyordu ve BTC'de gerçek minimumun onda biri
kullanılıyordu. **O altı sonuç geçersiz sayıldı ve silinmedi, `out/stale_default_filters/`
altında duruyor.** Bu, bir önceki turun tüm replay sonuçlarını da etkiler.

## Test penceresine kaç kez bakıldı

* **Geliştirme (2022-09 → 2026-09):** sınırsız — zaten önceki turda görülmüştü.
* **Değerlendirme (2020-11 → 2022-08):** temel koşusu 22'lik toplu partide (aday
  seçilmeden önce, seçime GİRMEDEN), aday koşusu **bir kez**. Sonuç görüldükten sonra
  hiçbir parametre değiştirilmedi ve yeni aday üretilmedi.

## Denenip raporlanan her şey

18 yapılandırmanın **hepsi** `out/DENEY_V2.md` sec. 2'de tablodadır; başarısız olanlar
dahil. Ek olarak: iptal edilen 6 koşu (yukarıda), 4 referans, 1 aday değerlendirmesi,
2 hız/yapı sondası. Toplam 33 replay koşusu.

## Yöntem notları

* Nokta tahmini `analyze.observed_diff` ile gözlenen farktır; bootstrap YALNIZ aralık
  üretir. Bloklar coin × takvim ayıdır; hem eşleştirilmiş hem eşleştirilmemiş sürüm
  raporlanır (eşleştirilmiş daha dar, yani muhafazakâr DEĞİL).
* Sharpe günlük GERÇEKLEŞMİŞ özkaynaktan, işlemsiz günler dahil. Gerçekleşmemiş kâr/zarar
  serilmediği için seri düzdür ve Sharpe YUKARI yanlıdır.
* Maruziyet iki ayrı büyüklükle raporlanır: ortalama eş zamanlı pozisyon (1'i aşabilir) ve
  en az bir pozisyonun açık olduğu gün oranı.
* Replay `--no-patterns` koşar ve üretimin `legacy_brief` ajanlarını (momentum, candles,
  levels, volume, analog) İÇERMEZ — replay'de 20 değil **10** uzman çalışır. Bu yüzden
  hipotezler göstergelerini doğrudan çerçeveden hesaplar.

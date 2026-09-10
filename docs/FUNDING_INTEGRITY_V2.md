# Funding bütünlüğü V2 + eski MFE koruması — bağımsız incelemenin D1–D4'ü

**Taban:** `e8f19b6` · **Kapsam:** yalnız aşağıdaki dört onarım ve doğrudan regresyonları.
Yeni strateji, challenger ya da araştırma **yok**. PAPER sınırları değişmedi; sermaye, kaldıraç ve
toplam risk limitleri artırılmadı; kanonik defter, tarihsel dolumlar ve öğrenme kayıtları
**yeniden yazılmadı**; hiçbir koruyucu stop gevşetilmedi.

---

## D1 — Çıkış monitörü ile tur artık aynı kuralı uygular

**Kusur.** `exit_check` 60 saniyede bir çalışır ve önbellek araması yapar ama **ağa çıkmaz**;
`ensure_funding_rates`'in tek çağıranı 15 dakikalık turdur. Monitör settlement sınırına turdan önce
varıyor, `meta.last_funding_rate` ile **tahmini** tahakkuk yapıyor ve **watermark'ı ilerletiyordu**.
Tur sonra `settlements_due == []` bulup gerçek oranı hiç sormuyordu. Ölçülen hata 36,1×.

**Onarım.** `FundingQuote` artık `verified` taşır ve `FundingSchedule.require_verified` (varsayılan
**açık**) yalnız doğrulanmış bir alıntının dönemi kapatmasına izin verir. Doğrulanmamış hiçbir
değer — anlık snapshot oranı, `meta.last_funding_rate`, çıplak skaler — watermark'ı ilerletemez.
Dönem **bekler**, kaybolmaz; bir sonraki tur gerçek oranla **tam bir kez** kapatır.

Sonuç: monitör ile tur aynı `funding_rates.lookup`'ı ve aynı kuralı kullanır. Turdaki
`chained_rates(cache, static_rates(...))` yedeği **kaldırıldı** — doğrulanmamış olduğu için zaten
hiçbir dönemi kapatamıyordu ve "yedek çalışıyor" yanılsaması yaratıyordu.

**Çift tahakkuk.** Tur tick'i artık çıkış monitörüyle **aynı** `_exit_lock` altında çalışır.
Tekrar, eşzamanlı çağrı ve restart üç ayrı testle kapatıldı (aşağıda).

## D2 — Sabit sekiz saat varsayımı kaldırıldı

**Kusur.** `FUNDING_HOURS_UTC = (0, 8, 16)` sabitti. Bu botun işlem gördüğü 34 sembolün **10'u**
bu takvimde değil: CL, BZ, NATGAS, GPS, ONDO, PAXG, XAUT, XPD, ZRO **4 saatte bir**, NVDA
**saatte bir**. Grid o sembollerde dönemlerin yarısını ya da çoğunu hiç sormuyordu; açık 14
pozisyonda tahakkuk etmemiş funding **0,223121 USDT** ölçülmüştü.

**Onarım.** `FundingSchedule.settlement_source` eklendi ve üretimde
`FundingRateCache.settlements_in`'e bağlandı: settlement zamanları venue'nun **gerçekten
yayımladığı** kayıtlardan gelir. Grid varsayımı üretim yolunda kalmadı; `hours_utc` yalnız
kaynak verilmediğinde kullanılan bir yedektir.

Önbellek yenilemesi de **pencere tabanlı** oldu (`ensure_window`): "hangi grid saatleri eksik"
sorusu yerine "watermark'tan şimdiye kadarki pencere kapsandı mı". Pencere en kısa venue
aralığından (1 saat) kısaysa **hiç istek atılmaz**; kapsandıysa da atılmaz.

`ops/gap.py` ve replay/counterfactual harness'leri de aynı iki değişikliği aldı: doğrulanmış alıntı
ve gerçek settlement zamanları.

## D4 — Doğrulanmış sıfır ile eksik verinin varsayılan sıfırı ayrıldı

**Kusur.** `accrue` içinde `rate == ZERO` dalı dönemi **olaysız** kapatıyordu. `last_rate`
`meta.last_funding_rate == "0.0"` üzerinden geldiğinde watermark sessizce ilerliyor, hiçbir kayıt
kalmıyordu. Üretimde üç canlı pozisyon (CRCL, META, CRWV) tam olarak bu durumdaydı.

**Onarım.** Kaynaktan doğrulanmış sıfır artık gerçek bir olaydır:
`FundingEvent(amount=0, zero_rate=True, verified=True)` üretilir, dönem kapanır ve deftere geçer.
Doğrulanmamış sıfır ise oraya **hiç ulaşamaz** — `require_verified` daha önce bekletir.

## Eski (şişirilmiş) MFE koruması — dar çözüm

**Sorun.** Bar provenansı onarımı yeni sahte katkıyı engelliyor ama `mfe_pct` koşan bir maksimumdur
ve defter yeniden yazılmaz; onarımdan önce birikmiş sapma **kalıcıydı**. Ölçülmüş örnekler:
NATGAS 0,65R kayıtlı / 0,12R gerçek, GPS 0,67R / 0,21R — başa-baş kuralı amaçlanan eşiğin kabaca
yarısında ateşleyebiliyordu.

**Çözüm (semboller ya da güncel değerler VARSAYILMADAN).** `Position.mfe_pct_trusted` eklendi.
Defter bir pozisyonu ilk kez tick ettiğinde `meta.mfe_trusted_from` damgasını koyar, devralınan
tepeyi `meta.mfe_pct_legacy_at_stamp` altında **kayıt için** saklar ve güvenilir tepeyi sıfırdan
başlatır. O andan sonra biriken her değer ya provenansı doğrulanmış bir bardan ya da gerçek
mark'tan gelir. Başa-baş kuralı artık `mfe_pct` yerine **`mfe_pct_trusted`** okur.

* `mfe_pct` **değiştirilmez** — kayıt alanıdır, defter ve öğrenme kayıtları yeniden yazılmaz.
* Var olan `be_by_mfe` girdileri ve taşınmış stoplar **olduğu gibi kalır**; kural yalnız *yeni*
  hareket üretmeyi kısıtlar, hiçbir stopu gevşetmez.
* Onarımdan **sonra** açılan pozisyonda `mfe_pct` zaten sıfırdan başlar, dolayısıyla güvenilir tepe
  ile kayıt tepesi birebir aynı ilerler — davranış değişmez (test ile kilitli).
* Alan ve damga kalıcıdır: restart eski şişmeyi geri getirmez.

**Dondurulmuş üretim defteri üzerinde davranış** (14 açık pozisyon; bu bir anlık görüntüdür,
güncel VPS durumu değildir): yükleme anında damgalı pozisyon **0**; bir tick sonra hepsi damgalanır,
devralınan tepeler saklanır, güvenilir tepe gerçek mark hareketinden yeniden birikir ve **yeni
başa-baş hareketi oluşmaz**. NATGAS güvenilir 0,00 ve GPS 1,33 (= 0,14R) — ikisi de 1,00R eşiğinin
çok altında. Zaten taşınmış üç stop (CL, BZ, GOOGL) korunur.

## D3 — Test boşlukları davranışla kapatıldı

`tests/test_funding_integrity_v2.py` (14 test) gerçek motor/monitör/tur yollarını sınar; yardımcı
fonksiyon ya da kaynak metni kontrolü değildir.

| Mutasyon | Eskiden | Şimdi |
|---|---|---|
| `engine_v3._marks` → `bar_open = ""` | **0 test** düşüyordu (2103'ün hepsi yeşil) | `test_marks_carries_provenance_...` düşer — gerçek `_marks` çıktısı gerçek deftere veriliyor ve ömür içi barın ucu MFE'ye **ulaşmak zorunda** |
| turdan `ensure_funding_rates` kaldır | **0 test** düşüyordu | **3 test** düşer (monitör/tur sözleşmesi, yenileme kapısı, ölü venue) |

Mutasyonlar ölçüldü, sonra geri alındı; `tradingbot/engine_v3.py` çalışma kopyasında değişiklik yok.

Diğer kapılar: 4 saatlik ve 1 saatlik sözleşmede dönem sayısı, doğrulanmış sıfır, doğrulanmamış
sıfırın hiçbir şeyi kapatmaması, ölü venue'da turun düşmemesi ve hiçbir dönemin kaybolmaması,
restart sonrası tekrar tahakkuk olmaması, monitör ile turun eşzamanlı çalışmasında her
settlement'ın tam bir kez tahakkuk etmesi.

## Değişen sözleşme — mevcut testlerde yapılan güncellemeler

`require_verified` varsayılan **açık** olduğu için, konusu tahakkuk mekaniği olan mevcut testler
kaynaklarını artık **açıkça doğrulanmış** bildiriyor (gevşetme değil, sözleşmeyi ifade etme).
Konusu doğrudan eski "snapshot yedeği" davranışı olan üç test ise yeni, **daha katı** garantiyi
ifade edecek biçimde yeniden yazıldı:

* `test_venue_unreachable_keeps_cache_and_uncovered_periods_wait` — kapsanmayan dönemler artık
  snapshot oranıyla kapanmaz, **bekler**.
* `test_funding_unknown_periods_wait_instead_of_being_estimated` — tahmini tahakkuk yok; oran
  sonradan gelince kaçan dönemler geriye dönük ve tam bir kez uygulanır.
* `test_unverified_scalar_settles_nothing_and_holds_the_watermark` — **yeni** D1 regresyon kapısı.

## Açık kalan

* **D5** ölü venue'da tur gecikmesi: `max_symbols_per_refresh = 8`, zaman aşımı 5 sn. Pencere
  tabanlı yenileme istek sayısını büyük ölçüde düşürür (8 saatlik sembolde ~8 saatte bir) ama
  en kötü durumda tur başına ~40 sn gecikme olasılığı duruyor.
* **D6/D7** `test_09`'un ilk iddiasının boş olması ve `test_10`'un `bar_open=""` ile geçmesi.
  İkisi de bu turda **kapatılmadı**; yerlerini yeni davranış testleri fiilen doldurdu ama eski
  testler olduğu gibi duruyor.
* **D8** `bar_extremes_skipped` hâlâ üretimde hiçbir log/rapor alanına yazılmıyor.
* **D9** her girişten sonra ~1,5 saat stop/likidasyon yalnız mark ile değerlendirilir (29 kapanışta
  2125 saatin 45,5 saati; 0/29 işlem tamamen kör değil).
* İki açık pozisyonun **kayıtlı** `mfe_pct`'i şişik kalır; düzeltmek defteri değiştiren ayrı ve
  onay gerektiren bir iştir. Başa-baş kuralı artık o değeri okumaz.

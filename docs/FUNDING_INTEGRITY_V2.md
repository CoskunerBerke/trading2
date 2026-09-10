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
sorusu yerine "watermark'tan şimdiye kadarki pencere kapsandı mı".

**Kendi değişikliğimde ölçerek bulduğum iki kusur, aynı turda onarıldı:**

1. **Her turda yeniden istek.** İlk hâlinde `needs_window` yalnız "kapsama penceresi şimdiyi
   örtüyor mu" diye bakıyordu; pencerenin sonu her turda ilerlediği için koşul hep sağlanıyordu.
   Ölçüldü: 24 saatte 96 tur → **96 istek**. Artık bekleme, venue kayıtlarından **gözlenen**
   aralıktan türetilir (varsayılmaz): ardışık settlement'lar arasındaki en kısa fark. Ölçüldü:
   8 saatlik sembolde 96 turda **27**, 4 saatlikte **14**, 1 saatlikte **12** istek. Kalan istekler
   ısınma aşamasındandır — iki kayıt birikene kadar aralık bilinemez ve 1 saatlik taban kullanılır.
2. **Kapsanmayan dönemi atlayıp sonrakini kapatma.** `settlements_in` yalnız önbellekteki
   zamanları döndürdüğü için, önbellek geç bir pencereyi taşıyorsa aradaki dönem atlanıp
   sonraki kapatılabiliyordu — watermark ileri sarar ve dönem **sonsuza kadar kaybolurdu**.
   Bu, onarılan D1 kusurundan **daha kötü** olurdu. Artık çekilen pencerenin başlangıcı da
   izleniyor (`covered_from`) ve kapsama watermark'tan önce başlamıyorsa `settlements_in`
   **boş döner** (fail-closed). İkisi de testle kilitlendi.

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

`tests/test_funding_integrity_v2.py` (16 test) gerçek motor/monitör/tur yollarını sınar; yardımcı
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

* **D5** ölü venue'da tur gecikmesi: `max_symbols_per_refresh = 8`, zaman aşımı 5 sn, hata sonrası
  300 sn soğuma. Pencere tabanlı yenileme istek sayısını düşürdü (yukarıdaki ölçüm) ama en kötü
  durumda tur başına ~40 sn gecikme olasılığı **duruyor**; bu turda ele alınmadı.
* **D6/D7** `test_09`'un ilk iddiasının boş olması ve `test_10`'un `bar_open=""` ile geçmesi.
  İkisi de bu turda **kapatılmadı**; yerlerini yeni davranış testleri fiilen doldurdu ama eski
  testler olduğu gibi duruyor.
* **D8** `bar_extremes_skipped` hâlâ üretimde hiçbir log/rapor alanına yazılmıyor.
* **D9** her girişten sonra ~1,5 saat stop/likidasyon yalnız mark ile değerlendirilir (29 kapanışta
  2125 saatin 45,5 saati; 0/29 işlem tamamen kör değil).
* İki açık pozisyonun **kayıtlı** `mfe_pct`'i şişik kalır; düzeltmek defteri değiştiren ayrı ve
  onay gerektiren bir iştir. Başa-baş kuralı artık o değeri okumaz.

---

# Bağımsız inceleme (2026-09-10) ve sonrasında yapılanlar

İnceleme `e5c365b` üzerinde yapıldı ve **onay vermedi**: bir YÜKSEK gerileme, üç test boşluğu ve
beş düşük öncelikli bulgu çıkardı. Hepsi aşağıda; hangisi kapatıldı, hangisi açık kaldı.

| # | Bulgu | Durum |
|---|---|---|
| **DEF-1** | **Yenileme her turda çalışıyor** (ölçüldü: 14 pozisyon için 714 istek/gün, `e8f19b6`'da ~56) **ve** `sorted(todo)[:8]` alfabetik ilk 8'i besleyip kuyruğu 8 saate kadar aç bırakıyor. `e8f19b6`'ya göre GERİLEME. | **KAPANDI.** Her-turda kısmı `ac2e3a8`'de zaten onarılmıştı (kendi ölçümümle bulunmuştu). Aç bırakma kısmı burada kapandı: seçim artık alfabetik değil, **en eski çekimden** başlıyor. Ölçüldü (sembol başına doğru filtrelemeyle, 14 sembol / 3 gün / 288 tur): **350 istek (117/gün)**, sembol başına dağılım **21–33** (eskiden 9–261), her sembolün gecikmesi **1,0 saat**, hiç çekilmeyen sembol **yok**. |
| **DEF-2** | Bekleyen bir settlement **pozisyon kapanınca kalıcı kaybolur**; kayda sessiz sıfır yazılır. "Dönem bekler, kaybolmaz" iddiası kapanışta **yanlış**. | **KAPANDI (iddia da düzeltildi).** Çözülememiş dönem sayısı artık `TradeRecord.funding_pending_settlements` ve `meta.funding_watermark_at_close` ile taşınıyor, ayrıca WARNING loglanıyor. Uydurma oran hâlâ **yazılmıyor** — kayıt eksik olduğunu **söylüyor**. Garanti: *dönem pozisyon AÇIKKEN kaybolmaz; kapanışta çözülememişse kayıt bunu bildirir.* |
| **DEF-3** | D2'nin **üretim kablolaması** test edilmiyor: `settlement_source = None` → 2142 test yeşil. | **KAPANDI.** `test_engine_wires_the_venue_settlement_source_into_the_ledger` motorun kendi defteri üzerinden 4 saatlik sözleşmenin dönem sayısını sayar. Mutasyonla doğrulandı: **1 test düşüyor.** |
| **DEF-4** | Eski MFE testi 3,2 puan (=**0,96R**) ekiyordu — eşiğin altında, eski kod da ateşlemezdi: **kapı değil**. | **KAPANDI.** Ekilen tepe **4,0 puan (1,20R)** yapıldı; eski kod bunu ateşlerdi. Mutasyonla doğrulandı: **2 test düşüyor.** |
| **DEF-5** | Turdaki `_exit_lock` test edilmiyor; kilit testi bloklamayla ölçtüğü için mutasyonda düşmüyordu (`ensure_gap_reconciled` zaten aynı kilidi alıyor). | **KAPANDI.** Test kilidi sayaç proxy'siyle sarıp `ledger2.tick` çağrıldığı **anda** tutma derinliğini okuyor. Mutasyonla doğrulandı: **1 test düşüyor.** |
| **DEF-6** | Güvenilir tepe sıfırlaması, arm olmaya yakın üç pozisyondan (ETH 0,873R, META 0,918R, ZEN 0,952R) korumayı **geri çekiyor**; belge bunu söylemiyordu. | **BELGELENDİ** (aşağıda). Bilinçli takas. |
| **DEF-7** | `verified` / `zero_rate` / `pending_settlements` hiçbir yere yazılmıyor; doğrulanmış sıfır ile veri yokluğu defterde ayırt edilemiyor, bekleyen dönem için **log bile yok**. | **KAPANDI.** Defter kaydı `funding rate=X verified` / `... zero verified` etiketi taşıyor; bekleyen dönem WARNING loglanıyor; kapanış kaydı sayıyı taşıyor. |
| **DEF-8** | Altı üretim yorumu/metni artık **tersini** söylüyor (operatörün gördüğü Obsidian raporu dâhil). | **KAPANDI.** Altısı da düzeltildi. |
| **DEF-9** | `scripts/research/funding_endtoend_check.py`'nin "ESKİ" kolonu sessizce `+0.000000` olmuş; doğrulama vakum. | **KAPANDI.** Eski semantik açıkça yeniden kuruluyor; **üretim yolu ile bağımsız mutabakat hâlâ 6 hanede aynı.** |
| **DEF-10** | `replay/challengers.py` ve `futures_backtest.py` hâlâ sabit grid'de. | **KISMEN.** `challengers.run_plan` artık `funding_settlements` alıyor ve `challenger_realised29.py` gerçek venue zamanlarını geçiriyor. `futures_backtest.py` **açık kaldı** (üretim yolu değil). |
| **DEF-11** | Önbellek, kilit dışında mutasyona uğruyor; monitör iterasyon hâlindeyken tur yeni anahtar ekleyebilir. | **AÇIK.** Etki sınırlı: `settlements_due` istisnayı fail-closed yakalar (dönem bekler, uyarı loglanır) ve watch döngüsü bugün tek iş parçacıklı. Kaydedildi, onarılmadı. |

## İncelemenin haklı olduğu iki ifade düzeltmesi

1. **"Yeni başa-baş hareketi oluşmaz" bir kanıt değildi.** Dondurulmuş defterde 1,0R üstündeki her
   pozisyon zaten `tp1_done` ya da zaten `be_by_mfe` taşıyor; en yüksek uygun aday ZEN 0,952R.
   Yani **onarılmamış kod da** yeni hareket üretmezdi. Doğru ifade: onarım, şişirilmiş tepeyi
   kuralın girdisi olmaktan çıkarır; dondurulmuş anlık görüntüde bunun gözlenebilir bir farkı
   yoktur. Ayırt edici kanıt testtedir (ekilen 1,20R tepe: eski kod ateşler, yeni kod ateşlemez).
2. **`require_verified` üretimde canlı muhafız değil, ikinci kemerdir.** `settlement_source`
   bağlıyken `settlements_due` yalnızca önbellekteki anahtarları döndürür, dolayısıyla `lookup`
   garantili isabet eder ve `require_verified` pratikte devreye girmez. Asıl koruma D2'nin
   kendisidir; `require_verified` kaynak değişirse ya da biri `settlement_source`'u kaldırırsa
   devreye giren yedektir.

## DEF-6 — bilinçli takas, açıkça

Güvenilir tepe sıfırdan başladığı için, onarımdan önce açılmış ve **arm olmaya yakın** pozisyonlar
korumayı yeniden kazanmak zorundadır. Dondurulmuş defterde bunlar ETH (0,873R), META (0,918R) ve
ZEN (0,952R). Hiçbir stop **gevşetilmez**, var olan hiçbir `be_by_mfe` düşürülmez; geri çekilen şey
*henüz oluşmamış* bir korumadır. Gerekçe: bu üç tepe de provenans onarımından önce birikti ve
doğrulanabilir değil. Alternatif — doğrulanamayan bir tepeye dayanarak stop taşımak — bu sürümün
kapatmaya çalıştığı davranışın ta kendisidir.

## Sayı düzeltmeleri

* Turdan `ensure_funding_rates` kaldırıldığında düşen test sayısı **3 değil 4**'tür
  (`test_restart_does_not_reapply_settled_periods` de düşüyor).
* Önceki commit mesajındaki "2103 test" bu ağaca ait değildir: `e5c365b`'de 2164 test toplanır,
  2142 geçer, 22 atlanır.
* `e8f19b6`'nın ~56 istek/gün rakamı daha düşüktür ama **yanlış takvime** (sabit 8 saat) sorduğu
  için eksik soruyordu. Doğru takvimle 14 sembolün gerçek settlement sayısı günde 42–84'tür;
  117 istek bunun üstünde ince bir paydır ve her istek ağırlık 1'dir.

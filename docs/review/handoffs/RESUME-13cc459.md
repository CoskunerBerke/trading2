# DEVAM NOKTASI — funding tamamlanma sürümü (dondurulmuş)

Bu dosya bilgisayar kapansa bile oturumun nerede kaldığını taşır. Kaynak: 2026-09-10 oturumu.

## Dondurulan sürüm

| Alan | Değer |
|---|---|
| Nihai SHA | `13cc459dc498e51ecf5c6382e0ac9ba3d204784a` |
| Kısa | `13cc459` |
| Dallar | `feature/evidence-repairs-v1` ve `feature/quant-evaluation-v1`, ikisi de origin'de bu SHA'da |
| Release çalışma ağacı | `C:\Users\berke\release-lci` (temiz) |
| VPS'te çalışan sürüm | `1c4cba1b49307c10a53f0c25a62eccb229b3da1f` — DEĞİŞMEDİ, dağıtım YAPILMADI |

## Paket (dağıtılmadı)

Klasör: `C:\Users\berke\trading2-deploy\`

```
tb-13cc459.bundle       a5ed580703faf1718a0c8cf3e29169143b0261f025b1aaa8b1821dfadbce0c48
tb-deploy-13cc459.sh    cd44721321623eb3f1b841d0b93afe08d19a7e7dbd022007925f501ee44eff80
```

Bundle `13cc459`'u içerir, tabanı `1c4cba1`'dir (`git bundle verify` ile doğrulandı).
Betik LF satır sonlu, 21944 bayt, `bash -n` temiz. Eski paketler `superseded/` altındadır.

VPS'te çalıştırma: `bash ~/tb-deploy-13cc459.sh` — çıkış kodu 0 ve son satırda `DEPLOY_OK`
görünmelidir. **Bu oturumda çalıştırılmadı ve kullanıcı onayı olmadan çalıştırılmamalı.**

## Sürüm hattı

`0bcbcb0` (kapanış incelemesinde BLOKE) → `f9a2807` → `8cb299f` → `13cc459`.

Engel: üretimde `settlement_source = FundingRateCache.settlements_in` bağlıyken `settlements_in`
iki AYRI durumda aynı boş listeyi döndürüyordu — "aralık kontrol edildi, settlement yok" ve
"kapsamanın ne olduğu bilinmiyor". Kapanış kaydı ikisini ayırt edemediği için eksik funding
sessiz sıfır olarak geçiyordu ve öğrenmeye kesinleşmiş sonuç gibi giriyordu.

## Doğrulama durumu (13cc459)

| Ölçüm | Sonuç |
|---|---|
| Yerel tam paket | 2186 geçti, 22 atlandı, çıkış 0 |
| CI (üç iş) | Hepsi başarılı; CI tam paketi 2204 geçti, 4 atlandı |
| ruff `check .` + compileall | temiz |
| Mutasyon kapısı | 37 mutasyonun 37'si yakalandı, hayatta kalan yok |
| Sessiz sıfır (bütün ölçüm kesitleri) | 0 |
| 30 dk tutmada yanlış "EKSİK" | %45 → %0 |
| İstek maliyeti | 164/gün (14 sembol); önceki sürümde 117/gün |

Bağımsız inceleme: `f9a2807` ve `8cb299f` ayrı ayrı incelendi, ikisi de gerçek kusur çıkardı ve
kusurlar kapatıldı.

## AÇIK İŞ — devam noktası

`13cc459` bağımsız olarak İNCELENMEDİ. Kullanıcının verdiği son görev:

* Karşılaştırma tabanı **`8cb299f`** (son bağımsız incelemenin kapsadığı SHA).
* Mevcut doğrulayıcıya YALNIZ `git diff 8cb299f 13cc459 -- tradingbot/` kontrol ettirilecek.
* Kapsam: önceki engelleyici bulguların kapanışı ve bu farkın doğrudan regresyonları.
* Yeni genel tarama, araştırma, mutasyon kampanyası ve tam test/CI tekrarı YOK.
* Engelleyici yoksa: aynı SHA ve paket hash'leriyle kısa onay raporu.
* Engelleyici varsa: kanıtı bildir ve DUR; yeni onarım zinciri otomatik başlatılmayacak.
* VPS'e dağıtım YOK.

İncelenecek üretim kodu farkı (50 ekleme, 14 silme, dört dosya):

```
tradingbot/accounting/futures_ledger.py    8 +++----
tradingbot/engine_v3.py                    7 +++--
tradingbot/learn/research_policy.py       27 ++++++++++++--
tradingbot/market/funding_rates.py        22 ++++++------
```

Farkın içeriği: (1) `needs_window` artık kapsama ispatıyla aynı "tam saat" kuralını kullanıyor,
(2) `has_prefix` içindeki test edilemeyen `cov_to > lo` koşulu kaldırıldı, (3) `_finalize` bozuk
`pending` değerini fail-closed okuyor, (4) atlanan araştırma eşleşmesi
`ResearchRecord.skipped_funding_incomplete` sayacına yazılıyor ve restart'ta korunuyor.

## Kalıcı kayıt

Kalıcı bellek dosyası: `C:\Users\berke\.claude\projects\C--Users-berke-Trading-bot\memory\funding-v2-release-0bcbcb0-blocked.md`

## Devam eden kontrol (bu oturumda başlatıldı)

Mevcut doğrulayıcı **devam ettirilemedi**: bu ortamda alt ajanlara mesaj gönderme aracı yok.
Kullanıcının önceki turdaki kuralı uygulandı — tek bağımsız ajana raporlar ve kesin diff verildi.

* Karşılaştırma tabanı: **`8cb299f`** → hedef **`13cc459`**, yalnız `tradingbot/` altı.
* Diff dosyası: `diff-8cb299f-13cc459-production.patch`
  (sha256 `099d309c9162682192d8a4e15a095c4b23282f8e6a38923a867119f8a02179fc`)
* Ajana verilen kapsam sınırı: yeni genel tarama / araştırma / mutasyon kampanyası / tam test
  ve CI tekrarı YOK; yalnız önceki bulguların kapanışı ve bu farkın doğrudan regresyonları.

Sonuç gelmeden **paket hazır ilan edilmeyecek ve dağıtım yapılmayacak.**

Ajanın doğrulayacağı üç somut soru:
1. Kısa pencerede yenileme kuralı gerçekten hizalandı mı; yeni kural hem SAĞLAM (settlement
   düşebilecek pencereyi atlamamalı) hem SINIRLI (tur başına yeniden çekmeye dönmemeli) mi?
2. Atlanan araştırma eşleşmesi gerçekten kayda geçiyor ve restart'ta korunuyor mu?
3. `has_prefix` içinden kaldırılan `cov_to > lo` koşulu gerçekten (a) kuralı tarafından
   kapsanıyor mu? Kapsanmıyorsa bu doğrudan bir gerileme ve ENGELLEYİCİDİR.

## Bağımsız kontrol SONUCU (8cb299f → 13cc459, üretim kodu)

**Engelleyici bulgu bildirilmedi.** Üç kapanış iddiası da doğrulandı:

1. `needs_window` ile `covers` artık aynı tam-saat kuralını kullanıyor. Sağlamlık 400 bin
   rastgele pencere ve sınır ızgarasında sınandı: atlanan settlement saati 0. Sınırlılık için
   (b) ve (c) kapıları değişmemiş; tur başına yeniden çekmeye dönülmüyor.
2. Atlanan araştırma eşleşmesi sayaca geçiyor, `stats()` içinde görünüyor, restart'ta korunuyor.
3. `has_prefix` içinden kaldırılan `cov_to > lo` koşulu gerçekten (a) kuralı tarafından
   kapsanıyor; taban bölme monotonluğuyla kanıtlandı, karşı örnek bulunamadı.

`_finalize` yeniden düzenlemesi istisna atamıyor ve bozuk değerde TAM diyemiyor.

## AÇIK ARTIK — yayım gecikmesi yarışı (KENDİ REPRODÜKSİYONUMLA DOĞRULANDI)

`refresh` kapsamayı İSTEKTEN alır, yanıttan değil: venue sıfır satır dönse bile pencere
kapsanmış sayılır. Çekim tam saatteki settlement'ın venue tarafından yayımlanmasından ÖNCE
varırsa kapsama iddia edilir, `covers` TAM der ve pozisyon o tikte kapanırsa kayıt SESSİZ SIFIR
olur.

Ölçülen karşılaştırma (13:00 settlement'i 13:05'te yayımlanıyor, çekim 13:02'de):

| Pencere | 8cb299f | 13cc459 |
|---|---|---|
| Uzun (11:00 → 13:02) | SESSİZ SIFIR | SESSİZ SIFIR |
| Kısa (12:50 → 13:02) | EKSİK (hiç çekmiyordu) | **SESSİZ SIFIR** |

Yani mekanizma ÖNCEDEN VARDI ve bu diff'in ürünü değil; diff'in değiştirdiği şey, kısa
pozisyonların da bu yarışa girebilmesi. Pozisyon açık kalırsa sonraki tur kendini onarır;
sessiz sıfır yalnız kapanış aynı tike denk gelirse oluşur.

**En küçük düzeltme kapsamı (BAŞLATILMADI):** yanıt boşken kapsamayı, gözlenen takvimin o
pencerede settlement öngörmediği durumla sınırla; öngörüyorsa kapsamayı alınan en yeni satırın
ötesine taşıma. Dokunulacak tek yer `funding_rates.refresh`.

**Karar kullanıcıya bırakıldı. Dağıtım YAPILMADI.**

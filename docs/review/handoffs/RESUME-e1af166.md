# DEVAM NOKTASI — sabit on coinlik giris evreni

Bilgisayar kapansa bile devam noktası buradan okunur. Önceki tur: `RESUME-6247675.md`.

## Aday sürüm

| Alan | Değer |
|---|---|
| Dal | `feature/ten-coin-focus-v1` (origin'de) |
| Paket SHA | `e1af16624a94d441247429fe800779798f5ba2aa` (`e1af166`) |
| Taban | `1c4cba1b49307c10a53f0c25a62eccb229b3da1f` — **VPS'te şu an çalışan sürüm** |
| Çalışma ağacı | `C:\Users\berke\wt-ten` |
| VPS'te çalışan | `1c4cba1` — **DEĞİŞMEDİ, dağıtım YAPILMADI** |

**Neden `6247675` değil:** o hattaki funding tamamlanma sürümü, "300 sn geçti → settlement
yok" varsayımına bağlı bir güvenlik zarfı taşıyor ve kullanıcı talimatı bu paketi sırf yeni
özellik taşımak için dağıtmamayı söylüyor. Bu sürüm VPS'te çalışan `1c4cba1` üzerine kuruldu;
funding varsayımına yeni bir bağımlılık EKLEMEZ.

## Paket

```
tb-e1af166.bundle       a1b1e4c5d0de14d235d346b53218aa6beef157826a02d9eaeaab78351eb24a71
tb-deploy-e1af166.sh    ceadcec937841e0a19f832671d9a8f9dbcc9f6fb4b6f8f271fea51534bc87749
```

Bundle `e1af166` içerir, tabanı `1c4cba1`. **Bundle sadece `verify` ile değil, GERÇEKTEN
uygulanarak doğrulandı**: `1c4cba1`'deki bir depoya fetch edildi, ff-only merge yapıldı ve
sonuç ağacının hash'i kaynak çalışma ağacıyla BİREBİR aynı çıktı
(`f6ee781cc6a4a61c75d11d02dac291f03588b9f8`).
Betik LF satır sonlu, 16531 bayt, `bash -n` temiz. Eski paketler `superseded/` altındadır.

VPS'te:

```
scp tb-e1af166.bundle tb-deploy-e1af166.sh ubuntu@<VPS_HOST>:~/
bash ~/tb-deploy-e1af166.sh
```

Çıkış kodu 0 ve son satırda `DEPLOY_OK` görünmelidir.
**Çalıştırılmadı; kullanıcı onayı olmadan çalıştırılmamalı.**

Betikteki 30 değişmez ve 13 config sözleşmesi kontrolü YERELDE bu kod üzerinde çalıştırıldı
ve hepsi geçti — betik doğrulanmamış iddia taşımıyor.

## VPS erişimi — YOK

`~/.ssh/trading2_ovh` parola korumalı (`aes256-ctr`/`bcrypt`). Bu oturumdan
`ssh -o BatchMode=yes` → `Permission denied (publickey)`. VPS'in sürümü, sağlığı ve açık
pozisyonları bu oturumda **doğrulanmadı**. Yukarıdaki "VPS'te çalışan `1c4cba1`" bilgisi
önceki oturum kaydından gelir, ölçüm değildir.

## Bu turda ne yapıldı

### 1. Sabit giriş evreni (talimat §C)

Yeni USDⓈ-M girişi yalnız on sembolde açılabilir: BTC, ETH, SOL, BNB, XRP, LINK, DOGE,
AVAX, LTC, AAVE (hepsi `/USDT`). Onunun da sözleşme kimliği Binance USDⓈ-M `exchangeInfo`
üzerinden doğrulandı: `status=TRADING`, `contractType=PERPETUAL`, `quoteAsset=USDT`.

* `tradingbot/entry_universe.py` — üyelik kuralının TEK kaynağı.
* `SYMBOL_NOT_IN_ENTRY_UNIVERSE` — `decision_gates` içinde HARD_SAFETY olarak kayıtlı.
* Kapı **yalnız giriş** yolundadır. `exit_check` ve `ledger2.tick` ona hiç uğramaz; kaynak
  sözleşmesi testiyle sabitlendi. Evren daralması pozisyon KAPATMAZ.
* Tur kapsamı = evren ∪ açık pozisyonlar. Evren dışı açık pozisyon kapsamda KALIR.
* Fail-closed: `enabled=true` + boş liste `ConfigError`. `allow_long` ve `allow_short`
  birlikte false olamaz.

### 2. Veri kimliği onarımı — bulunmuş gerçek kusur

`core_set` muafiyeti `cfg.coins` içindeki sembollerin karar çerçevelerini TradingView
`BINANCE:<SYM>` akışından, yani **SPOT mumlarından** aldırıyordu. On coin SPOT mumlarıyla
analiz edilip PERP sözleşmede işlem görüyordu.

Artık evren sembolleri perpetual kaynaktan istenir. Perpetual çerçeve yoksa spot ile
sessizce TAMAMLANMAZ: analiz sürer (açık pozisyonun bağlamı ve panel için gerekir), yeni
giriş `DATA_INVALID` + `FUTURES_FRAMES_UNAVAILABLE` ile kapanır. Provenans her turda
`state/frame_provenance.json`a yazılır ve karar kaydına gömülür.

Gerçek turla doğrulandı: **10/10 sembol perpetual çerçeveyle analiz edildi.**

### 3. On coinlik analiz ekranı (teslimat 1)

`/universe` — karar, yön, rejim, kurulum, giriş tetiği, fikri geçersiz kılan koşul, stop,
hedefler, maliyet sonrası beklenen R, kalibre olasılık, veri kimliği, açılmama nedeni.
Sayfa hiçbir şey türetmez; üç dosyayı okur. Evren dışı açık pozisyon EVREN DIŞI etiketiyle
listede kalır.

### 4. Haber/olay kaydı (talimat §G)

`tradingbot/market/news.py` + `venue_events.py`.

* Yayın zamanı ve sisteme ulaşma zamanı AYRI alanlar; yayın zamanı bilinmiyorsa boş kalır
  ve gecikme "ölçülemedi" olur.
* Üç durum ayrı: doğrulanmış / söylenti / veri yok.
* Doğrulanabilir kaynak borsanın kendi ucu: `exchangeInfo` + `fundingInfo` görüntü farkı →
  sembolün TRADING olmaktan çıkması, min-notional/adım/tick değişimi, funding aralığı ya da
  tavanı değişimi. Tur başına iki istek, ağırlık 1.
* İlk çalıştırma taban yazar, olay üretmez. Sağlayıcı hatası görüntüyü korur.
* `for_backtest()` her zaman boş döner — geçmişe haber taşınamaz (imza gereği).
* `news_catalyst` uzmanının bias ve güveni DAİMA 0; karar paketi `usable=False`.
* Toplama TUR yolundadır, `exit_check` içinde DEĞİL: kapanışlar ağ beklemez.

### 5. Teslim raporu

`python -m tradingbot universe-report` → sözleşme kuralları, zaman dilimi bazında kapsama,
karar dağılımı ve açılmama gerekçeleri, öğrenme sayıları, provenans, kaynak tüketimi.
Çıkış kodu bir kalite kapısıdır.

### 6. Tarayıcı kapatıldı

Tarayıcı sembol başına iki REST isteği yapar; 160 sembolde tarama başına 320 istek, 15 dk
kadans + `--scan-every 2` ile günde ~15.360 istek. `scanner_feeds_entries: false` olduğundan
ürünü hiçbir girişe dönüşmüyordu. Ölçüm: açıkken de kapalıyken de aynı tur çıktısı.
Geri açmak tek satır: `scanner.enabled: true`.

## Doğrulama

| Ölçüm | Sonuç |
|---|---|
| Yerel tam paket | **2183 geçti, 22 atlandı, 0 başarısız** |
| Yeni test | 151 (evren 33, panel 17, haber 36, rapor 15, replay funding 14, denetim kimliği 7, pattern önbelleği 7, güncellik 8, otomatik yenileme 18) |
| `ruff check .` + `compileall` | temiz |
| CI (`e1af166`) | **üç iş de başarılı**: learning-tests, deploy-tests, quant-evaluation-tests |
| Gerçek PAPER turu | 10/10 sembol analiz edildi |
| Tarihsel veri | **40/50 seri, 2.942.914 satır**; `history-validate` 60/60 seri geçerli, 0 hatalı |
| Karar kaydı denetim kimliği | code_sha 10/10, config_hash 10/10 (onarımdan önce 0/30) |
| Karar çerçevesi kaynağı | 10/10 PERPETUAL |
| Sözleşme doğrulaması | 10/10 |
| Açılan pozisyon | 0 — zorla işlem AÇILMADI |

## OTOMATİK YENİLEME (2026-09-11 eklendi)

Arşiv ve benzerlik indeksi artık **aynı süreçte, restart olmadan** güncel kalır.

| Alan | Değer |
|---|---|
| Çalışma sıklığı | 15 dk (arka plan iş parçacığı) |
| İndeksi tetikleyen seriler | yalnız `4h` |
| Arşivin güncellendiği seriler | `4h`, `1h`, `1d` |
| Tur başına istek tavanı | 24 |
| İndeks sembol tavanı (BELLEK) | 16 |
| Canlı ölçüm — tur 1 | 36,0 sn (indeks yok, tur BEKLEMEDİ) |
| Canlı ölçüm — yayım | sürüm 1, 138.907 olay, 10 seri |
| Canlı ölçüm — arşiv | +88 satır, 24 istek, 0 hata |

`state/history_refresh.json` her turda yazılır: sıklık, son başarılı yenileme, son hata,
indeks sürümü/olay/seri ve en yeni bar.

**Neden arka planda:** `watch` döngüsü TEK iş parçacıklıdır ve `exit_check` yalnız turlar
ARASINDA çalışır. Tur içine konan 40 sn'lik yeniden kurulum stop/TP'yi 40 sn geciktirirdi.

**Neden yalnız 4h tetikler:** indeks `_build_pattern_index` içinde yalnız 4h serisinden
kurulur. Her saat kapanan bir 1h barı yüzünden yeniden kurmak 40 sn CPU ve ardından soğuk
önbellekle ~250 sn'lik bir tur ederdi; indekste tek bir olay değiştirmezdi.

**Bayatlık ve toparlanma:** ağ hatasında arşive DOKUNULMAZ, eski indeks KORUNUR, hata
`last_error`da görünür. Kaynak dönünce aynı süreç elle müdahale olmadan yayımlar
(senaryo testiyle sabit). Boş cevap damgayı İLERLETMEZ.

## HABER KAYNAKLARI — ETKİN ve EKSİK

**Etkin (2):**

* `venue_contract_and_funding` — Binance `exchangeInfo` + `fundingInfo` görüntü farkı.
  Sembolün TRADING olmaktan çıkması, min-notional/adım/tick, funding aralık/tavan değişimi.
* `project_releases` — her coinin KENDİ deposundan GitHub Releases. Anahtarsız,
  doğrulanabilir, gerçek `published_at` + kalıcı bağlantı. On coinin onu da doğrulandı.
  Canlı: 50 olay, 50/50 gecikme ölçülebilir, olay–coin eşleşmesi karar kaydında.

**UYGULANMADI (boş entegrasyon EKLENMEDİ):**

* `macro_calendar` — **bağlı: makro takvim API anahtarı.** Anahtarsız güvenilir uç yok
  (FRED ve ticari sağlayıcılar anahtar ister; federalreserve.gov yalnız HTML).
  Kayıt sözleşmesi hazır; eksik olan yalnızca çekici.
* Borsa listeleme duyuruları, yönetişim oylamaları, genel basın akışı (RSS/JSON).

"Haberler takip ediliyor" cümlesi YALNIZ etkin iki kaynak için kurulabilir. Venue
değişiklikleri bütün haber analizi DEĞİLDİR.

## DEPOLAMA

| Sınıf | Boyut | Kurtarma |
|---|---|---|
| Ham arşiv (`data/history`) | 132,8 MB | yeniden indirmek ~2,5 saat — YEDEKLE |
| Yeniden üretilebilir önbellek (`data/`) | 0,3 MB | kaybı veri kaybı DEĞİL, güvenle silinebilir |
| State (`state/`) | 4,5 MB | YETKİLİ veri, önbellek değil |

Büyüme: ~1.270 satır/gün ≈ 0,1 MB/gün ≈ **0,02 GB/yıl** (10 sembol × 1d/4h/1h/15m;
46 bayt/satır ölçülen sıkıştırılmış ortalama — ölçüm değil hesaptır).

Benzerlik indeksi diske YAZILMAZ; ham arşivden yeniden kurulur, bu yüzden önbelleği
silmek veri kaybettirmez. `data/` `.gitignore`dadır; kod, config ve manifestler ayrı
yönetilir. Manifestler kaynak, tarih ve checksum taşır.

**VPS bağımsızlığı:** ilk kapsama bir kez `history-collect` ile yapılır (bilgisayarda ya
da VPS'te); sonrası worker'ın kendi artımlı yenilemesiyle sürer. Botun çalışması
bilgisayarın açık olmasına BAĞLI DEĞİLDİR.

## GIRISTEN KAPANISA IZLENEBILIR ORNEK (teslimat 4 ve 5)

`historical-replay --symbols SOL/USDT --tf 4h --from 2025-01-01 --to 2025-04-01
--run-id sol-funded`. Artifact: `trading2-artifacts/2026-09-11/replay-sol-funded.json`.

| Ölçüm | Değer |
|---|---|
| Karar | 541 |
| Açılan | 19 · kapanan **18** (tamamı out-of-sample) |
| Ortalama net R | **+0,134** |
| Medyan net R | -1,012 |
| %95 aralık | **[-0,613, +0,880] — SIFIRI İÇERİYOR** |
| Kazanma oranı | %38,9 |
| Profit factor | 1,20 |
| Ücret toplamı | 0,4119 USDT |
| Funding toplamı | -0,0608 USDT (net ödendi) |
| **Funding kapsamı** | **270/270 settlement cevaplandı, 0 bilinmiyor, `complete=true`** |
| Çıkışlar | 11 stop · 5 hedef2 · 2 başa-baş |
| Yön | 12 SHORT · 6 LONG · tamamı `pullback` |
| Determinism hash | `4e52862d1a3edde5` |

**Yorum: bu bir kâr kanıtı DEĞİLDİR.** 18 işlem karar vermeye yetmez ve aralık sıfırı
içeriyor. Medyanın -1,01R olması dağılımın asimetrik olduğunu gösterir: çok sayıda küçük
stop, az sayıda büyük kazanç. Tek sembol, tek kurulum, üç aylık pencere.

Bu tabloyu anlamlı kılan şey funding kapsamıdır: 270 settlement'ın 270'i arşivden
cevaplandı. Onarımdan önce funding hiç sorulmuyordu ve aynı tablo maliyet sonrası gibi
okunabiliyordu.

## ÖLÇÜLEN SINIR — BTC küçük hesapta 2x'te açılamıyor

100 USDT sermaye, %2 risk, 2,5×ATR(4h) stop ile üretim boyutlandırıcısı ve üretim
`quantize_order`ı üzerinde ölçüldü:

| Sembol | stop % | min notional | 2x | 3x | 4x | 5x |
|---|---|---|---|---|---|---|
| BTC/USDT | 2,44 | 50 | **ZERO_QTY** | OK | OK | OK |
| diğer dokuz | 3,2 – 5,6 | 5 – 20 | OK | OK | OK | OK |

Sebep: `max_position_pct=30` → 2x'te azami notional 60 USDT; 60/77.176 = 0,00078 BTC ve
adım 0,001 olduğu için aşağı yuvarlama sıfır verir. Davranış DOĞRUDUR — minimum emri
karşılamak için risk büyütülmez, stop daraltılmaz. Fakat sonucu şudur: **kaldıraç seçici 2x
verdiğinde BTC adayı hiçbir zaman açılmaz.** `historical-replay --symbols BTC/USDT` bunu
üretim yolunda doğruladı: her giriş `STEP_ZERO_QTY` ile reddedildi.

Karar kullanıcıya aittir. Seçenekler: BTC'yi evrende bırakıp bu kısıtı kabul etmek,
sermayeyi artırmak, ya da BTC'yi evrenden çıkarmak. **Risk bütçesi büyütülerek
çözülmemelidir.**

## Tarihsel veri (talimat §D) — TAMAMLANDI

`data/history/futures/<SEMBOL>/<TF>/<YIL>/<AY>.csv.gz`, kaynak Binance resmi arşivi
(data.binance.vision) + fapi REST tamamlama. Zaman dilimleri 1d/4h/1h/15m, listelemeden
bugüne. Manifest başına satır, boşluk, checksum, ilk/son bar UTC tutulur.

**Toplama TAMAMLANDI** (çıkış kodu 0). Yeniden çalıştırmak idempotenttir ve resume eder:

```
python -m tradingbot history-collect --market futures \
  --symbols BTC/USDT ETH/USDT SOL/USDT BNB/USDT XRP/USDT LINK/USDT DOGE/USDT AVAX/USDT LTC/USDT AAVE/USDT \
  --timeframes 1d 4h 1h 15m --max-available
```

Ölçülen sonuç: 40/50 seri (1m istenmedi), **2.942.914 satır**, 1905 boşluk, 137 MB.
`history-validate`: **60 seri, 0 geçersiz** — her manifest checksum'ı diskle uyuşuyor.
Süre: ~2,5 saat (yükü 15m taşıyor).

**VPS'te durum bilinmiyor.** VPS'te Tier A (tüm likit evren 1h/4h/1d) zaten toplanmış
olabilir; `universe-report` orada gerçek kapsamı gösterir.

## AÇIK İŞ

1. **BTC kararı**: yukarıdaki sınır kullanıcıya sunuldu, karar bekliyor.
2. **Dağıtım**: VPS erişimi yok. Paket hazır, kullanıcının tek adımı yukarıda.
3. **VPS tarihsel verisi**: VPS'te Tier A zaten toplanmış olabilir; `universe-report` orada
   gerçek kapsamı gösterir. 15m ve funding serileri eksikse `history-collect` çalıştırılmalı.
4. **1m serisi TOPLANMADI** (talimat "gerektiğinde" diyor). Rapor bunu 10 eksik seri olarak
   gösterir — bu doğru davranıştır, gizlenmiyor.
5. **Doğal işlem**: yerel PAPER turlarında henüz giriş açılmadı. Gerekçeler ölçüldü:
   `NO_TRADE_LOW_CONSENSUS=5`, `RESEARCH_SIZE_ONLY=3`, `LEVERAGE_GATE_BLOCKED=1`
   (BNB, `CONFIDENCE_BELOW_BASE`), `NO_TRADE_RED_TEAM_VETO=1`. Zorla işlem AÇILMADI.
6. **Girişten kapanışa örnek**: `historical-replay --symbols SOL/USDT --from 2025-01-01
   --to 2025-06-30 --run-id demo-sol-h1` başlatıldı, sonucu bu dosyada YOK.

## KAYNAK TÜKETİMİ — ÖLÇÜLDÜ (teslimat 7)

Tek süreçte üç ardışık tur, on sembol, tarayıcı kapalı:

| Bileşen | Süre | Sıklık |
|---|---|---|
| Motor kurulumu | 0,1 sn | süreç başına |
| Pattern indeksi kurulumu (138.891 olay, 10 seri) | 39,9 sn | süreç başına |
| Perpetual mum çekimi (10 sembol x 3 tf) | 10,0 sn | tur başına |
| Pattern kanıtı sorguları (10 sembol x 2 yön) | **251,5 sn → ~0** | tur başına |

**Onarımdan önce:** tur 1 = 340 sn, tur 2 = 286 sn, tur 3 = 286 sn. Bu bir ısınma maliyeti
DEĞİLDİ; her turda tekrar ediyordu.

**Onarımdan sonra:** tur 1 = 343 sn, tur 2 = **18,1 sn**, tur 3 = **13,8 sn**.
15 dakikalık worker aralığında meşguliyet %32'den ~%1,5'e indi.

Neden israftı: `SimilarPatternEngine` mum tablosunu `_load_pattern_engine` içinde bir kez
kurar ve tur içinde güncellemez. İki ardışık sorgu birebir aynı sonucu döndürdü — motor
aynı cevabı tur başına 20 kez hesaplıyordu.

**VPS için önemi:** üretim indeksi yereldekinden yaklaşık dört kat büyüktür (~578k olay).
Onarım olmadan tur süresi 15 dakikalık aralığı aşar ve turlar üst üste binmeye başlardı.
Bu, benim eklediğim bir gerileme DEĞİL, ölçülmemiş mevcut bir üretim özelliğiydi.

Diğer ölçümler: state 1,9 MB · tarihsel veri 137 MB · istek/tur 42 (hesap, ölçüm değil) ·
15 dk kadansta 4.032 istek/gün · LLM `noop` sağlayıcı, 0 çağrı.

## Bilinen sınırlar (kapsam dışı, dürüstlük için)

* Genel basın/makro haber akışı için hazır uç nokta YOK. Doğrulanmamış bir kaynağı varsayılan
  yapmadım; operatör eklerse kayıt sözleşmesi hazırdır.
* Tarihsel haber arşivi yok → backtest haber bağlamı OLMADAN çalışır.
* `perp_frames` ccxt `binanceusdm` üzerinden gider ve projenin `RateBudget` havuzunu
  KULLANMAZ. On sembolde tur başına 30 istek; ccxt'nin kendi limitleyicisine bağlıdır.
* Venue olaylarında `published_at` boştur: borsa değişikliği YAPTIĞI anı yayımlamıyor;
  yalnız GÖRDÜĞÜMÜZ an biliniyor.
* Karar çerçeveleri 1d/4h/1h'tir. 15m ve 1m TOPLANIR ama karar formülüne GİRMEZ — katkısı
  gösterilmeden eklenmedi (talimat §E).
* Tarayıcı kapalıyken `state/universe_eval.json` tazelenmez ve tier-A tarama kaydı üretilmez.

## Geri alma

```
bash /opt/tradingbot/app/deploy/rollback.sh     # -> 1c4cba1 ; state'e DOKUNMAZ
```

Kod değiştirmeden evreni kapatmak: `config.yaml → entry_universe.enabled: false`.
Eski davranış geri gelir, açık pozisyonlara yine dokunulmaz.

## PAKET DOĞRULAMA KAYDI (2026-09-11, teslim anı)

Aşağıdakiler paket dosyaları üzerinde YENİDEN ölçüldü, önceki turdan taşınmadı:

| Kontrol | Sonuç |
|---|---|
| Çalışma ağacı HEAD | `e1af16624a94d441247429fe800779798f5ba2aa` |
| Çalışma ağacı kirli mi | hayır (0 değişiklik) |
| origin ile fark | 0 ileri / 0 geri |
| Betik `TARGET_SHA` == HEAD | EVET |
| Betik `BUNDLE_SHA` == bundle'ın gerçek sha256 | EVET |
| Betik `BUNDLE` adı == paketteki dosya | EVET (`tb-e1af166.bundle`) |
| Betik `BASE_SHA` | `1c4cba1` (VPS'te çalışan sürüm) |
| `git bundle verify` | okay |
| Bundle UYGULANARAK doğrulandı | `1c4cba1` → ff-only → ağaç `f6ee781c…` kaynakla BİREBİR |
| `bash -n` | temiz |
| Betiğin 30 değişmezi + 13 config sözleşmesi | yerelde 30/30 ve 13/13 geçti |

Tam yollar:

```
C:\Users\berke\trading2-deploy\tb-e1af166.bundle
C:\Users\berke\trading2-deploy\tb-deploy-e1af166.sh
C:\Users\berke\trading2-deploy\RESUME-e1af166.md
```

## SON DURUM

* Yerel tam paket: **2183 geçti, 22 atlandı, 0 başarısız**.
* CI (`e1af166`): **üç iş de başarılı** — learning-tests, deploy-tests, quant-evaluation-tests.
* `ruff check .` ve `compileall`: temiz.
* **Dağıtım YAPILMADI.** SSH anahtarı parola korumalı olduğu için VPS'e bağlanılamadı;
  VPS'in mevcut sürümü ve sağlığı bu oturumda DOĞRULANAMADI. "VPS'te hiçbir şey çalışmıyor"
  denemez — yalnız bu sürümün oraya dağıtılmadığı ve oradaki durumun doğrulanmadığı bilinir.

## EKSİK KALANLAR

1. **Dağıtım** — paket hazır ve doğrulandı; çalıştırmak kullanıcının tek adımı.
2. **Makro takvim** — UYGULANMADI, makro takvim API anahtarına bağlı. Boş entegrasyon eklenmedi.
3. **Borsa listeleme duyuruları / yönetişim oylamaları / genel basın akışı** — uygulanmadı.
4. **BTC 2x'te açılamaz** — kullanıcı kabul etti; sermaye ve risk bütçesi DEĞİŞTİRİLMEDİ.
5. **1m serisi** — toplanmadı (talimat "gerektiğinde" diyor); rapor bunu eksik olarak gösterir.

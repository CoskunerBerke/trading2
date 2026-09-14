# KAPANIŞ — ne geçerli, ne geçersiz, ne bilinmiyor

Bu belge çalışmayı kapatır. Yeni hipotez, ızgara ya da filtre çalışması yoktur.

## 1. Sürüm, protokol ve sonuç dosyaları

| alan | değer |
|---|---|
| araştırma dalı (son) | `dfd6a39048977755fec2daa1bc63e50b20f088ca` · `work/entry-research-v1` · temiz |
| üretim yama dalı | `4e0b6ff5b27372072d1c97206ea4b14c581cbc09` · `work/prod-fixes-on-e1af166` · temiz |
| taban (VPS'te doğrulanan) | `e1af16624a94d441247429fe800779798f5ba2aa` |
| kullanılan protokol | `PROTOCOL_V2.md` — sha256 `2e162d32bd96729d…` |
| tam yerel paket | 2221 geçti, 22 atlandı, 0 başarısız · `ruff` temiz |

### Geçerli sonuçlar (gerçek borsa kurallarıyla)

23 koşu, hepsi `research/entry_v1/state/replay/` altında: `base_gated`, `base_ungated`,
`dev_base_ungated`, `eval_base_ungated`, 18 `dev_h*` yapılandırması, `eval_h1_r40_c0_2`.
Metrikler `out/meta_<id>.json` ve `out/DENEY_V2.md` içinde.

### GEÇERSİZ sonuçlar (varsayılan borsa kuralları) — silinmedi, ayrıldı

`out/stale_default_filters/` altında 15 meta dosyası. Bunlar `symbol_filters.json`
bağlanmadan önce koştu ve **alıntılanmamalıdır**. Kapsam:

* bu turun ilk H1 ızgarası (6 yapılandırma) ve ilk referansları,
* **önceki turun tüm replay sonuçları** (`research/pfdiag_v1/`), yani `−0,1898R`,
  `k = 0,75` karşılaştırması ve ona dayanan her sayı.

Ayakta kalan tek şey `k = 0,75` adayının **reddi**; sayısal gerekçesi yeniden ölçülmedi.

## 2. Kapılı üretim tabanı — düzeltilmiş kurallarla

Koşu `base_gated`, determinizm hash `f98ca4372154f38d`, funding kapsamı 11/11, bilinmeyen 0.

| ölçüt | değer |
|---|---|
| pencere | 2024-09-01 → 2026-09-01 |
| karar | 43.810 · aktionable 40.706 · **açılan 3** |
| hesap getirisi | **−1,97%** |
| ortalama net R | −1,0266 (üçü de zarar) |
| maksimum düşüş | 1,97% |
| maruziyet | günlerin %0,8'i |
| ret: RESEARCH_SIZE_ONLY | 31.007 |
| ret: NEGATIVE_NET_EDGE | 9.678 |

**"24 ayda 3 işlem" hâlâ geçerli.** Düzeltilmiş kurallarla da 3 işlem açılıyor; değişen
yalnız getiri (−2,89% → −1,97%).

Üç işlemin tamamı ilk on gündedir ve mekanizma kayıttan okunuyor:

| işlem | tarih | giriş `p_win` | sonuç |
|---|---|---|---|
| AAVE LONG | 2024-09-01 → 09-02 | 0,500 | −1,016R |
| XRP LONG | 2024-09-03 → 09-04 | 0,455 | −1,037R |
| DOGE LONG | 2024-09-10 → 09-11 | 0,417 | −1,027R |

Sonrasında 23,5 ay boyunca hiç işlem yok.

### Kullanılan olasılık modeli ve öğrenme durumu

* Kaynak: **hiyerarşik Beta önseli** (`p_win_source = hierarchical_prior`). Şampiyon
  sınıflandırıcı hazır olmadığı için `learner2.predict` kalibre değer üretmiyor.
* Platt kalibratörü: `n_fit = 0`, `a = 1`, `b = 0` — yani **fit edilmemiş**, birebir eşleme.
* Koşu sonunda öğrenme durumu: `n_closed = 3`, kök düğüm `{n: 3, s: 0}`, `lessons = 3`.
* Mekanizma: önsel `(s + α·prior) / (n + α)` ile küçülür. Üç kayıptan sonra
  `(0 + 10×0,5) / (3 + 10) = 0,385` olur; bu, plan geometrisinin başabaş eşiğinin altındadır
  ve kapı kalan pencerede her adayı reddeder. **Kendi kendini kilitleyen bir durum:**
  işlem açmadığı için öğrenemiyor, öğrenemediği için işlem açmıyor.

### Üretimden farklı kalan parçalar

| parça | üretim | bu koşu |
|---|---|---|
| ekonomi kapısı | var | **var** (bu turda bağlandı) |
| öğrenilmiş `p_win` | `0,5×prior + 0,5×legacy` harmanı | **saf prior** (replay'de legacy tahminci yok) |
| uzman ajanlar | 20 | **10** (`legacy_brief` verilmiyor: momentum, candles, levels, volume, analog yok) |
| benzerlik/pattern kanıtı | var | yok (`--no-patterns`) |
| giriş seçicilik / MTF | var | yok |
| haber bağlamı | var | yok |
| `require_verified_precision` | `true` | `false` (replay'de borsadan çekilmiş filtre doğrulaması yok) |

Bu koşu **üretimin tamamı değildir**; "kapılı taban" diye okunmalıdır.

## 3. Pencereler — kesin tarihler

| pencere | tarih (UTC) | daha önce kullanıldı mı | bu turdaki rolü |
|---|---|---|---|
| DEĞERLENDİRME | 2020-11-01 → 2022-08-31 | **hayır** | dondurulmuş adayın tek koşusu + temel |
| GELİŞTİRME | 2022-09-01 → 2026-09-01 | **evet** (önceki turda hem eğitim hem test olarak) | 18 yapılandırmanın tamamı, aday seçimi |
| test alt penceresi | 2024-09-01 → 2026-09-01 | **evet** (önceki turun "test"i) | yalnız referans raporlaması |

Ayrı bir "eğitim" penceresi YOKTUR. Koşu içi walk-forward
(`train 180 / test 30 / purge 6 / embargo 6`) her koşunun İÇİNDE çalışır; pencere ayrımı
değildir.

**2020-2022 kontrolü ileri dönem doğrulaması DEĞİLDİR.** Kural sonraki yıllarda
(2022-2026) seçildi ve daha ERKEN bir dönemde sınandı. Zaman sıralaması terstir; bu bir
geriye dönük kontroldür ve ileri dönem doğrulaması yerine geçmez.

### 2025-2026 kapsamı

Arşiv 2026-09-11'e kadar dolu (LTC ve AAVE 2026-09-10 16:00). Yani veri var. Fakat
geliştirme temelinde **2025'te 8, 2026'da 8 kapanış** var — çünkü sermaye 100'den 7,7'ye
düştüğü için pozisyon boyutu ve kapasite çöktü. Bu iki yıl geliştirme penceresinin parçası
olarak zaten görüldü; bakir değildir ve 16 işlemle tek başına yorumlanamaz.
Bu aşamada bu veride yeni optimizasyon yapılmadı.

## 4. Borsa filtreleri — senaryo varsayımı

`data/symbol_filters.json`, `source = binance_api`, **`verified_at = 2026-09-10T21:34:11Z`**.

Bu **bugünkü** kural kümesidir ve 2020-2026'nın tamamına uygulandı. Binance tick/step/
minimum değerlerini zaman içinde değiştirir; geçmiş tarihlerdeki gerçek kuralların bunlar
olduğu **doğrulanmadı ve iddia edilmiyor**.

Doğru etiket: **"bugünkü borsa kurallarıyla senaryo"**. Önceki durum (her sembolde
tick 0,01 / step 0,001 / minimum 5) buna göre çok daha kötüydü çünkü hiçbir sembolün
gerçeğine karşılık gelmiyordu; ama yeni durum da tarihsel kuralların doğrulanması değildir.

## 5. H3 −%46,27 varken H1 −%60,57 neden seçildi

Önceden yazılmış kural (`PROTOCOL_V2.md` §4):

> 1. Geliştirmede net hesap getirisi en yüksek yapılandırma seçilir.
> 2. Asgari şart: en az 60 kapanmış işlem, en az 6 farklı coin, en az 12 farklı takvim ayı.
> 3. Dayanıklılık şartı: aynı ailedeki 6 komşunun en az 4'ünde net hesap getirisi temel
>    koşudan yüksek olmalı.

H3 ailesinin tamamı:

| yapılandırma | getiri | n | asgari şart |
|---|---|---|---|
| `h3_p0.15_r30` | **−21,20%** | 39 | **KALDI** (n < 60) |
| `h3_p0.25_r30` | −21,71% | 44 | KALDI |
| `h3_p0.2_r30` | −23,91% | 41 | KALDI |
| `h3_p0.25_r35` | **−46,27%** | 111 | GEÇTİ |
| `h3_p0.2_r35` | −52,25% | 104 | GEÇTİ |
| `h3_p0.15_r35` | −54,55% | 96 | GEÇTİ |

**Uygulanan okuma:** "en yüksek getirili yapılandırma" önce seçilir, sonra asgari şarta
bakılır. H3'ün en yüksek getirilisi −%21,20 ve n = 39 → şart 2 düştü → aile elendi.
Geriye H1 (−%60,57) ve H2 (−%68,48) kaldı, H1 daha yüksek.

**Kural bu noktada BELİRSİZ ve bunu sonradan gerekçelendirmiyorum.** İkinci bir okuma da
mümkündü: "asgari şartı sağlayanlar arasından en yüksek getirili". O okumada H3'ün seçilmiş
yapılandırması `h3_p0.25_r35` (−%46,27, n = 111) olurdu ve **H1'i geçerdi; aday H3
olurdu.** Hangi okumanın doğru olduğu protokolde yazmıyor; uygulama (`make_deney_v2.py`)
birinci okumayı seçti ve bu seçim sonuçlardan önce koda yazılmıştı, ama metinde
belirtilmemişti.

Etkisi sınırlıdır — her iki aday da geliştirmede ağır zararda ve işlem başına ölçülebilir
iyileşme üretmiyor (H1 +0,047R, H3 −0,065R; iki aralık da sıfırı içeriyor) — fakat
**seçim keyfîdir ve bir sonraki protokolde tek cümleyle giderilmelidir.**

## 6. İddiaların kanıt kapsamına indirilmesi

### "Avantaj vardı ve eridi"

**Kanıtın söylediği:** filtresiz aday popülasyonunun işlem başına net R'si takvim yılına
göre 2021'de +0,124 (n = 281, PF 1,23), 2022'de −0,011 (n = 263), 2023'te −0,154 (n = 103),
2024'te −0,219 (n = 61). 2020-11 → 2022-08 penceresinde toplam +%91,1.

**Kanıtın söylemediği:** yıllık sonuçlar bir **mekanizma** kanıtlamaz. Aynı örüntüyü
şunlar da üretebilir ve elimdeki veriyle ayıramıyorum:

* **Hayatta kalma yanlılığı:** on coin 2026'da seçildi; 2021'de hepsi hayatta kalan
  kazananlardı. LONG tarafının 2021'de +0,241R vermesi bununla da açıklanabilir.
* **Bileşiklenme ve kapasite:** sermaye eridikçe pozisyon boyutu ve eş zamanlı pozisyon
  kapasitesi düşer, işlem popülasyonu değişir. 2025-2026'da yalnız 8'er kapanış var.
* **Değişen kural kümesi:** bugünkü filtrelerin geçmişe uygulanması (bkz. §4).
* **Rejim vs. kalabalıklaşma:** "trend zayıfladı" ile "kurulum kalabalıklaştı" aynı yıllık
  tabloyu üretir.

**Savunulabilir ifade:** *bu kurulumun ölçülen performansı 2021'de pozitif, sonraki
yıllarda monoton biçimde negatife döndü; nedeni bu veriyle belirlenmedi.*

### "Sorun girişte değil"

**Kanıtın söylediği:** denenen üç giriş kuralının hiçbiri işlem başına ölçülebilir
iyileşme üretmedi (+0,047R / +0,037R / −0,065R, üç %95 aralığı da sıfırı içeriyor).

**Kanıtın söylemediği:** bu, "giriş kalitesi sorun değil" demek DEĞİLDİR. Yalnız
**bu üç kuralın, bu 18 parametreyle, bu ölçüm ortamında** yardımcı olmadığını söyler.
Ölçüm ortamının sınırı ayrıca büyüktür: replay'de 20 değil **10** uzman çalışıyor ve
momentum/candles/levels/volume ajanları yok. Yani üretimin giriş sinyalinin tamamı
sınanmadı.

**Savunulabilir ifade:** *önceden yazılmış üç giriş kuralı, mevcut çıkış geometrisi ve
maliyet modeli altında ölçülebilir iyileşme sağlamadı.*

### Düşük maruziyetli adayın risk kullanımı

Değerlendirme penceresinde (2020-11 → 2022-08):

| ölçüt | temel (kapısız) | aday `h1_r40_c0.2` |
|---|---|---|
| işlem | 532 | 82 |
| hesap getirisi | +91,10% | +6,67% |
| **maksimum düşüş** | **22,03%** | **25,85%** |
| en düşük özkaynak | 91,22 | 88,04 |
| maruziyet (gün) | %99,9 | %52,6 |
| ortalama eş zamanlı pozisyon | 4,38 | 0,76 |
| **en yüksek eş zamanlı pozisyon** | 8 | 6 |
| en büyük tek kayıp | −5,14 USDT | −3,62 USDT |
| en uzun kayıp serisi | 12 | 6 |
| maliyet ×2 altında getiri | +72,97% | +3,78% |

**Okunuşu:** aday maruziyeti yarıya, ortalama eş zamanlı pozisyonu altıda bire indirdi ve
getirinin %93'ünü kaybetti — üstelik **maksimum düşüşü daha yüksek** (25,85% > 22,03%) ve
en düşük özkaynağı daha düşük. Yani azaltılan maruziyet risk azalması satın almadı.
Filtre, kazandıran işlemleri de eliyor.

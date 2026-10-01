# PANEL KULLANIMI (2026-09-16) — işlem terminali görünümü

Panel **salt okunurdur**: hiçbir sayfa karar vermez, emir açmaz, state dosyasına yazmaz. Her sayı yetkili kayıttan
okunur; bilinmeyen değer **"Veri yok"** yazar, asla 0 sayılmaz.

## Gezinme (dört bölüm)

| bölüm | ne gösterir |
|---|---|
| **Genel bakış** (`/`) | Seçili hesabın özeti, büyük grafik, **açık işlemler**, son kapanışlar |
| **İşlemler** (`/trades`) | Açık / Kapanan sekmeleri, coin arama, hesap ve piyasa filtresi |
| **Tarayıcı** (`/patterns`) | Mum trader: keşfedilen coinler, yeni listelenenler, formasyonlar, koşullu planlar, ekonomik rapor |
| **Gelişmiş** (menü) | Futures/Spot/Trend defterleri, Evren, Eski tarayıcı, Emirler, Risk, Öğrenme, Quant, Backtest, Modeller, LLM, Sağlık |

Eski derin bağlantılar (`/coin/BTC`, `/portfolio/futures`, `/universe`, `/quant`, …) aynen çalışır.

## Hesap seçimi

Üst çubuktaki **Hesap** kutusu gerçekten var olan defterleri listeler: *Ana bot*, *T2 · EMA200 trend*,
*M2 · 28g momentum*, *Mum trader*. Bir defter kayıt yazmadıysa listede **görünmez** (çalışıyormuş gibi gösterilmez).

Her hesap **kendi sanal sermayesiyle** ayrı okunur. Özkaynaklar toplanmaz: 100 USDT'lik üç ayrı deney defteri tek
portföy gibi birleştirilmez.

Dört özet kartı:
* **Özkaynak** — defterin güncel değeri (alt satırda başlangıç sermayesi).
* **Net sonuç** — gerçekleşen + açık; alt satırda ikisi ayrı. Bileşenlerden biri bilinmiyorsa "Veri yok".
* **Açık işlem** — LONG/SHORT ayrımıyla.
* **Kapanan işlem** — kazanan/kaybeden ayrımıyla (defterde net sonuç varsa).

## Açık işlemler ve grafik

Sağ sütundaki her işlem kartı: coin, yön, giriş, güncel fiyat, stop, hedef ve **açık K/Z (brüt)**. Miktar, kaldıraç,
açılış zamanı, ücret, funding ve fiyat kaynağı "ayrıntı" altındadır. Hedefi olmayan strateji için **hedef uydurulmaz**:
"Sabit hedef yok — strateji çıkışı" yazar.

Bir işleme **tıklayınca** (ya da klavyeyle Enter) aynı ekranda o coinin doğru hesap/piyasa grafiği açılır, satır
vurgulanır ve grafiğin altındaki **işlem/plan özeti** o coine geçer. Sayfa yenilenmez, dilim/katman seçimin korunur.

Grafik varsayılanı sadedir: **mumlar + gerçek işlem katmanı** (giriş, çıkış, stop, hedef). Seviyeler, bölgeler, trend
çizgileri, formasyonlar, hacim ve göstergeler *"Katmanlar ve göstergeler"* altında açılır. Dilimler: 15m / 1h / 4h / 1g
(+ 1w). "Geçmiş" kutusu motorun o andaki analiz kaydını açar; o an bilinmeyen bilgi geçmişe çizilmez.

Arşivde mum yoksa grafik sessizce boş kalmaz: **"mum verisi yok"** gerekçesi yazılır.

## İşlemler sayfası

`Açık` sekmesi kart listesi, `Kapanan` sekmesi tablo (coin, yön, giriş, çıkış, açılış, kapanış, net USDT, neden).
Çıkış nedenleri Türkçedir: Stop, Başa baş stop, Hedef 1/2, Likidasyon, Kısmi kapanış, Zaman stopu, Strateji çıkışı.
Kısmi kapanışlar tam kapanış gibi **çoğaltılmaz**; her satır defterdeki bir kapanmış işlem kaydıdır ve sayım defterle
uzlaşır (sayfa altında yazılır). Ana bot için ayrıntılı eski tablo açılır bölümde durur.

## Tarayıcı (mum trader)

Sırasıyla: keşfedilen / işleme uygun / yeni listelenen / taranan-taranmayan sayıları; yeni listelenen sözleşmeler
(**futures ilk işlem zamanı** ve yaşı — tokenin çıkışı ya da spot listelenmesi ile karıştırılmaz); koşullu planlar
(coin, yön, aile, dilim, durum, kohort, tetik/stop/hedef, maliyet sonrası ödül/risk, not); formasyon bulgularının
durumu; **dilim başına kapalı bar sayısı** (yetersiz dilim açıkça görünür); ekonomik değerlendirme.

Plan durumları: *Teyit/tetik bekliyor*, *Giriş şartı oluştu*, *Pozisyon açık*, *İşlem açılamadı*, *Yapı bozuldu*,
*Süresi doldu*, *İptal*. Ret gerekçeleri okunur ("Toplam risk sınırı dolu"); ham kod ayrıntıda kalır.

## Canlı güncelleme ve güncellik

Kartlar, açık işlemler ve son kapanışlar sayfa yenilemeden güncellenir (SSE olayı + 20 sn yoklama). Grafik sıfırlanmaz
ve seçimin kaybolmaz. Bağlantı koparsa **son doğrulanmış içerik ekranda kalır** ve üst çubukta uyarı belirir; boş
pozisyon listesi gibi görünmez.

Üst çubuk fiyat / strateji turu / kalp atışı yaşlarını **ayrı ayrı** gösterir — bunlar farklı kavramlardır. Fiyat
bayatsa kırmızı "fiyat bayat" rozeti çıkar ve o fiyatla taze K/Z üretilmez.

## Sınırlar

* Panel yatırım tavsiyesi değildir ve **PAPER** modu gösterir; gerçek para yoktur.
* Mum trader'ın `p_win` değeri **ölçülmemiştir**; raporda örneklem yetersizken hüküm "BELİRSİZ" yazar.
* Geometrik ödül/risk beklenen kazanç değildir.

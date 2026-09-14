# ÖN KAYITLI PROTOKOL V8 — mum formasyonu GİRİŞ SİNYALİ olarak ("görünce gir")

**Yazıldığı an:** 2026-09-13, **hiçbir V8 hesabı görülmeden.**
**Kaynak:** kullanıcının Türkçe mum tablosu ve "hepsini dahil edelim, o on coinde görünce girip
deneyelim" talebi. **Kod:** `5f9d652` + `candle_context.py` → candle_v1.2.0 (kayıtsız).

## 1. Soru

Formasyonu **giriş sinyali** olarak kullanan tek kurallı bir sistem (uzman yok, kapı yok:
boğa tarafi formasyon → LONG, ayı tarafi → SHORT), botun maliyet varsayımlarıyla üç pencerede
kâr eder mi? Bugüne kadar formasyon yalnız giriş ONAYI (V4) olarak ölçüldü; sinyal olarak hiç
ölçülmedi. Bu boşluk gerçektir ve bu deney onu kapatır.

## 2. Ne eklendi

candle_v1.2.0: belden tutma ×2, kros hamile ×2, güvercin yuvası, inen şahin, değen mumlar ×2,
tepen (kicker) ×2, doji yıldız ×2, doji sabah/akşam yıldızı, terk edilmiş bebek ×2, üç yıldız.
Tablodaki her isim artık ya doğrudan ya da eşdeğeriyle dedektörde. **Canlı vetonun taraf kümesi
DEĞİŞMEDİ** (ölçülmüş davranış korunur); geniş kümeler (`*_SIDE_SHAPES_EXT`) yalnız bu ölçümde.

## 3. Sistem (tek değer, ızgara yok)

* Veri: on coin, 4h kapanmış barlar, arşivden; BTC günlük EMA200 rejimi (S-C için).
* Sinyal: son 3 kapanmış barda `_shapes` (v1.2.0, üretim dedektörü); geniş kümeyle taraf; iki
  taraflı/ tarafsız → sinyal yok. Coin başına aynı anda tek pozisyon.
* Giriş: sinyal barının kapanışından SONRAKİ barın açılışı (gelecek yok).
* Çıkış: stop = giriş − 2,5 × ATR14(4h) (SHORT için +), hedef = 2R, zaman stopu 24 bar (4 gün).
  Aynı barda stop ve hedef → stop (kötümser, replay ile aynı varsayım).
* Boyut: coin kovası = sermaye/10; işlem riski kovanın %2'si; kaldıraç 5× notional/kova ile sınırlı.
* Maliyet: taker %0,05 + kayma %0,05 her yön; **funding dahil** (arşivdeki 8 saatlik oranlar,
  pozisyon açıkken ödenir/alınır).
* Kollar: **S-A** tüm formasyonlar iki yön · **S-B** yalnız boğa formasyonları, LONG · **S-C** S-B +
  BTC rejimi UP · **S-D** S-B + teyit barı (makalenin kuralı: sonraki kapanış formasyon kapanışının üstünde).
* Pencereler: P1, P2, P3 (aynı). 4 kol × 3 pencere = 12 hesap. Başka hesap yok.

## 4. Kabul (baştan yazılı)

Bu bir REFERANS ölçümüdür (Q2 gibi), üretim adayı değil. Bir kol ancak **üç pencerede de**
maliyet sonrası pozitifse "ilgi çekici" sayılır ve o zaman replay içinde (üretim defteriyle)
yeniden kurulur; tek pencerede pozitiflik hiçbir şey ifade etmez. Hiçbiri üç pencerede
pozitif değilse "formasyonu görünce gir" fikri bu on coin ve bu maliyetlerle KAPANIR.

## 5. Sınırlar

* Uzman/kapı yok: sistemin geri kalanı devre dışı; bu, sorunun kendisidir ("sadece formasyon").
* Formasyonlar 4h barda çok sık; işlem sayısı yüksek, maliyet belirleyici olacak.
* Evren 2026 seçimi (hayatta kalma yanlılığı lehte); buna rağmen negatifse sonuç güçlüdür.

## 6. EK (2026-09-13, S-E hesapları görülmeden yazıldı): ÖĞRENEN formasyon girişi

Kullanıcı: "görünce girsin ama öğrensin de: o muma girdim kâr ettim, gene oldu gene kâr ettim,
demek ki bu mum kâr getiriyor desin." Bu, formasyon başına sonuç öğrenen bir döngüdür ve ayrı
bir kol olarak sınanır:

* Her boğa sinyalinde bir SANAL işlem açılır (S-B kuralları) ve kapanınca net R'si, sinyal
  barındaki her boğa etiketine yazılır (havuz: on coin birlikte, kronolojik, GELECEK YOK: sonuç
  ancak sanal işlem kapandığında öğrenilir). Öğrenme 2019-09'dan itibaren sürekli birikir.
* **S-E1:** gerçek giriş yalnız, mevcut etiketlerden en az biri için n ≥ 30 ve ortalama net R > 0 ise.
* **S-E2:** aynı, ama ortalama R'nin alt %95 sınırı (ort − 1,96·sd/√n) > 0 olmalı.
* Kabul aynı: üç pencerede de pozitif. Eşikler (30, 1,96) tek değer; ızgara yok.


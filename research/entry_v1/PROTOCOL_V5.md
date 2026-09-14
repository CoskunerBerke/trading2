# ÖN KAYITLI PROTOKOL V5 — grafik formasyonu onayı (çift dip, OBO, üçgen, bayrak)

**Yazıldığı an:** 2026-09-12, **hiçbir V5 koşusu görülmeden.**
**Kod:** `work/entry-research-v1`, `0cfe3f3` + bu turun yaması (`out/v5_code.patch`).
**Kaynak:** kullanıcının ikinci görseli (alış: çekiç, boğa yutan, çift dip, ters OBO, Wolfe,
boğa bayrağı, sabah yıldızı; satış: kayan yıldız, ayı yutan, OBO, üçlü tepe, alçalan üçgen,
akşam yıldızı, ayı bayrağı) ve stil tablosundaki günlük zaman dilimi.

## 1. Soru

Klasik grafik formasyonlarının KIRILIŞLA teyitli hâli giriş onayı olarak kullanılırsa,
üretimle aynı sistemi ölçen replay'de işlem başına net R ve hesap getirisi iyileşir mi?
Aynı soru günlük barda sorulunca cevap değişir mi?

## 2. Dış kanıt (uygulamadan önce okundu)

Bulkowski'nin hisse verisi (1991+): çift dip %16 başarısızlık, alçalan üçgen zamanın
%53'ünde YUKARI kırılıyor, gevşek bayraklar %55 başarısız, tüm formasyonların performansı
1990'lardan bu yana yaklaşık yarıya inmiş. Çıkarım: **şekil yön vermez, kırılış verir.**
Dedektör buna göre yazıldı: formasyon yalnız boyun çizgisi/sınır KAPANIŞLA kırılınca sayılır,
yönü kırılışın yönüdür (alçalan üçgen yukarı kırılırsa BOĞA). Wolfe dalgası uygulanmadı.

## 3. Mevcut durum (ölçüldü)

* Mum formasyonlarının 7'si (çekiç, yutan ×2, sabah/akşam yıldızı, kayan yıldız) zaten
  botta ve 0cfe3f3 ile karar yolunda (C3 vetosu, ENFORCE).
* Grafik formasyonları botta geometrik olarak tespit EDİLMİYORDU (kodda isimleri yok).
  Benzer örüntü motoru ve seviye ajanı aynı bilginin bir kısmını dolaylı taşıyor.
* Bu turda eklendi: `tradingbot/chart_patterns.py` (chart_v1.0.0) + `chart_confirmation.py`.

## 4. Kollar (tamamı tetikli + C3 mum vetosu = ŞU ANKİ ÜRETİM; parametre ızgarası YOK)

| kol | kural |
|---|---|
| **B0** | temel = üretim (tetik + C3 mum vetosu); `v4_c3_p1/p2` koşuları yeniden kullanılır, hash aynı olmalı |
| **P1** | son 3 kapanmış 4h bar içinde adayın yönüyle AYNI tarafli teyitli kırılış varsa gir; yoksa/karşıysa girme |
| **P2** | yalnız VETO: taze KARŞI tarafli kırılış varsa girme |
| **P3** | P1'in günlük bar sürümü |
| **P4** | P2'nin günlük bar sürümü |

Geometrik eşikler tek değer (tolerans %1,5, derinlik %3, baş fazlası %2, direk %6, tazelik 3
bar). Bu veriye uydurulmadı; ızgara yok.

## 5. Pencereler

P1 2020-11-01→2022-08-31, P2 2022-09-01→2024-08-31. Hipotez kullanıcı görselinden geldi.
4 kol × 2 pencere = **8 koşu** + temel (mevcut). Başka koşu eklenmeyecek.

## 6. Ölçütler ve kabul (önceden bağlayıcı)

V4 ile aynı birincil/ikincil ölçütler, blok bootstrap (coin × ay, 2000, eşli), maliyet stresi.
Bir kol gölge PAPER adayı olabilmek için **P1 ve P2'nin her ikisinde de**:
1. net hesap getirisi pozitif, 2. B0'dan yüksek, 3. ≥40 işlem ve ≥5 coin, 4. stres altında pozitif,
5. **YENİ (V4 dersi):** kuralın TUTTUĞU temel işlemlerin ort R'si ELEDİKLERİNİN ort R'sinden
   düşük olmamalı (anahtar coin × açılış zamanı). Bu şart, getiri farkının serbest sermayeyle
   açılan yeni işlemlerden değil, seçicilikten geldiğini ister.

Tek pencerede sağlanırsa "tek pencerede olumlu, doğrulanmadı"; hiçbirinde sağlanmazsa
reddedilir. Eşikler sonradan DEĞİŞMEZ.

## 7. Uygulama kararı (baştan yazılı)

* Hiçbir kol geçmezse: modül üretime **SHADOW** olarak girer (her adayda dört varyantın hükmü
  ve taze formasyon listesi kaydedilir), ENFORCE açılmaz; operatör isterse tek satırla açar
  ve bu "doğrulanmış" diye sunulmaz.
* Bir kol geçerse: o kol ENFORCE, diğerleri gölge.
* Her durumda: canlı motor, replay ve araştırma kuralı AYNI fonksiyonu çağırır; config'i izleyen
  replay'in hash'i araştırma koşusuyla aynı olmalı.

## 8. Baştan yazılı sınırlar

* Formasyon dedektörü yeni; sentetik testlerle doğrulandı, gerçek veride sayım dağılımı
  (kaç formasyon, hangi tür) ilk kez bu koşularda görülecek ve raporlanacak.
* Tazelik 3 bar: P1/P3 kolları çok seçici olacak; işlem sayısı şart 3'ü geçmeyebilir.
* On coin 2026'da seçildi (hayatta kalma yanlılığı); borsa filtreleri geçmişe uygulanıyor;
  replay 18 uzman, üretim 20; tetik bar kapanışı çözünürlüğünde.

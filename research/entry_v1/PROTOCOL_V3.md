# ÖN KAYITLI PROTOKOL V3 — giriş tetiği ve sabırlı motor vetosu

**Yazıldığı an:** 2026-09-12, **hiçbir V3 koşusu görülmeden.**
**Kod:** `work/entry-research-v1`, worktree `C:\Users\berke\wt-entry`.

## 1. Ölçülen kusur (bu deneyin sebebi)

`engine_v3.py:1389` üretimde her adayda `_trigger_fired` çağırır:

* `breakout`: 4h mum planlanan seviyenin ÜSTÜNDE kapanmalı **ve** fiyat seviyeden
  %1,5'ten uzaksa giriş REDDEDİLİR (kovalama yasağı).
* diğer (`pullback` / `market`): fiyat planlanan girişe **%0,25** yaklaşmalı.

`replay/engine.py` bu kontrolü **hiç yapmaz**: her adayı bar kapanışında hemen açar.
Kaynaktan doğrulandı (`_trigger_fired` replay'de geçmiyor).

Sonucu: "destek seviyesine geri çekilmeyi bekle" davranışı üretimde VAR, backtest'te YOK.
Bu yüzden beklemenin kâr etkisi bugüne kadar **hiç ölçülmedi**. 2026-09-12'de ölçtüğüm
133/197 bölmesi de tetiksizdi ve bu protokolün girdisi olarak KULLANILMAZ (yalnız hipotezi
doğurdu).

## 2. Onarım (deney değil, parite)

`_trigger_fired` mantığı `tradingbot/entry_trigger.py` içine SAF fonksiyon olarak çıkarılır.
Canlı motor ve replay AYNI fonksiyonu çağırır. Mantık ikinci kez KOPYALANMAZ — bu paketin
bu oturumda dört kez yaşadığı kusur sınıfı tam olarak budur (ekonomi kapısı, başa-baş
koruması, borsa filtreleri, şimdi tetik).

## 3. İki kol

| kol | tanım |
|---|---|
| **A0** tetiksiz | bugünkü backtest davranışı: her aday bar kapanışında açılır |
| **A1** tetikli | üretim davranışı: `entry_trigger` geçmeyen aday AÇILMAZ |

Ek olarak, A1 üzerinde tek bir politika değişkeni:

| kol | tanım |
|---|---|
| **A2** tetikli + sabırlı motor VETOSU | A1 + `plan_source != "legacy"` olan aday AÇILMAZ |

A2, sabırlı (8 ajanlı, yapı farkında) motorun "bekle" dediği ya da yön uyuşmadığı adayları
eler. **Ayarlanacak parametre YOKTUR** — üçü de ikili kural. Parametre araması
yapılmayacağı için aşırı uydurma yüzeyi bu turda yok.

## 4. Pencereler ve dürüstlük beyanı

| pencere | tarih (UTC) | bu soru için durumu |
|---|---|---|
| P1 | 2020-11-01 → 2022-08-31 | daha önce BAŞKA soru için kullanıldı (h1 adayı); bu soru için İLK kez |
| P2 | 2022-09-01 → 2024-08-31 | geliştirme penceresinin parçası olarak görüldü; bu soru için İLK kez |

**Hiçbiri bakir değildir ve öyle sunulmayacaktır.** Hipotez 2024-09 → 2026-09 penceresinde
görülen bölmeden doğdu, bu yüzden o pencere bu protokolde SONUÇ penceresi olarak
KULLANILMAZ; yalnız tamlık için raporlanır.

Kol sayısı 3, pencere 2 → **6 koşu.** Başka koşu eklenmeyecek.

## 5. Ölçütler

Birincil: aynı 100 USDT başlangıç sermayesinde **maliyet sonrası net hesap getirisi (%)**.
Yanında: işlem sayısı, işlem başına ort/medyan net R, isabet, profit factor, maksimum düşüş,
maruziyet (gün oranı), ortalama eş zamanlı pozisyon, funding kapsamı, determinizm hash'i.

Belirsizlik: blok bootstrap (blok = coin × takvim ayı), 2000 tekrar. **Nokta tahmini
gözlenen farktır**; bootstrap yalnız aralık üretir. Aralığın sıfırı içermemesi tek başına
finansal yeterlilik kanıtı DEĞİLDİR.

Maliyet stresi: sabit işlem kümesinde ücret ×2 ve slippage ×2 (slippage fill'e gömülü
olduğu için BİR KEZ eklenir). Sabit kümede sonuç daima kötüleşmeli.

## 6. Kabul/ret (önceden bağlayıcı)

Bir kol "ileri dönem gölge PAPER adayı" olabilmek için **her ikisinde de** (P1 ve P2):

1. net hesap getirisi **pozitif**,
2. net hesap getirisi A0'dan yüksek,
3. en az 40 kapanmış işlem, en az 5 farklı coin,
4. maliyet stresi altında da pozitif.

Şartlar iki pencerede de sağlanmazsa kol **reddedilir**. Tek pencerede sağlanırsa
"tek pencerede olumlu, doğrulanmadı" diye raporlanır ve gölge PAPER'a GİRMEZ.

Sıfır işlem üreten kol "zarar etmedi" diye kazanan sayılmaz; şart 3 bunu engeller.
Eşikler sonuç görüldükten sonra DEĞİŞTİRİLMEYECEK.

## 7. Baştan yazılı sınırlar

* Hipotez görülmüş veriden doğdu; P1 ve P2 bakir değil.
* On coin 2026'da seçildi: hayatta kalma yanlılığı iki pencerede de var.
* Bugünkü borsa filtreleri geçmişe uygulanıyor (senaryo varsayımı, tarihsel doğrulama değil).
* Replay `--no-patterns` koşar ve `market:*` / `orderbook` / `derivatives` ajanları
  kullanılamaz durumdadır; 18 uzman çalışır, üretimde 20.
* Tetik replay'de **bar kapanışı** çözünürlüğünde değerlendirilir. Üretim 15 dakikalık tur +
  60 saniyelik çıkış monitörüyle çalışır, yani gerçekte fiyat seviyeye bar içinde de
  değebilir. Bu, tetiğin replay'de üretimden DAHA KATI olması demektir: replay bazı geçerli
  girişleri kaçırır. Yön olarak muhafazakârdır ve öyle raporlanacaktır.
* `self.triggers` (aynı barda ikinci giriş engeli) replay'de koşu başına sıfırlanır.

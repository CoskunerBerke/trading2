# ÖN KAYITLI PROTOKOL V14 — hayatta kalma yanlılığı: 2020'de bilinebilecek evren

**Yazıldığı an:** 2026-09-14, V14 verisi henüz bu bilgisayara gelmeden ve hiçbir V14 koşusu görülmeden.
Kod `3501304`; yalnız araştırma paketi; VPS'e ve üretime hiçbir şey girmez.

**Soru:** T2 (+107/+34/+10) ve M2 (+158/+46/+35) sonuçları BUGÜN var olan on coinle ölçüldü. 2020 sonunda
bir yatırımcı bu listeyi bilemezdi; o gün büyük olan coinlerin bir kısmı o günden beri değerinin çoğunu
kaybetti. Kural, 2020'de seçilebilecek evrende de kâr ediyor mu?

## 1. Evren ve veri

* **2020 evreni:** Ekim-2020 USDT-perpetual hacim sıralamasının ilk onu, VPS'te `vps-collect-2020-universe.sh`
  ile hesaplandı: **BTC ETH LINK YFI BCH UNI BNB LTC XRP DOT**. Bugünkü evrenden SOL, DOGE, AVAX, AAVE yok;
  yerlerinde YFI, BCH, UNI, DOT var. Sıralama dosyası: `ranking_oct2020.json` (paketin içinde).
* **Veri:** VPS'ten csv.gz paketi (`tb-2020-universe.tar.gz`), AYRI önbellek dizinine açılır
  (`C:/Users/berke/wt-2020/data`), koşular `TRADINGBOT_CACHE_DIR` ile o dizini kullanır. Bugünkü evrenin
  arşivine dokunulmaz.
* **Borsa filtreleri:** bugünkü `symbol_filters.json` (718 sembol; on sembolün hepsi var) aynen kopyalanır.
  Sınır: 2020–2021'deki tick/step değerleri bugünkünden farklı olabilir; bu, bugünkü evren koşularında da
  aynı biçimde vardı (aynı hata her iki tarafta).

## 2. Kollar

| kol | evren | koşu |
|---|---|---|
| T2 EMA200 (`t2_trend_regime`) | 2020 evreni, üç pencere, `--no-breakeven`, `--symbols` | 3 |
| M2 TSMOM 28g (`m2_tsmom28`) | 2020 evreni, üç pencere, `--no-breakeven`, `--symbols` | 3 |

Toplam **6 koşu.** Karşılaştırma tabanı: bugünkü evren (`v9_t2_*`, `v11_m2_*`), yeniden koşulmaz.
Başka evren, başka parametre, başka pencere YOK.

## 3. Bayraklar (baştan yazılı)

* **S1 işaret değişimi:** bir pencerede bugünkü evren pozitif, 2020 evreni negatif → o pencerede sonuç
  hayatta kalma yanlılığıyla şişmiş.
* **S2 yarıya iniş:** 2020 evreni pozitif ama bugünkü evrenin yarısından az → "şişmiş ama ayakta" (bilgi).
* **S3 örneklem:** 2020 evreninde bir pencerede < 10 işlem → o pencere yorumlanmaz.

**Karar kuralı:** bir kural için S1 ≥ 2 pencerede kalkarsa kuralın ölçülmüş beklentisi **HAYATTA KALMA
YANLILIĞIYLA GEÇERSİZ** sayılır: VPS'te değişiklik yapılmaz (kâğıt defterler zaten gerçek para taşımıyor)
ama RESUME/hafızadaki "+107/+34/+10", "+158/+46/+35" sayıları geçersiz ilan edilir ve canlıya geçiş
tartışması bu sonuçlara dayandırılamaz. S1 en fazla 1 pencerede kalkar ve S2 yaygınsa: beklenti aşağı
revize edilir, ileri test sürer. Hiç kalkmazsa: "2020 evreninde de ayakta" yazılır; kanıt değil, güven artışı.

## 4. Sınırlar

* Ekim-2020 sıralaması tek bir aya dayanır; başka ay başka liste verebilir (tek liste, ızgara yok).
* Rejim kapısı BTC'dir ve her iki evrende de var; fark yalnız işlem evreninden gelir.
* Bugünkü evrende 100 USDT ile BTC/ETH açılamıyordu; 2020 evreninde de aynı (adil karşılaştırma).
* Sonuç iyi çıksa bile evren "bugün hâlâ listede olan" coinlerden oluşuyor (bu on coinin hepsi hâlâ
  Binance vadelide); tam hayatta kalma testi delist olmuş coinleri de gerektirirdi — veri yok.

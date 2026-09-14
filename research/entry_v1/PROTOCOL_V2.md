# ÖN KAYITLI PROTOKOL V2 — giriş kuralı araştırması

**Yazıldığı an:** 2026-09-12, **hiçbir hipotez koşusu görülmeden.**
**Kod:** `work/entry-research-v1`, taban `350928c`, worktree `C:\Users\berke\wt-entry`.
**Veri:** `C:\Users\berke\wt-ten\data\history\futures` — Binance USDⓈ-M resmî arşivi, on coin,
1d/4h/1h/15m + funding, manifest checksum'lı.

## 1. Ne sabit, ne araştırılıyor

**SABİT (bu turda dokunulmaz):** çıkış geometrisi (stop 2,5×ATR, hedef 2× ve 3× stop
mesafesi), başa-baş kuralı (`breakeven_at_mfe_r = 1.0`), TP1 oranı 0,5, maliyet modeli
(taker %0,05, slippage 3 bps, gerçek funding arşivi), başlangıç sermayesi 100 USDT, risk
politikası ve kaldıraç seçimi, on coinlik evren, üretim risk motoru ve emir filtreleri.

**ARAŞTIRILAN:** giriş kuralı. Üç hipotez, hepsi karar anında KAPANMIŞ barlardan üretilen
büyüklükler üzerinde (`entry_rules.py`).

## 2. Hipotezler ve tam parametre listesi

Her kural `(sembol, karar, plan) -> (izin, gerekçe)` döndürür ve motorun `entry_rule`
kancasına takılır. Kanca `None` iken davranış birebir değişmez.

| aile | kural | ızgara (6 yapılandırma) |
|---|---|---|
| **H1** 4h trend uyumlu 1h geri çekilme + mum dönüş teyidi | LONG: rejim ∈ {TREND_UP, BREAKOUT}, EMA50 eğimi(1d) > 0, RSI(1h) ≤ `rsi`, CLV(4h) ≥ `clv`. SHORT: aynası | `rsi` ∈ {40, 45, 50} × `clv` ∈ {0,10, 0,20} |
| **H2** sıkışma sonrası kırılım + göreli hacim | BB genişlik sırası(0-100) ≤ `bb`, hacim oranı(4h) ≥ `vol`, LONG aralık konumu ≥ 0,80 / SHORT ≤ 0,20 | `bb` ∈ {25, 35, 45} × `vol` ∈ {1,15, 1,30} |
| **H3** yalnız yatay rejimde ortalamaya dönüş | rejim = RANGE zorunlu; LONG aralık konumu ≤ `pos` ve RSI(4h) ≤ `rsi`; SHORT aynası. Trend rejiminde HİÇ işlem yok | `pos` ∈ {0,15, 0,20, 0,25} × `rsi` ∈ {30, 35} |

**Toplam arama bütçesi: 18 yapılandırma.** Yakın parametre dayanıklılığı bu bütçeye
DAHİLDİR (her ailede 3×2 komşuluk). Bu sayı istatistiksel bir garanti değil, çalışma
kapsamıdır. LONG ve SHORT koşulları baştan simetrik yazıldı; hiçbir yön sonradan
çıkarılmayacak.

## 3. Pencereler

| pencere | tarih (UTC) | durum | kullanım |
|---|---|---|---|
| GELİŞTİRME | 2022-09-01 → 2026-09-01 | **GÖRÜLDÜ** (önceki tur bu iki pencereyi de tüketti) | 18 yapılandırmanın tamamı burada koşar; aday buradan seçilir |
| DEĞERLENDİRME | 2020-11-01 → 2022-08-31 | **HİÇ KULLANILMADI** | dondurulmuş TEK aday burada BİR KEZ koşar |

Değerlendirme penceresi geliştirmeden ÖNCEdir, ileri dönem DEĞİLDİR. İleri dönem
doğrulaması ayrıca gölge PAPER protokolüyle planlanır. Pencere 2020-11'de başlar çünkü on
coinin tamamının USDⓈ-M 4h arşivi o aydan itibaren vardır (doğrulandı).

Koşu içi walk-forward: `--train-days 180 --test-days 30 --purge 6 --embargo 6`. Coinler
tarihe göre rastgele bölünmez; bölme yalnız zamandır. Aynı takvim bloğundaki coinler
bağımsız sayılmaz (blok = coin × takvim ayı).

## 4. Ölçütler ve seçim kuralı

Birincil ölçüt **maliyet sonrası portföy sonucu**: aynı 100 USDT başlangıç sermayesi ve aynı
risk sınırlarıyla koşu sonundaki **net hesap getirisi (%)**. İşlem başına ortalama R ikincil
ölçüttür ve tek başına seçim yapmaz.

**Seçim kuralı (bağlayıcı, şimdi yazıldı):**
1. Geliştirmede net hesap getirisi en yüksek yapılandırma seçilir.
2. Asgari şart: **en az 60 kapanmış işlem, en az 6 farklı coin, en az 12 farklı takvim ayı.**
3. Dayanıklılık şartı: aynı ailedeki **6 komşunun en az 4'ünde** net hesap getirisi
   temel koşudan yüksek olmalı. Tek parlak eşik kabul edilmez.
4. Eşitlik bozma: daha yüksek işlem sayısı, sonra alfabetik ad.
5. **Hiçbir yapılandırma 1-3'ü sağlamıyorsa değerlendirme penceresi TÜKETİLMEZ** ve üçü de
   reddedilir. Kârlı sonuç çıkana kadar arama uzatılmaz.

Sıfır ya da çok az işlem üreten yapılandırma "zarar etmedi" diye kazanan sayılmaz; şart 2
bunu engeller.

## 5. Karşılaştırmalar (aynı veri, maliyet, sermaye, risk)

| referans | tanım | eksik üretim parçası |
|---|---|---|
| **B1a** mevcut bot — kapılı | üretim karar yolu + ekonomi kapısı (`economics_gate.assess_one`) | pattern kanıtı, giriş seçicilik/MTF, haber, legacy p_win harmanı; `p_win_source` alanında kayıtlı |
| **B1b** mevcut bot — kapısız | aynı yol, ekonomi kapısı KAPALI | yukarıdakiler + ekonomi kapısı |
| **B2** nakit | hiç işlem yok, getiri varsayılmaz: %0 | — |
| **B3** al-tut | on coin, başlangıçta eşit ağırlık, kaldıraçsız, tek alım + tek satış, taker %0,05 iki yönlü | SPOT benzeri, farklı maruziyet; kaldıraç ve stop yok |

B3 farklı bir maruziyet sınıfıdır ve üstünlük iddiası için tek başına yeterli değildir;
yalnız "para piyasada dursaydı ne olurdu" ölçüsüdür.

Portföy Sharpe'ı **günlük** özkaynak serisinden, **işlem yapılmayan günler dahil**
hesaplanır. Yalnız piyasada kalınan zamandan türetilmiş bir oran raporlanmaz.

## 6. Raporlanacaklar

Her koşu için: net USDT, hesap getirisi %, toplam/ortalama/medyan net R, net profit factor,
maksimum düşüş, işlem sayısı, maruziyet (pozisyonda geçen bar oranı), ortalama elde tutma,
coin/dönem dağılımı, funding kapsamı, determinizm hash'i, config sha256.

Belirsizlik: blok bootstrap (blok = coin × takvim ayı), 2000 tekrar. **Nokta tahmini gözlenen
farktır** (`observed_diff`); bootstrap yalnız aralık üretir. Aralığın sıfırı içermemesi tek
başına finansal yeterlilik kanıtı değildir ve öyle sunulmaz.

Maliyet stresi iki ayrı ölçümle: (a) işlem kümesi SABİT, ücret ×2 ve slippage ×2;
(b) dinamik portföy. (a) daima kötüleşmeli; kötüleşmiyorsa hesap kusuru vardır.

## 7. Kabul/ret

* Üç aile de §4'ün 1-3 şartlarını geliştirmede sağlayamazsa: **hepsi reddedilir**,
  değerlendirme penceresi tüketilmez.
* Bir aday şartları sağlarsa dondurulur ve değerlendirme penceresinde **bir kez** koşar.
  Orada net hesap getirisi B1a'nın üstünde ve pozitif değilse: **araştırma adayı** olarak
  kalır, kârlı strateji DENMEZ.
* Hiçbir durumda üretim worker'ı otomatik değiştirilmez; gölge PAPER ayrı onaya kalır.

## 8. Baştan yazılı sınırlar

* Geliştirme penceresi önceki turda GÖRÜLDÜ; oradaki üstünlük keşifseldir.
* Değerlendirme penceresi (2020-11 → 2022-08) geliştirmeden ÖNCEdir; farklı rejim taşır
  (2021 yükseliş + 2022 düşüş) ve ileri dönem doğrulaması yerine GEÇMEZ.
* On coin 2026'da seçildi: hayatta kalma/seçim yanlılığı her iki pencerede de vardır ve
  sonucu OLDUĞUNDAN İYİ gösterir.
* Replay varsayılan sembol filtreleriyle çalışır (borsadan çekilmiş filtre yoktur).
* Replay `--no-patterns` koşar; benzerlik indeksi standardizasyonu geleceği görür.
* Hipotezler kapısız (B1b) rejimde ölçülür: ekonomi kapısı açıkken üretim ayda ~5 işlem
  açtığı için giriş kuralı ayırt edilemez. Hayatta kalan aday AYRICA kapılı modda raporlanır.

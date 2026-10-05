# M2X — agresif M2 kâğıt defteri: ön kayıt v1 (2026-10-05)

PAPER. Gerçek para yok. Bu belge kodlamadan ve M2X için hiçbir geçmiş sonuç (simülasyon, bootstrap, getiri, düşüş,
işlem sayısı) hesaplanmadan önce yazıldı; commit zamanı kanıttır. İlk taslak bağımsız bir eleştiriden geçti. Eleştiri de
hiçbir sonuç hesaplamadı, yalnız kod yollarını okudu. Taslak commit edilmeden bu sürüme revize edildi; yanıtlar §6'dadır.
Buradaki sayılar ve kurallar sonuç görüldükten sonra GEVŞETİLMEZ ve "iyileştirilmez". Değişiklik yeni sürümdür
(`m2x_v2`), belgeye yeni bölüm olarak yazılır ve v1'in sonuçları yerinde kalır.

## Bağlam

- Sahibin sözleri (2026-10-05): "ayda %1 çok kötü istemiyorum, başka bir şey bulalım" ve "%50 riskli yapsak ama kötü
  dönemde de temkinli davranamaz mıyız?".
- Belgelenmiş hedefler: ayda en az +%1 net (karne `MONTHLY_TARGET_PCT`, 2026-10-03) ve günde en az +%1 (öğrenme motoru
  tasarımı `docs/SYSTEM_LEARNING_ENGINE_V1.md` §1.1, `impl/system` dalı, `a9be1d3`). Sahibin son mesajı aylık +%1'i yetersiz
  buluyor. Bu belge ikisini de hüküm ölçüsü olarak KULLANMAZ; yalnız rapor eder (§3.5, §4).
- Karar: M2'nin GERÇEKTEN açtığı işlemleri kendi defterinde, daha büyük boyutla kopyalayan ayrı bir kâğıt defter: **M2X**.
  Sinyal ve çıkış M2'nin; boyut ve risk freni M2X'in.

### M2'nin kanıtı: ne biliniyor, ne bilinmiyor

M2 = `m2_tsmom28` (defter `strategy_paper_m2`, günlük barda TSMOM28, yalnız LONG). Kod 2026-09-13'te eklendi (`f25cb39`).
VPS'teki ayarı üç dönemde değişti:

| Dönem (UTC) | M2'nin boyutu | Not |
|---|---|---|
| VPS'e ilk geliş (2026-09 ortası; saat repoda yok) → 2026-09-28 21:20 | taban: işlem başı başlangıç bakiyesinin %2'si, toplam açık risk %6, ~3 pozisyon | 2026-09-23'ten itibaren yapı katmanı ENFORCE (`0f0221a`) |
| 2026-09-28 21:20 → `f8b05fb` dağıtımı | öğrenme modu: %0,5, slot 40, kaldıraç ≤ 4; `extra_entries` alanı yok (= `open`, bütün öğrenme ekstraları açılır) | `docs/ogrenme_modu/DURUM.md` |
| `f8b05fb` dağıtımı → bugün | öğrenme modu + `extra_entries: record_selectivity` | kod 2026-10-03; sürüm betiği 2026-10-04; dağıtım saati repoda yok, VPS dağıtım kaydından alınacak |

- "49 kapanmış işlem, ortalama +0,12 R, %95 aralık [+0,05, +0,22]" VPS karnesinin defter satırıdır. M2 defterindeki BÜTÜN
  kapanmış işlemleri sayar; yukarıdaki üç dönemi karıştırır. İlk taslaktaki "öğrenme modunda, %0,5 riskte" etiketi
  YANLIŞTI.
- "30 günde +%7,42" karnenin AYLIK HEDEF bölümündeki "son 30 gün"dür: son 30 günde kapanan işlemlerin neti ÷ 200 USDT.
  Bu pencere M2'nin canlı ömrünün hemen tamamıdır ve çoğu taban dönemidir. Taban işleminde 1R = 4 USDT, öğrenme
  işleminde 1R = 1 USDT; yüzdenin büyük kısmı taban işlemlerinden gelebilir. "%0,5 riskte 30 günde +%7,42" okuması yanlıştır.
- Dönem başına işlem sayısı ve R bu çalışma alanında HESAPLANAMAZ: işlem listesi yalnız VPS'tedir. Uygulamanın 0. adımında
  VPS'ten salt okunur bir kırılım alınır (dönem başına n, ortalama R, giriş gününe göre kümelenmiş aralık) ve sonuç
  belgesine yazılır. Kırılım v1 sayılarını DEĞİŞTİRMEZ.
- %95 aralık, aynı gün açılan ve birlikte hareket eden kripto işlemlerini bağımsız sayar. Gerçek belirsizlik daha geniştir.
  Bu belgede aralık **iyimser** diye etiketlenir. Giriş gününe göre kümelenmiş aralık sonuç belgesinde verilir.
- Doğru ifade: M2 şu ana kadar ortalaması artıda olan tek defterdir. Örnek kısadır (yaklaşık 3 hafta), elverişli bir
  döneme denk gelir ve aralık iyimserdir. "Kanıtlanmış artı" DEĞİLDİR.
- M2 öğrenme modunda genelde aynı anda 25–29 pozisyon tutar (2026-09-29 dağıtımından önce 30). Hepsi kripto ve LONG;
  birlikte hareket ederler. Tek bir ortak çöküşte birlikte stop olur ya da likide olurlar.

## 0. Özet

### 0.1 Sahibe düz dille

- M2X, M2'nin açtığı işlemleri kendi 200 USDT'lik kâğıt defterinde kopyalar. İyi dönemde işlem başına M2'nin 4 katı risk
  alır (%2). Aynı anda en çok ~10 tam boy pozisyon tutar. M2 ≤ 10 pozisyon tutarken toplam risk M2'nin 4 katıdır. M2
  dolu iken (25–29 pozisyon) toplam risk M2'nin ancak ~1,4–1,6 katıdır ve sonradan gelen M2 işlemleri küçük alınır ya da
  atlanır.
- **−%50 bir zarar tavanı DEĞİLDİR.** Zirveden −%50'de yeni giriş durur. Açık pozisyonlar kapanmaz, M2'nin çıkışını
  bekler. Düşüş −%50'nin ötesine geçebilir. Simülasyon ne kadar geçtiğini gösterir (§3.5).
- Tek bir 2025-10-10 benzeri saatlik çöküş, en iyi kademede (K1) defterin yaklaşık yarısını alabilir. Kural, yeni giriş
  anında böyle bir olayın −%50 çizgisini aşmamasını sağlar. Girişten sonra fiyat hareketiyle bu sınır aşılabilir.
- Kötü dönemde temkin: düşüş %15'i geçince işlem riski yarıya, %25'i geçince çeyreğe iner. %35'te yeni giriş durur
  (yumuşak durdurma). Geri dönüş tek kademe tek kademe ve her biri 5 günle olur: ya M2'nin kendi işlemleri son 20 günde
  artıya döner ya da defter toparlanır.
- Simülasyon sonuçları getiri sözü değildir.

### 0.2 Sabitlenen sayılar

| Konu | Değer |
|---|---|
| Defter adı / klasör | `m2x_aggressive` / `state/strategy_paper_m2x/` (indeks: key `strategy_paper_m2x`, name `m2x_aggressive`, `kind: mirror`) |
| Başlangıç bakiyesi | 200 USDT (diğer defterlerle aynı) |
| Kaynak | M2 defterinde GERÇEKTEN açılan işlemler; M2X işlerken M2'de hâlâ açık olanlar (§1.2) |
| Çıkış | M2 ile aynı stop, M2'nin kural çıkışı aynı turda; tek bilinçli fark likidasyondur (§1.4) |
| İşlem başı risk | K1 %2,0 · K2 %1,0 · K3 %0,5 (mark'a göre canlı özkaynağın) |
| Açık risk tavanı (OR, mark'tan stopa) | K1 %20 · K2 %12 · K3 %6 |
| Kriz bütçesi | Her girişte, giriş sonrası CL ≤ E − 0,5 × P. CL: her pozisyonun %50'lik ani düşüşte ya da likidasyonda kaybı (§2.1) |
| Kademeler (Pₖ'den düşüş DDₖ) | K1 < %15 · K2 %15–25 · K3 %25–35 · K4 %35–50 = yumuşak durdurma (yeni giriş yok) |
| Durdurma | Gerçek zirveden düşüş DD ≥ %50 → yeni giriş yok; yalnız sahip `m2x-resume` ile açar |
| Aşağı inme | Gözlem noktasında hemen (birden çok kademe atlanabilir) |
| Yukarı çıkma | Tek kademe; 5 ardışık günlük görüntüde A (DDₖ ≤ kademenin alt sınırı − 5 puan) ya da B (M2'nin birim-R eğrisi 20 günde artıda) |
| Gözlem noktaları | Günlük görüntü (00:00 UTC sonrası ilk tam veri) ve M2'nin yeni giriş açtığı tur (§2.7) |
| Kaldıraç | Tampon içinde izin verilen EN YÜKSEK kaldıraç L_liq ≤ 4 (likidasyon ≥ 2 × stop; bracket MMR ile denetlenir) |
| Marj | Slot 10 (pozisyon başına marj ≤ %9,5 E), rezerv %5, toplam marj ≤ %95 E |
| Tavan aşılırsa | Ortak küçültme λ; λ < 0,25 ise M2'nin açılış sırasıyla 0,25 boy, kalanı atlanır (§2.4) |
| Min-notional | Çıkarma yalnız risk ≤ min(2 × kademe riski, %2) × E ve iki boşluk içinde kalırsa |
| Kill switch | `m2x_aggressive.enabled: false` (config.yaml varsayılanı). Sahip dağıtımda açar |
| Mevcut defterlere etkisi | YOK (§1.6, §1.7) |

Önerilen başlangıç değerlerinden sapmalar: K1 açık risk tavanı %25 değil **%20**. K4'te %0,25 risk yerine **yumuşak
durdurma**. Ek olarak **kriz bütçesi** ve **L_liq kaldıracı**. Gerekçeler §2.2, §2.3 ve §2.5'te. %25'li sürüm bilgi
kolu (e) olarak raporlanır.

## 1. Defterin tanımı

### 1.1 Kimlik

- Ad `m2x_aggressive`. Defter `state/strategy_paper_m2x/futures_ledger.json` (`FuturesLedgerV2`, diğer kâğıt defterlerle
  aynı sınıf). Politika durumu `state/strategy_paper_m2x/m2x_state.json`: zirveler, kademe, sayaçlar, U serisi,
  durdurma, dönemler. Özet `state/strategy_paper_m2x.json`.
- Başlangıç bakiyesi 200 USDT. Gerekçe T2/M2 defterindeki "BAKİYE 200" notudur: 100 USDT'de pozisyonlar borsa
  minimumlarına takılıyordu.
- Config'te AYRI bölüm: `m2x_aggressive`. `strategy_paper.extra` listesine GİRMEZ. Neden: `extra` listesindeki her girdi
  kendi kuralını koşan bir `StrategyBook`tur. Kural kaydı (`paper_rules.spec_for`), öğrenme modu defter listesi
  (`learning_mode.BOOK_NAMES`), ortak deneyim defter haritası (`shared_experience.rows.BOOKS`) ve karne defter listesi
  (`bot_scorecard.BOOKS`) bu listeye bağlıdır. M2X kural koşmaz; bu kayıtların hiçbirine eklenmez.
- Motorda AYRI öznitelik: `engine.m2x_book`. `engine.strategy_books`'a GİRMEZ (§1.6).
- `state/strategy_paper_index.json` (panel okur, karar yolu okumaz) girdisi:
  `{"key": "strategy_paper_m2x", "name": "m2x_aggressive", "kind": "mirror", "parent": "m2_tsmom28",
  "summary_file": "strategy_paper_m2x.json"}`. Panel `key`i klasör adı olarak kullanır. `name` benzersizdir ve bir kural
  adı değildir; ada göre anahtarlanan panel haritaları (ör. yapı görünümü) çakışmaz.

### 1.2 Hangi sinyaller kopyalanır: yalnız M2'nin GERÇEKTEN açtıkları

**Tanım:** M2 defterinde bir turda yeni açılan her pozisyon M2X için bir giriş adayıdır. Aday şunları taşır: M2 işlem
kimliği, sembol, yön (LONG), giriş fiyatı, stop, M2'nin sinyal barı (`signal_ts`) ve M2'nin o andaki öğrenme durumu
(aktif/askıda, `extra_entries` kipi, `learning_unlocked_by` sınıfı).

**Yakalama:** motor her defter için `held_before` ve adım sonrası `held_ids()` farkını zaten alıyor (M2'nin kilidiyle).
M2X adayları bu farktan, M2'nin `step`'inden hemen sonra yakalanır. Aynı adımda açılıp kapanan pozisyonlar M2'nin
geçmişinden bulunur (`opened_at` ≥ adım başı). Adaylar tur sonunda M2X işlenene kadar bellekte tutulur.

**Seçilmeyen tanım:** "politika girişleri + öğrenme ekstraları, M2'nin açtığı gibi" (kuralı ikinci kez koşturmak).
Neden: kuralı yeniden koşturmak yapı katmanının kullanılmış formasyon sayımı, veri reddi, yeniden başlatma ve kapasite
yüzünden M2'den ayrışabilir; bu ikinci bir kusur kaynağı olurdu. Karşı-olgusal ve yalnız-kayıt adaylar işlem değildir,
dolumları yoktur; kopyalamak M2X'i öğrenme katmanının çıktısına bağlardı. Bugünkü config'te (`record_selectivity`)
seçicilik ekstraları zaten açılmaz; kapasite ekstraları açılır ve kopyalanır.

**Bu bir saf boyut dönüşümü DEĞİLDİR (ilk taslağın iddiası geri çekildi).** Tavanlar dolunca M2X sonraki M2 girişlerini
küçültür ya da atlar. M2 25–29 pozisyon tutarken M2X ~10 tam boy tutar. Hangi işlemlerin tam boy alınacağını ZAMAN sırası
belirler: önce gelenler (çoğunlukla trend başı girişleri) tam boy, sonrakiler küçük ya da hiç. Bu, M2'nin işlem
dağılımından zamana göre bir alt küme seçimidir. **M2'nin ortalama R'si M2X'e TAŞINMAZ.** Raporlarda kopyalanan,
küçültülen ve atlanan M2 işlemlerinin M2'deki R'si ayrı ayrı verilir (§3.5). Her M2 işlemini aynı oranla kopyalayan
"orantılı kopya" ön kayıtlı karşılaştırma kolu (p) olur (§3.4).

**Ana kolun neden zaman sıralı tasarım olduğu:** sahibin isteği daha yüksek getiri. Orantılı kopyada (her işleme
OR tavanı / 28 ≈ %0,7) işlem başı risk M2'nin yalnız ~1,4 katıdır ve M2 az pozisyon tutarken (trend başlarında) ek risk
alınmaz. Zaman sıralı tasarım trend başlarında 4 kat risk alır. Hangisinin daha iyi olduğu bilinmiyor; (b) ve (p)
ikisi de önceden kayıtlı.

**Bağımlılık açıkça yazılır:** M2X, M2'nin config'i ne üretirse onu kopyalar. M2'nin öğrenme modu askıya düşerse ya da
kapatılırsa M2 taban boyuta döner (%2 risk, toplam açık risk %6). O zaman yalnız ~3 pozisyon açar ve M2X de yalnız onları
kopyalar. Her kopyalanan işlem M2'nin o andaki öğrenme durumunu meta olarak taşır; raporlar bu duruma göre bölünür.
`extra_entries` kipi değişirse M2X durum dosyasına "girdi rejimi değişti" olayı yazılır.

### 1.3 Giriş

- Yalnız M2'nin pozisyonu açtığı turda, M2X o turda işlenirken. M2X o turu işleyemediyse (arıza, yeniden başlatma) giriş
  YOK (`MISSED_PARENT_TOUR`). Trend ortasında sonradan girilmez.
- M2X işleyene kadar eş pozisyon M2'de kapanmışsa (aynı adımda ya da koruyucu izleyiciyle) giriş YOK
  (`M2X_PARENT_ALREADY_CLOSED`). O işlemin M2'deki R'si "atlanan" olarak raporlanır.
- Fiyat: M2'nin dolumunda kullanılan AYNI tur mark'ı (aynı `TickData`, turun `pmarks`ı). Kayma modeli de aynı olduğundan
  dolum fiyatı M2'ninkiyle aynıdır. M2X yeni fiyat ALMAZ (§1.6). Yaş denetimi M2'nin girişinde yapıldı; amaç M2'nin
  dolumunu kopyalamaktır.
- Aynı stop fiyatı. Hedef yok (M2'de yok). Başa-baş yok (`breakeven_at_mfe_r: 0`). M2 stopunu değiştirirse M2X aynı turda
  eşler (`STOP_SYNC`). Bugünkü config'te M2 stop taşımaz.
- Boyut §2'ye göre. Boyut sıfırsa ya da atlanırsa neden sayaçla kaydedilir: `M2X_HALTED`, `M2X_SOFT_HALT`,
  `M2X_OPEN_RISK_CAP`, `M2X_CRASH_BUDGET`, `M2X_MIN_NOTIONAL`, `M2X_MARGIN`, `M2X_LIQ_BUFFER`, `M2X_DATA_GAP`,
  `M2X_PARENT_ALREADY_CLOSED`, `M2X_NEW_ENTRIES_OFF`, `MISSED_PARENT_TOUR`. Karşı-olgusal yazılmaz.

### 1.4 Çıkış (M2 ile aynı, likidasyon hariç)

M2X'in tur içindeki işlem sırası M2'nin kendi sırasının (kural → kapanmış 1h bar uçları → tik) aynısıdır. Böylece aynı
girdi aynı çıkışı verir:

1. **M2'nin kural/yapı çıkışını eşle:** M2 bu turun `step`'inde bir pozisyonu kapattıysa (`M2_TSMOM28_CROSS_DOWN`,
   `M2_STRUCTURE_EXIT` / giriş yapısı bozulması, ileride eklenirse trail), M2X eşini aynı mark'la kapatır. Neden
   `MIRROR_PARENT_CLOSED:<M2 nedeni>`.
2. **Kapanmış 1h bar uçları:** `apply_closed_bars`, turun zaten hazırladığı `pbars` ile (M2 ile aynı satırlar).
3. **Tik:** `tick`, turun `pmarks`ı ile.
4. **Mutabakat:** eşi M2'de artık açık olmayan M2X pozisyonu bu turun mark'ıyla kapatılır (`MIRROR_ORPHAN_CLOSED`).
   Ayrışma olarak sayılır.
5. Gözlem (§2.7), sonra yeni girişler (§1.3), sonra kayıt.

Turlar arasında stopu koruyucu izleyici (60 sn) uygular. M2X'in tutamacı M2'ninkiyle aynı fiyat partisini alır. Stop aynı
olduğu için M2 ile aynı geçişte kapanır.

Kısmi kapanış M2'de yok; ileride olursa M2X aynı oranı kapatır. Zorla küçültme YOK: kademe düşse de açık pozisyonlara
dokunulmaz, kademe yalnız YENİ girişleri boyutlar. Bunun bedeli §2.3'te yazılı; ölçmek için kollar (h1, h2) var.

**Ayrışma kaynakları (ayrı ayrı sayılır):**

- (a) **Likidasyon (bilinçli):** M2X'in kaldıracı (L_liq, §2.5) M2'nin kaldıracından yüksek ya da eşittir; likidasyonu
  stopa daha yakındır. Bir 1h bar ya da izleyici gözlemi hem stopu hem M2X'in likidasyonunu geçerse defter ihtiyatlı
  kuralla likidasyon yazar (`PRUDENT_LIQUIDATION` ya da `FIRST_OBSERVATION_BEYOND_LIQUIDATION`); M2 ise stopla çıkar.
  Bu yalnız stoptan sonra bir saat içinde ~%14–37'lik ek düşüşte olur (§2.3 tablosu). Sayısı ve R maliyeti raporlanır,
  kol (g) ile karşılaştırılır. Aşağıdaki %2 hedefinin dışındadır çünkü tasarım gereğidir.
- (b) Yetim kapanış. (c) Miktar adımı / min-notional yuvarlaması (R farkı küçük). (d) Bracket denetimi yüzünden indirilen
  kaldıraç (M2X'in likidasyonu uzaklaşır; M2 likide olup M2X olmayabilir).
- Hedef: (b)–(d) toplamı ≤ %2 işlem. Aşılırsa sonuçlar "parite zayıf" başlığıyla verilir.

### 1.5 Maliyet ve funding

- Ücret, kayma ve likidasyon parametreleri M2 ile birebir aynı: `FeeSchedule` (taker %0,05, maker %0,02), kayma 3 bps,
  likidasyon ücreti %0,5, bracket'lı likidasyon fiyatı.
- Açık pozisyonun funding'i, turun ve izleyicinin zaten kullandığı bellekteki `FundingRates` ile uygulanır
  (`funding_rate_lookup`; ağ yok, bekleyen dönem tahmin edilmez).
- Kapanmış işlemlerin bekleyen funding'i M2X'in kendi tur adımında aynı bellekten uzlaştırılır (ağ yok).
- M2X'in bekleyen sembolleri `_funding_step`'in yenileme listesine EKLENMEZ. M2X pozisyonları M2'ninkilerin aynasıdır;
  bekleyen dönemleri M2'nin bekleyen dönemlerinin içindedir ve oranlar M2 için zaten çekilir. Yalnız M2X'te kalan
  bekleyen dönem (yetim ya da ayrışma) oran bellekte görünene kadar bekler. Sayısı özette ve `--check`'te
  `funding_pending_m2x_only` olarak görünür.
- Funding miktarla orantılıdır; R'yi değiştirmez.

### 1.6 Yalıtım (değişmezler; uygulamada testle korunur)

Kural: M2X, M2 dahil hiçbir mevcut defterin kararını, defterini, karşı-olgusalını, öğrenmesini, ortak deneyimini,
danışmanını, yapı deposunu, grafik analizini, karar günlüğünü ya da config özetini değiştirmez. Uygulanış:

1. **Ayrı öznitelik, `strategy_books` DEĞİL.** Listeye girseydi şuralara sızardı: ana tur kapsamı
   (`_strategy_open_symbols` → tur sembolleri), `_funding_step` yenilemesi (20 sn bütçe), grafik analizi defter döngüsü
   (`_chart_analysis_tour`), ortak deneyim `build_adapters` (`skipped` sayacı), öğrenme görünümleri (`_lm_refresh`,
   `_lm_books`), `_strategy_paper_exit_check`, Box zamanlayıcısı araması. Test: M2X açıkken `strategy_books`,
   `_strategy_open_symbols()` ve bu döngülerin girdileri M2X kapalıyla aynıdır.
2. **Sıra: EN SON.** `_strategy_paper_tour` içinde M2X bütün defterlerden (T2, M2, D4, C4, C4S) SONRA, indeks yazımından
   önce işlenir; D4/C4 tiklerini geciktirmez. M2'nin açılışları M2'nin adımında yakalanır (§1.2), işlem en sonda yapılır.
   Süre kendi tur fazında (`m2x`) ölçülür. Arıza `try/except` ile yutulur; M2X'in arızası hiçbir defteri etkilemez.
3. **Kendi fiyat/ağ erişimi YOK.** `_paper_marks`, canlı sağlayıcının sembol başına paylaşılan 60 sn önbelleğini
   (`runner.live.snapshot`) kullanır. M2X'in bir çağrısı, sonraki ana defter / T2 / M2 kontrollerinin hangi anlık görüntüyü
   alacağını, dolayısıyla stop dolumlarını değiştirebilirdi. M2X YALNIZ turun zaten aldığı `pmarks`/`pbars`ı ve
   izleyicinin tek fiyat partisini kullanır. `_paper_marks`, `exit_check`, sağlayıcı ya da `FundingRates.refresh`
   ÇAĞIRMAZ. İzleyicide M2X tutamacı listeye EN SONA eklenir. M2X sembolleri M2'ninkilerin alt kümesi olduğundan parti
   değişmez. Tek istisna yetim penceresidir (M2 kapattı, M2X aynı turda henüz kapatmadı) ve sayılır. İzleyici kapalıyken
   (yedek `_strategy_paper_exit_check` yolu) M2X yalnız turda ve kapanmış 1h bar uçlarıyla denetlenir. Test: snapshot,
   sağlayıcı ve refresh çağrılarını sayan bir casus. M2X açık ve kapalı koşularda çağrıların sayısı, sembolleri ve sırası
   aynıdır; M2X kodu sıfır çağrı yapar.
4. **config_hash.** `m2x_aggressive` bölümü `cfg.v3`'e girince `engine_v3.config_hash()` ve ortak deneyim toplayıcısının
   yedek özeti (`SharedExperienceCollector.from_engine`) değişirdi. Bu özet karar günlüğüne, ortak deneyim satırlarına,
   danışman metasına ve `chart_analysis` config'ine yazılan karar kimliğidir; `enabled: false` iken bile değişirdi. Bölüm
   İKİ yerde de `shared_experience` gibi düşürülür. Test: bölüm dahil config.yaml ile hesaplanan özet, `1c2c6e2`
   kodunun aynı config'te (bölümsüz) ürettiği değere EŞİTTİR; değer testte sabittir.
5. **Kayıtlar değişmez.** `paper_rules.spec_for`, `learning_mode.BOOK_NAMES`, `shared_experience.rows.BOOKS`,
   `bot_scorecard.BOOKS`. `tests/test_shared_experience_store_v1.py`'deki eşitlik testi aynen geçer.
6. **Defter globlayıcıları.** `bot_scorecard.find_books()` `state/*/futures_ledger.json`ı globlar; M2X'i kendi hükmüyle
   ana tabloya ve AYLIK HEDEF'e otomatik sokardı. Glob'dan ayna klasörleri (`MIRROR_BOOKS`) dışlanır; M2X ayrı bölümdedir
   (§4.2). Başka globlayıcılar:
   - Dağıtım betiklerinin `book_snapshot`ı (`state/*/futures_ledger.json`; yalnız görüntü). Bir sonraki sürüm betiği M2X
     satırını "ayna" diye etiketler ve değişmez sayımlarına katmaz.
   - Öğrenme motoru P1a'nın kapanış arşivi ve günlük hedef modülü (`daily_target.py`, `find_books`/`_window` mantığı;
     başka ekip, `impl/system`). O ekibe not: `kind: mirror` defterler toplamlara ve "pozitif defter" sayımına katılmamalı.
     Bu belge onların kodunu değiştirmez.
7. **Yazma alanı.** M2X yalnız `state/strategy_paper_m2x/` altına, `state/strategy_paper_m2x.json`a ve indeks girdisine
   yazar. M2'nin defterini yalnız OKUR: pozisyon sözlüğü kopyala-yaz olduğundan kilitsiz okuma tutarlıdır; adım farkı
   M2'nin kilidiyle alınır. M2'ye, ortak deneyime, danışmana, karşı-olgusal kaydına, yapı deposuna ve öğreniciye hiçbir
   şey yazmaz. `TradeMemory` tutmaz.
8. **YALNIZ PAPER.** LIVE/LIVE_LIMITED modunda `m2x_aggressive.enabled: true` ConfigError'dur (diğer defterlerin kapısıyla
   aynı). M2 config'te kapalıysa M2X yeni giriş açmaz (`PARENT_DISABLED`). Açık pozisyonlarını yalnız izleyici partisiyle
   korur; parti bu durumda M2X sembolleriyle büyür. M2 zaten kapalı olduğundan bu config M2 için yalıtım iddiasının
   dışındadır.
9. **Tur süresi.** M2X kural ve yapı analizi koşmaz; yükü defter tiki kadardır.

### 1.7 Kabul testi (bayt bayt)

Sabit saat ve sahte sağlayıcıyla (`tests/record_only_tour_runner.py` deseni) kaydedilmiş bir tur dizisi M2X açık ve
kapalı iki kez koşar. İçinde M2 girişleri, M2 kural çıkışı, izleyici stopu, bir veri boşluğu ve funding settlement olur.

**İzin verilen farkların TAM listesi:**

1. `state/strategy_paper_index.json`: M2X girdisi.
2. `state/protective_monitor.json`: `last_run.books` ve `observations` içindeki M2X anahtarı.
3. İlk-gözlem boşluk kayıtları (`MONITORING_GAP_FIRST_OBSERVATION`): yalnız `book` M2X olan satırlar.
4. Tur fazı süreleri (`tour_phases` ve health'teki süre alanları) ile yeni `m2x` fazı.
5. M2X'in kendi dosyaları (`state/strategy_paper_m2x/…`, `state/strategy_paper_m2x.json`).

Geri kalan her şey bayt bayt aynıdır: config_hash, karar günlükleri, `chart_analysis`, ortak deneyim satırları ve durumu,
danışman kayıtları, öğrenme görünümü ve `learning_mode.json`, karşı-olgusallar, yapı deposu, ana defter, T2/M2/Box/D4/
C4/C4S defterleri ve özetleri, trade memory, health.json'un süre dışı alanları.

## 2. Boyut politikası

### 2.1 Tanımlar

- **E (özkaynak):** M2X'in mark'a göre özkaynağı (cüzdan + gerçekleşmemiş). Gözlem noktasında hesaplanır. Her açık
  sembolde, turun doğrulanmış mark'ı ile M2X defterinin en son uygulanan doğrulanmış mark'ından (izleyici) hangisi
  tazeyse o kullanılır; yaşı ≤ 180 sn (`PRICE_MAX_AGE_S`) olmalıdır. Biri yoksa gözlem veri boşluğudur (§2.7).
- **P (gerçek zirve):** günlük görüntülerdeki E'nin koşan en büyüğü. İlk değeri başlangıç (ya da dönem başı) E'sidir.
  Yalnız günlük görüntüde yükselir. **DD = 1 − E / P.** Durdurma ve kriz bütçesi bundan ölçülür.
- **Pₖ (kademe zirvesi):** Pₖ ≤ P. Günlük görüntüde Pₖ := max(Pₖ, E). B anahtarıyla yukarı çıkışta yeniden demirlenir
  (§2.2). **DDₖ = 1 − E / Pₖ.** Kademe DDₖ'den belirlenir. Demirleme yokken Pₖ = P.
- **OR (açık risk):** Σ q × max(0, m − stop). Şu andan itibaren bütün stoplar boşluksuz dolarsa kaybedilecek tutar.
  Girişteki risk (Σ q × (giriş − stop)) bilgi olarak yanında raporlanır.
- **CL (kriz kaybı):** Σ min(q × (m − liq) + 0,005 × q × m, q × m × (X + 0,001)), **X = 0,50**. Birinci terim: fiyat
  likidasyona inerse kayıp; gerçekleşmemiş kâr ile izole marj, likidasyon ücreti dahil (defter likidasyon kaybını izole
  marjla kırpar, `liquidation_outcome`). İkinci terim: likidasyona ulaşmadan %50 düşüşte, en kötü uçtan dolum ve çıkış
  maliyeti. Kaldıraç 1'de liq ≈ 0 olduğundan ikinci terim bağlar. Eleştirinin önerdiği Σ min(izole marj + ücret,
  q × m × X) yerine bu biçim seçildi: E gerçekleşmemiş kârı içerir ve çöküşte o kâr da gider. Önerilen formül kârlı
  pozisyonun kaybını eksik sayardı.
- **Kriz bütçesi:** H_CL = E − 0,5 × P − CL. Yeni girişler giriş sonrası H_CL ≥ 0 kalacak kadar yapılır. Anlamı: girişten
  hemen sonra her pozisyon aynı anda %50 düşse ya da likide olsa, özkaynak 0,5 × P'nin altına inmez.
- **OR boşluğu:** H_OR = kademe tavanı × E − OR.
- **d:** işlem başına hedef risk = kademe riski × E.
- **U (M2'nin birim-R eğrisi):** U = Σ (M2X'in ilk açılışından beri kapanan M2 işlemlerinin net R'si) + Σ (M2'nin açık
  pozisyonlarının mark R'si). Net R = net sonuç ÷ girişteki risk (`features.risk_usdt`); mark R = (m − giriş) / (giriş −
  stop). Her M2 işlemi bir birimdir. Bu yüzden U ne M2X'in boyutuna ne de M2'nin boyut rejimine bağlıdır. U günlük
  görüntüde hesaplanır ve M2X durum dosyasında tutulur (son 60 gün); kapanan R'ler M2'nin geçmişinden tur tur birikir.
  M2'ye yazılmaz. **ΔU20 = U(bugün) − U(20 görüntü önce).**

### 2.2 Kademeler

| Kademe | DDₖ | İşlem başı risk | OR tavanı | Tavana göre tam boy pozisyon |
|---|---|---|---|---|
| K1 | < %15 | %2,0 | %20 | 10 |
| K2 | %15 ≤ DDₖ < %25 | %1,0 | %12 | 12 |
| K3 | %25 ≤ DDₖ < %35 | %0,5 | %6 | 12 |
| K4 (yumuşak durdurma) | %35 ≤ DDₖ, DD < %50 | yeni giriş YOK | — | — |
| DUR | DD ≥ %50 (gerçek zirveden) | yeni giriş YOK; yalnız sahip açar | — | — |

Kriz bütçesi her kademede ayrıca geçerlidir (§2.3).

- **Aşağı inme:** gözlem noktasında DDₖ bir alt sınırı geçmişse hemen o kademeye; birden fazla kademe birden atlanabilir.
  Gözlem noktaları dışında kademe değişmez.
- **Yukarı çıkma:** günlük görüntüde değerlendirilir. Bir görüntü şu iki koşuldan biri sağlanırsa "geçer":
  - **A (defterin kendi toparlanması):** DDₖ ≤ bulunulan kademenin alt sınırı − 5 puan. Eşikler: K2 → K1 için ≤ %10,
    K3 → K2 için ≤ %20, K4 → K3 için ≤ %30.
  - **B (sinyalin toparlanması):** ΔU20 > 0 ve DD < %50. 20 görüntülük U geçmişi yoksa B geçmez.
  - 5 ardışık geçen görüntü → bir kademe yukarı ve sayaç sıfırlanır. Geçmeyen ya da eksik görüntü sayacı sıfırlar.
  - Beşinci görüntüde A geçmiyorsa (çıkış B ile oluyorsa) Pₖ yeniden demirlenir: Pₖ := E / (1 − (eski kademenin alt
    sınırı − 0,05)). DDₖ böylece A'nın eşiğine çekilir. Çıkış bir sonraki gözlemde hemen geri inmez. Yeniden inmek için
    E'nin yeniden %7,1 (K3'ten K4'e), %6,25 (K2'den K3'e) ya da %5,6 (K1'den K2'ye) düşmesi gerekir. P (gerçek zirve)
    DEĞİŞMEZ; durdurma ve kriz bütçesi her zaman gerçek zirveden ölçülür.
- **Gerekçe:**
  1. Aşağı hızlı, yukarı yavaş: sahibin "kötü dönemde temkinli" isteğinin doğrudan karşılığıdır. 5 puanlık bant, sınır
     çevresindeki salınımda her gün kademe değişmesini önler. 5 görüntü M2'nin karar sıklığıdır: günde bir sinyal turu.
  2. Yalnız A ile toparlanma bir tuzaktır. Kademe riski kıstığı için toparlanma, kısılmış defterin kendi kârına bağlı
     kalır. Örneğin ilk taslağın K4'ünde (%0,25 risk, %3 tavan) DD %35'ten %30'a dönmek ~+%7,7 kâr isterdi;
     bu aylar sürebilirdi. K4'te yeni
     giriş hiç yoktur; A yalnız açık pozisyonlarla geçebilir.
  3. B, merdivenin kısmadığı bir ölçüye bakar: M2'nin kendi işlemlerinin eşit ağırlıklı R eğrisine. M2'nin son 20 günü
     artıdaysa ve bu 5 gün sürüyorsa kötü dönem bitmiş sayılır. Bir çöküşten sonra ΔU20 genelde ~20 gün eksi kalır;
     ilk çıkış en erken ~25 gün sonra, sonrakiler 5'er gün arayla olur.
  4. DD'nin mark'a göre ölçülmesi korunur: sahip mark özkaynağını görür. Bilinen bedeli: M2'nin trail'i yoktur
     (close ≤ close[−28] ile çıkar). Her başarılı trendin sonundaki geri veriş düşüş olarak görünür ve kademeyi indirir;
     bir sonraki trendin ilk günleri küçük boyutla geçer. Bu bedel, gerçekleşmiş ve stop değerli zirve kollarıyla (i1, i2)
     ve kademe süre istatistikleriyle ölçülür (§3.5).
  5. K4 neden yumuşak durdurma: %35–50 aralığında %0,25 risk, E ≈ 100–130 USDT'de 0,25–0,33 USDT eder. 5 USDT'lik en küçük
     emirle çoğu işlem min-notional'a takılırdı; kademe pratikte "çok seyrek işlem" olurdu. Kriz bütçesi de bu bölgede
     %23'ten 0'a iner. Ayrı bir küçük-risk kademesi karmaşıklık ekler, koruma eklemez.

### 2.3 Tavanların gerekçesi ve kriz matematiği

**Bu defterde likidasyon nasıl işler (ilk taslağın hatası).** `FuturesLedgerV2` izole marj kullanır. Bir 1h bar ya da
izleyici gözlemi hem stopu hem likidasyonu geçerse ve sıra gözlenmediyse defter likidasyon yazar (`exit_decision`:
`INTRABAR_ORDER_UNOBSERVED`, politika `PRUDENT_LIQUIDATION`). İlk gözlem likidasyonun ötesindeyse
`FIRST_OBSERVATION_BEYOND_LIQUIDATION` yazılır. İki durumda da pozisyon izole marjının tamamını kaybeder; ücret dahil,
kayıp marjla kırpılır. R cinsinden kayıp **1 / (L × s)** olur (s = stop mesafesi oranı), "2R" değil. `fit_size` gereken en
küçük kaldıracı (L_need) seçer. İlk taslak (ve M2) bu kaldıraçla boyutlanır. s = %12'de L = 2 ve kayıp ~4,2R; s = %15'te
L = 2 ve ~3,3R; s = %10'da L = 3 ve ~3,3R. Arşivdeki last-price 1h barları 2025-10-10 fitilini içerir. İlk taslağın
K1'i (10 pozisyon × %2) böyle bir olayda özkaynağın ~%65–85'ini kaybederdi, tablonun yazdığı %49'u değil. Bilgi: aynı
hesap M2'nin kendisi için de geçerlidir. 27 pozisyon × başlangıç bakiyesinin %0,5'i × ~4,2R ≈ başlangıç bakiyesinin
~%56'sı (s = %12, L = 2 varsayımıyla).

**v1'in iki çaresi:**

1. **Kaldıraç L_liq (§2.5):** tampon içinde izin verilen en yüksek kaldıraç (≤ 4). Notional ve R aynı kalır; izole marj
   küçülür ve likidasyon kaybı marjla sınırlanır. Yaklaşık değerler (MMR %0,4, defterin likidasyon formülü):

| s | L_need (M2 ve kol g) | Likidasyonda kayıp (L_need) | L_liq (v1) | Girişten likidasyon | Likidasyonda kayıp (L_liq) | Stoptan likidasyona saatlik ek düşüş: v1 / M2 |
|---|---|---|---|---|---|---|
| %5 | 4 | 5,0R | 4 | %24,7 | 5,0R | %20,7 / %20,7 |
| %8 | 3 | 4,2R | 4 | %24,7 | 3,1R | %18,2 / %27,3 |
| %10 | 3 | 3,3R | 4 | %24,7 | 2,5R | %16,3 / %25,7 |
| %12 | 2 | 4,2R | 4 | %24,7 | 2,1R | %14,4 / %43,0 |
| %15 | 2 | 3,3R | 3 | %33,1 | 2,2R | %21,3 / %40,9 |
| %20 | 2 | 2,5R | 2 | %49,8 | 2,5R | %37,3 / %37,3 |
| %25 | 1 | likidasyon yok; %50 düşüşte 2,0R | 1 | — | likidasyon yok; 2,0R | — |

   Kaldıraç tavanı 4 olduğundan dar stoplarda (s < %12) kayıp 2R'ye inmez. Bu pozisyonlar kriz bütçesinde daha çok yer
   tutar. Son sütun likidasyon ayrışmasının (§1.4a) eşiğidir: v1, M2'nin stopla çıktığı bazı sert saatlerde likide olur.

2. **Kriz bütçesi:** giriş sonrası H_CL ≥ 0. Bir K1 tam boy pozisyonu (s %10–20, L_liq) girişte CL ≈ E'nin %4,2–5'i
   eder. DD = 0'da bütçe E'nin %50'sidir, yani ~10–12 pozisyon; OR tavanı (%20) 10'da bağlar. L_need ile (kol g)
   pozisyon başına CL, s %10–15'te ≈ %6,7–8,3 olur ve bütçe ~6–7 pozisyonda dolar.

**Kademe tablosu — YALNIZ GİRİŞ ANI:**

| Kademe | Kademenin dibi (DD) | OR tavanı | Kriz bütçesi, dipte (E'nin %'si) | Boşluksuz bütün stoplar (dipten düşüş) | %50 kriz / likidasyon (dipten düşüş) |
|---|---|---|---|---|---|
| K1 | %15 | %20 | %41,2 | %32,0 | ≤ %50 (kural gereği) |
| K2 | %25 | %12 | %33,3 | %34,0 | ≤ %50 |
| K3 | %35 | %6 | %23,1 | %38,9 | ≤ %50 |
| K4 | %35–50 | yeni giriş yok | — | — | — |

Formüller: bütçe oranı = (0,5 − b) / (1 − b); boşluksuz stoplar = 1 − (1 − b)(1 − tavan). b, kademenin dibidir.
B ile yukarı çıkıldıysa kademe Pₖ'den, bütçe gerçek P'den ölçülür. Bütçe bu yüzden yine gerçek zirveye bağlı kalır.

**Bu tablo yalnız giriş anını anlatır. Kurallar şunları ZORLAMAZ:**

- Açık pozisyon küçültülmez. Kademe düşünce eski pozisyonlar yerinde kalır. Örnek: K1'de açılmış %20 OR, K2'nin %25'lik
  dibine gelindiğinde hâlâ açıksa, 2 kat kötü dolumlu bir olay 1 − 0,75 × 0,60 = **%55** düşüş verir.
- OR ve CL mark'a göre gerçekleşmemiş kârı içerir; fiyat yükseldikçe ikisi de büyür. Zirve gerçekleşmemiş kârla yükselir.
  Likide olacak pozisyonlarda kriz sonrası özkaynak (≈ cüzdan − Σ izole marj) ise yükselmez. Güçlü bir koşudan sonra tek
  bir çöküş defteri zirveden %50'nin ötesine götürebilir. O durumda H_CL < 0 olur ve yeni giriş durur
  (`M2X_CRASH_BUDGET`). Bedeli, trend sürerken M2'nin sonraki girişlerini kaçırmaktır; bu da sayılır.
- **−%50 bir zarar tavanı değil, giriş durdurmasıdır.** Garanti için çıkışları değiştirmek gerekirdi. Bu M2'den ayrılmak
  olurdu ve tanımın dışındadır; etkisi kollarla ölçülür (h1: durdurmada kapat; h2: kademe düşüşünde tavana küçült).
- Simülasyon şunları raporlar: gözlenen en büyük OR/E ve CL/E; saatlik "kriz sonrası düşüş" serisi
  1 − (E − CL) / P'nin en büyüğü ve %50'yi aştığı saatlerin payı; durdurmadan sonra ulaşılan düşüş (§3.5).

**Beklentiye etkisi (aritmetik, getiri tahmini değil):** M2 tam doluyken (25–29 pozisyon × başlangıç bakiyesinin %0,5'i)
açık riski başlangıç bakiyesinin ~%12,5–14,5'idir. M2X K1'de en çok %20'dir. Yani işlem başına 4 kat; M2 doluyken toplam
risk ~1,4–1,6 kat. Tam 4 kat yalnız M2'nin ≤ 10 pozisyon tuttuğu dönemlerde (trend başları) görülür. Simülasyon
gerçekleşen çarpanı ölçer.

**OR ölçüsünün neden mark'tan alındığı:** tavanın amacı aynı anda gelen kaybı sınırlamaktır. Kazanan pozisyonun stopa kadar
geri verebileceği tutar (kâr dahil) gerçek risktir. Bedeli: güçlü yükselişte kazananların yastığı tavanı doldurur ve yeni
M2 girişleri küçülür ya da atlanır. Girişteki riskle tavanlanan sürüm bilgi kolu (f) olarak raporlanır.

### 2.4 Tavan aşılırsa: ortak küçültme

Bir turda M2'den N aday geldiyse (atlama nedeni olmayanlar):

1. Her aday için tam boy (d) kurulur. OR_i ≈ d; CL_i, adayın L_liq'i ve planlanan miktarıyla §2.1 formülünden hesaplanır.
2. λ = min(1, H_OR / Σ OR_i, H_CL / Σ CL_i). λ ≥ 0,25 ise her aday λ × d riskle açılır; hepsi aynı oranda küçülür.
3. λ < 0,25 ise adaylar M2'nin açılış sırasıyla (M2 defterindeki açılış sırası; deterministik) 0,25 × d riskle, iki
   boşluğa da sığdıkça açılır. Kalanlar, bağlayan boşluğa göre `M2X_OPEN_RISK_CAP` ya da `M2X_CRASH_BUDGET` ile atlanır.
4. Gerçek boyut `fit_size` ve yuvarlamadan sonra kesinleşir. Adaylar sırayla, güncel boşluklarla denetlenir; sığmayan
   atlanır.

**Neden küçültme:** M2'nin sinyalleri aynı günlük kapanışta birlikte gelir. "İlk gelen tam boy" kuralı aynı turdaki
adaylar arasında sonucu sembol sırasına bağlardı. Küçültme, aynı turdaki işlemleri aynı oranda tutar. Turlar arasında
seçim yine zamana göredir (§1.2). %25 tabanı, ücret ve min-notional çıkarmasının anlamsız kılacağı kırıntı pozisyonları
önler.

### 2.5 Marj, kaldıraç, likidasyon tamponu

- Notional `learning_mode.fit_size` ile hesaplanır (aynı kod). Girdiler: `equity` = E; giriş = M2'nin dolum fiyatı;
  `risk_pct` = §2.4'ten çıkan risk ÷ E; `slots` = 10, yani pozisyon başına marj tavanı (1 − 0,05) × E / 10;
  `leverage_max` = 4 (profil 5 ve borsa tavanıyla ayrıca sınırlı); `liq_buffer_mult` = 2,0; `mmr` = max(0,004, sembolün
  planlanan notional'daki bracket MMR'si); `reserve_pct` = 5; `available_margin` = defterin serbest marjı; `hard_cap_pct`
  = 2; min-notional §2.6.
- **Kaldıraç L_liq'e çıkarılır:** `fit_size`'ın hesapladığı `l_liq` (tampon içindeki en yüksek kaldıraç). Notional ve risk
  değişmez, izole marj = notional / L_liq olur. M2'nin kaldıracı her zaman ≤ L_liq'tir.
- **Defterin kendi formülüyle denetim (eleştirinin opsiyonu):** açılıştan önce defterin bracket'lı likidasyon formülüyle
  (`liquidation_price`; MMR ve bakım tutarı) likidasyon mesafesinin ≥ 2 × s olduğu denetlenir. Değilse kaldıraç bir
  indirilir ve yeniden denetlenir; en çok `fit_size`'ın seçtiği kaldıraca kadar. Hâlâ sağlanmıyorsa giriş atlanır
  (`M2X_LIQ_BUFFER`). İndirme sayısı raporlanır.
- Serbest marj yetmezse `fit_size`'ın kendi yolu çalışır (önce kaldıraç, sonra `SHRUNK_TO_MARGIN`). Küçülmüş risk
  0,25 × d'nin altındaysa atlanır (`M2X_MARGIN`). Tampon hiç sağlanamıyorsa (stop > ~%49,8) atlanır
  (`M2X_LIQ_BUFFER`); M2'nin boyutu da aynı kuralla reddeder.
- **RiskEngine:** `PAPER_RESEARCH` profilinin KOPYASI. `size_on_live_equity: true` (yüzdeler canlı özkaynağa uygulanır),
  `max_total_open_risk_pct: 100` (tavanlar §2.2–2.3'te mark ölçüsüyle uygulanır; farklı tanımlı ikinci bir kapı olmasın),
  işlem tavanı %2. `PROFILES`'a yazılmaz.
- **Defter:** `FuturesLedgerV2(200)`; ücret, kayma ve likidasyon parametreleri M2 ile aynı (`use_brackets`, %0,5 ücret);
  `max_positions` 40.
- **Boyut tabanı canlı özkaynaktır:** M2 başlangıç bakiyesinin %0,5'ini riske eder (sabit 1 USDT). M2X canlı E'nin
  yüzdesini riske eder. Kâr büyüdükçe boyut büyür, kayıpta kendiliğinden küçülür. Bu kademelere ek ikinci bir frendir ve
  bilerek seçildi.
- **L_liq'in bedeli:** likidasyon ayrışması (§1.4a). Kol (g) aynı politikayı L_need ile koşar ve iki etkiyi ayırır:
  kriz kaybı ve kapasite.

### 2.6 Min-notional ve miktar adımı

- Notional = risk / s. Borsa filtreleri (min-notional, min miktar, miktar adımı) M2 ile aynı doğrulanmış önbellekten
  okunur.
- Adıma aşağı yuvarlanmış miktar en küçük emrin altındaysa en küçük emre ÇIKARILIR (miktar adıma yukarı). Koşul: çıkan
  risk ≤ min(2 × kademe riski × E, %2 × E) ve giriş sonrası H_OR ≥ 0 ve H_CL ≥ 0. Değilse atlanır (`M2X_MIN_NOTIONAL`).
- K3'te E ≈ 130 USDT iken çıkarma tavanı ≈ 1,3 USDT olur; 5 USDT'lik en küçük emirle s ≤ ~%26 olan işlemler girebilir.
  Simülasyon kademe başına atlanma oranını raporlar.

### 2.7 Gözlem noktaları ve veri boşluğu

- **O1 — günlük görüntü:** 00:00 UTC'den sonra M2X'in işlendiği ve verinin tam olduğu ilk tur. Çıkış ve tik
  adımlarından (§1.4, 1–4) SONRA, girişlerden ÖNCE alınır. P, Pₖ, U, yukarı çıkış sayacı, aşağı ve yukarı kademe değişimi
  ve DUR burada değerlendirilir.
- **O2 — giriş turu:** M2'nin yeni açılış yaptığı her tur. Girişlerden önce o turun E'siyle DD ve DDₖ hesaplanır; aşağı
  inme ve DUR değerlendirilir. Zirve güncellenmez, yukarı çıkılmaz.
- Başka hiçbir anda kademe, zirve ya da DUR değişmez. Panel anlık düşüşü YALNIZ gösterir.
- **Gerekçe:** kademe ve DUR yalnız yeni girişleri etkiler; girişler O2'de olur. Gün içinde her turda (15–20 dk turlar ve
  60 sn mark'lar) kademe indirmek, gün içi diplerin kademeyi aşağı "cırcırlamasına" yol açardı; çıkış ise en az 5 günlük
  görüntü ister. Canlının gözlem ızgarası simülasyonunkinden farklı olurdu ve benzetilen kademe yolu canlıyı temsil
  etmezdi.
- **Simülasyon ızgarası:** tek günlük karar turu 00:05 UTC'dir; O1 ile O2 aynı turdur. Canlıda M2 günlük girişlerini
  00:00 sonrası ilk turda açar ve bu tur O1'dir. Gün içi bir M2 girişi (ör. yeniden başlatma sonrası) canlıda ayrı bir O2
  olur, simülasyonda olmaz. Bu fark E2'de sayılır (§3.3).
- **Veri boşluğu (fail-closed):** açık bir M2X sembolünün 180 sn'den taze doğrulanmış mark'ı yoksa gözlem `M2X_DATA_GAP`
  olur. Kademe, zirve ve DUR değişmez; o turda yeni giriş YOK. O1 aynı UTC gününün sonraki turunda yeniden denenir. Gün
  biterse o günün görüntüsü yoktur ve yukarı çıkış sayacı sıfırlanır. **Veri boşluğu DUR'u asla tetiklemez.** U için
  M2'nin açık pozisyonlarından birinin mark'ı yoksa o günün B'si geçmez. Simülasyonda arşivde bar eksikse aynı kural
  uygulanır.

### 2.8 Durdurma (−%50) ve sahibin yeniden başlatması

- O1 ya da O2'de DD ≥ %50 → `HALTED`. Durum kalıcıdır (yeniden başlatma silmez). Yeni giriş yok. Açık pozisyonlar M2 ile
  çıkmaya devam eder; zorla kapanış yok. Panelde ve `--check`te kırmızı satır görünür.
- Yeniden başlatmayı yalnız sahip yapar, VPS'te komutla: `python -m tradingbot m2x-resume --i-reviewed --note "<neden>"`.
  Komut `state/strategy_paper_m2x/m2x_control.json`a `{request_id, at, note, epoch_expected}` yazar. Worker sonraki
  turda okur:
  - Yalnız `HALTED` iken ve `epoch_expected` güncel döneme eşitse kabul eder. Aksi hâlde `REJECTED_NOT_HALTED` ya da
    `REJECTED_STALE_EPOCH` yazar.
  - Tek kullanımlıktır: istek sonuçla birlikte "tüketildi" diye işaretlenir; aynı `request_id` yeniden işlenmez.
  - Kabul: dönem sayacı +1, P := Pₖ := E, kademe **K3**, sayaç 0. Neden K3: sahip inceledi ama temkin sürer; K1'e dönmek
    en az iki yukarı çıkış (≥ 10 günlük görüntü) ister.
  - Her istek ve sonucu özetin `resume_history` alanına yazılır (son 20).
- Panelde ve karnede başlangıçtan (200 USDT) toplam sonuç ve dönem içi sonuç AYRI gösterilir. Panelde düğme yoktur;
  uzaktan komut yüzeyi açılmaz.

### 2.9 Kill switch ve kapatma

- `m2x_aggressive.enabled: false` → defter yüklenmez. Config.yaml varsayılanı KAPALI; sahip dağıtımda açar.
  `TRADINGBOT_M2X=off` ortam değişkeni yalnız kapatır.
- `m2x_aggressive.new_entries: false` → yeni giriş yok (`M2X_NEW_ENTRIES_OFF`), açık pozisyonlar M2 ile çıkar.
- Önerilen kapatma sırası: önce `new_entries: false`, pozisyonlar bitince `enabled: false`. Kapalı defterde açık pozisyon
  DONAR (C4 notuyla aynı).

### 2.10 Config önerisi (uygulamada)

```yaml
m2x_aggressive:                 # M2X — M2'nin GERÇEK işlemlerini daha büyük boyutla kopyalayan kâğıt defter (PAPER)
  enabled: false                # KILL SWITCH — sahip dağıtımda açar; LIVE modda true ConfigError
  new_entries: true
  parent: m2_tsmom28
  state_dir: strategy_paper_m2x
  starting_equity_usdt: 200
  policy_version: m2x_v1        # sayılar kodda M2X_POLICY_V1 + mühür testi; değişiklik = v2
```

Kademe, tavan, bütçe, X, gözlem ve anahtar sayıları config'te DEĞİL, kodda durur (`tradingbot/m2x_policy.py`,
`M2X_POLICY_V1`). Bir testle sabitlenen `M2X_POLICY_SHA` mührü vardır (gold_v1 deseni). Config'ten sayı değiştirmek ön
kaydı sessizce bozardı. Bölüm `config_hash`'e girmez (§1.6.4).

## 3. Geçmiş simülasyonu (sözleşme)

**Bu bir tanımdır, söz değildir.** Simülasyon politikanın geçmiş veride NASIL davranacağını betimler. Gelecekteki getiri,
düşüş ya da durdurma için söz vermez. Dönem M2'nin çalışmadığı yılları içerir ve evren hayatta kalanlardan seçilmiştir.

### 3.1 Veri

- Yalnız `data.binance.vision` USDⓈ-M arşivi (fapi/api 451 döner): 1d ve 1h klines (last price), aylık `fundingRate`,
  1h `markPriceKlines` (funding settlement mark'ı ve bilgi). İndirme ve önbellek `signal_lab.ArchiveProvider` /
  `load_series` ile. Önbellek `…/scratchpad/m2x/cache`, çıktılar `…/scratchpad/m2x/out`. Fiyat verisi commit edilmez.
- **Evren:** config `coins` (40 sembol, = `entry_universe`, M2'nin bugünkü giriş evreni). Her sembol kendi
  listelenmesinden ve 210 günlük ısınmadan sonra girer (`ema200_trend.MIN_DAILY_BARS`). BTC rejimi için BTC/USDT.
  Liste 2026-09'da hacme ve yaşa göre seçildi; 2023'e uygulanması **hayatta kalan yanlılığı** taşır ve raporda yazılır.
- **Dönem:** kararlar 2023-01-01 → 2026-09-30 (UTC). Isınma verisi 2022-05-01'den. Dönem sonunda açık pozisyonlar mark'la
  değerlenir. İşlem istatistiklerine yalnız kapananlar girer; açık sayısı ayrıca yazılır.
- Eksik bar oranı, 24 saatten uzun boşluklar ve funding kapsamı sembol başına raporlanır. Örnekteki aşırı saatlik
  fitiller listelenir (ölçüt: herhangi bir 1h barda low/open − 1 ≤ −%25). 2025-10-10 ölçeğindeki olay sayısı (beklenti 1)
  her sonucun yanında yazılır.
- **Borsa filtreleri:** VPS'teki doğrulanmış `data/symbol_filters.json`un salt okunur kopyası. Kopya yoksa VARSAYIM: her
  sembolde min-notional 5 USDT, BTC 100, ETH 20; miktar adımı yok; varsayılan bracket'lar. Çıktıda "filtre: varsayım" diye
  işaretlenir; bu durumda BTC/ETH ve bracket denetimi sonuçları kesin değildir.

### 3.2 M2 sinyalinin denkliği: botun kendi üreteci (laboratuvarın TSMOM_28'i DEĞİL)

Sinyal laboratuvarının `TSMOM_28` algoritması M2'ye **denk değildir**:

| | `signal_lab` TSMOM_28 | M2 (`ema200_trend.decide`, `m2_tsmom28`) |
|---|---|---|
| Giriş | 28 bar getirisi işaret DEĞİŞTİRİNCE (≤0 → >0), kesişim başına bir kez | Düzken her gün close > close[−28] ise; stoptan sonra ertesi gün yeniden girer |
| BTC rejimi | yok | BTC günlük close > EMA200 şart |
| Yön | LONG ve SHORT | yalnız LONG |
| Yapı katmanı | yok | ENFORCE: girişi engelleyebilir (öğrenmede yalnız-kayıt), açık pozisyonda yapı çıkışı |
| Süre sınırı | 300 bar | yok |
| Stop yolu | günlük bar, bar içi kötümser | dakikalık mark izleyici + 1h bar uçları + ihtiyatlı likidasyon kuralı |
| Boyut/kapasite | yok | öğrenme `fit_size` (min-notional, slot, tampon); ör. BTC'de en küçük emir çatışması |
| Funding | yok (bilgi) | defterde |

Ortak olan: 3 × ATR14 felaket stopu (ikisi de Wilder EWM ATR), sonraki açılışta giriş ve close ≤ close[−28] çıkışı.
Laboratuvar sonucu M2 kanıtı yerine geçmez. Bu yüzden simülasyonun üreteci botun kendisidir:

- **E1 (kod kimliği):** benzetim gerçek `StrategyBook` nesnesini sürer (ad `m2_tsmom28`, config.yaml'daki M2 ayarları).
  Kural, yapı, öğrenme boyutu ve defter yeniden yazılmaz. Koşum aracı yalnız tesisattır: çerçeve, mark, bar ve saat
  verir. Bir test aracın kural/boyut mantığı içermediğini denetler (yalnız `StrategyBook`, `FuturesLedgerV2`,
  `paper_rules` çağrıları).
- **Kod sürümü:** `1c2c6e2` simüle edilir. M2 yolu VPS'teki `f8b05fb` ile aynıdır: `git diff f8b05fb 1c2c6e2 --
  tradingbot` yalnız `gold_lab.py` ekler ve config.yaml aynıdır. Çıktıya iki SHA da yazılır.
- **Öğrenme görünümü:** motorun kendi kurucusuyla (`LearningMode` / `_lm_refresh`), mod kapısı "aktif" zorlanarak kurulur;
  elle yeniden kurulmaz. Ayarlar config.yaml'dan gelir: risk %0,5, slot 40, kaldıraç ≤ 4, rezerv %5, tampon 2,
  `extra_entries: record_selectivity`, M2 için yapı giriş gölgesi.
- **Karşı-olgusal kapalı koşunun kararı değiştirmediği gösterilir:** 2025-09-01 → 2025-10-31 penceresi (10-10'u içerir)
  karşı-olgusal açık ve kapalı iki kez koşar. İşlem listeleri (sembol, açılış zamanı, stop, miktar, kapanış zamanı,
  neden) bire bir aynı olmalıdır. Değilse bütün simülasyon karşı-olgusal açık koşar.
- **Sandbox:** `state_path` ve `StructureStore` kökü `…/m2x/out/run_<id>/state` altındadır. Bir test bu kök dışında
  yazma olmadığını denetler; gerçek `state/` asla açılmaz.
- **Tur takvimi:** her UTC günü 00:05'te karar turu. Çerçeveler o ana kadar kapanmış 1d ve 1h barlardır. Canlı
  sağlayıcının 1d pencere uzunluğu kadar kuyruk verilir, böylece EMA200 ısınması canlıyla aynı olur; uzunluk koddan okunur
  ve çıktıya yazılır. Mark = 00:00 1h barının açılışı. Ardından saat başı: `apply_closed_bars` (1h uçları, defterin kendi
  sözleşmesi), 1h kapanış mark'ıyla `tick`, funding settlement (arşiv oranı; settlement saatinin mark kline açılışı, yoksa
  perp 1h açılışı, işaretli). M2X aynı akışı §1.4 sırasıyla, aynı girdilerle alır.
- **E3 (bilgi):** laboratuvarın `TSMOM_28` LONG olaylarının kaçı bir M2 girişiyle aynı güne düşüyor. Hükme girmez.

**Bilinen canlı/benzetim farkları (raporda yazılır):** canlıda stop dakikalık mark'la, benzetimde 1h barla denetlenir.
Benzetim ihtiyatlıdır: aynı 1h barda hem stop hem likidasyon geçerse likidasyon sayılır. Bu yüzden ayrıca `STOP_AT_LEVEL`
duyarlılığı raporlanır (defter kaydının `alternative` alanı). Mark yerine last price uçları kullanılır; canlıdaki 1h bar
yolu da öyledir. Giriş zamanı canlıda 00:00–00:20 arasıdır.

### 3.3 E2: canlıya parite

- **Kabul penceresi:** 2026-09-28 21:20 UTC (öğrenme modu VPS'te) → VPS kopyasının tarihi. Config tarihe göre yeniden
  oynatılır: `f8b05fb` dağıtımına kadar `extra_entries` yok (= `open`), sonra `record_selectivity`. `open` kipinin
  `943345c` ile bayt bayt aynı olduğu testle sabittir (DURUM.md); `1c2c6e2` bu dönemi de temsil eder.
- 2026-09-28 öncesi (taban M2: %2, toplam %6; 2026-09-23 öncesinde yapı katmanı yok) kabulün DIŞINDADIR. Bilgi olarak taban
  config'iyle ayrıca karşılaştırılır.
- Ölçüt: VPS'teki M2 defterinin salt okunur kopyası. Eşleşme: sembol + açılış UTC günü + stop farkı ≤ %0,5 + kapanış günü
  ±1 + çıkış nedeni sınıfı. Kabul: iki yönde de işlemlerin ≥ %90'ı eşleşir. Pencere kısadır (~1 hafta); n yanında yazılır.
  Eşleşmeyenler nedeniyle listelenir; gün içi canlı girişleri (§2.7) ayrıca sayılır.
- Kopya yoksa E2 "yapılamadı" yazılır ve sonuçlar "canlı paritesi kanıtsız" diye işaretlenir. E1 yine geçerlidir.

### 3.4 Kollar

M2X'in girdisi kol (a)'nın işlem akışıdır: hangi işlem, ne zaman, hangi stop, hangi çıkış. M2X kolları bu akışa yalnız
kendi politikalarını uygular. Defterleri (kaldıraç, likidasyon, funding, ücret) yine `FuturesLedgerV2` ve uygulanan
`m2x_policy` koduyla koşar. Simülasyon uygulanmış kodla yapılır; politika kodu tek kaynaktır.

| Kol | Tanım | Rol |
|---|---|---|
| (a) | M2 bugünkü config'iyle (öğrenme modu, %0,5, slot 40) | ÖN KAYITLI karşılaştırma; M2X'in girdisi |
| (b) | M2X v1: §2'nin tamamı | ÖN KAYITLI |
| (p) | Orantılı kopya: her M2 işlemine risk = min(kademe riski, kademe OR tavanı / 28) (K1 %0,71, K2 %0,43, K3 %0,21); aynı kriz bütçesi, merdiven ve durdurma | ÖN KAYITLI karşılaştırma |
| (c) | K1 sabit: %2, OR %20, kriz bütçesi, L_liq; kademe ve durdurma yok | bilgi |
| (d) | %2; OR tavanı, kriz bütçesi, kademe ve durdurma yok; L_liq; pozisyon başı marj tavanı yok (yalnız %95 toplam marj) | bilgi: tavan neden var. Bu kol "4 kat" DEĞİLDİR: marj ~20 pozisyon civarında bağlar; "marjın izin verdiği kadar"dır |
| (e) | v1, ama K1 OR tavanı %25 (önerilen başlangıç değeri) | bilgi |
| (f) | v1, ama OR girişteki riskle ölçülür | bilgi |
| (g) | v1, ama kaldıraç L_need (`fit_size`'ın seçimi, M2 ile aynı mantık) | bilgi: L_liq'in likidasyon ayrışması ve kapasite etkisi |
| (h1) | v1 + DUR'da bütün pozisyonlar o turun mark'ıyla kapatılır | bilgi: −%50'nin ötesine ne kadar geçildiği |
| (h2) | v1 + her kademe düşüşünde açık pozisyonlar orantılı küçültülür (OR ≤ yeni tavan ve H_CL ≥ 0) | bilgi |
| (i1) | v1, ama P ve Pₖ gerçekleşmiş özkaynaktan (cüzdan) | bilgi: geri veriş tuzağı |
| (i2) | v1, ama P ve Pₖ stop değerli özkaynaktan (cüzdan + Σ q × (stop − giriş)) | bilgi: geri veriş tuzağı |
| (j) | v1, ama yalnız A anahtarı (ilk taslağın toparlanma kuralı) | bilgi: B'nin etkisi |

Bilgi kolları v1'i DEĞİŞTİRMEZ. Bir bilgi kolu daha iyi görünse bile v1 aynen kalır; değişiklik ayrı ön kayıtla v2 olur.

### 3.5 Çıktılar (kol başına)

- Günlük getiriler: günlük görüntü özkaynağından (00:00 UTC, mark'a göre) rₜ = Eₜ / Eₜ₋₁ − 1.
- Aylık getiriler: UTC takvim ayı, mark'a göre. Bütün aylar listesi, çeyrekler, en kötü ve en iyi ay; ≥ +%1, ≥ 0 ve
  ≤ −%10 olan ayların payı.
- Günlük dağılım: ≥ +%1 olan günlerin payı, HER ZAMAN ≤ −%1 olan günlerin payı ve medyan günlük getiriyle birlikte.
  Yalnız bilgidir, hükme girmez.
- Toplam getiri, yıllık bileşik getiri, en büyük düşüş (günlük görüntü ve 1h mark), su altında geçen süre (en uzun ve
  toplam gün, ortalama toparlanma süresi), en kötü gün, en kötü ay.
- Alt dönemler (yalnız bilgi): 2023, 2024, 2025, 2026-01 → 09.
- M2X'e özgü:
  - her kademede geçen gün sayısı; kademe geçişleri; **en uzun K3 ve K4 kalışı**; anahtarına göre yukarı çıkışlar
    (A / B); durdurma olup olmadığı ve tarihi; **durdurmadan sonra ulaşılan en büyük düşüş**;
  - gözlenen **en büyük OR/E ve CL/E**; saatlik kriz sonrası düşüş serisinin en büyüğü ve %50'yi aştığı saatlerin payı;
    bağlayan tavanın dağılımı (OR / CL / marj);
  - M2 işlemlerinin tam boy, küçültülmüş ve atlanmış payı (kademe ve nedene göre); **kopyalanan, küçültülen ve atlanan
    M2 işlemlerinin M2'deki R'si** (n, ortalama, giriş gününe göre kümelenmiş %95 aralık);
  - ortalama açık risk ve M2'ye oranı (gerçekleşen çarpan); min-notional çıkarması; bracket nedeniyle kaldıraç indirimi;
  - likidasyon sayısı; **likidasyon ayrışması** (M2X likide, M2 stopla çıktı) sayısı ve R maliyeti; diğer ayrışmalar;
    eşleşen işlem başına M2X R − M2 R dağılımı.
- Funding toplamı, ücret toplamı.
- Her M2X kolunda ilk 28 gün "ısınma" diye etiketlenir: M2X boş başlar, M2 ise 25–29 pozisyon tutuyor olabilir.

### 3.6 Politika-tekrar bootstrap'ı (sabit tohum)

- **Girdi:** kol (a)'nın işlem yolları. Her M2 işlemi için giriş zamanı, giriş fiyatı, stop, girişten M2'nin çıkışına kadar
  saatlik OHLC (girişe göre normalize), çıkış zamanı ve nedeni. Ayrıca günlük olarak M2'nin açık pozisyon sayısı ve U.
- **Yeniden örnekleme:** takvim günleri üzerinde dairesel blok bootstrap. Blok 20 gün; duyarlılık için 5 ve 60 gün. Bir
  blok, girişi o blokta olan bütün M2 işlemlerini TAM yollarıyla (blok sonunu aşsa bile) taşır; zaman blokla birlikte
  kayar. Ufuk 365 gün; ayrıca örnek uzunluğunda bir ufuk.
- **Koşum:** her yeniden örnek uygulanan `m2x_policy` kodu ve `FuturesLedgerV2` ile koşar. Her işlem kendi sentetik
  sembolüdür (fiyatlar girişe normalize). Min-notional 5 USDT; miktar adımı yok; funding yok. Bunlar işaretlenir; funding
  ve gerçek filtreler yalnız tarihsel simülasyondadır. Kollar: (b), (p), (c).
- 2.000 yol, tohum 20261005.
- **Çıktılar "yeniden örnekleme sıklıkları"dır, olasılık DEĞİLDİR:** 365 günde en büyük düşüşün ≥ %15 / %25 / %35 / %50
  olma sıklığı; durdurma sıklığı; 12 aylık getirinin medyanı, %5 ve %95 dilimleri; ay ≥ +%1 payı; gün ≥ +%1 ve ≤ −%1
  payları ve medyan gün.
- **Her sayının yanında yazılır:** örnekteki 2025-10-10 ölçeğinde olay sayısı (tek çöküşlü örnek); evrenin hayatta kalan
  yanlılığı; örnekteki rejim sayısı (BTC günlük close'un EMA200 üstünde/altında geçirdiği ayrı dönemlerin sayısı).
  Bootstrap örnekte olmayan bir rejimi üretemez.
- İlk taslaktaki "alt sınır / üst sınır" çifti kaldırıldı (§6, madde 13).
- Tarihsel yolun kendisi (tek gerçekleşme) ayrıca yazılır: hangi kademeye ne zaman inildi, ne zaman çıkıldı.

### 3.7 Yorum kuralları (sonuçtan önce sabit)

- Simülasyon dağıtım için bir kapı değildir. Kararı sahip verir; bu bölüm yalnız neyin raporlanacağını sabitler.
- Kol (b)'nin tarihsel en büyük düşüşü ≥ %50 olursa ya da durdurma tetiklenirse bu raporun ilk satırına yazılır.
  Durdurmadan sonra ulaşılan düşüş de aynı satırdadır. Yeni sayılar (v2) önerilebilir ama v1'in sonucu değiştirilmez ve
  v2 ayrı ön kayıt ister.
- (b)–(d) ayrışmaları > %2 işlem olursa ya da E2 kabulü sağlanmazsa sonuçlar "parite zayıf" başlığıyla verilir.
- M2'nin kanıtı (Bağlam) iyimser etiketini korur. Sonuç belgesi M2X için "kârlı", "kanıtlı" ya da "hedefi tuttu"
  demez; yalnız ölçülen sayıları ve sıklıkları verir.
- Geçmiş performans gelecekteki sonucun göstergesi değildir. Kâğıt sonucudur: gerçek emir defteri, kısmi dolum ve gecikme
  yoktur.

## 4. Raporlama

### 4.1 Özet dosyası (`state/strategy_paper_m2x.json`, `m2x` bloğu)

`policy_version`, `policy_sha`, `epoch` ve dönem başlangıcı, `warmup` (ilk 28 gün), `equity_mtm`, `equity_realised`,
`peak` ve `peak_at` (P), `tier_peak` (Pₖ), `dd_pct` (DD), `tier_dd_pct` (DDₖ), `tier` (K1–K3 | K4_SOFT_HALT | HALTED),
`tier_since`, `risk_pct`, `open_risk_cap_pct`, `open_risk_mark_usdt` ve `_pct`, `open_risk_entry_pct`, `crash_loss_usdt`
ve `_pct`, `crash_budget_pct` (H_CL / E), `binding_cap` (OR | CL | MARGIN | —), `u_curve` (son U, ΔU20),
`step_up_streak` ve son geçen anahtar (A | B), `last_observation` {at, kind: SNAPSHOT | ENTRY, data_gap},
`halted` ve durdurma kaydı, `resume_history` (son 20: request_id, at, note, sonuç, epoch), `today_pct` (son günlük
görüntüden), `mtd_pct` (ay başı görüntüsünden, mark'a göre), `last30_pct`, `realised_mtd_pct`, `realised_last30_pct`,
atlanma sayaçları (nedene ve kademeye göre), `divergences` (türüne göre; likidasyon ayrışması ayrı),
`funding_pending_m2x_only`, `parent` (M2 açık pozisyon sayısı, eşlenmiş pozisyon sayısı, M2 öğrenme durumu ve
`extra_entries` kipi).

### 4.2 Karne (`scripts/bot_scorecard.py`)

- `BOOKS` DEĞİŞMEZ. Yeni `MIRROR_BOOKS = {"strategy_paper_m2x": "M2X agresif (M2 kopyası, PAPER)"}`. `find_books()`
  glob'u `MIRROR_BOOKS` klasörlerini dışlar. Ana tablo ve AYLIK HEDEF bölümü M2X'li ve M2X'siz state'te bayt bayt aynıdır
  (test).
- M2X ayrı bir bölümde, çıktının sonunda: **"M2X AGRESİF (M2 kopyası, PAPER) — hüküm yok: kopyadır, yeni kanıt
  değildir"**. Bağımsız bir hüküm sütunu YOKTUR. Hiçbir "pozitif defter" sayımına ve toplamına girmez.
- Satırlar: kapanmış işlem sayısı ve ortalama R (bilgi); gerçekleşmiş net USDT ve %; özkaynak (mark); P ve DD; Pₖ ve DDₖ;
  kademe ve risk %; OR (giriş ve mark) / tavan; CL / bütçe ve bağlayan tavan; bugün %, ay başından beri % (mark ve
  gerçekleşmiş), son 30 gün %; durum (ÇALIŞIYOR | YUMUŞAK DURDURMA | DURDURULDU); atlanma ve ayrışma sayıları;
  kopyalanan / atlanan M2 işlemlerinin M2'deki ortalama R'si; ısınma etiketi.
- Dosya yoksa (defter hiç açılmadıysa) karne çıktısı AYNEN eskisidir.

### 4.3 `deploy --check`

- Config özetine bir satır: `m2x_aggressive (config): enabled = … · new_entries = … · policy = m2x_v1`.
- Özet dosyasından bir satır: `M2X: özkaynak … · zirve … · düşüş %… (kademe zirvesinden %…) · kademe K? (risk %…, OR
  tavanı %…) · OR %… · kriz kaybı %… / bütçe %… · gerçekleşmiş ay %… · bugün %… · ay %… · son 30 gün %… · DURUM: …`.
  Bugün / ay / 30 gün yüzdeleri her zaman düşüş ve gerçekleşmiş sonucun YANINDA yazılır, satırın başında değil.
- Karne zaten basılır; M2X bölümü onun içindedir. Durdurmada satır `!!` ile başlar.

### 4.4 Panel

Strateji defterleri sayfasında M2X kartı (etiket "M2X · agresif M2 kopyası (PAPER)"):

- Özkaynak eğrisi, P ve Pₖ çizgileri; kademe sınırları (%15 / %25 / %35 / %50) işaretli düşüş göstergesi.
- Güncel kademe ve işlem başı risk. İki çubuk: OR (giriş ve mark) / tavan ile CL / kriz bütçesi. Bağlayan çubuk vurgulanır.
- Bugün %, ay %, son 30 gün %, başlangıçtan toplam %: düşüş ve gerçekleşmiş sonucun yanında; başlık değil.
- Durdurmada kırmızı bant ve "sahip incelemesi gerekli: `m2x-resume`" metni. Düğme yok. Yeniden başlatma geçmişi.
- Pozisyon tablosu: sembol, M2 eş işlem kimliği, M2X/M2 boyut oranı, kaldıraç, likidasyon fiyatı, stop, OR, CL.
- Atlanan M2 girişleri (son 50; neden ve kademe).
- İlk 28 gün "ısınma" etiketi.

## 5. Uygulama sırası (bu belgeden sonra)

0. VPS'ten salt okunur M2 kanıt kırılımı (Bağlam'daki üç dönem): sonuç belgesine yazılır, v1 sayılarını değiştirmez.
1. `tradingbot/m2x_policy.py`: `M2X_POLICY_V1`, mühür, saf fonksiyonlar (kademe, iki anahtar, demirleme, CL, ortak
   küçültme, L_liq ve bracket denetimi). Birim testleri.
2. `M2xBook` (ayna defter); motor bağlantısı (ayrı öznitelik, EN SON işlem, M2 adımında yakalama, `m2x` tur fazı,
   izleyici tutamacı en sonda); `m2x-resume` komutu; config doğrulaması (yalnız PAPER); `config_hash`'te iki yerde düşürme.
3. Yalıtım testleri: §1.7 kabul testi, çağrı casusu, `strategy_books` eşitliği, sabit config_hash.
4. Karne (`MIRROR_BOOKS` dışlaması), `--check`, panel.
5. Simülasyon ve bootstrap (§3), uygulanan kodla. Sonuçlar `docs/M2_AGGRESSIVE_V1_RESULTS.md`.
6. Dağıtım: config `enabled: false` ile gider; sahip açar. P1a ekibine `kind: mirror` notu iletilir.

## 6. Eleştiriye yanıt

Eleştirinin zorunlu 14 maddesi ve 6 opsiyonu. "Kabul" = belgeye işlendi.

1. **Kanıt ve zaman çizelgesi — kabul, bir kısmı ertelendi.** Dönem tablosu, karışık dönem uyarısı, "+%7,42"nin ne olduğu,
   iyimser aralık etiketi eklendi; "kanıtlanmış artı" ifadesi kaldırıldı (Bağlam). Dönem başına işlem sayısı bu çalışma
   alanında hesaplanamaz, çünkü işlem listesi yalnız VPS'tedir. Uygulama adım 0'a kondu (§5). Kümelenmiş aralık sonuç
   belgesinde verilecek. `record_selectivity`nin başlangıcı için eleştiri 10-02/03 diyor; repoda yalnız kod tarihi
   (10-03) ve sürüm betiği tarihi (10-04) var, dağıtım saati VPS kaydından alınacak.
2. **Kriz stresi bu defter için yanlıştı — kabul.** İzole marj ve ihtiyatlı likidasyon anlatıldı; kayıp 1/(L × s) R ve
   ilk taslağın ~%65–85'lik kaybı yazıldı (§2.3). İki çare birlikte alındı: kaldıraç L_liq ve kriz bütçesi CL. CL'nin
   biçimi mark tabanına göre düzeltildi: gerçekleşmemiş kâr dahil edildi (§2.1). X = %50 sabit; sembol başına 10-10 fitili
   yerine tek değer seçildi, çünkü yeni listelenen semboller için de tanımlı olmalı ve sonuç görmeden belirlenebilmeli.
   Bütçe kademe başına sabit sayı yerine sürekli formüldür: E − 0,5 × P. Bu, (1 − b)(1 − CL/E) ≥ 0,5 koşulunun her DD için
   eşit biçimidir. L_liq'in likidasyon ayrışması ölçülür (§1.4a, kol g). §2.3 tablosu yeniden hesaplandı.
3. **Tablo OR ≤ tavanı varsayıyordu — kabul.** Tablo "yalnız giriş anı" diye etiketlendi; %55 örneği yazıldı; en büyük
   OR ve CL, saatlik kriz sonrası düşüş ve durdurma sonrası düşüş raporlanacak; kollar h1 (durdurmada kapat) ve h2
   (tavana küçült) eklendi. Sahibe özet (§0.1) "−%50 giriş durdurmasıdır, zarar tavanı değildir" diyor.
4. **Gözlem ızgarası — kabul.** Kademe, zirve ve DUR yalnız O1 (günlük görüntü) ve O2 (M2 giriş turu) anlarında;
   simülasyonda ikisi aynı tur (§2.7). Veri boşluğu fail-closed: 180 sn yaş sınırı, boşlukta giriş yok, DUR asla
   tetiklenmez.
5. **Zirve / geri veriş / merdiven tuzağı — kabul.** Yukarı çıkışa merdivenin kısmadığı B anahtarı (M2'nin birim-R
   eğrisi, ΔU20 > 0) eklendi; B ile çıkışta Pₖ demirlenir, P değişmez (§2.2). Gerçekleşmiş ve stop değerli zirve kolları
   (i1, i2), yalnız-A kolu (j), kademe süreleri ve en uzun K3/K4 kalışı ön kayıtlı. Mark tabanlı düşüş bilinen bedeliyle
   korundu (§2.2, gerekçe 4).
6. **"Saf boyut dönüşümü" iddiası — kabul (eleştirinin ikinci seçeneği).** İddia geri çekildi; tasarım korundu; orantılı
   kopya (p) ön kayıtlı karşılaştırma kolu oldu; kopyalanan / atlanan M2 işlemlerinin R'si raporlanır (§1.2, §3.4,
   §3.5). Neden orantılı kopya ana kol olmadı: sahibin isteği daha yüksek getiri ve orantılı kopyada işlem başı risk M2'nin
   yalnız ~1,4 katıdır.
7. **`strategy_books`'a girmemek — kabul.** Ayrı öznitelik, sızıntı listesi, test; M2X EN SON işlenir; aynı adımda açılıp
   kapananlar dahil yakalama (§1.2, §1.6.1–2).
8. **Kendi fiyat/ağ erişimi yok — kabul.** Yalnız turun `pmarks`/`pbars`ı ve izleyicinin partisi; funding yalnız
   bellekten, yenileme listesine ekleme yok; M2X'e özgü bekleyen funding'in nasıl beklediği yazıldı; çağrı casusu testi
   (§1.5, §1.6.3).
9. **config_hash — kabul.** İki yerde düşürme ve `1c2c6e2` değerine sabit test (§1.6.4).
10. **Karne glob'u — kabul.** `MIRROR_BOOKS` dışlaması, hüküm yok, sayımlara girmez; `book_snapshot` ve P1a için not;
    indekste `kind: mirror` ve benzersiz `name` (§1.1, §1.6.6, §4.2).
11. **Kabul testi — kabul.** Sabit saat, sahte sağlayıcı ve izin verilen farkların tam listesi (§1.7).
12. **Simülasyon sadakati — kabul.** (a) E2 kabul penceresi öğrenme modu dönemine indirildi, config tarihe göre
    oynatılır, kod sürümü yazıldı (`1c2c6e2` = M2 yolunda `f8b05fb`). (b) Öğrenme görünümü motorun kurucusuyla.
    (c) Karşı-olgusal açık/kapalı eşitlik denetimi. (d) Sandbox. (e) Kol (d)'nin marjla sınırlı olduğu açıkça yazıldı ve
    pozisyon başı marj tavanı kaldırıldı (§3.2–3.4).
13. **Bootstrap — kabul.** Alt/üst sınır çifti kaldırıldı. Politika-tekrar bootstrap'ı uygulanan kodla koşar. Çıktılar
    "yeniden örnekleme sıklıkları"dır; her sayının yanında tek çöküşlü örnek, hayatta kalan yanlılığı ve rejim sayısı
    yazılır (§3.6).
14. **"Günlük +%1" — kısmen reddedildi.** Eleştiri sahibin günlük hedefinin belgelenmediğini söylüyor. Belgelenmiş:
    `docs/SYSTEM_LEARNING_ENGINE_V1.md` §1.1, "Günde en az +%1 (tek coin/altın veya toplam)" (`impl/system`, `a9be1d3`).
    Kaynak Bağlam'da gösterildi. Geri kalanı kabul: ≥ +%1 gün payı hep ≤ −%1 gün payı ve medyan günle birlikte verilir;
    panelde ve `--check`te bugün / ay / 30 gün yüzdeleri düşüş ve gerçekleşmiş sonucun yanındadır, başlık değildir
    (§3.5, §4.3, §4.4).

Opsiyonlar:

- **Bracket MMR denetimi — kabul.** `fit_size`'a sembolün bracket MMR'si verilir ve açılıştan önce defterin kendi
  likidasyon formülüyle denetlenir; gerekirse kaldıraç indirilir (§2.5).
- **K4'ü yumuşak durdurmaya katmak — kabul** (§2.2, gerekçe 5).
- **Kendi tur fazı — kabul** (`m2x`, §1.6.2).
- **Panelde iki OR ölçüsü ve CL, bağlayan çubuk — kabul** (§4.4).
- **İlk açılıştaki ısınma — kabul.** İlk 28 gün "ısınma" etiketli; bu dönemde hüküm benzeri ifade yok (§3.5, §4).
- **`m2x-resume` kontrol dosyası şeması — kabul.** Tek kullanım, yalnız `HALTED` iken, dönem sayacı ve `resume_history`
  (§2.8, §4.1).

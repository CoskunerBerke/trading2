# Motorun "kendi beyni" v1 — P3, P4 ve otomatik PAPER terfisi (tasarım ve ön kayıt)

**Durum:** TASARIM ve ÖN KAYIT, kod yok (2026-10-06). Dal `impl/engine-p3-design`, taban `cc88e9b` (P1a, P1b, P2a, P2b
kodu `tradingbot/research_engine/` altında). Bu belge `docs/SYSTEM_LEARNING_ENGINE_V1.md`'nin ("ana belge") §5.7–§5.9,
§6 ve §10 P3/P4/P7'sinin **yapılabilir ayrıntısıdır**. Ana belgenin mühürlü ve kayıtlı metni (eşikler, kapılar, bakışlar,
alfa) DEĞİŞMEZ; burada yalnız okunuşları kesinleşir ve sahip kararı kurulur. Bu belgeyle ana belge çelişirse **daha sıkı
olan** geçerlidir. Bu belgenin §2–§7'si P3a mühür commit'inde (§8.2) koda dökülür; ondan sonra her değişiklik yeni sürüm
(`_v2`) + yeni deneme sayımı + tarihli "Değişiklik" notudur.

**Sahip kararları (2026-10-06).** (1) Kapı A + Kapı B + aylık Holm'u geçen aday, terfi başına onay sorulmadan **yeni bir
PAPER defteri** olarak açılır; gerçek para asla; mevcut defterler değişmez; işlem başı risk %0,25; önceden kayıtlı durdurma
ve SAPMA izlemesiyle defter kendini kapatır; sahip her açılışı/kapanışı özetten ve `--check`'ten görür ve tek komutla
kapatır. Metin ana belge §6.7'deki tarihli "Değişiklik"tir (commit `ec44ce3`, PR dalı `claude/gifted-knuth-0ehpcs`; bu
dalda henüz yok). (2) Kapılar ve eşikler bu karardan sonra **gevşetilmez** (§8.2'deki "asla gevşetme" testi).

**Kısa özet.** Motor her gece, ağsız ve AI'sız, 106 önceden yazılmış varyantı coin coin, küme küme ve havuzda dener (818
kapı satırı); her denemeyi silinmez bir deftere yazar; çoklu testi (BH, DSR, PBO, plasebo) bütün geçmiş denemelerle (≥ 4.513
ham deneme) ödetir; her işlemin neden kazandığını/kaybettiğini dersler hâlinde biriktirir; mühürden sonra gelen görülmemiş
veride her şeyi izler; Kapı A'yı geçeni kendi kâğıt defterinde ileriye oynatır; Kapı B + Holm'u geçen ve worker'ın birebir
çalıştırabildiği adayı **mühürlü, imzalı bir manifest** ile yeni bir PAPER defteri olarak açtırır. Worker yalnız derlenmiş
kapalı bir katalogdaki kuralları, sert tavanlarla, mevcut defterlere hiçbir yol açmadan çalıştırır.

---

## 1. Kapsam, dürüst beklenti, isteğin bileşenlere karşılığı

### 1.1 Kapsam

| İçinde | Dışında |
|---|---|
| P3: `LIB_v1`, coin'e özel arama, walk-forward, ADVERSE/STRESS maliyet, zaman noktasında (PIT) evren, deneme defteri, BH/DSR/CSCV-PBO, plasebo, dersler, keşif katmanı, lider tablosu | mevcut defterlerin herhangi bir kararını değiştirmek (yalnız yazılı öneri, sahip onayı, normal sürüm) |
| P4: kayıt-yalnız ileri adaylar, Kapı B bakışları, aylık Holm | gerçek para, LIVE/TESTNET emir yolu |
| AUTO: motorda terfi manifesti + geri çekme isteği; worker'da tek seferlik `AUTO_v1` yeteneği (veriyle sürülen defter tipi) | LLM, panel sayfaları (P5), worker kayıt enstrümantasyonu (P6) |

### 1.2 Dürüst beklenti

- **Geçmiş:** gold_v1 0/32, gold_v2 0/24, fut_v1 0/8, fut_v2 0/8, book_v1 hedef 0/54; canlıda tek artı defter M2
  (+0,12R / 49 işlem). Bu motor bunları yeniden satmaz; deneme olarak sayar (§2.6).
- **Kapı A çok sıkıdır ve sıkı kalır.** Kümülatif N ≈ 4.513 iken DSR eşiği, satırlar arası günlük Sharpe sapmasının ≈ 3,66
  katıdır (N = 500'de 3,05; 10.000'de 3,86). Coin'e özel arama eşiği logaritmik büyütür; bedeli budur.
- **En erken tarihler** (LIB_v1 mührü 2026-10-25 varsayımıyla; §8.3): 5m/15m taktikleri için en erken otomatik defter
  ≈ 2027-02-02 (ama 5m taktikleri `AUTO_v1`'de otomatiğe uygun değildir, §7.5), 4h/1d taktikleri için ≈ 2027-03-02.
  Tek coinde günlük taktik ≥ 100 OOS işlem kuralı yüzünden çoğu zaman yıllarca "VERİ YETERSİZ" kalır.
- **En olası sonuç:** ilk 6 ayda **0** otomatik defter. Bir şey geçerse beklenen avantaj küçüktür (işlem başı +0,05…+0,2R);
  1 USDT riskle (§7.6) günde birkaç sent–1 USDT eder. **Otomatik defterler +%1/gün hedefini tutturmaz**; amaçları coin'e
  özel fikirleri güvenle ileriye denemektir.
- **Yanlış terfi:** aylık Holm aile hatası 0,05'tir; hiçbir yerde gerçek avantaj yokken bile yılda en çok ≈ 0,6 yanlış
  terfi üst sınırı vardır (gerçekte daha küçük olması beklenir ama ölçülemez). Bu yüzden tavanlar, otomatik emeklilik ve
  "kanıt değil, ileri test" etiketi zorunludur (§7).
- **"İnsandan zeki" iddiası yoktur.** Motorun üstünlüğü disiplindir: izin verilen her şeyi dener, her denemeyi sayar,
  başarısız fikri unutmaz, her kazancı/kaybı kanıtla açıklar ve kendi hatasını otomatik kapatır.

### 1.3 Sahibin isteği → bileşen

Sahibin sözü (aynen): *"ben istiyorum ki kendi beyini olsun ve insandan daha zekice düşünüp neden zarar veya kar ettiğini
anlayıp o coine özel algoritma geliştirip, kar etmeye çalışmasını istiyorum"*; hedef: *"günde ya en az bir coinden veya
altından %1 veya bütün coinlerin ve altının toplam karı %1"*.

| İstek parçası | Bileşen |
|---|---|
| "kendi beyni olsun" | gece döngüsü S4–S6: kütüphane → walk-forward → keşif → dersler → aday → kapı → terfi; insan beklemez (§2–§7) |
| "neden zarar veya kâr ettiğini anlayıp" | P2b atıf kodları + `cfgrid_v1` + R ayrıştırması → ders istatistiği → ders deposu (§4); otomatik defterlerin işlemleri de aynı günlüğe ve atıfa girer (§7.10) |
| "o coine özel algoritma geliştirip" | coin başına satırlar: `COIN_FIT` (aile ızgarasından coin'e seçim, hiyerarşik büzülmeli) ve canlı defter kopyalarının coin satırları; coin hücreli dersler; tek coin'lik otomatik defter (§2.3, §2.4, §4.3) |
| "kâr etmeye çalışsın" | Kapı A → P4 ileri aday → Kapı B → Holm → otomatik PAPER defteri (§6, §7) |
| "%1 / gün" | `tgt_v1` ölçümü değişmez; otomatik defterler ayrı `AUTO_PAPER` satırında ölçülür (§7.10, soru S4) |
| "her işlem maliyet/giriş/boyut/kaldıraç/taktikle kaydedilsin" | otomatik defter `FuturesLedgerV2` kaydı `promo_id`, `variant_id`, `lib_sha`, `risk_usdt`, `initial_stop` taşır; `tj_v1`'e girer |
| "kayıp/kazanç nedeni öğrenilip yeniden kullanılsın" | ders → önceden kayıtlı dönüşüm → yeni spec (yeni deneme) → aynı kapılar (§4.3) |

---

## 2. Strateji kütüphanesi `LIB_v1`

### 2.1 Varyant sözleşmesi, çekirdek ve parite

- **Saf kural.** Her varyant yeni paket `tradingbot/strategy_lib/` içinde saf bir fonksiyondur (G/Ç yok, saat yok, ağ yok;
  AST testi): `signals(bars, ctx) -> list[Intent]` ve `manage(pos, bars, ctx) -> Action`. `bars` = `{tf: kapanmış bar
  dizisi}`, `ctx` = `{now_ms, btc_bars?, funding?}`. `Intent` = `{signal_ts, side, entry_ref, stop, targets[],
  exit_rule, max_hold_bars, one_entry_per_signal, risk_atr_bounds}`; `Action` ∈ {`NONE`, `CLOSE(reason)`,
  `MOVE_STOP(price)`} ve yalnız kapanmış barda değerlendirilir. Paket `research_engine`'i import etmez; motor ve worker
  aynı modülü kullanır (tek kaynak).
- **Kopyalar** (`K_*`) yeniden yazılmaz: `paper_rules.decide_from_rows` ve `pattern_trader` kural fonksiyonları çağrılır.
- **Kimlik.** `spec_sha` = sha256(kanonik JSON `{family, variant_id, params, timeframes, exits, sizing, module_src_sha}`).
  Aynı `spec_sha` iki kez deneme sayılmaz (tekilleştirme); farklı kapsam farklı satırdır.
- **Çekirdek** `research_engine/kernel.py`: karar bar kapanışında, dolum sonraki barın açılışında ± kayma; bar içi sıra
  `learning_cf` kuralı (açılış → ters → lehte → kapanış); stop seviyede, boşlukta açılışta; fonlama deponun **gerçek
  uzlaşma zaman damgalarında**; likidasyon bracket formülüyle; büyüklük sabit 1 USDT risk (200 USDT × %0,5; keşifle aynı),
  kaldıraç büyüklükten türer, likidasyon mesafesi ≥ 2 × stop. Üç maliyet senaryosu aynı yol üzerinde aritmetik yeniden
  maliyetlendirmeyle hesaplanır (§3.3).
- **Parite (kabul):** (a) çekirdek ↔ `FuturesLedgerV2` + `strategy_paper.apply_action` + `tick`: aile başına ≥ 200 sentetik
  işlemde sinyal kimliği %100, medyan |ΔR| ≤ 0,02, p95 ≤ 0,05; (b) kopya ↔ canlı defter: ana belge §6.2 (yalnız POLICY
  kohortu, ≥ 20 girişli her config döneminde ≥ %95 aynı bar/yön, medyan |ΔR| ≤ 0,05). (b) tutmayan kopya lider tablosuna ve
  kapıya girmez (`PARITY_FAIL`).

### 2.2 Aileler, ızgaralar ve sayılar (mühürlü)

Varsayılanlar: R birimi = ilk stop; `max_hold` aksi yazılmadıkça 300 sinyal barı; LO = yalnız long, LS = iki yön;
ER20 = 20 barlık verim oranı (Kaufman); ATR = ATR14 (sinyal dilimi). "Oto" = otomatik PAPER kataloğuna uygun (§7.5).

| Kod | Aile | Varyantlar (kimlik deseni ve ızgara) | Dilim | Adet | Oto |
|---|---|---|---|---|---|
| K | Canlı defter kopyaları | `K_T2`, `K_M2`, `K_BOX` (canlı parametreler, en az stop %0,5), `K_D4`, `K_C4_CV001`…`K_C4_CV008`, `K_FM` (formasyon `momentum_4h_v3`) | defterin kendi | 13 | `K_BOX`, `K_FM` hariç |
| B | TSMOM | `TS_{7,14,28,56,84}_{LO,LS}`: L günlük getiri işareti; stop 3 ATR; çıkış işaret dönünce | 1d | 10 | evet |
| C | Ortalama kesişimi | `MAX_{20_50,50_200}_{4h,1d}_{LO,LS}`: EMA kesişimi; stop 3 ATR; çıkış ters kesişim | 4h, 1d | 8 | evet |
| D | Donchian | `DON_{20_10,55_20}_{4h,1d}_{LO,LS}`: kanal kırılımı; stop 2 ATR; çıkış karşı kanal. `DON_20_10_4h_LO` ≡ `K_D4` (aynı `spec_sha`, tekilleşir) | 4h, 1d | 7 yeni | evet |
| E | Oynaklık kırılımı | `KEL_20_2_{1h,4h}` (Keltner 20, 2 ATR kapanış kırılımı), `BBSQ_20_{1h,4h}` (BB(20,2) genişliği son 120 barın ≤ %20'liğinde iken kırılım); LS; stop 1,5 ATR; hedef 2R; `max_hold` 48 | 1h, 4h | 4 | yalnız 4h (2) |
| F1 | Aralık dönüşü | `RSI2_{1h,4h}` (RSI2 < 10 long / > 90 short), `BBRE_{1h,4h}` (BB(20,2) dışına çıkıp içine kapanış); yalnız ER20 < 0,3; stop 1,5 ATR; çıkış orta bant; `max_hold` 24 | 1h, 4h | 4 | yalnız 4h (2) |
| F2 | Box ızgarası | `BOX_n{10,20}_{mid,2r}_ms{0.5,1.0}`: kenar bandı × çıkış × en az stop %; `BOX_n10_mid_ms0.5` ≡ `K_BOX` (tekilleşir) | 1d + 5m | 7 yeni | hayır (5m) |
| H | Fonlama | `FUND_FADE_{24h,72h}` (f8 son 20 günün ≥ %95'liği → short, ≤ %5'liği → long), `FUND_FADE_LOW_{24h,72h}` (aynısı, yalnız ER20 < 0,3), `FUND_CARRY` (\|f8\| ≥ %90'lık iken fonlama alan taraf; \|f8\| < %50'lik olunca çık); stop 3 ATR(4h) | 4h + fonlama | 5 | hayır (veri) |
| I | BTC–ETH spread | `BTCETH_Z_{2.0,2.5}`: log oranın 20 günlük z'si; z = 0'da çık; en çok 72 saat; iki bacak | 1h | 2 | hayır |
| J | Kesitsel (XSEC) | `XS_MOM_{7,28}_k{3,5}` (haftalık, üst-k long / alt-k short, BTC-beta nötr), `XS_REV_1D_k5` | 1d | 5 | hayır |
| G | Altına özgü | `G_LDN_ORB` (07:00–08:00 UTC aralık kırılımı 12:00'ye kadar, 16:00'da çık), `G_NY_ORB` (13:00–14:00 aralığı, 20:00'de çık), `G_ASIA_DRIFT` (önceki gün yönünde 23:00 → 07:00), `G_MONTH_END` (ayın son 2 UTC günü long), `G_XAU_BTC_{28,56}` (XAU/BTC oranı momentumu > 0 iken long) | 1h, 1d | 6 | hayır (veri) |
| O | Örtüler (bir seferde bir faktör) | taban {T2, M2, BOX, D4, C4} × {`F_ER` (trend tabanında ER20 ≥ 0,3, Box'ta < 0,3), `F_BTC` (BTC 4h rejimi aynı yön), `F_VOL` (ATR% ≤ son 120 barın %80'liği), `X_TRAIL2` (+1R'den sonra 2 ATR iz süren), `X_CHAN3` (chandelier 3 × ATR22), `X_TIME` (`max_hold`'un yarısı; Box: 48 × 5m), `X_TPL` (1R/2R/3R'de üçte bir)} | tabanınki | 35 | T2/M2/D4/C4 × {F_ER, F_BTC, F_VOL, X_TRAIL2, X_CHAN3, X_TIME} = 24 |

**Toplam benzersiz varyant: 13 + 10 + 8 + 7 + 4 + 4 + 7 + 5 + 2 + 5 + 6 + 35 = 106.** Aile başına üst sınır (ana belge §6.2,
"≤ 8–12") O ailesinde taban başına 7 olarak uygulanır (beş ayrı alt aile). Örtüler yalnız bu beş canlı tabana uygulanır
(sonuca bakılarak seçilmiş bir alt küme değildir: dersler bu tabanların gerçek işlemlerinden gelir).

### 2.3 Kapsamlar (deneme satırları)

| Kapsam | Tanım | Hangi varyantlar |
|---|---|---|
| `POOLED_P23` | aynı parametre, P23'ün 23 coin'i; işlemler birleştirilir | B, C, D, E, F1, F2, H (45 varyant); kopyalar ve örtüler kendi canlı sembol kümelerinde (`POOLED_OWN`) |
| `CL_MAJ`, `CL_L1`, `CL_ALT` | aynı parametre, bir kümenin coin'leri | B…H (45) ve örtüler (35) |
| `COIN_FIT:<SYM>` | aile ızgarasından **coin'e özel** seçim (§3.2), P23'ün her coin'i | B, C, D, E, F1, F2, H aileleri (7 aile × 23) |
| `COIN:<SYM>` | bir kopyanın tek coin'de sonucu ("D4 yalnız SOL'da") | 13 kopya × 23 coin |
| `PORT_XSEC`, `PAIR_BTCETH` | portföy/çift | J, I |
| `XAU`, `PAXG`, `GOLD_POOLED` | XAUUSDT vadeli, PAXGUSDT vadeli, ikisi birlikte; uzun vekiller (PAXG spot 2020+, Dukascopy XAUUSD) yalnız gösterim, kapıya girmez | G |

**P23** (mühürlü; D4/C4'ün turda zaten çekilen 23 coin'i): BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, LTC, DOT, NEAR,
TRX, BCH, UNI, FIL, XLM, AAVE, APT, ARB, OP, INJ, SUI (hepsi `/USDT` USDⓈ-M). **Kümeler** (performansa değil, mühür anındaki
sabit niteliğe göre): `CL_MAJ` = {BTC, ETH}; `CL_L1` = {BNB, SOL, XRP, DOGE, ADA, AVAX, LINK, LTC, DOT, TRX, BCH};
`CL_ALT` = {NEAR, UNI, FIL, XLM, AAVE, APT, ARB, OP, INJ, SUI}. Giriş evreninin geri kalanı (yeni listelenenler) yalnız
keşif katmanında izlenir; kısa geçmişle coin satırı açılmaz.

### 2.4 Coin'e özel uzmanlaşmanın aşırı uydurmayı sınırlayan kuralları (mühürlü)

1. **En az veri (çıktıya kör).** Bir coin `COIN_FIT`/`COIN` satırı alabilmek için: P23'te; ilk test katmanından önce ≥ 730
   gün (1h ve üstü) veya ≥ 240 gün (5m) geçmiş; boşluk ≤ %1 ve arşivle doğrulanmış satır payı ≥ %99; varyantın (veya aile
   ızgarasının medyanının) mühür öncesi tüm pencerede o coin'de **ürettiği işlem sayısı** ≥ 100. Sayım yalnız girişleri
   sayar, sonuçlara bakmaz; ilk gece hesaplanıp `trials.jsonl`'a yazılır ve donar.
2. **Hiyerarşik büzülme (seçim).** `COIN_FIT`'te coin c için varyant v'nin eğitim puanı
   `s_c(v) = (n_c·r̄_c + k·r̃_K) / (n_c + k)`, `r̃_K = (n_K·r̄_K + k·r̄_P) / (n_K + k)`, k = 50 işlem; r̄ = ADVERSE ortalama net
   R; K = coin'in kümesi (coin hariç), P = havuz. Seçim §3.2'deki üç yollu düzenle yapılır; fark < 0,02R ise havuzun seçimi
   alınır. Böylece coin, ancak kendi kanıtı güçlüyse havuzdan ayrılır.
3. **Küme tutarlılığı (Kapı A'ya ek ön kayıt koşulu; gevşetme değil).** Bir `COIN_FIT`/`COIN` satırı Kapı A'yı ancak aynı
   seçimin/kopyanın **coin hariç kümesindeki** OOS ADVERSE ortalama net R'ı ≥ 0 ise geçer (altındaki XAU–PAXG tutarlılığının
   coin karşılığı).
4. **Tek enstrüman payı (§6.7).** Havuz ve küme satırlarında tek enstrüman OOS kârın en çok %50'si olabilir; coin satırları
   tasarım gereği tek enstrümanlıdır (muaf; yerine madde 3).
5. **Tam sayım.** Coin satırları BH ailesine ve DSR N'ine tam girer; `COIN_FIT`'in her ızgara seçeneği ayrı ham deneme sayılır
   (§2.5).
6. **Terfi tarafı.** Aynı coin'de en çok 1, toplamda en çok 2 tek-coin otomatik defter (§7.6).

### 2.5 Deneme bütçesi (kesin sayılar)

| Satır türü | Hesap | Kapı A satırı | Ham deneme (DSR N) |
|---|---|---|---|
| Havuz | 45 varyant × 1 | 45 | 45 |
| Küme | 45 × 3 | 135 | 135 |
| `COIN_FIT` | 7 aile × 23 coin; seçenek = ızgara (B 10, C 8, D 8, E 4, F1 4, F2 8, H 5 = 47) | 161 | 47 × 23 = 1.081 |
| Kopya | 13 × (kendi kümesi 1 + 23 coin) | 312 | 312 |
| Örtü | 35 × (kendi kümesi 1 + 3 küme) | 140 | 140 |
| XSEC + spread + altın | 5 + 2 + 6 × 3 | 25 | 25 |
| **LIB_v1 toplam** | | **818** | **1.738** |
| Öncül (içe alınan, §2.6) | belgelenmiş alt sınır | — | **≥ 2.775** |
| **Mühürde kümülatif N** | | | **≥ 4.513** |

- Üst sınırdır: §2.4 madde 1'i geçmeyen coin satırı açılmaz ama **yine sayılır** (seçenek değerlendirildiyse). Tersine,
  sayılar mühürden sonra küçültülemez.
- Keşif katmanı ayrıca 3.745 seri izler (§5.1): 45 × 40 + 13 × 40 + 35 × 40 + 5 + 2 + 18. Bunlar v1'in kapısına girmez,
  ama **bir sonraki LIB sürümü mühürlenirken** N'e eklenir (görülmüş sayılır).
- Rationale: 106 varyant ana belgenin aile listesinin tamamıdır; coin'e özel arama 1.081 ham denemeyle en büyük kalemdir ve
  DSR eşiğini ≈ 3,54'ten 3,66'ya çıkarır (logaritmik). Kapsamlar sonuçlara bakılmadan sabitlendi.

### 2.6 Öncül denemeler (içe alınır, yeniden satılmaz)

`research_engine/prior_trials.json` (mühürlü) şu satırları `kind=PRIOR` ile yazar: gold_v1 32 birincil + 1.133 keşif grubu;
gold_v2 24; fut_v1 8; fut_v2 8; book_v1 54 (kontrol hariç); D4 koşusu (36150821072) 38; C4 koşusu (36277302747) 24;
formasyon koşusu (36123072720) 1.412; Box DENEY_V15 42 kol → **2.775**. Keşif gruplarının toplamı bilinmeyen koşular
(fut_v1/fut_v2 keşfi) `count_lower_bound` ile işaretlenir; rapor JSON'u VPS'te bulunursa gerçek sayı eklenir, asla
düşülmez. **Yeniden girmeyenler** (yeni bir LIB sürümü ve Kapı A olmadan): PAXG hafta sonu dönüşü (gold_v2 B), altın günlük
Donchian/TSMOM (gold_v2 A; `G` ailesinde yok), fut_v1'in dört OI/fonlama kuralı, fut_v2'nin kalabalık takip/karşı
satırları, "Box en az stop %0,5" ve "seçicilik ekstrasız ana bot" (canlıda), C4'ün veri gözetlemeli `C4_07` alt kümesi.
Bunlar keşifte izlenmez; `engine-query trials --like` onları "ÖNCÜL — KANIT YOK" gösterir.

---

## 3. Walk-forward, maliyetler, PIT evren, deneme defteri ve istatistik

### 3.1 Katmanlar (`quant/walkforward.make_folds(validation_days>0)` + `run_three_way`)

| Sınıf | Eğitim | Doğrulama | Test (OOS) | Adım | Kip |
|---|---|---|---|---|---|
| 1h, 4h, 1d | veri başından genişleyen, ≥ 365 g | 90 g | 90 g | 90 g | çapalı |
| 5m, 15m | 120 g kayan | 30 g | 14 g | 14 g | kayan |
| Altın vadeli | ≥ 180 g genişleyen | 30 g | 30 g | 30 g | çapalı (`KISA_GEÇMİŞ`) |

- Veri başı: serinin listelendiği gün + 210 bar ısınma (göstergeler); en erken 2020-01-01. Arındırma/ambargo (her iki
  sınırda) = 1 sinyal barı + `max_hold`. `leakage_check` geçmeyen katman atılır (`LEAK`).
- **OOS = test katmanlarının birleşimi**, her satır türünde aynı tanım (CSCV ortak zaman ekseni için). Sabit parametreli
  satırların doğrulama dilimi yalnız gösterilir.
- "Katmanların pozitif payı" = ADVERSE ortalaması > 0 olan test katmanlarının oranı (≥ 5 işlemli katmanlar).
- Kayan pencere ikinci bir satır **değildir**; çapalı sonucun yanında sağlamlık sütunu olarak gösterilir.

### 3.2 Seçim (yalnız `COIN_FIT`) ve sızıntı

- `fit_fn`: eğitim diliminde her ızgara seçeneğinin coin, küme (coin hariç) ve havuz ADVERSE istatistikleri.
- `candidates_fn`: §2.4 madde 2'nin büzülmüş puanına göre en iyi 3 seçenek.
- `select_fn`: doğrulama diliminde aynı büzülmeyle en iyisi (eşitlikte veya fark < 0,02R ise havuzun seçimi).
- `evaluate_fn`: seçilen seçenek test diliminde; katmanlar birleşir. Seçim her katmanda `trials.jsonl`'a yazılır.
- Sabit parametreli satırlarda seçim yoktur; parametreler mühürdedir (ana belge §6.3 "yalnız eğitimde seçim" kuralı
  `COIN_FIT` için uygulanır).

### 3.3 Maliyet senaryoları (ana belge §6.3, aynen)

| Senaryo | Ücret | Kayma | Ek |
|---|---|---|---|
| base | taker %0,05 × 2 | 3 bps | gerçek fonlama, gerçek uzlaşma anlarında |
| **ADVERSE** (kapı) | taker %0,05 × 2 | 6 bps | + 1 tick spread (tick = serinin fiyat adımı) |
| STRESS | taker %0,07 × 2 | 9 bps | + 1 tick spread |

Fonlama her senaryoda gerçektir (vekil yok); ADVERSE/STRESS yeniden maliyetlendirme fonlamayı değiştirmez. Funding verisi
eksik dönemdeki işlem `FUNDING_GAP` ile işaretlenir ve o işlem kapı istatistiğinden çıkar (sayısı gösterilir).

### 3.4 Zaman noktasında evren (`pit_universe.py`, veri biriminde)

- Kaynak: data.binance.vision S3 listesi (`?prefix=data/futures/um/monthly/klines/&delimiter=/`; delist olmuşlar da
  listelidir). Sembol başına ilk ve son aylık 1d zip'i, son ayda günlük zip'lerle gün düzeyi; `exchangeinfo/` anlık
  görüntüleri ve P1b'nin `store/_meta/delisted.json`'u ile çapraz denetim. Çıktı `pit_universe/listings.json`
  (`{symbol, first_day, last_day|null, status, source}`), mühürlü kural `PIT_RULES_V1`.
- PIT evren (gün d): d − 60 günden önce listelenmiş, d'de delist olmamış ve d'ye kadar 30 günlük medyan günlük hacmi
  ≥ 20 M USDT (config `universe.min_quote_volume_24h` ile aynı) olan semboller. Bu, **bütün tarihî USDⓈ-M sembollerinin
  1d mumlarını** ister: tek seferlik ≈ 25 bin küçük zip (≈ 100 MB), sonra günlük ek; `--backfill-pit` alt komutu P1b
  doldurma kurallarıyla (pencereler, disk, `.CHECKSUM`).
- Kullanım: XSEC satırları **yalnız** PIT evrende değerlendirilir; PIT kurulmadan Kapı A'ya kapalıdır (ana belge §6.7).
  Diğer satırlar "HAYATTA KALAN EVREN" etiketini taşır (P23 bugünün listesidir; coin satırlarında yanlılık, hangi coin'in
  incelendiğindedir, testin kendisinde değil).

### 3.5 Deneme defteri `trials/trials.jsonl` (yalnız eklenir, hash zincirli)

- Satır: `{seq, prev_sha, row_sha, kind, at, lib_version, registry_sha, data_seal, code_sha, spec_sha, row_id, scope,
  params, window, n, n_days, mean_r_{base,adverse,stress}, p_day, q_bh, dsr, pbo, placebo_diff_ci, verdict, look_no,
  N_raw_cum, N_lesson_cum, N_explore_cum, fwd_read}`; `row_sha` = sha256(kanonik satır − `row_sha`), `prev_sha` = önceki
  satırın `row_sha`'sı.
- `kind` ∈ {`PRIOR`, `SPEC` (mühürde bir kez), `OPTION` (`COIN_FIT` seçeneği), `ELIG` (§2.4-1 sayımı), `SELECT` (katman
  seçimi), `LOOK_A`, `FWD_READ`, `BIRTH`, `LOOK_B`, `STOP_B`, `HOLM`, `PROMOTE`, `RETIRE`, `LESSON_LOOK`, `EXPLORE_SEEN`}.
- Sayaçlar hiç azalmaz: `N_raw_cum` = benzersiz (`lib_version`, `spec_sha`, kapsam) + `OPTION` + `PRIOR`; `N_lesson_cum` =
  ders bakışları; `N_explore_cum` = sonraki sürümde eklenen keşif serileri. DSR'nin N'i = `N_raw_cum + N_lesson_cum +
  N_explore_cum` (o anki değer). Bakışlar N'i büyütmez; alfa harcaması bakışları öder.
- Her gece S6 zinciri doğrular (`TRIALS_CHAIN_BROKEN` → kapı ve terfi o gece kapalı). Defter S7b yedeğine girer.
- `engine-query trials --like <metin>` aynı/benzer `spec_sha`, aile ve parametreleri ≤ 150 satırda listeler (ana belge §8
  madde 4: yeni fikirden önce zorunlu).

### 3.6 İstatistik (tek istatistik `p_day`, ana belge §6.4)

- **`p_day`:** H0 "ortalama net R ≤ 0", ADVERSE, gün kümelenmeli tek yönlü bootstrap; 10.000 yeniden örnekleme, sıfır
  ortalamaya kaydırılmış dağılım, tohum = sha256(`row_id|look_no`)'nun ilk 8 baytı; 1.000'lik parçalarla (bellek ≤ 16 MB).
- **BH-FDR** (q = 0,10): aile = o Kapı A bakışında ≥ 30 OOS işlemi olan **bütün** etkin LIB satırları (kapıya kapalı XSEC
  dahil; muhafazakâr).
- **DSR:** `validation.deflated_sharpe(sr, n_trials=N, T, skew, kurt, sr_var=...)`; sr = OOS günlük getirinin (1 USDT risk,
  200 USDT tabanı; pozisyonsuz gün 0) Sharpe'ı, T = OOS gün sayısı, çarpıklık/basıklık ampirik; **`sr_var` = o bakışın
  ailesindeki satırların günlük Sharpe'larının varyansı, tabanı 1/(T−1)** (fonksiyonun 0,01 varsayılan tabanı kullanılmaz:
  günlük SR için ≈ 6 yıllık Sharpe eşiği demekti; bu bir okunuş kesinleştirmesidir, gevşetme değildir — DSR tanımı
  Bailey–López de Prado'nun denemeler arası varyansıdır). Etkin N (|ρ| > 0,7 hiyerarşik kümeleme) yalnız ikincil sütun.
- **PBO (CSCV, `cscv.py` yeni):** seçim grubu matrisi = gün × yapılandırma (OOS günlük getiri), S = 16 eşit blok, C(16,8) =
  12.870 bölünmenin hepsi; PBO = IS-en-iyi yapılandırmanın OOS göreli sırasının logit ≤ 0 olduğu bölünme payı. **Seçim
  grupları:** standart ailelerde aynı kapsamdaki ailenin bütün varyantları; `COIN_FIT`'te o coin'deki ızgara; `COIN`
  satırlarında kopyanın 23 coin satırı (coin seçimi); kopya/örtü havuz ve küme satırlarında {kopya + 7 örtüsü}; altında
  enstrüman başına 6 varyant; XSEC 5; spread 2. Grup < 2 yapılandırma veya < 16 × 5 OOS gün → "hesaplanamaz" → kapı geçmez.
- **Plasebo (laboratuvar geleneği):** her gerçek işlem için K = 5 rastgele işlem: aynı sembol, aynı yön, aynı tutuş süresi
  (bar), aynı ATR katı stop, giriş zamanı aynı test katmanında kuralın sinyal vermediği barlardan (gerçek işlemlerin ±
  tutuşu hariç) düzgün; tohum sha256(`row_id|trade_key|k`). Plasebo farkı = gerçek − plasebo ortalaması, gün kümelenmeli
  %95 CI; Kapı A alt sınır > 0 ister. İşaret çevrilmiş sinyal yalnız gösterilir.
- **Sentetik boş dünya testi (kabul):** 200 rastgele yürüyüş dünyasında (aynı satır yapısı) Kapı A geçme oranı ≤ q;
  dikilmiş avantajlı dünyada geçer.

### 3.7 Kapı A bakışı (ana belge §6.7 aynen; yordam)

Bakış gecesi: her ayın 5'inden sonraki ilk gece. Sıra: (1) zincir ve mühür denetimi; (2) her satır için OOS istatistikleri
(üç maliyet), `p_day`, plasebo, katman payı, pay/tutarlılık (§2.4-3/4); (3) seçim gruplarında PBO; (4) aile BH; (5) DSR;
(6) mühür sonrası ileri okuma (satırın kendi keşif serisi; havuz/küme satırında üyelerin birleşimi; `COIN_FIT`'te mühürde
donmuş seçim): ≥ 60 gün (5m/15m ≥ 30) ve ≥ 20 işlem ve ADVERSE ortalama ≥ 0 (`FWD_READ` satırı); (7) bütün koşulları
geçen satır `PASS_A` → §6.1 doğum kuyruğu. Ara gecelerde hüküm kelimesi yoktur ("ara görünüm"). Ölçüt eşikleri: q_bh ≤ 0,10;
OOS ≥ 100 işlem ve ≥ 2 takvim yılı (altın ≥ 60 ve ≥ 9 ay); pozitif katman ≥ %60; PBO < 0,25; DSR ≥ 0,95; plasebo CI alt > 0;
ileri okuma yukarıdaki gibi; tek enstrüman payı ≤ %50 (coin satırında küme tutarlılığı); XSEC yalnız PIT ile; kopya/türev
kıyası aynı config dönemi içinde.

# Motorun "kendi beyni" v1 — P3, P4 ve otomatik PAPER terfisi (tasarım ve ön kayıt)

**Durum:** TASARIM ve ÖN KAYIT (2026-10-06); **P3a kodu ve mühür S1: 2026-10-11** (§8.2 değerleri, §8.4 uygulama notu);
**P3b-1 (sözleşme, kopyalar, çekirdek, parite): 2026-10-11** (§8.4 P3b-1 notu, §5.3 ölçüm notu). Dal `impl/engine-p3-design`, taban `cc88e9b` (P1a, P1b, P2a, P2b
kodu `tradingbot/research_engine/` altında). Bu belge `docs/SYSTEM_LEARNING_ENGINE_V1.md`'nin ("ana belge") §5.7–§5.9,
§6 ve §10 P3/P4/P7'sinin **yapılabilir ayrıntısıdır**. Ana belgenin mühürlü ve kayıtlı metni (eşikler, kapılar, bakışlar,
alfa) DEĞİŞMEZ; burada yalnız okunuşları kesinleşir ve sahip kararı kurulur. Bu belgeyle ana belge çelişirse **daha sıkı
olan** geçerlidir. Bu belgenin §2–§7'si P3a mühür commit'inde (§8.2) koda dökülür; ondan sonra her değişiklik yeni sürüm
(`_v2`) + yeni deneme sayımı + tarihli "Değişiklik" notudur.

**Sahip kararları (2026-10-06).** (1) Kapı A + Kapı B + aylık Holm'u geçen aday, terfi başına onay sorulmadan **yeni bir
PAPER defteri** olarak açılır; gerçek para asla; mevcut defterler değişmez; işlem başı risk %0,25; önceden kayıtlı durdurma
ve SAPMA izlemesiyle defter kendini kapatır; sahip her açılışı/kapanışı özetten ve `--check`'ten görür ve tek komutla
kapatır. Metin ana belge §6.7'deki tarihli "Değişiklik"tir (commit `ec44ce3`, PR dalında; bu
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
- **En erken tarihler** (LIB_v1 mührü 2026-10-25 varsayımıyla; §8.3): 4h/1d adayı için en erken otomatik defter
  ≈ 2027-03-02. 5m adayı en erken 2027-02-01 Holm'una yetişir ama `AUTO_v1`'de otomatiğe uygun değildir (yazılı öneri, §6.4).
  Tek coinde günlük taktik ≥ 100 OOS işlem kuralı yüzünden çoğu zaman yıllarca "VERİ YETERSİZ" kalır; coin'e özel otomatik
  defter gerçekçi olarak 4h taktiklerinden ve büyük olasılıkla 2027'nin ikinci yarısından önce değil gelir.
- **En olası sonuç:** ilk 6 ayda **0** otomatik defter. Bir şey geçerse beklenen avantaj küçüktür (işlem başı +0,05…+0,2R);
  1 USDT riskle (§7.6) günde birkaç sent–1 USDT eder. **Otomatik defterler +%1/gün hedefini tutturmaz**; amaçları coin'e
  özel fikirleri güvenle ileriye denemektir.
- **Yanlış terfi:** aylık Holm, bir ayda en az bir yanlış terfi olasılığını ≤ 0,05 tutar; hiçbir yerde gerçek avantaj
  yokken bile on iki ayda "yanlış terfili ay" sayısının beklentisi ≤ 0,6'dır (üst sınır; gerçekte daha küçük olması
  beklenir ama ölçülemez). Bu yüzden tavanlar, otomatik emeklilik ve "kanıt değil, ileri test" etiketi zorunludur (§7).
- **"İnsandan zeki" iddiası yoktur.** Motorun üstünlüğü disiplindir: izin verilen her şeyi dener, her denemeyi sayar,
  başarısız fikri unutmaz, her kazancı/kaybı kanıtla açıklar ve kendi hatasını otomatik kapatır.

**Sahip hedefi (2026-10-06):** toplamda (overall) her gün ≥ %1 **veya** ay sonunda ≥ %30. Motor bunu her gün ve her ay
sonunda ölçer ve açıkça yazar (ana belge §1.1 notu). Lider tablosu ve adaylar "hedefe beklenen günlük % katkısı ve CI'ı" ile
de sıralanır (§6.7 Kapı C içeriği), ama terfi kararı yalnız mühürlü kapılarla verilir; hedef yüzünden eşik gevşetilmez.

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
*(P3a notu: "Oto" sütunu sahip kararı S5'ten öncedir; S5 onu geçersiz kılar, yönlendirme yalnız S2'nin `AUTO_CATALOG_V1`'iyle
yapılır — §8.4.)*

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
| O | Örtüler (bir seferde bir faktör) | taban {T2, M2, BOX, D4, C4} × {`F_ER` (T2/M2/D4/C4'te ER20 ≥ 0,3, Box'ta < 0,3), `F_BTC` (BTC 4h rejimi aynı yön), `F_VOL` (ATR% ≤ son 120 barın %80'liği), `X_TRAIL2` (+1R'den sonra 2 ATR iz süren), `X_CHAN3` (chandelier 3 × ATR22), `X_TIME` (`max_hold`'un yarısı; Box: 48 × 5m), `X_TPL` (1R/2R/3R'de üçte bir)} | tabanınki | 35 | T2/M2/D4/C4 × {F_ER, F_BTC, F_VOL, X_TRAIL2, X_CHAN3, X_TIME} = 24 |

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

1. **En az veri (çıktıya kör).** Bir coin `COIN_FIT`/`COIN` satırı alabilmek için: P23'te; mühürden önce ≥ 730 gün
   (1h ve üstü) veya ≥ 240 gün (5m) geçmiş; boşluk ≤ %1 ve arşivle doğrulanmış satır payı ≥ %99; varyantın (veya aile
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
- Gerekçe: 106 varyant ana belgenin aile listesinin tamamıdır; coin'e özel arama 1.081 ham denemeyle en büyük kalemdir ve
  DSR eşiğini ≈ 3,59'dan (N = 3.432) 3,66'ya (N = 4.513) çıkarır; artış logaritmiktir, yani coin başına aramanın istatistik
  bedeli küçüktür, asıl bedeli coin başına az işlemdir (§1.2). Kapsamlar sonuçlara bakılmadan sabitlendi.

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
  dahil; muhafazakâr). *(P3a mührü: ana belge §6.4'ün daha sıkı tanımı geçerlidir — işlem sayısı süzgeci yok, bkz. §8.4.)*
- **DSR:** `validation.deflated_sharpe(sr, n_trials=N, T, skew, kurt, sr_var=...)`; sr = OOS günlük getirinin (1 USDT risk,
  200 USDT tabanı; pozisyonsuz gün 0) Sharpe'ı, T = OOS gün sayısı, çarpıklık/basıklık ampirik; **`sr_var` = o bakışın
  ailesindeki satırların günlük Sharpe'larının varyansı, tabanı 1/(T−1)** (fonksiyonun 0,01 varsayılan tabanı kullanılmaz:
  günlük SR için yıllık ≈ 7 Sharpe eşiği demekti; bu bir okunuş kesinleştirmesidir, gevşetme değildir — DSR tanımı
  Bailey–López de Prado'nun denemeler arası varyansıdır). Etkin N (|ρ| > 0,7 hiyerarşik kümeleme) yalnız ikincil sütun.
- **PBO (CSCV, `cscv.py` yeni):** seçim grubu matrisi = gün × yapılandırma (OOS günlük getiri), S = 16 eşit blok, C(16,8) =
  12.870 bölünmenin hepsi; PBO = IS-en-iyi yapılandırmanın OOS göreli sırasının logit ≤ 0 olduğu bölünme payı. **Seçim
  grupları:** standart ailelerde aynı kapsamdaki ailenin bütün varyantları; `COIN_FIT`'te o coin'deki ızgara; `COIN`
  satırlarında kopyanın 23 coin satırı (coin seçimi); kopya/örtü havuz ve küme satırlarında {kopya + 7 örtüsü}; altında
  enstrüman başına 6 varyant; XSEC 5; spread 2. Grup < 2 yapılandırma veya < 16 × 5 OOS gün → "hesaplanamaz" → kapı geçmez.
- **Plasebo (laboratuvar geleneği):** her gerçek işlem için K = 5 rastgele işlem: aynı sembol, aynı yön, aynı tutuş süresi
  (bar), aynı ATR katı stop, giriş zamanı aynı test katmanında kuralın sinyal vermediği barlardan (gerçek işlemlerin ±
  tutuşu hariç) düzgün; tohum sha256(`row_id|trade_key|k`). Plasebo farkı = gerçek − plasebo ortalaması, gün kümelenmeli
  %95 CI; Kapı A alt sınır > 0 ister (laboratuvarların SIKI GÜÇLÜ ADAY şartının karşılığı). İşaret çevrilmiş sinyal yalnız
  gösterilir. Lider tablosu her satıra laboratuvar hükmünü de (GÜÇLÜ ADAY / ZAYIF İZ / KANIT YOK / KAYBETTİRİR / VERİ AZ,
  standart ve sıkı; `signal_lab.verdict`/`verdict_strict`) yalnız gösterim sütunu olarak yazar: geçmiş sonuçlarla aynı dil.
- **Sentetik boş dünya testi (kabul):** 200 rastgele yürüyüş dünyasında (aynı satır yapısı) Kapı A geçme oranı ≤ q;
  dikilmiş avantajlı dünyada geçer. Laboratuvar ölçümüne göre rastgele yürüyüşte grupların %6–11'i ZAYIF İZ, %0–0,5'i GÜÇLÜ
  ADAY çıkar; Kapı A'nın boş dünyadaki oranı bunun altında olmalıdır (raporlanır).

### 3.7 Kapı A bakışı (ana belge §6.7 aynen; yordam)

Bakış gecesi: her ayın 5'inden sonraki ilk gece. Sıra: (1) zincir ve mühür denetimi; (2) her satır için OOS istatistikleri
(üç maliyet), `p_day`, plasebo, katman payı, pay/tutarlılık (§2.4-3/4); (3) seçim gruplarında PBO; (4) aile BH; (5) DSR;
(6) mühür sonrası ileri okuma (satırın kendi keşif serisi; havuz/küme satırında üyelerin birleşimi; `COIN_FIT`'te mühürde
donmuş seçim): ≥ 60 gün (5m/15m ≥ 30) ve ≥ 20 işlem ve ADVERSE ortalama ≥ 0 (`FWD_READ` satırı); (7) bütün koşulları
geçen satır `PASS_A` → §6.1 doğum kuyruğu. Ara gecelerde hüküm kelimesi yoktur ("ara görünüm"). Ölçüt eşikleri: q_bh ≤ 0,10;
OOS ≥ 100 işlem ve ≥ 2 takvim yılı (altın ≥ 60 ve ≥ 9 ay); pozitif katman ≥ %60; PBO < 0,25; DSR ≥ 0,95; plasebo CI alt > 0;
ileri okuma yukarıdaki gibi; tek enstrüman payı ≤ %50 (coin satırında küme tutarlılığı); XSEC yalnız PIT ile; kopya/türev
kıyası aynı config dönemi içinde.

---

## 4. Dersler: atıftan önceden kayıtlı aday kurala

### 4.1 Atıf → ders istatistiği (ana belge §5.7; S6, `lessons.py`)

- **Girdi:** P2b'nin `attribution/YYYY-MM.jsonl.gz` satırları (kodlar, `cfgrid_v1` hücreleri, R ayrıştırması) ve `tj_v1`
  (kohort, config dönemi, giriş kovası). Gerçek işlem kanıtı (`REAL`) ve karşı-olgusal kanıt (`CF`, `cf_aux_v1`
  muhafazakâr R) ayrı satırdır, birleştirilmez; POLICY ve öğrenme-ekstra kohortları karışmaz; hücreler config dönemi
  içinde hesaplanır.
- **Hücreler** (geri çekilme düzeyleri; çapraz derinlik ≤ 2): L0 defter; L1 defter × taktik; L2 L1 × enstrüman; L3 L1 ×
  durum kovası (`situation_v1` 4h trend × 4h oynaklık); L4 L1 × enstrüman × kova. Veri olan en kaba düzey kullanılır.
- **Hücre istatistikleri:** n ve gün sayısı, ortalama net R, Wilson kazanma oranı, ebeveyne `HierarchicalRate` büzülmesi
  (alpha 10), gün kümelenmeli CI95 (5.000, sabit tohum), her EX_ANTE `cfgrid_v1` hücresi için gerçeğe göre eşli ΔR ve CI'ı,
  `p_day` (§3.6).
- **Ders türleri ve v1 karşılığı:** IZGARA (EX_ANTE hücre ΔR > 0) ve FİLTRE (hücrede atla, eşli Δ = −r) aday üretebilir.
  MALİYET yalnız raporlanır (`cfgrid_v1`'de fonlamadan kaçınma hücresi yok; limit giriş worker'ın taker yürütmesini
  değiştirir; ikisi de `cfgrid_v2` ister). AYRIM (kazananı kaybedenden ayıran özellik) yalnız walk-forward OOS AUC CI > 0,5
  ise raporlanır, v1'de aday üretmez.
- **Uygunluk:** n ≥ 30 ve ≥ 10 farklı gün.
- **Bakışlar:** hücrenin n'i 30, 60, 120, 240'ı geçtiği gece; alfa 0,01 / 0,01 / 0,015 / 0,015. Her bakış `LESSON_LOOK`
  satırıdır. BH ailesi (q = 0,10) = o gece bakışı olan **bütün** (hücre, ders türü/EX_ANTE hücresi) çiftleri; gecelik üst
  sınır 2.000 çift (n'i büyük olan önce, sonra `cell_id`), sığmayan ertesi geceye kalır ve sayılır.
- **İleri doğrulama:** bulgu T_k anında hash'lenir (`RESEARCH_HYPOTHESIS`); doğrulama yalnız T_k'dan sonra kapanan
  işlemlerle, hücrenin bir sonraki bakışında: aynı işaret, `p_day` ≤ o bakışın alfası, ≥ 3 aylık katmanın ≥ 2/3'ünde işaret
  tutarlılığı.

### 4.2 Ders deposu ve yaşam döngüsü (ana belge §5.8)

- `learn/lesson_store` (`lesson_v2`, `build_lesson`, `transition`) + `learn/journal_archive.SegmentArchive`; kök
  `data/research/lessons`; anahtarlar `B| I| A| T| V| C| X| E|`; `state/lesson_archive`'a dokunulmaz.
- Durumlar: `OBSERVATION` → `RESEARCH_HYPOTHESIS` → `VALIDATED_POLICY_CANDIDATE` → (`APPLIED_BOUNDED` yalnız sahibin
  `engine-query --approve` komutu ve normal sürümle, yalnız **mevcut defter** değişikliğinde) | `REJECTED` | `RETIRED`.
  Otomatik terfi bir ders durumu **değildir**: dersten türeyen aday otomatik defter olursa ders `VALIDATED_POLICY_CANDIDATE`
  kalır ve `linked_promo_id` alır. Gece çalıştırıcısı `APPLIED_BOUNDED` yazamaz (test).
- `RETIRED`: sonraki bakışta işaret döndü; ya da `attribution_v1`/`cfgrid_v1`/veri mührü değişikliği dersin işlemlerini
  geçersizleştirdi (yeniden hesaplanır, yeni `LESSON_LOOK` satırları).
- P2b'nin geçici `regime_fit` önceli (`JOURNAL_PRIOR`) ders deposu kurulunca `LESSON_PRIOR`'a geçer (`as_of < opened_at`).

### 4.3 Dersten önceden kayıtlı aday kurala (ana belge §5.9; tek otomatik fikir üreticisi)

| Doğrulanmış ders | Dönüşüm (mühürlü liste) | Aday spec ve kapsamı | Tekilleştirme |
|---|---|---|---|
| FİLTRE: taban T, kova X'te kaybettiriyor | T4 `EXCLUDE_REGIME(X)` | T + "X'te giriş yok", `POOLED_OWN` | O ailesindeki `F_ER`/`F_BTC`/`F_VOL` ile `spec_sha` aynıysa yeni deneme yok, o satıra bağlanır |
| FİLTRE: taban T, enstrüman c'de kaybettiriyor | T1 `CELL_FILTER(I=c)` | T'nin kümesi − {c}, `POOLED_OWN` | — |
| "T yalnız c'de kazanıyor" | dönüşüm yok | zaten LIB_v1 satırı `COIN:c` | yeni deneme yok |
| IZGARA stop ekseni ({0,75; 1; 1,5; 2} × ATR) | T2 `STOP_FLOOR(k)` | T + stop kuralı, ders hücresinin kapsamı | — |
| IZGARA çıkış ekseni (1R/2R/3R, 2 ATR iz, hedefsiz + zaman) | T3 `EXIT_VARIANT(e)` | T + çıkış, ders hücresinin kapsamı | `X_*` ile `spec_sha` aynıysa bağlanır |
| MALİYET, AYRIM | v1'de yok | — | — |

- Dönüşüm kodu LIB_v1 mührünün parçasıdır; türeyen spec'ler `library/derived_specs.jsonl`'a (yalnız eklenir) motorun
  kendisince yazılır ve ayda bir `LIB_v1.<AAAA-AA>` olarak mühürlenir (her ayın 1'inden sonraki ilk gece; aylık üst sınır
  20 spec, düşük q ve büyük |Δ| önce). İnsan PR'ı gerekmez; yeni aile/dönüşüm türü ise kod PR'ıdır (yeni LIB sürümü).
- Her spec'in kendi `T_seal`'ı vardır; Kapı A'nın ileri okuma şartı (≥ 60 gün) yüzünden dersten türeyen aday en erken
  mühründen ~2 ay sonra kapıyı geçebilir. Canlıda zaten olan kural (ham config'e karşı denetim; örn. Box en az stop %0,5)
  aday olmaz. Hiçbir dönüşüm mevcut defteri değiştirmez; geçen aday yalnız **yeni** defter olabilir (§7).

### 4.4 Her adımın deneme maliyeti

| Adım | `trials.jsonl` | N'e etkisi |
|---|---|---|
| Atıf (S2), ders ara görünümü | yok | yok (hüküm yok) |
| Ders bakışı | 1 `LESSON_LOOK` / (hücre, tür) / bakış | `N_lesson_cum` +1 |
| Türeyen spec | 1 `SPEC` / spec (+ ızgara seçeneği varsa `OPTION`) | `N_raw_cum` +1 (+ seçenekler) |
| Kapı A bakışı | 1 `LOOK_A` / satır / ay | yok (BH + DSR + ileri okuma öder) |
| Keşif okuması (insan/AI'ın "en iyi 5"i görmesi) | sürüm mühründe `EXPLORE_SEEN` | sonraki sürümde `N_explore_cum` += seri sayısı |
| Kapı B bakışı | 1 `LOOK_B` / aday / bakış | yok (alfa harcaması öder) |
| Holm | 1 `HOLM` / aday / ay | yok (aile hatası öder) |

---

## 5. Keşif katmanı, gece planı ve kaynaklar

### 5.1 Keşif katmanı (ana belge §6.8; `explore.py`, S4x)

- **Seriler (3.745):** 45 bağımsız kripto varyantı × giriş evreninin 40 coin'i (1.800); 13 kopya × 40 (520); 35 örtü × 40
  (1.400); XSEC 5; spread 2; altın 6 × {XAUUSDT, PAXGUSDT vadeli, PAXG spot} (18).
- **Veri:** yalnız açılışı `T_SEAL_MS`'den (mühür commit'inin UTC zamanı, kayıtta) sonra olan, arşivle doğrulanmış, mühürlü
  barlar; göstergeler öncesindeki barlarla ısınır (seçim yok). `DATA_MOVING`/`DATA_STALE` serisi o gece atlanır.
- **Durum:** seri başına `explore/<lib>/<variant>/<SYM>.state.json` (gösterge durumu, açık pozisyon, son bar) ve
  `<SYM>.jsonl` (işlemler; üç maliyetle günlük özsermaye, 1 USDT risk / 200 USDT taban). Artımlı adım baştan hesapla
  bayt-özdeştir (test).
- **Çıktı ve gösterim:** ana belge §6.8 aynen; `engine-query explore [--gold] [--variant <id>]`; özette yalnız "ileri verili
  seri sayısı, medyan serinin sonucu, '3.745 seri arasından en iyi 5 — seçim yanlılığı, kanıt değil'". Hüküm kelimesi
  üretmez (metin testi), terfi ettirmez, canlı toplama girmez. Kapı A yalnız satırın kendi ileri birleşimini okur (§3.7).

### 5.2 Gece planı (P3 + P4 + P7b; 01:37 → iç son tarih 03:40 UTC)

| Sıra | Aşama | Bütçe | Not |
|---|---|---|---|
| 1 | S0, S1s, S1a, S3 | P1a/P2 gibi | değişmez |
| 2 | S1b, S2 | P2b rezervleri | değişmez |
| 3 | **S4x** keşif adımı | ≤ 10 dk | yalnız yeni barlar |
| 4 | **S5** P4 adayları | ≤ 5 dk | ≤ 30 aday |
| 5 | **S5r** otomatik defter izleme (P7b) | ≤ 3 dk | SAPMA, parite, kalp atışı (§7.7) |
| 6 | **S4w** walk-forward kuyruğu | son tarih − 25 dk'ya kadar | ilk kurulum birikmesi, aylık ek, haftalık 1/7 doğrulama |
| 7 | **S6** dersler, denemeler, kapılar | ≤ 10 dk; Kapı A gecesi ≤ 40 dk (S4w o gece kısılır); Holm gecesi + 5 dk | son tarih − 8 dk'da durur |
| 8 | S7, S7b | değişmez | `trials/`, `explore/`, `prospective/`, `promotions/`, `lessons/`, `library/` yedekte |

- İleri veri aşamaları (S4x, S5, S5r) zamana duyarlı ve ucuzdur; önce koşar. S4w kalan süreyi kullanır.
- A/B KAPALI, SKEW ve `DATA_STALE` gecelerinde S4x/S5/S4w çalışmaz; ilk AÇIK gece bütün birikmiş barları işler.
- İlk doldurma birimi (`tb-engine-backfill`) etkinken S4w `BACKFILL_ACTIVE` ile atlanır (bellek toplamı).
- WF önbelleği `backtests/cache/<spec_sha>/<kapsam>/<veri_ay_mührü>.parquet` (işlem listesi + günlük getiri); ay parçasının
  mührü değişince geçersizleşir. Haftalık rotasyon her gece 1/7'yi sıfırdan hesaplar ve karşılaştırır (bayt farkı →
  `WF_CACHE_MISMATCH`, seri yeniden kurulur).

### 5.3 Kaynaklar (dürüst öneri)

- **Bellek.** VPS 4 vCPU / 7,7 GB; worker en çok ≈ 5,5 GB (6 GiB cgroup); panel ≤ 0,5 GB; gece birimi bugün `MemoryHigh=400M` /
  `MemoryMax=512M` (P2 ölçülen tepe 283 MiB). P3 eki: tek seri × dilim bellekte (5m 3 yıl ≈ 20 MB ham, pandas ile ≈ 60 MB),
  CSCV matrisi < 1 MB, bootstrap parçası 16 MB, keşif akışla → beklenen tepe ≈ 400–450 MiB. **Karar kuralı (önceden):** P3g
  büyük sentetik dünyada (P2b dünyası + P3 yükü) `VmHWM` ≤ 350 MiB ise 400M/512M kalır; değilse `MemoryHigh=640M` /
  `MemoryMax=800M` (`ENGINE_EXPECTED_MEMORY_MAX=838860800`) — tek seferlik, ana belge §9.3'teki kapılı birim kurulumuyla. Worker tepesinde
  boş ≈ 7,7 − 5,5 − 0,5 − 0,5 ≈ 1,2 GB ≥ 0,8 GB; ilk doldurma S4w ile aynı anda koşmaz; `OOMScoreAdjust=1000` aynı. İç
  koruma: RSS > 0,7 × MemoryMax → çalışan S4w/S6 işi kontrol noktası yazar ve durur (`MEMORY_GUARD`); S1a/S3/S7b asla.
- **CPU:** `CPUQuota=100%`, `--jobs 1`, `Nice=19`, `CPUWeight=10`, IO `idle` (değişmez).
- **Süre:** S4w gecede ≈ 60–80 dk. İlk tam kurulum (106 varyant × uygun seriler × bütün geçmiş) tahmini ≈ 8 çekirdek-saat
  (sandbox) × 3 (VPS) ≈ 24 saat → ≈ 18–24 AÇIK gece (A/B dönüşümlü olduğundan ≈ 5–6 takvim haftası). Sonrası: aylık ek ≈ 1
  gece, haftalık doğrulama 7 gece. **Ana belge P3 kabul 10'un okunuşu:** "tam tarama 7 gecede" kararlı durumdaki doğrulama
  rotasyonudur; ilk kurulum `WF_BACKLOG done/total, ETA` olarak görünür; bitmemiş satır Kapı A'da `HESAPLANMADI` (geçmedi
  değil, BH ailesinde değil). Gerçek hız P3b'de ölçülür; tahmin tutmazsa bu satır güncellenir.
  - **Ölçüm notu (P3b-1, 2026-10-11; yalnız sentetik veri, `scripts/engine_p3b_measure.py`; sandbox 4 vCPU Xeon 2,1 GHz,
    tek iş parçacığı, iki ölçüm koşusu eşzamanlı).** Canlı kural fonksiyonunu her barda çağıran kopyalarda çekirdeğin
    hızı (karar + bar içi yol, bar/sn): T2 58, M2 58 (1d; ortak yapı analizi + günlük pencerenin göstergeleri her barda),
    D4 90 (4h; `signal_lab` göstergeleri her barda 400 satırda yeniden), C4 264 (4h, varyasyon başına), Formasyon 279 (4h),
    Box 146 (5m). Ölçekle (P23'ün 23 coin'i; 1d ≈ 2.450, 4h ≈ 14.700 bar ≈ 2020→bugün; 5m ≈ 3 yıl ≈ 315.000 bar; kendi
    sembol kümesinin P23 dışındaki coin'leri sayılmadı → alt sınır) K ailesinin bir tam geçmiş koşusu ≈ T2+M2 0,5 + D4 1,0 +
    C4 2,9 + Formasyon 0,3 + **Box 13,8** ≈ **18,6 çekirdek-saat (sandbox)** ≈ 56 saat VPS (× 3). O örtüleri tabanı her
    örtüde yeniden koşarsa ≈ +128 çekirdek-saat (Box örtüleri ≈ 97). **Sonuç: yukarıdaki "106 varyant ≈ 8 çekirdek-saat"
    tahmini K + O için TUTMAZ** (B…O'nun saf sayısal aileleri P3b-2'de ölçülür; onlar bar başına canlı fonksiyon
    çağırmaz). Önlemler (P3b-2, ilk WF kurulumundan önce; her biri bayt-aynılık testiyle): (1) kopyanın `signals`'ı
    pozisyondan bağımsızdır → (taban, sembol, bar) başına bir kez hesaplanıp taban ve yedi örtüsü paylaşır (örtünün ek
    maliyeti ≈ yalnız pozisyondaki `manage`); (2) kopyaların satır yolu her barda DataFrame kurmadan, canlı okumayla
    bayt-aynı olduğu testle kanıtlanan hızlı yol (m2x `daily_frame` deseni); (3) Box 5m için aynı ilkeyle ön-süzgeç. Bu
    önlemlerden sonra ölçüm P3b-2'de yinelenir; ilk kurulum takvimi (≈ 5–6 hafta) tutmazsa bu satır yeniden yazılır
    (S4w kuyruğu bütçesiyle sınırlı kalır; bitmemiş satır Kapı A'da `HESAPLANMADI`). Bellek: tek seri × dilim, kopya
    penceresi ≤ 699 bar; ölçüm koşusu RSS ≈ 0,2 GB (sentetik; `VmHWM` kararı P3g'de).
- **Disk:** trials ≤ 50 MB/yıl, keşif ≈ 200 MB/yıl, WF önbelleği ≈ 0,5 GB, 14 gecelik ayrıntı ≈ 0,3 GB, adaylar ≈ 50 MB, PIT 1d
  ≈ 0,1 GB → ≤ 1,5 GB (15/20 GB sınırlarının içinde).
- **Kabul (VPS, 14 A/B gecesi):** tur p95 ≤ KAPALI + %5; `NRestarts` değişmez; `memory.peak` ≤ 0,8 × MemoryMax; Box kaçan
  bar ve koruyucu izleyici gecikmesi KAPALI tabanında; 418/429 = 0.

---

## 6. P4: kayıt-yalnız ileri adaylar ve Kapı B

### 6.1 Doğum ve kapasite

- Kapı A bakışında her `PASS_A` satırı bir aday doğurur: `cand_<lib>_<row_id>_<sha6>` (`BIRTH` satırı). Dondurulanlar:
  varyant ve parametreler (`COIN_FIT`'te son katmanın seçimi), kapsamın sembol listesi, maliyet modeli, büyüklük (200 USDT,
  işlem başı %0,5 = 1 USDT), durdurma kuralları, bakış takvimi (sınıfa göre), Kapı A OOS CI'ı ve geriye-test p95 düşüşü.
- En çok 30 etkin aday. Fazlası kuyruğa girer (önce düşük q, sonra düşük `p_day`); iki Kapı A bakışında yer bulamayan kuyruk
  girdisi düşer ve bir sonraki bakışta yeniden nitelenmelidir.

### 6.2 Adım (S5, `prospective.py`)

- Aday başına `prospective/<id>/futures_ledger.json` (`FuturesLedgerV2`; worker'ın `FeeSchedule`, kayma, bracket,
  likidasyon ayarları), `scenarios.jsonl` (ADVERSE/STRESS yeniden maliyet) ve `tj_v1` biçimli satırlar
  (`source=PROSPECTIVE_REPLAY`); atıf (S2) bunlara da uygulanır.
- Barlar: yalnız arşivle doğrulanmış, mühürlü kapanmış barlar (`SealedReader`; gecikme ≤ ~26 saat, kaydedilir). Karar
  `close_time < adım` barlarıyla, dolum sonraki barın açılışında + kayma; stop/hedef yolu depoda varsa 1m/5m'den, yoksa
  sinyal diliminde ters-önce; fonlama gerçek uzlaşmalarda. Worker'ın tur gecikmesi (≤ ~20,5 dk) bilgi sütunu `exec_delay`
  olarak ayrıca modellenir; kapıya girmez, terfi sonrası parite beklentisini verir.
- Nedensellik: kesik veriyle ve tam veriyle adım atan iki koşu ortak dönemde bayt-özdeş karar verir (zaman yolculuğu testi).

### 6.3 Kapı B bakışları ve aylık Holm (ana belge §6.7, aynen)

- Bakışlar: 5m–4h taktikleri 50/100/150 kapanmış işlem; 1d taktikleri ve altın 30/60/90; alfa 0,01 / 0,015 / 0,025. En az
  süre 28 gün (1d: 90; altın: 60). Bir bakışta hepsi: `p_day` (ADVERSE, ileri pencere) ≤ bakış alfası; STRESS
  ortalaması sıfırdan büyük; ortalama Kapı A OOS CI'ı içinde; düşüş ≤ 1,5 × geriye-test p95 ve ≤ özsermayenin %8'i; likidasyon yok; nedensellik
  testi geçer; `DATA_STALE` günleri ≤ %10 → `PASS_B`. Yalnız `p_day` şartı tutmuyorsa ve son bakış değilse aday sürer.
- Durdurma (yalnız bakışlarda; ana belge §6.6): ≥ 30 işlemden sonra ortalama R < −0,10; kayan 60 günlük ortalama R CI üst
  sınırı < 0; düşüş > 2 × geriye-test p95; günlük net R CUSUM (h = 4σ) → `STOPPED`. Son bakışta geçmeyen → `FAILED_B`.
- **Holm ayı (okunuş kesinleştirmesi):** toplu iş her ayın ilk UTC pazartesisi gecesi; aile = bir önceki toplu iş gecesinden
  bu geceye kadar herhangi bir Kapı B bakışı olan **bütün** adaylar; her birinin p'si o aralıktaki son bakışının `p_day`'i;
  Holm, aile genelinde α = 0,05. `PASS_B` ∧ Holm → `PROMOTABLE` (her aile üyesi için `HOLM` satırı).
- **Kapı A'nın aylık tekrarı nihai hatayı neden şişirmez:** Kapı A yalnız süzgeçtir; doğrulayıcı test Kapı B'dir ve yalnız
  adayın doğumundan **sonra** gelen, hiçbir seçimde görülmemiş veriyle yapılır, alfa bakışlara bölünür ve aylık Holm aile
  hatasını sınırlar. Kapı A'nın yanlış pozitifi yalnız bir P4 yuvasına mal olur.

### 6.4 Yönlendirme ve etiket

- `PROMOTABLE` ve otomatik katalogda (§7.5) → terfi manifesti (§7.3). Katalog dışı (1h/5m dilimli varyantlar, altın,
  fonlama, XSEC, spread, `K_BOX`, `K_FM`, Box örtüleri, `X_TPL` örtüleri) → ana belge §6.7 Kapı C yazılı önerisi (`proposals/<id>.json` + `.tr.md`), sahip
  onayı, normal sürüm. Sahip kararındaki "o yazılana kadar Kapı C geçerlidir" hükmü bu adaylar için sürer.
- Etiket her yerde "ileri yeniden oynatma — gerçek defter değil"; canlı toplamlara asla girmez; `engine-query candidates`.

---

## 7. Otomatik PAPER terfisi (sahip kararı 2026-10-06)

### 7.1 Seçim: mühürlü, imzalı manifest + derlenmiş kapalı katalog (kural dili yok)

- **Seçilen:** worker yalnız `AUTO_v1` sürümünde derlenmiş `strategy_lib` fonksiyonlarını, `AUTO_CATALOG_V1`'de sayılmış
  parametre demetleriyle çalıştırır. Manifest kod, ifade, dosya yolu ya da serbest parametre taşımaz; yalnız katalogdan bir
  `variant_id`, kapalı bir `scope_id`, numaralandırılmış örtü alanları (§4.3 dönüşümleri: kova ∈ `situation_v1` kümesi,
  coin ∈ P23, stop k ∈ {0,75; 1; 1,5; 2}, çıkış ∈ sabit liste) ve politikanın sabitlediği sayıları seçer.
- **Neden kural dili (DSL) değil:** (a) yorumlayıcı worker'da yeni bir yürütme yüzeyidir; (b) anlamının motor çekirdeğiyle
  bayt bayt aynı olması ayrıca kanıtlanmalıdır; (c) serbest birleşimler hiç test edilmemiş kurallar üretir ve deneme sayısını
  tanımsız bırakır. Katalogda **test edilen kod çalışan koddur** (aynı modül), olası davranışlar sonlu ve mühürlüdür, keyfi
  kod çalıştırma yoktur.
- **Bedeli (dürüst):** yeni aile, yeni dönüşüm türü ya da yeni veri girdisi bir worker sürümü ister. Katalog içindeki her
  terfi sürüm istemez.
- **Ana belgeyle ilişki:** sahip kararındaki "ayrıca ve ön kayıtla yazılacak" tasarım budur. Ana belge §2.1 ilke 2 ("motorun
  hiçbir karar yoluna yazma yolu yoktur") bu karar için daralır: motorun tek yazma yolu **yeni** oto defterlerin manifestidir;
  mevcut defterlere hiçbir yol yoktur (AST + bayt testi, §7.8). Mühür S2 ve `AUTO_v1` canlıya çıkana kadar Kapı C'nin öneri +
  onay yolu geçerlidir.

### 7.2 Akış

```
motor S6 (Holm gecesi)                         worker (her turun sonu, "auto" fazı)
  PROMOTABLE ∧ katalogda ─► promotions/inbox/<promo_id>.json ─► doğrula (§7.4) ─► red: state/auto_paper/rejects.jsonl
                                                               └► kabul: state/auto_paper/<promo_id>/accepted.json
                                                                   BEKLEMEDE (veto penceresi 24 s) ─► AKTİF ─► DURAKLADI ─► EMEKLİ
motor ertesi gece: state/auto_paper/acks.jsonl (salt-okunur) ─► manifest promotions/archive/'a, trials: PROMOTE
motor S5r: SAPMA ─► promotions/retire/<promo_id>.json ─► worker uygular ("şüphede dur": iyi biçimli geri çekme imzasız da kabul)
sahip: python -m tradingbot auto-paper --kill | --unkill | --veto <id> | --retire <id> | --status  (yalnız state/auto_paper/)
```

Yazma alanları ayrıdır: motor yalnız `data/research/promotions/`'a, worker yalnız `state/auto_paper/`'a yazar. Sahibin kill/veto
dosyaları `state/` altındadır; motor birimleri `state`'i çekirdek düzeyinde salt-okunur gördüğü için **motor bir kill'i
asla geri alamaz**.

### 7.3 Manifest şeması `auto_promo_v1` (kanonik JSON: sıralı anahtar, `(",", ":")`, UTF-8; ≤ 32 KB)

`{schema, promo_id, created_at, valid_until (+14 g), lib_version, lib_sha, catalog_sha, registry_sha, auto_policy_sha,
engine_code_sha, trials_head_sha, candidate_id, row_id, variant_id, family, overlays{…}, scope_id, symbols[],
timeframes[], sizing{starting_equity_usdt: 400, risk_pct: 0.25, leverage_max: 3}, retire_params{a_ci95_mean_r[2],
bt_p95_dd_r, b_mean_r, cusum_h_sigma: 4}, evidence{gate_a{q_bh, n_oos, years, pos_fold_share, pbo, dsr, N, sr_var,
placebo_ci_lo, fwd_days, fwd_n, fwd_mean_adverse, top_share|cluster_coherence_r}, gate_b{look_no, n, days, p_day, alpha,
stress_mean_r, mean_in_a_ci, dd_r, dd_pct, liquidations, causality_ok, stale_share}, holm{m, rank, p, p_adj, alpha}},
max_corr_with_active, kid, sig}`. `promo_id` = `auto_<lib>_<variant_id>_<scope>_<sha8>` (sha8 = kanonik gövde − `sig`'in
sha256'sı). `sig` = HMAC-SHA256(anahtar, kanonik gövde − `sig`); `kid` = sha256(anahtar)[:12].

**Anahtar ve tehdit modeli (dürüst).** Anahtar `/opt/tradingbot/data/keys/promotion_hmac.key` (32 rastgele bayt, 0440
`tradingbot`; `AUTO_v1` betiği yoksa üretir). Gece birimi (`ReadOnlyPaths=/opt/tradingbot/data`) imzalar, worker doğrular.
İmza bütünlük ve kaynak sağlar (kısmi yazım, elle düzenleme, motor dışı kod yolları); **ele geçirilmiş bir motora karşı
koruma değildir** (aynı kullanıcı, aynı güven alanı). Asıl güvence, manifest doğru olsa da olmasa da geçerli olan katalog +
sert tavanlar + PAPER'dır (§7.6). P4b ile veri birimine `InaccessiblePaths=-/opt/tradingbot/data/keys` eklenir (ağlı tek
birim anahtarı okuyamaz).

### 7.4 Worker doğrulaması (sıralı; ilk hata red nedenidir, manifest dosyasına dokunulmaz)

| # | Denetim | Sonuç |
|---|---|---|
| 1 | boyut ≤ 32 KB, JSON, şema `auto_promo_v1`, anahtar kümesi **tam** (bilinmeyen anahtar = red) | `REJECT_SCHEMA` |
| 2 | anahtar okunur; `kid` eşit; HMAC eşit (sabit zamanlı karşılaştırma) | `REJECT_NO_KEY` / `REJECT_SIG` |
| 3 | `created_at` ≤ şimdi + 5 dk; `valid_until` > şimdi | `REJECT_TIME` / `EXPIRED` |
| 4 | `registry_sha`, `catalog_sha`, `auto_policy_sha`, `lib_sha` worker'da derlenmiş sabitlere eşit | `REJECT_SKEW` |
| 5 | `variant_id` katalogda; örtüler numaralı alanlarda; `scope_id` çözümü = `symbols`; semboller ⊆ config `entry_universe.symbols` (turun zaten çektiği 40 sembol; P23 bunun alt kümesi); dilimler ⊆ {1d, 4h} | `REJECT_CATALOG` |
| 6 | `sizing` = politika sabitleri (birebir) | `REJECT_SIZING` |
| 7 | `evidence` alanlarının her biri kayıt eşiklerini sağlar (Kapı A, Kapı B, Holm `p_adj` ≤ 0,05); `max_corr_with_active` ≤ 0,7 | `REJECT_EVIDENCE` |
| 8 | `promo_id` daha önce kabul/red/emekli/veto edilmemiş | `REJECT_DUPLICATE` |
| 9 | kapasite: etkin + bekleyen < 4; tek-coin ≤ 2; aynı coin ≤ 1; aynı aile ≤ 2 | `WAITLIST` (red değil; `valid_until`'a kadar bekler) |
| 10 | config ve `state/mode.json` PAPER; `auto_paper.enabled`; KILL yok; env kapatmamış; motor kalp atışı ≤ 72 saat (`data/research/summary/run_status.json`) | `HOLD` (tekrar denenir) |

Kabul: `accepted.json` (manifest + `accepted_at` + `activate_at` = +24 s), boş `futures_ledger.json`, `acks.jsonl` satırı.

### 7.5 `AutoBook` çalışması (`tradingbot/autobook/`)

- **Katalog `AUTO_CATALOG_V1` (64 varyant):** B 10, C 8, D 7 + `K_D4`, E 2 (4h), F1 2 (4h), `K_T2`, `K_M2`, `K_C4_CV001…008`,
  O'nun T2/M2/D4/C4 × {`F_ER`, `F_BTC`, `F_VOL`, `X_TRAIL2`, `X_CHAN3`, `X_TIME`} = 24. Ölçüt: worker turunun **zaten
  çektiği** veri yeter (1d 420 gün, 4h 730 gün; 1h yalnız 30 gün, 5m yalnız 3 gün → dışarıda); girdiler OHLCV + BTC 4h
  (fonlama/OI yok); semboller giriş evreninde (tur kapsamında; T2/M2 kopyaları 40, diğerleri P23 ve alt kümeleri); çıkış stop/hedef/kapanmış barda `CLOSE`/`MOVE_STOP` ile ifade edilir
  (TP merdiveni kısmi çıkış ister → dışarıda). Gerekçe: worker'ın kendi veri çekmesi yeni ağ yolu ve mevcut defterlerin
  önbelleğiyle etkileşim demekti (M2X §1.6 madde 3).
- **Tur içinde:** `_strategy_paper_tour`'un en sonunda, M2X'ten sonra, indeks yazımından önce; kendi fazı `auto`; defter
  başına `try/except`; faz bütçesi 5 sn (aşılırsa kalan oto defterler o tur atlanır, `AUTO_TIME_BUDGET`).
- **Giriş penceresi:** sinyal kapanışından 60 dk (D4 ile aynı); pencere kaçarsa giriş yok (`MISSED_WINDOW`), aynı sinyalle
  ikinci giriş yok (`one_entry_per_signal`). Çekirdeğin "sonraki bar açılışı" dolumuyla fark P4'te `exec_delay` olarak ölçülür
  ve S5r paritesinin toleransına girer.
- **Girdiler yalnız turun hazırladıkları:** `runner.last_frames` (salt okunur), `pmarks`/`pmarks_f`/`pgaps`, `pbars`
  (kapanmış 1h uçları), `funding_rates` (yalnız arama), `_frame_provenance`. Fiyatı/çerçevesi olmayan sembolde giriş yok
  (`AUTO_NO_MARK` / `DATA_FRAME_MISSING_*`).
- **Yürütme diğer defterlerle aynı:** `strategy_lib` niyeti → `strategy_paper.verify_paper_data` → `apply_action(learning=None)`
  → `apply_closed_bars` → `tick` → kayıt. Kendi `FuturesLedgerV2`'si (`state/auto_paper/<promo_id>/futures_ledger.json`;
  ücret/kayma/bracket/likidasyon config'teki gibi), kendi `RiskEngine`'i (profil kopyası + §7.6 tavanları) ve **bellek içi,
  kalıcı olmayan özel `KillSwitch`'i**. Kayıt `features.auto = {promo_id, variant_id, lib_sha, catalog_sha}` taşır.
- **Koruyucu izleyici:** oto tutamaçları listenin EN SONUNA eklenir (M2X'ten sonra); izleyici tek toplu `premiumIndex`
  kullandığı için diğer defterlerin fiyat partisi değişmez.

### 7.6 Sert tavanlar `AUTO_POLICY_V1` (worker kodunda sabit, mühürlü; config yalnız daha sıkı yapabilir)

| Tavan | Değer | Gerekçe |
|---|---|---|
| Etkin + bekleyen defter | ≤ 4 | izlenebilirlik; yanlış terfi bedelini sınırlar (soru S1) |
| Tek-coin defter / aynı coin / aynı aile | ≤ 2 / ≤ 1 / ≤ 2 | coin'e özel defterlerde yoğunlaşma; çeşitlilik |
| Başlangıç özsermayesi | ~~400 USDT~~ → **1.000 USDT** (sahip kararı 2026-10-06, S8) | %0,25 = 1 USDT: P4 adayı (200 × %0,5), keşif ve canlı defterlerle aynı mutlak risk → aynı min-notional ve yuvarlama davranışı (soru S8) |
| İşlem başı risk | %0,25 sabit | sahip kararı; öğrenme modu, slot, min-notional yükseltmesi yok |
| Kaldıraç | ≤ 3; likidasyon mesafesi ≥ 2 × stop | canlı defterlerin tavanlarını (3–5) aşmaz |
| Defter açık riski | ≤ özsermayenin %2'si (8 işlem) | |
| Bütün oto defterler açık riski | ≤ toplam oto özsermayenin %1,5'i | aynı anda dolu dört defter olmaz |
| Sembol başına (oto toplamı) | ≤ 2 USDT açık risk (S8 kararıyla ölçeklenir: ≤ 5 USDT = 1.000 × %0,5); defter başına sembolde 1 pozisyon | korelasyon |
| Günlük yeni giriş | ≤ 10 / defter / UTC günü | kaçak döngü koruması |
| Min-notional | < 5 USDT → ret | riski büyütmez |
| Düşüş | zirveden −%6 → yeni giriş yok (`DURAKLADI_DD`); −%10 → hepsi kapanır, `EMEKLİ_DD` | Kapı B sınırı 200'ün %8'i = 16 USDT = 400'ün %4'ü; −%6 bunun 1,5, −%10 2,5 katı (test edilen dağılımın dışı) |
| Motor kalp atışı | > 72 saat → yeni giriş yok (`ENGINE_SILENT`) | SAPMA izlemesi çalışmıyorsa açılmaz |
| Mod | yalnız PAPER; LIVE/LIVE_LIMITED'da `auto_paper.enabled: true` = `ConfigError` | gerçek para asla |
| Genel kapatma | `state/auto_paper/KILL` (sahip komutu), env `TRADINGBOT_AUTO_PAPER=off`, `auto_paper.enabled: false` | KILL yeniden başlatma istemez |

### 7.7 Emeklilik (otomatik) ve SAPMA

- **Worker'da (her tur):** düşüş kuralları; KILL (sonraki turda yeni giriş yok, açık oto pozisyonlar tur fiyatıyla kapanır,
  soru S3); BEKLEMEDE'de `--veto`; 3 ardışık tur istisnası → `DURAKLADI_ERROR` (pozisyonlar izleyiciyle korunur), 24 saat
  sürerse `EMEKLİ_ERROR`; kuralın çerçevesi 3 ardışık karar anında yok → `DURAKLADI_DATA` (7 gün sürerse `EMEKLİ_DATA`);
  `ENGINE_SILENT`; manifestin `catalog_sha`'sı yeni worker sürümünün kataloğuna eşit değil → `EMEKLİ_SKEW`.
- **Motorda (S5r, her gece hesap; karar yalnız bakışlarda: n = 30, 60, 100, sonra her 50 işlem):** ana belge §6.6 durdurma
  kümesi aynen (≥ 30 işlemden sonra ortalama R < −0,10; kayan 60 günlük ortalama R CI üst < 0; düşüş > 2 × geriye-test p95;
  günlük net R CUSUM h = 4σ) → `SAPMA_STAT`; n ≥ 60'ta canlı ortalama R < Kapı A OOS CI alt sınırı → `SAPMA_DIST`; parite:
  motor oto defterin kuralını depolanmış barlarda gölge olarak oynatır, ≥ 20 girişte sinyal eşleşmesi < %90 ya da eşleşenlerde
  medyan |ΔR| > 0,10 → `SAPMA_PARITY`. Motor geri çekme isteği yazar, worker uygular.
- **Kapanış biçimi:** SAPMA/SKEW/veto/hata → yeni giriş yok, açık pozisyonlar kendi kurallarıyla çıkar, 7 gün sonra kalanlar tur
  fiyatıyla kapanır. Düşüş −%10 ve KILL → sonraki turda hepsi kapanır. Emekli defter silinmez, aynı `promo_id` asla yeniden
  açılmaz (yeni kanıt = yeni aday = yeni `promo_id`).

### 7.8 Yalıtım değişmezleri (M2X §1.6 deseni; hepsi testli)

1. Ayrı öznitelik `engine.auto_books`; `strategy_books`, `_strategy_open_symbols`, `_funding_step`, `_chart_analysis_tour`,
   ortak deneyim `build_adapters`, `_lm_refresh`/`_lm_books`, `_strategy_paper_exit_check`, Box zamanlayıcısı listelerine girmez.
2. Sıra en son; kendi fazı; arızası hiçbir defteri etkilemez.
3. Kendi fiyat/ağ çağrısı yok: casus testi `runner.live.snapshot`, sağlayıcı, `FundingRates.refresh`, `_paper_marks`,
   `exit_check` çağrılarının sayısı, sembolleri ve sırası oto açık/kapalı aynıdır; oto kodu sıfır çağrı yapar.
4. `config_hash()` ve `SharedExperienceCollector.from_engine` özetinden `auto_paper` bölümü düşer (`m2x_aggressive` gibi);
   değer testte sabit (bölümsüz kodun aynı config'teki değerine eşit).
5. Kayıtlar değişmez: `paper_rules.spec_for`, `learning_mode.BOOK_NAMES`, `shared_experience.rows.BOOKS`,
   `bot_scorecard.BOOKS`/`MIRROR_BOOKS`, `strategy_paper_index.json` (oto girdisi yok; ayrı `state/auto_paper/index.json`).
6. Globlayıcılar: oto defterler iki düzey derindedir; `state/*/futures_ledger.json` onları görmez (karne, `book_snapshot`,
   motor `find_books`); okuyan her yer ayrı ve açık bölümle okur.
7. Paylaşılan değiştirilebilir durum yok: paylaşılan `KillSwitch` asla `trip` edilmez (casus); ama paylaşılan kill etkinse oto
   defterler de giriş açmaz (dur yönü). TradeMemory, öğrenici, danışman, CF kaydı, yapı deposu, karar günlüğü yok.
8. Yazma yalnız `state/auto_paper/**` (açma modu testi); `data/research/promotions` yalnız okunur.
9. AST: `tradingbot/autobook/` `research_engine`'i, `execution/*`'u ve ağ modüllerini import etmez; `engine_v3` onu tembel
   import eder; `strategy_lib` saf kalır.
10. **Bayt testi** (`tests/record_only_tour_runner.py`): sabit saat, sahte sağlayıcı; kabul edilmiş bir manifestle oto açık
    ve kapalı iki koşu. İzin verilen farkların TAM listesi: `state/auto_paper/**`; `protective_monitor.json`'daki oto anahtarları;
    oto defterli ilk-gözlem boşluk satırları; tur fazı süreleri ve `auto` fazı. Geri kalan her şey bayt bayt aynıdır (config_hash,
    karar günlükleri, `chart_analysis`, ortak deneyim ve danışman, öğrenme görünümü, CF'ler, yapı deposu, ana defter, T2/M2/Box/
    D4/C4/C4S/M2X defterleri ve özetleri, `strategy_paper_index.json`, `killswitch.json`, health'in süre dışı alanları). "Etkin
    ama manifest yok" koşusu kapalıyla (faz süresi hariç) aynıdır.

### 7.9 Tek seferlik worker sürümü `AUTO_v1`

- **İçerik:** `tradingbot/strategy_lib/` (P3b'de yazılır; worker ilk kez import eder), `tradingbot/autobook/{policy, catalog,
  manifest, verify, book, cli}.py`, `config_v3`'e katı anahtarlı `auto_paper` bölümü (`enabled`, `max_books` ≤ 4, `veto_hours`
  ≥ 24, `key_path`), `engine_v3` kancaları (kurulum, tur sonu, izleyici tutamaçları, config_hash düşürme, faz), karnede
  OTOMATİK bölümü, `auto-paper` alt komutu, anahtar üretimi, sürüm betiği (`--dry-run`, deploy = worker boşta durdur/başlat,
  `--check`, `--rollback`; 3 gün kuralı), testler (§8.1 P7a).
- **Sonra:** terfiler sürüm istemez. Sürüm isteyenler: yeni aile/dönüşüm türü, 1h/5m/altın/fonlama verisi, katalog değişikliği
  (katalog sha'sı değişince eski oto defterler `EMEKLİ_SKEW`; sessiz kayma yok).
- Mevcut defterlerin herhangi bir değişikliği bu yoldan **asla** yapılmaz (ana belge §9.1 değişmez).

### 7.10 İzleme

- **`--check`** ("OTOMATİK PAPER DEFTERLERİ"; AUTO_v1 betiği ve sonraki her worker/motor betiği): KILL, anahtar `kid`, motor
  kalp atışı yaşı; etkin/bekleyen/emekli sayısı ve tavanlar; defter başına `promo_id`, varyant, kapsam, durum, açılış, işlem
  sayısı, net R, özsermaye, düşüş, açık risk, son durum nedeni; son 7 günün red/bekleme nedenleri.
- **`digest_tr.md`** (≤ 10 satır): aynı özet + sabit uyarı: "Bu defterler 4.513+ deneme arasından seçildi; ileri testtir,
  kanıt değil. Hiçbir yerde avantaj yoksa bile ayda ≤ %5 olasılıkla yanlış terfi olabilir."
- **Günlük hedef:** ayrı `AUTO_PAPER` akışı, yalnız tanımlayıcı (hüküm ailesine girmez); `tgt_v1`'in `LIVE_PAPER` başlığı
  değişmez; "toplam (oto dahil)" bilgi satırı (soru S4).
- **Günlük/atıf/dersler (P7b):** motorun `ledgers.py`'sine `state/auto_paper/*/futures_ledger.json` ayrı akış olarak eklenir
  (S1a arşiv, S1b `tj_v1`, S2 atıf) → "neden kazandı/kaybetti" oto defterlerde de çalışır.
- `engine-query auto [<promo_id>]`, `python -m tradingbot auto-paper --status` (salt-okunur).

### 7.11 Geri alma

1. Anında: `sudo -u tradingbot … python -m tradingbot auto-paper --kill --operator berke --note "<neden>"` (yeniden başlatma yok).
2. Tek defter: `--retire <promo_id>`; bekleyen: `--veto <promo_id>`.
3. Config: `auto_paper.enabled: false` + yeniden başlatma (açık oto pozisyonlar DONAR; bu yüzden önce kill).
4. Kod: AUTO_v1 betiğinin `--rollback`'i (önceki worker sürümü); `state/auto_paper` kalır, okunmaz.
5. Motor: `engine-query auto --pause-promotions --operator berke` (yeni manifest yazılmaz) ya da gece zamanlayıcısını kapatmak
   (72 saat sonra `ENGINE_SILENT` oto girişleri de durdurur).

### 7.12 Arıza kipleri ve sınırlama

| Arıza | Sınırlama |
|---|---|
| Kısmi/bozuk manifest | atomik yazım; §7.4-1 `REJECT_SCHEMA`; motor ertesi gece yeniden yazar |
| İmza bozuk / anahtar yok / anahtar değişti | `REJECT_SIG`/`NO_KEY` (fail-closed); motor `kid` farkını görür, bekleyenleri yeniden imzalar; etkin defterler sürer |
| Motor ile worker sürüm kayması | `REJECT_SKEW`; `--check` ve özet uyarısı |
| Motor kapıyı yanlış değerlendirdi (alanlar tutarsız) | §7.4-7 `REJECT_EVIDENCE` |
| Motor kapıyı yanlış değerlendirdi (alanlar tutarlı ama yanlış) / yanlış pozitif aday | yakalanmaz; katalog + %0,25 + açık risk tavanları + −%6/−%10 + SAPMA sınırlar; en kötü ≈ 40 USDT kâğıt kayıp / defter |
| Uzun kesintiden sonra eski manifest | `valid_until` 14 gün → `EXPIRED` |
| Kapasite dolu, korelasyonlu aday | `WAITLIST`; motor etkin oto defterle günlük getiri korelasyonu > 0,7 olanı bekletir |
| State geri yüklemesi | görülmüş-kimlik listesi state'tedir; motor manifesti arşivlediyse yeniden kabul yok; arşivlenmeden önceki geri yüklemede aynı `promo_id` yeniden kabul edilirse `RESTORE_REACCEPT` dönemi ayrı izlenir |
| `AutoBook` istisnası, süre aşımı | defter başına `try/except`; 3 ardışık → `DURAKLADI_ERROR`; 5 sn faz bütçesi; bayt testi diğer defterlerin değişmediğini kanıtlar |
| Fiyat/çerçeve yok | giriş yok; açık pozisyonu izleyici (toplu `premiumIndex`) korur |
| Kaçak döngü | günlük 10 giriş, sembolde 1 pozisyon, açık risk tavanları |
| Paylaşılan kill tetiklenmesi | özel `KillSwitch`; casus testi |
| Motor durdu | 72 saat sonra `ENGINE_SILENT` |
| Gece/veri birimi ele geçirildi, sahte manifest | imza durdurmaz (aynı güven alanı); katalog + tavanlar + PAPER; veri birimi anahtarı göremez (P4b) |
| Worker yürütmesi motor çekirdeğinden ayrıştı | S5r parite → `SAPMA_PARITY` |
| LIVE moda geçiş | `ConfigError`; `AutoBook` kurulmaz |
| Motorun sahip kill/veto'sunu geri alması | imkânsız: `state` motor birimlerinde çekirdek düzeyinde salt-okunur |
| Katalog değişen worker sürümü | eski defterler `EMEKLİ_SKEW` |
| Giriş evreni değişti, oto defterin sembolü artık turda yok | o sembolde fiyat/çerçeve yok → giriş yok; açık pozisyonu izleyici korur; 7 gün sürerse `EMEKLİ_DATA` |

### 7.13 Dürüst etiket

Her yerde "OTOMATİK PAPER — ileri test, kanıt değil"; oto defterler `KANITLANDI`/`TUTTU` üretmez (`AUTO_PAPER` hüküm ailesinde
değildir, metin testi); k*/kaldıraç satırı oto defterler için de yalnız lider tablosundadır; motor kaldıraç artırmayı asla
önermez.

---

## 8. Yapım planı, mühür planı ve dağıtım sırası

### 8.1 Ajan boyutunda adımlar ve kabul testleri

| Adım | Teslimat | Kabul testleri (depo; sentetik veri, ağ yok) |
|---|---|---|
| **P3a** mühürler ve defter | `research_engine/registry.py` (`LIB_V1`, `WF_PROTOCOL_V1`, `STATS_V1`, `LESSON_REGISTRY_V1`, `PIT_RULES_V1`, `T_SEAL_MS`), `prior_trials.json`, `trials.py`, `library/config_epochs.json` üreticisi (git geçmişi + belgelenmiş sahip kararları), `engine-query trials` | sha'lar sabit; her alan değişikliği sha'yı değiştirir (özellik testi); yalnız ekleme + zincir; sayaçlar azalmaz; öncül = 2.775; `spec_sha` tekilleştirme; bütçe tablosu (818 / 1.738) koddan yeniden üretilir; "asla gevşetme" iskeleti |
| **P3b** kütüphane + çekirdek | `tradingbot/strategy_lib/` (B…O, kopyalar canlı fonksiyonlardan), `research_engine/kernel.py` | aile başına altın sentetik yollar; §2.1 parite (a) ve (b) (fixture ledger'lar, dönem başına); `strategy_lib` saflığı (AST: G/Ç, saat, ağ yok); belirlenimcilik; hız ölçümü (§5.3 tahmini güncellenir) |
| **P3c** PIT evren | `pit_universe.py` (veri birimi), `engine-data --backfill-pit` | delist olmuş sembollü S3 listesi fixture'ı doğru tarihler; gün d'de PIT evren; PIT yokken XSEC kapıya kapalı; P1b pencere/disk/`.CHECKSUM` kuralları; gece import grafiği `pit_universe`'ı içermez |
| **P3d** walk-forward + istatistik | `wf.py`, `stats.py`, `cscv.py`, `placebo.py` | `leakage_check`, ambargo; `COIN_FIT` üç yollu (`run_three_way` her adımın yalnız kendi verisini görür); maliyet senaryoları; `p_day` başvuru uygulamasıyla eşit; BH; DSR ampirik `sr_var`; CSCV bilinen cevap (gürültü → PBO 0,5 ± 0,1; dikilmiş avantaj → < 0,1); plasebo tohum belirlenimciliği; dikilmiş avantaj Kapı A'yı geçer, 200 boş dünyada geçme ≤ q; Kapı A yalnız bakış gecesinde (özellik) |
| **P3e** keşif + lider tablosu | `explore.py`, lider tablosu KEŞİF bölümü, `engine-query explore` | zaman yolculuğu (yalnız ≥ `T_SEAL_MS`); artımlı = baştan; hüküm kelimesi yok; 3.745 seri; ölçekli dünyada ≤ 10 dk |
| **P3f** dersler | `lessons.py`, `derived_specs.jsonl`, aylık `LIB_v1.<ay>` | bakış yalnız eşiklerde; alfa bölünmesi; BH ailesi; ileri doğrulama yalnız T_k sonrası; geçişler; runner `APPLIED_BOUNDED` yazamaz; dönüşüm → spec + O ile tekilleştirme; canlı kural dışlama; §5.10 dönem başına yeniden üretim |
| **P3g** gece + kaynak + sürüm | `night.py` S4x/S4w/S6 planı, bütçeler, `MEMORY_GUARD`, gerekirse birim dosyası, `tb-engine-<sha7>.sh` | büyük sentetik dünyada tam gece pencere içinde; `VmHWM` ölçümü ve §5.3 karar kuralı; Kapı A gecesi planı; birim sözleşmesi (`ENGINE_EXPECTED_MEMORY_MAX` = `MemoryMax`); bağımsız koşucu alt kümesi |
| **P4a** adaylar + kapılar | `prospective.py`, `promotion.py` (Kapı A doğumu, Kapı B, durdurma, Holm ayı), Kapı C önerileri | ana belge P4 kabul 1–7; Holm ayı tanımı; kapasite/kuyruk; yönlendirme (katalog içi → manifest, dışı → öneri) |
| **P4b** manifestler | manifest yazıcı (kanonik, HMAC, atomik), geri çekme istekleri, ack okuma/arşiv, `--pause-promotions`, `engine-query auto`, veri birimi `InaccessiblePaths` keys | altın manifest baytları; imza; eksik bir koşulda manifest **hiç** yazılmaz; anahtar değişince yeniden imza |
| **P7a** worker `AUTO_v1` | §7.9 | doğrulayıcı matrisi (her red nedeni); tavanlar; emeklilik yolları; kill/veto/unkill; yalıtım casusları; bayt testi; config_hash sabiti; LIVE `ConfigError`; geri yükleme; karne; `--check` |
| **P7b** motorda oto izleme | `ledgers.py` oto akışı, S5r (SAPMA, parite, CUSUM, kalp atışı), özet, `AUTO_PAPER` hedef akışı | SAPMA yalnız bakışlarda; parite fixture'ı; geri çekme isteği biçimi; özet ≤ 8 KB |

### 8.2 Mühür planı

| Mühür | İçerik | Ne zaman | Nerede sabitlenir |
|---|---|---|---|
| **S1** | `LIB_V1` (106 varyant, kapsamlar, P23, kümeler, §2.4 kuralları, örtüler, §4.3 dönüşümleri), `WF_PROTOCOL_V1`, `STATS_V1`, `LESSON_REGISTRY_V1`, `PIT_RULES_V1`, `PRIOR_TRIALS_V1`, `CONFIG_EPOCHS_V1`, `THRESHOLD_DIRECTIONS_V1` (asla gevşetme yön tablosu), `T_SEAL_MS` | P3a commit'i; **P3 kodunun hiçbir gerçek veri koşusundan önce** (hedef ≤ 2026-10-25) | `tests/test_research_engine_registry.py` + bu tabloya aynı commit'te sha değerleri (**MÜHÜRLENDİ**, değerler tablonun altında) |
| **S2** | `PROMOTION_REGISTRY_V1` (ana belge §6.7 + §3.7 yordamı + §6.3 Holm ayı + §6.4 yönlendirme), `AUTO_POLICY_V1`, `AUTO_CATALOG_V1`, `MANIFEST_SCHEMA_V1` | P4a commit'i, P4 dağıtımından önce, **sahibin kapı metni onayından sonra** | motor ve worker testlerinde aynı sabitler (eşitlik testi) |

**S1 mühür değerleri (P3a düzeltme mührü 2'nin — §8.5'e sahip cevabı, 2026-10-11 — yeniden mühür commit'i; `tradingbot/research_engine/registry.py`; sha256 = kanonik JSON'un — sıralı anahtar,
`(",", ":")`, UTF-8):**

- `LIB_V1` `5f05c27d474e458a391d488ae2eff09d5e2067420f901346eb1c8130164b1903` — 106 varyant (+2 alias'lı ızgara üyesi), kapsamlar, P23, kümeler, §2.4 kuralları, örtüler (faktör parametreleri spec'te), kopya zaman stopları, varyant bağımlılıkları (`requires`), §4.3 dönüşümleri; §8.5 kararları B2–B5, B7, B8, B10, B11 (2026-10-11)
- `WF_PROTOCOL_V1` `6857aa10b370140beb33bc690341c8df16707733ec66aa83acb2b1e9d1d3dc6d` — §3.1–§3.3 katmanlar (B1 bölüşümü), COIN_FIT seçimi, maliyetler; §2.1 çekirdek; §5.2 önbellek
- `STATS_V1` `4a75ce951e1eddb8024537c24ba3d200c823d761ec6b292e7c3875f5874d60e8` — §3.6 p_day, BH (ailede işlem süzgeci yok; ana belge §6.4), DSR (sr_var tabanı 1/(T−1)), CSCV-PBO, plasebo, boş dünya; B6 üreteç ve tohumlar
- `LESSON_REGISTRY_V1` `8fbed7f5e33e21a02ec9547bfd84ab7ad0a798d9cc25908aa99911244ad58836` — §4.1 hücreler/bakışlar/ileri doğrulama (B6 CI tohumu), §4.2 depo ve durumlar, §4.4 deneme maliyeti
- `PIT_RULES_V1` `59d0628c975e42aaa7810e231ce0704e5113b6a2bde4fd59fb29e65d8fef48b7` — §3.4 zaman noktasında evren (B11: hacim penceresi d hariç)
- `PRIOR_TRIALS_V1` `ad6d8903e66034ce96cafe006f0a071e254968308627ab9998b0464f96984921` — `research_engine/prior_trials.json` (§2.6; toplam 2.775)
- `CONFIG_EPOCHS_V1` `082f3ade6736566838f02c2710d40c97680949d65b59ed3f5c5605e72e872e0f` — `research_engine/library/config_epochs.json` (git geçmişi + belgelenmiş dağıtım/sahip kayıtları; `covered_until` = `T_SEAL_MS`)
- `THRESHOLD_DIRECTIONS_V1` `6ad438010cc06041644b356f1454e4b45092a49521665f3f513111bf1f36524b` — asla gevşetme yön tablosu (eşikler ve kapı-dışı eşik benzerleri; kök-göreli yollar; B1/B11 tanımları `fixed`)
- `T_SEAL_MS` `1791747600000` = 2026-10-11T19:40:00+00:00 (mühürlü içeriği değiştiren son commit'in işleyen zamanına eşit ya da ondan sonra; test git'ten doğrular, tarih elle ayarlanmaz)
- `REGISTRY_SHA` `8d115bd31c296aa07852c3c8323d54c655f9c07723ee6b25604482b9b04fa3c3` — S1 kümesinin (sekiz sha + `T_SEAL_MS`) kanonik sha'sı; deneme satırlarının `registry_sha`'sı

- "Gerçek veri koşusu": VPS'te S4/S5'in herhangi bir çalışması, P3 koduyla herhangi bir laboratuvar ya da elle geriye-test.
  P3b'nin hız ölçümü yalnız sentetik veriyle yapılır. Mühür commit'inin zamanı `T_SEAL_MS`'dir; ileri veri saati dağıtımdan
  bağımsız olarak burada başlar (veri birimi mühürden sonraki barları zaten saklar).
- **Asla gevşetme testi** (sahip kararı 2): `PROMOTION_REGISTRY` ve `AUTO_POLICY`'deki her eşik için yön tablosu (ör. q ≤, PBO <,
  DSR ≥, risk ≤, defter sayısı ≤); sonraki her sürümün değeri v1'den gevşekse CI kırılır.
- Mühürden sonra her değişiklik: `_v2`, yeni `SPEC`/`OPTION` satırları, bu belgeye tarihli "Değişiklik" notu.

### 8.3 Dağıtım sırası (sahip her VPS komutunu kendisi çalıştırır)

| Adım | En erken | Koşul |
|---|---|---|
| P1a | ≈ 2026-10-09 | planlandığı gibi |
| Hızlı kNN worker sürümü | ≥ 2026-10-12 | P0 tabanları sonra yeniden alınır |
| P1b + ilk doldurma | kNN yeniden başlatmasından ≥ 3 gün ve iki temiz `--check` sonra (≈ 2026-10-15/16) | doldurma 13–32 saat |
| P2 | doldurma bitti ve P1b'nin ilk `--check`'leri temiz (≈ 2026-10-20) | 14 gece A/B |
| **Mühür S1** | ≈ 2026-10-25 (dağıtımdan bağımsız) | gerçek veri koşusu yok |
| P3 (P3b–P3g) | P2'den sonra (≈ 2026-11 ortası) | ilk WF kurulumu ≈ 5–6 hafta |
| P4 (P4a–P4b) + **mühür S2** | ilk Kapı A bakışından önce (≤ 2026-12-05) | sahip kapı metnini onaylar |
| P7b motorda oto izleme | AUTO_v1'den önce | `ENGINE_SILENT` şartı |
| **AUTO_v1 worker sürümü** | ≈ 2027-01, en erken Holm (2027-02-01) öncesi; başka sürümün yeniden başlatmasından ≥ 3 gün | normal worker sürümü |

En erken olaylar: ilk Kapı A bakışı 2026-12-06 (yalnız 5m/15m; ileri 30 gün) ve 2027-01-06 (diğerleri); 4h/1d adayı için en
erken Holm 2027-03-01, en erken otomatik defter ≈ 2027-03-02 (24 saat veto penceresiyle).

### 8.4 P3a uygulama notu (2026-10-11; düzeltme turu 1 ve §8.5'e sahip cevabıyla yeniden mühürlendi)

**Ne mühürlendi (S1).** Yeni modüller ağsız ve yalnız standart kütüphaneyle yazıldı; hiçbiri gece birimine, worker'a ya da
bir karar yoluna bağlanmadı (P3a VPS'te hiçbir şey çalıştırmaz, gerçek veriye dokunmaz).

- `tradingbot/research_engine/registry.py`: `LIB_V1` (§2.1 sözleşmesi, §2.2 106 varyant + 2 alias'lı ızgara üyesi
  `DON_20_10_4h_LO ≡ K_D4` ve `BOX_n10_mid_ms0.5 ≡ K_BOX`, §2.3 kapsamlar, P23 ve kümeler, §2.4 kuralları, §2.5 bütçe
  bildirimi, O örtüleri — faktör parametreleri varyantın `params`'ında, yani `spec_sha`'da —, kopyaların canlı koddan
  okunan zaman stopları, §4.3 dönüşümleri ve aylık türeyen spec kuralları, §5.1 keşif sayımı, sahip kararları),
  `WF_PROTOCOL_V1` (§3.1 katman sınıfları, §3.2 `COIN_FIT` seçimi, §3.3 maliyetler, §2.1 çekirdek, §5.2 önbellek),
  `STATS_V1` (§3.6 `p_day`, BH, DSR + `sr_var` tabanı, CSCV-PBO + seçim grupları, plasebo, boş dünya), `LESSON_REGISTRY_V1`
  (§4.1 hücreler/bakışlar/ileri doğrulama, §4.2 depo ve durumlar, §4.4 deneme maliyeti), `PIT_RULES_V1` (§3.4) ve
  `THRESHOLD_DIRECTIONS_V1` ("asla gevşetme" yön tablosu). Kanonik bayt `json.dumps(sort_keys, (",", ":"),
  ensure_ascii=False)` UTF-8; kayıtlar iç içe donuk (`MappingProxyType`). `REGISTRY_SHA` = S1 kümesinin (sekiz sha +
  `T_SEAL_MS`) kanonik sha'sıdır; deneme satırlarının `registry_sha`'sı budur.
- `tradingbot/research_engine/prior_trials.json` (`PRIOR_TRIALS_V1`): §2.6'nın on satırı (toplam 2.775; fut_v1/fut_v2
  `count_lower_bound`) ve yedi "yeniden girmeyen" fikir (arama anahtar kelimeleriyle).
- `tradingbot/research_engine/library/config_epochs.json` (`CONFIG_EPOCHS_V1`): `epochs.py` + `scripts/gen_config_epochs.py`
  ile git geçmişinden (`deploy/releases/tb-deploy-*.sh` sürümlerinin `config.yaml` blob baytları; `git cat-file blob`,
  textconv ve imza gösterimi kapalı, UTF-8 katı) ve belgelenmiş dağıtım/sahip kararı kayıtlarından üretildi; `--check`
  aynı baytları yeniden üretir (test). Kurallar: defter görünümü = bütün config yolları − başka defterlerin yalnız-kendi
  bölümleri − belgelenmiş YALNIZ-KAYIT yollar (`shared_experience`, `history.evidence_subprocess`); görünüm değişen
  dağıtımda yeni dönem; dağıtım saati depoda yalnız pencereyle biliniyorsa `TRANSITION` dönemi; `E0` = taban `3b0ae8e` ve
  öncesi (belgelenmemiş). Pencere alt sınırı = dağıtımın olamayacağı son an (sürüm betiğinin ilk commit'i ya da önceki
  sürümün çalıştığı belgelenmiş son an; hangisi daha geçse). Sonuç (örn. Box): `E2026-09-28T2120Z` (öğrenme modu L1, en az
  stop %0,32) → `E2026-09-29T2243Z` (slot 40; ana belge §6.2'nin "2026-09-30" örnek sınırı TR tarihidir) →
  `X2026-10-04T114556Z` (f8b05fb'nin dağıtım saati depoda yok: alt sınır sürüm betiğinin ilk commit'i `f94ef5a`, üst sınır
  TOUR_CONTENTION_V1 §1'deki ilk f8b05fb turu) → `E2026-10-05T0455Z`. Gölge danışman ve ortak deneyim dönem açmaz.
  **Kapsam:** dosya `covered_until` = `T_SEAL_MS` taşır; V1 yalnız o ana kadarki dağıtımları bilir. P2 okuyucusu
  (`rehydrate.config_epoch`) `TRANSITION` penceresinde açılan işleme dönem ATAMAZ (`None`, `SEALED_TRANSITION`) ve
  `covered_until`'dan sonra açılan işlemi, o andan sonra başlayan bir dönem örtmedikçe son döneme YAZMAZ (`None`,
  `SEALED_UNCOVERED`); günlük bunu `config_epoch` eksik + neden notuyla kaydeder. Mühürle P3g arasındaki dağıtımlar (örn.
  ≥ 2026-10-12 kNN worker'ı) böylece sessizce son döneme karışmaz; sonraki dönemleri gece birimi açar (ana belge §6.2
  "yeni sha = yeni dönem", P3g). Dosya `data/research/library/`'ye **henüz kurulmaz**: gece birimi P3g'ye kadar P2'nin
  geçici sınırlarını kullanır. P2 notu 38(b) için dürüst not: mühürlü dosya da en az stop %0,32 rejimini slot
  değişikliğinde (§6.2'nin örnek sınırı) böler; ana belge §5.10'un yeniden üretimi aynı rejimin iki dönemini `opened_by`
  ile birleştirerek okumalıdır (P3f).
- `tradingbot/research_engine/trials.py`: `trials/trials.jsonl` yazarı. **Atomik ekleme:** eski baytlar + yeni satırlar
  aynı klasörde geçici dosyaya yazılır, `fsync`, geri okunur; eski baytların TAM önek olduğu ve hedefin değişmediği
  doğrulanır; `os.replace` + klasör `fsync` (OOM/SIGKILL/ENOSPC/elektrik kesintisi hedefi hiç değiştirmez; yarım satır
  yazılamaz). Modülde başka yeniden yazma/kesme/silme yolu yoktur (AST testi: tek `os.replace`, silme yalnız kendi geçici
  dosyası). Yazarlar `trials/trials.lock` üzerinde `flock(LOCK_EX)` alır; yeniden doğrulama ve yazım kilit içindedir.
  **Geri sarma:** her eklemeden sonra `trials/trials.hwm.json` (satır, baş sha, sayaçlar) atomik yazılır;
  `verify_chain(expect=HWM)` / `verify_ledger` dosya kısaldıysa ya da HWM'nin başı zincirde değilse `ROLLED_BACK` verir
  (satır sınırında kesme, eski yedeğin geri yüklenmesi; "sayaçlar azalmaz" artık dosyalar arasında da geçerli). HWM'nin
  gece özetine kopyası P3g'dedir (defterle birlikte eski HWM geri yüklenirse onu da yakalar). **Doğrulama** her girdiyi bir
  koda çevirir, istisna fırlatmaz: `TORN_TAIL` (sayaçlar/baş doğrulanmış tam satırlarındır), `BAD_JSON` (NaN, Infinity,
  1e999, geçersiz UTF-8, aşırı iç içe), `NOT_CANONICAL` (yalnız vekil dahil), `BAD_SCHEMA` (`seq`/sayaçlar düz tam sayı
  değil, sha'lar 64 hex değil), `BAD_SEQ`, `BAD_PREV`, `BAD_ROW_SHA`, `BAD_COUNTER`, `DUPLICATE`, `ROLLED_BACK`, `BAD_HWM`;
  kırık zincire eklenmez. **Sayaçlar** §3.5 tanımıyla; tekilleştirme anahtarı (LIB AİLESİ, `spec_sha`, kapsam): aylık
  `LIB_v1.<AAAA-AA>` mühürleri `LIB_v1` ailesidir (§4.3 T3/T4 "spec_sha aynıysa o satıra bağlanır"), yeni ana sürüm
  (`LIB_v2`) yeniden sayılır; `PRIOR` ve `EXPLORE_SEEN` satırının `n`'i kadar; `LESSON_LOOK` +1. `import_priors` (2.775, bir
  kez) ve `seal_entries` (LIB_v1 için 657 `SPEC` + 1.081 `OPTION` = 1.738; öncülle N = 4.513) testlidir. Gerçek `SPEC`
  satırları `module_src_sha` ister (P3b); defterin gece bağlantısı ve S6 zincir denetimi P3g'dedir.
- `engine-query trials [--like <metin>]` (salt-okunur, kilit almaz, ≤ 150 satır / ≤ 4 KB): zincir durumu (HWM dahil;
  `ROLLED_BACK`'te son yazımın satırı ve DSR N'i), sayaçlar, DSR N, koddan bütçe, boşluk/çalıştırılmayan varyant/protokol
  boşluğu sayıları; `--like` ile LIB_v1 varyantları, defter satırları (bütün eşleşmeler sayılır, en yeni 40'ı gösterilir;
  spec_sha öneki dahil), öncüller ve "ÖNCÜL — KANIT YOK" satırları.

**Koddan yeniden üretilen sayılar (testli):** §2.5 tablosu satır satır (havuz 45/45, küme 135/135, `COIN_FIT` 161/1.081,
kopya 312/312, örtü 140/140, diğer 25/25; toplam **818 / 1.738**), öncül **2.775**, mühürde N **4.513**, benzersiz varyant
106, ızgara 47, keşif 3.745, §2.2 "Oto" sütunu (S5 öncesi) 64.

**Asla gevşetme iskeleti.** `THRESHOLD_DIRECTIONS_V1` S1 mühür kümesinin parçasıdır (sha §8.2'de); yollar kök-görelidir.
Her kaydın `thresholds` ağacındaki her sayı ve eşik gibi davranan kapı-dışı alanlar (`budget_declared` "küçültülemez" →
`min`; `xsec_requires_pit`, `step_equals_test`, `data_start_earliest`, `dsr.sr_var_floor`, `null_world.pass_rate_max`,
BH ailesi tanımı → `fixed`) yönlüdür. `max`: q, alfa, pay, tavan; `min`: n, gün, maliyet, CI düzeyi, plasebo alt sınırı;
`fixed`: iki yönde de sonucu oynatabilen tanımlar — katman uzunlukları, ısınma, CSCV blokları, bakış eşikleri, yeniden
örnekleme sayıları (p'nin çözünürlüğü), plasebo K'si (CI genişliği), katman başına en az işlem, aday sayısı, PIT
eşikleri. Test, tablonun her eşiği kapsadığını, gevşetilmiş/kaldırılmış her değerin yakalandığını, sıkılaştırmanın kabul
edildiğini ve sonraki her sürümün (`REGISTRY_VERSIONS`) v1'den gevşek olmadığını sınar. Kapı A/B, Holm ve otomatik terfi
eşikleri (q_bh ≤ 0,10 kapı kullanımı, PBO < 0,25, DSR ≥ 0,95, …) §8.2 gereği S2'dedir; S2 yönlerini AYRI bir tabloyla ekler,
S1 tablosu değişmez.

**Okunuşlar (gerekçesiyle kayıtta, `readings_tr`):** F2 `n10/n20` = kenar bandı %10/%20 (alias K_BOX'un canlı near_frac'ı
0,10); K_BOX "en az stop %0,5" = öğrenme tabanı `learning_mode.books.b1_box_fade.min_stop_pct`; kopyaların değerleri mühür
anındaki canlı config'tir (`db5db96`, testte git'ten karşılaştırılır) ve zaman stopları canlı koddandır (D4 300 × 4h, C4 24
× 4h, FM 24 × 4h, Box gün sonu; testte koda karşı); LO'da ters sinyal = pozisyon yok; `f8` fut_v1 tanımı; spread z'nin
tersi yönde; XS_REV alt-k long; altın ORB yönü kırılım yönü; BBRE "orta bant" = kendi BB(20,2)'sinin orta çizgisi.
**BH ailesi (STATS_V1):** §3.6'daki "≥ 30 OOS işlemi olan" süzgeci m'yi küçültüp BH'yi gevşetirdi; ana belge §6.4'ün "o
bakışta değerlendirilen bütün spec × enstrüman satırları" tanımı daha sıkıdır ve bu belgenin önsözü gereği o mühürlendi:
aile = o bakışta değerlendirilen BÜTÜN etkin LIB satırları (kapıya kapalı XSEC ve boşluk yüzünden çalıştırılmayanlar
dahil); `p_day`'i hesaplanamayan satır p = 1 ile girer. **S5 ve "Oto" sütunu:** §2.2'nin "Oto" sütunu S5'ten önceki
tablodur; varyant alanı `oto_column_pre_s5` yalnız tarihsel kayıttır (H, G, 1h ve 5m için S5'ten sonra yanlıştır) ve hiçbir
yönlendirmede kullanılmaz. Otomatik yol / Kapı C yönlendirmesi YALNIZ S2'nin `AUTO_CATALOG_V1`'iyle yapılır (P4a).

**UNSPECIFIED (belge söylemiyor; uydurulmadı, `{"status": "UNSPECIFIED", "gap": …}`).** *(Düzeltme turu 1 durumu; çoğu
2026-10-11'de doldu — aşağıdaki değişiklik notu.)* Varyantın boşlukları = kendi
alanları + `requires` bağımlılıkları (ER20 kullanan → `defaults.er20_timeframe`; örtü → `covers.factors.<F>` ve
`copy_books.<taban>`; kopya ve F2 → `copy_books.<taban>`; `variant_gaps`). Böyle **48 varyant** (22'si S5 öncesi "Oto"
sütununda) gerçek veride hiçbir katmanda (WF, keşif, kapı) **çalıştırılmaz** (`UNSPECIFIED_PARAM`) ama bütçede **sayılır**:

- doğrudan: E — KEL ×2 orta çizgi türü, BBSQ ×2 "kırılım"ın referansı; F1 — RSI2 ×2'nin "orta bant" çıkışı (RSI2'de bant
  yok); H — dört FUND_FADE'in çıkışı ve "24h/72h" ekinin anlamı, FUND_CARRY yüzdelik penceresi; I — iki spread'in stopu ve
  bacak oranı; J — beş XSEC'in stopu/R birimi, XS_MOM beta penceresi ve haftalık dengeleme anı, XS_REV dengeleme sıklığı ve
  beta nötrlüğü; G — altı altın taktiğinin stopu, G_NY_ORB giriş son saati, G_ASIA_DRIFT'in "önceki gün yönü";
- ER20'nin dilimi → RSI2 ×2, BBRE ×2, FUND_FADE_LOW ×2, beş `O_*_F_ER`;
- F_BTC'nin "BTC 4h rejimi" → beş `O_*_F_BTC`;
- canlı T2/M2 kuralında zaman stopu yok → `max_hold` tanımsız → WF arındırma/ambargosu (1 + max_hold) ve `X_TIME`
  ("max_hold'un yarısı") tanımsız → `K_T2`, `K_M2` ve 14 örtüsü (`O_T2_*`, `O_M2_*`).

**Protokol boşlukları** *(düzeltme turu 1 durumu; 2026-10-11'de hepsi doldu)* (`protocol_gaps`; dolmadan ilgili satır
türünde hiçbir gerçek veri WF/kapı/ders koşusu yapılmaz):
`make_folds` purge/embargo bölüşümü (yalnız sınır başına "1 + max_hold" yazılı; aşağıdaki §8.5 soru 1), bootstrap
üretecinin algoritması (yalnız tohum), plasebo tohumunun bayt → tam sayı dönüşümü + `trade_key` tanımı + üreteci, ders
CI95'inin "sabit tohum"u, §2.4-4 "kâr payı"nın paydası (havuz/küme satırları), PIT hacim penceresinin d'yi içerip içermediği
(XSEC). Boşluk doldurmak mühür değişikliğidir (aşağıya bakın).

**Değişiklik 2026-10-11 — §8.5'e sahip cevabı ("Önerilenleri kabul et"; S1 düzeltme mührü 2).** §8.5'teki önerilen
varsayılanların hepsi kabul edildi ve kayda `decided(...)` ile yazıldı (`decision` = §8.5 maddesi, `reading_tr` "sahip
kararı 2026-10-11, §8.5 önerilen"; `LIB_V1.owner_decisions` `P3A_GAPS`): B1 (b) her sınırda purge + embargo = 1 + max_hold,
purge = ⌊(1 + max_hold)/2⌋, embargo = kalan (300 → 150 + 151; `make_folds` yalnız toplamı kullanır); B2 (a) T2/M2
kopyalarının `max_hold`'u yalnız WF için 300 × 1d (`wf_only`; canlı kural değişmez, X_TIME = 150 — dürüst not: kural zaman
stopsuz kaldığından 300 × 1d'den uzun bir T2/M2 işlemi sınır boşluğundan uzun sürebilir); B3 ER20 sinyal diliminde,
örtülerde tabanın sinyal diliminde, Box tabanında 1d; B4 F_BTC = `regime_gate.btc_regime` BTC 4h barlarında (kapanış >
EMA200 → UP; LONG yalnız UP'ta, SHORT yalnız DOWN'da; rejim bilinmiyorsa giriş yok); B5 RSI2 orta bant = BB(20,2) orta
çizgisi; B6 üreteç `random.Random(int(sha256(…).hexdigest()[:16], 16))` (cfgrid_v1 biçimi; p_day `row_id|look_no`, plasebo
`row_id|trade_key|k` ve `trade_key` = tj_v1 anahtarı, ders CI `cell_id|look_no`); B7 KEL orta çizgisi EMA20 (kapanış),
BBSQ kırılımı = BB(20,2) dışına kapanış; B8 FUND_FADE "24h/72h" = en çok tutuş 6/18 × 4h (çıkış zaman ya da stop;
`max_hold_bars` 6/18), FUND_CARRY yüzdelik penceresi 20 gün; B10 G_NY_ORB giriş son saati 18:00 UTC, G_ASIA_DRIFT yönü =
girişin UTC gününden önceki UTC gününün açılış → kapanışı (okunuş: sıfır getiride giriş yok); B11 §2.4-4 payda = pozitif
enstrüman net R'larının toplamı, PIT hacim penceresi d − 30 … d − 1. Üç yeni tanım asla-gevşetme tablosunda `fixed`
(`purge_embargo.make_folds_split.sum_bars`, `specialisation_rules.4_profit_share_def.denominator`,
`membership.volume_window_includes_d.includes_d`). **Kalan boşluklar** yalnız önerisi olmayanlardır: B9 (iki spread'in
stopu ve bacak oranı; beş XSEC'in stopu/R birimi, XS_MOM beta penceresi ve dengeleme anı, XS_REV sıklığı ve beta
nötrlüğü) ve B10'un altın stopları — 25 boşluk; **13 varyant** (I 2, J 5, G 6; hiçbiri S5 öncesi "Oto" sütununda değil)
çalıştırılmaz. Bütçede sayılan ama çalıştırılmayan pay koddan (`registry.blocked_budget`, testli): **25 Kapı A satırı /
25 ham deneme** (OTHER satırlarının hepsi). Aynı tanımla düzeltme turu 1'de 48 varyant, 274 satır, 504 ham denemeydi.
Protokol boşluğu kalmadı (`protocol_gaps` boş). Bütçe, öncül ve N değişmedi (818 / 1.738, 2.775, 4.513). Henüz hiçbir gerçek
veri koşusu olmadığı için bu `_v2` değil düzeltme mührüdür: bütün sha'lar, `REGISTRY_SHA` ve `T_SEAL_MS` yeniden sabitlendi
(§8.2); `config_epochs.json`'un `covered_until`'ı yeni `T_SEAL_MS`'tir.

**`T_SEAL_MS`.** Mühürlü içeriği (sabitlenmiş sha/T_SEAL satırları, `prior_trials.json`, `config_epochs.json`) değiştiren
EN SON commit'in işleyen zamanına eşit ya da ondan sonradır (en çok 24 saat); test bunu git'ten doğrular, commit tarihi elle
ayarlanmaz (değer commit'ten birkaç dakika sonraki tam dakikadır: aradaki veri ileri pencereye girmez, muhafazakâr). İçeriği
değiştiren her fix-up commit'i `T_SEAL_MS`'i yeniler; mühür commit'leri yalnız fast-forward (ya da merge commit) ile
birleşir — rebase/squash/cherry-pick işleyen zamanı değiştirir, test kırılır, yeniden mühür gerekir. Keşif katmanı ve Kapı
A'nın ileri okuması yalnız açılışı bu andan sonra olan barları kullanır.

**Testler.** `tests/test_research_engine_registry.py` (sha sabitleri ve belge eşitliği, her alan değişikliğinin sha'yı
değiştirdiği özellik testi, donma, bütçe/öncül/N yeniden üretimi, `spec_sha` tekilleştirme ve örtü faktör parametreleri,
varyant bağımlılık kuralı, kopya zaman stoplarının canlı koda eşitliği, asla gevşetme, §8.5 sahip kararı değerleri, UNSPECIFIED/çalıştırılmayan
varyant/protokol boşluğu listeleri, öncül dosyası, `T_SEAL_MS` ≥ son mühürlü değişiklik, config dönemleri: git'ten yeniden
üretim + belirlenimcilik + f8b05fb alt sınırı + P2 okuyucu uyumu (TRANSITION ve kapsam dışı), yalıtım);
`tests/test_research_engine_trials.py` (yalnız ekleme öneki, sayaçların azalmadığı özellik testi, her çökme noktasında
hedefin bozulmadığı, flock ile sıralanan yazarlar, satır sınırında kesme ve başka zincir = `ROLLED_BACK`, bozuk HWM,
kurcalama matrisi + JSON dışı değerler + zincir alanı türleri istisnasız kod, yarım satırda önek sayaçları, eşzamanlı
değişiklik reddi, LIB ailesi tekilleştirmesi, öncül 2.775, `seal_entries` 1.738 → N 4.513, sorgu sınırları, bütün
eşleşmelerin sayımı ve salt-okunurluk). P1b yalıtım testinin ağsız modül listesinde `registry`, `trials`, `epochs`, `query`
vardır. Bağımsız koşucu (VPS alt kümesi) değişmedi.

**Açık kalanlar.** (a) **Entegrasyondan önce** §8.5'teki boşluk soruları (önce katman bölüşümü) sahibe sorulur; cevaplar
boşlukları doldurur ve S1 YENİDEN mühürlenir (yeni sha'lar, yeni `T_SEAL_MS`; henüz hiçbir şey koşmadığı için `_v2`
değil, düzeltme mührüdür — boşluklu satırların ham denemeleri böylece iki kez sayılmaz). **2026-10-11: yapıldı** (yukarıdaki
değişiklik notu); B9 ve altın stopları için cevap gelirse aynı yolla doldurulur. (b) `config_epochs.json`'un, deneme defterinin ve HWM'nin gece birimine/özetine bağlanması (P3g;
`TRIALS_CHAIN_BROKEN` S6'da). (c) Mühürden sonraki config değişiklikleri için gece kuralı "yeni config sha = yeni dönem"
(ana belge §6.2) P3g'de; V1 dosyası değişmez. (d) S2 (`PROMOTION_REGISTRY_V1`, `AUTO_POLICY_V1`, `AUTO_CATALOG_V1`,
`MANIFEST_SCHEMA_V1`) sahibin kapı metni onayından sonra.

**P3b-1 uygulama notu (2026-10-11; temel: sözleşme, kopyalar, çekirdek, parite).** S1 mühürlü kayıtları DEĞİŞMEDİ
(`registry.py`, sha'lar, `REGISTRY_SHA`, `T_SEAL_MS` aynı); hiçbir canlı karar yolu, worker kodu ya da config değişmedi —
canlı kural fonksiyonları yalnız ÇAĞRILIR. Hiçbir şey gece birimine bağlanmadı; gerçek veri yok, ölçümler sentetik.

- **`tradingbot/strategy_lib/`** (saf; motor paketini anmaz ve import etmez — AST testi + alt süreçte `sys.modules`
  denetimi): `contract.py` — §2.1'in `Intent`'i (dokuz alan; ayrıca karara girmeyen bilgi alanı `evidence`), `EntryRef`
  (giriş referansı + giriş anında uygulanan kabul sınırları: `apply_action`'ın giriş kayması, Formasyonun kovalama
  sınırı), `Target` (mutlak fiyat ya da GERÇEK girişten R), `RiskAtrBounds` (alt, üst, ATR), `Action` (NONE / CLOSE /
  MOVE_STOP), `PosView`, `BookEntry`, `Variant`; `catalog.py` — 13 kopya, parametreler YALNIZ mühürlü `LIB_V1`
  varyantının `params`'ından (anahtar kümesi tam olmalı; eksik/fazla = ValueError); `identity.py` — `module_src_sha`
  (aşağıda); `livebars.py` — canlı çerçeve pencereleri (perp çerçevesi `PERP_FRAME_LIMITS` − oluşan bar: 1d 399, 4h 699,
  5m 499; günlükte `indicators.ema`/`atr`; Formasyon 4h 240; testte canlı koda eşit); kopyalar `k_trend` (T2/M2:
  canlı `StrategyBook.step`'in çağırdığı `paper_rules.decide_with_structures`; mühürdeki yapı kipi ENFORCE, kullanılmış
  yapılar canlı `used_patterns_of` ile kopyanın kendi girişlerinden), `k_box` (`decide_from_rows`; mühürdeki SHADOW yapı
  kipi kararı değiştirmediği için yapı analizi çağrılmaz), `k_d4` (`decide_from_rows`), `k_c4` (bağdaştırıcı, aşağıda),
  `k_fm` (`strategy_v3.build_plans_v3` + `structures.analyze`; `PatternBook._try_open`'ın kovalama ve risk aralığı
  kapıları niyette, zaman stopu 24 × 4h).
- **`ctx` okunuşu.** §2.1 `{now_ms, btc_bars?, funding?}`'e iki SALT-OKUNUR anahtar eklendi: `symbol` ve `history`
  (varyantın bu sembolde açtığı girişler). Gerekçe: canlı kurallar kendi defter geçmişine bakar (ortak yapı katmanının
  "kullanılmış yapı" kümesi, Formasyonun "aynı kırılımla tek giriş" kuralı); defter olmadan bunlar ancak geçmişle saf
  çağrılabilir. `one_entry_per_signal` çekirdektedir.
- **Saf çağrılamayan canlı fonksiyon → bağdaştırıcı (`k_c4`).** C4'ün giriş yolu (`candle_book.decide` → `_matched` →
  `candle_variations.gate`) HER çağrıda laboratuvar kaydını dosyadan okur (`lab_info` ile iki kez daha) ve reddi günlüğe
  yazar. `k_c4.signals` kapıyı atlar ve girişin saf parçalarını canlı modüllerin kendi fonksiyonlarıyla aynı sırada
  çağırır (`candle_book.window_rows` → `candle_dsl.window_from_rows` → giriş penceresi → `candle_variations.get` →
  `candle_dsl.detect_last`; eylem alanları `decide`'ın OPEN sözlüğüyle aynı). Kapı mühürde sekiz varyasyonun hepsinde
  açıktır (test). `manage` canlı fonksiyonun kendisidir (açık pozisyonda kapı çağrılmaz). Parite (a) bağdaştırıcıyı kapı
  DAHİL canlı `decide`'a karşı sınar. Diğer kopyalar canlı fonksiyonu doğrudan çağırır (dosya/ağ/saat yok).
- **Kimlik.** `module_src_sha` = varyant modülünün `tradingbot.*` import KAPANIŞININ (AST; modül ve fonksiyon düzeyi)
  dosya sha'larının özeti — kopyanın çağırdığı canlı kural kaynakları da girer (T2/M2 37, Box/D4/C4 37, FM 43 dosya); canlı
  kural değişirse `spec_sha` değişir. **`spec_sha` paket içinde hesaplanmaz:** kanonik fonksiyon mühürlü kayıttadır ve paket
  motoru import edemez; yeniden yazmak yerine motor tarafında `kernel.spec_sha_for` = `registry.spec_sha(registry.lib_spec(v,
  module_src_sha))` kullanılır; deneme defterinin `seal_entries` satırına eşitliği (alias'lar `DON_20_10_4h_LO ≡ K_D4`,
  `BOX_n10_mid_ms0.5 ≡ K_BOX` dahil) testlidir.
- **`research_engine/kernel.py`** (`kernel_v1`): karar bar kapanışında (yalnız kapanmış barlar), bar başına tek eylem;
  dolum sonraki açılış ± kayma; giriş kapıları canlı sırayla (kovalama, `STRATEGY_BAD_STOP`, giriş kayması, risk aralığı);
  bar içi yol `learning_cf` kuralı (açılış → ters → lehte → kapanış; giriş barı ters uçtan); stop seviyede, yalnız açılış
  boşluğunda açılıştan; hedef seviyede kaymasız (taker ücret); birden çok hedefte TP1 yarım + ücret/kayma dahil başa-baş
  (`break_even_price` formülü) yalnız SONRAKİ noktalarda; kural CLOSE'u ve `max_hold_bars` sonraki açılışta (önce açılış
  gözlemi); fonlama deponun gerçek uzlaşma damgalarında, (giriş, çıkış] ve o andan önceki dolumlarla açık miktar (açılışta
  kapanan pozisyonun çıkış anı açılış, diğerlerinde bar kapanışı); `FUNDING_GAP` = seri pencereyi örtmüyor ya da kendi
  tipik aralığının 1,5 katından uzun boşluk / okunamaz satır; büyüklük canlı `learning_mode.fit_size` (slot 1, rezerv 0,
  200 × %0,5 = 1 USDT), kaldıraç gerektiği kadar, likidasyon mesafesi ≥ 2 × stop gerçek bracket formülüyle denetlenir;
  likidasyon açılış boşluğunda ya da kayma dolumu ötesine iterse, `liquidation_outcome` aritmetiği; üç senaryo aynı yolda
  aritmetik yeniden maliyetlendirme (spread tik'i hedef dolumuna da uygulanır — muhafazakâr okunuş). Belirlenimci (bayt
  testi). Fiyat adımına kuantize etmez (defter eder; ΔR'nin ana kaynağı).
- **`research_engine/parity.py`** — (a) başvuru: canlı kural DOĞRUDAN (C4'te kapı dahil) → `strategy_paper.apply_action`
  → `FuturesLedgerV2.tick` (aynı `learning_cf` yolu, base maliyet, serinin fiyat adımı, §2.1 büyüklüğü: kaldıraç
  çekirdeğinkine eşitlenir, risk × 50; R ölçekten bağımsız); sinyal kimliği (sembol, sinyal barı, yön) iki yönde.
  (b) iskelet: POLICY kohortu (`learning_mode.classify_unlock_codes`; etiketsiz eski kayıt `UNTAGGED`, POLICY sayılmaz),
  dönem `rehydrate.config_epoch` + mühürlü `config_epochs.json` (TRANSITION ve kapsam dışı ATANMAZ), ≥ 20 girişli dönem
  kapılı: ≥ %95 aynı bar/yön ve medyan |ΔR| ≤ 0,05; hüküm `PASS` / `PARITY_FAIL` / `PARITY_UNPROVEN` (kapılı dönem yok:
  koşul gösterilemedi → uygun değil); `mark_parity` kopya satırını lider tablosu/kapı için uygunluk dışı yapar.
  `research_engine/synthetic.py` (belirlenimci sentetik dünyalar; gece grafiğinde değil), `scripts/engine_p3b_measure.py`
  (parite (a) ≥ 200 işlem/aile + hız; yalnız sentetik).
- **Parite (a) sonucu** (`scripts/engine_p3b_measure.py`, 2026-10-11, sentetik; eşikler %100 / 0,02 / 0,05):

| Aile | Dünya | Eşleşen işlem | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| |
|---|---|---|---|---|---|---|
| T2 (`K_T2`) | 31 | 207 | %100 | 3,3e-6 | 2,1e-5 | 7,3e-5 |
| M2 (`K_M2`) | 9 | 200 | %100 | 6,2e-6 | 1,9e-5 | 4,0e-5 |
| Box (`K_BOX`) | 1 (60 gün 5m) | 331 | %100 | 1,3e-4 | 3,2e-3 | 1,4e-2 |
| D4 (`K_D4`) | 7 | 200 | %100 | 9,4e-6 | 2,6e-4 | 1,0e-3 |
| C4 (`K_C4_CV001…008` birlikte) | 7 | 222 | %100 | 2,1e-6 | 2,2e-4 | 7,0e-4 |
| FM (`K_FM`) | 3 | 265 | %100 | 7,5e-8 | 5,9e-5 | 3,3e-4 |

  Hepsi geçti. ΔR'nin kaynağı yalnız defterin fiyat adımına kuantize etmesi ve miktar adımıdır (betikli yollarda — TP1 +
  başa-baş, boşluk, likidasyon, fonlama, short — çekirdek R'si defterin `r_multiple`'ına 1e-9 içinde eşit). Bu ölçü
  çekirdeğin aritmetiğini sınar; canlı defterle farkı (tur mark'ı, 60 dk giriş penceresi, 60 sn izleyici, öğrenme kipi,
  slot/teminat) parite (b)'nin işidir.
- **Testler:** `tests/test_strategy_lib_p3b.py` (saflık AST'i, motoru yüklememe, kimlik kapanışı, `spec_sha` = kayıt =
  deneme defteri, parametre kümesi, canlı pencere sabitleri, C4 kapısı mühürde açık, kullanılmış yapı okuyucusu),
  `tests/test_research_engine_kernel_p3b.py` (dolum/yol, boşluk, giriş boşluğu reddi, bar içi sıra, TP1/başa-baş, R'den
  hedef, kural/zaman çıkışı, MOVE_STOP, giriş kapıları, tek giriş, 1 USDT risk + likidasyon ≥ 2 × stop, likidasyon
  sonucu, fonlama damgaları/kısmi miktar/`FUNDING_GAP`, üç senaryonun elle hesabı, short, belirlenimcilik ve pencere),
  `tests/test_research_engine_parity_p3b.py` (altı K ailesinin altın sentetik yolu, bayt-aynı tekrar, aile başına parite
  (a), betikli defter çapraz denetimi, parite (b) PASS/FAIL/UNPROVEN, kohort ve dönem atama, `PARITY_FAIL` işareti;
  `TB_P3B_FULL_PARITY=1` ile ≥ 200 işlem/aile). P1b yalıtım testinin paket dışı import listesine `kernel.py` ve
  `parity.py` (gerekçesiyle) eklendi.
- **Açık kalanlar (P3b-2 ve sonrası).** (1) B…O aileleri (93 varyant) ve `trials.seal_entries`'in 106 tabanın
  `module_src_sha`'sıyla gerçek `SPEC` satırları (şimdi 13 kopya). (2) **T2/M2 yapı kipi:** mühürlü spec ENFORCE'tur (giriş
  zamanlaması + yapı çıkışı); canlı öğrenme modu 2026-09-28'den beri `structures_entry_shadow: [main, t2_trend_regime,
  m2_tsmom28]` ile YENİ GİRİŞİ yapı denetiminden çıkarır (yönetim ENFORCE). Kopya mühürlü spec'i izler; bu dönemlerde parite
  (b)'nin T2/M2'de canlıda olup kopyada olmayan girişler göstermesi beklenir → sahip kararı / okunuş gerekir (spec
  değişikliği mühür meselesidir, P3b-1'de yapılmadı). (3) C4 kapısının (dosya okuyan) motor tarafında koşu öncesi denetimi.
  (4) Formasyonun canlı-yalnız kapıları (likidite/spread/derinlik, kayıptan sonra bekleme `cooldown_bars_after_loss`, evren)
  kopyada yok; parite (b) "açıklanan fark" girdisi (slot/teminat retleri) şimdilik yalnız sayılır. (5) Hız: yukarıdaki
  §5.3 ölçüm notu — taban sinyal önbelleği (örtülerle paylaşım), DataFrame'siz bayt-aynı satır yolu, Box 5m. (6) Parite
  (b)'nin gece bağlantısı ve canlı defter okuyucusu (P3g); `funding` anahtarı (H ailesi) P3b-2'de doldurulur.

### 8.5 P3a boşlukları — sahibe sorular (entegrasyondan önce; her cevap boşluğu doldurur ve S1'i yeniden mühürler)

**Cevap (sahip, 2026-10-11): "Önerilenleri kabul et".** Önerilen her varsayılan kayda yazıldı (son sütun; §8.4
değişiklik notu); önerisi olmayanlar (B9, altın stopları) boşluk kalır ve o varyantlar çalıştırılmaz. Sıra, kapı
sonucuna etkiye göredir.

| # | Boşluk | Seçenekler ve etkisi | Önerilen varsayılan | Cevap (sahip, 2026-10-11) |
|---|---|---|---|---|
| B1 | **Katman bölüşümü** (`make_folds(purge_bars, embargo_bars)`; §3.1 "her iki sınırda 1 sinyal barı + max_hold") | (a) purge = embargo = 1 + max_hold (sınır başına 2 × 301 bar): 1d'de ilk test katmanı ≈ 2025-02, OOS bugün ≈ 540 gün, Kapı A'nın "≥ 2 takvim yılı"na ≈ 2027-02'de ulaşır; (b) purge + embargo = 1 + max_hold (örn. 150 + 151): ilk test ≈ 2023-06, OOS ≈ 1.170 gün. 4h'ta fark küçük (≈ 2021-11 / 2021-08). | Sahip kararı. (a) metnin daha muhafazakâr okunuşu; (b) sızıntıyı aynı ölçüde örter (sınırdaki toplam boşluk ≥ 1 + max_hold, sınırdan önce açılan işlemin tamamı; çapalı WF'de eğitim hep testten öncedir) ve 1d satırlarını ≈ 1,5 yıl öne alır. Öneri: (b) | **Kabul (b)**: purge = ⌊(1 + max_hold)/2⌋, embargo = kalan (300 → 150 + 151) |
| B2 | **T2/M2 kopyalarının `max_hold`'u** (canlı kuralda zaman stopu yok) | (a) yalnız WF için §2.2 varsayılanı 300 sinyal barı (kural değişmez; X_TIME = 150); (b) kopyalar ve 14 örtüsü v1'de WF'siz | (a) | **Kabul (a)**: `wf_only` 300 × 1d, X_TIME = 150; canlı kural değişmez |
| B3 | **ER20'nin dilimi** (RSI2, BBRE, FUND_FADE_LOW, `F_ER`) | sinyal dilimi mi; örtülerde tabanın sinyal dilimi mi (Box: 5m mi 1d mi) | sinyal dilimi; Box'ta 1d (kutu günlüktür) | **Kabul**: sinyal dilimi; örtüde tabanınki; Box 1d |
| B4 | **F_BTC "BTC 4h rejimi"** | `regime_gate.btc_regime`'in 4h uygulaması mı, BTC 4h EMA200 üstü/altı mı | `regime_gate` tanımı (T2'nin kullandığı), 4h barlarda | **Kabul**: `regime_gate.btc_regime`, BTC 4h barlarında |
| B5 | **RSI2 "orta bant"** | BB(20,2) orta çizgisi mi, RSI 50 mi | BB(20,2) orta çizgisi (BBRE ile aynı) | **Kabul**: BB(20,2) orta çizgisi |
| B6 | **Rastgelelik** (p_day üreteci, plasebo tohumu ve `trade_key`, ders CI tohumu) | üreteç ve tohum biçimi | `random.Random(int(sha256(...)[:16], 16))` (cfgrid_v1 biçimi); plasebo `row_id|trade_key|k`, `trade_key` = günlükteki `tj_v1` anahtarı; ders CI `sha256(cell_id|look_no)` | **Kabul**: önerildiği gibi (`random.Random`, üç tohum) |
| B7 | **E** KEL orta çizgisi; BBSQ "kırılım" | EMA20/SMA20; bant dışına kapanış | EMA20 (kapanış); BB(20,2) dışına kapanış | **Kabul**: EMA20 (kapanış); BB(20,2) dışına kapanış |
| B8 | **H** FUND_FADE çıkışı ve "24h/72h"; FUND_CARRY penceresi | ufuk mu, pencere mi | 24h/72h = en çok tutuş (6/18 × 4h); FUND_CARRY 20 gün | **Kabul**: en çok tutuş 6/18 × 4h; FUND_CARRY 20 gün |
| B9 | **I** spread stopu ve bacak oranı; **J** XSEC stopu/R birimi, beta penceresi, dengeleme anı, XS_REV sıklığı ve beta nötrlüğü | tasarım kararı | öneri yok (yeni araştırma tasarımı ister; cevap gelene kadar 7 varyant çalıştırılmaz) | **Boşluk kalır** (öneri yoktu; 7 varyant çalıştırılmaz) |
| B10 | **G** altın stopları, G_NY_ORB giriş son saati, G_ASIA_DRIFT "önceki gün yönü" | — | stoplar: öneri yok (tasarım kararı); NY 18:00 (LDN'deki "aralık sonu + 4 saat" benzeşimi); önceki UTC günün açılış → kapanışı | **Kabul**: NY 18:00 UTC; önceki UTC günün açılış → kapanışı (sıfır getiride giriş yok). **Stoplar boşluk kalır** (6 altın varyantı çalıştırılmaz) |
| B11 | §2.4-4 "kâr payı"nın paydası; PIT hacim penceresi d'yi içeriyor mu | — | pozitif enstrüman net R'larının toplamı (daha sıkı; toplam ≤ 0 iken satır zaten geçemez); d hariç (d − 30 … d − 1; d'nin hacmi gün bitmeden bilinmez) | **Kabul**: pozitif enstrüman net R toplamı; d hariç (d − 30 … d − 1) |

Cevaplar gelince: ilgili `gap(...)` alanları değerle değiştirilir (gerekçe `reading_tr`), `GAPS_PINNED`/`BLOCKED_PINNED`
küçülür, sha'lar ve `T_SEAL_MS` yeniden sabitlenir, bu tabloya tarihli cevap yazılır. **2026-10-11: yapıldı** (S1 düzeltme
mührü 2; kalan 25 boşluk / 13 varyant / 25 Kapı A satırı; protokol boşluğu yok).

---

## 9. Sahibe sorular (her biri için önerilen varsayılan)

**Sahip kararları (2026-10-06, mühürden ÖNCE):**
- **S5 — değişti:** 5m, 1h, altın ve fonlama taktikleri de **otomatik** yola girer; ayrı yazılı öneri ve sahip onayı
  istenmez ("5. taktiği sen aç ve veri topla, zaten amacımız veri toplaman"). Bunlar P4 gölgesinde (motorun kendi kâğıt
  kaydı) herkes gibi veri toplar ve Kapı A + B + Holm'u geçerse otomatik PAPER defteri olur. Worker'ın bu taktikler için
  ek veri çekmesi (1h/5m barları, altın sembolleri, fonlama) `AUTO_v1` sürümüne eklenir; bunun worker'a yükü yine A/B ile
  ÖLÇÜLÜR (onay değil, güvenlik ölçümü). Kapılar ve eşikler değişmez.
- **S8 — değişti:** otomatik defter başlangıç özsermayesi **1.000 USDT** (400 değil); işlem başı risk %0,25 (= 2,5 USDT),
  kaldıraç ≤ 3, düşüş −%6 yeni giriş durur / −%10 emeklilik AYNEN. USDT ile yazılmış tavanlar (sembol başına açık risk)
  aynı oranla ölçeklenir; P4 adayıyla "aynı mutlak risk" gerekçesi artık geçmez (min-notional/yuvarlama farkı Kapı B
  sapma denetiminde R ile ölçülür, USDT ile değil).
- **S1, S2, S3, S4, S6, S7, S9:** önerilen varsayılanlar kabul (sahip yalnız S5 ve S8'i değiştirdi).


| # | Soru | Önerilen varsayılan |
|---|---|---|
| S1 | Aynı anda en çok kaç otomatik PAPER defteri? | **4** (tek-coin ≤ 2, aynı coin ≤ 1, aynı aile ≤ 2) |
| S2 | Yeni otomatik defter ilk işlemden önce 24 saat "BEKLEMEDE" kalsın mı (onay değil, yalnız `--veto` fırsatı)? | **Evet, 24 saat** |
| S3 | `--kill` açık oto pozisyonları sonraki turda kapatsın mı, yoksa yalnız yeni girişi mi durdursun? | **Kapatsın** (PAPER; temiz durum) |
| S4 | Otomatik defterler günlük hedefin başlık toplamına girsin mi? | **Hayır**: ayrı `AUTO_PAPER` satırı + "toplam (oto dahil)" bilgi satırı; `tgt_v1` değişmez; 60 gün sonra yeniden sorulur |
| S5 | `AUTO_v1`'de 5m, 1h, altın ve fonlama taktikleri otomatiğe uygun değil (worker verisi yok); bunlar yazılı öneri + onayla ilerlesin mi? | **Evet**; ayrı bir veri sürümü (`AUTO_v1.1`) ancak A/B ölçümüyle ve sizin kararınızla |
| S6 | Gece birimi belleği, P3 ölçümü 350 MiB'ı aşarsa 512M → 800M'e çıksın mı? | **Evet**, yalnız §5.3 kuralı tetiklenirse |
| S7 | §2 bütçesi (106 varyant, 818 satır, 1.738 ham deneme), §6.3/§3.7 okunuşları ve §7.6 tavanları mühürlensin mi (S1 ve S2)? | **Evet, yazıldığı gibi** (ana belge §12 soru 6'nın cevabı da budur) |
| S8 | Otomatik defter başlangıcı 400 USDT, işlem başı %0,25 (= 1 USDT, canlı defterlerle aynı mutlak risk) olsun mu? | **Evet** |
| S9 | Terfi/emeklilik Telegram bildirimi? | **Hayır** (config değişikliği gerekir; `--check` ve özet yeterli) |

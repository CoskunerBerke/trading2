# Motorun "kendi beyni" v1 — P3, P4 ve otomatik PAPER terfisi (tasarım ve ön kayıt)

**Durum:** TASARIM ve ÖN KAYIT (2026-10-06); **P3a kodu ve mühür S1: 2026-10-11** (§8.2 değerleri, §8.4 uygulama notu);
**P3b-1 (sözleşme, kopyalar, çekirdek, parite): 2026-10-11** (§8.4 P3b-1 notu, §5.3 ölçüm notu); **çekirdeğin canlı
defterle aynı ihtiyatlı stop dolumu (`kernel_v2`, sahip kararı) ve S1 düzeltme mührü 3: 2026-10-12** (§8.4); **P3b-2a saf
aileler (düzeltme mührü 4) ve bağımsız incelemesinin düzeltmesi — teknik okumalar, S1 düzeltme mührü 5: 2026-10-12**
(§8.4). Dal `impl/engine-p3-design`, taban `cc88e9b` (P1a, P1b, P2a, P2b
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
  `MOVE_STOP(price)`} ve yalnız kapanmış barda değerlendirilir (eylem işaret de taşıyabilir — `Action.flags`, ör.
  `F8_UNMEASURABLE`; çekirdek işleme yazar, kararı değiştirmez; §8.4 2026-10-12 inceleme düzeltmesi; işaretin kapı etkisi
  mühürlü listededir: 2026-10-12 P3b-2b'den beri `F8_UNMEASURABLE` de `FUNDING_GAP`/`PATH_1H_GAP` gibi kapı
  istatistiğinden çıkar — `WF_PROTOCOL_V1.gate_exclusions`, `kernel.gate_excluded`). Paket `research_engine`'i import
  etmez; motor ve worker aynı modülü kullanır (tek kaynak).
- **Kopyalar** (`K_*`) yeniden yazılmaz: `paper_rules.decide_from_rows` ve `pattern_trader` kural fonksiyonları çağrılır.
- **Kimlik.** `spec_sha` = sha256(kanonik JSON `{family, variant_id, params, timeframes, exits, sizing, module_src_sha}`).
  Aynı `spec_sha` iki kez deneme sayılmaz (tekilleştirme); farklı kapsam farklı satırdır.
- **Çekirdek** `research_engine/kernel.py` (`kernel_v2`): karar bar kapanışında, dolum sonraki barın açılışında ± kayma;
  çıkış yolu **canlı defterinkidir** (sahip kararı 2026-10-12, §8.4): stop/likidasyon hükmü canlı defterin saf
  `exit_decision`'ı (`exit_fill_v1`), canlı defterin gördüğü tiklerde — pozisyon açıkken sinyal barının açılışında fiyat
  tiki, sonra barın 1h OHLC alt barları (sinyal dilimi ≤ 1h ise barın kendisi); stop seviyede, **kapanış da ötedeyse
  kapanıştan**, boşlukta açılıştan, en kötü uç iki seviyeyi de geçerse likidasyon, aynı tikte stop hedeften önce; girişi
  içeren 1h saat diliminde yalnız stop/likidasyon, hedef kredisi yok (mühürlü okunuş `kernel.entry_hour`, §8.4 P3b-2a); fonlama
  deponun **gerçek uzlaşma zaman damgalarında**; likidasyon bracket formülüyle; büyüklük sabit 1 USDT risk (200 USDT ×
  %0,5; keşifle aynı), kaldıraç büyüklükten türer, likidasyon mesafesi ≥ 2 × stop. Üç maliyet senaryosu aynı yol üzerinde
  aritmetik yeniden maliyetlendirmeyle hesaplanır (§3.3).
- **Parite (kabul):** (a) çekirdek ↔ `FuturesLedgerV2` + `strategy_paper.apply_action` + `tick` (canlı gibi 1h OHLC
  tikleri, kırpma yok): aile başına ≥ 200 sentetik
  işlemde sinyal kimliği %100, medyan |ΔR| ≤ 0,02, p95 ≤ 0,05; (b) kopya ↔ canlı defter: ana belge §6.2 (yalnız POLICY
  kohortu, ≥ 20 girişli her config döneminde ≥ %95 aynı bar/yön, medyan |ΔR| ≤ 0,05; kopya o dönemin config görünümünden
  kurulur, eşlenemeyen dönem `NOT_COMPARABLE` — raporlanır, kapıya girmez; okunuş §8.4 P3b-1 notu). (b) tutmayan kopya
  lider tablosuna ve kapıya girmez (`PARITY_FAIL`); kapılı karşılaştırılabilir dönemi olmayan kopya da girmez
  (`PARITY_UNPROVEN`).

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
- **P3g okuması (2026-10-13; §8.4 P3g notu).** Uygulanan sıra S0, S1a, S3, S1b, S2, **S4x, S4w, S6**, S7, S7b (S5/S5r P4'te).
  Paylar: S1b/S2 payları değişmedi (42 / 12 dk); S4x ≤ 10 dk ve S4w son tarih − 25 dk'ya kadar (Kapı A bakış gecesi − 55 dk);
  S6 ≤ 10 dk (bakış gecesi ≤ 40 dk), son tarih − 8 dk'da durur; S7/S7b her gece. A/B KAPALI ve SKEW gecelerinde S6 de
  planda yoktur (KAPALI gece P1a'dan beri yalnız S0 + S1s'tir). S1b'nin aynı gece günlüğü büyük ölçüde yeniden kurduğu
  gecede (bellek tepesi) S4w `S1B_REBUILD` nedeniyle atlanır. Kapı A satırları bu sürümde kurulmadığından ayın bakış gecesi
  `HESAPLANMADI` kaydıyla tüketilir (S4w yalnız o gece kısılır). WF önbelleği bu sürümde
  `backtests/cache/<veri ayı>/<birim>.jsonl.gz` (birim = coin × koşu grubu; spec kimlikleri başlıkta) ve
  `backtests/queue.json` kuyruğudur (parquet değil; içerik aynı: işlem listesi + günlük piyasa değerlemesi + sinyal anları).

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
  - **Ölçüm notu 2 (`kernel_v2`, 2026-10-12; sentetik; aynı sandbox, tek süreç, tek iş parçacığı, makinede başka yük
    yok; `kernel_v1` ve `kernel_v2` aynı dünyalarda sırayla, yapı önbelleği her koşudan önce temizlenerek).** Canlı çıkış
    yolu (açılış fiyat tiki + yalnız değen barda 1h alt bar tikleri, her tikte canlı `exit_decision`) bar başına işi
    ölçülebilir biçimde değiştirmedi — `kernel_v2` / `kernel_v1` süre oranı 0,98–1,01 (gürültü içinde): T2 60,2 → 59,8,
    M2 60,7 → 61,8, D4 94,9 → 96,2, C4 290,6 → 288,7, Formasyon 302,8 → 299,1, Box 146,7 → 149,0 bar/sn. Süre yine
    kopyaların bar başına canlı kural çağrısıdır; değmeyen barda alt bar açılmadığından 1d × 24 / 4h × 4 alt bar maliyete
    girmez. Yukarıdaki ≈ 18,6 çekirdek-saat (sandbox) tahmini ve P3b-2 önlemleri değişmez. (Parite ölçüm koşusundaki
    eşzamanlı hızlar da aynı mertebede: T2 56, M2 54, D4 85, C4 285, Formasyon 298, Box 146 bar/sn.)
  - **Ölçüm notu 3 (P3b-2a, 2026-10-12; saf aileler; sentetik; aynı sandbox, tek süreç, tek iş parçacığı; makinede iki
    parite ölçüm süreci eşzamanlı — 4 vCPU'da ayrı çekirdekler; `scripts/engine_p3b_measure.py --families
    B,C,D,E,F1,F2,H --speed-only`).** Çekirdek hızı (karar + bar içi yol, bar/sn; varyant başına aralık): **B 12.100**
    (10.100–13.800), **C 5.400** (4.800–6.100; iki EMA her barda pencerede), **D 11.800** (9.600–13.200), **E 5.800** (KEL
    4.150 — EMA + ATR her barda; BBSQ 9.700–10.000 — sıkışma sırası yalnız kırılım barında), **F1 7.700** (RSI2 5.300–6.500;
    BBRE 11.600–11.800), **H 5.900** (5.000–7.500; fut_v1 `align_funding` her barda), **F2 164** (160–167; canlı Box kuralı her
    5m barda, K_BOX ile aynı mertebe). Ölçekle (P23'ün 23 coin'i; 1d ≈ 2.450, 4h ≈ 14.700, 1h ≈ 58.800 bar ≈ 2020→bugün;
    5m ≈ 3 yıl ≈ 315.000 bar) bir tam geçmiş koşusu: B 0,013 + C 0,08 + D 0,03 + E 0,16 + F1 0,12 + H 0,08 ≈ **0,5
    çekirdek-saat** (38 saf sayısal varyant; pencere başına gösterge hesabı, bar başına canlı kural çağrısından ≈ 15–250 kat ucuz) ama
    **F2 ≈ 86 çekirdek-saat** (7 × 23 × 315.000 / 164; sandbox) ≈ 258 saat VPS. K + B…H ≈ 18,6 + 0,5 + 86 ≈ **105
    çekirdek-saat (sandbox)** ≈ 315 saat VPS; bunun ≈ %95'i Box 5m'dir (K_BOX + yedi F2). **Sonuç:** "106 varyant ≈ 8
    çekirdek-saat" tahmini saf sayısal aileler için TUTAR (fazlasıyla), Box ailesi (K_BOX, F2, Box örtüleri) için TUTMAZ;
    P3b-2b'nin önlemlerinden (3) Box 5m ön-süzgeci (kutu günlük olduğundan kenar bandına girmeyen 5m barlarda kural çağrısı
    yok; F2'nin yedi ekseni ve K_BOX aynı günlük kutuyu paylaşır) belirleyicidir ve ilk WF kurulumundan önce bayt-aynılık
    testiyle yapılmalıdır; saf ailelerde artımlı gösterge (pencere başına yeniden hesap yerine) ek kazançtır ama gerekli
    değildir. Bellek: saf ailelerde pencere ≤ 400 bar × 4 sütun; değişmedi.
  - **Ölçüm notu 4 (P3b-2b, 2026-10-12; hız önlemleri sonrası; sentetik; aynı sandbox, tek süreç, tek iş parçacığı;
    makinede bir–iki parite ölçüm süreci eşzamanlı — ayrı çekirdekler; `scripts/engine_p3b_measure.py --speed-only`,
    düz yol `--slow`, örtüler `--families O`).** Üç önlem (her biri düz yolla bayt-aynılık testli; §8.4 P3b-2b notu):
    hızlı çerçeve (`livebars.FastFrame`, C4 penceresi `candle_window`), Box ve D4 ön-süzgeci, paylaşılan taban akışı +
    ailenin bar bar koşusu (`kernel.make_family`, `run_lockstep`). Çekirdek hızı (bar/sn; önce = aynı makinede düz yol
    `fast=False`, sonra = hızlı yol):

| Aile | Önce | Sonra | Kat | Ana kazanç |
|---|---|---|---|---|
| T2 (`K_T2`) | 58 | 122 | 2,1 | çerçeve (yapı analizi canlıdır, kalır) |
| M2 (`K_M2`) | 56 | 116 | 2,1 | çerçeve |
| D4 (`K_D4`) | 71 | 2.750 | 39 | ön-süzgeç (`signal_lab` yalnız olası kırılım/çıkış barında) + çerçeve |
| C4 (`K_C4_CV001…008`, varyasyon başına) | 242 | 1.890 | 7,8 | çerçeve + pencere |
| Formasyon (`K_FM`) | 239 | 295 | — | dokunulmadı (canlı yapı analizi ve plan; çerçeve zaten pandas'sız) |
| Box (`K_BOX`, 5m) | 126 | 21.400 | 171 | ön-süzgeç (kural barların ≈ %13'ünde çağrılır) + çerçeve |
| F2 (Box ızgarası, 7) | 148 | 21.700 | 147 | aynı (F2 aynı sınıf) |
| B, C, D, E, F1, H (saf) | — | 8.600 / 5.040 / 12.350 / 5.660 / 7.680 / 4.160 | — | değişmedi (P3b-2a ile aynı mertebe) |

    Örtü aileleri (taban başına K satırı + yedi örtü, paylaşılan akışla `run_lockstep`; C4'te K satırı yok — taban C4
    defteri; "aile geçişi" = serinin barı başına bütün koşular): O_T2 69 seri bar/sn (8 koşu → koşu başına ≈ 550), O_M2
    42 (≈ 340), O_BOX 2.255 (≈ 18.000), O_D4 946 (≈ 7.570), O_C4 577 (7 koşu → ≈ 4.040; son kodla parite koşusunda 602 → ≈ 4.210, tahminde 577 kullanıldı). Akış isabeti: Box/D4/C4'te
    sinyal çağrılarının %81–85'i önbellekten; T2/M2'de %52–68 (yapı katmanı kopyanın kendi girişlerine baktığından
    anahtar geçmiştir — filtre örtülerinin girişleri ayrışınca paylaşım düşer; `run_lockstep` bunu canlı yapı analizinin
    içerik adresli önbelleğiyle telafi eder, M2 ailesi 19 → 11 sn). **Tam geçmiş tahmini** (aynı ölçek: P23'ün 23 coin'i;
    1d ≈ 2.450, 4h ≈ 14.700, 1h ≈ 58.800, 5m ≈ 315.000 bar; kopyaların/örtülerin P23 dışı canlı sembolleri sayılmadı → alt
    sınır), çekirdek-saat (sandbox): K_C4 0,40 + Formasyon 0,32 + F2 0,65 + saf B…H 0,53 + O_T2 ailesi (K_T2 dahil) 0,23 +
    O_M2 (K_M2 dahil) 0,37 + O_BOX (K_BOX dahil) 0,89 + O_D4 (K_D4 dahil) 0,10 + O_C4 0,16 ≈ **3,7 çekirdek-saat**
    ≈ **11 saat VPS** (× 3). Önce (ölçüm notları 1 ve 3): K ≈ 18,6 + B…H 0,5 + F2 ≈ 86 + O ≈ 128 (taban her örtüde yeniden)
    ≈ **233 çekirdek-saat** ≈ 700 saat VPS → ≈ **60 kat** azaldı. Gece bütçesiyle (S4w ≈ 60–80 dk, §5.2): **K + B…H + O
    tam geçişi ≈ 8–11 AÇIK gece** (A/B dönüşümlü olduğundan ≈ 2,5–3,5 takvim haftası); yukarıdaki "106 varyant ≈ 8
    çekirdek-saat → 18–24 gece" tahmininin içinde. Dürüst sınırlar: (1) sentetik dünyalar — sentetik Box dünyasında
    kural barların ≈ %13'ünde çağrılır ve pozisyonsuzken barların ≈ %8'inde giriş üretir; gerçek 5m serilerinde bu paylar
    ve pozisyonda geçen süre farklıdır ve Box ailelerinin süresi onlarla orantılıdır, (2) VPS çarpanı (× 3) P3g'de
    ölçülür, (3) Formasyon ve T2/M2'nin süresi canlı yapı analizidir (canlı koda dokunulmadı), (4) P23 dışı canlı
    semboller ve WF katmanlarının ek yükü (seçim, CSCV, plasebo) P3d/P3g'de eklenir — bu tahmin yalnız çekirdek
    geçişidir. Bellek: paylaşılan akış boş kararları bit haritasında tutar (5m 3 yıl ≈ 40 KB), dolu kararlar ve yönetim
    önbelleği yalnız 1d/4h tabanlarında (bar başına bir giriş); değişmedi.
- **Disk:** trials ≤ 50 MB/yıl, keşif ≈ 200 MB/yıl, WF önbelleği ≈ 0,5 GB, 14 gecelik ayrıntı ≈ 0,3 GB, adaylar ≈ 50 MB, PIT 1d
  ≈ 0,1 GB → ≤ 1,5 GB (15/20 GB sınırlarının içinde).
- **Kabul (VPS, 14 A/B gecesi):** tur p95 ≤ KAPALI + %5; `NRestarts` değişmez; `memory.peak` ≤ 0,8 × MemoryMax; Box kaçan
  bar ve koruyucu izleyici gecikmesi KAPALI tabanında; 418/429 = 0.
- **P3g okuması (2026-10-13): bugünkü birim 640M/768M.** Yukarıdaki "bugün 400M / 512M" metni P1a birimidir; P2
  (2026-10-08, inceleme M6) gece birimini `MemoryHigh=640M` / `MemoryMax=768M`'e (`ENGINE_EXPECTED_MEMORY_MAX=805306368`)
  çıkarmıştı. Karar kuralı bu birime oransal olarak uygulandı: eşik = `MEMORY_GUARD` eşiği 0,7 × 768 MiB = 537,6 MiB
  (eski kuralın 350/512 ≈ 0,68 oranının karşılığı); büyük sentetik dünyada ölçülen tam gece `VmHWM` bunun altında kaldığı
  için birim DEĞİŞMEDİ (ölçümler §8.4 P3g notunda). `MEMORY_GUARD` sınırı cgroup `memory.max`'tır, okunamazsa
  `ENGINE_EXPECTED_MEMORY_MAX` (birim sözleşmesi: ikisi aynı bayt sayısı).

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
| **S1d** | `LIB_AG_V1` (38 varyant: ANTİGRAVİTY dökümlerinin 5 taktiği + 3 örtüsü, tam parametreler, kapsam `AG_POOLED`, seçim grupları, bütçe), `AG_UNIVERSE_V1` (S1b'nin 40'ı + XAUUSDT), `AG_CALENDAR_V1` (2023–2026 NYSE tam tatilleri + FOMC açıklama günleri), `S1D_DIRECTIONS_V1`, `S1D_SEAL_MS` | S1d commit'i (2026-10-13), AG kodunun hiçbir gerçek veri koşusundan önce; S1, S1b, S1c dokunulmadı | `research_engine/seal_s1d.py` + `tests/test_research_engine_seal_s1d.py` (**MÜHÜRLENDİ**, değerler §8.6) |
| **S2** | `PROMOTION_REGISTRY_V1` (ana belge §6.7 + §3.7 yordamı + §6.3 Holm ayı + §6.4 yönlendirme), `AUTO_POLICY_V1`, `AUTO_CATALOG_V1`, `MANIFEST_SCHEMA_V1` | P4a commit'i, P4 dağıtımından önce, **sahibin kapı metni onayından sonra** | motor ve worker testlerinde aynı sabitler (eşitlik testi) |

**S1 mühür değerleri (düzeltme mührü 9'un — CSCV blok kenarı arındırması geri alındı, CSCV matrisi günlük piyasa değerlemesi (U3), 2026-10-13, §8.4 — yeniden mühür commit'i; önceki: düzeltme mührü 8, P3d bağımsız incelemesi: seçim grubu bütünlüğü (R3) ve plasebo kurulumu (R5) `STATS_V1`'de, 2026-10-13; düzeltme mührü 7, P3d: plasebo fark CI'ı, CSCV ölçüsü ve iki kapalı hata kuralı `STATS_V1`'de tarihli okuma (U2–U5), 2026-10-13; düzeltme mührü 6, P3b-2b: O örtülerinin uygulama okumaları `LIB_V1`'de ve kapı dışı işaret listesi `WF_PROTOCOL_V1.gate_exclusions`'ta, 2026-10-12; düzeltme mührü 5, P3b-2a bağımsız incelemesinin bulgularına tarihli teknik okumalar `LIB_V1` satırlarında; düzeltme mührü 4, P3b-2a çekirdeğin giriş saati okunuşu `WF_PROTOCOL_V1.kernel.entry_hour`; düzeltme mührü 3, çekirdeğin canlı defterle aynı ihtiyatlı stop dolumu, sahip kararı 2026-10-12; düzeltme mührü 2, §8.5'e sahip cevabı 2026-10-11; `tradingbot/research_engine/registry.py`; sha256 = kanonik JSON'un — sıralı anahtar,
`(",", ":")`, UTF-8):**

- `LIB_V1` `ae237b55c6df7014a52649915342eeb0bd67d23a18edcee5cf3e4b8c773df2e3` — 106 varyant (+2 alias'lı ızgara üyesi), kapsamlar, P23, kümeler, §2.4 kuralları, örtüler (faktör parametreleri spec'te), kopya zaman stopları, varyant bağımlılıkları (`requires`), §4.3 dönüşümleri; §8.5 kararları B2–B5, B7, B8, B10, B11 (2026-10-11); P3b-2a inceleme teknik okumaları (2026-10-12): BBSQ `params.squeeze_bar` (sıkışma t − 1'de), FUND_CARRY `exits.exit_rule` (işaret ters → çık), RSI2 `params.entry_mid_side` (orta bandın doğru yanı), `defaults.window` (saf ailelerin penceresi 400 / 399); P3b-2b örtü uygulama okumaları (2026-10-12): `covers.reading`, yedi `covers.factors.<F>.reading` (35 örtünün `factor_params`'ında)
- `WF_PROTOCOL_V1` `085ca94c3a8abc007327a15c217eea7d56a5b9af7b5075ec42714d5424af46b4` — §3.1–§3.3 katmanlar (B1 bölüşümü), COIN_FIT seçimi, maliyetler; §2.1 çekirdek (2026-10-12: `kernel.stop_fill` = canlı `exit_decision` 1h tiklerinde, `intrabar_order` canlı tik sırası; P3b-2a: `kernel.entry_hour` = girişi içeren saat diliminde yalnız stop/likidasyon); §5.2 önbellek; P3b-2b: `gate_exclusions` (kapı istatistiğinden çıkan işaretler FUNDING_GAP, PATH_1H_GAP, F8_UNMEASURABLE)
- `STATS_V1` `fda79c7565db098f76379d5037443e119723367fdba535d8a8ed9897c57bf60e` — §3.6 p_day, BH (ailede işlem süzgeci yok; ana belge §6.4), DSR (sr_var tabanı 1/(T−1)), CSCV-PBO, plasebo, boş dünya; B6 üreteç ve tohumlar; P3d okumaları (2026-10-13): `placebo.ci` (U2), `pbo.metric` (U3), `placebo.no_eligible_bar` (U4), `pbo.single_config_group` (U5); düzeltme mührü 8: `pbo.group_completeness` (R3), `placebo.construction` (R5); düzeltme mührü 9: `pbo.metric.matrix_returns`/`matrix_fallback` (U3; günlük piyasa değerlemesi, blok kenarı arındırması kaldırıldı)
- `LESSON_REGISTRY_V1` `8fbed7f5e33e21a02ec9547bfd84ab7ad0a798d9cc25908aa99911244ad58836` — §4.1 hücreler/bakışlar/ileri doğrulama (B6 CI tohumu), §4.2 depo ve durumlar, §4.4 deneme maliyeti
- `PIT_RULES_V1` `59d0628c975e42aaa7810e231ce0704e5113b6a2bde4fd59fb29e65d8fef48b7` — §3.4 zaman noktasında evren (B11: hacim penceresi d hariç)
- `PRIOR_TRIALS_V1` `ad6d8903e66034ce96cafe006f0a071e254968308627ab9998b0464f96984921` — `research_engine/prior_trials.json` (§2.6; toplam 2.775)
- `CONFIG_EPOCHS_V1` `38126e55acda08dc414a3aec209f24933ea025a2f98843f4f8ad9f02f2172e65` — `research_engine/library/config_epochs.json` (git geçmişi + belgelenmiş dağıtım/sahip kayıtları; `covered_until` = `T_SEAL_MS`)
- `THRESHOLD_DIRECTIONS_V1` `6ccbdfcecd55c240ff445b7ab39abedd6696587d54b04712ab0c28fc1e9fb56f` — asla gevşetme yön tablosu (eşikler ve kapı-dışı eşik benzerleri; kök-göreli yollar; B1/B11 tanımları ve 2026-10-12'den `kernel.stop_fill.policy`, `kernel.entry_hour.policy` ve `gate_exclusions.flags` `fixed`; 2026-10-13'ten P3d okumaları: `placebo.ci.min_days_per_side` `min`, CI tekrar/tohum/düzen, CSCV ölçüsü ve iki kapalı hata kuralı `fixed`; düzeltme mührü 8'in grup bütünlüğü ve plasebo kurulumu ve düzeltme mührü 9'un CSCV matris tanımı `fixed`)
- `T_SEAL_MS` `1791885600000` = 2026-10-13T10:00:00+00:00 (mühürlü içeriği değiştiren son commit'in işleyen zamanına eşit ya da ondan sonra; test git'ten doğrular, tarih elle ayarlanmaz)
- `REGISTRY_SHA` `3e700fa03e455237c5ef45e29b85e5a810a9f8f7bd9dfe88465e543a5620beaf` — S1 kümesinin (sekiz sha + `T_SEAL_MS`) kanonik sha'sı; deneme satırlarının `registry_sha`'sı

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

### 8.4 P3a uygulama notu (2026-10-11; düzeltme turu 1 ve §8.5'e sahip cevabıyla yeniden mühürlendi; 2026-10-12 çekirdeğin stop dolumuyla düzeltme mührü 3; 2026-10-12 P3b-2a saf aileler ve giriş saati okunuşuyla düzeltme mührü 4; 2026-10-12 P3b-2a inceleme düzeltmesi ve teknik okumalarla düzeltme mührü 5; 2026-10-12 P3b-2b O örtüleri, hız önlemleri ve kapı dışı işaretlerle düzeltme mührü 6; 2026-10-13 P3d walk-forward ve istatistik; 2026-10-13 P3d okumalarıyla düzeltme mührü 7; 2026-10-13 P3d bağımsız incelemesiyle düzeltme mührü 8; 2026-10-13 CSCV günlük piyasa değerlemesiyle düzeltme mührü 9; 2026-10-13 P3e keşif katmanı; 2026-10-13 P3f dersler ve S1c mührü)

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

**P3b-1 uygulama notu (2026-10-11; temel: sözleşme, kopyalar, çekirdek, parite; bağımsız inceleme düzeltme turu 1 aynı
gün — aşağıda "Düzeltme turu 1").** S1 mühürlü kayıtları DEĞİŞMEDİ
(`registry.py`, sha'lar, `REGISTRY_SHA`, `T_SEAL_MS` aynı); hiçbir canlı karar yolu, worker kodu ya da config değişmedi —
canlı kural fonksiyonları yalnız ÇAĞRILIR. Hiçbir şey gece birimine bağlanmadı; gerçek veri yok, ölçümler sentetik.

- **`tradingbot/strategy_lib/`** (saf; motor paketini anmaz ve import etmez — AST testi + alt süreçte `sys.modules`
  denetimi): `contract.py` — §2.1'in `Intent`'i (dokuz alan; ayrıca karara girmeyen bilgi alanı `evidence`), `EntryRef`
  (giriş referansı + giriş anında uygulanan kabul sınırları: `apply_action`'ın giriş kayması, Formasyonun kovalama
  sınırı ve maliyet sonrası R/R tabanı), `Target` (mutlak fiyat ya da GERÇEK girişten R), `RiskAtrBounds` (alt, üst,
  ATR), `Action` (NONE / CLOSE / MOVE_STOP), `PosView`, `BookEntry`, `Variant`; `catalog.py` — 13 kopya, parametreler YALNIZ mühürlü `LIB_V1`
  varyantının `params`'ından (anahtar kümesi tam olmalı; eksik/fazla = ValueError); `identity.py` — `module_src_sha`
  (aşağıda); `livebars.py` — canlı çerçeve pencereleri (perp çerçevesi `PERP_FRAME_LIMITS` − oluşan bar: 1d 399, 4h 699,
  5m 499; günlükte `indicators.ema`/`atr`; Formasyon 4h 240; testte canlı koda eşit); kopyalar `k_trend` (T2/M2:
  canlı `StrategyBook.step`'in çağırdığı `paper_rules.decide_with_structures`; mühürdeki yapı kipi ENFORCE, kullanılmış
  yapılar canlı `used_patterns_of` ile kopyanın kendi girişlerinden), `k_box` (`decide_from_rows`; mühürdeki SHADOW yapı
  kipi kararı değiştirmediği için yapı analizi çağrılmaz), `k_d4` (`decide_from_rows`), `k_c4` (bağdaştırıcı, aşağıda),
  `k_fm` (`strategy_v3.build_plans_v3` + `structures.analyze`; `PatternBook._try_open`'ın saf geometri kapıları —
  kovalama, risk aralığı, `RR_BELOW_MIN_AT_ENTRY` (canlı `rr_after_cost`, planın `min_rr_after_cost`'u, defterin maliyet
  oranı `cost_fraction(LIVE_FEES)`) — niyette, zaman stopu 24 × 4h).
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
- **Kimlik.** `module_src_sha` (`strategy_lib.module_src_sha.v2`) = BİLDİRİLMİŞ dosya kümesinin **git HEAD blob'larının**
  sha'larının özeti: varyant modülü + `strategy_lib` içindeki bağımlılıkları (`contract`, `livebars`; Formasyonda
  `k_trend`) + modülün `LIVE_MODULES` listesi (kararı belirleyen canlı modüller, her biri gerekçesiyle; T2/M2 18, Box 9,
  D4 9, C4 10, FM 18 dosya). Liste, altı kopyanın `signals`/`manage` çalışırken yürüyen bütün `tradingbot` dosyalarının
  (profil izi, test) üst kümesidir; yürümeyen ama kararı belirleyen tanım/sabit modülleri (`candle_variations`,
  `learn.weekly_structure`, `pattern_trader.strategy`) ayrıca bildirilir. Bildirilmiş dosyalardan biri HEAD'den farklı,
  izlenmeyen ya da HEAD'de yoksa `DirtyTreeError` (işlenmemiş düzenleme kimlik üretmez); ilgisiz bir modülün düzenlenmesi
  kimliği değiştirmez, bildirilmiş birininki değiştirir (geçici git deposunda test). **`spec_sha` paket içinde
  hesaplanmaz:** kanonik fonksiyon mühürlü kayıttadır ve paket motoru import edemez; yeniden yazmak yerine motor tarafında `kernel.spec_sha_for` = `registry.spec_sha(registry.lib_spec(v,
  module_src_sha))` kullanılır; deneme defterinin `seal_entries` satırına eşitliği (alias'lar `DON_20_10_4h_LO ≡ K_D4`,
  `BOX_n10_mid_ms0.5 ≡ K_BOX` dahil) testlidir.
- **`research_engine/kernel.py`** (`kernel_v1`; çıkış yolu 2026-10-12'de `kernel_v2` oldu — aşağıdaki değişiklik notu): karar bar kapanışında (yalnız kapanmış barlar), bar başına tek eylem;
  dolum sonraki açılış ± kayma, o satır karar anında açılmıyorsa (veri boşluğu) `ENTRY_DATA_GAP` reddi; giriş kapıları
  canlı sırayla (kovalama, `STRATEGY_BAD_STOP`, giriş kayması, risk aralığı, Formasyonda `RR_BELOW_MIN_AT_ENTRY`); bar içi
  yol `learning_cf` kuralı (açılış → ters → lehte → kapanış; giriş barı ters uçtan); stop seviyede, yalnız açılış
  boşluğunda açılıştan; hedef seviyede kaymasız (taker ücret); birden çok hedefte TP1 yarım + ücret/kayma dahil başa-baş
  (`break_even_price` formülü) yalnız SONRAKİ noktalarda; kural CLOSE'u ve `max_hold_bars` sonraki açılışta (önce açılış
  gözlemi); `MOVE_STOP` yalnız sıkılaştırır ve karar fiyatını geçemez (aksi `MOVE_STOP_IGNORED` + neden); fonlama deponun
  gerçek uzlaşma satırlarında, damga nominal dakikaya yuvarlanmış; s'deki açık miktar s'den önce KESİN kapanmamış
  bacaklardan; bar içi bir çıkışın barındaki uzlaşma için ihtiyatlı kural (barın açılışı: yazılır — çıkış ondan önce
  olamaz; barın içi: yazılmaz, `FUNDING_EXIT_BAR` + tutarı ayrıca; barın kapanışı: yazılmaz — çıkış kesin önce);
  `FUNDING_GAP` = seri pencereyi örtmüyor, okunamaz/çelişen satır ya da YEREL aralığın (komşu aralıkların büyüğü; iki
  yandaki üçer aralığın medyanlarının büyüğü) 1,5 katından uzun boşluk; büyüklük canlı `learning_mode.fit_size` (slot 1,
  rezerv 0, 200 × %0,5 = 1 USDT), kaldıraç gerektiği kadar, likidasyon mesafesi ≥ 2 × stop gerçek bracket formülüyle
  denetlenir; likidasyon açılış boşluğunda ya da kayma dolumu ötesine iterse, `liquidation_outcome` aritmetiği (bu
  büyüklükte bracket hep birincidir: mmr %0,4 < likidasyon ücreti %0,5 → kayıp hep izole marja kırpılır; likidasyon
  fiyatı yalnız tetiklenmeyi belirler); üç senaryo aynı yolda aritmetik yeniden maliyetlendirme (spread tik'i hedef
  dolumuna da uygulanır — muhafazakâr okunuş). `signals_at` (tek karar anı) ve `replay_intent` (tek niyetin zorlanmış
  yeniden oynatımı) parite (b) içindir. Belirlenimci (bayt testi; A→B / B→A, yapı önbelleği temizlenerek ve ayrı
  süreçte). Fiyat adımına kuantize etmez (defter eder; ΔR'nin ana kaynağı).
- **`research_engine/parity.py`** — (a) başvuru: canlı kural DOĞRUDAN (C4'te kapı dahil; Formasyonda `_try_open`'ın
  kovalama ve R/R kapıları, maliyet oranı çağıranın verdiği canlı ücretlerden — ölçü betiği `FeesSection`'ı kendisi
  okur, `reference_run(fees=…)`; 2026-10-12'den beri modül canlı config'i import ETMEZ, verilmezse mühürlü kopyanın
  `k_fm.LIVE_FEES`'i, eşitliği testli) → `strategy_paper.apply_action` →
  `FuturesLedgerV2.tick` (base maliyet, serinin fiyat adımı, §2.1 büyüklüğü: kaldıraç çekirdeğinkine eşitlenir, risk × 50;
  R ölçekten bağımsız; veri boşluğundan sonra giriş yok); iki kip: `clipped` (KAPILI; çekirdekle aynı `learning_cf` yolu,
  stopun ötesindeki nokta seviyeye kırpılır — çekirdeğin aritmetiğini sınar, bu yüzden kısmen döngüseldir) ve `ohlc`
  (RAPOR; canlı gibi bar başına tek kırpılmamış OHLC tiki → defterin `exit_decision`'ı `STOP_CLOSE_BEYOND_LEVEL_PRUDENT`
  ve `PRUDENT_LIQUIDATION` dallarıyla çalışır); bar içi tikler açılıştan 1 ms sonra (çekirdeğin fonlama kuralı); sinyal
  kimliği (sembol, sinyal barı, yön) iki yönde; işaretli ortalama ΔR (çekirdek − defter) raporlanır.
  (b) ana belge §6.2 "o dönemin parametreleriyle": POLICY kohortu (`learning_mode.classify_unlock_codes`; etiketsiz eski
  kayıt `UNTAGGED`, POLICY sayılmaz); C4 kayıtları varyasyona göre süzülür (`features.candle_variation.id` ya da
  `setup_type`; her `K_C4_CVxxx` yalnız kendi varyasyonuyla; varyasyonu okunamayan POLICY kaydı EŞLEŞMEDİ); dönem
  `rehydrate.config_epoch` + mühürlü `config_epochs.json` (TRANSITION ve kapsam dışı ATANMAZ); dönem başına kopya o
  dönemin config GÖRÜNÜMÜNDEN kurulur (`view_sha` → görünüm `views_from_configs`'la sürüm config blob'larından;
  `params_from_view` kapalı hatalı eşleme: defter bölümünde bilinmeyen kural anahtarı, eksik karar yolu, belirsiz yapı
  kipi/farklı yapı politika sürümü, Formasyonda farklı ücret, kopyanın kuramadığı değer → `NOT_COMPARABLE`, raporlanır,
  kapıya girmez); her POLICY girişi kopyanın `signals`'ıyla girişin KARAR BARINDA, pozisyonsuz, `ctx.history` = canlı
  defterin kendi geçmişiyle eşlenir; ΔR o tek niyetin çekirdekte zorlanmış yeniden oynatımından (CAPACITY/SELECTIVITY
  pozisyonlarının yol bağımlılığı yok); `data_source.bars` anahtarı olmayan POLICY kaydı EŞLEŞMEDİ (atlanmaz); ≥ 20
  girişli karşılaştırılabilir dönem kapılı: ≥ %95 aynı bar/yön ve medyan |ΔR| ≤ 0,05; hüküm: kapılı dönemlerden biri
  tutmazsa `PARITY_FAIL`, ≥ 1 kapılı dönem varsa ve hepsi tutarsa `PASS`, yoksa `PARITY_UNPROVEN` (koşul gösterilemedi →
  uygun değil); `mark_parity` kopya satırını lider tablosu/kapı için uygunluk dışı yapar. Mühürlü dosyanın bugünkü
  dönemleri (git'teki sürüm config'lerinden): T2/M2/D4/FM'nin üç CONFIG dönemi, Box'ın dördü, C4'ün CV'li ikisi
  karşılaştırılabilir ve son dönem mühürlü parametrelere eşlenir (test); E0'lar (eksik anahtar / protokol) ve C4'ün boş
  liste dönemi `NOT_COMPARABLE`. **T2/M2 öğrenme kipi okunuşu (düzeltme):** 2026-09-28'den beri `structures_entry_shadow`
  ile yapı denetiminin engelleyeceği yeni giriş `STRUCTURE_*` koduyla açılır ve SELECTIVITY'dir (`strategy_paper`
  1095–1116, 1265–1271): POLICY kohortu tam olarak ENFORCE'un geçirdiği kümedir → bu dönemler mühürlü ENFORCE kopyasıyla
  karşılaştırılabilir; mühür değişmez.
  `research_engine/synthetic.py` (belirlenimci sentetik dünyalar; gece grafiğinde değil), `scripts/engine_p3b_measure.py`
  (parite (a) ≥ 200 işlem/aile + hız; yalnız sentetik).
- **Parite (a) sonucu** (`scripts/engine_p3b_measure.py`, düzeltme turu 1'den sonra yeniden koşuldu, 2026-10-11,
  sentetik; eşikler %100 / 0,02 / 0,05 yalnız `clipped` kipe uygulanır; `ohlc` kipi rapordur; `kernel_v1` — 2026-10-12'den
  beri kapı `live_1h`'dir ve `clipped` kip yoktur, aşağıdaki değişiklik notu):

| Aile | Dünya | Eşleşen işlem | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| | OHLC medyan \|ΔR\| | OHLC p95 \|ΔR\| | OHLC işaretli ort. (çekirdek − defter) | OHLC ihtiyatlı dolum |
|---|---|---|---|---|---|---|---|---|---|---|
| T2 (`K_T2`) | 31 | 207 | %100 | 3,3e-6 | 2,1e-5 | 7,3e-5 | 3,3e-6 | 2,2e-5 | +0,0014 R | 2 / 207 |
| M2 (`K_M2`) | 9 | 200 | %100 | 6,2e-6 | 1,9e-5 | 4,0e-5 | 6,2e-6 | 1,9e-5 | +0,0000 R | 0 / 200 |
| Box (`K_BOX`) | 1 (60 gün 5m) | 331 | %100 | 1,3e-4 | 3,2e-3 | 1,4e-2 | 2,6e-4 | 2,6e-1 | +0,042 R | 90 / 331 |
| D4 (`K_D4`) | 7 | 200 | %100 | 9,4e-6 | 2,6e-4 | 1,0e-3 | 1,8e-5 | 1,8e-1 | +0,021 R | 25 / 200 |
| C4 (`K_C4_CV001…008` birlikte) | 7 | 222 | %100 | 2,2e-6 | 2,2e-4 | 6,9e-4 | 1,1e-5 | 2,4e-1 | +0,035 R | 36 / 222 |
| FM (`K_FM`) | 3 | 265 | %100 | 7,5e-8 | 5,9e-5 | 3,3e-4 | 5,7e-7 | 1,7e-1 | +0,025 R | 45 / 265 |

  `clipped`: hepsi geçti; sayılar düzeltmeden öncekiyle aynı ya da çok yakın (C4 en büyük 7,0e-4 → 6,9e-4) — fonlama
  kuralı değişikliği başvuruya da aynen uygulandığı için. ΔR'nin kaynağı yalnız defterin fiyat adımına kuantize etmesi
  ve miktar adımıdır (betikli yollarda — TP1 + başa-baş, boşluk, likidasyon sınırının iki yanı, kayma ötesi likidasyon,
  fonlama, short — çekirdek R'si defterin `r_multiple`'ına 1e-9 içinde eşit). `ohlc` (kırpılmamış, canlı muhasebesi):
  sinyal kimliği her ailede %100 (bu dünyalarda çıkış farkı sonraki girişleri kaydırmadı); **çekirdek stop çıkışlarında
  canlı muhasebeye göre İYİMSERDİR**: defterin `STOP_CLOSE_BEYOND_LEVEL_PRUDENT` dalı (ters uç ve kapanış stopun
  ötesinde → dolum kapanıştan) işlemlerin %1 (T2) – %27 (Box) kadarında çalışır, çekirdek seviyeden doldurur; işlem
  başına ortalama fark +0,02…+0,04 R (Box, D4, C4, FM), T2 +0,001 R, M2 0 (çıkışları kural kapanışı); |ΔR| p95
  0,17–0,26 R, en büyüğü 0,22 (T2) – 0,89 (C4) R. Ölçü sinyal diliminin barıyla yapıldı; canlı defter 1h OHLC barı
  işler — 4h/1d ailelerinde bu ölçü canlıdan kaba (üst sınır), Box'ta (5m) canlıdan incedir (canlıda fark daha büyük
  olabilir). WF/Kapı A
  sonuçları bu iyimserlik kadar (aile başına ≈ +0,02–0,04 R/işlem) yukarı yanlıdır; ADVERSE/STRESS maliyet
  senaryoları bunu örtmez (kayma/ücret aritmetiğidir). Karar (P3b-2'ye açık kalan): çekirdeğe ihtiyatlı stop dolumu
  (kapanış da ötedeyse kapanıştan) eklenip eklenmeyeceği — eklenirse (a)'nın kapılı ölçüsü `ohlc` kipe geçer. Çekirdek
  fonlamasının ihtiyatlı kuralı: T2 10 / 207, M2 1 / 200 işlem `FUNDING_EXIT_BAR` (1d barın içindeki 08:00/16:00
  uzlaşmaları; yazılmayan tutar ortalama 1,5e-3 / 7,3e-4 R); 4h/5m ailelerinde 0 (uzlaşmalar bar sınırında). Sentetik
  Formasyon dünyalarında R/R kapısı hiçbir girişi reddetmedi (stoplar maliyet tabanından geniş; kapı birim testlidir).
  Bu ölçü çekirdeğin aritmetiğini sınar; canlı defterle farkı (tur mark'ı, 60 dk giriş penceresi, 60 sn izleyici,
  öğrenme kipi, slot/teminat) parite (b)'nin işidir.
- **Testler:** `tests/test_strategy_lib_p3b.py` (saflık AST'i, motoru yüklememe, bildirilmiş kimlik dosyaları elle
  sabitlenmiş ve özet git'ten bağımsız yeniden hesaplanır (döngüsel değil), geçici git deposunda ilgisiz düzenleme =
  aynı sha / ilgili düzenleme = farklı sha / kirli ağaç ve HEAD'de olmayan dosya = ret, `spec_sha` = kayıt = deneme
  defteri (ağaç kirliyse atlanır), Formasyon ücretleri = config.yaml = `FeesSection`, parametre kümesi, canlı pencere
  sabitleri, C4 kapısı mühürde açık, kullanılmış yapı okuyucusu), `tests/test_research_engine_kernel_p3b.py` (dolum/yol,
  boşluk, giriş boşluğu reddi, veri boşluğu reddi, bar içi sıra, TP1/başa-baş, R'den hedef, kural/zaman çıkışı, MOVE_STOP
  sıkılaştırma/gevşetme/fiyatı geçme (iki yön), giriş kapıları + R/R tabanı (canlı formül), tek giriş, zorlanmış tek niyet
  = sürekli koşu, 1 USDT risk + likidasyon ≥ 2 × stop, likidasyon sonucu, fonlama: nominal dakika, çelişen satır, çıkış
  barındaki uzlaşmanın üç durumu (1d bar içi, 4h kapanış), kısmi çıkıştan sonra miktar, 8h → 4h seri (boşluk yok; iki
  dönemde ve geçişten sonra eksik uzlaşma yakalanır), üç senaryonun elle hesabı, short, belirlenimcilik ve pencere),
  `tests/test_research_engine_parity_p3b.py` (altı K ailesinin altın sentetik yolu, bayt-aynı tekrar, A→B / B→A yapı
  önbelleği temizlenerek ve ayrı süreçte, aile başına parite (a) iki kipte, betikli defter çapraz denetimi — likidasyon
  fiyatı defterinkine eşit, açılış likidasyonun hemen altında / kayma ötesine / hemen üstünde — kırpılmamış likidasyon
  sonucu (bracket 2) defterle eşit, OHLC kipinde `STOP_CLOSE_BEYOND_LEVEL_PRUDENT` ve `PRUDENT_LIQUIDATION` dalları,
  altı kopyanın `signals`/`manage`'inde çalışma anı G/Ç-saat-rastgelelik yoklaması (denetim + profil kancası; yoklamanın
  kendisi de sınanır) ve çalışan dosyaların bildirilmiş kimlik kümesinde olması, parite (b): dönem görünümünden kurulan
  kopya + zorlanmış yeniden oynatımla PASS, anahtarsız kayıt EŞLEŞMEDİ → FAIL, medyan ΔR → FAIL, eşlenemeyen görünüm (beş
  neden + görünüm yok + sha tutmuyor) NOT_COMPARABLE → UNPROVEN, değişen-eşlenebilir parametreli dönem, kohort ve dönem
  atama, C4 çok varyasyonlu kayıtlar, `params_from_view` birim durumları, mühürlü dönem görünümlerinin git'teki sürüm
  config'lerinden çözülmesi, `PARITY_FAIL` işareti; `TB_P3B_FULL_PARITY=1` ile ≥ 200 işlem/aile). P1b yalıtım testinin
  paket dışı import listesine `kernel.py` ve `parity.py` (gerekçesiyle) eklendi.
- **Düzeltme turu 1 (bağımsız inceleme, diff `64f9cc8..25d551a`).** M1 `FUNDING_GAP` serinin TÜM medyan aralığıyla
  ölçülüyordu (8h → 4h'e geçen seride 8h dönemi boşluk sayılırdı) → yerel aralık (yukarıda), 300 g 8h + 500 g 4h testi.
  M2 (b) ana belge §6.2 ile çelişiyordu (mühürlü kopyanın tek sürekli koşusu, dönem parametresi yok, anahtarsız kayıt
  sessizce atlanıyor, C4 varyasyonları karışık) → yukarıdaki kural; T2/M2 öğrenme kipi dönemleri karşılaştırılabilir
  (önceki açık kalan (2)'deki "mühür sorusu" yanlıştı: düzeltildi, mühür değişmedi). m1 (a) kısmen döngüseldi (başvuru
  da seviyeye kırpıyordu) → kırpılmamış `ohlc` rapor kipi; ölçülen iyimserlik yukarıdaki tabloda. m2 fonlama
  damgaları: bar içi çıkış bar kapanışıyla damgalanıp çıkıştan SONRAKİ uzlaşmayı ödüyordu, ms kaymalı uzlaşma açılışta dolan girişe
  yazılıyordu → nominal dakika + ihtiyatlı kural (yukarıda). m3 `module_src_sha` 37 dosyalık çalışma ağacı kapanışıydı
  (ilgisiz düzenleme ve işlenmemiş değişiklik yeni deneme sayılıyordu; `live_modules` kullanılmıyordu) → bildirilmiş
  küme + HEAD blob'ları + kirli ağaç reddi. m4 veri boşluğundan sonra giriş saatler sonra doluyordu → `ENTRY_DATA_GAP`.
  m5 `MOVE_STOP` doğrulanmıyordu → yalnız sıkılaştırır, fiyatı geçemez. m6 Formasyonun saf geometri kapısı
  `RR_BELOW_MIN_AT_ENTRY` (`book.py` 1056–1060) eksikti → kopyada ve başvuruda. Altın yollarda yalnız fonlama değişti (C4
  iki, FM yedi işlem: 4h kapanışındaki uzlaşma artık çıkan miktara yazılmıyor); sentetik dünyalarda R/R kapısı bir giriş
  reddetmedi.
- **Açık kalanlar (P3b-2 ve sonrası).** (1) B…O aileleri (93 varyant) ve `trials.seal_entries`'in 106 tabanın
  `module_src_sha`'sıyla gerçek `SPEC` satırları (şimdi 13 kopya; bildirilmiş modül listeleri P3b-2 ailelerine de
  yazılacak). **2026-10-12 P3b-2a: B, C, D, E, F1, F2, H yapıldı** (45 + 2 alias; bildirilmiş modül listeleriyle; aşağıdaki
  P3b-2a notu); O (35) sonraki adım; I, J, G (13) mühürlü boşluk yüzünden `BLOCKED`. (2) C4 kapısının (dosya okuyan) motor tarafında koşu öncesi denetimi. (3) Formasyonun canlı-yalnız kapıları
  (likidite/spread/derinlik, kayıptan sonra bekleme `cooldown_bars_after_loss`, evren, fiyat adımına yuvarlama sonrası
  R/R) kopyada yok; parite (b) canlı girişten yola çıktığı için bunlar eşleşmeyi bozmaz, yalnız kopya fazlası
  (`copy_extra`) olarak görünür; "açıklanan fark" girdisi (slot/teminat retleri) şimdilik yalnız sayılır. Canlı
  Formasyon kayıtlarının `features.data_source.bars["4h"]` taşıyıp taşımadığı P3g'de gerçek defterde doğrulanmalı
  (taşımıyorsa her kayıt EŞLEŞMEDİ → `PARITY_FAIL`; kapalı hata bilinçli). (4) Config dönemleri yalnız config'i izler;
  dönem içinde canlı KOD değişmişse (sürüm) kopya bugünkü kodu çalıştırır — E0 dönemleri zaten `NOT_COMPARABLE`; sonraki
  dönemlerdeki kod sürümlerinin etkisi (b)'nin ΔR'sinde görünür, ayrı bir "kod dönemi" yoktur. (5) Fonlamanın ihtiyatlı
  kuralı 1d barlarda (T2/M2) bar içi her çıkışta 08:00/16:00 uzlaşmalarını belirsiz bırakır (`FUNDING_EXIT_BAR`, tutar
  ayrıca; kapıdan çıkarmaz); daha ince veri (1h) varsa bacak zamanı daraltılabilir (P3b-2). **2026-10-12: yapıldı**
  (`kernel_v2`: bacağın en erken anı 1h alt barın açılışı; 1h verisi eksikse eski kural). (6) Çekirdeğin stop
  dolumundaki iyimserliği (yukarıdaki `ohlc` sütunları, aile başına ≈ +0,02–0,04 R/işlem): ihtiyatlı dolumun çekirdeğe
  alınıp alınmayacağı sahip/tasarım kararıdır (mühürlü `WF_PROTOCOL_V1.kernel` metni değişeceği için P3b-1'de
  yapılmadı); o zamana dek Kapı A sonuçlarının bu kadar yukarı yanlı olduğu raporda yazılır. **2026-10-12: sahip kararı,
  yapıldı** (aşağıdaki değişiklik notu; S1 düzeltme mührü 3). (7) Hız: §5.3 ölçüm notu
  (bu tur hız yeniden ölçülmedi; bar başına iş değişmedi) — taban sinyal önbelleği, DataFrame'siz bayt-aynı satır yolu,
  Box 5m. (8) Parite (b)'nin gece bağlantısı ve canlı defter okuyucusu (P3g); `funding` anahtarı (H ailesi) P3b-2'de
  doldurulur. **2026-10-12 P3b-2a: `funding` yapıldı** (`needs_funding` + `funding_lookback_ms`).

**Değişiklik 2026-10-12 — çekirdeğin stop dolumu canlı defterle aynı (sahip kararı; `kernel_v2`; S1 düzeltme mührü 3).**
P3b-1 inceleme turunun ölçüsü (yukarıdaki `ohlc` sütunları; `parity.reference_run(mode="ohlc")`): `kernel_v1` stopu
seviyeden dolduruyordu ("seviyede; boşlukta açılışta"), canlı `FuturesLedgerV2` ise her 1h OHLC barında (`strategy_paper`
`BAR_TIMEFRAME` = 1h, `apply_closed_bars`) `exit_decision`'ı `STOP_CLOSE_BEYOND_LEVEL_PRUDENT` ve `PRUDENT_LIQUIDATION`
dallarıyla çalıştırır → çekirdek Box, D4, C4, FM'de işlem başına ≈ +0,02–0,04 R iyimserdi ve Kapı A sonuçları bu kadar
yukarı yanlıydı. **Karar** (proje kuralı: asla gevşetme; muhafazakâr; canlıyla aynı): çekirdek canlı ihtiyatlı stop
dolumunu üretir.

- **Çekirdek** (`kernel.py`; `KERNEL_VERSION` `kernel_v1` → `kernel_v2`): stop/likidasyon hükmü canlı defterin saf
  fonksiyonu `accounting.futures_ledger.exit_decision`'ın KENDİSİdir (çağrılır, yeniden yazılmaz; `exit_fill_v1`). Çekirdek
  onu float bir tik görünümüyle (`Obs`) çağırır; hükmün Decimal `TickData` ile aynı olduğu 4.000 örnekli özellik testiyle
  (eşitlik sınırları ve fonksiyonun bütün dalları) sınanır. Tikler canlı defterin gördükleridir: pozisyon açıkken her sinyal
  barının açılışında fiyat-yalnız tik (tur mark'ı; açılış stopun/likidasyonun ötesindeyse dolum açılıştan), sonra sinyal
  barının **1h OHLC alt barları** (`PATH_TF` = canlı `BAR_TIMEFRAME`, eşitliği testli; alt bar başına bir tik: ilk gözlem →
  en kötü uç → kapanış). Stop seviyeden, **kapanış da stopun ötesindeyse kapanıştan**; en kötü uç stopu ve likidasyonu
  birlikte geçerse likidasyon; kayma dolumu likidasyonun ötesine iterse likidasyon; çıkış yoksa hedefler aynı tikin lehte
  ucuyla (aynı tikte stop hedeften önce); TP1'in başa-baş stopu yalnız sonraki tiklerde (defterle aynı). Bacak, hükmün
  dayanağını taşır (`basis` = defterin `exit_fill.basis`). Sinyal dilimi ≤ 1h ise tik barın kendisidir.
- **Box (5m).** Canlı Box defteri 5m'lik değerlendirmede ve her 60 sn'de mark fiyatıyla tiklenir, kapanmış 1h barları
  `apply_closed_bars` ile uygulanır (`box_timer`: `_evaluate` → `step` + `apply_closed_bars` + `tick`, `_protect` her 60 sn);
  5m OHLC barı canlıda tik değildir. 60 sn gözlemi 1m verisi olmadan kurulamaz; çekirdek eldeki en ince barı (5m) OHLC tiki
  olarak işler: canlının 1h barından ince, 60 sn izleyicisinden kaba; ihtiyatlı dallar 5m kapanışıyla çalışır.
- **Hız kısaltması.** Stop/likidasyon/sıradaki hedef aralığına değmeyen sinyal barında alt bar açılmaz (alt barlar barın
  aralığındadır → hiçbir tik bir şey yapamaz). Kesinliği testlidir: 1h verisi TAMKEN her barı açan koşuyla bayt-aynı
  (1d × 24, 4h × 4, Box); değmeyen bir barın 1h verisi eksikse işlem, bacaklar ve R yine aynıdır, yalnız her barı açan
  koşu `PATH_1H_GAP` işareti koyardı (P3b-2a düzeltmesi m-B: önceki "bayt-aynı" ifadesi bu durumda tutmuyordu).
  Değen barın 1h alt barları barı tam ve bitişik örtmüyor, barın aralığını aşıyor ya da kendi içinde tutarsızsa bar tek OHLC
  tiki olarak işlenir ve işlem `PATH_1H_GAP` işaretlenir (mühürlü: `FUNDING_GAP` gibi kapı istatistiğinden çıkar, sayısı
  gösterilir); yalnız değen barın alt barları gerekir.
- **Fonlama** kuralı değişmedi; bacağın en erken anı artık gözlem barının (1h alt barın) açılışıdır → 1d ailelerinde bar
  içi çıkışın 08:00/16:00 belirsizliği (`FUNDING_EXIT_BAR`, P3b-1 açık kalan 5) 1h verisi olduğunda kalkar (sentetik
  ölçüde 0 işlem; önce T2 10, M2 1).
- **Sürüm.** Kalıcı çekirdek çıktısı henüz yok (önbellek, deneme satırı yazılmadı); `kernel_v2` anlamı değişen sonuçların
  ileride karışmaması içindir (kayıt biçimi de değişti: bacakta `basis`).
- **Mühür (düzeltme mührü 3).** Henüz hiçbir gerçek veri koşusu olmadığı için `_v2` değil, P3a düzeltmeleriyle aynı yol:
  `WF_PROTOCOL_V1.kernel.stop_fill` artık `{policy: LIVE_EXIT_DECISION_ON_1H_BARS, function, rule_tr, path_gap_tr,
  decision, reading_tr}` ("sahip kararı 2026-10-12 …: canlı defterle aynı ihtiyatlı stop dolumu; iyimserlik ölçümü
  +0,02–0,04 R …"); `intrabar_order` canlı tik sırasını yazar; `THRESHOLD_DIRECTIONS_V1`'e `kernel.stop_fill.policy` =
  `fixed` eklendi (sonraki bir sürüm kuralı değiştiremez). Eşiklerin hiçbiri değişmedi; asla-gevşetme testleri geçer
  (değişiklik sıkılaştırmadır). Bütün sha'lar, `REGISTRY_SHA`, `T_SEAL_MS` ve `config_epochs.json`'un `covered_until`'ı
  yeniden sabitlendi (§8.2; `T_SEAL_MS` mühür commit'inden birkaç dakika sonraki tam dakika).
- **Parite (a)** (`parity.py`): kapılı ölçü artık `live_1h` — kırpılmamış, canlı gibi: açılış fiyat tiki + sinyal barının
  BÜTÜN 1h OHLC barları (başvuru 1h barlarını çekirdekten bağımsız okur ve her birini tikler; çekirdeğin kısaltması böylece
  de sınanır); `clipped` kip kaldırıldı (çekirdek o yolu artık kullanmıyor); `ohlc` (sinyal barı başına tek tik) rapor
  olarak kaldı. `synthetic.py`: 4h/1d dünyalarına tutarlı 1h alt barlar (`subdivide`: açılıştan kapanışa Brown köprüsü,
  barın aralığına kırpılmış; ilk açılış/son kapanış/uçlar barınkiyle aynı; ayrı tohum → sinyal serisi bayt-aynı). Eşikler
  değişmedi (%100 / 0,02 / 0,05).
- **Parite (a) sonucu** (`scripts/engine_p3b_measure.py`, 2026-10-12, sentetik; kapı `live_1h`):

| Aile | Dünya | Eşleşen işlem | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| | İşaretli ort. (çekirdek − defter) | Defter: kapanıştan / ihtiyatlı likidasyon | Tek-tik (`ohlc`) p95 \|ΔR\| | Tek-tik işaretli ort. |
|---|---|---|---|---|---|---|---|---|---|---|
| T2 (`K_T2`) | 31 | 207 | %100 | 3,3e-6 | 2,1e-5 | 7,3e-5 | +6,3e-6 R | 4 / 0 | 5,9e-5 | +0,0007 R |
| M2 (`K_M2`) | 9 | 200 | %100 | 6,2e-6 | 1,9e-5 | 4,0e-5 | +7,3e-6 R | 1 / 0 | 2,0e-5 | −3,3e-5 R |
| Box (`K_BOX`) | 1 (60 gün 5m) | 331 | %100 | 1,6e-4 | 3,2e-3 | 1,4e-2 | +0,0003 R | 90 / 0 | 3,2e-3 | +0,0003 R |
| D4 (`K_D4`) | 7 | 200 | %100 | 9,4e-6 | 2,6e-4 | 1,0e-3 | +4,9e-5 R | 20 / 0 | 1,1e-1 | +0,0101 R |
| C4 (`K_C4_CV001…008` birlikte) | 7 | 222 | %100 | 4,5e-6 | 2,2e-4 | 6,9e-4 | +3,4e-5 R | 40 / 0 | 1,3e-1 | +0,0084 R |
| FM (`K_FM`) | 3 | 265 | %100 | 9,7e-8 | 5,9e-5 | 3,3e-4 | +1,0e-5 R | 39 / 0 | 1,2e-1 | +0,0164 R |

  Kapı (`live_1h`): **hepsi geçti** — sinyal kimliği her ailede %100, medyan |ΔR| ≤ 1,6e-4, p95 ≤ 3,2e-3, en büyük
  1,4e-2 R (Box; defterin hedef dolumunu fiyat adımına yuvarlaması); işaretli ortalama ≈ 0 (≤ +3e-4 R): **çekirdek artık
  canlı muhasebeye göre iyimser değil**. Çekirdeğin ve defterin çıkış dayanakları her ailede birebir aynı sayıdadır (ör.
  seviyeden / kapanıştan: Box 176 / 90, C4 101 / 40, FM 13 / 39, D4 40 / 20 + 1 açılış boşluğu, T2 6 / 4, M2 0 / 1);
  ihtiyatlı likidasyon hiç çalışmadı (likidasyon mesafesi ≥ 2 × stop). ΔR'nin kaynağı yalnız defterin fiyat/miktar
  adımına yuvarlamasıdır. Tek-tik raporu (`ohlc`): 4h ailelerinde sinyal barını tek OHLC tiki saymak (P3b-1'in rapor
  ölçüsü) canlının 1h muhasebesinden işlem başına 0,008–0,016 R daha kötümserdir (C4, D4, FM; p95 0,11–0,13 R) — P3b-1'in
  +0,02–0,04 R'lik ölçüsünün bir kısmı bu kabalıktandı (orada "üst sınır" denmişti); 1d ve Box'ta fark ihmal edilir.
  `FUNDING_EXIT_BAR` 0 işlem (P3b-1'de T2 10, M2 1); `PATH_1H_GAP` 0 (sentetik dünyaların hepsinde 1h barı var).
- **`kernel_v1` → `kernel_v2`** (aynı dünyalar, eşleşen işlemler, base R; ADVERSE aynı yönde):

| Aile | Eşleşen | Ortalama ΔR (v2 − v1) | Daha düşük | Daha yüksek | En büyük düşüş |
|---|---|---|---|---|---|
| T2 | 207 | −0,0007 R | %3,9 | %0,0 | −0,084 R |
| M2 | 200 | −4,0e-5 R | %0,5 | %0,0 | −0,008 R |
| BOX | 331 | −0,0418 R | %27,2 | %0,0 | −0,777 R |
| D4 | 200 | −0,0112 R | %10,0 | %0,0 | −0,293 R |
| C4 | 222 | −0,0263 R | %18,0 | %0,0 | −0,542 R |
| FM | 265 | −0,0087 R | %14,7 | %0,0 | −0,446 R |

  `kernel_v2` hiçbir eşleşen işlemde `kernel_v1`'den yüksek değildir ("daha yüksek" her ailede %0): değişiklik yalnız
  sıkılaştırmadır. Ortalama düşüş Box −0,042, C4 −0,026, D4 −0,011, FM −0,009, T2 −0,0007 R/işlem, M2 ≈ 0 (ADVERSE aynı).
  Box'ta düşüş P3b-1'in ölçtüğü iyimserliğe eşittir (+0,042; 5m'de tek-tik ölçüsü çekirdeğin tikiyle aynıdır); 4h ailelerinde düşüş + tek-tik
  kötümserliği = P3b-1'in ölçüsü (D4 0,011 + 0,010 ≈ 0,021, C4 0,026 + 0,008 ≈ 0,035, FM 0,009 + 0,016 ≈ 0,025).
- **Hız** (§5.3 ölçüm notu 2): değişmedi — `kernel_v2` / `kernel_v1` süre oranı 0,98–1,01.
- **Testler.** `tests/test_research_engine_kernel_p3b.py`: canlı fonksiyonun kendisi çağrılır + mühür/sürüm/`BAR_TIMEFRAME`
  eşitliği; float görünüm = Decimal `TickData` (özellik testi); ihtiyatlı kapanış dolumu ve ihtiyatlı likidasyon; 1h alt
  barlar günün içindeki sırayı belirler (hedef önce → HEDEF, stop önce → STOP; alt barsız gün tek tik → STOP +
  `PATH_1H_GAP`); ihtiyatlı dolum 1h kapanışından (gün kapanışından değil — 1h ayrıntısı daha ihtiyatlı); TP1 erken saatte,
  başa-baş stopu aynı gün sonraki saatte; kısaltmanın kesinliği (1h verisi tamken her barı açan koşuyla bayt-aynı, dört
  aile); eksik /
  aralık dışı / hiç olmayan alt bar → tek tik + işaret, değmeyen barın eksik alt barı işaret koymaz; ≤ 1h dilimde tik barın
  kendisi; 1h alt barla fonlama saatine daralır. Eski testler yeni sözleşmeye göre güncellendi (TP1 sonrası başa-baş
  stopu sonraki açılış tikinde boşlukla; `PATH_1H_GAP` işareti). `tests/test_research_engine_parity_p3b.py`: altın yollar
  yeniden sabitlendi (değişen satırlar yalnız stop çıkışları ve hepsi aşağı: Box 5, Formasyon 2 işlem); (a) kapısı
  `live_1h`'ye karşı; betikli defter çapraz denetimi canlı tiklerle (ihtiyatlı iki dal + 1h barlı gün: çekirdek R'si ve
  dayanağı defterinkine eşit; `kernel_v1`'in seviyeden dolumu bu yollarda > 0,1 R iyimserdi); `LIVE_BAR_TF` =
  `BAR_TIMEFRAME`. `tests/test_research_engine_registry.py`: yeni sabitler, `kernel.stop_fill.policy` `fixed`. P1b yalıtım
  listesi: `kernel.py` → `accounting.futures_ledger` (canlı `exit_decision`), `parity.py`'den `learning_cf` çıktı.
- **Açık kalanlar.** (1) Canlının stop dolumlarının çoğu 60 sn izleyicisinin mark tikinden gelir (ilk gözlem stopun
  ötesinde → o mark'tan); çekirdek ve (a) başvurusu yalnız canlının 1h bar sözleşmesini (+ açılış tiki) kurar — 60 sn
  gözlemi 1m verisi olmadan kurulamaz. 1h tikindeki ihtiyatlı dal (kapanıştan dolum) çoğu durumda 60 sn izleyicisinden
  kötüdür, seviyeden dolum (kapanış geri dönmüşse) ise izleyicinin aşma payı kadar iyidir; net etki parite (b)'nin ΔR'sinde
  görünür (P3g). (2) Canlıda girişin 1h barı `apply_closed_bars`'a girmez (açılış anından önce açılmış; o saati izleyici
  örter); çekirdek onu OHLC tiki olarak işler (açılışı giriş fiyatı). **2026-10-12 P3b-2a (m-A): yapıldı** — girişi içeren
  saat diliminde yalnız stop/likidasyon, hedef kredisi yok (mühürlü okunuş `kernel.entry_hour`; aşağıdaki P3b-2a notu).
  (3) `PATH_1H_GAP` ve `FUNDING_GAP` işaretli işlemlerin kapı istatistiğinden çıkarılması kapı kodunda (P3c/P3e)
  uygulanmalı; gerçek veride 4h/1d ile 1h barların tutarlılığı ve `PATH_1H_GAP` sayısı P3g'de ölçülür.

**P3b-2a uygulama notu (2026-10-12; saf aileler B, C, D, E, F1, F2, H; çekirdek düzeltmeleri m-A…m-D; S1 düzeltme mührü 4).**
Canlı karar yolu, worker kodu ve config DEĞİŞMEDİ (canlı fonksiyonlar yalnız çağrılır); hiçbir şey gece birimine
bağlanmadı; gerçek veri yok, ölçümler sentetik.

- **Kapsam ve sayım (kayıtla uzlaşır, testli).** `strategy_lib` kataloğu her LIB varyantına bir durum verir:
  `IMPLEMENTED` **58** = K 13 + **B 10, C 8, D 7, E 4, F1 4, F2 7, H 5** (45 yeni); `ALIAS` 2 (`DON_20_10_4h_LO → K_D4`,
  `BOX_n10_mid_ms0.5 → K_BOX`; tabanın kopyasını çalıştırır); `BLOCKED` **13** (I 2, J 5, G 6 — §8.5 B9 ve B10 altın
  stopları; `registry.blocked_variants` ile aynı küme; ÇALIŞAN KOD YOK: `make` / `kernel.make_variant` /
  `module_src_sha` `BlockedVariant` fırlatır, paket kaynaklarında bu ailelerin kuralı yok — test); `PENDING` 35 (O
  örtüleri, sonraki adım). 58 + 35 = **93** = kaydın çalıştırılabilir varyantı (106 − 13). Uygulanan aile sayıları kaydın
  alias'sız `family_counts`'una eşittir.
- **Yeni modüller** (saf; AST ve çalışma anı yoklaması): `indicators.py` (tek tanım), `pure.py` (ortak taban),
  `b_tsmom.py`, `c_macross.py`, `d_donchian.py`, `e_volbreak.py` (KEL, BBSQ), `f1_range.py` (RSI2, BBRE), `f2_boxgrid.py`,
  `h_funding.py`. Varyant YALNIZ mühürlü satırdan kurulur: `params` (anahtar kümesi tam) + `spec` = `{family, timeframes,
  signal_tf, sides, exits}` (motor tarafı `kernel.variant_spec`; F2'de ayrıca `base_params` = K_BOX'un mühürlü
  parametreleri, "diğer bütün parametreler K_BOX'unki"); `exits.stop` = m × ATR14 sinyal kapanışından, `target_r` GERÇEK
  girişten R hedefi, `max_hold_bars` niyetin zaman stopu — hepsi kayıttan okunur (sabit yazılmaz); beklenmeyen yapı
  ValueError. `ctx["funding"]` (H): çekirdek `now − funding_lookback_ms < ms ≤ now` uzlaşma satırlarını verir
  (`funding_lookback_ms` = fut_v1 `live_requirements("4h")["funding_ms"]` = 20 gün + 9 saat).
- **Göstergeler** (`strategy_lib/indicators.py`, altın testli): EMA, Wilder ATR14 ve RSI = `tradingbot.indicators`
  (= `signal_lab` ATR'si) — pandas `ewm`'in kayan nokta sırası birebir → başvuruyla **bayt-aynı** (rastgele serilerde
  test); BB(20,2), BB genişliği ve yüzdelik sırası = `tradingbot.indicators.bollinger` / `indicators_ext.bb_width_pct_rank`
  tanımı (göreli ≤ 1e-9 / 1e-12); Keltner = `indicators_ext.keltner(…, 20, 2.0, atr_len=14)` ve Donchian =
  `tradingbot.indicators.donchian` = `signal_lab` `hi20/lo10` (bayt-aynı); ER20 (başvurusu yok) elle hesaplanmış altın
  değerlerle. Başvurular saftır ama pandas'lıdır; çağrı başına DataFrame kurmak ATR'de ≈ 1,15 ms ölçüldü — aynı sayıları
  veren saf Python eşdeğerleri kullanıldı (tanım yine başvurununkidir, eşitlik testli). Fonlama ölçüsü fut_v1'in KENDİ
  fonksiyonudur (`futures_features.align_funding`, yeniden yazılmadı; varyant penceresinde bütün seriyle aynı sonuç — test).
- **Okunuşlar** (mühürlü metnin söylemediği ayrıntılar; muhafazakâr ya da harfiyen; modül belgelerinde):
  (1) **Pencere** = canlı kural çerçevesinin bir kurala verdiği bar sayısı: gün içi 400 (`paper_rules.intraday_rows`
  varsayılanı; canlı D4'ün göstergelerini hesapladığı pencere), 1d 399 (`livebars.LIVE_CLOSED_BARS["1d"]`); EMA/ATR tohumu
  pencerenin başına bağlıdır, AUTO_v1 worker'ı aynı pencereyi verir. (2) **Durum / olay:** TS (getiri işareti), MAX (EMA
  ilişkisi), RSI2 (RSI < 10 / > 90) ve H (yüzdelik) DURUMDUR — pozisyonsuzken koşul sağlanıyorsa girilir; "LO = sinyal
  tersine dönünce pozisyon yok", LS ters yöne çekirdekte bir bar gecikmeyle geçer (MAX'ta salt olay okunuşunda çıkış
  barının kesişimi hiç kullanılamaz ve LS ≈ LO olurdu). DON, KEL, BBSQ **kırılımı** = kapanışın seviyeyi İLK kez geçmesi
  (canlı D4'ün `_fresh_breakout` tanımı; `DON_20_10_4h_LO ≡ K_D4` bunu bağlar); BBRE = önceki kapanış önceki barın bandının
  dışında, bu kapanış bandın içinde. DON LS'de long'un çıkış barındaki kırılım (artık taze değil) alınmaz. (3) **BBSQ**:
  "iken" = kırılım barında; yüzdelik = o barın genişliğinin kendisi DAHİL son 120 genişlik içindeki ortalama sırası / 120
  ≤ 0,20 (`bb_width_pct_rank` tanımı; pencere tam dolmadan giriş yok). *[2026-10-12 inceleme düzeltmesiyle değişti:
  sıkışma t − 1'de — aşağıdaki değişiklik notu, bulgu 2.]* (4) **Orta bant** (RSI2 B5, BBRE): kapanış ≥ SMA20
  (long) / ≤ (short) → sonraki açılış. (5) **FUND_FADE**: `f8_hi ≥ 0,95` → short, `f8_lo ≥ 0,95` → long (fut_v1 payları,
  eşitlik uç sayılmaz, ≥ 30 değer); çıkış yalnız en çok tutuş (6/18 × 4h; ekin saati 4h'a bölünerek doğrulanır) ya da stop.
  **FUND_CARRY**: |f8| üzerinde aynı fonksiyonla `f8_hi ≥ 0,90` → fonlamayı ALAN taraf (f8 > 0 short, < 0 long); çıkış
  `< 0,50`; işaret dönse de |f8| yüksekse kural çıkışı yok (yazılmadı; stop ve zaman stopu korur). *[2026-10-12 inceleme
  düzeltmesiyle değişti: işaret ters → çık (bulgu 3); f8 aralık kuralı (bulgu 1); RSI2 girişi orta bandın doğru yanında
  (bulgu 4) — aşağıdaki değişiklik notu.]* (6) **F2**: canlı Box
  kuralı (K_BOX yolu) + üç eksen; `2r` = canlı `r_multiple` (exit_r 2,0 = satırın `target_r`'ı; hedef canlı kural gibi
  sinyal kapanışından ölçülen mutlak fiyat). (7) Ölçülemeyen gösterge/fonlama → giriş yok; açık pozisyonda kural çıkışı
  ölçülemiyorsa tut (stop ve zaman stopu sürer).
- **Tekilleştirme (sahte değil, testli).** Alias kataloğun AYNI modülüne çözülür → bildirilmiş dosya kümesi ve
  `module_src_sha` tabanınkiyle aynı → `registry.lib_spec` tabanın spec'ini taşır → `kernel.spec_sha_for(alias)` =
  tabanınki = `trials.seal_entries` satırı (HEAD'deki gerçek sha'larla); deneme defteri alias'ın tabanla aynı kapsamdaki
  satırını `DUPLICATE` sayar (sayaç değişmez). Saf D modülü başka dosya kümesidir (farklı `module_src_sha`): tekilleşme
  kataloğun alias'ından gelir, tesadüften değil. Davranış eşitliği: D modülünün 20/10/4h/LO kurulumu K_D4 ile **bayt-aynı**
  işlemler verir (K_D4'ün 60 barlık ısınmasından sonra; pencere 400 = canlı D4'ünki); F2'nin alias eksenleri K_BOX'un kural
  parametrelerini birebir verir. 58 uygulanmış varyantın `spec_sha`'ları farklıdır.
- **Çekirdek düzeltmeleri (son doğrulama turunun küçük bulguları).**
  **m-A giriş saati:** mühürlü `intrabar_order` giriş saati için ayrı bir şey söylemiyordu (her 1h alt bar bir OHLC tiki).
  Canlıda girişi içeren 1h barı `apply_closed_bars`'a girmez (bar açılışı ≥ `opened_at` kuralı; o saati 60 sn izleyici
  örter) ve 1h barının lehte ucu canlının görmediği bir fiyattır → o saatte hedef kredisi iyimserdi. Okunuş: girişi içeren
  1h saat dilimindeki HER tikte yalnız stop/likidasyon, hedef kredisi yok (tek-tik yedeğinde bütün bar; ≤ 1h dilimde saatin
  bütün tikleri, açılış tikleri dahil); hedef sonraki tikte hâlâ oradaysa alınır — hiçbir zaman iyimser değil. Açıklayıcı
  tarihli okunuş olarak mühre yazıldı: `WF_PROTOCOL_V1.kernel.entry_hour` (`policy` `STOP_AND_LIQUIDATION_ONLY`, `rule_tr`,
  `decision`, `reading_tr`) ve `THRESHOLD_DIRECTIONS_V1`'e `kernel.entry_hour.policy` = `fixed`; parite (a) başvurusu aynı
  okunuşu defterin hedeflerini o saat maskeleyerek uygular. `KERNEL_VERSION` değişmedi (`kernel_v2`; kalıcı çıktı yok).
  Etkisi (sentetik; aynı dünyalarda eski/yeni çekirdek eşleştirmesi): K ailelerinin parite dünyalarında (Box, C4, FM)
  818 işlemin hiçbiri değişmedi (hedefe giriş saatinde ulaşılmıyor; T2, M2, D4 hedefsiz); E'de (KEL, BBSQ; 1h/4h) 389
  işlemde 0; F2'nin 2R ızgarasında (en az stop %0,5) işlemlerin ≈ %2'si — hepsi aşağı (bir HEDEF → STOP, ikisi hedefi
  sonraki tikte alır), ortalama ≈ −0,02 R/işlem, yol bağımlılığıyla birkaç sonraki giriş kayar; kutu ortası çıkışında 0.
  **m-B:** kısaltmanın "bayt-aynı"lığı yalnız 1h verisi tamken geçerlidir (değmeyen barın eksik 1h verisi yalnız her barı
  açan koşuda işaret koyardı; yol ve R aynı) — çekirdek belgesi, yukarıdaki not ve test düzeltildi. **m-C:** parite (a)
  başvurusu kısmen eksik 1h verisinde eldeki 1h barlarını tikliyordu; artık çekirdekle aynı tamlık kuralı (bitişik, tam
  sayıda, barın aralığında, tutarlı) → değilse tek OHLC tiki ve çıkış kararı gereken (defterin stopuna/likidasyonuna/sıradaki
  hedefine değen) barda `PATH_1H_GAP` (başvurunun kendi defter durumundan; `RefTrade.flags`) — kısmen delinmiş 1h
  dünyalarında işaretli işlem kümeleri çekirdekle aynı (test). **m-D (kör nokta, belgelendi ve testle sabitlendi):**
  `FUNDING_GAP`'in yerel aralık ölçüsü, uzlaşma aralığının DEĞİŞTİĞİ yerin hemen yanındaki TEK eksik uzlaşmayı yakalamaz
  (8h → 4h'te ilk 4h uzlaşmasının yokluğu 8h'lik bir aralık bırakır — değişimin bir uzlaşma sonra olmasından ayırt
  edilemez; 4h → 8h'te ilk 8h'nin yokluğu 12h = 1,5 × 8h, eşik kesin büyüktür); kararlı seride tek eksik yakalanır. Etki:
  o uzlaşmanın fonlaması işlemde yazılmaz (tek uzlaşma: ≈ oran / stop mesafesi R, ör. %0,01 / %2 = 0,005 R) ve işlem kapıda kalır.
  Kapatma: veri biriminin borsa aralık geçmişiyle (fundingInfo) karşılaştırma (P3g).
- **Parite (a) saf ailelerde.** Canlı karşılıkları olmadığından başvurunun kuralı varyantın kendi `signals`/`manage`'idir
  (`parity._PureRule` → `apply_action` → `FuturesLedgerV2.tick`, zaman stopu elle kapanış; F2'de bu zaten canlı Box
  kuralıdır): çekirdeğin R'den hedef, zaman stopu, kural çıkışı, fonlama, giriş saati ve 1h yedeği aritmetiğini canlı
  defterle sınar. Sonuç (`scripts/engine_p3b_measure.py --families B,C,D,E,F1,F2,H --box-days 20`, 2026-10-12, sentetik, `live_1h`):
  **hepsi geçti**.

| Aile | Dünya | Eşleşen işlem | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| | İşaretli ort. (çekirdek − defter) | Çıkışlar (çekirdek) | Tek-tik (`ohlc`) p95 \|ΔR\| | Tek-tik işaretli ort. |
|---|---|---|---|---|---|---|---|---|---|---|
| B (TS, 10) | 1 | 564 | %100 | 6,2e-6 | 1,9e-5 | 4,3e-5 | +7,4e-6 R | kural 548, stop 10, son 6 | 2,2e-5 | +0,0000 R |
| C (MAX, 8) | 2 | 315 | %100 | 4,1e-6 | 8,6e-5 | 5,1e-4 | +2,1e-5 R | kural 208, stop 91, zaman 6, son 10 | 8,6e-2 | +0,0096 R |
| D (DON, 7) | 2 | 335 | %100 | 5,8e-6 | 2,2e-4 | 6,3e-4 | +3,6e-5 R | kural 195, stop 131, son 9 | 1,5e-1 | +0,0153 R |
| E (KEL, BBSQ) | 2 | 389 | %100 | 1,4e-5 | 3,1e-4 | 8,1e-4 | +7,7e-5 R | hedef 168, stop 210, zaman 7, son 4 | 4,3e-2 | +0,0098 R |
| F1 (RSI2, BBRE) | 1 | 521 | %100 | 1,5e-5 | 1,2e-4 | 2,1e-4 | +2,8e-5 R | orta bant 297, stop 224 | 6,8e-2 | +0,0080 R |
| F2 (BOX ızgarası, 7) | 1 (20 gün 5m) | 567 | %100 | 2,0e-4 | 2,5e-3 | 1,4e-2 | +4,2e-4 R | hedef 134, stop 358, gün sonu 68, son 7 | 2,5e-3 | +0,0004 R |
| H (fonlama, 5) | 1 | 278 | %100 | 2,2e-5 | 1,1e-4 | 2,2e-4 | +3,3e-5 R | zaman 206, stop 49, kural 23 | 2,9e-2 | +0,0056 R |

  ΔR'nin kaynağı yine yalnız defterin fiyat/miktar adımına yuvarlamasıdır (en büyüğü F2'de bir hedef dolumu, K_BOX'takiyle
  aynı); `PATH_1H_GAP` ve `FUNDING_GAP` 0 (sentetik dünyalarda veri tam). Tek-tik raporu 4h/1d ailelerinde K'deki gibi
  0,006–0,015 R daha kötümserdir.
- **K aileleri parite (a)** (`scripts/engine_p3b_measure.py`, 2026-10-12, sentetik, `live_1h` kapısı, m-A/m-C sonrası):
| Aile | Dünya | Eşleşen işlem | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| | İşaretli ort. (çekirdek − defter) | Defter: kapanıştan / ihtiyatlı likidasyon | Tek-tik (`ohlc`) p95 \|ΔR\| | Tek-tik işaretli ort. |
|---|---|---|---|---|---|---|---|---|---|---|
| T2 (`K_T2`) | 31 | 207 | %100 | 3,3e-6 | 2,1e-5 | 7,3e-5 | +6,3e-6 R | 4 / 0 | — | — |
| M2 (`K_M2`) | 9 | 200 | %100 | 6,2e-6 | 1,9e-5 | 4,0e-5 | +7,3e-6 R | 1 / 0 | 2,0e-5 | −3,3e-5 R |
| Box (`K_BOX`) | 1 (60 gün 5m) | 331 | %100 | 1,6e-4 | 3,2e-3 | 1,4e-2 | +3,3e-4 R | 90 / 0 | 3,2e-3 | +0,0003 R |
| D4 (`K_D4`) | 7 | 200 | %100 | 9,4e-6 | 2,6e-4 | 1,0e-3 | +4,9e-5 R | 20 / 0 | 1,1e-1 | +0,0101 R |
| C4 (`K_C4_CV001…008` birlikte) | 7 | 222 | %100 | 4,5e-6 | 2,2e-4 | 6,9e-4 | +3,4e-5 R | 40 / 0 | 1,4e-1 | +0,0390 R |
| FM (`K_FM`) | 3 | 265 | %100 | 9,7e-8 | 5,9e-5 | 3,3e-4 | +1,0e-5 R | 39 / 0 | 1,2e-1 | +0,0164 R |

  Kapı (`live_1h`): **hepsi geçti** — kapılı sütunlar 2026-10-12 tablosuyla birebir aynı (m-A bu dünyalarda hiçbir işlemi değiştirmedi; m-C yalnız eksik 1h verisinde çalışır, sentetik dünyalarda veri tam). Tek-tik raporu (kapıya girmez) yalnız C4'te değişti, +0,0084 → +0,0390 R: o kipte giriş barının TEK tiki giriş saatini içerdiğinden bütün giriş barı hedefsizdir (çekirdeğin tek-tik yedeğiyle aynı kural; 1h kipinde yalnız ilk saat) — C4'ün hedefleri giriş barının sonraki saatlerinde sık ulaşılıyor. T2'nin tek-tik raporu bu koşuda koşulmadı (hedefsiz; değişemez).
- **Hız** (§5.3 ölçüm notu 3): bar/sn B 12.100, C 5.400, D 11.800, E 5.800, F1 7.700, H 5.900, F2 164; bir tam geçmiş
  koşusu saf sayısal ailelerde toplam ≈ 0,5, F2'de ≈ 86 çekirdek-saat (sandbox) — Box 5m ön-süzgeci P3b-2b'de belirleyici.
- **Testler.** Yeni: `tests/test_strategy_lib_indicators_p3b.py` (elle hesaplanmış altın değerler; başvurulara eşitlik;
  fonlama ölçüsü = fut_v1 fonksiyonu, pencere = bütün seri), `tests/test_strategy_lib_families_p3b.py` (katalog ↔ kayıt
  sayımı 58 + 35 = 93; I/J/G'de çalışan kod yok; mühürlü satırdan kurulum ve kapalı hata; bildirilmiş kimlik dosyaları elle
  sabit; HEAD'den `module_src_sha` + alias tekilleştirmesi defterde `DUPLICATE`; D modülü = K_D4 bayt-aynı; F2 alias =
  K_BOX parametreleri; her ailenin elle kurulmuş yolu — işaret dönüşü + LS geçişi, ters kesişim, taze kırılım + karşı kanal
  + short, Keltner 2R hedefi gerçek girişten + 48 bar zaman stopu, BBSQ sıkışma şartı, RSI2 ER süzgeci + orta bant + 24 bar,
  BBRE iki yön, FUND_FADE uç → yön + 6/18 bar + veri yok/sabit oran/< 30 değer → giriş yok + ER süzgeci, FUND_CARRY alan
  taraf + medyan altı çıkış; 45 varyantın altın sentetik yolu (özet sha'sı sabit; LS'de iki yön; ailenin çıkış türleri
  kapsanır); belirlenimcilik; çalışma anı G/Ç-saat-rastgelelik yoklaması ve çalışan dosyalar ⊆ bildirilmiş küme; saf
  ailelerde parite (a); kısmen eksik 1h verisinde başvurunun tek-tik yedeği ve işaret eşitliği). Güncellenen:
  `test_research_engine_kernel_p3b.py` (giriş saati: 1d tek tik, 1h alt barlar, ≤ 1h dilim; TP1 testleri giriş saatinin
  dışına taşındı; m-B; m-D kör noktası), `test_strategy_lib_p3b.py` (58 uygulanmış varyant), `test_research_engine_registry.py`
  (yeni sabitler, `kernel.entry_hour.policy` `fixed`).
- **Mühür (düzeltme mührü 4).** Henüz hiçbir gerçek veri koşusu olmadığı için `_v2` değil, önceki düzeltmelerle aynı yol:
  `WF_PROTOCOL_V1` (`kernel.entry_hour`) ve `THRESHOLD_DIRECTIONS_V1` değişti; bütün sha'lar, `REGISTRY_SHA`, `T_SEAL_MS` ve
  `config_epochs.json`'un `covered_until`'ı yeniden sabitlendi (§8.2). `LIB_V1` DEĞİŞMEDİ (saf aileler mühürlü satırları
  okur). Eşiklerin hiçbiri değişmedi; asla-gevşetme testleri geçer.
- **Açık kalanlar.** (1) O örtüleri (35) — sonraki P3b-2 adımı; `trials.seal_entries`'in gerçek `SPEC` satırları için
  bütün çalıştırılabilir varyantların `module_src_sha`'sı (şimdi 58). **2026-10-12 P3b-2b: yapıldı** (93; aşağıdaki
  P3b-2b notu). (2) Hız önlemleri (P3b-2b; §5.3): K kopyalarının bar
  başına canlı kural çağrısı ve Box 5m baskın; saf ailelerin pencere başına gösterge hesabı (≈ binlerce bar/sn) artımlı
  hale getirilebilir — bayt-aynılık testiyle. (3) Saf ailelerin parite (a)'sı çekirdeğin aritmetiğini sınar; bu ailelerin
  canlı bir karşılığı yoktur — AUTO_v1'de worker aynı modülü çağırır (parite (b) o zaman anlam kazanır). (4) Gerçek veride
  fonlama aralık değişimleri (m-D) ve `PATH_1H_GAP` sayısı P3g'de ölçülür.

**Değişiklik 2026-10-12 — P3b-2a bağımsız incelemesinin (PASS_WITH_MINORS) düzeltmesi; teknik okumalar; S1 düzeltme mührü 5.**
Sahip teknik okumaları devretti (teknik ayrıntılar uygulayıcıya bırakıldı); beş karar aynen uygulandı. Henüz hiçbir
gerçek veri koşusu olmadığı için bu `_v2` değil düzeltme mührüdür (önceki düzeltmelerle aynı yol). Bütçe ve varyant
sayıları DEĞİŞMEDİ (818 / 1.738, 106 varyant, 13 çalıştırılmayan; testli). Canlı karar yolu, worker kodu ve config
DEĞİŞMEDİ; hiçbir şey gece birimine bağlanmadı; ölçümler sentetik.

1. **f8 aralık kuralı** (bulgu 1; `strategy_lib/h_funding.py`). fut_v1 `align_funding` aralığı ardışık farktan çıkarır:
   4h/2h/1h dizisinde güncelden önceki TEK uzlaşma eksikse aralık 8h/4h/2h okunur, f8 YARIYA iner ve 20 günün en düşüğü
   olabilir (`f8_lo` = 1 → yanlış FUND_FADE LONG; FUND_CARRY'nin |f8| çıkışı yanlış tetiklenir) — açık başarısızlıktı.
   Kural: karar anında GÖRÜNÜR (≤ T − 60 sn) uzlaşmalarda güncelin fut_v1 aralığı bir öncekininkinden FARKLIYSA ya da son
   iki aralıktan biri çekirdeğin YEREL aralık kuralına göre boşluksa (`kernel.FundingSeries`; karar anında yalnız geçmiş
   tarafı; aynı sabitler ve aynı sonuç — test) f8, `f8_hi`, `f8_lo` NaN → giriş yok, pozisyonda durum değişmez (en az üç
   görünür uzlaşma gerekir). Pozisyondayken bir karar anında f8 ölçülemezse işlem `F8_UNMEASURABLE` ile işaretlenir —
   yalnız BİLGİ (kapı istatistiğinden çıkarmaz; boşluk işlemin penceresindeyse çekirdeğin `FUNDING_GAP`'i zaten çıkarır)
   *[2026-10-12 P3b-2b ile değişti: `F8_UNMEASURABLE` de kapı istatistiğinden çıkar — `WF_PROTOCOL_V1.gate_exclusions`,
   düzeltme mührü 6]*:
   FUND_CARRY ölçülemeyen kararda tutar (çıkışın ölçülemeyen parçası uygulanmaz), FUND_FADE'in çıkışı zaman/stoptur
   (f8 karara girmez, yalnız işaret). Taşıyıcı: sözleşmede yeni `Action.flags` (yalnız bilgi; `strategy_lib.hold(...)`;
   çekirdek işleme bir kez yazar; eylemin türü/anlamı değişmez). Kararlı 8h dizisinde davranış fut_v1'in kendisidir
   (ms kaymalı rastgele dizide her karar anında eşitlik; 8h'te güncelden önceki uzlaşmanın yokluğu 16h → fut_v1'de zaten
   NaN). Bu madde bir LIB satırı okunuşu değil, veri kusurunda f8'in ölçülemezliğidir; kodda (`module_src_sha` kimliği)
   ve testtedir.
   **m-D / f8 kör noktası (güncelleme).** Çekirdeğin m-D kör noktası f8 için de vardır ve testle sabitlendi: 8h → 4h
   geçişinde İLK 4h uzlaşması eksikse kalan 8h'lik aralık değişimin bir uzlaşma sonra olmasından ayırt edilemez → TEK
   karar penceresinde (bir sonraki uzlaşmaya kadar, bir 4h bar) f8 gerçeğin yarısıdır; sonraki pencere NaN'dır (aralık
   değişti). Sürekli "her ikinci uzlaşma eksik" deseni gerçek bir 4h → 8h geçişinden ayırt edilemez (yalnız geçişin ilk
   birkaç penceresi NaN). Bunların dışında kararlı dizide tek ya da arka arkaya iki eksik uzlaşma ve geçişin kendisi
   (8h → 4h'te 1, 4h → 8h'te yerel kural yüzünden ≈ 3 karar penceresi) NaN'dır — muhafazakâr. Kapatma yine P3g: veri
   biriminin borsa aralık geçmişiyle (fundingInfo) karşılaştırma.
2. **BBSQ sıkışması t − 1'de** (bulgu 2; mühürlü `LIB_V1.variants[BBSQ_20_*].params.squeeze_bar` = {`squeeze_at`:
   `PREVIOUS_BAR`, `breakout_at`: `SIGNAL_BAR`}). P3b-2a'nın "iken = kırılım barında" okunuşu yerine yerleşik sıkışma
   okunuşu: t − 1'in BB(20,2) genişliğinin, t − 1'de biten (kendisi dahil) son 120 genişlik içindeki ortalama sırası / 120
   ≤ 0,20; kırılım t'de (kapanış BB(20,2) dışında, ilk kez; B7). Kırılım barı bandı genişlettiğinden t'de ölçmek
   kırılımın kendisini ölçerdi. Test iki okunuşu ayırır: t − 1 sıkışık / t geniş → giriş; t − 1 geniş (pencerede eski
   sıçrama) / t sıkışık → giriş yok.
3. **FUND_CARRY işaret çıkışı** (bulgu 3; mühürlü `LIB_V1.variants[FUND_CARRY].exits.exit_rule` = {`rule_tr`,
   `abs_pct_below` 50, `sign_against` true}). P3b-2a'nın "işaret dönse de |f8| yüksekse kural çıkışı yok" okunuşu yerine:
   f8'in işareti pozisyona ters dönünce (short'ta f8 < 0, long'da f8 > 0: artık fonlamayı alan taraf değil; f8 = 0 ters
   değildir) → sonraki açılış; |f8| < %50'lik çıkışı aynen. Pozisyonsuz sonraki kararda |f8| hâlâ uçtaysa yeni alan taraf
   girer (testli).
4. **RSI2 girişi orta bandın doğru yanında** (bulgu 4; mühürlü `LIB_V1.variants[RSI2_*].params.entry_mid_side` =
   {`rule` `LONG_BELOW_MID_SHORT_ABOVE_MID`, BB(20,2) — B5'in çıkış çizgisi}): long kapanış < orta, short kapanış > orta
   (orta ölçülemiyorsa giriş yok); aksi halde orta bant çıkışı ilk kararda hemen tetiklenirdi.
5. **Pencere mühürlü okuma** (bulgu 5; `LIB_V1.defaults.window` = {`bars` 1h 400, 4h 400, 1d 399, `families` B, C, D, E,
   F1, H}): `strategy_lib.pure.WINDOW`'un değerleri (canlı kural çerçevesi: `paper_rules.intraday_rows` 400, `livebars`
   1d 399). EMA/ATR tohumu pencerenin başına bağlı olduğundan (ör. MAX_50_200'ün EMA200 durumu) pencere kuralın
   parçasıdır; `kernel.make_variant` kurulumda eşitliği denetler (`check_window`; değilse ValueError).
6. **Test düzeltmeleri** (bulgu 6). Box alias eşitliği işlem üreten dünyada (`box_world(22, 3 gün)`: 4 işlem, `to_dict`
   bayt-aynı; eski 2 günlük dünya 0 işlem veriyordu). H parite dünyası (`synthetic.fund_world`): zaman ekseninin %55'inde
   8h → 4h geçişi (00/04/…/20 damgaları; oran örttüğü dönemle ölçeklenir, geçişin ilk uzlaşması 8 saati örter), her
   damgada 0–999 ms kayma ve iki eksik uzlaşma (8h döneminde %30, 4h döneminde %80). Başvuru defteri fonlama kaynağına
   canlı gibi BAĞLANIR (`FundingSchedule.bind_source`; canlı `strategy_paper.bind_funding`): `hours_for` = satırların UTC
   saatlerinin birleşimi; o saatte satır yoksa ve iki yanı ≤ 8 saat (+ 120 sn) arayla ise o saat uzlaşma değildir (0),
   daha büyük boşlukta dönem BEKLER — deponun arşiv kaynağının kuralı (`m2x_sim.ArchiveFunding`), çekirdekten bağımsız.
   K dünyalarında (00/08/16 tam) davranış aynı.

**Sinyal sayıları** (aynı dünyalar, eski ↔ yeni kod; `kr.signals`): BBSQ — altın (seed 31) 1h 25 → 35, 4h 21 → 26; parite
dünyaları (tohum 11–14) 1h 165 → 220, 4h 113 → 152 (≈ +%34; kırılım barının kendi genişliği artık sıkışmayı bozmuyor).
RSI2 — altın 1h 176 → 165, 4h 86 → 84; parite 1h 851 → 797, 4h 624 → 587 (≈ −%6). H — P3b-2a'nın 8h dünyalarında (eksiksiz)
beş varyantın sinyalleri, işlemleri ve çıkışları birebir aynı (FUND_CARRY'nin işaret çıkışı hiç tetiklenmedi: AR(1)
oranı sıfırdan geçerken |f8| < %50'lik çıkışı önce gelir; işaret çıkışı |f8|'in büyük bir ters sıçramasında çalışır).
Yeni H dünyalarında (geçiş + ms + iki boşluk) eski ↔ yeni kod: altın 0 fark; parite (tohum 11–14) FUND_FADE_24h 375 → 374,
FUND_FADE_LOW_24h 259 → 258, FUND_CARRY 180 → 179, diğerleri aynı (boşluk/geçiş penceresinde engellenen yanlış girişler).

**Parite (a)** (`scripts/engine_p3b_measure.py --families E,F1,H` ve K aileleri, 2026-10-12, sentetik, `live_1h`): **hepsi
geçti**.

| Aile | Dünya | Eşleşen işlem | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| | İşaretli ort. (çekirdek − defter) | Çıkışlar (çekirdek) | Tek-tik (`ohlc`) p95 \|ΔR\| | Tek-tik işaretli ort. |
|---|---|---|---|---|---|---|---|---|---|---|
| E (KEL, BBSQ) | 1 | 204 | %100 | 5,9e-6 | 2,2e-4 | 3,2e-4 | +4,5e-5 R | hedef 78, stop 119, zaman 6, son 1 | 7,4e-2 | +0,0122 R |
| F1 (RSI2, BBRE) | 1 | 498 | %100 | 1,4e-5 | 1,2e-4 | 2,1e-4 | +2,8e-5 R | orta bant 278, stop 220 | 7,2e-2 | +0,0083 R |
| H (fonlama, 5) | 1 (8h → 4h, ms kayması, 2 boşluk) | 292 | %100 | 2,2e-5 | 1,3e-4 | 1,6e-2 | +1,7e-4 R | zaman 217, stop 48, kural 27 | 1,1e-2 | +0,0021 R |

  H'nin en büyük |ΔR|'ı (1,6e-2) 8h dönemindeki boşluğu örten işlemdir: defter o dönemi BEKLETİR ve sonraki uzlaşmaları
  yazmaz, çekirdek kalanları yazar ve işlemi `FUNDING_GAP` ile kapıdan çıkarır; 4h dönemindeki boşlukta iki taraf aynı
  (|ΔR| ≈ 5e-5). H dünyasında `FUNDING_GAP` 8 işlem, `F8_UNMEASURABLE` 8 işlem. K aileleri (başvurunun
  bağlanan fonlama kaynağıyla yeniden ölçüldü): **hepsi geçti**, kapılı sütunlar
  2026-10-12 tablosuyla birebir aynı (T2 207 / M2 200 / Box 331 / D4 200 / C4 222 / FM 265 eşleşen, kimlik %100, medyan
  |ΔR| ≤ 1,6e-4, p95 ≤ 3,2e-3, işaretli ort. ≤ +3,3e-4 R; tek-tik raporları da aynı, T2'ninki yine koşulmadı) — K
  dünyalarının 00/08/16 tam fonlamasında bağlanan kaynak eski aramayla aynı sonucu verir.
  Hız (aynı ölçüm, yüklü makinede, çekirdek bar/sn): E ≈ 5.700–6.200, F1 ≈ 7.400–8.300, H ≈ 4.500 (H 5.900'den: aralık
  kuralı + 4h döneminde iki kat uzlaşma;
  §5.3'teki tam geçmiş tahmininde H ≈ 0,08 → ≈ 0,1 çekirdek-saat, toplam değişmez).
- **Testler.** `tests/test_strategy_lib_families_p3b.py`: f8 aralık kuralı (200 uzlaşmalık 4h ve 1h dizisinde güncelden
  önceki eksik → giriş yok, eski okunuşta `f8_lo` = 1; kararlı 8h'te fut_v1'in kendisi; 8h → 4h geçişi ve sabitlenmiş kör
  nokta; yerel kural = çekirdeğinki, rastgele dizilerde), FUND_CARRY işaret çıkışı (iki yön) ve pozisyonda
  `F8_UNMEASURABLE` + tutma, FUND_FADE'de yalnız işaret, BBSQ t ↔ t − 1 ayrımı, RSI2 orta yan (iki yön), Box alias işlem
  üreten dünyada, H parite dünyasının özellikleri (4h damgaları, ms kaymaları, çekirdeğin kuralıyla ≥ 2 boşluk) ve beş H
  varyantında birlikte parite; altın yollar BBSQ, RSI2 ve H için bilerek yeniden sabitlendi.
  `tests/test_research_engine_registry.py`: yeni sabitler; teknik okumalar (yollar, `decision`, `reading_tr` öneki, değerler,
  başka yerde bu işaretle okuma yok, bütçe 818 / 1.738 ve 106 aynı) ve pencerenin çekirdek denetimi.
- **Mühür (düzeltme mührü 5).** Değişen: `LIB_V1` (dört teknik okuma; satır ve sayı sayıları aynı), `CONFIG_EPOCHS_V1`
  (yalnız `covered_until` = yeni `T_SEAL_MS`), `T_SEAL_MS`, `REGISTRY_SHA`; `WF_PROTOCOL_V1`, `STATS_V1`,
  `LESSON_REGISTRY_V1`, `PIT_RULES_V1`, `PRIOR_TRIALS_V1`, `THRESHOLD_DIRECTIONS_V1` DEĞİŞMEDİ (eşik değişmedi; okumalar
  kural tanımıdır, eşik değil). Yeni değerler §8.2'de; yerleşik `T_SEAL_MS` yolu (mühürlü içeriği değiştiren son
  commit'in işleyen zamanı ≤ `T_SEAL_MS` ≤ + 24 saat; tam dakika; tarih elle ayarlanmaz).
- **Açık kalanlar.** (1) Gerçek veride f8 aralık değişimleri, `F8_UNMEASURABLE` ve `FUNDING_GAP` sayısı P3g'de ölçülür
  (fundingInfo karşılaştırması kör noktayı kapatır). (2) FUND_CARRY'nin işaret çıkışı sentetik dünyalarda tetiklenmedi
  (yalnız elle kurulmuş testte). (3) P3b-2a'nın diğer açık kalanları (O örtüleri, hız önlemleri) aynen.
  **2026-10-12 P3b-2b: yapıldı** (aşağıdaki P3b-2b notu).

**P3b-2b uygulama notu (2026-10-12; O örtüleri, hız önlemleri, kapı dışı işaretler; S1 düzeltme mührü 6).** Canlı karar
yolu, worker kodu ve config DEĞİŞMEDİ (canlı kural fonksiyonları ve satır okuyucuları yalnız çağrılır); hiçbir şey gece
birimine bağlanmadı; gerçek veri yok, ölçümler sentetik. Henüz gerçek veri koşusu olmadığı için mühür değişikliği `_v2`
değil düzeltme mührüdür (önceki düzeltmelerle aynı yol).

- **Kapsam ve sayım (kayıtla uzlaşır, testli).** Katalog: `IMPLEMENTED` **93** = K 13 + B…H 45 + **O 35** (taban başına
  7: {T2, M2, BOX, D4, C4} × {F_ER, F_BTC, F_VOL, X_TRAIL2, X_CHAN3, X_TIME, X_TPL}); `PENDING` yok; `ALIAS` 2; `BLOCKED`
  13 → 93 = kaydın çalıştırılabilir varyantı (106 − 13). Her örtünün kendi `spec_sha`'sı (35'i, 13 kopya ve saf ailelerle
  birlikte 93'ü farklı; `trials.seal_entries` satırına eşit) ve bildirilmiş modül kümesi vardır: faktör modülü
  (`o_f_er`, `o_f_btc`, `o_f_vol`, `o_x_trail2`, `o_x_chan3`, `o_x_time`, `o_x_tpl`) + çerçeve (`o_cover`, `stream`,
  `contract`, `indicators`) ∪ taban kopyanın kümesi (`catalog.extra_modules` → `k_trend`/`k_box`/`k_d4`/`k_c4` +
  `LIVE_MODULES`; F_BTC'de ayrıca `regime_gate`). Faktör modülü taban modüllerini import etmez (taban katalogda kurulur):
  örtünün kümesinde yalnız KENDİ faktörü ve kendi tabanı vardır (elle sabitlenmiş dosya listeleri ve çalışma anı izi ⊆
  küme, test).
- **Örtü = taban + tam olarak bir faktör** (`o_cover.Cover`). Taban canlı kopyanın kendisidir, mühürlü parametreleriyle
  (`kernel.variant_spec`: `base_variants`, `base_params`, `copy_books.<taban>.max_hold`). **C4 tabanı C4 defteridir**
  (mühürlü `covers.bases.C4`: "8 varyasyon birlikte"): `k_c4.CandleBook` sekiz varyasyonu config sırasıyla dener ve ilk
  eşleşeninkini verir (canlı `candle_book.decide` → `hits[0]`; config.yaml sırasına ve tek varyasyon kopyalarına eşitlik
  testli), sembol başına tek pozisyon. **Filtre** (F_*): tabanın niyeti, koşul karar anında KAPANMIŞ verilerle sağlanırsa
  aynen geçer, sağlanmazsa o bar giriş yok; seri karar anında kesildiğinde bütün örtülerin niyetleri aynı (geleceği
  görmeme, 35 örtüde test). **Çıkış örtüsü** (X_*): giriş tabanınkidir; açık pozisyonda tabanın CLOSE'u önceliklidir,
  örtü stopu YALNIZ çekirdeğin doğrulanmış `MOVE_STOP`'uyla sıkılaştırır; aday seviye karar fiyatının ötesindeyse
  (delinmiş) sonraki açılışta piyasa çıkışı (CLOSE; canlı defterde fiyatın ötesine konan stop ilk gözlemde dolar);
  tabanın işaretleri korunur. Ölçümlerde örtülerin hiçbir stop isteği `MOVE_STOP_IGNORED` almadı.
- **Uygulama okumaları (mühürlü teknik okuma; `registry.impl_reading`: `decision` "P3b-2b uygulama okuması <madde>
  (2026-10-12)", `reading_tr` "teknik okuma 2026-10-12; sahip teknik ayrıntıları bana bıraktı; P3b-2b uygulama okuması
  …"; `LIB_V1.covers.reading` ve `covers.factors.<F>.reading` — faktör okuması her örtünün `params.factor_params`'ına,
  yani `spec_sha`'ya girer; kod okunuşu kurulumda doğrular, uymayan okuma kapalı hatadır).** F_ER: ER20 (Kaufman)
  tabanın sinyal diliminin son 21 kapanışından (Box'ta 1d, B3); ölçülemezse giriş yok; T2/M2/D4/C4 ≥ 0,3 geçer, Box
  < 0,3 geçer. F_BTC: B4'ün canlı `regime_gate.btc_regime`'i, karar anında kapanmış son **699** BTC 4h barında (canlı
  perp çerçevesinin 4h penceresi), satırlarda `ema200` sütunu yok → `ema_last` (SMA200 tohumlu); < 200 bar → giriş yok.
  F_VOL: ATR% = ATR14 / kapanış × 100 tabanın sinyal diliminde (Box'ta 1d, B3 ile aynı gerekçe), karar barının ATR%'si
  kendisi dahil son 120 değerin ortalama yüzdelik sırasında (`pct_rank`, BBSQ ile aynı tanım) ≤ 0,80; ölçülemezse giriş
  yok. X_TRAIL2: girişten bu yana (giriş barı dahil) en iyi uç gerçek giriş ± 1R'ye ulaşınca silahlanır; aday = en iyi uç
  ∓ 2 × ATR14 (sinyal dilimi, karar barında, tabanın penceresinde; Box 5m). X_CHAN3: LeBeau chandelier — girişten bu yana
  en iyi uç ∓ 3 × ATR22, ilk karardan itibaren (giriş öncesi tepeler sayılmaz). X_TIME: ⌊max_hold × 0,5⌋ — T2/M2 150 × 1d
  (B2 `x_time_bars`), D4 150 × 4h, C4 12 × 4h; Box 48 × 5m; taban daha kısa bir zaman stopu taşıyorsa o kalır. X_TPL:
  tabanın hedefleri yerine gerçek girişten 1R/2R/3R; ilk ikisi İLK miktarın 1/3'ü, son hedef kalanı; ilk hedeften sonra
  canlı defterin TP1 başa-baş kuralı aynen. Sözleşme ekleri: `Target.frac` (ilk miktarın payı; None = defterin
  varsayılanı, eski davranış aynı), `ctx["btc_4h_bars"]` (`Variant.needs_btc_4h`/`btc_4h_lookback`, `SeriesData.btc_4h`).
- **Hız önlemleri (her biri düz yolla bayt-aynı; `fast=False` = P3b-2b öncesi düz yol, testlerin başvurusu).**
  (a) **Paylaşılan akış** (`stream.SharedBase`, `kernel.make_family`): tabanın `signals`/`manage`'i (seri, karar anı,
  kopyanın BEYAN ettiği girdi anahtarı) başına bir kez hesaplanır — T2/M2'de anahtar kullanılmış yapı kümesinin kaynağı
  olan geçmiş içeriği (`k_trend.history_key`), D4'te açılış anı, C4'te açılış anı + varyasyon anlık görüntüsü, Box'ta
  `signals` anahtarsız (yönetim ön-süzgeçle O(1), paylaşılmaz); çekirdek her koşunun başında `bind_series(seri)` çağırır,
  başka seri nesnesi önbelleği sıfırlar, bağlanmamış akış önbelleklemez. `kernel.run_lockstep` ailenin koşularını bar bar
  sırayla ilerletir (aynı çağrılar; sonuçlar ayrı koşularla bayt-aynı): canlı yapı analizinin içerik adresli LRU önbelleği
  aynı bar için sıcak kalır. Test: beş tabanın ailesi (paylaşımlı ve bar bar) = her örtünün ayrı koşusu = düz yol.
  (b) **Box ön-süzgeci** (`k_box.BoxCopy.cannot_open`/`cannot_close`; F2 ve Box örtüleri aynı sınıfı kullanır): canlı
  kural, `box_theory.decide`'ın kendi koşullarından türetilen GEREK koşul tutmayan barda çağrılmaz — ≥ 3 kapanmış 5m bar;
  karar anı sinyal barının UTC gününde; izinli yanda tetik (kırmızı ve kapanış < önceki düşük / yeşil ve kapanış > önceki
  tepe); önceki günün kutusu (`read_box`) ve son iki mumun kenar konumu (canlı `_edge_location`) tetiklenen yanın kenarı;
  açık pozisyonda yalnız gün sonu mümkünse; kapanmamış/sırasız barda süzgeç karar vermez (kural çağrılır). Kanıt: 14
  günlük üç dünyada (biri uç durumlu: eksik günlük bar → kutu yok, eksik 5m saati, tetik eşitlikleri kapanış = önceki
  düşük/tepe ve kapanış = açılış, kenar bandına tam değen uç, iki kenara değen mum) sekiz Box parametre kümesinin (K_BOX
  + yedi F2) HER karar barında "süzgeç atladı ⇒ canlı kural OPEN üretmez", atlamadığında kopyanın niyeti = kuralınki;
  açık pozisyonda "atladı ⇒ CLOSE yok"; çekirdek koşuları düz yolla bayt-aynı. Aynı ilkeyle **D4 ön-süzgeci**
  (`_fresh_breakout`'un gerek koşulu c > önceki 20 barın tepesi ve önceki bar ≤ önceki kanal; kanal çıkışı ve zaman
  sınırı için kuralın taradığı aynı satırlar; aynı testler). (c) **Hızlı çerçeve** (`livebars.FastFrame`): kopyaların
  canlı çerçevesi pandas'sız (sütun listeleri; `ema200`/`atr14` bayt-aynı saf eşdeğerlerle) ve canlı satır okuyucularının
  kullandığı yüzün tamamıyla (`columns`, `tail`, `[sütunlar]`, `to_dict("records")`) — okuyucular ve kurallar aynen
  çağrılır; her okuyucunun çıktısı (değer, tür, anahtar sırası, NaN) pandas çerçevesininkiyle aynı (1, 5, 199, 200, 450
  barlık pencereler, hacim yok/None, float zaman damgası, tail 0/negatif/aşırı); C4 penceresi `livebars.candle_window`
  (satır sözlüğü kurmadan, `window_from_rows`'un aynı kesim ve denetimleri; her ret nedeniyle eşit). Ek: çekirdekte
  `ctx["history"]` demeti yalnız girişte yenilenir (aynı içerik).
- **Hız ve tam geçmiş tahmini**: §5.3 ölçüm notu 4 — K + B…H + O tam geçişi ≈ 3,7 çekirdek-saat (sandbox) ≈ 11 saat VPS
  ≈ 8–11 AÇIK gece (önce ≈ 233 çekirdek-saat).
- **Kapı dışı işaretler (mühürlü liste).** WF_PROTOCOL_V1 listeyi önceden yalnız metinlerde taşıyordu (`costs.funding_gap_tr`
  FUNDING_GAP, `kernel.stop_fill.path_gap_tr` PATH_1H_GAP); liste artık tek yerde: `WF_PROTOCOL_V1.gate_exclusions` =
  {`flags` [FUNDING_GAP, PATH_1H_GAP, F8_UNMEASURABLE], `rule_tr` (bütün kapı istatistiklerinden çıkar — Kapı A/B,
  `p_day`, DSR, CSCV-PBO, plasebo, ders bakışları; sayısı ve payı gösterilir; kayıttan silinmez), `decision` "P3b-2b
  talimatı (2026-10-12) …", `reading_tr`} ve `THRESHOLD_DIRECTIONS_V1`'de `gate_exclusions.flags` = `fixed` (sonraki bir
  sürüm işaret ekleyip çıkaramaz). `F8_UNMEASURABLE` P3b-2a'da yalnız bilgiydi; artık FUNDING_GAP/PATH_1H_GAP gibi
  kapıdan çıkar. Uygulayıcı `kernel.gate_excluded(işlem)` (`KernelTrade`, `to_dict()` ya da `flags` taşıyan kayıt;
  `GATE_EXCLUDED_FLAGS` mühürden okunur). Listeyi tüketen kapı kodu P3d/P3e'dedir (henüz yok).
- **Parite (a) örtülerde** (`scripts/engine_p3b_measure.py --families O`, 2026-10-12, sentetik, `live_1h`; taban başına
  yedi örtü bir aile birimi; başvuru = örtünün kendi kuralı → `apply_action` → `FuturesLedgerV2.tick`, başvurunun kendi
  geçmişi ve BTC 4h dilimiyle, `MOVE_STOP` başvuruda bağımsız yazılmış aynı kuralla, X_TPL payları defterin alt sınıfında
  `_FracLedger._close_part` ile — canlı defter ara hedefte kalanın `tp1_fraction`'ını kapattığından ilk miktarın üçte
  birini kapatamaz): **hepsi geçti**.

| Aile | Dünya | Eşleşen işlem (örtü başına) | Sinyal kimliği | Medyan \|ΔR\| | p95 \|ΔR\| | En büyük \|ΔR\| | İşaretli ort. (çekirdek − defter) | Çıkışlar (çekirdek) |
|---|---|---|---|---|---|---|---|---|
| O_T2 | 3 | 208 (F_ER 15, F_BTC 28, F_VOL 29, X_TRAIL2 39, X_CHAN3 37, X_TIME 29, X_TPL 31) | %100 | 4,8e-6 | 1,6e-5 | 2,7e-5 | +6,2e-6 R | kural 158, stop 42, son 6, hedef 1, zaman 1 |
| O_M2 | 2 | 397 (23, 52, 61, 68, 65, 63, 65) | %100 | 6,6e-6 | 1,8e-5 | 2,9e-5 | +7,3e-6 R | kural 375, stop 16, son 6 |
| O_BOX | 1 (150 gün 5m) | 5.232 (594, 360, 67, 1.112, 1.068, 949, 1.082) | %100 | 2,1e-4 | 2,7e-3 | 1,2e-1 | +4,1e-4 R | stop 4.257, hedef 410, kural 372, zaman 186, son 7 |
| O_D4 | 1 | 208 (20, 11, 27, 41, 37, 28, 44) | %100 | 1,0e-5 | 1,4e-4 | 3,5e-4 | +3,4e-5 R | stop 114, kural 83, hedef 11 |
| O_C4 | 2 | 340 (20, 41, 45, 58, 58, 59, 59) | %100 | 6,3e-6 | 1,4e-4 | 3,7e-4 | +2,8e-5 R | stop 208, hedef 75, kural 36, zaman 21 |

  ΔR'nin kaynağı yine defterin fiyat/miktar adımına yuvarlamasıdır; Box'taki tek büyük fark (0,12 R, X_TPL) defterin
  TP1 başa-baş stopunu fiyat adımına YUKARI yuvarlamasıdır (LONG; `break_even_price`): başa-baş seviyesine bir adım
  kalan bir mum defterde stopu tetikler, çekirdekte bir sonraki mumda kapanıştan çıkar — üç hedefli payların sınır
  durumu; medyan/p95 eşiğin çok altında. `PATH_1H_GAP` 0, `MOVE_STOP_IGNORED` 0 (iki tarafta), kapı dışı işaretli işlem
  0 (sentetik veri tam).
- **K aileleri parite (a)** (aynı betik, 2026-10-12, hızlı yolla; `live_1h`): **hepsi geçti** ve kapılı sütunlar
  2026-10-12 tablosuyla birebir aynı — hızlı çerçeve, ön-süzgeç ve pencere bayt-aynı olduğundan: T2 31 dünya / 207
  eşleşen, medyan 3,3e-6, p95 2,1e-5, en büyük 7,3e-5; M2 9 / 200, 6,2e-6, 1,9e-5, 4,0e-5; Box 1 (60 gün 5m) / 331,
  1,6e-4, 3,2e-3, 1,4e-2; D4 7 / 200, 9,4e-6, 2,6e-4, 1,0e-3; C4 (sekiz kopya birlikte) 7 / 222, 4,5e-6, 2,2e-4, 6,9e-4;
  FM 3 / 265, 9,7e-8, 5,9e-5, 3,3e-4; sinyal kimliği hepsinde %100, yalnız defterde ya da yalnız çekirdekte işlem yok.
  Hızlı yolun çekirdek hızı aynı koşuda (makinede eşzamanlı başka ölçümler varken) T2 121, M2 106, Box 26.350, D4 3.170,
  C4 1.790, FM 261 bar/sn.
- **Testler.** Yeni `tests/test_strategy_lib_covers_p3b.py`: katalog ↔ kayıt (35 örtü, 7/taban, 93 çalıştırılabilir,
  bekleyen yok, modül = faktör + taban); mühürlü satırdan kurulum ve kapalı hata (parametre, spec, faktör türü, okunuş
  değişikliği); C4 defteri = config sırası, ilk eşleşen = tek varyasyon kopyası; elle sabitlenmiş kimlik dosyaları
  (yalnız kendi faktörü, taban kümesi ⊂ örtü kümesi), farklı `spec_sha`'lar; faktörlerin elle kurulmuş yolları (F_ER eşiği
  iki yanda ve 0,3'te, Box'ta ters ve 1d; F_BTC = canlı tanım, 699 pencere, `ema200` sütunu okunmaz, < 200 → yok; F_VOL =
  ATR% sırası; X_TRAIL2 silahlanma / 2 ATR / yalnız sıkılaştırma / delinme CLOSE / SHORT / tabanın CLOSE'u öncelikli ve
  işaretleri korunur; X_CHAN3 giriş öncesi tepeyi saymaz; X_TIME 150/150/150/12/48; X_TPL payları 1/3, 1/3, kalan ve TP1
  sonrası başa-baş); geleceği görmeme (5 taban × 7 örtü); paylaşılan akış ve bar bar koşu bayt-aynılığı, bağlanmamış
  akış önbelleklemez, seri değişince sıfırlanır, geçmiş anahtarı içeriğe bağlı; Box ön-süzgecinin üç dünyada her bar
  sağlamlığı + uç durumlar + çekirdek bayt-aynılığı; D4 ön-süzgeci aynı; hızlı çerçeve her okuyucuda ve `candle_window`
  her ret nedeninde eşit; K kopyalarının hızlı/düz koşuları bayt-aynı; kapı dışı işaretler (mühür = çekirdek sabiti =
  H'nin işareti, `gate_excluded` üç kayıt biçiminde, kapı-dışı olmayan işaretler, asla-gevşetme yakalar, H dünyasında
  F8_UNMEASURABLE işlemleri dışlanır); örtülerde parite (a) (sekiz örtü, küçük dünyalar) ve `TB_P3B_FULL_PARITY=1` ile
  ≥ 200/aile; çalışma anı G/Ç-saat-rastgelelik yoklaması ve çalışan dosyalar ⊆ bildirilmiş küme; belirlenimcilik.
  Güncellenen: `test_strategy_lib_p3b.py` (93; `livebars` artık `indicators`'ı içerir → K kopyalarının kimlik
  dosyalarına `strategy_lib/indicators.py` eklendi), `test_strategy_lib_families_p3b.py` (O uygulandı; saf aile listesi
  O'yu dışlar; F2 kimlik dosyaları), `test_research_engine_registry.py` (yeni sabitler; P3b-2b okumaları; kapı dışı
  liste `fixed`).
- **Mühür (düzeltme mührü 6).** Değişen: `LIB_V1` (örtü uygulama okumaları: `covers.reading`, yedi
  `covers.factors.<F>.reading` ve 35 örtünün `factor_params.reading`'i; satır ve sayı sayıları aynı), `WF_PROTOCOL_V1`
  (`gate_exclusions`), `THRESHOLD_DIRECTIONS_V1` (`gate_exclusions.flags` `fixed`), `CONFIG_EPOCHS_V1` (yalnız
  `covered_until` = yeni `T_SEAL_MS`), `T_SEAL_MS`, `REGISTRY_SHA`; `STATS_V1`, `LESSON_REGISTRY_V1`, `PIT_RULES_V1`,
  `PRIOR_TRIALS_V1` DEĞİŞMEDİ. Bütçe ve varyant sayıları aynı (818 / 1.738, 106, 13 çalıştırılmayan; testli); eşiklerin
  hiçbiri değişmedi, asla-gevşetme testleri geçer. Yeni değerler §8.2'de; yerleşik `T_SEAL_MS` yolu.
- **Açık kalanlar.** (1) Hız sentetik dünyalarda ölçüldü; gerçek 5m serilerinde Box kuralının çağrılma payı ve
  pozisyonda geçen süre (Box ailelerinin süresi bunlarla orantılı) ve VPS çarpanı P3g'de ölçülür; T2/M2 ve Formasyonun
  süresi canlı yapı analizidir (canlı koda dokunulmadı; T2/M2 örtüleri arasında paylaşım geçmiş anahtarıyla sınırlı).
  (2) K_C4'ün sekiz tek varyasyon kopyası pencereyi ayrı ayrı kurar (≈ 0,4 çekirdek-saat); ortak pencere akışı ileride
  eklenebilir (bayt-aynılık testiyle). (3) Kapı dışı listeyi tüketen kapı kodu P3d/P3e'de; WF koşucusu aileleri
  `make_family` + `run_lockstep` ile kurmalıdır (P3d) — tek başına kurulan örtü doğrudur ama paylaşmaz. (4) X_TPL'nin
  canlı defter karşılığı yoktur (ara hedefte ilk miktarın payı); AUTO_v1'de worker'ın defteri bu payı desteklemelidir
  (P7a) — başvurunun `_FracLedger`'ı bunun tek farkıdır. (5) P3b-2a'nın açık kalanları (f8 aralık değişimleri,
  `PATH_1H_GAP` sayısı gerçek veride) aynen.

**P3d uygulama notu (2026-10-13; walk-forward, istatistik, CSCV-PBO, plasebo, Kapı A bakış yordamı; S1 mührü DEĞİŞMEDİ).**
Yalnız PAPER araştırma kodu; canlı kod/config/worker, gece birimi ve dağıtım değişmedi (gece bağlantısı P3g); gerçek veri
koşusu yok (yalnız sentetik dünyalar). Mühürlü kayıtlar (`WF_PROTOCOL_V1`, `STATS_V1`, `LIB_V1`,
`THRESHOLD_DIRECTIONS_V1`, `T_SEAL_MS`, `REGISTRY_SHA`) tek kaynaktır ve dokunulmadı; aşağıdaki okunuşlar mühürde
yazılmayan uygulama ayrıntılarıdır, "UNSEALED" maddeleri sahibe sorulur (yeniden mühür yapılmadı).

- **Modüller.** `research_engine/wf.py`: `quant.walkforward.make_folds(validation_days > 0)` + `run_three_way` (yeniden
  kullanılır, çatallanmaz); sınıflar mühürden (`H1_PLUS` çapalı 365/90/90, `INTRADAY` kayan 120/30/14, `GOLD_FUT` çapalı
  180/30/30, adım = test); veri başı ilk bar + 210 bar, en erken 2020-01-01; B1 bölüşümü (purge ⌊(1 + max_hold)/2⌋, embargo
  kalan; her iki sınırda); katman başına `leakage_check` + etiket ufku denetimi → `LEAK` katmanı atılır ve sayılır; sabit
  parametreli satır tek adaylı `run_three_way`; `COIN_FIT` üç yollu seçim (§2.4-2 büzülmesi, en iyi 3 aday, havuz eşiği
  0,02R) ve katman başına `SELECT` satırı; kapı dışı işlemler (`kernel.gate_excluded`) seçim dahil hiçbir istatistiğe girmez,
  sayı/pay/işaret dağılımı satırda; aileler `kernel.run_lockstep`, kopya + örtüler `kernel.make_family` + `run_lockstep`.
  `research_engine/stats.py`: `p_day`, BH, DSR (`validation.deflated_sharpe` + ampirik `sr_var`), seçim grupları, bakış
  takvimi ve §3.7 yordamı `gate_a_look` (zincir + mühür → satır istatistikleri → PBO → BH → DSR → `FWD_READ` → `LOOK_A`).
  `research_engine/cscv.py`: C(16,8) = 12.870 bölünmenin hepsi. `research_engine/placebo.py`: K = 5, B6 tohumu, çekirdeğin
  zorlanmış yeniden oynatımı, gün kümelenmeli fark CI'ı. `synthetic.py`: işlem düzeyi Kapı A dünyası (`gate_world`; boş ve
  dikilmiş avantajlı). Deneme defterine yazım yalnız `trials.TrialsLedger.append` (AST testi: yeni modüllerde dosya
  açma/yazma/silme yok).
- **Okunuşlar (mühürde yazılmayan ayrıntı; sıkılaştıran ya da nötr).** (1) İşlemin günü GİRİŞ UTC günüdür (OOS üyeliği
  girişle → her OOS işlemi OOS ekseninde; CSCV'nin ortak ekseni); DSR'nin günlük getirisi = o gün girilen işlemlerin ADVERSE
  net USDT'si / 200 (gerçekleşmiş, girişe yazılır; çekirdek bacaklarından günlük piyasa değerlemesi yok). (2) `p_day`:
  R mikro-R'ye yuvarlanır, karşılaştırma tam sayıyla kesin; p = (1 + aşım) / (B + 1); gün çekimi
  `random.Random(B6).choices(range(D), k=D)` akışının KENDİSİ (aynı Mersenne Twister durumu numpy'ye aktarılır; testte
  saf Python başvurusuyla eşit); < 2 gün → p yok (BH'de 1). (3) Sızıntı denetimine etiket ufku eklendi (eğitim işleminin
  çıkışı doğrulamaya, doğrulamanınki teste taşamaz) — kanıtlanmamış satırı atar, gevşetmez. (4) `max_hold` çözümü:
  `exits.max_hold_bars`; kopya/örtü/F2'de `copy_books.<taban>.max_hold`; Box "UTC gün sonu" = bir UTC günü (288 × 5m);
  çok varyantlı satırda (ızgara) en uzun boşluk. (5) `COIN_FIT`: havuz = verilen bütün P23 coin'leri (coin dahil), küme =
  coin'in kümesi − coin; "havuzun seçimi" = adaylar içinde doğrulama havuz ortalaması en yüksek olan; eşitlikte ızgara
  sırası; eğitim/doğrulama dilimi boşsa katman atlanır (test günleri işlemsiz gün olarak OOS'ta kalır). (6) CSCV: ölçü
  günlük Sharpe (Bailey vd. varsayılanı; DSR ile aynı), T mod 16 en eski gün atılır, grup ekseni üyelerin OOS günlerinin
  kesişimi, OOS sırasında eşitlik ortalama sıra, IS-en-iyide eşitlik ilk sütun. (7) `sr_var` = o bakışta Sharpe'ı
  hesaplanan aile satırlarının örneklem varyansı (ddof = 1), taban satırın kendi 1/(T − 1)'i. (8) Plasebo: ATR katı sinyal
  kapanışına ve sinyal barının ATR14'üne göre; `max_hold_bars` = gerçek tutuş − 1 (çekirdek çıkış barını sayar; zaman
  çıkışında tutuşlar eşit, testli); çekirdek girişi reddederse aynı üreteçten yeniden çekim (≤ 20); "± tutuş" = |t − gerçek
  giriş| ≤ gerçek tutuş. (9) Seçim grupları: standart/diğer → aile × kapsam; `COIN_FIT` → ızgara; `COIN:` kopya satırları →
  kopyanın coin satırları; kopyanın `POOLED_OWN` satırı ve örtüler → {kopya + örtüleri} (C4'te sekiz varyasyon + yedi
  örtü). (10) Bakış gecesi: gün > 5 ve o ay defterde `LOOK_A` yok; `look_no` = defterdeki farklı bakış aylarının sayısı + 1;
  ara gecede hüküm kelimesi ve yazım yok. (11) Hızlı kip (`full=False`; yalnız boş dünya deneyinde): BH'de geçemeyecek p
  erken durur, koşulu tutmayan satırın PBO/plasebosu hesaplanmaz — hüküm tam kiple aynı (testli); defter yazan yordam hep
  tam kiptir.
- **UNSEALED — mühürde yok, sahibe sorulur (uydurulmadı; kapalı hata).** *[2026-10-13 ile değişti: U2–U5 düzeltme mührü 7
  ile `STATS_V1` okuması oldu, U1 geçici kalır ve hiçbir geçiş eyleme dönüşmez, U6/U7 yalnız belgelenir — aşağıdaki
  değişiklik notu.]* (U1) Kapı A eşiklerinin S1 dışındakileri
  (OOS ≥ 100 işlem ve ≥ 2 takvim yılı, katman payı ≥ %60, PBO < 0,25, DSR ≥ 0,95, ileri okuma eşikleri) ana belge §6.7
  gereği S2'dedir (P4a): `stats.GATE_A_PROVISIONAL` = S1 değerleri (q, plasebo alt sınırı, tek enstrüman payı, küme
  tutarlılığı) + §3.7 METNİ, `sealed: False`; her `LOOK_A` satırı kaynağını yazar (`PROVISIONAL_UNSEALED`). "≥ 2 takvim
  yılı" ≥ 730 OOS günü, altının "≥ 9 ay"ı ≥ 274 gün okundu. (U2) Plasebo fark CI'ının tekrar sayısı, tohumu ve taraf
  başına en az günü S1'de yok: B = 10.000 (p_day'in mühürlü sayısı), tohum B6 biçimi `row_id|look_no|placebo_ci|real` /
  `…|placebo`, en az 5 gün (laboratuvar `STRICT_MIN_DAYS`), yüzdelik ⌊α/2·(B−1)⌋ / ⌈(1−α/2)·(B−1)⌉. (U3) CSCV başarım ölçüsü
  mühürde adlandırılmamış (Sharpe seçildi). (U4) **Hep piyasada olan ters dönen kurallarda plasebo tanımsız:** "kuralın
  sinyal vermediği ve gerçek işlemlerin ± tutuşu dışındaki bar" kümesi boştur (sentetik 1d dünyada TS_*_LS 5/5 ve
  MAX_*_1d_LS 2/2 için 0 uygun bar; Donchian LS'de var) → plasebo yok → bu satırlar Kapı A'yı hiç geçemez (kapalı hata,
  testli). Sahip kararı gerekir (ör. "sinyal" = gerçek GİRİŞ barı, ya da ± tutuş dışlamasının bu kurallarda yeniden
  tanımı). (U5) `K_FM@POOLED_OWN`'un örtüsü yok → seçim grubu tek yapılandırma → PBO hesaplanamaz → hiç geçemez (mühürlü
  grup tanımının sonucu). (U6) Kopya/türev "aynı config dönemi" kıyası ve altının XAU–PAXG tutarlılığı girdi olarak
  istenir (`epoch_ok`, `cluster_coherence_r`); verilmezse kapı geçmez (P4a). (U7) BH çözünürlüğü: m = 818 ve B = 10.000'de
  tek başına geçen bir satır p = 1/10.001 (hiç aşım yok) ister (q = 0,0818); ikinci bir anlamlı satır eşiği gevşetmez,
  yalnız sırayı değiştirir.
- **Ölçümler (bu sandbox, tek süreç).** Tam sentetik Kapı A bakışı (29 gerçek havuz satırı, aile 818, plasebo + CSCV + DSR
  + `FWD_READ`/`LOOK_A` yazımı, N = 4.513): ≈ 9 sn. Uçtan uca çekirdek → WF → plasebo → bakış (3 coin × 11 varyant, 1d,
  1.600 bar + 1h alt barlar): ≈ 8 sn (çekirdek ≈ 7,5 sn). 200 boş dünya (hızlı kip): 0/200 dünya, 0/5.800 satır Kapı A'yı
  geçti (≤ q = 0,10); ≈ 115 sn. Dikilmiş avantajlı dünyada yalnız dikilen satır geçer (q 0,082, DSR ≈ 1,0, PBO 0, plasebo
  CI alt > 0).
- **Testler.** `tests/test_research_engine_wf_p3d.py` (B1 ve her iki sınırda boşluk, sınıf uzunlukları, `max_hold` çözümü,
  LEAK katmanının atılması/sayılması, arındırma bölgesi, çekirdek zaman stoplarıyla sızıntısızlık, `run_three_way`
  adımlarının yalnız kendi dilimini gördüğü casus testi, test dilimini zehirlemenin seçimi değiştirmemesi, büzülme + havuz
  eşiği + ADVERSE seçimi, küme tutarlılığı ve ızgara matrisi, `SELECT` satırlarının bir kez eklenmesi, mühürlü maliyet
  tablosu, kapı dışı işlemler, `make_family` + `run_lockstep` bayt-aynılığı, uçtan uca bakış),
  `tests/test_research_engine_stats_p3d.py` (`p_day` = başvuru uygulaması, tohum/akış eşitliği, parça/bellek/taşma yolu,
  boşta kalibrasyon, BH = adım-yukarı yordamı ve m'nin küçülmemesi, DSR ampirik `sr_var` + taban, dikilmiş avantaj geçer,
  200 boş dünya ≤ q (`slow`), hızlı kip = tam kip, kapı dışı işlemler, bakış gecesi kuralı ve özellik testi, kırık zincir /
  mühür → kapı kapalı ve yazım yok, geçici eşiklerin etiketi, seçim grupları, yazım yolu AST'si),
  `tests/test_research_engine_cscv_p3d.py` (gürültü → ortalama PBO 0,5 ± 0,1; dikilmiş avantaj → < 0,1; düz başvuruya
  eşitlik; hesaplanamaz durumlar), `tests/test_research_engine_placebo_p3d.py` (B6 tohumu, belirlenimcilik ve yeniden
  çekim, uygun bar kümesi, sinyal kaydedicinin sonucu değiştirmemesi, çekirdek plasebosunun yön/tutuş/ATR katı/katman
  eşitliği, fark CI'ı, hep piyasada olan kural). Yalıtım sözleşmesi (`test_research_engine_p1b_contract.py`) paket dışı
  izinlere gerekçesiyle `wf.py` → `quant` (yalnız `walkforward`), `stats.py` → `validation`, `placebo.py` →
  `strategy_lib` (+ `indicators`) eklendi; dört modül ağsız modül listesinde.
- **Açık kalanlar.** (1) U1–U7 sahibe (U1 S2 ile; U2–U4 S1 düzeltme mührüyle ya da S2'de). (2) Gece bağlantısı, önbellek,
  P23 gerçek veri koşusu ve bütçe P3g'de; ileri okuma (`fwd`) keşif katmanından gelir (P3e). (3) `POOLED_OWN`/küme
  satırlarının kapsam sembollerinin canlı kümelerden çözümü ve XSEC (PIT, P3c) satır kurucuları P3e/P3g'de; bu adımda
  `gate_a_look` satır sonuçlarını hazır alır.

**Değişiklik 2026-10-13 — P3d boşluklarına karar (sahip teknik okumaları koordinatöre devretti; asla gevşetme); S1
düzeltme mührü 7.** Henüz hiçbir gerçek veri koşusu olmadığı için `_v2` değil düzeltme mührüdür.

- **U2 (mühürlendi, `STATS_V1.placebo.ci`):** plasebo fark CI'ı uygulandığı gibi — iki taraf ayrı ve bağımsız gün kümeli
  bootstrap, B = 10.000 (p_day'in mühürlü sayısı), tohumlar B6 biçimi `row_id|look_no|placebo_ci|real` ve `…|placebo`,
  gün çekimi `random.Random(tohum).choices(range(D), k=D)`, taraf başına en az 5 gün (azsa CI yok → kapı geçmez),
  yüzdelik indisleri ⌊α/2·(B−1)⌋ / ⌈(1−α/2)·(B−1)⌉. Asla-gevşetme: `min_days_per_side` `min`, tekrar/tohum/düzen `fixed`.
- **U3 (mühürlendi, `STATS_V1.pbo.metric`):** CSCV ölçüsü günlük Sharpe (ortalama / örneklem std, ddof = 1; std = 0 → 0);
  aynı okumada uygulanan kurallar: T mod 16 en eski gün atılır, grup ekseni üyelerin OOS günlerinin kesişimi, IS-en-iyide
  eşitlik ilk yapılandırma, OOS sırasında eşitlik ortalama sıra. Ölçü, artık gün ve eksen `fixed`.
- **U4 (mühürlendi, `STATS_V1.placebo.no_eligible_bar`):** kapalı hata korunur — uygun bar kümesi boşsa (hep piyasada olan
  ters dönen kurallar: TS_*_LS, MAX_*_LS) plasebo yoktur ve satır Kapı A'yı geçemez; başka plasebo uydurulmaz. "± tutuş"
  = |t − gerçek giriş| ≤ gerçek tutuş. Kural `fixed`.
- **U5 (mühürlendi, `STATS_V1.pbo.single_config_group`):** kapalı hata korunur — K_FM@POOLED_OWN'un seçim grubu tek
  yapılandırmadır, PBO hesaplanamaz, Kapı A'yı geçemez. Kural `fixed`.
- **U1 (geçici kalır; S2'ye, sahibin kapı metni onayından sonra):** `GATE_A_PROVISIONAL` `PROVISIONAL_UNSEALED`
  etiketini taşır; mühürlü S2 eşik nesnesi (`stats.SEALED_GATE_A_THRESHOLDS`) yokken bütün koşulları tutan satırın hükmü
  `PASS_A_PROVISIONAL`'dır, hiçbir yol `PASS_A` üretmez (eşik sözlüğünde `sealed: True` sahtelense bile), `LOOK_A`
  satırları geçici kaynağı ve `actionable: false` taşır; `stats.actionable_pass` P4a'nın doğum kuyruğu için tek kapıdır ve
  S2'ye kadar hep yanlıştır (test).
- **U6/U7:** yalnız belgelenir (yukarıdaki P3d notu).
- **Kod:** `placebo.py` ve `cscv.py` değerleri mühürden okur ve okuma uygulananla uyuşmazsa yüklenmez (kapalı hata);
  `stats.py` geçici geçişi yukarıdaki gibi ayırır.
- **Mühür:** değişen `STATS_V1` (dört okuma; `thresholds` ağacı aynı), `THRESHOLD_DIRECTIONS_V1` (yeni tanımların yönleri),
  `CONFIG_EPOCHS_V1` (yalnız `covered_until` = yeni `T_SEAL_MS`), `T_SEAL_MS`, `REGISTRY_SHA`; `LIB_V1`, `WF_PROTOCOL_V1`,
  `LESSON_REGISTRY_V1`, `PIT_RULES_V1`, `PRIOR_TRIALS_V1` DEĞİŞMEDİ. Bütçe ve sayılar aynı (818 / 1.738, 106, 13
  çalıştırılmayan, N 4.513); hiçbir eşik değişmedi. Yeni değerler §8.2'de; yerleşik `T_SEAL_MS` yolu (commit'ten sonraki
  tam dakika).
- **Testler.** `test_research_engine_registry.py` (yeni sabitler; teknik okuma kümesi U2–U5'i içerir;
  `test_p3d_readings_2026_10_13_are_sealed_without_touching_thresholds`), `test_research_engine_stats_p3d.py`
  (`test_provisional_thresholds_never_yield_an_actionable_pass`, `test_p3d_readings_are_sealed_and_used_by_the_code`;
  bakış testleri `PASS_A_PROVISIONAL` bekler).

**Değişiklik 2026-10-13 — P3d bağımsız incelemesi (FAIL: açık kalan korumalar; istatistiğin kendisi doğru bulundu);
S1 düzeltme mührü 8.** Henüz hiçbir gerçek veri koşusu yok → `_v2` değil düzeltme mührü. Bütçe, sayılar ve eşikler
hiçbir yerde gevşemedi (`STATS_V1.thresholds` aynı; testli).

- **1 — N koruması (kod).** `gate_a_look` defterin öncüllerini `PRIOR_TRIALS_V1` ile (satır satır, toplam 2.775) ve DSR
  N'ini mühürdeki N ile (`LIB_V1.budget_declared.N_AT_SEAL_MIN` = 4.513) karşılaştırır; tutmazsa `CLOSED`
  (`PRIORS_MISMATCH` / `N_BELOW_SEAL`), hiçbir satır yazılmaz (boş defter, yalnız öncüller, eksik öncül testli).
- **2 — m koruması (kod).** Deftere yazan `gate_a_look`'ta aile her zaman `registry.gate_a_rows()`'tur (818); `family`
  parametresi yalnız saf `gate_a_evaluate`'te vardır. Daha küçük aileyle yazan testler düzeltildi.
- **3 — seçim grubu bütünlüğü (mühürlendi, `STATS_V1.pbo.group_completeness`, R3).** Her grubun üyeleri mühürden kurulur
  (`stats.expected_group_members`); eksik ya da fazla üyeli grup `INCOMPLETE` → PBO yok → satır geçmez
  (`PBO_GROUP_INCOMPLETE`, `groups_incomplete` sayılır; seçilmiş alt küme testi).
- **4 — CSCV blok kenarı arındırması (mühürlendi, U3 okumasına eklendi).** *[2026-10-13 düzeltme mührü 9 ile GERİ ALINDI —
  aşağıdaki değişiklik notu.]* Eşit bloklar kurulduktan sonra 16 bloğun her
  birinin ilk h takvim günü atılır, h = grubun mühürlü en uzun max_hold'u (gün); arındırmadan sonra bir blokta < 5 gün
  kalırsa hesaplanamaz. Bilinen cevaplar arındırmayla da tutar (gürültü ortalaması 0,5 ± 0,1; dikilmiş < 0,1). **Sonuç
  (sahibe):** max_hold'u 300 × 1d olan grupların (B, C, D aileleri, T2/M2 kopya-örtü grupları, bu ailelerin COIN_FIT
  ızgaraları) PBO'su ≥ 16 × 305 = 4.880 OOS günü (≈ 13,4 yıl) ister → 818 satırın **296'sı** pratikte Kapı A'yı geçemez;
  95 satır (300 × 4h, D4 türü) ≥ 880 OOS günü ister; kalan 427 satır ≤ 208 gün.
- **5 — plasebo (mühürlendi, `STATS_V1.placebo.construction`, R5; hepsi `fixed`).** (a) Koşul HEM net (ADVERSE) HEM brüt R'da
  (ücret, kayma, spread sıfır; fonlama gerçek) tutmalı — iki CI'ın alt sınırı da > 0; LOOK_A ikisini de yazar. Yeni
  oynaklık kümelenmeli boş dünya (`synthetic.vol_walk`: düşük/yüksek oynaklık rejimleri, sürüklenme ∝ σ — yalnız risk
  primi): yalnız yüksek oynaklıkta long giren kural p/DSR/PBO/katman/ileri okumayı geçer ama Kapı A'yı GEÇEMEZ (brüt
  plasebo; testli). **Bulgu:** ilk kurulumda (gerçek tutuş zaman stopu + açık ATR stopu) plasebo gerçek işlemden kısa
  tutuluyordu (gerçek işlemin gerçekleşen tutuşu üst sınır, plasebo ayrıca erken stoplanır) → sürüklenmeli piyasada long
  kural plaseboyu salt sürüklenmeyle yeniyordu (o dünyada brüt CI alt > 0 çıktı). Düzeltme (sıkılaştırma, R5'in parçası):
  **maruziyet eşleşmesi** — plasebo yalnız zamanla, gerçek işlemin tutuşunda çıkar; ATR katı stop R birimidir, koruyucu
  stop %45 uzaktadır (likidasyon tamponu tutar) ve R ATR katı stopa göre aynı bacak ve miktarla kesin yeniden ölçülür;
  tutuşu veri sonuna sığmayan çekim reddedilir. (b) Kapsama: yalnız en az bir kapı-içi plasebosu olan gerçek işlemler
  karşılaştırılır; bunlar gerçek işlemlerin ≥ %80'i değilse satır geçmez (`PLACEBO_COVERAGE`, sayılır). (c) Mühürlü
  okuma: `MAX_TRIES` 20, ATR14, sinyal kapanışı çapası, plasebo `max_hold_bars` = gerçek `bars_held` − 1, maruziyet
  eşleşmesi (`exit`, `guard_stop_frac` 0,45), net + brüt şartı, %80 kapsama.
- **6 — eyleme dönüşme ve saat (kod).** `actionable_pass(defter, row_id, look_no)` yalnız doğrulanmış zincirden (HWM
  dahil) geri okunan `kind == LOOK_A`, mühürlü S2 eşikleriyle (`params.thresholds_sealed`) verilmiş `PASS_A` için doğrudur
  (saf değerlendirme sonucu asla; kurcalanmış defterde yanlış; S2 yokken hep yanlış). `gate_a_look` enjekte saatin
  (`clock`) bugünkü gecesi olmayan `night`'ı reddeder (`REFUSED`/`NOT_CURRENT_NIGHT`; geçmiş/gelecek bakış yok).
- **7 — yalnız belgelenir (S2 tasarım maddesi, sahibe).** Ana belge Kapı A hükümlerinin aylık bakışlar boyunca alfa
  harcamasıyla verilmesini öngörür; şimdiki yordam her ay q = 0,10 ile yeniden sınar (aynı satır bir ay geçemeyip sonraki
  ay geçebilir → çok bakışlı birikimli birinci tür hata kontrol edilmez). Kapı A eşikleri sahibin onayından sonra S2'de
  mühürlenirken bakışlar arası alfa harcaması (ör. Kapı B'nin 0,01/0,015/0,025 düzeni gibi) ya da "satır başına tek Kapı A
  kararı" tanımı karara bağlanmalıdır; P3d'de uygulanmadı.
- **Boş dünya ölçümleri (hızlı kip, hüküm-eşdeğer).** Sabit oynaklık: 0/200 dünya, 0/5.800 satır (≈ 131 sn); oynaklık
  kümelenmeli (tuzak satırıyla): 1/200 dünya (0,005), 1/5.800 satır (≈ 274 sn) — ikisi de ≤ q. Dikilmiş avantaj (E ailesi
  satırı, R5 kurulumuyla) geçer.
- **Mühür:** değişen `STATS_V1` (U3'e blok kenarı arındırması; R3; R5), `THRESHOLD_DIRECTIONS_V1` (yeni tanımlar
  `fixed`), `CONFIG_EPOCHS_V1` (yalnız `covered_until`), `T_SEAL_MS`, `REGISTRY_SHA`; diğer beş kayıt DEĞİŞMEDİ. Değerler
  §8.2'de.
- **Testler.** `test_research_engine_stats_p3d.py`: N/öncül koruması, şimdiki gece, `actionable_pass` geri okuma (mühürlü
  test nesnesiyle olumlu, kurcalamada olumsuz), eksik grup, kapsama, oynaklık kümelenmeli boş dünya (tek dünya + 200
  dünya `slow`), bakış testleri 818 satırlık aile ve saatle; `test_research_engine_cscv_p3d.py`: blok kenarı arındırması;
  `test_research_engine_placebo_p3d.py`: maruziyet eşleşmesi, brüt R, kapsama; `test_research_engine_wf_p3d.py`: uçtan uca
  bakış N = 4.513 ve eksik grup; `test_research_engine_registry.py`: `test_fixup_8_review_readings_are_sealed_and_fixed`.

**Değişiklik 2026-10-13 — CSCV blok kenarı arındırması geri alındı, matris günlük piyasa değerlemesi (koordinatör kararı;
sahip teknik okumaları devretti); S1 düzeltme mührü 9.** Henüz hiçbir gerçek veri koşusu yok → `_v2` değil düzeltme mührü.
Bütçe, sayılar ve eşikler değişmedi (`STATS_V1.thresholds` aynı).

- **Neden.** Düzeltme mührü 8'in arındırması (her bloğun ilk h = en uzun max_hold günü) istatistiksel bir zorunluluk
  değil, tasarımın yan etkisiydi: 300 × 1d max_hold'lu bütün günlük trend aileleri (B, C, D; T2/M2 kopyaları ve örtüleri;
  bu ailelerin COIN_FIT ızgaraları) ≥ 4.880 OOS günü isteyecekti → 818 satırın 296'sı kalıcı olarak sınanamazdı. Sızıntıyı
  önemli kılan, işlemin bütün sonucunun GİRİŞ gününe yazılmasıydı (bloğun sonunda açılan işlemin sonucu bir sonraki
  bloğun fiyat yolundan gelir).
- **Yeni okuma (mühürlendi, U3: `STATS_V1.pbo.metric.matrix_returns` = `DAILY_MTM_PRICE_PATH_RESIDUAL_ON_EXIT_DAY`,
  `matrix_fallback` = `LINEAR_OVER_HELD_DAYS_ONLY_WITHOUT_PRICE_PATH`; ikisi `fixed`; Bailey vd. uygulaması).** CSCV
  matrisi GÜNLÜK PİYASA DEĞERLEMESİ getirileridir: her işlemin ADVERSE net sonucu tutulduğu UTC günlerine dağıtılır.
  Kullanılan yöntem tam olarak **fiyat yolu**dur (çekirdeğin bar başına değerleme çıktısı yok; bu yüzden pozisyonun bar
  kapanışından bar kapanışına fiyat hareketi kullanılır): ilk gün giriş referansından (`entry_ref`) gün sonu işaretine, ara
  günler işaretten işarete, son gün işaretten bacakların ağırlıklı çıkış fiyatına (`exit_ref`); işaret = sinyal diliminin o
  UTC gününün sonunda ya da önce kapanan (ve girişten sonra kapanan) son barının kapanışı, yoksa bir önceki işaret; günlük
  R = yön × fiyat farkı / |giriş − ilk stop|; kalan (maliyet, fonlama, kısmi çıkış etkisi) = net R − Σ günlük R çıkış
  gününe yazılır (toplam = gerçekleşen net R, kesin; testli); USDT = R × (net USDT / net R). Fiyat yolu YALNIZ yoksa
  (işlem satırı barsız kurulmuşsa) tutulan günlere doğrusal. OOS ekseni dışına düşen günler atılır. Blok kenarı
  arındırması YOK. Korunanlar: en eski T mod 16 gün atılır, ortak eksen üyelerin OOS günlerinin kesişimi, eşitlik
  kuralları, R3 grup bütünlüğü. Uygulama: `stats.mtm_allocation` / `mtm_daily`, `wf.trade_rows(sonuçlar, data)`
  (+ `exit_ref`), sentetik dünyada `light_trade` fiyat yolu. DSR'nin günlük serisi DEĞİŞMEDİ (giriş günü; açık madde
  aşağıda).
- **Yapılabilirlik (sahibe).** Mühürlü PBO alt sınırı (16 blok × 5 gün = 80 ortak OOS günü) Kapı A'nın OOS alt sınırının
  (≥ 730 gün; altın ≥ 274) çok altındadır; bu yüzden OOS alt sınırına ulaşan satırın PBO'su hesaplanabilir: **818 satırın
  792'si**. Hesaplanamayan 26: UNSPECIFIED yüzünden hiç koşmayan 13 varyantın 25 satırı (I spread 2, J XSEC 5, G altın 18;
  grupları tümüyle boşluklu) ve tek yapılandırmalı K_FM@POOLED_OWN (U5).
- **Doğrulamalar.** Bilinen cevaplar arındırmasız tutar (gürültü ortalaması 0,5 ± 0,1; dikilmiş < 0,1). Dikilmiş avantaj
  satırı (KEL_20_2_4h@POOLED_P23; E ailesi — düzeltme mührü 8'de seçilmişti, günlük satıra dönmek gerekmedi) Kapı A'yı
  geçer; oynaklık kümelenmeli dünyadaki tuzak satırı geçmez. Boş dünyalar (hızlı kip): sabit oynaklık 0/200 dünya,
  0/5.800 satır (≈ 173 sn); oynaklık kümelenmeli 1/200 dünya (0,005), 1/5.800 satır (≈ 320 sn) — ikisi de ≤ q.
- **Açık madde (sahibe):** DSR'nin `sr`'ı hâlâ giriş günü serisinden (mühürlü metin "pozisyonsuz gün 0" der; günlük
  piyasa değerlemesi bu metne daha yakındır). Değiştirilmedi çünkü DSR'yi gevşetebilir; karar S2'ye ya da sonraki bir
  düzeltme mührüne.
- **Mühür:** değişen `STATS_V1` (U3 okuması), `THRESHOLD_DIRECTIONS_V1` (`edge_purge` yönleri çıktı, `matrix_returns`/
  `matrix_fallback` `fixed`), `CONFIG_EPOCHS_V1` (yalnız `covered_until`), `T_SEAL_MS`, `REGISTRY_SHA`; diğer beş kayıt
  DEĞİŞMEDİ. Değerler §8.2'de.

**P3e uygulama notu (2026-10-13; keşif katmanı `explore.py` (S4x), lider tablosunun KEŞİF bölümü, `engine-query explore`;
S1 mührü DEĞİŞMEDİ).** Yalnız PAPER araştırma kodu; canlı kod/config/worker, gece birimi ve dağıtım değişmedi (S4x'in gece
bağlantısı ve bütçesi P3g); gerçek veri koşusu yok (yalnız sentetik dünyalar); deneme defterine yazım yok. Mühürlü kayıtlar
(`LIB_V1.explore`, `T_SEAL_MS`, `REGISTRY_SHA`, …) tek kaynaktır ve dokunulmadı; aşağıdaki okunuşlar mühürde yazılmayan
uygulama ayrıntılarıdır (sıkılaştıran ya da nötr).

- **Modüller.** `research_engine/explore.py`: seri planı, mühürlü/arşiv-doğrulanmış girdi, artımlı ileri koşu, dosyalar,
  `fwd_reads` (Kapı A'ya giden tek yol), `leaderboard_lines` (KEŞİF bölümü), `digest_lines` (özet), `query_lines`
  (`engine-query explore [--gold] [--variant <id>]`; `query.py` + `cli_v3` ayrıştırıcısı). `synthetic.explore_world`
  (tutarlı 1h → 4h/1d, 1h'nin 5m alt barları, 1d ön geçmişi, fonlama). Yeniden kullanılan: `kernel.run_lockstep` /
  `make_family` / `funding_of` / `cost_trade` / `gate_excluded`, `stats.mtm_allocation` (U3), `wf.exit_ref`; yeni istatistik
  yok. Yalıtım sözleşmesine `explore.py` → `strategy_lib` (`BookEntry`) izni ve ağsız modül listesi eklendi.
- **Seriler (kayıttan, 3.745).** 45 STANDARD (alias'sız) × 40 + 13 K × 40 + 35 O × 40 + J 5 (`PORT_XSEC`) + I 2
  (`PAIR_BTCETH`) + G 6 × 3 altın enstrümanı = `registry.explore_series()`; plan bu sayıyı tutmazsa ya da giriş evreni 40
  farklı coin değilse kapalı hata (`ExploreRefused`). 40 coin'in KİMLİĞİ mühürde yok (yalnız sayı): çalışma anındaki giriş
  evrenidir (`universe.entry_universe`, config `entry_universe.symbols`; P23'ün 23 coin'i içinde). UNSPECIFIED 13 varyantın
  25 serisi (I 2, J 5, G 18) planda sayılır, çalıştırılmaz (`UNSPECIFIED_PARAM`). Bütün çalıştırılabilir varyantların
  penceresi sonludur (değilse kapalı hata: artımlı = baştan ancak böyle kanıtlanır).
- **Veri ve zaman yolculuğu.** Yalnız mühürlü parçalar (`provider.SealedReader`) ve `_src == archive` satırları — ısınma,
  ileri barlar ve fonlama için aynı kural. Açılışı `T_SEAL_MS`'den önceki satırlar YALNIZ göstergeyi ısıtır: yükleme başı
  sabittir (`warm_start` = T_SEAL − ⌈(en uzun mühürlü pencere + 2) × 1,5⌉ bar; geceye bağlı değil) ve çekirdeğe yalnız
  karar anından önceki pencere + 2 bar verilir. İlk karar, açılışı ≥ T_SEAL olan ilk barın kapanışındadır (`start_ms` =
  T_SEAL + sinyal dilimi; çekirdeğin `start_ms`'i ve vekil `_Fwd` ayrı ayrı denetler). İleri satırlar ilk doğrulanmamış
  (`rest`, `archive_unverified`, `seed`) ya da kapanmamış satırda kesilir; serinin cephesi okuduğu bütün dizilerin (görünüm
  dilimleri, 1h yol barları, BTC 1d/4h) en küçük cephesidir; fonlama cepheden sonra kesilir. `DATA_MOVING` coin'in serileri
  o gece atlanır (durum değişmez); fiyat adımı (tick) verilmezse seri çalıştırılmaz (`NO_TICK`; spread maliyeti
  ölçülemez).
- **Artımlı = baştan (bayt-özdeş).** Durumun "düz karar anı" (`checkpoint_ms`): açık pozisyon varsa onun karar anı (= giriş
  barının açılışı), yoksa cephedeki son sinyal barının kapanışı — orada çekirdekte pozisyon da bekleyen eylem de yoktur;
  çekirdeğin kalan durumu önceki girişlerin geçmişi (`ctx.history`) ve kullanılmış `one_entry_per_signal` sinyalleridir.
  Vekil geçmişi JSON biçiminde verir (iki yolda da: önceki koşuların girişleri `jsonl`'dan tembel yüklenir, bu koşununkiler
  çevrilir; yalnız okuyan varyant — T2/M2 ve örtüleri, Formasyon — yükler), kullanılmış sinyalleri düşürür (çekirdeğin
  kendi denetimiyle aynı sonuç) ve karar anı öncesinde niyet döndürmez. Fonlamadan türeyen alanlar (`funding_of`,
  `FUNDING_GAP`/`FUNDING_EXIT_BAR`, üç maliyetle R/net, MTM) `FundingSeries`'in yerel aralığı yüzünden pencerenin iki
  yanındaki uzlaşmalara bağlıdır: işlem, son bacağından sonra ≥ 4 uzlaşma (`FUNDING_LOCAL_SPAN` + 1) gelene kadar
  `unsettled` kalır ve her gece aynı fonksiyonlarla yeniden türetilir; sonra `<COIN>.jsonl`'a eklenir (yalnız eklenir).
  Gün satırı (`D`, üç maliyetle günlük %) gün kesinleşince eklenir (gün < min(cephe günü, karar anı günü, kesinleşmemiş/açık
  işlemlerin giriş günü)). Satırlar zaman anahtarına göre sıralıdır ve kesinleşen küme her zaman önektir → gece gece ekleme
  baştan yazımla aynı baytlar; toplamlar (n, ΣR, özsermaye, tepe, düşüş, ≥ +%1 gün) aynı sırayla birikir. Önceki cephenin
  verisi (parmak izi: diziler + fonlama + tick), sürüm/mühür/çekirdek sürümü ya da `jsonl` boyu tutmazsa seri BAŞTAN
  kurulur (sayılır).
- **Çıktı (ana belge §6.8).** Seri başına n, ortalama net R (base/ADVERSE/STRESS), toplam R, günlük % (%0,5 risk, 200 USDT
  taban; `stats.mtm_allocation` dağılımı her maliyetle), P(gün ≥ +%1), en büyük düşüş, mühürden bu yana gün (⌊(cephe −
  T_SEAL) / 1 gün⌋), açık pozisyon; `source = EXPLORATION_FWD`. Yalnız kapanmış işlemler; kapı dışı işaretli işlemler
  (`kernel.gate_excluded`) HİÇBİR keşif istatistiğine girmez (sayısı gösterilir). Lider tablosu/özet: "N seri arasından en
  iyi 5 — seçim yanlılığı, kanıt değil" (ADVERSE toplam R'ye göre), medyan serinin sonucu (≥ 1 işlemli serilerin ADVERSE
  toplam R'si), altın ayrı. Metin hüküm kelimesi üretmez (`verdict_word_violations`; Türkçe ve Latin küçük harfle; çizilen
  her metin yazılmadan denetlenir, ihlalde hata).
- **Kapı yolu (§3.7 (6)).** Keşif Kapı A/B'ye doğrudan girmez: kapı/istatistik/defter modülleri `explore`'u import etmez,
  `explore` deneme defterini import etmez ve kapıyı çağırmaz (AST testleri). Tek okuyucu `fwd_reads` → `stats.gate_a_look(
  fwd=…)`: satırın KENDİ serileri (COIN: tek seri; havuz/küme: P23/küme coin'lerinin birleşimi; `POOLED_OWN`: defterin
  kendi sembolleri — girdi; `COIN_FIT`: mühürde donmuş seçim — girdi, ızgara dışı seçim kapalı hata), yalnız kapı-içi
  kapanmış işlemler: `n`, `mean_adverse`, `days` (üyelerin en küçüğü), `intraday`; bir üyenin durumu yoksa satır yok
  (kapı `FWD_MISSING`, kapalı).
- **Ölçüm (bu sandbox, tek süreç; `test_scaled_world_nightly_step_within_the_s4x_budget`, `slow`).** 40 coin × 3.720
  çalıştırılabilir seri (`synthetic.explore_world`, 1h/4h/1d/5m + fonlama): mühürden bu yana 7 günlük birikmiş
  geri dönüş ≈ 183 sn (16.630 işlem); ardından bir gecelik adım (1 yeni gün) ≈ 48 sn (3.022 işlem, 210.089 sinyal
  barı; `UNSPECIFIED_PARAM` 25 + `OK` 3.720) — S4x bütçesi ≤ 10 dk (§5.2) içinde; geri dönüş ≈ 26 sn/gün → 10 dk'da
  ≈ 23 günlük birikim. En pahalısı Box ailesi (5m; 15 varyant × 40). VPS çarpanı ve `VmHWM` P3g'de.
- **Testler.** `tests/test_research_engine_explore_p3e.py`: 3.745 plan (kayıt, formül, 25 çalıştırılmayan, evren ≠ 40 kapalı
  hata); artımlı = baştan bayt-özdeş (iki karışık dünya, rastgele geceler, 3 günlük fonlama arşivi gecikmesi; T2/M2 +
  örtüleri ve Formasyon ile geçmiş yolu; aynı gece yeniden = `UNCHANGED`); geçmiş veri/sürüm/yarım ekleme → baştan = baştan
  hesap; zaman yolculuğu özellik testi (6 tohum: kural hiç T_SEAL öncesi açılan barın kapanışında karar vermez, işlem/gün
  satırı mühür öncesine düşmez, pencere dışı mühür öncesi barları bozmak ve cepheden sonrası hiçbir şeyi değiştirmez);
  yükleyici kuralları ve `ResearchStore`'dan okuma (`rest` kuyruğu cepheyi keser), `DATA_MOVING` ve `NO_TICK`; kapı dışı
  işaretler (FUNDING_GAP, PATH_1H_GAP; NaN mark); tek kapı yolu; hüküm kelimesi yok (uç değerli seriler dahil, pozitif
  kontrol); `engine-query explore` ≤ 150 satır / 4 KB, salt-okunur, CLI.
- **Açık kalanlar.** (1) *[2026-10-13: S1b ile çözüldü — mark = markpx 1h; aşağıdaki S1b notu.]* **Arşiv fonlama
  satırlarında mark yok** (`parse_funding_zip` mark = NaN; REST satırlarının markı arşiv
  gelince NaN'lı arşiv satırıyla yer değiştirir): çekirdek bunu okunamaz sayar → bir uzlaşmayı kapsayan her işlem
  `FUNDING_GAP` → gerçek veride keşif istatistiği ve ileri okuma neredeyse boş kalır (kapalı hata; WF/kapı için de aynı).
  Mark kaynağı (ör. uzlaşma saatindeki `markpx_1h` açılışı) bir okuma kararıdır — P3g'den önce sahibe/koordinatöre.
  (2) Fonlama arşivi aylıktır: ileri işlemler ≤ ~1 ay `unsettled` (kapı dışı) kalır; durum dosyası bunları taşır.
  (3) *[2026-10-13: S1b `ENTRY_UNIVERSE_V1` ile donduruldu.]* 40 coin'in kimliği S1'de dondurulmamış; config değişirse seriler değişir (sayı aynı) — sonraki mühürde dondurulması
  önerilir. (4) `COIN_FIT` donmuş seçimi ve `POOLED_OWN` sembol kümeleri `fwd_reads`'e girdi (P3g/P4a kurar).
  (5) Gece bağlantısı, S4x bütçesi ve `DATA_STALE`/A-B KAPALI kuralı, `EXPLORE_SEEN` (sonraki LIB mühründe), `jsonl`'ın
  derin sha denetimi (gece yalnız boy) P3g'de. (6) Dağıtımda T_SEAL'dan bu yana biriken geri dönüş ilk gecenin 10 dk'sını
  aşabilir: süre dolunca coin'ler arasında durulur, kalan sonraki gece sürer (artımlı tasarım güvenli).

**Değişiklik 2026-10-13 — Mühür S1b: fonlama mark kaynağı ve dondurulmuş 40 coin'lik giriş evreni (sahip kararı; S1
DOKUNULMADI).** Sahip kararı (koordinatör iletisi, 2026-10-13): mühürlü `registry.py` değiştirilmez; P3e'nin iki açık kalanı
(1: arşiv fonlama satırlarında mark yok; 3: 40 coin'in kimliği mühürde yok) AYRI ADLI, AYRI DOSYALI yeni bir mühürle
sabitlenir. S1'in bütün değerleri (`REGISTRY_SHA`, `T_SEAL_MS`, sekiz sha; `registry.py`, `prior_trials.json`,
`config_epochs.json`) 43a0bf0 ile bayt-aynıdır (testli).

- **`research_engine/seal_s1b.py`.** `FUNDING_MARK_V1`: çekirdeğin fonlama mark'ı = deponun `markpx_1h` serisinden,
  uzlaşma anında (nominal dakika) BAŞLAYAN 1h barının AÇILIŞI; yoksa o anda BİTEN barın KAPANIŞI; ikisi de yoksa uzlaşma
  okunamaz → `FUNDING_GAP`; uzlaşmadan sonraki fiyat hiç kullanılmaz; fonlama satırının kendi mark'ı (REST) yalnız
  denetim — göreli fark > %0,5 → `FUNDING_MARK_MISMATCH` (bilgi; kapı dışı listesine girmez), tutar markpx'le. WF (P3d),
  keşif (P3e) ve parite (a) aynı yolu kullanır (`kernel.settlement_marks` → `funding_series` / `apply_funding`).
  `ENTRY_UNIVERSE_V1`: config.yaml `entry_universe.symbols`'ın 40 coin'i, config sırasıyla; çalışma anındaki evren bu
  kümeyle sırasız aynı değilse keşif ve WF çalışmaz, eksik/fazla/yinelenen coin'ler raporlanır (`universe.entry_universe_diff`,
  `wf.require_entry_universe`, keşifte `_run.json` + lider tablosunda "ÇALIŞMADI"). `S1B_DIRECTIONS_V1`: asla-gevşetme
  yönleri — iki okumanın bütün tanım alanları `fixed` (S1'in `registry.looser_than` makinesiyle; registry.py'ye
  dokunmadan). Kanonik bayt S1'inkiyle aynı (`registry.canonical_bytes`).
- **Değerler.** `FUNDING_MARK_V1` `165057d63cf6818464d773916761a6b4a143883a20da1221c15b12de5a80a5dd`;
  `ENTRY_UNIVERSE_V1` `f1d5744789fa585e6bc5a059168c9bdd1bb5138902dbacbc90d81139362ee3f6`; `S1B_DIRECTIONS_V1`
  `106f15397c60e5f1ebb31d64b0bcea503fbf9c19349cd23e286374dd3f221c29`; `S1B_SEAL_MS` `1791908100000` (2026-10-13T16:15:00Z; mühür
  commit'inin işleyen zamanından birkaç dakika sonraki tam dakika, T_SEAL_MS kuralı, testte git'ten); `S1B_SHA`
  `b3254fe69f72c48dda1ec372e6230d8b4a8106bac61043c4f5b88b4db9d08c31` = {üç sha, `S1B_SEAL_MS`, genişlettiği S1 `REGISTRY_SHA` 3e700fa0…} kanonik sha'sı.
- **Kapalı hata.** `seal_s1b.PINNED` mühür anının sabitleridir; dosya yoksa, içerik değişip sabitler yenilenmediyse, S1
  değiştiyse ya da mühürlü okuma çekirdeğin uyguladığı kuralla aynı değilse çekirdek (`kernel.SealError`), keşif
  (`REFUSED`, açık neden) ve WF (`EntryUniverseRefused`) çalışmaz. Önceki "mühür bekliyor" bayrakları
  (`kernel.FUNDING_MARK_SEALED`, `universe.ENTRY_UNIVERSE_SEALED`) kaldırıldı; değerler S1b'den okunur. Keşif durumu S1b
  sha'sını taşır (S1b değişirse seri baştan kurulur).
- **Etki (sentetik).** Satır mark'ı gerçek olan P3b/P3d dünyalarında sonuçlar bayt-aynı (sentetik markpx = satır mark'ı).
  Arşiv gibi NaN satır mark'lı keşif dünyasında (6 coin × 93 varyant = 558 seri, 7 gün) kapı dışı işlem 845/2.668'den
  (%31,7; hiçbiri kesinleşmiyordu) 0/2.668'e indi. Ölçekli dünya (40 coin, 3.720 seri): gece adımı ≈ 47 sn.
- **Testler.** `tests/test_research_engine_seal_s1b.py` (sabit sha'lar; `S1B_SEAL_MS` ≥ seal_s1b.py'yi değiştiren son
  commit ve ≤ 24 saat; her alan değişikliği sha'yı değiştirir; yönler her tanım alanını kapsar ve her değişikliği yakalar;
  S1 43a0bf0 ile bayt-aynı; eksik/tutmayan mühürde çekirdek/keşif/WF açık nedenle durur); `test_research_engine_explore_p3e.py`
  (markpx açılışı → biten barın kapanışı → NaN, %0,5 denetimi, ileriye bakmama özellik testi, NaN satır mark'ı + markpx =
  satır mark'ıyla aynı sonuç, markpx deliği, evren farkı); P3b testleri `SeriesData.markpx_1h` verir.

**P3f uygulama notu (2026-10-13; dersler `lessons.py`, `library/derived_specs.jsonl`, aylık `LIB_v1.<AAAA-AA>`,
`engine-query lessons`; okumalar AYRI ADLI S1c mührüyle — S1 ve S1b DOKUNULMADI).** Yalnız PAPER araştırma kodu; canlı
kod/config/worker, gece birimi ve dağıtım değişmedi (S6'nın gece bağlantısı ve bütçesi P3g); gerçek veri koşusu yok (yalnız
sentetik dünyalar). `registry.py`, `seal_s1b.py`, `prior_trials.json`, `config_epochs.json` 9132798 ile bayt-aynı (testli).

- **Modüller.** `research_engine/lessons.py` (kanıt, hücreler, istatistik, bakış planı, BH, ileri doğrulama, ders deposu,
  dönüşüm → spec, canlı kural denetimi, tekilleştirme, aylık mühür, §5.10, defter × kod görünümü, `query_lines`);
  `research_engine/seal_s1c.py` (`LESSON_READINGS_V1`, `S1C_DIRECTIONS_V1`, `S1C_SEAL_MS`, `PINNED`, `check`);
  `query.py` + `cli_v3` ayrıştırıcısı (`engine-query lessons [--book B] [--like metin]`); `rawconfig.NEEDS`'e
  `strategy_paper.extra[].symbols` (yalnız T1 canlı kural denetimi). Yeniden kullanılan: `stats.p_day` (tek istatistik,
  B6 tohumu `row_id|look_no`), `stats.bh_qvalues`, `stats.choice_stream` (CI), `trials.TrialsLedger` (bütün sayım),
  `learn/lesson_store.build_lesson` + `transition` (lesson_v2, izinli geçişler; motor ilk gerçek çağıran),
  `daily_target.wilson`; HierarchicalRate formülü (s + α·μ_ebeveyn)/(n + α), α = 10. Yalıtım sözleşmesine
  `lessons.py` → `learn.lesson_store` izni ve ağsız modül listesine `lessons`, `seal_s1c` eklendi.
- **Neden ayrı mühür (S1c).** S1 `LESSON_REGISTRY_V1` eşikleri ve tanımları taşır ama uygulamayı belirleyen birkaç yer açıktı:
  çok eşikli birikimde (Box 14 günde 980 işlem: ilk gece 30/60/120/240 birlikte geçilir) bakış sırası, ≥ 10 gün şartının
  yüksek frekanslı defterlerde eşiklerle ilişkisi, "bir sonraki bakış" ile "≥ 3 aylık katman" şartının birlikte okunuşu
  (240'tan sonra bakış yok; katman şartı ≥ 3 ay ister), dönüşümün hangi düzeyde ve hangi kapsamla uygulanacağı, spec
  biçimi, canlı kural denetiminin anahtarları ve §5.10'un bölünmüş rejimi. Bunlar sonuca etki eden tanımlar olduğu için
  gerçek veriden önce mühürlendi (sahip kararı: S1 dosyası düzenlenmez; yeni okumalar ayrı adlı mühürle). Değerler
  `lessons.py`'nin girdisidir (metin değil). Okunuşlar (hepsi sıkılaştıran ya da nötr):
  (1) *Kanıt*: atıf satırı; hata/dönemsiz (TRANSITION, kapsam dışı)/net_r'siz/eski attribution_v1-cfgrid_v1 sha'lı satır
  girmez (sayılır); REAL = ölçülmüş net R, CF = cf_aux_v1 hücre − E_ACTUAL (aynı işlem, `rank_src = cf_aux_v1`); gün =
  giriş UTC günü; S2 bekleyen işi varken bakış yok. (2) *Hücre*: (düzey, defter, config dönemi, kohort) + düzeyin
  boyutları; uygun her düzey ayrı hücre (aile büyür → BH sıkılaşır); kova = situation_v1 (satırın `situation_entry`'si,
  yoksa S2'nin `regime_fit.bucket`'ı). (3) *Çift*: FİLTRE (REAL, değer −net R) + 15 IZGARA hücresi (CF; SKIP FİLTRE'nin CF
  ikizi, boyut ekseni öneri değil); MALİYET yalnız rapor (hücrenin ortalama maliyet R'si), AYRIM v1'de hesaplanmaz.
  (4) *Bakış*: çiftin n'i n_k'yı geçmiş, çift uygun, k defterde yok → bakış; gece çift başına EN ÇOK bir (en küçük bekleyen
  k; birikmiş eşikler sonraki gecelere); örneklem o gecenin as_of'una kadarki bütün kanıt; anlamlı = etki > 0 VE p_day ≤
  α_k VE q_BH ≤ 0,10; aile = o gecenin bütün bakışları (keşif + doğrulama), üst sınır 2.000 (n azalan, cell_id, pair_id).
  (5) *İleri doğrulama*: TEK atış; T_k ayından sonraki 3. TAM UTC ayının bitişinden sonraki ilk gece, örneklem (T_k, ay
  sonu] içinde kapanan AYNI hücrenin kanıtı (uygun değilse pencere bir ay uzar — yalnız büyüklüğe bağlı); α = α_{k+1}
  (k = 4 → son alfa); geçer = ortalama > 0, p ≤ α ve ≥ 3 aylık katmanın ≥ 2/3'ünde katman ortalaması > 0; ortalama ≤ 0 →
  RETIRED, aksi → REJECTED. (6) *Durum*: ana belge §5.8 daha sıkı: VALIDATED_POLICY_CANDIDATE yalnız kural değişikliği
  gerektirmeyen (canlıda zaten olan) derse; kural değişikliği isteyen ders FWD_PASS ile RESEARCH_HYPOTHESIS kalır, spec'i
  kuyruğa girer (Kapı B P4'te). Gece APPLIED_BOUNDED yazamaz. Geçersizleşme (attribution/cfgrid sha'sı ya da son keşif
  bakışının kanıt özeti değişti) → `RETIRE` satırı, ders RETIRED, kuşak + 1 → yeni `LESSON_LOOK`. (7) *Dönüşüm*: FİLTRE
  L2 → T1, L3 → T4 (POOLED_OWN); IZGARA S_ATR* → T2(k), X_* → T3(e) (L2/L4 COIN:c, diğerleri POOLED_OWN; L1+ taktik, L3/L4
  kova koşulu spec'te); E_*/M_* ve L0/L1/L4 FİLTRE'si dönüşümsüz; taban = canlı defterin LIB kopyası (T2, M2, BOX, D4, C4,
  FM; kopyası olmayan defterde ders var, spec yok); yalnız POLICY/UNTAGGED kohortu spec üretir. Spec örtü biçimidir (family
  O; `module_src_sha` = sha256(taban modül sha'sı | LIB_V1.transforms sha | okumalar sha)). v1'de hiçbir cfgrid hücresi/kova
  bir O faktörüyle aynı tanımlı değil (X_TRAIL2ATR girişten, X_TRAIL2 +1R'den; X_NONE_TIME ufuk sonu, X_TIME max_hold/2;
  kova ≠ F_*) → eşdeğerlik tablosu boş; tekilleştirme spec_sha ile LIB_v1 (örtüler, kopyalar), türeyen spec'ler ve deneme
  defterinin LIB ailesi üzerinden. (8) *Canlı kural*: ham config + bildirilmiş kod sabiti — T1 c defterin ham sembol
  listesinde yoksa (liste yoksa Box/T2/M2'de `entry_universe`); T2 canlı ATR çarpanı = k (D4: `donchian_trend.STOP_ATR` =
  2, testte koda karşı); T3 Box `exit_kind = r_multiple` ve `exit_r` = hedef; T4 v1 config'inde yok. Config okunamazsa
  doğrulama bakışı ertelenir (hüküm canlı kural denetimi ister). (9) *Aylık mühür*: 1'inden sonraki ilk gece (ay için
  manifest yoksa) kuyruktan ≤ 20 (q artan, |Δ| azalan, spec_sha) → `SPEC` satırı (`LIB_v1.<AAAA-AA>`, N_raw_cum + 1) +
  `SEALED` satırı + `library/LIB_v1.<AAAA-AA>.json` (bir kez; yeniden çalışma aylık tavanı korur); sığmayan kuyrukta.
  (10) *§5.10*: dönem başına ayrı; aynı min_stop rejiminin dönemleri birleşir (sonraki dönemin — TRANSITION dahil —
  `opened_by`'ı `learning_mode.books.b1_box_fade.min_stop_pct`'i içermiyorsa): E2026-09-28T2120Z + E2026-09-29T2243Z bir
  rejim, E2026-10-05T0455Z ayrı; ham config min_stop_pct ≥ 0,5 → `APPLIED_DECISION_MONITOR`, aday yok.
- **S1c değerleri.** `LESSON_READINGS_V1` `b1d069233fe71472a45912e05f8a1bb2844a2603846b29b2c75d9cb7e01a008f`; `S1C_DIRECTIONS_V1`
  `01ef6b0f737b27598e5756a86258ba128042c33db3924a15e3a42d285d03ef3f` (bütün tanım alanları `fixed`, ileri doğrulamanın tam ay
  sayısı `min`); `S1C_SEAL_MS` `1791914160000` (2026-10-13T17:56:00Z; mühür commit'inin işleyen zamanından sonraki tam dakika, testte git'ten);
  `S1C_SHA` `277922ca4c84b29017e93b05e5e3947bce57b5e890ef8413828f03917a52de95` = {iki sha, `S1C_SEAL_MS`, S1 `REGISTRY_SHA` 3e700fa0…,
  S1b `S1B_SHA` b3254fe6…} kanonik sha'sı. Tutmazsa (`seal_s1c.check`) ders katmanı çalışmaz (`LessonsRefused`).
- **Durumun tek kaynağı deneme defteridir.** Her bakış bir `LESSON_LOOK` satırıdır (`row_id` `LESSON|<çift>|g<kuşak>`,
  doğrulama `LESSON_FWD|<çift>|g<kuşak>|h<k>`; `params` istatistikleri, hükmü, hipotez hash'ini, spec önerisini taşır),
  geçersizleşme bir `RETIRE` satırıdır. Ders deposu (`lessons/lessons.jsonl`, yalnız eklenir, hash zincirli; olaylar
  CREATE/LOOK/TRANSITION/UPDATE/SPEC) defterin satırlarından belirlenimci türetilir: silinirse aynı baytlarla yeniden kurulur
  (testli); aynı gece yeniden çalışmak yeni bakış/deneme üretmez. `derived_specs.jsonl` (PROPOSED/LINKED/EXCLUDED_LIVE/
  SEALED/SEAL_LINKED) de yalnız eklenir ve zincirlidir; kırık zincire eklenmez (`LessonStoreBroken`).
- **Kabul → test** (`tests/test_research_engine_lessons_p3f.py`, 22 test; `tests/test_research_engine_seal_s1c.py`, 4):
  *bakış yalnız eşiklerde* — uygun olmayan (5 gün) 40 işlemde bakış yok, 10. günde bakış 1, 220 işlemlik sıçramada 2/3/4
  birer gece arayla, 4'ten sonra hiç; ara gecede defter baytı değişmez; rastgele gelişlerde (3 tohum) her bakış n ≥ n_k,
  sırayla, gece başına bir. *Alfa bölünmesi* — α_k ve toplam 0,05 mühürden; p = 0,012 bakış 1/2'de değil 3'te anlamlı;
  doğrulama α_{k+1} (son alfa sınırı). *BH ailesi* — üç defter, FİLTRE + IZGARA: q = o gecenin bütün bakışlarının BH'si;
  üst sınır n azalan/cell_id sırasıyla, taşan sonraki gecelere (kaybolmaz, sayılır); doğrulama bakışları aileye dahil.
  *İleri doğrulama yalnız T_k sonrası* — T_k'da hipotez; Aralık ve 28 Şubat gecelerinde bakış yok; 1 Mart'ta doğrulama
  n'i yalnız (T_k, 1 Mart) işlemleri, 4 katman; pencere sonrası işlemler sonucu değiştirmez; işaret dönünce RETIRED,
  katman tutarsızsa REJECTED, seyrek örneklemde pencere bir ay uzar. *Geçişler* — lesson_v2, durum geçmişi, atlamalı
  geçiş `ValueError`; cfgrid sha değişince 5 ders RETIRED (INVALIDATED) ve kuşak 1 baştan bakar (N_lesson artar); tek
  işlemin değeri değişince yalnız o işlemi içeren çiftler. *Runner APPLIED_BOUNDED yazamaz* — genel durum makinesi VPC →
  APPLIED_BOUNDED'a izin verse de `runner_transition` ve depo yazarı reddeder (dosya değişmez); kaynakta ad yalnız sabit
  açılımında, dize sabiti yok; uçtan uca dünyada defter/depo/spec dosyalarında hiç geçmez. *Dönüşüm → spec + O ile
  tekilleştirme* — eşleme tablosu, spec kimliği (taban kodu kimlikte, kohort değil), v1'de hiçbir dönüşüm 452 LIB
  kopya/örtü satırıyla aynı değil; iki dersten aynı spec → PROPOSED + LINKED; tanımı aynı örtü (eşdeğerlik verilmiş gibi)
  → `LIB_v1:O_BOX_F_VOL@POOLED_OWN`'a bağlanır, yeni deneme yok; defterin LIB ailesindeki aynı spec → bağlanır; aylık mühür
  20 + 5 taşan, sıralama, N_raw + 20, manifest sha'sı, ay içinde ikinci mühür yok, çökme benzetiminde tavan korunur, taşanlar
  sonraki ay. *Canlı kural dışlama* — matris (D4 k = 2 kod sabiti, T2 atr_mult, Box r_multiple, T1 sembol listesi/evren,
  T4, okunamayan config); uçtan uca D4 S_ATR200 dersi EXCLUDED_LIVE + VALIDATED_POLICY_CANDIDATE, S_ATR150 spec'i mühürlenir,
  canlı kural hiçbir zaman SPEC denemesi olmaz; config yokken doğrulama ertelenir. *§5.10* — sentetik eski rejim iki döneme
  bölünmüş (40 + 32 dar, 100 + 76 %0,5–1): dönem başına ayrı ve birleşik rejimde 72 / −0,56R ve 176 / +0,17R,
  TIGHT_STOP_COST_MULTIPLIER baskın → REPRODUCED; işaret ters → NOT_REPRODUCED; yeni rejim izlenir
  (`APPLIED_DECISION_MONITOR`, min_stop 0,32 → NOT_APPLIED); gece adımında spec/deneme yok; hücreler dönem sınırını aşmaz.
  Ek: bütün defterler (Box, T2, ana bot) ders ve defter × kod görünümü alır (Box WRONG_DIRECTION → en iyi EX_ANTE S_ATR150);
  eski sürüm/dönemsiz satırlar sayılarak dışlanır; S2 bekleyen işi bakışı durdurur; kırık depo zinciri eklemeyi durdurur;
  `engine-query lessons` ≤ 150 satır / ≤ 4 KB, salt-okunur (dosya baytları değişmez), CLI config yüklemez. S1c: sabit
  sha'lar, S1/S1b bayt-aynı, alan değişikliği → sha, `S1C_SEAL_MS` ≥ commit zamanı, yönler her alanı kapsar ve yakalar.
- **Ölçüm (sentetik, bu sandbox, paralel test yükü altında).** 6 defter × 40 coin × 9 kova, 30.000 işlem (her biri 15
  IZGARA hücreli), 2.466 hücre: ilk gece 2.000 bakış (üst sınır; 2.912 taşan) ≈ 22 sn, ertesi gece ≈ 24 sn. Tepe bellek ve
  gece bütçesi P3g'de (sürecin 433 MB'lık tepesi test satırlarının kendisi dahildir; gece yolu atıfı akışla okur).
- **Açık kalanlar.** (1) Gece bağlantısı (S6: `run_lessons`'ın `raw` = `rawconfig.read_raw_config`, `module_src_sha` =
  `kernel.module_src_shas()`, `versions` = attribution/cfgrid sha'ları), süre bütçesi, `_last_run.json`'un özet/digest'e
  girmesi ve `TRIALS_CHAIN_BROKEN`/`LessonStoreBroken` → S6 kapalı P3g'de. (2) VALIDATED_POLICY_CANDIDATE'e kural değişikliği
  gerektiren ders için Kapı B yolu ve `linked_promo_id` P4a'da; APPLIED_BOUNDED yalnız sahibin `engine-query --approve` yolu
  (P4/P7; depo okuyucusu o yol eklenene kadar yasak durumu yazdırmaz). (3) AYRIM (WF OOS AUC) v1'de hesaplanmıyor; MALİYET
  yalnız rapor. (4) P2b'nin `regime_fit` önceli (JOURNAL_PRIOR) → LESSON_PRIOR geçişi atıfı değiştirir (cfgrid sürümü); P3g
  ile birlikte ele alınmalı. (5) Depo tek dosya (`SegmentArchive` dönüşümü yok); büyüklük P3g ölçümünde. (6) Türeyen spec'lerin
  çekirdekte çalıştırılması (dönüşüm yürütücüsü, `module_src_sha`'nın gerçek kodu) P4 öncesi kod PR'ı; S1c eşdeğerlik tablosu
  ancak tanımı birebir aynı bir faktör eklenirse dolar. (7) Geçersizleşme denetimi her gece her çiftin özetini yeniden
  hesaplar (O(çift × n)); ölçekli gerçek veride süre P3g'de ölçülür.

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

### 8.6 S1d — LIB_AG_V1 (2026-10-13; ANTİGRAVİTY dökümlerinden mekanik varyantlar; AYRI ADLI mühür — S1, S1b, S1c DOKUNULMADI)

**Sahip kararı (koordinatör iletisi, 2026-10-13).** Sahibin ANTİGRAVİTY klasöründeki bir takdir tüccarının (ICT tarzı:
öldürme bölgeleri, öğle süpürmesi, 10:00 "TENM" açılış modeli, MSS/FVG, ret blokları, MSNR) dökümlerinden 5 taktik ve 3
örtü MEKANİK varyantlara çevrilir; ayrı adlı yeni modülde mühürlenir (`LIB_AG_V1`; S1 `registry.py`, `seal_s1b.py`,
`seal_s1c.py`, `prior_trials.json` bayt-aynı, testli); LIB_V1 varyantlarıyla AYNI kapılardan geçer (walk-forward, maliyet
senaryoları, BH/DSR, CSCV, plasebo), deneme defterinde ve çoklu sınamada sayılır; bu mührün evrenine XAUUSDT eklenir.
Yalnız PAPER araştırma kodu; canlı kod/config/worker, gece birimi, dağıtım ve P3g dosyaları değişmedi; gerçek veri koşusu
yok (yalnız sentetik). Eşik gevşetilmedi, yeni eşik yok.

- **Modüller.** `strategy_lib/ag_clock.py` (New York saati, oturumlar), `ag_common.py` (ortak taban, `AgEntryRef`,
  salınım/FVG, önbellek), aileler `ag_p1_lunch.py`, `ag_p2_tenm.py`, `ag_p3_mss.py`, `ag_p4_rb.py`, `ag_p5_msnr.py`, örtüler
  `ag_overlay.py`; `catalog.py`'ye EK (`AG_*`, `make_ag`; LIB_V1'in `IMPLEMENTED` 93'ü değişmedi). Motor tarafı
  `research_engine/seal_s1d.py` (mühür) ve `research_engine/lib_ag.py` (EKLEME API'si). Çekirdeğe YALNIZ EKLEME
  (`kernel.limit_through`, tek-bar limit dalı, `_entry_gate`'in sonuna iki kapı; bu alanları taşımayan niyet bayt-aynı
  işlenir): sözleşme dosyası (`contract.py`) ve göstergeler değişmedi → LIB_V1'in 93 `module_src_sha`/`spec_sha`'sı aynı.
  Deneme defterine `LIB_AG_v<n>` sürüm adı (`trials.LIB_VERSION_RE`; ayrı tekilleştirme ailesi). Veri birimine XAUUSDT için
  `markpx_1h` (`universe.AG_GOLD_FUTURES`, `ROLE_AG`).
- **Ortak kurallar (mühürlü `params.common`; her varyantın `spec_sha`'sına girer).** Saat America/New_York, barın AÇILIŞ
  zamanından; yaz saati ABD 2007 kuralının saf hesabı (paket saflık testi `zoneinfo`'yu yasaklar — tz veritabanını dosyadan
  okur), zoneinfo ile 2019–2030 her saat ve her geçiş gününde her 5 dakika eşit (test). Oturumlar [başlangıç, bitiş):
  ASIA 20:00–24:00, LONDON 02:00–05:00, NYAM 09:30–11:00, LUNCH 12:00–13:00, PM 13:30–16:00; KZ = LONDON ∪ NYAM ∪ LUNCH ∪ PM.
  Salınım = 3 barlık fraktal (orta barın yükseği iki komşusununkinden büyük; dip simetrik), sağ komşu KAPANINCA doğrulanır.
  ATR14 = Wilder, kurulum diliminde. Tampon = yapısal stopun %0,05 ötesi. Stop mesafesi DOLUM fiyatının < %0,08'i ya da >
  %1,2'si → işlem yok. Sonuçlar R'de, çekirdeğin büyüklüğü/ücreti/kayma senaryolarıyla; tek açık pozisyon (çekirdek); varyant
  × sembol × New York günü başına en çok bir işlem; her başvuru seviyesi yalnız kapanmış barlardan.
- **Limit emri.** Çekirdekte limit kuralı yoktu → sahibin yedek kuralı: yalnız seviye ≥ 1 tik İÇİNDEN geçilince dolum, taker
  ücreti. Uygulama: tek-bar limit (emir yalnız sonraki bar için; dolum SEVİYEDEN; maliyet piyasa girişiyle aynı — kayma +
  spread + taker, istenenden ihtiyatlı); strateji kurulum geçerli kaldıkça emri her bar yeniden verir (çekirdek her bar
  kapanışında "düz" kalır → keşfin artımlı = baştan sözleşmesi bozulmaz); seviyeye İLK dokunuş kurulumu tüketir (dokunup
  geçmeyen bar dolum sayılmaz).

| Aile (varyant) | Dilim | Mühürlü kural | Varyantlar |
|---|---|---|---|
| **P1 LUNCH_SWEEP** (`AG_LS_<bias>_<accum>`) | 5m (+15m, 4h) | Başvurular NYAM_H/L (açılışı 09:30–12:00 olan 30 bar; eksikse gün yok) ve koşan LUNCH_H/L (12:00'den tetik barına kadar kapanmış öğle barları). Tetik: açılışı [12:00, 13:30) olan bar; yükseği bir başvuru tepesinin üstünde ve kapanışı altında → sonraki açılışta KISA (UZUN simetrik; iki yönde süpürme → yok). Stop = süpürme barı ucu + tampon; hedef = (NYAM ∪ öğle-şimdiye-kadar) aralığının karşı ucu; hedef < 1,0R → yok (dolumla); zaman çıkışı 16:00 New York. `htf`: yön = son kapanmış 4h kapanış − 4h EMA50 (300 bar pencere) işareti; `quiet`: bugünkü NYAM aralığı ≤ önceki 20 New York gününün NYAM aralıklarının medyanı (15m'den; eksik gün → yok) | bias {none, htf} × accum {none, quiet} = **4** |
| **P2 TENM** (`AG_TENM_<A/B>_<WL/ANY>`) | 5m (çekirdekte 1m yok) | L = 10:00 New York 5m barının açılışı; 09:30–10:00 gözlem; giriş anı 10:00–12:00. Fitilsiz = 10:00 barının açılış yanı fitili ≤ aralığının %10'u. Kip A: açılışı ≥ 10:05 olan bar o gün oluşmuş, henüz girilmemiş 5m FVG'ye (uzun: L'nin altında yükseliş FVG'si) İLK kez girer, sonra FVG'den L'ye doğru dışarı ilk kapanış → sonraki açılışta L'ye doğru; stop = manipülasyon ucu (10:00'dan beri) + tampon; hedef L; < 1,5R → yok. Kip B: gövdesi ≥ 1,5 × ATR14 olan ve L'yi kesen kapanış → kırılım yönünde L'de limit, ilk geri dönüş; stop = kırılım barının karşı ucu + tampon; hedef 3R. 5m kapanışları L'yi 3 kez kesince gün ölü. Günde bir işlem | mode {A, B} × wickless_required {yes, no} = **4** |
| **P3 MSS_FVG** (`AG_MSS_<tf>_<r>R_<sess>`) | 5m, 15m | Yer değiştirme barı: gövde ≥ 1,5 × ATR14 ve kapanış son doğrulanmış karşı salınımın ötesinde (MSS). FVG'si i − 1 / i + 1'den; limit fiyata yakın kenarda (yükseliş: FVG üstü), 12 bar geçerli; stop = yer değiştirmeden önceki salınım ucu + tampon; hedef 2R / 3R; KZ = emir yalnız bölgede | tf {5m, 15m} × TP {2R, 3R} × session {KZ, ALL} = **8** |
| **P4 RB_LIMIT** (`AG_RB_<DIRECT/CONFIRM>`) | 5m, yalnız KZ | RB = fitili son 48 bar içindeki aynı yön doğrulanmış salınımın ötesine geçen ve kapanışı içeride olan doğrulanmış salınım barı; çizgi = fitile yakın gövde kenarı; stop = fitil ucu + tampon; ilk dönüş ≥ 1 bar sonra, 24 bar geçerli. direct = çizgide limit; confirm = dokunuştan sonraki 5m bar çizginin ötesinde kapanır → sonraki açılış. Hedef = en yakın karşı doğrulanmış 5m salınımı ≥ 2R uzaksa o, değilse 2R | entry {direct, confirm} = **2** |
| **P5 MSNR** (`AG_MSNR_<2R/LEVEL>`) | 1h seviye, 5m giriş | Seviye = son 168 1h bar içinde gövde tepeleri (max(o, c)) — ya da ayrı küme olarak gövde dipleri — %0,05 içinde olan ≥ 2 bar (aynı renk tepeler ve yükselen kapanış = düşen açılış). Gövde geçişiyle taze; yalnız ondan sonraki ilk dokunuş; dokunuştan ≤ 12 5m bar içinde geçiş yönünde 5m MSS → sonraki 5m açılış; stop = dokunuş ucu + tampon; hedef 2R ya da kâr yönündeki sonraki seviye (< 1,5R → yok) | TP {2R, sonraki seviye} = **2** |
| **AG_O örtüleri** (`AGO_<taban>_<örtü>`) | tabanınki | LIB_V1 canlı defter kopyalarının AYRI kopyalarına tek örtü (taban varyantları ve LIB_V1 O örtüleri değişmez): **KZ** = giriş yalnız KZ'de; **NOREACT** = dolumdan sonraki 3 giriş-dilimi barında MFE < 0,3R → o barın kapanışında çık; **CAL** = NYSE tam tatili ve FOMC açıklama günü (New York tarihi) giriş yok, takvim dışı → giriş yok (`CAL_UNCOVERED`) | {KZ, NOREACT, CAL} × {T2, M2, BOX, D4, C4, FM} = **18** |

Toplam **38** varyant (P1 4, P2 4, P3 8, P4 2, P5 2, AG_O 18). Örtü tabanları: T2, M2, BOX, D4, FM her biri kendi mühürlü
S1 spec'i (tek parametre kümesi); C4'ün sekiz mühürlü varyasyonu var → LIB_V1 örtülerinin okunuşu aynen: C4 defteri (8
varyasyon config sırasıyla, ilk eşleşen) temsilî spec'tir. FM LIB_V1'de örtüsüzdü, burada örtülenir.

**Takdir okumaları (her biri mühürde `LIB_AG_V1.readings_tr`'de; "döküm imâsı" = koordinatörün ilettiği kural metninin ve
ICT kavramının ima ettiği; dökümlerin kendisi bu çalışmada okunmadı, kaynak koordinatörün mühür metnidir):**

| # | Okuma (seçilen) | Döküm imâsı / gerekçe |
|---|---|---|
| R1 | Saat: ABD 2007 yaz saati kuralının saf hesabı, zoneinfo'yla eşitliği testli | "zoneinfo, DST otomatik" isteniyor; paket saflığı tz dosyası okumayı yasaklar |
| R2 | P1 tetik barları = açılışı [12:00, 13:30): 12:00'de kapanan bar NYAM aralığının son barıdır | öğle süpürmesi = sabah aralığının ÖĞLEDE süpürülmesi |
| R3 | P1: aynı barda iki yönde süpürme → yok; hedef aralığı tetik barı dahil | yön belirsizse takdir tüccarı işlem almaz |
| R4 | P1 quiet: aralıklar 15m barlarından (bugün dahil aynı tanım); 20 takvim günü; eksik gün → yok | "birikim" = sakin sabah; 7/24 piyasada her takvim günü işlem günü |
| R5 | ATR14 = Wilder, barın kendisinde biten SABİT 100 barlık pencere; karşılaştırmada önceki barın ATR'si | "yer değiştirme" ortalamaya göre büyük gövde; kendi ATR'sini şişirmemeli |
| R6 | Tek-bar limit, ilk dokunuş tüketir, maliyet piyasa girişiyle aynı | sahibin ihtiyatlı limit kuralı; kuyruk önceliği bilinmez |
| R7 | Çekirdeğin giriş saati kuralı AG'ye de uygulanır (girişi içeren 1h diliminde hedef kredisi yok) | mevcut ihtiyatlı çekirdek kuralı; 5m'de hedef en erken sonraki saatte alınabilir |
| R8 | 1R/1,5R hedef tabanı ve %0,08–%1,2 stop mesafesi DOLUM fiyatıyla (çekirdeğin R/R kapısı, maliyet payı 0) | "giriş fiyatının %'si" |
| R9 | Zaman çıkışı yalnız P1'de yazılı (16:00); P2–P5'te 48 sinyal barı (5m 4 saat, 15m 12 saat) | dökümler gün içi seans işlemleri anlatıyor; tek kural, sonuca bakılmadan |
| R10 | P2 "3 kez kesme" = 10:00'dan beri kapanışların L'ye göre yanının 3 değişimi (eşit kapanış yan değiştirmez) | çizgi etrafında kararsızlık = model geçersiz |
| R11 | P2 kip A: FVG'nin üç barı o New York gününde; ilk dokunuş ≥ 10:05; "L'ye doğru kapanış" = FVG'nin L tarafına ilk dışarı kapanış; FVG'nin öbür ötesine kapanış bozar; manipülasyon ucu 10:00'dan beri | "Judas" hareketi FVG'de tükenir, fiyat çizgiye döner |
| R12 | P2 kip B: kırılım = L'yi KESEN kapanış; en son kırılım geçerli; emir 12:00'ye kadar | kırılım ve yeniden sınama |
| R13 | P3: MSS = son karşı salınımın ötesine o salınımdan beri İLK kapanış; salınım araması 100 barla sınırlı; stop salınımı = yer değiştirme barında doğrulanmış son karşı-yön salınım; yalnız en son kurulum; KZ'de bölge dışı dokunuş da tüketir | MSS + FVG girişi |
| R14 | P4: önceki salınım [j − 48, j − 2]; dış bar iki yönde RB → yok; ilk dönüş j + 2'den; hedef salınımı = hiç aşılmamış (süpürülmemiş) en yakın karşı salınım; yalnız en son RB | ret bloğu likidite alıp geri kapanan fitil; hedef karşı likidite |
| R15 | P5: açgözlü kümeleme (sıralı değerler, ilk üyenin %0,05 üstüne kadar); seviye = üyelerin ortalaması; oluşma = zamanca ikinci üye; taze = oluşmadan sonraki EN SON gövde geçişi; MSS dokunuş barı dahil 0–12 bar; stop = dokunuştan MSS barına kadarki uç; sonraki seviye bütün kümelerden | MSNR A/V seviyeleri, "taze" seviye, ilk dokunuşta yapı kırılımı |
| R16 | Örtüler tabanların AYRI kopyaları; C4 = C4 defteri; T2/M2 × KZ yapısal olarak işlemsiz (1d karar 00:00 UTC = New York 19:00/20:00) ama deneme sayılır | sahip: "her örtü × her canlı K tabanı bir varyant" |
| R17 | NOREACT: giriş dilimi = tabanın sinyal dilimi; MFE dolum fiyatından; karar 3. barın kapanışında, çıkış sonraki açılışta | "tepki yoksa çık" |
| R18 | CAL: giriş anının New York tarihi; takvim dışı → giriş yok | tahmin edilmez |
| R19 | Günde bir işlem = dolum anının New York günü; reddedilen giriş işlem değildir | — |

- **Evren ve veri (`AG_UNIVERSE_V1`).** S1b `ENTRY_UNIVERSE_V1`'in 40 coin'i (sha f1d57447…, DEĞİŞMEDİ) + XAUUSDT = 41;
  bütün AG varyantları 41 sembolde koşar; Kapı A satırı 41'in birleşimidir (`AG_POOLED`), sembol başına sonuç yalnız rapor
  (deneme değil). XAUUSDT: Binance USDⓈ-M sürekli vadeli (TradFi altın, 7/24 bar); gold_lab_v2'nin mekân verisine göre
  arşiv 2025-12-11'de başlıyor (pencere başı 2025-12-01'den 10 gün sonra; 2026-09-30'a kadar 1h 7.048 / 4h 1.762 / 1d 294
  bar, eksik yok); kesin listelenme anı çalışma anında `exchangeInfo.onboardDate` (`from_listing`). Veri birimi XAUUSDT'yi
  zaten altın vadeli satırında 1m–1d + fonlamayla planlıyordu; S1d `markpx_1h`'ı ekler (S1b fonlama mark'ı; yoksa her
  uzlaşmayı kapsayan işlem `FUNDING_GAP` olurdu) — aynı disk/pencere kuralı (altın vadeli taban tarihi 2025-12-01, 1m son
  400 gün). Listelenme öncesi geçmiş yoktur (doldurulmaz).
- **Takvim (`AG_CALENDAR_V1`).** NYSE tam tatilleri 2023–2026 (41 gün; 2025-01-09 Carter ulusal yas günü dahil; yarım günler
  yok; testte tatil kurallarıyla yeniden hesaplanıp eşitlendi) ve FOMC açıklama günleri (32; iki günlük toplantının ikinci
  günü; 2024-11-07 Perşembe). **Kaynak:** federalreserve.gov bu ortamdan erişilemedi (DNS) → tarihler bilgiyle yazıldı,
  `source.verified_online = false`; **belirsiz:** 2026-07-29, 2026-09-16, 2026-10-28, 2026-12-09 (yayımlanmış takvimden;
  erteleme ya da planlanmamış açıklama olduysa listede yok). Kapsam 2023-01-01 … 2026-12-31; dışında CAL varyantı işlem
  açmaz ve pencere `CAL_UNCOVERED` işaretlenir (`lib_ag.cal_flag`). 2027 takvimi yeni mühür ister.
- **Kapılar, deneme bütçesi.** Kapılar LIB_V1'inkiler (WF_PROTOCOL_V1, STATS_V1; S2 eşikleri mühürlenince onlar). WF sınıfı
  P1–P5 `INTRADAY`, örtüler tabanınki (BOX `INTRADAY`, diğerleri `H1_PLUS`); arındırma/ambargo B1 kuralıyla `max_hold`'dan
  (AG 48; örtüde `copy_books.<taban>.max_hold`). Deneme defteri: 38 `SPEC` satırı (`lib_version = LIB_AG_v1`, `row_id =
  <varyant>@AG_POOLED`; ayrı tekilleştirme ailesi) → N_raw + 38; DSR N alt sınırı 4.513 → **4.551**; BH ailesi = o bakışın
  LIB_V1 satırları + 38 AG satırı (m büyür → daha sıkı); PBO grupları: aile başına (P1 4, P2 4, P3 8, P4 2, P5 2) ve örtü
  tabanı başına (3); plasebo aynı kurulum. Keşif serisi 38 × 41 = 1.558 (yapılırsa bir sonraki LIB mühründe N_explore'a).
- **P3g'nin (S4w/S4x) çağıracağı EKLEME API'si (`research_engine/lib_ag.py`).** Her gece önce `seal_s1d.require()`
  (tutmazsa AG atlanır, açık neden); bir kez `TrialsLedger.append(lib_ag.seal_entries(lib_ag.module_src_shas()))`;
  sembol başına (`lib_ag.universe_symbols()`, ya da `lib_ag.universe_diff(entry_symbols)["ag_symbols"]`) `SeriesData`
  (5m, 15m, 1h, 4h, 1d; T2/M2 için BTC 1d) → `lib_ag.run_variants(ids, data, start_ms=…, end_ms=…)` →
  `lib_ag.trade_rows(sonuçlar, data)`; satır başına `lib_ag.evaluate(vid, satırlar, lib_ag.plan_for([vid], başlangıç,
  bitiş))`; Kapı A ailesine `lib_ag.gate_a_rows()` (bugün `stats.gate_a_look` ailesini yalnız `registry.gate_a_rows()`'tan
  kurar → AG satırlarını aileye katmak P3g'nin değişikliğidir), seçim grupları `lib_ag.selection_group_key(row_id)`,
  plasebo `placebo.KernelPlacebo(lib_ag.make_variant(vid), data)`, CAL penceresi `lib_ag.cal_flag(başlangıç, bitiş)`.
  Keşifte AG serileri `lib_ag.series_plan()`; AG ileri verisi `S1D_SEAL_MS`'den.
- **S1d değerleri.** `LIB_AG_V1` `6421cce57d83d7178a5cfeebf32c6b8141f7977dbfba65e93a9a52a566be82a1`;
  `AG_UNIVERSE_V1` `7e856fa946d6f0668df1e44e6a0cddd371cbaee8a5b70b72001fb27ed8c4a3ec`;
  `AG_CALENDAR_V1` `1ac8e04cd3158aaaf80b29bd8a4f8b19e94b7bf1124607216c4db60ba140bb9d`;
  `S1D_DIRECTIONS_V1` `a69cd988100a7be82efa88f86c2a1596e3e269bc49d141c5b5415ec4b19e1c15` (üç kaydın bütün tanım alt ağaçları `fixed`);
  `S1D_SEAL_MS` `1791926460000` (2026-10-13T21:21:00Z; mühür commit'inin işleyen zamanından birkaç dakika sonraki tam dakika, T_SEAL_MS
  kuralı, testte git'ten); `S1D_SHA` `065b1e7d77d26b6131a149621b75167d4f05666c67cc69cadb9e309d25bb2798` = {dört sha, `S1D_SEAL_MS`, S1
  `REGISTRY_SHA` 3e700fa0…, S1b `S1B_SHA` b3254fe6…, S1c `S1C_SHA` 277922ca…} kanonik sha'sı. Tutmazsa (`seal_s1d.check`)
  `lib_ag.make_variant` `S1dSealError` verir (AG varyantı kurulmaz).
- **Testler.** `tests/test_strategy_lib_ag_s1d.py`: saat = zoneinfo (2019–2030, geçiş günleri), oturum/KZ üyeliği; katalog =
  mühür, LIB_V1 kataloğu 93; altın yollar — P1 (tetik yalnız süpürme barında, 12:00 ilk / 13:30 dışarıda, 16:00 çıkışı, stop
  ve hedef sayıları), P1 yaz saati (EST/EDT günleri ve iki geçiş Pazarı: tetik 12:20 New York, UTC saati kayar), P1 htf/quiet,
  P2 kip B (limit L'de, ilk dönüşe kadar 5 dolmayan bar, 3R hedefi giriş saati sonrası), fitil, 3 kesmede ölü gün, kip A (FVG
  dokunuşu ve L'ye kapanış; 10:05 öncesi dokunulmuş FVG değil), P3 (limit FVG kenarında, dokununca tükenir, 12 bar süresi,
  KZ/ALL), P4 (direct limit + salınım hedefi, confirm + 2R, KZ dışı yok), P5 (ilk dokunuş + MSS, sonraki seviye < 1,5R → yok,
  12 bar sonrası MSS yok); örtüler (KZ, CAL tarihleri/kapsamı ve New York tarihi, NOREACT eşiği ve öncelik, Box örtüleri
  çekirdekte); çekirdeğin limit eki (dokunmak dolum değil, ≥ 1 tik, tik 0, stop yüzdesi, hedef yönü, dolum barında stop);
  20 AG varyantında ileriye bakmama (gelecek barları silmek ve bozmak niyeti değiştirmez) ve önbellek şeffaflığı (önbellekli
  koşunun niyeti = taze hesap); paket motoru ve zoneinfo'yu yüklemez; saflık AST testi yeni dosyaları da tarar.
  `tests/test_research_engine_seal_s1d.py`: sabit sha'lar, S1/S1b/S1c + `contract.py`/`indicators.py` 80fa144 ile
  bayt-aynı, `S1D_SEAL_MS` ≥ commit zamanı, her alan değişikliği sha'yı değiştirir, yönler her tanım yaprağını kapsar ve
  yakalar, varyant sayısı ve bütçe etkisi (38 SPEC → N_raw + 38, ikinci ekleme `DUPLICATE`, LIB_v1 ayrı aile, geçersiz
  sürüm adları reddedilir; LIB_V1 bütçesi 818/1.738 değişmedi), spec_sha, evren ve takvim kuralları, XAUUSDT veri katmanı
  (markpx_1h dahil, listelenmeden), bozuk mühürde kurulum yok, ağsızlık.
- **Ölçüm (sentetik, bu sandbox).** 20 günlük 5m dünyasında varyant başına çekirdek koşusu 0,1–0,5 sn (P3 ALL ve P5 LEVEL
  en yavaş) → sembol-yıl başına ≈ 2–9 sn; 38 × 41 sembol ve yıllarca 5m geçmişi için gece bütçesi P3g'de ölçülmeli.
- **Dürüst etiket.** Konuşmacının sonuçları doğrulanmamış tekrar oynatma (replay) iddialarıdır, kanıt değildir. Konuşmacı
  kriptoyu bilmediğini söylüyor; anlattığı piyasalar NQ/ES, FX ve altındı — oturum saatleri ABD hisse piyasasınındır ve 7/24
  kripto vadelilere aktarım bir varsayımdır. Mekanikleştirme takdiri (bağlam, "temiz" yapı, haber) kaybeder; mühürlü kural
  dökümün kendisi değil, sınanabilir bir okumasıdır. 38 yeni deneme çoklu sınama bedelini büyütür; hiçbir varyantın kapıyı
  geçmemesi beklenen sonuçlardandır.
- **Açık kalanlar.** (1) AG satırlarının Kapı A BH ailesine, CSCV gruplarına ve gece koşusuna bağlanması P3g'de (yukarıdaki
  API). (2) FOMC tarihlerinin çevrimiçi doğrulanması (erişim olunca; değişiklik yeni mühür). (3) 2027 takvimi. (4) Sembol başına
  rapor biçimi (yalnız betimsel). (5) AG keşfinde (S4x) önbelleğin bellek tavanı ve gece süresi P3g ölçümünde.

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

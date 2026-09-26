# Mum varyasyonları (4h) — C4 kâğıt defteri (2026-09-26)

Kullanıcının gönderdiği mum dizilimlerini (görsel ya da metin) sabit bir tanıma çeviren, laboratuvarda ölçen ve yalnız
kullanıcı açıkça izin verirse PAPER'da işleyen defter. Mod PAPER; gerçek para yok. Kaldıraç 1.

**Bugünkü durum: defter açık ama BOŞTUR.** `config.yaml`'daki liste (`variations: []`) boş başlar. Kullanıcı bir
varyasyon gönderip aşağıdaki prosedür tamamlanana kadar defter hiçbir işlem açmaz. Kuralın durumu (`rule_state`)
`NO_VARIATIONS` ("varyasyon bekliyor") döner. Kayıttaki tek varyasyon bir tasarım örneğidir ve hiçbir zaman işlem açmaz.

Kâr garantisi değildir. Laboratuvar sonucu geçmiş testtir; defter canlı davranışı ölçer.

## 1. Durum tablosu

| Kimlik | Başlık | Hüküm | Laboratuvar çalıştırması | `definition_sha` | Gözlem | Etkin |
|---|---|---|---|---|---|---|
| `CV000_EXAMPLE_BULL3` | ÖRNEK (işlem açmaz): düşüş sonrası uzun kırmızı, küçük bekleme, hacimli yeşil | — | yok | `d429437bb457fcdd` | — | hayır (örnek) |

Yeni bir varyasyon etkinleştirildiğinde bu tabloya bir satır eklenir (prosedürün 8. adımı).

## 2. Kural ve sınırlar

- **Dilim:** yalnız 4h. Pencere son 500 kapanmış 4h bardır, hacim dahil. 5m ve 15m tanımları reddedilir (`COST_TRAP`:
  maliyet R'yi yer). 1h ve 1d laboratuvarda yalnız bilgi amaçlı koşar; defterde açılmaz (`TF_NOT_ENABLED_V1`).
- **Evren:** D4 ile aynı 23 coin. BTC rejimine ve ortak yapı kataloğuna (structures_v1) bakılmaz, çünkü laboratuvar
  onlarsız ölçer.
- **Tek dedektör:** `candle_dsl.detect_last(pencere, varyasyon)`. Defter ve laboratuvar aynı fonksiyonu aynı 500 barlık
  pencereyle çağırır. Laboratuvar bunu her tarihsel bar için o barda biten pencereyle yapar.
- **Kapı:** her tur, her varyasyon `candle_variations.gate` ile denetlenir. Kapı asla hata vermez; geçmeyen varyasyon
  yalnız kendisi kapanır. Nedeni `rule_state`'te görünür ve motor günlüğüne süreç başına bir kez uyarı olarak yazılır.
  Sırasıyla denetlenenler: kimlik → tanım → örnek değil → emekli değil → çeviri onayı → laboratuvar kaydı → DSL sürümü
  ve pencere → `definition_sha` → CI kaydı → geçerli kayıt (şema, bilinen hüküm, yalnız varyasyon koşusu;
  `LAB_RECORD_INVALID`) → 4h ölçülmüş (`LAB_NOT_4H`) → çeviri onayı sonuçtan önce → KAYBETTİRİR değil → kullanıcı onayı
  → onay sonuçtan sonra → GÜÇLÜ ADAY değilse açık gözlem onayı.
- **"Önce" ve "sonra" çalıştırmaya bağlıdır, yalnız güne değil.** Laboratuvar kaydı, koşu anında kayıtta duran çeviri
  onayını taşır. Onaysız (taslak) koşunun kaydı kapıdan geçmez; onay sonradan aynı gün yazılsa da geçmez
  (`READBACK_AFTER_LAB`). Kullanıcı onayı, sonucunu gördüğü çalıştırmanın kimliğini taşır: `approval.run_id` =
  kaydın `run.github_run_id`. Kimlik yoksa ya da başka çalıştırmanınsa `APPROVAL_BEFORE_LAB`. Tarih karşılaştırmaları
  yedek denetim olarak kalır.
- **Giriş:** sinyal kapanışından sonraki ilk doğrulanmış perp fiyatı, en geç 60 dakika içinde. Kesintiden sonra geç giriş
  yapılmaz.
- **Stop:** tanımın stop kuralı (varsayılan: formasyonun dibi − 0,25 × ATR14). Girişten stop'a mesafe tanımın risk
  aralığı dışındaysa (varsayılan 0,1–5 ATR) girilmez (`RISK_OUTSIDE_TESTED_RANGE`).
- **Hedef:** gerçek giriş fiyatından `target_r` × risk (varsayılan 2R). Tek hedeftir; değince pozisyonun tamamı kapanır.
- **Zaman sınırı:** girişin barından sayılan `max_hold_bars` bar (varsayılan 24 bar = 4 gün) kapanınca kapatılır. Sınır
  girişteki anlık görüntüden okunur; varyasyon sonradan emekli edilse de açık pozisyon kendi kuralıyla biter.
- **Portföy:** sembol başına tek pozisyon, aynı sinyalle tek giriş (stop aynı barda gelse bile), aynı anda en çok 3
  pozisyon, toplam risk tavanı. Tek pozisyon tavanını aşan işlem reddedilmez, tavana sığacak kadar küçültülür
  (`size_scaled_to_cap`); risk bütçenin altında kalır.
- **Öncelik:** `variations` listesinin sırası. Aynı barda eşleşen diğer varyasyonlar işlem kaydında `also_matched`
  alanında görünür.
- **İzlenebilirlik:** kurulum adı `candle:<kimlik>`. `features.candle_variation` alanında kimlik, `definition_sha`, DSL
  sürümü, laboratuvar hükmü, çalıştırma bağlantısı, doğrulama dönemi ortalaması ve aralığı, gözlem bayrağı, hedef R ve
  en uzun tutma durur. `meta.signal` alanında sinyal zamanı, kapanışı, ATR14 ve `lab_algo = <kimlik>` durur.

## 3. DSL başvurusu

Tanımın tam dilbilgisi `tradingbot/candle_dsl.py` başındaki açıklamadadır. Özet:

- **Mumlar:** 1–5 mum, soldan sağa `c0 … c{n-1}`. Her mum için renk (`bull`, `bear`, `any`) ve aralıklar (`[min, max]`,
  iki uç dahil, `None` açık uç): `body_range`, `upper_wick_range`, `lower_wick_range`, `close_pos` (aralığa oran, 0–1),
  `range_atr`, `body_atr` (formasyondan önceki barın ATR14'üne oran), `volume_ratio` (önceki 20 barın hacim ortalamasına
  oran). Aralığı sıfır olan barda aralığa bölünen oranlar tanımsızdır ve koşul geçmez.
- **İlişkiler:** `c1.high <= c0.high`, `c2.close > c0.body_mid`, `c2.body >= 2 * c1.body`,
  `c1.open > c0.close + 0.05 atr`, `c1.low <= min_low(-10..-1)`. Alanlar: `open high low close body range body_hi
  body_lo body_mid mid upper_wick lower_wick volume`. Toplamlar formasyondan önceki barlar üzerinde (-50..-1):
  `max_high min_low max_close min_close avg_body avg_range avg_volume`. `==` yoktur.
- **Bağlam (şekil dışı koşullar):** `rsi14`, `volume_ratio`, `atr_regime` (teyit barında), `trend` (`with`, `against`,
  `mixed`; laboratuvarın trend dilimi, yöne göre), `ema200_side`, `prior_move` (formasyondan önceki L barın ATR
  cinsinden hareketi).
- **Teyit:** `pattern_close` (son mumun kapanışı) ya da `break` (1–3 bar içinde formasyonun ucunun ötesinde ilk kapanış).
- **Stop, çıkış, risk aralığı:** `stop.anchor` (`pattern` ya da `pattern_to_confirm`), `stop.atr_buffer` (0–2),
  `exit.target_r` (0,5–10 ya da hedefsiz), `exit.max_hold_bars` (1–300), `risk_atr_bounds`.

Otomatik ayna yoktur: SHORT sürüm ayrı bir kimliktir. Tanım değişirse `definition_sha` değişir ve kapı varyasyonu
kapatır; değişiklik her zaman yeni kimliktir (`supersedes` ile).

### Sözlük (kelime → sabit eşik)

Çeviride eşik vaka başına ya da sonuç görüldükten sonra seçilmez; kelimeler `candle_dsl.LEXICON`'dan okunur. Katalogda
karşılığı olanlar `learn.candle_context.CandleContextConfig` değerleridir ve bir test eşitliği denetler.

| Kelime | Kısıt | Kaynak |
|---|---|---|
| doji | `body_range <= 0.10` | `doji_body_ratio` |
| küçük gövde / topaç | `body_range <= 0.35` | `spinning_top_body_max` |
| uzun gövde | `body_range >= 0.60` | `belt_hold_body_min` |
| marubozu | `body_range >= 0.85` | `marubozu_body_ratio` |
| üst / alt fitilsiz | o taraftaki fitil `<= 0.05` | `belt_hold_wick_max` |
| çekiç / uzun alt fitil | `lower_wick_range >= 0.55`, `upper_wick_range <= 0.20` (ters çekiç aynası) | `hammer_wick_ratio` / `hammer_opposite_wick_max` |
| büyük mum / küçük mum | `range_atr >= 1.3` / `range_atr <= 0.7` | DSL v1 sabiti |
| güçlü kapanış | `close_pos >= 0.70` (boğa) / `<= 0.30` (ayı) | DSL v1 sabiti |
| hacimli / hacimsiz | `volume_ratio >= 1.5` / `<= 0.8` | laboratuvar hacim dilimleri |
| aşırı satım / aşırı alım | `rsi14 <= 30` / `>= 70` | laboratuvar RSI dilimleri |
| düşüş sonrası / yükseliş sonrası | `prior_move {bars: 10, max: -0.5}` / `{bars: 10, min: 0.5}` | katalog `detect_trend` |
| trendle / trende karşı | `trend ["with"]` / `["against"]` | laboratuvar trend bağlamı |
| iç mum | `c1.high <= c0.high`, `c1.low >= c0.low` | — |
| yutan (gövde) | `c1.body_hi >= c0.body_hi`, `c1.body_lo <= c0.body_lo` | `engulf_min_ratio` 1,0 |
| gövde boşluğu | `c1.body_lo > c0.body_hi` (yukarı) | perp'lerde gerçek boşluk nadirdir |
| varsayılan çıkışlar | stop formasyon ucu ± 0,25 ATR, hedef 2R, en çok 24 bar | laboratuvarın tampon, `default_rr`, `max_hold_bars` değerleri |

### Örnek (kayıttaki tek kayıt, `example=True`)

`CV000_EXAMPLE_BULL3` (LONG): düşüş sonrası (`prior_move` 10 bar ≤ −0,5 ATR, RSI14 ≤ 55) uzun kırmızı mum (gövde ≥ %60,
aralık ≥ 1,3 ATR); gövdesi ilk mumun gövde ortasının altında kalan küçük bekleme mumu (gövde ≤ %35, aralık ≤ 0,7 ATR),
son 10 barın dibini süpürür; hacimli yeşil mum (gövde ≥ %60, üst fitil ≤ %25, hacim ≥ 1,5×) ilk mumun gövde ortasının
üstünde kapanır. Teyit üçüncü mumun kapanışı; stop formasyon dibi − 0,25 ATR; hedef 2R; en çok 24 bar. Eşleşen ve kıl
payı kaçan diziler `tests/candle_variation_examples.py` içindedir.

### İfade edilemeyenler

Yatay seviyeler, trend çizgileri, salınım (swing) yapıları, bayrak/flama, omuz-baş-omuz gibi çok barlık grafik
formasyonları bu DSL ile yazılmaz. Bunlar ortak yapı kataloğuna (structures) ya da Formasyon defterine aittir. Böyle bir
görsel gelirse çeviride açıkça söylenir ve oraya yönlendirilir.

## 4. Laboratuvar karşılıkları

| Parça | Defter | Laboratuvardaki karşılığı |
|---|---|---|
| Sinyal | `detect_last`, son 500 kapanmış 4h bar | aynı fonksiyon, aynı pencere, her bar için (`candle_lab.variation_events`) |
| Okunamayan pencere | sinyal yok (eksik bar, zaman boşluğu, okunamaz OHLC, hacim yok) | o pencere değerlendirilmez (`valid_ends`) |
| Giriş fiyatı | sinyal kapanışından sonraki ilk doğrulanmış perp fiyatı, en geç 60 dk | sonraki barın açılışı |
| Stop | dedektörün stop'u | aynı değer |
| ATR (risk aralığı için) | dedektörün pencere ATR14'ü | aynı değer (`atr_sig`) |
| Risk aralığı | `RISK_OUTSIDE_TESTED_RANGE` | `STOP_TOO_CLOSE` / `STOP_TOO_FAR`, aynı eşitsizlik |
| Hedef | gerçek giriş ± `target_r` × risk | sonraki açılış ± `default_rr` × risk |
| Zaman sınırı | girişin barı dahil H bar kapanınca, sonraki turun fiyatından (kesintide `late_bars`) | `close[j+H-1]` |
| Aynı sinyal | tek işlem | her sinyal tek işlem |
| Maliyet | ücret + kayma + funding | taker %0,05 + 3 bps her tarafta (funding yok) |
| Karşılaştırma | — | eşleştirilmiş plasebo `PLACEBO_<kimlik>`: aynı yön, aynı bağlam ve `prior_move`, aynı risk/ATR dağılımı, aynı çıkış ve risk aralığı, rastgele an |

Plasebo hükmün tek sorusunu cevaplar: mumun şekli, bağlamının ve risk/çıkış geometrisinin ötesinde R katıyor mu? Hüküm
kelimeleri laboratuvarınkilerdir: GÜÇLÜ ADAY, ZAYIF İZ, KANIT YOK, KAYBETTİRİR, VERİ AZ.

**Plasebonun stop'u neden stop kuralından gelmez.** Mumun şekli stop mesafesini de belirler: boğa gövdesi kapanışı
dipten uzaklaştırır, kırılım barı formasyon tepesinin üstünde kapanır. Stop kuralını rastgele barın son n barına
uygulamak plaseboya daha dar stop verir. O zaman plasebonun R cinsinden maliyeti daha yüksek, sürüklenmesi farklı olur
ve `vs_placebo` şekli değil stop geometrisini ölçer. Avantajı olmayan, hafif yükselen rastgele veride boğa mumu ve iç
mum kırılımı bu yüzden GÜÇLÜ ADAY çıkıyordu. Bu yüzden plasebo stop'u `kapanış ∓ q × ATR14` olarak kurulur. q (risk/ATR),
o bardan önceki gerçek isabetlerden birinden alınır; seçim zaman damgasıyla belirlenimcidir ve geleceğe bakmaz.
Kırılım teyidinde o isabetin gecikmesi (teyit barı − formasyon sonu) de alınır, böylece `prior_move` gerçek olaylardaki
gibi formasyonun başına çapalanır. Bir sembolde henüz gerçek isabet yoksa plasebo atlanır
(`PLACEBO_<kimlik>:NO_REAL_RISK`).

**Eşleşme testleri:** `tests/test_candle_variations_book.py` (`test_parity_*`). Sentetik serilerde defterin açtığı
barlar, stop'ları, ATR'si, hedefi, risk sınırları ve zaman çıkışı laboratuvarınkilerle birebir aynıdır.

## 5. Laboratuvardan farklı olanlar

Bunlar ölçümü etkiler; sonuç yorumlanırken akılda tutulmalı.

- **Giriş.** Defter sonraki barın açılışında değil, sinyal kapanışından sonraki ilk doğrulanmış perp fiyatından girer
  (en geç 60 dk). Kovalama kapısı yoktur; stop mesafesini risk aralığı korur.
- **Portföy sınırları.** Aynı anda en çok 3 pozisyon, sembol başına bir pozisyon ve toplam risk tavanı vardır. Semboller
  config sırasıyla işlenir; 4h sinyalleri çoğu zaman aynı anda geldiği için defter sinyallerin taraflı bir alt kümesini
  alır. Varyasyonlar arasında öncelik gölgelemesi olur. Laboratuvar örtüşen sinyallerin hepsini sayar; kayıttaki
  bilgi amaçlı örtüşmesiz ortalama R (`non_overlap_mean_r`) deftere daha yakındır.
- **Fiyat yolu.** Defter stop ve hedefi 60 sn'lik canlı fiyat izleyicisi ve kapanmış 1h bar uçlarıyla denetler;
  laboratuvar 4h bar uçlarıyla kötümser denetler (aynı barda ikisi → stop). Hedef defterde maker dolumu olabilir.
- **Funding** defterde işlenir, laboratuvarda yoktur.
- **Evren.** Defterde 23 coin, laboratuvarda 30 coin.
- **Kesinti.** Kesintiden sonra geç giriş yapılmaz.
- **EMA200** 500 barlık pencerenin tanımıdır; TradingView'daki değer değildir.
- **Zaman çıkışının fiyatı.** Laboratuvar H'inci barın kapanışında çıkar. Defter o bar kapandıktan sonraki ilk turda,
  o anki doğrulanmış perp fiyatından kapatır; kesintide geciken barlar `late_bars` alanına yazılır.
- **Replay** motoru `signal_already_used` çağırmaz. Bu, D4 ile ortak, önceden var olan bir farktır. C4 replay'i en az
  502 barlık dilim ister (`lookback_bars >= 502`; varsayılan 400). Daha kısa dilimde açıkça hata verir, sessizce boş
  sonuç üretmez.

## 6. Çoklu test uyarısı

Her yeni varyasyon yeni bir denemedir; `trials_to_date` bugüne kadar denenenleri sayar (emekliler dahil). Rastgele
yürüyüş verisinde kombinasyonların yaklaşık %0–0,5'i GÜÇLÜ ADAY, %6–11'i ZAYIF İZ çıkar. Çok varyasyon denendikçe şans
eseri "iyi" görünen bir tanım bulma olasılığı artar. Laboratuvarın doğrulama dönemi (son üçte bir) kullanıcının
baktığı grafiklerle örtüşür; gerçekten örneklem dışı olan tek ölçüm ileriye dönük PAPER sonucudur. Bir hüküm için en az
30 kapanmış işlem gerekir.

GÜÇLÜ ADAY'ın "eşini geçiyor" şartı yalnız iki dönemde de farkın sıfırdan büyük olmasını ister, anlamlı olmasını değil.
Genel olarak yükselen bir piyasada avantajı olmayan bir LONG şekli, iki dönemde aralığı sıfırın üstünde bulur ve eşini
yaklaşık dörtte bir olasılıkla şans eseri iki kez geçer. Tek bir GÜÇLÜ ADAY bu yüzden kanıt değildir; eşine göre farkın
büyüklüğüne ve ileriye dönük PAPER sonucuna bakılır.

## 7. Varyasyon ekleme prosedürü

Her adım `claude/candle-cvNNN` dalında bir commit ile biter.

1. **Çeviri.** Görsel ya da metin okunur. `notes_tr` yazılır: soldan sağa her mumun rengi, gövdesi, fitilleri, ATR'ye
   ve komşularına göre büyüklüğü, konumu (içinde, yutan, ortanın üstünde kapanış) ve hacmi; sonra formasyon öncesi
   bağlam, teyit ve yön. Her sıfat sözlükten eşlenir, eşik uydurulmaz. DSL'de ifade edilemeyen şekil (seviye, trend
   çizgisi, salınım yapısı) açıkça söylenir ve yapı kataloğuna ya da Formasyon'a yönlendirilir. 5m/15m grafikten gelen
   bir şekil dilimi olmadan çevrilir ve 4h'de test edilir; bu da söylenir.
2. **Onay (readback).** Kullanıcıya tanımın Türkçe açıklaması, eşleşen örneğin tablo ya da ASCII biçimi, belirsizlikler
   için en çok 3 soru (her biri için sözlüğün varsayılanı önerilerek) ve varsayılan çıkışlar gönderilir. Kullanıcı
   onaylamadan ilerlenmez. Kayda `readback: {confirmed_by: "user", date}` yazılır. Onay laboratuvar koşusundan ÖNCE
   commit edilmelidir. Laboratuvar kaydı koşu anındaki onayı taşır; taslak koşunun kaydı kapıdan geçmez. Çeviri
   onayından sonra laboratuvar yeniden koşulur.
3. **Tanım.** `tradingbot/candle_variations.py` içindeki `VARIATIONS` listesine sıradaki kimlikle (`CVnnn_KISA_AD`)
   eklenir; `source`, `notes_tr` ve `readback` ile. Ayna ya da sonraki her değişiklik `supersedes` alanıyla yeni
   kimliktir.
4. **Birim testi.** `tests/candle_variation_examples.py` içine `EXAMPLES[kimlik]` eklenir: bir eşleşen dizi ve her ana
   koşul için bir kıl payı kaçan dizi (ATR birimiyle). Çalıştır:
   `python -m pytest tests/test_candle_dsl.py tests/test_candle_variations_book.py -q`. Testler varyasyon eklenince
   DEĞİŞTİRİLMEDEN geçmelidir; yalnız `EXAMPLES` eklenir.
5. **Laboratuvar.** `research/signal_lab_args.txt` şu satır yapılır ve itilir (`signal-lab.yml` dosya değişince
   tetiklenir):
   `--only-variations --variations CVnnn_X --symbols genis --tfs 4h,1h,1d --days 4h=1460,1h=730,1d=1825`.
   Alternatif: `gh workflow run signal-lab.yml -f args="..."`. `signal-lab-report` çıktısından `console.txt` (MUM
   VARYASYONLARI bölümü) ve `variation_records/CVnnn_X.json` indirilir.
6. **Kayıt.** JSON dosyası bayt bayt `tradingbot/candle_lab_records/CVnnn_X.json` olarak kopyalanır ve commit edilir.
   `claude/candle-cvNNN` dalına itmek `chart-analysis.yml`i tetikler: CI tanım mührünü, DSL sürümünü ve altın özeti
   (`golden_sha`, `test_golden_sha_matches_every_lab_record`) denetler. Tetiklenmediyse elle koşulur:
   `gh workflow run chart-analysis.yml --ref claude/candle-cvNNN`. CI yeşil olmadan ilerlenmez.
7. **Kullanıcıya rapor (sade Türkçe).** Hüküm kelimesi; keşif ve doğrulama dönemi için işlem sayısı, ortalama R ve gün
   kümeli %95 aralık; iki dönemde eşleştirilmiş plaseboya göre fark; R cinsinden maliyet; dilim başına sonuç (1h ve 1d
   bilgi amaçlı); örtüşmesiz ortalama R; öne çıkan bağlam dilimleri (keşif amaçlı olduğu belirtilerek);
   `trials_to_date` ve çoklu test uyarısı; doğrulama döneminin görülen grafiklerle örtüştüğü hatırlatması;
   "Kâr garantisi değildir." Bu adımda hiçbir şey etkinleştirilmez.
8. **Etkinleştirme (yalnız kullanıcının açık onayıyla).**
   - GÜÇLÜ ADAY: `approval: {by: "user", date, observation: false, run_id: "<kaydın run.github_run_id>"}`. `run_id`,
     kullanıcının sonucunu gördüğü laboratuvar çalıştırmasıdır; yoksa ya da başka çalıştırmanınsa kapı geçmez.
   - ZAYIF İZ, KANIT YOK ya da VERİ AZ: yalnız kullanıcı açıkça gözlemlemek isterse, `observation: true` (aynı
     `run_id` ile). İşlemleri "gözlem, kanıtlanmadı" diye etiketlenir (D4 gibi).
   - KAYBETTİRİR: asla; `retired` yazılır.
   - Kimlik `config.yaml → c4_candle_variations.rule_params.variations` listesine eklenir; sıra önceliktir.
   - Bu belgenin durum tablosu güncellenir.
   - Testler çalıştırılır. `claude/candle-cvNNN` dalına itilir; `chart-analysis.yml` bu dalda ve `config.yaml`
     değişikliğinde tetiklenir (gerekirse `gh workflow run chart-analysis.yml --ref claude/candle-cvNNN`). CI yeşil
     olmalı (`test_repository_config_variations_pass_gate`: etkin her kimlik kapıdan geçer). Sonra PR açılır ve
     birleştirilir.
   - Dağıtım `bash deploy/update.sh` ile (turlar arasında yeniden başlatır). CI yeşil olmadan dağıtılmaz.
9. **İzleme.** `python scripts/bot_scorecard.py --state <state>` defterin `candle:CVnnn` satırını ve varyasyon başına
   sonucu gösterir (`by_variation`: hüküm, gözlem işareti, laboratuvar doğrulama aralığı, `LAB_DRIFT`). Kuralın durumu
   (`paper_rules.state_for`; panelin grafik analizi verisindeki `rule_state` alanı) her varyasyon için kapı durumunu,
   eşleşmeyi, ilk başarısız koşulu ve bekleyen kırılımı taşır. Grafiğin Türkçe açıklama satırlarına bu defter için
   henüz ayrı bir bölüm eklenmedi (sonraki adım). Hüküm için en az 30 işlem gerekir.
10. **Değiştirme ya da emekli etme.** Kimlik config'ten çıkarılır; açık pozisyonlar kendi stop, hedef ve (anlık
    görüntüden) zaman sınırıyla biter. Kayıtta `retired` yazılır. Bir `definition` asla düzenlenmez: değişirse mühür
    değişir ve kapı varyasyonu kapatır. Değişiklik yeni kimlik, yeni laboratuvar çalıştırması ve yeni bir denemedir.

## 8. Kapatma

`config.yaml` → `strategy_paper.extra` içinde `c4_candle_variations`. Durum klasörü `state/strategy_paper_candle4h`.

- **Tek varyasyonu kapatmak:** kimliği `variations` listesinden çıkarmak yeter. Diğer varyasyonlar sürer; o
  varyasyonun açık pozisyonları kendi stop, hedef ve (girişteki anlık görüntüden) zaman sınırıyla biter.
- **Tüm defteri kapatmak (`enabled: false`):** defter hiç kurulmaz; stop, hedef ve zaman sınırını işleyen kalmaz. Açık
  pozisyon varsa `state/strategy_paper_candle4h` kaydında donmuş olarak durur (D4 ile aynı). Temiz kapatmak için önce
  listeyi boşaltın (`variations: []`), açık pozisyonların kapanmasını bekleyin, sonra `enabled: false` yapın.

Diğer defterler (ana bot, T2, M2, Box, Formasyon v3, D4) değişmez. Uygulayıcıdaki iki yeni anahtar
(`target_r_from_entry`, `variation`) yalnız onları taşıyan eylemde çalışır.

Bu değişiklik VPS'e ancak birleştirilip dağıtıldığında gider. O zamana kadar canlı botlarda hiçbir şey değişmez.

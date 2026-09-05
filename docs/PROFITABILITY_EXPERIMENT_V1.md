# PROFITABILITY_EXPERIMENT_V1 — beş donmuş politikanın izole PAPER yarışması

**Durum:** SHADOW PAPER ONLY · `applied_to_canonical = false` · `auto_promotion = false` ·
terfi bugün **imkânsız**.

> **Bu deney kâr vaat etmez ve kârı kanıtlamaz.** Tek bir soruyu yanıtlar: sabit
> politikalardan hangisi — varsa — **maliyet sonrası pozitif ileriye dönük beklenti**
> üretir? Bir challenger'ın tek bir kaybedeni elemesi **başarı sayılmaz**.

Kök-neden denetimi: `docs/PROFITABILITY_ROOT_CAUSE_V1.md`.

---

## 1. Neden bu deney

Kök-neden denetimi üç şeyi gösterdi:

1. 23 kapanışta beklenti **-0,4655 R**, ama %95 GA **[-0,9201, +0,0822]** — sıfırı içeriyor.
2. Sürtünme zararın yalnız **%16,7**'si; tamamen silinse beklenti yine **-0,3878 R**.
3. **Mevcut giriş filtrelerinin hiçbiri** tarihsel örneklemde beklentiyi pozitife
   çevirmiyor; A/E kombinasyonu profit factor'ü 0,4436 → **0,4379'a düşürüyor**.

Yani geriye dönük seçim işe yaramıyor ve örneklem zaten yetersiz. Bu yüzden **yeni bir
strateji eklenmez**; var olan politikalar **ileriye dönük, adil ve izole** bir yarışmaya
sokulur.

---

## 2. Beş donmuş politika

| Politika | Tür | Tanım |
| --- | --- | --- |
| `P0_CHAMPION_MIRROR` | referans | Şampiyonun gelecekteki kabul ettiği girişleri **aynen** aynalar. Yalnız kıyas tabanı. |
| `P1_SELECTIVE_AE` | filtre-only | A **ya da** E kesin `VETO` derse eler. Mevcut A/E sürümü ve eşikleri **yeniden ayarlanmaz**; `ABSTAIN` **VETO'ya çevrilmez**. |
| `P2_DIRECTIONAL_DIVERSIFICATION` | filtre-only | Aynı yön ve ölçülmüş korelasyon kümesi yoğunlaşmasını sınırlar. Yalnız girişten **önce** bilinen bilgi. |
| `P3_PROFIT_PROTECTION` | çıkış | Girişi aynalar, çıkışta **zaten sürümlenmiş** `exit_policy` kâr kilidini kullanır. Eşik ayarı **yok**. |
| `P4_COMBINED` | birleşik | Tam olarak P1 + P2 + P3. **Ek kural yok.** |

`number_of_trials = 5` — çoklu karşılaştırma düzeltmesi bu sayıyla yapılır.

### 2.1 Kimlik dondurma

Her kayıt şunları taşır: `experiment_id`, `policy_version`, `config_id`, `code_sha`,
`frozen_at`, `evaluation_start_at`, `number_of_trials`.

`config_id` **sonuçlardan bağımsızdır**: yalnız sürüm + eşikler üzerinden türetilir
(`frozen_at` ve `code_sha` hariç). Bir eşik değişirse kimlik değişir; böylece sonuç
görüldükten sonra sessizce eşik oynatmak **imkânsızdır**.

`evaluation_start_at` **bir kez** dondurulur ve kitap dosyasından okunur; sonradan geriye
çekilemez.

---

## 3. P2 limitleri — neden bu değerler

Değerler **risk profilinin kendi bütçesinden** türetilmiştir, 23 tarihsel kapanışa göre
optimize **edilmemiştir**.

| Limit | Değer | Gerekçe |
| --- | --- | --- |
| `max_same_direction_risk_share` | **0,60** | `max_total_open_risk_pct` bütçesinin çoğunluğu tek yöne gidemez ilkesi. Ölçülen açık portföyün **%91'i LONG**tur; 0,60 bunu sınırlar ama tek yönlü işlemi yasaklamaz. |
| `max_cluster_risk_share` | **0,35** | Tek bir ölçülmüş korelasyon kümesi toplam riskin üçte birinden fazlasını taşımasın. |
| `max_positions_per_cluster` | **3** | Aynı kümede en fazla üç eşzamanlı bahis. |
| `correlation_min_overlap` | **30** | 30 örtüşen kapanmış 1s barından az veriyle korelasyon **hesaplanmaz** (`UNKNOWN`). |
| `correlation_cluster_threshold` | **0,60** | Bu eşiğin üstündeki `\|r\|` aynı küme sayılır. |
| `correlation_lookback_bars` | **120** | Motorun zaten çektiği 1s karesinden bounded pencere. |

**Korelasyon ölçülemezse küme kısıtı UYGULANMAZ** ve bu açıkça `CORRELATION_UNKNOWN` olarak
raporlanır — eksik veri sıfır sayılmaz. Tek pozisyonluk portföyde "aynı yön payı" tanım
gereği 1,0'dır; bu bir yoğunlaşma değildir ve kısıt ancak açık pozisyon varken anlamlıdır.

**Yeni sağlayıcı isteği yoktur.** Korelasyon, motorun zaten çektiği `1h` karesinden ve
yalnız `bar.close_time <= as_of_ms` koşulunu sağlayan **kapanmış** barlardan hesaplanır.

---

## 4. İzolasyon sözleşmesi

Deney **hiçbir koşulda**:

| Yasak | Koruma |
| --- | --- |
| Kanonik futures/spot defterine yazmak | modül defteri ithal bile etmez (test 3–4) |
| RiskEngine'i çağırmak/değiştirmek | çapraz ithal yok (test 3–4) |
| Gateway/emir yoluna dokunmak | gateway/ccxt/borsa adı kodda yok (test 5) |
| Sermaye/kaldıraç/risk bütçesi değiştirmek | ilgili adlar kodda yok (test 6) |
| Kanonik pozisyon açmak/kapatmak/boyutlandırmak | fingerprint testi (test 4) |
| Aktif giriş kararını değiştirmek | katman açık/kapalı kanonik nesneler aynı (test 1–2) |
| Şampiyonun reddettiği işlemi simüle etmek | `CHAMPION_DID_NOT_ACCEPT` → `FILTER` (test 15) |
| Kanonik stop/TP'yi oynatmak | P3/P4 yalnız kendi `SimPosition.stop`unu değiştirir (test 16) |
| `ACTIVE`/`PAPER_BOUNDED` moda geçmek | `config_v3` fail-closed reddeder |
| Otomatik terfi | `experiment_auto_promotion=true` → `ConfigError` |

Deney yalnız **kendi** dosyalarına yazar:

```
state/profitability_experiment_events.jsonl   (ekle-yalnız olay defteri — KANONİK)
state/profitability_experiment_books.json     (atomik + checksum'lı türev anlık görüntü)
state/profitability_experiment.json           (atomik karşılaştırma raporu)
```

### 4.1 Dayanıklılık

* **Ekle-yalnız defter** kanonik kayıttır; kitap her zaman ondan yeniden üretilebilir.
* **Deterministik `event_id`** → aynı olay iki kez uygulanmaz (idempotent replay).
* **Çökme kurtarma:** kitap checksum'ı tutmazsa `replay()` defterden onarır.
* **Bozuk satır gizlenmez:** sayılır ve `malformed` olarak raporlanır.
* **Arşiv-önce rotasyon:** arşiv yoksa ya da yazılamazsa **budama yapılmaz** (kayıpsız).

---

## 5. Filtre-only ve simülasyon kuralları

* Challenger'lar yalnız şampiyonun **zaten kabul ettiği** girişleri eler ya da aynalar.
  Şampiyonun reddettiği bir işlem **simüle edilmez** — aksi hâlde hiç var olmamış bir dolum
  uydurulur ve karşılaştırılabilirlik kaybolur.
* Kabul edilen giriş **aynen** aynalanır: fiyat, miktar, stop, hedef, kaldıraç ve maliyet
  modeli şampiyonunkiyle aynıdır.
* Marklar yalnız kanonik `position_path` kayıtlarından gelir; **fiyat uydurulmaz**.
* Kapanışlar kanonik kapanıştan aynalanır. Çıkış fiyatı ölçülemezse pozisyon **açık kalır**;
  uydurma kapanış üretilmez.
* P3/P4 ek olarak kendi kâr kilidini uygular; sıkıştırılmış stop bir markta ihlal edilirse
  **simüle** çıkış olur. Kanonik stop/TP **etkilenmez**.
* Her karar `ACCEPT` / `FILTER` / `ABSTAIN` ve gerekçe kodlarıyla kaydedilir; fiyat ya da
  gerekli alan yoksa **`ABSTAIN`**.

---

## 6. Ölçülen metrikler

Politika başına: açılan/kapanan/elenen/çekimser, kapsam, çekimser oran, net USDT, toplam R,
kazanma oranı, ortalama kazanan/kaybeden, profit factor, beklenti, maxDD, CVaR5, komisyon/
funding/kayma (ayrı; kayma **ölçüldü/eksik** sayısıyla), turnover, açık risk, aynı yön risk
payı, kaçınılan zarar, kaçırılan kâr, ortalama MFE, MFE tutma, giveback ve bootstrap GA.

**Maliyet iki kez sayılmaz:** brüt PnL'den komisyon ve funding birer kez düşülür; kayma
ayrı alandadır ve PnL'den tekrar düşülmez.

---

## 7. İki ayrı statü

### `EARLY_DIRECTIONALITY`
* **10** karşılaştırılabilir kapanıştan sonra görünür.
* **Yalnız bilgilendiricidir**: `activates_anything = false`. Hiçbir şeyi aktive edemez,
  terfi ettiremez.
* Güven aralığı ve düşük örneklem uyarısı **zorunludur**.

### `PROMOTION_ELIGIBILITY`
Mevcut kapılar **korunur ve gevşetilemez** (`ExperimentConfig.validate` bunu koda gömer:
`promotion_min_closes < 50` ya da `promotion_min_days < 30` → `ValueError`).

En az **50** karşılaştırılabilir kapanış · en az **30** takvim günü · pozitif maliyet
sonrası beklenti · profit factor > 1 · sıfırı dışlayan GA · düşüş kötüleşmedi · CVaR5
kötüleşmedi · yoğunlaşma kötüleşmedi · yeterli kapsam · çoklu karşılaştırma düzeltmesi
(Šidák, `n=5` → α ≈ 0,0102).

**Örneklem ön koşulu düşerken bağımlı kapılar `PASS` değil `NOT_EVALUABLE_LOW_SAMPLE`
olur.** Otomatik terfi hiçbir koşulda mümkün değildir. Örneklem yeterli olduğunda Deflated
Sharpe / PBO tarzı düzeltme eklenmelidir — bugünkü örneklemde **uygulanamaz**.

---

## 8. Ön-deney dışlaması

`evaluation_start_at`'ten **önce** açılmış her pozisyon — F00033 ve F00034 dâhil, hâlâ
açıklarsa — **`PRE_EXPERIMENT_OBSERVATION_ONLY`**dir. Bunlar simüle portföylere deney
girişlerini görmüş gibi **eklenmez**; motor süzgeci `opened_at < evaluation_start_at` olan
adayı hiç üretmez.

---

## 9. Bilinen sınırlamalar

1. **Deney kârlılığı kanıtlamaz.** Bugün sıfır karşılaştırılabilir kapanış vardır.
2. Filtre-only tasarım gereği challenger'lar şampiyonun **kaçırdığı** fırsatı bulamaz;
   ölçülen tek şey "daha az kötü mü" sorusudur.
3. P0 dışındaki politikalar aynı veriyi paylaşır → **bağımsız deney değildir**; Šidák
   düzeltmesi bunu ancak kısmen telafi eder.
4. Korelasyon yalnız 1s getirileriyle ve bounded pencereyle ölçülür; gerçek varlık-sınıfı
   ortak faktörleri modellenmez.
5. Kayma çoğu kapanışta **ölçülü** ama etki (impact) maliyeti **hiç ölçülmez** (`UNKNOWN`).
6. P3/P4 çıkışı yalnız `position_path` kayıtlarının çözünürlüğü kadar hassastır; yol
   eksikse politika farkı **ölçülemez**.
7. Kök-neden bulgularının çoğu `OBSERVATION_ONLY` sınıfındadır (22/23 kapanış
   `LEGACY_MEMORY`) ve **nedensellik iddia etmez**.

---

## 10. Geri alma

Deney tamamen katkısaldır:

* **Config ile durdurma:** `entry_selectivity.experiment_enabled: false` → tur kancası erken
  döner, hiçbir deney dosyası yazılmaz, aktif davranış değişmez.
* **Kod ile geri alma:** dağıtım öncesi SHA'ya `git reset --hard <rollback_sha>` + servis
  restart. Deneye ait tek kalıcı çıktı üç izole dosyadır; kanonik defter/pozisyon/risk/
  öğrenme durumu deney tarafından **hiç yazılmadığı için** düzeltilecek yan etki yoktur.

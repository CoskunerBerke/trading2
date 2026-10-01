# PROFITABILITY_EXPERIMENT_V1_2 — kapsam-eşli E ile düzeltilmiş beş kollu deney

**Durum:** SHADOW PAPER ONLY · `applied_to_canonical = false` · `auto_promotion = false` ·
terfi bugün **imkânsız** · `pfexp_v1` **SUPERSEDED_INCOMPLETE_ENTRY_INPUT** (salt okunur) ·
`pfexp_v1_1` **SUPERSEDED_E_SCOPE_MISMATCH_DRAINING** (kabul kapalı, takip sürüyor).

> **Bu deney kâr vaat etmez ve kârı kanıtlamaz.** Hiçbir politika kârlı kanıtlanmadı, kazanan
> strateji seçilmedi, otomatik terfi mümkün değil. v1 ve v1.1 kararları yeniden yazılmadı;
> düzeltilmiş deney sıfırdan başlar; eksik girdi ABSTAIN demektir.

Önceki belgeler: `PROFITABILITY_EXPERIMENT_V1.md`, `PROFITABILITY_EXPERIMENT_V1_1.md`.

---

## 1. Doğrulanmış kusur (2026-09-07, F00036 salt okunur semantik denetimi)

| Soru | Kanıt |
| --- | --- |
| Snapshot sayaçları giriş öncesi mi? | Evet. `state` giriş döngüsünden ÖNCE kurulur (`engine_v3.py` `_portfolio_state`), `_entry_capture` fill'den (`ledger2.open`) ÖNCE çağrılır ve değerleri tamponda dondurur; `_refresh_after_fill` yalnız SONRAKİ adayı etkiler. Aynı turda ORCA (rank 0) ve ONDO (rank 1) 12/11/13.0766, sonraki adaylar 13/12/14.2152 kaydetti. |
| 12 / 11 nereden? | 11 futures (10 LONG + 1 SHORT) + 1 STOPSUZ SPOT BNB holding (yönü daima LONG). F00036 kendi sayımında YOK. |
| `portfolio_open_risk_usdt = 13.076588` neyi içerir? | Giriş öncesi futures stop riski **4.850588** + stopsuz spot BNB TAM notional **8.226**. Aday riski (planlanan 1.616, gerçekleşen 1.1386) İÇERMEZ. Kaynak `PortfolioState.total_open_risk_usdt` — "YALNIZ RAPORLAMA, kabul kapısı kullanmaz". |
| `risk_budget_usdt = 6.0` neyi ifade eder? | Futures stop-risk kovasının uygulanan bütçesi (starting_equity 100 × %6; `risk/engine.py` TOTAL_OPEN_RISK kapısı adayın kendi kovasını kullanır, spot notional EKLENMEZ). |
| Sonuç | Aynı birim (USDT), **farklı kapsam**: entry_v1.0.0 E, panelde `diagnostic_ratio_not_enforced` etiketli birleşik değeri futures-only bütçeye böldü (2.179). Kapsam-eşli değer 4.850588 / 6.0 = 0.808. |
| İkinci uyuşmazlık | Karar anı bütçesi starting_equity tabanlı; kapanış-atıf raporu canlı futures equity'den türetiyordu → aynı işlem için iki farklı payda. |

F00036 kararı her makul kapsamda **FILTER**tı (A: p_win 0.293 < 0.3707; futures ısı 0.808 > 0.80;
futures-only aynı yön 10 ≥ 6). Bu karar **v1.1 kanıtıdır** ve v1.2 olarak yeniden etiketlenmez.

---

## 2. Onarım — snapshot (commit `fix(snapshot)`)

Yeni, giriş öncesi, RANKING'de donan, kapsam-eşli alanlar (eski alanlar AYNEN korunur):

| Alan | Kaynak | Kapsam |
| --- | --- | --- |
| `portfolio_futures_stop_risk_usdt` | `PortfolioState.futures_stop_risk_usdt` tanımı (SPOT dışı `risk_usdt` toplamı) | FUTURES_STOP_RISK_BUCKET, aday HARİÇ |
| `same_direction_open_futures` | yalnız futures pozisyonlarının yön sayımı | FUTURES_ONLY, aday HARİÇ |
| `portfolio_scope` | etiket sözlüğü | eski üç alan COMBINED_SPOT_FUTURES tanı; `risk_budget_usdt` FUTURES_STOP_RISK_BUCKET |

Ölçülmüş sıfır 0.0'dır; ölçülemeyen futures riski `None`/MISSING (uydurma yok). Eski satırlar
yeni alanları taşımaz.

## 3. Onarım — challenger E `entry_v1.1.0` (commit `fix(entry)`)

* Isı = `portfolio_futures_stop_risk_usdt` / **snapshot'ta donmuş** `risk_budget_usdt`;
  yoğunlaşma = `same_direction_open_futures`. Eşikler AYNI: 0.80 ve 6.
* Çağıranın verdiği bütçe (rapor anındaki canlı equity) **kaydedilir, kullanılmaz** →
  giriş anı E ile kapanış-atıf E aynı snapshot için bayt bayt aynı.
* Gerekli alan eksikse **açık ABSTAIN** (bu ailede üçüncü değer); birleşik eski alanlara
  DÜŞÜLMEZ, onlar yalnız `diagnostics` bloğunda `used_for_decision=false` ile görünür.
* `entry_v1.0.0` karar/gerekçe/kanıt sözlüğü bayt bayt korunur; snapshot kendi sürümüyle
  okunur (`EntryChallengerConfig.for_snapshot`); eski satır yeni anlamla yorumlanmaz.
* Deney adaptörü (`profitability_ae.point_in_time_ae`) deneyin istediği giriş politikası
  sürümünü zorunlu kılar: farklı sürümle yazılmış snapshot →
  `ENTRY_SNAPSHOT_POLICY_VERSION_MISMATCH` ABSTAIN.

## 4. Sürümleme (commit `feat(experiment)`)

| Öğe | `pfexp_v1` | `pfexp_v1_1` | `pfexp_v1_2` |
| --- | --- | --- | --- |
| policy_version | pfexp_v1.0.0 | pfexp_v1.1.0 | pfexp_v1.2.0 |
| config_id | `e3863761d50c79c3` | `5d3549e72a2049ff` | yeni deterministik |
| giriş politikası (A/E) | — (kapanış atıfı) | entry_v1.0.0 | **entry_v1.1.0** |
| statü | SUPERSEDED_INCOMPLETE_ENTRY_INPUT | SUPERSEDED_E_SCOPE_MISMATCH_DRAINING → _COMPLETE | ACTIVE_SHADOW |
| kabul | kapalı (salt okunur) | **kapalı** (yalnız takip) | açık |
| dosyalar | `profitability_experiment*` | `profitability_experiment_v1_1_*` | `profitability_experiment_v1_2_*` |

`entry_policy_version` deney sürümünden **türetilir** (kimliğe alan eklenmez): v1/v1.1
config_id'leri değişmeden yeniden kurulur — testlerde pin'lidir.

### 4.1 v1.1 drain (kabul kapalı / yalnız takip)

* Motor v1.1 kimliğini kimlik dosyasındaki donmuş başlangıçtan BİREBİR yeniden kurar ve v1.1
  kitabının kimliğiyle karşılaştırır; olay defterinde yabancı kimlik arar. Herhangi biri
  uyuşmazsa **drain kurulmaz** (`_READ_ONLY`; F00036 simülasyonları dondurulmuş açık kalır).
* Drain turunda 1. adım (yeni kabuller) HİÇ çalışmaz; ikinci kilit olarak karar/dolum türü
  hiçbir olay deftere giremez ve sayılır (`rejected_admission_events`).
* Yalnız mevcut simüle pozisyonlara (F00036: P0/P2/P3) mark ve kanonik kapanış aynalanır;
  P1/P4 pozisyonsuz kalır. v1.2 başlangıcından sonra açılan işlem v1.1'e giremez.
* Bütün simüle pozisyonlar kapanınca statü `SUPERSEDED_E_SCOPE_MISMATCH_COMPLETE`.
* Eski olaylar düzenlenmez; yalnız EKLENİR. Karar olaylarının sha256'sı v1.2 raporunun
  `superseded_versions` bölümünde tur tur görünür.

### 4.2 F00036

Yalnız `pfexp_v1_1` kanıtında; `pfexp_v1_2`de `PRE_EXPERIMENT_OBSERVATION_ONLY` (v1.2
başlangıcından önce açıldı) — doğal kapanışı v1.1'e aynalanır, v1.2'ye asla girmez.

## 5. P2 (değişmedi)

P2 yoğunlaşmayı YALNIZ kendi başlangıç-sonrası izole simüle defterinde ölçer; kanonik
defterdeki mevcut pozisyonları devralmaz. Yeni bir deneyin başında P2, P0 ile aynı kararı
verebilir. Bu amaçlanan izole-defter semantiğidir ve kanonik portföyün çeşitlendiğinin kanıtı
DEĞİLDİR. Kaynak sha'sı testte pin'lidir.

## 6. Pano

`/learning` → «Kârlılık Deneyi»: dürüstlük beyanları; **aktif v1.2** (kimlik, giriş politikası,
E kapsamı, son kararların kapsam alanları — futures stop riski, futures bütçesi, futures ısı
oranı, birleşik tanı maruziyeti, futures-dışı bileşen (stopsuz spot), birleşik ısı (tanı),
aynı yön futures / birleşik — beş politika metrikleri, karşılaştırılabilir kapanışlar, kapılar,
düşük örneklem, bütünlük); **kabul-kapalı v1.1** (drain statüsü, kapsam kusuru açıklaması,
F00036'nın değiştirilmemiş kararları, kârlılık sonucu YOK); **tarihsel v1**. Birleşik tanı
değeri hiçbir yerde futures limit ihlali olarak etiketlenmez. API:
`/api/state/profitability_experiment_v1_2`, `_v1_1`, `profitability_experiment`.

## 7. Bilinen sınırlamalar

1. Deney kârlılığı kanıtlamaz; v1.2 sıfır karşılaştırılabilir kapanışla başlar.
2. E'nin futures bütçesi karar anı starting_equity tabanlıdır (PAPER_RESEARCH); canlı equity
   tabanlı profil seçilirse yine karar anındaki değer donar.
3. v1.1 drain yalnız F00036'nın P0/P2/P3 simülasyonlarını tamamlar; v1.1 hiçbir zaman beş kollu
   karşılaştırma üretmez.
4. Terfi kapıları (≥50 karşılaştırılabilir kapanış, ≥30 gün, diğerleri) AYNEN korunur.

# Kanıt Onarımı V1 / V1.1 — ölçülmüş kesit cezaları, kâr koruması, aday sonuç etiketleme

Tarih: 2026-09-09 · Taban: `9be55d1` · Dal: `feature/evidence-repairs-v1`
V1 (8c99b8f) kesitleri SERT kapıyla kapatıyordu ve VPS'e dağıtıldı. **V1.1 operatör kararıyla sert kapıları
kaldırır:** hiçbir coine ya da yöne yasak konmaz; ölçülen açık YUMUŞAK kanıt cezası olarak beklentiden düşülür.

Bu sürüm bir strateji fikri değil, bir **ölçümün** sonucudur. Önce ölçüm, sonra onarımlar, bayraklar, testler,
dağıtım ve geri alma. Her rakam üretim durumundan yeniden türetilmiştir.

---

## 1. Ölçüm

### 1.1 Kapanmış defter (27 işlem, `futures_ledger.json`)

| Ölçü | Değer |
|---|---|
| Kazanma oranı | %25,9 (7/27) |
| Kazanan ort. R / kaybeden ort. R | +1,91 / −1,07 |
| Başa baş için gereken kazanma oranı | %36,0 |
| İşlem başına gerçekleşen beklenti | **−0,300R** |
| Sermaye | 100,00 → 97,10 USDT (gerçekleşen −5,33) |
| Çıkış türleri | `stop` 20 (hepsi zarar, −14,71) · `hedef2` 5 (hepsi kâr, +8,76) · `başa-baş stop` 2 (kâr, +0,62) |

Girişte verilen `expected_r` 27 işlemin tamamında 1,77–1,99 arasında (ayırt etme gücü sıfır);
tahmin +1,93R, gerçekleşen −0,30R. Sebep: `coinhead/head.py` `_cost_and_r` ödül/risk **oranı**
hesaplar, olasılık içermez; plan geometrisi hedefi her zaman stop mesafesinin 2 katına koyar.

### 1.2 Aday havuzu (263 bağımsız kurulum, `entry_snapshot.jsonl`)

`entry_snapshot.jsonl` 2.677 aday değerlendirmesini giriş/stop/hedef fiyatlarıyla tutuyordu ama
hiçbiri sonuçla etiketlenmemişti. (sembol, yön, gün) başına ilk aday alınarak 263 bağımsız kurulum
çıkarıldı; 239'u Binance **vadeli** (fapi) 1h barlarıyla etiketlendi.

Kural (önceden kayıtlı): giriş = ts anındaki piyasa açılışı (motor böyle dolduruyor); stop/hedef
plandan; ufuk 7 gün; gidiş-dönüş 0,16R maliyet; aynı bar içinde stop+hedef → STOP.

| Kesit | n | Ort. R | Kazanan | %95 alt | %95 üst |
|---|---:|---:|---:|---:|---:|
| kripto LONG (spot'ta listeli) | 148 | **+0,155** | %42,6 | −0,048 | +0,357 |
| yalnız-vadeli LONG (tokenize hisse/emtia ağırlıklı) | 78 | **−0,204** | %33,3 | −0,428 | +0,020 |
| yalnız-vadeli SHORT | 9 | **−0,578** | %11,1 | −1,112 | −0,044 |
| kripto SHORT | 4 | **−0,745** | %0,0 | −1,062 | −0,427 |
| tüm havuz | 239 | −0,005 | %37,7 | −0,154 | +0,144 |

Defter bağımsız doğruluyor: 9 kripto LONG +0,49R (5 kazanan); diğer 18 işlem −0,70R (2 kazanan).
Botun 27 işleminin 17'si tokenize hisse/emtia, 5'i SHORT.

Seçim testi (n=10 açılan vs 229 açılmayan): +0,05R fark, p=0,90 → seçim ne yardım ediyor ne zarar.
Simülatör doğrulaması: 4 gerçek kapanışta işaret 4/4 tutarlı; kazananları ~0,4R eksik gösteriyor → muhafazakâr.

### 1.3 Düşen üç onarım hipotezi (152 kripto kurulumu, aynı kural)

| Hipotez | Sonuç | Neden |
|---|---:|---|
| zaman çıkışı (12 saat) | +0,055R vs 7 gün +0,263R | kenar tutma süresiyle ARTIYOR |
| giriş/stop tutarlılığı (stopu dolumdan yeniden ölç) | +0,002R | kötü giriş stopu da hedefi de yaklaştırıyor |
| limit giriş (plan seviyesinde, 6–48 sa) | en iyi +0,031R vs +0,128R | ters seçilim: yalnız düşmeye devam edenler doluyor |

### 1.4 Açık defter (12 pozisyon)

Stop'u yukarı taşıyan tek canlı mekanizma TP1'e dokunulmasını şart koşuyordu (`futures_ledger.py` `tick`);
12 pozisyonun 9'unda stop ilk yerindeydi. Futures'ta `Position.trailing_pct` hiçbir yerde atanmıyor.
Ölçüm anında ZEN +%12,3 MFE ile stop girişin %13 altında, ETH +%5,0 MFE ile %5,7 altında.

---

## 2. Onarımlar

### 2.1 Kesit açıkları YUMUŞAK kanıt cezası (V1.1 — yasak yok)

* `decision_gates.py`: iki SOFT_EVIDENCE kodu — `SHORT_SEGMENT_PENALTY`, `FUTURES_ONLY_SEGMENT_PENALTY`.
  (V1'in `SHORT_DISABLED` / `NOT_SPOT_LISTED` sert kodları kaldırıldı.)
* `engine_v3.py` `_assess_opportunities`: `futures_v3.short_penalty_r > 0` iken SHORT adaya,
  `universe.futures_only_penalty_r > 0` iken Binance spot'ta listeli olmayan futures adaya
  `GateLedger.penalise(...)` ile ceza eklenir. Ceza `opportunity.soft_evidence`de kod + miktar + gerekçe ile
  günlüğe düşer. **Mekanik:** ceza yalnız `conservative_net_edge_r`den düşer; net beklentisi pozitif aday
  hiçbir zaman sıfırlanmaz, en kötü `RESEARCH_MULTIPLIER` (0,25×) boyutunda açılır. Toplam yumuşak ceza
  0,60R ile sınırlıdır (`soft_penalty_r(cap)`).
* `market/spot_listing.py`: resmi spot `exchangeInfo`'dan TRADING sembol kümesi; `state/spot_listing.json`
  (atomik, TTL 24 sa). `is_listed()` üç değerli: True/False/None. Veri YOKSA ceza fail-safe uygulanır
  (yasak değil). Yenileme arızası eski önbelleği korur; boş sonuç önbelleği ezmez.
* Tarama önü eleme **kaldırıldı** (V1'de vardı; fiilen yasaktı). Yalnız-vadeli adaylar analize girer, karar
  cezayla verilir.

### 2.2 MFE tabanlı başa-baş (kâr koruması) — değişmedi

* `accounting/futures_ledger.py`: `breakeven_at_mfe_r` (Decimal, 0 = kapalı). `tick()` içinde MFE
  `initial_stop`'a göre R'ye çevrilir; eşik aşılınca stop **gerçek başa-başa** taşınır. TP1 beklenmez.
  Yalnız sıkılaştırır, mark'ın yanlış tarafına konmaz, pozisyon başına bir kez (`meta.be_by_mfe`).
  Sonraki stop dokunuşu `EXIT_BE_STOP` etiketlenir. Knob defterle serileştirilir; eski defter 0 ile yüklenir.

### 2.3 Aday sonuç etiketleme — değişmedi

* `learn/outcome_labeler.py`: ufku dolan her aday üçlü bariyerle etiketlenir ve **ayrı** dosyaya
  (`state/entry_outcomes.jsonl`) yazılır. `entry_snapshot.jsonl` değişmez. Birleştirme anahtarı `candidate_id`.
* `engine_v3.py` `_label_entry_outcomes`: tur sonunda, yalnız SICAK satırlar, tur başına en fazla
  `outcome_max_symbols_per_tour` sembol, arıza turu durdurmaz. Durum: `state/entry_outcomes_status.json`.
* Bu sürüm yalnız veri üretir; öğrenme katmanının etiketleri tüketmesi ayrı iştir.

---

## 3. Bayraklar

| Anahtar | Kod varsayılanı | `config.yaml` (bu dal) | Etki |
|---|---|---|---|
| `futures_v3.short_penalty_r` | `0.0` | **`0.50`** | SHORT adaya 0,50R yumuşak ceza (ölçülen ~0,6R, n=13 için çekildi) |
| `universe.futures_only_penalty_r` | `0.0` | **`0.35`** | spot'ta listesiz futures adaya 0,35R yumuşak ceza (ölçülen ~0,37R) |
| `universe.spot_listing_ttl_minutes` | `1440` | `1440` | önbellek tazeliği |
| `futures_v3.breakeven_at_mfe_r` | `0.0` | **`1.0`** | MFE +1,0R'de stop → başa-baş |
| `learning_v3.outcome_labeling_enabled` | `false` | `false` (ayrı GO) | etiketleme |
| `learning_v3.outcome_horizon_hours` / `outcome_cost_r` / `outcome_max_symbols_per_tour` | `168` / `0.16` / `15` | aynı | ufuk / maliyet / oran bütçesi |

Kod varsayılanları eski davranışı **birebir korur**; değişiklik yalnız config ile açılır. Pratik etki: SHORT ya da
yalnız-vadeli bir aday, kanıtı olağanüstü değilse araştırma boyutunda (0,25×) açılır; hiçbir aday yalnız kesiti
yüzünden reddedilmez.

---

## 4. Testler

* `tests/test_evidence_repairs_v1.py` — kodların SOFT olduğu ve sert engel olarak kullanılamadığı, config
  varsayılanları/yükleme, `SpotListing`, motor cezaları (küçültür ama sıfırlamaz, LONG etkilenmez, veri yokken
  fail-safe, tavan 0,60), `ensure_spot_listing` ağ disiplini, tarama elemesinin kaldırıldığı, MFE başa-baş.
* `tests/test_outcome_labeler_v1.py` — üçlü bariyer, `label_pending`, motor kancası.

---

## 5. Dağıtım ve geri alma

Dağıtım: doğrulanmış yedek → `.last_good_commit` → bundle ile ff-only → import/config/bayrak kontrolü → doctor →
restart → sağlık. V1 kaydı: araştırma ağacında `docs/EVIDENCE_REPAIRS_V1_DEPLOYMENT.md`.
Geri alma: `bash deploy/rollback.sh` ya da cezaları `0.0` yapıp restart (eski davranış). Zaten taşınmış bir
başa-baş stop'u kasıtlı olarak geri alınmaz.

## 6. Bilinen sınırlar

* Ölçüm tek bir üç haftalık pencere; kesit ayrımı sonucu gördükten sonra yapıldı (doğal 2×2, defter bağımsız
  doğruluyor), SHORT n=4/9. Kripto LONG **kanıtlanmış kenar değildir** (CI sıfırı içeriyor).
* Yumuşak ceza, sert kapıya göre daha az tasarruf sağlar: ölçülen kaybeden kesitlerden bazı işlemler küçük
  boyutta yine alınır. Bu, operatörün bilinçli tercihidir.
* Spot listeleme kuralı birkaç yalnız-vadeli coini de (KAS, VVV) cezalandırır.
* Etiketleme yalnız sıcak snapshot satırlarını görür; arşive düşenler için çevrimdışı yol gerekir.

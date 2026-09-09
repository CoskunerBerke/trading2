# Kanıt Onarımı V1 — ölçülmüş kaybeden kesitler, kâr koruması, aday sonuç etiketleme

Tarih: 2026-09-09 · Taban: `9be55d1` (`feature/quant-evaluation-v1`, VPS'te canlı) · Dal: `feature/evidence-repairs-v1`

Bu sürüm bir strateji fikri değil, bir **ölçümün** sonucudur. Önce ölçüm, sonra üç onarım, sonra
bayraklar, testler, dağıtım ve geri alma. Her rakam üretim durumundan yeniden türetilmiştir; önceki
oturum raporlarına dayanmaz.

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
Simülatör doğrulaması: 4 gerçek kapanışta işaret 4/4 tutarlı; kazananları ~0,4R eksik gösteriyor
(hedef t1 alınıyor, defter t2'ye gidiyor) → muhafazakâr.

### 1.3 Düşen üç onarım hipotezi (152 kripto kurulumu, aynı kural)

| Hipotez | Sonuç | Neden |
|---|---:|---|
| zaman çıkışı (12 saat) | +0,055R vs 7 gün +0,263R | kenar tutma süresiyle ARTIYOR |
| giriş/stop tutarlılığı (stopu dolumdan yeniden ölç) | +0,002R | kötü giriş stopu da hedefi de yaklaştırıyor |
| limit giriş (plan seviyesinde, 6–48 sa) | en iyi +0,031R vs +0,128R | ters seçilim: yalnız düşmeye devam edenler doluyor |

"İyi giriyor, kötü çıkıyor" tezi bu veride tutmuyor; tersine erken çıkmak zarar ettiriyor.

### 1.4 Açık defter (12 pozisyon)

Stop'u yukarı taşıyan tek canlı mekanizma TP1'e dokunulmasını şart koşuyor
(`accounting/futures_ledger.py` `tick`); 12 pozisyonun 9'unda stop ilk yerinde. Futures'ta
`Position.trailing_pct` hiçbir yerde atanmıyor. Ölçüm anında ZEN +%12,3 MFE ile stop girişin %13
altında, ETH +%5,0 MFE ile %5,7 altında.

---

## 2. Onarımlar

### 2.1 Kaybeden kesitler SERT kapıyla kapanır

* `decision_gates.py`: iki yeni HARD_SAFETY kodu — `SHORT_DISABLED`, `NOT_SPOT_LISTED`.
* `engine_v3.py` `_assess_opportunities`: `futures_v3.allow_short=false` iken SHORT adaylar,
  `universe.require_spot_listing=true` iken Binance spot'ta listeli olmayan futures adaylar
  `GateLedger.block(...)` ile engellenir → `opportunity.hard_block_codes`, huni
  `size_multiplier_zero`, karar günlüğü. Sessiz atlama yok.
* `market/spot_listing.py` (yeni): resmi spot `exchangeInfo`'dan TRADING sembol kümesi;
  `state/spot_listing.json` (atomik, TTL 24 sa). `is_listed()` üç değerli: True/False/**None**.
  Veri YOKSA kapı fail-closed (yeni giriş açılmaz; kod günlüğe düşer). Yenileme arızası eski
  önbelleği korur; boş sonuç önbelleği ezmez.
* `engine_v3.py`: `_spot_provider_factory`, `ensure_spot_listing` (tur başında, kapı açıkken,
  günde ~1 istek); tarama adayları derin analize alınmadan önce elenir (çekirdek coinler ve
  **açık pozisyonlar korunur** — çıkışlar etkilenmez).

### 2.2 MFE tabanlı başa-baş (kâr koruması)

* `accounting/futures_ledger.py`: `breakeven_at_mfe_r` (Decimal, 0 = kapalı). `tick()` içinde MFE
  `initial_stop`'a göre R'ye çevrilir; eşik aşılınca stop **gerçek başa-başa**
  (`break_even_price`: giriş + komisyon + kayma) taşınır. TP1 beklenmez. Yalnız sıkılaştırır,
  mark'ın yanlış tarafına konmaz, pozisyon başına bir kez (`meta.be_by_mfe`, kalıcı).
  Sonraki stop dokunuşu `EXIT_BE_STOP` etiketlenir.
* Knob defterle birlikte serileştirilir; eski defter dosyaları 0 ile yüklenir.

### 2.3 Aday sonuç etiketleme

* `learn/outcome_labeler.py` (yeni): ufku dolan her aday üçlü bariyerle etiketlenir ve **ayrı**
  dosyaya (`state/entry_outcomes.jsonl`, salt ekleme) yazılır. `entry_snapshot.jsonl` değişmez;
  mevcut tüketiciler ve rotasyon/arşiv etkilenmez. Birleştirme anahtarı `candidate_id`.
  Giriş referansı ts'deki bar açılışı; aynı bar → STOP; zaman aşımı piyasa değeriyle, `label=None`.
* `engine_v3.py` `_label_entry_outcomes`: tur sonunda, yalnız SICAK satırlar (arşiv açılmaz),
  tur başına en fazla `outcome_max_symbols_per_tour` sembol (en eski önce), arıza turu durdurmaz.
  Durum: `state/entry_outcomes_status.json` (sayaçlar + özet).
* Bu sürüm yalnız **veri üretir**; öğrenme katmanının bu etiketleri tüketmesi ayrı iştir.

---

## 3. Bayraklar

| Anahtar | Kod varsayılanı | `config.yaml` (bu dal) | Etki |
|---|---|---|---|
| `futures_v3.allow_short` | `true` | **`false`** | SHORT adaylar `SHORT_DISABLED` |
| `universe.require_spot_listing` | `false` | **`true`** | spot'ta listeli olmayan futures adaylar `NOT_SPOT_LISTED` |
| `universe.spot_listing_ttl_minutes` | `1440` | `1440` | önbellek tazeliği |
| `futures_v3.breakeven_at_mfe_r` | `0.0` | **`1.0`** | MFE +1,0R'de stop → başa-baş |
| `learning_v3.outcome_labeling_enabled` | `false` | *(bu dalda kapalı; ayrı GO ile)* | etiketleme |
| `learning_v3.outcome_horizon_hours` | `168` | `168` | ufuk |
| `learning_v3.outcome_cost_r` | `0.16` | `0.16` | maliyet |
| `learning_v3.outcome_max_symbols_per_tour` | `15` | `15` | oran bütçesi |

Kod varsayılanları eski davranışı **birebir korur**; değişiklik yalnız config ile açılır.

---

## 4. Testler

* `tests/test_evidence_repairs_v1.py` — kapı kodları, config varsayılanları/yükleme, `SpotListing`
  (üç değerli, kalıcı, bayat-ama-kullanılabilir, boş ezmez), motor kapıları (SHORT, spot; fail-closed;
  bayrak kapalıyken eski davranış), `ensure_spot_listing` ağ disiplini, MFE başa-baş (eşik, TP1'siz,
  varsayılan kapalı, SHORT simetrik, asla gevşetmez, mark'ı geçmez, kalıcılık, eski defter).
* `tests/test_outcome_labeler_v1.py` — üçlü bariyer, çerçeve çözümleme, `label_pending` (ufuk,
  idempotent, spot/geometri atlama, tavan en-eski-önce, sağlayıcı arızası izole), motor kancası
  (kapalıyken ağa çıkmaz, açıkken durum belgesi, arşiv açılmaz).

---

## 5. Dağıtım

1. Yerel: tam test paketi yeşil.
2. GitHub push (401 alınırsa `git bundle` ile; bkz. önceki dağıtım notları).
3. VPS: yedek → kod → `update.sh` (worker + dashboard yeniden başlar). Kesinti beklentisi < 1 dk.
4. İlk turdan sonra doğrula: `state/spot_listing.json` var ve `n` > 1000; `decision_funnel.json`'da
   `size_multiplier_zero` artışı; `opportunity.hard_block_codes` içinde `NOT_SPOT_LISTED`/`SHORT_DISABLED`;
   açık pozisyonlar aynen duruyor (çıkışlar etkilenmedi).
5. Bir hafta: kripto LONG kesitinin ileriye dönük ölçümü (bkz. §6). Etiketleme bayrağı ayrı GO ile açılır.

## 6. Geri alma

* Davranış: `config.yaml`'da `allow_short: true`, `require_spot_listing: false`,
  `breakeven_at_mfe_r: 0.0` → eski davranış; kod geri alınmadan da mümkün.
* Defter: `breakeven_at_mfe_r` knob'u 0 yapılınca yeni taşıma olmaz; ZATEN taşınmış stoplar
  (`meta.be_by_mfe`) yerinde kalır — bu kasıtlıdır (kâr koruması geri alınmaz), gerekirse elle.
* Etiketleme: bayrak kapalı → dosya büyümez; `entry_outcomes.jsonl` silinebilir (türev veri).
* Kod: `git revert` tek commit.

## 7. Bilinen sınırlar

* Ölçüm tek bir üç haftalık pencere; kesit ayrımı sonucu gördükten sonra yapıldı (doğal 2×2, defter
  bağımsız doğruluyor), SHORT n=4/9. Kripto LONG **kanıtlanmış kenar değildir** (CI sıfırı içeriyor);
  bu onarım kanamayı durdurur, kâr garantisi vermez.
* Spot listeleme kuralı birkaç yalnız-vadeli coini de (KAS, VVV) kapsam dışı bırakır.
* Etiketleme yalnız sıcak snapshot satırlarını görür; arşive düşenler için çevrimdışı yol gerekir.

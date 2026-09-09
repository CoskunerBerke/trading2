# Funding mutabakatı ve portföy maruziyeti — ölçüm (2026-09-09/10)

Bu belge **ölçümdür**. Funding onarımı ayrı bir dalda yürüyor; maruziyet tarafında politika
değişikliği **yapılmadı**, yalnız gözlem eklendi.

---

## 1. Funding — "15× şişik" iddiası yeniden üretildi ve büyüklüğü düzeltildi

**Yöntem:** 29 kapanmış işlemin her biri için, defterin **kendi kuralıyla** (00/08/16 UTC
settlement, `qty × mark × rate`, `rate > 0` iken LONG öder) ama **gerçek** Binance USD-M
`fundingRate` geçmişi ve **her settlement'ın kendi mark'ı** kullanılarak yeniden hesaplandı.
Betik: `scripts/research/funding_recon.py` (public API, yanıtlar diske önbelleklenir).

| | Değer |
|---|---:|
| Binance USD-M perpetual olan işlem | **29 / 29** |
| Eşleşen settlement | **hepsi** (`due == matched`, mükerrer yok, kayıp dönem yok) |
| Kayıtlı funding toplamı | **+0.067382 USDT** |
| Gerçek funding toplamı | **+0.004014 USDT** |
| Net hata | +0.063369 USDT |
| **İşlem başına mutlak hatanın toplamı** | **0.209232 USDT** |
| Aynı 29 işlemin gerçekleşmiş net PnL'i | −7.026514 USDT |
| Mutlak hatanın gerçekleşmiş zarara oranı | **%3.0** |

**"15×" rakamı yanıltıcıdır.** 0.067382 / 0.004014 ≈ 16.8, ama bu **birbirini götüren iki işaretli
toplamın** oranıdır; funding kaleminin net toplamı sıfıra yakın olduğu için oran patlar. Zararın
büyüklüğü olarak okunamaz. Doğru büyüklük **0.209 USDT mutlak hata**, yani gerçekleşmiş zararın
**%3'ü**. Gerçek ama **ikincil** bir muhasebe kusurudur.

En büyük tekil sapmalar:

| İşlem | Sembol | settlement | Kayıtlı | Gerçek | Hata |
|---|---|---:|---:|---:|---:|
| F00015 | STX/USDT | 15 | +0.144644 | +0.040753 | **+0.103892** |
| F00004 | BZ/USDT | 18 | +0.028859 | +0.008606 | +0.020253 |
| F00034 | NVDA/USDT | 13 | −0.041275 | −0.022832 | −0.018443 |
| F00024 | CL/USDT | 5 | 0.000000 | +0.017387 | −0.017387 |

Sapma **settlement sayısıyla** büyüyor; bu, kök nedenin doğrudan imzasıdır.

### Kök neden — raporda değil, tahakkukta

`engine_v3.py` (≈931–940) her turda **o anki** funding oranından bir sözlük kurup
`static_rates(funding)` veriyor. `static_rates` settlement zaman damgası argümanını **hiç
kullanmaz**. `funding.py::FundingSchedule.accrue()` de kaçırılan **bütün** settlement'lara aynı
tek oranı **ve** aynı tek mark'ı uyguluyor. Bir settlement'lık normal turda etkisi ihmal
edilebilir; kaçırılmış uzun pencerede hata settlement sayısıyla çarpılır.

**Elenen hipotezler (ölçülerek):**

* **Birim hatası yok.** `agents/market.py` ve `coinhead/specialists.py` ham oranı `× 100` ile
  yüzdeye çevirir, `engine_v3` `/ 100` ile kesre geri döner. Uçtan uca doğru.
* **Mükerrer tahakkuk yok.** Beklenen settlement sayısı ile eşleşen sayısı 29/29 işlemde eşit.
* **İşaret doğru.** Her işlemde kayıtlı ve gerçek değerin işareti aynı.
* **Sıfır kayıtların hepsi kusur değil.** NVDA F00018, XPD, SPCX, NATGAS gerçekten `0.00000000`
  oranlıydı (tokenize enstrümanlarda olağan). KORU, CL, BMNR, MSFT, AAPL'de ise gerçek funding
  vardı ve hiç tahakkuk etmemişti — oran hiç gelmediği için `accrue()` fail-closed bekledi ve
  pozisyon o oran hiç gelmeden kapandı.
* **`ops/gap.py` bu kusuru TAŞIMAZ.** Kesinti uzlaştırması saat anahtarlı gerçek bir
  `rate_lookup` kullanır. Kusur yalnız normal tur yolundadır.

### Onarımın doğrulanması — iki bağımsız uygulama, aynı sayı

`scripts/research/funding_recon.py` defterin kuralını sıfırdan yeniden yazar.
`scripts/research/funding_endtoend_check.py` ise **üretim kodunu** (`FundingRateCache` +
`FundingSchedule.accrue` + `chained_rates`) gerçek venue verisiyle çalıştırır. İkisi 6 hanede
birebir aynı sonucu verir:

| İşlem | settlement | ESKİ (tek anlık oran) | **YENİ (üretim yolu)** | Bağımsız mutabakat | Deftere kayıtlı |
|---|---:|---:|---:|---:|---:|
| F00015 STX | 15 | +0.004620 | **+0.040753** | +0.040753 | +0.144644 |
| F00004 BZ | 18 | +0.000000 | **+0.008606** | +0.008606 | +0.028859 |
| F00034 NVDA | 13 | −0.041866 | **−0.022832** | −0.022832 | −0.041275 |

"ESKİ" sütunu ölçüm anındaki son oranla hesaplanmıştır, yani defterin kaydettiği tarihsel değerle
birebir aynı olması beklenmez; gösterdiği şey tek bir anlık oranın kaçırılmış dönemlere
uygulanmasının **ne kadar oynadığıdır**.

**Geçmiş etki yeniden yazılmaz.** Kanonik defter olduğu gibi kalır; yukarıdaki tablo mutabakat
kaydıdır.

---

## 2. Kayıp modeli — sabit −1.0R veri tarafından desteklenmiyor

29 kapanışın 22'si zarar; `r_multiple` zaten ücret/funding/kayma sonrası **NET**
(`expectancy_basis = NET_OUTCOME`, dolayısıyla `cost_r = 0` ve maliyet ikinci kez düşülmez).

| | Değer |
|---|---:|
| Zarar sayısı | 22 (hepsi `stop`, hiçbirinde TP1 kısmi çıkışı yok) |
| Ortalama |R| | **1.0774** |
| Medyan / sd | 1.0649 / 0.0831 |
| %95 güven aralığı (ortalama) | **1.0419 – 1.1130** |
| Risk ağırlıklı ortalama | 1.0579 |
| Ayrıştırma | brüt 1.0426 R (stop ötesi gap/kayma) + 0.0349 R (ücret + funding) |

Sabit **1.0 değeri güven aralığının dışındadır.**

**Ama etkisi doğrudan 0.077R değildir.** `opportunity.hierarchical_expectancy` içinde
`realised_win_r = (exp_r + (1−p)·L) / p` ve `assess` içinde `gross = p·W − (1−p)·L`. Karışım
ağırlığı `w = n/(n+20)` **1** olduğunda `L` cebirsel olarak **sadeleşir** ve `gross = exp_r` olur;
`L` yalnız soğuk başlangıç geometri karışımının `(1−w)` payından etkiler. Üretimde `sample_size`
medyanı **4** (en fazla 24) olduğundan `w ≈ 0.17`'dir, yani karışım geometri ağırlıklıdır ve `L`'nin
etkisi vardır — ama "her adayda +0.077R şişme" biçiminde okunamaz. Gerçek büyüklük ayrı dalda
2797 aday üzerinde sayısal olarak ölçülüyor.

**İlgili, henüz kapanmamış tutarsızlık:** `_assess_opportunities`, `hierarchical_expectancy`'nin
döndürdüğü `p_win`'i `d.p_win` (kalibre LearnerV2 değeri) ile **ezer** ama `avg_win_r`'yi
**korur** — oysa `avg_win_r` diğer olasılıkla geri çözülmüştür. İki hiyerarşi ayrıca farklı yaprak
anahtarları kullanır (`win`: `symbol|setup`, `exp_r`: `setup|side`). Ayrı dalda inceleniyor.

---

## 3. Portföy riski — kanıtlı kusur ile politika sorusu ayrıldı

**Ölçüm (dondurulmuş defter, 14 açık pozisyon, başlangıç özkaynağı 100 USDT, equity 95.95):**

| | Değer |
|---|---:|
| Mevcut stoplardan futures stop-riski | 5.8321 USDT = **%5.83** (tavan %6 → kapı **bağlayıcı**) |
| İlk stoplardan ölçülseydi | 8.5308 USDT = %8.53 (tavanın %42 üstü) |
| Brüt nominal | **177.45 USDT = özkaynağın 1.85 katı** |
| Kullanılan marj | 63.75 USDT = özkaynağın **%66.4**'ü |
| Stop-riski sıfırlanmış pozisyon | **6 / 14** (stop girişte ya da ötesinde) |

**Bu bir kod kusuru DEĞİL.** Kapı kendi tanımını doğru uygular: stop başa-başa taşındığında o
pozisyonun *stop riski* gerçekten ~0'dır. Kalan risk gap ve korelasyondur, stop riski değil.
"Sınırsız" nitelemesi de kanıtlanmadı: bütçe şu anda 5.83/6.00 ile bağlayıcıdır.

**Kanıtlı olan ayrışma:** `risk/profiles.py` yorumu PAPER_RESEARCH kararının "toplam açık risk
(%6), işlem başına risk tavanı (%2), **margin, liq buffer** ve same-symbol kapılarıyla" verildiğini
söyler. Oysa profilde `futures_margin_utilization_cap_pct = None` ve
`min_liquidation_buffer_mult = None`'dır; `risk/engine.py` bu iki kapıyı `is not None` koşuluyla
sardığı için **hiç çalışmazlar**. Belge ile efektif config ayrışmıştır.
`tests/test_exposure_observability_v1.py::test_06` bu ayrımı açıkta tutar; **değerler
DEĞİŞTİRİLMEDİ.**

**Bu turda yapılan (yalnız gözlem, hiçbir kapı/limit değişmedi):** `risk.json` artık
`futures_notional_usdt`, `futures_notional_to_equity`, `futures_zero_stop_risk_positions` ve
`futures_zero_stop_risk_notional_usdt` yayımlar. Böylece "bütçe boş" ile "maruziyet küçük"
karıştırılamaz.

**Operatöre bırakılan politika kararı (uygulanmadı):** brüt nominal ya da marj kullanımı için ayrı
bir tavan istenip istenmediği. Uygulanırsa yalnız **yeni girişleri** etkiler; açık pozisyonlara
dokunmaz. Tek satırlık karşılıkları profilde hazırdır
(`futures_margin_utilization_cap_pct`, `min_liquidation_buffer_mult`).

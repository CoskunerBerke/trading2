# Dağıtım kaydı — bar provenansı, funding, kayıp modeli, maruziyet gözlemi (2026-09-10)

**Taban:** `1c4cba1` (VPS'te çalışan sürüm) · **Hedef:** bu dalın ucu, `feature/evidence-repairs-v1`
**Yalnız PAPER.** Gerçek para modu açılmadı, sermaye/kaldıraç/toplam risk limitleri artırılmadı,
kanonik defter ve öğrenme kayıtları yeniden yazılmadı, açık pozisyonlara dokunulmadı.

---

## 1. DÜZELTİLDİ VE DAĞITILDI

| # | Onarım | Kanıt | Dosya |
|---|---|---|---|
| 1 | **Bar provenansı** — giriş öncesi kapanmış bar artık MAE/MFE'ye yazamaz ve stop/hedef/likidasyon tetikleyemez | 29 kapanışın 5'inde kayıtlı MFE'nin, giriş öncesi bir 1h barın ucuyla kuruşuna kadar eşit olduğu gösterildi | `docs/BAR_PROVENANCE_V1.md` |
| 2 | **Funding settlement başına gerçek oran + mark** | Üretim yolu ile bağımsız mutabakat 6 hanede aynı: F00015 +0.040753, F00004 +0.008606, F00034 −0.022832 | `docs/FUNDING_AND_EXPOSURE_MEASUREMENT_V1.md` |
| 3 | **Kayıp büyüklüğü ölçülen değerden** — sabit 1.0 yerine daraltmalı kestirim 1.0655 | 22 kayıp, ortalama 1.0774, %95 GA [1.042, 1.113]; 1.0 aralığın dışında | aynı belge §2 |
| 4 | **Brüt maruziyet gözlemi** — `risk.json` artık nominal/özkaynak oranını ve stop-riski sıfırlanmış pozisyonları ayrı yayımlar | 14 açık pozisyon: stop-riski %5.83 (tavan %6) ama nominal özkaynağın 1.85 katı | aynı belge §3 |
| 5 | **Bellek kanıtı toplayıcı** — cgroup + RSS + restart + tur süresi, 5 dakikada bir, systemd timer | kurulum ve okuma yordamı | `docs/MEMORY_VERIFICATION_V1.md` |
| 6 | **Haftalık değerlendirme hazırlığı** — "yeterli veri birikti mi?" raporu, systemd timer | dondurulmuş anlık görüntüde araştırma ölçümünü bağımsız yeniden üretti | `scripts/evaluation_readiness.py` |

Hiçbiri koruyucu stop **gevşetmez**; mevcut stoplar olduğu gibi bırakıldı. Kayıp büyüklüğü onarımı
her adayın kenarını düşürür, hiçbirini yükseltmez.

### UYARI — bar provenansı onarımı iki açık pozisyondaki ESKİ şişmeyi GERİ ALMAZ

`mfe_pct` koşan bir maksimumdur ve defter yeniden yazılmadı. Yeni kod sahte **yeni** katkıyı
engeller ama **birikmiş sapma kalıcıdır**. Sonuç: bu iki pozisyonda başa-baş kuralı **hâlâ erken
tetikleyebilir** ve stop, amaçlanandan önce taşınabilir.

| Pozisyon | Kayıtlı MFE | Gerçek MFE | Kalıcı sapma | Kural tetiklendiğinde GERÇEK MFE |
|---|---:|---:|---:|---:|
| F00038 NATGAS | 0.65R | 0.12R | 0.53R | **0.47R** (hedef 1.00R) |
| F00043 GPS | 0.67R | 0.21R | 0.46R | **0.54R** (hedef 1.00R) |

Bu **risksiz değildir**: erken başa-baş taşıması, hâlâ lehte gidebilecek bir pozisyonu gürültüyle
kapatabilir ve kazananın başını keser — C1 ölçümünün gösterdiği zararın aynı türü. Etki yalnız bu
iki pozisyonla sınırlıdır; sonraki her pozisyon temiz başlar. **Stop gevşetilmedi ve
gevşetilmeyecek**; düzeltme isteniyorsa bu, defteri değiştiren AYRI ve açıkça onaylanmış bir
işlemdir.

## 2. SHADOW ÇALIŞIYOR

* **Gölge akışı** (`tradingbot/replay/shadow.py`, `scripts/shadow_stream.py`): aday başına bir
  JSONL satırı — kabul **ve** ret, karar anı girdileri + sürüm, kullanılan olasılık ve kaynağı,
  maliyet, ceza, boyut, ret nedeni, sonuç olgunlaşma zamanı, etiketleme kuralı, temel ve rakip
  sonuçları yan yana. Reddedilen adaylar korunur; aynı kurulumun tekrarları bağımsız işlem
  sayılmaz (bölüm düzeyi bağımlılık etiketi taşır).
* `assert_safe_output` üretim öğrenme durumuna yazmaya çalışan her yolu **reddeder** (doğrulandı:
  koruma koşumu düşürüyor). Benzetilmiş sonuçlar gerçek kapanışların öğrenme kayıtlarına karışmaz.
* **Ön-kayıtlı rakipler** `docs/CHALLENGERS_V1.md` — hiçbiri çalıştırılmadan ÖNCE commit edildi.

## 3. GERÇEK ÖLÇÜM TABLOSU

### 3a. Kaybın ayrıştırması (29 kapanış, gerçek 1m barlar)

| Bileşen | Değer | n | %95 aralık |
|---|---|---:|---|
| Maliyet sürüklemesi (ücret+funding+kayma) | **+0.0795 R/işlem** | 29 | [+0.054, +0.105] |
| Çıkış geri verme — en iyi uygulanabilir onarım (MFE 1R'de başa-baş) | **+0.1073 R/işlem** | 29 | [0.000, +0.227] |
| Giriş seçimi — sıralama sinyali (`conservative_net_edge_r` ↔ ileri R, Spearman) | **+0.032** | 1334 aday / 144 küme | [−0.136, +0.197] |

Çıkış onarımı **zaten canlı**. Bu örneklemde giriş seçiminde **ölçülebilir sinyal bulunamadı**.
Bu, sinyalin olmadığının kanıtı değil, bu 1334 aday / 5 günlük örneklemle **saptanamadığıdır**;
güç hesabı yapılmadı, dolayısıyla küçük bir etkiyi saptamak için gereken örneklem bilinmiyor.

### 3b. Temel/rakip karşılaştırması — üçü de DÜŞTÜ

| Rakip | Ölçülen | %95 aralık | Karar |
|---|---:|---|---|
| C1 — 24 saatte 1R'ye ulaşmayanı kes | **−0.1641 R/işlem** | [−0.264, −0.072], p<0.0002 | **REDDEDİLDİ — ölçülebilir ŞEKİLDE ZARARLI** |
| C2 — ilk hedefi 1.0R'ye çek | **−0.0616 R/işlem** | [−0.121, −0.007], p=0.024 | **REDDEDİLDİ** |
| C3 — kapıyı kendi değişkeninin medyanına yükselt | +0.0600 R/işlem | [−0.275, +0.380], p=0.74 | **REDDEDİLDİ — ön-kayıtlı null doğrulandı** |

C1 yavaş kaybedenleri kurtarırken yavaş kazananların başını kesiyor (ZRO +2.37→+0.39,
BNB +2.42→−0.19, LTC +1.85→+0.13). C2 tasarlandığı gibi çalışıyor — kazanma oranı %52.3→%57.6,
ortalama R 0.263→0.201 — ve tam bu yüzden kaybettiriyor.

### 3c. Veri neyi SÖYLEMİYOR

**29 kapanış bu sistemin para kaybettiğini göstermez.** Beklenti −0.3567 R, ama %95 aralık
**[−0.852, +0.139]** sıfırı içeriyor; kazanma oranı 7/29 = %24.1, Wilson **[%12.2, %42.1]**,
başa-baş oranı **%36.1**'i içeriyor. Aynı nokta kestirimi sürerse aralık sıfırı **56 kapanışta**
dışlar. Bu sayı iki bağımsız hesapla üretildi.

Ayrıca settlement edilemeyenler: aday havuzundaki kenar düzeyi (5 piyasa günü, %97 LONG, tek bir
gün bütün havuz sonucunu taşıyor); üretimin kabul kararının değer katıp katmadığı (6 olgun kabul);
29 gerçekleşmiş işlemin giriş kalitesi (karar anı snapshot'ları o dönemden önce); üretimin
reddettiği adayları ALACAK herhangi bir rakip (izole harness çıkışları üretir, marj rekabetini ve
tavanı üretmez).

## 4. AÇIK KALDI — ve somut nedeni

| Konu | Neden açık |
|---|---|
| **`p_win` / `avg_win_r` tutarsızlığı** | Ölçüldü (%24 aday kararı değişiyor; tutarlı kullanımda 32 yerine 698 aday işlem yapılabilir). Saf çözüm `1c4cba1`'i geri açar; doğru çözüm kazanç büyüklüğünü de doğrudan ölçmektir ve kendi ön-kayıtlı ölçütünü hak eder. `docs/PWIN_WIN_MAGNITUDE_INCONSISTENCY.md` |
| **Uzun süreli OOM doğrulaması** | Ölçüm altyapısı kuruldu, veri HENÜZ YOK. Kabul ölçütü sonuçlara bakılmadan yazıldı: ≥7 gün, `oom_kill` artışı 0, `NRestarts` artışı 0, `memory.peak` < tavanın %90'ı. |
| İki açık pozisyonun şişik MFE'si | **Hâlâ erken stop taşıması TETİKLEYEBİLİR** — F00038 gerçek 0.47R'de, F00043 gerçek 0.54R'de (hedef 1.00R). Defter yeniden yazılmadı; düzeltmek ayrı ve onay gerektiren bir işlem. Bkz. §1 uyarısı. |
| MFE eksik ölçümü (`last_only` tikler) | Muhafazakâr yönde hata; ayrı iş. |
| Funding takvimi 8 saate sabit kodlu | 4h/1h funding aralıklı sembollerde eksik tahakkuk; onarılan hatadan büyük olabilir. |
| Funding önbellek ıskası anlık orana düşer | Tek-settlement durumunda bugünkünden kötü olmama koşulunun bilinçli bedeli. |
| `path_rows` ~127 MB + `paths_by_trade()` ~130 MB | Akışa çevrilmedi; OOM payı hâlâ dar. |
| `worst_case` ölü config, `exec_reject` kalıcı değil, likidite alanları fail-open | Ayrı işler; hiçbiri bu turda ölçülmedi. |
| Risk politikası: brüt nominal / marj tavanı | **Politika kararı, kod kusuru değil.** Ölçüldü ve yayımlandı; uygulanmadı. |

## 5. SONRAKİ DEĞERLENDİRMEYİ ÇALIŞTIRAN MEKANİZMA

| Ne | Nasıl çalışır | Nereye yazar |
|---|---|---|
| `tradingbot-memprobe.timer` | 5 dakikada bir, systemd, `Persistent=true`; oturumdan bağımsız | `data/logs/memory_probe.jsonl` |
| `tradingbot-evalcheck.timer` | Pazartesi 07:00, ağ kullanmaz, yalnız okur | `data/logs/evaluation_readiness.{log,json}` |
| Aday akışı | Worker zaten her turda yazıyor (2811 satır) | `state/entry_snapshot.jsonl` |

Okuma komutları:

```bash
/opt/tradingbot/venv/bin/python /opt/tradingbot/app/scripts/memory_probe_report.py --rows 12
/opt/tradingbot/venv/bin/python /opt/tradingbot/app/scripts/evaluation_readiness.py
```

Tam değerlendirme (elle, ağ gerektirir):
`scripts/counterfactual_run.py` → `scripts/challenger_eval.py` → `scripts/challenger_verdict.py`.
Kabul ölçütleri `docs/CHALLENGERS_V1.md` içinde **önceden** yazılıdır.

## 5b. BAĞIMSIZ İNCELEME SONUCU — İKİ YÜKSEK ÖNCELİKLİ KUSUR AÇIK

Adversaryal inceleme `2ed0fa0` üzerinde **tamamlandı**. Bar provenansı ve kayıp büyüklüğü
onarımları doğrulandı; **funding onarımı canlı yolda iddia ettiğini YAPMIYOR.**

| # | Kusur | Ölçüm |
|---|---|---|
| **D1** | **Funding onarımı canlı yolun ~%93'ünde ETKİSİZ.** `exit_check` 60 sn'de bir çalışır ve `funding_rates.lookup` ile tick eder ama `ensure_funding_rates` **çağırmaz** (bilerek: ağa çıkmasın). Tur 15 dk'da bir çalışır ve `ensure_funding_rates`'in TEK çağıranıdır. Çıkış monitörü settlement sınırına önce varır, `meta.last_funding_rate`'e (`estimated=True`) düşer, **watermark'ı ilerletir** ve kaydeder. Tur sonra `settlements_due == []` bulur, hiç çekim yapmaz. | Gerçek defter+önbellekle yeniden üretildi: çıkış monitörü önce → 0.00500000 tahakkuk, gerçek 0.00013860 → **36,1× hata**, doğru oran hiç çekilmedi. Turun kazandığı pencere ≈ 60/900 sn = **%6,7**. |
| **D2** | **4 saatlik funding aralıkları hâlâ işlenmiyor** ve açıkta bıraktığı hata, kapattığından **BÜYÜK**. | Açık 14 pozisyonda tahakkuk etmemiş funding **0,223121 USDT**; kapatılan hata 29 kapanışta 0,209232 USDT. CL/BZ/NATGAS/GPS 4 saatte bir, settlement başına %0,25–0,34. |
| D4 | Fail-closed, `meta.last_funding_rate == "0.0"` olduğunda **sızıyor**: olay yazılmadan watermark ilerliyor. **Canlı 3 pozisyonda** (CRCL, META, CRWV) bu durum var. Önceden var olan kusur, bu sürümde eklenmedi — ama `2506c99` mesajı koşulsuz "fail-closed değişmedi" diyor; ikinci yarısı **yanlış**. | 2 settlement sessizce atlandı. |
| D3 | İki mutasyon **tüm 2103 testi yeşil bırakıyor**: `_marks`'ta `bar_open=""` (özellik sessizce ölür) ve turdan `ensure_funding_rates` kaldırma (önbellek hiç dolmaz). Üretici tarafta test koruması yok. | M2 ve M9. |
| D6 | `test_09`'un ilk iddiası boş: `last=3000, high=3200` ile başa-baş zaten mark kontrolüyle bloklanıyor; guard geri alınsa da geçiyor. | `last=3010` yapılırsa gerçek olur. |
| D7 | `test_10` metin tarıyor: `bar_open=""` (boş değer) testi **geçiriyor**. Anahtar kelimeyi zorunlu kılıyor, provenansı değil. | M10. |
| D8 | `bar_extremes_skipped` üretimde **görünmez**: hiçbir log/rapor/`health.json` alanına yazılmıyor. Sessiz mark-only moduna karşı erken uyarı yok. | — |
| D9 | Bar provenansı gereği her girişten sonra ~1,5 saat stop/TP/likidasyon **yalnız mark** ile değerlendirilir. Guard yalnız uç *esirger*, asla vermez; yeni yanlış tetik üretemez. | 29 kapanışta 2125 saatin **45,5 saati** (%2,1); **0/29** işlem tamamen kör değil. |

**Sonuç:** funding değişikliği `1c4cba1`'e göre **gerileme değildir** (en kötü durumda bugünle
birebir aynı davranır), ama commit mesajı ve bu belgenin önceki hâli **fazlasını iddia ediyordu**.
D1 kapatılmadan "her settlement kendi gerçek oranını alır" **denemez**.

Doğrulanan: bar provenansı kanıtı bağımsız olarak yeniden üretildi (7 pozisyonda şişme birebir);
kayıp büyüklüğü 2797 adayda **kesinlikle muhafazakâr** (2797'sinde kenar düştü, 0'ında yükseldi;
111 aday `tradeable → blocked`, 0 aday tersi); mutasyon M1 → 7 test düşüyor; defter/öğrenme
kayıtları yeniden yazılmıyor; hiçbir koruyucu stop gevşemiyor.

## 6. BAĞIMSIZ İNCELEMENİN KAPSAMI — dürüst durum

* Değişiklikleri yazmayan ayrı bir adversaryal Claude ajanı **`2ed0fa0`** üzerinde çalıştı ve
  **tamamlandı**; sonucu §5b'de. Testler orada da 2103 passed / 22 skipped, ruff temiz.
* İnceleme **onay vermedi**: iki yüksek öncelikli kusur açık (D1, D2).
* `2ed0fa0 → 4d666db` arası fark bağımsız incelemenin **görmediği** kısımdır. Bu farkın üretim
  yolunda olmadığı **mekanik olarak** gösterildi: worker'ın gerçek import grafiği 84 `tradingbot`
  modülü yüklüyor ve eklenen beş modülün (`replay/{fidelity,challengers,counterfactual,shadow,stats}`)
  **hiçbiri** bu grafikte yok (`replay/__init__.py` yalnız `engine`'i import eder). Farkın geri
  kalanı belge, betik, systemd birimi ve test dosyasıdır.
* Bu **kendi** kontrolümdür, bağımsız değildir.

## 7. GPT‑6 Astra

Bu oturumda yetkili bir GPT‑6 Astra bağlantısı **YOKTUR** (yüklü connector listesi boş).
**Astra incelemesi yapılmadı.**

## 8. DAĞITIM DURUMU

**Bu sürüm VPS'e DAĞITILMADI.** Oturumdan SSH mümkün değil (anahtar parola korumalı).
`4d666db` yalnız origin'e push edildi ve CI bu commit üzerinde çalıştırıldı. VPS'te
`1c4cba1` çalışmaya devam ediyor. Dağıtım paketi (`bundle` + betik) teslim edildi; betik
çalıştırılana kadar dağıtım **yapılmamıştır**.

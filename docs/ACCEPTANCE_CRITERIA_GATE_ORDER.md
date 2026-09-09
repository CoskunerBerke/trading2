# Kabul Ölçütleri — Kapı Sırası Onarımı (ÖNCEDEN KAYITLI)

**Bu dosya sonuçlara BAKILMADAN yazıldı.** Yazım anı: dağıtımdan önce, ilk tur çalıştırılmadan.
Amaç: "sonuç iyi çıktı, demek ki eşiği doğru seçmişiz" türü geriye dönük uydurmayı imkânsız kılmak.

Değişiklik: `engine_v3.tour()` içinde ekonomik kapı (`_assess_opportunities`) artık kalibre `p_win`
hesaplandıktan SONRA çalışıyor. Öncesinde 48 satır önce çalışıyor ve HEAD önselini (`0.5+0.25*conf`)
kullanıyordu.

---

## 1. Bu bir mühendislik kusuru onarımıdır, strateji değişikliği DEĞİLDİR

Gerekçe, ölçümden bağımsız olarak geçerlidir:

* Kapının içindeki dal `if d.p_win:` ve yorumu **"kalibre model tahmini onceliklidir"** diyor.
  Kod bunun tersini yapıyordu. Niyet ile davranış çelişiyordu.
* HEAD önseli (`head.py:354`) uzlaşma güveninin monoton bir dönüşümüdür; **hiçbir sonuç olayının
  olasılık tahmini değildir** ve hiçbir şeye karşı kalibre edilmemiştir.
* Üretim verisiyle ölçüldü: 2797 adayın **2797'sinde (%100)** kapının kullandığı olasılık HEAD
  önseliydi; `opportunity.hierarchical_expectancy`'nin ürettiği kalibre değer hiçbir adayda
  kullanılmadı. Bu, tasarımın kendi sözleşmesinin ihlalidir.

Dolayısıyla onarım, sonuç ne olursa olsun doğrudur. Aşağıdaki ölçütler onarımın **doğru
uygulandığını** ve **beklenmeyen yan etkisi olmadığını** sınar; kârlılığı kanıtlamaz.

## 2. Dağıtım öncesi kapılar (hepsi geçmeli)

| # | Ölçüt | Eşik |
|---|---|---|
| G1 | Tam test paketi | 0 başarısız |
| G2 | `ruff check` değişen dosyalarda | temiz |
| G3 | Yeni regresyon testleri | `tests/test_gate_uses_calibrated_pwin.py` 6/6 geçer |
| G4 | Bağımsız doğrulayıcı (E ajanı) | onarımı ve kanıtı teyit eder; **kendi yazdığımı tek onaylayan ben olamam** |
| G5 | `doctor --quick` | OK |
| G6 | PAPER bayrakları | `mode=PAPER`, `live_trading=false`, `gateway=paper` değişmemiş |
| G7 | Doğrulanmış yedek + rollback işaretçisi | alınmış ve sidecar doğrulanmış |

## 3. Dağıtım sonrası ölçütler

### 3a. DOĞRU UYGULAMA (bunlar sağlanmazsa GERİ AL)

| # | Ölçüt | Eşik | Nasıl ölçülür |
|---|---|---|---|
| P1 | Kapının kullandığı olasılık artık HEAD önseli DEĞİL | Yeni yazılan aday kayıtlarının **%0**'ı `round(0.5+0.25*conf,3)` ile eşleşir | `entry_snapshot.jsonl` üzerinde kimlik tersine çözümü: `p = (gross+|L|)/(W+|L|)` |
| P2 | Kapının kullandığı olasılık kayıtlı `p_win` ile eşleşir | ≥ %99 eşleşme (yuvarlama toleransı 5e-4) | aynı tersine çözüm |
| P3 | Hata yok | restart sonrası `Traceback\|CRITICAL\|ERROR` = 0 | journalctl |
| P4 | Servis sağlığı | `ready=200`, en az 1 tam tur, `NRestarts` artışı yok | /health + journal |
| P5 | Kanonik defter korunur | açık pozisyon kimlikleri, `seq`, `history` uzunluğu değişmez (doğal kapanış hariç) | ledger karşılaştırması |
| P6 | Chief sıralaması bozulmadı | `chief.ranking` içinde `conservative_net_edge_r` dolu | `coin_heads.json` |

### 3b. BEKLENEN DAVRANIŞ DEĞİŞİKLİĞİ (ölçülecek, kapı DEĞİL)

Ajan B'nin karşı-olgusal ölçümü: kayıtlı istatistiksel `p_win` ile aynı adaylar değerlendirilseydi
işlem yapılabilir aday oranı **%91,7 → %1,9**'a düşerdi ve hiçbir aday tam boyut almazdı.

**Bu nedenle işlem sayısının ÇOK AZALMASI beklenen ve doğru sonuçtur.** Aşağıdakiler kapı değildir,
gözlemdir:

* Tur başına kabul edilen aday sayısı (beklenti: sıfıra yakın).
* `size_multiplier` dağılımı (beklenti: 1.0 doygunluğu kaybolur, 0.25 araştırma tabanı öne çıkar).
* Huni sayaçlarında `size_multiplier_zero` artışı.

**İşlem sayısının azalması BAŞARISIZLIK SAYILMAZ.** Ölçülebilir kenarı olmayan bir sistemde doğru
işlem sayısı sıfıra yakındır. Tersine, işlem sayısı azalmazsa onarım UYGULANMAMIŞ demektir (P1/P2 düşer).

### 3c. KÂRLILIK HAKKINDA HİÇBİR İDDİA YOK

Bu onarım kârlılık kanıtı üretmez ve üretmesi de beklenmez. Kâr/zarar üzerinden değerlendirme için
gereken örneklem (kapanmış işlem sayısı) mevcut değildir; bugünkü toplam 29 kapanıştır ve onarım
sonrası kapanış sayısı sıfırdır. Kârlılık değerlendirmesi ayrı ve önceden kayıtlı bir çalışmadır.

## 4. Geri alma tetikleyicileri

Aşağıdakilerden **herhangi biri** olursa `bash deploy/rollback.sh` ile önceki SHA'ya dön:

* P1 veya P2 düşerse (onarım uygulanmamış ya da yanlış değeri kullanıyor).
* P3: restart sonrası tur içinde Traceback/CRITICAL.
* P4: servis `ready` olamıyor ya da tekrar tekrar yeniden başlıyor.
* P5: kanonik defterde beklenmeyen değişiklik.
* Chief sıralaması `conservative_net_edge_r` göremiyorsa (P6) — kapının chief'ten sonra kaldığı anlamına gelir.

Geri alma **kod/config içindir**. İlerlemiş işlem defteri eski yedeğe DÖNDÜRÜLMEZ.

## 5. Bir sonraki değerlendirmenin koşulu

Zaman geçmesi tek başına kanıt değildir. Bir sonraki ölçüm şu koşul sağlandığında yapılır:

* **En az 10 yeni doğal kapanış** onarım sonrası SHA ile açılmış işlemlerden, **veya**
* 14 gün geçmesi ve o ana kadarki kapanış sayısının raporlanması (yetersizse açıkça "yetersiz" denir).

O ölçümde bakılacaklar: kabul edilen aday sayısı, kabul edilenlerin gerçekleşen R dağılımı,
`p_win` kalibrasyonu (Brier, güvenilirlik tablosu), ve reddedilen adayların etiketlenmiş sonuçları
(fırsat maliyeti). Kalibrasyon iyileşmezse bir sonraki iş `p_win` modelidir, eşik ayarı değildir.

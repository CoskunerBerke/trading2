# ÖLÇÜM NEYİ TEMSİL EDİYOR — karar değiştiren dört boşluk

Taban `350928c`, bu turun dalı `work/entry-research-v1`, worktree `C:\Users\berke\wt-entry`.
Bu belge yeni deneyi etkileyen boşlukları kapatır; genel denetim değildir.

## A. Replay hangi sistemi ölçtü

Kaynaktan çıkarıldı (`out/path_parity.json`, üretici betik kayıtta):

| aşama | canlı motor | replay (350928c) |
|---|---|---|
| mum/çerçeve üretimi | var | var |
| 20 uzman ajan (`registry.run_many`) | var | var |
| chief sıralama/izin | var | var |
| **öğrenilmiş p_win (`learner2.predict`)** | var | **YOK** |
| **öğrenme etkisi (`_learning_influence`)** | var | **YOK** |
| **EKONOMİ KAPISI (`assess`)** | var | **YOK** |
| **fırsat boyut çarpanı (`size_multiplier`)** | var | **YOK** |
| **araştırma politikası (`RESEARCH_SIZE_ONLY`)** | var | **YOK** |
| **giriş seçicilik / MTF** | var | **YOK** |
| **haber bağlamı** | var | **YOK** |
| risk motoru (`risk.evaluate`) | var | var |
| defter açılış / tick / funding | var | var |
| öğrenme zinciri (kapanış) | var | var |

**Sonuç: önceki turun replay'i "üretim karar yolu" DEĞİLDİ.** Ekonomi kapısı hiç
çalışmıyordu: `replay/engine.py` içinde `assess`, `opportunity`, `size_multiplier` geçen tek
satır yoktu. Ölçülen şey, kapısı olmayan bir **aday popülasyonuydu**. Bu popülasyonun negatif
çıkması, üretim modelinin tamamına ait bir kanıt değildir — kapının işi zaten bu adayları
elemektir.

### Onarım: tek kaynak kapı

`tradingbot/economics_gate.py` — saf fonksiyon `assess_one`. Canlı motor ve replay AYNI
fonksiyonu çağırır, ceza tablosu tektir. Mantık ikinci kez kopyalanmadı; çünkü bu paketin
daha önce yaşadığı kusur sınıfı tam olarak buydu (çıkış politikasında `breakeven_at_mfe_r`).

Replay artık kapıyı **varsayılan olarak çalıştırır**. Kapatmak (`--no-economics-gate`)
bilinçli bir araştırma seçimidir: kapısız popülasyonu ölçmek için.

Replay'in kalan sapması saydam: `p_win_source` alanı her kararda ya `calibrated_model`,
ya `hierarchical_prior`, ya da `head_heuristic` yazar. Üretimde şampiyon model hazır
değilken `0,5 × prior + 0,5 × legacy` harmanı kullanılır; replay'de legacy tahminci yoktur,
saf prior kullanılır. Bu fark ölçülebilir ve kayıtlıdır, gizli değildir.

### Ölçülen etki (2026-07, on coin, bir ay)

| | aday (actionable) | açılan | ret: NEGATIVE_NET_EDGE | ret: RESEARCH_SIZE_ONLY |
|---|---|---|---|---|
| kapısız | 1254 | 13 | 0 | 0 |
| **kapılı** | 1472 | **5** | **1189** | **257** |

Kapı, üretimde gözlenen davranışı yeniden üretiyor: `1c4cba1` dağıtıldıktan sonra işlem
sayısının sıfıra düşmesi bir arıza değil, kapının çalışmasıydı.

### Farklı senaryolarda kapı davranışı (test edildi)

`tests/test_economics_gate_parity_v1.py` — 12 önerme. p_win `None` iken hiyerarşik tahmine
düşer; `0.0` iken kelepçelenir ve **düşmez** (fail-open kapandı); iki uç da kelepçelenir;
üretimin ölçülen medyanı 0,278 ile beklenti negatif olur; sıfır stop mesafesi sert engeldir;
kayıtsız kod fail-closed'dur; SHORT cezası pozitif nokta tahminini `research_only`e düşürür;
spot'ta listeli sembole yalnız-vadeli cezası uygulanmaz; boyut çarpanı muhafazakâr edge ile
ölçeklenir ve risk tavanını aşmaz.

## B. Olasılık ile kazanç aynı olayı anlatıyor mu

`learn/learner_v2.py:199` — etiket `r_multiple > 0.25`. Bu, **kapanışta net R'nin +0,25R'yi
aşması**dır; hedefe stop'tan önce ulaşma olasılığı DEĞİLDİR.

Önceki tur `p_win ≈ 0,27` ile `%35,9` başabaşı karşılaştırdı. O karşılaştırma VPS'in 25
kapanışında geçerliydi çünkü orada `P(R>0)` ile `P(R>0,25)` tesadüfen **aynıydı** (ikisi de
0,280). Genel olarak aynı değiller:

| örneklem | P(R>0) | P(R>+0,25R) | arada kalan | arada kalanların çıkışı |
|---|---|---|---|---|
| test temeli (195) | %31,79 | %24,62 | 14 | 14'ü başa-baş stop |
| geliştirme temeli (97) | %24,74 | %15,46 | 9 | 9'u başa-baş stop |
| k=0,75 testi (230) | %54,35 | %45,65 | 20 | 18 başa-baş, 2 hedef2 |

Fark tamamen **başa-baş stop** çıkışlarından geliyor: 0 < R ≤ 0,25 bandına düşüyorlar.

Aynı olay tanımıyla, gerçekleşmiş dağılımdan başabaş olasılığı:

| örneklem | gerçekleşen p | E[R \| olay] | E[R \| değil] | başabaş p | açıklık |
|---|---|---|---|---|---|
| test temeli | 0,2462 | +1,6772 | −0,7994 | **0,3228** | −7,7 puan |
| geliştirme temeli | 0,1546 | +1,4388 | −0,7151 | **0,3320** | −17,7 puan |

**Düzeltilmiş hedef: test penceresinde açıklık 7,7 puandır** (önceki turun "3–10 puan"
aralığı farklı bir kurgudan, MFE tablosundan geliyordu).

Kalibratörün fit edilmemiş olması ayrı bir konudur ve tek başına ayırt edici sinyal
yaratmaz: Platt kalibrasyonu monotondur, sıralamayı değiştirmez. Sabit skorları seçici
modele çevirmez.

## C. Maliyet stresinde temel neden daha az kaybetti

İki ölçüm ayrı yapıldı.

**C1 — sabit işlemler, yalnız maliyet kötüleşiyor.** Giriş/çıkış zamanları, yönler ve
miktarlar sabit. Defter kimliği `net = brüt − ücret + funding` olarak 195/195 işlemde
doğrulandı, yani slippage fill fiyatına gömülüdür ve ayrıca düşülmez. Ücreti ikiye katlamak
`ücret` kadar, slippage'ı ikiye katlamak `slippage_cost` kadar EK gider ekler:

| koşu | n | net | ek ücret | ek slippage | stresli net | fark |
|---|---|---|---|---|---|---|
| test temeli | 195 | −76,1853 | 4,5328 | 25,3910 | **−106,1091** | −29,9238 |
| test k=0,75 | 230 | −84,8493 | 5,2050 | 35,5794 | **−125,6336** | −40,7843 |

Sözleşme tuttu: sabit işlem kümesinde artan gider daima sonucu kötüleştirir. **Hesap
kusuru yok.**

**C2 — dinamik portföy.** Sermaye ve kapılar yeniden çalıştığı için işlem kümesi değişiyor:
temel 195, stresli 201; 11 (coin × ay) hücresi yalnız temelde, 14 hücre yalnız streste.
Stresli koşunun daha iyi görünmesi (−63,75 / −76,19) **farklı işlemler açıldığı içindir**,
maliyetin faydası değildir. Funding işareti de karıştırılmadı: gider artışı ile alınan
funding geliri ayrı alanlardır ve stres yalnız gider tarafını büyütür.

## D. Teşhis ve sonuç tablolarının uzlaştırılması

**Maliyet payı.** Pay = brüt − net = 0,2972 USDT (ücret 0,4103 eksi alınan funding 0,1131).
Payda = |net| = 4,2474. Oran **%7,0**. Bilinmeyen: 9 kapanışta funding tam sıfır kayıtlı ve
defter "ölçüldü sıfır" ile "sorulmadı" ayrımını taşımıyor; bu 9 işlem gerçekte funding
ödediyse pay büyür. Oran küçük paydalarda kararsızdır: funding'i sıfırdan farklı olan 16
işlemlik alt kümede net −0,0104 USDT olduğu için aynı oran %1364 çıkar. **Bu yüzden oran
yalnız tüm örneklem için anlamlıdır, alt kümelere taşınamaz.**

**Örneklemler aynı değil.** 25 kapanış, maliyeti doğrulanmış 16 kayıt, dışlanan 9 kayıt ve
giriş SHA'sı bilinmeyen 22 kayıt farklı kümelerdir.

**Mekanizma grupları artık toplanıyor** (önceki tur 15/25'i gösteriyordu):

| grup | n | net USDT |
|---|---|---|
| yön/geç giriş (MFE < 0,25R, zararla kapandı) | 6 | −6,4159 |
| kâr geri verme (MFE ≥ 1,0R, zararla kapandı) | 2 | −1,2415 |
| kazananlar | 7 | +9,3775 |
| artık (MFE 0,25R–0,94R, zararla kapandı) | 10 | −5,9675 |
| **toplam** | **25** | **−4,2474** = defter |

**En önemli uzlaştırma:** giriş kod kimliği bilinen 3 işlemin üçü de KAZANÇ (+3,1813 net,
ort +1,2538R); kimliği bilinmeyen 22 işlem −7,4287 (ort −0,4385R). Üstelik on coin evrenine
ait olan 3 işlemin de üçü kazanç (+3,0835 net). **Bu 25 işlemlik örneklem, on coin
stratejisi hakkında pratikte hiçbir kanıt taşımıyor.** Önceki turun teşhisi bu örneklemi
"stratejinin" davranışı gibi okuyordu; düzeltildi.

**Düşük isabet tek başına yanlış yön kanıtı değildir.** Geç giriş, stop yerleşimi ve çıkış
zamanlaması aynı isabeti üretebilir. Elimdeki kayıtlarla bunları AYIRAMIYORUM: giriş anı
tetik zamanı ile bar kapanışı arasındaki gecikme kaydı yok. Bu yüzden "yön isabeti" ifadesi
bir mekanizma iddiası değil, ölçülen büyüklüktür.

**Estimand düzeltmesi.** Önceki tur adayın temelden farkını **+0,0087R** diye yazdı; tablo
farkı ise −0,1824 − (−0,1898) = **+0,0074R**. İkisi farklı büyüklüktür: +0,0074 gözlenen
örnek farkı, +0,0087 blok bootstrap ortalamasıdır (bloklar eşit olasılıkla yeniden
ağırlıklandığı için sapar). `analyze.observed_diff` artık nokta tahmini üretir; bootstrap
yalnız aralık içindir.

| büyüklük | değer | %95 aralık |
|---|---|---|
| gözlenen fark | **+0,0074** | — |
| blok bootstrap, eşleştirilmiş | +0,0087 | [−0,1443, +0,1584] |
| blok bootstrap, eşleştirilmemiş | +0,0073 | [−0,1771, +0,2039] |

Karar değişmiyor: üç ölçüm de sıfırı içeriyor.

**Genellenmeme.** Geliştirmedeki +0,2163R'lik üstünlüğün testte +0,0074R'ye düşmesi
**genellenmediğinin** kanıtıdır. Tek nedenin aşırı uydurma olduğunu bu karşılaştırma
kanıtlamaz: rejim değişimi, kapasite etkileşimi ve iki pencerenin farklı coin/ay bileşimi de
aynı sonucu üretebilir. Yazılan iddia budur.

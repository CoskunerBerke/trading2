# ÖN KAYITLI PROTOKOL V9 — SADELEŞTİRME: tek kurallı trend, botun kendi defterinde

**Yazıldığı an:** 2026-09-13, **hiçbir V9 koşusu görülmeden.**
**Kod:** `0796c91` + replay "strateji modu" (kayıtsız, `out/v9_code.patch`).
**Kaynak:** V7-Q2 ve V8 yan bulgusu: tek kurallı EMA200 trend referansı üç pencerede pozitif, naif
formasyon girişi bile 2022 sonrasında botu geçiyor. Kullanıcı: "kâra en çok yaklaştıracak neyse yap".

## 1. Soru

Referans simülatöründe artıda çıkan tek kural, **botun kendi sistemi** içinde (aynı defter, taker +
kayma, arşiv funding, gerçek borsa filtreleri, aynı risk motoru ve %2 işlem riski, aynı kaldıraç
sınırları, aynı likidasyon modeli) hâlâ artıda mı? Botu (uzman yığını) geçiyor mu?

## 2. Strateji modu (altyapı, deney değil)

`HistoricalReplay(strategy=...)`: uzman yığını, baş yönetici ve kapılar ATLANIR; her 4h barda strateji
`(sembol, t, kapanmış çerçeveler, açık pozisyon, replay)` alır ve `OPEN` / `CLOSE` / hiçbir şey döner.
Açılış aynı `risk.evaluate` + `ledger2.open` yolundan (boyut = işlem riski / stop mesafesi, filtre,
kayma), kapanış `ledger2.close_manual` (kayma dahil); stop/hedef/funding/likidasyon `ledger2.tick`.
Yani "aynı sistem" iddiası defter düzeyinde doğrudur; yalnız karar kaynağı değişir.

## 3. Kollar (tek değer, ızgara yok)

| kol | kural |
|---|---|
| **T1** saf trend | son KAPANMIŞ günlük close > EMA200 → LONG (piyasa, ilk 4h barda); close < EMA200 → kapat. Felaket stopu: giriş − 3×ATR14(1d). Hedef yok. Başa-baş koruması KAPALI. Kaldıraç 1. |
| **T2** trend + rejim | T1 + yalnız BTC rejimi UP iken giriş (BTC için özdeş). |
| **T3** trend + botun çıkışı | T1 kuralları + ÜRETİMİN başa-baş koruması (`breakeven_at_mfe_r`) açık: "trend girişi, botun çıkış yönetimi". |

Karşılaştırma satırları: **B0** eski üretim (`v4_c3_*`, `v6_b0_p3`) ve **R1** şu anki üretim (`v7_r1_*`).
Pencereler P1, P2, P3. 3 kol × 3 pencere = **9 koşu.** Uzman yığını atlandığı için hızlı.

## 4. Kabul (baştan yazılı)

Bir kol "sadeleştirme adayı" olabilmek için **üç pencerede de**: (1) maliyet sonrası hesap getirisi
pozitif, (2) R1'den (şu anki üretim) yüksek, (3) ≥ 20 kapanmış işlem ve ≥ 5 coin (trend kuralı az
işlem üretir; eşik bilinçli olarak 40 değil 20), (4) ücret ×2 + kayma ×2 stresi altında pozitif.
Tutulan/elenen şartı burada anlamsız (farklı karar kaynağı); yerine **maruziyet** raporlanır.

Geçen kol varsa: bir sonraki adım üretimde "strateji modu"nu PAPER'da ayrı bir defterle koşturmak
(mevcut bot ile yan yana, aynı VPS) — bu protokolün kapsamı DIŞI, ayrı karar.
Geçen kol yoksa: "sadeleştir" hipotezi bu haliyle düşer; trend referansının artısı kaldıraçsız
tam-ağırlık maruziyetinden geliyordur ve bu botun risk çerçevesine taşınamaz.

## 5. Sınırlar

* Referans simülatöründe %10 sabit ağırlık vardı; burada boyut risk motorundan gelir (%2 risk /
  stop mesafesi, toplam risk tavanı). Sonuçların farklı çıkması BEKLENİR; soru "hangi yönde".
* Tek EMA200; ızgara yok. On coin 2026 seçimi (lehte yanlılık).
* Replay `--no-patterns`; strateji modu uzman/kapı kullanmadığı için o fark burada önemsiz.

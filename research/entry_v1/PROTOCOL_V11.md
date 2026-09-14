# ÖN KAYITLI PROTOKOL V11 — momentum tanımı: EMA200'ün alternatifleri

**Yazıldığı an:** 2026-09-13, **hiçbir V11 koşusu görülmeden.** Kod `a619fb0` (strateji modu), kurallar
yalnız araştırma paketinde (`momentum_rules.py`); üretime bir şey girmez.
**Kaynak:** Liu & Tsyvinski (RFS 2021) kriptoda ZAMAN SERİSİ MOMENTUMU (1–4 haftalık ufuk) ve yatırımcı
ilgisini öngörücü buluyor; V9'da EMA200 trend kuralı üç pencerede pozitif çıktı. Soru: momentumu başka
tanımlarla ölçmek T2'yi geçer mi?

## 1. Kollar (T2'den TEK değişiklik; BTC rejim kapısı EMA200 ile AYNI kalır; stop 3×ATR14(1d), kaldıraç 1,
hedef yok, başa-baş kapalı — hepsi T2 ile aynı)

| kol | giriş/çıkış sinyali (günlük KAPANMIŞ bar) |
|---|---|
| **T2** temel | close > EMA200 → LONG; close < EMA200 → kapat (`v9_t2_*`, yeniden koşulmaz) |
| **M1** kısa EMA | close > EMA100 → LONG; close < EMA100 → kapat |
| **M2** 4 haftalık TSMOM | close > 28 gün önceki close → LONG; altına inince kapat (Liu-Tsyvinski ufku) |
| **M3** 13 haftalık TSMOM | close > 91 gün önceki close → LONG; altına inince kapat (klasik 3 ay) |

3 kol × 3 pencere = **9 koşu.** Başka kol/parametre eklenmeyecek (ızgara YOK: 100, 28, 91 birer kez).

## 2. Kabul (baştan yazılı)

Bir kol T2'nin yerine geçebilmek için **üç pencerede de**: (1) maliyet sonrası pozitif, (2) T2'den yüksek
hesap getirisi, (3) ≥ 20 işlem ve ≥ 5 coin, (4) stres altında pozitif, (5) en kötü pencere maksimum düşüşü
T2'ninkinden (%22,3) BÜYÜK OLMAMALI. Tek pencerede T2'yi geçmek hiçbir şey ifade etmez; T2 kalır.
Hiçbiri geçmezse T2 tanımı doğrulanmış sayılır ("başka tanım daha iyi değildi").

## 3. Sınırlar

* Aynı üç pencere; T2 bu pencerelerde görüldü, yani tanım seçimi "temiz" değildir; bu yüzden şart (5)
  ve "tek pencere yetmez" kuralı sıkı tutulur.
* İşlem sayıları küçük, aralıklar geniş; 28 günlük kural daha çok işlem ve maliyet üretir (beklenti).
* Yatırımcı ilgisi (arama hacmi) bu turda YOK: arşiv yok.

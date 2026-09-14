# ÖN KAYITLI PROTOKOL V13R — T2 ve M2 sonuçlarını KIRMA denemesi (sağlamlık)

**Yazıldığı an:** 2026-09-14, **hiçbir V13R koşusu görülmeden.** Kod `3501304`; kurallar yalnız araştırma
paketinde (`momentum_rules.py`, `run_rule.py --exclude`); üretime ve VPS'e HİÇBİR ŞEY girmez. İleri test
(T2 + M2 kâğıt defterleri) dokunulmadan sürer.

**Soru:** DENEY_V9/V11'deki pozitif sonuçlar (T2 +107/+34/+10, M2 +158/+46/+35) kırılgan mı? Bir sonucun
"gerçek" olması için birkaç coine, tek bir parametreye ya da pencere sınırlarına bağlı OLMAMASI gerekir.
Bu tur sonucu iyileştirmeye değil, çürütmeye çalışır. Çürütülemezse güven artar; çürütülürse ileri
testte neye bakılacağı değişir.

## 1. Kollar (hepsi strateji modu, `--no-breakeven`, on coinlik evren, ölçülen üç pencere)

| kol | ne değişir | koşu |
|---|---|---|
| **R1 coin çıkar (LOO)** | evren aynı yüklenir, TEK coin yeni giriş açamaz (`--exclude`); T2 ve M2, her pencere, 10 coin | 60 |
| **R2 komşu parametre** | M2: 21 ve 42 gün (28 ölçüldü, 91 reddedildi); T2: EMA150, EMA250 + KONTROL `t2r_ema200` (araştırma EMA'sı; üretim sütunuyla fark ölçülür) | 15 |
| **R3 pencere kaydırma** | başlangıç +3 ay: 2021-02→2022-11, 2022-12→2024-11, 2024-12→2026-08; T2 ve M2 | 6 |
| **R4 yoğunlaşma** | koşu YOK; mevcut v9_t2 / v11_m2 kayıtlarından: coin başına katkı, en iyi 3 işlemin brüt kâr payı, T2–M2 aylık net korelasyonu | 0 |

Toplam **81 koşu.** Başka kol/parametre eklenmeyecek. Maliyet stresi (ücret+kayma ×2) DENEY_V11'de zaten
raporlu ve her pencerede pozitif; yeniden koşulmaz.

## 2. Kırılganlık bayrakları (baştan yazılı)

* **F1 coin bağımlılığı:** bir pencerede LOO getirilerinin EN DÜŞÜĞÜ < 0 → o pencerede sonuç tek coine bağlı.
  Ek: tek coin çıkarılınca getiri tabanın yarısının altına iniyorsa "yoğunlaşmış" (bilgi).
* **F2 parametre uçurumu:** taban pozitifken komşu parametre o pencerede negatif → uçurum. T2 komşuları
  üretim T2 ile değil KONTROL (`t2r_ema200`) ile karşılaştırılır; kontrol ile üretim farkı ayrıca raporlanır.
* **F3 sınır şansı:** kaydırılmış pencerede negatif → sınır şansı.
* **F4 işlem yoğunlaşması:** en iyi 3 işlem brüt kârın > %50'si → yoğun (bilgi amaçlı, tek başına red değil).

**Karar kuralı:** Bir kural için F1/F2/F3'ten herhangi biri ≥ 2 pencerede kalkarsa o kural **KIRILGAN**
sayılır: VPS'te değişiklik yapılmaz, ama "M2 T2'nin yerine geçsin" tartışması masadan kalkar ve ileri test
raporlarında ilgili coin/parametre ayrıca izlenir. Hiçbir bayrak kalkmazsa "kırılamadı" yazılır; bu da
KANIT DEĞİL, yalnız güven artışıdır (aynı üç pencere, hayatta kalma yanlılığı hâlâ var).

## 3. Sınırlar

* Hayatta kalma yanlılığı bu turda çözülmez: on coin bugün var olanlardır. `vps-collect-2020-universe.sh`
  ile Ekim-2020 hacim sıralaması toplanınca ayrı bir turda (V14) ölçülür.
* LOO'da BTC çıkarılsa bile BTC rejim kapısı BTC verisini kullanır (çıkarma yalnız yeni girişi engeller).
* Kaydırılmış P3 21 aydır (veri 2026-09'da bitiyor).
* Aynı üç pencere; parametre komşuları bu pencerelerde ilk kez görülüyor ama taban da bu pencerelerde
  seçilmişti — komşu testinin gücü sınırlıdır.

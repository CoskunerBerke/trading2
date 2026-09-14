# ÖN KAYITLI PROTOKOL V6 — seans ve hafta sonu kapısı

**Yazıldığı an:** 2026-09-12, **hiçbir V6 koşusu görülmeden.**
**Kod:** `work/entry-research-v1`, `5fc4507` + `tradingbot/session_gate.py` (kayıtsız, `out/v6_code.patch`).
**Kaynak:** kullanıcı "borsa kapandı, pazartesi 9'da açılıyor, bot bunu kaale almalı" dedi.

## 1. Soru ve dürüstlük beyanı

Kripto vadeli sözleşmeleri kapanmaz; soru "borsa kapalıyken işlem yapma" değil, "geleneksel
seans saatleri ve hafta sonu bu sistemin giriş kalitesini değiştiriyor mu" sorusudur.

**Bu hipotez, P1 ve P2 penceresindeki temel koşunun BÖLMESİNDEN doğdu** (2026-09-12, aynı
oturum): P1'de ABD seansı +0,175R vs +0,037R; P2'de fark yok (−0,193 vs −0,202); hafta sonu
tutarsız. Bu yüzden **P1 ve P2 bu soru için temiz değildir** ve tek başlarına kanıt sayılmaz.
Temiz pencere olarak **P3 = 2024-09-01 → 2026-08-31** eklendi; bu soru için hiç bakılmadı.

## 2. Kollar (tetik + C3 mum vetosu = üretim; parametre yok)

| kol | kural |
|---|---|
| **B0** | temel = üretim; P1/P2 için `v4_c3_*`, P3 için yeni koşu `v6_b0_p3` |
| **S1** | yalnız karar saati 12:00–19:59 UTC (4h barda 12:00 ve 16:00 kapanışları) |
| **S2** | Cumartesi/Pazar (UTC) giriş yok |
| **S3** | S1 ve S2 birlikte |

Mantık `tradingbot/session_gate.py` (saf); araştırma kuralı delege eder. Sınırlar tek değer
(12–20 UTC, hafta sonu = Cmt/Paz UTC); ızgara yok.

## 3. Koşular

3 kol × 3 pencere + B0 P3 = **10 koşu.** Başka koşu eklenmeyecek.

## 4. Ölçütler ve kabul (önceden bağlayıcı)

V5 ile aynı beş şart (pozitif, temelden iyi, ≥40 işlem ve ≥5 coin, stres altında pozitif,
tutulan ort R ≥ elenen ort R), blok bootstrap (coin × ay, eşli, 2000), maliyet stresi.

Bir kol gölge PAPER adayı olabilmek için **üç pencerenin de** beşini sağlamalı. P1'de sağlanıp
P3'te sağlanmazsa bu "bölmenin kendini doğrulaması"dır, kanıt değil. Eşikler sonradan DEĞİŞMEZ.

## 5. Uygulama kararı (baştan yazılı)

* Hiçbir kol geçmezse: üretime HİÇBİR ŞEY eklenmez (zaman bilgisi karar kaydında zaten var,
  gölge kaydın ek değeri yok). Sonuç DENEY_V6'ya yazılır, hafızaya "seans kapısı reddedildi" girer.
* Bir kol geçerse: `session_gate` üretim karar yoluna OFF/SHADOW/ENFORCE sözleşmesiyle bağlanır,
  replay config'i izler, parite hash'i doğrulanır, sonra dağıtım.

## 6. Baştan yazılı sınırlar

* P3 penceresi kısa (24 ay) ve kapı sırası onarımından beri işlem sayısı düşük olabilir;
  şart 3 (≥40 işlem) P3'te temeli bile eleyebilir. O durumda kol "P3'te ölçülemedi" diye
  raporlanır, "geçti" sayılmaz.
* S1 4h barda yalnız iki karar anını (12:00, 16:00 UTC) bırakır; işlem sayısı üçte bire iner.
* On coin 2026'da seçildi; borsa filtreleri geçmişe uygulanıyor; replay 18 uzman.

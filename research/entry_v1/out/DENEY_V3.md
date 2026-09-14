# DENEY V3 — giriş tetiği ve sabırlı motor vetosu

Protokol sonuçlar görülmeden yazıldı: `PROTOCOL_V3.md`. Altı koşu, başka koşu eklenmedi.
Kod: `work/entry-research-v1` · `6378c25`.

## 1. Sonuçlar

| pencere | kol | işlem | hesap getirisi | ort net R | isabet | PF | maks düşüş | maruziyet (gün) | coin |
|---|---|---|---|---|---|---|---|---|---|
| **P1** 2020-11→2022-08 | A0 tetiksiz | 444 | −5,41% | +0,0008 | %35,6 | 0,988 | 38,01% | %100,0 | 9 |
| P1 | **A1 tetikli** | 499 | **+17,22%** | +0,0143 | %36,3 | **1,036** | 35,98% | %99,4 | 9 |
| P1 | A2 tetik+veto | 77 | −13,63% | −0,1043 | %28,6 | 0,856 | 27,44% | %45,1 | 9 |
| **P2** 2022-09→2024-08 | A0 tetiksiz | 201 | −86,54% | −0,2337 | %27,9 | 0,584 | 86,54% | %95,5 | 10 |
| P2 | A1 tetikli | 198 | −80,12% | −0,2109 | %28,8 | 0,560 | 80,87% | %92,6 | 9 |
| P2 | A2 tetik+veto | 91 | −61,24% | −0,3588 | %25,3 | 0,431 | 61,26% | %53,5 | 10 |

Aynı pencerelerde al-tut referansı: P1 **+627,54%**, P2 (2022-09→2024-08 dilimi ayrıca
hesaplanmadı; 2022-09→2026-09 için +114,98%).

## 2. Kabul ölçütlerinin uygulanması (önceden yazılı, değiştirilmedi)

Bir kol gölge PAPER adayı olabilmek için **iki pencerede de**: (1) pozitif getiri,
(2) A0'dan yüksek, (3) ≥40 işlem ve ≥5 coin, (4) maliyet stresi altında pozitif.

| kol | P1 | P2 | karar |
|---|---|---|---|
| A1 tetikli | (1) ✓ +17,22% · (2) ✓ · (3) ✓ 499/9 · (4) ✓ +0,80% | **(1) ✗ −80,12%** · (2) ✓ | **tek pencerede olumlu, DOĞRULANMADI** |
| A2 tetik+veto | **(1) ✗ −13,63%** · **(2) ✗ A0'dan kötü** | **(1) ✗ −61,24%** · (2) ✓ | **REDDEDİLDİ** |

**Hiçbir kol gölge PAPER'a girmiyor.**

## 3. Belirsizlik

İşlem başına net R farkı, blok bootstrap (blok = coin × takvim ayı, 2000 tekrar):

| karşılaştırma | pencere | gözlenen | %95 aralık |
|---|---|---|---|
| A1 − A0 | P1 | +0,0135 | [−0,083, +0,112] |
| A1 − A0 | P2 | +0,0228 | [−0,178, +0,225] |
| A2 − A1 | P1 | −0,1186 | [−0,421, +0,196] |
| A2 − A1 | P2 | −0,1479 | [−0,408, +0,110] |

Dört aralık da sıfırı içeriyor. Hiçbir fark istatistiksel olarak ayırt edilemiyor.

**Tek tutarlı örüntü:** A1 − A0 farkının işareti **iki pencerede de pozitif**. Yani
planlanan girişi beklemek beklememekten iyi, ama etki küçük ve belirsiz.

## 4. Maliyet stresi (sabit işlem kümesi, ücret ×2 + slippage ×2)

| koşu | net | stresli | fark |
|---|---|---|---|
| A1 P1 | +17,22 | **+0,80** | −16,42 |
| A1 P2 | −80,12 | −86,96 | −6,84 |
| A0 P1 | −5,41 | −19,93 | −14,52 |

A1'in P1'deki kazancı maliyet iki katına çıkınca **neredeyse tamamen yok oluyor**
(+17,22 → +0,80). Sözleşme tuttu: sabit kümede artan gider daima kötüleştiriyor.

## 5. GERİ ÇEKİLEN İDDİA — veto hipotezi doğrulanmadı

2026-09-12'de 2024-09→2026-09 penceresinde bir **bölme** ölçtüm: sabırlı motorun
onayladığı 133 işlem −0,0127R, onaylamadığı 197 işlem −0,1321R, fark **+0,1194R**.
Bundan "bot doğru girişi biliyor, dinlemiyor" sonucunu çıkardım ve veto kolunu kurdum.

**Kontrollü deney bunun TERSİNİ gösterdi.** Vetoyu uygulamak iki pencerede de kötüleştirdi:
P1'de −0,1186R, P2'de −0,1479R. İşaret tam ters çevrildi.

Nedenini bu veriyle ayırmıyorum, ama iki aday açıklama var:

* **Bölme kontrollü deney değildi.** Yalnız onaylı işlemler açılsaydı sermaye, kapasite ve
  sıralama farklı olurdu. Bölmede "onaylı" grubun iyi görünmesi, onaylanmayanların
  sermayeyi tüketmesinin yan etkisi olabilir.
* **Seçim korelasyonu.** "Seviye yakında" olması sakin/yapılı piyasa demektir. O alt kümeyi
  seçmek maruziyetin üçte ikisini kesiyor ve ölçüme göre kesilenlerin içinde kazandıranlar
  da var (A2 maruziyeti %45'e düşürdü, getiriyi A1'in altına indirdi).

**Ders:** bir bölme (aynı koşu içinde alt gruplama) bir politika kararını desteklemez.
Politika, kendi kolu olarak koşturulmalıdır. Bu, bu oturumda aynı hatayı ikinci kez
yapmamdı — ilki havuzlanmış `p_win` çeyrekleriydi.

## 6. Ayakta kalan

Deney bir strateji üretmedi. Üreten şey **ölçüm kalitesi**: bu turda dört backtest-üretim
ayrışması bulundu ve tek kaynağa taşındı.

| ayrışma | replay'de durumu | onarım |
|---|---|---|
| ekonomi kapısı (`assess`) | **hiç çalışmıyordu** | `economics_gate.py` — iki motor aynı fonksiyon |
| başa-baş koruması | config 1.0 derken 0 ile koşuyordu | ctor'a bağlandı |
| borsa filtreleri | varsayılan (DOGE tick 0,01 vs gerçek 0,00001) | `symbol_filters.json` bağlandı |
| giriş tetiği | **hiç çalışmıyordu** | `entry_trigger.py` — iki motor aynı fonksiyon |

Bu dördü onarılmadan önce üretilen her backtest sayısı, üretimden farklı bir sistemi
ölçüyordu. Bundan sonraki ölçümler ilk kez aynı sistemi ölçüyor.

## 7. Bilinen sınırlar

* Hipotez görülmüş veriden doğdu; P1 ve P2 bakir değil.
* On coin 2026'da seçildi; hayatta kalma yanlılığı iki pencerede de var.
* Bugünkü borsa filtreleri geçmişe uygulandı (senaryo varsayımı).
* Replay `--no-patterns`, 18 uzman (üretimde 20).
* Tetik replay'de bar kapanışı çözünürlüğünde; üretimde fiyat bar içinde de seviyeye
  değebilir. Yani replay bazı geçerli girişleri kaçırır — yön olarak muhafazakâr.
* P1 2021 yükselişini kapsıyor; A1'in oradaki +%17,22'si al-tut'un +%627,54'ünün çok
  gerisinde.

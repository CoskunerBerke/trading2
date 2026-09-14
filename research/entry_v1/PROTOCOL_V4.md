# ÖN KAYITLI PROTOKOL V4 — mum formasyonu giriş onayı

**Yazıldığı an:** 2026-09-12, **hiçbir V4 koşusu görülmeden.**
**Kod:** `work/entry-research-v1` (worktree `C:\Users\berke\wt-entry`, `6378c25` + bu turun
KAYITSIZ yaması; yama `out/v4_code.patch` olarak saklanır, sha256'sı `v4_meta.json`'da).
**Kaynak:** kullanıcının paylaştığı mum formasyonu tablosu ve
`pratikteknikanaliz.com/2020/10/10/kazandiran-mum-formasyonlari-1/` (uzun gölgeli mum ve
içeri bar/harami; makale "tek başına güvenilmez, teyit mumu bekle" der).

## 1. Soru

Klasik mum formasyonları giriş ONAYI olarak kullanılırsa, üretimle aynı sistemi ölçen replay'de
işlem başına net R ve hesap getirisi iyileşir mi?

## 2. Mevcut durum (ölçüldü)

* Üretim `tradingbot/learn/candle_context.py` (candle_v1.0.0) 12 şekli zaten tespit ediyor:
  doji, çekiç/asılı adam, ters çekiç/kayan yıldız, marubozu, topaç, yutan ×2, sabah/akşam
  yıldızı, üç beyaz asker, üç kara karga.
* Bu tespit **hiçbir giriş kararına bağlı değil**: yalnız `candle_context` gölge kaydı ve
  `entry_challenger_v2` güven farkı (SHADOW, applied=0).
* Tabloda olup dedektörde OLMAYANLAR bu turda eklendi (candle_v1.1.0): harami ×2, delici
  çizgi, kara bulut örtüsü, cımbız dip/tepe. Eşikler geometrik, bu veriye uydurulmadı.
* `entry_challenger_v2` taraf kümesini KOPYALIYORDU; tek kaynağa bağlandı.

## 3. Kollar (tamamı tetikli = V3 A1 davranışı; parametre ızgarası YOK)

| kol | kural | ne test eder |
|---|---|---|
| **C0** | kuralsız | temel; determinizm hash'i **v3_a1 ile AYNI olmalı**, değilse sonuç okunmadan sebep bulunur |
| **C1** | son KAPANMIŞ 4h barda adayın yönüyle aynı taraflı şekil → gir; yoksa/karşıysa girme | tablonun saf kullanımı |
| **C2** | şekil bir önceki 4h barda oluşmuş VE son kapanmış bar teyit etmiş (`confirm_bars=1`) | makalenin kuralı |
| **C3** | yalnız VETO: son 4h barda KARŞI taraflı şekil varsa girme | "formasyon ekle" değil, "ters formasyona girme" |
| **C4** | C1'in günlük (1d) bar sürümü | zaman dilimi etkisi |

Kural `candle_rules.py`; üretim `_shapes` / `BULL_SIDE_SHAPES` / `evaluate_confirmation`
fonksiyonlarını çağırır, kopyalamaz.

## 4. Pencereler

| pencere | tarih (UTC) | durum |
|---|---|---|
| P1 | 2020-11-01 → 2022-08-31 | bu soru için İLK kez; hipotez veriden değil kullanıcı tablosundan geldi |
| P2 | 2022-09-01 → 2024-08-31 | aynı |

5 kol × 2 pencere = **10 koşu.** Başka koşu eklenmeyecek. Eşik/parametre araması yok.

## 5. Ölçütler

Birincil: 100 USDT başlangıçta **maliyet sonrası net hesap getirisi (%)**. Yanında işlem
sayısı, ort/medyan net R, isabet, PF, maks düşüş, coin sayısı, ret dökümü, determinizm hash'i.
Belirsizlik: blok bootstrap (coin × ay), 2000 tekrar, eşli; nokta tahmini gözlenen fark.
Maliyet stresi: sabit işlem kümesinde ücret ×2 + slippage ×2 (bir kez eklenir).

## 6. Kabul/ret (önceden bağlayıcı, V3 ile aynı)

Bir kol gölge PAPER adayı olabilmek için **P1 ve P2'nin her ikisinde de**:
1. net hesap getirisi pozitif, 2. C0'dan yüksek, 3. ≥40 kapanmış işlem ve ≥5 coin,
4. maliyet stresi altında da pozitif.

Tek pencerede sağlanırsa "tek pencerede olumlu, doğrulanmadı"; hiçbirinde sağlanmazsa
**reddedilir**. Sıfır işlem "zarar etmedi" sayılmaz (şart 3). Eşikler sonradan DEĞİŞMEZ.

## 7. Baştan yazılı sınırlar

* Şekil eşikleri (doji 0,10; çekiç fitil 0,55; cımbız tolerans 0,10) tek değerdir; ızgara
  yoktur, dolayısıyla "daha iyi eşik var mıydı" sorusu bu turda cevaplanmaz.
* On coin 2026'da seçildi: hayatta kalma yanlılığı. Bugünkü borsa filtreleri geçmişe uygulanır.
* Replay 18 uzman, üretim 20; tetik bar kapanışı çözünürlüğünde (üretimden daha katı).
* Formasyon 4h barın KAPANIŞINDA okunur; üretimde 15 dk tur vardır ama formasyon tanımı
  gereği bar kapanmadan okunamaz — burada üretimle aynı.
* C3 (veto) C0'a en yakın koldur; farkı küçük olacağı baştan beklenir.

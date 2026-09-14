# DEVAM NOKTASI — performans teşhisi + hedef mesafesi deneyi

Önceki tur: `RESUME-e1af166.md`. Bu tur yalnız YEREL araştırma ve dar onarımdır.
**VPS'e dağıtım YAPILMADI, servis yeniden başlatılMADI, açık pozisyon/stop DEĞİŞTİRİLMEDİ.**

## Kimlik

| Alan | Değer |
|---|---|
| VPS'te çalıştığı bildirilen sürüm | `e1af16624a94d441247429fe800779798f5ba2aa` |
| Bu turun dalı | `work/perf-diagnosis-v1` |
| Worktree | `C:\Users\berke\wt-diag` |
| Onarım commit'leri | `75a0f3e` → `c9fff3e` → `c2396e0` → `350928c` (taban `e1af166`) |
| Araştırma çıktıları | `C:\Users\berke\research\pfdiag_v1\` |
| VPS erişimi | **YOK** — `~/.ssh/trading2_ovh` parola korumalı, `BatchMode` → publickey ret |

## Kanıt tabanı ve sınırları

* **VPS state dışa aktarımı**, kesme `2026-09-07T21:16:41Z`, `app_head=12db804c`, konum
  `C:\Users\berke\research\pfres_v1\` (MANIFEST.txt her dosyanın sha256'sını taşır).
  Bu dosyalar ÖNCEKİ bir oturumda alındı; bu oturumda YENİDEN alınamadı.
* **Yerel PAPER kaydı** `C:\Users\berke\wt-ten\state\` — e1af166 ile 2026-09-11'de
  çalıştırılmış gerçek tur (10 karar, 0 açılış).
* **Tarihsel arşiv** `C:\Users\berke\wt-ten\data\history\futures` — Binance USDⓈ-M resmî
  arşivi, on coin, 1d/4h/1h/15m + funding, 2019-09'dan bugüne, manifest checksum'lı.
* 2026-09-11 ekranındaki 40 kapanış ÖLÇÜLMEDİ. Elimdeki kayıt 25 kapanışa kadar gidiyor;
  aradaki 15 kapanışın satırları yok.

## VPS'ten eksik kayıtları almak için TEK komut

Salt okunur, servis durdurmaz, state yazmaz:

```
scp -i C:\Users\berke\.ssh\trading2_ovh C:\Users\berke\trading2-deploy\tb-export-diag-20260911.sh ubuntu@<VPS_HOST>:~/
ssh -i C:\Users\berke\.ssh\trading2_ovh ubuntu@<VPS_HOST> "bash ~/tb-export-diag-20260911.sh"
```

Son satırda `EXPORT_OK` ve paketin yolu görünür; paketi `scp` ile
`C:\Users\berke\research\pfdiag_v1\` altına alın.
Betik sha256: `6385acb29bef579f18f07d656a3dc43cab6f9587b48e591250f506deb884f054`.

## Bu turda ne yapıldı

1. **Teşhis** — `research\pfdiag_v1\out\TESHIS.md`
2. **Doğrulanmış dar onarımlar** — `research\pfdiag_v1\out\ONARIMLAR.md`
   (birim/etiket, backtest-canlı çıkış paritesi, ekonomi kapısında fail-open, iki test kusuru)
3. **Ön kayıtlı deney protokolü** — `research\pfdiag_v1\PROTOCOL.md`
   (pencereler, ızgara, maliyet stresi ve kabul ölçütleri sonuçlar görülmeden yazıldı;
   somut SEÇİM kuralı yazılmamıştı — `out/ARASTIRMA_KAYDI.md` bunu düzeltiyor)
4. **Deney** — `research\pfdiag_v1\out\DENEY.md`
5. **Araştırma kaydı ve bağımsız inceleme** — `research\pfdiag_v1\out\ARASTIRMA_KAYDI.md`

### Bağımsız inceleme

Oturum sonunda tek bir karşıt inceleme yalnız son değişiklikler ve bu belgelerdeki iddialar
üzerinde koşturuldu. Üç kod kusuru (ekonomi kapısında fail-open, boşa geçen bir güvenlik
testi, bayat sütun adı) ve dört yazım hatası (ön kayıt provenansı, `p_win` "ters sıralama"
iddiası, "bit-aynı" abartısı, eksik artık satırı) buldu. Hepsi bağımsız doğrulandıktan
sonra onarıldı; bulguların tam listesi `out/ARASTIRMA_KAYDI.md` sonunda.

## Tekrarlanabilir komutlar

Tümü `C:\Users\berke\research\pfdiag_v1` içinden çalışır ve `wt-diag` kodunu,
`wt-ten\data` arşivini kullanır (kod ve veri AYRI, kopya yok):

```
python run_replay.py --run-id <id> --k <k|null> --k2 <k2|null> --from YYYY-MM-DD --to YYYY-MM-DD
python grid_train.py                 # GELISTIRME izgarasi (7 kosu)
python grid_test.py <k|null>         # TEST + maliyet stresi (2 kosu)
python analyze.py <run_id> ...       # olcutler + blok bootstrap
python trade_table.py                # VPS kapanislarinin teshis tablosu
python funnel2.py                    # dedup'lu giris hunisi
```

Her koşu `out/meta_<run_id>.json` içine config sha256'sını, doğrulanmış config değerlerini,
karar/açılış sayısını, determinizm hash'ini ve funding kapsamını yazar.

## Dosyalar

```
C:\Users\berke\research\pfdiag_v1\PROTOCOL.md              on kayitli protokol
C:\Users\berke\research\pfdiag_v1\out\TESHIS.md            teshis
C:\Users\berke\research\pfdiag_v1\out\ONARIMLAR.md         onarimlar + once/sonra olcum
C:\Users\berke\research\pfdiag_v1\out\DENEY.md             deney sonucu ve karar
C:\Users\berke\research\pfdiag_v1\out\trade_diagnostics.json
C:\Users\berke\research\pfdiag_v1\out\entry_funnel_dedup.json
C:\Users\berke\research\pfdiag_v1\out\closed_trades_20260907.json
C:\Users\berke\trading2-deploy\tb-export-diag-20260911.sh  VPS salt-okunur disa aktarim
```

## Sonuç — tek cümlelik özet

Mevcut strateji **dokunulmamış 24 aylık test penceresinde negatif**: 195 işlem, ortalama
−0,1898R, %95 aralık [−0,353, −0,029] (sıfırı içermiyor). Tek aday (hedef mesafesi
`k = 0,75`) **REDDEDİLDİ**: test ortalaması −0,1824R, temelden farkı +0,0087R ve aralığı
[−0,144, +0,158] sıfırı içeriyor. Geliştirmedeki +0,2163R'lik üstünlük teste taşınmadı.

| koşu | n | net USDT | ort R | %95 aralık | isabet |
|---|---|---|---|---|---|
| temel (test) | 195 | −76,19 | −0,1898 | [−0,353, −0,029] | %31,8 |
| aday k=0,75 (test) | 230 | −84,85 | −0,1824 | [−0,295, −0,080] | %54,4 |
| temel + maliyet stresi | 201 | −63,75 | −0,1549 | [−0,317, +0,010] | %31,3 |
| aday + maliyet stresi | 229 | −86,10 | −0,1732 | [−0,289, −0,062] | %54,6 |

Kabul ölçütlerinden 5'inin yalnız biri (örnek yeterliliği) geçti.

## Açık iş

1. VPS'ten güncel kayıtları almak (yukarıdaki tek komut) — 09-07 sonrası 15 kapanış eksik.
2. Onarımların VPS'e dağıtımı — paket HAZIRLANMADI; bu tur yalnız yerel doğrulama içeriyor.
3. BTC 2x'te açılamıyor kısıtı (önceki turdan devam) — karar kullanıcıda.

4. **Gölge PAPER paketi HAZIRLANMADI** — aday reddedildiği için gölge karşılaştırmasına
   sokulacak bir değişiklik yok. Paket, pozitif ya da en azından ayırt edilebilir bir aday
   çıktığında anlamlıdır.
5. **Ölçülen sonraki adım:** girişte kullanılan olasılık (`p_win`) geliştirme verisinde
   sonucu AYIRMIYOR (en yüksek çeyrekte gerçekleşen isabet %17,7, en düşükte %31,6 —
   sıralama ters). Ayrım gücü olan tek gözlem yön (LONG −0,193R / SHORT −0,403R) ve rejim
   (BREAKOUT −0,099R / HIGH_VOL −0,720R). Üçünü birden seçen en iyi iç örnek dilim bile
   −0,028R. Ölçülebilir hedef: verili hedef mesafesinde isabetin 3–10 puan artması.

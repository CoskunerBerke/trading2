# ÖN KAYITLI PROTOKOL V7 — piyasa rejimi kapısı, basit temel ve hayatta kalma kontrolü

**Yazıldığı an:** 2026-09-13, **hiçbir V7 koşusu görülmeden.**
**Kod:** `work/entry-research-v1` `5fc4507` + `tradingbot/regime_gate.py` (kayıtsız, `out/v7_code.patch`).
**Kaynak:** kullanıcı "kâr için ne lazımsa yap, neyi yanlış yapıyorsak düzeltelim" dedi. Bu tur
gösterge DEĞİL, ölçülmüş mekanizmanın kendisini sınar.

## 1. Üç soru

**Q1 (rejim):** Sistem yalnız BTC yükseliş rejiminde ve yalnız LONG çalışırsa üç pencerede de
temeli geçer mi? Mekanizma: 2021 LONG +0,24R, 2023 sonrası iki yön de negatif, SHORT kesiti
−0,63R (239 kurulum). Kırmızı takım zaten `AGAINST_BTC_REGIME` YUMUŞAK cezası uygular; bu tur
SERT kapıyı sınar.

**Q2 (basit temel):** Tek kurallı, parametresiz bir sistem (günlük close > EMA200 ise tut, değilse
nakit; on coin eşit ağırlık; komisyon ve kayma dahil) aynı pencerelerde botu geçiyor mu?
Slaytın "az kural az parametre" iddiasının doğrudan ölçümü. Bu bir REFERANSTIR, üretim adayı
değil; maruziyet sınıfı farklı (kaldıraçsız, stopsuz).

**Q3 (hayatta kalma):** Evren 2026 bilgisiyle değil 2020-10 bilgisiyle seçilseydi (o ay Binance
USDT-M vadelide hacme göre ilk 10, bugün hâlâ listede olanlar arasından) temel sonuç ne olurdu?
2021 kârının ne kadarı "bugün hayatta olan coinleri seçmiş olmaktan" geliyor?

## 2. Dürüstlük beyanı

* Q1'in hipotezi yıl düzeyindeki gözlemden geldi ("2021 iyi, 2022+ kötü"); P1/P2 bu anlamda
  kirli. P3 rejim sorusu için temiz (P3'te rejime göre hiç bakılmadı). Kabul üç pencerede birden.
* Q3'te bugün listeden kalkmış coinler (EOS, MATIC, MKR, FTM, WAVES, OMG, ICX …) veri
  alınamadığı için evrene GİREMEZ; artık hayatta kalma yanlılığı kalır ve öyle raporlanır.
* Rejim tanımı tek değer: BTC günlük close > EMA200. Izgara yok.

## 3. Koşular

| grup | koşu |
|---|---|
| Q1 | R1 yalnız LONG + yalnız UP; R2 DOWN'da giriş yok; R3 UP→LONG, DOWN→SHORT. × P1, P2, P3 = **9 koşu**; temel B0 = `v4_c3_p1/p2`, `v6_b0_p3` |
| Q2 | `benchmarks_v7.py`: al-tut ve EMA200 trend temeli, üç pencere (koşu değil, hesap) |
| Q3 | 2020-10 hacim sıralamasıyla seçilen 10 coin, temel yol (tetik + mum vetosu), P1/P2/P3 = **3 koşu** |

Başka koşu eklenmeyecek.

## 4. Kabul (önceden bağlayıcı)

**Q1:** V5/V6'daki beş şart, üç pencerede de. **İstisna (baştan yazılı):** rejim kapısının doğru
davranışı düşüş rejiminde işlem AÇMAMAKTIR. Bir pencerede BTC'nin UP rejiminde geçirdiği gün
payı %30'un altındaysa, o pencerede şart 3 (≥40 işlem) "uygulanamaz" sayılır ve şart 1, 2, 4, 5
yeterlidir; ama kol üç pencere toplamında en az 80 işlem üretmelidir (hiç işlem yapmayan kol
kazanan sayılmaz). Sıfır işlemli pencerede getiri 0 ve "temelden iyi" doğrudur; bu tek başına
kabul getirmez, toplam şartı bunu engeller.

**Q2:** Kabul/ret yok; rapor. Temel botu her pencerede geçiyorsa bu, sadeleştirme lehine kanıttır.

**Q3:** Kabul/ret yok; rapor. 2020-seçimli evrende P1 getirisi 2026-seçimli evrenin yarısının
altındaysa "2021 kârının önemli kısmı hayatta kalma yanlılığı" hükmü verilir.

## 5. Uygulama kararı (baştan yazılı)

* Q1'de bir kol geçerse: `regime_gate` üretime OFF/SHADOW/ENFORCE sözleşmesiyle bağlanır
  (candle/chart ile aynı), replay config'i izler, parite hash'i doğrulanır, dağıtım paketi hazırlanır.
* Geçmezse: üretime hiçbir şey eklenmez; modül araştırma paketine taşınır.
* Q2 ve Q3 yalnız bilgi üretir; hiçbir üretim değişikliğine tek başına yol açmaz.

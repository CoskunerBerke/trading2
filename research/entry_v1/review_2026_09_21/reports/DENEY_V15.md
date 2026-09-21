# DENEY V15 — Box Theory: ölçüm sonucu

Protokol: `PROTOCOL_V15.md` (karar kuralı **sonuçlar görülmeden** yazıldı, değiştirilmedi).
Ham veri: `out/BOX_SWEEP_v15_a.json` · tablo: `out/BOX_SWEEP_v15_a.md`

## Hüküm

**42 kolun 0'ı karar kuralını geçti.** Kural, ölçülen hiçbir konfigürasyonda avantaj göstermedi.

Karar kuralı: üç pencerede de net ortalama R'nin %95 güven aralığı sıfırın üstünde ve pencere başına
≥30 işlem. Fiilî örneklem pencere başına **2.500–48.000 işlem** olduğu için bu bir "veri az" sonucu
değildir; güven aralıkları ±0,01–0,04R genişliğinde.

## Kurulum

* 10 coin (giriş evreni), USDⓈ-M perpetual, 5m + 1d.
* Arşiv `C:\Users\berke\wt-ten\data\history` — `history-validate`: 70 seri, `invalid: 0`;
  BTC 5m 618.048 satır, 0 boşluk / 0 kopya / kalite 1,0; indirme `bad_chunks: 0`.
* Pencereler P1 2020-11→2022-08 · P2 2022-09→2024-08 · P3 2024-09→2026-08.
* 6 asgari-stop eşiği × 7 çıkış × 3 pencere = 42 kol; toplam ~1,1 milyon tetik tarandı, süre 43 dk.
* Maliyet: taker %0,05 + kayma 3 bps **iki bacakta**; funding arşivden (eksik seri 0, bilinmeyen sorgu 0).
* Aynı barda stop+hedef → **stop** (worst_case). Doluluk her kolda ayrı.

## Ana tablo (çıkış = box_opposite ve r2; tamamı raporda)

| eşik | pencere | n | **brüt** ortR | **net** ortR | maliyet/R | medyan stop% | **cap_ok** |
|---|---|---|---|---|---|---|---|
| %0 | P1 | 26.386 | +0,0197 | −0,2104 | 0,237 | 0,823 | **0,089** |
| %0 | P2 | 26.645 | +0,0389 | −0,3380 | 0,381 | 0,543 | **0,035** |
| %0 | P3 | 32.110 | −0,0300 | −0,4558 | 0,428 | 0,483 | **0,021** |
| %1,0 | P1 | 14.825 | −0,0313 | −0,1395 | 0,112 | 1,339 | 0,162 |
| %1,0 | P2 | 9.128 | +0,0403 | −0,0807 | 0,121 | 1,216 | 0,104 |
| %1,0 | P3 | 9.671 | −0,0442 | −0,1692 | 0,126 | 1,189 | 0,073 |
| %2,22 | P1 | 5.576 | −0,0458 | −0,1040 | 0,058 | 2,567 | **0,995** |
| %2,22 | P2 | 2.568 | +0,1013 | +0,0415 | 0,060 | 2,454 | **0,995** |
| %2,22 | P3 | 2.623 | −0,0312 | −0,0930 | 0,061 | 2,448 | **0,994** |

`cap_ok` = üretimin tek-coin notional tavanını geçebilen, yani **gerçekten açılabilecek** işlemlerin oranı.

## Üç ayrı sonuç, karıştırılmamalı

**1. Sinyalin kendisinde avantaj yok.** Maliyet sıfır sayılsa bile brüt ortalama R 42 kolun tamamında
−0,053 ile +0,101 arasında sıkışıyor ve deseni sabit: **P2 artı, P1 ve P3 eksi.** Bu bir avantaj değil,
tek pencerelik rejim şansıdır — karar kuralı tam olarak bunu elemek için üç pencere istiyor.

**2. Maliyet, videonun stopuyla öldürücü.** Kuralın stopu bir 5m mumu kadar dar; eşiksiz kolda medyan
stop fiyatın %0,48–0,82'si ve gidiş-dönüş maliyet (%0,16) **riskin %24–47'sini** yiyor. Eşik %2,22'ye
çekilince maliyet 0,06R'ye iniyor — ama net yine eksi, çünkü altında taşıyacak avantaj yok.

**3. Kural mevcut risk çerçevesinde boyutlandırılamıyor.** `notional/özkaynak = risk% / stop%`, tavan
`%30 × kaldıraç`. Videonun stopuyla notional özkaynağın 4–8 katı çıkıyor:

| eşik | üretimde açılabilen işlem oranı |
|---|---|
| %0 (video birebir) | **%2–9** |
| %1,0 | %7–21 |
| %1,33 (kaldıraç 5 sınırı) | %12–29 |
| %2,22 (kaldıraç 3 sınırı) | **%99,5** |

Doğrudan kanıt: aynı kuralı tam üretim replay'iyle koşturunca (BTC, 3 gün)
**865 karar → 143 sinyal → 0 pozisyon**, 143 retin tamamı `MAX_POSITION_PCT`.

## Çıkış karşılaştırması (eşik %2,22, hepsi boyutlandırılabilir)

| çıkış | P1 net | P2 net | P3 net | **en kötü pencere** |
|---|---|---|---|---|
| box_mid | −0,0805 | +0,0050 | −0,0785 | **−0,0805** |
| r2 | −0,0849 | +0,0149 | −0,0898 | −0,0898 |
| r3 | −0,0843 | +0,0289 | −0,0934 | −0,0934 |
| r1 | −0,0749 | −0,0137 | −0,0944 | −0,0944 |
| r1.5 | −0,0800 | −0,0034 | −0,0944 | −0,0944 |
| box_opposite | −0,1040 | +0,0415 | −0,0930 | −0,1040 |
| none | −0,1122 | +0,0402 | −0,0961 | −0,1122 |

**Hiçbiri pozitif değil.** "Kazanan" yok; `box_mid` yalnızca *en az kötü* olandır. Çıkışın videoda
eksik olması bir boşluktu, ama sonucu belirleyen şey o değildi: yedi çıkış arasındaki fark
0,03R, sıfıra olan uzaklık ise her birinde bundan büyük.

## Ne YAPILMADI

* Kol seçimi sonuçlara bakılarak **değiştirilmedi**; karar kuralı protokolde önceden yazılıdır.
* P2'nin pozitifliği "avantaj" diye sunulmadı — üç pencereden biri.
* Eşik/çıkış ızgarası sonuç iyileşene kadar genişletilmedi; eşikler üretim uygunluk aritmetiğinden seçildi.
* Aşama B (tetik/long-stop/bant/taraf/stride kolları) **koşulmadı**: Aşama A'da taşıyıcı bir sinyal
  çıkmadığı için alt kolları taramak, gürültü içinde pozitif aramak olurdu.

## Karar

Kâğıt defter (`strategy_paper_box`) **AÇIK** — kâr beklentisiyle değil, iki ölçüm için:

1. canlı davranışın backtest'le tutup tutmadığını görmek (parite), ve
2. panelde kuralın çalışır halini göstermek.

Eşik `min_stop_pct: 2.22` **zorunludur**: onsuz defter her sinyali reddedip sessizce ölü kalır.
Beklenen sonuç **negatiftir** (−0,08R civarı) ve defter bunu ispatlamak için değil, ölçüm hattının
doğru çalıştığını göstermek için açıktır. Kapatmak tek satır: `enabled: false`.

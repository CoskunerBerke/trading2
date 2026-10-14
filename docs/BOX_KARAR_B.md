# Box defteri — karar B (2026-09-27, kullanıcı onayı)

Mod PAPER; gerçek para yok. Box kuralının kendisi (kutu, tetik, stop, hedef) değişmedi.

## Neden

- Box karnesi 19 işlemde −17,86 USDT. Kayıpların hepsi eski koddaki teknik hatalardan geldi (günlerce açık kalan
  pozisyonlar). Bu hatalar önceki dağıtımda düzeltildi.
- Laboratuvarda Box'ın 42 ayarının hiçbiri sıkı testi geçmedi (işlem başına yaklaşık −0,07R). Kâr beklentisi yok.
- Canlıda ortak yapı katmanı ENFORCE modundaydı. Box'ın giriş ve stop'unu test edilmemiş bir sürümle değiştiriyordu.
- Gün sonu çıkışı dolum saatine bakıyordu. 23:55 barının sinyali gece yarısından sonra dolunca pozisyon kapanmak
  yerine ~24 saat daha açık kalıyordu.

## Ne değişti

1. **Yapı katmanı Box için SHADOW** (`config.yaml` → `structures.b1_box_fade`). Yapı analizi hesaplanır ve kaydedilir,
   ama Box'ın kararına karışmaz. Box laboratuvarda test edilen kuralıyla işlem açar.
2. **Gün sonu, sinyal barının gününe göre** (`box_theory.decide`). Sinyal barı, pozisyonun veri kaynağı kaydındaki
   girişte okunan 5m barıdır (`features.data_source.bars["5m"]`). Kayıt yoksa ya da bozuksa eski davranış: dolum anı.
   Açık eski pozisyonlar da bu kaydı taşır; onlar da düzelir.
3. **Günü bitmiş sinyalle giriş yok.** Günün son barı (23:55) gece yarısında kapanır ve onu okuyan tur hep ertesi
   gündedir. O günün kutusuyla yeni günde girilmez; girilseydi gün sonu kuralı onu hemen kapatırdı (boşa maliyet).

Testler: `tests/test_box_theory_v15.py` (gün sonu ve karar anı bölümü). Eski kodda bu testlerden 4'ü düşer.

## Geri almak

- Yapı katmanı: `structures.b1_box_fade: ENFORCE`.
- Gün sonu düzeltmesi kod değişikliğidir; geri almak için commit geri alınır.
- Box'ı tamamen kapatmak: `strategy_paper.extra` içinde `b1_box_fade` için `enabled: false`. Önce açık pozisyonların
  kapanmasını beklemek daha temizdir.

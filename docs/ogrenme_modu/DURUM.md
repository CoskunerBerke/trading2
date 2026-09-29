# Öğrenme modu L1: durum kaydı (güncelleme 2026-09-28 ~07:00 UTC)

**Kodlandı, dağıtılmadı (2026-09-29):** karşı-olgusal NET etiket `cf_label_v2` (CONTRACT "cost-bias fix" bölümü). Kesinleşen
"olsaydı" etiketine defterin kendi yürütme modeliyle aynı barlarda yeniden oynatılan net R eklenir; eski brüt alanlar kalır.
Karne/panel net ortalamayı raporlar (brüt yalnız bilgi). Eski etiketler: son ~25 saati kod içinde tembel, daha eskisi
`scripts/cf_backfill_net.py` ile (worker durmuşken). C4 sessizliği: hata bulunmadı (2 kapanışta 0 sinyal olasılığı %9–24);
VPS'te salt okunur teşhis komutu çalıştırılacak. Sıradaki dağıtım betiğine eklenecekler: (1) worker durmuşken önce kuru,
sonra `--apply` ile `cf_backfill_net.py`; (2) `--check` karşı-olgusal satırına net n / net ort. R (brüt) ve etiket sürümü
sayıları (v2 / v1 / v1c), yeni etiketlerde `label_version` yoksa uyarı.

**Sürüyor (2026-09-29 08:15 UTC):** 10 saatlik canlı kontrol temiz (bellek 3,6G/tepe 4,5G, tur 6 dk, Box kaçan bar 0/saat). Bulgular: (1) "olsaydı" R'si BRÜT — Box karşı-olgusal ort. +0,40R, gerçek öğrenme işlemleri −0,64R; maliyet (ücret+kayma) düşülmüş, sürümlü etiket (r_gross/cost_r/r_net/label_version) kuruluyor ve eski kayıtlar yeniden hesaplanacak. (2) C4 10 saatte 0 sinyal/0 karşı-olgusal — olağan mı hata mı inceleniyor. (3) Ortak Deneyim Katmanı 1. aşama tasarımı öğrenme modu sonrası koda göre yenileniyor (karşı-olgusal satırları net R ile birinci sınıf satır). Ardından: uygulama → bağımsız inceleme turları → tek dağıtım betiği.

**DAĞITILDI (2026-09-28 21:20 UTC, kullanıcı):** VPS `a8fe2a5` → `c0b8c94` (`tb-deploy-c0b8c94.sh --detach`). Kuru çalışma 26/26 değişmez; doğrulanmış yedek `tradingbot-manual-20260928T211042Z.tar.gz` (24.440 dosya); worker 60 sn kararlı; yeniden başlatma sonrası bellek 556M (tepe 668M) / 6G, OOM 0. Önceki tepe 4,8G. İzleme: `--check` (ilk tam turdan sonra; Box için ikinci örnek ≥50 dk sonra).

**Güncel (2026-09-28 akşam): DAĞITIMA HAZIR.** Kod `c0b8c94`, dağıtım betiği `deploy/releases/tb-deploy-c0b8c94.sh` (`9f54393`, sha256 `d258fb3e…264f`). Dağıtımı yalnız kullanıcı yapar.
- Dört inceleme turu: `627eac5` (F1–F9) · `49256cb` (politika rezervi, A15 +CF, geri dönüş güvenliği) · `e6dac70` (politika tabanı öğrenicisi, artımlı bellek okuyucuları) · `c0b8c94` (panel bellek sınırı, okuyucu sağlamlığı, POLICY_BASIS_LOST alarmı). Son tam paket 3484 geçti / 0 düştü; öğrenme kapalıyken 48 tur öncekiyle birebir.
- Ertelenenler (izlenecek): deneyim önbelleği ~1,8 MB/gün; çıkış değerlendirmesi CPU'su kapanan işlem sayısıyla doğrusal; deney olaylarının her turda baştan okunması (~1,9 sn/okuma 30. günde); trade_memory/position_path/deney olayları ~12,5 MB/gün disk (döndürme yok); T2/M2/D4 "olsaydı" etiketi 30 barlık ufuk (yaklaşık); D4/C4 sessiz 60 dk pencere bitişleri kayıtsız; depo unit dosyası hâlâ MemoryMax=4G (VPS 6G).

---

(Aşağısı 05:35 UTC ara kaydıdır.)

**Bu commit bir ara kayıttır (WIP), dağıtılamaz.** VPS'e giden yol yalnız `deploy/releases/tb-deploy-<tip>.sh` betikleridir, ve bu kod için henüz betik yok. VPS'te son doğrulanan kod `a8fe2a5`. `7ad8832` betiği hazır, kullanıcının çalıştırması bekleniyor.

## İstek

Kullanıcı: "bütün algoritmalardaki bir şeyin sınırı olmaması lazım … hepsinde mümkün olduğunca en fazla işleme girmesi lazım ki, botumuz öğrenebilen bir bot olsun".

Tasarım belgeleri:
- `SPEC.md`: sınır envanteri ve gerekçeler.
- `CONTRACT.md`: bağlayıcı kararlar, arayüzler, `config.yaml` değerleri.

## Yapılanlar (kodlandı, testleri yazıldı)

| Aşama | Kapsam | Yeni testler |
|---|---|---|
| A — temel | `learning_mode.py` (anahtar, `fit_size`, `profile_for`, kaldıraç geri düşüşü), `learning_cf.py` ("olsaydı" kaydı), `config_v3.LearningModeSection` (yalnız PAPER; aksi ConfigError), `FuturesLedgerV2.open(allow_shrink=…)` çağrı başına (kalıcı değil), P0 günlük düzeltmesi (EXCHANGE_REJECTED → OPEN_FAILED), `ShadowTrade` ek alanları | 140 geçti |
| B1 — defterler | T2/M2/Box/D4/C4: slot boyutlandırma, öğrenme risk motoru, "olsaydı" kaydı, Box `min_stop_pct` 0,32, C4 eş eşleşen varyasyonlar, T2/M2 yapı girişi SHADOW | 25 geçti (eski kodda 25'i de düşüyor) |
| B2 — Formasyon | D1 adet tavanı, D5 bekleme, D6 ölçekleme, D7 en düşük R/R, D16 likidite yeniden deneme, D17 derinlik, D18 hacim taşıma, S9 evren birleşimi | 20 geçti |
| C1 — ana bot | slot boyutlandırma, çarpanlar yalnız kayıt, kaldıraç geri düşüşü, en küçük emir yükseltmesi, S1 rejim / S2 mum / S3 yapı girişi SHADOW, S5 keşif işlemleri, sinyal anahtarlı "olsaydı" kaydı | 35 geçti |
| C2 — bağlantı | motor → defterler / Box zamanlayıcısı / Formasyon, `config.yaml` `learning_mode` bölümü, panel ("ÖĞRENME MODU AÇIK"), karne önce/sonra ayrımı | 25 geçti |

Testler Python 3.12 ister: depodaki f-string sözdizimi 3.12'ye özgü, CI da 3.12 kullanıyor.

## Bağımsız inceleme bulguları (düzeltme aşaması bu kayıt sırasında çalışıyordu)

**Yüksek**
- M2'de yapı girişi SHADOW olunca, yapı katmanının onayladığı girişler dahil bütün öğrenme girişlerinde M2'nin `ENTRY_STRUCTURE_FAILED` çıkışı kapanıyor. M2'nin çıkışları değişmemeliydi (`strategy_paper.py` ~1041).

**Orta**
- En küçük emir yükseltmesi, risk motoru notional'ı 4 haneye yuvarlayınca `MIN_NOTIONAL` ile düşüyor. Yükseltmelerin defterlerde ~%40'ında, Formasyon'da ~%15'inde oluyor.
- Reddedilip "olsaydı" kaydı yazılan sinyal sonraki turda gerçekten açılırsa, kayıt geri alınmıyor.
- Ana botun "olsaydı" kayıtları eski etiketleyici yüzünden bir bar erken kapanıyor.
- P0: temel yoldaki başarılı spot dolumlar, eski spot durum hatası yüzünden OPEN_FAILED sayılıyor.

**Düşük**
- Defter boyutlandırması stop mesafesini kaymadan önceki fiyattan ölçüyor; risk etiketi gerçek riski olduğundan az gösteriyor.
- Defterler `max_total_open_risk_pct` ayarını okumuyor, hep 100 kullanıyor.
- `label_pending` her geçişte bütün bekleyen yolları yeniden yürüyor ve bunu defter kilidi altında yapıyor.
- Askıdaki Formasyon temel davranışla birebir aynı değil.

## Sıradaki adımlar

1. Düzeltme ve tam regresyon: bulguları düzelt, tam test paketi, CI listesi, ruff.
2. İncelenmiş commit'ler, ardından yeni dağıtım betiği `tb-deploy-<tip>.sh`:
   - değişmezler: PAPER, gateway paper, `learning_mode` etkin, defter slotları;
   - ön koşul `7ad8832`.
3. Kullanıcıya tek SSH komutu. Dağıtımı yalnız kullanıcı yapar.
4. Sonra Ortak Deneyim Katmanı 1. aşama: salt kayıt, her işlem ve "olsaydı" kaydı girişteki piyasa durumuyla birlikte ("daha önce görmüştüm, kazanmıştım").

Ertelenenler (L1 dışında):
- zaman dilimi başına veri kimliği (B7);
- evrenin 60 coine çıkarılması;
- ayrı 4h zamanlayıcısı;
- büyük sanal sermaye;
- öğrenmenin kararı etkilemesi (S15);
- T2/M2/D4 için kural-çıkışı etiketlemesi (şimdilik 30 barlık ufuk, yaklaşık).

`l1_asama_raporlari.json`: aşama ajanlarının ham raporları; yapılmayanlar ve arayüz notları dahil.

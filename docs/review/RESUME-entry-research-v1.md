# DEVAM NOKTASI — ölçüm temsili + giriş kuralı araştırması

Önceki tur: `RESUME-perf-diagnosis-v1.md`. Bu tur yalnız YEREL araştırma ve dar onarımdır.
**VPS'e dağıtım YAPILMADI, servis yeniden başlatılMADI, pozisyon/stop DEĞİŞTİRİLMEDİ.**

## Kimlik

| alan | değer |
|---|---|
| VPS'te doğrudan doğrulanan son sürüm | `e1af16624a94d441247429fe800779798f5ba2aa` |
| önceki turun yerel ucu | `350928c88312906ac9d46107b3b88d07b6faea11` |
| bu turun dalı | `work/entry-research-v1` · worktree `C:\Users\berke\wt-entry` |
| üretim düzeltme paketi | `work/prod-fixes-on-e1af166` · worktree `C:\Users\berke\wt-fixpkg` |
| araştırma çıktıları | `C:\Users\berke\research\entry_v1\` |
| VPS erişimi | **YOK** — anahtar parola korumalı, `BatchMode` → publickey ret |

## Bu turda ne bulundu

1. **Replay ekonomi kapısını hiç çalıştırmıyordu.** `assess`, `opportunity`,
   `size_multiplier` geçen tek satır yoktu. Önceki turun "üretim karar yolu" dediği şey
   kapısı olmayan bir aday popülasyonuydu. Kapı `tradingbot/economics_gate.py` ile tek
   kaynağa taşındı; canlı motor ve replay aynı fonksiyonu çağırıyor.
2. **Replay varsayılan borsa kurallarıyla çalışıyordu.** DOGE fiyat adımı 0,01 (gerçek
   0,00001), BTC minimum 5 (gerçek 50), miktar adımı her sembolde 0,001 (DOGE'de gerçek 1).
   DOGE fiyatı bir kuruşa yuvarlanıyordu. Gerçek `symbol_filters.json` bağlandı.
   **Bu, önceki turun tüm replay sonuçlarını da etkiler.**
3. **Sıfır fill fiyatı çökertiyordu.** `raw_qty = notional / fill` sıfıra bölüyordu
   (DOGE 2021, SHORT açılışı aşağı yuvarlanınca). Üretim kodunda da vardı; RET'e çevrildi.
4. **Ölçülen örüntü:** filtresiz temelin işlem başına net R'si 2021'de **+0,124**,
   2022'de −0,011, 2023'te −0,154, 2024'te −0,219. 2021'de LONG +0,241R, 2022'de SHORT
   +0,056R; 2023'ten sonra iki yön de negatif. **Bu bir mekanizma kanıtı DEĞİLDİR** —
   hayatta kalma yanlılığı, bileşiklenme/kapasite çöküşü ve bugünkü filtrelerin geçmişe
   uygulanması aynı tabloyu üretebilir (bkz. `out/KAPANIS.md` §6).
5. **Üç giriş hipotezi de reddedildi.** Bu üç kural, bu 18 parametreyle ve bu ölçüm
   ortamında ölçülebilir iyileşme üretmedi; "giriş kalitesi sorun değil" demek DEĞİLDİR
   (replay'de 20 değil 10 uzman çalışıyor).

## Sonuç tablosu

| koşu | n | hesap getirisi | ort R | PF | Sharpe |
|---|---|---|---|---|---|
| B1a mevcut bot KAPILI (2024-09→2026-09) | 3 | −1,97% | −1,0266 | 0 | −1,16 |
| B1b kapısız (2024-09→2026-09) | 279 | −51,99% | −0,1117 | 0,78 | −0,63 |
| B1b kapısız (GELİŞTİRME 2022-09→2026-09) | 229 | −90,16% | −0,2368 | 0,58 | −0,83 |
| B1b kapısız (DEĞERLENDİRME 2020-11→2022-08) | 532 | **+91,10%** | +0,0965 | 1,18 | +1,09 |
| aday `h1_r40_c0.2` (GELİŞTİRME) | 197 | −60,57% | −0,1900 | 0,68 | −0,88 |
| aday `h1_r40_c0.2` (DEĞERLENDİRME) | 82 | +6,67% | +0,0392 | 1,08 | +0,28 |
| B2 nakit | 0 | 0,00% | — | — | — |
| B3 al-tut (test / geliştirme / değerlendirme) | — | +8,34% / +114,98% / +627,54% | — | — | — |

## Tekrarlanabilir komutlar

`C:\Users\berke\research\entry_v1` içinden; kod `wt-entry`, veri `wt-ten\data` (kopya yok):

```
python run_rule.py --rule <ad|none> --run-id <id> --from YYYY-MM-DD --to YYYY-MM-DD [--gate]
python run_all.py            # 4 referans + 18 yapilandirma, 8 paralel
python report.py <run_id> <from> <to>
python benchmarks.py <from> <to>          # al-tut
python make_deney_v2.py                   # DENEY_V2.md uretir
```

Her koşu `out/meta_<id>.json` içine karar/açılış sayısını, ret dökümünü, determinizm
hash'ini ve funding kapsamını yazar.

## Dosyalar

```
research\entry_v1\PROTOCOL_V2.md               on kayitli protokol (sonuclar gorulmeden)
research\entry_v1\out\OLCUM_TEMSILI.md         olcum neyi temsil ediyor (4 bosluk)
research\entry_v1\out\DENEY_V2.md              18 yapilandirma + karar + rejim bulgusu
research\entry_v1\out\ARASTIRMA_KAYDI_V2.md    tum denemeler, iptal edilenler dahil
research\entry_v1\out\path_parity.json         uretim/replay asama karsilastirmasi
research\entry_v1\out\stale_default_filters\   GECERSIZ ilk kosular (silinmedi)
trading2-deploy\fixes-on-e1af166.patch         URETIM duzeltme yamasi
trading2-deploy\FIXES-ON-e1af166.md            yamanin icerigi + uygulama notu
trading2-deploy\SHADOW-PAPER-PACKAGE.md        golge PAPER iskeleti (aday YOK)
trading2-deploy\vps-export.ps1                 PowerShell 5.1 salt-okunur disa aktarim
trading2-deploy\tb-export-diag-v2.sh           VPS betigi (sir taramali, salt okunur)
research\entry_v1\out\KAPANIS.md               KAPANIS: gecerli/gecersiz ayrimi, pencereler, secim gerekcesi
```

Teslim dosyalarinin sha256 degerleri:

```
10b5fb4a0e6c4ae0f5b6eecd3bbc3484fadfe13f36b27110baaa959a1050fdd5  fixes-on-e1af166.patch
163ceb12933442d47fe8308a309f63bbeb944bd840612c6eeac324c92a8b442f  tb-export-diag-v2.sh
0110e4196250a2f4ddab8eaad32e80e2c77aeef1358f0254007526099a904595  vps-export.ps1
310bb7912b37e0695c1f35e984daffe96cb8ff2afc8cf45a3f5ac384e5d5a3b7  SHADOW-PAPER-PACKAGE.md
2e162d32bd96729d2f8cdff906741b05d549207f638d3790b555df70c1cfd041  PROTOCOL_V2.md
5daa89d5aafbcade643bbd772dc311ca85d8b18a60448a8e5b89f235a7c0e7e5  DENEY_V2.md
628ff1da869bfbdb7a286e4dafb7014690ccd4042d7fd6b1b4cb26ebc3a89112  OLCUM_TEMSILI.md
431e7a3e72e4b95053dc8fab66e9c1648c857b16e156b99f1abd6ecd34b585df  ARASTIRMA_KAYDI_V2.md
be8e54dbc4dfbb451d332362ed752955c00a5e070389af324b179245b0fb9ec4  KAPANIS.md
```

Eski `tb-export-diag-20260911.sh` `superseded/` altina alindi: blok yerlestirmesi hatali
ve sir taramasi yoktu.

## Açık iş

1. **VPS kayıtları alınmadı** — erişim yok. `vps-export.ps1` hazır; hash doğrulaması,
   `$LASTEXITCODE` kontrolü ve paketi geri indirme adımı içinde.
2. **Üretim düzeltme paketi uygulanmadı** — `FIXES-ON-e1af166.md` içindeki üç kontrol
   (HEAD, temiz ağaç, `apply --check`) operatörce çalıştırılmalı.
3. **Gölge PAPER paketi boş** — aday reddedildiği için kurulmadı; iskelet hazır.
4. **Sonraki turun ölçüm hedefi:** yıllık düşüşün (2021 +0,124R → 2024 −0,219R) kaynağını
   AYIRMAK. En az dört aday açıklama var ve bu veriyle ayrılmadı: rejim değişimi, hayatta
   kalma yanlılığı, kapasite/bileşiklenme çöküşü, kural kümesinin geçmişe uygulanması.
5. **Önceki turun replay sonuçları geçersiz** (varsayılan borsa kuralları). Yeniden
   koşulmadıkça alıntılanmamalı.

## V3 — giriş tetiği ve veto deneyi (2026-09-12, son)

Kod `6378c25`. Protokol `research\entry_v1\PROTOCOL_V3.md` (sonuçlar görülmeden yazıldı).
Sonuç `research\entry_v1\out\DENEY_V3.md`. Tam paket **2224 geçti, 22 atlandı, 0 başarısız**.

**Dördüncü backtest/üretim ayrışması bulundu ve onarıldı:** üretim `_trigger_fired` ile
"planlanan seviyeye gelmeden GİRME" kuralını uygular; replay bunu HİÇ yapmıyordu.
`tradingbot/entry_trigger.py` — iki motor artık aynı saf fonksiyonu çağırıyor.

| pencere | kol | n | getiri | ort R | PF | maruziyet |
|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | A0 tetiksiz | 444 | −5,41% | +0,0008 | 0,988 | %100 |
| P1 | A1 tetikli | 499 | **+17,22%** | +0,0143 | 1,036 | %99 |
| P1 | A2 tetik+veto | 77 | −13,63% | −0,1043 | 0,856 | %45 |
| P2 2022-09→2024-08 | A0 tetiksiz | 201 | −86,54% | −0,2337 | 0,584 | %96 |
| P2 | A1 tetikli | 198 | −80,12% | −0,2109 | 0,560 | %93 |
| P2 | A2 tetik+veto | 91 | −61,24% | −0,3588 | 0,431 | %54 |

**Karar:** A1 "tek pencerede olumlu, DOĞRULANMADI" (P2'de negatif) → gölge PAPER'a girmez.
A2 **REDDEDİLDİ** (iki pencerede de A1'den kötü). Eşikler değiştirilmedi.

A1 − A0 farkı iki pencerede de POZİTİF işaretli (+0,0135R, +0,0228R) ama iki aralık da
sıfırı içeriyor. A1'in P1 kazancı maliyet ×2 altında +17,22% → **+0,80%**'e iniyor.

**GERİ ÇEKİLEN İDDİA:** 2024-09→2026-09'daki bölme "sabırlı motor onayı +0,119R iyi"
diyordu; kontrollü kol tersini gösterdi (−0,119R / −0,148R). Bölme politika kararını
desteklemez. Ayrıntı: `DENEY_V3.md` §5.

## Sonraki oturum için açık iş (güncel)

1. **VPS kayıtları alınmadı** — `vps-export.ps1` hazır, SSH erişimi yok.
2. **Üretim düzeltme paketi uygulanmadı** — `FIXES-ON-e1af166.md`.
   NOT: paket `e1af166` tabanlıdır ve V2/V3 onarımlarını (ekonomi kapısı çıkarımı,
   borsa filtreleri, giriş tetiği) İÇERMEZ; onlar araştırma altyapısıdır.
3. **Gölge PAPER paketi boş** — hiçbir kol kabul eşiğini geçmedi.
4. **Ölçüm artık güvenilir.** Dört ayrışma onarıldı; bundan sonraki backtest ilk kez
   üretimle aynı sistemi ölçüyor. Yeni bir hipotez bu temelde sınanabilir.
5. **Reddedilenler (tekrar denenmemeli):** hedef mesafesi ızgarası (k), üç giriş
   hipotezi (H1/H2/H3, 18 yapılandırma), sabırlı motor vetosu (A2).
6. **Kullanıcı talepleri ve durumu:**
   * GainzAlgo V2 → kapalı kaynak, ücretli; kaynaktan uygulanamaz.
   * Fon/hisse eklemek → iki bağımsız ölçümde negatif kesit **ve** o sembollerin
     tarihsel arşivi yok, backtest edilemez.
   * 15m karar formülüne girmiyor (tek gerçek açık teknik boşluk).
   * Canlı haber: iki doğrulanabilir kaynak var, hiçbiri kapıya girmiyor (tasarım).

## V4 — mum formasyonu giriş onayı (2026-09-12, SON)

Kullanıcı klasik mum formasyonu tablosunu ve `pratikteknikanaliz.com` makalesini paylaşıp
"bu tarz mumlar lazım, kâr ettir" dedi. Protokol `PROTOCOL_V4.md` sonuç görülmeden yazıldı;
sonuç `out/DENEY_V4.md`. Kod: `6378c25` + **KAYITSIZ yama** (`out/v4_code.patch`, commit
edilmedi; wt-entry ağacı kirli).

**Ölçülen durum:** üretim `learn/candle_context.py` 12 şekli zaten tespit ediyordu, hiçbir
giriş kararına bağlı değildi. Eksik altı şekil eklendi (candle_v1.1.0: harami ×2, delici
çizgi, kara bulut, cımbız ×2); `entry_challenger_v2` kopya taraf kümesi tek kaynağa bağlandı.
Testler: yeni 10 + kural sözleşmesi 5 + ilgili paket alt kümesi 632 geçti.

| pencere | C0 temel | C1 4h şekil | C2 4h+teyit | C3 karşı-veto | C4 1d şekil |
|---|---|---|---|---|---|
| P1 getiri | +17,22% | +42,56% | +18,01% | +87,24% | +60,52% |
| P2 getiri | −80,12% | −86,61% | −85,90% | −78,69% | −68,91% |
| P1 ort R | +0,0143 | +0,0414 | +0,0246 | +0,0817 | +0,0652 |
| P2 ort R | −0,2109 | −0,2859 | −0,2924 | −0,1996 | −0,1385 |

**Karar:** dördü de "tek pencerede olumlu, DOĞRULANMADI". Gölge PAPER'a giren kol YOK.
C0 hash'i v3_a1 ile iki pencerede de aynı (parite korundu).

**Asıl bulgu (DENEY_V4 §5.1):** filtre kolları temel işlem kümesini yeniden diziyor: C1 P1'de
499 temel işlemin 126'sı tutuldu, 375 YENİ işlem serbest sermayeyle açıldı. Tutulan işlemler
elenenlerden KÖTÜ (C1 −0,068R vs +0,042R). Getiri farkı formasyonun seçiciliğinden değil, yeni
işlemlerden geldi. C4 tek tutarlı işaret (iki pencerede tutulan ≥ elenen) ama aralıklar sıfırı
içeriyor, P2 −%69.

**Reddedilenler listesine eklendi:** 4h mum formasyonu onayı (C1), teyitli formasyon (C2).
"Bir daha bak" (yalnız yeni protokol + üçüncü pencere ile): C3 karşı-şekil vetosu, C4 günlük.

Dosyalar ve sha256:

```
2def0a020a2b0adb62b38f41c97d65bdd1dee570ef99a7e690db9bb7be6de69c  PROTOCOL_V4.md
9096d7d4df24c0d1843994f226fa59e349c74c060890ca3e9e112f0db90a64fe  out/DENEY_V4.md
c50f51752a33a4cf1807cd7cd647e908990a72e4860f167eb11a74395778ce06  candle_rules.py
978a30d98b1fa27b0602eb5c30e5b629f50126a16a1de4078005e6c850c8bd65  run_v4.py
dbd7dce0b7e103568b6ddf93e4ad4c1dd62c1179428c3abcac67e10f2b37a551  make_deney_v4.py
85e2c3148e46ed69611c125f657922d67d2a3c949903bf592f35f1657b1a88d4  out/v4_code.patch
f5b1d812f21a321fba960bdcd0fce9c0648fc2e6a724ab42dbc12f062d090c9b  out/v4_sonuc.json
7334a75097393606ae1feac88320242787bad87b4bfa5692fcfaa49a98c57303  out/v4_meta.json
```

Duman koşuları `v4_smoke_c2` / `v4_smoke_c4` (2020-11→2021-01) yalnız hata taramasıdır,
sonuç olarak alıntılanmaz.

Tam paket regresyonu (6378c25 + yama): 2234 passed, 22 skipped, 25 warnings in 2017.12s (0:33:37)

## V4.1 — mum onayı UYGULANDI ve dağıtım paketi hazır (2026-09-12, SON)

Kullanıcı V4 sonuçlarını gördükten sonra "en mantıklı varyantı uygula, VPS'e aktif et" dedi.
Bu bir operatör kararıdır; DENEY_V4 §5.1 bulgusu (filtre seçicilik kanıtı değil) ayakta.

| alan | değer |
|---|---|
| commit | `0cfe3f3de51d3e37681f87972d672f9fcfdfe91a` · `work/entry-research-v1` · wt-entry TEMİZ |
| seçili varyant | `c3_4h_veto` ENFORCE: son KAPANMIŞ 4h barda adayın yönüne KARŞI şekil varsa giriş yok |
| gölge | `c1_4h`, `c2_4h_confirm`, `c4_1d` her adayda `risk.json` → `last_decisions[].candle_confirmation.shadow` |
| tek kaynak | `tradingbot/candle_confirmation.py`; canlı motor + replay + `candle_rules.py` aynı fonksiyon |
| parite kanıtı | config'i izleyen replay `v4_parity_c3_p1` hash `d03ccb9dc38fa006…` = `v4_c3_p1`; 503/503 işlem |
| huni | `decision_funnel.json` → `run.candle_blocked`; block_code `CANDLE_VETO:C3_OPPOSITE_PATTERN` |
| güvenlik | ENFORCE yalnız PAPER/TESTNET/OBSERVE/SHADOW_LIVE; LIVE'da ConfigError; değerlendirme arızası ENFORCE'ta fail-closed |
| testler | mum şekli 10 + parite/config 16 + motor döngüsü 3 = 29 yeni, hepsi geçti; tam paket: aşağıda |

**Dağıtım paketi** (`C:/Users/berke/trading2-deploy`):

```
7c964b8fbf8fba51e1e7e1472bf98bd37e020a8591588d1eb89a3a11d8fe361e  tb-0cfe3f3.bundle      (1c4cba1..work/entry-research-v1, 151 KB)
bea8cca70c8f608aaba7bab88966e7ce37b7dd0188cda21f84ca2b2f9b6c81d5  tb-deploy-0cfe3f3.sh
```

Betik tabanı `1c4cba1` YA DA `e1af166` kabul eder (VPS'te hangisi çalışıyor bu oturumda
doğrulanamadı: SSH yok); başka HEAD görürse HİÇBİR ŞEYE DOKUNMADAN durur ve HEAD'i yazar.
Bundle 1c4cba1'den itibaren her şeyi taşır: on coin evreni (e1af166), dört parite onarımı,
p_win fail-open kapanışı, sıfır fill reddi, mum onayı. **6247675 (funding V2 dalı) bu
pakette YOKTUR**; VPS o daldaysa ff-only başarısız olur ve betik durur.

VPS'te sırayla (kullanıcı çalıştırır):

```
scp tb-0cfe3f3.bundle tb-deploy-0cfe3f3.sh ubuntu@<VPS>:~/
ssh ubuntu@<VPS> 'sha256sum ~/tb-0cfe3f3.bundle ~/tb-deploy-0cfe3f3.sh'
ssh -t ubuntu@<VPS> 'sudo bash ~/tb-deploy-0cfe3f3.sh'   # betik v2: ROOT ile, git işlemleri tradingbot adına; son satır DEPLOY_OK
```

15-30 dk sonra (betiğin 10. bölümündeki komutlar): journal'da `MUM ONAYI: mode=ENFORCE
variant=c3_4h_veto`; `decision_funnel.json` içinde `candle_blocked`; `risk.json` içinde
`candle_confirmation` kayıtları. Geri alma: `bash /opt/tradingbot/app/deploy/rollback.sh`.

Tam paket regresyonu: 2249 passed, 22 skipped, 1 failed (34 dk). Tek düşen test, paket başladıktan SONRA düzeltilen bir string iddiasıydı (`test_validate_v3_uses_the_shared_validator`); commit edilen sürümde aynı dosya tek başına: 16 passed. Motor döngüsü testi ayrıca 3/3.

NOT (2026-09-12 15:18Z): ilk betik `ubuntu` ile çalıştırılınca yazma yetkisi kontrolünde DURDU (uygulama `tradingbot` kullanıcısına ait); hiçbir şeye dokunmadı. Betik v2 root ile çalışır, depo işlemlerini `sudo -u tradingbot` ile yapar, bundle değişmedi.

## DAĞITILDI — 2026-09-12 15:23Z, VPS `0cfe3f3` (SON)

Kullanıcı çalıştırdı: `ssh -t ... "sudo bash ~/tb-deploy-0cfe3f3.sh"` → `DEPLOY_OK`.

| alan | değer |
|---|---|
| VPS önce | `e1af166` · dal adı `feature/quant-evaluation-v1` (yanıltıcı; SHA belirleyici) |
| VPS sonra | `0cfe3f3de51d3e37681f87972d672f9fcfdfe91a` · ff-only, 25 dosya |
| yedek | `/opt/tradingbot/data/backups/manual/tradingbot-manual-20260912T152326Z.tar.gz` · sha256 `28331158…` · doğrulandı |
| defter | 41 kapanış, 3 açık, seq 44 — ÖNCE ve SONRA aynı |
| değişmezler | 12/12 OK; config sözleşmesi 23/23 OK; doctor 0 hata; pip atlandı (requirements aynı) |
| sağlık | `/health/live` 200 (4 sn), NRestarts 0 → 0 |
| açık kalan | `MUM ONAYI: mode=ENFORCE variant=c3_4h_veto` başlangıç logu betik içinde (20 sn) görülmedi; ilk turda doğrulanacak |
| geri alma | `sudo -u tradingbot git -C /opt/tradingbot/app checkout -q e1af166 && sudo systemctl restart tradingbot-worker tradingbot-dashboard` |
| log | `/opt/tradingbot/rollback/deploy-0cfe3f3…-20260912T152325Z/deploy.log` |

Sonraki oturumun ilk işi: journal'da `MUM ONAYI` satırı, `decision_funnel.json` içinde
`candle_blocked`, `risk.json` içinde `candle_confirmation` kayıtları. Doctor çıktısındaki
`vault_writable C:/Users/berke/...` satırı ESKİ bir config tuhaflığıdır, bu turla ilgisi yok.

## V5 — grafik formasyonları ÖLÇÜLDÜ ve REDDEDİLDİ; SHADOW olarak paketlendi (2026-09-12, SON)

Kullanıcı ikinci görseli (çift dip, OBO, üçlü tepe, üçgen, bayrak, Wolfe) ve stil tablosunu
paylaşıp "gerçekten ekleyelim, kâr etmek istiyorum" dedi. Protokol `PROTOCOL_V5.md` sonuç
görülmeden yazıldı; sonuç `out/DENEY_V5.md`; kod `5fc4507` (`work/entry-research-v1`, TEMİZ).

| alan | değer |
|---|---|
| dedektör | `tradingbot/chart_patterns.py` chart_v1.0.0: 10 formasyon, KIRILIŞLA teyitli, yön kırılıştan; Wolfe yok |
| onay modülü | `tradingbot/chart_confirmation.py`: OFF/SHADOW/ENFORCE, p1_4h_confirm / p2_4h_veto / p3_1d_confirm / p4_1d_veto |
| temel B0 | üretim = tetik + C3 mum vetosu (`v4_c3_*`) |
| sonuç | **dördü de REDDEDİLDİ.** P1 (taze kırılış şart) iki pencerede ÖLÇÜLEBİLİR ZARARLI: −0,18R [−0,32, −0,04], −0,22R [−0,41, −0,02]. P2 vetosu temelin en iyi 132 işlemini (+0,22R) eledi |
| sayım | 4h karar noktalarının ~%30'unda son 3 barda teyitli kırılış var; dedektör sık ateşliyor, ızgara yok |
| uygulama | config.yaml `chart_confirmation_mode: SHADOW`, variant p2_4h_veto; her adayın kaydında 4 hüküm + taze formasyon listesi; KARAR DEĞİŞMEZ |
| testler | dedektör 14 + parite/config 8 + motor döngüsü 3 = 25 yeni; hedefli alt küme 656 geçti; tam paket: aşağıda |

**Dağıtım paketi** (taban `0cfe3f3`, VPS'te doğrulanmış; ROOT betiği, depo işlemleri tradingbot adına):

```
17601774f44c99e102b9edecf8f4f04bb2fc5ba54fca35da0c4ff0878a757343  tb-5fc4507.bundle
b579c0d90882d953434a6128386585517f23624763bd69ec21a5a58f01311167  tb-deploy-5fc4507.sh
```

```
scp -i ~/.ssh/trading2_ovh tb-5fc4507.bundle tb-deploy-5fc4507.sh ubuntu@<VPS_HOST>:~/
ssh -t -i ~/.ssh/trading2_ovh ubuntu@<VPS_HOST> "sudo bash ~/tb-deploy-5fc4507.sh"
```

Bu dağıtım karar yolunu DEĞİŞTİRMEZ (grafik SHADOW, mum ENFORCE aynen). Amaç canlı kanıt
toplamak. ENFORCE'a çevirmek tek satır ama DENEY_V5 karşısında ÖNERİLMEZ.

**Reddedilenler listesine eklendi:** grafik formasyonu kırılış şartı (P1/P3), karşı-kırılış
vetosu (P2/P4), 4h ve 1d. Stil tablosu: bot zaten swing/algoritmik; scalping/gün içi arşiv yok
ve maliyet yüzünden yanlış yön; günlük stil V4-C4 ve V5-P3/P4 ile iki kez denendi, doğrulanmadı.

Tam paket regresyonu (5fc4507): 2278 passed, 22 skipped, 25 warnings in 2131.39s (0:35:31)

## DAĞITILDI — 2026-09-12 20:17Z, VPS `5fc4507` (SON)

`sudo bash ~/tb-deploy-5fc4507.sh` → `DEPLOY_OK`. `0cfe3f3 → 5fc4507` ff-only, 9 dosya.
Yedek `tradingbot-manual-20260912T201713Z.tar.gz` (sha256 `4a8f277a…`, doğrulandı). Defter 41/3/44
ÖNCE ve SONRA aynı. Değişmezler 11/11, config sözleşmesi 24/24, doctor 0 hata, pip atlandı,
`/health/live` 200 (4 sn), NRestarts 0→0. `.last_good_commit = 0cfe3f3`.

VPS'te şu an: mum vetosu ENFORCE (C3), grafik formasyonları SHADOW (p2_4h_veto seçili, dört
hüküm kayıtta). Sonraki oturumun ilk işi: journal'da `MUM ONAYI: mode=ENFORCE` ve
`GRAFIK ONAYI: mode=SHADOW` satırları; `decision_funnel.json` içinde `candle_blocked` /
`chart_blocked`; `risk.json` içinde `candle_confirmation` ve `chart_confirmation` kayıtları.

**DOĞRULANDI 2026-09-12 20:19Z:** worker başlangıç logu `MUM ONAYI: mode=ENFORCE variant=c3_4h_veto
policy=candle_v1.1.0` ve `GRAFIK ONAYI: mode=SHADOW variant=p2_4h_veto policy=chart_v1.0.0`; traceback/error
yok. Açık kalan tek gözlem: ilk turlarda `decision_funnel.json` → `candle_blocked` / `chart_blocked` ve
`risk.json` içindeki `candle_confirmation` / `chart_confirmation` kayıtları (henüz okunmadı).

## V6 — seans ve hafta sonu kapısı REDDEDİLDİ (2026-09-13, SON)

Kullanıcı "borsa kapandı, 9'da açılıyor, bot kaale almalı" dedi. Kripto vadeli kapanmaz; hipotez
"ABD seansı (12-20 UTC) ve hafta sonu" olarak kuruldu. Hipotez P1/P2 bölmesinden doğduğu için o
pencereler KİRLİ sayıldı, temiz P3 = 2024-09-01→2026-08-31 eklendi. Protokol `PROTOCOL_V6.md`,
sonuç `out/DENEY_V6.md`, 10 koşu.

| kol | P1 (kirli) | P2 (kirli) | P3 (temiz) | karar |
|---|---|---|---|---|
| B0 üretim | +87,2% | −78,7% | **−57,8% (200 işlem)** | |
| S1 yalnız 12-20 UTC | +9,5% | −78,5% | −39,2% | REDDEDİLDİ |
| S2 hafta sonu yok | +56,2% | −86,5% | −41,3% | REDDEDİLDİ |
| S3 ikisi | −19,8% | −86,1% | −36,6% | REDDEDİLDİ |

Bölme kendini doğrulamadı (S1 P1'de −0,08R). P3'te kollar temeli geçiyor ama eksi ve tutulan
işlemler elenenlerden kötü; fark yeni işlemlerden. **Yeni bilgi:** şu anki üretim yolu temiz
P3'te de kaybediyor (−0,19R/işlem, isabet %28,5). Üretime hiçbir şey eklenmedi;
`session_gate.py` araştırma paketinde (`research/entry_v1/`), wt-entry `5fc4507` temiz.

**Reddedilenler (kümülatif, tekrar denenmez):** hedef mesafesi ızgarası; H1/H2/H3 (18 yapılandırma);
sabırlı motor vetosu; mum onayı c1/c2/c4 (c3 kullanıcı kararıyla canlı); grafik formasyonu p1–p4;
seans s1–s3. **Sonraki tur:** giriş süzgeci değil, avantajın 2021→2024 erimesinin kaynağını ayırmak.

## V7 — piyasa rejimi kapısı, basit temel, hayatta kalma (2026-09-13, SON)

Kullanıcı "kâr için ne lazımsa yap" dedi. Bu tur gösterge değil, ölçülmüş mekanizma sınandı.
Protokol `PROTOCOL_V7.md`, sonuç `out/DENEY_V7.md`, kod `5f9d652` (`work/entry-research-v1`, TEMİZ).

**Q1 rejim kapısı (BTC günlük close > EMA200 → UP), temel = üretim, 3 pencere, 9 koşu:**

| kol | P1 | P2 | P3 | fark/işlem (P1, P2, P3) | karar |
|---|---|---|---|---|---|
| B0 üretim | +87,2% | −78,7% | −57,8% | | |
| **R1 yalnız LONG + yalnız UP** | **+140,8%** | **−28,4%** | **−39,8%** | +0,19R [+0,05,+0,34] · +0,15R · +0,09R | doğrulanmadı (P2/P3 negatif) ama temeli HER pencerede HER ölçütte geçiyor; şart 5 her yerde ✓ |
| R2 DOWN'da giriş yok | +117,6% | −75,8% | −23,4% | +0,12 · −0,05 · +0,10 | doğrulanmadı |
| R3 rejimi izle | +132,3% | −72,2% | −50,9% | +0,07 · +0,04 · +0,07 | REDDEDİLDİ |

BTC UP gün payı: P1 %51, P2 %71, P3 %53. R1 UP rejiminde yalnız LONG'la bile 2023-24'te −28%:
sorun rejimden ibaret değil. R1 = ölçülmüş KAYIP AZALTICI, kârlı kural değil. Operatör kararıyla
üretime bağlandı: `regime_gate.py` (tek kaynak), OFF/SHADOW/ENFORCE, config.yaml **ENFORCE r1**.
Motor kancası 2d (mum ve grafikten sonra), huni `regime_blocked`, `REGIME_VETO:<sebep>`, BTC karesi
yoksa fail-closed. Testler 4+9+3 = 16 yeni; ilgili 40 test geçti. Parite replay'i (`v7_parity_r1_p1`,
hash `v7_r1_p1` ile aynı olmalı) ve tam paket bu satır yazılırken KOŞUYOR.

**Q2 basit temel (`benchmarks_v7.py`, on coin eşit ağırlık, maliyet dahil, kaldıraç/stop yok):**
al-tut +592% / +94% / +11%; EMA200 trend +219% / +27% / +24%; bot +87% / −79% / −58%.
Tek kurallı trend üç pencerede de pozitif ve botu her pencerede geçiyor (evren 2026 seçimi → şişkin).

**Q3 hayatta kalma:** yeni veri toplanamadı (fapi.binance.com ve data.binance.vision bu ağdan
bağlantı sıfırlıyor). VPS betiği hazır: `vps-collect-2020-universe.sh` (ubuntu, ayrı dizin,
archive-first, üretime dokunmaz) → `~/tb-2020-universe.tar.gz`. Kısmi kontrol: 2021 kârının 2/3'ü
2020'de zaten büyük altı coinden; hayatta kalma ana açıklama değil. `run_rule.py --symbols` eklendi.

**Sonraki adım (tam paket + parite geçince):** `gen_deploy_v7.py` ile `tb-<sha>.bundle` + `tb-deploy-<sha>.sh`
(taban 5fc4507), kullanıcı `sudo bash` ile çalıştırır. Açık SHORT pozisyonlar kapatılmaz; kapı yalnız
yeni girişleri etkiler.

Tam paket regresyonu (5f9d652): 2294 passed, 22 skipped, 25 warnings in 2220.00s (0:36:59) · PARITE_V7: `v7_parity_r1_p1` hash `c4b151fd631d7bb5…` = `v7_r1_p1`, 261/261 işlem (üretim kancası = araştırma kuralı). Dağıtım paketi: `tb-5f9d652.bundle` sha256 `ca467f09d8fba6c7322a11d72ff62d0722963a75744b8269d9a987e387d84e2d`, `tb-deploy-5f9d652.sh` sha256 `582699c111cebfef922eda2ac9a593aa8c204373cceb6cce2f0d3d5ab4c4f512` (taban 5fc4507, root betiği).

## V8 — mum formasyonu GİRİŞ SİNYALİ ve ÖĞRENEN giriş; candle_v1.2.0 (2026-09-13, SON)

Kullanıcı Türkçe mum tablosunu paylaşıp "hepsini dahil edelim, görünce girelim; öğrensin de" dedi.

* **candle_v1.2.0 (`0796c91`):** belden tutma, kros hamile, güvercin yuvası, inen şahin, değen mumlar,
  tepen (kicker), doji yıldız, doji sabah/akşam yıldızı, terk edilmiş bebek, üç yıldız eklendi. Canlı
  vetonun taraf kümesi DEĞİŞMEDİ; geniş kümeler `*_SIDE_SHAPES_EXT` yalnız araştırma. Testler: yeni 8 +
  ilgili alt küme 229 + 46 geçti. Tam paket 5f9d652'de koşuldu (2294 geçti); 0796c91 farkı yalnız
  candle_context + testler, hedefli alt kümeyle kapatıldı.
* **V8 (`benchmarks_v8.py`, referans simülatör; PROTOCOL_V8):** "formasyonu görünce gir" 4 kol × 3 pencere:
  hiçbiri üç pencerede pozitif değil (en iyi S-C +38/−22/+7). KAPANDI. Yan bulgu: naif formasyon girişi
  2022 sonrası botun çok üstünde (−5/−2 vs −79/−58): bot yığını negatif seçicilik üretiyor (ikinci kanıt).
* **V8 §6 öğrenen kol (`benchmarks_v8_learn.py`):** sanal işlemlerden formasyon başına net R öğrenir,
  yalnız alt %95 sınırı > 0 olan etiketlere girer. S-E1 (ort>0) KAPANDI; **S-E2 üç pencerede pozitif:
  +9,2% / +0,09% / +4,4%, PF 1,04 / 1,00 / 1,03 → BAŞA BAŞ**, kâr değil. Etiket ortalamaları ±0,05R.
  Protokol gereği "ilgi çekici"; replay'de yeniden kurmak sonraki turun işi, önceliği düşük.

**Dağıtım paketi (rejim kapısı ENFORCE r1 + candle_v1.2.0), taban 5fc4507, root betiği:**

```
ffc961d7e28ac22ceb0bcffd8817fb43d941c0389eb1c6af6c21561d356f7c80  tb-0796c91.bundle
118d37018afe6958f12f6c14332dfcff6ea9eea951988dcecf64b74a85afa8f6  tb-deploy-0796c91.sh
```

Önceki 5f9d652 paketi silindi (yerine 0796c91). Parite kanıtı 5f9d652'de: `v7_parity_r1_p1` = `v7_r1_p1`.

**Kümülatif reddedilenler:** hedef ızgarası; H1/H2/H3; sabırlı veto; mum onayı c1/c2/c4; grafik p1–p4;
seans s1–s3; rejim r2/r3; formasyon-sinyal S-A/B/C/D ve S-E1. **Canlıda:** mum vetosu c3 (ENFORCE),
grafik (SHADOW), rejim r1 (ENFORCE, dağıtım bekliyor). **Başa baş:** S-E2. **Botu geçen referanslar:**
al-tut, EMA200 trend (üç pencerede pozitif). **Sonraki tur:** sadeleştirme — trend temelini üretim
defteri/maliyet modeliyle replay'de kurup botla aynı sistemde karşılaştırmak.

## DAĞITILDI — 2026-09-13 11:03Z, VPS `0796c91` (SON)

`sudo bash ~/tb-deploy-0796c91.sh` → `DEPLOY_OK`. `5fc4507 → 0796c91` ff-only, 12 dosya. Yedek
`tradingbot-manual-20260913T110323Z.tar.gz` (sha256 `1ad1af09…`, doğrulandı). Defter 41/3/44 ÖNCE ve
SONRA aynı. Değişmezler 14/14, config sözleşmesi 25/25, doctor 0 hata, pip atlandı, `/health/live` 200
(5 sn), NRestarts 0→0. `.last_good_commit = 5fc4507`.

VPS'te şu an: **rejim kapısı ENFORCE r1** (yalnız LONG, yalnız BTC 1d close > EMA200; her 15 dk turunda
yeniden değerlendirilir), mum vetosu ENFORCE (c3), grafik formasyonları SHADOW, candle_v1.2.0 tam katalog.
Kullanıcı kararı: birinci seçenek (ölçümün dediği). Endişe "piyasa dönerse": kapı dinamik, açık pozisyonu
stop yönetir; "rejim dönünce açık LONG'u kapat" kuralı ÖLÇÜLMEDİ (sonraki tur adayı).

Sonraki oturumun ilk işi: journal'da `REJIM KAPISI: mode=ENFORCE variant=r1_long_only_uptrend`;
`decision_funnel.json` içinde `regime_blocked`; `risk.json` içinde `regime_gate.regime` (UP/DOWN).

## V9 — SADELEŞTİRME: tek kurallı trend botun kendi defterinde (2026-09-13, SON)

Replay'e **strateji modu** eklendi (`HistoricalReplay(strategy=...)`, commit `0ab33af`, VPS'e gerek yok,
araştırma altyapısı): uzman yığını/baş yönetici/kapılar atlanır; açılış aynı risk motoru + boyut
(risk/stop) + filtre + kayma, kapanış `close_manual`, stop/funding/likidasyon/başa-baş `ledger2.tick`,
`_on_closed` ortak kayıt. Koşu 6–10 dk. `run_rule.py --strategy <ad> [--no-breakeven]`,
`strategy_rules.py` (T1/T2/T3), `run_v9.py`, `make_deney_v9.py`. Protokol `PROTOCOL_V9.md`, sonuç `out/DENEY_V9.md`.

| kol | P1 | P2 | P3 | maksDD en kötü | 3 pencere bileşik |
|---|---|---|---|---|---|
| R1 şu anki üretim | +140,8% | −28,4% | −39,8% | %55 | ~+4% |
| T1 saf EMA200 trend | +96,7% | +16,2% | +2,6% | %33 | ~+135% |
| **T2 trend + BTC rejimi** | **+107,3%** | **+33,7%** | **+10,2%** | **%22** | **~+205%** |
| T3 trend + botun çıkışı | +107,9% | −11,1% | +38,7% | %47 | ~+156% |

Harfiyen: "bazı pencerelerde olumlu, DOĞRULANMADI" (tek düşen şart: P1'de R1'i geçmek). Diğer şartlar
üç pencerede sağlandı. Kâra en yakın bulgu. Sınırlar: az işlem (21–76), geniş aralıklar, evren 2026,
100 USDT'de BTC açılamıyor, TOTAL_OPEN_RISK çoğu sinyali reddediyor. T3: botun başa-baş koruması trendi
erken kesiyor.

**Sonraki adım (ayrı karar):** T2'yi VPS'te PAPER modda AYRI defterle canlı ileri test → üretim motoruna
strateji modu kancası (replay ile aynı), parite hash'i, ayrı state dizini, dashboard satırı. Gerçek para YOK.

**DOĞRULANDI 2026-09-13 ~11:30Z (`tb-check.sh`):** başlangıç satırları `MUM ONAYI ENFORCE candle_v1.2.0`,
`GRAFIK ONAYI SHADOW`, `REJIM KAPISI ENFORCE r1`; hata yok. İlk tur hunisi: 7 aday, 7 tetik, 2 mum vetosu,
**3 rejim vetosu (AVAX/DOGE/XRP SHORT → R1_SHORT_BLOCKED)**, 1 negatif beklenti, 0 açılış; rejim UP.
Mum vetosu yiyen adaylarda `rejim=None` beklenen (sıra: mum → grafik → rejim). Kayan 24 saat: 559 aday,
147 mum vetosu, 261 negatif beklenti, 0 açılış. Defter: SOL/ZEN/TRX LONG açık, 41 kapanış. Canlı kontrol
betiği `trading2-deploy/tb-check.sh` (salt okunur; scp + `sudo bash ~/tb-check.sh`).

## V10 — TREND STRATEJİSİ KÂĞIT İLERİ TEST: üretim motoruna bağlandı (2026-09-13, DEVAM EDİYOR)

Kullanıcı kararı: T2'yi VPS'te PAPER modda AYRI defterle canlı ileri teste almak. Yapılanlar (wt-entry, 0796c91 üzerine KAYITSIZ):

* `tradingbot/ema200_trend.py` — kural TEK KAYNAK (`decide`, `daily_rows_from_frame`, `read_daily`); replay
  `research/entry_v1/strategy_rules.py` ona delege eder. (`tradingbot/strategies/` paketi AÇILAMAZ: mevcut
  `strategies.py` modülüyle çakışıyor — ilk denemede bu hata alındı ve tek modüle taşındı.)
* `tradingbot/strategy_paper.py` — `apply_action` (boyut = işlem riski / stop, `risk.evaluate`, `ledger.open`,
  `close_manual`; iki motor da bunu çağırır), `StrategyBook` (ayrı defter `state/strategy_paper/futures_ledger.json`,
  ayrı `trade_memory.jsonl` kaynak `STRATEGY_PAPER`, özet `state/strategy_paper.json`), `validate_settings`
  (LIVE'da enabled reddi).
* `replay/engine.py` `_strategy_step` → `apply_action`'a delege (tek kaynak); `learn/memory.py` SOURCES +STRATEGY_PAPER.
* `config_v3.py` `StrategyPaperSection` (enabled, name, starting_equity_usdt=100, atr_mult=3, breakeven_at_mfe_r=0,
  state_dir, symbols); `config.yaml` `strategy_paper.enabled: true, name: t2_trend_regime`.
* `engine_v3.py`: init'te `self.strategy_book`; turda ana defter kaydından SONRA `_strategy_paper_tour` (kural → defter →
  tick aynı marks/funding/bar_advance → özet); `exit_check` başında `_strategy_paper_exit_check` (canlı fiyatla tick).
  `run_id` turda atanır (init'te yok — ilk hata buydu).
* Dashboard: `state.py` STATE_FILES `strategy_paper`; overview'da "Trend stratejisi — kâğıt ileri test" kartları;
  `/portfolio/strategy` sayfası.
* Testler: `tests/test_strategy_paper_v1.py` (kural birim + tek kaynak AST + config, 8), `tests/test_strategy_paper_engine_v1.py`
  (gerçek tur: UP'ta LONG açar, EMA altına inince kapatır, DOWN'da düz, kapalıyken dosya yok; 3),
  `tests/test_replay_strategy_mode_v1.py` (3). 14/14 geçti. Fixture notu: sentetik 1d ve 4h kareleri bağımsız
  üretildiğinden 1d kapanışı 4h fiyatına ölçeklendi ve ATR %3 alındı (%2'de tek coin %30 tavanına takılıyor).
* Bilinen davranış: stop (close − 3×ATR) giriş fiyatının üstündeyse `STRATEGY_BAD_STOP` (giriş yok, fail-closed);
  ATR küçükse `MAX_POSITION_PCT` reddi (replay'de de aynıydı, parite korunuyor).

Commit `a619fb0` (`work/entry-research-v1`, wt-entry TEMİZ). Dağıtım paketi (taban 0796c91, ROOT betiği):

```
2166b01b15339f581b325b30fa678b1db0874517f17c7e9996e0c46cb316bd64  tb-a619fb0.bundle
1f99a686ef94a9cedfb04da79f03e5cca3e69551272835c680729537ad1a7fad  tb-deploy-a619fb0.sh
```

Kullanıcı: scp + `sudo bash ~/tb-deploy-a619fb0.sh` → `DEPLOY_OK`; ilk turda journal `STRATEJI KAGIT DEFTERI: name=t2_trend_regime`
ve `/opt/tradingbot/data/state/strategy_paper.json` (regime, counters, positions). Dashboard: ana sayfa kartları + `/portfolio/strategy`.
YENI-PENCERE-PROMPT.md güncel.

PARITE_V10: `v10_parity_t2_p1` hash `d5f25da58ecca6ca…` = `v9_t2_p1` (21/21 işlem) — ortak kural + ortak uygulayıcı yeniden düzenlemesi davranışı KORUDU. · Tam paket regresyonu (a619fb0 öncesi ağaç, dashboard testi hariç; o ayrıca 3/3): 2316 passed, 22 skipped, 25 warnings in 744.58s (0:12:24)

## DAĞITILDI — 2026-09-13 18:33Z, VPS `a619fb0` (SON)

`sudo bash ~/tb-deploy-a619fb0.sh` → `DEPLOY_OK`. `0796c91 → a619fb0` ff-only, 13 dosya. Yedek
`tradingbot-manual-20260913T183304Z.tar.gz` (sha256 `58495e79…`, doğrulandı). Ana defter 41/3/44 ÖNCE ve
SONRA aynı. Değişmezler 13/13, config 24/24, doctor 0 hata, `/health/live` 200 (4 sn), `/portfolio/strategy`
200, NRestarts 0→0. `.last_good_commit = 0796c91`. Strateji defteri dizini ilk turda 100 USDT ile açılacak.

VPS'te şu an iki kâğıt defter yan yana: (1) ana bot — 20 uzman, tetik, mum vetosu ENFORCE, grafik SHADOW,
rejim kapısı ENFORCE r1; (2) trend defteri — t2_trend_regime, 100 USDT, ATR 3, başa-baş kapalı.
Sonraki oturumun ilk işi: `tb-check.sh` (genişletildi) ile journal `STRATEJI KAGIT DEFTERI: name=t2_trend_regime`,
`strategy_paper.json` (regime, counters, positions, rejections), dashboard kartları.

Kullanıcı beklentisi notu: "haftada 7-9 işlem" istiyor; ana bot zaten o hızda ve ölçülmüş sonucu eksi. Kararı
iki defterin canlı karşılaştırması verecek (aylar). Aday sonraki tur: 4h barda aynı trend kuralı (ön kayıtlı).

## V11 — momentum tanımı alternatifleri (2026-09-13, SON)

Protokol `PROTOCOL_V11.md` (önce yazıldı), sonuç `out/DENEY_V11.md`, kurallar `momentum_rules.py` (yalnız araştırma),
9 koşu strateji modunda (`run_v11.py`, `--no-breakeven`). Temel T2 = `v9_t2_*` (VPS'teki canlı kâğıt defter).

| kol | P1 | P2 | P3 | en kötü DD | karar |
|---|---|---|---|---|---|
| T2 EMA200 (canlı) | +107% | +34% | +10% | %22,3 | temel |
| M1 EMA100 | +141% | +49% | +14% | %20,6 | 5/5 şart 3 pencerede: ADAY |
| **M2 TSMOM 28 gün** | **+158%** | **+46%** | **+35%** | **%16,3** | 5/5 şart 3 pencerede: ADAY (P1 ort R %95 [+0,47, +3,32] sıfırı dışlıyor) |
| M3 TSMOM 91 gün | +160% | +43% | −1% | %26,2 | REDDEDİLDİ |

Protokol boşluğu: iki kol geçince seçim kuralı yazılmamıştı; sonuca bakıp seçmek 3-aday seçim yanlılığı taşır
ve pencereler T2'nin seçildiği pencereler. M2 "daha güçlü aday", kesin üstünlük değil. Operatör kararı bekliyor:
(a) canlı kâğıt defterde T2→M2 (config + `ema200_trend.py`'ye `m2_tsmom28` varyantı + testler + parite + deploy),
(b) M2'yi ikinci defter olarak eklemek (çoklu defter kodu). Üretime henüz HİÇBİR ŞEY girmedi.

## V12 — ÇOKLU KÂĞIT DEFTER + M2 ikinci defter (2026-09-13, DEVAM EDİYOR)

Kullanıcı "en iyi seçenek" diye sordu; öneri: T2 kalsın, M2 ikinci kâğıt defter olarak yanına eklensin
(iki aday aynı canlı veride, seçim yanlılığı canlı sınanır, altyapı kalıcı). Kullanıcı kabul etti.

* `strategy_paper.extra` (config listesi): her ek defter kendi dizini/belleği/özeti ile aynı `StrategyBook`
  yolundan; `strategy_paper_index.json` canlı defterleri listeler; dashboard her defter için kart + sayfa.
  Doğrulama: `state_dir` benzersiz ve düz, ad bilinen, LIVE'da red. Bir defterin arızası diğerini etkilemez.
* `ema200_trend.py`: `m2_tsmom28` varyantı (close > close[-28]; rejim kapısı + 3×ATR aynı; çıkış
  `M2_TSMOM28_CROSS_DOWN`). `strategy_rules.py` m2'yi üretim modülüne delege eder.
* config.yaml: extra → `m2_tsmom28`, `strategy_paper_m2`, 100 USDT, ATR 3, başa-baş kapalı.
* Testler: 5 yeni (kural, config extra, iki defter motor turu, dashboard); strateji seti 25/25.
* Ders: patch betiğine kabuk heredoc'u ile `
` yazmak bozuluyor (tool katmanı kaçışı düzleştiriyor);
  Edit aracıyla düzeltildi. `book_specs(cfg)` V3Config alır (`.v3` değil).

Commit `f25cb39` (wt-entry TEMİZ). Dağıtım paketi (taban a619fb0, ROOT betiği, 28 değişmez):

```
dcdedd330ff5000b4e93d4407d2d81ca805ec9462f043671ac09c704af81e23c  tb-f25cb39.bundle
6fa4343abbf621c04623b9b5b36de46fa72524fa96e4a45c8d803a0f9d1e3373  tb-deploy-f25cb39.sh
```

PARITE_V12: `v12_parity_m2_p1` hash `470b9e6c9ab29733…` = `v11_m2_p1` (47/47 işlem) — üretim modülü = araştırma kuralı DOĞRULANDI · Tam paket regresyonu (f25cb39 ağacı): 2324 passed, 22 skipped, 25 warnings in 729.41s (0:12:09)

**Deploy denemesi 1 (2026-09-13 19:51Z) DURDU, 5. adım:** `canli motor coklu defter + indeks` kontrolü
indeks dosya adını `engine_v3` kaynağında arıyordu; ad `strategy_paper.INDEX_FILE` içinde (kod doğru, kontrol
yanlış). VPS'te kod `f25cb39`'a ilerlemiş, servis YENİDEN BAŞLAMAMIŞ (eski süreç a619fb0 ile çalışıyor);
`.last_good_commit = a619fb0`, yedek `tradingbot-manual-20260913T195050Z`. Betik v2 (sha256 `97255160…`):
kontrol düzeltildi; HEAD zaten hedefteyse fetch/merge atlanır, değişmezler + config + restart yapılır;
geri alma hedefi `.last_good_commit`. Kullanıcı yalnız betiği yeniden kopyalayıp çalıştırır (bundle aynı).
Ders: deploy değişmezlerini yazarken ismin hangi modülde olduğunu KAYNAKTAN doğrula.

## DAĞITILDI — 2026-09-13 19:55Z, VPS `f25cb39` (SON)

Betik v2 → `DEPLOY_OK`. HEAD zaten hedefteydi (fetch/merge atlandı), değişmezler 17/17, config 25/25, doctor 0
hata, `/health/live` 200 (4 sn), `/portfolio/strategy` 200, NRestarts 0→0. Ana defter 41/3/44 aynı. Yedek
`tradingbot-manual-20260913T195538Z.tar.gz`. `.last_good_commit = a619fb0`. M2 defteri ilk turda 100 USDT ile açılır.

VPS'te şu an ÜÇ bakiye eğrisi: (1) ana bot (20 uzman + kapılar), (2) T2 EMA200 trend defteri, (3) M2 28 günlük
momentum defteri. Sonraki oturumun ilk işi: `tb-check.sh` ile journal `STRATEJI KAGIT DEFTERI: name=m2_tsmom28`,
`strategy_paper_m2.json`, `strategy_paper_index.json` (iki defter), dashboard iki kart çifti.

## V13 — f25cb39 ilk tur kontrolü ve onarım (2026-09-14)

`tb-check.sh` (19:56Z restart + 7 tur): iki defter ayakta. T2: SOL/BNB/**ZEN** LONG, özkaynak 99.46; M2: SOL/BNB/XRP
LONG, özkaynak 99.53 (BTC STEP_ZERO_QTY, ETH MIN_NOTIONAL: 100 USDT 1x'te beklenen; TOTAL_OPEN_RISK 3×%2=%6 tavan,
replay ile aynı). Ana bot: 24 saatte 549 aday, 0 açılış (kapı sırası onarımından beri bilinen durum).

**Kusur 1 — evren kayması:** T2 ZEN/USDT'de açmıştı. `_strategy_paper_tour` defterlere ana turun listesini veriyordu
(= evren ∪ ana defterin açık pozisyonları). Ölçüm on coinde, ileri test başka evrende. **Kusur 2:** restart'ta
sayaçlar sıfırlanıyordu ("opened 0", 3 açık).

**Onarım commit `3501304`** (strategy_paper.py, engine_v3.py, tests/test_strategy_paper_universe_v1.py):
defter yeni pozisyonu yalnız `entry_universe.symbols` içinde açar; evren dışı açık pozisyon yönetilmeye devam
eder (kural kapanışı + stop); ana tur kapsamı defterlerin açık pozisyonlarını içerir (`_strategy_open_symbols`);
`_restore_counters` (opened/closed defterden, rejected/tours özetten, bozuk özet → defter gerçeği).
Hedefli: 4 yeni + 65 ilgili test geçti. Replay/ema200_trend dokunulmadı → parite hash'leri geçerli.
Paket: `tb-3501304.bundle` (sha256 d268fc2d…) + `tb-deploy-3501304.sh` (sha256 1c558619…, 21 değişmez + 25
config; **yerelde çalıştırıldı, 21/21 + 25/25 OK**), taban f25cb39, yarım kalırsa yeniden çalıştırılabilir.
Tam paket: `scratchpad/full_v13.log` — sonuç aşağıda.

**TAM_PAKET_V13 (commit 3501304):** `2328 passed, 22 skipped, 25 warnings in 697.91s (0:11:37)`. Yeni 4 test f25cb39'da 4/4 DUSER, 3501304'te gecer. Deploy komutlari kullaniciya verildi; DEPLOY_OK bekleniyor.

## DAĞITILDI — 2026-09-13 22:57Z, VPS `3501304` (SON)

`DEPLOY_OK`; ff f25cb39→3501304 (3 dosya), değişmezler 21/21, config 25/25, doctor 0 hata, health 200 (4 sn),
NRestarts 0→0, ana defter 42 kapanış / 2 açık (ZEN'i ana bot deploy öncesi kapatmıştı; T2 hâlâ ZEN taşıyor →
V13 kapsam onarımı tam zamanında). `.last_good_commit = f25cb39`, yedek `tradingbot-manual-20260913T225711Z`.
Kullanıcı tb-check'i restart'tan hemen sonra çalıştırdı: özetler ESKİ sürecin (tours=9, T2 opened=0). Sonraki
oturumun ilk işi: tb-check → T2 counters.opened=3 & tours≥10, M2 opened=3, ZEN `son=` yenilenmiş, evren dışı yeni
giriş yok. Sonra bekleme: üç eğri (ana bot, T2, M2) haftalık okunur; gerçek para hiçbirinde yok.

**V13 DOĞRULANDI (2026-09-14, tours=31):** T2 counters.opened=3 (restart'ta korundu), M2 opened=3; ret artışları tam
22×4 ve 22×7 → defterler her turda yalnız on coini değerlendiriyor; ZEN son fiyatı yenileniyor (yalnız yönetim);
journal'da Traceback/ERROR yok; ana defter 42/2 değişmedi; T2 99.94, M2 100.37 USDT. Ana bot 24 saatte 541 aday,
0 açılış (kapı sırası onarımından beri bilinen davranış). SONRAKİ ADIM: bekleme. Haftalık `tb-check.sh` okuması;
üç eğri (ana bot, T2, M2) DENEY_V9/V11 dağılımıyla karşılaştırılır. Kod işi yok; gerçek para yok.

## V13R — sağlamlık taraması (2026-09-14, VPS'e dokunmadan)

PROTOCOL_V13R ön kayıtlı; 81/81 koşu (12 paralel, ~65 dk); `out/DENEY_V13R.md`, `out/v13r_sonuc.json`; araçlar:
`run_rule.py --exclude` (LOO sargısı), `momentum_rules.py` +5 araştırma varyantı (m2_tsmom21/42, t2r_ema200/150/250),
`run_v13r.py`, `make_deney_v13r.py`. Sonuç: F1 hiçbir pencerede kalkmadı (P2 SOL'a yoğun ama pozitif); F2 T2 EMA250
P2'de −15% (1 pencere), M2 komşuları hep pozitif; F3 T2 P3 kaydırılınca −4,2% (1 pencere), M2 +2,5%; F4 her
pencerede yoğun (en iyi 3 işlem brüt kârın %65–97'si); T2–M2 aylık korelasyon 0,36/−0,03/−0,15. Mekanik hüküm:
ikisi de "kırılamadı"; dürüst okuma: M2 her testte ayakta, T2 en yeni dönemde kırılgan. VPS'te değişiklik yok.
Ekim-2020 ilk 10 (VPS sıralaması): BTC ETH LINK YFI BCH UNI BNB LTC XRP DOT — V14 verisi (csv.gz paketi)
kullanıcı tarafından toplanıyor; YFI/BCH/UNI/DOT/LINK/LTC için borsa filtreleri de gerekecek.

## V14 — hayatta kalma yanlılığı (2026-09-14, koşu başladı)

PROTOCOL_V14.md ön kayıtlı (bayraklar S1 işaret değişimi, S2 yarıya iniş, S3 <10 işlem; S1 ≥ 2 pencere → ölçülmüş
beklenti GEÇERSİZ). Veri: `trading2-deploy/tb-2020-universe.tar.gz` (sha256 1b8754c8…, 37 MB, VPS `vps-collect-2020-universe.sh`
v2: sıralama HistoryStore ile, parquet→csv.gz), `run_v14.py --prepare` → `C:/Users/berke/wt-2020/data` (39 coinin 1d'si,
ilk 10'un 1h/4h/1d/funding/oi_1h; `symbol_filters.json` kopyalandı) PREPARE_OK; evren BTC ETH LINK YFI BCH UNI BNB LTC
XRP DOT = protokol. Koşu: `run_v14.py` (6 koşu, `TRADINGBOT_CACHE_DIR` ile ayrı önbellek), rapor `make_deney_v14.py`
→ `out/DENEY_V14.md`. Not: VPS'te kullanıcı betiği bir kez daha nohup ile başlattı (idempotent; paket zaten çekildi).

**V14 OLAY (2026-09-14):** ilk 6 koşu YANLIŞ ARŞİVİ okudu — `run_rule.py` import anında `TRADINGBOT_CACHE_DIR=wt-ten`
yazıyor, `run_v14.py`'nin env'i ezildi; YFI/BCH/UNI/DOT hiç yüklenmedi (ne işlem ne ret). İlk DENEY_V14 silindi
(`out/v14_run_INVALID_wtten.log` tutuldu). Onarım: `run_rule.py --cache-dir` + meta `history_root`, `run_v14.py`
guard (yanlış kök → rc=99). Duman koşusu `history_root = wt-2020/data/history` doğrulandı; 6 koşu yeniden başladı.

**V14 SONUÇ (2026-09-14, geçerli koşu, `history_root=wt-2020`):** T2 +89,4 / −11,9 / +35,2 (bugün +107/+34/+10) → S1
P2'de 1 pencere; M2 +101,0 / +21,1 / +9,6 (bugün +158/+46/+35) → S2 P2 ve P3. Mekanik hüküm ikisi için "beklenti
aşağı revize, ileri test sürer". P2'nin bugünkü kârı SOL'muş (2020 listesinde yok). M2 hiçbir pencerede eksi değil;
6 yıllık bileşik M2 ~+167% (bugünkü evrende ~+408%), T2 ~+126%. Sekiz coin gerçekten işlem gördü. VPS'te
değişiklik yok. Bekleme haftası işi tamam: V13R + V14. Sonraki oturum: haftalık tb-check okuması; kod işi yok.

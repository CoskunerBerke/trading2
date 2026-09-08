# LEARN_COUNT_REPAIR_DEPLOYMENT_V4 — dağıtım ve durum göçü kaydı

**Durum:** DAĞITILDI ve DOĞRULANDI · PAPER · 2026-09-08 ·
`feature/quant-evaluation-v1` @ `9bafb57f0b39caef68d791f4d74daad351b6afec`

> Bu belge `docs/LEARN_COUNT_REPAIR_RELEASE_V4.md`'nin §7 "UYGULANMADI" planının
> **yürütme kaydıdır**. O belgedeki "HAZIRLIK TAMAM · DAĞITILMADI" başlığı artık TARİHSELDİR.
>
> **Bu bir kârlılık sonucu DEĞİLDİR.** Yalnız öğrenme sayaçları ve panel ifadeleri düzeltildi.
> Kapı olasılık kaynağı, ödeme formülü, giriş/çıkış eşikleri ve strateji DEĞİŞMEDİ; ayrı ödeme
> araştırması dağıtılmadı.

---

## 1. Sürüm kimliği ve kapılar

| | Değer |
| --- | --- |
| Dağıtım dalı | `feature/quant-evaluation-v1` |
| Dağıtılan SHA | `9bafb57f0b39caef68d791f4d74daad351b6afec` |
| Önceki üretim SHA | `12db804c7301dbebe337813e20e44c9e967d260b` (`.last_good_commit`) |
| Push biçimi | **ileri sarma** (`12db804..9bafb57`), force YOK, araştırma birleştirme YOK |
| Sürüm ağacı testleri | **1936 passed / 26 skipped / 0 failed** (343.9 s) |
| CI (tam SHA) | run **34217288369** · `9bafb57f0b39` · 3/3 iş, 60/60 adım **success** |
| Araştırma paketi | üretim ağacında **YOK** (`tradingbot/research` 0 dosya) |

Sürüm ağacı ile araştırma dalı arasındaki TEK fark araştırma paketinin silinmesidir; altı
üretim dosyası (`learn/model.py`, `learn/learner_v2.py`, `learn/reconcile.py`,
`dashboard/views.py`, `dashboard/app.py`, `dashboard/state.py`) **birebir aynıdır**.

## 2. Cutover

| Adım | Zaman (UTC) |
| --- | --- |
| Doğrulanmış yedek | 10:56:42 — `tradingbot-manual-20260908T105642Z.tar.gz` · `ecabbf67…` · 802 girdi |
| Worker durdurma | **10:56:51** — `SIGTERM → tur 45 bitince temiz durdu` |
| Yetkili anlık görüntü | 10:58:33 — 13 dosya, `cutover_snapshot.tar.gz` `6460bc15…` |
| Durum göçü | 10:59:10 |
| Sürüm kurulumu | 11:00:00 |
| Servis başlatma | **11:00:49** |
| `ready` 200 | **11:12:19** (soğuk ilk tur bitince) |

**Kesinti: 238 saniye.** Durdurma `systemctl stop` ile kooperatif oldu; ne defter/öğrenme
sınırında zorla kesilme oldu, ne de kapanış yarıda kaldı (`closed=0`).

## 3. Durum göçü — B (onar)

```
girdi  learn_v2.json  sha256 f3cd29cd25c22916a8db6af4bd8d3dd23f93ffbdc0bb0cc92c00557448824345
çıktı  learn_v2.json  sha256 ec481a3da502632b300ba9de39977787cf543f15268abf6043597d90f1982677
kanonik JSON  b17fed0f8ac56a2f… → 4b8c62392b82576f…
```

Ön koşullar TAZE anlık görüntü üzerinde yeniden koşuldu: **13/13 GEÇTİ**
(schema=2, işaret yok, çürüme yok, Bernoulli/ağırlıksız, yabancı düğüm yok, legacy import yok,
ata parite çift, Σrejim=global, global n = 2·`n_closed`, kanonik 25 kapanış / 7 kazanç).
Ders penceresi TAM (`lessons` 25 = `n_closed` 25, 500 sınırına 475 boşluk).

Bağımsız doğrulama **VERIFIED**:

| Yol | Sonuç |
| --- | --- |
| saklanan durum ≡ derslerden ESKİ semantikle replay | 100/100 düğüm, 0 uyuşmazlık |
| dönüştürülmüş durum ≡ derslerden YENİ semantikle replay | 100/100 düğüm, 0 uyuşmazlık |
| `exp_r` dokunulmadı | 19/19 düğüm, 0 uyuşmazlık |

Değişen 8 düğüm (`n`, `s`, `ss` **birlikte** yarılandı):

```
(global)            n 50.0 → 25    s 14.0 → 7.0
regime:TREND_UP     n 24.0 → 12    s  8.0 → 4.0
regime:TREND_DOWN   n 10   →  5    s  0.0 → 0.0
regime:EUPHORIC     n  4   →  2    s  4.0 → 2.0
regime:BREAKOUT     n  4   →  2    s  2.0 → 1.0
regime:RANGE        n  4   →  2    s  0.0 → 0.0
regime:SQUEEZE      n  2   →  1    s  0.0 → 0.0
regime:LOW_VOL      n  2   →  1    s  0.0 → 0.0
```

Alan bazlı fark denetimi: izinli 16 ata alanı + 1 işaret dışında **0 izinsiz değişiklik**.
`win` yaprakları, `exp_r`, `agent_hit`, `lessons`, `calibrator`, `n_closed`, `alpha`,
`prior_mean`, `half_life_days`, `last_metrics`, `baseline_metrics` ve `LearnedIndex` korundu.

Yazım: aynı dosya sisteminde doğrulanmış geçici dosya + `fsync` + `os.replace` + dizin
`fsync`; sahiplik/izin korundu (`0640 uid=999 gid=987`). Değiştirmeden hemen önce girdi
hash'i yeniden ölçüldü ve yakalanan hash'e EŞİT çıktı.

**İkinci dönüşüm reddedilir** — `ALREADY_CONVERTED`. Bu, dosyadaki `ancestor_count_repair`
işaretine BAĞLI DEĞİLDİR: `LearnerV2.save()` bilinmeyen üst düzey anahtarları korumaz, işaret
ilk doğal kapanışta DÜŞER; red aritmetik değişmezden gelir (`global n == n_closed`).

## 4. Yazıcı disiplini

Tek gerçek yazıcı `tradingbot watch` (`engine_v3.py`); panel yalnız okur (`StateReader`);
saatlik yedek `learn_v2.json`'a yazmaz ve cutover boyunca durduruldu (sonra geri açıldı).
Göç sırasında `pgrep` ile hem araç içinden hem dışarıdan yazıcı yokluğu doğrulandı; eski
PID'ler (311633/311634) `/proc` üzerinden çıkmış olarak onaylandı.

## 5. Geri alma hazırlığı (dağıtımdan ÖNCE prova edildi)

* **Case A** — yeni öğrenme yoksa: cutover `learn_v2.json` (`f3cd29cd…`) + `12db804` birlikte.
* **Case B** — yeni doğal kapanış olduysa: eski dosya GERİ YÜKLENMEZ. `lci_migrate.py reverse`
  mevcut durumu eski semantiğe çevirir; ata düğümleri ikiye katlama ile derslerden ESKİ
  semantikle replay AYNI sonucu vermelidir. 26 kapanışlı provada `global n=52` çıktı — yani
  yeni kapanış KORUNDU; gerçek `12db804` kodu bu durumu yükledi, `LearnedIndex` 26/26 satırı
  okudu, `lessons[].learning_keys` bozulmadı.

Kanonik defter, dolumlar, pozisyonlar, `trade_memory.jsonl`, pfexp dosyaları ve
`learned_closes.jsonl` bu prosedürle ASLA eski anlık görüntüye döndürülmez.

## 6. Provenans

`learning_keys` (semantik sürümü, kapanış anı rejimi, `win` yaprakları, `exp_r` yaprağı,
ağırlık, `feature_version`) artık derse ve MEVCUT `learned_closes.jsonl` indeksine ek alan
olarak yazılır. İkinci defter değildir, idempotency anahtarını değiştirmez, eski satırları
bozmaz. Dağıtım anında indekste `learning_keys` taşıyan satır **0**'dır (25 satırın hepsi
onarım öncesinden). İlk doğal kapanışta üretilmesi beklenir.

## 7. İki doğal tur — canary

Tur tetiklenmedi, test işlemi yaratılmadı; gözlem yalnız DOĞAL etkinliktir.

| Tur | Başlangıç | Bitiş | Süre | Sınıf |
| --- | --- | --- | --- | --- |
| #1 | 14:01:23 | 14:12:08 | **10 dk 45 s** | SOĞUK (tarama dahil, 131 sembol / 102 s) |
| #2 | 14:30:32 | 14:31:11 | **39 s** | SICAK |
| #3 | 14:49:34 | — | sürüyor | — |

Soğuk süre önceki yeniden başlatmanın profiliyle uyumludur (2026-09-07 22:39:29 → 22:49:51 =
10 dk 22 s). Kısa `ready=503` penceresi bu soğuk tur boyunca sürdü; **ready gerçekten
11:12:19Z'de 200 oldu** ve o andan sonra düşmedi.

`NRestarts=0` (her iki servis), `traceback|critical` sayısı **0/0**, `health.json` HEALTHY
(`seconds 38.2`, `symbols 12`, `decisions 12`, `opened 0`, `closed 0`).

### 7.1 Onarılmış istatistikler restart sonrası korundu

`learn_v2.json` sha256 hâlâ `ec481a3d…` — göç çıktısıyla BİREBİR. Göç iki kez uygulanmadı.

### 7.2 Düzeltilmiş sözleşme altında mutabakat

```
global n = 25 == n_closed 25 == benzersiz öğrenilmiş kapanış 25 == lessons 25
global s =  7 == kanonik kazanç (r > +0.25R) 7
Σ(regime n) = 25 == global n        Σ(regime s) = 7.0 == global s
exp_r global n = 25.0 (DOKUNULMADI)
```

Doğal kapanış OLMADI, dolayısıyla sayılacak yeni gözlem yoktur.

### 7.3 Kanonik defter farkı = doğal olay

Cutover anlık görüntüsüne göre alan bazlı fark: **eklenen 0 / silinen 0 / değişen 23**, ve
değişen alan adları yalnız `last_price`, `bars_held`, `updated_at`. `seq` 36, `equity`,
11 AÇIK pozisyon (aynı küme), `history` 25, `entries` 430 DEĞİŞMEDİ. Fiyat/zaman türevi
olmayan değişiklik **0**. Bu bir göç edimi değildir; piyasa hareketidir.

### 7.4 Deney kimlikleri

| Deney | id/cfg/start | Durum | Not |
| --- | --- | --- | --- |
| `pfexp_v1` | donmuş | SHADOW, `applied=False` | dosya sha256 **hiç değişmedi** (salt okunur) |
| `pfexp_v1_1` | donmuş | `SUPERSEDED_E_SCOPE_MISMATCH_DRAINING` | yalnız doğal ilerleme; kabul YOK |
| `pfexp_v1_2` | donmuş | `ACTIVE_SHADOW` | yalnız doğal ilerleme |

Her üçünde `experiment_id`, `policy_version`, `config_id`, `config_hash`,
`evaluation_start_at`, `frozen_at`, `status`, `mode`, `applied_to_canonical` ve
`n_comparable_closes` DEĞİŞMEDİ; kimlik dosyalarının sha256'sı aynıdır. Rapor alanı `code_sha`
`12db804` → `9bafb57` oldu — bu, raporu ÜRETEN kodun kaydıdır, deney kimliği değildir.
Sıfırlama, yeniden kayıt, backfill YOK.

### 7.5 Panel

* Kolonlar: `Konsensüs gücü` ve `P(kazanç) — istatistiksel tahmin`; eski `Güven` /
  düz `P(kazanç)` başlıkları YOK.
* «Alan anlamları (konsensüs gücü ≠ olasılık)» legend'i 5 maddeyle render ediliyor.
* Kalibrasyon durumu KANITTAN: «kalibratör FIT EDİLMEMİŞ (n_fit=0) · champion sınıflandırıcı:
  YOK» (`learn_v2.calibrator.n_fit = 0`, `models.json` champion yok). Sabit iddia YOK.
* Giriş planı üretilmeyen 11 satırda `Beklenen Net Getiri` ve `E[R]` = «yok (plan üretilmedi)».
  Bu anlık görüntüde hesaplanmış `%0.00` satırı yoktur; olsaydı `%0.00` olarak kalırdı.
* Açık pozisyon yönetimi tablosunda 11/11 satır `UNKNOWN` (`economics_evaluated=false`).
* NOT: faktör skorları ve uzman ajan tablolarındaki `Güven` kolonları AYRI bir alandır
  (ajan/faktör düzeyi) ve bu sürümün kapsamında değildir.

### 7.6 Değişmeyenler

Olasılık kaynağı, ekonomik eşikler, risk profili (`PAPER_RESEARCH`, `risk_per_trade 2.0`),
giriş/çıkış politikası ve offline ödeme mantığı dağıtımla DEĞİŞMEDİ.
Güvenlik: `mode=PAPER`, `live_order_path_enabled=false`, `ALLOW_LIVE_TRADING=false`,
kill switch **ARMED**, `TRADINGBOT_LEARNING_INFLUENCE_MODE=PAPER_BOUNDED`
(öğrenme etkisi evrensel SHADOW DEĞİLDİR), otomatik terfi kapalı.

## 8. Sonuç ve bekleyen tek kilometre taşı

**DEPLOYED_AND_VERIFIED.** Çalışan worker tek güncelleme semantiğini (`v2-multileaf`)
kullanıyor ve geçmiş 25 kapanışın ata düğümlerdeki çift sayımı kalıcı olarak onarıldı.

**PENDING_FIRST_NATURAL_CLOSE:** Canary penceresinde doğal kapanış olmadı
(`closed=0`, `learned_closes.jsonl` 25 satırda sabit, `learning_keys` taşıyan satır 0,
`learn_v2.json.bak` hâlâ 2026-09-05 — yani `save()` henüz çalışmadı). Bu yüzden `learning_keys`
provenansı ÜRETİMDE henüz gözlenmedi. Uygulama kanıtı sürüm ağacındaki 19 regresyondur
(`test_learning_provenance.py` 2 + `test_learning_count_integrity.py` 17), CI'da da koştu.
Dağıtımın kendisi bu nedenle belirsiz DEĞİLDİR; yalnız bu tek operasyonel gözlem bekliyor.

## 9. Saklanan kanıt

| Ne | Nerede |
| --- | --- |
| Doğrulanmış yedek | `data/backups/manual/tradingbot-manual-20260908T105642Z.tar.gz` (+ `.sha256`) |
| Cutover anlık görüntüsü | `/tmp/lci_cutover/snapshot/` + `cutover_snapshot.tar.gz` `6460bc15…` |
| Anlık görüntü hash listesi | `/tmp/lci_cutover/snapshot.sha256` |
| Göç manifesti | `/tmp/lci_cutover/manifests/migration_manifest.json` |
| Kuru çalıştırma kaydı | `/tmp/lci_cutover/dry_out.json`, `dry_manifest.json` |
| Yönetimsel araç (sabitlenmiş) | `/tmp/lci_admin/` — `lci_migrate.py` + sürüm ağacı + `learn_state_repair.py` |

Göç işareti `learn_v2.json` içinde KALICI DEĞİLDİR (bkz. §3); kalıcı kanıt manifest ve anlık
görüntüdür.

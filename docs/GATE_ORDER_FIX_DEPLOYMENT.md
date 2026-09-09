# Kapı Sırası + Bellek Onarımı — dağıtım kaydı (2026-09-09)

Dağıtılan: `feature/evidence-repairs-v1` @ **1c4cba1** (taban 8163a79). VPS dalı
`feature/quant-evaluation-v1` üzerine ff-only. origin'e push edildi.
Tasarım/ölçüm: release ağacında `docs/RESEARCH_TASK_LOG.md`, kabul ölçütleri
`docs/ACCEPTANCE_CRITERIA_GATE_ORDER.md` (sonuçlara bakılmadan yazıldı).

---

## 1. Ne onarıldı

### A. Ekonomik kapı kalibre olasılığı hiç kullanmıyordu (en büyük ekonomik sorun)

`engine_v3.tour()` içinde ekonomik kapı, öğrenici döngüsünden **48 satır önce** çalışıyordu.
Kapının içindeki `if d.p_win:` dalı o anda `head.py:354`'ün önselini okuyordu:
`0.5 + 0.25*confidence`, daima ≥ 0.5, yani hiçbir zaman falsy. Sonuç: `hierarchical_expectancy`'nin
kalibre olasılığı **her adayda** eziliyordu. Kod, kendi yorumunun (*"kalibre model tahmini
onceliklidir"*) tersini yapıyordu.

**Ölçüm** — kapının kimliğinden geri çözüm `p = (gross + |L|) / (W + |L|)`, üç bağımsız hesap
(ben, A ajanı, B ajanı) ve dördüncü olarak E ajanının doğrulaması aynı sonuca vardı:

| Kapının kullandığı olasılık | aday | pay |
|---|---:|---:|
| HEAD önseli `0.5+0.25*conf` | **2797** | **%100** |
| kayıtlı istatistiksel `p_win` | 0 | %0 |

* Ortalama beklenti şişmesi **+0.988R/aday**; 2113 aday (%75,5) kendi olasılığında negatif.
* **NATGAS 2026-09-08T13:51Z (kabul edildi):** kapı 0.625, istatistiksel 0.342,
  `0.625×1.525382 − 0.375 = 0.578364` (kayıtla 6 hanede birebir); gerçek olasılıkla **−0.136R**.
* Kusur `3c4c506`'dan (2026-08-21) beri var; kapı kalibre olasılığı **bir kez bile** kullanmamış.

**Onarım:** öğrenici döngüsü kapıdan önceye alındı. `legacy_chief.risk_mode` ayrı ve erken bir
`chief_mgr.decide(...)` çağrısından geliyor — `market_risk_mode` yalnız BTC rejimi + verdict
sayılarından türer, `d.opportunity`'ye bağımlı değildir; `decide` saftır (ampirik olarak
doğrulandı: `decisions_mutated=False`). Yetkili chief kararı kapıdan sonra kurulur.
**Yan kazanç:** `_influence_log` artık kapı anında dolu, bayat çapraz-tur
`decision_changed_by_learning` kusuru da kapandı.

### B. Worker OOM (canlı güvenilirlik kusuru)

Worker 4 GiB cgroup sınırında OOM ile öldürülmüştü (`status=9/KILL`, 15:57:25Z).
Kök neden ölçüldü: `entry_snapshot.jsonl` 93,6 MB'a çıkmıştı; `iter_hot_rows()` tamamını tek string
sonra tüm satırları ayrı liste olarak alıyordu; `by_candidate()` tur başına **iki kez**
çağrılıyordu; `_hot_lines()` ayrıca `retention_stats()` üzerinden her turda 374 MB tepe yapıyordu.

| Yol | Eski tepe | Yeni tepe |
|---|---|---|
| `retention_stats()` (her tur) | 374,3 MB | **0,3 MB** |
| tur içi iki `by_candidate()` | 889,1 MB | **258,2 MB** |
| saf akışla tam gezinme (93,6 MB) | 374,3 MB | **0,4 MB** |
| turlar arası tutulan grafik | — | **yok** |

**Tur başına toplam tepe azalması ≈ 1,0 GB.**

---

## 2. Bağımsız doğrulama kendi onarımlarımda 5 kusur buldu

Kabul ölçütü G4 gereği doğrulayıcı bağımsızdı; kendi yazdığımın tek onaylayanı ben olmadım.
Beşi de onarıldı ve testle kilitlendi.

| # | Kusur | Neden ciddiydi |
|---|---|---|
| E-1 | `drop_hot_cache()` iki tüketicinin **arasında** | Son tüketici yeniden ayrıştırıyor **ve** memo turlar arası ~258 MB kalıcı kalıyordu — yani bellek onarımım temel sürüme göre **gerileme**ydi |
| E-2 | `_hot_lines()` dönüştürülmemişti | Her turda 374 MB tepe, en büyük tek sıçrama açıkta kalmıştı |
| E-3 | `test_05` **boştu** | `features_from_brief` tur içinde iki yerden çağrılıyor; spy son çağrıyı kaydedince `risk_mode` yanlış sabitlense bile test geçiyordu |
| E-4 | `test_01` totolojiydi | Geri alma dedektörü değildi, öyleymiş gibi yazılmıştı |
| E-5 | Hiçbir test kapının `_execute`'tan önce çalıştığını iddia etmiyordu | Kapı emir yolundan sonraya taşınsa 6 testin hepsi geçerdi ve kapı **tamamen devre dışı** kalırdı |

E-3'ün onarımı, doğrulayıcının kullandığı tam senaryoyla sınandı: `risk_mode = "NOTR_HARDCODED"`
enjekte edildiğinde test artık **düşüyor**.

---

## 3. Dağıtım

| An (UTC) | Olay |
|---|---|
| 19:07:21 | Doğrulanmış yedek `tradingbot-manual-20260909T190721Z.tar.gz` (896 üye, sidecar OK, yollar güvenli) |
| 19:07:xx | `.last_good_commit=8163a79`; tag `backup/vps-pre-evrepairs-v1-8163a79` |
| 19:08:0x | Bundle sha256 `48aaf1a3…63bf` doğrulandı; 8163a79 → 1c4cba1 ff-only |
| 19:08:0x | **Yeniden başlatmadan ÖNCE üretimde doğrulanan değişmezler:** `d.p_win@10571 < gate@11690`; `gate@11690 < execute@12264`; `drop@19877 > experiment@19422`; `_hot_line_count` var ve akışlı; PAPER bayrakları değişmemiş, config uyarısı 0 |
| 19:08:0x | `doctor --quick` OK |
| 19:08:10 | Restart; `/health/live` 200 **4 sn** sonra; NRestarts=0 |

İlk deneme CRLF satır sonu yüzünden kabukta düştü; **hiçbir şey dağıtılmadı**, HEAD 8163a79'da
kaldı, betik düzeltilip tekrarlandı.

## 4. Beklenen davranış ve kabul ölçütleri

Karşı-olgusal ölçüm: kalibre olasılıkla işlem yapılabilir aday oranı **%91,7 → %1,9**; en son
kaydedilen döngüde **7/7 → 0/7**. **İşlem sayısının çok azalması beklenen ve doğru sonuçtur,
başarısızlık değildir.** Ölçülebilir kenarı olmayan bir sistemde doğru işlem sayısı sıfıra yakındır.
Tersine, işlem sayısı azalmazsa onarım uygulanmamış demektir (P1/P2 düşer).

Dağıtım sonrası kapılar: P1 (kapı artık HEAD önselini kullanmıyor, hedef %0), P2 (kayıtlı `p_win`
ile eşleşme ≥ %99), P3 (hata yok), P4 (sağlık), P5 (kanonik defter korunur), P6 (chief sıralaması).
Ayrıntı ve geri alma tetikleyicileri: `docs/ACCEPTANCE_CRITERIA_GATE_ORDER.md`.

## 4b. DOĞRULANMIŞ SONUÇ (ilk tur, 2026-09-09 22:08:57 → 22:19:41 yerel / 19:08 → 19:19 UTC)

İlk tur 643,7 sn, 22 karar, **0 açılış**, 0 hata, `ready=200`, `NRestarts=0`, HEALTHY.

**P1/P2 — asıl ölçüt, kapının kimliğinden geri çözümle:**

| | aday | pay | hedef |
|---|---:|---:|---|
| kalibre `p_win` ile eşleşen | **7** | **%100** | ≥ %99 ✓ |
| HEAD önseli ile eşleşen | **0** | **%0** | %0 ✓ |

Aynı semboller, dağıtım öncesi tahminimle birebir örtüşüyor:

| Sembol | Kayıtlı olasılık | Kapının kullandığı | Eski HEAD önseli | Yeni kenar | Boyut |
|---|---|---|---|---|---|
| PHA | 0,304 | 0,304 | 0,602 | −0,474 | 0,0 |
| DELL | 0,277 | 0,277 | 0,649 | −0,741 | 0,0 |
| MRVL | 0,259 | 0,259 | 0,500 | −0,881 | 0,0 |
| SKHY | 0,229 | 0,229 | 0,731 | −0,947 | 0,0 |
| TSLA | 0,212 | 0,212 | 0,612 | −1,089 | 0,0 |

**Huni** — yeni davranış: `actionable 7`, `ranked 7`, `trigger_fired 2`,
**`positive_conservative_edge 0`**, **`negative_edge_blocked 2`**, `opened 0`.
Eskiden bu adaylar pozitif kenarla geçiyordu.

**P3** hata 0 · **P4** sağlık tam · **P5** defter dağıtım anıyla birebir aynı
(açık 14, `seq` 44, `history` 30, yeni giriş yok; son kapanış F00038 NATGAS **18:46**, yani
dağıtımdan önce) · **P6** chief sıralaması 22 satır, 7'sinde `conservative_net_edge_r` dolu.

**Bellek:** tepe **4,15 GB → 3,78 GB** (4 GiB tavana karşı), RSS 3,54 GB. Düşüş `_hot_lines`
onarımıyla uyumlu. **OOM riski azaldı ama kalkmadı:** E ajanının işaret ettiği ve bu sürümde
DOKUNULMAYAN iki sıçrama duruyor — `engine_v3.py:3229` `path_rows` ~127 MB ve
`position_path.paths_by_trade()` ~130 MB (tur başına iki kez). Sıraya alındı.

## 5. Geri alma

`bash deploy/rollback.sh` (`.last_good_commit` → 8163a79). Kod/config içindir; ilerlemiş işlem
defteri eski yedeğe **döndürülmez**.

## 6. Kârlılık hakkında iddia yok

Bu dağıtım sistemi kendi sözleşmesiyle tutarlı hâle getirir ve kanıtlı bir güvenilirlik kusurunu
kapatır. Kârlılık kanıtı üretmez. Kalibre olasılığın gerçekten ayırt etme gücü olup olmadığı 29
kapanışla gösterilemiyor; B ajanının ölçümünde her iki olasılık da taban orandan kötü skorluyor
(Brier 0.231 ve 0.346, taban 0.183). Sıradaki iş eşik ayarı değil, olasılık modelinin kendisidir.

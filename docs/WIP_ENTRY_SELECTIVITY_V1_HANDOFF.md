# WIP HANDOFF — ENTRY_SELECTIVITY_CHALLENGER_V1

> **Durum: YARIM.** Bu doküman plansız bir PC kapanmasından sonra alınan kurtarma denetiminin
> kalıcı kaydıdır. Faz 1'in sayısal bulguları başka HİÇBİR yerde yoktur; kaybolursa yeniden
> ölçülmesi gerekir.

Tarih: 2026-09-02. Denetim sonucu: `SAFE_TO_RESUME_FROM_EXISTING_WIP`.

## 1. Depo durumu

| Alan | Değer |
| --- | --- |
| Ana branch | `feature/quant-evaluation-v1` |
| Local HEAD | `2707294d5f88d57dd3e97fc119eaffea3b0dfc73` |
| Origin HEAD | `2707294d5f88d57dd3e97fc119eaffea3b0dfc73` (0 ahead / 0 behind) |
| WIP branch | `wip/entry-selectivity-v1-recovered` |
| VPS HEAD | `8fc75030c4fd6fc92b02e2326c0e3aa65da81f45` |

VPS **bir commit geride ve fark yalnız dokümandır** (`2707294` docs-only). Çalışan kod aynı.

## 2. Kurtarma denetimi bulguları

- Index boş, stash yok, `MERGE_HEAD`/`REBASE`/`CHERRY_PICK`/`REVERT` markeri yok.
- Vault dışında **hiçbir izlenen dosya değişmemiş**. 207 vault dosyası kirli, bu normaldir.
- Vault dışı untracked yalnız iki modül. İkisi de AST-geçerli, import edilebilir, `__all__` taşıyor.
- **Hiçbir mevcut dosya bu iki modülü referans etmiyor** — tamamen izole. Motor çağırmıyor,
  config tanımıyor, test yok. Kesinti güvenli bir noktada olmuş.
- Ruff temiz, compileall OK, tam suite **1468 passed / 21 skipped** (kesinti öncesiyle aynı).
  Toplanan test 1489 — yeni test eklenmemiş.
- VPS'te kısmi deployment **yok**: `entry_*.py` dosyaları ve `entry_*` state dosyaları bulunmuyor.
- Son güvenli işlem: `entry_challenger.py` yazımı. Yarım üçüncü dosya yok.

## 3. Faz 1 ölçümleri (KALICILAŞTIRILDI — kaynak: 2026-09-02 üretim verisi)

### 3.1 Hipotez doğrulaması (defterden, 19 kapanış)

| Büyüklük | Hipotez | Ölçülen |
| --- | --- | --- |
| Kapanmış işlem | ~19 | 19 |
| Kazanan / kaybeden | 5 / 14 | 5 / 14 |
| Beklenti | −0,335R | −0,3350R |
| Ortalama kazanan | +1,71R | +1,7074R |
| Ortalama kaybeden | −1,07R | −1,0644R |
| Profit factor | 0,57 | 0,5729 |
| Çıkış koruması | SHADOW | SHADOW, applied 0 |

Toplam −6,3653R / −4,4029 USDT. Çıkış nedeni: 14 stop, 3 hedef2, 2 başa-baş stop.
Yön: 15 LONG / 4 SHORT. Kazanma oranı %26,3, ödeme oranı 1,60, kırılma noktası %38,5.
**Sorun ödeme oranında değil, kabul edilen kaybeden oranında.**

### 3.2 En kritik bulgu — `p_win` ve `edge` TERS ayrım yapıyor

19 kapanışın 19'u `trade_memory.jsonl` giriş kaydına bağlanabildi; 13'ünde `opportunity` dolu.

| Alan | Kazanan ort. | Kaybeden ort. | Yorum |
| --- | --- | --- | --- |
| `p_win` | 0,3430 (n=5) | 0,4342 (n=14) | **TERS** |
| `conservative_net_edge_r` | 0,4879 (n=3) | 0,5787 (n=10) | **TERS** |
| `net_expectancy_r` | 0,8590 | 0,8651 | ayrım yok |
| `expected_r` | 1,9364 | 1,9304 | ayrım yok (plan geometrisi artefaktı) |
| `consensus_confidence` | 0,5971 | 0,5938 | ayrım yok |
| `consensus_score` | 0,3832 | 0,1954 | **doğru yönde** |
| `atr_pct` | 1,9710 (n=3) | 2,6566 (n=10) | **doğru yönde** |

En büyük üç kazanç en düşük `p_win` değerlerindeydi: BZ 0,243 (+2,429R), CL 0,272 (+2,415R),
ZRO 0,390 (+2,366R).

**Sonuç: bugün kabul kararını veren iki büyüklük üzerinden seçicilik uygulamak kazananları
elerdi.** Bu bir tasarım kısıtıdır, challenger A yine tanımlanır ama bu örneklemde ters çalışır.
n=5 kazanan istatistiksel sonuç için çok azdır; bu bir gözlemdir, sonuç değildir.

### 3.3 Alan bulunabilirliği (52 ACCEPTED karar günlüğü kaydı)

**Dolu (52/52):** `confidence`, `p_win`, `expected_r`, `leverage`, `planned_notional`, `regime`,
`risk_allowed`, `applied_risk_usdt`, `size_multiplier_total`, `execution_cost_estimate`,
`specialist_scores`, `features` (68 alan), `timeframe`, `chief_allow`, `feature_version`,
`features_missing`.

**Tamamen boş (0/52):** `price`, `setup`, `vetoes`, `risk_reasons`, `code_sha`, `config_hash`,
`policy_id`, `market_type`.

`features` içinde boş: `p_win`, `spread_pct`, `est_slippage_pct`, `depth_ratio`, `liquidity_ok`,
`stop`.

**En kritik boşluk:** `opportunity` / `conservative_net_edge_r` karar günlüğünde **hiç yok** —
oysa kabul kararını veren tek ekonomik büyüklük odur.

### 3.4 Kabul → açılış hunisi

52 ACCEPTED adayın **45'i `EXCHANGE_REJECTED`**; yalnız 7'si gerçekten açıldı.
`outcome_kind` dağılımı (20.000 kayıt): SCREENED_OUT 15810, NON_ACTIONABLE 2763, NO_TRIGGER 496,
RISK_REJECTED 383, LEVERAGE_BLOCKED 276, RESEARCH_BLOCKED 185, ACCEPTED 52, DATA_INVALID 17.

### 3.5 Kabul çağrı zinciri (kaynaktan)

```
market data → agents/specialists → coinhead consensus
  → learner2.predict (p_win) → _learning_influence (SHADOW/PAPER_BOUNDED)
  → _assess_opportunities → opportunity.assess  ← KABUL KARARI BURADA
  → chief.priority + conservative_net_edge_r ile sıralama
  → _execute_locked: chief veto → tetik → ekonomi → duplicate → araştırma politikası
  → boyut çarpanları → RiskEngine.evaluate → ledger.open
  → tick → _finalize → outcome → lesson
```

Kabul eşiği tek sayıdır: `conservative_net_edge_r = net − k/sqrt(n+1) − soft_penalty`,
`opportunity.py`, `UNCERTAINTY_K = 0.20`, `FULL_SIZE_EDGE_R = 0.35`.
`avg_win_r`/`avg_loss_r` örnek yetersizken `1.6`/`1.0` **varsayılanına** düşer — ölçüm değildir.

## 4. Kurtarılan iki modül

| Dosya | Satır | Amaç |
| --- | --- | --- |
| `tradingbot/learn/entry_snapshot.py` | 413 | Faz 2. Sıralamaya giren her aday için append-only point-in-time snapshot. Her alanın kaynağını taşır (`MEASURED`/`MODELED`/`DEFAULTED`/`MISSING`); eksik alan sıfır sayılmaz. `EntrySnapshotStore`, deterministik `candidate_id`/`decision_id`, `trade_id` bağlama, `snapshot_from_memory_entry` legacy köprüsü. |
| `tradingbot/learn/entry_challenger.py` | 364 | Faz 3. Beş bağımsız challenger ailesi (A olasılık/edge, B rejim/yön, C konsensüs dağılımı, D likidite/maliyet-risk, E portföy ısısı). Versiyonlu `EntryChallengerConfig`, `config_id`. `applied` daima `False`. |

Tasarım sözleşmeleri (değiştirilmemeli):

- Eksik veri VETO gerekçesi **değildir** (`MISSING_MEANS_ACCEPT = True`). Ölçemediğimiz için
  reddetmek, ölçtüğümüzü iddia etmenin başka biçimidir.
- Eşikler 19 işleme uydurulmadı. Kırılma noktası `p* = 1/(1+payoff)` ekonomik kimliğinden gelir.
- Aileler **birleştirilmez**; birleşik süper filtre hangi gerekçenin işe yaradığını ölçülemez kılar.
- D ailesi üretimde bugün karar veremez (likidite alanları 0/52) ve bunu açıkça bildirir.

## 5. YENİDEN YAZILMAMASI gereken dosyalar

- `tradingbot/learn/entry_snapshot.py`
- `tradingbot/learn/entry_challenger.py`
- Zaten canlıda olan ve dokunulmaması gereken: `learn/close_chain.py`, `learn/provenance.py`,
  `learn/reconcile.py`, `learn/position_mgmt.py`, `learn/position_path.py`, `learn/exit_policy.py`,
  `learn/exit_eval.py`, `learn/exit_executor.py`.

## 6. Kanonik VPS durumu ve güvenlik değişmezleri

Ölçüm 2026-09-02, salt okunur.

| Kontrol | Değer |
| --- | --- |
| HEAD / branch / tree | `8fc7503` / `feature/quant-evaluation-v1` / temiz |
| worker | active, enabled, NRestarts 0, PID 240097 |
| dashboard | active, enabled, NRestarts 0, PID 240099 |
| health ready / live | 200 / 200, heartbeat yaşı 30,7 sn |
| MODE | PAPER |
| live_order_path | False |
| ALLOW_LIVE_TRADING | false |
| kill switch | ARMED |
| outbox | 0 |
| contributed capital | `starting_equity` 100,0 |
| risk / max açık risk / kaldıraç tavanı | 2,0% / 6,0% / 5 |
| exit modu | SHADOW, applied 0, verdict `INSUFFICIENT_EXIT_SAMPLE` |
| açık pozisyon | 9 (AAPL, BZ, CL, CRCL, ETH, GOOGL, MSFT, SOL, SUI) |
| kapanış | 20; zincir 20/20/20, eksik 0, duplicate 0 |
| futures fingerprint | `18b70fbf1985aacf` |
| spot fingerprint | `ff3b6a3df374c96d` |
| Traceback/CRITICAL (3 saat) | 0 |
| kısmi deployment | YOK (`entry_*.py` ve `entry_*` state dosyaları bulunmuyor) |

Fingerprint alan kümesi (tutarlı kullanılmalı): futures için `side, qty, entry_avg, stop,
take_profit, targets, targets_hit, leverage, isolated_margin, tp1_done, initial_stop, initial_qty`;
spot için `assets, lots, locked_assets, position_meta, cash, open_orders` (**`positions` anahtarı
spot defterinde YOKTUR** — boş sözlük üzerinden hash almak vacuous kanıt üretir).

## 7. Görevin tamamı — faz durumu

| Faz | İçerik | Durum |
| --- | --- | --- |
| 1 | Kanonik denetim, alan kaynağı/erişilebilirliği | **TAMAM** (bulgular bölüm 3'te) |
| 2 | Giriş adayı point-in-time snapshot | **MODÜL TAMAM**, motora bağlı DEĞİL |
| 3 | Beş SHADOW challenger ailesi | **MODÜL TAMAM**, bağlı DEĞİL |
| 4 | Sonuç atıfı (blocked loser, missed winner, CVaR5, konsantrasyon) | **YAPILMADI** |
| 5 | Offline replay + üç yönlü walk-forward denetimi | **YAPILMADI** |
| 6 | Panel bölümü + LLM sayfası dürüstlüğü | **YAPILMADI** |
| 7 | Terfi kapıları | **YAPILMADI** |
| 8 | Testler + deployment | **YAPILMADI** |
| — | `config_v3.EntrySelectivitySection` | **YAPILMADI** |
| — | Motor bağlantısı (`engine_v3`) | **YAPILMADI** |

### Değişmez kısıtlar (görev metninden)

Aktif giriş kararı, pozisyon büyüklüğü, kaldıraç, stop, TP, RiskEngine, muhasebe, defter,
gateway ve canlı emir davranışı **DEĞİŞMEYECEK**. Sistem `mode = SHADOW`, `applied = false`
kalacak. Yalnız gözlem ve karşı-olgusal karar üretir.

## 8. Tam olarak sıradaki adım

1. `tradingbot/learn/entry_eval.py` yaz (Faz 4 + 7):
   - Bağlı bir işlem kapandığında her aile için: blocked loser, blocked winner, kaçınılan zarar
     R/USDT, kaçırılan kâr R/USDT, karşı-olgusal beklenti, profit factor, drawdown, CVaR5,
     fee/funding/slippage duyarlılığı, sembol/yön/rejim yoğunlaşması.
   - Deterministik outcome kimliğiyle tekilleştirme.
   - Terfi kapıları: 50 gerçekten bağlı kapanış, 30 takvim günü, yön/rejim kapsamı, pozitif
     out-of-sample iyileşme, sıfırı dışlayan güven aralığı, PF iyileşmesi, kötüleşmemiş
     drawdown/CVaR, kabul edilebilir missed-winner oranı, tek sembol yoğunlaşması yok,
     leakage ve point-in-time kontrolleri.
   - Kapılar dolmadıkça `verdict = INSUFFICIENT_ENTRY_SAMPLE`, `applied = false`.
   - **`LEGACY_MEMORY` kayıtları terfi kanıtı SAYILMAZ**; yalnız gözlem.
2. `config_v3.EntrySelectivitySection` (fail-closed: `SHADOW` dışı mod ve `auto_promotion=true`
   `ConfigError`).
3. `engine_v3` bağlantısı: sıralama anında snapshot yaz, açılışta `trade_id` bağla,
   tur sonunda challenger değerlendir ve rapor yaz. Fail-safe (arıza turu durdurmaz).
4. Faz 5: mevcut geçmiş veriyle point-in-time giriş kararı sadakatle yeniden üretilebilir mi?
   **Beklenen sonuç fail-closed**: `opportunity` günlükte yok, likidite alanları 0/52,
   `code_sha`/`config_hash` boş. Eksik alanları tam listeyle raporla, sentetik kârlılık uydurma.
5. Faz 6: `/learning` altına salt okunur "Giriş Seçiciliği" bölümü + `/api/entry-selectivity`.
   LLM sayfası gerçek durumu izleyip `DISABLED` / `NOT_CONFIGURED` / `NO_CALLS` göstermeli;
   LLM etkinleştirme, sağlayıcı ekleme veya secret basma YOK.
6. Faz 8: en az 30 regresyon, sonra tam suite + ruff + compileall + secret tarama.

## 9. Hâlâ gereken testler (en az 30)

no-lookahead; eksik alan `UNKNOWN` kalır; snapshot outcome'dan ÖNCE yazılır; deterministik kimlik;
tekilleştirme; legacy dışlama (`LEGACY_MEMORY` terfi kanıtı değil); challenger izolasyonu
(gateway/execution/accounting/RiskEngine mutasyon importu YOK — AST testi); aktif karar
değişmez; feature açık/kapalı RiskEngine sonucu birebir aynı; maliyet ve funding; walk-forward
izolasyonu; panel bozuk şemada 500 vermez; PAPER/live güvenlik değişmezleri; beş ailenin her biri
için ACCEPT/VETO ve `MISSING_DATA` yolu; `applied=false` her zaman; config fail-closed;
`p_win` ters kalibrasyonunun uydurma bir eşikle maskelenmediği.

## 10. Deployment kısıtları

CI tamamen yeşil olmadan VPS'e **geçilmez**. Sonra sırayla: salt okunur preflight, doğrulanmış
yedek, futures + spot fingerprint, `merge --ff-only`, yalnız SHADOW config, restart,
iki doğal tur canary, `applied = 0` kanıtı, açık pozisyon/stop/TP/boyut/kaldıraç/sermaye/risk
bütçesi değişmediğinin kanıtı, endpoint smoke.

Elle pozisyon açma/kapatma/değiştirme YOK. Giriş filtresi veya çıkış politikası aktive etme YOK.
Sermaye, risk bütçesi, kaldıraç değiştirme YOK. Legend entegrasyonuna dokunma YOK.

`git -c protocol.version=0 fetch origin` kullan: GitHub bu IP için anonim protokol v2 isteğine
401 dönebiliyor. Uzun canary'leri `PowerShell(run_in_background=True)` ile başlat; Bash tool'unun
`ssh`'i Windows ssh-agent'ını görmez.

## 11. Beklenen nihai sonuç

`ENTRY_SELECTIVITY_CHALLENGER_V1_SHADOW_PASSED` ya da
`ENTRY_SELECTIVITY_CHALLENGER_V1_BLOCKED`.

Faz 5'in fail-closed çıkması **beklenen ve dürüst** sonuçtur; bu tek başına görevi BLOCKED
yapmaz. BLOCKED, snapshot/challenger katmanının SHADOW'da bile güvenle çalıştırılamadığı
durumdur.

Bugünkü veriyle terfi **imkânsızdır**: 0 gerçekten bağlı kapanış (yeni snapshot deposu boş),
19 kapanış yalnız `LEGACY_MEMORY`. Aktivasyondan önce en az 50 bağlı kapanış ve 30 takvim günü
birikmeli.

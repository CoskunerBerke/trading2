# PROFITABILITY_EXPERIMENT_V1_1 — karar anı A/E ile düzeltilmiş beş kollu deney

**Durum:** SHADOW PAPER ONLY · `applied_to_canonical = false` · `auto_promotion = false` ·
terfi bugün **imkânsız** · `pfexp_v1` **SUPERSEDED_INCOMPLETE_ENTRY_INPUT** (salt okunur).

> **Bu deney kâr vaat etmez ve kârı kanıtlamaz.** Hiçbir politika kârlı kanıtlanmadı, kazanan
> strateji seçilmedi, otomatik terfi mümkün değil. Tarihsel v1 kanıtı yeniden yazılmadı;
> düzeltilmiş deney sıfırdan başlar; eksik girdi ABSTAIN demektir.

Önceki tasarım: `docs/PROFITABILITY_EXPERIMENT_V1.md`. Bu belge yalnız **neyin yanlış olduğunu,
neyin değiştiğini ve neyin korunduğunu** anlatır.

---

## 1. Doğrulanmış kusur (2026-09-06)

| Soru | Kanıt |
| --- | --- |
| P1/P4 A/E'yi nereden alıyordu? | `engine_v3._experiment_candidates` (4f3c417): `entry_selectivity.json` → `trades[].families` |
| Bu kaynak yeni açılan işlemi içerebilir mi? | Hayır. `trades`, `_write_entry_eval` içinde `build_report(closes=canonical_closes(history))` çıktısından türetilir — yalnız **kapanmış** işlemler. Açık pozisyon `ledger.positions`tadır, `history`de değil. |
| Üretimde ne oldu? | F00035 (ZEN/USDT LONG, 2026-09-05T18:01:32Z): P0/P2/P3 `ACCEPT` + simüle pozisyon; P1/P4 `ABSTAIN` / `ENTRY_FAMILY_DECISION_UNAVAILABLE`. `entry_selectivity.json.trades` içinde F00035 satırı **yok** (24 satır, hepsi kapanmış). |
| Snapshot A/E kararını saklıyor mu? | Hayır; snapshot yalnız karar anı **girdilerini** saklar (`p_win`, `conservative_net_edge_r`, `avg_win_r/avg_loss_r`, `portfolio_open_risk_usdt`, `same_direction_open`, …). |
| Hangi saf değerlendirici hesaplayabilir? | `entry_challenger.challenger_a` / `challenger_e` (`evaluate_all`) — `entry_v1.0.0` eşikleriyle. |
| Yasak bilgi okuyor mu? | Hayır: yalnız snapshot alanları + parametreler. Kapanış-atıf raporu `realized_payoff` için önceki kapanışları kullanır; canlı yol bunu **vermez**. |

Fail-safe `ABSTAIN` doğruydu; **girdi borusu yanlıştı**. Deney pratikte yalnız P0/P2/P3'ü ölçtü
ve geçerli bir beş kollu karşılaştırma değildi.

### Neden F00035 geriye dönük onarılamaz

* Kararı 38 yol kaydı ve kısmi sonuç (MFE/MAE, net R) görüldükten **sonra** vermek karar anı
  kararı değildir; örneklem ileri-bakış kirlenir.
* v1 olay defteri ekle-yalnızdır ve deterministik `event_id` taşır: F00035 için P1/P4 karar
  olayı **zaten var**; ikinci bir karar aynı kimlikle yazılamaz, farklı kimlikle yazılırsa aynı
  işlem/politika için iki karar olur.
* F00035'in snapshot'ı `risk_budget_usdt` taşımaz; E ailesinin ısı oranı point-in-time
  ölçülemez.

Bu yüzden F00035 **yalnız** v1 kanıtında kalır ve v1.1'de
`PRE_EXPERIMENT_OBSERVATION_ONLY`dir — düzeltilmiş başlangıçtan sonra kapansa bile.

---

## 2. Tek kanonik A/E yolu (`learn/profitability_ae.py`)

```
entry_snapshot (değişmez, RANKING, sees_outcome=false)
    └─ ALLOWED_SNAPSHOT_KEYS ile SINIRLI görünüm
        ├─ challenger_a(view, entry_cfg, realized_payoff=None)
        └─ challenger_e(view, entry_cfg, risk_budget_usdt=view.risk_budget_usdt)
            └─ üç değerli bacak: VETO→FILTER · ölçülmüş ACCEPT→ACCEPT · eksik→ABSTAIN
```

* **as-of** = snapshot `ts`. Beş politika aynı aday kimliğini ve aynı as-of'u alır.
* **Eşik kopyalanmaz/ayarlanmaz**: `entry_cfg` motorun kanonik `EntryChallengerConfig`idir.
* **Eksik → ABSTAIN**: `ENTRY_FAMILY_A_INPUT_MISSING` / `ENTRY_FAMILY_E_INPUT_MISSING` + alan
  adları. Ölçülmüş sıfır sıfırdır; ölçülmemiş alan `None`.
* **Kapanış geçmişi / öğrenilmiş sonuç / yol / ders OKUNMAZ**; `realized_payoff` verilmez.
* Snapshot point-in-time değilse ya da sonuç alanı taşıyorsa **reddedilir** (ABSTAIN).
* Provenans: `source=ENTRY_SNAPSHOT`, `stage=RANKING`, `sees_outcome=false`,
  `close_history_read=false`, `network=false`.

### Yeni snapshot alanı: `risk_budget_usdt`

Şampiyonun kendi türetmesi (`equity × max_total_open_risk_pct / 100`) karar anında
`chief_permission` bağlamına konur ve snapshot'a **MEASURED** alan olarak girer. Eski satırlar
bu alanı taşımaz → `MISSING` (sıfır değil) → E bacağı ABSTAIN. Şema eklemeli ve geriye uyumlu.

---

## 3. Politika davranışı (v1.1)

| Politika | Davranış |
| --- | --- |
| P0 | şampiyon girişini aynen aynalar |
| P1 | snapshot A/E: bir bacak FILTER → FILTER; hiçbiri FILTER değil ama bir bacak ABSTAIN → ABSTAIN; iki ölçülmüş ACCEPT → ACCEPT |
| P2 | değişmedi (yön/korelasyon yoğunlaşması) |
| P3 | girişi aynalar; yalnız donmuş kâr koruma çıkışı farklıdır |
| P4 | düzeltilmiş P1 + değişmemiş P2 + P3 çıkışı; **FILTER > ABSTAIN > ACCEPT** |

ABSTAIN asla sessizce FILTER ya da ACCEPT olmaz. FILTER/ABSTAIN: karar olayı kalır, dolum/
pozisyon yok. ACCEPT: tam bir idempotent simüle pozisyon, kanonik ekonomi aynen kopyalanır.

---

## 4. Sürümleme ve kanıt koruma

| Öğe | `pfexp_v1` (tarihsel) | `pfexp_v1_1` (düzeltilmiş) |
| --- | --- | --- |
| policy_version | `pfexp_v1.0.0` | `pfexp_v1.1.0` |
| config_id | `e3863761d50c79c3` | yeni deterministik (eşikler + sürüm) |
| A/E kaynağı | `CLOSED_TRADE_ATTRIBUTION` | `ENTRY_SNAPSHOT_POINT_IN_TIME` |
| dosyalar | `profitability_experiment{_events.jsonl,_books.json,.json}` — **bir daha yazılmaz** | `profitability_experiment_v1_1{_events.jsonl,_books.json,_identity.json,.json}` |
| başlangıç | `2026-09-05T06:31:03Z` (değişmez) | dağıtım sonrası ilk motor kurulumunda **bir kez** dondurulur (`_identity.json`) |
| motor | **çalıştırmayı reddeder** (`ValueError`) | canlı |

* Olay/dolum/pozisyon/kapanış/kitap/rapor hepsi `experiment_id + policy_version + config_id +
  code_sha` taşır. Üçlü uyuşmayan olay **yabancıdır**: yazılmaz, replay'de sayılır ve atlanır.
  Kitap anlık görüntüsünün kimliği/gömülü kimliği/checksum'ı uyuşmazsa güvenilmez → defterden
  replay (fail-closed).
* `evaluation_start_at` config ile verilemez, başka deneyden devralınmaz, geriye/ileriye
  çekilemez.
* v1.1 raporu `superseded_versions[0]` içinde v1'in **salt okunur** özetini (sayımlar, işlem
  başına katılım, kapsam kusuru, dosya sha256'ları) taşır.

---

## 5. Pano

`/learning` → «Kârlılık Deneyi»: dürüstlük beyanları, **düzeltilmiş sürüm** (kimlik, yeni
başlangıç, kararlar, kapsam, kapanışlar, metrikler + GA, kapılar, düşük örneklem, bütünlük/
checksum/yabancı kimlik) ve **tarihsel sürüm** (SUPERSEDED statüsü, orijinal başlangıç, orijinal
sayımlar, F00035'in gerçek P0/P2/P3 katılımı ve P1/P4 çekimserliği, kapsam kusuru, **kârlılık
sonucu yok**). API: `/api/state/profitability_experiment_v1_1` ve
`/api/state/profitability_experiment`.

---

## 6. Bilinen sınırlamalar

1. Deney kârlılığı kanıtlamaz; bugün sıfır karşılaştırılabilir kapanış vardır.
2. v1'in F00035 simülasyonu dondurulmuştur (P0/P2/P3 "açık" kalır); kanonik gerçek defterdedir.
3. E bacağı yalnız yeni kodla yazılmış snapshot'larda (bütçe alanı) tam ölçülebilir.
4. A bacağının ödeme oranı snapshot'ın karar anı `avg_win_r/avg_loss_r` istatistiğinden gelir;
   kapanış-atıf raporundaki genişleyen pencere ödeme oranından farklı olabilir (bilinçli).
5. Terfi: ≥50 karşılaştırılabilir kapanış, ≥30 takvim günü ve diğer kapılar **aynen** korunur.

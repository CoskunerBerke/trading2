# PAPER Learning Loop Integrity V3

Amaç: kapanan **her** PAPER işleminin eksiksiz, tam bir kez ve idempotent biçimde öğrenilmesi;
öğrenmenin aktif emir yoluna dokunmadan ölçülebilir hale gelmesi.

Taban: `feature/quant-evaluation-v1` @ `ffbbcf6`. Ölçüm tarihi 2026-09-02.

## 1. Ölçülen başlangıç durumu (varsayım değil)

VPS canonical state (`/opt/tradingbot/data/state`) üzerinden:

| Ölçüm | Değer |
| ----- | ----- |
| Kanonik final kapanış (`futures_ledger.history`) | 18 |
| Outcome (`trade_memory.jsonl`, `kind=exit`) | 18 |
| Ders (`learning.json.lessons`) | 18 |
| Duplicate outcome / ders | 0 / 0 |
| Kazanan / kaybeden | 5 / 13 |
| Net PnL | −3,5295 USDT |
| Toplam R | −5,3391R (ortalama −0,2966R) |
| TP1 sonrası kapanan | 5 |
| Giriş kararına GERÇEKTEN bağlı | 2 |
| Quant rapor örneklemi | 9 (kanoniğin yarısı), yaş ~151 saat |

Yani "her işlem öğreniliyor mu" sorusunun cevabı **evet**; asıl boşluklar başka yerdeydi.

### Bulunan gerçek boşluklar

1. **Kayıp penceresi.** `engine_v3.tour()` sırası `ledger2.save()` → öğrenme şeklindedir. İki adım
   arasında süreç ölürse kapanış defterde kalıcıdır, fakat `ledger2.tick()` onu bir daha
   döndürmez ve o işlem **kalıcı olarak** öğrenilmemiş kalır. Hiçbir kurtarma geçişi yoktu.

2. **Giriş bağlantısı yok.** `TradeRecord` içinde `decision_id` alanı **yoktur**. Karar günlüğü
   20.000 satır tavanında arşive döndüğü için geçmişe dönük arama da güvenilir değil: 18
   kapanışın yalnız 2'si bir `ACCEPTED` kaydına bağlanabildi.

3. **`build_outcome_link` çağrısı `decision_id` geçmiyordu.** Üretimdeki bütün outcome bağlantı
   kayıtları `decision_id: null` ile yazılmıştı (6/6). "Bağlantı kaydı" hiçbir karara
   bağlanmıyordu.

4. **`code_sha` / `config_hash` boş.** Karar günlüğünde 0/20000. Kimse `cfg.code_sha` set
   etmiyordu.

5. **Açık pozisyonda sahte ekonomi.** `coinhead/head.py:226-236` açık pozisyonlu sembol için
   erken döner; ekonomik değerlendirme **hiç çalışmaz**. Bu yüzden sekiz açık pozisyonun
   sekizinde de `expected_r = 0.0` ve `p_win ≈ 0.38` görünüyordu. Bunlar ölçüm değil,
   doldurulmamış dataclass varsayılanlarıdır.

6. **`REDUCE`/`EXIT` tüketilmiyor.** `_execute_locked` yalnız `d.is_actionable` adayları işler ve
   `is_actionable` sadece `SPOT_LONG`/`FUTURES_LONG`/`FUTURES_SHORT` için `True`'dur. Yani bu iki
   karar bugün **yalnız ekrana yazılan bir görüştür**.

7. **Maliyet dersе ulaşmıyordu.** Gözlem bloğu olan 9 dersin **0'ında** `fee_drag_r` doluydu.
   `Learner._diagnose`, `classify_edge_execution`'a `labels` geçmiyordu.

## 2. Kanonik zincir

```
FINAL LEDGER CLOSE
  → immutable outcome event
  → entry/outcome link
  → exactly-one lesson
  → calibration/statistics update
  → bounded agent-weight update
  → learning summary
  → quant refresh
```

**Kanonik kaynak defterdir.** `history` listesi yalnız `FuturesLedgerV2._finalize()` tarafından
büyütülür ve `_finalize` yalnız pozisyon tamamen kapandığında çağrılır. TP1 kısmi azaltması
`_close_part` ile yapılır ve `history`'ye satır **eklemez**; yalnız kapanış kaydında
`tp1_done=True` bırakır. Bu yüzden "history satırı = final kapanış" eşitliği kod düzeyinde
doğrudur ve kısmi TP asla final kapanış sayılmaz.

## 3. Yeni modüller

| Modül | Sorumluluk |
| ----- | ---------- |
| `learn/close_chain.py` | Kanonik kapanış listesi, deterministik `close_event_id`, R-normalize metrikler, zincir raporu |
| `learn/provenance.py` | Açılış anı karar kimliği (`entry_provenance.jsonl`), `LEGACY_UNLINKED` işareti |
| `learn/reconcile.py` | `LearnedIndex` (idempotency otoritesi), plan/apply, tur sonu tamamlama |
| `learn/position_mgmt.py` | Açık pozisyon gözlemi, `UNKNOWN` ekonomi, `ADVISORY_ONLY`, SHADOW executor sözleşmesi |

### Provenance neden deftere yazılmadı

Defterin serileşmesi ve ekonomisi değişmeden kalsın diye. `futures_ledger.json` byte düzeyinde
etkilenmez; bağlama anahtarı `trade_id`'dir ve depo ayrı bir append-only JSONL'dir. Test 18 bunu
sha256 ile kilitler.

### İdempotency neden ayrı bir indekse bağlandı

"Ders var mı" sorusunu ders listesine bakarak cevaplamak **güvenli değildir**. Sıcak pencere
(`lesson_hot_window`, varsayılan 200) dolduğunda dersler arşiv segmentlerine döner ve
`LessonStore` numaralandırma API'si sunmaz (`query` retrieval içindir, tam tarama değil). O
noktada arşivlenmiş bir ders "eksik" görünür ve **ikinci kez** üretilirdi. Bu yüzden tamamlanan
her kapanış olayı `learned_closes.jsonl` içine yazılır: idempotency çıkarım değil, **kayıttır**.

`close_event_id = stable_id("close", trade_id, closed_at, exit_reason)` — deterministiktir,
restart/retry sonrası aynı değeri üretir.

## 4. Operasyon

```bash
python -m tradingbot learning-reconcile --dry-run --table
```

```bash
python -m tradingbot learning-reconcile --apply --manifest-out reports/reconcile.json
```

`--dry-run` hiçbir dosyaya yazmaz. `--apply` yalnız **eksik** adımı ekler, mevcut tarihçeyi
yeniden yazmaz, defteri değiştirmez ve ikinci çalıştırmada sıfır değişiklik üretir (çıkış kodu
idempotency kanıtıdır: 0 = temiz, 1 = artık iş kaldı). Gerçek state üzerinde `--apply` yalnız
doğrulanmış yedek sonrasında çalıştırılır (`bash deploy/backup.sh daily`).

İlk çalıştırmada indeks yoksa **bootstrap** yapılır: mevcut dersi olan kapanışlar indekse
taşınır, yeniden öğrenilmez.

## 5. Her işlemde öğrenme sözleşmesi

Her final kapanışta: yeterli istatistikler, kalibrasyon kovaları, gözlem + hipotez, ajan
katkıları `before/delta/after`, maliyet/giriş/çıkış ayrıştırması, retrieval indeksi.

Tek işlemin **yapamayacakları** (testle kilitli):

- `abs(agent_delta) < 0.05`
- yetersiz örnekte `evidence_quality = LOW_SAMPLE`, `policy_status = OBSERVATION`
- politika terfisi yok, `causal_claim = False`
- risk, kaldıraç, boyut, stop ve TP öğrenmeden **üretilemez**
- öğrenme RiskEngine'i geçersiz kılamaz
- `MODEL_WAS_RIGHT` / `MODEL_WAS_WRONG` gibi kesin hüküm üretilmez

## 6. Açık pozisyon yönetimi — bugün ADVISORY_ONLY

`position_management.json` her doğal turda yazılır ve **salt okunurdur**: motor bu dosyayı
okumaz. Ekonomi değerlendirilmediyse `p_win`, `expected_net_return` ve `remaining_edge` alanları
`UNKNOWN` **dizesidir** — `0.00` ya da `0.50` üretilmez, böylece sayı bekleyen bir tüketici
`UNKNOWN`u sessizce sıfır sanamaz.

`ManagementExecutor` yalnız `SHADOW` modunu kabul eder; başka bir mod `ValueError` ile reddedilir.
Sınıf bir gateway, outbox ya da defter nesnesi **kabul etmez** ve modülün import sınırı AST
testiyle doğrulanır. Gerçek çıkış politikası bu sürümde **aktif değildir** ve out-of-sample kanıt
oluşmadan aktive edilmeyecektir.

## 7. Quant tazeliği

Quant raporu offline üretilir (worker'dan bağımsız, bilinçli tasarım). Bu yüzden burada yeniden
üretilmez, fakat kanonik kapanış sayısıyla **karşılaştırılır**: `quant_sample_count`,
`quant_sample_gap` ve `quant_covers_all_closes` alanları `learning_chain.json` içine yazılır ve
panel eski bir raporu `ESKİ (n=9/18)` olarak gösterir. 18 kapanış varken n=9 raporu "güncel"
görünemez.

## 8. Bilinen sınırlamalar

- **Mevcut 18 ders geriye dönük zenginleştirilmez.** Maliyet düzeltmesi (`learning.py` içindeki
  `labels` geçişi) yalnız **bundan sonraki** kapanışların gözlem bloğuna `fee_drag_r` /
  `funding_drag_r` koyar. Tarihçe yeniden yazılmaz — bu bilinçli bir sözleşmedir.
- **Mevcut 18 kapanış `LEGACY_UNLINKED` kalır.** Kimlik uydurulmaz. Bağlantı yalnız bu sürümden
  sonra açılan pozisyonlarda kurulur.
- `code_sha` git deposu erişilebilirse doldurulur; yoksa `None` kalır ve uydurulmaz.
- Defter `history_keep = 5000` ile sınırlıdır. Bugünkü 18 kapanış için sorun değil, fakat 5000'i
  aşan bir gelecekte kanonik kaynak kırpılır ve zincir raporu o eski kapanışları göremez.
- Quant raporu hâlâ **elle** üretilir; bu görev yalnız bayatlığı görünür kıldı.

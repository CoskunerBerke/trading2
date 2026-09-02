# Exit Giveback & Profit Protection V1 (SHADOW)

Amaç: açık pozisyonun ne kadar lehe gidip ne kadarını geri verdiğini **ölçülebilir** hale
getirmek ve alternatif çıkış politikalarını aynı gerçek fiyat yolu üzerinde karşı-olgusal olarak
karşılaştırmak. Bu sürümde hiçbir çıkış politikası **aktive edilmez**.

Taban: `feature/quant-evaluation-v1` @ `b49e3dc`. Ölçüm tarihi 2026-09-02.

## 1. Mevcut pozisyon yönetimi çağrı zinciri (ölçüldü)

```
runner.live.snapshot  →  marks  →  FuturesLedgerV2.tick()
                                     ├── funding accrue
                                     ├── likidasyon
                                     ├── stop  (gap-through, worst-case)
                                     └── hedefler (TP1 kısmi → başabaş stop; son hedef tam)
                                   →  _finalize()  →  history
                                   →  outcome / lesson / learned index
```

| Soru | Ölçülen cevap |
| ---- | ------------- |
| Mark güncelleme sıklığı | `exit_check()` 60 sn (`--exit-every`), `tour()` 15 dk |
| Stop/TP kontrol sıklığı | ikisi de `ledger.tick()` çağırır, yani 60 sn |
| Stop/TP ile yönetim çakışır mı | Hayır. Tick **önce** çalışır; yönetim katmanı tick'ten sonra ve yalnız hâlâ açık pozisyonları görür |
| Kısmi azaltma metodu | `FuturesLedgerV2.close_partial(symbol, price, fraction)` — `quantize_qty` ile adım korumalı |
| Tam kapanış metodu | `close_manual(...)` ve tick içi `_finalize(...)` |
| Fee/funding | `_close_part` her parçada fill notional üzerinden komisyon + kayma yazar; funding `tick` başında `funding.accrue` ile işler |
| LONG/SHORT simetrisi | Evet — `pos.side.sign` ve `worst`/`best` seçimi tek yerde |
| Asgari qty/notional | Açılışta `filters.min_notional`, azaltmada `quantize_qty(_step(pos))` |

İki tick yolu **aynı değildir**: tur `_marks()` ile 1h bar yüksek/düşük uçlarını da verir,
`exit_check` yalnız son fiyatı bilir. Bu ayrım yol kaydında `tick_kind` alanında taşınır
(`bar_extremes` / `last_only`), çünkü bar uçları olmadan hesaplanan MFE gerçek en iyi noktayı
kaçırabilir.

## 2. Neden yeni bir veri katmanı gerekti

Defter yalnız `mfe_pct` / `mae_pct` **uç değerlerini** tutar. Hangi anda, hangi sırayla ve hangi
stop/hedef durumundayken oraya gidildiği hiçbir yerde yoktu. Bu iki sayıdan fiyat yolu türetmek
mümkün değildir; bir çıkış politikasını geçmişe dönük değerlendirmek de bu yüzden imkânsızdı.

`position_path.jsonl` bu boşluğu kapatır ve **yeni bir veri kaynağı eklemez**: yalnız motorun
zaten aldığı mark güncellemelerinden snapshot üretir.

## 3. Politikalar

| Politika | Ne yapar |
| -------- | -------- |
| `champion` | Bugünkü davranış: statik stop, mevcut TP dizisi, TP1 sonrası gerçek başabaş stop. Snapshot düzeyinde aksiyon üretmez |
| `challenger_a_profit_lock` | MFE eşikleri aşılınca stop'u sıkıştırır (1.0R→başabaş, 1.5R→+0.5R, 2.5R→+1.5R) |
| `challenger_b_giveback_reduce` | MFE ≥ 1.5R iken 0.75R geri verilirse kalanın %50'sini azaltmayı önerir |
| `challenger_c_time_carry` | Yaş + kalan avantaj + funding sürüklemesi. Ekonomi `UNKNOWN` ise **çıkış üretmez** |

Eşikler koda gömülü değildir: `ExitPolicyConfig` versiyonludur, `config.yaml` üzerinden gelir ve
her karara `policy_version` + `config_id` yazılır. Eşik değişimi `config_id`'yi değiştirir.

### Güvenlik değişmezleri

- Stop yalnız **sıkışır**. Gevşetme önerisi üretilemez, yürütücü de ayrıca reddeder.
- Stop markın yanlış tarafına konamaz. `min_stop_buffer_r` kadar tampon bırakılır.
- Pozisyon **büyütülemez**; `qty_after > qty_before` fail-closed reddedilir.
- Kalan qty/notional asgarinin altına düşecekse azaltma yapılmaz.
- Pozisyon başına tur başına en fazla **bir** aksiyon (config ile artırılamaz) + cooldown.
- Deterministik `idempotency_key` → restart duplicate üretmez.

## 4. SHADOW yürütücü

`ExitExecutor` yalnız `SHADOW` modunu kabul eder; `PAPER_BOUNDED` dahil her şey `ValueError` ile
reddedilir. `config.yaml` üzerinden de açılamaz: `validate_v3` `EXIT_EXECUTION_NOT_ACTIVATED`
verir. `SHADOW`da defter, emir yolu ve outbox **çağrılmaz** ve modül bunları import bile edemez
(AST testiyle kilitli). Her niyet `applied=False` + `blocker` ile döner.

## 5. Karşı-olgusal değerlendirme

`replay_policy` snapshot'ları kronolojik oynatır ve her kararı **yalnız o ana kadarki bilgiyle**
verir. Sonucu gördükten sonra geçmiş bir karar değiştirilmez; test bunu, yolu kesip önek
kararlarının birebir aynı kaldığını doğrulayarak kilitler.

Yolu tam olmayan işlemler için challenger sonucu **üretilmez**: kayıt `NO_COMPLETE_PATH` taşır.
Mevcut 18 kapanışın hiçbirinin yolu yoktur (özellik onlardan sonra geldi), dolayısıyla hepsi bu
durumdadır. Bu bir eksiklik değil, dürüst bir kapsam beyanıdır.

Ölçülen büyüklükler: net expectancy R, captured R, giveback R, payoff, profit factor, drawdown,
CVaR5, çıkış maliyeti, kaçırılan kazanç ve kaçınılan zarar (ayrı ayrı), sembol/yön yoğunlaşması.

## 6. Terfi kapıları

Hiçbiri bu görevde otomatik açılmaz. `ELIGIBLE_FOR_PAPER_BOUNDED` için gerekenler:

- en az 50 yol-tam kapanış
- en az 30 takvim günü
- rolling walk-forward kanıtı
- champion'dan anlamlı yüksek net expectancy, maliyet sonrası
- drawdown ve CVaR kötüleşmemiş
- tek sembol/rejim yoğunlaşması yok
- güven aralığı sıfır avantajı dışlıyor

Kapılar dolmadıkça verdict `INSUFFICIENT_EXIT_SAMPLE`.

## 7. Gerçek veriyle ilk gözlem (2026-09-02, 8 açık pozisyon)

| Sembol | net R | MFE R | Geri verilen R | Capture | A | B |
| ------ | ----- | ----- | -------------- | ------- | - | - |
| SOL | +0,494 | +2,085 | 1,590 | 0,24 | HOLD | REDUCE |
| BMNR | −0,679 | +1,091 | 1,770 | — | HOLD | HOLD |
| MSFT | −0,405 | +1,279 | 1,684 | — | HOLD | HOLD |
| ETH | −0,163 | +0,873 | 1,036 | — | HOLD | HOLD |

Kâr geri verme sorunu ölçülebilir hale geldi: SOL 2,085R'ye kadar gitmiş, 1,59R'sini geri
vermiş ve elde tuttuğu pay 0,24.

**Önemli sınırlama.** BMNR, MSFT ve SOL'da MFE eşiği zaten aşılmış olmasına rağmen Challenger A
`STOP_WOULD_BE_WRONG_SIDE_OF_MARK` diyerek hiçbir şey önermiyor: fiyat kilit seviyesinin altına
düşmüş, o stop **anında tetiklenirdi**. Yani kâr kilidi geriye dönük uygulanamaz; yalnız MFE
eşiğinin aşıldığı **anda** kayıtlı bir yol üzerinde işe yarar. Yol kaydının önkoşul olmasının
sebebi tam olarak budur.

## 8. Yan bulgu: exit-monitor öğrenme indeksine yazmıyordu

Bu görevde `exit_check()` ve gap-reconcile yolları incelenirken, ikisinin de `Learner.learn()`
çağırıp **öğrenildi indeksine yazmadığı** görüldü. Kapanışların çoğu 60 saniyelik bu monitörden
geçer. Ders sıcak pencereden (200) arşive döndükten sonra o kapanış "eksik" görünüp **ikinci kez**
öğrenilebilirdi. İki çağrı yerine de `note_learned(...)` eklendi ve regresyonla kilitlendi.

## 9. Bilinen sınırlamalar

- Mevcut 18 kapanışta yol yok; hepsi `NO_COMPLETE_PATH`. Karşılaştırma ancak bu sürümden sonra
  açılıp kapanan işlemlerde mümkün.
- `exit_check` yolu bar uçlarını bilmez; o snapshot'lardan hesaplanan MFE gerçek en iyi noktayı
  kaçırabilir. Alan `tick_kind` ile işaretlenir, düzeltilmez.
- Challenger C üretimde hiç tetiklenmeyecek: açık pozisyonda ekonomi değerlendirilmiyor ve
  politika `UNKNOWN`da çıkış üretmiyor. Bu bilinçli.
- Yol deposunun rotasyonu yok. 8 pozisyon × 4 tur/saat ≈ 770 snapshot/gün, yaklaşık 0,5 MB/gün.
  Uzun vadede bir arşiv/rotasyon gerekir.
- Karşı-olgusal maliyet modeli sabit oranlıdır (`eval_fee_rate`, `eval_slippage_rate`); defterin
  gerçek kademeli tarifesiyle birebir aynı değildir.

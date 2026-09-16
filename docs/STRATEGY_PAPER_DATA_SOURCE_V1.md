# STRATEJİ KÂĞIT DEFTERİ — VERİ KAYNAĞI DOĞRULAMASI V1 (2026-09-16)

Amaç: T2 (`t2_trend_regime`) ve M2 (`m2_tsmom28`) kâğıt defterlerinin futures PAPER işlemi yalnız **doğrulanmış USDⓈ-M
perpetual verisiyle** açılsın/kapansın. 25adb9d grafik kaydını doğruluyordu ama işlem yolu doğrulamıyordu: perpetual
mumlar alınamayınca motor ana bot için yeni girişi kapatırken (`_entry_data_blocked`), `_strategy_paper_tour` SPOT
ikameli çerçeveleri `StrategyBook.step`e iletiyor, `apply_action` kaynak bakmadan futures pozisyonu açıyor; stop/çıkış
tick'leri spot ticker `last` ve spot 1h fitilleriyle besleniyordu. Kanıt: `docs/review/evidence-2026-09-16/rt_before.txt`.

Bu tur T2/M2 kuralını, on coin evrenini, sermaye/risk ayarlarını, stop geometrisini ve PAPER modunu DEĞİŞTİRMEZ. Beklenen
tek fark: yanlış/doğrulanamayan veriyle işlem yapılmaması ve kâğıt defter fiyat yolunun spot ticker yerine doğrulanmış
perp mark fiyatını kullanması.

## 1. Sözleşme (`tradingbot/strategy_paper.py`)

`DataVerdict` — kural verisinin kimlik hükmü; canlı motor ve replay AYNI nesneyi `apply_action(..., data=)`e taşır:

| alan | anlam |
|---|---|
| `ok` | sembolün kural çerçevesi doğrulanmış USDM_PERP: provenans var, **bu turun** kimliği (`tour_id == run_id`), piyasa USDM_PERP, provenansta bağlanan bar zaman damgası bellekteki çerçeveyle birebir (`frames["1d"].last_ts`) |
| `entry_ok` | yeni giriş için ayrıca: sağlayıcının `entry_ok` bayrağı (perp çerçeve eksikse False) ve **BTC referansı** için aynı doğrulama |
| `reason` | ilk başarısızlık kodu: `DATA_PROVENANCE_MISSING`, `DATA_PROVENANCE_STALE`, `DATA_MARKET_SPOT`, `DATA_FRAME_MISSING_1D`, `DATA_FRAME_MISMATCH_1D`, `DATA_ENTRY_BLOCKED:<neden>`, `DATA_BTC_<aynı kodlar>`, `DATA_VERDICT_MISSING` (hüküm hiç verilmedi) |
| `market`, `source`, `tour_id`, `bars` | gerçekten kullanılan piyasa/kaynak/tur ve bar zamanları (`{"1d": son kapanmış bar açılış ms}`) |
| `btc` | `{required, ok, market, reason, bars}` |

`verify_paper_data(symbol, frames, provenance, run_id, btc_frames, btc_provenance, need_btc)`: SAF. Provenans defter
adından ya da beklenen piyasadan **uydurulamaz**; sağlayıcının bu tur için yazdığı kayıt yüklü veriye bağlanmış olmalı.
Kuralın ihtiyacı `decide`/`read_daily`den türetilir: yalnız GÜNLÜK kapanmış barlar (`RULE_TIMEFRAMES = ("1d",)`, en az 210
bar `read_daily` içinde); BTC rejimi yalnız yeni girişte gerekir (`decide` position_open iken BTC okumaz) → açık pozisyonun
kural kapanışı BTC eksikliğiyle engellenmez; 15m/1h/4h dilimleri bu kural için zorunlu değildir (mum formasyonu
dedektörlerinin veri ihtiyacı ayrıdır).

`apply_action(act, ..., data=None)` fail-closed: hüküm yoksa ya da `ok` değilse ne OPEN ne CLOSE (`REJECTED`, gerekçe
`DATA_*`); OPEN ayrıca `entry_ok` ister. Açılan pozisyonun `features.data_source` ve `meta.data_source` alanlarına
piyasa/kaynak/tur/bar zamanları/BTC hükmü yazılır (işlem kaydında kanıt).

## 2. Akış: sağlayıcıdan ortak uygulayıcıya

1. `engine_v3.tour`: evren sembolleri için `perp_frames` (binanceusdm) denenir; sonuç `_frame_provenance[s] = {market:
   USDM_PERP|SPOT, source, entry_ok, reason}` (2026-09-13'ten beri). `run_symbol` başarısız olursa o sembolün provenansı bu
   turda SİLİNİR (eski çerçeve/onay bu turda doğrulanamaz).
2. `engine_v3._bind_provenance(s)` (**yeni**, run_symbol'dan hemen sonra): provenansa `tour_id` (= `run_id`), dilim
   başına `frames[tf].last_ts/n` (bellekteki çerçeveden) ve canlı snapshot'taki `perp_mark` (funding.markPrice) eklenir.
   `frame_provenance.json` bu alanlarla yazılır.
3. `engine_v3._strategy_paper_tour`: `book.step(..., provenance_by_symbol=self._frame_provenance, data_gaps=...)`;
   fiyatlar `_paper_marks` ile (aşağıda). Ana botun spot-ticker `marks`i kâğıt defterlere ARTIK verilmez.
4. `StrategyBook.step`: sembol başına `verify_paper_data`; hüküm `data_checks[sym]`. `ok` değilse **kural değerlendirilmez**
   (SPOT ikamesiyle ne OPEN ne CLOSE) → `_reject_data` (sayaç `data_rejected`, `rejections[DATA_*]`, `last_actions[sym] =
   DATA_REJECTED`, `data_events` olayı; bilgi için kanıtsız veriyle kuralın ne diyeceği `would_act` olarak yazılır,
   UYGULANMAZ). Pozisyon yokken `entry_ok` değilse aynı şekilde `ENTRY` aşamasında ret. Aksi hâlde `decide` →
   `apply_action(data=verdict)`.
5. Replay (`replay/engine.py::_paper_data_verdict`): "replay zaten futures" varsayımı YOK — hüküm arşiv deposunun
   manifest'inden (`store.manifest(market, sym, tf)`: market == futures, provider, row_count>0) türetilir; spot arşivi
   `DATA_MARKET_SPOT`, manifest'siz seri `DATA_ARCHIVE_MANIFEST_MISSING`. BTC çerçevesi aynı arşiv piyasasından okunur (ayrı
   provenans yok; `btc.note`). Doğrulanmış aynı veri/kuralda karar, boyut, fill ve maliyet davranışı değişmez
   (`tests/test_replay_strategy_mode_v1.py`).

## 3. Fiyat yolu (kâğıt defterler)

Önce: tur tick'i `_marks` (spot ticker `last` + spot 1h high/low), 60 sn izleyici `ticker.last` (spot) = mark. Şimdi
(`engine_v3._paper_marks`):

* Fiyat = **USDⓈ-M perpetual mark** (`funding.markPrice`, binanceusdm `fetch_funding_rate`), her kontrolde canlı
  sağlayıcıdan (`runner.live.snapshot`, sembol başına paylaşılan 60 sn önbellek). Spot ticker kullanılmaz; yanlış fiyat
  etiket değiştirilerek futures yapılmaz. Turun provenansına bağlanan `perp_mark` **kayıttır, fiyat kaynağı değil**
  (2026-09-16 ikinci tur; bkz. §6): tur kimliği aynı diye eski fiyat yeniden kullanılmaz.
* Tur ve izleyici tick'i **fiyat-yalnız**dır (bar ucu taşımaz). Kapanmış 1h bar uçları ayrı sözleşmeyle
  (`StrategyBook.apply_closed_bars`, §6) YALNIZ bu turda provenansı USDM_PERP olan çerçeveden; SPOT ikamesi fitili futures
  stop'unu tetiklemez (`test_5`), giriş öncesi/tüketilmiş fitil yeni olay üretmez (`test_strategy_paper_price_time_v1`).
* Geçerli ve güncel perp fiyatı yoksa: sembol için tick YOK (uydurma gerçekleşme yok), `data_gaps[sym]` gerekçeli
  (`NO_VERIFIED_FUTURES_PRICE` | `STALE_FUTURES_PRICE` | `INVALID_FUTURES_PRICE_TIME`) + `PRICE_GAP` olayı (durum/gerekçe
  değişince bir kez; `PRICE_RESTORED` ile kapanır), pozisyon izlenmeye devam eder, özetteki `last_price` son bilinen
  değerdir (yeni zaman damgası basılmaz). Fiyat geri gelince mevcut davranış sürer (stop/hedef doğrulanmış mark ile
  çalışır, çerçeve SPOT olsa bile).
* Ana botun (`ledger2`) tick yolu bu turda DEĞİŞMEDİ (aynı spot ticker kaynağı + son 1h ucu; kapsam dışı, ayrıca not edildi).

## 4. İzlenebilirlik

`strategy_paper.json` / `strategy_paper_m2.json`: `counters.data_rejected` (risk/defter reddinden ayrı; yeniden
başlatmada korunur), `rejections[DATA_*]`, `data_checks` (bu turun sembol hükümleri), `data_gaps`, `data_events_recent`
(son 30; kalıcı kuyruk 50), `data_policy`, `last_actions[sym]` (`DATA_REJECTED`/`DATA_GAP`/`OPENED` + `data`). Açılan
pozisyon/işlem kaydı `features.data_source`. `chart_analysis/config.json.skipped` grafik kaydının nedenidir, işlem
günlüğünün yerini tutmaz.

Geçmiş kayıtlar: bu commit'ten ÖNCE açılmış T2/M2 işlemlerinde `data_source` yoktur → hangi veriyle girildiği
**bilinmiyor**; sonradan futures provenansı eklenmez, topluca temiz/geçersiz ilan edilmez. Defter, bakiye, açık
pozisyonlar, ileri test başlangıçları ve sayaçlar sıfırlanmaz. VPS geçmişi bu oturumda okunmadı.

## 5. Testler

`tests/test_strategy_paper_data_source_v1.py` (9): SPOT-only (giriş yok, gerekçe, grafik ayrımı); provenans yok/bayat/
çelişkili/giriş kapalı ve `apply_action` fail-closed; coin USDM_PERP + BTC SPOT/eksik; doğrulanmış perp çerçeve (×2 fiyat,
aynı zaman damgaları) ile giriş ve `data_source` kaydı; açık pozisyon + SPOT fitili + fiyat boşluğu + doğrulanmış mark ile
koruyucu stop; kural kapanışı BTC'siz / SPOT ikamesiyle CLOSE yok; yeniden başlatma + chart açık/kapalı; replay manifest
bağı. Test harness'ında sağlayıcı taklit edilir (`_install(market=, btc_market=)`, `install_perp_frames`), doğrulama yolu
gerçek koddur.

## 6. Zaman sözleşmeleri (2026-09-16, ikinci tur — canlı fiyat ve zaman doğrulaması)

Bağımsız inceleme 6042fcf'de üç kusur buldu; üçü de gerçek motor turuyla yeniden üretildi
(`docs/review/evidence-2026-09-16/repro_pt.py`, `pt_before.txt` → `pt_after.txt`; kapanış: `docs/review/PAPER_PRICE_TIME_2026-09-16.md`).
Sabitler `tradingbot/strategy_paper.py` başındadır ve özet dosyasına `data_policy.{price,bars,freshness}` olarak yazılır.

| sözleşme | kural | kod |
|---|---|---|
| **Canlı fiyat** | Her kontrolde `runner.live.snapshot` (paylaşılan 60 sn önbellek: ana defter/T2/M2 aynı isteği tekrarlamaz). Fiyat = `funding.mark`; **kaynak zamanı** = `funding.ts` (borsanın mark zaman damgası, ms) yoksa snapshot `ts` (alınma, epoch sn); **kontrol anı** ayrı. Şart: sonlu ve pozitif; kaynak zamanı çözülebilir, `now`dan `PRICE_FUTURE_SKEW_S` (120 sn) fazla ileride değil, yaş ≤ `PRICE_MAX_AGE_S` (180 sn = önbellek 60 + izleyici 60 + pay). Aksi: tick YOK, boşluk gerekçeli. Turun bağlı `perp_mark`ı kayıttır (fiyat + zamanlar + bağlama anındaki yaş), yeniden kullanılmaz. `TickData.ts` = fiyatın kaynak zamanı (ISO, UTC). | `verified_price`, `parse_ts_ms`, `engine_v3._paper_marks(now=)` |
| **Bar uçları (1h)** | Yalnız (1) `now` anında kapanmış (`açılış + 1h <= now`), (2) pozisyon açılışından SONRA açılmış (`açılış >= opened_at`; girişi içeren bar dahil değil — replay `_advance` ile aynı sınır), (3) tüketilmemiş (`pozisyon.meta.ohlc_cursor["1h"]`, defter dosyasında kalıcı) barlar; kronolojik, bar başına bir defter tick'i, tick zamanı = bar kapanışı (kapanış kaydı gerçek olay zamanı). Ölçek dışı bar (canlı mark ±%20 dışı ya da `low<=close<=high` değil) atlanır, imleç ilerler, `BAR_SKIPPED` olayı. İzleyici (60 sn) bar ucu uygulamaz. Tur sırası: kural → bar uçları → canlı fiyat (stop sonrası aynı turda yeniden giriş yok). | `StrategyBook.apply_closed_bars`, `engine_v3._paper_closed_bars` |
| **Günlük sinyal güncelliği** | Değerlendirme anı `as_of` açık girdidir: canlıda turun karar saati (`_tour_now_ms`), replay'de simülasyonun karar anı (`t + tf`; duvar saati DEĞİL). Kural `as_of` anında KAPANMIŞ son barı okur; hüküm `bars["1d"]` = o bar (kapanmamış/gelecek son satır dışlanır; `step` kuralın okuduğu barla birebir eşitliği ayrıca denetler → `DATA_BAR_MISMATCH_1D`). Beklenen son kapanış = `as_of`tan önceki UTC gün sınırı; `BAR_LAG_TOLERANCE_MS["1d"]` (1 saat = 4 tur aralığı; canlı sağlayıcıya karşı ÖLÇÜLMEDİ, kod sabiti) içinde bir önceki bar da güncel sayılır; daha eskisi `DATA_FRAME_STALE_1D` (ne OPEN ne kural CLOSE; koruyucu stop canlı fiyatla sürer). Son satır `as_of + 60 sn`den ileride açılmışsa `DATA_FRAME_FUTURE_1D`; `as_of` yoksa `DATA_AS_OF_MISSING`. BTC referansı aynı kural (`DATA_BTC_FRAME_STALE_1D`), yalnız yeni girişte. `prov.tour_id == run_id` güncellik kanıtı değildir. | `frame_freshness`, `expected_last_closed_open`, `verify_paper_data(as_of_ms=)`, `replay._paper_data_verdict(sym, t, fr)` |

Testler: `tests/test_strategy_paper_price_time_v1.py` (10): izleyici aynı run_id ile 105→95 stop; bayat/gelecek/kesik fiyat
boşlukları ve toparlanma; giriş öncesi fitil (tur + izleyici) ve girişten sonraki bar ile stop; bar bir kez (tur tekrarı,
izleyici, yeniden başlatma, kapanmamış/giriş öncesi/ölçek dışı); 46 gün eski seri (coin/BTC) ret + güncel seri giriş ve
`data_source.bars == kuralın barı == signal_ts`; gece yarısı sınırı/tolerans/kapanmamış/gelecek saf sözleşme; önceki DATA_*
engelleri + bayat mumda fiyat koruması; replay simülasyon zamanı (eski tarih ret değil, arşiv boşluğu ret, yalnız 4h parite);
fiyat-yalnız tick + ISO zaman + sayaç sözlüğü. Mevcut `test_5`in kaçırdığı yol (pozitif bağlı mark, aynı run_id, fiyat
değişimi, yeni tur yok) artık `test_1`de.

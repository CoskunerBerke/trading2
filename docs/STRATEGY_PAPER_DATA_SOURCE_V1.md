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

* Fiyat = **USDⓈ-M perpetual mark** (`funding.markPrice`, binanceusdm `fetch_funding_rate`; bu turda bağlanan
  `perp_mark`, yoksa taze snapshot). Spot ticker kullanılmaz; yanlış fiyat etiket değiştirilerek futures yapılmaz.
* 1h high/low (bar içi stop/TP tetiği) YALNIZ bu turda provenansı USDM_PERP olan çerçeveden; SPOT ikamesi fitili futures
  stop'unu tetiklemez (`test_5`).
* Doğrulanmış perp fiyatı yoksa: sembol için tick YOK (uydurma gerçekleşme yok), `data_gaps[sym] =
  NO_VERIFIED_FUTURES_PRICE` + `PRICE_GAP` olayı (durum değişince bir kez; `PRICE_RESTORED` ile kapanır), pozisyon
  izlenmeye devam eder, özetteki `last_price` son bilinen değerdir. Fiyat geri gelince mevcut davranış sürer (stop/hedef
  doğrulanmış mark ile çalışır, çerçeve SPOT olsa bile).
* Ana botun (`ledger2`) tick yolu bu turda DEĞİŞMEDİ (aynı spot ticker kaynağı; kapsam dışı, ayrıca not edildi).

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

# T2/M2 PAPER — CANLI FİYAT VE ZAMAN DOĞRULAMASI (kapanış, 2026-09-16)

Kapsam: `work/chart-analysis-v1`, inceleme tabanı 6042fcf5a4563cc4edaa9b92cb81492cbe29d0a2. T2/M2 kural formülleri, on
coin evreni, sermaye/risk ayarları, stop geometrisi, PAPER modu, geçmiş defter/açık pozisyon/ileri test başlangıcı
DEĞİŞMEDİ; SPOT engeli, BTC referans kontrolü, ortak `DataVerdict` sözleşmesi ve grafik onarımları korundu. VPS'e dağıtım
YAPILMADI; VPS HEAD'i okunmadı; bu kusurların VPS'te gerçekleştiği GÖZLEMLENMEDİ (kaynak kodu incelemesiyle bulundu).
Sözleşme belgesi: `docs/STRATEGY_PAPER_DATA_SOURCE_V1.md` §3 ve §6.

## 1. Üç bulgu — yeniden üretim (6042fcf) ve onarım sonrası

Betik `evidence-2026-09-16/repro_pt.py` — gerçek `TradingEngineV3.tour` (tests/test_engine_v3 harness'ı: ağ yok, sentetik
çerçeveler + sahte canlı snapshot) + gerçek `StrategyBook`/`FuturesLedgerV2`. Sağlayıcı taklit edilir; `_bind_provenance`,
`_paper_marks`, `verify_paper_data`, `apply_action`, `ledger.tick` gerçek koddur. `pt_before.txt` 6042fcf'de (geçici
worktree), `pt_after.txt` onarım sonrası aynı betikle.

| # | vaka | 6042fcf (`pt_before.txt`) | onarım (`pt_after.txt`) |
|---|---|---|---|
| F1 | sağlıklı tur → pozitif bağlı `perp_mark` (10 dk eski) → aynı run_id ile 3 exit-check; sağlayıcı stop altı (0,95×stop) güncel fiyat | sağlayıcı çağrısı 0; tick fiyatları [tur fiyatı ×3]; pozisyon açık, kapanış 0 (bağlı mark kaldırılınca +4 çağrı ve stop) | sağlayıcı çağrısı 4 (2 sembol × 2 defter), ilk kontrolde stop; kapanış 2+2 |
| F2a | giriş ÖNCESİ kapanmış 1h bar low=0,88×fiyat; tur pozisyonu açar ve hemen tick'ler | açılan 2, kapanan 2 (`stop`) aynı turda | açılan 2, kapanan 0 |
| F2b | pozisyon açık; aynı eski barın low'u düşük; 60 sn izleyici, mark = giriş | kapanan 2 (`stop`) | kapanan 0 |
| F3 | coin + BTC günlük serisi 46 gün eski (yeterli mum), yeni tur kimliğiyle bağlı | `data_checks ok/entry_ok = True`, ret 0, T2 2 pozisyon açtı, `data_source.bars.1d` = 30 Tem | `DATA_FRAME_STALE_1D` ×2, açılan 0 |

Neden mevcut `test_5` F1'i kaçırdı: önce `funding.mark`'ı kaldırıp YENİ tur çalıştırıyor (bağlı `perp_mark=None`), sonraki
exit-check zaten snapshot yoluna düşüyordu; "sağlıklı turda pozitif bağlı mark, aynı run_id, fiyat değişti, yeni tur yok"
yolu sınanmamıştı. Yeni `test_1` tam bu yolu sınar (bağlı mark temizlenmez, tur açılmaz, sağlayıcı çağrısı sayılır).

## 2. Onarım (kod)

* `tradingbot/strategy_paper.py`: `PRICE_MAX_AGE_S=180`, `PRICE_FUTURE_SKEW_S=120`, `BAR_LAG_TOLERANCE_MS={"1d": 1 saat}`,
  `BAR_FUTURE_SKEW_MS=60 sn`, `BAR_TIMEFRAME="1h"`; `parse_ts_ms` (ISO / epoch sn / epoch ms → tek sözleşme, UTC ms);
  `verified_price` (sonlu-pozitif mark, kaynak zamanı `funding.ts` yoksa snapshot `ts`, yaş/gelecek denetimi);
  `frame_freshness` + `expected_last_closed_open` (as_of anında kapanmış son bar, beklenen son kapanış, tolerans, gelecek);
  `verify_paper_data(as_of_ms=)` (fail-closed: `DATA_AS_OF_MISSING`; `bars` = kuralın okuduğu kapanmış bar; `detail`);
  `DataVerdict.as_of_ms/detail`; `StrategyBook.step` `as_of=now` + `DATA_BAR_MISMATCH_1D` tutarlılık kapısı;
  `StrategyBook.apply_closed_bars` (kapanmış, girişten sonra açılmış, bir kez — `meta.ohlc_cursor`, bar kapanış zamanlı
  tick, ölçek dışı bar atlanır/olay); `tick` fiyat-yalnız; özet `data_policy.{price,bars,freshness}`.
* `tradingbot/engine_v3.py`: `_tour_now_ms` (turun karar saati); `_bind_provenance` → `as_of_ms` + `perp_mark` KAYIT
  (fiyat, `ts`, `price_ts_ms`, `fetched_at_ms`, bağlama anındaki yaş, `fresh`, `reason`); `_paper_marks(now=)` her kontrolde
  canlı sağlayıcı + `verified_price`, fiyat-yalnız `TickData` (ts = fiyatın kaynak zamanı, ISO), gerekçeli boşluklar;
  `_paper_closed_bars` (yalnız bu turda USDM_PERP bağlı çerçeveden ham 1h satırları); `_strategy_paper_tour` sırası
  kural → bar uçları → canlı fiyat; `_strategy_paper_exit_check` fiyat-yalnız, kontrol anına göre yaş.
* `tradingbot/agents/market.py`: `funding.ts` (ccxt `timestamp`: borsanın mark zaman damgası, ms) snapshot'a eklendi.
* `tradingbot/replay/engine.py`: `_paper_data_verdict(sym, t, fr)` — `as_of = t + tf` (simülasyon anı), dilimdeki günlük
  çerçeve için aynı `frame_freshness`; arşiv boşluğu `DATA_FRAME_STALE_1D`; yalnız 4h dilimde önceki davranış aynı.
* Testler: `tests/test_strategy_paper_price_time_v1.py` (yeni, 10); `tests/test_strategy_paper_data_source_v1.py` (test_2
  saf sözleşme `as_of_ms`; `news` kapalı — ağsız); `tests/test_strategy_paper_engine_v1.py::_install` 1d serisi üretim
  güncelliğine (son bar bugünün UTC 00:00'ında kapanmış) çekildi — `_engine` harness'ı her dilimi bir bar geride kuruyordu
  (üretime göre bir gün bayat); `.github/workflows/chart-analysis.yml` artık `test_strategy_paper_data_source_v1.py` ve
  yeni dosyayı da çalıştırıyor (6042fcf CI'ı veri kaynağı dosyasını KOŞMUYORDU — o "yeşil" bu dosyayı kapsamıyordu).

## 3. Kabul matrisi → test

| # | kabul | test |
|---|---|---|
| 1 | sağlıklı tur, pozitif bağlı mark, aynı run_id, 105→95 → stop 100; bağlı mark temizlenmez, tur yok | `test_1` (gerçek motor turu + izleyici) |
| 2 | sağlayıcı kesik / yalnız yaşlı / gelecek zamanlı → gerekçeli boşluk, eski markla fill yok, son bilinen fiyat korunur; toparlanma | `test_2` (+ `verified_price`/`parse_ts_ms` saf) |
| 3 | giriş öncesi low=99 barı + 105/stop 100 + güncel 105 → açık; girişten sonraki bar stop altı → kapanır (tur + izleyici) | `test_3` |
| 4 | aynı bar yeniden tüketilmez (tur tekrarı, izleyici, yeniden başlatma); kapanmamış/giriş öncesi/ölçek dışı bar | `test_4` |
| 5 | coin/BTC serisi haftalarca eski + yeni tour_id → ret; güncel seri → giriş, kural aynı, `data_source.bars == kural barı == signal_ts` | `test_5[False/True]` |
| 6 | UTC gece yarısı sınırı, kapanmamış son bar, gelecek damgası, tolerans sınırı, as_of yok | `test_6` (saf) |
| 7 | SPOT/provenans yok engelleri korunur; bayat mumda yeni giriş/kural CLOSE yok, güncel fiyatla koruma sürer | `test_7` + mevcut `test_1..3,6` |
| 8 | replay as_of = simülasyon anı; eski tarih tek başına ret değil; arşiv boşluğu ret; yalnız 4h parite | `test_8` + `test_replay_strategy_mode_v1` |
| — | tur tick'i fiyat-yalnız, ISO zaman, `perp_mark` kayıt, sayaç sözlüğü değişmedi | `test_9` |

## 4. Yerel test kanıtı (etiketli)

* **Gerçek motor turu** (harness: ağ yok, sağlayıcı taklit): `repro_pt.py` 4/4 BULGU_VAR (6042fcf) → 4/4 BULGU_YOK; yeni dosya
  10/10 geçti (6,7 sn; `news` kapalıyken ağ denemesi yok).
* **Hedefli regresyon**: `test_strategy_paper_*` (6 dosya), `test_replay_strategy_mode_v1`, `test_chart_analysis_v1*`,
  `test_chart_patterns_v1`, `test_chart_confirmation_*`, `test_dashboard`, `test_engine_v3`, `test_entry_universe`,
  `test_universe_report`, `test_universe_screen` → **158 geçti**; bütün `test_replay_*` → **74 geçti, 13 atlandı** (ortam:
  Linux sandbox / bash). `ruff check tradingbot/` temiz.
* **Dar yardımcı fonksiyon testi**: `test_2` (verified_price/parse_ts_ms), `test_6` (frame_freshness/verify_paper_data).
* **VPS doğrulaması**: YOK (dağıtılmadı). Gerçek Binance `funding.ts`/`markPrice` yolu yalnız kod/CI ile doğrulandı;
  `BAR_LAG_TOLERANCE` canlı sağlayıcıya karşı ölçülmedi (kod sabiti, `data_policy` ile görünür).

## 5. Beklenen üretim farkı

* Kâğıt defter fiyatı her 60 sn'de canlı perp mark (paylaşılan önbellek; ana defterin `exit_check`i zaten aynı isteği
  yapıyor — ek yük yalnız ana defterde olmayan sembollerde). Fiyat bayat/geçersizse tick yok, `data_gaps` gerekçeli.
* 1h bar uçları yalnız girişten sonraki kapanmış barlara, bir kez uygulanır; kapanış kaydı bar kapanış zamanlı olabilir.
* Günlük seri güncel değilse (`DATA_FRAME_STALE_1D`) yeni giriş ve kural CLOSE yok; stop koruması sürer. Sağlıklı veride
  ret BEKLENMEZ; ilk turlarda `data_checks[*].detail.1d.age_s` ve `data_gaps` izlenmeli.
* Ana botun (`ledger2`) tick yolu (spot ticker + son 1h ucu her turda) bu turda DEĞİŞMEDİ — aynı "eski fitil" sorusu ana
  bot için ayrı karar gerektirir (kapsam dışı bırakıldı, not edildi).
